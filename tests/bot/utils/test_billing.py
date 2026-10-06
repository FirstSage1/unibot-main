"""Тесты для утилит биллинга (src/bot/utils/billing.py).

Проверяют корректность работы общих функций для handlers:
- check_billing_and_show_error: проверка баланса и показ ошибки
- charge_after_delivery: списание токенов после доставки

Особое внимание к:
- Мокированию всех внешних зависимостей
- Корректной обработке недостаточного баланса
- Интеграции с BillingService
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from src.bot.utils.billing import charge_after_delivery, check_billing_and_show_error
from src.db.models.user import User
from src.services.billing_service import BillingService, ChargeResult, GenerationCost
from src.utils.i18n import Localization

# =============================================================================
# ФИКСТУРЫ
# =============================================================================


@pytest.fixture
def mock_billing_service() -> BillingService:
    """Мок BillingService."""
    service = MagicMock(spec=BillingService)
    service.check_and_reserve = AsyncMock()
    # charge_generation возвращает ChargeResult
    service.charge_generation = AsyncMock(
        return_value=ChargeResult(
            tokens_charged=15, from_subscription=10, from_balance=5
        )
    )
    return service


@pytest.fixture
def mock_message() -> Message:
    """Мок Telegram Message."""
    message = MagicMock(spec=Message)
    message.edit_text = AsyncMock()
    return message


@pytest.fixture
def mock_user() -> User:
    """Мок пользователя."""
    user = MagicMock(spec=User)
    user.id = 123
    user.balance = 100
    user.telegram_id = 987654321
    return user


@pytest.fixture
def mock_l10n() -> Localization:
    """Мок локализации."""
    l10n = MagicMock(spec=Localization)

    def get_translation(key: str, **kwargs: str | int) -> str:
        translations = {
            "billing_insufficient_balance": (
                "Недостаточно токенов для генерации {model_name}. "
                "Требуется: {price}, доступно: {balance}"
            ),
        }
        text = translations.get(key, key)
        if kwargs:
            text = text.format(**kwargs)
        return text

    l10n.get = MagicMock(side_effect=get_translation)
    return l10n


# =============================================================================
# ТЕСТЫ check_billing_and_show_error()
# =============================================================================


class TestCheckBillingAndShowError:
    """Тесты для функции check_billing_and_show_error()."""

    @pytest.mark.asyncio
    async def test_check_billing_sufficient_balance_returns_cost(
        self,
        mock_billing_service: BillingService,
        mock_user: User,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что при достаточном балансе возвращается GenerationCost."""
        # Arrange
        cost = GenerationCost(
            can_proceed=True, tokens_cost=15, model_key="gpt-4o", quantity=1.0
        )
        mock_billing_service.check_and_reserve.return_value = cost

        # Act
        result = await check_billing_and_show_error(
            mock_billing_service,
            mock_user,
            "gpt-4o",
            mock_message,
            mock_l10n,
        )

        # Assert
        assert result == cost
        mock_billing_service.check_and_reserve.assert_called_once_with(
            mock_user, "gpt-4o", quantity=1.0
        )
        # Сообщение об ошибке НЕ должно показываться
        mock_message.edit_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_billing_insufficient_balance_shows_error(
        self,
        mock_billing_service: BillingService,
        mock_user: User,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что при недостаточном балансе показывается ошибка."""
        # Arrange
        cost = GenerationCost(
            can_proceed=False, tokens_cost=15, model_key="gpt-4o", quantity=1.0
        )
        mock_billing_service.check_and_reserve.return_value = cost

        # Act
        result = await check_billing_and_show_error(
            mock_billing_service,
            mock_user,
            "gpt-4o",
            mock_message,
            mock_l10n,
        )

        # Assert
        assert result is None
        # Сообщение об ошибке должно быть показано
        mock_message.edit_text.assert_called_once()
        call_args = mock_message.edit_text.call_args
        error_text = call_args[0][0]
        assert "Недостаточно токенов" in error_text
        # Функция использует глобальный yaml_config.get_model() для display_name
        # Если модель не найдена — использует model_key
        assert "gpt-4o" in error_text.lower() or "GPT-4o" in error_text
        assert "15" in error_text
        assert "100" in error_text

    @pytest.mark.asyncio
    async def test_check_billing_with_custom_quantity(
        self,
        mock_billing_service: BillingService,
        mock_user: User,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить передачу custom quantity (для PerMinutePricing)."""
        # Arrange
        cost = GenerationCost(
            can_proceed=True, tokens_cost=25, model_key="whisper", quantity=5.0
        )
        mock_billing_service.check_and_reserve.return_value = cost

        # Act — передаём quantity=5.0 (5 минут аудио)
        result = await check_billing_and_show_error(
            mock_billing_service,
            mock_user,
            "whisper",
            mock_message,
            mock_l10n,
            quantity=5.0,
        )

        # Assert
        assert result == cost
        mock_billing_service.check_and_reserve.assert_called_once_with(
            mock_user, "whisper", quantity=5.0
        )
        mock_message.edit_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_billing_handles_unknown_model(
        self,
        mock_billing_service: BillingService,
        mock_user: User,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить обработку неизвестной модели (когда get_model вернёт None)."""
        # Arrange
        cost = GenerationCost(
            can_proceed=False, tokens_cost=15, model_key="unknown-model", quantity=1.0
        )
        mock_billing_service.check_and_reserve.return_value = cost

        # Act
        # Функция использует глобальный yaml_config, который может не знать о модели
        result = await check_billing_and_show_error(
            mock_billing_service,
            mock_user,
            "unknown-model",
            mock_message,
            mock_l10n,
        )

        # Assert
        assert result is None
        mock_message.edit_text.assert_called_once()
        call_args = mock_message.edit_text.call_args
        error_text = call_args[0][0]
        # Должно использоваться model_key вместо display_name (если модель не найдена)
        assert "unknown-model" in error_text


# =============================================================================
# ТЕСТЫ charge_after_delivery()
# =============================================================================


class TestChargeAfterDelivery:
    """Тесты для функции charge_after_delivery()."""

    @pytest.mark.asyncio
    async def test_charge_after_delivery_calls_billing_service(
        self,
        mock_billing_service: BillingService,
        mock_user: User,
    ) -> None:
        """Проверить, что функция вызывает BillingService.charge_generation."""
        # Arrange
        cost = GenerationCost(
            can_proceed=True, tokens_cost=15, model_key="gpt-4o", quantity=1.0
        )

        # Act
        await charge_after_delivery(
            mock_billing_service, mock_user, "gpt-4o", cost, "chat"
        )

        # Assert
        mock_billing_service.charge_generation.assert_called_once_with(
            mock_user, "gpt-4o", cost, "chat"
        )

    @pytest.mark.asyncio
    async def test_charge_after_delivery_with_different_generation_types(
        self,
        mock_billing_service: BillingService,
        mock_user: User,
    ) -> None:
        """Проверить списание для разных типов генерации."""
        # Arrange
        test_cases = [
            ("gpt-4o", 15, "chat"),
            ("flux-pro", 50, "image"),
            ("instruct-pix2pix", 30, "image_edit"),
            ("whisper", 25, "stt"),
        ]

        for model_key, tokens, gen_type in test_cases:
            cost = GenerationCost(
                can_proceed=True, tokens_cost=tokens, model_key=model_key, quantity=1.0
            )

            # Act
            await charge_after_delivery(
                mock_billing_service, mock_user, model_key, cost, gen_type
            )

            # Assert
            mock_billing_service.charge_generation.assert_called_with(
                mock_user, model_key, cost, gen_type
            )

    @pytest.mark.asyncio
    async def test_charge_after_delivery_with_per_minute_pricing(
        self,
        mock_billing_service: BillingService,
        mock_user: User,
    ) -> None:
        """Проверить списание для PerMinutePricing (quantity > 1)."""
        # Arrange
        cost = GenerationCost(
            can_proceed=True, tokens_cost=25, model_key="whisper", quantity=5.0
        )

        # Act
        await charge_after_delivery(
            mock_billing_service, mock_user, "whisper", cost, "stt"
        )

        # Assert
        mock_billing_service.charge_generation.assert_called_once_with(
            mock_user, "whisper", cost, "stt"
        )

    @pytest.mark.asyncio
    async def test_charge_after_delivery_zero_cost_still_calls_service(
        self,
        mock_billing_service: BillingService,
        mock_user: User,
    ) -> None:
        """Проверить, что даже при нулевой стоимости функция вызывает сервис.

        BillingService сам решит, нужно ли создавать транзакцию.
        """
        # Arrange
        cost = GenerationCost(
            can_proceed=True, tokens_cost=0, model_key="free-model", quantity=1.0
        )
        # Для бесплатной модели charge_generation возвращает нулевой результат
        mock_billing_service.charge_generation.return_value = ChargeResult(
            tokens_charged=0
        )

        # Act
        await charge_after_delivery(
            mock_billing_service, mock_user, "free-model", cost, "chat"
        )

        # Assert
        # Функция должна вызвать сервис даже для бесплатной модели
        # Сервис сам решит, нужно ли что-то делать
        mock_billing_service.charge_generation.assert_called_once_with(
            mock_user, "free-model", cost, "chat"
        )
