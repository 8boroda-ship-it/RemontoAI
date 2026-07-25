from aiogram.fsm.state import State, StatesGroup


class OrderForm(StatesGroup):
    description = State()
    photos = State()
    city = State()
    address = State()
    name = State()
    phone = State()
    slot = State()
    confirmation = State()

