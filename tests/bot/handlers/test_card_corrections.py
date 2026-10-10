"""Регрессии последовательных правок, повторов и восстановления старого фото."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.bot.handlers.card_corrections import handle_correction
from src.bot.states.card import CardStates
from src.services.product_card import CardError, ProductCardService


@pytest.fixture
def state() -> FSMContext:
    """Изолированное хранилище диалога, без локальной БД."""
    return FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=5, user_id=6))


@pytest.fixture
def message() -> MagicMock:
    """Текстовая правка с ответами Telegram в виде моков."""
    message = MagicMock()
    message.from_user.id = 6
    message.message_id = 100
    message.text = "поменяй вид со спины на анфас"
    message.answer = AsyncMock()
    message.answer_photo = AsyncMock(
        return_value=MagicMock(
            photo=[MagicMock(file_id="edited")],
        )
    )
    message.answer.return_value.delete = AsyncMock()
    return message


@pytest.fixture
def service() -> MagicMock:
    """AI-доставка без платных запросов."""
    service = MagicMock(spec=ProductCardService)
    service.config = MagicMock()

    async def generate(
        session: object,
        user_id: int,
        image: bytes,
        prompt: str,
        deliver: Callable[[str, str], Awaitable[None]],
    ) -> None:
        await deliver("https://example.com/edited.png", prompt)

    service.generate = AsyncMock(side_effect=generate)
    return service


@pytest.fixture
def bot() -> MagicMock:
    """Каждый file_id превращается в различимые байты, без сети."""
    bot = MagicMock(id=1)
    bot.download = AsyncMock(side_effect=lambda file_id: BytesIO(file_id.encode()))
    return bot


@pytest.fixture
def l10n() -> MagicMock:
    """Явная локализация без чтения настроек окружения."""
    l10n = MagicMock()
    l10n.get.side_effect = lambda key, **_: key
    return l10n


@asynccontextmanager
async def session_factory() -> AsyncIterator[MagicMock]:
    """Не подключаться к рабочей БД в тестах обработчиков."""
    yield MagicMock()


async def test_two_corrections_use_latest_result(
    state: FSMContext,
    message: MagicMock,
    bot: MagicMock,
    service: MagicMock,
    l10n: MagicMock,
) -> None:
    """Правки используют последнее фото и не повторяются для того же message_id."""
    await state.set_state(CardStates.waiting_for_correction)
    await state.set_data({"session_id": "test", "result_file_id": "original-result"})
    await asyncio.gather(
        *[
            handle_correction(message, state, l10n, bot, service, session_factory)
            for _ in range(2)
        ]
    )
    service.generate.assert_awaited_once()
    assert service.generate.call_args.args[2] == b"original-result"
    assert message.text in service.generate.call_args.args[3]
    assert (await state.get_data())["result_file_id"] == "edited"
    message.message_id = 101
    message.text = "убери листья"
    await handle_correction(message, state, l10n, bot, service, session_factory)
    assert service.generate.call_args.args[2] == b"edited"
    assert await state.get_state() == CardStates.waiting_for_correction.state


async def test_failure_preserves_last_photo(
    state: FSMContext,
    message: MagicMock,
    bot: MagicMock,
    service: MagicMock,
    l10n: MagicMock,
) -> None:
    """Повторная попытка после ошибки не требует загрузки фото заново."""
    await state.set_state(CardStates.waiting_for_correction)
    await state.set_data({"session_id": "test", "result_file_id": "last"})
    service.generate.side_effect = CardError("card_empty_response")
    await handle_correction(message, state, l10n, bot, service, session_factory)
    assert (await state.get_data())["result_file_id"] == "last"
    assert await state.get_state() == CardStates.waiting_for_correction.state
    message.answer.assert_any_await("card_empty_response")
    message.answer.return_value.delete.assert_awaited_once()


async def test_reply_recovers_old_card(
    state: FSMContext,
    message: MagicMock,
    bot: MagicMock,
    service: MagicMock,
    l10n: MagicMock,
) -> None:
    """Фото, созданное до обновления, восстанавливается из ответа пользователя."""
    message.reply_to_message.from_user.id = bot.id
    message.reply_to_message.photo = [MagicMock(file_id="old-card")]
    await handle_correction(message, state, l10n, bot, service, session_factory)
    assert service.generate.call_args.args[2] == b"old-card"


async def test_reply_selects_photo_in_active_session(
    state: FSMContext,
    message: MagicMock,
    bot: MagicMock,
    service: MagicMock,
    l10n: MagicMock,
) -> None:
    """Ответ на старое фото имеет приоритет над последним результатом FSM."""
    await state.set_state(CardStates.waiting_for_correction)
    await state.set_data({"session_id": "test", "result_file_id": "latest"})
    message.reply_to_message.from_user.id = bot.id
    message.reply_to_message.photo = [MagicMock(file_id="selected")]
    await handle_correction(message, state, l10n, bot, service, session_factory)
    assert service.generate.call_args.args[2] == b"selected"
    await handle_correction(message, state, l10n, bot, service, session_factory)
    service.generate.assert_awaited_once()
    assert (await state.get_data())["result_file_id"] == "edited"


async def test_other_command_state_is_preserved(
    state: FSMContext,
    message: MagicMock,
    bot: MagicMock,
    service: MagicMock,
    l10n: MagicMock,
) -> None:
    """Фоновая генерация не стирает новый диалог пользователя."""
    await state.set_state(CardStates.waiting_for_correction)
    await state.set_data({"session_id": "test", "result_file_id": "last"})

    async def generate(
        session: object,
        user_id: int,
        image: bytes,
        prompt: str,
        deliver: Callable[[str, str], Awaitable[None]],
    ) -> None:
        await state.set_state("ChatGPTStates:waiting_for_message")
        await state.set_data({"model_key": "chat"})
        await deliver("https://example.com/result.png", prompt)

    service.generate.side_effect = generate
    await handle_correction(message, state, l10n, bot, service, session_factory)
    assert await state.get_state() == "ChatGPTStates:waiting_for_message"
    assert await state.get_data() == {"model_key": "chat"}


async def test_overlong_change_does_not_call_ai(
    state: FSMContext,
    message: MagicMock,
    bot: MagicMock,
    service: MagicMock,
    l10n: MagicMock,
) -> None:
    """Ограничение длины сохраняет промпт в пределах сообщения Telegram."""
    await state.set_state(CardStates.waiting_for_correction)
    await state.set_data({"session_id": "test", "result_file_id": "last"})
    message.text = "а" * 1501
    await handle_correction(message, state, l10n, bot, service, session_factory)
    service.generate.assert_not_awaited()
    message.answer.assert_any_await("card_correction_length")
