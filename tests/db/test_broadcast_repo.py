"""Тесты для BroadcastRepository.

Модуль тестирует:
- Создание рассылки (create)
- Получение рассылки по ID (get_by_id)
- Получение активных рассылок (get_active_broadcasts)
- Запуск рассылки (start)
- Приостановка рассылки (pause)
- Отмена рассылки (cancel)
- Завершение рассылки (complete)
- Маркировка как неудачной (fail)
- Обновление прогресса (update_progress, increment_progress)
- Смена статуса (set_status)

Тестируемая функциональность:
1. CRUD операции работают корректно
2. Валидация статусов при переходах (can_start, can_pause, can_cancel)
3. Атомарные обновления прогресса
4. Фильтрация по статусам
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.broadcast import Broadcast, BroadcastStatus, ParseMode
from src.db.models.user import User
from src.db.repositories.broadcast_repo import BroadcastRepository

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """Создать администратора для тестирования рассылок."""
    admin = User(
        telegram_id=987654321,
        username="admin",
        first_name="Admin",
        last_name="User",
        language="ru",
        balance=10000,
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)
    return admin


@pytest.fixture
async def draft_broadcast(
    db_session: AsyncSession,
    admin_user: User,
) -> Broadcast:
    """Создать тестовую рассылку в статусе DRAFT."""
    broadcast = Broadcast(
        name="Тестовая рассылка",
        message_text="<b>Привет!</b> Это тестовая рассылка.",
        parse_mode=ParseMode.HTML,
        status=BroadcastStatus.DRAFT,
        created_by_id=admin_user.id,
        filter_language="ru",
        filter_exclude_blocked=True,
    )
    db_session.add(broadcast)
    await db_session.commit()
    await db_session.refresh(broadcast)
    return broadcast


# ==============================================================================
# ТЕСТЫ СОЗДАНИЯ РАССЫЛКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_create_broadcast_with_minimal_params(
    db_session: AsyncSession,
    admin_user: User,
) -> None:
    """Тест: создание рассылки с минимальными параметрами."""
    repo = BroadcastRepository(db_session)

    broadcast = await repo.create(
        name="Минимальная рассылка",
        message_text="Текст сообщения",
    )

    assert broadcast.id is not None
    assert broadcast.name == "Минимальная рассылка"
    assert broadcast.message_text == "Текст сообщения"
    assert broadcast.status == BroadcastStatus.DRAFT
    assert broadcast.parse_mode == "HTML"
    assert broadcast.total_recipients == 0
    assert broadcast.sent_count == 0
    assert broadcast.failed_count == 0
    assert broadcast.filter_exclude_blocked is True


@pytest.mark.asyncio
async def test_create_broadcast_with_all_filters(
    db_session: AsyncSession,
    admin_user: User,
) -> None:
    """Тест: создание рассылки со всеми фильтрами сегментации."""
    repo = BroadcastRepository(db_session)
    after_date = datetime(2025, 1, 1, tzinfo=UTC)
    before_date = datetime(2025, 12, 31, tzinfo=UTC)

    broadcast = await repo.create(
        name="Сегментированная рассылка",
        message_text="Текст для сегмента",
        created_by_id=admin_user.id,
        filter_language="en",
        filter_has_payments=True,
        filter_source="promo_winter",
        filter_registered_after=after_date,
        filter_registered_before=before_date,
        filter_exclude_blocked=False,
    )

    assert broadcast.filter_language == "en"
    assert broadcast.filter_has_payments is True
    assert broadcast.filter_source == "promo_winter"
    # SQLite не сохраняет timezone, поэтому сравниваем без timezone
    assert broadcast.filter_registered_after.replace(tzinfo=None) == after_date.replace(
        tzinfo=None
    )
    assert broadcast.filter_registered_before.replace(
        tzinfo=None
    ) == before_date.replace(tzinfo=None)
    assert broadcast.filter_exclude_blocked is False


# ==============================================================================
# ТЕСТЫ ПОЛУЧЕНИЯ РАССЫЛКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_get_by_id_returns_broadcast(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: получение существующей рассылки по ID."""
    repo = BroadcastRepository(db_session)

    result = await repo.get_by_id(draft_broadcast.id)

    assert result is not None
    assert result.id == draft_broadcast.id
    assert result.name == draft_broadcast.name


@pytest.mark.asyncio
async def test_get_by_id_returns_none_for_nonexistent(
    db_session: AsyncSession,
) -> None:
    """Тест: получение несуществующей рассылки возвращает None."""
    repo = BroadcastRepository(db_session)

    result = await repo.get_by_id(99999)

    assert result is None


# ==============================================================================
# ТЕСТЫ ПОЛУЧЕНИЯ АКТИВНЫХ РАССЫЛОК
# ==============================================================================


@pytest.mark.asyncio
async def test_get_active_broadcasts_returns_pending_and_running(
    db_session: AsyncSession,
    admin_user: User,
) -> None:
    """Тест: get_active_broadcasts возвращает PENDING и RUNNING рассылки."""
    repo = BroadcastRepository(db_session)

    # Создаём рассылки в разных статусах
    pending = Broadcast(
        name="Pending",
        message_text="Text",
        status=BroadcastStatus.PENDING,
    )
    running = Broadcast(
        name="Running",
        message_text="Text",
        status=BroadcastStatus.RUNNING,
    )
    draft = Broadcast(
        name="Draft",
        message_text="Text",
        status=BroadcastStatus.DRAFT,
    )
    completed = Broadcast(
        name="Completed",
        message_text="Text",
        status=BroadcastStatus.COMPLETED,
    )

    db_session.add_all([pending, running, draft, completed])
    await db_session.commit()

    result = await repo.get_active_broadcasts()

    assert len(result) == 2
    statuses = {b.status for b in result}
    assert statuses == {BroadcastStatus.PENDING, BroadcastStatus.RUNNING}


@pytest.mark.asyncio
async def test_get_active_broadcasts_returns_oldest_first(
    db_session: AsyncSession,
) -> None:
    """Тест: активные рассылки возвращаются в порядке FIFO (старые первыми)."""
    repo = BroadcastRepository(db_session)

    # Создаём три рассылки с разными датами
    old = Broadcast(
        name="Old",
        message_text="Text",
        status=BroadcastStatus.PENDING,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    middle = Broadcast(
        name="Middle",
        message_text="Text",
        status=BroadcastStatus.PENDING,
        created_at=datetime(2025, 1, 2, tzinfo=UTC),
    )
    new = Broadcast(
        name="New",
        message_text="Text",
        status=BroadcastStatus.PENDING,
        created_at=datetime(2025, 1, 3, tzinfo=UTC),
    )

    db_session.add_all([new, middle, old])  # Добавляем в произвольном порядке
    await db_session.commit()

    result = await repo.get_active_broadcasts()

    assert len(result) == 3
    assert result[0].name == "Old"
    assert result[1].name == "Middle"
    assert result[2].name == "New"


# ==============================================================================
# ТЕСТЫ ЗАПУСКА РАССЫЛКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_start_changes_status_to_running(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: start меняет статус на RUNNING."""
    repo = BroadcastRepository(db_session)

    result = await repo.start(draft_broadcast, total_recipients=100)

    assert result.status == BroadcastStatus.RUNNING
    assert result.total_recipients == 100


@pytest.mark.asyncio
async def test_start_sets_started_at_on_first_start(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: start устанавливает started_at при первом запуске."""
    repo = BroadcastRepository(db_session)

    assert draft_broadcast.started_at is None

    result = await repo.start(draft_broadcast, total_recipients=50)

    assert result.started_at is not None
    assert isinstance(result.started_at, datetime)


@pytest.mark.asyncio
async def test_start_preserves_started_at_on_resume(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: start сохраняет started_at при возобновлении."""
    repo = BroadcastRepository(db_session)

    # Первый запуск
    result1 = await repo.start(draft_broadcast, total_recipients=50)
    first_started_at = result1.started_at

    # Приостанавливаем
    await repo.pause(result1)

    # Возобновляем
    result2 = await repo.start(result1, total_recipients=50)

    assert result2.started_at == first_started_at


@pytest.mark.asyncio
async def test_start_raises_for_invalid_status(
    db_session: AsyncSession,
) -> None:
    """Тест: start выбрасывает ValueError для недопустимого статуса."""
    repo = BroadcastRepository(db_session)

    # Создаём рассылку в статусе COMPLETED
    broadcast = Broadcast(
        name="Completed",
        message_text="Text",
        status=BroadcastStatus.COMPLETED,
    )
    db_session.add(broadcast)
    await db_session.commit()

    with pytest.raises(ValueError, match="Нельзя запустить рассылку"):
        await repo.start(broadcast, total_recipients=10)


# ==============================================================================
# ТЕСТЫ ПРИОСТАНОВКИ РАССЫЛКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_pause_changes_status_to_paused(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: pause меняет статус на PAUSED."""
    repo = BroadcastRepository(db_session)

    # Сначала запускаем
    running = await repo.start(draft_broadcast, total_recipients=50)

    # Приостанавливаем
    result = await repo.pause(running)

    assert result.status == BroadcastStatus.PAUSED


@pytest.mark.asyncio
async def test_pause_raises_for_non_running_status(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: pause выбрасывает ValueError если статус не RUNNING."""
    repo = BroadcastRepository(db_session)

    with pytest.raises(ValueError, match="Нельзя приостановить рассылку"):
        await repo.pause(draft_broadcast)


# ==============================================================================
# ТЕСТЫ ОТМЕНЫ РАССЫЛКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_cancel_changes_status_to_cancelled(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: cancel меняет статус на CANCELLED."""
    repo = BroadcastRepository(db_session)

    result = await repo.cancel(draft_broadcast)

    assert result.status == BroadcastStatus.CANCELLED
    assert result.completed_at is not None


@pytest.mark.asyncio
async def test_cancel_works_for_running_broadcast(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: cancel работает для запущенной рассылки."""
    repo = BroadcastRepository(db_session)

    running = await repo.start(draft_broadcast, total_recipients=50)
    result = await repo.cancel(running)

    assert result.status == BroadcastStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_raises_for_completed_broadcast(
    db_session: AsyncSession,
) -> None:
    """Тест: cancel выбрасывает ValueError для завершённой рассылки."""
    repo = BroadcastRepository(db_session)

    broadcast = Broadcast(
        name="Completed",
        message_text="Text",
        status=BroadcastStatus.COMPLETED,
    )
    db_session.add(broadcast)
    await db_session.commit()

    with pytest.raises(ValueError, match="Нельзя отменить рассылку"):
        await repo.cancel(broadcast)


# ==============================================================================
# ТЕСТЫ ЗАВЕРШЕНИЯ РАССЫЛКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_complete_changes_status_to_completed(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: complete меняет статус на COMPLETED."""
    repo = BroadcastRepository(db_session)

    result = await repo.complete(draft_broadcast)

    assert result.status == BroadcastStatus.COMPLETED
    assert result.completed_at is not None


# ==============================================================================
# ТЕСТЫ МАРКИРОВКИ КАК НЕУДАЧНОЙ
# ==============================================================================


@pytest.mark.asyncio
async def test_fail_changes_status_to_failed(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: fail меняет статус на FAILED."""
    repo = BroadcastRepository(db_session)

    result = await repo.fail(draft_broadcast, "Тестовая ошибка")

    assert result.status == BroadcastStatus.FAILED
    assert result.error_message == "Тестовая ошибка"
    assert result.completed_at is not None


# ==============================================================================
# ТЕСТЫ ОБНОВЛЕНИЯ ПРОГРЕССА
# ==============================================================================


@pytest.mark.asyncio
async def test_update_progress_updates_counters(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: update_progress обновляет счётчики отправки."""
    repo = BroadcastRepository(db_session)

    await repo.update_progress(
        broadcast_id=draft_broadcast.id,
        sent_count=10,
        failed_count=2,
        last_processed_user_id=12,
    )

    # Перезагружаем из БД
    await db_session.refresh(draft_broadcast)

    assert draft_broadcast.sent_count == 10
    assert draft_broadcast.failed_count == 2
    assert draft_broadcast.last_processed_user_id == 12


@pytest.mark.asyncio
async def test_increment_progress_increments_sent(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: increment_progress увеличивает sent_count."""
    repo = BroadcastRepository(db_session)

    # Устанавливаем начальные значения
    draft_broadcast.sent_count = 5
    draft_broadcast.failed_count = 1
    await db_session.commit()

    await repo.increment_progress(
        broadcast_id=draft_broadcast.id,
        sent_delta=3,
        last_processed_user_id=8,
    )

    await db_session.refresh(draft_broadcast)

    assert draft_broadcast.sent_count == 8  # 5 + 3
    assert draft_broadcast.failed_count == 1  # Без изменений
    assert draft_broadcast.last_processed_user_id == 8


@pytest.mark.asyncio
async def test_increment_progress_increments_failed(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: increment_progress увеличивает failed_count."""
    repo = BroadcastRepository(db_session)

    draft_broadcast.sent_count = 5
    draft_broadcast.failed_count = 1
    await db_session.commit()

    await repo.increment_progress(
        broadcast_id=draft_broadcast.id,
        failed_delta=2,
        last_processed_user_id=7,
    )

    await db_session.refresh(draft_broadcast)

    assert draft_broadcast.sent_count == 5  # Без изменений
    assert draft_broadcast.failed_count == 3  # 1 + 2


# ==============================================================================
# ТЕСТЫ СМЕНЫ СТАТУСА
# ==============================================================================


@pytest.mark.asyncio
async def test_set_status_changes_status(
    db_session: AsyncSession,
    draft_broadcast: Broadcast,
) -> None:
    """Тест: set_status меняет статус напрямую."""
    repo = BroadcastRepository(db_session)

    await repo.set_status(draft_broadcast.id, BroadcastStatus.RUNNING)

    await db_session.refresh(draft_broadcast)
    assert draft_broadcast.status == BroadcastStatus.RUNNING
