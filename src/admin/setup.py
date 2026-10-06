"""Настройка и монтирование админки к FastAPI.

Админка монтируется только если настроены ADMIN__USERNAME и ADMIN__PASSWORD.
Если они не заданы — админка недоступна (возвращается 404).
"""

from pathlib import Path

from fastapi import FastAPI
from sqladmin import Admin

from src.admin.auth import get_admin_auth
from src.admin.views import (
    BroadcastAdmin,
    GenerationAdmin,
    PaymentAdmin,
    ReferralAdmin,
    SubscriptionAdmin,
    UserAdmin,
)
from src.config.settings import settings
from src.config.yaml_config import yaml_config
from src.db.base import get_sync_engine
from src.utils.logging import get_logger

logger = get_logger(__name__)


def setup_admin(app: FastAPI) -> Admin | None:
    """Настроить и подключить админку к FastAPI.

    Админка включается только если заданы ADMIN__USERNAME и ADMIN__PASSWORD.
    Это позволяет отключить админку на продакшене если она не нужна.

    Args:
        app: FastAPI приложение.

    Returns:
        Объект Admin если админка включена, None если отключена.
    """
    if not settings.admin.is_enabled:
        logger.info("Админка отключена (не заданы ADMIN__USERNAME и ADMIN__PASSWORD)")
        return None

    # Создаём синхронный engine для SQLAdmin
    sync_engine = get_sync_engine()

    # Создаём админку с аутентификацией и кастомными шаблонами
    # Путь к шаблонам относительно модуля admin
    templates_path = Path(__file__).parent / "templates"

    admin = Admin(
        app=app,
        engine=sync_engine,
        authentication_backend=get_admin_auth(),
        title="Unibot Admin",
        templates_dir=str(templates_path),
    )

    # Регистрируем представления моделей
    admin.add_view(UserAdmin)
    admin.add_view(PaymentAdmin)
    admin.add_view(ReferralAdmin)
    admin.add_view(GenerationAdmin)

    # Регистрируем подписки если есть подписочные тарифы
    if yaml_config.has_subscription_tariffs():
        admin.add_view(SubscriptionAdmin)

    # Регистрируем рассылки если они включены в конфиге
    if yaml_config.broadcast.enabled:
        admin.add_view(BroadcastAdmin)

    # Формируем URL админки — полный если есть домен, иначе относительный путь
    if settings.app.domain:
        # Убираем trailing slash и добавляем /admin
        domain = settings.app.domain.rstrip("/")
        if not domain.startswith("http"):
            domain = f"https://{domain}"
        admin_url = f"{domain}/admin"
    else:
        admin_url = "/admin"

    logger.info("Админка доступна: %s", admin_url)
    return admin
