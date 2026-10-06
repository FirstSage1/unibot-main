"""Модель подписки.

Хранит информацию о подписках пользователей на тарифные планы.
Подписки дают пользователю определённое количество токенов на период (месяц).

Поддерживаемые провайдеры:
- Telegram Stars — нативные подписки, Telegram сам управляет продлением
- YooKassa — рекуррентные платежи через сохранённую карту
- Stripe — рекуррентные платежи через SetupIntent

Жизненный цикл подписки:
1. PENDING — создана, ожидает первого платежа
2. ACTIVE — активна, пользователь может использовать токены
3. PAST_DUE — просрочена, попытка автопродления не удалась (retry)
4. CANCELED — отменена пользователем, но активна до конца периода
5. EXPIRED — истекла и не была продлена
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing_extensions import override

from src.db.models_base import Base

if TYPE_CHECKING:
    from src.db.models.payment import Payment
    from src.db.models.user import User


class SubscriptionStatus(StrEnum):
    """Статус подписки.

    Значения:
        PENDING: Подписка создана, ожидает первого платежа.
            Обычно это промежуточное состояние при создании платежа.
        ACTIVE: Подписка активна, пользователь может использовать токены.
            Токены подписки списываются в первую очередь (перед балансом).
        PAST_DUE: Просрочена — попытка автопродления не удалась.
            Система будет повторять попытки списания.
            Пользователь получает уведомления о проблеме с оплатой.
        CANCELED: Отменена пользователем.
            Подписка остаётся активной до конца оплаченного периода.
            После истечения переходит в EXPIRED.
        EXPIRED: Истекла и не была продлена.
            Неиспользованные токены либо сгорают, либо переносятся в баланс
            (зависит от burn_unused в тарифе).
    """

    PENDING = "pending"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"


class Subscription(Base):
    """Подписка пользователя на тарифный план.

    Подписка предоставляет пользователю определённое количество токенов
    на каждый период (обычно месяц). Токены подписки списываются
    в первую очередь, до основного баланса.

    Автопродление:
    - Telegram Stars: Telegram сам управляет продлением, мы получаем webhook
    - YooKassa/Stripe: APScheduler проверяет истекающие подписки и списывает
      со сохранённой карты (payment_method_id)

    Отмена подписки:
    - Пользователь может отменить подписку в /settings
    - Подписка остаётся активной до конца оплаченного периода
    - После истечения переходит в EXPIRED

    Перенос токенов:
    - Если burn_unused=true в тарифе — неиспользованные токены сгорают
    - Если burn_unused=false — переносятся в основной баланс

    Attributes:
        id: Внутренний ID подписки (автоинкремент).
        user_id: ID пользователя (FK → users.id).
        tariff_slug: Slug тарифа из config.yaml (например, "pro_monthly").
        provider: Платёжный провайдер (yookassa, stripe, telegram_stars).
        status: Текущий статус подписки.
        tokens_per_period: Количество токенов, начисляемых каждый период.
            Фиксируется при создании на случай изменения тарифа.
        tokens_remaining: Оставшиеся токены в текущем периоде.
            Списываются при генерациях до достижения 0.
        period_start: Начало текущего периода (дата активации или продления).
        period_end: Конец текущего периода (когда нужно продлить).
        auto_renewal: Включено ли автоматическое продление.
            False после отмены пользователем.
        cancel_at_period_end: True если пользователь отменил подписку.
            Подписка будет активна до period_end, затем станет EXPIRED.
        payment_method_id: ID сохранённого метода оплаты для автопродления.
            YooKassa: ID платёжного метода
            Stripe: pm_... (PaymentMethod ID)
            Telegram Stars: telegram_subscription_id
        original_payment_id: ID первого платежа, который создал подписку.
        last_renewal_payment_id: ID последнего платежа за продление.
            None для первого периода.
        renewal_attempts: Количество неудачных попыток продления.
            Сбрасывается после успешного платежа.
        last_renewal_attempt_at: Время последней попытки продления.
            Используется для retry-логики.
        metadata_json: JSON с дополнительными данными.
            Telegram Stars: subscription_id от Telegram
            Причина отмены, история изменений и т.д.
        created_at: Время создания записи в БД.
        updated_at: Время последнего обновления.
        user: Связь с моделью User.
        original_payment: Связь с первым платежом.
        last_renewal_payment: Связь с последним платежом за продление.

    Индексы:
        - user_id + status — активные подписки пользователя
        - status + period_end — для планировщика (истекающие подписки)
        - tariff_slug — аналитика по тарифам
    """

    __tablename__ = "subscriptions"

    # Первичный ключ
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ID пользователя из таблицы users
    # BigInteger для совместимости с большими telegram_id
    # ondelete="CASCADE" — при удалении пользователя удаляются все его подписки
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Slug тарифа из config.yaml
    # Например: "basic_monthly", "pro_monthly", "premium_monthly"
    tariff_slug: Mapped[str] = mapped_column(String(100), nullable=False)

    # Платёжный провайдер
    # Определяет способ автопродления:
    # - telegram_stars: нативные подписки Telegram
    # - yookassa/stripe: списание со сохранённой карты
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    # Текущий статус подписки
    # Используем Enum для гарантии валидности значений на уровне БД
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False, length=50),
        default=SubscriptionStatus.PENDING,
        nullable=False,
    )

    # Количество токенов на период
    # Фиксируется при создании подписки, чтобы изменения тарифа
    # не влияли на существующие подписки
    tokens_per_period: Mapped[int] = mapped_column(nullable=False)

    # Оставшиеся токены в текущем периоде
    # При продлении: если burn_unused=false, остаток добавляется к новым токенам
    tokens_remaining: Mapped[int] = mapped_column(default=0, nullable=False)

    # Границы текущего периода
    # period_start — когда начался текущий оплаченный период
    # period_end — когда заканчивается и нужно продлить
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Флаги автопродления
    # auto_renewal=True — подписка будет автоматически продлеваться
    # cancel_at_period_end=True — не продлевать после period_end
    auto_renewal: Mapped[bool] = mapped_column(default=True, nullable=False)
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False, nullable=False)

    # ID сохранённого метода оплаты для автопродления
    # YooKassa: ID из response.payment_method.id
    # Stripe: pm_... из SetupIntent
    # Telegram Stars: telegram_subscription_id (Telegram сам управляет)
    payment_method_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ID первого платежа, который создал подписку
    # Используется для отслеживания истории
    original_payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ID последнего платежа за продление
    # None для первого периода, затем обновляется при каждом продлении
    last_renewal_payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Счётчик неудачных попыток продления
    # Используется для retry-логики с exponential backoff
    # После 3-5 неудачных попыток подписка переходит в EXPIRED
    renewal_attempts: Mapped[int] = mapped_column(default=0, nullable=False)

    # Время последней попытки продления
    # Используется для расчёта времени следующей попытки
    last_renewal_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    # JSON с дополнительными данными
    # - telegram_subscription_id: ID подписки в Telegram Stars
    # - cancellation_reason: причина отмены
    # - renewal_history: история продлений
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # Связи с другими таблицами
    # Связь с пользователем
    user: Mapped["User"] = relationship(
        back_populates="subscriptions",
        lazy="selectin",
    )

    # Связь с первым платежом
    original_payment: Mapped["Payment | None"] = relationship(
        foreign_keys=[original_payment_id],
        lazy="selectin",
    )

    # Связь с последним платежом за продление
    last_renewal_payment: Mapped["Payment | None"] = relationship(
        foreign_keys=[last_renewal_payment_id],
        lazy="selectin",
    )

    # Индексы для оптимизации запросов
    __table_args__ = (
        # Индекс для поиска активных подписок пользователя
        # Используется в billing_service для проверки подписки
        Index("ix_subscriptions_user_status", "user_id", "status"),
        # Индекс для планировщика — поиск истекающих подписок
        # Условие: status IN (active, past_due) AND period_end < ?
        Index("ix_subscriptions_status_period_end", "status", "period_end"),
        # Индекс для аналитики по тарифам
        Index("ix_subscriptions_tariff", "tariff_slug"),
    )

    @property
    def is_active(self) -> bool:
        """Проверяет, активна ли подписка.

        Подписка считается активной, если:
        - Статус ACTIVE или PAST_DUE (даём время на повторную оплату)
        - Текущее время меньше period_end

        Returns:
            True если подписка активна и можно использовать токены.
        """
        return self.status in (
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.PAST_DUE,
        )

    @property
    def can_be_renewed(self) -> bool:
        """Проверяет, можно ли продлить подписку.

        Returns:
            True если подписка может быть продлена автоматически.
        """
        return (
            self.auto_renewal
            and not self.cancel_at_period_end
            and self.status
            in (
                SubscriptionStatus.ACTIVE,
                SubscriptionStatus.PAST_DUE,
            )
        )

    @override
    def __repr__(self) -> str:
        """Строковое представление для отладки."""
        return (
            f"<Subscription(id={self.id}, user_id={self.user_id}, "
            f"tariff={self.tariff_slug}, status={self.status}, "
            f"tokens_remaining={self.tokens_remaining})>"
        )
