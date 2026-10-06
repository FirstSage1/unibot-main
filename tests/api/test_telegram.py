"""Тесты для API endpoint Telegram webhook.

Проверяет:
- Немедленный возврат 200 OK
- Обработку update в фоновой задаче
- Корректную передачу update в диспетчер
- Обработку ошибок без влияния на ответ
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.telegram import router


@pytest.fixture
def test_app() -> FastAPI:
    """Создать тестовое FastAPI приложение.

    Returns:
        FastAPI приложение с подключенным telegram router.
    """
    app = FastAPI()
    app.include_router(router)

    # Создаём моки для bot и dp
    app.state.bot = MagicMock()
    app.state.dp = MagicMock()

    return app


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    """Создать тестовый HTTP-клиент.

    Args:
        test_app: Тестовое FastAPI приложение.

    Returns:
        TestClient для выполнения HTTP-запросов.
    """
    return TestClient(test_app)


class TestTelegramWebhook:
    """Тесты для endpoint POST /api/telegram/webhook."""

    def test_telegram_webhook_returns_200_ok(self, client: TestClient) -> None:
        """Проверить, что endpoint всегда возвращает 200 OK."""
        # Arrange
        update_data = {
            "update_id": 123456789,
            "message": {
                "message_id": 1,
                "date": 1609459200,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Test"},
                "text": "Hello",
            },
        }

        # Act
        response = client.post("/api/telegram/webhook", json=update_data)

        # Assert
        assert response.status_code == 200

    def test_telegram_webhook_returns_200_with_empty_body(
        self, client: TestClient
    ) -> None:
        """Проверить возврат 200 OK даже с пустым телом запроса."""
        # Arrange
        update_data: dict[str, Any] = {}

        # Act
        response = client.post("/api/telegram/webhook", json=update_data)

        # Assert
        assert response.status_code == 200

    def test_telegram_webhook_returns_200_with_invalid_json(
        self, client: TestClient
    ) -> None:
        """Проверить возврат 200 OK даже при невалидном update."""
        # Arrange
        # Невалидный update (отсутствует update_id)
        update_data = {
            "message": {
                "message_id": 1,
                "date": 1609459200,
                "chat": {"id": 123, "type": "private"},
            }
        }

        # Act
        response = client.post("/api/telegram/webhook", json=update_data)

        # Assert
        # ВАЖНО: endpoint должен вернуть 200 OK даже при невалидных данных
        # Ошибка парсинга обрабатывается в фоновой задаче
        assert response.status_code == 200

    @patch("src.api.telegram._process_update", new_callable=AsyncMock)
    def test_telegram_webhook_schedules_background_task(
        self, mock_process_update: AsyncMock, client: TestClient
    ) -> None:
        """Проверить, что endpoint ставит обработку в фоновую задачу.

        Args:
            mock_process_update: Мок функции _process_update.
            client: Тестовый HTTP-клиент.
        """
        # Arrange
        update_data = {
            "update_id": 123456789,
            "message": {
                "message_id": 1,
                "date": 1609459200,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Test"},
                "text": "Hello",
            },
        }

        # Act
        response = client.post("/api/telegram/webhook", json=update_data)

        # Assert
        assert response.status_code == 200
        # Background task должна быть вызвана (TestClient автоматически их выполняет)
        mock_process_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_update_parses_update_correctly(self) -> None:
        """Проверить корректный парсинг update в _process_update."""
        # Arrange
        from src.api.telegram import _process_update

        update_data = {
            "update_id": 123456789,
            "message": {
                "message_id": 1,
                "date": 1609459200,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Test"},
                "text": "Hello",
            },
        }
        mock_bot = MagicMock()
        mock_dp = MagicMock()
        mock_dp.feed_update = AsyncMock()

        # Act
        await _process_update(update_data, mock_bot, mock_dp)

        # Assert
        mock_dp.feed_update.assert_called_once()
        call_args = mock_dp.feed_update.call_args
        assert call_args.args[0] == mock_bot  # Первый аргумент — bot

        # Проверяем, что второй аргумент — это Update с правильным update_id
        update_obj = call_args.args[1]
        assert update_obj.update_id == 123456789

    @pytest.mark.asyncio
    async def test_process_update_handles_parsing_error_gracefully(self) -> None:
        """Проверить обработку ошибок парсинга без исключений."""
        # Arrange
        from src.api.telegram import _process_update

        # Невалидный update (отсутствует update_id)
        invalid_update_data = {"message": {"text": "test"}}
        mock_bot = MagicMock()
        mock_dp = MagicMock()

        # Act & Assert (не должно быть исключения)
        await _process_update(invalid_update_data, mock_bot, mock_dp)

        # feed_update не должна быть вызвана при ошибке парсинга
        mock_dp.feed_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_update_handles_dispatcher_error_gracefully(self) -> None:
        """Проверить обработку ошибок диспетчера без исключений."""
        # Arrange
        from src.api.telegram import _process_update

        update_data = {
            "update_id": 123456789,
            "message": {
                "message_id": 1,
                "date": 1609459200,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Test"},
                "text": "Hello",
            },
        }
        mock_bot = MagicMock()
        mock_dp = MagicMock()
        mock_dp.feed_update = AsyncMock(side_effect=Exception("Handler error"))

        # Act & Assert (не должно быть исключения)
        await _process_update(update_data, mock_bot, mock_dp)

        # feed_update должна быть вызвана, но ошибка перехвачена
        mock_dp.feed_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_update_logs_update_id_on_error(self) -> None:
        """Проверить логирование update_id при ошибке обработки."""
        # Arrange
        from src.api.telegram import _process_update

        update_data = {
            "update_id": 999888777,
            "message": {
                "message_id": 1,
                "date": 1609459200,
                "chat": {"id": 123, "type": "private"},
            },
        }
        mock_bot = MagicMock()
        mock_dp = MagicMock()
        mock_dp.feed_update = AsyncMock(side_effect=Exception("Test error"))

        # Act
        with patch("src.api.telegram.logger") as mock_logger:
            await _process_update(update_data, mock_bot, mock_dp)

            # Assert
            # Проверяем, что logger.exception был вызван с упоминанием update_id
            mock_logger.exception.assert_called_once()
            call_args = mock_logger.exception.call_args
            # Проверяем, что в логе есть update_id
            assert "999888777" in str(call_args)

    def test_telegram_webhook_response_has_no_body(self, client: TestClient) -> None:
        """Проверить, что ответ не содержит тела (пустой response)."""
        # Arrange
        update_data = {
            "update_id": 123456789,
            "message": {
                "message_id": 1,
                "date": 1609459200,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Test"},
                "text": "Hello",
            },
        }

        # Act
        response = client.post("/api/telegram/webhook", json=update_data)

        # Assert
        assert response.status_code == 200
        assert response.text == ""  # Пустое тело ответа

    @patch("src.api.telegram._process_update", new_callable=AsyncMock)
    def test_telegram_webhook_passes_correct_args_to_background_task(
        self, mock_process_update: AsyncMock, client: TestClient, test_app: FastAPI
    ) -> None:
        """Проверить передачу правильных аргументов в фоновую задачу.

        Args:
            mock_process_update: Мок функции _process_update.
            client: Тестовый HTTP-клиент.
            test_app: Тестовое FastAPI приложение.
        """
        # Arrange
        update_data = {
            "update_id": 123456789,
            "message": {
                "message_id": 1,
                "date": 1609459200,
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Test"},
                "text": "Hello",
            },
        }

        # Act
        client.post("/api/telegram/webhook", json=update_data)

        # Assert
        mock_process_update.assert_called_once()
        call_args = mock_process_update.call_args
        # Первый аргумент — update_data
        assert call_args.args[0] == update_data
        # Второй аргумент — bot из app.state
        assert call_args.args[1] == test_app.state.bot
        # Третий аргумент — dp из app.state
        assert call_args.args[2] == test_app.state.dp
