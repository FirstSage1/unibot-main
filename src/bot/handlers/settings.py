"""Обработчик команды /settings для управления настройками пользователя.

Предоставляет единую точку доступа к настройкам:
- Смена языка интерфейса (если localization.enabled=true)
- Управление подпиской и автопродлением

Архитектура:
- Команда /settings показывает inline-клавиатуру с доступными настройками
- Каждая настройка открывает своё подменю через callback_query
- После изменения настройки пользователь возвращается в главное меню
"""

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.bot.keyboards import create_language_keyboard
from src.config.yaml_config import yaml_config
from src.db.base import DatabaseSession
from src.db.exceptions import (
    DatabaseConnectionError,
    DatabaseOperationError,
    UserNotFoundError,
)
from src.db.repositories.user_repo import UserRepository
from src.services.subscription_service import create_subscription_service
from src.utils.i18n import Localization, create_localization
from src.utils.logging import get_logger

router = Router(name="settings")
logger = get_logger(__name__)

# Префиксы для callback_data
SETTINGS_PREFIX = "settings:"
SETTINGS_LANG_PREFIX = "settings_lang:"
SETTINGS_SUB_PREFIX = "settings_sub:"


def create_settings_keyboard(l10n: Localization) -> InlineKeyboardMarkup:
    """Создать клавиатуру главного меню настроек.

    Показывает доступные настройки в зависимости от конфигурации:
    - Язык (если localization.enabled=true)
    - Подписка (если есть подписочные тарифы)

    Args:
        l10n: Объект локализации для получения текстов кнопок.

    Returns:
        InlineKeyboardMarkup с кнопками настроек.
    """
    buttons: list[list[InlineKeyboardButton]] = []

    # Кнопка смены языка (только если мультиязычность включена)
    if Localization.is_enabled():
        buttons.append(
            [
                InlineKeyboardButton(
                    text=l10n.get("settings_language_button"),
                    callback_data=f"{SETTINGS_PREFIX}language",
                )
            ]
        )

    # Кнопка управления подпиской (только если есть подписочные тарифы)
    if yaml_config.has_subscription_tariffs():
        buttons.append(
            [
                InlineKeyboardButton(
                    text=l10n.get("settings_subscription_button"),
                    callback_data=f"{SETTINGS_PREFIX}subscription",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("settings"))
async def cmd_settings(message: Message, l10n: Localization) -> None:
    """Обработать команду /settings.

    Показывает главное меню настроек с inline-клавиатурой.
    Доступные настройки зависят от конфигурации бота.

    Args:
        message: Входящее сообщение с командой /settings.
        l10n: Объект локализации (внедряется через LanguageMiddleware).
    """
    keyboard = create_settings_keyboard(l10n)

    # Если нет доступных настроек — показываем сообщение об этом
    if not keyboard.inline_keyboard:
        await message.answer(l10n.get("settings_no_options"))
        return

    await message.answer(
        l10n.get("settings_title"),
        reply_markup=keyboard,
        parse_mode="HTML",
    )


@router.callback_query(F.data == f"{SETTINGS_PREFIX}language")
async def process_settings_language(
    callback: CallbackQuery, l10n: Localization
) -> None:
    """Обработать нажатие кнопки "Язык" в настройках.

    Показывает клавиатуру выбора языка (аналогично /language).

    Args:
        callback: Callback от inline-кнопки.
        l10n: Объект локализации.
    """
    # Проверяем, что мультиязычность включена
    if not Localization.is_enabled():
        await callback.answer(l10n.get("error_unknown"))
        return

    # Создаём клавиатуру с языками
    keyboard = create_language_keyboard(l10n, callback_prefix=SETTINGS_LANG_PREFIX)

    # Добавляем кнопку "Назад"
    keyboard.inline_keyboard.append(
        [
            InlineKeyboardButton(
                text=l10n.get("settings_back_button"),
                callback_data=f"{SETTINGS_PREFIX}back",
            )
        ]
    )

    # Редактируем сообщение
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            l10n.get("language_command"),
            reply_markup=keyboard,
        )

    await callback.answer()


@router.callback_query(F.data.startswith(SETTINGS_LANG_PREFIX))
async def process_settings_language_selection(
    callback: CallbackQuery, l10n: Localization
) -> None:
    """Обработать выбор языка в настройках.

    Callback data формат: "settings_lang:<language_code>"
    Например: "settings_lang:ru", "settings_lang:en"

    После выбора языка показывает главное меню настроек на новом языке.

    Args:
        callback: Callback от inline-кнопки.
        l10n: Объект локализации.
    """
    if not callback.data:
        await callback.answer(l10n.get("error_callback_data"))
        return

    # Извлекаем код языка из callback_data
    # Формат: "settings_lang:ru" → "ru"
    selected_language = callback.data.split(":", 1)[1]

    # Проверяем, что выбранный язык доступен
    available_languages = Localization.get_available_languages()
    if selected_language not in available_languages:
        logger.warning(
            "Попытка выбрать недоступный язык в настройках: %s (доступны: %s)",
            selected_language,
            available_languages,
        )
        await callback.answer(l10n.get("error_language_not_supported"))
        return

    # Получаем пользователя из callback
    tg_user = callback.from_user

    # Обновляем язык пользователя в БД
    try:
        async with DatabaseSession() as session:
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(tg_user.id)

            if user is None:
                raise UserNotFoundError(tg_user.id)

            # Сохраняем старый язык для логирования
            old_language = user.language

            # Обновляем язык
            await repo.update_language(user, selected_language)

        logger.info(
            "Язык изменён через настройки: user_id=%d, telegram_id=%d, old=%s, new=%s",
            user.id,
            tg_user.id,
            old_language,
            selected_language,
        )

        # Создаём объект локализации с НОВЫМ языком для ответа
        new_l10n = create_localization(selected_language)

        # Получаем название выбранного языка
        language_name = new_l10n.get(f"language_name_{selected_language}")

        # Показываем подтверждение и возвращаемся в меню настроек
        keyboard = create_settings_keyboard(new_l10n)

        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                new_l10n.get("settings_language_changed", language_name=language_name),
                reply_markup=keyboard,
                parse_mode="HTML",
            )

        await callback.answer()

    except UserNotFoundError:
        logger.exception(
            "Пользователь не найден в БД при обновлении языка: telegram_id=%d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_user_not_found"), show_alert=True)

    except DatabaseConnectionError:
        logger.exception(
            "Ошибка подключения к БД при обновлении языка для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_db_temporary"), show_alert=True)

    except DatabaseOperationError:
        logger.exception(
            "Ошибка операции БД при обновлении языка для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_db_permanent"), show_alert=True)

    except Exception:
        logger.exception(
            "Неожиданная ошибка при обновлении языка для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_unexpected"), show_alert=True)


@router.callback_query(F.data == f"{SETTINGS_PREFIX}back")
async def process_settings_back(callback: CallbackQuery, l10n: Localization) -> None:
    """Обработать нажатие кнопки "Назад" в подменю настроек.

    Возвращает пользователя в главное меню настроек.

    Args:
        callback: Callback от inline-кнопки.
        l10n: Объект локализации.
    """
    keyboard = create_settings_keyboard(l10n)

    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            l10n.get("settings_title"),
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    await callback.answer()


@router.callback_query(F.data == f"{SETTINGS_PREFIX}subscription")
async def process_settings_subscription(  # noqa: C901, PLR0912
    callback: CallbackQuery, l10n: Localization
) -> None:
    """Обработать нажатие кнопки "Управление подпиской".

    Показывает информацию о подписке и кнопки управления.

    Args:
        callback: Callback от inline-кнопки.
        l10n: Объект локализации.
    """
    # Проверяем, что есть подписочные тарифы
    if not yaml_config.has_subscription_tariffs():
        await callback.answer(l10n.get("error_unknown"))
        return

    # Получаем пользователя из callback
    tg_user = callback.from_user

    try:
        async with DatabaseSession() as session:
            # Получаем пользователя из БД
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(tg_user.id)

            if user is None:
                raise UserNotFoundError(tg_user.id)

            # Получаем информацию о подписке
            subscription_service = create_subscription_service(session)
            subscription = await subscription_service.get_active_subscription(user)

            # Создаём клавиатуру
            buttons: list[list[InlineKeyboardButton]] = []

            if subscription is None:
                # Нет активной подписки
                message_text = l10n.get("settings_subscription_none")
            else:
                # Форматируем дату окончания
                period_end_str = subscription.period_end.strftime("%d.%m.%Y")

                # Форматируем auto_renewal
                auto_renewal_text = (
                    "✅ Включено" if subscription.auto_renewal else "❌ Выключено"
                )

                # Получаем название тарифа
                tariff = yaml_config.get_tariff(subscription.tariff_slug)
                if tariff:
                    lang_name = tariff.name.get(user.language)
                    default_name = tariff.name.get("ru", subscription.tariff_slug)
                    tariff_name = lang_name or default_name
                else:
                    tariff_name = subscription.tariff_slug

                # Формируем сообщение
                message_text = l10n.get(
                    "settings_subscription_info",
                    tariff_name=tariff_name,
                    period_end=period_end_str,
                    tokens_remaining=subscription.tokens_remaining,
                    auto_renewal=auto_renewal_text,
                )

                # Добавляем кнопки управления
                if subscription.auto_renewal:
                    # Если автопродление включено — показываем кнопку отмены
                    buttons.append(
                        [
                            InlineKeyboardButton(
                                text=l10n.get("settings_subscription_cancel_button"),
                                callback_data=f"{SETTINGS_SUB_PREFIX}cancel",
                            )
                        ]
                    )
                else:
                    # Если отключено — показываем кнопку включения
                    buttons.append(
                        [
                            InlineKeyboardButton(
                                text=l10n.get(
                                    "settings_subscription_enable_auto_renewal_button"
                                ),
                                callback_data=f"{SETTINGS_SUB_PREFIX}enable",
                            )
                        ]
                    )

            # Кнопка "Назад"
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=l10n.get("settings_back_button"),
                        callback_data=f"{SETTINGS_PREFIX}back",
                    )
                ]
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            # Редактируем сообщение
            if isinstance(callback.message, Message):
                await callback.message.edit_text(
                    message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

            await callback.answer()

    except UserNotFoundError:
        logger.exception(
            "Пользователь не найден в БД при просмотре подписки: telegram_id=%d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_user_not_found"), show_alert=True)

    except DatabaseConnectionError:
        logger.exception(
            "Ошибка подключения к БД при просмотре подписки для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_db_temporary"), show_alert=True)

    except DatabaseOperationError:
        logger.exception(
            "Ошибка операции БД при просмотре подписки для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_db_permanent"), show_alert=True)

    except Exception:
        logger.exception(
            "Неожиданная ошибка при просмотре подписки для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_unexpected"), show_alert=True)


@router.callback_query(F.data == f"{SETTINGS_SUB_PREFIX}cancel")
async def process_subscription_cancel(
    callback: CallbackQuery, l10n: Localization
) -> None:
    """Обработать запрос на отмену подписки.

    Показывает подтверждение перед отменой.

    Args:
        callback: Callback от inline-кнопки.
        l10n: Объект локализации.
    """
    # Получаем пользователя из callback
    tg_user = callback.from_user

    try:
        async with DatabaseSession() as session:
            # Получаем пользователя из БД
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(tg_user.id)

            if user is None:
                raise UserNotFoundError(tg_user.id)

            # Получаем подписку
            subscription_service = create_subscription_service(session)
            subscription = await subscription_service.get_active_subscription(user)

            if subscription is None:
                await callback.answer(
                    l10n.get("settings_subscription_none"), show_alert=True
                )
                return

            # Форматируем дату окончания
            period_end_str = subscription.period_end.strftime("%d.%m.%Y")

            # Показываем подтверждение
            message_text = l10n.get(
                "settings_subscription_cancel_confirm",
                period_end=period_end_str,
            )

            # Клавиатура с подтверждением
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=l10n.get("settings_subscription_cancel_confirm_yes"),
                            callback_data=f"{SETTINGS_SUB_PREFIX}cancel_confirm",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text=l10n.get("settings_subscription_cancel_confirm_no"),
                            callback_data=f"{SETTINGS_PREFIX}subscription",
                        )
                    ],
                ]
            )

            # Редактируем сообщение
            if isinstance(callback.message, Message):
                await callback.message.edit_text(
                    message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

            await callback.answer()

    except UserNotFoundError:
        logger.exception(
            "Пользователь не найден в БД при отмене подписки: telegram_id=%d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_user_not_found"), show_alert=True)

    except Exception:
        logger.exception(
            "Ошибка при отмене подписки для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_unexpected"), show_alert=True)


@router.callback_query(F.data == f"{SETTINGS_SUB_PREFIX}cancel_confirm")
async def process_subscription_cancel_confirm(
    callback: CallbackQuery, l10n: Localization, bot: Bot
) -> None:
    """Подтвердить отмену подписки.

    Для подписок Telegram Stars дополнительно вызывается
    bot.edit_user_star_subscription для отмены автопродления на стороне Telegram.

    Args:
        callback: Callback от inline-кнопки.
        l10n: Объект локализации.
        bot: Экземпляр Telegram-бота для отмены Stars подписок.
    """
    # Получаем пользователя из callback
    tg_user = callback.from_user

    try:
        async with DatabaseSession() as session:
            # Получаем пользователя из БД
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(tg_user.id)

            if user is None:
                raise UserNotFoundError(tg_user.id)

            # Получаем подписку
            subscription_service = create_subscription_service(session)
            subscription = await subscription_service.get_active_subscription(user)

            if subscription is None:
                await callback.answer(
                    l10n.get("settings_subscription_none"), show_alert=True
                )
                return

            # Для подписок Telegram Stars отменяем автопродление через Bot API.
            # Это нужно делать ДО отмены в нашей БД, чтобы Telegram перестал
            # автоматически списывать Stars.
            is_stars = subscription.provider == "telegram_stars"
            payment_id = subscription.payment_method_id
            if is_stars and payment_id is not None:
                try:
                    await bot.edit_user_star_subscription(
                        user_id=tg_user.id,
                        telegram_payment_charge_id=payment_id,
                        is_canceled=True,
                    )
                    logger.info(
                        "Отменена Stars подписка: user_id=%d, charge_id=%s",
                        tg_user.id,
                        subscription.payment_method_id,
                    )
                except Exception:
                    # Логируем ошибку, но продолжаем — подписка отменится в БД.
                    # Ошибка может быть если подписка уже отменена в Telegram.
                    logger.exception(
                        "Не удалось отменить Stars подписку: user_id=%d",
                        tg_user.id,
                    )

            # Отменяем подписку в нашей БД
            await subscription_service.cancel_subscription(
                subscription,
                reason="User canceled via settings",
            )
            await session.commit()

            # Форматируем дату окончания
            period_end_str = subscription.period_end.strftime("%d.%m.%Y")

            # Показываем подтверждение
            message_text = l10n.get(
                "settings_subscription_canceled",
                period_end=period_end_str,
            )

            # Возвращаемся в главное меню настроек
            keyboard = create_settings_keyboard(l10n)

            # Редактируем сообщение
            if isinstance(callback.message, Message):
                await callback.message.edit_text(
                    message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

            await callback.answer()

    except UserNotFoundError:
        logger.exception(
            "Пользователь не найден в БД при подтверждении отмены: telegram_id=%d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_user_not_found"), show_alert=True)

    except Exception:
        logger.exception(
            "Ошибка при подтверждении отмены подписки для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_unexpected"), show_alert=True)


@router.callback_query(F.data == f"{SETTINGS_SUB_PREFIX}enable")
async def process_subscription_enable_auto_renewal(
    callback: CallbackQuery, l10n: Localization, bot: Bot
) -> None:
    """Включить автопродление подписки.

    Для подписок Telegram Stars дополнительно вызывается
    bot.edit_user_star_subscription с is_canceled=False для восстановления.

    Args:
        callback: Callback от inline-кнопки.
        l10n: Объект локализации.
        bot: Экземпляр Telegram-бота для восстановления Stars подписок.
    """
    # Получаем пользователя из callback
    tg_user = callback.from_user

    try:
        async with DatabaseSession() as session:
            # Получаем пользователя из БД
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(tg_user.id)

            if user is None:
                raise UserNotFoundError(tg_user.id)

            # Получаем подписку
            subscription_service = create_subscription_service(session)
            subscription = await subscription_service.get_active_subscription(user)

            if subscription is None:
                await callback.answer(
                    l10n.get("settings_subscription_none"), show_alert=True
                )
                return

            # Для подписок Telegram Stars восстанавливаем автопродление через Bot API.
            # Это нужно делать ДО изменения в БД, чтобы Telegram продолжил
            # автоматически списывать Stars.
            is_stars = subscription.provider == "telegram_stars"
            payment_id = subscription.payment_method_id
            if is_stars and payment_id is not None:
                try:
                    await bot.edit_user_star_subscription(
                        user_id=tg_user.id,
                        telegram_payment_charge_id=payment_id,
                        is_canceled=False,  # Восстанавливаем подписку
                    )
                    logger.info(
                        "Восстановлена Stars подписка: user_id=%d, charge_id=%s",
                        tg_user.id,
                        payment_id,
                    )
                except Exception:
                    # Если не удалось восстановить в Telegram, не обновляем БД
                    logger.exception(
                        "Не удалось восстановить Stars подписку: user_id=%d",
                        tg_user.id,
                    )
                    restore_failed_msg = l10n.get(
                        "settings_subscription_restore_failed"
                    )
                    await callback.answer(restore_failed_msg, show_alert=True)
                    return

            # Включаем автопродление
            from src.db.models.subscription import SubscriptionStatus

            # Включаем автопродление и снимаем флаг отмены
            subscription.auto_renewal = True
            subscription.cancel_at_period_end = False
            subscription.status = SubscriptionStatus.ACTIVE

            await session.flush()
            await session.commit()

            logger.info(
                "Автопродление включено: subscription_id=%d, user_id=%d",
                subscription.id,
                user.id,
            )

            # Форматируем дату окончания
            period_end_str = subscription.period_end.strftime("%d.%m.%Y")

            # Показываем подтверждение
            message_text = l10n.get(
                "settings_subscription_auto_renewal_enabled",
                period_end=period_end_str,
            )

            # Возвращаемся в главное меню настроек
            keyboard = create_settings_keyboard(l10n)

            # Редактируем сообщение
            if isinstance(callback.message, Message):
                await callback.message.edit_text(
                    message_text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )

            await callback.answer()

    except UserNotFoundError:
        logger.exception(
            "Пользователь не найден в БД при включении автопродления: telegram_id=%d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_user_not_found"), show_alert=True)

    except Exception:
        logger.exception(
            "Ошибка при включении автопродления для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_unexpected"), show_alert=True)
