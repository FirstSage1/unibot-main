"""Fallback-обработчики для необработанных сообщений и команд.

Этот модуль содержит обработчики, которые срабатывают когда
сообщение не было обработано другими хендлерами:

- unknown_command — для команд, которые не зарегистрированы или отключены
- Может расширяться для других fallback-сценариев

ВАЖНО: Роутер должен подключаться ПОСЛЕДНИМ, чтобы другие
обработчики имели приоритет.
"""

from aiogram import F, Router
from aiogram.types import Message

from src.utils.i18n import Localization
from src.utils.logging import get_logger

router = Router(name="fallback")
logger = get_logger(__name__)


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message, l10n: Localization) -> None:
    """Обработать неизвестную или отключённую команду.

    Срабатывает для любых сообщений начинающихся с /, которые
    не были обработаны другими хендлерами. Это может быть:
    - Команда, которая не существует
    - Команда, которая отключена в config.yaml
    - Команда из закешированного меню Telegram

    Отправляет пользователю понятное сообщение и логирует warning.

    Args:
        message: Сообщение с командой.
        l10n: Объект локализации для переводов.
    """
    if not message.from_user or not message.text:
        return

    # Извлекаем имя команды (первое слово без /)
    command = message.text.split()[0].lstrip("/")

    await message.answer(l10n.get("command_not_found", command=command))

    logger.warning(
        "Неизвестная команда: user_id=%d, command=/%s",
        message.from_user.id,
        command,
    )
