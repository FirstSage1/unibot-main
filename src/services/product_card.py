"""Создание карточки товара с анализом фото и оплатой после доставки."""

import asyncio
import base64
from collections.abc import Awaitable, Callable

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import YamlConfig
from src.db.models.generation import GenerationDBStatus
from src.db.repositories import UserRepository
from src.db.repositories.generation_repo import GenerationRepository
from src.providers.ai.base import GenerationType
from src.services.ai_service import AIService
from src.services.billing_service import BillingService
from src.services.card_prompts import IDEAS_PROMPT, CardIdeas, build_card_prompt


class CardError(Exception):
    """Ошибка сценария с безопасным ключом пользовательского сообщения."""


class ProductCardService:
    """Оркестрация AI и биллинга без зависимости от Telegram."""

    def __init__(self, ai_service: AIService, config: YamlConfig) -> None:
        self.ai = ai_service
        self.config = config

    def validate_models(self) -> None:
        """Проверить явно настроенные модели анализа и редактирования."""
        available = self.ai.get_available_models()
        for key, kind in (
            (self.config.card.idea_model, GenerationType.CHAT),
            (self.config.card.image_model, GenerationType.IMAGE_EDIT),
        ):
            if key not in available or available[key].generation_type != kind:
                raise CardError("card_no_models_available")

    async def suggest(
        self, session: AsyncSession, telegram_user_id: int, image_data: bytes
    ) -> CardIdeas:
        """Учесть анализ товара как расход, включённый в стоимость карточки."""
        self.validate_models()
        user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
        if user is None:
            raise CardError("error_user_not_found")
        repo = GenerationRepository(session)
        if await repo.count_pending_generations(user.id):
            raise CardError("card_busy")
        key = self.config.card.idea_model
        model = self.config.models[key]
        generation = await repo.create_generation(user.id, "chat", key)
        generation_id = generation.id
        try:
            ideas, usage = await self._analyze(image_data)
            await repo.update_generation_status(
                generation_id,
                GenerationDBStatus.COMPLETED,
                cost_rub=model.cost.calculate(usage),
                tokens_charged=0,
            )
            return ideas
        except (Exception, asyncio.CancelledError):
            await session.rollback()
            await repo.update_generation_status(
                generation_id, GenerationDBStatus.FAILED
            )
            raise

    async def _analyze(self, image_data: bytes) -> tuple[CardIdeas, dict[str, int]]:
        """Проанализировать референс и вернуть три проверенных сюжета."""
        encoded = base64.b64encode(image_data).decode("ascii")
        async with asyncio.timeout(self.config.generation_timeouts.chat):
            result = await self.ai.generate(
                model_key=self.config.card.idea_model,
                prompt="",
                system_prompt=IDEAS_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Предложи три сценария для этого товара.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{encoded}",
                                },
                            },
                        ],
                    }
                ],
            )
        if not result.is_success or not isinstance(result.content, str):
            raise CardError("card_analysis_error")
        raw = result.content.strip()
        if raw.startswith("```json") and raw.endswith("```"):
            raw = raw[7:-3].strip()
        try:
            ideas = CardIdeas.model_validate_json(raw)
        except ValidationError as error:
            raise CardError("card_analysis_error") from error
        usage = {
            key: value
            for key, value in result.usage.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        return ideas, usage

    async def generate(
        self,
        session: AsyncSession,
        telegram_user_id: int,
        image_data: bytes,
        prompt: str,
        deliver: Callable[[str, str], Awaitable[None]],
    ) -> None:
        """Сгенерировать фото, доставить результат и учесть оплату и историю."""
        self.validate_models()
        user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
        if user is None:
            raise CardError("error_user_not_found")
        billing = BillingService(session, self.config.billing, self.config)
        key = self.config.card.image_model
        cost = await billing.check_and_reserve(user, key)
        if not cost.can_proceed:
            raise CardError("card_insufficient_balance")
        repo = GenerationRepository(session)
        if await repo.count_pending_generations(user.id):
            raise CardError("card_busy")
        model = self.config.models[key]
        generation = await repo.create_generation(
            user.id, "image_edit", key, model.cost.calculate()
        )
        generation_id = generation.id
        try:
            async with asyncio.timeout(self.config.generation_timeouts.image_edit):
                result = await self.ai.generate(
                    model_key=key,
                    prompt=prompt,
                    image_data=image_data,
                )
            if (
                not result.is_success
                or not isinstance(result.content, str)
                or not result.content
            ):
                raise CardError("card_empty_response")
            await deliver(result.content, prompt)
            charged = await billing.charge_generation(user, key, cost, "image_edit")
            await repo.update_generation_status(
                generation_id,
                GenerationDBStatus.COMPLETED,
                tokens_charged=charged.tokens_charged,
                cost_rub=model.cost.calculate(result.usage),
                transaction_id=charged.transaction_id,
            )
        except (Exception, asyncio.CancelledError):
            await session.rollback()
            await repo.update_generation_status(
                generation_id, GenerationDBStatus.FAILED
            )
            raise

    def prompt(self, ideas: CardIdeas, index: int) -> str:
        """Собрать полный запрос для выбранной идеи."""
        return build_card_prompt(ideas, index)


def create_card_service() -> ProductCardService:
    """Собрать зависимости сценария в точке входа."""
    from src.config.yaml_config import yaml_config
    from src.services.ai_service import create_ai_service

    return ProductCardService(create_ai_service(), yaml_config)
