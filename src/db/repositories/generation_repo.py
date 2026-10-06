"""Репозиторий для работы с генерациями AI.

Этот модуль реализует методы для:
- Создания записей о начале генерации
- Обновления статуса после завершения
- Проверки cooldown (время с последней генерации)
- Подсчёта активных генераций (лимит параллельных задач)
- Аналитики использования моделей

Используется вместо in-memory хранения для:
- Персистентности данных (переживает перезапуски)
- Синхронизации между воркерами (если запущено несколько экземпляров)
- Аудита и аналитики (история всех генераций сохраняется)
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.generation import Generation, GenerationDBStatus
from src.utils.logging import get_logger

logger = get_logger(__name__)


class GenerationRepository:
    """Репозиторий для работы с генерациями AI.

    Управляет записями о генерациях:
    - Создаёт запись при запуске генерации (статус PENDING)
    - Обновляет статус при завершении (COMPLETED или FAILED)
    - Предоставляет методы для проверки cooldown
    - Подсчитывает активные генерации для лимитов

    Attributes:
        session: Асинхронная сессия SQLAlchemy для работы с БД.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Создать репозиторий генераций.

        Args:
            session: Асинхронная сессия для работы с БД.
        """
        self.session = session

    async def create_generation(
        self,
        user_id: int,
        generation_type: str,
        model_key: str,
        cost_rub: Decimal | None = None,
    ) -> Generation:
        """Создать запись о начале генерации.

        Вызывается при запуске генерации, до отправки запроса к AI-провайдеру.
        Создаёт запись со статусом PENDING и текущим временем.

        Args:
            user_id: ID пользователя (из таблицы users).
            generation_type: Тип генерации (chat, image, image_edit, tts, stt).
            model_key: Ключ модели из config.yaml (например, "gpt-4o").
            cost_rub: Предварительная себестоимость в рублях (для image/tts/stt).
                Для chat-генераций себестоимость рассчитывается после получения
                ответа (зависит от количества токенов).

        Returns:
            Созданная запись генерации.

        Example:
            generation = await repo.create_generation(
                user_id=123,
                generation_type="image",
                model_key="flux-dev",
                cost_rub=Decimal("0.81"),
            )
            # Теперь можно выполнить генерацию
            result = await ai_service.generate(...)
            # После завершения обновить статус и метрики
            await repo.update_generation_completed(
                generation.id,
                tokens_charged=10,
                transaction_id=transaction.id,
            )
        """
        generation = Generation(
            user_id=user_id,
            generation_type=generation_type,
            model_key=model_key,
            status=GenerationDBStatus.PENDING,
            cost_rub=cost_rub or Decimal(0),
        )
        self.session.add(generation)
        await self.session.commit()
        await self.session.refresh(generation)

        logger.debug(
            "Генерация создана: id=%d, user_id=%d, type=%s, model=%s, cost_rub=%s",
            generation.id,
            user_id,
            generation_type,
            model_key,
            cost_rub or "0",
        )

        return generation

    async def update_generation_status(
        self,
        generation_id: int,
        status: GenerationDBStatus,
        completed_at: datetime | None = None,
        tokens_charged: int | None = None,
        cost_rub: Decimal | None = None,
        transaction_id: int | None = None,
    ) -> None:
        """Обновить статус генерации и метрики стоимости.

        Вызывается после завершения генерации (успешного или с ошибкой).
        Обновляет статус, время завершения и метрики себестоимости.

        Args:
            generation_id: ID генерации для обновления.
            status: Новый статус (COMPLETED или FAILED).
            completed_at: Время завершения. Если None — используется текущее время.
            tokens_charged: Количество списанных токенов (опционально).
            cost_rub: Себестоимость в рублях (опционально, обновляет если передано).
            transaction_id: ID транзакции списания (опционально).

        Example:
            # Успешное завершение с метриками
            await repo.update_generation_status(
                generation.id,
                GenerationDBStatus.COMPLETED,
                tokens_charged=10,
                cost_rub=Decimal("0.81"),
                transaction_id=transaction.id,
            )

            # Завершение с ошибкой (без метрик)
            await repo.update_generation_status(
                generation.id,
                GenerationDBStatus.FAILED,
            )
        """
        # Получаем генерацию из БД
        stmt = select(Generation).where(Generation.id == generation_id)
        result = await self.session.execute(stmt)
        generation = result.scalar_one_or_none()

        if generation is None:
            logger.warning("Генерация не найдена: id=%d", generation_id)
            return

        # Обновляем статус и время завершения
        generation.status = status
        generation.completed_at = completed_at or datetime.now(UTC)

        # Обновляем метрики стоимости если переданы
        if tokens_charged is not None:
            generation.tokens_charged = tokens_charged
        if cost_rub is not None:
            generation.cost_rub = cost_rub
        if transaction_id is not None:
            generation.transaction_id = transaction_id

        await self.session.commit()

        # Вычисляем длительность генерации
        # Нормализуем timezone: created_at может быть offset-naive
        # (от SQLite/PostgreSQL), а completed_at — offset-aware (datetime.now(UTC))
        created = generation.created_at
        completed = generation.completed_at
        if created.tzinfo is None and completed.tzinfo is not None:
            # created_at без timezone — предполагаем UTC
            created = created.replace(tzinfo=UTC)
        elif created.tzinfo is not None and completed.tzinfo is None:
            # completed_at без timezone — предполагаем UTC
            completed = completed.replace(tzinfo=UTC)

        duration = (completed - created).total_seconds()
        logger.debug(
            "Статус обновлён: id=%d, status=%s, duration=%.2fs, "
            "tokens=%s, cost_rub=%s, tx_id=%s",
            generation_id,
            status,
            duration,
            tokens_charged,
            cost_rub,
            transaction_id,
        )

    async def get_last_generation(
        self,
        user_id: int,
        generation_type: str,
    ) -> Generation | None:
        """Получить последнюю генерацию пользователя определённого типа.

        Используется для проверки cooldown: нужно узнать, когда была
        последняя генерация этого типа, чтобы проверить, прошло ли
        достаточно времени.

        Args:
            user_id: ID пользователя.
            generation_type: Тип генерации (chat, image, etc.).

        Returns:
            Последняя генерация или None, если генераций ещё не было.

        Example:
            last_gen = await repo.get_last_generation(user_id, "image")
            if last_gen:
                elapsed = (datetime.now(UTC) - last_gen.created_at).total_seconds()
                if elapsed < cooldown_seconds:
                    raise CooldownError(...)
        """
        stmt = (
            select(Generation)
            .where(
                Generation.user_id == user_id,
                Generation.generation_type == generation_type,
            )
            .order_by(desc(Generation.created_at))
            .limit(1)
        )

        result = await self.session.execute(stmt)
        generation = result.scalar_one_or_none()

        if generation:
            # Преобразуем created_at в timezone-aware для корректного вычисления разницы
            # SQLite хранит datetime без timezone, поэтому добавляем UTC
            created_at_aware = generation.created_at.replace(tzinfo=UTC)
            logger.debug(
                "Последняя генерация найдена: user_id=%d, type=%s, создана=%s назад",
                user_id,
                generation_type,
                datetime.now(UTC) - created_at_aware,
            )
        else:
            logger.debug(
                "Генераций не найдено: user_id=%d, type=%s",
                user_id,
                generation_type,
            )

        return generation

    async def count_pending_generations(
        self,
        user_id: int,
    ) -> int:
        """Подсчитать количество активных генераций пользователя.

        Используется для проверки лимита параллельных задач.
        Считает только генерации со статусом PENDING.

        Args:
            user_id: ID пользователя.

        Returns:
            Количество активных генераций.

        Example:
            pending_count = await repo.count_pending_generations(user_id)
            if pending_count >= max_parallel_tasks:
                raise TooManyGenerationsError(...)
        """
        stmt = (
            select(func.count())
            .select_from(Generation)
            .where(
                Generation.user_id == user_id,
                Generation.status == GenerationDBStatus.PENDING,
            )
        )

        result = await self.session.execute(stmt)
        count = result.scalar_one()

        logger.debug(
            "Активных генераций: user_id=%d, count=%d",
            user_id,
            count,
        )

        return count

    async def get_user_generations(
        self,
        user_id: int,
        limit: int = 10,
    ) -> list[Generation]:
        """Получить последние генерации пользователя.

        Используется для аналитики и отображения истории.
        Возвращает последние N генераций, отсортированные от новых к старым.

        Args:
            user_id: ID пользователя.
            limit: Максимальное количество генераций.

        Returns:
            Список генераций (от новых к старым).

        Example:
            recent = await repo.get_user_generations(user_id, limit=20)
            for gen in recent:
                print(f"{gen.generation_type}: {gen.model_key} - {gen.status}")
        """
        stmt = (
            select(Generation)
            .where(Generation.user_id == user_id)
            .order_by(desc(Generation.created_at))
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        generations = list(result.scalars().all())

        logger.debug(
            "История загружена: user_id=%d, найдено=%d",
            user_id,
            len(generations),
        )

        return generations

    async def cleanup_stuck_generations(
        self,
        timeout_seconds: int,
    ) -> int:
        """Пометить зависшие генерации как FAILED.

        Находит генерации со статусом PENDING, которые были созданы больше
        timeout_seconds назад, и помечает их как FAILED.

        Это критически важно для очистки "утечек" генераций при:
        - Крешах сервера во время обработки
        - Таймаутах запросов к AI-провайдерам
        - Неожиданных завершениях процессов

        Args:
            timeout_seconds: Максимальное время генерации в секундах.
                Генерации старше этого времени считаются зависшими.

        Returns:
            Количество очищенных генераций.

        Example:
            # При startup очищаем генерации старше 10 минут
            cleaned = await repo.cleanup_stuck_generations(timeout_seconds=600)
            logger.info(f"Очищено зависших генераций: {cleaned}")
        """
        # Вычисляем пороговое время: now - timeout
        threshold_time = datetime.now(UTC) - timedelta(seconds=timeout_seconds)

        # Находим все PENDING генерации старше порогового времени
        stmt = select(Generation).where(
            Generation.status == GenerationDBStatus.PENDING,
            Generation.created_at < threshold_time,
        )

        result = await self.session.execute(stmt)
        stuck_generations = list(result.scalars().all())

        if not stuck_generations:
            logger.debug("Зависших генераций не найдено")
            return 0

        # Помечаем все как FAILED
        now = datetime.now(UTC)
        for generation in stuck_generations:
            generation.status = GenerationDBStatus.FAILED
            generation.completed_at = now

        await self.session.commit()

        count = len(stuck_generations)
        logger.info(
            "Очищено зависших генераций: %d (старше %d сек)",
            count,
            timeout_seconds,
        )

        return count
