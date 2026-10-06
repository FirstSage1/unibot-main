"""Утилиты для работы с Telegram API.

Этот модуль содержит вспомогательные функции для:
- Отправки typing indicators (показывает пользователю статус: "печатает", "фото")
- Разбиения длинных сообщений на части (лимит Telegram — 4096 символов)
- Работы с data URL для отправки изображений

Зачем нужны typing indicators:
    Telegram показывает пользователю статус действия бота.
    Например, "бот печатает..." или "бот отправляет фото...".
    Это улучшает UX, так как пользователь понимает, что запрос обрабатывается.

Зачем нужно разбиение сообщений:
    Telegram имеет лимит на длину сообщения — 4096 символов.
    Если AI сгенерировал длинный ответ, его нужно разбить на части.
    Мы разбиваем по границам абзацев, чтобы не резать текст посередине предложения.
"""

import base64
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram.enums import ChatAction
from aiogram.types import BufferedInputFile, Chat, Message, URLInputFile

from src.providers.ai.base import GenerationType

# ==============================================================================
# КОНСТАНТЫ
# ==============================================================================

# Максимальная длина сообщения в Telegram.
# Если сообщение длиннее — нужно разбить на части.
# Официальный лимит: 4096 символов для обычных сообщений.
# Используем немного меньше для безопасности (учитываем Unicode и форматирование).
TELEGRAM_MESSAGE_MAX_LENGTH = 4000

# Маппинг типа генерации на ChatAction в Telegram.
# Telegram показывает пользователю разные статусы в зависимости от действия:
# - typing — "бот печатает..." (для текстовых ответов)
# - upload_photo — "бот отправляет фото..." (для изображений)
# - upload_video — "бот отправляет видео..." (для видео)
# - upload_document — "бот отправляет файл..." (для документов)
# - record_voice — "бот записывает голосовое..." (для TTS/аудио)
GENERATION_TYPE_TO_CHAT_ACTION: dict[GenerationType, ChatAction] = {
    GenerationType.CHAT: ChatAction.TYPING,
    GenerationType.IMAGE: ChatAction.UPLOAD_PHOTO,
    GenerationType.TTS: ChatAction.RECORD_VOICE,
    GenerationType.STT: ChatAction.TYPING,  # STT возвращает текст, поэтому typing
}


# ==============================================================================
# TYPING INDICATORS
# ==============================================================================


def get_chat_action_for_generation_type(generation_type: GenerationType) -> ChatAction:
    """Получить ChatAction для типа генерации.

    Каждому типу генерации соответствует свой ChatAction в Telegram:
    - CHAT → typing (бот печатает...)
    - IMAGE → upload_photo (бот отправляет фото...)
    - TTS → record_voice (бот записывает голосовое...)
    - STT → typing (результат — текст)

    Args:
        generation_type: Тип генерации (CHAT, IMAGE, TTS, STT).

    Returns:
        ChatAction для отправки в Telegram.
        Если тип не найден — возвращает TYPING как дефолтное значение.

    Examples:
        >>> get_chat_action_for_generation_type(GenerationType.CHAT)
        ChatAction.TYPING

        >>> get_chat_action_for_generation_type(GenerationType.IMAGE)
        ChatAction.UPLOAD_PHOTO
    """
    return GENERATION_TYPE_TO_CHAT_ACTION.get(generation_type, ChatAction.TYPING)


@asynccontextmanager
async def typing_action(
    chat: Chat | Message,
    generation_type: GenerationType,
) -> AsyncIterator[None]:
    """Контекстный менеджер для отправки typing indicator.

    Отправляет в чат соответствующий ChatAction перед выполнением операции.
    Telegram показывает пользователю статус ("печатает...", "отправляет фото..." и т.д.)
    в течение 5 секунд или до получения следующего сообщения от бота.

    Пример использования:
        async with typing_action(message.chat, GenerationType.CHAT):
            result = await ai_service.generate(...)
            await message.answer(result.content)

    Args:
        chat: Чат или сообщение, в который отправить action.
            Если передан Message — используется message.chat.
        generation_type: Тип генерации для определения ChatAction.

    Yields:
        None — просто выполняет код внутри контекста.

    Note:
        ChatAction автоматически сбрасывается после отправки сообщения ботом.
        Если генерация занимает больше 5 секунд — Telegram перестаёт показывать статус.
        Для долгих операций рекомендуется периодически повторять отправку action.
    """
    # Получаем объект чата (если передан Message — берём chat из него)
    chat_obj = chat.chat if isinstance(chat, Message) else chat

    # Получаем соответствующий ChatAction
    action = get_chat_action_for_generation_type(generation_type)

    # Отправляем action в Telegram
    # Это покажет пользователю статус "печатает..." / "отправляет фото..." и т.д.
    await chat_obj.bot.send_chat_action(chat_id=chat_obj.id, action=action)  # type: ignore[union-attr]

    yield


async def send_chat_action(
    chat: Chat | Message,
    generation_type: GenerationType,
) -> None:
    """Отправить typing indicator в чат.

    Простая функция (не контекстный менеджер) для отправки ChatAction.
    Полезна когда не нужен контекстный менеджер, например:
    - Перед длинной операцией
    - Периодически во время долгой генерации

    Args:
        chat: Чат или сообщение, в который отправить action.
        generation_type: Тип генерации для определения ChatAction.

    Examples:
        # Отправить "печатает..." перед генерацией
        await send_chat_action(message, GenerationType.CHAT)
        result = await ai_service.generate(...)
    """
    chat_obj = chat.chat if isinstance(chat, Message) else chat
    action = get_chat_action_for_generation_type(generation_type)
    await chat_obj.bot.send_chat_action(chat_id=chat_obj.id, action=action)  # type: ignore[union-attr]


# ==============================================================================
# РАЗБИЕНИЕ ДЛИННЫХ СООБЩЕНИЙ
# ==============================================================================


def split_long_message(
    text: str,
    max_length: int = TELEGRAM_MESSAGE_MAX_LENGTH,
) -> list[str]:
    """Разбить длинное сообщение на части.

    Telegram имеет лимит на длину сообщения — 4096 символов.
    Эта функция разбивает длинный текст на части, стараясь:
    1. Не превышать лимит в каждой части
    2. Разбивать по границам абзацев (двойной перенос строки)
    3. Если абзац слишком длинный — разбивать по одинарному переносу
    4. Если строка слишком длинная — разбивать по пробелам (между словами)
    5. В крайнем случае — разбивать посимвольно

    Args:
        text: Исходный текст для разбиения.
        max_length: Максимальная длина каждой части.
            По умолчанию: 4000 символов (с запасом от лимита 4096).

    Returns:
        Список частей сообщения.
        Если текст короче max_length — возвращает список с одним элементом.

    Examples:
        >>> split_long_message("Короткий текст")
        ['Короткий текст']

        >>> split_long_message("A" * 5000, max_length=4000)
        ['AAAA...', 'AAAA...']  # Две части
    """
    # Если текст короткий — возвращаем как есть
    if len(text) <= max_length:
        return [text]

    parts: list[str] = []
    remaining = text

    while remaining:
        # Если остаток помещается в одно сообщение — добавляем и выходим
        if len(remaining) <= max_length:
            parts.append(remaining)
            break

        # Ищем место для разбиения (приоритет: абзац > строка > слово > символ)
        split_index = _find_split_index(remaining, max_length)

        # Добавляем часть и продолжаем с остатком
        parts.append(remaining[:split_index].rstrip())
        remaining = remaining[split_index:].lstrip()

    return parts


def _find_split_index(text: str, max_length: int) -> int:
    """Найти оптимальный индекс для разбиения текста.

    Ищет место для разбиения в порядке приоритета:
    1. Двойной перенос строки (граница абзаца) — лучший вариант
    2. Одинарный перенос строки (граница строки)
    3. Пробел (граница слова)
    4. Если ничего не найдено — max_length (разбиваем посимвольно)

    Args:
        text: Текст для поиска точки разбиения.
        max_length: Максимальная длина части.

    Returns:
        Индекс, по которому нужно разбить текст.
    """
    # Ищем в пределах max_length
    search_area = text[:max_length]

    # 1. Ищем границу абзаца (двойной перенос)
    # rfind ищет с конца — это важно, чтобы взять максимально длинную часть
    paragraph_break = search_area.rfind("\n\n")
    if paragraph_break > 0:
        return paragraph_break + 2  # +2 чтобы включить \n\n в текущую часть

    # 2. Ищем границу строки (одинарный перенос)
    line_break = search_area.rfind("\n")
    if line_break > 0:
        return line_break + 1  # +1 чтобы включить \n в текущую часть

    # 3. Ищем границу слова (пробел)
    space = search_area.rfind(" ")
    if space > 0:
        return space + 1  # +1 чтобы пробел остался в текущей части

    # 4. Не нашли хороших мест — разбиваем по max_length
    # Это происходит только если в тексте нет пробелов/переносов
    # (например, очень длинный URL или код без пробелов)
    return max_length


async def send_long_message(
    message: Message,
    text: str,
    max_length: int = TELEGRAM_MESSAGE_MAX_LENGTH,
    **kwargs: object,
) -> list[Message]:
    """Отправить длинное сообщение, разбив на части при необходимости.

    Удобная функция для отправки сообщений любой длины.
    Автоматически разбивает текст на части, если он превышает лимит Telegram.

    Перед отправкой первой части показывает typing indicator.

    Args:
        message: Исходное сообщение (для получения chat_id).
        text: Текст для отправки.
        max_length: Максимальная длина каждой части.
        **kwargs: Дополнительные параметры для message.answer().
            Например: parse_mode, reply_markup, disable_notification.

    Returns:
        Список отправленных сообщений.
        Если текст короткий — список с одним сообщением.

    Examples:
        # Отправить длинный ответ AI
        sent_messages = await send_long_message(
            message,
            ai_response,
            parse_mode=ParseMode.MARKDOWN,
        )
        print(f"Отправлено {len(sent_messages)} сообщений")
    """
    parts = split_long_message(text, max_length)
    sent_messages: list[Message] = []

    for i, part in enumerate(parts):
        # Перед первой частью показываем typing
        if i == 0:
            await send_chat_action(message, GenerationType.CHAT)

        sent_msg = await message.answer(part, **kwargs)  # type: ignore[arg-type]
        sent_messages.append(sent_msg)

    return sent_messages


# ==============================================================================
# РАБОТА С ИЗОБРАЖЕНИЯМИ
# ==============================================================================

# Регулярное выражение для парсинга data URL.
# Формат: data:image/png;base64,iVBORw0KGgo...
DATA_URL_PATTERN = re.compile(r"^data:image/(\w+);base64,(.+)$", re.DOTALL)


def create_input_file_from_url(
    url: str,
    filename: str = "image.png",
) -> BufferedInputFile | URLInputFile:
    """Создать InputFile из URL или data URL.

    AI-провайдеры могут возвращать изображения в разных форматах:
    1. HTTP URL (https://example.com/image.png) — используем URLInputFile
    2. Data URL (data:image/png;base64,...) — декодируем и используем BufferedInputFile

    Args:
        url: URL изображения (HTTP или data URL).
        filename: Имя файла для Telegram (используется при отправке).

    Returns:
        InputFile для отправки через Telegram API.

    Examples:
        >>> photo = create_input_file_from_url("data:image/png;base64,iVBORw0...")
        >>> await message.answer_photo(photo=photo)

        >>> photo = create_input_file_from_url("https://example.com/image.png")
        >>> await message.answer_photo(photo=photo)
    """
    # Проверяем, является ли URL data URL
    match = DATA_URL_PATTERN.match(url)
    if match:
        # Извлекаем формат и base64-данные
        image_format = match.group(1)  # png, jpeg, gif, etc.
        base64_data = match.group(2)

        # Декодируем base64 в байты
        image_bytes = base64.b64decode(base64_data)

        # Создаём имя файла с правильным расширением
        file_extension = image_format.lower()
        if file_extension == "jpeg":
            file_extension = "jpg"
        actual_filename = f"image.{file_extension}"

        return BufferedInputFile(file=image_bytes, filename=actual_filename)

    # Обычный HTTP URL — используем URLInputFile
    return URLInputFile(url=url, filename=filename)
