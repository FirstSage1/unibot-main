"""Тесты middleware блокировки пользователей."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message
from aiogram.types import User as TelegramUser

from src.bot.middleware.blocked_user import BlockedUserMiddleware
from src.db.models.user import User as DbUser


def make_message(user_id: int) -> Message:
    """Создать сообщение Telegram для теста."""
    return Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=user_id, type="private"),
        from_user=TelegramUser(id=user_id, is_bot=False, first_name="Test"),
        text="hello",
    )


@pytest.mark.asyncio
async def test_blocked_user_gets_message_and_handler_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Заблокированный пользователь получает ответ вместо вызова handler."""
    db_user = DbUser(telegram_id=42, is_blocked=True)
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = db_user
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def session_factory() -> AsyncGenerator[MagicMock, None]:
        yield session

    middleware = BlockedUserMiddleware(session_factory)

    event = make_message(42)
    answer = AsyncMock()
    monkeypatch.setattr(Message, "answer", answer)
    handler = AsyncMock()
    data = {"event_from_user": event.from_user}

    result = await middleware(handler, event, data)

    assert result is None
    answer.assert_awaited_once_with("Вы забанены")
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_unblocked_user_reaches_handler() -> None:
    """Обычный пользователь передаётся следующему обработчику."""
    db_user = DbUser(telegram_id=42, is_blocked=False)

    session = MagicMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = db_user
    session.execute = AsyncMock(return_value=query_result)

    @asynccontextmanager
    async def session_factory() -> AsyncGenerator[MagicMock, None]:
        yield session

    middleware = BlockedUserMiddleware(session_factory)
    event = make_message(42)
    handler = AsyncMock(return_value="handled")

    result = await middleware(handler, event, {"event_from_user": event.from_user})

    assert result == "handled"
    handler.assert_awaited_once()


@pytest.mark.parametrize("with_message", [True, False])
async def test_blocked_callback_cannot_reach_handler(
    monkeypatch: pytest.MonkeyPatch,
    with_message: bool,
) -> None:
    """Старые и inline-кнопки не позволяют обойти бан."""
    session = MagicMock()
    query_result = MagicMock()
    query_result.scalar_one_or_none.return_value = DbUser(
        telegram_id=42, is_blocked=True
    )
    session.execute = AsyncMock(return_value=query_result)

    @asynccontextmanager
    async def session_factory() -> AsyncGenerator[MagicMock, None]:
        yield session

    event = CallbackQuery(
        id="test",
        from_user=TelegramUser(id=42, is_bot=False, first_name="Test"),
        chat_instance="test",
        message=make_message(42) if with_message else None,
        data="start",
    )
    answer = AsyncMock()
    monkeypatch.setattr(CallbackQuery, "answer", answer)
    handler = AsyncMock()
    middleware = BlockedUserMiddleware(session_factory)

    result = await middleware(handler, event, {"event_from_user": event.from_user})

    assert result is None
    answer.assert_awaited_once_with("Вы забанены", show_alert=True)
    handler.assert_not_awaited()
