"""Тесты для обработчика покупки токенов.

Модуль тестирует:
- _send_stars_invoice — отправка invoice с поддержкой подписок
- successful_payment_handler — обработка успешной оплаты Stars
- Разные типы платежей: разовая покупка, первая подписка, продление

Тестируемая функциональность:
1. subscription_period=2592000 добавляется для подписок
2. Описание invoice зависит от типа тарифа (tokens vs tokens_per_period)
3. Разные сообщения для: первой покупки, активации подписки, продления
4. is_recurring и is_first_recurring корректно обрабатываются
5. Все данные передаются в PaymentService для обработки webhook
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import LabeledPrice, Message, SuccessfulPayment

from src.bot.handlers.buy import (
    _send_stars_invoice,
    successful_payment_handler,
)
from src.utils.i18n import Localization

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def mock_bot() -> MagicMock:
    """Мок Bot для отправки invoice."""
    bot = MagicMock(spec=Bot)
    bot.send_invoice = AsyncMock()
    return bot


@pytest.fixture
def mock_l10n() -> MagicMock:
    """Мок Localization."""
    l10n = MagicMock(spec=Localization)
    l10n.language = "ru"

    def get_translation(key: str, **kwargs: Any) -> str:
        translations = {
            "buy_invoice_description": "Разовая покупка {tokens} токенов",
            "buy_invoice_subscription_description": (
                "Подписка: {tokens} токенов каждые 30 дней"
            ),
            "buy_success": "✅ Токены начислены: {tokens}",
            "buy_subscription_activated": (
                "✅ Подписка активирована! Вы получили {tokens} токенов"
            ),
            "buy_subscription_renewed": (
                "✅ Подписка продлена! Начислено {tokens} токенов"
            ),
            "buy_payment_failed": "❌ Платёж не удался",
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    l10n.get.side_effect = get_translation
    return l10n


@pytest.fixture
def mock_tariff_one_time() -> MagicMock:
    """Мок разового тарифа (не подписка)."""
    tariff = MagicMock()
    tariff.slug = "tokens_1000"
    tariff.name.get.return_value = "1000 токенов"
    tariff.tokens = 1000
    tariff.tokens_per_period = None
    tariff.is_subscription = False
    tariff.effective_tokens = 1000
    return tariff


@pytest.fixture
def mock_tariff_subscription() -> MagicMock:
    """Мок тарифа-подписки."""
    tariff = MagicMock()
    tariff.slug = "pro_monthly"
    tariff.name.get.return_value = "Pro (месяц)"
    tariff.tokens = None  # Для подписок это общее количество, не используется
    tariff.tokens_per_period = 5000
    tariff.is_subscription = True
    tariff.effective_tokens = 5000
    return tariff


@pytest.fixture
def mock_payment_info() -> MagicMock:
    """Мок информации о платеже."""
    payment_info = MagicMock()
    payment_info.payment_id = "payment_123"
    payment_info.amount = 100  # В Stars
    payment_info.provider_payment_id = None  # Для Stars изначально None
    payload_json = '{"tariff_slug": "tokens_1000", "user_id": 123456789}'
    payment_info.metadata = {"invoice": MagicMock(payload=payload_json)}
    return payment_info


# ==============================================================================
# ТЕСТЫ _send_stars_invoice
# ==============================================================================


@pytest.mark.asyncio
async def test_send_stars_invoice_one_time_tariff(
    mock_bot: MagicMock,
    mock_tariff_one_time: MagicMock,
    mock_payment_info: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: для разового тарифа НЕ добавляется subscription_period."""
    await _send_stars_invoice(
        bot=mock_bot,
        chat_id=123456789,
        tariff=mock_tariff_one_time,
        payment_info=mock_payment_info,
        l10n=mock_l10n,
    )

    # Проверяем что send_invoice вызван
    mock_bot.send_invoice.assert_called_once()
    call_kwargs = mock_bot.send_invoice.call_args[1]

    # Проверяем базовые параметры
    assert call_kwargs["chat_id"] == 123456789
    assert call_kwargs["title"] == "1000 токенов"
    assert call_kwargs["currency"] == "XTR"
    assert call_kwargs["description"] == "Разовая покупка 1000 токенов"

    # КРИТИЧНО: subscription_period НЕ должен быть добавлен
    assert "subscription_period" not in call_kwargs


@pytest.mark.asyncio
async def test_send_stars_invoice_subscription_tariff(
    mock_bot: MagicMock,
    mock_tariff_subscription: MagicMock,
    mock_payment_info: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: для подписки используется create_invoice_link с subscription_period."""
    # Мокируем create_invoice_link чтобы возвращал ссылку
    mock_bot.create_invoice_link = AsyncMock(
        return_value="https://t.me/$test_invoice_link"
    )
    mock_bot.send_message = AsyncMock()

    await _send_stars_invoice(
        bot=mock_bot,
        chat_id=123456789,
        tariff=mock_tariff_subscription,
        payment_info=mock_payment_info,
        l10n=mock_l10n,
    )

    # Для подписок используется create_invoice_link, НЕ send_invoice
    mock_bot.send_invoice.assert_not_called()
    mock_bot.create_invoice_link.assert_called_once()

    call_kwargs = mock_bot.create_invoice_link.call_args[1]

    # Проверяем базовые параметры
    assert call_kwargs["title"] == "Pro (месяц)"
    assert call_kwargs["currency"] == "XTR"
    assert call_kwargs["description"] == "Подписка: 5000 токенов каждые 30 дней"

    # КРИТИЧНО: subscription_period должен быть = 30 дней в секундах
    assert call_kwargs["subscription_period"] == 2592000  # 30 * 24 * 60 * 60

    # Проверяем что ссылка отправлена пользователю
    mock_bot.send_message.assert_called_once()
    send_msg_kwargs = mock_bot.send_message.call_args[1]
    assert send_msg_kwargs["chat_id"] == 123456789


@pytest.mark.asyncio
async def test_send_stars_invoice_price_structure(
    mock_bot: MagicMock,
    mock_tariff_one_time: MagicMock,
    mock_payment_info: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: цена передаётся как LabeledPrice."""
    await _send_stars_invoice(
        bot=mock_bot,
        chat_id=123456789,
        tariff=mock_tariff_one_time,
        payment_info=mock_payment_info,
        l10n=mock_l10n,
    )

    call_kwargs = mock_bot.send_invoice.call_args[1]
    prices = call_kwargs["prices"]

    # Проверяем структуру цены
    assert len(prices) == 1
    assert isinstance(prices[0], LabeledPrice)
    assert prices[0].amount == 100


# ==============================================================================
# ТЕСТЫ successful_payment_handler
# ==============================================================================


@pytest.fixture
def mock_message_with_payment() -> MagicMock:
    """Мок сообщения с successful_payment."""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock()
    message.from_user.id = 123456789
    message.answer = AsyncMock()

    # Мок SuccessfulPayment
    payment = MagicMock(spec=SuccessfulPayment)
    payment.currency = "XTR"
    payment.total_amount = 100
    payment.invoice_payload = '{"tariff_slug": "tokens_1000", "user_id": 123456789}'
    payment.telegram_payment_charge_id = "tg_charge_123"
    payment.provider_payment_charge_id = "provider_charge_123"

    # По умолчанию не рекуррентный
    payment.is_recurring = False
    payment.is_first_recurring = False
    payment.subscription_expiration_date = None

    message.successful_payment = payment
    return message


@pytest.mark.asyncio
async def test_successful_payment_one_time_purchase(
    mock_message_with_payment: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: разовая покупка показывает сообщение buy_success."""
    with (
        patch("src.bot.handlers.buy.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.buy.create_telegram_stars_provider") as mock_provider,
        patch("src.bot.handlers.buy.create_payment_service") as mock_service_cls,
        patch("src.bot.handlers.buy.yaml_config") as mock_config,
    ):
        # Настройка моков
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        mock_provider.return_value = MagicMock()

        # Мок тарифа
        mock_tariff = MagicMock()
        mock_tariff.is_subscription = False
        mock_tariff.effective_tokens = 1000
        mock_config.get_tariff.return_value = mock_tariff

        # Мок PaymentService.process_webhook
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.is_success = True
        mock_result.tariff_slug = "tokens_1000"
        mock_service.process_webhook = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        # Вызываем обработчик
        await successful_payment_handler(mock_message_with_payment, mock_l10n)

        # Проверяем что показано правильное сообщение
        mock_message_with_payment.answer.assert_called_once()
        call_text = mock_message_with_payment.answer.call_args[0][0]
        assert call_text == "✅ Токены начислены: 1000"

        # Проверяем что process_webhook вызван с правильными данными
        webhook_data = mock_service.process_webhook.call_args[0][1]
        assert webhook_data["is_recurring"] is False
        assert webhook_data["is_first_recurring"] is False


@pytest.mark.asyncio
async def test_successful_payment_first_subscription(
    mock_message_with_payment: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: первая покупка подписки показывает buy_subscription_activated."""
    # Устанавливаем is_first_recurring=True для первой подписки
    mock_message_with_payment.successful_payment.is_recurring = False
    mock_message_with_payment.successful_payment.is_first_recurring = True

    with (
        patch("src.bot.handlers.buy.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.buy.create_telegram_stars_provider") as mock_provider,
        patch("src.bot.handlers.buy.create_payment_service") as mock_service_cls,
        patch("src.bot.handlers.buy.yaml_config") as mock_config,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_provider.return_value = MagicMock()

        # Мок тарифа-подписки
        mock_tariff = MagicMock()
        mock_tariff.is_subscription = True
        mock_tariff.effective_tokens = 5000
        mock_config.get_tariff.return_value = mock_tariff

        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.is_success = True
        mock_result.tariff_slug = "pro_monthly"
        mock_service.process_webhook = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        await successful_payment_handler(mock_message_with_payment, mock_l10n)

        # Проверяем сообщение активации подписки
        call_text = mock_message_with_payment.answer.call_args[0][0]
        assert call_text == "✅ Подписка активирована! Вы получили 5000 токенов"


@pytest.mark.asyncio
async def test_successful_payment_recurring_renewal(
    mock_message_with_payment: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: автопродление подписки показывает buy_subscription_renewed."""
    # Устанавливаем is_recurring=True, is_first_recurring=False для продления
    mock_message_with_payment.successful_payment.is_recurring = True
    mock_message_with_payment.successful_payment.is_first_recurring = False
    expiration = 1704067200
    mock_message_with_payment.successful_payment.subscription_expiration_date = (
        expiration
    )

    with (
        patch("src.bot.handlers.buy.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.buy.create_telegram_stars_provider") as mock_provider,
        patch("src.bot.handlers.buy.create_payment_service") as mock_service_cls,
        patch("src.bot.handlers.buy.yaml_config") as mock_config,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_provider.return_value = MagicMock()

        # Мок тарифа-подписки
        mock_tariff = MagicMock()
        mock_tariff.is_subscription = True
        mock_tariff.effective_tokens = 5000
        mock_config.get_tariff.return_value = mock_tariff

        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.is_success = True
        mock_result.tariff_slug = "pro_monthly"
        mock_service.process_webhook = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        await successful_payment_handler(mock_message_with_payment, mock_l10n)

        # КРИТИЧНО: проверяем сообщение продления
        call_text = mock_message_with_payment.answer.call_args[0][0]
        assert call_text == "✅ Подписка продлена! Начислено 5000 токенов"

        # Проверяем что данные о рекуррентности переданы в webhook
        webhook_data = mock_service.process_webhook.call_args[0][1]
        assert webhook_data["is_recurring"] is True
        assert webhook_data["is_first_recurring"] is False
        assert webhook_data["subscription_expiration_date"] == 1704067200


@pytest.mark.asyncio
async def test_successful_payment_passes_all_fields_to_webhook(
    mock_message_with_payment: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: все поля successful_payment передаются в process_webhook."""
    mock_message_with_payment.successful_payment.is_recurring = True
    mock_message_with_payment.successful_payment.is_first_recurring = True
    expiration = 1704067200
    mock_message_with_payment.successful_payment.subscription_expiration_date = (
        expiration
    )

    with (
        patch("src.bot.handlers.buy.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.buy.create_telegram_stars_provider") as mock_provider,
        patch("src.bot.handlers.buy.create_payment_service") as mock_service_cls,
        patch("src.bot.handlers.buy.yaml_config") as mock_config,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_provider.return_value = MagicMock()

        mock_tariff = MagicMock()
        mock_tariff.is_subscription = True
        mock_tariff.effective_tokens = 5000
        mock_config.get_tariff.return_value = mock_tariff

        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.is_success = True
        mock_result.tariff_slug = "pro_monthly"
        mock_service.process_webhook = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        await successful_payment_handler(mock_message_with_payment, mock_l10n)

        # КРИТИЧНО: проверяем все поля в данных webhook
        webhook_data = mock_service.process_webhook.call_args[0][1]
        assert webhook_data["currency"] == "XTR"
        assert webhook_data["total_amount"] == 100
        assert webhook_data["telegram_payment_charge_id"] == "tg_charge_123"
        assert webhook_data["provider_payment_charge_id"] == "provider_charge_123"
        assert webhook_data["is_recurring"] is True
        assert webhook_data["is_first_recurring"] is True
        assert webhook_data["subscription_expiration_date"] == 1704067200


@pytest.mark.asyncio
async def test_successful_payment_handles_missing_recurring_fields(
    mock_message_with_payment: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: обработка отсутствия полей is_recurring в старых версиях aiogram."""
    # Удаляем атрибуты (как если бы это была старая версия aiogram)
    payment = mock_message_with_payment.successful_payment
    delattr(payment, "is_recurring")
    delattr(payment, "is_first_recurring")
    delattr(payment, "subscription_expiration_date")

    with (
        patch("src.bot.handlers.buy.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.buy.create_telegram_stars_provider") as mock_provider,
        patch("src.bot.handlers.buy.create_payment_service") as mock_service_cls,
        patch("src.bot.handlers.buy.yaml_config") as mock_config,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_provider.return_value = MagicMock()

        mock_tariff = MagicMock()
        mock_tariff.is_subscription = False
        mock_tariff.effective_tokens = 1000
        mock_config.get_tariff.return_value = mock_tariff

        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.is_success = True
        mock_result.tariff_slug = "tokens_1000"
        mock_service.process_webhook = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        # НЕ должно быть исключения
        await successful_payment_handler(mock_message_with_payment, mock_l10n)

        # Проверяем что значения по умолчанию (False/None) переданы
        webhook_data = mock_service.process_webhook.call_args[0][1]
        assert webhook_data["is_recurring"] is False
        assert webhook_data["is_first_recurring"] is False
        assert webhook_data["subscription_expiration_date"] is None


@pytest.mark.asyncio
async def test_successful_payment_failed_result(
    mock_message_with_payment: MagicMock,
    mock_l10n: MagicMock,
) -> None:
    """Тест: если платёж не обработан — показывается сообщение об ошибке."""
    with (
        patch("src.bot.handlers.buy.DatabaseSession") as mock_session_cls,
        patch("src.bot.handlers.buy.create_telegram_stars_provider") as mock_provider,
        patch("src.bot.handlers.buy.create_payment_service") as mock_service_cls,
    ):
        mock_session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = mock_session
        mock_provider.return_value = MagicMock()

        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.is_success = False  # Платёж не удался
        mock_service.process_webhook = AsyncMock(return_value=mock_result)
        mock_service_cls.return_value = mock_service

        await successful_payment_handler(mock_message_with_payment, mock_l10n)

        # Проверяем сообщение об ошибке
        call_text = mock_message_with_payment.answer.call_args[0][0]
        assert call_text == "❌ Платёж не удался"
