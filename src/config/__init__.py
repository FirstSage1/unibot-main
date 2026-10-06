"""Модуль конфигурации.

Для доступа к настройкам используйте:
    from src.config.settings import settings

Для использования только классов настроек (без загрузки .env):
    from src.config.models import AIProvidersSettings
"""

# Не импортируем settings здесь, чтобы тесты могли импортировать
# другие модули из src.config без загрузки .env файла.
# Для доступа к settings используйте прямой импорт:
#   from src.config.settings import settings
