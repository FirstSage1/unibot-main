"""Проверки HTTP-сессии бота без запросов к Telegram."""

import pytest

from src.bot.loader import SystemProxySession, create_bot


@pytest.mark.asyncio
async def test_bot_uses_system_proxy_and_closes_session() -> None:
    """Сессия учитывает системный прокси и освобождает соединения."""
    bot = create_bot("123456:TEST")
    assert isinstance(bot.session, SystemProxySession)
    client = await bot.session.create_session()
    assert client.trust_env
    assert await bot.session.create_session() is client

    await bot.session.close()
    assert client.closed

    reopened_client = await bot.session.create_session()
    assert reopened_client is not client
    assert reopened_client.trust_env
    await bot.session.close()
    assert reopened_client.closed


@pytest.mark.asyncio
async def test_bot_uses_explicit_proxy() -> None:
    """Явный HTTP-прокси передаётся в клиент Telegram."""
    bot = create_bot("123456:TEST", proxy_url="http://proxy.example:8080")
    assert isinstance(bot.session, SystemProxySession)
    client = await bot.session.create_session()

    assert client._default_proxy is not None
    assert str(client._default_proxy) == "http://proxy.example:8080"

    await bot.session.close()
