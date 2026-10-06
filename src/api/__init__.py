"""API эндпоинты.

Этот модуль содержит FastAPI роутеры для:
- Health check (/health)
- Корневые пути (/, включая YooKassa webhook)
- Админ-панели (возврат платежей, управление)
- Webhook'ов платёжных провайдеров (YooKassa, Stripe)

Telegram Stars обрабатываются через aiogram handlers,
а не через HTTP webhooks.
"""

from src.api.admin import router as admin_router
from src.api.health import router as health_router
from src.api.root import router as root_router
from src.api.webhooks import router as webhooks_router

__all__ = ["admin_router", "health_router", "root_router", "webhooks_router"]
