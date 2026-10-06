"""Модели настроек приложения.

Этот модуль содержит только классы настроек (Pydantic модели),
БЕЗ загрузки из переменных окружения. Это позволяет:
- Импортировать классы в тестах без побочных эффектов
- Создавать экземпляры с тестовыми данными
- Изолировать тесты от реальных переменных окружения

Для загрузки настроек из .env используйте модуль settings.py.
"""

from pydantic import AliasChoices, AliasPath, BaseModel, Field, SecretStr


class BotSettings(BaseModel):
    """Настройки Telegram-бота."""

    token: SecretStr
    # Отдельный HTTP-прокси позволяет восстановить Telegram без смены маршрута AI.
    proxy: str | None = None


class DatabaseSettings(BaseModel):
    """Настройки базы данных.

    По умолчанию используется SQLite (логика в db/base.py).
    Для PostgreSQL укажите DATABASE__POSTGRES_URL в переменных окружения.
    """

    # URL подключения к PostgreSQL (опционально).
    # Если не указан — используется SQLite.
    # Формат: postgresql+asyncpg://user:pass@host:5432/dbname
    postgres_url: str | None = None


class TelegramLoggingSettings(BaseModel):
    """Настройки отправки логов в Telegram.

    Позволяет получать уведомления об ошибках прямо в Telegram.
    Полезно для мониторинга бота без доступа к серверу.

    Уведомления отправляются только если указан chat_id.
    """

    # Telegram ID для получения логов.
    # Это может быть:
    #   - Личный ID пользователя (число вида 123456789)
    #   - ID группы (отрицательное число вида -1001234567890)
    #
    # Как узнать свой ID: напишите боту @userinfobot в Telegram.
    # Если не указан — логи в Telegram не отправляются.
    chat_id: int | None = None

    # Минимальный уровень логов для отправки в Telegram.
    # По умолчанию ERROR — отправляются только ошибки.
    #
    # Варианты (от самого подробного к минимальному):
    #   DEBUG    — всё подряд (НЕ РЕКОМЕНДУЕТСЯ — завалит сообщениями)
    #   INFO     — основные события (много сообщений)
    #   WARNING  — предупреждения и ошибки
    #   ERROR    — только ошибки (РЕКОМЕНДУЕТСЯ)
    #   CRITICAL — только критические ошибки
    #
    # ВАЖНО: Если поставить INFO или DEBUG — будет ОЧЕНЬ много сообщений!
    level: str = "ERROR"

    @property
    def is_enabled(self) -> bool:
        """Проверить, включена ли отправка логов в Telegram."""
        return self.chat_id is not None


class LoggingSettings(BaseModel):
    """Настройки логирования."""

    level: str = "INFO"

    # Часовой пояс для отображения времени в логах и админке.
    # Формат: строка из базы IANA (Europe/Moscow, UTC, America/New_York).
    # По умолчанию — Москва (UTC+3).
    # Список: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
    timezone: str = "Europe/Moscow"

    # Настройки отправки логов в Telegram.
    # Позволяет админу получать уведомления об ошибках прямо в мессенджер.
    telegram: TelegramLoggingSettings = TelegramLoggingSettings()


class AdminSettings(BaseModel):
    """Настройки веб-админки.

    Админка доступна только если указаны username и password.
    Если они не заданы — админка не монтируется к приложению.

    URL админки: /admin
    """

    # Логин для входа в админку.
    # Если не указан — админка недоступна.
    username: str | None = None

    # Пароль для входа в админку.
    # Рекомендуется использовать сложный пароль (минимум 12 символов).
    password: SecretStr | None = None

    # Секретный ключ для подписи сессий.
    # Используется для защиты cookies от подделки.
    # Если не указан — генерируется автоматически (не сохраняется между перезапусками).
    secret_key: SecretStr | None = None

    @property
    def is_enabled(self) -> bool:
        """Проверить, включена ли админка.

        Админка включена только если указаны оба параметра: username и password.
        """
        return self.username is not None and self.password is not None


class AIProvidersSettings(BaseModel):
    """Настройки AI-провайдеров.

    Поддерживаемые провайдеры:
    - OpenRouter (https://openrouter.ai) — агрегатор моделей
    - RouterAI (https://routerai.ru) — российский сервис
    - AnyModel (https://anymodel.org) — резервный OpenAI-совместимый шлюз

    Оба провайдера OpenAI-совместимые, работают через единый адаптер.
    """

    # OpenRouter API ключ.
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "openrouter_api_key", AliasPath("openrouter", "api_key")
        ),
    )

    # RouterAI API ключ.
    routerai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "routerai_api_key", AliasPath("routerai", "api_key")
        ),
    )

    # Резервный ключ используется, когда основной RouterAI недоступен.
    anymodel_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "anymodel_api_key", AliasPath("anymodel", "api_key")
        ),
    )

    @property
    def has_openrouter(self) -> bool:
        """Проверить, настроен ли OpenRouter."""
        return self.openrouter_api_key is not None

    @property
    def has_routerai(self) -> bool:
        """Проверить, настроен ли RouterAI."""
        return self.routerai_api_key is not None

    @property
    def has_anymodel(self) -> bool:
        """Проверить, настроен ли резервный AnyModel."""
        return self.anymodel_api_key is not None


class FSMSettings(BaseModel):
    """Настройки хранилища FSM-состояний.

    FSM (Finite State Machine) — механизм для управления состояниями диалогов.
    Например, когда пользователь выбирает модель → вводит промпт → ждёт результат.

    Варианты хранилища:
    - memory — в памяти (по умолчанию, теряется при перезапуске)
    - sqlite — в SQLite файле (сохраняется между перезапусками)
    - redis — в Redis (для масштабирования на несколько инстансов)
    """

    # Тип хранилища: memory, sqlite, redis.
    # По умолчанию: sqlite (данные сохраняются в файл).
    storage: str = "sqlite"

    # Путь к файлу SQLite для FSM-состояний.
    # Используется только если storage=sqlite.
    # На Amvera папка /data сохраняется между деплоями.
    sqlite_path: str = "data/fsm.db"

    # URL подключения к Redis.
    # Используется только если storage=redis.
    # Формат: redis://[[username]:[password]@]host[:port]/[db]
    # Пример: redis://localhost:6379/0
    redis_url: str | None = None


# ==============================================================================
# НАСТРОЙКИ ПЛАТЁЖНЫХ ПРОВАЙДЕРОВ
# ==============================================================================


class YooKassaSettings(BaseModel):
    """Настройки YooKassa (ЮKassa).

    YooKassa — российская платёжная система для приёма платежей в рублях.
    Поддерживает: банковские карты, ЮMoney, SberPay, Tinkoff и др.

    Как получить ключи:
    1. Зарегистрируйтесь на https://yookassa.ru
    2. Подключите магазин (потребуются документы ИП/ООО)
    3. В личном кабинете → Интеграция → Ключи API
    4. Создайте секретный ключ

    Для тестирования:
    - Используйте тестовый магазин (shop_id начинается с test_)
    - Тестовые карты: https://yookassa.ru/developers/payment-acceptance/testing
    """

    # ID магазина (shopId) из личного кабинета YooKassa.
    # Формат: числовой ID (например, 123456) или тестовый (test_123456)
    shop_id: str | None = None

    # Секретный ключ для аутентификации API-запросов.
    # Генерируется в личном кабинете: Интеграция → Ключи API
    # ВАЖНО: Храните в секрете! При утечке — отзовите и создайте новый.
    secret_key: SecretStr | None = None

    @property
    def is_configured(self) -> bool:
        """Проверить, настроен ли YooKassa."""
        return self.shop_id is not None and self.secret_key is not None


class StripeSettings(BaseModel):
    """Настройки Stripe.

    Stripe — международная платёжная система для приёма платежей в USD/EUR и др.
    Поддерживает: банковские карты, Apple Pay, Google Pay и др.

    Как получить ключи:
    1. Зарегистрируйтесь на https://stripe.com
    2. В Dashboard → Developers → API keys
    3. Скопируйте Secret key и Publishable key

    Для тестирования:
    - Используйте тестовые ключи (начинаются с sk_test_ и pk_test_)
    - Тестовые карты: 4242 4242 4242 4242 (успех), 4000 0000 0000 9995 (отказ)
    """

    # Секретный ключ (Secret key) для серверных операций.
    # Формат: sk_live_... (боевой) или sk_test_... (тестовый)
    # Используется для создания платежей, обработки webhook'ов.
    secret_key: SecretStr | None = None

    # Секрет для проверки подписи webhook'ов.
    # Создаётся в Dashboard → Developers → Webhooks → Signing secret
    # Формат: whsec_...
    # ВАЖНО: Без этого нельзя безопасно обрабатывать webhook'и!
    webhook_secret: SecretStr | None = None

    @property
    def is_configured(self) -> bool:
        """Проверить, настроен ли Stripe."""
        return self.secret_key is not None

    @property
    def has_webhook_secret(self) -> bool:
        """Проверить, настроен ли webhook secret."""
        return self.webhook_secret is not None


class TelegramStarsSettings(BaseModel):
    """Настройки Telegram Stars.

    Telegram Stars — встроенная валюта Telegram для оплаты внутри ботов.
    НЕ ТРЕБУЕТ внешних ключей — работает через Telegram Bot Payments API.

    Преимущества:
    - Работает из коробки (не нужны документы, регистрация в платёжках)
    - Пользователь платит прямо в Telegram (без редиректов)
    - Поддержка подписок с автопродлением (период 30 дней)

    Ограничения:
    - Фиксированный курс Stars (примерно 1 Star ≈ $0.02)
    - Подписки только на 30 дней
    - Вывод средств через Telegram (Fragment)

    ВАЖНО: Включение/выключение настраивается в config.yaml (секция payments),
    а не через переменные окружения.
    """


class PaymentsSettings(BaseModel):
    """Настройки платёжных провайдеров.

    Поддерживаемые провайдеры:
    - YooKassa — для рублёвых платежей (Россия)
    - Stripe — для международных платежей (USD)
    - Telegram Stars — встроенная валюта Telegram

    Провайдер автоматически включается если настроены его ключи.
    Пользователю показываются только доступные способы оплаты.
    """

    # Настройки YooKassa
    yookassa: YooKassaSettings = YooKassaSettings()

    # Настройки Stripe
    stripe: StripeSettings = StripeSettings()

    # Настройки Telegram Stars
    telegram_stars: TelegramStarsSettings = TelegramStarsSettings()

    @property
    def has_yookassa(self) -> bool:
        """Проверить, настроен ли YooKassa."""
        return self.yookassa.is_configured

    @property
    def has_stripe(self) -> bool:
        """Проверить, настроен ли Stripe."""
        return self.stripe.is_configured

    @property
    def has_telegram_stars(self) -> bool:
        """Проверить, включены ли Telegram Stars.

        Читает настройку из config.yaml (секция payments.telegram_stars.enabled).
        """
        # Ленивый импорт для избежания циклических зависимостей
        from src.config.yaml_config import yaml_config

        return yaml_config.payments.telegram_stars.enabled

    @property
    def available_providers(self) -> list[str]:
        """Получить список доступных провайдеров."""
        providers = []
        if self.has_yookassa:
            providers.append("yookassa")
        if self.has_stripe:
            providers.append("stripe")
        if self.has_telegram_stars:
            providers.append("telegram_stars")
        return providers

    @property
    def has_any_provider(self) -> bool:
        """Проверить, есть ли хотя бы один настроенный провайдер."""
        return len(self.available_providers) > 0


class AppSettings(BaseModel):
    """Настройки приложения (домен, режим работы).

    Эти настройки определяют режим работы Telegram-бота:
    - DEV (локальная разработка): long polling
    - PROD (продакшен с доменом): webhook mode
    """

    # Домен приложения для webhook mode (опционально).
    # Если указан — бот работает через webhook.
    # Если не указан — бот работает через long polling (для локальной разработки).
    #
    # Формат: можно указать с протоколом или без:
    #   - example.com
    #   - https://example.com
    #   - https://example.com/
    #
    # Приложение автоматически:
    #   - Добавит https:// если не указан протокол
    #   - Удалит лишние слеши
    #   - Сформирует WEBHOOK_URL: https://{domain}/api/telegram/webhook
    #
    # ВАЖНО для PROD:
    #   - Домен должен быть доступен из интернета
    #   - Должен быть HTTPS (Telegram не принимает HTTP webhook)
    #   - Telegram должен иметь доступ к этому домену
    domain: str | None = None

    @property
    def is_production(self) -> bool:
        """Проверить, работает ли приложение в production mode.

        Production mode включается автоматически при наличии домена.
        В production используется webhook вместо polling.
        """
        return self.domain is not None


class ChannelSettings(BaseModel):
    """Настройки обязательной подписки на канал.

    Если указан required_id — бот проверяет, подписан ли пользователь
    на указанный канал. Без подписки функционал бота недоступен.

    Бот должен быть администратором канала для проверки подписки
    (нужен доступ к getChatMember API).

    Как получить ID канала:
    1. Добавьте бота @userinfobot в канал
    2. Перешлите любое сообщение из канала боту
    3. Бот покажет ID вида -1001234567890

    Важно:
    - ID канала всегда отрицательный и начинается с -100
    - Бот должен быть администратором канала
    - При ошибке проверки (API недоступен, нет прав) — пользователь пропускается
    """

    # ID канала для обязательной подписки.
    # Если None — проверка подписки отключена, бот работает для всех.
    # Если задан — пользователи без подписки не смогут использовать бота.
    #
    # Формат: отрицательное число вида -1001234567890
    # Пример: CHANNEL__REQUIRED_ID=-1001234567890
    required_id: int | None = None

    # Ссылка на канал для кнопки "Подписаться".
    # Формат: @channelname или https://t.me/channelname
    # Если не указана — кнопка не отображается (только текст с просьбой подписаться).
    #
    # Пример: CHANNEL__INVITE_LINK=@mychannel
    invite_link: str | None = None

    @property
    def is_enabled(self) -> bool:
        """Проверить, включена ли проверка подписки на канал."""
        return self.required_id is not None
