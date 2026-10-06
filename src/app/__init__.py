"""Модуль приложения.

Содержит factory для создания FastAPI app и lifecycle management.
"""

from src.app.factory import create_app
from src.app.lifecycle import ApplicationLifecycle

__all__ = [
    "ApplicationLifecycle",
    "create_app",
]
