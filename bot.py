import os
import asyncio
import secrets
import string
import re
from urllib.parse import quote_plus
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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

# Второй администратор.
# Первый администратор по-прежнему берётся из ADMIN_ID в Railway.
SECOND_ADMIN_ID = 451626217

ADMIN_IDS = {
    admin_id
    for admin_id in (
        ADMIN_ID,
        SECOND_ADMIN_ID,
    )
    if admin_id
}

LOCAL_TZ = ZoneInfo("Asia/Almaty")


if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")


bot = Bot(token=TOKEN)

dp = Dispatcher(
    storage=MemoryStorage()
)

db_pool = None


# =========================================================
# СТАТУСЫ
# =========================================================

STATUS_NAMES = {
    "new": "🆕 Новый",
    "postponed": "🗓 Перенесён",
    "problem": "⚠️ Проблема",
    "assigned": "🚚 Назначен курьер",
    "accepted": "✅ Курьер принял",
    "pickup_photo": "📸 Фото получения",
    "picked_up": "📦 Товар забран",
    "on_the_way": "🚗 В пути",
    "arrived": "📍 Курьер прибыл",
    "kaspi_code": "🔐 Код Kaspi получен",
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

    # НОВОЕ:
    # Этап загрузки документов
    documents = State()

    confirm = State()
    schedule_time = State()



class QuickOrderCreation(StatesGroup):
    input_text = State()
    confirm = State()
    edit_value = State()

class OrderEdit(StatesGroup):
    value = State()


class CourierPhoto(StatesGroup):
    pickup_photo = State()
    delivery_photo = State()
    kaspi_code = State()


class AdminSearch(StatesGroup):
    order_id = State()


class AdminPrice(StatesGroup):
    value = State()


class OrderReschedule(StatesGroup):
    value = State()


class CourierProblem(StatesGroup):
    details = State()


# =========================================================
# КЛАВИАТУРЫ
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_keyboard(role, user_id: int):

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
                text="⚡ Быстрый заказ"
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
            ),
            KeyboardButton(
                text="🕒 Отправить позже"
            ),
        ],
        [
            KeyboardButton(
                text="❌ Отменить заказ"
            )
        ],
    ],
    resize_keyboard=True,
)


skip_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="⏭ Пропустить"
            )
        ],
    ],
    resize_keyboard=True,
)


# =========================================================
# НОВОЕ — КНОПКА ДЛЯ ДОКУМЕНТОВ
# =========================================================

documents_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(
                text="✅ Готово"
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
            KeyboardButton(
                text="🧾 Очереди"
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
            )
        ],
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

                status TEXT NOT NULL
                    DEFAULT 'new',

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

        # =================================================
        # НОВОЕ — ДОКУМЕНТЫ ЗАКАЗА
        # =================================================

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_documents (
                id SERIAL PRIMARY KEY,

                order_id INTEGER NOT NULL
                    REFERENCES orders(id)
                    ON DELETE CASCADE,

                file_id TEXT NOT NULL,

                file_name TEXT,

                mime_type TEXT,

                file_size BIGINT,

                created_at TIMESTAMPTZ
                    NOT NULL DEFAULT NOW()
            )
            """
        )

        # =================================================
        # ОТЛОЖЕННЫЕ ЗАКАЗЫ
        # =================================================

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_orders (
                id SERIAL PRIMARY KEY,

                store_id INTEGER NOT NULL
                    REFERENCES stores(id)
                    ON DELETE CASCADE,

                client_name TEXT NOT NULL,
                client_phone TEXT NOT NULL,
                pickup_address TEXT NOT NULL,
                delivery_address TEXT NOT NULL,
                item TEXT NOT NULL,
                kittek_order_number TEXT,
                kaspi_order_number TEXT,
                delivery_time TEXT NOT NULL,
                comment TEXT,
                created_by_telegram_id BIGINT NOT NULL,

                scheduled_for TIMESTAMPTZ NOT NULL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                created_order_id INTEGER
                    REFERENCES orders(id)
                    ON DELETE SET NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                processed_at TIMESTAMPTZ
            )
            """
        )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_scheduled_orders_due
            ON scheduled_orders (scheduled_for)
            WHERE status = 'scheduled'
            """
        )

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_order_documents (
                id SERIAL PRIMARY KEY,

                scheduled_order_id INTEGER NOT NULL
                    REFERENCES scheduled_orders(id)
                    ON DELETE CASCADE,

                file_id TEXT NOT NULL,
                file_name TEXT,
                mime_type TEXT,
                file_size BIGINT,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        # =================================================
        # ОЧЕРЕДЬ / ПЕРЕНОС / ПРОБЛЕМЫ ЗАКАЗА
        # =================================================

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS queue_position INTEGER
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS rescheduled_for TIMESTAMPTZ
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS rescheduled_by BIGINT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS problem_reason TEXT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS problem_details TEXT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS problem_previous_status TEXT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS problem_reported_by BIGINT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS problem_reported_at TIMESTAMPTZ
            """
        )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_courier_queue
            ON orders (courier_id, queue_position)
            """
        )

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_rescheduled_due
            ON orders (rescheduled_for)
            WHERE status = 'postponed'
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

        # =================================================
        # ОТДЕЛЬНАЯ TELEGRAM-ГРУППА ДЛЯ КАЖДОГО МАГАЗИНА
        # =================================================

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

        # =================================================
        # МИГРАЦИИ
        # =================================================

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

        # =================================================
        # KASPI — КОД ПОДТВЕРЖДЕНИЯ
        # =================================================

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
            kaspi_confirmation_code TEXT
            """
        )

        # =================================================
        # ОДНО ЖИВОЕ СООБЩЕНИЕ СТАТУСОВ В ГРУППЕ
        # =================================================

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
            status_group_message_id BIGINT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
            status_group_chat_id BIGINT
            """
        )

        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS
            status_group_topic_id BIGINT
            """
        )

        # =================================================
        # УТРЕННЕЕ ВОССТАНОВЛЕНИЕ МЕНЮ
        # =================================================

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS morning_menu_deliveries (
                send_date DATE NOT NULL,
                telegram_id BIGINT NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (send_date, telegram_id)
            )
            """
        )

        # =================================================
        # ОТЧЁТЫ КУРЬЕРА — ФОТО И ВИДЕО
        # =================================================

        await conn.execute(
            """
            ALTER TABLE order_photos
            ADD COLUMN IF NOT EXISTS
            media_type TEXT
            NOT NULL DEFAULT 'photo'
            """
        )

        # =================================================
        # СТАРЫЕ ВЛАДЕЛЬЦЫ МАГАЗИНОВ
        # =================================================

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

        # =================================================
        # ИСТОРИЯ СТАРЫХ ЗАКАЗОВ
        # =================================================

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

def price_text(value) -> str:

    try:
        value = Decimal(
            value or 0
        )

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


def optional_number(value):

    if value is None:
        return "—"

    value = str(
        value
    ).strip()

    if not value:
        return "—"

    return value


async def get_store_membership(
    user_id: int
):

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


async def get_courier(
    user_id: int
):

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


async def get_user_role(
    user_id: int
):

    store = await get_store_membership(
        user_id
    )

    courier = await get_courier(
        user_id
    )

    if (
        store
        and store["status"] == "approved"
    ):
        return "store", store

    if (
        courier
        and courier["status"] == "approved"
    ):
        return "courier", courier

    if (
        store
        and store["status"] == "pending"
    ):
        return "store", store

    if (
        courier
        and courier["status"] == "pending"
    ):
        return "courier", courier

    if store:
        return "store", store

    if courier:
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

                c.full_name
                    AS courier_name,

                c.phone
                    AS courier_phone,

                c.vehicle
                    AS courier_vehicle,

                su.full_name
                    AS created_by

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
    title=None
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

    extra_text = ""

    if (
        "queue_position" in order
        and order["queue_position"]
        and order["status"] in QUEUE_ACTIVE_STATUSES
    ):
        extra_text += (
            f"\n📋 Очередь курьера: "
            f"№{order['queue_position']}"
        )

    if (
        "rescheduled_for" in order
        and order["status"] == "postponed"
        and order["rescheduled_for"]
    ):
        local_time = order["rescheduled_for"].astimezone(LOCAL_TZ)
        extra_text += (
            f"\n🗓 Повторная публикация: "
            f"{local_time.strftime('%d.%m.%Y %H:%M')}"
        )

    if (
        "problem_reason" in order
        and order["status"] == "problem"
    ):
        extra_text += (
            f"\n⚠️ Проблема: "
            f"{order['problem_reason'] or 'Не указана'}"
        )

    return (
        f"{title}\n\n"

        f"Статус: {status}\n"

        f"💰 Стоимость: "
        f"{price_text(order['delivery_price'])}\n\n"

        f"🔢 Kittek №: "
        f"{optional_number(order['kittek_order_number'])}\n"

        f"🛒 Kaspi №: "
        f"{optional_number(order['kaspi_order_number'])}\n\n"

        f"🏪 Магазин: "
        f"{order['store_name']}\n"

        f"👤 Создал: "
        f"{author}\n"

        f"📍 Забрать: "
        f"{order['pickup_address']}\n\n"

        f"👤 Клиент: "
        f"{order['client_name']}\n"

        f"📞 "
        f"{order['client_phone']}\n"

        f"📍 Доставить: "
        f"{order['delivery_address']}\n\n"

        f"📦 "
        f"{order['item']}\n"

        f"🕐 "
        f"{order['delivery_time']}\n"

        f"📝 "
        f"{order['comment']}\n\n"

        f"🚚 Курьер: "
        f"{courier_name}"
        f"{extra_text}"
    )


def parse_scheduled_datetime(value: str):
    """Parse manager-entered local date/time and return aware Asia/Almaty datetime."""

    text = (value or "").strip()
    now = datetime.now(LOCAL_TZ)

    formats = [
        "%d.%m.%Y %H:%M",
        "%d.%m.%y %H:%M",
        "%d.%m %H:%M",
        "%H:%M",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue

        if fmt == "%d.%m %H:%M":
            parsed = parsed.replace(year=now.year)
            aware = parsed.replace(tzinfo=LOCAL_TZ)
            if aware <= now:
                aware = aware.replace(year=now.year + 1)
            return aware

        if fmt == "%H:%M":
            aware = now.replace(
                hour=parsed.hour,
                minute=parsed.minute,
                second=0,
                microsecond=0,
            )
            if aware <= now:
                aware += timedelta(days=1)
            return aware

        return parsed.replace(tzinfo=LOCAL_TZ)

    return None


# =========================================================
# ОЧЕРЕДЬ КУРЬЕРА / ПЕРЕНОСЫ
# =========================================================

QUEUE_ACTIVE_STATUSES = (
    "assigned",
    "accepted",
    "pickup_photo",
    "picked_up",
    "on_the_way",
    "arrived",
    "kaspi_code",
    "delivery_photo",
)


async def get_next_queue_position(conn, courier_id: int) -> int:
    value = await conn.fetchval(
        """
        SELECT COALESCE(MAX(queue_position), 0) + 1
        FROM orders
        WHERE courier_id = $1
          AND status = ANY($2::text[])
        """,
        courier_id,
        list(QUEUE_ACTIVE_STATUSES),
    )
    return int(value or 1)


async def normalize_courier_queue(conn, courier_id: int):
    if not courier_id:
        return

    rows = await conn.fetch(
        """
        SELECT id
        FROM orders
        WHERE courier_id = $1
          AND status = ANY($2::text[])
        ORDER BY
            COALESCE(queue_position, 2147483647),
            created_at,
            id
        """,
        courier_id,
        list(QUEUE_ACTIVE_STATUSES),
    )

    for position, row in enumerate(rows, start=1):
        await conn.execute(
            """
            UPDATE orders
            SET queue_position = $1
            WHERE id = $2
            """,
            position,
            row["id"],
        )


async def get_order_queue_info(order_id: int):
    async with db_pool.acquire() as conn:
        order = await conn.fetchrow(
            """
            SELECT
                o.id,
                o.courier_id,
                o.queue_position,
                o.status,
                c.full_name AS courier_name
            FROM orders o
            LEFT JOIN couriers c
                ON c.id = o.courier_id
            WHERE o.id = $1
            """,
            order_id,
        )

        if not order or not order["courier_id"]:
            return {
                "courier_name": None,
                "position": None,
                "ahead": 0,
                "total": 0,
            }

        if order["status"] not in QUEUE_ACTIVE_STATUSES:
            return {
                "courier_name": order["courier_name"],
                "position": None,
                "ahead": 0,
                "total": 0,
            }

        await normalize_courier_queue(
            conn,
            order["courier_id"],
        )

        refreshed = await conn.fetchrow(
            """
            SELECT queue_position
            FROM orders
            WHERE id = $1
            """,
            order_id,
        )

        position = refreshed["queue_position"]

        total = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE courier_id = $1
              AND status = ANY($2::text[])
            """,
            order["courier_id"],
            list(QUEUE_ACTIVE_STATUSES),
        )

        ahead = max((position or 1) - 1, 0)

        return {
            "courier_name": order["courier_name"],
            "position": position,
            "ahead": ahead,
            "total": int(total or 0),
        }


async def postpone_existing_order(
    order_id: int,
    scheduled_for,
    actor_type: str,
    actor_telegram_id: int,
    note: str,
):
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
                return None

            if order["status"] in (
                "delivered",
                "cancelled",
            ):
                return None

            old_courier_id = order["courier_id"]

            await conn.execute(
                """
                UPDATE orders
                SET
                    status = 'postponed',
                    rescheduled_for = $1,
                    rescheduled_by = $2,
                    courier_id = NULL,
                    queue_position = NULL,
                    problem_reason = NULL,
                    problem_details = NULL,
                    problem_previous_status = NULL,
                    problem_reported_by = NULL,
                    problem_reported_at = NULL,
                    updated_at = NOW()
                WHERE id = $3
                """,
                scheduled_for,
                actor_telegram_id,
                order_id,
            )

            await add_history(
                conn,
                order_id,
                "postponed",
                actor_type,
                actor_telegram_id,
                note,
            )

            if old_courier_id:
                await normalize_courier_queue(
                    conn,
                    old_courier_id,
                )

    if old_courier_id:
        await notify_courier(
            old_courier_id,
            f"🗓 Заказ №{order_id} перенесён и снят с вашей очереди.",
        )

    await notify_store_users(
        order["store_id"],
        f"🗓 Заказ №{order_id} перенесён на "
        f"{scheduled_for.strftime('%d.%m.%Y %H:%M')}.",
    )

    await update_store_status_message(
        order_id
    )

    return order



async def release_postponed_orders():
    released = []

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id
                FROM orders
                WHERE status = 'postponed'
                  AND rescheduled_for IS NOT NULL
                  AND rescheduled_for <= NOW()
                ORDER BY rescheduled_for, id
                FOR UPDATE SKIP LOCKED
                LIMIT 20
                """
            )

            for row in rows:
                updated = await conn.fetchrow(
                    """
                    UPDATE orders
                    SET
                        status = 'new',
                        rescheduled_for = NULL,
                        rescheduled_by = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                      AND status = 'postponed'
                    RETURNING id, store_id, created_by_telegram_id
                    """,
                    row["id"],
                )

                if not updated:
                    continue

                await add_history(
                    conn,
                    row["id"],
                    "new",
                    "system",
                    None,
                    "Перенесённый заказ повторно опубликован автоматически",
                )

                released.append(updated)

    for order in released:
        await publish_new_order(order["id"])

        try:
            await bot.send_message(
                order["created_by_telegram_id"],
                f"🚀 Заказ №{order['id']} повторно опубликован по расписанию.",
            )
        except Exception:
            pass

    return len(released)


async def send_order_to_admin(order_id: int):
    if not ADMIN_IDS:
        return

    order = await get_order_full(order_id)
    if not order:
        return

    documents = await get_order_documents(order_id)

    for admin_id in ADMIN_IDS:

        try:
            await bot.send_message(
                admin_id,
                build_order_text(
                    order,
                    title=f"🆕 НОВЫЙ ЗАКАЗ №{order_id}",
                ),
            )

            for document in documents:
                try:
                    await bot.send_document(
                        chat_id=admin_id,
                        document=document["file_id"],
                        caption=(
                            f"📎 Заказ №{order_id}\n"
                            f"{document['file_name'] or 'Документ'}"
                        ),
                    )
                except Exception:
                    pass

        except Exception as error:
            print(
                f"Could not send order #{order_id} "
                f"to admin {admin_id}:",
                error,
            )


async def publish_new_order(order_id: int):
    order = await get_order_full(order_id)

    if not order:
        return

    await send_store_topic_text(
        order["store_id"],
        "orders",
        build_order_text(
            order,
            title=f"🆕 НОВЫЙ ЗАКАЗ №{order_id}",
        ),
    )

    await send_order_documents_to_group(
        order_id=order_id,
        store_id=order["store_id"],
    )

    await send_order_to_admin(order_id)


async def create_scheduled_order_now(scheduled_order_id: int):
    """Atomically materialize one due scheduled order into the normal orders table."""

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            scheduled = await conn.fetchrow(
                """
                SELECT *
                FROM scheduled_orders
                WHERE id = $1
                  AND status = 'scheduled'
                  AND scheduled_for <= NOW()
                FOR UPDATE
                """,
                scheduled_order_id,
            )

            if not scheduled:
                return None

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
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                RETURNING id
                """,
                scheduled["store_id"],
                scheduled["client_name"],
                scheduled["client_phone"],
                scheduled["pickup_address"],
                scheduled["delivery_address"],
                scheduled["item"],
                scheduled["kittek_order_number"],
                scheduled["kaspi_order_number"],
                scheduled["delivery_time"],
                scheduled["comment"],
                scheduled["created_by_telegram_id"],
            )

            documents = await conn.fetch(
                """
                SELECT file_id, file_name, mime_type, file_size
                FROM scheduled_order_documents
                WHERE scheduled_order_id = $1
                ORDER BY id
                """,
                scheduled_order_id,
            )

            for document in documents:
                await conn.execute(
                    """
                    INSERT INTO order_documents (
                        order_id,
                        file_id,
                        file_name,
                        mime_type,
                        file_size
                    )
                    VALUES ($1,$2,$3,$4,$5)
                    """,
                    order_id,
                    document["file_id"],
                    document["file_name"],
                    document["mime_type"],
                    document["file_size"],
                )

            await add_history(
                conn,
                order_id,
                "new",
                "store",
                scheduled["created_by_telegram_id"],
                f"Отложенный заказ создан по расписанию #{scheduled_order_id}",
            )

            await conn.execute(
                """
                UPDATE scheduled_orders
                SET status = 'completed',
                    created_order_id = $2,
                    processed_at = NOW(),
                    last_error = NULL
                WHERE id = $1
                """,
                scheduled_order_id,
                order_id,
            )

    return (
        order_id,
        scheduled["created_by_telegram_id"],
    )


async def scheduled_orders_worker():
    """Persistent worker: due jobs survive bot restarts because they live in PostgreSQL."""

    while True:
        try:
            async with db_pool.acquire() as conn:
                due_ids = await conn.fetch(
                    """
                    SELECT id
                    FROM scheduled_orders
                    WHERE status = 'scheduled'
                      AND scheduled_for <= NOW()
                    ORDER BY scheduled_for, id
                    LIMIT 20
                    """
                )

            for row in due_ids:
                try:
                    result = await create_scheduled_order_now(row["id"])

                    if not result:
                        continue

                    order_id, creator_id = result

                    await publish_new_order(order_id)

                    try:
                        await bot.send_message(
                            creator_id,
                            f"✅ Отложенный заказ №{order_id} создан "
                            "и отправлен по расписанию.",
                        )
                    except Exception:
                        pass

                except Exception as error:
                    print(
                        f"Scheduled order #{row['id']} failed:",
                        error,
                    )

                    async with db_pool.acquire() as conn:
                        await conn.execute(
                            """
                            UPDATE scheduled_orders
                            SET attempts = attempts + 1,
                                last_error = $2
                            WHERE id = $1
                              AND status = 'scheduled'
                            """,
                            row["id"],
                            str(error)[:1000],
                        )

        except asyncio.CancelledError:
            raise
        except Exception as error:
            print("Scheduled orders worker error:", error)

        try:
            await release_postponed_orders()
        except Exception as error:
            print("Postponed orders worker error:", error)

        await asyncio.sleep(15)


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
# TELEGRAM-ГРУППЫ МАГАЗИНОВ
# =========================================================

async def get_store_report_settings(
    store_id: int
):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT
                store_id,
                group_chat_id,
                new_orders_topic_id,
                status_topic_id

            FROM store_report_settings

            WHERE store_id = $1
            """,
            store_id,
        )


async def send_store_topic_text(
    store_id: int,
    topic_type: str,
    text: str,
):

    settings = await get_store_report_settings(
        store_id
    )

    if not settings:
        return

    chat_id = settings[
        "group_chat_id"
    ]

    if topic_type == "orders":

        topic_id = settings[
            "new_orders_topic_id"
        ]

    else:

        topic_id = settings[
            "status_topic_id"
        ]

    if (
        not chat_id
        or not topic_id
    ):
        return

    try:

        await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=text,
        )

    except Exception as error:

        print(
            "STORE GROUP MESSAGE ERROR:",
            store_id,
            topic_type,
            error,
        )



# =========================================================
# ОДНО ЖИВОЕ СООБЩЕНИЕ СТАТУСОВ НА КАЖДЫЙ ЗАКАЗ
# =========================================================

def status_history_line(
    status: str,
    note,
    order,
):

    note_text = (
        str(note).strip()
        if note
        else ""
    )

    if (
        note_text
        and "разрешил продолжить" in note_text.lower()
    ):
        return "▶️ Проблема решена, доставка продолжается"

    if status == "assigned":
        if "измен" in note_text.lower():
            return f"🔄 {note_text}"
        if note_text:
            return f"🚚 {note_text}"
        return "🚚 Курьер назначен"

    if status == "accepted":
        return "✅ Курьер принял заказ"

    if status == "pickup_photo":
        return "📸🎥 Отчёт при получении сохранён"

    if status == "picked_up":
        return "📦 Курьер получил товар"

    if status == "on_the_way":
        return "🚗 Курьер выехал к клиенту"

    if status == "arrived":
        return "📍 Курьер приехал к клиенту"

    if status == "kaspi_code":
        code = (
            order["kaspi_confirmation_code"]
            or ""
        ).strip()

        if code:
            return (
                "🔐 Код Kaspi получен: "
                f"{code}"
            )

        return "🔐 Код Kaspi получен"

    if status == "delivery_photo":
        return "📸🎥 Отчёт доставки сохранён"

    if status == "delivered":
        return "✅ Заказ доставлен"

    if status == "cancelled":
        return "❌ Заказ отменён"

    if status == "postponed":
        if note_text:
            return f"🗓 {note_text}"
        return "🗓 Заказ перенесён"

    if status == "problem":
        if note_text:
            return f"⚠️ {note_text}"
        return "⚠️ Возникла проблема с заказом"

    return None



async def build_store_status_timeline(
    order_id: int,
):

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            SELECT
                o.*,
                s.store_name,
                c.full_name AS courier_name

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

            LEFT JOIN couriers c
                ON c.id = o.courier_id

            WHERE o.id = $1
            """,
            order_id,
        )

        if not order:
            return None, None

        history = await conn.fetch(
            """
            SELECT
                status,
                note,
                created_at

            FROM order_status_history

            WHERE order_id = $1

            ORDER BY
                created_at ASC,
                id ASC
            """,
            order_id,
        )

    lines = []

    for row in history:

        line = status_history_line(
            row["status"],
            row["note"],
            order,
        )

        if (
            line
            and line not in lines
        ):
            lines.append(
                line
            )

    if not lines:
        return order, None

    text = (
        f"📦 СТАТУС ЗАКАЗА №{order_id}\n\n"
        f"🔢 Kittek №: "
        f"{optional_number(order['kittek_order_number'])}\n"
        f"🛒 Kaspi №: "
        f"{optional_number(order['kaspi_order_number'])}\n"
        f"👤 Клиент: "
        f"{order['client_name']}\n"
        f"📍 {order['delivery_address']}\n\n"
        + "\n".join(lines)
    )

    return order, text


async def update_store_status_message(
    order_id: int,
):

    order, text = await build_store_status_timeline(
        order_id
    )

    if (
        not order
        or not text
    ):
        return

    settings = await get_store_report_settings(
        order["store_id"]
    )

    if not settings:
        return

    chat_id = settings[
        "group_chat_id"
    ]

    topic_id = settings[
        "status_topic_id"
    ]

    if (
        not chat_id
        or not topic_id
    ):
        return

    saved_message_id = order[
        "status_group_message_id"
    ]

    same_place = (
        saved_message_id
        and order[
            "status_group_chat_id"
        ] == chat_id
        and order[
            "status_group_topic_id"
        ] == topic_id
    )

    if same_place:

        try:

            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=saved_message_id,
                text=text,
            )

            return

        except Exception as error:

            error_text = str(
                error
            ).lower()

            if (
                "message is not modified"
                in error_text
            ):
                return

            print(
                "STATUS MESSAGE EDIT ERROR:",
                order_id,
                error,
            )

    try:

        sent = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=topic_id,
            text=text,
        )

    except Exception as error:

        print(
            "STATUS MESSAGE SEND ERROR:",
            order_id,
            error,
        )

        return

    async with db_pool.acquire() as conn:

        await conn.execute(
            """
            UPDATE orders

            SET
                status_group_message_id = $1,
                status_group_chat_id = $2,
                status_group_topic_id = $3,
                updated_at = NOW()

            WHERE id = $4
            """,
            sent.message_id,
            chat_id,
            topic_id,
            order_id,
        )

async def send_store_topic_photo(
    store_id: int,
    photo_file_id: str,
    caption: str,
):

    settings = await get_store_report_settings(
        store_id
    )

    if not settings:
        return

    chat_id = settings[
        "group_chat_id"
    ]

    topic_id = settings[
        "status_topic_id"
    ]

    if (
        not chat_id
        or not topic_id
    ):
        return

    try:

        await bot.send_photo(
            chat_id=chat_id,
            message_thread_id=topic_id,
            photo=photo_file_id,
            caption=caption,
        )

    except Exception as error:

        print(
            "STORE GROUP PHOTO ERROR:",
            store_id,
            error,
        )



async def send_store_topic_video(
    store_id: int,
    video_file_id: str,
    caption: str,
):

    settings = await get_store_report_settings(
        store_id
    )

    if not settings:
        return

    chat_id = settings[
        "group_chat_id"
    ]

    topic_id = settings[
        "status_topic_id"
    ]

    if (
        not chat_id
        or not topic_id
    ):
        return

    try:

        await bot.send_video(
            chat_id=chat_id,
            message_thread_id=topic_id,
            video=video_file_id,
            caption=caption,
        )

    except Exception as error:

        print(
            "STORE GROUP VIDEO ERROR:",
            store_id,
            error,
        )


# =========================================================
# НОВОЕ — ОТПРАВКА ДОКУМЕНТА В ТЕМУ НОВЫХ ЗАКАЗОВ
# =========================================================

async def send_store_topic_document(
    store_id: int,
    file_id: str,
    caption: str,
):

    settings = await get_store_report_settings(
        store_id
    )

    if not settings:
        return

    chat_id = settings[
        "group_chat_id"
    ]

    topic_id = settings[
        "new_orders_topic_id"
    ]

    if (
        not chat_id
        or not topic_id
    ):
        return

    try:

        await bot.send_document(
            chat_id=chat_id,
            message_thread_id=topic_id,
            document=file_id,
            caption=caption,
        )

    except Exception as error:

        print(
            "STORE GROUP DOCUMENT ERROR:",
            store_id,
            error,
        )


# =========================================================
# НОВОЕ — ПОЛУЧЕНИЕ ДОКУМЕНТОВ ЗАКАЗА ИЗ БАЗЫ
# =========================================================

async def get_order_documents(
    order_id: int
):

    async with db_pool.acquire() as conn:

        return await conn.fetch(
            """
            SELECT
                id,
                file_id,
                file_name,
                mime_type,
                file_size,
                created_at

            FROM order_documents

            WHERE order_id = $1

            ORDER BY id ASC
            """,
            order_id,
        )


# =========================================================
# НОВОЕ — ОТПРАВКА ВСЕХ ДОКУМЕНТОВ ПОСЛЕ КАРТОЧКИ
# =========================================================

async def send_order_documents_to_group(
    order_id: int,
    store_id: int,
):

    documents = await get_order_documents(
        order_id
    )

    if not documents:
        return

    total = len(documents)

    for index, document in enumerate(
        documents,
        start=1,
    ):

        file_name = (
            document["file_name"]
            or "Документ"
        )

        caption = (
            f"📎 Заказ №{order_id}\n"
            f"Документ {index}/{total}\n"
            f"📄 {file_name}"
        )

        await send_store_topic_document(
            store_id=store_id,
            file_id=document["file_id"],
            caption=caption,
        )


async def build_status_report(
    order_id: int,
    headline: str,
):

    order = await get_order_full(
        order_id
    )

    if not order:

        return (
            f"{headline}\n\n"
            f"📦 Заказ №{order_id}"
        )

    courier = (
        order["courier_name"]
        or "Не назначен"
    )

    return (
        f"{headline}\n\n"

        f"📦 Заказ №{order_id}\n"

        f"🔢 Kittek №: "
        f"{optional_number(order['kittek_order_number'])}\n"

        f"🛒 Kaspi №: "
        f"{optional_number(order['kaspi_order_number'])}\n\n"

        f"🏪 "
        f"{order['store_name']}\n"

        f"👤 Клиент: "
        f"{order['client_name']}\n"

        f"📍 "
        f"{order['delivery_address']}\n"

        f"🚚 Курьер: "
        f"{courier}"
    )



def courier_order_keyboard(
    order_id: int,
    status: str,
    has_kaspi: bool = False,
    client_phone=None,
    delivery_address=None,
    can_work: bool = True,
):

    buttons = []

    action_map = {
        "assigned": (
            "✅ Принять заказ",
            f"accept_order:{order_id}",
        ),
        "accepted": (
            "📸🎥 Отчёт при получении",
            f"pickup_photo:{order_id}",
        ),
        "pickup_photo": (
            "📦 Товар забран",
            f"picked_up:{order_id}",
        ),
        "picked_up": (
            "🚗 Выехал к клиенту",
            f"on_way:{order_id}",
        ),
        "on_the_way": (
            "📍 Я приехал",
            f"arrived:{order_id}",
        ),
        "arrived": (
            (
                "🔐 Ввести код Kaspi"
                if has_kaspi
                else "📸🎥 Отчёт доставки"
            ),
            (
                f"kaspi_code:{order_id}"
                if has_kaspi
                else f"delivery_photo:{order_id}"
            ),
        ),
        "kaspi_code": (
            "📸🎥 Отчёт доставки",
            f"delivery_photo:{order_id}",
        ),
        "delivery_photo": (
            "✅ Завершить доставку",
            f"delivered:{order_id}",
        ),
    }

    if (
        can_work
        and status in action_map
    ):

        text, callback_data = action_map[
            status
        ]

        buttons.append([
            InlineKeyboardButton(
                text=text,
                callback_data=callback_data,
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                text="⚠️ Проблема с заказом",
                callback_data=(
                    f"order_problem:{order_id}"
                ),
            )
        ])

    utility_row = []

    if client_phone:

        utility_row.append(
            InlineKeyboardButton(
                text="📞 Позвонить",
                callback_data=(
                    f"call_client:{order_id}"
                ),
            )
        )

    if delivery_address:

        route_url = (
            "https://www.google.com/maps/dir/"
            "?api=1&destination="
            + quote_plus(
                delivery_address
            )
        )

        utility_row.append(
            InlineKeyboardButton(
                text="🗺 Маршрут",
                url=route_url,
            )
        )

    if utility_row:
        buttons.append(
            utility_row
        )

    buttons.append([
        InlineKeyboardButton(
            text="📋 Вся очередь",
            callback_data="courier_full_queue",
        )
    ])

    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )




async def get_courier_order_card(
    order_id: int,
):

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            SELECT
                o.*,
                s.store_name

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

            WHERE o.id = $1
            """,
            order_id,
        )

    if not order:
        return None, None

    queue_info = await get_order_queue_info(
        order_id
    )

    queue_text = ""

    if queue_info["position"]:
        queue_text = (
            f"\n📋 Очередь: №{queue_info['position']} "
            f"из {queue_info['total']}\n"
            f"⏳ Перед заказом: {queue_info['ahead']}"
        )

    problem_text = ""

    if order["status"] == "problem":
        problem_text = (
            f"\n\n⚠️ Причина: "
            f"{order['problem_reason'] or 'Не указана'}"
        )

        if order["problem_details"]:
            problem_text += (
                f"\n📝 {order['problem_details']}"
            )

    text = (
        f"🚚 ЗАКАЗ №{order['id']}\n\n"

        f"🔢 Kittek №: "
        f"{optional_number(order['kittek_order_number'])}\n"

        f"🛒 Kaspi №: "
        f"{optional_number(order['kaspi_order_number'])}\n\n"

        f"🏪 Магазин: "
        f"{order['store_name']}\n"

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
        f"{STATUS_NAMES.get(order['status'], order['status'])}"
        f"{queue_text}"
        f"{problem_text}"
    )

    can_work = (
        bool(
            queue_info["position"]
        )
        and queue_info[
            "position"
        ] == 1
        and order[
            "status"
        ] in QUEUE_ACTIVE_STATUSES
    )

    keyboard = courier_order_keyboard(
        order_id,
        order["status"],
        bool(
            (
                order["kaspi_order_number"]
                or ""
            ).strip()
        ),
        client_phone=order[
            "client_phone"
        ],
        delivery_address=order[
            "delivery_address"
        ],
        can_work=can_work,
    )

    return text, keyboard


async def update_courier_order_card(
    chat_id: int,
    message_id: int,
    order_id: int,
):

    text, keyboard = await get_courier_order_card(
        order_id
    )

    if not text:
        return

    try:

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
        )

    except Exception as error:

        print(
            "COURIER CARD UPDATE ERROR:",
            order_id,
            error,
        )


async def send_courier_order_card(
    telegram_id: int,
    order_id: int,
):

    text, keyboard = await get_courier_order_card(
        order_id
    )

    if not text:
        return

    try:

        await bot.send_message(
            chat_id=telegram_id,
            text=text,
            reply_markup=keyboard,
        )

    except Exception as error:

        print(
            "COURIER CARD SEND ERROR:",
            order_id,
            error,
        )



# =========================================================
# ЕЖЕДНЕВНОЕ ВОССТАНОВЛЕНИЕ КЛАВИАТУРЫ
# =========================================================

async def get_morning_menu_users():

    users = {}

    async with db_pool.acquire() as conn:

        store_users = await conn.fetch(
            """
            SELECT
                su.telegram_id

            FROM store_users su

            JOIN stores s
                ON s.id = su.store_id

            WHERE s.status = 'approved'
            """
        )

        courier_users = await conn.fetch(
            """
            SELECT
                telegram_id

            FROM couriers

            WHERE status = 'approved'
            """
        )

    for row in store_users:
        users[
            int(row["telegram_id"])
        ] = "store"

    for row in courier_users:
        users.setdefault(
            int(row["telegram_id"]),
            "courier",
        )

    for admin_id in ADMIN_IDS:
        users.setdefault(
            admin_id,
            None,
        )

    return users


async def send_morning_menu_once():

    now = datetime.now(
        LOCAL_TZ
    )

    if (
        now.hour < 9
        or now.hour >= 12
    ):
        return

    send_date = now.date()

    users = await get_morning_menu_users()

    for user_id, fallback_role in users.items():

        async with db_pool.acquire() as conn:

            already_sent = await conn.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1

                    FROM morning_menu_deliveries

                    WHERE send_date = $1
                      AND telegram_id = $2
                )
                """,
                send_date,
                user_id,
            )

        if already_sent:
            continue

        try:

            role, info = await get_user_role(
                user_id
            )

            role = (
                role
                or fallback_role
            )

            if is_admin(
                user_id
            ):

                keyboard = main_keyboard(
                    role,
                    user_id,
                )

            elif (
                role == "store"
                and info
                and info[
                    "status"
                ] == "approved"
            ):

                keyboard = store_keyboard

            elif (
                role == "courier"
                and info
                and info[
                    "status"
                ] == "approved"
            ):

                keyboard = courier_keyboard

            else:
                continue

            await bot.send_message(
                user_id,
                "☀️ Доброе утро!\n\n"
                "Система доставки готова к работе.\n"
                "Ниже восстановлено актуальное меню.\n\n"
                "Если кнопки отображаются неправильно — "
                "отправьте /start.",
                reply_markup=keyboard,
            )

            async with db_pool.acquire() as conn:

                await conn.execute(
                    """
                    INSERT INTO morning_menu_deliveries (
                        send_date,
                        telegram_id
                    )

                    VALUES ($1,$2)

                    ON CONFLICT (
                        send_date,
                        telegram_id
                    )
                    DO NOTHING
                    """,
                    send_date,
                    user_id,
                )

        except Exception as error:

            print(
                "MORNING MENU ERROR:",
                user_id,
                error,
            )


async def morning_menu_worker():

    print(
        "Morning menu worker started."
    )

    while True:

        try:

            await send_morning_menu_once()

        except asyncio.CancelledError:
            raise

        except Exception as error:

            print(
                "MORNING MENU WORKER ERROR:",
                error,
            )

        await asyncio.sleep(
            60
        )

# =========================================================
# START
# =========================================================

@dp.message(
    CommandStart()
)
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


@dp.message(
    Command("myid")
)
async def myid_handler(
    message: Message
):

    await message.answer(
        "🆔 Ваш Telegram ID:\n\n"
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
# ПРИВЯЗКА TELEGRAM-ГРУППЫ
# =========================================================

@dp.message(
    Command("bindorders")
)
async def bind_orders_topic(
    message: Message
):

    if message.chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:

        await message.answer(
            "❌ Команду /bindorders "
            "нужно отправить "
            "в группе магазина."
        )

        return

    if message.sender_chat is not None:

        await message.answer(
            "❌ Вы отправляете "
            "команду анонимно.\n\n"
            "Отключите анонимность "
            "администратора и повторите."
        )

        return

    if not message.message_thread_id:

        await message.answer(
            "❌ Откройте тему "
            "для новых заказов "
            "и отправьте /bindorders "
            "внутри неё."
        )

        return

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await message.answer(
            "❌ Ваш Telegram-аккаунт "
            "не привязан к магазину."
        )

        return

    if membership["status"] != "approved":

        await message.answer(
            "❌ Магазин ещё "
            "не одобрен администратором."
        )

        return

    if membership[
        "member_role"
    ] != "owner":

        await message.answer(
            "❌ Привязать группу "
            "может только владелец магазина."
        )

        return

    async with db_pool.acquire() as conn:

        another_store = await conn.fetchrow(
            """
            SELECT
                rs.store_id,
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

        if another_store:

            await message.answer(
                "❌ Эта группа уже "
                "привязана к другому магазину:\n\n"

                f"🏪 "
                f"{another_store['store_name']}"
            )

            return

        old_settings = await conn.fetchrow(
            """
            SELECT
                group_chat_id

            FROM store_report_settings

            WHERE store_id = $1
            """,
            membership["store_id"],
        )

        changing_group = (
            old_settings
            and old_settings["group_chat_id"]
            and old_settings["group_chat_id"]
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

            VALUES (
                $1,$2,$3,NOW()
            )

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

        if changing_group:

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

        f"🏪 "
        f"{membership['store_name']}\n\n"

        "📦 Новые заказы "
        "будут приходить сюда."
    )


@dp.message(
    Command("bindstatus")
)
async def bind_status_topic(
    message: Message
):

    if message.chat.type not in {
        ChatType.GROUP,
        ChatType.SUPERGROUP,
    }:

        await message.answer(
            "❌ Команду /bindstatus "
            "нужно отправить "
            "в группе магазина."
        )

        return

    if message.sender_chat is not None:

        await message.answer(
            "❌ Вы отправляете "
            "команду анонимно.\n\n"
            "Отключите анонимность "
            "администратора."
        )

        return

    if not message.message_thread_id:

        await message.answer(
            "❌ Откройте тему "
            "для статусов и фото "
            "и отправьте /bindstatus "
            "внутри неё."
        )

        return

    membership = await get_store_membership(
        message.from_user.id
    )

    if not membership:

        await message.answer(
            "❌ Ваш аккаунт "
            "не привязан к магазину."
        )

        return

    if membership["status"] != "approved":

        await message.answer(
            "❌ Магазин ещё "
            "не одобрен."
        )

        return

    if membership[
        "member_role"
    ] != "owner":

        await message.answer(
            "❌ Привязать тему "
            "может только владелец."
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
                "❌ Сначала выполните "
                "/bindorders "
                "в теме новых заказов."
            )

            return

        if (
            settings["group_chat_id"]
            != message.chat.id
        ):

            await message.answer(
                "❌ Эта тема находится "
                "в другой группе.\n\n"
                "Обе темы должны быть "
                "в одной группе магазина."
            )

            return

        if (
            settings["new_orders_topic_id"]
            == message.message_thread_id
        ):

            await message.answer(
                "❌ Для статусов "
                "нужна отдельная тема."
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

        f"🏪 "
        f"{membership['store_name']}\n\n"

        "🚚 Статусы и фотоотчёты "
        "будут приходить сюда."
    )


@dp.message(
    Command("reportsettings")
)
async def report_settings(
    message: Message
):

    if message.sender_chat is not None:

        await message.answer(
            "❌ Отправьте команду "
            "от своего личного аккаунта."
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
            "⚙️ НАСТРОЙКИ TELEGRAM\n\n"

            f"🏪 "
            f"{membership['store_name']}\n\n"

            "💬 Группа: ❌\n"
            "📦 Новые заказы: ❌\n"
            "🚚 Статусы/фото: ❌"
        )

        return

    await message.answer(
        "⚙️ НАСТРОЙКИ TELEGRAM\n\n"

        f"🏪 "
        f"{membership['store_name']}\n\n"

        f"💬 Группа: "
        f"{'✅' if settings['group_chat_id'] else '❌'}\n"

        f"📦 Новые заказы: "
        f"{'✅' if settings['new_orders_topic_id'] else '❌'}\n"

        f"🚚 Статусы/фото: "
        f"{'✅' if settings['status_topic_id'] else '❌'}"
    )


@dp.message(
    Command("unbindgroup")
)
async def unbind_group(
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

    if membership[
        "member_role"
    ] != "owner":

        await message.answer(
            "❌ Только владелец "
            "может отвязать группу."
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
        "✅ Telegram-группа отвязана.\n\n"
        "Теперь можно заново выполнить "
        "/bindorders и /bindstatus."
    )


# =========================================================
# МАГАЗИН
# =========================================================

@dp.message(
    F.text == "🏪 Магазин"
)
async def store_section(
    message: Message,
    state: FSMContext,
):

    role, info = await get_user_role(
        message.from_user.id
    )

    if role == "courier":

        await message.answer(
            "❌ Вы зарегистрированы как курьер.\n\n"
            "Один Telegram-аккаунт "
            "может иметь только одну рабочую роль."
        )

        return

    if role == "store":

        if info["status"] == "approved":

            await message.answer(
                f"🏪 "
                f"{info['store_name']}\n\n"

                "Выберите действие:",

                reply_markup=store_keyboard,
            )

            return

        await message.answer(
            f"🏪 "
            f"{info['store_name']}\n\n"

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
        "📍 Введите адрес магазина или склада:"
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
        "⏳ Ожидайте подтверждения администратора."
    )

    await send_main_menu(
        message
    )


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
        "🔑 Введите код приглашения от магазина:"
    )


@dp.message(
    StoreJoin.invite_code
)
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
                    "❌ Этот магазин сейчас недоступен."
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
        "✅ Вы присоединились к магазину!\n\n"

        f"🏪 "
        f"{invite['store_name']}\n"

        "👤 Роль: Менеджер",

        reply_markup=store_keyboard,
    )


# =========================================================
# МЕНЕДЖЕРЫ
# =========================================================

@dp.message(
    F.text == "👥 Менеджеры"
)
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
                    WHEN member_role = 'owner'
                    THEN 0
                    ELSE 1
                END,
                id
            """,
            membership["store_id"],
        )

    text = (
        "👥 КОМАНДА МАГАЗИНА\n\n"

        f"🏪 "
        f"{membership['store_name']}\n\n"
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
        or membership["status"] != "approved"
    ):

        await callback.answer(
            "Только владелец может "
            "приглашать менеджеров.",
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

        f"🏪 "
        f"{membership['store_name']}\n\n"

        f"Код: {code}\n\n"

        "Передайте этот код менеджеру.\n\n"

        "Менеджер должен открыть:\n"
        "🏪 Магазин → "
        "🔑 Присоединиться к магазину\n\n"

        "⚠️ Код одноразовый."
    )


@dp.callback_query(
    F.data.startswith(
        "remove_manager:"
    )
)
async def remove_manager_confirm(
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
            "❌ Только владелец "
            "может удалять менеджеров.",
            show_alert=True,
        )

        return

    manager_id = int(
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
            manager_id,
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
                        f"confirm_remove_manager:"
                        f"{manager_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Отмена",
                    callback_data="cancel_remove_manager",
                )
            ],
        ]
    )

    await callback.message.answer(
        "⚠️ УДАЛЕНИЕ МЕНЕДЖЕРА\n\n"

        f"👤 "
        f"{manager['full_name']}\n\n"

        "Удалить менеджера?",

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
            SELECT
                full_name,
                member_role

            FROM store_users

            WHERE store_id = $1
              AND telegram_id = $2
            """,
            membership["store_id"],
            manager_id,
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
            manager_id,
        )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Менеджер "
        f"{manager['full_name']} удалён."
    )

    try:

        await bot.send_message(
            manager_id,

            "ℹ️ Вы больше не являетесь "
            f"менеджером магазина "
            f"{membership['store_name']}."
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
        "Удаление отменено."
    )

    await callback.answer()


# =========================================================
# ПРОФИЛЬ МАГАЗИНА
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
        if membership["member_role"] == "owner"
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

        f"🔐 Роль: "
        f"{role_name}\n"

        f"Статус: "
        f"{membership['status']}"
    )


# =========================================================
# СТАТИСТИКА МАГАЗИНА
# =========================================================

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
                        WHERE status = 'delivered'
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

        f"🏪 "
        f"{membership['store_name']}\n\n"

        f"📦 Всего: "
        f"{stats['total']}\n"

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
# БЫСТРЫЙ ЗАКАЗ ТЕКСТОМ
# =========================================================

def clean_quick_value(
    value,
):

    if value is None:
        return None

    value = str(
        value
    ).strip()

    return (
        value
        if value
        else None
    )


def parse_quick_order_text(
    text: str,
):

    raw_lines = [
        line.strip()
        for line in (
            text
            or ""
        ).splitlines()
        if line.strip()
    ]

    result = {
        "client_name": None,
        "client_phone": None,
        "delivery_address": None,
        "item": None,
        "kittek_order_number": None,
        "kaspi_order_number": None,
        "delivery_time": "-",
        "comment": None,
    }

    used = set()

    phone_pattern = re.compile(
        r"(?:(?:\+?7|8)"
        r"[\s\-\(\)]*"
        r"\d(?:[\s\-\(\)]*\d){9})"
    )

    for index, line in enumerate(
        raw_lines
    ):

        lower = line.lower()

        if (
            (
                "kittek" in lower
                or "киттек" in lower
            )
            and result[
                "kittek_order_number"
            ] is None
        ):

            value = re.sub(
                r"(?i).*?(?:kittek|киттек)"
                r"\s*(?:№|номер)?\s*[:\-]?\s*",
                "",
                line,
            ).strip()

            result[
                "kittek_order_number"
            ] = clean_quick_value(
                value
            )

            used.add(
                index
            )

            continue

        if (
            (
                "kaspi" in lower
                or "каспи" in lower
            )
            and result[
                "kaspi_order_number"
            ] is None
        ):

            value = re.sub(
                r"(?i).*?(?:kaspi|каспи)"
                r"\s*(?:№|номер)?\s*[:\-]?\s*",
                "",
                line,
            ).strip()

            result[
                "kaspi_order_number"
            ] = clean_quick_value(
                value
            )

            used.add(
                index
            )

            continue

        if lower.startswith(
            (
                "адрес:",
                "доставить:",
                "доставка:",
            )
        ):

            value = line.split(
                ":",
                1,
            )[1].strip()

            result[
                "delivery_address"
            ] = clean_quick_value(
                value
            )

            used.add(
                index
            )

            continue

        if lower.startswith(
            "время:"
        ):

            value = line.split(
                ":",
                1,
            )[1].strip()

            result[
                "delivery_time"
            ] = (
                value
                or "-"
            )

            used.add(
                index
            )

            continue

        if lower.startswith(
            (
                "комментарий:",
                "коммент:",
            )
        ):

            value = line.split(
                ":",
                1,
            )[1].strip()

            result[
                "comment"
            ] = clean_quick_value(
                value
            )

            used.add(
                index
            )

            continue

        if lower.startswith(
            (
                "товар:",
                "модель:",
            )
        ):

            value = line.split(
                ":",
                1,
            )[1].strip()

            result[
                "item"
            ] = clean_quick_value(
                value
            )

            used.add(
                index
            )

            continue

        if lower.startswith(
            "клиент:"
        ):

            value = line.split(
                ":",
                1,
            )[1].strip()

            result[
                "client_name"
            ] = clean_quick_value(
                value
            )

            used.add(
                index
            )

            continue

        phone_match = phone_pattern.search(
            line
        )

        if (
            phone_match
            and result[
                "client_phone"
            ] is None
        ):

            phone = phone_match.group(
                0
            ).strip()

            result[
                "client_phone"
            ] = phone

            name = (
                line[
                    :phone_match.start()
                ]
                + " "
                + line[
                    phone_match.end():
                ]
            ).strip(
                " ,-—;:"
            )

            if (
                name
                and result[
                    "client_name"
                ] is None
            ):

                result[
                    "client_name"
                ] = name

            used.add(
                index
            )

    for index, line in enumerate(
        raw_lines
    ):

        if index in used:
            continue

        compact = re.sub(
            r"\s+",
            "",
            line,
        )

        if (
            result[
                "kittek_order_number"
            ] is None
            and re.fullmatch(
                r"\d{3,8}",
                compact,
            )
        ):

            result[
                "kittek_order_number"
            ] = compact

            used.add(
                index
            )

            continue

        if (
            result[
                "kaspi_order_number"
            ] is None
            and re.fullmatch(
                r"\d{9,16}",
                compact,
            )
        ):

            result[
                "kaspi_order_number"
            ] = compact

            used.add(
                index
            )

            continue

    for index, line in enumerate(
        raw_lines
    ):

        if index in used:
            continue

        if (
            result[
                "item"
            ] is None
        ):

            result[
                "item"
            ] = line

            used.add(
                index
            )

            break

    remaining = [
        line
        for index, line in enumerate(
            raw_lines
        )
        if index not in used
    ]

    if (
        remaining
        and result[
            "comment"
        ] is None
    ):

        result[
            "comment"
        ] = "\n".join(
            remaining
        )

    if not result[
        "client_name"
    ]:

        result[
            "client_name"
        ] = "Не указан"

    if not result[
        "comment"
    ]:

        result[
            "comment"
        ] = "Нет"

    return result


def quick_order_preview_text(
    data: dict,
    membership,
):

    return (
        "⚡ БЫСТРЫЙ ЗАКАЗ — ПРОВЕРКА\n\n"
        f"🔢 Kittek №: "
        f"{optional_number(data.get('kittek_order_number'))}\n"
        f"🛒 Kaspi №: "
        f"{optional_number(data.get('kaspi_order_number'))}\n\n"
        f"🏪 Магазин: "
        f"{membership['store_name']}\n"
        f"👤 Создал: "
        f"{membership['full_name']}\n"
        f"📍 Забрать: "
        f"{membership['address']}\n\n"
        f"👤 Клиент: "
        f"{data.get('client_name') or '—'}\n"
        f"📞 "
        f"{data.get('client_phone') or '—'}\n"
        f"📍 Доставить: "
        f"{data.get('delivery_address') or '—'}\n\n"
        f"📦 "
        f"{data.get('item') or '—'}\n"
        f"🕐 "
        f"{data.get('delivery_time') or '-'}\n"
        f"📝 "
        f"{data.get('comment') or 'Нет'}"
    )


def quick_order_confirm_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Всё верно",
                    callback_data=(
                        "quick_order_confirm"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Исправить",
                    callback_data=(
                        "quick_order_edit_menu"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=(
                        "quick_order_cancel"
                    ),
                )
            ],
        ]
    )


@dp.message(
    F.text == "⚡ Быстрый заказ"
)
async def quick_order_start(
    message: Message,
    state: FSMContext,
):

    membership = await get_store_membership(
        message.from_user.id
    )

    if (
        not membership
        or membership[
            "status"
        ] != "approved"
    ):

        await message.answer(
            "❌ Быстрый заказ доступен "
            "только одобренному магазину."
        )

        return

    await state.clear()

    await state.set_state(
        QuickOrderCreation.input_text
    )

    await message.answer(
        "⚡ БЫСТРЫЙ ЗАКАЗ\n\n"
        "Отправьте весь заказ одним сообщением.\n\n"
        "Например:\n"
        "18607\n\n"
        "MW820DHSBK\n\n"
        "Адрес: Щепкина 42 кв 99, "
        "3 подъезд, 4 этаж\n\n"
        "+7 705 244 2975 Альмира\n\n"
        "Без кода, заранее позвонить\n\n"
        "Бот распознает поля и обязательно "
        "покажет карточку перед созданием.",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(
    QuickOrderCreation.input_text
)
async def quick_order_receive(
    message: Message,
    state: FSMContext,
):

    text = (
        message.text
        or ""
    ).strip()

    if not text:

        await message.answer(
            "❌ Отправьте заказ текстом."
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

    parsed = parse_quick_order_text(
        text
    )

    await state.update_data(
        quick_order=parsed
    )

    await state.set_state(
        QuickOrderCreation.confirm
    )

    await message.answer(
        quick_order_preview_text(
            parsed,
            membership,
        ),
        reply_markup=(
            quick_order_confirm_keyboard()
        ),
    )


@dp.callback_query(
    F.data == "quick_order_edit_menu"
)
async def quick_order_edit_menu(
    callback: CallbackQuery,
):

    fields = [
        (
            "🔢 Kittek №",
            "kittek_order_number",
        ),
        (
            "🛒 Kaspi №",
            "kaspi_order_number",
        ),
        (
            "👤 Клиент",
            "client_name",
        ),
        (
            "📞 Телефон",
            "client_phone",
        ),
        (
            "📍 Адрес",
            "delivery_address",
        ),
        (
            "📦 Товар",
            "item",
        ),
        (
            "🕐 Время",
            "delivery_time",
        ),
        (
            "📝 Комментарий",
            "comment",
        ),
    ]

    buttons = [
        [
            InlineKeyboardButton(
                text=title,
                callback_data=(
                    f"quick_order_edit:"
                    f"{field}"
                ),
            )
        ]
        for title, field in fields
    ]

    buttons.append([
        InlineKeyboardButton(
            text="↩️ Назад",
            callback_data=(
                "quick_order_preview"
            ),
        )
    ])

    await callback.message.answer(
        "✏️ Что изменить?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith(
        "quick_order_edit:"
    )
)
async def quick_order_edit_field(
    callback: CallbackQuery,
    state: FSMContext,
):

    field = callback.data.split(
        ":",
        1,
    )[1]

    allowed = {
        "kittek_order_number",
        "kaspi_order_number",
        "client_name",
        "client_phone",
        "delivery_address",
        "item",
        "delivery_time",
        "comment",
    }

    if field not in allowed:

        await callback.answer(
            "Неизвестное поле.",
            show_alert=True,
        )

        return

    await state.update_data(
        quick_edit_field=field
    )

    await state.set_state(
        QuickOrderCreation.edit_value
    )

    await callback.message.answer(
        "✏️ Введите новое значение.\n\n"
        "Для Kittek/Kaspi можно написать «-», "
        "чтобы очистить поле."
    )

    await callback.answer()


@dp.message(
    QuickOrderCreation.edit_value
)
async def quick_order_edit_value(
    message: Message,
    state: FSMContext,
):

    value = (
        message.text
        or ""
    ).strip()

    data = await state.get_data()

    field = data.get(
        "quick_edit_field"
    )

    quick_order = data.get(
        "quick_order",
        {},
    )

    if not field:

        await state.clear()

        await message.answer(
            "❌ Ошибка редактирования."
        )

        return

    if (
        field in {
            "kittek_order_number",
            "kaspi_order_number",
        }
        and value.lower()
        in {
            "-",
            "нет",
            "none",
        }
    ):

        value = None

    elif not value:

        await message.answer(
            "❌ Значение не может "
            "быть пустым."
        )

        return

    quick_order[
        field
    ] = value

    await state.update_data(
        quick_order=quick_order,
        quick_edit_field=None,
    )

    await state.set_state(
        QuickOrderCreation.confirm
    )

    membership = await get_store_membership(
        message.from_user.id
    )

    await message.answer(
        quick_order_preview_text(
            quick_order,
            membership,
        ),
        reply_markup=(
            quick_order_confirm_keyboard()
        ),
    )


@dp.callback_query(
    F.data == "quick_order_preview"
)
async def quick_order_preview(
    callback: CallbackQuery,
    state: FSMContext,
):

    data = await state.get_data()

    quick_order = data.get(
        "quick_order"
    )

    membership = await get_store_membership(
        callback.from_user.id
    )

    if (
        not quick_order
        or not membership
    ):

        await callback.answer(
            "Данные быстрого заказа "
            "не найдены.",
            show_alert=True,
        )

        return

    await state.set_state(
        QuickOrderCreation.confirm
    )

    await callback.message.answer(
        quick_order_preview_text(
            quick_order,
            membership,
        ),
        reply_markup=(
            quick_order_confirm_keyboard()
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data == "quick_order_cancel"
)
async def quick_order_cancel(
    callback: CallbackQuery,
    state: FSMContext,
):

    await state.clear()

    await callback.message.answer(
        "❌ Быстрый заказ отменён.",
        reply_markup=store_keyboard,
    )

    await callback.answer()


@dp.callback_query(
    F.data == "quick_order_confirm"
)
async def quick_order_confirm(
    callback: CallbackQuery,
    state: FSMContext,
):

    data = await state.get_data()

    quick_order = data.get(
        "quick_order"
    )

    membership = await get_store_membership(
        callback.from_user.id
    )

    if (
        not quick_order
        or not membership
        or membership[
            "status"
        ] != "approved"
    ):

        await callback.answer(
            "Данные заказа недоступны.",
            show_alert=True,
        )

        return

    missing = []

    if not quick_order.get(
        "client_phone"
    ):
        missing.append(
            "телефон клиента"
        )

    if not quick_order.get(
        "delivery_address"
    ):
        missing.append(
            "адрес доставки"
        )

    if not quick_order.get(
        "item"
    ):
        missing.append(
            "товар"
        )

    if missing:

        await callback.answer(
            "Заполните: "
            + ", ".join(
                missing
            ),
            show_alert=True,
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
                    kittek_order_number,
                    kaspi_order_number,
                    delivery_time,
                    comment,
                    created_by_telegram_id
                )

                VALUES (
                    $1,$2,$3,$4,$5,$6,
                    $7,$8,$9,$10,$11
                )

                RETURNING id
                """,
                membership[
                    "store_id"
                ],
                quick_order.get(
                    "client_name"
                ) or "Не указан",
                quick_order[
                    "client_phone"
                ],
                membership[
                    "address"
                ],
                quick_order[
                    "delivery_address"
                ],
                quick_order[
                    "item"
                ],
                quick_order.get(
                    "kittek_order_number"
                ),
                quick_order.get(
                    "kaspi_order_number"
                ),
                quick_order.get(
                    "delivery_time"
                ) or "-",
                quick_order.get(
                    "comment"
                ) or "Нет",
                callback.from_user.id,
            )

            await add_history(
                conn,
                order_id,
                "new",
                "store",
                callback.from_user.id,
                "Заказ создан быстрым вводом",
            )

    await state.clear()

    await callback.message.answer(
        f"✅ Заказ №{order_id} создан "
        "быстрым вводом.",
        reply_markup=store_keyboard,
    )

    await publish_new_order(
        order_id
    )

    await callback.answer(
        "Заказ создан."
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
            "❌ Создавать заказы может "
            "только пользователь "
            "одобренного магазина."
        )

        return

    await state.clear()

    # Сразу создаём пустой список документов
    await state.update_data(
        documents=[]
    )

    await state.set_state(
        OrderCreation.client_name
    )

    await message.answer(
        "📦 СОЗДАНИЕ ЗАКАЗА\n\n"
        "👤 Введите имя клиента:",

        reply_markup=ReplyKeyboardRemove(),
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


@dp.message(
    OrderCreation.item
)
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
        "🛒 Введите номер заказа по Kaspi.\n\n"
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
        "🕐 Укажите желаемое время доставки:",

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
        "📝 Добавьте комментарий.\n\n"
        "Если комментария нет — "
        "напишите: Нет"
    )


# =========================================================
# ИЗМЕНЕНО — ПОСЛЕ КОММЕНТАРИЯ ИДУТ ДОКУМЕНТЫ
# =========================================================

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

    await state.set_state(
        OrderCreation.documents
    )

    await message.answer(
        "📎 ДОКУМЕНТЫ К ЗАКАЗУ\n\n"

        "Отправьте нужные документы.\n"
        "Можно отправить несколько файлов — "
        "по одному или сразу несколько.\n\n"

        "Когда все документы будут добавлены, "
        "нажмите «✅ Готово».\n\n"

        "Если документов нет — "
        "просто нажмите «✅ Готово».",

        reply_markup=documents_keyboard,
    )


# =========================================================
# НОВОЕ — ПРИНИМАЕМ ДОКУМЕНТЫ
# =========================================================

@dp.message(
    OrderCreation.documents,
    F.document,
)
async def order_document_received(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    documents = data.get(
        "documents",
        [],
    )

    document = message.document

    documents.append({
        "file_id": document.file_id,
        "file_name": (
            document.file_name
            or "document"
        ),
        "mime_type": document.mime_type,
        "file_size": document.file_size,
    })

    await state.update_data(
        documents=documents
    )

    await message.answer(
        f"✅ Файл добавлен.\n\n"
        f"📎 Документов: {len(documents)}\n\n"
        "Отправьте следующий файл "
        "или нажмите «✅ Готово».",

        reply_markup=documents_keyboard,
    )


# =========================================================
# НОВОЕ — НАЖАЛИ "ГОТОВО"
# =========================================================

@dp.message(
    OrderCreation.documents,
    F.text == "✅ Готово",
)
async def order_documents_done(
    message: Message,
    state: FSMContext,
):

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

    documents = data.get(
        "documents",
        [],
    )

    await state.set_state(
        OrderCreation.confirm
    )

    documents_text = (
        f"📎 Документов: {len(documents)}"
        if documents
        else "📎 Документы: нет"
    )

    await message.answer(
        "📦 ПРОВЕРЬТЕ ЗАКАЗ\n\n"

        f"🏪 Магазин: "
        f"{membership['store_name']}\n"

        f"👤 Создал: "
        f"{membership['full_name']}\n"

        f"📍 Забрать: "
        f"{membership['address']}\n\n"

        f"🔢 Kittek №: "
        f"{optional_number(data.get('kittek_order_number'))}\n"

        f"🛒 Kaspi №: "
        f"{optional_number(data.get('kaspi_order_number'))}\n\n"

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

        f"{documents_text}\n\n"

        "Создать заказ?",

        reply_markup=order_confirm_keyboard,
    )


# =========================================================
# НОВОЕ — ЕСЛИ НА ЭТАПЕ ДОКУМЕНТОВ ПРИШЛО НЕ ТО
# =========================================================

@dp.message(
    OrderCreation.documents
)
async def order_document_wrong(
    message: Message
):

    await message.answer(
        "📎 На этом этапе отправьте файл "
        "как документ.\n\n"
        "Когда закончите — "
        "нажмите «✅ Готово».",

        reply_markup=documents_keyboard,
    )


# =========================================================
# ИЗМЕНЕНО — СОЗДАНИЕ ЗАКАЗА + СОХРАНЕНИЕ ДОКУМЕНТОВ
# =========================================================

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
        or membership["status"] != "approved"
    ):

        await state.clear()

        await message.answer(
            "❌ Магазин недоступен."
        )

        return

    documents = data.get(
        "documents",
        [],
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
                    $1,$2,$3,$4,$5,$6,
                    $7,$8,$9,$10,$11
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

            # =============================================
            # СОХРАНЯЕМ ВСЕ ДОКУМЕНТЫ В БАЗУ
            # =============================================

            for document in documents:

                await conn.execute(
                    """
                    INSERT INTO order_documents (
                        order_id,
                        file_id,
                        file_name,
                        mime_type,
                        file_size
                    )

                    VALUES (
                        $1,$2,$3,$4,$5
                    )
                    """,
                    order_id,
                    document["file_id"],
                    document.get(
                        "file_name"
                    ),
                    document.get(
                        "mime_type"
                    ),
                    document.get(
                        "file_size"
                    ),
                )

            await add_history(
                conn,
                order_id,
                "new",
                "store",
                message.from_user.id,
                (
                    "Заказ создан"
                    if not documents
                    else (
                        f"Заказ создан. "
                        f"Документов: "
                        f"{len(documents)}"
                    )
                ),
            )

    await state.clear()

    await message.answer(
        f"✅ Заказ №{order_id} создан!\n\n"
        f"📎 Документов: {len(documents)}\n"
        "Статус: 🆕 Новый",

        reply_markup=store_keyboard,
    )

    # ЕДИНАЯ ПУБЛИКАЦИЯ: ТЕМА МАГАЗИНА + ДОКУМЕНТЫ + АДМИН
    await publish_new_order(order_id)


@dp.message(
    OrderCreation.confirm,
    F.text == "🕒 Отправить позже",
)
async def order_schedule_start(
    message: Message,
    state: FSMContext,
):
    await state.set_state(OrderCreation.schedule_time)

    now = datetime.now(LOCAL_TZ)

    await message.answer(
        "🕒 ОТЛОЖЕННЫЙ ЗАКАЗ\n\n"
        "Укажите дату и время, когда заказ должен быть создан "
        "и отправлен в группу и администратору.\n\n"
        "Формат: ДД.ММ.ГГГГ ЧЧ:ММ\n"
        "Например: 20.08.2026 14:30\n\n"
        f"Сейчас: {now.strftime('%d.%m.%Y %H:%M')}",
        reply_markup=ReplyKeyboardRemove(),
    )


@dp.message(OrderCreation.schedule_time)
async def order_schedule_save(
    message: Message,
    state: FSMContext,
):
    scheduled_for = parse_scheduled_datetime(message.text)
    now = datetime.now(LOCAL_TZ)

    if not scheduled_for:
        await message.answer(
            "❌ Не понял дату и время.\n\n"
            "Введите так: 20.08.2026 14:30"
        )
        return

    if scheduled_for <= now:
        await message.answer(
            "❌ Время должно быть в будущем.\n\n"
            "Введите новую дату и время, например: "
            "20.08.2026 14:30"
        )
        return

    data = await state.get_data()

    membership = await get_store_membership(message.from_user.id)

    if (
        not membership
        or membership["status"] != "approved"
    ):
        await state.clear()
        await message.answer(
            "❌ Магазин недоступен.",
            reply_markup=store_keyboard,
        )
        return

    documents = data.get("documents", [])

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            scheduled_id = await conn.fetchval(
                """
                INSERT INTO scheduled_orders (
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
                    created_by_telegram_id,
                    scheduled_for
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                RETURNING id
                """,
                membership["store_id"],
                data["client_name"],
                data["client_phone"],
                membership["address"],
                data["delivery_address"],
                data["item"],
                data.get("kittek_order_number"),
                data.get("kaspi_order_number"),
                data["delivery_time"],
                data["comment"],
                message.from_user.id,
                scheduled_for,
            )

            for document in documents:
                await conn.execute(
                    """
                    INSERT INTO scheduled_order_documents (
                        scheduled_order_id,
                        file_id,
                        file_name,
                        mime_type,
                        file_size
                    )
                    VALUES ($1,$2,$3,$4,$5)
                    """,
                    scheduled_id,
                    document["file_id"],
                    document.get("file_name"),
                    document.get("mime_type"),
                    document.get("file_size"),
                )

    await state.clear()

    await message.answer(
        f"✅ Отложенный заказ сохранён.\n\n"
        f"🗓 Будет создан: "
        f"{scheduled_for.strftime('%d.%m.%Y %H:%M')}\n"
        f"📎 Документов: {len(documents)}\n\n"
        "До этого времени он не попадёт в обычные заказы "
        "и не будет отправлен в группу.\n"
        "В указанное время бот создаст обычный заказ "
        "и отправит его автоматически.",
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
# МОИ ЗАКАЗЫ
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
                o.*,

                su.full_name
                    AS created_by,

                c.full_name
                    AS courier_name,

                (
                    SELECT COUNT(*)
                    FROM order_documents od
                    WHERE od.order_id = o.id
                ) AS documents_count

            FROM orders o

            LEFT JOIN store_users su
                ON su.telegram_id =
                   o.created_by_telegram_id

            LEFT JOIN couriers c
                ON c.id = o.courier_id

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
                        f"edit_order:"
                        f"{order['id']}"
                    ),
                )
            ])

        # Перенос / перевыпуск доступен именно создателю заказа.
        if (
            order["created_by_telegram_id"]
            == message.from_user.id
            and order["status"] in (
                "new",
                "postponed",
            )
        ):

            buttons.append([
                InlineKeyboardButton(
                    text="🗓 Перенести заказ",
                    callback_data=(
                        f"store_reschedule:"
                        f"{order['id']}"
                    ),
                )
            ])

        if (
            order["created_by_telegram_id"]
            == message.from_user.id
            and order["status"] == "postponed"
        ):

            buttons.append([
                InlineKeyboardButton(
                    text="🚀 Перевыпустить сейчас",
                    callback_data=(
                        f"store_release_now:"
                        f"{order['id']}"
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

        queue_info = await get_order_queue_info(
            order["id"]
        )

        courier_text = (
            order["courier_name"]
            or "Не назначен"
        )

        queue_text = ""

        if queue_info["position"]:

            queue_text = (
                f"\n📋 Место в очереди: "
                f"№{queue_info['position']} "
                f"из {queue_info['total']}\n"
                f"⏳ Активных заказов перед вашим: "
                f"{queue_info['ahead']}"
            )

        postponed_text = ""

        if (
            order["status"] == "postponed"
            and order["rescheduled_for"]
        ):

            local_time = order[
                "rescheduled_for"
            ].astimezone(
                LOCAL_TZ
            )

            postponed_text = (
                f"\n🗓 Повторная публикация: "
                f"{local_time.strftime('%d.%m.%Y %H:%M')}"
            )

        problem_text = ""

        if order["status"] == "problem":

            problem_text = (
                f"\n⚠️ Проблема: "
                f"{order['problem_reason'] or 'Не указана'}"
            )

            if order["problem_details"]:

                problem_text += (
                    f"\n📝 "
                    f"{order['problem_details']}"
                )

        await message.answer(
            f"📦 ЗАКАЗ №"
            f"{order['id']}\n\n"

            f"Статус: "
            f"{STATUS_NAMES.get(order['status'], order['status'])}\n"

            f"💰 Стоимость: "
            f"{price_text(order['delivery_price'])}\n\n"

            f"🔢 Kittek №: "
            f"{optional_number(order['kittek_order_number'])}\n"

            f"🛒 Kaspi №: "
            f"{optional_number(order['kaspi_order_number'])}\n\n"

            f"📎 Документов: "
            f"{order['documents_count']}\n\n"

            f"👤 Создал: "
            f"{author}\n"

            f"👤 Клиент: "
            f"{order['client_name']}\n"

            f"📞 "
            f"{order['client_phone']}\n"

            f"📍 "
            f"{order['delivery_address']}\n"

            f"📦 "
            f"{order['item']}\n"

            f"🕐 "
            f"{order['delivery_time']}\n"

            f"📝 "
            f"{order['comment']}\n\n"

            f"🚚 Курьер: "
            f"{courier_text}"
            f"{queue_text}"
            f"{postponed_text}"
            f"{problem_text}",

            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=buttons
            ),
        )


# =========================================================
# ИСТОРИЯ МАГАЗИНА
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "store_history:"
    )
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

            ORDER BY
                created_at ASC,
                id ASC
            """,
            order_id,
        )

    text = (
        f"🕐 ИСТОРИЯ ЗАКАЗА №"
        f"{order_id}\n\n"
    )

    for row in history:

        time_text = (
            row["created_at"]
            .strftime(
                "%d.%m.%Y %H:%M"
            )
        )

        text += (
            f"{STATUS_NAMES.get(row['status'], row['status'])}\n"
            f"🕐 {time_text}"
        )

        if row["note"]:

            text += (
                f"\n📝 "
                f"{row['note']}"
            )

        text += "\n\n"

    await callback.message.answer(
        text
    )

    await callback.answer()


# =========================================================
# РЕДАКТИРОВАНИЕ ЗАКАЗА
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "edit_order:"
    )
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
            "❌ После назначения курьера "
            "заказ редактировать нельзя.",
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
                        f"{order_id}:client_name"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📞 Телефон",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:client_phone"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📍 Адрес доставки",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:delivery_address"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Товар",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:item"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Kittek №",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:"
                        f"kittek_order_number"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🛒 Kaspi №",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:"
                        f"kaspi_order_number"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🕐 Время",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:delivery_time"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📝 Комментарий",
                    callback_data=(
                        f"edit_field:"
                        f"{order_id}:comment"
                    ),
                )
            ],
        ]
    )

    await callback.message.answer(
        f"✏️ РЕДАКТИРОВАНИЕ "
        f"ЗАКАЗА №{order_id}\n\n"

        f"🔢 Kittek №: "
        f"{optional_number(order['kittek_order_number'])}\n"

        f"🛒 Kaspi №: "
        f"{optional_number(order['kaspi_order_number'])}\n\n"

        f"👤 "
        f"{order['client_name']}\n"

        f"📞 "
        f"{order['client_phone']}\n"

        f"📍 "
        f"{order['delivery_address']}\n"

        f"📦 "
        f"{order['item']}\n"

        f"🕐 "
        f"{order['delivery_time']}\n"

        f"📝 "
        f"{order['comment']}\n\n"

        "Что хотите изменить?",

        reply_markup=keyboard,
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith(
        "edit_field:"
    )
)
async def edit_order_field(
    callback: CallbackQuery,
    state: FSMContext,
):

    parts = callback.data.split(":")

    order_id = int(
        parts[1]
    )

    field = parts[2]

    field_names = {
        "client_name": "имя клиента",
        "client_phone": "номер телефона",
        "delivery_address": "адрес доставки",
        "item": "товар",
        "kittek_order_number": "номер Kittek",
        "kaspi_order_number": "номер Kaspi",
        "delivery_time": "время доставки",
        "comment": "комментарий",
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

    extra = ""

    if field in {
        "kittek_order_number",
        "kaspi_order_number",
    }:

        extra = (
            "\n\nЕсли номера нет, "
            "напишите: -"
        )

    await callback.message.answer(
        f"✏️ Заказ №{order_id}\n\n"

        f"Введите новое "
        f"{field_names[field]}:"
        f"{extra}"
    )

    await callback.answer()


@dp.message(
    OrderEdit.value
)
async def save_order_edit(
    message: Message,
    state: FSMContext,
):

    raw_value = (
        message.text or ""
    ).strip()

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
        "kittek_order_number",
        "kaspi_order_number",
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

    value = raw_value

    if field in {
        "kittek_order_number",
        "kaspi_order_number",
    }:

        if raw_value.lower() in {
            "-",
            "нет",
            "none",
            "пропустить",
        }:

            value = None

    else:

        if not raw_value:

            await message.answer(
                "❌ Значение "
                "не может быть пустым."
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

                RETURNING
                    id,
                    store_id
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
            "❌ Заказ уже назначен курьеру. "
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

@dp.message(
    F.text == "🚚 Курьер"
)
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
        "✅ Заявка курьера отправлена!\n\n"
        "⏳ Ожидайте подтверждения администратора."
    )

    await send_main_menu(
        message
    )


# =========================================================
# ПРОФИЛЬ КУРЬЕРА
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

        f"👤 "
        f"{courier['full_name']}\n"

        f"📞 "
        f"{courier['phone']}\n"

        f"🚗 "
        f"{courier['vehicle']}\n"

        f"Статус: "
        f"{courier['status']}"
    )


# =========================================================
# СТАТИСТИКА КУРЬЕРА
# =========================================================

@dp.message(
    F.text == "📊 Моя статистика"
)
async def courier_statistics(
    message: Message
):

    courier_id = await get_approved_courier_id(
        message.from_user.id
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
                        WHERE status = 'delivered'
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
# МОИ ДОСТАВКИ
# =========================================================


@dp.message(
    F.text == "📦 Мои доставки"
)
async def courier_orders(
    message: Message
):

    courier_id = await get_approved_courier_id(
        message.from_user.id
    )

    if not courier_id:

        await message.answer(
            "❌ Вы не зарегистрированы "
            "как одобренный курьер."
        )

        return

    async with db_pool.acquire() as conn:

        await normalize_courier_queue(
            conn,
            courier_id,
        )

        next_order = await conn.fetchrow(
            """
            SELECT
                id,
                queue_position

            FROM orders

            WHERE courier_id = $1
              AND status = ANY($2::text[])

            ORDER BY
                queue_position,
                id

            LIMIT 1
            """,
            courier_id,
            list(
                QUEUE_ACTIVE_STATUSES
            ),
        )

        total_active = await conn.fetchval(
            """
            SELECT COUNT(*)

            FROM orders

            WHERE courier_id = $1
              AND status = ANY($2::text[])
            """,
            courier_id,
            list(
                QUEUE_ACTIVE_STATUSES
            ),
        )

        problem_count = await conn.fetchval(
            """
            SELECT COUNT(*)

            FROM orders

            WHERE courier_id = $1
              AND status = 'problem'
            """,
            courier_id,
        )

    if not next_order:

        if problem_count:

            await message.answer(
                "⚠️ В рабочей очереди сейчас "
                "нет следующей доставки.\n\n"
                f"Проблемных заказов: "
                f"{problem_count}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📋 Вся очередь",
                                callback_data=(
                                    "courier_full_queue"
                                ),
                            )
                        ]
                    ]
                ),
            )

        else:

            await message.answer(
                "📦 У вас пока нет "
                "активных доставок."
            )

        return

    await message.answer(
        "🟢 СЛЕДУЮЩАЯ ДОСТАВКА\n\n"
        f"Это заказ №1 из "
        f"{int(total_active or 0)} "
        "в вашей текущей очереди.\n"
        f"После него останется: "
        f"{max(int(total_active or 0) - 1, 0)}"
    )

    await send_courier_order_card(
        message.from_user.id,
        next_order["id"],
    )




# =========================================================
# КУРЬЕР — ЗВОНОК / ВСЯ ОЧЕРЕДЬ / ПРОСМОТР ЗАКАЗА
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "call_client:"
    )
)
async def courier_call_client(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = await get_approved_courier_id(
        callback.from_user.id
    )

    if not courier_id:

        await callback.answer(
            "Курьер не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            SELECT
                client_name,
                client_phone

            FROM orders

            WHERE id = $1
              AND courier_id = $2
              AND status NOT IN (
                    'delivered',
                    'cancelled',
                    'postponed'
                  )
            """,
            order_id,
            courier_id,
        )

    if not order:

        await callback.answer(
            "Заказ недоступен.",
            show_alert=True,
        )

        return

    await callback.message.answer(
        "📞 ТЕЛЕФОН КЛИЕНТА\n\n"
        f"👤 {order['client_name']}\n"
        f"📞 {order['client_phone']}\n\n"
        "Нажмите на номер телефона в Telegram — "
        "на iPhone откроется обычный телефонный звонок."
    )

    await callback.answer()


@dp.callback_query(
    F.data == "courier_full_queue"
)
async def courier_full_queue(
    callback: CallbackQuery
):

    courier_id = await get_approved_courier_id(
        callback.from_user.id
    )

    if not courier_id:

        await callback.answer(
            "Курьер не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        await normalize_courier_queue(
            conn,
            courier_id,
        )

        orders = await conn.fetch(
            """
            SELECT
                o.id,
                o.queue_position,
                o.status,
                o.client_name,
                o.delivery_address,
                o.delivery_time

            FROM orders o

            WHERE o.courier_id = $1
              AND (
                    o.status = ANY($2::text[])
                    OR o.status = 'problem'
                  )

            ORDER BY
                CASE
                    WHEN o.status = 'problem'
                    THEN 1
                    ELSE 0
                END,
                COALESCE(
                    o.queue_position,
                    2147483647
                ),
                o.id
            """,
            courier_id,
            list(
                QUEUE_ACTIVE_STATUSES
            ),
        )

    if not orders:

        await callback.answer(
            "Активных заказов нет.",
            show_alert=True,
        )

        return

    text = (
        "📋 ВСЯ ОЧЕРЕДЬ\n\n"
        "Нажмите на нужный заказ, "
        "чтобы посмотреть подробности.\n\n"
    )

    buttons = []

    for order in orders:

        if order[
            "status"
        ] == "problem":

            prefix = "⚠️"
            position_text = (
                "Проблема"
            )

        else:

            prefix = (
                "🟢"
                if order[
                    "queue_position"
                ] == 1
                else "⏳"
            )

            position_text = (
                f"№{order['queue_position']}"
            )

        text += (
            f"{prefix} {position_text} — "
            f"заказ №{order['id']}\n"
            f"👤 {order['client_name']}\n"
            f"📍 {order['delivery_address']}\n"
            f"🕐 {order['delivery_time']}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"{prefix} Заказ "
                    f"№{order['id']}"
                ),
                callback_data=(
                    f"courier_queue_order:"
                    f"{order['id']}"
                ),
            )
        ])

    await callback.message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith(
        "courier_queue_order:"
    )
)
async def courier_queue_order_details(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = await get_approved_courier_id(
        callback.from_user.id
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
                  AND status NOT IN (
                        'delivered',
                        'cancelled',
                        'postponed'
                      )
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

    await send_courier_order_card(
        callback.from_user.id,
        order_id,
    )

    await callback.answer()

# =========================================================
# ПРИНЯТЬ ЗАКАЗ
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "accept_order:"
    )
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

    await update_courier_order_card(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        order_id=order_id,
    )


    await update_store_status_message(
        order_id
    )

    await callback.answer()



# =========================================================
# ФОТО ПОЛУЧЕНИЯ
# =========================================================


@dp.callback_query(
    F.data.startswith(
        "pickup_photo:"
    )
)
async def pickup_photo_request(
    callback: CallbackQuery,
    state: FSMContext,
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = await get_approved_courier_id(
        callback.from_user.id
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
        order_id=order_id,
        report_count=0,
        courier_card_chat_id=(
            callback.message.chat.id
        ),
        courier_card_message_id=(
            callback.message.message_id
        ),
    )

    await state.set_state(
        CourierPhoto.pickup_photo
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Готово",
                    callback_data=(
                        f"pickup_report_done:"
                        f"{order_id}"
                    ),
                )
            ]
        ]
    )

    await callback.message.answer(
        f"📸🎥 ОТЧЁТ ПРИ ПОЛУЧЕНИИ\n\n"
        f"Заказ №{order_id}\n\n"
        "Отправьте фото и/или видео.\n"
        "Можно отправить несколько файлов подряд.\n\n"
        "Когда закончите — нажмите "
        "«✅ Готово».",
        reply_markup=keyboard,
    )

    await callback.answer()




@dp.message(
    CourierPhoto.pickup_photo,
    F.photo | F.video,
)
async def pickup_photo_received(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    order_id = data[
        "order_id"
    ]

    courier_id = await get_approved_courier_id(
        message.from_user.id
    )

    if not courier_id:

        await state.clear()

        return

    if message.photo:

        media_type = "photo"

        file_id = (
            message.photo[-1].file_id
        )

    else:

        media_type = "video"

        file_id = (
            message.video.file_id
        )

    async with db_pool.acquire() as conn:

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
                file_id,
                media_type
            )

            VALUES (
                $1,$2,'pickup',$3,$4
            )
            """,
            order_id,
            courier_id,
            file_id,
            media_type,
        )

    count = (
        int(
            data.get(
                "report_count",
                0,
            )
        )
        + 1
    )

    await state.update_data(
        report_count=count
    )

    caption = (
        f"📦 Заказ №{order_id}\n"
        f"Отчёт при получении — "
        f"файл {count}"
    )

    if media_type == "photo":

        await send_store_topic_photo(
            order["store_id"],
            file_id,
            caption,
        )

    else:

        await send_store_topic_video(
            order["store_id"],
            file_id,
            caption,
        )

    await message.answer(
        f"✅ Добавлено файлов: "
        f"{count}\n\n"
        "Можно отправить ещё фото/видео "
        "или нажать «✅ Готово»."
    )





@dp.callback_query(
    F.data.startswith(
        "pickup_report_done:"
    )
)
async def pickup_report_done(
    callback: CallbackQuery,
    state: FSMContext,
):

    order_id = int(
        callback.data.split(":")[1]
    )

    data = await state.get_data()

    if (
        data.get("order_id")
        != order_id
    ):

        await callback.answer(
            "Этот отчёт уже неактивен.",
            show_alert=True,
        )

        return

    report_count = int(
        data.get(
            "report_count",
            0,
        )
    )

    if report_count < 1:

        await callback.answer(
            "Сначала отправьте хотя бы "
            "одно фото или видео.",
            show_alert=True,
        )

        return

    courier_id = await get_approved_courier_id(
        callback.from_user.id
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
                    status = 'pickup_photo',
                    updated_at = NOW()

                WHERE id = $1
                  AND courier_id = $2
                  AND status = 'accepted'

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
                    "pickup_photo",
                    "courier",
                    callback.from_user.id,
                    (
                        "Отчёт при получении: "
                        f"{report_count} "
                        "фото/видео"
                    ),
                )

    if not order:

        await callback.answer(
            "Заказ недоступен.",
            show_alert=True,
        )

        return

    await state.clear()

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Отчёт заказа №"
        f"{order_id} сохранён."
    )

    await send_courier_order_card(
        callback.from_user.id,
        order_id,
    )


    await update_store_status_message(
        order_id
    )

    await callback.answer(
        "Отчёт сохранён."
    )


@dp.message(
    CourierPhoto.pickup_photo
)
async def pickup_photo_wrong(
    message: Message
):

    await message.answer(
        "📸🎥 Отправьте фотографию "
        "или видео.\n\n"
        "Когда закончите — нажмите "
        "«✅ Готово»."
    )



# =========================================================
# ТОВАР ЗАБРАН
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "picked_up:"
    )
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
                  AND status = 'pickup_photo'

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

    await update_courier_order_card(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        order_id=order_id,
    )

    await callback.answer(
        "📦 Товар забран."
    )


    await update_store_status_message(
        order_id
    )

    await callback.answer()



# =========================================================
# В ПУТИ
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "on_way:"
    )
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

    if not courier_id:
        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    status = 'on_the_way',
                    updated_at = NOW()

                WHERE id = $1
                  AND courier_id = $2
                  AND status = 'picked_up'

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
    await update_courier_order_card(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        order_id=order_id,
    )

    await callback.answer(
        "🚗 Вы выехали к клиенту."
    )


    await update_store_status_message(
        order_id
    )

    await callback.answer()



# =========================================================
# ПРИЕХАЛ
# =========================================================


@dp.callback_query(
    F.data.startswith(
        "arrived:"
    )
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
                  AND status = 'on_the_way'

                RETURNING
                    id,
                    store_id,
                    created_by_telegram_id,
                    kaspi_order_number
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

    await update_courier_order_card(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        order_id=order_id,
    )


    kaspi_number = (
        order["kaspi_order_number"]
        or ""
    ).strip()

    if (
        kaspi_number
        and order[
            "created_by_telegram_id"
        ]
    ):

        try:

            await bot.send_message(
                order[
                    "created_by_telegram_id"
                ],
                "🚨🚨🚨 "
                "КУРЬЕР ПРИЕХАЛ К КЛИЕНТУ "
                "🚨🚨🚨\n\n"
                f"📦 Заказ №{order_id}\n"
                f"🛒 Kaspi №: "
                f"{kaspi_number}\n\n"
                "🔐 ОТПРАВЬТЕ КУРЬЕРУ "
                "КОД ПОДТВЕРЖДЕНИЯ KASPI."
            )

        except Exception as error:

            print(
                "CREATOR ARRIVAL NOTIFY ERROR:",
                order_id,
                error,
            )

    await update_store_status_message(
        order_id
    )

    await callback.answer(
        "📍 Вы прибыли к клиенту."
    )




# =========================================================
# КОД ПОДТВЕРЖДЕНИЯ KASPI
# =========================================================


@dp.callback_query(
    F.data.startswith(
        "kaspi_code:"
    )
)
async def kaspi_code_request(
    callback: CallbackQuery,
    state: FSMContext,
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = await get_approved_courier_id(
        callback.from_user.id
    )

    if not courier_id:

        await callback.answer(
            "Курьер не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            SELECT
                id,
                kaspi_order_number

            FROM orders

            WHERE id = $1
              AND courier_id = $2
              AND status = 'arrived'
            """,
            order_id,
            courier_id,
        )

    if not order:

        await callback.answer(
            "Заказ недоступен.",
            show_alert=True,
        )

        return

    kaspi_number = (
        order["kaspi_order_number"]
        or ""
    ).strip()

    if not kaspi_number:

        await callback.answer(
            "Код Kaspi для этого "
            "заказа не требуется.",
            show_alert=True,
        )

        return

    await state.update_data(
        order_id=order_id,
        courier_card_chat_id=(
            callback.message.chat.id
        ),
        courier_card_message_id=(
            callback.message.message_id
        ),
    )

    await state.set_state(
        CourierPhoto.kaspi_code
    )

    await callback.message.answer(
        "🔐 КОД ПОДТВЕРЖДЕНИЯ KASPI\n\n"
        f"📦 Заказ №{order_id}\n"
        f"🛒 Kaspi №: "
        f"{kaspi_number}\n\n"
        "Введите код подтверждения клиента:"
    )

    await callback.answer()


@dp.message(
    CourierPhoto.kaspi_code
)
async def kaspi_code_received(
    message: Message,
    state: FSMContext,
):

    code = (
        message.text
        or ""
    ).strip()

    if not code:

        await message.answer(
            "❌ Введите код "
            "подтверждения текстом."
        )

        return

    data = await state.get_data()

    order_id = data[
        "order_id"
    ]

    courier_id = await get_approved_courier_id(
        message.from_user.id
    )

    if not courier_id:

        await state.clear()

        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    kaspi_confirmation_code = $1,
                    status = 'kaspi_code',
                    updated_at = NOW()

                WHERE id = $2
                  AND courier_id = $3
                  AND status = 'arrived'
                  AND COALESCE(
                        TRIM(
                            kaspi_order_number
                        ),
                        ''
                      ) <> ''

                RETURNING
                    id,
                    store_id,
                    created_by_telegram_id,
                    kaspi_order_number
                """,
                code,
                order_id,
                courier_id,
            )

            if order:

                courier_name = await conn.fetchval(
                    """
                    SELECT full_name

                    FROM couriers

                    WHERE id = $1
                    """,
                    courier_id,
                )

                await add_history(
                    conn,
                    order_id,
                    "kaspi_code",
                    "courier",
                    message.from_user.id,
                    (
                        "Курьер ввёл код "
                        "подтверждения Kaspi"
                    ),
                )

    if not order:

        await state.clear()

        await message.answer(
            "❌ Заказ недоступен."
        )

        return

    await state.clear()

    await message.answer(
        f"✅ Код Kaspi для заказа "
        f"№{order_id} сохранён."
    )

    await send_courier_order_card(
        message.from_user.id,
        order_id,
    )

    notice = (
        "🚨 KASPI — КОД ПОДТВЕРЖДЕНИЯ 🚨\n\n"
        f"📦 Заказ №{order_id}\n"
        f"🛒 Kaspi №: "
        f"{order['kaspi_order_number']}\n"
        f"🚚 Курьер: "
        f"{courier_name or '-'}\n\n"
        f"🔐 КОД: {code}\n\n"
        "✅ Код получен курьером."
    )

    creator_id = order[
        "created_by_telegram_id"
    ]

    if creator_id:

        try:

            await bot.send_message(
                creator_id,
                notice,
            )

        except Exception as error:

            print(
                "CREATOR KASPI CODE "
                "NOTIFY ERROR:",
                order_id,
                error,
            )

    await update_store_status_message(
        order_id
    )


# =========================================================
# ФОТО / ВИДЕО ДОСТАВКИ
# =========================================================


@dp.callback_query(
    F.data.startswith(
        "delivery_photo:"
    )
)
async def delivery_photo_request(
    callback: CallbackQuery,
    state: FSMContext,
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = await get_approved_courier_id(
        callback.from_user.id
    )

    if not courier_id:

        await callback.answer(
            "Курьер не найден.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            SELECT
                id,
                kaspi_order_number,
                kaspi_confirmation_code,
                status

            FROM orders

            WHERE id = $1
              AND courier_id = $2
              AND status IN (
                    'arrived',
                    'kaspi_code'
                  )
            """,
            order_id,
            courier_id,
        )

    if not order:

        await callback.answer(
            "Заказ недоступен.",
            show_alert=True,
        )

        return

    has_kaspi = bool(
        (
            order[
                "kaspi_order_number"
            ]
            or ""
        ).strip()
    )

    if (
        has_kaspi
        and order["status"]
        != "kaspi_code"
    ):

        await callback.answer(
            "Для Kaspi-заказа сначала "
            "введите код подтверждения.",
            show_alert=True,
        )

        return

    await state.update_data(
        order_id=order_id,
        report_count=0,
        courier_card_chat_id=(
            callback.message.chat.id
        ),
        courier_card_message_id=(
            callback.message.message_id
        ),
    )

    await state.set_state(
        CourierPhoto.delivery_photo
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Готово",
                    callback_data=(
                        f"delivery_report_done:"
                        f"{order_id}"
                    ),
                )
            ]
        ]
    )

    await callback.message.answer(
        f"📸🎥 ОТЧЁТ ДОСТАВКИ\n\n"
        f"Заказ №{order_id}\n\n"
        "Отправьте фото и/или видео.\n"
        "Можно отправить несколько файлов подряд.\n\n"
        "Когда закончите — нажмите "
        "«✅ Готово».",
        reply_markup=keyboard,
    )

    await callback.answer()




@dp.message(
    CourierPhoto.delivery_photo,
    F.photo | F.video,
)
async def delivery_photo_received(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    order_id = data[
        "order_id"
    ]

    courier_id = await get_approved_courier_id(
        message.from_user.id
    )

    if not courier_id:

        await state.clear()

        return

    if message.photo:

        media_type = "photo"

        file_id = (
            message.photo[-1].file_id
        )

    else:

        media_type = "video"

        file_id = (
            message.video.file_id
        )

    async with db_pool.acquire() as conn:

        order = await conn.fetchrow(
            """
            SELECT
                id,
                store_id,
                kaspi_order_number,
                status

            FROM orders

            WHERE id = $1
              AND courier_id = $2
              AND status IN (
                    'arrived',
                    'kaspi_code'
                  )
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

        has_kaspi = bool(
            (
                order[
                    "kaspi_order_number"
                ]
                or ""
            ).strip()
        )

        if (
            has_kaspi
            and order["status"]
            != "kaspi_code"
        ):

            await state.clear()

            await message.answer(
                "❌ Для Kaspi-заказа "
                "сначала введите код "
                "подтверждения."
            )

            return

        await conn.execute(
            """
            INSERT INTO order_photos (
                order_id,
                courier_id,
                photo_type,
                file_id,
                media_type
            )

            VALUES (
                $1,$2,'delivery',$3,$4
            )
            """,
            order_id,
            courier_id,
            file_id,
            media_type,
        )

    count = (
        int(
            data.get(
                "report_count",
                0,
            )
        )
        + 1
    )

    await state.update_data(
        report_count=count
    )

    caption = (
        f"✅ Заказ №{order_id}\n"
        f"Отчёт доставки — "
        f"файл {count}"
    )

    if media_type == "photo":

        await send_store_topic_photo(
            order["store_id"],
            file_id,
            caption,
        )

    else:

        await send_store_topic_video(
            order["store_id"],
            file_id,
            caption,
        )

    await message.answer(
        f"✅ Добавлено файлов: "
        f"{count}\n\n"
        "Можно отправить ещё фото/видео "
        "или нажать «✅ Готово»."
    )





@dp.callback_query(
    F.data.startswith(
        "delivery_report_done:"
    )
)
async def delivery_report_done(
    callback: CallbackQuery,
    state: FSMContext,
):

    order_id = int(
        callback.data.split(":")[1]
    )

    data = await state.get_data()

    if (
        data.get("order_id")
        != order_id
    ):

        await callback.answer(
            "Этот отчёт уже неактивен.",
            show_alert=True,
        )

        return

    report_count = int(
        data.get(
            "report_count",
            0,
        )
    )

    if report_count < 1:

        await callback.answer(
            "Сначала отправьте хотя бы "
            "одно фото или видео.",
            show_alert=True,
        )

        return

    courier_id = await get_approved_courier_id(
        callback.from_user.id
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
                    status = 'delivery_photo',
                    updated_at = NOW()

                WHERE id = $1
                  AND courier_id = $2
                  AND status IN (
                        'arrived',
                        'kaspi_code'
                      )
                  AND (
                        COALESCE(
                            TRIM(
                                kaspi_order_number
                            ),
                            ''
                        ) = ''
                        OR status = 'kaspi_code'
                      )

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
                    "delivery_photo",
                    "courier",
                    callback.from_user.id,
                    (
                        "Отчёт доставки: "
                        f"{report_count} "
                        "фото/видео"
                    ),
                )

    if not order:

        await callback.answer(
            "Для Kaspi-заказа сначала "
            "введите код подтверждения.",
            show_alert=True,
        )

        return

    await state.clear()

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Отчёт доставки заказа "
        f"№{order_id} сохранён."
    )

    await send_courier_order_card(
        callback.from_user.id,
        order_id,
    )


    await update_store_status_message(
        order_id
    )

    await callback.answer(
        "Отчёт сохранён."
    )


@dp.message(
    CourierPhoto.delivery_photo
)
async def delivery_photo_wrong(
    message: Message
):

    await message.answer(
        "📸🎥 Отправьте фотографию "
        "или видео.\n\n"
        "Когда закончите — нажмите "
        "«✅ Готово»."
    )



# =========================================================
# ДОСТАВЛЕНО
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "delivered:"
    )
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
                  AND status = 'delivery_photo'

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

    if order:
        async with db_pool.acquire() as conn:
            await normalize_courier_queue(
                conn,
                courier_id,
            )

    if not order:

        await callback.answer(
            "Не удалось завершить доставку.",
            show_alert=True,
        )

        return

    await update_courier_order_card(
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        order_id=order_id,
    )

    await callback.answer(
        "✅ Доставка завершена."
    )

    await callback.message.answer(
        "🎉 Доставка завершена.\n\n"
        "Чтобы продолжить работу, откройте "
        "«📦 Мои доставки».",
        reply_markup=courier_keyboard,
    )


    await update_store_status_message(
        order_id
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

    if await deny_admin_message(
        message
    ):
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
                        'postponed',
                        'delivered',
                        'cancelled'
                    )
                ) AS active_count,

                COUNT(*) FILTER (
                    WHERE status = 'delivered'
                ) AS delivered_count,

                COUNT(*) FILTER (
                    WHERE status = 'cancelled'
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

@dp.message(
    F.text == "📊 Статистика"
)
async def admin_statistics(
    message: Message
):

    if await deny_admin_message(
        message
    ):
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
                    WHERE status = 'postponed'
                ) AS postponed_count,

                COUNT(*) FILTER (
                    WHERE status NOT IN (
                        'new',
                        'postponed',
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
                        WHERE status = 'delivered'
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

        f"🗓 Перенесённых: "
        f"{orders['postponed_count']}\n"

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

                su.full_name
                    AS created_by

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
                c.id,
                c.full_name,
                c.vehicle,
                (
                    SELECT COUNT(*)
                    FROM orders q
                    WHERE q.courier_id = c.id
                      AND q.status = ANY($1::text[])
                ) AS active_count

            FROM couriers c

            WHERE c.status = 'approved'

            ORDER BY
                active_count ASC,
                c.full_name
            """,
            list(QUEUE_ACTIVE_STATUSES),
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
                        f"🚚 "
                        f"{courier['full_name']} "
                        f"({courier['vehicle']}) — "
                        f"{courier['active_count']} в очереди"
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
                    f"set_price:"
                    f"{order['id']}"
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
                text="🗓 Перенести",
                callback_data=(
                    f"admin_reschedule:"
                    f"{order['id']}"
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
# АДМИН — НАЗНАЧЕНИЕ
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "assign:"
    )
)
async def assign_order(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
        return

    parts = callback.data.split(":")

    order_id = int(
        parts[1]
    )

    courier_id = int(
        parts[2]
    )

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

            await normalize_courier_queue(
                conn,
                courier_id,
            )

            order = await conn.fetchrow(
                """
                UPDATE orders

                SET
                    courier_id = $1,
                    status = 'assigned',
                    queue_position = (
                        SELECT COALESCE(MAX(q.queue_position), 0) + 1
                        FROM orders q
                        WHERE q.courier_id = $1
                          AND q.status = ANY($3::text[])
                    ),
                    updated_at = NOW()

                WHERE id = $2
                  AND status = 'new'

                RETURNING *
                """,
                courier_id,
                order_id,
                list(QUEUE_ACTIVE_STATUSES),
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

            await normalize_courier_queue(
                conn,
                courier_id,
            )

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

    await send_courier_order_card(
        courier["telegram_id"],
        order_id,
    )


    await update_store_status_message(
        order_id
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

                c.full_name
                    AS courier_name,

                c.phone
                    AS courier_phone,

                c.vehicle
                    AS courier_vehicle,

                su.full_name
                    AS created_by

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
                'postponed',
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
                            f"reassign_order:"
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
                            f"admin_history:"
                            f"{order['id']}"
                        ),
                    ),

                    InlineKeyboardButton(
                        text="📸 Фото",
                        callback_data=(
                            f"admin_photos:"
                            f"{order['id']}"
                        ),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🗓 Перенести",
                        callback_data=(
                            f"admin_reschedule:"
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

        if order["status"] == "problem":

            keyboard.inline_keyboard.insert(
                0,
                [
                    InlineKeyboardButton(
                        text="▶️ Продолжить после проблемы",
                        callback_data=(
                            f"problem_continue:"
                            f"{order['id']}"
                        ),
                    )
                ],
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

                c.full_name
                    AS courier_name,

                c.phone
                    AS courier_phone,

                c.vehicle
                    AS courier_vehicle,

                su.full_name
                    AS created_by

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
                            f"admin_history:"
                            f"{order['id']}"
                        ),
                    ),

                    InlineKeyboardButton(
                        text="📸 Фото",
                        callback_data=(
                            f"admin_photos:"
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
# АДМИН — ПОИСК
# =========================================================

@dp.message(
    F.text == "🔎 Найти заказ"
)
async def admin_search_start(
    message: Message,
    state: FSMContext,
):

    if await deny_admin_message(
        message
    ):
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
                    f"admin_history:"
                    f"{order_id}"
                ),
            ),

            InlineKeyboardButton(
                text="📸 Фото",
                callback_data=(
                    f"admin_photos:"
                    f"{order_id}"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                text="💰 Стоимость",
                callback_data=(
                    f"set_price:"
                    f"{order_id}"
                ),
            )
        ],
    ]

    if order["status"] not in (
        "delivered",
        "cancelled",
    ):

        if order["status"] == "problem":

            buttons.append([
                InlineKeyboardButton(
                    text="▶️ Продолжить после проблемы",
                    callback_data=(
                        f"problem_continue:"
                        f"{order_id}"
                    ),
                )
            ])

        if order["status"] not in (
            "new",
            "postponed",
        ):

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
                text="🗓 Перенести",
                callback_data=(
                    f"admin_reschedule:"
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
        build_order_text(
            order
        ),

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


# =========================================================
# АДМИН — ИСТОРИЯ
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "admin_history:"
    )
)
async def admin_history(
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

        history = await conn.fetch(
            """
            SELECT
                status,
                note,
                created_at

            FROM order_status_history

            WHERE order_id = $1

            ORDER BY
                created_at ASC,
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
        f"🕐 ИСТОРИЯ ЗАКАЗА №"
        f"{order_id}\n\n"
    )

    for row in history:

        time_text = (
            row["created_at"]
            .strftime(
                "%d.%m.%Y %H:%M"
            )
        )

        text += (
            f"{STATUS_NAMES.get(row['status'], row['status'])}\n"
            f"🕐 {time_text}"
        )

        if row["note"]:

            text += (
                f"\n📝 "
                f"{row['note']}"
            )

        text += "\n\n"

    await callback.message.answer(
        text
    )

    await callback.answer()


# =========================================================
# АДМИН — ФОТО
# =========================================================


@dp.callback_query(
    F.data.startswith(
        "admin_photos:"
    )
)
async def admin_photos(
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

        photos = await conn.fetch(
            """
            SELECT
                photo_type,
                file_id,
                media_type

            FROM order_photos

            WHERE order_id = $1

            ORDER BY created_at ASC
            """,
            order_id,
        )

    if not photos:

        await callback.answer(
            "Фото и видео отсутствуют.",
            show_alert=True,
        )

        return

    await callback.answer()

    for photo in photos:

        if photo[
            "photo_type"
        ] == "pickup":

            caption = (
                f"📦 Заказ №{order_id}\n"
                "Отчёт при получении"
            )

        else:

            caption = (
                f"✅ Заказ №{order_id}\n"
                "Отчёт после доставки"
            )

        try:

            if (
                photo["media_type"]
                == "video"
            ):

                await bot.send_video(
                    callback.from_user.id,
                    video=photo[
                        "file_id"
                    ],
                    caption=caption,
                )

            else:

                await bot.send_photo(
                    callback.from_user.id,
                    photo=photo[
                        "file_id"
                    ],
                    caption=caption,
                )

        except Exception:
            pass



# =========================================================
# АДМИН — СТОИМОСТЬ
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "set_price:"
    )
)
async def set_price_start(
    callback: CallbackQuery,
    state: FSMContext,
):

    if await deny_admin_callback(
        callback
    ):
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
        f"💰 СТОИМОСТЬ ЗАКАЗА №"
        f"{order_id}\n\n"

        f"Сейчас: "
        f"{price_text(order['delivery_price'])}\n\n"

        "Введите новую стоимость "
        "в тенге.\n\n"

        "Например: 3000"
    )

    await callback.answer()


@dp.message(
    AdminPrice.value
)
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
    ).strip().replace(
        " ",
        "",
    )

    raw = raw.replace(
        ",",
        ".",
    )

    try:

        value = Decimal(
            raw
        )

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
                    store_id,
                    status
                """,
                value,
                order_id,
            )

            if order:

                await add_history(
                    conn,
                    order_id,
                    order["status"],
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
        f"✅ Стоимость заказа №"
        f"{order_id}: "
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

    if await deny_admin_callback(
        callback
    ):
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
                        f"confirm_cancel_order:"
                        f"{order_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Не отменять",
                    callback_data="cancel_admin_action",
                )
            ],
        ]
    )

    await callback.message.answer(
        f"⚠️ ОТМЕНА ЗАКАЗА №"
        f"{order_id}\n\n"

        f"🏪 "
        f"{order['store_name']}\n"

        f"👤 "
        f"{order['client_name']}\n\n"

        "Вы точно хотите отменить "
        "этот заказ?",

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

    if await deny_admin_callback(
        callback
    ):
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

    if order and order["courier_id"]:
        async with db_pool.acquire() as conn:
            await normalize_courier_queue(
                conn,
                order["courier_id"],
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

    await update_store_status_message(
        order_id
    )

    await callback.answer(
        "Заказ отменён."
    )



# =========================================================
# АДМИН — ПЕРЕНАЗНАЧЕНИЕ
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
        "postponed",
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
                    f"🚚 "
                    f"{courier['full_name']} "
                    f"({courier['vehicle']})"
                ),
                callback_data=(
                    f"confirm_reassign:"
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
            callback_data="cancel_admin_action",
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

    if await deny_admin_callback(
        callback
    ):
        return

    parts = callback.data.split(":")

    order_id = int(
        parts[1]
    )

    new_courier_id = int(
        parts[2]
    )

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
                "postponed",
                "delivered",
                "cancelled",
            ):

                await callback.answer(
                    "Этот заказ нельзя переназначить.",
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

            await normalize_courier_queue(
                conn,
                new_courier_id,
            )

            await conn.execute(
                """
                UPDATE orders

                SET
                    courier_id = $1,
                    status = 'assigned',
                    problem_reason = NULL,
                    problem_details = NULL,
                    problem_previous_status = NULL,
                    problem_reported_by = NULL,
                    problem_reported_at = NULL,
                    queue_position = (
                        SELECT COALESCE(MAX(q.queue_position), 0) + 1
                        FROM orders q
                        WHERE q.courier_id = $1
                          AND q.status = ANY($3::text[])
                    ),
                    updated_at = NOW()

                WHERE id = $2
                """,
                new_courier_id,
                order_id,
                list(QUEUE_ACTIVE_STATUSES),
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

    async with db_pool.acquire() as conn:
        if old_courier_id:
            await normalize_courier_queue(
                conn,
                old_courier_id,
            )

        await normalize_courier_queue(
            conn,
            new_courier_id,
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

    await send_courier_order_card(
        new_courier["telegram_id"],
        order_id,
    )


    await update_store_status_message(
        order_id
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

    if await deny_admin_callback(
        callback
    ):
        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        "Действие отменено."
    )

    await callback.answer()


# =========================================================
# ОЧЕРЕДИ КУРЬЕРОВ — АДМИН
# =========================================================

async def build_courier_queue_view(
    courier_id: int,
):

    async with db_pool.acquire() as conn:

        courier = await conn.fetchrow(
            """
            SELECT
                id,
                full_name,
                vehicle

            FROM couriers

            WHERE id = $1
              AND status = 'approved'
            """,
            courier_id,
        )

        if not courier:
            return None, None

        await normalize_courier_queue(
            conn,
            courier_id,
        )

        orders = await conn.fetch(
            """
            SELECT
                o.id,
                o.queue_position,
                o.status,
                o.client_name,
                o.delivery_address,
                s.store_name

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

            WHERE o.courier_id = $1
              AND o.status = ANY($2::text[])

            ORDER BY o.queue_position, o.id
            """,
            courier_id,
            list(QUEUE_ACTIVE_STATUSES),
        )

    text = (
        "🧾 ОЧЕРЕДЬ КУРЬЕРА\n\n"
        f"🚚 {courier['full_name']}\n"
        f"🚗 {courier['vehicle']}\n\n"
    )

    buttons = []

    if not orders:
        text += "📦 Активных заказов нет."

    for order in orders:

        text += (
            f"{order['queue_position']}️⃣ "
            f"Заказ №{order['id']} — "
            f"{STATUS_NAMES.get(order['status'], order['status'])}\n"
            f"🏪 {order['store_name']}\n"
            f"👤 {order['client_name']}\n"
            f"📍 {order['delivery_address']}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=f"🔝 №{order['id']}",
                callback_data=(
                    f"queue_move:{courier_id}:"
                    f"{order['id']}:top"
                ),
            ),
            InlineKeyboardButton(
                text="⬆️",
                callback_data=(
                    f"queue_move:{courier_id}:"
                    f"{order['id']}:up"
                ),
            ),
            InlineKeyboardButton(
                text="⬇️",
                callback_data=(
                    f"queue_move:{courier_id}:"
                    f"{order['id']}:down"
                ),
            ),
            InlineKeyboardButton(
                text="🔻",
                callback_data=(
                    f"queue_move:{courier_id}:"
                    f"{order['id']}:bottom"
                ),
            ),
        ])

    keyboard = None

    if buttons:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=buttons
        )

    return text, keyboard


@dp.message(
    F.text == "🧾 Очереди"
)
async def admin_queues(
    message: Message
):

    if await deny_admin_message(
        message
    ):
        return

    async with db_pool.acquire() as conn:

        couriers = await conn.fetch(
            """
            SELECT
                c.id,
                c.full_name,
                c.vehicle,
                COUNT(o.id) FILTER (
                    WHERE o.status = ANY($1::text[])
                ) AS active_count

            FROM couriers c

            LEFT JOIN orders o
                ON o.courier_id = c.id

            WHERE c.status = 'approved'

            GROUP BY
                c.id,
                c.full_name,
                c.vehicle

            ORDER BY
                active_count ASC,
                c.full_name
            """,
            list(QUEUE_ACTIVE_STATUSES),
        )

    if not couriers:

        await message.answer(
            "🚚 Одобренных курьеров нет."
        )

        return

    buttons = []

    for courier in couriers:

        buttons.append([
            InlineKeyboardButton(
                text=(
                    f"🚚 {courier['full_name']} — "
                    f"{courier['active_count']} заказов"
                ),
                callback_data=(
                    f"queue_courier:"
                    f"{courier['id']}"
                ),
            )
        ])

    await message.answer(
        "🧾 ОЧЕРЕДИ КУРЬЕРОВ\n\n"
        "Выберите курьера.\n"
        "В скобках показана текущая нагрузка.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )


@dp.callback_query(
    F.data.startswith("queue_courier:")
)
async def admin_queue_courier(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
        return

    courier_id = int(
        callback.data.split(":")[1]
    )

    text, keyboard = await build_courier_queue_view(
        courier_id
    )

    if not text:

        await callback.answer(
            "Курьер не найден.",
            show_alert=True,
        )

        return

    await callback.message.answer(
        text,
        reply_markup=keyboard,
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("queue_move:")
)
async def admin_queue_move(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
        return

    parts = callback.data.split(":")

    courier_id = int(parts[1])
    order_id = int(parts[2])
    direction = parts[3]

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            await normalize_courier_queue(
                conn,
                courier_id,
            )

            rows = await conn.fetch(
                """
                SELECT id, status
                FROM orders
                WHERE courier_id = $1
                  AND status = ANY($2::text[])
                ORDER BY queue_position, id
                FOR UPDATE
                """,
                courier_id,
                list(QUEUE_ACTIVE_STATUSES),
            )

            ids = [row["id"] for row in rows]
            statuses = {
                row["id"]: row["status"]
                for row in rows
            }

            if order_id not in ids:

                await callback.answer(
                    "Заказ уже не находится "
                    "в этой очереди.",
                    show_alert=True,
                )

                return

            old_index = ids.index(order_id)
            new_index = old_index

            if direction == "top":
                new_index = 0

            elif direction == "up":
                new_index = max(
                    0,
                    old_index - 1,
                )

            elif direction == "down":
                new_index = min(
                    len(ids) - 1,
                    old_index + 1,
                )

            elif direction == "bottom":
                new_index = len(ids) - 1

            ids.pop(old_index)
            ids.insert(new_index, order_id)

            for position, current_id in enumerate(
                ids,
                start=1,
            ):

                await conn.execute(
                    """
                    UPDATE orders
                    SET
                        queue_position = $1,
                        updated_at = NOW()
                    WHERE id = $2
                    """,
                    position,
                    current_id,
                )

            await add_history(
                conn,
                order_id,
                statuses.get(order_id, "assigned"),
                "admin",
                callback.from_user.id,
                (
                    "Позиция в очереди изменена: "
                    f"{new_index + 1}"
                ),
            )

    await notify_courier(
        courier_id,
        "🧾 Администратор изменил порядок вашей очереди. "
        "Откройте «📦 Мои доставки», чтобы увидеть новый порядок.",
    )

    text, keyboard = await build_courier_queue_view(
        courier_id
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=keyboard,
        )

    await callback.answer(
        "Очередь обновлена."
    )


# =========================================================
# ПЕРЕНОС / ПЕРЕВЫПУСК СУЩЕСТВУЮЩЕГО ЗАКАЗА
# =========================================================

async def can_store_reschedule(
    user_id: int,
    order_id: int,
):

    async with db_pool.acquire() as conn:

        return await conn.fetchrow(
            """
            SELECT
                o.id,
                o.store_id,
                o.status,
                o.created_by_telegram_id

            FROM orders o

            JOIN store_users su
                ON su.store_id = o.store_id

            WHERE o.id = $1
              AND su.telegram_id = $2
              AND o.created_by_telegram_id = $2
              AND o.status IN (
                  'new',
                  'postponed'
              )
            """,
            order_id,
            user_id,
        )


async def show_reschedule_menu(
    callback: CallbackQuery,
    order_id: int,
    actor: str,
):

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌙 Через 24 часа",
                    callback_data=(
                        f"reschedule_24:"
                        f"{actor}:{order_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Указать дату и время",
                    callback_data=(
                        f"reschedule_custom:"
                        f"{actor}:{order_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Отмена",
                    callback_data=(
                        "cancel_admin_action"
                        if actor == "admin"
                        else "cancel_store_action"
                    ),
                )
            ],
        ]
    )

    await callback.message.answer(
        f"🗓 ПЕРЕНОС ЗАКАЗА №{order_id}\n\n"
        "Выберите время повторной публикации.\n\n"
        "Заказ сохранит тот же номер, документы, "
        "Kittek/Kaspi и всю историю.",
        reply_markup=keyboard,
    )


@dp.callback_query(
    F.data.startswith("admin_reschedule:")
)
async def admin_reschedule_start(
    callback: CallbackQuery
):

    if await deny_admin_callback(
        callback
    ):
        return

    order_id = int(
        callback.data.split(":")[1]
    )

    order = await get_order_full(
        order_id
    )

    if (
        not order
        or order["status"] in (
            "delivered",
            "cancelled",
        )
    ):

        await callback.answer(
            "Этот заказ нельзя перенести.",
            show_alert=True,
        )

        return

    await show_reschedule_menu(
        callback,
        order_id,
        "admin",
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("store_reschedule:")
)
async def store_reschedule_start(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    valid = await can_store_reschedule(
        callback.from_user.id,
        order_id,
    )

    if not valid:

        await callback.answer(
            "Перенести можно только свой новый "
            "или уже перенесённый заказ.",
            show_alert=True,
        )

        return

    await show_reschedule_menu(
        callback,
        order_id,
        "store",
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("reschedule_24:")
)
async def reschedule_24_hours(
    callback: CallbackQuery
):

    parts = callback.data.split(":")
    actor = parts[1]
    order_id = int(parts[2])

    if actor == "admin":

        if await deny_admin_callback(
            callback
        ):
            return

        order = await get_order_full(
            order_id
        )

        if (
            not order
            or order["status"] in (
                "delivered",
                "cancelled",
            )
        ):

            await callback.answer(
                "Заказ нельзя перенести.",
                show_alert=True,
            )

            return

    else:

        valid = await can_store_reschedule(
            callback.from_user.id,
            order_id,
        )

        if not valid:

            await callback.answer(
                "Нет доступа.",
                show_alert=True,
            )

            return

    scheduled_for = (
        datetime.now(LOCAL_TZ)
        + timedelta(hours=24)
    )

    result = await postpone_existing_order(
        order_id,
        scheduled_for,
        actor,
        callback.from_user.id,
        (
            "Заказ перенесён на 24 часа: "
            f"{scheduled_for.strftime('%d.%m.%Y %H:%M')}"
        ),
    )

    if not result:

        await callback.answer(
            "Не удалось перенести заказ.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Заказ №{order_id} перенесён.\n\n"
        f"🗓 Повторная публикация: "
        f"{scheduled_for.strftime('%d.%m.%Y %H:%M')}"
    )

    await callback.answer(
        "Заказ перенесён."
    )


@dp.callback_query(
    F.data.startswith("reschedule_custom:")
)
async def reschedule_custom_start(
    callback: CallbackQuery,
    state: FSMContext,
):

    parts = callback.data.split(":")
    actor = parts[1]
    order_id = int(parts[2])

    if actor == "admin":

        if await deny_admin_callback(
            callback
        ):
            return

    else:

        valid = await can_store_reschedule(
            callback.from_user.id,
            order_id,
        )

        if not valid:

            await callback.answer(
                "Нет доступа.",
                show_alert=True,
            )

            return

    await state.update_data(
        reschedule_order_id=order_id,
        reschedule_actor=actor,
    )

    await state.set_state(
        OrderReschedule.value
    )

    await callback.message.answer(
        f"📅 Заказ №{order_id}\n\n"
        "Введите дату и время повторной публикации.\n\n"
        "Формат: ДД.ММ.ГГГГ ЧЧ:ММ\n"
        "Например: 20.08.2026 10:00"
    )

    await callback.answer()


@dp.message(
    OrderReschedule.value
)
async def reschedule_custom_save(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    order_id = data.get(
        "reschedule_order_id"
    )

    actor = data.get(
        "reschedule_actor"
    )

    scheduled_for = parse_scheduled_datetime(
        message.text
    )

    if not scheduled_for:

        await message.answer(
            "❌ Не понял дату и время.\n\n"
            "Например: 20.08.2026 10:00"
        )

        return

    if scheduled_for <= datetime.now(
        LOCAL_TZ
    ):

        await message.answer(
            "❌ Время должно быть в будущем."
        )

        return

    if actor == "admin":

        if not is_admin(
            message.from_user.id
        ):

            await state.clear()
            return

    else:

        valid = await can_store_reschedule(
            message.from_user.id,
            order_id,
        )

        if not valid:

            await state.clear()

            await message.answer(
                "❌ Нет доступа к этому заказу."
            )

            return

    result = await postpone_existing_order(
        order_id,
        scheduled_for,
        actor,
        message.from_user.id,
        (
            "Заказ перенесён на "
            f"{scheduled_for.strftime('%d.%m.%Y %H:%M')}"
        ),
    )

    await state.clear()

    if not result:

        await message.answer(
            "❌ Не удалось перенести заказ."
        )

        return

    role, _ = await get_user_role(
        message.from_user.id
    )

    reply_markup = (
        admin_keyboard
        if actor == "admin"
        else store_keyboard
    )

    await message.answer(
        f"✅ Заказ №{order_id} перенесён.\n\n"
        f"🗓 Повторная публикация: "
        f"{scheduled_for.strftime('%d.%m.%Y %H:%M')}",
        reply_markup=reply_markup,
    )


@dp.callback_query(
    F.data.startswith("store_release_now:")
)
async def store_release_now(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    valid = await can_store_reschedule(
        callback.from_user.id,
        order_id,
    )

    if (
        not valid
        or valid["status"] != "postponed"
    ):

        await callback.answer(
            "Заказ уже опубликован или недоступен.",
            show_alert=True,
        )

        return

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            updated = await conn.fetchrow(
                """
                UPDATE orders
                SET
                    status = 'new',
                    rescheduled_for = NULL,
                    rescheduled_by = NULL,
                    updated_at = NOW()
                WHERE id = $1
                  AND status = 'postponed'
                RETURNING id
                """,
                order_id,
            )

            if updated:

                await add_history(
                    conn,
                    order_id,
                    "new",
                    "store",
                    callback.from_user.id,
                    "Менеджер перевыпустил заказ сейчас",
                )

    if not updated:

        await callback.answer(
            "Заказ уже опубликован.",
            show_alert=True,
        )

        return

    await publish_new_order(
        order_id
    )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"🚀 Заказ №{order_id} перевыпущен сейчас."
    )

    await callback.answer(
        "Заказ опубликован."
    )


@dp.callback_query(
    F.data == "cancel_store_action"
)
async def cancel_store_action(
    callback: CallbackQuery
):

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.answer(
        "Действие отменено."
    )


# =========================================================
# ПРОБЛЕМА С ЗАКАЗОМ
# =========================================================

PROBLEM_REASONS = {
    "client_cancelled": "❌ Клиент отменил заказ",
    "no_answer": "📵 Клиент не отвечает",
    "cannot_accept": "🕐 Клиент не может принять сейчас",
    "wrong_address": "📍 Неверный адрес",
    "product": "📦 Проблема с товаром",
    "payment": "💳 Проблема с оплатой",
    "vehicle": "🚚 Проблема с автомобилем",
    "other": "⚠️ Другая проблема",
}


async def register_order_problem(
    order_id: int,
    courier_telegram_id: int,
    reason: str,
    details=None,
):

    courier_id = await get_approved_courier_id(
        courier_telegram_id
    )

    if not courier_id:
        return None

    async with db_pool.acquire() as conn:

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                SELECT
                    id,
                    store_id,
                    status,
                    courier_id

                FROM orders

                WHERE id = $1
                  AND courier_id = $2
                  AND status = ANY($3::text[])

                FOR UPDATE
                """,
                order_id,
                courier_id,
                list(QUEUE_ACTIVE_STATUSES),
            )

            if not order:
                return None

            previous_status = order[
                "status"
            ]

            await conn.execute(
                """
                UPDATE orders

                SET
                    status = 'problem',
                    problem_reason = $1,
                    problem_details = $2,
                    problem_previous_status = $3,
                    problem_reported_by = $4,
                    problem_reported_at = NOW(),
                    queue_position = NULL,
                    updated_at = NOW()

                WHERE id = $5
                """,
                reason,
                details,
                previous_status,
                courier_telegram_id,
                order_id,
            )

            await add_history(
                conn,
                order_id,
                "problem",
                "courier",
                courier_telegram_id,
                (
                    f"Проблема: {reason}"
                    + (
                        f". {details}"
                        if details
                        else ""
                    )
                ),
            )

            await normalize_courier_queue(
                conn,
                courier_id,
            )

    await notify_store_users(
        order["store_id"],
        f"⚠️ Заказ №{order_id}\n"
        f"Проблема: {reason}"
        + (
            f"\n📝 {details}"
            if details
            else ""
        ),
    )

    if ADMIN_IDS:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="▶️ Продолжить",
                        callback_data=(
                            f"problem_continue:"
                            f"{order_id}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗓 Перенести",
                        callback_data=(
                            f"admin_reschedule:"
                            f"{order_id}"
                        ),
                    ),
                    InlineKeyboardButton(
                        text="🔄 Сменить курьера",
                        callback_data=(
                            f"reassign_order:"
                            f"{order_id}"
                        ),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отменить заказ",
                        callback_data=(
                            f"cancel_order_admin:"
                            f"{order_id}"
                        ),
                    )
                ],
            ]
        )

        for admin_id in ADMIN_IDS:

            try:

                await bot.send_message(
                    admin_id,
                    f"⚠️ ПРОБЛЕМА С ЗАКАЗОМ №{order_id}\n\n"
                    f"Причина: {reason}"
                    + (
                        f"\n📝 {details}"
                        if details
                        else ""
                    ),
                    reply_markup=keyboard,
                )

            except Exception:
                pass

    await update_store_status_message(
        order_id
    )

    return order



@dp.callback_query(
    F.data.startswith("order_problem:")
)
async def courier_problem_menu(
    callback: CallbackQuery,
    state: FSMContext,
):

    order_id = int(
        callback.data.split(":")[1]
    )

    courier_id = await get_approved_courier_id(
        callback.from_user.id
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
                  AND status = ANY($3::text[])
            )
            """,
            order_id,
            courier_id,
            list(QUEUE_ACTIVE_STATUSES),
        )

    if not valid:

        await callback.answer(
            "Этот заказ сейчас недоступен.",
            show_alert=True,
        )

        return

    await state.update_data(
        problem_source_chat_id=callback.message.chat.id,
        problem_source_message_id=callback.message.message_id,
    )

    buttons = []

    for code, title in PROBLEM_REASONS.items():

        buttons.append([
            InlineKeyboardButton(
                text=title,
                callback_data=(
                    f"problem_reason:"
                    f"{order_id}:{code}"
                ),
            )
        ])

    await callback.message.answer(
        f"⚠️ ПРОБЛЕМА С ЗАКАЗОМ №{order_id}\n\n"
        "Выберите причину:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("problem_reason:")
)
async def courier_problem_reason(
    callback: CallbackQuery,
    state: FSMContext,
):

    parts = callback.data.split(":")

    order_id = int(parts[1])
    code = parts[2]

    if code not in PROBLEM_REASONS:

        await callback.answer(
            "Неизвестная причина.",
            show_alert=True,
        )

        return

    if code == "other":

        await state.update_data(
            problem_order_id=order_id,
        )

        await state.set_state(
            CourierProblem.details
        )

        await callback.message.answer(
            f"⚠️ Заказ №{order_id}\n\n"
            "Опишите проблему своими словами:"
        )

        await callback.answer()
        return

    reason = PROBLEM_REASONS[code]

    result = await register_order_problem(
        order_id,
        callback.from_user.id,
        reason,
    )

    if not result:

        await callback.answer(
            "Не удалось сообщить о проблеме.",
            show_alert=True,
        )

        return

    source = await state.get_data()

    source_chat_id = source.get(
        "problem_source_chat_id"
    )

    source_message_id = source.get(
        "problem_source_message_id"
    )

    if source_chat_id and source_message_id:
        try:
            await update_courier_order_card(
                source_chat_id,
                source_message_id,
                order_id,
            )
        except Exception:
            pass

    await state.clear()

    await callback.message.answer(
        f"✅ Проблема по заказу №{order_id} отправлена администратору."
    )

    await callback.answer(
        "Проблема отправлена."
    )


@dp.message(
    CourierProblem.details
)
async def courier_problem_details(
    message: Message,
    state: FSMContext,
):

    details = (
        message.text or ""
    ).strip()

    if not details:

        await message.answer(
            "❌ Опишите проблему текстом."
        )

        return

    data = await state.get_data()

    order_id = data.get(
        "problem_order_id"
    )

    result = await register_order_problem(
        order_id,
        message.from_user.id,
        PROBLEM_REASONS["other"],
        details,
    )

    source_chat_id = data.get(
        "problem_source_chat_id"
    )

    source_message_id = data.get(
        "problem_source_message_id"
    )

    if result and source_chat_id and source_message_id:
        try:
            await update_courier_order_card(
                source_chat_id,
                source_message_id,
                order_id,
            )
        except Exception:
            pass

    await state.clear()

    if not result:

        await message.answer(
            "❌ Не удалось сообщить о проблеме."
        )

        return

    await message.answer(
        f"✅ Проблема по заказу №{order_id} отправлена администратору."
    )


@dp.callback_query(
    F.data.startswith("problem_continue:")
)
async def admin_problem_continue(
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

        async with conn.transaction():

            order = await conn.fetchrow(
                """
                SELECT
                    id,
                    courier_id,
                    store_id,
                    problem_previous_status

                FROM orders

                WHERE id = $1
                  AND status = 'problem'

                FOR UPDATE
                """,
                order_id,
            )

            if not order:

                await callback.answer(
                    "Проблема уже решена.",
                    show_alert=True,
                )

                return

            restored_status = (
                order["problem_previous_status"]
                if order["problem_previous_status"]
                in QUEUE_ACTIVE_STATUSES
                else "assigned"
            )

            queue_position = await get_next_queue_position(
                conn,
                order["courier_id"],
            )

            await conn.execute(
                """
                UPDATE orders
                SET
                    status = $1,
                    queue_position = $2,
                    problem_reason = NULL,
                    problem_details = NULL,
                    problem_previous_status = NULL,
                    problem_reported_by = NULL,
                    problem_reported_at = NULL,
                    updated_at = NOW()
                WHERE id = $3
                """,
                restored_status,
                queue_position,
                order_id,
            )

            await add_history(
                conn,
                order_id,
                restored_status,
                "admin",
                callback.from_user.id,
                "Администратор разрешил продолжить доставку",
            )

            await normalize_courier_queue(
                conn,
                order["courier_id"],
            )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await notify_courier(
        order["courier_id"],
        f"▶️ Проблема по заказу №{order_id} закрыта. "
        "Можно продолжать доставку."
    )

    async with db_pool.acquire() as conn:
        courier_tg = await conn.fetchval(
            """
            SELECT telegram_id
            FROM couriers
            WHERE id = $1
            """,
            order["courier_id"],
        )

    if courier_tg:
        await send_courier_order_card(
            courier_tg,
            order_id,
        )

    await notify_store_users(
        order["store_id"],
        f"▶️ По заказу №{order_id} проблема решена. "
        "Доставка продолжается."
    )

    await update_store_status_message(
        order_id
    )

    await callback.answer(
        "Доставка продолжена."
    )



# =========================================================
# АДМИН — МАГАЗИНЫ
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
                    WHEN s.status = 'pending'
                    THEN 0

                    WHEN s.status = 'approved'
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

        status = {
            "pending": "⏳ Ожидает",
            "approved": "✅ Одобрен",
            "rejected": "❌ Отклонён",
        }.get(
            store["status"],
            store["status"],
        )

        await message.answer(
            f"🏪 "
            f"{store['store_name']}\n\n"

            f"Статус: "
            f"{status}\n"

            f"👥 Пользователей: "
            f"{store['members_count']}\n"

            f"👤 Контакт: "
            f"{store['contact_name']}\n"

            f"📞 "
            f"{store['phone']}\n"

            f"📍 "
            f"{store['address']}",

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

    if await deny_admin_message(
        message
    ):
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

        status = {
            "pending": "⏳ Ожидает",
            "approved": "✅ Одобрен",
            "rejected": "❌ Отклонён",
        }.get(
            courier["status"],
            courier["status"],
        )

        await message.answer(
            f"🚚 "
            f"{courier['full_name']}\n\n"

            f"Статус: "
            f"{status}\n"

            f"📞 "
            f"{courier['phone']}\n"

            f"🚗 "
            f"{courier['vehicle']}",

            reply_markup=keyboard,
        )


# =========================================================
# ОДОБРЕНИЕ МАГАЗИНА
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "approve_store:"
    )
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
    F.data.startswith(
        "reject_store:"
    )
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

    await callback.answer(
        "Магазин отклонён."
    )


# =========================================================
# ОДОБРЕНИЕ КУРЬЕРА
# =========================================================

@dp.callback_query(
    F.data.startswith(
        "approve_courier:"
    )
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

        await bot.send_message(
            courier["telegram_id"],

            "✅ Ваша заявка "
            "курьера одобрена."
        )

    except Exception:
        pass

    await callback.answer(
        "Курьер одобрен."
    )


@dp.callback_query(
    F.data.startswith(
        "reject_courier:"
    )
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

    # В группах бот не мешает общению.

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
        "Admin IDs:",
        sorted(ADMIN_IDS)
        if ADMIN_IDS
        else "NOT SET",
    )

    print(
        "Bot is starting..."
    )

    scheduled_worker_task = asyncio.create_task(
        scheduled_orders_worker()
    )

    morning_worker_task = asyncio.create_task(
        morning_menu_worker()
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        scheduled_worker_task.cancel()

        morning_worker_task.cancel()

        for task in (
            scheduled_worker_task,
            morning_worker_task,
        ):

            try:

                await task

            except asyncio.CancelledError:
                pass



if __name__ == "__main__":

    asyncio.run(
        main()
    )
