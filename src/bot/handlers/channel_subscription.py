"""Обработчик callback проверки подписки на канал.

Этот handler является fallback для случаев, когда ChannelSubscriptionMiddleware
по какой-то причине не обрабатывает callback "check_channel_sub".

Обычно callback обрабатывается в middleware (channel_subscription.py).
Но если middleware не срабатывает (например, из-за порядка middleware или кеширования),
этот handler подхватит callback и обработает его.

Логика:
1. Получаем настройки канала из settings
2. Проверяем подписку пользователя через Bot API
3. Показываем соответствующее сообщение (подписан/не подписан)
"""

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot.middleware import CALLBACK_CHECK_SUBSCRIPTION
from src.config.settings import settings
from src.utils.i18n import Localization
from src.utils.logging import get_logger

router = Router(name="channel_subscription")
logger = get_logger(__name__)

# Статусы, которые считаются "подписан на канал"
SUBSCRIBED_STATUSES = frozenset(
    {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }
)


def _format_channel_url(invite_link: str | None) -> str | None:
    """Преобразовать invite_link в полный URL канала.

    Поддерживаемые форматы:
    - @channelname -> https://t.me/channelname
    - https://t.me/... -> как есть
    - channelname -> https://t.me/channelname

    Args:
        invite_link: Ссылка на канал в любом формате.

    Returns:
        URL канала или None если invite_link не задан.
    """
    if not invite_link:
        return None

    if invite_link.startswith("@"):
        return f"https://t.me/{invite_link[1:]}"
    if invite_link.startswith("https://"):
        return invite_link
    return f"https://t.me/{invite_link}"


def _create_subscription_keyboard(l10n: Localization) -> InlineKeyboardMarkup:
    """Создать клавиатуру с кнопками подписки.

    Args:
        l10n: Объект локализации для текстов кнопок.

    Returns:
        Клавиатура с кнопками.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    # Кнопка "Подписаться на канал" (если есть invite_link)
    channel_url = _format_channel_url(settings.channel.invite_link)
    if channel_url:
        subscribe_text = l10n.get("channel_subscription_button")
        buttons.append([InlineKeyboardButton(text=subscribe_text, url=channel_url)])

    # Кнопка "Проверить подписку" (callback)
    check_text = l10n.get("channel_subscription_check_button")
    buttons.append(
        [
            InlineKeyboardButton(
                text=check_text,
                callback_data=CALLBACK_CHECK_SUBSCRIPTION,
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data == CALLBACK_CHECK_SUBSCRIPTION)
async def handle_check_subscription_callback(
    callback: CallbackQuery,
    bot: Bot,
    l10n: Localization,
) -> None:
    """Обработать callback кнопки "Проверить подписку".

    Этот handler — fallback для случаев, когда middleware не обработал callback.
    Проверяет подписку пользователя и показывает соответствующее сообщение.

    Args:
        callback: Входящий callback от кнопки.
        bot: Экземпляр Telegram-бота (внедряется aiogram).
        l10n: Объект локализации (внедряется через LanguageMiddleware).
    """
    user_id = callback.from_user.id

    # Получаем настройки канала
    channel_settings = settings.channel

    # Если проверка подписки отключена — просто отвечаем на callback
    if not channel_settings.is_enabled or channel_settings.required_id is None:
        logger.warning(
            "Callback check_channel_sub получен, но проверка подписки отключена. "
            "user_id=%d",
            user_id,
        )
        await callback.answer()
        return

    # Проверяем подписку через Bot API
    try:
        chat_member = await bot.get_chat_member(
            chat_id=channel_settings.required_id,
            user_id=user_id,
        )
        is_subscribed = chat_member.status in SUBSCRIBED_STATUSES

        logger.debug(
            "Проверка подписки (fallback): user_id=%d, status=%s, subscribed=%s",
            user_id,
            chat_member.status,
            is_subscribed,
        )

    except TelegramAPIError as e:
        # Ошибка API — пропускаем пользователя (fallback)
        logger.warning(
            "Ошибка проверки подписки (fallback handler) для user_id=%d: %s",
            user_id,
            e,
        )
        # Показываем сообщение об ошибке
        await callback.answer(
            l10n.get("error_unexpected"),
            show_alert=True,
        )
        return

    if is_subscribed:
        # Пользователь подписался — благодарим
        logger.info(
            "Пользователь %d подтвердил подписку на канал %d (fallback handler)",
            user_id,
            channel_settings.required_id,
        )

        text = l10n.get("channel_subscription_thanks")

        # Отвечаем на callback (убираем "часики" на кнопке)
        await callback.answer()

        # Редактируем исходное сообщение, убирая кнопки
        try:
            if callback.message and isinstance(callback.message, Message):
                await callback.message.edit_text(text)
        except TelegramAPIError as e:
            logger.warning(
                "Не удалось отредактировать сообщение о подписке (fallback): %s",
                e,
            )
    else:
        # Пользователь ещё не подписан — напоминаем
        logger.info(
            "Пользователь %d ещё не подписан на канал %d (fallback handler)",
            user_id,
            channel_settings.required_id,
        )

        text = l10n.get("channel_subscription_not_subscribed")

        # Показываем сообщение во всплывающем окне (alert)
        try:
            await callback.answer(text, show_alert=True)
        except TelegramAPIError as e:
            logger.warning(
                "Не удалось отправить alert о неподписке (fallback): %s",
                e,
            )
