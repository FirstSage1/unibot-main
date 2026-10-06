"""Клавиатуры для Telegram-бота.

Этот модуль экспортирует все клавиатуры из подмодулей для удобного импорта.

Структура:
    keyboards/
    ├── __init__.py          # Этот файл (экспорты)
    └── inline/              # Inline-клавиатуры (кнопки под сообщениями)
        ├── models.py        # Выбор AI-модели
        └── language.py      # Выбор языка

Примеры использования:
    # Импорт из корня keyboards (рекомендуется)
    from src.bot.keyboards import create_model_selection_keyboard
    from src.bot.keyboards import create_language_keyboard

    # Импорт напрямую из inline (альтернативный вариант)
    from src.bot.keyboards.inline import create_model_selection_keyboard
"""

from src.bot.keyboards.inline import (
    create_language_keyboard,
    create_model_selection_keyboard,
)

__all__ = [
    "create_language_keyboard",
    "create_model_selection_keyboard",
]
