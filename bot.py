import os
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton


TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


bot = Bot(token=TOKEN)
dp = Dispatcher()


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


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 Добро пожаловать в систему доставки!\n\n"
        "Выберите вашу роль:",
        reply_markup=main_keyboard,
    )


@dp.message()
async def message_handler(message: Message):
    if message.text == "🏪 Магазин":
        await message.answer(
            "🏪 Вы выбрали режим магазина.\n\n"
            "Скоро здесь появится регистрация магазина."
        )

    elif message.text == "🚚 Курьер":
        await message.answer(
            "🚚 Вы выбрали режим курьера.\n\n"
            "Скоро здесь появится регистрация курьера."
        )

    elif message.text == "👨‍💼 Администратор":
        await message.answer(
            "👨‍💼 Раздел администратора.\n\n"
            "Доступ будет настроен позже."
        )

    else:
        await message.answer(
            "Пожалуйста, выберите действие с помощью кнопок."
        )


async def main():
    print("Bot is starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
