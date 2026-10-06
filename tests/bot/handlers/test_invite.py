"""Тесты для обработчика команды /invite.

Модуль тестирует:
- cmd_invite (обработчик команды /invite)
- Отображение реферальной ссылки и статистики
- Обработку отключённой реферальной программы
- Отображение информации о невыплаченных бонусах
- Предупреждение о достижении лимита заработка

Критические сценарии:
1. Реферальная программа отключена (referral.enabled=false)
2. Пользователь не найден в БД
3. Корректное отображение статистики
4. Отображение pending bonuses (require_payment=True)
5. Предупреждение о max_earnings
6. Обработка ошибок БД
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Message, User
from sqlalchemy.exc import SQLAlchemyError

from src.bot.handlers.invite import cmd_invite
from src.db.models.user import User as DbUser
from src.services.referral_service import ReferralStats
from src.utils.i18n import Localization

# =============================================================================
# ФИКСТУРЫ
# =============================================================================


@pytest.fixture
def mock_message() -> MagicMock:
    """Мок Message с пользователем."""
    message = MagicMock(spec=Message)
    message.from_user = User(
        id=123456789,
        is_bot=False,
        first_name="Test User",
        username="testuser",
        language_code="ru",
    )
    message.answer = AsyncMock()
    return message


@pytest.fixture
def mock_bot() -> MagicMock:
    """Мок Bot с username."""
    bot = MagicMock(spec=Bot)
    bot_me = MagicMock()
    bot_me.username = "test_bot"
    bot.get_me = AsyncMock(return_value=bot_me)
    return bot


@pytest.fixture
def mock_l10n() -> MagicMock:
    """Мок Localization."""
    l10n = MagicMock(spec=Localization)

    def get_translation(key: str, **kwargs: dict[str, str]) -> str:
        translations = {
            "invite_disabled": "Реферальная программа отключена",
            "error_user_not_found": "Пользователь не найден в БД",
            "error_unknown": "Произошла неизвестная ошибка",
            "invite_info": (
                "Ваша реферальная ссылка: {link}\n\n"
                "Приглашено: {total_referrals}\n"
                "Заработано: {total_earnings}\n"
                "Бонус за реферала: {inviter_bonus}\n"
                "Максимум: {max_earnings}"
            ),
            "invite_pending_bonuses": "Невыплаченных бонусов: {count}",
            "invite_max_reached": "Достигнут максимальный лимит заработка",
        }
        text = translations.get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

    l10n.get.side_effect = get_translation
    return l10n


@pytest.fixture
def mock_db_user() -> MagicMock:
    """Мок DB User."""
    user = MagicMock(spec=DbUser)
    user.id = 1
    user.telegram_id = 123456789
    user.username = "testuser"
    return user


@pytest.fixture
def mock_referral_stats() -> ReferralStats:
    """Мок статистики рефералов."""
    return ReferralStats(
        total_referrals=5,
        total_earnings=250,
        pending_bonuses=0,
        max_earnings=5000,
        inviter_bonus=50,
        can_earn_more=True,
    )


# =============================================================================
# ТЕСТЫ cmd_invite
# =============================================================================


class TestCmdInvite:
    """Тесты для обработчика команды /invite."""

    @pytest.mark.asyncio
    async def test_cmd_invite_handles_message_without_user(
        self,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Проверить обработку сообщения без from_user."""
        # Arrange
        message = MagicMock(spec=Message)
        message.from_user = None

        with patch("src.bot.handlers.invite.logger") as mock_logger:
            # Act
            await cmd_invite(message, mock_l10n, mock_bot)

            # Assert
            mock_logger.warning.assert_called_once()
            assert "без from_user" in mock_logger.warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_invite_referral_disabled_shows_message(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Проверить сообщение когда реферальная программа отключена."""
        # Arrange
        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=False)
            mock_service_cls.return_value = mock_service

            # Act
            await cmd_invite(mock_message, mock_l10n, mock_bot)

            # Assert
            mock_message.answer.assert_called_once_with(
                "Реферальная программа отключена"
            )

    @pytest.mark.asyncio
    async def test_cmd_invite_user_not_found_shows_error(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Проверить ошибку когда пользователь не найден в БД."""
        # Arrange
        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.UserRepository") as mock_repo_cls,
            patch("src.bot.handlers.invite.logger") as mock_logger,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service_cls.return_value = mock_service

            mock_repo = MagicMock()
            mock_repo.get_by_telegram_id = AsyncMock(return_value=None)
            mock_repo_cls.return_value = mock_repo

            # Act
            await cmd_invite(mock_message, mock_l10n, mock_bot)

            # Assert
            mock_logger.error.assert_called_once()
            mock_message.answer.assert_called_once_with("Пользователь не найден в БД")

    @pytest.mark.asyncio
    async def test_cmd_invite_shows_stats_and_link(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
        mock_db_user: MagicMock,
        mock_referral_stats: ReferralStats,
    ) -> None:
        """Проверить отображение статистики и реферальной ссылки."""
        # Arrange
        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.UserRepository") as mock_repo_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service.get_referral_stats = AsyncMock(
                return_value=mock_referral_stats
            )
            mock_service.get_invite_link = MagicMock(
                return_value="https://t.me/test_bot?start=ref_123456789"
            )
            mock_service_cls.return_value = mock_service

            mock_repo = MagicMock()
            mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
            mock_repo_cls.return_value = mock_repo

            # Act
            await cmd_invite(mock_message, mock_l10n, mock_bot)

            # Assert
            mock_message.answer.assert_called_once()
            response = mock_message.answer.call_args[0][0]
            assert "https://t.me/test_bot?start=ref_123456789" in response
            assert "5" in response  # total_referrals
            assert "250" in response  # total_earnings
            assert "50" in response  # inviter_bonus
            assert "5000" in response  # max_earnings

    @pytest.mark.asyncio
    async def test_cmd_invite_shows_pending_bonuses_info(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
        mock_db_user: MagicMock,
    ) -> None:
        """Проверить отображение информации о невыплаченных бонусах."""
        # Arrange
        stats_with_pending = ReferralStats(
            total_referrals=10,
            total_earnings=400,
            pending_bonuses=3,  # Есть невыплаченные бонусы
            max_earnings=5000,
            inviter_bonus=50,
            can_earn_more=True,
        )

        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.UserRepository") as mock_repo_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service.get_referral_stats = AsyncMock(return_value=stats_with_pending)
            mock_service.get_invite_link = MagicMock(
                return_value="https://t.me/bot?start=ref_1"
            )
            mock_service_cls.return_value = mock_service

            mock_repo = MagicMock()
            mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
            mock_repo_cls.return_value = mock_repo

            # Act
            await cmd_invite(mock_message, mock_l10n, mock_bot)

            # Assert
            response = mock_message.answer.call_args[0][0]
            assert "Невыплаченных бонусов: 3" in response

    @pytest.mark.asyncio
    async def test_cmd_invite_shows_max_reached_warning(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
        mock_db_user: MagicMock,
    ) -> None:
        """Проверить предупреждение о достижении лимита заработка."""
        # Arrange
        stats_max_reached = ReferralStats(
            total_referrals=100,
            total_earnings=5000,
            pending_bonuses=0,
            max_earnings=5000,
            inviter_bonus=50,
            can_earn_more=False,  # Достигнут лимит
        )

        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.UserRepository") as mock_repo_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service.get_referral_stats = AsyncMock(return_value=stats_max_reached)
            mock_service.get_invite_link = MagicMock(
                return_value="https://t.me/bot?start=ref_1"
            )
            mock_service_cls.return_value = mock_service

            mock_repo = MagicMock()
            mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
            mock_repo_cls.return_value = mock_repo

            # Act
            await cmd_invite(mock_message, mock_l10n, mock_bot)

            # Assert
            response = mock_message.answer.call_args[0][0]
            assert "Достигнут максимальный лимит заработка" in response

    @pytest.mark.asyncio
    async def test_cmd_invite_no_warning_when_can_earn_more(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
        mock_db_user: MagicMock,
        mock_referral_stats: ReferralStats,
    ) -> None:
        """Проверить отсутствие предупреждения когда можно ещё зарабатывать."""
        # Arrange
        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.UserRepository") as mock_repo_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service.get_referral_stats = AsyncMock(
                return_value=mock_referral_stats
            )
            mock_service.get_invite_link = MagicMock(
                return_value="https://t.me/bot?start=ref_1"
            )
            mock_service_cls.return_value = mock_service

            mock_repo = MagicMock()
            mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
            mock_repo_cls.return_value = mock_repo

            # Act
            await cmd_invite(mock_message, mock_l10n, mock_bot)

            # Assert
            response = mock_message.answer.call_args[0][0]
            assert "Достигнут максимальный лимит заработка" not in response

    @pytest.mark.asyncio
    async def test_cmd_invite_no_pending_bonuses_info_when_zero(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
        mock_db_user: MagicMock,
        mock_referral_stats: ReferralStats,
    ) -> None:
        """Проверить отсутствие информации о pending bonuses когда их нет."""
        # Arrange
        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.UserRepository") as mock_repo_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service.get_referral_stats = AsyncMock(
                return_value=mock_referral_stats
            )
            mock_service.get_invite_link = MagicMock(
                return_value="https://t.me/bot?start=ref_1"
            )
            mock_service_cls.return_value = mock_service

            mock_repo = MagicMock()
            mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
            mock_repo_cls.return_value = mock_repo

            # Act
            await cmd_invite(mock_message, mock_l10n, mock_bot)

            # Assert
            response = mock_message.answer.call_args[0][0]
            assert "Невыплаченных бонусов" not in response

    @pytest.mark.asyncio
    async def test_cmd_invite_handles_sqlalchemy_error(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Проверить обработку ошибки БД (SQLAlchemyError)."""
        # Arrange
        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.logger") as mock_logger,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service_cls.return_value = mock_service

            # Вызываем SQLAlchemyError при обращении к UserRepository
            with patch(
                "src.bot.handlers.invite.UserRepository",
                side_effect=SQLAlchemyError("DB Error"),
            ):
                # Act
                await cmd_invite(mock_message, mock_l10n, mock_bot)

                # Assert
                mock_logger.exception.assert_called_once()
                mock_message.answer.assert_called_once_with(
                    "Произошла неизвестная ошибка"
                )

    @pytest.mark.asyncio
    async def test_cmd_invite_handles_os_error(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
    ) -> None:
        """Проверить обработку ошибки подключения к БД (OSError)."""
        # Arrange
        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.logger") as mock_logger,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service_cls.return_value = mock_service

            # Вызываем OSError при обращении к UserRepository
            with patch(
                "src.bot.handlers.invite.UserRepository",
                side_effect=OSError("Connection Error"),
            ):
                # Act
                await cmd_invite(mock_message, mock_l10n, mock_bot)

                # Assert
                mock_logger.exception.assert_called_once()
                mock_message.answer.assert_called_once_with(
                    "Произошла неизвестная ошибка"
                )

    @pytest.mark.asyncio
    async def test_cmd_invite_logs_debug_info(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
        mock_db_user: MagicMock,
        mock_referral_stats: ReferralStats,
    ) -> None:
        """Проверить логирование debug-информации."""
        # Arrange
        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.UserRepository") as mock_repo_cls,
            patch("src.bot.handlers.invite.logger") as mock_logger,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service.get_referral_stats = AsyncMock(
                return_value=mock_referral_stats
            )
            mock_service.get_invite_link = MagicMock(
                return_value="https://t.me/bot?start=ref_1"
            )
            mock_service_cls.return_value = mock_service

            mock_repo = MagicMock()
            mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
            mock_repo_cls.return_value = mock_repo

            # Act
            await cmd_invite(mock_message, mock_l10n, mock_bot)

            # Assert
            mock_logger.debug.assert_called_once()
            log_message = mock_logger.debug.call_args[0][0]
            assert "Показана реферальная информация" in log_message

    @pytest.mark.asyncio
    async def test_cmd_invite_gets_bot_username(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_bot: MagicMock,
        mock_db_user: MagicMock,
        mock_referral_stats: ReferralStats,
    ) -> None:
        """Проверить получение username бота для ссылки."""
        # Arrange
        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.UserRepository") as mock_repo_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service.get_referral_stats = AsyncMock(
                return_value=mock_referral_stats
            )
            mock_service.get_invite_link = MagicMock(
                return_value="https://t.me/bot?start=ref_1"
            )
            mock_service_cls.return_value = mock_service

            mock_repo = MagicMock()
            mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
            mock_repo_cls.return_value = mock_repo

            # Act
            await cmd_invite(mock_message, mock_l10n, mock_bot)

            # Assert
            mock_bot.get_me.assert_called_once()
            mock_service.get_invite_link.assert_called_once_with(
                mock_db_user, "test_bot"
            )

    @pytest.mark.asyncio
    async def test_cmd_invite_handles_bot_without_username(
        self,
        mock_message: MagicMock,
        mock_l10n: MagicMock,
        mock_db_user: MagicMock,
        mock_referral_stats: ReferralStats,
    ) -> None:
        """Проверить обработку бота без username (использует fallback 'bot')."""
        # Arrange
        bot_without_username = MagicMock(spec=Bot)
        bot_me = MagicMock()
        bot_me.username = None  # Username отсутствует
        bot_without_username.get_me = AsyncMock(return_value=bot_me)

        with (
            patch("src.bot.handlers.invite.DatabaseSession") as mock_session_cls,
            patch(
                "src.bot.handlers.invite.create_referral_service"
            ) as mock_service_cls,
            patch("src.bot.handlers.invite.UserRepository") as mock_repo_cls,
        ):
            mock_session = AsyncMock()
            mock_session_cls.return_value.__aenter__.return_value = mock_session

            mock_service = MagicMock()
            mock_service.is_enabled = MagicMock(return_value=True)
            mock_service.get_referral_stats = AsyncMock(
                return_value=mock_referral_stats
            )
            mock_service.get_invite_link = MagicMock(
                return_value="https://t.me/bot?start=ref_1"
            )
            mock_service_cls.return_value = mock_service

            mock_repo = MagicMock()
            mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user)
            mock_repo_cls.return_value = mock_repo

            # Act
            await cmd_invite(mock_message, mock_l10n, bot_without_username)

            # Assert
            mock_service.get_invite_link.assert_called_once_with(mock_db_user, "bot")
