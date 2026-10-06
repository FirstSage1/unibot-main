"""Клавиатуры для платежей.

Этот модуль содержит функции для создания inline-клавиатур:
- Выбор тарифа (tokens_100, tokens_500 и т.д.)
- Выбор способа оплаты (Telegram Stars, YooKassa, Stripe)

Используется в handlers:
- buy — логика покупки (через callback)
- /balance — показ баланса с кнопкой пополнения
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config.yaml_config import TariffConfig
from src.utils.logging import get_logger

logger = get_logger(__name__)


def create_tariff_selection_keyboard(
    tariffs: list[TariffConfig],
    language: str = "ru",
) -> InlineKeyboardMarkup:
    """Создать клавиатуру для выбора тарифа.

    Показывает включённые тарифы с названием и количеством токенов.

    Args:
        tariffs: Список тарифов из config.yaml.
        language: Код языка для локализации названий.

    Returns:
        Inline-клавиатура с кнопками тарифов.

    Example:
        >>> from src.config.yaml_config import yaml_config
        >>> tariffs = yaml_config.get_enabled_tariffs()
        >>> keyboard = create_tariff_selection_keyboard(tariffs, "ru")
        >>> await message.answer("Выберите тариф:", reply_markup=keyboard)
    """
    if not tariffs:
        logger.warning("Нет доступных тарифов")
        return InlineKeyboardMarkup(inline_keyboard=[])

    buttons: list[list[InlineKeyboardButton]] = []

    for tariff in tariffs:
        # Получаем название на нужном языке
        name = tariff.name.get(language)
        tokens = tariff.effective_tokens

        # Формируем текст кнопки в зависимости от типа тарифа
        if tariff.is_subscription:
            # Подписка: "💎 Стартовая подписка — 100/мес"
            period_suffix = "/мес" if language == "ru" else "/mo"
            button_text = f"💎 {name} — {tokens}{period_suffix}"
        else:
            # Разовая покупка: "💎 100 — Стартовый"
            button_text = f"💎 {tokens} — {name}"

        # callback_data: "tariff:{slug}"
        button = InlineKeyboardButton(
            text=button_text,
            callback_data=f"tariff:{tariff.slug}",
        )
        buttons.append([button])

    logger.debug(
        "Создана клавиатура выбора тарифа: %d тарифов",
        len(tariffs),
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_provider_selection_keyboard(
    tariff: TariffConfig,
    available_providers: list[str],
    language: str = "ru",
) -> InlineKeyboardMarkup:
    """Создать клавиатуру для выбора способа оплаты.

    Показывает только провайдеры, которые:
    1. Настроены (есть API-ключи)
    2. Имеют цену для выбранного тарифа

    Args:
        tariff: Выбранный тариф.
        available_providers: Список настроенных провайдеров.
        language: Код языка для локализации.

    Returns:
        Inline-клавиатура с кнопками провайдеров.

    Example:
        >>> tariff = yaml_config.get_tariff("tokens_100")
        >>> providers = ["telegram_stars", "yookassa"]
        >>> keyboard = create_provider_selection_keyboard(tariff, providers, "ru")
    """
    # Названия провайдеров для отображения
    provider_names = {
        "telegram_stars": "⭐ Telegram Stars",
        "yookassa": "💳 YooKassa (RUB)",
        "stripe": "💳 Stripe (USD)",
    }

    # Символы валют
    currency_symbols = {
        "RUB": "₽",
        "USD": "$",
        "EUR": "€",
        "XTR": "⭐",
    }

    buttons: list[list[InlineKeyboardButton]] = []

    for provider in available_providers:
        # Проверяем, есть ли цена для этого провайдера
        if not tariff.is_available_for_provider(provider):
            continue

        price = tariff.get_price_for_provider(provider)
        currency = tariff.get_currency_for_provider(provider)

        if price is None or currency is None:
            continue

        # Формируем текст кнопки
        provider_display = provider_names.get(provider, provider)
        currency_symbol = currency_symbols.get(currency, currency)

        # Для Stars показываем без дроби
        if currency == "XTR":
            price_text = f"{int(price)} {currency_symbol}"
        else:
            price_text = f"{price} {currency_symbol}"

        button_text = f"{provider_display} — {price_text}"

        # callback_data: "pay:{tariff_slug}:{provider}"
        button = InlineKeyboardButton(
            text=button_text,
            callback_data=f"pay:{tariff.slug}:{provider}",
        )
        buttons.append([button])

    # Кнопка "Назад" к выбору тарифа
    back_button = InlineKeyboardButton(
        text="◀️ Назад" if language == "ru" else "◀️ Back",
        callback_data="buy:back",
    )
    buttons.append([back_button])

    logger.debug(
        "Создана клавиатура выбора провайдера: tariff=%s, providers=%d",
        tariff.slug,
        len(buttons) - 1,  # Минус кнопка "Назад"
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def create_buy_button_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    """Создать клавиатуру с кнопкой покупки токенов.

    Используется в /balance для быстрого перехода к покупке.

    Args:
        language: Код языка для локализации.

    Returns:
        Inline-клавиатура с кнопкой "Купить токены".
    """
    button_text = "💰 Купить токены" if language == "ru" else "💰 Buy tokens"

    button = InlineKeyboardButton(
        text=button_text,
        callback_data="buy:start",
    )

    return InlineKeyboardMarkup(inline_keyboard=[[button]])
