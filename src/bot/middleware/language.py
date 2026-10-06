"""Middleware для определения языка пользователя.

Этот модуль автоматически определяет язык пользователя и создаёт
объект Localization для каждого события.

Логика определения языка:
1. Если мультиязычность отключена (localization.enabled=false)
   → используем язык по умолчанию
2. Получаем пользователя из БД по telegram_id
3. Если пользователь найден → используем его язык (User.language)
4. Если не найден → используем язык по умолчанию

Объект Localization добавляется в data["l10n"] и доступен в обработчиках:
    async def cmd_start(message: Message, l10n: Localization) -> None:
        await message.answer(l10n.get("start_message"))

Архитектура (Dependency Injection):
    # Production — использовать create_language_middleware()
    middleware = create_language_middleware()

    # Тесты — создать с mock session_factory и localization_factory
    middleware = LanguageMiddleware(
        session_factory=mock_session_factory,
        localization_factory=mock_localization_factory,
    )
"""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Protocol

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User
from sqlalchemy.exc import SQLAlchemyError
from typing_extensions import override

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories.user_repo import UserRepository
from src.utils.i18n import Localization, create_localization
from src.utils.logging import get_logger

# Алиас для читаемости
AsyncContextManager = AbstractAsyncContextManager

logger = get_logger(__name__)


class SessionFactory(Protocol):
    """Протокол для фабрики сессий БД (для Dependency Injection).

    Позволяет инжектировать mock session factory в тестах.
    DatabaseSession и AsyncSession совместимы с этим протоколом.
    """

    def __call__(self) -> "AsyncContextManager[AsyncSession]":
        """Создать асинхронный контекстный менеджер для сессии БД."""
        ...


class LocalizationFactory(Protocol):
    """Протокол для фабрики Localization (для Dependency Injection).

    Позволяет инжектировать mock localization factory в тестах.
    """

    def __call__(self, language: str) -> Localization:
        """Создать объект Localization для указанного языка."""
        ...


class LanguageMiddleware(BaseMiddleware):
    """Middleware для определения языка пользователя.

    Создаёт объект Localization на основе языка пользователя из БД.
    Если пользователь не найден или мультиязычность отключена —
    используется язык по умолчанию.

    Attributes:
        _session_factory: Фабрика для создания сессий БД (инжектируется).
        _localization_factory: Фабрика для создания Localization (инжектируется).

    Example:
        # Production
        middleware = create_language_middleware()
        dp.message.middleware(middleware)

        # Тесты
        middleware = LanguageMiddleware(
            session_factory=mock_session_factory,
            localization_factory=mock_localization_factory,
        )
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        localization_factory: LocalizationFactory,
    ) -> None:
        """Создать middleware с инжектированными зависимостями.

        Args:
            session_factory: Фабрика для создания сессий БД.
            localization_factory: Фабрика для создания Localization.
        """
        super().__init__()
        self._session_factory = session_factory
        self._localization_factory = localization_factory

    @override
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Обработать событие и добавить объект Localization в data.

        Args:
            handler: Следующий обработчик в цепочке.
            event: Событие от Telegram (Message, CallbackQuery и т.д.)
            data: Словарь данных для передачи в обработчик.

        Returns:
            Результат вызова следующего обработчика.
        """
        # Если мультиязычность отключена — используем язык по умолчанию
        if not Localization.is_enabled():
            data["l10n"] = self._localization_factory(
                Localization.get_default_language()
            )
            return await handler(event, data)

        # Получаем telegram user из события
        # aiogram автоматически добавляет event_from_user в data
        telegram_user: User | None = data.get("event_from_user")

        if telegram_user is None:
            # Событие без пользователя (редкий случай)
            data["l10n"] = self._localization_factory(
                Localization.get_default_language()
            )
            return await handler(event, data)

        # Пытаемся получить язык пользователя из БД
        language = await self._get_user_language(telegram_user.id)

        # Создаём объект Localization и добавляем в data
        data["l10n"] = self._localization_factory(language)

        # Вызываем следующий обработчик
        return await handler(event, data)

    async def _get_user_language(self, telegram_id: int) -> str:
        """Получить язык пользователя из БД.

        Args:
            telegram_id: ID пользователя в Telegram.

        Returns:
            Код языка пользователя (ru, en и т.д.) или язык по умолчанию.
        """
        try:
            async with self._session_factory() as session:
                repo = UserRepository(session)
                user = await repo.get_by_telegram_id(telegram_id)

                if user is not None and user.language:
                    return user.language

        except SQLAlchemyError:
            # Логируем ошибку БД, но не падаем — возвращаем язык по умолчанию
            logger.exception(
                "Ошибка получения языка пользователя %d из БД",
                telegram_id,
            )

        except OSError:
            # Ошибки сети/файловой системы
            logger.exception(
                "Ошибка подключения к БД при получении языка пользователя %d",
                telegram_id,
            )

        # Если пользователь не найден или произошла ошибка — язык по умолчанию
        return Localization.get_default_language()


def create_language_middleware() -> LanguageMiddleware:
    """Создать LanguageMiddleware с production зависимостями (factory function).

    Это основной способ создания middleware в production коде.
    Использует реальные DatabaseSession и create_localization.

    Returns:
        Настроенный LanguageMiddleware.

    Example:
        middleware = create_language_middleware()
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)
    """
    # Импортируем здесь, чтобы избежать циклических импортов
    from src.db.base import DatabaseSession

    return LanguageMiddleware(
        session_factory=DatabaseSession,
        localization_factory=create_localization,
    )
