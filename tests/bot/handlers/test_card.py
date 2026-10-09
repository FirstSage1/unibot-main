"""Проверки выбора идеи, устаревших кнопок и полного промпта."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from src.bot.handlers.card import cmd_card, handle_idea_selection, handle_product_photo
from src.bot.states.card import CardStates
from src.services.product_card import ProductCardService
from tests.services.test_card_prompts import make_ideas


async def test_full_flow_and_duplicate_click() -> None:
    """Две одновременные кнопки дают только одну генерацию и полный промпт."""
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=2, user_id=3))
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(id=3)
    message.photo = [MagicMock(file_id="reference")]
    menu = MagicMock(spec=Message)
    menu.message_id = 42
    menu.delete = AsyncMock()
    message.answer = AsyncMock(return_value=menu)
    menu.answer = AsyncMock(return_value=menu)
    menu.answer_photo = AsyncMock()
    menu.edit_reply_markup = AsyncMock()
    l10n = MagicMock()
    l10n.get.side_effect = lambda key, **_: key
    bot = MagicMock()
    bot.download = AsyncMock(side_effect=lambda *_: BytesIO(b"photo"))
    service = MagicMock(spec=ProductCardService)
    service.config = MagicMock()
    service.suggest = AsyncMock(return_value=make_ideas())
    service.prompt.return_value = "п" * 1900

    async def generate(
        session: object,
        user_id: int,
        image: bytes,
        prompt: str,
        deliver: Callable[[str, str], Awaitable[None]],
    ) -> None:
        menu.answer.assert_any_await(
            "card_prompt_heading\n\n" + "п" * 1900, parse_mode=None
        )
        await deliver("https://example.com/photo.png", prompt)

    service.generate = AsyncMock(side_effect=generate)

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    await cmd_card(message, state, l10n)
    await handle_product_photo(message, state, l10n, bot, service)
    assert await state.get_state() == CardStates.waiting_for_idea.state
    keyboard = message.answer.call_args.kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 3
    callback = MagicMock(message=menu)
    callback.from_user.id = 3
    callback.data = keyboard.inline_keyboard[1][0].callback_data
    callback.answer = AsyncMock()
    await asyncio.gather(
        *[
            handle_idea_selection(callback, state, l10n, bot, service, session_factory)
            for _ in range(2)
        ]
    )
    service.generate.assert_awaited_once()
    menu.answer_photo.assert_awaited_once()
    menu.answer.assert_any_await(
        "card_prompt_heading\n\n" + "п" * 1900, parse_mode=None
    )
    assert await state.get_state() is None


async def test_old_callback_does_not_clear_new_session() -> None:
    """Старая кнопка не меняет новое фото или выбранный сценарий."""
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=5, user_id=6))
    await state.set_state(CardStates.waiting_for_idea)
    await state.set_data({"session_id": "new", "menu_id": 99})
    callback = MagicMock()
    callback.data = "card:old:0"
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    service = MagicMock(spec=ProductCardService)
    await handle_idea_selection(callback, state, MagicMock(), MagicMock(), service)
    service.generate.assert_not_called()
    assert (await state.get_data())["session_id"] == "new"
