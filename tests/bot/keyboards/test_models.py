"""Тесты для клавиатуры выбора моделей.

Проверяют корректность генерации inline-клавиатур для выбора AI-моделей.
"""

from aiogram.types import InlineKeyboardMarkup

from src.bot.keyboards.inline.models import create_model_selection_keyboard
from src.config.yaml_config import ModelConfig


class TestCreateModelSelectionKeyboard:
    """Тесты для функции create_model_selection_keyboard."""

    def test_keyboard_with_single_model(self) -> None:
        """Проверить создание клавиатуры с одной моделью."""
        # Arrange
        models = {
            "gpt-4o": ModelConfig(
                provider="openai",
                model_id="openai/gpt-4o",
                generation_type="chat",
                display_name="GPT-4o",
                price_tokens=15,
            ),
        }

        # Act
        keyboard = create_model_selection_keyboard(models, "chat")

        # Assert
        assert isinstance(keyboard, InlineKeyboardMarkup)
        assert len(keyboard.inline_keyboard) == 1
        assert len(keyboard.inline_keyboard[0]) == 1

        button = keyboard.inline_keyboard[0][0]
        assert button.text == "GPT-4o (15 💎)"
        assert button.callback_data == "model:gpt-4o"

    def test_keyboard_with_multiple_models(self) -> None:
        """Проверить создание клавиатуры с несколькими моделями."""
        # Arrange
        models = {
            "gpt-4o": ModelConfig(
                provider="openai",
                model_id="openai/gpt-4o",
                generation_type="chat",
                display_name="GPT-4o",
                price_tokens=15,
            ),
            "gpt-4o-mini": ModelConfig(
                provider="openai",
                model_id="openai/gpt-4o-mini",
                generation_type="chat",
                display_name="GPT-4o Mini",
                price_tokens=5,
            ),
            "claude-3-5-sonnet": ModelConfig(
                provider="anthropic",
                model_id="anthropic/claude-3-5-sonnet",
                generation_type="chat",
                display_name="Claude 3.5 Sonnet",
                price_tokens=20,
            ),
        }

        # Act
        keyboard = create_model_selection_keyboard(models, "chat")

        # Assert
        assert len(keyboard.inline_keyboard) == 3

        # Проверяем каждую кнопку
        buttons = [row[0] for row in keyboard.inline_keyboard]

        assert buttons[0].text == "GPT-4o (15 💎)"
        assert buttons[0].callback_data == "model:gpt-4o"

        assert buttons[1].text == "GPT-4o Mini (5 💎)"
        assert buttons[1].callback_data == "model:gpt-4o-mini"

        assert buttons[2].text == "Claude 3.5 Sonnet (20 💎)"
        assert buttons[2].callback_data == "model:claude-3-5-sonnet"

    def test_keyboard_filters_by_generation_type(self) -> None:
        """Проверить фильтрацию моделей по типу генерации."""
        # Arrange
        models = {
            "gpt-4o": ModelConfig(
                provider="openai",
                model_id="openai/gpt-4o",
                generation_type="chat",
                display_name="GPT-4o",
                price_tokens=15,
            ),
            "dall-e-3": ModelConfig(
                provider="openai",
                model_id="openai/dall-e-3",
                generation_type="image",
                display_name="DALL-E 3",
                price_tokens=50,
            ),
            "whisper-1": ModelConfig(
                provider="openai",
                model_id="openai/whisper-1",
                generation_type="stt",
                display_name="Whisper",
                price_tokens=10,
            ),
        }

        # Act
        chat_keyboard = create_model_selection_keyboard(models, "chat")
        image_keyboard = create_model_selection_keyboard(models, "image")
        stt_keyboard = create_model_selection_keyboard(models, "stt")

        # Assert
        assert len(chat_keyboard.inline_keyboard) == 1
        assert chat_keyboard.inline_keyboard[0][0].text == "GPT-4o (15 💎)"

        assert len(image_keyboard.inline_keyboard) == 1
        assert image_keyboard.inline_keyboard[0][0].text == "DALL-E 3 (50 💎)"

        assert len(stt_keyboard.inline_keyboard) == 1
        assert stt_keyboard.inline_keyboard[0][0].text == "Whisper (10 💎)"

    def test_keyboard_returns_empty_if_no_matching_models(self) -> None:
        """Проверить, что возвращается пустая клавиатура если нет подходящих моделей."""
        # Arrange
        models = {
            "gpt-4o": ModelConfig(
                provider="openai",
                model_id="openai/gpt-4o",
                generation_type="chat",
                display_name="GPT-4o",
                price_tokens=15,
            ),
        }

        # Act
        keyboard = create_model_selection_keyboard(models, "image")

        # Assert
        assert isinstance(keyboard, InlineKeyboardMarkup)
        assert len(keyboard.inline_keyboard) == 0

    def test_keyboard_uses_model_key_if_display_name_missing(self) -> None:
        """Проверить, что используется model_key если display_name не указан."""
        # Arrange
        models = {
            "test-model": ModelConfig(
                provider="test",
                model_id="test/model",
                generation_type="chat",
                display_name=None,  # Не указан display_name
                price_tokens=10,
            ),
        }

        # Act
        keyboard = create_model_selection_keyboard(models, "chat")

        # Assert
        button = keyboard.inline_keyboard[0][0]
        assert button.text == "test-model (10 💎)"

    def test_keyboard_preserves_model_order(self) -> None:
        """Проверить, что порядок моделей сохраняется.

        Примечание: В Python 3.7+ dict сохраняет порядок вставки.
        """
        # Arrange
        models = {
            "model-a": ModelConfig(
                provider="test",
                model_id="test/model-a",
                generation_type="chat",
                display_name="Model A",
                price_tokens=10,
            ),
            "model-b": ModelConfig(
                provider="test",
                model_id="test/model-b",
                generation_type="chat",
                display_name="Model B",
                price_tokens=20,
            ),
            "model-c": ModelConfig(
                provider="test",
                model_id="test/model-c",
                generation_type="chat",
                display_name="Model C",
                price_tokens=30,
            ),
        }

        # Act
        keyboard = create_model_selection_keyboard(models, "chat")

        # Assert
        buttons = [row[0] for row in keyboard.inline_keyboard]
        assert buttons[0].callback_data == "model:model-a"
        assert buttons[1].callback_data == "model:model-b"
        assert buttons[2].callback_data == "model:model-c"

    def test_keyboard_with_empty_models_dict(self) -> None:
        """Проверить обработку пустого словаря моделей."""
        # Arrange
        models: dict[str, ModelConfig] = {}

        # Act
        keyboard = create_model_selection_keyboard(models, "chat")

        # Assert
        assert isinstance(keyboard, InlineKeyboardMarkup)
        assert len(keyboard.inline_keyboard) == 0

    def test_button_format_includes_price_and_emoji(self) -> None:
        """Проверить формат кнопки с ценой и эмодзи монеты."""
        # Arrange
        models = {
            "gpt-4o": ModelConfig(
                provider="openai",
                model_id="openai/gpt-4o",
                generation_type="chat",
                display_name="GPT-4o",
                price_tokens=15,
            ),
        }

        # Act
        keyboard = create_model_selection_keyboard(models, "chat")

        # Assert
        button = keyboard.inline_keyboard[0][0]
        assert "💎" in button.text  # Эмодзи монеты
        assert "15" in button.text  # Цена
        assert "GPT-4o" in button.text  # Название модели

    def test_callback_data_format(self) -> None:
        """Проверить формат callback_data для кнопок."""
        # Arrange
        models = {
            "test-model-123": ModelConfig(
                provider="test",
                model_id="test/model-123",
                generation_type="chat",
                display_name="Test Model",
                price_tokens=10,
            ),
        }

        # Act
        keyboard = create_model_selection_keyboard(models, "chat")

        # Assert
        button = keyboard.inline_keyboard[0][0]
        assert button.callback_data == "model:test-model-123"
        assert button.callback_data.startswith("model:")
