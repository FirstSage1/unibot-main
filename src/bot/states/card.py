"""Состояния FSM для создания рекламных фото товара."""

from aiogram.fsm.state import State, StatesGroup


class CardStates(StatesGroup):
    """Шаги создания фото товара: загрузка исходника и выбор сцены."""

    waiting_for_product_photo = State()
    analyzing = State()
    waiting_for_idea = State()
    generating = State()
    waiting_for_correction = State()
