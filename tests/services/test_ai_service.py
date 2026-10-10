"""Тесты для AI-сервиса."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from src.config.models import AIProvidersSettings
from src.config.yaml_config import ModelConfig, YamlConfig
from src.core.exceptions import (
    AIServiceError,
    GenerationError,
    ModelNotFoundError,
    ProviderNotAvailableError,
)
from src.providers.ai.base import (
    GenerationResult,
    GenerationStatus,
    GenerationType,
)
from src.services.ai_service import AIService


@pytest.fixture
def mock_settings() -> AIProvidersSettings:
    """Настройки с мок-ключами."""
    return AIProvidersSettings(
        openrouter_api_key=SecretStr("sk-or-test-key"),
        routerai_api_key=SecretStr("routerai-test-key"),
    )


@pytest.fixture
def empty_config() -> YamlConfig:
    """Пустая конфигурация."""
    return YamlConfig(models={})


@pytest.fixture
def test_config() -> YamlConfig:
    """Конфигурация с тестовыми моделями."""
    return YamlConfig(
        models={
            "gpt-4o": ModelConfig(
                provider="openrouter",
                model_id="openai/gpt-4o",
                generation_type=GenerationType.CHAT,
                price_tokens=10,
                params={"max_tokens": 4096, "temperature": 0.7},
            ),
            "claude-sonnet": ModelConfig(
                provider="routerai",
                model_id="anthropic/claude-sonnet-3.5",
                generation_type=GenerationType.CHAT,
                price_tokens=15,
            ),
            "dall-e": ModelConfig(
                provider="openrouter",
                model_id="openai/dall-e-3",
                generation_type=GenerationType.IMAGE,
                price_tokens=50,
            ),
        }
    )


def test_ai_service_initialization(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: инициализация AIService."""
    service = AIService(mock_settings, test_config)

    assert service._settings is mock_settings
    assert service._config is test_config
    assert len(service._adapters) == 0  # Адаптеры создаются лениво


def test_ai_service_get_available_models_with_api_keys(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: get_available_models возвращает модели с настроенными провайдерами."""
    service = AIService(mock_settings, test_config)

    available = service.get_available_models()

    # Все 3 модели доступны (openrouter и routerai настроены)
    assert len(available) == 3
    assert "gpt-4o" in available
    assert "claude-sonnet" in available
    assert "dall-e" in available


def test_ai_service_get_available_models_without_api_keys(
    test_config: YamlConfig,
) -> None:
    """Тест: get_available_models фильтрует модели без API-ключей."""
    # Настройки без API-ключей
    empty_settings = AIProvidersSettings()
    service = AIService(empty_settings, test_config)

    available = service.get_available_models()

    # Никакие модели не доступны (нет API-ключей)
    assert len(available) == 0


def test_ai_service_get_available_models_partial_api_keys(
    test_config: YamlConfig,
) -> None:
    """Тест: get_available_models с частичными API-ключами."""
    # Только OpenRouter настроен
    partial_settings = AIProvidersSettings(
        openrouter_api_key=SecretStr("sk-or-test-key"),
    )
    service = AIService(partial_settings, test_config)

    available = service.get_available_models()

    # Только модели OpenRouter доступны
    assert len(available) == 2
    assert "gpt-4o" in available
    assert "dall-e" in available
    assert "claude-sonnet" not in available  # RouterAI не настроен


def test_ai_service_get_generation_type(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: get_generation_type возвращает правильный тип."""
    service = AIService(mock_settings, test_config)

    assert service.get_generation_type("gpt-4o") == "chat"
    assert service.get_generation_type("dall-e") == "image"


def test_ai_service_get_generation_type_unknown_model(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: get_generation_type выбрасывает ошибку для неизвестной модели."""
    service = AIService(mock_settings, test_config)

    with pytest.raises(ModelNotFoundError) as exc_info:
        service.get_generation_type("unknown-model")

    assert "unknown-model" in str(exc_info.value)
    assert exc_info.value.model_key == "unknown-model"


def test_ai_service_get_model_price(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: get_model_price возвращает правильную цену."""
    service = AIService(mock_settings, test_config)

    assert service.get_model_price("gpt-4o") == 10
    assert service.get_model_price("claude-sonnet") == 15
    assert service.get_model_price("dall-e") == 50


def test_ai_service_get_model_price_unknown_model(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: get_model_price возвращает 0 для неизвестной модели."""
    service = AIService(mock_settings, test_config)

    assert service.get_model_price("unknown-model") == 0


@pytest.mark.asyncio
async def test_ai_service_generate_unknown_model(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: generate выбрасывает ModelNotFoundError для неизвестной модели."""
    service = AIService(mock_settings, test_config)

    with pytest.raises(ModelNotFoundError) as exc_info:
        await service.generate("unknown-model", "test prompt")

    assert "unknown-model" in str(exc_info.value)
    assert exc_info.value.model_key == "unknown-model"


@pytest.mark.asyncio
async def test_ai_service_generate_provider_not_available(
    test_config: YamlConfig,
) -> None:
    """Тест: generate выбрасывает ProviderNotAvailableError если ключ не настроен."""
    # Настройки без API-ключей
    empty_settings = AIProvidersSettings()
    service = AIService(empty_settings, test_config)

    with pytest.raises(ProviderNotAvailableError) as exc_info:
        await service.generate("gpt-4o", "test prompt")

    assert "openrouter" in str(exc_info.value)
    assert exc_info.value.provider_type == "openrouter"


@pytest.mark.asyncio
async def test_ai_service_generate_success(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тест: generate успешно вызывает адаптер провайдера."""
    service = AIService(mock_settings, test_config)

    # Мокируем адаптер
    mock_adapter = MagicMock()
    mock_result = GenerationResult(
        status=GenerationStatus.SUCCESS,
        content="Тестовый ответ",
    )
    mock_adapter.generate = AsyncMock(return_value=mock_result)

    # Подставляем мок-адаптер в кеш сервиса
    service._adapters["openrouter"] = mock_adapter

    result = await service.generate("gpt-4o", "Привет!")

    # Проверяем результат
    assert result.status == GenerationStatus.SUCCESS
    assert result.content == "Тестовый ответ"

    # Проверяем что адаптер вызван с правильными параметрами
    mock_adapter.generate.assert_called_once()
    call_kwargs = mock_adapter.generate.call_args.kwargs
    assert call_kwargs["model_id"] == "openai/gpt-4o"
    assert call_kwargs["prompt"] == "Привет!"
    assert call_kwargs["generation_type"] == GenerationType.CHAT
    assert call_kwargs["max_tokens"] == 4096
    assert call_kwargs["temperature"] == 0.7


@pytest.mark.asyncio
async def test_ai_service_generate_merges_params(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: generate объединяет параметры из конфига и kwargs."""
    service = AIService(mock_settings, test_config)

    # Мокируем адаптер
    mock_adapter = MagicMock()
    mock_result = GenerationResult(
        status=GenerationStatus.SUCCESS,
        content="Ответ",
    )
    mock_adapter.generate = AsyncMock(return_value=mock_result)
    service._adapters["openrouter"] = mock_adapter

    # Вызываем с дополнительными параметрами
    await service.generate(
        "gpt-4o",
        "Привет!",
        temperature=0.9,  # Перезаписываем значение из конфига
        top_p=0.95,  # Добавляем новый параметр
    )

    # Проверяем параметры
    call_kwargs = mock_adapter.generate.call_args.kwargs
    assert call_kwargs["max_tokens"] == 4096  # Из конфига
    assert call_kwargs["temperature"] == 0.9  # Перезаписано через kwargs
    assert call_kwargs["top_p"] == 0.95  # Добавлено через kwargs


@pytest.mark.asyncio
async def test_ai_service_uses_next_model_after_temporary_failure(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Временная ошибка первой модели переключает запрос на следующий маршрут."""
    service = AIService(mock_settings, test_config)
    adapter = MagicMock()
    adapter.get_model_candidates = AsyncMock(return_value=["first", "second"])
    adapter.generate = AsyncMock(
        side_effect=[
            GenerationError(
                "temporary", provider="test", model_id="first", is_retryable=True
            ),
            GenerationResult(status=GenerationStatus.SUCCESS, content="готово"),
        ]
    )
    service._adapters["openrouter"] = adapter

    result = await service.generate("gpt-4o", "проверка")

    assert result.content == "готово"
    assert [call.kwargs["model_id"] for call in adapter.generate.await_args_list] == [
        "first",
        "second",
    ]


@pytest.mark.asyncio
async def test_ai_service_adapter_caching(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: адаптеры кешируются при повторных вызовах."""
    service = AIService(mock_settings, test_config)

    adapter1 = service._get_adapter("openrouter")
    adapter2 = service._get_adapter("openrouter")

    # Должен быть тот же объект
    assert adapter1 is adapter2


def test_ai_service_is_provider_available_true(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: _is_provider_available возвращает True для настроенных провайдеров."""
    service = AIService(mock_settings, test_config)

    assert service._is_provider_available("openrouter") is True
    assert service._is_provider_available("routerai") is True

    assert (
        service._resolve_model_id("routerai", "openai/gpt-5.2", "chat")
        == "openai/gpt-5.2"
    )
    assert (
        service._resolve_model_id("anymodel", "openai/gpt-5.2", "chat")
        == "cx/gpt-5.6-terra"
    )
    assert (
        service._resolve_model_id("anymodel", "google/gemini-image", "image")
        == "cx/gpt-image-2"
    )


def test_ai_service_is_provider_available_false(
    test_config: YamlConfig,
) -> None:
    """Тест: _is_provider_available возвращает False для ненастроенных провайдеров."""
    empty_settings = AIProvidersSettings()
    service = AIService(empty_settings, test_config)

    assert service._is_provider_available("openrouter") is False
    assert service._is_provider_available("routerai") is False


def test_routerai_models_use_anymodel_when_routerai_key_missing(
    test_config: YamlConfig,
) -> None:
    """Модели RouterAI становятся доступными через AnyModel fallback."""
    settings = AIProvidersSettings(anymodel_api_key=SecretStr("anymodel-test-key"))
    service = AIService(settings, test_config)

    adapter = service._get_adapter("routerai")

    assert adapter._base_url == "https://anymodel.org/v1"
    assert service._is_provider_available("routerai") is True


def test_ai_service_is_provider_available_unregistered(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: _is_provider_available для незарегистрированных провайдеров."""
    service = AIService(mock_settings, test_config)

    assert service._is_provider_available("nonexistent-provider") is False


def test_model_not_found_error_attributes() -> None:
    """Тест: атрибуты ModelNotFoundError."""
    error = ModelNotFoundError(
        "Модель не найдена",
        model_key="test-model",
    )

    assert error.message == "Модель не найдена"
    assert error.model_key == "test-model"
    assert str(error) == "Модель не найдена"


def test_ai_service_error_attributes() -> None:
    """Тест: атрибуты AIServiceError."""
    error = AIServiceError("Ошибка сервиса", model_key="test-model")

    assert error.message == "Ошибка сервиса"
    assert error.model_key == "test-model"
    assert str(error) == "Ошибка сервиса"


def test_ai_service_initialization_with_proxy(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: инициализация AIService с прокси."""
    proxy_url = "http://proxy.example.com:8080"
    service = AIService(mock_settings, test_config, proxy_url=proxy_url)

    assert service._proxy_url == proxy_url


def test_ai_service_initialization_without_proxy(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
) -> None:
    """Тест: инициализация AIService без прокси."""
    service = AIService(mock_settings, test_config, proxy_url=None)

    assert service._proxy_url is None


def test_ai_service_passes_proxy_and_timeout_to_adapter(
    mock_settings: AIProvidersSettings,
    test_config: YamlConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тест: AIService передает proxy_url и timeout в адаптер."""
    proxy_url = "socks5://proxy.example.com:1080"
    service = AIService(mock_settings, test_config, proxy_url=proxy_url)

    mock_adapter = MagicMock()
    mock_create_adapter = MagicMock(return_value=mock_adapter)
    monkeypatch.setattr(service._registry, "create_adapter", mock_create_adapter)

    adapter = service._get_adapter("openrouter")

    # Таймаут = max(60, 180, 180, 120, 300) = 300
    mock_create_adapter.assert_called_once_with(
        "openrouter",
        mock_settings,
        proxy_url=proxy_url,
        timeout=300.0,
    )
    assert adapter is mock_adapter
