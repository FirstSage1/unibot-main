"""Inline-клавиатуры для Telegram-бота.

Inline-клавиатуры отображаются под сообщениями и позволяют пользователю
выбирать опции без отправки текстовых команд.

Структура:
- models.py — клавиатура выбора AI-модели (используется в chatgpt, imagine, edit)
- language.py — клавиатура выбора языка интерфейса
"""

from src.bot.keyboards.inline.language import create_language_keyboard
from src.bot.keyboards.inline.models import create_model_selection_keyboard

__all__ = [
    "create_language_keyboard",
    "create_model_selection_keyboard",
]
