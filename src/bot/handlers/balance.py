"""Обработчик команды /balance для просмотра баланса токенов.

Показывает пользователю:
- Текущий баланс токенов
- Информацию о подписке (если есть активная)
- Информацию о системе биллинга (включена/отключена)
- Кнопку для покупки токенов (если есть провайдеры оплаты)

Если биллинг отключён (billing.enabled=false) — все генерации бесплатны.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError

from src.bot.keyboards.inline.payments import create_buy_button_keyboard
from src.config.settings import settings
from src.db.base import DatabaseSession
from src.db.repositories.user_repo import UserRepository
from src.services.billing_service import create_billing_service
from src.utils.i18n import Localization
from src.utils.logging import get_logger

router = Router(name="balance")
logger = get_logger(__name__)

# Формат даты для отображения периода подписки
DATE_FORMAT = "%d.%m.%Y"


def _format_auto_renewal(enabled: bool, language: str) -> str:
    """Форматировать статус автопродления для отображения.

    Args:
        enabled: Включено ли автопродление.
        language: Код языка (ru, en).

    Returns:
        Локализованная строка статуса.
    """
    if language == "ru":
        return "✅ Включено" if enabled else "❌ Отключено"
    return "✅ Enabled" if enabled else "❌ Disabled"


@router.message(Command("balance"))
async def cmd_balance(message: Message, l10n: Localization) -> None:
    """Обработать команду /balance.

    Показывает текущий баланс токенов и информацию о подписке.

    Args:
        message: Входящее сообщение с командой /balance.
        l10n: Объект локализации (внедряется через LanguageMiddleware).
    """
    if message.from_user is None:
        logger.warning("Команда /balance без from_user")
        return

    telegram_id = message.from_user.id

    try:
        async with DatabaseSession() as session:
            # Получаем пользователя
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(telegram_id)

            if user is None:
                # Пользователь не найден (не должно происходить при нормальной работе)
                logger.error(
                    "Пользователь не найден в БД: telegram_id=%d",
                    telegram_id,
                )
                await message.answer(l10n.get("error_user_not_found"))
                return

            # Получаем информацию о балансе
            billing = create_billing_service(session)
            info = await billing.get_balance_info(user)

        # Форматируем и отправляем ответ
        if info.billing_enabled:
            # Биллинг включён
            if info.has_subscription:
                # Есть активная подписка — показываем расширенную информацию
                period_end_str = (
                    info.subscription_period_end.strftime(DATE_FORMAT)
                    if info.subscription_period_end
                    else "—"
                )
                auto_renewal_str = _format_auto_renewal(
                    info.subscription_auto_renewal, l10n.language
                )

                response = l10n.get(
                    "balance_info_with_subscription",
                    subscription_name=info.subscription_name or "—",
                    subscription_tokens=info.subscription_tokens,
                    balance=info.balance,
                    total_tokens=info.total_tokens,
                    period_end=period_end_str,
                    auto_renewal=auto_renewal_str,
                )
            else:
                # Нет подписки — показываем только баланс
                response = l10n.get("balance_info", balance=info.balance)

            # Добавляем кнопку покупки, если есть провайдеры оплаты
            # Проверяем наличие хотя бы одного провайдера (YooKassa, Stripe или Stars)
            if settings.payments.has_any_provider:
                keyboard = create_buy_button_keyboard(language=l10n.language)
                await message.answer(response, reply_markup=keyboard)
            else:
                # Провайдеры не настроены — показываем баланс без кнопки
                await message.answer(response)
        else:
            # Биллинг отключён — все генерации бесплатны
            response = l10n.get("balance_info_disabled")
            await message.answer(response)

        logger.debug(
            "Показан баланс: user_id=%d, balance=%d, subscription=%s, enabled=%s",
            user.id,
            info.balance,
            info.has_subscription,
            info.billing_enabled,
        )

    except SQLAlchemyError:
        logger.exception(
            "Ошибка БД при получении баланса для telegram_id=%d",
            telegram_id,
        )
        await message.answer(l10n.get("error_unknown"))

    except OSError:
        logger.exception(
            "Ошибка подключения к БД при получении баланса для telegram_id=%d",
            telegram_id,
        )
        await message.answer(l10n.get("error_unknown"))
