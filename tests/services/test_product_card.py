"""Проверки AI, оплаты и истории карточки на изолированной БД."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import (
    BillingConfig,
    CardConfig,
    CostConfig,
    ModelConfig,
    YamlConfig,
)
from src.core.exceptions import ModelTemporarilyUnavailableError
from src.db.models.generation import GenerationDBStatus
from src.db.models.user import User
from src.db.repositories.generation_repo import GenerationRepository
from src.providers.ai.base import GenerationResult, GenerationStatus, GenerationType
from src.services.ai_service import AIService
from src.services.product_card import CardError, ProductCardService
from tests.services.test_card_prompts import make_ideas


@pytest.fixture
def ai() -> MagicMock:
    """Заменить все внешние запросы на явный мок."""
    return MagicMock(spec=AIService)


@pytest.fixture
def service(ai: MagicMock) -> ProductCardService:
    """Передать настройки теста без окружения и глобального конфига."""
    config = YamlConfig(
        card=CardConfig(idea_model="vision", image_model="edit"),
        billing=BillingConfig(enabled=True),
        models={
            "vision": ModelConfig(
                provider="test",
                model_id="vision",
                generation_type=GenerationType.CHAT,
                price_tokens=0,
                cost=CostConfig(
                    input_tokens_rub_per_1k=1,
                    output_tokens_rub_per_1k=2,
                ),
            ),
            "edit": ModelConfig(
                provider="test",
                model_id="edit",
                generation_type=GenerationType.IMAGE_EDIT,
                price_tokens=20,
            ),
        },
    )
    ai.get_available_models.return_value = config.models
    ai.generate = AsyncMock(
        return_value=GenerationResult(
            status=GenerationStatus.SUCCESS,
            content="https://example.com/result.png",
        )
    )
    return ProductCardService(ai, config)


async def test_suggest_sends_reference_and_validates_json(
    service: ProductCardService,
    ai: MagicMock,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    """Анализ получает само фото, а не только текст запроса."""
    ai.generate.return_value = GenerationResult(
        status=GenerationStatus.SUCCESS,
        content=make_ideas().model_dump_json(),
        usage={"prompt_tokens": 100, "completion_tokens": 200},
    )
    ideas = await service.suggest(db_session, test_user.telegram_id, b"photo")
    assert len(ideas.ideas) == 3
    request = ai.generate.call_args.kwargs
    assert request["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    generation = await GenerationRepository(db_session).get_last_generation(
        test_user.id, "chat"
    )
    assert generation is not None
    assert generation.status == GenerationDBStatus.COMPLETED
    assert generation.cost_rub == Decimal("0.5")
    assert generation.tokens_charged == 0
    assert test_user.balance == 1000


async def test_bad_analysis_does_not_invent_fallback(
    service: ProductCardService,
    ai: MagicMock,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    """Неясное фото требует повторной загрузки, а не случайных сцен."""
    ai.generate.return_value = GenerationResult(
        status=GenerationStatus.SUCCESS,
        content='{"product":"", "ideas":[]}',
    )
    with pytest.raises(CardError, match="card_analysis_error"):
        await service.suggest(db_session, test_user.telegram_id, b"photo")
    assert (
        await GenerationRepository(db_session).count_pending_generations(test_user.id)
        == 0
    )


async def test_charge_only_after_delivery(
    service: ProductCardService,
    ai: MagicMock,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    """Доставка видит исходный баланс; списание и история появляются после неё."""
    balance = test_user.balance

    async def deliver(content: str, prompt: str) -> None:
        assert test_user.balance == balance
        assert content.endswith("result.png")
        assert prompt == "полный промпт"

    await service.generate(
        db_session, test_user.telegram_id, b"photo", "полный промпт", deliver
    )
    await db_session.refresh(test_user)
    assert test_user.balance == balance - 20
    generation = await GenerationRepository(db_session).get_last_generation(
        test_user.id, "image_edit"
    )
    assert generation is not None
    assert generation.status == GenerationDBStatus.COMPLETED
    assert generation.tokens_charged == 20
    assert ai.generate.call_args.kwargs["image_data"] == b"photo"


@pytest.mark.parametrize(
    "failure",
    [
        OSError("доставка"),
        asyncio.CancelledError(),
        ModelTemporarilyUnavailableError(
            "Модель временно недоступна",
            provider="anymodel",
            model_id="cx/gpt-image-2",
            is_retryable=True,
        ),
    ],
)
async def test_failed_delivery_never_charges(
    service: ProductCardService,
    ai: MagicMock,
    db_session: AsyncSession,
    test_user: User,
    failure: BaseException,
) -> None:
    """Ошибка и отмена оставляют баланс и завершают запись генерации."""
    user_id, telegram_id, balance = (
        test_user.id,
        test_user.telegram_id,
        test_user.balance,
    )
    if isinstance(failure, ModelTemporarilyUnavailableError):
        ai.generate.side_effect = failure
    with pytest.raises(type(failure)):
        await service.generate(
            db_session, telegram_id, b"photo", "промпт", AsyncMock(side_effect=failure)
        )
    await db_session.refresh(test_user)
    assert test_user.balance == balance
    generation = await GenerationRepository(db_session).get_last_generation(
        user_id, "image_edit"
    )
    assert generation is not None
    assert generation.status == GenerationDBStatus.FAILED


async def test_insufficient_balance_skips_provider(
    service: ProductCardService,
    ai: MagicMock,
    db_session: AsyncSession,
    test_user: User,
) -> None:
    """Платный запрос не уходит при недостатке средств."""
    test_user.balance = 0
    await db_session.commit()
    with pytest.raises(CardError, match="card_insufficient_balance"):
        await service.generate(
            db_session, test_user.telegram_id, b"photo", "промпт", AsyncMock()
        )
    ai.generate.assert_not_awaited()
