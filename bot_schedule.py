import logging
import os
import threading

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from flask import Flask

# === Логирование ===
logging.basicConfig(level=logging.INFO)

# === Получаем токен из переменных окружения ===
API_TOKEN = os.getenv('BOT_TOKEN')
if not API_TOKEN:
    raise Exception("BOT_TOKEN не найден в переменных окружения.")

# === Инициализация бота и диспетчера ===
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# === Обработчики ===
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await message.reply("Привет! Я эхо-бот. Напиши мне что-нибудь, и я повторю!")

@dp.message_handler(commands=['help'])
async def help_command(message: types.Message):
    await message.reply("Я просто повторяю твои сообщения. Попробуй написать что-нибудь!")

@dp.message_handler()
async def echo_message(message: types.Message):
    await message.answer(message.text)









# === Flask-сервер для Render (пинг) ===
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is alive!", 200

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# === Точка входа ===
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    executor.start_polling(dp, skip_updates=True)
