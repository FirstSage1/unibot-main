"""Тесты для провайдера Telegram Stars.

Проверяют корректность работы TelegramStarsProvider:
- create_payment: создание invoice для отправки через bot.send_invoice()
- process_webhook: обработка successful_payment
- verify_webhook: проверка подписи (всегда True для Telegram)
- cancel_subscription: отмена подписки (возвращает False)

Особенности:
- Telegram Stars не делает внешних HTTP-запросов
- Все данные передаются через payload в invoice
- Валюта всегда XTR
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import PaymentError
from src.providers.payments.base import PaymentStatus
from src.providers.payments.stars import (
    TELEGRAM_STARS_CURRENCY,
    TelegramStarsInvoice,
    TelegramStarsProvider,
    create_telegram_stars_provider,
)

# =============================================================================
# ФИКСТУРЫ
# =============================================================================


@pytest.fixture
def provider() -> TelegramStarsProvider:
    """Создать экземпляр TelegramStarsProvider."""
    return TelegramStarsProvider()


@pytest.fixture
def valid_successful_payment_data() -> dict[str, Any]:
    """Данные successful_payment от Telegram.

    Формат соответствует aiogram Message.successful_payment.
    """
    payload = json.dumps(
        {
            "user_id": 123456789,
            "tariff_slug": "tokens_100",
            "provider": "telegram_stars",
            "created_at": "2026-01-07T12:00:00+00:00",
        }
    )

    return {
        "currency": "XTR",
        "total_amount": 50,
        "invoice_payload": payload,
        "telegram_payment_charge_id": "stars_charge_123",
        "provider_payment_charge_id": "provider_charge_456",
        "is_recurring": False,
        "is_first_recurring": False,
    }


# =============================================================================
# ТЕСТЫ TelegramStarsInvoice
# =============================================================================


class TestTelegramStarsInvoice:
    """Тесты для TelegramStarsInvoice dataclass."""

    def test_to_send_invoice_kwargs_returns_correct_fields(self) -> None:
        """Проверить, что to_send_invoice_kwargs возвращает правильные поля."""
        # Arrange
        invoice = TelegramStarsInvoice(
            title="100 токенов",
            description="Покупка 100 токенов для генерации",
            payload='{"user_id": 123}',
            currency="XTR",
            prices=[{"label": "100 токенов", "amount": 50}],
        )

        # Act
        kwargs = invoice.to_send_invoice_kwargs()

        # Assert
        assert kwargs["title"] == "100 токенов"
        assert kwargs["description"] == "Покупка 100 токенов для генерации"
        assert kwargs["payload"] == '{"user_id": 123}'
        assert kwargs["currency"] == "XTR"
        assert kwargs["prices"] == [{"label": "100 токенов", "amount": 50}]
        # provider_token должен быть пустой строкой для Stars
        assert kwargs["provider_token"] == ""

    def test_to_send_invoice_kwargs_does_not_include_chat_id(self) -> None:
        """Проверить, что chat_id не включается в kwargs (добавляется отдельно)."""
        # Arrange
        invoice = TelegramStarsInvoice(
            title="Test",
            description="Test",
            payload="{}",
            currency="XTR",
            prices=[{"label": "Test", "amount": 1}],
        )

        # Act
        kwargs = invoice.to_send_invoice_kwargs()

        # Assert
        assert "chat_id" not in kwargs


# =============================================================================
# ТЕСТЫ TelegramStarsProvider.provider_name и currency
# =============================================================================


class TestProviderProperties:
    """Тесты для свойств провайдера."""

    def test_provider_name_is_telegram_stars(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что provider_name == 'telegram_stars'."""
        assert provider.provider_name == "telegram_stars"

    def test_currency_is_xtr(self, provider: TelegramStarsProvider) -> None:
        """Проверить, что currency == 'XTR'."""
        assert provider.currency == "XTR"
        assert provider.currency == TELEGRAM_STARS_CURRENCY


# =============================================================================
# ТЕСТЫ create_payment()
# =============================================================================


class TestCreatePayment:
    """Тесты для метода create_payment()."""

    @pytest.mark.asyncio
    async def test_create_payment_returns_payment_intent(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить успешное создание платежа."""
        # Act
        intent = await provider.create_payment(
            amount=Decimal(50),
            currency="XTR",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Покупка 100 токенов",
        )

        # Assert
        assert intent.provider == "telegram_stars"
        assert intent.amount == Decimal(50)
        assert intent.currency == "XTR"
        assert intent.status == PaymentStatus.PENDING
        # payment_id = None до successful_payment
        assert intent.payment_id is None
        # confirmation_url = None (оплата внутри Telegram)
        assert intent.confirmation_url is None

    @pytest.mark.asyncio
    async def test_create_payment_includes_invoice_in_metadata(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что metadata содержит TelegramStarsInvoice."""
        # Act
        intent = await provider.create_payment(
            amount=Decimal(50),
            currency="XTR",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Покупка 100 токенов",
        )

        # Assert
        assert "invoice" in intent.metadata
        invoice = intent.metadata["invoice"]
        assert isinstance(invoice, TelegramStarsInvoice)
        assert invoice.currency == "XTR"
        assert invoice.prices[0]["amount"] == 50

    @pytest.mark.asyncio
    async def test_create_payment_includes_user_id_in_metadata(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что metadata содержит user_id."""
        # Act
        intent = await provider.create_payment(
            amount=Decimal(100),
            currency="XTR",
            user_id=987654321,
            tariff_slug="tokens_500",
            description="Покупка 500 токенов",
        )

        # Assert
        assert intent.metadata["user_id"] == 987654321
        assert intent.metadata["tariff_slug"] == "tokens_500"

    @pytest.mark.asyncio
    async def test_create_payment_payload_contains_identifiers(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что payload содержит идентификаторы платежа."""
        # Act
        intent = await provider.create_payment(
            amount=Decimal(50),
            currency="XTR",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Тест",
        )

        # Assert
        payload = intent.metadata["payload"]
        payload_data = json.loads(payload)
        assert payload_data["user_id"] == 123456789
        assert payload_data["tariff_slug"] == "tokens_100"
        assert payload_data["provider"] == "telegram_stars"
        assert "created_at" in payload_data

    @pytest.mark.asyncio
    async def test_create_payment_uses_custom_title_from_kwargs(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что можно передать кастомный title через kwargs."""
        # Act
        intent = await provider.create_payment(
            amount=Decimal(50),
            currency="XTR",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Описание",
            title="Кастомный заголовок",
        )

        # Assert
        invoice = intent.metadata["invoice"]
        assert invoice.title == "Кастомный заголовок"

    @pytest.mark.asyncio
    async def test_create_payment_uses_description_as_title_by_default(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что по умолчанию title = description."""
        # Act
        intent = await provider.create_payment(
            amount=Decimal(50),
            currency="XTR",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Покупка токенов",
        )

        # Assert
        invoice = intent.metadata["invoice"]
        assert invoice.title == "Покупка токенов"
        assert invoice.description == "Покупка токенов"

    @pytest.mark.asyncio
    async def test_create_payment_converts_decimal_to_int_for_stars(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что Decimal преобразуется в int для Stars."""
        # Act
        intent = await provider.create_payment(
            amount=Decimal("50.99"),  # Дробная часть должна отбросится
            currency="XTR",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Тест",
        )

        # Assert
        invoice = intent.metadata["invoice"]
        # Сумма должна быть целым числом
        assert invoice.prices[0]["amount"] == 50

    @pytest.mark.asyncio
    async def test_create_payment_rejects_non_xtr_currency(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что провайдер отклоняет валюту отличную от XTR."""
        # Act & Assert
        with pytest.raises(PaymentError) as exc_info:
            await provider.create_payment(
                amount=Decimal(100),
                currency="RUB",
                user_id=123456789,
                tariff_slug="tokens_100",
                description="Тест",
            )

        assert "XTR" in str(exc_info.value)
        assert exc_info.value.provider == "telegram_stars"

    @pytest.mark.asyncio
    async def test_create_payment_rejects_zero_amount(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что провайдер отклоняет нулевую сумму."""
        # Act & Assert
        with pytest.raises(PaymentError) as exc_info:
            await provider.create_payment(
                amount=Decimal(0),
                currency="XTR",
                user_id=123456789,
                tariff_slug="tokens_100",
                description="Тест",
            )

        assert "минимальная" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_create_payment_rejects_negative_amount(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что провайдер отклоняет отрицательную сумму."""
        # Act & Assert
        with pytest.raises(PaymentError) as exc_info:
            await provider.create_payment(
                amount=Decimal(-10),
                currency="XTR",
                user_id=123456789,
                tariff_slug="tokens_100",
                description="Тест",
            )

        assert exc_info.value.provider == "telegram_stars"

    @pytest.mark.asyncio
    async def test_create_payment_sets_created_at(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что created_at устанавливается."""
        # Act
        before = datetime.now(UTC)
        intent = await provider.create_payment(
            amount=Decimal(50),
            currency="XTR",
            user_id=123456789,
            tariff_slug="tokens_100",
            description="Тест",
        )
        after = datetime.now(UTC)

        # Assert
        assert intent.created_at is not None
        assert before <= intent.created_at <= after


# =============================================================================
# ТЕСТЫ verify_webhook()
# =============================================================================


class TestVerifyWebhook:
    """Тесты для метода verify_webhook()."""

    @pytest.mark.asyncio
    async def test_verify_webhook_always_returns_true(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что verify_webhook всегда возвращает True.

        Telegram гарантирует подлинность successful_payment через Bot API.
        """
        # Act
        result = await provider.verify_webhook(b"any_payload", "any_signature")

        # Assert
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_webhook_with_empty_params(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить verify_webhook с пустыми параметрами."""
        # Act
        result = await provider.verify_webhook(b"", "")

        # Assert
        assert result is True


# =============================================================================
# ТЕСТЫ process_webhook()
# =============================================================================


class TestProcessWebhook:
    """Тесты для метода process_webhook()."""

    @pytest.mark.asyncio
    async def test_process_webhook_returns_succeeded_status(
        self,
        provider: TelegramStarsProvider,
        valid_successful_payment_data: dict[str, Any],
    ) -> None:
        """Проверить, что successful_payment возвращает SUCCEEDED."""
        # Act
        result = await provider.process_webhook(valid_successful_payment_data)

        # Assert
        assert result.status == PaymentStatus.SUCCEEDED
        assert result.is_success is True

    @pytest.mark.asyncio
    async def test_process_webhook_extracts_payment_id(
        self,
        provider: TelegramStarsProvider,
        valid_successful_payment_data: dict[str, Any],
    ) -> None:
        """Проверить, что payment_id извлекается из telegram_payment_charge_id."""
        # Act
        result = await provider.process_webhook(valid_successful_payment_data)

        # Assert
        assert result.payment_id == "stars_charge_123"

    @pytest.mark.asyncio
    async def test_process_webhook_extracts_amount_and_currency(
        self,
        provider: TelegramStarsProvider,
        valid_successful_payment_data: dict[str, Any],
    ) -> None:
        """Проверить, что amount и currency извлекаются корректно."""
        # Act
        result = await provider.process_webhook(valid_successful_payment_data)

        # Assert
        assert result.amount == Decimal(50)
        assert result.currency == "XTR"

    @pytest.mark.asyncio
    async def test_process_webhook_parses_metadata_from_payload(
        self,
        provider: TelegramStarsProvider,
        valid_successful_payment_data: dict[str, Any],
    ) -> None:
        """Проверить, что metadata парсится из invoice_payload."""
        # Act
        result = await provider.process_webhook(valid_successful_payment_data)

        # Assert
        assert result.metadata["user_id"] == 123456789
        assert result.metadata["tariff_slug"] == "tokens_100"
        assert result.user_id == 123456789
        assert result.tariff_slug == "tokens_100"

    @pytest.mark.asyncio
    async def test_process_webhook_handles_recurring_payment(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить обработку рекуррентного платежа."""
        # Arrange
        data = {
            "currency": "XTR",
            "total_amount": 100,
            "invoice_payload": "{}",
            "telegram_payment_charge_id": "recurring_123",
            "is_recurring": True,
            "is_first_recurring": False,
            "subscription_expiration_date": 1735689600,  # Timestamp
        }

        # Act
        result = await provider.process_webhook(data)

        # Assert
        assert result.is_recurring is True
        assert result.subscription_expiration_date is not None

    @pytest.mark.asyncio
    async def test_process_webhook_handles_first_recurring_payment(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить обработку первого рекуррентного платежа."""
        # Arrange
        data = {
            "currency": "XTR",
            "total_amount": 100,
            "invoice_payload": "{}",
            "telegram_payment_charge_id": "first_recurring_123",
            "is_recurring": False,
            "is_first_recurring": True,
        }

        # Act
        result = await provider.process_webhook(data)

        # Assert
        # is_first_recurring тоже считается recurring
        assert result.is_recurring is True

    @pytest.mark.asyncio
    async def test_process_webhook_raises_on_missing_charge_id(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что ошибка выбрасывается без telegram_payment_charge_id."""
        # Arrange
        data = {
            "currency": "XTR",
            "total_amount": 50,
            "invoice_payload": "{}",
            # telegram_payment_charge_id отсутствует
        }

        # Act & Assert
        with pytest.raises(PaymentError) as exc_info:
            await provider.process_webhook(data)

        assert "telegram_payment_charge_id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_process_webhook_handles_invalid_json_payload(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить обработку невалидного JSON в payload."""
        # Arrange
        data = {
            "currency": "XTR",
            "total_amount": 50,
            "invoice_payload": "not_valid_json",
            "telegram_payment_charge_id": "charge_123",
        }

        # Act — не должно выбросить исключение
        result = await provider.process_webhook(data)

        # Assert — metadata должна быть пустой
        assert result.metadata == {}

    @pytest.mark.asyncio
    async def test_process_webhook_stores_raw_data(
        self,
        provider: TelegramStarsProvider,
        valid_successful_payment_data: dict[str, Any],
    ) -> None:
        """Проверить, что raw_data сохраняется для отладки."""
        # Act
        result = await provider.process_webhook(valid_successful_payment_data)

        # Assert
        assert result.raw_data == valid_successful_payment_data

    @pytest.mark.asyncio
    async def test_process_webhook_uses_default_currency_if_missing(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что используется XTR если currency отсутствует."""
        # Arrange
        data = {
            "total_amount": 50,
            "invoice_payload": "{}",
            "telegram_payment_charge_id": "charge_123",
            # currency отсутствует
        }

        # Act
        result = await provider.process_webhook(data)

        # Assert
        assert result.currency == "XTR"


# =============================================================================
# ТЕСТЫ cancel_subscription()
# =============================================================================


class TestCancelSubscription:
    """Тесты для метода cancel_subscription()."""

    @pytest.mark.asyncio
    async def test_cancel_subscription_returns_false(
        self, provider: TelegramStarsProvider
    ) -> None:
        """Проверить, что cancel_subscription возвращает False.

        Отмена подписки Stars требует Bot instance и должна
        выполняться через bot.edit_user_star_subscription().
        """
        # Act
        result = await provider.cancel_subscription("subscription_123")

        # Assert
        assert result is False


# =============================================================================
# ТЕСТЫ create_telegram_stars_provider()
# =============================================================================


class TestCreateTelegramStarsProvider:
    """Тесты для фабричной функции create_telegram_stars_provider()."""

    def test_creates_provider_instance(self) -> None:
        """Проверить, что фабрика создаёт экземпляр провайдера."""
        # Act
        provider = create_telegram_stars_provider()

        # Assert
        assert isinstance(provider, TelegramStarsProvider)
        assert provider.provider_name == "telegram_stars"
        assert provider.currency == "XTR"
