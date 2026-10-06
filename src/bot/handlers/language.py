"""Обработчик команды /language для смены языка интерфейса.

Позволяет пользователю выбрать язык интерфейса бота из списка доступных языков.
Выбранный язык сохраняется в БД (User.language) и используется при всех взаимодействиях.

Доступные языки настраиваются в config.yaml (localization.available_languages).
Если мультиязычность отключена (localization.enabled=false) — команда недоступна.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError

from src.bot.keyboards import create_language_keyboard
from src.db.base import DatabaseSession
from src.db.repositories.user_repo import UserRepository
from src.utils.i18n import Localization, create_localization
from src.utils.logging import get_logger

router = Router(name="language")
logger = get_logger(__name__)


@router.message(Command("language"))
async def cmd_language(message: Message, l10n: Localization) -> None:
    """Обработать команду /language.

    Показывает inline-клавиатуру с доступными языками для выбора.

    Args:
        message: Входящее сообщение с командой /language.
        l10n: Объект локализации (внедряется через LanguageMiddleware).
    """
    # Если мультиязычность отключена — игнорируем команду
    # Примечание: этот роутер не регистрируется если enabled=false,
    # но проверка оставлена для безопасности
    if not Localization.is_enabled():
        logger.debug(
            "Команда /language проигнорирована: "
            "мультиязычность отключена (localization.enabled=false)"
        )
        return

    # Создаём клавиатуру с языками
    keyboard = create_language_keyboard(l10n)

    # Отправляем сообщение с клавиатурой
    await message.answer(
        l10n.get("language_command"),
        reply_markup=keyboard,
    )


@router.callback_query(lambda c: c.data and c.data.startswith("lang:"))
async def process_language_selection(
    callback: CallbackQuery, l10n: Localization
) -> None:
    """Обработать выбор языка через callback.

    Callback data формат: "lang:<language_code>"
    Например: "lang:ru", "lang:en"

    Args:
        callback: Callback от inline-кнопки.
        l10n: Объект локализации (внедряется через LanguageMiddleware).
    """
    if not callback.data:
        await callback.answer(l10n.get("error_callback_data"))
        return

    # Извлекаем код языка из callback_data
    # Формат: "lang:ru" → "ru"
    selected_language = callback.data.split(":", 1)[1]

    # Проверяем, что выбранный язык доступен
    available_languages = Localization.get_available_languages()
    if selected_language not in available_languages:
        logger.warning(
            "Попытка выбрать недоступный язык: %s (доступны: %s)",
            selected_language,
            available_languages,
        )
        await callback.answer(l10n.get("error_language_not_supported"))
        return

    # Получаем пользователя из callback
    # Примечание: callback.from_user всегда User (не Optional) в aiogram
    tg_user = callback.from_user

    # Обновляем язык пользователя в БД
    try:
        async with DatabaseSession() as session:
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(tg_user.id)

            if user is None:
                # Пользователь не найден (не должно происходить)
                logger.error(
                    "Пользователь не найден в БД: telegram_id=%d",
                    tg_user.id,
                )
                await callback.answer(l10n.get("error_unknown"))
                return

            # Сохраняем старый язык для логирования
            old_language = user.language

            # Обновляем язык
            await repo.update_language(user, selected_language)

        logger.info(
            "Язык изменён: user_id=%d, telegram_id=%d, old=%s, new=%s",
            user.id,
            tg_user.id,
            old_language,
            selected_language,
        )

        # Создаём объект локализации с НОВЫМ языком для ответа
        new_l10n = create_localization(selected_language)

        # Получаем название выбранного языка
        language_name = new_l10n.get(f"language_name_{selected_language}")

        # Отправляем подтверждение на НОВОМ языке
        # callback.message может быть Message или InaccessibleMessage
        # edit_text доступен только для Message
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                new_l10n.get("language_changed", language_name=language_name),
            )

        # Отвечаем на callback (убираем "часики" на кнопке)
        await callback.answer()

    except SQLAlchemyError:
        logger.exception(
            "Ошибка БД при обновлении языка для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_unknown"))

    except OSError:
        logger.exception(
            "Ошибка подключения к БД при обновлении языка для пользователя %d",
            tg_user.id,
        )
        await callback.answer(l10n.get("error_unknown"))
