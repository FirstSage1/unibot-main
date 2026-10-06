"""Сервис рассылок (массовая отправка сообщений).

Этот модуль реализует логику массовых рассылок пользователям:
- Создание и управление рассылками
- Сегментация получателей
- Отправка сообщений с rate limiting
- Обработка ошибок и FloodWait
- Сохранение прогресса для возобновления

Архитектура:
- BroadcastService — бизнес-логика (CRUD, подсчёт получателей, preview)
- BroadcastWorker — фоновая отправка с rate limiting (запускается отдельно)

Жизненный цикл рассылки:
1. Администратор создаёт рассылку в админке (статус DRAFT)
2. Администратор настраивает сегментацию и текст
3. Администратор делает предпросмотр (preview_message)
4. Администратор запускает рассылку (статус RUNNING)
5. BroadcastWorker отправляет сообщения с rate limiting
6. Прогресс сохраняется после каждого батча
7. При завершении статус меняется на COMPLETED

Пример использования:
    async with session_factory() as session:
        service = create_broadcast_service(session)

        # Создать рассылку
        broadcast = await service.create_broadcast(
            name="Новогодняя акция",
            message_text="<b>Привет!</b> У нас скидки!",
            filter_language="ru",
        )

        # Подсчитать получателей
        count = await service.count_recipients(broadcast)

        # Запустить
        await service.start_broadcast(broadcast)
"""

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import BroadcastConfig, YamlConfig
from src.db.models.broadcast import Broadcast, BroadcastStatus, ParseMode
from src.db.models.user import User
from src.db.repositories.broadcast_repo import BroadcastRepository
from src.db.repositories.user_repo import UserRepository
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager as AsyncContextManager

logger = get_logger(__name__)

# =============================================================================
# КОНСТАНТЫ
# =============================================================================

# Интервал между итерациями воркера рассылок (секунды)
WORKER_LOOP_INTERVAL_SECONDS: float = 5.0

# Задержка перед повторной попыткой отправки при ошибке (секунды)
RETRY_BACKOFF_SECONDS: float = 1.0


# =============================================================================
# ПРОТОКОЛЫ И DATA CLASSES
# =============================================================================


class BotProtocol(Protocol):
    """Протокол для Telegram бота (для DI и тестирования)."""

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
        **kwargs: object,
    ) -> object:
        """Отправить сообщение."""
        ...


@dataclass
class BroadcastPreview:
    """Результат предпросмотра рассылки.

    Содержит информацию для отображения администратору перед запуском.

    Attributes:
        broadcast: Объект рассылки.
        total_recipients: Количество получателей по критериям сегментации.
        sample_message: Отформатированный текст сообщения для preview.
        filters_description: Человекочитаемое описание фильтров.
    """

    broadcast: Broadcast
    total_recipients: int
    sample_message: str
    filters_description: str


@dataclass
class SendResult:
    """Результат отправки одного сообщения.

    Attributes:
        success: Успешно ли отправлено.
        user_id: ID пользователя (внутренний).
        telegram_id: Telegram ID пользователя.
        error: Текст ошибки (если есть).
        retry_after: Время ожидания при FloodWait (секунды).
    """

    success: bool
    user_id: int
    telegram_id: int
    error: str | None = None
    retry_after: int | None = None


@dataclass
class BatchResult:
    """Результат отправки батча сообщений.

    Attributes:
        sent_count: Количество успешно отправленных.
        failed_count: Количество неудачных.
        last_user_id: ID последнего обработанного пользователя.
        flood_wait_seconds: Время ожидания FloodWait (если был).
    """

    sent_count: int
    failed_count: int
    last_user_id: int | None
    flood_wait_seconds: int | None = None


# =============================================================================
# СЕРВИС РАССЫЛОК
# =============================================================================


class BroadcastService:
    """Сервис для управления рассылками.

    Использует Dependency Injection — все зависимости передаются в конструктор.
    Это позволяет легко тестировать код без реальной БД и Telegram API.

    Основные методы:
    - create_broadcast(): Создать новую рассылку
    - count_recipients(): Подсчитать получателей
    - preview_broadcast(): Предпросмотр перед отправкой
    - start_broadcast(): Запустить рассылку
    - pause_broadcast(): Приостановить
    - cancel_broadcast(): Отменить

    Attributes:
        _session: Асинхронная сессия SQLAlchemy.
        _config: Конфигурация рассылок из yaml_config.
        _broadcast_repo: Репозиторий для работы с рассылками.
        _user_repo: Репозиторий для работы с пользователями.
    """

    def __init__(
        self,
        session: AsyncSession,
        yaml_config: YamlConfig,
    ) -> None:
        """Инициализировать сервис рассылок.

        Args:
            session: Асинхронная сессия SQLAlchemy.
            yaml_config: Полная YAML-конфигурация (для валидации и broadcast настроек).
        """
        self._session = session
        self._yaml_config = yaml_config
        self._config = yaml_config.broadcast
        self._broadcast_repo = BroadcastRepository(session)
        self._user_repo = UserRepository(session)

    async def create_broadcast(
        self,
        name: str,
        message_text: str,
        parse_mode: str = ParseMode.HTML,
        created_by_id: int | None = None,
        filter_language: str | None = None,
        filter_has_payments: bool | None = None,
        filter_source: str | None = None,
        filter_registered_after: datetime | None = None,
        filter_registered_before: datetime | None = None,
        filter_exclude_blocked: bool = True,
    ) -> Broadcast:
        """Создать новую рассылку.

        Рассылка создаётся в статусе DRAFT. После создания можно:
        - Изменить параметры
        - Посмотреть предпросмотр (preview_broadcast)
        - Запустить (start_broadcast)

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

        Raises:
            ValueError: Если фильтры невалидны.
        """
        # Валидация фильтров для защиты от некорректных данных
        if filter_language is not None:
            available_languages = self._yaml_config.localization.available_languages
            if filter_language not in available_languages:
                raise ValueError(
                    f"Invalid language filter: {filter_language}. "
                    f"Available: {', '.join(available_languages)}"
                )

        if (
            filter_registered_after is not None
            and filter_registered_before is not None
            and filter_registered_after >= filter_registered_before
        ):
            raise ValueError(
                "filter_registered_after must be before filter_registered_before. "
                f"Got: after={filter_registered_after}, "
                f"before={filter_registered_before}"
            )

        if filter_source is not None and len(filter_source) > 255:
            raise ValueError(
                f"filter_source exceeds maximum length of 255 characters. "
                f"Got: {len(filter_source)} characters"
            )

        # Валидация message_text
        if len(message_text) > 4096:
            raise ValueError(
                f"message_text exceeds Telegram limit of 4096 characters. "
                f"Got: {len(message_text)} characters"
            )

        broadcast = await self._broadcast_repo.create(
            name=name,
            message_text=message_text,
            parse_mode=parse_mode,
            created_by_id=created_by_id,
            filter_language=filter_language,
            filter_has_payments=filter_has_payments,
            filter_source=filter_source,
            filter_registered_after=filter_registered_after,
            filter_registered_before=filter_registered_before,
            filter_exclude_blocked=filter_exclude_blocked,
        )

        logger.info(
            "Создана рассылка: id=%d, name=%s, created_by=%s",
            broadcast.id,
            broadcast.name,
            created_by_id,
        )

        return broadcast

    async def count_recipients(self, broadcast: Broadcast) -> int:
        """Подсчитать количество получателей рассылки.

        Учитывает все фильтры сегментации из рассылки.
        Используется для отображения количества получателей перед запуском.

        Args:
            broadcast: Рассылка для подсчёта.

        Returns:
            Количество пользователей, соответствующих критериям.
        """
        return await self._user_repo.count_by_segment(
            language=broadcast.filter_language,
            has_payments=broadcast.filter_has_payments,
            source=broadcast.filter_source,
            registered_after=broadcast.filter_registered_after,
            registered_before=broadcast.filter_registered_before,
            exclude_blocked=broadcast.filter_exclude_blocked,
            after_user_id=broadcast.last_processed_user_id,
        )

    async def preview_broadcast(self, broadcast: Broadcast) -> BroadcastPreview:
        """Получить предпросмотр рассылки.

        Возвращает информацию для отображения администратору:
        - Количество получателей
        - Текст сообщения
        - Описание фильтров

        Args:
            broadcast: Рассылка для предпросмотра.

        Returns:
            BroadcastPreview с информацией для отображения.
        """
        total = await self.count_recipients(broadcast)
        filters = self._describe_filters(broadcast)

        return BroadcastPreview(
            broadcast=broadcast,
            total_recipients=total,
            sample_message=broadcast.message_text,
            filters_description=filters,
        )

    async def start_broadcast(self, broadcast: Broadcast) -> Broadcast:
        """Запустить рассылку.

        Меняет статус на RUNNING и фиксирует количество получателей.
        После этого BroadcastWorker начнёт отправку сообщений.

        Args:
            broadcast: Рассылка для запуска.

        Returns:
            Обновлённая рассылка в статусе RUNNING.

        Raises:
            ValueError: Если рассылка не может быть запущена (неверный статус).
        """
        total = await self.count_recipients(broadcast)

        broadcast = await self._broadcast_repo.start(broadcast, total)

        logger.info(
            "Рассылка запущена: id=%d, name=%s, recipients=%d",
            broadcast.id,
            broadcast.name,
            total,
        )

        return broadcast

    async def pause_broadcast(self, broadcast: Broadcast) -> Broadcast:
        """Приостановить рассылку.

        Можно возобновить позже с места остановки.

        Args:
            broadcast: Рассылка для приостановки.

        Returns:
            Обновлённая рассылка в статусе PAUSED.
        """
        broadcast = await self._broadcast_repo.pause(broadcast)

        logger.info(
            "Рассылка приостановлена: id=%d, name=%s, progress=%d/%d",
            broadcast.id,
            broadcast.name,
            broadcast.sent_count + broadcast.failed_count,
            broadcast.total_recipients,
        )

        return broadcast

    async def cancel_broadcast(self, broadcast: Broadcast) -> Broadcast:
        """Отменить рассылку.

        Отменённую рассылку нельзя возобновить.

        Args:
            broadcast: Рассылка для отмены.

        Returns:
            Обновлённая рассылка в статусе CANCELLED.
        """
        broadcast = await self._broadcast_repo.cancel(broadcast)

        logger.info(
            "Рассылка отменена: id=%d, name=%s, sent=%d, failed=%d",
            broadcast.id,
            broadcast.name,
            broadcast.sent_count,
            broadcast.failed_count,
        )

        return broadcast

    async def get_broadcast(self, broadcast_id: int) -> Broadcast | None:
        """Получить рассылку по ID.

        Args:
            broadcast_id: ID рассылки.

        Returns:
            Broadcast или None если не найдена.
        """
        return await self._broadcast_repo.get_by_id(broadcast_id)

    async def get_active_broadcasts(self) -> list[Broadcast]:
        """Получить список активных рассылок.

        Активные рассылки — PENDING или RUNNING.
        Используется BroadcastWorker для получения рассылок на обработку.

        Returns:
            Список активных рассылок.
        """
        return await self._broadcast_repo.get_active_broadcasts()

    async def get_all_broadcasts(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Broadcast]:
        """Получить список всех рассылок.

        Используется в админке для отображения истории.

        Args:
            limit: Максимальное количество.
            offset: Смещение (для пагинации).

        Returns:
            Список рассылок (новые первыми).
        """
        return await self._broadcast_repo.get_all(limit=limit, offset=offset)

    def _describe_filters(self, broadcast: Broadcast) -> str:
        """Сформировать человекочитаемое описание фильтров.

        Args:
            broadcast: Рассылка с фильтрами.

        Returns:
            Строка с описанием активных фильтров.
        """
        filters: list[str] = []

        if broadcast.filter_language:
            filters.append(f"Язык: {broadcast.filter_language}")

        if broadcast.filter_has_payments is not None:
            if broadcast.filter_has_payments:
                filters.append("Только платившие")
            else:
                filters.append("Только бесплатные")

        if broadcast.filter_source:
            filters.append(f"Источник: {broadcast.filter_source}")

        if broadcast.filter_registered_after:
            date_str = broadcast.filter_registered_after.strftime("%d.%m.%Y")
            filters.append(f"Зарегистрированы после: {date_str}")

        if broadcast.filter_registered_before:
            date_str = broadcast.filter_registered_before.strftime("%d.%m.%Y")
            filters.append(f"Зарегистрированы до: {date_str}")

        if broadcast.filter_exclude_blocked:
            filters.append("Исключить заблокированных")

        if not filters:
            return "Без фильтров (все пользователи)"

        return "; ".join(filters)


# =============================================================================
# ВОРКЕР РАССЫЛОК
# =============================================================================


class BroadcastWorker:
    """Воркер для фоновой отправки рассылок.

    Выполняет отправку сообщений с соблюдением rate limiting
    и обработкой ошибок Telegram API.

    Особенности:
    - Rate limiting (messages_per_second из конфига)
    - Обработка FloodWait (ожидание retry_after)
    - Сохранение прогресса после каждого батча
    - Graceful shutdown через stop()

    Использование:
        worker = BroadcastWorker(bot, session_factory, config)
        await worker.start()  # Запустить в фоне
        ...
        await worker.stop()   # Остановить
    """

    def __init__(
        self,
        bot: Bot,
        session_factory: "Callable[[], AsyncContextManager[AsyncSession]]",
        config: BroadcastConfig,
    ) -> None:
        """Инициализировать воркер.

        Args:
            bot: Telegram бот для отправки сообщений.
            session_factory: Фабрика для создания сессий БД
                (возвращает context manager).
            config: Конфигурация рассылок.
        """
        self._bot = bot
        self._session_factory = session_factory
        self._config = config
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Интервал между сообщениями (секунды)
        self._send_interval = 1.0 / config.messages_per_second

    async def start(self) -> None:
        """Запустить воркер в фоновом режиме.

        Воркер будет периодически проверять наличие активных рассылок
        и обрабатывать их.
        """
        if self._running:
            logger.warning("BroadcastWorker уже запущен")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("BroadcastWorker запущен")

    async def stop(self) -> None:
        """Остановить воркер.

        Дожидается завершения текущего батча и останавливается.
        Прогресс сохраняется, рассылку можно возобновить позже.
        """
        if not self._running:
            return

        self._running = False

        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        logger.info("BroadcastWorker остановлен")

    async def _run_loop(self) -> None:
        """Основной цикл обработки рассылок."""
        while self._running:
            try:
                await self._process_broadcasts()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Ошибка в BroadcastWorker")

            # Пауза между итерациями
            await asyncio.sleep(WORKER_LOOP_INTERVAL_SECONDS)

    async def _process_broadcasts(self) -> None:
        """Обработать все активные рассылки."""
        async with self._session_factory() as session:
            repo = BroadcastRepository(session)
            user_repo = UserRepository(session)

            broadcasts = await repo.get_active_broadcasts()

            for broadcast in broadcasts:
                if not self._running:
                    break

                await self._process_broadcast(broadcast, repo, user_repo)

    async def _process_broadcast(
        self,
        broadcast: Broadcast,
        repo: BroadcastRepository,
        user_repo: UserRepository,
    ) -> None:
        """Обработать одну рассылку.

        Отправляет батч сообщений и обновляет прогресс.
        """
        # Меняем статус на RUNNING если был PENDING
        if broadcast.status == BroadcastStatus.PENDING:
            await repo.set_status(broadcast.id, BroadcastStatus.RUNNING)

        # Получаем получателей для батча
        batch = await user_repo.get_by_segment(
            language=broadcast.filter_language,
            has_payments=broadcast.filter_has_payments,
            source=broadcast.filter_source,
            registered_after=broadcast.filter_registered_after,
            registered_before=broadcast.filter_registered_before,
            exclude_blocked=broadcast.filter_exclude_blocked,
            after_user_id=broadcast.last_processed_user_id,
            limit=self._config.batch_size,
        )

        if not batch:
            # Все сообщения отправлены
            await repo.complete(broadcast)
            logger.info(
                "Рассылка завершена: id=%d, sent=%d, failed=%d",
                broadcast.id,
                broadcast.sent_count,
                broadcast.failed_count,
            )
            return

        # Отправляем батч
        result = await self._send_batch(broadcast, batch)

        # Обновляем прогресс атомарно (используем инкременты для безопасности)
        last_user = result.last_user_id or broadcast.last_processed_user_id or 0
        await repo.increment_progress(
            broadcast_id=broadcast.id,
            sent_delta=result.sent_count,
            failed_delta=result.failed_count,
            last_processed_user_id=last_user,
        )

        # Если был FloodWait — ждём
        if result.flood_wait_seconds:
            wait_time = result.flood_wait_seconds * self._config.flood_wait_multiplier
            logger.warning(
                "FloodWait: ждём %.1f секунд (broadcast_id=%d)",
                wait_time,
                broadcast.id,
            )
            await asyncio.sleep(wait_time)

    async def _send_batch(
        self,
        broadcast: Broadcast,
        users: list[User],
    ) -> BatchResult:
        """Отправить батч сообщений.

        Args:
            broadcast: Рассылка.
            users: Список пользователей для отправки.

        Returns:
            BatchResult с результатами отправки.
        """
        sent = 0
        failed = 0
        last_user_id: int | None = None
        flood_wait: int | None = None

        if broadcast.parse_mode != ParseMode.NONE:
            parse_mode = broadcast.parse_mode
        else:
            parse_mode = None

        for user in users:
            if not self._running:
                break

            result = await self._send_message(
                user=user,
                text=broadcast.message_text,
                parse_mode=parse_mode,
            )

            last_user_id = user.id

            if result.success:
                sent += 1
            else:
                failed += 1

                if result.retry_after:
                    flood_wait = result.retry_after
                    break

            # Rate limiting
            await asyncio.sleep(self._send_interval)

        return BatchResult(
            sent_count=sent,
            failed_count=failed,
            last_user_id=last_user_id,
            flood_wait_seconds=flood_wait,
        )

    async def _send_message(
        self,
        user: User,
        text: str,
        parse_mode: str | None,
    ) -> SendResult:
        """Отправить сообщение одному пользователю.

        Args:
            user: Пользователь-получатель.
            text: Текст сообщения.
            parse_mode: Режим форматирования.

        Returns:
            SendResult с результатом отправки.
        """
        for attempt in range(self._config.retry_on_error + 1):
            try:
                await self._bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode=parse_mode,
                )

                return SendResult(
                    success=True,
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                )

            except TelegramRetryAfter as e:
                # FloodWait — нужно подождать
                logger.warning(
                    "FloodWait при отправке пользователю %d: retry_after=%d",
                    user.telegram_id,
                    e.retry_after,
                )
                return SendResult(
                    success=False,
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    error=f"FloodWait: {e.retry_after}s",
                    retry_after=e.retry_after,
                )

            except TelegramForbiddenError:
                # Пользователь заблокировал бота — не ошибка, просто пропускаем
                logger.debug(
                    "Пользователь %d заблокировал бота",
                    user.telegram_id,
                )
                return SendResult(
                    success=False,
                    user_id=user.id,
                    telegram_id=user.telegram_id,
                    error="Пользователь заблокировал бота",
                )

            except (OSError, TimeoutError, RuntimeError) as e:
                # Другие ошибки — пробуем повторить
                if attempt < self._config.retry_on_error:
                    logger.warning(
                        "Ошибка отправки пользователю %d (попытка %d/%d): %s",
                        user.telegram_id,
                        attempt + 1,
                        self._config.retry_on_error + 1,
                        e,
                    )
                    await asyncio.sleep(RETRY_BACKOFF_SECONDS)
                else:
                    logger.error(
                        "Не удалось отправить сообщение пользователю %d: %s",
                        user.telegram_id,
                        e,
                    )
                    return SendResult(
                        success=False,
                        user_id=user.id,
                        telegram_id=user.telegram_id,
                        error=str(e),
                    )

        # Не должны сюда дойти, но на всякий случай
        return SendResult(
            success=False,
            user_id=user.id,
            telegram_id=user.telegram_id,
            error="Неизвестная ошибка",
        )


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================


def create_broadcast_service(
    session: AsyncSession,
    yaml_config: YamlConfig | None = None,
) -> BroadcastService:
    """Создать экземпляр BroadcastService (factory function).

    Это основной способ создания BroadcastService в production коде.
    Использует глобальный yaml_config если не передан явно.

    Args:
        session: Асинхронная сессия SQLAlchemy.
        yaml_config: YAML-конфигурация (опционально, берётся из глобальной).

    Returns:
        Настроенный экземпляр BroadcastService.

    Example:
        async with DatabaseSession() as session:
            service = create_broadcast_service(session)
            preview = await service.preview_broadcast(broadcast)
    """
    if yaml_config is None:
        from src.config.yaml_config import yaml_config as global_yaml_config

        yaml_config = global_yaml_config

    return BroadcastService(
        session=session,
        yaml_config=yaml_config,
    )


def create_broadcast_worker(
    bot: Bot,
    session_factory: "Callable[[], AsyncSession]",
    yaml_config: YamlConfig | None = None,
) -> BroadcastWorker:
    """Создать экземпляр BroadcastWorker (factory function).

    Args:
        bot: Telegram бот для отправки сообщений.
        session_factory: Фабрика для создания сессий БД.
        yaml_config: YAML-конфигурация (опционально, берётся из глобальной).

    Returns:
        Настроенный экземпляр BroadcastWorker.

    Example:
        worker = create_broadcast_worker(bot, session_factory)
        await worker.start()  # Запустить в фоне
    """
    if yaml_config is None:
        from src.config.yaml_config import yaml_config as global_yaml_config

        yaml_config = global_yaml_config

    return BroadcastWorker(
        bot=bot,
        session_factory=session_factory,
        config=yaml_config.broadcast,
    )
