"""Сервисы генерации AI контента.

Этот модуль содержит сервисный слой для всех типов генераций AI.
Использует Template Method Pattern для переиспользования общей логики.

Архитектура:
- BaseGenerationService - абстрактный базовый класс с общим алгоритмом
- ChatGenerationService - сервис для текстовых диалогов
- ImageGenerationService - сервис для генерации изображений

Преимущества паттерна:
- DRY: вся общая логика (billing, cooldown, tracking) в одном месте
- SOLID: Single Responsibility, Open/Closed принципы
- Тестируемость: легко мокировать сервисы
- Масштабируемость: просто добавлять новые типы генераций
"""

from src.services.generation.base import BaseGenerationService, GenerationResult
from src.services.generation.chat import ChatGenerationService
from src.services.generation.image import ImageGenerationService

__all__ = [
    "BaseGenerationService",
    "ChatGenerationService",
    "GenerationResult",
    "ImageGenerationService",
]
