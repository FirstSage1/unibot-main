"""Сервис реферальной программы.

Этот модуль реализует бизнес-логику реферальной системы:
- Обработка реферальных ссылок (ref_USERID)
- Начисление бонусов пригласившему и приглашённому
- Проверка лимитов заработка (max_earnings)
- Получение статистики для /invite

Основной паттерн использования:
1. При регистрации (в /start) — process_referral() для обработки ref_USERID
2. При команде /invite — get_referral_stats() для статистики и get_invite_link()

Пример использования в handler:
    async def cmd_start(message, ...):
        async with DatabaseSession() as session:
            referral_service = create_referral_service(session)

            # Обрабатываем реферальную ссылку
            result = await referral_service.process_referral(
                invitee=user,
                start_param="ref_123456",
            )
            if result.success:
                # Показываем бонус приглашённому
                await message.answer(f"Бонус: {result.invitee_bonus}")
"""

import re
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import ReferralConfig, YamlConfig
from src.db.models.transaction import TransactionType
from src.db.models.user import User
from src.db.repositories.referral_repo import ReferralRepository
from src.db.repositories.transaction_repo import TransactionRepository
from src.db.repositories.user_repo import UserRepository
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Регулярное выражение для извлечения user_id из реферального параметра
# Формат: ref_123456 где 123456 — telegram_id пригласившего
REFERRAL_PARAM_PATTERN = re.compile(r"^ref_(\d+)$")


@dataclass
class ReferralResult:
    """Результат обработки реферальной ссылки.

    Возвращается методом process_referral() и содержит информацию
    о результате обработки реферальной ссылки.

    Attributes:
        success: Успешно ли обработана ссылка.
        invitee_bonus: Бонус, начисленный приглашённому (0 если не начислен).
        inviter_bonus: Бонус, начисленный пригласившему (0 если отложен/не начислен).
        error: Причина неудачи (если success=False).
        bonus_pending: True если бонус пригласившему отложен (require_payment=True).
    """

    success: bool
    invitee_bonus: int = 0
    inviter_bonus: int = 0
    error: str | None = None
    bonus_pending: bool = False


@dataclass
class ReferralStats:
    """Статистика реферальной программы пользователя.

    Используется для отображения в команде /invite.

    Attributes:
        total_referrals: Всего приглашённых пользователей.
        total_earnings: Заработано токенов (уже выплаченные бонусы).
        pending_bonuses: Количество невыплаченных бонусов (при require_payment=True).
        max_earnings: Максимум токенов через рефералку (из конфига).
        inviter_bonus: Бонус за одного реферала (из конфига).
        can_earn_more: Можно ли ещё зарабатывать (не достигнут max_earnings).
    """

    total_referrals: int
    total_earnings: int
    pending_bonuses: int
    max_earnings: int
    inviter_bonus: int
    can_earn_more: bool


class ReferralService:
    """Сервис для управления реферальной программой.

    Использует Dependency Injection — сессия и конфиг передаются в конструктор.
    Это позволяет легко тестировать код без реальной БД и конфигурации.

    Основные методы:
    - process_referral(): Обработать реферальную ссылку при регистрации
    - get_referral_stats(): Получить статистику для /invite
    - get_invite_link(): Сгенерировать реферальную ссылку
    - pay_pending_bonus(): Выплатить отложенный бонус (при первой оплате)

    Attributes:
        _session: Асинхронная сессия SQLAlchemy.
        _config: Конфигурация реферальной программы.
        _referral_repo: Репозиторий для работы с рефералами.
        _user_repo: Репозиторий для работы с пользователями.
        _transaction_repo: Репозиторий для работы с транзакциями.
    """

    def __init__(
        self,
        session: AsyncSession,
        config: ReferralConfig,
    ) -> None:
        """Инициализировать сервис реферальной программы.

        Args:
            session: Асинхронная сессия SQLAlchemy.
            config: Конфигурация реферальной программы.
        """
        self._session = session
        self._config = config
        self._referral_repo = ReferralRepository(session)
        self._user_repo = UserRepository(session)
        self._transaction_repo = TransactionRepository(session)

    def is_enabled(self) -> bool:
        """Проверить, включена ли реферальная программа.

        Returns:
            True если referral.enabled=True в конфиге.
        """
        return self._config.enabled

    def parse_referral_param(self, start_param: str | None) -> int | None:
        """Извлечь telegram_id пригласившего из start-параметра.

        Формат: ref_123456 → 123456

        Args:
            start_param: Start-параметр из команды /start.

        Returns:
            telegram_id пригласившего или None если формат неверный.
        """
        if not start_param:
            return None

        match = REFERRAL_PARAM_PATTERN.match(start_param)
        if not match:
            return None

        try:
            return int(match.group(1))
        except ValueError:
            return None

    async def process_referral(
        self,
        invitee: User,
        start_param: str | None,
    ) -> ReferralResult:
        """Обработать реферальную ссылку при регистрации.

        Вызывается в /start после создания нового пользователя.
        Проверяет валидность реферальной ссылки и начисляет бонусы.

        Логика:
        1. Проверяем, включена ли реферальная программа
        2. Парсим start_param (формат ref_TELEGRAM_ID)
        3. Находим пригласившего по telegram_id
        4. Проверяем защиту от злоупотреблений:
           - Нельзя пригласить себя
           - Пользователь уже был приглашён
           - Лимит заработка не превышен
        5. Создаём запись Referral
        6. Начисляем бонусы (если require_payment=False)

        Args:
            invitee: Новый пользователь (приглашённый).
            start_param: Start-параметр из команды /start.

        Returns:
            ReferralResult с информацией о результате.
        """
        # Проверяем, включена ли реферальная программа
        if not self._config.enabled:
            return ReferralResult(success=False, error="referral_disabled")

        # Парсим start-параметр
        inviter_telegram_id = self.parse_referral_param(start_param)
        if inviter_telegram_id is None:
            return ReferralResult(success=False, error="invalid_param")

        # Защита: нельзя пригласить себя
        if inviter_telegram_id == invitee.telegram_id:
            logger.warning(
                "Попытка пригласить себя: telegram_id=%d",
                invitee.telegram_id,
            )
            return ReferralResult(success=False, error="self_invite")

        # Находим пригласившего
        inviter = await self._user_repo.get_by_telegram_id(inviter_telegram_id)
        if inviter is None:
            logger.warning(
                "Пригласивший не найден: telegram_id=%d",
                inviter_telegram_id,
            )
            return ReferralResult(success=False, error="inviter_not_found")

        # Защита: пользователь уже был приглашён
        existing_referral = await self._referral_repo.get_by_invitee_id(invitee.id)
        if existing_referral is not None:
            logger.warning(
                "Пользователь уже был приглашён: invitee_id=%d",
                invitee.id,
            )
            return ReferralResult(success=False, error="already_invited")

        # Проверяем лимит заработка пригласившего
        current_earnings = await self._referral_repo.get_total_earnings(inviter.id)
        max_earnings = self._config.max_earnings
        can_earn_more = max_earnings == 0 or current_earnings < max_earnings

        # Определяем бонусы
        invitee_bonus = self._config.invitee_bonus
        inviter_bonus = self._config.inviter_bonus if can_earn_more else 0

        # Если превышен лимит — бонус пригласившему = 0
        if max_earnings > 0 and current_earnings + inviter_bonus > max_earnings:
            inviter_bonus = max(0, max_earnings - current_earnings)

        # Определяем, нужно ли отложить выплату бонуса
        bonus_pending = self._config.require_payment and inviter_bonus > 0

        # Создаём запись реферала
        await self._referral_repo.create(
            inviter=inviter,
            invitee=invitee,
            inviter_bonus_amount=inviter_bonus if not bonus_pending else 0,
            invitee_bonus_amount=invitee_bonus,
            bonus_paid=not bonus_pending and inviter_bonus > 0,
        )

        # Начисляем бонус приглашённому (всегда сразу)
        if invitee_bonus > 0:
            await self._transaction_repo.create(
                user=invitee,
                type_=TransactionType.REFERRAL_BONUS,
                amount=invitee_bonus,
                description="Бонус за приглашение",
            )
            logger.info(
                "Начислен бонус приглашённому: user_id=%d, amount=%d",
                invitee.id,
                invitee_bonus,
            )

        # Начисляем бонус пригласившему (если не отложен)
        if inviter_bonus > 0 and not bonus_pending:
            await self._transaction_repo.create(
                user=inviter,
                type_=TransactionType.REFERRAL_BONUS,
                amount=inviter_bonus,
                description="Бонус за приглашение друга",
            )
            logger.info(
                "Начислен бонус пригласившему: user_id=%d, amount=%d",
                inviter.id,
                inviter_bonus,
            )

        logger.info(
            "Реферал обработан: inviter_id=%d, invitee_id=%d, "
            "inviter_bonus=%d, invitee_bonus=%d, pending=%s",
            inviter.id,
            invitee.id,
            inviter_bonus,
            invitee_bonus,
            bonus_pending,
        )

        return ReferralResult(
            success=True,
            invitee_bonus=invitee_bonus,
            inviter_bonus=inviter_bonus if not bonus_pending else 0,
            bonus_pending=bonus_pending,
        )

    async def get_referral_stats(self, user: User) -> ReferralStats:
        """Получить статистику реферальной программы пользователя.

        Используется для отображения в команде /invite.

        Args:
            user: Пользователь, для которого нужна статистика.

        Returns:
            ReferralStats со всей статистикой.
        """
        total_referrals = await self._referral_repo.count_referrals(user.id)
        total_earnings = await self._referral_repo.get_total_earnings(user.id)
        pending_bonuses = await self._referral_repo.count_pending_bonuses(user.id)

        max_earnings = self._config.max_earnings
        can_earn_more = max_earnings == 0 or total_earnings < max_earnings

        return ReferralStats(
            total_referrals=total_referrals,
            total_earnings=total_earnings,
            pending_bonuses=pending_bonuses,
            max_earnings=max_earnings,
            inviter_bonus=self._config.inviter_bonus,
            can_earn_more=can_earn_more,
        )

    def get_invite_link(self, user: User, bot_username: str) -> str:
        """Сгенерировать реферальную ссылку для пользователя.

        Формат: https://t.me/botname?start=ref_TELEGRAM_ID

        Args:
            user: Пользователь, для которого генерируем ссылку.
            bot_username: Username бота (без @).

        Returns:
            Полная реферальная ссылка.
        """
        return f"https://t.me/{bot_username}?start=ref_{user.telegram_id}"

    async def pay_pending_bonus(
        self,
        invitee: User,
    ) -> tuple[User | None, int]:
        """Выплатить отложенный бонус пригласившему.

        Вызывается когда приглашённый делает первую оплату
        (при require_payment=True).

        Args:
            invitee: Пользователь, который сделал оплату.

        Returns:
            Кортеж (inviter, bonus_amount):
            - inviter: Пригласивший пользователь (или None если нет)
            - bonus_amount: Выплаченный бонус (0 если не выплачен)
        """
        # Находим невыплаченный реферал
        referral = await self._referral_repo.get_unpaid_referral_by_invitee(invitee.id)
        if referral is None:
            return None, 0

        # Находим пригласившего
        inviter = await self._user_repo.get_by_id(referral.inviter_id)
        if inviter is None:
            return None, 0

        # Проверяем лимит заработка
        current_earnings = await self._referral_repo.get_total_earnings(inviter.id)
        max_earnings = self._config.max_earnings

        bonus_amount = self._config.inviter_bonus
        if max_earnings > 0:
            remaining = max_earnings - current_earnings
            if remaining <= 0:
                return None, 0
            bonus_amount = min(bonus_amount, remaining)

        # Выплачиваем бонус
        await self._referral_repo.mark_bonus_paid(referral, bonus_amount)

        await self._transaction_repo.create(
            user=inviter,
            type_=TransactionType.REFERRAL_BONUS,
            amount=bonus_amount,
            description="Бонус за приглашение друга (после оплаты)",
        )

        logger.info(
            "Выплачен отложенный бонус: inviter_id=%d, invitee_id=%d, amount=%d",
            inviter.id,
            invitee.id,
            bonus_amount,
        )

        return inviter, bonus_amount


def create_referral_service(
    session: AsyncSession,
    yaml_config: YamlConfig | None = None,
) -> ReferralService:
    """Создать экземпляр ReferralService (factory function).

    Это основной способ создания ReferralService в production коде.
    Использует глобальный yaml_config если не передан явно.

    Args:
        session: Асинхронная сессия SQLAlchemy.
        yaml_config: YAML-конфигурация (опционально, берётся из глобальной).

    Returns:
        Настроенный экземпляр ReferralService.

    Example:
        async with DatabaseSession() as session:
            service = create_referral_service(session)
            stats = await service.get_referral_stats(user)
    """
    if yaml_config is None:
        from src.config.yaml_config import yaml_config as global_yaml_config

        yaml_config = global_yaml_config

    return ReferralService(
        session=session,
        config=yaml_config.referral,
    )
