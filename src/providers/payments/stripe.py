"""Провайдер платежей через Stripe.

Stripe — международная платёжная система для приёма платежей в USD/EUR.
Поддерживает: банковские карты, Apple Pay, Google Pay и др.

API документация: https://stripe.com/docs/api

Используется Stripe Checkout для простой интеграции:
1. Создаём Checkout Session через POST /v1/checkout/sessions
2. Получаем URL для редиректа пользователя
3. Пользователь оплачивает на странице Stripe
4. Stripe отправляет webhook на наш сервер
5. Обрабатываем webhook и начисляем токены

Настройка webhook в Dashboard Stripe:
1. Developers → Webhooks → Add endpoint
2. URL: https://your-domain.com/api/webhooks/stripe
3. События: checkout.session.completed, payment_intent.succeeded
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
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

# Базовый URL API Stripe
STRIPE_API_URL = "https://api.stripe.com/v1"

# Валюта Stripe — доллары (можно использовать другие, но USD основная)
STRIPE_CURRENCY = "USD"

# Таймаут HTTP-запросов в секундах
HTTP_TIMEOUT = 30.0

# Допустимое расхождение времени для проверки webhook (5 минут)
WEBHOOK_TOLERANCE = 300

# Маппинг статусов Stripe → наши статусы
STRIPE_STATUS_MAP = {
    "open": PaymentStatus.PENDING,
    "complete": PaymentStatus.SUCCEEDED,
    "expired": PaymentStatus.CANCELED,
    # PaymentIntent statuses
    "requires_payment_method": PaymentStatus.PENDING,
    "requires_confirmation": PaymentStatus.PENDING,
    "requires_action": PaymentStatus.PENDING,
    "processing": PaymentStatus.PENDING,
    "requires_capture": PaymentStatus.PENDING,
    "canceled": PaymentStatus.CANCELED,
    "succeeded": PaymentStatus.SUCCEEDED,
}


class StripeProvider(BasePaymentProvider):
    """Провайдер платежей через Stripe.

    Использует Stripe Checkout Sessions API для создания платежей.
    Аутентификация: Bearer token.

    Attributes:
        _secret_key: Секретный ключ API (sk_live_... или sk_test_...).
        _webhook_secret: Секрет для проверки подписи webhook (whsec_...).
        _client: HTTP-клиент для запросов к API.

    Example:
        provider = StripeProvider(
            secret_key="sk_test_...",
            webhook_secret="whsec_...",
        )

        intent = await provider.create_payment(
            amount=Decimal("9.99"),
            currency="USD",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Buy 100 tokens",
            return_url="https://t.me/your_bot",
        )

        # intent.confirmation_url — URL для редиректа пользователя
    """

    PROVIDER_NAME = "stripe"
    CURRENCY = STRIPE_CURRENCY

    def __init__(
        self,
        secret_key: str,
        webhook_secret: str | None = None,
        *,
        timeout: float = HTTP_TIMEOUT,
    ) -> None:
        """Создать провайдер Stripe.

        Args:
            secret_key: Секретный ключ API из Dashboard Stripe.
            webhook_secret: Секрет для проверки подписи webhook.
                Если None — верификация webhook отключена (небезопасно!).
            timeout: Таймаут HTTP-запросов в секундах.
        """
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Получить или создать HTTP-клиент.

        Returns:
            Настроенный HTTP-клиент с авторизацией.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=STRIPE_API_URL,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        return self._client

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    @override
    def provider_name(self) -> str:
        """Название провайдера."""
        return self.PROVIDER_NAME

    @property
    @override
    def currency(self) -> str:
        """Основная валюта провайдера (USD)."""
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
        """Создать платёж через Stripe Checkout.

        Создаёт Checkout Session и возвращает URL для редиректа.

        Stripe принимает суммы в центах (минимальных единицах валюты):
        - USD: $9.99 → 999 cents
        - EUR: €9.99 → 999 cents
        - RUB: ₽99.00 → 9900 kopecks

        Args:
            amount: Сумма платежа в долларах (или другой валюте).
            currency: Код валюты (USD, EUR и др.).
            user_id: Telegram ID пользователя.
            tariff_slug: Slug тарифа из config.yaml.
            description: Описание платежа для пользователя.
            return_url: URL для возврата после оплаты.
            save_payment_method: Сохранить карту для автоплатежей.
            **kwargs: Дополнительные параметры.

        Returns:
            PaymentIntent с confirmation_url для редиректа.

        Raises:
            PaymentError: При ошибке API.
        """
        # Конвертируем сумму в центы (минимальные единицы)
        # Stripe требует целое число
        amount_cents = int(amount * 100)

        if amount_cents < 50:  # Минимум 50 центов для USD
            raise PaymentError(
                f"Минимальная сумма — $0.50, получено: ${amount}",
                provider=self.provider_name,
            )

        # Формируем return URL
        success_url = return_url or "https://t.me"
        cancel_url = return_url or "https://t.me"

        # Формируем данные для Checkout Session
        # Stripe API использует form-encoded формат
        form_data: dict[str, Any] = {
            "mode": "payment",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": str(amount_cents),
            "line_items[0][price_data][product_data][name]": description,
            "line_items[0][quantity]": "1",
            "metadata[user_id]": str(user_id),
            "metadata[tariff_slug]": tariff_slug,
        }

        # Сохранение карты для рекуррентов
        if save_payment_method:
            form_data["payment_intent_data[setup_future_usage]"] = "off_session"

        try:
            client = await self._get_client()

            logger.debug(
                "Stripe create_payment: amount=%s %s, user_id=%s, tariff=%s",
                amount,
                currency,
                user_id,
                tariff_slug,
            )

            response = await client.post(
                "/checkout/sessions",
                data=form_data,
            )

            # Проверяем статус ответа
            if response.status_code >= 400:
                error_data = response.json() if response.content else {}
                error_obj = error_data.get("error", {})
                error_msg = error_obj.get("message", response.text)
                raise PaymentError(
                    f"Ошибка Stripe API: {error_msg}",
                    provider=self.provider_name,
                    is_retryable=response.status_code >= 500,
                )

            data = response.json()

            # Извлекаем данные сессии
            session_id = data.get("id", "")
            status = data.get("status", "open")
            checkout_url = data.get("url")

            # Время истечения сессии
            expires_at = None
            if "expires_at" in data:
                expires_at = datetime.fromtimestamp(data["expires_at"], tz=UTC)

            logger.info(
                "Stripe Checkout Session создан: session_id=%s, status=%s",
                session_id,
                status,
            )

            return PaymentIntent(
                payment_id=session_id,
                provider=self.provider_name,
                amount=amount,
                currency=currency.upper(),
                status=STRIPE_STATUS_MAP.get(status, PaymentStatus.PENDING),
                confirmation_url=checkout_url,
                metadata={
                    "user_id": user_id,
                    "tariff_slug": tariff_slug,
                },
                created_at=datetime.now(UTC),
                expires_at=expires_at,
            )

        except PaymentError:
            raise
        except httpx.TimeoutException as e:
            logger.warning("Stripe таймаут: %s", e)
            raise PaymentError(
                "Таймаут запроса к Stripe",
                provider=self.provider_name,
                is_retryable=True,
                original_error=e,
            ) from e
        except httpx.HTTPError as e:
            logger.exception("Stripe HTTP ошибка")
            raise PaymentError(
                f"Ошибка HTTP: {e}",
                provider=self.provider_name,
                is_retryable=True,
                original_error=e,
            ) from e
        except Exception as e:
            logger.exception("Stripe неожиданная ошибка")
            raise PaymentError(
                f"Неожиданная ошибка: {e}",
                provider=self.provider_name,
                original_error=e,
            ) from e

    @override
    async def verify_webhook(self, payload: bytes, signature: str) -> bool:
        """Проверить подпись webhook от Stripe.

        Stripe использует HMAC-SHA256 для подписи webhook.
        Формат заголовка Stripe-Signature:
        t=timestamp,v1=signature,v0=legacy_signature

        Документация:
        https://stripe.com/docs/webhooks/signatures

        Args:
            payload: Сырое тело HTTP-запроса.
            signature: Значение заголовка Stripe-Signature.

        Returns:
            True если подпись валидна.
        """
        if not self._webhook_secret:
            logger.warning("Stripe webhook_secret не настроен — пропускаем проверку")
            return True

        if not signature:
            logger.warning("Stripe webhook без подписи")
            return False

        try:
            # Парсим заголовок Stripe-Signature
            # Формат: t=123456,v1=abc123,v0=def456
            sig_parts: dict[str, str] = {}
            for part in signature.split(","):
                if "=" in part:
                    key, value = part.split("=", 1)
                    sig_parts[key] = value

            timestamp = sig_parts.get("t", "")
            expected_sig = sig_parts.get("v1", "")

            if not timestamp or not expected_sig:
                logger.warning("Stripe некорректный формат подписи")
                return False

            # Проверяем timestamp (защита от replay attacks)
            current_time = int(time.time())
            sig_time = int(timestamp)
            if abs(current_time - sig_time) > WEBHOOK_TOLERANCE:
                logger.warning(
                    "Stripe webhook timestamp устарел: %d vs %d",
                    sig_time,
                    current_time,
                )
                return False

            # Формируем подписываемую строку
            # Формат: timestamp.payload
            signed_payload = f"{timestamp}.{payload.decode('utf-8')}"

            # Вычисляем HMAC-SHA256
            computed_sig = hmac.new(
                self._webhook_secret.encode(),
                signed_payload.encode(),
                hashlib.sha256,
            ).hexdigest()

            # Сравниваем безопасно
            is_valid = hmac.compare_digest(expected_sig, computed_sig)

            if not is_valid:
                logger.warning("Stripe невалидная подпись webhook")

            return is_valid

        except Exception:
            logger.exception("Stripe ошибка проверки подписи")
            return False

    @override
    async def process_webhook(self, data: dict[str, Any]) -> PaymentResult:
        """Обработать webhook от Stripe.

        Обрабатываем события:
        - checkout.session.completed — сессия оплачена
        - payment_intent.succeeded — платёж успешен
        - payment_intent.payment_failed — платёж не прошёл

        Формат webhook:
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_...",
                    "payment_intent": "pi_...",
                    "status": "complete",
                    "metadata": {...}
                }
            }
        }

        Args:
            data: Распарсенный JSON webhook.

        Returns:
            PaymentResult с информацией о платеже.

        Raises:
            PaymentError: При некорректных данных.
        """
        try:
            event_type = data.get("type", "")
            event_data = data.get("data", {})
            obj = event_data.get("object", {})

            # Определяем ID платежа
            # Для checkout.session — это payment_intent
            # Для payment_intent — это id
            payment_id = obj.get("payment_intent") or obj.get("id", "")

            if not payment_id:
                raise PaymentError(
                    "Отсутствует payment_id в webhook",
                    provider=self.provider_name,
                )

            # Определяем статус
            status = PaymentStatus.PENDING
            error_message = None

            if (
                event_type == "checkout.session.completed"
                or event_type == "payment_intent.succeeded"
            ):
                status = PaymentStatus.SUCCEEDED
            elif event_type == "payment_intent.payment_failed":
                status = PaymentStatus.FAILED
                last_error = obj.get("last_payment_error", {})
                error_message = last_error.get("message", "Платёж не прошёл")
            elif event_type == "checkout.session.expired":
                status = PaymentStatus.CANCELED
            else:
                # Неизвестное событие — используем статус из объекта
                obj_status = obj.get("status", "")
                status = STRIPE_STATUS_MAP.get(obj_status, PaymentStatus.PENDING)

            # Извлекаем сумму
            # Для checkout.session — amount_total в центах
            # Для payment_intent — amount в центах
            amount_cents = obj.get("amount_total") or obj.get("amount", 0)
            amount = Decimal(amount_cents) / 100 if amount_cents else None

            currency = obj.get("currency", self.CURRENCY).upper()

            # Метаданные
            metadata = obj.get("metadata", {})

            # ID платёжного метода (для рекуррентов)
            payment_method_id = obj.get("payment_method")

            logger.info(
                "Stripe webhook: event=%s, payment_id=%s, status=%s",
                event_type,
                payment_id,
                status,
            )

            return PaymentResult(
                payment_id=payment_id,
                provider=self.provider_name,
                status=status,
                amount=amount,
                currency=currency,
                metadata=metadata,
                payment_method_id=payment_method_id,
                error_message=error_message,
                raw_data=data,
            )

        except PaymentError:
            raise
        except Exception as e:
            logger.exception("Stripe ошибка обработки webhook")
            raise PaymentError(
                f"Ошибка обработки webhook: {e}",
                provider=self.provider_name,
                original_error=e,
            ) from e

    @override
    async def charge_saved_card(
        self,
        payment_method_id: str,
        amount: Decimal,
        currency: str,
        user_id: int,
        *,
        tariff_slug: str,
        description: str,
        **kwargs: Any,
    ) -> PaymentResult:
        """Списать с сохранённой карты.

        Создаёт PaymentIntent и подтверждает его автоматически.

        Args:
            payment_method_id: ID сохранённого метода оплаты (pm_...).
            amount: Сумма списания в долларах.
            currency: Код валюты.
            user_id: Telegram ID пользователя.
            tariff_slug: Slug тарифа.
            description: Описание списания.

        Returns:
            PaymentResult с результатом списания.

        Raises:
            PaymentError: При ошибке списания.
        """
        amount_cents = int(amount * 100)

        form_data = {
            "amount": str(amount_cents),
            "currency": currency.lower(),
            "payment_method": payment_method_id,
            "off_session": "true",
            "confirm": "true",
            "description": description,
            "metadata[user_id]": str(user_id),
            "metadata[tariff_slug]": tariff_slug,
            "metadata[is_recurring]": "true",
        }

        try:
            client = await self._get_client()

            response = await client.post(
                "/payment_intents",
                data=form_data,
            )

            if response.status_code >= 400:
                error_data = response.json() if response.content else {}
                error_obj = error_data.get("error", {})
                error_msg = error_obj.get("message", response.text)
                raise PaymentError(
                    f"Ошибка списания Stripe: {error_msg}",
                    provider=self.provider_name,
                )

            data = response.json()

            payment_id = data.get("id", "")
            status_str = data.get("status", "")
            status = STRIPE_STATUS_MAP.get(status_str, PaymentStatus.PENDING)

            logger.info(
                "Stripe recurring payment: payment_id=%s, status=%s",
                payment_id,
                status,
            )

            return PaymentResult(
                payment_id=payment_id,
                provider=self.provider_name,
                status=status,
                amount=amount,
                currency=currency.upper(),
                metadata={"user_id": user_id, "tariff_slug": tariff_slug},
                is_recurring=True,
                raw_data=data,
            )

        except PaymentError:
            raise
        except Exception as e:
            logger.exception("Stripe ошибка рекуррентного платежа")
            raise PaymentError(
                f"Ошибка рекуррентного платежа: {e}",
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
        """Вернуть средства за платёж.

        Args:
            payment_id: ID платежа (PaymentIntent ID: pi_...).
            amount: Сумма возврата в долларах (None = полный возврат).
            reason: Причина возврата.

        Returns:
            PaymentResult с результатом возврата.

        Raises:
            PaymentError: При ошибке возврата.
        """
        form_data: dict[str, str] = {
            "payment_intent": payment_id,
        }

        if amount is not None:
            amount_cents = int(amount * 100)
            form_data["amount"] = str(amount_cents)

        if reason:
            # Stripe поддерживает: duplicate, fraudulent, requested_by_customer
            form_data["reason"] = "requested_by_customer"
            form_data["metadata[reason_text]"] = reason

        try:
            client = await self._get_client()

            response = await client.post(
                "/refunds",
                data=form_data,
            )

            if response.status_code >= 400:
                error_data = response.json() if response.content else {}
                error_obj = error_data.get("error", {})
                error_msg = error_obj.get("message", response.text)
                raise PaymentError(
                    f"Ошибка возврата Stripe: {error_msg}",
                    provider=self.provider_name,
                )

            data = response.json()

            refund_status = data.get("status", "")
            refund_amount = Decimal(data.get("amount", 0)) / 100
            currency = data.get("currency", self.CURRENCY).upper()

            # Маппинг статусов возврата
            status = PaymentStatus.REFUNDED
            if refund_status == "failed":
                status = PaymentStatus.FAILED
            elif refund_status == "canceled":
                status = PaymentStatus.CANCELED

            logger.info(
                "Stripe refund: payment_id=%s, amount=%s, status=%s",
                payment_id,
                refund_amount,
                refund_status,
            )

            return PaymentResult(
                payment_id=payment_id,
                provider=self.provider_name,
                status=status,
                amount=refund_amount,
                currency=currency,
                raw_data=data,
            )

        except PaymentError:
            raise
        except Exception as e:
            logger.exception("Stripe ошибка возврата")
            raise PaymentError(
                f"Ошибка возврата: {e}",
                provider=self.provider_name,
                original_error=e,
            ) from e


def create_stripe_provider(
    secret_key: str,
    webhook_secret: str | None = None,
) -> StripeProvider:
    """Фабричная функция для создания провайдера Stripe.

    Args:
        secret_key: Секретный ключ API Stripe.
        webhook_secret: Секрет для проверки webhook (рекомендуется).

    Returns:
        Настроенный экземпляр StripeProvider.

    Example:
        from src.config.settings import settings

        provider = create_stripe_provider(
            secret_key=settings.payments.stripe.secret_key.get_secret_value(),
            webhook_secret=settings.payments.stripe.webhook_secret.get_secret_value()
                if settings.payments.stripe.webhook_secret else None,
        )
    """
    return StripeProvider(
        secret_key=secret_key,
        webhook_secret=webhook_secret,
    )
