"""Тесты для реестра команд бота.

Проверяют корректность работы CommandRegistry:
- Регистрация команд
- Фильтрация команд по requires_localization
- Фильтрация команд по requires_billing
- Фильтрация команд по requires_legal
- Получение включённых роутеров
- Получение команд для меню Telegram
"""

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from aiogram import Router
from aiogram.types import BotCommand

from src.bot.commands.registry import CommandDefinition, CommandRegistry
from src.config.yaml_config import CommandConfig, CommandsConfig


@pytest.fixture
def mock_commands_config() -> CommandsConfig:
    """Создать мок-объект конфигурации команд."""
    config = MagicMock(spec=CommandsConfig)

    # Словарь настроек команд
    commands = {
        "start": CommandConfig(
            enabled=True,
            show_in_menu=True,
            menu_description={"ru": "🚀 Начать", "en": "🚀 Start"},
        ),
        "help": CommandConfig(
            enabled=True,
            show_in_menu=True,
            menu_description={"ru": "❓ Помощь", "en": "❓ Help"},
        ),
        "language": CommandConfig(
            enabled=True,
            show_in_menu=True,
            menu_description={"ru": "🌐 Язык", "en": "🌐 Language"},
        ),
        "terms": CommandConfig(
            enabled=True,
            show_in_menu=True,
            menu_description={"ru": "📄 Условия", "en": "📄 Terms"},
        ),
        "buy": CommandConfig(
            enabled=True,
            show_in_menu=True,
            menu_description={"ru": "💳 Купить", "en": "💳 Buy"},
        ),
        "disabled_command": CommandConfig(
            enabled=False,
            show_in_menu=False,
            menu_description={"ru": "❌ Отключена", "en": "❌ Disabled"},
        ),
    }

    def is_enabled(name: str) -> bool:
        """Проверить включена ли команда."""
        cmd = commands.get(name)
        return cmd.enabled if cmd else False

    def should_show_in_menu(name: str) -> bool:
        """Проверить нужно ли показывать команду в меню."""
        cmd = commands.get(name)
        return cmd.show_in_menu if cmd else False

    def get_menu_description(name: str, language: str = "ru") -> str:
        """Получить описание команды для меню."""
        cmd = commands.get(name)
        if not cmd or not cmd.menu_description:
            return ""
        return cmd.menu_description.get(language, "")

    config.is_enabled = MagicMock(side_effect=is_enabled)
    config.should_show_in_menu = MagicMock(side_effect=should_show_in_menu)
    config.get_menu_description = MagicMock(side_effect=get_menu_description)
    config.commands = commands  # Добавляем словарь команд для прямого доступа

    return config


@pytest.fixture
def mock_router_factory() -> Callable[[], Router]:
    """Создать фабрику mock-роутеров."""

    def factory() -> Router:
        return MagicMock(spec=Router)

    return factory


class TestCommandRegistry:
    """Тесты для CommandRegistry."""

    def test_register_adds_command_definition(
        self, mock_router_factory: Callable[[], Router]
    ) -> None:
        """Проверить, что register добавляет определение команды."""
        registry = CommandRegistry()

        registry.register(name="test_command", router_factory=mock_router_factory)

        assert len(registry.definitions) == 1
        assert registry.definitions[0].name == "test_command"
        assert registry.definitions[0].router_factory == mock_router_factory

    def test_register_with_requires_localization(
        self, mock_router_factory: Callable[[], Router]
    ) -> None:
        """Проверить, что register сохраняет requires_localization."""
        registry = CommandRegistry()

        registry.register(
            name="test_command",
            router_factory=mock_router_factory,
            requires_localization=True,
        )

        assert registry.definitions[0].requires_localization is True

    def test_register_with_requires_billing(
        self, mock_router_factory: Callable[[], Router]
    ) -> None:
        """Проверить, что register сохраняет requires_billing."""
        registry = CommandRegistry()

        registry.register(
            name="test_command",
            router_factory=mock_router_factory,
            requires_billing=True,
        )

        assert registry.definitions[0].requires_billing is True

    def test_register_with_requires_legal(
        self, mock_router_factory: Callable[[], Router]
    ) -> None:
        """Проверить, что register сохраняет requires_legal."""
        registry = CommandRegistry()

        registry.register(
            name="test_command",
            router_factory=mock_router_factory,
            requires_legal=True,
        )

        assert registry.definitions[0].requires_legal is True

    def test_get_enabled_routers_returns_enabled_commands_only(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить, что get_enabled_routers возвращает только включённые команды."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)
        registry.register(name="disabled_command", router_factory=mock_router_factory)

        routers = registry.get_enabled_routers(
            commands_config=mock_commands_config,
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=True,
        )

        # start включена, disabled_command отключена
        assert len(routers) == 1

    def test_get_enabled_routers_filters_by_localization(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить, что команды с requires_localization фильтруются."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)
        registry.register(
            name="language",
            router_factory=mock_router_factory,
            requires_localization=True,
        )

        # Локализация выключена — language не должна появиться
        routers = registry.get_enabled_routers(
            commands_config=mock_commands_config,
            localization_enabled=False,
            billing_enabled=True,
            legal_documents_configured=True,
        )

        assert len(routers) == 1

    def test_get_enabled_routers_filters_by_billing(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить, что команды с requires_billing фильтруются."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)
        registry.register(
            name="buy",
            router_factory=mock_router_factory,
            requires_billing=True,
        )

        # Биллинг выключен — buy не должна появиться
        routers = registry.get_enabled_routers(
            commands_config=mock_commands_config,
            localization_enabled=True,
            billing_enabled=False,
            legal_documents_configured=True,
        )

        assert len(routers) == 1

    def test_get_enabled_routers_filters_by_legal(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить, что команды с requires_legal фильтруются."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)
        registry.register(
            name="terms",
            router_factory=mock_router_factory,
            requires_legal=True,
        )

        # Юридические документы не настроены — terms не должна появиться
        routers = registry.get_enabled_routers(
            commands_config=mock_commands_config,
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=False,
        )

        assert len(routers) == 1

    def test_get_enabled_routers_includes_legal_when_configured(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить включение команд с requires_legal когда документы настроены."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)
        registry.register(
            name="terms",
            router_factory=mock_router_factory,
            requires_legal=True,
        )

        # Юридические документы настроены — terms должна появиться
        routers = registry.get_enabled_routers(
            commands_config=mock_commands_config,
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=True,
        )

        assert len(routers) == 2

    def test_get_enabled_routers_calls_router_factory(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить, что get_enabled_routers вызывает фабрику роутера."""
        registry = CommandRegistry()
        factory_mock = MagicMock(return_value=MagicMock(spec=Router))
        registry.register(name="start", router_factory=factory_mock)

        routers = registry.get_enabled_routers(
            commands_config=mock_commands_config,
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=True,
        )

        factory_mock.assert_called_once()
        assert len(routers) == 1

    def test_get_menu_bot_commands_returns_only_visible_commands(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить, что get_menu_bot_commands возвращает только видимые команды."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)
        registry.register(name="help", router_factory=mock_router_factory)

        menu_commands = registry.get_menu_bot_commands(
            commands_config=mock_commands_config,
            language="ru",
            default_language="ru",
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=True,
        )

        assert len(menu_commands) == 2
        assert all(isinstance(cmd, BotCommand) for cmd in menu_commands)

    def test_get_menu_bot_commands_filters_by_legal(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить, что команды с requires_legal не попадают в меню без документов."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)
        registry.register(
            name="terms",
            router_factory=mock_router_factory,
            requires_legal=True,
        )

        # Юридические документы не настроены
        menu_commands = registry.get_menu_bot_commands(
            commands_config=mock_commands_config,
            language="ru",
            default_language="ru",
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=False,
        )

        # Только start должна быть в меню
        assert len(menu_commands) == 1
        assert menu_commands[0].command == "start"

    def test_get_menu_bot_commands_includes_legal_when_configured(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить добавление в меню команд с requires_legal когда документы есть."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)
        registry.register(
            name="terms",
            router_factory=mock_router_factory,
            requires_legal=True,
        )

        # Юридические документы настроены
        menu_commands = registry.get_menu_bot_commands(
            commands_config=mock_commands_config,
            language="ru",
            default_language="ru",
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=True,
        )

        # Обе команды должны быть в меню
        assert len(menu_commands) == 2
        command_names = {cmd.command for cmd in menu_commands}
        assert "start" in command_names
        assert "terms" in command_names

    def test_get_menu_bot_commands_uses_correct_language(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить, что get_menu_bot_commands использует правильный язык."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)

        menu_commands_ru = registry.get_menu_bot_commands(
            commands_config=mock_commands_config,
            language="ru",
            default_language="ru",
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=True,
        )

        menu_commands_en = registry.get_menu_bot_commands(
            commands_config=mock_commands_config,
            language="en",
            default_language="en",
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=True,
        )

        # Проверяем, что описания на разных языках
        assert menu_commands_ru[0].description == "🚀 Начать"
        assert menu_commands_en[0].description == "🚀 Start"

    def test_get_menu_bot_commands_filters_disabled_commands(
        self,
        mock_commands_config: CommandsConfig,
        mock_router_factory: Callable[[], Router],
    ) -> None:
        """Проверить, что отключённые команды не попадают в меню."""
        registry = CommandRegistry()
        registry.register(name="start", router_factory=mock_router_factory)
        registry.register(name="disabled_command", router_factory=mock_router_factory)

        menu_commands = registry.get_menu_bot_commands(
            commands_config=mock_commands_config,
            language="ru",
            default_language="ru",
            localization_enabled=True,
            billing_enabled=True,
            legal_documents_configured=True,
        )

        # disabled_command отключена в конфиге
        command_names = {cmd.command for cmd in menu_commands}
        assert "start" in command_names
        assert "disabled_command" not in command_names

    def test_card_command_is_in_enabled_menu(self) -> None:
        """Включённая команда /card должна публиковаться в меню Telegram."""
        from src.bot.commands import get_command_registry
        from src.config.yaml_config import yaml_config

        registry = get_command_registry()
        menu_commands = registry.get_menu_bot_commands(
            commands_config=yaml_config.commands,
            language="ru",
            localization_enabled=True,
            billing_enabled=yaml_config.billing.enabled,
            legal_documents_configured=yaml_config.legal.has_documents(),
        )

        assert "card" in {command.command for command in menu_commands}


class TestCommandDefinition:
    """Тесты для CommandDefinition."""

    def test_command_definition_creation(
        self, mock_router_factory: Callable[[], Router]
    ) -> None:
        """Проверить создание CommandDefinition."""
        definition = CommandDefinition(
            name="test",
            router_factory=mock_router_factory,
            requires_localization=True,
            requires_billing=False,
            requires_legal=True,
        )

        assert definition.name == "test"
        assert definition.router_factory == mock_router_factory
        assert definition.requires_localization is True
        assert definition.requires_billing is False
        assert definition.requires_legal is True

    def test_command_definition_defaults(
        self, mock_router_factory: Callable[[], Router]
    ) -> None:
        """Проверить значения по умолчанию для CommandDefinition."""
        definition = CommandDefinition(
            name="test",
            router_factory=mock_router_factory,
        )

        assert definition.requires_localization is False
        assert definition.requires_billing is False
        assert definition.requires_legal is False
