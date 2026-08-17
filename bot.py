import os
import asyncio
import secrets
import string
from decimal import Decimal, InvalidOperation

import asyncpg

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
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


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


ADMIN_ID = safe_int(ADMIN_ID_RAW)

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


class CourierPhoto(StatesGroup):
    pickup_photo = State()
    delivery_photo = State()


class AdminPrice(StatesGroup):
    value = State()


# =========================================================
# КЛАВИАТУРЫ
# =========================================================

def is_admin(user_id: int) -> bool:
    return (
        ADMIN_ID != 0
        and user_id == ADMIN_ID
    )


def main_keyboard(role, user_id: int):

    rows = []

    if role == "store":

        rows.append([
            KeyboardButton(text="🏪 Магазин")
        ])

    elif role == "courier":

        rows.append([
            KeyboardButton(text="🚚 Курьер")
        ])

    else:

        rows.append([
            KeyboardButton(text="🏪 Магазин"),
            KeyboardButton(text="🚚 Курьер"),
        ])

    if is_admin(user_id):

        rows.append([
            KeyboardButton(text="👨‍💼 Администратор")
        ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
    )


store_entry_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🆕 Зарегистрировать магазин")],
        [KeyboardButton(text="🔑 Присоединиться к магазину")],
        [KeyboardButton(text="⬅️ Главное меню")],
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

        [KeyboardButton(text="👥 Менеджеры")],

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


registration_confirm_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Отправить заявку")],
        [KeyboardButton(text="❌ Отмена")],
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


skip_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏭ Пропустить")]
    ],
    resize_keyboard=True,
)


admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="📦 Новые заказы"),
            KeyboardButton(text="🚚 Активные"),
        ],

        [KeyboardButton(text="✅ Доставленные")],

        [
            KeyboardButton(text="🏪 Магазины"),
            KeyboardButton(text="🚚 Курьеры"),
        ],

        [KeyboardButton(text="⬅️ Главное меню")],
    ],
    resize_keyboard=True,
)


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

                store_id INTEGER NOT NULL
                    REFERENCES stores(id),

                client_name TEXT NOT NULL,
                client_phone TEXT NOT NULL,

                pickup_address TEXT NOT NULL,
                delivery_address TEXT NOT NULL,

                item TEXT NOT NULL,

                kittek_order_number TEXT,
                kaspi_order_number TEXT,

                delivery_time TEXT NOT NULL,
                comment TEXT,

                status TEXT NOT NULL DEFAULT 'new',

                courier_id INTEGER,

                created_by_telegram_id BIGINT,

                delivery_price NUMERIC(12,2)
                    NOT NULL DEFAULT 0,

                created_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW(),

                updated_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW()
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_photos (
                id SERIAL PRIMARY KEY,

                order_id INTEGER NOT NULL
                    REFERENCES orders(id)
                    ON DELETE CASCADE,

                courier_id INTEGER NOT NULL
                    REFERENCES couriers(id),

                photo_type TEXT NOT NULL,

                file_id TEXT NOT NULL,

                created_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW()
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS store_users (
                id SERIAL PRIMARY KEY,

                store_id INTEGER NOT NULL
                    REFERENCES stores(id)
                    ON DELETE CASCADE,

                telegram_id BIGINT NOT NULL UNIQUE,

                full_name TEXT NOT NULL,

                member_role TEXT NOT NULL
                    DEFAULT 'manager',

                created_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW()
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS store_invites (
                id SERIAL PRIMARY KEY,

                store_id INTEGER NOT NULL
                    REFERENCES stores(id)
                    ON DELETE CASCADE,

                code TEXT NOT NULL UNIQUE,

                created_by BIGINT NOT NULL,

                is_active BOOLEAN NOT NULL
                    DEFAULT TRUE,

                used_by BIGINT,

                created_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW(),

                used_at TIMESTAMPTZ
            )
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_status_history (
                id SERIAL PRIMARY KEY,

                order_id INTEGER NOT NULL
                    REFERENCES orders(id)
                    ON DELETE CASCADE,

                status TEXT NOT NULL,

                actor_type TEXT,

                actor_telegram_id BIGINT,

                note TEXT,

                created_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW()
            )
            """
        )

        # ГРУППА + ТЕМЫ ДЛЯ КАЖДОГО МАГАЗИНА

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS store_report_settings (
                store_id INTEGER PRIMARY KEY
                    REFERENCES stores(id)
                    ON DELETE CASCADE,

                group_chat_id BIGINT,

                new_orders_topic_id BIGINT,

                status_topic_id BIGINT,

                updated_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW()
            )
            """
        )

        # МИГРАЦИИ

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
            kittek_order_number TEXT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
            kaspi_order_number TEXT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
            created_by_telegram_id BIGINT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
            delivery_price NUMERIC(12,2)
            NOT NULL DEFAULT 0
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
            updated_at TIMESTAMPTZ
            NOT NULL DEFAULT NOW()
            """
        )

        # СТАРЫЕ ВЛАДЕЛЬЦЫ

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


# =========================================================
# ОБЩИЕ ФУНКЦИИ
# =========================================================

def optional_number(value):

    if value is None:
        return "—"

    value = str(value).strip()

    if not value:
        return "—"

    return value


def price_text(value):

    try:
        value = Decimal(value or 0)

    except Exception:
        value = Decimal("0")

    if value <= 0:
        return "Не указана"

    if value == value.to_integral():

        return (
            f"{int(value):,}"
            .replace(",", " ")
            + " ₸"
        )

    return (
        f"{value:,.2f}"
        .replace(",", " ")
        + " ₸"
    )


async def get_store_membership(user_id: int):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT
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


async def get_approved_courier_id(user_id: int):

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

    if store:
        return "store", store

    if courier:
        return "courier", courier

    return None, None


async def send_main_menu(message: Message):

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


async def deny_admin_message(message: Message):

    if not is_admin(
        message.from_user.id
    ):

        await message.answer(
            "❌ Доступ запрещён."
        )

        return True

    return False


async def deny_admin_callback(callback: CallbackQuery):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True,
        )

        return True

    return False


async def add_history(
    conn,
    order_id,
    status,
    actor_type=None,
    actor_telegram_id=None,
    note=None,
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


async def get_order_full(order_id):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT
                o.*,

                s.store_name,

                c.full_name AS courier_name,

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


def build_order_text(order, title=None):

    if not title:
        title = f"📦 ЗАКАЗ №{order['id']}"

    courier_name = (
        order["courier_name"]
        if "courier_name" in order
        and order["courier_name"]
        else "Не назначен"
    )

    created_by = (
        order["created_by"]
        if "created_by" in order
        and order["created_by"]
        else "Не указан"
    )

    return (
        f"{title}\n\n"

        f"🏪 {order['store_name']}\n"
        f"👤 Создал: {created_by}\n\n"

        f"🔢 Kittek №: "
        f"{optional_number(order['kittek_order_number'])}\n"

        f"🛒 Kaspi №: "
        f"{optional_number(order['kaspi_order_number'])}\n\n"

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

        f"🚚 Курьер: {courier_name}\n"

        f"💰 Стоимость: "
        f"{price_text(order['delivery_price'])}"
    )


# =========================================================
# ОТПРАВКА В ГРУППУ МАГАЗИНА
# =========================================================

async def get_store_report_settings(
    store_id
):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT
                group_chat_id,
                new_orders_topic_id,
                status_topic_id

            FROM store_report_settings

            WHERE store_id = $1
            """,
            store_id,
        )


async def send_store_text(
    store_id,
    topic_type,
    text,
):

    settings = await get_store_report_settings(
        store_id
    )

    if not settings:
        return

    chat_id = settings["group_chat_id"]

    if not chat_id:
        return

    if topic_type == "orders":
        topic_id = settings["new_orders_topic_id"]

    else:
        topic_id = settings["status_topic_id"]

    if not topic_id:
        return

    try:

        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=text,
        )

    except Exception as error:

        print(
            "STORE GROUP ERROR:",
            store_id,
            topic_type,
            error,
        )


async def send_store_photo(
    store_id,
    file_id,
    caption,
):

    settings = await get_store_report_settings(
        store_id
    )

    if not settings:
        return

    if (
        not settings["group_chat_id"]
        or not settings["status_topic_id"]
    ):
        return

    try:

        await bot.send_photo(
            chat_id=settings["group_chat_id"],
            message_thread_id=settings["status_topic_id"],
            photo=file_id,
            caption=caption,
        )

    except Exception as error:

        print(
            "STORE PHOTO ERROR:",
            store_id,
            error,
        )


async def build_status_text(
    order_id,
    headline,
):

    order = await get_order_full(
        order_id
    )

    if not order:

        return (
            f"{headline}\n\n"
            f"📦 Заказ №{order_id}"
        )

    return (
        f"{headline}\n\n"

        f"📦 Заказ №{order_id}\n"

        f"🔢 Kittek №: "
        f"{optional_number(order['kittek_order_number'])}\n"

        f"🛒 Kaspi №: "
        f"{optional_number(order['kaspi_order_number'])}\n\n"

        f"🏪 {order['store_name']}\n"

        f"👤 Клиент: "
        f"{order['client_name']}\n"

        f"📍 "
        f"{order['delivery_address']}"
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

        text += "\n\n🏪 Ваша роль: Магазин"

        if info["status"] == "pending":

            text += (
                "\n⏳ Магазин ожидает одобрения."
            )

    elif role == "courier":

        text += "\n\n🚚 Ваша роль: Курьер"

        if info["status"] == "pending":

            text += (
                "\n⏳ Заявка ожидает одобрения."
            )

    else:

        text += "\n\nВыберите вашу роль:"

    await message.answer(
        text,
        reply_markup=main_keyboard(
            role,
            message.from_user.id,
        ),
    )


@dp.message(Command("myid"))
async def myid_handler(message: Message):

    await message.answer(
        f"🆔 Ваш Telegram ID:\n\n"
        f"{message.from_user.id}"
    )


@dp.message(
    F.text == "⬅️ Главное меню"
)
async def back_main(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await send_main_menu(
        message
    )


# =========================================================
# ПРИВЯЗКА ГРУППЫ
# =========================================================

@dp.message(Command("bindorders"))
async def bind_orders_topic(
    message: Message
):

    if message.chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:

        await message.answer(
            "❌ Команда должна быть "
            "в группе магазина."
        )

        return

    if message.sender_chat is not None:

        await message.answer(
            "❌ Вы пишете анонимно.\n\n"
            "Отключите анонимность администратора "
            "и отправьте команду от своего аккаунта."
        )

        return

    if not message.message_thread_id:

        await message.answer(
            "❌ Откройте тему новых заказов "
            "и отправьте /bindorders внутри неё."
        )

        return

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await message.answer(
            "❌ Ваш аккаунт не привязан "
            "к магазину."
        )

        return

    if membership["status"] != "approved":

        await message.answer(
            "❌ Магазин ещё не одобрен."
        )

        return

    if membership["member_role"] != "owner":

        await message.answer(
            "❌ Привязать группу может "
            "только владелец магазина."
        )

        return

    async with db_pool.acquire() as conn:

        other = await conn.fetchrow(
            """
            SELECT
                s.store_name

            FROM store_report_settings rs

            JOIN stores s
                ON s.id = rs.store_id

            WHERE rs.group_chat_id = $1
              AND rs.store_id != $2
            """,
            message.chat.id,
            membership["store_id"],
        )

        if other:

            await message.answer(
                "❌ Эта группа уже привязана "
                "к другому магазину:\n\n"
                f"🏪 {other['store_name']}"
            )

            return

        old = await conn.fetchrow(
            """
            SELECT group_chat_id

            FROM store_report_settings

            WHERE store_id = $1
            """,
            membership["store_id"],
        )

        clear_status = (
            old
            and old["group_chat_id"]
            and old["group_chat_id"]
            != message.chat.id
        )

        await conn.execute(
            """
            INSERT INTO store_report_settings (
                store_id,
                group_chat_id,
                new_orders_topic_id,
                updated_at
            )

            VALUES ($1,$2,$3,NOW())

            ON CONFLICT (store_id)

            DO UPDATE SET
                group_chat_id =
                    EXCLUDED.group_chat_id,

                new_orders_topic_id =
                    EXCLUDED.new_orders_topic_id,

                updated_at = NOW()
            """,
            membership["store_id"],
            message.chat.id,
            message.message_thread_id,
        )

        if clear_status:

            await conn.execute(
                """
                UPDATE store_report_settings

                SET status_topic_id = NULL

                WHERE store_id = $1
                """,
                membership["store_id"],
            )

    await message.answer(
        "✅ ТЕМА НОВЫХ ЗАКАЗОВ ПРИВЯЗАНА!\n\n"

        f"🏪 {membership['store_name']}\n\n"

        "📦 Новые заказы будут "
        "приходить сюда."
    )


@dp.message(Command("bindstatus"))
async def bind_status_topic(
    message: Message
):

    if message.chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:

        await message.answer(
            "❌ Команда должна быть "
            "в группе магазина."
        )

        return

    if message.sender_chat is not None:

        await message.answer(
            "❌ Вы пишете анонимно.\n\n"
            "Отправьте команду "
            "от личного аккаунта."
        )

        return

    if not message.message_thread_id:

        await message.answer(
            "❌ Откройте тему статусов "
            "и отправьте /bindstatus внутри неё."
        )

        return

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await message.answer(
            "❌ Ваш аккаунт не привязан "
            "к магазину."
        )

        return

    if membership["status"] != "approved":

        await message.answer(
            "❌ Магазин ещё не одобрен."
        )

        return

    if membership["member_role"] != "owner":

        await message.answer(
            "❌ Привязать тему может "
            "только владелец."
        )

        return

    async with db_pool.acquire() as conn:

        settings = await conn.fetchrow(
            """
            SELECT
                group_chat_id,
                new_orders_topic_id

            FROM store_report_settings

            WHERE store_id = $1
            """,
            membership["store_id"],
        )

        if not settings:

            await message.answer(
                "❌ Сначала выполните /bindorders "
                "в теме новых заказов."
            )

            return

        if (
            settings["group_chat_id"]
            != message.chat.id
        ):

            await message.answer(
                "❌ Эта тема находится "
                "в другой группе."
            )

            return

        if (
            settings["new_orders_topic_id"]
            == message.message_thread_id
        ):

            await message.answer(
                "❌ Для статусов нужна "
                "другая тема."
            )

            return

        await conn.execute(
            """
            UPDATE store_report_settings

            SET
                status_topic_id = $1,
                updated_at = NOW()

            WHERE store_id = $2
            """,
            message.message_thread_id,
            membership["store_id"],
        )

    await message.answer(
        "✅ ТЕМА СТАТУСОВ ПРИВЯЗАНА!\n\n"

        f"🏪 {membership['store_name']}\n\n"

        "🚚 Статусы и фотоотчёты "
        "будут приходить сюда."
    )


@dp.message(Command("reportsettings"))
async def report_settings(
    message: Message
):

    if message.sender_chat is not None:

        await message.answer(
            "❌ Отправьте команду "
            "от личного аккаунта."
        )

        return

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await message.answer(
            "❌ Магазин не найден."
        )

        return

    settings = await get_store_report_settings(
        membership["store_id"]
    )

    if not settings:

        await message.answer(
            "⚙️ TELEGRAM\n\n"

            f"🏪 {membership['store_name']}\n\n"

            "📦 Новые заказы: ❌\n"
            "🚚 Статусы/фото: ❌"
        )

        return

    await message.answer(
        "⚙️ TELEGRAM\n\n"

        f"🏪 {membership['store_name']}\n\n"

        f"📦 Новые заказы: "
        f"{'✅' if settings['new_orders_topic_id'] else '❌'}\n"

        f"🚚 Статусы/фото: "
        f"{'✅' if settings['status_topic_id'] else '❌'}"
    )


@dp.message(Command("unbindgroup"))
async def unbind_group(
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

    if membership["member_role"] != "owner":

        await message.answer(
            "❌ Только владелец может "
            "отвязать группу."
        )

        return

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            DELETE FROM store_report_settings

            WHERE store_id = $1
            """,
            membership["store_id"],
        )

    await message.answer(
        "✅ Группа отвязана."
    )


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
            "как курьер."
        )

        return

    if role == "store":

        if info["status"] == "approved":

            await message.answer(
                f"🏪 {info['store_name']}\n\n"
                "Выберите действие:",
                reply_markup=store_keyboard,
            )

        else:

            await message.answer(
                f"🏪 {info['store_name']}\n\n"
                "⏳ Магазин ожидает одобрения."
            )

        return

    await message.answer(
        "🏪 МАГАЗИН\n\n"
        "Выберите действие:",
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
        "🏪 Введите название магазина:"
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
        "📍 Введите адрес магазина/склада:"
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

        f"🏪 {data['store_name']}\n"
        f"👤 {data['contact_name']}\n"
        f"📞 {data['phone']}\n"
        f"📍 {data['address']}\n\n"

        "Отправить заявку?",

        reply_markup=registration_confirm_keyboard,
    )


@dp.message(
    StoreRegistration.confirm,
    F.text == "✅ Отправить заявку",
)
async def register_store_confirm(
    message: Message,
    state: FSMContext,
):

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

                    status =
                        'pending'

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

                VALUES ($1,$2,$3,'owner')

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
        "✅ Заявка отправлена."
    )

    await send_main_menu(
        message
    )


# =========================================================
# МЕНЕДЖЕР
# =========================================================

@dp.message(
    F.text == "🔑 Присоединиться к магазину"
)
async def join_store_start(
    message: Message,
    state: FSMContext,
):

    await state.set_state(
        StoreJoin.invite_code
    )

    await message.answer(
        "🔑 Введите код приглашения:"
    )


@dp.message(StoreJoin.invite_code)
async def join_store_code(
    message: Message,
    state: FSMContext,
):

    code = (
        message.text or ""
    ).strip().upper()

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
                    "или использован."
                )

                return

            if invite["status"] != "approved":

                await message.answer(
                    "❌ Магазин недоступен."
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

                VALUES ($1,$2,$3,'manager')

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
        f"✅ Вы присоединились к "
        f"{invite['store_name']}.",
        reply_markup=store_keyboard,
    )


# =========================================================
# МЕНЕДЖЕРЫ
# =========================================================

@dp.message(F.text == "👥 Менеджеры")
async def managers_handler(message: Message):

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:
        return

    async with db_pool.acquire() as conn:

        members = await conn.fetch(
            """
            SELECT
                full_name,
                telegram_id,
                member_role

            FROM store_users

            WHERE store_id = $1

            ORDER BY
                CASE
                    WHEN member_role = 'owner'
                    THEN 0
                    ELSE 1
                END
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
                f"👑 Владелец: "
                f"{member['full_name']}\n"
            )

        else:

            text += (
                f"👤 Менеджер: "
                f"{member['full_name']}\n"
            )

            if (
                membership["member_role"]
                == "owner"
            ):

                buttons.append([
                    InlineKeyboardButton(
                        text=(
                            f"❌ Удалить "
                            f"{member['full_name']}"
                        ),

                        callback_data=(
                            f"remove_manager:"
                            f"{member['telegram_id']}"
                        ),
                    )
                ])

    if (
        membership["member_role"]
        == "owner"
    ):

        buttons.append([
            InlineKeyboardButton(
                text="➕ Пригласить менеджера",
                callback_data="create_manager_invite",
            )
        ])

    keyboard = (
        InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
        if buttons
        else None
    )

    await message.answer(
        text,
        reply_markup=keyboard,
    )


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
        or membership["member_role"] != "owner"
    ):

        await callback.answer(
            "❌ Только владелец.",
            show_alert=True,
        )

        return

    alphabet = (
        string.ascii_uppercase
        + string.digits
    )

    async with db_pool.acquire() as conn:

        while True:

            code = "".join(
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
                code,
            )

            if not exists:
                break

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

    await callback.message.answer(
        "🔑 КОД ПРИГЛАШЕНИЯ\n\n"

        f"🏪 {membership['store_name']}\n\n"

        f"Код: {code}\n\n"

        "⚠️ Код одноразовый."
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("remove_manager:")
)
async def remove_manager(
    callback: CallbackQuery
):

    membership = await get_store_membership(
        callback.from_user.id
    )

    if (
        not membership
        or membership["member_role"] != "owner"
    ):

        await callback.answer(
            "❌ Нет доступа.",
            show_alert=True,
        )

        return

    manager_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        manager = await conn.fetchrow(
            """
            DELETE FROM store_users

            WHERE store_id = $1
              AND telegram_id = $2
              AND member_role = 'manager'

            RETURNING full_name
            """,
            membership["store_id"],
            manager_id,
        )

    if manager:

        await callback.message.answer(
            f"✅ Менеджер "
            f"{manager['full_name']} удалён."
        )

    await callback.answer()


# =========================================================
# ПРОФИЛЬ МАГАЗИНА
# =========================================================

@dp.message(
    F.text == "🏪 Профиль магазина"
)
async def store_profile(message: Message):

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:
        return

    role = (
        "👑 Владелец"
        if membership["member_role"] == "owner"
        else "👤 Менеджер"
    )

    await message.answer(
        "🏪 ПРОФИЛЬ МАГАЗИНА\n\n"

        f"Название: "
        f"{membership['store_name']}\n"

        f"📍 {membership['address']}\n"
        f"📞 {membership['phone']}\n\n"

        f"👤 {membership['full_name']}\n"
        f"🔐 {role}"
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
        or membership["status"] != "approved"
    ):

        await message.answer(
            "❌ Магазин не одобрен."
        )

        return

    await state.clear()

    await state.set_state(
        OrderCreation.client_name
    )

    await message.answer(
        "👤 Введите имя клиента:",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(OrderCreation.client_name)
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
        "📞 Введите телефон клиента:"
    )


@dp.message(OrderCreation.client_phone)
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
        "📦 Что нужно доставить?"
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
        "🔢 Введите номер заказа Kittek.\n\n"
        "Если номера нет — "
        "нажмите «⏭ Пропустить».",

        reply_markup=skip_keyboard,
    )


@dp.message(
    OrderCreation.kittek_order_number
)
async def order_kittek_number(
    message: Message,
    state: FSMContext,
):

    value = None

    if message.text != "⏭ Пропустить":

        value = (
            message.text or ""
        ).strip()

    await state.update_data(
        kittek_order_number=value
    )

    await state.set_state(
        OrderCreation.kaspi_order_number
    )

    await message.answer(
        "🛒 Введите номер заказа Kaspi.\n\n"
        "Если номера нет — "
        "нажмите «⏭ Пропустить».",

        reply_markup=skip_keyboard,
    )


@dp.message(
    OrderCreation.kaspi_order_number
)
async def order_kaspi_number(
    message: Message,
    state: FSMContext,
):

    value = None

    if message.text != "⏭ Пропустить":

        value = (
            message.text or ""
        ).strip()

    await state.update_data(
        kaspi_order_number=value
    )

    await state.set_state(
        OrderCreation.delivery_time
    )

    await message.answer(
        "🕐 Введите время доставки:",
        reply_markup=ReplyKeyboardRemove(),
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
        "📝 Введите комментарий.\n\n"
        "Если нет — напишите: Нет"
    )


@dp.message(OrderCreation.comment)
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

    await state.set_state(
        OrderCreation.confirm
    )

    await message.answer(
        "📦 ПРОВЕРЬТЕ ЗАКАЗ\n\n"

        f"🏪 {membership['store_name']}\n"

        f"👤 Создал: "
        f"{membership['full_name']}\n\n"

        f"🔢 Kittek №: "
        f"{optional_number(data.get('kittek_order_number'))}\n"

        f"🛒 Kaspi №: "
        f"{optional_number(data.get('kaspi_order_number'))}\n\n"

        f"📍 Забрать: "
        f"{membership['address']}\n\n"

        f"👤 Клиент: "
        f"{data['client_name']}\n"

        f"📞 {data['client_phone']}\n"

        f"📍 {data['delivery_address']}\n\n"

        f"📦 {data['item']}\n"
        f"🕐 {data['delivery_time']}\n"
        f"📝 {data['comment']}",

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
                    kittek_order_number,
                    kaspi_order_number,
                    delivery_time,
                    comment,
                    created_by_telegram_id
                )

                VALUES (
                    $1,$2,$3,$4,$5,
                    $6,$7,$8,$9,$10,$11
                )

                RETURNING id
                """,
                membership["store_id"],
                data["client_name"],
                data["client_phone"],
                membership["address"],
                data["delivery_address"],
                data["item"],
                data.get(
                    "kittek_order_number"
                ),
                data.get(
                    "kaspi_order_number"
                ),
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
        f"✅ Заказ №{order_id} создан!",
        reply_markup=store_keyboard,
    )

    # =============================================
    # АВТОМАТИЧЕСКАЯ ОТПРАВКА В ГРУППУ
    # =============================================

    order = await get_order_full(
        order_id
    )

    if order:

        await send_store_text(
            order["store_id"],
            "orders",
            build_order_text(
                order,
                title=(
                    f"🆕 НОВЫЙ ЗАКАЗ №"
                    f"{order_id}"
                ),
            ),
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
        "❌ Создание отменено.",
        reply_markup=store_keyboard,
    )


# =========================================================
# МОИ ЗАКАЗЫ
# =========================================================

@dp.message(F.text == "📦 Мои заказы")
async def store_orders(message: Message):

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:
        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(
            """
            SELECT
                o.*,

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
            "📦 Заказов нет."
        )

        return

    for order in orders:

        await message.answer(
            f"📦 ЗАКАЗ №{order['id']}\n\n"

            f"Статус: "
            f"{STATUS_NAMES.get(order['status'], order['status'])}\n\n"

            f"🔢 Kittek №: "
            f"{optional_number(order['kittek_order_number'])}\n"

            f"🛒 Kaspi №: "
            f"{optional_number(order['kaspi_order_number'])}\n\n"

            f"👤 Создал: "
            f"{order['created_by'] or '—'}\n"

            f"👤 Клиент: "
            f"{order['client_name']}\n"

            f"📞 {order['client_phone']}\n"

            f"📍 {order['delivery_address']}\n"

            f"📦 {order['item']}\n"

            f"🕐 {order['delivery_time']}\n"

            f"📝 {order['comment']}"
        )


# =========================================================
# КУРЬЕР
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
            "как магазин."
        )

        return

    if role == "courier":

        if info["status"] == "approved":

            await message.answer(
                f"🚚 {info['full_name']}",
                reply_markup=courier_keyboard,
            )

        else:

            await message.answer(
                "⏳ Заявка ожидает одобрения."
            )

        return

    await state.set_state(
        CourierRegistration.full_name
    )

    await message.answer(
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
        "📞 Введите телефон:"
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

        f"👤 {data['full_name']}\n"
        f"📞 {data['phone']}\n"
        f"🚗 {data['vehicle']}",

        reply_markup=registration_confirm_keyboard,
    )


@dp.message(
    CourierRegistration.confirm,
    F.text == "✅ Отправить заявку",
)
async def courier_confirm(
    message: Message,
    state: FSMContext,
):

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

            VALUES ($1,$2,$3,$4,'pending')

            ON CONFLICT (telegram_id)

            DO UPDATE SET
                full_name =
                    EXCLUDED.full_name,

                phone =
                    EXCLUDED.phone,

                vehicle =
                    EXCLUDED.vehicle,

                status =
                    'pending'
            """,
            message.from_user.id,
            data["full_name"],
            data["phone"],
            data["vehicle"],
        )

    await state.clear()

    await message.answer(
        "✅ Заявка отправлена."
    )

    await send_main_menu(
        message
    )


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
        return

    await message.answer(
        "🚚 ПРОФИЛЬ\n\n"

        f"👤 {courier['full_name']}\n"
        f"📞 {courier['phone']}\n"
        f"🚗 {courier['vehicle']}"
    )


# =========================================================
# ДОСТАВКИ КУРЬЕРА
# =========================================================

@dp.message(
    F.text == "📦 Мои доставки"
)
async def courier_orders(message: Message):

    courier_id = await get_approved_courier_id(
        message.from_user.id
    )

    if not courier_id:

        await message.answer(
            "❌ Курьер не найден."
        )

        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(
            """
            SELECT
                o.*,
                s.store_name

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

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
            "📦 Активных доставок нет."
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
                    text="📸 Фото товара",
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

        elif order["status"] == "delivery_photo":

            buttons = [[
                InlineKeyboardButton(
                    text="✅ Завершить доставку",
                    callback_data=(
                        f"delivered:"
                        f"{order['id']}"
                    ),
                )
            ]]

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=buttons
        )

        await message.answer(
            f"🚚 ЗАКАЗ №{order['id']}\n\n"

            f"🔢 Kittek №: "
            f"{optional_number(order['kittek_order_number'])}\n"

            f"🛒 Kaspi №: "
            f"{optional_number(order['kaspi_order_number'])}\n\n"

            f"🏪 {order['store_name']}\n"

            f"📍 Забрать: "
            f"{order['pickup_address']}\n\n"

            f"👤 {order['client_name']}\n"

            f"📞 {order['client_phone']}\n"

            f"📍 {order['delivery_address']}\n\n"

            f"📦 {order['item']}\n"

            f"🕐 {order['delivery_time']}\n"

            f"📝 {order['comment']}",

            reply_markup=keyboard,
        )


# =========================================================
# ПРИНЯЛ ЗАКАЗ
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

    courier_id = await get_approved_courier_id(
        callback.from_user.id
    )

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
            "Недоступно.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Заказ №{order_id} принят."
    )

    text = await build_status_text(
        order_id,
        "✅ КУРЬЕР ПРИНЯЛ ЗАКАЗ",
    )

    await send_store_text(
        order["store_id"],
        "status",
        text,
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

    await state.update_data(
        order_id=order_id
    )

    await state.set_state(
        CourierPhoto.pickup_photo
    )

    await callback.message.answer(
        "📸 Отправьте фото товара."
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

    courier_id = await get_approved_courier_id(
        message.from_user.id
    )

    file_id = (
        message.photo[-1].file_id
    )

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

            await conn.execute(
                """
                INSERT INTO order_photos (
                    order_id,
                    courier_id,
                    photo_type,
                    file_id
                )

                VALUES ($1,$2,'pickup',$3)
                """,
                order_id,
                courier_id,
                file_id,
            )

            await conn.execute(
                """
                UPDATE orders

                SET
                    status = 'pickup_photo',
                    updated_at = NOW()

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
                "Фото получения товара",
            )

    await state.clear()

    await message.answer(
        "✅ Фото сохранено."
    )

    text = await build_status_text(
        order_id,
        "📸 ФОТООТЧЁТ — ПОЛУЧЕНИЕ",
    )

    await send_store_photo(
        order["store_id"],
        file_id,
        text,
    )


@dp.message(
    CourierPhoto.pickup_photo
)
async def pickup_photo_wrong(
    message: Message
):

    await message.answer(
        "📸 Отправьте фотографию."
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

    courier_id = await get_approved_courier_id(
        callback.from_user.id
    )

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            UPDATE orders

            SET
                status = 'picked_up',
                updated_at = NOW()

            WHERE id = $1
              AND courier_id = $2
              AND status = 'pickup_photo'

            RETURNING store_id
            """,
            order_id,
            courier_id,
        )

    if not order:

        await callback.answer(
            "Недоступно.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    text = await build_status_text(
        order_id,
        "📦 ТОВАР ЗАБРАН",
    )

    await send_store_text(
        order["store_id"],
        "status",
        text,
    )

    await callback.message.answer(
        "📦 Товар забран."
    )

    await callback.answer()


# =========================================================
# ВЫЕХАЛ
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

    courier_id = await get_approved_courier_id(
        callback.from_user.id
    )

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            UPDATE orders

            SET
                status = 'on_the_way',
                updated_at = NOW()

            WHERE id = $1
              AND courier_id = $2
              AND status = 'picked_up'

            RETURNING store_id
            """,
            order_id,
            courier_id,
        )

    if not order:
        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    text = await build_status_text(
        order_id,
        "🚗 КУРЬЕР ВЫЕХАЛ К КЛИЕНТУ",
    )

    await send_store_text(
        order["store_id"],
        "status",
        text,
    )

    await callback.message.answer(
        "🚗 Вы выехали."
    )

    await callback.answer()


# =========================================================
# ПРИЕХАЛ
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

    courier_id = await get_approved_courier_id(
        callback.from_user.id
    )

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            UPDATE orders

            SET
                status = 'arrived',
                updated_at = NOW()

            WHERE id = $1
              AND courier_id = $2
              AND status = 'on_the_way'

            RETURNING store_id
            """,
            order_id,
            courier_id,
        )

    if not order:
        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    text = await build_status_text(
        order_id,
        "📍 КУРЬЕР ПРИБЫЛ",
    )

    await send_store_text(
        order["store_id"],
        "status",
        text,
    )

    await callback.message.answer(
        "📍 Вы прибыли."
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

    await state.update_data(
        order_id=order_id
    )

    await state.set_state(
        CourierPhoto.delivery_photo
    )

    await callback.message.answer(
        "📸 Отправьте фото доставки."
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

    courier_id = await get_approved_courier_id(
        message.from_user.id
    )

    file_id = (
        message.photo[-1].file_id
    )

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

            await conn.execute(
                """
                INSERT INTO order_photos (
                    order_id,
                    courier_id,
                    photo_type,
                    file_id
                )

                VALUES ($1,$2,'delivery',$3)
                """,
                order_id,
                courier_id,
                file_id,
            )

            await conn.execute(
                """
                UPDATE orders

                SET
                    status = 'delivery_photo',
                    updated_at = NOW()

                WHERE id = $1
                """,
                order_id,
            )

    await state.clear()

    await message.answer(
        "✅ Фото доставки сохранено."
    )

    text = await build_status_text(
        order_id,
        "📸 ФОТООТЧЁТ — ДОСТАВКА",
    )

    await send_store_photo(
        order["store_id"],
        file_id,
        text,
    )


@dp.message(
    CourierPhoto.delivery_photo
)
async def delivery_photo_wrong(
    message: Message
):

    await message.answer(
        "📸 Отправьте фотографию."
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

    courier_id = await get_approved_courier_id(
        callback.from_user.id
    )

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            UPDATE orders

            SET
                status = 'delivered',
                updated_at = NOW()

            WHERE id = $1
              AND courier_id = $2
              AND status = 'delivery_photo'

            RETURNING
                store_id,
                delivery_price
            """,
            order_id,
            courier_id,
        )

    if not order:

        await callback.answer(
            "Недоступно.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        "✅ Доставка завершена."
    )

    text = await build_status_text(
        order_id,
        "✅ ДОСТАВКА ЗАВЕРШЕНА",
    )

    await send_store_text(
        order["store_id"],
        "status",
        text,
    )

    await callback.answer()


# =========================================================
# АДМИН
# =========================================================

@dp.message(
    F.text == "👨‍💼 Администратор"
)
async def admin_home(
    message: Message
):

    if await deny_admin_message(
        message
    ):
        return

    await message.answer(
        "👨‍💼 АДМИН-ПАНЕЛЬ",
        reply_markup=admin_keyboard,
    )


@dp.message(
    F.text == "📦 Новые заказы"
)
async def admin_new_orders(
    message: Message
):

    if await deny_admin_message(
        message
    ):
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

            ORDER BY o.id ASC
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
            "📦 Новых заказов нет."
        )

        return

    for order in orders:

        buttons = []

        for courier in couriers:

            buttons.append([
                InlineKeyboardButton(
                    text=(
                        f"🚚 {courier['full_name']} "
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
                text="❌ Отменить заказ",
                callback_data=(
                    f"cancel_order_admin:"
                    f"{order['id']}"
                ),
            )
        ])

        await message.answer(
            build_order_text(
                order,
                title=(
                    f"🆕 ЗАКАЗ №"
                    f"{order['id']}"
                ),
            ),

            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=buttons
            ),
        )


# =========================================================
# НАЗНАЧИТЬ КУРЬЕРА
# =========================================================

@dp.callback_query(
    F.data.startswith("assign:")
)
async def assign_order(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
        return

    parts = callback.data.split(":")

    order_id = int(parts[1])
    courier_id = int(parts[2])

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

            await add_history(
                conn,
                order_id,
                "assigned",
                "admin",
                callback.from_user.id,
                (
                    f"Назначен курьер: "
                    f"{courier['full_name']}"
                ),
            )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Заказ №{order_id} "
        f"назначен курьеру "
        f"{courier['full_name']}."
    )

    order_full = await get_order_full(
        order_id
    )

    try:

        await bot.send_message(
            courier["telegram_id"],
            build_order_text(
                order_full,
                title=(
                    f"🚚 ВАМ НАЗНАЧЕН "
                    f"ЗАКАЗ №{order_id}"
                ),
            ),
        )

    except Exception:
        pass

    text = await build_status_text(
        order_id,
        (
            f"🚚 НАЗНАЧЕН КУРЬЕР: "
            f"{courier['full_name']}"
        ),
    )

    await send_store_text(
        order["store_id"],
        "status",
        text,
    )

    await callback.answer()


# =========================================================
# АКТИВНЫЕ
# =========================================================

@dp.message(
    F.text == "🚚 Активные"
)
async def admin_active_orders(
    message: Message
):

    if await deny_admin_message(
        message
    ):
        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(
            """
            SELECT
                o.*,

                s.store_name,

                c.full_name AS courier_name,

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
            "🚚 Активных заказов нет."
        )

        return

    for order in orders:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Сменить курьера",

                        callback_data=(
                            f"reassign_order:"
                            f"{order['id']}"
                        ),
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="❌ Отменить заказ",

                        callback_data=(
                            f"cancel_order_admin:"
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
# ДОСТАВЛЕННЫЕ
# =========================================================

@dp.message(
    F.text == "✅ Доставленные"
)
async def admin_delivered(
    message: Message
):

    if await deny_admin_message(
        message
    ):
        return

    async with db_pool.acquire() as conn:

        orders = await conn.fetch(
            """
            SELECT
                o.*,

                s.store_name,

                c.full_name AS courier_name,

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
            "✅ Доставленных нет."
        )

        return

    for order in orders:

        await message.answer(
            build_order_text(
                order,
                title=(
                    f"✅ ЗАКАЗ №"
                    f"{order['id']}"
                ),
            )
        )


# =========================================================
# ОТМЕНА ЗАКАЗА
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "cancel_order_admin:"
    )
)
async def cancel_order_admin(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
        return

    order_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            UPDATE orders

            SET
                status = 'cancelled',
                updated_at = NOW()

            WHERE id = $1
              AND status NOT IN (
                  'delivered',
                  'cancelled'
              )

            RETURNING
                store_id,
                courier_id
            """,
            order_id,
        )

    if not order:

        await callback.answer(
            "Нельзя отменить.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    text = await build_status_text(
        order_id,
        "❌ ЗАКАЗ ОТМЕНЁН",
    )

    await send_store_text(
        order["store_id"],
        "status",
        text,
    )

    await callback.message.answer(
        f"❌ Заказ №{order_id} отменён."
    )

    await callback.answer()


# =========================================================
# ПЕРЕНАЗНАЧЕНИЕ
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "reassign_order:"
    )
)
async def reassign_order(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
        return

    order_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            SELECT *

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

    buttons = []

    for courier in couriers:

        if courier["id"] == order["courier_id"]:
            continue

        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"🚚 {courier['full_name']} "
                    f"({courier['vehicle']})"
                ),

                callback_data=(
                    f"confirm_reassign:"
                    f"{order_id}:"
                    f"{courier['id']}"
                ),
            )
        ])

    await callback.message.answer(
        f"🔄 Заказ №{order_id}\n\n"
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

    if await deny_admin_callback(
        callback
    ):
        return

    parts = callback.data.split(":")

    order_id = int(parts[1])
    courier_id = int(parts[2])

    async with db_pool.acquire() as conn:

        courier = await conn.fetchrow(
            """
            SELECT *

            FROM couriers

            WHERE id = $1
              AND status = 'approved'
            """,
            courier_id,
        )

        order = await conn.fetchrow(
            """
            UPDATE orders

            SET
                courier_id = $1,
                status = 'assigned',
                updated_at = NOW()

            WHERE id = $2
              AND status NOT IN (
                  'delivered',
                  'cancelled'
              )

            RETURNING *
            """,
            courier_id,
            order_id,
        )

    if not order:
        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Новый курьер: "
        f"{courier['full_name']}"
    )

    order_full = await get_order_full(
        order_id
    )

    try:

        await bot.send_message(
            courier["telegram_id"],

            build_order_text(
                order_full,

                title=(
                    f"🚚 ВАМ НАЗНАЧЕН "
                    f"ЗАКАЗ №{order_id}"
                ),
            ),
        )

    except Exception:
        pass

    text = await build_status_text(
        order_id,
        (
            f"🔄 КУРЬЕР ИЗМЕНЁН: "
            f"{courier['full_name']}"
        ),
    )

    await send_store_text(
        order["store_id"],
        "status",
        text,
    )

    await callback.answer()


# =========================================================
# МАГАЗИНЫ
# =========================================================

@dp.message(
    F.text == "🏪 Магазины"
)
async def admin_stores(
    message: Message
):

    if await deny_admin_message(
        message
    ):
        return

    async with db_pool.acquire() as conn:

        stores = await conn.fetch(
            """
            SELECT *

            FROM stores

            ORDER BY id DESC
            """
        )

    if not stores:

        await message.answer(
            "🏪 Магазинов нет."
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
                            f"approve_store:"
                            f"{store['id']}"
                        ),
                    ),

                    InlineKeyboardButton(
                        text="❌ Отклонить",

                        callback_data=(
                            f"reject_store:"
                            f"{store['id']}"
                        ),
                    ),
                ]]
            )

        await message.answer(
            f"🏪 {store['store_name']}\n\n"
            f"Статус: {store['status']}\n"
            f"📞 {store['phone']}\n"
            f"📍 {store['address']}",

            reply_markup=keyboard,
        )


@dp.callback_query(
    F.data.startswith("approve_store:")
)
async def approve_store(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
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

            RETURNING
                telegram_id,
                store_name
            """,
            store_id,
        )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    if store:

        try:

            await bot.send_message(
                store["telegram_id"],

                f"✅ Магазин "
                f"{store['store_name']} одобрен."
            )

        except Exception:
            pass

    await callback.answer()


@dp.callback_query(
    F.data.startswith("reject_store:")
)
async def reject_store(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
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

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.answer()


# =========================================================
# КУРЬЕРЫ
# =========================================================

@dp.message(
    F.text == "🚚 Курьеры"
)
async def admin_couriers(
    message: Message
):

    if await deny_admin_message(
        message
    ):
        return

    async with db_pool.acquire() as conn:

        couriers = await conn.fetch(
            """
            SELECT *

            FROM couriers

            ORDER BY id DESC
            """
        )

    if not couriers:

        await message.answer(
            "🚚 Курьеров нет."
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
                            f"approve_courier:"
                            f"{courier['id']}"
                        ),
                    ),

                    InlineKeyboardButton(
                        text="❌ Отклонить",

                        callback_data=(
                            f"reject_courier:"
                            f"{courier['id']}"
                        ),
                    ),
                ]]
            )

        await message.answer(
            f"🚚 {courier['full_name']}\n\n"

            f"Статус: "
            f"{courier['status']}\n"

            f"📞 {courier['phone']}\n"
            f"🚗 {courier['vehicle']}",

            reply_markup=keyboard,
        )


@dp.callback_query(
    F.data.startswith("approve_courier:")
)
async def approve_courier(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
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
                "✅ Ваша заявка одобрена."
            )

        except Exception:
            pass

    await callback.answer()


@dp.callback_query(
    F.data.startswith("reject_courier:")
)
async def reject_courier(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
        return

    courier_id = int(
        callback.data.split(":")[1]
    )

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE couriers

            SET status = 'rejected'

            WHERE id = $1
            """,
            courier_id,
        )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.answer()


# =========================================================
# ОТМЕНА
# =========================================================

@dp.message(
    F.text == "❌ Отмена"
)
async def cancel_handler(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await send_main_menu(
        message
    )


# =========================================================
# FALLBACK
# =========================================================

@dp.message()
async def fallback(
    message: Message
):

    # В группах молчим
    if message.chat.type in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:
        return

    await message.answer(
        "Пожалуйста, используйте кнопки меню."
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
        else "NOT SET"
    )

    print(
        "Bot is starting..."
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":

    asyncio.run(
        main()
    )
