import logging
import os
import threading
import asyncio
import pandas as pd
import requests
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from io import StringIO

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from flask import Flask

# === Логирование ===
logging.basicConfig(level=logging.INFO)

# === Переменные окружения ===
API_TOKEN = os.getenv('BOT_TOKEN')
admin     = int(os.getenv('ADMIN_ID', '0'))  # ADMIN_ID должен быть числом
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SHEET_NAME     = os.getenv('SHEET_NAME')

if not API_TOKEN or not SPREADSHEET_ID or not SHEET_NAME:
    raise Exception("Переменные окружения BOT_TOKEN, SPREADSHEET_ID и SHEET_NAME обязательны.")

FETCH_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet={SHEET_NAME}"

# === Инициализация бота и диспетчера ===
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# === Flask-сервер для Render — для пинга ===
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is alive!", 200

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# === Глобальное состояние ===
last_hash = None

# === Команды ===
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    if message.from_user.id == admin:
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Бобро пожаловать!</b>\nЭто бот для прослушки прослушек.",
            parse_mode='HTML'
        )

# === Работа с таблицей ===
def sync_fetch_text():
    response = requests.get(FETCH_URL)
    response.raise_for_status()
    return response.text

async def fetch_text_async():
    return await asyncio.to_thread(sync_fetch_text)

async def check_for_updates():
    global last_hash
    try:
        content = await fetch_text_async()
        current_hash = hash(content)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if last_hash is None:
            await bot.send_message(admin, f"[{now}] ✅ Первая загрузка данных.", parse_mode='HTML')
            last_hash = current_hash
        elif current_hash != last_hash:
            await bot.send_message(admin, f"[{now}] 📌 Таблица обновилась!", parse_mode='HTML')
            last_hash = current_hash
        else:
            await bot.send_message(admin, f"[{now}] ✅ Без изменений.", parse_mode='HTML')
    except Exception as e:
        print(f"[{datetime.now()}] ❌ Ошибка при загрузке: {e}")

async def generate_report():
    try:
        content = await fetch_text_async()
        df = pd.read_csv(StringIO(content))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await bot.send_message(admin, f"[{now}] 📊 Отчет: {len(df)} строк, {len(df.columns)} столбцов.", parse_mode='HTML')
    except Exception as e:
        print(f"[{datetime.now()}] ⚠️ Ошибка при генерации отчета: {e}")

# === Главный запуск ===
if __name__ == '__main__':
    # Запускаем Flask-сервер в отдельном потоке
    threading.Thread(target=run_flask).start()

    # Настраиваем планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_for_updates, "interval", minutes=1)
    scheduler.add_job(generate_report, CronTrigger(day="10,20,30", hour=9, minute=0))
    scheduler.start()
    print("🔄 Планировщик запущен.")

    # Запускаем бота
    executor.start_polling(dp, skip_updates=True)
