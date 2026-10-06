"""Инициализация Bot и Dispatcher.

Этот модуль создаёт экземпляры бота и диспетчера.
Также содержит функцию для регистрации команд в меню Telegram.

Поддерживаемые хранилища FSM:
- memory — в памяти (теряется при перезапуске, для тестов)
- sqlite — в SQLite файле (сохраняется между перезапусками)
- redis — в Redis (для масштабирования)
"""

from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import ClientSession
from typing_extensions import override

from src.config.models import FSMSettings
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from src.config.yaml_config import CommandsConfig, LocalizationConfig

logger = get_logger(__name__)


class SystemProxySession(AiohttpSession):
    """Использовать системный HTTP-прокси для доступа к Telegram."""

    def __init__(self, proxy_url: str | None = None) -> None:
        """Инициализировать сессию с системным или заданным HTTP-прокси."""
        super().__init__()
        self._proxy_url = proxy_url
        self._system_client: ClientSession | None = None

    @override
    async def create_session(self) -> ClientSession:
        """Создать HTTP-клиент с настройками прокси операционной системы."""
        if self._system_client is None or self._system_client.closed:
            self._system_client = ClientSession(
                trust_env=True,
                proxy=self._proxy_url,
            )
        return self._system_client

    @override
    async def close(self) -> None:
        """Закрыть соединения HTTP-клиента."""
        if self._system_client is not None:
            await self._system_client.close()


def create_bot(token: str, proxy_url: str | None = None) -> Bot:
    """Создать экземпляр бота.

    Args:
        token: Токен Telegram-бота (может быть SecretStr или str).
        proxy_url: HTTP-прокси для доступа к Telegram API.

    Returns:
        Настроенный экземпляр Bot.
    """
    # Если token — SecretStr (из pydantic settings), извлекаем значение
    token_str = (
        token.get_secret_value() if hasattr(token, "get_secret_value") else token
    )
    return Bot(
        token=token_str,
        session=SystemProxySession(proxy_url=proxy_url),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_storage(fsm_settings: FSMSettings) -> BaseStorage:
    """Создать хранилище FSM на основе настроек.

    Поддерживаемые типы хранилищ:
    - memory — MemoryStorage (данные в памяти, теряются при рестарте)
    - sqlite — SQLStorage (данные в файле, сохраняются между рестартами)
    - redis — RedisStorage (для масштабирования на несколько инстансов)

    Args:
        fsm_settings: Настройки FSM из конфигурации.

    Returns:
        Инициализированное хранилище FSM.

    Raises:
        ValueError: Если указан неизвестный тип хранилища.
        ValueError: Если для redis не указан redis_url.
    """
    storage_type = fsm_settings.storage.lower()

    if storage_type == "memory":
        logger.info("FSM storage: MemoryStorage (данные в памяти)")
        return MemoryStorage()

    if storage_type == "sqlite":
        # Ленивый импорт — библиотека нужна только для sqlite
        from pathlib import Path

        from aiogram_fsm_sqlitestorage import SQLiteStorage

        # Создаём родительскую директорию, если не существует.
        sqlite_path = Path(fsm_settings.sqlite_path)

        try:
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            # SQLiteStorage реализует BaseStorage
            storage: BaseStorage = SQLiteStorage(db_path=fsm_settings.sqlite_path)
            logger.info(
                "FSM storage: SQLiteStorage (файл: %s)",
                fsm_settings.sqlite_path,
            )
            return storage
        except (OSError, PermissionError) as e:
            # Fallback на MemoryStorage если SQLite недоступен
            logger.warning(
                "Не удалось создать SQLite storage (%s), используем MemoryStorage. "
                "FSM-состояния будут потеряны при перезапуске.",
                e,
            )
            return MemoryStorage()

    if storage_type == "redis":
        if not fsm_settings.redis_url:
            raise ValueError("Для FSM storage=redis необходимо указать FSM__REDIS_URL")

        # Ленивый импорт — библиотека aiogram[redis] нужна только для redis
        from aiogram.fsm.storage.redis import RedisStorage

        logger.info(
            "FSM storage: RedisStorage (url: %s)",
            fsm_settings.redis_url.split("@")[-1],  # Скрываем пароль в логах
        )
        return RedisStorage.from_url(fsm_settings.redis_url)

    raise ValueError(
        f"Неизвестный тип FSM storage: {storage_type}. "
        "Допустимые значения: memory, sqlite, redis"
    )


def create_dispatcher(fsm_settings: FSMSettings | None = None) -> Dispatcher:
    """Создать Dispatcher с настроенным хранилищем FSM.

    Args:
        fsm_settings: Настройки FSM. Если None — используется MemoryStorage.

    Returns:
        Настроенный экземпляр Dispatcher.
    """
    storage: BaseStorage
    if fsm_settings is None:
        # Для обратной совместимости и тестов
        storage = MemoryStorage()
        logger.info("FSM storage: MemoryStorage (настройки не переданы)")
    else:
        storage = create_storage(fsm_settings)

    return Dispatcher(storage=storage)


async def register_bot_commands(
    bot: Bot,
    commands_config: "CommandsConfig | None" = None,
    localization_config: "LocalizationConfig | None" = None,
) -> None:
    """Зарегистрировать команды бота в меню Telegram с локализацией.

    Регистрирует команды на основе конфигурации из config.yaml:
    - Только команды с enabled=true и show_in_menu=true попадают в меню
    - Описания команд локализуются для каждого языка из available_languages
    - Для каждого языка вызывается set_my_commands с language_code
    - Дополнительно регистрируется default меню (без language_code)

    Telegram показывает команды в меню на языке пользователя:
    - Если язык пользователя есть в available_languages — видит локализованное меню
    - Если нет — видит default меню (на default_language)

    Args:
        bot: Экземпляр бота.
        commands_config: Конфигурация команд.
            Если None — загружается из yaml_config.
        localization_config: Конфигурация локализации.
            Если None — загружается из yaml_config.

    Example:
        # В config.yaml:
        # commands:
        #   chatgpt:
        #     enabled: true
        #     show_in_menu: true
        #     menu_description:
        #       ru: "Диалог с ИИ"
        #       en: "Chat with AI"
        #
        # Результат в Telegram:
        # - Для ru пользователей: /chatgpt — "Диалог с ИИ"
        # - Для en пользователей: /chatgpt — "Chat with AI"
        # - Для остальных: /chatgpt — "Диалог с ИИ" (default)
    """
    from src.bot.commands import get_command_registry
    from src.config.yaml_config import (
        yaml_config,
    )
    from src.utils.i18n import Localization

    # Загружаем конфиги если не переданы
    if commands_config is None:
        commands_config = yaml_config.commands

    if localization_config is None:
        localization_config = yaml_config.localization

    # Проверяем включена ли локализация
    localization_enabled = Localization.is_enabled()

    # Проверяем включён ли биллинг
    billing_enabled = yaml_config.billing.enabled

    # Проверяем настроены ли юридические документы
    legal_documents_configured = yaml_config.legal.has_documents()

    # Получаем реестр команд
    registry = get_command_registry()

    # Определяем языки для регистрации меню
    default_language = localization_config.default_language
    available_languages = localization_config.available_languages

    # Регистрируем меню для каждого языка
    for language in available_languages:
        bot_commands = registry.get_menu_bot_commands(
            commands_config=commands_config,
            language=language,
            default_language=default_language,
            localization_enabled=localization_enabled,
            billing_enabled=billing_enabled,
            legal_documents_configured=legal_documents_configured,
        )

        if bot_commands:
            await bot.set_my_commands(bot_commands, language_code=language)
            logger.debug(
                "Зарегистрировано меню для языка '%s': %d команд",
                language,
                len(bot_commands),
            )

    # Регистрируем default меню (без language_code) — для всех остальных языков
    default_commands = registry.get_menu_bot_commands(
        commands_config=commands_config,
        language=default_language,
        default_language=default_language,
        localization_enabled=localization_enabled,
        billing_enabled=billing_enabled,
        legal_documents_configured=legal_documents_configured,
    )

    if default_commands:
        await bot.set_my_commands(default_commands)
