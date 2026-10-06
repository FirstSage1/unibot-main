"""Тесты для AppSettings из src/config/models.py.

Проверяет:
- Свойство is_production при разных значениях domain
- Корректную работу с опциональным доменом
"""

import pytest

from src.config.models import AppSettings


class TestAppSettings:
    """Тесты для класса AppSettings."""

    def test_is_production_with_domain_returns_true(self) -> None:
        """Проверить, что is_production возвращает True при наличии домена."""
        # Arrange
        settings = AppSettings(domain="example.com")

        # Act
        result = settings.is_production

        # Assert
        assert result is True

    def test_is_production_with_domain_https_returns_true(self) -> None:
        """Проверить, что is_production возвращает True при домене с протоколом."""
        # Arrange
        settings = AppSettings(domain="https://example.com")

        # Act
        result = settings.is_production

        # Assert
        assert result is True

    def test_is_production_with_domain_trailing_slash_returns_true(self) -> None:
        """Проверить, что is_production возвращает True при домене со слешем."""
        # Arrange
        settings = AppSettings(domain="https://example.com/")

        # Act
        result = settings.is_production

        # Assert
        assert result is True

    def test_is_production_without_domain_returns_false(self) -> None:
        """Проверить, что is_production возвращает False без домена."""
        # Arrange
        settings = AppSettings(domain=None)

        # Act
        result = settings.is_production

        # Assert
        assert result is False

    def test_is_production_default_value_returns_false(self) -> None:
        """Проверить, что is_production возвращает False при значении по умолчанию."""
        # Arrange
        settings = AppSettings()

        # Act
        result = settings.is_production

        # Assert
        assert result is False

    @pytest.mark.parametrize(
        "domain",
        [
            "example.com",
            "https://example.com",
            "http://example.com",
            "https://api.example.com",
            "example.com:8080",
            "https://example.com/",
            "subdomain.example.com",
        ],
    )
    def test_is_production_with_various_valid_domains(self, domain: str) -> None:
        """Проверить is_production с различными валидными форматами доменов.

        Args:
            domain: Тестируемый домен.
        """
        # Arrange
        settings = AppSettings(domain=domain)

        # Act
        result = settings.is_production

        # Assert
        assert result is True

    def test_domain_field_optional_allows_none(self) -> None:
        """Проверить, что поле domain является опциональным."""
        # Arrange & Act
        settings = AppSettings(domain=None)

        # Assert
        assert settings.domain is None

    def test_domain_field_stores_original_value(self) -> None:
        """Проверить, что поле domain сохраняет оригинальное значение."""
        # Arrange
        original_domain = "https://example.com/"

        # Act
        settings = AppSettings(domain=original_domain)

        # Assert
        assert settings.domain == original_domain
