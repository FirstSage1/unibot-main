"""Статические файлы бота.

Этот модуль предоставляет удобный доступ к статическим файлам,
которые бот отправляет пользователям (изображения, аудио, видео).

Структура папок:
    static/
    ├── images/     # Изображения (welcome.jpg, help.png, error.png)
    ├── audio/      # Аудиофайлы
    └── video/      # Видеофайлы

Пример использования:
    from src.bot.static import get_image, WELCOME_IMAGE

    # Получить путь к файлу
    path = get_image("welcome.jpg")

    # Использовать предопределённую константу
    await message.answer_photo(FSInputFile(WELCOME_IMAGE))
"""

from pathlib import Path

from aiogram.types import FSInputFile

# Корневая директория статики
STATIC_DIR = Path(__file__).parent

# Директории по типам контента
IMAGES_DIR = STATIC_DIR / "images"
AUDIO_DIR = STATIC_DIR / "audio"
VIDEO_DIR = STATIC_DIR / "video"


# ==============================================================================
# ПРЕДОПРЕДЕЛЁННЫЕ ПУТИ К ФАЙЛАМ
# ==============================================================================
#
# Добавляйте сюда константы для часто используемых файлов.
# Это позволяет IDE подсказывать имена и ловить опечатки.

# Изображение для команды /start
WELCOME_IMAGE = IMAGES_DIR / "welcome.jpg"

# Изображение для команды /help
HELP_IMAGE = IMAGES_DIR / "help.png"

# Изображение при ошибках
ERROR_IMAGE = IMAGES_DIR / "error.png"


# ==============================================================================
# ФУНКЦИИ ДЛЯ ПОЛУЧЕНИЯ ПУТЕЙ
# ==============================================================================


def get_image(filename: str) -> Path:
    """Получить путь к изображению.

    Args:
        filename: Имя файла (например, "welcome.jpg").

    Returns:
        Полный путь к файлу.

    Raises:
        FileNotFoundError: Если файл не существует.

    Example:
        >>> path = get_image("welcome.jpg")
        >>> await message.answer_photo(FSInputFile(path))
    """
    path = IMAGES_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Изображение не найдено: {path}")
    return path


def get_audio(filename: str) -> Path:
    """Получить путь к аудиофайлу.

    Args:
        filename: Имя файла.

    Returns:
        Полный путь к файлу.

    Raises:
        FileNotFoundError: Если файл не существует.
    """
    path = AUDIO_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Аудиофайл не найден: {path}")
    return path


def get_video(filename: str) -> Path:
    """Получить путь к видеофайлу.

    Args:
        filename: Имя файла.

    Returns:
        Полный путь к файлу.

    Raises:
        FileNotFoundError: Если файл не существует.
    """
    path = VIDEO_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Видеофайл не найден: {path}")
    return path


def get_image_input(filename: str) -> FSInputFile:
    """Получить FSInputFile для изображения (готово к отправке).

    Args:
        filename: Имя файла.

    Returns:
        FSInputFile для использования в answer_photo().

    Example:
        >>> await message.answer_photo(get_image_input("welcome.jpg"))
    """
    return FSInputFile(get_image(filename))


def get_audio_input(filename: str) -> FSInputFile:
    """Получить FSInputFile для аудио (готово к отправке)."""
    return FSInputFile(get_audio(filename))


def get_video_input(filename: str) -> FSInputFile:
    """Получить FSInputFile для видео (готово к отправке)."""
    return FSInputFile(get_video(filename))
