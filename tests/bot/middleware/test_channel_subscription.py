"""Тесты для middleware проверки подписки на канал.

Модуль тестирует:
- ChannelSubscriptionMiddleware (middleware для проверки подписки)

Тестируемая функциональность:
1. Middleware пропускает подписанных пользователей
2. Middleware блокирует неподписанных пользователей
3. Кеширование результата проверки подписки
4. TTL кеша — устаревший кеш перепроверяется
5. Fallback при ошибке API — пропускаем пользователя
6. Отправка сообщения с кнопкой подписки
7. Обработка событий без пользователя
8. Формирование URL для кнопки из разных форматов invite_link
9. clear_cache() очищает кеш

Архитектура тестов:
- Используем Dependency Injection — инжектируем mock bot и time_provider
- Все внешние зависимости мокаются через конструктор
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    ChatMemberLeft,
    ChatMemberMember,
    Message,
    User,
)

from src.bot.middleware.channel_subscription import (
    SUBSCRIBED_STATUSES,
    ChannelSubscriptionMiddleware,
)
from src.utils.i18n import Localization

# ==============================================================================
# ФИКСТУРЫ — БАЗОВЫЕ ОБЪЕКТЫ
# ==============================================================================


@pytest.fixture
def mock_handler() -> AsyncMock:
    """Мок async handler."""
    return AsyncMock(return_value="handler_result")


@pytest.fixture
def mock_bot() -> AsyncMock:
    """Мок Telegram бота."""
    return AsyncMock()


@pytest.fixture
def mock_time_provider() -> MagicMock:
    """Мок провайдера времени для детерминированного тестирования кеша."""
    mock = MagicMock()
    mock.return_value = 1000.0
    return mock


@pytest.fixture
def channel_id() -> int:
    """ID тестового канала."""
    return -1001234567890


@pytest.fixture
def invite_link() -> str:
    """Ссылка на тестовый канал."""
    return "@testchannel"


# ==============================================================================
# ФИКСТУРЫ — TELEGRAM ОБЪЕКТЫ
# ==============================================================================


@pytest.fixture
def mock_user() -> User:
    """Тестовый пользователь."""
    return User(
        id=123456789,
        is_bot=False,
        first_name="Test User",
    )


@pytest.fixture
def mock_message(mock_user: User) -> Message:
    """Мок Message с пользователем."""
    message = MagicMock(spec=Message)
    message.from_user = mock_user
    message.answer = AsyncMock()
    return message


@pytest.fixture
def mock_callback_query(mock_user: User) -> CallbackQuery:
    """Мок CallbackQuery с пользователем."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = mock_user
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.data = None  # По умолчанию None, можно переопределить в тестах
    return callback


@pytest.fixture
def mock_localization() -> MagicMock:
    """Мок объекта локализации."""
    l10n = MagicMock(spec=Localization)
    l10n.get.side_effect = lambda key: {
        "channel_subscription_required": "Требуется подписка на канал",
        "channel_subscription_button": "Подписаться",
    }.get(key, f"[{key}]")
    return l10n


# ==============================================================================
# ФИКСТУРЫ — CHAT MEMBER СТАТУСЫ
# ==============================================================================


@pytest.fixture
def member_status() -> ChatMemberMember:
    """Статус подписанного пользователя."""
    return MagicMock(status=ChatMemberStatus.MEMBER)


@pytest.fixture
def left_status() -> ChatMemberLeft:
    """Статус неподписанного пользователя."""
    return MagicMock(status=ChatMemberStatus.LEFT)


@pytest.fixture
def admin_status() -> MagicMock:
    """Статус администратора канала."""
    return MagicMock(status=ChatMemberStatus.ADMINISTRATOR)


@pytest.fixture
def creator_status() -> MagicMock:
    """Статус создателя канала."""
    return MagicMock(status=ChatMemberStatus.CREATOR)


@pytest.fixture
def kicked_status() -> MagicMock:
    """Статус заблокированного пользователя."""
    return MagicMock(status=ChatMemberStatus.KICKED)


# ==============================================================================
# ТЕСТЫ ПОДПИСАННЫХ ПОЛЬЗОВАТЕЛЕЙ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_passes_subscribed_user(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: middleware пропускает подписанных пользователей."""
    mock_bot.get_chat_member.return_value = member_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_message, data)

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(mock_message, data)
    assert result == "handler_result"

    # get_chat_member должен быть вызван
    mock_bot.get_chat_member.assert_called_once_with(
        chat_id=channel_id,
        user_id=mock_user.id,
    )


@pytest.mark.asyncio
async def test_middleware_passes_admin_user(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    admin_status: MagicMock,
) -> None:
    """Тест: middleware пропускает администраторов канала."""
    mock_bot.get_chat_member.return_value = admin_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_message, data)

    mock_handler.assert_called_once()
    assert result == "handler_result"


@pytest.mark.asyncio
async def test_middleware_passes_creator_user(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    creator_status: MagicMock,
) -> None:
    """Тест: middleware пропускает создателя канала."""
    mock_bot.get_chat_member.return_value = creator_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_message, data)

    mock_handler.assert_called_once()
    assert result == "handler_result"


# ==============================================================================
# ТЕСТЫ НЕПОДПИСАННЫХ ПОЛЬЗОВАТЕЛЕЙ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_blocks_unsubscribed_user(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    left_status: MagicMock,
) -> None:
    """Тест: middleware блокирует неподписанных пользователей."""
    mock_bot.get_chat_member.return_value = left_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_message, data)

    # Handler НЕ должен быть вызван
    mock_handler.assert_not_called()
    assert result is None

    # Должно быть отправлено сообщение
    mock_message.answer.assert_called_once()


@pytest.mark.asyncio
async def test_middleware_blocks_kicked_user(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    kicked_status: MagicMock,
) -> None:
    """Тест: middleware блокирует забаненных пользователей."""
    mock_bot.get_chat_member.return_value = kicked_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_message, data)

    mock_handler.assert_not_called()
    assert result is None


# ==============================================================================
# ТЕСТЫ КЕШИРОВАНИЯ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_caches_subscription_result(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: middleware кеширует результат проверки подписки."""
    mock_bot.get_chat_member.return_value = member_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Первый запрос — вызов API
    await middleware(mock_handler, mock_message, data)
    assert mock_bot.get_chat_member.call_count == 1

    # Второй запрос — из кеша
    await middleware(mock_handler, mock_message, data)
    assert mock_bot.get_chat_member.call_count == 1  # Не изменилось!


@pytest.mark.asyncio
async def test_middleware_cache_expires(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: кеш устаревает после TTL и перепроверяется."""
    mock_bot.get_chat_member.return_value = member_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Первый запрос в момент 1000.0
    mock_time_provider.return_value = 1000.0
    await middleware(mock_handler, mock_message, data)
    assert mock_bot.get_chat_member.call_count == 1

    # Второй запрос через 301 секунду — кеш устарел
    mock_time_provider.return_value = 1301.0
    await middleware(mock_handler, mock_message, data)
    assert mock_bot.get_chat_member.call_count == 2  # Новый вызов API!


@pytest.mark.asyncio
async def test_middleware_cache_not_expired_at_boundary(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: кеш устаревает ровно в момент expires_at (граничное условие).

    Логика: кеш валиден когда current_time < expires_at.
    При current_time == expires_at кеш УЖЕ устарел.
    """
    mock_bot.get_chat_member.return_value = member_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Первый запрос в момент 1000.0
    mock_time_provider.return_value = 1000.0
    await middleware(mock_handler, mock_message, data)

    # Второй запрос ровно через 300 секунд — кеш УЖЕ устарел (граница)
    mock_time_provider.return_value = 1300.0
    await middleware(mock_handler, mock_message, data)
    assert mock_bot.get_chat_member.call_count == 2  # Новый вызов API


@pytest.mark.asyncio
async def test_middleware_no_cache_when_ttl_is_zero(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: при TTL=0 кеширование отключено."""
    mock_bot.get_chat_member.return_value = member_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        cache_ttl_seconds=0,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Каждый запрос вызывает API
    await middleware(mock_handler, mock_message, data)
    await middleware(mock_handler, mock_message, data)
    await middleware(mock_handler, mock_message, data)

    assert mock_bot.get_chat_member.call_count == 3


# ==============================================================================
# ТЕСТЫ FALLBACK ПРИ ОШИБКАХ API
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_fallback_on_api_error(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
) -> None:
    """Тест: при ошибке API пользователь пропускается (fallback)."""
    mock_bot.get_chat_member.side_effect = TelegramAPIError(
        method="getChatMember",  # type: ignore[arg-type]
        message="Bot is not a member of the channel",
    )

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_message, data)

    # Handler ДОЛЖЕН быть вызван (fallback)
    mock_handler.assert_called_once()
    assert result == "handler_result"


@pytest.mark.asyncio
async def test_middleware_does_not_cache_api_errors(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: ошибки API не кешируются."""
    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Первый запрос — ошибка API
    mock_bot.get_chat_member.side_effect = TelegramAPIError(
        method="getChatMember",  # type: ignore[arg-type]
        message="Network error",
    )
    await middleware(mock_handler, mock_message, data)

    # Второй запрос — успех (ошибка не закешировалась)
    mock_bot.get_chat_member.side_effect = None
    mock_bot.get_chat_member.return_value = member_status
    await middleware(mock_handler, mock_message, data)

    # API вызван дважды (ошибка не закешировалась)
    assert mock_bot.get_chat_member.call_count == 2


# ==============================================================================
# ТЕСТЫ СОБЫТИЙ БЕЗ ПОЛЬЗОВАТЕЛЯ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_passes_event_without_user(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_time_provider: MagicMock,
    channel_id: int,
) -> None:
    """Тест: события без пользователя пропускаются."""
    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    # Событие без event_from_user
    message = MagicMock(spec=Message)
    data: dict[str, Any] = {"event_from_user": None}

    result = await middleware(mock_handler, message, data)

    # Handler должен быть вызван
    mock_handler.assert_called_once()
    assert result == "handler_result"

    # API не должен вызываться
    mock_bot.get_chat_member.assert_not_called()


# ==============================================================================
# ТЕСТЫ ФОРМИРОВАНИЯ URL КНОПКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_creates_button_with_at_username(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    left_status: MagicMock,
) -> None:
    """Тест: кнопка создаётся с URL из @username."""
    mock_bot.get_chat_member.return_value = left_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        invite_link="@mychannel",
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    await middleware(mock_handler, mock_message, data)

    # Проверяем что answer был вызван с кнопкой
    mock_message.answer.assert_called_once()
    call_kwargs = mock_message.answer.call_args.kwargs
    keyboard = call_kwargs.get("reply_markup")

    assert keyboard is not None
    # URL должен быть https://t.me/mychannel
    button = keyboard.inline_keyboard[0][0]
    assert button.url == "https://t.me/mychannel"


@pytest.mark.asyncio
async def test_middleware_creates_button_with_https_url(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    left_status: MagicMock,
) -> None:
    """Тест: кнопка создаётся с полным HTTPS URL."""
    mock_bot.get_chat_member.return_value = left_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        invite_link="https://t.me/mychannel",
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    await middleware(mock_handler, mock_message, data)

    call_kwargs = mock_message.answer.call_args.kwargs
    keyboard = call_kwargs.get("reply_markup")
    button = keyboard.inline_keyboard[0][0]
    assert button.url == "https://t.me/mychannel"


@pytest.mark.asyncio
async def test_middleware_creates_button_with_plain_username(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    left_status: MagicMock,
) -> None:
    """Тест: кнопка создаётся из plain username."""
    mock_bot.get_chat_member.return_value = left_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        invite_link="mychannel",
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    await middleware(mock_handler, mock_message, data)

    call_kwargs = mock_message.answer.call_args.kwargs
    keyboard = call_kwargs.get("reply_markup")
    button = keyboard.inline_keyboard[0][0]
    assert button.url == "https://t.me/mychannel"


@pytest.mark.asyncio
async def test_middleware_no_subscribe_button_without_invite_link(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    left_status: MagicMock,
) -> None:
    """Тест: без invite_link кнопка "Подписаться" отсутствует.

    Но кнопка "Проверить подписку" должна присутствовать всегда.
    """
    mock_bot.get_chat_member.return_value = left_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        invite_link=None,  # Нет ссылки
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    await middleware(mock_handler, mock_message, data)

    call_kwargs = mock_message.answer.call_args.kwargs
    keyboard = call_kwargs.get("reply_markup")

    # Клавиатура должна быть (с кнопкой проверки)
    assert keyboard is not None
    # Должна быть только одна строка кнопок (только "Проверить подписку")
    assert len(keyboard.inline_keyboard) == 1
    # Кнопка должна быть callback (не URL)
    button = keyboard.inline_keyboard[0][0]
    assert button.callback_data == "check_channel_sub"
    assert button.url is None


# ==============================================================================
# ТЕСТЫ ЛОКАЛИЗАЦИИ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_uses_localization_for_message(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_localization: MagicMock,
    channel_id: int,
    left_status: MagicMock,
) -> None:
    """Тест: middleware использует локализацию для текста сообщения."""
    mock_bot.get_chat_member.return_value = left_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        invite_link="@test",
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {
        "event_from_user": mock_user,
        "l10n": mock_localization,
    }
    await middleware(mock_handler, mock_message, data)

    # Проверяем что был вызван l10n.get()
    mock_localization.get.assert_any_call("channel_subscription_required")
    mock_localization.get.assert_any_call("channel_subscription_button")


# ==============================================================================
# ТЕСТЫ CALLBACK QUERY
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_works_with_callback_query(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_callback_query: CallbackQuery,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: middleware работает с CallbackQuery."""
    mock_bot.get_chat_member.return_value = member_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_callback_query, data)

    mock_handler.assert_called_once()
    assert result == "handler_result"


@pytest.mark.asyncio
async def test_middleware_blocks_callback_for_unsubscribed(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_callback_query: CallbackQuery,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    left_status: MagicMock,
) -> None:
    """Тест: middleware блокирует CallbackQuery для неподписанных."""
    mock_bot.get_chat_member.return_value = left_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        invite_link="@test",
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_callback_query, data)

    mock_handler.assert_not_called()
    assert result is None

    # Должен быть вызван callback.answer()
    mock_callback_query.answer.assert_called_once()


# ==============================================================================
# ТЕСТЫ CALLBACK "ПРОВЕРИТЬ ПОДПИСКУ"
# ==============================================================================


@pytest.mark.asyncio
async def test_check_subscription_callback_when_subscribed(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_callback_query: CallbackQuery,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: callback 'check_channel_sub' при подписке.

    Должно показывать благодарность и редактировать сообщение.
    """
    from src.bot.middleware.channel_subscription import CALLBACK_CHECK_SUBSCRIPTION

    mock_bot.get_chat_member.return_value = member_status
    mock_callback_query.data = CALLBACK_CHECK_SUBSCRIPTION

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_callback_query, data)

    # Handler НЕ вызывается (callback обрабатывается внутри middleware)
    mock_handler.assert_not_called()
    assert result is None

    # callback.answer() должен быть вызван (убрать "часики")
    mock_callback_query.answer.assert_called_once()

    # Сообщение должно быть отредактировано
    mock_callback_query.message.edit_text.assert_called_once()
    call_args = mock_callback_query.message.edit_text.call_args
    text = call_args[0][0]
    assert "Спасибо за подписку" in text or "✅" in text


@pytest.mark.asyncio
async def test_check_subscription_callback_when_not_subscribed(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_callback_query: CallbackQuery,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    left_status: MagicMock,
) -> None:
    """Тест: callback 'check_channel_sub' при отсутствии подписки — alert."""
    from src.bot.middleware.channel_subscription import CALLBACK_CHECK_SUBSCRIPTION

    mock_bot.get_chat_member.return_value = left_status
    mock_callback_query.data = CALLBACK_CHECK_SUBSCRIPTION

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}
    result = await middleware(mock_handler, mock_callback_query, data)

    # Handler НЕ вызывается
    mock_handler.assert_not_called()
    assert result is None

    # callback.answer() должен быть вызван с текстом alert
    mock_callback_query.answer.assert_called_once()
    call_kwargs = mock_callback_query.answer.call_args.kwargs
    assert "show_alert" in call_kwargs
    assert call_kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_check_subscription_callback_clears_cache_before_check(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_callback_query: CallbackQuery,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
    left_status: MagicMock,
) -> None:
    """Тест: callback 'check_channel_sub' очищает кеш перед проверкой."""
    from src.bot.middleware.channel_subscription import CALLBACK_CHECK_SUBSCRIPTION

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Первый запрос — кешируем статус "не подписан"
    mock_bot.get_chat_member.return_value = left_status
    mock_callback_query.data = None  # Обычный callback
    await middleware(mock_handler, mock_callback_query, data)
    assert mock_bot.get_chat_member.call_count == 1

    # Пользователь подписывается (меняем статус)
    mock_bot.get_chat_member.return_value = member_status

    # Без callback 'check_channel_sub' кеш бы использовался
    await middleware(mock_handler, mock_callback_query, data)
    assert mock_bot.get_chat_member.call_count == 1  # Из кеша!

    # Теперь callback 'check_channel_sub' — должен очистить кеш и перепроверить
    mock_callback_query.data = CALLBACK_CHECK_SUBSCRIPTION
    await middleware(mock_handler, mock_callback_query, data)
    assert mock_bot.get_chat_member.call_count == 2  # Новый вызов API!


@pytest.mark.asyncio
async def test_check_subscription_callback_with_localization_subscribed(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_callback_query: CallbackQuery,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_localization: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: callback 'check_channel_sub' использует локализацию при подписке."""
    from src.bot.middleware.channel_subscription import CALLBACK_CHECK_SUBSCRIPTION

    mock_bot.get_chat_member.return_value = member_status
    mock_callback_query.data = CALLBACK_CHECK_SUBSCRIPTION

    # Настраиваем локализацию
    mock_localization.get.side_effect = lambda key: {
        "channel_subscription_thanks": "Thank you for subscribing!"
    }.get(key, f"[{key}]")

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {
        "event_from_user": mock_user,
        "l10n": mock_localization,
    }
    await middleware(mock_handler, mock_callback_query, data)

    # Проверяем что был вызван l10n.get()
    mock_localization.get.assert_called_with("channel_subscription_thanks")

    # Текст должен быть из локализации
    call_args = mock_callback_query.message.edit_text.call_args
    text = call_args[0][0]
    assert text == "Thank you for subscribing!"


@pytest.mark.asyncio
async def test_check_subscription_callback_with_localization_not_subscribed(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_callback_query: CallbackQuery,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_localization: MagicMock,
    channel_id: int,
    left_status: MagicMock,
) -> None:
    """Тест: callback использует локализацию при отсутствии подписки.

    Callback 'check_channel_sub' должен показать alert с локализованным текстом.
    """
    from src.bot.middleware.channel_subscription import CALLBACK_CHECK_SUBSCRIPTION

    mock_bot.get_chat_member.return_value = left_status
    mock_callback_query.data = CALLBACK_CHECK_SUBSCRIPTION

    # Настраиваем локализацию
    mock_localization.get.side_effect = lambda key: {
        "channel_subscription_not_subscribed": "You are not subscribed yet!"
    }.get(key, f"[{key}]")

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {
        "event_from_user": mock_user,
        "l10n": mock_localization,
    }
    await middleware(mock_handler, mock_callback_query, data)

    # Проверяем что был вызван l10n.get()
    mock_localization.get.assert_called_with("channel_subscription_not_subscribed")

    # Текст alert должен быть из локализации
    call_args = mock_callback_query.answer.call_args[0]
    assert call_args[0] == "You are not subscribed yet!"


# ==============================================================================
# ТЕСТЫ CLEAR_CACHE
# ==============================================================================


@pytest.mark.asyncio
async def test_clear_cache_for_specific_user(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: clear_cache() очищает кеш для конкретного пользователя."""
    mock_bot.get_chat_member.return_value = member_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Первый запрос — кешируется
    await middleware(mock_handler, mock_message, data)
    assert mock_bot.get_chat_member.call_count == 1

    # Очищаем кеш для пользователя
    middleware.clear_cache(mock_user.id)

    # Следующий запрос — снова вызывает API
    await middleware(mock_handler, mock_message, data)
    assert mock_bot.get_chat_member.call_count == 2


@pytest.mark.asyncio
async def test_clear_cache_for_all_users(
    mock_bot: AsyncMock,
    mock_handler: AsyncMock,
    mock_time_provider: MagicMock,
    channel_id: int,
    member_status: MagicMock,
) -> None:
    """Тест: clear_cache() без аргументов очищает весь кеш."""
    mock_bot.get_chat_member.return_value = member_status

    middleware = ChannelSubscriptionMiddleware(
        bot=mock_bot,
        channel_id=channel_id,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    # Создаём двух пользователей
    user1 = User(id=111, is_bot=False, first_name="User 1")
    user2 = User(id=222, is_bot=False, first_name="User 2")

    msg1 = MagicMock(spec=Message)
    msg1.answer = AsyncMock()

    msg2 = MagicMock(spec=Message)
    msg2.answer = AsyncMock()

    # Кешируем для обоих
    await middleware(mock_handler, msg1, {"event_from_user": user1})
    await middleware(mock_handler, msg2, {"event_from_user": user2})
    assert mock_bot.get_chat_member.call_count == 2

    # Очищаем весь кеш
    middleware.clear_cache()

    # Следующие запросы — снова вызывают API
    await middleware(mock_handler, msg1, {"event_from_user": user1})
    await middleware(mock_handler, msg2, {"event_from_user": user2})
    assert mock_bot.get_chat_member.call_count == 4


# ==============================================================================
# ТЕСТЫ КОНСТАНТ
# ==============================================================================


def test_subscribed_statuses_contains_expected_values() -> None:
    """Тест: SUBSCRIBED_STATUSES содержит правильные статусы."""
    assert ChatMemberStatus.MEMBER in SUBSCRIBED_STATUSES
    assert ChatMemberStatus.ADMINISTRATOR in SUBSCRIBED_STATUSES
    assert ChatMemberStatus.CREATOR in SUBSCRIBED_STATUSES

    # Эти статусы НЕ должны быть в списке
    assert ChatMemberStatus.LEFT not in SUBSCRIBED_STATUSES
    assert ChatMemberStatus.KICKED not in SUBSCRIBED_STATUSES
    assert ChatMemberStatus.RESTRICTED not in SUBSCRIBED_STATUSES
