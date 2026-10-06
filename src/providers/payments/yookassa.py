"""Провайдер платежей через YooKassa (ЮKassa).

YooKassa — российская платёжная система для приёма платежей в рублях.
Поддерживает: банковские карты, ЮMoney, SberPay, Tinkoff и др.

API документация: https://yookassa.ru/developers/api

Процесс оплаты:
1. Создаём платёж через POST /v3/payments
2. Получаем confirmation_url для редиректа пользователя
3. Пользователь оплачивает на странице YooKassa
4. YooKassa отправляет webhook на наш сервер
5. Обрабатываем webhook и начисляем токены

Настройка webhook в личном кабинете YooKassa:
1. Интеграция → HTTP-уведомления
2. URL: https://your-domain.com/api/webhooks/yookassa
3. События: payment.succeeded, payment.canceled
"""

import uuid
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

# Базовый URL API YooKassa
YOOKASSA_API_URL = "https://api.yookassa.ru/v3"

# Валюта YooKassa — рубли
YOOKASSA_CURRENCY = "RUB"

# Таймаут HTTP-запросов в секундах
HTTP_TIMEOUT = 30.0

# Маппинг статусов YooKassa → наши статусы
YOOKASSA_STATUS_MAP = {
    "pending": PaymentStatus.PENDING,
    "waiting_for_capture": PaymentStatus.PENDING,  # Холдирование
    "succeeded": PaymentStatus.SUCCEEDED,
    "canceled": PaymentStatus.CANCELED,
}


class YooKassaProvider(BasePaymentProvider):
    """Провайдер платежей через YooKassa.

    Использует YooKassa REST API для создания платежей.
    Аутентификация: HTTP Basic Auth (shop_id:secret_key).

    Attributes:
        _shop_id: ID магазина в YooKassa.
        _secret_key: Секретный ключ API.
        _client: HTTP-клиент для запросов к API.

    Example:
        provider = YooKassaProvider(
            shop_id="123456",
            secret_key="test_secret_key_123",
        )

        intent = await provider.create_payment(
            amount=Decimal("99.00"),
            currency="RUB",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Покупка 100 токенов",
            return_url="https://t.me/your_bot",
        )

        # intent.confirmation_url — URL для редиректа пользователя
    """

    PROVIDER_NAME = "yookassa"
    CURRENCY = YOOKASSA_CURRENCY

    def __init__(
        self,
        shop_id: str,
        secret_key: str,
        *,
        timeout: float = HTTP_TIMEOUT,
    ) -> None:
        """Создать провайдер YooKassa.

        Args:
            shop_id: ID магазина из личного кабинета YooKassa.
            secret_key: Секретный ключ API.
            timeout: Таймаут HTTP-запросов в секундах.
        """
        self._shop_id = shop_id
        self._secret_key = secret_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Получить или создать HTTP-клиент.

        Ленивая инициализация клиента для корректной работы с asyncio.

        Returns:
            Настроенный HTTP-клиент.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=YOOKASSA_API_URL,
                auth=(self._shop_id, self._secret_key),
                timeout=self._timeout,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Закрыть HTTP-клиент.

        Вызовите при завершении работы приложения.
        """
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
        """Основная валюта провайдера (RUB)."""
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
        """Создать платёж через YooKassa API.

        Отправляет POST-запрос к /v3/payments и возвращает
        URL для редиректа пользователя на страницу оплаты.

        Args:
            amount: Сумма платежа в рублях.
            currency: Код валюты (должен быть "RUB").
            user_id: Telegram ID пользователя.
            tariff_slug: Slug тарифа из config.yaml.
            description: Описание платежа для пользователя.
            return_url: URL для возврата после оплаты.
                Если не указан — используется стандартная страница.
            save_payment_method: Сохранить карту для автоплатежей.
            **kwargs: Дополнительные параметры (игнорируются).

        Returns:
            PaymentIntent с confirmation_url для редиректа.

        Raises:
            PaymentError: При ошибке API.
        """
        # Валидируем валюту
        if currency.upper() != self.CURRENCY:
            raise PaymentError(
                f"YooKassa поддерживает только RUB, получено: {currency}",
                provider=self.provider_name,
            )

        # Генерируем идемпотентный ключ
        # Это позволяет безопасно повторять запросы при сетевых ошибках
        idempotence_key = str(uuid.uuid4())

        # Формируем тело запроса
        # Документация: https://yookassa.ru/developers/api#create_payment
        request_body: dict[str, Any] = {
            "amount": {
                "value": str(amount),
                "currency": currency.upper(),
            },
            "capture": True,  # Автоматический захват платежа
            "description": description,
            "metadata": {
                "user_id": str(user_id),
                "tariff_slug": tariff_slug,
            },
        }

        # Добавляем return_url если указан
        if return_url:
            request_body["confirmation"] = {
                "type": "redirect",
                "return_url": return_url,
            }
        else:
            # Без return_url — просто редирект
            request_body["confirmation"] = {
                "type": "redirect",
                "return_url": "https://t.me",  # Fallback на Telegram
            }

        # Сохранение платёжного метода для рекуррентов
        if save_payment_method:
            request_body["save_payment_method"] = True

        try:
            client = await self._get_client()

            logger.debug(
                "YooKassa create_payment: amount=%s %s, user_id=%s, tariff=%s",
                amount,
                currency,
                user_id,
                tariff_slug,
            )

            response = await client.post(
                "/payments",
                json=request_body,
                headers={"Idempotence-Key": idempotence_key},
            )

            # Проверяем статус ответа
            if response.status_code >= 400:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get("description", response.text)
                raise PaymentError(
                    f"Ошибка YooKassa API: {error_msg}",
                    provider=self.provider_name,
                    is_retryable=response.status_code >= 500,
                )

            data = response.json()

            # Извлекаем данные платежа
            payment_id = data.get("id", "")
            status = data.get("status", "pending")
            confirmation = data.get("confirmation", {})
            confirmation_url = confirmation.get("confirmation_url")

            # Время истечения платежа
            expires_at = None
            if "expires_at" in data:
                expires_at = datetime.fromisoformat(data["expires_at"])

            logger.info(
                "YooKassa платёж создан: payment_id=%s, status=%s",
                payment_id,
                status,
            )

            return PaymentIntent(
                payment_id=payment_id,
                provider=self.provider_name,
                amount=amount,
                currency=currency.upper(),
                status=YOOKASSA_STATUS_MAP.get(status, PaymentStatus.PENDING),
                confirmation_url=confirmation_url,
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
            logger.warning("YooKassa таймаут: %s", e)
            raise PaymentError(
                "Таймаут запроса к YooKassa",
                provider=self.provider_name,
                is_retryable=True,
                original_error=e,
            ) from e
        except httpx.HTTPError as e:
            logger.exception("YooKassa HTTP ошибка")
            raise PaymentError(
                f"Ошибка HTTP: {e}",
                provider=self.provider_name,
                is_retryable=True,
                original_error=e,
            ) from e
        except Exception as e:
            logger.exception("YooKassa неожиданная ошибка")
            raise PaymentError(
                f"Неожиданная ошибка: {e}",
                provider=self.provider_name,
                original_error=e,
            ) from e

    @override
    async def verify_webhook(self, payload: bytes, signature: str) -> bool:
        """Проверить подлинность webhook от YooKassa.

        ВАЖНО: YooKassa НЕ использует HMAC-подпись для webhook'ов.
        Официальный способ проверки — по IP-адресу отправителя.
        Доверенные IP: 185.71.76.0/27, 185.71.77.0/27, 77.75.153.0/25,
        77.75.154.128/25, 77.75.156.11, 77.75.156.35

        Для дополнительной безопасности рекомендуется:
        1. Настроить IP-whitelist на уровне firewall/nginx
        2. Проверять статус платежа через API после получения webhook

        Документация: https://yookassa.ru/developers/using-api/webhooks

        Args:
            payload: Сырое тело HTTP-запроса (не используется).
            signature: Значение заголовка (не используется YooKassa).

        Returns:
            True — проверка подписи не требуется для YooKassa.
        """
        # YooKassa не использует криптографическую подпись webhook'ов.
        # Подлинность проверяется по IP-адресу на уровне инфраструктуры.
        return True

    @override
    async def process_webhook(self, data: dict[str, Any]) -> PaymentResult:
        """Обработать webhook от YooKassa.

        Формат webhook (payment.succeeded):
        {
            "type": "notification",
            "event": "payment.succeeded",
            "object": {
                "id": "payment_id",
                "status": "succeeded",
                "amount": {"value": "99.00", "currency": "RUB"},
                "metadata": {"user_id": "123", "tariff_slug": "tokens_100"},
                "payment_method": {"id": "pm_id", ...}
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
            event = data.get("event", "")
            payment_obj = data.get("object", {})

            # Извлекаем данные платежа
            payment_id = payment_obj.get("id", "")
            if not payment_id:
                raise PaymentError(
                    "Отсутствует payment_id в webhook",
                    provider=self.provider_name,
                )

            status_str = payment_obj.get("status", "")
            status = YOOKASSA_STATUS_MAP.get(status_str, PaymentStatus.PENDING)

            # Сумма платежа
            amount_obj = payment_obj.get("amount", {})
            amount = Decimal(amount_obj.get("value", "0"))
            currency = amount_obj.get("currency", self.CURRENCY)

            # Метаданные
            metadata = payment_obj.get("metadata", {})

            # ID платёжного метода (для рекуррентов)
            payment_method = payment_obj.get("payment_method", {})
            payment_method_id = payment_method.get("id")

            # Сообщение об ошибке (для canceled)
            error_message = None
            if status == PaymentStatus.CANCELED:
                cancellation = payment_obj.get("cancellation_details", {})
                error_message = cancellation.get("reason", "Платёж отменён")

            logger.info(
                "YooKassa webhook: event=%s, payment_id=%s, status=%s",
                event,
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
            logger.exception("YooKassa ошибка обработки webhook")
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

        Используется для автоматических списаний (рекуррентные платежи).
        Требует предварительного сохранения карты (save_payment_method=True).

        Args:
            payment_method_id: ID сохранённого метода оплаты.
            amount: Сумма списания.
            currency: Код валюты (RUB).
            user_id: Telegram ID пользователя.
            tariff_slug: Slug тарифа.
            description: Описание списания.

        Returns:
            PaymentResult с результатом списания.

        Raises:
            PaymentError: При ошибке списания.
        """
        idempotence_key = str(uuid.uuid4())

        request_body: dict[str, Any] = {
            "amount": {
                "value": str(amount),
                "currency": currency.upper(),
            },
            "capture": True,
            "description": description,
            "payment_method_id": payment_method_id,
            "metadata": {
                "user_id": str(user_id),
                "tariff_slug": tariff_slug,
                "is_recurring": "true",
            },
        }

        try:
            client = await self._get_client()

            response = await client.post(
                "/payments",
                json=request_body,
                headers={"Idempotence-Key": idempotence_key},
            )

            if response.status_code >= 400:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get("description", response.text)
                raise PaymentError(
                    f"Ошибка списания YooKassa: {error_msg}",
                    provider=self.provider_name,
                )

            data = response.json()

            payment_id = data.get("id", "")
            status_str = data.get("status", "")
            status = YOOKASSA_STATUS_MAP.get(status_str, PaymentStatus.PENDING)

            logger.info(
                "YooKassa recurring payment: payment_id=%s, status=%s",
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
            logger.exception("YooKassa ошибка рекуррентного платежа")
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
            payment_id: ID платежа для возврата.
            amount: Сумма возврата (None = полный возврат).
            reason: Причина возврата.

        Returns:
            PaymentResult с результатом возврата.

        Raises:
            PaymentError: При ошибке возврата.
        """
        idempotence_key = str(uuid.uuid4())

        # Сначала получаем информацию о платеже
        try:
            client = await self._get_client()

            # Получаем платёж для определения суммы
            payment_response = await client.get(f"/payments/{payment_id}")
            if payment_response.status_code >= 400:
                raise PaymentError(
                    f"Платёж не найден: {payment_id}",
                    provider=self.provider_name,
                )

            payment_data = payment_response.json()
            payment_amount = payment_data.get("amount", {})

            # Если сумма не указана — возвращаем полную
            refund_amount = amount
            if refund_amount is None:
                refund_amount = Decimal(payment_amount.get("value", "0"))

            currency = payment_amount.get("currency", self.CURRENCY)

            # Создаём возврат
            request_body: dict[str, Any] = {
                "payment_id": payment_id,
                "amount": {
                    "value": str(refund_amount),
                    "currency": currency,
                },
            }

            if reason:
                request_body["description"] = reason

            response = await client.post(
                "/refunds",
                json=request_body,
                headers={"Idempotence-Key": idempotence_key},
            )

            if response.status_code >= 400:
                error_data = response.json() if response.content else {}
                error_msg = error_data.get("description", response.text)
                raise PaymentError(
                    f"Ошибка возврата YooKassa: {error_msg}",
                    provider=self.provider_name,
                )

            data = response.json()
            refund_status = data.get("status", "")

            # Маппинг статусов возврата
            status = PaymentStatus.REFUNDED
            if refund_status == "canceled":
                status = PaymentStatus.CANCELED

            logger.info(
                "YooKassa refund: payment_id=%s, amount=%s, status=%s",
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
            logger.exception("YooKassa ошибка возврата")
            raise PaymentError(
                f"Ошибка возврата: {e}",
                provider=self.provider_name,
                original_error=e,
            ) from e


def create_yookassa_provider(
    shop_id: str,
    secret_key: str,
) -> YooKassaProvider:
    """Фабричная функция для создания провайдера YooKassa.

    Args:
        shop_id: ID магазина YooKassa.
        secret_key: Секретный ключ API.

    Returns:
        Настроенный экземпляр YooKassaProvider.

    Example:
        from src.config.settings import settings

        provider = create_yookassa_provider(
            shop_id=settings.payments.yookassa.shop_id,
            secret_key=settings.payments.yookassa.secret_key.get_secret_value(),
        )
    """
    return YooKassaProvider(
        shop_id=shop_id,
        secret_key=secret_key,
    )
