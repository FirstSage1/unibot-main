"""Репозиторий для работы с рассылками.

Содержит все операции с таблицей broadcasts:
- CRUD операции (создание, чтение, обновление)
- Поиск активных рассылок для обработки
- Обновление прогресса отправки
- Смена статусов

Паттерн работы с рассылками:
1. Администратор создаёт рассылку (статус DRAFT)
2. Администратор запускает рассылку (статус PENDING → RUNNING)
3. BroadcastWorker обрабатывает рассылку (обновляет прогресс)
4. После завершения статус меняется на COMPLETED
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.broadcast import Broadcast, BroadcastStatus


class BroadcastRepository:
    """Репозиторий для работы с рассылками.

    Использует Dependency Injection — сессия передаётся в конструктор.
    Это позволяет легко тестировать код без реальной БД.

    Пример использования:
        async with DatabaseSession() as session:
            repo = BroadcastRepository(session)
            broadcast = await repo.get_by_id(1)
            if broadcast and broadcast.can_start:
                await repo.start(broadcast)
    """

    def __init__(self, session: AsyncSession) -> None:
        """Инициализировать репозиторий.

        Args:
            session: Асинхронная сессия SQLAlchemy.
        """
        self._session = session

    async def create(
        self,
        name: str,
        message_text: str,
        parse_mode: str = "HTML",
        created_by_id: int | None = None,
        filter_language: str | None = None,
        filter_has_payments: bool | None = None,
        filter_source: str | None = None,
        filter_registered_after: datetime | None = None,
        filter_registered_before: datetime | None = None,
        filter_exclude_blocked: bool = True,
    ) -> Broadcast:
        """Создать новую рассылку.

        Рассылка создаётся в статусе DRAFT — её можно редактировать
        до запуска.

        Args:
            name: Название рассылки для администратора.
            message_text: Текст сообщения (с форматированием).
            parse_mode: Режим форматирования (HTML, Markdown и др.).
            created_by_id: ID администратора, создавшего рассылку.
            filter_language: Фильтр по языку (None = все).
            filter_has_payments: Фильтр по оплатам (None = все).
            filter_source: Фильтр по источнику (None = все).
            filter_registered_after: Только зарегистрированные после даты.
            filter_registered_before: Только зарегистрированные до даты.
            filter_exclude_blocked: Исключить заблокированных.

        Returns:
            Созданная рассылка в статусе DRAFT.

        Example:
            broadcast = await repo.create(
                name="Новогодняя акция 2025",
                message_text="<b>Привет!</b> У нас скидки!",
                filter_language="ru",
            )
        """
        broadcast = Broadcast(
            name=name,
            message_text=message_text,
            parse_mode=parse_mode,
            status=BroadcastStatus.DRAFT,
            created_by_id=created_by_id,
            filter_language=filter_language,
            filter_has_payments=filter_has_payments,
            filter_source=filter_source,
            filter_registered_after=filter_registered_after,
            filter_registered_before=filter_registered_before,
            filter_exclude_blocked=filter_exclude_blocked,
        )

        self._session.add(broadcast)
        await self._session.commit()
        await self._session.refresh(broadcast)
        return broadcast

    async def get_by_id(self, broadcast_id: int) -> Broadcast | None:
        """Получить рассылку по ID.

        Args:
            broadcast_id: ID рассылки.

        Returns:
            Объект Broadcast или None если не найдена.
        """
        return await self._session.get(Broadcast, broadcast_id)

    async def get_active_broadcasts(self) -> list[Broadcast]:
        """Получить список активных рассылок для обработки.

        Активные рассылки — это рассылки в статусе PENDING или RUNNING.
        Используется BroadcastWorker для получения списка рассылок,
        которые нужно обрабатывать.

        Returns:
            Список активных рассылок (старые первыми — FIFO).
        """
        stmt = (
            select(Broadcast)
            .where(
                Broadcast.status.in_(
                    [
                        BroadcastStatus.PENDING,
                        BroadcastStatus.RUNNING,
                    ]
                )
            )
            .order_by(Broadcast.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_all(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Broadcast]:
        """Получить список всех рассылок с пагинацией.

        Используется в админке для отображения истории рассылок.

        Args:
            limit: Максимальное количество записей.
            offset: Смещение (для пагинации).

        Returns:
            Список рассылок (новые первыми).
        """
        stmt = (
            select(Broadcast)
            .order_by(Broadcast.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def start(
        self,
        broadcast: Broadcast,
        total_recipients: int,
    ) -> Broadcast:
        """Запустить рассылку.

        Меняет статус на RUNNING и фиксирует время начала.
        Можно запустить только рассылку в статусе DRAFT или PAUSED.

        Args:
            broadcast: Рассылка для запуска.
            total_recipients: Количество получателей.

        Returns:
            Обновлённая рассылка.

        Raises:
            ValueError: Если рассылка не может быть запущена.
        """
        if not broadcast.can_start:
            raise ValueError(
                f"Нельзя запустить рассылку в статусе {broadcast.status}. "
                f"Допустимые статусы: {BroadcastStatus.DRAFT}, {BroadcastStatus.PAUSED}"
            )

        broadcast.status = BroadcastStatus.RUNNING
        broadcast.total_recipients = total_recipients

        # Фиксируем время начала только при первом запуске
        if broadcast.started_at is None:
            broadcast.started_at = datetime.now(UTC)

        await self._session.commit()
        await self._session.refresh(broadcast)
        return broadcast

    async def pause(self, broadcast: Broadcast) -> Broadcast:
        """Приостановить рассылку.

        Меняет статус на PAUSED. Рассылку можно возобновить позже.
        Можно приостановить только рассылку в статусе RUNNING.

        Args:
            broadcast: Рассылка для приостановки.

        Returns:
            Обновлённая рассылка.

        Raises:
            ValueError: Если рассылка не может быть приостановлена.
        """
        if not broadcast.can_pause:
            raise ValueError(
                f"Нельзя приостановить рассылку в статусе {broadcast.status}. "
                f"Допустимый статус: {BroadcastStatus.RUNNING}"
            )

        broadcast.status = BroadcastStatus.PAUSED
        await self._session.commit()
        await self._session.refresh(broadcast)
        return broadcast

    async def cancel(self, broadcast: Broadcast) -> Broadcast:
        """Отменить рассылку.

        Меняет статус на CANCELLED. Отменённую рассылку нельзя возобновить.

        Args:
            broadcast: Рассылка для отмены.

        Returns:
            Обновлённая рассылка.

        Raises:
            ValueError: Если рассылка не может быть отменена.
        """
        if not broadcast.can_cancel:
            raise ValueError(
                f"Нельзя отменить рассылку в статусе {broadcast.status}. "
                f"Допустимые статусы: DRAFT, PENDING, RUNNING, PAUSED"
            )

        broadcast.status = BroadcastStatus.CANCELLED
        broadcast.completed_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(broadcast)
        return broadcast

    async def complete(self, broadcast: Broadcast) -> Broadcast:
        """Завершить рассылку.

        Меняет статус на COMPLETED и фиксирует время завершения.

        Args:
            broadcast: Рассылка для завершения.

        Returns:
            Обновлённая рассылка.
        """
        broadcast.status = BroadcastStatus.COMPLETED
        broadcast.completed_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(broadcast)
        return broadcast

    async def fail(
        self,
        broadcast: Broadcast,
        error_message: str,
    ) -> Broadcast:
        """Отметить рассылку как неудачную.

        Меняет статус на FAILED и сохраняет сообщение об ошибке.
        Используется при критических ошибках, остановивших рассылку.

        Args:
            broadcast: Рассылка, завершившаяся с ошибкой.
            error_message: Описание ошибки.

        Returns:
            Обновлённая рассылка.
        """
        broadcast.status = BroadcastStatus.FAILED
        broadcast.error_message = error_message
        broadcast.completed_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(broadcast)
        return broadcast

    async def update_progress(
        self,
        broadcast_id: int,
        sent_count: int,
        failed_count: int,
        last_processed_user_id: int,
    ) -> None:
        """Обновить прогресс рассылки (DEPRECATED).

        .. deprecated:: 1.0
            Используйте :meth:`increment_progress` для избежания race conditions.
            Этот метод устанавливает абсолютные значения и может потерять данные
            при конкурентных обновлениях.

        Args:
            broadcast_id: ID рассылки.
            sent_count: Новое количество успешно отправленных.
            failed_count: Новое количество неудачных попыток.
            last_processed_user_id: ID последнего обработанного пользователя.
        """
        stmt = (
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                sent_count=sent_count,
                failed_count=failed_count,
                last_processed_user_id=last_processed_user_id,
            )
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def increment_progress(
        self,
        broadcast_id: int,
        sent_delta: int = 0,
        failed_delta: int = 0,
        last_processed_user_id: int | None = None,
    ) -> None:
        """Инкрементально обновить прогресс рассылки.

        Атомарно увеличивает счётчики на указанные значения.
        Используется для обновления прогресса после обработки одного сообщения.

        Args:
            broadcast_id: ID рассылки.
            sent_delta: На сколько увеличить sent_count (обычно 0 или 1).
            failed_delta: На сколько увеличить failed_count (обычно 0 или 1).
            last_processed_user_id: ID последнего обработанного пользователя.
        """
        values: dict[str, object] = {}

        if sent_delta != 0:
            values["sent_count"] = Broadcast.sent_count + sent_delta

        if failed_delta != 0:
            values["failed_count"] = Broadcast.failed_count + failed_delta

        if last_processed_user_id is not None:
            values["last_processed_user_id"] = last_processed_user_id

        if not values:
            return

        stmt = update(Broadcast).where(Broadcast.id == broadcast_id).values(**values)
        await self._session.execute(stmt)
        await self._session.commit()

    async def set_status(
        self,
        broadcast_id: int,
        status: BroadcastStatus,
    ) -> None:
        """Установить статус рассылки напрямую.

        Низкоуровневый метод для смены статуса без загрузки объекта.
        Используется BroadcastWorker для быстрой смены статуса.

        Args:
            broadcast_id: ID рассылки.
            status: Новый статус.
        """
        stmt = (
            update(Broadcast).where(Broadcast.id == broadcast_id).values(status=status)
        )
        await self._session.execute(stmt)
        await self._session.commit()
