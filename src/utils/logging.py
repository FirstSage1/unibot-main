"""Настройка логирования.

Поддерживает три канала вывода:
1. Консоль (stdout) — для просмотра в терминале/логах Amvera (с цветной подсветкой)
2. Файл с ротацией — для хранения истории (data/logs/app.log)
3. Telegram — для уведомлений админа об ошибках (опционально)

Компактный формат логов:
    25-01-07 21:55:46 | INFO | bot.handlers.chatgpt | Сообщение
"""

import logging
import queue
import sys
import threading
import traceback
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import httpx
from typing_extensions import override

from src.config.constants import DATA_DIR
from src.utils.timezone import get_timezone

if TYPE_CHECKING:
    from src.config.settings import TelegramLoggingSettings

# Папка для логов
LOGS_DIR = DATA_DIR / "logs"

# Максимальная длина сообщения в Telegram (с запасом для форматирования)
# Telegram API ограничивает сообщения до 4096 символов
TELEGRAM_MESSAGE_MAX_LENGTH = 3500


# ==============================================================================
# ANSI-коды для цветного вывода в терминале
# ==============================================================================
#
# Эти коды работают в большинстве терминалов (Linux, macOS, Windows Terminal).
# Формат: \033[<код>m — где <код> определяет цвет/стиль.
#
# Основные цвета текста:
#   30 = чёрный, 31 = красный, 32 = зелёный, 33 = жёлтый
#   34 = синий, 35 = пурпурный, 36 = голубой, 37 = белый
#
# Яркие версии: 90-97 (аналогично, но ярче)
#
# Стили:
#   0 = сброс, 1 = жирный, 2 = тусклый, 4 = подчёркивание


class AnsiColors:
    """ANSI escape-коды для цветного вывода в терминале."""

    RESET = "\033[0m"
    BOLD = "\033[1m"

    # Цвета для разных уровней логирования (яркие версии)
    DEBUG = "\033[36m"  # Голубой (cyan)
    INFO = "\033[32m"  # Зелёный (green)
    WARNING = "\033[33m"  # Жёлтый (yellow)
    ERROR = "\033[31m"  # Красный (red)
    CRITICAL = "\033[35m"  # Пурпурный (magenta)

    # Дополнительные цвета
    GREY = "\033[90m"  # Серый (для даты/времени)


# Соответствие уровней логирования и цветов
LEVEL_COLORS: dict[str, str] = {
    "DEBUG": AnsiColors.DEBUG,
    "INFO": AnsiColors.INFO,
    "WARNING": AnsiColors.WARNING,
    "ERROR": AnsiColors.ERROR,
    "CRITICAL": AnsiColors.CRITICAL,
}


class TimezoneFormatter(logging.Formatter):
    """Форматтер логов с поддержкой часового пояса.

    Стандартный logging.Formatter использует локальное время системы.
    Этот форматтер позволяет указать любой часовой пояс для отображения.
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        timezone_name: str = "Europe/Moscow",
    ) -> None:
        """Инициализировать форматтер.

        Args:
            fmt: Формат строки лога.
            datefmt: Формат даты/времени.
            timezone_name: Название часового пояса из базы IANA.
        """
        super().__init__(fmt, datefmt)
        self.timezone = get_timezone(timezone_name)

    @override
    def formatTime(
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        """Отформатировать время записи лога.

        Переопределяет стандартный метод для использования настроенного часового пояса.

        Args:
            record: Запись лога.
            datefmt: Формат даты (если не указан, используется self.datefmt).

        Returns:
            Отформатированная строка времени.
        """
        # Конвертируем timestamp записи в datetime с нужным часовым поясом
        dt = datetime.fromtimestamp(record.created, tz=self.timezone)

        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime(self.default_time_format)


class ColoredFormatter(TimezoneFormatter):
    """Форматтер логов с цветной подсветкой уровней.

    Компактный формат:
        25-01-07 21:55:46 | INFO | bot.handlers.chatgpt | Сообщение

    Особенности:
    - Короткий год (25 вместо 2025)
    - Убран префикс "src." из имени модуля
    - Уровни логирования выделяются цветом

    Цвета уровней:
        - DEBUG:    голубой
        - INFO:     зелёный (как у Uvicorn)
        - WARNING:  жёлтый
        - ERROR:    красный
        - CRITICAL: пурпурный

    Цвета отображаются только в терминале с поддержкой ANSI-кодов.
    В файловом логе цвета не нужны — используйте обычный TimezoneFormatter.
    """

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        timezone_name: str = "Europe/Moscow",
        use_colors: bool = True,
    ) -> None:
        """Инициализировать форматтер с цветами.

        Args:
            fmt: Формат строки лога.
            datefmt: Формат даты/времени.
            timezone_name: Название часового пояса из базы IANA.
            use_colors: Использовать ли цветную подсветку.
        """
        super().__init__(fmt, datefmt, timezone_name)
        self.use_colors = use_colors

    @override
    def format(self, record: logging.LogRecord) -> str:
        """Отформатировать запись лога с цветной подсветкой.

        Args:
            record: Запись лога.

        Returns:
            Отформатированная строка с ANSI-кодами для цветов.
        """
        # Убираем префикс "src." из имени модуля для компактности
        # src.bot.handlers.chatgpt → bot.handlers.chatgpt
        original_name = record.name
        record.name = record.name.removeprefix("src.")

        # Получаем базовую отформатированную строку
        formatted = super().format(record)

        # Восстанавливаем оригинальное имя (для других handler-ов)
        record.name = original_name

        if not self.use_colors:
            return formatted

        # Добавляем цвет к уровню логирования
        level_color = LEVEL_COLORS.get(record.levelname, "")
        if level_color:
            # Заменяем уровень на цветную версию
            # Формат: "| INFO |" → "| \033[32mINFO\033[0m |"
            colored_level = f"{level_color}{record.levelname}{AnsiColors.RESET}"
            formatted = formatted.replace(
                f"| {record.levelname} |",
                f"| {colored_level} |",
            )

        return formatted


class TelegramHandler(logging.Handler):
    """Handler для отправки логов в Telegram.

    Особенности:
    - Асинхронная отправка через очередь (не блокирует основной поток)
    - Длинные сообщения отправляются как файл .txt
    - HTML-форматирование для читаемости
    - Таймауты для защиты от зависания при проблемах с сетью

    Использует отдельный поток для отправки, чтобы не блокировать
    основной event loop приложения.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: int,
        level: int = logging.ERROR,
        proxy_url: str | None = None,
    ) -> None:
        """Инициализировать handler.

        Args:
            bot_token: Токен Telegram-бота.
            chat_id: ID чата для отправки (пользователь или группа).
            level: Минимальный уровень логов для отправки.
        """
        super().__init__(level)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.proxy_url = proxy_url

        # Очередь для асинхронной отправки
        # Используем daemon-поток чтобы не блокировать завершение программы
        self._queue: queue.Queue[logging.LogRecord | None] = queue.Queue()
        self._worker_thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="telegram-logger",
        )
        self._worker_thread.start()

        # HTTP-клиент с таймаутами
        # connect: 5 сек на установку соединения
        # read: 30 сек на получение ответа (отправка файла может быть долгой)
        self._http_client = httpx.Client(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
            proxy=proxy_url,
        )

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Добавить запись в очередь на отправку.

        Этот метод вызывается из основного потока, поэтому
        не делаем тут сетевых операций — только кладём в очередь.

        Args:
            record: Запись лога.
        """
        self._queue.put(record)

    def _worker(self) -> None:
        """Фоновый поток для отправки сообщений.

        Читает записи из очереди и отправляет их в Telegram.
        None в очереди — сигнал к завершению.
        """
        while True:
            record = self._queue.get()

            # None — сигнал завершения
            if record is None:
                break

            try:
                self._send_to_telegram(record)
            except (OSError, RuntimeError, httpx.HTTPError) as e:
                # Ошибки отправки в Telegram не должны ломать приложение
                # Логируем в stderr чтобы не создавать рекурсию (иначе бесконечный цикл)
                sys.stderr.write(f"[TelegramHandler] Ошибка отправки: {e}\n")

    def _send_to_telegram(self, record: logging.LogRecord) -> None:
        """Отправить запись лога в Telegram.

        Если сообщение слишком длинное — отправляет как файл.

        Args:
            record: Запись лога для отправки.
        """
        # Форматируем сообщение
        message = self._format_message(record)

        # Если сообщение короткое — отправляем как текст
        if len(message) <= TELEGRAM_MESSAGE_MAX_LENGTH:
            self._send_message(message)
        else:
            # Длинное сообщение — отправляем как файл
            self._send_as_file(record, message)

    def _format_message(self, record: logging.LogRecord) -> str:
        """Форматировать запись лога для Telegram.

        Использует HTML-разметку для лучшей читаемости.

        Args:
            record: Запись лога.

        Returns:
            Отформатированное сообщение.
        """
        # Эмодзи для разных уровней
        level_emoji = {
            "DEBUG": "🔍",
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "ERROR": "🚨",
            "CRITICAL": "💀",
        }
        emoji = level_emoji.get(record.levelname, "📝")

        # Форматируем время в UTC
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")

        # Базовое сообщение
        header = f"{emoji} <b>{record.levelname}</b>\n"
        header += f"🕐 {time_str}\n"
        header += f"📍 {record.name}\n\n"

        # Текст сообщения (экранируем HTML)
        text = self._escape_html(record.getMessage())

        # Добавляем traceback если есть
        if record.exc_info:
            tb = "".join(traceback.format_exception(*record.exc_info))
            text += f"\n\n<b>Traceback:</b>\n<pre>{self._escape_html(tb)}</pre>"

        return header + text

    def _escape_html(self, text: str) -> str:
        """Экранировать HTML-символы.

        Args:
            text: Исходный текст.

        Returns:
            Текст с экранированными символами.
        """
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _send_message(self, message: str) -> None:
        """Отправить текстовое сообщение в Telegram.

        Args:
            message: Текст сообщения (с HTML-разметкой).
        """
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = self._http_client.post(
            url,
            data={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML",
            },
        )
        response.raise_for_status()
        if not response.json().get("ok", False):
            raise RuntimeError(f"Telegram API rejected log: {response.text}")

    def _send_as_file(self, record: logging.LogRecord, full_message: str) -> None:
        """Отправить лог как файл .txt.

        Используется когда сообщение слишком длинное для текста.
        Сначала отправляет краткое уведомление, затем файл с полным логом.

        Args:
            record: Запись лога.
            full_message: Полный текст сообщения.
        """
        # Краткое уведомление
        level_emoji = {
            "DEBUG": "🔍",
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "ERROR": "🚨",
            "CRITICAL": "💀",
        }
        emoji = level_emoji.get(record.levelname, "📝")

        short_message = (
            f"{emoji} <b>{record.levelname}</b>\n\n"
            f"Лог слишком большой для сообщения.\n"
            f"Полный текст отправлен файлом ниже."
        )
        self._send_message(short_message)

        # Отправляем файл
        # Убираем HTML-теги для файла — там нужен чистый текст
        clean_message = full_message.replace("<b>", "").replace("</b>", "")
        clean_message = clean_message.replace("<pre>", "").replace("</pre>", "")
        clean_message = clean_message.replace("&lt;", "<").replace("&gt;", ">")
        clean_message = clean_message.replace("&amp;", "&")

        # Имя файла с датой и уровнем (в UTC)
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        filename = f"log_{record.levelname}_{dt.strftime('%Y%m%d_%H%M%S')}.txt"

        url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
        response = self._http_client.post(
            url,
            data={"chat_id": self.chat_id},
            files={"document": (filename, clean_message.encode("utf-8"))},
        )
        response.raise_for_status()
        if not response.json().get("ok", False):
            raise RuntimeError(f"Telegram API rejected log file: {response.text}")

    @override
    def close(self) -> None:
        """Закрыть handler и освободить ресурсы."""
        # Отправляем сигнал завершения в очередь
        self._queue.put(None)
        # Ждём завершения потока (максимум 5 секунд)
        self._worker_thread.join(timeout=5.0)
        # Закрываем HTTP-клиент
        self._http_client.close()
        super().close()


def _should_use_colors() -> bool:
    """Определить, поддерживает ли терминал цвета.

    Проверяет:
    1. Переменную окружения NO_COLOR (стандарт https://no-color.org/)
    2. Является ли stdout терминалом (tty)

    Returns:
        True если можно использовать цвета.
    """
    import os

    # Стандарт NO_COLOR — если установлена, цвета отключены
    if os.environ.get("NO_COLOR"):
        return False

    # Проверяем, является ли stdout терминалом
    # В Docker/CI/перенаправлении в файл — isatty() вернёт False
    return sys.stdout.isatty()


def setup_logging(
    level: str = "INFO",
    timezone_name: str = "Europe/Moscow",
    telegram_settings: "TelegramLoggingSettings | None" = None,
    bot_token: str | None = None,
    proxy_url: str | None = None,
) -> None:
    """Настроить логирование приложения.

    Логи выводятся в консоль (с цветной подсветкой) и сохраняются в файл с ротацией.
    Файлы логов: data/logs/app.log (максимум 5 МБ, 3 резервных копии).

    Компактный формат логов:
        25-01-07 21:55:46 | INFO | bot.handlers.chatgpt | Сообщение

    Особенности:
    - Короткий год (25 вместо 2025) — экономит 2 символа
    - Убран префикс "src." — он везде одинаковый
    - Уровни логирования выделяются цветом в консоли

    Цвета уровней:
        - DEBUG:    голубой
        - INFO:     зелёный (как у Uvicorn)
        - WARNING:  жёлтый
        - ERROR:    красный
        - CRITICAL: пурпурный

    Опционально можно настроить отправку логов в Telegram.
    Для этого нужно передать telegram_settings и bot_token.

    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        timezone_name: Часовой пояс для отображения времени в логах.
        telegram_settings: Настройки Telegram-логирования (опционально).
        bot_token: Токен бота для отправки логов в Telegram.
    """
    # Создаём папку для логов если её нет
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Компактный формат: короткий год, без лишних пробелов
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    date_format = "%y-%m-%d %H:%M:%S"  # 25-01-07 вместо 2025-01-07

    # Определяем, поддерживает ли терминал цвета
    use_colors = _should_use_colors()

    # Форматтер для консоли (с цветами)
    console_formatter = ColoredFormatter(
        log_format,
        datefmt=date_format,
        timezone_name=timezone_name,
        use_colors=use_colors,
    )

    # Форматтер для файла (без цветов)
    file_formatter = TimezoneFormatter(
        log_format, datefmt=date_format, timezone_name=timezone_name
    )

    # Корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    # Консольный вывод (с цветами)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # Файловый вывод с ротацией (без цветов)
    # maxBytes=5MB, backupCount=3 — хранит app.log + app.log.1, app.log.2, app.log.3
    file_handler = RotatingFileHandler(
        LOGS_DIR / "app.log",
        maxBytes=5 * 1024 * 1024,  # 5 МБ
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    # Telegram handler (опционально)
    # Настраивается если указан chat_id и есть токен бота
    if telegram_settings and telegram_settings.is_enabled and bot_token:
        telegram_level = getattr(
            logging, telegram_settings.level.upper(), logging.ERROR
        )
        telegram_handler = TelegramHandler(
            bot_token=bot_token,
            chat_id=telegram_settings.chat_id,  # type: ignore[arg-type]
            level=telegram_level,
            proxy_url=proxy_url,
        )
        root_logger.addHandler(telegram_handler)

    # ===========================================================================
    # Настраиваем логирование Uvicorn для унифицированного формата
    # ===========================================================================
    #
    # Uvicorn по умолчанию использует свой формат логов.
    # Мы переопределяем его, чтобы все логи выглядели одинаково.

    # Uvicorn.error — основные сообщения сервера (startup, shutdown)
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.handlers = []
    uvicorn_error.addHandler(console_handler)
    uvicorn_error.addHandler(file_handler)
    uvicorn_error.propagate = False

    # Uvicorn.access — логи HTTP-запросов
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers = []
    uvicorn_access.addHandler(console_handler)
    uvicorn_access.addHandler(file_handler)
    uvicorn_access.propagate = False

    # Uvicorn — родительский логгер
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.handlers = []
    uvicorn_logger.propagate = False

    # Настройка уровней логирования внешних библиотек.
    # INFO для aiogram — позволяет видеть ошибки и важные события Telegram API.
    # WARNING для остальных — уменьшает шум, но ошибки всё равно видны.
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Получить логгер с указанным именем.

    Args:
        name: Имя логгера (обычно __name__).

    Returns:
        Настроенный экземпляр логгера.
    """
    return logging.getLogger(name)
