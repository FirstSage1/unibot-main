"""Обработчик команды /invite для реферальной программы.

Показывает пользователю:
- Персональную реферальную ссылку
- Статистику: сколько приглашено, сколько заработано
- Информацию о бонусах

Команда автоматически отключается если referral.enabled=false.
"""

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError

from src.db.base import DatabaseSession
from src.db.repositories.user_repo import UserRepository
from src.services.referral_service import create_referral_service
from src.utils.i18n import Localization
from src.utils.logging import get_logger

router = Router(name="invite")
logger = get_logger(__name__)


@router.message(Command("invite"))
async def cmd_invite(message: Message, l10n: Localization, bot: Bot) -> None:
    """Обработать команду /invite.

    Показывает реферальную ссылку и статистику.

    Args:
        message: Входящее сообщение с командой /invite.
        l10n: Объект локализации (внедряется через LanguageMiddleware).
        bot: Экземпляр бота (для получения username).
    """
    if message.from_user is None:
        logger.warning("Команда /invite без from_user")
        return

    telegram_id = message.from_user.id

    try:
        async with DatabaseSession() as session:
            # Создаём сервис
            referral_service = create_referral_service(session)

            # Проверяем, включена ли реферальная программа
            if not referral_service.is_enabled():
                await message.answer(l10n.get("invite_disabled"))
                return

            # Получаем пользователя
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(telegram_id)

            if user is None:
                logger.error(
                    "Пользователь не найден в БД: telegram_id=%d",
                    telegram_id,
                )
                await message.answer(l10n.get("error_user_not_found"))
                return

            # Получаем статистику
            stats = await referral_service.get_referral_stats(user)

            # Получаем username бота для ссылки
            bot_info = await bot.get_me()
            bot_username = bot_info.username or "bot"

            # Генерируем реферальную ссылку
            invite_link = referral_service.get_invite_link(user, bot_username)

        # Форматируем ответ
        response = l10n.get(
            "invite_info",
            link=invite_link,
            total_referrals=stats.total_referrals,
            total_earnings=stats.total_earnings,
            inviter_bonus=stats.inviter_bonus,
            max_earnings=stats.max_earnings,
        )

        # Добавляем информацию о невыплаченных бонусах (если есть)
        if stats.pending_bonuses > 0:
            response += "\n\n" + l10n.get(
                "invite_pending_bonuses",
                count=stats.pending_bonuses,
            )

        # Добавляем предупреждение если достигнут лимит
        if not stats.can_earn_more and stats.max_earnings > 0:
            response += "\n\n" + l10n.get("invite_max_reached")

        await message.answer(response)

        logger.debug(
            "Показана реферальная информация: user_id=%d, referrals=%d, earnings=%d",
            user.id,
            stats.total_referrals,
            stats.total_earnings,
        )

    except SQLAlchemyError:
        logger.exception(
            "Ошибка БД при получении реферальной информации для telegram_id=%d",
            telegram_id,
        )
        await message.answer(l10n.get("error_unknown"))

    except OSError:
        logger.exception(
            "Ошибка подключения к БД для telegram_id=%d",
            telegram_id,
        )
        await message.answer(l10n.get("error_unknown"))
