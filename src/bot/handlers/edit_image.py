"""Обработчик команды /edit_image — редактирование изображений с помощью AI.

Этот модуль реализует редактирование изображений через AI:
- Отправка изображения для редактирования
- Выбор модели (Gemini, GPT-5 Image)
- Ввод промпта (описание изменений)
- Редактирование изображения и отправка результата
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InaccessibleMessage,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.keyboards import create_model_selection_keyboard
from src.bot.states import EditImageStates
from src.bot.utils.billing import charge_after_delivery, check_billing_and_show_error
from src.core.exceptions import GenerationError
from src.db.base import DatabaseSession
from src.db.exceptions import DatabaseError, UserNotFoundError
from src.db.repositories import UserRepository
from src.services.ai_service import AIService, create_ai_service
from src.services.billing_service import create_billing_service
from src.utils import create_input_file_from_url
from src.utils.i18n import Localization
from src.utils.logging import get_logger

# Команда для меню бота
COMMAND = BotCommand(command="edit_image", description="✏️ Редактирование изображений")

# Два роутера для правильного приоритета:
# - router: команды (высокий приоритет, регистрируется первым)
# - fsm_router: FSM handlers (низкий приоритет, регистрируется после всех команд)
# Это гарантирует что команды работают в любом FSM состоянии.
router = Router(name="edit_image")
fsm_router = Router(name="edit_image_fsm")
logger = get_logger(__name__)

# Константа для типа генерации
GENERATION_TYPE_IMAGE_EDIT = "image_edit"


async def _download_image(bot: Bot, file_id: str) -> bytes | None:
    """Скачать изображение из Telegram по file_id.

    Args:
        bot: Экземпляр бота.
        file_id: ID файла в Telegram.

    Returns:
        Байты изображения или None если скачивание не удалось.
    """
    file = await bot.get_file(file_id)
    if not file.file_path:
        return None

    image_bytes = await bot.download_file(file.file_path)
    if not image_bytes:
        return None

    return image_bytes.read()


async def _handle_edit_error(
    error: Exception,
    processing_msg: Message,
    l10n: Localization,
    model_key: str,
    user_id: int,
) -> None:
    """Обработать ошибку редактирования изображения.

    Args:
        error: Исключение.
        processing_msg: Сообщение для редактирования с ошибкой.
        l10n: Локализация.
        model_key: Ключ модели.
        user_id: ID пользователя в Telegram.
    """
    if isinstance(error, UserNotFoundError):
        await processing_msg.edit_text(l10n.get("error_user_not_found"))
        logger.warning("Незарегистрированный пользователь: %d", error.telegram_id)
    elif isinstance(error, GenerationError):
        await processing_msg.edit_text(l10n.get("generation_error"))
        logger.error("Ошибка AI: model=%s, error=%s", model_key, error.message)
    elif isinstance(error, DatabaseError):
        key = "error_db_temporary" if error.retryable else "error_db_permanent"
        await processing_msg.edit_text(l10n.get(key))
        log_level = 20 if error.retryable else 40
        logger.log(log_level, "Ошибка БД: error=%s", error.message)
    else:
        await processing_msg.edit_text(l10n.get("generation_unexpected_error"))
        logger.error("Неожиданная ошибка: user_id=%d", user_id, exc_info=error)


@router.message(Command(COMMAND))
async def cmd_edit_image(
    message: Message, state: FSMContext, l10n: Localization
) -> None:
    """Обработать команду /edit_image — начать редактирование изображения.

    Просит пользователя отправить изображение для редактирования.

    Args:
        message: Сообщение с командой /edit_image.
        state: FSM контекст для управления состоянием.
        l10n: Объект локализации для переводов.
    """
    if not message.from_user:
        return

    await state.set_state(EditImageStates.waiting_for_image)
    await message.answer(l10n.get("edit_send_image"))
    logger.info(
        "Пользователь %d начал редактирование /edit_image", message.from_user.id
    )


@fsm_router.message(EditImageStates.waiting_for_image, F.photo)
async def handle_image_upload(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    ai_service: AIService | None = None,
) -> None:
    """Обработать загрузку изображения от пользователя.

    Скачивает изображение, сохраняет в FSM state и показывает выбор модели.
    Фильтрует модели по доступным провайдерам (проверяет наличие API-ключей).

    Args:
        message: Сообщение с фотографией.
        state: FSM контекст.
        l10n: Объект локализации для переводов.
        ai_service: AI-сервис для проверки доступных моделей (DI для тестирования).
    """
    if not message.from_user or not message.photo:
        return

    # Берём фото с наибольшим разрешением (последнее в списке)
    photo = message.photo[-1]
    await state.update_data(image_file_id=photo.file_id)

    # Получаем только доступные модели (с настроенными API-ключами)
    if ai_service is None:
        ai_service = create_ai_service()

    available_models = ai_service.get_available_models()

    # Создаём клавиатуру для выбора модели
    keyboard = create_model_selection_keyboard(
        available_models, GENERATION_TYPE_IMAGE_EDIT
    )

    if not keyboard.inline_keyboard:
        await message.answer(l10n.get("no_models_available"))
        await state.clear()
        logger.warning(
            "Нет доступных image_edit-моделей для пользователя %d",
            message.from_user.id,
        )
        return

    await state.set_state(EditImageStates.waiting_for_model_selection)
    await message.answer(l10n.get("edit_choose_model"), reply_markup=keyboard)

    logger.info(
        "Пользователь %d загрузил изображение для редактирования: file_id=%s",
        message.from_user.id,
        photo.file_id,
    )


@fsm_router.message(
    EditImageStates.waiting_for_image,
    ~F.photo,
    ~F.text.startswith("/"),
)
async def handle_invalid_image(message: Message, l10n: Localization) -> None:
    """Обработать сообщение без изображения в состоянии ожидания.

    Команды исключены фильтром ~F.text.startswith("/"),
    поэтому сюда попадают только не-фото, не-команды сообщения.

    Args:
        message: Любое сообщение без фото.
        l10n: Объект локализации.
    """
    await message.answer(l10n.get("edit_please_send_image"))


@fsm_router.callback_query(
    EditImageStates.waiting_for_model_selection,
    F.data.startswith("model:"),
)
async def handle_model_selection(
    callback: CallbackQuery,
    state: FSMContext,
    l10n: Localization,
) -> None:
    """Обработать выбор модели пользователем.

    Args:
        callback: Callback от inline-кнопки с выбором модели.
        state: FSM контекст.
        l10n: Объект локализации для переводов.
    """
    if (
        not callback.data
        or not callback.message
        or isinstance(callback.message, InaccessibleMessage)
    ):
        return

    model_key = callback.data.split(":", 1)[1]
    await state.update_data(model_key=model_key)
    await state.set_state(EditImageStates.waiting_for_prompt)
    await callback.message.edit_text(
        l10n.get("edit_model_selected", model_key=model_key),
    )
    await callback.answer()

    logger.info(
        "Пользователь %d выбрал модель для редактирования: %s",
        callback.from_user.id,
        model_key,
    )


@fsm_router.message(EditImageStates.waiting_for_prompt, F.text, ~F.text.startswith("/"))
async def handle_user_prompt(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    ai_service: AIService | None = None,
    session_factory: Callable[
        [], AbstractAsyncContextManager[AsyncSession]
    ] = DatabaseSession,
) -> None:
    """Обработать промпт от пользователя и отредактировать изображение.

    Команды (сообщения начинающиеся с /) исключены фильтром ~F.text.startswith("/").
    Это гарантирует что /balance, /help и другие команды работают в любой момент.

    Args:
        message: Текстовое сообщение с описанием изменений.
        state: FSM контекст с сохранёнными данными.
        l10n: Объект локализации для переводов.
        ai_service: AI-сервис для генерации (DI для тестирования).
        session_factory: Фабрика для создания сессий БД (DI для тестирования).
    """
    if not message.from_user or not message.text or not message.bot:
        return

    state_data = await state.get_data()
    model_key = state_data.get("model_key")
    image_file_id = state_data.get("image_file_id")

    if not model_key or not image_file_id:
        key = "edit_model_not_selected" if not model_key else "edit_image_not_found"
        await message.answer(l10n.get(key))
        if not image_file_id:
            await state.clear()
        return

    if ai_service is None:
        ai_service = create_ai_service()

    processing_msg = await message.answer(l10n.get("edit_processing"))

    try:
        async with session_factory() as session:
            user_repo = UserRepository(session)
            user = await user_repo.get_by_telegram_id(message.from_user.id)
            if not user:
                raise UserNotFoundError(message.from_user.id)

            # === БИЛЛИНГ: Проверяем возможность генерации ===
            billing = create_billing_service(session)
            cost = await check_billing_and_show_error(
                billing, user, model_key, processing_msg, l10n
            )
            if cost is None:
                return  # Ошибка уже показана пользователю

            # Скачиваем изображение
            image_data = await _download_image(message.bot, image_file_id)
            if not image_data:
                await processing_msg.edit_text(l10n.get("edit_image_download_error"))
                await state.clear()
                return

            logger.debug(
                "Отправляем в AI: user_id=%d, model=%s, image_size=%d",
                user.id,
                model_key,
                len(image_data),
            )

            # Генерируем редактирование через AI-сервис
            result = await ai_service.generate(
                model_key=model_key,
                prompt=message.text,
                image_data=image_data,
            )

            if not result.content or not isinstance(result.content, str):
                await processing_msg.edit_text(l10n.get("edit_empty_response"))
                return

            await processing_msg.delete()
            # Обрабатывает как HTTP URL, так и data URL (base64)
            try:
                await message.answer_photo(
                    photo=create_input_file_from_url(result.content),
                    caption=l10n.get(
                        "edit_completed", model_key=model_key, prompt=message.text[:200]
                    ),
                )
            except Exception:
                # Логируем ошибку отправки изображения с полным traceback
                logger.exception(
                    "Ошибка отправки изображения | user_id=%d | model=%s | "
                    "url_preview=%s",
                    message.from_user.id,
                    model_key,
                    result.content[:100] if result.content else "None",
                )
                await message.answer(l10n.get("imagine_send_error"))
                return

            # === БИЛЛИНГ: Списываем токены ПОСЛЕ успешной отправки ===
            await charge_after_delivery(
                billing, user, model_key, cost, GENERATION_TYPE_IMAGE_EDIT
            )

            logger.info(
                "Изображение отредактировано: user_id=%d, model=%s",
                user.id,
                model_key,
            )

            await state.clear()

    except (UserNotFoundError, GenerationError, DatabaseError) as e:
        await _handle_edit_error(
            e, processing_msg, l10n, model_key, message.from_user.id
        )
    except Exception:
        # Неожиданная ошибка (баг в коде и т.д.)
        await processing_msg.edit_text(l10n.get("generation_unexpected_error"))
        logger.exception("Неожиданная ошибка: user_id=%d", message.from_user.id)
