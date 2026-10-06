"""Обработчики команд бота.

Этот модуль создаёт главный роутер и подключает суб-роутеры
на основе конфигурации команд из config.yaml.

АРХИТЕКТУРА ПРИОРИТЕТА РОУТЕРОВ:
================================
В aiogram 3.x приоритет handler'ов определяется порядком регистрации роутеров.
Первый подходящий handler обрабатывает сообщение.

Проблема: Если FSM handler зарегистрирован раньше Command handler'а другого
роутера, то FSM handler перехватит команду как обычное сообщение.

Решение: Разделяем роутеры на два уровня:
1. Command routers (router) — регистрируются ПЕРВЫМИ
2. FSM routers (fsm_router) — регистрируются ПОСЛЕ всех команд

Это гарантирует что команды (/balance, /help и т.д.) работают в любом
FSM состоянии.

Модули с FSM экспортируют два роутера:
- router: команда (например /chatgpt)
- fsm_router: FSM handlers (ожидание сообщения, выбор модели)

Какие команды включены — определяется в секции `commands` конфига:
- enabled: true/false — подключать ли роутер
- show_in_menu: true/false — показывать ли в меню Telegram

Пример config.yaml:
    commands:
      chatgpt:
        enabled: true
        show_in_menu: true
        menu_description:
          ru: "Диалог с ИИ"
          en: "Chat with AI"
      billing:
        enabled: false  # Команда отключена

Если команда не указана в конфиге — она считается ОТКЛЮЧЁННОЙ.
"""

from aiogram import Router

from src.bot.commands import get_command_registry
from src.config.yaml_config import CommandsConfig
from src.utils.i18n import Localization
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Модули с FSM роутерами (экспортируют fsm_router помимо router)
FSM_MODULES = ["chatgpt", "imagine", "edit_image"]


def get_main_router(commands_config: CommandsConfig | None = None) -> Router:
    """Создать главный роутер со всеми включёнными обработчиками.

    Подключает роутеры в правильном порядке для приоритета команд:
    1. Сначала все command routers (router) — команды
    2. Потом все FSM routers (fsm_router) — FSM handlers
    3. В конце fallback router — неизвестные команды

    Это гарантирует что команды работают в любом FSM состоянии.

    Подключает роутеры только для команд, которые:
    1. Зарегистрированы в реестре команд (src/bot/commands/registry.py)
    2. Включены в конфиге (commands.X.enabled=true)
    3. Не требуют локализации ИЛИ локализация включена
    4. Не требуют биллинга ИЛИ биллинг включён
    5. Не требуют юридических документов ИЛИ документы настроены

    Args:
        commands_config: Конфигурация команд из config.yaml.
            Если None — загружается из глобального yaml_config.

    Returns:
        Главный роутер с подключёнными суб-роутерами.
    """
    router = Router(name="main")

    # Если конфиг не передан — загружаем из глобального
    if commands_config is None:
        from src.config.yaml_config import yaml_config

        commands_config = yaml_config.commands
        billing_enabled = yaml_config.billing.enabled
        legal_documents_configured = yaml_config.legal.has_documents()
    else:
        # Если конфиг передан явно — загружаем остальные настройки из глобального
        from src.config.yaml_config import yaml_config

        billing_enabled = yaml_config.billing.enabled
        legal_documents_configured = yaml_config.legal.has_documents()

    # Проверяем включена ли локализация
    localization_enabled = Localization.is_enabled()

    # Получаем реестр команд и список роутеров для включённых команд
    registry = get_command_registry()
    routers = registry.get_enabled_routers(
        commands_config=commands_config,
        localization_enabled=localization_enabled,
        billing_enabled=billing_enabled,
        legal_documents_configured=legal_documents_configured,
    )

    # ШАГ 1: Подключаем все command routers (высокий приоритет)
    for cmd_router in routers:
        router.include_router(cmd_router)

    # ШАГ 2: Подключаем FSM routers для модулей с FSM (низкий приоритет)
    # FSM роутеры регистрируются ПОСЛЕ всех команд, чтобы команды
    # обрабатывались первыми в любом FSM состоянии.
    for module_name in FSM_MODULES:
        if not commands_config.is_enabled(module_name):
            continue

        try:
            module = __import__(
                f"src.bot.handlers.{module_name}",
                fromlist=["fsm_router"],
            )
            if hasattr(module, "fsm_router"):
                router.include_router(module.fsm_router)
                logger.debug("FSM роутер для /%s подключён", module_name)
        except (ImportError, AttributeError) as e:
            logger.warning(
                "Не удалось загрузить fsm_router для %s: %s",
                module_name,
                e,
            )

    # ШАГ 3: Channel subscription fallback router (если проверка подписки включена)
    # Этот handler обрабатывает callback "check_channel_sub"
    # если middleware его пропустил. Подключается перед fallback.
    from src.config.settings import settings

    if settings.channel.is_enabled:
        from src.bot.handlers.channel_subscription import (
            router as channel_subscription_router,
        )

        router.include_router(channel_subscription_router)
        logger.debug("Channel subscription fallback router подключён")

    # ШАГ 4: Fallback-роутер подключается ПОСЛЕДНИМ
    # Он обрабатывает неизвестные команды, которые не были обработаны выше.
    from src.bot.handlers.fallback import router as fallback_router

    router.include_router(fallback_router)

    return router


__all__ = ["get_main_router"]
