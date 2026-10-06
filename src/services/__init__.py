"""Сервисы приложения.

Этот пакет содержит бизнес-логику приложения.

Сервисы:
- AIService — оркестрация AI-генераций, fallback-логика, управление провайдерами.
- BillingService — управление токенами, лимитами и биллингом.
"""

from src.core.exceptions import InsufficientBalanceError
from src.services.ai_service import AIService
from src.services.billing_service import (
    BalanceInfo,
    BillingService,
    GenerationCost,
    create_billing_service,
)

__all__ = [
    "AIService",
    "BalanceInfo",
    "BillingService",
    "GenerationCost",
    "InsufficientBalanceError",
    "create_billing_service",
]
