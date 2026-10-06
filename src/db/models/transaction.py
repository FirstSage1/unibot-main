"""Модель транзакции (леджер баланса токенов).

Транзакция — это запись о любом изменении баланса пользователя:
- Начисление бонуса при регистрации
- Списание токенов за генерацию
- Покупка токенов
- Возврат средств
- Ручная корректировка администратором

Почему леджер (журнал транзакций):
1. Аудит — можно отследить историю всех операций
2. Отладка — легко понять, откуда взялся текущий баланс
3. Споры — доказательство всех начислений и списаний
4. Аналитика — статистика по типам операций

Важно: баланс пользователя хранится и в User.balance (для быстрого доступа),
и вычисляется как сумма всех транзакций (для проверки целостности).
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing_extensions import override

from src.db.base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class TransactionType(StrEnum):
    """Тип транзакции — определяет причину изменения баланса.

    Используется для:
    - Фильтрации истории транзакций
    - Аналитики и отчётности

    Значения:
        REGISTRATION_BONUS: Бонусные токены при первом /start.
            Сумма задаётся в config.yaml → billing.registration_bonus.
        GENERATION: Списание токенов за генерацию.
            Сумма зависит от модели (config.yaml → models.*.price_tokens).
        PURCHASE: Покупка токенов (через платёжную систему).
        REFUND: Возврат токенов (при ошибке генерации или по запросу).
        ADMIN_ADJUSTMENT: Ручная корректировка баланса администратором.
        REFERRAL_BONUS: Бонус за приглашение друга (реферальная программа).
            Начисляется пригласившему и приглашённому.
        SUBSCRIPTION_TRANSFER: Перенос неиспользованных токенов подписки в баланс.
            Происходит при истечении подписки, если burn_unused=false в тарифе.
    """

    REGISTRATION_BONUS = "registration_bonus"
    GENERATION = "generation"
    PURCHASE = "purchase"
    REFUND = "refund"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    REFERRAL_BONUS = "referral_bonus"
    SUBSCRIPTION_TRANSFER = "subscription_transfer"


class Transaction(Base):
    """Транзакция — запись об изменении баланса пользователя.

    Каждая операция с балансом создаёт новую транзакцию.
    Баланс пользователя = сумма всех его транзакций.

    Паттерн "леджер" (бухгалтерский журнал):
    - Никогда не изменяем и не удаляем транзакции
    - Для отмены создаём обратную транзакцию (REFUND)
    - Текущий баланс всегда можно пересчитать из истории

    Attributes:
        id: Уникальный ID транзакции (автоинкремент).
        user_id: ID пользователя (FK → users.id).
        type: Тип транзакции (TransactionType).
        amount: Сумма операции. Положительное = начисление, отрицательное = списание.
        balance_after: Баланс пользователя ПОСЛЕ этой транзакции.
            Хранится для быстрого доступа без пересчёта всей истории.
        description: Человекочитаемое описание для истории.
            Например: "Генерация изображения (DALL-E 3)"
        metadata_json: JSON с дополнительными данными (model_key, generation_id и т.д.).
            Используется для детальной аналитики и отладки.
        created_at: Дата и время создания транзакции.
        user: Связь с моделью User.

    Примеры транзакций:
        # Бонус при регистрации (+100 токенов)
        Transaction(type=REGISTRATION_BONUS, amount=100, balance_after=100)

        # Генерация (-15 токенов)
        Transaction(type=GENERATION, amount=-15, balance_after=85)
    """

    __tablename__ = "transactions"

    # Первичный ключ
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ID пользователя из таблицы users
    # BigInteger для совместимости с большими telegram_id
    # ondelete="CASCADE" — при удалении пользователя удаляются все его транзакции
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Тип транзакции — определяет причину изменения баланса
    # String(50) — достаточно для всех значений TransactionType
    type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Сумма операции (положительная = начисление, отрицательная = списание)
    amount: Mapped[int] = mapped_column(nullable=False)

    # Баланс ПОСЛЕ транзакции — для быстрого доступа к текущему балансу
    # Позволяет показать баланс в истории без пересчёта всех предыдущих транзакций
    balance_after: Mapped[int] = mapped_column(nullable=False)

    # Описание для отображения пользователю
    # Например: "Бонус за регистрацию", "Генерация: GPT-4o"
    description: Mapped[str] = mapped_column(String(255), nullable=False)

    # JSON с дополнительными данными
    # Примеры содержимого:
    #   {"model_key": "gpt-4o", "generation_type": "chat"}
    #   {"model_key": "dall-e-3", "generation_type": "image", "prompt": "..."}
    # Text вместо JSON потому что SQLite не поддерживает нативный JSON-тип
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Дата создания транзакции
    # func.now() — текущее время на стороне БД
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    # Связь с пользователем
    user: Mapped["User"] = relationship(
        back_populates="transactions",
        lazy="selectin",
    )

    # Индексы для оптимизации запросов
    __table_args__ = (
        # Индекс для получения истории транзакций пользователя с сортировкой по дате
        # Используется в /history (пагинация по дате)
        Index("ix_transactions_user_created", "user_id", "created_at"),
        # Индекс для подсчёта транзакций по типу
        # Используется для аналитики (COUNT WHERE type=...)
        Index("ix_transactions_user_type", "user_id", "type"),
    )

    @override
    def __repr__(self) -> str:
        """Строковое представление для отладки."""
        return (
            f"<Transaction(id={self.id}, user_id={self.user_id}, "
            f"type={self.type}, amount={self.amount}, "
            f"balance_after={self.balance_after})>"
        )
