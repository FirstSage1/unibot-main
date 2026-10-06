"""Health check эндпоинт.

Содержит endpoint для проверки работоспособности сервиса:
- GET /health — health check для мониторинга и liveness probes
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Проверка состояния сервиса.

    Используется для мониторинга и проверки доступности.
    Timeweb, Amvera и другие хостинги используют этот эндпоинт
    для определения, что приложение живо.

    Returns:
        Словарь со статусом "ok"
    """
    return {"status": "ok"}
