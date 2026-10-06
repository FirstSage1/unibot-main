"""Тесты для inline-клавиатур юридических документов.

Модуль тестирует:
- create_legal_documents_keyboard (клавиатура со ссылками на документы)
- create_terms_acceptance_keyboard (клавиатура для запроса согласия)

Тестируемая функциональность:
1. Клавиатура содержит правильное количество кнопок
2. Кнопки содержат правильные URL-адреса
3. Кнопки содержат правильные тексты из локализации
4. Клавиатура для согласия содержит callback-кнопку
5. Локализация работает для обоих языков (ru, en)
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.keyboards.inline.legal import (
    create_legal_documents_keyboard,
    create_terms_acceptance_keyboard,
)
from src.utils.i18n import Localization

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def mock_l10n_ru() -> MagicMock:
    """Мок Localization для русского языка."""
    l10n = MagicMock(spec=Localization)
    l10n.language = "ru"

    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "legal_privacy_policy_button": "🔒 Политика конфиденциальности",
            "legal_terms_of_service_button": "📋 Пользовательское соглашение",
            "legal_accept_button": "✅ Принимаю условия",
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    l10n.get.side_effect = get_translation
    return l10n


@pytest.fixture
def mock_l10n_en() -> MagicMock:
    """Мок Localization для английского языка."""
    l10n = MagicMock(spec=Localization)
    l10n.language = "en"

    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "legal_privacy_policy_button": "🔒 Privacy Policy",
            "legal_terms_of_service_button": "📋 Terms of Service",
            "legal_accept_button": "✅ I Accept the Terms",
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    l10n.get.side_effect = get_translation
    return l10n


# ==============================================================================
# ТЕСТЫ create_legal_documents_keyboard
# ==============================================================================


def test_create_legal_documents_keyboard_returns_markup(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: create_legal_documents_keyboard возвращает InlineKeyboardMarkup."""
    result = create_legal_documents_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    assert isinstance(result, InlineKeyboardMarkup)


def test_create_legal_documents_keyboard_has_two_buttons(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: клавиатура содержит 2 кнопки (Privacy Policy и Terms of Service)."""
    keyboard = create_legal_documents_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    # Проверяем что есть 2 ряда кнопок
    assert len(keyboard.inline_keyboard) == 2

    # Проверяем что в каждом ряду по 1 кнопке
    assert len(keyboard.inline_keyboard[0]) == 1
    assert len(keyboard.inline_keyboard[1]) == 1


def test_create_legal_documents_keyboard_privacy_policy_button(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: кнопка Privacy Policy содержит правильный URL и текст."""
    keyboard = create_legal_documents_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    privacy_button = keyboard.inline_keyboard[0][0]

    assert isinstance(privacy_button, InlineKeyboardButton)
    assert privacy_button.text == "🔒 Политика конфиденциальности"
    assert privacy_button.url == "https://example.com/privacy"


def test_create_legal_documents_keyboard_terms_button(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: кнопка Terms of Service содержит правильный URL и текст."""
    keyboard = create_legal_documents_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    terms_button = keyboard.inline_keyboard[1][0]

    assert isinstance(terms_button, InlineKeyboardButton)
    assert terms_button.text == "📋 Пользовательское соглашение"
    assert terms_button.url == "https://example.com/terms"


def test_create_legal_documents_keyboard_localization_en(
    mock_l10n_en: MagicMock,
) -> None:
    """Тест: клавиатура использует английскую локализацию."""
    keyboard = create_legal_documents_keyboard(
        l10n=mock_l10n_en,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    privacy_button = keyboard.inline_keyboard[0][0]
    terms_button = keyboard.inline_keyboard[1][0]

    assert "Privacy Policy" in privacy_button.text
    assert "Terms of Service" in terms_button.text


def test_create_legal_documents_keyboard_with_different_urls(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: клавиатура работает с различными URL-адресами."""
    keyboard = create_legal_documents_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://docs.google.com/privacy",
        terms_of_service_url="https://docs.google.com/terms",
    )

    privacy_button = keyboard.inline_keyboard[0][0]
    terms_button = keyboard.inline_keyboard[1][0]

    assert privacy_button.url == "https://docs.google.com/privacy"
    assert terms_button.url == "https://docs.google.com/terms"


# ==============================================================================
# ТЕСТЫ create_terms_acceptance_keyboard
# ==============================================================================


def test_create_terms_acceptance_keyboard_returns_markup(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: create_terms_acceptance_keyboard возвращает InlineKeyboardMarkup."""
    result = create_terms_acceptance_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    assert isinstance(result, InlineKeyboardMarkup)


def test_create_terms_acceptance_keyboard_has_three_buttons(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: клавиатура содержит 3 кнопки (Privacy, Terms, Accept)."""
    keyboard = create_terms_acceptance_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    # Проверяем что есть 3 ряда кнопок
    assert len(keyboard.inline_keyboard) == 3

    # Проверяем что в каждом ряду по 1 кнопке
    assert len(keyboard.inline_keyboard[0]) == 1
    assert len(keyboard.inline_keyboard[1]) == 1
    assert len(keyboard.inline_keyboard[2]) == 1


def test_create_terms_acceptance_keyboard_privacy_button(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: кнопка Privacy Policy содержит правильный URL и текст."""
    keyboard = create_terms_acceptance_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    privacy_button = keyboard.inline_keyboard[0][0]

    assert isinstance(privacy_button, InlineKeyboardButton)
    assert privacy_button.text == "🔒 Политика конфиденциальности"
    assert privacy_button.url == "https://example.com/privacy"


def test_create_terms_acceptance_keyboard_terms_button(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: кнопка Terms of Service содержит правильный URL и текст."""
    keyboard = create_terms_acceptance_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    terms_button = keyboard.inline_keyboard[1][0]

    assert isinstance(terms_button, InlineKeyboardButton)
    assert terms_button.text == "📋 Пользовательское соглашение"
    assert terms_button.url == "https://example.com/terms"


def test_create_terms_acceptance_keyboard_accept_button(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: кнопка Accept содержит правильный callback_data и текст."""
    keyboard = create_terms_acceptance_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    accept_button = keyboard.inline_keyboard[2][0]

    assert isinstance(accept_button, InlineKeyboardButton)
    assert accept_button.text == "✅ Принимаю условия"
    assert accept_button.callback_data == "legal:accept"


def test_create_terms_acceptance_keyboard_accept_button_is_callback(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: кнопка Accept использует callback_data, а не URL."""
    keyboard = create_terms_acceptance_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    accept_button = keyboard.inline_keyboard[2][0]

    # Проверяем что у кнопки есть callback_data
    assert accept_button.callback_data is not None

    # Проверяем что у кнопки НЕТ url
    assert accept_button.url is None


def test_create_terms_acceptance_keyboard_localization_en(
    mock_l10n_en: MagicMock,
) -> None:
    """Тест: клавиатура использует английскую локализацию."""
    keyboard = create_terms_acceptance_keyboard(
        l10n=mock_l10n_en,
        privacy_policy_url="https://example.com/privacy",
        terms_of_service_url="https://example.com/terms",
    )

    privacy_button = keyboard.inline_keyboard[0][0]
    terms_button = keyboard.inline_keyboard[1][0]
    accept_button = keyboard.inline_keyboard[2][0]

    assert "Privacy Policy" in privacy_button.text
    assert "Terms of Service" in terms_button.text
    assert "I Accept" in accept_button.text


def test_create_terms_acceptance_keyboard_with_different_urls(
    mock_l10n_ru: MagicMock,
) -> None:
    """Тест: клавиатура работает с различными URL-адресами."""
    keyboard = create_terms_acceptance_keyboard(
        l10n=mock_l10n_ru,
        privacy_policy_url="https://docs.google.com/privacy",
        terms_of_service_url="https://docs.google.com/terms",
    )

    privacy_button = keyboard.inline_keyboard[0][0]
    terms_button = keyboard.inline_keyboard[1][0]

    assert privacy_button.url == "https://docs.google.com/privacy"
    assert terms_button.url == "https://docs.google.com/terms"
