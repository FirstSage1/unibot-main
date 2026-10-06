"""Тесты для утилит работы с Telegram webhook.

Проверяет:
- Нормализацию домена (normalize_domain)
- Построение webhook URL (build_webhook_url)
- Установку webhook с retry-логикой (setup_webhook)
- Удаление webhook (remove_webhook)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.bot.webhook import (
    WEBHOOK_PATH,
    build_webhook_url,
    normalize_domain,
    remove_webhook,
    setup_webhook,
)


class TestNormalizeDomain:
    """Тесты для функции normalize_domain."""

    def test_normalize_domain_without_protocol_adds_https(self) -> None:
        """Проверить добавление https:// при отсутствии протокола."""
        # Arrange
        domain = "example.com"

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "https://example.com"

    def test_normalize_domain_with_https_preserves_protocol(self) -> None:
        """Проверить, что normalize_domain сохраняет https:// протокол."""
        # Arrange
        domain = "https://example.com"

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "https://example.com"

    def test_normalize_domain_with_http_preserves_protocol(self) -> None:
        """Проверить, что normalize_domain сохраняет http:// протокол."""
        # Arrange
        domain = "http://example.com"

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "http://example.com"

    def test_normalize_domain_removes_trailing_slash(self) -> None:
        """Проверить, что normalize_domain удаляет слеш в конце."""
        # Arrange
        domain = "https://example.com/"

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "https://example.com"

    def test_normalize_domain_removes_path(self) -> None:
        """Проверить, что normalize_domain удаляет путь из URL."""
        # Arrange
        domain = "https://example.com/some/path"

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "https://example.com"

    def test_normalize_domain_removes_query_params(self) -> None:
        """Проверить, что normalize_domain удаляет query параметры."""
        # Arrange
        domain = "https://example.com?key=value"

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "https://example.com"

    def test_normalize_domain_removes_fragment(self) -> None:
        """Проверить, что normalize_domain удаляет fragment (#)."""
        # Arrange
        domain = "https://example.com#section"

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "https://example.com"

    def test_normalize_domain_with_subdomain(self) -> None:
        """Проверить normalize_domain с поддоменом."""
        # Arrange
        domain = "api.example.com"

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "https://api.example.com"

    def test_normalize_domain_with_port(self) -> None:
        """Проверить normalize_domain с портом."""
        # Arrange
        domain = "example.com:8080"

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "https://example.com:8080"

    def test_normalize_domain_strips_whitespace(self) -> None:
        """Проверить, что normalize_domain убирает пробелы."""
        # Arrange
        domain = "  example.com  "

        # Act
        result = normalize_domain(domain)

        # Assert
        assert result == "https://example.com"

    @pytest.mark.parametrize(
        ("input_domain", "expected_output"),
        [
            ("example.com", "https://example.com"),
            ("https://example.com", "https://example.com"),
            ("http://example.com", "http://example.com"),
            ("https://example.com/", "https://example.com"),
            ("api.example.com", "https://api.example.com"),
            ("example.com:8080", "https://example.com:8080"),
            ("https://example.com/path", "https://example.com"),
            ("https://example.com?q=1", "https://example.com"),
        ],
    )
    def test_normalize_domain_various_inputs(
        self, input_domain: str, expected_output: str
    ) -> None:
        """Проверить normalize_domain с различными входными данными.

        Args:
            input_domain: Входной домен для нормализации.
            expected_output: Ожидаемый результат нормализации.
        """
        # Act
        result = normalize_domain(input_domain)

        # Assert
        assert result == expected_output


class TestBuildWebhookUrl:
    """Тесты для функции build_webhook_url."""

    def test_build_webhook_url_returns_correct_url(self) -> None:
        """Проверить, что build_webhook_url формирует корректный URL."""
        # Arrange
        domain = "https://example.com"
        expected_url = f"https://example.com{WEBHOOK_PATH}"

        # Act
        result = build_webhook_url(domain)

        # Assert
        assert result == expected_url

    def test_build_webhook_url_with_trailing_slash(self) -> None:
        """Проверить build_webhook_url с доменом со слешем в конце."""
        # Arrange
        domain = "https://example.com/"
        expected_url = f"https://example.com{WEBHOOK_PATH}"

        # Act
        result = build_webhook_url(domain)

        # Assert
        assert result == expected_url

    def test_build_webhook_url_with_http(self) -> None:
        """Проверить build_webhook_url с http протоколом."""
        # Arrange
        domain = "http://example.com"
        expected_url = f"http://example.com{WEBHOOK_PATH}"

        # Act
        result = build_webhook_url(domain)

        # Assert
        assert result == expected_url

    def test_build_webhook_url_with_subdomain(self) -> None:
        """Проверить build_webhook_url с поддоменом."""
        # Arrange
        domain = "https://api.example.com"
        expected_url = f"https://api.example.com{WEBHOOK_PATH}"

        # Act
        result = build_webhook_url(domain)

        # Assert
        assert result == expected_url

    def test_build_webhook_url_uses_webhook_path_constant(self) -> None:
        """Проверить, что build_webhook_url использует константу WEBHOOK_PATH."""
        # Arrange
        domain = "https://example.com"

        # Act
        result = build_webhook_url(domain)

        # Assert
        assert WEBHOOK_PATH in result
        assert result.endswith(WEBHOOK_PATH)


class TestSetupWebhook:
    """Тесты для функции setup_webhook."""

    @pytest.mark.asyncio
    async def test_setup_webhook_success_on_first_attempt(self) -> None:
        """Проверить успешную установку webhook с первой попытки."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock(return_value=True)
        domain = "https://example.com"

        # Act
        result = await setup_webhook(mock_bot, domain)

        # Assert
        assert result is True
        mock_bot.set_webhook.assert_called_once()
        call_args = mock_bot.set_webhook.call_args
        assert call_args.kwargs["url"] == f"https://example.com{WEBHOOK_PATH}"
        assert call_args.kwargs["drop_pending_updates"] is False

    @pytest.mark.asyncio
    async def test_setup_webhook_bot_returns_false_retries_and_fails(self) -> None:
        """Проверить retry при возврате False от Telegram API."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock(return_value=False)
        domain = "https://example.com"

        # Act
        with patch("src.bot.webhook.asyncio.sleep", new_callable=AsyncMock):
            result = await setup_webhook(mock_bot, domain)

        # Assert
        assert result is False
        assert mock_bot.set_webhook.call_count == 3  # MAX_RETRIES

    @pytest.mark.asyncio
    async def test_setup_webhook_success_on_second_attempt(self) -> None:
        """Проверить успешную установку webhook со второй попытки."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock(side_effect=[False, True])
        domain = "https://example.com"

        # Act
        with patch("src.bot.webhook.asyncio.sleep", new_callable=AsyncMock):
            result = await setup_webhook(mock_bot, domain)

        # Assert
        assert result is True
        assert mock_bot.set_webhook.call_count == 2

    @pytest.mark.asyncio
    async def test_setup_webhook_client_error_raises_runtime_error(self) -> None:
        """Проверить RuntimeError при 4xx ошибке от Telegram API."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock(
            side_effect=Exception("400 Bad Request: invalid url")
        )
        domain = "https://example.com"

        # Act & Assert
        with pytest.raises(RuntimeError, match="Некорректный webhook URL"):
            await setup_webhook(mock_bot, domain)

        # Проверяем, что не было retry для 4xx ошибки
        assert mock_bot.set_webhook.call_count == 1

    @pytest.mark.asyncio
    async def test_setup_webhook_server_error_retries_and_fails(self) -> None:
        """Проверить retry при 5xx ошибке от Telegram API."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock(
            side_effect=Exception("500 Internal Server Error")
        )
        domain = "https://example.com"

        # Act
        with patch("src.bot.webhook.asyncio.sleep", new_callable=AsyncMock):
            result = await setup_webhook(mock_bot, domain)

        # Assert
        assert result is False
        assert mock_bot.set_webhook.call_count == 3  # MAX_RETRIES

    @pytest.mark.asyncio
    async def test_setup_webhook_network_error_retries_and_fails(self) -> None:
        """Проверить retry при сетевой ошибке."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock(side_effect=Exception("Connection timeout"))
        domain = "https://example.com"

        # Act
        with patch("src.bot.webhook.asyncio.sleep", new_callable=AsyncMock):
            result = await setup_webhook(mock_bot, domain)

        # Assert
        assert result is False
        assert mock_bot.set_webhook.call_count == 3  # MAX_RETRIES

    @pytest.mark.asyncio
    async def test_setup_webhook_retries_with_exponential_backoff(self) -> None:
        """Проверить exponential backoff между попытками."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock(side_effect=Exception("Temporary error"))
        domain = "https://example.com"
        mock_sleep = AsyncMock()

        # Act
        with patch("src.bot.webhook.asyncio.sleep", mock_sleep):
            await setup_webhook(mock_bot, domain)

        # Assert
        # Проверяем, что sleep вызывался с правильными задержками: 1s, 2s
        assert mock_sleep.call_count == 2  # Между тремя попытками — две паузы
        sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_calls == [1, 2]  # Exponential backoff из RETRY_DELAYS

    @pytest.mark.asyncio
    async def test_setup_webhook_success_after_temporary_errors(self) -> None:
        """Проверить успех после нескольких временных ошибок."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock(
            side_effect=[
                Exception("Timeout"),
                Exception("Connection error"),
                True,
            ]
        )
        domain = "https://example.com"

        # Act
        with patch("src.bot.webhook.asyncio.sleep", new_callable=AsyncMock):
            result = await setup_webhook(mock_bot, domain)

        # Assert
        assert result is True
        assert mock_bot.set_webhook.call_count == 3

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error_message",
        [
            "400 Bad Request",
            "401 Unauthorized",
            "403 Forbidden",
            "404 Not Found",
            "invalid url",
            "Bad Request: webhook url is invalid",
        ],
    )
    async def test_setup_webhook_client_errors_raise_immediately(
        self, error_message: str
    ) -> None:
        """Проверить немедленное прерывание при различных 4xx ошибках.

        Args:
            error_message: Сообщение об ошибке от API.
        """
        # Arrange
        mock_bot = MagicMock()
        mock_bot.set_webhook = AsyncMock(side_effect=Exception(error_message))
        domain = "https://example.com"

        # Act & Assert
        with pytest.raises(RuntimeError):
            await setup_webhook(mock_bot, domain)

        # Проверяем отсутствие retry
        assert mock_bot.set_webhook.call_count == 1


class TestRemoveWebhook:
    """Тесты для функции remove_webhook."""

    @pytest.mark.asyncio
    async def test_remove_webhook_success(self) -> None:
        """Проверить успешное удаление webhook."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.delete_webhook = AsyncMock(return_value=True)

        # Act
        await remove_webhook(mock_bot)

        # Assert
        mock_bot.delete_webhook.assert_called_once_with(drop_pending_updates=False)

    @pytest.mark.asyncio
    async def test_remove_webhook_returns_false_no_exception(self) -> None:
        """Проверить, что функция не бросает исключение при возврате False."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.delete_webhook = AsyncMock(return_value=False)

        # Act & Assert (не должно быть исключения)
        await remove_webhook(mock_bot)

        mock_bot.delete_webhook.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_webhook_handles_exception_gracefully(self) -> None:
        """Проверить, что функция обрабатывает исключения без прерывания."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.delete_webhook = AsyncMock(side_effect=Exception("API Error"))

        # Act & Assert (не должно быть исключения)
        await remove_webhook(mock_bot)

        mock_bot.delete_webhook.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_webhook_preserves_pending_updates(self) -> None:
        """Проверить, что remove_webhook НЕ удаляет непрочитанные update."""
        # Arrange
        mock_bot = MagicMock()
        mock_bot.delete_webhook = AsyncMock(return_value=True)

        # Act
        await remove_webhook(mock_bot)

        # Assert
        call_args = mock_bot.delete_webhook.call_args
        assert call_args.kwargs["drop_pending_updates"] is False
