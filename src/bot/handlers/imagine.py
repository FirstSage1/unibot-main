"""Обработчик команды /imagine — генерация изображений по текстовому описанию.

Этот модуль реализует генерацию изображений через AI:
- Выбор модели (DALL-E, FLUX, и др.)
- Ввод промпта (текстовое описание желаемого изображения)
- Генерация изображения и отправка пользователю
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from aiogram import F, Router
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
from src.bot.states import ImagineStates
from src.db.base import DatabaseSession
from src.providers.ai.base import GenerationType
from src.services.ai_service import AIService, create_ai_service
from src.services.generation import ImageGenerationService
from src.utils import create_input_file_from_url, send_chat_action
from src.utils.i18n import Localization
from src.utils.logging import get_logger

# Команда для меню бота
COMMAND = BotCommand(command="imagine", description="🎨 Генерация изображений")

# Два роутера для правильного приоритета:
# - router: команды (высокий приоритет, регистрируется первым)
# - fsm_router: FSM handlers (низкий приоритет, регистрируется после всех команд)
# Это гарантирует что команды работают в любом FSM состоянии.
router = Router(name="imagine")
fsm_router = Router(name="imagine_fsm")
logger = get_logger(__name__)

# Константы для улучшения читаемости
GENERATION_TYPE_IMAGE = "image"


@router.message(Command(COMMAND))
async def cmd_imagine(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    ai_service: AIService | None = None,
) -> None:
    """Обработать команду /imagine — начать генерацию изображения.

    Показывает пользователю клавиатуру выбора модели для генерации изображений.
    Фильтрует модели по доступным провайдерам (проверяет наличие API-ключей).

    Args:
        message: Сообщение с командой /imagine.
        state: FSM контекст для управления состоянием.
        l10n: Объект локализации для переводов.
        ai_service: AI-сервис для проверки доступных моделей (DI для тестирования).
    """
    if not message.from_user:
        return

    # Получаем только доступные модели (с настроенными API-ключами)
    # Это позволяет работать без API-ключей — просто не будет моделей
    if ai_service is None:
        ai_service = create_ai_service()

    available_models = ai_service.get_available_models()

    # Создаём клавиатуру для выбора модели
    keyboard = create_model_selection_keyboard(available_models, GENERATION_TYPE_IMAGE)

    # Проверяем, есть ли доступные модели данного типа
    if not keyboard.inline_keyboard:
        await message.answer(l10n.get("no_models_available"))
        logger.warning(
            "Нет доступных image-моделей для пользователя %d (проверьте API-ключи)",
            message.from_user.id,
        )
        return

    # Устанавливаем состояние "ожидание выбора модели"
    await state.set_state(ImagineStates.waiting_for_model_selection)

    await message.answer(
        l10n.get("imagine_choose_model"),
        reply_markup=keyboard,
    )

    logger.info("Пользователь %d начал генерацию /imagine", message.from_user.id)


@fsm_router.callback_query(
    ImagineStates.waiting_for_model_selection,
    F.data.startswith("model:"),
)
async def handle_model_selection(
    callback: CallbackQuery,
    state: FSMContext,
    l10n: Localization,
) -> None:
    """Обработать выбор модели пользователем.

    Сохраняет выбранную модель в FSM state и переводит в режим ввода промпта.

    Args:
        callback: Callback от inline-кнопки с выбором модели.
        state: FSM контекст.
        l10n: Объект локализации для переводов.
    """
    # Проверяем наличие необходимых данных
    # Примечание: callback.from_user всегда User (не Optional) в aiogram
    if (
        not callback.data
        or not callback.message
        or isinstance(callback.message, InaccessibleMessage)
    ):
        return

    # Извлекаем model_key из callback_data
    # Формат: "model:dall-e-3" → "dall-e-3"
    model_key = callback.data.split(":", 1)[1]

    # Сохраняем выбранную модель в FSM state
    await state.update_data(model_key=model_key)

    # Переходим в состояние ввода промпта
    await state.set_state(ImagineStates.waiting_for_prompt)

    # Подтверждаем выбор пользователю
    await callback.message.edit_text(
        l10n.get("imagine_model_selected", model_key=model_key),
    )

    # Подтверждаем обработку callback (убирает "часики" на кнопке)
    await callback.answer()

    logger.info(
        "Пользователь %d выбрал модель: %s",
        callback.from_user.id,
        model_key,
    )


@fsm_router.message(ImagineStates.waiting_for_prompt, F.text, ~F.text.startswith("/"))
async def handle_user_prompt(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    ai_service: AIService | None = None,
    session_factory: Callable[
        [], AbstractAsyncContextManager[AsyncSession]
    ] = DatabaseSession,
) -> None:
    """Обработать промпт от пользователя и сгенерировать изображение.

    Использует ImageGenerationService для выполнения генерации.
    Вся общая логика (billing, cooldown, tracking, error handling)
    инкапсулирована в сервисе.

    ВАЖНО: Команды (сообщения начинающиеся с /) исключены фильтром
    ~F.text.startswith("/"). Это гарантирует что /balance, /help и другие
    команды работают в состоянии ввода промпта.

    Алгоритм:
    1. Получаем промпт от пользователя
    2. Вызываем ImageGenerationService.execute()
    3. Отправляем изображение пользователю
    4. Выходим из FSM

    Args:
        message: Текстовое сообщение с описанием изображения.
        state: FSM контекст с сохранённой моделью.
        l10n: Объект локализации для переводов.
        ai_service: AI-сервис для генерации (DI для тестирования).
        session_factory: Фабрика для создания сессий БД (DI для тестирования).
    """
    if not message.from_user or not message.text:
        return

    # Получаем сохранённую модель из FSM state
    state_data = await state.get_data()
    model_key = state_data.get("model_key")

    if not model_key:
        await message.answer(l10n.get("imagine_model_not_selected"))
        return

    # Показываем пользователю, что идёт обработка
    processing_msg = await message.answer(l10n.get("imagine_generating"))

    async with session_factory() as session:
        # Получаем user.id для передачи в сервис
        from src.db.repositories import UserRepository

        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(message.from_user.id)
        if not user:
            await processing_msg.edit_text(l10n.get("error_user_not_found"))
            return

        # === СЕРВИС: Вызываем ImageGenerationService для выполнения генерации ===
        # Вся логика billing, cooldown, tracking, error handling внутри сервиса
        generation_service = ImageGenerationService(session, ai_service=ai_service)

        result = await generation_service.execute(
            telegram_user_id=message.from_user.id,
            model_key=model_key,
            processing_msg=processing_msg,
            l10n=l10n,
            prompt=message.text,
            user_id=user.id,
        )

        # Если генерация не удалась, сервис уже показал ошибку пользователю
        if not result.success:
            return

        # result.content содержит URL изображения
        image_url = result.content

        # Удаляем сообщение "Генерирую изображение..."
        await processing_msg.delete()

        # Отправляем typing indicator перед отправкой изображения
        await send_chat_action(message, GenerationType.IMAGE)

        # Отправляем изображение пользователю
        # create_input_file_from_url обрабатывает как HTTP URL, так и data URL (base64)
        try:
            photo = create_input_file_from_url(image_url)
            await message.answer_photo(
                photo=photo,
                caption=l10n.get(
                    "imagine_generated",
                    model_key=model_key,
                    prompt=message.text[:200],
                ),
            )
        except Exception:
            # Логируем ошибку отправки изображения с полным traceback
            logger.exception(
                "Ошибка отправки изображения | user_id=%d | model=%s | url_preview=%s",
                message.from_user.id,
                model_key,
                image_url[:100] if image_url else "None",
            )
            await message.answer(l10n.get("imagine_send_error"))
            return

        # Выходим из FSM — пользователь должен снова вызвать /imagine
        await state.clear()
