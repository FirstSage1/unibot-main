"""Тесты для обработчика команды /imagine.

Проверяют корректность работы генерации изображений:
- Команда /imagine показывает выбор модели
- Выбор модели сохраняется в FSM state
- Промпт пользователя обрабатывается и отправляется в AI
- Изображение отправляется пользователю
- FSM очищается после генерации
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from src.bot.handlers.imagine import (
    cmd_imagine,
    handle_model_selection,
    handle_user_prompt,
)
from src.bot.states import ImagineStates
from src.config.yaml_config import ModelConfig
from src.db.models.user import User
from src.providers.ai.base import GenerationResult, GenerationStatus
from src.services.ai_service import AIService
from src.services.billing_service import GenerationCost
from src.utils.i18n import Localization


@pytest.fixture
def mock_message() -> Message:
    """Создать мок-объект сообщения от Telegram."""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(spec=TelegramUser)
    message.from_user.id = 123456789
    message.from_user.username = "test_user"
    message.text = "Кот в космосе"
    message.answer = AsyncMock()
    message.answer_photo = AsyncMock()

    # Атрибут chat нужен для send_chat_action
    message.chat = MagicMock()
    message.chat.id = 123456789
    message.chat.bot = MagicMock()
    message.chat.bot.send_chat_action = AsyncMock()

    return message


@pytest.fixture
def mock_callback_query(mock_message: Message) -> CallbackQuery:
    """Создать мок-объект callback query от Telegram."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = mock_message.from_user
    callback.message = mock_message
    callback.data = "model:dall-e-3"
    callback.answer = AsyncMock()
    mock_message.edit_text = AsyncMock()
    return callback


@pytest.fixture
def mock_fsm_context() -> FSMContext:
    """Создать мок-объект FSM контекста."""
    context = MagicMock(spec=FSMContext)
    context.set_state = AsyncMock()
    context.update_data = AsyncMock()
    context.get_data = AsyncMock(return_value={"model_key": "dall-e-3"})
    context.get_state = AsyncMock(return_value=ImagineStates.waiting_for_prompt)
    context.clear = AsyncMock()
    return context


@pytest.fixture
def mock_ai_service() -> AIService:
    """Создать мок-объект AI сервиса."""
    service = MagicMock(spec=AIService)
    service.generate = AsyncMock(
        return_value=GenerationResult(
            status=GenerationStatus.SUCCESS,
            content="https://example.com/generated-image.png",
        )
    )
    # Добавляем get_available_models() с одной image-моделью
    service.get_available_models = MagicMock(
        return_value={
            "dall-e-3": ModelConfig(
                provider="openai",
                model_id="openai/dall-e-3",
                generation_type="image",
                display_name="DALL-E 3",
                price_tokens=50,
            ),
        }
    )
    return service


@pytest.fixture
def mock_billing_cost() -> GenerationCost:
    """Создать мок GenerationCost для успешного биллинга."""
    return GenerationCost(
        can_proceed=True, tokens_cost=50, model_key="dall-e-3", quantity=1.0
    )


@pytest.fixture
def mock_l10n() -> Localization:
    """Создать мок-объект локализации.

    Возвращает переводы на русском языке для тестирования.
    Метод get() возвращает строку с подставленными параметрами.
    """
    l10n = MagicMock(spec=Localization)

    # Словарь переводов для тестов
    translations = {
        "imagine_choose_model": "🎨 <b>Выберите модель для генерации:</b>",
        "imagine_model_selected": "✅ Модель выбрана: <b>{model_key}</b>",
        "imagine_model_not_selected": "❌ Модель не выбрана.",
        "imagine_generating": "⏳ Генерирую изображение...",
        "imagine_generated": "🎨 Изображение сгенерировано моделью {model_key}",
        "imagine_empty_response": "❌ AI вернул пустой ответ.",
        "imagine_generation_error": "❌ Ошибка генерации: {error}",
        "imagine_unexpected_error": "❌ Произошла неожиданная ошибка.",
        "error_user_not_found": "❌ Ошибка: пользователь не найден.",
        "error_db_temporary": "❌ Временная ошибка БД.",
        "error_db_permanent": "❌ Ошибка при работе с базой данных.",
        "no_models_available": "❌ Модели недоступны",
    }

    def get_translation(key: str, **kwargs: str) -> str:
        """Вернуть перевод с подставленными параметрами."""
        text = translations.get(key, key)
        if kwargs:
            text = text.format(**kwargs)
        return text

    l10n.get = MagicMock(side_effect=get_translation)
    return l10n


class TestCmdImagine:
    """Тесты для команды /imagine."""

    @pytest.mark.asyncio
    async def test_cmd_imagine_shows_model_selection(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
    ) -> None:
        """Проверить, что команда /imagine показывает выбор модели."""
        # Act — передаём ai_service через DI
        await cmd_imagine(mock_message, mock_fsm_context, mock_l10n, mock_ai_service)

        # Assert
        # Проверяем, что состояние изменилось
        mock_fsm_context.set_state.assert_called_once_with(
            ImagineStates.waiting_for_model_selection
        )

        # Проверяем, что ответ отправлен
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        assert "Выберите модель" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_imagine_no_available_models(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
    ) -> None:
        """Проверить, что при отсутствии моделей показывается сообщение об ошибке."""
        # Arrange — нет доступных моделей
        mock_ai_service.get_available_models = MagicMock(return_value={})

        # Act
        await cmd_imagine(mock_message, mock_fsm_context, mock_l10n, mock_ai_service)

        # Assert
        # Состояние НЕ должно измениться
        mock_fsm_context.set_state.assert_not_called()

        # Ответ должен быть отправлен с ключом no_models_available
        mock_message.answer.assert_called_once()
        mock_l10n.get.assert_called_with("no_models_available")

    @pytest.mark.asyncio
    async def test_cmd_imagine_without_from_user(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
    ) -> None:
        """Проверить, что обработчик корректно обрабатывает отсутствие from_user."""
        # Arrange
        mock_message.from_user = None

        # Act
        await cmd_imagine(mock_message, mock_fsm_context, mock_l10n, mock_ai_service)

        # Assert
        # Не должно быть вызовов
        mock_fsm_context.set_state.assert_not_called()
        mock_message.answer.assert_not_called()


class TestHandleModelSelection:
    """Тесты для обработчика выбора модели."""

    @pytest.mark.asyncio
    async def test_handle_model_selection_saves_model_key(
        self,
        mock_callback_query: CallbackQuery,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что выбранная модель сохраняется в FSM state."""
        # Act
        await handle_model_selection(mock_callback_query, mock_fsm_context, mock_l10n)

        # Assert
        mock_fsm_context.update_data.assert_called_once_with(model_key="dall-e-3")

    @pytest.mark.asyncio
    async def test_handle_model_selection_changes_state(
        self,
        mock_callback_query: CallbackQuery,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что состояние меняется на waiting_for_prompt."""
        # Act
        await handle_model_selection(mock_callback_query, mock_fsm_context, mock_l10n)

        # Assert
        mock_fsm_context.set_state.assert_called_once_with(
            ImagineStates.waiting_for_prompt
        )

    @pytest.mark.asyncio
    async def test_handle_model_selection_edits_message(
        self,
        mock_callback_query: CallbackQuery,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что сообщение редактируется с подтверждением."""
        # Act
        await handle_model_selection(mock_callback_query, mock_fsm_context, mock_l10n)

        # Assert
        assert mock_callback_query.message is not None
        mock_callback_query.message.edit_text.assert_called_once()
        call_args = mock_callback_query.message.edit_text.call_args
        assert "Модель выбрана" in call_args[0][0]
        assert "dall-e-3" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_model_selection_answers_callback(
        self,
        mock_callback_query: CallbackQuery,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что callback подтверждается (убирает часики)."""
        # Act
        await handle_model_selection(mock_callback_query, mock_fsm_context, mock_l10n)

        # Assert
        mock_callback_query.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_model_selection_without_callback_data(
        self,
        mock_callback_query: CallbackQuery,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
    ) -> None:
        """Проверить обработку отсутствия callback_data."""
        # Arrange
        mock_callback_query.data = None

        # Act
        await handle_model_selection(mock_callback_query, mock_fsm_context, mock_l10n)

        # Assert
        mock_fsm_context.update_data.assert_not_called()


class TestHandleUserPrompt:
    """Тесты для обработчика промпта пользователя."""

    @pytest.mark.asyncio
    async def test_handle_user_prompt_generates_and_sends_image(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        mock_billing_cost: GenerationCost,
    ) -> None:
        """Проверить, что изображение генерируется и отправляется пользователю."""
        # Arrange
        processing_msg = MagicMock()
        processing_msg.delete = AsyncMock()
        processing_msg.edit_text = AsyncMock()
        mock_message.answer = AsyncMock(return_value=processing_msg)

        # Act
        with patch(
            "src.bot.utils.billing.check_billing_and_show_error",
            return_value=mock_billing_cost,
        ):
            with patch("src.bot.utils.billing.charge_after_delivery"):
                await handle_user_prompt(
                    mock_message,
                    mock_fsm_context,
                    mock_l10n,
                    mock_ai_service,
                    session_factory,
                )

        # Assert
        # AI сервис должен быть вызван через ImageGenerationService
        mock_ai_service.generate.assert_called_once()
        call_args = mock_ai_service.generate.call_args
        assert call_args[1]["model_key"] == "dall-e-3"
        assert call_args[1]["prompt"] == "Кот в космосе"

        # Изображение должно быть отправлено
        mock_message.answer_photo.assert_called_once()

        # FSM должен быть очищен
        mock_fsm_context.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_user_prompt_without_model_key_in_state(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
    ) -> None:
        """Проверить обработку отсутствия model_key в FSM state."""
        # Arrange
        mock_fsm_context.get_data = AsyncMock(return_value={})

        # Act
        await handle_user_prompt(
            mock_message, mock_fsm_context, mock_l10n, mock_ai_service
        )

        # Assert
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        assert "модель не выбрана" in call_args[0][0].lower()
        mock_ai_service.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_user_prompt_without_from_user(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
    ) -> None:
        """Проверить обработку отсутствия from_user."""
        # Arrange
        mock_message.from_user = None

        # Act
        await handle_user_prompt(
            mock_message, mock_fsm_context, mock_l10n, mock_ai_service
        )

        # Assert
        mock_message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_user_prompt_handles_generation_error(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        mock_billing_cost: GenerationCost,
    ) -> None:
        """Проверить обработку ошибки генерации AI."""
        # Arrange
        from src.core.exceptions import GenerationError

        processing_msg = MagicMock()
        processing_msg.edit_text = AsyncMock()
        processing_msg.delete = AsyncMock()
        mock_message.answer = AsyncMock(return_value=processing_msg)

        mock_ai_service.generate = AsyncMock(
            side_effect=GenerationError(
                "Таймаут API",
                provider="openai",
                model_id="dall-e-3",
            )
        )

        # Act
        with patch(
            "src.bot.utils.billing.check_billing_and_show_error",
            return_value=mock_billing_cost,
        ):
            await handle_user_prompt(
                mock_message,
                mock_fsm_context,
                mock_l10n,
                mock_ai_service,
                session_factory,
            )

        # Assert
        # ImageGenerationService обрабатывает ошибку и показывает сообщение
        processing_msg.edit_text.assert_called_once()
        call_args = processing_msg.edit_text.call_args
        # Проверяем что показана ошибка
        error_text = call_args[0][0].lower()
        has_error = "error" in error_text or "ошибк" in error_text
        has_key = "image_generation_error" in call_args[0][0]
        assert has_error or has_key

        # FSM НЕ должен быть очищен при ошибке
        mock_fsm_context.clear.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_user_prompt_handles_empty_ai_response(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        mock_billing_cost: GenerationCost,
    ) -> None:
        """Проверить обработку пустого ответа от AI."""
        # Arrange
        processing_msg = MagicMock()
        processing_msg.edit_text = AsyncMock()
        processing_msg.delete = AsyncMock()
        mock_message.answer = AsyncMock(return_value=processing_msg)

        mock_ai_service.generate = AsyncMock(
            return_value=GenerationResult(
                status=GenerationStatus.SUCCESS,
                content="",  # Пустой ответ
            )
        )

        # Act
        with patch(
            "src.bot.utils.billing.check_billing_and_show_error",
            return_value=mock_billing_cost,
        ):
            await handle_user_prompt(
                mock_message,
                mock_fsm_context,
                mock_l10n,
                mock_ai_service,
                session_factory,
            )

        # Assert
        # ImageGenerationService обрабатывает пустой ответ и показывает ошибку
        processing_msg.edit_text.assert_called_once()
        call_args = processing_msg.edit_text.call_args
        # Проверяем что показана ошибка
        error_text = call_args[0][0].lower()
        has_error = "error" in error_text
        has_key = "image_generation_error" in call_args[0][0]
        assert has_error or has_key

        # FSM НЕ должен быть очищен при ошибке
        mock_fsm_context.clear.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_user_prompt_deletes_processing_message(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
        db_session: AsyncSession,
        test_user: User,
        session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        mock_billing_cost: GenerationCost,
    ) -> None:
        """Проверить, что сообщение 'Генерирую...' удаляется после генерации."""
        # Arrange
        processing_msg = MagicMock()
        processing_msg.delete = AsyncMock()
        processing_msg.edit_text = AsyncMock()
        mock_message.answer = AsyncMock(return_value=processing_msg)

        # Act
        with patch(
            "src.bot.utils.billing.check_billing_and_show_error",
            return_value=mock_billing_cost,
        ):
            with patch("src.bot.utils.billing.charge_after_delivery"):
                await handle_user_prompt(
                    mock_message,
                    mock_fsm_context,
                    mock_l10n,
                    mock_ai_service,
                    session_factory,
                )

        # Assert
        processing_msg.delete.assert_called_once()
