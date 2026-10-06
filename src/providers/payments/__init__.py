"""Платёжные провайдеры.

Этот модуль содержит адаптеры для работы с платёжными системами:
- YooKassa — для платежей в рублях (РФ)
- Stripe — для международных платежей в USD
- Telegram Stars — встроенная валюта Telegram

Архитектура:
- BasePaymentProvider — абстрактный интерфейс для всех провайдеров
- Каждый провайдер реализует методы создания платежа и обработки webhook
- PaymentService в services/ оркестрирует работу с провайдерами

Пример использования:
    from src.providers.payments import (
        create_telegram_stars_provider,
        create_yookassa_provider,
        create_stripe_provider,
    )

    # Создаём провайдеры
    providers = {}

    # Telegram Stars — всегда доступен
    providers["telegram_stars"] = create_telegram_stars_provider()

    # YooKassa — если настроен
    if settings.payments.has_yookassa:
        providers["yookassa"] = create_yookassa_provider(
            shop_id=settings.payments.yookassa.shop_id,
            secret_key=settings.payments.yookassa.secret_key.get_secret_value(),
        )

    # Stripe — если настроен
    if settings.payments.has_stripe:
        providers["stripe"] = create_stripe_provider(
            secret_key=settings.payments.stripe.secret_key.get_secret_value(),
        )
"""

from src.core.exceptions import PaymentError
from src.providers.payments.base import (
    BasePaymentProvider,
    PaymentIntent,
    PaymentResult,
    PaymentStatus,
)
from src.providers.payments.stars import (
    TelegramStarsInvoice,
    TelegramStarsProvider,
    create_telegram_stars_provider,
)
from src.providers.payments.stripe import (
    StripeProvider,
    create_stripe_provider,
)
from src.providers.payments.yookassa import (
    YooKassaProvider,
    create_yookassa_provider,
)

__all__ = [
    # Base
    "BasePaymentProvider",
    "PaymentError",
    "PaymentIntent",
    "PaymentResult",
    "PaymentStatus",
    # Stripe
    "StripeProvider",
    "TelegramStarsInvoice",
    # Telegram Stars
    "TelegramStarsProvider",
    # YooKassa
    "YooKassaProvider",
    "create_stripe_provider",
    "create_telegram_stars_provider",
    "create_yookassa_provider",
]
