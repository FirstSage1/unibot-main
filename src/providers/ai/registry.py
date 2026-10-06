"""Реестр AI-провайдеров (паттерн Registry, Open/Closed Principle)."""

from typing import Protocol

from src.config.models import AIProvidersSettings
from src.core.exceptions import ProviderNotAvailableError
from src.providers.ai.base import BaseProviderAdapter
from src.utils.logging import get_logger

logger = get_logger(__name__)


class ProviderAdapterFactory(Protocol):
    """Протокол фабрики адаптеров (structural subtyping)."""

    def create(
        self,
        settings: AIProvidersSettings,
        proxy_url: str | None = None,
        timeout: float | None = None,
    ) -> BaseProviderAdapter | None:
        """Создать адаптер если доступен API-ключ."""
        ...


class ProviderRegistry:
    """Реестр AI-провайдеров."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderAdapterFactory] = {}

    def register(self, provider_type: str, factory: ProviderAdapterFactory) -> None:
        """Зарегистрировать провайдер."""
        self._factories[provider_type] = factory
        logger.debug("Зарегистрирован провайдер: %s", provider_type)

    def create_adapter(
        self,
        provider_type: str,
        settings: AIProvidersSettings,
        proxy_url: str | None = None,
        timeout: float | None = None,
    ) -> BaseProviderAdapter:
        """Создать адаптер для провайдера."""
        if provider_type not in self._factories:
            raise ProviderNotAvailableError(
                f"Провайдер '{provider_type}' не зарегистрирован. "
                f"Доступные: {', '.join(sorted(self._factories.keys()))}",
                provider_type=provider_type,
            )

        adapter = self._factories[provider_type].create(
            settings, proxy_url=proxy_url, timeout=timeout
        )

        if adapter is None:
            raise ProviderNotAvailableError(
                f"API-ключ для '{provider_type}' не настроен.",
                provider_type=provider_type,
            )

        return adapter

    def list_providers(self) -> list[str]:
        """Список зарегистрированных провайдеров."""
        return sorted(self._factories.keys())


_registry = ProviderRegistry()


def register_provider(provider_type: str, factory: ProviderAdapterFactory) -> None:
    """Зарегистрировать провайдер в глобальном реестре."""
    _registry.register(provider_type, factory)


def get_registry() -> ProviderRegistry:
    """Получить глобальный реестр."""
    return _registry
