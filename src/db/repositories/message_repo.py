"""Репозиторий для работы с историей сообщений (контекст диалога).

Этот модуль реализует методы для:
- Добавления сообщений в историю
- Получения контекста диалога для AI
- Очистки истории (команда /clear)
- Ограничения размера контекста (max_messages)
"""

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.message import Message
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Максимальное количество сообщений в контексте по умолчанию
# Можно настроить через config.yaml в будущем
DEFAULT_MAX_CONTEXT_MESSAGES = 20


class MessageRepository:
    """Репозиторий для работы с историей сообщений.

    Управляет контекстом диалога:
    - Сохраняет сообщения пользователя и ответы AI
    - Загружает историю для передачи в AI (контекст)
    - Очищает старые сообщения при превышении лимита
    - Удаляет весь контекст по запросу пользователя

    Attributes:
        session: Асинхронная сессия SQLAlchemy для работы с БД.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Создать репозиторий сообщений.

        Args:
            session: Асинхронная сессия для работы с БД.
        """
        self.session = session

    async def add_message(
        self,
        user_id: int,
        model_key: str,
        role: str,
        content: str,
    ) -> Message:
        """Добавить сообщение в историю.

        Args:
            user_id: ID пользователя (из таблицы users).
            model_key: Ключ модели из config.yaml (например, "gpt-4o").
            role: Роль отправителя ("user" или "assistant").
            content: Текст сообщения.

        Returns:
            Созданное сообщение.
        """
        message = Message(
            user_id=user_id,
            model_key=model_key,
            role=role,
            content=content,
        )
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(message)

        logger.debug(
            "Сообщение добавлено: user_id=%d, model=%s, role=%s",
            user_id,
            model_key,
            role,
        )

        return message

    async def get_context(
        self,
        user_id: int,
        model_key: str,
        max_messages: int = DEFAULT_MAX_CONTEXT_MESSAGES,
    ) -> list[Message]:
        """Получить контекст диалога для передачи в AI.

        Возвращает последние N сообщений пользователя и AI,
        отсортированные от старых к новым (хронологически).

        Args:
            user_id: ID пользователя.
            model_key: Ключ модели (контекст привязан к конкретной модели).
            max_messages: Максимальное количество сообщений в контексте.

        Returns:
            Список сообщений (от самого старого к самому новому).
        """
        # Запрос последних N сообщений (сортировка по убыванию)
        # Используем и created_at, и id для детерминированной сортировки
        # (в тестах created_at может быть одинаковым для нескольких записей)
        stmt = (
            select(Message)
            .where(
                Message.user_id == user_id,
                Message.model_key == model_key,
            )
            .order_by(desc(Message.created_at), desc(Message.id))
            .limit(max_messages)
        )

        result = await self.session.execute(stmt)
        messages = list(result.scalars().all())

        # Переворачиваем список: от старых к новым (AI ожидает хронологию)
        messages.reverse()

        logger.debug(
            "Контекст загружен: user_id=%d, model=%s, сообщений=%d",
            user_id,
            model_key,
            len(messages),
        )

        return messages

    async def clear_context(
        self,
        user_id: int,
        model_key: str | None = None,
    ) -> int:
        """Очистить историю сообщений (команда /clear).

        Args:
            user_id: ID пользователя.
            model_key: Ключ модели (если None — удаляются все сообщения пользователя).

        Returns:
            Количество удалённых сообщений.
        """
        # Формируем условия удаления
        conditions = [Message.user_id == user_id]
        if model_key:
            conditions.append(Message.model_key == model_key)

        stmt = delete(Message).where(*conditions)
        result = await self.session.execute(stmt)
        await self.session.commit()

        # result.rowcount существует для DELETE операций, но mypy не видит это
        # Используем getattr для безопасного доступа
        deleted_count = getattr(result, "rowcount", 0) or 0

        logger.info(
            "Контекст очищен: user_id=%d, model=%s, удалено=%d",
            user_id,
            model_key or "all",
            deleted_count,
        )

        return deleted_count

    async def count_messages(
        self,
        user_id: int,
        model_key: str | None = None,
    ) -> int:
        """Подсчитать количество сообщений в истории.

        Использует SQL COUNT для эффективного подсчёта без загрузки данных в память.

        Args:
            user_id: ID пользователя.
            model_key: Ключ модели (если None — считаются все сообщения).

        Returns:
            Количество сообщений.
        """
        conditions = [Message.user_id == user_id]
        if model_key:
            conditions.append(Message.model_key == model_key)

        # Используем SQL COUNT вместо загрузки всех записей
        stmt = select(func.count()).select_from(Message).where(*conditions)
        result = await self.session.execute(stmt)

        return result.scalar_one()
