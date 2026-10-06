"""Адаптер для OpenAI-совместимого API.

Этот модуль реализует интеграцию с OpenAI API для:
- Генерации текста (GPT, Claude и другие LLM)
- Генерации изображений (DALL-E)
- Озвучки текста (TTS)
- Распознавания речи (STT/Whisper)

Используется для работы с OpenAI-совместимыми провайдерами:
- OpenRouter (openrouter.ai) — агрегатор моделей от разных провайдеров
- RouterAI (routerai.ru) — российский сервис
- AnyModel (anymodel.org) — резервный OpenAI-совместимый шлюз

Оба провайдера используют один формат API, отличаются только base_url и ключом.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from openai import AsyncOpenAI
from typing_extensions import override

from src.core.exceptions import GenerationError
from src.providers.ai.base import (
    BaseProviderAdapter,
    GenerationResult,
    GenerationStatus,
    GenerationType,
)
from src.providers.ai.registry import register_provider
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

    from src.config.models import AIProvidersSettings

logger = get_logger(__name__)

# Типы генерации, которые поддерживает OpenAI API
SUPPORTED_GENERATION_TYPES = frozenset(
    {
        GenerationType.CHAT,
        GenerationType.IMAGE,
        GenerationType.IMAGE_EDIT,
        GenerationType.TTS,
        GenerationType.STT,
    }
)

# Таймаут по умолчанию для HTTP-клиента (в секундах).
# Используется если AIService не передал таймаут из конфигурации.
DEFAULT_TIMEOUT_SECONDS = 60.0


class OpenAIAdapter(BaseProviderAdapter):
    """Адаптер для OpenAI-совместимого API.

    Поддерживает:
    - Chat Completions (GPT, Claude и другие LLM)
    - Image Generation (DALL-E)
    - Image Edit (редактирование изображений через мультимодальный Chat API)
    - Text-to-Speech (OpenAI TTS)
    - Speech-to-Text (Whisper)

    Пример использования:
        # Для OpenRouter
        adapter = OpenAIAdapter(
            api_key="sk-or-v1-...",
            base_url="https://openrouter.ai/api/v1"
        )

        # Генерация текста
        result = await adapter.generate(
            model_id="openai/gpt-4o",
            prompt="Привет! Как дела?",
            generation_type=GenerationType.CHAT,
            messages=[{"role": "user", "content": "Привет!"}],
        )

        # Генерация изображения
        result = await adapter.generate(
            model_id="openai/dall-e-3",
            prompt="Кот в космосе",
            generation_type=GenerationType.IMAGE,
            size="1024x1024",
        )

    Attributes:
        _client: Асинхронный клиент OpenAI SDK.
        _base_url: Base URL для API (openrouter.ai или routerai.ru).
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        timeout: float = 60.0,
        organization: str | None = None,
        proxy_url: str | None = None,
    ) -> None:
        """Создать адаптер OpenAI-совместимого API.

        Args:
            api_key: API-ключ провайдера.
            base_url: URL для API провайдера.
                Например: https://openrouter.ai/api/v1 или https://api.routerai.ru/v1
                По умолчанию: None (используется стандартный OpenAI URL).
            timeout: Таймаут запросов в секундах.
            organization: ID организации (опционально, обычно не требуется).
            proxy_url: URL прокси-сервера (опционально).
                Формат: http://host:port, https://host:port, socks5://host:port
                Если указан — все HTTP-запросы будут идти через прокси.
        """
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._proxy_url = proxy_url

        # Создаем httpx клиент с прокси (если указан)
        http_client: httpx.AsyncClient | None = None
        if proxy_url:
            logger.info("Используем прокси для OpenAI API: %s", proxy_url)
            http_client = httpx.AsyncClient(
                proxy=proxy_url,
                timeout=timeout,
            )

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            organization=organization,
            http_client=http_client,
        )

    @property
    @override
    def provider_name(self) -> str:
        """Название провайдера."""
        # Если используется кастомный base_url — это не стандартный OpenAI
        if self._base_url:
            return f"openai-compatible ({self._base_url})"
        return "openai"

    @override
    def supports_capability(self, generation_type: GenerationType) -> bool:
        """Проверить поддержку типа генерации."""
        return generation_type in SUPPORTED_GENERATION_TYPES

    @override
    async def generate(
        self,
        model_id: str,
        prompt: str,
        *,
        generation_type: GenerationType,
        **kwargs: Any,
    ) -> GenerationResult:
        """Выполнить генерацию через OpenAI-совместимый API.

        Args:
            model_id: ID модели в формате owner/model (например: openai/gpt-4o).
            prompt: Текстовый промпт.
            generation_type: Тип генерации.
            **kwargs: Дополнительные параметры:
                - messages: list[dict] — история диалога для CHAT
                - system_prompt: str — системный промпт для CHAT
                - max_tokens: int — максимум токенов в ответе
                - temperature: float — температура генерации (0.0-2.0)
                - size: str — размер изображения ("1024x1024", "1792x1024")
                - quality: str — качество изображения ("standard", "hd")
                - voice: str — голос для TTS ("alloy", "echo", "fable", и др.)
                - audio_data: bytes — аудиоданные для STT
                - image_data: bytes — исходное изображение для IMAGE_EDIT

        Returns:
            GenerationResult с результатом.

        Raises:
            GenerationError: При ошибке API.
        """
        if not self.supports_capability(generation_type):
            raise GenerationError(
                f"OpenAI не поддерживает {generation_type.value}",
                provider=self.provider_name,
                model_id=model_id,
                is_retryable=False,
            )

        try:
            if generation_type == GenerationType.CHAT:
                return await self._generate_chat(model_id, prompt, **kwargs)
            elif generation_type == GenerationType.IMAGE:
                return await self._generate_image(model_id, prompt, **kwargs)
            elif generation_type == GenerationType.IMAGE_EDIT:
                return await self._generate_image_edit(model_id, prompt, **kwargs)
            elif generation_type == GenerationType.TTS:
                return await self._generate_tts(model_id, prompt, **kwargs)
            elif generation_type == GenerationType.STT:
                return await self._generate_stt(model_id, **kwargs)
            else:
                raise GenerationError(
                    f"Неизвестный тип генерации: {generation_type}",
                    provider=self.provider_name,
                    model_id=model_id,
                    is_retryable=False,
                )
        except GenerationError:
            # Пробрасываем наши ошибки без изменений
            raise
        except Exception as e:
            # Оборачиваем неожиданные ошибки
            # logging.exception() автоматически включает traceback
            logger.exception(
                "Ошибка OpenAI API (model=%s, generation_type=%s)",
                model_id,
                generation_type,
            )
            raise GenerationError(
                str(e),
                provider=self.provider_name,
                model_id=model_id,
                is_retryable=self._is_retryable_error(e),
                original_error=e,
            ) from e

    async def _generate_chat(
        self,
        model_id: str,
        prompt: str,
        **kwargs: Any,
    ) -> GenerationResult:
        """Генерация текста через Chat Completions API.

        Args:
            model_id: ID модели в формате owner/model.
            prompt: Сообщение пользователя.
            **kwargs:
                - messages: Полная история диалога.
                - system_prompt: Системный промпт.
                - max_tokens: Максимум токенов.
                - temperature: Температура (0.0-2.0).

        Returns:
            GenerationResult с текстом ответа.
        """
        # Формируем список сообщений
        messages: list[ChatCompletionMessageParam] = []

        # Добавляем системный промпт если есть
        system_prompt = kwargs.get("system_prompt")
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Добавляем историю диалога или только текущий промпт
        history = kwargs.get("messages", [])
        if history:
            messages.extend(history)
        else:
            messages.append({"role": "user", "content": prompt})

        # Параметры генерации
        max_tokens = kwargs.get("max_tokens", 4096)
        temperature = kwargs.get("temperature", 0.7)

        logger.debug(
            "OpenAI Chat: model=%s, messages=%d, max_tokens=%d",
            model_id,
            len(messages),
            max_tokens,
        )

        response = await self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Извлекаем ответ
        content = response.choices[0].message.content or ""

        # Информация об использовании токенов
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        logger.debug("OpenAI Chat завершён: tokens=%s", usage)

        return GenerationResult(
            status=GenerationStatus.SUCCESS,
            content=content,
            usage=usage,
            raw_response={"id": response.id, "model": response.model},
        )

    def _uses_chat_completions_for_images(self) -> bool:
        """Проверить, использует ли провайдер Chat Completions для изображений.

        RouterAI и OpenRouter не поддерживают стандартный Images API.
        Вместо этого они используют Chat Completions API с параметром
        modalities=["image", "text"].

        Returns:
            True если нужно использовать Chat Completions API для изображений.
        """
        if not self._base_url:
            return False
        # RouterAI и OpenRouter используют Chat Completions API для изображений
        return "routerai.ru" in self._base_url or "openrouter.ai" in self._base_url

    async def _generate_image(
        self,
        model_id: str,
        prompt: str,
        **kwargs: Any,
    ) -> GenerationResult:
        """Генерация изображения.

        Поддерживает два способа генерации:
        1. Images API (стандартный OpenAI) — endpoint /images/generate
        2. Chat Completions API с modalities (RouterAI, OpenRouter) — для провайдеров,
           которые не поддерживают Images API

        Args:
            model_id: ID модели в формате owner/model.
            prompt: Описание изображения.
            **kwargs:
                - size: Размер ("1024x1024", "1792x1024", "1024x1792").
                - quality: Качество ("standard", "hd").
                - style: Стиль ("natural", "vivid").
                - max_tokens: Максимум токенов для Chat Completions API.

        Returns:
            GenerationResult с URL или base64 изображения.
        """
        # RouterAI и OpenRouter используют Chat Completions API с modalities
        if self._uses_chat_completions_for_images():
            return await self._generate_image_via_chat(model_id, prompt, **kwargs)

        # Стандартный OpenAI Images API
        return await self._generate_image_via_images_api(model_id, prompt, **kwargs)

    async def _generate_image_via_chat(
        self,
        model_id: str,
        prompt: str,
        **kwargs: Any,
    ) -> GenerationResult:
        """Генерация изображения через Chat Completions API с modalities.

        Используется для RouterAI и OpenRouter, которые не поддерживают
        стандартный Images API (/images/generate).

        Согласно документации RouterAI:
        - Параметр modalities=["image", "text"] включает генерацию изображений
        - Результат возвращается в поле images сообщения как base64 data URL

        Args:
            model_id: ID модели (например: google/gemini-2.5-flash-image-preview).
            prompt: Описание изображения.
            **kwargs:
                - max_tokens: Максимум токенов в ответе (по умолчанию 4096).
                - image_only: Если True, промпт явно требует только изображение.

        Returns:
            GenerationResult с data URL изображения (base64).
        """
        max_tokens = kwargs.get("max_tokens", 4096)
        image_only = kwargs.get("image_only", False)

        logger.debug(
            "Image via Chat Completions: model=%s, prompt='%s', image_only=%s",
            model_id,
            prompt[:100],
            image_only,
        )

        # Формируем промпт для генерации изображения.
        # Добавляем явную инструкцию для моделей, которые могут отвечать текстом.
        image_prompt = prompt
        if image_only:
            # Для моделей, которые должны возвращать ТОЛЬКО изображение
            image_prompt = f"Generate an image: {prompt}"
        else:
            # Для универсальных моделей добавляем чёткую инструкцию
            image_prompt = (
                f"Please generate an image based on this description. "
                f"Do not ask questions, just create the image: {prompt}"
            )

        messages: list[ChatCompletionMessageParam] = [
            {"role": "user", "content": image_prompt}
        ]

        # Chat Completions API с modalities для генерации изображений
        # extra_body используется для передачи нестандартных параметров
        response = await self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            max_tokens=max_tokens,
            extra_body={"modalities": ["image", "text"]},
        )

        # Извлекаем изображение из ответа.
        # RouterAI возвращает изображения в поле images сообщения.
        message = response.choices[0].message
        image_url = self._extract_image_from_message(message)

        if not image_url:
            # Если изображение не найдено — логируем детали для отладки
            content = message.content or ""
            # Проверяем, есть ли у message другие поля с данными
            has_images_field = hasattr(message, "images") and message.images
            logger.error(
                "Модель не вернула изображение | "
                "model=%s | prompt='%s' | "
                "content='%s' | has_images=%s | response_id=%s",
                model_id,
                prompt[:150],
                content[:300] if content else "(пусто)",
                has_images_field,
                response.id,
            )
            raise GenerationError(
                "Модель не сгенерировала изображение. "
                "Попробуйте переформулировать запрос.",
                provider=self.provider_name,
                model_id=model_id,
                is_retryable=True,
            )

        # Информация об использовании токенов
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        logger.debug(
            "Image via Chat завершён: image_url_length=%d, tokens=%s",
            len(image_url),
            usage,
        )

        return GenerationResult(
            status=GenerationStatus.SUCCESS,
            content=image_url,
            usage=usage,
            raw_response={"id": response.id, "model": response.model},
        )

    def _extract_image_from_message(self, message: Any) -> str | None:
        """Извлечь URL изображения из ответа API.

        RouterAI и OpenRouter могут возвращать изображения в разных форматах:
        1. В поле images сообщения (массив base64 data URLs)
        2. В поле content как data URL (если модель возвращает напрямую)
        3. В content как часть multipart ответа

        Args:
            message: Объект сообщения от API (ChatCompletionMessage).

        Returns:
            Data URL изображения (data:image/png;base64,...) или None.
        """
        # Проверяем поле images (RouterAI формат)
        image_url = self._extract_from_images_field(message)
        if image_url:
            return image_url

        # Проверяем content
        content = message.content
        return self._extract_from_content(content)

    def _extract_from_images_field(self, message: Any) -> str | None:
        """Извлечь изображение из поля images (RouterAI формат).

        API может возвращать images в разных форматах:
        1. Строка с base64 или data URL: ["data:image/png;base64,..."]
        2. Словарь с image_url: [{"type": "image_url", "image_url": {"url": "..."}}]
        """
        images = getattr(message, "images", None)
        if not images or not isinstance(images, list) or len(images) == 0:
            return None

        first_image = images[0]

        # Формат 1: строка (base64 или data URL)
        if isinstance(first_image, str):
            if first_image.startswith("data:image"):
                return first_image
            # Если это просто base64, оборачиваем в data URL
            return f"data:image/png;base64,{first_image}"

        # Формат 2: словарь {"type": "image_url", "image_url": {"url": "..."}}
        if isinstance(first_image, dict) and first_image.get("type") == "image_url":
            image_url_obj = first_image.get("image_url", {})
            if isinstance(image_url_obj, dict):
                url = image_url_obj.get("url")
                if url and isinstance(url, str):
                    return str(url)

        return None

    def _extract_from_content(self, content: Any) -> str | None:
        """Извлечь изображение из поля content."""
        # Проверяем content как строку — может содержать data URL напрямую
        if content and isinstance(content, str):
            if content.startswith("data:image"):
                return str(content)
            # Иногда API возвращает строковое представление списка
            content = self._parse_string_as_list(content)

        # Проверяем content как список (multipart ответ)
        if not content or not isinstance(content, list):
            return None

        return self._extract_image_from_list(content)

    def _parse_string_as_list(self, content: str) -> str | list[dict[str, object]]:
        """Парсить строку как JSON или Python список, если возможно."""
        if not content.startswith("["):
            return content

        import ast
        import contextlib
        import json

        with contextlib.suppress(json.JSONDecodeError):
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
            return content

        with contextlib.suppress(ValueError, SyntaxError):
            parsed = ast.literal_eval(content)
            if isinstance(parsed, list):
                return parsed
            return content

        return content

    def _extract_image_from_list(self, parts: list[dict[str, object]]) -> str | None:
        """Извлечь изображение из списка частей multipart ответа."""
        for part in parts:
            if not isinstance(part, dict):
                continue

            # Формат {"type": "image_url", "image_url": {"url": "data:..."}}
            if part.get("type") == "image_url":
                url = self._get_url_from_image_url_obj(part.get("image_url", {}))
                if url:
                    return url

            # Альтернативный формат {"type": "image", "data": "base64..."}
            if part.get("type") == "image":
                data = part.get("data")
                if data and isinstance(data, str):
                    return f"data:image/png;base64,{data}"

        return None

    def _get_url_from_image_url_obj(self, image_url_obj: Any) -> str | None:
        """Извлечь URL из объекта image_url."""
        if not isinstance(image_url_obj, dict):
            return None
        url = image_url_obj.get("url")
        if url and isinstance(url, str):
            return str(url)
        return None

    async def _generate_image_via_images_api(
        self,
        model_id: str,
        prompt: str,
        **kwargs: Any,
    ) -> GenerationResult:
        """Генерация изображения через стандартный Images API (DALL-E).

        Используется для стандартного OpenAI API.

        Args:
            model_id: ID модели (например: dall-e-3).
            prompt: Описание изображения.
            **kwargs:
                - size: Размер ("1024x1024", "1792x1024", "1024x1792").
                - quality: Качество ("standard", "hd").
                - style: Стиль ("natural", "vivid").

        Returns:
            GenerationResult с URL изображения.
        """
        size = kwargs.get("size", "1024x1024")
        quality = kwargs.get("quality", "standard")
        style = kwargs.get("style", "vivid")

        if self._base_url == "https://anymodel.org/v1":
            import base64

            references = kwargs.get("image_data")
            extra_body = {}
            if references:
                encoded = base64.b64encode(references).decode("ascii")
                extra_body["images"] = [f"data:image/jpeg;base64,{encoded}"]
            response = await self._client.images.generate(
                model=model_id,
                prompt=prompt,
                size=size,
                quality=quality,
                n=1,
                response_format="b64_json",
                extra_body=extra_body,
            )
            if not response.data or not response.data[0].b64_json:
                raise GenerationError(
                    "AnyModel вернул пустое изображение",
                    provider=self.provider_name,
                    model_id=model_id,
                    is_retryable=True,
                )
            return GenerationResult(
                status=GenerationStatus.SUCCESS,
                content=f"data:image/png;base64,{response.data[0].b64_json}",
            )

        logger.debug(
            "OpenAI Images API: model=%s, size=%s, quality=%s",
            model_id,
            size,
            quality,
        )

        response = await self._client.images.generate(
            model=model_id,
            prompt=prompt,
            size=size,
            quality=quality,
            style=style,
            n=1,
        )

        # DALL-E возвращает URL изображения
        if not response.data:
            raise GenerationError(
                "OpenAI вернул пустой ответ",
                provider=self.provider_name,
                model_id=model_id,
                is_retryable=True,
            )

        image_data = response.data[0]
        image_url = image_data.url

        logger.debug("OpenAI Images API завершён: url=%s", image_url)

        return GenerationResult(
            status=GenerationStatus.SUCCESS,
            content=image_url,
            raw_response={"revised_prompt": image_data.revised_prompt},
        )

    async def _generate_image_edit(
        self,
        model_id: str,
        prompt: str,
        **kwargs: Any,
    ) -> GenerationResult:
        """Редактирование изображения через мультимодальный Chat Completions API.

        Использует модели с поддержкой vision + генерации изображений
        (Gemini, GPT-5 Image). Изображение отправляется как base64.

        Args:
            model_id: ID модели в формате owner/model.
            prompt: Текстовое описание изменений, которые нужно внести.
            **kwargs:
                - image_data: bytes — исходное изображение для редактирования.
                - max_tokens: int — максимум токенов в ответе (по умолчанию 4096).

        Returns:
            GenerationResult с URL отредактированного изображения.

        Raises:
            GenerationError: Если image_data не передан или API вернул ошибку.
        """
        import base64

        image_data = kwargs.get("image_data")
        max_tokens = kwargs.get("max_tokens", 4096)

        if not image_data:
            raise GenerationError(
                "Для редактирования изображения необходимо передать image_data",
                provider=self.provider_name,
                model_id=model_id,
                is_retryable=False,
            )

        if self._base_url == "https://anymodel.org/v1":
            return await self._generate_image_via_images_api(model_id, prompt, **kwargs)

        # Кодируем изображение в base64
        image_base64 = base64.b64encode(image_data).decode("utf-8")

        logger.debug(
            "OpenAI Image Edit: model=%s, image_size=%d bytes, prompt='%s'",
            model_id,
            len(image_data),
            prompt[:100],
        )

        # Формируем мультимодальное сообщение с изображением и промптом
        # Формат соответствует OpenAI Vision API
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}",
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt,
                    },
                ],
            }
        ]

        # Для RouterAI/OpenRouter нужен параметр modalities для генерации изображений
        extra_body = {}
        if self._uses_chat_completions_for_images():
            extra_body["modalities"] = ["image", "text"]

        response = await self._client.chat.completions.create(
            model=model_id,
            messages=messages,
            max_tokens=max_tokens,
            extra_body=extra_body or None,
        )

        # Извлекаем изображение из ответа.
        # RouterAI возвращает изображения в поле images сообщения.
        message = response.choices[0].message
        image_url = self._extract_image_from_message(message)

        if not image_url:
            # Если изображение не найдено, проверяем текстовый content
            content = message.content or ""
            logger.warning(
                "Изображение не найдено в ответе редактирования, content='%s'",
                content[:200] if content else "(пусто)",
            )
            raise GenerationError(
                "Модель не сгенерировала отредактированное изображение. "
                "Попробуйте переформулировать запрос.",
                provider=self.provider_name,
                model_id=model_id,
                is_retryable=True,
            )

        # Информация об использовании токенов
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        logger.debug(
            "OpenAI Image Edit завершён: image_url_length=%d, tokens=%s",
            len(image_url),
            usage,
        )

        return GenerationResult(
            status=GenerationStatus.SUCCESS,
            content=image_url,
            usage=usage,
            raw_response={"id": response.id, "model": response.model},
        )

    async def _generate_tts(
        self,
        model_id: str,
        prompt: str,
        **kwargs: Any,
    ) -> GenerationResult:
        """Генерация аудио через Text-to-Speech API.

        Args:
            model_id: ID модели в формате owner/model.
            prompt: Текст для озвучки.
            **kwargs:
                - voice: Голос ("alloy", "echo", "fable", "onyx", "nova", "shimmer").
                - speed: Скорость (0.25-4.0).
                - response_format: Формат ("mp3", "opus", "aac", "flac").

        Returns:
            GenerationResult с аудиоданными (bytes).
        """
        voice = kwargs.get("voice", "alloy")
        speed = kwargs.get("speed", 1.0)
        response_format = kwargs.get("response_format", "mp3")

        logger.debug(
            "OpenAI TTS: model=%s, voice=%s, text_length=%d",
            model_id,
            voice,
            len(prompt),
        )

        response = await self._client.audio.speech.create(
            model=model_id,
            voice=voice,
            input=prompt,
            speed=speed,
            response_format=response_format,
        )

        # Получаем аудиоданные как bytes
        audio_data = response.read()

        logger.debug("OpenAI TTS завершён: size=%d bytes", len(audio_data))

        return GenerationResult(
            status=GenerationStatus.SUCCESS,
            content=audio_data,
        )

    async def _generate_stt(
        self,
        model_id: str,
        **kwargs: Any,
    ) -> GenerationResult:
        """Распознавание речи через Speech-to-Text API.

        Args:
            model_id: ID модели в формате owner/model.
            **kwargs:
                - audio_data: bytes — аудиоданные для распознавания.
                - audio_file: путь к файлу (альтернатива audio_data).
                - language: Язык аудио (опционально, автоопределение).

        Returns:
            GenerationResult с распознанным текстом.
        """
        audio_data = kwargs.get("audio_data")
        language = kwargs.get("language")

        if not audio_data:
            raise GenerationError(
                "Для STT необходимо передать audio_data",
                provider=self.provider_name,
                model_id=model_id,
                is_retryable=False,
            )

        logger.debug(
            "OpenAI STT: model=%s, audio_size=%d bytes",
            model_id,
            len(audio_data),
        )

        # OpenAI SDK ожидает file-like object
        # Создаём tuple (filename, bytes, content_type)
        audio_file: tuple[str, bytes, str] = ("audio.ogg", audio_data, "audio/ogg")

        # Формируем параметры запроса
        # language передаём только если он указан явно
        create_kwargs: dict[str, Any] = {
            "model": model_id,
            "file": audio_file,
        }
        if language:
            create_kwargs["language"] = language

        transcription = await self._client.audio.transcriptions.create(
            **create_kwargs,
        )

        text = transcription.text

        logger.debug("OpenAI STT завершён: text_length=%d", len(text))

        return GenerationResult(
            status=GenerationStatus.SUCCESS,
            content=text,
        )

    def _is_retryable_error(self, error: Exception) -> bool:
        """Определить, можно ли повторить запрос после ошибки.

        Некоторые ошибки временные (rate limit, server error),
        другие постоянные (invalid API key, bad request).

        Args:
            error: Исключение от OpenAI SDK.

        Returns:
            True если можно повторить, False если ошибка постоянная.
        """
        error_message = str(error).lower()

        # Временные ошибки — можно повторить
        retryable_patterns = [
            "rate limit",
            "timeout",
            "connection",
            "server error",
            "502",
            "503",
            "504",
        ]

        return any(pattern in error_message for pattern in retryable_patterns)


# ==============================================================================
# ФАБРИКА АДАПТЕРОВ
# ==============================================================================


class OpenAIAdapterFactory:
    """Фабрика для создания OpenAI-совместимых адаптеров.

    Эта фабрика создаёт OpenAIAdapter для провайдеров, использующих
    OpenAI-совместимый API (OpenRouter, RouterAI и другие).

    Attributes:
        _base_url: URL API провайдера.
        _api_key_attr: Имя атрибута в AIProvidersSettings для API-ключа.
    """

    def __init__(self, base_url: str, api_key_attr: str) -> None:
        """Создать фабрику адаптеров.

        Args:
            base_url: URL API провайдера.
                Например: https://openrouter.ai/api/v1
            api_key_attr: Имя атрибута в settings для API-ключа.
                Например: openrouter_api_key
        """
        self._base_url = base_url
        self._api_key_attr = api_key_attr

    def create(
        self,
        settings: AIProvidersSettings,
        proxy_url: str | None = None,
        timeout: float | None = None,
    ) -> BaseProviderAdapter | None:
        """Создать адаптер если настроен API-ключ."""
        api_key = getattr(settings, self._api_key_attr, None)
        if api_key is None:
            return None

        return OpenAIAdapter(
            api_key=api_key.get_secret_value(),
            base_url=self._base_url,
            proxy_url=proxy_url,
            timeout=timeout or DEFAULT_TIMEOUT_SECONDS,
        )


# ==============================================================================
# РЕГИСТРАЦИЯ ПРОВАЙДЕРОВ
# ==============================================================================

# Регистрируем OpenRouter — агрегатор моделей от разных провайдеров
# URL: https://openrouter.ai
# API-ключ берётся из переменной окружения: AI__OPENROUTER_API_KEY
register_provider(
    "openrouter",
    OpenAIAdapterFactory(
        base_url="https://openrouter.ai/api/v1",
        api_key_attr="openrouter_api_key",
    ),
)

# AnyModel использует OpenAI-совместимый endpoint https://anymodel.org/v1.
# Идентификатор модели берётся из каталога AnyModel; поддерживается и bare-id.
register_provider(
    "anymodel",
    OpenAIAdapterFactory(
        base_url="https://anymodel.org/v1",
        api_key_attr="anymodel_api_key",
    ),
)

# Регистрируем RouterAI — российский OpenAI-совместимый сервис
# URL: https://routerai.ru/api/v1 (НЕ api.routerai.ru!)
# API-ключ берётся из переменной окружения: AI__ROUTERAI_API_KEY
# Документация: https://routerai.ru/docs/guides
register_provider(
    "routerai",
    OpenAIAdapterFactory(
        base_url="https://routerai.ru/api/v1",
        api_key_attr="routerai_api_key",
    ),
)
