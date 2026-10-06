"""Тесты для системы локализации (i18n).

Модуль тестирует:
- LocalizationConfig dataclass
- LocalizationService — сервис хранения переводов
- Localization — класс для получения переводов
- load_translations_from_yaml — загрузка из файлов
- create_localization — factory function

Архитектура тестов следует принципам Dependency Injection:
- Все зависимости инжектируются через конструктор
- Не используются глобальные состояния
- Легко мокировать любые компоненты
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.utils.i18n import (
    Localization,
    LocalizationConfig,
    LocalizationService,
    load_translations_from_yaml,
)

# ==============================================================================
# ФИКСТУРЫ
# ==============================================================================


@pytest.fixture
def mock_translations() -> dict[str, dict[str, str]]:
    """Тестовые переводы для двух языков."""
    return {
        "ru": {
            "start_message": "Привет! Я бот.",
            "hello_user": "Привет, {name}!",
            "language_command": "Выберите язык:",
            "error_unknown": "Произошла ошибка.",
            "language_name_ru": "🇷🇺 Русский",
            "language_name_en": "🇬🇧 English",
        },
        "en": {
            "start_message": "Hello! I'm a bot.",
            "hello_user": "Hello, {name}!",
            "language_command": "Choose language:",
            "error_unknown": "An error occurred.",
            "language_name_ru": "🇷🇺 Russian",
            "language_name_en": "🇬🇧 English",
        },
    }


@pytest.fixture
def localization_config() -> LocalizationConfig:
    """Базовый конфиг локализации для тестов."""
    return LocalizationConfig(
        enabled=True,
        default_language="ru",
        available_languages=("ru", "en"),
    )


@pytest.fixture
def localization_service(
    mock_translations: dict[str, dict[str, str]],
    localization_config: LocalizationConfig,
) -> LocalizationService:
    """Сервис локализации с тестовыми данными."""
    return LocalizationService(
        translations=mock_translations,
        config=localization_config,
    )


# ==============================================================================
# ТЕСТЫ LocalizationConfig
# ==============================================================================


def test_localization_config_default_values() -> None:
    """Тест: LocalizationConfig имеет правильные значения по умолчанию.

    По умолчанию локализация ОТКЛЮЧЕНА (enabled=False) и доступен только русский язык.
    Это соответствует дефолтным значениям в src/utils/i18n.py и config.yaml.
    """
    config = LocalizationConfig()

    assert config.enabled is False
    assert config.default_language == "ru"
    assert config.available_languages == ("ru",)


def test_localization_config_custom_values() -> None:
    """Тест: LocalizationConfig принимает кастомные значения."""
    config = LocalizationConfig(
        enabled=False,
        default_language="en",
        available_languages=("en", "zh", "de"),
    )

    assert config.enabled is False
    assert config.default_language == "en"
    assert config.available_languages == ("en", "zh", "de")


def test_localization_config_is_immutable() -> None:
    """Тест: LocalizationConfig неизменяемый (frozen=True)."""
    config = LocalizationConfig()

    with pytest.raises(AttributeError):
        config.enabled = False


# ==============================================================================
# ТЕСТЫ LocalizationService
# ==============================================================================


def test_localization_service_stores_translations(
    localization_service: LocalizationService,
    mock_translations: dict[str, dict[str, str]],
) -> None:
    """Тест: LocalizationService хранит переводы."""
    assert localization_service.translations == mock_translations


def test_localization_service_stores_config(
    localization_service: LocalizationService,
    localization_config: LocalizationConfig,
) -> None:
    """Тест: LocalizationService хранит конфигурацию."""
    assert localization_service.config == localization_config


def test_localization_service_get_translation(
    localization_service: LocalizationService,
) -> None:
    """Тест: get_translation возвращает перевод."""
    result = localization_service.get_translation("ru", "start_message")
    assert result == "Привет! Я бот."

    result = localization_service.get_translation("en", "start_message")
    assert result == "Hello! I'm a bot."


def test_localization_service_get_translation_returns_none_for_missing_key(
    localization_service: LocalizationService,
) -> None:
    """Тест: get_translation возвращает None для несуществующего ключа."""
    result = localization_service.get_translation("ru", "nonexistent_key")
    assert result is None


def test_localization_service_get_translation_returns_none_for_missing_language(
    localization_service: LocalizationService,
) -> None:
    """Тест: get_translation возвращает None для несуществующего языка."""
    result = localization_service.get_translation("fr", "start_message")
    assert result is None


# ==============================================================================
# ТЕСТЫ Localization — инициализация
# ==============================================================================


def test_localization_initialization_with_valid_language(
    localization_service: LocalizationService,
) -> None:
    """Тест: инициализация Localization с валидным языком."""
    l10n = Localization("ru", localization_service)
    assert l10n.language == "ru"

    l10n = Localization("en", localization_service)
    assert l10n.language == "en"


def test_localization_initialization_with_invalid_language_uses_default(
    localization_service: LocalizationService,
) -> None:
    """Тест: инициализация с невалидным языком использует default_language."""
    l10n = Localization("fr", localization_service)

    # Должен использовать default_language из конфига
    assert l10n.language == "ru"


# ==============================================================================
# ТЕСТЫ Localization — получение переводов
# ==============================================================================


def test_localization_get_returns_correct_translation(
    localization_service: LocalizationService,
) -> None:
    """Тест: get() возвращает правильный перевод для текущего языка."""
    l10n_ru = Localization("ru", localization_service)
    l10n_en = Localization("en", localization_service)

    assert l10n_ru.get("start_message") == "Привет! Я бот."
    assert l10n_en.get("start_message") == "Hello! I'm a bot."


def test_localization_get_falls_back_to_default_language(
    mock_translations: dict[str, dict[str, str]],
) -> None:
    """Тест: get() использует default_language если перевод не найден."""
    # Добавляем язык с неполными переводами
    translations = mock_translations.copy()
    translations["es"] = {
        "start_message": "¡Hola! Soy un bot.",
        # language_command отсутствует
    }

    config = LocalizationConfig(
        enabled=True,
        default_language="ru",
        available_languages=("ru", "en", "es"),
    )
    service = LocalizationService(translations=translations, config=config)
    l10n = Localization("es", service)

    # Перевод есть на испанском
    assert l10n.get("start_message") == "¡Hola! Soy un bot."

    # Перевода нет на испанском — используем русский (default)
    assert l10n.get("language_command") == "Выберите язык:"


def test_localization_get_returns_key_if_not_found(
    localization_service: LocalizationService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: get() возвращает ключ если перевод отсутствует полностью."""
    l10n = Localization("ru", localization_service)

    result = l10n.get("nonexistent_key")

    assert result == "nonexistent_key"

    # Должно быть предупреждение в логах
    assert any(
        "Перевод не найден: key=nonexistent_key" in record.message
        for record in caplog.records
    )


# ==============================================================================
# ТЕСТЫ Localization — форматирование строк
# ==============================================================================


def test_localization_get_with_formatting(
    localization_service: LocalizationService,
) -> None:
    """Тест: get() правильно форматирует строки с параметрами."""
    l10n_ru = Localization("ru", localization_service)
    l10n_en = Localization("en", localization_service)

    assert l10n_ru.get("hello_user", name="Алиса") == "Привет, Алиса!"
    assert l10n_en.get("hello_user", name="Alice") == "Hello, Alice!"


def test_localization_get_formatting_error_returns_unformatted_string(
    localization_service: LocalizationService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: ошибка форматирования возвращает неотформатированную строку."""
    l10n = Localization("ru", localization_service)

    # Строка требует параметр {name}, но мы передаём неправильный параметр.
    # Это вызовет KeyError при вызове str.format().
    # Примечание: если kwargs пустой, format() не вызывается вовсе.
    result = l10n.get("hello_user", wrong_param="test")  # name отсутствует

    # Должна вернуться строка без форматирования
    assert result == "Привет, {name}!"

    # Должна быть ошибка в логах
    assert any(
        "Ошибка форматирования перевода" in record.message for record in caplog.records
    )


def test_localization_get_formatting_with_extra_params_ignores_them(
    localization_service: LocalizationService,
) -> None:
    """Тест: лишние параметры форматирования игнорируются."""
    l10n = Localization("ru", localization_service)

    # Передаём лишний параметр
    result = l10n.get("hello_user", name="Алиса", age=25)

    assert result == "Привет, Алиса!"


# ==============================================================================
# ТЕСТЫ Localization — статические методы
# ==============================================================================


def test_localization_is_enabled_returns_config_value(
    localization_service: LocalizationService,
) -> None:
    """Тест: is_enabled() возвращает значение из конфига."""
    # Патчим глобальный сервис
    with patch(
        "src.utils.i18n.get_localization_service",
        return_value=localization_service,
    ):
        assert Localization.is_enabled() is True


def test_localization_get_available_languages_returns_config_value(
    localization_service: LocalizationService,
) -> None:
    """Тест: get_available_languages() возвращает список из конфига."""
    with patch(
        "src.utils.i18n.get_localization_service",
        return_value=localization_service,
    ):
        result = Localization.get_available_languages()
        assert result == ["ru", "en"]


def test_localization_get_default_language_returns_config_value(
    localization_service: LocalizationService,
) -> None:
    """Тест: get_default_language() возвращает default_language из конфига."""
    with patch(
        "src.utils.i18n.get_localization_service",
        return_value=localization_service,
    ):
        result = Localization.get_default_language()
        assert result == "ru"


# ==============================================================================
# ТЕСТЫ load_translations_from_yaml
# ==============================================================================


def test_load_translations_from_yaml_loads_all_languages(
    tmp_path: Path,
) -> None:
    """Тест: load_translations_from_yaml загружает все доступные языки."""
    # Создаём временную папку с файлами переводов
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()

    ru_file = locales_dir / "ru.yaml"
    en_file = locales_dir / "en.yaml"

    ru_translations = {"start_message": "Привет!", "key1": "Значение1"}
    en_translations = {"start_message": "Hello!", "key1": "Value1"}

    ru_file.write_text(yaml.dump(ru_translations), encoding="utf-8")
    en_file.write_text(yaml.dump(en_translations), encoding="utf-8")

    result = load_translations_from_yaml(
        locales_dir=locales_dir,
        available_languages=["ru", "en"],
    )

    assert "ru" in result
    assert "en" in result
    assert result["ru"]["start_message"] == "Привет!"
    assert result["en"]["start_message"] == "Hello!"


def test_load_translations_from_yaml_handles_missing_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: load_translations_from_yaml обрабатывает отсутствующий файл."""
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()

    # Создаём только ru.yaml
    ru_file = locales_dir / "ru.yaml"
    ru_translations = {"start_message": "Привет!"}
    ru_file.write_text(yaml.dump(ru_translations), encoding="utf-8")

    # Запрашиваем ru и fr (fr не существует)
    result = load_translations_from_yaml(
        locales_dir=locales_dir,
        available_languages=["ru", "fr"],
    )

    # ru должен загрузиться
    assert "ru" in result
    # fr не должен загрузиться
    assert "fr" not in result

    # Должна быть ошибка в логах
    assert any("Файл перевода не найден" in record.message for record in caplog.records)


def test_load_translations_from_yaml_handles_invalid_yaml(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: load_translations_from_yaml обрабатывает невалидный YAML."""
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()

    # Создаём файл с невалидным YAML
    invalid_file = locales_dir / "ru.yaml"
    invalid_file.write_text("invalid: yaml: content: [[[", encoding="utf-8")

    # Создаём валидный en.yaml
    en_file = locales_dir / "en.yaml"
    en_translations = {"start_message": "Hello!"}
    en_file.write_text(yaml.dump(en_translations), encoding="utf-8")

    result = load_translations_from_yaml(
        locales_dir=locales_dir,
        available_languages=["ru", "en"],
    )

    # ru не должен загрузиться
    assert "ru" not in result
    # en должен загрузиться
    assert "en" in result

    # Должна быть ошибка в логах
    assert any(
        "Ошибка загрузки переводов" in record.message for record in caplog.records
    )


def test_load_translations_from_yaml_raises_if_no_languages_loaded(
    tmp_path: Path,
) -> None:
    """Тест: load_translations_from_yaml бросает RuntimeError без переводов."""
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()

    # Указываем языки, для которых нет файлов
    with pytest.raises(RuntimeError) as exc_info:
        load_translations_from_yaml(
            locales_dir=locales_dir,
            available_languages=["fr", "de"],
        )

    assert "Не удалось загрузить ни одного языка" in str(exc_info.value)


# ==============================================================================
# ТЕСТЫ Edge Cases
# ==============================================================================


def test_localization_get_with_empty_string_value(
    mock_translations: dict[str, dict[str, str]],
    localization_config: LocalizationConfig,
) -> None:
    """Тест: get() правильно обрабатывает пустую строку как значение."""
    translations = mock_translations.copy()
    translations["ru"]["empty_key"] = ""

    service = LocalizationService(translations=translations, config=localization_config)
    l10n = Localization("ru", service)

    # Пустая строка — валидный перевод
    result = l10n.get("empty_key")
    assert result == ""


def test_localization_works_with_empty_translations(
    localization_config: LocalizationConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Тест: Localization работает если переводы пусты."""
    service = LocalizationService(translations={}, config=localization_config)
    l10n = Localization("ru", service)

    # Все запросы должны возвращать ключ
    result = l10n.get("start_message")
    assert result == "start_message"

    # Должно быть предупреждение
    assert any("Перевод не найден" in record.message for record in caplog.records)
