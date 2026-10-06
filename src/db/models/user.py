"""Модель пользователя.

Хранит информацию о пользователях Telegram, которые взаимодействовали с ботом.
Это основная сущность — все остальные данные (генерации, платежи, подписки)
связаны с пользователем.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing_extensions import override

from src.db.models_base import Base

if TYPE_CHECKING:
    from src.db.models.generation import Generation
    from src.db.models.message import Message
    from src.db.models.payment import Payment
    from src.db.models.subscription import Subscription
    from src.db.models.transaction import Transaction


class User(Base):
    """Пользователь бота.

    Создаётся при первом /start. Хранит:
    - Данные из Telegram (id, username, имя)
    - Баланс токенов
    - Статус (заблокирован ли)
    - Согласие с офертой

    Attributes:
        id: Внутренний ID в нашей БД (автоинкремент).
        telegram_id: ID пользователя в Telegram. BigInteger, потому что
            Telegram ID может быть очень большим числом (больше 2^31).
        username: @username в Telegram (без @). Может быть None.
        first_name: Имя пользователя из Telegram.
        last_name: Фамилия пользователя из Telegram. Может быть None.
        language: Код языка (ru, en). По умолчанию ru.
        created_at: Дата первого /start (регистрации).
        source: Откуда пришёл пользователь (start-параметр).
            Например: ?start=ref_123 → source="ref_123"
        balance: Баланс внутренних токенов. 0 = нет токенов.
        is_blocked: Заблокирован ли пользователь админом.
            Заблокированные не могут пользоваться ботом.
        terms_accepted_at: Когда принял оферту. None = не принял.
        accepted_legal_version: Версия оферты, которую принял.
            При обновлении оферты можно запросить повторное согласие.
    """

    __tablename__ = "users"

    # Первичный ключ — внутренний ID
    # autoincrement=True — БД сама генерирует уникальные ID
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Telegram ID — уникальный идентификатор пользователя в Telegram
    # BigInteger — потому что Telegram ID может быть > 2 миллиардов
    # unique=True — два пользователя не могут иметь одинаковый telegram_id
    # index=True — ускоряет поиск по этому полю (используется часто)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
    )

    # Данные профиля из Telegram
    # String(255) — ограничение длины для оптимизации хранения
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Язык интерфейса (ru, en)
    # default="ru" — новые пользователи получают русский по умолчанию
    language: Mapped[str] = mapped_column(String(10), default="ru", nullable=False)

    # Дата регистрации (первый /start)
    # func.now() — текущее время на момент INSERT
    # server_default — значение устанавливается на стороне БД
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    # Откуда пришёл пользователь (start-параметр)
    # Например: t.me/bot?start=promo_winter → source="promo_winter"
    # Полезно для аналитики: какие каналы привлечения работают
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Баланс внутренних токенов
    # default=0 — новые пользователи начинают с нуля
    # (бонус при регистрации начисляется отдельной транзакцией)
    balance: Mapped[int] = mapped_column(default=0, nullable=False)

    # Заблокирован ли пользователь
    # True — бот не отвечает на сообщения этого пользователя
    is_blocked: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Согласие с офертой
    # terms_accepted_at — когда нажал "Принимаю"
    # accepted_legal_version — версия документа (например, "1.0")
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_legal_version: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )

    # Флаг: был ли начислен регистрационный бонус
    # Защита от race condition — гарантирует однократное начисление
    # даже при повторных быстрых вызовах /start или принятия оферты
    registration_bonus_granted: Mapped[bool] = mapped_column(
        default=False, nullable=False
    )

    # Связь с сообщениями (история диалогов)
    # back_populates — двусторонняя связь с Message.user
    # cascade="all, delete-orphan" — при удалении пользователя
    # удаляются все его сообщения
    messages: Mapped[list["Message"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Связь с транзакциями (история операций с балансом)
    # back_populates — двусторонняя связь с Transaction.user
    # cascade="all, delete-orphan" — при удалении пользователя
    # удаляются все его транзакции
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Связь с платежами (история покупок токенов)
    # back_populates — двусторонняя связь с Payment.user
    # cascade="all, delete-orphan" — при удалении пользователя
    # удаляются все его платежи
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Связь с подписками
    # back_populates — двусторонняя связь с Subscription.user
    # cascade="all, delete-orphan" — при удалении пользователя
    # удаляются все его подписки
    subscriptions: Mapped[list["Subscription"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # Связь с генерациями AI
    # back_populates — двусторонняя связь с Generation.user
    # cascade="all, delete-orphan" — при удалении пользователя
    # удаляются все его генерации
    generations: Mapped[list["Generation"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @override
    def __repr__(self) -> str:
        """Строковое представление для отладки."""
        return (
            f"<User(id={self.id}, telegram_id={self.telegram_id}, "
            f"username={self.username})>"
        )
