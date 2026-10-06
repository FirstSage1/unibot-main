"""Провайдер платежей через Telegram Stars.

Telegram Stars — встроенная валюта Telegram для оплаты внутри ботов.
Работает через Telegram Bot Payments API (send_invoice, successful_payment).

Особенности:
- НЕ требует внешних API ключей — всё через Telegram Bot API
- Пользователь платит прямо в Telegram (без редиректов)
- Валюта: XTR (Telegram Stars)
- Минимальный платёж: 1 Star
- Поддержка подписок с автопродлением (30 дней)

Процесс оплаты:
1. Бот отправляет invoice через bot.send_invoice() с currency="XTR"
2. Пользователь подтверждает оплату в Telegram
3. Telegram отправляет pre_checkout_query
4. Бот отвечает answer_pre_checkout_query(ok=True)
5. Telegram списывает Stars и отправляет successful_payment

Документация:
- https://core.telegram.org/bots/api#payments
- https://core.telegram.org/bots/payments-stars
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from typing_extensions import override

from src.core.exceptions import PaymentError
from src.providers.payments.base import (
    BasePaymentProvider,
    PaymentIntent,
    PaymentResult,
    PaymentStatus,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Валюта Telegram Stars
TELEGRAM_STARS_CURRENCY = "XTR"


@dataclass
class TelegramStarsInvoice:
    """Данные invoice для отправки через bot.send_invoice().

    Эта структура содержит всё необходимое для отправки invoice пользователю.
    Используется во внутреннем обработчике покупки (buy.py).

    Attributes:
        title: Название товара (показывается в invoice).
        description: Описание товара.
        payload: Уникальный payload для идентификации платежа.
            Формат: JSON с user_id и tariff_slug.
        currency: Код валюты (всегда "XTR" для Stars).
        prices: Список цен (LabeledPrice).
            Для Stars обычно одна позиция с суммой в Stars.
    """

    title: str
    description: str
    payload: str
    currency: str
    prices: list[dict[str, Any]]

    def to_send_invoice_kwargs(self) -> dict[str, Any]:
        """Преобразовать в kwargs для bot.send_invoice().

        Returns:
            Словарь с параметрами для send_invoice().
            Не включает chat_id — его нужно добавить отдельно.

        Example:
            invoice = provider.create_invoice(...)
            await bot.send_invoice(
                chat_id=user.telegram_id,
                **invoice.to_send_invoice_kwargs()
            )
        """
        return {
            "title": self.title,
            "description": self.description,
            "payload": self.payload,
            "currency": self.currency,
            "prices": self.prices,
            # provider_token НЕ нужен для Telegram Stars (или пустая строка)
            "provider_token": "",
        }


class TelegramStarsProvider(BasePaymentProvider):
    """Провайдер платежей через Telegram Stars.

    Особенность: не делает HTTP-запросы к внешним API.
    Вместо этого подготавливает данные для bot.send_invoice().

    Атрибуты класса:
        PROVIDER_NAME: Имя провайдера для идентификации.
        CURRENCY: Код валюты (XTR).

    Example:
        provider = TelegramStarsProvider()

        # Создаём "платёж" (получаем данные для invoice)
        intent = await provider.create_payment(
            amount=Decimal("50"),
            currency="XTR",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Покупка 100 токенов",
        )

        # intent.metadata["invoice"] содержит TelegramStarsInvoice
        invoice = intent.metadata["invoice"]

        # Отправляем invoice пользователю
        await bot.send_invoice(
            chat_id=user_id,
            **invoice.to_send_invoice_kwargs()
        )
    """

    PROVIDER_NAME = "telegram_stars"
    CURRENCY = TELEGRAM_STARS_CURRENCY

    @property
    @override
    def provider_name(self) -> str:
        """Название провайдера."""
        return self.PROVIDER_NAME

    @property
    @override
    def currency(self) -> str:
        """Основная валюта провайдера (XTR)."""
        return self.CURRENCY

    @override
    async def create_payment(
        self,
        amount: Decimal,
        currency: str,
        user_id: int,
        *,
        tariff_slug: str,
        description: str,
        return_url: str | None = None,
        save_payment_method: bool = False,
        **kwargs: Any,
    ) -> PaymentIntent:
        """Создать платёж через Telegram Stars.

        В отличие от YooKassa/Stripe, здесь НЕ создаётся платёж на стороне
        внешнего сервиса. Вместо этого:
        1. Формируем payload с идентификаторами (user_id, tariff_slug)
        2. Создаём структуру TelegramStarsInvoice
        3. Возвращаем её в metadata для последующей отправки через bot.send_invoice()

        Args:
            amount: Сумма в Stars (целое число, Decimal для совместимости).
            currency: Код валюты (должен быть "XTR").
            user_id: Telegram ID пользователя.
            tariff_slug: Slug тарифа из config.yaml.
            description: Описание платежа для пользователя.
            return_url: Не используется (оплата внутри Telegram).
            save_payment_method: Не используется (Stars не поддерживают).
            **kwargs: Дополнительные параметры (title для invoice).

        Returns:
            PaymentIntent с данными invoice в metadata["invoice"].

        Raises:
            PaymentError: Если валюта не XTR или сумма невалидна.

        Example:
            intent = await provider.create_payment(
                amount=Decimal("50"),
                currency="XTR",
                user_id=123456789,
                tariff_slug="tokens_100",
                description="Покупка 100 токенов",
                title="100 токенов",  # Опционально
            )
        """
        # Валидируем валюту
        if currency.upper() != self.CURRENCY:
            raise PaymentError(
                f"Telegram Stars поддерживает только валюту XTR, получено: {currency}",
                provider=self.provider_name,
            )

        # Валидируем сумму (Stars — целые числа, минимум 1)
        stars_amount = int(amount)
        if stars_amount < 1:
            raise PaymentError(
                f"Минимальная сумма — 1 Star, получено: {amount}",
                provider=self.provider_name,
            )

        # Формируем payload — JSON с идентификаторами платежа
        # Этот payload вернётся в successful_payment
        payload_data = {
            "user_id": user_id,
            "tariff_slug": tariff_slug,
            "provider": self.provider_name,
            "created_at": datetime.now(UTC).isoformat(),
        }
        payload = json.dumps(payload_data, ensure_ascii=False)

        # Получаем title из kwargs или формируем автоматически
        title = kwargs.get("title", description)

        # Формируем invoice
        invoice = TelegramStarsInvoice(
            title=title,
            description=description,
            payload=payload,
            currency=self.CURRENCY,
            prices=[
                {
                    "label": title,
                    "amount": stars_amount,  # Для XTR — сумма в Stars (целое число)
                }
            ],
        )

        logger.info(
            "Подготовлен invoice для Telegram Stars: user_id=%s, tariff=%s, stars=%d",
            user_id,
            tariff_slug,
            stars_amount,
        )

        # Возвращаем PaymentIntent
        # payment_id = None, потому что платёж ещё не создан
        # confirmation_url = None, потому что оплата внутри Telegram
        return PaymentIntent(
            payment_id=None,  # Будет присвоен после successful_payment
            provider=self.provider_name,
            amount=Decimal(stars_amount),
            currency=self.CURRENCY,
            status=PaymentStatus.PENDING,
            confirmation_url=None,  # Оплата внутри Telegram
            metadata={
                "user_id": user_id,
                "tariff_slug": tariff_slug,
                "invoice": invoice,  # Данные для bot.send_invoice()
                "payload": payload,
            },
            created_at=datetime.now(UTC),
        )

    @override
    async def verify_webhook(self, payload: bytes, signature: str) -> bool:
        """Проверить подпись webhook.

        Для Telegram Stars webhook'и не используются напрямую.
        Вместо этого бот получает Update с successful_payment
        через стандартный механизм aiogram.

        Безопасность обеспечивается Telegram — только реальные
        successful_payment приходят через Bot API.

        Args:
            payload: Не используется.
            signature: Не используется.

        Returns:
            Всегда True (проверка на уровне aiogram).
        """
        # Telegram гарантирует подлинность successful_payment
        # Дополнительная проверка не требуется
        return True

    @override
    async def process_webhook(self, data: dict[str, Any]) -> PaymentResult:
        """Обработать данные successful_payment.

        Вызывается из обработчика aiogram после получения successful_payment.
        Парсит данные платежа и возвращает структурированный результат.

        Формат данных (из aiogram Message.successful_payment):
        {
            "currency": "XTR",
            "total_amount": 50,
            "invoice_payload": "{\"user_id\": 123, ...}",
            "telegram_payment_charge_id": "...",
            "provider_payment_charge_id": "...",
            "subscription_expiration_date": 1234567890,  # Опционально
            "is_recurring": false,
            "is_first_recurring": false,
        }

        Args:
            data: Данные из successful_payment (dict-представление).

        Returns:
            PaymentResult с информацией о платеже.

        Raises:
            PaymentError: Если данные невалидны.
        """
        try:
            # Извлекаем основные данные
            currency = data.get("currency", self.CURRENCY)
            total_amount = data.get("total_amount", 0)

            # telegram_payment_charge_id — уникальный ID платежа в Telegram
            payment_id = data.get("telegram_payment_charge_id", "")
            if not payment_id:
                raise PaymentError(
                    "Отсутствует telegram_payment_charge_id",
                    provider=self.provider_name,
                )

            # Парсим payload с метаданными
            # В payload передаётся payment_id из нашей БД для надёжной связи
            payload_str = data.get("invoice_payload", "{}")
            try:
                metadata = json.loads(payload_str)
            except json.JSONDecodeError:
                logger.warning(
                    "Не удалось распарсить invoice_payload: %s",
                    payload_str,
                )
                metadata = {}

            # Извлекаем internal_payment_id для поиска платежа в БД
            internal_payment_id = metadata.get("payment_id")
            if internal_payment_id:
                logger.debug(
                    "Извлечён internal_payment_id из payload: %s",
                    internal_payment_id,
                )

            # Добавляем данные о подписке если есть
            is_recurring = data.get("is_recurring", False)
            is_first_recurring = data.get("is_first_recurring", False)
            subscription_expiration_ts = data.get("subscription_expiration_date")

            subscription_expiration_date = None
            if subscription_expiration_ts:
                subscription_expiration_date = datetime.fromtimestamp(
                    subscription_expiration_ts, tz=UTC
                )

            logger.info(
                "Обработан successful_payment: payment_id=%s, amount=%d %s, "
                "is_recurring=%s",
                payment_id,
                total_amount,
                currency,
                is_recurring,
            )

            return PaymentResult(
                payment_id=payment_id,
                provider=self.provider_name,
                status=PaymentStatus.SUCCEEDED,
                amount=Decimal(total_amount),
                currency=currency,
                metadata=metadata,
                is_recurring=is_recurring or is_first_recurring,
                subscription_expiration_date=subscription_expiration_date,
                raw_data=data,
            )

        except PaymentError:
            raise
        except Exception as e:
            logger.exception("Ошибка обработки successful_payment")
            raise PaymentError(
                f"Ошибка обработки данных платежа: {e}",
                provider=self.provider_name,
                original_error=e,
            ) from e

    @override
    async def refund_payment(
        self,
        payment_id: str,
        amount: Decimal | None = None,
        *,
        reason: str | None = None,
    ) -> PaymentResult:
        """Вернуть платёж Telegram Stars.

        Для возврата платежа нужно вызвать Bot.refund_star_payment
        с параметрами user_id и telegram_payment_charge_id.

        ВАЖНО: Эта операция требует Bot instance, который недоступен
        в провайдере. Рефанд должен выполняться на уровне обработчика.

        Args:
            payment_id: ID платежа (telegram_payment_charge_id).
            amount: Сумма возврата (не используется для Stars — всегда полный возврат).
            reason: Причина возврата (опционально, для логирования).

        Returns:
            PaymentResult с ошибкой (операция не поддерживается на уровне провайдера).

        Note:
            Для возврата используйте:
            await bot.refund_star_payment(
                user_id=user_id,
                telegram_payment_charge_id=payment_id,
            )

        Raises:
            PaymentError: Всегда, так как операция должна выполняться на уровне бота.
        """
        logger.warning("Возврат Stars должен выполняться через bot.refund_star_payment")
        raise PaymentError(
            "Refund operation must be performed at bot handler level using "
            "bot.refund_star_payment(user_id, telegram_payment_charge_id). "
            "This provider method is not supported.",
            provider=self.provider_name,
        )

    @override
    async def cancel_subscription(self, subscription_id: str) -> bool:
        """Отменить подписку Telegram Stars.

        Для отмены подписки нужно вызвать Bot.edit_user_star_subscription
        с параметром is_canceled=True.

        ВАЖНО: Эта операция требует Bot instance, который недоступен
        в провайдере. Отмена должна выполняться на уровне обработчика.

        Args:
            subscription_id: ID подписки (telegram_payment_charge_id).

        Returns:
            False (операция не поддерживается на уровне провайдера).

        Note:
            Для отмены используйте:
            await bot.edit_user_star_subscription(
                user_id=user_id,
                telegram_payment_charge_id=subscription_id,
                is_canceled=True,
            )
        """
        logger.warning(
            "Отмена подписки Stars должна выполняться через "
            "bot.edit_user_star_subscription"
        )
        return False


def create_telegram_stars_provider() -> TelegramStarsProvider:
    """Фабричная функция для создания провайдера Telegram Stars.

    Returns:
        Настроенный экземпляр TelegramStarsProvider.

    Example:
        provider = create_telegram_stars_provider()
        providers = {"telegram_stars": provider}
        payment_service = create_payment_service(session, providers)
    """
    return TelegramStarsProvider()
