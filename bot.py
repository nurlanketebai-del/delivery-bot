import os
import asyncio

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton


TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")


bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db_pool = None


main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🏪 Магазин"),
            KeyboardButton(text="🚚 Курьер"),
        ],
        [
            KeyboardButton(text="👨‍💼 Администратор"),
        ],
    ],
    resize_keyboard=True,
)


confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Отправить заявку")],
        [KeyboardButton(text="❌ Отмена")],
    ],
    resize_keyboard=True,
)


class StoreRegistration(StatesGroup):
    store_name = State()
    contact_name = State()
    phone = State()
    address = State()
    confirm = State()


async def init_db():
    global db_pool

    db_pool = await asyncpg.create_pool(DATABASE_URL)

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stores (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL UNIQUE,
                store_name TEXT NOT NULL,
                contact_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                address TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "👋 Добро пожаловать в систему доставки!\n\n"
        "Выберите вашу роль:",
        reply_markup=main_keyboard,
    )


@dp.message(F.text == "🏪 Магазин")
async def store_start(message: Message, state: FSMContext):
    telegram_id = message.from_user.id

    async with db_pool.acquire() as conn:
        store = await conn.fetchrow(
            """
            SELECT store_name, status
            FROM stores
            WHERE telegram_id = $1
            """,
            telegram_id,
        )

    if store:
        await message.answer(
            f"🏪 Магазин: {store['store_name']}\n"
            f"Статус заявки: {store['status']}"
        )
        return

    await state.set_state(StoreRegistration.store_name)

    await message.answer(
        "🏪 Регистрация магазина\n\n"
        "Введите название магазина:"
    )


@dp.message(StoreRegistration.store_name)
async def store_name_handler(message: Message, state: FSMContext):
    await state.update_data(store_name=message.text)

    await state.set_state(StoreRegistration.contact_name)

    await message.answer(
        "Введите имя контактного лица:"
    )


@dp.message(StoreRegistration.contact_name)
async def contact_name_handler(message: Message, state: FSMContext):
    await state.update_data(contact_name=message.text)

    await state.set_state(StoreRegistration.phone)

    await message.answer(
        "Введите номер телефона:\n\n"
        "Например: +7 777 123 45 67"
    )


@dp.message(StoreRegistration.phone)
async def phone_handler(message: Message, state: FSMContext):
    await state.update_data(phone=message.text)

    await state.set_state(StoreRegistration.address)

    await message.answer(
        "Введите адрес магазина или склада:"
    )


@dp.message(StoreRegistration.address)
async def address_handler(message: Message, state: FSMContext):
    await state.update_data(address=message.text)

    data = await state.get_data()

    await state.set_state(StoreRegistration.confirm)

    await message.answer(
        "Проверьте данные:\n\n"
        f"🏪 Магазин: {data['store_name']}\n"
        f"👤 Контакт: {data['contact_name']}\n"
        f"📞 Телефон: {data['phone']}\n"
        f"📍 Адрес: {data['address']}\n\n"
        "Отправить заявку?",
        reply_markup=confirm_keyboard,
    )


@dp.message(StoreRegistration.confirm, F.text == "✅ Отправить заявку")
async def store_confirm_handler(message: Message, state: FSMContext):
    data = await state.get_data()

    telegram_id = message.from_user.id

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO stores (
                telegram_id,
                store_name,
                contact_name,
                phone,
                address
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (telegram_id)
            DO UPDATE SET
                store_name = EXCLUDED.store_name,
                contact_name = EXCLUDED.contact_name,
                phone = EXCLUDED.phone,
                address = EXCLUDED.address,
                status = 'pending'
            """,
            telegram_id,
            data["store_name"],
            data["contact_name"],
            data["phone"],
            data["address"],
        )

    await state.clear()

    await message.answer(
        "✅ Заявка отправлена!\n\n"
        "Статус: ⏳ Ожидает подтверждения администратора.",
        reply_markup=main_keyboard,
    )


@dp.message(StoreRegistration.confirm, F.text == "❌ Отмена")
async def store_cancel_handler(message: Message, state: FSMContext):
    await state.clear()

    await message.answer(
        "Регистрация отменена.",
        reply_markup=main_keyboard,
    )


@dp.message(F.text == "🚚 Курьер")
async def courier_handler(message: Message):
    await message.answer(
        "🚚 Раздел курьера.\n\n"
        "Регистрацию курьера добавим следующим этапом."
    )


@dp.message(F.text == "👨‍💼 Администратор")
async def admin_handler(message: Message):
    await message.answer(
        "👨‍💼 Раздел администратора.\n\n"
        "Доступ администратора добавим следующим этапом."
    )


@dp.message()
async def fallback_handler(message: Message):
    await message.answer(
        "Пожалуйста, используйте кнопки меню."
    )


async def main():
    print("Connecting to PostgreSQL...")

    await init_db()

    print("Database connected.")
    print("Bot is starting...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
