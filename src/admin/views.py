"""Представления (views) для админки.

Каждый ModelView определяет как модель отображается в админке:
- Какие колонки показывать в списке
- Какие поля доступны для поиска
- Какие поля можно редактировать

Примечание: SQLAdmin использует атрибуты класса для конфигурации.
Это стандартный паттерн библиотеки, поэтому отключаем RUF012.
"""

# ruff: noqa: RUF012, S704

from typing import Any
from urllib.parse import urlencode

from markupsafe import Markup
from sqladmin import ModelView, action
from sqladmin._types import UnsetAny
from sqladmin.filters import BooleanFilter
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import RedirectResponse

from src.config.settings import settings
from src.db.models.broadcast import Broadcast, BroadcastStatus, ParseMode
from src.db.models.generation import Generation, GenerationDBStatus
from src.db.models.payment import Payment, PaymentProvider, PaymentStatus
from src.db.models.referral import Referral
from src.db.models.subscription import Subscription, SubscriptionStatus
from src.db.models.user import User
from src.utils.timezone import format_datetime

# Часовой пояс для отображения времени в админке
ADMIN_TIMEZONE = settings.logging.timezone

# Словари для форматирования статусов рассылок
BROADCAST_STATUS_LABELS = {
    BroadcastStatus.DRAFT: "Черновик",
    BroadcastStatus.PENDING: "Ожидает",
    BroadcastStatus.RUNNING: "Выполняется",
    BroadcastStatus.PAUSED: "Приостановлена",
    BroadcastStatus.COMPLETED: "Завершена",
    BroadcastStatus.CANCELLED: "Отменена",
    BroadcastStatus.FAILED: "Ошибка",
}

# Цвета для статус-бейджей рассылок (Bootstrap классы)
BROADCAST_STATUS_COLORS = {
    BroadcastStatus.DRAFT: "secondary",
    BroadcastStatus.PENDING: "info",
    BroadcastStatus.RUNNING: "warning",
    BroadcastStatus.PAUSED: "dark",
    BroadcastStatus.COMPLETED: "success",
    BroadcastStatus.CANCELLED: "secondary",
    BroadcastStatus.FAILED: "danger",
}

PARSE_MODE_LABELS = {
    ParseMode.HTML: "HTML",
    ParseMode.MARKDOWN: "Markdown",
    ParseMode.MARKDOWN_V2: "MarkdownV2",
    ParseMode.NONE: "Без форматирования",
}

# Словари для форматирования статусов и провайдеров платежей
PAYMENT_STATUS_LABELS = {
    PaymentStatus.PENDING: "Ожидает оплаты",
    PaymentStatus.SUCCEEDED: "Успешно",
    PaymentStatus.FAILED: "Ошибка",
    PaymentStatus.REFUNDED: "Возврат",
    PaymentStatus.CANCELED: "Отменён",
}

PAYMENT_PROVIDER_LABELS = {
    PaymentProvider.YOOKASSA: "ЮKassa (RUB)",
    PaymentProvider.STRIPE: "Stripe (USD)",
    PaymentProvider.TELEGRAM_STARS: "Telegram Stars (XTR)",
}

# Словари для форматирования статусов подписок
SUBSCRIPTION_STATUS_LABELS = {
    SubscriptionStatus.PENDING: "Ожидает",
    SubscriptionStatus.ACTIVE: "Активна",
    SubscriptionStatus.PAST_DUE: "Просрочена",
    SubscriptionStatus.CANCELED: "Отменена",
    SubscriptionStatus.EXPIRED: "Истекла",
}

# Словари для форматирования статусов генераций
GENERATION_STATUS_LABELS = {
    GenerationDBStatus.PENDING: "В обработке",
    GenerationDBStatus.COMPLETED: "Завершена",
    GenerationDBStatus.FAILED: "Ошибка",
}


# =============================================================================
# КАСТОМНЫЕ ФИЛЬТРЫ ДЛЯ АДМИНКИ
# =============================================================================


class HasPaymentsFilter:
    """Фильтр пользователей по наличию успешных платежей.

    Позволяет отфильтровать:
    - Платящих пользователей (с успешными платежами)
    - Бесплатных пользователей (без успешных платежей)
    """

    has_operator = False
    default_value: UnsetAny = "all"
    template = "sqladmin/filters/lookup_filter.html"
    title = "Платежи"
    parameter_name = "has_payments"

    async def lookups(
        self, request: Any, model: Any, run_query: Any
    ) -> list[tuple[str, str]]:
        """Возвращает варианты фильтра."""
        return [
            ("all", "Все"),
            ("paid", "Платящие"),
            ("free", "Бесплатные"),
        ]

    async def get_filtered_query(self, query: Any, value: str, model: Any) -> Any:
        """Применяет фильтр к запросу."""
        if value == "paid":
            # Пользователи с хотя бы одним успешным платежом
            subquery = select(Payment.user_id).where(
                Payment.status == PaymentStatus.SUCCEEDED.value
            )
            return query.where(User.id.in_(subquery))
        elif value == "free":
            # Пользователи без успешных платежей
            subquery = select(Payment.user_id).where(
                Payment.status == PaymentStatus.SUCCEEDED.value
            )
            return query.where(~User.id.in_(subquery))
        return query


class HasActiveSubscriptionFilter:
    """Фильтр пользователей по наличию активной подписки.

    Позволяет отфильтровать:
    - Подписчиков (с активной подпиской)
    - Без подписки (без активной подписки)
    """

    has_operator = False
    default_value: UnsetAny = "all"
    template = "sqladmin/filters/lookup_filter.html"
    title = "Подписка"
    parameter_name = "has_subscription"

    async def lookups(
        self, request: Any, model: Any, run_query: Any
    ) -> list[tuple[str, str]]:
        """Возвращает варианты фильтра."""
        return [
            ("all", "Все"),
            ("subscribed", "С подпиской"),
            ("no_subscription", "Без подписки"),
        ]

    async def get_filtered_query(self, query: Any, value: str, model: Any) -> Any:
        """Применяет фильтр к запросу."""
        if value == "subscribed":
            # Пользователи с активной подпиской
            subquery = select(Subscription.user_id).where(
                Subscription.status == SubscriptionStatus.ACTIVE.value
            )
            return query.where(User.id.in_(subquery))
        elif value == "no_subscription":
            # Пользователи без активной подписки
            subquery = select(Subscription.user_id).where(
                Subscription.status == SubscriptionStatus.ACTIVE.value
            )
            return query.where(~User.id.in_(subquery))
        return query


class SubscriptionStatusFilter:
    """Фильтр подписок по статусу."""

    has_operator = False
    default_value: UnsetAny = "all"
    template = "sqladmin/filters/lookup_filter.html"
    title = "Статус подписки"
    parameter_name = "subscription_status"

    async def lookups(
        self, request: Any, model: Any, run_query: Any
    ) -> list[tuple[str, str]]:
        """Возвращает варианты фильтра."""
        return [
            ("all", "Все"),
            (SubscriptionStatus.ACTIVE.value, "Активные"),
            (SubscriptionStatus.PENDING.value, "Ожидающие"),
            (SubscriptionStatus.PAST_DUE.value, "Просроченные"),
            (SubscriptionStatus.CANCELED.value, "Отменённые"),
            (SubscriptionStatus.EXPIRED.value, "Истёкшие"),
        ]

    async def get_filtered_query(self, query: Any, value: str, model: Any) -> Any:
        """Применяет фильтр к запросу."""
        if value != "all":
            return query.where(Subscription.status == value)
        return query


class PaymentStatusFilter:
    """Фильтр платежей по статусу."""

    has_operator = False
    default_value: UnsetAny = "all"
    template = "sqladmin/filters/lookup_filter.html"
    title = "Статус платежа"
    parameter_name = "payment_status"

    async def lookups(
        self, request: Any, model: Any, run_query: Any
    ) -> list[tuple[str, str]]:
        """Возвращает варианты фильтра."""
        return [
            ("all", "Все"),
            (PaymentStatus.SUCCEEDED.value, "Успешные"),
            (PaymentStatus.PENDING.value, "Ожидающие"),
            (PaymentStatus.FAILED.value, "Неудачные"),
            (PaymentStatus.REFUNDED.value, "Возвраты"),
            (PaymentStatus.CANCELED.value, "Отменённые"),
        ]

    async def get_filtered_query(self, query: Any, value: str, model: Any) -> Any:
        """Применяет фильтр к запросу."""
        if value != "all":
            return query.where(Payment.status == value)
        return query


class PaymentProviderFilter:
    """Фильтр платежей по провайдеру."""

    has_operator = False
    default_value: UnsetAny = "all"
    template = "sqladmin/filters/lookup_filter.html"
    title = "Провайдер"
    parameter_name = "payment_provider"

    async def lookups(
        self, request: Any, model: Any, run_query: Any
    ) -> list[tuple[str, str]]:
        """Возвращает варианты фильтра."""
        return [
            ("all", "Все"),
            (PaymentProvider.TELEGRAM_STARS.value, "Telegram Stars"),
            (PaymentProvider.YOOKASSA.value, "ЮKassa"),
            (PaymentProvider.STRIPE.value, "Stripe"),
        ]

    async def get_filtered_query(self, query: Any, value: str, model: Any) -> Any:
        """Применяет фильтр к запросу."""
        if value != "all":
            return query.where(Payment.provider == value)
        return query


class UserAdmin(ModelView, model=User):
    """Представление пользователей в админке.

    Позволяет:
    - Просматривать список всех пользователей
    - Искать по telegram_id и username
    - Редактировать данные пользователя
    """

    # Название в меню админки
    name = "Пользователь"
    name_plural = "Пользователи"

    # Иконка в меню (Bootstrap Icons)
    icon = "fa-solid fa-users"

    # Человекочитаемые названия полей
    column_labels = {
        User.id: "ID",
        User.telegram_id: "Telegram ID",
        User.username: "Имя пользователя",
        User.first_name: "Имя",
        User.last_name: "Фамилия",
        User.language: "Язык",
        User.created_at: "Дата регистрации",
        User.source: "Источник (start=)",
        User.balance: "Баланс",
        User.is_blocked: "Заблокирован",
        User.terms_accepted_at: "Дата принятия оферты",
        User.accepted_legal_version: "Версия оферты",
    }

    # Колонки в списке пользователей
    column_list = [
        User.id,
        User.telegram_id,
        User.username,
        User.first_name,
        User.balance,
        User.language,
        User.created_at,
        User.source,
    ]

    # Колонки для поиска
    column_searchable_list = [
        User.telegram_id,
        User.username,
        User.first_name,
        User.last_name,
    ]

    # Подсказка в поле поиска
    column_search_placeholder = "Поиск по Telegram ID, имени пользователя или имени"

    # Сортировка по умолчанию (новые первыми)
    column_default_sort = [(User.created_at, True)]

    # Колонки для сортировки
    column_sortable_list = [
        User.id,
        User.telegram_id,
        User.created_at,
        User.language,
        User.balance,
    ]

    # Фильтры для поиска платящих/бесплатных пользователей и подписчиков
    column_filters = [
        HasPaymentsFilter(),
        HasActiveSubscriptionFilter(),
        BooleanFilter(User.is_blocked, "Заблокирован"),
    ]

    # Форматирование дат с учётом часового пояса
    # SQLAdmin передаёт в callback экземпляр модели (m) и имя атрибута (a)
    # Время конвертируется в часовой пояс из настроек (LOGGING__TIMEZONE)
    column_formatters = {
        User.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
        ),
        User.terms_accepted_at: lambda m, a: format_datetime(
            m.terms_accepted_at,
            ADMIN_TIMEZONE,
        ),
        User.is_blocked: lambda m, a: "Да" if m.is_blocked else "Нет",
        User.language: lambda m, a: {
            "ru": "Русский",
            "en": "English",
        }.get(m.language, m.language),
    }

    # Форматирование дат в детальном просмотре
    column_formatters_detail = {
        User.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
            fmt="%d.%m.%Y %H:%M:%S",
        ),
        User.terms_accepted_at: lambda m, a: format_datetime(
            m.terms_accepted_at,
            ADMIN_TIMEZONE,
            fmt="%d.%m.%Y %H:%M:%S",
        ),
        User.is_blocked: lambda m, a: "Да" if m.is_blocked else "Нет",
        User.language: lambda m, a: {
            "ru": "Русский",
            "en": "English",
        }.get(m.language, m.language),
    }

    # Поля в детальном просмотре
    column_details_list = [
        User.id,
        User.telegram_id,
        User.username,
        User.first_name,
        User.last_name,
        User.language,
        User.balance,
        User.is_blocked,
        User.source,
        User.created_at,
        User.terms_accepted_at,
        User.accepted_legal_version,
        # Связанные объекты
        "subscriptions",
        "payments",
    ]

    # Поля для редактирования
    # telegram_id нельзя менять — это идентификатор из Telegram
    form_columns = [
        User.username,
        User.first_name,
        User.last_name,
        User.language,
        User.source,
        User.balance,
        User.is_blocked,
    ]

    # Описания полей в форме редактирования
    form_args = {
        "source": {
            "description": (
                "Параметр start= из ссылки t.me/bot?start=VALUE "
                "при первом запуске бота. Используется для отслеживания "
                "рекламных кампаний и источников трафика. "
                "Например: instagram, youtube, friend123"
            ),
        },
    }

    # Сколько записей на странице
    page_size = 50
    page_size_options = [25, 50, 100, 200]

    # Разрешаем экспорт в CSV
    can_export = True
    export_types = ["csv"]


class SubscriptionAdmin(ModelView, model=Subscription):
    """Представление подписок в админке.

    Позволяет:
    - Просматривать список всех подписок
    - Отслеживать активные подписки и их статус
    - Анализировать использование токенов
    - Просматривать историю продлений
    - Выявлять проблемные подписки (PAST_DUE)
    """

    # Название в меню админки
    name = "Подписка"
    name_plural = "Подписки"

    # Иконка в меню (Bootstrap Icons)
    icon = "fa-solid fa-rotate"

    # Человекочитаемые названия полей
    column_labels = {
        Subscription.id: "ID",
        Subscription.user_id: "ID пользователя",
        Subscription.tariff_slug: "Тариф",
        Subscription.provider: "Провайдер",
        Subscription.status: "Статус",
        Subscription.tokens_per_period: "Токенов на период",
        Subscription.tokens_remaining: "Токенов осталось",
        Subscription.period_start: "Начало периода",
        Subscription.period_end: "Конец периода",
        Subscription.auto_renewal: "Автопродление",
        Subscription.cancel_at_period_end: "Отменена",
        Subscription.payment_method_id: "Метод оплаты",
        Subscription.original_payment_id: "ID первого платежа",
        Subscription.last_renewal_payment_id: "ID последнего платежа",
        Subscription.renewal_attempts: "Попыток продления",
        Subscription.last_renewal_attempt_at: "Последняя попытка",
        Subscription.metadata_json: "Метаданные",
        Subscription.created_at: "Дата создания",
        Subscription.updated_at: "Дата обновления",
    }

    # Колонки в списке подписок
    column_list = [
        Subscription.id,
        Subscription.user_id,
        Subscription.tariff_slug,
        Subscription.provider,
        Subscription.status,
        Subscription.tokens_remaining,
        Subscription.period_end,
        Subscription.auto_renewal,
        Subscription.created_at,
    ]

    # Колонки для поиска
    column_searchable_list = [
        Subscription.tariff_slug,
        Subscription.payment_method_id,
    ]

    # Подсказка в поле поиска
    column_search_placeholder = "Поиск по тарифу или методу оплаты"

    # Сортировка по умолчанию (новые первыми)
    column_default_sort = [(Subscription.created_at, True)]

    # Колонки для сортировки
    column_sortable_list = [
        Subscription.id,
        Subscription.user_id,
        Subscription.tariff_slug,
        Subscription.status,
        Subscription.tokens_remaining,
        Subscription.period_end,
        Subscription.created_at,
    ]

    # Фильтры по статусу подписки и автопродлению
    column_filters = [
        SubscriptionStatusFilter(),
        BooleanFilter(Subscription.auto_renewal, "Автопродление"),
        BooleanFilter(Subscription.cancel_at_period_end, "Отменена"),
    ]

    # Форматирование значений в списке
    column_formatters = {
        Subscription.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
        ),
        Subscription.updated_at: lambda m, a: (
            format_datetime(m.updated_at, ADMIN_TIMEZONE) if m.updated_at else "—"
        ),
        Subscription.period_start: lambda m, a: format_datetime(
            m.period_start,
            ADMIN_TIMEZONE,
        ),
        Subscription.period_end: lambda m, a: format_datetime(
            m.period_end,
            ADMIN_TIMEZONE,
        ),
        Subscription.last_renewal_attempt_at: lambda m, a: (
            format_datetime(m.last_renewal_attempt_at, ADMIN_TIMEZONE)
            if m.last_renewal_attempt_at
            else "—"
        ),
        Subscription.status: lambda m, a: SUBSCRIPTION_STATUS_LABELS.get(
            m.status, m.status
        ),
        Subscription.provider: lambda m, a: PAYMENT_PROVIDER_LABELS.get(
            PaymentProvider(m.provider), m.provider
        ),
        Subscription.auto_renewal: lambda m, a: "Да" if m.auto_renewal else "Нет",
        Subscription.cancel_at_period_end: lambda m, a: (
            "Да" if m.cancel_at_period_end else "Нет"
        ),
        Subscription.tokens_remaining: lambda m, a: (
            f"{m.tokens_remaining} / {m.tokens_per_period}"
        ),
        # Показываем username или имя пользователя вместо ID
        Subscription.user_id: lambda m, a: (
            f"@{m.user.username}"
            if m.user and m.user.username
            else (
                f"{m.user.first_name} {m.user.last_name}".strip()
                if m.user and m.user.first_name
                else str(m.user_id)
            )
        ),
    }

    # Форматирование в детальном просмотре
    column_formatters_detail = {
        Subscription.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
            fmt="%d.%m.%Y %H:%M:%S",
        ),
        Subscription.updated_at: lambda m, a: (
            format_datetime(
                m.updated_at,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.updated_at
            else "—"
        ),
        Subscription.period_start: lambda m, a: format_datetime(
            m.period_start,
            ADMIN_TIMEZONE,
            fmt="%d.%m.%Y %H:%M:%S",
        ),
        Subscription.period_end: lambda m, a: format_datetime(
            m.period_end,
            ADMIN_TIMEZONE,
            fmt="%d.%m.%Y %H:%M:%S",
        ),
        Subscription.last_renewal_attempt_at: lambda m, a: (
            format_datetime(
                m.last_renewal_attempt_at,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.last_renewal_attempt_at
            else "—"
        ),
        Subscription.status: lambda m, a: SUBSCRIPTION_STATUS_LABELS.get(
            m.status, m.status
        ),
        Subscription.provider: lambda m, a: PAYMENT_PROVIDER_LABELS.get(
            PaymentProvider(m.provider), m.provider
        ),
        Subscription.auto_renewal: lambda m, a: "Да" if m.auto_renewal else "Нет",
        Subscription.cancel_at_period_end: lambda m, a: (
            "Да" if m.cancel_at_period_end else "Нет"
        ),
        # Показываем username или имя пользователя вместо ID
        Subscription.user_id: lambda m, a: (
            f"@{m.user.username}"
            if m.user and m.user.username
            else (
                f"{m.user.first_name} {m.user.last_name}".strip()
                if m.user and m.user.first_name
                else str(m.user_id)
            )
        ),
    }

    # Поля в детальном просмотре
    column_details_list = [
        Subscription.id,
        Subscription.user_id,
        Subscription.tariff_slug,
        Subscription.provider,
        Subscription.status,
        Subscription.tokens_per_period,
        Subscription.tokens_remaining,
        Subscription.period_start,
        Subscription.period_end,
        Subscription.auto_renewal,
        Subscription.cancel_at_period_end,
        Subscription.payment_method_id,
        Subscription.original_payment_id,
        Subscription.last_renewal_payment_id,
        Subscription.renewal_attempts,
        Subscription.last_renewal_attempt_at,
        Subscription.created_at,
        Subscription.updated_at,
        Subscription.metadata_json,
    ]

    # Запрещаем создание и редактирование через админку
    # Подписки должны создаваться только через бота или webhook
    can_create = False
    can_edit = False
    can_delete = False

    # Сколько записей на странице
    page_size = 50
    page_size_options = [25, 50, 100, 200]

    # Разрешаем экспорт в CSV
    can_export = True
    export_types = ["csv"]


class GenerationAdmin(ModelView, model=Generation):
    """Представление истории генераций в админке.

    Позволяет:
    - Просматривать список всех генераций AI
    - Отслеживать статус генераций (pending, completed, failed)
    - Анализировать использование моделей
    - Выявлять проблемные генерации и ошибки
    - Мониторить время выполнения генераций
    """

    # Название в меню админки
    name = "Генерация"
    name_plural = "Генерации"

    # Иконка в меню (Bootstrap Icons)
    icon = "fa-solid fa-wand-magic-sparkles"

    # Человекочитаемые названия полей
    column_labels = {
        Generation.id: "ID",
        Generation.user_id: "ID пользователя",
        Generation.generation_type: "Тип генерации",
        Generation.model_key: "Модель",
        Generation.status: "Статус",
        Generation.tokens_charged: "Токенов списано",
        Generation.cost_rub: "Себестоимость (₽)",
        Generation.transaction_id: "ID транзакции",
        Generation.created_at: "Время запуска",
        Generation.completed_at: "Время завершения",
    }

    # Колонки в списке генераций
    column_list = [
        Generation.id,
        Generation.user_id,
        Generation.generation_type,
        Generation.model_key,
        Generation.status,
        Generation.tokens_charged,
        Generation.cost_rub,
        Generation.created_at,
    ]

    # Колонки для поиска
    column_searchable_list = [
        Generation.model_key,
        Generation.generation_type,
    ]

    # Подсказка в поле поиска
    column_search_placeholder = "Поиск по модели или типу генерации"

    # Сортировка по умолчанию (новые первыми)
    column_default_sort = [(Generation.created_at, True)]

    # Колонки для сортировки
    column_sortable_list = [
        Generation.id,
        Generation.user_id,
        Generation.generation_type,
        Generation.model_key,
        Generation.status,
        Generation.tokens_charged,
        Generation.cost_rub,
        Generation.created_at,
        Generation.completed_at,
    ]

    # Форматирование значений в списке
    column_formatters = {
        Generation.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
        ),
        Generation.completed_at: lambda m, a: (
            format_datetime(m.completed_at, ADMIN_TIMEZONE) if m.completed_at else "—"
        ),
        Generation.status: lambda m, a: GENERATION_STATUS_LABELS.get(
            GenerationDBStatus(m.status), m.status
        ),
        Generation.generation_type: lambda m, a: {
            "chat": "💬 Чат",
            "image": "🎨 Изображение",
            "image_edit": "✏️ Редактирование",
            "tts": "🔊 Озвучка",
            "stt": "🎤 Распознавание",
        }.get(m.generation_type, m.generation_type),
        # Форматирование себестоимости с 4 знаками после запятой и символом рубля
        Generation.cost_rub: lambda m, a: f"{m.cost_rub:.4f} ₽" if m.cost_rub else "—",
        # Показываем 0 токенов если не списано (биллинг отключён)
        Generation.tokens_charged: lambda m, a: str(m.tokens_charged),
        # Показываем username или имя пользователя вместо ID
        Generation.user_id: lambda m, a: (
            f"@{m.user.username}"
            if m.user and m.user.username
            else (
                f"{m.user.first_name} {m.user.last_name}".strip()
                if m.user and m.user.first_name
                else str(m.user_id)
            )
        ),
    }

    # Форматирование в детальном просмотре
    column_formatters_detail = {
        Generation.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
            fmt="%d.%m.%Y %H:%M:%S",
        ),
        Generation.completed_at: lambda m, a: (
            format_datetime(
                m.completed_at,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.completed_at
            else "—"
        ),
        Generation.status: lambda m, a: GENERATION_STATUS_LABELS.get(
            GenerationDBStatus(m.status), m.status
        ),
        Generation.generation_type: lambda m, a: {
            "chat": "💬 Чат",
            "image": "🎨 Изображение",
            "image_edit": "✏️ Редактирование",
            "tts": "🔊 Озвучка",
            "stt": "🎤 Распознавание",
        }.get(m.generation_type, m.generation_type),
        # Форматирование себестоимости с 4 знаками после запятой и символом рубля
        Generation.cost_rub: lambda m, a: f"{m.cost_rub:.4f} ₽" if m.cost_rub else "—",
        # Показываем 0 токенов если не списано (биллинг отключён)
        Generation.tokens_charged: lambda m, a: str(m.tokens_charged),
        # ID транзакции или прочерк если нет
        Generation.transaction_id: lambda m, a: (
            str(m.transaction_id) if m.transaction_id else "—"
        ),
        # Показываем username или имя пользователя вместо ID
        Generation.user_id: lambda m, a: (
            f"@{m.user.username}"
            if m.user and m.user.username
            else (
                f"{m.user.first_name} {m.user.last_name}".strip()
                if m.user and m.user.first_name
                else str(m.user_id)
            )
        ),
    }

    # Поля в детальном просмотре
    column_details_list = [
        Generation.id,
        Generation.user_id,
        Generation.generation_type,
        Generation.model_key,
        Generation.status,
        Generation.tokens_charged,
        Generation.cost_rub,
        Generation.transaction_id,
        Generation.created_at,
        Generation.completed_at,
    ]

    # Запрещаем создание и редактирование через админку
    # Генерации создаются только через бота
    can_create = False
    can_edit = False
    can_delete = False

    # Сколько записей на странице
    page_size = 50
    page_size_options = [25, 50, 100, 200]

    # Разрешаем экспорт в CSV
    can_export = True
    export_types = ["csv"]


# =============================================================================
# ФОРМАТТЕРЫ ДЛЯ РАССЫЛОК
# =============================================================================


def format_broadcast_status(model: Broadcast, attr: Any) -> Markup:
    """Форматирует статус рассылки как цветной бейдж.

    Args:
        model: Объект рассылки.
        attr: Название атрибута (не используется).

    Returns:
        HTML с цветным бейджем статуса.
    """
    status = BroadcastStatus(model.status)
    label = BROADCAST_STATUS_LABELS.get(status, model.status)
    color = BROADCAST_STATUS_COLORS.get(status, "secondary")
    return Markup(f'<span class="badge bg-{color}">{label}</span>')


def format_broadcast_progress(model: Broadcast, attr: Any) -> Markup:
    """Форматирует прогресс рассылки как прогресс-бар.

    Args:
        model: Объект рассылки.
        attr: Название атрибута (не используется).

    Returns:
        HTML с прогресс-баром или прочерк если рассылка не начата.
    """
    if model.total_recipients == 0:
        return Markup("—")

    percent = model.progress_percent
    processed = model.sent_count + model.failed_count

    # Цвет в зависимости от прогресса
    if percent >= 100:
        color = "success"
    elif percent >= 50:
        color = "info"
    else:
        color = "primary"

    return Markup(
        f'<div class="progress" style="min-width: 80px; height: 20px;">'
        f'<div class="progress-bar bg-{color}" style="width: {percent}%;" '
        f'title="{processed}/{model.total_recipients}">'
        f"{percent:.0f}%</div></div>"
    )


def format_message_preview(model: Broadcast, attr: Any) -> Markup:
    """Форматирует превью текста сообщения с обрезкой.

    Args:
        model: Объект рассылки.
        attr: Название атрибута (не используется).

    Returns:
        HTML с обрезанным текстом и tooltip с полным текстом.
    """
    text = model.message_text or ""
    # Экранируем HTML для безопасности
    import html

    escaped_text = html.escape(text)
    escaped_preview = html.escape(text[:80] + "..." if len(text) > 80 else text)
    return Markup(f'<span title="{escaped_text}">{escaped_preview}</span>')


def _build_flash_redirect(
    base_url: str,
    msg: str,
    msg_type: str = "info",
) -> str:
    """Построить URL с flash-сообщением.

    Args:
        base_url: Базовый URL для редиректа.
        msg: Сообщение для отображения.
        msg_type: Тип сообщения (success, danger, warning, info).

    Returns:
        URL с query-параметрами msg и msg_type.
    """
    # Удаляем существующие параметры msg из URL
    if "?" in base_url:
        base_url = base_url.split("?")[0]
    params = urlencode({"msg": msg, "msg_type": msg_type})
    return f"{base_url}?{params}"


def _get_error_detail(response_error: object) -> str:
    """Извлечь сообщение об ошибке из HTTP ответа.

    Ожидает объект с атрибутом response (httpx.HTTPStatusError).

    Args:
        response_error: Ошибка HTTP от httpx.

    Returns:
        Сообщение об ошибке.
    """
    import json

    try:
        response = getattr(response_error, "response", None)
        if response is None:
            return str(response_error)
        data = response.json()
        detail = data.get("detail", str(response_error))
        return str(detail)
    except (json.JSONDecodeError, ValueError, KeyError, AttributeError):
        response = getattr(response_error, "response", None)
        if response is not None:
            return getattr(response, "text", None) or str(response_error)
        return str(response_error)


class BroadcastAdmin(ModelView, model=Broadcast):
    """Представление рассылок в админке.

    Позволяет:
    - Просматривать список всех рассылок
    - Создавать новые рассылки
    - Редактировать черновики
    - Просматривать статистику отправки
    """

    # Название в меню админки
    name = "Рассылка"
    name_plural = "Рассылки"

    # Иконка в меню (Bootstrap Icons)
    icon = "fa-solid fa-paper-plane"

    # Кастомные шаблоны с Telegram-редактором для текста сообщения
    edit_template = "sqladmin/broadcast_edit.html"
    create_template = "sqladmin/broadcast_create.html"

    # Человекочитаемые названия полей
    column_labels = {
        Broadcast.id: "ID",
        Broadcast.name: "Название",
        Broadcast.message_text: "Текст сообщения",
        Broadcast.parse_mode: "Форматирование",
        Broadcast.status: "Статус",
        Broadcast.created_by_id: "Создал",
        Broadcast.created_at: "Дата создания",
        Broadcast.started_at: "Дата запуска",
        Broadcast.completed_at: "Дата завершения",
        Broadcast.filter_language: "Фильтр: язык",
        Broadcast.filter_has_payments: "Фильтр: оплаты",
        Broadcast.filter_source: "Фильтр: источник",
        Broadcast.filter_registered_after: "Фильтр: зарег. после",
        Broadcast.filter_registered_before: "Фильтр: зарег. до",
        Broadcast.filter_exclude_blocked: "Исключить заблокированных",
        Broadcast.total_recipients: "Всего получателей",
        Broadcast.sent_count: "Отправлено",
        Broadcast.failed_count: "Ошибок",
        Broadcast.last_processed_user_id: "Последний user_id",
        Broadcast.error_message: "Ошибка",
        "progress": "Прогресс",
    }

    # Колонки в списке рассылок
    column_list = [
        Broadcast.id,
        Broadcast.name,
        Broadcast.message_text,
        Broadcast.status,
        "progress",  # Виртуальная колонка с прогресс-баром
        Broadcast.sent_count,
        Broadcast.failed_count,
        Broadcast.created_at,
        Broadcast.started_at,
    ]

    # Колонки для поиска
    column_searchable_list = [
        Broadcast.name,
        Broadcast.message_text,
    ]

    # Подсказка в поле поиска
    column_search_placeholder = "Поиск по названию или тексту сообщения"

    # Сортировка по умолчанию (новые первыми)
    column_default_sort = [(Broadcast.created_at, True)]

    # Колонки для сортировки
    column_sortable_list = [
        Broadcast.id,
        Broadcast.name,
        Broadcast.status,
        Broadcast.created_at,
        Broadcast.started_at,
        Broadcast.completed_at,
        Broadcast.total_recipients,
        Broadcast.sent_count,
    ]

    # Форматирование значений в списке
    column_formatters = {
        Broadcast.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
        ),
        Broadcast.started_at: lambda m, a: (
            format_datetime(
                m.started_at,
                ADMIN_TIMEZONE,
            )
            if m.started_at
            else "—"
        ),
        Broadcast.completed_at: lambda m, a: (
            format_datetime(
                m.completed_at,
                ADMIN_TIMEZONE,
            )
            if m.completed_at
            else "—"
        ),
        Broadcast.status: format_broadcast_status,
        Broadcast.message_text: format_message_preview,
        "progress": format_broadcast_progress,
        Broadcast.parse_mode: lambda m, a: PARSE_MODE_LABELS.get(
            m.parse_mode, m.parse_mode
        ),
        Broadcast.filter_has_payments: lambda m, a: (
            "Да"
            if m.filter_has_payments is True
            else "Нет"
            if m.filter_has_payments is False
            else "—"
        ),
        Broadcast.filter_exclude_blocked: lambda m, a: (
            "Да" if m.filter_exclude_blocked else "Нет"
        ),
    }

    # Форматирование в детальном просмотре
    column_formatters_detail = {
        Broadcast.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
            fmt="%d.%m.%Y %H:%M:%S",
        ),
        Broadcast.started_at: lambda m, a: (
            format_datetime(
                m.started_at,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.started_at
            else "—"
        ),
        Broadcast.completed_at: lambda m, a: (
            format_datetime(
                m.completed_at,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.completed_at
            else "—"
        ),
        Broadcast.filter_registered_after: lambda m, a: (
            format_datetime(
                m.filter_registered_after,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.filter_registered_after
            else "—"
        ),
        Broadcast.filter_registered_before: lambda m, a: (
            format_datetime(
                m.filter_registered_before,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.filter_registered_before
            else "—"
        ),
        Broadcast.status: format_broadcast_status,
        Broadcast.parse_mode: lambda m, a: PARSE_MODE_LABELS.get(
            m.parse_mode, m.parse_mode
        ),
        Broadcast.filter_has_payments: lambda m, a: (
            "Да"
            if m.filter_has_payments is True
            else "Нет"
            if m.filter_has_payments is False
            else "—"
        ),
        Broadcast.filter_exclude_blocked: lambda m, a: (
            "Да" if m.filter_exclude_blocked else "Нет"
        ),
        "progress": format_broadcast_progress,
    }

    # Поля в детальном просмотре
    column_details_list = [
        Broadcast.id,
        Broadcast.name,
        Broadcast.status,
        "progress",  # Прогресс-бар в детальном просмотре
        Broadcast.message_text,
        Broadcast.parse_mode,
        Broadcast.created_at,
        Broadcast.started_at,
        Broadcast.completed_at,
        Broadcast.total_recipients,
        Broadcast.sent_count,
        Broadcast.failed_count,
        Broadcast.filter_language,
        Broadcast.filter_has_payments,
        Broadcast.filter_source,
        Broadcast.filter_registered_after,
        Broadcast.filter_registered_before,
        Broadcast.filter_exclude_blocked,
        Broadcast.error_message,
    ]

    # Поля для создания/редактирования
    form_columns = [
        Broadcast.name,
        Broadcast.message_text,
        Broadcast.parse_mode,
        Broadcast.filter_language,
        Broadcast.filter_has_payments,
        Broadcast.filter_source,
        Broadcast.filter_registered_after,
        Broadcast.filter_registered_before,
        Broadcast.filter_exclude_blocked,
    ]

    # Описания полей в форме
    form_args = {
        "name": {
            "description": (
                "Название для идентификации рассылки (не отправляется пользователям)"
            ),
        },
        "message_text": {
            "description": (
                "Текст сообщения. HTML: <b>жирный</b>, <i>курсив</i>, "
                "<a href='url'>ссылка</a>"
            ),
        },
        "parse_mode": {
            "description": "Режим форматирования текста. Рекомендуется HTML.",
        },
        "filter_language": {
            "description": (
                "Только пользователи с этим языком интерфейса (ru, en). Пусто = все."
            ),
        },
        "filter_has_payments": {
            "description": (
                "True = только платившие, False = только бесплатные, пусто = все."
            ),
        },
        "filter_source": {
            "description": (
                "Только пользователи из этого источника (start param). Пусто = все."
            ),
        },
        "filter_registered_after": {
            "description": "Только зарегистрированные после этой даты.",
        },
        "filter_registered_before": {
            "description": "Только зарегистрированные до этой даты.",
        },
        "filter_exclude_blocked": {
            "description": "Исключить заблокированных пользователей из рассылки.",
        },
    }

    # Сколько записей на странице
    page_size = 25
    page_size_options = [10, 25, 50, 100]

    # Разрешаем создание и редактирование
    can_create = True
    can_edit = True
    can_delete = True

    # Разрешаем экспорт в CSV
    can_export = True
    export_types = ["csv"]

    # =========================================================================
    # КНОПКИ УПРАВЛЕНИЯ РАССЫЛКОЙ
    # =========================================================================

    @action(
        name="start_broadcast",
        label="Запустить",
        confirmation_message="Вы уверены, что хотите запустить рассылку? "
        "Сообщения будут отправлены всем получателям.",
        add_in_detail=True,
        add_in_list=True,
    )
    async def action_start_broadcast(self, request: Request) -> RedirectResponse:
        """Запустить выбранные рассылки."""
        import httpx

        from src.utils.logging import get_logger

        logger = get_logger(__name__)

        pks_param = request.query_params.get("pks", "")
        pks: list[str] = [pk.strip() for pk in pks_param.split(",") if pk.strip()]
        base_url = str(request.base_url).rstrip("/")
        cookies = dict(request.cookies)

        success_count = 0
        error_msg = ""
        total_recipients = 0

        async with httpx.AsyncClient(cookies=cookies, timeout=30.0) as client:
            for pk in pks:
                try:
                    response = await client.post(
                        f"{base_url}/api/admin/broadcasts/{pk}/start",
                    )
                    response.raise_for_status()
                    data = response.json()
                    success_count += 1
                    total_recipients += data.get("total_recipients", 0)
                except httpx.HTTPStatusError as e:
                    error_msg = _get_error_detail(e)
                    logger.warning("Ошибка запуска рассылки %s: %s", pk, error_msg)
                except httpx.RequestError as e:
                    error_msg = str(e)
                    logger.error("Сетевая ошибка при запуске рассылки %s: %s", pk, e)

        # Формируем сообщение
        referer = request.headers.get("Referer", "")
        list_url = str(request.url_for("admin:list", identity=self.identity))
        redirect_url = referer or list_url

        if success_count > 0:
            msg = f"Рассылка запущена. Получателей: {total_recipients}"
            redirect_url = _build_flash_redirect(redirect_url, msg, "success")
        elif error_msg:
            msg = f"Ошибка: {error_msg}"
            redirect_url = _build_flash_redirect(redirect_url, msg, "danger")

        return RedirectResponse(redirect_url, status_code=302)

    @action(
        name="pause_broadcast",
        label="Приостановить",
        confirmation_message="Приостановить рассылку? "
        "Её можно будет возобновить позже.",
        add_in_detail=True,
        add_in_list=True,
    )
    async def action_pause_broadcast(self, request: Request) -> RedirectResponse:
        """Приостановить выбранные рассылки."""
        import httpx

        from src.utils.logging import get_logger

        logger = get_logger(__name__)

        pks_param = request.query_params.get("pks", "")
        pks: list[str] = [pk.strip() for pk in pks_param.split(",") if pk.strip()]
        base_url = str(request.base_url).rstrip("/")
        cookies = dict(request.cookies)

        success_count = 0
        error_msg = ""

        async with httpx.AsyncClient(cookies=cookies, timeout=30.0) as client:
            for pk in pks:
                try:
                    response = await client.post(
                        f"{base_url}/api/admin/broadcasts/{pk}/pause",
                    )
                    response.raise_for_status()
                    success_count += 1
                except httpx.HTTPStatusError as e:
                    error_msg = _get_error_detail(e)
                    logger.warning("Ошибка приостановки рассылки %s: %s", pk, error_msg)
                except httpx.RequestError as e:
                    error_msg = str(e)
                    logger.error(
                        "Сетевая ошибка при приостановке рассылки %s: %s", pk, e
                    )

        referer = request.headers.get("Referer", "")
        list_url = str(request.url_for("admin:list", identity=self.identity))
        redirect_url = referer or list_url

        if success_count > 0:
            redirect_url = _build_flash_redirect(
                redirect_url, "Рассылка приостановлена", "success"
            )
        elif error_msg:
            redirect_url = _build_flash_redirect(
                redirect_url, f"Ошибка: {error_msg}", "danger"
            )

        return RedirectResponse(redirect_url, status_code=302)

    @action(
        name="cancel_broadcast",
        label="Отменить",
        confirmation_message="Отменить рассылку? Это действие нельзя отменить!",
        add_in_detail=True,
        add_in_list=True,
    )
    async def action_cancel_broadcast(self, request: Request) -> RedirectResponse:
        """Отменить выбранные рассылки."""
        import httpx

        from src.utils.logging import get_logger

        logger = get_logger(__name__)

        pks_param = request.query_params.get("pks", "")
        pks: list[str] = [pk.strip() for pk in pks_param.split(",") if pk.strip()]
        base_url = str(request.base_url).rstrip("/")
        cookies = dict(request.cookies)

        success_count = 0
        error_msg = ""

        async with httpx.AsyncClient(cookies=cookies, timeout=30.0) as client:
            for pk in pks:
                try:
                    response = await client.post(
                        f"{base_url}/api/admin/broadcasts/{pk}/cancel",
                    )
                    response.raise_for_status()
                    success_count += 1
                except httpx.HTTPStatusError as e:
                    error_msg = _get_error_detail(e)
                    logger.warning("Ошибка отмены рассылки %s: %s", pk, error_msg)
                except httpx.RequestError as e:
                    error_msg = str(e)
                    logger.error("Сетевая ошибка при отмене рассылки %s: %s", pk, e)

        referer = request.headers.get("Referer", "")
        list_url = str(request.url_for("admin:list", identity=self.identity))
        redirect_url = referer or list_url

        if success_count > 0:
            redirect_url = _build_flash_redirect(
                redirect_url, "Рассылка отменена", "warning"
            )
        elif error_msg:
            redirect_url = _build_flash_redirect(
                redirect_url, f"Ошибка: {error_msg}", "danger"
            )

        return RedirectResponse(redirect_url, status_code=302)

    @action(
        name="test_broadcast",
        label="Тест",
        confirmation_message="Отправить тестовое сообщение админу?",
        add_in_detail=True,
        add_in_list=True,
    )
    async def action_test_broadcast(self, request: Request) -> RedirectResponse:
        """Отправить тестовое сообщение админу."""
        import httpx

        from src.utils.logging import get_logger

        logger = get_logger(__name__)

        pks_param = request.query_params.get("pks", "")
        pks: list[str] = [pk.strip() for pk in pks_param.split(",") if pk.strip()]
        base_url = str(request.base_url).rstrip("/")
        cookies = dict(request.cookies)

        success_count = 0
        error_msg = ""

        async with httpx.AsyncClient(cookies=cookies, timeout=30.0) as client:
            for pk in pks:
                try:
                    response = await client.post(
                        f"{base_url}/api/admin/broadcasts/{pk}/test",
                    )
                    response.raise_for_status()
                    success_count += 1
                except httpx.HTTPStatusError as e:
                    try:
                        error_msg = e.response.json().get("detail", str(e))
                    except (ValueError, KeyError):
                        error_msg = e.response.text or str(e)
                    logger.warning(
                        "Ошибка отправки тестового сообщения рассылки %s: %s",
                        pk,
                        error_msg,
                    )
                except httpx.RequestError as e:
                    error_msg = str(e)
                    logger.error(
                        "Сетевая ошибка при отправке теста рассылки %s: %s",
                        pk,
                        e,
                    )

        referer = request.headers.get("Referer", "")
        list_url = str(request.url_for("admin:list", identity=self.identity))
        redirect_url = referer or list_url

        if success_count > 0:
            redirect_url = _build_flash_redirect(
                redirect_url, "Тестовое сообщение отправлено", "success"
            )
        elif error_msg:
            redirect_url = _build_flash_redirect(
                redirect_url, f"Ошибка: {error_msg}", "danger"
            )

        return RedirectResponse(redirect_url, status_code=302)

    @action(
        name="count_recipients",
        label="Подсчитать",
        confirmation_message=None,
        add_in_detail=True,
        add_in_list=False,
    )
    async def action_count_recipients(self, request: Request) -> RedirectResponse:
        """Подсчитать количество получателей рассылки."""
        import httpx

        from src.utils.logging import get_logger

        logger = get_logger(__name__)

        pk = request.query_params.get("pks", "")
        base_url = str(request.base_url).rstrip("/")
        cookies = dict(request.cookies)

        count = 0
        filters_desc = ""
        error_msg = ""

        async with httpx.AsyncClient(cookies=cookies, timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{base_url}/api/admin/broadcasts/{pk}/count",
                )
                response.raise_for_status()
                data = response.json()
                count = data.get("count", 0)
                filters_desc = data.get("filters_description", "")
            except httpx.HTTPStatusError as e:
                error_msg = _get_error_detail(e)
                logger.warning(
                    "Ошибка подсчёта получателей рассылки %s: %s", pk, error_msg
                )
            except httpx.RequestError as e:
                error_msg = str(e)
                logger.error("Сетевая ошибка при подсчёте получателей %s: %s", pk, e)

        referer = request.headers.get("Referer", "")
        list_url = str(request.url_for("admin:list", identity=self.identity))
        redirect_url = referer or list_url

        if count > 0 or not error_msg:
            msg = f"Получателей: {count}"
            if filters_desc:
                msg += f" ({filters_desc})"
            redirect_url = _build_flash_redirect(redirect_url, msg, "info")
        else:
            redirect_url = _build_flash_redirect(
                redirect_url, f"Ошибка: {error_msg}", "danger"
            )

        return RedirectResponse(redirect_url, status_code=302)


class ReferralAdmin(ModelView, model=Referral):
    """Представление рефералов в админке.

    Позволяет:
    - Просматривать список всех рефералов
    - Отслеживать статус выплаты бонусов
    - Анализировать реферальную активность
    - Выявлять злоупотребления
    """

    # Название в меню админки
    name = "Реферал"
    name_plural = "Рефералы"

    # Иконка в меню (Bootstrap Icons)
    icon = "fa-solid fa-users-between-lines"

    # Человекочитаемые названия полей
    column_labels = {
        Referral.id: "ID",
        Referral.inviter_id: "ID пригласившего",
        Referral.invitee_id: "ID приглашённого",
        Referral.inviter_bonus_amount: "Бонус пригласившему",
        Referral.invitee_bonus_amount: "Бонус приглашённому",
        Referral.bonus_paid_at: "Дата выплаты",
        Referral.created_at: "Дата создания",
    }

    # Колонки в списке рефералов
    column_list = [
        Referral.id,
        Referral.inviter_id,
        Referral.invitee_id,
        Referral.inviter_bonus_amount,
        Referral.invitee_bonus_amount,
        Referral.bonus_paid_at,
        Referral.created_at,
    ]

    # Сортировка по умолчанию (новые первыми)
    column_default_sort = [(Referral.created_at, True)]

    # Колонки для сортировки
    column_sortable_list = [
        Referral.id,
        Referral.created_at,
        Referral.inviter_id,
        Referral.bonus_paid_at,
    ]

    # Форматирование дат с учётом часового пояса
    column_formatters = {
        Referral.bonus_paid_at: lambda m, a: (
            format_datetime(m.bonus_paid_at, ADMIN_TIMEZONE)
            if m.bonus_paid_at
            else "Не выплачен"
        ),
        Referral.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
        ),
        # Показываем username или имя пользователя вместо ID
        Referral.inviter_id: lambda m, a: (
            f"@{m.inviter.username}"
            if m.inviter and m.inviter.username
            else (
                f"{m.inviter.first_name} {m.inviter.last_name}".strip()
                if m.inviter and m.inviter.first_name
                else str(m.inviter_id)
            )
        ),
        Referral.invitee_id: lambda m, a: (
            f"@{m.invitee.username}"
            if m.invitee and m.invitee.username
            else (
                f"{m.invitee.first_name} {m.invitee.last_name}".strip()
                if m.invitee and m.invitee.first_name
                else str(m.invitee_id)
            )
        ),
    }

    # Форматирование в детальном просмотре
    column_formatters_detail = {
        Referral.bonus_paid_at: lambda m, a: (
            format_datetime(
                m.bonus_paid_at,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.bonus_paid_at
            else "Не выплачен"
        ),
        Referral.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
            fmt="%d.%m.%Y %H:%M:%S",
        ),
        # Показываем username или имя пользователя вместо ID
        Referral.inviter_id: lambda m, a: (
            f"@{m.inviter.username}"
            if m.inviter and m.inviter.username
            else (
                f"{m.inviter.first_name} {m.inviter.last_name}".strip()
                if m.inviter and m.inviter.first_name
                else str(m.inviter_id)
            )
        ),
        Referral.invitee_id: lambda m, a: (
            f"@{m.invitee.username}"
            if m.invitee and m.invitee.username
            else (
                f"{m.invitee.first_name} {m.invitee.last_name}".strip()
                if m.invitee and m.invitee.first_name
                else str(m.invitee_id)
            )
        ),
    }

    # Поля в детальном просмотре
    column_details_list = [
        Referral.id,
        Referral.inviter_id,
        Referral.invitee_id,
        Referral.inviter_bonus_amount,
        Referral.invitee_bonus_amount,
        Referral.bonus_paid_at,
        Referral.created_at,
    ]

    # Запрещаем создание и редактирование через админку
    # Рефералы должны создаваться только через бота
    can_create = False
    can_edit = False
    can_delete = False

    # Сколько записей на странице
    page_size = 50
    page_size_options = [25, 50, 100, 200]

    # Разрешаем экспорт в CSV
    can_export = True
    export_types = ["csv"]


class PaymentAdmin(ModelView, model=Payment):
    """Представление платежей в админке.

    Позволяет:
    - Просматривать список всех платежей
    - Отслеживать статус платежей
    - Анализировать платёжную активность
    - Экспортировать данные для отчётности
    """

    # Название в меню админки
    name = "Платёж"
    name_plural = "Платежи"

    # Иконка в меню (Bootstrap Icons)
    icon = "fa-solid fa-credit-card"

    # Человекочитаемые названия полей
    column_labels = {
        Payment.id: "ID",
        Payment.user_id: "ID пользователя",
        Payment.provider: "Провайдер",
        Payment.provider_payment_id: "ID провайдера",
        Payment.status: "Статус",
        Payment.amount: "Сумма",
        Payment.currency: "Валюта",
        Payment.tariff_slug: "Тариф",
        Payment.tokens_amount: "Токенов",
        Payment.description: "Описание",
        Payment.payment_method_id: "Метод оплаты",
        Payment.metadata_json: "Метаданные",
        Payment.is_recurring: "Рекуррентный",
        Payment.created_at: "Дата создания",
        Payment.updated_at: "Дата обновления",
        Payment.completed_at: "Дата завершения",
    }

    # Колонки в списке платежей
    column_list = [
        Payment.id,
        Payment.user_id,
        Payment.provider,
        Payment.status,
        Payment.amount,
        Payment.currency,
        Payment.tariff_slug,
        Payment.tokens_amount,
        Payment.created_at,
        Payment.completed_at,
    ]

    # Колонки для поиска
    column_searchable_list = [
        Payment.provider_payment_id,
        Payment.tariff_slug,
        Payment.description,
    ]

    # Подсказка в поле поиска
    column_search_placeholder = "Поиск по ID провайдера, тарифу или описанию"

    # Сортировка по умолчанию (новые первыми)
    column_default_sort = [(Payment.created_at, True)]

    # Колонки для сортировки
    column_sortable_list = [
        Payment.id,
        Payment.user_id,
        Payment.provider,
        Payment.status,
        Payment.amount,
        Payment.created_at,
        Payment.completed_at,
    ]

    # Фильтры по статусу и провайдеру
    column_filters = [
        PaymentStatusFilter(),
        PaymentProviderFilter(),
        BooleanFilter(Payment.is_recurring, "Рекуррентный"),
    ]

    # Форматирование значений в списке
    column_formatters = {
        Payment.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
        ),
        Payment.updated_at: lambda m, a: (
            format_datetime(m.updated_at, ADMIN_TIMEZONE) if m.updated_at else "—"
        ),
        Payment.completed_at: lambda m, a: (
            format_datetime(m.completed_at, ADMIN_TIMEZONE) if m.completed_at else "—"
        ),
        Payment.status: lambda m, a: PAYMENT_STATUS_LABELS.get(
            PaymentStatus(m.status), m.status
        ),
        Payment.provider: lambda m, a: PAYMENT_PROVIDER_LABELS.get(
            PaymentProvider(m.provider), m.provider
        ),
        Payment.amount: lambda m, a: f"{m.amount} {m.currency}",
        Payment.is_recurring: lambda m, a: "Да" if m.is_recurring else "Нет",
        # Показываем username или имя пользователя вместо ID
        Payment.user_id: lambda m, a: (
            f"@{m.user.username}"
            if m.user and m.user.username
            else (
                f"{m.user.first_name} {m.user.last_name}".strip()
                if m.user and m.user.first_name
                else str(m.user_id)
            )
        ),
    }

    # Форматирование в детальном просмотре
    column_formatters_detail = {
        Payment.created_at: lambda m, a: format_datetime(
            m.created_at,
            ADMIN_TIMEZONE,
            fmt="%d.%m.%Y %H:%M:%S",
        ),
        Payment.updated_at: lambda m, a: (
            format_datetime(
                m.updated_at,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.updated_at
            else "—"
        ),
        Payment.completed_at: lambda m, a: (
            format_datetime(
                m.completed_at,
                ADMIN_TIMEZONE,
                fmt="%d.%m.%Y %H:%M:%S",
            )
            if m.completed_at
            else "—"
        ),
        Payment.status: lambda m, a: PAYMENT_STATUS_LABELS.get(
            PaymentStatus(m.status), m.status
        ),
        Payment.provider: lambda m, a: PAYMENT_PROVIDER_LABELS.get(
            PaymentProvider(m.provider), m.provider
        ),
        Payment.amount: lambda m, a: f"{m.amount} {m.currency}",
        Payment.is_recurring: lambda m, a: "Да" if m.is_recurring else "Нет",
        # Показываем username или имя пользователя вместо ID
        Payment.user_id: lambda m, a: (
            f"@{m.user.username}"
            if m.user and m.user.username
            else (
                f"{m.user.first_name} {m.user.last_name}".strip()
                if m.user and m.user.first_name
                else str(m.user_id)
            )
        ),
    }

    # Поля в детальном просмотре
    column_details_list = [
        Payment.id,
        Payment.user_id,
        Payment.provider,
        Payment.provider_payment_id,
        Payment.status,
        Payment.amount,
        Payment.currency,
        Payment.tariff_slug,
        Payment.tokens_amount,
        Payment.description,
        Payment.payment_method_id,
        Payment.is_recurring,
        Payment.created_at,
        Payment.updated_at,
        Payment.completed_at,
        Payment.metadata_json,
    ]

    # Запрещаем создание и редактирование через админку
    # Платежи должны создаваться только через бота
    can_create = False
    can_edit = False
    can_delete = False

    # Сколько записей на странице
    page_size = 50
    page_size_options = [25, 50, 100, 200]

    # Разрешаем экспорт в CSV
    can_export = True
    export_types = ["csv"]
