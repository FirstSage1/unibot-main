"""Репозиторий для работы с транзакциями (леджер баланса).

Содержит все операции с таблицей transactions:
- Создание транзакции с обновлением баланса пользователя
- Получение истории транзакций с пагинацией
- Подсчёт транзакций по типу

Паттерн "леджер":
- Транзакции никогда не изменяются и не удаляются
- Для отмены операции создаётся обратная транзакция
- Текущий баланс = сумма всех транзакций пользователя
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.transaction import Transaction, TransactionType
from src.db.models.user import User


class TransactionRepository:
    """Репозиторий для работы с транзакциями.

    Использует Dependency Injection — сессия передаётся в конструктор.
    Это позволяет легко тестировать код без реальной БД.

    Пример использования:
        async with DatabaseSession() as session:
            repo = TransactionRepository(session)
            await repo.create(
                user=user,
                type_=TransactionType.GENERATION,
                amount=-15,
                description="Генерация: GPT-4o",
            )
    """

    def __init__(self, session: AsyncSession) -> None:
        """Инициализировать репозиторий.

        Args:
            session: Асинхронная сессия SQLAlchemy.
        """
        self._session = session

    async def create(
        self,
        user: User,
        type_: TransactionType,
        amount: int,
        description: str,
        metadata_json: str | None = None,
    ) -> Transaction:
        """Создать транзакцию и обновить баланс пользователя.

        Атомарная операция:
        1. Вычисляет новый баланс
        2. Создаёт транзакцию с balance_after
        3. Обновляет User.balance

        Args:
            user: Пользователь, которому создаётся транзакция.
            type_: Тип транзакции (TransactionType).
            amount: Сумма операции (+ начисление, - списание).
            description: Описание для отображения в истории.
            metadata_json: JSON-строка с дополнительными данными.

        Returns:
            Созданная транзакция.

        Example:
            # Бонус при регистрации
            await repo.create(
                user=user,
                type_=TransactionType.REGISTRATION_BONUS,
                amount=100,
                description="Бонус за регистрацию",
            )

            # Списание за генерацию
            await repo.create(
                user=user,
                type_=TransactionType.GENERATION,
                amount=-15,
                description="Генерация: GPT-4o",
                metadata_json='{"model_key": "gpt-4o", "generation_type": "chat"}',
            )
        """
        # Вычисляем новый баланс
        new_balance = user.balance + amount

        # Создаём транзакцию
        transaction = Transaction(
            user_id=user.id,
            type=type_.value,
            amount=amount,
            balance_after=new_balance,
            description=description,
            metadata_json=metadata_json,
        )

        # Обновляем баланс пользователя
        user.balance = new_balance

        # Сохраняем
        self._session.add(transaction)
        await self._session.commit()
        await self._session.refresh(transaction)

        return transaction

    async def get_history(
        self,
        user_id: int,
        limit: int = 10,
        offset: int = 0,
    ) -> list[Transaction]:
        """Получить историю транзакций пользователя.

        Транзакции отсортированы по дате создания (новые первыми).
        Поддерживает пагинацию через limit/offset.

        Args:
            user_id: Внутренний ID пользователя.
            limit: Максимальное количество записей (по умолчанию 10).
            offset: Смещение от начала (для пагинации).

        Returns:
            Список транзакций (новые первыми).

        Example:
            # Первая страница (10 записей)
            page1 = await repo.get_history(user.id, limit=10, offset=0)

            # Вторая страница
            page2 = await repo.get_history(user.id, limit=10, offset=10)
        """
        stmt = (
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_total(self, user_id: int) -> int:
        """Подсчитать общее количество транзакций пользователя.

        Используется для пагинации в /history (показать "Страница 1 из N").

        Args:
            user_id: Внутренний ID пользователя.

        Returns:
            Общее количество транзакций.
        """
        stmt = select(func.count(Transaction.id)).where(Transaction.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def count_by_type(
        self,
        user_id: int,
        type_: TransactionType,
    ) -> int:
        """Подсчитать количество транзакций определённого типа.

        Может использоваться для аналитики:
        - Сколько генераций выполнил пользователь
        - Сколько покупок совершил

        Args:
            user_id: Внутренний ID пользователя.
            type_: Тип транзакции для подсчёта.

        Returns:
            Количество транзакций указанного типа.

        Example:
            # Сколько генераций выполнено
            generations = await repo.count_by_type(
                user.id,
                TransactionType.GENERATION,
            )
        """
        stmt = (
            select(func.count(Transaction.id))
            .where(Transaction.user_id == user_id)
            .where(Transaction.type == type_.value)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()
