"""Сервис генерации изображений по текстовому описанию.

Этот модуль реализует ImageGenerationService для генерации изображений
через AI-модели (DALL-E, FLUX, Stable Diffusion, и др.).

Особенности image-генераций:
- Генерация по текстовому промпту (описанию желаемого изображения)
- Возврат URL сгенерированного изображения
- Поддержка различных моделей через единый интерфейс
"""

from typing import Any

from typing_extensions import override

from src.core.exceptions import GenerationError
from src.services.generation.base import BaseGenerationService, GenerationResult
from src.utils.logging import get_logger

logger = get_logger(__name__)


class ImageGenerationService(BaseGenerationService):
    """Сервис для генерации изображений по текстовому описанию.

    Наследует BaseGenerationService и реализует специфичную логику:
    - Отправка текстового промпта в AI
    - Получение URL сгенерированного изображения
    - Валидация результата (проверка что URL не пустой)

    Attributes:
        generation_type: "image" - тип генерации для tracking и cooldown
    """

    generation_type = "image"

    @override
    async def _perform_generation(
        self, model_key: str, **generation_params: Any
    ) -> GenerationResult:
        """Выполнить генерацию изображения по текстовому описанию.

        Args:
            model_key: Ключ модели из config.yaml.
            **generation_params: Должен содержать:
                - prompt: str - текстовое описание желаемого изображения
                - user_id: int - ID пользователя (для логирования)

        Returns:
            GenerationResult с URL сгенерированного изображения.

        Raises:
            GenerationError: Если промпт пустой, AI вернул пустой ответ,
                или произошла ошибка генерации.

        Example:
            result = await service._perform_generation(
                model_key="dall-e-3",
                prompt="A photo of a bear riding a bicycle over the moon",
                user_id=123
            )
            # result.content = "https://cdn.replicate.com/.../output.png"
        """
        prompt = generation_params.get("prompt")
        user_id = generation_params.get("user_id")

        if not prompt:
            raise ValueError("No prompt provided for image generation")

        if user_id is None:
            raise ValueError("user_id is required for image generation")

        logger.debug(
            "Отправляем в AI: user_id=%d, model=%s, prompt='%s'",
            user_id,
            model_key,
            prompt[:100],  # Логируем только первые 100 символов
        )

        # Генерируем изображение через AI-сервис
        result = await self.ai_service.generate(
            model_key=model_key,
            prompt=prompt,
        )

        # Проверяем результат
        if not result.content or not isinstance(result.content, str):
            logger.error(
                "AI вернул пустой content: user_id=%d, model=%s",
                user_id,
                model_key,
            )
            raise GenerationError(
                "AI returned empty response",
                provider="ai_service",
                model_id=model_key,
            )

        # result.content содержит URL изображения
        image_url = result.content

        logger.info(
            "Изображение сгенерировано: user_id=%d, model=%s, url=%s",
            user_id,
            model_key,
            image_url[:50],  # Логируем только начало URL
        )

        # Передаём usage для расчёта себестоимости (если доступно)
        return GenerationResult(
            content=image_url,
            success=True,
            usage=result.usage,
        )
