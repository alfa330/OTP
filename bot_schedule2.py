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
from flask import Flask, request, jsonify, send_file
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
from werkzeug.utils import secure_filename
from google.cloud import storage as gcs_storage
import tempfile
from datetime import datetime, timedelta
import time
import math

os.environ['TZ'] = 'Asia/Almaty'
time.tzset()

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
CORS(app, resources={
    r"/api/*": {
        "origins": ["https://alfa330.github.io", "http://localhost:*"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-API-Key", "X-User-Id"],
        "supports_credentials": False,
        "max_age": 86400
    }
})

def get_gcs_client():
    # Если используется JSON из переменной окружения
    credentials_content = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_CONTENT')
    if credentials_content:
        import json
        from google.oauth2 import service_account
        credentials_info = json.loads(credentials_content)
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
        return gcs_storage.Client(credentials=credentials)
    return gcs_storage.Client()

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
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

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', 'https://alfa330.github.io')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, X-API-Key, X-User-Id')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, DELETE')
    return response

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

@app.route('/api/user/profile', methods=['GET'])
@require_api_key
def get_user_profile():
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            logging.warning("Missing user_id parameter in profile request")
            return jsonify({"error": "Missing user_id parameter"}), 400

        try:
            user_id = int(user_id)
        except ValueError:
            logging.error(f"Invalid user_id format: {user_id}")
            return jsonify({"error": "Invalid user_id format"}), 400

        user = db.get_user(id=user_id)
        if not user:
            logging.warning(f"User not found with ID: {user_id}")
            return jsonify({"error": "User not found"}), 404

        supervisor_name = None
        if user[6]:
            supervisor = db.get_user(id=user[6])
            supervisor_name = supervisor[2] if supervisor else None

        hire_date = user[5].strftime('%Y-%m-%d') if user[5] else None

        profile_data = {
            "id": user[0],
            "name": user[2] or "Unknown",
            "role": user[3] or "Unknown",
            "direction": user[4],  # Now returns direction name from joined directions table
            "hire_date": hire_date,
            "supervisor_name": supervisor_name,
            "telegram_id": user[1] or None,
            "scores_table_url": user[9] or None,
            "hours_table_url": user[8] or None,
            "status": user[11],  # Adjust index based on query
            "rate": float(user[12]) if user[12] else 1.0
        }

        logging.info(f"Profile data fetched successfully for user_id: {user_id}")
        return jsonify({"status": "success", "profile": profile_data}), 200
    except Exception as e:
        logging.error(f"Error fetching user profile for user_id {user_id}: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/users', methods=['GET'])
@require_api_key
def get_admin_users():
    try:
        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if requester[3] == 'admin' or requester[3] == 'sv':
            with db._get_cursor() as cursor:
                cursor.execute("""
                    SELECT u.id, u.name, d.name as direction, s.name as supervisor_name, u.direction_id, u.supervisor_id, u.role, u.status, u.rate
                    FROM users u
                    LEFT JOIN directions d ON u.direction_id = d.id
                    LEFT JOIN users s ON u.supervisor_id = s.id
                    WHERE u.role = 'operator'
                """)
                users = []
                for row in cursor.fetchall():
                    users.append({
                        "id": row[0],
                        "name": row[1],
                        "direction": row[2],
                        "supervisor_name": row[3],
                        "direction_id": row[4],
                        "supervisor_id": row[5],
                        "role": row[6],
                        "status": row[7],
                        "rate": float(row[8])
                    })
        return jsonify({"status": "success", "users": users}), 200
    except Exception as e:
        logging.error(f"Error fetching users: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/update_user', methods=['POST'])
@require_api_key
def admin_update_user():
    try:
        data = request.get_json()
        required_fields = ['user_id', 'field', 'value']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        user_id = int(data['user_id'])
        field = data['field']
        value = data['value']
        if field in ['direction_id', 'supervisor_id']:
            value = int(value) if value else None
        elif field == 'status':
            if value not in ['working', 'fired', 'unpaid_leave']:
                return jsonify({"error": "Invalid status value"}), 400
        elif field == 'rate':
            try:
                value = float(value)
                if value not in [1.0, 0.75, 0.5]:
                    return jsonify({"error": "Invalid rate value"}), 400
            except ValueError:
                return jsonify({"error": "Invalid rate format"}), 400
        else:
            return jsonify({"error": "Invalid field"}), 400

        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester or requester[3] != 'admin' and requester[3] != 'sv':
            return jsonify({"error": "Only admins can update users"}), 403

        success = db.update_user(user_id, field, value, changed_by=requester_id)  # Pass changed_by
        if not success:
            return jsonify({"error": "Failed to update user"}), 500

        return jsonify({"status": "success", "message": "User updated"}), 200
    except Exception as e:
        logging.error(f"Error updating user: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
    
@app.route('/api/user/history', methods=['GET'])
@require_api_key
def get_user_history():
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({"error": "Missing user_id parameter"}), 400
        user_id = int(user_id)

        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "Requester not found"}), 404

        target_user = db.get_user(id=user_id)
        if not target_user:
            return jsonify({"error": "User not found"}), 404

        # Permissions: Admin can view any, SV only their operators
        if requester[3] != 'admin' and (requester[3] != 'sv' or target_user[6] != requester_id):
            return jsonify({"error": "Unauthorized to view this user's history"}), 403

        history = db.get_user_history(user_id)
        return jsonify({"status": "success", "history": history}), 200
    except Exception as e:
        logging.error(f"Error fetching user history: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/user/change_password', methods=['POST'])
@require_api_key
def change_password():
    try:
        data = request.get_json()
        required_fields = ['user_id', 'new_password']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields (user_id, new_password)"}), 400

        user_id = int(data['user_id'])
        new_password = data['new_password']
        requester_id = int(request.headers.get('X-User-Id', user_id))  # Assume user ID is passed in headers for authentication

        if not new_password or len(new_password) < 6:
            return jsonify({"error": "New password must be at least 6 characters long"}), 400

        # Fetch requester and target user
        requester = db.get_user(id=requester_id)
        target_user = db.get_user(id=user_id)

        if not requester or not target_user:
            return jsonify({"error": "User not found"}), 404

        # Authorization checks
        if requester[3] == 'operator' and requester_id != user_id:
            return jsonify({"error": "Operators can only change their own password"}), 403

        if requester[3] == 'sv' and (target_user[3] != 'operator' or target_user[6] != requester_id) and requester_id != user_id:
            return jsonify({"error": "Supervisors can only change passwords for their operators"}), 403

        # Admins can change any password
        if requester[3] != 'admin' and requester_id != user_id and not (requester[3] == 'sv' and target_user[6] == requester_id):
            return jsonify({"error": "Unauthorized to change this user's password"}), 403

        # Update password
        success = db.update_user_password(user_id, new_password)
        if not success:
            return jsonify({"error": "Failed to update password"}), 500

        # Notify user via Telegram
        if target_user[1]:  # Check if telegram_id exists
            telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
            payload = {
                "chat_id": target_user[1],
                "text": f"Ваш пароль был успешно изменён. Новый пароль: <b>{new_password}</b>",
                "parse_mode": "HTML"
            }
            response = requests.post(telegram_url, json=payload, timeout=10)
            if response.status_code != 200:
                error_detail = response.json().get('description', 'Unknown error')
                logging.error(f"Telegram API error: {error_detail}")

        return jsonify({"status": "success", "message": f"Password updated for user {target_user[2]}"})

    except Exception as e:
        logging.error(f"Error changing password: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/sv/preview_table', methods=['POST'])
@require_api_key
def preview_table():
    try:
        data = request.get_json()
        if not data or 'table_url' not in data:
            return jsonify({"error": "Missing table_url"}), 400

        table_url = data['table_url']
        user_id = int(request.headers.get('X-User-Id'))
        user = db.get_user(id=user_id)
        if not user or user[3] != 'sv':
            return jsonify({"error": "Unauthorized: Only supervisors can preview tables"}), 403

        sheet_name, operators, error = extract_fio_and_links(table_url)
        if error:
            return jsonify({"error": error}), 400

        return jsonify({
            "status": "success",
            "sheet_name": sheet_name,
            "operators": operators
        }), 200
    except Exception as e:
        logging.error(f"Error previewing table: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/sv/save_table', methods=['POST'])
@require_api_key
def save_table():
    try:
        data = request.get_json()
        required_fields = ['table_url', 'direction_id', 'operators']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields: table_url, direction_id, operators"}), 400

        table_url = data['table_url']
        direction_id = int(data['direction_id'])
        operators = data['operators']  # List of dicts with 'name'

        user_id = int(request.headers.get('X-User-Id'))
        user = db.get_user(id=user_id)
        if not user or user[3] != 'sv':
            return jsonify({"error": "Unauthorized: Only supervisors can save tables"}), 403

        # Validate direction
        directions = db.get_directions()
        if not any(d['id'] == direction_id for d in directions):
            return jsonify({"error": "Invalid direction_id"}), 400

        # Update supervisor's hours table
        db.update_user_table(user_id=user_id, hours_table_url=table_url)

        # Create/update operators with direction and supervisor
        for op in operators:
            db.create_user(
                telegram_id=None,
                name=op['name'],
                role='operator',
                direction_id=direction_id,
                supervisor_id=user_id
            )

        # Optional: Send Telegram notification to supervisor
        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": user[1],
            "text": f"Таблица часов успешно обновлена и операторы сохранены с направлением ID {direction_id} ✅",
            "parse_mode": "HTML"
        }
        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code != 200:
            logging.error(f"Telegram notification failed: {response.json().get('description')}")

        return jsonify({
            "status": "success",
            "message": "Table updated, operators saved with selected direction"
        }), 200
    except Exception as e:
        logging.error(f"Error saving table: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/user/change_login', methods=['POST'])
@require_api_key
def change_login():
    try:
        data = request.get_json()
        required_fields = ['user_id', 'new_login']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields (user_id, new_login)"}), 400

        user_id = int(data['user_id'])
        new_login = data['new_login']
        requester_id = int(request.headers.get('X-User-Id', user_id))

        if not new_login or len(new_login) < 4:
            return jsonify({"error": "New login must be at least 4 characters long"}), 400

        # Check if new login is already taken
        if db.get_user_by_login(new_login):
            return jsonify({"error": "Login is already in use"}), 400

        # Fetch requester and target user
        requester = db.get_user(id=requester_id)
        target_user = db.get_user(id=user_id)

        if not requester or not target_user:
            return jsonify({"error": "User not found"}), 404

        # Authorization checks
        if requester[3] == 'operator' and requester_id != user_id:
            return jsonify({"error": "Operators can only change their own login"}), 403

        if requester[3] == 'sv' and (target_user[3] != 'operator' or target_user[6] != requester_id) and requester_id != user_id:
            return jsonify({"error": "Supervisors can only change logins for their operators"}), 403

        # Admins can change any login
        if requester[3] != 'admin' and requester_id != user_id and not (requester[3] == 'sv' and target_user[6] == requester_id):
            return jsonify({"error": "Unauthorized to change this user's login"}), 403

        # Update login
        success = db.update_operator_login(user_id, target_user[6] if requester[3] == 'sv' or requester[3] == 'admin' else None, new_login)
        if not success:
            return jsonify({"error": "Failed to update login"}), 500

        # Notify user via Telegram
        if target_user[1]:
            telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
            payload = {
                "chat_id": target_user[1],
                "text": f"Ваш логин был успешно изменён. Новый логин: <b>{new_login}</b>",
                "parse_mode": "HTML"
            }
            response = requests.post(telegram_url, json=payload, timeout=10)
            if response.status_code != 200:
                error_detail = response.json().get('description', 'Unknown error')
                logging.error(f"Telegram API error: {error_detail}")

        return jsonify({"status": "success", "message": f"Login updated for user {target_user[2]}"})

    except Exception as e:
        logging.error(f"Error changing login: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/user/hours', methods=['GET'])
@require_api_key
def get_user_hours():
    try:
        operator_id = request.args.get('operator_id')
        month = request.args.get('month')
        if not operator_id:
            return jsonify({"error": "Missing operator_id parameter"}), 400
        if not month:
            month = datetime.now().strftime('%Y-%m')
        operator_id = int(operator_id)
        hours_summary = db.get_hours_summary(operator_id=operator_id, month=month)
        
        if not hours_summary:
            return jsonify({"status": "success", "hours": None}), 200

        # Assuming get_hours_summary returns a list, take the first item
        hours_data = hours_summary[0]

        return jsonify({"status": "success", "hours": hours_data}), 200
    except Exception as e:
        logging.error(f"Error fetching hours data: {e}")
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
        month = request.args.get('month')
        if not operator_id:
            return jsonify({"error": "Missing operator_id parameter"}), 400
        if not month:
            month = None
        operator_id = int(operator_id)
        evaluations = db.get_call_evaluations(operator_id, month=month)

        # Получаем информацию о супервайзере для dispute button
        operator = db.get_user(id=operator_id)
        supervisor = db.get_user(id=operator[6]) if operator and operator[6] else None

        # Просто возвращаем массивы из базы, не пересоздаём их из комментариев
        return jsonify({
            "status": "success", 
            "evaluations": evaluations,
            "supervisor": {
                "id": supervisor[0] if supervisor else None,
                "name": supervisor[2] if supervisor else None
            }
        })
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

@app.route('/api/call_evaluation/dispute', methods=['POST'])
@require_api_key
def dispute_call_evaluation():
    if request.method == 'OPTIONS':
        return _build_cors_preflight_response()
    try:
        data = request.get_json()
        required_fields = ['operator_id', 'id', 'month', 'dispute_text']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields"}), 400

        operator_id = int(data['operator_id'])
        operator = db.get_user(id=operator_id)
        if not operator:
            return jsonify({"error": "Operator not found"}), 404

        supervisor = db.get_user(id=operator[6]) if operator[6] else None
        if not supervisor:
            return jsonify({"error": "Supervisor not found for this operator"}), 404

        # Get call details
        evaluations = db.get_call_evaluations(operator_id, data['month'])
        call = next((e for e in evaluations if e['id'] == data['id']), None)
        if not call:
            return jsonify({"error": "Call evaluation not found"}), 404

        # --- NEW: Try to get audio signed URL ---
        audio_url = None
        if call.get('audio_path'):
            try:
                gcs_client = get_gcs_client()
                path_parts = call['audio_path'].split('/', 1)
                if len(path_parts) == 2:
                    bucket_name, blob_path = path_parts
                    bucket = gcs_client.bucket(bucket_name)
                    blob = bucket.blob(blob_path)
                    if blob.exists():
                        audio_url = blob.generate_signed_url(
                            version="v4",
                            expiration=timedelta(minutes=15),
                            method="GET",
                            response_type='audio/mpeg'
                        )
            except Exception as e:
                logging.error(f"Error generating signed audio URL for dispute: {e}")
        # --- END NEW ---

        # Сообщение для супервайзера
        supervisor_message = (
            f"⚠️ <b>Запрос на пересмотр оценки</b>\n\n"
            f"👤 Оператор: <b>{operator[2]}</b>\n"
            f"📞 Звонок ID: {call['id']}\n"
            f"📱 Номер: {call['phone_number']}\n"
            f"📅 Дата обращения: {call['appeal_date']}\n"
            f"💯 Оценка: {call['score']}\n"
            f"📅 Месяц: {call['month']}\n\n"
            f"📝 <b>Сообщение от оператора:</b>\n"
            f"{data['dispute_text']}"
        )

        # Сообщение для админа
        admin_message = (
            f"⚠️ <b>Запрос на пересмотр оценки</b>\n\n"
            f"💬 Супервайзер: <b>{supervisor[2]}</b>\n"
            f"👤 Оператор: <b>{operator[2]}</b>\n"
            f"📞 Звонок ID: {call['id']}\n"
            f"📅 Дата обращения: {call['appeal_date']}\n"
            f"📱 Номер: {call['phone_number']}\n"
            f"💯 Оценка: {call['score']}\n"
            f"📅 Месяц: {call['month']}\n\n"
            f"📝 <b>Сообщение от оператора:</b>\n"
            f"{data['dispute_text']}"
        )

        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        telegram_audio_url = f"https://api.telegram.org/bot{API_TOKEN}/sendAudio"

        # --- NEW: Send audio if available ---
        def send_telegram_message(chat_id, text, audio_url=None):
            if audio_url:
                payload = {
                    "chat_id": chat_id,
                    "audio": audio_url,
                    "caption": text,
                    "parse_mode": "HTML"
                }
                resp = requests.post(telegram_audio_url, json=payload, timeout=10)
            else:
                payload = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML"
                }
                resp = requests.post(telegram_url, json=payload, timeout=10)
            return resp

        # Отправка супервайзеру
        supervisor_response = send_telegram_message(supervisor[1], supervisor_message, audio_url)
        if supervisor_response.status_code != 200:
            error_detail = supervisor_response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error (supervisor): {error_detail}")
            return jsonify({"error": f"Failed to send dispute message to supervisor: {error_detail}"}), 500

        # Отправка админу
        admin_response = send_telegram_message(admin, admin_message, audio_url)
        if admin_response.status_code != 200:
            error_detail = admin_response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error (admin): {error_detail}")
            return jsonify({"error": f"Failed to send dispute message to admin: {error_detail}"}), 500
        # --- END NEW ---

        return jsonify({"status": "success", "message": "Dispute sent to supervisor and admin"})
    except Exception as e:
        logging.error(f"Error processing dispute: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/add_sv', methods=['POST'])
@require_api_key
def add_sv():
    try:
        data = request.get_json()
        required_fields = ['name']  # Removed 'telegram_id' from required fields
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required field: name"}), 400

        name = data['name']
        
        login = f"sv_{str(uuid.uuid4())[:8]}"
        password = str(uuid.uuid4())[:8]  
        
        # Create supervisor with login and hashed_password, telegram_id set to None
        sv_id = db.create_user(
            telegram_id=None,  # Explicitly set to None
            name=name,
            role='sv',
            login=login,
            password=password
        )
        
        return jsonify({
            "status": "success",
            "message": f"SV {name} added",
            "id": sv_id,
            "login": login,  # Return plain login
            "password": password  # Return plain password for admin to share
        })
    except Exception as e:
        logging.error(f"Error adding SV: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/directions', methods=['GET'])
@require_api_key
def get_directions():
    try:
        user_id = request.headers.get('X-User-Id')
        if not user_id:
            logging.warning("Missing X-User-Id header in /api/admin/directions request")
            return jsonify({"error": "Missing X-User-Id header"}), 400

        try:
            requester_id = int(user_id)
        except (ValueError, TypeError):
            logging.error(f"Invalid X-User-Id format: {user_id}")
            return jsonify({"error": "Invalid X-User-Id format"}), 400

        requester = db.get_user(id=requester_id)
        if not requester or not (requester[3] == 'admin' or requester[3] == 'sv'):
            return jsonify({"error": f"Only admins can access directions {requester[3]}"}), 403

        directions = db.get_directions()
        return jsonify({"status": "success", "directions": directions}), 200
    except Exception as e:
        logging.error(f"Error fetching directions: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/save_directions', methods=['POST'])
@require_api_key
def save_directions():
    try:
        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester or requester[3] != 'admin':
            return jsonify({"error": "Only admins can save directions"}), 403

        data = request.get_json()
        if not data or 'directions' not in data:
            return jsonify({"error": "Missing directions data"}), 400

        directions = data['directions']
        db.save_directions(directions, requester_id)

        # Получаем обновлённый список направлений с id
        updated_directions = db.get_directions()

        return jsonify({
            "status": "success",
            "message": "Directions saved successfully",
            "directions": updated_directions
        }), 200

    except Exception as e:
        logging.error(f"Error saving directions: {e}")
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
            return jsonify({"error": "User not found or invalid role"}), 404
        
        # Initialize response data
        response_data = {
            "status": "success",
            "name": user[2],
            "table": user[9],  # scores_table_url
            "operators": []
        }

        # If user is a supervisor, fetch their operators and call statistics
        if user[3] == 'sv':
            operators = db.get_operators_by_supervisor(user_id)
            current_month = datetime.now().strftime('%Y-%m')
            
            for operator in operators:
                operator_id, operator_name, direction_id, hire_date, hours_table_url, scores_table_url, supervisor_name, status, rate =[operator[kkk] for kkk in operator]
                # Get direction name from direction_id
                
                # Get call evaluations for the operator
                evaluations = db.get_call_evaluations(operator_id, month=current_month)
                call_count = len(evaluations)
                avg_score = sum(float(e['score']) for e in evaluations) / call_count if call_count > 0 else 0
                
                response_data["operators"].append({
                    "id": operator_id,
                    "name": operator_name,
                    "direction_id": direction_id,
                    "call_count": call_count,
                    "avg_score": round(avg_score, 2) if call_count > 0 else None,
                    "scores_table_url": scores_table_url,
                    "status": status,
                    "rate": rate
                })
        
        return jsonify(response_data), 200
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

@app.route('/api/admin/monthly_report', methods=['GET'])
@require_api_key
def handle_monthly_report():
    try:
        month = request.args.get('month')
        if not month:
            month = datetime.now().strftime('%Y-%m')
        
        # Validate month format
        try:
            month_start = datetime.strptime(month + '-01', '%Y-%m-%d')
        except ValueError:
            return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400
        
        logging.info(f"Generating monthly report for {month}")
        
        svs = db.get_supervisors()
        
        if not svs:
            return jsonify({"error": "No supervisors found"}), 404
        
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})

        # Styles
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#D3D3D3',
            'border': 1,
            'align': 'center'
        })
        fio_format = workbook.add_format({'border': 1, 'bold': True, 'align': 'left'})
        int_format = workbook.add_format({'border': 1, 'num_format': '0', 'align': 'center'})
        float_format = workbook.add_format({'border': 1, 'num_format': '0.00', 'align': 'center'})
        # Score color formats
        red_format = workbook.add_format({'border': 1, 'num_format': '0.00', 'bg_color': '#FFCCCC', 'align': 'center'})
        yellow_format = workbook.add_format({'border': 1, 'num_format': '0.00', 'bg_color': '#FFFACD', 'align': 'center'})
        green_format = workbook.add_format({'border': 1, 'num_format': '0.00', 'bg_color': '#C6EFCE', 'align': 'center'})
        # Итоговые колонки
        total_format = workbook.add_format({'border': 1, 'bold': True, 'bg_color': '#E2EFDA', 'num_format': '0.00', 'align': 'center'})
        total_int_format = workbook.add_format({'border': 1, 'bold': True, 'bg_color': '#E2EFDA', 'num_format': '0', 'align': 'center'})

        for sv_id, sv_name, _, _ in svs:
            operators = db.get_operators_by_supervisor(sv_id)
            
            safe_sheet_name = sv_name[:31].replace('/', '_').replace('\\', '_').replace('?', '_').replace('*', '_').replace('[', '_').replace(']', '_')
            worksheet = workbook.add_worksheet(safe_sheet_name)
            
            # Headers
            headers = ['ФИО']
            for i in range(1, 21):
                headers.append(f'{i}')
            headers.append('Средний балл')
            headers.append('Кол-во оцененных звонков')
            
            for col, header in enumerate(headers):
                worksheet.write(0, col, header, header_format)
            
            for row_idx, op in enumerate(operators, start=1):
                op_id = op['id']
                op_name = op['name']
                
                # Get scores
                with db._get_cursor() as cursor:
                    cursor.execute("""
                        SELECT c.score
                        FROM calls c
                        JOIN (
                            SELECT phone_number, MAX(created_at) as max_date
                            FROM calls 
                            WHERE operator_id = %s AND month = %s AND is_draft = FALSE 
                            GROUP BY phone_number
                        ) lv ON c.phone_number = lv.phone_number AND c.created_at = lv.max_date
                        WHERE c.is_draft = FALSE
                        ORDER BY c.created_at ASC
                    """, (op_id, month))
                    scores = [row[0] for row in cursor.fetchall()]
                
                count = len(scores)
                avg_score = sum(scores) / count if count > 0 else 0.0

                # ФИО
                worksheet.write(row_idx, 0, op_name, fio_format)
                
                # Оценки с цветом
                for col in range(1, 21):
                    if col-1 < count:
                        score = scores[col-1]
                        if score < 80:
                            fmt = red_format
                        elif score < 95:
                            fmt = yellow_format
                        else:
                            fmt = green_format
                        worksheet.write(row_idx, col, score, fmt)
                    else:
                        worksheet.write(row_idx, col, '', float_format)
                
                # Итоговые колонки
                worksheet.write(row_idx, 21, avg_score, total_format)
                worksheet.write(row_idx, 22, count, total_int_format)
            
            worksheet.set_column('A:A', 30)
            for i in range(1, 21):
                worksheet.set_column(i, i, 12)
            worksheet.set_column(21, 21, 15)
            worksheet.set_column(22, 22, 20)
        
        workbook.close()
        output.seek(0)
        
        if output.getvalue():
            filename = f"Monthly_Report_{month}.xlsx"
            return send_file(
                output,
                as_attachment=True,
                download_name=filename,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        else:
            logging.error("Report is empty")
            return jsonify({"error": "Generated report is empty"}), 500
            
    except Exception as e:
        logging.error(f"Error in monthly_report: {e}")
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
        
        db.update_user_table(user_id = user_id, hours_table_url = table_url)
        
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

@app.route('/api/call_versions/<int:call_id>', methods=['GET'])
@require_api_key
def get_call_versions(call_id):
    try:
        with db._get_cursor() as cursor:
            # Получаем все версии оценки, начиная с текущей и идя назад по previous_version_id
            versions = []
            current_id = call_id
            
            while current_id:
                cursor.execute("""
                    SELECT 
                        c.id, c.score, c.comment, c.phone_number, c.month, 
                        c.audio_path, c.created_at, c.is_correction,
                        u.name as evaluator_name,
                        c.previous_version_id  
                    FROM calls c
                    JOIN users u ON c.evaluator_id = u.id
                    WHERE c.id = %s
                """, (current_id,))
                version = cursor.fetchone()
                if not version:
                    break
                    
                versions.append({
                    "id": version[0],
                    "score": float(version[1]),
                    "comment": version[2],
                    "phone_number": version[3],
                    "month": version[4],
                    "audio_path": version[5],
                    "evaluation_date": version[6].strftime('%Y-%m-%d %H:%M'),
                    "is_correction": version[7],
                    "evaluator_name": version[8],
                    "audio_url": None  # Будет заполнено позже
                })
                
                # Переходим к предыдущей версии
                current_id = version[9]  # previous_version_id

            # Генерируем signed URLs для аудиофайлов
            gcs_client = get_gcs_client()
            for version in versions:
                if version['audio_path']:
                    try:
                        path_parts = version['audio_path'].split('/', 1)
                        if len(path_parts) == 2:
                            bucket_name, blob_path = path_parts
                            bucket = gcs_client.bucket(bucket_name)
                            blob = bucket.blob(blob_path)
                            if blob.exists():
                                version['audio_url'] = blob.generate_signed_url(
                                    version="v4",
                                    expiration=timedelta(minutes=15),
                                    method="GET",
                                    response_type='audio/mpeg'
                                )
                    except Exception as e:
                        logging.error(f"Error generating signed URL for version {version['id']}: {e}")

            return jsonify({"status": "success", "versions": versions})
    except Exception as e:
        logging.error(f"Error fetching call versions: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/call_evaluation/<int:evaluation_id>', methods=['DELETE'])
def delete_draft_evaluation(evaluation_id):
    try:
        requester_id = int(request.headers.get('X-User-Id'))
        with db._get_cursor() as cursor:
            cursor.execute("""
                SELECT is_draft, evaluator_id, audio_path FROM calls 
                WHERE id = %s
            """, (evaluation_id,))
            result = cursor.fetchone()
            if not result:
                return jsonify({"error": "Evaluation not found"}), 404
            is_draft, evaluator_id, audio_path = result
            if not is_draft:
                return jsonify({"error": "Can only delete draft evaluations"}), 400
            if evaluator_id != requester_id:
                return jsonify({"error": "Unauthorized to delete this draft"}), 403

            # Удаление файла из GCS
            if audio_path:
                try:
                    bucket_name = os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET')
                    client = get_gcs_client()
                    bucket = client.bucket(bucket_name)
                    blob_path = audio_path.replace(f"https://storage.googleapis.com/{bucket_name}/", "")
                    blob = bucket.blob(blob_path)
                    blob.delete()
                except Exception as e:
                    logging.error(f"Error deleting file from GCS: {e}")

            cursor.execute("""
                DELETE FROM calls WHERE id = %s
            """, (evaluation_id,))
            
        return jsonify({"status": "success", "message": "Draft deleted"}), 200
    except Exception as e:
        logging.error(f"Error deleting draft evaluation: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/audio/<int:evaluation_id>', methods=['GET'])
@require_api_key
def get_audio_file(evaluation_id):
    try:
        call = db.get_call_by_id(evaluation_id)
        if not call or not call['audio_path']:
            return jsonify({"error": "Audio file not found"}), 404

        gcs_client = get_gcs_client()

        # Разбиваем путь: 'bucket_name/folder/file.mp3'
        path_parts = call['audio_path'].split('/', 1)
        if len(path_parts) != 2:
            return jsonify({"error": "Invalid GCS path format"}), 400

        bucket_name, blob_path = path_parts
        bucket = gcs_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        if not blob.exists():
            return jsonify({"error": "Audio file not found in GCS"}), 404

        # Генерируем временный Signed URL
        signed_url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=15),
            method="GET",
            response_type='audio/mpeg'
        )

        return jsonify({"status": "success", "url": signed_url})
    except Exception as e:
        logging.error(f"Error generating signed audio URL: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/call_evaluation', methods=['POST'])
def receive_call_evaluation():
    try:
        if not request.form:
            return jsonify({"error": "Missing form data"}), 400

        required_fields = ['evaluator', 'operator', 'phone_number', 'appeal_date', 'score', 'comment', 'month', 'is_draft']
        missing_fields = [field for field in required_fields if field not in request.form]
        if missing_fields:
            return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

        evaluator_name = request.form['evaluator']
        operator_name = request.form['operator']
        phone_number = request.form['phone_number']
        appeal_date = request.form['appeal_date']
        score = float(request.form['score'])
        comment = request.form['comment']
        month = request.form['month'] or datetime.now().strftime('%Y-%m')
        is_draft = request.form['is_draft'].lower() == 'true'
        scores = json.loads(request.form.get('scores', '[]'))
        criterion_comments = json.loads(request.form.get('criterion_comments', '[]'))
        direction_id = request.form.get('direction')
        previous_version_id = request.form.get('previous_version_id')
        is_correction = request.form.get('is_correction', 'false').lower() == 'true'

        evaluator = db.get_user(name=evaluator_name)
        operator = db.get_user(name=operator_name)
        if not evaluator or not operator:
            return jsonify({"error": "Evaluator or operator not found"}), 404

        if is_correction:
            requester_id = int(request.headers.get('X-User-Id'))
            requester = db.get_user(id=requester_id)
            if not requester or requester[3] != 'admin':
                return jsonify({"error": "Only admins can perform re-evaluations"}), 403

        audio_path = None
        audio_data = None
        blob_path = None
        bucket_name = None
        has_new_audio = False

        if 'audio_file' in request.files:
            file = request.files['audio_file']
            if file and file.filename:
                has_new_audio = True
                try:
                    audio_data = file.read()
                except Exception as e:
                    logging.error(f"Error reading audio file: {e}")
                    return jsonify({"error": f"Failed to process audio file: {str(e)}"}), 500
                filename = secure_filename(f"{uuid.uuid4()}.mp3")
                bucket_name = os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET')
                upload_folder = os.getenv('UPLOAD_FOLDER', 'Uploads/')
                blob_path = f"{upload_folder}{filename}"
                audio_path = "my-app-audio-uploads/" + blob_path

        elif is_correction and previous_version_id:
            try:
                with db._get_cursor() as cursor:
                    cursor.execute("SELECT audio_path FROM calls WHERE id = %s", (previous_version_id,))
                    result = cursor.fetchone()
                    if result and result[0]:
                        audio_path = result[0]
            except Exception as e:
                logging.error(f"Error retrieving audio_path from previous version: {e}")
                return jsonify({"error": f"Failed to retrieve audio from previous version: {str(e)}"}), 500

        evaluation_id = db.add_call_evaluation(
            evaluator_id=evaluator[0],
            operator_id=operator[0],
            phone_number=phone_number,
            score=score,
            comment=comment,
            month=month,
            audio_path=audio_path,
            is_draft=is_draft,
            scores=scores,
            criterion_comments=criterion_comments,
            direction_id=direction_id,
            is_correction=is_correction,
            previous_version_id=previous_version_id if previous_version_id else None,
            appeal_date=appeal_date
        )

        if has_new_audio or (not is_draft and audio_path):
            threading.Thread(target=background_upload_and_notify, args=(
                audio_data, bucket_name, blob_path, evaluation_id, is_draft,
                evaluator[2], operator[2], month, phone_number, score, comment,
                is_correction, previous_version_id, audio_path if not has_new_audio else None,
                appeal_date
            )).start()
        elif not is_draft:
            threading.Thread(target=send_telegram_notification, args=(
                evaluator[2], operator[2], month, phone_number, score, comment,
                is_correction, previous_version_id, None, appeal_date
            )).start()

        return jsonify({"status": "success", "evaluation_id": evaluation_id}), 200
    except Exception as e:
        logging.error(f"Error processing call evaluation: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

def background_upload_and_notify(audio_data, bucket_name, blob_path, evaluation_id, is_draft,
                                 evaluator_name, operator_name, month, phone_number, score, comment,
                                 is_correction, previous_version_id, existing_audio_path, appeal_date):
    audio_path = existing_audio_path or ("my-app-audio-uploads/" + blob_path if blob_path else None)
    upload_success = False
    if audio_data:
        try:
            client = get_gcs_client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            blob.upload_from_string(audio_data, content_type='audio/mpeg')
            upload_success = True
        except Exception as e:
            logging.error(f"Error uploading file to GCS in background: {e}")
            try:
                with db._get_connection() as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("UPDATE calls SET audio_path = NULL WHERE id = %s", (evaluation_id,))
                        conn.commit()
            except Exception as update_e:
                logging.error(f"Error updating DB after failed upload: {update_e}")
            audio_path = None
    if not is_draft:
        send_telegram_notification(evaluator_name, operator_name, month, phone_number, score, comment,
                                   is_correction, previous_version_id, audio_path if (upload_success or existing_audio_path) else None, appeal_date)

def send_telegram_notification(evaluator_name, operator_name, month, phone_number, score, comment, is_correction, previous_version_id, audio_path, appeal_date):
    try:
        API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
        admin = os.getenv('TELEGRAM_ADMIN_CHAT_ID')
        if not admin:
            return

        message = (
            f"👤 Оценивающий: <b>{evaluator_name}</b>\n"
            f"📋 Оператор: <b>{operator_name}</b>\n"
            f"📄 За месяц: <b>{month}</b>\n"
            f"📱 Номер телефона: <b>{phone_number}</b>\n"
            f"📅 Дата обращения: <b>{appeal_date}</b>\n"
            f"💯 Оценка: <b>{score}</b>\n"
        )
        if is_correction:
            message += f"🔄 <b>Переоценка звонка (ID предыдущей версии: {previous_version_id})</b>\n"
        if score < 100 and comment:
            message += f"\n💬 Комментарий: \n{comment}\n"

        audio_signed_url = None
        if audio_path:
            try:
                client = get_gcs_client()
                path_parts = audio_path.split('/', 1)
                if len(path_parts) == 2:
                    bucket_name, blob_path = path_parts
                    bucket = client.bucket(bucket_name)
                    blob = bucket.blob(blob_path)
                    if blob.exists():
                        expiration = datetime.utcnow() + timedelta(minutes=15)
                        audio_signed_url = blob.generate_signed_url(
                            expiration=expiration,
                            method='GET',
                            version='v4'
                        )
            except Exception as e:
                logging.error(f"Error generating signed URL for audio: {e}")

        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendAudio" if audio_signed_url else f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": admin,
            "parse_mode": "HTML"
        }
        if audio_signed_url:
            payload["audio"] = audio_signed_url
            payload["caption"] = f"📞 <b>{'Переоценка звонка' if is_correction else 'Оценка звонка'}</b>\n" + message
        else:
            payload["text"] = f"💬 <b>{'Переоценка чата' if is_correction else 'Оценка чата'}</b>\n" + message

        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code != 200:
            error_detail = response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error: {error_detail}")
    except Exception as e:
        logging.error(f"Error sending Telegram notification: {e}")

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
    
@app.route('/api/user/toggle_active', methods=['POST'])
@require_api_key
def toggle_user_active():
    try:
        data = request.get_json()
        if not data or 'is_active' not in data:
            return jsonify({"error": "Missing is_active field"}), 400

        user_id = int(request.headers.get('X-User-Id'))
        user = db.get_user(id=user_id)
        if not user or user[3] != 'operator':
            return jsonify({"error": "Only operators can toggle active status"}), 403

        current_active = user[10]  # Предполагая, что user — это кортеж и is_active на позиции 10
        new_active = data['is_active']

        if current_active == new_active:
            return jsonify({"status": "unchanged", "message": "Active status is already set to the requested value"})

        success = db.set_user_active(user_id, new_active)
        if not success:
            return jsonify({"error": "Failed to update active status"}), 500

        log_success = db.log_activity(user_id, new_active)
        if not log_success:
            # Опционально: можно откатить обновление, если лог не удался, но для простоты продолжаем с предупреждением
            logging.warning("Active status updated but logging failed")
            return jsonify({"status": "partial_success", "message": "Active status updated, but logging failed"}), 500

        return jsonify({"status": "success", "message": "Active status updated and logged"})
    except Exception as e:
        logging.error(f"Error toggling active status: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/active_operators', methods=['GET'])
@require_api_key
def get_active_operators():
    try:
        operators = db.get_active_operators()
        return jsonify({"status": "success", "operators": operators})
    except Exception as e:
        logging.error(f"Error fetching active operators: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
    
@app.route('/api/report/monthly', methods=['GET'])
@require_api_key
def get_monthly_report():
    try:
        supervisor_id = request.args.get('supervisor_id')
        month = request.args.get('month')
        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "Requester not found"}), 404

        if not supervisor_id:
            if requester[3] == 'sv':
                supervisor_id = requester_id
            else:
                return jsonify({"error": "supervisor_id required"}), 400
        else:
            supervisor_id = int(supervisor_id)

        if requester[3] != 'admin' and (requester[3] != 'sv' or supervisor_id != requester_id):
            return jsonify({"error": "Unauthorized to access this report"}), 403

        logging.info("Начало генерации отчета")

        filename, content = db.generate_monthly_report(supervisor_id, month)
        if not filename or not content:
            return jsonify({"error": "Failed to generate report"}), 500
        
        return send_file(
            BytesIO(content),
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logging.error(f"Error generating monthly report: {e}")
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
    delete = State()
    delete_operator = State()  # Новое состояние
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

def get_access_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Сменить логин'))
    kb.insert(KeyboardButton('Сменить пароль'))
    kb.add(KeyboardButton('Выход🚪')) 
    kb.add(KeyboardButton('Отмена ❌'))
    return kb

def _build_cors_preflight_response():
    response = jsonify({"status": "ok"})
    response.headers.add("Access-Control-Allow-Origin", "https://alfa330.github.io")
    response.headers.add("Access-Control-Allow-Headers", "Content-Type, X-API-Key, X-User-Id")
    response.headers.add("Access-Control-Allow-Methods", "POST, OPTIONS")
    return response

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
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    directions = db.get_directions()  # Запрашиваем направления из базы данных
    if not directions:
        return None  # Если направлений нет, возвращаем None
    buttons = [
        types.InlineKeyboardButton(
            f"{direction['name']} {'📄' if direction['hasFileUpload'] else '📝'}",
            callback_data=f"dir_{direction['id']}"
        )
        for direction in directions
    ]
    keyboard.add(*buttons)
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
    if user and user[3] == 'sv':
        kb = get_sv_keyboard()
    elif user and user[3] == 'operator':
        kb = get_operator_keyboard()
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
                text=f"<b>Добро пожаловать, оператор {user[2]}!</b>\n\n"
                    "Используйте кнопки ниже для просмотра вашей статистики.",
                parse_mode='HTML',
                reply_markup=get_operator_keyboard()
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
    user = db.get_user(telegram_id=message.from_user.id)
    if user:
        await bot.send_message(
            chat_id=message.from_user.id,
            text=f"<b>Вы уже авторизованы как {user[2]} ({user[3]})</b>.\n\n",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardRemove()
        )
        await message.delete()
        return
    
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
        elif role == 'operator':
            await message.answer(
                text=f"<b>Добро пожаловать, оператор {name}!</b>\n\n"
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
            ikb.insert(InlineKeyboardButton(text=sv_name, callback_data=f"change_hours_table_{sv_id}"))
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите супервайзера для изменения таблицы часов</b>",
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

@dp.callback_query_handler(lambda c: c.data.startswith('change_hours_table_'), state=sv.change_table)
async def select_sv_for_hours_table_change(callback: types.CallbackQuery, state: FSMContext):
    sv_id = int(callback.data.split('_')[-1])
    async with state.proxy() as data:
        data['sv_id'] = sv_id
    user = db.get_user(id=sv_id)
    if user:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f'<b>Отправьте новую таблицу часов для {user[2]}🖊</b>',
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await state.set_state("waiting_for_hours_table_admin")
    else:
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        await state.finish()

@dp.message_handler(state="waiting_for_hours_table_admin")
async def save_hours_table_admin(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        sv_id = data.get('sv_id')
    user = db.get_user(id=sv_id)
    if user and user[3] == 'sv':
        try:
            sheet_name, operators, error = extract_fio_and_links(message.text)
            if error:
                await bot.send_message(
                    chat_id=message.from_user.id,
                    text=f"{error}\n\n<b>Пожалуйста, отправьте корректную ссылку на таблицу.</b>",
                    parse_mode="HTML",
                    reply_markup=get_cancel_keyboard()
                )
                return

            async with state.proxy() as data:
                data['hours_table_url'] = message.text
                data['operators'] = operators
                data['sheet_name'] = sheet_name
                data['sv_id'] = user[0]

            message_text = f"<b>Название листа:</b> {sheet_name}\n\n<b>ФИО операторов:</b>\n"
            for op in operators:
                    message_text += f"👤 {op['name']}\n"
            message_text += "\n<b>Это все операторы для этого СВ?</b>"

            await bot.send_message(
                chat_id=message.from_user.id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=get_verify_keyboard(),
                disable_web_page_preview=True
            )
            await state.set_state("verify_hours_table_admin")
            await message.delete()
        except Exception as e:
            await bot.send_message(
                chat_id=message.from_user.id,
                text=f"<b>Ошибка при обработке таблицы: {str(e)}</b>",
                parse_mode='HTML'
            )
            await state.finish()

@dp.callback_query_handler(state="verify_hours_table_admin")
async def verify_hours_table_admin(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        hours_table_url = data.get('hours_table_url')
        sv_id = data.get('sv_id')
        operators = data.get('operators')
        sheet_name = data.get('sheet_name')

    user = db.get_user(id=sv_id)
    if not user:
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        await state.finish()
        return

    if callback.data == "verify_yes":
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="Выберите направление для операторов:",
            reply_markup=get_direction_keyboard()
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await state.set_state("select_hours_direction_admin")
    elif callback.data == "verify_no":
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f'<b>Отправьте корректную таблицу часов для {user[2]}🖊</b>',
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await state.set_state("waiting_for_hours_table_admin")

@dp.callback_query_handler(state="select_hours_direction_admin")
async def select_hours_direction_admin(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        hours_table_url = data.get('hours_table_url')
        sv_id = data.get('sv_id')
        operators = data.get('operators')

    direction_id = None
    if callback.data.startswith("dir_"):
        try:
            direction_id = int(callback.data.replace("dir_", ""))
        except ValueError:
            await bot.answer_callback_query(callback.id, text="Ошибка: Неверный формат направления")
            return

    direction = next((d for d in db.get_directions() if d['id'] == direction_id), None)
    if not direction:
        await bot.answer_callback_query(callback.id, text="Ошибка: Направление не найдено")
        await state.finish()
        return

    user = db.get_user(id=sv_id)
    if not user:
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        await state.finish()
        return

    # Обновляем таблицу часов супервайзера
    db.update_user_table(user[0], hours_table_url=hours_table_url)

    # Создаём/обновляем операторов с direction_id
    for op in operators:
        db.create_user(
            telegram_id=None,
            name=op['name'],
            role='operator',
            direction_id=direction_id,
            supervisor_id=user[0]
        )

    await bot.send_message(
        chat_id=callback.from_user.id,
        text=f"""<b>Таблица часов для {user[2]} сохранена, операторы добавлены/обновлены с направлением "{direction['name']}"✅</b>""",
        parse_mode='HTML',
        reply_markup=get_editor_keyboard()
    )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
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
            regular_hours = hours.get('regular_hours', 0) or 0
            norm_hours = hours.get('norm_hours', 0) or 0
            percent_complete = 0
            if norm_hours > 0:
                percent_complete = round(regular_hours / norm_hours * 100, 1)
            message_text += (
                f"👤 <b>{op_name}</b>\n"
                f"   ⏱️ Часы работы: {regular_hours} из {norm_hours}\n"
                f"   📈 Процент выполнения: {percent_complete}%\n"
                f"   📚 Часы тренинга: {hours.get('training_hours', 0)}\n"
                f"   💸 Штрафы: {hours.get('fines', 0)}\n\n"
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
    
    for op_id, op_name, _, _, _, _ in operators:
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
            callback_data=f"notify_{sv_id}_{1}"))
    
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
        for op in operators:
            ikb.insert(InlineKeyboardButton(text=op['name'], callback_data=f"cred_{op['id']}"))
        
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
                parse_mode='HTML',
                reply_markup=get_sv_keyboard()
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
            sheet_name, operators, error = extract_fio_and_links(message.text)
            if error:
                await bot.send_message(
                    chat_id=message.from_user.id,
                    text=f"{error}\n\n<b>Пожалуйста, отправьте корректную ссылку на таблицу.</b>",
                    parse_mode="HTML",
                    reply_markup=get_cancel_keyboard()
                )
                return

            async with state.proxy() as data:
                data['hours_table_url'] = message.text
                data['operators'] = operators
                data['sheet_name'] = sheet_name
                data['sv_id'] = user[0]

            message_text = f"<b>Название листа:</b> {sheet_name}\n\n<b>ФИО операторов:</b>\n"
            for op in operators:
                message_text += f"👤 {op['name']}\n"
            message_text += "\n<b>Это все ваши операторы?</b>"

            await bot.send_message(
                chat_id=message.from_user.id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=get_verify_keyboard(),
                disable_web_page_preview=True
            )
            await state.set_state("verify_hours_table")
            await message.delete()
        except Exception as e:
            await bot.send_message(
                chat_id=message.from_user.id,
                text=f"<b>Ошибка при обработке таблицы: {str(e)}</b>",
                parse_mode='HTML'
            )
            await state.finish()

@dp.callback_query_handler(state="verify_hours_table")
async def verify_hours_table(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        hours_table_url = data.get('hours_table_url')
        sv_id = data.get('sv_id')
        operators = data.get('operators')
        sheet_name = data.get('sheet_name')

    user = db.get_user(id=sv_id)
    if not user:
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        await state.finish()
        return

    if callback.data == "verify_yes":
        await bot.send_message(
            chat_id=callback.from_user.id,
            text="Выберите направление для операторов:",
            reply_markup=get_direction_keyboard()
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await state.set_state("select_hours_direction")
    elif callback.data == "verify_no":
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f'<b>Отправьте корректную таблицу часов для {user[2]}🖊</b>',
            parse_mode='HTML',
            reply_markup=get_cancel_keyboard()
        )
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        await state.set_state("waiting_for_hours_table")

@dp.callback_query_handler(state="select_hours_direction")
async def select_hours_direction(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        hours_table_url = data.get('hours_table_url')
        sv_id = data.get('sv_id')
        operators = data.get('operators')

    # Извлекаем direction_id из callback_data
    direction_id = None
    if callback.data.startswith("dir_"):
        try:
            direction_id = int(callback.data.replace("dir_", ""))
        except ValueError:
            await bot.answer_callback_query(callback.id, text="Ошибка: Неверный формат направления")
            return

    direction = next((d for d in db.get_directions() if d['id'] == direction_id), None)
    if not direction:
        await bot.answer_callback_query(callback.id, text="Ошибка: Направление не найдено")
        await state.finish()
        return

    user = db.get_user(id=sv_id)
    if not user:
        await bot.answer_callback_query(callback.id, text="Ошибка: СВ не найден")
        await state.finish()
        return

    # Обновляем таблицу часов супервайзера
    db.update_user_table(user[0], hours_table_url=hours_table_url)

    # Создаём/обновляем операторов с direction_id
    for op in operators:
        db.create_user(
            telegram_id=None,
            name=op['name'],
            role='operator',
            direction_id=direction_id,
            supervisor_id=user[0]
        )

    await bot.send_message(
        chat_id=callback.from_user.id,
        text=f"""<b>Таблица часов сохранена, операторы добавлены/обновлены с направлением "{direction['name']}"✅</b>""",
        parse_mode='HTML',
        reply_markup=get_sv_keyboard()
    )
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
    await state.finish()


@dp.message_handler(regexp='Доступ🔑')
async def change_credentials_menu(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user:        
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите что хотите изменить:</b>",
            parse_mode='HTML',
            reply_markup=get_access_keyboard()
        )
    await message.delete()

@dp.message_handler(regexp='Сменить логин')
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

@dp.message_handler(regexp='Сменить пароль')
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

@dp.message_handler(regexp='Выход🚪')
async def logout_user(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user:
        # Обнуляем telegram_id в базе данных
        with db._get_cursor() as cursor:
            cursor.execute("UPDATE users SET telegram_id = NULL WHERE id = %s", (user[0],))
        await bot.send_message(  
            chat_id=message.from_user.id,
            text="✅ <b>Вы успешно вышли из системы.</b>Для входа снова нажмите 'Вход👤'.",
            parse_mode='HTML',
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton('Вход👤'))
            )
    else:
        await bot.send_message(
            chat_id=message.from_user.id,
            text="❌ Вы не вошли в систему.",
            parse_mode='HTML'
        )
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
            f"⏱ <b>Часы работы:</b> {stats['regular_hours']} из {stats['norm_hours']} ({stats['percent_complete']}%)\n"
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
        for eval in evaluations[:5]:  # Показываем последние 5 оценок (уже только последние версии)
            correction_mark = " (корректировка)" if eval['is_correction'] else ""
            message_text += (
                f"📞 <b>Звонок {eval['call_number']}{correction_mark}</b>\n"
                f"   📅 {eval['month']}\n"
                f"   📱 {eval['phone_number']}\n"
                f"   ⭐ Оценка: <b>{eval['score']}</b>\n"
                f"   🕒 Дата оценки: {eval['evaluation_date']}\n"
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
        for col in ws.iter_cols(min_row=1, max_row=1):
            for cell in col:
                if cell.value is not None and "ФИО" in str(cell.value).strip():
                    fio_column = cell.column

        if not fio_column:
            os.remove(temp_file)
            return None, None, "Колонка ФИО не найдена."

        operators = []
        for row in ws.iter_rows(min_row=2):
            fio_cell = row[fio_column - 1]
            if not fio_cell.value:
                break
            operator_info = {
                "name": str(fio_cell.value)
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
        
        current_date = datetime.now()
        current_month = current_date.strftime('%Y-%m')
        current_week = get_current_week_of_month()
        now = current_date.strftime("%Y-%m-%d %H:%M:%S")
        output = BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#D3D3D3',
            'border': 1
        })
        cell_format_int = workbook.add_format({'border': 1, 'num_format': '0'})
        cell_format_float = workbook.add_format({'border': 1, 'num_format': '0.00'})
        
        # Определение недель текущего месяца
        first_day = current_date.replace(day=1)
        weeks = []
        for w in range(1, current_week + 1):
            start_day = (w - 1) * 7 + 1
            start_date = first_day + timedelta(days=start_day - 1)
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_day = min(start_day + 6, current_date.day)
            end_date = first_day + timedelta(days=end_day - 1)
            end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
            weeks.append((w, start_date, end_date))
        
        # Get supervisors from the database
        svs = db.get_supervisors()
        
        for sv_id, sv_name, _, _ in svs:
            operators = db.get_operators_by_supervisor(sv_id)
            
            safe_sheet_name = sv_name[:31].replace('/', '_').replace('\\', '_').replace('?', '_').replace('*', '_').replace('[', '_').replace(']', '_')
            worksheet = workbook.add_worksheet(safe_sheet_name)
            
            # Headers
            headers = ['ФИО']
            for w, _, _ in weeks:
                headers.append(f'Неделя {w} Количество звонков')
                headers.append(f'Неделя {w} Средний балл')
            
            for col, header in enumerate(headers):
                worksheet.write(0, col, header, header_format)
            
            for row_idx, op in enumerate(operators, start=1):
                op_name = op['name']
                worksheet.write(row_idx, 0, op_name, cell_format_int)
                col = 1
                for w, start_date, end_date in weeks:
                    call_count, avg_score = db.get_week_call_stats(op['id'], start_date, end_date)
                    worksheet.write(row_idx, col, call_count, cell_format_int)
                    col += 1
                    worksheet.write(row_idx, col, avg_score, cell_format_float)
                    col += 1
            
            worksheet.set_column('A:A', 30)
            for i in range(1, len(headers)):
                worksheet.set_column(i, i, 20)
        
        workbook.close()
        output.seek(0)
        
        if output.getvalue():
            filename = f"Weekly_Report_{current_month}_{current_date.strftime('%Y%m%d')}.xlsx"
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
                data = {'chat_id': admin_id, 'caption': f"[{now}] 📊 Отчет по неделям за {current_month} (до {current_week}-й недели)"}
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
