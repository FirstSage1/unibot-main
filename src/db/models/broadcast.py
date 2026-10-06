"""Модель рассылки (broadcast).

Рассылка — это массовая отправка сообщений пользователям бота.
Поддерживает сегментацию получателей по различным критериям:
- По языку интерфейса
- По дате регистрации
- По наличию оплат
- По источнику (start param)
- По статусу блокировки

Жизненный цикл рассылки:
1. DRAFT — черновик, можно редактировать
2. PENDING — ожидает запуска (запланирована или готова к старту)
3. RUNNING — выполняется отправка
4. PAUSED — приостановлена (можно возобновить)
5. COMPLETED — завершена успешно
6. CANCELLED — отменена администратором
7. FAILED — завершена с критической ошибкой

Особенности:
- Прогресс сохраняется после каждого батча (last_processed_user_id)
- При перезапуске бота рассылка продолжается с места остановки
- Rate limiting настраивается в config.yaml
- FloodWait от Telegram обрабатывается автоматически
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing_extensions import override

from src.db.models_base import Base

if TYPE_CHECKING:
    from src.db.models.user import User


class BroadcastStatus(StrEnum):
    """Статус рассылки — определяет текущее состояние.

    Используется для:
    - Отображения в админке
    - Логики обработки в BroadcastWorker
    - Фильтрации рассылок

    Значения:
        DRAFT: Черновик — можно редактировать, не запущена.
        PENDING: Ожидает запуска — готова к отправке или запланирована.
        RUNNING: Выполняется — идёт отправка сообщений.
        PAUSED: Приостановлена — можно возобновить с места остановки.
        COMPLETED: Завершена — все сообщения отправлены.
        CANCELLED: Отменена — администратор отменил рассылку.
        FAILED: Ошибка — критическая ошибка, требует внимания.
    """

    DRAFT = "draft"
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ParseMode(StrEnum):
    """Режим форматирования текста сообщения.

    Telegram поддерживает несколько режимов форматирования:
    - HTML — теги <b>, <i>, <code>, <a href="..."> и др.
    - Markdown — *bold*, _italic_, `code`, [link](url)
    - MarkdownV2 — расширенный Markdown с экранированием
    - None — обычный текст без форматирования

    Рекомендуется использовать HTML — он более предсказуем.

    Документация: https://core.telegram.org/bots/api#formatting-options
    """

    HTML = "HTML"
    MARKDOWN = "Markdown"
    MARKDOWN_V2 = "MarkdownV2"
    NONE = "None"


class Broadcast(Base):
    """Рассылка — массовая отправка сообщений пользователям.

    Администратор создаёт рассылку в админке, указывая:
    - Текст сообщения (с форматированием)
    - Критерии сегментации (опционально)
    - Режим форматирования (HTML/Markdown)

    После запуска BroadcastWorker отправляет сообщения
    с соблюдением rate limiting и обработкой ошибок.

    Attributes:
        id: Уникальный ID рассылки (автоинкремент).
        name: Название рассылки для удобства администратора.
            Например: "Новогодняя акция 2025" или "Анонс новых функций".
        message_text: Текст сообщения для отправки.
            Поддерживает форматирование (HTML/Markdown).
        parse_mode: Режим форматирования текста (HTML, Markdown, None).
        status: Текущий статус рассылки (BroadcastStatus).
        created_by_id: ID администратора, создавшего рассылку.
            Используется для аудита и разграничения ответственности.
        created_at: Дата и время создания рассылки.
        started_at: Дата и время начала отправки.
            None если рассылка ещё не запускалась.
        completed_at: Дата и время завершения.
            None если рассылка ещё не завершена.

        # Сегментация — фильтры для выбора получателей
        # Если фильтр не указан (None) — не применяется
        filter_language: Фильтр по языку интерфейса.
            Например: "ru" — только русскоязычные пользователи.
        filter_has_payments: Фильтр по факту оплат.
            True — только платившие, False — только бесплатные, None — все.
        filter_source: Фильтр по источнику (start param).
            Например: "promo_winter" — пользователи, пришедшие по этой ссылке.
        filter_registered_after: Фильтр по дате регистрации (от).
            Только пользователи, зарегистрированные после этой даты.
        filter_registered_before: Фильтр по дате регистрации (до).
            Только пользователи, зарегистрированные до этой даты.
        filter_exclude_blocked: Исключить заблокированных пользователей.
            По умолчанию True — заблокированные не получают рассылку.

        # Статистика и прогресс
        total_recipients: Общее количество получателей на момент запуска.
            Вычисляется при старте рассылки по критериям сегментации.
        sent_count: Количество успешно отправленных сообщений.
        failed_count: Количество неудачных попыток отправки.
            Причины: пользователь заблокировал бота, удалил аккаунт и др.
        last_processed_user_id: ID последнего обработанного пользователя.
            Используется для возобновления рассылки после паузы/перезапуска.
        error_message: Текст последней ошибки (для статуса FAILED).

        # Связи
        created_by: Администратор, создавший рассылку.
    """

    __tablename__ = "broadcasts"

    # Первичный ключ
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Название рассылки для идентификации в админке
    # Например: "Новогодняя акция 2025", "Анонс подписок", "Технические работы"
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Название рассылки для администратора",
    )

    # Текст сообщения для отправки пользователям
    # Text — для длинных сообщений (до 4096 символов в Telegram)
    message_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Текст сообщения с форматированием (HTML/Markdown)",
    )

    # Режим форматирования текста
    # HTML — рекомендуемый режим, более предсказуемый
    parse_mode: Mapped[str] = mapped_column(
        String(20),
        default=ParseMode.HTML,
        nullable=False,
        doc="Режим форматирования: HTML, Markdown, MarkdownV2, None",
    )

    # Статус рассылки
    status: Mapped[str] = mapped_column(
        String(20),
        default=BroadcastStatus.DRAFT,
        nullable=False,
        index=True,  # Индекс для быстрого поиска активных рассылок
        doc="Статус: draft, pending, running, paused, completed, cancelled, failed",
    )

    # Кто создал рассылку (для аудита)
    # nullable=True — на случай удаления пользователя-администратора
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="ID администратора, создавшего рассылку",
    )

    # Временные метки
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        doc="Дата и время создания",
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        doc="Дата и время начала отправки",
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        doc="Дата и время завершения",
    )

    # ==========================================================================
    # СЕГМЕНТАЦИЯ — фильтры для выбора получателей
    # ==========================================================================
    # Если значение None — фильтр не применяется (все пользователи проходят)

    # Фильтр по языку интерфейса пользователя
    # Пример: "ru" — только пользователи с русским интерфейсом
    filter_language: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        doc="Фильтр по языку (ru, en, и т.д.). None = все языки",
    )

    # Фильтр по наличию оплат
    # True = только платившие пользователи (есть транзакции типа PURCHASE)
    # False = только бесплатные (нет транзакций PURCHASE)
    # None = все пользователи
    filter_has_payments: Mapped[bool | None] = mapped_column(
        nullable=True,
        doc="Фильтр по факту оплат. True=платившие, False=бесплатные, None=все",
    )

    # Фильтр по источнику (start param при регистрации)
    # Пример: "promo_winter" — пользователи по ссылке t.me/bot?start=promo_winter
    filter_source: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Фильтр по источнику (start param). None = все источники",
    )

    # Фильтр по дате регистрации — нижняя граница
    # Только пользователи, зарегистрированные ПОСЛЕ этой даты
    filter_registered_after: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        doc="Только зарегистрированные после этой даты",
    )

    # Фильтр по дате регистрации — верхняя граница
    # Только пользователи, зарегистрированные ДО этой даты
    filter_registered_before: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        doc="Только зарегистрированные до этой даты",
    )

    # Исключить заблокированных пользователей
    # По умолчанию True — заблокированным не отправляем
    filter_exclude_blocked: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
        doc="Исключить заблокированных (is_blocked=True)",
    )

    # ==========================================================================
    # СТАТИСТИКА И ПРОГРЕСС
    # ==========================================================================

    # Общее количество получателей (вычисляется при запуске)
    # 0 если рассылка ещё не запускалась
    total_recipients: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        doc="Общее количество получателей на момент запуска",
    )

    # Количество успешно отправленных сообщений
    sent_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        doc="Количество успешно отправленных",
    )

    # Количество неудачных попыток отправки
    # Причины: бот заблокирован, пользователь удалён, и др.
    failed_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        doc="Количество неудачных попыток",
    )

    # ID последнего обработанного пользователя
    # Используется для возобновления рассылки после паузы/перезапуска
    # При запуске рассылки обрабатываем пользователей с id > last_processed_user_id
    last_processed_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        doc="ID последнего обработанного пользователя (для возобновления)",
    )

    # Текст ошибки (для статуса FAILED)
    # Содержит описание критической ошибки, остановившей рассылку
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Текст последней критической ошибки",
    )

    # ==========================================================================
    # СВЯЗИ
    # ==========================================================================

    # Связь с пользователем (администратором), создавшим рассылку
    created_by: Mapped["User | None"] = relationship(
        lazy="selectin",
        doc="Администратор, создавший рассылку",
    )

    @override
    def __repr__(self) -> str:
        """Строковое представление для отладки."""
        return (
            f"<Broadcast(id={self.id}, name={self.name!r}, "
            f"status={self.status}, sent={self.sent_count}/{self.total_recipients})>"
        )

    @property
    def progress_percent(self) -> float:
        """Процент выполнения рассылки.

        Returns:
            Процент от 0 до 100. Если total_recipients=0, возвращает 0.
        """
        if self.total_recipients == 0:
            return 0.0
        processed = self.sent_count + self.failed_count
        return float(round(processed / self.total_recipients * 100, 1))

    @property
    def is_active(self) -> bool:
        """Активна ли рассылка (можно ли её обрабатывать).

        Returns:
            True если статус PENDING или RUNNING.
        """
        return self.status in (BroadcastStatus.PENDING, BroadcastStatus.RUNNING)

    @property
    def can_start(self) -> bool:
        """Можно ли запустить рассылку.

        Returns:
            True если статус DRAFT или PAUSED.
        """
        return self.status in (BroadcastStatus.DRAFT, BroadcastStatus.PAUSED)

    @property
    def can_pause(self) -> bool:
        """Можно ли приостановить рассылку.

        Returns:
            True если статус RUNNING.
        """
        return self.status == BroadcastStatus.RUNNING

    @property
    def can_cancel(self) -> bool:
        """Можно ли отменить рассылку.

        Returns:
            True если статус DRAFT, PENDING, RUNNING или PAUSED.
        """
        return self.status in (
            BroadcastStatus.DRAFT,
            BroadcastStatus.PENDING,
            BroadcastStatus.RUNNING,
            BroadcastStatus.PAUSED,
        )
