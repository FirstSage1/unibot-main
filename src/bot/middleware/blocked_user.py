"""Middleware, блокирующий взаимодействие заблокированных пользователей."""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import override

from src.db.repositories.user_repo import UserRepository

BLOCKED_USER_MESSAGE = "Вы забанены"


class BlockedUserMiddleware(BaseMiddleware):
    """Останавливает обработку событий для пользователей с баном в БД."""

    def __init__(
        self,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Создать middleware с фабрикой асинхронных сессий БД."""
        super().__init__()
        self._session_factory = session_factory

    @override
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Проверить бан и ответить вместо handler; Any нужен для API aiogram."""
        telegram_user = data.get("event_from_user")
        if not isinstance(telegram_user, TelegramUser):
            return await handler(event, data)

        async with self._session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(telegram_user.id)

        if user is None or not user.is_blocked:
            return await handler(event, data)

        if isinstance(event, Message):
            await event.answer(BLOCKED_USER_MESSAGE)
        elif isinstance(event, CallbackQuery):
            await event.answer(BLOCKED_USER_MESSAGE, show_alert=True)
        return None


def create_blocked_user_middleware() -> BlockedUserMiddleware:
    """Создать middleware с production-фабрикой сессий."""
    from src.db.base import DatabaseSession

    return BlockedUserMiddleware(session_factory=DatabaseSession)
