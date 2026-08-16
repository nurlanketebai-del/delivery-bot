import os
import asyncio
import secrets
import string

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
    delivery_time = State()
    comment = State()
    confirm = State()


class CourierPhoto(StatesGroup):
    pickup_photo = State()
    delivery_photo = State()


# =========================================================
# БАЗА ДАННЫХ
# =========================================================

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

        # Пользователи магазинов:
        # owner = владелец
        # manager = менеджер
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

        # Приглашения менеджеров
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

        # Кто из менеджеров создал заказ
        await conn.execute(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS created_by_telegram_id BIGINT
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


# =========================================================
# ОБЩИЕ ФУНКЦИИ
# =========================================================

def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


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


async def get_user_role(user_id: int):
    """
    Возвращает:
    ("store", store)
    ("courier", courier)
    (None, None)

    Отклонённые старые заявки не блокируют пользователя.
    """

    store = await get_store_membership(user_id)
    courier = await get_courier(user_id)

    # Одобренный магазин
    if store and store["status"] == "approved":
        return "store", store

    # Одобренный курьер
    if courier and courier["status"] == "approved":
        return "courier", courier

    # Ожидающий магазин
    if store and store["status"] == "pending":
        return "store", store

    # Ожидающий курьер
    if courier and courier["status"] == "pending":
        return "courier", courier

    # rejected не блокирует выбор новой роли
    return None, None


async def deny_admin_message(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(
            "❌ Доступ запрещён.\n\n"
            "Этот раздел доступен только администратору."
        )
        return True

    return False


async def deny_admin_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            "❌ У вас нет прав администратора.",
            show_alert=True,
        )
        return True

    return False


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


# =========================================================
# КЛАВИАТУРЫ
# =========================================================

def main_keyboard(role, user_id: int):

    rows = []

    if role == "store":
        rows.append(
            [KeyboardButton(text="🏪 Магазин")]
        )

    elif role == "courier":
        rows.append(
            [KeyboardButton(text="🚚 Курьер")]
        )

    else:
        rows.append(
            [
                KeyboardButton(text="🏪 Магазин"),
                KeyboardButton(text="🚚 Курьер"),
            ]
        )

    if is_admin(user_id):
        rows.append(
            [KeyboardButton(text="👨‍💼 Администратор")]
        )

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


async def send_main_menu(message: Message):
    role, _ = await get_user_role(message.from_user.id)

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

    text = "👋 Добро пожаловать в систему доставки!"

    if role == "store":
        text += "\n\n🏪 Ваша роль: Магазин"

        if info["status"] == "pending":
            text += "\n⏳ Магазин ожидает одобрения."

    elif role == "courier":
        text += "\n\n🚚 Ваша роль: Курьер"

        if info["status"] == "pending":
            text += "\n⏳ Заявка ожидает одобрения."

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
            "❌ Вы зарегистрированы как курьер.\n\n"
            "Один Telegram-аккаунт может иметь "
            "только одну рабочую роль."
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
            "⏳ Магазин ожидает подтверждения администратора."
        )
        return

    await message.answer(
        "🏪 МАГАЗИН\n\n"
        "Вы можете зарегистрировать новый магазин "
        "или присоединиться к существующему магазинy "
        "как менеджер.",
        reply_markup=store_entry_keyboard,
    )


# =========================================================
# РЕГИСТРАЦИЯ МАГАЗИНА
# =========================================================

@dp.message(F.text == "🆕 Зарегистрировать магазин")
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


@dp.message(StoreRegistration.store_name)
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


@dp.message(StoreRegistration.contact_name)
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


@dp.message(StoreRegistration.phone)
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


@dp.message(StoreRegistration.address)
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
        f"🏪 Магазин: {data['store_name']}\n"
        f"👤 Контакт: {data['contact_name']}\n"
        f"📞 Телефон: {data['phone']}\n"
        f"📍 Адрес: {data['address']}\n\n"
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

            # Старую rejected-заявку можно обновить.
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
                    store_name = EXCLUDED.store_name,
                    contact_name = EXCLUDED.contact_name,
                    phone = EXCLUDED.phone,
                    address = EXCLUDED.address,
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
                    store_id = EXCLUDED.store_id,
                    full_name = EXCLUDED.full_name,
                    member_role = 'owner'
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

    await send_main_menu(message)


# =========================================================
# ПРИСОЕДИНЕНИЕ К МАГАЗИНУ
# =========================================================

@dp.message(F.text == "🔑 Присоединиться к магазину")
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


@dp.message(StoreJoin.invite_code)
async def join_store_code(
    message: Message,
    state: FSMContext,
):

    code = (message.text or "").strip().upper()

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
                    "❌ Код неверный или уже использован."
                )
                return

            if invite["status"] != "approved":
                await message.answer(
                    "❌ Этот магазин сейчас недоступен."
                )
                return

            # Если раньше была rejected-заявка магазина,
            # разрешаем заменить старую связь.
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
                    store_id = EXCLUDED.store_id,
                    full_name = EXCLUDED.full_name,
                    member_role = 'manager'
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
        f"🏪 {invite['store_name']}\n"
        "👤 Роль: Менеджер",
        reply_markup=store_keyboard,
    )


# =========================================================
# МЕНЕДЖЕРЫ МАГАЗИНА
# =========================================================

@dp.message(F.text == "👥 Менеджеры")
async def managers_handler(message: Message):

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
        f"🏪 {membership['store_name']}\n\n"
    )

    for member in members:

        if member["member_role"] == "owner":
            role_text = "👑 Владелец"
        else:
            role_text = "👤 Менеджер"

        text += (
            f"{role_text}: "
            f"{member['full_name']}\n"
        )

    keyboard = None

    if membership["member_role"] == "owner":

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Пригласить менеджера",
                        callback_data="create_manager_invite",
                    )
                ]
            ]
        )

    await message.answer(
        text,
        reply_markup=keyboard,
    )


@dp.callback_query(
    F.data == "create_manager_invite"
)
async def create_manager_invite(
    callback: CallbackQuery,
):

    membership = await get_store_membership(
        callback.from_user.id
    )

    if (
        not membership
        or membership["status"] != "approved"
        or membership["member_role"] != "owner"
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
# ПРОФИЛЬ МАГАЗИНА
# =========================================================

@dp.message(F.text == "🏪 Профиль магазина")
async def store_profile(message: Message):

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
        f"Название: {membership['store_name']}\n"
        f"📍 Адрес: {membership['address']}\n"
        f"📞 Телефон: {membership['phone']}\n\n"
        f"👤 Пользователь: {membership['full_name']}\n"
        f"🔐 Роль: {role_name}\n"
        f"Статус: {membership['status']}"
    )


# =========================================================
# СОЗДАНИЕ ЗАКАЗА
# =========================================================

@dp.message(F.text == "➕ Создать заказ")
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
            "только пользователь одобренного магазина."
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
        "📞 Введите номер телефона клиента:"
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


@dp.message(OrderCreation.delivery_address)
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
        OrderCreation.delivery_time
    )

    await message.answer(
        "🕐 Укажите желаемое время доставки:"
    )


@dp.message(OrderCreation.delivery_time)
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
        "📝 Добавьте комментарий к заказу.\n\n"
        "Если комментария нет — напишите: Нет"
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
        f"🏪 Магазин: {membership['store_name']}\n"
        f"👤 Создал: {membership['full_name']}\n"
        f"📍 Забрать: {membership['address']}\n\n"
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

    async with db_pool.acquire() as conn:

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
                $1,$2,$3,$4,$5,$6,$7,$8,$9
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

@dp.message(F.text == "📦 Мои заказы")
async def store_orders(message: Message):

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
                o.status,
                o.created_by_telegram_id,
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

    status_map = {
        "new": "🆕 Новый",
        "assigned": "🚚 Назначен",
        "accepted": "✅ Курьер принял",
        "pickup_photo": "📸 Фото получения",
        "picked_up": "📦 Товар забран",
        "on_the_way": "🚗 В пути",
        "arrived": "📍 Курьер прибыл",
        "delivery_photo": "📸 Фото доставки",
        "delivered": "✅ Доставлен",
    }

    text = "📦 ЗАКАЗЫ МАГАЗИНА\n\n"

    for order in orders:

        author = (
            order["created_by"]
            or "Старый заказ"
        )

        text += (
            f"№{order['id']} — "
            f"{status_map.get(order['status'], order['status'])}\n"
            f"👤 Создал: {author}\n"
            f"👤 Клиент: {order['client_name']}\n"
            f"📞 {order['client_phone']}\n"
            f"📍 {order['delivery_address']}\n"
            f"📦 {order['item']}\n\n"
        )

    await message.answer(text)


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
            "❌ Вы зарегистрированы как пользователь магазина.\n\n"
            "Один Telegram-аккаунт может иметь "
            "только одну рабочую роль."
        )
        return

    if role == "courier":

        if info["status"] == "approved":
            await message.answer(
                f"🚚 Курьер: {info['full_name']}\n\n"
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


@dp.message(CourierRegistration.full_name)
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


@dp.message(CourierRegistration.phone)
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
        "🚗 Укажите транспорт.\n\n"
        "Например: KYC T3"
    )


@dp.message(CourierRegistration.vehicle)
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
        f"👤 Имя: {data['full_name']}\n"
        f"📞 Телефон: {data['phone']}\n"
        f"🚗 Транспорт: {data['vehicle']}\n\n"
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
        "⏳ Ожидайте подтверждения администратора."
    )

    await send_main_menu(message)


# =========================================================
# ПРОФИЛЬ КУРЬЕРА
# =========================================================

@dp.message(F.text == "🚚 Профиль курьера")
async def courier_profile(message: Message):

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


# =========================================================
# МОИ ДОСТАВКИ
# =========================================================

@dp.message(F.text == "📦 Мои доставки")
async def courier_orders(message: Message):

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

        orders = await conn.fetch(
            """
            SELECT
                o.*,
                s.store_name

            FROM orders o

            JOIN stores s
                ON s.id = o.store_id

            WHERE o.courier_id = $1
              AND o.status != 'delivered'

            ORDER BY o.id DESC
            """,
            courier_id,
        )

    if not orders:
        await message.answer(
            "📦 У вас пока нет активных доставок."
        )
        return

    status_map = {
        "assigned": "🚚 Назначен",
        "accepted": "✅ Принят",
        "pickup_photo": "📸 Фото товара получено",
        "picked_up": "📦 Товар забран",
        "on_the_way": "🚗 В пути",
        "arrived": "📍 Прибыл",
        "delivery_photo": "📸 Фото доставки получено",
    }

    for order in orders:

        buttons = []

        if order["status"] == "assigned":

            buttons = [[
                InlineKeyboardButton(
                    text="✅ Принять заказ",
                    callback_data=f"accept_order:{order['id']}",
                )
            ]]

        elif order["status"] == "accepted":

            buttons = [[
                InlineKeyboardButton(
                    text="📸 Фото товара при получении",
                    callback_data=f"pickup_photo:{order['id']}",
                )
            ]]

        elif order["status"] == "pickup_photo":

            buttons = [[
                InlineKeyboardButton(
                    text="📦 Товар забран",
                    callback_data=f"picked_up:{order['id']}",
                )
            ]]

        elif order["status"] == "picked_up":

            buttons = [[
                InlineKeyboardButton(
                    text="🚗 Выехал к клиенту",
                    callback_data=f"on_way:{order['id']}",
                )
            ]]

        elif order["status"] == "on_the_way":

            buttons = [[
                InlineKeyboardButton(
                    text="📍 Я приехал",
                    callback_data=f"arrived:{order['id']}",
                )
            ]]

        elif order["status"] == "arrived":

            buttons = [[
                InlineKeyboardButton(
                    text="📸 Фото доставки",
                    callback_data=f"delivery_photo:{order['id']}",
                )
            ]]

        elif order["status"] == "delivery_photo":

            buttons = [[
                InlineKeyboardButton(
                    text="✅ Завершить доставку",
                    callback_data=f"delivered:{order['id']}",
                )
            ]]

        keyboard = None

        if buttons:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=buttons
            )

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
            f"Статус: "
            f"{status_map.get(order['status'], order['status'])}",
            reply_markup=keyboard,
        )


# =========================================================
# ПРИНЯТИЕ ЗАКАЗА
# =========================================================

@dp.callback_query(
    F.data.startswith("accept_order:")
)
async def accept_order(callback: CallbackQuery):

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

        result = await conn.execute(
            """
            UPDATE orders
            SET status = 'accepted'

            WHERE id = $1
              AND courier_id = $2
              AND status = 'assigned'
            """,
            order_id,
            courier_id,
        )

    if result == "UPDATE 0":
        await callback.answer(
            "Заказ недоступен.",
            show_alert=True,
        )
        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Заказ №{order_id} принят.\n\n"
        "Теперь сделайте фото товара "
        "при получении."
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
        order_id=order_id
    )

    await state.set_state(
        CourierPhoto.pickup_photo
    )

    await callback.message.answer(
        f"📸 Отправьте фотографию товара "
        f"для заказа №{order_id}."
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

    if not courier_id:
        await state.clear()
        await message.answer(
            "❌ Курьер не найден."
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
            await state.clear()

            await message.answer(
                "❌ Этот заказ недоступен."
            )
            return

        file_id = message.photo[-1].file_id

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
            SET status = 'pickup_photo'
            WHERE id = $1
            """,
            order_id,
        )

    await state.clear()

    await message.answer(
        f"✅ Фото заказа №{order_id} сохранено.\n\n"
        "Откройте «📦 Мои доставки»."
    )


@dp.message(CourierPhoto.pickup_photo)
async def pickup_photo_wrong(message: Message):
    await message.answer(
        "📸 Пожалуйста, отправьте именно фотографию."
    )


# =========================================================
# ТОВАР ЗАБРАН
# =========================================================

@dp.callback_query(
    F.data.startswith("picked_up:")
)
async def picked_up(callback: CallbackQuery):

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

        result = await conn.execute(
            """
            UPDATE orders
            SET status = 'picked_up'

            WHERE id = $1
              AND courier_id = $2
              AND status = 'pickup_photo'
            """,
            order_id,
            courier_id,
        )

    if result == "UPDATE 0":
        await callback.answer(
            "Статус изменить не удалось.",
            show_alert=True,
        )
        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"📦 Заказ №{order_id}: товар забран."
    )

    await callback.answer()


# =========================================================
# В ПУТИ
# =========================================================

@dp.callback_query(
    F.data.startswith("on_way:")
)
async def on_way(callback: CallbackQuery):

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

        result = await conn.execute(
            """
            UPDATE orders
            SET status = 'on_the_way'

            WHERE id = $1
              AND courier_id = $2
              AND status = 'picked_up'
            """,
            order_id,
            courier_id,
        )

    if result == "UPDATE 0":
        await callback.answer(
            "Статус изменить не удалось.",
            show_alert=True,
        )
        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"🚗 Заказ №{order_id}: "
        "вы выехали к клиенту."
    )

    await callback.answer()


# =========================================================
# ПРИЕХАЛ
# =========================================================

@dp.callback_query(
    F.data.startswith("arrived:")
)
async def arrived(callback: CallbackQuery):

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

        result = await conn.execute(
            """
            UPDATE orders
            SET status = 'arrived'

            WHERE id = $1
              AND courier_id = $2
              AND status = 'on_the_way'
            """,
            order_id,
            courier_id,
        )

    if result == "UPDATE 0":
        await callback.answer(
            "Статус изменить не удалось.",
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
        f"📸 Отправьте фото подтверждения "
        f"доставки заказа №{order_id}."
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

    if not courier_id:
        await state.clear()

        await message.answer(
            "❌ Курьер не найден."
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
            await state.clear()

            await message.answer(
                "❌ Этот заказ недоступен."
            )
            return

        file_id = message.photo[-1].file_id

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
            SET status = 'delivery_photo'
            WHERE id = $1
            """,
            order_id,
        )

    await state.clear()

    await message.answer(
        f"✅ Фото доставки заказа №{order_id} "
        "сохранено.\n\n"
        "Откройте «📦 Мои доставки» "
        "и завершите заказ."
    )


@dp.message(CourierPhoto.delivery_photo)
async def delivery_photo_wrong(message: Message):
    await message.answer(
        "📸 Пожалуйста, отправьте именно фотографию."
    )


# =========================================================
# ДОСТАВЛЕНО
# =========================================================

@dp.callback_query(
    F.data.startswith("delivered:")
)
async def delivered(callback: CallbackQuery):

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
            UPDATE orders

            SET status = 'delivered'

            WHERE id = $1
              AND courier_id = $2
              AND status = 'delivery_photo'

            RETURNING store_id
            """,
            order_id,
            courier_id,
        )

        if not order:
            await callback.answer(
                "Не удалось завершить доставку.",
                show_alert=True,
            )
            return

        store_users = await conn.fetch(
            """
            SELECT telegram_id
            FROM store_users
            WHERE store_id = $1
            """,
            order["store_id"],
        )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"✅ Заказ №{order_id} "
        "успешно доставлен!"
    )

    # Уведомляем всех менеджеров магазина
    for user in store_users:
        try:
            await bot.send_message(
                user["telegram_id"],
                f"✅ Заказ №{order_id} "
                "успешно доставлен."
            )
        except Exception:
            pass

    await callback.answer(
        "Доставка завершена."
    )


# =========================================================
# АДМИН — ГЛАВНАЯ
# =========================================================

@dp.message(F.text == "👨‍💼 Администратор")
async def admin_home(message: Message):

    if await deny_admin_message(message):
        return

    async with db_pool.acquire() as conn:

        new_orders = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status = 'new'
            """
        )

        active_orders = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM orders

            WHERE status NOT IN (
                'new',
                'delivered'
            )
            """
        )

        delivered_orders = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status = 'delivered'
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
        f"📦 Новых заказов: {new_orders}\n"
        f"🚚 Активных заказов: {active_orders}\n"
        f"✅ Доставленных: {delivered_orders}\n\n"
        f"🏪 Новых заявок магазинов: {pending_stores}\n"
        f"🚚 Новых заявок курьеров: {pending_couriers}",
        reply_markup=admin_keyboard,
    )


# =========================================================
# АДМИН — НОВЫЕ ЗАКАЗЫ
# =========================================================

@dp.message(F.text == "📦 Новые заказы")
async def admin_new_orders(message: Message):

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

    await message.answer(
        f"📦 НОВЫЕ ЗАКАЗЫ: {len(orders)}"
    )

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
                        f"assign:{order['id']}:"
                        f"{courier['id']}"
                    ),
                )
            ])

        keyboard = None

        if buttons:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=buttons
            )

        author = (
            order["created_by"]
            or "Старый заказ"
        )

        text = (
            f"🆕 ЗАКАЗ №{order['id']}\n\n"
            f"🏪 Магазин: {order['store_name']}\n"
            f"👤 Создал: {author}\n"
            f"📍 Забрать: {order['pickup_address']}\n\n"
            f"👤 Клиент: {order['client_name']}\n"
            f"📞 {order['client_phone']}\n"
            f"📍 Доставить: {order['delivery_address']}\n\n"
            f"📦 {order['item']}\n"
            f"🕐 {order['delivery_time']}\n"
            f"📝 {order['comment']}"
        )

        if not couriers:
            text += (
                "\n\n⚠️ Нет одобренных курьеров."
            )

        await message.answer(
            text,
            reply_markup=keyboard,
        )


# =========================================================
# АДМИН — НАЗНАЧИТЬ КУРЬЕРА
# =========================================================

@dp.callback_query(
    F.data.startswith("assign:")
)
async def assign_order(callback: CallbackQuery):

    if await deny_admin_callback(callback):
        return

    parts = callback.data.split(":")

    order_id = int(parts[1])
    courier_id = int(parts[2])

    async with db_pool.acquire() as conn:

        courier = await conn.fetchrow(
            """
            SELECT
                id,
                telegram_id,
                full_name,
                vehicle

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
                status = 'assigned'

            WHERE id = $2
              AND status = 'new'

            RETURNING *
            """,
            courier_id,
            order_id,
        )

        if not order:
            await callback.answer(
                "Заказ уже назначен "
                "или недоступен.",
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
            f"🚚 ВАМ НАЗНАЧЕН ЗАКАЗ №{order_id}\n\n"
            f"🏪 Магазин: {store['store_name']}\n"
            f"📍 Забрать: {order['pickup_address']}\n\n"
            f"👤 Клиент: {order['client_name']}\n"
            f"📞 {order['client_phone']}\n"
            f"📍 Доставить: {order['delivery_address']}\n\n"
            f"📦 {order['item']}\n"
            f"🕐 {order['delivery_time']}\n"
            f"📝 {order['comment']}\n\n"
            "Откройте «📦 Мои доставки», "
            "чтобы принять заказ."
        )
    except Exception:
        pass

    await callback.answer(
        "Заказ назначен."
    )


# =========================================================
# АДМИН — АКТИВНЫЕ
# =========================================================

@dp.message(F.text == "🚚 Активные")
async def admin_active_orders(message: Message):

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
                'delivered'
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

    status_map = {
        "assigned": "🚚 Назначен курьер",
        "accepted": "✅ Курьер принял",
        "pickup_photo": "📸 Фото товара получено",
        "picked_up": "📦 Товар забран",
        "on_the_way": "🚗 В пути",
        "arrived": "📍 Курьер прибыл",
        "delivery_photo": "📸 Фото доставки получено",
    }

    await message.answer(
        f"🚚 АКТИВНЫЕ ЗАКАЗЫ: {len(orders)}"
    )

    for order in orders:

        await message.answer(
            f"🚚 ЗАКАЗ №{order['id']}\n\n"
            f"Статус: "
            f"{status_map.get(order['status'], order['status'])}\n\n"
            f"🏪 Магазин: {order['store_name']}\n"
            f"👤 Создал: "
            f"{order['created_by'] or 'Старый заказ'}\n"
            f"📍 Забрать: {order['pickup_address']}\n\n"
            f"👤 Клиент: {order['client_name']}\n"
            f"📞 {order['client_phone']}\n"
            f"📍 Доставить: {order['delivery_address']}\n\n"
            f"📦 {order['item']}\n"
            f"🕐 {order['delivery_time']}\n"
            f"📝 {order['comment']}\n\n"
            f"🚚 Курьер: "
            f"{order['courier_name'] or '-'}\n"
            f"📞 Курьер: "
            f"{order['courier_phone'] or '-'}"
        )


# =========================================================
# АДМИН — ДОСТАВЛЕННЫЕ
# =========================================================

@dp.message(F.text == "✅ Доставленные")
async def admin_delivered_orders(message: Message):

    if await deny_admin_message(message):
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
            "✅ Доставленных заказов пока нет.",
            reply_markup=admin_keyboard,
        )
        return

    await message.answer(
        "✅ ПОСЛЕДНИЕ ДОСТАВЛЕННЫЕ"
    )

    for order in orders:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📸 Фото заказа",
                        callback_data=(
                            f"admin_photos:{order['id']}"
                        ),
                    )
                ]
            ]
        )

        await message.answer(
            f"✅ ЗАКАЗ №{order['id']}\n\n"
            f"🏪 {order['store_name']}\n"
            f"👤 Создал: "
            f"{order['created_by'] or 'Старый заказ'}\n"
            f"👤 Клиент: {order['client_name']}\n"
            f"📞 {order['client_phone']}\n"
            f"📍 {order['delivery_address']}\n"
            f"📦 {order['item']}\n"
            f"🚚 Курьер: "
            f"{order['courier_name'] or '-'}",
            reply_markup=keyboard,
        )


# =========================================================
# АДМИН — ФОТО
# =========================================================

@dp.callback_query(
    F.data.startswith("admin_photos:")
)
async def admin_photos(callback: CallbackQuery):

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
            "У этого заказа нет фотографий.",
            show_alert=True,
        )
        return

    await callback.answer()

    for photo in photos:

        if photo["photo_type"] == "pickup":
            caption = (
                f"📸 Заказ №{order_id}\n"
                "Фото товара при получении"
            )
        else:
            caption = (
                f"📸 Заказ №{order_id}\n"
                "Фото после доставки"
            )

        try:
            await bot.send_photo(
                callback.from_user.id,
                photo=photo["file_id"],
                caption=caption,
            )
        except Exception:
            await bot.send_message(
                callback.from_user.id,
                "❌ Не удалось загрузить фотографию."
            )


# =========================================================
# АДМИН — МАГАЗИНЫ
# =========================================================

@dp.message(F.text == "🏪 Магазины")
async def admin_stores(message: Message):

    if await deny_admin_message(message):
        return

    async with db_pool.acquire() as conn:

        stores = await conn.fetch(
            """
            SELECT
                s.*,
                COUNT(su.id) AS members_count

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

            LIMIT 50
            """
        )

    if not stores:
        await message.answer(
            "🏪 Магазинов пока нет.",
            reply_markup=admin_keyboard,
        )
        return

    await message.answer(
        f"🏪 МАГАЗИНЫ: {len(stores)}"
    )

    for store in stores:

        keyboard = None

        if store["status"] == "pending":

            status = "⏳ Ожидает"

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="✅ Одобрить",
                        callback_data=(
                            f"approve_store:{store['id']}"
                        ),
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=(
                            f"reject_store:{store['id']}"
                        ),
                    ),
                ]]
            )

        elif store["status"] == "approved":
            status = "✅ Одобрен"

        else:
            status = "❌ Отклонён"

        await message.answer(
            f"🏪 {store['store_name']}\n\n"
            f"Статус: {status}\n"
            f"👥 Пользователей: "
            f"{store['members_count']}\n"
            f"👤 Контакт: {store['contact_name']}\n"
            f"📞 {store['phone']}\n"
            f"📍 {store['address']}",
            reply_markup=keyboard,
        )


# =========================================================
# АДМИН — КУРЬЕРЫ
# =========================================================

@dp.message(F.text == "🚚 Курьеры")
async def admin_couriers(message: Message):

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

            LIMIT 50
            """
        )

    if not couriers:
        await message.answer(
            "🚚 Курьеров пока нет.",
            reply_markup=admin_keyboard,
        )
        return

    await message.answer(
        f"🚚 КУРЬЕРЫ: {len(couriers)}"
    )

    for courier in couriers:

        keyboard = None

        if courier["status"] == "pending":

            status = "⏳ Ожидает"

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[
                    InlineKeyboardButton(
                        text="✅ Одобрить",
                        callback_data=(
                            f"approve_courier:{courier['id']}"
                        ),
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить",
                        callback_data=(
                            f"reject_courier:{courier['id']}"
                        ),
                    ),
                ]]
            )

        elif courier["status"] == "approved":
            status = "✅ Одобрен"

        else:
            status = "❌ Отклонён"

        await message.answer(
            f"🚚 {courier['full_name']}\n\n"
            f"Статус: {status}\n"
            f"📞 {courier['phone']}\n"
            f"🚗 {courier['vehicle']}",
            reply_markup=keyboard,
        )


# =========================================================
# АДМИН — ОДОБРИТЬ МАГАЗИН
# =========================================================

@dp.callback_query(
    F.data.startswith("approve_store:")
)
async def approve_store(callback: CallbackQuery):

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

    await callback.message.answer(
        f"✅ Магазин "
        f"{store['store_name']} одобрен."
    )

    for user in users:
        try:
            await bot.send_message(
                user["telegram_id"],
                "✅ Заявка магазина одобрена!\n\n"
                f"🏪 {store['store_name']}\n\n"
                "Теперь можно создавать заказы."
            )
        except Exception:
            pass

    await callback.answer(
        "Магазин одобрен."
    )


# =========================================================
# АДМИН — ОТКЛОНИТЬ МАГАЗИН
# =========================================================

@dp.callback_query(
    F.data.startswith("reject_store:")
)
async def reject_store(callback: CallbackQuery):

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
# АДМИН — ОДОБРИТЬ КУРЬЕРА
# =========================================================

@dp.callback_query(
    F.data.startswith("approve_courier:")
)
async def approve_courier(
    callback: CallbackQuery,
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

    await callback.message.answer(
        f"✅ Курьер "
        f"{courier['full_name']} одобрен."
    )

    try:
        await bot.send_message(
            courier["telegram_id"],
            "✅ Ваша заявка курьера одобрена!\n\n"
            "Теперь вам могут назначать заказы."
        )
    except Exception:
        pass

    await callback.answer(
        "Курьер одобрен."
    )


# =========================================================
# АДМИН — ОТКЛОНИТЬ КУРЬЕРА
# =========================================================

@dp.callback_query(
    F.data.startswith("reject_courier:")
)
async def reject_courier(
    callback: CallbackQuery,
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
                "❌ Ваша заявка курьера отклонена."
            )
        except Exception:
            pass

    await callback.answer(
        "Курьер отклонён."
    )


# =========================================================
# ОТМЕНА
# =========================================================

@dp.message(F.text == "❌ Отмена")
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
async def fallback(message: Message):
    await message.answer(
        "Пожалуйста, используйте кнопки меню."
    )


# =========================================================
# ЗАПУСК
# =========================================================

async def main():

    print("Connecting to PostgreSQL...")

    await init_db()

    print("Database connected.")

    print(
        "Admin ID:",
        ADMIN_ID if ADMIN_ID else "NOT SET"
    )

    print("Bot is starting...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
