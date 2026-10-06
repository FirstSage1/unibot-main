"""Утилиты проверки cooldown для генераций AI.

Этот модуль содержит логику проверки минимального интервала между генерациями.
Используется вместо middleware для большей гибкости и явного контроля.

Преимущества database-based cooldown:
- Персистентность (переживает перезапуски бота)
- Синхронизация между воркерами (если запущено несколько экземпляров)
- Аудит всех генераций в БД

Паттерн использования:
1. check_generation_cooldown() — перед запуском генерации
2. create_generation_record() — при запуске генерации
3. ... выполнить генерацию ...
4. update_generation_status() — после завершения

Example:
    # В handler перед генерацией
    try:
        await check_generation_cooldown(
            generation_repo=generation_repo,
            user_id=user.id,
            generation_type="image",
            config=yaml_config,
        )
    except CooldownError as e:
        await message.reply(f"⏳ {e}")
        return

    # Создать запись о генерации
    generation = await generation_repo.create_generation(
        user_id=user.id,
        generation_type="image",
        model_key="flux-dev",
    )

    # Выполнить генерацию
    result = await ai_service.generate(...)

    # Обновить статус
    await generation_repo.update_generation_status(
        generation.id,
        (
            GenerationDBStatus.COMPLETED
            if result.is_success
            else GenerationDBStatus.FAILED
        ),
    )
"""

from datetime import UTC, datetime

from src.config.yaml_config import YamlConfig
from src.core.exceptions import CooldownError, TooManyGenerationsError
from src.db.repositories.generation_repo import GenerationRepository
from src.utils.logging import get_logger
from src.utils.timezone import ensure_utc_aware

__all__ = [
    "CooldownError",
    "TooManyGenerationsError",
    "check_generation_cooldown",
    "check_parallel_generations_limit",
]

logger = get_logger(__name__)


async def check_generation_cooldown(
    generation_repo: GenerationRepository,
    user_id: int,
    generation_type: str,
    config: YamlConfig,
) -> None:
    """Проверить cooldown перед запуском генерации.

    Проверяет время с последней генерации этого типа и выбрасывает
    CooldownError, если прошло недостаточно времени.

    Cooldown настраивается для каждого типа генерации в config.yaml:
    - chat: 2 секунды (защита от двойных кликов)
    - image: 10 секунд (генерация изображений дороже)
    - tts: 5 секунд (озвучка занимает время)
    - stt: 5 секунд (распознавание речи)

    Args:
        generation_repo: Репозиторий для работы с генерациями.
        user_id: ID пользователя.
        generation_type: Тип генерации (chat, image, image_edit, tts, stt).
        config: YAML-конфигурация с настройками cooldowns.

    Raises:
        CooldownError: Если cooldown ещё не истёк.

    Example:
        await check_generation_cooldown(
            generation_repo=generation_repo,
            user_id=user.id,
            generation_type="image",
            config=yaml_config,
        )
    """
    # Получаем настройку cooldown для этого типа генерации
    cooldown_seconds = getattr(config.limits.generation_cooldowns, generation_type, 0)

    # Если cooldown = 0, проверка не нужна (отключена для этого типа)
    if cooldown_seconds == 0:
        logger.debug(
            "Cooldown отключён: user_id=%d, type=%s",
            user_id,
            generation_type,
        )
        return

    # Получаем последнюю генерацию этого типа
    last_generation = await generation_repo.get_last_generation(
        user_id=user_id,
        generation_type=generation_type,
    )

    # Если это первая генерация — cooldown не нужен
    if last_generation is None:
        logger.debug(
            "Первая генерация: user_id=%d, type=%s",
            user_id,
            generation_type,
        )
        return

    # Проверяем прошедшее время
    # Нормализуем datetime из БД как UTC (SQLite/PostgreSQL возвращают naive datetime)
    created_at_utc = ensure_utc_aware(last_generation.created_at)
    elapsed_seconds = (datetime.now(UTC) - created_at_utc).total_seconds()

    if elapsed_seconds < cooldown_seconds:
        seconds_left = int(cooldown_seconds - elapsed_seconds) + 1
        logger.info(
            "Cooldown: user_id=%d, type=%s, осталось=%d сек",
            user_id,
            generation_type,
            seconds_left,
        )
        raise CooldownError(seconds_left, generation_type)

    logger.debug(
        "Cooldown прошёл: user_id=%d, type=%s, прошло=%.1f сек",
        user_id,
        generation_type,
        elapsed_seconds,
    )


async def check_parallel_generations_limit(
    generation_repo: GenerationRepository,
    user_id: int,
    config: YamlConfig,
) -> None:
    """Проверить лимит параллельных генераций для пользователя.

    Подсчитывает количество активных (PENDING) генераций и выбрасывает
    TooManyGenerationsError, если превышен лимит.

    Лимит настраивается в config.yaml:
        limits:
          max_parallel_tasks_per_user: 2  # по умолчанию

    Args:
        generation_repo: Репозиторий для работы с генерациями.
        user_id: ID пользователя.
        config: YAML-конфигурация с настройками лимитов.

    Raises:
        TooManyGenerationsError: Если превышен лимит параллельных задач.

    Example:
        await check_parallel_generations_limit(
            generation_repo=generation_repo,
            user_id=user.id,
            config=yaml_config,
        )
    """
    max_parallel = config.limits.max_parallel_tasks_per_user
    current_count = await generation_repo.count_pending_generations(user_id)

    if current_count >= max_parallel:
        logger.warning(
            "Превышен лимит генераций: user_id=%d, current=%d, max=%d",
            user_id,
            current_count,
            max_parallel,
        )
        raise TooManyGenerationsError(current_count, max_parallel)

    logger.debug(
        "Лимит генераций в норме: user_id=%d, current=%d, max=%d",
        user_id,
        current_count,
        max_parallel,
    )
