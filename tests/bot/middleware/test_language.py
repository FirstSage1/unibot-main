"""Тесты для middleware определения языка.

Модуль тестирует:
- LanguageMiddleware (middleware для автоопределения языка пользователя)

Тестируемая функциональность:
1. Middleware добавляет объект Localization в data["l10n"]
2. Если мультиязычность отключена → используется default_language
3. Получение языка из БД по telegram_id пользователя
4. Fallback на default_language если пользователь не найден
5. Fallback на default_language если у пользователя нет language
6. Обработка событий без пользователя (event_from_user=None)
7. Обработка ошибок при получении языка из БД
8. Middleware не блокирует выполнение handler при ошибках

Архитектура тестов:
- Используем Dependency Injection — инжектируем mock зависимости
- НЕ используем @patch для DatabaseSession (middleware принимает session_factory)
- Все внешние зависимости мокаются через конструктор
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.middleware.language import (
    LanguageMiddleware,
    LocalizationFactory,
    SessionFactory,
)
from src.db.models.user import User as DbUser
from src.utils.i18n import Localization, LocalizationConfig, LocalizationService

# ==============================================================================
# ФИКСТУРЫ — базовые объекты
# ==============================================================================


@pytest.fixture
def mock_handler() -> AsyncMock:
    """Мок async handler."""
    return AsyncMock(return_value="handler_result")


@pytest.fixture
def mock_translations() -> dict[str, dict[str, str]]:
    """Тестовые переводы для двух языков."""
    return {
        "ru": {
            "start_message": "Привет! Я бот.",
            "hello_user": "Привет, {name}!",
        },
        "en": {
            "start_message": "Hello! I'm a bot.",
            "hello_user": "Hello, {name}!",
        },
    }


@pytest.fixture
def localization_config() -> LocalizationConfig:
    """Базовый конфиг локализации для тестов."""
    return LocalizationConfig(
        enabled=True,
        default_language="ru",
        available_languages=("ru", "en"),
    )


@pytest.fixture
def localization_config_disabled() -> LocalizationConfig:
    """Конфиг с отключённой локализацией."""
    return LocalizationConfig(
        enabled=False,
        default_language="en",
        available_languages=("ru", "en"),
    )


@pytest.fixture
def localization_service(
    mock_translations: dict[str, dict[str, str]],
    localization_config: LocalizationConfig,
) -> LocalizationService:
    """Сервис локализации с тестовыми данными."""
    return LocalizationService(
        translations=mock_translations,
        config=localization_config,
    )


@pytest.fixture
def localization_service_disabled(
    mock_translations: dict[str, dict[str, str]],
    localization_config_disabled: LocalizationConfig,
) -> LocalizationService:
    """Сервис локализации с отключённой мультиязычностью."""
    return LocalizationService(
        translations=mock_translations,
        config=localization_config_disabled,
    )


# ==============================================================================
# ФИКСТУРЫ — MOCK SESSION FACTORY
# ==============================================================================


@pytest.fixture
def mock_session() -> AsyncMock:
    """Мок AsyncSession."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_session_factory(mock_session: AsyncMock) -> SessionFactory:
    """Фабрика сессий — возвращает async context manager.

    Используется как dependency injection для LanguageMiddleware.
    """

    @asynccontextmanager
    async def _session_factory() -> AsyncGenerator[AsyncSession, None]:
        yield mock_session

    return _session_factory


@pytest.fixture
def mock_session_factory_error() -> SessionFactory:
    """Фабрика сессий, которая выбрасывает SQLAlchemyError."""

    @asynccontextmanager
    async def _session_factory() -> AsyncGenerator[AsyncSession, None]:
        raise SQLAlchemyError("Database connection error")
        yield  # Never executed, but required for generator

    return _session_factory


# ==============================================================================
# ФИКСТУРЫ — LOCALIZATION FACTORY
# ==============================================================================


@pytest.fixture
def mock_localization_factory(
    localization_service: LocalizationService,
) -> LocalizationFactory:
    """Фабрика Localization — создаёт объект локализации для языка.

    Используется как dependency injection для LanguageMiddleware.
    """

    def _factory(language: str) -> Localization:
        return Localization(language, localization_service)

    return _factory


@pytest.fixture
def mock_localization_factory_disabled(
    localization_service_disabled: LocalizationService,
) -> LocalizationFactory:
    """Фабрика Localization с отключённой мультиязычностью."""

    def _factory(language: str) -> Localization:
        return Localization(language, localization_service_disabled)

    return _factory


# ==============================================================================
# ФИКСТУРЫ — TELEGRAM ОБЪЕКТЫ
# ==============================================================================


@pytest.fixture
def mock_message() -> Message:
    """Мок Message с пользователем."""
    message = MagicMock(spec=Message)
    message.from_user = User(
        id=123456789,
        is_bot=False,
        first_name="Test User",
    )
    return message


@pytest.fixture
def mock_callback_query() -> CallbackQuery:
    """Мок CallbackQuery с пользователем."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(
        id=123456789,
        is_bot=False,
        first_name="Test User",
    )
    return callback


# ==============================================================================
# ФИКСТУРЫ — DB USERS
# ==============================================================================


@pytest.fixture
def mock_db_user_ru() -> DbUser:
    """Мок пользователя из БД с языком ru."""
    user = MagicMock(spec=DbUser)
    user.id = 1
    user.telegram_id = 123456789
    user.language = "ru"
    return user


@pytest.fixture
def mock_db_user_en() -> DbUser:
    """Мок пользователя из БД с языком en."""
    user = MagicMock(spec=DbUser)
    user.id = 2
    user.telegram_id = 987654321
    user.language = "en"
    return user


# ==============================================================================
# ТЕСТЫ БАЗОВОЙ ФУНКЦИОНАЛЬНОСТИ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_adds_l10n_to_data(
    mock_message: Message,
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory: LocalizationFactory,
    mock_db_user_ru: DbUser,
) -> None:
    """Тест: middleware добавляет объект Localization в data."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )

    data: dict[str, Any] = {"event_from_user": mock_message.from_user}

    # Патчим _get_user_language, чтобы не лезть в БД
    with patch.object(middleware, "_get_user_language", return_value="ru"):
        result = await middleware(mock_handler, mock_message, data)

    # Проверяем что Localization добавлен в data
    assert "l10n" in data
    assert isinstance(data["l10n"], Localization)
    assert data["l10n"].language == "ru"

    # Проверяем что handler был вызван
    mock_handler.assert_called_once_with(mock_message, data)
    assert result == "handler_result"


@pytest.mark.asyncio
async def test_middleware_uses_default_language_when_disabled(
    mock_message: Message,
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory_disabled: LocalizationFactory,
    localization_service_disabled: LocalizationService,
) -> None:
    """Тест: если мультиязычность отключена → используется default_language."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory_disabled,
    )

    data: dict[str, Any] = {"event_from_user": mock_message.from_user}

    # Патчим is_enabled(), чтобы вернуть False
    with (
        patch(
            "src.bot.middleware.language.Localization.is_enabled",
            return_value=False,
        ),
        patch(
            "src.bot.middleware.language.Localization.get_default_language",
            return_value="en",
        ),
    ):
        result = await middleware(mock_handler, mock_message, data)

    # Должен быть создан Localization с default_language
    assert "l10n" in data
    assert data["l10n"].language == "en"

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(mock_message, data)
    assert result == "handler_result"


# ==============================================================================
# ТЕСТЫ ПОЛУЧЕНИЯ ЯЗЫКА ИЗ БД
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_gets_language_from_database(
    mock_message: Message,
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory: LocalizationFactory,
    mock_db_user_ru: DbUser,
) -> None:
    """Тест: middleware получает язык пользователя из БД."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )

    data: dict[str, Any] = {"event_from_user": mock_message.from_user}

    # Мокируем UserRepository и Localization.is_enabled()
    with (
        patch("src.bot.middleware.language.Localization.is_enabled", return_value=True),
        patch("src.bot.middleware.language.UserRepository") as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user_ru)
        mock_repo_cls.return_value = mock_repo

        await middleware(mock_handler, mock_message, data)

        # Проверяем что репозиторий был вызван с правильным telegram_id
        assert mock_message.from_user is not None
        mock_repo.get_by_telegram_id.assert_called_once_with(mock_message.from_user.id)

    # Проверяем что язык взят из БД
    assert data["l10n"].language == "ru"


@pytest.mark.asyncio
async def test_middleware_falls_back_to_default_if_user_not_found(
    mock_message: Message,
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory: LocalizationFactory,
) -> None:
    """Тест: fallback на default_language если пользователь не найден в БД."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )

    data: dict[str, Any] = {"event_from_user": mock_message.from_user}

    with (
        patch("src.bot.middleware.language.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.middleware.language.Localization.get_default_language",
            return_value="ru",
        ),
    ):
        # Пользователь не найден (None)
        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=None)
        mock_repo_cls.return_value = mock_repo

        await middleware(mock_handler, mock_message, data)

    # Должен использовать default_language
    assert data["l10n"].language == "ru"


@pytest.mark.asyncio
async def test_middleware_falls_back_if_user_language_is_none(
    mock_message: Message,
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory: LocalizationFactory,
) -> None:
    """Тест: fallback на default_language если у пользователя language=None."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )

    data: dict[str, Any] = {"event_from_user": mock_message.from_user}

    # Создаём пользователя без языка
    user_without_language = MagicMock(spec=DbUser)
    user_without_language.language = None

    with (
        patch("src.bot.middleware.language.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.middleware.language.Localization.get_default_language",
            return_value="ru",
        ),
    ):
        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=user_without_language)
        mock_repo_cls.return_value = mock_repo

        await middleware(mock_handler, mock_message, data)

    # Должен использовать default_language
    assert data["l10n"].language == "ru"


# ==============================================================================
# ТЕСТЫ ОБРАБОТКИ СОБЫТИЙ БЕЗ ПОЛЬЗОВАТЕЛЯ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_handles_event_without_user(
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory: LocalizationFactory,
) -> None:
    """Тест: middleware обрабатывает события без пользователя."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )

    # Message без from_user
    message = MagicMock(spec=Message)
    message.from_user = None

    data: dict[str, Any] = {"event_from_user": None}

    with patch(
        "src.bot.middleware.language.Localization.get_default_language",
        return_value="ru",
    ):
        await middleware(mock_handler, message, data)

    # Должен использовать default_language
    assert data["l10n"].language == "ru"

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(message, data)


# ==============================================================================
# ТЕСТЫ ОБРАБОТКИ ОШИБОК
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_handles_database_error(
    mock_message: Message,
    mock_handler: AsyncMock,
    mock_session_factory_error: SessionFactory,
    mock_localization_factory: LocalizationFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: middleware обрабатывает ошибки БД и использует default_language."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory_error,
        localization_factory=mock_localization_factory,
    )

    data: dict[str, Any] = {"event_from_user": mock_message.from_user}

    with (
        patch("src.bot.middleware.language.Localization.is_enabled", return_value=True),
        patch(
            "src.bot.middleware.language.Localization.get_default_language",
            return_value="ru",
        ),
    ):
        await middleware(mock_handler, mock_message, data)

    # Должен использовать default_language
    assert data["l10n"].language == "ru"

    # Handler должен быть вызван (middleware не блокирует выполнение)
    mock_handler.assert_called_once_with(mock_message, data)

    # Должна быть ошибка в логах (ищем часть сообщения)
    assert any("Ошибка" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_middleware_handles_repository_error(
    mock_message: Message,
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory: LocalizationFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: middleware обрабатывает ошибки репозитория."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )

    data: dict[str, Any] = {"event_from_user": mock_message.from_user}

    with (
        patch("src.bot.middleware.language.Localization.is_enabled", return_value=True),
        patch("src.bot.middleware.language.UserRepository") as mock_repo_cls,
        patch(
            "src.bot.middleware.language.Localization.get_default_language",
            return_value="en",
        ),
    ):
        # Репозиторий выбрасывает SQLAlchemyError
        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(
            side_effect=SQLAlchemyError("Repository error")
        )
        mock_repo_cls.return_value = mock_repo

        await middleware(mock_handler, mock_message, data)

    # Должен использовать default_language
    assert data["l10n"].language == "en"

    # Handler должен быть вызван
    mock_handler.assert_called_once()

    # Должна быть ошибка в логах (ищем часть сообщения)
    assert any("Ошибка" in record.message for record in caplog.records)


# ==============================================================================
# ТЕСТЫ С РАЗНЫМИ ТИПАМИ СОБЫТИЙ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_works_with_callback_query(
    mock_callback_query: CallbackQuery,
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory: LocalizationFactory,
    mock_db_user_en: DbUser,
) -> None:
    """Тест: middleware работает с CallbackQuery."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )

    data: dict[str, Any] = {"event_from_user": mock_callback_query.from_user}

    with (
        patch("src.bot.middleware.language.Localization.is_enabled", return_value=True),
        patch("src.bot.middleware.language.UserRepository") as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_by_telegram_id = AsyncMock(return_value=mock_db_user_en)
        mock_repo_cls.return_value = mock_repo

        await middleware(mock_handler, mock_callback_query, data)

    # Язык должен быть взят из БД
    assert data["l10n"].language == "en"

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(mock_callback_query, data)


@pytest.mark.asyncio
async def test_middleware_different_users_have_different_languages(
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory: LocalizationFactory,
) -> None:
    """Тест: разные пользователи получают разные языки."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )

    # Пользователь 1 с русским языком
    user1_message = MagicMock(spec=Message)
    user1_message.from_user = User(id=111, is_bot=False, first_name="User 1")

    user1_db = MagicMock(spec=DbUser)
    user1_db.language = "ru"

    # Пользователь 2 с английским языком
    user2_message = MagicMock(spec=Message)
    user2_message.from_user = User(id=222, is_bot=False, first_name="User 2")

    user2_db = MagicMock(spec=DbUser)
    user2_db.language = "en"

    data1: dict[str, Any] = {"event_from_user": user1_message.from_user}
    data2: dict[str, Any] = {"event_from_user": user2_message.from_user}

    with (
        patch("src.bot.middleware.language.Localization.is_enabled", return_value=True),
        patch("src.bot.middleware.language.UserRepository") as mock_repo_cls,
    ):
        # Настраиваем репозиторий для возврата разных пользователей
        mock_repo = MagicMock()

        async def get_user_by_id(telegram_id: int) -> DbUser | None:
            if telegram_id == 111:
                return user1_db
            if telegram_id == 222:
                return user2_db
            return None

        mock_repo.get_by_telegram_id = get_user_by_id
        mock_repo_cls.return_value = mock_repo

        # Обрабатываем оба события
        await middleware(mock_handler, user1_message, data1)
        await middleware(mock_handler, user2_message, data2)

    # Проверяем что каждый получил свой язык
    assert data1["l10n"].language == "ru"
    assert data2["l10n"].language == "en"


# ==============================================================================
# ТЕСТЫ EDGE CASES
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_preserves_existing_data(
    mock_message: Message,
    mock_handler: AsyncMock,
    mock_session_factory: SessionFactory,
    mock_localization_factory: LocalizationFactory,
) -> None:
    """Тест: middleware не перезаписывает существующие данные в data."""
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )

    # data с уже существующими ключами
    data: dict[str, Any] = {
        "event_from_user": mock_message.from_user,
        "existing_key": "existing_value",
        "model_key": "gpt-4o",
    }

    with patch.object(middleware, "_get_user_language", return_value="ru"):
        await middleware(mock_handler, mock_message, data)

    # Существующие ключи должны остаться
    assert data["existing_key"] == "existing_value"
    assert data["model_key"] == "gpt-4o"

    # И добавлен новый ключ l10n
    assert "l10n" in data
    assert data["l10n"].language == "ru"
