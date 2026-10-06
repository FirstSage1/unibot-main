"""Тесты для fallback-обработчиков.

Проверяют корректность работы обработчиков для необработанных сообщений:
- unknown_command обрабатывает неизвестные команды
- Отправляет понятное сообщение пользователю
- Логирует предупреждение о неизвестной команде
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message
from aiogram.types import User as TelegramUser

from src.bot.handlers.fallback import unknown_command
from src.utils.i18n import Localization


@pytest.fixture
def mock_message() -> Message:
    """Создать мок-объект сообщения от Telegram."""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(spec=TelegramUser)
    message.from_user.id = 123456789
    message.from_user.username = "test_user"
    message.text = "/unknown_command"
    message.answer = AsyncMock()
    return message


@pytest.fixture
def mock_l10n() -> Localization:
    """Создать мок-объект локализации."""
    l10n = MagicMock(spec=Localization)

    # Словарь переводов для тестов
    translations = {
        "command_not_found": "❌ Команда /{command} не найдена или отключена.",
    }

    def get_translation(key: str, **kwargs: str) -> str:
        """Вернуть перевод с подставленными параметрами."""
        text = translations.get(key, key)
        if kwargs:
            text = text.format(**kwargs)
        return text

    l10n.get = MagicMock(side_effect=get_translation)
    return l10n


class TestUnknownCommand:
    """Тесты для обработчика неизвестных команд."""

    @pytest.mark.asyncio
    async def test_unknown_command_sends_error_message(
        self,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что при неизвестной команде отправляется сообщение об ошибке."""
        # Act
        await unknown_command(mock_message, mock_l10n)

        # Assert
        mock_message.answer.assert_called_once()
        mock_l10n.get.assert_called_with("command_not_found", command="unknown_command")

    @pytest.mark.asyncio
    async def test_unknown_command_extracts_command_name(
        self,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что имя команды правильно извлекается из текста."""
        # Arrange
        mock_message.text = "/test_cmd with args"

        # Act
        await unknown_command(mock_message, mock_l10n)

        # Assert
        mock_l10n.get.assert_called_with("command_not_found", command="test_cmd")

    @pytest.mark.asyncio
    async def test_unknown_command_handles_command_with_bot_username(
        self,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить обработку команды с @bot_username."""
        # Arrange
        mock_message.text = "/start@mybot"

        # Act
        await unknown_command(mock_message, mock_l10n)

        # Assert
        # Должна извлечься команда с @bot_username
        call_kwargs = mock_l10n.get.call_args[1]
        assert call_kwargs["command"] == "start@mybot"

    @pytest.mark.asyncio
    async def test_unknown_command_without_from_user(
        self,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что обработчик корректно обрабатывает отсутствие from_user."""
        # Arrange
        mock_message.from_user = None

        # Act
        await unknown_command(mock_message, mock_l10n)

        # Assert
        # Не должно быть вызовов
        mock_message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_command_without_text(
        self,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что обработчик корректно обрабатывает отсутствие text."""
        # Arrange
        mock_message.text = None

        # Act
        await unknown_command(mock_message, mock_l10n)

        # Assert
        # Не должно быть вызовов
        mock_message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_command_logs_warning(
        self,
        mock_message: Message,
        mock_l10n: Localization,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Проверить, что обработчик логирует предупреждение о неизвестной команде."""
        import logging

        caplog.set_level(logging.WARNING)

        # Act
        await unknown_command(mock_message, mock_l10n)

        # Assert
        # Проверяем что было залогировано предупреждение
        assert any("Неизвестная команда" in record.message for record in caplog.records)
        assert any("unknown_command" in record.message for record in caplog.records), (
            "Имя команды должно быть в логе"
        )
