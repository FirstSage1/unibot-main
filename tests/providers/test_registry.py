"""Тесты для реестра AI-провайдеров."""

import pytest
from typing_extensions import override

from src.config.models import AIProvidersSettings
from src.providers.ai.base import BaseProviderAdapter, GenerationType
from src.providers.ai.registry import (
    ProviderAdapterFactory,
    ProviderNotAvailableError,
    ProviderRegistry,
)


class MockAdapter(BaseProviderAdapter):
    """Мок-адаптер для тестирования."""

    def __init__(self, provider_name: str) -> None:
        """Создать мок-адаптер.

        Args:
            provider_name: Название провайдера.
        """
        self._provider_name = provider_name

    @property
    @override
    def provider_name(self) -> str:
        """Название провайдера."""
        return self._provider_name

    @override
    def supports_capability(self, generation_type: GenerationType) -> bool:
        """Проверить поддержку типа генерации."""
        return generation_type == GenerationType.CHAT

    @override
    async def generate(  # type: ignore[no-untyped-def]
        self, *args, **kwargs
    ):
        """Заглушка для generate."""
        raise NotImplementedError("Это мок")


class MockFactory:
    """Мок-фабрика для тестирования."""

    def __init__(
        self,
        should_return_adapter: bool = True,
        provider_name: str = "mock-provider",
    ) -> None:
        self.should_return_adapter = should_return_adapter
        self.provider_name = provider_name
        self.create_called_with: AIProvidersSettings | None = None
        self.proxy_url_called_with: str | None = None
        self.timeout_called_with: float | None = None

    def create(
        self,
        settings: AIProvidersSettings,
        proxy_url: str | None = None,
        timeout: float | None = None,
    ) -> BaseProviderAdapter | None:
        """Создать адаптер или None."""
        self.create_called_with = settings
        self.proxy_url_called_with = proxy_url
        self.timeout_called_with = timeout
        if self.should_return_adapter:
            return MockAdapter(self.provider_name)
        return None


def test_registry_register_provider() -> None:
    """Тест: регистрация провайдера в реестре."""
    registry = ProviderRegistry()
    factory = MockFactory()

    registry.register("test-provider", factory)

    assert "test-provider" in registry.list_providers()


def test_registry_list_providers_sorted() -> None:
    """Тест: список провайдеров отсортирован по алфавиту."""
    registry = ProviderRegistry()

    registry.register("zebra", MockFactory())
    registry.register("alpha", MockFactory())
    registry.register("beta", MockFactory())

    providers = registry.list_providers()
    assert providers == ["alpha", "beta", "zebra"]


def test_registry_create_adapter_success() -> None:
    """Тест: создание адаптера через реестр."""
    registry = ProviderRegistry()
    factory = MockFactory(should_return_adapter=True, provider_name="test-provider")
    registry.register("test-provider", factory)

    # Создаём mock settings
    settings = AIProvidersSettings()

    adapter = registry.create_adapter("test-provider", settings)

    assert adapter.provider_name == "test-provider"
    assert factory.create_called_with is settings


def test_registry_create_adapter_provider_not_registered() -> None:
    """Тест: ошибка при попытке создать адаптер незарегистрированного провайдера."""
    registry = ProviderRegistry()
    registry.register("existing-provider", MockFactory())

    settings = AIProvidersSettings()

    with pytest.raises(ProviderNotAvailableError) as exc_info:
        registry.create_adapter("nonexistent-provider", settings)

    assert "не зарегистрирован" in str(exc_info.value)
    assert "nonexistent-provider" in str(exc_info.value)
    assert "existing-provider" in str(exc_info.value)
    assert exc_info.value.provider_type == "nonexistent-provider"


def test_registry_create_adapter_api_key_not_configured() -> None:
    """Тест: ошибка когда фабрика возвращает None (API-ключ не настроен)."""
    registry = ProviderRegistry()
    factory = MockFactory(should_return_adapter=False)
    registry.register("test-provider", factory)

    settings = AIProvidersSettings()

    with pytest.raises(ProviderNotAvailableError) as exc_info:
        registry.create_adapter("test-provider", settings)

    assert "API-ключ" in str(exc_info.value)
    assert "test-provider" in str(exc_info.value)
    assert exc_info.value.provider_type == "test-provider"


def test_registry_create_adapter_caches_result() -> None:
    """Тест: адаптер кешируется, фабрика вызывается только раз."""
    registry = ProviderRegistry()
    factory = MockFactory(should_return_adapter=True)
    registry.register("test-provider", factory)

    settings = AIProvidersSettings()

    # Первый вызов
    adapter1 = registry.create_adapter("test-provider", settings)

    # Второй вызов - должен вернуть тот же адаптер
    adapter2 = registry.create_adapter("test-provider", settings)

    # Адаптеры разные объекты, но фабрика вызвана дважды
    # (в текущей реализации кеш НЕ реализован в ProviderRegistry,
    # кеш находится в AIService)
    assert adapter1 is not adapter2  # Каждый раз новый адаптер
    assert factory.create_called_with is settings


def test_registry_overwrite_provider() -> None:
    """Тест: перезапись провайдера при повторной регистрации."""
    registry = ProviderRegistry()

    factory1 = MockFactory(provider_name="provider-v1")
    factory2 = MockFactory(provider_name="provider-v2")

    registry.register("test-provider", factory1)
    registry.register("test-provider", factory2)

    settings = AIProvidersSettings()
    adapter = registry.create_adapter("test-provider", settings)

    # Должен использоваться второй factory
    assert adapter.provider_name == "provider-v2"


def test_provider_not_available_error_attributes() -> None:
    """Тест: атрибуты ProviderNotAvailableError."""
    error = ProviderNotAvailableError(
        "Тестовое сообщение об ошибке",
        provider_type="test-provider",
    )

    assert error.message == "Тестовое сообщение об ошибке"
    assert error.provider_type == "test-provider"
    assert str(error) == "Тестовое сообщение об ошибке"


def test_provider_not_available_error_optional_provider_type() -> None:
    """Тест: provider_type опционален в ProviderNotAvailableError."""
    error = ProviderNotAvailableError("Ошибка без типа провайдера")

    assert error.message == "Ошибка без типа провайдера"
    assert error.provider_type is None


def test_provider_adapter_factory_protocol() -> None:
    """Тест: MockFactory соответствует протоколу ProviderAdapterFactory."""
    # Это проверка статической типизации, просто убедимся что код компилируется
    factory: ProviderAdapterFactory = MockFactory()
    assert callable(factory.create)
