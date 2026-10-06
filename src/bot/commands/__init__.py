"""Модуль управления командами бота.

Предоставляет централизованный реестр команд и функции для работы с ними.
Команды включаются/выключаются через config.yaml (секция commands).
"""

from src.bot.commands.registry import CommandRegistry, get_command_registry

__all__ = ["CommandRegistry", "get_command_registry"]
