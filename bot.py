import os

import asyncio

import asyncpg

from aiogram import Bot, Dispatcher, F

from aiogram.filters import CommandStart

from aiogram.fsm.context import FSMContext

from aiogram.fsm.state import State, StatesGroup

from aiogram.fsm.storage.memory import MemoryStorage

from aiogram.types import (

    Message,

    ReplyKeyboardMarkup,

    KeyboardButton,

    InlineKeyboardMarkup,

    InlineKeyboardButton,

    CallbackQuery,

)

TOKEN = os.getenv("BOT_TOKEN")

DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN:

    raise RuntimeError("BOT_TOKEN is not set")

if not DATABASE_URL:

    raise RuntimeError("DATABASE_URL is not set")

bot = Bot(token=TOKEN)

dp = Dispatcher(storage=MemoryStorage())

db_pool = None

# =========================

# КЛАВИАТУРЫ

# =========================

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

registration_confirm_keyboard = ReplyKeyboardMarkup(

    keyboard=[

        [KeyboardButton(text="✅ Отправить заявку")],

        [KeyboardButton(text="❌ Отмена")],

    ],

    resize_keyboard=True,

)

store_keyboard = ReplyKeyboardMarkup(

    keyboard=[

        [KeyboardButton(text="➕ Создать заказ")],

        [

            KeyboardButton(text="📦 Мои заказы"),

            KeyboardButton(text="🏪 Профиль магазина"),

        ],

        [KeyboardButton(text="⬅️ Главное меню")],

    ],

    resize_keyboard=True,

)

order_confirm_keyboard = ReplyKeyboardMarkup(

    keyboard=[

        [KeyboardButton(text="✅ Создать заказ")],

        [KeyboardButton(text="❌ Отменить заказ")],

    ],

    resize_keyboard=True,

)

# =========================

# FSM

# =========================

class StoreRegistration(StatesGroup):

    store_name = State()

    contact_name = State()

    phone = State()

    address = State()

    confirm = State()

class OrderCreation(StatesGroup):

    client_name = State()

    client_phone = State()

    delivery_address = State()

    item = State()

    delivery_time = State()

    comment = State()

    confirm = State()

# =========================

# DATABASE

# =========================

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

        await conn.execute(

            """

            CREATE TABLE IF NOT EXISTS orders (

                id SERIAL PRIMARY KEY,

                store_id INTEGER NOT NULL REFERENCES stores(id),

                client_name TEXT NOT NULL,

                client_phone TEXT NOT NULL,

                pickup_address TEXT NOT NULL,

                delivery_address TEXT NOT NULL,

                item TEXT NOT NULL,

                delivery_time TEXT NOT NULL,

                comment TEXT,

                status TEXT NOT NULL DEFAULT 'new',

                courier_id BIGINT,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()

            )

            """

        )

# =========================

# START

# =========================

@dp.message(CommandStart())

async def start_handler(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(

        "👋 Добро пожаловать в систему доставки!\n\n"

        "Выберите вашу роль:",

        reply_markup=main_keyboard,

    )

@dp.message(F.text == "⬅️ Главное меню")

async def back_to_main(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(

        "Главное меню:",

        reply_markup=main_keyboard,

    )

# =========================

# МАГАЗИН

# =========================

@dp.message(F.text == "🏪 Магазин")

async def store_start(message: Message, state: FSMContext):

    telegram_id = message.from_user.id

    async with db_pool.acquire() as conn:

        store = await conn.fetchrow(

            """

            SELECT *

            FROM stores

            WHERE telegram_id = $1

            """,

            telegram_id,

        )

    if store:

        if store["status"] == "approved":

            await message.answer(

                f"🏪 {store['store_name']}\n\n"

                "Выберите действие:",

                reply_markup=store_keyboard,

            )

            return

        if store["status"] == "pending":

            await message.answer(

                f"🏪 Магазин: {store['store_name']}\n\n"

                "⏳ Ваша заявка ожидает подтверждения администратора."

            )

            return

        if store["status"] == "rejected":

            await message.answer(

                f"🏪 Магазин: {store['store_name']}\n\n"

                "❌ Ваша заявка была отклонена."

            )

            return

    await state.set_state(StoreRegistration.store_name)

    await message.answer(

        "🏪 Регистрация магазина\n\n"

        "Введите название магазина:"

    )

# =========================

# РЕГИСТРАЦИЯ МАГАЗИНА

# =========================

@dp.message(StoreRegistration.store_name)

async def store_name_handler(message: Message, state: FSMContext):

    await state.update_data(store_name=message.text)

    await state.set_state(StoreRegistration.contact_name)

    await message.answer("Введите имя контактного лица:")

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

    await message.answer("Введите адрес магазина или склада:")

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

        reply_markup=registration_confirm_keyboard,

    )

@dp.message(

    StoreRegistration.confirm,

    F.text == "✅ Отправить заявку"

)

async def store_confirm_handler(message: Message, state: FSMContext):

    data = await state.get_data()

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

            message.from_user.id,

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

@dp.message(

    StoreRegistration.confirm,

    F.text == "❌ Отмена"

)

async def registration_cancel(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(

        "Регистрация отменена.",

        reply_markup=main_keyboard,

    )

# =========================

# ПРОФИЛЬ МАГАЗИНА

# =========================

@dp.message(F.text == "🏪 Профиль магазина")

async def store_profile(message: Message):

    async with db_pool.acquire() as conn:

        store = await conn.fetchrow(

            """

            SELECT *

            FROM stores

            WHERE telegram_id = $1

            """,

            message.from_user.id,

        )

    if not store:

        await message.answer("Магазин не найден.")

        return

    await message.answer(

        "🏪 ПРОФИЛЬ МАГАЗИНА\n\n"

        f"Название: {store['store_name']}\n"

        f"👤 Контакт: {store['contact_name']}\n"

        f"📞 Телефон: {store['phone']}\n"

        f"📍 Адрес: {store['address']}\n"

        f"✅ Статус: {store['status']}"

    )

# =========================

# СОЗДАНИЕ ЗАКАЗА

# =========================

@dp.message(F.text == "➕ Создать заказ")

async def create_order_start(message: Message, state: FSMContext):

    async with db_pool.acquire() as conn:

        store = await conn.fetchrow(

            """

            SELECT id, status

            FROM stores

            WHERE telegram_id = $1

            """,

            message.from_user.id,

        )

    if not store or store["status"] != "approved":

        await message.answer(

            "❌ Создавать заказы может только одобренный магазин."

        )

        return

    await state.clear()

    await state.set_state(OrderCreation.client_name)

    await message.answer(

        "📦 СОЗДАНИЕ ЗАКАЗА\n\n"

        "Введите имя клиента:"

    )

@dp.message(OrderCreation.client_name)

async def order_client_name(message: Message, state: FSMContext):

    await state.update_data(client_name=message.text)

    await state.set_state(OrderCreation.client_phone)

    await message.answer(

        "📞 Введите номер телефона клиента:"

    )

@dp.message(OrderCreation.client_phone)

async def order_client_phone(message: Message, state: FSMContext):

    await state.update_data(client_phone=message.text)

    await state.set_state(OrderCreation.delivery_address)

    await message.answer(

        "📍 Введите адрес доставки клиента:"

    )

@dp.message(OrderCreation.delivery_address)

async def order_delivery_address(message: Message, state: FSMContext):

    await state.update_data(delivery_address=message.text)

    await state.set_state(OrderCreation.item)

    await message.answer(

        "📦 Что нужно доставить?\n\n"

        "Например:\n"

        "Холодильник HOFMANN — 1 шт."

    )

@dp.message(OrderCreation.item)

async def order_item(message: Message, state: FSMContext):

    await state.update_data(item=message.text)

    await state.set_state(OrderCreation.delivery_time)

    await message.answer(

        "🕐 Укажите желаемое время доставки.\n\n"

        "Например:\n"

        "Сегодня 15:00–18:00"

    )

@dp.message(OrderCreation.delivery_time)

async def order_delivery_time(message: Message, state: FSMContext):

    await state.update_data(delivery_time=message.text)

    await state.set_state(OrderCreation.comment)

    await message.answer(

        "📝 Добавьте комментарий к заказу.\n\n"

        "Например: 5 этаж, есть лифт.\n\n"

        "Если комментария нет — напишите: Нет"

    )

@dp.message(OrderCreation.comment)

async def order_comment(message: Message, state: FSMContext):

    await state.update_data(comment=message.text)

    data = await state.get_data()

    async with db_pool.acquire() as conn:

        store = await conn.fetchrow(

            """

            SELECT store_name, address

            FROM stores

            WHERE telegram_id = $1

            """,

            message.from_user.id,

        )

    await state.set_state(OrderCreation.confirm)

    await message.answer(

        "📦 ПРОВЕРЬТЕ ЗАКАЗ\n\n"

        f"🏪 Магазин: {store['store_name']}\n"

        f"📍 Забрать: {store['address']}\n\n"

        f"👤 Клиент: {data['client_name']}\n"

        f"📞 Телефон: {data['client_phone']}\n"

        f"📍 Доставить: {data['delivery_address']}\n\n"

        f"📦 Товар: {data['item']}\n"

        f"🕐 Время: {data['delivery_time']}\n"

        f"📝 Комментарий: {data['comment']}\n\n"

        "Создать заказ?",

        reply_markup=order_confirm_keyboard,

    )

@dp.message(

    OrderCreation.confirm,

    F.text == "✅ Создать заказ"

)

async def order_confirm(message: Message, state: FSMContext):

    data = await state.get_data()

    async with db_pool.acquire() as conn:

        store = await conn.fetchrow(

            """

            SELECT id, address

            FROM stores

            WHERE telegram_id = $1

              AND status = 'approved'

            """,

            message.from_user.id,

        )

        if not store:

            await state.clear()

            await message.answer(

                "❌ Магазин не найден или не одобрен.",

                reply_markup=main_keyboard,

            )

            return

        order_id = await conn.fetchval(

            """

            INSERT INTO orders (

                store_id,

                client_name,

                client_phone,

                pickup_address,

                delivery_address,

                item,

                delivery_time,

                comment

            )

            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)

            RETURNING id

            """,

            store["id"],

            data["client_name"],

            data["client_phone"],

            store["address"],

            data["delivery_address"],

            data["item"],

            data["delivery_time"],

            data["comment"],

        )

    await state.clear()

    await message.answer(

        f"✅ Заказ №{order_id} создан!\n\n"

        "Статус: 🆕 Новый\n\n"

        "Теперь заказ доступен администратору.",

        reply_markup=store_keyboard,

    )

@dp.message(

    OrderCreation.confirm,

    F.text == "❌ Отменить заказ"

)

async def order_cancel(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(

        "❌ Создание заказа отменено.",

        reply_markup=store_keyboard,

    )

# =========================

# МОИ ЗАКАЗЫ

# =========================

@dp.message(F.text == "📦 Мои заказы")

async def my_orders(message: Message):

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(

            """

            SELECT

                o.id,

                o.client_name,

                o.delivery_address,

                o.item,

                o.status,

                o.created_at

            FROM orders o

            JOIN stores s ON s.id = o.store_id

            WHERE s.telegram_id = $1

            ORDER BY o.id DESC

            LIMIT 10

            """,

            message.from_user.id,

        )

    if not orders:

        await message.answer(

            "📦 У вас пока нет заказов."

        )

        return

    status_map = {

        "new": "🆕 Новый",

        "assigned": "🚚 Назначен курьер",

        "picked_up": "📦 Забран",

        "on_the_way": "🚗 В пути",

        "arrived": "📍 Курьер прибыл",

        "delivered": "✅ Доставлен",

        "problem": "🚨 Проблема",

        "cancelled": "❌ Отменён",

    }

    text = "📦 ВАШИ ПОСЛЕДНИЕ ЗАКАЗЫ\n\n"

    for order in orders:

        text += (

            f"№{order['id']} — "

            f"{status_map.get(order['status'], order['status'])}\n"

            f"👤 {order['client_name']}\n"

            f"📍 {order['delivery_address']}\n"

            f"📦 {order['item']}\n\n"

        )

    await message.answer(text)

# =========================

# АДМИНИСТРАТОР

# =========================

@dp.message(F.text == "👨‍💼 Администратор")

async def admin_handler(message: Message):

    async with db_pool.acquire() as conn:

        stores = await conn.fetch(

            """

            SELECT

                id,

                telegram_id,

                store_name,

                contact_name,

                phone,

                address

            FROM stores

            WHERE status = 'pending'

            ORDER BY created_at ASC

            """

        )

        new_orders = await conn.fetch(

            """

            SELECT

                o.id,

                o.client_name,

                o.client_phone,

                o.pickup_address,

                o.delivery_address,

                o.item,

                o.delivery_time,

                o.comment,

                s.store_name

            FROM orders o

            JOIN stores s ON s.id = o.store_id

            WHERE o.status = 'new'

            ORDER BY o.created_at ASC

            """

        )

    await message.answer(

        "👨‍💼 АДМИНИСТРАТОР\n\n"

        f"🏪 Новых заявок магазинов: {len(stores)}\n"

        f"📦 Новых заказов: {len(new_orders)}"

    )

    for store in stores:

        keyboard = InlineKeyboardMarkup(

            inline_keyboard=[

                [

                    InlineKeyboardButton(

                        text="✅ Одобрить",

                        callback_data=f"approve_store:{store['id']}",

                    ),

                    InlineKeyboardButton(

                        text="❌ Отклонить",

                        callback_data=f"reject_store:{store['id']}",

                    ),

                ]

            ]

        )

        await message.answer(

            "🏪 ЗАЯВКА МАГАЗИНА\n\n"

            f"Название: {store['store_name']}\n"

            f"👤 Контакт: {store['contact_name']}\n"

            f"📞 Телефон: {store['phone']}\n"

            f"📍 Адрес: {store['address']}",

            reply_markup=keyboard,

        )

    for order in new_orders:

        await message.answer(

            f"🆕 НОВЫЙ ЗАКАЗ №{order['id']}\n\n"

            f"🏪 Магазин: {order['store_name']}\n"

            f"📍 Забрать: {order['pickup_address']}\n\n"

            f"👤 Клиент: {order['client_name']}\n"

            f"📞 {order['client_phone']}\n"

            f"📍 Доставить: {order['delivery_address']}\n\n"

            f"📦 {order['item']}\n"

            f"🕐 {order['delivery_time']}\n"

            f"📝 {order['comment']}"

        )

@dp.callback_query(F.data.startswith("approve_store:"))

async def approve_store_handler(callback: CallbackQuery):

    store_id = int(callback.data.split(":")[1])

    async with db_pool.acquire() as conn:

        store = await conn.fetchrow(

            """

            UPDATE stores

            SET status = 'approved'

            WHERE id = $1

            RETURNING telegram_id, store_name

            """,

            store_id,

        )

    if not store:

        await callback.answer("Заявка не найдена.")

        return

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(

        f"✅ Магазин {store['store_name']} одобрен."

    )

    try:

        await bot.send_message(

            store["telegram_id"],

            "✅ Ваша заявка одобрена!\n\n"

            f"🏪 Магазин: {store['store_name']}\n"

            "Теперь вы можете пользоваться системой доставки.",

        )

    except Exception:

        pass

    await callback.answer("Магазин одобрен.")

@dp.callback_query(F.data.startswith("reject_store:"))

async def reject_store_handler(callback: CallbackQuery):

    store_id = int(callback.data.split(":")[1])

    async with db_pool.acquire() as conn:

        store = await conn.fetchrow(

            """

            UPDATE stores

            SET status = 'rejected'

            WHERE id = $1

            RETURNING telegram_id, store_name

            """,

            store_id,

        )

    if not store:

        await callback.answer("Заявка не найдена.")

        return

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(

        f"❌ Магазин {store['store_name']} отклонён."

    )

    try:

        await bot.send_message(

            store["telegram_id"],

            "❌ Ваша заявка магазина была отклонена."

        )

    except Exception:

        pass

    await callback.answer("Заявка отклонена.")

# =========================

# КУРЬЕР

# =========================

@dp.message(F.text == "🚚 Курьер")

async def courier_handler(message: Message):

    await message.answer(

        "🚚 Раздел курьера.\n\n"

        "Регистрацию и назначение заказов курьеру "

        "добавим следующим этапом."

    )

# =========================

# FALLBACK

# =========================

@dp.message()

async def fallback_handler(message: Message):

    await message.answer(

        "Пожалуйста, используйте кнопки меню."

    )

# =========================

# RUN

# =========================

async def main():

    print("Connecting to PostgreSQL...")

    await init_db()

    print("Database connected.")

    print("Bot is starting...")

    await dp.start_polling(bot)

if __name__ == "__main__":

    asyncio.run(main())
