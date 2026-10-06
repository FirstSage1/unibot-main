"""Тесты для middleware проверки согласия с юридическими документами.

Модуль тестирует:
- LegalConsentMiddleware (middleware для проверки согласия)

Тестируемая функциональность:
1. Middleware пропускает пользователей, принявших актуальную версию
2. Middleware блокирует пользователей без согласия
3. Middleware блокирует пользователей с устаревшей версией
4. Кеширование результата проверки
5. TTL кеша — устаревший кеш перепроверяется
6. Callback "legal:accept" проходит без проверки
7. Обработка событий без пользователя
8. clear_cache() очищает кеш
9. update_cache() обновляет кеш после принятия

Архитектура тестов:
- Используем Dependency Injection — инжектируем mock legal_config и time_provider
- БД мокается через patch _get_or_create_user_and_check
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Message, User

from src.bot.middleware.legal_consent import (
    CALLBACK_LEGAL_ACCEPT,
    LegalConsentMiddleware,
)
from src.utils.i18n import Localization

# ==============================================================================
# ФИКСТУРЫ — БАЗОВЫЕ ОБЪЕКТЫ
# ==============================================================================


@pytest.fixture
def mock_handler() -> AsyncMock:
    """Мок async handler."""
    return AsyncMock(return_value="handler_result")


@pytest.fixture
def mock_time_provider() -> MagicMock:
    """Мок провайдера времени для детерминированного тестирования кеша."""
    mock = MagicMock()
    mock.return_value = 1000.0
    return mock


@pytest.fixture
def mock_legal_config() -> MagicMock:
    """Мок конфигурации юридических документов."""
    config = MagicMock()
    config.enabled = True
    config.version = "1.0"
    config.privacy_policy_url = "https://example.com/privacy"
    config.terms_of_service_url = "https://example.com/terms"
    config.has_documents.return_value = True
    return config


# ==============================================================================
# ФИКСТУРЫ — TELEGRAM ОБЪЕКТЫ
# ==============================================================================


@pytest.fixture
def mock_user() -> User:
    """Тестовый пользователь."""
    return User(
        id=123456789,
        is_bot=False,
        first_name="Test User",
    )


@pytest.fixture
def mock_message(mock_user: User) -> Message:
    """Мок Message с пользователем."""
    message = MagicMock(spec=Message)
    message.from_user = mock_user
    message.answer = AsyncMock()
    return message


@pytest.fixture
def mock_callback_query(mock_user: User) -> CallbackQuery:
    """Мок CallbackQuery с пользователем."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = mock_user
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    callback.data = None
    return callback


@pytest.fixture
def mock_localization() -> MagicMock:
    """Мок объекта локализации."""
    l10n = MagicMock(spec=Localization)
    l10n.get.side_effect = lambda key: {
        "legal_acceptance_request": "Примите условия использования",
    }.get(key, f"[{key}]")
    return l10n


# ==============================================================================
# ФИКСТУРЫ — МОКАЕМ БД
# ==============================================================================


@pytest.fixture
def mock_db_user_accepted() -> MagicMock:
    """Мок пользователя, который принял актуальную версию."""
    user = MagicMock()
    user.accepted_legal_version = "1.0"
    return user


@pytest.fixture
def mock_db_user_outdated() -> MagicMock:
    """Мок пользователя с устаревшей версией."""
    user = MagicMock()
    user.accepted_legal_version = "0.9"
    return user


@pytest.fixture
def mock_db_user_not_accepted() -> MagicMock:
    """Мок пользователя, который не принимал условия."""
    user = MagicMock()
    user.accepted_legal_version = None
    return user


# ==============================================================================
# ТЕСТЫ — ПОЛЬЗОВАТЕЛИ С СОГЛАСИЕМ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_passes_user_with_consent(
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
    mock_db_user_accepted: MagicMock,
) -> None:
    """Тест: middleware пропускает пользователей с актуальным согласием."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Мокаем запрос к БД (get_or_create + проверка версии)
    with patch.object(
        middleware,
        "_get_or_create_user_and_check",
        return_value="1.0",
    ):
        result = await middleware(mock_handler, mock_message, data)

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(mock_message, data)
    assert result == "handler_result"


# ==============================================================================
# ТЕСТЫ — ПОЛЬЗОВАТЕЛИ БЕЗ СОГЛАСИЯ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_blocks_user_without_consent(
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: middleware блокирует пользователей без согласия."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Мокаем запрос к БД — пользователь не принимал условия
    with patch.object(
        middleware,
        "_get_or_create_user_and_check",
        return_value=None,
    ):
        result = await middleware(mock_handler, mock_message, data)

    # Handler НЕ должен быть вызван
    mock_handler.assert_not_called()
    assert result is None

    # Должно быть отправлено сообщение
    mock_message.answer.assert_called_once()


@pytest.mark.asyncio
async def test_middleware_blocks_user_with_outdated_version(
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: middleware блокирует пользователей с устаревшей версией согласия."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Мокаем запрос к БД — пользователь принял старую версию
    with patch.object(
        middleware,
        "_get_or_create_user_and_check",
        return_value="0.9",  # Устаревшая версия
    ):
        result = await middleware(mock_handler, mock_message, data)

    # Handler НЕ должен быть вызван
    mock_handler.assert_not_called()
    assert result is None


# ==============================================================================
# ТЕСТЫ — CALLBACK "legal:accept"
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_passes_legal_accept_callback(
    mock_handler: AsyncMock,
    mock_callback_query: CallbackQuery,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: middleware пропускает callback legal:accept без проверки."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        time_provider=mock_time_provider,
    )

    # Устанавливаем callback_data
    mock_callback_query.data = CALLBACK_LEGAL_ACCEPT

    data: dict[str, Any] = {"event_from_user": mock_user}

    # НЕ мокаем БД — она не должна вызываться
    result = await middleware(mock_handler, mock_callback_query, data)

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(mock_callback_query, data)
    assert result == "handler_result"


# ==============================================================================
# ТЕСТЫ — КЕШИРОВАНИЕ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_caches_consent_result(
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: middleware кеширует результат проверки согласия."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    # Создаём мок для отслеживания вызовов
    mock_get_version = AsyncMock(return_value="1.0")

    with patch.object(
        middleware,
        "_get_or_create_user_and_check",
        mock_get_version,
    ):
        # Первый запрос — вызов БД
        await middleware(mock_handler, mock_message, data)
        assert mock_get_version.call_count == 1

        # Второй запрос — из кеша
        await middleware(mock_handler, mock_message, data)
        assert mock_get_version.call_count == 1  # Не изменилось!


@pytest.mark.asyncio
async def test_middleware_cache_expires(
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: middleware перепроверяет при истечении TTL кеша."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {"event_from_user": mock_user}

    mock_get_version = AsyncMock(return_value="1.0")

    with patch.object(
        middleware,
        "_get_or_create_user_and_check",
        mock_get_version,
    ):
        # Первый запрос — время 1000
        mock_time_provider.return_value = 1000.0
        await middleware(mock_handler, mock_message, data)
        assert mock_get_version.call_count == 1

        # Второй запрос — время 1100 (кеш ещё валиден)
        mock_time_provider.return_value = 1100.0
        await middleware(mock_handler, mock_message, data)
        assert mock_get_version.call_count == 1

        # Третий запрос — время 1400 (кеш истёк, TTL=300)
        mock_time_provider.return_value = 1400.0
        await middleware(mock_handler, mock_message, data)
        assert mock_get_version.call_count == 2


# ==============================================================================
# ТЕСТЫ — СОБЫТИЯ БЕЗ ПОЛЬЗОВАТЕЛЯ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_passes_event_without_user(
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: middleware пропускает события без пользователя."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        time_provider=mock_time_provider,
    )

    # data без event_from_user
    data: dict[str, Any] = {}

    result = await middleware(mock_handler, mock_message, data)

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(mock_message, data)
    assert result == "handler_result"


# ==============================================================================
# ТЕСТЫ — УПРАВЛЕНИЕ КЕШЕМ
# ==============================================================================


def test_clear_cache_single_user(
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: clear_cache очищает кеш для одного пользователя."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        time_provider=mock_time_provider,
    )

    # Вручную добавляем записи в кеш
    middleware._cache[123] = ("1.0", 2000.0)
    middleware._cache[456] = ("1.0", 2000.0)

    # Очищаем для одного пользователя
    middleware.clear_cache(123)

    assert 123 not in middleware._cache
    assert 456 in middleware._cache


def test_clear_cache_all(
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: clear_cache(None) очищает весь кеш."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        time_provider=mock_time_provider,
    )

    # Вручную добавляем записи в кеш
    middleware._cache[123] = ("1.0", 2000.0)
    middleware._cache[456] = ("1.0", 2000.0)

    # Очищаем весь кеш
    middleware.clear_cache(None)

    assert len(middleware._cache) == 0


def test_update_cache(
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: update_cache обновляет кеш после принятия условий."""
    mock_time_provider.return_value = 1000.0

    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        cache_ttl_seconds=300,
        time_provider=mock_time_provider,
    )

    # Обновляем кеш
    middleware.update_cache(123, "1.0")

    assert 123 in middleware._cache
    accepted_version, expires_at = middleware._cache[123]
    assert accepted_version == "1.0"
    assert expires_at == 1300.0  # 1000 + 300


# ==============================================================================
# ТЕСТЫ — ЛОКАЛИЗАЦИЯ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_sends_localized_message(
    mock_handler: AsyncMock,
    mock_message: Message,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
    mock_localization: MagicMock,
) -> None:
    """Тест: middleware отправляет локализованное сообщение."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        time_provider=mock_time_provider,
    )

    data: dict[str, Any] = {
        "event_from_user": mock_user,
        "l10n": mock_localization,
    }

    with patch.object(
        middleware,
        "_get_or_create_user_and_check",
        return_value=None,
    ):
        await middleware(mock_handler, mock_message, data)

    # Должно быть отправлено сообщение
    mock_message.answer.assert_called_once()
    call_args = mock_message.answer.call_args
    # Проверяем что текст взят из локализации
    assert "Примите условия использования" in call_args[0][0]


# ==============================================================================
# ТЕСТЫ — CALLBACK QUERY
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_blocks_callback_without_consent(
    mock_handler: AsyncMock,
    mock_callback_query: CallbackQuery,
    mock_user: User,
    mock_time_provider: MagicMock,
    mock_legal_config: MagicMock,
) -> None:
    """Тест: middleware блокирует callback от пользователя без согласия."""
    middleware = LegalConsentMiddleware(
        legal_config=mock_legal_config,
        time_provider=mock_time_provider,
    )

    mock_callback_query.data = "some_other_callback"

    data: dict[str, Any] = {"event_from_user": mock_user}

    with patch.object(
        middleware,
        "_get_or_create_user_and_check",
        return_value=None,
    ):
        result = await middleware(mock_handler, mock_callback_query, data)

    # Handler НЕ должен быть вызван
    mock_handler.assert_not_called()
    assert result is None

    # Должен быть вызван callback.answer и отправлено сообщение
    mock_callback_query.answer.assert_called_once()
    mock_callback_query.message.answer.assert_called_once()
