"""Обработчик команды /start.

При первом /start создаёт пользователя в базе данных.
При повторном — обновляет данные профиля (username, имя).

Автоопределение языка:
- При первом /start определяет язык из Telegram (User.language_code)
- Если язык доступен в available_languages — использует его
- Если нет — использует default_language из конфига

Согласие с документами:
- При первом /start показывает запрос на согласие с юр. документами
- После согласия показывает приветствие и начисляет бонус
- При обновлении версии документов запрашивает повторное согласие
"""

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, Message, ReplyKeyboardRemove

from src.bot.static import WELCOME_IMAGE
from src.config.yaml_config import yaml_config
from src.db.base import DatabaseSession
from src.db.repositories.user_repo import UserRepository
from src.services.billing_service import create_billing_service
from src.services.referral_service import create_referral_service
from src.utils.i18n import Localization
from src.utils.logging import get_logger

router = Router(name="start")
logger = get_logger(__name__)

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

# Длина префикса команды "/start " для извлечения параметра
# Telegram deep links: t.me/bot?start=promo → /start promo
COMMAND_START_PREFIX_LENGTH = 7  # len("/start ") = 7


def _extract_start_param(message: Message) -> str | None:
    """Извлечь start-параметр из команды /start.

    Telegram позволяет передавать параметры через deep link:
    t.me/bot?start=promo_winter → /start promo_winter

    Args:
        message: Сообщение с командой /start.

    Returns:
        Параметр после /start или None если его нет.
    """
    if message.text and len(message.text) > COMMAND_START_PREFIX_LENGTH:
        return message.text[COMMAND_START_PREFIX_LENGTH:].strip() or None
    return None


def _detect_user_language(telegram_language_code: str | None) -> str:
    """Определить язык пользователя на основе language_code из Telegram.

    Логика:
    1. Если language_code пустой или None → язык по умолчанию
    2. Если язык есть в available_languages → используем его
    3. Иначе → язык по умолчанию

    Args:
        telegram_language_code: Код языка из Telegram (ru, en, zh и т.д.).
            Может быть полным locale: "ru-RU", "en-US".

    Returns:
        Код языка для использования в боте (ISO 639-1: ru, en).

    Example:
        >>> _detect_user_language("ru")  # ru в available_languages
        "ru"
        >>> _detect_user_language("ru-RU")  # Полный locale → извлекаем "ru"
        "ru"
        >>> _detect_user_language("fr")  # fr НЕ в available_languages
        "ru"  # default_language
        >>> _detect_user_language(None)
        "ru"  # default_language
    """
    if not telegram_language_code:
        return Localization.get_default_language()

    # Telegram может передать полный locale вида "ru-RU" или "en-US"
    # Берём только код языка до дефиса (ISO 639-1)
    language_code = telegram_language_code.lower().split("-")[0]

    # Проверяем, доступен ли язык
    available_languages = Localization.get_available_languages()
    if language_code in available_languages:
        return language_code

    # Язык недоступен — используем язык по умолчанию
    return Localization.get_default_language()


@router.message(CommandStart())
async def cmd_start(message: Message, l10n: Localization) -> None:
    """Обработать команду /start.

    При первом запуске:
    - Создаёт пользователя в БД
    - Определяет язык на основе Telegram language_code
    - Сохраняет start-параметр (откуда пришёл)
    - Показывает запрос на согласие с юр. документами (если настроено)

    При повторном запуске:
    - Обновляет данные профиля (username мог измениться)
    - Проверяет, нужно ли повторное согласие (если версия изменилась)

    Args:
        message: Входящее сообщение с командой /start.
        l10n: Объект локализации (внедряется через LanguageMiddleware).
    """
    tg_user = message.from_user
    if not tg_user:
        # Теоретически невозможно для личных сообщений
        await message.answer(l10n.get("start_message"))
        return

    # Извлекаем start-параметр (для аналитики и рефералов)
    source = _extract_start_param(message)

    # Определяем язык на основе Telegram language_code
    # Если язык доступен — используем его, иначе — язык по умолчанию
    detected_language = _detect_user_language(tg_user.language_code)

    # Получаем конфигурацию юридических документов
    legal_config = yaml_config.legal

    # Флаг: нужно ли показать запрос на согласие
    needs_terms_acceptance = False

    # Сохраняем/обновляем пользователя в БД
    # Переменная для хранения бонуса (начисляется только после согласия)
    registration_bonus = 0
    # Бонус за переход по реферальной ссылке
    referral_bonus = 0

    async with DatabaseSession() as session:
        repo = UserRepository(session)
        user, created = await repo.get_or_create(
            telegram_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            language=detected_language,
            source=source,
        )

        if created:
            logger.info(
                "Новый пользователь: %s (id=%d), language=%s, source=%s",
                tg_user.full_name,
                tg_user.id,
                detected_language,
                source,
            )

            # Обрабатываем реферальную ссылку (если есть)
            # Формат: ref_TELEGRAM_ID → начисление бонусов обоим
            referral_service = create_referral_service(session)
            referral_result = await referral_service.process_referral(
                invitee=user,
                start_param=source,
            )
            if referral_result.success:
                referral_bonus = referral_result.invitee_bonus
                logger.info(
                    "Реферал обработан для user_id=%d: invitee_bonus=%d",
                    user.id,
                    referral_bonus,
                )

            # Проверяем, нужно ли запросить согласие с документами
            if legal_config.enabled and legal_config.has_documents():
                needs_terms_acceptance = True
            else:
                # Если документы не настроены — начисляем бонус сразу
                billing = create_billing_service(session)
                registration_bonus = await billing.grant_registration_bonus(user)
        else:
            # Обновляем данные профиля (могли измениться)
            await repo.update_profile(
                user=user,
                username=tg_user.username,
                first_name=tg_user.first_name,
                last_name=tg_user.last_name,
            )
            logger.info(
                "Пользователь вернулся: %s (id=%d)",
                tg_user.full_name,
                tg_user.id,
            )

            # Проверяем, нужно ли повторное согласие (версия изменилась)
            if (
                legal_config.enabled
                and legal_config.has_documents()
                and repo.needs_terms_acceptance(user, legal_config.version)
            ):
                needs_terms_acceptance = True
                logger.info(
                    "Требуется повторное согласие: id=%d, "
                    "accepted_version=%s, current_version=%s",
                    tg_user.id,
                    user.accepted_legal_version,
                    legal_config.version,
                )

    # Если нужно согласие — показываем запрос
    if needs_terms_acceptance:
        # Импортируем здесь, чтобы избежать циклических импортов
        from src.bot.handlers.terms import show_terms_acceptance_request

        shown = await show_terms_acceptance_request(message, l10n)
        if shown:
            # Запрос показан — не показываем приветствие,
            # оно будет показано после согласия
            return

    # Отправляем приветственное сообщение с картинкой на языке пользователя
    # Если файл welcome.jpg существует — отправляем фото с подписью,
    # иначе — только текстовое сообщение.
    #
    # ВАЖНО: Используем ReplyKeyboardRemove для очистки reply keyboard.
    # Это гарантирует, что при перезапуске бота не будет висеть устаревшая
    # клавиатура от предыдущей версии бота.
    if WELCOME_IMAGE.exists():
        await message.answer_photo(
            photo=FSInputFile(WELCOME_IMAGE),
            caption=l10n.get("start_message"),
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await message.answer(
            l10n.get("start_message"),
            reply_markup=ReplyKeyboardRemove(),
        )

    # Если начислен бонус — уведомляем пользователя
    if registration_bonus > 0:
        await message.answer(
            l10n.get("billing_registration_bonus", amount=registration_bonus)
        )

    # Если начислен реферальный бонус — уведомляем пользователя
    if referral_bonus > 0:
        await message.answer(l10n.get("referral_invitee_bonus", amount=referral_bonus))
