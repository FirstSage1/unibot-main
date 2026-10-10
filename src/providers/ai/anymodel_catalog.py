"""Предварительная проверка image-маршрутов AnyModel."""

import asyncio
from collections.abc import Callable
from time import monotonic

import httpx
from pydantic import BaseModel, ValidationError

from src.core.exceptions import ModelTemporarilyUnavailableError
from src.providers.ai.base import GenerationType
from src.utils.logging import get_logger

logger = get_logger(__name__)
CATALOG_TIMEOUT_SECONDS = 15
MODEL_COOLDOWN_SECONDS = 60
MAX_MODEL_CANDIDATES = 3
PREFERRED_IMAGE_MODELS = (
    "cx/gpt-image-2",
    "flow/nano-banana",
    "cx/gpt-image-2.5",
    "flow/nano-banana-pro",
    "krea/nano-banana-pro",
    "krea/nano-banana",
)


class CatalogModel(BaseModel):
    """Документированные поля каталога, используемые для выбора модели."""

    id: str
    kind: str | None = None
    endpoint: str | None = None
    capabilities: list[str] = []


class ImageCatalog(BaseModel):
    """Ответ GET /models/image."""

    data: list[CatalogModel]


class AnyModelCatalog:
    """Каталог и временное исключение маршрутов, вернувших недоступность."""

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._unavailable_until: dict[str, float] = {}

    def mark_unavailable(self, model_id: str) -> None:
        """Не отправлять новые запросы известному недоступному маршруту."""
        self._unavailable_until[model_id] = self._clock() + MODEL_COOLDOWN_SECONDS

    async def candidates(
        self,
        client: object,
        preferred: str,
        operation: GenerationType,
        *,
        base_url: str,
        api_key: str,
        proxy: str | None,
    ) -> list[str]:
        """Получить совместимые маршруты до отправки платного запроса."""
        try:
            async with asyncio.timeout(CATALOG_TIMEOUT_SECONDS):
                routes = await self._candidates(
                    client,
                    preferred,
                    operation,
                    base_url=base_url,
                    api_key=api_key,
                    proxy=proxy,
                )
        except (httpx.HTTPError, TimeoutError, ValidationError, ValueError) as error:
            logger.warning("Проверка каталога AnyModel: %s", type(error).__name__)
            raise self._unavailable(preferred) from error
        if not routes:
            raise self._unavailable(preferred)
        return routes

    async def _candidates(
        self,
        client: object,
        preferred: str,
        operation: GenerationType,
        *,
        base_url: str,
        api_key: str,
        proxy: str | None,
    ) -> list[str]:
        """Проверить опубликованные маршруты и capabilities через общий клиент."""
        del client
        async with httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
            proxy=proxy,
        ) as catalog_client:
            response = await catalog_client.get("models/image")
            response.raise_for_status()
            catalog = ImageCatalog.model_validate(response.json())
            return await self._select_models(
                catalog_client, catalog, preferred, operation
            )

    async def _select_models(
        self,
        client: httpx.AsyncClient,
        catalog: ImageCatalog,
        preferred: str,
        operation: GenerationType,
    ) -> list[str]:
        """Отобрать опубликованные и совместимые маршруты."""
        published = {item.id: item for item in catalog.data}
        ordered = dict.fromkeys((preferred, *PREFERRED_IMAGE_MODELS, *published))
        candidates: list[str] = []
        for model_id in ordered:
            if model_id not in published:
                continue
            if self._unavailable_until.get(model_id, 0) > self._clock():
                continue
            info = await self._model_info(client, model_id)
            if info is None or not self._supports(info, operation):
                continue
            candidates.append(model_id)
            if len(candidates) == MAX_MODEL_CANDIDATES:
                break
        return candidates

    async def _model_info(
        self, client: httpx.AsyncClient, model_id: str
    ) -> CatalogModel | None:
        """Пропустить маршрут, недоступный уже при проверке метаданных."""
        try:
            response = await client.get("models/info", params={"id": model_id})
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in {404, 406, 503}:
                raise
            self.mark_unavailable(model_id)
            return None
        return CatalogModel.model_validate(response.json())

    @staticmethod
    def _supports(info: CatalogModel, operation: GenerationType) -> bool:
        """Не подменять редактирование моделью, которая не принимает референс."""
        if info.kind != "image" or info.endpoint != "/v1/images/generations":
            return False
        supported = (
            {"edit"}
            if operation == GenerationType.IMAGE_EDIT
            else {
                "text2img",
                "textToImage",
            }
        )
        return bool(supported.intersection(info.capabilities))

    @staticmethod
    def _unavailable(model_id: str) -> ModelTemporarilyUnavailableError:
        """Вернуть безопасную ошибку вместо непроверенного платного запроса."""
        return ModelTemporarilyUnavailableError(
            "Не найдена доступная совместимая модель",
            provider="anymodel",
            model_id=model_id,
            is_retryable=True,
        )
