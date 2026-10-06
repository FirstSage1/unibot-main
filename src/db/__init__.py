"""Модуль базы данных.

Содержит:
- base.py — подключение к БД (engine, session, Base)
- models_base.py — базовый класс для моделей (без загрузки settings)
- models/ — модели SQLAlchemy (таблицы)
- repositories/ — репозитории для работы с данными

Для изоляции тестов используйте:
    from src.db.models_base import Base  # Без загрузки settings

Для runtime-использования с реальной БД:
    from src.db.base import DatabaseSession, get_session
"""

# Не импортируем из base.py здесь, чтобы тесты могли импортировать
# Base из models_base.py без загрузки settings.
# Для доступа к функциям БД используйте прямой импорт:
#   from src.db.base import DatabaseSession, get_session
