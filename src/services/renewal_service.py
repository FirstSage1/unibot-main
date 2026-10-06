"""Сервис автопродления подписок.

Этот модуль реализует логику рекуррентных платежей для автоматического
продления подписок пользователей.

Поддерживаемые провайдеры:
- YooKassa: рекуррентные платежи через payment_method_id
- Stripe: рекуррентные платежи через payment_method_id
- Telegram Stars: управляется Telegram автоматически (не требует нашего участия)

Процесс автопродления:
1. Планировщик вызывает process_subscription_renewal() для каждой истекающей подписки
2. Сервис проверяет наличие сохранённого метода оплаты
3. Создаёт рекуррентный платёж через соответствующий провайдер
4. При успехе — продлевает подписку и начисляет токены
5. При неудаче — помечает подписку как PAST_DUE для retry
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import TariffConfig, YamlConfig
from src.core.exceptions import PaymentError
from src.db.models.payment import Payment, PaymentStatus
from src.db.models.subscription import Subscription
from src.db.repositories.payment_repo import PaymentRepository
from src.db.repositories.subscription_repo import SubscriptionRepository
from src.db.repositories.user_repo import UserRepository
from src.providers.payments.base import BasePaymentProvider, PaymentResult
from src.utils.logging import get_logger

logger = get_logger(__name__)


class RenewalResult(StrEnum):
    """Результат попытки продления подписки."""

    SUCCESS = "success"  # Платёж прошёл, подписка продлена
    PAYMENT_FAILED = "payment_failed"  # Платёж не прошёл (retry позже)
    NO_PAYMENT_METHOD = "no_payment_method"  # Нет сохранённого метода оплаты
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"  # Провайдер не настроен
    TARIFF_NOT_FOUND = "tariff_not_found"  # Тариф не найден в конфиге
    SKIPPED = "skipped"  # Пропущено (Telegram Stars или другие причины)
    ERROR = "error"  # Неожиданная ошибка


@dataclass
class RenewalAttemptResult:
    """Результат попытки продления одной подписки.

    Attributes:
        subscription_id: ID подписки.
        user_id: ID пользователя.
        result: Результат попытки.
        payment_id: ID созданного платежа (при успехе).
        error_message: Сообщение об ошибке (при неудаче).
    """

    subscription_id: int
    user_id: int
    result: RenewalResult
    payment_id: int | None = None
    error_message: str | None = None


class RenewalService:
    """Сервис для автоматического продления подписок.

    Оркестрирует процесс рекуррентных платежей:
    - Проверяет конфигурацию тарифа и провайдера
    - Создаёт платёж через соответствующий провайдер
    - Обновляет статус подписки и начисляет токены
    - Записывает историю платежей

    Attributes:
        _session: Асинхронная сессия SQLAlchemy.
        _providers: Словарь провайдеров {имя: экземпляр}.
        _yaml_config: Конфигурация из config.yaml.
    """

    def __init__(
        self,
        session: AsyncSession,
        providers: dict[str, BasePaymentProvider],
        yaml_config: YamlConfig,
    ) -> None:
        """Инициализация сервиса.

        Args:
            session: Асинхронная сессия SQLAlchemy.
            providers: Словарь настроенных провайдеров.
            yaml_config: Конфигурация из config.yaml.
        """
        self._session = session
        self._providers = providers
        self._yaml_config = yaml_config
        self._payment_repo = PaymentRepository(session)
        self._subscription_repo = SubscriptionRepository(session)
        self._user_repo = UserRepository(session)

    async def process_subscription_renewal(
        self,
        subscription: Subscription,
    ) -> RenewalAttemptResult:
        """Обработать продление одной подписки.

        Основной метод для автопродления. Выполняет полный цикл:
        1. Проверяет возможность продления
        2. Создаёт рекуррентный платёж
        3. Обрабатывает результат
        4. Обновляет подписку

        Args:
            subscription: Подписка для продления.

        Returns:
            RenewalAttemptResult с результатом попытки.
        """
        # Telegram Stars управляется Telegram — пропускаем
        if subscription.provider == "telegram_stars":
            logger.debug(
                "Пропускаем Telegram Stars подписку id=%d (управляется Telegram)",
                subscription.id,
            )
            return RenewalAttemptResult(
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                result=RenewalResult.SKIPPED,
            )

        # Проверяем наличие сохранённого метода оплаты
        if not subscription.payment_method_id:
            logger.warning(
                "Нет payment_method_id для подписки id=%d",
                subscription.id,
            )
            await self._mark_renewal_failed(subscription)
            return RenewalAttemptResult(
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                result=RenewalResult.NO_PAYMENT_METHOD,
                error_message="Нет сохранённого метода оплаты",
            )

        # Проверяем провайдер
        provider = self._providers.get(subscription.provider)
        if provider is None:
            logger.error(
                "Провайдер %s не настроен для подписки id=%d",
                subscription.provider,
                subscription.id,
            )
            await self._mark_renewal_failed(subscription)
            return RenewalAttemptResult(
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                result=RenewalResult.PROVIDER_NOT_CONFIGURED,
                error_message=f"Провайдер {subscription.provider} не настроен",
            )

        # Получаем тариф
        tariff = self._yaml_config.get_tariff(subscription.tariff_slug)
        if tariff is None:
            logger.error(
                "Тариф %s не найден для подписки id=%d",
                subscription.tariff_slug,
                subscription.id,
            )
            await self._mark_renewal_failed(subscription)
            return RenewalAttemptResult(
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                result=RenewalResult.TARIFF_NOT_FOUND,
                error_message=f"Тариф {subscription.tariff_slug} не найден",
            )

        # Получаем пользователя для telegram_id
        user = await self._user_repo.get_by_id(subscription.user_id)
        if user is None:
            logger.error(
                "Пользователь id=%d не найден для подписки id=%d",
                subscription.user_id,
                subscription.id,
            )
            await self._mark_renewal_failed(subscription)
            return RenewalAttemptResult(
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                result=RenewalResult.ERROR,
                error_message="Пользователь не найден",
            )

        # Выполняем рекуррентный платёж
        # payment_method_id проверен на None выше (line 137), это для mypy
        assert subscription.payment_method_id is not None
        try:
            result = await self._charge_recurring_payment(
                subscription=subscription,
                tariff=tariff,
                provider=provider,
                telegram_id=user.telegram_id,
                payment_method_id=subscription.payment_method_id,
            )

            if result.is_success:
                # Платёж прошёл — продлеваем подписку
                payment = await self._create_renewal_payment(
                    subscription=subscription,
                    tariff=tariff,
                    provider_result=result,
                )
                await self._renew_subscription(subscription, tariff, payment.id)

                logger.info(
                    "Подписка id=%d успешно продлена, payment_id=%d",
                    subscription.id,
                    payment.id,
                )

                return RenewalAttemptResult(
                    subscription_id=subscription.id,
                    user_id=subscription.user_id,
                    result=RenewalResult.SUCCESS,
                    payment_id=payment.id,
                )
            else:
                # Платёж не прошёл
                await self._mark_renewal_failed(subscription)
                error_msg = result.error_message or "Платёж отклонён"

                logger.warning(
                    "Платёж для подписки id=%d не прошёл: %s",
                    subscription.id,
                    error_msg,
                )

                return RenewalAttemptResult(
                    subscription_id=subscription.id,
                    user_id=subscription.user_id,
                    result=RenewalResult.PAYMENT_FAILED,
                    error_message=error_msg,
                )

        except PaymentError as e:
            await self._mark_renewal_failed(subscription)
            logger.exception(
                "Ошибка платежа для подписки id=%d",
                subscription.id,
            )
            return RenewalAttemptResult(
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                result=RenewalResult.PAYMENT_FAILED,
                error_message=str(e),
            )
        except Exception as e:
            await self._mark_renewal_failed(subscription)
            logger.exception(
                "Неожиданная ошибка при продлении подписки id=%d",
                subscription.id,
            )
            return RenewalAttemptResult(
                subscription_id=subscription.id,
                user_id=subscription.user_id,
                result=RenewalResult.ERROR,
                error_message=str(e),
            )

    async def _charge_recurring_payment(
        self,
        subscription: Subscription,
        tariff: TariffConfig,
        provider: BasePaymentProvider,
        telegram_id: int,
        payment_method_id: str,
    ) -> PaymentResult:
        """Выполнить рекуррентное списание.

        Args:
            subscription: Подписка для продления.
            tariff: Конфигурация тарифа.
            provider: Провайдер платежей.
            telegram_id: Telegram ID пользователя.
            payment_method_id: ID сохранённого метода оплаты.

        Returns:
            PaymentResult с результатом списания.

        Raises:
            PaymentError: При ошибке списания.
        """
        # Получаем цену и валюту для провайдера
        price = tariff.get_price_for_provider(subscription.provider)
        currency = tariff.get_currency_for_provider(subscription.provider)

        if price is None or currency is None:
            msg = f"Тариф {tariff.slug} недоступен для {subscription.provider}"
            raise PaymentError(msg, provider=subscription.provider)

        description = f"Автопродление подписки «{tariff.name.ru}»"

        logger.info(
            "Создаём рекуррентный платёж: subscription_id=%d, amount=%s %s, "
            "payment_method_id=%s",
            subscription.id,
            price,
            currency,
            payment_method_id,
        )

        return await provider.charge_saved_card(
            payment_method_id=payment_method_id,
            amount=Decimal(str(price)),
            currency=currency,
            user_id=telegram_id,
            tariff_slug=tariff.slug,
            description=description,
        )

    async def _create_renewal_payment(
        self,
        subscription: Subscription,
        tariff: TariffConfig,
        provider_result: PaymentResult,
    ) -> Payment:
        """Создать запись о платеже за продление.

        Args:
            subscription: Подписка.
            tariff: Конфигурация тарифа.
            provider_result: Результат от провайдера.

        Returns:
            Созданный Payment.
        """
        price = tariff.get_price_for_provider(subscription.provider)
        currency = tariff.get_currency_for_provider(subscription.provider)

        metadata = {
            "subscription_id": subscription.id,
            "tariff_slug": tariff.slug,
            "tariff_name": tariff.name.ru,
            "is_recurring_renewal": True,
            "tokens_per_period": tariff.tokens_per_period,
        }

        return await self._payment_repo.create(
            user_id=subscription.user_id,
            provider=subscription.provider,
            provider_payment_id=provider_result.payment_id,
            amount=Decimal(str(price)) if price else Decimal(0),
            currency=currency or "RUB",
            tariff_slug=tariff.slug,
            tokens_amount=tariff.tokens_per_period,
            description=f"Автопродление подписки ({tariff.slug})",
            status=PaymentStatus.SUCCEEDED,
            is_recurring=True,
            payment_method_id=subscription.payment_method_id,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )

    async def _renew_subscription(
        self,
        subscription: Subscription,
        tariff: TariffConfig,
        payment_id: int,
    ) -> None:
        """Продлить подписку после успешного платежа.

        Args:
            subscription: Подписка для продления.
            tariff: Конфигурация тарифа.
            payment_id: ID платежа за продление.
        """
        # Рассчитываем новый период
        now = datetime.now(UTC)
        # Новый период начинается с момента окончания текущего
        # или с текущего момента, если подписка уже просрочена
        new_period_start = max(subscription.period_end, now)
        new_period_end = new_period_start + timedelta(days=tariff.period_days)

        # Определяем, нужно ли переносить токены
        carry_over = not tariff.burn_unused

        await self._subscription_repo.renew(
            subscription,
            period_start=new_period_start,
            period_end=new_period_end,
            last_renewal_payment_id=payment_id,
            carry_over_tokens=carry_over,
        )

        logger.info(
            "Подписка продлена: id=%d, new_period_end=%s, tokens=%d",
            subscription.id,
            new_period_end,
            subscription.tokens_remaining,
        )

    async def _mark_renewal_failed(self, subscription: Subscription) -> None:
        """Пометить попытку продления как неудачную.

        Args:
            subscription: Подписка.
        """
        await self._subscription_repo.record_renewal_attempt(
            subscription,
            success=False,
        )


def create_renewal_service(
    session: AsyncSession,
    providers: dict[str, BasePaymentProvider],
    yaml_config: YamlConfig | None = None,
) -> RenewalService:
    """Фабричная функция для создания RenewalService.

    Args:
        session: Асинхронная сессия SQLAlchemy.
        providers: Словарь настроенных провайдеров.
        yaml_config: Конфигурация из config.yaml (опционально).

    Returns:
        Настроенный экземпляр RenewalService.
    """
    if yaml_config is None:
        from src.config.yaml_config import yaml_config as global_yaml_config

        yaml_config = global_yaml_config

    return RenewalService(
        session=session,
        providers=providers,
        yaml_config=yaml_config,
    )
