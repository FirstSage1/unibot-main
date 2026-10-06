"""Репозиторий для работы с платежами.

Этот модуль содержит класс PaymentRepository для CRUD-операций
с платежами в базе данных.

Основные операции:
- Создание нового платежа
- Получение платежа по ID или provider_payment_id
- Обновление статуса платежа
- Получение истории платежей пользователя
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.payment import Payment, PaymentStatus
from src.utils.logging import get_logger

logger = get_logger(__name__)


class PaymentRepository:
    """Репозиторий для работы с платежами.

    Предоставляет методы для CRUD-операций с таблицей payments.
    Используется сервисами для работы с платежами.

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
        provider: str,
        provider_payment_id: str | None,
        amount: Decimal,
        currency: str,
        tariff_slug: str,
        tokens_amount: int,
        description: str,
        status: PaymentStatus = PaymentStatus.PENDING,
        is_recurring: bool = False,
        payment_method_id: str | None = None,
        metadata_json: str | None = None,
    ) -> Payment:
        """Создать новый платёж.

        Args:
            user_id: ID пользователя в нашей системе.
            provider: Имя провайдера (yookassa, stripe, telegram_stars).
            provider_payment_id: ID платежа на стороне провайдера
                (может быть None для Telegram Stars).
            amount: Сумма платежа.
            currency: Код валюты (RUB, USD, XTR).
            tariff_slug: Slug тарифа из config.yaml.
            tokens_amount: Количество токенов для начисления.
            description: Описание платежа.
            status: Начальный статус платежа.
            is_recurring: Флаг рекуррентного платежа.
            payment_method_id: ID сохранённого метода оплаты (для рекуррентов).
            metadata_json: JSON с дополнительными данными.

        Returns:
            Созданный объект Payment.
        """
        payment = Payment(
            user_id=user_id,
            provider=provider,
            provider_payment_id=provider_payment_id,
            amount=amount,
            currency=currency,
            tariff_slug=tariff_slug,
            tokens_amount=tokens_amount,
            description=description,
            status=status,
            is_recurring=is_recurring,
            payment_method_id=payment_method_id,
            metadata_json=metadata_json,
        )
        self._session.add(payment)
        await self._session.flush()
        await self._session.refresh(payment)

        logger.info(
            "Создан платёж: id=%s, user_id=%s, provider=%s, amount=%s %s",
            payment.id,
            user_id,
            provider,
            amount,
            currency,
        )

        return payment

    async def get_by_id(self, payment_id: int) -> Payment | None:
        """Получить платёж по ID.

        Args:
            payment_id: ID платежа в нашей БД.

        Returns:
            Payment или None если не найден.
        """
        stmt = select(Payment).where(Payment.id == payment_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_provider_id(
        self, provider: str, provider_payment_id: str
    ) -> Payment | None:
        """Получить платёж по ID провайдера.

        Используется для идемпотентной обработки webhook'ов.
        Если платёж с таким provider_payment_id уже существует — не создаём дубликат.

        Args:
            provider: Имя провайдера.
            provider_payment_id: ID платежа на стороне провайдера.

        Returns:
            Payment или None если не найден.
        """
        stmt = select(Payment).where(
            Payment.provider == provider,
            Payment.provider_payment_id == provider_payment_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(
        self,
        payment: Payment,
        status: PaymentStatus,
        *,
        payment_method_id: str | None = None,
        completed_at: datetime | None = None,
    ) -> Payment:
        """Обновить статус платежа.

        Args:
            payment: Объект платежа для обновления.
            status: Новый статус.
            payment_method_id: ID сохранённого метода оплаты (для рекуррентов).
            completed_at: Время завершения платежа.

        Returns:
            Обновлённый объект Payment.
        """
        payment.status = status
        if payment_method_id is not None:
            payment.payment_method_id = payment_method_id
        if completed_at is not None:
            payment.completed_at = completed_at

        await self._session.flush()
        await self._session.refresh(payment)

        logger.info(
            "Обновлён статус платежа: id=%s, status=%s",
            payment.id,
            status,
        )

        return payment

    async def get_user_payments(
        self,
        user_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        status: PaymentStatus | None = None,
    ) -> list[Payment]:
        """Получить историю платежей пользователя.

        Args:
            user_id: ID пользователя.
            limit: Максимальное количество записей.
            offset: Смещение для пагинации.
            status: Фильтр по статусу (опционально).

        Returns:
            Список платежей, отсортированных по дате создания (новые первые).
        """
        stmt = select(Payment).where(Payment.user_id == user_id)

        if status is not None:
            stmt = stmt.where(Payment.status == status)

        stmt = stmt.order_by(Payment.created_at.desc()).limit(limit).offset(offset)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_user_payments(
        self, user_id: int, *, status: PaymentStatus | None = None
    ) -> int:
        """Подсчитать количество платежей пользователя.

        Args:
            user_id: ID пользователя.
            status: Фильтр по статусу (опционально).

        Returns:
            Количество платежей.
        """
        from sqlalchemy import func

        stmt = select(func.count(Payment.id)).where(Payment.user_id == user_id)

        if status is not None:
            stmt = stmt.where(Payment.status == status)

        result = await self._session.execute(stmt)
        return result.scalar() or 0
