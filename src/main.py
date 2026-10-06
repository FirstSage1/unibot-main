"""Точка входа в приложение.

Запускает FastAPI-сервер и Telegram-бота одновременно.

Режимы работы бота:
- DEV (без домена): long polling
- PROD (APP__DOMAIN указан): webhook mode

Команда запуска:
    uvicorn src.main:app --host 0.0.0.0 --port 8000

Или через python:
    python -m src
    python src/main.py
"""

import sys
from pathlib import Path

# Добавляем корень проекта в sys.path для запуска через python src/main.py
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Импорты после sys.path, иначе не найдёт модуль src при запуске python src/main.py
from src.app import create_app  # noqa: E402
from src.config.settings import settings  # noqa: E402
from src.utils.logging import setup_logging  # noqa: E402

# Настраиваем логирование при импорте модуля
# Передаём настройки Telegram для отправки ошибок админу
setup_logging(
    level=settings.logging.level,
    timezone_name=settings.logging.timezone,
    telegram_settings=settings.logging.telegram,
    bot_token=settings.bot.token.get_secret_value(),
    proxy_url=settings.bot.proxy,
)

# Создаём FastAPI приложение
app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
