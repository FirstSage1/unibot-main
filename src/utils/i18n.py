"""Система локализации (мультиязычности) бота.

Этот модуль отвечает за загрузку и предоставление переводов интерфейса бота
на разные языки. Переводы хранятся в YAML-файлах в папке locales/.

Архитектура (Dependency Injection):
1. LocalizationConfig — конфигурация (инжектируется, можно мокать)
2. LocalizationService — синглтон с переводами (инжектируется)
3. Localization — объект для конкретного пользователя (создаётся через factory)

Принцип работы:
1. При старте бота вызывается init_localization() для загрузки переводов
2. Middleware определяет язык пользователя (из БД или Telegram)
3. Обработчики получают объект Localization через dependency injection
4. Локализованные сообщения отправляются пользователю

Пример использования:
    # В обработчике
    async def cmd_start(message: Message, l10n: Localization) -> None:
        await message.answer(l10n.get("start_message"))

    # В тестах — можно инжектировать mock переводы
    service = LocalizationService(
        translations={"ru": {"start_message": "Привет!"}},
        config=LocalizationConfig(
            enabled=True, default_language="ru", available_languages=("ru",)
        ),
    )
    l10n = Localization("ru", service)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.config.constants import PROJECT_ROOT
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Путь к папке с переводами
LOCALES_DIR = PROJECT_ROOT / "locales"


@dataclass(frozen=True)
class LocalizationConfig:
    """Конфигурация локализации (иммутабельная, инжектируемая).

    Это dataclass для передачи настроек локализации.
    Можно легко создать в тестах без зависимости от yaml_config.

    По умолчанию мультиязычность ОТКЛЮЧЕНА — используется только русский язык.

    Attributes:
        enabled: Включена ли мультиязычность.
        default_language: Язык по умолчанию (ISO 639-1: ru, en, zh).
        available_languages: Список доступных языков.
    """

    enabled: bool = False
    default_language: str = "ru"
    available_languages: tuple[str, ...] = field(default_factory=lambda: ("ru",))


class LocalizationService:
    """Сервис локализации — хранит переводы и конфигурацию.

    Это синглтон (но можно создать отдельный экземпляр для тестов).
    Загружает переводы один раз при инициализации.

    Attributes:
        translations: Словарь переводов {язык: {ключ: значение}}.
        config: Конфигурация локализации.

    Example:
        # Production — использовать глобальный instance
        service = get_localization_service()

        # Тесты — создать отдельный экземпляр с mock данными
        service = LocalizationService(
            translations={"ru": {"key": "Значение"}},
            config=LocalizationConfig(enabled=True, default_language="ru"),
        )
    """

    # Глобальный экземпляр сервиса (синглтон)
    _instance: "LocalizationService | None" = None

    def __init__(
        self,
        translations: dict[str, dict[str, str]],
        config: LocalizationConfig,
    ) -> None:
        """Создать сервис локализации.

        Args:
            translations: Словарь переводов {язык: {ключ: значение}}.
            config: Конфигурация локализации.
        """
        self._translations = translations
        self._config = config

    @property
    def translations(self) -> dict[str, dict[str, str]]:
        """Получить все переводы."""
        return self._translations

    @property
    def config(self) -> LocalizationConfig:
        """Получить конфигурацию."""
        return self._config

    def get_translation(self, language: str, key: str) -> str | None:
        """Получить перевод для ключа на указанном языке.

        Args:
            language: Код языка (ru, en и т.д.).
            key: Ключ перевода.

        Returns:
            Переведённая строка или None если не найдена.
        """
        return self._translations.get(language, {}).get(key)


def load_translations_from_yaml(
    locales_dir: Path,
    available_languages: list[str],
) -> dict[str, dict[str, str]]:
    """Загрузить переводы из YAML-файлов.

    Args:
        locales_dir: Путь к папке с файлами переводов.
        available_languages: Список языков для загрузки.

    Returns:
        Словарь переводов {язык: {ключ: значение}}.

    Raises:
        RuntimeError: Если не удалось загрузить ни одного языка.
    """
    translations: dict[str, dict[str, str]] = {}

    for lang in available_languages:
        file_path = locales_dir / f"{lang}.yaml"

        if not file_path.exists():
            logger.error(
                "Файл перевода не найден: %s (язык: %s). "
                "Создайте файл или удалите язык из available_languages.",
                file_path,
                lang,
            )
            continue

        try:
            with file_path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            translations[lang] = data
            logger.info(
                "Загружены переводы для языка: %s (%d ключей)",
                lang,
                len(data),
            )

        except yaml.YAMLError as e:
            logger.error("Ошибка загрузки переводов для %s: %s", lang, e)
            continue

    if not translations:
        raise RuntimeError(
            f"Не удалось загрузить ни одного языка из {locales_dir}. "
            f"Проверьте наличие файлов локализации для: {available_languages}"
        )

    return translations


def init_localization() -> LocalizationService:
    """Инициализировать систему локализации при старте приложения.

    Загружает конфигурацию из yaml_config и переводы из файлов.
    Создаёт глобальный экземпляр LocalizationService.

    Returns:
        Инициализированный LocalizationService.

    Note:
        Импорт yaml_config делается здесь, а не на уровне модуля,
        чтобы избежать циклических импортов и side effects при импорте.
    """
    # Импортируем здесь, чтобы избежать циклических импортов
    # и side effects при импорте модуля
    from src.config.yaml_config import yaml_config

    config = LocalizationConfig(
        enabled=yaml_config.localization.enabled,
        default_language=yaml_config.localization.default_language,
        available_languages=tuple(yaml_config.localization.available_languages),
    )

    translations = load_translations_from_yaml(
        locales_dir=LOCALES_DIR,
        available_languages=list(config.available_languages),
    )

    service = LocalizationService(translations=translations, config=config)

    # Сохраняем как глобальный экземпляр
    LocalizationService._instance = service

    logger.info(
        "Локализация инициализирована: languages=%s, default=%s, enabled=%s",
        config.available_languages,
        config.default_language,
        config.enabled,
    )

    return service


def get_localization_service() -> LocalizationService:
    """Получить глобальный экземпляр LocalizationService.

    Если сервис ещё не инициализирован — инициализирует его.
    Это ленивая инициализация для случаев, когда модуль импортируется
    до вызова init_localization().

    Returns:
        Глобальный экземпляр LocalizationService.
    """
    if LocalizationService._instance is None:
        init_localization()

    # Мы только что инициализировали, значит _instance точно не None
    assert LocalizationService._instance is not None
    return LocalizationService._instance


class Localization:
    """Класс для работы с локализацией (переводами) для конкретного пользователя.

    Получает переводы из LocalizationService и предоставляет интерфейс
    для получения переведённых строк.

    Attributes:
        language: Текущий язык пользователя (ISO 639-1 код: ru, en, zh и т.д.).
        _service: Сервис локализации (инжектируется).

    Example:
        # Production — использовать create_localization()
        l10n = create_localization("ru")
        message = l10n.get("start_message")

        # Тесты — создать напрямую с mock сервисом
        service = LocalizationService(
            translations={"ru": {"key": "Значение"}},
            config=LocalizationConfig(enabled=True, default_language="ru"),
        )
        l10n = Localization("ru", service)
    """

    def __init__(self, language: str, service: LocalizationService) -> None:
        """Создать объект локализации для конкретного языка.

        Args:
            language: Код языка пользователя (ru, en, zh и т.д.).
            service: Сервис локализации с загруженными переводами.
        """
        # Если язык не доступен — используем язык по умолчанию
        if language not in service.config.available_languages:
            language = service.config.default_language

        self.language = language
        self._service = service

    def get(self, key: str, **kwargs: Any) -> str:
        """Получить переведённую строку по ключу.

        Если перевод не найден для текущего языка — используется язык по умолчанию.
        Если не найден и там — возвращается сам ключ.

        Поддерживает форматирование через kwargs:
            l10n.get("hello_user", name="Alice") → "Hello, Alice!"

        Args:
            key: Ключ перевода (например, "start_message").
            **kwargs: Параметры для форматирования строки.

        Returns:
            Переведённая и отформатированная строка.

        Example:
            >>> l10n = create_localization("ru")
            >>> l10n.get("start_message")
            "Привет! Я бот для AI-генерации..."
            >>> l10n.get("hello_user", name="Алиса")
            "Привет, Алиса!"
        """
        default_language = self._service.config.default_language

        # Пытаемся получить перевод на текущем языке
        translation = self._service.get_translation(self.language, key)

        # Если не нашли — берём из языка по умолчанию
        if translation is None:
            translation = self._service.get_translation(default_language, key)

        # Если и там нет — возвращаем ключ
        if translation is None:
            logger.warning(
                "Перевод не найден: key=%s, language=%s, default=%s",
                key,
                self.language,
                default_language,
            )
            return key

        # Форматируем строку, если переданы параметры
        if kwargs:
            try:
                return translation.format(**kwargs)
            except KeyError as e:
                logger.error(
                    "Ошибка форматирования перевода: key=%s, language=%s, error=%s",
                    key,
                    self.language,
                    e,
                )
                return translation

        return translation

    # =========================================================================
    # Статические методы для обратной совместимости и удобства
    # Делегируют вызовы к глобальному LocalizationService
    # =========================================================================

    @staticmethod
    def is_enabled() -> bool:
        """Проверить, включена ли мультиязычность.

        Returns:
            True если в конфиге localization.enabled=true, иначе False.
        """
        return get_localization_service().config.enabled

    @staticmethod
    def get_available_languages() -> list[str]:
        """Получить список доступных языков.

        Returns:
            Список кодов языков (ru, en, zh и т.д.).
        """
        return list(get_localization_service().config.available_languages)

    @staticmethod
    def get_default_language() -> str:
        """Получить язык по умолчанию.

        Returns:
            Код языка по умолчанию (ru, en и т.д.).
        """
        return get_localization_service().config.default_language


def create_localization(language: str) -> Localization:
    """Создать объект Localization для указанного языка (factory function).

    Это основной способ создания Localization в production коде.
    Использует глобальный LocalizationService.

    Args:
        language: Код языка пользователя (ru, en и т.д.).

    Returns:
        Объект Localization для указанного языка.

    Example:
        l10n = create_localization("ru")
        message = l10n.get("start_message")
    """
    service = get_localization_service()
    return Localization(language, service)
