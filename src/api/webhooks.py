"""Webhook эндпоинты для платёжных провайдеров.

Этот модуль содержит HTTP-эндпоинты для приёма webhook'ов от:
- YooKassa: POST / (рекомендуется) или POST /api/webhooks/yookassa
- Stripe: POST /api/webhooks/stripe

Telegram Stars обрабатываются через aiogram handlers (successful_payment),
а не через HTTP webhooks.

Важно:
- Webhook'и должны возвращать 200 OK как можно быстрее
- Все провайдеры ожидают ответ в течение нескольких секунд
- При ошибках возвращаем 200 OK (иначе провайдер будет повторять запросы)
- Валидация подписи обязательна для безопасности

Настройка webhook'ов:
- YooKassa: Личный кабинет → Интеграция → HTTP-уведомления
  URL: https://ваш-домен.ru/ (корневой путь для простоты)
- Stripe: Dashboard → Developers → Webhooks
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from aiogram import Bot
from aiogram.enums import ParseMode
from fastapi import APIRouter, Header, Request, Response

from src.config.settings import settings
from src.config.yaml_config import yaml_config
from src.db.base import DatabaseSession
from src.db.repositories.user_repo import UserRepository
from src.providers.payments import (
    create_stripe_provider,
    create_yookassa_provider,
)
from src.providers.payments.base import BasePaymentProvider, PaymentResult
from src.services.payment_service import create_payment_service
from src.utils.i18n import create_localization
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from src.db.models.user import User

logger = get_logger(__name__)

# Роутер для webhook'ов платежей
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

THandler = TypeVar("THandler", bound=Callable[..., Any])


def typed_post(*args: Any, **kwargs: Any) -> Callable[[THandler], THandler]:
    """Типизированный wrapper для router.post."""
    return router.post(*args, **kwargs)


async def _send_payment_notification(
    bot: Bot,
    user: "User",
    result: PaymentResult,
) -> None:
    """Отправить уведомление пользователю об успешной оплате.

    Определяет тип тарифа (разовая покупка или подписка) и отправляет
    соответствующее сообщение пользователю.

    Args:
        bot: Экземпляр Telegram бота.
        user: Пользователь, которому начислены токены.
        result: Результат обработки webhook.
    """
    # Получаем tariff_slug из результата webhook
    tariff_slug = result.tariff_slug
    if not tariff_slug:
        logger.warning(
            "Не удалось определить tariff_slug для уведомления: payment_id=%s",
            result.payment_id,
        )
        return

    # Получаем конфигурацию тарифа
    tariff = yaml_config.get_tariff(tariff_slug)
    if tariff is None:
        logger.warning(
            "Тариф не найден для уведомления: tariff_slug=%s",
            tariff_slug,
        )
        return

    # Определяем количество токенов
    tokens_amount = tariff.effective_tokens

    # Получаем язык пользователя и создаём локализацию
    language = user.language or "ru"
    l10n = create_localization(language)

    # Формируем сообщение в зависимости от типа тарифа
    if tariff.is_subscription:
        message_text = l10n.get("buy_subscription_activated", tokens=tokens_amount)
    else:
        message_text = l10n.get("buy_success", tokens=tokens_amount)

    try:
        await bot.send_message(
            chat_id=user.telegram_id,
            text=message_text,
            parse_mode=ParseMode.HTML,
        )
        logger.info(
            "Отправлено уведомление об оплате: user_id=%d, tariff=%s",
            user.telegram_id,
            tariff_slug,
        )
    except (OSError, RuntimeError) as e:
        # Не падаем при ошибке отправки — платёж уже обработан
        # OSError — сетевые ошибки, RuntimeError — ошибки Telegram API
        logger.warning(
            "Не удалось отправить уведомление об оплате: user_id=%d, error=%s",
            user.telegram_id,
            e,
        )


@typed_post("/yookassa")
async def yookassa_webhook(
    request: Request,
    content_type: str | None = Header(None, alias="Content-Type"),
) -> Response:
    """Обработать webhook от YooKassa.

    YooKassa отправляет уведомления о событиях:
    - payment.succeeded — платёж успешно завершён
    - payment.canceled — платёж отменён
    - payment.waiting_for_capture — ожидает подтверждения (холд)
    - refund.succeeded — возврат успешен

    URL для настройки: https://your-domain.com/api/webhooks/yookassa

    Формат запроса:
    {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": "payment_id",
            "status": "succeeded",
            "amount": {"value": "99.00", "currency": "RUB"},
            "metadata": {"user_id": "123", "tariff_slug": "tokens_100"}
        }
    }

    Args:
        request: HTTP-запрос от YooKassa.
        content_type: Content-Type заголовок.

    Returns:
        Response с кодом 200 OK.
    """
    # Проверяем, настроен ли YooKassa
    if not settings.payments.has_yookassa:
        logger.warning("YooKassa webhook получен, но провайдер не настроен")
        return Response(status_code=200)

    try:
        # Читаем тело запроса
        payload = await request.body()

        # Получаем подпись (если есть)
        signature = request.headers.get("Signature", "")

        # Создаём провайдер
        provider = create_yookassa_provider(
            shop_id=settings.payments.yookassa.shop_id or "",
            secret_key=settings.payments.yookassa.secret_key.get_secret_value()
            if settings.payments.yookassa.secret_key
            else "",
        )

        # Проверяем подпись
        is_valid = await provider.verify_webhook(payload, signature)
        if not is_valid:
            logger.warning("YooKassa webhook: невалидная подпись")
            # Возвращаем 200 чтобы YooKassa не повторял запрос
            return Response(status_code=200)

        # Парсим данные
        data: dict[str, Any] = await request.json()

        logger.info(
            "YooKassa webhook: event=%s",
            data.get("event", "unknown"),
        )

        # Обрабатываем webhook через PaymentService
        async with DatabaseSession() as session:
            # Собираем провайдеры
            providers: dict[str, BasePaymentProvider] = {"yookassa": provider}

            payment_service = create_payment_service(
                session=session,
                providers=providers,
            )

            result = await payment_service.process_webhook("yookassa", data)

            logger.info(
                "YooKassa webhook обработан: payment_id=%s, status=%s",
                result.payment_id,
                result.status,
            )

            # Отправляем уведомление пользователю при успешной оплате
            if result.is_success:
                bot: Bot | None = getattr(request.app.state, "bot", None)
                if bot is not None:
                    # Получаем telegram_id из metadata
                    telegram_id = result.metadata.get("user_id")
                    if telegram_id:
                        user_repo = UserRepository(session)
                        user = await user_repo.get_by_telegram_id(int(telegram_id))
                        if user:
                            await _send_payment_notification(bot, user, result)
                        else:
                            logger.warning(
                                "Пользователь не найден для уведомления: "
                                "telegram_id=%s",
                                telegram_id,
                            )
                else:
                    logger.warning("Bot не доступен для отправки уведомления об оплате")

        return Response(status_code=200)

    except Exception:
        # Логируем ошибку, но возвращаем 200
        # Иначе YooKassa будет повторять запрос
        logger.exception("YooKassa webhook ошибка")
        return Response(status_code=200)


@typed_post("/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(None, alias="Stripe-Signature"),
) -> Response:
    """Обработать webhook от Stripe.

    Stripe отправляет уведомления о событиях:
    - checkout.session.completed — сессия оплаты завершена
    - payment_intent.succeeded — платёж успешен
    - payment_intent.payment_failed — платёж не прошёл
    - charge.refunded — возврат средств

    URL для настройки: https://your-domain.com/api/webhooks/stripe

    Формат запроса:
    {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_...",
                "payment_intent": "pi_...",
                "status": "complete",
                "metadata": {"user_id": "123", "tariff_slug": "tokens_100"}
            }
        }
    }

    Args:
        request: HTTP-запрос от Stripe.
        stripe_signature: Значение заголовка Stripe-Signature.

    Returns:
        Response с кодом 200 OK.
    """
    # Проверяем, настроен ли Stripe
    if not settings.payments.has_stripe:
        logger.warning("Stripe webhook получен, но провайдер не настроен")
        return Response(status_code=200)

    try:
        # Читаем тело запроса
        payload = await request.body()

        # Получаем webhook secret
        webhook_secret = None
        if settings.payments.stripe.webhook_secret:
            webhook_secret = settings.payments.stripe.webhook_secret.get_secret_value()

        # Создаём провайдер
        provider = create_stripe_provider(
            secret_key=settings.payments.stripe.secret_key.get_secret_value()
            if settings.payments.stripe.secret_key
            else "",
            webhook_secret=webhook_secret,
        )

        # Проверяем подпись
        is_valid = await provider.verify_webhook(payload, stripe_signature or "")
        if not is_valid:
            logger.warning("Stripe webhook: невалидная подпись")
            return Response(status_code=200)

        # Парсим данные
        data: dict[str, Any] = await request.json()

        logger.info(
            "Stripe webhook: type=%s",
            data.get("type", "unknown"),
        )

        # Обрабатываем webhook через PaymentService
        async with DatabaseSession() as session:
            providers: dict[str, BasePaymentProvider] = {"stripe": provider}

            payment_service = create_payment_service(
                session=session,
                providers=providers,
            )

            result = await payment_service.process_webhook("stripe", data)

            logger.info(
                "Stripe webhook обработан: payment_id=%s, status=%s",
                result.payment_id,
                result.status,
            )

            # Отправляем уведомление пользователю при успешной оплате
            if result.is_success:
                bot: Bot | None = getattr(request.app.state, "bot", None)
                if bot is not None:
                    # Получаем telegram_id из metadata
                    telegram_id = result.metadata.get("user_id")
                    if telegram_id:
                        user_repo = UserRepository(session)
                        user = await user_repo.get_by_telegram_id(int(telegram_id))
                        if user:
                            await _send_payment_notification(bot, user, result)
                        else:
                            logger.warning(
                                "Пользователь не найден для уведомления: "
                                "telegram_id=%s",
                                telegram_id,
                            )
                else:
                    logger.warning("Bot не доступен для отправки уведомления об оплате")

        return Response(status_code=200)

    except Exception:
        logger.exception("Stripe webhook ошибка")
        return Response(status_code=200)
