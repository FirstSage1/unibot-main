"""Базовый класс для всех моделей SQLAlchemy.

Этот модуль содержит только декларативную базу без побочных эффектов.
Используется для изоляции тестов от загрузки настроек при импорте моделей.

Пример использования в моделях:
    from src.db.models_base import Base

    class User(Base):
        __tablename__ = "users"
        ...
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс для всех моделей.

    Все модели (User, Message, Generation и т.д.) наследуются от Base.
    Это позволяет SQLAlchemy автоматически создавать таблицы
    и отслеживать связи между ними.

    Пример использования:
        class User(Base):
            __tablename__ = "users"
            id: Mapped[int] = mapped_column(primary_key=True)
    """
