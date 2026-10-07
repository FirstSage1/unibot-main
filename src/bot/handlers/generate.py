"""Команда /generate для подготовки описания товара."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.states.generate import GenerateStates
from src.db.base import DatabaseSession
from src.db.repositories import UserRepository
from src.providers.ai.base import GenerationType
from src.services.ai_service import AIService
from src.services.generation import ChatGenerationService
from src.services.product_description import build_product_messages
from src.utils.i18n import Localization

router = Router(name="generate")
fsm_router = Router(name="generate_fsm")


@router.message(Command("generate"))
async def cmd_generate(
    message: Message, state: FSMContext, ai_service: AIService
) -> None:
    """Начать ввод данных товара и выбрать доступную GPT-модель."""
    await state.clear()
    models = ai_service.get_available_models()
    model_key = next(
        (
            key
            for key, model in models.items()
            if model.generation_type == GenerationType.CHAT
            and "gpt" in model.model_id.lower()
        ),
        None,
    )
    if model_key is None:
        await message.answer("Нет доступной GPT-модели. Попробуйте позже.")
        return
    await state.update_data(model_key=model_key)
    await state.set_state(GenerateStates.waiting_for_name)
    await message.answer("Введите название товара. Для выхода используйте /start.")


@fsm_router.message(GenerateStates.waiting_for_name, F.text, ~F.text.startswith("/"))
async def handle_product_name(message: Message, state: FSMContext) -> None:
    """Сохранить название и запросить характеристики."""
    name = (message.text or "").strip()
    if not name:
        await message.answer("Введите непустое название товара.")
        return
    await state.update_data(product_name=name)
    await state.set_state(GenerateStates.waiting_for_characteristics)
    await message.answer(
        "Введите характеристики: материал, размеры, цвет, комплектацию, "
        "назначение и другие известные особенности. Неизвестные данные пропустите."
    )


@fsm_router.message(
    GenerateStates.waiting_for_characteristics, F.text, ~F.text.startswith("/")
)
async def handle_product_characteristics(
    message: Message,
    state: FSMContext,
    l10n: Localization,
    ai_service: AIService,
    session_factory: Callable[
        [], AbstractAsyncContextManager[AsyncSession]
    ] = DatabaseSession,
) -> None:
    """Передать данные сервису с существующим учётом расходов."""
    characteristics = (message.text or "").strip()
    if not message.from_user:
        return
    if not characteristics:
        await message.answer("Введите непустые характеристики товара.")
        return
    data = await state.get_data()
    processing = await message.answer("Генерирую описание товара...")
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(message.from_user.id)
        if user is None:
            await processing.edit_text(l10n.get("error_user_not_found"))
            return
        service = ChatGenerationService(session, ai_service=ai_service)
        result = await service.execute(
            telegram_user_id=message.from_user.id,
            model_key=data["model_key"],
            processing_msg=processing,
            l10n=l10n,
            messages=build_product_messages(data["product_name"], characteristics),
            user_id=user.id,
        )
    if result.success:
        await message.answer(result.content, parse_mode=None)
        await processing.delete()
        await state.clear()
