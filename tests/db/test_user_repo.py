"""Тесты для UserRepository.

Модуль тестирует:
- UserRepository.update_language (обновление языка пользователя)

Тестируемая функциональность:
1. update_language обновляет поле User.language
2. update_language делает commit
3. update_language делает refresh и возвращает обновлённого пользователя
4. Обновлённый объект User имеет новый язык
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.models.user import User
from src.db.repositories.user_repo import UserRepository

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def mock_session() -> AsyncMock:
    """Мок асинхронной сессии SQLAlchemy."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def mock_user() -> User:
    """Мок пользователя из БД."""
    user = MagicMock(spec=User)
    user.id = 1
    user.telegram_id = 123456789
    user.language = "ru"
    user.username = "testuser"
    user.first_name = "Test"
    return user


# ==============================================================================
# ТЕСТЫ update_language
# ==============================================================================


@pytest.mark.asyncio
async def test_update_language_changes_user_language(
    mock_session: AsyncMock,
    mock_user: User,
) -> None:
    """Тест: update_language обновляет поле User.language."""
    repo = UserRepository(mock_session)

    result = await repo.update_language(mock_user, "en")

    # Проверяем что язык был изменён
    assert mock_user.language == "en"

    # Проверяем что был возвращён обновлённый пользователь
    assert result is mock_user


@pytest.mark.asyncio
async def test_update_language_commits_changes(
    mock_session: AsyncMock,
    mock_user: User,
) -> None:
    """Тест: update_language делает commit."""
    repo = UserRepository(mock_session)

    await repo.update_language(mock_user, "en")

    # Проверяем что commit был вызван
    mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_update_language_refreshes_user(
    mock_session: AsyncMock,
    mock_user: User,
) -> None:
    """Тест: update_language делает refresh пользователя."""
    repo = UserRepository(mock_session)

    await repo.update_language(mock_user, "en")

    # Проверяем что refresh был вызван с правильным пользователем
    mock_session.refresh.assert_called_once_with(mock_user)


@pytest.mark.asyncio
async def test_update_language_updates_to_different_languages(
    mock_session: AsyncMock,
    mock_user: User,
) -> None:
    """Тест: update_language работает с разными языками."""
    repo = UserRepository(mock_session)

    # Обновляем на английский
    await repo.update_language(mock_user, "en")
    assert mock_user.language == "en"

    # Обновляем на китайский
    await repo.update_language(mock_user, "zh")
    assert mock_user.language == "zh"


@pytest.mark.asyncio
async def test_update_language_can_set_same_language(
    mock_session: AsyncMock,
    mock_user: User,
) -> None:
    """Тест: update_language может установить тот же язык."""
    repo = UserRepository(mock_session)

    # Пользователь уже имеет язык "ru"
    assert mock_user.language == "ru"

    # Обновляем на тот же язык
    await repo.update_language(mock_user, "ru")

    assert mock_user.language == "ru"
    mock_session.commit.assert_called_once()
