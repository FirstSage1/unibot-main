"""Тесты для обработчика команды /language.

Модуль тестирует:
- cmd_language (обработчик команды /language)
- process_language_selection (обработчик callback при выборе языка)
- create_language_keyboard (создание клавиатуры с языками)

Тестируемая функциональность:
1. /language показывает клавиатуру с доступными языками
2. /language игнорируется если мультиязычность отключена
3. Клавиатура содержит кнопки для всех available_languages
4. Кнопки имеют правильные callback_data и текст
5. Выбор языка обновляет User.language в БД
6. Выбор языка отправляет подтверждение на НОВОМ языке
7. Обработка недоступного языка в callback
8. Обработка отсутствия пользователя в БД
9. Обработка ошибок при обновлении языка
10. Проверка что callback отвечает (убирает "часики")
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    User,
)

from src.bot.handlers.language import (
    cmd_language,
    process_language_selection,
)
from src.bot.keyboards import create_language_keyboard
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
    callback.data = "lang:en"
    callback.answer = AsyncMock()

    # Мок для callback.message
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()

    return callback


@pytest.fixture
def mock_l10n_ru() -> MagicMock:
    """Мок Localization для русского языка."""
    l10n = MagicMock(spec=Localization)
    l10n.language = "ru"

    # Настраиваем get() для возврата переводов
    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "language_command": "Выберите язык интерфейса:",
            "language_name_ru": "🇷🇺 Русский",
            "language_name_en": "🇬🇧 English",
            "language_changed": "✅ Язык интерфейса изменён на {language_name}",
            "error_language_not_supported": "❌ Этот язык пока не поддерживается.",
            "error_unknown": "❌ Произошла ошибка. Попробуйте ещё раз.",
            "error_callback_data": "❌ Ошибка: данные не найдены",
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    l10n.get.side_effect = get_translation
    return l10n


@pytest.fixture
def mock_l10n_en() -> MagicMock:
    """Мок Localization для английского языка."""
    l10n = MagicMock(spec=Localization)
    l10n.language = "en"

    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "language_command": "Choose your interface language:",
            "language_name_ru": "🇷🇺 Russian",
            "language_name_en": "🇬🇧 English",
            "language_changed": "✅ Interface language changed to {language_name}",
            "error_language_not_supported": "❌ This language is not supported yet.",
            "error_unknown": "❌ An error occurred. Please try again.",
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
# ТЕСТЫ create_language_keyboard
# ==============================================================================


def test_create_language_keyboard_returns_markup(mock_l10n_ru: MagicMock) -> None:
    """Тест: create_language_keyboard возвращает InlineKeyboardMarkup."""
    with patch(
        "src.bot.keyboards.inline.language.Localization.get_available_languages",
        return_value=["ru", "en"],
    ):
        keyboard = create_language_keyboard(mock_l10n_ru)

    assert isinstance(keyboard, InlineKeyboardMarkup)


def testcreate_language_keyboard_contains_all_available_languages(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: клавиатура содержит кнопки для всех доступных языков."""
    with patch(
        "src.bot.keyboards.inline.language.Localization.get_available_languages",
        return_value=["ru", "en"],
    ):
        keyboard = create_language_keyboard(mock_l10n_ru)

    # Проверяем количество кнопок
    assert len(keyboard.inline_keyboard) == 2

    # Проверяем callback_data
    buttons = [row[0] for row in keyboard.inline_keyboard]
    callback_data_list = [btn.callback_data for btn in buttons]

    assert "lang:ru" in callback_data_list
    assert "lang:en" in callback_data_list


def testcreate_language_keyboard_buttons_have_correct_text(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: кнопки имеют правильный текст (названия языков)."""
    with patch(
        "src.bot.keyboards.inline.language.Localization.get_available_languages",
        return_value=["ru", "en"],
    ):
        keyboard = create_language_keyboard(mock_l10n_ru)

    # Проверяем что клавиатура создана
    assert keyboard.inline_keyboard

    # Проверяем что get был вызван для каждого языка
    expected_calls = [
        call("language_name_ru"),
        call("language_name_en"),
    ]
    mock_l10n_ru.get.assert_has_calls(expected_calls, any_order=True)


def testcreate_language_keyboard_each_button_on_separate_row(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: каждая кнопка на отдельной строке."""
    with patch(
        "src.bot.keyboards.inline.language.Localization.get_available_languages",
        return_value=["ru", "en", "zh"],
    ):
        keyboard = create_language_keyboard(mock_l10n_ru)

    # Должно быть 3 строки (по одной кнопке в каждой)
    assert len(keyboard.inline_keyboard) == 3

    # Каждая строка содержит ровно 1 кнопку
    for row in keyboard.inline_keyboard:
        assert len(row) == 1


def test_create_language_keyboard_with_custom_prefix(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: клавиатура использует кастомный префикс callback_data."""
    custom_prefix = "settings_lang:"

    with patch(
        "src.bot.keyboards.inline.language.Localization.get_available_languages",
        return_value=["ru", "en"],
    ):
        keyboard = create_language_keyboard(mock_l10n_ru, callback_prefix=custom_prefix)

    # Получаем все кнопки из клавиатуры
    buttons = [btn for row in keyboard.inline_keyboard for btn in row]
    callback_data_list = [btn.callback_data for btn in buttons]

    # Проверяем что все кнопки используют кастомный префикс
    assert "settings_lang:ru" in callback_data_list
    assert "settings_lang:en" in callback_data_list

    # Проверяем что старый формат не используется (default prefix "lang:")
    assert "lang:ru" not in callback_data_list
    assert "lang:en" not in callback_data_list


# ==============================================================================
# ТЕСТЫ cmd_language
# ==============================================================================


@pytest.mark.asyncio
async def test_cmd_language_sends_message_with_keyboard(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /language отправляет сообщение с клавиатурой."""
    with (
        patch("src.bot.handlers.language.Localization.is_enabled", return_value=True),
        patch(
            "src.bot.keyboards.inline.language.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
    ):
        await cmd_language(mock_message, mock_l10n_ru)

    # Проверяем что answer был вызван
    mock_message.answer.assert_called_once()

    # Проверяем аргументы вызова
    call_args = mock_message.answer.call_args
    assert call_args is not None

    # Первый аргумент — текст сообщения
    assert call_args[0][0] == "Выберите язык интерфейса:"

    # reply_markup должен быть InlineKeyboardMarkup
    reply_markup = call_args[1].get("reply_markup")
    assert isinstance(reply_markup, InlineKeyboardMarkup)


@pytest.mark.asyncio
async def test_cmd_language_ignores_command_if_disabled(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /language игнорируется если мультиязычность отключена."""
    with patch("src.bot.handlers.language.Localization.is_enabled", return_value=False):
        await cmd_language(mock_message, mock_l10n_ru)

    # answer НЕ должен быть вызван — команда проигнорирована
    mock_message.answer.assert_not_called()


# ==============================================================================
# ТЕСТЫ process_language_selection
# ==============================================================================


@pytest.mark.asyncio
async def test_process_language_selection_updates_user_language(
    mock_callback_query: MagicMock,
    mock_l10n_ru: MagicMock,
    mock_l10n_en: MagicMock,
    mock_db_user: MagicMock,
) -> None:
    """Тест: выбор языка обновляет User.language в БД."""
    mock_callback_query.data = "lang:en"

    with (
        patch(
            "src.bot.handlers.language.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch("src.bot.handlers.language.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.language.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.language.create_localization", return_value=mock_l10n_en
        ),
    ):
        # Настраиваем DatabaseSession
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        # Настраиваем UserRepository
        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo.update_language = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        await process_language_selection(mock_callback_query, mock_l10n_ru)

    # Проверяем что update_language был вызван
    mock_repo.update_language.assert_called_once_with(mock_db_user, "en")


@pytest.mark.asyncio
async def test_process_language_selection_sends_confirmation_in_new_language(
    mock_callback_query: MagicMock,
    mock_l10n_ru: MagicMock,
    mock_l10n_en: MagicMock,
    mock_db_user: MagicMock,
) -> None:
    """Тест: подтверждение отправляется на НОВОМ языке."""
    mock_callback_query.data = "lang:en"

    with (
        patch(
            "src.bot.handlers.language.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch("src.bot.handlers.language.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.language.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.language.create_localization", return_value=mock_l10n_en
        ) as mock_create_l10n,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo.update_language = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        await process_language_selection(mock_callback_query, mock_l10n_ru)

    # Проверяем что был создан НОВЫЙ объект Localization с выбранным языком
    mock_create_l10n.assert_called_with("en")

    # Проверяем что edit_text был вызван с переводом на новом языке
    mock_callback_query.message.edit_text.assert_called_once()


@pytest.mark.asyncio
async def test_process_language_selection_answers_callback(
    mock_callback_query: MagicMock,
    mock_l10n_ru: MagicMock,
    mock_l10n_en: MagicMock,
    mock_db_user: MagicMock,
) -> None:
    """Тест: callback отвечает (убирает 'часики')."""
    mock_callback_query.data = "lang:en"

    with (
        patch(
            "src.bot.handlers.language.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch("src.bot.handlers.language.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.language.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.language.create_localization", return_value=mock_l10n_en
        ),
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
        mock_repo.update_language = AsyncMock(return_value=mock_db_user)
        mock_repo_cls.return_value = mock_repo

        await process_language_selection(mock_callback_query, mock_l10n_ru)

    # Проверяем что answer был вызван
    mock_callback_query.answer.assert_called_once()


@pytest.mark.asyncio
async def test_process_language_selection_rejects_unavailable_language(
    mock_callback_query: MagicMock,
    mock_l10n_ru: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: выбор недоступного языка отклоняется."""
    mock_callback_query.data = "lang:fr"  # Недоступный язык

    with patch(
        "src.bot.handlers.language.Localization.get_available_languages",
        return_value=["ru", "en"],
    ):
        await process_language_selection(mock_callback_query, mock_l10n_ru)

    # Проверяем что был отправлен ответ с ошибкой
    mock_callback_query.answer.assert_called_once_with(
        "❌ Этот язык пока не поддерживается."
    )

    # Проверяем предупреждение в логах
    assert any(
        "Попытка выбрать недоступный язык" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_process_language_selection_handles_missing_user(
    mock_callback_query: MagicMock,
    mock_l10n_ru: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: обработка отсутствия пользователя в БД."""
    mock_callback_query.data = "lang:en"

    with (
        patch(
            "src.bot.handlers.language.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch("src.bot.handlers.language.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.language.UserRepository") as mock_repo_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        # Пользователь не найден
        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=None)
        mock_repo_cls.return_value = mock_repo

        await process_language_selection(mock_callback_query, mock_l10n_ru)

    # Должен быть отправлен ответ с ошибкой
    mock_callback_query.answer.assert_called_once_with(
        "❌ Произошла ошибка. Попробуйте ещё раз."
    )

    # Должна быть ошибка в логах
    assert any(
        "Пользователь не найден в БД" in record.message for record in caplog.records
    )


@pytest.mark.asyncio
async def test_process_language_selection_handles_database_error(
    mock_callback_query: MagicMock,
    mock_l10n_ru: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: обработка ошибки при обновлении языка."""
    from sqlalchemy.exc import SQLAlchemyError

    mock_callback_query.data = "lang:en"

    with (
        patch(
            "src.bot.handlers.language.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch("src.bot.handlers.language.DatabaseSession") as mock_session_cls,
    ):
        # DatabaseSession выбрасывает SQLAlchemyError
        mock_session_cls.return_value.__aenter__.side_effect = SQLAlchemyError(
            "Database error"
        )

        await process_language_selection(mock_callback_query, mock_l10n_ru)

    # Должен быть отправлен ответ с ошибкой
    mock_callback_query.answer.assert_called_once_with(
        "❌ Произошла ошибка. Попробуйте ещё раз."
    )

    # Должна быть ошибка в логах
    assert any("Ошибка БД" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_process_language_selection_handles_missing_callback_data(
    mock_callback_query: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: обработка отсутствия callback_data."""
    mock_callback_query.data = None

    await process_language_selection(mock_callback_query, mock_l10n_ru)

    # Должен быть отправлен ответ с ошибкой
    mock_callback_query.answer.assert_called_once_with("❌ Ошибка: данные не найдены")


# Примечание: тест test_process_language_selection_handles_callback_without_user
# удалён, так как в aiogram callback.from_user для CallbackQuery всегда User,
# он не может быть None. Это невозможный сценарий.
