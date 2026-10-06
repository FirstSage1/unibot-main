# -*- coding: utf-8 -*-
"""Конфигурация Alembic для миграций базы данных.

Этот файл запускается каждый раз при выполнении команд alembic.
Настроен для работы с async SQLAlchemy (asyncpg, aiosqlite).

Как работают миграции:
1. Разработчик меняет модели (src/db/models/*.py)
2. Запускает: alembic revision --autogenerate -m "описание"
3. Alembic сравнивает модели с БД и генерирует миграцию
4. Разработчик проверяет миграцию и запускает: alembic upgrade head

На продакшене (Amvera):
- При старте контейнера выполняется: alembic upgrade head
- Все накопленные миграции применяются к БД
"""

import asyncio
import sys
from logging.config import fileConfig

from sqlalchemy.engine import Connection

from alembic import context

# Импортируем Base и модели, чтобы Alembic знал о таблицах
# ВАЖНО: импортировать именно модели, а не только Base!
from src.db.base import Base, get_engine

# Импортируем все модели для автогенерации
# ВАЖНО: все модели должны быть здесь, иначе Alembic их не увидит
from src.db.models import (  # noqa: F401
    Broadcast,
    Generation,
    Message,
    Payment,
    Referral,
    Subscription,
    Transaction,
    User,
)

# Конфигурация из alembic.ini
config = context.config

# Настройка логирования из alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata моделей — Alembic использует для сравнения с БД
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Выполнить миграции в 'offline' режиме.

    Offline режим — генерация SQL без подключения к БД.
    Используется для создания SQL-скриптов миграций.

    Пример: alembic upgrade head --sql > migration.sql
    """
    # Получаем URL из нашего engine (который настроен в src/db/base.py)
    engine = get_engine()
    url = str(engine.url)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Выполнить миграции с использованием соединения.

    Args:
        connection: Синхронное соединение SQLAlchemy.
    """
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Выполнить миграции в async режиме.

    Создаёт async engine и выполняет миграции через sync connection.
    Alembic не поддерживает async напрямую, поэтому используем
    run_sync() для выполнения миграций в sync контексте.
    """
    # Выводим прогресс в реальном времени
    print("Подключение к базе данных...", flush=True)

    # Получаем async engine из src/db/base.py
    engine = get_engine()

    try:
        print("Применение миграций...", flush=True)
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)

        print("✅ Миграции успешно применены!", flush=True)
    finally:
        # Закрываем engine чтобы процесс мог завершиться
        await engine.dispose()


def run_migrations_online() -> None:
    """Выполнить миграции в 'online' режиме.

    Online режим — подключение к реальной БД и применение миграций.
    Это стандартный режим для alembic upgrade head.
    """
    asyncio.run(run_async_migrations())


# Выбираем режим выполнения
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
