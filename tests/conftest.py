"""Общие фикстуры для всех тестов.

Этот файл содержит pytest-фикстуры, которые используются во всех тестах:
- Тестовая БД SQLite в памяти (для изоляции тестов)
- Асинхронные сессии SQLAlchemy
- Фабрики для создания тестовых данных (пользователи, сообщения)
- Регистрация команд для тестов middleware
"""

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models.message import Message
from src.db.models.user import User
from src.db.models_base import Base


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Создать event loop для всей тестовой сессии.

    По умолчанию pytest-asyncio создаёт новый event loop для каждого теста,
    но мы используем session-scoped loop для совместимости с БД-фикстурами.

    Yields:
        Event loop для выполнения асинхронных тестов.
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def test_engine() -> AsyncGenerator[Any, None]:
    """Создать тестовый движок SQLAlchemy.

    Использует SQLite в памяти (:memory:) для полной изоляции тестов.
    Каждый тест получает чистую БД без данных из предыдущих тестов.

    Yields:
        Асинхронный движок SQLAlchemy для тестовой БД.
    """
    # SQLite в памяти для полной изоляции тестов
    # file:memdb?mode=memory&cache=shared позволяет работать с async SQLAlchemy
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,  # Отключаем логи SQL в тестах
    )

    # Создаём все таблицы
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Очистка: удаляем все таблицы после теста
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: Any) -> AsyncGenerator[AsyncSession, None]:
    """Создать асинхронную сессию БД для теста.

    Каждый тест получает свою изолированную сессию.
    После завершения теста сессия автоматически откатывается и закрывается.

    Args:
        test_engine: Тестовый движок SQLAlchemy из фикстуры test_engine.

    Yields:
        Асинхронная сессия для работы с тестовой БД.
    """
    # Создаём session maker
    async_session_maker = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Создаём сессию
    async with async_session_maker() as session:
        yield session
        # Откатываем все изменения после теста (для изоляции)
        await session.rollback()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Создать тестового пользователя в БД.

    Возвращает готового пользователя с заполненными полями.
    Удобно для тестирования функций, требующих существующего user.

    Args:
        db_session: Сессия БД из фикстуры db_session.

    Returns:
        Созданный пользователь с ID=1, telegram_id=123456789.
    """
    user = User(
        telegram_id=123456789,
        username="test_user",
        first_name="Test",
        last_name="User",
        language="ru",
        balance=1000,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_message(db_session: AsyncSession, test_user: User) -> Message:
    """Создать тестовое сообщение в БД.

    Возвращает сообщение, связанное с тестовым пользователем.

    Args:
        db_session: Сессия БД из фикстуры db_session.
        test_user: Тестовый пользователь из фикстуры test_user.

    Returns:
        Созданное сообщение от пользователя.
    """
    message = Message(
        user_id=test_user.id,
        model_key="gpt-4o",
        role="user",
        content="Тестовое сообщение",
    )
    db_session.add(message)
    await db_session.commit()
    await db_session.refresh(message)
    return message


@pytest.fixture
def session_factory(
    db_session: AsyncSession,
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """Создать фабрику сессий БД для тестов с dependency injection.

    Возвращает фабрику, которая создаёт контекстный менеджер,
    возвращающий тестовую сессию БД. Используется для инжекции
    тестовой БД в хендлеры вместо реальной БД.

    Args:
        db_session: Тестовая сессия БД.

    Returns:
        Фабрика сессий, совместимая с типом DatabaseSession.

    Example:
        >>> async def handler(session_factory=DatabaseSession):
        ...     async with session_factory() as session:
        ...         # работаем с БД
        >>> await handler(session_factory=session_factory)  # в тесте
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _session_factory() -> AsyncGenerator[AsyncSession, None]:
        """Контекстный менеджер, возвращающий тестовую сессию."""
        yield db_session

    return _session_factory
