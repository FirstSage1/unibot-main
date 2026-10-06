"""Модель для отслеживания истории генераций AI.

Эта модель используется для:
- Проверки cooldown между генерациями (защита от спама)
- Аналитики и статистики использования (какие модели популярны)
- Аудита всех генераций пользователей (для расследования проблем)
- Мониторинга скорости и качества генераций (метрики производительности)

Отличие от Message:
- Message хранит только историю чат-диалогов (user/assistant messages)
- Generation хранит все типы генераций: chat, image, tts, stt, image_edit

Пример использования:
    # Запись начала генерации
    generation = Generation(
        user_id=user.id,
        generation_type=GenerationType.IMAGE,
        model_key="flux-dev",
        status=GenerationDBStatus.PENDING,
    )
    session.add(generation)
    await session.commit()

    # После завершения — обновление статуса
    generation.status = GenerationDBStatus.COMPLETED
    generation.completed_at = func.now()
    await session.commit()

    # Проверка cooldown
    last_gen = await session.execute(
        select(Generation)
        .where(
            Generation.user_id == user_id,
            Generation.generation_type == gen_type,
        )
        .order_by(Generation.created_at.desc())
        .limit(1)
    )
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing_extensions import override

from src.db.models_base import Base

if TYPE_CHECKING:
    from src.db.models.transaction import Transaction
    from src.db.models.user import User


class GenerationDBStatus(StrEnum):
    """Статус генерации в базе данных.

    Отличие от GenerationStatus из base.py:
    - GenerationStatus — статус от AI-провайдера (SUCCESS, PROCESSING, FAILED, CANCELED)
    - GenerationDBStatus — статус записи в БД (PENDING, COMPLETED, FAILED)

    PENDING включает как ожидание старта, так и PROCESSING от провайдера.
    COMPLETED соответствует SUCCESS от провайдера.
    FAILED объединяет FAILED и CANCELED от провайдера.

    Упрощённая схема для БД — нам не нужна вся детализация провайдеров.
    """

    # Генерация запущена, но ещё не завершена
    # Может быть в очереди или активно выполняться
    PENDING = "pending"

    # Генерация завершена успешно, результат получен
    COMPLETED = "completed"

    # Генерация завершилась с ошибкой или была отменена
    FAILED = "failed"


class Generation(Base):
    """Запись о генерации AI.

    Каждая запись представляет один запрос к AI-модели.
    Записывается при запуске генерации, обновляется при завершении.

    Используется для:
    1. Cooldown — проверка времени с последней генерации этого типа
    2. Аналитика — какие модели/типы используются чаще
    3. Мониторинг — средняя длительность генераций
    4. Отладка — история всех запросов для расследования проблем

    Attributes:
        id: Уникальный идентификатор генерации.
        user_id: ID пользователя, запустившего генерацию.
        generation_type: Тип генерации (chat, image, image_edit, tts, stt).
            Используется для группировки и cooldown-проверок.
        model_key: Ключ модели из config.yaml (например, "gpt-4o", "flux-dev").
            Позволяет видеть, какие модели используются в каждом типе.
        status: Текущий статус генерации (pending, completed, failed).
        created_at: Время начала генерации (для cooldown и аналитики).
        completed_at: Время завершения генерации (для расчёта длительности).
            None если генерация ещё не завершена.
        user: Связь с моделью User (для доступа к данным пользователя).

    Индексы:
        - (user_id, generation_type, created_at) — для быстрой проверки cooldown
        - created_at — для статистики по времени
    """

    __tablename__ = "generations"

    # Первичный ключ
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # ID пользователя из таблицы users
    # BigInteger потому что telegram_id может быть очень большим числом
    # index=True для быстрого поиска генераций конкретного пользователя
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Тип генерации: "chat", "image", "image_edit", "tts", "stt"
    # Используется для cooldown (разные типы имеют разные интервалы)
    # String(50) достаточно для всех типов
    # index=True для быстрой фильтрации по типу
    generation_type: Mapped[str] = mapped_column(
        String(50),
        index=True,
        nullable=False,
    )

    # Ключ модели из config.yaml (например, "gpt-4o", "flux-dev")
    # String(255) для длинных идентификаторов моделей
    # Индекс НЕ нужен — фильтрация по модели используется редко
    model_key: Mapped[str] = mapped_column(String(255), nullable=False)

    # Статус генерации: pending, completed, failed
    # String(50) для enum-значений
    # default — новые записи создаются со статусом PENDING
    status: Mapped[str] = mapped_column(
        String(50),
        default=GenerationDBStatus.PENDING,
        nullable=False,
    )

    # Дата начала генерации
    # server_default=func.now() — БД сама проставляет текущее время при INSERT
    # index=True для сортировки и фильтрации по времени (cooldown, статистика)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        index=True,
        nullable=False,
    )

    # Дата завершения генерации
    # nullable=True — заполняется только после завершения
    # Используется для расчёта длительности: completed_at - created_at
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # Количество списанных токенов за генерацию
    # 0 если биллинг отключён или генерация бесплатная
    # Заполняется после успешного списания токенов
    tokens_charged: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
    )

    # Себестоимость генерации в рублях
    # Рассчитывается на основе config.yaml → models.*.cost
    # Сохраняется для аналитики независимо от включения биллинга
    # Numeric(10, 4) — до 999999.9999 рублей с точностью до 0.0001
    cost_rub: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        default=Decimal(0),
        nullable=False,
    )

    # FK на транзакцию списания токенов
    # Может быть NULL если биллинг отключён или генерация не завершена
    # ondelete="SET NULL" — при удалении транзакции генерация остаётся
    transaction_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Связь с транзакцией списания
    # Позволяет получить детали транзакции (amount, balance_after и т.д.)
    transaction: Mapped["Transaction | None"] = relationship(
        lazy="selectin",
    )

    # Связь с пользователем
    # back_populates — двусторонняя связь с User.generations
    # lazy="selectin" — автоматически подгружает связь при запросе (N+1 query)
    user: Mapped["User"] = relationship(
        back_populates="generations",
        lazy="selectin",
    )

    @override
    def __repr__(self) -> str:
        """Строковое представление генерации для отладки.

        Returns:
            Строка с основной информацией о генерации.

        Example:
            <Generation(id=123, user_id=456, type=image,
                        model=flux-dev, status=completed)>
        """
        return (
            f"<Generation(id={self.id}, user_id={self.user_id}, "
            f"type={self.generation_type}, model={self.model_key}, "
            f"status={self.status})>"
        )
