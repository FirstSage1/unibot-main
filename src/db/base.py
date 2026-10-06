"""Базовая конфигурация SQLAlchemy.

Этот модуль отвечает за:
- Создание подключения к базе данных (engine)
- Настройку фабрики сессий (async_sessionmaker)
- Базовый класс для всех моделей (Base)

Как это работает:
1. При импорте создаётся engine — "труба" к БД
2. async_sessionmaker — фабрика для создания сессий
3. Каждый запрос к БД использует свою сессию (через get_session)

Почему async:
- Бот обрабатывает много пользователей одновременно
- Синхронные запросы блокируют весь event loop
- Async позволяет ждать ответа от БД, обрабатывая других пользователей

URL базы данных:
- Если DATABASE__POSTGRES_URL указан — используется PostgreSQL
- Иначе — SQLite (./data/bot.db или /data/bot.db на Amvera)

ВАЖНО: Для изоляции тестов engine и async_session_factory создаются лениво.
Импорт Base для моделей должен быть из src.db.models_base.
"""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config.constants import DATA_DIR
from src.db.models_base import Base

# Реэкспортируем Base для обратной совместимости
__all__ = [
    "Base",
    "DatabaseSession",
    "get_async_session_factory",
    "get_engine",
    "get_session",
    "get_sync_engine",
]

if TYPE_CHECKING:
    from src.config.settings import Settings

# Ленивые синглтоны для engine и session factory
_engine: AsyncEngine | None = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_settings() -> "Settings":
    """Ленивая загрузка настроек.

    Используется для отложенной загрузки settings при первом обращении к БД,
    а не при импорте модуля. Это позволяет тестам импортировать модуль
    без загрузки настроек из .env файла.

    Returns:
        Объект Settings с настройками приложения.
    """
    from src.config.settings import settings

    return settings


def _get_database_url() -> str:
    """Получить URL подключения к базе данных (async).

    Логика выбора:
    1. Если DATABASE__POSTGRES_URL указан — используем PostgreSQL
    2. Иначе — SQLite из DATA_DIR/bot.db

    Returns:
        URL подключения в формате SQLAlchemy (с async-драйвером).
    """
    settings = _get_settings()
    if settings.database.postgres_url:
        return settings.database.postgres_url

    # По умолчанию — SQLite
    # aiosqlite — асинхронный драйвер для SQLite
    # DATA_DIR = /data на Amvera, ./data локально
    db_path = DATA_DIR / "bot.db"
    return f"sqlite+aiosqlite:///{db_path}"


def _get_sync_database_url() -> str:
    """Получить URL подключения к базе данных (sync).

    Используется для SQLAdmin, который требует синхронный engine.

    Преобразования:
    - postgresql+asyncpg:// → postgresql://
    - sqlite+aiosqlite:// → sqlite://

    Returns:
        URL подключения в формате SQLAlchemy (с sync-драйвером).
    """
    async_url = _get_database_url()

    # Убираем async-драйверы из URL
    return async_url.replace("+asyncpg", "").replace("+aiosqlite", "")


def get_engine() -> AsyncEngine:
    """Получить асинхронный engine (ленивая инициализация).

    Engine — "движок" подключения к БД.
    Это пул соединений, который переиспользуется между запросами.

    Returns:
        Асинхронный Engine для SQLAlchemy.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _get_database_url(),
            echo=False,
            pool_pre_ping=True,
        )
    return _engine


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Получить фабрику асинхронных сессий (ленивая инициализация).

    Фабрика сессий — создаёт новые сессии для каждого запроса.
    expire_on_commit=False — не "протухать" объекты после commit.

    Returns:
        Фабрика асинхронных сессий SQLAlchemy.
    """
    global _async_session_factory
    if _async_session_factory is None:
        _async_session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


# Обратная совместимость: глобальные переменные как свойства
# Используйте get_engine() и get_async_session_factory() для нового кода
@property  # type: ignore[misc]
def engine() -> AsyncEngine:
    """Обратная совместимость. Используйте get_engine()."""
    return get_engine()


@property  # type: ignore[misc]
def async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Обратная совместимость. Используйте get_async_session_factory()."""
    return get_async_session_factory()


def get_sync_engine() -> Engine:
    """Получить синхронный engine для SQLAdmin.

    SQLAdmin использует синхронные запросы для отображения данных.
    Создаём engine лениво, чтобы не тратить ресурсы если админка отключена.

    Returns:
        Синхронный Engine для SQLAlchemy.
    """
    return create_engine(
        _get_sync_database_url(),
        echo=False,
        pool_pre_ping=True,
    )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Получить сессию для работы с БД (для FastAPI Depends).

    Yields:
        AsyncSession для выполнения запросов.
    """
    factory = get_async_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


class DatabaseSession:
    """Асинхронный контекстный менеджер для работы с БД.

    Автоматически откатывает транзакцию при ошибке.

    Пример использования:
        async with DatabaseSession() as session:
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(123)
    """

    def __init__(self) -> None:
        """Инициализировать менеджер."""
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        """Открыть сессию."""
        factory = get_async_session_factory()
        self._session = factory()
        return await self._session.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Закрыть сессию, откатить при ошибке."""
        if self._session is None:
            return

        if exc_type is not None:
            # При ошибке откатываем транзакцию
            await self._session.rollback()

        await self._session.close()
