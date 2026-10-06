"""Factory для создания FastAPI приложения.

Функция create_app() создаёт и настраивает FastAPI app:
- Подключает все роутеры (admin, webhooks, telegram, health)
- Настраивает админку
- Подключает lifecycle manager
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from src.admin import setup_admin
from src.admin.auth import get_admin_secret_key
from src.api.admin import router as admin_router
from src.api.health import router as health_router
from src.api.root import router as root_router
from src.api.telegram import router as telegram_router
from src.api.webhooks import router as webhooks_router
from src.app.lifecycle import ApplicationLifecycle
from src.config.settings import settings
from src.config.yaml_config import yaml_config
from src.utils.logging import get_logger

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Создать и настроить FastAPI приложение.

    Создаёт FastAPI app с:
    - Lifecycle management (startup/shutdown)
    - Всеми API роутерами
    - Админкой

    Returns:
        Настроенное FastAPI приложение готовое к запуску
    """
    # Создаём lifecycle manager
    lifecycle = ApplicationLifecycle(settings, yaml_config)

    # Определяем lifespan context manager
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        """Управление жизненным циклом приложения.

        Args:
            app: FastAPI приложение

        Yields:
            None: Приложение работает между startup и shutdown
        """
        # Startup
        await lifecycle.startup(app)

        yield

        # Shutdown
        await lifecycle.shutdown()

    # Создаём FastAPI приложение
    app = FastAPI(
        title="Unibot",
        description="Telegram-бот для AI-генерации",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Подключаем админку (если настроена)
    # Админка доступна по адресу /admin если заданы ADMIN__USERNAME и ADMIN__PASSWORD
    setup_admin(app)

    # Добавляем SessionMiddleware для работы сессий во всех роутах.
    # Это нужно для аутентификации admin API эндпоинтов через те же сессии,
    # что использует SQLAdmin (/admin).
    # secret_key берём из настроек админки — тот же, что используется для SQLAdmin.
    if settings.admin.is_enabled:
        app.add_middleware(
            SessionMiddleware,
            secret_key=get_admin_secret_key(),
        )

    # Подключаем API роутеры
    # Admin API: /api/admin/payments/{id}/refund
    app.include_router(admin_router)

    # Webhooks API: /api/webhooks/yookassa, /api/webhooks/stripe
    app.include_router(webhooks_router)

    # Telegram webhook API: /api/telegram/webhook
    # Используется только в production mode (когда указан APP__DOMAIN)
    app.include_router(telegram_router)

    # Health check API: /health
    app.include_router(health_router)

    # Root paths: GET / (redirect), POST / (YooKassa webhook)
    app.include_router(root_router)

    return app
