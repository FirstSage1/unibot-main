"""Исключения для работы с базой данных.

DEPRECATED: Этот модуль сохранён для обратной совместимости.
Используйте `from src.core.exceptions import ...` для новых импортов.

Все исключения централизованы в src/core/exceptions.py.
"""

from src.core.exceptions import (
    DatabaseConnectionError,
    DatabaseError,
    DatabaseOperationError,
    UserNotFoundError,
)

__all__ = [
    "DatabaseConnectionError",
    "DatabaseError",
    "DatabaseOperationError",
    "UserNotFoundError",
]
