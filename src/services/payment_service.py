"""Сервис платежей.

Этот модуль реализует логику работы с платежами:
- Создание платежей через различные провайдеры
- Обработка webhook'ов от провайдеров
- Начисление токенов после успешной оплаты
- Активация подписок при покупке подписочных тарифов

Основной паттерн использования:
1. Пользователь выбирает тариф и провайдер в меню покупки (из /balance)
2. Создаём платёж через create_payment()
3. Перенаправляем пользователя на страницу оплаты (или отправляем invoice)
4. При получении webhook — вызываем process_webhook()
5. При успешной оплате:
   - Для one_time тарифов — начисляем токены на баланс
   - Для subscription тарифов — создаём/активируем подписку

Поддерживаемые провайдеры:
- YooKassa — для платежей в рублях (РФ)
- Stripe — для международных платежей (USD)
- Telegram Stars — встроенная валюта Telegram
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config.yaml_config import TariffConfig, YamlConfig
from src.core.exceptions import (
    ProviderNotConfiguredError,
    TariffNotAvailableForProviderError,
    TariffNotFoundError,
)
from src.db.models.payment import Payment, PaymentStatus
from src.db.models.subscription import SubscriptionStatus
from src.db.models.transaction import TransactionType
from src.db.models.user import User
from src.db.repositories.payment_repo import PaymentRepository
from src.db.repositories.subscription_repo import SubscriptionRepository
from src.db.repositories.transaction_repo import TransactionRepository
from src.db.repositories.user_repo import UserRepository
from src.providers.payments.base import (
    BasePaymentProvider,
    PaymentResult,
)
from src.services.referral_service import ReferralService
from src.utils.logging import get_logger

__all__ = [
    "PaymentInfo",
    "PaymentService",
    "ProviderNotConfiguredError",
    "TariffNotAvailableForProviderError",
    "TariffNotFoundError",
    "create_payment_service",
]

logger = get_logger(__name__)


@dataclass
class PaymentInfo:
    """Информация о созданном платеже.

    Содержит данные для отображения пользователю.

    Attributes:
        payment_id: ID платежа в нашей БД.
        provider: Имя провайдера.
        tariff_slug: Slug тарифа.
        tokens_amount: Количество токенов.
        amount: Сумма платежа.
        currency: Код валюты.
        confirmation_url: URL для редиректа (YooKassa, Stripe) или None.
        provider_payment_id: ID платежа на стороне провайдера.
    """

    payment_id: int
    provider: str
    tariff_slug: str
    tokens_amount: int
    amount: Decimal
    currency: str
    confirmation_url: str | None
    provider_payment_id: str | None


class PaymentService:
    """Сервис для работы с платежами.

    Оркестрирует работу с различными платёжными провайдерами.
    Обрабатывает создание платежей и webhook'и.

    Attributes:
        _session: Асинхронная сессия SQLAlchemy.
        _providers: Словарь провайдеров {имя: экземпляр}.
        _yaml_config: Конфигурация из config.yaml.
        _payment_repo: Репозиторий платежей.
        _transaction_repo: Репозиторий транзакций.
        _user_repo: Репозиторий пользователей.
        _subscription_repo: Репозиторий подписок.
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
        self._transaction_repo = TransactionRepository(session)
        self._user_repo = UserRepository(session)
        self._subscription_repo = SubscriptionRepository(session)

    @property
    def available_providers(self) -> list[str]:
        """Получить список доступных (настроенных) провайдеров."""
        return list(self._providers.keys())

    def get_provider(self, provider_name: str) -> BasePaymentProvider | None:
        """Получить провайдер по имени.

        Args:
            provider_name: Имя провайдера.

        Returns:
            Экземпляр провайдера или None если не настроен.
        """
        return self._providers.get(provider_name)

    async def create_payment(
        self,
        user: User,
        tariff_slug: str,
        provider_name: str,
        *,
        return_url: str | None = None,
        save_payment_method: bool = False,
    ) -> PaymentInfo:
        """Создать платёж.

        Основной метод для инициации платежа. Создаёт запись в БД
        и вызывает провайдер для создания платежа на его стороне.

        Args:
            user: Пользователь, который платит.
            tariff_slug: Slug тарифа из config.yaml.
            provider_name: Имя провайдера (yookassa, stripe, telegram_stars).
            return_url: URL для возврата после оплаты (опционально).
            save_payment_method: Сохранить метод оплаты для рекуррентов.

        Returns:
            PaymentInfo с информацией о созданном платеже.

        Raises:
            TariffNotFoundError: Тариф не найден.
            ProviderNotConfiguredError: Провайдер не настроен.
            TariffNotAvailableForProviderError: Тариф недоступен для провайдера.
            PaymentError: Ошибка при создании платежа на стороне провайдера.
        """
        # Получаем тариф
        tariff = self._yaml_config.get_tariff(tariff_slug)
        if tariff is None:
            raise TariffNotFoundError(tariff_slug)

        # Проверяем провайдер
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ProviderNotConfiguredError(provider_name)

        # Проверяем, что тариф доступен для этого провайдера
        if not tariff.is_available_for_provider(provider_name):
            raise TariffNotAvailableForProviderError(tariff_slug, provider_name)

        # Получаем цену и валюту для провайдера
        price = tariff.get_price_for_provider(provider_name)
        currency = tariff.get_currency_for_provider(provider_name)

        if price is None or currency is None:
            raise TariffNotAvailableForProviderError(tariff_slug, provider_name)

        # Формируем описание
        # effective_tokens возвращает tokens для one_time
        # или tokens_per_period для subscription
        tokens_amount = tariff.effective_tokens
        description = f"Покупка {tokens_amount} токенов"

        # Создаём платёж на стороне провайдера
        payment_intent = await provider.create_payment(
            amount=Decimal(str(price)),
            currency=currency,
            user_id=user.telegram_id,
            tariff_slug=tariff_slug,
            description=description,
            return_url=return_url,
            save_payment_method=save_payment_method,
        )

        # Сохраняем платёж в БД
        metadata = {
            "tariff_slug": tariff_slug,
            "tokens_amount": tokens_amount,
            "user_telegram_id": user.telegram_id,
        }

        payment = await self._payment_repo.create(
            user_id=user.id,
            provider=provider_name,
            provider_payment_id=payment_intent.payment_id,
            amount=Decimal(str(price)),
            currency=currency,
            tariff_slug=tariff_slug,
            tokens_amount=tokens_amount,
            description=description,
            status=PaymentStatus.PENDING,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )

        await self._session.commit()

        logger.info(
            "Создан платёж: id=%s, provider=%s, tariff=%s, user=%s",
            payment.id,
            provider_name,
            tariff_slug,
            user.telegram_id,
        )

        return PaymentInfo(
            payment_id=payment.id,
            provider=provider_name,
            tariff_slug=tariff_slug,
            tokens_amount=tokens_amount,
            amount=Decimal(str(price)),
            currency=currency,
            confirmation_url=payment_intent.confirmation_url,
            provider_payment_id=payment_intent.payment_id,
        )

    async def process_webhook(
        self,
        provider_name: str,
        data: dict[str, Any],
    ) -> PaymentResult:
        """Обработать webhook от провайдера.

        Вызывается после верификации подписи webhook.
        Обновляет статус платежа в БД и начисляет токены при успехе.

        Args:
            provider_name: Имя провайдера.
            data: Распарсенные данные webhook.

        Returns:
            PaymentResult с результатом обработки.

        Raises:
            ProviderNotConfiguredError: Провайдер не настроен.
            PaymentError: Ошибка при обработке webhook.
        """
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ProviderNotConfiguredError(provider_name)

        # Парсим webhook через провайдер
        result = await provider.process_webhook(data)

        # Для автопродления подписки Stars — создаём новый платёж
        # Telegram присылает тот же payload, но это новое списание
        is_recurring_renewal = result.is_recurring and not data.get(
            "is_first_recurring", False
        )

        if is_recurring_renewal:
            # Автопродление: создаём новый платёж на основе оригинального
            payment = await self._handle_recurring_renewal(provider_name, result, data)
            if payment is None:
                return result
        else:
            # Обычный платёж: ищем существующий в БД
            payment = await self._find_payment(provider_name, result)
            if payment is None:
                logger.warning(
                    "Платёж не найден для webhook: provider=%s, payment_id=%s",
                    provider_name,
                    result.payment_id,
                )
                return result

            # Проверяем идемпотентность — если платёж уже обработан, пропускаем
            if payment.status == PaymentStatus.SUCCEEDED and result.is_success:
                logger.info(
                    "Webhook уже обработан (идемпотентность): payment_id=%s",
                    payment.id,
                )
                return result

        # Обновляем статус платежа
        completed_at = datetime.now(UTC) if result.is_success else None
        await self._payment_repo.update_status(
            payment,
            PaymentStatus(result.status.value),
            payment_method_id=result.payment_method_id,
            completed_at=completed_at,
        )

        # При успешной оплате — начисляем токены или активируем подписку
        if result.is_success:
            await self._process_successful_payment(payment, result.payment_method_id)

        await self._session.commit()

        logger.info(
            "Обработан webhook: payment_id=%s, status=%s, provider=%s",
            payment.id,
            result.status,
            provider_name,
        )

        return result

    async def _find_payment(
        self,
        provider_name: str,
        result: PaymentResult,
    ) -> Payment | None:
        """Найти платёж в БД по данным webhook.

        Args:
            provider_name: Имя провайдера.
            result: Результат парсинга webhook.

        Returns:
            Payment или None если не найден.
        """
        payment = None

        # Для Telegram Stars — используем internal_payment_id из payload
        internal_payment_id = result.metadata.get("payment_id")
        if internal_payment_id:
            payment = await self._payment_repo.get_by_id(int(internal_payment_id))
            # Сохраняем telegram_payment_charge_id для будущих refund
            if payment and result.payment_id and not payment.provider_payment_id:
                payment.provider_payment_id = result.payment_id

        # Fallback: поиск по provider_payment_id (для YooKassa, Stripe)
        if payment is None and result.payment_id:
            payment = await self._payment_repo.get_by_provider_id(
                provider_name, result.payment_id
            )

        return payment

    async def _handle_recurring_renewal(
        self,
        provider_name: str,
        result: PaymentResult,
        data: dict[str, Any],
    ) -> Payment | None:
        """Обработать автопродление подписки.

        При автопродлении Telegram присылает тот же payload, но это новое
        списание Stars. Создаём новый платёж на основе оригинального.

        Args:
            provider_name: Имя провайдера.
            result: Результат парсинга webhook.
            data: Сырые данные webhook.

        Returns:
            Новый Payment для автопродления или None при ошибке.
        """
        # Проверяем идемпотентность по telegram_payment_charge_id
        # Этот ID уникален для каждого списания
        if result.payment_id:
            existing = await self._payment_repo.get_by_provider_id(
                provider_name, result.payment_id
            )
            if existing:
                logger.info(
                    "Автопродление уже обработано: provider_payment_id=%s",
                    result.payment_id,
                )
                return None

        # Находим оригинальный платёж по payment_id из payload
        internal_payment_id = result.metadata.get("payment_id")
        if not internal_payment_id:
            logger.error(
                "Нет payment_id в payload для автопродления: %s",
                result.metadata,
            )
            return None

        original_payment = await self._payment_repo.get_by_id(int(internal_payment_id))
        if original_payment is None:
            logger.error(
                "Оригинальный платёж не найден для автопродления: id=%s",
                internal_payment_id,
            )
            return None

        # Создаём новый платёж для автопродления
        # Используем сумму и валюту из вебхука, либо fallback на оригинальный платёж
        amount = result.amount if result.amount is not None else original_payment.amount
        currency = result.currency or original_payment.currency

        metadata = {
            "tariff_slug": original_payment.tariff_slug,
            "tokens_amount": original_payment.tokens_amount,
            "user_telegram_id": data.get("user_id"),
            "original_payment_id": original_payment.id,
            "is_recurring_renewal": True,
        }

        payment = await self._payment_repo.create(
            user_id=original_payment.user_id,
            provider=provider_name,
            provider_payment_id=result.payment_id,
            amount=amount,
            currency=currency,
            tariff_slug=original_payment.tariff_slug,
            tokens_amount=original_payment.tokens_amount,
            description=f"Автопродление подписки ({original_payment.tariff_slug})",
            status=PaymentStatus.PENDING,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )

        logger.info(
            "Создан платёж для автопродления: id=%s, original_id=%s, user_id=%s",
            payment.id,
            original_payment.id,
            original_payment.user_id,
        )

        return payment

    async def _process_successful_payment(
        self, payment: Payment, payment_method_id: str | None = None
    ) -> None:
        """Обработать успешный платёж.

        Определяет тип тарифа и выполняет соответствующее действие:
        - Для one_time — начисляет токены на баланс
        - Для subscription — создаёт/активирует подписку

        Также выплачивает отложенный реферальный бонус пригласившему
        (если require_payment=True в конфиге реферальной программы).

        Args:
            payment: Успешно оплаченный платёж.
            payment_method_id: ID метода оплаты для рекуррентных платежей.
        """
        # Получаем тариф
        tariff = self._yaml_config.get_tariff(payment.tariff_slug)

        if tariff is None:
            # Тариф не найден — начисляем как обычные токены
            logger.warning(
                "Тариф не найден: %s, начисляем токены по умолчанию",
                payment.tariff_slug,
            )
            await self._credit_tokens(payment)
            return

        if tariff.is_subscription:
            # Подписочный тариф — создаём/активируем подписку
            await self._activate_subscription(payment, tariff, payment_method_id)
        else:
            # Разовый тариф — начисляем токены
            await self._credit_tokens(payment)

        # Выплачиваем отложенный реферальный бонус пригласившему
        # (если пользователь был приглашён и require_payment=True)
        await self._pay_pending_referral_bonus(payment)

    async def _credit_tokens(self, payment: Payment) -> None:
        """Начислить токены пользователю после успешной оплаты.

        Используется для разовых покупок токенов (one_time тарифы).

        Args:
            payment: Успешно оплаченный платёж.
        """
        # Получаем пользователя
        user = await self._user_repo.get_by_id(payment.user_id)
        if user is None:
            logger.error(
                "Пользователь не найден для начисления токенов: user_id=%s",
                payment.user_id,
            )
            return

        # Формируем описание транзакции
        description = f"Покупка токенов ({payment.tariff_slug})"

        # Получаем название тарифа для метаданных
        tariff = self._yaml_config.get_tariff(payment.tariff_slug)
        tariff_name = tariff.name.ru if tariff else payment.tariff_slug

        # Создаём транзакцию на начисление
        metadata = {
            "payment_id": payment.id,
            "tariff_slug": payment.tariff_slug,
            "tariff_name": tariff_name,
            "provider": payment.provider,
            "amount": str(payment.amount),
            "currency": payment.currency,
        }

        await self._transaction_repo.create(
            user=user,
            type_=TransactionType.PURCHASE,
            amount=payment.tokens_amount,
            description=description,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )

        logger.info(
            "Начислены токены: user_id=%s, tokens=%s, payment_id=%s",
            user.id,
            payment.tokens_amount,
            payment.id,
        )

    async def _activate_subscription(
        self,
        payment: Payment,
        tariff: TariffConfig,
        payment_method_id: str | None = None,
    ) -> None:
        """Активировать подписку после успешной оплаты.

        Создаёт новую подписку или продлевает существующую.

        Логика:
        1. Проверяем, есть ли активная подписка у пользователя
        2. Если есть — продлеваем (добавляем период)
        3. Если нет — создаём новую подписку

        Args:
            payment: Успешно оплаченный платёж.
            tariff: Конфигурация тарифа подписки.
            payment_method_id: ID метода оплаты для автопродления.
        """
        user = await self._user_repo.get_by_id(payment.user_id)
        if user is None:
            logger.error(
                "Пользователь не найден для активации подписки: user_id=%s",
                payment.user_id,
            )
            return

        # Проверяем существующую подписку
        existing = await self._subscription_repo.get_active_subscription(user.id)

        now = datetime.now(UTC)
        period_days = tariff.period_days
        tokens_per_period = tariff.tokens_per_period

        if existing and existing.tariff_slug == tariff.slug:
            # Продлеваем существующую подписку того же тарифа
            # Новый период начинается с окончания текущего
            period_start = existing.period_end
            period_end = period_start + timedelta(days=period_days)

            # Переносим неиспользованные токены если настроено
            carry_over = not tariff.burn_unused

            await self._subscription_repo.renew(
                existing,
                period_start=period_start,
                period_end=period_end,
                last_renewal_payment_id=payment.id,
                carry_over_tokens=carry_over,
            )

            logger.info(
                "Подписка продлена: subscription_id=%s, user_id=%s, period_end=%s",
                existing.id,
                user.id,
                period_end,
            )
        else:
            # Создаём новую подписку
            period_start = now
            period_end = now + timedelta(days=period_days)

            # Метаданные подписки
            metadata = {
                "first_payment_id": payment.id,
                "tariff_name": tariff.name.ru,
            }

            subscription = await self._subscription_repo.create(
                user_id=user.id,
                tariff_slug=tariff.slug,
                provider=payment.provider,
                tokens_per_period=tokens_per_period,
                period_start=period_start,
                period_end=period_end,
                status=SubscriptionStatus.ACTIVE,
                payment_method_id=payment_method_id,
                original_payment_id=payment.id,
                metadata_json=json.dumps(metadata, ensure_ascii=False),
            )

            # Устанавливаем автопродление если есть метод оплаты
            if payment_method_id:
                subscription.auto_renewal = True
                await self._session.flush()

            logger.info(
                "Создана подписка: subscription_id=%s, user_id=%s, "
                "tariff=%s, period_end=%s",
                subscription.id,
                user.id,
                tariff.slug,
                period_end,
            )

    async def _pay_pending_referral_bonus(self, payment: Payment) -> None:
        """Выплатить отложенный реферальный бонус пригласившему.

        Вызывается при успешной оплате приглашённого пользователя.
        Если в конфиге referral.require_payment=True, бонус пригласившему
        откладывается до первой оплаты — этот метод выплачивает его.

        Args:
            payment: Успешно оплаченный платёж.
        """
        # Проверяем, включена ли реферальная программа
        if not self._yaml_config.referral.enabled:
            return

        # Проверяем, требуется ли оплата для выплаты бонуса
        if not self._yaml_config.referral.require_payment:
            return

        # Получаем пользователя (приглашённого)
        user = await self._user_repo.get_by_id(payment.user_id)
        if user is None:
            return

        # Создаём сервис рефералов и пытаемся выплатить отложенный бонус
        referral_service = ReferralService(
            session=self._session,
            config=self._yaml_config.referral,
        )

        inviter, bonus_amount = await referral_service.pay_pending_bonus(user)

        if inviter and bonus_amount > 0:
            logger.info(
                "Выплачен отложенный реферальный бонус: "
                "inviter_id=%d, invitee_id=%d, amount=%d, payment_id=%d",
                inviter.id,
                user.id,
                bonus_amount,
                payment.id,
            )

    def get_tariffs_for_provider(self, provider_name: str) -> list[TariffConfig]:
        """Получить тарифы, доступные для провайдера.

        Args:
            provider_name: Имя провайдера.

        Returns:
            Список тарифов с ценой для этого провайдера.
        """
        return self._yaml_config.get_tariffs_for_provider(provider_name)

    def get_enabled_tariffs(self) -> list[TariffConfig]:
        """Получить все включённые тарифы.

        Returns:
            Список включённых тарифов.
        """
        return self._yaml_config.get_enabled_tariffs()


def create_payment_service(
    session: AsyncSession,
    providers: dict[str, BasePaymentProvider] | None = None,
    yaml_config: YamlConfig | None = None,
) -> PaymentService:
    """Фабричная функция для создания PaymentService.

    Основной способ создания сервиса платежей.
    Автоматически загружает конфигурацию если не передана.

    Args:
        session: Асинхронная сессия SQLAlchemy.
        providers: Словарь настроенных провайдеров (опционально).
        yaml_config: Конфигурация из config.yaml (опционально).

    Returns:
        Настроенный экземпляр PaymentService.

    Example:
        async with session_factory() as session:
            service = create_payment_service(session, providers)
            info = await service.create_payment(user, "tokens_100", "stars")
    """
    if yaml_config is None:
        from src.config.yaml_config import yaml_config as global_yaml_config

        yaml_config = global_yaml_config

    if providers is None:
        providers = {}

    return PaymentService(
        session=session,
        providers=providers,
        yaml_config=yaml_config,
    )
