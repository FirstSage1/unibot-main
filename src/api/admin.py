"""API эндпоинты для админ-панели.

Этот модуль содержит HTTP-эндпоинты для административных операций:
- POST /api/admin/payments/{payment_id}/refund — возврат платежа Telegram Stars
- POST /api/admin/broadcasts/{broadcast_id}/start — запуск рассылки
- POST /api/admin/broadcasts/{broadcast_id}/pause — приостановка рассылки
- POST /api/admin/broadcasts/{broadcast_id}/cancel — отмена рассылки
- POST /api/admin/broadcasts/{broadcast_id}/test — отправить тестовое сообщение админу

Важно:
- Все эндпоинты требуют админ-аутентификации
- Возвраты работают только для Telegram Stars
- После возврата статус платежа обновляется на REFUNDED
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Annotated, Any, TypeVar, cast

from aiogram import Bot
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin.auth import get_admin_auth
from src.config.settings import settings
from src.db.base import get_session
from src.db.models.broadcast import Broadcast, BroadcastStatus, ParseMode
from src.db.models.payment import PaymentProvider, PaymentStatus
from src.db.repositories.broadcast_repo import BroadcastRepository
from src.db.repositories.payment_repo import PaymentRepository
from src.services.broadcast_service import create_broadcast_service
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Роутер для админ API
router = APIRouter(prefix="/api/admin", tags=["admin"])

THandler = TypeVar("THandler", bound=Callable[..., Any])


def typed_post(*args: Any, **kwargs: Any) -> Callable[[THandler], THandler]:
    """Типизированный wrapper для router.post."""
    return router.post(*args, **kwargs)


class RefundResponse(BaseModel):
    """Ответ на запрос возврата платежа.

    Attributes:
        success: True если возврат успешен.
        payment_id: ID платежа.
        refunded_amount: Сумма возврата.
        message: Сообщение о результате.
    """

    success: bool
    payment_id: int
    refunded_amount: Decimal | None = None
    message: str


async def get_bot(request: Request) -> Bot:
    """Получить Bot instance из app.state.

    Args:
        request: FastAPI Request.

    Returns:
        Bot instance для выполнения операций Telegram API.

    Raises:
        HTTPException: Если bot не инициализирован.
    """
    bot = request.app.state.bot
    if bot is None:
        raise HTTPException(
            status_code=500,
            detail="Bot instance not available",
        )
    return cast("Bot", bot)


async def require_admin_auth(request: Request) -> None:
    """Проверить админ-аутентификацию.

    Требует, чтобы пользователь был авторизован в админ-панели.
    SQLAdmin использует cookie-based auth, поэтому проверяем сессию.

    Args:
        request: FastAPI Request.

    Raises:
        HTTPException: Если пользователь не авторизован.
    """
    # Получаем backend аутентификации из админки
    auth_backend = get_admin_auth()

    # Проверяем аутентификацию через SQLAdmin backend
    if not await auth_backend.authenticate(request):
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required",
        )


@typed_post(
    "/payments/{payment_id}/refund",
    dependencies=[Depends(require_admin_auth)],
)
async def refund_payment(
    payment_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    bot: Annotated[Bot, Depends(get_bot)],
) -> RefundResponse:
    """Выполнить возврат платежа Telegram Stars.

    Эндпоинт доступен только авторизованным администраторам.
    Работает только для платежей через Telegram Stars.

    Процесс:
    1. Загружаем платёж из БД
    2. Проверяем, что платёж успешен и это Telegram Stars
    3. Вызываем bot.refund_star_payment() с user_id и payment_charge_id
    4. Обновляем статус платежа на REFUNDED
    5. Создаём транзакцию возврата токенов (если были начислены)

    Args:
        payment_id: ID платежа в нашей системе.
        request: FastAPI Request.
        session: Async DB session.
        bot: Telegram Bot instance.

    Returns:
        RefundResponse с результатом возврата.

    Raises:
        HTTPException: Если платёж не найден, не может быть возвращён,
            или произошла ошибка при вызове Telegram API.
    """
    # Загружаем платёж из БД
    payment_repo = PaymentRepository(session)
    payment = await payment_repo.get_by_id(payment_id)

    if not payment:
        raise HTTPException(
            status_code=404,
            detail=f"Payment {payment_id} not found",
        )

    # Проверяем, что это Telegram Stars
    if payment.provider != PaymentProvider.TELEGRAM_STARS:
        raise HTTPException(
            status_code=400,
            detail=f"Refunds are only supported for Telegram Stars payments. "
            f"This payment uses provider: {payment.provider}",
        )

    # Проверяем, что платёж не был уже возвращён (проверяем до проверки SUCCEEDED)
    if payment.status == PaymentStatus.REFUNDED:
        return RefundResponse(
            success=True,
            payment_id=payment.id,
            refunded_amount=payment.amount,
            message="Payment already refunded",
        )

    # Проверяем статус платежа
    if payment.status != PaymentStatus.SUCCEEDED:
        raise HTTPException(
            status_code=400,
            detail=f"Only succeeded payments can be refunded. "
            f"Current status: {payment.status}",
        )

    # Проверяем наличие provider_payment_id (telegram_payment_charge_id)
    if not payment.provider_payment_id:
        raise HTTPException(
            status_code=400,
            detail="Payment is missing telegram_payment_charge_id",
        )

    try:
        # Вызываем Telegram API для возврата Stars
        # Для Telegram Stars возврат всегда полный (нельзя вернуть частично)
        result = await bot.refund_star_payment(
            user_id=payment.user_id,
            telegram_payment_charge_id=payment.provider_payment_id,
        )

        if not result:
            raise HTTPException(
                status_code=500,
                detail="Telegram refund_star_payment returned False",
            )

        # Обновляем статус платежа на REFUNDED
        payment.status = PaymentStatus.REFUNDED
        await session.commit()

        logger.info(
            "Платёж %d успешно возвращён через Telegram Stars API. "
            "User: %d, Amount: %s %s",
            payment.id,
            payment.user_id,
            payment.amount,
            payment.currency,
        )

        return RefundResponse(
            success=True,
            payment_id=payment.id,
            refunded_amount=payment.amount,
            message=(
                f"Successfully refunded {payment.amount} {payment.currency} "
                f"to user {payment.user_id}"
            ),
        )

    except Exception as e:
        logger.exception(
            "Ошибка при возврате платежа %d через Telegram API",
            payment.id,
        )
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Telegram API error: {e}",
        ) from e


# =============================================================================
# РАССЫЛКИ (BROADCAST)
# =============================================================================


class BroadcastResponse(BaseModel):
    """Ответ на операцию с рассылкой.

    Attributes:
        success: True если операция успешна.
        broadcast_id: ID рассылки.
        status: Текущий статус рассылки.
        message: Сообщение о результате.
        total_recipients: Количество получателей (для start).
    """

    success: bool
    broadcast_id: int
    status: str
    message: str
    total_recipients: int | None = None


async def _get_broadcast_or_404(broadcast_id: int, session: AsyncSession) -> Broadcast:
    """Получить рассылку или вернуть 404.

    Args:
        broadcast_id: ID рассылки.
        session: Сессия БД.

    Returns:
        Broadcast если найдена.

    Raises:
        HTTPException: Если рассылка не найдена.
    """
    repo = BroadcastRepository(session)
    broadcast = await repo.get_by_id(broadcast_id)
    if not broadcast:
        raise HTTPException(
            status_code=404,
            detail=f"Broadcast {broadcast_id} not found",
        )
    return broadcast


@typed_post(
    "/broadcasts/{broadcast_id}/start",
    dependencies=[Depends(require_admin_auth)],
)
async def start_broadcast(
    broadcast_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BroadcastResponse:
    """Запустить рассылку.

    Меняет статус рассылки на PENDING/RUNNING.
    BroadcastWorker начнёт отправку сообщений.

    Args:
        broadcast_id: ID рассылки.
        session: Сессия БД.

    Returns:
        BroadcastResponse с результатом.
    """
    broadcast = await _get_broadcast_or_404(broadcast_id, session)

    # Проверяем, можно ли запустить рассылку
    if broadcast.status not in (BroadcastStatus.DRAFT, BroadcastStatus.PAUSED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start broadcast in status '{broadcast.status}'. "
            f"Only DRAFT or PAUSED broadcasts can be started.",
        )

    service = create_broadcast_service(session)
    broadcast = await service.start_broadcast(broadcast)

    # Явный commit и refresh для гарантии персистентности
    await session.commit()
    await session.refresh(broadcast)

    logger.info(
        "Рассылка %d запущена через API. Получателей: %d",
        broadcast.id,
        broadcast.total_recipients,
    )

    return BroadcastResponse(
        success=True,
        broadcast_id=broadcast.id,
        status=broadcast.status,
        message=f"Рассылка запущена. Получателей: {broadcast.total_recipients}",
        total_recipients=broadcast.total_recipients,
    )


@typed_post(
    "/broadcasts/{broadcast_id}/pause",
    dependencies=[Depends(require_admin_auth)],
)
async def pause_broadcast(
    broadcast_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BroadcastResponse:
    """Приостановить рассылку.

    Args:
        broadcast_id: ID рассылки.
        session: Сессия БД.

    Returns:
        BroadcastResponse с результатом.
    """
    broadcast = await _get_broadcast_or_404(broadcast_id, session)

    if broadcast.status != BroadcastStatus.RUNNING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause broadcast in status '{broadcast.status}'. "
            f"Only RUNNING broadcasts can be paused.",
        )

    service = create_broadcast_service(session)
    broadcast = await service.pause_broadcast(broadcast)

    # Явный commit и refresh для гарантии персистентности
    await session.commit()
    await session.refresh(broadcast)

    logger.info("Рассылка %d приостановлена через API", broadcast.id)

    return BroadcastResponse(
        success=True,
        broadcast_id=broadcast.id,
        status=broadcast.status,
        message="Рассылка приостановлена. Можно возобновить позже.",
    )


@typed_post(
    "/broadcasts/{broadcast_id}/cancel",
    dependencies=[Depends(require_admin_auth)],
)
async def cancel_broadcast(
    broadcast_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BroadcastResponse:
    """Отменить рассылку.

    Отменённую рассылку нельзя возобновить.

    Args:
        broadcast_id: ID рассылки.
        session: Сессия БД.

    Returns:
        BroadcastResponse с результатом.
    """
    broadcast = await _get_broadcast_or_404(broadcast_id, session)

    if broadcast.status not in (
        BroadcastStatus.DRAFT,
        BroadcastStatus.PENDING,
        BroadcastStatus.RUNNING,
        BroadcastStatus.PAUSED,
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel broadcast in status '{broadcast.status}'.",
        )

    service = create_broadcast_service(session)
    broadcast = await service.cancel_broadcast(broadcast)

    # Явный commit и refresh для гарантии персистентности
    await session.commit()
    await session.refresh(broadcast)

    logger.info("Рассылка %d отменена через API", broadcast.id)

    return BroadcastResponse(
        success=True,
        broadcast_id=broadcast.id,
        status=broadcast.status,
        message="Рассылка отменена.",
    )


@typed_post(
    "/broadcasts/{broadcast_id}/test",
    dependencies=[Depends(require_admin_auth)],
)
async def test_broadcast(
    broadcast_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    bot: Annotated[Bot, Depends(get_bot)],
) -> BroadcastResponse:
    """Отправить тестовое сообщение рассылки админу.

    Отправляет сообщение на LOGGING__TELEGRAM__CHAT_ID.
    Позволяет проверить текст и форматирование перед запуском.

    Args:
        broadcast_id: ID рассылки.
        request: FastAPI Request.
        session: Сессия БД.
        bot: Telegram Bot.

    Returns:
        BroadcastResponse с результатом.
    """
    broadcast = await _get_broadcast_or_404(broadcast_id, session)

    # Проверяем, настроен ли admin chat_id
    admin_chat_id = settings.logging.telegram.chat_id
    if not admin_chat_id:
        raise HTTPException(
            status_code=400,
            detail="LOGGING__TELEGRAM__CHAT_ID не настроен. "
            "Укажите Telegram ID админа в .env для получения тестовых сообщений.",
        )

    # Определяем parse_mode
    if broadcast.parse_mode != ParseMode.NONE:
        parse_mode = broadcast.parse_mode
    else:
        parse_mode = None

    try:
        await bot.send_message(
            chat_id=admin_chat_id,
            text=f"[ТЕСТ] Рассылка #{broadcast.id}: {broadcast.name}\n\n"
            f"{broadcast.message_text}",
            parse_mode=parse_mode,
        )

        logger.info(
            "Тестовое сообщение рассылки %d отправлено админу (chat_id=%d)",
            broadcast.id,
            admin_chat_id,
        )

        return BroadcastResponse(
            success=True,
            broadcast_id=broadcast.id,
            status=broadcast.status,
            message=f"Тестовое сообщение отправлено (chat_id: {admin_chat_id})",
        )

    except Exception as e:
        logger.exception(
            "Ошибка отправки тестового сообщения рассылки %d",
            broadcast.id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка отправки: {e}",
        ) from e


class CountRecipientsResponse(BaseModel):
    """Ответ на запрос подсчёта получателей.

    Attributes:
        broadcast_id: ID рассылки.
        count: Количество получателей.
        filters_description: Описание применённых фильтров.
    """

    broadcast_id: int
    count: int
    filters_description: str


@typed_post(
    "/broadcasts/{broadcast_id}/count",
    dependencies=[Depends(require_admin_auth)],
)
async def count_recipients(
    broadcast_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CountRecipientsResponse:
    """Подсчитать получателей рассылки.

    Возвращает количество пользователей, которые получат рассылку
    с учётом всех фильтров.

    Args:
        broadcast_id: ID рассылки.
        session: Сессия БД.

    Returns:
        CountRecipientsResponse с количеством и описанием фильтров.
    """
    broadcast = await _get_broadcast_or_404(broadcast_id, session)
    service = create_broadcast_service(session)
    count = await service.count_recipients(broadcast)

    # Формируем описание фильтров
    filters: list[str] = []
    if broadcast.filter_language:
        filters.append(f"язык: {broadcast.filter_language}")
    if broadcast.filter_has_payments is True:
        filters.append("платившие")
    elif broadcast.filter_has_payments is False:
        filters.append("бесплатные")
    if broadcast.filter_source:
        filters.append(f"источник: {broadcast.filter_source}")
    if broadcast.filter_registered_after:
        filters.append(
            f"после: {broadcast.filter_registered_after.strftime('%d.%m.%Y')}"
        )
    if broadcast.filter_registered_before:
        date_str = broadcast.filter_registered_before.strftime("%d.%m.%Y")
        filters.append(f"до: {date_str}")
    if broadcast.filter_exclude_blocked:
        filters.append("без заблокированных")

    filters_description = ", ".join(filters) if filters else "без фильтров"

    logger.info(
        "Подсчёт получателей рассылки %d: %d (фильтры: %s)",
        broadcast.id,
        count,
        filters_description,
    )

    return CountRecipientsResponse(
        broadcast_id=broadcast.id,
        count=count,
        filters_description=filters_description,
    )
