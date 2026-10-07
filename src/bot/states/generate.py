"""Состояния подготовки описания товара."""

from aiogram.fsm.state import State, StatesGroup


class GenerateStates(StatesGroup):
    """Последовательный ввод названия и характеристик."""

    waiting_for_name = State()
    waiting_for_characteristics = State()
