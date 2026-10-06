"""Тесты для методов сегментации UserRepository.

Модуль тестирует:
- count_by_segment (подсчёт пользователей по фильтрам)
- get_by_segment (получение батча пользователей)
- iter_by_segment (асинхронная итерация по пользователям)
- _apply_segment_filters (внутренний метод применения фильтров)

Тестируемая функциональность:
1. Фильтрация по языку
2. Фильтрация по факту оплат (has_payments)
3. Фильтрация по источнику (source)
4. Фильтрация по дате регистрации (registered_after/before)
5. Исключение заблокированных (exclude_blocked)
6. Пагинация (after_user_id, limit)
7. Комбинации фильтров
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.transaction import Transaction, TransactionType
from src.db.models.user import User
from src.db.repositories.user_repo import UserRepository

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
async def users_for_segmentation(db_session: AsyncSession) -> list[User]:
    """Создать набор пользователей для тестирования сегментации."""
    users = [
        # Русскоязычные пользователи
        User(
            telegram_id=1001,
            username="user1",
            language="ru",
            source="promo_winter",
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            is_blocked=False,
        ),
        User(
            telegram_id=1002,
            username="user2",
            language="ru",
            source="promo_spring",
            created_at=datetime(2025, 2, 1, tzinfo=UTC),
            is_blocked=False,
        ),
        User(
            telegram_id=1003,
            username="user3",
            language="ru",
            source=None,
            created_at=datetime(2025, 3, 1, tzinfo=UTC),
            is_blocked=True,  # Заблокирован
        ),
        # Англоязычные пользователи
        User(
            telegram_id=2001,
            username="user4",
            language="en",
            source="promo_winter",
            created_at=datetime(2025, 1, 15, tzinfo=UTC),
            is_blocked=False,
        ),
        User(
            telegram_id=2002,
            username="user5",
            language="en",
            source=None,
            created_at=datetime(2025, 2, 15, tzinfo=UTC),
            is_blocked=False,
        ),
    ]

    for user in users:
        db_session.add(user)
    await db_session.commit()

    # Обновляем ID в объектах
    for user in users:
        await db_session.refresh(user)

    return users


@pytest.fixture
async def users_with_payments(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> list[User]:
    """Добавить транзакции оплаты для некоторых пользователей."""
    # Первый и четвёртый пользователи — платившие
    paying_users = [users_for_segmentation[0], users_for_segmentation[3]]

    for user in paying_users:
        # Устанавливаем начальный баланс и вычисляем balance_after
        initial_balance = user.balance
        transaction = Transaction(
            user_id=user.id,
            type=TransactionType.PURCHASE,
            amount=100,
            balance_after=initial_balance + 100,
            description="Покупка токенов",
        )
        db_session.add(transaction)
        # Обновляем баланс пользователя
        user.balance = initial_balance + 100

    await db_session.commit()
    return paying_users


# ==============================================================================
# ТЕСТЫ count_by_segment
# ==============================================================================


@pytest.mark.asyncio
async def test_count_by_segment_without_filters_returns_all(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: count_by_segment без фильтров возвращает всех незаблокированных."""
    repo = UserRepository(db_session)

    count = await repo.count_by_segment()

    # Всего 5 пользователей, но 1 заблокирован
    assert count == 4


@pytest.mark.asyncio
async def test_count_by_segment_by_language(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: фильтр по языку."""
    repo = UserRepository(db_session)

    ru_count = await repo.count_by_segment(language="ru")
    en_count = await repo.count_by_segment(language="en")

    # 3 русскоязычных, но 1 заблокирован = 2
    assert ru_count == 2
    # 2 англоязычных
    assert en_count == 2


@pytest.mark.asyncio
async def test_count_by_segment_by_source(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: фильтр по источнику."""
    repo = UserRepository(db_session)

    winter_count = await repo.count_by_segment(source="promo_winter")
    spring_count = await repo.count_by_segment(source="promo_spring")

    # 2 пользователя из promo_winter
    assert winter_count == 2
    # 1 пользователь из promo_spring
    assert spring_count == 1


@pytest.mark.asyncio
async def test_count_by_segment_by_registered_after(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: фильтр по дате регистрации (от)."""
    repo = UserRepository(db_session)

    after_jan = await repo.count_by_segment(
        registered_after=datetime(2025, 1, 20, tzinfo=UTC),
    )

    # Зарегистрированы после 20 января: user2, user4 (заблокирован), user5
    # user4 исключается (exclude_blocked=True)
    assert after_jan == 2


@pytest.mark.asyncio
async def test_count_by_segment_by_registered_before(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: фильтр по дате регистрации (до)."""
    repo = UserRepository(db_session)

    before_feb = await repo.count_by_segment(
        registered_before=datetime(2025, 2, 1, tzinfo=UTC),
    )

    # Зарегистрированы до 1 февраля: user1, user2, user4
    assert before_feb == 3


@pytest.mark.asyncio
async def test_count_by_segment_exclude_blocked_false(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: exclude_blocked=False включает заблокированных."""
    repo = UserRepository(db_session)

    count = await repo.count_by_segment(exclude_blocked=False)

    # Все 5 пользователей (включая заблокированного)
    assert count == 5


@pytest.mark.asyncio
async def test_count_by_segment_by_has_payments(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
    users_with_payments: list[User],
) -> None:
    """Тест: фильтр по наличию оплат."""
    repo = UserRepository(db_session)

    with_payments = await repo.count_by_segment(has_payments=True)
    without_payments = await repo.count_by_segment(has_payments=False)

    # 2 пользователя платили
    assert with_payments == 2
    # 2 не платили (1 заблокирован, не учитывается)
    assert without_payments == 2


@pytest.mark.asyncio
async def test_count_by_segment_combined_filters(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
    users_with_payments: list[User],
) -> None:
    """Тест: комбинация фильтров."""
    repo = UserRepository(db_session)

    # Русскоязычные платившие из promo_winter
    count = await repo.count_by_segment(
        language="ru",
        has_payments=True,
        source="promo_winter",
    )

    # Только user1
    assert count == 1


# ==============================================================================
# ТЕСТЫ get_by_segment
# ==============================================================================


@pytest.mark.asyncio
async def test_get_by_segment_returns_users(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: get_by_segment возвращает пользователей."""
    repo = UserRepository(db_session)

    users = await repo.get_by_segment(limit=10)

    assert len(users) == 4  # Без заблокированного
    assert all(isinstance(u, User) for u in users)


@pytest.mark.asyncio
async def test_get_by_segment_sorted_by_id(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: пользователи отсортированы по ID."""
    repo = UserRepository(db_session)

    users = await repo.get_by_segment(limit=10)

    # Проверяем что ID идут по возрастанию
    ids = [u.id for u in users]
    assert ids == sorted(ids)


@pytest.mark.asyncio
async def test_get_by_segment_respects_limit(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: limit ограничивает количество."""
    repo = UserRepository(db_session)

    users = await repo.get_by_segment(limit=2)

    assert len(users) == 2


@pytest.mark.asyncio
async def test_get_by_segment_pagination_with_after_user_id(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: пагинация через after_user_id."""
    repo = UserRepository(db_session)

    # Первая страница
    page1 = await repo.get_by_segment(limit=2)
    assert len(page1) == 2

    # Вторая страница (после последнего ID первой страницы)
    last_id = page1[-1].id
    page2 = await repo.get_by_segment(after_user_id=last_id, limit=2)

    # Проверяем что ID второй страницы больше последнего ID первой
    assert all(u.id > last_id for u in page2)
    # Проверяем что нет дубликатов
    page1_ids = {u.id for u in page1}
    page2_ids = {u.id for u in page2}
    assert page1_ids.isdisjoint(page2_ids)


@pytest.mark.asyncio
async def test_get_by_segment_filters_by_language(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: фильтрация по языку."""
    repo = UserRepository(db_session)

    ru_users = await repo.get_by_segment(language="ru", limit=10)

    assert len(ru_users) == 2
    assert all(u.language == "ru" for u in ru_users)


# ==============================================================================
# ТЕСТЫ iter_by_segment
# ==============================================================================


@pytest.mark.asyncio
async def test_iter_by_segment_yields_all_users(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: iter_by_segment возвращает всех пользователей."""
    repo = UserRepository(db_session)

    users = []
    async for user in repo.iter_by_segment(batch_size=2):
        users.append(user)  # noqa: PERF401

    assert len(users) == 4  # Без заблокированного


@pytest.mark.asyncio
async def test_iter_by_segment_respects_filters(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: iter_by_segment применяет фильтры."""
    repo = UserRepository(db_session)

    users = []
    async for user in repo.iter_by_segment(language="en", batch_size=10):
        users.append(user)  # noqa: PERF401

    assert len(users) == 2
    assert all(u.language == "en" for u in users)


@pytest.mark.asyncio
async def test_iter_by_segment_with_small_batch_size(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: iter_by_segment работает с маленьким batch_size."""
    repo = UserRepository(db_session)

    users = []
    async for user in repo.iter_by_segment(batch_size=1):
        users.append(user)  # noqa: PERF401

    # Должны получить всех пользователей по одному
    assert len(users) == 4


@pytest.mark.asyncio
async def test_iter_by_segment_preserves_order(
    db_session: AsyncSession,
    users_for_segmentation: list[User],
) -> None:
    """Тест: iter_by_segment возвращает пользователей в правильном порядке."""
    repo = UserRepository(db_session)

    user_ids = []
    async for user in repo.iter_by_segment(batch_size=2):
        user_ids.append(user.id)  # noqa: PERF401

    # ID должны идти по возрастанию
    assert user_ids == sorted(user_ids)
