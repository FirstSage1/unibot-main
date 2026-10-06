"""Middleware для проверки подписки пользователя на канал.

Этот модуль блокирует пользователей, которые не подписаны на обязательный канал.
Проверка включается через переменную окружения CHANNEL__REQUIRED_ID.

Логика работы:
1. Если CHANNEL__REQUIRED_ID не задан — middleware пропускает все запросы
2. Если задан — проверяем подписку через Telegram API (getChatMember)
3. Результат кешируется на время из config.yaml (channel_subscription.cache_ttl_seconds)
4. При ошибке API (нет прав, сеть недоступна) — пропускаем пользователя (fallback)
5. Если пользователь не подписан — отправляем сообщение с кнопкой

Кеширование:
- Результат проверки хранится в памяти (in-memory)
- Формат кеша: {user_id: (is_member: bool, expires_at: float)}
- TTL задаётся в config.yaml: channel_subscription.cache_ttl_seconds
- Кеш автоматически очищается при истечении TTL

Fallback при ошибках:
- Если не удалось проверить подписку (ошибка API, бот не админ) — пропускаем
- Это бизнес-решение: лучше пропустить, чем заблокировать из-за misconfig

Dependency Injection:
- Middleware принимает bot, channel_id и invite_link как параметры
- Это позволяет легко тестировать с mock-объектами

Пример использования:
    from aiogram import Dispatcher, Bot
    from src.config.settings import settings
    from src.config.yaml_config import yaml_config

    # Создаём middleware если проверка включена
    if settings.channel.is_enabled:
        middleware = ChannelSubscriptionMiddleware(
            bot=bot,
            channel_id=settings.channel.required_id,
            invite_link=settings.channel.invite_link,
            cache_ttl_seconds=yaml_config.channel_subscription.cache_ttl_seconds,
        )
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)
"""

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

from aiogram import BaseMiddleware
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
    User,
)
from typing_extensions import override

from src.utils.i18n import Localization
from src.utils.logging import get_logger

if TYPE_CHECKING:
    from aiogram import Bot

logger = get_logger(__name__)

# Константа для callback_data кнопки "Проверить подписку"
# Формат: check_channel_sub — проверить подписку на канал
CALLBACK_CHECK_SUBSCRIPTION = "check_channel_sub"

# Статусы, которые считаются "подписан на канал"
# member — обычный участник
# administrator — админ канала
# creator — создатель канала
SUBSCRIBED_STATUSES = frozenset(
    {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    }
)


class BotProtocol(Protocol):
    """Протокол для Bot (для Dependency Injection в тестах)."""

    async def get_chat_member(self, chat_id: int, user_id: int) -> Any:
        """Получить информацию об участнике чата."""
        ...


class TimeProvider(Protocol):
    """Протокол для получения текущего времени (для тестирования).

    Позволяет инжектировать mock-провайдер времени в тестах
    для детерминированного тестирования кеша.
    """

    def __call__(self) -> float:
        """Вернуть текущее время в секундах (epoch)."""
        ...


def default_time_provider() -> float:
    """Стандартный провайдер времени — time.time()."""
    return time.time()


class ChannelSubscriptionMiddleware(BaseMiddleware):
    """Middleware для проверки подписки на канал.

    Блокирует пользователей, которые не подписаны на обязательный канал.
    Результат проверки кешируется для уменьшения нагрузки на Telegram API.

    Attributes:
        _bot: Экземпляр Telegram-бота для вызова API.
        _channel_id: ID канала для проверки подписки.
        _invite_link: Ссылка на канал для кнопки "Подписаться".
        _cache_ttl_seconds: Время жизни кеша в секундах.
        _cache: Словарь {user_id: (is_member: bool, expires_at: float)}.
        _time_provider: Функция для получения текущего времени.
    """

    def __init__(
        self,
        bot: "Bot | BotProtocol",
        channel_id: int,
        invite_link: str | None = None,
        cache_ttl_seconds: int = 300,
        time_provider: TimeProvider | None = None,
    ) -> None:
        """Создать middleware для проверки подписки.

        Args:
            bot: Экземпляр Telegram-бота.
            channel_id: ID канала для проверки (отрицательное число вида -100...).
            invite_link: Ссылка на канал для кнопки "Подписаться".
                Формат: @channelname или https://t.me/channelname
                Если None — кнопка не отображается.
            cache_ttl_seconds: Время жизни кеша результата проверки.
                По умолчанию 300 секунд (5 минут).
                0 — кеширование отключено (проверка на каждый запрос).
            time_provider: Провайдер времени (для тестирования).
                По умолчанию используется time.time().
        """
        # Валидация channel_id: должен быть отрицательным (каналы/супергруппы)
        if channel_id >= 0:
            raise ValueError(
                f"channel_id должен быть отрицательным (канал/супергруппа), "
                f"получено: {channel_id}"
            )

        super().__init__()
        self._bot = bot
        self._channel_id = channel_id
        self._invite_link = invite_link
        self._cache_ttl_seconds = cache_ttl_seconds
        self._time_provider = time_provider or default_time_provider

        # Кеш: {user_id: (is_member: bool, expires_at: float)}
        self._cache: dict[int, tuple[bool, float]] = {}

        logger.info(
            "ChannelSubscriptionMiddleware инициализирован: "
            "channel_id=%d, invite_link=%s, cache_ttl=%d сек",
            channel_id,
            invite_link,
            cache_ttl_seconds,
        )

    @override
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Проверить подписку перед выполнением handler.

        Логика:
        1. Если это callback "check_channel_sub" — обрабатываем проверку подписки
        2. Получаем user_id из события
        3. Проверяем кеш — если есть валидный результат, используем его
        4. Если кеша нет или он устарел — делаем запрос к Telegram API
        5. При ошибке API — пропускаем пользователя (fallback)
        6. Если не подписан — блокируем и отправляем сообщение

        Args:
            handler: Обработчик события (следующий в цепочке middleware).
            event: Входящее событие (Message, CallbackQuery и т.д.).
            data: Словарь данных, передаваемых в handler.

        Returns:
            Результат выполнения handler или None (если заблокирован).
        """
        # Получаем пользователя из события
        telegram_user: User | None = data.get("event_from_user")
        if telegram_user is None:
            # Событие без пользователя (редкий случай) — пропускаем
            return await handler(event, data)

        user_id = telegram_user.id

        # Получаем объект локализации для сообщений
        l10n: Localization | None = data.get("l10n")

        # Специальная обработка callback "Проверить подписку"
        # Этот callback должен работать даже для неподписанных пользователей
        if (
            isinstance(event, CallbackQuery)
            and event.data == CALLBACK_CHECK_SUBSCRIPTION
        ):
            # Type narrowing: isinstance гарантирует что event это CallbackQuery
            return await self._handle_check_subscription_callback(event, user_id, l10n)

        # Проверяем подписку (из кеша или через API)
        is_subscribed = await self._check_subscription(user_id)

        if is_subscribed:
            # Пользователь подписан — пропускаем к handler
            return await handler(event, data)

        # Пользователь НЕ подписан — блокируем
        logger.info(
            "Пользователь %d заблокирован: не подписан на канал %d",
            user_id,
            self._channel_id,
        )

        # Отправляем сообщение с просьбой подписаться
        await self._send_subscription_required_message(event, l10n)

        # Возвращаем None — handler не вызывается
        return None

    async def _handle_check_subscription_callback(
        self,
        callback: CallbackQuery,
        user_id: int,
        l10n: Localization | None,
    ) -> None:
        """Обработать callback кнопки "Проверить подписку".

        Проверяет подписку пользователя и отправляет соответствующее сообщение:
        - Если не подписан — показывает сообщение с просьбой подписаться
        - Если подписан — благодарит и предлагает нажать /start

        Args:
            callback: Входящий callback от кнопки.
            user_id: ID пользователя в Telegram.
            l10n: Объект локализации для текстов.
        """
        # Очищаем кеш для актуальной проверки
        self.clear_cache(user_id)

        # Проверяем подписку напрямую через API (кеш очищен)
        is_subscribed = await self._check_subscription(user_id)

        if is_subscribed:
            # Пользователь подписался — благодарим
            logger.info(
                "Пользователь %d подтвердил подписку на канал %d",
                user_id,
                self._channel_id,
            )

            if l10n is not None:
                text = l10n.get("channel_subscription_thanks")
            else:
                text = (
                    "✅ Спасибо за подписку!\n\n"
                    "Теперь вы можете пользоваться ботом.\n"
                    "Нажмите /start чтобы начать."
                )

            # Отвечаем на callback (убираем "часики" на кнопке)
            await callback.answer()

            # Редактируем исходное сообщение, убирая кнопки
            try:
                if callback.message and isinstance(callback.message, Message):
                    await callback.message.edit_text(text)
            except TelegramAPIError as e:
                logger.warning(
                    "Не удалось отредактировать сообщение о подписке: %s",
                    e,
                )
        else:
            # Пользователь ещё не подписан — напоминаем
            logger.info(
                "Пользователь %d ещё не подписан на канал %d (проверка по кнопке)",
                user_id,
                self._channel_id,
            )

            if l10n is not None:
                text = l10n.get("channel_subscription_not_subscribed")
            else:
                text = (
                    "❌ Вы ещё не подписаны на канал.\n\n"
                    "Пожалуйста, подпишитесь на канал и нажмите кнопку проверки снова."
                )

            # Показываем сообщение во всплывающем окне (alert) и оставляем кнопки
            # callback.answer с текстом отображает popup
            try:
                await callback.answer(text, show_alert=True)
            except TelegramAPIError as e:
                logger.warning(
                    "Не удалось отправить alert о неподписке: %s",
                    e,
                )

    async def _check_subscription(self, user_id: int) -> bool:
        """Проверить подписку пользователя на канал.

        Сначала проверяем кеш. Если кеш валиден — возвращаем результат.
        Если кеша нет или он устарел — делаем запрос к API.
        При ошибке API — возвращаем True (fallback).

        Args:
            user_id: ID пользователя в Telegram.

        Returns:
            True если подписан (или ошибка API), False если не подписан.
        """
        current_time = self._time_provider()

        # Проверяем кеш
        if user_id in self._cache:
            is_member, expires_at = self._cache[user_id]
            if current_time < expires_at:
                # Кеш валиден
                logger.debug(
                    "Кеш подписки для user_id=%d: is_member=%s (до %s)",
                    user_id,
                    is_member,
                    expires_at,
                )
                return is_member

            # Кеш устарел — удаляем
            del self._cache[user_id]
            logger.debug(
                "Кеш подписки для user_id=%d устарел, удаляю",
                user_id,
            )

        # Кеша нет — проверяем через API
        try:
            chat_member = await self._bot.get_chat_member(
                chat_id=self._channel_id,
                user_id=user_id,
            )
            is_member = chat_member.status in SUBSCRIBED_STATUSES

            logger.debug(
                "Проверка подписки через API: user_id=%d, status=%s, is_member=%s",
                user_id,
                chat_member.status,
                is_member,
            )

        except TelegramAPIError as e:
            # Ошибка API — пропускаем пользователя (fallback)
            logger.warning(
                "Ошибка проверки подписки для user_id=%d на канал %d: %s. "
                "Fallback: пропускаем пользователя.",
                user_id,
                self._channel_id,
                e,
            )
            # НЕ кешируем ошибку — при следующем запросе попробуем снова
            return True

        # Сохраняем в кеш (если кеширование включено)
        if self._cache_ttl_seconds > 0:
            expires_at = current_time + self._cache_ttl_seconds
            self._cache[user_id] = (is_member, expires_at)
            logger.debug(
                "Сохранено в кеш: user_id=%d, is_member=%s, expires_at=%s",
                user_id,
                is_member,
                expires_at,
            )

        return is_member

    def _format_channel_url(self) -> str | None:
        """Преобразовать invite_link в полный URL канала.

        Поддерживаемые форматы:
        - @channelname -> https://t.me/channelname
        - https://t.me/... -> как есть
        - channelname -> https://t.me/channelname

        Returns:
            URL канала или None если invite_link не задан.
        """
        if not self._invite_link:
            return None

        if self._invite_link.startswith("@"):
            return f"https://t.me/{self._invite_link[1:]}"
        if self._invite_link.startswith("https://"):
            return self._invite_link
        return f"https://t.me/{self._invite_link}"

    def _create_subscription_keyboard(
        self, l10n: Localization | None
    ) -> InlineKeyboardMarkup:
        """Создать клавиатуру с кнопками подписки.

        Создаёт клавиатуру с двумя кнопками:
        1. "Подписаться на канал" (URL) — если задан invite_link
        2. "Проверить подписку" (callback)

        Args:
            l10n: Объект локализации для текстов кнопок.

        Returns:
            Клавиатура с кнопками.
        """
        buttons: list[list[InlineKeyboardButton]] = []

        # Кнопка "Подписаться на канал" (если есть invite_link)
        channel_url = self._format_channel_url()
        if channel_url:
            subscribe_text = (
                l10n.get("channel_subscription_button")
                if l10n
                else "📢 Подписаться на канал"
            )
            buttons.append([InlineKeyboardButton(text=subscribe_text, url=channel_url)])

        # Кнопка "Проверить подписку" (callback)
        check_text = (
            l10n.get("channel_subscription_check_button")
            if l10n
            else "🔄 Проверить подписку"
        )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=check_text,
                    callback_data=CALLBACK_CHECK_SUBSCRIPTION,
                )
            ]
        )

        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def _send_subscription_required_message(
        self,
        event: TelegramObject,
        l10n: Localization | None,
    ) -> None:
        """Отправить сообщение с просьбой подписаться на канал.

        Отправляет локализованное сообщение с двумя кнопками:
        1. "Подписаться на канал" — ссылка на канал (URL)
        2. "Проверить подписку" — callback для проверки подписки

        Args:
            event: Событие (Message или CallbackQuery).
            l10n: Объект локализации для текстов.
        """
        # Получаем текст сообщения
        text = (
            l10n.get("channel_subscription_required")
            if l10n
            else (
                "⚠️ Для использования бота необходимо подписаться на наш канал.\n\n"
                "После подписки нажмите кнопку «Проверить подписку»."
            )
        )

        keyboard = self._create_subscription_keyboard(l10n)

        # Отправляем сообщение в зависимости от типа события
        try:
            if isinstance(event, Message):
                await event.answer(text, reply_markup=keyboard)
            elif isinstance(event, CallbackQuery):
                await event.answer(show_alert=False)
                if event.message:
                    await event.message.answer(text, reply_markup=keyboard)
        except TelegramAPIError as e:
            logger.warning(
                "Не удалось отправить сообщение о подписке: %s",
                e,
            )

    def clear_cache(self, user_id: int | None = None) -> None:
        """Очистить кеш подписки.

        Используется для принудительной перепроверки подписки,
        например, после callback от кнопки "Я подписался".

        Args:
            user_id: ID пользователя для очистки.
                Если None — очищается весь кеш.
        """
        if user_id is None:
            count = len(self._cache)
            self._cache.clear()
            logger.info("Кеш подписки полностью очищен (%d записей)", count)
        elif user_id in self._cache:
            del self._cache[user_id]
            logger.debug("Кеш подписки очищен для user_id=%d", user_id)


def create_channel_subscription_middleware(
    bot: "Bot",
    channel_id: int,
    invite_link: str | None = None,
    cache_ttl_seconds: int = 300,
) -> ChannelSubscriptionMiddleware:
    """Создать ChannelSubscriptionMiddleware с production зависимостями.

    Это factory function для удобного создания middleware в production.

    Args:
        bot: Экземпляр Telegram-бота.
        channel_id: ID канала для проверки подписки.
        invite_link: Ссылка на канал для кнопки.
        cache_ttl_seconds: TTL кеша в секундах.

    Returns:
        Настроенный ChannelSubscriptionMiddleware.

    Example:
        if settings.channel.is_enabled:
            middleware = create_channel_subscription_middleware(
                bot=bot,
                channel_id=settings.channel.required_id,
                invite_link=settings.channel.invite_link,
                cache_ttl_seconds=yaml_config.channel_subscription.cache_ttl_seconds,
            )
            dp.message.middleware(middleware)
            dp.callback_query.middleware(middleware)
    """
    return ChannelSubscriptionMiddleware(
        bot=bot,
        channel_id=channel_id,
        invite_link=invite_link,
        cache_ttl_seconds=cache_ttl_seconds,
    )
