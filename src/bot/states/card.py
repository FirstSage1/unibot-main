"""Состояния FSM для создания рекламных фото товара."""

from aiogram.fsm.state import State, StatesGroup


class CardStates(StatesGroup):
    """Шаги создания фото товара: загрузка исходника и выбор сцены."""

    waiting_for_product_photo = State()
    waiting_for_idea = State()
