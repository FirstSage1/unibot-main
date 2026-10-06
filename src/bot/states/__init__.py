"""FSM (Finite State Machine) состояния для бота.

FSM используется для управления многошаговыми диалогами.
Например, для команды /chatgpt:
1. Пользователь вызывает /chatgpt
2. Бот показывает выбор модели (состояние: waiting_for_model)
3. Пользователь выбирает модель
4. Бот ожидает сообщение (состояние: waiting_for_message)
5. Пользователь пишет сообщение
6. Бот генерирует ответ и возвращается в состояние waiting_for_message
"""

from src.bot.states.chatgpt import ChatGPTStates
from src.bot.states.edit_image import EditImageStates
from src.bot.states.imagine import ImagineStates

__all__ = ["ChatGPTStates", "EditImageStates", "ImagineStates"]
