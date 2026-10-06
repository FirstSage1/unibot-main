"""Базовый адаптер для AI-провайдеров.

Этот модуль определяет абстрактный интерфейс, который должны реализовать
все AI-провайдеры. Это позволяет:
- Единообразно работать с разными провайдерами (OpenRouter, RouterAI)
- Легко добавлять новые провайдеры без изменения бизнес-логики

Паттерн: Adapter (GoF) + Strategy
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class GenerationType(StrEnum):
    """Типы генерации для AI-провайдеров.

    Каждая модель поддерживает один тип генерации.
    Это используется для:
    - Выбора правильного провайдера для задачи
    - Валидации запросов (модель должна соответствовать типу)
    - Группировки моделей в интерфейсе бота
    - Применения cooldowns (разные интервалы для разных типов)
    """

    # Текстовая генерация (ChatGPT, Claude, и т.д.)
    # Вход: текстовый промпт + история сообщений
    # Выход: текстовый ответ
    CHAT = "chat"

    # Генерация изображений (DALL-E, FLUX, Stable Diffusion)
    # Вход: текстовый промпт (описание картинки)
    # Выход: URL или байты изображения
    IMAGE = "image"

    # Text-to-Speech — озвучка текста (OpenAI TTS, ElevenLabs)
    # Вход: текст для озвучки + параметры голоса
    # Выход: аудиофайл (mp3, wav)
    TTS = "tts"

    # Speech-to-Text — распознавание речи (Whisper)
    # Вход: аудиофайл (голосовое сообщение)
    # Выход: текстовая расшифровка
    STT = "stt"

    # Редактирование изображений (Gemini, GPT-5 Image)
    # Вход: исходное изображение + текстовый промпт с описанием изменений
    # Выход: URL или байты отредактированного изображения
    # Использует мультимодальный Chat API с изображением в input
    IMAGE_EDIT = "image_edit"


class GenerationStatus(StrEnum):
    """Статус генерации.

    Генерация может быть:
    - Мгновенной (chat) — ответ приходит сразу
    - Долгой (video) — нужно опрашивать статус
    """

    # Генерация успешно завершена, результат готов
    SUCCESS = "success"

    # Генерация ещё выполняется (для долгих операций)
    # В этом случае нужно опрашивать статус через get_prediction_status()
    PROCESSING = "processing"

    # Генерация не удалась
    FAILED = "failed"

    # Генерация отменена пользователем или системой
    CANCELED = "canceled"


@dataclass
class GenerationResult:
    """Результат генерации от AI-провайдера.

    Унифицированная структура для всех типов генераций.
    Содержит либо готовый результат, либо информацию для отслеживания прогресса.

    Attributes:
        status: Текущий статус генерации.
        content: Результат генерации. Тип зависит от capability:
            - CHAT: str (текст ответа)
            - IMAGE: str (URL изображения) или bytes
            - VIDEO: str (URL видео) или bytes
            - TTS: bytes (аудиоданные)
            - STT: str (распознанный текст)
        prediction_id: ID генерации на стороне провайдера.
            Используется для отслеживания долгих операций (video).
        error_message: Сообщение об ошибке (если status == FAILED).
        usage: Информация об использовании токенов/ресурсов.
            Формат зависит от провайдера.
        raw_response: Сырой ответ от API (для отладки).
    """

    status: GenerationStatus
    content: str | bytes | None = None
    prediction_id: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Проверить, успешна ли генерация."""
        return self.status == GenerationStatus.SUCCESS

    @property
    def is_processing(self) -> bool:
        """Проверить, ещё ли выполняется генерация."""
        return self.status == GenerationStatus.PROCESSING

    @property
    def is_failed(self) -> bool:
        """Проверить, провалилась ли генерация."""
        return self.status == GenerationStatus.FAILED


class BaseProviderAdapter(ABC):
    """Абстрактный базовый класс для AI-провайдеров.

    Определяет интерфейс, который должны реализовать все провайдеры.
    Это позволяет AIService работать с любым провайдером единообразно.

    Для добавления нового провайдера:
    1. Создайте класс, наследующий BaseProviderAdapter
    2. Реализуйте методы generate() и supports_capability()
    3. Опционально: реализуйте get_prediction_status() для долгих операций

    Пример:
        class MyProviderAdapter(BaseProviderAdapter):
            def __init__(self, api_key: str) -> None:
                self._client = MyProviderClient(api_key)

            async def generate(self, model_id: str, prompt: str, **kwargs):
                response = await self._client.complete(model=model_id, prompt=prompt)
                return GenerationResult(
                    status=GenerationStatus.SUCCESS,
                    content=response.text,
                )

            def supports_capability(self, generation_type: GenerationType) -> bool:
                return generation_type == GenerationType.CHAT
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Название провайдера (для логов и ошибок).

        Должно быть уникальным и понятным.
        Примеры: "openai", "replicate", "neuroapi".
        """

    @abstractmethod
    async def generate(
        self,
        model_id: str,
        prompt: str,
        *,
        generation_type: GenerationType,
        **kwargs: Any,
    ) -> GenerationResult:
        """Выполнить генерацию.

        Основной метод для взаимодействия с AI-моделью.
        Должен обрабатывать все ошибки и возвращать GenerationResult.

        Args:
            model_id: Идентификатор модели на стороне провайдера.
                Например: "gpt-4o", "black-forest-labs/flux-dev".
            prompt: Текстовый промпт для генерации.
                Для CHAT — сообщение пользователя.
                Для IMAGE/VIDEO — описание желаемого результата.
                Для TTS — текст для озвучки.
            capability: Тип генерации (chat, image, video, tts, stt).
            **kwargs: Дополнительные параметры, зависящие от типа генерации:
                - messages: list[dict] — история диалога для CHAT
                - system_prompt: str — системный промпт для CHAT
                - size: str — размер изображения ("1024x1024")
                - voice: str — голос для TTS
                - audio_data: bytes — аудио для STT

        Returns:
            GenerationResult с результатом или статусом обработки.

        Raises:
            GenerationError: При ошибке генерации.
        """

    @abstractmethod
    def supports_capability(self, generation_type: GenerationType) -> bool:
        """Проверить, поддерживает ли провайдер данный тип генерации.

        Используется для:
        - Валидации запросов перед отправкой
        - Выбора провайдера для конкретной задачи
        - Отображения доступных опций в интерфейсе

        Args:
            capability: Тип генерации для проверки.

        Returns:
            True если провайдер поддерживает этот тип, False иначе.
        """

    async def get_prediction_status(self, prediction_id: str) -> GenerationResult:
        """Получить статус долгой генерации.

        Некоторые генерации (особенно видео) выполняются долго.
        Провайдер возвращает prediction_id, по которому можно отслеживать прогресс.

        По умолчанию выбрасывает NotImplementedError.
        Переопределите в провайдерах, которые поддерживают долгие операции.

        Args:
            prediction_id: ID генерации, полученный из GenerationResult.

        Returns:
            GenerationResult с текущим статусом.

        Raises:
            NotImplementedError: Если провайдер не поддерживает отслеживание.
            GenerationError: При ошибке получения статуса.
        """
        raise NotImplementedError(
            f"Провайдер {self.provider_name} не поддерживает отслеживание статуса"
        )

    async def cancel_prediction(self, prediction_id: str) -> bool:
        """Отменить долгую генерацию.

        Позволяет прервать генерацию, которая ещё не завершилась.
        Полезно для экономии ресурсов и улучшения UX.

        По умолчанию возвращает False (отмена не поддерживается).
        Переопределите в провайдерах с поддержкой отмены.

        Args:
            prediction_id: ID генерации для отмены.

        Returns:
            True если генерация успешно отменена, False иначе.
        """
        return False
