import logging
import os
import threading
import asyncio
import requests
import hashlib
import base64
import hmac
import jwt
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from io import BytesIO
from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.dispatcher import FSMContext
from flask import Flask, request, jsonify, send_file, g
from flask_cors import CORS
from functools import wraps
from openpyxl import load_workbook, Workbook
import re
import xlsxwriter
import json
import html
from concurrent.futures import ThreadPoolExecutor
from database import db
import uuid
from passlib.hash import pbkdf2_sha256
from werkzeug.utils import secure_filename
from google.cloud import storage as gcs_storage
import tempfile
from datetime import datetime, timedelta, date as dt_date
import time
import math
from urllib.parse import quote, urlparse, parse_qs
from ai_feed_back_service import generate_monthly_feedback_with_ai

os.environ['TZ'] = 'Asia/Almaty'
time.tzset()

# === Логирование =====================================================================================================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# === Переменные окружения =========================================================================================
API_TOKEN = os.getenv('BOT_TOKEN')
FLASK_API_KEY = os.getenv('FLASK_API_KEY')
admin4 = int(os.getenv('ADMIN_ID_4', '0'))
ADMIN_LOGIN_CD = os.getenv('ADMIN_LOGIN_CD', 'admin4')
ADMIN_PASSWORD_CD = os.getenv('ADMIN_PASSWORD_CD', 'admin1234')

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
JWT_SECRET = os.getenv('JWT_SECRET') or FLASK_API_KEY
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_MINUTES = int(os.getenv('JWT_ACCESS_TOKEN_MINUTES', '30'))
JWT_REFRESH_TOKEN_DAYS = int(os.getenv('JWT_REFRESH_TOKEN_DAYS', '30'))
JWT_ACCESS_COOKIE_NAME = os.getenv('JWT_ACCESS_COOKIE_NAME', 'otp_access_token')
JWT_REFRESH_COOKIE_NAME = os.getenv('JWT_REFRESH_COOKIE_NAME', 'otp_refresh_token')
JWT_REFRESH_HEADER_NAME = os.getenv('JWT_REFRESH_HEADER_NAME', 'X-Refresh-Token')
JWT_COOKIE_DOMAIN = os.getenv('JWT_COOKIE_DOMAIN', None)
JWT_COOKIE_SAMESITE = os.getenv('JWT_COOKIE_SAMESITE', 'None')
JWT_COOKIE_SECURE = os.getenv('JWT_COOKIE_SECURE', 'true').lower() == 'true'
JWT_COOKIE_PARTITIONED = os.getenv('JWT_COOKIE_PARTITIONED', 'true').lower() == 'true'
JWT_TOKEN_PEPPER = os.getenv('JWT_TOKEN_PEPPER', JWT_SECRET)
SENSITIVE_QR_SECRET = os.getenv('SENSITIVE_QR_SECRET', JWT_SECRET)
SENSITIVE_QR_TTL_SECONDS = int(os.getenv('SENSITIVE_QR_TTL_SECONDS', '300'))

ALLOWED_ORIGINS = {
    "https://alfa330.github.io",
    "https://call-evalution.pages.dev",
    "https://szov.pages.dev",
    "https://moders.pages.dev",
    "https://table-7kx.pages.dev",
    "https://base-pmy9.onrender.com"
}

if not os.getenv('JWT_SECRET'):
    logging.warning("JWT_SECRET is not set. Falling back to FLASK_API_KEY for signing tokens.")


def _normalize_origin(origin):
    if not origin:
        return ""
    return str(origin).strip().rstrip("/")


def _is_allowed_origin(origin):
    origin = _normalize_origin(origin)
    if not origin:
        return False
    if origin in ALLOWED_ORIGINS:
        return True
    return origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:")


CORS(app, resources={
    r"/api/*": {
        "origins": list(ALLOWED_ORIGINS) + [r"http://localhost:\d+", r"http://127\.0\.0\.1:\d+"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-API-Key", "X-User-Id", "Authorization", "X-Refresh-Token"],
        "supports_credentials": True,
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


class AuthError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _client_ip():
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.remote_addr


def _hash_token(raw_token):
    value = f"{raw_token}:{JWT_TOKEN_PEPPER}"
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _get_user_payload(user):
    return {
        "role": user[3],
        "id": user[0],
        "name": user[2],
        "telegram_id": user[1]
    }


def _set_request_auth_context(user_id):
    request.environ['HTTP_X_USER_ID'] = str(user_id)
    request.environ['HTTP_X_API_KEY'] = FLASK_API_KEY
    request.user_id = int(user_id)
    g.user_id = int(user_id)


def _decode_token(token, expected_type, verify_exp=True):
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": verify_exp}
        )
    except jwt.ExpiredSignatureError as e:
        raise AuthError("TOKEN_EXPIRED", "JWT token expired") from e
    except jwt.InvalidTokenError as e:
        raise AuthError("INVALID_TOKEN", "Invalid JWT token") from e

    token_type = payload.get("type")
    if token_type != expected_type:
        raise AuthError("INVALID_TOKEN_TYPE", "Invalid token type")
    if not payload.get("sub") or not payload.get("sid"):
        raise AuthError("INVALID_TOKEN", "Malformed JWT token")
    return payload


def _build_access_token(user, session_id):
    now = datetime.utcnow()
    payload = {
        "sub": str(user[0]),
        "role": user[3],
        "name": user[2],
        "sid": str(session_id),
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_ACCESS_TOKEN_MINUTES)).timestamp())
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _build_refresh_token(user_id, session_id):
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=JWT_REFRESH_TOKEN_DAYS)).timestamp())
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _cookie_options():
    secure = JWT_COOKIE_SECURE
    origin = request.headers.get('Origin', '')
    if origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:"):
        secure = False

    samesite = JWT_COOKIE_SAMESITE
    if not secure and str(samesite).lower() == 'none':
        samesite = 'Lax'

    partitioned = bool(JWT_COOKIE_PARTITIONED and secure and str(samesite).lower() == 'none')

    return {
        "httponly": True,
        "secure": secure,
        "samesite": samesite,
        "domain": JWT_COOKIE_DOMAIN,
        "path": "/",
        "partitioned": partitioned
    }


def _serialize_cookie(name, value, max_age, cookie_kwargs, expires=None):
    safe_value = quote(value, safe="!#$%&'()*+-./:<=>?@[]^_`{|}~")
    parts = [f"{name}={safe_value}"]
    parts.append(f"Path={cookie_kwargs['path']}")
    parts.append(f"Max-Age={int(max_age)}")
    if cookie_kwargs.get('domain'):
        parts.append(f"Domain={cookie_kwargs['domain']}")
    if expires:
        parts.append(f"Expires={expires}")
    if cookie_kwargs.get('httponly'):
        parts.append("HttpOnly")
    if cookie_kwargs.get('secure'):
        parts.append("Secure")
    if cookie_kwargs.get('samesite'):
        parts.append(f"SameSite={cookie_kwargs['samesite']}")
    if cookie_kwargs.get('partitioned'):
        parts.append("Partitioned")
    return "; ".join(parts)


def _set_cookie_header(response, name, value, max_age):
    cookie_kwargs = _cookie_options()
    cookie_header = _serialize_cookie(name, value, max_age=max_age, cookie_kwargs=cookie_kwargs)
    response.headers.add("Set-Cookie", cookie_header)


def _clear_cookie_header(response, name):
    cookie_kwargs = _cookie_options()
    cookie_header = _serialize_cookie(
        name,
        '',
        max_age=0,
        cookie_kwargs=cookie_kwargs,
        expires="Thu, 01 Jan 1970 00:00:00 GMT"
    )
    response.headers.add("Set-Cookie", cookie_header)


def _set_auth_cookies(response, access_token, refresh_token):
    _set_cookie_header(response, JWT_ACCESS_COOKIE_NAME, access_token, JWT_ACCESS_TOKEN_MINUTES * 60)
    _set_cookie_header(response, JWT_REFRESH_COOKIE_NAME, refresh_token, JWT_REFRESH_TOKEN_DAYS * 24 * 60 * 60)


def _clear_auth_cookies(response):
    _clear_cookie_header(response, JWT_ACCESS_COOKIE_NAME)
    _clear_cookie_header(response, JWT_REFRESH_COOKIE_NAME)


def _get_cookie_values(cookie_name):
    # request.cookies may behave inconsistently if duplicate cookie names exist.
    # Parse raw Cookie header and keep all occurrences.
    raw_cookie = request.headers.get('Cookie', '') or ''
    values = []
    for chunk in raw_cookie.split(';'):
        part = chunk.strip()
        if not part or '=' not in part:
            continue
        key, val = part.split('=', 1)
        if key.strip() == cookie_name:
            values.append(val.strip())
    if not values:
        fallback = request.cookies.get(cookie_name)
        if fallback:
            values.append(fallback)
    return values


def _get_cookie_value(cookie_name):
    values = _get_cookie_values(cookie_name)
    if not values:
        return None
    return values[-1]


def _get_bearer_access_token():
    auth_header = (request.headers.get('Authorization') or '').strip()
    if not auth_header:
        return None
    parts = auth_header.split(' ', 1)
    if len(parts) != 2 or parts[0].strip().lower() != 'bearer':
        return None
    token = parts[1].strip()
    return token or None


def _get_refresh_header_token():
    token = (request.headers.get(JWT_REFRESH_HEADER_NAME) or '').strip()
    return token or None


def _get_access_token_values():
    tokens = _get_cookie_values(JWT_ACCESS_COOKIE_NAME)
    header_token = _get_bearer_access_token()
    if header_token:
        tokens.append(header_token)
    return tokens


def _get_refresh_token_values():
    tokens = _get_cookie_values(JWT_REFRESH_COOKIE_NAME)
    header_token = _get_refresh_header_token()
    if header_token:
        tokens.append(header_token)
    return tokens


def _authenticate_access_cookie(optional=True, touch_session=True):
    access_tokens = _get_access_token_values()
    if not access_tokens:
        if optional:
            return None
        raise AuthError("MISSING_TOKEN", "Missing access token")

    last_error = AuthError("INVALID_TOKEN", "Invalid access token")
    for access_token in reversed(access_tokens):
        try:
            payload = _decode_token(access_token, expected_type="access", verify_exp=True)
            user_id = int(payload["sub"])
            session_id = str(payload["sid"])
            session = db.get_user_session(session_id=session_id, user_id=user_id)
            if not session:
                raise AuthError("SESSION_NOT_FOUND", "Session not found")
            if session["revoked_at"] is not None:
                raise AuthError("SESSION_REVOKED", "Session revoked")
            if session["expires_at"] and session["expires_at"] < datetime.utcnow():
                raise AuthError("SESSION_EXPIRED", "Session expired")

            if touch_session:
                db.touch_user_session(
                    session_id=session_id,
                    user_id=user_id,
                    ip_address=_client_ip(),
                    user_agent=request.headers.get('User-Agent')
                )

            _set_request_auth_context(user_id)
            return payload
        except AuthError as auth_error:
            last_error = auth_error
            continue

    raise last_error


def _authenticate_refresh_cookie(optional=True, rotate_tokens=True):
    refresh_tokens = _get_refresh_token_values()
    if not refresh_tokens:
        if optional:
            return None
        raise AuthError("MISSING_REFRESH_TOKEN", "Missing refresh token")

    last_error = AuthError("INVALID_TOKEN", "Invalid refresh token")
    for refresh_token in reversed(refresh_tokens):
        try:
            payload = _decode_token(refresh_token, expected_type="refresh", verify_exp=True)
            user_id = int(payload["sub"])
            session_id = str(payload["sid"])

            session = db.get_user_session(session_id=session_id, user_id=user_id)
            if not session:
                raise AuthError("SESSION_NOT_FOUND", "Session not found")
            if session["revoked_at"] is not None:
                raise AuthError("SESSION_REVOKED", "Session revoked")
            if session["expires_at"] and session["expires_at"] < datetime.utcnow():
                raise AuthError("SESSION_EXPIRED", "Session expired")
            if session["refresh_token_hash"] != _hash_token(refresh_token):
                raise AuthError("REFRESH_TOKEN_MISMATCH", "Refresh token mismatch")

            user = db.get_user(id=user_id)
            if not user:
                raise AuthError("USER_NOT_FOUND", "User not found")

            if rotate_tokens:
                new_access_token = _build_access_token(user, session_id)
                new_refresh_token = _build_refresh_token(user_id, session_id)
                db.rotate_user_session_token(
                    session_id=session_id,
                    user_id=user_id,
                    refresh_token_hash=_hash_token(new_refresh_token),
                    expires_at=datetime.utcnow() + timedelta(days=JWT_REFRESH_TOKEN_DAYS)
                )
                g.pending_auth_tokens = (new_access_token, new_refresh_token)
            else:
                db.touch_user_session(
                    session_id=session_id,
                    user_id=user_id,
                    ip_address=_client_ip(),
                    user_agent=request.headers.get('User-Agent')
                )

            _set_request_auth_context(user_id)
            return payload
        except AuthError as auth_error:
            last_error = auth_error
            continue

    raise last_error


def _current_session_id_from_access_token():
    access_token = _get_cookie_value(JWT_ACCESS_COOKIE_NAME) or _get_bearer_access_token()
    if not access_token:
        return None
    try:
        payload = _decode_token(access_token, expected_type="access", verify_exp=False)
        return str(payload.get("sid"))
    except AuthError:
        return None


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('utf-8').rstrip('=')


def _base64url_decode(value: str) -> bytes:
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode('utf-8'))


def _is_privileged_role(role: str) -> bool:
    return role in ('admin', 'sv')


def _mask_phone_number(phone_number):
    if phone_number is None:
        return None
    raw = str(phone_number).strip()
    if not raw:
        return raw
    digits = re.sub(r'\D', '', raw)
    if not digits:
        return '*' * min(len(raw), 6)
    if len(digits) <= 4:
        return '*' * len(digits)
    return ('*' * (len(digits) - 4)) + digits[-4:]


def _build_sensitive_qr_token(session_id, user_id):
    expires_at = datetime.utcnow() + timedelta(seconds=SENSITIVE_QR_TTL_SECONDS)
    payload = {
        "sid": str(session_id),
        "uid": int(user_id),
        "exp": int(expires_at.timestamp()),
        "nonce": uuid.uuid4().hex
    }
    payload_json = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    payload_b64 = _base64url_encode(payload_json)
    signature = hmac.new(
        SENSITIVE_QR_SECRET.encode('utf-8'),
        payload_b64.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    token = f"{payload_b64}.{signature}"
    return token, expires_at


def _decode_sensitive_qr_token(token):
    if not token or '.' not in token:
        raise ValueError("Invalid QR token format")

    payload_b64, signature = token.rsplit('.', 1)
    expected_signature = hmac.new(
        SENSITIVE_QR_SECRET.encode('utf-8'),
        payload_b64.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("Invalid QR token signature")

    try:
        payload_raw = _base64url_decode(payload_b64)
        payload = json.loads(payload_raw.decode('utf-8'))
    except Exception as exc:
        raise ValueError("Invalid QR token payload") from exc

    session_id = str(payload.get('sid') or '').strip()
    user_id = payload.get('uid')
    exp_ts = payload.get('exp')

    try:
        user_id = int(user_id)
        exp_ts = int(exp_ts)
    except Exception as exc:
        raise ValueError("Invalid QR token claims") from exc

    if not session_id:
        raise ValueError("Invalid QR token session")

    now_ts = int(datetime.utcnow().timestamp())
    if exp_ts <= now_ts:
        raise ValueError("QR token expired")

    return {
        "session_id": session_id,
        "user_id": user_id,
        "expires_at": datetime.utcfromtimestamp(exp_ts)
    }


def _is_sensitive_access_unlocked(user_id, session_id):
    if not user_id or not session_id:
        return False
    try:
        return db.is_session_sensitive_access_unlocked(session_id=session_id, user_id=user_id)
    except Exception as exc:
        logging.error(f"Failed to check sensitive session access: {exc}")
        return False


def _sanitize_evaluations_for_access(evaluations, reveal_sensitive):
    result = []
    for ev in (evaluations or []):
        if not isinstance(ev, dict):
            result.append(ev)
            continue

        item = dict(ev)
        phone_masked = _mask_phone_number(item.get('phone_number'))
        item['phone_number_masked'] = phone_masked
        if not reveal_sensitive:
            item['phone_number'] = phone_masked
            item['audio_path'] = None
        result.append(item)
    return result


def _authorize_operator_scope(requester, requester_id, operator_id):
    role = requester[3]
    if role == 'admin':
        return True
    if role == 'operator':
        return requester_id == operator_id
    if role == 'sv':
        operator = db.get_user(id=operator_id)
        return bool(operator and operator[3] == 'operator' and operator[6] == requester_id)
    return False


def _ensure_call_access_for_requester(call_operator_id, requester, requester_id):
    role = requester[3]
    if role == 'admin':
        return True
    if role == 'operator':
        return requester_id == call_operator_id
    if role == 'sv':
        operator = db.get_user(id=call_operator_id)
        return bool(operator and operator[3] == 'operator' and operator[6] == requester_id)
    return False


TASK_ALLOWED_TAGS = {'task', 'problem', 'suggestion'}
TASK_ALLOWED_ACTIONS = {'in_progress', 'completed', 'accepted', 'returned', 'reopened'}
TASK_MAX_FILES = 10
TASK_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024
TASK_ATTACHMENTS_UPLOAD_FOLDER = (os.getenv('TASK_ATTACHMENTS_UPLOAD_FOLDER') or 'TaskAttachments/').strip()
TASK_TAG_LABELS = {
    'task': 'Задача',
    'problem': 'Проблема',
    'suggestion': 'Предложение'
}


def _resolve_requester():
    requester_id_raw = request.headers.get('X-User-Id') or getattr(g, 'user_id', None)
    if not requester_id_raw:
        return None, None, ("X-User-Id header required", 400)
    try:
        requester_id = int(requester_id_raw)
    except Exception:
        return None, None, ("Invalid X-User-Id", 400)

    requester = db.get_user(id=requester_id)
    if not requester:
        return None, None, ("Requester not found", 404)
    return requester_id, requester, None


def _task_route_guard():
    requester_id, requester, error = _resolve_requester()
    if error:
        message, status_code = error
        return None, None, jsonify({"error": message}), status_code
    if requester[3] not in ('admin', 'sv'):
        return None, None, jsonify({"error": "Only admin and sv can access tasks"}), 403
    return requester_id, requester, None, None


def _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths):
    if not gcs_bucket or not uploaded_blob_paths:
        return
    for uploaded_path in uploaded_blob_paths:
        try:
            gcs_bucket.blob(uploaded_path).delete()
        except Exception:
            pass


def _upload_task_attachments_to_gcs(files, stage='initial'):
    file_items = [item for item in (files or []) if item and item.filename]
    if len(file_items) > TASK_MAX_FILES:
        raise ValueError(f"Too many files. Max allowed: {TASK_MAX_FILES}")
    if not file_items:
        return [], [], None

    bucket_name = (os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET_TASKS') or '').strip()
    if not bucket_name:
        raise RuntimeError("GOOGLE_CLOUD_STORAGE_BUCKET_TASKS is not configured")

    upload_folder = TASK_ATTACHMENTS_UPLOAD_FOLDER.strip('/')
    upload_prefix = f"{upload_folder}/" if upload_folder else ""
    gcs_client = get_gcs_client()
    gcs_bucket = gcs_client.bucket(bucket_name)

    attachments = []
    uploaded_blob_paths = []

    for idx, file_storage in enumerate(file_items):
        file_data = file_storage.read() or b''
        if len(file_data) > TASK_MAX_FILE_SIZE_BYTES:
            raise ValueError(f"File '{file_storage.filename}' exceeds 10 MB limit")

        safe_name = secure_filename(file_storage.filename) or f'attachment_{idx + 1}'
        content_type = (file_storage.mimetype or '').strip() or 'application/octet-stream'
        blob_path = (
            f"{upload_prefix}tasks/{stage}/"
            f"{datetime.utcnow().strftime('%Y/%m/%d')}/"
            f"{uuid.uuid4().hex}_{safe_name}"
        )

        blob = gcs_bucket.blob(blob_path)
        blob.upload_from_string(file_data, content_type=content_type)
        uploaded_blob_paths.append(blob_path)

        attachments.append({
            "file_name": safe_name,
            "content_type": content_type,
            "file_size": len(file_data),
            "storage_type": "gcs",
            "gcs_bucket": bucket_name,
            "gcs_blob_path": blob_path
        })

    return attachments, uploaded_blob_paths, gcs_bucket


@app.before_request
def hydrate_user_context_from_jwt():
    if not request.path.startswith('/api/'):
        return None
    if request.method == 'OPTIONS':
        return None

    if request.path == '/api/auth/refresh':
        return None

    try:
        payload = _authenticate_access_cookie(optional=True)
        if payload:
            return None
    except AuthError as auth_error:
        request.environ['JWT_AUTH_ERROR_CODE'] = auth_error.code

    try:
        _authenticate_refresh_cookie(optional=True, rotate_tokens=False)
    except AuthError as refresh_auth_error:
        request.environ['JWT_AUTH_ERROR_CODE'] = refresh_auth_error.code
    return None

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        if getattr(g, 'user_id', None):
            return f(*args, **kwargs)

        try:
            if _authenticate_access_cookie(optional=True):
                return f(*args, **kwargs)
        except AuthError as auth_error:
            request.environ['JWT_AUTH_ERROR_CODE'] = auth_error.code

        try:
            if _authenticate_refresh_cookie(optional=True, rotate_tokens=False):
                return f(*args, **kwargs)
        except AuthError as refresh_auth_error:
            request.environ['JWT_AUTH_ERROR_CODE'] = refresh_auth_error.code

        auth_error_code = request.environ.get('JWT_AUTH_ERROR_CODE')
        if auth_error_code:
            return jsonify({"error": "JWT authentication failed", "code": auth_error_code}), 401

        if _get_refresh_token_values():
            return jsonify({"error": "Access token required", "code": "TOKEN_EXPIRED"}), 401

        logging.warning("Unauthorized request: no valid JWT token")
        return jsonify({"error": "Unauthorized"}), 401
    return decorated

def _build_cors_preflight_response():
    origin = _normalize_origin(request.headers.get("Origin"))
    response = jsonify({"status": "ok"})

    if _is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"

    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, X-User-Id, Authorization, X-Refresh-Token"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Max-Age"] = "86400"
    return response

@app.route('/')
def index():
    return "Bot is alive!", 200

@app.after_request
def after_request(response):
    origin = _normalize_origin(request.headers.get('Origin'))
    if _is_allowed_origin(origin):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Vary'] = 'Origin'

    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Key, X-User-Id, Authorization, X-Refresh-Token'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS, DELETE, PUT'

    pending_tokens = getattr(g, 'pending_auth_tokens', None)
    if pending_tokens:
        access_token, refresh_token = pending_tokens
        _set_auth_cookies(response, access_token, refresh_token)

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

@app.route('/api/login', methods=['POST', 'OPTIONS'])
def login():
    try:
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        data = request.get_json() or {}
        login_value = data.get('login', '').strip()
        password = data.get('password', '')
        if not login_value or not password:
            return jsonify({"error": "Missing credentials"}), 400

        user = db.get_user_by_login(login_value)
        if user and db.verify_password(user[0], password):
            session_id = str(uuid.uuid4())
            access_token = _build_access_token(user, session_id)
            refresh_token = _build_refresh_token(user[0], session_id)
            db.create_user_session(
                session_id=session_id,
                user_id=user[0],
                refresh_token_hash=_hash_token(refresh_token),
                expires_at=datetime.utcnow() + timedelta(days=JWT_REFRESH_TOKEN_DAYS),
                user_agent=request.headers.get('User-Agent'),
                ip_address=_client_ip()
            )
            _set_request_auth_context(user[0])
            payload = _get_user_payload(user)
            response = jsonify({
                "status": "success",
                "user": payload,
                **payload,
                "access_token": access_token,
                "refresh_token": refresh_token
            })
            _set_auth_cookies(response, access_token, refresh_token)
            return response, 200

        return jsonify({"error": "Invalid credentials"}), 401
    except Exception as e:
        logging.error(f"Login error: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/auth/me', methods=['GET'])
@require_api_key
def auth_me():
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({"error": "Unauthorized"}), 401
        user = db.get_user(id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404
        payload = _get_user_payload(user)
        return jsonify({"status": "success", "user": payload, **payload}), 200
    except Exception as e:
        logging.error(f"auth_me error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/auth/refresh', methods=['POST', 'OPTIONS'])
def refresh_auth():
    try:
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        _authenticate_refresh_cookie(optional=False, rotate_tokens=True)
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            raise AuthError("UNAUTHORIZED", "Unauthorized")
        user = db.get_user(id=user_id)
        if not user:
            raise AuthError("USER_NOT_FOUND", "User not found")

        payload_user = _get_user_payload(user)
        response_payload = {"status": "success", "user": payload_user, **payload_user}
        pending_tokens = getattr(g, 'pending_auth_tokens', None)
        if pending_tokens:
            response_payload["access_token"] = pending_tokens[0]
            response_payload["refresh_token"] = pending_tokens[1]
        response = jsonify(response_payload)
        return response, 200
    except AuthError as auth_error:
        response = jsonify({"error": auth_error.message, "code": auth_error.code})
        _clear_auth_cookies(response)
        return response, 401
    except Exception as e:
        logging.error(f"refresh_auth error: {e}", exc_info=True)
        response = jsonify({"error": "Internal server error"})
        _clear_auth_cookies(response)
        return response, 500


@app.route('/api/logout', methods=['POST', 'OPTIONS'])
def logout():
    try:
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        session_id = _current_session_id_from_access_token()
        user_id = getattr(g, 'user_id', None)
        if session_id and user_id:
            db.revoke_user_session(session_id=session_id, user_id=user_id)
        elif session_id:
            db.revoke_user_session(session_id=session_id)

        response = jsonify({"status": "success"})
        _clear_auth_cookies(response)
        return response, 200
    except Exception as e:
        logging.error(f"logout error: {e}", exc_info=True)
        response = jsonify({"status": "success"})
        _clear_auth_cookies(response)
        return response, 200


@app.route('/api/auth/logout_all', methods=['POST', 'OPTIONS'])
@require_api_key
def logout_all():
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({"error": "Unauthorized"}), 401
        db.revoke_all_user_sessions(user_id=user_id)
        response = jsonify({"status": "success"})
        _clear_auth_cookies(response)
        return response, 200
    except Exception as e:
        logging.error(f"logout_all error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/auth/sessions', methods=['GET', 'OPTIONS'])
@require_api_key
def list_auth_sessions():
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({"error": "Unauthorized"}), 401
        current_session_id = _current_session_id_from_access_token()
        sessions = db.list_user_sessions(user_id=user_id)
        serialized = []
        for item in sessions:
            serialized.append({
                "session_id": item["session_id"],
                "is_current": item["session_id"] == current_session_id,
                "user_agent": item["user_agent"],
                "ip_address": item["ip_address"],
                "created_at": item["created_at"].isoformat() if item["created_at"] else None,
                "last_seen_at": item["last_seen_at"].isoformat() if item["last_seen_at"] else None,
                "expires_at": item["expires_at"].isoformat() if item["expires_at"] else None,
                "revoked_at": item["revoked_at"].isoformat() if item["revoked_at"] else None
            })
        return jsonify({"status": "success", "sessions": serialized}), 200
    except Exception as e:
        logging.error(f"list_auth_sessions error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/auth/sessions/<session_id>/revoke', methods=['POST', 'OPTIONS'])
@require_api_key
def revoke_auth_session(session_id):
    try:
        user_id = getattr(g, 'user_id', None)
        if not user_id:
            return jsonify({"error": "Unauthorized"}), 401
        revoked = db.revoke_user_session(session_id=session_id, user_id=user_id)
        if not revoked:
            return jsonify({"error": "Session not found"}), 404

        response = jsonify({"status": "success"})
        if _current_session_id_from_access_token() == session_id:
            _clear_auth_cookies(response)
        return response, 200
    except Exception as e:
        logging.error(f"revoke_auth_session error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/sessions', methods=['GET', 'OPTIONS'])
@require_api_key
def list_admin_sessions():
    try:
        requester_id = getattr(g, 'user_id', None)
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester = db.get_user(id=requester_id)
        if not requester or requester[3] != 'admin':
            return jsonify({"error": "Forbidden: only admins can access"}), 403

        current_session_id = _current_session_id_from_access_token()
        sessions = db.list_all_active_sessions()
        serialized = []
        for item in sessions:
            serialized.append({
                "session_id": item["session_id"],
                "user_id": item["user_id"],
                "user_name": item["user_name"],
                "user_role": item["user_role"],
                "user_login": item["user_login"],
                "supervisor_id": item["supervisor_id"],
                "supervisor_name": item["supervisor_name"],
                "is_current": item["session_id"] == current_session_id,
                "user_agent": item["user_agent"],
                "ip_address": item["ip_address"],
                "created_at": item["created_at"].isoformat() if item["created_at"] else None,
                "last_seen_at": item["last_seen_at"].isoformat() if item["last_seen_at"] else None,
                "expires_at": item["expires_at"].isoformat() if item["expires_at"] else None,
                "sensitive_data_unlocked": bool(item.get("sensitive_data_unlocked", False)),
                "sensitive_data_unlocked_at": (
                    item["sensitive_data_unlocked_at"].isoformat()
                    if item.get("sensitive_data_unlocked_at")
                    else None
                )
            })
        return jsonify({"status": "success", "sessions": serialized}), 200
    except Exception as e:
        logging.error(f"list_admin_sessions error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/sessions/<session_id>/revoke', methods=['POST', 'OPTIONS'])
@require_api_key
def revoke_admin_session(session_id):
    try:
        requester_id = getattr(g, 'user_id', None)
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester = db.get_user(id=requester_id)
        if not requester or requester[3] != 'admin':
            return jsonify({"error": "Forbidden: only admins can access"}), 403

        session = db.get_user_session(session_id=session_id)
        if not session or session["revoked_at"] is not None:
            return jsonify({"error": "Session not found"}), 404

        revoked = db.revoke_user_session(session_id=session_id)
        if not revoked:
            return jsonify({"error": "Session not found"}), 404

        current_session_revoked = _current_session_id_from_access_token() == session_id
        response = jsonify({
            "status": "success",
            "current_session_revoked": current_session_revoked
        })
        if current_session_revoked:
            _clear_auth_cookies(response)
        return response, 200
    except Exception as e:
        logging.error(f"revoke_admin_session error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/sensitive-access/qr/request', methods=['POST', 'OPTIONS'])
@require_api_key
def request_sensitive_access_qr():
    try:
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        requester_id = getattr(g, 'user_id', None)
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester = db.get_user(id=requester_id)
        if not requester or requester[3] != 'operator':
            return jsonify({"error": "Forbidden: only operator can generate QR access token"}), 403

        session_id = _current_session_id_from_access_token()
        if not session_id:
            return jsonify({"error": "Active session not found"}), 401

        session = db.get_user_session(session_id=session_id, user_id=requester_id)
        if not session or session["revoked_at"] is not None:
            return jsonify({"error": "Session not found or revoked"}), 401

        token, expires_at = _build_sensitive_qr_token(session_id=session_id, user_id=requester_id)
        qr_payload = f"OTP-SENSITIVE:{token}"

        return jsonify({
            "status": "success",
            "qr_payload": qr_payload,
            "token": token,
            "token_expires_at": expires_at.isoformat() + "Z",
            "granted": _is_sensitive_access_unlocked(requester_id, session_id),
            "required": True
        }), 200
    except Exception as e:
        logging.error(f"request_sensitive_access_qr error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/sensitive-access/status', methods=['GET', 'OPTIONS'])
@require_api_key
def get_sensitive_access_status():
    try:
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        requester_id = getattr(g, 'user_id', None)
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "User not found"}), 404

        role = requester[3]
        session_id = _current_session_id_from_access_token()
        required = role == 'operator'
        if required:
            granted = bool(_is_sensitive_access_unlocked(requester_id, session_id))
        else:
            granted = True

        return jsonify({
            "status": "success",
            "required": required,
            "granted": granted,
            "session_id": session_id
        }), 200
    except Exception as e:
        logging.error(f"get_sensitive_access_status error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/sensitive-access/revoke', methods=['POST', 'OPTIONS'])
@require_api_key
def revoke_sensitive_access():
    try:
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        requester_id = getattr(g, 'user_id', None)
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "User not found"}), 404

        session_id = _current_session_id_from_access_token()
        if not session_id:
            return jsonify({"error": "Active session not found"}), 401

        db.set_session_sensitive_access(session_id=session_id, user_id=requester_id, unlocked=False)
        return jsonify({"status": "success", "granted": False}), 200
    except Exception as e:
        logging.error(f"revoke_sensitive_access error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/sensitive-access/approve', methods=['POST', 'OPTIONS'])
@require_api_key
def approve_sensitive_access():
    try:
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        approver_id = getattr(g, 'user_id', None)
        if not approver_id:
            return jsonify({"error": "Unauthorized"}), 401

        approver = db.get_user(id=approver_id)
        if not approver or not _is_privileged_role(approver[3]):
            return jsonify({"error": "Forbidden: only admin or supervisor can approve QR"}), 403

        data = request.get_json(silent=True) or {}
        raw_token = (data.get('token') or '').strip()
        if not raw_token:
            return jsonify({"error": "Missing token"}), 400

        token = raw_token
        if raw_token.upper().startswith('OTP-SENSITIVE:'):
            token = raw_token.split(':', 1)[1].strip()
        if 'token=' in token and ('http://' in token or 'https://' in token):
            try:
                parsed = urlparse(token)
                query_token = parse_qs(parsed.query).get('token', [''])[0]
                if query_token:
                    token = query_token.strip()
            except Exception:
                pass

        claims = _decode_sensitive_qr_token(token)
        operator_id = claims["user_id"]
        operator = db.get_user(id=operator_id)
        if not operator or operator[3] != 'operator':
            return jsonify({"error": "QR token does not belong to operator session"}), 400

        session = db.get_user_session(session_id=claims["session_id"], user_id=operator_id)
        if not session or session["revoked_at"] is not None:
            return jsonify({"error": "Operator session not found or revoked"}), 410

        if approver[3] == 'sv' and operator[6] != approver_id:
            return jsonify({"error": "Supervisor can approve only own operators"}), 403

        updated = db.set_session_sensitive_access(
            session_id=claims["session_id"],
            user_id=operator_id,
            unlocked=True
        )
        if not updated:
            return jsonify({"error": "Failed to activate access for operator session"}), 409

        return jsonify({
            "status": "success",
            "operator_id": operator_id,
            "operator_name": operator[2],
            "session_id": claims["session_id"],
            "approved_by": approver[2],
            "approved_by_role": approver[3]
        }), 200
    except ValueError as token_error:
        return jsonify({"error": str(token_error)}), 400
    except Exception as e:
        logging.error(f"approve_sensitive_access error: {e}", exc_info=True)
        return jsonify({"error": "Internal server error"}), 500


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


@app.route('/api/average_scores', methods=['GET'])
@require_api_key
def api_average_scores():
    """Endpoint: /api/average_scores?start=YYYY-MM-DD&end=YYYY-MM-DD&operator_ids=1,2,3

    Returns per-operator average score and overall average for the period.
    """
    try:
        start = request.args.get('start')
        end = request.args.get('end')
        op_ids_raw = request.args.get('operator_ids')

        if not start or not end:
            return jsonify({"error": "Missing required parameters 'start' and 'end'"}), 400

        # parse optional operator ids
        operator_ids = None
        if op_ids_raw:
            try:
                operator_ids = [int(x) for x in re.split(r'\s*,\s*', op_ids_raw.strip()) if x]
            except Exception:
                return jsonify({"error": "Invalid operator_ids format; expected comma-separated integers"}), 400

        # normalize dates: accept YYYY-MM-DD or full ISO timestamps
        def _parse_date_param(s, end_of_day=False):
            try:
                if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
                    dt = datetime.strptime(s, '%Y-%m-%d')
                    if end_of_day:
                        return datetime.combine(dt.date(), datetime.max.time())
                    return datetime.combine(dt.date(), datetime.min.time())
                # try ISO parse
                return datetime.fromisoformat(s)
            except Exception:
                return None

        sd = _parse_date_param(start, end_of_day=False)
        ed = _parse_date_param(end, end_of_day=True)
        if not sd or not ed:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD or ISO format."}), 400

        result = db.get_average_scores_for_period(sd, ed, operator_ids)
        return jsonify({"status": "success", "data": result}), 200
    except Exception as e:
        logging.exception("Error in /api/average_scores: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/baiga/upload_day_csv', methods=['POST', 'OPTIONS'])
@require_api_key
def api_baiga_upload_day_csv():
    # Upload CSV file for a specific day, process server-side and save counts.
    try:
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        # expected: form-data with file field 'file' and 'day' in YYYY-MM-DD
        day = request.form.get('day') or request.args.get('day')
        if not day:
            return jsonify({"error": "Missing 'day' parameter (YYYY-MM-DD)"}), 400

        if 'file' not in request.files:
            return jsonify({"error": "Missing file field"}), 400

        f = request.files['file']
        raw = f.read()
        try:
            text = raw.decode('utf-8')
        except Exception:
            try:
                text = raw.decode('cp1251')
            except Exception:
                text = raw.decode('utf-8', errors='ignore')

        # parse CSV robustly using csv module with ';' as delimiter
        sio = BytesIO(text.encode('utf-8'))
        # Use text IO wrapper
        import io, csv
        si = io.StringIO(text)
        reader = csv.reader(si, delimiter=';', quotechar='"')

        rows = list(reader)
        if not rows:
            return jsonify({"error": "Empty CSV"}), 400

        # Skip header (first row)
        score_counts = {}
        for i in range(1, len(rows)):
            row = rows[i]
            if len(row) <= 7:
                continue
            fio_raw = row[5]
            score_raw = row[7]
            fio = (fio_raw or '').strip().strip('"')
            score_val = (score_raw or '').strip().strip('"')
            if not fio:
                continue
            try:
                num = float(score_val.replace(',', '.'))
                is_five = (int(round(num)) == 5)
            except Exception:
                is_five = (score_val == '5')

            if is_five:
                score_counts[fio] = score_counts.get(fio, 0) + 1

        # Resolve to operators
        operator_scores = {}
        unmatched = []
        total_found = sum(score_counts.values())
        for fio, cnt in score_counts.items():
            user = db.find_operator_by_name_fuzzy(fio)
            if user and user[0]:
                op_id = int(user[0])
                operator_scores[op_id] = operator_scores.get(op_id, 0) + int(cnt)
            else:
                unmatched.append({"fio": fio, "count": int(cnt)})

        # Replace day's baiga scores
        db.replace_baiga_scores_for_day(day, operator_scores)

        return jsonify({"status": "success", "totalFound": total_found, "matched": len(operator_scores), "unmatched": unmatched}), 200
    except Exception as e:
        logging.exception("Error in /api/baiga/upload_day_csv: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/baiga/day_scores', methods=['GET'])
@require_api_key
def api_baiga_day_scores():
    try:
        day = request.args.get('day')
        if not day:
            return jsonify({"error": "Missing 'day' parameter"}), 400
        rows = db.get_baiga_scores_for_day(day)
        return jsonify({"status": "success", "scores": rows}), 200
    except Exception as e:
        logging.exception("Error in /api/baiga/day_scores: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route('/api/baiga/period_scores', methods=['GET'])
@require_api_key
def api_baiga_period_scores():
    try:
        # expects start and end in YYYY-MM-DD
        start = request.args.get('start')
        end = request.args.get('end')
        if not start or not end:
            return jsonify({"error": "Missing 'start' and 'end' parameters (YYYY-MM-DD)"}), 400

        # validate dates loosely
        try:
            from datetime import datetime as _dt
            _dt.strptime(start, '%Y-%m-%d')
            _dt.strptime(end, '%Y-%m-%d')
        except Exception:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

        data = db.get_baiga_scores_for_range(start, end)
        # Transform into array of days preserving order from start to end
        from datetime import datetime as _dt, timedelta as _td
        s = _dt.strptime(start, '%Y-%m-%d').date()
        e = _dt.strptime(end, '%Y-%m-%d').date()
        days = []
        cur = s
        while cur <= e:
            ds = cur.isoformat()
            days.append({ 'date': ds, 'scores': data.get(ds, []) })
            cur = cur + _td(days=1)

        return jsonify({"status": "success", "days": days}), 200
    except Exception as e:
        logging.exception("Error in /api/baiga/period_scores: %s", e)
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/users', methods=['GET'])
@require_api_key
def get_admin_users():
    try:
        requester_id = int(request.headers.get('X-User-Id'))
        with db._get_cursor() as cursor:
            cursor.execute("""
                    SELECT u.id, u.name, d.name as direction, s.name as supervisor_name, u.direction_id, u.supervisor_id, u.role, u.status, u.rate, u.hire_date, u.gender, u.birth_date
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
                        "rate": float(row[8]),
                        "hire_date": row[9].strftime('%d-%m-%Y') if row[9] else None,
                        "gender": row[10],
                        "birth_date": row[11].strftime('%d-%m-%Y') if row[11] else None
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
        elif field == 'name':
            # Validate name and prevent duplicates
            if not value or not str(value).strip():
                return jsonify({"error": "Name cannot be empty"}), 400
            new_name = str(value).strip()
            # check duplicate by name (case-insensitive)
            existing = db.get_user_by_name(new_name)
            if existing and int(existing[0]) != user_id:
                return jsonify({"error": "Name already in use by another user"}), 400
            value = new_name
        elif field == 'status':
            if value not in ['working', 'fired', 'unpaid_leave', 'bs', 'sick_leave', 'annual_leave', 'dismissal']:
                return jsonify({"error": "Invalid status value"}), 400
        elif field == 'rate':
            try:
                value = float(value)
                if value not in [1.0, 0.75, 0.5]:
                    return jsonify({"error": "Invalid rate value"}), 400
            except ValueError:
                return jsonify({"error": "Invalid rate format"}), 400
        elif field in ['hire_date', 'birth_date']:
            if value:
                try:
                    datetime.strptime(value, '%Y-%m-%d')
                except ValueError:
                    return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400
            else:
                value = None  # Allow clearing the date
        elif field == 'gender':
            if value in [None, '']:
                value = None
            elif value not in ['male', 'female']:
                return jsonify({"error": "Invalid gender value"}), 400
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

@app.route('/api/sv/preview_calls_table', methods=['POST'])
@require_api_key
def preview_calls_table():
    try:
        # Проверяем наличие файла
        if 'file' not in request.files:
            return jsonify({"error": "Файл не был загружен"}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Файл не выбран"}), 400

        if not file.filename.lower().endswith(('.csv', '.xls', '.xlsx')):
            return jsonify({"error": "Неверный формат файла. Допустимы .csv, .xls, .xlsx"}), 400

        # Получаем user_id из заголовка
        user_id_header = request.headers.get('X-User-Id')
        if not user_id_header or not user_id_header.isdigit():
            return jsonify({"error": "Некорректный X-User-Id"}), 400
        user_id = int(user_id_header)

        user = db.get_user(id=user_id)
        if not user or user[3] != 'sv':  # user[3] == role
            return jsonify({"error": "Unauthorized: только супервайзеры могут загружать таблицы"}), 403

        # Только парсим файл, не сохраняем
        sheet_name, operators, error = db.parse_calls_file(file)
        if error:
            return jsonify({"error": error}), 400

        return jsonify({
            "status": "success",
            "sheet_name": sheet_name,
            "operators": operators
        }), 200

    except Exception as e:
        logging.error(f"Ошибка при предпросмотре таблицы звонков: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/sv/update_norm_hours', methods=['POST'])
@require_api_key
def update_norm_hours():
    try:
        data = request.get_json()
        operator_id = data.get("operator_id")
        month = data.get("month")
        norm_hours = data.get("norm_hours")

        if not operator_id or not month:
            return jsonify({"error": "operator_id and month required"}), 400

        with db._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO work_hours (operator_id, month, norm_hours)
                VALUES (%s, %s, %s)
                ON CONFLICT (operator_id, month)
                DO UPDATE SET norm_hours = EXCLUDED.norm_hours
            """, (operator_id, month, norm_hours))

        return jsonify({"status": "success"}), 200
    except Exception as e:
        logging.exception("update_norm_hours error")
        return jsonify({"error": str(e)}), 500

@app.route('/api/sv/daily_hours', methods=['GET'])
@require_api_key
def sv_daily_hours():
    """
    Возвращает daily_hours за месяц для всех операторов текущего супервайзера.
    Если запрос делает обычный оператор — возвращаем только его daily_hours.
    Параметры:
      - month (query param) — YYYY-MM (опционально, по умолчанию текущий месяц)
      - id (query param) — (только для admin) id нужного supervisor
    Заголовки:
      - X-User-Id (обязательно) — id супервайзера/оператора (проверяется роль)
    """
    try:
        # parse month
        month = request.args.get('month')
        if not month:
            month = datetime.now().strftime('%Y-%m')

        # requester id header
        requester_header = request.headers.get('X-User-Id')
        if not requester_header or not requester_header.isdigit():
            return jsonify({"error": "Invalid or missing X-User-Id header"}), 400
        requester_id = int(requester_header)

        # get requester from db
        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "Requester not found"}), 403

        # try to read role in a robust way (support tuple or dict)
        role = None
        if isinstance(requester, dict):
            role = requester.get('role')
        elif isinstance(requester, (list, tuple)) and len(requester) > 3:
            role = requester[3]
        else:
            # fallback: try attribute access
            role = getattr(requester, 'role', None)

        if not role:
            # если роль не определена — ошибка
            return jsonify({"error": "User role not determined"}), 500

        # -----------------------
        # Behavior for supervisors
        # -----------------------
        if role == 'sv':
            # Allow optional ?id=<supervisor_id> to view another supervisor's data
            # (frontend may pass `id` when a supervisor selects another supervisor).
            # If no id param provided, fall back to requester_id (own team).
            user_param = request.args.get('id')
            if user_param and str(user_param).isdigit():
                supervisor_id = int(user_param)
            else:
                supervisor_id = requester_id

            try:
                result = db.get_daily_hours_by_supervisor_month(supervisor_id, month)
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            # result expected to contain "operators" list
            return jsonify({
                "status": "success",
                "month": result.get("month", month),
                "days_in_month": result.get("days_in_month"),
                "operators": result.get("operators", [])
            }), 200

        # -----------------------
        # Behavior for operators
        # -----------------------
        elif role == 'operator':
            try:
                result = db.get_daily_hours_for_operator_month(requester_id, month)
            except Exception as e:
                logging.exception("Error fetching daily hours for operator")
                return jsonify({"error": "Failed to fetch daily hours"}), 500

            operator_obj = result.get("operator") if isinstance(result, dict) else None
            return jsonify({
                "status": "success",
                "month": result.get("month", month) if isinstance(result, dict) else month,
                "days_in_month": result.get("days_in_month") if isinstance(result, dict) else None,
                "operators": [operator_obj] if operator_obj else []
            }), 200

        # -----------------------
        # Behavior for admins
        # -----------------------
        elif role == 'admin':
            # admin must pass id (supervisor id) as query param "id"
            user_id = request.args.get('id')
            if not user_id or not str(user_id).isdigit():
                return jsonify({"error": "Missing or invalid 'id' parameter (supervisor id)"}), 400

            supervisor_id = int(user_id)
            try:
                result = db.get_daily_hours_by_supervisor_month(supervisor_id, month)
            except Exception as e:
                logging.exception("Error fetching daily hours for supervisor (admin request)")
                return jsonify({"error": str(e)}), 400

            # Normalize: expect result to contain "operators" list
            operators = result.get("operators") if isinstance(result, dict) else None
            if operators is None:
                # if older API returned single operator under "operator", handle it
                single = result.get("operator") if isinstance(result, dict) else None
                operators = [single] if single else []

            return jsonify({
                "status": "success",
                "month": result.get("month", month) if isinstance(result, dict) else month,
                "days_in_month": result.get("days_in_month"),
                "operators": operators
            }), 200

        else:
            return jsonify({"error": "Only supervisors, operators and admins can request this endpoint"}), 403

    except Exception as e:
        logging.exception("Error in sv_daily_hours")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/hours/upload_group_day', methods=['POST'])
@require_api_key
def upload_group_day():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Empty payload"}), 400

        # Парсим базовые поля
        date_str = data.get('date')  # ожидаем YYYY-MM-DD
        if not date_str:
            return jsonify({"error": "Field 'date' is required (YYYY-MM-DD)"}), 400

        try:
            # проверяем формат даты
            day_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

        default_month = date_str[:7]  # YYYY-MM

        sv_id_payload = data.get('sv_id')  # может быть None
        operators = data.get('operators')
        if not operators or not isinstance(operators, list):
            return jsonify({"error": "Field 'operators' must be a non-empty array"}), 400

        # Получаем id запроса (и проверяем пользователя)
        requester_header = request.headers.get('X-User-Id')
        if not requester_header or not requester_header.isdigit():
            return jsonify({"error": "Invalid or missing X-User-Id"}), 400
        requester_id = int(requester_header)
        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "Requester not found"}), 403

        requester_role = requester[3]  # согласно get_user селекту: u.role в позиции 3

        # Если запрос делает супервайзер — разрешаем только для его id (или если payload sv_id указан, он должен совпадать)
        if requester_role == 'sv':
            sv_id = requester_id
        else:
            # admin or others
            sv_id = int(sv_id_payload) if sv_id_payload else None

        processed = []
        skipped = []
        errors = []
        processed_operator_ids = set()

        for idx, row in enumerate(operators):
            try:
                # Normalize fields
                op_id = row.get('operator_id')
                name = (row.get('name') or '').strip()
                # numeric fields: ensure floats/ints
                try:
                    work_time = float(row.get('work_time') or 0.0)
                except Exception:
                    work_time = 0.0
                try:
                    break_time = float(row.get('break_time') or 0.0)
                except Exception:
                    break_time = 0.0
                try:
                    talk_time = float(row.get('talk_time') or 0.0)
                except Exception:
                    talk_time = 0.0
                try:
                    calls = int(row.get('calls') or 0)
                except Exception:
                    calls = 0
                try:
                    efficiency = float(row.get('efficiency') or 0.0)  # в часах
                except Exception:
                    efficiency = 0.0
                try:
                    fine_amount = float(row.get('fine_amount') or 0.0)
                except Exception:
                    fine_amount = 0.0
                
                fine_reason = (row.get('fine_reason') or None)
                # защита: принимаем только разрешённые причины (опционально)
                ALLOWED_FINE_REASONS = ['Корп такси', 'Опоздание', 'Прокси карта', 'Не выход', 'Другое']
                if fine_reason and fine_reason not in ALLOWED_FINE_REASONS:
                    # если невалидная причина — помечаем как "Другое" или сохраняем как есть, в примере — нормализуем в 'Другое'
                    fine_reason = 'Другое'

                fine_comment = (row.get('fine_comment') or None)
                # accept fines array (new format): list of {amount, reason, comment}
                fines_arr = row.get('fines') if isinstance(row.get('fines'), list) else None
                month = row.get('month') or default_month

                # resolve operator_id if not provided
                resolved_operator_id = None
                if op_id:
                    # verify operator exists
                    try:
                        op_id_int = int(op_id)
                        user_row = db.get_user(id=op_id_int)
                        if user_row and user_row[3] == 'operator':  # role == operator
                            # if requester is sv, ensure operator belongs to that sv
                            if requester_role == 'sv' and user_row[6] != requester_id:  # user_row[6] == supervisor_id in get_user select
                                skipped.append({"row": idx, "reason": "operator not under this supervisor", "name": name, "operator_id": op_id})
                                continue
                            resolved_operator_id = op_id_int
                        else:
                            skipped.append({"row": idx, "reason": "operator_id not found or not operator", "name": name, "operator_id": op_id})
                            continue
                    except Exception:
                        skipped.append({"row": idx, "reason": "invalid operator_id", "value": op_id})
                        continue
                else:
                    # try find by name under sv_id (if sv_id is known)
                    if sv_id:
                        with db._get_cursor() as cursor:
                            cursor.execute("""
                                SELECT id FROM users
                                WHERE name = %s AND role = 'operator' AND supervisor_id = %s
                                LIMIT 1
                            """, (name, sv_id))
                            res = cursor.fetchone()
                            if res:
                                resolved_operator_id = res[0]
                    # if still not found, try global lookup (admins may allow)
                    if not resolved_operator_id:
                        with db._get_cursor() as cursor:
                            cursor.execute("""
                                SELECT id FROM users
                                WHERE name = %s AND role = 'operator'
                                LIMIT 1
                            """, (name,))
                            res2 = cursor.fetchone()
                            if res2:
                                # if requester is sv, ensure operator belongs to them
                                if requester_role == 'sv':
                                    # check supervisor
                                    with db._get_cursor() as c2:
                                        c2.execute("SELECT supervisor_id FROM users WHERE id = %s", (res2[0],))
                                        sp = c2.fetchone()
                                        if sp and sp[0] == requester_id:
                                            resolved_operator_id = res2[0]
                                        else:
                                            skipped.append({"row": idx, "reason": "operator found but not under this supervisor", "name": name})
                                            continue
                                else:
                                    resolved_operator_id = res2[0]

                if not resolved_operator_id:
                    skipped.append({"row": idx, "reason": "operator not found", "name": name})
                    continue

                # Insert/update daily_hours
                try:
                    logging.info(
                        "Fine data: amount=%s, reason=%s, comment=%s",
                        fine_amount, fine_reason, fine_comment
                    )
                    # use the Database helper (assumes method exists)
                    db.insert_or_update_daily_hours(resolved_operator_id, date_str,
                                                    work_time=work_time,
                                                    break_time=break_time,
                                                    talk_time=talk_time,
                                                    calls=calls,
                                                    efficiency=efficiency,
                                                    fine_amount=fine_amount,
                                                    fine_reason=fine_reason,
                                                    fine_comment=fine_comment,
                                                    fines=fines_arr)
                    processed.append({"row": idx, "operator_id": resolved_operator_id, "name": name})
                    processed_operator_ids.add(resolved_operator_id)
                except Exception as e:
                    errors.append({"row": idx, "operator_id": resolved_operator_id, "error": str(e)})
                    continue

            except Exception as e:
                errors.append({"row": idx, "error": str(e)})
                continue

        # После всех вставок — агрегируем месяц для каждого обработанного оператора
        aggregations = {}
        for opid in processed_operator_ids:
            try:
                agg = db.aggregate_month_from_daily(opid, default_month)
                aggregations[opid] = agg
            except Exception as e:
                aggregations[opid] = {"error": str(e)}

        return jsonify({
            "status": "success",
            "date": date_str,
            "month": default_month,
            "processed_count": len(processed),
            "processed": processed,
            "skipped_count": len(skipped),
            "skipped": skipped,
            "errors": errors,
            "aggregations": aggregations
        }), 200

    except Exception as e:
        logging.exception("Error in upload_group_day")
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
                supervisor_id=user_id,
                hours_table_url=table_url
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
        supervisors = db.get_supervisors()
        logging.info(f"Fetched {len(supervisors)} supervisors")
        sv_data = [{"id": sv[0], "name": sv[1], "table": sv[2],"role": sv[3], "hire_date": sv[4].strftime('%d-%m-%Y') if sv[4] else None, "status": sv[5]} for sv in supervisors]
        return jsonify({"status": "success", "sv_list": sv_data})
    except Exception as e:
        logging.error(f"Error fetching SV list: {e}", exc_info=True)
        return jsonify({"error": f"Failed to fetch supervisors: {str(e)}"}), 500

@app.route('/api/call_evaluations', methods=['GET'])
@require_api_key
def get_call_evaluations():
    try:
        operator_id = request.args.get('operator_id')
        month = request.args.get('month')
        if not operator_id:
            return jsonify({"error": "Missing operator_id parameter"}), 400
        if not month:
            month = None
        operator_id = int(operator_id)
        requester_id = getattr(g, 'user_id', None)
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "Requester not found"}), 404

        if not _authorize_operator_scope(requester, requester_id, operator_id):
            return jsonify({"error": "Unauthorized to view this operator evaluations"}), 403

        evaluations = db.get_call_evaluations(operator_id, month=month)
        session_id = _current_session_id_from_access_token()
        role = requester[3]
        if role == 'operator':
            reveal_sensitive = bool(_is_sensitive_access_unlocked(requester_id, session_id))
        else:
            reveal_sensitive = True
        evaluations = _sanitize_evaluations_for_access(evaluations, reveal_sensitive)

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
            },
            "sensitive_access": {
                "required": role == 'operator',
                "granted": reveal_sensitive
            }
        })
    except Exception as e:
        logging.error(f"Error fetching evaluations: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
    
@app.route('/api/ai/monthly_feedback', methods=['POST'])
@require_api_key
def ai_monthly_feedback():
    try:
        # Проверка прав - admin, sv и operator (operator только для чтения из кэша)
        requester_id = request.headers.get('X-User-Id')
        if not requester_id:
            return jsonify({"status": "error", "error": "Missing X-User-Id header"}), 400
        
        try:
            requester_id = int(requester_id)
        except (ValueError, TypeError):
            return jsonify({"status": "error", "error": "Invalid X-User-Id format"}), 400
        
        requester = db.get_user(id=requester_id)
        if not requester or requester[3] not in ['admin', 'sv', 'operator']:
            return jsonify({"status": "error", "error": "Access denied"}), 403
        
        is_operator = (requester[3] == 'operator')

        data = request.get_json() or {}
        operator_id = data.get('operator_id')
        month = data.get('month')

        if operator_id is None or month is None:
            return jsonify({"status": "error", "error": "Missing operator_id or month"}), 400

        try:
            operator_id = int(operator_id)
        except Exception:
            return jsonify({"status": "error", "error": "Invalid operator_id"}), 400

        try:
            datetime.strptime(str(month), "%Y-%m")
        except Exception:
            return jsonify({"status": "error", "error": "Invalid month format. Use YYYY-MM"}), 400

        # Для операторов проверяем только кэш
        if is_operator:
            cached_feedback = db.get_ai_feedback_cache(requester_id, str(month))
            if cached_feedback:
                return jsonify({"status": "success", "result": cached_feedback['feedback_data']}), 200
            else:
                return jsonify({"status": "error", "error": "No cached feedback available"}), 404

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(
                generate_monthly_feedback_with_ai(operator_id=operator_id, month=str(month)),
                loop,
            )
            result = fut.result(timeout=180)
        else:
            result = asyncio.run(generate_monthly_feedback_with_ai(operator_id=operator_id, month=str(month)))

        if result is None:
            return jsonify({"status": "error", "error": "ai_failed"}), 500

        if isinstance(result, dict) and result.get('error'):
            return jsonify({"status": "error", "error": result}), 200

        return jsonify({"status": "success", "result": result}), 200

    except Exception as e:
        logging.exception("Error in /api/ai/monthly_feedback")
        return jsonify({"status": "error", "error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/operators_summary', methods=['GET'])
@require_api_key
def operators_summary():
    try:
        # month required (YYYY-MM)
        month = request.args.get('month')
        if not month:
            return jsonify({"error": "Missing month parameter (YYYY-MM)"}), 400
        from datetime import datetime
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400

        # requester identity + role check
        user_id_header = request.headers.get('X-User-Id') or request.args.get('requester_id')
        if not user_id_header:
            return jsonify({"error": "Missing X-User-Id header"}), 400
        try:
            requester_id = int(user_id_header)
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid X-User-Id format"}), 400

        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "Requester not found"}), 404

        # requester role: support tuple/dict user shapes (following your existing pattern)
        role = None
        if isinstance(requester, dict):
            role = requester.get('role')
        else:
            # assume role in pos 3 like in other parts of code
            role = requester[3] if len(requester) > 3 else None

        if role not in ('admin', 'sv'):
            return jsonify({"error": "Forbidden: only admins or supervisors can access"}), 403

        # optional supervisor_id param: admin can request any sv; sv will be forced to their id
        sv_param = request.args.get('supervisor_id')
        supervisor_id = None
        if role == 'sv':
            # if requester is SV — restrict to their operators only
            supervisor_id = requester_id
        else:
            # admin: allow optional supervisor filter
            if sv_param:
                try:
                    supervisor_id = int(sv_param)
                except ValueError:
                    return jsonify({"error": "Invalid supervisor_id"}), 400

        operators = db.get_operators_summary_for_month(month=month, supervisor_id=supervisor_id)

        return jsonify({"status": "success", "month": month, "operators": operators}), 200

    except Exception as e:
        logging.exception("Error in /api/admin/operators_summary")
        return jsonify({"error": "Internal server error"}), 500

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
            f"📅 Дата обращения: {' '.join(call['appeal_date'].split('T'))}\n"
            f"📅 Дата оценки: {call['created_at']}\n"
            f"💯 Оценка: {call['score']}\n"
            f"📅 За месяц: {call['month']}\n\n"
            f"📝 <b>Сообщение от оператора:</b>\n"
            f"{data['dispute_text']}"
        )

        # Сообщение для админа
        admin_message = (
            f"⚠️ <b>Запрос на пересмотр оценки</b>\n\n"
            f"💬 Супервайзер: <b>{supervisor[2]}</b>\n"
            f"👤 Оператор: <b>{operator[2]}</b>\n"
            f"📞 Звонок ID: {call['id']}\n"
            f"📅 Дата обращения: {' '.join(call['appeal_date'].split('T'))}\n"
            f"📅 Дата оценки: {call['created_at']}\n"
            f"📱 Номер: {call['phone_number']}\n"
            f"💯 Оценка: {call['score']}\n"
            f"📅 За месяц: {call['month']}\n\n"
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
        admin_response = send_telegram_message(os.getenv("ADMIN_ID"), admin_message, audio_url)
        if admin_response.status_code != 200:
            error_detail = admin_response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error (admin): {error_detail}")
            return jsonify({"error": f"Failed to send dispute message to admin: {error_detail}"}), 500
        # --- END NEW ---

        return jsonify({"status": "success", "message": "Dispute sent to supervisor and admin"})
    except Exception as e:
        logging.error(f"Error processing dispute: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/hours/send_request', methods=['POST'])
@require_api_key
def send_request():
    try:
        data = request.get_json()
        required_fields = ['date', 'message']
        if not data or not all(field in data for field in required_fields):
            return jsonify({"error": "Missing required fields: date, message"}), 400

        operator_id = int(request.headers.get('X-User-Id'))
        hours = data.get('hours', 0)
        date = data['date']
        message = data['message']

        operator = db.get_user(id=operator_id)
        if not operator:
            return jsonify({"error": "Operator not found"}), 404

        supervisor = db.get_user(id=operator[6]) if operator[6] else None
        if not supervisor:
            return jsonify({"error": "Supervisor not found for this operator"}), 404

        telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
        payload = {
            "chat_id": supervisor[1],
            "text": (
                f"⚠️ <b>Запрос по рабочим часам</b>\n\n"
                f"👤 Оператор: <b>{operator[2]}</b>\n"
                f"⏰ Часы: <b>{hours} ч</b>\n"
                f"📅 Дата: <b>{date}</b>\n"
                f"📝 <b>Сообщение:</b>\n{message}"
            ),
            "parse_mode": "HTML"
        }
        response = requests.post(telegram_url, json=payload, timeout=10)
        if response.status_code != 200:
            error_detail = response.json().get('description', 'Unknown error')
            logging.error(f"Telegram API error: {error_detail}")
            return jsonify({"error": f"Failed to send request: {error_detail}"}), 500

        return jsonify({"status": "success", "message": "Request sent to supervisor"}), 200
    except Exception as e:
        logging.error(f"Error sending request: {e}")
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
            password=password,
            rate=1.0  # Default rate for SV
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

@app.route('/api/admin/add_user', methods=['POST'])
@require_api_key
def add_user():
    try:
        data = request.get_json()
        # теперь обязательные только эти поля
        required_fields = ['name', 'rate', 'direction_id', 'hire_date']
        if not data or not all(field in data and data[field] for field in required_fields):
            return jsonify({"error": "Missing required field"}), 400

        name = data['name'].strip()
        if not name:
            return jsonify({"error": "Name cannot be empty"}), 400

        # role всегда оператор
        role = 'operator'

        # supervisor_id опционален
        supervisor_id = int(data['supervisor_id']) if data.get('supervisor_id') else None

        hire_date = data['hire_date']
        try:
            datetime.strptime(hire_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({"error": "Invalid hire_date format. Use YYYY-MM-DD"}), 400

        rate = float(data['rate']) if data.get('rate') else 1.0
        direction_id = int(data['direction_id']) if data.get('direction_id') else None
        gender = data.get('gender')
        if gender in [None, '']:
            gender = None
        elif gender not in ['male', 'female']:
            return jsonify({"error": "Invalid gender value"}), 400

        birth_date = data.get('birth_date')
        if birth_date:
            try:
                datetime.strptime(birth_date, '%Y-%m-%d')
            except ValueError:
                return jsonify({"error": "Invalid birth_date format. Use YYYY-MM-DD"}), 400
        else:
            birth_date = None

        login = f"user_{str(uuid.uuid4())[:8]}"
        password = str(uuid.uuid4())[:8]

        # Создаём оператора
        user_id = db.create_user(
            telegram_id=None,
            name=name,
            role=role,
            hire_date=hire_date,
            supervisor_id=supervisor_id,
            rate=rate,
            direction_id=direction_id,
            login=login,
            password=password,
            gender=gender,
            birth_date=birth_date
        )

        return jsonify({
            "status": "success",
            "message": f"Оператор {name} добавлен",
            "id": user_id,
            "login": login,
            "password": password
        })
    except Exception as e:
        logging.error(f"Error adding user: {e}")
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
        db.save_directions(directions)

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


@app.route('/api/operator/activity', methods=['GET'])
@require_api_key
def get_operator_activity():
    try:
        operator_id = request.args.get('operator_id')
        date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
        if not operator_id:
            return jsonify({"error": "Missing operator_id"}), 400
        
        operator_id = int(operator_id)
        supervisor_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=supervisor_id)
        
        # Проверка, что оператор принадлежит супервайзеру
        operator = db.get_user(id=operator_id)
        if requester[3]=="sv" and (not operator or operator[6] != supervisor_id):  # operator[6] - supervisor_id
            return jsonify({"error": "Unauthorized: This operator does not belong to you"}), 403
        
        logs = db.get_activity_logs(operator_id, date_str)
        return jsonify({"status": "success", "logs": logs}), 200
    except Exception as e:
        logging.error(f"Error fetching operator activity: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/sv/operators', methods=['GET'])
@require_api_key
def get_sv_operators_moderka():
    try:
        supervisor_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=supervisor_id)
        if not requester or requester[3] != 'sv':
            return jsonify({"error": "Unauthorized: Only supervisors can access this"}), 403
        
        operators = db.get_operators_by_supervisor(supervisor_id)
        return jsonify({"status": "success", "operators": operators}), 200
    except Exception as e:
        logging.error(f"Error fetching operators: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/sv/data', methods=['GET'])
@require_api_key
def get_sv_data():
    try:
        # id
        user_id_raw = request.args.get('id')
        if not user_id_raw:
            return jsonify({"error": "Missing ID parameter"}), 400
        try:
            user_id = int(user_id_raw)
        except ValueError:
            return jsonify({"error": "Invalid ID parameter"}), 400

        # optional month YYYY-MM
        month = request.args.get('month')
        if month:
            try:
                datetime.strptime(month, "%Y-%m")
            except ValueError:
                return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400
        else:
            month = datetime.now().strftime("%Y-%m")

        # fetch user (accept any caller role; admin can call this endpoint)
        user = db.get_user(id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        # normalize user name (support tuple/list or dict)
        if isinstance(user, dict):
            user_name = user.get("name") or user.get("full_name") or ""
        else:
            user_name = user[2] if len(user) > 2 else ""

        response_data = {
            "status": "success",
            "name": user_name,
            "table": user[9] if (not isinstance(user, dict) and len(user) > 9) else (user.get("scores_table_url") if isinstance(user, dict) else None),
            "requested_month": month,
            "operators": []
        }

        # try to get operators for this supervisor id (works when admin requests data for some sv)
        try:
            operators = db.get_operators_by_supervisor(user_id) or []
        except Exception:
            operators = []

        def _extract_score_and_imported(ev):
            """
            Возвращает (score_or_None, is_imported_bool).
            Поддерживает dict, sequence (tuple/list) и objects with attributes.
            For tuple/list we assume score index 4, is_imported last index (if present).
            """
            # dict-like
            try:
                if isinstance(ev, dict):
                    score = ev.get("score")
                    is_imported = bool(ev.get("is_imported")) if "is_imported" in ev else False
                    return (None if score is None else float(score), is_imported)
                # object with attributes
                if hasattr(ev, "__dict__") and not isinstance(ev, (list, tuple)):
                    score = getattr(ev, "score", None)
                    is_imported = bool(getattr(ev, "is_imported", False))
                    return (None if score is None else float(score), is_imported)
                # sequence-like
                if isinstance(ev, (list, tuple)):
                    # common positions from get_call_evaluations: score at index 4; is_imported at index 27 (if exists)
                    score = None
                    is_imported = False
                    if len(ev) > 4:
                        score = ev[4]
                    # try last position for is_imported
                    if len(ev) > 27:
                        is_imported = bool(ev[27])
                    else:
                        # fallback: if last element is bool, consider it
                        last = ev[-1]
                        if isinstance(last, bool):
                            is_imported = bool(last)
                    return (None if score is None else float(score), is_imported)
            except Exception:
                pass
            # cannot parse
            return (None, False)

        for op in operators:
            # flexible unpacking: support dict or sequence rows
            if isinstance(op, dict):
                operator_id = op.get("id") or op.get("operator_id") or op.get("user_id")
                operator_name = op.get("name") or op.get("operator_name")
                direction_id = op.get("direction_id")
                hire_date = op.get("hire_date")
                birth_date = op.get("birth_date")
                hours_table_url = op.get("hours_table_url")
                scores_table_url = op.get("scores_table_url")
                status = op.get("status")
                rate = op.get("rate")
                gender = op.get("gender")
            else:
                operator_id = op[0] if len(op) > 0 else None
                operator_name = op[1] if len(op) > 1 else None
                direction_id = op[2] if len(op) > 2 else None
                hire_date = op[3] if len(op) > 3 else None
                birth_date = op[10] if len(op) > 10 else None
                hours_table_url = op[4] if len(op) > 4 else None
                scores_table_url = op[5] if len(op) > 5 else None
                status = op[7] if len(op) > 7 else None
                rate = op[8] if len(op) > 8 else None
                gender = op[9] if len(op) > 9 else None

            # skip invalid rows
            if not operator_id:
                continue

            # get evaluations for requested month (be tolerant to db method return shape)
            try:
                evaluations = db.get_call_evaluations(operator_id, month=month) or []
            except Exception:
                evaluations = []

            # compute count and average only for реально оценённых записей
            scores = []
            eval_count = 0
            for ev in evaluations:
                score_val, is_imported = _extract_score_and_imported(ev)
                # count only non-imported and with a numeric score
                if not is_imported and score_val is not None:
                    scores.append(score_val)
                    eval_count += 1

            avg_score = round(sum(scores) / len(scores), 2) if len(scores) > 0 else None

            # ensure rate is numeric and reasonable
            try:
                rate_val = float(rate) if rate is not None else 1.0
            except Exception:
                rate_val = 1.0

            response_data["operators"].append({
                "id": operator_id,
                "name": operator_name,
                "hire_date": hire_date,
                "birth_date": (birth_date.strftime('%d-%m-%Y') if hasattr(birth_date, 'strftime') else birth_date),
                "direction_id": direction_id,
                # number of actual evaluated calls (with scores)
                "call_count": eval_count,
                "avg_score": avg_score,
                "scores_table_url": scores_table_url,
                "status": status,
                "rate": rate_val,
                "gender": gender
            })

        return jsonify(response_data), 200

    except Exception:
        logging.exception("Error fetching SV data")
        return jsonify({"error": "Internal server error"}), 500

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

@app.route('/api/tasks/recipients', methods=['GET', 'OPTIONS'])
@require_api_key
def get_task_recipients():
    try:
        requester_id, requester, guard_response, guard_status = _task_route_guard()
        if guard_response is not None:
            return guard_response, guard_status

        recipients = db.get_task_recipients(requester_id, requester[3])
        return jsonify({
            "status": "success",
            "recipients": recipients
        }), 200
    except Exception as e:
        logging.error(f"Error in get_task_recipients: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/tasks', methods=['GET', 'POST', 'OPTIONS'])
@require_api_key
def handle_tasks():
    try:
        requester_id, requester, guard_response, guard_status = _task_route_guard()
        if guard_response is not None:
            return guard_response, guard_status

        requester_role = requester[3]

        if request.method == 'GET':
            tasks = db.get_tasks_for_requester(requester_id, requester_role)
            return jsonify({
                "status": "success",
                "tasks": tasks
            }), 200

        subject = (request.form.get('subject') or '').strip()
        description = (request.form.get('description') or '').strip()
        tag = (request.form.get('tag') or 'task').strip().lower() or 'task'
        assigned_to_raw = request.form.get('assigned_to')

        if not subject:
            return jsonify({"error": "subject is required"}), 400
        if tag not in TASK_ALLOWED_TAGS:
            return jsonify({"error": "Invalid tag"}), 400
        if not assigned_to_raw:
            return jsonify({"error": "assigned_to is required"}), 400

        try:
            assigned_to = int(assigned_to_raw)
        except Exception:
            return jsonify({"error": "assigned_to must be an integer"}), 400

        allowed_recipients = db.get_task_recipients(requester_id, requester_role)
        allowed_ids = {int(item['id']) for item in allowed_recipients if item.get('id') is not None}
        if assigned_to not in allowed_ids:
            return jsonify({"error": "You cannot assign a task to this user"}), 403

        files = request.files.getlist('files')
        try:
            attachments, uploaded_blob_paths, gcs_bucket = _upload_task_attachments_to_gcs(files, stage='initial')
        except ValueError as upload_error:
            return jsonify({"error": str(upload_error)}), 400
        except RuntimeError as upload_error:
            return jsonify({"error": str(upload_error)}), 500
        except Exception as upload_error:
            logging.error(f"Task attachment upload failed: {upload_error}")
            return jsonify({"error": "Failed to upload task attachments"}), 500

        try:
            created = db.create_task(
                subject=subject,
                description=description,
                tag=tag,
                assigned_to=assigned_to,
                created_by=requester_id,
                attachments=attachments
            )
        except Exception:
            # DB write failed after upload -> cleanup uploaded blobs
            _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
            raise

        telegram_warning = None
        try:
            assignee = db.get_user(id=assigned_to)
            assignee_chat_id = assignee[1] if assignee else None
            if assignee_chat_id:
                tag_label = TASK_TAG_LABELS.get(tag, 'Задача')
                requester_name = requester[2] if requester else 'Система'
                message = (
                    f"📌 <b>Новая задача</b>\n\n"
                    f"Тема: <b>{html.escape(subject)}</b>\n"
                    f"Тег: <b>{html.escape(tag_label)}</b>\n"
                    f"Поставил(а): <b>{html.escape(str(requester_name))}</b>"
                )
                telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
                tg_response = requests.post(
                    telegram_url,
                    json={
                        "chat_id": assignee_chat_id,
                        "text": message,
                        "parse_mode": "HTML"
                    },
                    timeout=10
                )
                if tg_response.status_code != 200:
                    error_detail = tg_response.json().get('description', 'Unknown error')
                    telegram_warning = f"Task created, but Telegram notification failed: {error_detail}"
                    logging.error(f"Task Telegram API error: {error_detail}")
        except Exception as notify_error:
            telegram_warning = f"Task created, but Telegram notification failed: {str(notify_error)}"
            logging.error(f"Task Telegram notification error: {notify_error}")

        response_payload = {
            "status": "success",
            "message": "Task created successfully",
            "task_id": created.get("id")
        }
        if telegram_warning:
            response_payload["warning"] = telegram_warning

        return jsonify(response_payload), 201

    except Exception as e:
        logging.error(f"Error in handle_tasks: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/tasks/<int:task_id>/status', methods=['POST', 'OPTIONS'])
@require_api_key
def update_task_status(task_id):
    try:
        requester_id, requester, guard_response, guard_status = _task_route_guard()
        if guard_response is not None:
            return guard_response, guard_status

        content_type = (request.content_type or '').lower()
        is_multipart = 'multipart/form-data' in content_type

        if is_multipart:
            action = (request.form.get('action') or '').strip().lower()
            comment = (request.form.get('comment') or '').strip()
            completion_summary = (request.form.get('completion_summary') or '').strip()
            completion_files = request.files.getlist('files')
        else:
            data = request.get_json() or {}
            action = (data.get('action') or '').strip().lower()
            comment = (data.get('comment') or '').strip()
            completion_summary = (data.get('completion_summary') or '').strip()
            completion_files = []

        if action not in TASK_ALLOWED_ACTIONS:
            return jsonify({"error": "Invalid action"}), 400

        completion_attachments = []
        uploaded_blob_paths = []
        gcs_bucket = None

        if action == 'completed':
            try:
                completion_attachments, uploaded_blob_paths, gcs_bucket = _upload_task_attachments_to_gcs(
                    completion_files,
                    stage='result'
                )
            except ValueError as upload_error:
                return jsonify({"error": str(upload_error)}), 400
            except RuntimeError as upload_error:
                return jsonify({"error": str(upload_error)}), 500
            except Exception as upload_error:
                logging.error(f"Task completion attachment upload failed: {upload_error}")
                return jsonify({"error": "Failed to upload completion attachments"}), 500
        elif completion_files:
            return jsonify({"error": "Files can be attached only when completing a task"}), 400

        try:
            result = db.update_task_status(
                task_id=task_id,
                requester_id=requester_id,
                requester_role=requester[3],
                action=action,
                comment=comment,
                completion_summary=completion_summary if action == 'completed' else None,
                completion_attachments=completion_attachments
            )
        except ValueError as value_error:
            _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
            code = str(value_error)
            if code == 'TASK_NOT_FOUND':
                return jsonify({"error": "Task not found"}), 404
            if code in ('INVALID_ACTION', 'INVALID_TRANSITION'):
                return jsonify({"error": "Invalid task status transition"}), 400
            return jsonify({"error": code}), 400
        except PermissionError as permission_error:
            _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
            code = str(permission_error)
            if code in ('TASK_FORBIDDEN', 'ONLY_ASSIGNEE', 'ONLY_REVIEWER'):
                return jsonify({"error": "You do not have permission for this action"}), 403
            return jsonify({"error": code}), 403
        except Exception:
            _cleanup_task_uploaded_blobs(gcs_bucket, uploaded_blob_paths)
            raise

        action_messages = {
            'in_progress': 'Task moved to in progress',
            'completed': 'Task marked as completed',
            'accepted': 'Task accepted',
            'returned': 'Task returned for rework',
            'reopened': 'Task reopened'
        }
        return jsonify({
            "status": "success",
            "message": action_messages.get(action, 'Task status updated'),
            "task": result
        }), 200

    except Exception as e:
        logging.error(f"Error in update_task_status: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/tasks/attachments/<int:attachment_id>/download', methods=['GET', 'OPTIONS'])
@require_api_key
def download_task_attachment(attachment_id):
    try:
        requester_id, requester, guard_response, guard_status = _task_route_guard()
        if guard_response is not None:
            return guard_response, guard_status

        try:
            attachment = db.get_task_attachment_for_requester(
                attachment_id=attachment_id,
                requester_id=requester_id,
                requester_role=requester[3]
            )
        except PermissionError:
            return jsonify({"error": "You do not have access to this attachment"}), 403

        if not attachment:
            return jsonify({"error": "Attachment not found"}), 404

        if attachment.get('storage_type') == 'gcs':
            bucket_name = (attachment.get('gcs_bucket') or '').strip()
            blob_path = (attachment.get('gcs_blob_path') or '').strip()
            if not bucket_name or not blob_path:
                return jsonify({"error": "Attachment storage metadata is invalid"}), 500
            try:
                gcs_client = get_gcs_client()
                bucket = gcs_client.bucket(bucket_name)
                blob = bucket.blob(blob_path)
                if not blob.exists():
                    return jsonify({"error": "Attachment object not found in storage"}), 404
                file_bytes = blob.download_as_bytes()
            except Exception as storage_error:
                logging.error(f"Error downloading GCS task attachment: {storage_error}")
                return jsonify({"error": "Failed to download attachment from storage"}), 500

            return send_file(
                BytesIO(file_bytes),
                as_attachment=True,
                download_name=attachment['file_name'],
                mimetype=attachment.get('content_type') or 'application/octet-stream'
            )

        return send_file(
            BytesIO(attachment['file_data']),
            as_attachment=True,
            download_name=attachment['file_name'],
            mimetype=attachment.get('content_type') or 'application/octet-stream'
        )
    except Exception as e:
        logging.error(f"Error in download_task_attachment: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/admin/monthly_report', methods=['GET'])
@require_api_key
def handle_monthly_report():
    try:
        month = request.args.get('month')
        user_id = request.args.get('')
        if not month:
            month = datetime.now().strftime('%Y-%m')
        
        # Validate month format
        try:
            month_start = datetime.strptime(month + '-01', '%Y-%m-%d')
        except ValueError:
            return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400
        
        logging.info(f"Generating monthly report for {month}")
        
        user_id = int(request.headers.get('X-User-Id'))
        user = db.get_user(id=user_id)
        if user[3]=="sv":
            # get_user returns tuple: (id, telegram_id, name, role, ... , status)
            if user[5] == "fired":
                return jsonify({"error": "Your status is fired. No report available."}), 403
            svs=[(user[0], user[2], "", "", "", user[5])]
        elif user[3]=="admin":
            svs = [sv for sv in db.get_supervisors() if len(sv) > 5 and sv[5] != "fired"]
        else:
            return jsonify({"error": "Only admin or SV can access to this report"}), 404

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

        for sv in svs:
            sv_id, sv_name = sv[0], sv[1]
            # Exclude supervisors with status 'fired' (should already be filtered above, but double check)
            if len(sv) > 5 and sv[5] == "fired":
                continue
            operators = db.get_operators_by_supervisor(sv_id)
            # Exclude operators with status 'fired'
            operators = [op for op in operators if op.get('status', '').lower() != 'fired']

            safe_sheet_name = sv_name[:31].replace('/', '_').replace('\\', '_').replace('?', '_').replace('*', '_').replace('[', '_').replace(']', '_')
            worksheet = workbook.add_worksheet(safe_sheet_name)

            special_evaluator_id = 169
            special_table_header_row = len(operators) + 2

            # Headers
            headers = ['ФИО']
            for i in range(1, 21):
                headers.append(f'{i}')
            headers.append('Средний балл')
            headers.append('Кол-во оцененных звонков')

            for col, header in enumerate(headers):
                worksheet.write(0, col, header, header_format)

            for col, header in enumerate(headers):
                worksheet.write(special_table_header_row, col, header, header_format)

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

                with db._get_cursor() as cursor:
                    cursor.execute("""
                        SELECT c.score
                        FROM calls c
                        JOIN (
                            SELECT phone_number, MAX(created_at) as max_date
                            FROM calls
                            WHERE operator_id = %s AND month = %s AND is_draft = FALSE AND evaluator_id = %s
                            GROUP BY phone_number
                        ) lv ON c.phone_number = lv.phone_number AND c.created_at = lv.max_date
                        WHERE c.is_draft = FALSE AND c.operator_id = %s AND c.month = %s AND c.evaluator_id = %s
                        ORDER BY c.created_at ASC
                    """, (op_id, month, special_evaluator_id, op_id, month, special_evaluator_id))
                    special_scores = [row[0] for row in cursor.fetchall()]

                count = len(scores)
                avg_score = sum(scores) / count if count > 0 else 0.0

                special_count = len(special_scores)
                special_avg_score = sum(special_scores) / special_count if special_count > 0 else 0.0

                # ФИО
                worksheet.write(row_idx, 0, op_name, fio_format)

                special_row_idx = special_table_header_row + row_idx
                worksheet.write(special_row_idx, 0, op_name, fio_format)

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

                    if col-1 < special_count:
                        score = special_scores[col-1]
                        if score < 80:
                            fmt = red_format
                        elif score < 95:
                            fmt = yellow_format
                        else:
                            fmt = green_format
                        worksheet.write(special_row_idx, col, score, fmt)
                    else:
                        worksheet.write(special_row_idx, col, '', float_format)

                # Итоговые колонки
                worksheet.write(row_idx, 21, avg_score, total_format)
                worksheet.write(row_idx, 22, count, total_int_format)

                worksheet.write(special_row_idx, 21, special_avg_score, total_format)
                worksheet.write(special_row_idx, 22, special_count, total_int_format)

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

@app.route('/api/admin/users_report', methods=['GET'])
@require_api_key
def get_users_report():
    try:
        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester or requester[3] != 'admin':
            return jsonify({"error": "Only admins can generate users report"}), 403
        
        filename, content = db.generate_users_report()
        if not filename or not content:
            return jsonify({"error": "Failed to generate report"}), 500
        
        return send_file(
            BytesIO(content),
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        logging.error(f"Error generating users report: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/call_evaluation/sv_request', methods=['POST'])
@require_api_key
def sv_request_call():
    """
    Супервайзер отправляет запрос на переоценку по конкретному звонку (call_id).
    Тело JSON: { "call_id": int, "comment": "..." }
    Заголовки: X-User-Id, X-API-Key (require_api_key обеспечит авторизацию)
    """
    try:
        data = request.get_json()
        if not data or 'call_id' not in data:
            return jsonify({"error": "Missing call_id"}), 400

        call_id = int(data['call_id'])
        comment = data.get('comment', '').strip()
        requester_id = int(request.headers.get('X-User-Id'))

        # Проверка: существует ли звонок и принадлежит ли оператор супервайзеру
        call = db.get_call_by_id(call_id)
        if not call:
            return jsonify({"error": "Call not found"}), 404

        # получить запись звонка с operator_id
        with db._get_cursor() as cursor:
            cursor.execute("SELECT operator_id, evaluator_id, month FROM calls WHERE id = %s", (call_id,))
            row = cursor.fetchone()
            if not row:
                return jsonify({"error": "Call not found"}), 404
            operator_id, evaluator_id, call_month = row

        operator = db.get_user(id=operator_id)
        if not operator:
            return jsonify({"error": "Operator not found"}), 404

        # Проверяем что requester — супервайзер этого оператора (или админ)
        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "Requester not found"}), 403

        if requester[3] != 'admin' and operator[6] != requester_id:
            return jsonify({"error": "Only the operator's supervisor or admin can request reevaluation"}), 403

        # Сохраняем заявку в calls
        with db._get_cursor() as cursor:
            cursor.execute("""
                UPDATE calls
                SET sv_request = TRUE,
                    sv_request_comment = %s,
                    sv_request_by = %s,
                    sv_request_at = %s,
                    sv_request_approved = FALSE,
                    sv_request_approved_by = NULL,
                    sv_request_approved_at = NULL
                WHERE id = %s
            """, (comment, requester_id, datetime.utcnow(), call_id))

        # Уведомляем админа через Telegram с inline-кнопкой
        API_TOKEN = os.getenv('BOT_TOKEN')
        admin_chat_id = os.getenv('ADMIN_ID')
        if API_TOKEN and admin_chat_id:
            telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
            text = (
                f"⚠️ <b>Запрос на переоценку (от СВ)</b>\n\n"
                f"📞 Call ID: <b>{call_id}</b>\n"
                f"👤 Оператор ID: <b>{operator_id}</b> / {operator[2]}\n"
                f"📄 Месяц: <b>{call_month}</b>\n"
                f"📝 Комментарий: {comment or '-'}\n\n"
                f"Нажмите кнопку, чтобы одобрить переоценку и открыть звонок для переоценки."
            )
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "Одобрить переоценку ✅", "callback_data": f"approve_reval:{call_id}"}
                    ]
                ]
            }
            try:
                requests.post(telegram_url, json={
                    "chat_id": admin_chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(reply_markup)
                }, timeout=10)
            except Exception as e:
                logging.error(f"Failed to send telegram sv_request notification: {e}")

        return jsonify({"status": "success", "message": "sv_request created"}), 200

    except Exception as e:
        logging.error(f"Error in sv_request_call: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/call_versions/<int:call_id>', methods=['GET'])
@require_api_key
def get_call_versions(call_id):
    try:
        requester_id = getattr(g, 'user_id', None)
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester = db.get_user(id=requester_id)
        if not requester or not _is_privileged_role(requester[3]):
            return jsonify({"error": "Forbidden: only admin or supervisor can access call versions"}), 403

        head_call = db.get_call_by_id(call_id)
        if not head_call:
            return jsonify({"error": "Call not found"}), 404

        if not _ensure_call_access_for_requester(head_call["operator_id"], requester, requester_id):
            return jsonify({"error": "Unauthorized to access this call versions"}), 403

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

@app.route('/api/call_evaluations/<int:evaluation_id>', methods=['DELETE'])
@require_api_key
def delete_draft_evaluation(evaluation_id):
    try:
        # Now delete from imported_calls by id (admin or supervisor of the operator)
        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "Requester not found"}), 403

        with db._get_cursor() as cursor:
            cursor.execute("""
                SELECT operator_id, status FROM imported_calls
                WHERE id = %s
            """, (evaluation_id,))
            row = cursor.fetchone()
            if not row:
                return jsonify({"error": "Imported call not found"}), 404

            operator_id, status = row

            # Prevent deleting already evaluated calls
            if status == 'evaluated':
                return jsonify({"error": "Cannot delete evaluated imported call"}), 400

            # Authorization: admins can delete anything; supervisors can delete calls for their operators
            allowed = False
            try:
                role = requester[3]
            except Exception:
                role = None

            if role == 'admin':
                allowed = True

            if not allowed:
                return jsonify({"error": "Unauthorized to delete this imported call"}), 403

            cursor.execute("""
                DELETE FROM imported_calls WHERE id = %s
            """, (evaluation_id,))

        return jsonify({"status": "success", "message": "Imported call deleted"}), 200
    except Exception as e:
        logging.error(f"Error deleting draft evaluation: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/audio/<int:evaluation_id>', methods=['GET'])
@require_api_key
def get_audio_file(evaluation_id):
    try:
        requester_id = getattr(g, 'user_id', None)
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester = db.get_user(id=requester_id)
        if not requester or requester[3] not in ('admin', 'sv', 'operator'):
            return jsonify({"error": "Audio is not available for this role"}), 403

        if requester[3] == 'operator':
            session_id = _current_session_id_from_access_token()
            if not _is_sensitive_access_unlocked(requester_id, session_id):
                return jsonify({
                    "error": "Sensitive data access requires QR confirmation in current session",
                    "code": "SENSITIVE_ACCESS_REQUIRED"
                }), 403

        call = db.get_call_by_id(evaluation_id)
        if not call or not call['audio_path']:
            return jsonify({"error": "Audio file not found"}), 404

        if not _ensure_call_access_for_requester(call["operator_id"], requester, requester_id):
            return jsonify({"error": "Unauthorized to access this audio"}), 403

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

@app.route('/api/admin/shuffle', methods=['POST'])
@require_api_key
def shuffle_imported_calls():
    """
    Импорт звонков для распределения по операторам (admin shuffle).
    Формат входных данных:
    {
        "month": "2025-11",
        "distribution": [
            {
                "operator": "ФИО",
                "desired": 3,
                "available": 17,
                "calls": [
                    {
                        "id": "uuid",
                        "datetimeRaw": "22.10.2025 13:56:59",
                        "phone": "77084919987",
                        "durationSec": 136.28
                    },
                    ...
                ]
            },
            ...
        ]
    }
    """
    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    if not payload or "month" not in payload or "distribution" not in payload:
        return jsonify({"error": "Missing required fields: month and distribution"}), 400

    try:
        # ID импортирующего администратора / супервизора
        importer_id = getattr(request, "user_id", None)
        result = db.import_calls_from_distribution(payload, importer_id=importer_id)

        return jsonify({
            "status": "ok",
            "month": payload.get("month"),
            "imported": result.get("imported"),
            "updated": result.get("updated"),
            "skipped": result.get("skipped"),
            "missing_operators": result.get("missing_operators"),
            "errors": result.get("errors"),
            "timestamp": datetime.now().isoformat()
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/api/call_evaluation', methods=['POST'])
@require_api_key
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
            if not requester:
                return jsonify({"error": "Requester not found"}), 403

            # admins всегда могут делать переоценки
            if requester[3] == 'admin':
                allowed = True
            else:
                # non-admin может переоценивать ТОЛЬКО если:
                # - передан previous_version_id
                # - previous call exists AND sv_request == TRUE AND sv_request_approved == TRUE
                # - и sv_request_by == requester_id (т.е. именно этот СВ отправил запрос)
                allowed = False
                if previous_version_id:
                    try:
                        prev_id = int(previous_version_id)
                    except Exception:
                        prev_id = None
                    if prev_id:
                        try:
                            with db._get_cursor() as cursor:
                                cursor.execute("SELECT sv_request, sv_request_approved, sv_request_by FROM calls WHERE id = %s", (prev_id,))
                                prev = cursor.fetchone()
                        except Exception as e:
                            logging.error(f"Error checking previous call for re-eval permissions: {e}")
                            prev = None

                        if prev:
                            sv_request_flag = bool(prev[0])
                            sv_request_approved_flag = bool(prev[1])
                            sv_request_by_id = prev[2]
                            if sv_request_flag and sv_request_approved_flag and sv_request_by_id == requester_id:
                                allowed = True

            if not allowed:
                return jsonify({"error": "Only admins or the supervisor who requested and was approved can perform re-evaluations"}), 403


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
        API_TOKEN = os.getenv('BOT_TOKEN')
        admin = os.getenv('ADMIN_ID')
        if not admin:
            return

        message = (
            f"👤 Оценивающий: <b>{evaluator_name}</b>\n"
            f"📋 Оператор: <b>{operator_name}</b>\n"
            f"📄 За месяц: <b>{month}</b>\n"
            f"📱 Номер телефона: <b>{phone_number}</b>\n"
            f"📅 Дата обращения: <b>{' '.join(appeal_date.split('T'))}</b>\n"
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
        if not data or 'status' not in data:
            return jsonify({"error": "Missing status field"}), 400

        new_status = data['status']
        allowed_statuses = {"active", "break", "training", "inactive", "tech","iesigning"}
        if new_status not in allowed_statuses:
            return jsonify({"error": f"Invalid status. Allowed: {', '.join(allowed_statuses)}"}), 400

        user_id = int(request.headers.get('X-User-Id'))
        user = db.get_user(id=user_id)
        if not user:
            return jsonify({"error": "Unauthurized"}), 403

        # здесь предполагаем, что user[10] хранит BOOLEAN (is_active)
        current_active = user[10]
        new_active_bool = True if new_status == "active" else False

        # если статус active ↔ True/False не меняется — нет смысла обновлять
        if current_active == new_active_bool:
            # но дополнительно можно проверить, не был ли изменён текстовый статус (например break/training)
            last_log_status = db.get_last_activity_status(user_id)  # нужна функция в db
            if last_log_status == new_status:
                return jsonify({"status": "unchanged", "message": "Status is already set to the requested value"})

        # обновляем users.is_active (bool)
        success = db.set_user_active(user_id, new_status)
        if not success:
            return jsonify({"error": "Failed to update user active flag"}), 500

        # записываем строковый статус в логи
        log_success = db.log_activity(user_id, new_status)
        if not log_success:
            logging.warning("User active flag updated but logging failed")
            return jsonify({
                "status": "partial_success",
                "message": "User active flag updated, but logging failed"
            }), 500

        return jsonify({"status": "success", "message": f"Status updated to '{new_status}'"}), 200

    except Exception as e:
        logging.error(f"Error toggling status: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/active_operators', methods=['GET'])
@require_api_key
def get_active_operators():
    try:
        direction_name = request.args.get("direction")  # например: /api/active_operators?direction=Sales
        operators = db.get_active_operators(direction_name)
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

MONTH_RE = re.compile(r'^\d{4}-\d{2}$')

def build_trainings_map(trainings_list):
    """
    Преобразует список тренингов в структуру { operator_id: { dayNum: [training...] } }.
    Ожидается, что у тренинга есть поле 'operator_id' и 'date' в формате 'YYYY-MM-DD'.
    """
    tmap = {}
    if not trainings_list:
        return tmap
    for t in trainings_list:
        op = t.get('operator_id')
        if op is None:
            continue
        dt = t.get('date') or t.get('date_str') or t.get('day')
        day = None
        if isinstance(dt, str) and len(dt) >= 10:
            try:
                day = int(dt[8:10])
            except Exception:
                day = None
        elif isinstance(dt, int):
            day = dt
        if day is None:
            # пропускаем записи без понятного дня
            continue
        tmap.setdefault(op, {}).setdefault(day, []).append(t)
    return tmap

@app.route('/api/report/monthly_hours', methods=['GET'])
@require_api_key
def get_monthly_report_hours():
    try:
        supervisor_id = request.args.get('supervisor_id')
        month = request.args.get('month')
        if not month or not MONTH_RE.match(month):
            return jsonify({"error": "month required in format YYYY-MM"}), 400

        # requester
        try:
            requester_id = int(request.headers.get('X-User-Id'))
        except Exception:
            return jsonify({"error": "X-User-Id header required"}), 400

        requester = db.get_user(id=requester_id)
        if not requester:
            return jsonify({"error": "Requester not found"}), 404

        # роль в requester ожидается в requester[3] как в вашем примере
        role = requester[3] 

        # Если supervisor_id не указан — формируем общий отчёт для всех операторов .
        # Если supervisor_id указан или requester — sv без param, формируем отчёт по конкретному СВ.
        generate_all = False
        if not supervisor_id:
                generate_all = True
        else:
            try:
                supervisor_id = int(supervisor_id)
            except ValueError:
                return jsonify({"error": "supervisor_id must be integer"}), 400

        # проверка прав для случая конкретного SV: admin или сам SV
        if not generate_all:
            if role != 'admin' and role != 'sv':
                return jsonify({"error": "Unauthorized to access this report"}), 403

        logging.info("Начало генерации отчета: supervisor_id=%s month=%s generate_all=%s", supervisor_id, month, generate_all)

        try:
            if generate_all:
                operators = db.get_daily_hours_for_all_month(month)
            else:
                operators = db.get_daily_hours_by_supervisor_month(supervisor_id, month)
        except Exception as e:
            logging.exception("Ошибка получения operators из db")
            return jsonify({"error": f"Ошибка получения операторов: {str(e)}"}), 500

        try:
            if generate_all:
                trainings_list = db.get_trainings(None, month)
            else:
                trainings_list = db.get_trainings(supervisor_id, month)
        except Exception as e:
            logging.exception("Ошибка получения trainings из db")
            trainings_list = []

        trainings_map = build_trainings_map(trainings_list)

        if generate_all:
            filename, content = db.generate_excel_report_all_operators_from_view(operators, trainings_map, month)
        else:
            filename, content = db.generate_excel_report_from_view(operators, trainings_map, month)
            
        if not filename or not content:
            logging.error("Генерация отчёта вернула пустой результат")
            return jsonify({"error": "Failed to generate report"}), 500

        return send_file(
            BytesIO(content),
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        logging.exception("Error generating monthly report")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/trainings', methods=['GET'])
@require_api_key
def get_trainings():
    
    try:
        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester:
            logging.warning(f"User not found: {requester_id}")
            return jsonify({"error": "User not found"}), 404

        month = request.args.get('month')
        if month:
            try:
                datetime.strptime(month, '%Y-%m')
            except ValueError:
                logging.warning(f"Invalid month format: {month}")
                return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400

        role = requester[3]
        if role not in ['admin', 'sv', 'operator']:
            logging.warning(f"Unauthorized role: {role}")
            return jsonify({"error": "Unauthorized"}), 403

        # Allow optional ?id=<supervisor_id> to fetch trainings for a specific supervisor
        # If id is absent, default to requester_id (own data)
        user_param = request.args.get('id')
        if user_param and str(user_param).isdigit():
            target_id = int(user_param)
        else:
            target_id = requester_id

        trainings = db.get_trainings(target_id, month)

        logging.info(f"Trainings fetched for role {role}, month: {month}, by user {requester_id}")
        return jsonify({"status": "success", "trainings": trainings}), 200
    except Exception as e:
        logging.error(f"Error fetching trainings: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/trainings', methods=['POST'])
@require_api_key
def add_training():
    
    try:
        data = request.get_json()
        required_fields = ['operator_id', 'date', 'start_time', 'end_time', 'reason']
        if not data or not all(field in data for field in required_fields):
            logging.warning(f"Missing required fields in add_training: {data}")
            return jsonify({"error": "Missing required fields"}), 400

        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester:
            logging.warning(f"User not found: {requester_id}")
            return jsonify({"error": "User not found"}), 404

        # Проверка прав: админ или супервайзер оператора
        operator = db.get_user(id=data['operator_id'])
        if not operator:
            logging.warning(f"Operator not found: {data['operator_id']}")
            return jsonify({"error": "Operator not found"}), 404
        
        if requester[3] != 'admin' and  requester[3] != 'sv':
            logging.warning(f"Unauthorized attempt to add training by user {requester_id}")
            return jsonify({"error": "Unauthorized"}), 403

        # Валидация данных
        try:
            datetime.strptime(data['date'], '%Y-%m-%d')
            datetime.strptime(data['start_time'], '%H:%M')
            datetime.strptime(data['end_time'], '%H:%M')
        except ValueError:
            logging.warning(f"Invalid date/time format in add_training: {data}")
            return jsonify({"error": "Invalid date or time format"}), 400

        allowed_reasons = [
            "Обратная связь", "Собрание", "Тех. сбой", "Мотивационная беседа",
            "Дисциплинарный тренинг", "Тренинг по качеству. Разбор ошибок",
            "Тренинг по качеству. Объяснение МШ", "Тренинг по продукту",
            "Мониторинг", "Практика в офисе таксопарка", "Другое"
        ]
        if data['reason'] not in allowed_reasons:
            logging.warning(f"Invalid training reason: {data['reason']}")
            return jsonify({"error": "Invalid training reason"}), 400

        training_id = db.add_training(
            operator_id=data['operator_id'],
            training_date=data['date'],
            start_time=data['start_time'],
            end_time=data['end_time'],
            reason=data['reason'],
            comment=data.get('comment'),
            created_by=requester_id,
            count_in_hours=data.get("count_in_hours", True)
        )
        logging.info(f"Training added: ID {training_id} for operator {data['operator_id']}")
        return jsonify({"status": "success", "id": training_id}), 201
    except Exception as e:
        logging.error(f"Error adding training: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/trainings/<int:training_id>', methods=['PUT'])
@require_api_key
def update_training(training_id):
    
    try:
        data = request.get_json()
        if not data:
            logging.warning("No data provided for update_training")
            return jsonify({"error": "No data provided"}), 400

        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester:
            logging.warning(f"User not found: {requester_id}")
            return jsonify({"error": "User not found"}), 404

        # Проверка прав: админ или создатель тренинга
        with db._get_cursor() as cursor:
            cursor.execute("""
                SELECT created_by FROM trainings WHERE id = %s
            """, (training_id,))
            training = cursor.fetchone()
            if not training:
                logging.warning(f"Training not found: {training_id}")
                return jsonify({"error": "Training not found"}), 404

            if requester[3] != 'admin' and requester[3] != 'sv':
                logging.warning(f"Unauthorized attempt to update training {training_id} by user {requester_id}")
                return jsonify({"error": "Unauthorized"}), 403

        # Валидация данных, если они предоставлены
        if 'date' in data:
            try:
                datetime.strptime(data['date'], '%Y-%m-%d')
            except ValueError:
                logging.warning(f"Invalid date format: {data['date']}")
                return jsonify({"error": "Invalid date format"}), 400
        if 'start_time' in data:
            try:
                datetime.strptime(data['start_time'], '%H:%M')
            except ValueError:
                logging.warning(f"Invalid start_time format: {data['start_time']}")
                return jsonify({"error": "Invalid start time format"}), 400
        if 'end_time' in data:
            try:
                datetime.strptime(data['end_time'], '%H:%M')
            except ValueError:
                logging.warning(f"Invalid end_time format: {data['end_time']}")
                return jsonify({"error": "Invalid end time format"}), 400
        if 'reason' in data:
            allowed_reasons = [
                "Обратная связь", "Собрание", "Тех. сбой", "Мотивационная беседа",
                "Дисциплинарный тренинг", "Тренинг по качеству. Разбор ошибок",
                "Тренинг по качеству. Объяснение МШ", "Тренинг по продукту",
                "Мониторинг", "Практика в офисе таксопарка", "Другое"
            ]
            if data['reason'] not in allowed_reasons:
                logging.warning(f"Invalid training reason: {data['reason']}")
                return jsonify({"error": "Invalid training reason"}), 400

        success = db.update_training(
            training_id=training_id,
            training_date=data.get('date'),
            start_time=data.get('start_time'),
            end_time=data.get('end_time'),
            reason=data.get('reason'),
            comment=data.get('comment'),
            count_in_hours=data.get('count_in_hours') 
        )
        if not success:
            logging.warning(f"Failed to update training {training_id}")
            return jsonify({"error": "Failed to update training"}), 500

        logging.info(f"Training updated: ID {training_id}")
        return jsonify({"status": "success", "message": "Training updated"}), 200
    except Exception as e:
        logging.error(f"Error updating training {training_id}: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/trainings/<int:training_id>', methods=['DELETE'])
@require_api_key
def delete_training(training_id):
    try:
        requester_id = int(request.headers.get('X-User-Id'))
        requester = db.get_user(id=requester_id)
        if not requester:
            logging.warning(f"User not found: {requester_id}")
            return jsonify({"error": "User not found"}), 404

        # Проверка прав: админ или создатель тренинга
        with db._get_cursor() as cursor:
            cursor.execute("""
                SELECT created_by FROM trainings WHERE id = %s
            """, (training_id,))
            training = cursor.fetchone()
            if not training:
                logging.warning(f"Training not found: {training_id}")
                return jsonify({"error": "Training not found"}), 404
            
            if requester[3] != 'admin' and requester[3] != 'sv':
                logging.warning(f"Unauthorized attempt to delete training {training_id} by user {requester_id}")
                return jsonify({"error": "Unauthorized"}), 403

        success = db.delete_training(training_id)
        if not success:
            logging.warning(f"Failed to delete training {training_id}")
            return jsonify({"error": "Failed to delete training"}), 500

        logging.info(f"Training deleted: ID {training_id}")
        return jsonify({"status": "success", "message": "Training deleted"}), 200
    except Exception as e:
        logging.error(f"Error deleting training {training_id}: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


# ==================== Work Schedules API ====================

WORK_SCHEDULE_EXCEL_SHIFT_RE = re.compile(
    r'(?P<sh>\d{1,2})(?:[:/](?P<sm>\d{1,2}))?\s*\*\s*(?P<eh>\d{1,2})(?:[:/](?P<em>\d{1,2}))?'
)


def _normalize_schedule_excel_name_key(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).replace('ё', 'е').replace('Ё', 'Е').lower()


def _parse_schedule_excel_header_date(cell):
    value = cell.value
    if value is None:
        return None

    # openpyxl для xlsx чаще всего уже возвращает datetime/date при is_date=True
    if getattr(cell, 'is_date', False):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, dt_date):
            return value

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, dt_date):
        return value

    text = str(value).strip()
    if not text:
        return None
    for fmt in ('%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue
    return None


def _format_schedule_excel_compact_time(hhmm):
    try:
        hh, mm = str(hhmm).split(':', 1)
        h = int(hh)
        m = int(mm)
    except Exception:
        return str(hhmm or '')
    if m == 0:
        return str(h)
    return f"{h}/{m:02d}"


def _parse_schedule_excel_cell_value(raw_value):
    """
    Возвращает:
      {'kind': 'empty'}
      {'kind': 'day_off'}
      {'kind': 'shifts', 'shifts': [{'start': 'HH:MM', 'end': 'HH:MM'}, ...]}
      {'kind': 'invalid', 'error': '...'}
    """
    if raw_value is None:
        return {'kind': 'empty'}

    text = str(raw_value).strip()
    if not text:
        return {'kind': 'empty'}

    lowered = text.lower()
    shift_matches = list(WORK_SCHEDULE_EXCEL_SHIFT_RE.finditer(text))

    if not shift_matches and 'выходн' in lowered:
        return {'kind': 'day_off'}

    shifts = []
    seen = set()
    for m in shift_matches:
        try:
            sh = int(m.group('sh'))
            sm = int(m.group('sm') or 0)
            eh = int(m.group('eh'))
            em = int(m.group('em') or 0)
        except Exception:
            continue

        if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
            return {'kind': 'invalid', 'error': f'Некорректное время: {m.group(0)}'}

        start = f"{sh:02d}:{sm:02d}"
        end = f"{eh:02d}:{em:02d}"
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        shifts.append({'start': start, 'end': end})

    if shifts:
        return {'kind': 'shifts', 'shifts': shifts}

    if 'выходн' in lowered:
        return {'kind': 'day_off'}

    return {'kind': 'invalid', 'error': 'Ячейка не похожа на смену или "Выходной"'}


def _ws_time_to_minutes(value):
    if value is None:
        return 0
    parts = str(value).strip().split(':')
    try:
        hh = int(parts[0])
        mm = int(parts[1]) if len(parts) > 1 else 0
    except Exception:
        return 0
    return hh * 60 + mm


def _ws_minutes_to_time(minutes):
    minutes = int(round(minutes)) % (24 * 60)
    hh = minutes // 60
    mm = minutes % 60
    return f"{hh:02d}:{mm:02d}"


def _ws_compute_breaks_for_shift_minutes(start_min, end_min):
    dur = int(end_min) - int(start_min)
    if dur <= 0:
        return []
    breaks = []

    def snap5(x):
        return int(round(float(x) / 5.0) * 5)

    def push_centered(center, size):
        center = snap5(center)
        s = snap5(center - (size / 2))
        e = s + int(size)
        s = max(int(start_min), min(int(end_min), s))
        e = max(int(start_min), min(int(end_min), e))
        if e > s:
            breaks.append({'start': s, 'end': e})

    if dur >= 5 * 60 and dur < 6 * 60:
        push_centered(start_min + dur * 0.5, 15)
    elif dur >= 6 * 60 and dur < 8 * 60:
        push_centered(start_min + dur / 3, 15)
        push_centered(start_min + 2 * dur / 3, 15)
    elif dur >= 8 * 60 and dur < 11 * 60:
        centers = [start_min + dur * 0.25, start_min + dur * 0.5, start_min + dur * 0.75]
        sizes = [15, 30, 15]
        for c, sz in zip(centers, sizes):
            push_centered(c, sz)
    elif dur >= 11 * 60:
        centers = [start_min + dur * 0.2, start_min + dur * 0.45, start_min + dur * 0.7, start_min + dur * 0.87]
        sizes = [15, 30, 15, 15]
        for c, sz in zip(centers, sizes):
            push_centered(c, sz)

    normalized = []
    seen = set()
    for b in sorted(breaks, key=lambda x: (int(x.get('start', 0)), int(x.get('end', 0)))):
        s = int(b.get('start', 0))
        e = int(b.get('end', 0))
        if e <= s:
            continue
        key = (s, e)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({'start': s, 'end': e})
    return normalized


def _ws_intervals_overlap(a, b):
    return int(a['start']) < int(b['end']) and int(b['start']) < int(a['end'])


def _ws_merge_intervals(items):
    if not items:
        return []
    sorted_items = sorted(
        [{'start': int(x['start']), 'end': int(x['end'])} for x in items if int(x.get('end', 0)) > int(x.get('start', 0))],
        key=lambda x: (x['start'], x['end'])
    )
    if not sorted_items:
        return []
    res = [dict(sorted_items[0])]
    for cur in sorted_items[1:]:
        last = res[-1]
        if cur['start'] <= last['end']:
            last['end'] = max(last['end'], cur['end'])
        else:
            res.append(dict(cur))
    return res


def _ws_parse_date_str(value):
    return datetime.strptime(str(value), '%Y-%m-%d').date()


def _ws_date_str(value):
    if isinstance(value, datetime):
        return value.date().strftime('%Y-%m-%d')
    if isinstance(value, dt_date):
        return value.strftime('%Y-%m-%d')
    return str(value)


def _ws_add_days_str(date_str, delta_days):
    return (_ws_parse_date_str(date_str) + timedelta(days=int(delta_days))).strftime('%Y-%m-%d')


def _ws_clone_ops_for_break_simulation(operators):
    cloned = []
    for op in (operators or []):
        shifts = {}
        for d, segs in (op.get('shifts') or {}).items():
            cloned_segs = []
            for seg in (segs or []):
                seg_copy = {
                    'start': str(seg.get('start') or ''),
                    'end': str(seg.get('end') or ''),
                    'breaks': [ {'start': int(b.get('start')), 'end': int(b.get('end'))} for b in (seg.get('breaks') or []) if b is not None ]
                }
                smin = _ws_time_to_minutes(seg_copy['start'])
                emin = _ws_time_to_minutes(seg_copy['end'])
                if emin <= smin:
                    emin += 1440
                seg_copy['__startMin'] = smin
                seg_copy['__endMin'] = emin
                cloned_segs.append(seg_copy)
            shifts[str(d)] = cloned_segs
        cloned.append({
            'id': op.get('id'),
            'direction': op.get('direction'),
            'shifts': shifts,
            'daysOff': list(op.get('daysOff') or [])
        })
    return cloned


def _ws_sanitize_break_direction_groups(groups):
    if not isinstance(groups, list):
        return []
    used = set()
    seen_groups = set()
    result = []
    for group in groups:
        if not isinstance(group, list):
            continue
        normalized = []
        local_keys = set()
        for raw in group:
            label = str(raw or '').strip()
            key = label.lower()
            if not key or key in used or key in local_keys:
                continue
            local_keys.add(key)
            normalized.append(label)
        if len(normalized) < 2:
            continue
        sig = '|'.join(sorted(x.lower() for x in normalized))
        if sig in seen_groups:
            continue
        seen_groups.add(sig)
        for x in normalized:
            used.add(x.lower())
        result.append(normalized)
    return result


def _ws_make_direction_scope_resolver(break_direction_groups):
    scope_map = {}
    for group in _ws_sanitize_break_direction_groups(break_direction_groups):
        keys = sorted({str(x or '').strip().lower() for x in group if str(x or '').strip()})
        if len(keys) < 2:
            continue
        scope_key = f"group:{'|'.join(keys)}"
        for k in keys:
            scope_map[k] = scope_key

    def _resolver(direction_value):
        key = str(direction_value or '').strip().lower()
        if not key:
            return 'dir:'
        return scope_map.get(key) or f"dir:{key}"

    return _resolver


def _ws_build_occupied_intervals_for_date(all_operators, date_str, exclude_op_id, direction_scope, get_scope_key):
    occupied = []
    prev_str = _ws_add_days_str(date_str, -1)
    next_str = _ws_add_days_str(date_str, 1)
    target_scope = str(get_scope_key(direction_scope))

    def clamp_push(s, e):
        ns = max(0, min(2880, int(s)))
        ne = max(0, min(2880, int(e)))
        if ne > ns:
            occupied.append({'start': ns, 'end': ne})

    for op in (all_operators or []):
        if int(op.get('id') or 0) == int(exclude_op_id or 0):
            continue
        if str(get_scope_key(op.get('direction'))) != target_scope:
            continue

        for seg in (op.get('shifts', {}).get(date_str) or []):
            for b in (seg.get('breaks') or []):
                clamp_push(b.get('start', 0), b.get('end', 0))

        for seg in (op.get('shifts', {}).get(prev_str) or []):
            for b in (seg.get('breaks') or []):
                clamp_push(int(b.get('start', 0)) - 1440, int(b.get('end', 0)) - 1440)

        for seg in (op.get('shifts', {}).get(next_str) or []):
            for b in (seg.get('breaks') or []):
                clamp_push(int(b.get('start', 0)) + 1440, int(b.get('end', 0)) + 1440)

    return _ws_merge_intervals(occupied)


def _ws_find_non_overlapping_start(desired_start, length, seg_start, seg_end, occupied_intervals):
    step = 5
    def snap(v):
        return int(round(float(v) / step) * step)

    desired_start = snap(desired_start)
    candidates = [0]
    max_shift = max(int(seg_end) - int(seg_start), 60)
    for d in range(step, max_shift + step, step):
        candidates.append(d)
        candidates.append(-d)

    for delta in candidates:
        s = desired_start + delta
        start = max(int(seg_start), min(int(seg_end) - int(length), s))
        end = start + int(length)
        if start < int(seg_start) or end > int(seg_end):
            continue
        test_iv = {'start': start, 'end': end}
        if any(_ws_intervals_overlap(test_iv, occ) for occ in (occupied_intervals or [])):
            continue
        return start
    return None


def _ws_adjust_breaks_for_operator_on_date(op, date_str, all_operators, get_direction_scope_key):
    segs = (op or {}).get('shifts', {}).get(date_str) or []
    if not segs:
        return
    occupied = _ws_build_occupied_intervals_for_date(
        all_operators=all_operators,
        date_str=date_str,
        exclude_op_id=op.get('id'),
        direction_scope=op.get('direction'),
        get_scope_key=get_direction_scope_key
    )
    for seg in segs:
        raw_seg_start = int(seg.get('__startMin', _ws_time_to_minutes(seg.get('start'))))
        seg_start_base = _ws_time_to_minutes(seg.get('start'))
        seg_end_base = _ws_time_to_minutes(seg.get('end'))
        raw_seg_end = int(seg.get('__endMin', seg_end_base + (1440 if seg_end_base <= seg_start_base else 0)))
        if not isinstance(seg.get('breaks'), list):
            seg['breaks'] = []
        if len(seg['breaks']) == 0:
            seg['breaks'] = _ws_compute_breaks_for_shift_minutes(raw_seg_start, raw_seg_end)

        seg_start = max(0, raw_seg_start)
        seg_end = max(seg_start, min(2880, raw_seg_end))
        new_breaks = []
        for b in (seg.get('breaks') or []):
            b_start = int(b.get('start', 0))
            b_end = int(b.get('end', 0))
            length = b_end - b_start
            if length <= 0 or (seg_end - seg_start) <= 0:
                continue
            desired_start = max(seg_start, min(seg_end - length, b_start))
            found = _ws_find_non_overlapping_start(desired_start, length, seg_start, seg_end, occupied)
            if found is not None:
                nb = {'start': int(found), 'end': int(found) + int(length)}
            else:
                clamped_start = max(seg_start, min(seg_end - length, b_start))
                clamped_end = max(seg_start, min(seg_end, b_end))
                if clamped_end <= clamped_start:
                    continue
                nb = {'start': clamped_start, 'end': clamped_end}
            new_breaks.append(nb)
            occupied.append(nb)
            occupied = _ws_merge_intervals(occupied)
        seg['breaks'] = new_breaks


def _iter_schedule_excel_dates(start_date_obj, end_date_obj):
    cur = start_date_obj
    while cur <= end_date_obj:
        yield cur
        cur = cur + timedelta(days=1)


@app.route('/api/work_schedules/my', methods=['GET'])
@require_api_key
def get_my_work_schedules():
    """
    Получить смены/перерывы/выходные текущего оператора.
    Query params: start_date, end_date (optional, format: YYYY-MM-DD)
    """
    try:
        requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester_id = int(requester_id)
        user_data = db.get_user(id=requester_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404

        if user_data[3] != 'operator':
            return jsonify({"error": "Forbidden"}), 403

        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        operator_schedule = db.get_operator_with_shifts(requester_id, start_date, end_date)
        if not operator_schedule:
            return jsonify({"error": "Operator not found"}), 404

        return jsonify({"operator": operator_schedule}), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error getting my work schedules: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/operators', methods=['GET'])
@require_api_key
def get_operators_with_schedules():
    """
    Получить всех операторов с их сменами и выходными днями.
    Query params: start_date, end_date (optional, format: YYYY-MM-DD)
    """
    try:
        user = request.headers.get('X-User-Id')
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        
        user_id = int(user)
        user_data = db.get_user(id=user_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404
        
        role = user_data[3]
        
        # Только admin и sv могут видеть планировщик смен
        if role not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403
        
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        operators = db.get_operators_with_shifts(start_date, end_date)
        
        return jsonify({"operators": operators}), 200
    
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error getting operators with schedules: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/shift', methods=['POST'])
@require_api_key
def save_work_shift():
    """
    Сохранить смену для оператора.
    Body: {
        "operator_id": int,
        "shift_date": "YYYY-MM-DD",
        "start_time": "HH:MM",
        "end_time": "HH:MM",
        "breaks": [{"start": minutes, "end": minutes}, ...],  // optional
        "previous_start_time": "HH:MM",  // optional, for edit without duplicates
        "previous_end_time": "HH:MM"     // optional, for edit without duplicates
    }
    """
    try:
        user = request.headers.get('X-User-Id')
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        
        user_id = int(user)
        user_data = db.get_user(id=user_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404
        
        role = user_data[3]
        
        if role not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403
        
        data = request.get_json(silent=True) or {}
        operator_id = data.get('operator_id')
        shift_date = data.get('shift_date')
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        breaks = data.get('breaks')
        previous_start_time = data.get('previous_start_time')
        previous_end_time = data.get('previous_end_time')
        
        if not all([operator_id, shift_date, start_time, end_time]):
            return jsonify({"error": "Missing required fields"}), 400
        
        shift_id = db.save_shift(
            operator_id,
            shift_date,
            start_time,
            end_time,
            breaks,
            previous_start_time=previous_start_time,
            previous_end_time=previous_end_time
        )
        
        return jsonify({"message": "Shift saved successfully", "shift_id": shift_id}), 200
    
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error saving shift: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/shift', methods=['DELETE'])
@require_api_key
def delete_work_shift():
    """
    Удалить смену оператора.
    Body: {
        "operator_id": int,
        "shift_date": "YYYY-MM-DD",
        "start_time": "HH:MM",
        "end_time": "HH:MM"
    }
    """
    try:
        user = request.headers.get('X-User-Id')
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        
        user_id = int(user)
        user_data = db.get_user(id=user_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404
        
        role = user_data[3]
        
        if role not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403
        
        data = request.get_json(silent=True) or {}
        operator_id = data.get('operator_id')
        shift_date = data.get('shift_date')
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        
        if not all([operator_id, shift_date, start_time, end_time]):
            return jsonify({"error": "Missing required fields"}), 400
        
        success = db.delete_shift(operator_id, shift_date, start_time, end_time)
        
        if success:
            return jsonify({"message": "Shift deleted successfully"}), 200
        else:
            return jsonify({"error": "Shift not found"}), 404
    
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error deleting shift: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/day_off', methods=['POST'])
@require_api_key
def toggle_work_day_off():
    """
    Переключить выходной день для оператора.
    Body: {
        "operator_id": int,
        "day_off_date": "YYYY-MM-DD"
    }
    """
    try:
        user = request.headers.get('X-User-Id')
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        
        user_id = int(user)
        user_data = db.get_user(id=user_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404
        
        role = user_data[3]
        
        if role not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403
        
        data = request.get_json(silent=True) or {}
        operator_id = data.get('operator_id')
        day_off_date = data.get('day_off_date')
        
        if not all([operator_id, day_off_date]):
            return jsonify({"error": "Missing required fields"}), 400
        
        is_day_off = db.toggle_day_off(operator_id, day_off_date)
        
        return jsonify({
            "message": "Day off toggled successfully",
            "is_day_off": is_day_off
        }), 200
    
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error toggling day off: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/status_period', methods=['POST'])
@require_api_key
def save_work_schedule_status_period():
    """
    Сохранить специальный статус оператора на период.
    Body: {
        "operator_id": int,
        "status_code": "bs" | "sick_leave" | "annual_leave" | "dismissal",
        "start_date": "YYYY-MM-DD",
        "end_date": "YYYY-MM-DD",              # required except dismissal; for dismissal optional (прерывание)
        "dismissal_reason": "...",             # required for dismissal
        "is_blacklist": true|false,            # optional: only for dismissal (ЧС / без восстановления)
        "comment": "..."                       # required for dismissal
        "range_start": "YYYY-MM-DD",           # optional: return operator snapshot for range
        "range_end": "YYYY-MM-DD"              # optional
    }
    """
    try:
        requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester_id = int(requester_id)
        user_data = db.get_user(id=requester_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404

        if user_data[3] not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json(silent=True) or {}
        operator_id = data.get('operator_id')
        status_code = data.get('status_code')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        dismissal_reason = data.get('dismissal_reason')
        is_blacklist = data.get('is_blacklist')
        comment = data.get('comment')
        range_start = data.get('range_start')
        range_end = data.get('range_end')

        if not operator_id or not status_code or not start_date:
            return jsonify({"error": "Missing required fields"}), 400

        status_period = db.save_schedule_status_period(
            operator_id=operator_id,
            status_code=status_code,
            start_date=start_date,
            end_date=end_date,
            dismissal_reason=dismissal_reason,
            is_blacklist=is_blacklist,
            comment=comment,
            created_by=requester_id
        )

        operator_snapshot = None
        if range_start and range_end:
            operator_snapshot = db.get_operator_with_shifts(operator_id, range_start, range_end)

        return jsonify({
            "message": "Status period saved successfully",
            "status_period": status_period,
            "operator": operator_snapshot
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error saving status period: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/status_period', methods=['DELETE'])
@require_api_key
def delete_work_schedule_status_period():
    """
    Удалить специальный статус оператора (период) по id.
    Body: {
        "status_period_id": int,
        "operator_id": int,        # optional but recommended
        "range_start": "YYYY-MM-DD",
        "range_end": "YYYY-MM-DD"
    }
    """
    try:
        requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester_id = int(requester_id)
        user_data = db.get_user(id=requester_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404

        if user_data[3] not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json(silent=True) or {}
        status_period_id = data.get('status_period_id')
        operator_id = data.get('operator_id')
        range_start = data.get('range_start')
        range_end = data.get('range_end')

        if not status_period_id:
            return jsonify({"error": "Missing status_period_id"}), 400

        deleted_period = db.delete_schedule_status_period(
            status_period_id=status_period_id,
            operator_id=operator_id
        )
        if not deleted_period:
            return jsonify({"error": "Status period not found"}), 404

        target_operator_id = operator_id or deleted_period.get('operatorId')
        operator_snapshot = None
        if target_operator_id and range_start and range_end:
            operator_snapshot = db.get_operator_with_shifts(target_operator_id, range_start, range_end)

        return jsonify({
            "message": "Status period deleted successfully",
            "deleted_status_period": deleted_period,
            "operator": operator_snapshot
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error deleting status period: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/shifts_bulk', methods=['POST'])
@require_api_key
def save_shifts_bulk():
    """
    Массовое сохранение смен для оператора.
    Body: {
        "operator_id": int,
        "shifts": [
            {
                "date": "YYYY-MM-DD",
                "start": "HH:MM",
                "end": "HH:MM",
                "breaks": [{"start": minutes, "end": minutes}, ...]  // optional
            },
            ...
        ]
    }
    """
    try:
        user = request.headers.get('X-User-Id')
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        
        user_id = int(user)
        user_data = db.get_user(id=user_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404
        
        role = user_data[3]
        
        if role not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403
        
        data = request.get_json(silent=True) or {}
        operator_id = data.get('operator_id')
        shifts = data.get('shifts', [])
        
        if not operator_id:
            return jsonify({"error": "Missing operator_id"}), 400
        
        if not shifts:
            return jsonify({"error": "No shifts provided"}), 400
        
        shift_ids = db.save_shifts_bulk(operator_id, shifts)
        
        return jsonify({
            "message": f"Successfully saved {len(shift_ids)} shifts",
            "shift_ids": shift_ids
        }), 200
    
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error saving shifts bulk: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/bulk_actions', methods=['POST'])
@require_api_key
def apply_work_schedule_bulk_actions():
    """
    Атомарные массовые действия по графикам в одном запросе.
    Body: {
        "actions": [
            {"action": "set_shift", "operator_id": 1, "date": "YYYY-MM-DD", "start": "HH:MM", "end": "HH:MM", "breaks": [...]},
            {"action": "set_day_off", "operator_id": 1, "date": "YYYY-MM-DD"},
            {"action": "delete_shifts", "operator_id": 1, "date": "YYYY-MM-DD"}  # очистка дня: смены + выходной
        ]
    }
    """
    try:
        requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester_id = int(requester_id)
        user_data = db.get_user(id=requester_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404

        if user_data[3] not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403

        data = request.get_json(silent=True) or {}
        actions = data.get('actions')
        result = db.apply_work_schedule_bulk_actions(actions)
        return jsonify({
            "message": "Bulk work schedule actions applied successfully",
            "result": result
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error applying bulk work schedule actions: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/export_excel', methods=['GET'])
@require_api_key
def export_work_schedules_excel():
    """
    Экспорт графика в Excel-матрицу:
    ФИО | Ставка | YYYY-MM-DD даты (в файле выводятся как dd.mm.yyyy)
    """
    try:
        requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester_id = int(requester_id)
        user_data = db.get_user(id=requester_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404
        if user_data[3] not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403

        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required"}), 400

        start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
        if end_date_obj < start_date_obj:
            return jsonify({"error": "end_date must be >= start_date"}), 400

        operators = db.get_operators_with_shifts(start_date, end_date) or []
        operators = sorted(operators, key=lambda op: str(op.get('name') or '').lower())

        wb = Workbook()
        ws = wb.active
        ws.title = 'График'

        ws.cell(row=1, column=1, value='ФИО')
        ws.cell(row=1, column=2, value='Ставка')
        date_list = list(_iter_schedule_excel_dates(start_date_obj, end_date_obj))
        for idx, day_obj in enumerate(date_list, start=3):
            cell = ws.cell(row=1, column=idx, value=day_obj)
            cell.number_format = 'DD.MM.YYYY'

        for col_idx in range(1, 3 + len(date_list)):
            ws.cell(row=1, column=col_idx).font = ws.cell(row=1, column=col_idx).font.copy(bold=True)

        def _format_shift_cell_segment(shift_item):
            start = _format_schedule_excel_compact_time(shift_item.get('start'))
            end = _format_schedule_excel_compact_time(shift_item.get('end'))
            return f"{start}*{end}"

        for row_idx, op in enumerate(operators, start=2):
            ws.cell(row=row_idx, column=1, value=op.get('name') or '')
            rate_val = op.get('rate')
            ws.cell(row=row_idx, column=2, value=rate_val if rate_val is not None else '')

            shifts_by_date = op.get('shifts') or {}
            days_off_set = set(op.get('daysOff') or [])

            for col_idx, day_obj in enumerate(date_list, start=3):
                day_key = day_obj.strftime('%Y-%m-%d')
                shifts = shifts_by_date.get(day_key) or []
                if shifts:
                    ws.cell(row=row_idx, column=col_idx, value=','.join(_format_shift_cell_segment(s) for s in shifts))
                elif day_key in days_off_set:
                    ws.cell(row=row_idx, column=col_idx, value='Выходной')
                else:
                    ws.cell(row=row_idx, column=col_idx, value='')

        ws.freeze_panes = 'C2'
        ws.auto_filter.ref = ws.dimensions
        ws.column_dimensions['A'].width = 36
        ws.column_dimensions['B'].width = 10
        for i in range(len(date_list)):
            col_letter = ws.cell(row=1, column=3 + i).column_letter
            ws.column_dimensions[col_letter].width = 12

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        filename = f"work_schedule_{start_date_obj.strftime('%Y%m%d')}_{end_date_obj.strftime('%Y%m%d')}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error exporting work schedules excel: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/work_schedules/import_excel', methods=['POST'])
@require_api_key
def import_work_schedules_excel():
    """
    Импорт графика из Excel-матрицы:
    - Row 1: ФИО, Ставка, даты...
    - Cells: "9*18", "10*16/30", "9*13,14*18" или "Выходной"
    """
    try:
        requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
        if not requester_id:
            return jsonify({"error": "Unauthorized"}), 401

        requester_id = int(requester_id)
        user_data = db.get_user(id=requester_id)
        if not user_data:
            return jsonify({"error": "User not found"}), 404
        if user_data[3] not in ['admin', 'sv']:
            return jsonify({"error": "Forbidden"}), 403

        file_storage = request.files.get('file')
        if not file_storage:
            return jsonify({"error": "file is required"}), 400

        file_name = secure_filename(file_storage.filename or 'schedule.xlsx')
        if not file_name.lower().endswith(('.xlsx', '.xlsm')):
            return jsonify({"error": "Only .xlsx/.xlsm files are supported"}), 400

        wb = load_workbook(filename=BytesIO(file_storage.read()), data_only=True)
        ws = wb.active
        if ws.max_row < 2 or ws.max_column < 3:
            return jsonify({"error": "Excel file is too small for schedule import"}), 400

        header_cells = list(ws[1])
        fio_col_idx = None
        for cell in header_cells:
            header_text = str(cell.value or '').strip().lower()
            if header_text == 'фио' or 'фио' in header_text:
                fio_col_idx = cell.column
                break
        if fio_col_idx is None:
            fio_col_idx = 1

        date_columns = []
        for cell in header_cells:
            parsed_date = _parse_schedule_excel_header_date(cell)
            if not parsed_date:
                continue
            date_columns.append((cell.column, parsed_date))

        if not date_columns:
            return jsonify({"error": "No date columns found in header row"}), 400

        date_columns.sort(key=lambda item: item[0])
        import_start = min(d for _, d in date_columns).strftime('%Y-%m-%d')
        import_end = max(d for _, d in date_columns).strftime('%Y-%m-%d')

        operators = db.get_operators_with_shifts(import_start, import_end) or []
        operator_exact_map = {}
        for op in operators:
            key = _normalize_schedule_excel_name_key(op.get('name'))
            if not key:
                continue
            operator_exact_map.setdefault(key, []).append(op)

        parsed_entries = []
        skipped_rows = 0
        skipped_empty_cells = 0
        unmatched_rows = []
        ambiguous_rows = []
        invalid_cells = []

        for row_idx in range(2, ws.max_row + 1):
            fio_value = ws.cell(row=row_idx, column=fio_col_idx).value
            fio_text = str(fio_value or '').strip()
            if not fio_text:
                continue

            op_match = None
            exact_candidates = operator_exact_map.get(_normalize_schedule_excel_name_key(fio_text), [])
            if len(exact_candidates) == 1:
                op_match = exact_candidates[0]
            elif len(exact_candidates) > 1:
                ambiguous_rows.append({'row': row_idx, 'name': fio_text, 'count': len(exact_candidates)})
                skipped_rows += 1
                continue
            else:
                # fallback на существующий fuzzy finder
                fuzzy = db.find_operator_by_name_fuzzy(fio_text)
                if fuzzy:
                    op_match = {
                        'id': fuzzy[0],
                        'name': fuzzy[2]
                    }

            if not op_match:
                unmatched_rows.append({'row': row_idx, 'name': fio_text})
                skipped_rows += 1
                continue

            operator_id = int(op_match['id'])
            for col_idx, day_obj in date_columns:
                raw_cell = ws.cell(row=row_idx, column=col_idx).value
                parsed_cell = _parse_schedule_excel_cell_value(raw_cell)
                kind = parsed_cell.get('kind')
                if kind == 'empty':
                    skipped_empty_cells += 1
                    continue
                if kind == 'invalid':
                    invalid_cells.append({
                        'row': row_idx,
                        'column': int(col_idx),
                        'date': day_obj.strftime('%Y-%m-%d'),
                        'name': fio_text,
                        'value': str(raw_cell or ''),
                        'error': parsed_cell.get('error') or 'Invalid cell format'
                    })
                    continue

                entry = {
                    'operator_id': operator_id,
                    'date': day_obj.strftime('%Y-%m-%d'),
                    'is_day_off': (kind == 'day_off'),
                    'shifts': []
                }
                if kind == 'shifts':
                    entry['shifts'] = parsed_cell.get('shifts') or []
                parsed_entries.append(entry)

        if not parsed_entries and not invalid_cells:
            return jsonify({
                "message": "No schedule cells found to import",
                "result": {
                    "days_processed": 0,
                    "set_day_off_days": 0,
                    "set_shift_days": 0,
                    "shift_rows_saved": 0,
                    "deleted_shift_rows": 0,
                    "deleted_day_off_rows": 0,
                    "affected_operator_ids": []
                },
                "warnings": {
                    "skipped_rows": skipped_rows,
                    "skipped_empty_cells": skipped_empty_cells,
                    "unmatched_rows": unmatched_rows,
                    "ambiguous_rows": ambiguous_rows,
                    "invalid_cells": invalid_cells
                }
            }), 200

        if not parsed_entries and invalid_cells:
            return jsonify({
                "message": "No valid schedule cells found to import",
                "result": {
                    "days_processed": 0,
                    "set_day_off_days": 0,
                    "set_shift_days": 0,
                    "shift_rows_saved": 0,
                    "deleted_shift_rows": 0,
                    "deleted_day_off_rows": 0,
                    "affected_operator_ids": []
                },
                "warnings": {
                    "skipped_rows": skipped_rows,
                    "skipped_empty_cells": skipped_empty_cells,
                    "unmatched_rows": unmatched_rows,
                    "ambiguous_rows": ambiguous_rows,
                    "invalid_cells": invalid_cells[:30],
                    "invalid_cells_total": len(invalid_cells)
                }
            }), 200

        break_direction_groups = []
        try:
            raw_groups = request.form.get('break_direction_groups')
            if raw_groups:
                parsed_groups = json.loads(raw_groups)
                if isinstance(parsed_groups, list):
                    break_direction_groups = parsed_groups
        except Exception:
            # Не валим импорт из-за битого optional-поля, просто игнорируем группы.
            break_direction_groups = []

        # Подготовка перерывов как во фронте: автогенерация + анти-пересечение между операторами
        # (по направлению / группе направлений) на симуляции перед сохранением.
        import_start_obj = datetime.strptime(import_start, '%Y-%m-%d').date()
        import_end_obj = datetime.strptime(import_end, '%Y-%m-%d').date()
        sim_start = (import_start_obj - timedelta(days=1)).strftime('%Y-%m-%d')
        sim_end = (import_end_obj + timedelta(days=1)).strftime('%Y-%m-%d')
        sim_source_operators = db.get_operators_with_shifts(sim_start, sim_end) or []
        sim_operators = _ws_clone_ops_for_break_simulation(sim_source_operators)
        sim_op_by_id = {int(op.get('id')): op for op in sim_operators if op.get('id') is not None}
        scope_resolver = _ws_make_direction_scope_resolver(break_direction_groups)

        for entry in parsed_entries:
            op_id = int(entry.get('operator_id'))
            date_str = str(entry.get('date'))
            sim_op = sim_op_by_id.get(op_id)
            if not sim_op:
                sim_op = {'id': op_id, 'direction': None, 'shifts': {}, 'daysOff': []}
                sim_operators.append(sim_op)
                sim_op_by_id[op_id] = sim_op

            sim_op['daysOff'] = [d for d in (sim_op.get('daysOff') or []) if str(d) != date_str]
            sim_op.setdefault('shifts', {})
            sim_op['shifts'].pop(date_str, None)

            if entry.get('is_day_off'):
                if date_str not in sim_op['daysOff']:
                    sim_op['daysOff'].append(date_str)
                entry['shifts'] = []
                continue

            prepared_shifts = []
            for raw_shift in (entry.get('shifts') or []):
                start_str = str(raw_shift.get('start') or raw_shift.get('start_time') or '').strip()
                end_str = str(raw_shift.get('end') or raw_shift.get('end_time') or '').strip()
                if not start_str or not end_str:
                    continue
                smin = _ws_time_to_minutes(start_str)
                emin = _ws_time_to_minutes(end_str)
                if emin <= smin:
                    emin += 1440
                prepared_shifts.append({
                    'start': _ws_minutes_to_time(smin),
                    'end': _ws_minutes_to_time(emin),
                    '__startMin': smin,
                    '__endMin': emin,
                    'breaks': _ws_compute_breaks_for_shift_minutes(smin, emin)
                })

            sim_op['shifts'][date_str] = prepared_shifts
            if prepared_shifts:
                _ws_adjust_breaks_for_operator_on_date(sim_op, date_str, sim_operators, scope_resolver)

            # Сохраняем в entry уже финальные перерывы после анти-пересечения
            final_shifts = []
            for seg in (sim_op.get('shifts', {}).get(date_str) or []):
                final_shifts.append({
                    'start': str(seg.get('start') or ''),
                    'end': str(seg.get('end') or ''),
                    'breaks': [
                        {'start': int(b.get('start')), 'end': int(b.get('end'))}
                        for b in (seg.get('breaks') or [])
                        if b is not None and int(b.get('end', 0)) > int(b.get('start', 0))
                    ]
                })
            entry['shifts'] = final_shifts

        result = db.import_work_schedule_excel_entries(parsed_entries)
        return jsonify({
            "message": "Work schedules imported from Excel successfully",
            "result": result,
            "breakAntiOverlapApplied": True,
            "warnings": {
                "skipped_rows": skipped_rows,
                "skipped_empty_cells": skipped_empty_cells,
                "unmatched_rows": unmatched_rows,
                "ambiguous_rows": ambiguous_rows,
                "invalid_cells": invalid_cells[:30],
                "invalid_cells_total": len(invalid_cells)
            }
        }), 200

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error importing work schedules excel: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


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


class sv_edit(StatesGroup):
    choose_action = State()
    edit_login = State()
    edit_password = State()
    edit_status = State()


class operator_edit(StatesGroup):
    choose_action = State()
    edit_login = State()
    edit_password = State()
    change_sv = State()

MAX_LOGIN_ATTEMPTS = 3

def get_access_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Сменить логин'))
    kb.insert(KeyboardButton('Сменить пароль'))
    kb.add(KeyboardButton('Выход🚪')) 
    kb.add(KeyboardButton('Назад 🔙'))
    return kb

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
    kb.insert(KeyboardButton('Редактировать СВ✏️'))
    kb.add(KeyboardButton('Назад 🔙'))
    return kb

def get_operators_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton('Добавить оператора👷‍♂️'))
    kb.insert(KeyboardButton('Редактировать Оператора✏️'))
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
    logging.info(f"User {message.from_user.id} canceled the operation.")
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.finish()
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        kb = get_admin_keyboard()
    elif user and user[3] == 'sv':
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
    elif user and user[3] == 'sv':
        await bot.send_message(
            chat_id=message.from_user.id,
            text=f'<b>Главное меню</b>',
            parse_mode='HTML',
            reply_markup=get_sv_keyboard()
        )
    elif user and user[3] == 'operator':
        await bot.send_message(
            chat_id=message.from_user.id,
            text=f'<b>Главное меню</b>',
            parse_mode='HTML',
            reply_markup=get_operator_keyboard()
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


@dp.message_handler(regexp='Редактировать СВ✏️')
async def editSv(message: types.Message):
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
        for sv_id, sv_name, *_ in supervisors:
            ikb.insert(InlineKeyboardButton(text=sv_name, callback_data=f"editsv_{sv_id}"))

        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите супервайзера для редактирования</b>",
            parse_mode='HTML',
            reply_markup=ikb
        )
    await message.delete()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editsv_'))
async def editSV_select(callback: types.CallbackQuery, state: FSMContext):
    try:
        sv_id = int(callback.data.split('_')[1])
        user = db.get_user(id=sv_id)
        if not user:
            await bot.answer_callback_query(callback.id, text="Супервайзер не найден")
            return

        ikb = InlineKeyboardMarkup(row_width=1)
        ikb.add(
            InlineKeyboardButton('Сменить логин', callback_data=f'edit_login_{sv_id}'),
            InlineKeyboardButton('Сменить пароль', callback_data=f'edit_pass_{sv_id}'),
        )
        ikb.add(InlineKeyboardButton('Изменить статус', callback_data=f'edit_status_{sv_id}'))
        ikb.add(InlineKeyboardButton('Отмена', callback_data='edit_cancel'))

        await bot.send_message(
            chat_id=callback.from_user.id,
            text=(f"<b>Супервайзер:</b> {user[2]}\n" 
                  f"<b>Текущий статус:</b> {user[9] if len(user) > 9 else 'unknown'}\n\n"
                  "Выберите действие:"),
            parse_mode='HTML',
            reply_markup=ikb
        )
    finally:
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('edit_login_'))
async def editSV_login_start(callback: types.CallbackQuery, state: FSMContext):
    sv_id = int(callback.data.split('_')[2])
    await state.update_data({'sv_edit_id': sv_id})
    await sv_edit.edit_login.set()
    await bot.send_message(chat_id=callback.from_user.id, text='Введите новый логин супервайзера:')
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.message_handler(state=sv_edit.edit_login)
async def process_sv_login_change(message: types.Message, state: FSMContext):
    data = await state.get_data()
    sv_id = data.get('sv_edit_id')
    new_login = message.text.strip()
    try:
        success = db.update_operator_login(sv_id, None, new_login)
        if success:
            await bot.send_message(chat_id=message.from_user.id, text=f'Логин супервайзера обновлён на: <code>{new_login}</code>', parse_mode='HTML', reply_markup=get_editor_keyboard())
        else:
            await bot.send_message(chat_id=message.from_user.id, text='Не удалось изменить логин супервайзера')
    except Exception as e:
        logging.error(f"Error updating supervisor login: {e}")
        await bot.send_message(chat_id=message.from_user.id, text='Ошибка при обновлении логина')
    finally:
        await state.finish()
        await message.delete()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('edit_pass_'))
async def editSV_pass_start(callback: types.CallbackQuery, state: FSMContext):
    sv_id = int(callback.data.split('_')[2])
    await state.update_data({'sv_edit_id': sv_id})
    await sv_edit.edit_password.set()
    await bot.send_message(chat_id=callback.from_user.id, text='Введите новый пароль супервайзера:')
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.message_handler(state=sv_edit.edit_password)
async def process_sv_password_change(message: types.Message, state: FSMContext):
    data = await state.get_data()
    sv_id = data.get('sv_edit_id')
    new_pass = message.text.strip()
    try:
        success = db.update_user_password(sv_id, new_pass)
        if success:
            await bot.send_message(chat_id=message.from_user.id, text='Пароль супервайзера успешно обновлён', reply_markup=get_editor_keyboard())
        else:
            await bot.send_message(chat_id=message.from_user.id, text='Не удалось изменить пароль супервайзера')
    except Exception as e:
        logging.error(f"Error updating supervisor password: {e}")
        await bot.send_message(chat_id=message.from_user.id, text='Ошибка при обновлении пароля')
    finally:
        await state.finish()
        await message.delete()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('edit_status_'))
async def editSV_status_menu(callback: types.CallbackQuery):
    sv_id = int(callback.data.split('_')[2])
    ikb = InlineKeyboardMarkup(row_width=2)
    ikb.add(
        InlineKeyboardButton('Работает', callback_data=f'edit4_status_set_{sv_id}_working'),
        InlineKeyboardButton('Уволен', callback_data=f'edit4_status_set_{sv_id}_fired')
    )
    ikb.add(InlineKeyboardButton('БС', callback_data=f'edit4_status_set_{sv_id}_unpaid_leave'))
    ikb.add(InlineKeyboardButton('Отмена', callback_data='edit_cancel'))
    await bot.send_message(chat_id=callback.from_user.id, text='Выберите новый статус для СВ:', reply_markup=ikb)
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('edit4_status_set_'))
async def editSV_status_set(callback: types.CallbackQuery):
    parts = callback.data.split('_')
    sv_id = int(parts[3])
    new_status = parts[4]+'_' + parts[5] if len(parts) > 5 else parts[4]
    try:
        requester = db.get_user(telegram_id=callback.from_user.id)
        changed_by = requester[0] if requester else None
        success = db.update_user(sv_id, 'status', new_status, changed_by=changed_by)
        if success:
            await bot.send_message(chat_id=callback.from_user.id, text=f'Статус супервайзера обновлён на: {new_status}', reply_markup=get_editor_keyboard())
        else:
            await bot.send_message(chat_id=callback.from_user.id, text='Не удалось изменить статус супервайзера')
    except Exception as e:
        logging.error(f"Error updating supervisor status: {e}")
        await bot.send_message(chat_id=callback.from_user.id, text='Ошибка при обновлении статуса')
    finally:
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data == 'edit_cancel')
async def editSV_cancel(callback: types.CallbackQuery):
    await bot.answer_callback_query(callback.id, text='Действие отменено')
    await bot.send_message(chat_id=callback.from_user.id, text='Отмена', reply_markup=get_editor_keyboard())
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)

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
        for sv_id, sv_name, _, _, _, status in supervisors:
            if status != 'fired':
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
    
    for op in operators:
        op_id = op.get('id')
        op_name = op.get('name')
        hours_data = db.get_hours_summary(op_id, current_month)
        if hours_data:
            hours = hours_data[0]
            regular_hours = round(hours.get('regular_hours', 0) or 0, 2)
            norm_hours = hours.get('norm_hours', 0) or 0
            percent_complete = 0
            if norm_hours > 0:
                percent_complete = round(regular_hours / norm_hours * 100, 1)
            message_text += (
                f"👤 <b>{op_name}</b>\n"
                f"   ⏱️ Часы работы: {regular_hours} из {norm_hours}\n"
                f"   📈 Процент выполнения: {percent_complete}%\n"
                f"   📚 Часы тренинга: {round(hours.get('training_hours', 0), 2)}\n"
                f"   💸 Штрафы: {hours.get('fines', 0)}\n\n"
            )
        else:
            message_text += f"👤 <b>{op_name}</b> - данные отсутствуют\n\n"
    logging.info(message_text)
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
                    chat_id=user[1],
                    parse_mode='HTML',
                    reply_markup=get_evaluations_keyboard()
                )
        ikb = InlineKeyboardMarkup(row_width=1)
        for sv_id, sv_name, _, _, _, status in supervisors:
            if status != 'fired':
                ikb.insert(InlineKeyboardButton(text=sv_name, callback_data=f"eval_{sv_id}"))
        
        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Лист СВ:</b>",
            parse_mode='HTML',
            reply_markup=ikb
        )
        await sv.view_evaluations.set()
    await message.delete()

@dp.message_handler(regexp='Редактировать Оператора✏️')
async def edit_operator_menu(message: types.Message):
    user = db.get_user(telegram_id=message.from_user.id)
    if user and user[3] == 'admin':
        supervisors = db.get_supervisors()
        if not supervisors:
            await bot.send_message(
                chat_id=message.from_user.id,
                text="<b>Нет доступных супервайзеров</b>",
                parse_mode='HTML',
                reply_markup=get_operators_keyboard()
            )
            return

        ikb = InlineKeyboardMarkup(row_width=1)
        for sv_id, sv_name, *_ in supervisors:
            ikb.insert(InlineKeyboardButton(text=sv_name, callback_data=f'editop_sv_{sv_id}'))

        await bot.send_message(
            chat_id=message.from_user.id,
            text="<b>Выберите СВ, чтобы показать его операторов</b>",
            parse_mode='HTML',
            reply_markup=ikb
        )
    await message.delete()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop_sv_'))
async def edit_operator_by_sv(callback: types.CallbackQuery, state: FSMContext):
    sv_id = int(callback.data.split('_')[2])
    sv = db.get_user(id=sv_id)
    if not sv or sv[3] != 'sv':
        await bot.answer_callback_query(callback.id, text='СВ не найден')
        return

    operators = db.get_operators_by_supervisor(sv_id)
    if not operators:
        await bot.send_message(chat_id=callback.from_user.id, text='У выбранного СВ нет операторов')
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)
        return

    ikb = InlineKeyboardMarkup(row_width=1)
    for op in operators:
        op_id = op.get('id')
        op_name = op.get('name')
        ikb.insert(InlineKeyboardButton(text=op_name, callback_data=f'editop1_{op_id}'))

    await bot.send_message(chat_id=callback.from_user.id, text=f"Операторы СВ: <b>{sv[2]}</b>", parse_mode='HTML', reply_markup=ikb)
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop1_'))
async def edit_operator_select(callback: types.CallbackQuery, state: FSMContext):
    op_id = int(callback.data.split('_')[1])
    user = db.get_user(id=op_id)
    if not user or user[3] != 'operator':
        await bot.answer_callback_query(callback.id, text='Оператор не найден')
        return

    ikb = InlineKeyboardMarkup(row_width=1)
    ikb.add(
        InlineKeyboardButton('Сменить логин', callback_data=f'editop_login_{op_id}'),
        InlineKeyboardButton('Сменить пароль', callback_data=f'editop_pass_{op_id}'),
        InlineKeyboardButton('Сменить ставку', callback_data=f'editop_rate_{op_id}'),
        InlineKeyboardButton('Изменить статус', callback_data=f'editop_status_{op_id}')
    )
    ikb.add(InlineKeyboardButton('Отмена', callback_data='editop_cancel'))

    await bot.send_message(chat_id=callback.from_user.id, text=(f"Оператор: <b>{user[2]}</b>\nВыберите действие:"), parse_mode='HTML', reply_markup=ikb)
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop_login_'))
async def edit_operator_login_start(callback: types.CallbackQuery, state: FSMContext):
    op_id = int(callback.data.split('_')[2])
    await state.update_data({'edit_op_id': op_id})
    await operator_edit.edit_login.set()
    await bot.send_message(chat_id=callback.from_user.id, text='Введите новый логин оператора:')
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.message_handler(state=operator_edit.edit_login)
async def process_operator_login_change(message: types.Message, state: FSMContext):
    data = await state.get_data()
    op_id = data.get('edit_op_id')
    new_login = message.text.strip()
    try:
        success = db.update_operator_login(op_id, None, new_login)
        if success:
            await bot.send_message(chat_id=message.from_user.id, text=f'Логин оператора обновлён на: <code>{new_login}</code>', parse_mode='HTML', reply_markup=get_operators_keyboard())
        else:
            await bot.send_message(chat_id=message.from_user.id, text='Не удалось изменить логин оператора')
    except Exception as e:
        logging.error(f"Error updating operator login: {e}")
        await bot.send_message(chat_id=message.from_user.id, text='Ошибка при обновлении логина')
    finally:
        await state.finish()
        await message.delete()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop_pass_'))
async def edit_operator_pass_start(callback: types.CallbackQuery, state: FSMContext):
    op_id = int(callback.data.split('_')[2])
    await state.update_data({'edit_op_id': op_id})
    await operator_edit.edit_password.set()
    await bot.send_message(chat_id=callback.from_user.id, text='Введите новый пароль оператора:')
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.message_handler(state=operator_edit.edit_password)
async def process_operator_password_change(message: types.Message, state: FSMContext):
    data = await state.get_data()
    op_id = data.get('edit_op_id')
    new_pass = message.text.strip()
    try:
        success = db.update_user_password(op_id, new_pass)
        if success:
            await bot.send_message(chat_id=message.from_user.id, text='Пароль оператора успешно обновлён', reply_markup=get_operators_keyboard())
        else:
            await bot.send_message(chat_id=message.from_user.id, text='Не удалось изменить пароль оператора')
    except Exception as e:
        logging.error(f"Error updating operator password: {e}")
        await bot.send_message(chat_id=message.from_user.id, text='Ошибка при обновлении пароля')
    finally:
        await state.finish()
        await message.delete()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop_rate_'))
async def edit_operator_rate_menu(callback: types.CallbackQuery):
    op_id = int(callback.data.split('_')[2])
    ikb = InlineKeyboardMarkup(row_width=3)
    ikb.add(
        InlineKeyboardButton('0.5', callback_data=f'editop2_rate_set_{op_id}_0.5'),
        InlineKeyboardButton('0.75', callback_data=f'editop2_rate_set_{op_id}_0.75'),
        InlineKeyboardButton('1', callback_data=f'editop2_rate_set_{op_id}_1')
    )
    ikb.add(InlineKeyboardButton('Отмена', callback_data='editop_cancel'))
    await bot.send_message(chat_id=callback.from_user.id, text='Выберите новую ставку для оператора:', reply_markup=ikb)
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop2_rate_set_'))
async def edit_operator_rate_set(callback: types.CallbackQuery):
    parts = callback.data.split('_')
    op_id = int(parts[3])
    rate = float(parts[4])
    try:
        requester = db.get_user(telegram_id=callback.from_user.id)
        changed_by = requester[0] if requester else None
        success = db.update_user(op_id, 'rate', rate, changed_by=changed_by)
        if success:
            await bot.send_message(chat_id=callback.from_user.id, text=f'Ставка оператора обновлена на {rate}', reply_markup=get_operators_keyboard())
        else:
            await bot.send_message(chat_id=callback.from_user.id, text='Не удалось изменить ставку оператора')
    except Exception as e:
        logging.error(f"Error updating operator rate: {e}")
        await bot.send_message(chat_id=callback.from_user.id, text='Ошибка при обновлении ставки')
    finally:
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop_status_'))
async def edit_operator_status_menu(callback: types.CallbackQuery):
    op_id = int(callback.data.split('_')[2])
    ikb = InlineKeyboardMarkup(row_width=2)
    ikb.add(
        InlineKeyboardButton('Работает', callback_data=f'editop2_status_set_{op_id}_working'),
        InlineKeyboardButton('Уволен', callback_data=f'editop2_status_set_{op_id}_fired')
    )
    ikb.add(InlineKeyboardButton('БС', callback_data=f'editop2_status_set_{op_id}_unpaid_leave'))
    ikb.add(InlineKeyboardButton('Отмена', callback_data='editop_cancel'))
    await bot.send_message(chat_id=callback.from_user.id, text='Выберите новый статус для оператора:', reply_markup=ikb)
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop2_status_set_'))
async def edit_operator_status_set(callback: types.CallbackQuery):
    parts = callback.data.split('_')
    op_id = int(parts[3])
    new_status = parts[4]+'_' + parts[5] if len(parts) > 5 else parts[4]
    try:
        requester = db.get_user(telegram_id=callback.from_user.id)
        changed_by = requester[0] if requester else None
        success = db.update_user(op_id, 'status', new_status, changed_by=changed_by)
        if success:
            await bot.send_message(chat_id=callback.from_user.id, text=f'Статус оператора обновлён на: {new_status}', reply_markup=get_operators_keyboard())
        else:
            await bot.send_message(chat_id=callback.from_user.id, text='Не удалось изменить статус оператора')
    except Exception as e:
        logging.error(f"Error updating operator status: {e}")
        await bot.send_message(chat_id=callback.from_user.id, text='Ошибка при обновлении статуса')
    finally:
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop_change_sv_'))
async def edit_operator_change_sv_start(callback: types.CallbackQuery, state: FSMContext):
    op_id = int(callback.data.split('_')[2])
    supervisors = db.get_supervisors()
    if not supervisors:
        await bot.answer_callback_query(callback.id, text='Нет доступных СВ')
        return

    ikb = InlineKeyboardMarkup(row_width=1)
    for sv_id, sv_name, *_ in supervisors:
        ikb.insert(InlineKeyboardButton(text=sv_name, callback_data=f'editop_setsv_{op_id}_{sv_id}'))

    ikb.add(InlineKeyboardButton('Отмена', callback_data='editop_cancel'))
    await bot.send_message(chat_id=callback.from_user.id, text='Выберите нового СВ для оператора:', reply_markup=ikb)
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith('editop_setsv_'))
async def edit_operator_set_sv(callback: types.CallbackQuery):
    parts = callback.data.split('_')
    op_id = int(parts[2])
    sv_id = int(parts[3])
    try:
        requester = db.get_user(telegram_id=callback.from_user.id)
        changed_by = requester[0] if requester else None
        success = db.update_user(op_id, 'supervisor_id', sv_id, changed_by=changed_by)
        if success:
            await bot.send_message(chat_id=callback.from_user.id, text='Супервайзер оператора успешно обновлён', reply_markup=get_operators_keyboard())
        else:
            await bot.send_message(chat_id=callback.from_user.id, text='Не удалось сменить супервайзера')
    except Exception as e:
        logging.error(f"Error setting operator supervisor: {e}")
        await bot.send_message(chat_id=callback.from_user.id, text='Ошибка при смене СВ')
    finally:
        await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.callback_query_handler(lambda c: c.data in ['editop_cancel'])
async def editop_cancel(callback: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback.id, text='Действие отменено')
    await bot.send_message(chat_id=callback.from_user.id, text='Отмена', reply_markup=get_operators_keyboard())
    await bot.delete_message(chat_id=callback.from_user.id, message_id=callback.message.message_id)


@dp.message_handler(regexp='Отчет за месяц📅')
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
    
    for op in operators:
        # Для каждого оператора получаем статистику звонков
        if op.get('status') == 'fired':
            continue
        op_id = op.get('id')
        op_name = op.get('name')
        with db._get_cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*), AVG(score) 
                FROM calls 
                WHERE operator_id = %s AND month=%s
            """, (op_id, datetime.now().strftime('%Y-%m')))
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

async def _is_admin_user(tg_id):
    """Проверка админа: сначала сравнение с переменной admin, затем fallback — проверка в БД."""
    try:
        if os.getenv("ADMIN_ID") is not None and str(tg_id) == str(os.getenv("ADMIN_ID")):
            return True

        # fallback: проверить в users по telegram_id и role='admin'
        loop = asyncio.get_event_loop()
        def _check():
            with db._get_cursor() as cur:
                cur.execute("SELECT role FROM users WHERE telegram_id = %s LIMIT 1", (tg_id,))
                row = cur.fetchone()
                return bool(row and row[0] == 'admin')
        return await loop.run_in_executor(None, _check)
    except Exception as e:
        logging.exception("Error checking admin: %s", e)
        return False

async def _approve_call_and_get_notify_row(call_id, approver_tg_id):
    """
    Пометить звонок одобренным и вернуть данные для уведомлений:
    (sv_request_by, sv_tg, evaluator_id, eval_tg)
    """
    loop = asyncio.get_event_loop()

    def _update():
        with db._get_cursor() as cur:
            # получить internal user id админа (если есть)
            cur.execute("SELECT id FROM users WHERE telegram_id = %s LIMIT 1", (approver_tg_id,))
            row = cur.fetchone()
            approver_user_id = row[0] if row else None

            cur.execute("""
                UPDATE calls
                SET sv_request_approved = TRUE,
                    sv_request_approved_by = %s,
                    sv_request_approved_at = %s
                WHERE id = %s
            """, (approver_user_id, datetime.utcnow(), call_id))

            cur.execute("""
                SELECT c.sv_request_by, u_super.telegram_id AS sv_tg, c.evaluator_id, u_eval.telegram_id AS eval_tg
                FROM calls c
                LEFT JOIN users u_super ON u_super.id = c.sv_request_by
                LEFT JOIN users u_eval ON u_eval.id = c.evaluator_id
                WHERE c.id = %s
            """, (call_id,))
            return cur.fetchone()

    result = await loop.run_in_executor(None, _update)
    return result  # None или кортеж (sv_request_by, sv_tg, evaluator_id, eval_tg)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('approve_reval:'))
async def handle_approve_reval(callback_query: types.CallbackQuery):
    """
    Обработка approve_reval:{call_id}.
    Требования:
      - admin (telegram id) доступен в переменной `admin`.
      - db доступен и синхронен (используем run_in_executor).
    """
    cq = callback_query
    user = cq.from_user
    data = cq.data

    # извлечь call_id
    try:
        _, call_id_str = data.split(':', 1)
        call_id = int(call_id_str)
    except Exception:
        await cq.answer("Неверный идентификатор звонка", show_alert=True)
        return

    # проверить, что нажал админ
    try:
        is_admin = await _is_admin_user(user.id)
    except Exception as e:
        logging.exception("Ошибка проверки администратора: %s", e)
        is_admin = False

    if not is_admin:
        await cq.answer("Только администратор может одобрять переоценку", show_alert=True)
        return

    # пометим звонок как одобренный и получим данные для уведомлений
    try:
        notify_row = await _approve_call_and_get_notify_row(call_id, user.id)
    except Exception as e:
        logging.exception("Ошибка при пометке звонка: %s", e)
        await cq.answer("Ошибка сервера при одобрении", show_alert=True)
        return

    # ответ на callback (чтобы кнопка показала результат)
    try:
        await cq.answer("Переоценка одобрена администратором", show_alert=False)
    except Exception as e:
        logging.debug("Не удалось послать answerCallbackQuery: %s", e)

    # убрать inline-кнопку (если возможно)
    try:
        if cq.message:
            await cq.bot.edit_message_reply_markup(chat_id=cq.message.chat.id, message_id=cq.message.message_id, reply_markup=None)
    except Exception as e:
        logging.debug("Не удалось удалить reply_markup: %s", e)

    # уведомить супервайзера и оценивающего (если есть tg id)
    if notify_row:
        try:
            sv_request_by, sv_tg, evaluator_id, eval_tg = notify_row
            if sv_tg:
                try:
                    await cq.bot.send_message(int(sv_tg),
                        f"✅ Ваша заявка на переоценку для Call ID {call_id} одобрена администратором. Можете инициировать переоценку.")
                except Exception as e:
                    logging.debug("Не удалось уведомить супервайзера: %s", e)
            if eval_tg:
                try:
                    await cq.bot.send_message(int(eval_tg),
                        f"ℹ️ Админ одобрил запрос на переоценку для Call ID {call_id}. Для переоценки создайте новую оценку с is_correction=true и previous_version_id={call_id}.")
                except Exception as e:
                    logging.debug("Не удалось уведомить оценивающего: %s", e)
        except Exception as e:
            logging.exception("Ошибка при отправке уведомлений после approve: %s", e)

    # (опционально) логирование
    logging.info("Call %s approved by tg_user %s", call_id, user.id)


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
            f"⏱ <b>Общие часы работы:</b> {stats['regular_hours']+stats['training_hours']} из {stats['norm_hours']} ({stats['percent_complete']}%)\n"
            f"📚 <b>Часы тренинга:</b> {stats['training_hours']}\n"
            f"📞 <b>Количество звонков в час:</b> {stats['calls_per_hour']}\n"
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
        evaluations = db.get_call_evaluations(user[0], month=datetime.now().strftime('%Y-%m'))
        
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
            masked_phone = eval.get('phone_number_masked') or _mask_phone_number(eval.get('phone_number'))
            message_text += (
                f"📞 <b>Звонок {eval['id']}{correction_mark}</b>\n"
                f"   📅 {eval['month']}\n"
                f"   📱 {masked_phone}\n"
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
        
        for sv_id, sv_name, _, _, _, _ in svs:
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

def sync_schedule_statuses_to_user_statuses_job():
    """Background job: sync users.status with active schedule status periods."""
    try:
        updated = db.sync_user_statuses_from_schedule_periods()
        if updated:
            logging.info("Auto status sync updated %s operator statuses", updated)
    except Exception as e:
        logging.exception("Error in auto status sync job: %s", e)

# === Главный запуск =============================================================================================
if __name__ == '__main__':
    # Инициализация администратора
    if not db.get_user(name='Мулдир Юсупова'):
        db.create_user(
            telegram_id=admin4,
            name='Мулдир Юсупова',
            role='admin',
            rate=1.0,
            login=ADMIN_LOGIN_CD,
            password=ADMIN_PASSWORD_CD
        )
        logging.info("Admin CD created")
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Настраиваем и запускаем планировщик
    async def monthly_auto_fill_norm():
        """Coroutine executed on the 1st day of each month to auto-fill norm_hours."""
        try:
            month = datetime.now().strftime('%Y-%m')
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(executor_pool, db.auto_fill_norm_hours, month)
            logging.info(f"auto_fill_norm_hours result: {result}")
        except Exception as e:
            logging.exception(f"Error running monthly_auto_fill_norm: {e}")

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        generate_weekly_report, 
        CronTrigger(day_of_week='mon', hour=9, minute=0),
        misfire_grace_time=3600
    )

    # Запуск авто-заполнения нормы часов: 1-й день каждого месяца в 03:00
    scheduler.add_job(
        monthly_auto_fill_norm,
        CronTrigger(day='1', hour=3, minute=0),
        misfire_grace_time=3600
    )

    # Автопереключение статусов операторов по периодным статусам графика (ежедневно в полночь)
    scheduler.add_job(
        sync_schedule_statuses_to_user_statuses_job,
        CronTrigger(hour=0, minute=0),
        id='sync_schedule_statuses_to_user_statuses',
        misfire_grace_time=120,
        max_instances=1,
        coalesce=True
    )

    scheduler.start()

    # Стартовый прогон (не ждём первой минуты)
    try:
        sync_schedule_statuses_to_user_statuses_job()
    except Exception:
        logging.exception("Initial auto status sync failed")
    
    logging.info("🔄 Планировщик запущен")
    logging.info("🤖 Бот запущен")
    
    # Запускаем бота
    executor.start_polling(dp, skip_updates=True)

