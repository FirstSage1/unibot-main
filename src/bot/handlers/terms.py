"""Обработчик команды /terms и согласия с юридическими документами.

Этот модуль отвечает за:
- Команду /terms — показ ссылок на Политику конфиденциальности и Оферту
- Обработку callback «Принимаю» — сохранение согласия пользователя

Документы хранятся на внешних ресурсах (Google Docs, сайт и т.п.),
а ссылки настраиваются в config.yaml (секция legal).

Логика работы:
1. При /terms показываем сообщение со ссылками на документы
2. При нажатии «Принимаю» сохраняем факт согласия в БД
3. Если документы не настроены — показываем предупреждение
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    FSInputFile,
    InaccessibleMessage,
    Message,
)

from src.bot.keyboards.inline.legal import (
    create_legal_documents_keyboard,
    create_terms_acceptance_keyboard,
)
from src.bot.static import WELCOME_IMAGE
from src.config.yaml_config import yaml_config
from src.db.base import DatabaseSession
from src.db.repositories.user_repo import UserRepository
from src.services.billing_service import create_billing_service
from src.utils.i18n import Localization
from src.utils.logging import get_logger

# Команда /terms для меню бота
COMMAND = BotCommand(command="terms", description="Юридические документы")

router = Router(name="terms")
logger = get_logger(__name__)


@router.message(Command("terms"))
async def cmd_terms(message: Message, l10n: Localization) -> None:
    """Обработать команду /terms.

    Показывает ссылки на юридические документы:
    - Политика конфиденциальности
    - Пользовательское соглашение (оферта)

    Если документы не настроены в config.yaml — показывает предупреждение.

    Args:
        message: Входящее сообщение с командой /terms.
        l10n: Объект локализации.
    """
    legal_config = yaml_config.legal

    # Проверяем, включена ли функция юридических документов
    if not legal_config.enabled:
        await message.answer(l10n.get("legal_disabled"))
        return

    # Проверяем, настроены ли ссылки на документы
    if not legal_config.has_documents():
        await message.answer(l10n.get("legal_not_configured"))
        return

    # Создаём клавиатуру со ссылками на документы
    keyboard = create_legal_documents_keyboard(
        l10n=l10n,
        privacy_policy_url=legal_config.privacy_policy_url,
        terms_of_service_url=legal_config.terms_of_service_url,
    )

    await message.answer(
        l10n.get("legal_documents_message"),
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "legal:accept")
async def callback_accept_terms(callback: CallbackQuery, l10n: Localization) -> None:
    """Обработать нажатие кнопки «Принимаю».

    Сохраняет согласие пользователя с юридическими документами:
    - Записывает дату/время согласия
    - Записывает версию документов

    После сохранения показывает приветственное сообщение и,
    если включён биллинг, начисляет бонус при регистрации.

    Args:
        callback: Callback-запрос от кнопки «Принимаю».
        l10n: Объект локализации.
    """
    if (
        callback.message is None
        or isinstance(callback.message, InaccessibleMessage)
        or callback.from_user is None
    ):
        await callback.answer(l10n.get("error_callback_data"))
        return

    legal_config = yaml_config.legal

    # Сохраняем согласие в БД
    registration_bonus = 0

    async with DatabaseSession() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(callback.from_user.id)

        if user is None:
            await callback.answer(l10n.get("error_user_not_found"), show_alert=True)
            return

        # Проверяем, не принял ли пользователь уже эту версию
        if not repo.needs_terms_acceptance(user, legal_config.version):
            await callback.answer(l10n.get("legal_already_accepted"))
            # Удаляем клавиатуру, оставляем сообщение
            await callback.message.edit_reply_markup(reply_markup=None)
            return

        # Сохраняем согласие
        await repo.accept_terms(user, legal_config.version)

        logger.info(
            "Пользователь принял условия: id=%d, version=%s",
            callback.from_user.id,
            legal_config.version,
        )

        # Начисляем бонус при регистрации (если биллинг включён и бонус ещё не начислен)
        # Проверяем флаг registration_bonus_granted, а не баланс
        # (баланс может быть > 0 из-за реферального бонуса)
        if not user.registration_bonus_granted:
            billing = create_billing_service(session)
            registration_bonus = await billing.grant_registration_bonus(user)

    # Подтверждаем нажатие
    await callback.answer(l10n.get("legal_accepted_notification"))

    # Редактируем сообщение — убираем клавиатуру и меняем текст
    await callback.message.edit_text(
        l10n.get("legal_accepted_message"),
        reply_markup=None,
    )

    # Показываем приветственное сообщение с картинкой
    # Если файл welcome.jpg существует — отправляем фото с подписью,
    # иначе — только текстовое сообщение
    if WELCOME_IMAGE.exists():
        await callback.message.answer_photo(
            photo=FSInputFile(WELCOME_IMAGE),
            caption=l10n.get("start_message"),
        )
    else:
        await callback.message.answer(l10n.get("start_message"))

    # Если начислен бонус — уведомляем пользователя
    if registration_bonus > 0:
        await callback.message.answer(
            l10n.get("billing_registration_bonus", amount=registration_bonus)
        )


async def show_terms_acceptance_request(
    message: Message,
    l10n: Localization,
) -> bool:
    """Показать запрос на согласие с документами.

    Вспомогательная функция для использования в /start и других местах.
    Показывает сообщение с просьбой принять условия использования.

    Args:
        message: Сообщение для ответа.
        l10n: Объект локализации.

    Returns:
        True если запрос показан, False если документы не настроены.
    """
    legal_config = yaml_config.legal

    # Проверяем, настроены ли документы
    if not legal_config.has_documents():
        logger.warning(
            "Юридические документы не настроены, но legal.enabled=true. "
            "Заполните privacy_policy_url и terms_of_service_url в config.yaml"
        )
        return False

    # Создаём клавиатуру для согласия
    keyboard = create_terms_acceptance_keyboard(
        l10n=l10n,
        privacy_policy_url=legal_config.privacy_policy_url,
        terms_of_service_url=legal_config.terms_of_service_url,
    )

    await message.answer(
        l10n.get("legal_acceptance_request"),
        reply_markup=keyboard,
    )

    return True
