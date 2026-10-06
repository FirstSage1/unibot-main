"""Утилиты для работы с часовыми поясами.

Этот модуль предоставляет функции для работы с временем в указанной часовой зоне.

Как это работает:
1. Время в базе данных хранится в UTC (универсальное время)
2. При отображении пользователю время конвертируется в его часовой пояс
3. Часовой пояс задаётся через LOGGING__TIMEZONE в настройках
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def get_timezone(timezone_name: str) -> ZoneInfo:
    """Получить объект часового пояса по имени.

    Args:
        timezone_name: Название часового пояса из базы IANA.
            Примеры: "Europe/Moscow", "UTC", "America/New_York".

    Returns:
        Объект ZoneInfo для указанного часового пояса.

    Raises:
        ZoneInfoNotFoundError: Если указанный часовой пояс не найден.
    """
    return ZoneInfo(timezone_name)


def now_in_timezone(timezone_name: str) -> datetime:
    """Получить текущее время в указанном часовом поясе.

    Args:
        timezone_name: Название часового пояса из базы IANA.

    Returns:
        Текущее время с информацией о часовом поясе.
    """
    tz = get_timezone(timezone_name)
    return datetime.now(tz)


def to_timezone(dt: datetime, timezone_name: str) -> datetime:
    """Конвертировать время в указанный часовой пояс.

    Если время не имеет информации о часовом поясе (naive datetime),
    предполагается, что это UTC.

    Args:
        dt: Время для конвертации.
        timezone_name: Название целевого часового пояса.

    Returns:
        Время в указанном часовом поясе.
    """
    tz = get_timezone(timezone_name)

    # Если datetime naive (без tzinfo) — считаем его UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)

    return dt.astimezone(tz)


def ensure_utc_aware(dt: datetime) -> datetime:
    """Гарантировать, что datetime является timezone-aware в UTC.

    Если datetime naive (без tzinfo) — считаем его UTC и добавляем tzinfo.
    Если datetime уже aware — конвертируем в UTC.

    Это критично для сравнения datetime из БД (обычно naive) с datetime.now(UTC).
    SQLite и PostgreSQL (без timezone) возвращают naive datetime,
    но логически они хранят UTC. Эта функция делает это явным.

    Args:
        dt: Время для нормализации.

    Returns:
        Время с timezone=UTC.

    Example:
        >>> from datetime import datetime, UTC
        >>> naive_dt = datetime(2024, 1, 1, 12, 0, 0)  # Из БД
        >>> aware_dt = ensure_utc_aware(naive_dt)
        >>> aware_dt.tzinfo
        datetime.timezone.utc
        >>> # Теперь можно безопасно сравнивать:
        >>> (datetime.now(UTC) - aware_dt).total_seconds()
    """
    if dt.tzinfo is None:
        # Naive datetime из БД — считаем UTC
        return dt.replace(tzinfo=UTC)
    # Уже aware — конвертируем в UTC для консистентности
    return dt.astimezone(UTC)


def format_datetime(
    dt: datetime | None,
    timezone_name: str,
    fmt: str = "%d.%m.%Y %H:%M",
) -> str:
    """Отформатировать время в указанном часовом поясе.

    Удобная функция для отображения времени в интерфейсе.

    Args:
        dt: Время для форматирования. Если None — возвращает "-".
        timezone_name: Название часового пояса для отображения.
        fmt: Формат вывода (по умолчанию: "ДД.ММ.ГГГГ ЧЧ:ММ").

    Returns:
        Отформатированная строка с временем или "-" если dt is None.
    """
    if dt is None:
        return "-"

    local_dt = to_timezone(dt, timezone_name)
    return local_dt.strftime(fmt)
