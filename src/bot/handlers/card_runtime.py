"""Общие операции Telegram для генерации и коррекции карточек."""

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import Message

from src.bot.states.card import CardStates
from src.core.exceptions import ImageNoOutputError, ModelTemporarilyUnavailableError
from src.services.product_card import CardError, ProductCardService
from src.utils.i18n import Localization
from src.utils.logging import get_logger

logger = get_logger(__name__)
isolation = SimpleEventIsolation()


async def download_photo(bot: Bot, file_id: str) -> bytes:
    """Скачать JPEG, который Telegram подготовил из отправленного фото."""
    async with asyncio.timeout(30):
        image = await bot.download(file_id)
    if image is None:
        raise CardError("card_photo_download_error")
    return image.read()


def completion_text(service: ProductCardService, l10n: Localization) -> str:
    """Показать стоимость следующей правки до её отправки пользователем."""
    model = service.config.models[service.config.card.image_model]
    price = model.price_tokens if service.config.billing.enabled else 0
    return l10n.get("card_completed", price=price)


async def owns_session(state: FSMContext, session_id: str) -> bool:
    """Проверить, что другая команда не сменила текущий диалог."""
    current = await state.get_state()
    return bool(
        current
        and current.startswith("CardStates:")
        and (await state.get_data()).get("session_id") == session_id
    )


async def finish_generation(state: FSMContext, session_id: str) -> None:
    """Сохранить последнее доставленное фото для следующих правок."""
    if await owns_session(state, session_id):
        if await state.get_state() == CardStates.waiting_for_idea.state:
            return
        if (await state.get_data()).get("result_file_id"):
            await state.set_state(CardStates.waiting_for_correction)
        else:
            await state.clear()


async def clear_session(state: FSMContext, session_id: str) -> None:
    """Не стереть диалог, который пользователь начал другой командой."""
    if (await state.get_data()).get("session_id") == session_id:
        current = await state.get_state()
        if current and current.startswith("CardStates:"):
            await state.clear()


def error_key(error: Exception) -> str:
    """Не выводить пользователю или в журнал сырые ответы провайдера."""
    logger.warning("Сбой /card: %s", type(error).__name__)
    if isinstance(error, ImageNoOutputError):
        return "card_image_no_output"
    if isinstance(error, ModelTemporarilyUnavailableError):
        return "card_model_unavailable"
    return str(error) if isinstance(error, CardError) else "card_failed"


async def report_error(message: Message, l10n: Localization, key: str) -> None:
    """Сообщить о сбое даже при удалённом сообщении прогресса."""
    try:
        await message.answer(l10n.get(key))
    except (OSError, RuntimeError, TelegramAPIError):
        logger.warning("Не удалось доставить уведомление /card", exc_info=False)
