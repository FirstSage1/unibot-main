"""Модель реферала (приглашённого пользователя).

Хранит связь между пригласившим (inviter) и приглашённым (invitee).
Используется для:
- Отслеживания кто кого пригласил
- Расчёта заработка на рефералах
- Защиты от повторных бонусов

Паттерн использования:
1. Пользователь A получает реферальную ссылку t.me/bot?start=ref_A
2. Пользователь B переходит по ссылке и регистрируется
3. Создаётся запись Referral(inviter_id=A, invitee_id=B)
4. Обоим начисляются бонусы (если условия соблюдены)
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing_extensions import override

from src.db.models_base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class Referral(Base):
    """Реферал — запись о приглашении пользователя.

    Каждая запись связывает пригласившего (inviter) с приглашённым (invitee).
    Один пользователь может быть приглашён только один раз (invitee_id уникален).

    Атрибуты:
        id: Уникальный ID записи (автоинкремент).
        inviter_id: ID пригласившего пользователя (FK → users.id).
        invitee_id: ID приглашённого пользователя (FK → users.id).
            Уникальный — один пользователь может быть приглашён только раз.
        inviter_bonus_amount: Сколько токенов получил пригласивший за этого реферала.
            0 = бонус ещё не выплачен (например, ждём первую оплату).
        invitee_bonus_amount: Сколько токенов получил приглашённый.
        bonus_paid_at: Когда был выплачен бонус пригласившему.
            None = бонус ещё не выплачен (require_payment=true и нет оплаты).
        created_at: Дата создания реферала (когда приглашённый зарегистрировался).
        inviter: Связь с пользователем-пригласившим.
        invitee: Связь с пользователем-приглашённым.

    Пример:
        # Пользователь 1 пригласил пользователя 2
        referral = Referral(
            inviter_id=1,
            invitee_id=2,
            inviter_bonus_amount=50,
            invitee_bonus_amount=25,
        )
    """

    __tablename__ = "referrals"

    # Первичный ключ
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ID пригласившего пользователя
    # BigInteger для совместимости с большими ID пользователей
    # ondelete="CASCADE" — при удалении пользователя удаляются его рефералы
    inviter_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ID приглашённого пользователя
    # unique=True — один пользователь может быть приглашён только один раз
    # Это защита от повторных бонусов
    invitee_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Сколько токенов получил пригласивший за этого реферала
    # 0 = бонус ещё не выплачен (ждём первую оплату при require_payment=true)
    inviter_bonus_amount: Mapped[int] = mapped_column(default=0, nullable=False)

    # Сколько токенов получил приглашённый при регистрации
    invitee_bonus_amount: Mapped[int] = mapped_column(default=0, nullable=False)

    # Когда был выплачен бонус пригласившему
    # None = бонус ещё не выплачен (при require_payment=true)
    # Позволяет отследить, нужно ли ещё выплатить бонус
    bonus_paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Дата создания реферала (когда приглашённый зарегистрировался)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    # Связь с пригласившим пользователем
    # foreign_keys нужен, потому что у нас две FK на одну таблицу
    inviter: Mapped["User"] = relationship(
        "User",
        foreign_keys=[inviter_id],
        lazy="selectin",
    )

    # Связь с приглашённым пользователем
    invitee: Mapped["User"] = relationship(
        "User",
        foreign_keys=[invitee_id],
        lazy="selectin",
    )

    # Индексы для оптимизации запросов
    __table_args__ = (
        # Индекс для подсчёта рефералов пользователя и суммы заработка
        # Используется в /invite (статистика) и проверке max_earnings
        Index("ix_referrals_inviter_bonus", "inviter_id", "bonus_paid_at"),
        # Частичный индекс для невыплаченных бонусов (оптимизация count_pending_bonuses)
        # Используется в /invite для отображения количества ожидающих выплаты бонусов
        Index(
            "ix_referrals_unpaid_bonuses",
            "inviter_id",
            postgresql_where=text("bonus_paid_at IS NULL"),
        ),
    )

    @override
    def __repr__(self) -> str:
        """Строковое представление для отладки."""
        is_paid = self.bonus_paid_at is not None
        return (
            f"<Referral(id={self.id}, inviter_id={self.inviter_id}, "
            f"invitee_id={self.invitee_id}, bonus_paid={is_paid})>"
        )
