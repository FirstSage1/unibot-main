"""Диалог /card: фото, три идеи использования, изображение с промптом."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from secrets import token_hex

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers.card_corrections import router as corrections_router
from src.bot.handlers.card_runtime import (
    clear_session,
    completion_text,
    download_photo,
    error_key,
    finish_generation,
    isolation,
    owns_session,
    report_error,
)
from src.bot.states.card import CardStates
from src.core.exceptions import (
    AIServiceError,
    DatabaseError,
    GenerationError,
)
from src.db.base import DatabaseSession
from src.services.card_prompts import IDEA_COUNT, CardIdeas
from src.services.product_card import CardError, ProductCardService, create_card_service
from src.utils import create_input_file_from_url
from src.utils.i18n import Localization
from src.utils.logging import get_logger

router = Router(name="card")
fsm_router = Router(name="card_fsm")
fsm_router.include_router(corrections_router)
logger = get_logger(__name__)
# Повторные callback одного пользователя не должны запускать платные запросы.
router.shutdown.register(isolation.close)


@router.message(Command("card", ignore_case=True))
async def cmd_card(message: Message, state: FSMContext, l10n: Localization) -> None:
    """Начать новый сценарий и сделать старые кнопки недействительными."""
    if not message.from_user:
        return
    current = await state.get_state()
    if current in {CardStates.analyzing.state, CardStates.generating.state}:
        await message.answer(l10n.get("card_busy"))
        return
    await state.clear()
    await state.set_state(CardStates.waiting_for_product_photo)
    await message.answer(l10n.get("card_send_photo"))


@fsm_router.message(CardStates.waiting_for_product_photo, F.photo)
async def handle_product_photo(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    bot: Bot,
    card_service: ProductCardService | None = None,
) -> None:
    """Показать идеи, основанные на анализе фотографии пользователя."""
    if not message.from_user or not message.photo:
        return
    async with isolation.lock(state.key):
        if await state.get_state() != CardStates.waiting_for_product_photo.state:
            return
        service = card_service if card_service is not None else create_card_service()
        session_id = token_hex(6)
        file_id = message.photo[-1].file_id
        await state.set_data({"session_id": session_id, "image_file_id": file_id})
        await state.set_state(CardStates.analyzing)
        try:
            processing = await message.answer(l10n.get("card_analyzing"))
            ideas = await service.suggest(await download_photo(bot, file_id))
            if (await state.get_data()).get("session_id") != session_id:
                return
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{index + 1}. {idea.title}",
                            callback_data=f"card:{session_id}:{index}",
                        )
                    ]
                    for index, idea in enumerate(ideas.ideas)
                ]
            )
            description = "\n\n".join(
                f"{index + 1}. {idea.title}\n{idea.scene}"
                for index, idea in enumerate(ideas.ideas)
            )
            model = service.config.models[service.config.card.image_model]
            price = model.price_tokens if service.config.billing.enabled else 0
            menu = await message.answer(
                l10n.get("card_choose_idea", price=price) + "\n\n" + description,
                reply_markup=keyboard,
                parse_mode=None,
            )
            await state.update_data(ideas=ideas.model_dump(), menu_id=menu.message_id)
            await state.set_state(CardStates.waiting_for_idea)
            with suppress(Exception):
                await processing.delete()
        except asyncio.CancelledError:
            await report_error(message, l10n, "card_interrupted")
            await clear_session(state, session_id)
            raise
        except (
            CardError,
            OSError,
            ValueError,
            TelegramAPIError,
            AIServiceError,
            GenerationError,
            DatabaseError,
            SQLAlchemyError,
        ) as error:
            logger.warning("Анализ /card: %s", type(error).__name__, exc_info=False)
            await report_error(message, l10n, error_key(error))
            await clear_session(state, session_id)


@fsm_router.message(
    CardStates.waiting_for_product_photo, ~F.photo, ~F.text.startswith("/")
)
async def handle_invalid_photo(message: Message, l10n: Localization) -> None:
    """Объяснить, что для анализа нужен именно снимок товара."""
    await message.answer(l10n.get("card_please_send_photo"))


@router.callback_query(F.data.startswith("card:"))
async def handle_idea_selection(
    callback: CallbackQuery,
    state: FSMContext,
    l10n: Localization,
    bot: Bot,
    card_service: ProductCardService | None = None,
    session_factory: Callable[
        [], AbstractAsyncContextManager[AsyncSession]
    ] = DatabaseSession,
) -> None:
    """Проверить кнопку и доставить результат до списания оплаты."""
    await callback.answer()
    if not callback.message or isinstance(callback.message, InaccessibleMessage):
        return
    message = callback.message
    async with isolation.lock(state.key):
        data = await state.get_data()
        parts = (callback.data or "").split(":")
        if (
            len(parts) != 3
            or parts[1] != data.get("session_id")
            or not parts[2].isdigit()
            or not 0 <= int(parts[2]) < IDEA_COUNT
            or data.get("menu_id") != message.message_id
            or await state.get_state() != CardStates.waiting_for_idea.state
        ):
            await message.answer(l10n.get("card_session_expired"))
            return
        session_id = parts[1]
        service = card_service if card_service is not None else create_card_service()
        await state.set_state(CardStates.generating)
        try:
            ideas = CardIdeas.model_validate(data["ideas"])
            prompt = service.prompt(ideas, int(parts[2]))
            await message.edit_reply_markup(reply_markup=None)
            await message.answer(
                l10n.get("card_prompt_heading") + "\n\n" + prompt,
                parse_mode=None,
            )
            processing = await message.answer(l10n.get("card_generating"))
            image = await download_photo(bot, str(data["image_file_id"]))

            async def deliver(content: str, full_prompt: str) -> None:
                """Отправить фото после уже показанного полного промпта."""
                sent = await message.answer_photo(
                    photo=create_input_file_from_url(content),
                    caption=completion_text(service, l10n),
                    parse_mode=None,
                )
                if sent.photo and await owns_session(state, session_id):
                    await state.update_data(result_file_id=sent.photo[-1].file_id)

            async with session_factory() as session:
                await service.generate(
                    session, callback.from_user.id, image, prompt, deliver
                )
            with suppress(Exception):
                await processing.delete()
        except asyncio.CancelledError:
            await report_error(message, l10n, "card_interrupted")
            raise
        except (
            CardError,
            OSError,
            ValueError,
            TelegramAPIError,
            AIServiceError,
            GenerationError,
            DatabaseError,
            SQLAlchemyError,
        ) as error:
            logger.warning("Генерация /card: %s", type(error).__name__, exc_info=False)
            await report_error(message, l10n, error_key(error))
        finally:
            await finish_generation(state, session_id)
