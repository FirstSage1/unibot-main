"""Тесты для сервиса платежей (PaymentService).

Проверяют корректность работы основной логики платежей:
- create_payment: создание платежа через провайдер
- process_webhook: обработка webhook и начисление токенов
- get_tariffs_for_provider: получение тарифов для провайдера

Особенности:
- Провайдеры мокаются для изоляции тестов
- БД мокается для скорости тестов
- Проверяется идемпотентность обработки webhook
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import TariffConfig, TariffName, TariffPrice, YamlConfig
from src.db.models.payment import Payment, PaymentStatus
from src.db.models.user import User
from src.providers.payments.base import (
    BasePaymentProvider,
    PaymentIntent,
    PaymentResult,
)
from src.providers.payments.base import PaymentStatus as ProviderPaymentStatus
from src.services.payment_service import (
    PaymentInfo,
    PaymentService,
    ProviderNotConfiguredError,
    TariffNotAvailableForProviderError,
    TariffNotFoundError,
    create_payment_service,
)

# =============================================================================
# ФИКСТУРЫ
# =============================================================================


@pytest.fixture
def mock_session() -> AsyncSession:
    """Мок-сессия SQLAlchemy."""
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    return session


@pytest.fixture
def mock_provider() -> BasePaymentProvider:
    """Мок платёжного провайдера (YooKassa для совместимости с TariffConfig)."""
    provider = MagicMock(spec=BasePaymentProvider)
    # Используем yookassa, потому что TariffConfig.is_available_for_provider()
    # проверяет только конкретные имена провайдеров
    provider.provider_name = "yookassa"
    provider.currency = "RUB"

    # Мокаем create_payment
    provider.create_payment = AsyncMock(
        return_value=PaymentIntent(
            payment_id="provider_payment_123",
            provider="yookassa",
            amount=Decimal(99),
            currency="RUB",
            status=ProviderPaymentStatus.PENDING,
            confirmation_url="https://payment.example.com/pay/123",
            metadata={"user_id": 123456789, "tariff_slug": "tokens_100"},
        )
    )

    # Мокаем process_webhook
    provider.process_webhook = AsyncMock(
        return_value=PaymentResult(
            payment_id="provider_payment_123",
            provider="yookassa",
            status=ProviderPaymentStatus.SUCCEEDED,
            amount=Decimal(99),
            currency="RUB",
            metadata={"user_id": 123456789, "tariff_slug": "tokens_100"},
        )
    )

    return provider


@pytest.fixture
def mock_telegram_stars_provider() -> BasePaymentProvider:
    """Мок провайдера Telegram Stars."""
    provider = MagicMock(spec=BasePaymentProvider)
    provider.provider_name = "telegram_stars"
    provider.currency = "XTR"

    provider.create_payment = AsyncMock(
        return_value=PaymentIntent(
            payment_id=None,  # Stars не имеет payment_id до successful_payment
            provider="telegram_stars",
            amount=Decimal(50),
            currency="XTR",
            status=ProviderPaymentStatus.PENDING,
            confirmation_url=None,  # Оплата внутри Telegram
            metadata={"user_id": 123456789, "tariff_slug": "tokens_100"},
        )
    )

    return provider


@pytest.fixture
def mock_yaml_config() -> YamlConfig:
    """Мок конфигурации YAML с тарифами."""
    config = MagicMock(spec=YamlConfig)

    # Создаём тестовый тариф
    tariff_100 = TariffConfig(
        slug="tokens_100",
        tokens=100,
        name=TariffName(ru="100 токенов", en="100 tokens"),
        enabled=True,
        price=TariffPrice(
            rub=99,
            usd=1.99,
            stars=50,
        ),
    )

    tariff_500 = TariffConfig(
        slug="tokens_500",
        tokens=500,
        name=TariffName(ru="500 токенов", en="500 tokens"),
        enabled=True,
        price=TariffPrice(
            rub=399,
            usd=7.99,
            stars=200,
        ),
    )

    # Настраиваем get_tariff
    config.get_tariff.side_effect = lambda slug: {
        "tokens_100": tariff_100,
        "tokens_500": tariff_500,
    }.get(slug)

    # Настраиваем get_enabled_tariffs
    config.get_enabled_tariffs.return_value = [tariff_100, tariff_500]

    # Настраиваем get_tariffs_for_provider
    config.get_tariffs_for_provider.side_effect = lambda provider: {
        "telegram_stars": [tariff_100, tariff_500],
        "yookassa": [tariff_100, tariff_500],
        "stripe": [tariff_100, tariff_500],
    }.get(provider, [])

    return config


@pytest.fixture
def test_user() -> User:
    """Тестовый пользователь."""
    user = MagicMock(spec=User)
    user.id = 1
    user.telegram_id = 123456789
    user.username = "test_user"
    user.balance = 1000
    return user


@pytest.fixture
def payment_service(
    mock_session: AsyncSession,
    mock_provider: BasePaymentProvider,
    mock_yaml_config: YamlConfig,
) -> PaymentService:
    """Создать PaymentService с моками."""
    return PaymentService(
        session=mock_session,
        providers={"yookassa": mock_provider},
        yaml_config=mock_yaml_config,
    )


# =============================================================================
# ТЕСТЫ available_providers
# =============================================================================


class TestAvailableProviders:
    """Тесты для свойства available_providers."""

    def test_returns_list_of_provider_names(
        self,
        mock_session: AsyncSession,
        mock_provider: BasePaymentProvider,
        mock_yaml_config: YamlConfig,
    ) -> None:
        """Проверить, что возвращается список имён провайдеров."""
        # Arrange
        service = PaymentService(
            session=mock_session,
            providers={"provider_a": mock_provider, "provider_b": mock_provider},
            yaml_config=mock_yaml_config,
        )

        # Act
        providers = service.available_providers

        # Assert
        assert set(providers) == {"provider_a", "provider_b"}

    def test_returns_empty_list_if_no_providers(
        self,
        mock_session: AsyncSession,
        mock_yaml_config: YamlConfig,
    ) -> None:
        """Проверить, что возвращается пустой список без провайдеров."""
        # Arrange
        service = PaymentService(
            session=mock_session,
            providers={},
            yaml_config=mock_yaml_config,
        )

        # Act
        providers = service.available_providers

        # Assert
        assert providers == []


# =============================================================================
# ТЕСТЫ get_provider()
# =============================================================================


class TestGetProvider:
    """Тесты для метода get_provider()."""

    def test_returns_provider_by_name(
        self, payment_service: PaymentService, mock_provider: BasePaymentProvider
    ) -> None:
        """Проверить, что провайдер возвращается по имени."""
        # Act
        provider = payment_service.get_provider("yookassa")

        # Assert
        assert provider is mock_provider

    def test_returns_none_for_unknown_provider(
        self, payment_service: PaymentService
    ) -> None:
        """Проверить, что None возвращается для неизвестного провайдера."""
        # Act
        provider = payment_service.get_provider("unknown_provider")

        # Assert
        assert provider is None


# =============================================================================
# ТЕСТЫ create_payment()
# =============================================================================


class TestCreatePayment:
    """Тесты для метода create_payment()."""

    @pytest.mark.asyncio
    async def test_creates_payment_successfully(
        self,
        payment_service: PaymentService,
        mock_provider: BasePaymentProvider,
        test_user: User,
    ) -> None:
        """Проверить успешное создание платежа."""
        # Arrange
        with patch.object(
            payment_service._payment_repo, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_payment = MagicMock(spec=Payment)
            mock_payment.id = 1
            mock_create.return_value = mock_payment

            # Act
            result = await payment_service.create_payment(
                user=test_user,
                tariff_slug="tokens_100",
                provider_name="yookassa",
            )

            # Assert
            assert isinstance(result, PaymentInfo)
            assert result.provider == "yookassa"
            assert result.tariff_slug == "tokens_100"
            assert result.tokens_amount == 100
            assert result.confirmation_url == "https://payment.example.com/pay/123"

    @pytest.mark.asyncio
    async def test_calls_provider_create_payment(
        self,
        payment_service: PaymentService,
        mock_provider: BasePaymentProvider,
        test_user: User,
    ) -> None:
        """Проверить, что вызывается create_payment провайдера."""
        # Arrange
        with patch.object(
            payment_service._payment_repo, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_payment = MagicMock(spec=Payment)
            mock_payment.id = 1
            mock_create.return_value = mock_payment

            # Act
            await payment_service.create_payment(
                user=test_user,
                tariff_slug="tokens_100",
                provider_name="yookassa",
            )

            # Assert
            mock_provider.create_payment.assert_called_once()
            call_kwargs = mock_provider.create_payment.call_args.kwargs
            assert call_kwargs["tariff_slug"] == "tokens_100"
            assert call_kwargs["user_id"] == test_user.telegram_id

    @pytest.mark.asyncio
    async def test_saves_payment_to_database(
        self,
        payment_service: PaymentService,
        test_user: User,
    ) -> None:
        """Проверить, что платёж сохраняется в БД."""
        # Arrange
        with patch.object(
            payment_service._payment_repo, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_payment = MagicMock(spec=Payment)
            mock_payment.id = 1
            mock_create.return_value = mock_payment

            # Act
            await payment_service.create_payment(
                user=test_user,
                tariff_slug="tokens_100",
                provider_name="yookassa",
            )

            # Assert
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["user_id"] == test_user.id
            assert call_kwargs["provider"] == "yookassa"
            assert call_kwargs["tariff_slug"] == "tokens_100"
            assert call_kwargs["tokens_amount"] == 100

    @pytest.mark.asyncio
    async def test_raises_tariff_not_found_error(
        self,
        payment_service: PaymentService,
        test_user: User,
    ) -> None:
        """Проверить, что выбрасывается ошибка для несуществующего тарифа."""
        # Act & Assert
        with pytest.raises(TariffNotFoundError) as exc_info:
            await payment_service.create_payment(
                user=test_user,
                tariff_slug="nonexistent_tariff",
                provider_name="yookassa",
            )

        assert exc_info.value.slug == "nonexistent_tariff"

    @pytest.mark.asyncio
    async def test_raises_provider_not_configured_error(
        self,
        payment_service: PaymentService,
        test_user: User,
    ) -> None:
        """Проверить, что выбрасывается ошибка для ненастроенного провайдера."""
        # Act & Assert
        with pytest.raises(ProviderNotConfiguredError) as exc_info:
            await payment_service.create_payment(
                user=test_user,
                tariff_slug="tokens_100",
                provider_name="unknown_provider",
            )

        assert exc_info.value.provider == "unknown_provider"

    @pytest.mark.asyncio
    async def test_raises_tariff_not_available_for_provider_error(
        self,
        mock_session: AsyncSession,
        mock_provider: BasePaymentProvider,
        mock_yaml_config: YamlConfig,
        test_user: User,
    ) -> None:
        """Проверить ошибку если тариф недоступен для провайдера."""
        # Arrange
        # Создаём тариф без цены для yookassa
        tariff = MagicMock(spec=TariffConfig)
        tariff.slug = "stars_only"
        tariff.tokens = 100
        tariff.is_available_for_provider.return_value = False  # Недоступен

        def get_tariff_mock(slug: str) -> TariffConfig | None:
            return tariff if slug == "stars_only" else None

        mock_yaml_config.get_tariff.side_effect = get_tariff_mock

        service = PaymentService(
            session=mock_session,
            providers={"yookassa": mock_provider},
            yaml_config=mock_yaml_config,
        )

        # Act & Assert
        with pytest.raises(TariffNotAvailableForProviderError) as exc_info:
            await service.create_payment(
                user=test_user,
                tariff_slug="stars_only",
                provider_name="yookassa",
            )

        assert exc_info.value.slug == "stars_only"
        assert exc_info.value.provider == "yookassa"

    @pytest.mark.asyncio
    async def test_commits_session_after_create(
        self,
        payment_service: PaymentService,
        mock_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что сессия коммитится после создания платежа."""
        # Arrange
        with patch.object(
            payment_service._payment_repo, "create", new_callable=AsyncMock
        ) as mock_create:
            mock_payment = MagicMock(spec=Payment)
            mock_payment.id = 1
            mock_create.return_value = mock_payment

            # Act
            await payment_service.create_payment(
                user=test_user,
                tariff_slug="tokens_100",
                provider_name="yookassa",
            )

            # Assert
            mock_session.commit.assert_called_once()


# =============================================================================
# ТЕСТЫ process_webhook()
# =============================================================================


class TestProcessWebhook:
    """Тесты для метода process_webhook()."""

    @pytest.mark.asyncio
    async def test_processes_webhook_successfully(
        self,
        payment_service: PaymentService,
        mock_provider: BasePaymentProvider,
    ) -> None:
        """Проверить успешную обработку webhook."""
        # Arrange
        with (
            patch.object(
                payment_service._payment_repo,
                "get_by_provider_id",
                new_callable=AsyncMock,
            ) as mock_get,
            patch.object(
                payment_service._payment_repo,
                "update_status",
                new_callable=AsyncMock,
            ),
            patch.object(payment_service, "_credit_tokens", new_callable=AsyncMock),
        ):
            mock_payment = MagicMock(spec=Payment)
            mock_payment.id = 1
            mock_payment.status = PaymentStatus.PENDING
            mock_get.return_value = mock_payment

            webhook_data = {"event": "payment.succeeded"}

            # Act
            result = await payment_service.process_webhook("yookassa", webhook_data)

            # Assert
            assert result.status == ProviderPaymentStatus.SUCCEEDED
            mock_provider.process_webhook.assert_called_once_with(webhook_data)

    @pytest.mark.asyncio
    async def test_updates_payment_status_on_success(
        self,
        payment_service: PaymentService,
    ) -> None:
        """Проверить, что статус платежа обновляется при успехе."""
        # Arrange
        with (
            patch.object(
                payment_service._payment_repo,
                "get_by_provider_id",
                new_callable=AsyncMock,
            ) as mock_get,
            patch.object(
                payment_service._payment_repo,
                "update_status",
                new_callable=AsyncMock,
            ) as mock_update,
            patch.object(payment_service, "_credit_tokens", new_callable=AsyncMock),
        ):
            mock_payment = MagicMock(spec=Payment)
            mock_payment.id = 1
            mock_payment.status = PaymentStatus.PENDING
            mock_get.return_value = mock_payment

            # Act
            await payment_service.process_webhook("yookassa", {})

            # Assert
            mock_update.assert_called_once()
            # Проверяем, что статус обновлён на SUCCEEDED
            call_args = mock_update.call_args
            assert call_args[0][1] == PaymentStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_credits_tokens_on_success(
        self,
        payment_service: PaymentService,
    ) -> None:
        """Проверить, что токены начисляются при успешной оплате."""
        # Arrange
        with (
            patch.object(
                payment_service._payment_repo,
                "get_by_provider_id",
                new_callable=AsyncMock,
            ) as mock_get,
            patch.object(
                payment_service._payment_repo,
                "update_status",
                new_callable=AsyncMock,
            ),
            patch.object(
                payment_service, "_credit_tokens", new_callable=AsyncMock
            ) as mock_credit,
        ):
            mock_payment = MagicMock(spec=Payment)
            mock_payment.id = 1
            mock_payment.status = PaymentStatus.PENDING
            mock_get.return_value = mock_payment

            # Act
            await payment_service.process_webhook("yookassa", {})

            # Assert
            mock_credit.assert_called_once_with(mock_payment)

    @pytest.mark.asyncio
    async def test_idempotent_webhook_processing(
        self,
        payment_service: PaymentService,
    ) -> None:
        """Проверить идемпотентность — повторный webhook не начисляет токены."""
        # Arrange
        with (
            patch.object(
                payment_service._payment_repo,
                "get_by_provider_id",
                new_callable=AsyncMock,
            ) as mock_get,
            patch.object(
                payment_service._payment_repo,
                "update_status",
                new_callable=AsyncMock,
            ) as mock_update,
            patch.object(
                payment_service, "_credit_tokens", new_callable=AsyncMock
            ) as mock_credit,
        ):
            # Платёж уже в статусе SUCCEEDED
            mock_payment = MagicMock(spec=Payment)
            mock_payment.id = 1
            mock_payment.status = PaymentStatus.SUCCEEDED
            mock_get.return_value = mock_payment

            # Act
            await payment_service.process_webhook("yookassa", {})

            # Assert — статус не обновляется, токены не начисляются
            mock_update.assert_not_called()
            mock_credit.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_unknown_payment(
        self,
        payment_service: PaymentService,
    ) -> None:
        """Проверить обработку webhook для неизвестного платежа."""
        # Arrange
        with patch.object(
            payment_service._payment_repo,
            "get_by_provider_id",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = None  # Платёж не найден

            # Act
            result = await payment_service.process_webhook("yookassa", {})

            # Assert — результат возвращается, но без обновления БД
            assert result.status == ProviderPaymentStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_raises_provider_not_configured_error(
        self,
        payment_service: PaymentService,
    ) -> None:
        """Проверить ошибку для ненастроенного провайдера."""
        # Act & Assert
        with pytest.raises(ProviderNotConfiguredError):
            await payment_service.process_webhook("unknown_provider", {})

    @pytest.mark.asyncio
    async def test_commits_session_after_processing(
        self,
        payment_service: PaymentService,
        mock_session: AsyncSession,
    ) -> None:
        """Проверить, что сессия коммитится после обработки webhook."""
        # Arrange
        with (
            patch.object(
                payment_service._payment_repo,
                "get_by_provider_id",
                new_callable=AsyncMock,
            ) as mock_get,
            patch.object(
                payment_service._payment_repo,
                "update_status",
                new_callable=AsyncMock,
            ),
            patch.object(payment_service, "_credit_tokens", new_callable=AsyncMock),
        ):
            mock_payment = MagicMock(spec=Payment)
            mock_payment.id = 1
            mock_payment.status = PaymentStatus.PENDING
            mock_get.return_value = mock_payment

            # Act
            await payment_service.process_webhook("yookassa", {})

            # Assert
            mock_session.commit.assert_called()


# =============================================================================
# ТЕСТЫ get_tariffs_for_provider() и get_enabled_tariffs()
# =============================================================================


class TestGetTariffs:
    """Тесты для методов получения тарифов."""

    def test_get_tariffs_for_provider_delegates_to_config(
        self,
        payment_service: PaymentService,
        mock_yaml_config: YamlConfig,
    ) -> None:
        """Проверить, что get_tariffs_for_provider делегирует в конфиг."""
        # Act
        tariffs = payment_service.get_tariffs_for_provider("telegram_stars")

        # Assert
        mock_yaml_config.get_tariffs_for_provider.assert_called_once_with(
            "telegram_stars"
        )
        assert len(tariffs) == 2

    def test_get_enabled_tariffs_delegates_to_config(
        self,
        payment_service: PaymentService,
        mock_yaml_config: YamlConfig,
    ) -> None:
        """Проверить, что get_enabled_tariffs делегирует в конфиг."""
        # Act
        tariffs = payment_service.get_enabled_tariffs()

        # Assert
        mock_yaml_config.get_enabled_tariffs.assert_called_once()
        assert len(tariffs) == 2


# =============================================================================
# ТЕСТЫ create_payment_service()
# =============================================================================


class TestCreatePaymentService:
    """Тесты для фабричной функции create_payment_service()."""

    def test_creates_service_with_empty_providers_by_default(
        self, mock_session: AsyncSession, mock_yaml_config: YamlConfig
    ) -> None:
        """Проверить, что по умолчанию создаётся сервис без провайдеров."""
        # Act — передаём yaml_config явно чтобы избежать импорта глобального
        service = create_payment_service(mock_session, yaml_config=mock_yaml_config)

        # Assert
        assert service.available_providers == []

    def test_creates_service_with_provided_providers(
        self,
        mock_session: AsyncSession,
        mock_provider: BasePaymentProvider,
        mock_yaml_config: YamlConfig,
    ) -> None:
        """Проверить, что провайдеры передаются в сервис."""
        # Act
        service = create_payment_service(
            mock_session,
            providers={"test": mock_provider},
            yaml_config=mock_yaml_config,
        )

        # Assert
        assert "test" in service.available_providers

    def test_creates_service_with_provided_yaml_config(
        self,
        mock_session: AsyncSession,
        mock_yaml_config: YamlConfig,
    ) -> None:
        """Проверить, что yaml_config передаётся в сервис."""
        # Act
        service = create_payment_service(
            mock_session,
            yaml_config=mock_yaml_config,
        )

        # Assert
        # Проверяем, что конфиг используется
        service.get_enabled_tariffs()
        mock_yaml_config.get_enabled_tariffs.assert_called()


# =============================================================================
# ТЕСТЫ АВТОПРОДЛЕНИЯ ПОДПИСКИ
# =============================================================================


class TestRecurringRenewal:
    """Тесты для автопродления подписки через Telegram Stars."""

    @pytest.mark.asyncio
    async def test_creates_new_payment_for_recurring_renewal(
        self,
        mock_session: AsyncSession,
        mock_yaml_config: YamlConfig,
    ) -> None:
        """Проверить создание нового платежа при автопродлении."""
        # Arrange
        provider = MagicMock(spec=BasePaymentProvider)
        provider.provider_name = "telegram_stars"
        provider.currency = "XTR"

        # Webhook с is_recurring=True (не первый платёж)
        provider.process_webhook = AsyncMock(
            return_value=PaymentResult(
                payment_id="renewal_charge_456",
                provider="telegram_stars",
                status=ProviderPaymentStatus.SUCCEEDED,
                amount=Decimal(50),
                currency="XTR",
                is_recurring=True,
                metadata={"payment_id": "1"},  # ID оригинального платежа
            )
        )

        service = PaymentService(
            session=mock_session,
            providers={"telegram_stars": provider},
            yaml_config=mock_yaml_config,
        )

        # Оригинальный платёж
        original_payment = MagicMock(spec=Payment)
        original_payment.id = 1
        original_payment.user_id = 123
        original_payment.tariff_slug = "subscription_basic"
        original_payment.tokens_amount = 100
        original_payment.amount = Decimal(50)
        original_payment.currency = "XTR"
        original_payment.status = PaymentStatus.SUCCEEDED

        # Новый платёж (созданный для автопродления)
        new_payment = MagicMock(spec=Payment)
        new_payment.id = 2
        new_payment.status = PaymentStatus.PENDING

        with (
            patch.object(
                service._payment_repo,
                "get_by_id",
                new_callable=AsyncMock,
            ) as mock_get_by_id,
            patch.object(
                service._payment_repo,
                "get_by_provider_id",
                new_callable=AsyncMock,
            ) as mock_get_by_provider_id,
            patch.object(
                service._payment_repo,
                "create",
                new_callable=AsyncMock,
            ) as mock_create,
            patch.object(
                service._payment_repo,
                "update_status",
                new_callable=AsyncMock,
            ),
            patch.object(
                service, "_process_successful_payment", new_callable=AsyncMock
            ),
        ):
            # Нет существующего платежа с таким renewal ID
            mock_get_by_provider_id.return_value = None
            # Оригинальный платёж найден
            mock_get_by_id.return_value = original_payment
            # Создаём новый платёж
            mock_create.return_value = new_payment

            # Act
            webhook_data = {"is_first_recurring": False, "user_id": 123456789}
            result = await service.process_webhook("telegram_stars", webhook_data)

            # Assert
            assert result.status == ProviderPaymentStatus.SUCCEEDED
            # Проверяем, что создан новый платёж для автопродления
            mock_create.assert_called_once()
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["user_id"] == original_payment.user_id
            assert call_kwargs["tariff_slug"] == original_payment.tariff_slug
            assert "Автопродление" in call_kwargs["description"]

    @pytest.mark.asyncio
    async def test_skips_duplicate_recurring_renewal(
        self,
        mock_session: AsyncSession,
        mock_yaml_config: YamlConfig,
    ) -> None:
        """Проверить идемпотентность — дублирующий webhook не создаёт платёж."""
        # Arrange
        provider = MagicMock(spec=BasePaymentProvider)
        provider.provider_name = "telegram_stars"
        provider.currency = "XTR"

        provider.process_webhook = AsyncMock(
            return_value=PaymentResult(
                payment_id="renewal_charge_456",
                provider="telegram_stars",
                status=ProviderPaymentStatus.SUCCEEDED,
                amount=Decimal(50),
                currency="XTR",
                is_recurring=True,
                metadata={"payment_id": "1"},
            )
        )

        service = PaymentService(
            session=mock_session,
            providers={"telegram_stars": provider},
            yaml_config=mock_yaml_config,
        )

        # Платёж уже был создан для этого renewal_charge_456
        existing_renewal = MagicMock(spec=Payment)
        existing_renewal.id = 2
        existing_renewal.status = PaymentStatus.SUCCEEDED

        with (
            patch.object(
                service._payment_repo,
                "get_by_provider_id",
                new_callable=AsyncMock,
            ) as mock_get_by_provider_id,
            patch.object(
                service._payment_repo,
                "create",
                new_callable=AsyncMock,
            ) as mock_create,
        ):
            # Платёж с таким ID уже существует
            mock_get_by_provider_id.return_value = existing_renewal

            # Act
            webhook_data = {"is_first_recurring": False}
            result = await service.process_webhook("telegram_stars", webhook_data)

            # Assert
            assert result.status == ProviderPaymentStatus.SUCCEEDED
            # Новый платёж НЕ должен создаваться
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_recurring_payment_uses_normal_flow(
        self,
        payment_service: PaymentService,
    ) -> None:
        """Проверить, что первый платёж подписки обрабатывается как обычный."""
        # Arrange
        # Первый платёж (is_first_recurring=True) должен обрабатываться стандартно
        payment_service._providers["yookassa"].process_webhook = AsyncMock(
            return_value=PaymentResult(
                payment_id="first_charge_123",
                provider="yookassa",
                status=ProviderPaymentStatus.SUCCEEDED,
                amount=Decimal(99),
                currency="RUB",
                is_recurring=True,  # Это рекуррентный платёж
                metadata={"payment_id": "1"},
            )
        )

        original_payment = MagicMock(spec=Payment)
        original_payment.id = 1
        original_payment.status = PaymentStatus.PENDING

        with (
            patch.object(
                payment_service._payment_repo,
                "get_by_id",
                new_callable=AsyncMock,
            ) as mock_get_by_id,
            patch.object(
                payment_service._payment_repo,
                "get_by_provider_id",
                new_callable=AsyncMock,
            ),
            patch.object(
                payment_service._payment_repo,
                "update_status",
                new_callable=AsyncMock,
            ),
            patch.object(
                payment_service, "_process_successful_payment", new_callable=AsyncMock
            ),
        ):
            mock_get_by_id.return_value = original_payment

            # Act — is_first_recurring=True, так что это обычный первый платёж
            webhook_data = {"is_first_recurring": True}
            result = await payment_service.process_webhook("yookassa", webhook_data)

            # Assert
            assert result.status == ProviderPaymentStatus.SUCCEEDED
