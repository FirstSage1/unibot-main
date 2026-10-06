"""Модель для хранения истории сообщений в диалогах с AI.

Эта модель используется для сохранения контекста диалога в /chatgpt.
Каждое сообщение (от пользователя и от AI) записывается в БД,
чтобы AI мог учитывать предыдущий контекст в следующих ответах.

Пример использования:
    - Пользователь: "Привет! Как дела?"
    - AI: "Привет! У меня всё отлично, спасибо!"
    - Пользователь: "А что ты умеешь?" <- AI видит весь предыдущий контекст
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing_extensions import override

from src.db.models_base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class Message(Base):
    """Сообщение в диалоге с AI.

    Каждая запись представляет одно сообщение в диалоге:
    - role="user" — сообщение от пользователя
    - role="assistant" — ответ от AI
    - role="system" — системный промпт (опционально)

    Attributes:
        id: Уникальный идентификатор сообщения.
        user_id: ID пользователя, которому принадлежит диалог.
        model_key: Ключ модели из config.yaml (например, "gpt-4o").
        role: Роль отправителя сообщения (user/assistant/system).
        content: Текст сообщения.
        created_at: Дата и время создания сообщения.
        user: Связь с моделью User (для доступа к данным пользователя).
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)

    # ID пользователя из таблицы users
    # BigInteger потому что telegram_id может быть очень большим числом
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    # Ключ модели, с которой велась переписка (например, "gpt-4o")
    # Используется для фильтрации истории по конкретной модели
    model_key: Mapped[str] = mapped_column(index=True)

    # Роль отправителя: "user", "assistant", "system"
    # OpenAI API требует указывать роль для каждого сообщения в контексте
    role: Mapped[str]

    # Текст сообщения
    # Text вместо String для поддержки длинных сообщений (>255 символов)
    content: Mapped[str] = mapped_column(Text)

    # Дата создания сообщения (для сортировки и очистки старых)
    created_at: Mapped[datetime] = mapped_column(
        default=func.now(),
        index=True,
    )

    # Связь с пользователем (для удобного доступа к данным)
    user: Mapped["User"] = relationship(
        back_populates="messages",
        lazy="selectin",
    )

    @override
    def __repr__(self) -> str:
        """Строковое представление сообщения для отладки.

        Returns:
            Строка с основной информацией о сообщении.
        """
        content_preview = (
            self.content[:50] + "..." if len(self.content) > 50 else self.content
        )
        return (
            f"<Message(id={self.id}, user_id={self.user_id}, "
            f"role={self.role}, content='{content_preview}')>"
        )
