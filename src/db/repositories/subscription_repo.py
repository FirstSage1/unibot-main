"""Репозиторий для работы с подписками.

Этот модуль содержит класс SubscriptionRepository для CRUD-операций
с подписками пользователей в базе данных.

Основные операции:
- Создание новой подписки
- Получение активной подписки пользователя
- Обновление статуса и токенов подписки
- Поиск истекающих подписок для автопродления
- Списание токенов из подписки
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.subscription import Subscription, SubscriptionStatus
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SubscriptionRepository:
    """Репозиторий для работы с подписками.

    Предоставляет методы для CRUD-операций с таблицей subscriptions.
    Используется SubscriptionService для управления подписками пользователей.

    Attributes:
        session: Асинхронная сессия SQLAlchemy.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Инициализация репозитория.

        Args:
            session: Асинхронная сессия SQLAlchemy.
        """
        self._session = session

    async def create(  # noqa: PLR0913
        self,
        *,
        user_id: int,
        tariff_slug: str,
        provider: str,
        tokens_per_period: int,
        period_start: datetime,
        period_end: datetime,
        status: SubscriptionStatus = SubscriptionStatus.PENDING,
        payment_method_id: str | None = None,
        original_payment_id: int | None = None,
        metadata_json: str | None = None,
    ) -> Subscription:
        """Создать новую подписку.

        Args:
            user_id: ID пользователя в нашей системе.
            tariff_slug: Slug тарифа из config.yaml (например, "pro_monthly").
            provider: Платёжный провайдер (yookassa, stripe, telegram_stars).
            tokens_per_period: Количество токенов на период.
            period_start: Начало текущего периода.
            period_end: Конец текущего периода.
            status: Начальный статус подписки.
            payment_method_id: ID сохранённого метода оплаты.
            original_payment_id: ID первого платежа.
            metadata_json: JSON с дополнительными данными.

        Returns:
            Созданный объект Subscription.
        """
        subscription = Subscription(
            user_id=user_id,
            tariff_slug=tariff_slug,
            provider=provider,
            status=status,
            tokens_per_period=tokens_per_period,
            tokens_remaining=tokens_per_period,  # Начинаем с полным балансом
            period_start=period_start,
            period_end=period_end,
            payment_method_id=payment_method_id,
            original_payment_id=original_payment_id,
            metadata_json=metadata_json,
        )
        self._session.add(subscription)
        await self._session.flush()
        await self._session.refresh(subscription)

        logger.info(
            "Создана подписка: id=%s, user_id=%s, tariff=%s, tokens=%s",
            subscription.id,
            user_id,
            tariff_slug,
            tokens_per_period,
        )

        return subscription

    async def get_by_id(self, subscription_id: int) -> Subscription | None:
        """Получить подписку по ID.

        Args:
            subscription_id: ID подписки в нашей БД.

        Returns:
            Subscription или None если не найдена.
        """
        stmt = select(Subscription).where(Subscription.id == subscription_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_subscription(self, user_id: int) -> Subscription | None:
        """Получить активную подписку пользователя.

        Активной считается подписка со статусом ACTIVE или PAST_DUE.
        Если у пользователя несколько активных подписок (edge case),
        возвращается подписка с наибольшим period_end.

        Args:
            user_id: ID пользователя.

        Returns:
            Активная Subscription или None если нет.
        """
        stmt = (
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_(
                    [SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE]
                ),
            )
            .order_by(Subscription.period_end.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_subscriptions(
        self,
        user_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        include_expired: bool = False,
    ) -> list[Subscription]:
        """Получить все подписки пользователя.

        Args:
            user_id: ID пользователя.
            limit: Максимальное количество записей.
            offset: Смещение для пагинации.
            include_expired: Включить истёкшие подписки.

        Returns:
            Список подписок, отсортированных по дате создания (новые первые).
        """
        stmt = select(Subscription).where(Subscription.user_id == user_id)

        if not include_expired:
            stmt = stmt.where(Subscription.status != SubscriptionStatus.EXPIRED)

        stmt = stmt.order_by(Subscription.created_at.desc()).limit(limit).offset(offset)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        subscription: Subscription,
        status: SubscriptionStatus,
        *,
        cancel_at_period_end: bool | None = None,
    ) -> Subscription:
        """Обновить статус подписки.

        Args:
            subscription: Объект подписки для обновления.
            status: Новый статус.
            cancel_at_period_end: Если True — не продлевать после окончания периода.

        Returns:
            Обновлённый объект Subscription.
        """
        subscription.status = status
        if cancel_at_period_end is not None:
            subscription.cancel_at_period_end = cancel_at_period_end
            if cancel_at_period_end:
                subscription.auto_renewal = False

        await self._session.flush()
        await self._session.refresh(subscription)

        logger.info(
            "Обновлён статус подписки: id=%s, status=%s, cancel_at_period_end=%s",
            subscription.id,
            status,
            cancel_at_period_end,
        )

        return subscription

    async def deduct_tokens(
        self, subscription: Subscription, amount: int
    ) -> Subscription:
        """Списать токены из подписки.

        Атомарно уменьшает tokens_remaining на указанную сумму.
        Вызывается после успешной генерации.

        Использует атомарный UPDATE с WHERE для предотвращения race condition
        при конкурентных списаниях.

        Args:
            subscription: Объект подписки.
            amount: Количество токенов для списания.

        Returns:
            Обновлённый объект Subscription.

        Raises:
            ValueError: Если недостаточно токенов.
        """
        from sqlalchemy import update

        # Атомарный UPDATE с проверкой баланса в WHERE clause
        stmt = (
            update(Subscription)
            .where(
                Subscription.id == subscription.id,
                Subscription.tokens_remaining >= amount,  # Атомарная проверка
            )
            .values(tokens_remaining=Subscription.tokens_remaining - amount)
            .returning(Subscription)
        )

        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()

        if updated is None:
            # Обновление не выполнено — недостаточно токенов
            raise ValueError(
                f"Недостаточно токенов в подписке: требуется {amount}, "
                f"доступно {subscription.tokens_remaining}"
            )

        # Коммитим изменения в БД
        # Важно: без commit() изменения tokens_remaining не сохранятся!
        await self._session.commit()

        # Обновляем локальный объект
        await self._session.refresh(subscription)

        logger.debug(
            "Списано %s токенов из подписки id=%s, осталось %s",
            amount,
            subscription.id,
            subscription.tokens_remaining,
        )

        return subscription

    async def renew(
        self,
        subscription: Subscription,
        *,
        period_start: datetime,
        period_end: datetime,
        last_renewal_payment_id: int | None = None,
        carry_over_tokens: bool = False,
    ) -> Subscription:
        """Продлить подписку.

        Обновляет период подписки и начисляет новые токены.
        Если carry_over_tokens=True, неиспользованные токены добавляются к новым.

        Args:
            subscription: Объект подписки для продления.
            period_start: Начало нового периода.
            period_end: Конец нового периода.
            last_renewal_payment_id: ID платежа за продление.
            carry_over_tokens: Перенести неиспользованные токены.

        Returns:
            Обновлённый объект Subscription.
        """
        old_tokens = subscription.tokens_remaining

        subscription.period_start = period_start
        subscription.period_end = period_end
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.renewal_attempts = 0  # Сбрасываем счётчик попыток
        subscription.last_renewal_attempt_at = None

        if last_renewal_payment_id is not None:
            subscription.last_renewal_payment_id = last_renewal_payment_id

        # Начисляем новые токены
        if carry_over_tokens:
            subscription.tokens_remaining = subscription.tokens_per_period + old_tokens
        else:
            subscription.tokens_remaining = subscription.tokens_per_period

        await self._session.flush()
        await self._session.refresh(subscription)

        logger.info(
            "Подписка продлена: id=%s, period_end=%s, tokens=%s (перенесено: %s)",
            subscription.id,
            period_end,
            subscription.tokens_remaining,
            old_tokens if carry_over_tokens else 0,
        )

        return subscription

    async def record_renewal_attempt(
        self,
        subscription: Subscription,
        *,
        success: bool = False,
    ) -> Subscription:
        """Записать попытку продления.

        Используется для retry-логики при неудачном автопродлении.
        При успехе renewal_attempts сбрасывается через метод renew().

        Args:
            subscription: Объект подписки.
            success: Была ли попытка успешной.

        Returns:
            Обновлённый объект Subscription.
        """
        subscription.last_renewal_attempt_at = datetime.now(UTC)
        if not success:
            subscription.renewal_attempts += 1
            subscription.status = SubscriptionStatus.PAST_DUE

        await self._session.flush()
        await self._session.refresh(subscription)

        logger.info(
            "Попытка продления подписки: id=%s, success=%s, attempts=%s",
            subscription.id,
            success,
            subscription.renewal_attempts,
        )

        return subscription

    async def get_expiring_subscriptions(
        self,
        before: datetime,
        *,
        limit: int = 100,
    ) -> list[Subscription]:
        """Найти подписки, которые истекают до указанной даты.

        Используется планировщиком для отправки напоминаний и автопродления.
        Возвращает подписки со статусом ACTIVE, которые:
        - Имеют auto_renewal=True
        - НЕ помечены cancel_at_period_end=True
        - period_end < before

        Args:
            before: Дата, до которой должна истечь подписка.
            limit: Максимальное количество записей.

        Returns:
            Список истекающих подписок.
        """
        stmt = (
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                Subscription.auto_renewal.is_(True),
                Subscription.cancel_at_period_end.is_(False),
                Subscription.period_end < before,
            )
            .order_by(Subscription.period_end.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_past_due_subscriptions(
        self,
        *,
        max_attempts: int = 5,
        limit: int = 100,
    ) -> list[Subscription]:
        """Найти подписки с неудачными попытками продления.

        Используется планировщиком для retry-логики.
        Возвращает подписки со статусом PAST_DUE, у которых
        количество попыток меньше max_attempts.

        Args:
            max_attempts: Максимальное количество попыток.
            limit: Максимальное количество записей.

        Returns:
            Список подписок для повторной попытки продления.
        """
        stmt = (
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.PAST_DUE,
                Subscription.renewal_attempts < max_attempts,
            )
            .order_by(Subscription.last_renewal_attempt_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def expire_subscription(
        self,
        subscription: Subscription,
    ) -> Subscription:
        """Пометить подписку как истёкшую.

        Вызывается когда:
        - Достигнут лимит попыток продления
        - Пользователь отменил подписку и период закончился

        Args:
            subscription: Объект подписки.

        Returns:
            Обновлённый объект Subscription.
        """
        subscription.status = SubscriptionStatus.EXPIRED
        subscription.auto_renewal = False

        await self._session.flush()
        await self._session.refresh(subscription)

        logger.info(
            "Подписка истекла: id=%s, user_id=%s, remaining_tokens=%s",
            subscription.id,
            subscription.user_id,
            subscription.tokens_remaining,
        )

        return subscription

    async def count_active_subscriptions(self, tariff_slug: str | None = None) -> int:
        """Подсчитать количество активных подписок.

        Используется для аналитики и мониторинга.

        Args:
            tariff_slug: Фильтр по тарифу (опционально).

        Returns:
            Количество активных подписок.
        """
        stmt = select(func.count(Subscription.id)).where(
            Subscription.status.in_(
                [SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE]
            )
        )

        if tariff_slug is not None:
            stmt = stmt.where(Subscription.tariff_slug == tariff_slug)

        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def get_by_payment_method_id(
        self, provider: str, payment_method_id: str
    ) -> Subscription | None:
        """Найти подписку по ID метода оплаты.

        Используется для обработки webhook'ов Telegram Stars,
        где subscription_id приходит в payment_method_id.

        Args:
            provider: Платёжный провайдер.
            payment_method_id: ID метода оплаты / подписки.

        Returns:
            Subscription или None если не найдена.
        """
        stmt = select(Subscription).where(
            Subscription.provider == provider,
            Subscription.payment_method_id == payment_method_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
