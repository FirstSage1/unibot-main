"""Клавиатура для выбора языка интерфейса.

Генерирует inline-клавиатуру с кнопками выбора языка.
Каждая кнопка показывает локализованное название языка с флагом.

Используется в handlers /language и /settings.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.utils.i18n import Localization
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Префикс по умолчанию для callback_data выбора языка
DEFAULT_LANGUAGE_CALLBACK_PREFIX = "lang:"


def create_language_keyboard(
    l10n: Localization,
    callback_prefix: str = DEFAULT_LANGUAGE_CALLBACK_PREFIX,
) -> InlineKeyboardMarkup:
    """Создать inline-клавиатуру с доступными языками.

    Получает список языков из конфигурации и создаёт кнопку для каждого.
    Названия языков берутся из локализации (например "🇷🇺 Русский").

    Args:
        l10n: Объект локализации для получения названий языков.
        callback_prefix: Префикс для callback_data кнопок.
            По умолчанию "lang:" — формат "lang:ru", "lang:en".
            Можно указать другой префикс для использования в разных контекстах
            (например, "settings_lang:" для настроек).

    Returns:
        InlineKeyboardMarkup с кнопками выбора языка.

    Example:
        >>> keyboard = create_language_keyboard(l10n)
        >>> await message.answer("Выберите язык:", reply_markup=keyboard)

        >>> # С кастомным префиксом для настроек
        >>> keyboard = create_language_keyboard(l10n, callback_prefix="settings_lang:")
    """
    # Получаем список доступных языков из конфига
    available_languages = Localization.get_available_languages()

    # Создаём кнопки для каждого языка
    buttons: list[list[InlineKeyboardButton]] = []

    for lang_code in available_languages:
        # Получаем локализованное название языка
        # Например: "language_name_ru" → "🇷🇺 Русский"
        language_name = l10n.get(f"language_name_{lang_code}")

        # Создаём кнопку
        # callback_data: "{prefix}{lang_code}" (напр. "lang:ru", "settings_lang:ru")
        button = InlineKeyboardButton(
            text=language_name,
            callback_data=f"{callback_prefix}{lang_code}",
        )

        # Каждая кнопка на отдельной строке (по 1 кнопке в ряду)
        buttons.append([button])

    logger.debug(
        "Создана клавиатура выбора языка: языков=%d, prefix=%s",
        len(available_languages),
        callback_prefix,
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)
