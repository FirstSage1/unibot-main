"""Управление планировщиком APScheduler.

Этот модуль предоставляет функции для:
- Создания и настройки планировщика
- Регистрации периодических задач
- Запуска и остановки планировщика

APScheduler используется для:
- Автопродления подписок (ежедневно в 06:00 UTC / 09:00 МСК)
- Обработки неудачных продлений (ежедневно)
- Истечения устаревших подписок (раз в день)

Интеграция с FastAPI:
    Планировщик запускается в lifespan FastAPI приложения.
    При остановке приложения планировщик корректно завершается.

Пример использования:
    from src.scheduler import create_scheduler, start_scheduler, stop_scheduler

    scheduler = create_scheduler(yaml_config, bot)
    start_scheduler(scheduler)
    ...
    stop_scheduler(scheduler)
"""

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.config.yaml_config import YamlConfig
from src.scheduler.tasks import (
    expire_stale_subscriptions,
    process_broadcasts,
    process_past_due,
    process_renewals,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


def create_scheduler(
    yaml_config: YamlConfig,
    bot: Bot,
) -> AsyncIOScheduler:
    """Создать и настроить планировщик задач.

    Регистрирует периодические задачи:
    - Подписки: автопродление, retry, истечение (если есть подписочные тарифы)
    - Рассылки: обработка активных рассылок каждые 30 секунд

    Планировщик НЕ запускается автоматически — нужно вызвать start_scheduler().

    Расписание задач:
    - process_renewals: ежедневно в 06:00 UTC (09:00 МСК)
    - process_past_due: ежедневно в 06:05 UTC
    - expire_stale_subscriptions: ежедневно в 03:00 UTC
    - process_broadcasts: каждые 30 секунд

    Args:
        yaml_config: YAML-конфигурация.
        bot: Telegram Bot для отправки уведомлений.

    Returns:
        Настроенный экземпляр AsyncIOScheduler (не запущенный).

    Example:
        scheduler = create_scheduler(yaml_config, bot)
        start_scheduler(scheduler)
    """
    scheduler = AsyncIOScheduler(timezone="UTC")

    # === ПОДПИСКИ ===
    # Регистрируем задачи подписок только если есть подписочные тарифы
    if not yaml_config.has_subscription_tariffs():
        logger.debug("Подписочные тарифы не найдены, задачи подписок не регистрируются")
    else:
        _register_subscription_jobs(scheduler, yaml_config, bot)

    # === РАССЫЛКИ ===
    # Обработка активных рассылок — каждые 30 секунд
    # Если активных рассылок нет — задача быстро завершается (один SELECT)
    scheduler.add_job(
        process_broadcasts,
        trigger=IntervalTrigger(seconds=30),
        kwargs={"yaml_config": yaml_config, "bot": bot},
        id="process_broadcasts",
        name="Обработка рассылок (каждые 30 сек)",
        replace_existing=True,
    )

    logger.info(
        "Планировщик создан с %d задачами",
        len(scheduler.get_jobs()),
    )

    return scheduler


def _register_subscription_jobs(
    scheduler: AsyncIOScheduler,
    yaml_config: YamlConfig,
    bot: Bot,
) -> None:
    """Зарегистрировать задачи для работы с подписками.

    Args:
        scheduler: Планировщик.
        yaml_config: YAML-конфигурация.
        bot: Telegram Bot.
    """
    # Проверка подписок и автопродление — 1 раз в день в 06:00 UTC (09:00 МСК)
    scheduler.add_job(
        process_renewals,
        trigger=CronTrigger(hour=6, minute=0),
        kwargs={"yaml_config": yaml_config, "bot": bot},
        id="process_renewals",
        name="Автопродление подписок (06:00 UTC)",
        replace_existing=True,
    )

    # Обработка PAST_DUE — раз в день в 06:00 UTC (вместе с renewals)
    # Попытки списания раз в день, количество дней = billing.renewal_retry_days
    scheduler.add_job(
        process_past_due,
        trigger=CronTrigger(hour=6, minute=5),
        kwargs={"yaml_config": yaml_config, "bot": bot},
        id="process_past_due",
        name="Повторные попытки продления (06:05 UTC)",
        replace_existing=True,
    )

    # Истечение устаревших подписок — раз в день в 03:00 UTC
    scheduler.add_job(
        expire_stale_subscriptions,
        trigger=CronTrigger(hour=3, minute=0),
        kwargs={"yaml_config": yaml_config},
        id="expire_stale_subscriptions",
        name="Истечение устаревших подписок",
        replace_existing=True,
    )


def start_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Запустить планировщик.

    Запускает выполнение зарегистрированных задач по расписанию.
    Планировщик работает в фоне и не блокирует event loop.

    Args:
        scheduler: Экземпляр AsyncIOScheduler.
    """
    if scheduler.running:
        logger.warning("Планировщик уже запущен")
        return

    scheduler.start()
    logger.info("Планировщик запущен")

    # Логируем зарегистрированные задачи
    for job in scheduler.get_jobs():
        logger.debug("  - %s: %s", job.id, job.next_run_time)


def stop_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Остановить планировщик.

    Корректно завершает все задачи и останавливает планировщик.
    Вызывается при остановке FastAPI приложения.

    Args:
        scheduler: Экземпляр AsyncIOScheduler.
    """
    if not scheduler.running:
        logger.debug("Планировщик не запущен, пропускаем остановку")
        return

    scheduler.shutdown(wait=False)
    logger.info("Планировщик остановлен")
