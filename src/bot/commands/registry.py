"""Реестр команд бота.

Этот модуль связывает имена команд с их роутерами и предоставляет
централизованное управление командами на основе конфигурации.

Как это работает:
1. Каждая команда регистрируется в COMMAND_ROUTERS с именем и фабрикой роутера
2. При старте бота get_main_router() проверяет config.yaml (секция commands)
3. Подключаются только роутеры для команд с enabled=true
4. register_bot_commands() регистрирует меню для команд с show_in_menu=true

Добавление новой команды:
1. Создайте handler в src/bot/handlers/my_command.py
2. Добавьте роутер в COMMAND_ROUTERS ниже
3. Добавьте команду в config.yaml (секция commands)

Пример:
    # В handlers/my_command.py
    router = Router(name="my_command")

    # В этом файле (registry.py)
    from src.bot.handlers import my_command
    COMMAND_ROUTERS["my_command"] = lambda: my_command.router

    # В config.yaml
    commands:
      my_command:
        enabled: true
        show_in_menu: true
        menu_description:
          ru: "Моя команда"
          en: "My command"
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from aiogram import Router
from aiogram.types import BotCommand

from src.config.yaml_config import CommandsConfig
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CommandDefinition:
    """Определение команды для реестра.

    Связывает имя команды с её роутером и дополнительными настройками.

    Attributes:
        name: Имя команды (без слеша): start, chatgpt, billing.
        router_factory: Функция, возвращающая роутер команды.
            Используется фабрика (lambda), а не сам роутер, чтобы:
            1. Избежать циклических импортов при загрузке модуля
            2. Создавать роутер только если команда включена
        requires_localization: Команда требует включённой мультиязычности.
            Если True и localization.enabled=false — команда игнорируется.
        requires_billing: Команда требует включённой системы биллинга.
            Если True и billing.enabled=false — команда игнорируется.
        requires_legal: Команда требует настроенных юридических документов.
            Если True и legal.has_documents()=false — команда игнорируется.
    """

    name: str
    router_factory: Callable[[], Router]
    requires_localization: bool = False
    requires_billing: bool = False
    requires_legal: bool = False


@dataclass
class CommandRegistry:
    """Реестр всех доступных команд бота.

    Содержит список определений команд и методы для работы с ними
    на основе конфигурации (CommandsConfig).

    Принципы работы:
    - Команда включена если: есть в конфиге И enabled=true
    - Если команда не указана в конфиге — она ОТКЛЮЧЕНА
    - Если requires_localization=true и локализация выключена — команда игнорируется

    Attributes:
        definitions: Список определений команд.
    """

    definitions: list[CommandDefinition] = field(default_factory=list)

    def register(
        self,
        name: str,
        router_factory: Callable[[], Router],
        requires_localization: bool = False,
        requires_billing: bool = False,
        requires_legal: bool = False,
    ) -> None:
        """Зарегистрировать команду в реестре.

        Args:
            name: Имя команды (start, chatgpt, и т.д.)
            router_factory: Фабрика для создания роутера команды
            requires_localization: Требуется ли включённая локализация
            requires_billing: Требуется ли включённая система биллинга
            requires_legal: Требуются ли настроенные юридические документы
        """
        self.definitions.append(
            CommandDefinition(
                name=name,
                router_factory=router_factory,
                requires_localization=requires_localization,
                requires_billing=requires_billing,
                requires_legal=requires_legal,
            )
        )

    def get_enabled_routers(
        self,
        commands_config: CommandsConfig,
        localization_enabled: bool = True,
        billing_enabled: bool = True,
        legal_documents_configured: bool = False,
    ) -> list[Router]:
        """Получить список роутеров для включённых команд.

        Проверяет каждую зарегистрированную команду:
        1. Есть ли она в конфиге и включена ли (enabled=true)
        2. Если требует локализации — проверяет localization.enabled
        3. Если требует биллинга — проверяет billing.enabled
        4. Если требует юридических документов — проверяет legal.has_documents()

        Args:
            commands_config: Конфигурация команд из config.yaml
            localization_enabled: Включена ли мультиязычность
            billing_enabled: Включена ли система биллинга
            legal_documents_configured: Настроены ли юридические документы

        Returns:
            Список роутеров для подключения к диспетчеру.
        """
        routers: list[Router] = []

        for definition in self.definitions:
            # Проверяем включена ли команда в конфиге
            if not commands_config.is_enabled(definition.name):
                logger.debug(
                    "Команда /%s отключена в конфиге",
                    definition.name,
                )
                continue

            # Проверяем требования к локализации
            if definition.requires_localization and not localization_enabled:
                logger.debug(
                    "Команда /%s требует локализации, но она отключена",
                    definition.name,
                )
                continue

            # Проверяем требования к биллингу
            if definition.requires_billing and not billing_enabled:
                logger.debug(
                    "Команда /%s требует биллинга, но он отключён",
                    definition.name,
                )
                continue

            # Проверяем требования к юридическим документам
            if definition.requires_legal and not legal_documents_configured:
                logger.debug(
                    "Команда /%s требует настроенных юридических документов",
                    definition.name,
                )
                continue

            # Создаём роутер через фабрику
            try:
                router = definition.router_factory()
                routers.append(router)
                logger.debug("Команда /%s включена", definition.name)
            except Exception:
                logger.exception(
                    "Ошибка создания роутера для команды /%s",
                    definition.name,
                )

        return routers

    def get_menu_bot_commands(
        self,
        commands_config: CommandsConfig,
        language: str,
        default_language: str = "ru",
        localization_enabled: bool = True,
        billing_enabled: bool = True,
        legal_documents_configured: bool = False,
    ) -> list[BotCommand]:
        """Получить список BotCommand для меню Telegram.

        Возвращает только команды, которые:
        1. Включены в конфиге (enabled=true)
        2. Должны показываться в меню (show_in_menu=true)
        3. Не требуют локализации ИЛИ локализация включена
        4. Не требуют биллинга ИЛИ биллинг включён
        5. Не требуют юридических документов ИЛИ документы настроены

        Args:
            commands_config: Конфигурация команд
            language: Код языка для описаний
            default_language: Язык по умолчанию для fallback
            localization_enabled: Включена ли мультиязычность
            billing_enabled: Включена ли система биллинга
            legal_documents_configured: Настроены ли юридические документы

        Returns:
            Список BotCommand для регистрации в Telegram.
        """
        bot_commands: list[BotCommand] = []

        for definition in self.definitions:
            # Проверяем включена ли команда
            if not commands_config.is_enabled(definition.name):
                continue

            # Проверяем требования к локализации
            if definition.requires_localization and not localization_enabled:
                continue

            # Проверяем требования к биллингу
            if definition.requires_billing and not billing_enabled:
                continue

            # Проверяем требования к юридическим документам
            if definition.requires_legal and not legal_documents_configured:
                continue

            # Проверяем нужно ли показывать в меню
            if not commands_config.should_show_in_menu(definition.name):
                continue

            # Получаем конфиг команды
            command_config = commands_config.commands.get(definition.name)
            if command_config is None:
                continue

            # Получаем описание для языка
            description = command_config.get_description(language, default_language)

            # Если описание пустое — пропускаем (или используем имя команды)
            if not description:
                description = f"/{definition.name}"

            bot_commands.append(
                BotCommand(command=definition.name, description=description)
            )

        return bot_commands


# =============================================================================
# ГЛОБАЛЬНЫЙ РЕЕСТР КОМАНД
# =============================================================================
#
# Здесь регистрируются все команды бота.
# Порядок регистрации определяет порядок подключения роутеров.
#
# ВАЖНО: Используются lambda-фабрики для отложенного импорта роутеров.
# Это позволяет избежать циклических импортов и загружать модули
# только для включённых команд.

_registry: CommandRegistry | None = None


def get_command_registry() -> CommandRegistry:
    """Получить глобальный реестр команд (singleton).

    При первом вызове создаёт реестр и регистрирует все команды.
    При последующих вызовах возвращает существующий реестр.

    Returns:
        Глобальный реестр команд.
    """
    global _registry

    if _registry is not None:
        return _registry

    _registry = CommandRegistry()

    # =========================================================================
    # РЕГИСТРАЦИЯ КОМАНД
    # =========================================================================
    #
    # Порядок регистрации важен — он влияет на порядок обработки сообщений.
    # Более специфичные handlers должны быть первыми.

    # /start — регистрация и приветствие
    # Используется специальный фильтр CommandStart()
    _registry.register(
        name="start",
        router_factory=lambda: (
            __import__("src.bot.handlers.start", fromlist=["router"]).router
        ),
    )

    # /chatgpt — диалог с AI-моделями
    _registry.register(
        name="chatgpt",
        router_factory=lambda: (
            __import__("src.bot.handlers.chatgpt", fromlist=["router"]).router
        ),
    )

    _registry.register(
        name="generate",
        router_factory=lambda: (
            __import__("src.bot.handlers.generate", fromlist=["router"]).router
        ),
    )

    # /imagine — генерация изображений
    _registry.register(
        name="imagine",
        router_factory=lambda: (
            __import__("src.bot.handlers.imagine", fromlist=["router"]).router
        ),
    )

    # /edit_image — редактирование изображений
    _registry.register(
        name="edit_image",
        router_factory=lambda: (
            __import__("src.bot.handlers.edit_image", fromlist=["router"]).router
        ),
    )

    # /clear — очистка истории диалога
    _registry.register(
        name="clear",
        router_factory=lambda: (
            __import__("src.bot.handlers.clear", fromlist=["router"]).router
        ),
    )

    # /language — смена языка интерфейса
    # Требует включённой мультиязычности (localization.enabled=true)
    _registry.register(
        name="language",
        router_factory=lambda: (
            __import__("src.bot.handlers.language", fromlist=["router"]).router
        ),
        requires_localization=True,
    )

    # /balance — просмотр баланса токенов
    # Команда доступна только при включённой системе биллинга (billing.enabled=true)
    _registry.register(
        name="balance",
        router_factory=lambda: (
            __import__("src.bot.handlers.balance", fromlist=["router"]).router
        ),
        requires_billing=True,
    )

    # /invite — реферальная программа
    # Автоматически отключается если referral.enabled=false в конфиге
    _registry.register(
        name="invite",
        router_factory=lambda: (
            __import__("src.bot.handlers.invite", fromlist=["router"]).router
        ),
    )

    # /settings — настройки пользователя (язык, подписка и т.д.)
    _registry.register(
        name="settings",
        router_factory=lambda: (
            __import__("src.bot.handlers.settings", fromlist=["router"]).router
        ),
    )

    # /help — помощь и контакт поддержки
    _registry.register(
        name="help",
        router_factory=lambda: (
            __import__("src.bot.handlers.help", fromlist=["router"]).router
        ),
    )

    # /terms — юридические документы (оферта, политика конфиденциальности)
    # Требует настроенных ссылок на документы (legal.has_documents()=true)
    _registry.register(
        name="terms",
        router_factory=lambda: (
            __import__("src.bot.handlers.terms", fromlist=["router"]).router
        ),
        requires_legal=True,
    )

    # /error — тестирование системы отслеживания ошибок (только для разработки)
    _registry.register(
        name="error",
        router_factory=lambda: (
            __import__("src.bot.handlers.error", fromlist=["router"]).router
        ),
    )

    # Обработка платежей — только через callback из /balance (не команда).
    # Router обрабатывает callbacks: buy:start, tariff:*, pay:* и др.
    _registry.register(
        name="buy",
        router_factory=lambda: (
            __import__("src.bot.handlers.buy", fromlist=["router"]).router
        ),
        requires_billing=True,
    )

    logger.debug(
        "Зарегистрировано команд в реестре: %d",
        len(_registry.definitions),
    )

    return _registry


def reset_registry() -> None:
    """Сбросить глобальный реестр (для тестов).

    Позволяет пересоздать реестр с нуля при следующем вызове
    get_command_registry().
    """
    global _registry
    _registry = None
