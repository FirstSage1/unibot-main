"""Проверка обоих документированных форматов настроек AI без реальных ключей."""

import os
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config.models import AIProvidersSettings


class IsolatedSettings(BaseSettings):
    """Изолированная модель для проверки загрузки временного dotenv-файла."""

    model_config = SettingsConfigDict(env_nested_delimiter="__")
    ai: AIProvidersSettings


@pytest.mark.parametrize("provider", ["routerai", "openrouter", "anymodel"])
@pytest.mark.parametrize("separator", ["_", "__"])
def test_documented_api_key_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    separator: str,
) -> None:
    """Имена из README и .env.example одинаково включают провайдера."""
    for name in tuple(os.environ):
        monkeypatch.delenv(name)
    dotenv = tmp_path / ".env"
    name = f"AI__{provider.upper()}{separator}API_KEY"
    dotenv.write_text(f"{name}=test-key\n", encoding="utf-8")

    settings = IsolatedSettings(_env_file=dotenv)

    key = getattr(settings.ai, f"{provider}_api_key")
    assert key.get_secret_value() == "test-key"
    assert getattr(settings.ai, f"has_{provider}")
