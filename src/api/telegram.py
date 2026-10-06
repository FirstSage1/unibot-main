"""API endpoint для Telegram webhook.

Этот модуль реализует endpoint для приёма update от Telegram в webhook mode.

КРИТИЧЕСКОЕ ПРАВИЛО (из PRD 2.3):
- Endpoint должен ВСЕГДА отвечать 200 OK как можно быстрее (до любого I/O)
- Вся обработка сообщения — ПОСЛЕ ответа или в фоне
- Если Telegram не получает 200 OK вовремя — он повторяет запрос
- Это может привести к дублированию обработки и генераций
"""

from collections.abc import Callable
from typing import Any, TypeVar

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import APIRouter, BackgroundTasks, Request, Response

from src.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

THandler = TypeVar("THandler", bound=Callable[..., Any])


def typed_post(*args: Any, **kwargs: Any) -> Callable[[THandler], THandler]:
    """Типизированный wrapper для router.post."""
    return router.post(*args, **kwargs)


async def _process_update(
    update_data: dict[str, Any],
    bot: Bot,
    dp: Dispatcher,
) -> None:
    """Обработать update от Telegram в фоновой задаче.

    Эта функция вызывается ПОСЛЕ того, как endpoint вернул 200 OK.
    Вся логика обработки (обращение к БД, AI, генерация) происходит здесь.

    Args:
        update_data: Сырые данные update от Telegram.
        bot: Инстанс Telegram бота.
        dp: Диспетчер aiogram для обработки update.
    """
    try:
        # Парсим update
        update = Update(**update_data)

        logger.debug(
            "Обработка webhook update: type=%s, id=%s",
            update.update_type if hasattr(update, "update_type") else "unknown",
            update.update_id,
        )

        # Передаём update в диспетчер aiogram
        # Он сам роутит на нужный handler
        await dp.feed_update(bot, update)

        logger.debug("Update %s успешно обработан", update.update_id)

    except Exception:
        # Логируем ошибку, но НЕ пробрасываем её выше
        # Webhook уже получил 200 OK, не должны влиять на ответ
        update_id = update_data.get("update_id", "unknown")
        logger.exception("Ошибка обработки webhook update: %s", update_id)


@typed_post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """Принять webhook от Telegram.

    КРИТИЧЕСКИ ВАЖНО:
    1. Сначала ставим обработку в фоновую задачу
    2. Сразу возвращаем 200 OK — ДО любого I/O!
    3. Telegram получает быстрый ответ и не повторяет запрос

    Args:
        request: FastAPI request с доступом к app.state (bot, dp).
        background_tasks: FastAPI механизм для фоновых задач.

    Returns:
        Response с кодом 200 (всегда успешный ответ).
    """
    # Парсим тело запроса
    update_data = await request.json()

    # Получаем bot и dispatcher из app.state
    # Они сохранены в main.py при старте приложения
    bot: Bot = request.app.state.bot
    dp: Dispatcher = request.app.state.dp

    # 1. КРИТИЧНО: Ставим обработку в фон
    background_tasks.add_task(_process_update, update_data, bot, dp)

    # 2. КРИТИЧНО: Сразу возвращаем 200 OK
    # Telegram получит ответ мгновенно, до любой обработки
    return Response(status_code=200)
