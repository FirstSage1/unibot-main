"""Тесты для сервиса реферальной программы (ReferralService).

Проверяют корректность работы реферальной системы:
- parse_referral_param: парсинг start-параметра (ref_123456)
- process_referral: обработка реферальной ссылки при регистрации
- get_referral_stats: получение статистики для /invite
- get_invite_link: генерация реферальной ссылки
- pay_pending_bonus: выплата отложенного бонуса

Особое внимание к:
- Защите от самоприглашения
- Защите от повторного приглашения
- Лимиту заработка (max_earnings)
- Режиму отложенных бонусов (require_payment)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import ReferralConfig
from src.db.models.referral import Referral
from src.db.models.transaction import TransactionType
from src.db.models.user import User
from src.db.repositories.referral_repo import ReferralRepository
from src.db.repositories.transaction_repo import TransactionRepository
from src.db.repositories.user_repo import UserRepository
from src.services.referral_service import (
    REFERRAL_PARAM_PATTERN,
    ReferralService,
    ReferralStats,
)

# =============================================================================
# ФИКСТУРЫ
# =============================================================================


@pytest.fixture
def mock_session() -> AsyncSession:
    """Мок-сессия SQLAlchemy."""
    return MagicMock(spec=AsyncSession)


@pytest.fixture
def referral_config_enabled() -> ReferralConfig:
    """Конфигурация реферальной программы (включена)."""
    return ReferralConfig(
        enabled=True,
        inviter_bonus=50,
        invitee_bonus=25,
        max_earnings=5000,
        require_payment=False,
    )


@pytest.fixture
def referral_config_disabled() -> ReferralConfig:
    """Конфигурация реферальной программы (отключена)."""
    return ReferralConfig(
        enabled=False,
        inviter_bonus=50,
        invitee_bonus=25,
        max_earnings=5000,
        require_payment=False,
    )


@pytest.fixture
def referral_config_require_payment() -> ReferralConfig:
    """Конфигурация с отложенными бонусами (require_payment=True)."""
    return ReferralConfig(
        enabled=True,
        inviter_bonus=50,
        invitee_bonus=25,
        max_earnings=5000,
        require_payment=True,
    )


@pytest.fixture
def referral_config_no_limit() -> ReferralConfig:
    """Конфигурация без лимита заработка (max_earnings=0)."""
    return ReferralConfig(
        enabled=True,
        inviter_bonus=50,
        invitee_bonus=25,
        max_earnings=0,
        require_payment=False,
    )


@pytest.fixture
def inviter_user() -> User:
    """Пользователь-пригласивший."""
    user = MagicMock(spec=User)
    user.id = 1
    user.telegram_id = 111111111
    user.username = "inviter"
    user.balance = 1000
    return user


@pytest.fixture
def invitee_user() -> User:
    """Пользователь-приглашённый (новый)."""
    user = MagicMock(spec=User)
    user.id = 2
    user.telegram_id = 222222222
    user.username = "invitee"
    user.balance = 0
    return user


# =============================================================================
# ТЕСТЫ parse_referral_param()
# =============================================================================


class TestParseReferralParam:
    """Тесты для метода parse_referral_param()."""

    def test_parse_valid_referral_param(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
    ) -> None:
        """Проверить парсинг валидного реферального параметра."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        # Act & Assert
        assert service.parse_referral_param("ref_123456") == 123456
        assert service.parse_referral_param("ref_1") == 1
        assert service.parse_referral_param("ref_999999999999") == 999999999999

    def test_parse_invalid_referral_param_returns_none(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
    ) -> None:
        """Проверить, что невалидные параметры возвращают None."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        # Act & Assert — разные невалидные форматы
        assert service.parse_referral_param(None) is None
        assert service.parse_referral_param("") is None
        assert service.parse_referral_param("promo") is None
        assert service.parse_referral_param("ref_") is None
        assert service.parse_referral_param("ref_abc") is None
        assert service.parse_referral_param("REF_123456") is None  # Case sensitive
        assert service.parse_referral_param("referral_123456") is None
        assert service.parse_referral_param("123456") is None
        assert service.parse_referral_param("ref_123_456") is None

    def test_referral_param_pattern_regex(self) -> None:
        """Проверить корректность регулярного выражения."""
        # Valid matches
        assert REFERRAL_PARAM_PATTERN.match("ref_123456") is not None
        assert REFERRAL_PARAM_PATTERN.match("ref_1") is not None

        # Invalid — no match
        assert REFERRAL_PARAM_PATTERN.match("ref_") is None
        assert REFERRAL_PARAM_PATTERN.match("ref_abc") is None
        assert REFERRAL_PARAM_PATTERN.match("promo_123") is None


# =============================================================================
# ТЕСТЫ process_referral()
# =============================================================================


class TestProcessReferral:
    """Тесты для метода process_referral()."""

    @pytest.mark.asyncio
    async def test_process_referral_disabled_returns_error(
        self,
        mock_session: AsyncSession,
        referral_config_disabled: ReferralConfig,
        invitee_user: User,
    ) -> None:
        """Проверить, что отключённая реферальная программа возвращает ошибку."""
        # Arrange
        service = ReferralService(mock_session, referral_config_disabled)

        # Act
        result = await service.process_referral(invitee_user, "ref_111111111")

        # Assert
        assert result.success is False
        assert result.error == "referral_disabled"

    @pytest.mark.asyncio
    async def test_process_referral_invalid_param_returns_error(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        invitee_user: User,
    ) -> None:
        """Проверить, что невалидный параметр возвращает ошибку."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        # Act & Assert — разные невалидные параметры
        result = await service.process_referral(invitee_user, None)
        assert result.success is False
        assert result.error == "invalid_param"

        result = await service.process_referral(invitee_user, "promo")
        assert result.success is False
        assert result.error == "invalid_param"

    @pytest.mark.asyncio
    async def test_process_referral_self_invite_returns_error(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        invitee_user: User,
    ) -> None:
        """Проверить защиту от самоприглашения."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        # Act — пользователь пытается пригласить сам себя
        result = await service.process_referral(
            invitee_user, f"ref_{invitee_user.telegram_id}"
        )

        # Assert
        assert result.success is False
        assert result.error == "self_invite"

    @pytest.mark.asyncio
    async def test_process_referral_inviter_not_found_returns_error(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        invitee_user: User,
    ) -> None:
        """Проверить ошибку, когда пригласивший не найден."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)
        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_telegram_id = AsyncMock(return_value=None)

        # Act
        result = await service.process_referral(invitee_user, "ref_999999999")

        # Assert
        assert result.success is False
        assert result.error == "inviter_not_found"

    @pytest.mark.asyncio
    async def test_process_referral_already_invited_returns_error(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
        invitee_user: User,
    ) -> None:
        """Проверить ошибку, когда пользователь уже был приглашён."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)
        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_telegram_id = AsyncMock(return_value=inviter_user)
        service._referral_repo = MagicMock(spec=ReferralRepository)
        # Уже есть запись о реферале
        service._referral_repo.get_by_invitee_id = AsyncMock(
            return_value=MagicMock(spec=Referral)
        )

        # Act
        result = await service.process_referral(
            invitee_user, f"ref_{inviter_user.telegram_id}"
        )

        # Assert
        assert result.success is False
        assert result.error == "already_invited"

    @pytest.mark.asyncio
    async def test_process_referral_success_immediate_bonus(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
        invitee_user: User,
    ) -> None:
        """Проверить успешную обработку с немедленным начислением бонусов."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_telegram_id = AsyncMock(return_value=inviter_user)

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.get_by_invitee_id = AsyncMock(return_value=None)
        service._referral_repo.get_total_earnings = AsyncMock(return_value=0)
        service._referral_repo.create = AsyncMock()

        service._transaction_repo = MagicMock(spec=TransactionRepository)
        service._transaction_repo.create = AsyncMock()

        # Act
        result = await service.process_referral(
            invitee_user, f"ref_{inviter_user.telegram_id}"
        )

        # Assert
        assert result.success is True
        assert result.invitee_bonus == 25
        assert result.inviter_bonus == 50
        assert result.bonus_pending is False
        assert result.error is None

        # Проверяем создание записи реферала
        service._referral_repo.create.assert_called_once()

        # Проверяем начисление бонусов (2 транзакции)
        assert service._transaction_repo.create.call_count == 2

    @pytest.mark.asyncio
    async def test_process_referral_success_pending_bonus(
        self,
        mock_session: AsyncSession,
        referral_config_require_payment: ReferralConfig,
        inviter_user: User,
        invitee_user: User,
    ) -> None:
        """Проверить успешную обработку с отложенным бонусом (require_payment=True)."""
        # Arrange
        service = ReferralService(mock_session, referral_config_require_payment)

        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_telegram_id = AsyncMock(return_value=inviter_user)

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.get_by_invitee_id = AsyncMock(return_value=None)
        service._referral_repo.get_total_earnings = AsyncMock(return_value=0)
        service._referral_repo.create = AsyncMock()

        service._transaction_repo = MagicMock(spec=TransactionRepository)
        service._transaction_repo.create = AsyncMock()

        # Act
        result = await service.process_referral(
            invitee_user, f"ref_{inviter_user.telegram_id}"
        )

        # Assert
        assert result.success is True
        assert result.invitee_bonus == 25
        assert result.inviter_bonus == 0  # Бонус пригласившему отложен
        assert result.bonus_pending is True

        # Только 1 транзакция — бонус приглашённому
        assert service._transaction_repo.create.call_count == 1

    @pytest.mark.asyncio
    async def test_process_referral_max_earnings_reached(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
        invitee_user: User,
    ) -> None:
        """Проверить, что бонус пригласившему не начисляется при достижении лимита."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_telegram_id = AsyncMock(return_value=inviter_user)

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.get_by_invitee_id = AsyncMock(return_value=None)
        # Уже заработал max_earnings (5000)
        service._referral_repo.get_total_earnings = AsyncMock(return_value=5000)
        service._referral_repo.create = AsyncMock()

        service._transaction_repo = MagicMock(spec=TransactionRepository)
        service._transaction_repo.create = AsyncMock()

        # Act
        result = await service.process_referral(
            invitee_user, f"ref_{inviter_user.telegram_id}"
        )

        # Assert
        assert result.success is True
        assert result.invitee_bonus == 25  # Приглашённый всё равно получает
        assert result.inviter_bonus == 0  # Пригласивший достиг лимита

    @pytest.mark.asyncio
    async def test_process_referral_partial_bonus_on_limit(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
        invitee_user: User,
    ) -> None:
        """Проверить частичный бонус при приближении к лимиту."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_telegram_id = AsyncMock(return_value=inviter_user)

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.get_by_invitee_id = AsyncMock(return_value=None)
        # Заработал 4980, до лимита осталось 20 (меньше стандартного бонуса 50)
        service._referral_repo.get_total_earnings = AsyncMock(return_value=4980)
        service._referral_repo.create = AsyncMock()

        service._transaction_repo = MagicMock(spec=TransactionRepository)
        service._transaction_repo.create = AsyncMock()

        # Act
        result = await service.process_referral(
            invitee_user, f"ref_{inviter_user.telegram_id}"
        )

        # Assert
        assert result.success is True
        assert result.invitee_bonus == 25
        assert result.inviter_bonus == 20  # Только оставшиеся до лимита

    @pytest.mark.asyncio
    async def test_process_referral_no_limit(
        self,
        mock_session: AsyncSession,
        referral_config_no_limit: ReferralConfig,
        inviter_user: User,
        invitee_user: User,
    ) -> None:
        """Проверить работу без лимита заработка (max_earnings=0)."""
        # Arrange
        service = ReferralService(mock_session, referral_config_no_limit)

        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_telegram_id = AsyncMock(return_value=inviter_user)

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.get_by_invitee_id = AsyncMock(return_value=None)
        # Заработал очень много, но лимита нет
        service._referral_repo.get_total_earnings = AsyncMock(return_value=100000)
        service._referral_repo.create = AsyncMock()

        service._transaction_repo = MagicMock(spec=TransactionRepository)
        service._transaction_repo.create = AsyncMock()

        # Act
        result = await service.process_referral(
            invitee_user, f"ref_{inviter_user.telegram_id}"
        )

        # Assert
        assert result.success is True
        assert result.inviter_bonus == 50  # Полный бонус, лимита нет


# =============================================================================
# ТЕСТЫ get_referral_stats()
# =============================================================================


class TestGetReferralStats:
    """Тесты для метода get_referral_stats()."""

    @pytest.mark.asyncio
    async def test_get_referral_stats_returns_correct_data(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
    ) -> None:
        """Проверить корректность возвращаемой статистики."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.count_referrals = AsyncMock(return_value=10)
        service._referral_repo.get_total_earnings = AsyncMock(return_value=500)
        service._referral_repo.count_pending_bonuses = AsyncMock(return_value=2)

        # Act
        stats = await service.get_referral_stats(inviter_user)

        # Assert
        assert isinstance(stats, ReferralStats)
        assert stats.total_referrals == 10
        assert stats.total_earnings == 500
        assert stats.pending_bonuses == 2
        assert stats.max_earnings == 5000
        assert stats.inviter_bonus == 50
        assert stats.can_earn_more is True

    @pytest.mark.asyncio
    async def test_get_referral_stats_max_reached(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
    ) -> None:
        """Проверить статистику при достижении лимита."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.count_referrals = AsyncMock(return_value=100)
        service._referral_repo.get_total_earnings = AsyncMock(return_value=5000)
        service._referral_repo.count_pending_bonuses = AsyncMock(return_value=0)

        # Act
        stats = await service.get_referral_stats(inviter_user)

        # Assert
        assert stats.total_earnings == 5000
        assert stats.max_earnings == 5000
        assert stats.can_earn_more is False

    @pytest.mark.asyncio
    async def test_get_referral_stats_no_limit(
        self,
        mock_session: AsyncSession,
        referral_config_no_limit: ReferralConfig,
        inviter_user: User,
    ) -> None:
        """Проверить статистику без лимита заработка."""
        # Arrange
        service = ReferralService(mock_session, referral_config_no_limit)

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.count_referrals = AsyncMock(return_value=1000)
        service._referral_repo.get_total_earnings = AsyncMock(return_value=50000)
        service._referral_repo.count_pending_bonuses = AsyncMock(return_value=0)

        # Act
        stats = await service.get_referral_stats(inviter_user)

        # Assert
        assert stats.max_earnings == 0
        assert stats.can_earn_more is True  # Лимита нет


# =============================================================================
# ТЕСТЫ get_invite_link()
# =============================================================================


class TestGetInviteLink:
    """Тесты для метода get_invite_link()."""

    def test_get_invite_link_format(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
    ) -> None:
        """Проверить формат реферальной ссылки."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        # Act
        link = service.get_invite_link(inviter_user, "my_test_bot")

        # Assert
        expected = f"https://t.me/my_test_bot?start=ref_{inviter_user.telegram_id}"
        assert link == expected

    def test_get_invite_link_different_users(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
    ) -> None:
        """Проверить, что разные пользователи получают разные ссылки."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        user1 = MagicMock(spec=User)
        user1.telegram_id = 123

        user2 = MagicMock(spec=User)
        user2.telegram_id = 456

        # Act
        link1 = service.get_invite_link(user1, "bot")
        link2 = service.get_invite_link(user2, "bot")

        # Assert
        assert link1 != link2
        assert "ref_123" in link1
        assert "ref_456" in link2


# =============================================================================
# ТЕСТЫ pay_pending_bonus()
# =============================================================================


class TestPayPendingBonus:
    """Тесты для метода pay_pending_bonus()."""

    @pytest.mark.asyncio
    async def test_pay_pending_bonus_no_referral(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        invitee_user: User,
    ) -> None:
        """Проверить, что возвращается (None, 0) если реферал не найден."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)
        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.get_unpaid_referral_by_invitee = AsyncMock(
            return_value=None
        )

        # Act
        inviter, bonus = await service.pay_pending_bonus(invitee_user)

        # Assert
        assert inviter is None
        assert bonus == 0

    @pytest.mark.asyncio
    async def test_pay_pending_bonus_success(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
        invitee_user: User,
    ) -> None:
        """Проверить успешную выплату отложенного бонуса."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        referral = MagicMock(spec=Referral)
        referral.inviter_id = inviter_user.id

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.get_unpaid_referral_by_invitee = AsyncMock(
            return_value=referral
        )
        service._referral_repo.get_total_earnings = AsyncMock(return_value=0)
        service._referral_repo.mark_bonus_paid = AsyncMock()

        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_id = AsyncMock(return_value=inviter_user)

        service._transaction_repo = MagicMock(spec=TransactionRepository)
        service._transaction_repo.create = AsyncMock()

        # Act
        inviter, bonus = await service.pay_pending_bonus(invitee_user)

        # Assert
        assert inviter == inviter_user
        assert bonus == 50

        # Проверяем, что бонус отмечен как выплаченный
        service._referral_repo.mark_bonus_paid.assert_called_once()

        # Проверяем создание транзакции
        service._transaction_repo.create.assert_called_once()
        call_args = service._transaction_repo.create.call_args
        assert call_args[1]["user"] == inviter_user
        assert call_args[1]["amount"] == 50
        assert call_args[1]["type_"] == TransactionType.REFERRAL_BONUS

    @pytest.mark.asyncio
    async def test_pay_pending_bonus_respects_max_earnings(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
        invitee_user: User,
    ) -> None:
        """Проверить, что выплата учитывает лимит заработка."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        referral = MagicMock(spec=Referral)
        referral.inviter_id = inviter_user.id

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.get_unpaid_referral_by_invitee = AsyncMock(
            return_value=referral
        )
        # Уже заработал 4980, до лимита 20
        service._referral_repo.get_total_earnings = AsyncMock(return_value=4980)
        service._referral_repo.mark_bonus_paid = AsyncMock()

        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_id = AsyncMock(return_value=inviter_user)

        service._transaction_repo = MagicMock(spec=TransactionRepository)
        service._transaction_repo.create = AsyncMock()

        # Act
        inviter, bonus = await service.pay_pending_bonus(invitee_user)

        # Assert
        assert inviter == inviter_user
        assert bonus == 20  # Только остаток до лимита

    @pytest.mark.asyncio
    async def test_pay_pending_bonus_max_reached_returns_zero(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
        inviter_user: User,
        invitee_user: User,
    ) -> None:
        """Проверить, что при достигнутом лимите бонус не выплачивается."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        referral = MagicMock(spec=Referral)
        referral.inviter_id = inviter_user.id

        service._referral_repo = MagicMock(spec=ReferralRepository)
        service._referral_repo.get_unpaid_referral_by_invitee = AsyncMock(
            return_value=referral
        )
        # Уже достигнут лимит
        service._referral_repo.get_total_earnings = AsyncMock(return_value=5000)

        service._user_repo = MagicMock(spec=UserRepository)
        service._user_repo.get_by_id = AsyncMock(return_value=inviter_user)

        # Act
        inviter, bonus = await service.pay_pending_bonus(invitee_user)

        # Assert
        assert inviter is None
        assert bonus == 0


# =============================================================================
# ТЕСТЫ is_enabled()
# =============================================================================


class TestIsEnabled:
    """Тесты для метода is_enabled()."""

    def test_is_enabled_returns_true_when_enabled(
        self,
        mock_session: AsyncSession,
        referral_config_enabled: ReferralConfig,
    ) -> None:
        """Проверить, что is_enabled() возвращает True когда программа включена."""
        # Arrange
        service = ReferralService(mock_session, referral_config_enabled)

        # Act & Assert
        assert service.is_enabled() is True

    def test_is_enabled_returns_false_when_disabled(
        self,
        mock_session: AsyncSession,
        referral_config_disabled: ReferralConfig,
    ) -> None:
        """Проверить, что is_enabled() возвращает False когда программа отключена."""
        # Arrange
        service = ReferralService(mock_session, referral_config_disabled)

        # Act & Assert
        assert service.is_enabled() is False
