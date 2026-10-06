"""Репозиторий для работы с пользователями.

Содержит все операции с таблицей users:
- Создание пользователя
- Поиск по telegram_id
- Обновление данных
- Сегментация для рассылок
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.transaction import Transaction, TransactionType
from src.db.models.user import User


class UserRepository:
    """Репозиторий для работы с пользователями.

    Использует Dependency Injection — сессия передаётся в конструктор.
    Это позволяет легко тестировать код без реальной БД.

    Пример использования:
        async with get_session() as session:
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(123456789)
    """

    def __init__(self, session: AsyncSession) -> None:
        """Инициализировать репозиторий.

        Args:
            session: Асинхронная сессия SQLAlchemy.
        """
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        """Найти пользователя по Telegram ID.

        Args:
            telegram_id: ID пользователя в Telegram.

        Returns:
            User если найден, None если не существует.
        """
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        """Найти пользователя по внутреннему ID.

        Args:
            user_id: Внутренний ID пользователя в нашей БД.

        Returns:
            User если найден, None если не существует.
        """
        return await self._session.get(User, user_id)

    async def create(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language: str = "ru",
        source: str | None = None,
    ) -> User:
        """Создать нового пользователя.

        Args:
            telegram_id: ID пользователя в Telegram.
            username: @username (без @).
            first_name: Имя из профиля Telegram.
            last_name: Фамилия из профиля Telegram.
            language: Код языка (ru, en).
            source: Start-параметр (откуда пришёл).

        Returns:
            Созданный объект User (уже в БД).
        """
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language=language,
            source=source,
        )
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def get_or_create(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        language: str = "ru",
        source: str | None = None,
    ) -> tuple[User, bool]:
        """Получить пользователя или создать нового.

        Атомарная операция с защитой от race condition.
        Если два запроса пытаются создать одного пользователя одновременно,
        один из них получит IntegrityError и повторит поиск.

        Args:
            telegram_id: ID пользователя в Telegram.
            username: @username (без @).
            first_name: Имя из профиля Telegram.
            last_name: Фамилия из профиля Telegram.
            language: Код языка (ru, en).
            source: Start-параметр (откуда пришёл).

        Returns:
            Кортеж (user, created):
            - user: объект User
            - created: True если создан новый, False если существующий
        """
        # Сначала пробуем найти
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            return user, False

        # Пробуем создать
        try:
            user = await self.create(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language=language,
                source=source,
            )
            return user, True
        except IntegrityError:
            # Другой запрос успел создать пользователя — откатываем и ищем снова
            await self._session.rollback()
            user = await self.get_by_telegram_id(telegram_id)
            if user is None:
                # Не должно произойти, но на всякий случай
                raise RuntimeError(
                    f"Не удалось создать или найти пользователя "
                    f"с telegram_id={telegram_id}"
                ) from None
            return user, False

    async def update_profile(
        self,
        user: User,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> User:
        """Обновить данные профиля пользователя.

        Telegram позволяет менять username и имя — обновляем при каждом /start.

        Args:
            user: Объект пользователя для обновления.
            username: Новый @username.
            first_name: Новое имя.
            last_name: Новая фамилия.

        Returns:
            Обновлённый объект User.
        """
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        await self._session.commit()
        await self._session.refresh(user)
        return user

    async def update_language(self, user: User, language: str) -> User:
        """Обновить язык интерфейса пользователя.

        Используется при выборе языка через команду /language.

        Args:
            user: Объект пользователя для обновления.
            language: Новый код языка (ru, en, zh и т.д.)

        Returns:
            Обновлённый объект User.
        """
        user.language = language
        await self._session.commit()
        await self._session.refresh(user)
        return user

    # ==========================================================================
    # МЕТОДЫ ДЛЯ СЕГМЕНТАЦИИ (РАССЫЛКИ)
    # ==========================================================================

    async def count_by_segment(
        self,
        *,
        language: str | None = None,
        has_payments: bool | None = None,
        source: str | None = None,
        registered_after: datetime | None = None,
        registered_before: datetime | None = None,
        exclude_blocked: bool = True,
        after_user_id: int | None = None,
    ) -> int:
        """Подсчитать количество пользователей по критериям сегментации.

        Используется для:
        - Предпросмотра количества получателей перед рассылкой
        - Отображения total_recipients в рассылке

        Args:
            language: Фильтр по языку (None = все).
            has_payments: True = только платившие, False = только бесплатные.
            source: Фильтр по start-параметру (None = все).
            registered_after: Только зарегистрированные после даты.
            registered_before: Только зарегистрированные до даты.
            exclude_blocked: Исключить заблокированных (по умолчанию True).
            after_user_id: Только пользователи с id > after_user_id (для пагинации).

        Returns:
            Количество пользователей, соответствующих критериям.

        Example:
            # Все русскоязычные пользователи
            count = await repo.count_by_segment(language="ru")

            # Платившие пользователи из конкретного источника
            count = await repo.count_by_segment(
                has_payments=True,
                source="promo_winter",
            )
        """
        stmt = select(func.count(User.id))

        # Применяем фильтры
        stmt = self._apply_segment_filters(
            stmt,
            language=language,
            has_payments=has_payments,
            source=source,
            registered_after=registered_after,
            registered_before=registered_before,
            exclude_blocked=exclude_blocked,
            after_user_id=after_user_id,
        )

        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get_by_segment(
        self,
        *,
        language: str | None = None,
        has_payments: bool | None = None,
        source: str | None = None,
        registered_after: datetime | None = None,
        registered_before: datetime | None = None,
        exclude_blocked: bool = True,
        after_user_id: int | None = None,
        limit: int = 100,
    ) -> list[User]:
        """Получить пользователей по критериям сегментации.

        Возвращает батч пользователей для рассылки.
        Используйте after_user_id для пагинации.

        Args:
            language: Фильтр по языку (None = все).
            has_payments: True = только платившие, False = только бесплатные.
            source: Фильтр по start-параметру (None = все).
            registered_after: Только зарегистрированные после даты.
            registered_before: Только зарегистрированные до даты.
            exclude_blocked: Исключить заблокированных (по умолчанию True).
            after_user_id: Только пользователи с id > after_user_id (для пагинации).
            limit: Максимальное количество пользователей в батче.

        Returns:
            Список пользователей, отсортированных по id.

        Example:
            # Первый батч
            batch1 = await repo.get_by_segment(language="ru", limit=100)

            # Следующий батч (пагинация по id)
            if batch1:
                last_id = batch1[-1].id
                batch2 = await repo.get_by_segment(
                    language="ru",
                    after_user_id=last_id,
                    limit=100,
                )
        """
        stmt = select(User)

        # Применяем фильтры
        stmt = self._apply_segment_filters(
            stmt,
            language=language,
            has_payments=has_payments,
            source=source,
            registered_after=registered_after,
            registered_before=registered_before,
            exclude_blocked=exclude_blocked,
            after_user_id=after_user_id,
        )

        # Сортировка по id для стабильной пагинации
        stmt = stmt.order_by(User.id.asc()).limit(limit)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def iter_by_segment(
        self,
        *,
        language: str | None = None,
        has_payments: bool | None = None,
        source: str | None = None,
        registered_after: datetime | None = None,
        registered_before: datetime | None = None,
        exclude_blocked: bool = True,
        after_user_id: int | None = None,
        batch_size: int = 100,
    ) -> AsyncIterator[User]:
        """Итерировать по пользователям сегмента.

        Асинхронный генератор для обработки большого количества пользователей
        без загрузки всех в память.

        Args:
            language: Фильтр по языку (None = все).
            has_payments: True = только платившие, False = только бесплатные.
            source: Фильтр по start-параметру (None = все).
            registered_after: Только зарегистрированные после даты.
            registered_before: Только зарегистрированные до даты.
            exclude_blocked: Исключить заблокированных (по умолчанию True).
            after_user_id: Начать с пользователей после этого id.
            batch_size: Размер батча для загрузки из БД.

        Yields:
            User — пользователи по одному.

        Example:
            async for user in repo.iter_by_segment(language="ru"):
                await send_message(user.telegram_id, text)
        """
        current_after_id = after_user_id

        while True:
            batch = await self.get_by_segment(
                language=language,
                has_payments=has_payments,
                source=source,
                registered_after=registered_after,
                registered_before=registered_before,
                exclude_blocked=exclude_blocked,
                after_user_id=current_after_id,
                limit=batch_size,
            )

            if not batch:
                break

            for user in batch:
                yield user

            # Следующий батч начинается после последнего пользователя
            current_after_id = batch[-1].id

    def _apply_segment_filters(
        self,
        stmt: Any,
        *,
        language: str | None = None,
        has_payments: bool | None = None,
        source: str | None = None,
        registered_after: datetime | None = None,
        registered_before: datetime | None = None,
        exclude_blocked: bool = True,
        after_user_id: int | None = None,
    ) -> Any:
        """Применить фильтры сегментации к запросу.

        Внутренний метод для построения WHERE-условий.

        Args:
            stmt: Базовый SELECT-запрос.
            language: Фильтр по языку.
            has_payments: Фильтр по факту оплат.
            source: Фильтр по источнику.
            registered_after: Фильтр по дате регистрации (от).
            registered_before: Фильтр по дате регистрации (до).
            exclude_blocked: Исключить заблокированных.
            after_user_id: Только пользователи с id > after_user_id.

        Returns:
            Запрос с применёнными фильтрами.
        """
        # Фильтр по языку
        if language is not None:
            stmt = stmt.where(User.language == language)

        # Фильтр по источнику
        if source is not None:
            stmt = stmt.where(User.source == source)

        # Фильтр по дате регистрации (от)
        if registered_after is not None:
            stmt = stmt.where(User.created_at >= registered_after)

        # Фильтр по дате регистрации (до)
        if registered_before is not None:
            stmt = stmt.where(User.created_at <= registered_before)

        # Исключить заблокированных
        if exclude_blocked:
            stmt = stmt.where(User.is_blocked == False)  # noqa: E712

        # Пагинация по id
        if after_user_id is not None:
            stmt = stmt.where(User.id > after_user_id)

        # Фильтр по факту оплат (подзапрос)
        if has_payments is not None:
            # Подзапрос: есть ли у пользователя транзакции типа PURCHASE
            purchase_exists = (
                select(Transaction.id)
                .where(Transaction.user_id == User.id)
                .where(Transaction.type == TransactionType.PURCHASE.value)
                .exists()
            )

            if has_payments:
                # Только пользователи с оплатами
                stmt = stmt.where(purchase_exists)
            else:
                # Только пользователи без оплат
                stmt = stmt.where(~purchase_exists)

        return stmt

    def needs_terms_acceptance(self, user: User, current_version: str) -> bool:
        """Проверить, нужно ли запросить согласие с документами.

        Согласие требуется если:
        1. Пользователь ещё не принимал документы (terms_accepted_at is None)
        2. Версия документов изменилась (accepted_legal_version != current_version)

        Args:
            user: Объект пользователя.
            current_version: Текущая версия документов из конфига.

        Returns:
            True если нужно показать запрос на согласие.
        """
        if user.terms_accepted_at is None:
            return True
        return user.accepted_legal_version != current_version

    async def accept_terms(self, user: User, version: str) -> User:
        """Сохранить согласие пользователя с юридическими документами.

        Вызывается когда пользователь нажимает кнопку «Принимаю» при первом
        использовании бота или при обновлении версии документов.

        Сохраняет:
        - terms_accepted_at — дату/время согласия
        - accepted_legal_version — версию документов

        Args:
            user: Объект пользователя.
            version: Версия юридических документов (например, "1.0").

        Returns:
            Обновлённый объект User.
        """
        from datetime import UTC, datetime

        user.terms_accepted_at = datetime.now(UTC)
        user.accepted_legal_version = version
        await self._session.commit()
        await self._session.refresh(user)
        return user
