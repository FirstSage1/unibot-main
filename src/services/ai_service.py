"""Сервис для оркестрации AI-генераций."""

from typing import Any

from src.config.models import AIProvidersSettings
from src.config.yaml_config import (
    ModelConfig,
    YamlConfig,
)
from src.core.exceptions import ModelNotFoundError, ProviderNotAvailableError
from src.providers.ai import BaseProviderAdapter, GenerationResult
from src.providers.ai.registry import get_registry
from src.utils.logging import get_logger

logger = get_logger(__name__)


class AIService:
    """Центральный сервис для AI-генераций.

    Управляет адаптерами провайдеров и маршрутизирует запросы.
    Адаптеры создаются лениво при первом обращении.
    """

    def __init__(
        self,
        settings: AIProvidersSettings,
        config: YamlConfig,
        proxy_url: str | None = None,
    ) -> None:
        self._settings = settings
        self._config = config
        self._proxy_url = proxy_url
        self._registry = get_registry()

        # Максимальный таймаут из конфигурации — один адаптер на провайдер.
        # При добавлении нового типа генерации достаточно добавить поле
        # в GenerationTimeouts — max() автоматически учтёт его (OCP).
        timeouts = config.generation_timeouts
        self._timeout = max(
            timeouts.chat,
            timeouts.image,
            timeouts.image_edit,
            timeouts.tts,
            timeouts.stt,
        )

        self._adapters: dict[str, BaseProviderAdapter] = {}

        logger.info(
            "AIService: OpenRouter=%s, RouterAI=%s, AnyModel=%s, прокси=%s, "
            "моделей=%d, таймаут=%dс",
            "да" if settings.has_openrouter else "нет",
            "да" if settings.has_routerai else "нет",
            "да" if settings.has_anymodel else "нет",
            "да" if proxy_url else "нет",
            len(config.models),
            self._timeout,
        )

    def _get_adapter(self, provider: str) -> BaseProviderAdapter:
        """Получить адаптер для провайдера (с ленивой инициализацией)."""
        if provider in self._adapters:
            return self._adapters[provider]

        resolved_provider = self._resolve_provider(provider)
        adapter = self._registry.create_adapter(
            resolved_provider,
            self._settings,
            proxy_url=self._proxy_url,
            timeout=float(self._timeout),
        )

        self._adapters[provider] = adapter
        logger.debug("Создан адаптер: %s (запрошен: %s)", resolved_provider, provider)
        return adapter

    def _resolve_provider(self, provider: str) -> str:
        """Выбрать AnyModel, если RouterAI не настроен."""
        if (
            provider == "routerai"
            and not self._settings.has_routerai
            and self._settings.has_anymodel
        ):
            logger.info("RouterAI не настроен; используем AnyModel как fallback")
            return "anymodel"
        return provider

    @staticmethod
    def _resolve_model_id(provider: str, model_id: str, generation_type: str) -> str:
        """Подобрать идентификатор AnyModel из его каталога для fallback."""
        if provider != "anymodel" or model_id.startswith("am/"):
            return model_id
        if generation_type == "chat":
            return "cx/gpt-5.6-terra"
        if generation_type in {"image", "image_edit"}:
            # Старый cx-маршрут перенаправлял запросы на снятую модель gpt-6-sol.
            return "am/gpt-image-2"
        return model_id

    async def generate(
        self,
        model_key: str,
        prompt: str,
        **kwargs: Any,
    ) -> GenerationResult:
        """Выполнить генерацию через AI-провайдер."""
        model_config = self._config.get_model(model_key)
        if not model_config:
            raise ModelNotFoundError(
                f"Модель '{model_key}' не найдена в конфигурации.",
                model_key=model_key,
            )

        adapter = self._get_adapter(model_config.provider)
        resolved_provider = self._resolve_provider(model_config.provider)
        resolved_model_id = self._resolve_model_id(
            resolved_provider if model_config.provider == "routerai" else "direct",
            model_config.model_id,
            model_config.generation_type.value,
        )
        params = {**model_config.params, **kwargs}

        logger.debug(
            "Генерация: model_key=%s, provider=%s, type=%s",
            model_key,
            model_config.provider,
            model_config.generation_type.value,
        )

        result = await adapter.generate(
            model_id=resolved_model_id,
            prompt=prompt,
            generation_type=model_config.generation_type,
            **params,
        )

        logger.debug(
            "Генерация завершена: model_key=%s, status=%s",
            model_key,
            result.status.value,
        )
        return result

    def get_available_models(self) -> dict[str, ModelConfig]:
        """Получить модели с настроенными провайдерами."""
        return {
            key: config
            for key, config in self._config.models.items()
            if self._is_provider_available(config.provider)
        }

    def _is_provider_available(self, provider: str) -> bool:
        """Проверить, доступен ли провайдер."""
        try:
            self._get_adapter(provider)
            return True
        except ProviderNotAvailableError:
            return False

    def get_generation_type(self, model_key: str) -> str:
        """Получить тип генерации для модели."""
        model_config = self._config.get_model(model_key)
        if not model_config:
            raise ModelNotFoundError(
                f"Модель '{model_key}' не найдена в конфигурации.",
                model_key=model_key,
            )
        return model_config.generation_type.value

    def get_model_price(self, model_key: str) -> int:
        """Получить стоимость генерации в токенах."""
        model_config = self._config.get_model(model_key)
        return model_config.price_tokens if model_config else 0


def create_ai_service() -> AIService:
    """Создать AI-сервис с настройками из окружения."""
    from src.config.settings import settings
    from src.config.yaml_config import yaml_config

    return AIService(settings.ai, yaml_config, proxy_url=settings.proxy)
