"""Тесты для OpenAI-провайдера и фабрики адаптеров."""

from pydantic import SecretStr

from src.config.models import AIProvidersSettings
from src.providers.ai.openai_provider import OpenAIAdapter, OpenAIAdapterFactory


def test_openai_adapter_factory_creates_adapter_when_key_present() -> None:
    """Тест: фабрика создаёт адаптер когда API-ключ настроен."""
    settings = AIProvidersSettings(
        openrouter_api_key=SecretStr("sk-or-test-key"),
    )

    factory = OpenAIAdapterFactory(
        base_url="https://openrouter.ai/api/v1",
        api_key_attr="openrouter_api_key",
    )

    adapter = factory.create(settings)

    assert adapter is not None
    assert isinstance(adapter, OpenAIAdapter)
    assert adapter._api_key == "sk-or-test-key"
    assert adapter._base_url == "https://openrouter.ai/api/v1"


def test_openai_adapter_factory_returns_none_when_key_absent() -> None:
    """Тест: фабрика возвращает None когда API-ключ не настроен."""
    settings = AIProvidersSettings()  # Все ключи None

    factory = OpenAIAdapterFactory(
        base_url="https://openrouter.ai/api/v1",
        api_key_attr="openrouter_api_key",
    )

    adapter = factory.create(settings)

    assert adapter is None


def test_openai_adapter_factory_routerai() -> None:
    """Тест: фабрика работает для RouterAI с другим base_url."""
    settings = AIProvidersSettings(
        routerai_api_key=SecretStr("routerai-test-key"),
    )

    factory = OpenAIAdapterFactory(
        base_url="https://api.routerai.ru/v1",
        api_key_attr="routerai_api_key",
    )

    adapter = factory.create(settings)

    assert adapter is not None
    assert isinstance(adapter, OpenAIAdapter)
    assert adapter._api_key == "routerai-test-key"
    assert adapter._base_url == "https://api.routerai.ru/v1"


def test_openai_adapter_factory_multiple_keys() -> None:
    """Тест: фабрика выбирает правильный ключ из нескольких."""
    settings = AIProvidersSettings(
        openrouter_api_key=SecretStr("sk-or-key"),
        routerai_api_key=SecretStr("routerai-key"),
    )

    # Фабрика для OpenRouter
    openrouter_factory = OpenAIAdapterFactory(
        base_url="https://openrouter.ai/api/v1",
        api_key_attr="openrouter_api_key",
    )

    # Фабрика для RouterAI
    routerai_factory = OpenAIAdapterFactory(
        base_url="https://api.routerai.ru/v1",
        api_key_attr="routerai_api_key",
    )

    openrouter_adapter = openrouter_factory.create(settings)
    routerai_adapter = routerai_factory.create(settings)

    assert openrouter_adapter is not None
    assert openrouter_adapter._api_key == "sk-or-key"

    assert routerai_adapter is not None
    assert routerai_adapter._api_key == "routerai-key"


def test_openai_adapter_provider_name_with_base_url() -> None:
    """Тест: provider_name включает base_url когда он задан."""
    adapter = OpenAIAdapter(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
    )

    assert "openrouter.ai" in adapter.provider_name
    assert "openai-compatible" in adapter.provider_name


def test_openai_adapter_provider_name_without_base_url() -> None:
    """Тест: provider_name для стандартного OpenAI без base_url."""
    adapter = OpenAIAdapter(api_key="test-key")

    assert adapter.provider_name == "openai"


def test_openai_adapter_factory_creates_adapter_with_proxy() -> None:
    """Тест: фабрика создаёт адаптер с прокси когда proxy_url указан."""
    settings = AIProvidersSettings(
        openrouter_api_key=SecretStr("sk-or-test-key"),
    )

    factory = OpenAIAdapterFactory(
        base_url="https://openrouter.ai/api/v1",
        api_key_attr="openrouter_api_key",
    )

    adapter = factory.create(settings, proxy_url="http://proxy.example.com:8080")

    assert adapter is not None
    assert isinstance(adapter, OpenAIAdapter)
    assert adapter._proxy_url == "http://proxy.example.com:8080"


def test_openai_adapter_factory_creates_adapter_without_proxy() -> None:
    """Тест: фабрика создаёт адаптер без прокси когда proxy_url не указан."""
    settings = AIProvidersSettings(
        openrouter_api_key=SecretStr("sk-or-test-key"),
    )

    factory = OpenAIAdapterFactory(
        base_url="https://openrouter.ai/api/v1",
        api_key_attr="openrouter_api_key",
    )

    adapter = factory.create(settings, proxy_url=None)

    assert adapter is not None
    assert isinstance(adapter, OpenAIAdapter)
    assert adapter._proxy_url is None


def test_openai_adapter_with_proxy_url() -> None:
    """Тест: OpenAIAdapter сохраняет proxy_url когда он указан."""
    adapter = OpenAIAdapter(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        proxy_url="http://proxy.example.com:8080",
    )

    assert adapter._proxy_url == "http://proxy.example.com:8080"


def test_openai_adapter_without_proxy_url() -> None:
    """Тест: OpenAIAdapter работает без proxy_url."""
    adapter = OpenAIAdapter(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
    )

    assert adapter._proxy_url is None


def test_extract_image_from_images_field_with_data_url() -> None:
    """Тест: извлечение изображения из поля images с data URL."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    # Создаём mock message с полем images (RouterAI формат)
    message = MagicMock()
    message.images = ["data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"]
    message.content = None

    result = adapter._extract_image_from_message(message)

    assert result == "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"


def test_extract_image_from_images_field_with_base64() -> None:
    """Тест: извлечение изображения из поля images с чистым base64."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    # Создаём mock message с base64 без data URL префикса
    message = MagicMock()
    message.images = ["iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"]
    message.content = None

    result = adapter._extract_image_from_message(message)

    # Должен добавить data URL префикс
    assert result == "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"


def test_extract_image_from_images_field_empty_list() -> None:
    """Тест: пустой список images возвращает None."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = []
    message.content = None

    result = adapter._extract_image_from_message(message)

    assert result is None


def test_extract_image_from_images_field_none() -> None:
    """Тест: отсутствие поля images возвращает None."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = None
    message.content = None

    result = adapter._extract_image_from_message(message)

    assert result is None


def test_extract_image_from_images_field_not_list() -> None:
    """Тест: images не список возвращает None."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = "not a list"
    message.content = None

    result = adapter._extract_image_from_message(message)

    assert result is None


def test_extract_image_from_images_field_not_string() -> None:
    """Тест: элемент images не строка возвращает None."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = [123]  # Число вместо строки
    message.content = None

    result = adapter._extract_image_from_message(message)

    assert result is None


def test_extract_image_from_content_string_with_data_url() -> None:
    """Тест: извлечение изображения из content как строки с data URL."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = None
    message.content = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"

    result = adapter._extract_image_from_message(message)

    assert result == "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"


def test_extract_image_from_content_multipart_image_url_format() -> None:
    """Тест: извлечение изображения из content multipart (image_url формат)."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = None
    message.content = [
        {"type": "text", "text": "Вот ваше изображение"},
        {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"
            },
        },
    ]

    result = adapter._extract_image_from_message(message)

    assert result == "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"


def test_extract_image_from_content_multipart_image_data_format() -> None:
    """Тест: извлечение изображения из content multipart (image data формат)."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = None
    message.content = [
        {"type": "text", "text": "Вот ваше изображение"},
        {"type": "image", "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"},
    ]

    result = adapter._extract_image_from_message(message)

    # Должен добавить data URL префикс
    assert result == "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"


def test_extract_image_from_content_multipart_no_image() -> None:
    """Тест: content multipart без изображений возвращает None."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = None
    message.content = [
        {"type": "text", "text": "Только текст"},
        {"type": "text", "text": "Ещё текст"},
    ]

    result = adapter._extract_image_from_message(message)

    assert result is None


def test_extract_image_from_content_none() -> None:
    """Тест: content=None возвращает None."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = None
    message.content = None

    result = adapter._extract_image_from_message(message)

    assert result is None


def test_extract_image_from_content_empty_string() -> None:
    """Тест: пустой content возвращает None."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = None
    message.content = ""

    result = adapter._extract_image_from_message(message)

    assert result is None


def test_extract_image_from_content_text_without_data_url() -> None:
    """Тест: текстовый content без data URL возвращает None."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = None
    message.content = "Просто текст без изображения"

    result = adapter._extract_image_from_message(message)

    assert result is None


def test_extract_image_priority_images_over_content() -> None:
    """Тест: поле images имеет приоритет над content."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    # И images, и content содержат изображения
    message.images = ["data:image/png;base64,FROM_IMAGES_FIELD"]
    message.content = "data:image/png;base64,FROM_CONTENT_FIELD"

    result = adapter._extract_image_from_message(message)

    # Должно вернуться изображение из images
    assert result == "data:image/png;base64,FROM_IMAGES_FIELD"


def test_extract_from_images_field_directly() -> None:
    """Тест: _extract_from_images_field работает напрямую."""
    from unittest.mock import MagicMock

    adapter = OpenAIAdapter(api_key="test-key")

    message = MagicMock()
    message.images = ["data:image/jpeg;base64,/9j/4AAQSkZJRg"]

    result = adapter._extract_from_images_field(message)

    assert result == "data:image/jpeg;base64,/9j/4AAQSkZJRg"


def test_extract_from_content_directly_with_string() -> None:
    """Тест: _extract_from_content работает напрямую со строкой."""
    adapter = OpenAIAdapter(api_key="test-key")

    content = "data:image/webp;base64,UklGRiQAAABXRUJQVlA4"
    result = adapter._extract_from_content(content)

    assert result == "data:image/webp;base64,UklGRiQAAABXRUJQVlA4"


def test_extract_from_content_directly_with_list() -> None:
    """Тест: _extract_from_content работает напрямую со списком."""
    adapter = OpenAIAdapter(api_key="test-key")

    content = [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,TEST123"},
        }
    ]
    result = adapter._extract_from_content(content)

    assert result == "data:image/png;base64,TEST123"


def test_extract_from_content_directly_empty_list() -> None:
    """Тест: _extract_from_content с пустым списком возвращает None."""
    adapter = OpenAIAdapter(api_key="test-key")

    result = adapter._extract_from_content([])

    assert result is None


def test_extract_from_content_malformed_image_url_structure() -> None:
    """Тест: _extract_from_content с некорректной структурой image_url."""
    adapter = OpenAIAdapter(api_key="test-key")

    # image_url не словарь
    content = [{"type": "image_url", "image_url": "not a dict"}]
    result = adapter._extract_from_content(content)

    assert result is None


def test_extract_from_content_missing_url_in_image_url() -> None:
    """Тест: _extract_from_content без поля url в image_url."""
    adapter = OpenAIAdapter(api_key="test-key")

    content = [{"type": "image_url", "image_url": {}}]
    result = adapter._extract_from_content(content)

    assert result is None


def test_extract_from_content_image_type_missing_data() -> None:
    """Тест: _extract_from_content с type=image без data."""
    adapter = OpenAIAdapter(api_key="test-key")

    content = [{"type": "image"}]
    result = adapter._extract_from_content(content)

    assert result is None


def test_extract_from_content_non_dict_elements() -> None:
    """Тест: _extract_from_content игнорирует не-словарные элементы."""
    adapter = OpenAIAdapter(api_key="test-key")

    content = [
        "not a dict",
        123,
        None,
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,OK"}},
    ]
    result = adapter._extract_from_content(content)

    # Должен найти правильный элемент, пропустив некорректные
    assert result == "data:image/png;base64,OK"
