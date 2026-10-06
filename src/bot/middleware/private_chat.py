"""Middleware для фильтрации сообщений только из личных чатов.

Согласно PRD (раздел 4.5), бот работает ТОЛЬКО в личных сообщениях.
Сообщения из групп и каналов игнорируются без ответа.

Пример использования:
    from aiogram import Dispatcher
    from src.bot.middleware.private_chat import PrivateChatMiddleware

    dp = Dispatcher()

    # Регистрируем middleware — должен быть ПЕРВЫМ в цепочке
    private_chat_middleware = PrivateChatMiddleware()
    dp.message.middleware(private_chat_middleware)
    dp.callback_query.middleware(private_chat_middleware)

Типы чатов в Telegram:
    - private: Личные сообщения (✓ разрешены)
    - group: Обычные группы (✗ игнорируются)
    - supergroup: Супергруппы (✗ игнорируются)
    - channel: Каналы (✗ игнорируются)
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject
from typing_extensions import override

from src.utils.logging import get_logger

logger = get_logger(__name__)


class PrivateChatMiddleware(BaseMiddleware):
    """Middleware для фильтрации сообщений только из личных чатов.

    Пропускает только сообщения из личных чатов (ChatType.PRIVATE).
    Сообщения из групп, супергрупп и каналов игнорируются без ответа.

    Это соответствует требованию PRD:
    "Бот работает только в личных сообщениях.
     Сообщения из групп/каналов — игнорируются без ответа."
    """

    @override
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Проверить тип чата и пропустить только личные сообщения.

        Args:
            handler: Обработчик события (следующий в цепочке middleware).
            event: Входящее событие (Message или CallbackQuery).
            data: Словарь данных, передаваемых в handler.

        Returns:
            Результат выполнения handler или None, если сообщение из группы/канала.
        """
        # Определяем chat_type в зависимости от типа события
        # aiogram возвращает ChatType enum из chat.type
        chat_type: ChatType | str | None = None

        if isinstance(event, Message):
            # Для обычных сообщений берём chat.type напрямую
            chat_type = event.chat.type

        elif (
            isinstance(event, CallbackQuery)
            and event.message
            and hasattr(event.message, "chat")
        ):
            # Для callback_query берём chat.type из message
            chat_type = event.message.chat.type

        # Если не удалось определить тип чата — пропускаем (на всякий случай)
        if chat_type is None:
            logger.debug(
                "Не удалось определить тип чата для события %s, пропускаю",
                type(event).__name__,
            )
            return await handler(event, data)

        # Пропускаем только личные чаты
        # Сравниваем как с enum, так и со строкой для совместимости
        if chat_type == ChatType.PRIVATE or chat_type == "private":
            return await handler(event, data)

        # Игнорируем сообщения из групп и каналов БЕЗ ответа
        # Логируем только на уровне DEBUG, чтобы не спамить логи
        # chat_type может быть как ChatType enum, так и строкой
        chat_type_str = chat_type.value if hasattr(chat_type, "value") else chat_type
        logger.debug(
            "Игнорирую сообщение из %s чата (только private разрешён)",
            chat_type_str,
        )

        # Возвращаем None — сообщение не обрабатывается
        return None
