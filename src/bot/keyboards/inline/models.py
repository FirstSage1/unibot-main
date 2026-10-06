"""Клавиатура для выбора AI-моделей.

Генерирует inline-клавиатуру с кнопками выбора модели.
Каждая кнопка содержит название модели и стоимость в токенах.

Используется в handlers:
- /chatgpt — выбор chat-модели (GPT-4o, Claude и др.)
- /imagine — выбор image-модели (DALL-E, FLUX и др.)
- /edit — выбор модели для редактирования (Gemini и др.)
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config.yaml_config import ModelConfig
from src.utils.logging import get_logger

logger = get_logger(__name__)


def create_model_selection_keyboard(
    models: dict[str, ModelConfig],
    generation_type: str,
) -> InlineKeyboardMarkup:
    """Создать клавиатуру для выбора модели.

    Фильтрует модели по типу генерации и создаёт кнопки с:
    - Названием модели (display_name)
    - Стоимостью в токенах (price_tokens)

    Кнопки отображаются по 1-2 в ряду для удобства нажатия.

    Args:
        models: Словарь моделей из config.yaml (ключ → ModelConfig).
        generation_type: Тип генерации для фильтрации
            (chat, image, tts, stt, image_edit).

    Returns:
        Inline-клавиатура с кнопками выбора модели.

    Example:
        >>> from src.config.yaml_config import yaml_config
        >>> keyboard = create_model_selection_keyboard(yaml_config.models, "chat")
        >>> await message.answer("Выберите модель:", reply_markup=keyboard)
    """
    # Фильтруем модели по типу генерации
    # models — это dict[str, ModelConfig], где ключ — это model_key
    filtered_models: list[tuple[str, ModelConfig]] = [
        (model_key, config)
        for model_key, config in models.items()
        if config.generation_type == generation_type
    ]

    if not filtered_models:
        logger.warning(
            "Нет моделей для типа генерации: %s",
            generation_type,
        )
        # Возвращаем пустую клавиатуру
        return InlineKeyboardMarkup(inline_keyboard=[])

    # Создаём кнопки
    buttons: list[list[InlineKeyboardButton]] = []

    for model_key, config in filtered_models:
        # Текст кнопки: "GPT-4o (15 токенов)"
        display_name = config.display_name or model_key
        button_text = f"{display_name} ({config.price_tokens} 💎)"

        # callback_data — данные, которые вернутся при нажатии кнопки
        # Формат: "model:{model_key}" для обработки в handler
        button = InlineKeyboardButton(
            text=button_text,
            callback_data=f"model:{model_key}",
        )

        # Добавляем кнопку в отдельный ряд
        # В будущем можно группировать по 2 кнопки в ряд для компактности
        buttons.append([button])

    logger.debug(
        "Создана клавиатура выбора модели: type=%s, моделей=%d",
        generation_type,
        len(filtered_models),
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)
