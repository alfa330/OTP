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
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.dispatcher import FSMContext
from aiogram.utils.exceptions import TelegramAPIError
from flask import Flask, request, jsonify
from functools import wraps

# === Логирование =====================================================================================================
logging.basicConfig(level=logging.INFO)

# === Переменные окружения ============================================================================================
API_TOKEN = os.getenv('BOT_TOKEN')
admin = int(os.getenv('ADMIN_ID', '0'))
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SHEET_NAME = os.getenv('SHEET_NAME')
FLASK_API_KEY = os.getenv('FLASK_API_KEY')

if not API_TOKEN:
    raise Exception("Переменная окружения BOT_TOKEN обязательна.")
if not FLASK_API_KEY:
    raise Exception("Переменная окружения FLASK_API_KEY обязательна.")

FETCH_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/gviz/tq?tqx=out:csv&sheet={SHEET_NAME}"

# === Инициализация бота и диспетчера =================================================================================
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

# === Flask-сервер ====================================================================================================
app = Flask(__name__)

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if api_key and api_key == FLASK_API_KEY:
            return f(*args, **kwargs)
        else:
            return jsonify({"error": "Invalid or missing API key"}), 401
    return decorated

@app.route('/')
def index():
    return "Bot is alive!", 200

async def send_telegram_message(chat_id, message):
    """Helper function to send Telegram message asynchronously."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode='HTML'
        )
    except TelegramAPIError as te:
        logging.error(f"Telegram API error: {te}")
        raise

@app.route('/api/call_evaluation', methods=['POST'])
@require_api_key
def receive_call_evaluation():
    try:
        data = request.get_json()
        required_fields = ['evaluator', 'operator', 'month', 'call_number', 'phone_number', 'score', 'comment']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing or invalid required fields"}), 400

        # Sanitize inputs
        for field in required_fields:
            if not isinstance(data[field], (str, int, float)):
                return jsonify({"error": f"Invalid type for {field}"}), 400

        # Construct message
        message = (
            f"📞 <b>Оценка звонка</b>\n"
            f"👤 Оценивающий: <b>{data['evaluator']}</b>\n"
            f"📋 Оператор: <b>{data['operator']}</b>\n"
            f"📄 За месяц: <b>{data['month']}</b>\n"
            f"📞 Звонок: <b>№{data['call_number']}</b>\n"
            f"📱 Номер телефона: <b>{data['phone_number']}</b>\n"
            f"💯 Оценка: <b>{data['score']}</b>\n"
        )
        if data['score'] < 100 and data['comment']:
            message += f"\n💬 Комментарий: {data['comment']}\n"

        # Check if aiogram event loop is available
        if dp.loop is None:
            logging.error("aiogram event loop is not initialized")
            return jsonify({"error": "Internal server error: aiogram event loop not initialized"}), 500

        # Run async send_telegram_message in aiogram's event loop
        future = asyncio.run_coroutine_threadsafe(
            send_telegram_message(admin, message),
            dp.loop
        )
        try:
            future.result(timeout=30)  # Wait up to 30 seconds
        except asyncio.TimeoutError:
            logging.error("Telegram message sending timed out")
            return jsonify({"error": "Telegram message sending timed out"}), 500
        except Exception as e:
            logging.error(f"Error in async task: {e}")
            return jsonify({"error": f"Internal server error: {str(e)}"}), 500

        return jsonify({"status": "success"}), 200
    except Exception as e:
        logging.error(f"Error processing call evaluation: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

def run_flask():
    app.run(host='0.0.0.0', port=8080, debug=False)

# === Глобальное состояние ============================================================================================
last_hash = None

# === Классы ==========================================================================================================
class new_sv(StatesGroup):
    svname = State()
    svid = State()

class sv(StatesGroup):
    crtable = State()
    delete = State()

class SV:
    def __init__(self, name, id):
        self.name = name
        self.id = id
        self.table = ''

SVlist = {}

# Helper function to create cancel keyboard
def get_cancel_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton('Отмена ❌'))
    return kb

# Helper function to create admin keyboard
def get_admin_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Добавить СВ➕'))
    kb.insert(KeyboardButton('Убрать СВ❌'))
    return kb

# Global cancel handler
@dp.message_handler(regexp='Отмена ❌', state='*')
async def cancel_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.finish()
    kb = get_admin_keyboard() if message.from_user.id == admin else ReplyKeyboardRemove()
    await bot.send_message(
        chat_id=message.from_user.id,
        text="Действие отменено.",
        parse_mode='HTML',
        reply_markup=kb
    )
    await message.delete()

# === Команды =========================================================================================================
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await message.delete()
    if message.from_user.id == admin:
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Бобро пожаловать!</b>\nЭто бот для прослушки прослушек.",
            parse_mode='HTML',
            reply_markup=get_admin_keyboard()
        )
    else:
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        if message.from_user.id in SVlist:
            kb.add(KeyboardButton('Добавить таблицу📑'))
        await bot.send_message(
            chat_id=message.from_user.id,
            text=f"<b>Бобро пожаловать!</b>\nТвой <b>ID</b> что бы присоединиться к команде:\n\n<pre>{message.from_user.id}</pre>",
            parse_mode='HTML',
            reply_markup=kb
        )

# === Админка =========================================================================================================
@dp.message_handler(regexp='Добавить СВ➕')
async def newSv(message: types.Message):
    await bot.send_message(
        text='<b>Добавление СВ, этап</b>: 1 из 2📍\n\nФИО нового СВ🖊',
        chat_id=message.from_user.id,
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await new_sv.svname.set()

@dp.message_handler(state=new_sv.svname)
async def newSVname(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['svname'] = message.text
    await message.answer(
        text=f'Класс, ФИО - <b>{message.text}</b>\n\n<b>Добавление СВ, этап</b>: 2 из 2📍\n\nНапишите <b>ID</b> нового СВ🆔',
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await new_sv.next()
    await message.delete()

@dp.message_handler(state=new_sv.svid)
async def newSVid(message: types.Message, state: FSMContext):
    try:
        sv_id = int(message.text)
        async with state.proxy() as data:
            data['svid'] = sv_id
        kb_sv = ReplyKeyboardMarkup(resize_keyboard=True)
        kb_sv.add(KeyboardButton('Добавить таблицу📑'))
        await bot.send_message(
            chat_id=sv_id,
            text=f"Принятие в команду прошло успешно <b>успешно✅</b>\n\nОсталось отправить таблицу вашей группы. Нажмите <b>Добавить таблицу📑</b> что бы сделать это.",
            parse_mode='HTML',
            reply_markup=kb_sv
        )
        SVlist[sv_id] = SV(data['svname'], sv_id)
        await message.answer(
            text=f'Класс, ID - <b>{message.text}</b>\n\nДобавление СВ прошло <b>успешно✅</b>. Новому супервайзеру осталось лишь отправить таблицу этого месяца👌🏼',
            parse_mode='HTML',
            reply_markup=get_admin_keyboard()
        )
        await state.finish()
    except:
        await message.answer(
            text='Ой, похоже вы отправили не тот <b>ID</b>❌\n\n<b>Пожалуйста повторите попытку!</b>',
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    await message.delete()

@dp.message_handler(regexp='Убрать СВ❌')
async def delSv(message: types.Message):
    if SVlist:
        await bot.send_message(
            text='<b>Выберете СВ которого надо исключить🖊</b>',
            chat_id=admin,
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        ikb = InlineKeyboardMarkup(row_width=1)
        for i in SVlist:
            ikb.insert(InlineKeyboardButton(text=SVlist[i].name, callback_data=str(i)))
        await bot.send_message(
            text='<b>Лист СВ:</b>',
            chat_id=admin,
            parse_mode='HTML',
            reply_markup=ikb
        )
        await sv.delete.set()
    else:
        await bot.send_message(
            text='<b>В команде нет СВ🤥</b>',
            chat_id=admin,
            parse_mode='HTML',
            reply_markup=get_admin_keyboard()
        )
    await message.delete()

@dp.callback_query_handler(state=sv.delete)
async def delSVcall(callback: types.CallbackQuery, state: FSMContext):
    SV = SVlist[int(callback.data)]
    del SVlist[int(callback.data)]
    await bot.send_message(
        text=f"Супервайзер <b>{SV.name}</b> успешно исключен из вашей команды✅",
        chat_id=admin,
        parse_mode='HTML',
        reply_markup=get_admin_keyboard()
    )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await bot.send_message(
        text=f"Вы были исключены из команды❌",
        chat_id=SV.id,
        parse_mode='HTML',
        reply_markup=ReplyKeyboardRemove()
    )
    await state.finish()

# === Работа с СВ =====================================================================================================
@dp.message_handler(regexp='Добавить таблицу📑')
async def crtablee(message: types.Message):
    await bot.send_message(
        text='<b>Отправьте вашу таблицу ОКК🖊</b>',
        chat_id=message.from_user.id,
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await sv.crtable.set()
    await message.delete()

@dp.message_handler(state=sv.crtable)
async def tableName(message: types.Message, state: FSMContext):
    try:
        if message.from_user.id not in SVlist:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="Ошибка: Вы не зарегистрированы как супервайзер! Пожалуйста, добавьтесь через администратора.",
                parse_mode="HTML",
                reply_markup=ReplyKeyboardRemove()
            )
            await state.finish()
            return
        SVlist[message.from_user.id].table = message.text
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(KeyboardButton('Добавить таблицу📑'))
        await bot.send_message(
            chat_id=message.from_user.id,
            text='<b>Таблица успешно получена✅</b>',
            parse_mode='HTML',
            reply_markup=kb
        )
        try:
            await message.delete()
        except Exception as e:
            print(f"Ошибка при удалении сообщения: {e}")
        await state.finish()
    except Exception as e:
        print(f"Ошибка в tableName: {e}")
        await bot.send_message(
            chat_id=message.from_user.id,
            text="Произошла ошибка при обработке таблицы. Попробуйте снова или свяжитесь с администратора.",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )

# === Работа с таблицей ===============================================================================================
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
            logging.info(f"[{now}] No changes in spreadsheet data.")
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

# === Главный запуск ==================================================================================================
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_for_updates, "interval", minutes=1)
    scheduler.add_job(generate_report, CronTrigger(day="10,20,30", hour=9, minute=0))
    scheduler.start()
    print("🔄 Планировщик запущен.")
    executor.start_polling(dp, skip_updates=True)