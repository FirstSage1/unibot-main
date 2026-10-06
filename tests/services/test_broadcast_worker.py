"""Тесты для BroadcastWorker.

Модуль тестирует:
- Запуск и остановку воркера (start, stop)
- Обработку батча сообщений (_send_batch)
- Отправку отдельного сообщения (_send_message)
- Обработку FloodWait (TelegramRetryAfter)
- Обработку блокировки бота (TelegramForbiddenError)
- Retry логику при сетевых ошибках
- Rate limiting (задержка между сообщениями)
- Обновление прогресса после батча

Тестируемая функциональность:
1. Воркер корректно обрабатывает активные рассылки
2. FloodWait вызывает паузу с умножением времени
3. Блокировка бота не останавливает рассылку
4. Сетевые ошибки повторяются retry_on_error раз
5. Прогресс сохраняется после каждого батча
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import BroadcastConfig
from src.db.models.broadcast import Broadcast, BroadcastStatus, ParseMode
from src.db.models.user import User
from src.services.broadcast_service import (
    BroadcastWorker,
)

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def broadcast_config() -> BroadcastConfig:
    """Создать тестовую конфигурацию рассылок."""
    return BroadcastConfig(
        enabled=True,
        messages_per_second=10,  # Для быстрых тестов
        batch_size=10,  # Минимальное допустимое значение
        retry_on_error=2,
        flood_wait_multiplier=1.5,
    )


@pytest.fixture
def mock_bot() -> AsyncMock:
    """Создать мок Telegram бота."""
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.fixture
def mock_session_factory(
    db_session: AsyncSession,
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """Создать мок фабрики сессий."""

    @asynccontextmanager
    async def _factory() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    return _factory


@pytest.fixture
async def test_users(db_session: AsyncSession) -> list[User]:
    """Создать тестовых пользователей."""
    users = [
        User(telegram_id=1001, language="ru", is_blocked=False),
        User(telegram_id=1002, language="ru", is_blocked=False),
        User(telegram_id=1003, language="ru", is_blocked=False),
    ]
    for user in users:
        db_session.add(user)
    await db_session.commit()
    for user in users:
        await db_session.refresh(user)
    return users


@pytest.fixture
async def pending_broadcast(
    db_session: AsyncSession,
    test_users: list[User],
) -> Broadcast:
    """Создать рассылку в статусе PENDING."""
    broadcast = Broadcast(
        name="Тестовая рассылка",
        message_text="<b>Привет!</b> Тестовое сообщение.",
        parse_mode=ParseMode.HTML,
        status=BroadcastStatus.PENDING,
        total_recipients=len(test_users),
    )
    db_session.add(broadcast)
    await db_session.commit()
    await db_session.refresh(broadcast)
    return broadcast


# ==============================================================================
# ТЕСТЫ ЗАПУСКА И ОСТАНОВКИ ВОРКЕРА
# ==============================================================================


@pytest.mark.asyncio
async def test_worker_start_creates_background_task(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
) -> None:
    """Тест: запуск воркера создаёт фоновую задачу."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)

    await worker.start()

    assert worker._running is True
    assert worker._task is not None


@pytest.mark.asyncio
async def test_worker_stop_cancels_task(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
) -> None:
    """Тест: остановка воркера отменяет задачу."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)

    await worker.start()
    await worker.stop()

    assert worker._running is False


@pytest.mark.asyncio
async def test_worker_start_twice_does_nothing(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
) -> None:
    """Тест: повторный запуск не создаёт новую задачу."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)

    await worker.start()
    first_task = worker._task

    await worker.start()

    assert worker._task is first_task


# ==============================================================================
# ТЕСТЫ ОТПРАВКИ СООБЩЕНИЯ
# ==============================================================================


@pytest.mark.asyncio
async def test_send_message_success(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
) -> None:
    """Тест: успешная отправка сообщения."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    user = test_users[0]

    result = await worker._send_message(user, "Текст", "HTML")

    assert result.success is True
    assert result.user_id == user.id
    assert result.telegram_id == user.telegram_id
    mock_bot.send_message.assert_called_once_with(
        chat_id=user.telegram_id,
        text="Текст",
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_send_message_handles_flood_wait(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
) -> None:
    """Тест: обработка FloodWait."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    user = test_users[0]

    # Имитируем FloodWait с retry_after=30
    mock_bot.send_message.side_effect = TelegramRetryAfter(
        method=Mock(),
        retry_after=30,
        message="Flood wait",
    )

    result = await worker._send_message(user, "Текст", "HTML")

    assert result.success is False
    assert result.retry_after == 30
    assert result.error is not None
    assert "FloodWait" in result.error


@pytest.mark.asyncio
async def test_send_message_handles_forbidden_error(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
) -> None:
    """Тест: обработка блокировки бота пользователем."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    user = test_users[0]

    mock_bot.send_message.side_effect = TelegramForbiddenError(
        method=Mock(),
        message="Bot was blocked",
    )

    result = await worker._send_message(user, "Текст", "HTML")

    assert result.success is False
    assert result.error is not None
    assert "заблокировал бота" in result.error


@pytest.mark.asyncio
async def test_send_message_retries_on_network_error(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
) -> None:
    """Тест: повторные попытки при сетевых ошибках."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    user = test_users[0]

    # Первые 2 попытки — ошибка, третья — успех
    mock_bot.send_message.side_effect = [
        TimeoutError("Network error"),
        TimeoutError("Network error"),
        None,  # Успех
    ]

    result = await worker._send_message(user, "Текст", "HTML")

    # Должно быть 3 попытки (retry_on_error=2 → всего 3 попытки)
    assert mock_bot.send_message.call_count == 3
    assert result.success is True


@pytest.mark.asyncio
async def test_send_message_fails_after_max_retries(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
) -> None:
    """Тест: отказ после исчерпания попыток."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    user = test_users[0]

    # Все попытки — ошибка
    mock_bot.send_message.side_effect = TimeoutError("Network error")

    result = await worker._send_message(user, "Текст", "HTML")

    # retry_on_error=2 → всего 3 попытки
    assert mock_bot.send_message.call_count == 3
    assert result.success is False


# ==============================================================================
# ТЕСТЫ ОТПРАВКИ БАТЧА
# ==============================================================================


@pytest.mark.asyncio
async def test_send_batch_sends_all_messages(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: отправка батча отправляет все сообщения."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    # Устанавливаем флаг running чтобы батч обрабатывался
    worker._running = True

    result = await worker._send_batch(pending_broadcast, test_users)

    assert result.sent_count == len(test_users)
    assert result.failed_count == 0
    assert mock_bot.send_message.call_count == len(test_users)


@pytest.mark.asyncio
async def test_send_batch_counts_failures(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: батч подсчитывает неудачи."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    worker._running = True

    # Первое сообщение успешно, остальные — блокировка
    mock_bot.send_message.side_effect = [
        None,  # Успех
        TelegramForbiddenError(method=Mock(), message="Blocked"),
        TelegramForbiddenError(method=Mock(), message="Blocked"),
    ]

    result = await worker._send_batch(pending_broadcast, test_users)

    assert result.sent_count == 1
    assert result.failed_count == 2


@pytest.mark.asyncio
async def test_send_batch_stops_on_flood_wait(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: батч останавливается при FloodWait."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    worker._running = True

    # Второе сообщение вызывает FloodWait
    mock_bot.send_message.side_effect = [
        None,  # Успех
        TelegramRetryAfter(method=Mock(), retry_after=30, message="Flood"),
    ]

    result = await worker._send_batch(pending_broadcast, test_users)

    # Должно отправиться только 2 сообщения
    assert mock_bot.send_message.call_count == 2
    assert result.flood_wait_seconds == 30


@pytest.mark.asyncio
async def test_send_batch_updates_last_user_id(
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: батч обновляет last_user_id."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    worker._running = True

    result = await worker._send_batch(pending_broadcast, test_users)

    # Последний обработанный ID = ID последнего пользователя
    assert result.last_user_id == test_users[-1].id


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_send_batch_respects_rate_limit(
    mock_sleep: AsyncMock,
    mock_bot: AsyncMock,
    mock_session_factory: Mock,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: батч соблюдает rate limiting."""
    worker = BroadcastWorker(mock_bot, mock_session_factory, broadcast_config)
    worker._running = True

    await worker._send_batch(pending_broadcast, test_users)

    # Должна быть задержка после каждого сообщения
    # messages_per_second=10 → интервал = 0.1 секунды
    expected_interval = 1.0 / broadcast_config.messages_per_second
    assert mock_sleep.call_count == len(test_users)
    for call_args in mock_sleep.call_args_list:
        assert call_args[0][0] == pytest.approx(expected_interval)


# ==============================================================================
# ТЕСТЫ ОБРАБОТКИ РАССЫЛКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_process_broadcast_changes_pending_to_running(
    mock_bot: AsyncMock,
    db_session: AsyncSession,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: обработка меняет статус PENDING → RUNNING."""

    @asynccontextmanager
    async def session_factory() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    worker = BroadcastWorker(mock_bot, session_factory, broadcast_config)
    worker._running = True

    await worker._process_broadcasts()

    await db_session.refresh(pending_broadcast)
    assert pending_broadcast.status == BroadcastStatus.RUNNING


@pytest.mark.asyncio
async def test_process_broadcast_updates_progress(
    mock_bot: AsyncMock,
    db_session: AsyncSession,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: обработка обновляет прогресс."""

    @asynccontextmanager
    async def session_factory() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    worker = BroadcastWorker(mock_bot, session_factory, broadcast_config)
    worker._running = True

    await worker._process_broadcasts()

    await db_session.refresh(pending_broadcast)
    # Все сообщения должны быть отправлены
    assert pending_broadcast.sent_count == len(test_users)
    assert pending_broadcast.last_processed_user_id == test_users[-1].id


@pytest.mark.asyncio
async def test_process_broadcast_completes_when_no_users_left(
    mock_bot: AsyncMock,
    db_session: AsyncSession,
    broadcast_config: BroadcastConfig,
    pending_broadcast: Broadcast,
) -> None:
    """Тест: рассылка завершается когда больше нет пользователей."""

    @asynccontextmanager
    async def session_factory() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    worker = BroadcastWorker(mock_bot, session_factory, broadcast_config)
    worker._running = True

    # Все пользователи уже обработаны
    pending_broadcast.last_processed_user_id = 99999
    await db_session.commit()

    await worker._process_broadcasts()

    await db_session.refresh(pending_broadcast)
    assert pending_broadcast.status == BroadcastStatus.COMPLETED


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_process_broadcast_waits_on_flood_wait(
    mock_sleep: AsyncMock,
    mock_bot: AsyncMock,
    db_session: AsyncSession,
    broadcast_config: BroadcastConfig,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: воркер ждёт при FloodWait."""

    @asynccontextmanager
    async def session_factory() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    worker = BroadcastWorker(mock_bot, session_factory, broadcast_config)
    worker._running = True

    # Первое сообщение вызывает FloodWait
    mock_bot.send_message.side_effect = [
        TelegramRetryAfter(method=Mock(), retry_after=10, message="Flood"),
    ]

    await worker._process_broadcasts()

    # Должна быть пауза с умножением на flood_wait_multiplier
    expected_wait = 10 * broadcast_config.flood_wait_multiplier
    # Находим вызов sleep с ожиданием FloodWait (исключая rate limit sleeps)
    flood_wait_calls = [
        call_args[0][0]
        for call_args in mock_sleep.call_args_list
        if call_args[0][0] > 1.0
    ]
    assert len(flood_wait_calls) >= 1
    assert flood_wait_calls[0] == pytest.approx(expected_wait)
