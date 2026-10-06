"""Обработчик команды /help для отображения справки и контакта поддержки.

Показывает пользователю общую информацию о боте и контакт для обращения в поддержку.
Контакт поддержки настраивается в config.yaml (support.contact).

Если контакт не указан — команда покажет только общую справку без контакта.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.config.yaml_config import yaml_config
from src.utils.i18n import Localization
from src.utils.logging import get_logger

router = Router(name="help")
logger = get_logger(__name__)


@router.message(Command("help"))
async def cmd_help(message: Message, l10n: Localization) -> None:
    """Обработать команду /help.

    Отображает справочную информацию о боте и контакт поддержки.
    Контакт берётся из конфигурации (config.yaml → support.contact).

    Логика отображения:
    1. Если support.contact задан — показываем полное сообщение с контактом
    2. Если support.contact пустой — показываем сообщение без контакта

    Args:
        message: Входящее сообщение с командой /help.
        l10n: Объект локализации (внедряется через LanguageMiddleware).

    Примечание:
        Контакт поддержки может быть:
        - Username Telegram: @support_username
        - Ссылка на чат: https://t.me/support_chat
        - Email: support@example.com
    """
    # Получаем контакт поддержки из конфигурации
    support_contact = yaml_config.support.contact

    # Выбираем ключ локализации в зависимости от наличия контакта
    if support_contact:
        # Контакт указан — показываем полное сообщение
        help_text = l10n.get("help_message_with_contact", contact=support_contact)
        logger.debug(
            "Показываем /help с контактом поддержки: %s",
            support_contact,
        )
    else:
        # Контакт не указан — показываем сокращённое сообщение
        help_text = l10n.get("help_message_no_contact")
        logger.debug("Показываем /help без контакта поддержки")

    await message.answer(help_text, parse_mode="HTML")
