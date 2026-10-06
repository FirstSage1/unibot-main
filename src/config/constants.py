"""Константы приложения."""

from pathlib import Path

# ==============================================================================
# ПУТИ К ФАЙЛАМ И ДИРЕКТОРИЯМ
# ==============================================================================

# Корень проекта (где лежит pyproject.toml)
PROJECT_ROOT = Path(__file__).parent.parent.parent


# Папка для данных (база, логи, медиафайлы)
#
# На Amvera: /data — персистентное хранилище (абсолютный путь обязателен!)
# Локально: ./data — папка в корне проекта
#
# Проверяем наличие /data — если есть, значит мы на Amvera
_AMVERA_DATA = Path("/data")
DATA_DIR = _AMVERA_DATA if _AMVERA_DATA.exists() else PROJECT_ROOT / "data"

# Создаём директорию если не существует (важно для первого запуска)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Папка для медиафайлов (изображения и видео для рассылок)
# На продакшене хранится на сетевом диске, чтобы не терялись при деплое
MEDIA_DIR = DATA_DIR / "media"
MEDIA_IMAGES_DIR = MEDIA_DIR / "images"
MEDIA_VIDEOS_DIR = MEDIA_DIR / "videos"
