"""Базовый адаптер для платёжных провайдеров.

Этот модуль определяет абстрактный интерфейс, который должны реализовать
все платёжные провайдеры. Это позволяет:
- Единообразно работать с разными провайдерами (YooKassa, Stripe, Telegram Stars)
- Легко добавлять новые провайдеры без изменения бизнес-логики
- Тестировать логику платежей с mock-провайдерами

Паттерн: Adapter (GoF) + Strategy
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class PaymentStatus(StrEnum):
    """Статус платежа.

    Значения:
        PENDING: Платёж создан, ожидает оплаты пользователем.
            Для YooKassa/Stripe — пользователь перенаправлен на страницу оплаты.
            Для Telegram Stars — отправлен invoice, ждём successful_payment.
        SUCCEEDED: Платёж успешно завершён, деньги получены.
            Токены должны быть начислены пользователю.
        FAILED: Платёж не удался (отклонён банком, отменён пользователем).
            Токены не начисляются.
        REFUNDED: Средства возвращены пользователю.
            Если токены были начислены — нужно списать их обратно.
        CANCELED: Платёж отменён до завершения (таймаут, отмена ботом).
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELED = "canceled"


@dataclass
class PaymentIntent:
    """Намерение платежа — созданный, но ещё не оплаченный платёж.

    Возвращается методом create_payment() после создания платежа на стороне провайдера.
    Содержит информацию, необходимую для показа пользователю
    (ссылка на оплату или invoice).

    Attributes:
        payment_id: Уникальный ID платежа на стороне провайдера.
            YooKassa: payment.id (UUID)
            Stripe: pi_... (PaymentIntent ID)
            Telegram Stars: None (invoice отправляется напрямую)
        provider: Название провайдера (yookassa, stripe, telegram_stars).
        amount: Сумма платежа в валюте провайдера.
        currency: Код валюты (RUB, USD, XTR).
        status: Текущий статус платежа.
        confirmation_url: URL для редиректа пользователя на страницу оплаты.
            YooKassa/Stripe: ссылка на платёжную форму.
            Telegram Stars: None (используется send_invoice).
        metadata: Дополнительные данные платежа (user_id, tariff_slug и т.д.).
        created_at: Время создания платежа.
        expires_at: Время истечения платежа (после которого нельзя оплатить).
    """

    payment_id: str | None
    provider: str
    amount: Decimal
    currency: str
    status: PaymentStatus = PaymentStatus.PENDING
    confirmation_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    expires_at: datetime | None = None


@dataclass
class PaymentResult:
    """Результат обработки webhook от платёжного провайдера.

    Возвращается методом process_webhook() после верификации и парсинга webhook-данных.
    Содержит информацию для обновления статуса платежа в нашей БД.

    Attributes:
        payment_id: ID платежа на стороне провайдера.
        provider: Название провайдера.
        status: Новый статус платежа.
        amount: Сумма платежа (может отличаться от исходной при частичной оплате).
        currency: Код валюты.
        metadata: Метаданные платежа (включая user_id, tariff_slug).
        payment_method_id: ID сохранённого метода оплаты для рекуррентов.
            Stripe: pm_... (PaymentMethod ID)
            YooKassa: ID платёжного метода
            Telegram Stars: None
        is_recurring: True если это автопродление подписки (Telegram Stars).
        subscription_expiration_date: Дата истечения подписки (Telegram Stars).
        error_message: Сообщение об ошибке (если status == FAILED).
        raw_data: Сырые данные webhook для отладки.
    """

    payment_id: str
    provider: str
    status: PaymentStatus
    amount: Decimal | None = None
    currency: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    payment_method_id: str | None = None
    is_recurring: bool = False
    subscription_expiration_date: datetime | None = None
    error_message: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        """Проверить, успешен ли платёж."""
        return self.status == PaymentStatus.SUCCEEDED

    @property
    def user_id(self) -> int | None:
        """Получить ID пользователя из метаданных."""
        user_id = self.metadata.get("user_id")
        if user_id is not None:
            return int(user_id)
        return None

    @property
    def tariff_slug(self) -> str | None:
        """Получить slug тарифа из метаданных."""
        return self.metadata.get("tariff_slug")


class BasePaymentProvider(ABC):
    """Абстрактный базовый класс для платёжных провайдеров.

    Определяет интерфейс, который должны реализовать все провайдеры.
    Это позволяет PaymentService работать с любым провайдером единообразно.

    Для добавления нового провайдера:
    1. Создайте класс, наследующий BasePaymentProvider
    2. Реализуйте все абстрактные методы
    3. Зарегистрируйте провайдер в конфигурации
    4. Добавьте webhook-эндпоинт в FastAPI

    Пример:
        class MyPaymentProvider(BasePaymentProvider):
            @property
            def provider_name(self) -> str:
                return "my_provider"

            async def create_payment(self, amount, currency, user_id, **kwargs):
                # Создаём платёж через API провайдера
                response = await self._client.create_payment(...)
                return PaymentIntent(
                    payment_id=response.id,
                    provider=self.provider_name,
                    amount=amount,
                    currency=currency,
                    confirmation_url=response.confirmation_url,
                )

            async def verify_webhook(self, payload, signature):
                # Проверяем подпись webhook
                return self._client.verify_signature(payload, signature)

            async def process_webhook(self, data):
                # Обрабатываем данные webhook
                return PaymentResult(...)
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Название провайдера (для логов и идентификации).

        Должно быть уникальным и понятным.
        Примеры: "yookassa", "stripe", "telegram_stars".
        """

    @property
    @abstractmethod
    def currency(self) -> str:
        """Основная валюта провайдера.

        YooKassa: "RUB"
        Stripe: "USD"
        Telegram Stars: "XTR"
        """

    @abstractmethod
    async def create_payment(  # noqa: PLR0913
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
        """Создать платёж.

        Основной метод для инициации платежа. Создаёт запись на стороне
        провайдера и возвращает информацию для показа пользователю.

        Args:
            amount: Сумма платежа в валюте провайдера.
            currency: Код валюты (RUB, USD, XTR).
            user_id: ID пользователя в нашей системе (Telegram ID).
            tariff_slug: Slug тарифа из config.yaml (например, "tokens_100").
            description: Описание платежа для пользователя.
            return_url: URL для возврата после оплаты (YooKassa, Stripe).
            save_payment_method: Сохранить метод оплаты для рекуррентов.
            **kwargs: Дополнительные параметры провайдера.

        Returns:
            PaymentIntent с информацией о созданном платеже.

        Raises:
            PaymentError: При ошибке создания платежа.
        """

    @abstractmethod
    async def verify_webhook(self, payload: bytes, signature: str) -> bool:
        """Проверить подпись webhook от провайдера.

        Важно для безопасности: без проверки подписи злоумышленник может
        отправить поддельный webhook и "начислить" себе токены.

        Args:
            payload: Сырое тело HTTP-запроса (bytes).
            signature: Значение заголовка с подписью.
                YooKassa: заголовок Signature
                Stripe: заголовок Stripe-Signature

        Returns:
            True если подпись валидна, False иначе.
        """

    @abstractmethod
    async def process_webhook(self, data: dict[str, Any]) -> PaymentResult:
        """Обработать webhook от провайдера.

        Вызывается ПОСЛЕ успешной верификации подписи.
        Парсит данные webhook и возвращает структурированный результат.

        Args:
            data: Распарсенный JSON из тела webhook.

        Returns:
            PaymentResult с информацией о статусе платежа.

        Raises:
            PaymentError: При ошибке обработки webhook.
        """

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
        """Списать с сохранённой карты (для рекуррентных платежей).

        Используется для автопродления подписок. Списывает деньги
        без участия пользователя, используя ранее сохранённый метод оплаты.

        По умолчанию выбрасывает NotImplementedError.
        Переопределите в провайдерах с поддержкой рекуррентов (Stripe, YooKassa).

        Args:
            payment_method_id: ID сохранённого метода оплаты.
            amount: Сумма списания.
            currency: Код валюты.
            user_id: ID пользователя.
            tariff_slug: Slug тарифа подписки.
            description: Описание списания.
            **kwargs: Дополнительные параметры.

        Returns:
            PaymentResult с результатом списания.

        Raises:
            NotImplementedError: Если провайдер не поддерживает рекурренты.
            PaymentError: При ошибке списания.
        """
        raise NotImplementedError(
            f"Провайдер {self.provider_name} не поддерживает рекуррентные платежи"
        )

    async def refund_payment(
        self,
        payment_id: str,
        amount: Decimal | None = None,
        *,
        reason: str | None = None,
    ) -> PaymentResult:
        """Вернуть средства за платёж.

        Выполняет полный или частичный возврат средств пользователю.

        По умолчанию выбрасывает NotImplementedError.
        Переопределите в провайдерах с поддержкой возвратов.

        Args:
            payment_id: ID платежа для возврата.
            amount: Сумма возврата (None = полный возврат).
            reason: Причина возврата.

        Returns:
            PaymentResult с результатом возврата.

        Raises:
            NotImplementedError: Если провайдер не поддерживает возвраты.
            PaymentError: При ошибке возврата.
        """
        raise NotImplementedError(
            f"Провайдер {self.provider_name} не поддерживает возвраты"
        )

    async def cancel_subscription(self, subscription_id: str) -> bool:
        """Отменить подписку.

        Используется для отмены автопродления подписки Telegram Stars.

        По умолчанию возвращает False (отмена не поддерживается).
        Переопределите в провайдерах с поддержкой подписок.

        Args:
            subscription_id: ID подписки для отмены.

        Returns:
            True если подписка успешно отменена, False иначе.
        """
        return False
