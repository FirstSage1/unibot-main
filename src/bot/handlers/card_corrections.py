"""Текстовые правки последней фотографии /card и ответы на старые фото."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from secrets import token_hex

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers.card_runtime import (
    completion_text,
    download_photo,
    error_key,
    finish_generation,
    isolation,
    owns_session,
    report_error,
)
from src.bot.states.card import CardStates
from src.core.exceptions import AIServiceError, DatabaseError, GenerationError
from src.db.base import DatabaseSession
from src.services.card_prompts import build_correction_prompt
from src.services.product_card import CardError, ProductCardService, create_card_service
from src.utils import create_input_file_from_url
from src.utils.i18n import Localization

router = Router(name="card_corrections")


@router.message(CardStates.waiting_for_correction, F.text, ~F.text.startswith("/"))
@router.message(
    StateFilter(None), F.reply_to_message.photo, F.text, ~F.text.startswith("/")
)
async def handle_correction(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    bot: Bot,
    card_service: ProductCardService | None = None,
    session_factory: Callable[
        [], AbstractAsyncContextManager[AsyncSession]
    ] = DatabaseSession,
) -> None:
    """Применить правку к последнему результату; при ошибке оставить его доступным."""
    if not message.from_user or not message.text:
        return
    async with isolation.lock(state.key):
        if not await prepare_correction(message, state, l10n, bot):
            return
        data = await state.get_data()
        processing: Message | None = None
        try:
            prompt = build_correction_prompt(message.text)
        except ValueError:
            await message.answer(l10n.get("card_correction_length"))
            return
        session_id = str(data.get("session_id", ""))
        file_id = data.get("result_file_id")
        if not isinstance(file_id, str) or not file_id:
            await message.answer(l10n.get("card_correction_missing"))
            return
        service = card_service if card_service is not None else create_card_service()
        await state.update_data(last_correction_id=message.message_id)
        await state.set_state(CardStates.generating)
        try:
            await message.answer(
                l10n.get("card_prompt_heading") + "\n\n" + prompt, parse_mode=None
            )
            processing = await message.answer(l10n.get("card_generating"))
            image = await download_photo(bot, file_id)

            async def deliver(content: str, full_prompt: str) -> None:
                """Запомнить идентификатор доставленного Telegram изображения."""
                sent = await message.answer_photo(
                    photo=create_input_file_from_url(content),
                    caption=completion_text(service, l10n),
                    parse_mode=None,
                )
                if sent.photo and await owns_session(state, session_id):
                    await state.update_data(result_file_id=sent.photo[-1].file_id)

            async with session_factory() as session:
                await service.generate(
                    session, message.from_user.id, image, prompt, deliver
                )
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
            await report_error(message, l10n, error_key(error))
        finally:
            if processing is not None:
                with suppress(Exception):
                    await processing.delete()
            await finish_generation(state, session_id)


@router.message(CardStates.generating, F.text, ~F.text.startswith("/"))
async def handle_busy(message: Message, l10n: Localization) -> None:
    """Не оставлять без ответа текст во время генерации."""
    await message.answer(l10n.get("card_busy"))


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def handle_missing_context(message: Message, l10n: Localization) -> None:
    """Помочь продолжить старую карточку, контекст которой уже был очищен."""
    await message.answer(l10n.get("card_correction_missing"))


async def prepare_correction(
    message: Message, state: FSMContext, l10n: Localization, bot: Bot
) -> bool:
    """Восстановить старое фото по ответу и отсеять повторную доставку текста."""
    current = await state.get_state()
    if current not in {None, CardStates.waiting_for_correction.state}:
        await message.answer(l10n.get("card_busy"))
        return False
    data = await state.get_data()
    if data.get("last_correction_id") == message.message_id:
        return False
    reply = message.reply_to_message
    if reply and reply.photo and reply.from_user and reply.from_user.id == bot.id:
        # Ответ на конкретное фото должен редактировать именно его, даже если
        # в FSM уже хранится более новый результат другой карточки.
        await state.update_data(
            session_id=data.get("session_id") or token_hex(6),
            result_file_id=reply.photo[-1].file_id,
        )
        await state.set_state(CardStates.waiting_for_correction)
    elif current is None:
        await message.answer(l10n.get("card_correction_missing"))
        return False
    return True
