"""Тесты для MessageRepository.

Проверяют корректность работы с историей сообщений:
- Добавление сообщений (add_message)
- Получение контекста диалога (get_context)
- Очистка истории (clear_context)
- Подсчёт сообщений (count_messages)
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.user import User
from src.db.repositories.message_repo import MessageRepository


class TestMessageRepository:
    """Тесты для репозитория сообщений."""

    @pytest.mark.asyncio
    async def test_add_message_creates_message(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что add_message создаёт сообщение в БД."""
        # Arrange
        repo = MessageRepository(db_session)

        # Act
        message = await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content="Привет, как дела?",
        )

        # Assert
        assert message.id is not None
        assert message.user_id == test_user.id
        assert message.model_key == "gpt-4o"
        assert message.role == "user"
        assert message.content == "Привет, как дела?"
        assert message.created_at is not None

    @pytest.mark.asyncio
    async def test_add_message_with_assistant_role(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить добавление сообщения от assistant (AI)."""
        # Arrange
        repo = MessageRepository(db_session)

        # Act
        message = await repo.add_message(
            user_id=test_user.id,
            model_key="claude-3-5-sonnet-20241022",
            role="assistant",
            content="Привет! У меня всё отлично, спасибо!",
        )

        # Assert
        assert message.role == "assistant"
        assert message.model_key == "claude-3-5-sonnet-20241022"

    @pytest.mark.asyncio
    async def test_get_context_returns_empty_for_new_user(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить пустой список для нового пользователя."""
        # Arrange
        repo = MessageRepository(db_session)

        # Act
        context = await repo.get_context(
            user_id=test_user.id,
            model_key="gpt-4o",
        )

        # Assert
        assert context == []

    @pytest.mark.asyncio
    async def test_get_context_returns_messages_in_chronological_order(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что get_context возвращает сообщения от старых к новым."""
        # Arrange
        repo = MessageRepository(db_session)

        # Создаём диалог из 3 сообщений
        msg1 = await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content="Первое сообщение",
        )
        msg2 = await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="assistant",
            content="Второе сообщение",
        )
        msg3 = await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content="Третье сообщение",
        )

        # Act
        context = await repo.get_context(
            user_id=test_user.id,
            model_key="gpt-4o",
        )

        # Assert
        assert len(context) == 3
        # Проверяем по ID, так как created_at может быть одинаковым в тестах
        assert context[0].id == msg1.id
        assert context[1].id == msg2.id
        assert context[2].id == msg3.id

    @pytest.mark.asyncio
    async def test_get_context_filters_by_model_key(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что get_context фильтрует сообщения по model_key."""
        # Arrange
        repo = MessageRepository(db_session)

        # Создаём сообщения для разных моделей
        await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content="Сообщение для GPT-4o",
        )
        await repo.add_message(
            user_id=test_user.id,
            model_key="claude-3-5-sonnet-20241022",
            role="user",
            content="Сообщение для Claude",
        )

        # Act
        gpt_context = await repo.get_context(
            user_id=test_user.id,
            model_key="gpt-4o",
        )
        claude_context = await repo.get_context(
            user_id=test_user.id,
            model_key="claude-3-5-sonnet-20241022",
        )

        # Assert
        assert len(gpt_context) == 1
        assert gpt_context[0].content == "Сообщение для GPT-4o"

        assert len(claude_context) == 1
        assert claude_context[0].content == "Сообщение для Claude"

    @pytest.mark.asyncio
    async def test_get_context_respects_max_messages_limit(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что get_context возвращает не больше max_messages."""
        # Arrange
        repo = MessageRepository(db_session)

        # Создаём 10 сообщений и сохраняем их для проверки
        messages = []
        for i in range(10):
            msg = await repo.add_message(
                user_id=test_user.id,
                model_key="gpt-4o",
                role="user",
                content=f"Сообщение {i + 1}",
            )
            messages.append(msg)

        # Act
        context = await repo.get_context(
            user_id=test_user.id,
            model_key="gpt-4o",
            max_messages=5,
        )

        # Assert
        assert len(context) == 5
        # Должны вернуться последние 5 сообщений по ID
        assert context[0].id == messages[5].id  # Сообщение 6
        assert context[4].id == messages[9].id  # Сообщение 10

    @pytest.mark.asyncio
    async def test_get_context_filters_by_user_id(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что get_context фильтрует сообщения по user_id."""
        # Arrange
        repo = MessageRepository(db_session)

        # Создаём второго пользователя
        user2 = User(
            telegram_id=987654321,
            username="user2",
            first_name="User",
            last_name="Two",
            language="ru",
            balance=0,
        )
        db_session.add(user2)
        await db_session.commit()
        await db_session.refresh(user2)

        # Создаём сообщения для обоих пользователей
        await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content="Сообщение от user1",
        )
        await repo.add_message(
            user_id=user2.id,
            model_key="gpt-4o",
            role="user",
            content="Сообщение от user2",
        )

        # Act
        context_user1 = await repo.get_context(
            user_id=test_user.id,
            model_key="gpt-4o",
        )
        context_user2 = await repo.get_context(
            user_id=user2.id,
            model_key="gpt-4o",
        )

        # Assert
        assert len(context_user1) == 1
        assert context_user1[0].content == "Сообщение от user1"

        assert len(context_user2) == 1
        assert context_user2[0].content == "Сообщение от user2"

    @pytest.mark.asyncio
    async def test_clear_context_deletes_messages_for_model(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что clear_context удаляет сообщения для конкретной модели."""
        # Arrange
        repo = MessageRepository(db_session)

        # Создаём сообщения для разных моделей
        await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content="Сообщение GPT-4o #1",
        )
        await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content="Сообщение GPT-4o #2",
        )
        await repo.add_message(
            user_id=test_user.id,
            model_key="claude-3-5-sonnet-20241022",
            role="user",
            content="Сообщение Claude",
        )

        # Act
        deleted_count = await repo.clear_context(
            user_id=test_user.id,
            model_key="gpt-4o",
        )

        # Assert
        assert deleted_count == 2

        # Проверяем, что сообщения GPT-4o удалены
        gpt_context = await repo.get_context(test_user.id, "gpt-4o")
        assert len(gpt_context) == 0

        # Проверяем, что сообщения Claude остались
        claude_context = await repo.get_context(
            test_user.id,
            "claude-3-5-sonnet-20241022",
        )
        assert len(claude_context) == 1

    @pytest.mark.asyncio
    async def test_clear_context_deletes_all_messages_if_model_key_none(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что clear_context удаляет все сообщения если model_key=None."""
        # Arrange
        repo = MessageRepository(db_session)

        # Создаём сообщения для разных моделей
        await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content="Сообщение GPT-4o",
        )
        await repo.add_message(
            user_id=test_user.id,
            model_key="claude-3-5-sonnet-20241022",
            role="user",
            content="Сообщение Claude",
        )

        # Act
        deleted_count = await repo.clear_context(
            user_id=test_user.id,
            model_key=None,
        )

        # Assert
        assert deleted_count == 2

        # Проверяем, что все сообщения удалены
        gpt_context = await repo.get_context(test_user.id, "gpt-4o")
        assert len(gpt_context) == 0

        claude_context = await repo.get_context(
            test_user.id,
            "claude-3-5-sonnet-20241022",
        )
        assert len(claude_context) == 0

    @pytest.mark.asyncio
    async def test_clear_context_returns_zero_if_no_messages(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что clear_context возвращает 0 если сообщений нет."""
        # Arrange
        repo = MessageRepository(db_session)

        # Act
        deleted_count = await repo.clear_context(
            user_id=test_user.id,
            model_key="gpt-4o",
        )

        # Assert
        assert deleted_count == 0

    @pytest.mark.asyncio
    async def test_count_messages_returns_correct_count(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что count_messages возвращает правильное количество."""
        # Arrange
        repo = MessageRepository(db_session)

        # Создаём 3 сообщения для gpt-4o
        for i in range(3):
            await repo.add_message(
                user_id=test_user.id,
                model_key="gpt-4o",
                role="user",
                content=f"Сообщение {i + 1}",
            )

        # Создаём 2 сообщения для claude
        for i in range(2):
            await repo.add_message(
                user_id=test_user.id,
                model_key="claude-3-5-sonnet-20241022",
                role="user",
                content=f"Сообщение {i + 1}",
            )

        # Act
        gpt_count = await repo.count_messages(
            user_id=test_user.id,
            model_key="gpt-4o",
        )
        claude_count = await repo.count_messages(
            user_id=test_user.id,
            model_key="claude-3-5-sonnet-20241022",
        )
        total_count = await repo.count_messages(
            user_id=test_user.id,
            model_key=None,
        )

        # Assert
        assert gpt_count == 3
        assert claude_count == 2
        assert total_count == 5

    @pytest.mark.asyncio
    async def test_count_messages_returns_zero_for_empty_history(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что count_messages возвращает 0 для пустой истории."""
        # Arrange
        repo = MessageRepository(db_session)

        # Act
        count = await repo.count_messages(
            user_id=test_user.id,
            model_key="gpt-4o",
        )

        # Assert
        assert count == 0

    @pytest.mark.asyncio
    async def test_message_model_repr(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить строковое представление модели Message."""
        # Arrange
        repo = MessageRepository(db_session)

        # Act
        message = await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content="Короткий текст",
        )

        # Assert
        repr_str = repr(message)
        assert "Message" in repr_str
        assert f"id={message.id}" in repr_str
        assert f"user_id={test_user.id}" in repr_str
        assert "role=user" in repr_str
        assert "Короткий текст" in repr_str

    @pytest.mark.asyncio
    async def test_message_model_repr_truncates_long_content(
        self,
        db_session: AsyncSession,
        test_user: User,
    ) -> None:
        """Проверить, что __repr__ обрезает длинный контент."""
        # Arrange
        repo = MessageRepository(db_session)
        long_content = "a" * 100  # 100 символов

        # Act
        message = await repo.add_message(
            user_id=test_user.id,
            model_key="gpt-4o",
            role="user",
            content=long_content,
        )

        # Assert
        repr_str = repr(message)
        assert "..." in repr_str  # Контент должен быть обрезан
        # Проверяем, что в repr не показывается весь контент (только первые 50 символов)
        assert long_content not in repr_str
