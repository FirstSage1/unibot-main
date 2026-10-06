"""Настройки приложения через переменные окружения.

ВАЖНО: Этот модуль загружает настройки из .env файла при импорте.
Для использования только классов настроек (без загрузки .env)
импортируйте из src.config.models вместо этого модуля.

Пример для тестов:
    # Изолированный импорт без побочных эффектов:
    from src.config.models import AIProvidersSettings

    # НЕ используйте в тестах (загрузит .env):
    from src.config.settings import AIProvidersSettings
"""

import sys

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Импортируем классы настроек из models.py
# Это позволяет тестам импортировать классы без побочных эффектов
from src.config.models import (
    AdminSettings,
    AIProvidersSettings,
    AppSettings,
    BotSettings,
    ChannelSettings,
    DatabaseSettings,
    FSMSettings,
    LoggingSettings,
    PaymentsSettings,
    StripeSettings,
    TelegramLoggingSettings,
    TelegramStarsSettings,
    YooKassaSettings,
)

# Реэкспортируем классы для обратной совместимости
__all__ = [
    "AIProvidersSettings",
    "AdminSettings",
    "AppSettings",
    "BotSettings",
    "ChannelSettings",
    "DatabaseSettings",
    "FSMSettings",
    "LoggingSettings",
    "PaymentsSettings",
    "Settings",
    "StripeSettings",
    "TelegramLoggingSettings",
    "TelegramStarsSettings",
    "YooKassaSettings",
    "load_settings",
    "settings",
]

# ==============================================================================
# СЛОВАРЬ ОШИБОК НА РУССКОМ ЯЗЫКЕ
# ==============================================================================
#
# Pydantic выдаёт ошибки на английском. Здесь мы переводим их на русский,
# чтобы начинающему разработчику было понятно, что пошло не так.
#
# Ключ — название поля в формате "родитель.поле" (например, "bot.token")
# Значение — понятное описание ошибки и как её исправить

# Инструкции по установке переменных окружения для разных окружений
ENV_SETUP_INSTRUCTIONS = """
Как установить переменную окружения:
  • Локальная разработка: добавьте {var_name}=значение в файл .env
  • Production (Amvera): укажите в настройках приложения → Переменные
"""


def _format_env_error(var_name: str, description: str) -> str:
    """Форматирует сообщение об ошибке для переменной окружения.

    Args:
        var_name: Имя переменной (например, BOT__TOKEN).
        description: Описание переменной.

    Returns:
        Полное сообщение с инструкциями по установке.
    """
    return f"""
❌ Не указана переменная окружения: {var_name}
   {description}
{ENV_SETUP_INSTRUCTIONS.format(var_name=var_name)}""".strip()


FIELD_ERROR_MESSAGES: dict[str, str] = {
    "bot": _format_env_error("BOT__TOKEN", "Токен Telegram-бота от @BotFather"),
    "bot.token": _format_env_error("BOT__TOKEN", "Токен Telegram-бота от @BotFather"),
}

# Сообщение по умолчанию для неизвестных полей
DEFAULT_ERROR_MESSAGE = """
❌ Ошибка конфигурации

Как установить переменные окружения:
  • Локальная разработка: добавьте переменные в файл .env
  • Production (Amvera): укажите в настройках приложения → Переменные
""".strip()


class Settings(BaseSettings):
    """Главные настройки приложения."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppSettings = AppSettings()
    bot: BotSettings
    database: DatabaseSettings = DatabaseSettings()
    logging: LoggingSettings = LoggingSettings()
    admin: AdminSettings = AdminSettings()
    ai: AIProvidersSettings = AIProvidersSettings()
    fsm: FSMSettings = FSMSettings()
    payments: PaymentsSettings = PaymentsSettings()
    channel: ChannelSettings = ChannelSettings()

    # URL прокси-сервера для обхода блокировок (опционально).
    # Формат: http://host:port, https://host:port, socks5://host:port
    # Если указан — все HTTP-запросы к AI-провайдерам будут идти через прокси.
    # Если не указан — прямое подключение.
    # Пример: http://proxy.example.com:8080
    proxy: str | None = None


def _format_validation_error(error: ValidationError) -> str:
    """Преобразовать ошибку Pydantic в понятное русское сообщение.

    Args:
        error: Ошибка валидации от Pydantic.

    Returns:
        Понятное сообщение на русском языке.
    """
    messages: list[str] = []

    for err in error.errors():
        # Получаем путь к полю (например, ("bot", "token") -> "bot.token")
        field_path = ".".join(str(loc) for loc in err["loc"])

        # Ищем русское сообщение для этого поля
        if field_path in FIELD_ERROR_MESSAGES:
            messages.append(FIELD_ERROR_MESSAGES[field_path])
        else:
            messages.append(DEFAULT_ERROR_MESSAGE)
            messages.append(f"Поле: {field_path}")
            messages.append(f"Тип ошибки: {err['type']}")
            messages.append(f"Сообщение: {err['msg']}")

    return "\n".join(messages)


def load_settings() -> Settings:
    """Загрузить настройки из переменных окружения.

    Если настройки некорректны — выводит понятную ошибку на русском
    и завершает программу.

    Returns:
        Объект Settings с загруженными настройками.
    """
    try:
        return Settings()
    except ValidationError as e:
        # Выводим понятную ошибку на русском
        print(_format_validation_error(e), file=sys.stderr)
        sys.exit(1)


# Загружаем настройки при импорте модуля.
# Если .env не настроен — программа завершится с понятной ошибкой.
settings = load_settings()
