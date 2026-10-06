"""Тесты для обработчика команды /error.

Проверяют корректность работы тестовой команды для отслеживания ошибок:
- Ошибка вызывается и логируется
- Пользователю отправляется сообщение об ошибке
- Обработка случая без from_user
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message
from aiogram.types import User as TelegramUser

from src.bot.handlers.error import DebugError, cmd_error
from src.utils.i18n import Localization


@pytest.fixture
def mock_message() -> Message:
    """Создать мок-объект сообщения от Telegram."""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(spec=TelegramUser)
    message.from_user.id = 123456789
    message.from_user.username = "test_user"
    message.answer = AsyncMock()
    return message


@pytest.fixture
def mock_l10n() -> Localization:
    """Создать мок-объект локализации."""
    l10n = MagicMock(spec=Localization)

    # Словарь переводов для тестов
    translations = {
        "error_test_triggered": (
            "🔴 Тестовая ошибка успешно вызвана!\n\n"
            "Это сообщение подтверждает, что:\n"
            "• Команда /error работает\n"
            "• Ошибка залогирована в терминале\n"
            "• Система мониторинга уведомлена"
        ),
    }

    def get_translation(key: str, **kwargs: str) -> str:
        """Вернуть перевод с подставленными параметрами."""
        text = translations.get(key, key)
        if kwargs:
            text = text.format(**kwargs)
        return text

    l10n.get = MagicMock(side_effect=get_translation)
    return l10n


class TestCmdError:
    """Тесты для команды /error."""

    @pytest.mark.asyncio
    async def test_cmd_error_triggers_exception_and_logs_it(
        self,
        mock_message: Message,
        mock_l10n: Localization,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Проверить, что /error вызывает исключение и логирует его."""
        # Arrange
        caplog.set_level(logging.INFO)

        # Act
        await cmd_error(mock_message, mock_l10n)

        # Assert
        # Проверяем, что ошибка была залогирована
        assert any("Тестовая ошибка" in record.message for record in caplog.records)
        assert any("user_id=123456789" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_cmd_error_sends_confirmation_message(
        self,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что /error отправляет сообщение пользователю."""
        # Act
        await cmd_error(mock_message, mock_l10n)

        # Assert
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        assert "Тестовая ошибка успешно вызвана" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_error_calls_l10n_get_with_correct_key(
        self,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что используется правильный ключ локализации."""
        # Act
        await cmd_error(mock_message, mock_l10n)

        # Assert
        mock_l10n.get.assert_called_once_with("error_test_triggered")

    @pytest.mark.asyncio
    async def test_cmd_error_logs_info_before_exception(
        self,
        mock_message: Message,
        mock_l10n: Localization,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Проверить, что INFO лог записывается перед вызовом ошибки."""
        # Arrange
        caplog.set_level(logging.INFO)

        # Act
        await cmd_error(mock_message, mock_l10n)

        # Assert
        # Ищем INFO лог о вызове тестовой ошибки
        info_logs = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any(
            "вызвал тестовую ошибку" in record.message for record in info_logs
        ), "INFO лог о вызове тестовой ошибки не найден"

    @pytest.mark.asyncio
    async def test_cmd_error_logs_exception_with_traceback(
        self,
        mock_message: Message,
        mock_l10n: Localization,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Проверить, что ошибка логируется с traceback."""
        # Arrange
        caplog.set_level(logging.ERROR)

        # Act
        await cmd_error(mock_message, mock_l10n)

        # Assert
        # Ищем ERROR лог с traceback
        error_logs = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_logs) >= 1, "ERROR лог не найден"

        # Проверяем, что есть exc_info (traceback)
        assert any(r.exc_info is not None for r in error_logs), (
            "ERROR лог без traceback"
        )

    @pytest.mark.asyncio
    async def test_cmd_error_includes_username_in_logs(
        self,
        mock_message: Message,
        mock_l10n: Localization,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Проверить, что username включён в логи."""
        # Arrange
        caplog.set_level(logging.INFO)

        # Act
        await cmd_error(mock_message, mock_l10n)

        # Assert
        assert any("test_user" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_cmd_error_handles_no_from_user(
        self,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить обработку случая без from_user."""
        # Arrange
        mock_message.from_user = None

        # Act
        await cmd_error(mock_message, mock_l10n)

        # Assert
        # Команда всё равно должна отработать
        mock_message.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_cmd_error_handles_no_username(
        self,
        mock_message: Message,
        mock_l10n: Localization,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Проверить обработку пользователя без username."""
        # Arrange
        mock_message.from_user.username = None
        caplog.set_level(logging.INFO)

        # Act
        await cmd_error(mock_message, mock_l10n)

        # Assert
        mock_message.answer.assert_called_once()
        # Лог должен содержать None или какое-то значение по умолчанию
        assert any("user_id=123456789" in record.message for record in caplog.records)


class TestDebugError:
    """Тесты для класса DebugError."""

    def test_debug_error_is_exception(self) -> None:
        """Проверить, что DebugError является исключением."""
        assert issubclass(DebugError, Exception)

    def test_debug_error_can_be_raised(self) -> None:
        """Проверить, что DebugError можно выбросить."""
        with pytest.raises(DebugError) as exc_info:
            raise DebugError("Test message")

        assert "Test message" in str(exc_info.value)

    def test_debug_error_can_be_caught(self) -> None:
        """Проверить, что DebugError можно поймать."""
        caught = False
        try:
            raise DebugError("Test message")
        except DebugError:
            caught = True

        assert caught
