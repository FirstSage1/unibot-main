"""Утилиты для работы с Telegram webhook.

Этот модуль содержит логику:
- Нормализации домена из APP__DOMAIN
- Формирования webhook URL
- Проверки SSL-сертификата
- Установки webhook с retry-логикой
- Удаления webhook при переходе в polling mode
"""

import asyncio
import socket
import ssl
from typing import Any
from urllib.parse import urljoin, urlparse

from aiogram import Bot

from src.utils.logging import get_logger

logger = get_logger(__name__)

# Константы для webhook
WEBHOOK_PATH = "/api/telegram/webhook"
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]  # Exponential backoff: 1s, 2s, 4s

# Известные фейковые сертификаты (self-signed от ingress controllers)
FAKE_CERT_ISSUERS = [
    "kubernetes ingress controller fake certificate",
    "acme co",
    "ingress-nginx",
]


def normalize_domain(domain: str) -> str:
    """Нормализовать домен для webhook URL.

    Логика нормализации (из PRD):
    1. Добавляет https:// если не указан протокол
    2. Удаляет лишние слеши в конце
    3. Извлекает чистый домен

    Args:
        domain: Домен из APP__DOMAIN (example.com или https://example.com/).

    Returns:
        Нормализованный домен с протоколом (https://example.com).

    Example:
        >>> normalize_domain("example.com")
        "https://example.com"
        >>> normalize_domain("https://example.com/")
        "https://example.com"
        >>> normalize_domain("http://example.com")
        "http://example.com"
    """
    domain = domain.strip()

    # Добавляем https:// если протокол не указан
    if not domain.startswith(("http://", "https://")):
        domain = f"https://{domain}"

    # Парсим URL для нормализации
    parsed = urlparse(domain)

    # Формируем нормализованный домен: scheme://netloc
    # Удаляем path, query, fragment если они были указаны
    return f"{parsed.scheme}://{parsed.netloc}"


def build_webhook_url(domain: str) -> str:
    """Построить полный webhook URL.

    Args:
        domain: Нормализованный домен (https://example.com).

    Returns:
        Полный webhook URL (https://example.com/api/telegram/webhook).

    Example:
        >>> build_webhook_url("https://example.com")
        "https://example.com/api/telegram/webhook"
    """
    # urljoin корректно обрабатывает случаи с/без слеша в конце
    return urljoin(domain, WEBHOOK_PATH)


def _extract_cert_issuer(cert: dict[str, Any]) -> str:
    """Извлечь issuer из SSL-сертификата.

    Args:
        cert: Сертификат из getpeercert().

    Returns:
        Строка с CN и O из issuer в нижнем регистре.
    """
    issuer_parts: list[str] = []
    for rdn in cert.get("issuer", ()):
        for key, value in rdn:
            if key in ("commonName", "organizationName"):
                issuer_parts.append(str(value).lower())
    return " ".join(issuer_parts)


def _is_fake_certificate(issuer_str: str) -> bool:
    """Проверить, является ли сертификат фейковым.

    Args:
        issuer_str: Строка issuer сертификата.

    Returns:
        True если сертификат фейковый (self-signed от ingress).
    """
    return any(pattern in issuer_str for pattern in FAKE_CERT_ISSUERS)


def _check_ssl_certificate(domain: str) -> tuple[bool, str]:
    """Проверить SSL-сертификат домена.

    Telegram требует валидный SSL-сертификат для webhook.
    Проверяет что сертификат существует и не является фейковым.

    Args:
        domain: Домен для проверки (https://example.com).

    Returns:
        Кортеж (is_valid, message).
    """
    parsed = urlparse(domain)
    hostname = parsed.netloc or parsed.path

    try:
        context = ssl.create_default_context()
        with (
            socket.create_connection((hostname, 443), timeout=10) as sock,
            context.wrap_socket(sock, server_hostname=hostname) as ssock,
        ):
            cert = ssock.getpeercert()

            if not cert:
                return False, "SSL-сертификат отсутствует"

            issuer_str = _extract_cert_issuer(cert)

            if _is_fake_certificate(issuer_str):
                return False, (
                    f"Фейковый SSL-сертификат: '{issuer_str}'. "
                    "Это временный сертификат от Kubernetes/Ingress. "
                    "Подождите 5-10 минут пока сгенерируется Let's Encrypt "
                    "сертификат, затем перезапустите приложение."
                )

            return True, "OK"

    except ssl.SSLCertVerificationError as e:
        return False, f"SSL-сертификат невалидный: {e}"
    except ssl.SSLError as e:
        return False, f"SSL ошибка: {e}"
    except TimeoutError:
        return False, "Таймаут при проверке SSL-сертификата"
    except OSError as e:
        return False, f"Ошибка подключения: {e}"


async def setup_webhook(bot: Bot, domain: str) -> bool:
    """Установить webhook с retry-логикой.

    Согласно PRD (раздел 2.3):
    - До 3 попыток с exponential backoff (1s, 2s, 4s)
    - При временных ошибках (5xx, timeout, сетевые) — повторяем
    - При ошибках валидации (4xx, некорректный URL) — фатально

    Args:
        bot: Инстанс Telegram бота.
        domain: Нормализованный домен (https://example.com).

    Returns:
        True если webhook установлен успешно, False при критической ошибке.

    Raises:
        RuntimeError: При невалидном SSL-сертификате или ошибках валидации (4xx).
    """
    webhook_url = build_webhook_url(domain)

    logger.info("Установка webhook: %s", webhook_url)

    # Проверяем SSL-сертификат перед установкой webhook.
    # Невалидный SSL — критическая ошибка, приложение не может работать.
    ssl_valid, ssl_message = _check_ssl_certificate(domain)
    if not ssl_valid:
        logger.error(
            "✗ SSL-сертификат невалидный для %s: %s",
            domain,
            ssl_message,
        )
        raise RuntimeError(
            f"Невалидный SSL-сертификат для {domain}: {ssl_message}. "
            "Telegram требует валидный SSL для webhook. "
            "Исправьте сертификат и перезапустите приложение."
        )

    for attempt in range(MAX_RETRIES):
        try:
            # Устанавливаем webhook с явным списком allowed_updates.
            # ВАЖНО: pre_checkout_query НЕ включён по умолчанию в Telegram API,
            # без него платежи Stars не работают.
            result = await bot.set_webhook(
                url=webhook_url,
                drop_pending_updates=False,  # Не удаляем непрочитанные update
                allowed_updates=[
                    "message",
                    "edited_message",
                    "callback_query",
                    "inline_query",
                    "chosen_inline_result",
                    "pre_checkout_query",  # Обязательно для Telegram Stars
                    "my_chat_member",
                    "chat_member",
                ],
            )

            if result:
                logger.info("✓ Webhook успешно установлен: %s", webhook_url)
                return True

            # Telegram вернул False — что-то не так
            logger.warning(
                "Telegram вернул False при установке webhook (попытка %d/%d)",
                attempt + 1,
                MAX_RETRIES,
            )

        except Exception as e:
            error_message = str(e)

            # Проверяем тип ошибки по сообщению
            client_error_keywords = [
                "400",
                "401",
                "403",
                "404",
                "invalid url",
                "bad request",
            ]
            is_client_error = any(
                keyword in error_message.lower() for keyword in client_error_keywords
            )

            if is_client_error:
                # 4xx ошибка — проблема с URL или конфигурацией
                logger.error(
                    "✗ Критическая ошибка валидации webhook: %s", error_message
                )
                raise RuntimeError(
                    f"Некорректный webhook URL или конфигурация: {error_message}"
                ) from e

            # Временная ошибка (5xx, сеть, таймаут)
            logger.warning(
                "Временная ошибка установки webhook (попытка %d/%d): %s",
                attempt + 1,
                MAX_RETRIES,
                error_message,
            )

        # Если не последняя попытка — ждём перед retry
        if attempt < MAX_RETRIES - 1:
            delay = RETRY_DELAYS[attempt]
            logger.info("Повтор через %d секунд...", delay)
            await asyncio.sleep(delay)

    # Все попытки исчерпаны
    logger.error(
        "✗ Не удалось установить webhook после %d попыток. "
        "Приложение не может работать в PROD без webhook.",
        MAX_RETRIES,
    )
    return False


async def remove_webhook(bot: Bot) -> None:
    """Удалить webhook (переход в polling mode).

    Args:
        bot: Инстанс Telegram бота.
    """
    logger.info("Удаление webhook (переход в polling mode)...")

    try:
        result = await bot.delete_webhook(drop_pending_updates=False)
        if result:
            logger.info("✓ Webhook удалён, используется polling mode")
        else:
            logger.warning("Telegram вернул False при удалении webhook")
    except Exception:
        logger.exception("Ошибка при удалении webhook")
        # Не критично — продолжаем работу
