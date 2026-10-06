"""Тесты для загрузки и валидации YAML-конфигурации."""

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config.yaml_config import load_yaml_config
from src.providers.ai.base import GenerationType


def test_model_config_with_generation_type() -> None:
    """Тест загрузки конфига с generation_type."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        config = load_yaml_config(temp_path)

        assert len(config.models) == 1
        assert "test-model" in config.models

        model = config.models["test-model"]
        assert model.generation_type == GenerationType.CHAT
        assert model.provider == "openrouter"
        assert model.model_id == "openai/gpt-4o"
        assert model.price_tokens == 10
    finally:
        Path(temp_path).unlink()


def test_model_config_validates_generation_type() -> None:
    """Тест валидации неправильного generation_type."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: invalid_type
    price_tokens: 10
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        with pytest.raises(ValidationError) as exc_info:
            load_yaml_config(temp_path)

        # Проверяем, что ошибка содержит информацию о неправильном типе
        error_message = str(exc_info.value)
        assert "invalid_type" in error_message.lower()
        assert "generation_type" in error_message.lower()
    finally:
        Path(temp_path).unlink()


def test_model_config_all_generation_types() -> None:
    """Тест всех допустимых типов генерации."""
    yaml_content = """
models:
  chat-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10

  image-model:
    provider: openrouter
    model_id: openai/dall-e-3
    generation_type: image
    price_tokens: 50

  tts-model:
    provider: openrouter
    model_id: openai/tts-1
    generation_type: tts
    price_tokens: 5

  stt-model:
    provider: openrouter
    model_id: openai/whisper-1
    generation_type: stt
    price_tokens: 5
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        config = load_yaml_config(temp_path)

        assert len(config.models) == 4

        assert config.models["chat-model"].generation_type == GenerationType.CHAT
        assert config.models["image-model"].generation_type == GenerationType.IMAGE
        assert config.models["tts-model"].generation_type == GenerationType.TTS
        assert config.models["stt-model"].generation_type == GenerationType.STT
    finally:
        Path(temp_path).unlink()


def test_get_model_returns_config_with_generation_type() -> None:
    """Тест get_model() возвращает конфиг с generation_type."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        config = load_yaml_config(temp_path)
        model = config.get_model("test-model")

        assert model is not None
        assert model.generation_type == GenerationType.CHAT
    finally:
        Path(temp_path).unlink()


def test_get_model_returns_none_for_unknown_model() -> None:
    """Тест get_model() возвращает None для неизвестной модели."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        config = load_yaml_config(temp_path)
        model = config.get_model("nonexistent-model")

        assert model is None
    finally:
        Path(temp_path).unlink()


def test_generation_type_error_message_contains_available_types() -> None:
    """Тест: сообщение об ошибке валидации содержит список доступных типов."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: video
    price_tokens: 10
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        with pytest.raises(ValidationError) as exc_info:
            load_yaml_config(temp_path)

        error_message = str(exc_info.value)
        # Проверяем что сообщение содержит неправильный тип
        assert "video" in error_message.lower()
        # Проверяем что сообщение содержит доступные типы
        assert "chat" in error_message
        assert "image" in error_message
        assert "tts" in error_message
        assert "stt" in error_message
    finally:
        Path(temp_path).unlink()


def test_model_config_missing_required_field_raises_error() -> None:
    """Тест: отсутствие обязательного поля generation_type вызывает ошибку."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    price_tokens: 10
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        with pytest.raises(ValidationError) as exc_info:
            load_yaml_config(temp_path)

        error_message = str(exc_info.value)
        assert "generation_type" in error_message.lower()
    finally:
        Path(temp_path).unlink()


def test_model_config_negative_price_tokens_raises_error() -> None:
    """Тест: отрицательное значение price_tokens вызывает ошибку."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: -10
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        with pytest.raises(ValidationError) as exc_info:
            load_yaml_config(temp_path)

        error_message = str(exc_info.value)
        assert "price_tokens" in error_message.lower()
    finally:
        Path(temp_path).unlink()


def test_model_config_both_providers_accepted() -> None:
    """Тест: оба провайдера (openrouter и routerai) принимаются."""
    yaml_content = """
models:
  openrouter-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10

  routerai-model:
    provider: routerai
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        config = load_yaml_config(temp_path)

        assert len(config.models) == 2
        assert config.models["openrouter-model"].provider == "openrouter"
        assert config.models["routerai-model"].provider == "routerai"
    finally:
        Path(temp_path).unlink()


def test_model_config_custom_provider_accepted() -> None:
    """Тест: провайдер теперь строка, любой провайдер принимается.

    Валидация провайдера происходит в runtime через реестр,
    а не на этапе загрузки конфига.
    """
    yaml_content = """
models:
  test-model:
    provider: custom_provider
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        # Любой провайдер принимается как строка
        config = load_yaml_config(temp_path)

        assert len(config.models) == 1
        assert config.models["test-model"].provider == "custom_provider"
    finally:
        Path(temp_path).unlink()


def test_model_config_optional_fields_work() -> None:
    """Тест: опциональные поля (display_name, description, params) работают."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10
    display_name: "GPT-4o"
    description: "Самая умная модель OpenAI"
    params:
      max_tokens: 4096
      temperature: 0.7
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        config = load_yaml_config(temp_path)
        model = config.models["test-model"]

        assert model.display_name == "GPT-4o"
        assert model.description == "Самая умная модель OpenAI"
        assert model.params["max_tokens"] == 4096
        assert model.params["temperature"] == 0.7
    finally:
        Path(temp_path).unlink()


def test_cost_config_chat_model() -> None:
    """Тест: CostConfig для chat-модели с токенами в рублях."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10
    cost:
      input_tokens_rub_per_1k: 0.50
      output_tokens_rub_per_1k: 1.50
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        config = load_yaml_config(temp_path)
        model = config.models["test-model"]

        assert model.cost.input_tokens_rub_per_1k == 0.50
        assert model.cost.output_tokens_rub_per_1k == 1.50
        assert model.cost.per_request_rub is None
    finally:
        Path(temp_path).unlink()


def test_cost_config_image_model() -> None:
    """Тест: CostConfig для image-модели с фиксированной ценой в рублях."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/dall-e-3
    generation_type: image
    price_tokens: 50
    cost:
      per_request_rub: 4.00
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        config = load_yaml_config(temp_path)
        model = config.models["test-model"]

        assert model.cost.per_request_rub == 4.00
        assert model.cost.input_tokens_rub_per_1k is None
        assert model.cost.output_tokens_rub_per_1k is None
    finally:
        Path(temp_path).unlink()


def test_cost_config_negative_value_raises_error() -> None:
    """Тест: отрицательное значение в CostConfig вызывает ошибку."""
    yaml_content = """
models:
  test-model:
    provider: openrouter
    model_id: openai/gpt-4o
    generation_type: chat
    price_tokens: 10
    cost:
      input_tokens_rub_per_1k: -0.50
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        with pytest.raises(ValidationError) as exc_info:
            load_yaml_config(temp_path)

        error_message = str(exc_info.value)
        assert "input_tokens_rub_per_1k" in error_message.lower()
    finally:
        Path(temp_path).unlink()


def test_load_yaml_config_nonexistent_file_returns_empty_config() -> None:
    """Тест: загрузка несуществующего файла возвращает пустой конфиг."""
    config = load_yaml_config("nonexistent_file.yaml")

    assert len(config.models) == 0
    assert config.generation_timeouts.chat == 60
    assert config.limits.max_parallel_tasks_per_user == 2


def test_load_yaml_config_empty_models_dict() -> None:
    """Тест: пустой словарь models обрабатывается корректно."""
    yaml_content = """
models: {}
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(yaml_content)
        temp_path = f.name

    try:
        config = load_yaml_config(temp_path)

        assert len(config.models) == 0
        assert isinstance(config.models, dict)
    finally:
        Path(temp_path).unlink()


def test_cost_config_calculate_per_request_pricing() -> None:
    """Тест: calculate() возвращает фиксированную стоимость для per_request моделей."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    # Создаём конфигурацию с фиксированной стоимостью
    cost_config = CostConfig(per_request_rub=4.00)

    # Вызываем calculate без usage
    result = cost_config.calculate()

    # Проверяем что вернулась правильная стоимость
    assert result == Decimal("4.0000")
    assert isinstance(result, Decimal)


def test_cost_config_calculate_per_request_ignores_usage() -> None:
    """Тест: calculate() игнорирует usage для per_request моделей."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(per_request_rub=0.81)

    # Передаём usage, но он должен игнорироваться
    usage = {"prompt_tokens": 1000, "completion_tokens": 2000}
    result = cost_config.calculate(usage)

    # Стоимость должна быть фиксированной, а не рассчитанной по токенам
    assert result == Decimal("0.8100")


def test_cost_config_calculate_token_based_pricing() -> None:
    """Тест: calculate() рассчитывает стоимость по токенам для chat моделей."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    usage = {"prompt_tokens": 100, "completion_tokens": 200}
    result = cost_config.calculate(usage)

    # (100 / 1000 * 0.50) + (200 / 1000 * 1.50) = 0.05 + 0.30 = 0.35
    expected = Decimal("0.3500")
    assert result == expected


def test_cost_config_calculate_token_based_no_usage_returns_zero() -> None:
    """Тест: calculate() возвращает 0 если usage=None для токен-модели."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    # Вызываем без usage
    result = cost_config.calculate(None)

    assert result == Decimal(0)


def test_cost_config_calculate_token_based_empty_usage_returns_zero() -> None:
    """Тест: calculate() возвращает 0 если usage пустой."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    # Пустой usage (без токенов)
    result = cost_config.calculate({})

    assert result == Decimal(0)


def test_cost_config_calculate_token_based_only_input_tokens() -> None:
    """Тест: calculate() корректно считает только входящие токены."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    # Только входящие токены
    usage = {"prompt_tokens": 1000, "completion_tokens": 0}
    result = cost_config.calculate(usage)

    # 1000 / 1000 * 0.50 = 0.50
    assert result == Decimal("0.5000")


def test_cost_config_calculate_token_based_only_output_tokens() -> None:
    """Тест: calculate() корректно считает только исходящие токены."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    # Только исходящие токены
    usage = {"prompt_tokens": 0, "completion_tokens": 1000}
    result = cost_config.calculate(usage)

    # 1000 / 1000 * 1.50 = 1.50
    assert result == Decimal("1.5000")


def test_cost_config_calculate_token_based_missing_prompt_tokens() -> None:
    """Тест: calculate() обрабатывает отсутствие prompt_tokens."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    # usage без prompt_tokens
    usage = {"completion_tokens": 200}
    result = cost_config.calculate(usage)

    # Считаем только completion_tokens: 200 / 1000 * 1.50 = 0.30
    assert result == Decimal("0.3000")


def test_cost_config_calculate_token_based_missing_completion_tokens() -> None:
    """Тест: calculate() обрабатывает отсутствие completion_tokens."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    # usage без completion_tokens
    usage = {"prompt_tokens": 100}
    result = cost_config.calculate(usage)

    # Считаем только prompt_tokens: 100 / 1000 * 0.50 = 0.05
    assert result == Decimal("0.0500")


def test_cost_config_calculate_no_config_returns_zero() -> None:
    """Тест: calculate() возвращает 0 если конфигурация пустая."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    # Пустая конфигурация (все поля None)
    cost_config = CostConfig()

    usage = {"prompt_tokens": 100, "completion_tokens": 200}
    result = cost_config.calculate(usage)

    assert result == Decimal(0)


def test_cost_config_calculate_only_input_tokens_config() -> None:
    """Тест: calculate() работает если задан только input_tokens_rub_per_1k."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    # Конфигурация только с входящими токенами
    cost_config = CostConfig(input_tokens_rub_per_1k=0.50)

    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    result = cost_config.calculate(usage)

    # Считаем только входящие токены, исходящие игнорируем
    # 1000 / 1000 * 0.50 = 0.50
    assert result == Decimal("0.5000")


def test_cost_config_calculate_only_output_tokens_config() -> None:
    """Тест: calculate() работает если задан только output_tokens_rub_per_1k."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    # Конфигурация только с исходящими токенами
    cost_config = CostConfig(output_tokens_rub_per_1k=1.50)

    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    result = cost_config.calculate(usage)

    # Считаем только исходящие токены, входящие игнорируем
    # 500 / 1000 * 1.50 = 0.75
    assert result == Decimal("0.7500")


def test_cost_config_calculate_rounding() -> None:
    """Тест: calculate() правильно округляет до 4 знаков после запятой."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.123456,
        output_tokens_rub_per_1k=0.987654,
    )

    usage = {"prompt_tokens": 333, "completion_tokens": 777}
    result = cost_config.calculate(usage)

    # (333 / 1000 * 0.123456) + (777 / 1000 * 0.987654)
    # = 0.04109065 + 0.76740606 = 0.80849671
    # Округляем до 4 знаков: 0.8085
    expected = Decimal("0.8085")
    assert result == expected


def test_cost_config_calculate_priority_per_request_over_tokens() -> None:
    """Тест: per_request_rub имеет приоритет над токен-ценами."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    # Конфигурация с ОБОИМИ типами цен
    cost_config = CostConfig(
        per_request_rub=5.00,
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    usage = {"prompt_tokens": 1000, "completion_tokens": 1000}
    result = cost_config.calculate(usage)

    # Должна вернуться per_request_rub, а не расчёт по токенам
    assert result == Decimal("5.0000")


def test_cost_config_calculate_large_token_counts() -> None:
    """Тест: calculate() корректно обрабатывает большие количества токенов."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    # Большое количество токенов
    usage = {"prompt_tokens": 100000, "completion_tokens": 50000}
    result = cost_config.calculate(usage)

    # (100000 / 1000 * 0.50) + (50000 / 1000 * 1.50) = 50 + 75 = 125
    assert result == Decimal("125.0000")


def test_cost_config_calculate_fractional_tokens() -> None:
    """Тест: calculate() корректно обрабатывает дробные значения токенов."""
    from decimal import Decimal

    from src.config.yaml_config import CostConfig

    cost_config = CostConfig(
        input_tokens_rub_per_1k=0.50,
        output_tokens_rub_per_1k=1.50,
    )

    # Небольшое количество токенов (меньше 1000)
    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    result = cost_config.calculate(usage)

    # (10 / 1000 * 0.50) + (5 / 1000 * 1.50) = 0.005 + 0.0075 = 0.0125
    expected = Decimal("0.0125")
    assert result == expected
