"""Модели базы данных (таблицы).

Каждая модель — это класс Python, который соответствует таблице в БД.
SQLAlchemy автоматически преобразует объекты в SQL-запросы.

Все модели должны наследоваться от Base (из db.base).
"""

from src.db.models.broadcast import Broadcast, BroadcastStatus, ParseMode
from src.db.models.generation import Generation, GenerationDBStatus
from src.db.models.message import Message
from src.db.models.payment import Payment, PaymentProvider, PaymentStatus
from src.db.models.referral import Referral
from src.db.models.subscription import Subscription, SubscriptionStatus
from src.db.models.transaction import Transaction, TransactionType
from src.db.models.user import User

__all__ = [
    "Broadcast",
    "BroadcastStatus",
    "Generation",
    "GenerationDBStatus",
    "Message",
    "ParseMode",
    "Payment",
    "PaymentProvider",
    "PaymentStatus",
    "Referral",
    "Subscription",
    "SubscriptionStatus",
    "Transaction",
    "TransactionType",
    "User",
]
