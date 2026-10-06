"""Тесты для admin API endpoints.

Модуль тестирует:
- POST /api/admin/payments/{id}/refund — возврат платежа Telegram Stars
- POST /api/admin/broadcasts/{id}/start — запуск рассылки
- POST /api/admin/broadcasts/{id}/pause — приостановка рассылки
- POST /api/admin/broadcasts/{id}/cancel — отмена рассылки
- POST /api/admin/broadcasts/{id}/test — тестовое сообщение админу
- Проверка админ-аутентификации
- Валидация статусов
- Обработка ошибок Telegram API
"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from src.api.admin import router as admin_router
from src.db.models.broadcast import Broadcast, BroadcastStatus, ParseMode
from src.db.models.payment import Payment, PaymentProvider, PaymentStatus
from src.db.models.user import User


@pytest.fixture
def mock_bot() -> Mock:
    """Mock Telegram Bot instance.

    Returns:
        Mock с методом refund_star_payment.
    """
    bot = Mock()
    bot.refund_star_payment = AsyncMock(return_value=True)
    return bot


@pytest.fixture
def app_with_bot(mock_bot: Mock) -> FastAPI:
    """FastAPI app с mock bot в state.

    Args:
        mock_bot: Mock Bot instance.

    Returns:
        FastAPI приложение с admin router и bot в state.
    """
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")  # noqa: S106
    app.include_router(admin_router)
    app.state.bot = mock_bot
    return app


@pytest.fixture
async def client(app_with_bot: FastAPI) -> AsyncClient:
    """Async HTTP client для тестирования API.

    Args:
        app_with_bot: FastAPI приложение.

    Returns:
        AsyncClient для отправки запросов.
    """
    transport = ASGITransport(app=app_with_bot)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def succeeded_payment(db_session: AsyncSession, test_user: User) -> Payment:
    """Платёж Telegram Stars со статусом SUCCEEDED.

    Args:
        db_session: Database session.
        test_user: User fixture.

    Returns:
        Payment со статусом SUCCEEDED.
    """
    payment = Payment(
        user_id=test_user.id,
        provider=PaymentProvider.TELEGRAM_STARS,
        provider_payment_id="telegram_charge_12345",
        status=PaymentStatus.SUCCEEDED,
        amount=Decimal(50),
        currency="XTR",
        tariff_slug="tokens_100",
        tokens_amount=100,
        description="Покупка 100 токенов",
    )
    db_session.add(payment)
    await db_session.commit()
    await db_session.refresh(payment)
    return payment


@pytest_asyncio.fixture
async def pending_payment(db_session: AsyncSession, test_user: User) -> Payment:
    """Платёж Telegram Stars со статусом PENDING.

    Args:
        db_session: Database session.
        test_user: User fixture.

    Returns:
        Payment со статусом PENDING.
    """
    payment = Payment(
        user_id=test_user.id,
        provider=PaymentProvider.TELEGRAM_STARS,
        provider_payment_id="telegram_charge_67890",
        status=PaymentStatus.PENDING,
        amount=Decimal(100),
        currency="XTR",
        tariff_slug="tokens_200",
        tokens_amount=200,
        description="Покупка 200 токенов",
    )
    db_session.add(payment)
    await db_session.commit()
    await db_session.refresh(payment)
    return payment


@pytest_asyncio.fixture
async def yookassa_payment(db_session: AsyncSession, test_user: User) -> Payment:
    """Платёж YooKassa (не Telegram Stars).

    Args:
        db_session: Database session.
        test_user: User fixture.

    Returns:
        Payment через YooKassa.
    """
    payment = Payment(
        user_id=test_user.id,
        provider=PaymentProvider.YOOKASSA,
        provider_payment_id="yoo_payment_12345",
        status=PaymentStatus.SUCCEEDED,
        amount=Decimal("99.00"),
        currency="RUB",
        tariff_slug="tokens_100",
        tokens_amount=100,
        description="Покупка 100 токенов",
    )
    db_session.add(payment)
    await db_session.commit()
    await db_session.refresh(payment)
    return payment


class TestRefundPayment:
    """Тесты для POST /api/admin/payments/{id}/refund."""

    @pytest.mark.asyncio
    async def test_successful_refund(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        succeeded_payment: Payment,
        db_session: AsyncSession,
        mock_bot: Mock,
    ) -> None:
        """Успешный возврат платежа Telegram Stars."""
        # Используем dependency_overrides для мокирования зависимостей
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/payments/{succeeded_payment.id}/refund"
        )

        # Очищаем overrides после теста
        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()

        # Проверяем ответ
        assert data["success"] is True
        assert data["payment_id"] == succeeded_payment.id
        assert data["refunded_amount"] == str(succeeded_payment.amount)
        assert "Successfully refunded" in data["message"]

        # Проверяем, что вызван bot.refund_star_payment
        mock_bot.refund_star_payment.assert_called_once_with(
            user_id=succeeded_payment.user_id,
            telegram_payment_charge_id=succeeded_payment.provider_payment_id,
        )

        # Проверяем, что статус обновлён в БД
        await db_session.refresh(succeeded_payment)
        assert succeeded_payment.status == PaymentStatus.REFUNDED

    @pytest.mark.asyncio
    async def test_payment_not_found(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
    ) -> None:
        """Ошибка 404 если платёж не найден."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post("/api/admin/payments/99999/refund")

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_wrong_provider(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        yookassa_payment: Payment,
        db_session: AsyncSession,
    ) -> None:
        """Ошибка 400 если платёж не через Telegram Stars."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/payments/{yookassa_payment.id}/refund"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "only supported for Telegram Stars" in detail
        assert yookassa_payment.provider in detail

    @pytest.mark.asyncio
    async def test_wrong_status(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        pending_payment: Payment,
        db_session: AsyncSession,
    ) -> None:
        """Ошибка 400 если платёж не в статусе SUCCEEDED."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(f"/api/admin/payments/{pending_payment.id}/refund")

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "Only succeeded payments can be refunded" in detail
        assert pending_payment.status in detail

    @pytest.mark.asyncio
    async def test_already_refunded(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        succeeded_payment: Payment,
        db_session: AsyncSession,
        mock_bot: Mock,
    ) -> None:
        """Возврат успеха если платёж уже возвращён."""
        # Меняем статус на REFUNDED
        succeeded_payment.status = PaymentStatus.REFUNDED
        await db_session.commit()

        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/payments/{succeeded_payment.id}/refund"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "already refunded" in data["message"].lower()

        # Bot API не должен вызываться
        mock_bot.refund_star_payment.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_provider_payment_id(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        succeeded_payment: Payment,
        db_session: AsyncSession,
        mock_bot: Mock,
    ) -> None:
        """Ошибка 400 если отсутствует provider_payment_id."""
        # Удаляем provider_payment_id
        succeeded_payment.provider_payment_id = None
        await db_session.commit()

        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/payments/{succeeded_payment.id}/refund"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 400
        assert "missing telegram_payment_charge_id" in response.json()["detail"]

        # Bot API не должен вызываться
        mock_bot.refund_star_payment.assert_not_called()

    @pytest.mark.asyncio
    async def test_telegram_api_error(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        succeeded_payment: Payment,
        db_session: AsyncSession,
        mock_bot: Mock,
    ) -> None:
        """Ошибка 500 если Telegram API вернул ошибку."""
        # Мокируем ошибку от Telegram API
        mock_bot.refund_star_payment.side_effect = Exception("Telegram API error")

        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/payments/{succeeded_payment.id}/refund"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 500
        assert "Telegram API error" in response.json()["detail"]

        # Проверяем, что статус НЕ изменён в БД (rollback)
        await db_session.refresh(succeeded_payment)
        assert succeeded_payment.status == PaymentStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_telegram_api_returns_false(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        succeeded_payment: Payment,
        db_session: AsyncSession,
        mock_bot: Mock,
    ) -> None:
        """Ошибка 500 если Telegram API вернул False."""
        # Мокируем False от Telegram API
        mock_bot.refund_star_payment.return_value = False

        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/payments/{succeeded_payment.id}/refund"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 500
        assert "returned False" in response.json()["detail"]

        # Проверяем, что статус НЕ изменён
        await db_session.refresh(succeeded_payment)
        assert succeeded_payment.status == PaymentStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_requires_admin_auth(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        succeeded_payment: Payment,
        db_session: AsyncSession,
    ) -> None:
        """Ошибка 401 если пользователь не авторизован."""
        # НЕ мокируем require_admin_auth — проверяем реальную аутентификацию
        # Мокируем auth_backend.authenticate чтобы вернуть False
        from src.db.base import get_session

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        with patch("src.api.admin.get_admin_auth") as mock_get_auth:
            mock_auth_backend = Mock()
            mock_auth_backend.authenticate = AsyncMock(return_value=False)
            mock_get_auth.return_value = mock_auth_backend

            response = await client.post(
                f"/api/admin/payments/{succeeded_payment.id}/refund"
            )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 401
        assert "authentication required" in response.json()["detail"].lower()


# =============================================================================
# ТЕСТЫ РАССЫЛОК
# =============================================================================


@pytest_asyncio.fixture
async def draft_broadcast(db_session: AsyncSession) -> Broadcast:
    """Рассылка в статусе DRAFT.

    Args:
        db_session: Database session.

    Returns:
        Broadcast со статусом DRAFT.
    """
    broadcast = Broadcast(
        name="Test broadcast",
        message_text="Hello World!",
        parse_mode=ParseMode.HTML,
        status=BroadcastStatus.DRAFT,
    )
    db_session.add(broadcast)
    await db_session.commit()
    await db_session.refresh(broadcast)
    return broadcast


@pytest_asyncio.fixture
async def running_broadcast(db_session: AsyncSession) -> Broadcast:
    """Рассылка в статусе RUNNING.

    Args:
        db_session: Database session.

    Returns:
        Broadcast со статусом RUNNING.
    """
    broadcast = Broadcast(
        name="Running broadcast",
        message_text="Hello World!",
        parse_mode=ParseMode.HTML,
        status=BroadcastStatus.RUNNING,
        total_recipients=100,
        sent_count=50,
    )
    db_session.add(broadcast)
    await db_session.commit()
    await db_session.refresh(broadcast)
    return broadcast


class TestStartBroadcast:
    """Тесты для POST /api/admin/broadcasts/{id}/start."""

    async def test_start_draft_broadcast_success(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
        draft_broadcast: Broadcast,
    ) -> None:
        """Успешный запуск рассылки из статуса DRAFT."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/broadcasts/{draft_broadcast.id}/start"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["broadcast_id"] == draft_broadcast.id
        # Статус может быть PENDING или RUNNING в зависимости от скорости воркера
        assert data["status"] in (BroadcastStatus.PENDING, BroadcastStatus.RUNNING)

    async def test_start_nonexistent_broadcast_404(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
    ) -> None:
        """404 при попытке запустить несуществующую рассылку."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post("/api/admin/broadcasts/99999/start")

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 404

    async def test_start_running_broadcast_fails(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
        running_broadcast: Broadcast,
    ) -> None:
        """Нельзя запустить рассылку в статусе RUNNING."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/broadcasts/{running_broadcast.id}/start"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 400
        assert "DRAFT or PAUSED" in response.json()["detail"]


class TestPauseBroadcast:
    """Тесты для POST /api/admin/broadcasts/{id}/pause."""

    async def test_pause_running_broadcast_success(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
        running_broadcast: Broadcast,
    ) -> None:
        """Успешная приостановка рассылки."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/broadcasts/{running_broadcast.id}/pause"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["status"] == BroadcastStatus.PAUSED

    async def test_pause_draft_broadcast_fails(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
        draft_broadcast: Broadcast,
    ) -> None:
        """Нельзя приостановить рассылку в статусе DRAFT."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/broadcasts/{draft_broadcast.id}/pause"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 400
        assert "RUNNING" in response.json()["detail"]


class TestCancelBroadcast:
    """Тесты для POST /api/admin/broadcasts/{id}/cancel."""

    async def test_cancel_draft_broadcast_success(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
        draft_broadcast: Broadcast,
    ) -> None:
        """Успешная отмена рассылки из статуса DRAFT."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/broadcasts/{draft_broadcast.id}/cancel"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["status"] == BroadcastStatus.CANCELLED

    async def test_cancel_running_broadcast_success(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
        running_broadcast: Broadcast,
    ) -> None:
        """Успешная отмена рассылки в статусе RUNNING."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        response = await client.post(
            f"/api/admin/broadcasts/{running_broadcast.id}/cancel"
        )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["status"] == BroadcastStatus.CANCELLED


class TestTestBroadcast:
    """Тесты для POST /api/admin/broadcasts/{id}/test."""

    async def test_test_broadcast_success(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
        draft_broadcast: Broadcast,
        mock_bot: Mock,
    ) -> None:
        """Успешная отправка тестового сообщения."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        mock_bot.send_message = AsyncMock()

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        with patch("src.api.admin.settings") as mock_settings:
            mock_settings.logging.telegram.chat_id = 123456789

            response = await client.post(
                f"/api/admin/broadcasts/{draft_broadcast.id}/test"
            )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "123456789" in data["message"]
        mock_bot.send_message.assert_called_once()

    async def test_test_broadcast_no_admin_chat_id(
        self,
        client: AsyncClient,
        app_with_bot: FastAPI,
        db_session: AsyncSession,
        draft_broadcast: Broadcast,
    ) -> None:
        """Ошибка когда не настроен admin chat_id."""
        from src.api.admin import require_admin_auth
        from src.db.base import get_session

        app_with_bot.dependency_overrides[require_admin_auth] = lambda: None

        async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        app_with_bot.dependency_overrides[get_session] = override_get_session

        with patch("src.api.admin.settings") as mock_settings:
            mock_settings.logging.telegram.chat_id = None

            response = await client.post(
                f"/api/admin/broadcasts/{draft_broadcast.id}/test"
            )

        app_with_bot.dependency_overrides.clear()

        assert response.status_code == 400
        assert "LOGGING__TELEGRAM__CHAT_ID" in response.json()["detail"]
