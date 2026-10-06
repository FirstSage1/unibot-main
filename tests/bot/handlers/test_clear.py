"""Тесты для обработчика команды /clear.

Проверяют корректность работы очистки истории диалога:
- Очистка истории для конкретной модели
- Очистка всей истории если пользователь не в режиме диалога
- Обработка пустой истории
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers.clear import cmd_clear
from src.bot.states import ChatGPTStates
from src.db.models.user import User
from src.db.repositories import MessageRepository
from src.utils.i18n import Localization


@pytest.fixture
def mock_message() -> Message:
    """Создать мок-объект сообщения от Telegram."""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(spec=TelegramUser)
    message.from_user.id = 123456789
    message.from_user.username = "test_user"
    message.answer = AsyncMock()
    return message


@pytest.fixture
def mock_fsm_context() -> FSMContext:
    """Создать мок-объект FSM контекста."""
    context = MagicMock(spec=FSMContext)
    context.get_state = AsyncMock(return_value=None)
    context.get_data = AsyncMock(return_value={})
    return context


@pytest.fixture
def mock_l10n() -> Localization:
    """Создать мок-объект локализации."""
    l10n = MagicMock(spec=Localization)

    # Словарь переводов для тестов
    translations = {
        "clear_history_empty": "ℹ️ История диалога уже пуста.",
        "clear_history_model": (
            "✅ История диалога с моделью <b>{model_key}</b> очищена.\n\n"
            "Удалено сообщений: {deleted_count}\n\n"
            "Можете продолжить диалог с чистого листа."
        ),
        "clear_history_all": (
            "✅ Вся история диалогов очищена.\n\n"
            "Удалено сообщений: {deleted_count}\n\n"
            "Используйте /chatgpt для начала нового диалога."
        ),
        "clear_unexpected_error": "❌ Произошла неожиданная ошибка.",
        "error_user_not_found": "❌ Ошибка: пользователь не найден.",
        "error_db_temporary": "❌ Временная ошибка БД.",
        "error_db_permanent": "❌ Ошибка при работе с базой данных.",
    }

    def get_translation(key: str, **kwargs: str) -> str:
        """Вернуть перевод с подставленными параметрами."""
        text = translations.get(key, key)
        if kwargs:
            text = text.format(**kwargs)
        return text

    l10n.get = MagicMock(side_effect=get_translation)
    return l10n


class TestCmdClear:
    """Тесты для команды /clear."""

    @pytest.mark.asyncio
    async def test_cmd_clear_deletes_messages_for_current_model(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Проверить, что /clear удаляет сообщения для текущей модели."""
        # Arrange
        mock_fsm_context.get_state = AsyncMock(
            return_value=ChatGPTStates.waiting_for_message
        )
        mock_fsm_context.get_data = AsyncMock(return_value={"model_key": "gpt-4o"})

        # Создаём сообщения в БД
        repo = MessageRepository(db_session)
        await repo.add_message(test_user.id, "gpt-4o", "user", "Сообщение 1")
        await repo.add_message(test_user.id, "gpt-4o", "assistant", "Ответ 1")
        await repo.add_message(
            test_user.id,
            "claude-3-5-sonnet-20241022",
            "user",
            "Сообщение Claude",
        )

        # Act
        await cmd_clear(mock_message, mock_fsm_context, mock_l10n, session_factory)

        # Assert
        # Проверяем, что сообщения gpt-4o удалены
        gpt_messages = await repo.get_context(test_user.id, "gpt-4o")
        assert len(gpt_messages) == 0

        # Проверяем, что сообщения Claude остались
        claude_messages = await repo.get_context(
            test_user.id, "claude-3-5-sonnet-20241022"
        )
        assert len(claude_messages) == 1

    @pytest.mark.asyncio
    async def test_cmd_clear_deletes_all_messages_if_not_in_dialog(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Проверить, что /clear удаляет все сообщения если не в режиме диалога."""
        # Arrange
        mock_fsm_context.get_state = AsyncMock(return_value=None)

        # Создаём сообщения для разных моделей
        repo = MessageRepository(db_session)
        await repo.add_message(test_user.id, "gpt-4o", "user", "Сообщение GPT")
        await repo.add_message(
            test_user.id, "claude-3-5-sonnet-20241022", "user", "Сообщение Claude"
        )

        # Act
        await cmd_clear(mock_message, mock_fsm_context, mock_l10n, session_factory)

        # Assert
        # Проверяем, что все сообщения удалены
        gpt_messages = await repo.get_context(test_user.id, "gpt-4o")
        assert len(gpt_messages) == 0

        claude_messages = await repo.get_context(
            test_user.id, "claude-3-5-sonnet-20241022"
        )
        assert len(claude_messages) == 0

    @pytest.mark.asyncio
    async def test_cmd_clear_shows_success_message_with_count(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Проверить, что отображается сообщение с количеством удалённых."""
        # Arrange
        mock_fsm_context.get_state = AsyncMock(
            return_value=ChatGPTStates.waiting_for_message
        )
        mock_fsm_context.get_data = AsyncMock(return_value={"model_key": "gpt-4o"})

        # Создаём 3 сообщения
        repo = MessageRepository(db_session)
        await repo.add_message(test_user.id, "gpt-4o", "user", "Сообщение 1")
        await repo.add_message(test_user.id, "gpt-4o", "assistant", "Ответ 1")
        await repo.add_message(test_user.id, "gpt-4o", "user", "Сообщение 2")

        # Act
        await cmd_clear(mock_message, mock_fsm_context, mock_l10n, session_factory)

        # Assert
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        assert "очищена" in call_args[0][0]
        assert "3" in call_args[0][0]  # Количество удалённых
        assert "gpt-4o" in call_args[0][0]  # Название модели

    @pytest.mark.asyncio
    async def test_cmd_clear_shows_empty_message_if_no_history(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Проверить, что показывается сообщение если история пуста."""
        # Act
        await cmd_clear(mock_message, mock_fsm_context, mock_l10n, session_factory)

        # Assert
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        assert "уже пуста" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_clear_without_from_user(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
    ) -> None:
        """Проверить обработку отсутствия from_user."""
        # Arrange
        mock_message.from_user = None

        # Act
        await cmd_clear(mock_message, mock_fsm_context, mock_l10n)

        # Assert
        mock_message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_cmd_clear_shows_error_if_user_not_found(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        db_session: AsyncSession,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Проверить, что показывается ошибка если пользователь не найден."""
        # Arrange
        # Не создаём test_user, чтобы симулировать отсутствие пользователя

        # Act
        await cmd_clear(mock_message, mock_fsm_context, mock_l10n, session_factory)

        # Assert
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        assert "не найден" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_clear_handles_database_error(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Проверить обработку ошибки БД."""
        # Arrange
        from contextlib import asynccontextmanager

        from src.db.exceptions import DatabaseError

        @asynccontextmanager
        async def error_session_factory() -> AsyncGenerator[AsyncSession, None]:
            """Фабрика сессий, выбрасывающая ошибку БД."""
            raise DatabaseError("Database connection failed", retryable=True)
            yield  # unreachable but needed for type checker

        # Act
        await cmd_clear(
            mock_message, mock_fsm_context, mock_l10n, error_session_factory
        )

        # Assert
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        # Проверяем, что показывается сообщение о временной ошибке
        assert "Временная ошибка БД" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_clear_message_format_for_specific_model(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Проверить формат сообщения при очистке конкретной модели."""
        # Arrange
        mock_fsm_context.get_state = AsyncMock(
            return_value=ChatGPTStates.waiting_for_message
        )
        mock_fsm_context.get_data = AsyncMock(return_value={"model_key": "gpt-4o"})

        repo = MessageRepository(db_session)
        await repo.add_message(test_user.id, "gpt-4o", "user", "Сообщение")

        # Act
        await cmd_clear(mock_message, mock_fsm_context, mock_l10n, session_factory)

        # Assert
        call_args = mock_message.answer.call_args
        message_text = call_args[0][0]
        assert "модель" in message_text.lower()
        assert "gpt-4o" in message_text
        assert "продолжить диалог" in message_text.lower()

    @pytest.mark.asyncio
    async def test_cmd_clear_message_format_for_all_models(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Проверить формат сообщения при очистке всех моделей."""
        # Arrange
        mock_fsm_context.get_state = AsyncMock(return_value=None)

        repo = MessageRepository(db_session)
        await repo.add_message(test_user.id, "gpt-4o", "user", "Сообщение")

        # Act
        await cmd_clear(mock_message, mock_fsm_context, mock_l10n, session_factory)

        # Assert
        call_args = mock_message.answer.call_args
        message_text = call_args[0][0]
        assert "Вся история" in message_text
        assert "/chatgpt" in message_text

    @pytest.mark.asyncio
    async def test_cmd_clear_counts_messages_correctly(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    ) -> None:
        """Проверить корректность подсчёта удалённых сообщений."""
        # Arrange
        # Создаём 5 сообщений
        repo = MessageRepository(db_session)
        for i in range(5):
            await repo.add_message(test_user.id, "gpt-4o", "user", f"Сообщение {i}")

        # Act
        await cmd_clear(mock_message, mock_fsm_context, mock_l10n, session_factory)

        # Assert
        call_args = mock_message.answer.call_args
        assert "5" in call_args[0][0]
