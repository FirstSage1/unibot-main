"""Обработчик команды /clear — очистка истории диалога с AI.

Удаляет все сохранённые сообщения пользователя для текущей модели,
с которой он общается. Это позволяет начать "с чистого листа",
если AI запутался в контексте или нужно поменять тему разговора.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BotCommand, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.states import ChatGPTStates
from src.db.base import DatabaseSession
from src.db.exceptions import DatabaseError, UserNotFoundError
from src.db.repositories import MessageRepository, UserRepository
from src.utils.i18n import Localization
from src.utils.logging import get_logger

# Команда для меню бота
COMMAND = BotCommand(command="clear", description="🗑 Очистить историю диалога")

router = Router(name="clear")
logger = get_logger(__name__)


@router.message(Command(COMMAND))
async def cmd_clear(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    session_factory: Callable[
        [], AbstractAsyncContextManager[AsyncSession]
    ] = DatabaseSession,
) -> None:
    """Очистить историю диалога с AI.

    Удаляет все сохранённые сообщения для текущей модели.
    Если пользователь не в режиме диалога — удаляются все сообщения.

    Args:
        message: Сообщение с командой /clear.
        state: FSM контекст для определения текущей модели.
        l10n: Объект локализации для переводов.
        session_factory: Фабрика для создания сессий БД (DI для тестирования).
    """
    if not message.from_user:
        return

    # Получаем текущее состояние FSM
    current_state = await state.get_state()

    # Если пользователь в режиме диалога — очищаем историю для текущей модели
    # Иначе — очищаем всю историю
    model_key = None
    if current_state == ChatGPTStates.waiting_for_message:
        state_data = await state.get_data()
        model_key = state_data.get("model_key")

    try:
        async with session_factory() as session:
            user_repo = UserRepository(session)
            message_repo = MessageRepository(session)

            # Находим пользователя
            user = await user_repo.get_by_telegram_id(message.from_user.id)
            if not user:
                raise UserNotFoundError(message.from_user.id)

            # Очищаем историю
            deleted_count = await message_repo.clear_context(
                user_id=user.id,
                model_key=model_key,
            )

            if deleted_count == 0:
                await message.answer(l10n.get("clear_history_empty"))
            else:
                # Формируем сообщение в зависимости от контекста
                if model_key:
                    text = l10n.get(
                        "clear_history_model",
                        model_key=model_key,
                        deleted_count=deleted_count,
                    )
                else:
                    text = l10n.get(
                        "clear_history_all",
                        deleted_count=deleted_count,
                    )

                await message.answer(text)

                logger.info(
                    "История очищена: user_id=%d, model=%s, удалено=%d",
                    user.id,
                    model_key or "all",
                    deleted_count,
                )

    except UserNotFoundError as e:
        # Пользователь не зарегистрирован — невосстановимая ошибка
        await message.answer(l10n.get("error_user_not_found"))
        logger.warning(
            "Попытка очистки истории незарегистрированным пользователем: "
            "telegram_id=%d",
            e.telegram_id,
        )

    except DatabaseError as e:
        # Ошибка работы с БД (может быть восстановимой)
        if e.retryable:
            await message.answer(l10n.get("error_db_temporary"))
            logger.warning(
                "Восстановимая ошибка БД при очистке истории: user_id=%d, error=%s",
                message.from_user.id,
                e.message,
            )
        else:
            await message.answer(l10n.get("error_db_permanent"))
            logger.error(
                "Невосстановимая ошибка БД при очистке истории: user_id=%d, error=%s",
                message.from_user.id,
                e.message,
            )

    except Exception:
        # Неожиданная ошибка (баг в коде, и т.д.)
        await message.answer(l10n.get("generation_unexpected_error"))
        logger.exception(
            "Неожиданная ошибка при очистке истории: user_id=%d, model=%s",
            message.from_user.id,
            model_key or "all",
        )
