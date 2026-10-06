"""Сервис управления подписками.

Этот модуль реализует логику работы с подписками пользователей:
- Создание подписки при оплате
- Проверка активной подписки
- Списание токенов из подписки
- Продление и отмена подписок
- Обработка истечения подписок

Приоритет списания токенов:
1. Сначала списываются токены подписки (Subscription.tokens_remaining)
2. Если токены подписки закончились — списывается основной баланс (User.balance)

Автопродление:
- Telegram Stars: Telegram сам управляет продлением, мы получаем webhook
- YooKassa/Stripe: APScheduler проверяет истекающие подписки и списывает
  со сохранённой карты (payment_method_id)

Пример использования:
    async with session_factory() as session:
        subscription_service = create_subscription_service(session)

        # Проверить активную подписку
        sub = await subscription_service.get_active_subscription(user)
        if sub:
            print(f"Осталось {sub.tokens_remaining} до {sub.period_end}")

        # Создать подписку после оплаты
        subscription = await subscription_service.activate_subscription(
            user=user,
            tariff_slug="pro_monthly",
            provider="telegram_stars",
            payment_id=123,
            payment_method_id="tg_sub_123",
        )
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import TariffConfig, YamlConfig
from src.db.models.subscription import Subscription, SubscriptionStatus
from src.db.models.transaction import TransactionType
from src.db.models.user import User
from src.db.repositories.subscription_repo import SubscriptionRepository
from src.db.repositories.transaction_repo import TransactionRepository
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SubscriptionInfo:
    """Информация о подписке для отображения пользователю.

    Используется в команде /balance для показа информации о подписке.

    Attributes:
        has_subscription: Есть ли активная подписка.
        tariff_name: Название тарифа (локализованное).
        tokens_remaining: Оставшиеся токены в текущем периоде.
        period_end: Дата окончания периода.
        auto_renewal: Включено ли автопродление.
        status: Статус подписки.
    """

    has_subscription: bool
    tariff_name: str | None = None
    tokens_remaining: int = 0
    period_end: datetime | None = None
    auto_renewal: bool = False
    status: SubscriptionStatus | None = None


@dataclass
class TokenSource:
    """Источник токенов для списания.

    Показывает, откуда будут списаны токены для генерации.

    Attributes:
        from_subscription: Количество токенов из подписки.
        from_balance: Количество токенов из основного баланса.
        total: Общее количество доступных токенов.
        subscription_id: ID подписки (если есть).
    """

    from_subscription: int
    from_balance: int
    total: int
    subscription_id: int | None = None


class SubscriptionService:
    """Сервис для управления подписками пользователей.

    Использует Dependency Injection — сессия и конфиг передаются в конструктор.
    Это позволяет легко тестировать код без реальной БД и конфигурации.

    Основные методы:
    - get_active_subscription(): Получить активную подписку пользователя
    - activate_subscription(): Создать и активировать подписку
    - deduct_tokens(): Списать токены из подписки
    - cancel_subscription(): Отменить подписку
    - renew_subscription(): Продлить подписку
    - get_subscription_info(): Получить информацию для отображения

    Attributes:
        _session: Асинхронная сессия SQLAlchemy.
        _yaml_config: Полная YAML-конфигурация.
        _subscription_repo: Репозиторий для работы с подписками.
        _transaction_repo: Репозиторий для работы с транзакциями.
    """

    def __init__(
        self,
        session: AsyncSession,
        yaml_config: YamlConfig,
    ) -> None:
        """Инициализировать сервис подписок.

        Args:
            session: Асинхронная сессия SQLAlchemy.
            yaml_config: Полная YAML-конфигурация (тарифы и billing).
        """
        self._session = session
        self._yaml_config = yaml_config
        self._subscription_repo = SubscriptionRepository(session)
        self._transaction_repo = TransactionRepository(session)

    async def get_active_subscription(self, user: User) -> Subscription | None:
        """Получить активную подписку пользователя.

        Активной считается подписка со статусом ACTIVE или PAST_DUE.
        Если у пользователя несколько активных подписок (edge case),
        возвращается подписка с наибольшим period_end.

        Args:
            user: Пользователь.

        Returns:
            Активная Subscription или None если нет.
        """
        return await self._subscription_repo.get_active_subscription(user.id)

    async def get_subscription_info(
        self,
        user: User,
        language: str = "ru",
    ) -> SubscriptionInfo:
        """Получить информацию о подписке для отображения.

        Используется в команде /balance.

        Args:
            user: Пользователь.
            language: Код языка для локализации названия тарифа.

        Returns:
            SubscriptionInfo с информацией о подписке.
        """
        subscription = await self.get_active_subscription(user)

        if subscription is None:
            return SubscriptionInfo(has_subscription=False)

        # Получаем название тарифа
        tariff = self._yaml_config.get_tariff(subscription.tariff_slug)
        tariff_name = tariff.name.get(language) if tariff else subscription.tariff_slug

        return SubscriptionInfo(
            has_subscription=True,
            tariff_name=tariff_name,
            tokens_remaining=subscription.tokens_remaining,
            period_end=subscription.period_end,
            auto_renewal=subscription.auto_renewal,
            status=SubscriptionStatus(subscription.status),
        )

    async def get_available_tokens(self, user: User) -> TokenSource:
        """Получить информацию о доступных токенах.

        Показывает, сколько токенов доступно из подписки и основного баланса.

        Args:
            user: Пользователь.

        Returns:
            TokenSource с информацией об источниках токенов.
        """
        subscription = await self.get_active_subscription(user)

        if subscription is None:
            return TokenSource(
                from_subscription=0,
                from_balance=user.balance,
                total=user.balance,
                subscription_id=None,
            )

        return TokenSource(
            from_subscription=subscription.tokens_remaining,
            from_balance=user.balance,
            total=subscription.tokens_remaining + user.balance,
            subscription_id=subscription.id,
        )

    async def activate_subscription(  # noqa: PLR0913
        self,
        user: User,
        tariff_slug: str,
        provider: str,
        payment_id: int | None = None,
        payment_method_id: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Subscription:
        """Создать и активировать подписку.

        Вызывается после успешной оплаты подписки.
        Если у пользователя уже есть активная подписка — помечает её как expired
        и переносит остаток токенов в баланс (если burn_unused=false).

        Args:
            user: Пользователь.
            tariff_slug: Slug тарифа из config.yaml.
            provider: Платёжный провайдер (yookassa, stripe, telegram_stars).
            payment_id: ID платежа в нашей системе.
            payment_method_id: ID сохранённого метода оплаты для автопродления.
            period_start: Начало периода (по умолчанию — сейчас).
            period_end: Конец периода (по умолчанию — period_start + period_days).
            metadata: Дополнительные данные для metadata_json.

        Returns:
            Созданная и активированная Subscription.

        Raises:
            ValueError: Если тариф не найден или не является подпиской.
        """
        # Получаем конфигурацию тарифа
        tariff = self._yaml_config.get_tariff(tariff_slug)
        if tariff is None:
            raise ValueError(f"Тариф {tariff_slug} не найден")
        if not tariff.is_subscription:
            raise ValueError(f"Тариф {tariff_slug} не является подпиской")

        # Деактивируем текущую подписку если есть
        current_subscription = await self.get_active_subscription(user)
        if current_subscription is not None:
            await self._expire_subscription_with_transfer(
                current_subscription,
                tariff,
            )

        # Определяем период
        now = datetime.now(UTC)
        if period_start is None:
            period_start = now
        if period_end is None:
            period_end = period_start + timedelta(days=tariff.period_days)

        # Создаём подписку
        metadata_json = json.dumps(metadata) if metadata else None

        subscription = await self._subscription_repo.create(
            user_id=user.id,
            tariff_slug=tariff_slug,
            provider=provider,
            tokens_per_period=tariff.tokens_per_period,
            period_start=period_start,
            period_end=period_end,
            status=SubscriptionStatus.ACTIVE,
            payment_method_id=payment_method_id,
            original_payment_id=payment_id,
            metadata_json=metadata_json,
        )

        logger.info(
            "Активирована подписка: user_id=%d, tariff=%s, tokens=%d, period_end=%s",
            user.id,
            tariff_slug,
            tariff.tokens_per_period,
            period_end,
        )

        return subscription

    async def deduct_tokens(
        self,
        user: User,
        amount: int,
    ) -> TokenSource:
        """Списать токены (сначала из подписки, потом из баланса).

        Приоритет списания:
        1. Токены подписки (Subscription.tokens_remaining)
        2. Основной баланс (User.balance)

        Args:
            user: Пользователь.
            amount: Количество токенов для списания.

        Returns:
            TokenSource с информацией об источниках списания.

        Note:
            Этот метод НЕ создаёт транзакции в леджере.
            Транзакции создаются в billing_service.charge_generation().
            Здесь только обновляется Subscription.tokens_remaining.
        """
        subscription = await self.get_active_subscription(user)

        if subscription is None or subscription.tokens_remaining == 0:
            # Нет подписки или токены закончились — всё из баланса
            return TokenSource(
                from_subscription=0,
                from_balance=amount,
                total=user.balance,
                subscription_id=None,
            )

        # Сначала списываем из подписки
        from_subscription = min(subscription.tokens_remaining, amount)
        from_balance = amount - from_subscription

        if from_subscription > 0:
            await self._subscription_repo.deduct_tokens(subscription, from_subscription)

        return TokenSource(
            from_subscription=from_subscription,
            from_balance=from_balance,
            total=subscription.tokens_remaining + user.balance,
            subscription_id=subscription.id,
        )

    async def cancel_subscription(
        self,
        subscription: Subscription,
        reason: str | None = None,
    ) -> Subscription:
        """Отменить подписку.

        Подписка остаётся активной до конца оплаченного периода,
        но не будет автоматически продлеваться.

        Args:
            subscription: Подписка для отмены.
            reason: Причина отмены (для логирования).

        Returns:
            Обновлённая Subscription.
        """
        # Помечаем, что не нужно продлевать
        subscription = await self._subscription_repo.update_status(
            subscription,
            SubscriptionStatus.CANCELED,
            cancel_at_period_end=True,
        )

        # Сохраняем причину в metadata
        if reason:
            metadata = json.loads(subscription.metadata_json or "{}")
            metadata["cancellation_reason"] = reason
            metadata["canceled_at"] = datetime.now(UTC).isoformat()
            subscription.metadata_json = json.dumps(metadata)
            await self._session.flush()

        logger.info(
            "Подписка отменена: subscription_id=%d, user_id=%d, reason=%s",
            subscription.id,
            subscription.user_id,
            reason,
        )

        return subscription

    async def renew_subscription(
        self,
        subscription: Subscription,
        payment_id: int | None = None,
    ) -> Subscription:
        """Продлить подписку на новый период.

        Вызывается после успешной оплаты продления.
        Начисляет новые токены и обновляет период.

        Args:
            subscription: Подписка для продления.
            payment_id: ID платежа за продление.

        Returns:
            Обновлённая Subscription.
        """
        tariff = self._yaml_config.get_tariff(subscription.tariff_slug)

        # Определяем, нужно ли переносить токены
        carry_over = tariff is not None and not tariff.burn_unused

        # Если нужно перенести токены — создаём транзакцию в баланс
        if carry_over and subscription.tokens_remaining > 0:
            # Получаем пользователя
            from src.db.repositories.user_repo import UserRepository

            user_repo = UserRepository(self._session)
            user = await user_repo.get_by_id(subscription.user_id)

            if user is not None:
                slug = subscription.tariff_slug
                await self._transaction_repo.create(
                    user=user,
                    type_=TransactionType.SUBSCRIPTION_TRANSFER,
                    amount=subscription.tokens_remaining,
                    description=f"Перенос токенов из подписки {slug}",
                    metadata_json=json.dumps({"subscription_id": subscription.id}),
                )

        # Рассчитываем новый период
        now = datetime.now(UTC)
        period_days = tariff.period_days if tariff else 30
        new_period_start = now
        new_period_end = now + timedelta(days=period_days)

        # Продлеваем подписку
        subscription = await self._subscription_repo.renew(
            subscription,
            period_start=new_period_start,
            period_end=new_period_end,
            last_renewal_payment_id=payment_id,
            carry_over_tokens=False,  # Токены уже перенесены в баланс
        )

        logger.info(
            "Подписка продлена: subscription_id=%d, new_period_end=%s, tokens=%d",
            subscription.id,
            new_period_end,
            subscription.tokens_remaining,
        )

        return subscription

    async def expire_subscription(
        self,
        subscription: Subscription,
    ) -> Subscription:
        """Пометить подписку как истёкшую.

        Вызывается когда:
        - Достигнут лимит попыток продления
        - Пользователь отменил подписку и период закончился

        Если burn_unused=false в тарифе — неиспользованные токены
        переносятся в основной баланс.

        Args:
            subscription: Подписка.

        Returns:
            Обновлённая Subscription.
        """
        tariff = self._yaml_config.get_tariff(subscription.tariff_slug)
        return await self._expire_subscription_with_transfer(subscription, tariff)

    async def _expire_subscription_with_transfer(
        self,
        subscription: Subscription,
        tariff: TariffConfig | None,
    ) -> Subscription:
        """Помечает подписку как истёкшую и переносит токены.

        Args:
            subscription: Подписка.
            tariff: Конфигурация тарифа (может быть None если тариф удалён).

        Returns:
            Обновлённая Subscription.
        """
        # Переносим токены если нужно
        should_transfer = (
            tariff is not None
            and not tariff.burn_unused
            and subscription.tokens_remaining > 0
        )
        if should_transfer:
            # Получаем пользователя для создания транзакции
            from src.db.repositories.user_repo import UserRepository

            user_repo = UserRepository(self._session)
            user = await user_repo.get_by_id(subscription.user_id)

            if user is not None:
                tariff_slug = subscription.tariff_slug
                await self._transaction_repo.create(
                    user=user,
                    type_=TransactionType.SUBSCRIPTION_TRANSFER,
                    amount=subscription.tokens_remaining,
                    description=f"Перенос токенов из подписки {tariff_slug}",
                    metadata_json=json.dumps({"subscription_id": subscription.id}),
                )

                logger.info(
                    "Перенесены токены из истёкшей подписки: "
                    "subscription_id=%d, tokens=%d, new_balance=%d",
                    subscription.id,
                    subscription.tokens_remaining,
                    user.balance,
                )

        # Помечаем как истёкшую
        return await self._subscription_repo.expire_subscription(subscription)

    async def get_expiring_subscriptions(
        self,
        days_before: int = 1,
    ) -> list[Subscription]:
        """Получить подписки, которые скоро истекают.

        Используется планировщиком для проверки автопродления.

        Args:
            days_before: За сколько дней до истечения (по умолчанию 1 день).

        Returns:
            Список подписок, которые истекают в указанный период.
        """
        before = datetime.now(UTC) + timedelta(days=days_before)
        return await self._subscription_repo.get_expiring_subscriptions(before)

    async def get_past_due_subscriptions(self) -> list[Subscription]:
        """Получить подписки с неудачными попытками продления.

        Используется планировщиком для retry-логики.
        Максимальное количество попыток = billing.renewal_retry_days.

        Returns:
            Список подписок для повторной попытки продления.
        """
        max_attempts = self._yaml_config.billing.renewal_retry_days
        return await self._subscription_repo.get_past_due_subscriptions(
            max_attempts=max_attempts,
        )

    async def record_renewal_attempt(
        self,
        subscription: Subscription,
        success: bool,
    ) -> Subscription:
        """Записать попытку продления.

        Args:
            subscription: Подписка.
            success: Была ли попытка успешной.

        Returns:
            Обновлённая Subscription.
        """
        return await self._subscription_repo.record_renewal_attempt(
            subscription,
            success=success,
        )


def create_subscription_service(
    session: AsyncSession,
    yaml_config: YamlConfig | None = None,
) -> SubscriptionService:
    """Создать экземпляр SubscriptionService (factory function).

    Это основной способ создания SubscriptionService в production коде.
    Использует глобальный yaml_config если не передан явно.

    Args:
        session: Асинхронная сессия SQLAlchemy.
        yaml_config: YAML-конфигурация (опционально, берётся из глобальной).

    Returns:
        Настроенный экземпляр SubscriptionService.

    Example:
        async with DatabaseSession() as session:
            service = create_subscription_service(session)
            subscription = await service.get_active_subscription(user)
    """
    if yaml_config is None:
        from src.config.yaml_config import yaml_config as global_yaml_config

        yaml_config = global_yaml_config

    return SubscriptionService(
        session=session,
        yaml_config=yaml_config,
    )
