"""Утилиты биллинга для обработчиков бота.

Этот модуль содержит общую логику проверки и списания токенов,
используемую во всех handlers генерации (chatgpt, imagine, edit_image).

Паттерн использования:
1. check_billing_and_show_error() — перед генерацией
2. ... выполнить генерацию и отправить результат ...
3. charge_after_delivery() — после успешной доставки
"""

from aiogram.types import Message

from src.config.yaml_config import yaml_config
from src.db.models.user import User
from src.services.billing_service import (
    PER_GENERATION_PRICING,
    BillingService,
    ChargeResult,
    GenerationCost,
)
from src.utils.i18n import Localization
from src.utils.logging import get_logger

logger = get_logger(__name__)


async def check_billing_and_show_error(
    billing: BillingService,
    user: User,
    model_key: str,
    processing_msg: Message,
    l10n: Localization,
    quantity: float = PER_GENERATION_PRICING,
) -> GenerationCost | None:
    """Проверить возможность генерации и показать ошибку при нехватке токенов.

    Централизованная проверка баланса перед любой генерацией.
    Если токенов недостаточно — редактирует processing_msg с сообщением об ошибке.

    Стратегии ценообразования:
    - PerGenerationPricing: quantity=PER_GENERATION_PRICING (по умолчанию)
      Для chat, image, image_edit — фиксированная цена за запрос.
    - PerMinutePricing: quantity=минуты
      Для stt, tts — цена за минуту аудио.

    Args:
        billing: Сервис биллинга.
        user: Пользователь, запрашивающий генерацию.
        model_key: Ключ модели из config.yaml.
        processing_msg: Сообщение "Генерирую..." для редактирования при ошибке.
        l10n: Объект локализации.
        quantity: Множитель стоимости. По умолчанию PER_GENERATION_PRICING (1.0).
            Для STT/TTS передайте количество минут (PerMinutePricing).

    Returns:
        GenerationCost если генерация возможна, None если недостаточно токенов.

    Example:
        # PerGenerationPricing (chat, image)
        cost = await check_billing_and_show_error(
            billing, user, model_key, processing_msg, l10n
        )

        # PerMinutePricing (stt — 5 минут аудио)
        cost = await check_billing_and_show_error(
            billing, user, model_key, processing_msg, l10n, quantity=5.0
        )

        if cost is None:
            return  # Ошибка уже показана пользователю
    """
    cost = await billing.check_and_reserve(user, model_key, quantity=quantity)

    if not cost.can_proceed:
        # Недостаточно токенов — показываем ошибку
        model_config = yaml_config.get_model(model_key)
        model_name = model_config.display_name if model_config else model_key

        await processing_msg.edit_text(
            l10n.get(
                "billing_insufficient_balance",
                model_name=model_name,
                price=cost.tokens_cost,
                balance=user.balance,
            )
        )

        logger.info(
            "Недостаточно токенов: user_id=%d, model=%s, price=%d, balance=%d",
            user.id,
            model_key,
            cost.tokens_cost,
            user.balance,
        )
        return None

    return cost


async def charge_after_delivery(
    billing: BillingService,
    user: User,
    model_key: str,
    cost: GenerationCost,
    generation_type: str,
) -> ChargeResult:
    """Списать токены после успешной доставки результата пользователю.

    Вызывается ПОСЛЕ того, как результат генерации был отправлен пользователю.
    Это гарантирует, что токены списываются только за реально полученные генерации.

    Если модель бесплатная (tokens_cost=0), списание не происходит.

    Args:
        billing: Сервис биллинга.
        user: Пользователь, которому списываем токены.
        model_key: Ключ модели.
        cost: Результат check_and_reserve() — содержит информацию о стоимости.
        generation_type: Тип генерации (chat, image, image_edit, tts, stt).

    Returns:
        ChargeResult с информацией о списанных токенах и ID транзакции.

    Example:
        # После успешной отправки изображения
        result = await charge_after_delivery(
            billing, user, model_key, cost, "image"
        )
        # result.tokens_charged — сколько списано
        # result.transaction_id — ID транзакции (если списано из баланса)
    """
    charge_result = await billing.charge_generation(
        user, model_key, cost, generation_type
    )

    # Логируем только при реальном списании (модель не бесплатная)
    if charge_result.tokens_charged > 0:
        logger.info(
            "Списание за генерацию: user_id=%d, model=%s, tokens=%d "
            "(подписка=%d, баланс=%d)",
            user.id,
            model_key,
            charge_result.tokens_charged,
            charge_result.from_subscription,
            charge_result.from_balance,
        )

    return charge_result
