import logging
import os
import threading
import asyncio
import requests
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from io import BytesIO
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.dispatcher import FSMContext
from flask import Flask, request, jsonify
from flask_cors import CORS
from functools import wraps
from openpyxl import load_workbook
import re
import xlsxwriter
import json
from concurrent.futures import ThreadPoolExecutor
from database import db
import uuid
from passlib.hash import pbkdf2_sha256

# === Логирование =====================================================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === Переменные окружения =========================================================================================
API_TOKEN = os.getenv('BOT_TOKEN')
FLASK_API_KEY = os.getenv('FLASK_API_KEY')
admin = int(os.getenv('ADMIN_ID', '0'))
ADMIN_LOGIN = os.getenv('ADMIN_LOGIN', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')

if not API_TOKEN:
    raise Exception("Переменная окружения BOT_TOKEN обязательна.")
if not FLASK_API_KEY:
    raise Exception("Переменная окружения FLASK_API_KEY обязательна.")

# === Инициализация бота и диспетчера =============================================================================
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

# === Блокировки ==================================================================================================
report_lock = threading.Lock()
executor_pool = ThreadPoolExecutor(max_workers=4)

# === Flask-сервер ================================================================================================
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": ["https://alfa330.github.io", "http://localhost:3000"]}})

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if api_key and api_key == FLASK_API_KEY:
            return f(*args, **kwargs)
        else:
            logging.warning(f"Invalid or missing API key: {api_key}")
            return jsonify({"error": "Invalid or missing API key", "provided_key": api_key}), 401
    return decorated

@app.route('/')
def index():
    return "Bot is alive!", 200

@app.route('/api/health', methods=['GET'])
def health_check():
    try:
        # Test database connectivity
        with db._get_cursor() as cursor:
            cursor.execute("SELECT 1")
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        logging.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        if not data or ('key' not in data and ('login' not in data or 'password' not in data)):
            return jsonify({"error": "Missing credentials"}), 400
        
        if 'login' in data and 'password' in data:
            # Аутентификация по логину/паролю
            login = data['login']
            password = data['password']
            user = db.get_user_by_login(login)
            if user and db.verify_password(user[0], password):
                return jsonify({
                    "status": "success", 
                    "role": user[3],
                    "id": user[0],
                    "name": user[2],
                    "telegram_id": user[1],
                    "apiKey": FLASK_API_KEY  # Return API key for frontend
                })
        
        return jsonify({"error": "Invalid credentials"}), 401
    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/sv_list', methods=['GET'])
@require_api_key
def get_sv_list():
    try:
        # Verify database connectivity
        with db._get_cursor() as cursor:
            cursor.execute("SELECT 1")
        supervisors = db.get_supervisors()
        logging.info(f"Fetched {len(supervisors)} supervisors")
        sv_data = [{"id": sv[0], "name": sv[1], "table": sv[2]} for sv in supervisors]
        return jsonify({"status": "success", "sv_list": sv_data})
    except Exception as e:
        logging.error(f"Error fetching SV list: {e}", exc_info=True)
        return jsonify({"error": f"Failed to fetch supervisors: {str(e)}"}), 500

@app.route('/api/call_evaluations', methods=['GET'])
def get_call_evaluations():
    try:
        operator_id = request.args.get('operator_id')
        if not operator_id:
            return jsonify({"error": "Missing operator_id parameter"}), 400
        operator_id = int(operator_id)
        evaluations = db.get_call_evaluations(operator_id)
        return jsonify({"status": "success", "evaluations": evaluations})
    except Exception as e:
        logging.error(f"Error fetching evaluations: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/change_sv_table', methods=['POST'])
@require_api_key
def change_sv_table():
    try:
        data = request.get_json()
        required_fields = ['id', 'table_url']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        sv_id = int(data['id'])
        table_url = data['table_url']
        
        sheet_name, operators, error = extract_fio_and_links(table_url)
        if error:
            return jsonify({"error": error}), 400
        
        db.update_user_table(sv_id, table_url)
        
        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": db.get_user(id=sv_id)[1],
            "text": f"Таблица успешно обновлена администратором <b>успешно✅</b>",
            "parse_mode": "HTML"
        }
        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code != 200:
            error_detail = response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error: {error_detail}")
        
        return jsonify({
            "status": "success",
            "message": "Table updated",
            "operators": operators
        })
    except Exception as e:
        logging.error(f"Error updating SV table: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/add_sv', methods=['POST'])
@require_api_key
def add_sv():
    try:
        data = request.get_json()
        required_fields = ['name', 'telegram_id']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        name = data['name']
        telegram_id = int(data['telegram_id'])
        
        # Проверяем существование пользователя
        if db.get_user(telegram_id=telegram_id):
            return jsonify({"error": "SV with this Telegram ID already exists"}), 400
        
        # Создаем супервайзера
        sv_id = db.create_user(
            telegram_id=telegram_id,
            name=name,
            role='sv'
        )
        
        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": telegram_id,
            "text": f"Принятие в команду прошло успешно <b>успешно✅</b>",
            "parse_mode": "HTML"
        }
        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code != 200:
            error_detail = response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error: {error_detail}")
        
        return jsonify({"status": "success", "message": f"SV {name} added", "id": sv_id})
    except Exception as e:
        logging.error(f"Error adding SV: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/remove_sv', methods=['POST'])
@require_api_key
def remove_sv():
    try:
        data = request.get_json()
        if not data or 'id' not in data:
            return jsonify({"error": "Missing ID field"}), 400

        sv_id = int(data['id'])
        user = db.get_user(id=sv_id)
        if not user or user[3] != 'sv':
            return jsonify({"error": "SV not found"}), 404
        
        telegram_id = user[1]
        name = user[2]
        
        # Удаление пользователя
        with db._get_cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s", (sv_id,))
        
        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": telegram_id,
            "text": f"Вы были исключены из команды❌",
            "parse_mode": "HTML"
        }
        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code != 200:
            error_detail = response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error: {error_detail}")
        
        return jsonify({"status": "success", "message": f"SV {name} removed"})
    except Exception as e:
        logging.error(f"Error removing SV: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/sv/data', methods=['GET'])
def get_sv_data():
    try:
        user_id = request.args.get('id')
        if not user_id:
            return jsonify({"error": "Missing ID parameter"}), 400
        user_id = int(user_id)
        
        user = db.get_user(id=user_id)
        if not user or user[3] not in ['sv', 'operator']:
            return jsonify({"error": "User not found"}), 404
        
        table_url = user[9]
        operators = []
        error = None
        if table_url:
            sheet_name, operators, error = extract_fio_and_links(table_url)
            if error:
                return jsonify({"error": error}), 400
                
        return jsonify({
            "status": "success",
            "name": user[2],
            "table": table_url,
            "operators": operators
        })
    except Exception as e:
        logging.error(f"Error fetching SV data: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/notify_sv', methods=['POST'])
@require_api_key
def notify_supervisor():
    try:
        data = request.get_json()
        required_fields = ['sv_id', 'operator_name']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        sv_id = int(data['sv_id'])
        operator_name = data['operator_name']
        
        current_week = get_current_week_of_month()
        expected_calls = get_expected_calls(current_week)
        
        user = db.get_user(id=sv_id)
        if not user or user[3] != 'sv':
            return jsonify({"error": "SV not found"}), 404

        notification_text = (
            f"⚠️ <b>Требуется внимание!</b>\n\n"
            f"У оператора <b>{operator_name}</b> недостаточно прослушанных звонков.\n"
            f"Текущая неделя: {current_week}\n"
            f"Ожидается: {expected_calls} звонков (по 5 в неделю)\n\n"
            f"Пожалуйста, проверьте и прослушайте недостающие звонки."
        )
        
        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": user[1],
            "text": notification_text,
            "parse_mode": "HTML"
        }
        response = requests.post(telegram_url, json=payload, timeout=10)
        
        if response.status_code != 200:
            error_detail = response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error: {error_detail}")
            return jsonify({"error": f"Failed to send Telegram message: {error_detail}"}), 500

        return jsonify({"status": "success", "message": f"Notification sent to {user[2]}"}), 200
    except Exception as e:
        logging.error(f"Error in notify_supervisor: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/sv_operators', methods=['GET'])
@require_api_key
def get_sv_operators():
    try:
        sv_id = request.args.get('sv_id')
        if not sv_id:
            return jsonify({"error": "Missing sv_id parameter"}), 400
        
        sv_id = int(sv_id)
        user = db.get_user(id=sv_id)
        if not user or user[3] != 'sv':
            return jsonify({"error": "SV not found"}), 404
        
        table_url = user[6]  # table_url stored in 7th column
        operators = []
        error = None
        
        if table_url:
            sheet_name, operators, error = extract_fio_and_links(table_url)
            if error:
                return jsonify({"error": error}), 400
        
        current_week = get_current_week_of_month()
        expected_calls = get_expected_calls(current_week)
        
        operators_with_issues = []
        for op in operators:
            call_count = op.get('call_count', 0)
            if call_count in [None, "#DIV/0!"]:
                call_count = 0
            else:
                try:
                    call_count = int(call_count)
                except (ValueError, TypeError):
                    call_count = 0
            
            if call_count < expected_calls:
                operators_with_issues.append({
                    'name': op.get('name', ''),
                    'call_count': call_count,
                    'expected_calls': expected_calls,
                    'avg_score': op.get('avg_score', None)
                })
        
        return jsonify({
            "status": "success",
            "operators": operators,
            "operators_with_issues": operators_with_issues,
            "current_week": current_week,
            "expected_calls": expected_calls
        })
    except Exception as e:
        logging.error(f"Error fetching SV operators: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/generate_report', methods=['GET'])
@require_api_key
def handle_generate_report():
    try:
        if not report_lock.acquire(blocking=False):
            return jsonify({"error": "Report generation is already in progress"}), 429
            
        executor_pool.submit(sync_generate_weekly_report)
        return jsonify({"status": "success", "message": "Report generation started"})
    except Exception as e:
        logging.error(f"Error in generate_report: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/sv/update_table', methods=['POST'])
def update_sv_table():
    try:
        data = request.get_json()
        required_fields = ['id', 'table_url']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        user_id = int(data['id'])
        table_url = data['table_url']
        
        user = db.get_user(id=user_id)
        if not user or user[3] not in ['sv', 'operator']:
            return jsonify({"error": "User not found"}), 404
        
        sheet_name, operators, error = extract_fio_and_links(table_url)
        if error:
            return jsonify({"error": error}), 400
        
        db.update_user_table(user_id = user_id, scores_table_url = table_url)
        
        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": user[1],
            "text": f"Таблица успешно обновлена <b>успешно✅</b>",
            "parse_mode": "HTML"
        }
        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code != 200:
            error_detail = response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error: {error_detail}")
        
        return jsonify({"status": "success", "message": "Table updated", "operators": operators})
    except Exception as e:
        logging.error(f"Error updating table: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/call_evaluation', methods=['POST'])
@require_api_key
def receive_call_evaluation():
    try:
        data = request.get_json()
        required_fields = ['evaluator', 'operator', 'call_number', 'phone_number', 'score', 'comment']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing or invalid required fields"}), 400

        # Set month to current YYYY-MM if not provided
        month = datetime.now().strftime('%Y-%m')

        # Find evaluator and operator by name
        evaluator = db.get_user(name=data['evaluator'])
        operator = db.get_user(name=data['operator'])

        if not evaluator or not operator:
            return jsonify({"error": "Evaluator or operator not found"}), 404

        # Check for existing call evaluation with same call_number and month
        existing_calls = db.get_call_evaluations(operator_id=operator[0], month=month)
        is_correction = any(call['call_number'] == data['call_number'] for call in existing_calls)

        # Add call evaluation (history is preserved as new entry)
        db.add_call_evaluation(
            evaluator_id=evaluator[0],
            operator_id=operator[0],
            month=month,
            call_number=data['call_number'],
            phone_number=data['phone_number'],
            score=data['score'],
            comment=data['comment']
        )

        # Construct Telegram message
        message = (
            f"📞 <b>Оценка звонка{' (корректировка)' if is_correction else ''}</b>\n"
            f"👤 Оценивающий: <b>{evaluator[2]}</b>\n"
            f"📋 Оператор: <b>{operator[2]}</b>\n"
            f"📄 За месяц: <b>{month}</b>\n"
            f"📞 Звонок: <b>№{data['call_number']}</b>\n"
            f"📱 Номер телефона: <b>{data['phone_number']}</b>\n"
            f"💯 Оценка: <b>{data['score']}</b>\n"
        )
        if data['score'] < 100 and data['comment']:
            message += f"\n💬 Комментарий: \n{data['comment']}\n"

        # Send notification to admin
        admin = db.get_user(role='admin')
        if admin:
            telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
            payload = {
                "chat_id": admin[1],
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(telegram_url, json=payload, timeout=10)
            if response.status_code != 200:
                error_detail = response.json().get('description', 'Unknown error')
                logging.error(f"Telegram API error: {error_detail}")

        return jsonify({"status": "success"}), 200
    except Exception as e:
        logging.error(f"Error processing call evaluation: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/add_operator', methods=['POST'])
@require_api_key
def add_operator():
    try:
        data = request.get_json()
        required_fields = ['name', 'telegram_id', 'supervisor_id']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        name = data['name']
        telegram_id = int(data['telegram_id'])
        supervisor_id = int(data['supervisor_id'])
        
        # Проверяем существование пользователя
        if db.get_user(telegram_id=telegram_id):
            return jsonify({"error": "Operator with this Telegram ID already exists"}), 400
        
        # Создаем оператора
        operator_id = db.create_user(
            telegram_id=telegram_id,
            name=name,
            role='operator',
            supervisor_id=supervisor_id
        )
        
        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": telegram_id,
            "text": f"Вы добавлены как оператор в команду <b>успешно✅</b>",
            "parse_mode": "HTML"
        }
        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code != 200:
            error_detail = response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error: {error_detail}")
        
        return jsonify({"status": "success", "message": f"Operator {name} added", "id": operator_id})
    except Exception as e:
        logging.error(f"Error adding operator: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

def run_flask():
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)), debug=False, use_reloader=False)

# === Классы =====================================================================================================
class new_sv(StatesGroup):
    svname = State()
    svid = State()

class new_operator(StatesGroup):
    opname = State()
    opid = State()
    svselect = State()

class sv(StatesGroup):
    crtable = State()
    delete = State()
    delete_operator = State()  # Новое состояние
    verify_table = State()
    select_direction= State()
    view_evaluations = State()
    change_table = State()

class ChangeCredentials(StatesGroup):
    waiting_for_value = State()
    waiting_for_new_login = State()
    waiting_for_new_password = State()
    waiting_for_current_password = State()
    

class Auth(StatesGroup):
    login = State()
    password = State()

MAX_LOGIN_ATTEMPTS = 3

def get_current_week_of_month():
    today = datetime.now()
    week_number = (today.day - 1) // 7 + 1
    return week_number

def get_expected_calls(week_number):
    return week_number * 5

def get_cancel_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(KeyboardButton('Отмена ❌'))
    return kb

def get_admin_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Редактор СВ📝'))
    kb.insert(KeyboardButton('Операторы👷'))
    kb.add(KeyboardButton('Данные📈'))
    kb.add(KeyboardButton('Доступ🔑'))
    return kb

def get_data_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Оценки📊'))
    kb.insert(KeyboardButton('Часы⏱️'))
    kb.add(KeyboardButton('Назад 🔙'))
    return kb

def get_evaluations_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Отчет за месяц📅'))
    kb.add(KeyboardButton('Назад 🔙'))
    return kb

def get_sv_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Добавить таблицу оценок📑'))
    kb.add(KeyboardButton('Добавить таблицу часов📊'))
    kb.add(KeyboardButton('Управление операторами🔑'))
    kb.add(KeyboardButton('Доступ🔑'))
    return kb

def get_verify_keyboard():
    ikb = InlineKeyboardMarkup(row_width=2)
    ikb.add(
        InlineKeyboardButton("Да ✅", callback_data="verify_yes"),
        InlineKeyboardButton("Нет ❌", callback_data="verify_no")
    )
    return ikb

def get_direction_keyboard():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("Чат 📱", callback_data="dir_chat"),
        types.InlineKeyboardButton("Модератор 🛡️", callback_data="dir_moderator"),
        types.InlineKeyboardButton("Линия 📞", callback_data="dir_line")
    )
    return keyboard

def get_editor_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Добавить СВ➕'))
    kb.insert(KeyboardButton('Убрать СВ❌'))
    kb.add(KeyboardButton('Изменить таблицу СВ🔄'))
    kb.add(KeyboardButton('Назад 🔙'))
    return kb

def get_operators_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Добавить оператора👷‍♂️'))
    kb.insert(KeyboardButton('Убрать оператора❌'))
    kb.add(KeyboardButton('Назад 🔙'))
    return kb

def get_operator_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Моя статистика📊'))
    kb.add(KeyboardButton('Мои оценки📝'))
    kb.add(KeyboardButton('Доступ🔑'))
    return kb

@dp.message_handler(regexp='Отмена ❌', state='*')
async def cancel_handler(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.finish()
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        kb = get_admin_keyboard()
    else:
        kb = ReplyKeyboardRemove()
    await bot.send_message(
        chat_id=message.from_user.id,
        text="Действие отменено.",
        parse_mode='HTML',
        reply_markup=kb
    )
    await message.delete()

# === Команды ====================================================================================================
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await message.delete()
    user = db.get_user(telegram_id=message.from_user.id)
    
    if user:
        if user[3] == 'admin':
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>Бобро пожаловать!</b>\nЭто бот для прослушки прослушек.",
                parse_mode='HTML',
                reply_markup=get_admin_keyboard()
            )
        elif user[3] == 'sv':
            await bot.send_message(
                chat_id=message.from_user.id,
                text=f"<b>Бобро пожаловать, {user[2]}!</b>",
                parse_mode='HTML',
                reply_markup=get_sv_keyboard()
            )
        elif user[3] == 'operator':
            await bot.send_message(
                chat_id=message.from_user.id,
                text=f"<b>Бобро пожаловать, оператор {user[2]}!</b>",
                parse_mode='HTML'
            )
    else:
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(KeyboardButton('Вход👤'))
        await bot.send_message(
            chat_id=message.from_user.id,
            text=f"<b>Бобро пожаловать!</b>\n\nНажмите <b>Вход👤</b>, чтобы подключиться к <b>OTP dashboard</b>. 👥",
            parse_mode='HTML',
            reply_markup=kb
        )

@dp.message_handler(regexp='Вход👤')
async def start_auth(message: types.Message):
    await message.delete()
    await message.answer(
        "<b>Введите ваш логин:</b>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await Auth.login.set()
    # Инициализация счетчика попыток
    await dp.storage.set_data(chat=message.chat.id, data={'attempts': 0})

@dp.message_handler(state=Auth.login)
async def process_login(message: types.Message, state: FSMContext):
    login = message.text.strip()
    try:
        user = db.get_user_by_login(login)
        if not user:
            await message.delete()
            await message.answer(
                "<b>Неверный логин. Попробуйте снова🔁</b>",
                parse_mode='HTML'
            )
            return

        # Сохраняем данные пользователя в состоянии
        await state.update_data({
            'user': {
                'id': user[0],
                'telegram_id': user[1],
                'name': user[2],
                'role': user[3],
                'direction': user[4],
                'hire_date': user[5],
                'supervisor_id': user[6],
                'login': user[7]
            }
        })
        await message.delete()
        await message.answer(
            "<b>Введите ваш пароль:</b>",
            parse_mode='HTML'
        )
        await Auth.password.set()
    except Exception as e:
        logging.error(f"Ошибка при обработке логина {login}: {str(e)}")
        await message.delete()
        await message.answer(
            "<b>Произошла ошибка. Попробуйте снова или свяжитесь с поддержкой.</b>",
            parse_mode='HTML'
        )
        await state.finish()
        
@dp.message_handler(state=Auth.password)
async def process_password(message: types.Message, state: FSMContext):
    password = message.text.strip()
    user_data = await state.get_data()
    user = user_data.get('user')
    attempts = (await dp.storage.get_data(chat=message.chat.id)).get('attempts', 0)

    try:
        if attempts >= MAX_LOGIN_ATTEMPTS:
            await message.delete()
            await message.answer(
                "<b>Слишком много попыток. Авторизация заблокирована. Свяжитесь с поддержкой.</b>",
                parse_mode='HTML'
            )
            await state.finish()
            await dp.storage.reset_data(chat=message.chat.id)
            logging.warning(f"Превышено количество попыток авторизации для chat_id {message.chat.id}")
            return

        if not user or not db.verify_password(user['id'], password):
            attempts += 1
            await dp.storage.set_data(chat=message.chat.id, data={'attempts': attempts})
            await message.delete()
            await message.answer(
                f"<b>Неверный пароль. Осталось попыток: {MAX_LOGIN_ATTEMPTS - attempts}. Попробуйте снова.</b>",
                parse_mode='HTML'
            )
            logging.warning(f"Неверный пароль для логина {user.get('login')} (попытка {attempts})")
            return

        # Успешная авторизация
        db.update_telegram_id(user['id'], message.from_user.id)
        await message.delete()

        # Формируем приветственное сообщение в зависимости от роли
        role = user['role']
        name = user['name']
        if role == 'admin':
            await message.answer(
                "<b>Бобро пожаловать!</b>\nЭто бот для управления прослушками.",
                parse_mode='HTML',
                reply_markup=get_admin_keyboard()
            )
        elif role == 'sv':
            await message.answer(
                f"<b>Бобро пожаловать, {name}!</b>",
                parse_mode='HTML',
                reply_markup=get_sv_keyboard()
            )
        elif user[3] == 'operator':
            await message.answer(
                chat_id=message.from_user.id,
                text=f"<b>Добро пожаловать, оператор {user[2]}!</b>\n\n"
                    "Используйте кнопки ниже для просмотра вашей статистики.",
                parse_mode='HTML',
                reply_markup=get_operator_keyboard()
            )

        logging.info(f"Успешная авторизация: login={user['login']}, role={role}, chat_id={message.chat.id}")
        await state.finish()
        await dp.storage.reset_data(chat=message.chat.id)

    except Exception as e:
        logging.error(f"Ошибка при обработке пароля для login {user.get('login')}: {str(e)}")
        await message.delete()
        await message.answer(
            "<b>Произошла ошибка. Попробуйте снова или свяжитесь с СВ.</b>",
            parse_mode='HTML'
        )
        await state.finish()

# === Админка ===================================================================================================

@dp.message_handler(regexp='Редактор СВ📝')
async def editor_sv(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        await bot.send_message(
            chat_id=message.from_user.id,
            text='<b>Редактор супервайзеров</b>',
            parse_mode='HTML',
            reply_markup=get_editor_keyboard()
        )
    await message.delete()

@dp.message_handler(regexp='Операторы👷')
async def operators_menu(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        await bot.send_message(
            chat_id=message.from_user.id,
            text='<b>Управление операторами</b>',
            parse_mode='HTML',
            reply_markup=get_operators_keyboard()
        )
    await message.delete()

@dp.message_handler(regexp='Назад 🔙')
async def back_to_admin(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        await bot.send_message(
            chat_id=message.from_user.id,
            text='<b>Главное меню</b>',
            parse_mode='HTML',
            reply_markup=get_admin_keyboard()
        )
    await message.delete()


@dp.message_handler(regexp='Добавить СВ➕')
async def newSv(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        await bot.send_message(
            text='<b>Добавление СВ, этап</b>: 1 из 2📍\n\nФИО нового СВ🖊',
            chat_id=message.from_user.id,
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await new_sv.svname.set()
    await message.delete()

@dp.message_handler(state=new_sv.svname)
async def newSVname(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['svname'] = message.text
    
    # Генерируем случайные логин и пароль
    login = f"sv_{str(uuid.uuid4())[:8]}"
    password = str(uuid.uuid4())[:8]
    
    async with state.proxy() as data:
        data['login'] = login
        data['password'] = password
    
    await message.answer(
        text=f'<b>Данные для нового СВ:</b>\n\n'
             f'ФИО: <b>{message.text}</b>\n'
             f'Логин: <code>{login}</code>\n'
             f'Пароль: <code>{password}</code>\n\n'
             f'Передайте эти данные супервайзеру. Он сможет войти в систему и добавить '
             f'остальную информацию самостоятельно.\n\n'
             f'<b>Хотите сохранить этого супервайзера?</b>',
        parse_mode='HTML',
        reply_markup=get_verify_keyboard()
    )
    await new_sv.next()
    await message.delete()

@dp.callback_query_handler(state=new_sv.svid)
async def newSVid(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "verify_yes":
        async with state.proxy() as data:
            sv_name = data['svname']
            login = data['login']
            password = data['password']
        
        # Создаем супервайзера без telegram_id (он добавит его при первом входе)
        sv_id = db.create_user(
            telegram_id=None,  # Будет установлен при первом входе
            name=sv_name,
            role='sv',
            login=login,
            password=password
        )
        
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f'<b>Супервайзер {sv_name} успешно добавлен!</b>\n\n'
                 f'Логин: <code>{login}</code>\n'
                 f'Пароль: <code>{password}</code>\n\n'
                 f'Передайте эти данные супервайзеру для входа в систему.',
            parse_mode='HTML',
            reply_markup=get_editor_keyboard()
        )
    else:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text='Добавление супервайзера отменено.',
            parse_mode='HTML',
            reply_markup=get_editor_keyboard()
        )
    
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await state.finish()


@dp.message_handler(regexp='Добавить оператора👷‍♂️')
async def newOperator(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        await bot.send_message(
            text='<b>Добавление оператора, этап</b>: 1 из 3📍\n\nФИО нового оператора🖊',
            chat_id=message.from_user.id,
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await new_operator.opname.set()
    await message.delete()

@dp.message_handler(state=new_operator.opname)
async def newOperatorName(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['opname'] = message.text
    await message.answer(
        text=f'Класс, ФИО - <b>{message.text}</b>\n\n<b>Добавление оператора, этап</b>: 2 из 3📍\n\nНапишите <b>ID</b> нового оператора🆔',
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await new_operator.next()
    await message.delete()

@dp.message_handler(state=new_operator.opid)
async def newOperatorId(message: types.Message, state: FSMContext):
    try:
        op_id = int(message.text)
        async with state.proxy() as data:
            data['opid'] = op_id
            
        supervisors = db.get_supervisors()
        if not supervisors:
            await message.answer(
                text='Нет доступных супервайзеров! Сначала добавьте СВ.',
                parse_mode='HTML',
                reply_markup=get_cancel_keyboard()
            )
            await state.finish()
            return
            
        ikb = InlineKeyboardMarkup(row_width=1)
        for sv in supervisors:
            ikb.insert(InlineKeyboardButton(text=sv[1], callback_data=str(sv[0])))
            
        await message.answer(
            text=f'Отлично, ID - <b>{message.text}</b>\n\n<b>Добавление оператора, этап</b>: 3 из 3📍\n\nВыберите супервайзера для этого оператора:',
            parse_mode='HTML',
            reply_markup=ikb
        )
        await new_operator.next()
    except:
        await message.answer(
            text='Ой, похоже вы отправили не тот <b>ID</b>❌\n\n<b>Пожалуйста повторите попытку!</b>',
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
    await message.delete()

@dp.callback_query_handler(state=new_operator.svselect)
async def newOperatorSV(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        op_name = data['opname']
        op_id = data['opid']
        sv_id = int(callback.data)
        
        # Создаем оператора
        db.create_user(
            telegram_id=op_id,
            name=op_name,
            role='operator',
            supervisor_id=sv_id
        )
        
        await bot.send_message(
            chat_id=op_id,
            text=f"Вы добавлены как оператор в команду <b>успешно✅</b>",
            parse_mode='HTML'
        )
        
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f'Оператор <b>{op_name}</b> успешно добавлен✅',
            parse_mode='HTML',
            reply_markup=get_operators_keyboard()
        )
    await state.finish()
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.message_handler(regexp='Убрать СВ❌')
async def delSv(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        supervisors = db.get_supervisors()
        if not supervisors:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>Нет доступных супервайзеров</b>",
                parse_mode='HTML',
                reply_markup=get_editor_keyboard()
            )
            return

        ikb = InlineKeyboardMarkup(row_width=1)
        for sv_id, sv_name, _, _ in supervisors:
            ikb.insert(InlineKeyboardButton(text=sv_name, callback_data=f"delsv_{sv_id}"))
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите супервайзера для удаления</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Лист СВ:</b>",
            parse_mode='HTML',
            reply_markup=ikb
        )
        await sv.delete.set()
    await message.delete()

@dp.callback_query_handler(state=sv.delete)
async def delSVcall(callback: types.CallbackQuery, state: FSMContext):
    sv_id = int(callback.data.split('_')[1])
    user = db.get_user(id=sv_id)
    
    if user:
        with db._get_cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s", (sv_id,))
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"Супервайзер <b>{user[2]}</b> удалён!",
            parse_mode='HTML',
            reply_markup=get_editor_keyboard()
        )
    else:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="Супервайзер не найден!",
            parse_mode='HTML'
        )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await state.finish()


@dp.message_handler(regexp='Изменить таблицу СВ🔄')
async def change_sv_table(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        supervisors = db.get_supervisors()
        if not supervisors:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>Нет доступных супервайзеров</b>",
                parse_mode='HTML',
                reply_markup=get_editor_keyboard()
            )
            return

        ikb = InlineKeyboardMarkup(row_width=1)
        for sv_id, sv_name, _, _ in supervisors:
            ikb.insert(InlineKeyboardButton(text=sv_name, callback_data=f"change_table_{sv_id}"))
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите супервайзера для изменения таблицы</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Лист СВ:</b>",
            parse_mode='HTML',
            reply_markup=ikb
        )

        await sv.change_table.set()
    await message.delete()

@dp.callback_query_handler(lambda c: c.data.startswith('change_table_'), state=sv.change_table)
async def select_sv_for_table_change(callback: types.CallbackQuery, state: FSMContext):
    sv_id = int(callback.data.split('_')[2])
    async with state.proxy() as data:
        data['sv_id'] = sv_id
    user = db.get_user(id=sv_id)
    if user:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f'<b>Отправьте новую таблицу ОКК для {user[2]}🖊</b>',
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await sv.crtable.set()
    else:
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        await state.finish()

@dp.message_handler(regexp='Данные📈')
async def view_data_menu(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        await bot.send_message(
            chat_id=message.from_user.id,
            text='<b>Меню данных</b>',
            parse_mode='HTML',
            reply_markup=get_data_keyboard()
        )
    await message.delete()

@dp.message_handler(regexp='Часы⏱️')
async def view_hours_data(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        supervisors = db.get_supervisors()
        if not supervisors:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>Нет доступных супервайзеров</b>",
                parse_mode='HTML',
                reply_markup=get_admin_keyboard()
            )
            return
        
        ikb = InlineKeyboardMarkup(row_width=1)
        for sv_id, sv_name, _, _ in supervisors:
            ikb.insert(InlineKeyboardButton(text=sv_name, callback_data=f"hours_{sv_id}"))
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите супервайзера для просмотра часов операторов:</b>",
            parse_mode='HTML',
            reply_markup=ikb
        )
    await message.delete()

# Обработчик для отображения часов операторов
@dp.callback_query_handler(lambda c: c.data.startswith('hours_'))
async def show_operator_hours(callback: types.CallbackQuery):
    sv_id = int(callback.data.split('_')[1])
    user = db.get_user(id=sv_id)
    
    if not user or user[3] != 'sv':
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        return

    operators = db.get_operators_by_supervisor(sv_id)
    current_month = datetime.now().strftime('%Y-%m')
    
    message_text = f"<b>Часы операторов {user[2]} за {current_month}:</b>\n\n"
    
    for op_id, op_name, _, _, _, _ in operators:
        hours_data = db.get_hours_summary(op_id, current_month)
        if hours_data:
            hours = hours_data[0]
            message_text += (
                f"👤 <b>{op_name}</b>\n"
                f"   ⏱️ Часы работы: {hours['regular_hours']}\n"
                f"   📚 Часы тренинга: {hours['training_hours']}\n"
                f"   💸 Штрафы: {hours['fines']}\n\n"
            )
        else:
            message_text += f"👤 <b>{op_name}</b> - данные отсутствуют\n\n"
    
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=message_text,
        parse_mode='HTML'
    )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.message_handler(regexp='Оценки📊')
async def view_evaluations(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        supervisors = db.get_supervisors()
        if not supervisors:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>Нет доступных супервайзеров</b>",
                parse_mode='HTML',
                reply_markup=get_admin_keyboard()
            )
            return
        
        await bot.send_message(
                    text='<b>Выберите чьи оценки просмотреть или сгенерируйте отчет</b>',
                    chat_id=admin,
                    parse_mode='HTML',
                    reply_markup=get_evaluations_keyboard()
                )
        ikb = InlineKeyboardMarkup(row_width=1)
        for sv_id, sv_name, _, _ in supervisors:
            ikb.insert(InlineKeyboardButton(text=sv_name, callback_data=f"eval_{sv_id}"))
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Лист СВ:</b>",
            parse_mode='HTML',
            reply_markup=ikb
        )
        await sv.view_evaluations.set()
    await message.delete()

@dp.message_handler(regexp='Убрать оператора❌')
async def remove_operator_menu(message: types.Message, state: FSMContext):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        # Use the cursor within a with block
        with db._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, s.name 
                FROM users u
                LEFT JOIN users s ON u.supervisor_id = s.id
                WHERE u.role = 'operator'
            """)
            operators = cursor.fetchall()
        
        if not operators:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>Нет доступных операторов</b>",
                parse_mode='HTML',
                reply_markup=get_operators_keyboard()
            )
            return

        ikb = InlineKeyboardMarkup(row_width=1)
        for op_id, op_name, sv_name in operators:
            supervisor = f" ({sv_name})" if sv_name else ""
            ikb.insert(InlineKeyboardButton(
                text=f"{op_name}{supervisor}",
                callback_data=f"delop_{op_id}"
            ))
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите оператора для удаления</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )

        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Лист операторов:</b>",
            parse_mode='HTML',
            reply_markup=ikb
        )

        await state.set_state("delete_operator")
    await message.delete()

@dp.callback_query_handler(lambda c: c.data.startswith('delop_'), state="delete_operator")
async def remove_operator_callback(callback: types.CallbackQuery, state: FSMContext):
    op_id = int(callback.data.split('_')[1])
    user = db.get_user(id=op_id)
    
    if user and user[3] == 'operator':
        with db._get_cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE id = %s", (op_id,))
        
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"Оператор <b>{user[2]}</b> удалён!",
            parse_mode='HTML',
            reply_markup=get_operators_keyboard()
        )
    else:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="Оператор не найден!",
            parse_mode='HTML'
        )
    
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await state.finish()


@dp.message_handler(regexp='Отчет за месяц📅', state=sv.view_evaluations)
async def handle_monthly_report(message: types.Message, state: FSMContext):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        try:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="📊 Генерирую отчет за месяц...",
                parse_mode='HTML'
            )
            await generate_weekly_report()
            await bot.send_message(
                chat_id=message.from_user.id,
                text="✅ Отчет успешно сгенерирован и отправлен!",
                parse_mode='HTML',
                reply_markup=get_admin_keyboard()
            )
        except Exception as e:
            logging.error(f"Ошибка генерации отчета: {e}")
            await bot.send_message(
                chat_id=message.from_user.id,
                text=f"❌ Ошибка генерации отчета: {str(e)}",
                parse_mode='HTML',
                reply_markup=get_admin_keyboard()
            )
    else:
        await bot.send_message(
            chat_id=message.from_user.id,
            text="⚠️ Команда доступна только администратору",
            parse_mode='HTML'
        )
    await state.finish()
    await message.delete()

@dp.message_handler(regexp='Назад 🔙', state=sv.view_evaluations)
async def back_from_evaluations(message: types.Message, state: FSMContext):
    await state.finish()
    await back_to_admin(message)


@dp.callback_query_handler(lambda c: c.data.startswith('eval_'), state=sv.view_evaluations)
async def show_sv_evaluations(callback: types.CallbackQuery, state: FSMContext):
    sv_id = int(callback.data.split('_')[1])
    user = db.get_user(id=sv_id)
    
    if not user or user[3] != 'sv':
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        return

    # Получаем операторов этого СВ
    operators = db.get_operators_by_supervisor(sv_id)
    current_week = get_current_week_of_month()
    expected_calls = get_expected_calls(current_week)
    
    message_text = (
        f"<b>Оценки {user[2]} (неделя {current_week}):</b>\n"
        f"<i>Ожидается: {expected_calls} звонков (по 5 в неделю)</i>\n\n"
    )
    
    operators_with_issues = []
    
    for op_id, op_name, table_url in operators:
        # Для каждого оператора получаем статистику звонков
        with db._get_cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*), AVG(score) 
                FROM calls 
                WHERE operator_id = %s
            """, (op_id,))
            result = cursor.fetchone()
        
        call_count = result[0] or 0
        avg_score = result[1] or 0
        
        # Форматируем данные для отображения
        if call_count < expected_calls:
            operators_with_issues.append({
                'name': op_name,
                'call_count': call_count,
                'expected': expected_calls
            })
            
        # Добавляем в общее сообщение
        message_text += f"👤 {op_name}\n"
        message_text += f"   📞 Звонков: {call_count}/{expected_calls}\n"
        message_text += f"   ⭐ Средний балл: {avg_score:.2f}\n\n"
    
    # Клавиатура для уведомлений
    ikb = InlineKeyboardMarkup(row_width=1)
    for op in operators_with_issues:
        ikb.add(InlineKeyboardButton(
            text=f"Уведомить о {op['name']}",
            callback_data=f"notify_{sv_id}_{op['name']}"
        ))
    
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=message_text,
        parse_mode='HTML',
        reply_markup=ikb
    )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith('notify_'))
async def notify_supervisor_handler(callback: types.CallbackQuery):
    try:
        _, sv_id, op_name = callback.data.split('_', 2)
        sv_id = int(sv_id)
        
        user = db.get_user(id=sv_id)
        if not user or user[3] != 'sv':
            await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
            return

        current_week = get_current_week_of_month()
        expected_calls = get_expected_calls(current_week)
        
        notification_text = (
            f"⚠️ <b>Требуется внимание!</b>\n\n"
            f"У оператора <b>{op_name}</b> недостаточно прослушанных звонков.\n"
            f"Текущая неделя: {current_week}\n"
            f"Ожидается: {expected_calls} звонков\n\n"
            f"Пожалуйста, проверьте и прослушайте недостающие звонки."
        )
        
        await bot.send_message(
            chat_id=user[1],
            text=notification_text,
            parse_mode='HTML'
        )
        
        await bot.answer_callback_query(
            callback.id,
            text=f"Уведомление отправлено СВ {user[2]}",
            show_alert=False
        )
    except Exception as e:
        logging.error(f"Ошибка уведомления: {e}")
        await bot.answer_callback_query(
            callback.id,
            text="Ошибка отправки уведомления",
            show_alert=True
        )


# === Супервайзерам =============================================================================================

@dp.message_handler(regexp='Управление операторами🔑')
async def manage_operators_credentials(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'sv':
        operators = db.get_operators_by_supervisor(user[0])
        if not operators:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>У вас нет операторов</b>",
                parse_mode='HTML',
                reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton('Назад 🔙'))
            )
            return

        ikb = InlineKeyboardMarkup(row_width=1)
        for op_id, op_name, _, _, _, _ in operators:
            ikb.insert(InlineKeyboardButton(text=op_name, callback_data=f"cred_{op_id}"))
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите оператора для управления доступом:</b>",
            parse_mode='HTML',
            reply_markup=ikb
        )
    await message.delete()

@dp.callback_query_handler(lambda c: c.data.startswith('cred_'))
async def operator_credentials_menu(callback: types.CallbackQuery):
    operator_id = int(callback.data.split('_')[1])
    user = db.get_user(telegram_id=callback.from_user.id)
    
    if user and user[3] == 'sv':
        operator = db.get_operator_credentials(operator_id, user[0])
        if not operator:
            await bot.answer_callback_query(callback.id, text="Оператор не найден")
            return

        ikb = InlineKeyboardMarkup(row_width=2)
        ikb.add(
            InlineKeyboardButton("Изменить логин", callback_data=f"chlogin_{operator_id}"),
            InlineKeyboardButton("Изменить пароль", callback_data=f"chpass_{operator_id}")
        )
        ikb.add(InlineKeyboardButton("Назад", callback_data="cred_back"))

        await bot.edit_message_text(
            chat_id=callback.from_user.id,
            message_id=callback.message.message_id,
            text=f"<b>Управление доступом оператора</b>\n\nЛогин: <code>{operator[1]}</code>",
            parse_mode='HTML',
            reply_markup=ikb
        )

@dp.callback_query_handler(lambda c: c.data.startswith('chlogin_'))
async def change_login_start(callback: types.CallbackQuery, state: FSMContext):
    operator_id = int(callback.data.split('_')[1])
    await state.update_data(operator_id=operator_id, action="login")
    await bot.edit_message_text(
        chat_id=callback.from_user.id,
        message_id=callback.message.message_id,
        text="Введите новый логин для оператора:",
        reply_markup=None
    )
    await ChangeCredentials.waiting_for_value.set()

@dp.callback_query_handler(lambda c: c.data.startswith('chpass_'))
async def change_password_start(callback: types.CallbackQuery, state: FSMContext):
    operator_id = int(callback.data.split('_')[1])
    await state.update_data(operator_id=operator_id, action="password")
    await bot.edit_message_text(
        chat_id=callback.from_user.id,
        message_id=callback.message.message_id,
        text="Введите новый пароль для оператора:",
        reply_markup=None
    )
    await ChangeCredentials.waiting_for_value.set()

@dp.message_handler(state=ChangeCredentials.waiting_for_value)
async def process_credential_change(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    operator_id = user_data['operator_id']
    action = user_data['action']
    value = message.text.strip()
    user = db.get_user(telegram_id=message.from_user.id)

    try:
        if action == "login":
            success = db.update_operator_login(operator_id, user[0], value)
            msg = f"Логин оператора изменён на: <code>{value}</code>"
        else:
            success = db.update_operator_password(operator_id, user[0], value)
            msg = "Пароль оператора успешно изменён"
        
        if success:
            operator = db.get_user(id=operator_id)
            await bot.send_message(
                chat_id=message.from_user.id,
                text=f"✅ {msg}\n\nОператор: {operator[2]}",
                parse_mode='HTML'
            )
        else:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="❌ Не удалось изменить данные оператора",
                parse_mode='HTML'
            )
    except Exception as e:
        logging.error(f"Error changing operator {action}: {e}")
        await bot.send_message(
            chat_id=message.from_user.id,
            text=f"❌ Ошибка при изменении: {str(e)}",
            parse_mode='HTML'
        )
    
    await state.finish()
    await message.delete()

@dp.callback_query_handler(lambda c: c.data == "cred_back", state="*")
async def credentials_back(callback: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await manage_operators_credentials(callback.message)


@dp.message_handler(regexp='Добавить таблицу оценок📑')
async def crtablee(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'sv':
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
        user_id = message.from_user.id
        user = db.get_user(telegram_id=user_id)
        is_admin_changing = await state.get_state() == sv.crtable.state and user and user[3] == 'admin'
        
        if not is_admin_changing and (not user or user[3] != 'sv'):
            await bot.send_message(
                chat_id=user_id,
                text="Ошибка: Вы не зарегистрированы как супервайзер! Пожалуйста, добавьтесь через администратора.",
                parse_mode="HTML",
                reply_markup=types.ReplyKeyboardRemove()
            )
            await state.finish()
            return

        sheet_name, operators, error = extract_fio_and_links(message.text)
        
        if error:
            await bot.send_message(
                chat_id=user_id,
                text=f"{error}\n\n<b>Пожалуйста, отправьте корректную ссылку на таблицу.</b>",
                parse_mode="HTML",
                reply_markup=get_cancel_keyboard()
            )
            return

        async with state.proxy() as data:
            data['table_url'] = message.text
            data['operators'] = operators
            data['sheet_name'] = sheet_name
            if is_admin_changing or user[3] == 'sv':
                data.setdefault('sv_id', user_id)

        message_text = f"<b>Название листа:</b> {sheet_name}\n\n<b>ФИО операторов:</b>\n"
        for op in operators:
            if op['link']:
                message_text += f"👤 {op['name']} → <a href='{op['link']}'>Ссылка</a>\n"
            else:
                message_text += f"👤 {op['name']} → Ссылка отсутствует\n"
        message_text += "\n<b>Это все ваши операторы?</b>"

        await bot.send_message(
            chat_id=user_id,
            text=message_text,
            parse_mode="HTML",
            reply_markup=get_verify_keyboard(),
            disable_web_page_preview=True
        )
        await sv.verify_table.set()
        await message.delete()
    except Exception as e:
        logging.error(f"Ошибка в tableName: {e}")
        await bot.send_message(
            chat_id=message.from_user.id,
            text="Произошла ошибка при обработке таблицы. Попробуйте снова или свяжитесь с администратором.",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )

@dp.callback_query_handler(state=sv.verify_table)
async def verify_table(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        table_url = data.get('table_url')
        sv_id = data.get('sv_id')
        operators = data.get('operators')
        sheet_name = data.get('sheet_name')
    
    user = db.get_user(telegram_id=sv_id)
    if not user:
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        await state.finish()
        return
    
    if callback.data == "verify_yes":
        async with state.proxy() as data:
            data['operators'] = operators
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="Выберите направление для операторов:",
            reply_markup=get_direction_keyboard()
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await sv.select_direction.set()
    elif callback.data == "verify_no":
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f'<b>Отправьте корректную таблицу ОКК для {user[2]}🖊</b>',
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await sv.crtable.set()

@dp.callback_query_handler(state=sv.select_direction)
async def select_direction(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        table_url = data.get('table_url')
        sv_id = data.get('sv_id')
        operators = data.get('operators')
    
    direction_map = {
        "dir_chat": "chat",
        "dir_moderator": "moderator",
        "dir_line": "line"
    }
    direction = direction_map.get(callback.data)
    
    if not direction:
        await bot.answer_callback_query(callback.id, text="Ошибка: Неверное направление")
        return
    
    user = db.get_user(telegram_id=sv_id)
    if not user:
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        await state.finish()
        return
    
    db.update_user_table(user[0], scores_table_url=table_url)
    
    for op in operators:
        db.create_user(
            telegram_id=None,
            name=op['name'],
            role='operator',
            direction=direction,
            supervisor_id=user[0]
        )
    
    await bot.send_message(
        chat_id=callback.from_user.id,
        text=f'<b>Таблица оценок сохранена, операторы добавлены/обновлены с направлением "{direction}"✅</b>',
        parse_mode='HTML',
        reply_markup=get_sv_keyboard()
    )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await state.finish()


@dp.message_handler(regexp='Добавить таблицу часов📊')
async def add_hours_table(message: types.Message, state: FSMContext):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'sv':
        await bot.send_message(
            text='<b>Отправьте ссылку на таблицу с часами работы операторов:</b>',
            chat_id=message.from_user.id,
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await state.set_state("waiting_for_hours_table")

# Обработчик для сохранения таблицы часов
@dp.message_handler(state="waiting_for_hours_table")
async def save_hours_table(message: types.Message, state: FSMContext):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'sv':
        try:
            db.update_user_table(user[0], hours_table_url=message.text)
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>Таблица часов успешно сохранена!</b>",
                parse_mode='HTML',
                reply_markup=get_sv_keyboard()
            )
        except Exception as e:
            await bot.send_message(
                chat_id=message.from_user.id,
                text=f"<b>Ошибка при сохранении таблицы: {str(e)}</b>",
                parse_mode='HTML'
            )
        await state.finish()


@dp.message_handler(regexp='Доступ🔑')
async def change_credentials_menu(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user:
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(KeyboardButton('Изменить логин'))
        kb.add(KeyboardButton('Изменить пароль'))
        kb.add(KeyboardButton('Назад 🔙'))
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите что хотите изменить:</b>",
            parse_mode='HTML',
            reply_markup=kb
        )
    await message.delete()

@dp.message_handler(regexp='Изменить логин')
async def change_login_start(message: types.Message):
    await bot.send_message(
        chat_id=message.from_user.id,
        text="<b>Введите новый логин:</b>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await ChangeCredentials.waiting_for_new_login.set()
    await message.delete()

@dp.message_handler(state=ChangeCredentials.waiting_for_new_login)
async def process_new_login(message: types.Message, state: FSMContext):
    new_login = message.text.strip()
    if len(new_login) < 4:
        await bot.send_message(
            chat_id=message.from_user.id,
            text="Логин должен быть не менее 4 символов. Попробуйте еще раз:",
            reply_markup=get_cancel_keyboard()
        )
        return
    
    user = db.get_user(telegram_id=message.from_user.id)
    if user:
        try:
            # Проверяем, не занят ли логин
            existing_user = db.get_user_by_login(new_login)
            if existing_user and existing_user[0] != user[0]:
                await bot.send_message(
                    chat_id=message.from_user.id,
                    text="Этот логин уже занят. Попробуйте другой:",
                    reply_markup=get_cancel_keyboard()
                )
                return
            
            # Обновляем логин
            with db._get_cursor() as cursor:
                cursor.execute("UPDATE users SET login = %s WHERE id = %s", (new_login, user[0]))
            
            await bot.send_message(
                chat_id=message.from_user.id,
                text=f"✅ Логин успешно изменен на: <code>{new_login}</code>",
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Error changing login: {e}")
            await bot.send_message(
                chat_id=message.from_user.id,
                text="❌ Произошла ошибка при изменении логина. Попробуйте позже."
            )
    
    await state.finish()
    await message.delete()

@dp.message_handler(regexp='Изменить пароль')
async def change_password_start(message: types.Message):
    await bot.send_message(
        chat_id=message.from_user.id,
        text="<b>Введите текущий пароль для подтверждения:</b>",
        parse_mode='HTML',
        reply_markup=get_cancel_keyboard()
    )
    await ChangeCredentials.waiting_for_current_password.set()
    await message.delete()

@dp.message_handler(state=ChangeCredentials.waiting_for_current_password)
async def verify_current_password(message: types.Message, state: FSMContext):
    current_password = message.text.strip()
    user = db.get_user(telegram_id=message.from_user.id)
    
    if user and db.verify_password(user[0], current_password):
        await state.update_data(user_id=user[0])
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Введите новый пароль:</b>",
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await ChangeCredentials.waiting_for_new_password.set()
    else:
        await bot.send_message(
            chat_id=message.from_user.id,
            text="❌ Неверный текущий пароль. Попробуйте еще раз:",
            reply_markup=get_cancel_keyboard()
        )
    await message.delete()

@dp.message_handler(state=ChangeCredentials.waiting_for_new_password)
async def process_new_password(message: types.Message, state: FSMContext):
    new_password = message.text.strip()
    if len(new_password) < 6:
        await bot.send_message(
            chat_id=message.from_user.id,
            text="Пароль должен быть не менее 6 символов. Попробуйте еще раз:",
            reply_markup=get_cancel_keyboard()
        )
        return
    
    user_data = await state.get_data()
    user_id = user_data.get('user_id')
    
    if user_id:
        try:
            password_hash = pbkdf2_sha256.hash(new_password)
            with db._get_cursor() as cursor:
                cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (password_hash, user_id))
            
            await bot.send_message(
                chat_id=message.from_user.id,
                text="✅ Пароль успешно изменен!"
            )
        except Exception as e:
            logging.error(f"Error changing password: {e}")
            await bot.send_message(
                chat_id=message.from_user.id,
                text="❌ Произошла ошибка при изменении пароля. Попробуйте позже."
            )
    
    await state.finish()
    await message.delete()


# === Операторам =============================================================================================

@dp.message_handler(regexp='Моя статистика📊')
async def show_operator_stats(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'operator':
        stats = db.get_operator_stats(user[0])
        current_month = datetime.now().strftime('%B %Y')
        
        message_text = (
            f"<b>Ваша статистика за {current_month}:</b>\n\n"
            f"⏱ <b>Часы работы:</b> {stats['regular_hours']}\n"
            f"📚 <b>Часы тренинга:</b> {stats['training_hours']}\n"
            f"💸 <b>Штрафы:</b> {stats['fines']}\n\n"
            f"📞 <b>Прослушано звонков:</b> {stats['call_count']}\n"
            f"⭐ <b>Средний балл:</b> {stats['avg_score']:.2f}"
        )
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text=message_text,
            parse_mode='HTML'
        )
    await message.delete()

@dp.message_handler(regexp='Мои оценки📝')
async def show_operator_evaluations(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'operator':
        evaluations = db.get_call_evaluations(user[0])
        
        if not evaluations:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>У вас пока нет оценок за текущий месяц.</b>",
                parse_mode='HTML'
            )
            return
        
        message_text = "<b>Ваши последние оценки:</b>\n\n"
        for eval in evaluations[:5]:  # Показываем последние 5 оценок
            message_text += (
                f"📞 <b>Звонок {eval['call_number']}</b>\n"
                f"   📅 {eval['month']}\n"
                f"   📱 {eval['phone_number']}\n"
                f"   ⭐ Оценка: <b>{eval['score']}</b>\n"
            )
            if eval['comment']:
                message_text += f"   💬 Комментарий: {eval['comment']}\n"
            message_text += "\n"
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text=message_text,
            parse_mode='HTML'
        )
    await message.delete()

# Остальной код остается аналогичным предыдущей версии, но с использованием базы данных
# ... (код для удаления СВ, изменения таблиц, просмотра оценок и т.д.) ...

def extract_fio_and_links(spreadsheet_url):
    try:
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", spreadsheet_url)
        if not match:
            return None, None, "Ошибка: Неверный формат ссылки на Google Sheets."
        file_id = match.group(1)
        export_url = f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx"

        response = requests.get(export_url)
        if response.status_code != 200:
            return None, None, "Ошибка: Не удалось скачать таблицу. Проверьте доступность."

        temp_file = f"temp_table_{threading.current_thread().ident}.xlsx"
        with open(temp_file, "wb") as f:
            f.write(response.content)

        wb = load_workbook(temp_file, data_only=True)
        ws = wb.worksheets[-1]
        sheet_name = ws.title

        fio_column = None
        score_columns = []
        for col in ws.iter_cols(min_row=1, max_row=1):
            for cell in col:
                if cell.value is not None:
                    value = str(cell.value).strip()
                    if "ФИО" in value:
                        fio_column = cell.column
                    else:
                        try:
                            num = float(value)
                            if 1 <= int(num) <= 20:
                                score_columns.append(cell.column)
                        except (ValueError, TypeError):
                            continue

        if not fio_column:
            os.remove(temp_file)
            return None, None, "Колонка ФИО не найдена."
        if not score_columns:
            os.remove(temp_file)
            return None, None, "Столбцы с оценками (1-20) не найдены."

        operators = []
        for row in ws.iter_rows(min_row=2):
            fio_cell = row[fio_column - 1]
            if not fio_cell.value:
                break
            scores = []
            for col_idx in score_columns:
                score_cell = row[col_idx - 1]
                try:
                    score = float(score_cell.value) if float(score_cell.value)>=0 else None
                    if score is not None:
                        scores.append(score)
                except (ValueError, TypeError):
                    continue
            call_count = len(scores)
            avg_score = sum(scores) / call_count if scores else None
            operator_info = {
                "name": str(fio_cell.value),
                "link": fio_cell.hyperlink.target if fio_cell.hyperlink else None,
                "call_count": call_count,
                "avg_score": avg_score
            }
            operators.append(operator_info)

        os.remove(temp_file)
        return sheet_name, operators, None
    except Exception as e:
        if 'temp_file' in locals() and os.path.exists(temp_file):
            os.remove(temp_file)
        logging.error(f"Ошибка обработки таблицы: {str(e)}")
        return None, None, f"Ошибка обработки: {str(e)}"

def sync_generate_weekly_report():
    try:
        if not report_lock.acquire(blocking=False):
            logging.warning("Report generation skipped: already running")
            return False
            
        logging.info("Starting weekly report generation")
        
        current_week = get_current_week_of_month()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#D3D3D3',
            'border': 1
        })
        cell_format_int = workbook.add_format({'border': 1, 'num_format': '0'})
        cell_format_float = workbook.add_format({'border': 1, 'num_format': '0.00'})
        
        # Get supervisors from the database
        svs = db.get_supervisors()
        
        for sv_id, sv_name, table_url, _ in svs:
            if not table_url:
                logging.warning(f"No table URL for supervisor {sv_name}")
                continue
                
            sheet_name, operators, error = extract_fio_and_links(table_url)
            if error:
                logging.error(f"Error processing table for SV {sv_name}: {error}")
                continue
                
            safe_sheet_name = sv_name[:31].replace('/', '_').replace('\\', '_').replace('?', '_').replace('*', '_').replace('[', '_').replace(']', '_')
            worksheet = workbook.add_worksheet(safe_sheet_name)
            headers = ['ФИО', 'Количество звонков', 'Средний балл']
            for col, header in enumerate(headers):
                worksheet.write(0, col, header, header_format)
                
            for row, op in enumerate(operators, start=1):
                name = op.get('name', '')
                call_count = op.get('call_count', 0)
                avg_score = op.get('avg_score', None)
                
                if call_count in [None, "#DIV/0!"]:
                    call_count = 0
                else:
                    try:
                        call_count = int(call_count)
                    except (ValueError, TypeError):
                        call_count = 0
                
                try:
                    score_val = float(avg_score) if avg_score else None
                except (ValueError, TypeError):
                    score_val = ''
                
                worksheet.write(row, 0, name, cell_format_int)
                worksheet.write(row, 1, call_count, cell_format_int)
                worksheet.write(row, 2, score_val, cell_format_float)
            
            worksheet.set_column('A:A', 30)
            worksheet.set_column('B:B', 20)
            worksheet.set_column('C:C', 15)
        
        workbook.close()
        output.seek(0)
        
        if output.getvalue():
            filename = f"Weekly_Report_Week{current_week}_{datetime.now().strftime('%Y%m%d')}.xlsx"
            telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendDocument"
            
            # Fetch admins from the database
            try:
                admins = []
                with db._get_cursor() as cursor:
                    cursor.execute("SELECT telegram_id FROM users WHERE role = 'admin'")
                    admins = [row[0] for row in cursor.fetchall()]
            except Exception as e:
                logging.error(f"Error fetching admins: {e}")
                return False
            
            # Send report to all admins
            for admin_id in admins:
                files = {'document': (filename, output.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}
                data = {'chat_id': admin_id, 'caption': f"[{now}] 📊 Еженедельный отчет за {current_week}-ю неделю"}
                response = requests.post(telegram_url, files=files, data=data)
                
                if response.status_code != 200:
                    logging.error(f"Error sending report to admin {admin_id}: {response.text}")
            
            return True
        else:
            logging.error("Report is empty")
            return False
    except Exception as e:
        logging.error(f"Critical error in report generation: {e}")
        return False
    finally:
        try:
            report_lock.release()
        except:
            pass

async def generate_weekly_report():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor_pool, sync_generate_weekly_report)

# === Главный запуск =============================================================================================
if __name__ == '__main__':
    # Инициализация администратора
    if not db.get_user(role='admin'):
        db.create_user(
            telegram_id=admin,
            name='Admin',
            role='admin',
            login=ADMIN_LOGIN,
            password=ADMIN_PASSWORD
        )
        logging.info("Admin user created")
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Настраиваем и запускаем планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        generate_weekly_report, 
        CronTrigger(day_of_week='mon', hour=9, minute=0),
        misfire_grace_time=3600
    )
    scheduler.add_job(
        db.process_and_upload_timesheet,
        CronTrigger(minute='*/2'),
        misfire_grace_time=3600
    )
    scheduler.start()
    
    logging.info("🔄 Планировщик запущен")
    logging.info("🤖 Бот запущен")
    
    # Запускаем бота
    executor.start_polling(dp, skip_updates=True)
