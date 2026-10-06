"""Централизованные исключения приложения.

Этот модуль содержит ВСЕ кастомные исключения проекта.
Централизация исключений обеспечивает:
- Единый источник правды для всех типов ошибок
- Упрощённую навигацию и поддержку
- Единообразную иерархию исключений
- Удобный импорт: `from src.core.exceptions import SomeError`

Организация исключений по доменам:
- Database: Ошибки работы с БД
- AI Service: Ошибки AI-сервиса и моделей
- AI Providers: Ошибки провайдеров генерации
- Payment Service: Ошибки платёжного сервиса
- Payment Providers: Ошибки платёжных провайдеров
- Subscriptions: Ошибки подписок
- Billing: Ошибки биллинга
- Bot: Ошибки бота (cooldowns, лимиты)
- Debug: Отладочные исключения
"""

from typing_extensions import override

# =============================================================================
# DATABASE EXCEPTIONS
# =============================================================================
# Исключения для работы с базой данных.
# Иерархия: DatabaseError -> UserNotFoundError, DatabaseConnectionError, и т.д.
# =============================================================================


class DatabaseError(Exception):
    """Базовое исключение для ошибок работы с БД.

    Используется как родительский класс для всех ошибок БД.
    Может быть потенциально восстановимым (retry) в зависимости от причины.
    """

    def __init__(self, message: str, retryable: bool = False) -> None:
        """Создать исключение БД.

        Args:
            message: Описание ошибки.
            retryable: Можно ли повторить операцию (True для временных сбоев).
        """
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class UserNotFoundError(DatabaseError):
    """Пользователь не найден в базе данных.

    Невосстановимая ошибка — пользователь должен выполнить /start для регистрации.
    """

    def __init__(self, telegram_id: int) -> None:
        """Создать исключение о ненайденном пользователе.

        Args:
            telegram_id: ID пользователя в Telegram.
        """
        super().__init__(
            f"Пользователь с telegram_id={telegram_id} не найден в БД",
            retryable=False,
        )
        self.telegram_id = telegram_id


class DatabaseConnectionError(DatabaseError):
    """Ошибка подключения к базе данных.

    Потенциально восстановимая — может помочь retry через несколько секунд.
    """

    def __init__(self, original_error: Exception) -> None:
        """Создать исключение о проблемах с подключением к БД.

        Args:
            original_error: Оригинальное исключение от SQLAlchemy.
        """
        super().__init__(
            f"Не удалось подключиться к БД: {original_error}",
            retryable=True,
        )
        self.original_error = original_error


class DatabaseOperationError(DatabaseError):
    """Ошибка выполнения операции с БД.

    Может быть восстановимой (deadlock, timeout) или невосстановимой
    (constraint violation).
    """

    def __init__(
        self, operation: str, original_error: Exception, retryable: bool = False
    ) -> None:
        """Создать исключение об ошибке операции БД.

        Args:
            operation: Название операции (add_message, get_context, и т.д.).
            original_error: Оригинальное исключение от SQLAlchemy.
            retryable: Можно ли повторить операцию.
        """
        super().__init__(
            f"Ошибка выполнения операции '{operation}': {original_error}",
            retryable=retryable,
        )
        self.operation = operation
        self.original_error = original_error


# =============================================================================
# AI SERVICE EXCEPTIONS
# =============================================================================
# Исключения для AI-сервиса (оркестрация генераций).
# Связаны с конфигурацией и логикой сервиса, а не с конкретным провайдером.
# =============================================================================


class AIServiceError(Exception):
    """Базовое исключение для ошибок AI-сервиса.

    Используется когда ошибка связана с конфигурацией или логикой сервиса,
    а не с конкретным провайдером.

    Attributes:
        message: Описание ошибки.
        model_key: Ключ модели, при работе с которой произошла ошибка.
    """

    def __init__(self, message: str, model_key: str | None = None) -> None:
        """Создать исключение AIServiceError.

        Args:
            message: Описание ошибки.
            model_key: Ключ модели (опционально).
        """
        self.message = message
        self.model_key = model_key
        super().__init__(message)


class ModelNotFoundError(AIServiceError):
    """Модель не найдена в конфигурации.

    Возникает когда запрашивается модель, которой нет в config.yaml.
    """


# =============================================================================
# AI PROVIDER EXCEPTIONS
# =============================================================================
# Исключения для AI-провайдеров (OpenRouter, RouterAI, и т.д.).
# Связаны с ошибками конкретных провайдеров при генерации.
# =============================================================================


class GenerationError(Exception):
    """Ошибка при генерации.

    Выбрасывается когда провайдер не может выполнить запрос.
    Содержит информацию для логирования и отображения пользователю.

    Attributes:
        message: Человекочитаемое описание ошибки.
        provider: Название провайдера (openai, replicate, и т.д.).
        model_id: ID модели, на которой произошла ошибка.
        is_retryable: Можно ли повторить запрос (True для временных ошибок).
        original_error: Оригинальное исключение от SDK провайдера.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model_id: str,
        is_retryable: bool = False,
        original_error: Exception | None = None,
    ) -> None:
        """Создать ошибку генерации.

        Args:
            message: Описание ошибки.
            provider: Название провайдера.
            model_id: ID модели.
            is_retryable: Можно ли повторить запрос.
            original_error: Оригинальное исключение.
        """
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model_id = model_id
        self.is_retryable = is_retryable
        self.original_error = original_error

    @override
    def __str__(self) -> str:
        """Строковое представление ошибки."""
        return f"[{self.provider}:{self.model_id}] {self.message}"


class ProviderNotAvailableError(Exception):
    """Провайдер недоступен или не зарегистрирован.

    Возникает когда:
    - Провайдер не зарегистрирован в реестре
    - API-ключ для провайдера не настроен

    Attributes:
        message: Описание ошибки.
        provider_type: Тип провайдера, который недоступен.
    """

    def __init__(self, message: str, provider_type: str | None = None) -> None:
        """Создать исключение ProviderNotAvailableError.

        Args:
            message: Описание ошибки.
            provider_type: Тип провайдера (опционально).
        """
        self.message = message
        self.provider_type = provider_type
        super().__init__(message)


# =============================================================================
# PAYMENT SERVICE EXCEPTIONS
# =============================================================================
# Исключения для сервиса платежей.
# Связаны с тарифами и конфигурацией провайдеров.
# =============================================================================


class TariffNotFoundError(Exception):
    """Тариф не найден.

    Возникает когда указан несуществующий slug тарифа.
    """

    def __init__(self, slug: str) -> None:
        """Создать исключение.

        Args:
            slug: Slug тарифа, который не найден.
        """
        super().__init__(f"Тариф не найден: {slug}")
        self.slug = slug


class ProviderNotConfiguredError(Exception):
    """Провайдер не настроен.

    Возникает когда пытаются использовать провайдер,
    у которого не настроены ключи.
    """

    def __init__(self, provider: str) -> None:
        """Создать исключение.

        Args:
            provider: Имя провайдера.
        """
        super().__init__(f"Провайдер не настроен: {provider}")
        self.provider = provider


class TariffNotAvailableForProviderError(Exception):
    """Тариф недоступен для провайдера.

    Возникает когда тариф не имеет цены для указанного провайдера.
    """

    def __init__(self, slug: str, provider: str) -> None:
        """Создать исключение.

        Args:
            slug: Slug тарифа.
            provider: Имя провайдера.
        """
        super().__init__(f"Тариф {slug} недоступен для провайдера {provider}")
        self.slug = slug
        self.provider = provider


# =============================================================================
# PAYMENT PROVIDER EXCEPTIONS
# =============================================================================
# Исключения для платёжных провайдеров (YooKassa, Stripe, Telegram Stars).
# =============================================================================


class PaymentError(Exception):
    """Ошибка при работе с платёжным провайдером.

    Выбрасывается когда провайдер не может выполнить операцию.
    Содержит информацию для логирования и отображения пользователю.

    Attributes:
        message: Человекочитаемое описание ошибки.
        provider: Название провайдера (yookassa, stripe, telegram_stars).
        is_retryable: Можно ли повторить операцию (True для временных ошибок).
        original_error: Оригинальное исключение от SDK провайдера.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        is_retryable: bool = False,
        original_error: Exception | None = None,
    ) -> None:
        """Создать ошибку платежа.

        Args:
            message: Описание ошибки.
            provider: Название провайдера.
            is_retryable: Можно ли повторить операцию.
            original_error: Оригинальное исключение.
        """
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.is_retryable = is_retryable
        self.original_error = original_error

    @override
    def __str__(self) -> str:
        """Строковое представление ошибки."""
        return f"[{self.provider}] {self.message}"


# =============================================================================
# SUBSCRIPTION EXCEPTIONS
# =============================================================================
# Исключения для работы с подписками пользователей.
# =============================================================================


class SubscriptionError(Exception):
    """Базовое исключение для ошибок подписок."""


class SubscriptionNotFoundError(SubscriptionError):
    """Подписка не найдена."""

    def __init__(self, subscription_id: int) -> None:
        self.subscription_id = subscription_id
        super().__init__(f"Подписка с id={subscription_id} не найдена")


class SubscriptionExpiredError(SubscriptionError):
    """Подписка истекла."""

    def __init__(self, subscription_id: int) -> None:
        self.subscription_id = subscription_id
        super().__init__(f"Подписка с id={subscription_id} истекла")


class InsufficientSubscriptionTokensError(SubscriptionError):
    """Недостаточно токенов в подписке."""

    def __init__(self, subscription_id: int, required: int, available: int) -> None:
        self.subscription_id = subscription_id
        self.required = required
        self.available = available
        super().__init__(
            f"Недостаточно токенов: требуется {required}, доступно {available}"
        )


# =============================================================================
# BILLING EXCEPTIONS
# =============================================================================
# Исключения для сервиса биллинга (управление токенами).
# =============================================================================


class InsufficientBalanceError(Exception):
    """Недостаточно токенов для генерации.

    Возникает когда баланс меньше стоимости генерации.

    Attributes:
        user_id: ID пользователя.
        model_key: Ключ модели.
        required: Требуемое количество токенов.
        available: Доступное количество токенов.
    """

    def __init__(
        self,
        user_id: int,
        model_key: str,
        required: int,
        available: int,
    ) -> None:
        """Создать исключение о недостаточном балансе.

        Args:
            user_id: ID пользователя.
            model_key: Ключ модели.
            required: Требуемое количество токенов.
            available: Доступное количество токенов.
        """
        self.user_id = user_id
        self.model_key = model_key
        self.required = required
        self.available = available
        super().__init__(
            f"Недостаточно токенов: требуется {required}, доступно {available} "
            f"(user_id={user_id}, model_key={model_key})"
        )


# =============================================================================
# BOT EXCEPTIONS
# =============================================================================
# Исключения для бота (cooldowns, лимиты генераций).
# =============================================================================


class CooldownError(Exception):
    """Пользователь попытался запустить генерацию слишком рано.

    Возникает когда пользователь пытается запустить генерацию до истечения
    минимального интервала (cooldown) после предыдущей генерации того же типа.

    Attributes:
        seconds_left: Сколько секунд осталось до разрешения следующего запроса.
        generation_type: Тип генерации (chat, image, tts, stt).
    """

    def __init__(self, seconds_left: int, generation_type: str) -> None:
        """Создать исключение CooldownError.

        Args:
            seconds_left: Количество секунд до разрешения.
            generation_type: Тип генерации.
        """
        self.seconds_left = seconds_left
        self.generation_type = generation_type
        super().__init__(
            f"Подождите {seconds_left} сек перед следующим запросом "
            f"(тип: {generation_type})"
        )


class TooManyGenerationsError(Exception):
    """Превышен лимит параллельных генераций для пользователя.

    Возникает когда пользователь пытается запустить генерацию, когда
    уже выполняется максимальное количество параллельных задач.

    Attributes:
        current_count: Текущее количество активных генераций.
        max_allowed: Максимально разрешённое количество.
    """

    def __init__(self, current_count: int, max_allowed: int) -> None:
        """Создать исключение TooManyGenerationsError.

        Args:
            current_count: Текущее количество активных генераций.
            max_allowed: Максимально разрешённое количество.
        """
        self.current_count = current_count
        self.max_allowed = max_allowed
        super().__init__(
            f"Слишком много активных генераций: {current_count}/{max_allowed}. "
            "Дождитесь завершения предыдущих запросов."
        )


# =============================================================================
# DEBUG EXCEPTIONS
# =============================================================================
# Отладочные исключения для тестирования.
# =============================================================================


class DebugError(Exception):
    """Отладочное исключение для проверки системы отслеживания ошибок.

    Это исключение вызывается командой /error для тестирования:
    - Логирования ошибок в терминал
    - Отправки уведомлений в систему мониторинга
    - Отображения сообщения пользователю

    Примечание: Класс назван DebugError (а не TestError), чтобы pytest
    не путал его с тестовым классом и не выдавал предупреждение.
    """
