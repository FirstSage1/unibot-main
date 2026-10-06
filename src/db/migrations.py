"""Проверка статуса миграций базы данных.

Этот модуль проверяет, применены ли все миграции к базе данных.
Используется при старте приложения для предупреждения о несинхронизированных миграциях.

Как работает проверка:
1. Получаем текущую ревизию из таблицы alembic_version в БД
2. Получаем последнюю ревизию из файлов миграций (head)
3. Если они не совпадают — выводим предупреждение

Типичные ситуации:
- Нет таблицы alembic_version → база не инициализирована
- Ревизия в БД отличается от head → есть непримененные миграции
- Ревизия в БД = head → всё в порядке
"""

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.utils.logging import get_logger

logger = get_logger(__name__)

# Путь к папке с миграциями относительно корня проекта
MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "alembic" / "versions"


def _parse_migration_file(content: str) -> tuple[str | None, str | None]:
    """Извлечь revision и down_revision из содержимого файла миграции.

    Args:
        content: Текстовое содержимое файла миграции.

    Returns:
        Кортеж (revision, down_revision). Оба могут быть None.
    """
    revision = None
    down_revision = None

    for line in content.splitlines():
        if line.startswith("revision"):
            # revision = "e2acbd4d7c23"
            revision = line.split("=")[1].strip().strip("\"'")
        elif line.startswith("down_revision"):
            # down_revision = None или down_revision = "abc123"
            value = line.split("=")[1].strip()
            if value != "None":
                down_revision = value.strip("\"'")

    return revision, down_revision


def _find_head_revision(revisions: dict[str, str | None]) -> str | None:
    """Найти head-ревизию среди всех ревизий.

    Head — это ревизия, на которую не ссылается ни одна другая как down_revision.

    Args:
        revisions: Словарь revision_id -> down_revision.

    Returns:
        ID head-ревизии или None.
    """
    all_down_revisions = set(revisions.values())
    heads = [rev for rev in revisions if rev not in all_down_revisions]

    if len(heads) == 1:
        return heads[0]
    elif len(heads) > 1:
        # Несколько голов — берём первую (алфавитно)
        logger.warning("Обнаружено несколько head-ревизий: %s", heads)
        return min(heads)

    return None


def get_head_revision() -> str | None:
    """Получить последнюю ревизию из файлов миграций.

    Читает файлы миграций и находит revision с down_revision = None
    (начальная миграция) или самую новую в цепочке.

    Returns:
        ID последней ревизии (head) или None, если миграций нет.
    """
    if not MIGRATIONS_DIR.exists():
        logger.warning("Папка миграций не найдена: %s", MIGRATIONS_DIR)
        return None

    # Собираем все ревизии и их зависимости
    revisions: dict[str, str | None] = {}  # revision_id -> down_revision

    for migration_file in MIGRATIONS_DIR.glob("*.py"):
        if migration_file.name.startswith("_"):
            continue

        content = migration_file.read_text(encoding="utf-8")
        revision, down_revision = _parse_migration_file(content)

        if revision:
            revisions[revision] = down_revision

    if not revisions:
        return None

    return _find_head_revision(revisions)


async def get_current_revision(engine: AsyncEngine) -> str | None:
    """Получить текущую ревизию из базы данных.

    Читает таблицу alembic_version, которую создаёт Alembic.

    Args:
        engine: Асинхронный SQLAlchemy engine.

    Returns:
        ID текущей ревизии или None, если таблица не существует.
    """
    async with engine.connect() as conn:
        try:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            return row[0] if row else None
        except (OperationalError, ProgrammingError):
            # Таблица не существует — миграции не применялись
            # OperationalError: SQLite (no such table)
            # ProgrammingError: PostgreSQL (relation does not exist)
            return None


async def check_migrations(engine: AsyncEngine) -> None:
    """Проверить статус миграций и вывести предупреждение если нужно.

    Сравнивает текущую ревизию в БД с последней ревизией в файлах миграций.
    Выводит WARNING если миграции не применены.

    Args:
        engine: Асинхронный SQLAlchemy engine.
    """
    head_revision = get_head_revision()
    current_revision = await get_current_revision(engine)

    if head_revision is None:
        logger.warning(
            "⚠️  Файлы миграций не найдены. "
            "Выполните: alembic revision --autogenerate -m 'initial'"
        )
        return

    if current_revision is None:
        logger.warning(
            "⚠️  МИГРАЦИИ НЕ ПРИМЕНЕНЫ! База данных не инициализирована.\n"
            "   Код может работать некорректно!\n"
            "   Выполните: alembic upgrade head"
        )
        return

    if current_revision != head_revision:
        logger.warning(
            "⚠️  МИГРАЦИИ НЕ АКТУАЛЬНЫ!\n"
            "   Текущая ревизия: %s\n"
            "   Последняя ревизия: %s\n"
            "   Код может работать некорректно!\n"
            "   Выполните: alembic upgrade head",
            current_revision,
            head_revision,
        )
        return

    logger.debug("✅ Миграции актуальны (ревизия: %s)", current_revision)
