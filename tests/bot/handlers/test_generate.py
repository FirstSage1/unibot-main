"""Проверки сценария описания товара."""

from unittest.mock import AsyncMock, MagicMock

from src.bot.handlers.generate import cmd_generate, handle_product_name
from src.bot.states.generate import GenerateStates
from src.config.yaml_config import ModelConfig
from src.providers.ai.base import GenerationType
from src.services.product_description import build_product_messages


async def test_generate_selects_gpt_and_requests_name() -> None:
    """Команда выбирает GPT и начинает отдельный сценарий."""
    message = AsyncMock()
    state = AsyncMock()
    ai_service = MagicMock()
    ai_service.get_available_models.return_value = {
        "gpt": ModelConfig(
            provider="anymodel",
            model_id="cx/gpt-5.6-terra",
            generation_type=GenerationType.CHAT,
            price_tokens=1,
        )
    }
    await cmd_generate(message, state, ai_service)
    state.update_data.assert_awaited_once_with(model_key="gpt")
    state.set_state.assert_awaited_once_with(GenerateStates.waiting_for_name)


async def test_generate_without_gpt_does_not_start() -> None:
    """Недоступный провайдер не приводит к запуску генерации."""
    message = AsyncMock()
    state = AsyncMock()
    ai_service = MagicMock()
    ai_service.get_available_models.return_value = {}
    await cmd_generate(message, state, ai_service)
    state.set_state.assert_not_awaited()
    message.answer.assert_awaited_once()


async def test_product_name_is_trimmed() -> None:
    """Название сохраняется перед запросом характеристик."""
    message = AsyncMock()
    message.text = "  Термос  "
    state = AsyncMock()
    await handle_product_name(message, state)
    state.update_data.assert_awaited_once_with(product_name="Термос")
    state.set_state.assert_awaited_once_with(GenerateStates.waiting_for_characteristics)


def test_product_facts_are_separate_from_instructions() -> None:
    """Пользовательские данные не попадают в системные инструкции."""
    messages = build_product_messages("Термос", "Сталь, 500 мл")
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "500 мл" in messages[1]["content"]
    assert "500 мл" not in messages[0]["content"]
