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
from collections import defaultdict
import pandas as pd
from typing import List, Dict, Any, Tuple

logging.basicConfig(level=logging.INFO)

os.environ['TZ'] = 'Asia/Almaty'
time.tzset()

# Global connection pool
MIN_CONN = 1
MAX_CONN = 20  # Adjust based on expected load
POOL = None

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
                UPDATE calls
                SET month = '2025-09'
                WHERE operator_id = 5
                AND month = '2025-10';

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

            cursor.execute("""
                ALTER TABLE daily_hours
                    ADD COLUMN IF NOT EXISTS fine_amount FLOAT NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS fine_reason VARCHAR(64),
                    ADD COLUMN IF NOT EXISTS fine_comment TEXT;           
            """)

            # Processed sheets table
            cursor.execute("""
                DROP TABLE IF EXISTS processed_sheets CASCADE;
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
            """)

    def create_user(self, telegram_id, name, role, direction_id=None, rate=None, hire_date=None, supervisor_id=None, login=None, password=None, hours_table_url=None):
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
                    INSERT INTO users (telegram_id, name, role, direction_id, rate, hire_date, supervisor_id, login, password_hash, hours_table_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (telegram_id, name, role, direction_id, rate, hire_date, supervisor_id, login, password_hash, hours_table_url))
                return cursor.fetchone()[0]
            except psycopg2.IntegrityError as e:
                cursor.execute("ROLLBACK TO SAVEPOINT before_insert")
                if 'unique_name_role' in str(e):
                    cursor.execute("""
                        UPDATE users
                        SET direction_id = COALESCE(%s, direction_id),
                            supervisor_id = COALESCE(%s, supervisor_id),
                            hours_table_url = COALESCE(%s, hours_table_url)
                        WHERE name = %s AND role = %s
                        RETURNING id
                    """, (direction_id, supervisor_id, hours_table_url, name, role))
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
                            hours_table_url = COALESCE(%s, hours_table_url)
                        WHERE telegram_id = %s
                        RETURNING id
                    """, (name, role, direction_id, hire_date, supervisor_id, login, password_hash, hours_table_url, telegram_id))
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
                                    fine_amount=0.0, fine_reason=None, fine_comment=None):
        """
        Вставляет/обновляет запись daily_hours (operator_id + day).
        efficiency и fine_amount ожидаются в часах/суммах соответственно.
        """
        if isinstance(day, str):
            day = datetime.strptime(day, "%Y-%m-%d").date()
        with self._get_cursor() as cursor:
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
            """, (operator_id, day, work_time, break_time, talk_time, calls, efficiency,
                float(fine_amount) if fine_amount is not None else 0.0,
                fine_reason, fine_comment))

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
                }

            # 2) Получаем имя/ставку + данные work_hours одним LEFT JOIN запросом (включая fines)
            cursor.execute(
                """
                SELECT u.name, u.rate,
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
                WHERE u.id = %s
                LIMIT 1
                """,
                (month, operator_id),
            )
            row = cursor.fetchone()

        # default values if user / work_hours not found
        if row:
            name, rate, norm_hours, regular_hours, total_break_time, total_talk_time, \
                total_calls, total_efficiency_hours, calls_per_hour, fines = row
            rate = float(rate) if rate is not None else 0.0
        else:
            name = None
            rate = 0.0
            norm_hours = regular_hours = total_break_time = total_talk_time = 0.0
            total_calls = 0
            total_efficiency_hours = calls_per_hour = fines = 0.0

        operator_obj = {
            "operator_id": operator_id,
            "name": name,
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
                SELECT u.id, u.name, u.rate,
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
                    "fine_comment": fine_comment
                }
                daily_map.setdefault(op_id, {})[day_num] = d

            # Сбор финального списка операторов
            operators = []
            for row in operator_rows:
                (op_id, op_name, rate, norm_hours,
                regular_hours, total_break_time, total_talk_time,
                total_calls, total_efficiency_hours, calls_per_hour, fines) = row

                operators.append({
                    "operator_id": op_id,
                    "name": op_name,
                    "rate": float(rate) if rate is not None else 0.0,
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
            cursor.execute("""
                SELECT
                    COALESCE(SUM(work_time),0),
                    COALESCE(SUM(break_time),0),
                    COALESCE(SUM(talk_time),0),
                    COALESCE(SUM(calls),0),
                    COALESCE(SUM(efficiency),0),
                    COALESCE(SUM(fine_amount),0)
                FROM daily_hours
                WHERE operator_id = %s AND day >= %s AND day <= %s
            """, (operator_id, start, end))
            row = cursor.fetchone()
            total_work_time, total_break_time, total_talk_time, total_calls, total_efficiency_hours, total_fines = row

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
            SELECT u.id, u.telegram_id, u.name, u.role, d.name, u.hire_date, u.supervisor_id, u.login, u.hours_table_url, u.scores_table_url, u.is_active, u.status, u.rate  -- Add status and rate
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
                SELECT id, audio_path FROM calls WHERE id = %s
            """, (call_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0],
                    "audio_path": row[1]
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

    def add_call_evaluation(self, evaluator_id, operator_id, phone_number, score, 
                            comment=None, month=None, audio_path=None, is_draft=False, 
                            scores=None, criterion_comments=None, direction_id=None, 
                            is_correction=False, previous_version_id=None, appeal_date=None):
        month = month or datetime.now().strftime('%Y-%m')
        
        # Подготовка JSON данных один раз
        scores_json = json.dumps(scores) if scores else None
        criterion_comments_json = json.dumps(criterion_comments) if criterion_comments else None
        
        with self._get_cursor() as cursor:
            # Проверка существования direction_id одним запросом
            if direction_id:
                cursor.execute("SELECT 1 FROM directions WHERE id = %s AND is_active = TRUE", (direction_id,))  # Added is_active filter
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
            
            return call_id

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
                        "work_time": round(work_time_h, 1),
                        "break_time": round(break_time_h, 1),
                        "talk_time": round(talk_time_h, 1),
                        "calls": int(calls),
                        "efficiency": round(efficiency_h, 1),
                        "month": month
                    })
                except Exception as e:
                    print(f"Ошибка при обработке строки {row}: {e}")
                    continue

            return sheet_name, operators, None

        except Exception as e:
            return None, None, str(e)


    def get_operator_stats(self, operator_id):
        """Получить статистику оператора (часы, оценки)"""
        with self._get_cursor() as cursor:
            # Получаем текущий месяц
            current_month = datetime.now().strftime('%Y-%m')
            
            # Получаем данные о часах и оценках в одном запросе для оптимизации
            cursor.execute("""
                SELECT 
                    wh.regular_hours, 
                    wh.training_hours, 
                    wh.fines, 
                    wh.norm_hours,
                    (SELECT COUNT(*) FROM calls WHERE operator_id = %s AND month = %s AND is_draft = FALSE) AS call_count,
                    (SELECT AVG(score) FROM calls WHERE operator_id = %s AND month = %s AND is_draft = FALSE) AS avg_score
                FROM work_hours wh
                WHERE wh.operator_id = %s AND wh.month = %s
            """, (operator_id, current_month, operator_id, current_month, operator_id, current_month))
            row = cursor.fetchone()
            
            if row:
                regular_hours, training_hours, fines, norm_hours, call_count, avg_score = row
            else:
                regular_hours = training_hours = fines = norm_hours = call_count = avg_score = 0
            
            percent_complete = (regular_hours / norm_hours * 100) if norm_hours > 0 else 0
            
            return {
                'regular_hours': regular_hours,
                'training_hours': training_hours,
                'fines': fines,
                'norm_hours': norm_hours,
                'percent_complete': round(percent_complete, 2),
                'call_count': call_count,
                'avg_score': float(avg_score) if avg_score else 0
            }

    def get_operators_by_supervisor(self, supervisor_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.direction_id, u.hire_date, u.hours_table_url, u.scores_table_url, s.name as supervisor_name, u.status, u.rate
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
                    'rate': row[8]
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
                wh.regular_hours,
                wh.training_hours,
                wh.fines,
                wh.norm_hours,
                wh.calls_per_hour
            FROM users u
            LEFT JOIN work_hours wh ON u.id = wh.operator_id
            WHERE u.role = 'operator'
        """
        params = []
        if operator_id:
            query += " AND u.id = %s"
            params.append(operator_id)
        if month:
            query += " AND wh.month = %s"
            params.append(month)
        
        # Добавить группировку, если operator_id не указан
        if not operator_id:
            query += " GROUP BY u.id, u.name, u.direction_id, wh.regular_hours, wh.training_hours, wh.fines, wh.norm_hours, wh.calls_per_hour"
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "operator_id": row[0],
                    "operator_name": row[1],
                    "direction": row[2],
                    "regular_hours": float(row[3]) if row[3] is not None else 0,
                    "training_hours": float(row[4]) if row[4] is not None else 0,
                    "fines": float(row[4]) if row[4] is not None else 0,
                    "norm_hours": float(row[5]) if row[5] is not None else 0,
                    "daily_hours": row[6] if row[6] is not None else [0.0]*31,
                    "calls_per_hour": row[7]
                } for row in cursor.fetchall()
            ]

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

    def verify_password(self, user_id, password):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT password_hash FROM users WHERE id = %s
            """, (user_id,))
            result = cursor.fetchone()
            if result and result[0]:
                return pbkdf2_sha256.verify(password, result[0])
            return False

    def get_call_evaluations(self, operator_id, month=None):
        query = """
            WITH latest_versions AS (
                -- Находим последние версии для каждого уникального вызова
                -- Группируем по phone_number и month, чтобы найти последние оценки для каждого номера
                SELECT 
                    phone_number,
                    month,
                    MAX(created_at) as latest_date
                FROM calls
                WHERE operator_id = %s
                GROUP BY phone_number, month
            ),
            latest_calls AS (
                -- Получаем полные данные последних версий
                SELECT 
                    c.id,
                    c.phone_number,
                    c.month,
                    c.created_at
                FROM calls c
                JOIN latest_versions lv ON 
                    c.phone_number = lv.phone_number AND 
                    c.month = lv.month AND 
                    c.created_at = lv.latest_date
                WHERE c.operator_id = %s
            )
            SELECT 
                c.id,
                c.month, 
                c.phone_number, 
                c.appeal_date,
                c.score, 
                c.comment,
                c.audio_path,
                c.is_draft,
                c.is_correction,
                TO_CHAR(c.created_at, 'YYYY-MM-DD HH24:MI'),
                c.scores,
                c.criterion_comments,
                d.id as direction_id,
                d.name as direction_name,
                d.criteria as direction_criteria,
                d.has_file_upload as direction_has_file_upload,
                u.name as evaluator_name,
                c.created_at
            FROM calls c
            JOIN latest_calls lc ON c.id = lc.id
            LEFT JOIN directions d ON c.direction_id = d.id  
            LEFT JOIN users u ON c.evaluator_id = u.id
            WHERE c.operator_id = %s
        """
        params = [operator_id, operator_id, operator_id]
        if month:
            query += " AND c.month = %s"
            params.append(month)
        query += " ORDER BY c.created_at DESC"
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "id": row[0],
                    "month": row[1],
                    "phone_number": row[2],
                    "appeal_date": row[3].strftime('%Y-%m-%d %H:%M:%S') if row[3] else None,
                    "score": float(row[4]),
                    "comment": row[5],
                    "audio_path": row[6],
                    "is_draft": row[7],
                    "is_correction": row[8],
                    "evaluation_date": row[9],
                    "scores": row[10] if row[10] else [],
                    "criterion_comments": row[11] if row[11] else [],
                    "direction": {
                        "id": row[12],
                        "name": row[13],
                        "criteria": row[14] if row[14] else [],
                        "hasFileUpload": row[15] if row[15] is not None else True
                    } if row[12] else None,
                    "evaluator": row[16] if row[16] else None,
                    "created_at":row[17]
                } for row in cursor.fetchall()
            ]
        
    def update_user(self, user_id, field, value, changed_by=None):
        allowed_fields = ['direction_id', 'supervisor_id', 'status', 'rate', 'hire_date']  # Add new fields
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
        Summary: даты по столбцам + цветной RichText активаций/деактиваций.
        Operator sheets: строки событий + одна строка "Итого" на день с колонками-итогами
        (E..I человеко-читаемые в формате HH:MM:SS, J..N — секунды, скрытые для сортировки).
        """
        def sanitize_table_name(name):
            sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', (name or ""))
            if not sanitized or (not sanitized[0].isalpha() and sanitized[0] != '_'):
                sanitized = f"_{sanitized}"
            return sanitized[:255]

        def format_hms(duration):
            """Возвращает строку HH:MM:SS, где hours может быть >=24 (полные часы)."""
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

            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = "Summary"

            # -------------------------
            # 1) Summary: заголовки и данные (как раньше, но стилизовано)
            # -------------------------
            ws_summary.cell(1, 1).value = "ФИО"
            for col, dt in enumerate(dates, start=2):
                ws_summary.cell(1, col).value = dt.strftime("%Y-%m-%d")
                ws_summary.cell(1, col).alignment = Alignment(horizontal='center', vertical='center')
            last_date_col = 1 + len(dates)
            ws_summary.cell(1, last_date_col + 1).value = "Итого активаций"
            ws_summary.cell(1, last_date_col + 2).value = "Итого деактиваций"

            # шрифты для rich text
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
                        # отображаем как цветной rich text: "activations | deactivations"
                        rt = CellRichText([
                            TextBlock(green_font, str(activations)),
                            TextBlock(default_font, " | "),
                            TextBlock(red_font, str(deactivations))
                        ])
                        cell.value = rt
                    else:
                        # fallback plain text
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
            # применим границы к заполненным ячейкам summary
            for r in range(1, row):
                for c in range(1, last_date_col + 3):
                    ws_summary.cell(r, c).border = thin_border
            # автоширина (приближённая)
            ws_summary.column_dimensions['A'].width = 24
            for idx in range(2, last_date_col + 3):
                col_letter = ws_summary.cell(1, idx).column_letter
                ws_summary.column_dimensions[col_letter].width = 12
            ws_summary.freeze_panes = "A2"

            # -------------------------
            # 2) Листы по операторам: события + одна строка итого на день с колонками итогов
            #    (E..I — HH:MM:SS, J..N — секунды (скрыты) для фильтрации)
            # -------------------------
            # стили и заливки
            total_fill = PatternFill(start_color="EDEDED", end_color="EDEDED", fill_type="solid")
            status_fill = PatternFill(start_color="F7F7F7", end_color="F7F7F7", fill_type="solid")
            green_fill = PatternFill(start_color="CFF5D0", end_color="CFF5D0", fill_type="solid")
            orange_fill = PatternFill(start_color="FFE8C0", end_color="FFE8C0", fill_type="solid")
            yellow_fill = PatternFill(start_color="FFF9C4", end_color="FFF9C4", fill_type="solid")
            purple_fill = PatternFill(start_color="E8D7F7", end_color="E8D7F7", fill_type="solid")
            blue_fill = PatternFill(start_color="DCEEFF", end_color="DCEEFF", fill_type="solid")
            inactive_fill = PatternFill(start_color="FFDADA", end_color="FFDADA", fill_type="solid")

            # порядок статусов (исключаем 'inactive') — используется для колонок итогов E..I
            status_order = [
                ('active', 'Активен', green_fill),
                ('iesigning', 'Подписание', blue_fill),
                ('break', 'Перерыв', orange_fill),
                ('training', 'Тренинг', yellow_fill),
                ('tech', 'Тех. поддержка', purple_fill),
            ]

            for op_id, name in operators_dict.items():
                ws = wb.create_sheet(title=(name[:31] or f"op_{op_id}"))

                # Заголовок: A..N (A-D — события, E-I — human totals, J-N — seconds hidden)
                headers = [
                    "Дата", "Время / Описание", "Статус", "Длительность",
                    "Итого: Активен", "Итого: Подписание", "Итого: Перерыв", "Итого: Тренинг", "Итого: Тех",
                    "Итого Активен (сек)", "Итого Подписание (сек)", "Итого Перерыв (сек)", "Итого Тренинг (сек)", "Итого Тех (сек)"
                ]
                for col_idx, h in enumerate(headers, start=1):
                    cell = ws.cell(1, col_idx)
                    cell.value = h
                    cell.font = Font(bold=True)
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    cell.border = thin_border

                # Скрываем числовые колонки J..N (10..14)
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

                def write_day_totals_row(r, day_dt, counts_dict, day_status_times_dict):
                    """
                    Записывает одну строку 'Итого' с колонками E..I (HH:MM:SS) и J..N (секунды).
                    Возвращает следующий свободный row.
                    """
                    # A..D
                    ws.cell(r, 1).value = day_dt.strftime("%Y-%m-%d")
                    ws.cell(r, 2).value = "Итого"
                    ws.cell(r, 3).value = CellRichText([TextBlock(green_font, str(counts_dict['act'])), TextBlock(default_font, " | "), TextBlock(red_font, str(counts_dict['deact']))]) if CellRichText and TextBlock and green_font and red_font else f"{counts_dict['act']} | {counts_dict['deact']}"
                    ws.cell(r, 4).value = ""  # оставляем D пустой в строке Итого

                    # стиль для A..D
                    for c in range(1, 5):
                        cell = ws.cell(r, c)
                        cell.fill = total_fill
                        cell.font = Font(bold=True)
                        cell.alignment = Alignment(horizontal='center', vertical='center')
                        cell.border = thin_border

                    # колонки итогов E..I и J..N
                    for idx, (status_key, status_name, fill) in enumerate(status_order):
                        dur = day_status_times_dict.get(status_key, timedelta(0))
                        dur_sec = int(dur.total_seconds())
                        dur_hms = format_hms(dur)
                        col_read = 5 + idx   # E..I
                        col_sec = 10 + idx   # J..N
                        ws.cell(r, col_read).value = dur_hms
                        ws.cell(r, col_sec).value = dur_sec
                        # стили
                        ws.cell(r, col_read).alignment = Alignment(horizontal='center', vertical='center')
                        ws.cell(r, col_sec).alignment = Alignment(horizontal='center', vertical='center')
                        ws.cell(r, col_read).border = thin_border
                        ws.cell(r, col_sec).border = thin_border
                        # лёгкая заливка
                        ws.cell(r, col_read).fill = status_fill

                    return r + 1

                for i, log in enumerate(logs):
                    dt = log['date']
                    if current_day is None:
                        current_day = dt
                    if dt != current_day:
                        # записать строку итогов предыдущего дня
                        counts = counts_per_op[op_id][current_day]
                        current_row = write_day_totals_row(current_row, current_day, counts, day_status_times)
                        # сброс
                        day_status_times = defaultdict(timedelta)
                        current_day = dt

                    # next_time
                    if i < len(logs) - 1:
                        next_time = logs[i + 1]['change_time']
                    else:
                        next_time = end_time

                    duration = next_time - log['change_time']

                    # дубликат состояния?
                    if i > 0 and log['is_active'] == logs[i - 1]['is_active']:
                        dur_str = "N/A (дубликат)"
                    else:
                        dur_str = format_hms(duration)
                        if log['is_active'] != 'inactive':
                            day_status_times[log['is_active']] += duration

                    # пишем событие (A..D)
                    ws.cell(current_row, 1).value = dt.strftime("%Y-%m-%d")
                    ws.cell(current_row, 2).value = log['change_time'].strftime("%H:%M:%S")
                    ws.cell(current_row, 3).value = log['is_active'].capitalize()
                    ws.cell(current_row, 4).value = dur_str

                    # заливка статуса
                    cell_stat = ws.cell(current_row, 3)
                    if log['is_active'] == 'active':
                        cell_stat.fill = green_fill
                    elif log['is_active'] == 'break':
                        cell_stat.fill = orange_fill
                    elif log['is_active'] == 'training':
                        cell_stat.fill = yellow_fill
                    elif log['is_active'] == 'tech':
                        cell_stat.fill = purple_fill
                    elif log['is_active'] == 'iesigning':
                        cell_stat.fill = blue_fill
                    elif log['is_active'] == 'inactive':
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

    def generate_users_report(self, current_date=None):
        """
        Generates an Excel report of all operators with columns: ФИО | Направление | Супервайзер | Статус | Ставка | Дата принятия | Логин.
    
        :param current_date: Optional, current date for filename (defaults to today).
        :return: (filename, content) or (None, None) on error.
        """
        try:
            if current_date is None:
                current_date = date.today()
            else:
                current_date = datetime.strptime(current_date, "%Y-%m-%d").date() if isinstance(current_date, str) else current_date
    
            filename = f"users_report_{current_date.strftime('%Y-%m-%d')}.xlsx"
    
            # Fetch all operators with required data
            with self._get_cursor() as cursor:
                cursor.execute("""
                    SELECT u.name, COALESCE(d.name, 'N/A') as direction, COALESCE(s.name, 'N/A') as supervisor,
                           u.status, u.rate, u.hire_date, u.supervisor_id, u.login
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
                name, direction, supervisor, status, rate, hire_date, sup_id, login = row
                operators_by_supervisor[sup_id].append((name, direction, supervisor, status, rate, hire_date, login))
                if sup_id and supervisor != 'N/A':
                    supervisors[sup_id] = supervisor
    
            # Create workbook
            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = "Summary"
    
            # Headers for summary
            headers = ["ФИО", "Направление", "Супервайзер", "Статус", "Ставка", "Дата принятия", "Логин"]
            for col, header in enumerate(headers, start=1):
                cell = ws_summary.cell(1, col)
                cell.value = header
                cell.font = Font(bold=True)
    
            # Fill summary data
            row = 2
            for op in all_operators:
                name, direction, supervisor, status, rate, hire_date, _, login = op
                ws_summary.cell(row, 1).value = name
                ws_summary.cell(row, 2).value = direction
                ws_summary.cell(row, 3).value = supervisor
                ws_summary.cell(row, 4).value = status
                ws_summary.cell(row, 5).value = float(rate) if rate else 1.0
                ws_summary.cell(row, 6).value = hire_date.strftime('%Y-%m-%d') if hire_date else 'N/A'
                ws_summary.cell(row, 7).value = login
                row += 1
    
            # Add table to summary
            tab_summary = Table(displayName="SummaryTable", ref=f"A1:G{row-1}")
            style = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=True, showLastColumn=False, showRowStripes=True, showColumnStripes=False)
            tab_summary.tableStyleInfo = style
            ws_summary.add_table(tab_summary)
    
            # Auto-adjust columns
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
                for name, direction, supervisor, status, rate, hire_date, login in ops:
                    ws.cell(row, 1).value = name
                    ws.cell(row, 2).value = direction
                    ws.cell(row, 3).value = supervisor
                    ws.cell(row, 4).value = status
                    ws.cell(row, 5).value = float(rate) if rate else 1.0
                    ws.cell(row, 6).value = hire_date.strftime('%Y-%m-%d') if hire_date else 'N/A'
                    ws.cell(row, 7).value = login
                    row += 1
    
                # Add table
                if row > 2:
                    table_ref = f"A1:G{row-1}"
                    tab = Table(displayName=f"Table_{sheet_title.replace(' ', '_')}", ref=table_ref)
                    tab.tableStyleInfo = style
                    ws.add_table(tab)
    
                # Auto-adjust columns
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

    def generate_excel_report_from_view(self,
        operators: List[Dict[str, Any]],
        trainings_map: Dict[int, Dict[int, List[Dict[str, Any]]]],
        month: str,  # 'YYYY-MM'
        filename: str = None
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
            return round(num, 1)

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
            return round(num, 1)

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
            headers = ["Оператор", "Ставка", "Норма часов (ч)"] + [f"{d:02d}.{mon:02d}" for d in days] + ["Итого"]
            if extra_cols:
                headers += [c[0] for c in extra_cols]
            _make_header(ws, headers)
            row = 2
            for op in operators:
                daily = op.get('daily', {})
                name = op.get('name') or f"op_{op.get('operator_id')}"
                set_cell(ws, row, 1, name, align_center=False)
                # Ставка и Норма оставляем без изменения/округления
                set_cell(ws, row, 2, float(op.get('rate') or 0), align_center=False)
                set_cell(ws, row, 3, float(op.get('norm_hours') or 0), align_center=False)
                total = 0.0
                totals = { 'work_time': 0.0, 'calls': 0, 'efficiency': 0.0 }
                for c_idx, day in enumerate(days, start=4):
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
                total_col = 4 + len(days)
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
            for i in range(2, 5 + len(days) + (len(extra_cols) if extra_cols else 0)):
                col = ws.cell(1, i).column_letter
                ws.column_dimensions[col].width = 12

        def build_work_time_sheet():
            ws = wb.create_sheet(title='Отработанные часы'[:31])
            headers = ["Оператор", "Ставка", "Норма часов (ч)"] + [f"{d:02d}.{mon:02d}" for d in days] + ["Итого часов", "С выч. тренинга", "Вып нормы (%)", "Выработка"]
            _make_header(ws, headers)
            row = 2
            for op in operators:
                daily = op.get('daily', {})
                name = op.get('name') or f"op_{op.get('operator_id')}"
                set_cell(ws, row, 1, name, align_center=False)
                set_cell(ws, row, 2, float(op.get('rate') or 0), align_center=False)
                norm = float(op.get('norm_hours') or 0)
                set_cell(ws, row, 3, norm, align_center=False)

                total_work = 0.0
                total_counted_trainings = 0.0

                for c_idx, day in enumerate(days, start=4):
                    dkey = str(day)
                    work_val = 0.0
                    d = daily.get(dkey)
                    if d:
                        work_val = float(d.get('work_time') or 0.0)
                    trainings_for_day = trainings_map.get(op.get('operator_id'), {}).get(day, []) if trainings_map else []
                    counted_for_day = 0.0
                    for t in trainings_for_day:
                        dur = compute_training_duration_hours(t)
                        if t.get('count_in_hours'):
                            counted_for_day += dur
                    total_work += work_val
                    total_counted_trainings += counted_for_day
                    cell_val = fmt_day_value('work_time', work_val)
                    fill = FILL_POS if (isinstance(cell_val, (int, float)) and cell_val > 0) else None
                    set_cell(ws, row, c_idx, cell_val, fill=fill)

                itogo_chasov = total_work + total_counted_trainings
                set_cell(ws, row, 4 + len(days), fmt_total_value('work_time', itogo_chasov))
                set_cell(ws, row, 5 + len(days), fmt_total_value('work_time', total_work))
                if norm and norm != 0:
                    percent = round((itogo_chasov / norm) * 100, 1)
                    percent_display = f"{percent}%"
                else:
                    percent_display = None
                set_cell(ws, row, 6 + len(days), percent_display)
                set_cell(ws, row, 7 + len(days), fmt_total_value('work_time', round(norm - itogo_chasov, 1) if norm is not None else None))

                row += 1

            ws.column_dimensions['A'].width = 24
            for i in range(2, 8 + len(days)):
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

        build_work_time_sheet()
        build_generic_sheet('break_time', 'Перерыв', 'break_time', is_hour=True)
        build_calls_sheet()
        build_efficiency_sheet()

        ws_t = wb.create_sheet(title='Тренинги'[:31])
        headers = ["Оператор"] + [f"{d:02d}.{mon:02d}" for d in days] + ["Всего (ч)", "Засчитано (ч)", "Не засчитано (ч)"]
        _make_header(ws_t, headers)
        row = 2
        for op in operators:
            name = op.get('name') or f"op_{op.get('operator_id')}"
            set_cell(ws_t, row, 1, name, align_center=False)
            total_all = 0.0
            total_counted = 0.0
            total_not = 0.0
            op_trainings = trainings_map.get(op.get('operator_id')) or {}
            for c_idx, day in enumerate(days, start=2):
                arr = op_trainings.get(day, []) if isinstance(op_trainings, dict) else []
                counted = 0.0
                not_counted = 0.0
                for t in arr:
                    dur = compute_training_duration_hours(t)
                    total_all += dur
                    if t.get('count_in_hours'):
                        counted += dur
                        total_counted += dur
                    else:
                        not_counted += dur
                        total_not += dur
                if counted == 0 and not_counted == 0:
                    set_cell(ws_t, row, c_idx, "")
                else:
                    c_val = round(counted, 1)
                    n_val = round(not_counted, 1)
                    set_cell(ws_t, row, c_idx, f"{c_val} / {n_val}", fill=FILL_POS)

            set_cell(ws_t, row, 2 + len(days), round(total_all, 1))
            set_cell(ws_t, row, 3 + len(days), round(total_counted, 1))
            set_cell(ws_t, row, 4 + len(days), round(total_not, 1))
            row += 1

        ws_t.column_dimensions['A'].width = 24
        for i in range(2, 5 + len(days)):
            col = ws_t.cell(1, i).column_letter
            ws_t.column_dimensions[col].width = 14

        out = BytesIO()
        wb.save(out)
        out.seek(0)
        content = out.getvalue()
        if filename is None:
            filename = f"report_{month}.xlsx"
        return filename, content

# Initialize database
db = Database()
