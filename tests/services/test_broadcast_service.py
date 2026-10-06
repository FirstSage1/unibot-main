"""Тесты для BroadcastService.

Модуль тестирует:
- Создание рассылки (create_broadcast)
- Подсчёт получателей (count_recipients)
- Предпросмотр рассылки (preview_broadcast)
- Запуск рассылки (start_broadcast)
- Приостановка (pause_broadcast)
- Отмена (cancel_broadcast)
- Получение рассылок (get_broadcast, get_active_broadcasts, get_all_broadcasts)

Тестируемая функциональность:
1. Сервис корректно использует репозитории
2. Бизнес-логика работает правильно
3. Предпросмотр формирует правильное описание
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import BroadcastConfig, YamlConfig
from src.db.models.broadcast import BroadcastStatus
from src.db.models.user import User
from src.services.broadcast_service import BroadcastService

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def broadcast_config() -> BroadcastConfig:
    """Создать тестовую конфигурацию рассылок."""
    return BroadcastConfig(
        enabled=True,
        messages_per_second=25,
        batch_size=50,
        retry_on_error=3,
        flood_wait_multiplier=1.2,
    )


@pytest.fixture
def yaml_config(broadcast_config: BroadcastConfig) -> YamlConfig:
    """Создать тестовую YAML конфигурацию с поддержкой ru и en языков."""
    from src.config.yaml_config import LocalizationConfig as YamlLocalizationConfig

    # Используем дефолтный YamlConfig и подменяем broadcast секцию
    config = YamlConfig()
    # Переопределяем broadcast секцию для тестов
    object.__setattr__(config, "broadcast", broadcast_config)

    # Переопределяем localization секцию для поддержки ru и en
    # Это необходимо, так как тесты используют фильтр по языку "en"
    localization_config = YamlLocalizationConfig(
        enabled=True,
        default_language="ru",
        available_languages=["ru", "en"],
    )
    object.__setattr__(config, "localization", localization_config)

    return config


@pytest.fixture
async def admin_user(db_session: AsyncSession) -> User:
    """Создать администратора."""
    admin = User(
        telegram_id=987654321,
        username="admin",
        first_name="Admin",
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)
    return admin


@pytest.fixture
async def test_users(db_session: AsyncSession) -> list[User]:
    """Создать тестовых пользователей."""
    users = [
        User(telegram_id=1001, language="ru", is_blocked=False),
        User(telegram_id=1002, language="ru", is_blocked=False),
        User(telegram_id=1003, language="en", is_blocked=False),
        User(telegram_id=1004, language="ru", is_blocked=True),  # Заблокирован
    ]
    for user in users:
        db_session.add(user)
    await db_session.commit()
    return users


# ==============================================================================
# ТЕСТЫ СОЗДАНИЯ РАССЫЛКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_create_broadcast_saves_to_db(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    admin_user: User,
) -> None:
    """Тест: создание рассылки сохраняет её в БД."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тестовая рассылка",
        message_text="Текст сообщения",
        created_by_id=admin_user.id,
    )

    assert broadcast.id is not None
    assert broadcast.name == "Тестовая рассылка"
    assert broadcast.status == BroadcastStatus.DRAFT


@pytest.mark.asyncio
async def test_create_broadcast_with_filters(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    admin_user: User,
) -> None:
    """Тест: создание рассылки с фильтрами."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Сегментированная",
        message_text="Текст",
        filter_language="ru",
        filter_has_payments=True,
        filter_source="promo_winter",
    )

    assert broadcast.filter_language == "ru"
    assert broadcast.filter_has_payments is True
    assert broadcast.filter_source == "promo_winter"


# ==============================================================================
# ТЕСТЫ ПОДСЧЁТА ПОЛУЧАТЕЛЕЙ
# ==============================================================================


@pytest.mark.asyncio
async def test_count_recipients_without_filters(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    test_users: list[User],
) -> None:
    """Тест: подсчёт получателей без фильтров."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
    )

    count = await service.count_recipients(broadcast)

    # 4 пользователя, 1 заблокирован = 3
    assert count == 3


@pytest.mark.asyncio
async def test_count_recipients_with_language_filter(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    test_users: list[User],
) -> None:
    """Тест: подсчёт получателей с фильтром по языку."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
        filter_language="ru",
    )

    count = await service.count_recipients(broadcast)

    # 3 русскоязычных, 1 заблокирован = 2
    assert count == 2


# ==============================================================================
# ТЕСТЫ ПРЕДПРОСМОТРА
# ==============================================================================


@pytest.mark.asyncio
async def test_preview_broadcast_returns_total_recipients(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    test_users: list[User],
) -> None:
    """Тест: preview возвращает количество получателей."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
        filter_language="ru",
    )

    preview = await service.preview_broadcast(broadcast)

    assert preview.total_recipients == 2
    assert preview.sample_message == "Текст"


@pytest.mark.asyncio
async def test_preview_broadcast_describes_filters(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
) -> None:
    """Тест: preview формирует описание фильтров."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
        filter_language="ru",
        filter_has_payments=True,
        filter_source="promo_winter",
    )

    preview = await service.preview_broadcast(broadcast)

    # Проверяем что в описании есть все фильтры
    assert "ru" in preview.filters_description
    assert "платившие" in preview.filters_description.lower()
    assert "promo_winter" in preview.filters_description


@pytest.mark.asyncio
async def test_preview_broadcast_without_filters(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
) -> None:
    """Тест: preview показывает "Без фильтров" если фильтры не заданы."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
        filter_exclude_blocked=False,  # Отключаем дефолтный фильтр
    )

    preview = await service.preview_broadcast(broadcast)

    # Если нет фильтров, должно быть "Без фильтров"
    assert "без фильтров" in preview.filters_description.lower()


# ==============================================================================
# ТЕСТЫ ЗАПУСКА РАССЫЛКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_start_broadcast_changes_status(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    test_users: list[User],
) -> None:
    """Тест: запуск рассылки меняет статус на RUNNING."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
    )

    result = await service.start_broadcast(broadcast)

    assert result.status == BroadcastStatus.RUNNING
    assert result.total_recipients == 3


@pytest.mark.asyncio
async def test_start_broadcast_counts_recipients(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    test_users: list[User],
) -> None:
    """Тест: запуск подсчитывает получателей по фильтрам."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
        filter_language="en",
    )

    result = await service.start_broadcast(broadcast)

    # Только 1 англоязычный незаблокированный
    assert result.total_recipients == 1


# ==============================================================================
# ТЕСТЫ ПРИОСТАНОВКИ
# ==============================================================================


@pytest.mark.asyncio
async def test_pause_broadcast_changes_status(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    test_users: list[User],
) -> None:
    """Тест: приостановка меняет статус на PAUSED."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
    )
    running = await service.start_broadcast(broadcast)

    result = await service.pause_broadcast(running)

    assert result.status == BroadcastStatus.PAUSED


# ==============================================================================
# ТЕСТЫ ОТМЕНЫ
# ==============================================================================


@pytest.mark.asyncio
async def test_cancel_broadcast_changes_status(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
) -> None:
    """Тест: отмена меняет статус на CANCELLED."""
    service = BroadcastService(db_session, yaml_config)

    broadcast = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
    )

    result = await service.cancel_broadcast(broadcast)

    assert result.status == BroadcastStatus.CANCELLED


# ==============================================================================
# ТЕСТЫ ПОЛУЧЕНИЯ РАССЫЛОК
# ==============================================================================


@pytest.mark.asyncio
async def test_get_broadcast_returns_by_id(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
) -> None:
    """Тест: получение рассылки по ID."""
    service = BroadcastService(db_session, yaml_config)

    created = await service.create_broadcast(
        name="Тест",
        message_text="Текст",
    )

    result = await service.get_broadcast(created.id)

    assert result is not None
    assert result.id == created.id


@pytest.mark.asyncio
async def test_get_active_broadcasts_returns_pending_and_running(
    db_session: AsyncSession,
    yaml_config: YamlConfig,
    test_users: list[User],
) -> None:
    """Тест: получение активных рассылок."""
    service = BroadcastService(db_session, yaml_config)

    # Создаём рассылки в разных статусах
    _draft = await service.create_broadcast(name="Draft", message_text="Text")
    pending = await service.create_broadcast(name="Pending", message_text="Text")
    running = await service.create_broadcast(name="Running", message_text="Text")

    await service.start_broadcast(pending)
    await service.start_broadcast(running)

    active = await service.get_active_broadcasts()

    assert len(active) == 2
    statuses = {b.status for b in active}
    assert BroadcastStatus.RUNNING in statuses
