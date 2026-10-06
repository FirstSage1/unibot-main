"""Настройка и инициализация компонентов бота.

Этот модуль содержит функции для настройки диспетчера:
- setup_middlewares() — регистрация middleware (язык, подписка на канал, cooldown)
- setup_error_handlers() — обработчики ошибок (CooldownError)
- setup_handlers() — подключение роутеров

Использование:
    from src.bot.setup import setup_bot

    dp = create_dispatcher(settings.fsm)
    setup_bot(dp, yaml_config, ai_service, bot, settings.channel)
"""

from typing import TYPE_CHECKING

from aiogram import Dispatcher
from aiogram.types import ErrorEvent

from src.bot.handlers import get_main_router
from src.bot.middleware import (
    ChannelSubscriptionMiddleware,
    CooldownError,
    GenerationCooldownMiddleware,
    LegalConsentMiddleware,
    PrivateChatMiddleware,
    create_language_middleware,
)
from src.config.yaml_config import YamlConfig
from src.services.ai_service import AIService
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from aiogram import Bot

    from src.config.models import ChannelSettings

logger = get_logger(__name__)


def setup_middlewares(
    dp: Dispatcher,
    yaml_config: YamlConfig,
    ai_service: AIService,
    bot: "Bot",
    channel_settings: "ChannelSettings",
) -> None:
    """Зарегистрировать middleware в диспетчере.

    Порядок регистрации важен:
    1. PrivateChatMiddleware — фильтрует сообщения только из личных чатов (ПЕРВЫЙ!)
    2. LanguageMiddleware — определяет язык пользователя
    3. ChannelSubscriptionMiddleware — проверяет подписку на канал (требует l10n)
    4. LegalConsentMiddleware — проверяет согласие с юр. документами (требует l10n)
    5. GenerationCooldownMiddleware — контролирует cooldown между генерациями

    Args:
        dp: Диспетчер aiogram.
        yaml_config: Конфигурация из YAML (содержит настройки cooldown и кеша подписки).
        ai_service: AI-сервис для проверки активных генераций.
        bot: Экземпляр Telegram-бота (для проверки подписки).
        channel_settings: Настройки обязательной подписки на канал.
    """
    # Список зарегистрированных middleware для логирования
    registered_middlewares: list[str] = []

    # PrivateChatMiddleware должен быть ПЕРВЫМ — игнорирует сообщения из групп/каналов
    # Согласно PRD 4.5: "Бот работает только в личных сообщениях"
    private_chat_middleware = PrivateChatMiddleware()
    dp.message.middleware(private_chat_middleware)
    dp.callback_query.middleware(private_chat_middleware)
    registered_middlewares.append("PrivateChatMiddleware")

    # LanguageMiddleware — определяет язык до выполнения handlers
    language_middleware = create_language_middleware()
    dp.message.middleware(language_middleware)
    dp.callback_query.middleware(language_middleware)
    registered_middlewares.append("LanguageMiddleware")

    # ChannelSubscriptionMiddleware — проверяет подписку на обязательный канал
    # Регистрируем только если задан CHANNEL__REQUIRED_ID
    if channel_settings.is_enabled:
        # Type narrowing: is_enabled гарантирует что required_id не None
        assert channel_settings.required_id is not None

        channel_middleware = ChannelSubscriptionMiddleware(
            bot=bot,
            channel_id=channel_settings.required_id,
            invite_link=channel_settings.invite_link,
            cache_ttl_seconds=yaml_config.channel_subscription.cache_ttl_seconds,
        )
        dp.message.middleware(channel_middleware)
        dp.callback_query.middleware(channel_middleware)
        registered_middlewares.append("ChannelSubscriptionMiddleware")
        logger.info(
            "Проверка подписки на канал включена: channel_id=%d, cache_ttl=%d сек",
            channel_settings.required_id,
            yaml_config.channel_subscription.cache_ttl_seconds,
        )

    # LegalConsentMiddleware — проверяет согласие с юридическими документами
    # Регистрируем только если legal.enabled=true и документы настроены
    if yaml_config.legal.enabled and yaml_config.legal.has_documents():
        legal_middleware = LegalConsentMiddleware(
            legal_config=yaml_config.legal,
            # Кешируем на 24 часа — версия документов меняется редко
            cache_ttl_seconds=86400,
        )
        dp.message.middleware(legal_middleware)
        dp.callback_query.middleware(legal_middleware)
        registered_middlewares.append("LegalConsentMiddleware")
        logger.info(
            "Проверка согласия с документами включена: version=%s",
            yaml_config.legal.version,
        )

    # Middleware для контроля cooldowns между генерациями
    cooldown_middleware = GenerationCooldownMiddleware(yaml_config, ai_service)
    dp.message.middleware(cooldown_middleware)
    dp.callback_query.middleware(cooldown_middleware)
    registered_middlewares.append("CooldownMiddleware")


def setup_error_handlers(dp: Dispatcher) -> None:
    """Зарегистрировать обработчики ошибок в диспетчере.

    Обрабатываемые ошибки:
    - CooldownError — пользователь пытается запустить генерацию
      во время выполнения предыдущей

    Args:
        dp: Диспетчер aiogram.
    """

    @dp.errors()
    async def cooldown_error_handler(error_event: ErrorEvent) -> bool:
        """Обработать ошибку cooldown.

        Отправляет пользователю понятное сообщение о том, что предыдущая
        генерация ещё выполняется.

        Args:
            error_event: Объект ErrorEvent из aiogram, содержит:
                - exception: исключение (CooldownError)
                - update: Update-объект с Message или CallbackQuery

        Returns:
            True если ошибка обработана, False иначе.
        """
        if not isinstance(error_event.exception, CooldownError):
            return False

        exception: CooldownError = error_event.exception

        # Формируем понятное сообщение для пользователя
        message = (
            "⏳ Ваша предыдущая генерация ещё выполняется.\n"
            f"Дождитесь её завершения (осталось ~{exception.seconds_left} сек)"
        )

        # Определяем источник события и отправляем сообщение
        if error_event.update.message:
            # Сообщение от пользователя
            await error_event.update.message.answer(message)
        elif (
            error_event.update.callback_query
            and error_event.update.callback_query.message
        ):
            # Callback от inline-кнопки
            await error_event.update.callback_query.message.answer(message)

        return True

    logger.debug("Обработчики ошибок зарегистрированы: CooldownError")


def setup_handlers(dp: Dispatcher) -> None:
    """Подключить роутеры к диспетчеру.

    Подключает главный роутер, который содержит суб-роутеры
    на основе конфигурации команд из config.yaml (секция commands).

    Какие команды подключаются:
    - Только те, которые указаны в config.yaml с enabled=true
    - Команда /language — только если localization.enabled=true

    Args:
        dp: Диспетчер aiogram.
    """
    main_router = get_main_router()
    dp.include_router(main_router)
    logger.debug("Роутеры подключены к диспетчеру")


def setup_bot(
    dp: Dispatcher,
    yaml_config: YamlConfig,
    ai_service: AIService,
    bot: "Bot",
    channel_settings: "ChannelSettings",
) -> None:
    """Полная настройка бота: middleware, errors, handlers.

    Удобная функция для настройки всех компонентов одним вызовом.
    Вызывает в правильном порядке:
    1. setup_middlewares() — middleware
    2. setup_error_handlers() — обработчики ошибок
    3. setup_handlers() — роутеры

    Args:
        dp: Диспетчер aiogram.
        yaml_config: Конфигурация из YAML.
        ai_service: AI-сервис.
        bot: Экземпляр Telegram-бота.
        channel_settings: Настройки обязательной подписки на канал.

    Example:
        >>> dp = create_dispatcher(settings.fsm)
        >>> ai_service = create_ai_service()
        >>> setup_bot(dp, yaml_config, ai_service, bot, settings.channel)
    """
    setup_middlewares(dp, yaml_config, ai_service, bot, channel_settings)
    setup_error_handlers(dp)
    setup_handlers(dp)
