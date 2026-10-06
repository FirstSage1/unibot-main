"""Тесты для обработчика команды /start.

Модуль тестирует:
- cmd_start (обработчик команды /start)
- _extract_start_param (извлечение start-параметра)
- _detect_user_language (определение языка пользователя)

Тестируемая функциональность:
1. /start создаёт пользователя в БД при первом запуске
2. /start обновляет данные пользователя при повторном запуске
3. /start отправляет приветственное сообщение на языке пользователя
4. Извлечение start-параметра из deep link
5. Автоопределение языка из Telegram language_code
6. Fallback на default_language если язык недоступен
7. Обработка отсутствия from_user
8. Логирование создания/обновления пользователя
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message, User

from src.bot.handlers.start import (
    _detect_user_language,
    _extract_start_param,
    cmd_start,
)
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
        last_name="Last Name",
        username="testuser",
        language_code="ru",
    )
    message.text = "/start"
    message.answer = AsyncMock()
    message.answer_photo = AsyncMock()
    return message


@pytest.fixture
def mock_l10n_ru() -> MagicMock:
    """Мок Localization для русского языка."""
    l10n = MagicMock(spec=Localization)
    l10n.language = "ru"

    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "start_message": (
                "Привет! Я бот для AI-генерации.\n\n"
                "Доступные команды:\n"
                "/start — начать работу\n"
                "/language — выбрать язык интерфейса"
            ),
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
            "start_message": (
                "Hello! I'm an AI generation bot.\n\n"
                "Available commands:\n"
                "/start — start using the bot\n"
                "/language — choose interface language"
            ),
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    l10n.get.side_effect = get_translation
    return l10n


# ==============================================================================
# ТЕСТЫ _extract_start_param
# ==============================================================================


def test_extract_start_param_returns_none_for_simple_start() -> None:
    """Тест: _extract_start_param возвращает None для простого /start."""
    message = MagicMock(spec=Message)
    message.text = "/start"

    result = _extract_start_param(message)

    assert result is None


def test_extract_start_param_extracts_parameter() -> None:
    """Тест: _extract_start_param извлекает параметр из deep link."""
    message = MagicMock(spec=Message)
    message.text = "/start ref_123"

    result = _extract_start_param(message)

    assert result == "ref_123"


def test_extract_start_param_extracts_parameter_with_spaces() -> None:
    """Тест: _extract_start_param обрабатывает параметры с пробелами."""
    message = MagicMock(spec=Message)
    message.text = "/start promo winter"

    result = _extract_start_param(message)

    # Берётся всё после первого пробела
    assert result == "promo winter"


def test_extract_start_param_returns_none_if_text_is_none() -> None:
    """Тест: _extract_start_param возвращает None если text отсутствует."""
    message = MagicMock(spec=Message)
    message.text = None

    result = _extract_start_param(message)

    assert result is None


# ==============================================================================
# ТЕСТЫ _detect_user_language
# ==============================================================================


def test_detect_user_language_returns_language_if_available() -> None:
    """Тест: _detect_user_language возвращает язык если он доступен."""
    with (
        patch(
            "src.bot.handlers.start.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch(
            "src.bot.handlers.start.Localization.get_default_language",
            return_value="ru",
        ),
    ):
        result = _detect_user_language("en")

        assert result == "en"


def test_detect_user_language_returns_default_if_unavailable() -> None:
    """Тест: _detect_user_language возвращает default если язык недоступен."""
    with (
        patch(
            "src.bot.handlers.start.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch(
            "src.bot.handlers.start.Localization.get_default_language",
            return_value="ru",
        ),
    ):
        result = _detect_user_language("fr")

        assert result == "ru"


def test_detect_user_language_returns_default_if_none() -> None:
    """Тест: _detect_user_language возвращает default если language_code=None."""
    with patch(
        "src.bot.handlers.start.Localization.get_default_language",
        return_value="en",
    ):
        result = _detect_user_language(None)

        assert result == "en"


def test_detect_user_language_handles_locale_format() -> None:
    """Тест: _detect_user_language обрабатывает формат ru-RU."""
    with (
        patch(
            "src.bot.handlers.start.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch(
            "src.bot.handlers.start.Localization.get_default_language",
            return_value="ru",
        ),
    ):
        # Telegram может вернуть "ru-RU" вместо "ru"
        result = _detect_user_language("en-US")

        assert result == "en"


def test_detect_user_language_is_case_insensitive() -> None:
    """Тест: _detect_user_language не чувствителен к регистру."""
    with (
        patch(
            "src.bot.handlers.start.Localization.get_available_languages",
            return_value=["ru", "en"],
        ),
        patch(
            "src.bot.handlers.start.Localization.get_default_language",
            return_value="ru",
        ),
    ):
        # Telegram может вернуть "EN" или "En"
        result = _detect_user_language("EN")

        assert result == "en"


# ==============================================================================
# ТЕСТЫ cmd_start
# ==============================================================================


@pytest.mark.asyncio
async def test_cmd_start_creates_new_user(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: /start создаёт нового пользователя при первом запуске."""
    import logging

    caplog.set_level(logging.INFO)

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
    ):
        # Настраиваем DatabaseSession
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        # Настраиваем UserRepository
        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(
            return_value=(MagicMock(spec=DbUser), True)  # created=True
        )
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что get_or_create был вызван
    assert mock_message.from_user is not None
    mock_repo.get_or_create.assert_called_once_with(
        telegram_id=mock_message.from_user.id,
        username=mock_message.from_user.username,
        first_name=mock_message.from_user.first_name,
        last_name=mock_message.from_user.last_name,
        language="ru",
        source=None,
    )

    # Проверяем логирование создания пользователя
    assert any("Новый пользователь" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_cmd_start_updates_existing_user(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: /start обновляет данные существующего пользователя."""
    import logging

    caplog.set_level(logging.INFO)

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        # Пользователь уже существует (created=False)
        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(
            return_value=(MagicMock(spec=DbUser), False)  # created=False
        )
        # update_profile должен быть AsyncMock
        mock_repo.update_profile = AsyncMock()
        mock_repo_cls.return_value = mock_repo

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что update_profile был вызван для существующего пользователя
    mock_repo.update_profile.assert_called_once()

    # Проверяем логирование
    assert any("вернулся" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_cmd_start_sends_localized_message(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start отправляет приветственное сообщение на языке пользователя."""
    # Мокируем legal config — отключаем проверку согласия
    mock_legal_config = MagicMock()
    mock_legal_config.enabled = False

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.WELCOME_IMAGE") as mock_welcome_image,
        patch("src.bot.handlers.start.yaml_config") as mock_yaml_config,
    ):
        mock_yaml_config.legal = mock_legal_config

        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(return_value=(MagicMock(spec=DbUser), True))
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0 (не показываем дополнительное сообщение)
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        # Мокируем welcome image — картинка существует
        mock_welcome_image.exists.return_value = True

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что ответ с картинкой отправлен
    mock_message.answer_photo.assert_called_once()

    # Проверяем что использовался правильный ключ локализации
    mock_l10n_ru.get.assert_called_with("start_message")


@pytest.mark.asyncio
async def test_cmd_start_extracts_and_saves_start_param(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start извлекает и сохраняет start-параметр."""
    from src.services.referral_service import ReferralResult

    mock_message.text = "/start ref_123"

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.create_referral_service") as mock_referral_cls,
        patch("src.bot.handlers.start.WELCOME_IMAGE") as mock_welcome_image,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(return_value=(MagicMock(spec=DbUser), True))
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        # Мокируем реферальный сервис
        mock_referral = MagicMock()
        mock_referral.process_referral = AsyncMock(
            return_value=ReferralResult(success=False, error="invalid_param")
        )
        mock_referral_cls.return_value = mock_referral

        # Мокируем welcome image — картинка существует
        mock_welcome_image.exists.return_value = True

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что source был передан в get_or_create
    call_kwargs = mock_repo.get_or_create.call_args[1]
    assert call_kwargs["source"] == "ref_123"


@pytest.mark.asyncio
async def test_cmd_start_detects_language_from_telegram(
    mock_l10n_en: MagicMock,
) -> None:
    """Тест: /start определяет язык из Telegram language_code."""
    # Создаём Message с пользователем, у которого language_code="en"
    message = MagicMock(spec=Message)
    message.from_user = User(
        id=123456789,
        is_bot=False,
        first_name="Test User",
        language_code="en",  # Устанавливаем при создании
    )
    message.text = "/start"
    message.answer = AsyncMock()
    message.answer_photo = AsyncMock()

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.handlers.start._detect_user_language", return_value="en"
        ) as mock_detect,
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.WELCOME_IMAGE") as mock_welcome_image,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(return_value=(MagicMock(spec=DbUser), True))
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        # Мокируем welcome image — картинка существует
        mock_welcome_image.exists.return_value = True

        await cmd_start(message, mock_l10n_en)

    # Проверяем что _detect_user_language был вызван с правильным language_code
    mock_detect.assert_called_once_with("en")

    # Проверяем что язык был передан в get_or_create
    call_kwargs = mock_repo.get_or_create.call_args[1]
    assert call_kwargs["language"] == "en"


@pytest.mark.asyncio
async def test_cmd_start_handles_message_without_user(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start обрабатывает сообщение без from_user."""
    # Message без пользователя
    message = MagicMock(spec=Message)
    message.from_user = None
    message.answer = AsyncMock()

    await cmd_start(message, mock_l10n_ru)

    # Должен отправить сообщение без создания пользователя
    message.answer.assert_called_once()
    mock_l10n_ru.get.assert_called_with("start_message")


@pytest.mark.asyncio
async def test_cmd_start_logs_language_detection(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: /start логирует определённый язык для нового пользователя."""
    import logging

    caplog.set_level(logging.INFO)

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(
            return_value=(MagicMock(spec=DbUser), True)  # created=True
        )
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что язык был залогирован
    assert any("language=ru" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_cmd_start_sends_bonus_notification_if_granted(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start отправляет уведомление о бонусе если он был начислен."""

    # Добавляем перевод для billing_registration_bonus
    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "start_message": (
                "Привет! Я бот для AI-генерации.\n\n"
                "Доступные команды:\n"
                "/start — начать работу\n"
                "/language — выбрать язык интерфейса"
            ),
            "billing_registration_bonus": "Вам начислено {amount} токенов!",
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    mock_l10n_ru.get.side_effect = get_translation

    # Мокируем legal config — отключаем проверку согласия
    mock_legal_config = MagicMock()
    mock_legal_config.enabled = False

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.WELCOME_IMAGE") as mock_welcome_image,
        patch("src.bot.handlers.start.yaml_config") as mock_yaml_config,
    ):
        mock_yaml_config.legal = mock_legal_config

        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(
            return_value=(MagicMock(spec=DbUser), True)  # created=True
        )
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — начисляем бонус 100
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=100)
        mock_billing_cls.return_value = mock_billing

        # Мокируем welcome image — картинка существует
        mock_welcome_image.exists.return_value = True

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что answer_photo вызван 1 раз (приветствие)
    mock_message.answer_photo.assert_called_once()

    # Проверяем что answer вызван 1 раз (уведомление о бонусе)
    mock_message.answer.assert_called_once()

    # Проверяем уведомление о бонусе
    bonus_call = mock_message.answer.call_args[0]
    assert "100 токенов" in bonus_call[0]


@pytest.mark.asyncio
async def test_cmd_start_no_bonus_notification_if_zero(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start не отправляет уведомление о бонусе если он 0."""
    # Мокируем legal config — отключаем проверку согласия
    mock_legal_config = MagicMock()
    mock_legal_config.enabled = False

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.WELCOME_IMAGE") as mock_welcome_image,
        patch("src.bot.handlers.start.yaml_config") as mock_yaml_config,
    ):
        mock_yaml_config.legal = mock_legal_config

        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(
            return_value=(MagicMock(spec=DbUser), True)  # created=True
        )
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        # Мокируем welcome image — картинка существует
        mock_welcome_image.exists.return_value = True

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что был 1 вызов answer_photo (приветствие с картинкой)
    mock_message.answer_photo.assert_called_once()

    # Проверяем что answer не вызывался (бонус не начислен)
    mock_message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_start_sends_reply_keyboard_remove(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start отправляет ReplyKeyboardRemove для очистки старой клавиатуры.

    ВАЖНО: ReplyKeyboardRemove гарантирует, что при перезапуске бота не будет
    висеть устаревшая reply keyboard от предыдущей версии бота.
    """
    from aiogram.types import ReplyKeyboardRemove

    # Мокируем legal config — отключаем проверку согласия
    mock_legal_config = MagicMock()
    mock_legal_config.enabled = False

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.WELCOME_IMAGE") as mock_welcome_image,
        patch("src.bot.handlers.start.yaml_config") as mock_yaml_config,
    ):
        mock_yaml_config.legal = mock_legal_config

        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(
            return_value=(MagicMock(spec=DbUser), True)  # created=True
        )
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        # Мокируем welcome image — картинка существует
        mock_welcome_image.exists.return_value = True

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что был вызов answer_photo с ReplyKeyboardRemove
    mock_message.answer_photo.assert_called_once()
    call_kwargs = mock_message.answer_photo.call_args[1]
    assert "reply_markup" in call_kwargs
    assert isinstance(call_kwargs["reply_markup"], ReplyKeyboardRemove)


@pytest.mark.asyncio
async def test_cmd_start_sends_reply_keyboard_remove_when_no_image(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start отправляет ReplyKeyboardRemove даже когда нет welcome image."""
    from aiogram.types import ReplyKeyboardRemove

    # Мокируем legal config — отключаем проверку согласия
    mock_legal_config = MagicMock()
    mock_legal_config.enabled = False

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.WELCOME_IMAGE") as mock_welcome_image,
        patch("src.bot.handlers.start.yaml_config") as mock_yaml_config,
    ):
        mock_yaml_config.legal = mock_legal_config

        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(
            return_value=(MagicMock(spec=DbUser), True)  # created=True
        )
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        # Мокируем welcome image — картинка НЕ существует
        mock_welcome_image.exists.return_value = False

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что был вызов answer с ReplyKeyboardRemove
    mock_message.answer.assert_called_once()
    call_kwargs = mock_message.answer.call_args[1]
    assert "reply_markup" in call_kwargs
    assert isinstance(call_kwargs["reply_markup"], ReplyKeyboardRemove)


# ==============================================================================
# ТЕСТЫ РЕФЕРАЛЬНОЙ ПРОГРАММЫ В /start
# ==============================================================================


@pytest.mark.asyncio
async def test_cmd_start_processes_referral_link(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: /start обрабатывает реферальную ссылку и вызывает process_referral."""
    import logging

    from src.services.referral_service import ReferralResult

    caplog.set_level(logging.INFO)

    # Устанавливаем реферальный параметр
    mock_message.text = "/start ref_111111111"

    # Создаём mock для результата реферала
    mock_referral_result = ReferralResult(
        success=True,
        invitee_bonus=25,
        inviter_bonus=50,
        bonus_pending=False,
    )

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.create_referral_service") as mock_referral_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        # Мок пользователя
        mock_user = MagicMock(spec=DbUser)
        mock_user.id = 1

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(
            return_value=(mock_user, True)  # created=True
        )
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        # Мокируем реферальный сервис
        mock_referral = MagicMock()
        mock_referral.process_referral = AsyncMock(return_value=mock_referral_result)
        mock_referral_cls.return_value = mock_referral

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что process_referral был вызван с правильными параметрами
    mock_referral.process_referral.assert_called_once_with(
        invitee=mock_user,
        start_param="ref_111111111",
    )

    # Проверяем логирование
    assert any("Реферал обработан" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_cmd_start_shows_referral_bonus_notification(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start показывает уведомление о реферальном бонусе."""
    from src.services.referral_service import ReferralResult

    # Устанавливаем реферальный параметр
    mock_message.text = "/start ref_111111111"

    # Добавляем перевод для referral_invitee_bonus
    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "start_message": "Привет!",
            "referral_invitee_bonus": "Бонус за приглашение: {amount} токенов!",
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    mock_l10n_ru.get.side_effect = get_translation

    # Создаём mock для результата реферала с бонусом
    mock_referral_result = ReferralResult(
        success=True,
        invitee_bonus=25,
        inviter_bonus=50,
        bonus_pending=False,
    )

    # Мокируем legal config — отключаем проверку согласия
    mock_legal_config = MagicMock()
    mock_legal_config.enabled = False

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.create_referral_service") as mock_referral_cls,
        patch("src.bot.handlers.start.WELCOME_IMAGE") as mock_welcome_image,
        patch("src.bot.handlers.start.yaml_config") as mock_yaml_config,
    ):
        mock_yaml_config.legal = mock_legal_config

        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_user = MagicMock(spec=DbUser)
        mock_user.id = 1

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(return_value=(mock_user, True))
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        # Мокируем реферальный сервис
        mock_referral = MagicMock()
        mock_referral.process_referral = AsyncMock(return_value=mock_referral_result)
        mock_referral_cls.return_value = mock_referral

        # Мокируем welcome image — картинка существует
        mock_welcome_image.exists.return_value = True

        await cmd_start(mock_message, mock_l10n_ru)

    # Проверяем что answer вызван с уведомлением о бонусе
    # answer должен быть вызван 1 раз (реферальный бонус)
    mock_message.answer.assert_called_once()
    bonus_call = mock_message.answer.call_args[0][0]
    assert "25 токенов" in bonus_call


@pytest.mark.asyncio
async def test_cmd_start_no_referral_notification_on_failure(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start не показывает уведомление если реферал не обработан."""
    from src.services.referral_service import ReferralResult

    # Устанавливаем неверный реферальный параметр
    mock_message.text = "/start ref_999999999"

    # Создаём mock для неуспешного результата
    mock_referral_result = ReferralResult(
        success=False,
        invitee_bonus=0,
        error="inviter_not_found",
    )

    # Мокируем legal config — отключаем проверку согласия
    mock_legal_config = MagicMock()
    mock_legal_config.enabled = False

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_billing_service") as mock_billing_cls,
        patch("src.bot.handlers.start.create_referral_service") as mock_referral_cls,
        patch("src.bot.handlers.start.WELCOME_IMAGE") as mock_welcome_image,
        patch("src.bot.handlers.start.yaml_config") as mock_yaml_config,
    ):
        mock_yaml_config.legal = mock_legal_config

        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_user = MagicMock(spec=DbUser)
        mock_user.id = 1

        mock_repo = MagicMock()
        mock_repo.get_or_create = AsyncMock(return_value=(mock_user, True))
        mock_repo_cls.return_value = mock_repo

        # Мокируем биллинг — бонус 0
        mock_billing = MagicMock()
        mock_billing.grant_registration_bonus = AsyncMock(return_value=0)
        mock_billing_cls.return_value = mock_billing

        # Мокируем реферальный сервис — возвращает failure
        mock_referral = MagicMock()
        mock_referral.process_referral = AsyncMock(return_value=mock_referral_result)
        mock_referral_cls.return_value = mock_referral

        # Мокируем welcome image — картинка существует
        mock_welcome_image.exists.return_value = True

        await cmd_start(mock_message, mock_l10n_ru)

    # answer не должен быть вызван (бонус не начислен, реферал не удался)
    mock_message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_start_does_not_process_referral_for_existing_user(
    mock_message: MagicMock,
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: /start не обрабатывает реферал для существующего пользователя."""
    from src.services.referral_service import ReferralResult

    # Устанавливаем реферальный параметр
    mock_message.text = "/start ref_111111111"

    with (
        patch("src.bot.handlers.start.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.start.UserRepository") as mock_repo_cls,
        patch("src.bot.handlers.start._detect_user_language", return_value="ru"),
        patch("src.bot.handlers.start.create_referral_service") as mock_referral_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_user = MagicMock(spec=DbUser)
        mock_user.id = 1

        mock_repo = MagicMock()
        # Пользователь существует (created=False)
        mock_repo.get_or_create = AsyncMock(return_value=(mock_user, False))
        mock_repo.update_profile = AsyncMock()
        mock_repo_cls.return_value = mock_repo

        # Мокируем реферальный сервис
        mock_referral = MagicMock()
        mock_referral.process_referral = AsyncMock(
            return_value=ReferralResult(success=True, invitee_bonus=25)
        )
        mock_referral_cls.return_value = mock_referral

        await cmd_start(mock_message, mock_l10n_ru)

    # process_referral НЕ должен быть вызван для существующего пользователя
    mock_referral.process_referral.assert_not_called()
