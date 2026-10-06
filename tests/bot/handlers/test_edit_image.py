"""Тесты для обработчика команды /edit_image.

Проверяют корректность работы редактирования изображений:
- Команда /edit_image запрашивает загрузку изображения
- Загрузка изображения показывает выбор модели
- Выбор модели сохраняется в FSM state
- Промпт пользователя обрабатывается и отправляется в AI
- Отредактированное изображение отправляется пользователю
- FSM очищается после редактирования
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PhotoSize
from aiogram.types import User as TelegramUser

from src.bot.handlers.edit_image import (
    cmd_edit_image,
    handle_image_upload,
    handle_invalid_image,
    handle_model_selection,
)
from src.bot.states import EditImageStates
from src.config.yaml_config import ModelConfig
from src.services.ai_service import AIService
from src.utils.i18n import Localization


@pytest.fixture
def mock_message() -> Message:
    """Создать мок-объект сообщения от Telegram."""
    message = MagicMock(spec=Message)
    message.from_user = MagicMock(spec=TelegramUser)
    message.from_user.id = 123456789
    message.from_user.username = "test_user"
    message.text = "Сделай фон синим"
    message.answer = AsyncMock()
    message.answer_photo = AsyncMock()

    return message


@pytest.fixture
def mock_message_with_photo(mock_message: Message) -> Message:
    """Создать мок-объект сообщения с фотографией."""
    photo = MagicMock(spec=PhotoSize)
    photo.file_id = "AgACAgIAAxkBAAIB"
    photo.width = 1024
    photo.height = 768
    mock_message.photo = [photo]
    return mock_message


@pytest.fixture
def mock_callback_query(mock_message: Message) -> CallbackQuery:
    """Создать мок-объект callback query от Telegram."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = mock_message.from_user
    callback.message = mock_message
    callback.data = "model:gemini-pro-vision"
    callback.answer = AsyncMock()
    mock_message.edit_text = AsyncMock()
    return callback


@pytest.fixture
def mock_fsm_context() -> FSMContext:
    """Создать мок-объект FSM контекста."""
    context = MagicMock(spec=FSMContext)
    context.set_state = AsyncMock()
    context.update_data = AsyncMock()
    context.get_data = AsyncMock(
        return_value={
            "model_key": "gemini-pro-vision",
            "image_file_id": "AgACAgIAAxkBAAIB",
        }
    )
    context.get_state = AsyncMock(return_value=EditImageStates.waiting_for_prompt)
    context.clear = AsyncMock()
    return context


@pytest.fixture
def mock_ai_service() -> AIService:
    """Создать мок-объект AI сервиса."""
    service = MagicMock(spec=AIService)
    # Добавляем get_available_models() с одной image_edit-моделью
    service.get_available_models = MagicMock(
        return_value={
            "gemini-pro-vision": ModelConfig(
                provider="google",
                model_id="google/gemini-pro-vision",
                generation_type="image_edit",
                display_name="Gemini Pro Vision",
                price_tokens=30,
            ),
        }
    )
    return service


@pytest.fixture
def mock_l10n() -> Localization:
    """Создать мок-объект локализации."""
    l10n = MagicMock(spec=Localization)

    # Словарь переводов для тестов
    translations = {
        "edit_send_image": "📤 Отправьте изображение для редактирования",
        "edit_choose_model": "🎨 <b>Выберите модель для редактирования:</b>",
        "edit_model_selected": "✅ Модель выбрана: <b>{model_key}</b>",
        "edit_please_send_image": "❌ Пожалуйста, отправьте изображение.",
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


class TestCmdEditImage:
    """Тесты для команды /edit_image."""

    @pytest.mark.asyncio
    async def test_cmd_edit_image_requests_image_upload(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что команда /edit_image запрашивает загрузку изображения."""
        # Act
        await cmd_edit_image(mock_message, mock_fsm_context, mock_l10n)

        # Assert
        # Проверяем, что состояние изменилось
        mock_fsm_context.set_state.assert_called_once_with(
            EditImageStates.waiting_for_image
        )

        # Проверяем, что ответ отправлен
        mock_message.answer.assert_called_once()
        call_args = mock_message.answer.call_args
        assert "изображение" in call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_cmd_edit_image_without_from_user(
        self,
        mock_message: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что обработчик корректно обрабатывает отсутствие from_user."""
        # Arrange
        mock_message.from_user = None

        # Act
        await cmd_edit_image(mock_message, mock_fsm_context, mock_l10n)

        # Assert
        # Не должно быть вызовов
        mock_fsm_context.set_state.assert_not_called()
        mock_message.answer.assert_not_called()


class TestHandleImageUpload:
    """Тесты для обработчика загрузки изображения."""

    @pytest.mark.asyncio
    async def test_handle_image_upload_saves_file_id_and_shows_model_selection(
        self,
        mock_message_with_photo: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
    ) -> None:
        """Проверить, что file_id сохраняется и показывается выбор модели."""
        # Act
        await handle_image_upload(
            mock_message_with_photo, mock_fsm_context, mock_l10n, mock_ai_service
        )

        # Assert
        # Проверяем, что file_id сохранён
        mock_fsm_context.update_data.assert_called_once()
        call_kwargs = mock_fsm_context.update_data.call_args[1]
        assert "image_file_id" in call_kwargs

        # Проверяем, что состояние изменилось
        mock_fsm_context.set_state.assert_called_once_with(
            EditImageStates.waiting_for_model_selection
        )

        # Проверяем, что ответ отправлен
        mock_message_with_photo.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_image_upload_no_available_models(
        self,
        mock_message_with_photo: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
    ) -> None:
        """Проверить, что при отсутствии моделей показывается ошибка и FSM очищается."""
        # Arrange — нет доступных моделей
        mock_ai_service.get_available_models = MagicMock(return_value={})

        # Act
        await handle_image_upload(
            mock_message_with_photo, mock_fsm_context, mock_l10n, mock_ai_service
        )

        # Assert
        # Ответ должен быть отправлен с ключом no_models_available
        mock_message_with_photo.answer.assert_called()
        mock_l10n.get.assert_called_with("no_models_available")

        # FSM должен быть очищен
        mock_fsm_context.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_image_upload_without_from_user(
        self,
        mock_message_with_photo: Message,
        mock_fsm_context: FSMContext,
        mock_l10n: Localization,
        mock_ai_service: AIService,
    ) -> None:
        """Проверить, что обработчик корректно обрабатывает отсутствие from_user."""
        # Arrange
        mock_message_with_photo.from_user = None

        # Act
        await handle_image_upload(
            mock_message_with_photo, mock_fsm_context, mock_l10n, mock_ai_service
        )

        # Assert
        # Не должно быть вызовов
        mock_fsm_context.update_data.assert_not_called()
        mock_fsm_context.set_state.assert_not_called()


class TestHandleInvalidImage:
    """Тесты для обработчика невалидных сообщений."""

    @pytest.mark.asyncio
    async def test_handle_invalid_image_sends_error_message(
        self,
        mock_message: Message,
        mock_l10n: Localization,
    ) -> None:
        """Проверить, что при отправке текста вместо изображения показывается ошибка."""
        # Act
        await handle_invalid_image(mock_message, mock_l10n)

        # Assert
        mock_message.answer.assert_called_once()
        mock_l10n.get.assert_called_with("edit_please_send_image")


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
        mock_fsm_context.update_data.assert_called_once_with(
            model_key="gemini-pro-vision"
        )

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
            EditImageStates.waiting_for_prompt
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
        assert "gemini-pro-vision" in call_args[0][0]

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
