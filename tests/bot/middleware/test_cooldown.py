"""Тесты для middleware cooldown.

Модуль тестирует:
- CooldownError (исключение при нарушении cooldown)
- GenerationCooldownMiddleware (middleware для контроля cooldowns)

Тестируемая функциональность:
1. CooldownError хранит атрибуты seconds_left и generation_type
2. Middleware инициализируется с правильными настройками
3. Middleware пропускает запросы без model_key
4. Middleware блокирует запросы в cooldown периоде
5. Middleware пропускает запросы после истечения cooldown
6. Middleware обновляет timestamp после успешного выполнения handler
7. Middleware не обновляет timestamp если handler выбросил исключение
8. Разные cooldowns для разных типов генерации
9. Cooldowns per-user (разные пользователи независимы)
10. Обработка отсутствия user_id
11. Обработка ошибок при определении generation_type
"""

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Message, User

from src.bot.middleware.cooldown import CooldownError, GenerationCooldownMiddleware
from src.config.yaml_config import (
    GenerationCooldowns,
    Limits,
    ModelConfig,
    YamlConfig,
)
from src.providers.ai.base import GenerationType
from src.services.ai_service import AIService, ModelNotFoundError

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def mock_config() -> YamlConfig:
    """Тестовая конфигурация с cooldowns."""
    return YamlConfig(
        models={
            "gpt-4o": ModelConfig(
                provider="openrouter",
                model_id="openai/gpt-4o",
                generation_type=GenerationType.CHAT,
                price_tokens=10,
            ),
            "dall-e": ModelConfig(
                provider="openrouter",
                model_id="openai/dall-e-3",
                generation_type=GenerationType.IMAGE,
                price_tokens=50,
            ),
            "tts-1": ModelConfig(
                provider="openrouter",
                model_id="openai/tts-1",
                generation_type=GenerationType.TTS,
                price_tokens=5,
            ),
        },
        limits=Limits(
            max_parallel_tasks_per_user=2,
            generation_cooldowns=GenerationCooldowns(
                chat=2,
                image=10,
                tts=5,
                stt=5,
            ),
        ),
    )


@pytest.fixture
def mock_ai_service() -> AIService:
    """Мок AI-сервиса для определения типа генерации."""
    service = MagicMock(spec=AIService)

    # Настраиваем get_generation_type для возврата правильных типов
    def get_generation_type_side_effect(model_key: str) -> str:
        type_map = {
            "gpt-4o": "chat",
            "dall-e": "image",
            "tts-1": "tts",
        }
        if model_key not in type_map:
            raise ModelNotFoundError(
                f"Модель '{model_key}' не найдена",
                model_key=model_key,
            )
        return type_map[model_key]

    service.get_generation_type.side_effect = get_generation_type_side_effect
    return service


@pytest.fixture
def mock_message() -> Message:
    """Мок Message с пользователем."""
    message = MagicMock(spec=Message)
    message.from_user = User(
        id=123456789,
        is_bot=False,
        first_name="Test User",
    )
    message.answer = AsyncMock()
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
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()
    return callback


@pytest.fixture
def mock_handler() -> Callable[
    [Message | CallbackQuery, dict[str, Any]], Awaitable[Any]
]:
    """Мок async handler."""
    return AsyncMock(return_value=None)


# ==============================================================================
# ТЕСТЫ COOLDOWN ERROR
# ==============================================================================


def test_cooldown_error_attributes() -> None:
    """Тест: CooldownError хранит seconds_left и generation_type."""
    error = CooldownError(seconds_left=5, generation_type="chat")

    assert error.seconds_left == 5
    assert error.generation_type == "chat"


def test_cooldown_error_message() -> None:
    """Тест: CooldownError формирует правильное сообщение."""
    error = CooldownError(seconds_left=10, generation_type="image")

    expected_message = "Подождите 10 сек перед следующим запросом (тип: image)"
    assert str(error) == expected_message


# ==============================================================================
# ТЕСТЫ ИНИЦИАЛИЗАЦИИ MIDDLEWARE
# ==============================================================================


def test_middleware_initialization(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
) -> None:
    """Тест: middleware инициализируется с правильными настройками."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    assert middleware._cooldowns == mock_config.limits.generation_cooldowns
    assert middleware._ai_service is mock_ai_service
    assert middleware._last_request == {}


# ==============================================================================
# ТЕСТЫ ПРОПУСКА ЗАПРОСОВ БЕЗ MODEL_KEY
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_passes_request_without_model_key(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware пропускает запросы без model_key."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    # Данные без model_key
    data: dict[str, Any] = {}

    result = await middleware(mock_handler, mock_message, data)

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(mock_message, data)
    assert result is mock_handler.return_value

    # Timestamp не должен обновиться
    assert middleware._last_request == {}


@pytest.mark.asyncio
async def test_middleware_passes_request_without_user_id(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware пропускает запросы без user_id."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    # Message без from_user
    message = MagicMock(spec=Message)
    message.from_user = None

    data: dict[str, Any] = {"model_key": "gpt-4o"}

    result = await middleware(mock_handler, message, data)

    # Handler должен быть вызван
    mock_handler.assert_called_once_with(message, data)
    assert result is mock_handler.return_value

    # Timestamp не должен обновиться
    assert middleware._last_request == {}


# ==============================================================================
# ТЕСТЫ БЛОКИРОВКИ В COOLDOWN ПЕРИОДЕ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_blocks_request_in_cooldown_period(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware блокирует запросы в cooldown периоде."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    data: dict[str, Any] = {"model_key": "gpt-4o"}

    # Первый запрос — успешен
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, mock_message, data)

    # Второй запрос через 1 секунду — заблокирован (cooldown = 2 сек)
    with patch("src.bot.middleware.cooldown.time.time", return_value=1001.0):
        with pytest.raises(CooldownError) as exc_info:
            await middleware(mock_handler, mock_message, data)

    # Проверяем параметры ошибки
    error = exc_info.value
    assert error.generation_type == "chat"
    # seconds_left = int(2 - 1) + 1 = 2
    assert error.seconds_left == 2


@pytest.mark.asyncio
async def test_middleware_blocks_with_correct_seconds_left(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware правильно вычисляет seconds_left."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    data: dict[str, Any] = {"model_key": "gpt-4o"}  # cooldown = 2 сек

    # Первый запрос
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, mock_message, data)

    # Второй запрос через 0.5 секунд
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.5):
        with pytest.raises(CooldownError) as exc_info:
            await middleware(mock_handler, mock_message, data)

    # seconds_left = int(2.0 - 0.5) + 1 = int(1.5) + 1 = 2
    assert exc_info.value.seconds_left == 2


# ==============================================================================
# ТЕСТЫ ПРОПУСКА ПОСЛЕ ИСТЕЧЕНИЯ COOLDOWN
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_passes_request_after_cooldown_expires(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware пропускает запросы после истечения cooldown."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    data: dict[str, Any] = {"model_key": "gpt-4o"}  # cooldown = 2 сек

    # Первый запрос
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, mock_message, data)

    # Второй запрос через 2 секунды — должен пройти
    with patch("src.bot.middleware.cooldown.time.time", return_value=1002.0):
        result = await middleware(mock_handler, mock_message, data)

    # Handler должен быть вызван дважды
    assert mock_handler.call_count == 2
    assert result is mock_handler.return_value


@pytest.mark.asyncio
async def test_middleware_passes_request_just_after_cooldown(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware пропускает запрос ровно в момент истечения cooldown."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    data: dict[str, Any] = {"model_key": "gpt-4o"}  # cooldown = 2 сек

    # Первый запрос
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, mock_message, data)

    # Второй запрос ровно через 2.0 секунды
    with patch("src.bot.middleware.cooldown.time.time", return_value=1002.0):
        result = await middleware(mock_handler, mock_message, data)

    assert result is mock_handler.return_value
    assert mock_handler.call_count == 2


# ==============================================================================
# ТЕСТЫ ОБНОВЛЕНИЯ TIMESTAMP
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_updates_timestamp_after_successful_handler(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware обновляет timestamp после успешного handler."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    data: dict[str, Any] = {"model_key": "gpt-4o"}
    assert mock_message.from_user is not None
    user_id = mock_message.from_user.id
    key = (user_id, "chat")

    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, mock_message, data)

    # Timestamp должен быть установлен
    assert key in middleware._last_request
    assert middleware._last_request[key] == 1000.0


@pytest.mark.asyncio
async def test_middleware_does_not_update_timestamp_on_handler_exception(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
) -> None:
    """Тест: middleware не обновляет timestamp если handler выбросил исключение."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    # Handler который выбрасывает исключение
    failing_handler = AsyncMock(side_effect=ValueError("Handler error"))

    data: dict[str, Any] = {"model_key": "gpt-4o"}
    assert mock_message.from_user is not None
    user_id = mock_message.from_user.id
    key = (user_id, "chat")

    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        with pytest.raises(ValueError, match="Handler error"):
            await middleware(failing_handler, mock_message, data)

    # Timestamp НЕ должен быть установлен
    assert key not in middleware._last_request


@pytest.mark.asyncio
async def test_middleware_updates_timestamp_to_current_time_on_second_call(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware обновляет timestamp при повторном вызове."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    data: dict[str, Any] = {"model_key": "gpt-4o"}
    assert mock_message.from_user is not None
    user_id = mock_message.from_user.id
    key = (user_id, "chat")

    # Первый вызов
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, mock_message, data)

    assert middleware._last_request[key] == 1000.0

    # Второй вызов через 5 секунд
    with patch("src.bot.middleware.cooldown.time.time", return_value=1005.0):
        await middleware(mock_handler, mock_message, data)

    # Timestamp обновлён
    assert middleware._last_request[key] == 1005.0


# ==============================================================================
# ТЕСТЫ РАЗНЫХ ТИПОВ ГЕНЕРАЦИИ
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_different_cooldowns_for_different_types(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: разные cooldowns для разных типов генерации."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    # chat: cooldown = 2 сек
    chat_data: dict[str, Any] = {"model_key": "gpt-4o"}

    # image: cooldown = 10 сек
    image_data: dict[str, Any] = {"model_key": "dall-e"}

    # Запрос chat в момент 1000.0
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, mock_message, chat_data)

    # Запрос image в момент 1001.0 (через 1 сек)
    with patch("src.bot.middleware.cooldown.time.time", return_value=1001.0):
        await middleware(mock_handler, mock_message, image_data)

    # Второй запрос chat в момент 1002.0 (через 2 сек от первого) — должен пройти
    with patch("src.bot.middleware.cooldown.time.time", return_value=1002.0):
        await middleware(mock_handler, mock_message, chat_data)

    # Второй запрос image в момент 1005.0 (через 4 сек от первого) — заблокирован
    with patch("src.bot.middleware.cooldown.time.time", return_value=1005.0):
        with pytest.raises(CooldownError) as exc_info:
            await middleware(mock_handler, mock_message, image_data)

    assert exc_info.value.generation_type == "image"


@pytest.mark.asyncio
async def test_middleware_different_generation_types_have_independent_cooldowns(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: разные типы генерации имеют независимые cooldowns."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    assert mock_message.from_user is not None
    user_id = mock_message.from_user.id

    # Запускаем chat генерацию
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(
            mock_handler,
            mock_message,
            {"model_key": "gpt-4o"},
        )

    # Сразу после этого можем запустить TTS генерацию (независимый cooldown)
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.5):
        await middleware(
            mock_handler,
            mock_message,
            {"model_key": "tts-1"},
        )

    # Проверяем что оба timestamp установлены
    assert (user_id, "chat") in middleware._last_request
    assert (user_id, "tts") in middleware._last_request


# ==============================================================================
# ТЕСТЫ COOLDOWNS PER-USER
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_cooldowns_are_per_user(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_handler: AsyncMock,
) -> None:
    """Тест: cooldowns независимы для разных пользователей."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    # Создаём двух пользователей
    user1_message = MagicMock(spec=Message)
    user1_message.from_user = User(id=111, is_bot=False, first_name="User 1")

    user2_message = MagicMock(spec=Message)
    user2_message.from_user = User(id=222, is_bot=False, first_name="User 2")

    data: dict[str, Any] = {"model_key": "gpt-4o"}

    # User 1 делает запрос
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, user1_message, data)

    # User 2 сразу делает запрос — должен пройти (независимый cooldown)
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.5):
        await middleware(mock_handler, user2_message, data)

    # User 1 делает второй запрос через 1 сек — заблокирован
    with patch("src.bot.middleware.cooldown.time.time", return_value=1001.0):
        with pytest.raises(CooldownError):
            await middleware(mock_handler, user1_message, data)

    # User 2 делает второй запрос через 1 сек — заблокирован
    with patch("src.bot.middleware.cooldown.time.time", return_value=1001.5):
        with pytest.raises(CooldownError):
            await middleware(mock_handler, user2_message, data)


# ==============================================================================
# ТЕСТЫ ОБРАБОТКИ ОШИБОК
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_passes_request_on_unknown_model(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware пропускает запрос если модель неизвестна."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    # model_key, которого нет в ai_service (вызовет ModelNotFoundError)
    data: dict[str, Any] = {"model_key": "unknown-model"}

    result = await middleware(mock_handler, mock_message, data)

    # Handler должен быть вызван (middleware не блокирует)
    mock_handler.assert_called_once_with(mock_message, data)
    assert result is mock_handler.return_value

    # Timestamp не должен обновиться (т.к. тип генерации не определён)
    assert middleware._last_request == {}


@pytest.mark.asyncio
async def test_middleware_logs_warning_on_unknown_model(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_handler: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: middleware логирует предупреждение при неизвестной модели."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    data: dict[str, Any] = {"model_key": "unknown-model"}

    await middleware(mock_handler, mock_message, data)

    # Проверяем что есть лог-сообщение
    assert any(
        "Не удалось определить тип генерации" in record.message
        for record in caplog.records
    )


# ==============================================================================
# ТЕСТЫ С CALLBACK QUERY
# ==============================================================================


@pytest.mark.asyncio
async def test_middleware_works_with_callback_query(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_callback_query: CallbackQuery,
    mock_handler: AsyncMock,
) -> None:
    """Тест: middleware работает с CallbackQuery."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    data: dict[str, Any] = {"model_key": "gpt-4o"}

    # Первый запрос через callback
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, mock_callback_query, data)

    # Второй запрос через 1 сек — заблокирован
    with patch("src.bot.middleware.cooldown.time.time", return_value=1001.0):
        with pytest.raises(CooldownError):
            await middleware(mock_handler, mock_callback_query, data)


@pytest.mark.asyncio
async def test_middleware_shares_cooldown_between_message_and_callback(
    mock_config: YamlConfig,
    mock_ai_service: AIService,
    mock_message: Message,
    mock_callback_query: CallbackQuery,
    mock_handler: AsyncMock,
) -> None:
    """Тест: cooldown общий для Message и CallbackQuery одного пользователя."""
    middleware = GenerationCooldownMiddleware(mock_config, mock_ai_service)

    # Важно: оба события от одного пользователя
    assert mock_message.from_user is not None
    assert mock_callback_query.from_user is not None
    assert mock_message.from_user.id == mock_callback_query.from_user.id

    data: dict[str, Any] = {"model_key": "gpt-4o"}

    # Запрос через Message
    with patch("src.bot.middleware.cooldown.time.time", return_value=1000.0):
        await middleware(mock_handler, mock_message, data)

    # Запрос через CallbackQuery через 1 сек — заблокирован
    with patch("src.bot.middleware.cooldown.time.time", return_value=1001.0):
        with pytest.raises(CooldownError):
            await middleware(mock_handler, mock_callback_query, data)
