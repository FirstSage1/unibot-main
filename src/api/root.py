"""Корневые эндпоинты.

Обработка запросов на корневой путь /:
- GET / — редирект на админку
- POST / — webhook от YooKassa (упрощённая настройка)

YooKassa позволяет указать только домен без пути при настройке webhook.
Это упрощает настройку — достаточно указать https://your-domain.ru/
"""

from typing import TYPE_CHECKING, Any

from aiogram import Bot
from aiogram.enums import ParseMode
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse

from src.config.settings import settings
from src.config.yaml_config import yaml_config
from src.db.base import DatabaseSession
from src.db.repositories.user_repo import UserRepository
from src.providers.payments import create_yookassa_provider
from src.providers.payments.base import PaymentResult
from src.services.payment_service import create_payment_service
from src.utils.i18n import create_localization
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from src.db.models.user import User
    from src.providers.payments.base import BasePaymentProvider

logger = get_logger(__name__)

router = APIRouter(tags=["root"])


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


@router.get("/")
async def index() -> RedirectResponse:
    """Главная страница — редирект на админку.

    При открытии корня сайта перенаправляем в админ-панель.
    Если админка не настроена — пользователь увидит страницу входа.

    Returns:
        RedirectResponse на /admin
    """
    return RedirectResponse(url="/admin", status_code=302)


@router.post("/")
async def root_webhook(request: Request) -> Response:
    """Webhook от YooKassa на корневом пути.

    YooKassa позволяет указать только домен без пути при настройке webhook.
    Это упрощает настройку — достаточно указать https://your-domain.ru/

    Поддерживаемые события:
    - payment.succeeded — платёж успешно завершён
    - payment.canceled — платёж отменён
    - payment.waiting_for_capture — ожидает подтверждения
    - refund.succeeded — возврат успешен

    Args:
        request: HTTP-запрос от YooKassa.

    Returns:
        Response с кодом 200 OK.
    """
    # Проверяем, настроен ли YooKassa
    if not settings.payments.has_yookassa:
        logger.debug("POST / получен, но YooKassa не настроен — игнорируем")
        return Response(status_code=200)

    try:
        # Читаем тело запроса
        payload = await request.body()

        # Пробуем распарсить как JSON для проверки что это webhook
        try:
            data: dict[str, Any] = await request.json()
        except (ValueError, TypeError):
            # Не JSON — это не YooKassa webhook
            logger.debug("POST / — не JSON, игнорируем")
            return Response(status_code=200)

        # Проверяем что это YooKassa webhook по структуре
        if "event" not in data or "object" not in data:
            logger.debug("POST / — не похоже на YooKassa webhook, игнорируем")
            return Response(status_code=200)

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
            logger.warning("YooKassa webhook на /: невалидная подпись")
            return Response(status_code=200)

        logger.info(
            "YooKassa webhook на /: event=%s",
            data.get("event", "unknown"),
        )

        # Обрабатываем webhook через PaymentService
        async with DatabaseSession() as session:
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
        logger.exception("YooKassa webhook на / ошибка")
        return Response(status_code=200)
