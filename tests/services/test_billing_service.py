"""Тесты для сервиса биллинга (BillingService).

Проверяют корректность работы системы монетизации:
- check_and_reserve: проверка возможности генерации с учётом quantity
- charge_generation: списание токенов после генерации
- grant_registration_bonus: начисление бонуса при регистрации
- get_balance_info: получение информации о балансе

Особое внимание к:
- Стратегиям ценообразования (PerGenerationPricing, PerMinutePricing)
- Формуле расчёта: total_cost = price_tokens × quantity
- Поведению при отключённом биллинге
"""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import BillingConfig, ModelConfig, YamlConfig
from src.db.models.user import User
from src.db.repositories.transaction_repo import TransactionRepository
from src.services.billing_service import (
    PER_GENERATION_PRICING,
    BalanceInfo,
    BillingService,
    GenerationCost,
)

# =============================================================================
# ФИКСТУРЫ
# =============================================================================


@pytest.fixture
def mock_session() -> AsyncSession:
    """Мок-сессия SQLAlchemy."""
    return MagicMock(spec=AsyncSession)


@pytest.fixture
def billing_config_enabled() -> BillingConfig:
    """Конфигурация биллинга с включённым биллингом."""
    return BillingConfig(
        enabled=True,
        registration_bonus=100,
    )


@pytest.fixture
def billing_config_disabled() -> BillingConfig:
    """Конфигурация биллинга с отключённым биллингом."""
    return BillingConfig(
        enabled=False,
        registration_bonus=0,
    )


@pytest.fixture
def yaml_config_with_models() -> YamlConfig:
    """YAML-конфигурация с моделями."""
    config = MagicMock(spec=YamlConfig)

    # Модель для chat (фиксированная цена)
    config.get_model.side_effect = lambda key: {
        "gpt-4o": ModelConfig(
            provider="openai",
            model_id="openai/gpt-4o",
            generation_type="chat",
            display_name="GPT-4o",
            price_tokens=15,
        ),
        # Модель для image (фиксированная цена)
        "flux-pro": ModelConfig(
            provider="replicate",
            model_id="black-forest-labs/flux-pro",
            generation_type="image",
            display_name="FLUX Pro",
            price_tokens=50,
        ),
        # Модель для stt (цена за минуту)
        "whisper": ModelConfig(
            provider="openai",
            model_id="openai/whisper-large-v3",
            generation_type="stt",
            display_name="Whisper Large V3",
            price_tokens=5,  # 5 токенов за минуту
        ),
        # Бесплатная модель
        "free-model": ModelConfig(
            provider="openai",
            model_id="openai/gpt-3.5-turbo",
            generation_type="chat",
            display_name="GPT-3.5 Turbo (Free)",
            price_tokens=0,
        ),
    }.get(key)

    return config


@pytest.fixture(autouse=True)
def mock_subscription_repo() -> Generator[MagicMock, None, None]:
    """Автоматически мокировать SubscriptionRepository для всех тестов.

    Возвращает None для get_active_subscription() (нет активной подписки).
    Это позволяет тестам работать без subscription integration.
    """
    with patch(
        "src.services.billing_service.SubscriptionRepository"
    ) as mock_repo_class:
        mock_repo_instance = MagicMock()
        # get_active_subscription возвращает None (нет активной подписки)
        mock_repo_instance.get_active_subscription = AsyncMock(return_value=None)
        mock_repo_class.return_value = mock_repo_instance
        yield mock_repo_instance


@pytest.fixture
def test_user() -> User:
    """Тестовый пользователь с балансом 200 токенов."""
    user = MagicMock(spec=User)
    user.id = 123
    user.balance = 200
    user.telegram_id = 987654321
    user.registration_bonus_granted = False
    return user


@pytest.fixture
def test_user_zero_balance() -> User:
    """Тестовый пользователь с нулевым балансом."""
    user = MagicMock(spec=User)
    user.id = 456
    user.balance = 0
    user.telegram_id = 111222333
    user.registration_bonus_granted = False
    return user


# =============================================================================
# ТЕСТЫ check_and_reserve()
# =============================================================================


class TestCheckAndReserve:
    """Тесты для метода check_and_reserve()."""

    @pytest.mark.asyncio
    async def test_check_and_reserve_per_generation_pricing_sufficient_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить PerGenerationPricing (quantity=1) при достаточном балансе."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act
        cost = await billing.check_and_reserve(test_user, "gpt-4o")

        # Assert
        assert cost.can_proceed is True
        assert cost.tokens_cost == 15  # price_tokens × 1.0
        assert cost.model_key == "gpt-4o"
        assert cost.quantity == 1.0

    @pytest.mark.asyncio
    async def test_check_and_reserve_per_generation_pricing_insufficient_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user_zero_balance: User,
    ) -> None:
        """Проверить PerGenerationPricing при недостаточном балансе."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act
        cost = await billing.check_and_reserve(test_user_zero_balance, "gpt-4o")

        # Assert
        assert cost.can_proceed is False
        assert cost.tokens_cost == 15
        assert cost.model_key == "gpt-4o"
        assert cost.quantity == 1.0

    @pytest.mark.asyncio
    async def test_check_and_reserve_per_minute_pricing_sufficient_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить PerMinutePricing (quantity=N минут) при достаточном балансе."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act — 5 минут аудио
        cost = await billing.check_and_reserve(test_user, "whisper", quantity=5.0)

        # Assert
        assert cost.can_proceed is True
        assert cost.tokens_cost == 25  # 5 токенов/минуту × 5 минут = 25
        assert cost.model_key == "whisper"
        assert cost.quantity == 5.0

    @pytest.mark.asyncio
    async def test_check_and_reserve_per_minute_pricing_insufficient_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
    ) -> None:
        """Проверить PerMinutePricing при недостаточном балансе."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        # Пользователь с балансом 20 (хватит на 4 минуты, но не на 5)
        user = MagicMock(spec=User)
        user.id = 999
        user.balance = 20
        user.telegram_id = 999999

        # Act — 5 минут аудио (нужно 25 токенов)
        cost = await billing.check_and_reserve(user, "whisper", quantity=5.0)

        # Assert
        assert cost.can_proceed is False
        assert cost.tokens_cost == 25
        assert cost.quantity == 5.0

    @pytest.mark.asyncio
    async def test_check_and_reserve_fractional_quantity(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить расчёт с дробным quantity (например, 2.5 минуты)."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act — 2.5 минуты аудио
        cost = await billing.check_and_reserve(test_user, "whisper", quantity=2.5)

        # Assert
        assert cost.can_proceed is True
        # 5 токенов/минуту × 2.5 = 12.5 → int(12.5) = 12
        assert cost.tokens_cost == 12
        assert cost.quantity == 2.5

    @pytest.mark.asyncio
    async def test_check_and_reserve_billing_disabled_returns_zero_cost(
        self,
        mock_session: AsyncSession,
        billing_config_disabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user_zero_balance: User,
    ) -> None:
        """Проверить, что при отключённом биллинге всё бесплатно."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_disabled, yaml_config_with_models
        )

        # Act
        cost = await billing.check_and_reserve(test_user_zero_balance, "flux-pro")

        # Assert
        assert cost.can_proceed is True
        assert cost.tokens_cost == 0  # Биллинг отключён
        assert cost.model_key == "flux-pro"

    @pytest.mark.asyncio
    async def test_check_and_reserve_free_model_zero_cost(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user_zero_balance: User,
    ) -> None:
        """Проверить, что бесплатная модель (price_tokens=0) доступна всем."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act
        cost = await billing.check_and_reserve(test_user_zero_balance, "free-model")

        # Assert
        assert cost.can_proceed is True
        assert cost.tokens_cost == 0
        assert cost.model_key == "free-model"

    @pytest.mark.asyncio
    async def test_check_and_reserve_unknown_model_uses_zero_price(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что неизвестная модель использует price_tokens=0."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act
        cost = await billing.check_and_reserve(test_user, "unknown-model")

        # Assert
        assert cost.can_proceed is True
        assert cost.tokens_cost == 0  # Модель не найдена → цена 0
        assert cost.model_key == "unknown-model"

    @pytest.mark.asyncio
    async def test_check_and_reserve_default_quantity_is_per_generation_pricing(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что quantity по умолчанию = PER_GENERATION_PRICING (1.0)."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act — не передаём quantity
        cost = await billing.check_and_reserve(test_user, "gpt-4o")

        # Assert
        assert cost.quantity == PER_GENERATION_PRICING
        assert cost.quantity == 1.0

    @pytest.mark.asyncio
    async def test_check_and_reserve_boundary_exact_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
    ) -> None:
        """Проверить граничный случай: баланс = стоимость (должно разрешать)."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        user = MagicMock(spec=User)
        user.id = 777
        user.balance = 15  # Ровно столько, сколько стоит gpt-4o
        user.telegram_id = 777777

        # Act
        cost = await billing.check_and_reserve(user, "gpt-4o")

        # Assert
        assert cost.can_proceed is True
        assert cost.tokens_cost == 15

    @pytest.mark.asyncio
    async def test_check_and_reserve_boundary_one_token_short(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
    ) -> None:
        """Проверить граничный случай: баланс = стоимость - 1 (должно запрещать)."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        user = MagicMock(spec=User)
        user.id = 888
        user.balance = 14  # На 1 токен меньше, чем нужно
        user.telegram_id = 888888

        # Act
        cost = await billing.check_and_reserve(user, "gpt-4o")

        # Assert
        assert cost.can_proceed is False
        assert cost.tokens_cost == 15


# =============================================================================
# ТЕСТЫ charge_generation()
# =============================================================================


class TestChargeGeneration:
    """Тесты для метода charge_generation()."""

    @pytest.mark.asyncio
    async def test_charge_generation_creates_transaction(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что charge_generation создаёт транзакцию списания."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        cost = GenerationCost(
            can_proceed=True, tokens_cost=15, model_key="gpt-4o", quantity=1.0
        )

        # Act
        await billing.charge_generation(test_user, "gpt-4o", cost, "chat")

        # Assert
        billing._transaction_repo.create.assert_called_once()
        call_args = billing._transaction_repo.create.call_args
        assert call_args[1]["user"] == test_user
        assert call_args[1]["amount"] == -15  # Списание — отрицательное
        assert "GPT-4o" in call_args[1]["description"]

    @pytest.mark.asyncio
    async def test_charge_generation_billing_disabled_does_nothing(
        self,
        mock_session: AsyncSession,
        billing_config_disabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что при отключённом биллинге списание не происходит."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_disabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        cost = GenerationCost(
            can_proceed=True, tokens_cost=0, model_key="gpt-4o", quantity=1.0
        )

        # Act
        await billing.charge_generation(test_user, "gpt-4o", cost, "chat")

        # Assert
        billing._transaction_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_charge_generation_zero_cost_does_nothing(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что при нулевой стоимости списание не происходит."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        cost = GenerationCost(
            can_proceed=True, tokens_cost=0, model_key="free-model", quantity=1.0
        )

        # Act
        await billing.charge_generation(test_user, "free-model", cost, "chat")

        # Assert
        billing._transaction_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_charge_generation_includes_metadata(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что в транзакцию добавляются метаданные."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        cost = GenerationCost(
            can_proceed=True, tokens_cost=50, model_key="flux-pro", quantity=1.0
        )

        # Act
        await billing.charge_generation(test_user, "flux-pro", cost, "image")

        # Assert
        call_args = billing._transaction_repo.create.call_args
        metadata_json = call_args[1]["metadata_json"]
        assert "flux-pro" in metadata_json
        assert "image" in metadata_json

    @pytest.mark.asyncio
    async def test_charge_generation_per_minute_pricing(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить списание для PerMinutePricing (quantity > 1)."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        cost = GenerationCost(
            can_proceed=True, tokens_cost=25, model_key="whisper", quantity=5.0
        )

        # Act
        await billing.charge_generation(test_user, "whisper", cost, "stt")

        # Assert
        billing._transaction_repo.create.assert_called_once()
        call_args = billing._transaction_repo.create.call_args
        assert call_args[1]["amount"] == -25  # 5 токенов/минуту × 5 минут


# =============================================================================
# ТЕСТЫ grant_registration_bonus()
# =============================================================================


class TestGrantRegistrationBonus:
    """Тесты для метода grant_registration_bonus()."""

    @pytest.mark.asyncio
    async def test_grant_registration_bonus_creates_transaction(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что бонус создаёт транзакцию начисления."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        # Act
        bonus = await billing.grant_registration_bonus(test_user)

        # Assert
        assert bonus == 100
        billing._transaction_repo.create.assert_called_once()
        call_args = billing._transaction_repo.create.call_args
        assert call_args[1]["user"] == test_user
        assert call_args[1]["amount"] == 100  # Начисление — положительное

    @pytest.mark.asyncio
    async def test_grant_registration_bonus_billing_disabled_returns_zero(
        self,
        mock_session: AsyncSession,
        billing_config_disabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что при отключённом биллинге бонус не начисляется."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_disabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        # Act
        bonus = await billing.grant_registration_bonus(test_user)

        # Assert
        assert bonus == 0
        billing._transaction_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_grant_registration_bonus_zero_bonus_returns_zero(
        self,
        mock_session: AsyncSession,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что при нулевом бонусе ничего не начисляется."""
        # Arrange
        config = BillingConfig(enabled=True, registration_bonus=0)
        billing = BillingService(mock_session, config, yaml_config_with_models)
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        # Act
        bonus = await billing.grant_registration_bonus(test_user)

        # Assert
        assert bonus == 0
        billing._transaction_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_grant_registration_bonus_already_granted_returns_zero(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить защиту от повторного начисления (race condition)."""
        # Arrange
        test_user.registration_bonus_granted = True  # Бонус уже был начислен
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        # Act
        bonus = await billing.grant_registration_bonus(test_user)

        # Assert
        assert bonus == 0
        billing._transaction_repo.create.assert_not_called()


# =============================================================================
# ТЕСТЫ get_balance_info()
# =============================================================================


class TestGetBalanceInfo:
    """Тесты для метода get_balance_info()."""

    @pytest.mark.asyncio
    async def test_get_balance_info_returns_correct_data(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что get_balance_info возвращает правильные данные."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act
        info = await billing.get_balance_info(test_user)

        # Assert
        assert isinstance(info, BalanceInfo)
        assert info.balance == 200
        assert info.billing_enabled is True

    @pytest.mark.asyncio
    async def test_get_balance_info_billing_disabled(
        self,
        mock_session: AsyncSession,
        billing_config_disabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить, что get_balance_info показывает отключённый биллинг."""
        # Arrange
        billing = BillingService(
            mock_session, billing_config_disabled, yaml_config_with_models
        )

        # Act
        info = await billing.get_balance_info(test_user)

        # Assert
        assert info.billing_enabled is False


# =============================================================================
# ТЕСТЫ charge_generation() С ПОДПИСКОЙ
# =============================================================================


class TestChargeGenerationWithSubscription:
    """Тесты для метода charge_generation() при наличии активной подписки.

    Проверяют приоритет списания:
    1. Сначала из подписки (subscription.tokens_remaining)
    2. Затем из основного баланса (user.balance)
    """

    @pytest.fixture
    def mock_subscription(self) -> MagicMock:
        """Мок активной подписки с токенами."""
        subscription = MagicMock()
        subscription.id = 1
        subscription.tokens_remaining = 100
        return subscription

    @pytest.mark.asyncio
    async def test_charge_generation_deducts_from_subscription_only(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
        mock_subscription: MagicMock,
        mock_subscription_repo: MagicMock,
    ) -> None:
        """Проверить списание только из подписки, когда токенов достаточно.

        Если в подписке достаточно токенов для покрытия всей стоимости,
        списание должно происходить ТОЛЬКО из подписки, без создания транзакции.
        """
        # Arrange
        mock_subscription_repo.get_active_subscription = AsyncMock(
            return_value=mock_subscription
        )
        mock_subscription_repo.deduct_tokens = AsyncMock(return_value=mock_subscription)

        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        cost = GenerationCost(
            can_proceed=True, tokens_cost=15, model_key="gpt-4o", quantity=1.0
        )

        # Act
        await billing.charge_generation(test_user, "gpt-4o", cost, "chat")

        # Assert
        # Должен вызваться deduct_tokens для подписки
        mock_subscription_repo.deduct_tokens.assert_called_once_with(
            mock_subscription, 15
        )
        # НЕ должна создаваться транзакция (списание только из подписки)
        billing._transaction_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_charge_generation_deducts_from_subscription_and_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
        mock_subscription_repo: MagicMock,
    ) -> None:
        """Проверить комбинированное списание: часть из подписки, часть из баланса.

        Если в подписке недостаточно токенов, оставшаяся часть должна
        списываться из основного баланса через создание транзакции.
        """
        # Arrange — подписка с 10 токенами, нужно списать 15
        subscription = MagicMock()
        subscription.id = 1
        subscription.tokens_remaining = 10

        mock_subscription_repo.get_active_subscription = AsyncMock(
            return_value=subscription
        )
        mock_subscription_repo.deduct_tokens = AsyncMock(return_value=subscription)

        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        cost = GenerationCost(
            can_proceed=True, tokens_cost=15, model_key="gpt-4o", quantity=1.0
        )

        # Act
        await billing.charge_generation(test_user, "gpt-4o", cost, "chat")

        # Assert
        # Должен вызваться deduct_tokens для подписки (списать все 10 токенов)
        mock_subscription_repo.deduct_tokens.assert_called_once_with(subscription, 10)
        # Должна создаться транзакция на остаток (5 токенов)
        billing._transaction_repo.create.assert_called_once()
        call_args = billing._transaction_repo.create.call_args
        assert call_args[1]["amount"] == -5  # 15 - 10 = 5 из баланса

    @pytest.mark.asyncio
    async def test_charge_generation_no_subscription_deducts_from_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
        mock_subscription_repo: MagicMock,
    ) -> None:
        """Проверить списание из баланса при отсутствии подписки."""
        # Arrange — нет активной подписки
        mock_subscription_repo.get_active_subscription = AsyncMock(return_value=None)

        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        cost = GenerationCost(
            can_proceed=True, tokens_cost=15, model_key="gpt-4o", quantity=1.0
        )

        # Act
        await billing.charge_generation(test_user, "gpt-4o", cost, "chat")

        # Assert
        # deduct_tokens НЕ вызывается (нет подписки)
        mock_subscription_repo.deduct_tokens.assert_not_called()
        # Транзакция создаётся на полную стоимость
        billing._transaction_repo.create.assert_called_once()
        call_args = billing._transaction_repo.create.call_args
        assert call_args[1]["amount"] == -15

    @pytest.mark.asyncio
    async def test_charge_generation_subscription_zero_tokens_deducts_from_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        test_user: User,
        mock_subscription_repo: MagicMock,
    ) -> None:
        """Проверить списание из баланса при исчерпанных токенах подписки."""
        # Arrange — подписка есть, но токены закончились
        subscription = MagicMock()
        subscription.id = 1
        subscription.tokens_remaining = 0

        mock_subscription_repo.get_active_subscription = AsyncMock(
            return_value=subscription
        )

        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )
        billing._transaction_repo = MagicMock(spec=TransactionRepository)
        billing._transaction_repo.create = AsyncMock()

        cost = GenerationCost(
            can_proceed=True, tokens_cost=15, model_key="gpt-4o", quantity=1.0
        )

        # Act
        await billing.charge_generation(test_user, "gpt-4o", cost, "chat")

        # Assert
        # deduct_tokens НЕ вызывается (в подписке 0 токенов)
        mock_subscription_repo.deduct_tokens.assert_not_called()
        # Транзакция создаётся на полную стоимость
        billing._transaction_repo.create.assert_called_once()
        call_args = billing._transaction_repo.create.call_args
        assert call_args[1]["amount"] == -15


# =============================================================================
# ТЕСТЫ check_and_reserve() С ПОДПИСКОЙ
# =============================================================================


class TestCheckAndReserveWithSubscription:
    """Тесты для метода check_and_reserve() при наличии активной подписки.

    Проверяют, что при расчёте доступных токенов учитываются:
    - Токены подписки (subscription.tokens_remaining)
    - Основной баланс (user.balance)
    """

    @pytest.mark.asyncio
    async def test_check_and_reserve_includes_subscription_tokens(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        mock_subscription_repo: MagicMock,
    ) -> None:
        """Проверить, что токены подписки учитываются при проверке баланса."""
        # Arrange — пользователь с 0 на балансе, но 100 в подписке
        user = MagicMock(spec=User)
        user.id = 1
        user.balance = 0

        subscription = MagicMock()
        subscription.tokens_remaining = 100

        mock_subscription_repo.get_active_subscription = AsyncMock(
            return_value=subscription
        )

        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act — стоимость 15 токенов
        cost = await billing.check_and_reserve(user, "gpt-4o")

        # Assert — генерация должна быть разрешена (100 >= 15)
        assert cost.can_proceed is True
        assert cost.tokens_cost == 15

    @pytest.mark.asyncio
    async def test_check_and_reserve_combines_subscription_and_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        mock_subscription_repo: MagicMock,
    ) -> None:
        """Проверить, что баланс и подписка суммируются."""
        # Arrange — 10 в подписке + 10 на балансе = 20 доступно
        user = MagicMock(spec=User)
        user.id = 1
        user.balance = 10

        subscription = MagicMock()
        subscription.tokens_remaining = 10

        mock_subscription_repo.get_active_subscription = AsyncMock(
            return_value=subscription
        )

        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act — стоимость 15 токенов
        cost = await billing.check_and_reserve(user, "gpt-4o")

        # Assert — генерация разрешена (10 + 10 = 20 >= 15)
        assert cost.can_proceed is True
        assert cost.tokens_cost == 15

    @pytest.mark.asyncio
    async def test_check_and_reserve_insufficient_combined_balance(
        self,
        mock_session: AsyncSession,
        billing_config_enabled: BillingConfig,
        yaml_config_with_models: YamlConfig,
        mock_subscription_repo: MagicMock,
    ) -> None:
        """Проверить отказ, когда недостаточно токенов в сумме."""
        # Arrange — 5 в подписке + 5 на балансе = 10 < 15
        user = MagicMock(spec=User)
        user.id = 1
        user.balance = 5

        subscription = MagicMock()
        subscription.tokens_remaining = 5

        mock_subscription_repo.get_active_subscription = AsyncMock(
            return_value=subscription
        )

        billing = BillingService(
            mock_session, billing_config_enabled, yaml_config_with_models
        )

        # Act — стоимость 15 токенов
        cost = await billing.check_and_reserve(user, "gpt-4o")

        # Assert — генерация запрещена (5 + 5 = 10 < 15)
        assert cost.can_proceed is False
        assert cost.tokens_cost == 15
