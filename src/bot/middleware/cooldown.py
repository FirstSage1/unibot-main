"""Middleware для контроля cooldowns между генерациями.

Этот модуль реализует защиту от спама и случайных двойных запросов.
Пользователь не может запустить новую генерацию определённого типа,
пока не пройдёт минимальный интервал (cooldown) с предыдущей.

Cooldowns настраиваются в config.yaml для каждого типа генерации:
- chat: 2 секунды (защита от двойных кликов)
- image: 10 секунд (генерация изображений дороже)
- tts: 5 секунд (озвучка занимает время)
- stt: 5 секунд (распознавание речи)

Пример использования:
    from aiogram import Dispatcher
    from src.config.yaml_config import yaml_config
    from src.services.ai_service import create_ai_service

    dp = Dispatcher()
    ai_service = create_ai_service()

    # Регистрируем middleware
    cooldown_middleware = GenerationCooldownMiddleware(yaml_config, ai_service)
    dp.message.middleware(cooldown_middleware)
    dp.callback_query.middleware(cooldown_middleware)
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from typing_extensions import override

from src.config.yaml_config import YamlConfig
from src.core.exceptions import CooldownError, ModelNotFoundError
from src.services.ai_service import AIService
from src.utils.logging import get_logger

logger = get_logger(__name__)


class GenerationCooldownMiddleware(BaseMiddleware):
    """Middleware для контроля cooldowns между генерациями.

    Отслеживает время последней генерации каждого типа для каждого пользователя.
    Блокирует запросы, если не прошёл минимальный интервал (cooldown).

    Middleware проверяет наличие model_key в data словаре. Если model_key есть,
    определяет тип генерации и проверяет cooldown. Если model_key отсутствует,
    запрос считается не связанным с генерацией и пропускается.

    Attributes:
        _cooldowns: Настройки cooldowns для каждого типа генерации.
        _ai_service: Сервис для определения типа генерации по model_key.
        _last_request: Словарь {(user_id, generation_type): timestamp}.
    """

    def __init__(
        self,
        config: YamlConfig,
        ai_service: AIService,
    ) -> None:
        """Создать middleware для cooldowns.

        Args:
            config: YAML-конфигурация с настройками cooldowns.
            ai_service: AI-сервис для определения типа генерации.
        """
        self._cooldowns = config.limits.generation_cooldowns
        self._ai_service = ai_service

        # Храним время последнего запроса: {(user_id, generation_type): timestamp}
        self._last_request: dict[tuple[int, str], float] = {}

    @override
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Проверить cooldown перед выполнением handler.

        Args:
            handler: Обработчик события (следующий в цепочке middleware).
            event: Входящее событие (TelegramObject, обычно Message или CallbackQuery).
            data: Словарь данных, передаваемых в handler.

        Returns:
            Результат выполнения handler.

        Raises:
            CooldownError: Если cooldown ещё не истёк.
        """
        # Получаем user_id (проверяем наличие атрибута from_user)
        user_id = (
            event.from_user.id
            if hasattr(event, "from_user") and event.from_user
            else None
        )
        if user_id is None:
            # Без user_id невозможно отследить cooldown
            return await handler(event, data)

        # Получаем model_key из данных (handler должен установить)
        model_key = data.get("model_key")

        if model_key is None:
            # Если нет model_key — это не генерация, пропускаем
            return await handler(event, data)

        # Определяем тип генерации
        try:
            generation_type = self._ai_service.get_generation_type(model_key)
        except ModelNotFoundError as e:
            # Если модель не найдена — пусть handler сам обработает ошибку
            logger.warning(
                "Не удалось определить тип генерации для model_key=%s: %s",
                model_key,
                e,
            )
            return await handler(event, data)

        # Проверяем cooldown
        key = (user_id, generation_type)
        current_time = time.time()

        if key in self._last_request:
            elapsed = current_time - self._last_request[key]
            cooldown_seconds = getattr(self._cooldowns, generation_type)

            if elapsed < cooldown_seconds:
                seconds_left = int(cooldown_seconds - elapsed) + 1
                logger.info(
                    "Cooldown: user_id=%d, type=%s, осталось=%d сек",
                    user_id,
                    generation_type,
                    seconds_left,
                )
                raise CooldownError(seconds_left, generation_type)

        # Выполняем handler
        result = await handler(event, data)

        # Обновляем timestamp ТОЛЬКО если генерация прошла успешно.
        # Используем time.time() ещё раз, чтобы учесть время выполнения handler.
        # Если handler выбросил исключение — эта строка не выполнится.
        self._last_request[key] = time.time()
        logger.debug(
            "Cooldown обновлён: user_id=%d, type=%s",
            user_id,
            generation_type,
        )

        return result
