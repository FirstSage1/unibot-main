"""AI-провайдеры для генерации контента.

Этот пакет реализует плагинную архитектуру для работы с AI-сервисами.
Каждый провайдер — это адаптер, который реализует единый интерфейс BaseProviderAdapter.

Поддерживаемые провайдеры:
- OpenRouter (https://openrouter.ai) — агрегатор моделей
- RouterAI (https://routerai.ru) — российский сервис

Оба провайдера OpenAI-совместимые, работают через единый OpenAIAdapter.

Пример использования:
    from src.providers.ai import OpenAIAdapter

    adapter = OpenAIAdapter(
        api_key="sk-or-v1-...",
        base_url="https://openrouter.ai/api/v1",
    )
    result = await adapter.generate(
        model_id="openai/gpt-4o",
        prompt="Привет!",
    )
"""

from src.core.exceptions import GenerationError, ProviderNotAvailableError
from src.providers.ai.base import (
    BaseProviderAdapter,
    GenerationResult,
    GenerationStatus,
    GenerationType,
)
from src.providers.ai.openai_provider import OpenAIAdapter

__all__ = [
    "BaseProviderAdapter",
    "GenerationError",
    "GenerationResult",
    "GenerationStatus",
    "GenerationType",
    "OpenAIAdapter",
    "ProviderNotAvailableError",
]
