"""Обработчики покупки токенов.

Реализует flow покупки токенов через callback-кнопки (из /balance):
1. buy:start -> показываем список тарифов
2. tariff:<id> -> показываем способы оплаты
3. pay:<provider>:<tariff_id> -> создаём платёж
"""

import json
from typing import Any

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message
from sqlalchemy.exc import SQLAlchemyError

from src.bot.keyboards.inline.payments import (
    create_provider_selection_keyboard,
    create_tariff_selection_keyboard,
)
from src.config.settings import settings
from src.config.yaml_config import yaml_config
from src.db.base import DatabaseSession
from src.db.repositories.user_repo import UserRepository
from src.providers.payments import (
    PaymentError,
    create_stripe_provider,
    create_telegram_stars_provider,
    create_yookassa_provider,
)
from src.providers.payments.base import BasePaymentProvider
from src.services.payment_service import (
    ProviderNotConfiguredError,
    TariffNotAvailableForProviderError,
    TariffNotFoundError,
    create_payment_service,
)
from src.utils.i18n import Localization
from src.utils.logging import get_logger

router = Router(name="buy")
logger = get_logger(__name__)


def _get_available_providers() -> list[str]:
    """Получить список настроенных провайдеров.

    Returns:
        Список имён провайдеров, для которых есть настройки.
    """
    providers: list[str] = []

    # Telegram Stars - всегда доступны если включены
    if settings.payments.has_telegram_stars:
        providers.append("telegram_stars")

    # YooKassa - нужен shop_id и secret_key
    if settings.payments.has_yookassa:
        providers.append("yookassa")

    # Stripe - нужен secret_key
    if settings.payments.has_stripe:
        providers.append("stripe")

    return providers


def _create_providers_dict() -> dict[str, BasePaymentProvider]:
    """Создать словарь провайдеров для PaymentService.

    Returns:
        Словарь {имя_провайдера: экземпляр_провайдера}.
    """
    providers: dict[str, BasePaymentProvider] = {}

    if settings.payments.has_telegram_stars:
        providers["telegram_stars"] = create_telegram_stars_provider()

    if settings.payments.has_yookassa:
        yookassa_settings = settings.payments.yookassa
        if yookassa_settings.shop_id and yookassa_settings.secret_key:
            providers["yookassa"] = create_yookassa_provider(
                shop_id=yookassa_settings.shop_id,
                secret_key=yookassa_settings.secret_key.get_secret_value(),
            )

    if settings.payments.has_stripe:
        stripe_settings = settings.payments.stripe
        if stripe_settings.secret_key:
            webhook_secret = None
            if stripe_settings.webhook_secret:
                webhook_secret = stripe_settings.webhook_secret.get_secret_value()
            providers["stripe"] = create_stripe_provider(
                secret_key=stripe_settings.secret_key.get_secret_value(),
                webhook_secret=webhook_secret,
            )

    return providers


def _get_callback_message(callback: CallbackQuery) -> Message | None:
    """Получить сообщение из callback, если оно доступно."""
    if isinstance(callback.message, Message):
        return callback.message
    return None


@router.callback_query(F.data == "buy:start")
async def callback_buy_start(callback: CallbackQuery, l10n: Localization) -> None:
    """Обработать нажатие кнопки "Купить токены".

    Показывает список тарифов. Если нет настроенных провайдеров,
    всё равно показываем тарифы - ошибка будет при попытке оплаты.

    Args:
        callback: Callback query от кнопки.
        l10n: Объект локализации.
    """
    message = _get_callback_message(callback)
    if message is None:
        await callback.answer()
        return

    language = l10n.language

    # Проверяем провайдеры (логируем если нет)
    available_providers = _get_available_providers()
    if not available_providers:
        logger.warning("Нет настроенных провайдеров оплаты")

    # Получаем тарифы
    tariffs = yaml_config.get_enabled_tariffs()
    if not tariffs:
        await callback.answer(l10n.get("buy_no_tariffs"), show_alert=True)
        return

    # Обновляем сообщение
    keyboard = create_tariff_selection_keyboard(tariffs, language)

    await message.edit_text(
        l10n.get("buy_select_tariff"),
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "buy:back")
async def callback_buy_back(callback: CallbackQuery, l10n: Localization) -> None:
    """Обработать нажатие кнопки "Назад" - вернуться к выбору тарифа.

    Args:
        callback: Callback query от кнопки.
        l10n: Объект локализации.
    """
    message = _get_callback_message(callback)
    if message is None:
        await callback.answer()
        return

    language = l10n.language

    # Получаем тарифы
    tariffs = yaml_config.get_enabled_tariffs()

    # Обновляем сообщение
    keyboard = create_tariff_selection_keyboard(tariffs, language)

    await message.edit_text(
        l10n.get("buy_select_tariff"),
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tariff:"))
async def callback_select_tariff(callback: CallbackQuery, l10n: Localization) -> None:
    """Обработать выбор тарифа.

    Показывает способы оплаты для выбранного тарифа.

    Args:
        callback: Callback query с данными "tariff:{slug}".
        l10n: Объект локализации.
    """
    message = _get_callback_message(callback)
    if message is None or callback.data is None:
        await callback.answer()
        return

    language = l10n.language

    # Извлекаем slug тарифа
    tariff_slug = callback.data.split(":", 1)[1]

    # Получаем тариф
    tariff = yaml_config.get_tariff(tariff_slug)
    if tariff is None:
        await callback.answer(l10n.get("buy_tariff_not_found"), show_alert=True)
        return

    # Получаем доступные провайдеры
    available_providers = _get_available_providers()

    # Создаём клавиатуру выбора провайдера
    keyboard = create_provider_selection_keyboard(tariff, available_providers, language)

    # Формируем текст с информацией о тарифе
    tariff_name = tariff.name.get(language)
    text = l10n.get(
        "buy_select_provider",
        tariff_name=tariff_name,
        tokens=tariff.effective_tokens,
    )

    await message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("pay:"))
async def callback_pay(  # noqa: C901, PLR0912
    callback: CallbackQuery,
    bot: Bot,
    l10n: Localization,
) -> None:
    """Обработать выбор способа оплаты и создать платёж.

    Для Telegram Stars - отправляем invoice.
    Для YooKassa/Stripe - отправляем ссылку на оплату.

    Args:
        callback: Callback query с данными "pay:{tariff_slug}:{provider}".
        bot: Экземпляр бота для отправки invoice.
        l10n: Объект локализации.
    """
    message = _get_callback_message(callback)
    if message is None or callback.data is None or callback.from_user is None:
        await callback.answer()
        return

    # Извлекаем данные из callback_data
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer(l10n.get("error_unknown"), show_alert=True)
        return

    _, tariff_slug, provider_name = parts

    language = l10n.language
    telegram_id = callback.from_user.id

    try:
        async with DatabaseSession() as session:
            # Получаем пользователя
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(telegram_id)

            if user is None:
                await callback.answer(l10n.get("error_user_not_found"), show_alert=True)
                return

            # Создаём провайдеры
            providers = _create_providers_dict()

            # Создаём сервис платежей
            payment_service = create_payment_service(
                session=session,
                providers=providers,
            )

            # Получаем тариф для описания
            tariff = yaml_config.get_tariff(tariff_slug)
            if tariff is None:
                await callback.answer(l10n.get("buy_tariff_not_found"), show_alert=True)
                return

            tariff_name = tariff.name.get(language)

            # Создаём платёж
            payment_info = await payment_service.create_payment(
                user=user,
                tariff_slug=tariff_slug,
                provider_name=provider_name,
            )

            # Обрабатываем в зависимости от провайдера
            if provider_name == "telegram_stars":
                # Для Stars - отправляем invoice
                await _send_stars_invoice(
                    bot=bot,
                    chat_id=telegram_id,
                    tariff=tariff,
                    payment_info=payment_info,
                    l10n=l10n,
                )

                # Удаляем сообщение с выбором
                await message.delete()
                await callback.answer()

            else:
                # Для YooKassa/Stripe - отправляем ссылку
                if payment_info.confirmation_url:
                    text = l10n.get(
                        "buy_payment_link",
                        tariff_name=tariff_name,
                        tokens=tariff.effective_tokens,
                        url=payment_info.confirmation_url,
                    )
                    await message.edit_text(text)
                else:
                    await callback.answer(l10n.get("buy_error_no_url"), show_alert=True)

                await callback.answer()

            logger.info(
                "Создан платёж: user_id=%d, tariff=%s, provider=%s, payment_id=%s",
                user.id,
                tariff_slug,
                provider_name,
                payment_info.payment_id,
            )

    except TariffNotFoundError:
        await callback.answer(l10n.get("buy_tariff_not_found"), show_alert=True)
    except ProviderNotConfiguredError:
        await callback.answer(l10n.get("buy_provider_not_configured"), show_alert=True)
    except TariffNotAvailableForProviderError:
        await callback.answer(
            l10n.get("buy_tariff_not_available_for_provider"), show_alert=True
        )
    except PaymentError:
        logger.exception("Ошибка создания платежа")
        await callback.answer(l10n.get("buy_payment_error"), show_alert=True)
    except SQLAlchemyError:
        logger.exception("Ошибка БД при создании платежа")
        await callback.answer(l10n.get("error_unknown"), show_alert=True)


async def _send_stars_invoice(
    bot: Bot,
    chat_id: int,
    tariff: Any,
    payment_info: Any,
    l10n: Localization,
) -> None:
    """Отправить invoice для оплаты через Telegram Stars.

    Для подписок используется create_invoice_link (ограничение Telegram API),
    для разовых покупок - send_invoice.

    Args:
        bot: Экземпляр бота.
        chat_id: ID чата для отправки.
        tariff: Конфигурация тарифа.
        payment_info: Информация о платеже.
        l10n: Объект локализации.
    """
    language = l10n.language

    # Название и описание для invoice
    title = tariff.name.get(language)

    # Описание зависит от типа тарифа
    if tariff.is_subscription:
        description = l10n.get(
            "buy_invoice_subscription_description",
            tokens=tariff.tokens_per_period,
        )
    else:
        tokens = tariff.effective_tokens
        description = l10n.get("buy_invoice_description", tokens=tokens)

    # Формируем payload с payment_id из нашей БД
    payload = json.dumps(
        {
            "payment_id": payment_info.payment_id,
            "tariff_slug": tariff.slug,
        },
        ensure_ascii=False,
    )

    # Цена в Stars
    price = int(payment_info.amount)

    if tariff.is_subscription:
        # Для подписок Telegram требует использовать create_invoice_link
        # с параметром subscription_period (send_invoice не поддерживает подписки)
        # Документация: https://core.telegram.org/api/subscriptions
        subscription_period_seconds = 30 * 24 * 60 * 60  # 2592000 (30 дней)

        invoice_link = await bot.create_invoice_link(
            title=title,
            description=description,
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=title, amount=price)],
            subscription_period=subscription_period_seconds,
        )

        # Отправляем ссылку на оплату подписки
        await bot.send_message(
            chat_id=chat_id,
            text=l10n.get(
                "buy_subscription_invoice_link",
                tariff_name=title,
                tokens=tariff.tokens_per_period,
                stars=price,
                url=invoice_link,
            ),
        )

        logger.info(
            "Отправлена ссылка на подписку Stars: chat_id=%d, amount=%d, link=%s",
            chat_id,
            price,
            invoice_link,
        )
    else:
        # Для разовых покупок используем send_invoice
        await bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=description,
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=title, amount=price)],
        )

        logger.info(
            "Отправлен Stars invoice: chat_id=%d, amount=%d",
            chat_id,
            price,
        )


# =============================================================================
# ОБРАБОТЧИКИ TELEGRAM PAYMENTS (pre_checkout_query, successful_payment)
# =============================================================================


@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: Any) -> None:
    """Обработать pre_checkout_query от Telegram.

    Telegram отправляет этот запрос перед списанием Stars.
    Мы должны ответить ok=True чтобы подтвердить платёж.

    Args:
        pre_checkout_query: Запрос на предварительную проверку.
    """
    # Всегда подтверждаем - проверки уже были при создании invoice
    await pre_checkout_query.answer(ok=True)

    logger.debug(
        "Pre-checkout подтверждён: user_id=%d, payload=%s",
        pre_checkout_query.from_user.id,
        pre_checkout_query.invoice_payload,
    )


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, l10n: Localization) -> None:
    """Обработать successful_payment - успешную оплату через Stars.

    После успешного списания Stars Telegram отправляет сообщение
    с successful_payment. Обрабатываем два случая:

    1. Первая оплата (is_recurring=False или is_first_recurring=True):
       - Начисляем токены пользователю
       - Для подписок создаём запись в БД

    2. Автопродление подписки (is_recurring=True):
       - Telegram автоматически списал Stars
       - Продлеваем период подписки
       - Начисляем новые токены

    Args:
        message: Сообщение с данными successful_payment.
        l10n: Объект локализации.
    """
    if message.from_user is None or message.successful_payment is None:
        return

    payment = message.successful_payment
    telegram_id = message.from_user.id

    # Получаем данные о рекуррентности из платежа
    # Атрибуты могут отсутствовать в старых версиях aiogram
    is_recurring = getattr(payment, "is_recurring", False)
    is_first_recurring = getattr(payment, "is_first_recurring", False)
    subscription_expiration_date = getattr(
        payment, "subscription_expiration_date", None
    )

    logger.info(
        "Получен successful_payment: user_id=%d, amount=%d %s, "
        "is_recurring=%s, is_first_recurring=%s, expiration=%s",
        telegram_id,
        payment.total_amount,
        payment.currency,
        is_recurring,
        is_first_recurring,
        subscription_expiration_date,
    )

    try:
        # Преобразуем данные в формат для process_webhook
        # Добавляем поля для обработки подписок
        payment_data: dict[str, Any] = {
            "currency": payment.currency,
            "total_amount": payment.total_amount,
            "invoice_payload": payment.invoice_payload,
            "telegram_payment_charge_id": payment.telegram_payment_charge_id,
            "provider_payment_charge_id": payment.provider_payment_charge_id,
            # Поля для нативных подписок Telegram Stars
            "is_recurring": is_recurring,
            "is_first_recurring": is_first_recurring,
            "subscription_expiration_date": subscription_expiration_date,
        }

        async with DatabaseSession() as session:
            # Создаём провайдер Stars
            providers: dict[str, BasePaymentProvider] = {
                "telegram_stars": create_telegram_stars_provider()
            }

            # Обрабатываем через PaymentService
            payment_service = create_payment_service(
                session=session,
                providers=providers,
            )

            result = await payment_service.process_webhook(
                "telegram_stars", payment_data
            )

            if result.is_success:
                # Получаем количество токенов и тип тарифа из payload
                tokens_amount = 0
                is_subscription = False
                tariff_slug = result.tariff_slug
                if tariff_slug:
                    tariff = yaml_config.get_tariff(tariff_slug)
                    if tariff:
                        is_subscription = tariff.is_subscription
                        # Для подписок - tokens_per_period, для разовых - tokens
                        tokens_amount = tariff.effective_tokens

                # Формируем сообщение в зависимости от типа платежа
                if is_recurring and not is_first_recurring:
                    # Автопродление подписки - специальное сообщение
                    await message.answer(
                        l10n.get("buy_subscription_renewed", tokens=tokens_amount),
                    )
                    logger.info(
                        "Подписка продлена через Stars: user_id=%d, tokens=%d",
                        telegram_id,
                        tokens_amount,
                    )
                elif is_subscription:
                    # Первая покупка подписки
                    await message.answer(
                        l10n.get("buy_subscription_activated", tokens=tokens_amount),
                    )
                    logger.info(
                        "Подписка активирована через Stars: user_id=%d, tokens=%d",
                        telegram_id,
                        tokens_amount,
                    )
                else:
                    # Разовая покупка токенов
                    await message.answer(
                        l10n.get("buy_success", tokens=tokens_amount),
                    )
                    logger.info(
                        "Токены начислены через Stars: user_id=%d, tokens=%d",
                        telegram_id,
                        tokens_amount,
                    )
            else:
                await message.answer(l10n.get("buy_payment_failed"))

    except Exception:
        logger.exception("Ошибка обработки successful_payment")
        await message.answer(l10n.get("error_unknown"))


@router.message(F.refunded_payment)
async def refunded_payment_handler(message: Message, l10n: Localization) -> None:
    """Обработать refunded_payment - возврат платежа через Stars.

    Telegram отправляет это сообщение когда:
    1. Бот инициировал возврат через bot.refund_star_payment()
    2. Пользователь запросил chargeback (отмену)
    3. Подписка была отменена и Stars возвращены

    При получении обновляем статус платежа в БД на REFUNDED.

    Args:
        message: Сообщение с данными refunded_payment.
        l10n: Объект локализации.
    """
    if message.from_user is None or message.refunded_payment is None:
        return

    refund = message.refunded_payment
    telegram_id = message.from_user.id

    logger.info(
        "Получен refunded_payment: user_id=%d, amount=%d %s, charge_id=%s",
        telegram_id,
        refund.total_amount,
        refund.currency,
        refund.telegram_payment_charge_id,
    )

    try:
        async with DatabaseSession() as session:
            from src.db.repositories.payment_repo import PaymentRepository
            from src.db.repositories.subscription_repo import SubscriptionRepository

            payment_repo = PaymentRepository(session)
            subscription_repo = SubscriptionRepository(session)

            # Ищем платёж по telegram_payment_charge_id
            payment = await payment_repo.get_by_provider_id(
                "telegram_stars", refund.telegram_payment_charge_id
            )

            if payment:
                # Обновляем статус платежа на REFUNDED
                from src.db.models.payment import PaymentStatus

                await payment_repo.update_status(payment, PaymentStatus.REFUNDED)

                # Если это был подписочный платёж - отменяем подписку
                tariff = yaml_config.get_tariff(payment.tariff_slug)
                if tariff and tariff.is_subscription:
                    # Ищем активную подписку пользователя
                    subscription = await subscription_repo.get_active_subscription(
                        payment.user_id
                    )
                    if subscription:
                        from src.db.models.subscription import SubscriptionStatus

                        subscription.status = SubscriptionStatus.CANCELED
                        subscription.auto_renewal = False
                        subscription.cancel_at_period_end = True

                        logger.info(
                            "Подписка отменена из-за refund: subscription_id=%d, "
                            "user_id=%d",
                            subscription.id,
                            payment.user_id,
                        )

                await session.commit()

                logger.info(
                    "Платёж помечен как REFUNDED: payment_id=%d, user_id=%d",
                    payment.id,
                    payment.user_id,
                )
            else:
                logger.warning(
                    "Платёж не найден для refund: charge_id=%s, user_id=%d",
                    refund.telegram_payment_charge_id,
                    telegram_id,
                )

        # Уведомляем пользователя о возврате
        await message.answer(
            l10n.get("buy_payment_refunded", amount=refund.total_amount),
        )

    except Exception:
        logger.exception("Ошибка обработки refunded_payment")
        # Не показываем ошибку пользователю - возврат уже произошёл
