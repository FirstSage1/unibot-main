"""Тесты для обработчика команды /help.

Модуль тестирует:
- cmd_help (обработчик команды /help)

Тестируемая функциональность:
1. /help показывает справку с контактом поддержки (если задан)
2. /help показывает справку без контакта (если support.contact пустой)
3. Контакт берётся из config.yaml (support.contact)
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message, User

from src.bot.handlers.help import cmd_help
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
def mock_l10n() -> MagicMock:
    """Мок Localization."""
    l10n = MagicMock(spec=Localization)
    l10n.language = "ru"

    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "help_message_with_contact": ("❓ Помощь\n\nКонтакт: {contact}"),
            "help_message_no_contact": "❓ Помощь\n\nКонтакт не указан.",
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    l10n.get.side_effect = get_translation
    return l10n


# ==============================================================================
# ТЕСТЫ cmd_help
# ==============================================================================


@pytest.mark.asyncio
async def test_cmd_help_with_contact(
    mock_message: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: /help показывает справку с контактом поддержки."""
    with patch("src.bot.handlers.help.yaml_config") as mock_config:
        mock_config.support.contact = "@test_support"

        await cmd_help(mock_message, mock_l10n)

    # Проверяем что answer был вызван
    mock_message.answer.assert_called_once()

    # Проверяем что использован правильный ключ локализации
    mock_l10n.get.assert_called_with(
        "help_message_with_contact",
        contact="@test_support",
    )


@pytest.mark.asyncio
async def test_cmd_help_without_contact(
    mock_message: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: /help показывает справку без контакта (если не задан)."""
    with patch("src.bot.handlers.help.yaml_config") as mock_config:
        mock_config.support.contact = ""

        await cmd_help(mock_message, mock_l10n)

    # Проверяем что answer был вызван
    mock_message.answer.assert_called_once()

    # Проверяем что использован правильный ключ локализации
    mock_l10n.get.assert_called_with("help_message_no_contact")


@pytest.mark.asyncio
async def test_cmd_help_uses_html_parse_mode(
    mock_message: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: /help использует HTML parse_mode."""
    with patch("src.bot.handlers.help.yaml_config") as mock_config:
        mock_config.support.contact = "@test_support"

        await cmd_help(mock_message, mock_l10n)

    # Проверяем что parse_mode="HTML"
    call_kwargs = mock_message.answer.call_args[1]
    assert call_kwargs.get("parse_mode") == "HTML"


@pytest.mark.asyncio
async def test_cmd_help_with_telegram_link(
    mock_message: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: /help работает с telegram-ссылкой."""
    with patch("src.bot.handlers.help.yaml_config") as mock_config:
        mock_config.support.contact = "https://t.me/support_chat"

        await cmd_help(mock_message, mock_l10n)

    mock_l10n.get.assert_called_with(
        "help_message_with_contact",
        contact="https://t.me/support_chat",
    )


@pytest.mark.asyncio
async def test_cmd_help_with_email(
    mock_message: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: /help работает с email-адресом."""
    with patch("src.bot.handlers.help.yaml_config") as mock_config:
        mock_config.support.contact = "support@example.com"

        await cmd_help(mock_message, mock_l10n)

    mock_l10n.get.assert_called_with(
        "help_message_with_contact",
        contact="support@example.com",
    )
