"""Проверки уведомлений без потоков, настроек окружения и HTTP-запросов."""

import logging

import pytest

from src.utils.logging import TelegramHandler


@pytest.mark.parametrize(
    ("logger_name", "message", "explains_retry"),
    [
        (
            "aiogram.dispatcher",
            "Failed to fetch updates - TelegramNetworkError: "
            "HTTP Client says - ServerDisconnectedError: Server disconnected",
            True,
        ),
        (
            "aiogram.dispatcher",
            "Failed to fetch updates - TelegramUnauthorizedError: Unauthorized",
            False,
        ),
        ("src.services.ai_service", "Ошибка генерации <test>", False),
    ],
)
def test_notification_explains_only_polling_network_retry(
    logger_name: str, message: str, *, explains_retry: bool
) -> None:
    """Пояснение не скрывает ошибку и не обещает повтор для других сбоев."""
    handler = object.__new__(TelegramHandler)
    record = logging.LogRecord(logger_name, logging.ERROR, "", 0, message, (), None)

    notification = handler._format_message(record)

    assert ("автоматически повторяет подключение" in notification) == explains_retry
    assert handler._escape_html(message) in notification
    assert "<b>ERROR</b>" in notification
    assert "<test>" not in notification
