import os
import logging
import psycopg2
from contextlib import contextmanager
from passlib.hash import pbkdf2_sha256
from datetime import datetime, timedelta, date, time as dt_time
import time
import uuid
import requests
import openpyxl
import re
import json
import csv
import io
from io import BytesIO
import asyncio
from concurrent.futures import ThreadPoolExecutor
import gc
from psycopg2.pool import ThreadedConnectionPool  # Added for connection pooling
import calendar
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Border, Side, Alignment
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.styles.fills import Fill
from collections import defaultdict
import pandas as pd
from typing import List, Dict, Any, Tuple, Optional

logging.basicConfig(level=logging.INFO)

os.environ['TZ'] = 'Asia/Almaty'
time.tzset()

# Global connection pool
MIN_CONN = 1
MAX_CONN = 20  # Adjust based on expected load
POOL = None

# Вставьте/адаптируйте этот helper в ваш модуль
def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r'\D', '', str(phone))
    if not digits:
        return None
    # Оставим, например, последние 10-11 цифр (зависит от вашей логики)
    return digits

def _parse_datetime_raw(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    # ожидаемый формат 'dd.mm.yyyy hh:mm:ss'
    formats = ['%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M']  # запасные форматы
    for fmt in formats:
        try:
            # возвращаем timezone-naive datetime; при вставке используем AT TIME ZONE 'Asia/Almaty' или локаль
            return datetime.strptime(dt_str, fmt)
        except Exception:
            continue
    return None

def _time_to_minutes(time_str: str) -> int:
    """Преобразует время в формате 'HH:MM' в минуты от начала дня."""
    try:
        parts = time_str.split(':')
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        return hours * 60 + minutes
    except (ValueError, AttributeError):
        return 0

def _minutes_to_time(minutes: int) -> str:
    """Преобразует минуты от начала дня в формат 'HH:MM'."""
    minutes = minutes % (24 * 60)  # нормализация для смен через полночь
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"

def _merge_shifts_for_date(shifts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Объединяет перекрывающиеся смены на одну дату.
    shifts: список словарей с ключами 'start', 'end', 'breaks', 'id'
    Возвращает список объединенных смен.
    """
    if not shifts or len(shifts) == 0:
        return []
    
    # Преобразуем смены в интервалы в минутах
    intervals = []
    for shift in shifts:
        start_min = _time_to_minutes(shift.get('start', '00:00'))
        end_min = _time_to_minutes(shift.get('end', '00:00'))
        
        # Обработка смен через полночь
        if end_min <= start_min:
            end_min += 24 * 60
        
        intervals.append({
            'start': start_min,
            'end': end_min,
            'original': shift,
            'breaks': shift.get('breaks', [])
        })
    
    # Сортируем по времени начала
    intervals.sort(key=lambda x: x['start'])
    
    # Объединяем перекрывающиеся интервалы
    merged = []
    for interval in intervals:
        if not merged:
            merged.append(interval.copy())
        else:
            last = merged[-1]
            # Проверяем перекрытие: если начало новой смены <= конца последней
            if interval['start'] <= last['end']:
                # Объединяем: расширяем конец до максимума
                last['end'] = max(last['end'], interval['end'])
                # Объединяем перерывы
                last['breaks'].extend(interval['breaks'])
                # Сохраняем ID оригинальных смен для удаления старых
                if 'original_ids' not in last:
                    last['original_ids'] = [last['original'].get('id')]
                if interval['original'].get('id'):
                    last['original_ids'].append(interval['original'].get('id'))
            else:
                merged.append(interval.copy())
    
    # Преобразуем обратно в формат смен
    result = []
    for m in merged:
        start_time = _minutes_to_time(m['start'])
        end_time = _minutes_to_time(m['end'] % (24 * 60))
        
        # Объединяем и нормализуем перерывы (убираем дубликаты, сортируем)
        breaks = m.get('breaks', [])
        # Если перерывы в формате {'start': minutes, 'end': minutes}, оставляем как есть
        # Если в формате {'start': 'HH:MM', 'end': 'HH:MM'}, преобразуем
        normalized_breaks = []
        seen_breaks = set()
        for b in breaks:
            if isinstance(b, dict):
                start_br = b.get('start')
                end_br = b.get('end')
                # Если это строки времени, преобразуем в минуты
                if isinstance(start_br, str):
                    start_br_min = _time_to_minutes(start_br)
                else:
                    start_br_min = int(start_br) if start_br is not None else 0
                
                if isinstance(end_br, str):
                    end_br_min = _time_to_minutes(end_br)
                else:
                    end_br_min = int(end_br) if end_br is not None else 0
                
                break_key = (start_br_min, end_br_min)
                if break_key not in seen_breaks:
                    seen_breaks.add(break_key)
                    normalized_breaks.append({
                        'start': start_br_min,
                        'end': end_br_min
                    })
        
        # Сортируем перерывы по началу
        normalized_breaks.sort(key=lambda x: x['start'])
        
        result.append({
            'start': start_time,
            'end': end_time,
            'breaks': normalized_breaks
        })
    
    return result

SCHEDULE_SPECIAL_STATUS_META: Dict[str, Dict[str, str]] = {
    'bs': {
        'label': 'Б/С',
        'kind': 'absence'
    },
    'sick_leave': {
        'label': 'Больничный',
        'kind': 'absence'
    },
    'annual_leave': {
        'label': 'Ежегодный отпуск',
        'kind': 'absence'
    },
    'dismissal': {
        'label': 'Увольнение',
        'kind': 'dismissal'
    }
}

SCHEDULE_DISMISSAL_REASONS: List[str] = [
    'Б/С на летний период',
    'Мошенничество',
    'Нарушение дисциплины',
    'не может совмещать с учебой',
    'не может совмещать с работой',
    'не нравится работа',
    'выгорание',
    'не устраивает доход',
    'перевод в другой отдел',
    'переезд',
    'по состоянию здоровья',
    'пропал',
    'слабый/не выполняет kpi'
]

def get_pool():
    global POOL
    if POOL is None:
        conn_params = {
            'dbname': os.getenv('POSTGRES_DB'),
            'user': os.getenv('POSTGRES_USER'),
            'password': os.getenv('POSTGRES_PASSWORD'),
            'host': os.getenv('POSTGRES_HOST'),
            'port': os.getenv('POSTGRES_PORT', 5432)
        }
        POOL = ThreadedConnectionPool(MIN_CONN, MAX_CONN, **conn_params)
    return POOL

class Database:
    def __init__(self):
        self._init_db()

    @contextmanager
    def _get_connection(self):
        pool = get_pool()
        conn = pool.getconn()
        try:
            yield conn
        finally:
            pool.putconn(conn)

    @contextmanager
    def _get_cursor(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise e
            finally:
                cursor.close()

    def _init_db(self):
        with self._get_cursor() as cursor:
            # Users table (без direction_id на этом этапе)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE,
                    name VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL CHECK(role IN ('admin', 'sv', 'operator')),
                    hire_date DATE,
                    login VARCHAR(255) UNIQUE,
                    password_hash VARCHAR(255),
                    supervisor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    hours_table_url TEXT,
                    scores_table_url TEXT,
                    is_active BOOLEAN NOT NULL DEFAULT FALSE,
                    status VARCHAR(20) NOT NULL DEFAULT 'working' CHECK(status IN ('working', 'fired', 'unpaid_leave')),
                    rate DECIMAL(3,2) NOT NULL DEFAULT 1.00 CHECK(rate IN (1.00, 0.75, 0.50)),
                    CONSTRAINT unique_name_role UNIQUE (name, role)
                );
            """)
            # Directions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS directions (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    has_file_upload BOOLEAN NOT NULL DEFAULT TRUE,
                    criteria JSONB NOT NULL DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    version INTEGER NOT NULL DEFAULT 1,
                    previous_version_id INTEGER REFERENCES directions(id)
                );
            """)

            # Добавляем direction_id в users после создания directions
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'users' AND column_name = 'direction_id'
                    ) THEN
                        ALTER TABLE users ADD COLUMN direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL;
                    END IF;
                END $$;
            """)
            cursor.execute("""
                ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS gender VARCHAR(20) CHECK (gender IN ('male', 'female')),
                    ADD COLUMN IF NOT EXISTS birth_date DATE;
            """)
            # Calls table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_activity_logs (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    is_active VARCHAR(20) NOT NULL,
                    change_time TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calls (
                    id SERIAL PRIMARY KEY,
                    evaluator_id INTEGER NOT NULL REFERENCES users(id),
                    operator_id INTEGER NOT NULL REFERENCES users(id),
                    month VARCHAR(7) NOT NULL DEFAULT TO_CHAR(CURRENT_DATE, 'YYYY-MM'),
                    phone_number VARCHAR(255) NOT NULL,
                    appeal_date TIMESTAMP,
                    score FLOAT NOT NULL,
                    comment TEXT,
                    audio_path TEXT,
                    is_draft BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_correction BOOLEAN DEFAULT FALSE,
                    previous_version_id INTEGER REFERENCES calls(id),
                    scores JSONB,
                    criterion_comments JSONB,
                    direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL,
                    UNIQUE(evaluator_id, operator_id, month, phone_number, score, comment, is_draft)
                );
                ALTER TABLE calls
                    ADD COLUMN IF NOT EXISTS sv_request BOOLEAN NOT NULL DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS sv_request_comment TEXT,
                    ADD COLUMN IF NOT EXISTS sv_request_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    ADD COLUMN IF NOT EXISTS sv_request_at TIMESTAMP,
                    ADD COLUMN IF NOT EXISTS sv_request_approved BOOLEAN NOT NULL DEFAULT FALSE,
                    ADD COLUMN IF NOT EXISTS sv_request_approved_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    ADD COLUMN IF NOT EXISTS sv_request_approved_at TIMESTAMP;
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS imported_calls (
                    id SERIAL PRIMARY KEY,
                    external_id TEXT,                      -- id из JSON (можно UUID или любой string)
                    operator_name TEXT NOT NULL,           -- ФИО из JSON
                    operator_id INTEGER,                   -- resolved users.id (NULL допустим)
                    month VARCHAR(7) NOT NULL,             -- format 'YYYY-MM'
                    datetime_raw TIMESTAMP WITH TIME ZONE, -- parsed datetime (store with timezone)
                    phone_number TEXT,
                    phone_normalized TEXT,
                    duration_sec DOUBLE PRECISION,
                    desired INTEGER,
                    available INTEGER,
                    status VARCHAR(20) NOT NULL DEFAULT 'not_evaluated', -- not_evaluated / evaluated / skipped
                    imported_at TIMESTAMP WITH TIME ZONE DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    evaluated_at TIMESTAMP WITH TIME ZONE,
                    evaluated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    notes TEXT
                );

                ALTER TABLE imported_calls ADD COLUMN id_int SERIAL;
                ALTER TABLE imported_calls DROP CONSTRAINT imported_calls_pkey;
                ALTER TABLE imported_calls DROP COLUMN id;
                ALTER TABLE imported_calls RENAME COLUMN id_int TO id;
                ALTER TABLE imported_calls ADD PRIMARY KEY (id);
                CREATE UNIQUE INDEX IF NOT EXISTS uq_imported_calls_external_id_month ON imported_calls (external_id, month);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_month ON imported_calls(month);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_operator_id ON imported_calls(operator_id);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_status ON imported_calls(status);
                CREATE INDEX IF NOT EXISTS idx_imported_calls_phone_normalized ON imported_calls(phone_normalized);
            """)


            #Trainings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trainings (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    training_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    reason VARCHAR(255) NOT NULL CHECK(reason IN (
                        'Обратная связь', 'Собрание', 'Тех. сбой', 'Мотивационная беседа', 
                        'Дисциплинарный тренинг', 'Тренинг по качеству. Разбор ошибок', 
                        'Тренинг по качеству. Объяснение МШ', 'Тренинг по продукту', 
                        'Мониторинг', 'Практика в офисе таксопарка', 'Другое'
                    )),
                    comment TEXT,
                    created_by INTEGER REFERENCES users(id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    count_in_hours BOOLEAN NOT NULL DEFAULT TRUE,
                    UNIQUE(operator_id, training_date, start_time, end_time)
                );
            """)
            
            # Work hours table with fines column
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_hours (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    operator_id INTEGER NOT NULL REFERENCES users(id),
                    month VARCHAR(7) NOT NULL DEFAULT TO_CHAR(CURRENT_DATE, 'YYYY-MM'),
                    regular_hours FLOAT NOT NULL DEFAULT 0,    
                    training_hours FLOAT NOT NULL DEFAULT 0,
                    fines FLOAT NOT NULL DEFAULT 0,
                    norm_hours FLOAT NOT NULL DEFAULT 0,
                    total_break_time FLOAT NOT NULL DEFAULT 0,
                    total_talk_time FLOAT NOT NULL DEFAULT 0,
                    total_calls INTEGER NOT NULL DEFAULT 0,
                    total_efficiency_hours FLOAT NOT NULL DEFAULT 0, 
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    calls_per_hour FLOAT NOT NULL DEFAULT 0.0,
                    UNIQUE(operator_id, month)
                );
            """)

            cursor.execute("""
                CREATE EXTENSION IF NOT EXISTS pgcrypto;
                CREATE TABLE IF NOT EXISTS daily_hours (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day DATE NOT NULL,
                    work_time FLOAT NOT NULL DEFAULT 0,   -- часы работы
                    break_time FLOAT NOT NULL DEFAULT 0,
                    talk_time FLOAT NOT NULL DEFAULT 0,
                    calls INTEGER NOT NULL DEFAULT 0,
                    efficiency FLOAT NOT NULL DEFAULT 0,  -- часы
                    fine_amount FLOAT NOT NULL DEFAULT 0,
                    fine_reason VARCHAR(64),
                    fine_comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, day)
                );
            """)

            # Table for multiple fines per day (new schema)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_fines (
                    id SERIAL PRIMARY KEY,
                    daily_hours_id UUID REFERENCES daily_hours(id) ON DELETE CASCADE,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day DATE NOT NULL,
                    amount FLOAT NOT NULL DEFAULT 0,
                    reason VARCHAR(64),
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    changed_by INTEGER REFERENCES users(id),  -- Who made the change (e.g., admin or sv ID)
                    field_changed VARCHAR(50) NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    changed_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    session_id UUID PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    refresh_token_hash TEXT NOT NULL,
                    user_agent TEXT,
                    ip_address VARCHAR(64),
                    created_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    last_seen_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                    expires_at TIMESTAMP NOT NULL,
                    revoked_at TIMESTAMP,
                    sensitive_data_unlocked BOOLEAN NOT NULL DEFAULT FALSE,
                    sensitive_data_unlocked_at TIMESTAMP
                );
            """)
            cursor.execute("""
                ALTER TABLE user_sessions
                ADD COLUMN IF NOT EXISTS sensitive_data_unlocked BOOLEAN NOT NULL DEFAULT FALSE;
            """)
            cursor.execute("""
                ALTER TABLE user_sessions
                ADD COLUMN IF NOT EXISTS sensitive_data_unlocked_at TIMESTAMP;
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id
                ON user_sessions(user_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_sessions_expires_at
                ON user_sessions(expires_at);
            """)
            # Work schedules (shifts) table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_shifts (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    shift_date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, shift_date, start_time, end_time)
                );
            """)

            # Break periods within shifts
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shift_breaks (
                    id SERIAL PRIMARY KEY,
                    shift_id INTEGER NOT NULL REFERENCES work_shifts(id) ON DELETE CASCADE,
                    start_minutes INTEGER NOT NULL,
                    end_minutes INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Days off table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS days_off (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    day_off_date DATE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, day_off_date)
                );
            """)
            # Special statuses by period (vacation/sick leave/unpaid leave/dismissal)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_schedule_status_periods (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    status_code VARCHAR(32) NOT NULL,
                    start_date DATE NOT NULL,
                    end_date DATE NULL,
                    dismissal_reason TEXT NULL,
                    comment TEXT NULL,
                    created_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_shift_breaks_shift_id
                ON shift_breaks(shift_id);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_op_sched_status_periods_operator_start
                ON operator_schedule_status_periods(operator_id, start_date);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_op_sched_status_periods_operator_end
                ON operator_schedule_status_periods(operator_id, end_date);
            """)

            # AI feedback cache table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ai_feedback_cache (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    month VARCHAR(7) NOT NULL,
                    feedback_data JSONB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, month)
                );
            """)

            # Optimized Indexes (added more based on query patterns)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_calls_month ON calls(month);
                CREATE INDEX IF NOT EXISTS idx_calls_operator_id ON calls(operator_id);
                CREATE INDEX IF NOT EXISTS idx_calls_operator_month ON calls(operator_id, month);
                CREATE INDEX IF NOT EXISTS idx_calls_phone_number ON calls(phone_number);
                CREATE INDEX IF NOT EXISTS idx_calls_created_at ON calls(created_at);
                CREATE INDEX IF NOT EXISTS idx_calls_evaluator_id ON calls(evaluator_id);
                CREATE INDEX IF NOT EXISTS idx_calls_is_draft ON calls(is_draft);
                CREATE INDEX IF NOT EXISTS idx_work_hours_operator_id ON work_hours(operator_id);
                CREATE INDEX IF NOT EXISTS idx_work_hours_month ON work_hours(month);
                CREATE INDEX IF NOT EXISTS idx_directions_name ON directions(name);
                CREATE INDEX IF NOT EXISTS idx_directions_is_active ON directions(is_active);
                CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
                CREATE INDEX IF NOT EXISTS idx_users_supervisor_id ON users(supervisor_id);
                CREATE INDEX IF NOT EXISTS idx_users_login ON users(login);
                CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);
                CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
                CREATE INDEX IF NOT EXISTS idx_users_rate ON users(rate);
                CREATE INDEX IF NOT EXISTS idx_user_history_user_id ON user_history(user_id);
                CREATE INDEX IF NOT EXISTS idx_user_history_changed_at ON user_history(changed_at);
                CREATE INDEX IF NOT EXISTS idx_work_hours_calls_per_hour ON work_hours(calls_per_hour);
                CREATE INDEX IF NOT EXISTS idx_trainings_operator_id ON trainings(operator_id);
                CREATE INDEX IF NOT EXISTS idx_trainings_training_date ON trainings(training_date);
                CREATE INDEX IF NOT EXISTS idx_work_shifts_operator_date ON work_shifts(operator_id, shift_date);
                CREATE INDEX IF NOT EXISTS idx_work_shifts_date ON work_shifts(shift_date);
                CREATE INDEX IF NOT EXISTS idx_days_off_operator_date ON days_off(operator_id, day_off_date);
                CREATE INDEX IF NOT EXISTS idx_ai_feedback_cache_operator_month ON ai_feedback_cache(operator_id, month);
                
                    -- Table for Baiga daily scores (points per operator per day)
                    CREATE TABLE IF NOT EXISTS baiga_daily_scores (
                        id SERIAL PRIMARY KEY,
                        operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        day DATE NOT NULL,
                        points INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(operator_id, day)
                    );

                    CREATE INDEX IF NOT EXISTS idx_baiga_daily_scores_day ON baiga_daily_scores(day);
                    CREATE INDEX IF NOT EXISTS idx_baiga_daily_scores_operator ON baiga_daily_scores(operator_id);
            """)

    def create_user(self, telegram_id, name, role, direction_id=None, rate=None, hire_date=None, supervisor_id=None, login=None, password=None, hours_table_url=None, gender=None, birth_date=None):
        if login is None:
            base_login = f"user_{str(uuid.uuid4())[:8]}"
            with self._get_cursor() as cursor:
                while True:
                    cursor.execute("SELECT id FROM users WHERE login = %s", (base_login,))
                    if not cursor.fetchone():
                        login = base_login
                        break
                    base_login = f"user_{str(uuid.uuid4())[:8]}"
        else:
            login = login or str(telegram_id)

        password = password or "123456"
        password_hash = pbkdf2_sha256.hash(password)

        with self._get_cursor() as cursor:
            cursor.execute("SAVEPOINT before_insert")
            try:
                cursor.execute("""
                    INSERT INTO users (telegram_id, name, role, direction_id, rate, hire_date, supervisor_id, login, password_hash, hours_table_url, gender, birth_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (telegram_id, name, role, direction_id, rate, hire_date, supervisor_id, login, password_hash, hours_table_url, gender, birth_date))
                return cursor.fetchone()[0]
            except psycopg2.IntegrityError as e:
                cursor.execute("ROLLBACK TO SAVEPOINT before_insert")
                if 'unique_name_role' in str(e):
                    cursor.execute("""
                        UPDATE users
                        SET direction_id = COALESCE(%s, direction_id),
                            supervisor_id = COALESCE(%s, supervisor_id),
                            hours_table_url = COALESCE(%s, hours_table_url),
                            gender = COALESCE(%s, gender),
                            birth_date = COALESCE(%s, birth_date)
                        WHERE name = %s AND role = %s
                        RETURNING id
                    """, (direction_id, supervisor_id, hours_table_url, gender, birth_date, name, role))
                    result = cursor.fetchone()
                    if result:
                        return result[0]
                    else:
                        raise ValueError("User with this name and role not found")
                elif 'telegram_id' in str(e):
                    cursor.execute("""
                        UPDATE users
                        SET name = %s,
                            role = %s,
                            direction_id = COALESCE(%s, direction_id),
                            hire_date = COALESCE(%s, hire_date),
                            supervisor_id = COALESCE(%s, supervisor_id),
                            login = %s,
                            password_hash = %s,
                            hours_table_url = COALESCE(%s, hours_table_url),
                            gender = COALESCE(%s, gender),
                            birth_date = COALESCE(%s, birth_date)
                        WHERE telegram_id = %s
                        RETURNING id
                    """, (name, role, direction_id, hire_date, supervisor_id, login, password_hash, hours_table_url, gender, birth_date, telegram_id))
                    return cursor.fetchone()[0]
                else:
                    raise

    def update_telegram_id(self, user_id, telegram_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users 
                SET telegram_id = %s
                WHERE id = %s
                RETURNING telegram_id
            """, (telegram_id, user_id))
            result = cursor.fetchone()
            if result:
                return result[0]
            return None
            
    def get_directions(self):
        """Получить все направления из таблицы directions."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, has_file_upload, criteria, is_active
                FROM directions
                WHERE is_active = TRUE  -- Added filter for performance
                ORDER BY name
            """)
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "hasFileUpload": row[2],
                    "criteria": row[3],
                    "isActive": row[4]
                } for row in cursor.fetchall()
            ]

    def insert_or_update_daily_hours(self, operator_id, day, work_time=0.0, break_time=0.0,
                                    talk_time=0.0, calls=0, efficiency=0.0,
                                    fine_amount=0.0, fine_reason=None, fine_comment=None,
                                    fines: List[Dict[str, Any]] = None):
        """
        Вставляет/обновляет запись daily_hours (operator_id + day).
        efficiency и fine_amount ожидаются в часах/суммах соответственно.
        """
        if isinstance(day, str):
            day = datetime.strptime(day, "%Y-%m-%d").date()
        with self._get_cursor() as cursor:
            # Insert or update daily_hours and return its id for managing daily_fines
            cursor.execute("""
                INSERT INTO daily_hours (
                    operator_id, day, work_time, break_time, talk_time, calls, efficiency,
                    fine_amount, fine_reason, fine_comment
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (operator_id, day)
                DO UPDATE SET
                    work_time = EXCLUDED.work_time,
                    break_time = EXCLUDED.break_time,
                    talk_time = EXCLUDED.talk_time,
                    calls = EXCLUDED.calls,
                    efficiency = EXCLUDED.efficiency,
                    fine_amount = EXCLUDED.fine_amount,
                    fine_reason = EXCLUDED.fine_reason,
                    fine_comment = EXCLUDED.fine_comment,
                    created_at = CURRENT_TIMESTAMP
                RETURNING id
            """, (operator_id, day, work_time, break_time, talk_time, calls, efficiency,
                float(fine_amount) if fine_amount is not None else 0.0,
                fine_reason, fine_comment))
            res = cursor.fetchone()
            daily_id = res[0] if res else None

            # If fines provided, replace existing fines for this daily_hours record
            if daily_id and isinstance(fines, list):
                # remove previous fines for this daily row
                cursor.execute("DELETE FROM daily_fines WHERE daily_hours_id = %s", (daily_id,))
                insert_vals = []
                total_amount = 0.0
                first_reason = None
                comments = []
                for f in fines:
                    amt = float(f.get('amount') or 0)
                    reason = f.get('reason')
                    comment = f.get('comment')
                    insert_vals.append((daily_id, operator_id, day, amt, reason, comment))
                    total_amount += amt
                    if not first_reason and reason:
                        first_reason = reason
                    if comment:
                        comments.append(str(comment))

                if insert_vals:
                    cursor.executemany(
                        "INSERT INTO daily_fines (daily_hours_id, operator_id, day, amount, reason, comment) VALUES (%s, %s, %s, %s, %s, %s)",
                        insert_vals
                    )

                # update aggregated fields in daily_hours for backward compatibility
                agg_comment = '; '.join(comments) if comments else None
                cursor.execute("""
                    UPDATE daily_hours SET fine_amount = %s, fine_reason = %s, fine_comment = %s
                    WHERE id = %s
                """, (float(total_amount), first_reason, agg_comment, daily_id))

    def get_daily_hours_for_operator_month(self, operator_id: int, month: str):
        """
        Возвращает daily_hours для одного оператора за месяц YYYY-MM,
        а также norm_hours, aggregates (из work_hours) и rate/name из users.

        Включает поля штрафов из daily_hours: fine_amount, fine_reason, fine_comment
        и агрегированное поле fines из work_hours.

        Результат:
        {
            "month": month,
            "days_in_month": <int>,
            "operator": { ... }
        }
        """
        import calendar as _py_calendar
        from datetime import date as _date

        # validate month format YYYY-MM and compute date range
        try:
            year, mon = map(int, month.split('-'))
            days_in_month = _py_calendar.monthrange(year, mon)[1]
            start = _date(year, mon, 1)
            end = _date(year, mon, days_in_month)
        except Exception as e:
            raise ValueError("Invalid month format, expected YYYY-MM") from e

        with self._get_cursor() as cursor:
            # 1) Получаем daily_hours (одним запросом), теперь с информацией о штрафах
            cursor.execute(
                """
                SELECT day, work_time, break_time, talk_time, calls, efficiency,
                    fine_amount, fine_reason, fine_comment
                FROM daily_hours
                WHERE operator_id = %s AND day >= %s AND day <= %s
                ORDER BY day
                """,
                (operator_id, start, end),
            )
            daily_rows = cursor.fetchall()

            # build daily map: { "1": {...}, "2": {...}, ... }
            daily_map = {}
            for row in daily_rows:
                # row: (day, work_time, break_time, talk_time, calls, efficiency, fine_amount, fine_reason, fine_comment)
                day_obj = row[0]
                day_key = str(int(day_obj.day))
                daily_map[day_key] = {
                    "work_time": float(row[1]) if row[1] is not None else 0.0,
                    "break_time": float(row[2]) if row[2] is not None else 0.0,
                    "talk_time": float(row[3]) if row[3] is not None else 0.0,
                    "calls": int(row[4]) if row[4] is not None else 0,
                    "efficiency": float(row[5]) if row[5] is not None else 0.0,
                    "fine_amount": float(row[6]) if row[6] is not None else 0.0,
                    "fine_reason": row[7],
                    "fine_comment": row[8],
                    "fines": []  # will populate from daily_fines table if present
                }

            # fetch fines for this operator in the date range
            cursor.execute(
                """
                SELECT df.amount, df.reason, df.comment, dh.day
                FROM daily_fines df
                JOIN daily_hours dh ON df.daily_hours_id = dh.id
                WHERE dh.operator_id = %s AND dh.day >= %s AND dh.day <= %s
                ORDER BY dh.day, df.id
                """,
                (operator_id, start, end)
            )
            fines_rows = cursor.fetchall()
            for amt, reason, comment, day_obj in fines_rows:
                day_key = str(int(day_obj.day))
                entry = daily_map.get(day_key)
                fine_obj = {
                    "amount": float(amt) if amt is not None else 0.0,
                    "reason": reason,
                    "comment": comment
                }
                # include minutes for Опоздание for client convenience
                try:
                    if str(reason) == 'Опоздание':
                        fine_obj['minutes'] = int(round(float(amt) / 50)) if amt else 0
                except Exception:
                    pass
                if entry is not None:
                    entry.setdefault('fines', []).append(fine_obj)

            # 2) Получаем имя/ставку + данные work_hours одним LEFT JOIN запросом (включая fines)
            cursor.execute(
                """
                SELECT u.name, u.rate,
                    d.name as direction_name,
                    COALESCE(w.norm_hours, 0) AS norm_hours,
                    COALESCE(w.regular_hours, 0) AS regular_hours,
                    COALESCE(w.total_break_time, 0) AS total_break_time,
                    COALESCE(w.total_talk_time, 0) AS total_talk_time,
                    COALESCE(w.total_calls, 0) AS total_calls,
                    COALESCE(w.total_efficiency_hours, 0) AS total_efficiency_hours,
                    COALESCE(w.calls_per_hour, 0) AS calls_per_hour,
                    COALESCE(w.fines, 0) AS fines
                FROM users u
                LEFT JOIN work_hours w
                ON w.operator_id = u.id AND w.month = %s
                LEFT JOIN directions d ON u.direction_id = d.id
                WHERE u.id = %s
                LIMIT 1
                """,
                (month, operator_id),
            )
            row = cursor.fetchone()

        # default values if user / work_hours not found
        if row:
            name, rate, direction_name, norm_hours, regular_hours, total_break_time, total_talk_time, \
                total_calls, total_efficiency_hours, calls_per_hour, fines = row
            rate = float(rate) if rate is not None else 0.0
        else:
            name = None
            rate = 0.0
            direction_name = None
            norm_hours = regular_hours = total_break_time = total_talk_time = 0.0
            total_calls = 0
            total_efficiency_hours = calls_per_hour = fines = 0.0

        operator_obj = {
            "operator_id": operator_id,
            "name": name,
            "direction": direction_name,
            "rate": rate,
            "norm_hours": float(norm_hours),
            "fines": float(fines),
            "daily": daily_map,
            "aggregates": {
                "regular_hours": float(regular_hours),
                "total_break_time": float(total_break_time),
                "total_talk_time": float(total_talk_time),
                "total_calls": int(total_calls),
                "total_efficiency_hours": float(total_efficiency_hours),
                "calls_per_hour": float(calls_per_hour),
            },
        }

        return {"month": month, "days_in_month": days_in_month, "operator": operator_obj}

    def get_daily_hours_by_supervisor_month(self, supervisor_id, month):
        """
        Возвращает все daily_hours и агрегаты work_hours для всех операторов указанного супервайзера за месяц YYYY-MM.
        Включает также ставку (rate) из users, norm_hours и fines из work_hours.
        """
        import calendar as _py_calendar
        from datetime import date as _date

        # validate month format YYYY-MM
        try:
            year, mon = map(int, month.split('-'))
            days = _py_calendar.monthrange(year, mon)[1]
            start = _date(year, mon, 1)
            end = _date(year, mon, days)
        except Exception as e:
            raise ValueError("Invalid month format, expected YYYY-MM") from e

        with self._get_cursor() as cursor:
            # Получаем операторов + ставка + norm_hours + агрегаты work_hours (включая fines)
            cursor.execute("""
                SELECT u.id, u.name, u.rate, u.status, u.supervisor_id,
                    d.name as direction_name,
                    COALESCE(w.norm_hours, 0) as norm_hours,
                    COALESCE(w.regular_hours, 0) as regular_hours,
                    COALESCE(w.total_break_time, 0) as total_break_time,
                    COALESCE(w.total_talk_time, 0) as total_talk_time,
                    COALESCE(w.total_calls, 0) as total_calls,
                    COALESCE(w.total_efficiency_hours, 0) as total_efficiency_hours,
                    COALESCE(w.calls_per_hour, 0) as calls_per_hour,
                    COALESCE(w.fines, 0) as fines
                FROM users u
                LEFT JOIN work_hours w
                ON w.operator_id = u.id AND w.month = %s
                LEFT JOIN directions d ON u.direction_id = d.id
                WHERE u.role = 'operator' AND u.supervisor_id = %s
                ORDER BY u.name
            """, (month, supervisor_id))
            operator_rows = cursor.fetchall()  # list of tuples

            if not operator_rows:
                return {"month": month, "days_in_month": days, "operators": []}

            op_ids = [row[0] for row in operator_rows]

            # Получаем daily_hours для этих операторов за месяц (с информацией о штрафах)
            cursor.execute("""
                SELECT d.operator_id, d.day, d.work_time, d.break_time, d.talk_time, d.calls, d.efficiency,
                    d.fine_amount, d.fine_reason, d.fine_comment
                FROM daily_hours d
                WHERE d.operator_id = ANY(%s)
                AND d.day >= %s AND d.day <= %s
                ORDER BY d.operator_id, d.day
            """, (op_ids, start, end))
            daily_rows = cursor.fetchall()

            # Готовим словарь daily: operator_id -> {day_number: {...}}
            daily_map = {}
            for (op_id, day, work_time, break_time, talk_time, calls, eff,
                fine_amount, fine_reason, fine_comment) in daily_rows:
                day_num = str(int(day.day))
                d = {
                    "work_time": float(work_time) if work_time is not None else 0.0,
                    "break_time": float(break_time) if break_time is not None else 0.0,
                    "talk_time": float(talk_time) if talk_time is not None else 0.0,
                    "calls": int(calls) if calls is not None else 0,
                    "efficiency": float(eff) if eff is not None else 0.0,
                    "fine_amount": float(fine_amount) if fine_amount is not None else 0.0,
                    "fine_reason": fine_reason,
                    "fine_comment": fine_comment,
                    "fines": []
                }
                daily_map.setdefault(op_id, {})[day_num] = d

            # fetch fines for all operators in range
            cursor.execute("""
                SELECT df.operator_id, df.amount, df.reason, df.comment, df.day
                FROM daily_fines df
                WHERE df.operator_id = ANY(%s)
                AND df.day >= %s AND df.day <= %s
                ORDER BY df.operator_id, df.day, df.id
            """, (op_ids, start, end))
            fines_rows = cursor.fetchall()
            for op_id, amt, reason, comment, day_obj in fines_rows:
                day_num = str(int(day_obj.day))
                fine_obj = {"amount": float(amt) if amt is not None else 0.0, "reason": reason, "comment": comment}
                try:
                    if str(reason) == 'Опоздание':
                        fine_obj['minutes'] = int(round(float(amt) / 50)) if amt else 0
                except Exception:
                    pass
                if op_id in daily_map and day_num in daily_map[op_id]:
                    daily_map[op_id][day_num].setdefault('fines', []).append(fine_obj)

            # Сбор финального списка операторов
            operators = []
            for row in operator_rows:
                (op_id, op_name, rate, status, sup_id, direction_name, norm_hours, 
                regular_hours, total_break_time, total_talk_time,
                total_calls, total_efficiency_hours, calls_per_hour, fines) = row

                operators.append({
                    "operator_id": op_id,
                    "name": op_name,
                    "direction": direction_name,
                    "supervisor_id": sup_id,
                    "rate": float(rate) if rate is not None else 0.0,
                    "status": status,
                    "norm_hours": float(norm_hours) if norm_hours is not None else 0.0,
                    "daily": daily_map.get(op_id, {}),
                    "aggregates": {
                        "regular_hours": float(regular_hours),
                        "total_break_time": float(total_break_time),
                        "total_talk_time": float(total_talk_time),
                        "total_calls": int(total_calls),
                        "total_efficiency_hours": float(total_efficiency_hours),
                        "calls_per_hour": float(calls_per_hour),
                        "fines": float(fines)
                    }
                })

        return {"month": month, "days_in_month": days, "operators": operators}

    def get_daily_hours_for_all_month(self, month):
        """
        Возвращает все daily_hours и агрегаты work_hours для всех операторов за месяц YYYY-MM.
        Аналогично get_daily_hours_by_supervisor_month, но без фильтра по супервайзеру.
        """
        import calendar as _py_calendar
        from datetime import date as _date

        try:
            year, mon = map(int, month.split('-'))
            days = _py_calendar.monthrange(year, mon)[1]
            start = _date(year, mon, 1)
            end = _date(year, mon, days)
        except Exception as e:
            raise ValueError("Invalid month format, expected YYYY-MM") from e

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.rate, u.status, u.supervisor_id,
                    d.name as direction_name,
                    COALESCE(w.norm_hours, 0) as norm_hours,
                    COALESCE(w.regular_hours, 0) as regular_hours,
                    COALESCE(w.total_break_time, 0) as total_break_time,
                    COALESCE(w.total_talk_time, 0) as total_talk_time,
                    COALESCE(w.total_calls, 0) as total_calls,
                    COALESCE(w.total_efficiency_hours, 0) as total_efficiency_hours,
                    COALESCE(w.calls_per_hour, 0) as calls_per_hour,
                    COALESCE(w.fines, 0) as fines
                FROM users u
                LEFT JOIN work_hours w
                ON w.operator_id = u.id AND w.month = %s
                LEFT JOIN directions d ON u.direction_id = d.id
                WHERE u.role = 'operator'
                ORDER BY u.name
            """, (month,))
            operator_rows = cursor.fetchall()

            if not operator_rows:
                return {"month": month, "days_in_month": days, "operators": []}

            op_ids = [row[0] for row in operator_rows]

            cursor.execute("""
                SELECT d.operator_id, d.day, d.work_time, d.break_time, d.talk_time, d.calls, d.efficiency,
                    d.fine_amount, d.fine_reason, d.fine_comment
                FROM daily_hours d
                WHERE d.operator_id = ANY(%s)
                AND d.day >= %s AND d.day <= %s
                ORDER BY d.operator_id, d.day
            """, (op_ids, start, end))
            daily_rows = cursor.fetchall()

            daily_map = {}
            for (op_id, day, work_time, break_time, talk_time, calls, eff,
                fine_amount, fine_reason, fine_comment) in daily_rows:
                day_num = str(int(day.day))
                d = {
                    "work_time": float(work_time) if work_time is not None else 0.0,
                    "break_time": float(break_time) if break_time is not None else 0.0,
                    "talk_time": float(talk_time) if talk_time is not None else 0.0,
                    "calls": int(calls) if calls is not None else 0,
                    "efficiency": float(eff) if eff is not None else 0.0,
                    "fine_amount": float(fine_amount) if fine_amount is not None else 0.0,
                    "fine_reason": fine_reason,
                    "fine_comment": fine_comment,
                    "fines": []
                }
                daily_map.setdefault(op_id, {})[day_num] = d

            cursor.execute("""
                SELECT df.operator_id, df.amount, df.reason, df.comment, df.day
                FROM daily_fines df
                WHERE df.operator_id = ANY(%s)
                AND df.day >= %s AND df.day <= %s
                ORDER BY df.operator_id, df.day, df.id
            """, (op_ids, start, end))
            fines_rows = cursor.fetchall()
            for op_id, amt, reason, comment, day_obj in fines_rows:
                day_num = str(int(day_obj.day))
                fine_obj = {"amount": float(amt) if amt is not None else 0.0, "reason": reason, "comment": comment}
                try:
                    if str(reason) == 'Опоздание':
                        fine_obj['minutes'] = int(round(float(amt) / 50)) if amt else 0
                except Exception:
                    pass
                if op_id in daily_map and day_num in daily_map[op_id]:
                    daily_map[op_id][day_num].setdefault('fines', []).append(fine_obj)

            operators = []
            for row in operator_rows:
                (op_id, op_name, rate, status, sup_id, direction_name, norm_hours,
                regular_hours, total_break_time, total_talk_time,
                total_calls, total_efficiency_hours, calls_per_hour, fines) = row

                operators.append({
                    "operator_id": op_id,
                    "name": op_name,
                    "direction": direction_name,
                    "supervisor_id": sup_id,
                    "rate": float(rate) if rate is not None else 0.0,
                    "status": status,
                    "norm_hours": float(norm_hours) if norm_hours is not None else 0.0,
                    "daily": daily_map.get(op_id, {}),
                    "aggregates": {
                        "regular_hours": float(regular_hours),
                        "total_break_time": float(total_break_time),
                        "total_talk_time": float(total_talk_time),
                        "total_calls": int(total_calls),
                        "total_efficiency_hours": float(total_efficiency_hours),
                        "calls_per_hour": float(calls_per_hour),
                        "fines": float(fines)
                    }
                })

        return {"month": month, "days_in_month": days, "operators": operators}

    def aggregate_month_from_daily(self, operator_id, month):
        """
        Суммирует daily_hours за месяц и обновляет work_hours:
        - regular_hours <- SUM(work_time)
        - total_break_time <- SUM(break_time)
        - total_talk_time <- SUM(talk_time)
        - total_calls <- SUM(calls)
        - total_efficiency_hours <- SUM(efficiency)  <-- теперь суммируем часы
        - calls_per_hour = total_calls / regular_hours (0 если regular_hours == 0)
        Возвращает агрегаты.
        """
        year, mon = map(int, month.split('-'))
        start = date(year, mon, 1)
        end = date(year, mon, calendar.monthrange(year, mon)[1])
        with self._get_cursor() as cursor:
            # Sum daily_hours fields independently to avoid duplication when multiple fines exist per day.
            cursor.execute("""
                SELECT
                    COALESCE(SUM(work_time),0),
                    COALESCE(SUM(break_time),0),
                    COALESCE(SUM(talk_time),0),
                    COALESCE(SUM(calls),0),
                    COALESCE(SUM(efficiency),0)
                FROM daily_hours
                WHERE operator_id = %s AND day >= %s AND day <= %s
            """, (operator_id, start, end))
            row = cursor.fetchone()
            total_work_time, total_break_time, total_talk_time, total_calls, total_efficiency_hours = row

            # Sum fines from daily_fines separately (join via daily_hours to respect day/operator filter)
            cursor.execute("""
                SELECT COALESCE(SUM(df.amount), 0)
                FROM daily_fines df
                JOIN daily_hours dh ON df.daily_hours_id = dh.id
                WHERE dh.operator_id = %s AND dh.day >= %s AND dh.day <= %s
            """, (operator_id, start, end))
            total_fines = cursor.fetchone()[0] or 0.0

            # Защита от деления на ноль
            if total_work_time and float(total_work_time) > 0:
                calls_per_hour = float(total_calls) / float(total_work_time)
            else:
                calls_per_hour = 0.0

            # Вставляем/обновляем work_hours:
            cursor.execute("""
                INSERT INTO work_hours (
                    operator_id, month, regular_hours, training_hours, fines, total_break_time,
                    total_talk_time, total_calls, total_efficiency_hours, calls_per_hour
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (operator_id, month)
                DO UPDATE SET
                    regular_hours = EXCLUDED.regular_hours,
                    training_hours = EXCLUDED.training_hours,
                    fines = EXCLUDED.fines,
                    total_break_time = EXCLUDED.total_break_time,
                    total_talk_time = EXCLUDED.total_talk_time,
                    total_calls = EXCLUDED.total_calls,
                    total_efficiency_hours = EXCLUDED.total_efficiency_hours,
                    calls_per_hour = EXCLUDED.calls_per_hour,
                    created_at = CURRENT_TIMESTAMP
            """, (
                operator_id,
                month,
                float(total_work_time),
                0.0,  # training_hours — если считаешь отдельно, можно заполнять позже
                float(total_fines),
                float(total_break_time),
                float(total_talk_time),
                int(total_calls),
                float(total_efficiency_hours),
                float(calls_per_hour)
            ))

        return {
            "regular_hours": float(total_work_time),
            "total_break_time": float(total_break_time),
            "total_talk_time": float(total_talk_time),
            "total_calls": int(total_calls),
            "total_efficiency_hours": float(total_efficiency_hours),
            "calls_per_hour": float(calls_per_hour),
            "fines": float(total_fines)
        }

    def auto_fill_norm_hours(self, month):
        """
        Авто-подсчет `norm_hours` для всех операторов за указанный месяц (формат 'YYYY-MM'),
        где текущая `norm_hours` равна 0. Формула: рабочие дни * 8 * rate.

        Возвращает словарь: {"month": month, "work_days": int, "processed": int}
        processed — число строк, на которые сработал INSERT/UPDATE (оценочно).
        """
        import calendar as _py_calendar
        from datetime import date as _date, timedelta as _td

        try:
            year, mon = map(int, month.split('-'))
        except Exception as e:
            raise ValueError("Invalid month format, expected YYYY-MM") from e

        days_in_month = _py_calendar.monthrange(year, mon)[1]
        start = _date(year, mon, 1)
        end = _date(year, mon, days_in_month)

        # считаем рабочие дни (понедельник=0 .. пятница=4)
        work_days = 0
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                work_days += 1
            cur += _td(days=1)

        with self._get_cursor() as cursor:
            # Вставляем/обновляем norm_hours для всех операторов.
            # Для операторов без строки в work_hours будет INSERT, для существующих с norm_hours=0 — UPDATE.
            cursor.execute("""
                INSERT INTO work_hours (operator_id, month, norm_hours)
                SELECT u.id, %s as month, (%s::float * 8.0 * COALESCE(u.rate, 1.0))::float
                FROM users u
                WHERE u.role = 'operator'
                ON CONFLICT (operator_id, month) DO UPDATE
                  SET norm_hours = EXCLUDED.norm_hours
                  WHERE COALESCE(work_hours.norm_hours, 0) = 0
            """, (month, work_days))

            # rowcount может быть не точен при ON CONFLICT, но даёт оценку затронутых строк
            processed = cursor.rowcount if cursor.rowcount is not None else 0

        return {"month": month, "work_days": work_days, "processed": int(processed)}

    def save_directions(self, directions):
        """Сохранить направления в таблицу directions, создавая новые версии при изменениях."""
        with self._get_cursor() as cursor:
            # 1. Получаем текущие активные направления (только нужные поля)
            cursor.execute("""
                SELECT id, name, has_file_upload, criteria, version
                FROM directions
                WHERE is_active = TRUE
            """)
            existing_directions = {
                row[1]: {
                    "id": row[0],
                    "has_file_upload": row[2],
                    "criteria": row[3],
                    "version": row[4]
                } for row in cursor.fetchall()
            }
            
            new_direction_names = {d['name'] for d in directions}
            directions_to_deactivate = []
            insert_values = []
            
            # 2. Подготовка данных для пакетной обработки
            for direction in directions:
                name = direction['name']
                has_file_upload = direction['hasFileUpload']
                criteria = json.dumps(direction['criteria'])
                
                if name in existing_directions:
                    existing = existing_directions[name]
                    if (existing['has_file_upload'] == has_file_upload and
                        existing['criteria'] == criteria):
                        continue  # Ничего не изменилось, пропускаем
                    
                    directions_to_deactivate.append(existing['id'])
                    insert_values.append((
                        name, has_file_upload, criteria,
                        existing['version'] + 1, existing['id']
                    ))
                else:
                    insert_values.append((
                        name, has_file_upload, criteria,
                        1, None  # version=1, no previous version
                    ))
            
            # 3. Пакетное деактивирование старых версий
            if directions_to_deactivate:
                cursor.execute("""
                    UPDATE directions
                    SET is_active = FALSE
                    WHERE id = ANY(%s)
                """, (directions_to_deactivate,))
            
            # 4. Пакетное добавление новых версий
            if insert_values:
                cursor.executemany("""
                    INSERT INTO directions (
                        name, has_file_upload, criteria,
                        version, previous_version_id
                    )
                    VALUES (%s, %s, %s, %s, %s)
                """, insert_values)
            
            # 5. Деактивируем направления, которых нет в новом списке
            if new_direction_names:
                cursor.execute("""
                    UPDATE directions
                    SET is_active = FALSE
                    WHERE is_active = TRUE AND name NOT IN %s
                """, (tuple(new_direction_names),))
            else:
                cursor.execute("""
                    UPDATE directions
                    SET is_active = FALSE
                    WHERE is_active = TRUE
                """)
            
            # 6. Оптимизированное обновление direction_id в users
            cursor.execute("""
                -- Обновляем direction_id на последнюю активную версию с тем же именем
                UPDATE users u
                SET direction_id = latest.id
                FROM directions current
                JOIN (
                    SELECT name, id,
                        ROW_NUMBER() OVER (PARTITION BY name ORDER BY version DESC) as rn
                    FROM directions
                    WHERE is_active = TRUE
                ) latest ON current.name = latest.name AND latest.rn = 1
                WHERE u.direction_id = current.id AND current.is_active = FALSE;
                
                -- Обнуляем direction_id где нет активных направлений
                UPDATE users
                SET direction_id = NULL
                WHERE direction_id IS NOT NULL
                AND direction_id NOT IN (
                    SELECT id FROM directions WHERE is_active = TRUE
                );
            """)

    def get_operator_credentials(self, operator_id, supervisor_id):
        """Получить логин и хеш пароля оператора с проверкой принадлежности супервайзеру"""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.login 
                FROM users u
                WHERE u.id = %s AND u.role = 'operator' AND u.supervisor_id = %s
            """, (operator_id, supervisor_id))
            return cursor.fetchone()

    def migrate_daily_fines_from_daily_hours(self):
        """
        Migration helper: convert existing aggregated fine_amount/fine_reason/fine_comment
        from daily_hours into individual rows in daily_fines when no daily_fines exist for that day.
        This is idempotent: it will skip days that already have entries in daily_fines.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, operator_id, day, fine_amount, fine_reason, fine_comment
                FROM daily_hours
                WHERE COALESCE(fine_amount, 0) > 0
            """)
            rows = cursor.fetchall()
            for dh_id, op_id, day_obj, fine_amount, fine_reason, fine_comment in rows:
                # skip if fines already exist for this daily_hours
                cursor.execute("SELECT 1 FROM daily_fines WHERE daily_hours_id = %s LIMIT 1", (dh_id,))
                if cursor.fetchone():
                    continue
                # insert single aggregated fine as one row
                cursor.execute(
                    "INSERT INTO daily_fines (daily_hours_id, operator_id, day, amount, reason, comment) VALUES (%s, %s, %s, %s, %s, %s)",
                    (dh_id, op_id, day_obj, float(fine_amount or 0.0), fine_reason, fine_comment)
                )

    def update_user_password(self, user_id, new_password):
        password_hash = pbkdf2_sha256.hash(new_password)
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET password_hash = %s
                WHERE id = %s
                RETURNING id
            """, (password_hash, user_id))
            return cursor.fetchone() is not None
    
    def update_operator_login(self, operator_id, supervisor_id, new_login):
        """Обновить логин оператора с проверкой принадлежности"""
        if supervisor_id:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    UPDATE users SET login = %s
                    WHERE id = %s AND supervisor_id = %s
                    RETURNING id
                """, (new_login, operator_id, supervisor_id))
                return cursor.fetchone() is not None
        else:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    UPDATE users SET login = %s
                    WHERE id = %s
                    RETURNING id
                """, (new_login, operator_id))
                return cursor.fetchone() is not None
    
    def update_operator_password(self, operator_id, supervisor_id, new_password):
        """Обновить пароль оператора с проверкой принадлежности"""
        password_hash = pbkdf2_sha256.hash(new_password)
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET password_hash = %s
                WHERE id = %s AND role = 'operator' AND supervisor_id = %s
                RETURNING id
            """, (password_hash, operator_id, supervisor_id))
            return cursor.fetchone() is not None
            
    def get_user(self, **kwargs):
        conditions = []
        params = []
        for key, value in kwargs.items():
            if key == 'direction':
                conditions.append("d.name = %s")
                params.append(value)
            else:
                conditions.append(f"u.{key} = %s")
                params.append(value)
        
        query = f"""
            SELECT u.id, u.telegram_id, u.name, u.role, d.name, u.hire_date, u.supervisor_id, u.login, u.hours_table_url, u.scores_table_url, u.is_active, u.status, u.rate, u.gender, u.birth_date
            FROM users u
            LEFT JOIN directions d ON u.direction_id = d.id
            WHERE {' AND '.join(conditions)}
        """
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()

    def get_call_by_id(self, call_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, operator_id, phone_number, audio_path
                FROM calls
                WHERE id = %s
            """, (call_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0],
                    "operator_id": row[1],
                    "phone_number": row[2],
                    "audio_path": row[3]
                }
            return None
        
    def update_user_table(self, user_id, hours_table_url=None, scores_table_url=None):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users 
                SET hours_table_url = COALESCE(%s, hours_table_url),
                    scores_table_url = COALESCE(%s, scores_table_url)
                WHERE id = %s
            """, (hours_table_url, scores_table_url, user_id))

    def add_call_evaluation(self,
                            evaluator_id,
                            operator_id,
                            phone_number,
                            score,
                            comment=None,
                            month=None,
                            audio_path=None,
                            is_draft=False,
                            scores=None,
                            criterion_comments=None,
                            direction_id=None,
                            is_correction=False,
                            previous_version_id=None,
                            appeal_date=None,
                            external_id=None):   # <-- NEW optional param
        """
        Создаёт/обновляет запись в calls и, если возможно, помечает связанную запись в imported_calls как evaluated.
        """
        month = month or datetime.now().strftime('%Y-%m')

        # Подготовка JSON данных один раз
        scores_json = json.dumps(scores) if scores else None
        criterion_comments_json = json.dumps(criterion_comments) if criterion_comments else None

        with self._get_cursor() as cursor:
            # Проверка существования direction_id одним запросом
            if direction_id:
                cursor.execute("SELECT 1 FROM directions WHERE id = %s AND is_active = TRUE", (direction_id,))
                if not cursor.fetchone():
                    direction_id = None

            if is_correction and previous_version_id and not audio_path:
                cursor.execute("SELECT audio_path FROM calls WHERE id = %s", (previous_version_id,))
                result = cursor.fetchone()
                if result and result[0]:
                    audio_path = result[0]

            # Объединенная проверка черновика и существующей оценки
            cursor.execute("""
                WITH existing_data AS (
                    -- Проверка на существующий черновик
                    SELECT id, audio_path, FALSE AS is_existing_eval, created_at
                    FROM calls 
                    WHERE evaluator_id = %s 
                    AND operator_id = %s 
                    AND month = %s 
                    AND is_draft = TRUE
                    
                    UNION ALL
                    
                    -- Проверка на существующую оценку (не черновик)
                    SELECT id, NULL AS audio_path, TRUE AS is_existing_eval, created_at
                    FROM calls 
                    WHERE operator_id = %s 
                    AND month = %s 
                    AND phone_number = %s 
                    AND is_draft = FALSE
                )
                SELECT id, audio_path, is_existing_eval 
                FROM existing_data
                ORDER BY created_at DESC
                LIMIT 1
            """, (evaluator_id, operator_id, month, operator_id, month, phone_number))

            existing_record = cursor.fetchone()
            old_audio_path = None
            call_id = None

            if existing_record and not is_correction:
                call_id, old_audio_path, is_existing_eval = existing_record

                # Обновление существующей записи
                if is_draft:
                    # Для черновика обновляем все поля
                    cursor.execute("""
                        UPDATE calls
                        SET phone_number = %s,
                            score = %s,
                            comment = %s,
                            audio_path = COALESCE(%s, audio_path),
                            created_at = CURRENT_TIMESTAMP,
                            scores = %s,
                            criterion_comments = %s,
                            is_correction = %s,
                            direction_id = %s
                        WHERE id = %s
                        RETURNING id
                    """, (
                        phone_number, score, comment, audio_path,
                        scores_json, criterion_comments_json,
                        False,
                        direction_id,
                        call_id
                    ))
                    call_id = cursor.fetchone()[0]

                    # Удаляем старый аудиофайл если он был заменен
                    if old_audio_path and audio_path and old_audio_path != audio_path:
                        try:
                            bucket_name = os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET')
                            client = get_gcs_client()
                            bucket = client.bucket(bucket_name)
                            blob = bucket.blob(old_audio_path.replace("my-app-audio-uploads/", ""))
                            blob.delete()
                        except Exception as e:
                            logging.error(f"Error removing old audio file: {str(e)}")

                    # Перед возвратом — пометить imported_calls (если найдено)
                    try:
                        if external_id:
                            cursor.execute("""
                                UPDATE imported_calls
                                SET status = 'evaluated',
                                    evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                                    evaluated_by = %s
                                WHERE external_id = %s AND month = %s AND status != 'evaluated'
                            """, (evaluator_id, external_id, month))
                        else:
                            # попытаться найти по телефону + дате (точное совпадение)
                            if phone_number and appeal_date:
                                phone_norm = _normalize_phone(phone_number)
                                cursor.execute("""
                                    UPDATE imported_calls
                                    SET status = 'evaluated',
                                        evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                                        evaluated_by = %s
                                    WHERE phone_normalized = %s AND month = %s AND datetime_raw = %s AND status != 'evaluated'
                                """, (evaluator_id, phone_norm, month, appeal_date))
                    except Exception as e:
                        logging.exception("Error marking imported_call as evaluated: %s", e)

                    return call_id

            # Создаем новую запись (для новой оценки, переоценки или если нет черновика)
            cursor.execute("""
                INSERT INTO calls (
                    evaluator_id, operator_id, month, phone_number, score, comment,
                    audio_path, is_draft, is_correction, previous_version_id,
                    scores, criterion_comments, direction_id, appeal_date
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                evaluator_id, operator_id, month, phone_number, score, comment,
                audio_path, is_draft, is_correction, previous_version_id,
                scores_json, criterion_comments_json, direction_id, appeal_date
            ))
            call_id = cursor.fetchone()[0]

            # Удаляем старый аудиофайл, если он был заменен
            if old_audio_path and audio_path and old_audio_path != audio_path:
                try:
                    bucket_name = os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET')
                    client = get_gcs_client()
                    bucket = client.bucket(bucket_name)
                    blob = bucket.blob(old_audio_path.replace("my-app-audio-uploads/", ""))
                    blob.delete()
                except Exception as e:
                    logging.error(f"Error removing old audio file: {str(e)}")

            # После успешного создания — помечаем imported_calls как evaluated (если можем сопоставить)
            try:
                if external_id:
                    cursor.execute("""
                        UPDATE imported_calls
                        SET status = 'evaluated',
                            evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                            evaluated_by = %s
                        WHERE external_id = %s AND month = %s AND status != 'evaluated'
                    """, (evaluator_id, external_id, month))
                else:
                    if phone_number and appeal_date:
                        phone_norm = _normalize_phone(phone_number)
                        cursor.execute("""
                            UPDATE imported_calls
                            SET status = 'evaluated',
                                evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                                evaluated_by = %s
                            WHERE phone_normalized = %s AND month = %s AND datetime_raw = %s AND status != 'evaluated'
                        """, (evaluator_id, phone_norm, month, appeal_date))
            except Exception as e:
                logging.exception("Error marking imported_call as evaluated: %s", e)

            return call_id
    
    def import_calls_from_distribution(self, payload: dict, importer_id: Optional[int] = None) -> dict:
        """
        Импортирует JSON payload в imported_calls.
        Возвращает отчёт: {'imported': n, 'updated': m, 'skipped': k, 'errors': [...], 'missing_operators': [...]}
        """
        month = payload.get('month')
        if not month:
            raise ValueError("Payload must include 'month' (YYYY-MM)")

        imported = 0
        updated = 0
        skipped = 0
        errors = []
        missing_operators = set()

        with self._get_cursor() as cur:
            for op in payload.get('distribution', []):
                op_name = op.get('operator')
                desired = op.get('desired')
                available = op.get('available')

                cur.execute("SELECT id FROM users WHERE name = %s LIMIT 1", (op_name,))
                r = cur.fetchone()
                operator_id = r[0] if r else None
                if not operator_id:
                    missing_operators.add(op_name)

                for c in op.get('calls', []):
                    external_id = c.get('id')
                    dt_raw_str = c.get('datetimeRaw')
                    parsed_dt = _parse_datetime_raw(dt_raw_str)
                    phone = c.get('phone')
                    phone_norm = _normalize_phone(phone)
                    duration = c.get('durationSec')

                    try:
                        cur.execute("""
                            INSERT INTO imported_calls
                            (external_id, operator_name, operator_id, month, datetime_raw,
                            phone_number, phone_normalized, duration_sec, desired, available, status, imported_at)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'not_evaluated', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                            ON CONFLICT (external_id, month) DO UPDATE
                            SET operator_name = EXCLUDED.operator_name,
                                operator_id = COALESCE(EXCLUDED.operator_id, imported_calls.operator_id),
                                datetime_raw = COALESCE(EXCLUDED.datetime_raw, imported_calls.datetime_raw),
                                phone_number = COALESCE(EXCLUDED.phone_number, imported_calls.phone_number),
                                phone_normalized = COALESCE(EXCLUDED.phone_normalized, imported_calls.phone_normalized),
                                duration_sec = COALESCE(EXCLUDED.duration_sec, imported_calls.duration_sec),
                                desired = COALESCE(EXCLUDED.desired, imported_calls.desired),
                                available = COALESCE(EXCLUDED.available, imported_calls.available),
                                status = COALESCE(imported_calls.status, 'not_evaluated'),
                                imported_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'
                            """,
                            (external_id, op_name, operator_id, month, parsed_dt, phone, phone_norm, duration, desired, available)
                        )
                        # cur.rowcount может быть ненадёжен для ON CONFLICT; просто учитываем как imported/updated логически
                        imported += 1
                    except Exception as exc:
                        errors.append({"external_id": external_id, "error": str(exc)})
                        skipped += 1

        return {
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "missing_operators": list(missing_operators)
        }

    def list_imported_calls(self, month: str, status: Optional[str] = None, operator_name: Optional[str] = None, limit: int = 200):
        q = "SELECT id, external_id, operator_name, operator_id, datetime_raw, phone_number, phone_normalized, duration_sec, desired, available, status, evaluated_at, evaluated_by, notes FROM imported_calls WHERE month = %s"
        params = [month]
        if status:
            q += " AND status = %s"
            params.append(status)
        if operator_name:
            q += " AND operator_name = %s"
            params.append(operator_name)
        q += " ORDER BY datetime_raw NULLS LAST LIMIT %s"
        params.append(limit)
        with self._get_cursor() as cur:
            cur.execute(q, tuple(params))
            rows = cur.fetchall()
        keys = ["id","external_id","operator_name","operator_id","datetime_raw","phone_number","phone_normalized","duration_sec","desired","available","status","evaluated_at","evaluated_by","notes"]
        return [dict(zip(keys, r)) for r in rows]

    def mark_imported_call_skipped(self, imported_call_id: str, reason: Optional[str] = None):
        with self._get_cursor() as cur:
            cur.execute("UPDATE imported_calls SET status = 'skipped', notes = COALESCE(notes,'') || %s WHERE id = %s",
                        (f"\nskipped: {reason}" if reason else "\nskipped", imported_call_id))
            return cur.rowcount == 1

    def parse_calls_file(self, file):
        try:
            filename = file.filename.lower()

            if filename.endswith(".csv"):
                df = pd.read_csv(file, sep=";")
                sheet_name = "CSV"
            elif filename.endswith((".xls", ".xlsx")):
                excel = pd.ExcelFile(file)
                sheet_name = excel.sheet_names[0]
                df = pd.read_excel(excel, sheet_name=sheet_name)
            else:
                return None, None, "Неверный формат файла. Поддерживаются только CSV, XLS, XLSX"

            required_columns = [
                "Name",
                "Количество поступивших",
                "Время в работе",
                "Всего перерыва",
                "Тех. причина",
                "Тренинг",
                "Время в разговоре",
                "На удержании"
            ]
            for col in required_columns:
                if col not in df.columns:
                    return None, None, f"Не найдена колонка: {col}"

            operators = []
            month = date.today().strftime("%Y-%m")

            for _, row in df.iterrows():
                try:
                    name = str(row["Name"]).strip()
                    calls = int(row["Количество поступивших"]) if not pd.isna(row["Количество поступивших"]) else 0

                    def to_seconds(cell):
                        try:
                            return pd.to_timedelta(str(cell)).total_seconds()
                        except Exception:
                            return 0.0

                    work_time_s = to_seconds(row["Время в работе"])
                    break_time_s = to_seconds(row["Всего перерыва"])
                    tech_time_s = to_seconds(row["Тех. причина"])
                    training_time_s = to_seconds(row["Тренинг"])
                    talk_time_s = to_seconds(row["Время в разговоре"])
                    on_hold_s = to_seconds(row["На удержании"])

                    work_time_h = max((work_time_s - break_time_s - tech_time_s - training_time_s) / 3600.0, 0.0)
                    break_time_h = break_time_s / 3600.0
                    talk_time_h = talk_time_s / 3600.0
                    efficiency_h = (talk_time_s + on_hold_s) / 3600.0

                    operators.append({
                        "name": name,
                        "work_time": round(work_time_h, 2),
                        "break_time": round(break_time_h, 2),
                        "talk_time": round(talk_time_h, 2),
                        "calls": int(calls),
                        "efficiency": round(efficiency_h, 2),
                        "month": month
                    })
                except Exception as e:
                    print(f"Ошибка при обработке строки {row}: {e}")
                    continue

            return sheet_name, operators, None

        except Exception as e:
            return None, None, str(e)


    def get_operator_stats(self, operator_id):
        """Получить статистику оператора (часы, оценки, звонки в час, тренинги)"""
        with self._get_cursor() as cursor:
            # Текущий месяц в формате YYYY-MM
            current_month = datetime.now().strftime('%Y-%m')

            # 1) Берём агрегаты из work_hours (regular_hours, total_calls, fines, norm_hours)
            cursor.execute("""
                SELECT 
                    COALESCE(wh.regular_hours, 0) AS regular_hours,
                    COALESCE(wh.fines, 0) AS fines,
                    COALESCE(wh.norm_hours, 0) AS norm_hours,
                    COALESCE(wh.total_calls, 0) AS total_calls
                FROM work_hours wh
                WHERE wh.operator_id = %s AND wh.month = %s
                LIMIT 1
            """, (operator_id, current_month))
            wh_row = cursor.fetchone()

            if wh_row:
                regular_hours, fines, norm_hours, total_calls = wh_row
                regular_hours = float(regular_hours or 0.0)
                fines = float(fines or 0.0)
                norm_hours = float(norm_hours or 0.0)
                total_calls = int(total_calls or 0)
            else:
                regular_hours = 0.0
                fines = 0.0
                norm_hours = 0.0
                total_calls = 0

            # 2) Рассчитываем training_hours прямо по таблице trainings (учитываем count_in_hours = TRUE)
            cursor.execute("""
                SELECT COALESCE(SUM(
                    CASE 
                    WHEN t.end_time <= t.start_time 
                        THEN EXTRACT(EPOCH FROM (t.end_time + INTERVAL '24 hours' - t.start_time))
                    ELSE EXTRACT(EPOCH FROM (t.end_time - t.start_time))
                    END
                ) / 3600.0, 0) AS training_hours_seconds
                FROM trainings t
                WHERE t.operator_id = %s
                AND TO_CHAR(t.training_date, 'YYYY-MM') = %s
                AND t.count_in_hours = TRUE
            """, (operator_id, current_month))
            tr_row = cursor.fetchone()
            training_hours = float(tr_row[0] or 0.0)

            # 3) Количество оценённых звонков и средняя оценка (как раньше)
            cursor.execute("""
                SELECT 
                    (SELECT COUNT(*) FROM calls WHERE operator_id = %s AND month = %s AND is_draft = FALSE) AS call_count,
                    (SELECT AVG(score) FROM calls WHERE operator_id = %s AND month = %s AND is_draft = FALSE) AS avg_score
            """, (operator_id, current_month, operator_id, current_month))
            calls_row = cursor.fetchone()
            call_count = int(calls_row[0] or 0)
            avg_score = float(calls_row[1]) if calls_row[1] is not None else 0.0

            # 4) calls_per_hour: используем total_calls (из work_hours) делённые на regular_hours
            #    Если регулярных часов нет — 0.0
            if regular_hours and regular_hours > 0:
                calls_per_hour = float(total_calls) / float(regular_hours)
            else:
                calls_per_hour = 0.0

            # 5) percent_complete: считаем с учётом зачётных тренингов (если вы хотите старое поведение — вернуть regular_hours/norm_hours)
            if norm_hours and norm_hours > 0:
                percent_complete = ((regular_hours + training_hours) / norm_hours) * 100.0
            else:
                percent_complete = 0.0

            return {
                'operator_id': operator_id,
                'month': current_month,
                'regular_hours': round(float(regular_hours), 2),
                'training_hours': round(float(training_hours), 2),
                'fines': round(float(fines), 2),
                'norm_hours': float(norm_hours),
                'percent_complete': round(percent_complete, 2),
                'call_count': int(call_count),
                'avg_score': round(float(avg_score), 2),
                'total_calls': int(total_calls),
                'calls_per_hour': round(float(calls_per_hour), 2)
            }


    def get_operators_by_supervisor(self, supervisor_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.direction_id, u.hire_date, u.hours_table_url, u.scores_table_url, s.name as supervisor_name, u.status, u.rate, u.gender, u.birth_date
                FROM users u
                LEFT JOIN directions d ON u.direction_id = d.id
                LEFT JOIN users s ON u.supervisor_id = s.id
                WHERE u.supervisor_id = %s AND u.role = 'operator'
            """, (supervisor_id,))
            return [
                {
                    'id': row[0],
                    'name': row[1],
                    'direction_id': row[2],
                    'hire_date': row[3].strftime('%d-%m-%Y') if row[3] else None,
                    'hours_table_url': row[4],
                    'scores_table_url': row[5],
                    'supervisor_name': row[6],
                    'status': row[7],
                    'rate': row[8],
                    'gender': row[9],
                    'birth_date': row[10].strftime('%d-%m-%Y') if row[10] else None
                } for row in cursor.fetchall()
            ]

    def get_activity_logs(self, operator_id, date_str=None):
        # Если date_str не указан, используем текущую дату
        if date_str is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
        try:
            log_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD")
        
        with self._get_cursor() as cursor:
            # Берём последний статус до указанной даты
            cursor.execute("""
                SELECT change_time, is_active
                FROM operator_activity_logs
                WHERE operator_id = %s
                  AND change_time < %s
                ORDER BY change_time DESC
                LIMIT 1
            """, (operator_id, log_date))
            prev_log = cursor.fetchone()
    
            # Берём все записи за указанную дату
            cursor.execute("""
                SELECT change_time, is_active 
                FROM operator_activity_logs 
                WHERE operator_id = %s 
                  AND change_time::date = %s 
                ORDER BY change_time ASC
            """, (operator_id, log_date))
            logs = cursor.fetchall()
    
        result = []
        if prev_log:
            result.append({"change_time": prev_log[0].isoformat(), "is_active": prev_log[1]})
        result.extend([
            {"change_time": row[0].isoformat(), "is_active": row[1]}
            for row in logs
        ])
        return result
    
    def get_hours_summary(self, operator_id=None, month=None):
        query = """
            SELECT 
                u.id AS operator_id,
                u.name AS operator_name,
                u.direction_id,

                COALESCE(wh.regular_hours, 0) AS regular_hours,
                COALESCE(wh.fines, 0) AS fines,
                COALESCE(wh.norm_hours, 0) AS norm_hours,
                COALESCE(wh.total_calls, 0) AS total_calls,

                -- training hours считаем из trainings
                COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN t.end_time <= t.start_time
                                THEN EXTRACT(EPOCH FROM (t.end_time + INTERVAL '24 hours' - t.start_time))
                            ELSE EXTRACT(EPOCH FROM (t.end_time - t.start_time))
                        END
                    ) / 3600.0
                    FROM trainings t
                    WHERE t.operator_id = u.id
                    AND t.count_in_hours = TRUE
                    AND (%s IS NULL OR TO_CHAR(t.training_date, 'YYYY-MM') = %s)
                ), 0) AS training_hours

            FROM users u
            LEFT JOIN work_hours wh
                ON u.id = wh.operator_id
            AND (%s IS NULL OR wh.month = %s)
            WHERE u.role = 'operator'
        """

        params = [month, month, month, month]

        if operator_id:
            query += " AND u.id = %s"
            params.append(operator_id)

        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

            result = []
            for row in rows:
                regular_hours = float(row[3])
                total_calls = int(row[6])

                calls_per_hour = (
                    round(total_calls / regular_hours, 2)
                    if regular_hours > 0 else 0.0
                )

                result.append({
                    "operator_id": row[0],
                    "operator_name": row[1],
                    "direction": row[2],
                    "regular_hours": regular_hours,
                    "training_hours": round(float(row[7]), 2),
                    "fines": float(row[4]),
                    "norm_hours": float(row[5]),
                    "calls_per_hour": calls_per_hour
                })

            return result

    def get_supervisors(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, hours_table_url, role, hire_date, status
                FROM users 
                WHERE role = 'sv'
            """)
            return cursor.fetchall()

    def get_user_by_login(self, login):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, telegram_id, name, role, direction_id, hire_date, supervisor_id, login, password_hash, hours_table_url, scores_table_url 
                FROM users WHERE login = %s
            """, (login,))
            return cursor.fetchone()

    def get_user_by_name(self, name):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, telegram_id, name, role, direction_id, hire_date, supervisor_id, login
                FROM users WHERE LOWER(name) = LOWER(%s) LIMIT 1
            """, (name,))
            return cursor.fetchone()

    def verify_password(self, user_id, password):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT password_hash FROM users WHERE id = %s
            """, (user_id,))
            result = cursor.fetchone()
            if result and result[0]:
                return pbkdf2_sha256.verify(password, result[0])
            return False

    def create_user_session(
        self,
        session_id,
        user_id,
        refresh_token_hash,
        expires_at,
        user_agent=None,
        ip_address=None
    ):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_sessions (
                    session_id,
                    user_id,
                    refresh_token_hash,
                    user_agent,
                    ip_address,
                    expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (session_id, user_id, refresh_token_hash, user_agent, ip_address, expires_at))

    def get_user_session(self, session_id, user_id=None):
        with self._get_cursor() as cursor:
            if user_id is None:
                cursor.execute("""
                    SELECT
                        session_id::text,
                        user_id,
                        refresh_token_hash,
                        user_agent,
                        ip_address,
                        created_at,
                        last_seen_at,
                        expires_at,
                        revoked_at,
                        sensitive_data_unlocked,
                        sensitive_data_unlocked_at
                    FROM user_sessions
                    WHERE session_id = %s
                """, (session_id,))
            else:
                cursor.execute("""
                    SELECT
                        session_id::text,
                        user_id,
                        refresh_token_hash,
                        user_agent,
                        ip_address,
                        created_at,
                        last_seen_at,
                        expires_at,
                        revoked_at,
                        sensitive_data_unlocked,
                        sensitive_data_unlocked_at
                    FROM user_sessions
                    WHERE session_id = %s AND user_id = %s
                """, (session_id, user_id))

            row = cursor.fetchone()
            if not row:
                return None
            return {
                "session_id": row[0],
                "user_id": row[1],
                "refresh_token_hash": row[2],
                "user_agent": row[3],
                "ip_address": row[4],
                "created_at": row[5],
                "last_seen_at": row[6],
                "expires_at": row[7],
                "revoked_at": row[8],
                "sensitive_data_unlocked": bool(row[9]),
                "sensitive_data_unlocked_at": row[10]
            }

    def rotate_user_session_token(self, session_id, user_id, refresh_token_hash, expires_at):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE user_sessions
                SET refresh_token_hash = %s,
                    expires_at = %s,
                    last_seen_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE session_id = %s
                  AND user_id = %s
                  AND revoked_at IS NULL
                RETURNING session_id
            """, (refresh_token_hash, expires_at, session_id, user_id))
            return cursor.fetchone() is not None

    def touch_user_session(self, session_id, user_id=None, ip_address=None, user_agent=None):
        with self._get_cursor() as cursor:
            if user_id is None:
                cursor.execute("""
                    UPDATE user_sessions
                    SET last_seen_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                        ip_address = COALESCE(%s, ip_address),
                        user_agent = COALESCE(%s, user_agent)
                    WHERE session_id = %s
                      AND revoked_at IS NULL
                    RETURNING session_id
                """, (ip_address, user_agent, session_id))
            else:
                cursor.execute("""
                    UPDATE user_sessions
                    SET last_seen_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                        ip_address = COALESCE(%s, ip_address),
                        user_agent = COALESCE(%s, user_agent)
                    WHERE session_id = %s
                      AND user_id = %s
                      AND revoked_at IS NULL
                    RETURNING session_id
                """, (ip_address, user_agent, session_id, user_id))
            return cursor.fetchone() is not None

    def revoke_user_session(self, session_id, user_id=None):
        with self._get_cursor() as cursor:
            if user_id is None:
                cursor.execute("""
                    UPDATE user_sessions
                    SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE session_id = %s
                      AND revoked_at IS NULL
                    RETURNING session_id
                """, (session_id,))
            else:
                cursor.execute("""
                    UPDATE user_sessions
                    SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE session_id = %s
                      AND user_id = %s
                      AND revoked_at IS NULL
                    RETURNING session_id
                """, (session_id, user_id))
            return cursor.fetchone() is not None

    def revoke_all_user_sessions(self, user_id, except_session_id=None):
        with self._get_cursor() as cursor:
            if except_session_id:
                cursor.execute("""
                    UPDATE user_sessions
                    SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE user_id = %s
                      AND session_id <> %s
                      AND revoked_at IS NULL
                """, (user_id, except_session_id))
            else:
                cursor.execute("""
                    UPDATE user_sessions
                    SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE user_id = %s
                      AND revoked_at IS NULL
                """, (user_id,))
            return cursor.rowcount

    def set_session_sensitive_access(self, session_id, user_id, unlocked=True):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE user_sessions
                SET sensitive_data_unlocked = %s,
                    sensitive_data_unlocked_at = CASE
                        WHEN %s THEN (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                        ELSE NULL
                    END
                WHERE session_id = %s
                  AND user_id = %s
                  AND revoked_at IS NULL
                RETURNING session_id
            """, (bool(unlocked), bool(unlocked), session_id, user_id))
            return cursor.fetchone() is not None

    def is_session_sensitive_access_unlocked(self, session_id, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT sensitive_data_unlocked
                FROM user_sessions
                WHERE session_id = %s
                  AND user_id = %s
                  AND revoked_at IS NULL
                  AND expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                LIMIT 1
            """, (session_id, user_id))
            row = cursor.fetchone()
            return bool(row[0]) if row else False

    def list_user_sessions(self, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    session_id::text,
                    user_agent,
                    ip_address,
                    created_at,
                    last_seen_at,
                    expires_at,
                    revoked_at
                FROM user_sessions
                WHERE user_id = %s
                ORDER BY created_at DESC
            """, (user_id,))
            rows = cursor.fetchall()
            return [
                {
                    "session_id": row[0],
                    "user_agent": row[1],
                    "ip_address": row[2],
                    "created_at": row[3],
                    "last_seen_at": row[4],
                    "expires_at": row[5],
                    "revoked_at": row[6]
                }
                for row in rows
            ]

    def list_all_active_sessions(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    us.session_id::text,
                    us.user_id,
                    u.name,
                    u.role,
                    u.login,
                    u.supervisor_id,
                    sv.name AS supervisor_name,
                    us.user_agent,
                    us.ip_address,
                    us.created_at,
                    us.last_seen_at,
                    us.expires_at,
                    us.sensitive_data_unlocked,
                    us.sensitive_data_unlocked_at
                FROM user_sessions us
                JOIN users u ON u.id = us.user_id
                LEFT JOIN users sv ON sv.id = u.supervisor_id
                WHERE us.revoked_at IS NULL
                  AND us.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                ORDER BY u.name ASC, us.last_seen_at DESC, us.created_at DESC
            """)
            rows = cursor.fetchall()
            return [
                {
                    "session_id": row[0],
                    "user_id": row[1],
                    "user_name": row[2],
                    "user_role": row[3],
                    "user_login": row[4],
                    "supervisor_id": row[5],
                    "supervisor_name": row[6],
                    "user_agent": row[7],
                    "ip_address": row[8],
                    "created_at": row[9],
                    "last_seen_at": row[10],
                    "expires_at": row[11],
                    "sensitive_data_unlocked": bool(row[12]),
                    "sensitive_data_unlocked_at": row[13]
                }
                for row in rows
            ]

    def get_call_evaluations(self, operator_id, month=None):
        """
        Возвращает оценки звонков (calls) + неоценённые звонки (imported_calls).
        Для calls.duration -> NULL, для imported_calls.duration -> ic.duration_sec.
        """
        query = """
            WITH latest_versions AS (
                SELECT 
                    phone_number,
                    month,
                    MAX(created_at) AS latest_date
                FROM calls
                WHERE operator_id = %s
                GROUP BY phone_number, month
            ),
            latest_calls AS (
                SELECT 
                    c.id::text AS id_text,
                    c.phone_number,
                    c.month,
                    c.created_at
                FROM calls c
                JOIN latest_versions lv ON 
                    c.phone_number = lv.phone_number 
                    AND c.month = lv.month 
                    AND c.created_at = lv.latest_date
                WHERE c.operator_id = %s
            )
            SELECT 
                c.id::text AS id,
                c.month, 
                c.phone_number, 
                c.appeal_date,
                c.score, 
                c.comment,
                c.audio_path,
                c.is_draft,
                c.is_correction,
                TO_CHAR(c.created_at, 'YYYY-MM-DD HH24:MI') AS evaluation_date,
                c.scores,
                c.criterion_comments,
                d.id AS direction_id,
                d.name AS direction_name,
                d.criteria AS direction_criteria,
                d.has_file_upload AS direction_has_file_upload,
                u.name AS evaluator_name,
                c.created_at,
                c.sv_request,
                c.sv_request_comment,
                c.sv_request_by,
                su.name AS sv_request_by_name,
                TO_CHAR(c.sv_request_at, 'YYYY-MM-DD HH24:MI') AS sv_request_at,
                c.sv_request_approved,
                c.sv_request_approved_by,
                apu.name AS sv_request_approved_by_name,
                TO_CHAR(c.sv_request_approved_at, 'YYYY-MM-DD HH24:MI') AS sv_request_approved_at,
                NULL::numeric AS duration, -- у оценённых нет длительности
                FALSE AS is_imported
            FROM calls c
            JOIN latest_calls lc ON c.id::text = lc.id_text
            LEFT JOIN directions d ON c.direction_id = d.id  
            LEFT JOIN users u ON c.evaluator_id = u.id
            LEFT JOIN users su ON c.sv_request_by = su.id
            LEFT JOIN users apu ON c.sv_request_approved_by = apu.id
            WHERE c.operator_id = %s
        """
        params = [operator_id, operator_id, operator_id]
        if month:
            query += " AND c.month = %s"
            params.append(month)

        # добавляем неоценённые звонки из imported_calls
        query += """
            UNION ALL
            SELECT 
                ic.id::text AS id,
                ic.month,
                ic.phone_number,
                ic.datetime_raw AS appeal_date,
                NULL::numeric AS score,
                NULL::text AS comment,
                NULL::text AS audio_path,
                FALSE AS is_draft,
                FALSE AS is_correction,
                NULL::text AS evaluation_date,
                NULL::jsonb AS scores,
                NULL::jsonb AS criterion_comments,
                NULL::integer AS direction_id,
                NULL::text AS direction_name,
                NULL::jsonb AS direction_criteria,
                NULL::boolean AS direction_has_file_upload,
                NULL::text AS evaluator_name,
                ic.imported_at AS created_at,
                FALSE::boolean AS sv_request,
                NULL::text AS sv_request_comment,
                NULL::integer AS sv_request_by,
                NULL::text AS sv_request_by_name,
                NULL::text AS sv_request_at,
                FALSE::boolean AS sv_request_approved,
                NULL::integer AS sv_request_approved_by,
                NULL::text AS sv_request_approved_by_name,
                NULL::text AS sv_request_approved_at,
                ic.duration_sec::numeric AS duration, -- берем из imported_calls
                TRUE AS is_imported
            FROM imported_calls ic
            WHERE ic.operator_id = %s AND ic.status = 'not_evaluated'
        """
        params.append(operator_id)

        if month:
            query += " AND ic.month = %s"
            params.append(month)

        query += " ORDER BY created_at DESC"

        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [
                {
                    "id": row[0],
                    "month": row[1],
                    "phone_number": row[2],
                    "appeal_date": (
                        row[3].strftime('%Y-%m-%d %H:%M:%S')
                        if row[3] and hasattr(row[3], "strftime")
                        else (row[3] if row[3] else None)
                    ),
                    "score": float(row[4]) if row[4] is not None else None,
                    "comment": row[5],
                    "audio_path": row[6],
                    "is_draft": bool(row[7]),
                    "is_correction": bool(row[8]),
                    "evaluation_date": row[9],
                    "scores": row[10] if row[10] else [],
                    "criterion_comments": row[11] if row[11] else [],
                    "direction": {
                        "id": row[12],
                        "name": row[13],
                        "criteria": row[14] if row[14] else [],
                        "hasFileUpload": row[15] if row[15] is not None else True,
                    }
                    if row[12]
                    else None,
                    "evaluator": row[16] if row[16] else None,
                    "created_at": row[17],
                    "sv_request": bool(row[18]),
                    "sv_request_comment": row[19],
                    "sv_request_by": row[20],
                    "sv_request_by_name": row[21],
                    "sv_request_at": row[22],
                    "sv_request_approved": bool(row[23]),
                    "sv_request_approved_by": row[24],
                    "sv_request_approved_by_name": row[25],
                    "sv_request_approved_at": row[26],
                    "duration": float(row[27]) if row[27] is not None else None,
                    "is_imported": bool(row[28]),
                }
                for row in rows
            ]

        
    def get_operators_summary_for_month(self, month, supervisor_id=None):
        """
        Возвращает список операторов с количеством последних версий звонков за месяц.
        Последние версии определяются как MAX(created_at) для каждой комбинации:
        (phone_number, operator_id, month, appeal_date)
        Если supervisor_id задан — фильтрует операторов по этому SV.
        """
        query = """
        WITH latest_versions AS (
            -- для каждой пары (phone_number, operator_id, month, appeal_date) берем последний created_at
            SELECT
                phone_number,
                operator_id,
                month,
                appeal_date,
                MAX(created_at) AS latest_date
            FROM calls
            WHERE month = %s
            GROUP BY phone_number, operator_id, month, appeal_date
        ),
        latest_calls AS (
            -- берем сами записи (последние версии) для указанного месяца и appeal_date
            SELECT c.*
            FROM calls c
            JOIN latest_versions lv
            ON c.phone_number = lv.phone_number
            AND c.operator_id = lv.operator_id
            AND c.month = lv.month
            AND ( (c.appeal_date IS NULL AND lv.appeal_date IS NULL) OR (c.appeal_date = lv.appeal_date) )
            AND c.created_at = lv.latest_date
            WHERE c.month = %s
        ),
        counts AS (
            -- считаем количество последних версий звонков по оператору
            SELECT operator_id, COUNT(*) AS call_count
            FROM latest_calls
            GROUP BY operator_id
        )
        SELECT
            u.id AS operator_id,
            u.name AS operator_name,
            u.status,
            u.direction_id,
            d.name AS direction_name,
            u.supervisor_id,
            su.name AS supervisor_name,
            u.hire_date,
            COALESCE(c.call_count, 0) AS call_count
        FROM users u
        LEFT JOIN directions d ON u.direction_id = d.id
        LEFT JOIN users su ON u.supervisor_id = su.id
        LEFT JOIN counts c ON c.operator_id = u.id
        WHERE u.role = 'operator'
        """
        params = [month, month]

        if supervisor_id is not None:
            query += " AND u.supervisor_id = %s"
            params.append(supervisor_id)

        query += " ORDER BY u.name"

        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "status": row[2],
                    "direction_id": row[3],
                    "direction_name": row[4],
                    "supervisor_id": row[5],
                    "supervisor_name": row[6],
                    "hire_date": row[7].strftime('%d-%m-%Y') if row[7] else None,
                    "call_count": int(row[8])
                }
                for row in rows
            ]

    def update_user(self, user_id, field, value, changed_by=None):
        allowed_fields = ['direction_id', 'supervisor_id', 'status', 'rate', 'hire_date', 'name', 'gender', 'birth_date']
        if field not in allowed_fields:
            raise ValueError("Invalid field to update")
        
        with self._get_cursor() as cursor:
            # Fetch old value
            cursor.execute(f"SELECT {field} FROM users WHERE id = %s", (user_id,))
            old_value = cursor.fetchone()
            old_value = str(old_value[0]) if old_value and old_value[0] is not None else None
            
            # Update
            cursor.execute(f"UPDATE users SET {field} = %s WHERE id = %s RETURNING id", (value, user_id))
            updated = cursor.fetchone() is not None
            
            # Log history if updated
            if updated:
                cursor.execute("""
                    INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
                    VALUES (%s, %s, %s, %s, %s)
                """, (user_id, changed_by, field, old_value, str(value)))
            
            return updated

    def get_all_operators(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, direction_id, hire_date, supervisor_id, scores_table_url, hours_table_url
                FROM users 
                WHERE role = 'operator'
            """)
            return cursor.fetchall()

    def get_week_call_stats(self, operator_id, start_date, end_date):
        with self._get_cursor() as cursor:
            query = """
                WITH latest_versions AS (
                    SELECT 
                        phone_number,
                        month,
                        MAX(created_at) as latest_date
                    FROM calls
                    WHERE operator_id = %s
                    AND created_at >= %s
                    AND created_at <= %s
                    AND is_draft = FALSE
                    GROUP BY phone_number, month
                ),
                latest_calls AS (
                    SELECT 
                        c.id,
                        c.score
                    FROM calls c
                    JOIN latest_versions lv ON 
                        c.phone_number = lv.phone_number AND 
                        c.month = lv.month AND 
                        c.created_at = lv.latest_date
                    WHERE c.operator_id = %s
                    AND c.is_draft = FALSE
                )
                SELECT COUNT(*), AVG(score)::float
                FROM latest_calls
            """
            cursor.execute(query, (operator_id, start_date, end_date, operator_id))
            result = cursor.fetchone()
            count = result[0] or 0
            avg = result[1] or 0.0
            return count, avg

    def get_average_scores_for_period(self, start_date, end_date, operator_ids: Optional[List[int]] = None):
        """
        Возвращает среднюю оценку и количество оценок по операторам за выбранный период.

        Параметры:
        - start_date: начало периода (datetime или строка в формате SQL-совместимом, напр. 'YYYY-MM-DD' или full timestamp)
        - end_date: конец периода (inclusive)
        - operator_ids: опциональный список id операторов для фильтрации (если None — по всем операторам)

        Возвращает словарь:
        {
            "operators": { operator_id: {"count": int, "avg_score": float}, ... },
            "overall": {"count": total_count, "avg_score": overall_avg}
        }

        Реализация основана на логике последних версий оценок (по phone_number/operator_id/month)
        аналогично `get_week_call_stats`, но аггрегирует по операторам за период.
        """
        # normalize dates if strings were provided (leave as-is otherwise)
        sd = start_date
        ed = end_date
        params = [sd, ed]

        query = """
            WITH latest_versions AS (
                SELECT phone_number, operator_id, month, MAX(created_at) AS latest_date
                FROM calls
                WHERE is_draft = FALSE
                  AND created_at >= %s AND created_at <= %s
                GROUP BY phone_number, operator_id, month
            )
            SELECT c.operator_id, COUNT(*) AS cnt, AVG(c.score)::float AS avg_score
            FROM calls c
            JOIN latest_versions lv
              ON c.phone_number = lv.phone_number
             AND c.operator_id = lv.operator_id
             AND c.month = lv.month
             AND c.created_at = lv.latest_date
        """

        if operator_ids:
            query += "\nWHERE c.operator_id = ANY(%s)"
            params.append(operator_ids)

        query += "\nGROUP BY c.operator_id"

        with self._get_cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

        operators = {}
        total_count = 0
        total_weighted = 0.0
        for op_id, cnt, avg in rows:
            c = int(cnt or 0)
            a = float(avg) if avg is not None else 0.0
            operators[int(op_id)] = {"count": c, "avg_score": round(a, 2)}
            total_count += c
            total_weighted += a * c

        overall_avg = round((total_weighted / total_count), 2) if total_count > 0 else 0.0

        return {"operators": operators, "overall": {"count": total_count, "avg_score": overall_avg}}

    def set_user_active(self, user_id, status):
        # допустимые статусы
        allowed_statuses = {"active", "break", "training", "inactive", "tech", "iesigning"}
        if status not in allowed_statuses:
            return False  # неверный статус
    
        # преобразуем статус в boolean
        is_active = True if status == "active" else False
    
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users 
                SET is_active = %s
                WHERE id = %s AND role = 'operator'
                RETURNING id
            """, (is_active, user_id))
            return cursor.fetchone() is not None
    
    def get_active_operators(self, direction_name=None):
        with self._get_cursor() as cursor:
            if direction_name:
                cursor.execute("""
                    SELECT u.id, u.name, u.direction_id
                    FROM users u
                    JOIN directions d ON u.direction_id = d.id
                    WHERE u.role = 'operator' AND u.is_active = TRUE
                      AND d.name = %s AND d.is_active = TRUE
                    ORDER BY u.name
                """, (direction_name,))
            else:
                cursor.execute("""
                    SELECT id, name, direction_id 
                    FROM users 
                    WHERE role = 'operator' AND is_active = TRUE
                    ORDER BY name
                """)
            return [{"id": row[0], "name": row[1], "direction_id": row[2]} for row in cursor.fetchall()]

    
    def log_activity(self, operator_id, status):
        allowed_statuses = {"active", "break", "training", "inactive","tech","iesigning"}
        if status not in allowed_statuses:
            return False
    
        try:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO operator_activity_logs (operator_id, is_active)
                    VALUES (%s, %s)
                """, (operator_id, status))
            return True
        except Exception as e:
            logging.error(f"Error logging activity: {e}")
            return False
            
    def get_last_activity_status(self, operator_id):
        try:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    SELECT is_active
                    FROM operator_activity_logs
                    WHERE operator_id = %s
                    ORDER BY change_time DESC
                    LIMIT 1
                """, (operator_id,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            logging.error(f"Error fetching last activity status: {e}")
            return None

        
    def generate_monthly_report(self, supervisor_id, month=None, current_date=None):
        """
        Генерация месячного отчёта (xlsx).
        Включает:
        - Summary (как раньше);
        - All active (даты dd.mm.yyyy, ФИО с рамкой, ячейки часов >0 — серый B3B3B3);
        - Листы операторов: дата в формате dd.mm.yyyy в объединённой ячейке на день,
        длительности в числовом формате (часы, 2 знака), длительности окрашены в цвет статуса.
        """
        def sanitize_table_name(name):
            sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', (name or ""))
            if not sanitized or (not sanitized[0].isalpha() and sanitized[0] != '_'):
                sanitized = f"_{sanitized}"
            return sanitized[:255]

        def format_hms(duration):
            """Оставлю для совместимости — не используется для числовых полей."""
            if duration is None:
                return "N/A"
            total = int(duration.total_seconds())
            if total < 0:
                return "N/A"
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            return f"{h:02d}:{m:02d}:{s:02d}"

        try:
            if current_date is None:
                current_date = date.today()
            else:
                current_date = datetime.strptime(current_date, "%Y-%m-%d").date() if isinstance(current_date, str) else current_date

            if month is None:
                year = current_date.year
                mon = current_date.month
            else:
                try:
                    year, mon = map(int, month.split('-'))
                    if not (1 <= mon <= 12):
                        raise ValueError("Invalid month")
                except:
                    raise ValueError("Invalid month format. Use YYYY-MM")

            month_str = f"{year}-{mon:02d}"
            filename = f"monthly_report_supervisor_{supervisor_id}_{month_str}.xlsx"

            month_start_date = date(year, mon, 1)
            days_in_month = calendar.monthrange(year, mon)[1]
            month_end = date(year, mon, days_in_month)
            end_date = min(month_end, current_date)

            end_time = datetime.combine(end_date, dt_time(23, 59, 59))

            # Получаем данные
            with self._get_cursor() as cursor:
                cursor.execute("""
                    SELECT id, name
                    FROM users
                    WHERE supervisor_id = %s AND role = 'operator'
                """, (supervisor_id,))
                operators = cursor.fetchall()
                if not operators:
                    raise ValueError("No operators found for the given supervisor")
                operators_dict = {op[0]: op[1] for op in operators}

                cursor.execute("""
                    SELECT o.operator_id, o.change_time, o.is_active
                    FROM operator_activity_logs o
                    JOIN users u ON o.operator_id = u.id
                    WHERE u.supervisor_id = %s AND u.role = 'operator'
                    AND o.change_time >= %s
                    AND o.change_time < %s + INTERVAL '1 DAY'
                    ORDER BY o.operator_id, o.change_time
                """, (supervisor_id, month_start_date, end_date))
                all_logs = cursor.fetchall()

            # Группируем логи и считаем активации/деактивации для summary
            logs_per_op = defaultdict(list)
            counts_per_op = defaultdict(lambda: defaultdict(lambda: {'act': 0, 'deact': 0}))
            total_counts_per_op = defaultdict(lambda: {'act': 0, 'deact': 0})

            for log in all_logs:
                op_id, change_time, is_active = log
                dt = change_time.date()
                logs_per_op[op_id].append({'change_time': change_time, 'is_active': is_active, 'date': dt})
                if is_active == 'active':
                    counts_per_op[op_id][dt]['act'] += 1
                    total_counts_per_op[op_id]['act'] += 1
                else:
                    counts_per_op[op_id][dt]['deact'] += 1
                    total_counts_per_op[op_id]['deact'] += 1

            # Список дат для summary (от 1 числа до end_date)
            dates = [month_start_date + timedelta(days=i) for i in range((end_date - month_start_date).days + 1)]

            # Подготовим переводы статусов и заливки
            # перевод статусов, согласно вашему запросу:
            status_display = {
                'active': 'Активен',
                'tech': 'Перерыв',
                'break': 'Перерыв',
                'training': 'Тренинг',
                'iesigning': 'Подписание',
                'inactive': 'Завершил смену'
            }

            # стили и заливки
            total_fill = PatternFill(start_color="EDEDED", end_color="EDEDED", fill_type="solid")
            status_fill = PatternFill(start_color="F7F7F7", end_color="F7F7F7", fill_type="solid")
            green_fill = PatternFill(start_color="CFF5D0", end_color="CFF5D0", fill_type="solid")   # active
            orange_fill = PatternFill(start_color="FFE8C0", end_color="FFE8C0", fill_type="solid")  # break/tech
            yellow_fill = PatternFill(start_color="FFF9C4", end_color="FFF9C4", fill_type="solid")  # training
            purple_fill = PatternFill(start_color="E8D7F7", end_color="E8D7F7", fill_type="solid")  # tech/alt
            blue_fill = PatternFill(start_color="DCEEFF", end_color="DCEEFF", fill_type="solid")    # iesigning
            inactive_fill = PatternFill(start_color="FFDADA", end_color="FFDADA", fill_type="solid")# inactive
            grey_fill = PatternFill(start_color="B3B3B3", end_color="B3B3B3", fill_type="solid")     # All active >0

            # Соответствие статуса -> заливка (используем явное сопоставление)
            status_fill_map = {
                'active': green_fill,
                'iesigning': blue_fill,
                'break': orange_fill,
                'training': yellow_fill,
                'tech': orange_fill,      # вы просили tech = "Перерыв", поэтому даём тот же цвет что и break
                'inactive': inactive_fill
            }

            # порядок статусов для колонок итогов E..I (исключаем 'inactive')
            status_order = [
                ('active', status_display.get('active', 'active'), status_fill_map.get('active')),
                ('iesigning', status_display.get('iesigning', 'iesigning'), status_fill_map.get('iesigning')),
                ('break', status_display.get('break', 'break'), status_fill_map.get('break')),
                ('training', status_display.get('training', 'training'), status_fill_map.get('training')),
                ('tech', status_display.get('tech', 'tech'), status_fill_map.get('tech')),
            ]

            # --------- НОВЫЙ БЛОК: подсчёт суммарных секунд активности (active + iesigning) по дням для каждого оператора
            active_seconds_per_op_per_day = defaultdict(lambda: defaultdict(int))
            for op_id, logs in logs_per_op.items():
                logs_sorted = sorted(logs, key=lambda x: x['change_time'])
                for i, log in enumerate(logs_sorted):
                    dt = log['date']
                    if i < len(logs_sorted) - 1:
                        next_time = logs_sorted[i + 1]['change_time']
                    else:
                        next_time = end_time
                    duration = next_time - log['change_time']
                    # пропускаем дубликаты состояния
                    if i > 0 and log['is_active'] == logs_sorted[i - 1]['is_active']:
                        continue
                    if log['is_active'] in ('active', 'iesigning'):
                        active_seconds_per_op_per_day[op_id][dt] += int(duration.total_seconds())
            # --------- КОНЕЦ НОВОГО БЛОКА

            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = "Summary"

            # -------------------------
            # 1) Summary: заголовки и данные
            # -------------------------
            ws_summary.cell(1, 1).value = "ФИО"
            for col, dt in enumerate(dates, start=2):
                ws_summary.cell(1, col).value = dt.strftime("%Y-%m-%d")
                ws_summary.cell(1, col).alignment = Alignment(horizontal='center', vertical='center')
            last_date_col = 1 + len(dates)
            ws_summary.cell(1, last_date_col + 1).value = "Итого активаций"
            ws_summary.cell(1, last_date_col + 2).value = "Итого деактиваций"

            # шрифты для rich text (если доступны)
            green_font = InlineFont(color="00AA00") if InlineFont else None
            red_font = InlineFont(color="AA0000") if InlineFont else None
            default_font = InlineFont() if InlineFont else None

            # Заполняем summary
            row = 2
            for op_id, name in operators_dict.items():
                ws_summary.cell(row, 1).value = name
                for col, dt in enumerate(dates, start=2):
                    activations = counts_per_op[op_id][dt]['act']
                    deactivations = counts_per_op[op_id][dt]['deact']
                    cell = ws_summary.cell(row, col)
                    if CellRichText and TextBlock and green_font and red_font:
                        rt = CellRichText([
                            TextBlock(green_font, str(activations)),
                            TextBlock(default_font, " | "),
                            TextBlock(red_font, str(deactivations))
                        ])
                        cell.value = rt
                    else:
                        cell.value = f"{activations} | {deactivations}"
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrapText=True)
                # итоговые колонки
                act_cell = ws_summary.cell(row, last_date_col + 1)
                deact_cell = ws_summary.cell(row, last_date_col + 2)
                if CellRichText and TextBlock and green_font and red_font:
                    act_cell.value = CellRichText([TextBlock(green_font, str(total_counts_per_op[op_id]['act']))])
                    deact_cell.value = CellRichText([TextBlock(red_font, str(total_counts_per_op[op_id]['deact']))])
                else:
                    act_cell.value = total_counts_per_op[op_id]['act']
                    deact_cell.value = total_counts_per_op[op_id]['deact']
                act_cell.alignment = Alignment(horizontal='center', vertical='center')
                deact_cell.alignment = Alignment(horizontal='center', vertical='center')
                row += 1

            # легенда
            ws_summary.cell(row + 1, 1).value = "Легенда:"
            ws_summary.cell(row + 2, 1).value = "Зелёный — активации"
            ws_summary.cell(row + 3, 1).value = "Красный — деактивации"

            # стили summary: жирная шапка, границы, автоширины
            thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
            for c in range(1, last_date_col + 3):
                cell = ws_summary.cell(1, c)
                cell.font = Font(bold=True)
                cell.border = thin_border
            for r in range(1, row):
                for c in range(1, last_date_col + 3):
                    ws_summary.cell(r, c).border = thin_border
            ws_summary.column_dimensions['A'].width = 24
            for idx in range(2, last_date_col + 3):
                col_letter = ws_summary.cell(1, idx).column_letter
                ws_summary.column_dimensions[col_letter].width = 12
            ws_summary.freeze_panes = "A2"

            # -------------------------
            # НОВЫЙ ЛИСТ: All active (суммы часов активности по дням для каждого оператора)
            # -------------------------
            ws_all_active = wb.create_sheet(title="All active")
            # Заголовок: даты в формате dd.mm.yyyy
            ws_all_active.cell(1, 1).value = "ФИО"
            for col, dt in enumerate(dates, start=2):
                ws_all_active.cell(1, col).value = dt.strftime("%d.%m.%Y")
                ws_all_active.cell(1, col).alignment = Alignment(horizontal='center', vertical='center')
            total_col_idx = 1 + len(dates) + 1  # колонка для Итого (ч)
            ws_all_active.cell(1, total_col_idx).value = "Итого (ч)"

            # стиль шапки
            for c in range(1, total_col_idx + 1):
                cell = ws_all_active.cell(1, c)
                cell.font = Font(bold=True)
                cell.border = thin_border

            row = 2
            daily_totals_seconds = defaultdict(int)
            grand_total_seconds = 0

            for op_id in operators_dict.keys():
                name = operators_dict[op_id]
                name_cell = ws_all_active.cell(row, 1)
                name_cell.value = name
                name_cell.border = thin_border  # обводка ФИО
                name_cell.alignment = Alignment(horizontal='left', vertical='center')

                row_total_seconds = 0
                for col_idx, dt in enumerate(dates, start=2):
                    secs = active_seconds_per_op_per_day[op_id].get(dt, 0)
                    hours = secs / 3600.0
                    cell = ws_all_active.cell(row, col_idx)
                    cell.value = round(hours, 2)
                    cell.number_format = '0.00'
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    cell.border = thin_border
                    # если сумма больше 0 — красим в серый
                    if hours > 0:
                        cell.fill = grey_fill

                    row_total_seconds += secs
                    daily_totals_seconds[dt] += secs

                # итого по строке (оператору) в часах
                row_total_hours = row_total_seconds / 3600.0
                total_cell = ws_all_active.cell(row, total_col_idx)
                total_cell.value = round(row_total_hours, 2)
                total_cell.number_format = '0.00'
                total_cell.alignment = Alignment(horizontal='center', vertical='center')
                total_cell.border = thin_border
                if row_total_hours > 0:
                    total_cell.fill = grey_fill

                grand_total_seconds += row_total_seconds
                row += 1

            # Добавим строку итогов по дням (и общий итог)
            total_row = row
            tot_label_cell = ws_all_active.cell(total_row, 1)
            tot_label_cell.value = "Итого"
            tot_label_cell.font = Font(bold=True)
            tot_label_cell.border = thin_border
            tot_label_cell.alignment = Alignment(horizontal='left', vertical='center')

            for col_idx, dt in enumerate(dates, start=2):
                secs = daily_totals_seconds.get(dt, 0)
                hours = secs / 3600.0
                cell = ws_all_active.cell(total_row, col_idx)
                cell.value = round(hours, 2)
                cell.number_format = '0.00'
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border = thin_border
                if hours > 0:
                    cell.fill = grey_fill

            # общий итог всех операторов в часах
            grand_cell = ws_all_active.cell(total_row, total_col_idx)
            grand_cell.value = round(grand_total_seconds / 3600.0, 2)
            grand_cell.font = Font(bold=True)
            grand_cell.number_format = '0.00'
            grand_cell.alignment = Alignment(horizontal='center', vertical='center')
            grand_cell.border = thin_border
            if grand_total_seconds > 0:
                grand_cell.fill = grey_fill

            # автоширины и фиксация панелей
            ws_all_active.column_dimensions['A'].width = 24
            for idx in range(2, total_col_idx + 1):
                col_letter = ws_all_active.cell(1, idx).column_letter
                ws_all_active.column_dimensions[col_letter].width = 12
            ws_all_active.freeze_panes = "A2"

            # -------------------------
            # 2) Листы по операторам: события + одна строка итого на день с колонками итогов
            #    (E..I — ЧАСЫ с 2 знаками, J..N — секунды скрытые)
            # -------------------------
            for op_id, name in operators_dict.items():
                ws = wb.create_sheet(title=(name[:31] or f"op_{op_id}"))

                headers = [
                    "Дата", "Время / Описание", "Статус", "Длительность (ч)",
                    "Итого: Активен", "Итого: Подписание", "Итого: Перерыв", "Итого: Тренинг", "Итого: Тех",
                    "Итого Активен (сек)", "Итого Подписание (сек)", "Итого Перерыв (сек)", "Итого Тренинг (сек)", "Итого Тех (сек)"
                ]
                for col_idx, h in enumerate(headers, start=1):
                    cell = ws.cell(1, col_idx)
                    cell.value = h
                    cell.font = Font(bold=True)
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    cell.border = thin_border

                for col_letter in ['J', 'K', 'L', 'M', 'N']:
                    ws.column_dimensions[col_letter].hidden = True

                ws.freeze_panes = "A2"
                ws.column_dimensions['A'].width = 12
                ws.column_dimensions['B'].width = 36
                ws.column_dimensions['C'].width = 18
                ws.column_dimensions['D'].width = 14
                ws.column_dimensions['E'].width = 16
                ws.column_dimensions['F'].width = 16
                ws.column_dimensions['G'].width = 14
                ws.column_dimensions['H'].width = 14
                ws.column_dimensions['I'].width = 14

                logs = sorted(logs_per_op[op_id], key=lambda x: x['change_time'])
                current_row = 2
                current_day = None
                day_status_times = defaultdict(timedelta)  # суммарное timedelta по статусам для дня
                day_start_row = None  # для объединения дат

                def write_day_totals_row(r, day_dt, counts_dict, day_status_times_dict):
                    """
                    Строка 'Итого' для оператора за день.
                    A — пустая объединяемая ячейка
                    B — "Итого"
                    C — "В работе" (ярко-зелёная заливка)
                    D — итог рабочих часов (active + iesigning) в часах, 2 знака
                    E..I — часы по статусам (число часов, 2 знака), J..N — секунды
                    Возвращает следующий свободный row (r + 1).

                    ВАЖНО: НЕ присваивает cell.fill = None (openpyxl ожидает объект Fill).
                            Вместо этого fill задаётся только когда нужно.
                    """
                    # fallback на случай отсутствия глобальных стилей
                    try:
                        _ = total_fill
                    except NameError:
                        total_fill = PatternFill(start_color="EDEDED", end_color="EDEDED", fill_type="solid")
                    try:
                        _ = thin_border
                    except NameError:
                        thin_border = Border(left=Side(style='thin'), right=Side(style='thin'),
                                            top=Side(style='thin'), bottom=Side(style='thin'))
                    try:
                        _ = status_order
                    except NameError:
                        # default minimal status_order: (key, name, fill)
                        status_order = [
                            ('active', 'Активен', PatternFill(start_color="CFF5D0", end_color="CFF5D0", fill_type="solid")),
                            ('iesigning', 'Подписание', PatternFill(start_color="DCEEFF", end_color="DCEEFF", fill_type="solid")),
                            ('break', 'Перерыв', PatternFill(start_color="FFE8C0", end_color="FFE8C0", fill_type="solid")),
                            ('training', 'Тренинг', PatternFill(start_color="FFF9C4", end_color="FFF9C4", fill_type="solid")),
                            ('tech', 'Перерыв', PatternFill(start_color="FFE8C0", end_color="FFE8C0", fill_type="solid")),
                        ]

                    # жирная нижняя граница (для визуального раздела)
                    bottom_bold_border = Border(
                        left=Side(style='thin'),
                        right=Side(style='thin'),
                        top=Side(style='thin'),
                        bottom=Side(style='medium')
                    )

                    # насыщённый зелёный цвет для "В работе" и часов
                    strong_green_fill = PatternFill(start_color="66CC66", end_color="66CC66", fill_type="solid")

                    # A — пустая объединяемая ячейка (обычно объединена выше)
                    a_cell = ws.cell(r, 1)
                    a_cell.fill = total_fill
                    a_cell.border = bottom_bold_border
                    a_cell.alignment = Alignment(horizontal='center', vertical='center')

                    # B — "Итого"
                    b_cell = ws.cell(r, 2)
                    b_cell.value = "Итого"
                    b_cell.font = Font(bold=True)
                    b_cell.alignment = Alignment(horizontal='center', vertical='center')
                    b_cell.fill = total_fill
                    b_cell.border = bottom_bold_border

                    # C — "В работе" (ярко-зелёный)
                    c_cell = ws.cell(r, 3)
                    c_cell.value = "В работе"
                    c_cell.font = Font(bold=True)
                    c_cell.alignment = Alignment(horizontal='center', vertical='center')
                    c_cell.fill = strong_green_fill
                    c_cell.border = bottom_bold_border

                    # D — итог рабочих часов = active + iesigning (в часах)
                    active_td = day_status_times_dict.get('active', timedelta(0))
                    iesigning_td = day_status_times_dict.get('iesigning', timedelta(0))
                    total_work_sec = int((active_td + iesigning_td).total_seconds())
                    total_work_hours = round(total_work_sec / 3600.0, 2)

                    d_cell = ws.cell(r, 4)
                    d_cell.value = total_work_hours
                    d_cell.number_format = '0.00'
                    d_cell.alignment = Alignment(horizontal='center', vertical='center')
                    # ставим заливку ТОЛЬКО если > 0
                    if total_work_hours > 0:
                        d_cell.fill = strong_green_fill
                    d_cell.border = bottom_bold_border

                    # E..I (часы по статусам) и J..N (секунды) — заполняем и даём заливку по статусу, если >0
                    for idx, (status_key, status_name, fill) in enumerate(status_order):
                        dur_td = day_status_times_dict.get(status_key, timedelta(0))
                        dur_sec = int(dur_td.total_seconds())
                        dur_hours = round(dur_sec / 3600.0, 2)

                        col_read = 5 + idx   # E..I
                        col_sec = 10 + idx   # J..N

                        # часы (число)
                        rcell = ws.cell(r, col_read)
                        rcell.value = dur_hours
                        rcell.number_format = '0.00'
                        rcell.alignment = Alignment(horizontal='center', vertical='center')
                        # заливаем ТОЛЬКО если >0 и у нас есть валидная заливка
                        if dur_hours > 0 and isinstance(fill, PatternFill):
                            rcell.fill = fill
                        rcell.border = bottom_bold_border

                        # секунды (скрытые колонки) — всегда записываем число
                        scell = ws.cell(r, col_sec)
                        scell.value = dur_sec
                        scell.alignment = Alignment(horizontal='center', vertical='center')
                        scell.border = bottom_bold_border

                    return r + 1


                for i, log in enumerate(logs):
                    dt = log['date']
                    if current_day is None:
                        current_day = dt
                        day_start_row = current_row  # первый ряд для этого дня (включая будущий "Итого")
                    if dt != current_day:
                        # Перед записью итоговой строки — объединяем столбец A для предыдущего дня:
                        if day_start_row is not None:
                            merge_end = current_row  # текущ_row — место где будет написан "Итого"
                            ws.merge_cells(start_row=day_start_row, start_column=1, end_row=merge_end, end_column=1)
                            merged_cell = ws.cell(day_start_row, 1)
                            merged_cell.value = current_day.strftime("%d.%m.%Y")
                            merged_cell.alignment = Alignment(horizontal='center', vertical='center')

                        # записать строку итогов предыдущего дня
                        counts = counts_per_op[op_id][current_day]
                        current_row = write_day_totals_row(current_row, current_day, counts, day_status_times)
                        # сброс
                        day_status_times = defaultdict(timedelta)
                        # переходим к новому дню
                        current_day = dt
                        day_start_row = current_row  # первый ряд нового дня (включая будущий Итого)

                    # next_time
                    if i < len(logs) - 1:
                        next_time = logs[i + 1]['change_time']
                    else:
                        next_time = end_time

                    duration = next_time - log['change_time']

                    # дубликат состояния?
                    is_duplicate = (i > 0 and log['is_active'] == logs[i - 1]['is_active'])
                    if is_duplicate:
                        dur_hours = None
                    else:
                        dur_hours = round(duration.total_seconds() / 3600.0, 2)
                        if log['is_active'] != 'inactive':
                            day_status_times[log['is_active']] += duration

                    # пишем событие (A..D)
                    # A оставляем пустой — объединится позже
                    ws.cell(current_row, 2).value = log['change_time'].strftime("%H:%M:%S")
                    # статус — русская версия
                    status_key = log['is_active']
                    status_rus = status_display.get(status_key, status_key)
                    ws.cell(current_row, 3).value = status_rus

                    # длительность — ЧАСЫ числом с 2 знаками или пусто для дубликата
                    dur_cell = ws.cell(current_row, 4)
                    if dur_hours is None:
                        dur_cell.value = None
                    else:
                        dur_cell.value = dur_hours
                        dur_cell.number_format = '0.00'
                        # окрашиваем длительность в цвет статуса (если сопоставление есть)
                        fill = status_fill_map.get(status_key)
                        if fill is not None:
                            dur_cell.fill = fill

                    # заливка статуса в колонке C (сам статус)
                    cell_stat = ws.cell(current_row, 3)
                    fill_for_cell = status_fill_map.get(status_key)
                    if fill_for_cell is not None:
                        cell_stat.fill = fill_for_cell
                    else:
                        # fallback
                        if status_key == 'inactive':
                            cell_stat.fill = inactive_fill

                    # границы и выравнивание для строки события
                    for col_idx in range(1, 15):
                        ccell = ws.cell(current_row, col_idx)
                        ccell.border = thin_border
                        if col_idx == 4:
                            ccell.alignment = Alignment(horizontal='center', vertical='center')
                        else:
                            ccell.alignment = Alignment(horizontal='left', vertical='center')

                    current_row += 1

                # итоги для последнего дня
                if current_day is not None:
                    if day_start_row is not None:
                        merge_end = current_row  # сюда запишется "Итого"
                        ws.merge_cells(start_row=day_start_row, start_column=1, end_row=merge_end, end_column=1)
                        merged_cell = ws.cell(day_start_row, 1)
                        merged_cell.value = current_day.strftime("%d.%m.%Y")
                        merged_cell.alignment = Alignment(horizontal='center', vertical='center')

                    counts = counts_per_op[op_id][current_day]
                    current_row = write_day_totals_row(current_row, current_day, counts, day_status_times)

                # добавляем таблицу, охватывающую A..N
                if current_row > 2:
                    last_row = current_row - 1
                    sanitized_name = sanitize_table_name(f"{name}_{op_id}")
                    tab_ref = f"A1:N{last_row}"
                    tab_op = Table(displayName=f"Table_{sanitized_name}", ref=tab_ref)
                    style_op = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False, showRowStripes=True)
                    tab_op.tableStyleInfo = style_op
                    ws.add_table(tab_op)

            # Сохранение в BytesIO
            output = BytesIO()
            wb.save(output)
            output.seek(0)
            content = output.getvalue()
            return filename, content

        except Exception as e:
            logging.error(f"Error generating report: {e}")
            return None, None



    def add_training(self, operator_id, training_date, start_time, end_time, reason, comment, created_by, count_in_hours=True):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO trainings (operator_id, training_date, start_time, end_time, reason, comment, created_by, count_in_hours)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (operator_id, training_date, start_time, end_time, reason, comment, created_by, count_in_hours))
            return cursor.fetchone()[0]


    def get_trainings(self, requester_id=None, month=None):
        """
        Получить тренинги для оператора/всех, в зависимости от роли requester_id.
        Если operator_id указан — только для него.
        Если requester_id — определяет роль и фильтрует:
            - admin, sv: все тренинги (с фильтром по month, если указан)
            - operator: только свои тренинги (с фильтром по month, если указан)
        """
        # Получаем роль requester_id
        role = None
        if requester_id:
            with self._get_cursor() as cursor:
                cursor.execute("SELECT role FROM users WHERE id = %s", (requester_id,))
                res = cursor.fetchone()
                if res:
                    role = res[0]
        query = """
            SELECT t.id, t.operator_id, t.training_date, t.start_time, t.end_time, t.reason, t.comment, t.created_at, cb.name as created_by_name, t.count_in_hours
            FROM trainings t
            JOIN users u ON t.operator_id = u.id
            LEFT JOIN users cb ON t.created_by = cb.id
        """
        params = []
        where_clauses = []
        if month:
            where_clauses.append("TO_CHAR(t.training_date, 'YYYY-MM') = %s")
            params.append(month)
        if role == "operator":
            where_clauses.append("t.operator_id = %s")
            params.append(requester_id)
        # Для admin — без ограничений, кроме month
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY t.training_date DESC, t.start_time DESC"
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "id": row[0],
                    "operator_id": row[1],
                    "date": row[2].strftime('%Y-%m-%d'),
                    "start_time": row[3].strftime('%H:%M'),
                    "end_time": row[4].strftime('%H:%M'),
                    "reason": row[5],
                    "comment": row[6],
                    "created_at": row[7].strftime('%Y-%m-%d %H:%M'),
                    "created_by_name": row[8] if row[8] else "System",
                    "count_in_hours": bool(row[9])
                } for row in cursor.fetchall()
            ]
    
    def get_user_history(self, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT uh.id, uh.field_changed, uh.old_value, uh.new_value, uh.changed_at, u.name AS changed_by_name
                FROM user_history uh
                LEFT JOIN users u ON uh.changed_by = u.id
                WHERE uh.user_id = %s
                ORDER BY uh.changed_at DESC
            """, (user_id,))
            return [
                {
                    "id": row[0],
                    "field": row[1],
                    "old_value": row[2],
                    "new_value": row[3],
                    "changed_at": row[4].strftime('%Y-%m-%d %H:%M:%S'),
                    "changed_by": row[5] or "System"
                } for row in cursor.fetchall()
            ]

    def update_training(self, training_id, training_date=None, start_time=None, end_time=None, reason=None, comment=None, count_in_hours=None):
        updates = []
        params = []
        if training_date:
            updates.append("training_date = %s"); params.append(training_date)
        if start_time:
            updates.append("start_time = %s"); params.append(start_time)
        if end_time:
            updates.append("end_time = %s"); params.append(end_time)
        if reason:
            updates.append("reason = %s"); params.append(reason)
        if comment is not None:
            updates.append("comment = %s"); params.append(comment)
        if count_in_hours is not None:
            updates.append("count_in_hours = %s"); params.append(count_in_hours)

        if not updates:
            return False

        query = f"UPDATE trainings SET {', '.join(updates)} WHERE id = %s RETURNING id"
        params.append(training_id)

        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone() is not None

    def delete_training(self, training_id):
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM trainings WHERE id = %s RETURNING id", (training_id,))
            return cursor.fetchone() is not None

    def generate_users_report(self, current_date=None):
        """
        Generates an Excel report of all operators with columns: ФИО | Логин | Направление | Супервайзер | Статус | Ставка | Дата принятия.
        
        :param current_date: Optional, current date for filename (defaults to today).
        :return: (filename, content) or (None, None) on error.
        """
        try:
            if current_date is None:
                current_date = date.today()
            else:
                current_date = datetime.strptime(current_date, "%Y-%m-%d").date() if isinstance(current_date, str) else current_date
            
            filename = f"users_report_{current_date.strftime('%Y-%m-%d')}.xlsx"
            
            # Fetch all operators with required data (added u.login)
            with self._get_cursor() as cursor:
                cursor.execute("""
                    SELECT u.name, u.login, COALESCE(d.name, 'N/A') as direction, COALESCE(s.name, 'N/A') as supervisor,
                        u.status, u.rate, u.hire_date, u.supervisor_id
                    FROM users u
                    LEFT JOIN directions d ON u.direction_id = d.id
                    LEFT JOIN users s ON u.supervisor_id = s.id
                    WHERE u.role = 'operator'
                    ORDER BY s.name, u.name
                """)
                all_operators = cursor.fetchall()
            
            if not all_operators:
                logging.warning("No operators found for users report")
                return None, None
            
            # Group by supervisor
            operators_by_supervisor = defaultdict(list)
            supervisors = {}  # supervisor_id -> name
            for row in all_operators:
                # note: row structure: name, login, direction, supervisor, status, rate, hire_date, sup_id
                name, login, direction, supervisor, status, rate, hire_date, sup_id = row
                operators_by_supervisor[sup_id].append((name, login, direction, supervisor, status, rate, hire_date))
                if sup_id and supervisor != 'N/A':
                    supervisors[sup_id] = supervisor
            
            # Create workbook
            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = "Summary"
            
            # Headers for summary (added "Логин")
            headers = ["ФИО", "Логин", "Направление", "Супервайзер", "Статус", "Ставка", "Дата принятия"]
            for col, header in enumerate(headers, start=1):
                cell = ws_summary.cell(1, col)
                cell.value = header
                cell.font = Font(bold=True)
            
            # Fill summary data
            row = 2
            for op in all_operators:
                # op: name, login, direction, supervisor, status, rate, hire_date, sup_id
                name, login, direction, supervisor, status, rate, hire_date = op[:7]
                ws_summary.cell(row, 1).value = name
                ws_summary.cell(row, 2).value = login
                ws_summary.cell(row, 3).value = direction
                ws_summary.cell(row, 4).value = supervisor
                ws_summary.cell(row, 5).value = status
                ws_summary.cell(row, 6).value = float(rate) if rate else 1.0
                ws_summary.cell(row, 7).value = hire_date.strftime('%Y-%m-%d') if hire_date else 'N/A'
                row += 1
            
            # Add table to summary (expanded to G)
            tab_summary = Table(displayName="SummaryTable", ref=f"A1:G{row-1}")
            style = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=True, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
            tab_summary.tableStyleInfo = style
            ws_summary.add_table(tab_summary)
            
            # Auto-adjust columns A..G
            for col in ['A', 'B', 'C', 'D', 'E', 'F', 'G']:
                ws_summary.column_dimensions[col].auto_size = True
            
            # Create per-supervisor sheets
            for sup_id, ops in operators_by_supervisor.items():
                sup_name = supervisors.get(sup_id, "No Supervisor")
                sheet_title = sup_name[:31]  # Truncate to Excel sheet name limit
                ws = wb.create_sheet(title=sheet_title)
                
                # Headers
                for col, header in enumerate(headers, start=1):
                    cell = ws.cell(1, col)
                    cell.value = header
                    cell.font = Font(bold=True)
                
                # Fill data
                row = 2
                for name, login, direction, supervisor, status, rate, hire_date in ops:
                    ws.cell(row, 1).value = name
                    ws.cell(row, 2).value = login
                    ws.cell(row, 3).value = direction
                    ws.cell(row, 4).value = supervisor
                    ws.cell(row, 5).value = status
                    ws.cell(row, 6).value = float(rate) if rate else 1.0
                    ws.cell(row, 7).value = hire_date.strftime('%Y-%m-%d') if hire_date else 'N/A'
                    row += 1
                
                # Add table
                if row > 2:
                    tab = Table(displayName=f"Table_{sheet_title.replace(' ', '_')}", ref=f"A1:G{row-1}")
                    tab.tableStyleInfo = style
                    ws.add_table(tab)
                
                # Auto-adjust columns A..G
                for col in ['A', 'B', 'C', 'D', 'E', 'F', 'G']:
                    ws.column_dimensions[col].auto_size = True
            
            # Save to BytesIO
            output = BytesIO()
            wb.save(output)
            output.seek(0)
            content = output.getvalue()
            
            return filename, content
        
        except Exception as e:
            logging.error(f"Error generating users report: {e}")
            return None, None

    def generate_excel_report_from_view(self,
        operators: List[Dict[str, Any]],
        trainings_map: Dict[int, Dict[int, List[Dict[str, Any]]]],
        month: str,  # 'YYYY-MM'
        filename: str = None,
        include_supervisor: bool = False
    ) -> Tuple[str, bytes]:
        """
        Генерирует xlsx с листами: Отработанные часы, Перерыв, Звонки, Эффективность, Тренинги.

        Правила форматирования (по заданию пользователя):
        - Округлять все числа до 1 знака после запятой, КРОМЕ полей "Ставка" (rate) и "Норма часов" (norm_hours) — они оставляются как есть.
        - Внутри таблицы по дням значение 0 отображается как целое 0 (не 0.0).
        - Ячейки по дням, где значение > 0, закрашивать светло-серым (теперь чуть темнее).
        - Calls (количество звонков) остаются целыми числами.
        - Везде, где значение должно быть процентом, добавлять знак "%".
        - Добавить границы ко всем ячейкам таблиц.
        """

        operators = operators["operators"]

        # If requested, ensure each operator has `supervisor_name` populated.
        if include_supervisor:
            # collect supervisor ids that don't already have a name
            sup_ids = []
            for op in operators:
                if not op.get('supervisor_name') and op.get('supervisor_id'):
                    try:
                        sup_ids.append(int(op.get('supervisor_id')))
                    except Exception:
                        continue
            sup_ids = list(set(sup_ids))
            sup_map = {}
            if sup_ids:
                try:
                    with self._get_cursor() as cursor:
                        cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (sup_ids,))
                        sup_map = {r[0]: r[1] for r in cursor.fetchall()}
                except Exception:
                    logging.exception("Error fetching supervisors for report")
            # fill supervisor_name where missing
            for op in operators:
                if not op.get('supervisor_name') and op.get('supervisor_id'):
                    try:
                        op['supervisor_name'] = sup_map.get(int(op.get('supervisor_id')))
                    except Exception:
                        op['supervisor_name'] = None

        # Фильтруем уволенных операторов — оставляем только тех, у кого запись об
        # увольнении (field_changed='status', new_value='fired') попадает в выбранный месяц.
        try:
            # month уже распаршен ниже, но нам нужны year/mon для границ месяца — вычислим их сейчас
            year, mon = map(int, month.split('-'))
            month_start = date(year, mon, 1)
            days_in_month = calendar.monthrange(year, mon)[1]
            month_end = date(year, mon, days_in_month)
            next_month_start = month_end + timedelta(days=1)

            # собираем кандидатов со статусом 'fired'
            fired_candidates = [op.get('operator_id') for op in operators if (op.get('status') or '').lower() == 'fired']
            if fired_candidates:
                # Берём все записи об увольнении для кандидатов и затем проверяем дату увольнения
                # — включаем оператора в отчёт для месяца, если дата увольнения >= начала выбранного месяца.
                with self._get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT user_id, changed_at
                        FROM user_history
                        WHERE user_id = ANY(%s)
                          AND field_changed = 'status'
                          AND lower(new_value) = 'fired'
                        """,
                        (fired_candidates,)
                    )
                    rows = cursor.fetchall()
                allowed_fired_ids = set()
                for r in rows:
                    try:
                        uid = r[0]
                        changed_at = r[1]
                        if changed_at is None:
                            continue
                        # приведение к date
                        ch_date = changed_at.date() if hasattr(changed_at, 'date') else changed_at
                        if ch_date >= month_start:
                            allowed_fired_ids.add(uid)
                    except Exception:
                        continue
            else:
                allowed_fired_ids = set()

            # окончательный фильтр: включаем всех не-уволенных и только отфильтрованных уволенных
            operators = [op for op in operators if (op.get('status') or '').lower() != 'fired' or op.get('operator_id') in allowed_fired_ids]
        except Exception:
            # в случае ошибки фильтрации — не ломаем генерацию отчёта, оставляем исходный список
            logging.exception("Error filtering fired operators by dismissal date; proceeding without filter")

        FILL_POS = PatternFill(fill_type='solid', start_color='b3b3b3')  # чуть темнее серый
        THIN = Side(style='thin')
        BORDER_ALL = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

        def _make_header(ws, headers: List[str]):
            thin = Side(style='thin')
            border = Border(left=thin, right=thin, top=thin, bottom=thin)
            bold = Font(bold=True)
            for i, h in enumerate(headers, start=1):
                cell = ws.cell(1, i)
                cell.value = h
                cell.font = bold
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border = border

        def parse_time_to_minutes(t: str):
            if not t:
                return None
            try:
                parts = t.strip().split(':')
                hh = int(parts[0])
                mm = int(parts[1]) if len(parts) > 1 else 0
                return hh * 60 + mm
            except Exception:
                return None

        def compute_training_duration_hours(t: Dict[str, Any]) -> float:
            try:
                if t.get('duration_minutes') is not None:
                    return round(float(t['duration_minutes']) / 60.0, 2)
                if t.get('duration_hours') is not None:
                    return round(float(t['duration_hours']), 2)
                s = parse_time_to_minutes(t.get('start_time'))
                e = parse_time_to_minutes(t.get('end_time'))
                if s is None or e is None:
                    return 0.0
                diff = e - s
                if diff < 0:
                    diff += 24 * 60
                return round(diff / 60.0, 2)
            except Exception:
                return 0.0

        def fmt_day_value(metric_key: str, value: Any):
            """Формат для значений в столбцах по дням.
            - calls -> int
            - for hours/perc/eff -> round to 1 decimal; show 0 as integer 0
            """
            if metric_key == 'calls':
                try:
                    return int(value or 0)
                except Exception:
                    return 0
            try:
                num = float(value or 0)
            except Exception:
                return 0
            # represent exactly zero as int 0
            if abs(num) < 1e-9:
                return 0
            return round(num, 2)

        def fmt_total_value(metric_key: str, value: Any):
            """Формат для итоговых/доп. колонок (итого, проценты и т.п.).
            - для calls оставляем int
            - для остальных округляем до 1 знака (кроме rate и norm handled separately)
            """
            if value is None:
                return None
            if metric_key == 'calls':
                try:
                    return int(value or 0)
                except Exception:
                    return 0
            try:
                num = float(value)
            except Exception:
                return None
            return round(num, 2)

        logging.info(month)
        year, mon = map(int, month.split('-'))
        days_in_month = calendar.monthrange(year, mon)[1]
        days = list(range(1, days_in_month + 1))

        wb = Workbook()
        default = wb.active
        wb.remove(default)

        def set_cell(ws, r, c, value, align_center=True, fill=None):
            cell = ws.cell(r, c)
            cell.value = value
            if align_center:
                cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = BORDER_ALL
            if fill:
                cell.fill = fill
            return cell

        def build_generic_sheet(key: str, label: str, metric_key: str, is_hour=True, format_fn=None, extra_cols=None):
            ws = wb.create_sheet(title=label[:31])
            # Build headers with optional supervisor column
            headers = ["Оператор"]
            if include_supervisor:
                headers.append("Супервайзер")
            headers += ["Ставка", "Норма часов (ч)"] + [f"{d:02d}.{mon:02d}" for d in days] + ["Итого"]
            if extra_cols:
                headers += [c[0] for c in extra_cols]
            _make_header(ws, headers)

            # column indexes depending on presence of supervisor column
            rate_col = 2 + (1 if include_supervisor else 0)
            norm_col = rate_col + 1
            day_start_col = norm_col + 1

            row = 2
            for op in operators:
                daily = op.get('daily', {})
                name = op.get('name') or f"op_{op.get('operator_id')}"
                set_cell(ws, row, 1, name, align_center=False)
                if include_supervisor:
                    sup_name = op.get('supervisor_name') or ""
                    set_cell(ws, row, 2, sup_name, align_center=False)
                # Ставка и Норма оставляем без изменения/округления
                set_cell(ws, row, rate_col, float(op.get('rate') or 0), align_center=False)
                set_cell(ws, row, norm_col, float(op.get('norm_hours') or 0), align_center=False)
                total = 0.0
                totals = { 'work_time': 0.0, 'calls': 0, 'efficiency': 0.0 }
                for c_idx, day in enumerate(days, start=day_start_col):
                    dkey = str(day)
                    if metric_key == 'trainings':
                        set_cell(ws, row, c_idx, "")
                    else:
                        d = daily.get(dkey)
                        raw_v = None
                        if d:
                            raw_v = d.get(metric_key, 0)
                        cell_val = fmt_day_value(metric_key, raw_v)
                        # apply fill if >0
                        fill = FILL_POS if (isinstance(cell_val, (int, float)) and cell_val > 0) else None
                        set_cell(ws, row, c_idx, cell_val, fill=fill)
                        if metric_key == 'calls':
                            totals['calls'] += int(cell_val or 0)
                            total += int(cell_val or 0)
                        else:
                            num_for_tot = float(raw_v or 0)
                            total += num_for_tot
                            if metric_key == 'work_time':
                                totals['work_time'] += num_for_tot
                            if metric_key == 'efficiency':
                                totals['efficiency'] += num_for_tot
                # total cell
                total_col = day_start_col + len(days)
                set_cell(ws, row, total_col, fmt_total_value(metric_key, total))

                # extra cols
                if extra_cols:
                    for i, (_, fn) in enumerate(extra_cols, start=1):
                        val = fn(op, totals)
                        # if fn returns percent-string already, keep
                        if isinstance(val, str):
                            set_cell(ws, row, total_col + i, val)
                        else:
                            set_cell(ws, row, total_col + i, fmt_total_value(metric_key, val))

                row += 1

            ws.column_dimensions['A'].width = 24
            for i in range(2, len(headers) + 1):
                col = ws.cell(1, i).column_letter
                ws.column_dimensions[col].width = 12

        def build_work_time_sheet():
            ws = wb.create_sheet(title='Отработанные часы'[:31])
            headers = ["Оператор"]
            if include_supervisor:
                headers.append("Супервайзер")
            headers += ["Ставка", "Норма часов (ч)"] + [f"{d:02d}.{mon:02d}" for d in days] + ["Итого часов", "С выч. тренинга", "Вып нормы (%)", "Выработка"]
            _make_header(ws, headers)
            row = 2
            for op in operators:
                daily = op.get('daily', {})
                name = op.get('name') or f"op_{op.get('operator_id')}"
                set_cell(ws, row, 1, name, align_center=False)
                if include_supervisor:
                    sup_name = op.get('supervisor_name') or ""
                    set_cell(ws, row, 2, sup_name, align_center=False)
                rate_col = 2 + (1 if include_supervisor else 0)
                norm_col = rate_col + 1
                set_cell(ws, row, rate_col, float(op.get('rate') or 0), align_center=False)
                norm = float(op.get('norm_hours') or 0)
                set_cell(ws, row, norm_col, norm, align_center=False)

                total_work = 0.0
                total_counted_trainings = 0.0

                day_start = norm_col + 1
                for c_idx, day in enumerate(days, start=day_start):
                    dkey = str(day)
                    work_val = 0.0
                    d = daily.get(dkey)
                    if d:
                        work_val = float(d.get('work_time') or 0.0)
                    # Рассчитываем зачётные часы тренинга для дня и добавляем их к дневному показателю
                    trainings_for_day = trainings_map.get(op.get('operator_id'), {}).get(day, []) if trainings_map else []
                    counted_for_day = 0.0
                    for t in trainings_for_day:
                        dur = compute_training_duration_hours(t)
                        if t.get('count_in_hours'):
                            counted_for_day += dur

                    # Сохраняем в суммарные показатели отдельно, итоговая ячейка по дню — work + trainings
                    total_work += work_val
                    total_counted_trainings += counted_for_day
                    combined = work_val + counted_for_day
                    cell_val = fmt_day_value('work_time', combined)
                    fill = FILL_POS if (isinstance(cell_val, (int, float)) and cell_val > 0) else None
                    set_cell(ws, row, c_idx, cell_val, fill=fill)

                itogo_chasov = total_work + total_counted_trainings
                base_total_col = day_start + len(days)
                set_cell(ws, row, base_total_col, fmt_total_value('work_time', itogo_chasov))
                set_cell(ws, row, base_total_col + 1, fmt_total_value('work_time', total_work))
                if norm and norm != 0:
                    percent = round((itogo_chasov / norm) * 100, 2)
                    percent_display = f"{percent}%"
                else:
                    percent_display = None
                set_cell(ws, row, base_total_col + 2, percent_display)
                set_cell(ws, row, base_total_col + 3, fmt_total_value('work_time', round(norm - itogo_chasov, 2) if norm is not None else None))

                row += 1

            ws.column_dimensions['A'].width = 24
            for i in range(2, len(headers) + 1):
                col = ws.cell(1, i).column_letter
                ws.column_dimensions[col].width = 14

        def build_calls_sheet():
            def kvz_fn(op, totals):
                work_hours = 0.0
                daily = op.get('daily', {})
                for dnum in daily.values():
                    work_hours += float(dnum.get('work_time') or 0.0)
                calls = totals.get('calls', 0)
                if work_hours and work_hours != 0:
                    return round(calls / work_hours, 1)
                return None

            build_generic_sheet('calls', 'Звонки', 'calls', is_hour=False, extra_cols=[('КВЗ', kvz_fn)])

        def build_efficiency_sheet():
            def otn_fn(op, totals):
                sum_work = 0.0
                sum_eff = totals.get('efficiency', 0.0)
                daily = op.get('daily', {})
                for dnum in daily.values():
                    sum_work += float(dnum.get('work_time') or 0.0)
                if sum_work and sum_work != 0:
                    val = round((sum_eff / sum_work) * 100, 1)
                    return f"{val}%"
                return None

            build_generic_sheet('efficiency', 'Эффективность', 'efficiency', is_hour=True, extra_cols=[('Отн.', otn_fn)])

        def build_fines_sheet():
            ws_f = wb.create_sheet(title='Штрафы'[:31])
            headers = ["ФИО"]
            if include_supervisor:
                headers.append("Супервайзер")
            headers += [
                "Ставка",
                "Кол-во Опозданий",
                "Минуты",
                "Сумма Опозданий",
                "Корп такси",
                "Кол-во Не выход",
                "Сумма Не выход",
                "Прокси Карта",
                "Другое",
                "Итого"   # <--- НОВАЯ КОЛОНКА
            ]
            _make_header(ws_f, headers)

            row = 2
            def fmt_amt(x):
                try:
                    return round(float(x or 0.0), 1)
                except Exception:
                    return 0.0

            for op in operators:
                name = op.get('name') or f"op_{op.get('operator_id')}"
                set_cell(ws_f, row, 1, name, align_center=False)

                col_idx = 2
                if include_supervisor:
                    sup_name = op.get('supervisor_name') or ""
                    set_cell(ws_f, row, col_idx, sup_name, align_center=False)
                    col_idx += 1

                rate = op.get('rate') or 0
                set_cell(ws_f, row, col_idx, float(rate), align_center=False)
                col_idx += 1

                fines_map = op.get('daily', {})

                count_late = 0
                minutes_late = 0
                sum_late = 0.0
                sum_korp = 0.0
                count_no_show = 0
                sum_no_show = 0.0
                sum_proxy = 0.0
                sum_other = 0.0

                for day_entry in fines_map.values():
                    fines_list = day_entry.get('fines', []) if isinstance(day_entry, dict) else []
                    for f in fines_list:
                        reason = (f.get('reason') or '').strip()
                        amt = float(f.get('amount') or 0.0)
                        rl = reason.lower()

                        if rl == 'опоздание':
                            count_late += 1
                            minutes = int(f.get('minutes')) if f.get('minutes') is not None else int(round(amt / 50)) if amt else 0
                            minutes_late += minutes
                            sum_late += amt
                        elif 'корп' in rl and 'такси' in rl:
                            sum_korp += amt
                        elif rl == 'не выход' or 'не выход' in rl:
                            count_no_show += 1
                            sum_no_show += amt
                        elif 'прокси' in rl:
                            sum_proxy += amt
                        else:
                            sum_other += amt

                # Итоговая сумма штрафов
                total_fines = sum_late + sum_korp + sum_no_show + sum_proxy + sum_other

                # Записываем данные
                set_cell(ws_f, row, col_idx, int(count_late)); col_idx += 1
                set_cell(ws_f, row, col_idx, int(minutes_late)); col_idx += 1
                set_cell(ws_f, row, col_idx, fmt_amt(sum_late)); col_idx += 1
                set_cell(ws_f, row, col_idx, fmt_amt(sum_korp)); col_idx += 1
                set_cell(ws_f, row, col_idx, int(count_no_show)); col_idx += 1
                set_cell(ws_f, row, col_idx, fmt_amt(sum_no_show)); col_idx += 1
                set_cell(ws_f, row, col_idx, fmt_amt(sum_proxy)); col_idx += 1
                set_cell(ws_f, row, col_idx, fmt_amt(sum_other)); col_idx += 1

                # Новая колонка — ИТОГО
                set_cell(ws_f, row, col_idx, fmt_amt(total_fines)); col_idx += 1

                row += 1

            ws_f.column_dimensions['A'].width = 28
            for i in range(2, len(headers) + 1):
                col = ws_f.cell(1, i).column_letter
                ws_f.column_dimensions[col].width = 14

        build_work_time_sheet()
        build_generic_sheet('break_time', 'Перерыв', 'break_time', is_hour=True)
        build_calls_sheet()
        build_efficiency_sheet()
        build_fines_sheet()

        ws_t = wb.create_sheet(title='Тренинги'[:31])

        headers = ["Оператор"]
        if include_supervisor:
            headers.append("Супервайзер")
        headers += [f"{d:02d}.{mon:02d}" for d in days] + ["Всего (ч)"]
        _make_header(ws_t, headers)

        def fmt_num(n):
            """Форматируем число с одной цифрой после запятой, заменяем точку на запятую."""
            return f"{n:.1f}".replace('.', ',')

        # Заполняем строки — показываем по дням только зачётные часы и одну итоговую колонку "Всего (ч)".
        row_counted = 2

        for op in operators:
            name = op.get('name') or f"op_{op.get('operator_id')}"
            op_id = op.get('operator_id')

            # Берём все тренинги оператора (словарь day -> list)
            op_trainings = trainings_map.get(op_id) or {}

            # Инициализация итогов
            total_all = 0.0

            # Сначала пройдем все дни, чтобы посчитать общие итоги
            for day in days:
                arr = op_trainings.get(day, []) if isinstance(op_trainings, dict) else []
                for t in arr:
                    dur = compute_training_duration_hours(t)
                    total_all += dur

            # --- Заполнение вкладки "Тренинги" ---
            set_cell(ws_t, row_counted, 1, name, align_center=False)
            if include_supervisor:
                sup_name = op.get('supervisor_name') or ""
                set_cell(ws_t, row_counted, 2, sup_name, align_center=False)
            day_start = 2 + (1 if include_supervisor else 0)
            for c_idx, day in enumerate(days, start=day_start):
                arr = op_trainings.get(day, []) if isinstance(op_trainings, dict) else []
                counted = 0.0
                for t in arr:
                    if t.get('count_in_hours'):
                        counted += compute_training_duration_hours(t)
                if counted == 0:
                    set_cell(ws_t, row_counted, c_idx, "")
                else:
                    set_cell(ws_t, row_counted, c_idx, fmt_num(counted), fill=FILL_POS)

            # Итоги по строке — только Всего (ч)
            total_col = len(headers)
            set_cell(ws_t, row_counted, total_col, fmt_num(total_all))
            row_counted += 1

        # Настройка ширины колонок для вкладки Тренинги
        ws_t.column_dimensions['A'].width = 24
        for i in range(2, 3 + len(days)):
            col = ws_t.cell(1, i).column_letter
            ws_t.column_dimensions[col].width = 14

        out = BytesIO()
        wb.save(out)
        out.seek(0)
        content = out.getvalue()
        if filename is None:
            filename = f"report_{month}.xlsx"
        return filename, content

    def generate_excel_report_all_operators_from_view(self,
        operators: List[Dict[str, Any]],
        trainings_map: Dict[int, Dict[int, List[Dict[str, Any]]]],
        month: str,  # 'YYYY-MM'
        filename: str = None
        ) -> Tuple[str, bytes]:
        """
        Быстрая версия: дополняет операторов supervisor_name и вызывает
        generate_excel_report_from_view(include_supervisor=True).
        """
        # operators может быть {"operators": [...]} или список
        ops = operators["operators"] if isinstance(operators, dict) and "operators" in operators else operators
        logging.info("First operator for all-operators report: %s", ops[0])
        # Deduplicate ids и получить map одним запросом
        sup_ids = list({int(op.get('supervisor_id')) for op in ops if op.get('supervisor_id')})
        logging.info("Fetching supervisors for all-operators report: %s", sup_ids)
        sup_map = {}
        if sup_ids:
            try:
                with self._get_cursor() as cursor:
                    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (sup_ids,))
                    logging.info("Fetching supervisors for all-operators report: %s", sup_ids)
                    sup_map = {r[0]: r[1] for r in cursor.fetchall()}
            except Exception:
                logging.exception("Error fetching supervisors for all-operators report")

        # Добавляем supervisor_name к каждому оператору (O(N))
        for op in ops:
            sid = op.get('supervisor_id')
            op['supervisor_name'] = sup_map.get(int(sid)) if sid else None

        # Вызов генератора — один проход, файл формируется сразу с колонкой "Супервайзер"
        return self.generate_excel_report_from_view({"operators": ops}, trainings_map, month, filename=filename, include_supervisor=True)

    # ==================== Work Shifts Methods ====================
    
    def _normalize_schedule_date(self, value):
        if value is None:
            raise ValueError("shift_date is required")
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return datetime.strptime(value, '%Y-%m-%d').date()
        raise ValueError("Invalid date format, expected YYYY-MM-DD")

    def _normalize_schedule_time(self, value, field_name):
        if value is None:
            raise ValueError(f"{field_name} is required")
        if isinstance(value, dt_time):
            return value
        if isinstance(value, str):
            return datetime.strptime(value, '%H:%M').time()
        raise ValueError(f"Invalid {field_name} format, expected HH:MM")

    def _normalize_shift_breaks(self, breaks):
        if breaks is None:
            return []
        if not isinstance(breaks, list):
            raise ValueError("breaks must be a list")

        normalized = []
        seen = set()
        for item in breaks:
            if not isinstance(item, dict):
                raise ValueError("Each break must be an object")
            start_raw = item.get('start')
            end_raw = item.get('end')
            if start_raw is None or end_raw is None:
                raise ValueError("Each break must contain start and end")

            start_val = _time_to_minutes(start_raw) if isinstance(start_raw, str) else int(start_raw)
            end_val = _time_to_minutes(end_raw) if isinstance(end_raw, str) else int(end_raw)
            if end_val <= start_val:
                raise ValueError("Break end must be greater than break start")

            key = (start_val, end_val)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({'start': start_val, 'end': end_val})

        normalized.sort(key=lambda b: (b['start'], b['end']))
        return normalized

    def _schedule_interval_minutes(self, start_time_value, end_time_value):
        if isinstance(start_time_value, dt_time):
            start_str = start_time_value.strftime('%H:%M')
        else:
            start_str = str(start_time_value)
        if isinstance(end_time_value, dt_time):
            end_str = end_time_value.strftime('%H:%M')
        else:
            end_str = str(end_time_value)

        start_min = _time_to_minutes(start_str)
        end_min = _time_to_minutes(end_str)
        if end_min <= start_min:
            end_min += 24 * 60
        return start_min, end_min

    def _insert_shift_breaks(self, cursor, shift_id, breaks):
        cursor.execute("DELETE FROM shift_breaks WHERE shift_id = %s", (shift_id,))
        if breaks:
            cursor.executemany(
                """
                INSERT INTO shift_breaks (shift_id, start_minutes, end_minutes)
                VALUES (%s, %s, %s)
                """,
                [(shift_id, b['start'], b['end']) for b in breaks]
            )

    def _save_shift_tx(
        self,
        cursor,
        operator_id,
        shift_date,
        start_time,
        end_time,
        breaks=None,
        previous_start_time=None,
        previous_end_time=None
    ):
        breaks_norm = self._normalize_shift_breaks(breaks)
        new_start_min, new_end_min = self._schedule_interval_minutes(start_time, end_time)

        prev_start_obj = None
        prev_end_obj = None
        if previous_start_time is not None and previous_end_time is not None:
            prev_start_obj = self._normalize_schedule_time(previous_start_time, 'previous_start_time')
            prev_end_obj = self._normalize_schedule_time(previous_end_time, 'previous_end_time')
            if (
                prev_start_obj != start_time or prev_end_obj != end_time
            ):
                cursor.execute(
                    """
                    DELETE FROM work_shifts
                    WHERE operator_id = %s AND shift_date = %s
                      AND start_time = %s AND end_time = %s
                    """,
                    (operator_id, shift_date, prev_start_obj, prev_end_obj)
                )

        cursor.execute(
            """
            SELECT id, start_time, end_time
            FROM work_shifts
            WHERE operator_id = %s AND shift_date = %s
            """,
            (operator_id, shift_date)
        )
        existing_rows = cursor.fetchall()

        overlap_ids = []
        for shift_id, existing_start, existing_end in existing_rows:
            if existing_start == start_time and existing_end == end_time:
                continue
            ex_start_min, ex_end_min = self._schedule_interval_minutes(existing_start, existing_end)
            if new_start_min < ex_end_min and ex_start_min < new_end_min:
                overlap_ids.append(shift_id)

        if overlap_ids:
            cursor.execute(
                "DELETE FROM work_shifts WHERE id = ANY(%s)",
                (overlap_ids,)
            )

        cursor.execute(
            """
            INSERT INTO work_shifts (operator_id, shift_date, start_time, end_time, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (operator_id, shift_date, start_time, end_time)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (operator_id, shift_date, start_time, end_time)
        )
        shift_id = cursor.fetchone()[0]
        self._insert_shift_breaks(cursor, shift_id, breaks_norm)

        cursor.execute(
            """
            DELETE FROM days_off
            WHERE operator_id = %s AND day_off_date = %s
            """,
            (operator_id, shift_date)
        )

        return shift_id

    def _normalize_schedule_status_code(self, status_code):
        code = str(status_code or '').strip()
        if code not in SCHEDULE_SPECIAL_STATUS_META:
            raise ValueError("Invalid status_code")
        return code

    def _serialize_schedule_status_period(self, row):
        period_id, operator_id, status_code, start_date_value, end_date_value, dismissal_reason, comment = row[:7]
        meta = SCHEDULE_SPECIAL_STATUS_META.get(status_code, {})
        return {
            'id': int(period_id),
            'operatorId': int(operator_id),
            'statusCode': status_code,
            'label': meta.get('label') or status_code,
            'kind': meta.get('kind') or 'absence',
            'startDate': start_date_value.strftime('%Y-%m-%d') if start_date_value else None,
            'endDate': end_date_value.strftime('%Y-%m-%d') if end_date_value else None,
            'dismissalReason': dismissal_reason,
            'comment': comment or ''
        }

    def _load_schedule_status_periods_for_operators(self, cursor, operator_ids, start_date_obj=None, end_date_obj=None):
        operator_ids = [int(v) for v in (operator_ids or []) if v is not None]
        result = {
            op_id: {
                'scheduleStatusPeriods': [],
                'scheduleStatusDays': {}
            }
            for op_id in operator_ids
        }
        if not operator_ids:
            return result

        query = """
            SELECT id, operator_id, status_code, start_date, end_date, dismissal_reason, comment
            FROM operator_schedule_status_periods
            WHERE operator_id = ANY(%s)
        """
        params = [operator_ids]

        if start_date_obj and end_date_obj:
            query += " AND start_date <= %s AND COALESCE(end_date, DATE '9999-12-31') >= %s"
            params.extend([end_date_obj, start_date_obj])
        elif start_date_obj:
            query += " AND COALESCE(end_date, DATE '9999-12-31') >= %s"
            params.append(start_date_obj)
        elif end_date_obj:
            query += " AND start_date <= %s"
            params.append(end_date_obj)

        query += " ORDER BY operator_id, start_date, id"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        should_expand_days = bool(start_date_obj and end_date_obj)
        for row in rows:
            op_id = int(row[1])
            bucket = result.get(op_id)
            if bucket is None:
                continue

            period_payload = self._serialize_schedule_status_period(row)
            bucket['scheduleStatusPeriods'].append(period_payload)

            if not should_expand_days:
                continue

            period_start = row[3]
            period_end = row[4] or end_date_obj
            overlap_start = max(period_start, start_date_obj)
            overlap_end = min(period_end, end_date_obj)
            if overlap_end < overlap_start:
                continue

            day_value = {
                'id': period_payload['id'],
                'statusCode': period_payload['statusCode'],
                'label': period_payload['label'],
                'kind': period_payload['kind'],
                'startDate': period_payload['startDate'],
                'endDate': period_payload['endDate'],
                'dismissalReason': period_payload['dismissalReason'],
                'comment': period_payload['comment']
            }
            cur_day = overlap_start
            while cur_day <= overlap_end:
                bucket['scheduleStatusDays'][cur_day.strftime('%Y-%m-%d')] = day_value
                cur_day += timedelta(days=1)

        return result

    def _attach_schedule_status_periods_to_operators(self, cursor, operators_map, operator_ids, start_date_obj=None, end_date_obj=None):
        statuses_map = self._load_schedule_status_periods_for_operators(
            cursor=cursor,
            operator_ids=operator_ids,
            start_date_obj=start_date_obj,
            end_date_obj=end_date_obj
        )
        for op_id in (operator_ids or []):
            target = operators_map.get(op_id)
            if target is None:
                continue
            data = statuses_map.get(op_id) or {}
            target['scheduleStatusPeriods'] = data.get('scheduleStatusPeriods', [])
            target['scheduleStatusDays'] = data.get('scheduleStatusDays', {})

    def get_operators_with_shifts(self, start_date=None, end_date=None):
        """
        Получить всех операторов со сменами и выходными днями за период.
        Возвращает список операторов с их сменами и выходными.
        """
        start_date_obj = self._normalize_schedule_date(start_date) if start_date else None
        end_date_obj = self._normalize_schedule_date(end_date) if end_date else None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.supervisor_id, s.name as supervisor_name,
                       d.name as direction, u.status, u.rate
                FROM users u
                LEFT JOIN users s ON u.supervisor_id = s.id
                LEFT JOIN directions d ON u.direction_id = d.id
                WHERE u.role = 'operator'
                ORDER BY d.name, u.name
            """)
            operators = cursor.fetchall()
            if not operators:
                return []

            operator_ids = [row[0] for row in operators]
            result_map = {}
            for op_id, name, supervisor_id, supervisor_name, direction, status, rate in operators:
                result_map[op_id] = {
                    'id': op_id,
                    'name': name,
                    'supervisor_id': supervisor_id,
                    'supervisor_name': supervisor_name,
                    'direction': direction,
                    'status': status,
                    'rate': float(rate) if rate else 1.0,
                    'shifts': {},
                    'daysOff': [],
                    'scheduleStatusPeriods': [],
                    'scheduleStatusDays': {}
                }

            shifts_query = """
                SELECT ws.id, ws.operator_id, ws.shift_date, ws.start_time, ws.end_time
                FROM work_shifts ws
                WHERE ws.operator_id = ANY(%s)
            """
            shifts_params = [operator_ids]
            if start_date_obj:
                shifts_query += " AND ws.shift_date >= %s"
                shifts_params.append(start_date_obj)
            if end_date_obj:
                shifts_query += " AND ws.shift_date <= %s"
                shifts_params.append(end_date_obj)
            shifts_query += " ORDER BY ws.operator_id, ws.shift_date, ws.start_time"
            cursor.execute(shifts_query, shifts_params)
            shifts_rows = cursor.fetchall()

            shift_ids = []
            shift_ref = {}
            for shift_id, operator_id, shift_date, start_time_value, end_time_value in shifts_rows:
                op_entry = result_map.get(operator_id)
                if not op_entry:
                    continue
                date_str = shift_date.strftime('%Y-%m-%d')
                shift_item = {
                    'id': shift_id,
                    'start': start_time_value.strftime('%H:%M'),
                    'end': end_time_value.strftime('%H:%M'),
                    'breaks': []
                }
                op_entry['shifts'].setdefault(date_str, []).append(shift_item)
                shift_ids.append(shift_id)
                shift_ref[shift_id] = shift_item

            if shift_ids:
                cursor.execute(
                    """
                    SELECT shift_id, start_minutes, end_minutes
                    FROM shift_breaks
                    WHERE shift_id = ANY(%s)
                    ORDER BY shift_id, start_minutes, end_minutes
                    """,
                    (shift_ids,)
                )
                for shift_id, br_start, br_end in cursor.fetchall():
                    item = shift_ref.get(shift_id)
                    if item is None:
                        continue
                    item['breaks'].append({'start': int(br_start), 'end': int(br_end)})

            # Keep backward-compatible read behavior: frontend expects already merged segments on load.
            for op_entry in result_map.values():
                for day_key, day_shifts in list(op_entry['shifts'].items()):
                    op_entry['shifts'][day_key] = _merge_shifts_for_date(day_shifts)

            days_off_query = """
                SELECT operator_id, day_off_date
                FROM days_off
                WHERE operator_id = ANY(%s)
            """
            days_off_params = [operator_ids]
            if start_date_obj:
                days_off_query += " AND day_off_date >= %s"
                days_off_params.append(start_date_obj)
            if end_date_obj:
                days_off_query += " AND day_off_date <= %s"
                days_off_params.append(end_date_obj)
            days_off_query += " ORDER BY operator_id, day_off_date"
            cursor.execute(days_off_query, days_off_params)
            for operator_id, day_off_date in cursor.fetchall():
                op_entry = result_map.get(operator_id)
                if op_entry is None:
                    continue
                op_entry['daysOff'].append(day_off_date.strftime('%Y-%m-%d'))

            self._attach_schedule_status_periods_to_operators(
                cursor=cursor,
                operators_map=result_map,
                operator_ids=operator_ids,
                start_date_obj=start_date_obj,
                end_date_obj=end_date_obj
            )

            return [result_map[row[0]] for row in operators]

    def get_operator_with_shifts(self, operator_id, start_date=None, end_date=None):
        """
        Получить одного оператора с его сменами/перерывами/выходными за период.
        Возвращает структуру, совместимую с get_operators_with_shifts()[i].
        """
        operator_id = int(operator_id)
        start_date_obj = self._normalize_schedule_date(start_date) if start_date else None
        end_date_obj = self._normalize_schedule_date(end_date) if end_date else None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.supervisor_id, s.name as supervisor_name,
                       d.name as direction, u.status, u.rate
                FROM users u
                LEFT JOIN users s ON u.supervisor_id = s.id
                LEFT JOIN directions d ON u.direction_id = d.id
                WHERE u.id = %s AND u.role = 'operator'
                LIMIT 1
            """, (operator_id,))
            row = cursor.fetchone()
            if not row:
                return None

            op_id, name, supervisor_id, supervisor_name, direction, status, rate = row
            result = {
                'id': op_id,
                'name': name,
                'supervisor_id': supervisor_id,
                'supervisor_name': supervisor_name,
                'direction': direction,
                'status': status,
                'rate': float(rate) if rate else 1.0,
                'shifts': {},
                'daysOff': [],
                'scheduleStatusPeriods': [],
                'scheduleStatusDays': {}
            }

            shifts_query = """
                SELECT ws.id, ws.shift_date, ws.start_time, ws.end_time
                FROM work_shifts ws
                WHERE ws.operator_id = %s
            """
            shifts_params = [operator_id]
            if start_date_obj:
                shifts_query += " AND ws.shift_date >= %s"
                shifts_params.append(start_date_obj)
            if end_date_obj:
                shifts_query += " AND ws.shift_date <= %s"
                shifts_params.append(end_date_obj)
            shifts_query += " ORDER BY ws.shift_date, ws.start_time"
            cursor.execute(shifts_query, shifts_params)
            shifts_rows = cursor.fetchall()

            shift_ids = []
            shift_ref = {}
            for shift_id, shift_date, start_time_value, end_time_value in shifts_rows:
                date_str = shift_date.strftime('%Y-%m-%d')
                shift_item = {
                    'id': shift_id,
                    'start': start_time_value.strftime('%H:%M'),
                    'end': end_time_value.strftime('%H:%M'),
                    'breaks': []
                }
                result['shifts'].setdefault(date_str, []).append(shift_item)
                shift_ids.append(shift_id)
                shift_ref[shift_id] = shift_item

            if shift_ids:
                cursor.execute(
                    """
                    SELECT shift_id, start_minutes, end_minutes
                    FROM shift_breaks
                    WHERE shift_id = ANY(%s)
                    ORDER BY shift_id, start_minutes, end_minutes
                    """,
                    (shift_ids,)
                )
                for shift_id, br_start, br_end in cursor.fetchall():
                    item = shift_ref.get(shift_id)
                    if item is None:
                        continue
                    item['breaks'].append({'start': int(br_start), 'end': int(br_end)})

            for day_key, day_shifts in list(result['shifts'].items()):
                result['shifts'][day_key] = _merge_shifts_for_date(day_shifts)

            days_off_query = """
                SELECT day_off_date
                FROM days_off
                WHERE operator_id = %s
            """
            days_off_params = [operator_id]
            if start_date_obj:
                days_off_query += " AND day_off_date >= %s"
                days_off_params.append(start_date_obj)
            if end_date_obj:
                days_off_query += " AND day_off_date <= %s"
                days_off_params.append(end_date_obj)
            days_off_query += " ORDER BY day_off_date"
            cursor.execute(days_off_query, days_off_params)
            for (day_off_date,) in cursor.fetchall():
                result['daysOff'].append(day_off_date.strftime('%Y-%m-%d'))

            self._attach_schedule_status_periods_to_operators(
                cursor=cursor,
                operators_map={operator_id: result},
                operator_ids=[operator_id],
                start_date_obj=start_date_obj,
                end_date_obj=end_date_obj
            )

            return result

    def save_shift(
        self,
        operator_id,
        shift_date,
        start_time,
        end_time,
        breaks=None,
        previous_start_time=None,
        previous_end_time=None
    ):
        """
        Сохранить смену для оператора и синхронизировать её с фронтовым merged-состоянием.
        - заменяет пересекающиеся смены на этой дате одной присланной сменой
        - обновляет/перезаписывает перерывы
        - снимает day off на эту дату, если он был
        """
        shift_date_obj = self._normalize_schedule_date(shift_date)
        start_time_obj = self._normalize_schedule_time(start_time, 'start_time')
        end_time_obj = self._normalize_schedule_time(end_time, 'end_time')

        with self._get_cursor() as cursor:
            return self._save_shift_tx(
                cursor=cursor,
                operator_id=int(operator_id),
                shift_date=shift_date_obj,
                start_time=start_time_obj,
                end_time=end_time_obj,
                breaks=breaks,
                previous_start_time=previous_start_time,
                previous_end_time=previous_end_time
            )

    def delete_shift(self, operator_id, shift_date, start_time, end_time):
        """
        Удалить конкретную смену оператора.
        """
        shift_date_obj = self._normalize_schedule_date(shift_date)
        start_time_obj = self._normalize_schedule_time(start_time, 'start_time')
        end_time_obj = self._normalize_schedule_time(end_time, 'end_time')

        with self._get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM work_shifts
                WHERE operator_id = %s AND shift_date = %s
                  AND start_time = %s AND end_time = %s
                RETURNING id
            """, (int(operator_id), shift_date_obj, start_time_obj, end_time_obj))
            return cursor.fetchone() is not None

    def toggle_day_off(self, operator_id, day_off_date):
        """
        Переключить выходной день для оператора.
        Если день уже выходной - убрать, иначе - добавить и удалить все смены в этот день.
        Возвращает True если день стал выходным, False если был убран.
        """
        day_off_date_obj = self._normalize_schedule_date(day_off_date)
        operator_id = int(operator_id)

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT 1
                FROM days_off
                WHERE operator_id = %s AND day_off_date = %s
            """, (operator_id, day_off_date_obj))
            existing = cursor.fetchone()

            if existing:
                cursor.execute("""
                    DELETE FROM days_off
                    WHERE operator_id = %s AND day_off_date = %s
                """, (operator_id, day_off_date_obj))
                return False

            cursor.execute("""
                INSERT INTO days_off (operator_id, day_off_date)
                VALUES (%s, %s)
                ON CONFLICT (operator_id, day_off_date) DO NOTHING
            """, (operator_id, day_off_date_obj))
            cursor.execute("""
                DELETE FROM work_shifts
                WHERE operator_id = %s AND shift_date = %s
            """, (operator_id, day_off_date_obj))
            return True

    def save_schedule_status_period(
        self,
        operator_id,
        status_code,
        start_date,
        end_date=None,
        dismissal_reason=None,
        comment=None,
        created_by=None
    ):
        """
        Сохранить период специального статуса оператора.
        Новая запись замещает пересекающиеся периоды (с обрезкой/разделением при необходимости).
        """
        operator_id = int(operator_id)
        status_code_norm = self._normalize_schedule_status_code(status_code)
        start_date_obj = self._normalize_schedule_date(start_date)

        comment_norm = None
        if comment is not None:
            comment_text = str(comment).strip()
            comment_norm = comment_text or None

        if status_code_norm == 'dismissal':
            end_date_obj = None
            dismissal_reason_norm = str(dismissal_reason or '').strip()
            if dismissal_reason_norm not in set(SCHEDULE_DISMISSAL_REASONS):
                raise ValueError("Invalid dismissal_reason")
            if not comment_norm:
                raise ValueError("Comment is required for dismissal")
        else:
            end_date_obj = self._normalize_schedule_date(end_date or start_date_obj)
            if end_date_obj < start_date_obj:
                raise ValueError("end_date must be >= start_date")
            dismissal_reason_norm = None

        created_by_id = int(created_by) if created_by is not None else None
        infinite_date = date(9999, 12, 31)
        new_end_cmp = end_date_obj or infinite_date

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, status_code, start_date, end_date, dismissal_reason, comment, created_by
                FROM operator_schedule_status_periods
                WHERE operator_id = %s
                  AND start_date <= %s
                  AND COALESCE(end_date, DATE '9999-12-31') >= %s
                ORDER BY start_date, id
            """, (operator_id, new_end_cmp, start_date_obj))
            overlapping = cursor.fetchall()

            for row in overlapping:
                (
                    existing_id,
                    existing_status_code,
                    existing_start,
                    existing_end,
                    existing_dismissal_reason,
                    existing_comment,
                    existing_created_by
                ) = row
                existing_end_cmp = existing_end or infinite_date

                left_exists = existing_start < start_date_obj
                right_exists = (end_date_obj is not None) and (existing_end_cmp > end_date_obj)

                if left_exists and right_exists:
                    left_end = start_date_obj - timedelta(days=1)
                    right_start = end_date_obj + timedelta(days=1)

                    cursor.execute("""
                        UPDATE operator_schedule_status_periods
                        SET end_date = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (left_end, existing_id))

                    cursor.execute("""
                        INSERT INTO operator_schedule_status_periods (
                            operator_id, status_code, start_date, end_date,
                            dismissal_reason, comment, created_by, created_at, updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """, (
                        operator_id,
                        existing_status_code,
                        right_start,
                        existing_end,
                        existing_dismissal_reason,
                        existing_comment,
                        existing_created_by
                    ))
                    continue

                if left_exists:
                    left_end = start_date_obj - timedelta(days=1)
                    cursor.execute("""
                        UPDATE operator_schedule_status_periods
                        SET end_date = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (left_end, existing_id))
                    continue

                if right_exists:
                    right_start = end_date_obj + timedelta(days=1)
                    cursor.execute("""
                        UPDATE operator_schedule_status_periods
                        SET start_date = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (right_start, existing_id))
                    continue

                cursor.execute("""
                    DELETE FROM operator_schedule_status_periods
                    WHERE id = %s
                """, (existing_id,))

            cursor.execute("""
                INSERT INTO operator_schedule_status_periods (
                    operator_id, status_code, start_date, end_date,
                    dismissal_reason, comment, created_by, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id, operator_id, status_code, start_date, end_date, dismissal_reason, comment
            """, (
                operator_id,
                status_code_norm,
                start_date_obj,
                end_date_obj,
                dismissal_reason_norm,
                comment_norm,
                created_by_id
            ))
            saved_row = cursor.fetchone()
            return self._serialize_schedule_status_period(saved_row)

    def save_shifts_bulk(self, operator_id, shifts_data):
        """
        Массовое сохранение смен для оператора.
        shifts_data: [{'date': 'YYYY-MM-DD', 'start': 'HH:MM', 'end': 'HH:MM', 'breaks': [...]}, ...]
        """
        operator_id = int(operator_id)
        if not isinstance(shifts_data, list):
            raise ValueError("shifts must be a list")

        result_ids = []
        with self._get_cursor() as cursor:
            for shift in shifts_data:
                if not isinstance(shift, dict):
                    raise ValueError("Each shift must be an object")
                shift_date_obj = self._normalize_schedule_date(shift.get('date'))
                start_time_obj = self._normalize_schedule_time(shift.get('start'), 'start')
                end_time_obj = self._normalize_schedule_time(shift.get('end'), 'end')
                shift_id = self._save_shift_tx(
                    cursor=cursor,
                    operator_id=operator_id,
                    shift_date=shift_date_obj,
                    start_time=start_time_obj,
                    end_time=end_time_obj,
                    breaks=shift.get('breaks')
                )
                result_ids.append(shift_id)

        return result_ids

    def get_ai_feedback_cache(self, operator_id: int, month: str):
        """Получить кэшированный AI фидбэк для оператора за месяц"""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT feedback_data, created_at, updated_at
                FROM ai_feedback_cache
                WHERE operator_id = %s AND month = %s
            """, (operator_id, month))
            result = cursor.fetchone()
            if result:
                return {
                    'feedback_data': result[0],
                    'created_at': result[1],
                    'updated_at': result[2]
                }
            return None

    def find_operator_by_name_fuzzy(self, name: str):
        """Попытаться найти оператора по ФИО. Сначала точное совпадение (case-insensitive),
        затем попытка по фамилии (ILIKE '%surname%'). Возвращает запись пользователя или None.
        """
        if not name:
            return None
        name = name.strip()
        with self._get_cursor() as cursor:
            # exact case-insensitive match
            cursor.execute("""
                SELECT id, telegram_id, name, role, direction_id, hire_date, supervisor_id, login
                FROM users WHERE LOWER(name) = LOWER(%s) AND role = 'operator' LIMIT 1
            """, (name,))
            row = cursor.fetchone()
            if row:
                return row

            # try surname match: take first token as likely surname
            tokens = re.split(r'\s+', name)
            if tokens:
                surname = tokens[0]
                cursor.execute("""
                    SELECT id, telegram_id, name, role, direction_id, hire_date, supervisor_id, login
                    FROM users WHERE name ILIKE %s AND role = 'operator' LIMIT 1
                """, (f"%{surname}%",))
                row = cursor.fetchone()
                if row:
                    return row

        return None

    def replace_baiga_scores_for_day(self, day, scores_map: dict):
        """Replace all baiga_daily_scores for given day.
        scores_map: { operator_id (int) : points (int) }
        If operator_id keys are not ints, they will be skipped.
        """
        if isinstance(day, str):
            try:
                day_date = datetime.strptime(day, "%Y-%m-%d").date()
            except Exception:
                raise ValueError("Invalid day format, expected YYYY-MM-DD")
        else:
            day_date = day

        with self._get_cursor() as cursor:
            # remove all existing for day
            cursor.execute("DELETE FROM baiga_daily_scores WHERE day = %s", (day_date,))
            insert_vals = []
            for op_id, pts in (scores_map or {}).items():
                try:
                    oid = int(op_id)
                    pts_i = int(pts) if pts is not None else 0
                    insert_vals.append((oid, day_date, pts_i))
                except Exception:
                    continue

            if insert_vals:
                cursor.executemany(
                    "INSERT INTO baiga_daily_scores (operator_id, day, points) VALUES (%s, %s, %s)",
                    insert_vals
                )

    def get_baiga_scores_for_day(self, day):
        """Return list of {operator_id, name, points} for given day."""
        if isinstance(day, str):
            try:
                day_date = datetime.strptime(day, "%Y-%m-%d").date()
            except Exception:
                raise ValueError("Invalid day format, expected YYYY-MM-DD")
        else:
            day_date = day

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT b.operator_id, b.points, u.name
                FROM baiga_daily_scores b
                LEFT JOIN users u ON u.id = b.operator_id
                WHERE b.day = %s
            """, (day_date,))
            rows = cursor.fetchall()
        return [ { 'operator_id': r[0], 'points': int(r[1] or 0), 'name': r[2] } for r in rows ]

    def get_baiga_scores_for_range(self, start_day, end_day):
        """Return mapping of day (YYYY-MM-DD) -> list of {operator_id, points, name} for days in [start_day, end_day]."""
        if isinstance(start_day, str):
            try:
                start_date = datetime.strptime(start_day, "%Y-%m-%d").date()
            except Exception:
                raise ValueError("Invalid start_day format, expected YYYY-MM-DD")
        else:
            start_date = start_day

        if isinstance(end_day, str):
            try:
                end_date = datetime.strptime(end_day, "%Y-%m-%d").date()
            except Exception:
                raise ValueError("Invalid end_day format, expected YYYY-MM-DD")
        else:
            end_date = end_day

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT to_char(b.day, 'YYYY-MM-DD') as day_str, b.operator_id, b.points, u.name
                FROM baiga_daily_scores b
                LEFT JOIN users u ON u.id = b.operator_id
                WHERE b.day >= %s AND b.day <= %s
                ORDER BY b.day, b.operator_id
            """, (start_date, end_date))
            rows = cursor.fetchall()

        result = {}
        for day_str, op_id, pts, name in rows:
            entry = { 'operator_id': op_id, 'points': int(pts or 0), 'name': name }
            result.setdefault(day_str, []).append(entry)
        return result

    def save_ai_feedback_cache(self, operator_id: int, month: str, feedback_data: dict):
        """Сохранить или обновить AI фидбэк в кэше"""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO ai_feedback_cache (operator_id, month, feedback_data, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (operator_id, month)
                DO UPDATE SET
                    feedback_data = EXCLUDED.feedback_data,
                    updated_at = CURRENT_TIMESTAMP
            """, (operator_id, month, json.dumps(feedback_data, ensure_ascii=False)))


# Initialize database
db = Database()
