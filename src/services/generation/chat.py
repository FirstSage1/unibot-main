"""Сервис генерации текстовых диалогов с AI.

Этот модуль реализует ChatGenerationService для обработки текстовых диалогов
с AI-моделями (GPT-4o, Claude, и др.).

Особенности chat-генераций:
- Поддержка многошагового диалога с сохранением контекста
- Отправка истории предыдущих сообщений в AI
- Сохранение ответа AI в БД для следующих сообщений в диалоге
"""

from typing import Any

from typing_extensions import override

from src.core.exceptions import GenerationError
from src.db.repositories import MessageRepository
from src.services.generation.base import BaseGenerationService, GenerationResult
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Константы для ролей в диалоге
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


class ChatGenerationService(BaseGenerationService):
    """Сервис для генерации текстовых ответов AI в диалогах.

    Наследует BaseGenerationService и реализует специфичную логику:
    - Формирование контекста диалога с историей сообщений
    - Добавление system prompt из конфига модели
    - Сохранение ответа AI в БД для следующих сообщений

    Attributes:
        generation_type: "chat" - тип генерации для tracking и cooldown
    """

    generation_type = "chat"

    @override
    async def _perform_generation(
        self, model_key: str, **generation_params: Any
    ) -> GenerationResult:
        """Выполнить генерацию текстового ответа AI.

        Args:
            model_key: Ключ модели из config.yaml.
            **generation_params: Должен содержать:
                - messages: list[dict] - история диалога в формате OpenAI API
                    [{"role": "user", "content": "..."},
                     {"role": "assistant", "content": "..."}]
                - user_id: int - ID пользователя (для сохранения ответа в БД)

        Returns:
            GenerationResult с текстовым ответом AI.

        Raises:
            GenerationError: Если AI вернул пустой ответ или произошла ошибка генерации.

        Example:
            result = await service._perform_generation(
                model_key="gpt-4o",
                messages=[{"role": "user", "content": "Привет!"}],
                user_id=123
            )
            # result.content = "Здравствуйте! Как я могу вам помочь?"
        """
        messages = generation_params.get("messages", [])
        user_id = generation_params.get("user_id")

        if not messages:
            raise ValueError("No messages provided for chat generation")

        if user_id is None:
            raise ValueError("user_id is required for chat generation")

        logger.debug(
            "Отправляем в AI: user_id=%d, model=%s, сообщений в контексте=%d",
            user_id,
            model_key,
            len(messages),
        )

        # Генерируем ответ через AI-сервис
        # Для chat-моделей передаём messages, prompt оставляем пустым
        result = await self.ai_service.generate(
            model_key=model_key,
            prompt="",  # Не используется для chat-моделей
            messages=messages,
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

        # Сохраняем ответ AI в БД (для контекста следующих сообщений в диалоге)
        message_repo = MessageRepository(self.session)
        await message_repo.add_message(
            user_id=user_id,
            model_key=model_key,
            role=ROLE_ASSISTANT,
            content=result.content,
        )

        logger.info(
            "Ответ AI сгенерирован: user_id=%d, model=%s, длина=%d",
            user_id,
            model_key,
            len(result.content),
        )

        # Передаём usage для расчёта себестоимости
        return GenerationResult(
            content=result.content,
            success=True,
            usage=result.usage,
        )
