"""Обработчик команды /chatgpt — диалог с AI-моделями.

Этот модуль реализует полноценный диалог с AI:
- Выбор модели (GPT-4o, Claude, и др.)
- Многошаговый диалог с сохранением контекста
- Отправка истории предыдущих сообщений в AI
- Обработка ошибок AI с понятными сообщениями пользователю
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, CallbackQuery, InaccessibleMessage, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.keyboards import create_model_selection_keyboard
from src.bot.states import ChatGPTStates
from src.config.yaml_config import yaml_config
from src.db.base import DatabaseSession
from src.db.repositories import MessageRepository
from src.providers.ai.base import GenerationType
from src.services.ai_service import AIService, create_ai_service
from src.services.generation import ChatGenerationService
from src.utils import send_chat_action, send_long_message
from src.utils.i18n import Localization
from src.utils.logging import get_logger

# Команда для меню бота
COMMAND = BotCommand(command="chatgpt", description="💬 Диалог с AI")

# Два роутера для правильного приоритета:
# - router: команды (высокий приоритет, регистрируется первым)
# - fsm_router: FSM handlers (низкий приоритет, регистрируется после всех команд)
# Это гарантирует что команды работают в любом FSM состоянии.
router = Router(name="chatgpt")
fsm_router = Router(name="chatgpt_fsm")
logger = get_logger(__name__)

# Константы для улучшения читаемости
GENERATION_TYPE_CHAT = "chat"
# Роли сообщений для OpenAI/Anthropic
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


async def _send_ai_response(message: Message, content: str) -> None:
    """Отправить ответ AI пользователю с typing indicator и разбиением на части."""
    await send_chat_action(message, GenerationType.CHAT)
    await send_long_message(message, content)


@router.message(Command(COMMAND))
async def cmd_chatgpt(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    ai_service: AIService | None = None,
) -> None:
    """Обработать команду /chatgpt — начать диалог с AI.

    Показывает пользователю клавиатуру выбора модели для генерации текста.
    Фильтрует модели по доступным провайдерам (проверяет наличие API-ключей).

    Args:
        message: Сообщение с командой /chatgpt.
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
    keyboard = create_model_selection_keyboard(available_models, GENERATION_TYPE_CHAT)

    # Проверяем, есть ли доступные модели данного типа
    if not keyboard.inline_keyboard:
        await message.answer(l10n.get("no_models_available"))
        logger.warning(
            "Нет доступных chat-моделей для пользователя %d (проверьте API-ключи)",
            message.from_user.id,
        )
        return

    # Устанавливаем состояние "ожидание выбора модели"
    await state.set_state(ChatGPTStates.waiting_for_model_selection)

    await message.answer(
        l10n.get("chatgpt_choose_model"),
        reply_markup=keyboard,
    )

    logger.info("Пользователь %d начал диалог /chatgpt", message.from_user.id)


@fsm_router.callback_query(
    ChatGPTStates.waiting_for_model_selection,
    F.data.startswith("model:"),
)
async def handle_model_selection(
    callback: CallbackQuery,
    state: FSMContext,
    l10n: Localization,
) -> None:
    """Обработать выбор модели пользователем.

    Сохраняет выбранную модель в FSM state и переводит в режим диалога.

    Args:
        callback: Callback от inline-кнопки с выбором модели.
        state: FSM контекст.
        l10n: Объект локализации для переводов.
    """
    # Проверяем наличие необходимых данных
    if (
        not callback.data
        or not callback.message
        or isinstance(callback.message, InaccessibleMessage)
    ):
        return

    # Извлекаем model_key из callback_data
    # Формат: "model:gpt-4o" → "gpt-4o"
    model_key = callback.data.split(":", 1)[1]

    # Сохраняем выбранную модель в FSM state
    await state.update_data(model_key=model_key)

    # Переходим в состояние диалога
    await state.set_state(ChatGPTStates.waiting_for_message)

    # Подтверждаем выбор пользователю
    await callback.message.edit_text(
        l10n.get("chatgpt_model_selected", model_key=model_key),
    )

    # Подтверждаем обработку callback (убирает "часики" на кнопке)
    await callback.answer()

    logger.info(
        "Пользователь %d выбрал модель: %s",
        callback.from_user.id,
        model_key,
    )


@fsm_router.message(ChatGPTStates.waiting_for_message, F.text, ~F.text.startswith("/"))
async def handle_user_message(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    ai_service: AIService | None = None,
    session_factory: Callable[
        [], AbstractAsyncContextManager[AsyncSession]
    ] = DatabaseSession,
) -> None:
    """Обработать сообщение от пользователя и сгенерировать ответ AI.

    Использует ChatGenerationService для выполнения генерации.
    Вся общая логика (billing, cooldown, tracking, error handling)
    инкапсулирована в сервисе.

    ВАЖНО: Команды (сообщения начинающиеся с /) исключены фильтром
    ~F.text.startswith("/"). Это гарантирует что /balance, /help и другие
    команды работают в состоянии диалога с AI.

    Алгоритм:
    1. Загружаем контекст диалога из БД
    2. Добавляем новое сообщение пользователя в БД
    3. Формируем контекст для AI (история + новое сообщение)
    4. Вызываем ChatGenerationService.execute()
    5. Отправляем результат пользователю

    Args:
        message: Текстовое сообщение от пользователя.
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
        await message.answer(l10n.get("chatgpt_model_not_selected"))
        return

    # Показываем пользователю, что идёт обработка
    processing_msg = await message.answer(l10n.get("chatgpt_generating"))

    async with session_factory() as session:
        message_repo = MessageRepository(session)

        # Загружаем контекст диалога (предыдущие сообщения)
        from src.db.repositories import UserRepository

        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(message.from_user.id)
        if not user:
            await processing_msg.edit_text(l10n.get("error_user_not_found"))
            return

        context_messages = await message_repo.get_context(
            user_id=user.id,
            model_key=model_key,
            max_messages=yaml_config.limits.max_context_messages,
        )

        # Добавляем новое сообщение пользователя в БД
        await message_repo.add_message(
            user_id=user.id,
            model_key=model_key,
            role=ROLE_USER,
            content=message.text,
        )

        # Формируем контекст для AI (история + новое сообщение)
        # Формат OpenAI API: [{"role": "user", "content": "..."}, ...]
        messages_for_ai = []

        # Добавляем system prompt если он настроен в конфиге модели
        model_config = yaml_config.get_model(model_key)
        if model_config and model_config.system_prompt:
            messages_for_ai.append(
                {"role": "system", "content": model_config.system_prompt}
            )

        # Добавляем историю диалога
        messages_for_ai.extend(
            [{"role": msg.role, "content": msg.content} for msg in context_messages]
        )

        # Добавляем текущее сообщение пользователя
        messages_for_ai.append({"role": ROLE_USER, "content": message.text})

        generation_service = ChatGenerationService(session, ai_service=ai_service)

        result = await generation_service.execute(
            telegram_user_id=message.from_user.id,
            model_key=model_key,
            processing_msg=processing_msg,
            l10n=l10n,
            messages=messages_for_ai,
            user_id=user.id,
        )

        # Если генерация не удалась, сервис уже показал ошибку пользователю
        if not result.success:
            return

        # Удаляем сообщение "Генерирую ответ..." и отправляем результат
        await processing_msg.delete()
        await _send_ai_response(message, result.content)
