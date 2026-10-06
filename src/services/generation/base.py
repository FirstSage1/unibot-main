"""Базовый сервис генерации с Template Method Pattern.

Этот модуль содержит абстрактный базовый класс для всех типов генераций.
Реализует общий алгоритм генерации через Template Method Pattern.

Template Method Pattern:
1. Определяет скелет алгоритма в базовом классе
2. Делегирует специфичные шаги подклассам через абстрактные методы
3. Гарантирует единообразное выполнение общей логики

Общий алгоритм генерации:
1. Проверка billing (достаточно ли токенов)
2. Проверка cooldown (не слишком ли быстро повторная генерация)
3. Создание записи о генерации в БД (с предварительной себестоимостью)
4. Выполнение генерации (делегируется подклассам)
5. Обновление статуса генерации (с финальной себестоимостью)
6. Списание токенов после успешной доставки
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.utils.billing import charge_after_delivery, check_billing_and_show_error
from src.bot.utils.generation_cooldown import (
    CooldownError,
    check_generation_cooldown,
)
from src.config.yaml_config import ModelConfig, yaml_config
from src.core.exceptions import GenerationError
from src.db.exceptions import DatabaseError, UserNotFoundError
from src.db.models import GenerationDBStatus
from src.db.repositories import GenerationRepository, UserRepository
from src.services.ai_service import AIService, create_ai_service
from src.services.billing_service import create_billing_service
from src.utils.i18n import Localization
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class GenerationResult:
    """Результат генерации AI.

    Attributes:
        content: Сгенерированный контент (текст, URL изображения, и т.д.)
        success: Успешно ли завершилась генерация
        error_message: Сообщение об ошибке (если success=False)
        usage: Информация об использованных токенах (для расчёта себестоимости).
            Ожидаемые ключи: prompt_tokens, completion_tokens, total_tokens.
    """

    content: str
    success: bool = True
    error_message: str | None = None
    usage: dict[str, int] | None = None


class BaseGenerationService(ABC):
    """Абстрактный базовый сервис для всех типов генераций.

    Реализует Template Method Pattern для переиспользования общей логики:
    - Проверка billing и cooldown
    - Tracking генераций в БД
    - Обработка ошибок
    - Списание токенов

    Подклассы должны реализовать только специфичную логику генерации
    через метод _perform_generation().

    Attributes:
        generation_type: Тип генерации (chat, image, image_edit, tts, stt).
            Должен быть переопределён в подклассах.
    """

    generation_type: str  # Переопределяется в подклассах

    def __init__(
        self,
        session: AsyncSession,
        ai_service: AIService | None = None,
    ) -> None:
        """Создать сервис генерации.

        Args:
            session: Асинхронная сессия SQLAlchemy для работы с БД.
            ai_service: AI-сервис для генерации. Если None — создаётся автоматически.
                Параметр нужен для DI в тестах.
        """
        self.session = session
        self.ai_service = ai_service or create_ai_service()

        # Репозитории для работы с БД
        self.user_repo = UserRepository(session)
        self.generation_repo = GenerationRepository(session)
        self.billing_service = create_billing_service(session)

    async def execute(
        self,
        telegram_user_id: int,
        model_key: str,
        processing_msg: Message,
        l10n: Localization,
        **generation_params: Any,
    ) -> GenerationResult:
        """Template Method: общий алгоритм генерации для всех типов.

        Этот метод определяет скелет алгоритма генерации:
        1. Получить пользователя из БД
        2. Проверить billing (достаточно ли токенов)
        3. Проверить cooldown (не слишком ли часто)
        4. Создать запись о генерации
        5. Выполнить генерацию (делегируется подклассам)
        6. Обновить статус генерации
        7. Списать токены

        Args:
            telegram_user_id: Telegram ID пользователя.
            model_key: Ключ модели из config.yaml.
            processing_msg: Сообщение "Генерирую..." для редактирования при ошибках.
            l10n: Объект локализации для переводов.
            **generation_params: Специфичные параметры для конкретного типа генерации.
                Передаются в _perform_generation().

        Returns:
            GenerationResult с результатом генерации.

        Raises:
            UserNotFoundError: Пользователь не найден в БД.
            GenerationError: Ошибка при генерации AI.
            DatabaseError: Ошибка работы с БД.
        """
        try:
            # === ШАГ 1: Получить пользователя из БД ===
            user = await self.user_repo.get_by_telegram_id(telegram_user_id)
            if not user:
                raise UserNotFoundError(telegram_user_id)

            # === ШАГ 2: БИЛЛИНГ - Проверяем возможность генерации ===
            cost = await check_billing_and_show_error(
                self.billing_service, user, model_key, processing_msg, l10n
            )
            if cost is None:
                # Недостаточно токенов, ошибка уже показана пользователю
                return GenerationResult(
                    content="",
                    success=False,
                    error_message="Insufficient balance",
                )

            # === ШАГ 3: COOLDOWN - Защита от дублирования генераций ===
            try:
                await check_generation_cooldown(
                    generation_repo=self.generation_repo,
                    user_id=user.id,
                    generation_type=self.generation_type,
                    config=yaml_config,
                )
            except CooldownError as e:
                await processing_msg.edit_text(
                    l10n.get("generation_cooldown", seconds=e.seconds_left)
                )
                logger.info(
                    "Cooldown для %s: user_id=%d, осталось=%d сек",
                    self.generation_type,
                    user.id,
                    e.seconds_left,
                )
                return GenerationResult(
                    content="",
                    success=False,
                    error_message=f"Cooldown: {e.seconds_left} seconds left",
                )

            # === ШАГ 4: Получаем конфигурацию модели для расчёта себестоимости ===
            model_config = yaml_config.get_model(model_key)
            preliminary_cost = self._calculate_preliminary_cost(model_config)

            # === ШАГ 5: TRACKING - Создаём запись о начале генерации ===
            generation = await self.generation_repo.create_generation(
                user_id=user.id,
                generation_type=self.generation_type,
                model_key=model_key,
                cost_rub=preliminary_cost if preliminary_cost > 0 else None,
            )

            try:
                # === ШАГ 6: ГЕНЕРАЦИЯ - Вызываем специфичный метод подкласса ===
                result = await self._perform_generation(
                    model_key=model_key, **generation_params
                )

                # === ШАГ 7: Рассчитываем финальную себестоимость ===
                final_cost = self._calculate_final_cost(
                    model_config, preliminary_cost, result.usage
                )

                # === ШАГ 8: БИЛЛИНГ - Списываем токены ПОСЛЕ успешной генерации ===
                charge_result = await charge_after_delivery(
                    self.billing_service,
                    user,
                    model_key,
                    cost,
                    self.generation_type,
                )

                # === ШАГ 9: TRACKING - Обновляем статус с данными списания ===
                await self.generation_repo.update_generation_status(
                    generation.id,
                    GenerationDBStatus.COMPLETED,
                    cost_rub=final_cost if final_cost > 0 else None,
                    tokens_charged=charge_result.tokens_charged,
                    transaction_id=charge_result.transaction_id,
                )

                logger.info(
                    "Генерация %s успешна: user_id=%d, model=%s, cost_rub=%s, "
                    "tokens_charged=%d",
                    self.generation_type,
                    user.id,
                    model_key,
                    final_cost,
                    charge_result.tokens_charged,
                )

                return result

            except (GenerationError, DatabaseError):
                # === TRACKING: Обновляем статус на FAILED ===
                await self.generation_repo.update_generation_status(
                    generation.id,
                    GenerationDBStatus.FAILED,
                )
                # Пробрасываем исключение дальше для обработки внешним handler
                raise

        except UserNotFoundError as e:
            # Пользователь не зарегистрирован — невосстановимая ошибка
            await processing_msg.edit_text(l10n.get("error_user_not_found"))
            logger.warning(
                "Попытка использования бота незарегистрированным пользователем: "
                "telegram_id=%d",
                e.telegram_id,
            )
            return GenerationResult(
                content="",
                success=False,
                error_message="User not found",
            )

        except GenerationError as e:
            # Ошибка генерации AI (таймаут, недоступен провайдер, и т.д.)
            await processing_msg.edit_text(l10n.get("generation_error"))
            # Детальное логирование с информацией из исключения
            logger.error(
                "Ошибка генерации | type=%s | user=%d | model=%s | "
                "provider=%s | retryable=%s | error=%s",
                self.generation_type,
                telegram_user_id,
                model_key,
                e.provider,
                e.is_retryable,
                e.message,
            )
            return GenerationResult(
                content="",
                success=False,
                error_message=e.message,
            )

        except DatabaseError as e:
            # Ошибка работы с БД (может быть восстановимой)
            if e.retryable:
                await processing_msg.edit_text(l10n.get("error_db_temporary"))
                logger.warning(
                    "Восстановимая ошибка БД (%s): user_id=%d, error=%s",
                    self.generation_type,
                    telegram_user_id,
                    e.message,
                )
            else:
                await processing_msg.edit_text(l10n.get("error_db_permanent"))
                logger.error(
                    "Невосстановимая ошибка БД (%s): user_id=%d, error=%s",
                    self.generation_type,
                    telegram_user_id,
                    e.message,
                )
            return GenerationResult(
                content="",
                success=False,
                error_message=e.message,
            )

        except Exception:
            # Неожиданная ошибка (баг в коде, и т.д.)
            await processing_msg.edit_text(l10n.get("generation_unexpected_error"))
            logger.exception(
                "Неожиданная ошибка в генерации %s: user_id=%d, model=%s",
                self.generation_type,
                telegram_user_id,
                model_key,
            )
            return GenerationResult(
                content="",
                success=False,
                error_message="Unexpected error",
            )

    @abstractmethod
    async def _perform_generation(
        self, model_key: str, **generation_params: Any
    ) -> GenerationResult:
        """Выполнить специфичную логику генерации для данного типа.

        Этот метод должен быть реализован в подклассах.
        Каждый тип генерации имеет свои параметры и логику.

        Args:
            model_key: Ключ модели из config.yaml.
            **generation_params: Специфичные параметры для конкретного типа:
                - chat: messages (list[dict])
                - image: prompt (str)
                - tts: text (str), voice (str)
                - и т.д.

        Returns:
            GenerationResult с сгенерированным контентом.

        Raises:
            GenerationError: Если генерация не удалась.

        Example (в подклассе):
            async def _perform_generation(self, model_key: str, **params):
                messages = params.get("messages")
                result = await self.ai_service.generate(
                    model_key=model_key,
                    messages=messages
                )
                return GenerationResult(content=result.content)
        """

    def _calculate_preliminary_cost(self, model_config: ModelConfig | None) -> Decimal:
        """Рассчитать предварительную себестоимость генерации.

        Предварительная себестоимость используется для записи в БД при создании
        генерации. Для моделей с фиксированной ценой за запрос (per_request_rub)
        это будет финальная себестоимость. Для моделей с ценой за токены —
        возвращаем 0, т.к. точная цена будет известна только после генерации.

        Args:
            model_config: Конфигурация модели из yaml_config.
                Может быть None если модель не найдена.

        Returns:
            Предварительная себестоимость в рублях.
            0 если конфигурация отсутствует или цена за токены.
        """
        if model_config is None or model_config.cost is None:
            return Decimal(0)

        # Для моделей с фиксированной ценой за запрос — возвращаем её
        if model_config.cost.per_request_rub is not None:
            return model_config.cost.calculate()

        # Для моделей с ценой за токены — себестоимость будет рассчитана после
        return Decimal(0)

    def _calculate_final_cost(
        self,
        model_config: ModelConfig | None,
        preliminary_cost: Decimal,
        usage: dict[str, int] | None,
    ) -> Decimal:
        """Рассчитать финальную себестоимость генерации.

        Финальная себестоимость учитывает фактическое использование токенов.
        Для моделей с фиксированной ценой — возвращаем предварительную.
        Для моделей с ценой за токены — рассчитываем по usage.

        Args:
            model_config: Конфигурация модели из yaml_config.
            preliminary_cost: Предварительная себестоимость (для per_request моделей).
            usage: Информация об использованных токенах от AI-провайдера.
                Ожидаемые ключи: prompt_tokens, completion_tokens.

        Returns:
            Финальная себестоимость в рублях.
        """
        # Если предварительная цена уже рассчитана (per_request модель)
        if preliminary_cost > 0:
            return preliminary_cost

        if model_config is None or model_config.cost is None:
            return Decimal(0)

        # Рассчитываем по токенам
        return model_config.cost.calculate(usage)
