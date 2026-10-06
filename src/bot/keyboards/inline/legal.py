"""Клавиатуры для юридических документов.

Содержит inline-клавиатуры для:
- Отображения ссылок на документы
- Запроса согласия с условиями использования

Используется в handlers/terms.py и handlers/start.py
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.utils.i18n import Localization
from src.utils.logging import get_logger

logger = get_logger(__name__)


def create_legal_documents_keyboard(
    l10n: Localization,
    privacy_policy_url: str,
    terms_of_service_url: str,
) -> InlineKeyboardMarkup:
    """Создать клавиатуру со ссылками на юридические документы.

    Используется командой /terms для отображения документов.

    Args:
        l10n: Объект локализации.
        privacy_policy_url: Ссылка на Политику конфиденциальности.
        terms_of_service_url: Ссылка на Пользовательское соглашение.

    Returns:
        InlineKeyboardMarkup с кнопками-ссылками на документы.
    """
    buttons = [
        # Политика конфиденциальности
        [
            InlineKeyboardButton(
                text=l10n.get("legal_privacy_policy_button"),
                url=privacy_policy_url,
            )
        ],
        # Пользовательское соглашение (оферта)
        [
            InlineKeyboardButton(
                text=l10n.get("legal_terms_of_service_button"),
                url=terms_of_service_url,
            )
        ],
    ]

    logger.debug("Создана клавиатура с юридическими документами")

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_terms_acceptance_keyboard(
    l10n: Localization,
    privacy_policy_url: str,
    terms_of_service_url: str,
) -> InlineKeyboardMarkup:
    """Создать клавиатуру для запроса согласия с документами.

    Показывается новым пользователям при первом /start или при
    обновлении версии документов. Содержит ссылки на документы
    и кнопку «Принимаю».

    Args:
        l10n: Объект локализации.
        privacy_policy_url: Ссылка на Политику конфиденциальности.
        terms_of_service_url: Ссылка на Пользовательское соглашение.

    Returns:
        InlineKeyboardMarkup с кнопками ссылок и кнопкой согласия.
    """
    buttons = [
        # Политика конфиденциальности
        [
            InlineKeyboardButton(
                text=l10n.get("legal_privacy_policy_button"),
                url=privacy_policy_url,
            )
        ],
        # Пользовательское соглашение (оферта)
        [
            InlineKeyboardButton(
                text=l10n.get("legal_terms_of_service_button"),
                url=terms_of_service_url,
            )
        ],
        # Кнопка «Принимаю»
        [
            InlineKeyboardButton(
                text=l10n.get("legal_accept_button"),
                callback_data="legal:accept",
            )
        ],
    ]

    logger.debug("Создана клавиатура для запроса согласия с документами")

    return InlineKeyboardMarkup(inline_keyboard=buttons)
