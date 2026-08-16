import os
import asyncio
import secrets
import string
from decimal import Decimal, InvalidOperation

import asyncpg

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "0")

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    ADMIN_ID = 0

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")


bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

db_pool = None


# =========================================================
# СТАТУСЫ
# =========================================================

STATUS_NAMES = {
    "new": "🆕 Новый",
    "assigned": "🚚 Назначен курьер",
    "accepted": "✅ Курьер принял",
    "pickup_photo": "📸 Фото получения",
    "picked_up": "📦 Товар забран",
    "on_the_way": "🚗 В пути",
    "arrived": "📍 Курьер прибыл",
    "delivery_photo": "📸 Фото доставки",
    "delivered": "✅ Доставлен",
    "cancelled": "❌ Отменён",
}


# =========================================================
# FSM
# =========================================================

class StoreRegistration(StatesGroup):
    store_name = State()
    contact_name = State()
    phone = State()
    address = State()
    confirm = State()


class StoreJoin(StatesGroup):
    invite_code = State()


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
    kittek_order_number = State()
    kaspi_order_number = State()
    delivery_time = State()
    comment = State()
    confirm = State()


class OrderEdit(StatesGroup):
    value = State()


class CourierPhoto(StatesGroup):
    pickup_photo = State()
    delivery_photo = State()


class AdminSearch(StatesGroup):
    order_id = State()


class AdminPrice(StatesGroup):
    value = State()


# =========================================================
# БАЗА ДАННЫХ
# =========================================================

async def init_db():

    global db_pool

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=1,
        max_size=10,
    )

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
                courier_id INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_photos (
                id SERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(id),
                courier_id INTEGER NOT NULL REFERENCES couriers(id),
                photo_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS store_users (
                id SERIAL PRIMARY KEY,
                store_id INTEGER NOT NULL
                    REFERENCES stores(id) ON DELETE CASCADE,
                telegram_id BIGINT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                member_role TEXT NOT NULL DEFAULT 'manager',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS store_invites (
                id SERIAL PRIMARY KEY,
                store_id INTEGER NOT NULL
                    REFERENCES stores(id) ON DELETE CASCADE,
                code TEXT NOT NULL UNIQUE,
                created_by BIGINT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                used_by BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                used_at TIMESTAMPTZ
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_status_history (
                id SERIAL PRIMARY KEY,
                order_id INTEGER NOT NULL
                    REFERENCES orders(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                actor_type TEXT,
                actor_telegram_id BIGINT,
                note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS created_by_telegram_id BIGINT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS delivery_price NUMERIC(12,2)
            NOT NULL DEFAULT 0
            """
        )
        await conn.execute(
    """
    ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS kittek_order_number TEXT
    """
)

await conn.execute(
    """
    ALTER TABLE orders
    ADD COLUMN IF NOT EXISTS kaspi_order_number TEXT
    """
)

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ
            NOT NULL DEFAULT NOW()
            """
        )

        # Перенос старых владельцев магазинов
        await conn.execute(
            """
            INSERT INTO store_users (
                store_id,
                telegram_id,
                full_name,
                member_role
            )

            SELECT
                id,
                telegram_id,
                contact_name,
                'owner'

            FROM stores

            ON CONFLICT (telegram_id)
            DO NOTHING
            """
        )

        # Создаём первоначальную историю для старых заказов,
        # только если у заказа истории ещё нет.
        await conn.execute(
            """
            INSERT INTO order_status_history (
                order_id,
                status,
                actor_type,
                note,
                created_at
            )

            SELECT
                o.id,
                o.status,
                'system',
                'Импорт существующего заказа',
                o.created_at

            FROM orders o

            WHERE NOT EXISTS (
                SELECT 1
                FROM order_status_history h
                WHERE h.order_id = o.id
            )
            """
        )


# =========================================================
# ОБЩИЕ ФУНКЦИИ
# =========================================================

def is_admin(user_id: int) -> bool:
    return (
        ADMIN_ID != 0
        and user_id == ADMIN_ID
    )


def price_text(value) -> str:

    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal("0")

    if value <= 0:
        return "Не указана"

    if value == value.to_integral():
        return f"{int(value):,}".replace(",", " ") + " ₸"

    return f"{value:,.2f}".replace(",", " ") + " ₸"


async def get_store_membership(user_id: int):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT
                su.id AS store_user_id,
                su.store_id,
                su.telegram_id,
                su.full_name,
                su.member_role,

                s.store_name,
                s.contact_name,
                s.phone,
                s.address,
                s.status

            FROM store_users su

            JOIN stores s
                ON s.id = su.store_id

            WHERE su.telegram_id = $1
            """,
            user_id,
        )


async def get_courier(user_id: int):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT *
            FROM couriers
            WHERE telegram_id = $1
            """,
            user_id,
        )


async def get_approved_courier_id(
    user_id: int
):

    async with db_pool.acquire() as conn:

        return await conn.fetchval(
            """
            SELECT id

            FROM couriers

            WHERE telegram_id = $1
              AND status = 'approved'
            """,
            user_id,
        )


async def get_user_role(user_id: int):

    store = await get_store_membership(
        user_id
    )

    courier = await get_courier(
        user_id
    )

    if store and store["status"] == "approved":
        return "store", store

    if courier and courier["status"] == "approved":
        return "courier", courier

    if store and store["status"] == "pending":
        return "store", store

    if courier and courier["status"] == "pending":
        return "courier", courier

    return None, None


async def deny_admin_message(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):

        await message.answer(
            "❌ Доступ запрещён.\n\n"
            "Этот раздел доступен "
            "только администратору."
        )

        return True

    return False


async def deny_admin_callback(
    callback: CallbackQuery
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ У вас нет прав администратора.",
            show_alert=True,
        )

        return True

    return False


async def add_history(
    conn,
    order_id: int,
    status: str,
    actor_type: str = None,
    actor_telegram_id: int = None,
    note: str = None,
):

    await conn.execute(
        """
        INSERT INTO order_status_history (
            order_id,
            status,
            actor_type,
            actor_telegram_id,
            note
        )

        VALUES ($1,$2,$3,$4,$5)
        """,
        order_id,
        status,
        actor_type,
        actor_telegram_id,
        note,
    )


async def notify_store_users(
    store_id: int,
    text: str,
):

    async with db_pool.acquire() as conn:

        users = await conn.fetch(
            """
            SELECT telegram_id

            FROM store_users

            WHERE store_id = $1
            """,
            store_id,
        )

    for user in users:

        try:
            await bot.send_message(
                user["telegram_id"],
                text,
            )
        except Exception:
            pass


async def notify_courier(
    courier_id: int,
    text: str,
):

    if not courier_id:
        return

    async with db_pool.acquire() as conn:

        telegram_id = await conn.fetchval(
            """
            SELECT telegram_id

            FROM couriers

            WHERE id = $1
            """,
            courier_id,
        )

    if telegram_id:

        try:
            await bot.send_message(
                telegram_id,
                text,
            )
        except Exception:
            pass


async def get_order_full(
    order_id: int
):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT
                o.*,

                s.store_name,

                c.full_name AS courier_name,
                c.phone AS courier_phone,
                c.vehicle AS courier_vehicle,

                su.full_name AS created_by

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

            LEFT JOIN couriers c
                ON c.id = o.courier_id

            LEFT JOIN store_users su
                ON su.telegram_id =
                   o.created_by_telegram_id

            WHERE o.id = $1
            """,
            order_id,
        )


def build_order_text(
    order,
    title=None,
):

    title = (
        title
        or f"📦 ЗАКАЗ №{order['id']}"
    )

    courier_name = (
        order["courier_name"]
        if "courier_name" in order
        and order["courier_name"]
        else "Не назначен"
    )

    author = (
        order["created_by"]
        if "created_by" in order
        and order["created_by"]
        else "Не указан"
    )

    status = STATUS_NAMES.get(
        order["status"],
        order["status"],
    )

    return (
        f"{title}\n\n"

        f"Статус: {status}\n"
        f"💰 Стоимость: "
        f"{price_text(order['delivery_price'])}\n\n"

        f"🏪 Магазин: "
        f"{order['store_name']}\n"

        f"👤 Создал: {author}\n"

        f"📍 Забрать: "
        f"{order['pickup_address']}\n\n"

        f"👤 Клиент: "
        f"{order['client_name']}\n"

        f"📞 {order['client_phone']}\n"

        f"📍 Доставить: "
        f"{order['delivery_address']}\n\n"

        f"📦 {order['item']}\n"

        f"🕐 {order['delivery_time']}\n"

        f"📝 {order['comment']}\n\n"

        f"🚚 Курьер: {courier_name}"
    )


# =========================================================
# КЛАВИАТУРЫ
# =========================================================

def main_keyboard(
    role,
    user_id: int,
):

    rows = []

    if role == "store":

        rows.append([
            KeyboardButton(
                text="🏪 Магазин"
            )
        ])

    elif role == "courier":

        rows.append([
            KeyboardButton(
                text="🚚 Курьер"
            )
        ])

    else:

        rows.append([
            KeyboardButton(
                text="🏪 Магазин"
            ),
            KeyboardButton(
                text="🚚 Курьер"
            ),
        ])

    if is_admin(user_id):

        rows.append([
            KeyboardButton(
                text="👨‍💼 Администратор"
            )
        ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
    )


store_entry_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="🆕 Зарегистрировать магазин"
            )
        ],
        [
            KeyboardButton(
                text="🔑 Присоединиться к магазину"
            )
        ],
        [
            KeyboardButton(
                text="⬅️ Главное меню"
            )
        ],
    ],
    resize_keyboard=True,
)


store_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="➕ Создать заказ"
            )
        ],
        [
            KeyboardButton(
                text="📦 Мои заказы"
            ),
            KeyboardButton(
                text="📊 Статистика магазина"
            ),
        ],
        [
            KeyboardButton(
                text="🏪 Профиль магазина"
            ),
            KeyboardButton(
                text="👥 Менеджеры"
            ),
        ],
        [
            KeyboardButton(
                text="⬅️ Главное меню"
            )
        ],
    ],
    resize_keyboard=True,
)


courier_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="📦 Мои доставки"
            )
        ],
        [
            KeyboardButton(
                text="📊 Моя статистика"
            ),
            KeyboardButton(
                text="🚚 Профиль курьера"
            ),
        ],
        [
            KeyboardButton(
                text="⬅️ Главное меню"
            )
        ],
    ],
    resize_keyboard=True,
)

 skip_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏭ Пропустить")],
    ],
    resize_keyboard=True,
)       

registration_confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="✅ Отправить заявку"
            )
        ],
        [
            KeyboardButton(
                text="❌ Отмена"
            )
        ],
    ],
    resize_keyboard=True,
)


order_confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="✅ Создать заказ"
            )
        ],
        [
            KeyboardButton(
                text="❌ Отменить заказ"
            )
        ],
    ],
    resize_keyboard=True,
)


admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="📦 Новые заказы"
            ),
            KeyboardButton(
                text="🚚 Активные"
            ),
        ],
        [
            KeyboardButton(
                text="✅ Доставленные"
            ),
            KeyboardButton(
                text="🔎 Найти заказ"
            ),
        ],
        [
            KeyboardButton(
                text="📊 Статистика"
            ),
        ],
        [
            KeyboardButton(
                text="🏪 Магазины"
            ),
            KeyboardButton(
                text="🚚 Курьеры"
            ),
        ],
        [
            KeyboardButton(
                text="⬅️ Главное меню"
            ),
        ],
    ],
    resize_keyboard=True,
)


async def send_main_menu(
    message: Message
):

    role, _ = await get_user_role(
        message.from_user.id
    )

    await message.answer(
        "Главное меню:",
        reply_markup=main_keyboard(
            role,
            message.from_user.id,
        ),
    )


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start_handler(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    role, info = await get_user_role(
        message.from_user.id
    )

    text = (
        "👋 Добро пожаловать "
        "в систему доставки!"
    )

    if role == "store":

        text += (
            "\n\n🏪 Ваша роль: Магазин"
        )

        if info["status"] == "pending":
            text += (
                "\n⏳ Магазин ожидает одобрения."
            )

    elif role == "courier":

        text += (
            "\n\n🚚 Ваша роль: Курьер"
        )

        if info["status"] == "pending":
            text += (
                "\n⏳ Заявка ожидает одобрения."
            )

    else:

        text += (
            "\n\nВыберите вашу роль:"
        )

    await message.answer(
        text,
        reply_markup=main_keyboard(
            role,
            message.from_user.id,
        ),
    )


@dp.message(Command("myid"))
async def myid_handler(
    message: Message
):

    await message.answer(
        "🆔 Ваш Telegram ID:\n\n"
        f"{message.from_user.id}"
    )


@dp.message(F.text == "⬅️ Главное меню")
async def back_main(
    message: Message,
    state: FSMContext,
):

    await state.clear()
    await send_main_menu(message)


# =========================================================
# МАГАЗИН
# =========================================================

@dp.message(F.text == "🏪 Магазин")
async def store_section(
    message: Message,
    state: FSMContext,
):

    role, info = await get_user_role(
        message.from_user.id
    )

    if role == "courier":

        await message.answer(
            "❌ Вы зарегистрированы "
            "как курьер.\n\n"
            "Один Telegram-аккаунт может "
            "иметь только одну рабочую роль."
        )

        return

    if role == "store":

        if info["status"] == "approved":

            await message.answer(
                f"🏪 {info['store_name']}\n\n"
                "Выберите действие:",
                reply_markup=store_keyboard,
            )

            return

        await message.answer(
            f"🏪 {info['store_name']}\n\n"
            "⏳ Магазин ожидает "
            "подтверждения администратора."
        )

        return

    await message.answer(
        "🏪 МАГАЗИН\n\n"
        "Вы можете зарегистрировать "
        "новый магазин или присоединиться "
        "к существующему магазину "
        "как менеджер.",
        reply_markup=store_entry_keyboard,
    )


# =========================================================
# РЕГИСТРАЦИЯ МАГАЗИНА
# =========================================================

@dp.message(
    F.text == "🆕 Зарегистрировать магазин"
)
async def register_store_start(
    message: Message,
    state: FSMContext,
):

    role, _ = await get_user_role(
        message.from_user.id
    )

    if role:

        await message.answer(
            "❌ У вас уже есть рабочая роль."
        )

        return

    await state.set_state(
        StoreRegistration.store_name
    )

    await message.answer(
        "🏪 РЕГИСТРАЦИЯ МАГАЗИНА\n\n"
        "Введите название магазина:"
    )


@dp.message(
    StoreRegistration.store_name
)
async def register_store_name(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        store_name=message.text
    )

    await state.set_state(
        StoreRegistration.contact_name
    )

    await message.answer(
        "👤 Введите имя контактного лица:"
    )


@dp.message(
    StoreRegistration.contact_name
)
async def register_store_contact(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        contact_name=message.text
    )

    await state.set_state(
        StoreRegistration.phone
    )

    await message.answer(
        "📞 Введите номер телефона:"
    )


@dp.message(
    StoreRegistration.phone
)
async def register_store_phone(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        phone=message.text
    )

    await state.set_state(
        StoreRegistration.address
    )

    await message.answer(
        "📍 Введите адрес магазина "
        "или склада:"
    )


@dp.message(
    StoreRegistration.address
)
async def register_store_address(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        address=message.text
    )

    data = await state.get_data()

    await state.set_state(
        StoreRegistration.confirm
    )

    await message.answer(
        "Проверьте данные:\n\n"

        f"🏪 Магазин: "
        f"{data['store_name']}\n"

        f"👤 Контакт: "
        f"{data['contact_name']}\n"

        f"📞 Телефон: "
        f"{data['phone']}\n"

        f"📍 Адрес: "
        f"{data['address']}\n\n"

        "Отправить заявку?",

        reply_markup=
        registration_confirm_keyboard,
    )


@dp.message(
    StoreRegistration.confirm,
    F.text == "✅ Отправить заявку",
)
async def register_store_confirm(
    message: Message,
    state: FSMContext,
):

    role, _ = await get_user_role(
        message.from_user.id
    )

    if role:

        await state.clear()

        await message.answer(
            "❌ У вас уже есть рабочая роль."
        )

        return

    data = await state.get_data()

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            store_id = await conn.fetchval(
                """
                INSERT INTO stores (
                    telegram_id,
                    store_name,
                    contact_name,
                    phone,
                    address,
                    status
                )

                VALUES (
                    $1,$2,$3,$4,$5,'pending'
                )

                ON CONFLICT (telegram_id)

                DO UPDATE SET
                    store_name =
                        EXCLUDED.store_name,
                    contact_name =
                        EXCLUDED.contact_name,
                    phone =
                        EXCLUDED.phone,
                    address =
                        EXCLUDED.address,
                    status = 'pending'

                RETURNING id
                """,
                message.from_user.id,
                data["store_name"],
                data["contact_name"],
                data["phone"],
                data["address"],
            )

            await conn.execute(
                """
                INSERT INTO store_users (
                    store_id,
                    telegram_id,
                    full_name,
                    member_role
                )

                VALUES (
                    $1,$2,$3,'owner'
                )

                ON CONFLICT (telegram_id)

                DO UPDATE SET
                    store_id =
                        EXCLUDED.store_id,
                    full_name =
                        EXCLUDED.full_name,
                    member_role =
                        'owner'
                """,
                store_id,
                message.from_user.id,
                data["contact_name"],
            )

    await state.clear()

    await message.answer(
        "✅ Заявка магазина отправлена!\n\n"
        "⏳ Ожидайте подтверждения "
        "администратора."
    )

    await send_main_menu(message)


# =========================================================
# ПРИСОЕДИНЕНИЕ К МАГАЗИНУ
# =========================================================

@dp.message(
    F.text == "🔑 Присоединиться к магазину"
)
async def join_store_start(
    message: Message,
    state: FSMContext,
):

    role, _ = await get_user_role(
        message.from_user.id
    )

    if role:

        await message.answer(
            "❌ У вас уже есть рабочая роль."
        )

        return

    await state.set_state(
        StoreJoin.invite_code
    )

    await message.answer(
        "🔑 Введите код приглашения "
        "от магазина:"
    )


@dp.message(StoreJoin.invite_code)
async def join_store_code(
    message: Message,
    state: FSMContext,
):

    code = (
        message.text or ""
    ).strip().upper()

    role, _ = await get_user_role(
        message.from_user.id
    )

    if role:

        await state.clear()

        await message.answer(
            "❌ У вас уже есть рабочая роль."
        )

        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            invite = await conn.fetchrow(
                """
                SELECT
                    si.id,
                    si.store_id,
                    s.store_name,
                    s.status

                FROM store_invites si

                JOIN stores s
                    ON s.id = si.store_id

                WHERE si.code = $1
                  AND si.is_active = TRUE

                FOR UPDATE
                """,
                code,
            )

            if not invite:

                await message.answer(
                    "❌ Код неверный "
                    "или уже использован."
                )

                return

            if invite["status"] != "approved":

                await message.answer(
                    "❌ Этот магазин "
                    "сейчас недоступен."
                )

                return

            await conn.execute(
                """
                INSERT INTO store_users (
                    store_id,
                    telegram_id,
                    full_name,
                    member_role
                )

                VALUES (
                    $1,$2,$3,'manager'
                )

                ON CONFLICT (telegram_id)

                DO UPDATE SET
                    store_id =
                        EXCLUDED.store_id,
                    full_name =
                        EXCLUDED.full_name,
                    member_role =
                        'manager'
                """,
                invite["store_id"],
                message.from_user.id,
                message.from_user.full_name,
            )

            await conn.execute(
                """
                UPDATE store_invites

                SET
                    is_active = FALSE,
                    used_by = $1,
                    used_at = NOW()

                WHERE id = $2
                """,
                message.from_user.id,
                invite["id"],
            )

    await state.clear()

    await message.answer(
        "✅ Вы присоединились "
        "к магазину!\n\n"

        f"🏪 {invite['store_name']}\n"
        "👤 Роль: Менеджер",

        reply_markup=store_keyboard,
    )


# =========================================================
# МЕНЕДЖЕРЫ
# =========================================================

@dp.message(F.text == "👥 Менеджеры")
async def managers_handler(
    message: Message
):

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await message.answer(
            "❌ Вы не привязаны к магазину."
        )

        return

    if membership["status"] != "approved":

        await message.answer(
            "❌ Магазин ещё не одобрен."
        )

        return

    async with db_pool.acquire() as conn:

        members = await conn.fetch(
            """
            SELECT
                id,
                full_name,
                telegram_id,
                member_role

            FROM store_users

            WHERE store_id = $1

            ORDER BY
                CASE
                    WHEN member_role =
                        'owner'
                    THEN 0
                    ELSE 1
                END,
                id
            """,
            membership["store_id"],
        )

    text = (
        "👥 КОМАНДА МАГАЗИНА\n\n"
        f"🏪 {membership['store_name']}\n\n"
    )

    buttons = []

    for member in members:

        if member["member_role"] == "owner":

            text += (
                "👑 Владелец: "
                f"{member['full_name']}\n"
            )

        else:

            text += (
                "👤 Менеджер: "
                f"{member['full_name']}\n"
            )

            if (
                membership["member_role"]
                == "owner"
            ):

                buttons.append([
                    InlineKeyboardButton(
                        text=(
                            "❌ Удалить "
                            f"{member['full_name']}"
                        ),
                        callback_data=(
                            "remove_manager:"
                            f"{member['telegram_id']}"
                        ),
                    )
                ])

    if membership["member_role"] == "owner":

        buttons.append([
            InlineKeyboardButton(
                text="➕ Пригласить менеджера",
                callback_data=
                    "create_manager_invite",
            )
        ])

    keyboard = None

    if buttons:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=buttons
        )

    await message.answer(
        text,
        reply_markup=keyboard,
    )


@dp.callback_query(
    F.data.startswith("remove_manager:")
)
async def remove_manager_confirm(
    callback: CallbackQuery
):

    membership = await get_store_membership(
        callback.from_user.id
    )

    if (
        not membership
        or membership["member_role"]
            != "owner"
        or membership["status"]
            != "approved"
    ):

        await callback.answer(
            "❌ Только владелец может "
            "удалять менеджеров.",
            show_alert=True,
        )

        return

    manager_telegram_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        manager = await conn.fetchrow(
            """
            SELECT
                full_name,
                telegram_id,
                member_role

            FROM store_users

            WHERE store_id = $1
              AND telegram_id = $2
            """,
            membership["store_id"],
            manager_telegram_id,
        )

    if not manager:

        await callback.answer(
            "Менеджер не найден.",
            show_alert=True,
        )

        return

    if manager["member_role"] == "owner":

        await callback.answer(
            "❌ Владельца удалить нельзя.",
            show_alert=True,
        )

        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить",
                    callback_data=(
                        "confirm_remove_manager:"
                        f"{manager_telegram_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Отмена",
                    callback_data=
                        "cancel_remove_manager",
                )
            ],
        ]
    )

    await callback.message.answer(
        "⚠️ УДАЛЕНИЕ МЕНЕДЖЕРА\n\n"

        f"👤 {manager['full_name']}\n\n"

        "Удалить менеджера "
        "из магазина?",

        reply_markup=keyboard,
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith(
        "confirm_remove_manager:"
    )
)
async def confirm_remove_manager(
    callback: CallbackQuery
):

    membership = await get_store_membership(
        callback.from_user.id
    )

    if (
        not membership
        or membership["member_role"]
            != "owner"
        or membership["status"]
            != "approved"
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True,
        )

        return

    manager_telegram_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        manager = await conn.fetchrow(
            """
            SELECT
                full_name,
                telegram_id,
                member_role

            FROM store_users

            WHERE store_id = $1
              AND telegram_id = $2
            """,
            membership["store_id"],
            manager_telegram_id,
        )

        if not manager:

            await callback.answer(
                "Менеджер уже удалён.",
                show_alert=True,
            )

            return

        if manager["member_role"] == "owner":

            await callback.answer(
                "❌ Владельца удалить нельзя.",
                show_alert=True,
            )

            return

        await conn.execute(
            """
            DELETE FROM store_users

            WHERE store_id = $1
              AND telegram_id = $2
              AND member_role = 'manager'
            """,
            membership["store_id"],
            manager_telegram_id,
        )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        "✅ Менеджер "
        f"{manager['full_name']} удалён."
    )

    try:

        role, _ = await get_user_role(
            manager_telegram_id
        )

        await bot.send_message(
            manager_telegram_id,

            "ℹ️ Вы больше не являетесь "
            "менеджером магазина "
            f"{membership['store_name']}.",

            reply_markup=main_keyboard(
                role,
                manager_telegram_id,
            ),
        )

    except Exception:
        pass

    await callback.answer(
        "Менеджер удалён."
    )


@dp.callback_query(
    F.data == "cancel_remove_manager"
)
async def cancel_remove_manager(
    callback: CallbackQuery
):

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        "Удаление менеджера отменено."
    )

    await callback.answer()


@dp.callback_query(
    F.data == "create_manager_invite"
)
async def create_manager_invite(
    callback: CallbackQuery
):

    membership = await get_store_membership(
        callback.from_user.id
    )

    if (
        not membership
        or membership["status"]
            != "approved"
        or membership["member_role"]
            != "owner"
    ):

        await callback.answer(
            "Только владелец магазина "
            "может приглашать менеджеров.",
            show_alert=True,
        )

        return

    alphabet = (
        string.ascii_uppercase
        + string.digits
    )

    async with db_pool.acquire() as conn:

        code = None

        for _ in range(30):

            candidate = "".join(
                secrets.choice(alphabet)
                for _ in range(6)
            )

            exists = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM store_invites
                    WHERE code = $1
                )
                """,
                candidate,
            )

            if not exists:
                code = candidate
                break

        if not code:

            await callback.answer(
                "Не удалось создать код.",
                show_alert=True,
            )

            return

        await conn.execute(
            """
            INSERT INTO store_invites (
                store_id,
                code,
                created_by
            )

            VALUES ($1,$2,$3)
            """,
            membership["store_id"],
            code,
            callback.from_user.id,
        )

    await callback.answer()

    await callback.message.answer(
        "🔑 КОД ПРИГЛАШЕНИЯ\n\n"

        f"🏪 {membership['store_name']}\n\n"

        f"Код: {code}\n\n"

        "Передайте этот код менеджеру.\n\n"

        "Менеджер должен открыть:\n"
        "🏪 Магазин → "
        "🔑 Присоединиться к магазину\n\n"

        "⚠️ Код одноразовый."
    )


# =========================================================
# ПРОФИЛЬ / СТАТИСТИКА МАГАЗИНА
# =========================================================

@dp.message(
    F.text == "🏪 Профиль магазина"
)
async def store_profile(
    message: Message
):

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await message.answer(
            "❌ Магазин не найден."
        )

        return

    role_name = (
        "👑 Владелец"
        if membership["member_role"]
            == "owner"
        else "👤 Менеджер"
    )

    await message.answer(
        "🏪 ПРОФИЛЬ МАГАЗИНА\n\n"

        f"Название: "
        f"{membership['store_name']}\n"

        f"📍 Адрес: "
        f"{membership['address']}\n"

        f"📞 Телефон: "
        f"{membership['phone']}\n\n"

        f"👤 Пользователь: "
        f"{membership['full_name']}\n"

        f"🔐 Роль: {role_name}\n"

        f"Статус: "
        f"{membership['status']}"
    )


@dp.message(
    F.text == "📊 Статистика магазина"
)
async def store_statistics(
    message: Message
):

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await message.answer(
            "❌ Магазин не найден."
        )

        return

    async with db_pool.acquire() as conn:

        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,

                COUNT(*) FILTER (
                    WHERE status = 'new'
                ) AS new_count,

                COUNT(*) FILTER (
                    WHERE status NOT IN (
                        'new',
                        'delivered',
                        'cancelled'
                    )
                ) AS active_count,

                COUNT(*) FILTER (
                    WHERE status = 'delivered'
                ) AS delivered_count,

                COUNT(*) FILTER (
                    WHERE status = 'cancelled'
                ) AS cancelled_count,

                COALESCE(
                    SUM(delivery_price)
                    FILTER (
                        WHERE status =
                            'delivered'
                    ),
                    0
                ) AS total_price

            FROM orders

            WHERE store_id = $1
            """,
            membership["store_id"],
        )

    await message.answer(
        "📊 СТАТИСТИКА МАГАЗИНА\n\n"

        f"🏪 {membership['store_name']}\n\n"

        f"📦 Всего: {stats['total']}\n"
        f"🆕 Новых: "
        f"{stats['new_count']}\n"
        f"🚚 Активных: "
        f"{stats['active_count']}\n"
        f"✅ Доставленных: "
        f"{stats['delivered_count']}\n"
        f"❌ Отменённых: "
        f"{stats['cancelled_count']}\n\n"

        f"💰 Сумма доставленных: "
        f"{price_text(stats['total_price'])}"
    )


# =========================================================
# СОЗДАНИЕ ЗАКАЗА
# =========================================================

@dp.message(
    F.text == "➕ Создать заказ"
)
async def order_start(
    message: Message,
    state: FSMContext,
):

    membership = await get_store_membership(
        message.from_user.id
    )

    if (
        not membership
        or membership["status"]
            != "approved"
    ):

        await message.answer(
            "❌ Создавать заказы может "
            "только пользователь "
            "одобренного магазина."
        )

        return

    await state.clear()

    await state.set_state(
        OrderCreation.client_name
    )

    await message.answer(
        "📦 СОЗДАНИЕ ЗАКАЗА\n\n"
        "👤 Введите имя клиента:"
    )


@dp.message(
    OrderCreation.client_name
)
async def order_client_name(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        client_name=message.text
    )

    await state.set_state(
        OrderCreation.client_phone
    )

    await message.answer(
        "📞 Введите номер телефона клиента:"
    )


@dp.message(
    OrderCreation.client_phone
)
async def order_client_phone(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        client_phone=message.text
    )

    await state.set_state(
        OrderCreation.delivery_address
    )

    await message.answer(
        "📍 Введите адрес доставки:"
    )


@dp.message(
    OrderCreation.delivery_address
)
async def order_delivery_address(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        delivery_address=message.text
    )

    await state.set_state(
        OrderCreation.item
    )

    await message.answer(
        "📦 Что нужно доставить?\n\n"
        "Например: Холодильник — 1 шт."
    )


@dp.message(OrderCreation.item)
async def order_item(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        item=message.text
    )

    await state.set_state(
        OrderCreation.kittek_order_number
    )

    await message.answer(
        "🔢 Введите номер заказа по Kittek.\n\n"
        "Если номера нет — нажмите «⏭ Пропустить».",
        reply_markup=skip_keyboard,
    )

@dp.message(OrderCreation.kittek_order_number)
async def order_kittek_number(
    message: Message,
    state: FSMContext,
):

    value = None

    if message.text != "⏭ Пропустить":
        value = message.text

    await state.update_data(
        kittek_order_number=value
    )

    await state.set_state(
        OrderCreation.kaspi_order_number
    )

    await message.answer(
        "🛒 Введите номер заказа по Kaspi.\n\n"
        "Если номера нет — нажмите «⏭ Пропустить».",
        reply_markup=skip_keyboard,
    )


@dp.message(OrderCreation.kaspi_order_number)
async def order_kaspi_number(
    message: Message,
    state: FSMContext,
):

    value = None

    if message.text != "⏭ Пропустить":
        value = message.text

    await state.update_data(
        kaspi_order_number=value
    )

    await state.set_state(
        OrderCreation.delivery_time
    )

    await message.answer(
        "🕐 Укажите желаемое время доставки:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[],
            resize_keyboard=True,
        ),
    )


@dp.message(
    OrderCreation.delivery_time
)
async def order_delivery_time(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        delivery_time=message.text
    )

    await state.set_state(
        OrderCreation.comment
    )

    await message.answer(
        "📝 Добавьте комментарий.\n\n"
        "Если комментария нет — "
        "напишите: Нет"
    )


@dp.message(
    OrderCreation.comment
)
async def order_comment(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        comment=message.text
    )

    data = await state.get_data()

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await state.clear()

        await message.answer(
            "❌ Магазин не найден."
        )

        return

    await state.set_state(
        OrderCreation.confirm
    )

    await message.answer(
        "📦 ПРОВЕРЬТЕ ЗАКАЗ\n\n"

        f"🏪 Магазин: "
        f"{membership['store_name']}\n"

        f"👤 Создал: "
        f"{membership['full_name']}\n"

        f"📍 Забрать: "
        f"{membership['address']}\n\n"

        f"👤 Клиент: "
        f"{data['client_name']}\n"

        f"📞 Телефон: "
        f"{data['client_phone']}\n"

        f"📍 Доставить: "
        f"{data['delivery_address']}\n\n"

        f"📦 Товар: "
        f"{data['item']}\n"

        f"🕐 Время: "
        f"{data['delivery_time']}\n"

        f"📝 Комментарий: "
        f"{data['comment']}\n\n"

        "Создать заказ?",

        reply_markup=order_confirm_keyboard,
    )


@dp.message(
    OrderCreation.confirm,
    F.text == "✅ Создать заказ",
)
async def order_confirm(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    membership = await get_store_membership(
        message.from_user.id
    )

    if (
        not membership
        or membership["status"]
            != "approved"
    ):

        await state.clear()

        await message.answer(
            "❌ Магазин недоступен."
        )

        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

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
                    comment,
                    created_by_telegram_id
                )

                VALUES (
                    $1,$2,$3,$4,$5,
                    $6,$7,$8,$9
                )

                RETURNING id
                """,
                membership["store_id"],
                data["client_name"],
                data["client_phone"],
                membership["address"],
                data["delivery_address"],
                data["item"],
                data["delivery_time"],
                data["comment"],
                message.from_user.id,
            )

            await add_history(
                conn,
                order_id,
                "new",
                "store",
                message.from_user.id,
                "Заказ создан",
            )

    await state.clear()

    await message.answer(
        f"✅ Заказ №{order_id} создан!\n\n"
        "Статус: 🆕 Новый",
        reply_markup=store_keyboard,
    )


@dp.message(
    OrderCreation.confirm,
    F.text == "❌ Отменить заказ",
)
async def order_cancel(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        "❌ Создание заказа отменено.",
        reply_markup=store_keyboard,
    )


# =========================================================
# ЗАКАЗЫ МАГАЗИНА
# =========================================================

@dp.message(
    F.text == "📦 Мои заказы"
)
async def store_orders(
    message: Message
):

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await message.answer(
            "❌ Магазин не найден."
        )

        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(
            """
            SELECT
                o.id,
                o.client_name,
                o.client_phone,
                o.delivery_address,
                o.item,
                o.delivery_time,
                o.comment,
                o.status,
                o.delivery_price,

                su.full_name AS created_by

            FROM orders o

            LEFT JOIN store_users su
                ON su.telegram_id =
                   o.created_by_telegram_id

            WHERE o.store_id = $1

            ORDER BY o.id DESC

            LIMIT 20
            """,
            membership["store_id"],
        )

    if not orders:

        await message.answer(
            "📦 У магазина пока нет заказов."
        )

        return

    await message.answer(
        f"📦 ЗАКАЗЫ МАГАЗИНА: "
        f"{len(orders)}"
    )

    for order in orders:

        author = (
            order["created_by"]
            or "Старый заказ"
        )

        buttons = []

        if order["status"] == "new":

            buttons.append([
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=(
                        f"edit_order:{order['id']}"
                    ),
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                text="🕐 История",
                callback_data=(
                    f"store_history:"
                    f"{order['id']}"
                ),
            )
        ])

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=buttons
        )

        await message.answer(
            f"📦 ЗАКАЗ №{order['id']}\n\n"

            f"Статус: "
            f"{STATUS_NAMES.get(order['status'], order['status'])}\n"

            f"💰 Стоимость: "
            f"{price_text(order['delivery_price'])}\n"

            f"👤 Создал: {author}\n\n"

            f"👤 Клиент: "
            f"{order['client_name']}\n"

            f"📞 {order['client_phone']}\n"

            f"📍 {order['delivery_address']}\n"

            f"📦 {order['item']}\n"

            f"🕐 {order['delivery_time']}\n"

            f"📝 {order['comment']}",

            reply_markup=keyboard,
        )


# =========================================================
# ИСТОРИЯ ДЛЯ МАГАЗИНА
# =========================================================

@dp.callback_query(
    F.data.startswith("store_history:")
)
async def store_history(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    membership = await get_store_membership(
        callback.from_user.id
    )

    if not membership:

        await callback.answer(
            "Магазин не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        valid = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM orders

                WHERE id = $1
                  AND store_id = $2
            )
            """,
            order_id,
            membership["store_id"],
        )

        if not valid:

            await callback.answer(
                "Заказ недоступен.",
                show_alert=True,
            )

            return

        history = await conn.fetch(
            """
            SELECT
                status,
                note,
                created_at

            FROM order_status_history

            WHERE order_id = $1

            ORDER BY created_at ASC,
                     id ASC
            """,
            order_id,
        )

    text = (
        f"🕐 ИСТОРИЯ ЗАКАЗА №{order_id}\n\n"
    )

    for row in history:

        local_time = row[
            "created_at"
        ].strftime("%d.%m.%Y %H:%M")

        text += (
            f"{STATUS_NAMES.get(row['status'], row['status'])}\n"
            f"🕐 {local_time}"
        )

        if row["note"]:
            text += f"\n📝 {row['note']}"

        text += "\n\n"

    await callback.message.answer(text)

    await callback.answer()


# =========================================================
# РЕДАКТИРОВАНИЕ ЗАКАЗА
# =========================================================

@dp.callback_query(
    F.data.startswith("edit_order:")
)
async def edit_order_menu(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    membership = await get_store_membership(
        callback.from_user.id
    )

    if not membership:

        await callback.answer(
            "Магазин не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            SELECT *

            FROM orders

            WHERE id = $1
              AND store_id = $2
            """,
            order_id,
            membership["store_id"],
        )

    if not order:

        await callback.answer(
            "Заказ не найден.",
            show_alert=True,
        )

        return

    if order["status"] != "new":

        await callback.answer(
            "❌ Заказ уже назначен курьеру. "
            "Редактирование заблокировано.",
            show_alert=True,
        )

        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Имя клиента",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:"
                        "client_name"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📞 Телефон",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:"
                        "client_phone"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📍 Адрес доставки",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:"
                        "delivery_address"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Товар",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:"
                        "item"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🕐 Время",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:"
                        "delivery_time"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📝 Комментарий",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:"
                        "comment"
                    ),
                )
            ],
        ]
    )

    await callback.message.answer(
        f"✏️ РЕДАКТИРОВАНИЕ "
        f"ЗАКАЗА №{order_id}\n\n"

        f"👤 {order['client_name']}\n"
        f"📞 {order['client_phone']}\n"
        f"📍 {order['delivery_address']}\n"
        f"📦 {order['item']}\n"
        f"🕐 {order['delivery_time']}\n"
        f"📝 {order['comment']}\n\n"

        "Что хотите изменить?",

        reply_markup=keyboard,
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("edit_field:")
)
async def edit_order_field(
    callback: CallbackQuery,
    state: FSMContext,
):

    parts = callback.data.split(":")

    order_id = int(parts[1])
    field = parts[2]

    field_names = {
        "client_name":
            "имя клиента",

        "client_phone":
            "номер телефона",

        "delivery_address":
            "адрес доставки",

        "item":
            "товар",

        "delivery_time":
            "время доставки",

        "comment":
            "комментарий",
    }

    if field not in field_names:

        await callback.answer(
            "Неизвестное поле.",
            show_alert=True,
        )

        return

    membership = await get_store_membership(
        callback.from_user.id
    )

    if not membership:

        await callback.answer(
            "Магазин не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        valid = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1

                FROM orders

                WHERE id = $1
                  AND store_id = $2
                  AND status = 'new'
            )
            """,
            order_id,
            membership["store_id"],
        )

    if not valid:

        await callback.answer(
            "❌ Заказ уже нельзя редактировать.",
            show_alert=True,
        )

        return

    await state.update_data(
        edit_order_id=order_id,
        edit_field=field,
    )

    await state.set_state(
        OrderEdit.value
    )

    await callback.message.answer(
        f"✏️ Заказ №{order_id}\n\n"
        f"Введите новое "
        f"{field_names[field]}:"
    )

    await callback.answer()


@dp.message(OrderEdit.value)
async def save_order_edit(
    message: Message,
    state: FSMContext,
):

    value = (
        message.text or ""
    ).strip()

    if not value:

        await message.answer(
            "❌ Значение не может "
            "быть пустым."
        )

        return

    data = await state.get_data()

    order_id = data.get(
        "edit_order_id"
    )

    field = data.get(
        "edit_field"
    )

    allowed_fields = {
        "client_name",
        "client_phone",
        "delivery_address",
        "item",
        "delivery_time",
        "comment",
    }

    if (
        not order_id
        or field not in allowed_fields
    ):

        await state.clear()

        await message.answer(
            "❌ Ошибка редактирования."
        )

        return

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await state.clear()

        await message.answer(
            "❌ Магазин не найден."
        )

        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                f"""
                UPDATE orders

                SET
                    {field} = $1,
                    updated_at = NOW()

                WHERE id = $2
                  AND store_id = $3
                  AND status = 'new'

                RETURNING id
                """,
                value,
                order_id,
                membership["store_id"],
            )

            if order:

                await add_history(
                    conn,
                    order_id,
                    "new",
                    "store",
                    message.from_user.id,
                    f"Изменено поле: {field}",
                )

    await state.clear()

    if not order:

        await message.answer(
            "❌ Заказ уже был назначен "
            "курьеру.\n"
            "Редактирование заблокировано."
        )

        return

    await message.answer(
        f"✅ Заказ №{order_id} обновлён.",
        reply_markup=store_keyboard,
    )


# =========================================================
# КУРЬЕР — РЕГИСТРАЦИЯ
# =========================================================

@dp.message(F.text == "🚚 Курьер")
async def courier_section(
    message: Message,
    state: FSMContext,
):

    role, info = await get_user_role(
        message.from_user.id
    )

    if role == "store":

        await message.answer(
            "❌ Вы зарегистрированы "
            "как пользователь магазина.\n\n"
            "Один аккаунт может иметь "
            "только одну рабочую роль."
        )

        return

    if role == "courier":

        if info["status"] == "approved":

            await message.answer(
                f"🚚 Курьер: "
                f"{info['full_name']}\n\n"

                "Выберите действие:",

                reply_markup=courier_keyboard,
            )

            return

        await message.answer(
            "⏳ Ваша заявка курьера "
            "ожидает подтверждения."
        )

        return

    await state.set_state(
        CourierRegistration.full_name
    )

    await message.answer(
        "🚚 РЕГИСТРАЦИЯ КУРЬЕРА\n\n"
        "👤 Введите ваше имя:"
    )


@dp.message(
    CourierRegistration.full_name
)
async def courier_name(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        full_name=message.text
    )

    await state.set_state(
        CourierRegistration.phone
    )

    await message.answer(
        "📞 Введите номер телефона:"
    )


@dp.message(
    CourierRegistration.phone
)
async def courier_phone(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        phone=message.text
    )

    await state.set_state(
        CourierRegistration.vehicle
    )

    await message.answer(
        "🚗 Укажите транспорт:"
    )


@dp.message(
    CourierRegistration.vehicle
)
async def courier_vehicle(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        vehicle=message.text
    )

    data = await state.get_data()

    await state.set_state(
        CourierRegistration.confirm
    )

    await message.answer(
        "Проверьте данные:\n\n"

        f"👤 Имя: "
        f"{data['full_name']}\n"

        f"📞 Телефон: "
        f"{data['phone']}\n"

        f"🚗 Транспорт: "
        f"{data['vehicle']}\n\n"

        "Отправить заявку?",

        reply_markup=
            registration_confirm_keyboard,
    )


@dp.message(
    CourierRegistration.confirm,
    F.text == "✅ Отправить заявку",
)
async def courier_confirm(
    message: Message,
    state: FSMContext,
):

    role, _ = await get_user_role(
        message.from_user.id
    )

    if role:

        await state.clear()

        await message.answer(
            "❌ У вас уже есть рабочая роль."
        )

        return

    data = await state.get_data()

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            INSERT INTO couriers (
                telegram_id,
                full_name,
                phone,
                vehicle,
                status
            )

            VALUES (
                $1,$2,$3,$4,'pending'
            )

            ON CONFLICT (telegram_id)

            DO UPDATE SET
                full_name =
                    EXCLUDED.full_name,
                phone =
                    EXCLUDED.phone,
                vehicle =
                    EXCLUDED.vehicle,
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
        "⏳ Ожидайте подтверждения "
        "администратора."
    )

    await send_main_menu(message)


# =========================================================
# ПРОФИЛЬ / СТАТИСТИКА КУРЬЕРА
# =========================================================

@dp.message(
    F.text == "🚚 Профиль курьера"
)
async def courier_profile(
    message: Message
):

    courier = await get_courier(
        message.from_user.id
    )

    if not courier:

        await message.answer(
            "❌ Курьер не найден."
        )

        return

    await message.answer(
        "🚚 ПРОФИЛЬ КУРЬЕРА\n\n"

        f"👤 {courier['full_name']}\n"
        f"📞 {courier['phone']}\n"
        f"🚗 {courier['vehicle']}\n"
        f"Статус: {courier['status']}"
    )


@dp.message(
    F.text == "📊 Моя статистика"
)
async def courier_statistics(
    message: Message
):

    courier_id = (
        await get_approved_courier_id(
            message.from_user.id
        )
    )

    if not courier_id:

        await message.answer(
            "❌ Курьер не найден."
        )

        return

    async with db_pool.acquire() as conn:

        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,

                COUNT(*) FILTER (
                    WHERE status NOT IN (
                        'delivered',
                        'cancelled'
                    )
                ) AS active_count,

                COUNT(*) FILTER (
                    WHERE status = 'delivered'
                ) AS delivered_count,

                COUNT(*) FILTER (
                    WHERE status = 'cancelled'
                ) AS cancelled_count,

                COALESCE(
                    SUM(delivery_price)
                    FILTER (
                        WHERE status =
                            'delivered'
                    ),
                    0
                ) AS delivered_sum

            FROM orders

            WHERE courier_id = $1
            """,
            courier_id,
        )

    await message.answer(
        "📊 СТАТИСТИКА КУРЬЕРА\n\n"

        f"📦 Всего назначено: "
        f"{stats['total']}\n"

        f"🚚 Активных: "
        f"{stats['active_count']}\n"

        f"✅ Доставлено: "
        f"{stats['delivered_count']}\n"

        f"❌ Отменено: "
        f"{stats['cancelled_count']}\n\n"

        f"💰 Стоимость доставленных: "
        f"{price_text(stats['delivered_sum'])}"
    )


# =========================================================
# ДОСТАВКИ КУРЬЕРА
# =========================================================

@dp.message(
    F.text == "📦 Мои доставки"
)
async def courier_orders(
    message: Message
):

    courier_id = (
        await get_approved_courier_id(
            message.from_user.id
        )
    )

    if not courier_id:

        await message.answer(
            "❌ Вы не зарегистрированы "
            "как одобренный курьер."
        )

        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(
            """
            SELECT
                o.*,

                s.store_name,

                su.full_name AS created_by

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

            LEFT JOIN store_users su
                ON su.telegram_id =
                   o.created_by_telegram_id

            WHERE o.courier_id = $1

              AND o.status NOT IN (
                  'delivered',
                  'cancelled'
              )

            ORDER BY o.id DESC
            """,
            courier_id,
        )

    if not orders:

        await message.answer(
            "📦 У вас пока нет "
            "активных доставок."
        )

        return

    for order in orders:

        buttons = []

        if order["status"] == "assigned":

            buttons = [[
                InlineKeyboardButton(
                    text="✅ Принять заказ",
                    callback_data=(
                        f"accept_order:"
                        f"{order['id']}"
                    ),
                )
            ]]

        elif order["status"] == "accepted":

            buttons = [[
                InlineKeyboardButton(
                    text=(
                        "📸 Фото товара "
                        "при получении"
                    ),
                    callback_data=(
                        f"pickup_photo:"
                        f"{order['id']}"
                    ),
                )
            ]]

        elif order["status"] == "pickup_photo":

            buttons = [[
                InlineKeyboardButton(
                    text="📦 Товар забран",
                    callback_data=(
                        f"picked_up:"
                        f"{order['id']}"
                    ),
                )
            ]]

        elif order["status"] == "picked_up":

            buttons = [[
                InlineKeyboardButton(
                    text="🚗 Выехал к клиенту",
                    callback_data=(
                        f"on_way:"
                        f"{order['id']}"
                    ),
                )
            ]]

        elif order["status"] == "on_the_way":

            buttons = [[
                InlineKeyboardButton(
                    text="📍 Я приехал",
                    callback_data=(
                        f"arrived:"
                        f"{order['id']}"
                    ),
                )
            ]]

        elif order["status"] == "arrived":

            buttons = [[
                InlineKeyboardButton(
                    text="📸 Фото доставки",
                    callback_data=(
                        f"delivery_photo:"
                        f"{order['id']}"
                    ),
                )
            ]]

        elif (
            order["status"]
            == "delivery_photo"
        ):

            buttons = [[
                InlineKeyboardButton(
                    text="✅ Завершить доставку",
                    callback_data=(
                        f"delivered:"
                        f"{order['id']}"
                    ),
                )
            ]]

        keyboard = None

        if buttons:

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=buttons
            )

        author = (
            order["created_by"]
            or "Не указан"
        )

        await message.answer(
            f"🚚 ЗАКАЗ №{order['id']}\n\n"

            f"🏪 Магазин: "
            f"{order['store_name']}\n"

            f"👤 Создал: {author}\n"

            f"📍 Забрать: "
            f"{order['pickup_address']}\n\n"

            f"👤 Клиент: "
            f"{order['client_name']}\n"

            f"📞 {order['client_phone']}\n"

            f"📍 Доставить: "
            f"{order['delivery_address']}\n\n"

            f"📦 {order['item']}\n"

            f"🕐 {order['delivery_time']}\n"

            f"📝 {order['comment']}\n\n"

            f"💰 Стоимость: "
            f"{price_text(order['delivery_price'])}\n"

            f"Статус: "
            f"{STATUS_NAMES.get(order['status'], order['status'])}",

            reply_markup=keyboard,
        )


# =========================================================
# ПРИНЯТИЕ ЗАКАЗА
# =========================================================

@dp.callback_query(
    F.data.startswith("accept_order:")
)
async def accept_order(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = (
        await get_approved_courier_id(
            callback.from_user.id
        )
    )

    if not courier_id:

        await callback.answer(
            "Курьер не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    status = 'accepted',
                    updated_at = NOW()

                WHERE id = $1
                  AND courier_id = $2
                  AND status = 'assigned'

                RETURNING
                    id,
                    store_id
                """,
                order_id,
                courier_id,
            )

            if order:

                await add_history(
                    conn,
                    order_id,
                    "accepted",
                    "courier",
                    callback.from_user.id,
                    "Курьер принял заказ",
                )

    if not order:

        await callback.answer(
            "Заказ недоступен.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Заказ №{order_id} принят."
    )

    await notify_store_users(
        order["store_id"],
        f"✅ Заказ №{order_id}\n"
        "Курьер принял заказ."
    )

    await callback.answer()


# =========================================================
# ФОТО ПОЛУЧЕНИЯ
# =========================================================

@dp.callback_query(
    F.data.startswith("pickup_photo:")
)
async def pickup_photo_request(
    callback: CallbackQuery,
    state: FSMContext,
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = (
        await get_approved_courier_id(
            callback.from_user.id
        )
    )

    if not courier_id:

        await callback.answer(
            "Курьер не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        valid = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1

                FROM orders

                WHERE id = $1
                  AND courier_id = $2
                  AND status = 'accepted'
            )
            """,
            order_id,
            courier_id,
        )

    if not valid:

        await callback.answer(
            "Заказ недоступен.",
            show_alert=True,
        )

        return

    await state.update_data(
        order_id=order_id
    )

    await state.set_state(
        CourierPhoto.pickup_photo
    )

    await callback.message.answer(
        f"📸 Отправьте фотографию "
        f"товара для заказа №{order_id}."
    )

    await callback.answer()


@dp.message(
    CourierPhoto.pickup_photo,
    F.photo,
)
async def pickup_photo_received(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    order_id = data["order_id"]

    courier_id = (
        await get_approved_courier_id(
            message.from_user.id
        )
    )

    if not courier_id:

        await state.clear()
        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                SELECT
                    id,
                    store_id

                FROM orders

                WHERE id = $1
                  AND courier_id = $2
                  AND status = 'accepted'
                """,
                order_id,
                courier_id,
            )

            if not order:

                await state.clear()

                await message.answer(
                    "❌ Заказ недоступен."
                )

                return

            file_id = (
                message.photo[-1].file_id
            )

            await conn.execute(
                """
                INSERT INTO order_photos (
                    order_id,
                    courier_id,
                    photo_type,
                    file_id
                )

                VALUES (
                    $1,$2,'pickup',$3
                )
                """,
                order_id,
                courier_id,
                file_id,
            )

            await conn.execute(
                """
                UPDATE orders

                SET
                    status =
                        'pickup_photo',
                    updated_at =
                        NOW()

                WHERE id = $1
                """,
                order_id,
            )

            await add_history(
                conn,
                order_id,
                "pickup_photo",
                "courier",
                message.from_user.id,
                "Фото товара при получении",
            )

    await state.clear()

    await message.answer(
        f"✅ Фото заказа №{order_id} "
        "сохранено."
    )

    await notify_store_users(
        order["store_id"],
        f"📸 Заказ №{order_id}\n"
        "Курьер отправил фото товара "
        "при получении."
    )


@dp.message(
    CourierPhoto.pickup_photo
)
async def pickup_photo_wrong(
    message: Message
):

    await message.answer(
        "📸 Отправьте именно фотографию."
    )


# =========================================================
# ТОВАР ЗАБРАН
# =========================================================

@dp.callback_query(
    F.data.startswith("picked_up:")
)
async def picked_up(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = (
        await get_approved_courier_id(
            callback.from_user.id
        )
    )

    if not courier_id:
        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    status = 'picked_up',
                    updated_at = NOW()

                WHERE id = $1
                  AND courier_id = $2
                  AND status =
                      'pickup_photo'

                RETURNING
                    id,
                    store_id
                """,
                order_id,
                courier_id,
            )

            if order:

                await add_history(
                    conn,
                    order_id,
                    "picked_up",
                    "courier",
                    callback.from_user.id,
                    "Товар забран",
                )

    if not order:

        await callback.answer(
            "Статус недоступен.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"📦 Заказ №{order_id}: "
        "товар забран."
    )

    await notify_store_users(
        order["store_id"],
        f"📦 Заказ №{order_id}\n"
        "Товар забран курьером."
    )

    await callback.answer()


# =========================================================
# В ПУТИ
# =========================================================

@dp.callback_query(
    F.data.startswith("on_way:")
)
async def on_way(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = (
        await get_approved_courier_id(
            callback.from_user.id
        )
    )

    if not courier_id:
        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    status =
                        'on_the_way',
                    updated_at =
                        NOW()

                WHERE id = $1
                  AND courier_id = $2
                  AND status =
                      'picked_up'

                RETURNING
                    id,
                    store_id
                """,
                order_id,
                courier_id,
            )

            if order:

                await add_history(
                    conn,
                    order_id,
                    "on_the_way",
                    "courier",
                    callback.from_user.id,
                    "Курьер выехал к клиенту",
                )

    if not order:

        await callback.answer(
            "Статус недоступен.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"🚗 Заказ №{order_id}: "
        "вы выехали."
    )

    await notify_store_users(
        order["store_id"],
        f"🚗 Заказ №{order_id}\n"
        "Курьер выехал к клиенту."
    )

    await callback.answer()


# =========================================================
# КУРЬЕР ПРИЕХАЛ
# =========================================================

@dp.callback_query(
    F.data.startswith("arrived:")
)
async def arrived(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = (
        await get_approved_courier_id(
            callback.from_user.id
        )
    )

    if not courier_id:
        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    status = 'arrived',
                    updated_at = NOW()

                WHERE id = $1
                  AND courier_id = $2
                  AND status =
                      'on_the_way'

                RETURNING
                    id,
                    store_id
                """,
                order_id,
                courier_id,
            )

            if order:

                await add_history(
                    conn,
                    order_id,
                    "arrived",
                    "courier",
                    callback.from_user.id,
                    "Курьер прибыл к клиенту",
                )

    if not order:

        await callback.answer(
            "Статус недоступен.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"📍 Заказ №{order_id}: "
        "вы прибыли к клиенту."
    )

    await notify_store_users(
        order["store_id"],
        f"📍 Заказ №{order_id}\n"
        "Курьер прибыл к клиенту."
    )

    await callback.answer()


# =========================================================
# ФОТО ДОСТАВКИ
# =========================================================

@dp.callback_query(
    F.data.startswith("delivery_photo:")
)
async def delivery_photo_request(
    callback: CallbackQuery,
    state: FSMContext,
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = (
        await get_approved_courier_id(
            callback.from_user.id
        )
    )

    if not courier_id:

        await callback.answer(
            "Курьер не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        valid = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1

                FROM orders

                WHERE id = $1
                  AND courier_id = $2
                  AND status = 'arrived'
            )
            """,
            order_id,
            courier_id,
        )

    if not valid:

        await callback.answer(
            "Заказ недоступен.",
            show_alert=True,
        )

        return

    await state.update_data(
        order_id=order_id
    )

    await state.set_state(
        CourierPhoto.delivery_photo
    )

    await callback.message.answer(
        f"📸 Отправьте фото доставки "
        f"заказа №{order_id}."
    )

    await callback.answer()


@dp.message(
    CourierPhoto.delivery_photo,
    F.photo,
)
async def delivery_photo_received(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    order_id = data["order_id"]

    courier_id = (
        await get_approved_courier_id(
            message.from_user.id
        )
    )

    if not courier_id:

        await state.clear()
        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                SELECT
                    id,
                    store_id

                FROM orders

                WHERE id = $1
                  AND courier_id = $2
                  AND status = 'arrived'
                """,
                order_id,
                courier_id,
            )

            if not order:

                await state.clear()

                await message.answer(
                    "❌ Заказ недоступен."
                )

                return

            file_id = (
                message.photo[-1].file_id
            )

            await conn.execute(
                """
                INSERT INTO order_photos (
                    order_id,
                    courier_id,
                    photo_type,
                    file_id
                )

                VALUES (
                    $1,$2,'delivery',$3
                )
                """,
                order_id,
                courier_id,
                file_id,
            )

            await conn.execute(
                """
                UPDATE orders

                SET
                    status =
                        'delivery_photo',
                    updated_at =
                        NOW()

                WHERE id = $1
                """,
                order_id,
            )

            await add_history(
                conn,
                order_id,
                "delivery_photo",
                "courier",
                message.from_user.id,
                "Фото подтверждения доставки",
            )

    await state.clear()

    await message.answer(
        f"✅ Фото доставки заказа "
        f"№{order_id} сохранено."
    )

    await notify_store_users(
        order["store_id"],
        f"📸 Заказ №{order_id}\n"
        "Получено фото подтверждения "
        "доставки."
    )


@dp.message(
    CourierPhoto.delivery_photo
)
async def delivery_photo_wrong(
    message: Message
):

    await message.answer(
        "📸 Отправьте именно фотографию."
    )


# =========================================================
# ДОСТАВЛЕНО
# =========================================================

@dp.callback_query(
    F.data.startswith("delivered:")
)
async def delivered(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = (
        await get_approved_courier_id(
            callback.from_user.id
        )
    )

    if not courier_id:
        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    status = 'delivered',
                    updated_at = NOW()

                WHERE id = $1
                  AND courier_id = $2
                  AND status =
                      'delivery_photo'

                RETURNING
                    id,
                    store_id,
                    delivery_price
                """,
                order_id,
                courier_id,
            )

            if order:

                await add_history(
                    conn,
                    order_id,
                    "delivered",
                    "courier",
                    callback.from_user.id,
                    "Доставка завершена",
                )

    if not order:

        await callback.answer(
            "Не удалось завершить доставку.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Заказ №{order_id} доставлен!"
    )

    await notify_store_users(
        order["store_id"],
        f"✅ Заказ №{order_id} "
        "успешно доставлен.\n\n"
        f"💰 Стоимость: "
        f"{price_text(order['delivery_price'])}"
    )

    await callback.answer(
        "Доставка завершена."
    )


# =========================================================
# АДМИН — ГЛАВНАЯ
# =========================================================

@dp.message(
    F.text == "👨‍💼 Администратор"
)
async def admin_home(
    message: Message
):

    if await deny_admin_message(message):
        return

    async with db_pool.acquire() as conn:

        stats = await conn.fetchrow(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE status = 'new'
                ) AS new_count,

                COUNT(*) FILTER (
                    WHERE status NOT IN (
                        'new',
                        'delivered',
                        'cancelled'
                    )
                ) AS active_count,

                COUNT(*) FILTER (
                    WHERE status =
                        'delivered'
                ) AS delivered_count,

                COUNT(*) FILTER (
                    WHERE status =
                        'cancelled'
                ) AS cancelled_count

            FROM orders
            """
        )

        pending_stores = await conn.fetchval(
            """
            SELECT COUNT(*)

            FROM stores

            WHERE status = 'pending'
            """
        )

        pending_couriers = await conn.fetchval(
            """
            SELECT COUNT(*)

            FROM couriers

            WHERE status = 'pending'
            """
        )

    await message.answer(
        "👨‍💼 АДМИН-ПАНЕЛЬ\n\n"

        f"📦 Новых заказов: "
        f"{stats['new_count']}\n"

        f"🚚 Активных: "
        f"{stats['active_count']}\n"

        f"✅ Доставленных: "
        f"{stats['delivered_count']}\n"

        f"❌ Отменённых: "
        f"{stats['cancelled_count']}\n\n"

        f"🏪 Заявок магазинов: "
        f"{pending_stores}\n"

        f"🚚 Заявок курьеров: "
        f"{pending_couriers}",

        reply_markup=admin_keyboard,
    )


# =========================================================
# АДМИН — СТАТИСТИКА
# =========================================================

@dp.message(F.text == "📊 Статистика")
async def admin_statistics(
    message: Message
):

    if await deny_admin_message(message):
        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,

                COUNT(*) FILTER (
                    WHERE status = 'new'
                ) AS new_count,

                COUNT(*) FILTER (
                    WHERE status NOT IN (
                        'new',
                        'delivered',
                        'cancelled'
                    )
                ) AS active_count,

                COUNT(*) FILTER (
                    WHERE status =
                        'delivered'
                ) AS delivered_count,

                COUNT(*) FILTER (
                    WHERE status =
                        'cancelled'
                ) AS cancelled_count,

                COALESCE(
                    SUM(delivery_price)
                    FILTER (
                        WHERE status =
                            'delivered'
                    ),
                    0
                ) AS delivered_sum

            FROM orders
            """
        )

        stores = await conn.fetchval(
            """
            SELECT COUNT(*)

            FROM stores

            WHERE status = 'approved'
            """
        )

        couriers = await conn.fetchval(
            """
            SELECT COUNT(*)

            FROM couriers

            WHERE status = 'approved'
            """
        )

        managers = await conn.fetchval(
            """
            SELECT COUNT(*)

            FROM store_users

            WHERE member_role = 'manager'
            """
        )

    await message.answer(
        "📊 ОБЩАЯ СТАТИСТИКА\n\n"

        f"📦 Всего заказов: "
        f"{orders['total']}\n"

        f"🆕 Новых: "
        f"{orders['new_count']}\n"

        f"🚚 Активных: "
        f"{orders['active_count']}\n"

        f"✅ Доставленных: "
        f"{orders['delivered_count']}\n"

        f"❌ Отменённых: "
        f"{orders['cancelled_count']}\n\n"

        f"💰 Сумма доставленных: "
        f"{price_text(orders['delivered_sum'])}\n\n"

        f"🏪 Активных магазинов: "
        f"{stores}\n"

        f"👥 Менеджеров: "
        f"{managers}\n"

        f"🚚 Активных курьеров: "
        f"{couriers}"
    )


# =========================================================
# АДМИН — НОВЫЕ ЗАКАЗЫ
# =========================================================

@dp.message(
    F.text == "📦 Новые заказы"
)
async def admin_new_orders(
    message: Message
):

    if await deny_admin_message(message):
        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(
            """
            SELECT
                o.*,

                s.store_name,

                su.full_name AS created_by

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

            LEFT JOIN store_users su
                ON su.telegram_id =
                   o.created_by_telegram_id

            WHERE o.status = 'new'

            ORDER BY o.created_at ASC
            """
        )

        couriers = await conn.fetch(
            """
            SELECT
                id,
                full_name,
                vehicle

            FROM couriers

            WHERE status = 'approved'

            ORDER BY full_name
            """
        )

    if not orders:

        await message.answer(
            "📦 Новых заказов нет.",
            reply_markup=admin_keyboard,
        )

        return

    for order in orders:

        buttons = []

        for courier in couriers:

            buttons.append([
                InlineKeyboardButton(
                    text=(
                        "🚚 "
                        f"{courier['full_name']} "
                        f"({courier['vehicle']})"
                    ),
                    callback_data=(
                        f"assign:"
                        f"{order['id']}:"
                        f"{courier['id']}"
                    ),
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                text="💰 Установить стоимость",
                callback_data=(
                    f"set_price:{order['id']}"
                ),
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                text="🕐 История",
                callback_data=(
                    f"admin_history:"
                    f"{order['id']}"
                ),
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                text="❌ Отменить заказ",
                callback_data=(
                    "cancel_order_admin:"
                    f"{order['id']}"
                ),
            )
        ])

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=buttons
        )

        await message.answer(
            build_order_text(
                order,
                title=(
                    f"🆕 ЗАКАЗ №"
                    f"{order['id']}"
                ),
            ),
            reply_markup=keyboard,
        )


# =========================================================
# АДМИН — НАЗНАЧЕНИЕ
# =========================================================

@dp.callback_query(
    F.data.startswith("assign:")
)
async def assign_order(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    _, order_id_raw, courier_id_raw = (
        callback.data.split(":")
    )

    order_id = int(order_id_raw)
    courier_id = int(courier_id_raw)

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            courier = await conn.fetchrow(
                """
                SELECT *

                FROM couriers

                WHERE id = $1
                  AND status = 'approved'
                """,
                courier_id,
            )

            if not courier:

                await callback.answer(
                    "Курьер недоступен.",
                    show_alert=True,
                )

                return

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    courier_id = $1,
                    status = 'assigned',
                    updated_at = NOW()

                WHERE id = $2
                  AND status = 'new'

                RETURNING *
                """,
                courier_id,
                order_id,
            )

            if not order:

                await callback.answer(
                    "Заказ уже назначен.",
                    show_alert=True,
                )

                return

            store = await conn.fetchrow(
                """
                SELECT store_name

                FROM stores

                WHERE id = $1
                """,
                order["store_id"],
            )

            created_by = await conn.fetchval(
                """
                SELECT full_name

                FROM store_users

                WHERE telegram_id = $1
                """,
                order[
                    "created_by_telegram_id"
                ],
            )

            await add_history(
                conn,
                order_id,
                "assigned",
                "admin",
                callback.from_user.id,
                (
                    "Назначен курьер: "
                    f"{courier['full_name']}"
                ),
            )

    created_by = (
        created_by
        or "Не указан"
    )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Заказ №{order_id} назначен "
        f"курьеру {courier['full_name']}."
    )

    try:

        await bot.send_message(
            courier["telegram_id"],

            f"🚚 ВАМ НАЗНАЧЕН "
            f"ЗАКАЗ №{order_id}\n\n"

            f"🏪 Магазин: "
            f"{store['store_name']}\n"

            f"👤 Создал: "
            f"{created_by}\n"

            f"📍 Забрать: "
            f"{order['pickup_address']}\n\n"

            f"👤 Клиент: "
            f"{order['client_name']}\n"

            f"📞 {order['client_phone']}\n"

            f"📍 Доставить: "
            f"{order['delivery_address']}\n\n"

            f"📦 {order['item']}\n"

            f"🕐 {order['delivery_time']}\n"

            f"📝 {order['comment']}\n"

            f"💰 Стоимость: "
            f"{price_text(order['delivery_price'])}"
        )

    except Exception:
        pass

    await notify_store_users(
        order["store_id"],

        f"🚚 Заказ №{order_id}\n"
        f"Назначен курьер: "
        f"{courier['full_name']}."
    )

    await callback.answer(
        "Заказ назначен."
    )


# =========================================================
# АДМИН — АКТИВНЫЕ
# =========================================================

@dp.message(
    F.text == "🚚 Активные"
)
async def admin_active_orders(
    message: Message
):

    if await deny_admin_message(message):
        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(
            """
            SELECT
                o.*,

                s.store_name,

                c.full_name AS courier_name,
                c.phone AS courier_phone,
                c.vehicle AS courier_vehicle,

                su.full_name AS created_by

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

            LEFT JOIN couriers c
                ON c.id = o.courier_id

            LEFT JOIN store_users su
                ON su.telegram_id =
                   o.created_by_telegram_id

            WHERE o.status NOT IN (
                'new',
                'delivered',
                'cancelled'
            )

            ORDER BY o.id DESC
            """
        )

    if not orders:

        await message.answer(
            "🚚 Активных заказов нет.",
            reply_markup=admin_keyboard,
        )

        return

    for order in orders:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Сменить курьера",
                        callback_data=(
                            "reassign_order:"
                            f"{order['id']}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💰 Стоимость",
                        callback_data=(
                            f"set_price:"
                            f"{order['id']}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🕐 История",
                        callback_data=(
                            "admin_history:"
                            f"{order['id']}"
                        ),
                    ),
                    InlineKeyboardButton(
                        text="📸 Фото",
                        callback_data=(
                            "admin_photos:"
                            f"{order['id']}"
                        ),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить заказ",
                        callback_data=(
                            "cancel_order_admin:"
                            f"{order['id']}"
                        ),
                    )
                ],
            ]
        )

        await message.answer(
            build_order_text(
                order,
                title=(
                    f"🚚 ЗАКАЗ №"
                    f"{order['id']}"
                ),
            ),
            reply_markup=keyboard,
        )


# =========================================================
# АДМИН — ДОСТАВЛЕННЫЕ
# =========================================================

@dp.message(
    F.text == "✅ Доставленные"
)
async def admin_delivered_orders(
    message: Message
):

    if await deny_admin_message(message):
        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(
            """
            SELECT
                o.*,

                s.store_name,

                c.full_name AS courier_name,
                c.phone AS courier_phone,
                c.vehicle AS courier_vehicle,

                su.full_name AS created_by

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

            LEFT JOIN couriers c
                ON c.id = o.courier_id

            LEFT JOIN store_users su
                ON su.telegram_id =
                   o.created_by_telegram_id

            WHERE o.status = 'delivered'

            ORDER BY o.id DESC

            LIMIT 20
            """
        )

    if not orders:

        await message.answer(
            "✅ Доставленных заказов нет."
        )

        return

    for order in orders:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🕐 История",
                        callback_data=(
                            "admin_history:"
                            f"{order['id']}"
                        ),
                    ),
                    InlineKeyboardButton(
                        text="📸 Фото",
                        callback_data=(
                            "admin_photos:"
                            f"{order['id']}"
                        ),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="💰 Стоимость",
                        callback_data=(
                            f"set_price:"
                            f"{order['id']}"
                        ),
                    )
                ],
            ]
        )

        await message.answer(
            build_order_text(
                order,
                title=(
                    f"✅ ЗАКАЗ №"
                    f"{order['id']}"
                ),
            ),
            reply_markup=keyboard,
        )


# =========================================================
# АДМИН — ПОИСК ЗАКАЗА
# =========================================================

@dp.message(
    F.text == "🔎 Найти заказ"
)
async def admin_search_start(
    message: Message,
    state: FSMContext,
):

    if await deny_admin_message(message):
        return

    await state.set_state(
        AdminSearch.order_id
    )

    await message.answer(
        "🔎 Введите номер заказа.\n\n"
        "Например: 15"
    )


@dp.message(
    AdminSearch.order_id
)
async def admin_search_order(
    message: Message,
    state: FSMContext,
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    try:
        order_id = int(
            (message.text or "").strip()
        )
    except ValueError:

        await message.answer(
            "❌ Введите только номер заказа."
        )

        return

    await state.clear()

    order = await get_order_full(
        order_id
    )

    if not order:

        await message.answer(
            f"❌ Заказ №{order_id} не найден."
        )

        return

    buttons = [
        [
            InlineKeyboardButton(
                text="🕐 История",
                callback_data=(
                    f"admin_history:{order_id}"
                ),
            ),
            InlineKeyboardButton(
                text="📸 Фото",
                callback_data=(
                    f"admin_photos:{order_id}"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                text="💰 Стоимость",
                callback_data=(
                    f"set_price:{order_id}"
                ),
            )
        ],
    ]

    if order["status"] not in (
        "delivered",
        "cancelled",
    ):

        if order["status"] != "new":

            buttons.append([
                InlineKeyboardButton(
                    text="🔄 Сменить курьера",
                    callback_data=(
                        f"reassign_order:"
                        f"{order_id}"
                    ),
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                text="❌ Отменить заказ",
                callback_data=(
                    f"cancel_order_admin:"
                    f"{order_id}"
                ),
            )
        ])

    await message.answer(
        build_order_text(order),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


# =========================================================
# АДМИН — ИСТОРИЯ ЗАКАЗА
# =========================================================

@dp.callback_query(
    F.data.startswith("admin_history:")
)
async def admin_history(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    order_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        history = await conn.fetch(
            """
            SELECT
                status,
                actor_type,
                actor_telegram_id,
                note,
                created_at

            FROM order_status_history

            WHERE order_id = $1

            ORDER BY created_at ASC,
                     id ASC
            """,
            order_id,
        )

    if not history:

        await callback.answer(
            "История отсутствует.",
            show_alert=True,
        )

        return

    text = (
        f"🕐 ИСТОРИЯ ЗАКАЗА №{order_id}\n\n"
    )

    for row in history:

        time_text = row[
            "created_at"
        ].strftime("%d.%m.%Y %H:%M")

        text += (
            f"{STATUS_NAMES.get(row['status'], row['status'])}\n"
            f"🕐 {time_text}"
        )

        if row["note"]:
            text += (
                f"\n📝 {row['note']}"
            )

        text += "\n\n"

    await callback.message.answer(text)

    await callback.answer()


# =========================================================
# АДМИН — ФОТО ЗАКАЗА
# =========================================================

@dp.callback_query(
    F.data.startswith("admin_photos:")
)
async def admin_photos(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    order_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        photos = await conn.fetch(
            """
            SELECT
                photo_type,
                file_id

            FROM order_photos

            WHERE order_id = $1

            ORDER BY created_at ASC
            """,
            order_id,
        )

    if not photos:

        await callback.answer(
            "Фотографий нет.",
            show_alert=True,
        )

        return

    await callback.answer()

    for photo in photos:

        if photo["photo_type"] == "pickup":

            caption = (
                f"📦 Заказ №{order_id}\n"
                "Фото товара при получении"
            )

        else:

            caption = (
                f"✅ Заказ №{order_id}\n"
                "Фото после доставки"
            )

        try:

            await bot.send_photo(
                callback.from_user.id,
                photo=photo["file_id"],
                caption=caption,
            )

        except Exception:
            pass


# =========================================================
# АДМИН — СТОИМОСТЬ ДОСТАВКИ
# =========================================================

@dp.callback_query(
    F.data.startswith("set_price:")
)
async def set_price_start(
    callback: CallbackQuery,
    state: FSMContext,
):

    if await deny_admin_callback(callback):
        return

    order_id = int(
        callback.data.split(":")[1]
    )

    order = await get_order_full(
        order_id
    )

    if not order:

        await callback.answer(
            "Заказ не найден.",
            show_alert=True,
        )

        return

    await state.update_data(
        price_order_id=order_id
    )

    await state.set_state(
        AdminPrice.value
    )

    await callback.message.answer(
        f"💰 СТОИМОСТЬ ЗАКАЗА №{order_id}\n\n"

        f"Сейчас: "
        f"{price_text(order['delivery_price'])}\n\n"

        "Введите новую стоимость "
        "в тенге.\n\n"

        "Например: 3000"
    )

    await callback.answer()


@dp.message(AdminPrice.value)
async def save_price(
    message: Message,
    state: FSMContext,
):

    if not is_admin(
        message.from_user.id
    ):

        await state.clear()
        return

    raw = (
        message.text or ""
    ).strip().replace(" ", "")

    raw = raw.replace(",", ".")

    try:

        value = Decimal(raw)

    except InvalidOperation:

        await message.answer(
            "❌ Неверная сумма.\n\n"
            "Например: 3000"
        )

        return

    if value < 0:

        await message.answer(
            "❌ Стоимость не может "
            "быть отрицательной."
        )

        return

    data = await state.get_data()

    order_id = data.get(
        "price_order_id"
    )

    if not order_id:

        await state.clear()
        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    delivery_price = $1,
                    updated_at = NOW()

                WHERE id = $2

                RETURNING
                    id,
                    store_id
                """,
                value,
                order_id,
            )

            if order:

                current_status = (
                    await conn.fetchval(
                        """
                        SELECT status
                        FROM orders
                        WHERE id = $1
                        """,
                        order_id,
                    )
                )

                await add_history(
                    conn,
                    order_id,
                    current_status,
                    "admin",
                    message.from_user.id,
                    (
                        "Стоимость изменена: "
                        f"{price_text(value)}"
                    ),
                )

    await state.clear()

    if not order:

        await message.answer(
            "❌ Заказ не найден."
        )

        return

    await message.answer(
        f"✅ Стоимость заказа №{order_id}: "
        f"{price_text(value)}"
    )

    await notify_store_users(
        order["store_id"],
        f"💰 Заказ №{order_id}\n"
        f"Стоимость доставки: "
        f"{price_text(value)}"
    )


# =========================================================
# АДМИН — ОТМЕНА ЗАКАЗА
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "cancel_order_admin:"
    )
)
async def cancel_order_admin(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    order_id = int(
        callback.data.split(":")[1]
    )

    order = await get_order_full(
        order_id
    )

    if not order:

        await callback.answer(
            "Заказ не найден.",
            show_alert=True,
        )

        return

    if order["status"] == "delivered":

        await callback.answer(
            "Доставленный заказ "
            "отменить нельзя.",
            show_alert=True,
        )

        return

    if order["status"] == "cancelled":

        await callback.answer(
            "Заказ уже отменён.",
            show_alert=True,
        )

        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, отменить",
                    callback_data=(
                        "confirm_cancel_order:"
                        f"{order_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Не отменять",
                    callback_data=
                        "cancel_admin_action",
                )
            ],
        ]
    )

    await callback.message.answer(
        f"⚠️ ОТМЕНА ЗАКАЗА №{order_id}\n\n"

        f"🏪 {order['store_name']}\n"
        f"👤 {order['client_name']}\n\n"

        "Вы точно хотите "
        "отменить этот заказ?",

        reply_markup=keyboard,
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith(
        "confirm_cancel_order:"
    )
)
async def confirm_cancel_order(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    order_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    status = 'cancelled',
                    updated_at = NOW()

                WHERE id = $1
                  AND status != 'delivered'
                  AND status != 'cancelled'

                RETURNING
                    id,
                    store_id,
                    courier_id
                """,
                order_id,
            )

            if order:

                await add_history(
                    conn,
                    order_id,
                    "cancelled",
                    "admin",
                    callback.from_user.id,
                    "Заказ отменён администратором",
                )

    if not order:

        await callback.answer(
            "Заказ уже отменён "
            "или доставлен.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"❌ Заказ №{order_id} отменён."
    )

    await notify_store_users(
        order["store_id"],
        f"❌ Заказ №{order_id} "
        "отменён администратором."
    )

    if order["courier_id"]:

        await notify_courier(
            order["courier_id"],
            f"❌ Заказ №{order_id} "
            "отменён администратором."
        )

    await callback.answer(
        "Заказ отменён."
    )


# =========================================================
# АДМИН — ПЕРЕНАЗНАЧЕНИЕ
# =========================================================

@dp.callback_query(
    F.data.startswith("reassign_order:")
)
async def reassign_order(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    order_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            SELECT
                id,
                status,
                courier_id

            FROM orders

            WHERE id = $1
            """,
            order_id,
        )

        couriers = await conn.fetch(
            """
            SELECT
                id,
                full_name,
                vehicle

            FROM couriers

            WHERE status = 'approved'

            ORDER BY full_name
            """
        )

    if not order:

        await callback.answer(
            "Заказ не найден.",
            show_alert=True,
        )

        return

    if order["status"] in (
        "delivered",
        "cancelled",
    ):

        await callback.answer(
            "Этот заказ нельзя переназначить.",
            show_alert=True,
        )

        return

    buttons = []

    for courier in couriers:

        if (
            courier["id"]
            == order["courier_id"]
        ):
            continue

        buttons.append([
            InlineKeyboardButton(
                text=(
                    "🚚 "
                    f"{courier['full_name']} "
                    f"({courier['vehicle']})"
                ),
                callback_data=(
                    "confirm_reassign:"
                    f"{order_id}:"
                    f"{courier['id']}"
                ),
            )
        ])

    if not buttons:

        await callback.answer(
            "Нет другого "
            "одобренного курьера.",
            show_alert=True,
        )

        return

    buttons.append([
        InlineKeyboardButton(
            text="↩️ Отмена",
            callback_data=
                "cancel_admin_action",
        )
    ])

    await callback.message.answer(
        "🔄 СМЕНА КУРЬЕРА\n\n"

        f"Заказ №{order_id}\n\n"

        "Выберите нового курьера:",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith(
        "confirm_reassign:"
    )
)
async def confirm_reassign(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    parts = callback.data.split(":")

    order_id = int(parts[1])
    new_courier_id = int(parts[2])

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                SELECT *

                FROM orders

                WHERE id = $1

                FOR UPDATE
                """,
                order_id,
            )

            if not order:

                await callback.answer(
                    "Заказ не найден.",
                    show_alert=True,
                )

                return

            if order["status"] in (
                "delivered",
                "cancelled",
            ):

                await callback.answer(
                    "Этот заказ "
                    "нельзя переназначить.",
                    show_alert=True,
                )

                return

            new_courier = await conn.fetchrow(
                """
                SELECT
                    id,
                    telegram_id,
                    full_name

                FROM couriers

                WHERE id = $1
                  AND status = 'approved'
                """,
                new_courier_id,
            )

            if not new_courier:

                await callback.answer(
                    "Новый курьер недоступен.",
                    show_alert=True,
                )

                return

            old_courier_id = (
                order["courier_id"]
            )

            store = await conn.fetchrow(
                """
                SELECT store_name

                FROM stores

                WHERE id = $1
                """,
                order["store_id"],
            )

            created_by = await conn.fetchval(
                """
                SELECT full_name

                FROM store_users

                WHERE telegram_id = $1
                """,
                order[
                    "created_by_telegram_id"
                ],
            )

            await conn.execute(
                """
                UPDATE orders

                SET
                    courier_id = $1,
                    status = 'assigned',
                    updated_at = NOW()

                WHERE id = $2
                """,
                new_courier_id,
                order_id,
            )

            await add_history(
                conn,
                order_id,
                "assigned",
                "admin",
                callback.from_user.id,
                (
                    "Курьер изменён на: "
                    f"{new_courier['full_name']}"
                ),
            )

    created_by = (
        created_by
        or "Не указан"
    )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Заказ №{order_id} "
        "переназначен.\n\n"

        f"🚚 Новый курьер: "
        f"{new_courier['full_name']}"
    )

    if old_courier_id:

        await notify_courier(
            old_courier_id,
            f"🔄 Заказ №{order_id} "
            "переназначен другому курьеру."
        )

    try:

        await bot.send_message(
            new_courier["telegram_id"],

            f"🚚 ВАМ НАЗНАЧЕН "
            f"ЗАКАЗ №{order_id}\n\n"

            f"🏪 Магазин: "
            f"{store['store_name']}\n"

            f"👤 Создал: "
            f"{created_by}\n"

            f"📍 Забрать: "
            f"{order['pickup_address']}\n\n"

            f"👤 Клиент: "
            f"{order['client_name']}\n"

            f"📞 {order['client_phone']}\n"

            f"📍 Доставить: "
            f"{order['delivery_address']}\n"

            f"📦 {order['item']}\n"

            f"🕐 {order['delivery_time']}\n"

            f"📝 {order['comment']}\n"

            f"💰 Стоимость: "
            f"{price_text(order['delivery_price'])}"
        )

    except Exception:
        pass

    await notify_store_users(
        order["store_id"],

        f"🔄 Заказ №{order_id}\n"
        "Курьер изменён.\n"

        f"🚚 Новый курьер: "
        f"{new_courier['full_name']}"
    )

    await callback.answer(
        "Курьер изменён."
    )


@dp.callback_query(
    F.data == "cancel_admin_action"
)
async def cancel_admin_action(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        "Действие отменено."
    )

    await callback.answer()


# =========================================================
# АДМИН — МАГАЗИНЫ
# =========================================================

@dp.message(
    F.text == "🏪 Магазины"
)
async def admin_stores(
    message: Message
):

    if await deny_admin_message(message):
        return

    async with db_pool.acquire() as conn:

        stores = await conn.fetch(
            """
            SELECT
                s.*,

                COUNT(su.id)
                    AS members_count

            FROM stores s

            LEFT JOIN store_users su
                ON su.store_id = s.id

            GROUP BY s.id

            ORDER BY
                CASE
                    WHEN s.status =
                        'pending'
                    THEN 0

                    WHEN s.status =
                        'approved'
                    THEN 1

                    ELSE 2
                END,
                s.id DESC
            """
        )

    if not stores:

        await message.answer(
            "🏪 Магазинов пока нет."
        )

        return

    for store in stores:

        keyboard = None

        if store["status"] == "pending":

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="✅ Одобрить",
                        callback_data=(
                            "approve_store:"
                            f"{store['id']}"
                        ),
                    ),

                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=(
                            "reject_store:"
                            f"{store['id']}"
                        ),
                    ),
                ]]
            )

        status = {
            "pending": "⏳ Ожидает",
            "approved": "✅ Одобрен",
            "rejected": "❌ Отклонён",
        }.get(
            store["status"],
            store["status"],
        )

        await message.answer(
            f"🏪 {store['store_name']}\n\n"

            f"Статус: {status}\n"

            f"👥 Пользователей: "
            f"{store['members_count']}\n"

            f"👤 Контакт: "
            f"{store['contact_name']}\n"

            f"📞 {store['phone']}\n"

            f"📍 {store['address']}",

            reply_markup=keyboard,
        )


# =========================================================
# АДМИН — КУРЬЕРЫ
# =========================================================

@dp.message(
    F.text == "🚚 Курьеры"
)
async def admin_couriers(
    message: Message
):

    if await deny_admin_message(message):
        return

    async with db_pool.acquire() as conn:

        couriers = await conn.fetch(
            """
            SELECT *

            FROM couriers

            ORDER BY
                CASE
                    WHEN status = 'pending'
                    THEN 0

                    WHEN status = 'approved'
                    THEN 1

                    ELSE 2
                END,
                id DESC
            """
        )

    if not couriers:

        await message.answer(
            "🚚 Курьеров пока нет."
        )

        return

    for courier in couriers:

        keyboard = None

        if courier["status"] == "pending":

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="✅ Одобрить",
                        callback_data=(
                            "approve_courier:"
                            f"{courier['id']}"
                        ),
                    ),

                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=(
                            "reject_courier:"
                            f"{courier['id']}"
                        ),
                    ),
                ]]
            )

        status = {
            "pending": "⏳ Ожидает",
            "approved": "✅ Одобрен",
            "rejected": "❌ Отклонён",
        }.get(
            courier["status"],
            courier["status"],
        )

        await message.answer(
            f"🚚 {courier['full_name']}\n\n"

            f"Статус: {status}\n"

            f"📞 {courier['phone']}\n"

            f"🚗 {courier['vehicle']}",

            reply_markup=keyboard,
        )


# =========================================================
# АДМИН — ОДОБРЕНИЕ МАГАЗИНА
# =========================================================

@dp.callback_query(
    F.data.startswith("approve_store:")
)
async def approve_store(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    store_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        store = await conn.fetchrow(
            """
            UPDATE stores

            SET status = 'approved'

            WHERE id = $1

            RETURNING store_name
            """,
            store_id,
        )

        users = await conn.fetch(
            """
            SELECT telegram_id

            FROM store_users

            WHERE store_id = $1
            """,
            store_id,
        )

    if not store:

        await callback.answer(
            "Магазин не найден.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    for user in users:

        try:

            await bot.send_message(
                user["telegram_id"],

                f"✅ Магазин "
                f"{store['store_name']} "
                "одобрен."
            )

        except Exception:
            pass

    await callback.answer(
        "Магазин одобрен."
    )


@dp.callback_query(
    F.data.startswith("reject_store:")
)
async def reject_store(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    store_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE stores

            SET status = 'rejected'

            WHERE id = $1
            """,
            store_id,
        )

        users = await conn.fetch(
            """
            SELECT telegram_id

            FROM store_users

            WHERE store_id = $1
            """,
            store_id,
        )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    for user in users:

        try:

            await bot.send_message(
                user["telegram_id"],
                "❌ Заявка магазина отклонена."
            )

        except Exception:
            pass

    await callback.answer(
        "Магазин отклонён."
    )


# =========================================================
# АДМИН — ОДОБРЕНИЕ КУРЬЕРА
# =========================================================

@dp.callback_query(
    F.data.startswith("approve_courier:")
)
async def approve_courier(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    courier_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        courier = await conn.fetchrow(
            """
            UPDATE couriers

            SET status = 'approved'

            WHERE id = $1

            RETURNING
                telegram_id,
                full_name
            """,
            courier_id,
        )

    if not courier:

        await callback.answer(
            "Курьер не найден.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    try:

        role, _ = await get_user_role(
            courier["telegram_id"]
        )

        await bot.send_message(
            courier["telegram_id"],

            "✅ Ваша заявка курьера "
            "одобрена.",

            reply_markup=main_keyboard(
                role,
                courier["telegram_id"],
            ),
        )

    except Exception:
        pass

    await callback.answer(
        "Курьер одобрен."
    )


@dp.callback_query(
    F.data.startswith("reject_courier:")
)
async def reject_courier(
    callback: CallbackQuery
):

    if await deny_admin_callback(callback):
        return

    courier_id = int(
        callback.data.split(":")[1]
    )

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

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    if courier:

        try:

            await bot.send_message(
                courier["telegram_id"],
                "❌ Заявка курьера отклонена."
            )

        except Exception:
            pass

    await callback.answer(
        "Курьер отклонён."
    )


# =========================================================
# ОБЫЧНАЯ ОТМЕНА
# =========================================================

@dp.message(
    F.text == "❌ Отмена"
)
async def cancel_handler(
    message: Message,
    state: FSMContext,
):

    await state.clear()
    await send_main_menu(message)


# =========================================================
# FALLBACK
# =========================================================

@dp.message()
async def fallback(
    message: Message
):

    await message.answer(
        "Пожалуйста, используйте "
        "кнопки меню."
    )


# =========================================================
# ЗАПУСК
# =========================================================

async def main():

    print(
        "Connecting to PostgreSQL..."
    )

    await init_db()

    print(
        "Database connected."
    )

    print(
        "Admin ID:",
        ADMIN_ID
        if ADMIN_ID
        else "NOT SET",
    )

    print(
        "Bot is starting..."
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(main())
