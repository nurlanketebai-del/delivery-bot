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

courier_keyboard = ReplyKeyboardMarkup(

    keyboard=[

        [KeyboardButton(text="📦 Мои доставки")],

        [KeyboardButton(text="🚚 Профиль курьера")],

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

class CourierRegistration(StatesGroup):

    full_name = State()

    phone = State()

    vehicle = State()

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

            CREATE TABLE IF NOT EXISTS couriers (

                id SERIAL PRIMARY KEY,

                telegram_id BIGINT NOT NULL UNIQUE,

                full_name TEXT NOT NULL,

                phone TEXT NOT NULL,

                vehicle TEXT NOT NULL,

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

                courier_id INTEGER REFERENCES couriers(id),

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

            "SELECT * FROM stores WHERE telegram_id = $1",

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

@dp.message(StoreRegistration.store_name)

async def store_name_handler(message: Message, state: FSMContext):

    await state.update_data(store_name=message.text)

    await state.set_state(StoreRegistration.contact_name)

    await message.answer("Введите имя контактного лица:")

@dp.message(StoreRegistration.contact_name)

async def contact_name_handler(message: Message, state: FSMContext):

    await state.update_data(contact_name=message.text)

    await state.set_state(StoreRegistration.phone)

    await message.answer("Введите номер телефона:")

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

@dp.message(StoreRegistration.confirm, F.text == "✅ Отправить заявку")

async def store_confirm_handler(message: Message, state: FSMContext):

    data = await state.get_data()

    async with db_pool.acquire() as conn:

        await conn.execute(

            """

            INSERT INTO stores (

                telegram_id, store_name, contact_name, phone, address

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

# =========================

# КУРЬЕР

# =========================

@dp.message(F.text == "🚚 Курьер")

async def courier_start(message: Message, state: FSMContext):

    telegram_id = message.from_user.id

    async with db_pool.acquire() as conn:

        courier = await conn.fetchrow(

            "SELECT * FROM couriers WHERE telegram_id = $1",

            telegram_id,

        )

    if courier:

        if courier["status"] == "approved":

            await message.answer(

                f"🚚 Курьер: {courier['full_name']}\n\n"

                "Выберите действие:",

                reply_markup=courier_keyboard,

            )

            return

        if courier["status"] == "pending":

            await message.answer(

                "⏳ Ваша заявка курьера ожидает подтверждения администратора."

            )

            return

        if courier["status"] == "rejected":

            await message.answer(

                "❌ Ваша заявка курьера была отклонена."

            )

            return

    await state.set_state(CourierRegistration.full_name)

    await message.answer(

        "🚚 РЕГИСТРАЦИЯ КУРЬЕРА\n\n"

        "Введите ваше имя:"

    )

@dp.message(CourierRegistration.full_name)

async def courier_name_handler(message: Message, state: FSMContext):

    await state.update_data(full_name=message.text)

    await state.set_state(CourierRegistration.phone)

    await message.answer("📞 Введите номер телефона:")

@dp.message(CourierRegistration.phone)

async def courier_phone_handler(message: Message, state: FSMContext):

    await state.update_data(phone=message.text)

    await state.set_state(CourierRegistration.vehicle)

    await message.answer(

        "🚗 Укажите транспорт.\n\n"

        "Например: KYC T3 / легковой автомобиль / без автомобиля"

    )

@dp.message(CourierRegistration.vehicle)

async def courier_vehicle_handler(message: Message, state: FSMContext):

    await state.update_data(vehicle=message.text)

    data = await state.get_data()

    await state.set_state(CourierRegistration.confirm)

    await message.answer(

        "Проверьте данные:\n\n"

        f"👤 Имя: {data['full_name']}\n"

        f"📞 Телефон: {data['phone']}\n"

        f"🚗 Транспорт: {data['vehicle']}\n\n"

        "Отправить заявку?",

        reply_markup=registration_confirm_keyboard,

    )

@dp.message(CourierRegistration.confirm, F.text == "✅ Отправить заявку")

async def courier_confirm_handler(message: Message, state: FSMContext):

    data = await state.get_data()

    async with db_pool.acquire() as conn:

        await conn.execute(

            """

            INSERT INTO couriers (

                telegram_id, full_name, phone, vehicle

            )

            VALUES ($1, $2, $3, $4)

            ON CONFLICT (telegram_id)

            DO UPDATE SET

                full_name = EXCLUDED.full_name,

                phone = EXCLUDED.phone,

                vehicle = EXCLUDED.vehicle,

                status = 'pending'

            """,

            message.from_user.id,

            data["full_name"],

            data["phone"],

            data["vehicle"],

        )

    await state.clear()

    await message.answer(

        "✅ Заявка курьера отправлена!\n\n"

        "⏳ Ожидайте подтверждения администратора.",

        reply_markup=main_keyboard,

    )

@dp.message(F.text == "🚚 Профиль курьера")

async def courier_profile(message: Message):

    async with db_pool.acquire() as conn:

        courier = await conn.fetchrow(

            "SELECT * FROM couriers WHERE telegram_id = $1",

            message.from_user.id,

        )

    if not courier:

        await message.answer("Курьер не найден.")

        return

    await message.answer(

        "🚚 ПРОФИЛЬ КУРЬЕРА\n\n"

        f"👤 {courier['full_name']}\n"

        f"📞 {courier['phone']}\n"

        f"🚗 {courier['vehicle']}\n"

        f"Статус: {courier['status']}"

    )

@dp.message(F.text == "📦 Мои доставки")

async def courier_orders(message: Message):

    async with db_pool.acquire() as conn:

        courier = await conn.fetchrow(

            """

            SELECT id

            FROM couriers

            WHERE telegram_id = $1

              AND status = 'approved'

            """,

            message.from_user.id,

        )

        if not courier:

            await message.answer(

                "❌ Вы не зарегистрированы как одобренный курьер."

            )

            return

        orders = await conn.fetch(

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

                o.status,

                s.store_name

            FROM orders o

            JOIN stores s ON s.id = o.store_id

            WHERE o.courier_id = $1

            ORDER BY o.id DESC

            """,

            courier["id"],

        )

    if not orders:

        await message.answer(

            "📦 У вас пока нет назначенных доставок."

        )

        return

    for order in orders:

        await message.answer(

            f"🚚 ЗАКАЗ №{order['id']}\n\n"

            f"🏪 Магазин: {order['store_name']}\n"

            f"📍 Забрать: {order['pickup_address']}\n\n"

            f"👤 Клиент: {order['client_name']}\n"

            f"📞 {order['client_phone']}\n"

            f"📍 Доставить: {order['delivery_address']}\n\n"

            f"📦 {order['item']}\n"

            f"🕐 {order['delivery_time']}\n"

            f"📝 {order['comment']}\n\n"

            f"Статус: {order['status']}"

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

    await message.answer("📞 Введите номер телефона клиента:")

@dp.message(OrderCreation.client_phone)

async def order_client_phone(message: Message, state: FSMContext):

    await state.update_data(client_phone=message.text)

    await state.set_state(OrderCreation.delivery_address)

    await message.answer("📍 Введите адрес доставки клиента:")

@dp.message(OrderCreation.delivery_address)

async def order_delivery_address(message: Message, state: FSMContext):

    await state.update_data(delivery_address=message.text)

    await state.set_state(OrderCreation.item)

    await message.answer("📦 Что нужно доставить?")

@dp.message(OrderCreation.item)

async def order_item(message: Message, state: FSMContext):

    await state.update_data(item=message.text)

    await state.set_state(OrderCreation.delivery_time)

    await message.answer("🕐 Укажите желаемое время доставки:")

@dp.message(OrderCreation.delivery_time)

async def order_delivery_time(message: Message, state: FSMContext):

    await state.update_data(delivery_time=message.text)

    await state.set_state(OrderCreation.comment)

    await message.answer(

        "📝 Добавьте комментарий.\n"

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

@dp.message(OrderCreation.confirm, F.text == "✅ Создать заказ")

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

        "Статус: 🆕 Новый",

        reply_markup=store_keyboard,

    )

@dp.message(F.text == "📦 Мои заказы")

async def my_orders(message: Message):

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(

            """

            SELECT o.id, o.client_name, o.delivery_address, o.item, o.status

            FROM orders o

            JOIN stores s ON s.id = o.store_id

            WHERE s.telegram_id = $1

            ORDER BY o.id DESC

            LIMIT 10

            """,

            message.from_user.id,

        )

    if not orders:

        await message.answer("📦 У вас пока нет заказов.")

        return

    text = "📦 ВАШИ ЗАКАЗЫ\n\n"

    for order in orders:

        text += (

            f"№{order['id']} — {order['status']}\n"

            f"👤 {order['client_name']}\n"

            f"📍 {order['delivery_address']}\n"

            f"📦 {order['item']}\n\n"

        )

    await message.answer(text)

@dp.message(F.text == "🏪 Профиль магазина")

async def store_profile(message: Message):

    async with db_pool.acquire() as conn:

        store = await conn.fetchrow(

            "SELECT * FROM stores WHERE telegram_id = $1",

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

        f"Статус: {store['status']}"

    )

# =========================

# АДМИНИСТРАТОР

# =========================

@dp.message(F.text == "👨‍💼 Администратор")

async def admin_handler(message: Message):

    async with db_pool.acquire() as conn:

        stores = await conn.fetch(

            """

            SELECT *

            FROM stores

            WHERE status = 'pending'

            ORDER BY created_at ASC

            """

        )

        couriers = await conn.fetch(

            """

            SELECT *

            FROM couriers

            WHERE status = 'pending'

            ORDER BY created_at ASC

            """

        )

        new_orders = await conn.fetch(

            """

            SELECT

                o.*,

                s.store_name

            FROM orders o

            JOIN stores s ON s.id = o.store_id

            WHERE o.status = 'new'

            ORDER BY o.created_at ASC

            """

        )

        approved_couriers = await conn.fetch(

            """

            SELECT id, full_name, vehicle

            FROM couriers

            WHERE status = 'approved'

            ORDER BY full_name

            """

        )

    await message.answer(

        "👨‍💼 АДМИНИСТРАТОР\n\n"

        f"🏪 Заявок магазинов: {len(stores)}\n"

        f"🚚 Заявок курьеров: {len(couriers)}\n"

        f"📦 Новых заказов: {len(new_orders)}"

    )

    for store in stores:

        keyboard = InlineKeyboardMarkup(

            inline_keyboard=[[

                InlineKeyboardButton(

                    text="✅ Одобрить",

                    callback_data=f"approve_store:{store['id']}",

                ),

                InlineKeyboardButton(

                    text="❌ Отклонить",

                    callback_data=f"reject_store:{store['id']}",

                ),

            ]]

        )

        await message.answer(

            "🏪 ЗАЯВКА МАГАЗИНА\n\n"

            f"Название: {store['store_name']}\n"

            f"👤 {store['contact_name']}\n"

            f"📞 {store['phone']}\n"

            f"📍 {store['address']}",

            reply_markup=keyboard,

        )

    for courier in couriers:

        keyboard = InlineKeyboardMarkup(

            inline_keyboard=[[

                InlineKeyboardButton(

                    text="✅ Одобрить курьера",

                    callback_data=f"approve_courier:{courier['id']}",

                ),

                InlineKeyboardButton(

                    text="❌ Отклонить",

                    callback_data=f"reject_courier:{courier['id']}",

                ),

            ]]

        )

        await message.answer(

            "🚚 ЗАЯВКА КУРЬЕРА\n\n"

            f"👤 {courier['full_name']}\n"

            f"📞 {courier['phone']}\n"

            f"🚗 {courier['vehicle']}",

            reply_markup=keyboard,

        )

    for order in new_orders:

        if approved_couriers:

            buttons = []

            for courier in approved_couriers:

                buttons.append([

                    InlineKeyboardButton(

                        text=f"🚚 {courier['full_name']}",

                        callback_data=(

                            f"assign:{order['id']}:{courier['id']}"

                        ),

                    )

                ])

            keyboard = InlineKeyboardMarkup(

                inline_keyboard=buttons

            )

        else:

            keyboard = None

        text = (

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

        if not approved_couriers:

            text += "\n\n⚠️ Нет одобренных курьеров."

        await message.answer(

            text,

            reply_markup=keyboard,

        )

# =========================

# CALLBACKS

# =========================

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

    await bot.send_message(

        store["telegram_id"],

        "✅ Ваша заявка магазина одобрена!"

    )

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

            RETURNING telegram_id

            """,

            store_id,

        )

    if store:

        await bot.send_message(

            store["telegram_id"],

            "❌ Ваша заявка магазина отклонена."

        )

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.answer("Заявка отклонена.")

@dp.callback_query(F.data.startswith("approve_courier:"))

async def approve_courier_handler(callback: CallbackQuery):

    courier_id = int(callback.data.split(":")[1])

    async with db_pool.acquire() as conn:

        courier = await conn.fetchrow(

            """

            UPDATE couriers

            SET status = 'approved'

            WHERE id = $1

            RETURNING telegram_id, full_name

            """,

            courier_id,

        )

    if not courier:

        await callback.answer("Курьер не найден.")

        return

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(

        f"✅ Курьер {courier['full_name']} одобрен."

    )

    await bot.send_message(

        courier["telegram_id"],

        "✅ Ваша заявка курьера одобрена!\n\n"

        "Теперь администратор может назначать вам заказы."

    )

    await callback.answer("Курьер одобрен.")

@dp.callback_query(F.data.startswith("reject_courier:"))

async def reject_courier_handler(callback: CallbackQuery):

    courier_id = int(callback.data.split(":")[1])

    async with db_pool.acquire() as conn:

        courier = await conn.fetchrow(

            """

            UPDATE couriers

            SET status = 'rejected'

            WHERE id = $1

            RETURNING telegram_id

            """,

            courier_id,

        )

    if courier:

        await bot.send_message(

            courier["telegram_id"],

            "❌ Ваша заявка курьера отклонена."

        )

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.answer("Курьер отклонён.")

@dp.callback_query(F.data.startswith("assign:"))

async def assign_order_handler(callback: CallbackQuery):

    _, order_id, courier_id = callback.data.split(":")

    order_id = int(order_id)

    courier_id = int(courier_id)

    async with db_pool.acquire() as conn:

        result = await conn.fetchrow(

            """

            UPDATE orders

            SET

                courier_id = $1,

                status = 'assigned'

            WHERE id = $2

              AND status = 'new'

            RETURNING id

            """,

            courier_id,

            order_id,

        )

        courier = await conn.fetchrow(

            """

            SELECT telegram_id, full_name

            FROM couriers

            WHERE id = $1

              AND status = 'approved'

            """,

            courier_id,

        )

        order = await conn.fetchrow(

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

            WHERE o.id = $1

            """,

            order_id,

        )

    if not result or not courier or not order:

        await callback.answer(

            "Не удалось назначить заказ.",

            show_alert=True,

        )

        return

    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(

        f"✅ Заказ №{order_id} назначен курьеру "

        f"{courier['full_name']}."

    )

    await bot.send_message(

        courier["telegram_id"],

        f"🚚 ВАМ НАЗНАЧЕН ЗАКАЗ №{order['id']}\n\n"

        f"🏪 Магазин: {order['store_name']}\n"

        f"📍 Забрать: {order['pickup_address']}\n\n"

        f"👤 Клиент: {order['client_name']}\n"

        f"📞 {order['client_phone']}\n"

        f"📍 Доставить: {order['delivery_address']}\n\n"

        f"📦 {order['item']}\n"

        f"🕐 {order['delivery_time']}\n"

        f"📝 {order['comment']}"

    )

    await callback.answer("Заказ назначен.")

# =========================

# ОТМЕНА РЕГИСТРАЦИИ/ЗАКАЗА

# =========================

@dp.message(F.text == "❌ Отмена")

async def cancel_handler(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(

        "Действие отменено.",

        reply_markup=main_keyboard,

    )

@dp.message(F.text == "❌ Отменить заказ")

async def cancel_order_handler(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(

        "Создание заказа отменено.",

        reply_markup=store_keyboard,

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
