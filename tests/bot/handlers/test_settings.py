"""Тесты для обработчика команды /settings.

Модуль тестирует:
- cmd_settings (обработчик команды /settings)
- create_settings_keyboard (создание меню настроек)
- process_settings_language (открытие меню языка)
- process_settings_language_selection (выбор языка)
- process_settings_back (возврат в меню настроек)

Тестируемая функциональность:
1. /settings показывает меню настроек
2. Меню содержит кнопку языка если локализация включена
3. Меню пустое если нет доступных настроек
4. Кнопка "Язык" открывает выбор языка
5. Выбор языка обновляет БД и возвращает в меню
6. Кнопка "Назад" возвращает в меню настроек
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    User,
)

from src.bot.handlers.settings import (
    SETTINGS_LANG_PREFIX,
    SETTINGS_PREFIX,
    SETTINGS_SUB_PREFIX,
    cmd_settings,
    create_settings_keyboard,
    process_settings_back,
    process_settings_language,
    process_settings_language_selection,
    process_subscription_cancel_confirm,
    process_subscription_enable_auto_renewal,
)
from src.db.models.subscription import SubscriptionStatus
from src.db.models.user import User as DbUser
from src.utils.i18n import Localization

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def mock_message() -> MagicMock:
    """Мок Message с пользователем."""
    message = MagicMock(spec=Message)
    message.from_user = User(
        id=123456789,
        is_bot=False,
        first_name="Test User",
    )
    message.answer = AsyncMock()
    return message


@pytest.fixture
def mock_callback_query() -> MagicMock:
    """Мок CallbackQuery с пользователем."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(
        id=123456789,
        is_bot=False,
        first_name="Test User",
    )
    callback.data = f"{SETTINGS_PREFIX}language"
    callback.answer = AsyncMock()

    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()

    return callback


@pytest.fixture
def mock_l10n() -> MagicMock:
    """Мок Localization."""
    l10n = MagicMock(spec=Localization)
    l10n.language = "ru"

    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "settings_title": "⚙️ Настройки\n\nВыберите параметр:",
            "settings_language_button": "🌍 Язык интерфейса",
            "settings_back_button": "← Назад",
            "settings_no_options": "⚙️ Настройки\n\nНет доступных настроек.",
            "settings_language_changed": ("✅ Язык изменён на {language_name}"),
            "language_command": "Выберите язык:",
            "language_name_ru": "🇷🇺 Русский",
            "language_name_en": "🇬🇧 English",
            "error_unknown": "❌ Ошибка",
            "error_callback_data": "❌ Данные не найдены",
            "error_language_not_supported": "❌ Язык не поддерживается",
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    l10n.get.side_effect = get_translation
    return l10n


@pytest.fixture
def mock_db_user() -> MagicMock:
    """Мок пользователя из БД."""
    user = MagicMock(spec=DbUser)
    user.id = 1
    user.telegram_id = 123456789
    user.language = "ru"
    return user


# ==============================================================================
# ТЕСТЫ create_settings_keyboard
# ==============================================================================


def test_create_settings_keyboard_with_localization_enabled(
    mock_l10n: MagicMock,
) -> None:
    """Тест: клавиатура содержит кнопку языка если локализация включена."""
    with patch(
        "src.bot.handlers.settings.Localization.is_enabled",
        return_value=True,
    ):
        keyboard = create_settings_keyboard(mock_l10n)

    assert isinstance(keyboard, InlineKeyboardMarkup)
    assert len(keyboard.inline_keyboard) >= 1

    # Проверяем что есть кнопка языка
    buttons = [btn for row in keyboard.inline_keyboard for btn in row]
    callback_data_list = [btn.callback_data for btn in buttons]
    assert f"{SETTINGS_PREFIX}language" in callback_data_list


def test_create_settings_keyboard_without_localization(
    mock_l10n: MagicMock,
) -> None:
    """Тест: клавиатура содержит только подписки если локализация отключена."""
    with (
        patch(
            "src.bot.handlers.settings.Localization.is_enabled",
            return_value=False,
        ),
        patch("src.bot.handlers.settings.yaml_config") as mock_config,
    ):
        # Подписочные тарифы отсутствуют
        mock_config.has_subscription_tariffs.return_value = False

        keyboard = create_settings_keyboard(mock_l10n)

    assert isinstance(keyboard, InlineKeyboardMarkup)
    # Клавиатура пуста (нет доступных настроек)
    assert len(keyboard.inline_keyboard) == 0


# ==============================================================================
# ТЕСТЫ cmd_settings
# ==============================================================================


@pytest.mark.asyncio
async def test_cmd_settings_shows_menu(
    mock_message: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: /settings показывает меню настроек."""
    with patch(
        "src.bot.handlers.settings.Localization.is_enabled",
        return_value=True,
    ):
        await cmd_settings(mock_message, mock_l10n)

    mock_message.answer.assert_called_once()
    call_kwargs = mock_message.answer.call_args[1]
    assert "reply_markup" in call_kwargs
    assert call_kwargs.get("parse_mode") == "HTML"


@pytest.mark.asyncio
async def test_cmd_settings_shows_no_options_message(
    mock_message: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: /settings показывает сообщение если нет настроек."""
    with (
        patch(
            "src.bot.handlers.settings.Localization.is_enabled",
            return_value=False,
        ),
        patch("src.bot.handlers.settings.yaml_config") as mock_config,
    ):
        # Подписочные тарифы отсутствуют
        mock_config.has_subscription_tariffs.return_value = False

        await cmd_settings(mock_message, mock_l10n)

    mock_message.answer.assert_called_once()
    # Проверяем что вызван get для settings_no_options
    mock_l10n.get.assert_called_with("settings_no_options")


# ==============================================================================
# ТЕСТЫ process_settings_language
# ==============================================================================


@pytest.mark.asyncio
async def test_process_settings_language_shows_language_keyboard(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: кнопка 'Язык' показывает клавиатуру выбора языка."""
    with (
        patch(
            "src.bot.handlers.settings.Localization.is_enabled",
            return_value=True,
        ),
        patch(
            "src.bot.handlers.settings.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch(
            "src.bot.keyboards.inline.language.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
    ):
        await process_settings_language(mock_callback_query, mock_l10n)

    mock_callback_query.message.edit_text.assert_called_once()
    mock_callback_query.answer.assert_called_once()


@pytest.mark.asyncio
async def test_process_settings_language_disabled(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: если локализация отключена — показывает ошибку."""
    with patch(
        "src.bot.handlers.settings.Localization.is_enabled",
        return_value=False,
    ):
        await process_settings_language(mock_callback_query, mock_l10n)

    mock_callback_query.answer.assert_called_once_with(mock_l10n.get("error_unknown"))


# ==============================================================================
# ТЕСТЫ process_settings_language_selection
# ==============================================================================


@pytest.mark.asyncio
async def test_process_settings_language_selection_updates_user(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
) -> None:
    """Тест: выбор языка обновляет User.language в БД."""
    mock_callback_query.data = f"{SETTINGS_LANG_PREFIX}en"

    with (
        patch(
            "src.bot.handlers.settings.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch(
            "src.bot.handlers.settings.Localization.is_enabled",
            return_value=True,
        ),
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.settings.create_localization",
            return_value=mock_l10n,
        ),
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo.update_language = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        await process_settings_language_selection(mock_callback_query, mock_l10n)

    mock_repo.update_language.assert_called_once_with(mock_db_user, "en")


@pytest.mark.asyncio
async def test_process_settings_language_selection_returns_to_menu(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
) -> None:
    """Тест: после выбора языка возвращает в меню настроек."""
    mock_callback_query.data = f"{SETTINGS_LANG_PREFIX}en"

    with (
        patch(
            "src.bot.handlers.settings.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch(
            "src.bot.handlers.settings.Localization.is_enabled",
            return_value=True,
        ),
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.settings.create_localization",
            return_value=mock_l10n,
        ),
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo.update_language = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        await process_settings_language_selection(mock_callback_query, mock_l10n)

    # Проверяем что edit_text вызван с клавиатурой настроек
    mock_callback_query.message.edit_text.assert_called_once()
    call_kwargs = mock_callback_query.message.edit_text.call_args[1]
    assert "reply_markup" in call_kwargs


@pytest.mark.asyncio
async def test_process_settings_language_selection_rejects_unavailable(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: выбор недоступного языка отклоняется."""
    mock_callback_query.data = f"{SETTINGS_LANG_PREFIX}fr"

    with patch(
        "src.bot.handlers.settings.Localization.get_available_languages",
        return_value=["ru", "en"],
    ):
        await process_settings_language_selection(mock_callback_query, mock_l10n)

    mock_callback_query.answer.assert_called_once()


# ==============================================================================
# ТЕСТЫ process_settings_back
# ==============================================================================


@pytest.mark.asyncio
async def test_process_settings_back_returns_to_menu(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: кнопка 'Назад' возвращает в меню настроек."""
    mock_callback_query.data = f"{SETTINGS_PREFIX}back"

    with patch(
        "src.bot.handlers.settings.Localization.is_enabled",
        return_value=True,
    ):
        await process_settings_back(mock_callback_query, mock_l10n)

    mock_callback_query.message.edit_text.assert_called_once()
    mock_callback_query.answer.assert_called_once()


# ==============================================================================
# ТЕСТЫ ОБРАБОТКИ ОШИБОК
# ==============================================================================


@pytest.mark.asyncio
async def test_process_settings_language_selection_missing_callback_data(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: обработка отсутствия callback_data."""
    # callback.data отрезает префикс, но если данных нет — callback_data будет None
    mock_callback_query.data = None

    with patch(
        "src.bot.handlers.settings.Localization.get_available_languages",
        return_value=["ru", "en"],
    ):
        await process_settings_language_selection(mock_callback_query, mock_l10n)

    # Должен ответить ошибкой о недоступных данных (callback_data отсутствует)
    mock_callback_query.answer.assert_called_once_with(
        mock_l10n.get("error_callback_data")
    )


@pytest.mark.asyncio
async def test_process_settings_language_selection_user_not_found(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: обработка отсутствия пользователя в БД."""

    mock_callback_query.data = f"{SETTINGS_LANG_PREFIX}en"
    mock_l10n.get.return_value = "❌ Пользователь не найден"

    with (
        patch(
            "src.bot.handlers.settings.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=None)
        mock_repo_cls.return_value = mock_repo

        await process_settings_language_selection(mock_callback_query, mock_l10n)

    # Проверяем что вызван callback.answer с show_alert=True
    mock_callback_query.answer.assert_called_once()
    call_args = mock_callback_query.answer.call_args
    assert call_args[1].get("show_alert") is True


@pytest.mark.asyncio
async def test_process_settings_language_selection_database_connection_error(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: обработка ошибки подключения к БД."""
    from src.db.exceptions import DatabaseConnectionError

    mock_callback_query.data = f"{SETTINGS_LANG_PREFIX}en"
    mock_l10n.get.return_value = "❌ Временная ошибка БД"

    with (
        patch(
            "src.bot.handlers.settings.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
    ):
        # Симулируем ошибку подключения
        mock_session_cls.return_value.__aenter__.side_effect = DatabaseConnectionError(
            OSError("Connection failed")
        )

        await process_settings_language_selection(mock_callback_query, mock_l10n)

    # Проверяем что вызван callback.answer с error_db_temporary
    mock_callback_query.answer.assert_called_once()
    call_args = mock_callback_query.answer.call_args
    assert call_args[1].get("show_alert") is True


@pytest.mark.asyncio
async def test_process_settings_language_selection_database_operation_error(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
) -> None:
    """Тест: обработка ошибки операции БД."""
    from src.db.exceptions import DatabaseOperationError

    mock_callback_query.data = f"{SETTINGS_LANG_PREFIX}en"
    mock_l10n.get.return_value = "❌ Ошибка операции БД"

    with (
        patch(
            "src.bot.handlers.settings.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        # Симулируем ошибку при update_language
        mock_repo.update_language = AsyncMock(
            side_effect=DatabaseOperationError(
                "update_language",
                Exception("Database error"),
                retryable=False,
            )
        )
        mock_repo_cls.return_value = mock_repo

        await process_settings_language_selection(mock_callback_query, mock_l10n)

    # Проверяем что вызван callback.answer с error_db_permanent
    mock_callback_query.answer.assert_called_once()
    call_args = mock_callback_query.answer.call_args
    assert call_args[1].get("show_alert") is True


# ==============================================================================
# ТЕСТЫ УПРАВЛЕНИЯ ПОДПИСКОЙ (TELEGRAM STARS)
# ==============================================================================


@pytest.fixture
def mock_bot() -> MagicMock:
    """Мок Bot для управления подписками Stars."""
    from aiogram import Bot

    bot = MagicMock(spec=Bot)
    bot.edit_user_star_subscription = AsyncMock()
    return bot


@pytest.fixture
def mock_subscription_stars() -> MagicMock:
    """Мок активной подписки Telegram Stars."""
    from datetime import datetime, timedelta

    from src.db.models.subscription import Subscription

    subscription = MagicMock(spec=Subscription)
    subscription.id = 1
    subscription.provider = "telegram_stars"
    subscription.payment_method_id = "tg_charge_123456"
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.auto_renewal = True
    subscription.cancel_at_period_end = False
    subscription.period_end = datetime.now() + timedelta(days=25)
    return subscription


@pytest.fixture
def mock_subscription_yookassa() -> MagicMock:
    """Мок активной подписки YooKassa (не Stars)."""
    from datetime import datetime, timedelta

    from src.db.models.subscription import Subscription

    subscription = MagicMock(spec=Subscription)
    subscription.id = 2
    subscription.provider = "yookassa"
    subscription.payment_method_id = "pm_123456"
    subscription.status = SubscriptionStatus.ACTIVE
    subscription.auto_renewal = True
    subscription.cancel_at_period_end = False
    subscription.period_end = datetime.now() + timedelta(days=25)
    return subscription


@pytest.mark.asyncio
async def test_cancel_stars_subscription_calls_bot_api(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
    mock_subscription_stars: MagicMock,
    mock_bot: MagicMock,
) -> None:
    """Тест: отмена Stars подписки вызывает bot.edit_user_star_subscription."""
    mock_callback_query.data = f"{SETTINGS_SUB_PREFIX}cancel_confirm"

    with (
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.settings.create_subscription_service"
        ) as mock_service_cls,
    ):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        mock_service = MagicMock()
        mock_service.get_active_subscription = AsyncMock(
            return_value=mock_subscription_stars
        )
        mock_service.cancel_subscription = AsyncMock()
        mock_service_cls.return_value = mock_service

        await process_subscription_cancel_confirm(
            mock_callback_query, mock_l10n, mock_bot
        )

        # КРИТИЧНО: проверяем вызов edit_user_star_subscription
        mock_bot.edit_user_star_subscription.assert_called_once_with(
            user_id=123456789,
            telegram_payment_charge_id="tg_charge_123456",
            is_canceled=True,
        )

        # Проверяем что подписка отменена в БД
        mock_service.cancel_subscription.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_non_stars_subscription_skips_bot_api(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
    mock_subscription_yookassa: MagicMock,
    mock_bot: MagicMock,
) -> None:
    """Тест: отмена НЕ-Stars подписки НЕ вызывает bot.edit_user_star_subscription."""
    mock_callback_query.data = f"{SETTINGS_SUB_PREFIX}cancel_confirm"

    with (
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.settings.create_subscription_service"
        ) as mock_service_cls,
    ):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        mock_service = MagicMock()
        mock_service.get_active_subscription = AsyncMock(
            return_value=mock_subscription_yookassa
        )
        mock_service.cancel_subscription = AsyncMock()
        mock_service_cls.return_value = mock_service

        await process_subscription_cancel_confirm(
            mock_callback_query, mock_l10n, mock_bot
        )

        # КРИТИЧНО: НЕ должен вызываться для НЕ-Stars подписок
        mock_bot.edit_user_star_subscription.assert_not_called()

        # Подписка должна быть отменена в БД
        mock_service.cancel_subscription.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_stars_subscription_continues_on_api_error(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
    mock_subscription_stars: MagicMock,
    mock_bot: MagicMock,
) -> None:
    """Тест: ошибка API Telegram не блокирует отмену в БД."""
    mock_callback_query.data = f"{SETTINGS_SUB_PREFIX}cancel_confirm"

    # Симулируем ошибку при вызове Telegram API
    mock_bot.edit_user_star_subscription.side_effect = Exception("Telegram API error")

    with (
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.settings.create_subscription_service"
        ) as mock_service_cls,
    ):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        mock_service = MagicMock()
        mock_service.get_active_subscription = AsyncMock(
            return_value=mock_subscription_stars
        )
        mock_service.cancel_subscription = AsyncMock()
        mock_service_cls.return_value = mock_service

        # НЕ должно быть исключения
        await process_subscription_cancel_confirm(
            mock_callback_query, mock_l10n, mock_bot
        )

        # Подписка всё равно должна быть отменена в БД
        mock_service.cancel_subscription.assert_called_once()


@pytest.mark.asyncio
async def test_enable_stars_subscription_calls_bot_api(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
    mock_subscription_stars: MagicMock,
    mock_bot: MagicMock,
) -> None:
    """Тест: восстановление Stars подписки вызывает bot.edit_user_star_subscription."""
    # Подписка отменена, но ещё активна
    mock_subscription_stars.auto_renewal = False
    mock_subscription_stars.cancel_at_period_end = True

    mock_callback_query.data = f"{SETTINGS_SUB_PREFIX}enable"

    with (
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.settings.create_subscription_service"
        ) as mock_service_cls,
    ):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        mock_service = MagicMock()
        mock_service.get_active_subscription = AsyncMock(
            return_value=mock_subscription_stars
        )
        mock_service_cls.return_value = mock_service

        await process_subscription_enable_auto_renewal(
            mock_callback_query, mock_l10n, mock_bot
        )

        # КРИТИЧНО: проверяем вызов edit_user_star_subscription с is_canceled=False
        mock_bot.edit_user_star_subscription.assert_called_once_with(
            user_id=123456789,
            telegram_payment_charge_id="tg_charge_123456",
            is_canceled=False,
        )

        # Проверяем что подписка восстановлена в БД
        assert mock_subscription_stars.auto_renewal is True
        assert mock_subscription_stars.cancel_at_period_end is False
        assert mock_subscription_stars.status == SubscriptionStatus.ACTIVE


@pytest.mark.asyncio
async def test_enable_non_stars_subscription_skips_bot_api(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
    mock_subscription_yookassa: MagicMock,
    mock_bot: MagicMock,
) -> None:
    """Тест: восстановление НЕ-Stars подписки НЕ вызывает bot API."""
    mock_subscription_yookassa.auto_renewal = False
    mock_subscription_yookassa.cancel_at_period_end = True

    mock_callback_query.data = f"{SETTINGS_SUB_PREFIX}enable"

    with (
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.settings.create_subscription_service"
        ) as mock_service_cls,
    ):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        mock_service = MagicMock()
        mock_service.get_active_subscription = AsyncMock(
            return_value=mock_subscription_yookassa
        )
        mock_service_cls.return_value = mock_service

        await process_subscription_enable_auto_renewal(
            mock_callback_query, mock_l10n, mock_bot
        )

        # КРИТИЧНО: НЕ должен вызываться для НЕ-Stars подписок
        mock_bot.edit_user_star_subscription.assert_not_called()

        # Подписка должна быть восстановлена в БД
        assert mock_subscription_yookassa.auto_renewal is True


@pytest.mark.asyncio
async def test_enable_stars_subscription_fails_on_api_error(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
    mock_subscription_stars: MagicMock,
    mock_bot: MagicMock,
) -> None:
    """Тест: ошибка API Telegram блокирует восстановление в БД."""
    mock_subscription_stars.auto_renewal = False

    mock_callback_query.data = f"{SETTINGS_SUB_PREFIX}enable"

    # Симулируем ошибку при вызове Telegram API
    mock_bot.edit_user_star_subscription.side_effect = Exception("Telegram API error")

    # Добавляем перевод для ошибки
    def _get_translation(key: str, **_kwargs: Any) -> str:
        if key == "settings_subscription_restore_failed":
            return "Не удалось восстановить подписку"
        return key

    mock_l10n.get.side_effect = _get_translation

    with (
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.settings.create_subscription_service"
        ) as mock_service_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        mock_service = MagicMock()
        mock_service.get_active_subscription = AsyncMock(
            return_value=mock_subscription_stars
        )
        mock_service_cls.return_value = mock_service

        await process_subscription_enable_auto_renewal(
            mock_callback_query, mock_l10n, mock_bot
        )

        # Должна быть показана ошибка пользователю
        mock_callback_query.answer.assert_called()
        call_args = mock_callback_query.answer.call_args
        assert call_args[1].get("show_alert") is True

        # БД НЕ должна быть обновлена
        assert mock_subscription_stars.auto_renewal is False


@pytest.mark.asyncio
async def test_cancel_stars_subscription_without_payment_id(
    mock_callback_query: MagicMock,
    mock_l10n: MagicMock,
    mock_db_user: MagicMock,
    mock_subscription_stars: MagicMock,
    mock_bot: MagicMock,
) -> None:
    """Тест: Stars подписка без payment_method_id НЕ вызывает bot API."""
    # Подписка без payment_method_id (некорректное состояние, но проверяем)
    mock_subscription_stars.payment_method_id = None

    mock_callback_query.data = f"{SETTINGS_SUB_PREFIX}cancel_confirm"

    with (
        patch("src.bot.handlers.settings.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.settings.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.settings.create_subscription_service"
        ) as mock_service_cls,
    ):
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        mock_service = MagicMock()
        mock_service.get_active_subscription = AsyncMock(
            return_value=mock_subscription_stars
        )
        mock_service.cancel_subscription = AsyncMock()
        mock_service_cls.return_value = mock_service

        await process_subscription_cancel_confirm(
            mock_callback_query, mock_l10n, mock_bot
        )

        # НЕ должен вызываться без payment_method_id
        mock_bot.edit_user_star_subscription.assert_not_called()

        # Но подписка всё равно должна быть отменена в БД
        mock_service.cancel_subscription.assert_called_once()
