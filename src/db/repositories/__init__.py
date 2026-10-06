"""Репозитории для работы с данными.

Репозиторий — это паттерн, который инкапсулирует логику доступа к данным.
Вместо прямых SQL-запросов в хендлерах используем методы репозитория.

Преимущества:
- Вся логика работы с БД в одном месте
- Легко тестировать (можно подменить репозиторий на mock)
- Хендлеры не зависят от деталей реализации БД
"""

from src.db.repositories.broadcast_repo import BroadcastRepository
from src.db.repositories.generation_repo import GenerationRepository
from src.db.repositories.message_repo import MessageRepository
from src.db.repositories.payment_repo import PaymentRepository
from src.db.repositories.referral_repo import ReferralRepository
from src.db.repositories.subscription_repo import SubscriptionRepository
from src.db.repositories.transaction_repo import TransactionRepository
from src.db.repositories.user_repo import UserRepository

__all__ = [
    "BroadcastRepository",
    "GenerationRepository",
    "MessageRepository",
    "PaymentRepository",
    "ReferralRepository",
    "SubscriptionRepository",
    "TransactionRepository",
    "UserRepository",
]
