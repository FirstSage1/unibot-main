"""Веб-админка для управления ботом.

Использует SQLAdmin — автоматически генерирует интерфейс для моделей.
Админка доступна только если настроены ADMIN__USERNAME и ADMIN__PASSWORD.
"""

from src.admin.setup import setup_admin

__all__ = ["setup_admin"]
