"""Тесты для scheduler.tasks - задачи планировщика.

Модуль тестирует:
- process_broadcasts() - обработка активных рассылок
- _process_single_broadcast() - обработка одной рассылки
- FloodWait handling
- TelegramForbiddenError handling
- Batch processing logic
- Progress tracking
- Broadcast completion

Тестируемая функциональность:
1. Задача корректно находит активные рассылки
2. FloodWait не ломает обработку
3. Блокировка бота не останавливает рассылку
4. Прогресс корректно сохраняется
5. Рассылка завершается когда все сообщения отправлены
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import BroadcastConfig, YamlConfig
from src.db.models.broadcast import Broadcast, BroadcastStatus, ParseMode
from src.db.models.user import User
from src.db.repositories.broadcast_repo import BroadcastRepository
from src.scheduler.tasks import _process_single_broadcast, process_broadcasts

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def broadcast_config() -> BroadcastConfig:
    """Создать тестовую конфигурацию рассылок."""
    return BroadcastConfig(
        enabled=True,
        messages_per_second=10,
        batch_size=50,
        retry_on_error=2,
        flood_wait_multiplier=1.5,
    )


@pytest.fixture
def yaml_config(broadcast_config: BroadcastConfig) -> YamlConfig:
    """Создать тестовую YAML конфигурацию."""
    from src.config.yaml_config import LocalizationConfig as YamlLocalizationConfig

    config = YamlConfig()
    object.__setattr__(config, "broadcast", broadcast_config)

    localization_config = YamlLocalizationConfig(
        enabled=True,
        default_language="ru",
        available_languages=["ru", "en"],
    )
    object.__setattr__(config, "localization", localization_config)

    return config


@pytest.fixture
def mock_bot() -> AsyncMock:
    """Создать мок Telegram бота."""
    bot = AsyncMock(spec=Bot)
    bot.send_message = AsyncMock()
    return bot


@pytest.fixture
async def test_users(db_session: AsyncSession) -> list[User]:
    """Создать тестовых пользователей."""
    users = [
        User(telegram_id=2001, language="ru", is_blocked=False),
        User(telegram_id=2002, language="ru", is_blocked=False),
        User(telegram_id=2003, language="en", is_blocked=False),
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


@pytest.fixture
async def running_broadcast(
    db_session: AsyncSession,
    test_users: list[User],
) -> Broadcast:
    """Создать рассылку в статусе RUNNING."""
    broadcast = Broadcast(
        name="Запущенная рассылка",
        message_text="Текст сообщения",
        parse_mode=ParseMode.HTML,
        status=BroadcastStatus.RUNNING,
        total_recipients=len(test_users),
    )
    db_session.add(broadcast)
    await db_session.commit()
    await db_session.refresh(broadcast)
    return broadcast


# ==============================================================================
# ТЕСТЫ process_broadcasts()
# ==============================================================================


@pytest.mark.asyncio
async def test_process_broadcasts_no_active_broadcasts_exits_quickly(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
) -> None:
    """Тест: process_broadcasts быстро завершается если нет активных рассылок."""
    # Патчим DatabaseSession чтобы использовать тестовую БД
    with patch("src.scheduler.tasks.DatabaseSession") as mock_db_session:
        mock_db_session.return_value.__aenter__.return_value = db_session

        # Вызываем задачу
        await process_broadcasts(yaml_config, mock_bot)

        # Бот не должен отправлять сообщения
        mock_bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_process_broadcasts_processes_pending_broadcast(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: process_broadcasts обрабатывает PENDING рассылку."""
    with patch("src.scheduler.tasks.DatabaseSession") as mock_db_session:
        mock_db_session.return_value.__aenter__.return_value = db_session

        await process_broadcasts(yaml_config, mock_bot)

        # Проверяем что статус изменился на RUNNING
        await db_session.refresh(pending_broadcast)
        assert pending_broadcast.status == BroadcastStatus.RUNNING

        # Проверяем что сообщения отправлены
        assert mock_bot.send_message.call_count == len(test_users)


@pytest.mark.asyncio
async def test_process_broadcasts_processes_running_broadcast(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    running_broadcast: Broadcast,
) -> None:
    """Тест: process_broadcasts обрабатывает RUNNING рассылку."""
    with patch("src.scheduler.tasks.DatabaseSession") as mock_db_session:
        mock_db_session.return_value.__aenter__.return_value = db_session

        await process_broadcasts(yaml_config, mock_bot)

        # Проверяем что сообщения отправлены
        assert mock_bot.send_message.call_count == len(test_users)


@pytest.mark.asyncio
async def test_process_broadcasts_completes_when_all_sent(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    running_broadcast: Broadcast,
) -> None:
    """Тест: рассылка завершается когда все сообщения отправлены."""
    with patch("src.scheduler.tasks.DatabaseSession") as mock_db_session:
        mock_db_session.return_value.__aenter__.return_value = db_session

        # Первый вызов - отправка всех сообщений
        await process_broadcasts(yaml_config, mock_bot)

        await db_session.refresh(running_broadcast)
        assert running_broadcast.status == BroadcastStatus.RUNNING
        assert running_broadcast.sent_count == len(test_users)

        # Второй вызов - завершение рассылки (больше нет пользователей)
        await process_broadcasts(yaml_config, mock_bot)

        # Проверяем что рассылка завершена
        await db_session.refresh(running_broadcast)
        assert running_broadcast.status == BroadcastStatus.COMPLETED


# ==============================================================================
# ТЕСТЫ _process_single_broadcast()
# ==============================================================================


@pytest.mark.asyncio
async def test_process_single_broadcast_changes_pending_to_running(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    pending_broadcast: Broadcast,
) -> None:
    """Тест: _process_single_broadcast меняет статус PENDING → RUNNING."""
    from src.db.repositories.user_repo import UserRepository

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast

    await _process_single_broadcast(
        broadcast=pending_broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=1.0 / config.messages_per_second,
    )

    await db_session.refresh(pending_broadcast)
    assert pending_broadcast.status == BroadcastStatus.RUNNING


@pytest.mark.asyncio
async def test_process_single_broadcast_sends_messages(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    running_broadcast: Broadcast,
) -> None:
    """Тест: _process_single_broadcast отправляет сообщения."""
    from src.db.repositories.user_repo import UserRepository

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast

    await _process_single_broadcast(
        broadcast=running_broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=0.01,  # Быстрый интервал для теста
    )

    # Проверяем что сообщения отправлены всем пользователям
    assert mock_bot.send_message.call_count == len(test_users)


@pytest.mark.asyncio
async def test_process_single_broadcast_handles_flood_wait(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    running_broadcast: Broadcast,
) -> None:
    """Тест: _process_single_broadcast обрабатывает FloodWait."""
    from src.db.repositories.user_repo import UserRepository

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast

    # Второе сообщение вызывает FloodWait
    mock_bot.send_message.side_effect = [
        None,  # Успех
        TelegramRetryAfter(method=Mock(), retry_after=30, message="Flood"),
    ]

    await _process_single_broadcast(
        broadcast=running_broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=0.01,
    )

    # Проверяем что отправлено только 2 сообщения (1 успех, 1 FloodWait)
    assert mock_bot.send_message.call_count == 2

    # Проверяем что прогресс обновлён
    await db_session.refresh(running_broadcast)
    assert running_broadcast.sent_count == 1
    # FloodWait не считается failed — это техническая пауза, не ошибка отправки
    assert running_broadcast.failed_count == 0


@pytest.mark.asyncio
async def test_process_single_broadcast_handles_forbidden_error(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    running_broadcast: Broadcast,
) -> None:
    """Тест: _process_single_broadcast обрабатывает блокировку бота."""
    from src.db.repositories.user_repo import UserRepository

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast

    # Все сообщения вызывают блокировку
    mock_bot.send_message.side_effect = TelegramForbiddenError(
        method=Mock(),
        message="Bot was blocked",
    )

    await _process_single_broadcast(
        broadcast=running_broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=0.01,
    )

    # Проверяем что все попытки завершились неудачей
    await db_session.refresh(running_broadcast)
    assert running_broadcast.sent_count == 0
    assert running_broadcast.failed_count == len(test_users)


@pytest.mark.asyncio
async def test_process_single_broadcast_handles_network_errors(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    running_broadcast: Broadcast,
) -> None:
    """Тест: _process_single_broadcast обрабатывает сетевые ошибки."""
    from src.db.repositories.user_repo import UserRepository

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast

    # Все сообщения вызывают сетевую ошибку
    mock_bot.send_message.side_effect = OSError("Network error")

    await _process_single_broadcast(
        broadcast=running_broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=0.01,
    )

    # Проверяем что все попытки завершились неудачей
    await db_session.refresh(running_broadcast)
    assert running_broadcast.failed_count == len(test_users)


@pytest.mark.asyncio
async def test_process_single_broadcast_updates_progress(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    running_broadcast: Broadcast,
) -> None:
    """Тест: _process_single_broadcast обновляет прогресс."""
    from src.db.repositories.user_repo import UserRepository

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast

    await _process_single_broadcast(
        broadcast=running_broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=0.01,
    )

    # Проверяем что прогресс обновлён
    await db_session.refresh(running_broadcast)
    assert running_broadcast.sent_count == len(test_users)
    assert running_broadcast.last_processed_user_id == test_users[-1].id


@pytest.mark.asyncio
async def test_process_single_broadcast_completes_when_no_users(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    running_broadcast: Broadcast,
) -> None:
    """Тест: рассылка завершается когда больше нет пользователей."""
    from src.db.repositories.user_repo import UserRepository

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast

    # Все пользователи уже обработаны
    running_broadcast.last_processed_user_id = 99999
    await db_session.commit()

    await _process_single_broadcast(
        broadcast=running_broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=0.01,
    )

    # Проверяем что рассылка завершена
    await db_session.refresh(running_broadcast)
    assert running_broadcast.status == BroadcastStatus.COMPLETED


@pytest.mark.asyncio
async def test_process_single_broadcast_uses_parse_mode(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
) -> None:
    """Тест: _process_single_broadcast использует parse_mode из рассылки."""
    from src.db.repositories.user_repo import UserRepository

    # Рассылка с Markdown
    broadcast = Broadcast(
        name="Markdown рассылка",
        message_text="**Жирный текст**",
        parse_mode=ParseMode.MARKDOWN,
        status=BroadcastStatus.RUNNING,
        total_recipients=len(test_users),
    )
    db_session.add(broadcast)
    await db_session.commit()
    await db_session.refresh(broadcast)

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast

    await _process_single_broadcast(
        broadcast=broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=0.01,
    )

    # Проверяем что parse_mode передан в send_message
    for call_args in mock_bot.send_message.call_args_list:
        assert call_args.kwargs["parse_mode"] == ParseMode.MARKDOWN


@pytest.mark.asyncio
async def test_process_single_broadcast_no_parse_mode_when_none(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
) -> None:
    """Тест: _process_single_broadcast не использует parse_mode если NONE."""
    from src.db.repositories.user_repo import UserRepository

    # Рассылка без форматирования
    broadcast = Broadcast(
        name="Без форматирования",
        message_text="Простой текст",
        parse_mode=ParseMode.NONE,
        status=BroadcastStatus.RUNNING,
        total_recipients=len(test_users),
    )
    db_session.add(broadcast)
    await db_session.commit()
    await db_session.refresh(broadcast)

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast

    await _process_single_broadcast(
        broadcast=broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=0.01,
    )

    # Проверяем что parse_mode = None
    for call_args in mock_bot.send_message.call_args_list:
        assert call_args.kwargs["parse_mode"] is None


@pytest.mark.asyncio
@patch("asyncio.sleep", new_callable=AsyncMock)
async def test_process_single_broadcast_respects_rate_limit(
    mock_sleep: AsyncMock,
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    mock_bot: AsyncMock,
    test_users: list[User],
    running_broadcast: Broadcast,
) -> None:
    """Тест: _process_single_broadcast соблюдает rate limiting."""
    from src.db.repositories.user_repo import UserRepository

    repo = BroadcastRepository(db_session)
    user_repo = UserRepository(db_session)
    config = yaml_config.broadcast
    send_interval = 1.0 / config.messages_per_second

    await _process_single_broadcast(
        broadcast=running_broadcast,
        repo=repo,
        user_repo=user_repo,
        bot=mock_bot,
        config=config,
        send_interval=send_interval,
    )

    # Проверяем что задержка была после каждого сообщения
    assert mock_sleep.call_count == len(test_users)
    for call_args in mock_sleep.call_args_list:
        assert call_args[0][0] == pytest.approx(send_interval)
