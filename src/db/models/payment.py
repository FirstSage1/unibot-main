"""Модель платежа.

Хранит информацию о платежах пользователей через различные провайдеры:
- YooKassa (RUB)
- Stripe (USD)
- Telegram Stars (XTR)

Каждый платёж связан с пользователем и тарифом (покупка токенов или подписка).
История платежей используется для:
- Аудита и отчётности
- Обработки webhook'ов (идемпотентность)
- Разрешения споров с пользователями
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing_extensions import override

from src.db.models_base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class PaymentStatus(StrEnum):
    """Статус платежа в нашей системе.

    Значения:
        PENDING: Платёж создан, ожидает оплаты.
            Пользователь перенаправлен на страницу оплаты (YooKassa/Stripe)
            или получил invoice (Telegram Stars).
        SUCCEEDED: Платёж успешно завершён.
            Токены начислены пользователю.
        FAILED: Платёж не удался.
            Причина: отклонён банком, отменён пользователем, таймаут.
        REFUNDED: Выполнен возврат средств.
            Токены списаны с баланса пользователя (если были начислены).
        CANCELED: Платёж отменён.
            Например, пользователь закрыл страницу оплаты до завершения.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"
    CANCELED = "canceled"


class PaymentProvider(StrEnum):
    """Платёжный провайдер.

    Значения:
        YOOKASSA: ЮKassa — для платежей в рублях (Россия).
        STRIPE: Stripe — для международных платежей в USD.
        TELEGRAM_STARS: Telegram Stars — встроенная валюта Telegram.
    """

    YOOKASSA = "yookassa"
    STRIPE = "stripe"
    TELEGRAM_STARS = "telegram_stars"


class Payment(Base):
    """Платёж пользователя.

    Создаётся при инициации платежа, обновляется при получении webhook.
    Хранит полную историю платежей для аудита и аналитики.

    Attributes:
        id: Внутренний ID платежа (автоинкремент).
        user_id: ID пользователя (FK → users.id).
        provider: Платёжный провайдер (yookassa, stripe, telegram_stars).
        provider_payment_id: ID платежа на стороне провайдера.
            YooKassa: UUID платежа
            Stripe: PaymentIntent ID (pi_...)
            Telegram Stars: telegram_payment_charge_id
        status: Текущий статус платежа.
        amount: Сумма платежа в минимальных единицах валюты.
            RUB: копейки (100 = 1₽)
            USD: центы (100 = $1)
            XTR: Stars (целые числа)
        currency: Код валюты (RUB, USD, XTR).
        tariff_slug: Slug тарифа из config.yaml (например, "tokens_100").
        tokens_amount: Количество токенов, которые будут начислены.
        description: Описание платежа для пользователя.
        payment_method_id: ID сохранённого метода оплаты (для рекуррентов).
        metadata_json: JSON с дополнительными данными (payload, raw webhook).
        is_recurring: True если это автопродление подписки.
        created_at: Время создания платежа в нашей системе.
        updated_at: Время последнего обновления (при получении webhook).
        completed_at: Время успешного завершения платежа.
        user: Связь с моделью User.

    Индексы:
        - provider + provider_payment_id — для идемпотентности (не обрабатывать дважды)
        - user_id + created_at — для истории платежей пользователя
        - status + created_at — для аналитики и мониторинга
    """

    __tablename__ = "payments"

    # Первичный ключ
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ID пользователя из таблицы users
    # BigInteger для совместимости с большими telegram_id
    # ondelete="CASCADE" — при удалении пользователя удаляются все его платежи
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Платёжный провайдер
    # String(50) — достаточно для всех значений PaymentProvider
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    # ID платежа на стороне провайдера
    # Используется для:
    # - Идемпотентности (не обрабатывать один webhook дважды)
    # - Связи с провайдером при возвратах и спорах
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Статус платежа
    status: Mapped[str] = mapped_column(
        String(50),
        default=PaymentStatus.PENDING,
        nullable=False,
    )

    # Сумма платежа в минимальных единицах валюты
    # Numeric(12, 2) — до 12 цифр всего, 2 после запятой
    # Примеры: 9900 (99.00₽), 599 ($5.99), 50 (50 Stars)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    # Код валюты (RUB, USD, XTR)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)

    # Slug тарифа из config.yaml
    # Например: "tokens_100", "pro_monthly"
    tariff_slug: Mapped[str] = mapped_column(String(100), nullable=False)

    # Количество токенов для начисления
    # Берётся из тарифа, но сохраняется на случай изменения тарифа
    tokens_amount: Mapped[int] = mapped_column(nullable=False)

    # Описание платежа для пользователя
    # Например: "Покупка 100 токенов"
    description: Mapped[str] = mapped_column(String(500), nullable=False)

    # ID сохранённого метода оплаты (для рекуррентных платежей)
    # Stripe: pm_... (PaymentMethod ID)
    # YooKassa: ID платёжного метода
    # Telegram Stars: None (Telegram сам управляет подписками)
    payment_method_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # JSON с дополнительными данными
    # Содержит: payload, raw webhook data, error details и т.д.
    # Text вместо JSON потому что SQLite не поддерживает нативный JSON-тип
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Флаг рекуррентного платежа
    # True для автопродлений подписок Telegram Stars
    is_recurring: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Временные метки
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        onupdate=func.now(),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Связь с пользователем
    user: Mapped["User"] = relationship(
        back_populates="payments",
        lazy="selectin",
    )

    # Индексы для оптимизации запросов
    __table_args__ = (
        # Уникальный индекс для идемпотентности
        # Не обрабатываем один и тот же webhook дважды
        Index(
            "ix_payments_provider_payment_id",
            "provider",
            "provider_payment_id",
            unique=True,
        ),
        # Индекс для истории платежей пользователя
        Index("ix_payments_user_created", "user_id", "created_at"),
        # Индекс для аналитики по статусам
        Index("ix_payments_status_created", "status", "created_at"),
    )

    @override
    def __repr__(self) -> str:
        """Строковое представление для отладки."""
        return (
            f"<Payment(id={self.id}, user_id={self.user_id}, "
            f"provider={self.provider}, status={self.status}, "
            f"amount={self.amount} {self.currency})>"
        )
