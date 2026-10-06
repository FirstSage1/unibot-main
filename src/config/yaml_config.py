"""Загрузчик YAML-конфигурации.

Этот модуль загружает и валидирует config.yaml — файл с настройками,
которые можно менять без изменения кода.

Содержимое config.yaml:
- Конфигурация AI-моделей (провайдеры, цены, параметры)
- Таймауты генерации
- Лимиты и защита от спама
- Конфигурация команд бота
- Настройки биллинга
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from src.providers.ai.base import GenerationType


class CostConfig(BaseModel):
    """Себестоимость модели для расчёта маржинальности.

    Все цены хранятся в рублях для упрощения расчётов и бизнес-аналитики.
    При изменении курса валют цены можно обновить в config.yaml.

    Для текстовых моделей (chat):
        - input_tokens_rub_per_1k: Стоимость входящих токенов за 1000 штук в рублях
        - output_tokens_rub_per_1k: Стоимость исходящих токенов за 1000 штук в рублях

    Для остальных моделей (image, tts, stt):
        - per_request_rub: Фиксированная стоимость за один запрос в рублях

    Эти данные используются для отслеживания реальной себестоимости генераций
    и расчёта маржинальности бизнеса.

    Пример использования:
        model_config = yaml_config.get_model("gpt-4o")
        usage = {"prompt_tokens": 100, "completion_tokens": 200}
        cost = model_config.cost.calculate(usage)
        # cost = Decimal("0.0450")  # себестоимость в рублях
    """

    # Для chat-моделей: стоимость токенов в рублях
    input_tokens_rub_per_1k: float | None = Field(
        default=None,
        ge=0,
        description="Себестоимость 1000 входящих токенов в рублях",
    )
    output_tokens_rub_per_1k: float | None = Field(
        default=None,
        ge=0,
        description="Себестоимость 1000 исходящих токенов в рублях",
    )

    # Для image/tts/stt моделей: фиксированная цена в рублях
    per_request_rub: float | None = Field(
        default=None,
        ge=0,
        description="Фиксированная себестоимость за один запрос в рублях",
    )

    def calculate(self, usage: dict[str, int] | None = None) -> Decimal:
        """Рассчитать себестоимость генерации в рублях.

        Использует один из двух методов расчёта:
        1. Фиксированная цена (per_request_rub) — для image/tts/stt моделей
        2. По токенам (input + output) — для chat-моделей

        Приоритет: per_request_rub > расчёт по токенам.
        Если модель имеет per_request_rub — токены игнорируются.

        Args:
            usage: Словарь с количеством использованных токенов.
                Ожидаемые ключи:
                - prompt_tokens: количество входящих токенов
                - completion_tokens: количество исходящих токенов
                - total_tokens: общее количество (опционально)
                Если None — возвращается per_request_rub или Decimal(0).

        Returns:
            Себестоимость в рублях как Decimal для точных финансовых расчётов.
            Decimal(0) если конфигурация стоимости не настроена.

        Example:
            # Фиксированная стоимость (image-модель)
            cost = CostConfig(per_request_rub=0.81)
            result = cost.calculate()  # Decimal("0.81")

            # Расчёт по токенам (chat-модель)
            cost = CostConfig(
                input_tokens_rub_per_1k=0.50,
                output_tokens_rub_per_1k=1.50
            )
            usage = {"prompt_tokens": 100, "completion_tokens": 200}
            result = cost.calculate(usage)
            # (100 / 1000 * 0.50) + (200 / 1000 * 1.50) = 0.05 + 0.30 = 0.35
        """
        from decimal import ROUND_HALF_UP

        # Приоритет 1: Фиксированная стоимость за запрос
        if self.per_request_rub is not None:
            return Decimal(str(self.per_request_rub)).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )

        # Приоритет 2: Расчёт по токенам (требует usage)
        if usage is None:
            return Decimal(0)

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        input_cost = Decimal(0)
        output_cost = Decimal(0)

        if self.input_tokens_rub_per_1k is not None and prompt_tokens > 0:
            # Себестоимость = (токены / 1000) * цена_за_1000
            input_cost = (
                Decimal(prompt_tokens)
                / Decimal(1000)
                * Decimal(str(self.input_tokens_rub_per_1k))
            )

        if self.output_tokens_rub_per_1k is not None and completion_tokens > 0:
            output_cost = (
                Decimal(completion_tokens)
                / Decimal(1000)
                * Decimal(str(self.output_tokens_rub_per_1k))
            )

        total_cost = input_cost + output_cost
        return total_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class ModelConfig(BaseModel):
    """Конфигурация одной AI-модели.

    Каждая модель привязана к одному провайдеру.
    Провайдер определяет, какой API будет использоваться.

    Пример в config.yaml:
        gpt-4o:
          provider: openrouter
          model_id: openai/gpt-4o
          price_tokens: 15
          display_name: "GPT-4o"
          description: "Самая умная модель OpenAI"
          cost:
            input_tokens_rub_per_1k: 0.50
            output_tokens_rub_per_1k: 1.50
          params:
            max_tokens: 4096
            temperature: 0.7

        dall-e-3:
          provider: openrouter
          model_id: openai/dall-e-3
          price_tokens: 50
          display_name: "DALL-E 3"
          cost:
            per_request_rub: 4.00
          params:
            size: "1024x1024"
            quality: standard
    """

    # Обязательные поля
    provider: str = Field(description="Провайдер API (openrouter, routerai, и др.)")
    model_id: str = Field(
        description="ID модели на стороне провайдера (формат: owner/model)"
    )
    generation_type: GenerationType = Field(
        description="Тип генерации: CHAT, IMAGE, IMAGE_EDIT, TTS или STT"
    )
    price_tokens: int = Field(
        ge=0,
        description="Стоимость одной генерации в токенах",
    )

    # Опциональные поля
    display_name: str | None = Field(
        default=None,
        description="Название для отображения пользователю",
    )
    description: str | None = Field(
        default=None,
        description="Описание модели для пользователя",
    )
    cost: CostConfig = Field(
        default_factory=CostConfig,
        description="Себестоимость модели для расчёта маржинальности",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Параметры по умолчанию для модели",
    )
    system_prompt: str | None = Field(
        default=None,
        description="Системный промпт для chat-моделей (опционально)",
    )


class GenerationTimeouts(BaseModel):
    """Таймауты для разных типов генерации (в секундах).

    Если генерация не завершилась за указанное время — она считается неудачной.

    Рекомендуемые значения:
    - chat: 60 сек (обычно ответ приходит за 5-30 сек)
    - image: 180 сек (генерация может занять 1-2 минуты)
    - image_edit: 180 сек (редактирование изображений аналогично генерации)
    - tts: 120 сек (зависит от длины текста)
    - stt: 300 сек (зависит от длины аудио)
    """

    chat: int = 60
    image: int = 180
    image_edit: int = 180
    tts: int = 120
    stt: int = 300


class GenerationCooldowns(BaseModel):
    """Минимальные интервалы между запросами генерации (в секундах).

    Защита от спама: пользователь не может запустить новую генерацию,
    пока не пройдёт указанное время с предыдущей.

    Это защищает от:
    - Случайных двойных кликов
    - Намеренного спама
    - Перерасхода токенов
    """

    chat: int = 2
    image: int = 10
    image_edit: int = 10
    tts: int = 5
    stt: int = 5


class Limits(BaseModel):
    """Настройки лимитов запросов."""

    max_parallel_tasks_per_user: int = Field(
        default=2,
        ge=1,
        description="Максимальное количество параллельных генераций на пользователя",
    )
    max_context_messages: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Максимальное количество сообщений в контексте диалога",
    )
    generation_cooldowns: GenerationCooldowns = Field(
        default_factory=GenerationCooldowns,
        description="Интервалы между генерациями",
    )


class LocalizationConfig(BaseModel):
    """Настройки мультиязычности.

    Определяет, какие языки доступны в боте и какой язык используется по умолчанию.
    Переводы загружаются из файлов locales/<lang>.yaml

    По умолчанию мультиязычность ОТКЛЮЧЕНА — используется только русский язык.
    Для включения дополнительных языков установите enabled=true в config.yaml
    и добавьте нужные языки в available_languages.

    Attributes:
        enabled: Включена ли мультиязычность.
            Если False — используется только default_language, команда /language скрыта.
        default_language: Язык по умолчанию (ISO 639-1 код: ru, en, zh и т.д.)
        available_languages: Список доступных языков.
            Для каждого должен быть файл locales/<lang>.yaml
    """

    enabled: bool = Field(
        default=False,
        description="Включить поддержку мультиязычности (по умолчанию отключена)",
    )
    default_language: str = Field(
        default="ru",
        description="Язык по умолчанию (ISO 639-1)",
    )
    available_languages: list[str] = Field(
        default_factory=lambda: ["ru"],
        description="Список доступных языков (по умолчанию только русский)",
    )


class LegalConfig(BaseModel):
    """Настройки юридических документов.

    Управляет отображением юридических документов и запросом согласия.

    При enabled=True:
    - Новым пользователям показывается запрос на согласие при /start
    - Доступна команда /terms для просмотра документов
    - При изменении version у пользователей запрашивается повторное согласие

    При enabled=False:
    - Согласие не запрашивается
    - Команда /terms показывает сообщение о недоступности

    Attributes:
        enabled: Включить ли проверку согласия с документами.
        version: Версия документов (например, "1.0").
            При изменении версии пользователям показывается повторный запрос.
        privacy_policy_url: Ссылка на Политику конфиденциальности.
        terms_of_service_url: Ссылка на Пользовательское соглашение (оферту).
    """

    enabled: bool = Field(
        default=False,
        description="Включить проверку согласия с юридическими документами",
    )
    version: str = Field(
        default="1.0",
        description="Версия документов (при изменении запрашивается согласие)",
    )
    privacy_policy_url: str = Field(
        default="",
        description="Ссылка на Политику конфиденциальности",
    )
    terms_of_service_url: str = Field(
        default="",
        description="Ссылка на Пользовательское соглашение (оферту)",
    )

    def has_documents(self) -> bool:
        """Проверить, настроены ли ссылки на документы.

        Returns:
            True если обе ссылки заполнены.
        """
        return bool(self.privacy_policy_url and self.terms_of_service_url)


class BillingConfig(BaseModel):
    """Настройки системы биллинга (токенов).

    Управляет монетизацией бота:
    - Включение/отключение биллинга
    - Бонус при регистрации
    - Количество дней для повторных попыток списания подписки

    Логика работы:
    1. При регистрации (первый /start) пользователь получает registration_bonus токенов
    2. Каждая генерация списывает токены с баланса
    3. Стоимость генерации зависит от модели (models.*.price_tokens в config.yaml)
    4. Если баланс < стоимости — генерация отклоняется

    Если enabled=False:
    - Все генерации бесплатны (токены не списываются)
    - Бонус при регистрации не начисляется

    Attributes:
        enabled: Включена ли система биллинга.
            False = все генерации бесплатны.
        registration_bonus: Количество токенов, начисляемых при регистрации.
            0 = бонус отключён.
        renewal_retry_days: Количество дней для повторных попыток списания подписки.
            При неудачном списании система пытается раз в день в 09:00 МСК.
    """

    enabled: bool = Field(
        default=False,
        description="Включить систему биллинга (списание токенов за генерации)",
    )
    registration_bonus: int = Field(
        default=100,
        ge=0,
        description="Бонусных токенов при регистрации (0 = отключено)",
    )
    renewal_retry_days: int = Field(
        default=5,
        ge=1,
        le=14,
        description="Дней для повторных попыток списания подписки (1-14)",
    )


class TariffPrice(BaseModel):
    """Цены тарифа в разных валютах.

    Каждая валюта соответствует определённому провайдеру:
    - rub — YooKassa (рубли)
    - usd — Stripe (доллары)
    - stars — Telegram Stars

    Если валюта не указана — этот провайдер не будет предложен для данного тарифа.

    Пример:
        price:
          rub: 99      # 99₽ через YooKassa
          usd: 1.49    # $1.49 через Stripe
          stars: 50    # 50 Stars через Telegram
    """

    rub: int | None = Field(
        default=None,
        ge=0,
        description="Цена в рублях (для YooKassa)",
    )
    usd: float | None = Field(
        default=None,
        ge=0,
        description="Цена в долларах (для Stripe)",
    )
    stars: int | None = Field(
        default=None,
        ge=0,
        description="Цена в Telegram Stars",
    )


class TariffName(BaseModel):
    """Локализованное название тарифа.

    Содержит название на разных языках.
    Используется для отображения в интерфейсе бота.

    Пример:
        name:
          ru: "100 токенов"
          en: "100 tokens"
    """

    ru: str = Field(description="Название на русском")
    en: str = Field(default="", description="Название на английском")

    def get(self, language: str, default_language: str = "ru") -> str:
        """Получить название на указанном языке.

        Args:
            language: Код языка (ru, en, и т.д.)
            default_language: Язык по умолчанию

        Returns:
            Название тарифа на указанном языке.
        """
        if language == "en" and self.en:
            return self.en
        return self.ru


class TariffDescription(BaseModel):
    """Локализованное описание тарифа (опционально).

    Дополнительный текст под названием тарифа.

    Пример:
        description:
          ru: "Популярный выбор"
          en: "Popular choice"
    """

    ru: str = Field(default="", description="Описание на русском")
    en: str = Field(default="", description="Описание на английском")

    def get(self, language: str, default_language: str = "ru") -> str:
        """Получить описание на указанном языке."""
        if language == "en" and self.en:
            return self.en
        return self.ru


class TariffConfig(BaseModel):
    """Конфигурация одного тарифа (пакета токенов).

    Тариф — это пакет токенов, который можно купить.
    Может быть разовой покупкой (one_time) или подпиской (subscription).

    Пример в config.yaml:
        tokens_100:
          slug: tokens_100
          type: one_time
          name:
            ru: "100 токенов"
            en: "100 tokens"
          tokens: 100
          price:
            rub: 99
            usd: 1.49
            stars: 50
          enabled: true

        pro_monthly:
          slug: pro_monthly
          type: subscription
          name:
            ru: "Pro подписка"
            en: "Pro subscription"
          tokens_per_period: 1000
          period_days: 30
          burn_unused: false
          price:
            rub: 499
            usd: 6.99
            stars: 249
          enabled: true

    Attributes:
        slug: Уникальный идентификатор тарифа (используется в webhook payload).
        type: Тип тарифа (one_time = разовая покупка, subscription = подписка).
        name: Локализованное название тарифа.
        description: Локализованное описание (опционально).
        tokens: Количество токенов в пакете (для one_time).
        tokens_per_period: Количество токенов на период (для subscription).
        period_days: Длительность периода в днях (для subscription).
        burn_unused: Сгорают ли неиспользованные токены при продлении
            (для subscription).
        price: Цены в разных валютах.
        enabled: Включён ли тариф (false = не показывается пользователю).
    """

    slug: str = Field(description="Уникальный идентификатор тарифа")
    type: str = Field(
        default="one_time",
        description="Тип тарифа: one_time или subscription",
    )
    name: TariffName = Field(description="Локализованное название")
    description: TariffDescription = Field(
        default_factory=TariffDescription,
        description="Локализованное описание (опционально)",
    )
    # Для разовых покупок (one_time)
    tokens: int = Field(
        default=0,
        ge=0,
        description="Количество токенов в пакете (для one_time)",
    )
    # Для подписок (subscription)
    tokens_per_period: int = Field(
        default=0,
        ge=0,
        description="Количество токенов на период (для subscription)",
    )
    period_days: int = Field(
        default=30,
        ge=1,
        description="Длительность периода в днях (для subscription)",
    )
    burn_unused: bool = Field(
        default=True,
        description="Сгорают ли неиспользованные токены при продлении",
    )
    price: TariffPrice = Field(description="Цены в разных валютах")
    enabled: bool = Field(default=True, description="Включён ли тариф")

    @property
    def is_subscription(self) -> bool:
        """Проверяет, является ли тариф подпиской."""
        return self.type == "subscription"

    @property
    def effective_tokens(self) -> int:
        """Возвращает количество токенов для этого тарифа.

        Для one_time — tokens.
        Для subscription — tokens_per_period.
        """
        if self.is_subscription:
            return self.tokens_per_period
        return self.tokens

    def get_price_for_provider(self, provider: str) -> float | int | None:
        """Получить цену тарифа для указанного провайдера.

        Args:
            provider: Имя провайдера (yookassa, stripe, telegram_stars).

        Returns:
            Цена в валюте провайдера или None если не доступен.
        """
        price_map = {
            "yookassa": self.price.rub,
            "stripe": self.price.usd,
            "telegram_stars": self.price.stars,
        }
        return price_map.get(provider)

    def get_currency_for_provider(self, provider: str) -> str | None:
        """Получить код валюты для провайдера.

        Args:
            provider: Имя провайдера.

        Returns:
            Код валюты (RUB, USD, XTR) или None.
        """
        currency_map = {
            "yookassa": "RUB",
            "stripe": "USD",
            "telegram_stars": "XTR",
        }
        return currency_map.get(provider)

    def is_available_for_provider(self, provider: str) -> bool:
        """Проверить, доступен ли тариф для указанного провайдера.

        Args:
            provider: Имя провайдера.

        Returns:
            True если тариф имеет цену для этого провайдера.
        """
        return self.get_price_for_provider(provider) is not None


class TariffsConfig(BaseModel):
    """Конфигурация всех тарифов.

    Содержит словарь тарифов, где ключ — slug тарифа.

    Attributes:
        tariffs: Словарь {slug: настройки_тарифа}.
    """

    tariffs: dict[str, TariffConfig] = Field(
        default_factory=dict,
        description="Словарь тарифов {slug: настройки}",
    )

    def get_enabled_tariffs(self) -> list[TariffConfig]:
        """Получить список включённых тарифов.

        Returns:
            Список включённых тарифов в порядке определения в конфиге.
        """
        return [t for t in self.tariffs.values() if t.enabled]

    def get_tariff(self, slug: str) -> TariffConfig | None:
        """Получить тариф по slug.

        Args:
            slug: Уникальный идентификатор тарифа.

        Returns:
            TariffConfig или None если тариф не найден.
        """
        return self.tariffs.get(slug)

    def get_tariffs_for_provider(self, provider: str) -> list[TariffConfig]:
        """Получить тарифы, доступные для указанного провайдера.

        Args:
            provider: Имя провайдера (yookassa, stripe, telegram_stars).

        Returns:
            Список тарифов, у которых есть цена для этого провайдера.
        """
        return [
            t
            for t in self.get_enabled_tariffs()
            if t.is_available_for_provider(provider)
        ]

    def get_one_time_tariffs(self) -> list[TariffConfig]:
        """Получить только разовые тарифы (не подписки).

        Returns:
            Список включённых тарифов типа one_time.
        """
        return [t for t in self.get_enabled_tariffs() if not t.is_subscription]

    def get_subscription_tariffs(self) -> list[TariffConfig]:
        """Получить только подписочные тарифы.

        Returns:
            Список включённых тарифов типа subscription.
        """
        return [t for t in self.get_enabled_tariffs() if t.is_subscription]


class BroadcastConfig(BaseModel):
    """Настройки системы рассылок.

    Рассылки позволяют массово отправлять сообщения пользователям бота.
    Настройки контролируют скорость отправки и поведение при ошибках.

    Telegram API имеет ограничения на частоту отправки сообщений:
    - Не более 30 сообщений в секунду в один чат
    - При массовой рассылке рекомендуется ~25-30 сообщений в секунду

    При превышении лимита Telegram возвращает ошибку FloodWait с указанием
    времени ожидания. BroadcastWorker автоматически обрабатывает эту ошибку.

    Attributes:
        enabled: Включена ли система рассылок.
            Если False — рассылки недоступны в админке.
        messages_per_second: Количество сообщений в секунду.
            Рекомендуемое значение: 25-30.
            Слишком много — будут FloodWait ошибки.
            Слишком мало — рассылка займёт много времени.
        batch_size: Размер батча для сохранения прогресса.
            После каждых batch_size сообщений прогресс сохраняется в БД.
            Это позволяет возобновить рассылку после перезапуска.
            Рекомендуемое значение: 50-100.
        retry_on_error: Количество попыток отправки при временной ошибке.
            При ошибках сети или API (не FloodWait) воркер повторит попытку.
            0 = не повторять, отмечать как failed сразу.
        flood_wait_multiplier: Множитель для времени ожидания FloodWait.
            Telegram возвращает retry_after в секундах.
            Реальное ожидание = retry_after * flood_wait_multiplier.
            Значение > 1.0 добавляет запас на случай неточности.
            Рекомендуемое значение: 1.1-1.5.
    """

    enabled: bool = Field(
        default=True,
        description="Включить систему рассылок",
    )
    messages_per_second: float = Field(
        default=25.0,
        ge=1.0,
        le=30.0,
        description="Количество сообщений в секунду (1-30). Рекомендуется 25.",
    )
    batch_size: int = Field(
        default=50,
        ge=10,
        le=500,
        description="Размер батча для сохранения прогресса (10-500).",
    )
    retry_on_error: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Количество повторных попыток при ошибке (0-10).",
    )
    flood_wait_multiplier: float = Field(
        default=1.2,
        ge=1.0,
        le=3.0,
        description="Множитель для времени ожидания FloodWait (1.0-3.0).",
    )


class ReferralConfig(BaseModel):
    """Настройки реферальной программы.

    Реферальная система позволяет пользователям приглашать друзей
    и получать бонусы за каждого приглашённого.

    Как работает:
    1. Пользователь получает персональную ссылку через /invite
    2. Формат ссылки: https://t.me/botname?start=ref_USERID
    3. Когда друг переходит по ссылке и регистрируется:
       - Приглашённый получает invitee_bonus токенов
       - Пригласивший получает inviter_bonus токенов
    4. Пригласивший может заработать максимум max_earnings токенов

    Защита от злоупотреблений:
    - Нельзя пригласить самого себя
    - Один telegram_id = один бонус (повторная регистрация не даёт бонус)
    - Лимит заработка через max_earnings

    Attributes:
        enabled: Включена ли реферальная программа.
            Если False — команда /invite автоматически отключается.
        inviter_bonus: Токены пригласившему (за каждого нового реферала).
        invitee_bonus: Токены приглашённому (бонус при регистрации).
        max_earnings: Максимум токенов, которые можно заработать на рефералах.
            0 = без лимита.
        require_payment: Если True — бонус пригласившему начисляется только
            после первой оплаты приглашённого (защита от фродеров).
    """

    enabled: bool = Field(
        default=True,
        description="Включить реферальную программу",
    )
    inviter_bonus: int = Field(
        default=50,
        ge=0,
        description="Токенов пригласившему за каждого реферала",
    )
    invitee_bonus: int = Field(
        default=25,
        ge=0,
        description="Токенов приглашённому при регистрации",
    )
    max_earnings: int = Field(
        default=5000,
        ge=0,
        description="Максимум токенов через рефералку (0 = без лимита)",
    )
    require_payment: bool = Field(
        default=False,
        description="Бонус пригласившему только после первой оплаты приглашённого",
    )


class SupportConfig(BaseModel):
    """Настройки поддержки пользователей.

    Содержит контактную информацию для обращения в поддержку.
    Отображается в команде /help.

    Пример в config.yaml:
        support:
          contact: "@support_username"

    Attributes:
        contact: Контакт поддержки для отображения пользователям.
            Может быть:
            - username Telegram: @support_bot или @admin_username
            - ссылка на чат: https://t.me/support_chat
            - email: support@example.com
            Если пустой — команда /help покажет сообщение без контакта.
    """

    contact: str = Field(
        default="",
        description="Контакт поддержки (username, ссылка или email)",
    )


class ChannelSubscriptionConfig(BaseModel):
    """Настройки проверки подписки на канал.

    Определяет параметры кеширования результатов проверки подписки.
    Кеширование необходимо, чтобы не делать запрос к Telegram API
    при каждом сообщении от пользователя.

    ID канала и ссылка на него задаются через переменные окружения
    (CHANNEL__REQUIRED_ID, CHANNEL__INVITE_LINK), а TTL кеша — здесь.

    Attributes:
        cache_ttl_seconds: Время жизни кеша результата проверки подписки.
            После истечения TTL — проверка выполняется заново.
            Рекомендуемое значение: 300-600 секунд (5-10 минут).
            Слишком маленький TTL — нагрузка на Telegram API.
            Слишком большой — долгая задержка после подписки/отписки.
    """

    cache_ttl_seconds: int = Field(
        default=300,
        ge=0,
        description="Время жизни кеша проверки подписки в секундах (0 = без кеша)",
    )


class TelegramStarsConfig(BaseModel):
    """Настройки Telegram Stars.

    Telegram Stars — встроенная валюта Telegram для оплаты внутри ботов.
    Не требует внешних API-ключей — работает через Telegram Bot API.

    Attributes:
        enabled: Включить приём платежей через Telegram Stars.
    """

    enabled: bool = Field(
        default=False,
        description="Включить приём платежей через Telegram Stars",
    )


class PaymentsConfig(BaseModel):
    """Настройки провайдеров оплаты из YAML-конфига.

    Управляет включением/выключением платёжных провайдеров.

    Telegram Stars включается здесь (enabled: true).
    YooKassa и Stripe включаются автоматически при наличии ключей в .env.

    Пример в config.yaml:
        payments:
          telegram_stars:
            enabled: true

    Attributes:
        telegram_stars: Настройки Telegram Stars.
    """

    telegram_stars: TelegramStarsConfig = Field(
        default_factory=TelegramStarsConfig,
        description="Настройки Telegram Stars",
    )


class CommandConfig(BaseModel):
    """Конфигурация одной команды бота.

    Каждая команда может быть включена/выключена и показана/скрыта в меню Telegram.
    Описание команды локализуется для разных языков.

    Пример в config.yaml:
        commands:
          chatgpt:
            enabled: true
            show_in_menu: true
            menu_description:
              ru: "Диалог с ИИ"
              en: "Chat with AI"

    Attributes:
        enabled: Включена ли команда.
            Если false — роутер не подключается, команда полностью недоступна.
            Если команда включена, но её зависимости (API ключи и т.д.) не настроены —
            пользователь увидит ошибку при использовании (это ожидаемое поведение).
        show_in_menu: Показывать ли команду в меню Telegram (кнопка "/" в интерфейсе).
            Если false — команда работает, но не видна в меню.
            Пример: /start обычно не показывают в меню.
        menu_description: Описание команды для меню Telegram на разных языках.
            Ключ — код языка (ru, en, и т.д.).
            Первый язык (обычно ru) используется как default для остальных языков.
            Если словарь пустой и show_in_menu=true — используется имя команды.
    """

    enabled: bool = Field(
        default=True,
        description="Включена ли команда (если false — роутер не подключается)",
    )
    show_in_menu: bool = Field(
        default=True,
        description="Показывать ли команду в меню Telegram",
    )
    menu_description: dict[str, str] = Field(
        default_factory=dict,
        description="Описание команды для меню на разных языках {lang: description}",
    )

    def get_description(self, language: str, default_language: str = "ru") -> str:
        """Получить описание команды для указанного языка.

        Логика выбора:
        1. Если есть описание для запрошенного языка — возвращаем его
        2. Если нет — пробуем default_language
        3. Если и его нет — возвращаем первое доступное описание
        4. Если словарь пустой — возвращаем пустую строку

        Args:
            language: Код языка (ru, en, и т.д.)
            default_language: Язык по умолчанию (обычно ru)

        Returns:
            Описание команды на указанном языке или fallback.
        """
        if language in self.menu_description:
            return self.menu_description[language]
        if default_language in self.menu_description:
            return self.menu_description[default_language]
        if self.menu_description:
            # Возвращаем первое доступное описание
            return next(iter(self.menu_description.values()))
        return ""


class CommandsConfig(BaseModel):
    """Конфигурация всех команд бота.

    Содержит словарь команд, где ключ — имя команды (start, chatgpt, и т.д.),
    а значение — объект CommandConfig с настройками.

    ВАЖНО: Если команда не указана в конфиге — она считается ОТКЛЮЧЁННОЙ.
    Это обеспечивает явный контроль над доступными командами.

    Пример в config.yaml:
        commands:
          start:
            enabled: true
            show_in_menu: false
          chatgpt:
            enabled: true
            show_in_menu: true
            menu_description:
              ru: "Диалог с ИИ"
              en: "Chat with AI"
          billing:
            enabled: false  # Биллинг отключён
            show_in_menu: false

    Attributes:
        commands: Словарь {имя_команды: настройки}.
    """

    # Используем __root__ для Pydantic v2 — это позволяет использовать
    # CommandsConfig как dict напрямую
    commands: dict[str, CommandConfig] = Field(
        default_factory=dict,
        description="Словарь команд {имя: настройки}",
    )

    def is_enabled(self, command_name: str) -> bool:
        """Проверить, включена ли команда.

        Команда считается включённой если:
        1. Она есть в конфиге
        2. У неё enabled=true

        Если команда не указана в конфиге — она считается ОТКЛЮЧЁННОЙ.

        Args:
            command_name: Имя команды (start, chatgpt, billing, и т.д.)

        Returns:
            True если команда включена, False иначе.
        """
        config = self.commands.get(command_name)
        if config is None:
            return False
        return config.enabled

    def should_show_in_menu(self, command_name: str) -> bool:
        """Проверить, нужно ли показывать команду в меню.

        Команда показывается в меню если:
        1. Она включена (enabled=true)
        2. У неё show_in_menu=true

        Args:
            command_name: Имя команды.

        Returns:
            True если команду нужно показывать в меню.
        """
        if not self.is_enabled(command_name):
            return False
        config = self.commands.get(command_name)
        if config is None:
            return False
        return config.show_in_menu

    def get_menu_commands(
        self, language: str, default_language: str = "ru"
    ) -> list[tuple[str, str]]:
        """Получить список команд для меню Telegram на указанном языке.

        Возвращает только команды, которые:
        1. Включены (enabled=true)
        2. Должны показываться в меню (show_in_menu=true)

        Args:
            language: Код языка для описаний (ru, en, и т.д.)
            default_language: Язык по умолчанию для fallback

        Returns:
            Список кортежей (имя_команды, описание).
            Порядок соответствует порядку в конфиге.
        """
        result: list[tuple[str, str]] = []
        for name, config in self.commands.items():
            if config.enabled and config.show_in_menu:
                description = config.get_description(language, default_language)
                result.append((name, description))
        return result

    def get_enabled_commands(self) -> list[str]:
        """Получить список имён всех включённых команд.

        Returns:
            Список имён включённых команд.
        """
        return [name for name, config in self.commands.items() if config.enabled]


class YamlConfig(BaseModel):
    """Главная YAML-конфигурация.

    Загружается из config.yaml при старте приложения.
    """

    # Словарь моделей: ключ — ID модели в нашей системе
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    generation_timeouts: GenerationTimeouts = GenerationTimeouts()
    limits: Limits = Limits()
    localization: LocalizationConfig = LocalizationConfig()
    legal: LegalConfig = Field(
        default_factory=LegalConfig,
        description=(
            "Настройки юридических документов (оферта, политика конфиденциальности)"
        ),
    )
    billing: BillingConfig = BillingConfig()
    tariffs: TariffsConfig = Field(
        default_factory=TariffsConfig,
        description="Конфигурация тарифов (пакетов токенов для покупки)",
    )
    payments: PaymentsConfig = Field(
        default_factory=PaymentsConfig,
        description="Настройки провайдеров оплаты",
    )
    broadcast: BroadcastConfig = BroadcastConfig()
    referral: ReferralConfig = ReferralConfig()
    support: SupportConfig = Field(
        default_factory=SupportConfig,
        description="Настройки поддержки пользователей",
    )
    channel_subscription: ChannelSubscriptionConfig = Field(
        default_factory=ChannelSubscriptionConfig,
        description="Настройки проверки подписки на канал",
    )
    commands: CommandsConfig = Field(
        default_factory=CommandsConfig,
        description="Конфигурация команд бота (включение/выключение, меню)",
    )

    @field_validator("models", mode="before")
    @classmethod
    def parse_models(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Преобразовать сырые данные моделей в ModelConfig.

        Если models пустой словарь {} — возвращаем пустой dict.
        Иначе валидируем каждую модель.
        """
        if not v:
            return {}
        return v

    @field_validator("commands", mode="before")
    @classmethod
    def parse_commands(cls, v: dict[str, Any] | None) -> dict[str, Any]:
        """Преобразовать данные команд в структуру CommandsConfig.

        В YAML команды указываются напрямую:
            commands:
              start:
                enabled: true
              chatgpt:
                enabled: true

        Но CommandsConfig ожидает {"commands": {...}}.
        Этот валидатор оборачивает входные данные в нужную структуру.
        """
        if v is None:
            return {"commands": {}}
        # Если пришёл dict без ключа "commands" — это прямой словарь команд
        # Оборачиваем его в {"commands": ...}
        if "commands" not in v:
            return {"commands": v}
        return v

    @field_validator("tariffs", mode="before")
    @classmethod
    def parse_tariffs(cls, v: dict[str, Any] | None) -> dict[str, Any]:
        """Преобразовать данные тарифов в структуру TariffsConfig.

        В YAML тарифы указываются напрямую:
            tariffs:
              tokens_100:
                slug: tokens_100
                tokens: 100
                ...

        Но TariffsConfig ожидает {"tariffs": {...}}.
        Этот валидатор оборачивает входные данные в нужную структуру.
        """
        if v is None:
            return {"tariffs": {}}
        # Если пришёл dict без ключа "tariffs" — это прямой словарь тарифов
        # Оборачиваем его в {"tariffs": ...}
        if "tariffs" not in v:
            return {"tariffs": v}
        return v

    def get_model(self, model_key: str) -> ModelConfig | None:
        """Получить конфигурацию модели по ключу.

        Args:
            model_key: Ключ модели (gpt-4o, claude-sonnet, dall-e-3, и т.д.)

        Returns:
            ModelConfig или None если модель не найдена.
        """
        return self.models.get(model_key)

    def get_tariff(self, slug: str) -> TariffConfig | None:
        """Получить конфигурацию тарифа по slug.

        Args:
            slug: Уникальный идентификатор тарифа (tokens_100, pro_monthly, и т.д.)

        Returns:
            TariffConfig или None если тариф не найден.
        """
        return self.tariffs.get_tariff(slug)

    def get_enabled_tariffs(self) -> list[TariffConfig]:
        """Получить список всех включённых тарифов.

        Returns:
            Список включённых тарифов в порядке определения в конфиге.
        """
        return self.tariffs.get_enabled_tariffs()

    def get_tariffs_for_provider(self, provider: str) -> list[TariffConfig]:
        """Получить тарифы, доступные для указанного провайдера.

        Args:
            provider: Имя провайдера (yookassa, stripe, telegram_stars).

        Returns:
            Список тарифов с ценой для этого провайдера.
        """
        return self.tariffs.get_tariffs_for_provider(provider)

    def has_subscription_tariffs(self) -> bool:
        """Проверить, есть ли включённые подписочные тарифы.

        Подписки считаются включёнными, если в конфиге есть хотя бы один
        тариф с type=subscription и enabled=true.

        Returns:
            True если есть подписочные тарифы.
        """
        return len(self.tariffs.get_subscription_tariffs()) > 0


def load_yaml_config(path: Path | str = "config.yaml") -> YamlConfig:
    """Загрузить и валидировать YAML-конфигурацию.

    Args:
        path: Путь к файлу конфигурации.

    Returns:
        Валидированный объект конфигурации.

    Raises:
        FileNotFoundError: Файл конфигурации не найден.
        yaml.YAMLError: Некорректный YAML.
        pydantic.ValidationError: Некорректная конфигурация.
    """
    config_path = Path(path)

    if not config_path.exists():
        return YamlConfig()

    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return YamlConfig.model_validate(data)


yaml_config = load_yaml_config()
