"""Планировщик задач (APScheduler).

Модуль содержит периодические задачи:
- Проверка и автопродление подписок
- Отправка напоминаний об истекающих подписках
- Обработка неудачных попыток продления

Планировщик запускается вместе с основным приложением
и работает как фоновая задача.
"""

from src.scheduler.runner import create_scheduler, start_scheduler, stop_scheduler

__all__ = ["create_scheduler", "start_scheduler", "stop_scheduler"]
