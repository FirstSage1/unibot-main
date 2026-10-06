"""Тесты для базового сервиса генерации (BaseGenerationService).

Этот модуль тестирует общую логику расчёта себестоимости генераций,
которая используется всеми типами генераций (chat, image, tts, stt).
"""

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from typing_extensions import override

from src.config.yaml_config import CostConfig, ModelConfig
from src.providers.ai.base import GenerationType
from src.services.generation.base import BaseGenerationService, GenerationResult


class ConcreteGenerationService(BaseGenerationService):
    """Конкретная реализация BaseGenerationService для тестов."""

    generation_type: str = str(GenerationType.CHAT.value)

    @override
    async def _perform_generation(
        self, model_key: str, **generation_params: Any
    ) -> GenerationResult:
        """Заглушка для абстрактного метода."""
        return GenerationResult(content="test")


def test_calculate_preliminary_cost_with_per_request_pricing() -> None:
    """Тест: предварительная себестоимость для per_request модели."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="dall-e-3",
        generation_type=GenerationType.IMAGE,
        price_tokens=50,
        cost=CostConfig(per_request_rub=4.00),
    )

    # Act
    result = service._calculate_preliminary_cost(model_config)

    # Assert
    assert result == Decimal("4.0000")


def test_calculate_preliminary_cost_with_token_pricing_returns_zero() -> None:
    """Тест: предварительная себестоимость для токен-модели возвращает 0."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    # Act
    result = service._calculate_preliminary_cost(model_config)

    # Assert
    # Для токен-моделей предварительная себестоимость = 0
    assert result == Decimal(0)


def test_calculate_preliminary_cost_with_none_config() -> None:
    """Тест: предварительная себестоимость возвращает 0 если config=None."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    # Act
    result = service._calculate_preliminary_cost(None)

    # Assert
    assert result == Decimal(0)


def test_calculate_preliminary_cost_with_no_cost_config() -> None:
    """Тест: предварительная себестоимость возвращает 0 если cost пустой."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        # cost использует default_factory, поэтому создаётся пустой CostConfig
    )

    # Act
    result = service._calculate_preliminary_cost(model_config)

    # Assert
    assert result == Decimal(0)


def test_calculate_final_cost_returns_preliminary_for_per_request() -> None:
    """Тест: финальная себестоимость = предварительной для per_request моделей."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="dall-e-3",
        generation_type=GenerationType.IMAGE,
        price_tokens=50,
        cost=CostConfig(per_request_rub=4.00),
    )

    preliminary_cost = Decimal("4.0000")
    usage = {"prompt_tokens": 100, "completion_tokens": 200}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    # Для per_request моделей usage игнорируется
    assert result == preliminary_cost


def test_calculate_final_cost_calculates_from_usage_for_token_models() -> None:
    """Тест: финальная себестоимость рассчитывается по usage для токен-моделей."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    preliminary_cost = Decimal(0)
    usage = {"prompt_tokens": 100, "completion_tokens": 200}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    # (100 / 1000 * 0.50) + (200 / 1000 * 1.50) = 0.05 + 0.30 = 0.35
    expected = Decimal("0.3500")
    assert result == expected


def test_calculate_final_cost_returns_zero_for_none_config() -> None:
    """Тест: финальная себестоимость возвращает 0 если config=None."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    preliminary_cost = Decimal(0)
    usage = {"prompt_tokens": 100, "completion_tokens": 200}

    # Act
    result = service._calculate_final_cost(None, preliminary_cost, usage)

    # Assert
    assert result == Decimal(0)


def test_calculate_final_cost_returns_zero_for_no_cost_config() -> None:
    """Тест: финальная себестоимость возвращает 0 если cost пустой."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        # cost использует default_factory, поэтому создаётся пустой CostConfig
    )

    preliminary_cost = Decimal(0)
    usage = {"prompt_tokens": 100, "completion_tokens": 200}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    assert result == Decimal(0)


def test_calculate_final_cost_with_none_usage_for_token_model() -> None:
    """Тест: финальная себестоимость возвращает 0 если usage=None для токен-модели."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    preliminary_cost = Decimal(0)
    usage = None

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    # Без usage не можем рассчитать стоимость токен-модели
    assert result == Decimal(0)


def test_calculate_final_cost_with_empty_usage_for_token_model() -> None:
    """Тест: финальная себестоимость возвращает 0 если usage пустой."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    preliminary_cost = Decimal(0)
    usage: dict[str, int] = {}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    assert result == Decimal(0)


def test_calculate_final_cost_with_only_prompt_tokens() -> None:
    """Тест: финальная себестоимость корректно считает только входящие токены."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    preliminary_cost = Decimal(0)
    usage = {"prompt_tokens": 1000, "completion_tokens": 0}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    # 1000 / 1000 * 0.50 = 0.50
    assert result == Decimal("0.5000")


def test_calculate_final_cost_with_only_completion_tokens() -> None:
    """Тест: финальная себестоимость корректно считает только исходящие токены."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    preliminary_cost = Decimal(0)
    usage = {"prompt_tokens": 0, "completion_tokens": 1000}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    # 1000 / 1000 * 1.50 = 1.50
    assert result == Decimal("1.5000")


def test_calculate_final_cost_with_missing_prompt_tokens() -> None:
    """Тест: финальная себестоимость обрабатывает отсутствие prompt_tokens."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    preliminary_cost = Decimal(0)
    usage = {"completion_tokens": 200}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    # Считаем только completion_tokens: 200 / 1000 * 1.50 = 0.30
    assert result == Decimal("0.3000")


def test_calculate_final_cost_with_missing_completion_tokens() -> None:
    """Тест: финальная себестоимость обрабатывает отсутствие completion_tokens."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    preliminary_cost = Decimal(0)
    usage = {"prompt_tokens": 100}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    # Считаем только prompt_tokens: 100 / 1000 * 0.50 = 0.05
    assert result == Decimal("0.0500")


def test_calculate_final_cost_large_token_counts() -> None:
    """Тест: финальная себестоимость корректно обрабатывает большие токены."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    preliminary_cost = Decimal(0)
    usage = {"prompt_tokens": 100000, "completion_tokens": 50000}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    # (100000 / 1000 * 0.50) + (50000 / 1000 * 1.50) = 50 + 75 = 125
    assert result == Decimal("125.0000")


def test_calculate_preliminary_cost_various_per_request_values() -> None:
    """Тест: предварительная себестоимость корректно работает с разными ценами."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    test_cases = [
        (0.01, Decimal("0.0100")),
        (0.81, Decimal("0.8100")),
        (1.00, Decimal("1.0000")),
        (10.50, Decimal("10.5000")),
        (99.99, Decimal("99.9900")),
    ]

    for price, expected in test_cases:
        model_config = ModelConfig(
            provider="openrouter",
            model_id="test-model",
            generation_type=GenerationType.IMAGE,
            price_tokens=50,
            cost=CostConfig(per_request_rub=price),
        )

        # Act
        result = service._calculate_preliminary_cost(model_config)

        # Assert
        assert result == expected, f"Failed for price={price}"


def test_calculate_final_cost_fractional_tokens() -> None:
    """Тест: финальная себестоимость корректно обрабатывает дробные значения токенов."""
    # Arrange
    service = ConcreteGenerationService(
        session=MagicMock(),
        ai_service=MagicMock(),
    )

    model_config = ModelConfig(
        provider="openrouter",
        model_id="gpt-4o",
        generation_type=GenerationType.CHAT,
        price_tokens=10,
        cost=CostConfig(
            input_tokens_rub_per_1k=0.50,
            output_tokens_rub_per_1k=1.50,
        ),
    )

    preliminary_cost = Decimal(0)
    usage = {"prompt_tokens": 10, "completion_tokens": 5}

    # Act
    result = service._calculate_final_cost(model_config, preliminary_cost, usage)

    # Assert
    # (10 / 1000 * 0.50) + (5 / 1000 * 1.50) = 0.005 + 0.0075 = 0.0125
    expected = Decimal("0.0125")
    assert result == expected
