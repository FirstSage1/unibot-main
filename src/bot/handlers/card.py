"""Обработчик команды /card для создания рекламных фото товара."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.states.card import CardStates
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

COMMAND = BotCommand(command="card", description="🛍 Создать фото товара")
GENERATION_TYPE_IMAGE_EDIT = "image_edit"
IDEAS = {
    "card:everyday": "everyday",
    "card:outdoor": "outdoor",
    "card:gifting": "gifting",
}

router = Router(name="card")
fsm_router = Router(name="card_fsm")
logger = get_logger(__name__)


@router.message(Command(COMMAND))
async def cmd_card(message: Message, state: FSMContext, l10n: Localization) -> None:
    """Запросить у пользователя фотографию товара."""
    if not message.from_user:
        return

    await state.set_state(CardStates.waiting_for_product_photo)
    await message.answer(l10n.get("card_send_photo"))


@fsm_router.message(CardStates.waiting_for_product_photo, F.photo)
async def handle_product_photo(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    ai_service: AIService | None = None,
) -> None:
    """Сохранить фото товара и предложить три рекламных сюжета."""
    if not message.from_user or not message.photo:
        return

    service = ai_service or create_ai_service()
    models = service.get_available_models()
    if not any(
        model.generation_type == GENERATION_TYPE_IMAGE_EDIT for model in models.values()
    ):
        await message.answer(l10n.get("card_no_models_available"))
        await state.clear()
        return

    await state.update_data(image_file_id=message.photo[-1].file_id)
    await state.set_state(CardStates.waiting_for_idea)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=l10n.get(f"card_idea_{idea}"), callback_data=callback_data
                )
            ]
            for callback_data, idea in IDEAS.items()
        ]
    )
    await message.answer(l10n.get("card_choose_idea"), reply_markup=keyboard)


@fsm_router.message(
    CardStates.waiting_for_product_photo,
    ~F.photo,
    ~F.text.startswith("/"),
)
async def handle_invalid_photo(message: Message, l10n: Localization) -> None:
    """Попросить прислать фото вместо другого типа сообщения."""
    await message.answer(l10n.get("card_please_send_photo"))


@fsm_router.callback_query(CardStates.waiting_for_idea, F.data.in_(IDEAS))
async def handle_idea_selection(
    callback: CallbackQuery,
    state: FSMContext,
    l10n: Localization,
    ai_service: AIService | None = None,
    session_factory: Callable[
        [], AbstractAsyncContextManager[AsyncSession]
    ] = DatabaseSession,
) -> None:
    """Сгенерировать рекламный визуал для выбранного сценария."""
    if not callback.message or isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return

    idea_key = IDEAS.get(callback.data or "")
    state_data = await state.get_data()
    image_file_id = state_data.get("image_file_id")
    if not idea_key or not image_file_id:
        await callback.answer(l10n.get("card_session_expired"), show_alert=True)
        await state.clear()
        return

    service = ai_service or create_ai_service()
    model_key = next(
        (
            key
            for key, model in service.get_available_models().items()
            if model.generation_type == GENERATION_TYPE_IMAGE_EDIT
        ),
        None,
    )
    if not model_key:
        await callback.message.answer(l10n.get("card_no_models_available"))
        await state.clear()
        await callback.answer()
        return

    await callback.answer()
    processing_msg = await callback.message.answer(l10n.get("card_generating"))
    try:
        async with session_factory() as session:
            user = await UserRepository(session).get_by_telegram_id(
                callback.from_user.id
            )
            if not user:
                raise UserNotFoundError(callback.from_user.id)

            billing = create_billing_service(session)
            cost = await check_billing_and_show_error(
                billing, user, model_key, processing_msg, l10n
            )
            if cost is None:
                await state.clear()
                return

            image_data = await _download_image(callback.bot, image_file_id)
            if not image_data:
                await processing_msg.edit_text(l10n.get("card_photo_download_error"))
                await state.clear()
                return

            prompt = l10n.get(f"card_prompt_{idea_key}")
            result = await service.generate(
                model_key=model_key,
                prompt=prompt,
                image_data=image_data,
            )
            if not result.content or not isinstance(result.content, str):
                await processing_msg.edit_text(l10n.get("card_empty_response"))
                await state.clear()
                return

            await callback.message.answer_photo(
                photo=create_input_file_from_url(result.content),
                caption=l10n.get(
                    "card_completed",
                    idea=l10n.get(f"card_idea_{idea_key}"),
                    prompt=prompt[:700],
                ),
            )
            await processing_msg.delete()
            await charge_after_delivery(
                billing, user, model_key, cost, GENERATION_TYPE_IMAGE_EDIT
            )
            await state.clear()
    except (UserNotFoundError, GenerationError, DatabaseError):
        logger.exception(
            "Ошибка создания карточки товара | user_id=%d", callback.from_user.id
        )
        await processing_msg.edit_text(l10n.get("generation_unexpected_error"))
        await state.clear()
    except Exception:
        logger.exception(
            "Неожиданная ошибка создания карточки | user_id=%d", callback.from_user.id
        )
        await processing_msg.edit_text(l10n.get("generation_unexpected_error"))
        await state.clear()


async def _download_image(bot: Bot, file_id: str) -> bytes | None:
    """Загрузить исходное изображение товара из Telegram."""
    file = await bot.get_file(file_id)
    if not file.file_path:
        return None
    image = await bot.download_file(file.file_path)
    return image.read() if image else None
