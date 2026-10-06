"""Репозиторий для работы с рефералами.

Содержит все операции с таблицей referrals:
- Создание реферальной связи
- Поиск по inviter_id / invitee_id
- Подсчёт рефералов и заработка
- Обновление статуса выплаты бонуса
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.referral import Referral
from src.db.models.user import User


class ReferralRepository:
    """Репозиторий для работы с рефералами.

    Использует Dependency Injection — сессия передаётся в конструктор.
    Это позволяет легко тестировать код без реальной БД.

    Пример использования:
        async with DatabaseSession() as session:
            repo = ReferralRepository(session)
            referral = await repo.get_by_invitee_id(user.id)
    """

    def __init__(self, session: AsyncSession) -> None:
        """Инициализировать репозиторий.

        Args:
            session: Асинхронная сессия SQLAlchemy.
        """
        self._session = session

    async def create(
        self,
        inviter: User,
        invitee: User,
        inviter_bonus_amount: int = 0,
        invitee_bonus_amount: int = 0,
        bonus_paid: bool = False,
    ) -> Referral:
        """Создать реферальную связь.

        Args:
            inviter: Пригласивший пользователь.
            invitee: Приглашённый пользователь.
            inviter_bonus_amount: Бонус пригласившему (0 если отложен).
            invitee_bonus_amount: Бонус приглашённому.
            bonus_paid: Выплачен ли бонус пригласившему сейчас.

        Returns:
            Созданный объект Referral.

        Raises:
            IntegrityError: Если invitee_id уже существует (повторное приглашение).
        """
        referral = Referral(
            inviter_id=inviter.id,
            invitee_id=invitee.id,
            inviter_bonus_amount=inviter_bonus_amount,
            invitee_bonus_amount=invitee_bonus_amount,
            bonus_paid_at=datetime.now(UTC) if bonus_paid else None,
        )
        self._session.add(referral)
        await self._session.commit()
        await self._session.refresh(referral)
        return referral

    async def get_by_invitee_id(self, invitee_id: int) -> Referral | None:
        """Найти реферала по ID приглашённого.

        Используется для проверки, был ли пользователь уже приглашён.

        Args:
            invitee_id: Внутренний ID приглашённого пользователя.

        Returns:
            Referral если найден, None если пользователь не был приглашён.
        """
        stmt = select(Referral).where(Referral.invitee_id == invitee_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_referrals_by_inviter(
        self,
        inviter_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Referral]:
        """Получить список рефералов пользователя.

        Args:
            inviter_id: Внутренний ID пригласившего пользователя.
            limit: Максимальное количество записей.
            offset: Смещение для пагинации.

        Returns:
            Список рефералов (новые первыми).
        """
        stmt = (
            select(Referral)
            .where(Referral.inviter_id == inviter_id)
            .order_by(Referral.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_referrals(self, inviter_id: int) -> int:
        """Подсчитать количество рефералов пользователя.

        Считает всех приглашённых, независимо от статуса выплаты.

        Args:
            inviter_id: Внутренний ID пригласившего пользователя.

        Returns:
            Количество рефералов.
        """
        stmt = select(func.count(Referral.id)).where(Referral.inviter_id == inviter_id)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def get_total_earnings(self, inviter_id: int) -> int:
        """Подсчитать общий заработок на рефералах.

        Суммирует inviter_bonus_amount для всех выплаченных бонусов.

        Args:
            inviter_id: Внутренний ID пригласившего пользователя.

        Returns:
            Общая сумма заработка на рефералах.
        """
        stmt = (
            select(func.coalesce(func.sum(Referral.inviter_bonus_amount), 0))
            .where(Referral.inviter_id == inviter_id)
            .where(Referral.bonus_paid_at.isnot(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def count_pending_bonuses(self, inviter_id: int) -> int:
        """Подсчитать количество невыплаченных бонусов.

        Используется когда require_payment=True — показывает,
        сколько рефералов ещё не сделали первую оплату.

        Args:
            inviter_id: Внутренний ID пригласившего пользователя.

        Returns:
            Количество рефералов с невыплаченным бонусом.
        """
        stmt = (
            select(func.count(Referral.id))
            .where(Referral.inviter_id == inviter_id)
            .where(Referral.bonus_paid_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def mark_bonus_paid(
        self,
        referral: Referral,
        bonus_amount: int,
    ) -> Referral:
        """Пометить бонус как выплаченный.

        Используется когда require_payment=True и приглашённый
        сделал первую оплату — теперь можно выплатить бонус.

        Args:
            referral: Объект реферала для обновления.
            bonus_amount: Сумма бонуса пригласившему.

        Returns:
            Обновлённый объект Referral.
        """
        referral.inviter_bonus_amount = bonus_amount
        referral.bonus_paid_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(referral)
        return referral

    async def get_unpaid_referral_by_invitee(
        self,
        invitee_id: int,
    ) -> Referral | None:
        """Найти невыплаченный реферал по ID приглашённого.

        Используется когда require_payment=True — нужно найти реферал
        для выплаты бонуса после первой оплаты приглашённого.

        Args:
            invitee_id: Внутренний ID приглашённого пользователя.

        Returns:
            Referral с bonus_paid_at=None или None если не найден.
        """
        stmt = (
            select(Referral)
            .where(Referral.invitee_id == invitee_id)
            .where(Referral.bonus_paid_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
