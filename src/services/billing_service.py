"""Сервис биллинга (управление токенами).

Этот модуль реализует логику монетизации бота:
- Проверка возможности генерации (достаточно ли токенов)
- Списание токенов за генерации
- Начисление бонусов при регистрации
- Получение информации о балансе

Основной паттерн использования:
1. Перед генерацией — check_and_reserve() для проверки возможности
2. После успешной доставки результата — charge_generation() для списания
3. При регистрации — grant_registration_bonus() для начисления бонуса

Почему списание ПОСЛЕ доставки:
- Пользователь платит только за реально полученные генерации
- Если Telegram не доставил сообщение — токены не списываются
- Защита от потери денег при технических сбоях

Пример использования в handler:
    async def handle_generation(message, user, ...):
        async with session_factory() as session:
            billing = create_billing_service(session)

            # 1. Проверяем возможность генерации
            cost = await billing.check_and_reserve(user, "gpt-4o")
            if not cost.can_proceed:
                await message.answer("Недостаточно токенов!")
                return

            # 2. Выполняем генерацию
            result = await ai_service.generate(...)

            # 3. Отправляем пользователю
            await message.answer(result.content)

            # 4. Списываем токены ПОСЛЕ успешной доставки
            await billing.charge_generation(user, model_key, cost, "chat")
"""

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import BillingConfig, YamlConfig
from src.db.models.transaction import TransactionType
from src.db.models.user import User
from src.db.repositories.subscription_repo import SubscriptionRepository
from src.db.repositories.transaction_repo import TransactionRepository
from src.utils.logging import get_logger

logger = get_logger(__name__)

# =============================================================================
# КОНСТАНТЫ СТРАТЕГИЙ ЦЕНООБРАЗОВАНИЯ
# =============================================================================
# Эти константы определяют тип расчёта стоимости генерации.
# Используются как значение по умолчанию для параметра `quantity`.
#
# Формула: итоговая_стоимость = price_tokens × quantity
#
# PerGenerationPricing (quantity=1.0):
#   Фиксированная цена за один запрос генерации.
#   Используется для: chat, image, image_edit.
#   Пример: модель с price_tokens=10 → стоимость = 10 × 1.0 = 10 токенов.
#
# PerMinutePricing (quantity=минуты):
#   Цена рассчитывается за каждую минуту контента.
#   Используется для: stt (транскрипция), tts (озвучка).
#   Пример: модель с price_tokens=5, аудио 3 минуты → стоимость = 5 × 3.0 = 15 токенов.

PER_GENERATION_PRICING: float = 1.0
"""Множитель для фиксированной цены за генерацию (quantity=1.0)."""

PER_MINUTE_PRICING_UNIT: float = 1.0
"""Базовая единица для PerMinutePricing (1 минута).

Умножается на длительность в минутах.
"""


@dataclass
class GenerationCost:
    """Результат проверки возможности генерации.

    Возвращается методом check_and_reserve() и содержит информацию
    о стоимости генерации и возможности её выполнения.

    Стратегии ценообразования:
    - PerGenerationPricing (quantity=1): фиксированная цена за генерацию
      Используется для chat, image, image_edit
    - PerMinutePricing (quantity=минуты): цена за минуту
      Используется для stt, tts (в будущем)

    Итоговая стоимость = price_tokens × quantity

    Attributes:
        can_proceed: Можно ли выполнить генерацию.
            True если биллинг отключён или достаточно токенов.
        tokens_cost: Итоговая стоимость генерации в токенах (с учётом quantity).
            0 если биллинг отключён.
        model_key: Ключ модели для генерации.
        quantity: Множитель для расчёта стоимости.
            1.0 = PerGenerationPricing (за запрос)
            N = PerMinutePricing (за N минут)
    """

    can_proceed: bool
    tokens_cost: int
    model_key: str
    quantity: float = 1.0


@dataclass
class ChargeResult:
    """Результат списания токенов за генерацию.

    Возвращается методом charge_generation() и содержит информацию
    о фактически списанных токенах и созданной транзакции.

    Attributes:
        tokens_charged: Общее количество списанных токенов.
            Включает токены из подписки и основного баланса.
        from_subscription: Сколько токенов списано из подписки.
        from_balance: Сколько токенов списано из основного баланса.
        transaction_id: ID транзакции списания из баланса (None если
            списание только из подписки или стоимость 0).
    """

    tokens_charged: int
    from_subscription: int = 0
    from_balance: int = 0
    transaction_id: int | None = None


@dataclass
class BalanceInfo:
    """Информация о балансе пользователя.

    Используется для отображения в команде /balance.

    Приоритет списания токенов:
    1. Сначала списываются токены подписки (subscription_tokens)
    2. Потом основной баланс (balance)

    Attributes:
        balance: Текущий баланс токенов (основной, без подписки).
        billing_enabled: Включена ли система биллинга.
        subscription_tokens: Токены из активной подписки (0 если нет подписки).
        total_tokens: Общее количество токенов (balance + subscription_tokens).
        has_subscription: Есть ли активная подписка.
        subscription_name: Название подписки (если есть).
        subscription_period_end: Дата окончания периода подписки.
        subscription_auto_renewal: Включено ли автопродление.
    """

    balance: int
    billing_enabled: bool
    subscription_tokens: int = 0
    total_tokens: int = 0
    has_subscription: bool = False
    subscription_name: str | None = None
    subscription_period_end: datetime | None = None
    subscription_auto_renewal: bool = False


class BillingService:
    """Сервис для управления биллингом (токенами).

    Использует Dependency Injection — сессия и конфиг передаются в конструктор.
    Это позволяет легко тестировать код без реальной БД и конфигурации.

    Приоритет списания токенов:
    1. Сначала списываются токены подписки (Subscription.tokens_remaining)
    2. Потом основной баланс (User.balance)

    Основные методы:
    - check_and_reserve(): Проверить возможность генерации
    - charge_generation(): Списать токены после успешной генерации
    - grant_registration_bonus(): Начислить бонус при регистрации
    - get_balance_info(): Получить информацию о балансе

    Attributes:
        _session: Асинхронная сессия SQLAlchemy.
        _config: Конфигурация биллинга из yaml_config.
        _yaml_config: Полная YAML-конфигурация.
        _transaction_repo: Репозиторий для работы с транзакциями.
        _subscription_repo: Репозиторий для работы с подписками.
    """

    def __init__(
        self,
        session: AsyncSession,
        config: BillingConfig,
        yaml_config: YamlConfig,
    ) -> None:
        """Инициализировать сервис биллинга.

        Args:
            session: Асинхронная сессия SQLAlchemy.
            config: Конфигурация биллинга (billing секция из yaml_config).
            yaml_config: Полная YAML-конфигурация (для получения цен моделей).
        """
        self._session = session
        self._config = config
        self._yaml_config = yaml_config
        self._transaction_repo = TransactionRepository(session)
        self._subscription_repo = SubscriptionRepository(session)

    async def check_and_reserve(
        self,
        user: User,
        model_key: str,
        quantity: float = PER_GENERATION_PRICING,
    ) -> GenerationCost:
        """Проверить возможность генерации.

        Стратегии ценообразования:
        - PerGenerationPricing: quantity=1.0 (по умолчанию)
          Итоговая стоимость = price_tokens
        - PerMinutePricing: quantity=минуты_аудио
          Итоговая стоимость = price_tokens × quantity

        Логика проверки:
        1. Если биллинг отключён (enabled=False) → можно генерировать, стоимость 0
        2. Рассчитываем итоговую стоимость = price_tokens × quantity
        3. Суммируем доступные токены: подписка + основной баланс
        4. Если total_available >= стоимость → можно генерировать
        5. Иначе → can_proceed=False

        Args:
            user: Пользователь, запрашивающий генерацию.
            model_key: Ключ модели из config.yaml.
            quantity: Множитель стоимости. По умолчанию 1.0 (PerGenerationPricing).
                Для STT/TTS можно передать количество минут (PerMinutePricing).

        Returns:
            GenerationCost с информацией о стоимости и возможности генерации.

        Example:
            # PerGenerationPricing (chat, image)
            cost = await billing.check_and_reserve(user, "gpt-4o")

            # PerMinutePricing (stt — 5 минут аудио)
            cost = await billing.check_and_reserve(user, "whisper", quantity=5.0)
        """
        # Получаем базовую стоимость модели
        model_config = self._yaml_config.get_model(model_key)
        if model_config is None:
            # Модель не найдена — используем стоимость 0 (для обратной совместимости)
            logger.warning(
                "Модель %s не найдена в конфиге, используем price_tokens=0",
                model_key,
            )
            base_price = 0
        else:
            base_price = model_config.price_tokens

        # Рассчитываем итоговую стоимость с учётом quantity
        total_cost = int(base_price * quantity)

        # Если биллинг отключён — всё бесплатно
        if not self._config.enabled:
            return GenerationCost(
                can_proceed=True,
                tokens_cost=0,
                model_key=model_key,
                quantity=quantity,
            )

        # Получаем активную подписку для учёта токенов
        subscription = await self._subscription_repo.get_active_subscription(user.id)
        subscription_tokens = subscription.tokens_remaining if subscription else 0

        # Суммируем все доступные токены: подписка + баланс
        total_available = subscription_tokens + user.balance

        # Проверяем общий баланс
        if total_available >= total_cost:
            return GenerationCost(
                can_proceed=True,
                tokens_cost=total_cost,
                model_key=model_key,
                quantity=quantity,
            )

        # Недостаточно средств
        return GenerationCost(
            can_proceed=False,
            tokens_cost=total_cost,
            model_key=model_key,
            quantity=quantity,
        )

    async def charge_generation(
        self,
        user: User,
        model_key: str,
        cost: GenerationCost,
        generation_type: str,
    ) -> ChargeResult:
        """Списать токены за успешную генерацию.

        Вызывается ПОСЛЕ успешной доставки результата пользователю.

        Приоритет списания:
        1. Сначала списываются токены подписки (subscription.tokens_remaining)
        2. Если подписка закончилась или недостаточно — из основного баланса

        Args:
            user: Пользователь, которому списываем токены.
            model_key: Ключ модели (для metadata).
            cost: Результат check_and_reserve() — содержит информацию о стоимости.
            generation_type: Тип генерации (chat, image, image_edit, tts, stt).

        Returns:
            ChargeResult с информацией о списанных токенах и транзакции.

        Example:
            # После успешной отправки результата пользователю
            result = await billing.charge_generation(user, "gpt-4o", cost, "chat")
            # result.tokens_charged — сколько списано всего
            # result.transaction_id — ID транзакции (если списано из баланса)
        """
        # Если биллинг отключён — ничего не делаем
        if not self._config.enabled:
            logger.debug(
                "Биллинг отключён, пропускаем списание: user_id=%d, model_key=%s",
                user.id,
                model_key,
            )
            return ChargeResult(tokens_charged=0)

        # Если стоимость 0 — ничего не списываем (модель бесплатная)
        if cost.tokens_cost <= 0:
            logger.debug(
                "Стоимость 0, пропускаем списание: user_id=%d, model_key=%s",
                user.id,
                model_key,
            )
            return ChargeResult(tokens_charged=0)

        # Формируем описание для истории
        model_config = self._yaml_config.get_model(model_key)
        model_name = model_config.display_name if model_config else model_key

        # Формируем базовую metadata
        base_metadata = {
            "model_key": model_key,
            "generation_type": generation_type,
        }

        remaining_cost = cost.tokens_cost
        description = f"Генерация: {model_name}"
        charged_from_subscription = 0
        transaction_id: int | None = None

        # Получаем активную подписку
        subscription = await self._subscription_repo.get_active_subscription(user.id)

        # Если есть подписка с токенами — списываем сначала из неё
        if subscription and subscription.tokens_remaining > 0:
            # Сколько можем списать из подписки
            from_subscription = min(subscription.tokens_remaining, remaining_cost)

            if from_subscription > 0:
                # Списываем из подписки (без транзакции, т.к. это не баланс User)
                await self._subscription_repo.deduct_tokens(
                    subscription, from_subscription
                )
                remaining_cost -= from_subscription
                charged_from_subscription = from_subscription

                logger.debug(
                    "Списано из подписки: user_id=%d, subscription_id=%d, "
                    "tokens=%d, remaining=%d",
                    user.id,
                    subscription.id,
                    from_subscription,
                    subscription.tokens_remaining,
                )

        # Если осталось списать из основного баланса
        if remaining_cost > 0:
            metadata = json.dumps(base_metadata)
            transaction = await self._transaction_repo.create(
                user=user,
                type_=TransactionType.GENERATION,
                amount=-remaining_cost,
                description=description,
                metadata_json=metadata,
            )
            transaction_id = transaction.id

        logger.info(
            "Списание за генерацию: user_id=%d, model=%s, tokens=%d, "
            "from_subscription=%d, from_balance=%d",
            user.id,
            model_key,
            cost.tokens_cost,
            charged_from_subscription,
            remaining_cost,
        )

        return ChargeResult(
            tokens_charged=cost.tokens_cost,
            from_subscription=charged_from_subscription,
            from_balance=remaining_cost,
            transaction_id=transaction_id,
        )

    async def grant_registration_bonus(self, user: User) -> int:
        """Начислить бонус при регистрации.

        Вызывается один раз при создании нового пользователя (первый /start).
        Создаёт транзакцию REGISTRATION_BONUS и обновляет баланс.

        Защита от race condition: проверяет флаг registration_bonus_granted
        и начисляет бонус только один раз, даже при повторных вызовах.

        Args:
            user: Новый пользователь.

        Returns:
            Количество начисленных токенов (0 если биллинг отключён или бонус = 0).

        Example:
            # В handler /start после создания пользователя
            if created:
                bonus = await billing.grant_registration_bonus(user)
                if bonus > 0:
                    await message.answer(f"Вам начислено {bonus} токенов!")
        """
        # Если биллинг отключён — бонус не начисляем
        if not self._config.enabled:
            logger.debug(
                "Биллинг отключён, бонус не начисляется для user_id=%d",
                user.id,
            )
            return 0

        # Проверяем флаг: был ли уже начислен бонус (защита от race condition)
        if user.registration_bonus_granted:
            logger.debug(
                "Бонус уже был начислен ранее для user_id=%d",
                user.id,
            )
            return 0

        bonus = self._config.registration_bonus
        if bonus <= 0:
            logger.debug(
                "Бонус при регистрации отключён (registration_bonus=%d)",
                bonus,
            )
            return 0

        # Устанавливаем флаг ПЕРЕД созданием транзакции (атомарность)
        user.registration_bonus_granted = True
        await self._session.flush()

        await self._transaction_repo.create(
            user=user,
            type_=TransactionType.REGISTRATION_BONUS,
            amount=bonus,
            description="Бонус за регистрацию",
        )

        logger.info(
            "Начислен бонус при регистрации: user_id=%d, amount=%d, new_balance=%d",
            user.id,
            bonus,
            user.balance,
        )

        return bonus

    async def get_balance_info(self, user: User) -> BalanceInfo:
        """Получить информацию о балансе пользователя.

        Используется для команды /balance.
        Включает информацию о подписке если она есть.

        Args:
            user: Пользователь.

        Returns:
            BalanceInfo с текущим балансом и информацией о подписке.

        Example:
            info = await billing.get_balance_info(user)
            if info.has_subscription:
                await message.answer(
                    f"Подписка: {info.subscription_name}\n"
                    f"Токены: {info.subscription_tokens} + {info.balance}"
                )
            else:
                await message.answer(f"Баланс: {info.balance} токенов")
        """
        # Получаем активную подписку
        subscription = await self._subscription_repo.get_active_subscription(user.id)

        # Если есть подписка — формируем полную информацию
        if subscription:
            # Получаем название тарифа из конфига
            tariff = self._yaml_config.tariffs.get_tariff(subscription.tariff_slug)
            subscription_name = tariff.name.ru if tariff else subscription.tariff_slug

            return BalanceInfo(
                balance=user.balance,
                billing_enabled=self._config.enabled,
                subscription_tokens=subscription.tokens_remaining,
                total_tokens=subscription.tokens_remaining + user.balance,
                has_subscription=True,
                subscription_name=subscription_name,
                subscription_period_end=subscription.period_end,
                subscription_auto_renewal=subscription.auto_renewal,
            )

        # Нет подписки — только основной баланс
        return BalanceInfo(
            balance=user.balance,
            billing_enabled=self._config.enabled,
            total_tokens=user.balance,
        )


def create_billing_service(
    session: AsyncSession,
    yaml_config: YamlConfig | None = None,
) -> BillingService:
    """Создать экземпляр BillingService (factory function).

    Это основной способ создания BillingService в production коде.
    Использует глобальный yaml_config если не передан явно.

    Args:
        session: Асинхронная сессия SQLAlchemy.
        yaml_config: YAML-конфигурация (опционально, берётся из глобальной).

    Returns:
        Настроенный экземпляр BillingService.

    Example:
        async with DatabaseSession() as session:
            billing = create_billing_service(session)
            cost = await billing.check_and_reserve(user, "gpt-4o")
    """
    if yaml_config is None:
        from src.config.yaml_config import yaml_config as global_yaml_config

        yaml_config = global_yaml_config

    return BillingService(
        session=session,
        config=yaml_config.billing,
        yaml_config=yaml_config,
    )
