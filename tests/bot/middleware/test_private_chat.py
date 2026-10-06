"""Тесты для PrivateChatMiddleware.

Модуль тестирует:
- Пропуск сообщений из личных чатов (private)
- Блокировку сообщений из групп (group)
- Блокировку сообщений из супергрупп (supergroup)
- Блокировку сообщений из каналов (channel)
- Обработку CallbackQuery из разных типов чатов
- Fallback при невозможности определить тип чата
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Chat, Message, User

from src.bot.middleware.private_chat import PrivateChatMiddleware

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def middleware() -> PrivateChatMiddleware:
    """Экземпляр PrivateChatMiddleware для тестов."""
    return PrivateChatMiddleware()


@pytest.fixture
def mock_handler() -> AsyncMock:
    """Мок handler, который возвращает 'handled'."""
    return AsyncMock(return_value="handled")


@pytest.fixture
def mock_user() -> User:
    """Мок пользователя Telegram."""
    return User(
        id=123456789,
        is_bot=False,
        first_name="Test",
        username="testuser",
    )


def create_mock_message(chat_type: ChatType, user: User) -> Message:
    """Создать мок Message с заданным типом чата.

    Args:
        chat_type: Тип чата (private, group, supergroup, channel).
        user: Пользователь-отправитель.

    Returns:
        Мок объекта Message.
    """
    chat = Chat(id=123, type=chat_type)

    message = MagicMock(spec=Message)
    message.chat = chat
    message.from_user = user

    return message


def create_mock_callback_query(chat_type: ChatType, user: User) -> CallbackQuery:
    """Создать мок CallbackQuery с заданным типом чата.

    Args:
        chat_type: Тип чата для message внутри callback_query.
        user: Пользователь.

    Returns:
        Мок объекта CallbackQuery.
    """
    chat = Chat(id=123, type=chat_type)

    inner_message = MagicMock(spec=Message)
    inner_message.chat = chat

    callback_query = MagicMock(spec=CallbackQuery)
    callback_query.from_user = user
    callback_query.message = inner_message

    return callback_query


# ==============================================================================
# ТЕСТЫ ДЛЯ MESSAGE
# ==============================================================================


@pytest.mark.asyncio
async def test_private_message_allowed(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
    mock_user: User,
) -> None:
    """Сообщения из личных чатов должны пропускаться."""
    message = create_mock_message(ChatType.PRIVATE, mock_user)

    result = await middleware(mock_handler, message, {})

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(message, {})
    assert result == "handled"


@pytest.mark.asyncio
async def test_group_message_blocked(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
    mock_user: User,
) -> None:
    """Сообщения из групп должны игнорироваться."""
    message = create_mock_message(ChatType.GROUP, mock_user)

    result = await middleware(mock_handler, message, {})

    # Handler НЕ должен быть вызван
    mock_handler.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_supergroup_message_blocked(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
    mock_user: User,
) -> None:
    """Сообщения из супергрупп должны игнорироваться."""
    message = create_mock_message(ChatType.SUPERGROUP, mock_user)

    result = await middleware(mock_handler, message, {})

    # Handler НЕ должен быть вызван
    mock_handler.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_channel_message_blocked(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
    mock_user: User,
) -> None:
    """Сообщения из каналов должны игнорироваться."""
    message = create_mock_message(ChatType.CHANNEL, mock_user)

    result = await middleware(mock_handler, message, {})

    # Handler НЕ должен быть вызван
    mock_handler.assert_not_called()
    assert result is None


# ==============================================================================
# ТЕСТЫ ДЛЯ CALLBACK_QUERY
# ==============================================================================


@pytest.mark.asyncio
async def test_private_callback_allowed(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
    mock_user: User,
) -> None:
    """CallbackQuery из личных чатов должны пропускаться."""
    callback = create_mock_callback_query(ChatType.PRIVATE, mock_user)

    result = await middleware(mock_handler, callback, {})

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(callback, {})
    assert result == "handled"


@pytest.mark.asyncio
async def test_group_callback_blocked(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
    mock_user: User,
) -> None:
    """CallbackQuery из групп должны игнорироваться."""
    callback = create_mock_callback_query(ChatType.GROUP, mock_user)

    result = await middleware(mock_handler, callback, {})

    # Handler НЕ должен быть вызван
    mock_handler.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_supergroup_callback_blocked(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
    mock_user: User,
) -> None:
    """CallbackQuery из супергрупп должны игнорироваться."""
    callback = create_mock_callback_query(ChatType.SUPERGROUP, mock_user)

    result = await middleware(mock_handler, callback, {})

    # Handler НЕ должен быть вызван
    mock_handler.assert_not_called()
    assert result is None


# ==============================================================================
# EDGE CASES
# ==============================================================================


@pytest.mark.asyncio
async def test_callback_without_message_passes(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
    mock_user: User,
) -> None:
    """CallbackQuery без message должен пропускаться (fallback)."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = mock_user
    callback.message = None

    result = await middleware(mock_handler, callback, {})

    # Handler должен быть вызван (fallback — пропускаем)
    mock_handler.assert_called_once()
    assert result == "handled"


@pytest.mark.asyncio
async def test_unknown_event_type_passes(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
) -> None:
    """Неизвестный тип события должен пропускаться (fallback)."""
    # Создаём какой-то неизвестный объект
    unknown_event = MagicMock()
    unknown_event.__class__.__name__ = "UnknownEvent"

    result = await middleware(mock_handler, unknown_event, {})

    # Handler должен быть вызван (fallback — пропускаем)
    mock_handler.assert_called_once()
    assert result == "handled"


@pytest.mark.asyncio
async def test_data_passed_through(
    middleware: PrivateChatMiddleware,
    mock_handler: AsyncMock,
    mock_user: User,
) -> None:
    """Данные должны передаваться в handler без изменений."""
    message = create_mock_message(ChatType.PRIVATE, mock_user)
    data = {"key": "value", "l10n": "some_localization"}

    await middleware(mock_handler, message, data)

    # Проверяем, что data передан без изменений
    mock_handler.assert_called_once_with(message, data)
