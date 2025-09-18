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
import calendar
import pandas as pd

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

def process_timesheet(file_url, supervisor_id):
    """
    Process the last sheet of a Google Sheets timesheet to extract operator details.
    Args:
        file_url (str): Google Sheets URL (e.g., https://docs.google.com/spreadsheets/d/.../edit?gid=...).
    Returns:
        list: List of dicts with ФИО, Кол-во часов, Кол-во часов тренинга, Штрафы, Год-Месяц, daily_hours.
    """
    # Set current year-month
    current_month = datetime.now().strftime('%Y-%m')
    # Convert Google Sheets URL to Excel export URL
    sheet_id_match = re.search(r'spreadsheets/d/([a-zA-Z0-9_-]+)', file_url)
    if not sheet_id_match:
        return {"error": "Invalid Google Sheets URL format."}
    sheet_id = sheet_id_match.group(1)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
    # Download the file
    local_file = 'temp_timesheet.xlsx'
    try:
        response = requests.get(export_url)
        if response.status_code != 200:
            return {"error": f"Failed to download file: HTTP {response.status_code}"}
        with open(local_file, 'wb') as f:
            f.write(response.content)
    except Exception as e:
        return {"error": f"Failed to download file: {str(e)}"}
    try:
        # Load the Excel file and get the last sheet
        workbook = openpyxl.load_workbook(local_file, data_only=True)
        sheet_names = workbook.sheetnames
        last_sheet = workbook[sheet_names[-1]]
        current_sheet_name = last_sheet.title
        
        prev_month = datetime.now().replace(day=1) - timedelta(days=1)
        prev_month_str = prev_month.strftime('%Y-%m')
        
        db = Database() # Assuming db is global or accessible
        with db._get_cursor() as cursor:
            cursor.execute("""
                SELECT sheet_name FROM processed_sheets
                WHERE supervisor_id = %s AND processed_month = %s AND sheet_name = %s
            """, (supervisor_id, prev_month_str, current_sheet_name))
            if cursor.fetchone():
                return {"error": f"Sheet '{current_sheet_name}' was already processed last month"}
        # Set limits (55 rows, 50 columns)
        MAX_ROWS = 55
        MAX_COLS = 50
        # Extract data
        data = []
        fio_col, hours_col, training_col, norm_col = None, None, None, None
        # First pass - find headers in the main table (only check first row)
        for row_idx, row in enumerate(last_sheet.iter_rows(max_row=1, max_col=MAX_COLS, values_only=True), 1):
            headers = [str(cell).strip().replace('"', '').replace('\n', '').replace(' ', ' ') if cell else '' for cell in row]
            for idx, header in enumerate(headers):
                if idx >= MAX_COLS:
                    break
                header_lower = header.lower()
                if 'фио' in header_lower:
                    if fio_col is None:
                        fio_col = idx
                elif 'итого' in header_lower or 'итоговые' in header_lower:
                    if hours_col is None: # Only set if not already found
                        hours_col = idx
                elif 'часы тренинга' in header_lower or 'тренинг' in header_lower:
                    if training_col is None: # Only set if not already found
                        training_col = idx
                elif 'норма' in header_lower or 'норма часов' in header_lower:
                    if norm_col is None: # Only set if not already found
                        norm_col = idx
            
            if fio_col is None or hours_col is None:
                os.remove(local_file)
                return {"error": f"Required columns (ФИО, Итого) not found in sheet '{last_sheet.title}'."}
        
        # Identify day columns between norm_col and hours_col
        if norm_col is not None and hours_col is not None and norm_col < hours_col - 1:
            day_map = {day: idx for idx, day in enumerate(range(norm_col + 1, hours_col), start=1)}
        
        # Find the end of main data (first empty cell in ФИО column after header)
        main_data_end_row = None
        for row_idx, row in enumerate(last_sheet.iter_rows(min_row=2, max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True), 2):
            if not row[fio_col]: # Empty ФИО cell
                main_data_end_row = row_idx
                break
        # Second pass - find fines section (look for "ФИО" again after main data)
        fines_start_row = None
        fines_fio_col = None
        fines_amount_col = None
        
        for row_idx, row in enumerate(last_sheet.iter_rows(min_row=main_data_end_row+1 if main_data_end_row else 2,
                                                          max_row=MAX_ROWS,
                                                          max_col=MAX_COLS,
                                                          values_only=True),
                                  main_data_end_row+1 if main_data_end_row else 2):
            if not fines_start_row:
                for idx, cell in enumerate(row):
                    if idx >= MAX_COLS:
                        break
                    if cell and 'фио' in str(cell).lower():
                        fines_fio_col = idx
                    elif cell and ('штраф' in str(cell).lower() or 'сумма' in str(cell).lower()):
                        fines_amount_col = idx
                
                if fines_fio_col is not None:
                    fines_start_row = row_idx
                    break
        # Create a mapping of operator names to fines
        fines_map = {}
        if fines_start_row and fines_fio_col is not None and fines_amount_col is not None:
            for row in last_sheet.iter_rows(min_row=fines_start_row+1,
                                           max_row=MAX_ROWS,
                                           max_col=MAX_COLS,
                                           values_only=True):
                if fines_fio_col >= len(row) or fines_amount_col >= len(row):
                    continue
                    
                name = str(row[fines_fio_col]).strip() if row[fines_fio_col] else None
                fine = row[fines_amount_col] if fines_amount_col < len(row) else None
                
                if name and name != 'None' and fine is not None:
                    try:
                        fines_map[name] = float(fine)
                    except (ValueError, TypeError):
                        fines_map[name] = 0.0
        # Process main data rows (2 to main_data_end_row-1)
        for row_idx, row in enumerate(last_sheet.iter_rows(min_row=2,
                                                          max_row=main_data_end_row-1 if main_data_end_row else MAX_ROWS,
                                                          max_col=MAX_COLS,
                                                          values_only=True), 2):
            if fio_col >= len(row) or hours_col >= len(row):
                continue
                
            if row[fio_col] and row[hours_col] is not None:
                operator_name = str(row[fio_col]).strip()
                training_hours = 0.0
                norm_hours = 0.0
                if training_col is not None and training_col < len(row) and row[training_col] is not None:
                    try:
                        training_hours = float(row[training_col])
                    except (ValueError, TypeError):
                        training_hours = 0.0
                if norm_col is not None and norm_col < len(row) and row[norm_col] is not None:
                    try:
                        norm_hours = float(row[norm_col])
                    except (ValueError, TypeError):
                        norm_hours = 0.0
                # Extract daily hours
                daily_hours = [0.0] * 31
                for col, day in day_map.items():
                    if col < len(row):
                        try:
                            daily_hours[day - 1] = float(round(row[col],2) or 0.0)
                        except (ValueError, TypeError):
                            daily_hours[day - 1] = 0.0
                entry = {
                    'ФИО': operator_name,
                    'Кол-во часов': round(float(row[hours_col]) if row[hours_col] else 0.0, 2),
                    'Кол-во часов тренинга': round(training_hours, 2),
                    'Штрафы': round(fines_map.get(operator_name, 0.0), 2),
                    'Год-Месяц': current_month,
                    'Норма часов': norm_hours,
                    'daily_hours': daily_hours
                }
                data.append(entry)
       
        with db._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO processed_sheets (supervisor_id, sheet_name, processed_month)
                VALUES (%s, %s, %s)
                ON CONFLICT (supervisor_id, processed_month)
                DO UPDATE SET
                    sheet_name = EXCLUDED.sheet_name,
                    processed_at = CURRENT_TIMESTAMP
            """, (supervisor_id, current_sheet_name, current_month))
        # Clean up
        os.remove(local_file)
        return data
    except Exception as e:
        if os.path.exists(local_file):
            os.remove(local_file)
        return {"error": f"Error processing file: {str(e)}"}


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
                        'Мониторинг', 'Практика в офисе таксопарка'
                    )),
                    comment TEXT,
                    created_by INTEGER REFERENCES users(id),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                    daily_hours FLOAT[] NOT NULL DEFAULT ARRAY[]::FLOAT[],
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    calls_per_hour FLOAT NOT NULL DEFAULT 0.0,
                    UNIQUE(operator_id, month)
                );
            """)
            cursor.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'work_hours' AND column_name = 'calls_per_hour'
                    ) THEN
                        ALTER TABLE work_hours ADD COLUMN calls_per_hour FLOAT NOT NULL DEFAULT 0.0;
                    END IF;
                END $$;
            """)
            # Processed sheets table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS processed_sheets (
                    supervisor_id INTEGER NOT NULL REFERENCES users(id),
                    sheet_name TEXT NOT NULL,
                    processed_month VARCHAR(7) NOT NULL,
                    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (supervisor_id, processed_month)
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

    def create_user(self, telegram_id, name, role, direction_id=None, hire_date=None, supervisor_id=None, login=None, password=None, hours_table_url=None):
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
                    INSERT INTO users (telegram_id, name, role, direction_id, hire_date, supervisor_id, login, password_hash, hours_table_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (telegram_id, name, role, direction_id, hire_date, supervisor_id, login, password_hash, hours_table_url))
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

    def add_work_hours(self, operator_id, regular_hours, training_hours, fines=0.0, norm_hours=0.0, month=None):
        month = month or datetime.now().strftime('%Y-%m')
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO work_hours (operator_id, month, regular_hours, training_hours, fines, norm_hours)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (operator_id, month)
                DO UPDATE SET 
                    regular_hours = EXCLUDED.regular_hours,
                    training_hours = EXCLUDED.training_hours,
                    fines = EXCLUDED.fines,
                    norm_hours = EXCLUDED.norm_hours
            """, (operator_id, month, regular_hours, training_hours, fines, norm_hours))

    def process_calls_sheet(self, file):
        try:
            filename = file.filename.lower()
    
            # Загружаем файл в DataFrame
            if filename.endswith(".csv"):
                df = pd.read_csv(file, sep=";")
                sheet_name = "CSV"
            elif filename.endswith((".xls", ".xlsx")):
                excel = pd.ExcelFile(file)
                sheet_name = excel.sheet_names[0]
                df = pd.read_excel(excel, sheet_name=sheet_name)
            else:
                return None, None, "Неверный формат файла. Поддерживаются только CSV, XLS, XLSX"
    
            # Проверяем обязательные колонки
            required_columns = [
                "Name", 
                "Количество поступивших", 
                "Время в работе", 
                "Всего перерыва",
                "Тех. причина",
                "Тренинг"
            ]
            for col in required_columns:
                if col not in df.columns:
                    return None, None, f"Не найдена колонка: {col}"
    
            operators = []
            month = date.today().strftime("%Y-%m")
    
            with self._get_cursor() as cursor:
                for _, row in df.iterrows():
                    try:
                        name = str(row["Name"]).strip()
                        calls = int(row["Количество поступивших"])
    
                        # Конвертация времени в секунды
                        work_time = pd.to_timedelta(str(row["Время в работе"])).total_seconds()
                        break_time = pd.to_timedelta(str(row["Всего перерыва"])).total_seconds()
                        tech_time = pd.to_timedelta(str(row["Тех. причина"])).total_seconds()
                        training_time = pd.to_timedelta(str(row["Тренинг"])).total_seconds()
    
                        # Чистое рабочее время
                        net_hours = (work_time - break_time - tech_time - training_time) / 3600
                        cph = round(calls / net_hours, 1) if net_hours > 0 else 0
    
                        # Проверяем, есть ли оператор в БД
                        cursor.execute(
                            "SELECT id FROM users WHERE name = %s AND role = 'operator'",
                            (name,)
                        )
                        result = cursor.fetchone()
                        if not result:
                            continue
                        operator_id = result[0]
    
                        # Обновляем / вставляем данные по звонкам
                        cursor.execute("""
                            INSERT INTO work_hours (operator_id, month, calls_per_hour)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (operator_id, month)
                            DO UPDATE SET calls_per_hour = EXCLUDED.calls_per_hour;
                        """, (operator_id, month, cph))
    
                        operators.append((name, cph))
    
                    except Exception as e:
                        print(f"Ошибка при обработке строки {row}: {e}")
                        continue
    
            return sheet_name, operators, None
    
        except Exception as e:
            return None, None, str(e)

    
    def process_and_upload_timesheet(self):
        """
        Process timesheets from all supervisors' hours_table_url and upload data to work_hours table.
        Returns:
            dict: Aggregated status of all operations with processed and skipped records per supervisor.
        """
        # Get all supervisors with their hours_table_url
        supervisors = self.get_supervisors()
        if not supervisors:
            return {
                "status": "error",
                "message": "No supervisors found in database",
                "total_processed": 0,
                "total_skipped": 0,
                "details": []
            }
        total_processed = 0
        total_skipped = 0
        results = []
        for supervisor in supervisors:  
            supervisor_id, supervisor_name, hours_table_url, _, _, _ = supervisor
            
            if not hours_table_url:
                logging.warning(f"Supervisor {supervisor_name} (ID: {supervisor_id}) has no hours_table_url")
                results.append({
                    "supervisor_id": supervisor_id,
                    "supervisor_name": supervisor_name,
                    "status": "skipped",
                    "message": "No hours_table_url configured",
                    "processed": 0,
                    "skipped": 0,
                    "skipped_operators": []
                })
                continue
            # Process the timesheet for this supervisor
            timesheet_data = process_timesheet(hours_table_url,supervisor_id)
            
            if isinstance(timesheet_data, dict) and "error" in timesheet_data:
                error_msg = timesheet_data["error"]
                logging.error(f"Error processing timesheet for {supervisor_name}: {error_msg}")
                results.append({
                    "supervisor_id": supervisor_id,
                    "supervisor_name": supervisor_name,
                    "status": "error",
                    "message": error_msg,
                    "processed": 0,
                    "skipped": 0,
                    "skipped_operators": []
                })
                continue
            processed = 0
            skipped = 0
            skipped_operators = []
            with self._get_cursor() as cursor:
                for entry in timesheet_data:
                    fio = entry['ФИО']
                    regular_hours = entry['Кол-во часов']
                    training_hours = entry['Кол-во часов тренинга']
                    fines = entry['Штрафы']
                    month = entry['Год-Месяц']
                    norm_hours = entry.get('Норма часов', 0.0) # Use get() with default value
                    daily_hours = entry.get('daily_hours', [0.0] * 31)
                    # Find operator by name who is under this supervisor
                    cursor.execute("""
                        SELECT id FROM users
                        WHERE name = %s
                        AND role = 'operator'
                        AND supervisor_id = %s
                    """, (fio, supervisor_id))
                    operator = cursor.fetchone()
                    if operator:
                        operator_id = operator[0]
                        cursor.execute("""
                            INSERT INTO work_hours (
                                operator_id,
                                month,
                                regular_hours,
                                training_hours,
                                fines,
                                norm_hours,
                                daily_hours
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (operator_id, month)
                            DO UPDATE SET
                                regular_hours = EXCLUDED.regular_hours,
                                training_hours = EXCLUDED.training_hours,
                                fines = EXCLUDED.fines,
                                norm_hours = EXCLUDED.norm_hours,
                                daily_hours = EXCLUDED.daily_hours
                        """, (operator_id, month, regular_hours, training_hours, fines, norm_hours, daily_hours))
                        processed += 1
                    else:
                        logging.warning(f"Operator {fio} not found under supervisor {supervisor_name}")
                        skipped += 1
                        skipped_operators.append(fio)
            total_processed += processed
            total_skipped += skipped
            results.append({
                "supervisor_id": supervisor_id,
                "supervisor_name": supervisor_name,
                "status": "success",
                "processed": processed,
                "skipped": skipped,
                "skipped_operators": skipped_operators
            })
        return {
            "status": "completed",
            "total_processed": total_processed,
            "total_skipped": total_skipped,
            "details": results
        }

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
                    'hire_date': row[3],
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
                wh.daily_hours,
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
            query += " GROUP BY u.id, u.name, u.direction_id, wh.regular_hours, wh.training_hours, wh.fines, wh.norm_hours, wh.daily_hours, wh.calls_per_hour"
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "operator_id": row[0],
                    "operator_name": row[1],
                    "direction": row[2],
                    "regular_hours": float(row[3]) if row[3] is not None else 0,
                    "training_hours": float(row[4]) if row[4] is not None else 0,
                    "fines": float(row[5]) if row[5] is not None else 0,
                    "norm_hours": float(row[6]) if row[6] is not None else 0,
                    "daily_hours": row[7] if row[7] is not None else [0.0]*31,
                    "calls_per_hour": row[8]
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
            LEFT JOIN directions d ON c.direction_id = d.id AND d.is_active = TRUE  -- Added is_active filter
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
        Функция для генерации отчёта в формате Excel за указанный месяц (в формате YYYY-MM) до текущего дня (если месяц текущий).
        Добавлено: в Summary — итоги по всем статусам (кроме 'inactive').
        :param supervisor_id: ID супервайзера, для которого генерируется отчёт
        :param month: Опционально, месяц в формате YYYY-MM. Если не указан, используется текущий месяц.
        :param current_date: Опционально, текущая дата (для тестирования), по умолчанию - datetime.date.today()
        """
        def sanitize_table_name(name):
            """
            Sanitizes a name to be used as an Excel table name by replacing invalid characters
            and ensuring it starts with a letter or underscore.
            """
            sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
            if not sanitized:
                sanitized = "_t"
            if not sanitized[0].isalpha() and sanitized[0] != '_':
                sanitized = f"_{sanitized}"
            return sanitized[:255]

        def format_duration(duration):
            """
            Formats a timedelta into a string like '1h 23m 45s', omitting zero parts.
            """
            if duration.total_seconds() < 0:
                return "N/A"
            hours = int(duration.total_seconds() // 3600)
            minutes = int((duration.total_seconds() % 3600) // 60)
            seconds = int(duration.total_seconds() % 60)
            parts = []
            if hours > 0:
                parts.append(f"{hours}h")
            if minutes > 0:
                parts.append(f"{minutes}m")
            if seconds > 0 or not parts:
                parts.append(f"{seconds}s")
            return " ".join(parts)

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
            month_start = datetime(year, mon, 1, 0, 0, 0)
            days_in_month = calendar.monthrange(year, mon)[1]
            month_end = date(year, mon, days_in_month)
            end_date = min(month_end, current_date)
            dates = [month_start_date + timedelta(days=i) for i in range((end_date - month_start_date).days + 1)]

            # Определяем end_time как конец последнего дня периода
            end_time = datetime.combine(end_date, dt_time(23, 59, 59))

            # Получаем данные с использованием курсора класса
            with self._get_cursor() as cursor:
                # Получаем список операторов супервайзера
                cursor.execute("""
                    SELECT id, name 
                    FROM users 
                    WHERE supervisor_id = %s AND role = 'operator'
                """, (supervisor_id,))
                operators = cursor.fetchall()

                if not operators:
                    raise ValueError("No operators found for the given supervisor")

                operators_dict = {op[0]: op[1] for op in operators}

                # Получаем все логи за период для всех операторов супервайзера одним запросом
                cursor.execute("""
                    SELECT o.operator_id, o.change_time, o.is_active 
                    FROM operator_activity_logs o
                    JOIN users u ON o.operator_id = u.id
                    WHERE u.supervisor_id = %s AND u.role = 'operator'
                    AND o.change_time >= %s 
                    AND o.change_time < %s + INTERVAL '1 DAY' 
                    ORDER BY o.operator_id, o.change_time
                """, (supervisor_id, month_start, end_date))
                all_logs = cursor.fetchall()

            # Группируем логи по операторам и вычисляем counts для summary
            # counts_per_op[op_id][date][status] = count
            counts_per_op = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
            total_counts_per_op = defaultdict(lambda: defaultdict(int))
            logs_per_op = defaultdict(list)

            # Статусы, по которым хотим показывать итог (исключаем 'inactive')
            summary_statuses = ['active', 'break', 'training', 'tech', 'iesigning']

            for log in all_logs:
                op_id, change_time, is_active = log
                status = is_active if isinstance(is_active, str) else (str(is_active) if is_active is not None else 'inactive')
                dt = change_time.date()
                log_dict = {'change_time': change_time, 'is_active': status, 'date': dt}
                logs_per_op[op_id].append(log_dict)

                # Считаем по-статусно
                counts_per_op[op_id][dt][status] += 1
                total_counts_per_op[op_id][status] += 1

                # Поддерживаем старую логику активаций/деактиваций (act = 'active', deact = not 'active')
                if status == 'active':
                    total_counts_per_op[op_id]['act'] += 1
                else:
                    total_counts_per_op[op_id]['deact'] += 1
                    # (обратите внимание: это сохраняет прежнее поведение, где любое не-'active' считалось деактивацией)

            # Создаём workbook
            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = "Summary"

            # Заголовки для summary листа
            ws_summary.cell(1, 1).value = "ФИО"
            for col, dt in enumerate(dates, start=2):
                ws_summary.cell(1, col).value = dt.strftime("%Y-%m-%d")

            # Колонки итогов по каждому статусу (кроме 'inactive'), затем Итого активаций и Итого деактиваций
            status_labels = {
                'active': 'Актив',
                'break': 'Перерыв',
                'training': 'Обучение',
                'tech': 'Тех',
                'iesigning': 'IEsigning'
            }

            # Позиция, с которой начнутся итоговые колонки
            totals_start_col = 2 + len(dates)
            # Добавляем итоговые заголовки по статусам (кроме inactive)
            for idx, st in enumerate(summary_statuses):
                ws_summary.cell(1, totals_start_col + idx).value = f"Итого {status_labels.get(st, st)}"

            # Добавляем две старые колонки "Итого активаций" и "Итого деактиваций" после статусных колонок
            col_act = totals_start_col + len(summary_statuses)
            col_deact = col_act + 1
            ws_summary.cell(1, col_act).value = "Итого активаций"
            ws_summary.cell(1, col_deact).value = "Итого деактиваций"

            # Стили для текста (шрифты для rich text)
            green_font = InlineFont(color="00FF00")
            red_font = InlineFont(color="FF0000")
            orange_font = InlineFont(color="FFA500")
            yellow_font = InlineFont(color="FFFF00")
            purple_font = InlineFont(color="800080")
            blue_font = InlineFont(color="5195F5")
            default_font = InlineFont()

            status_font_map = {
                'active': green_font,
                'break': orange_font,
                'training': yellow_font,
                'tech': purple_font,
                'iesigning': blue_font
            }

            # Заполняем summary
            row = 2
            for op_id, name in operators_dict.items():
                ws_summary.cell(row, 1).value = name

                for col, dt in enumerate(dates, start=2):
                    activations = counts_per_op[op_id][dt].get('active', 0)
                    # По старой логике: все не-active считались деактивациями на дату
                    # Посчитаем deactivations как сумму всех statuses, кроме 'active'
                    deactivations = sum(count for st, count in counts_per_op[op_id][dt].items() if st != 'active')
                    cell = ws_summary.cell(row, col)
                    rt = CellRichText([
                        TextBlock(green_font, str(activations)),
                        TextBlock(default_font, " | "),
                        TextBlock(red_font, str(deactivations))
                    ])
                    cell.value = rt
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrapText=True)

                # Итоговые столбцы по каждому статусу
                for idx, st in enumerate(summary_statuses):
                    cnt = total_counts_per_op[op_id].get(st, 0)
                    cell = ws_summary.cell(row, totals_start_col + idx)
                    # Используем цветной шрифт для каждого статуса
                    font_for_status = status_font_map.get(st, default_font)
                    cell.value = CellRichText([TextBlock(font_for_status, str(cnt))])
                    cell.alignment = Alignment(horizontal='center', vertical='center')

                # Итоговые активации/деактивации (старые)
                cell_act = ws_summary.cell(row, col_act)
                cell_act.value = CellRichText([TextBlock(green_font, str(total_counts_per_op[op_id].get('act', 0)))])
                cell_act.alignment = Alignment(horizontal='center', vertical='center')

                cell_deact = ws_summary.cell(row, col_deact)
                cell_deact.value = CellRichText([TextBlock(red_font, str(total_counts_per_op[op_id].get('deact', 0)))])
                cell_deact.alignment = Alignment(horizontal='center', vertical='center')

                row += 1

            # Легенда (цвета)
            legend_row = row + 1
            ws_summary.cell(legend_row, 1).value = "Легенда:"
            legend_row += 1
            # Перечислим статусы с цветами
            for st in summary_statuses:
                font_for_status = status_font_map.get(st, default_font)
                label = status_labels.get(st, st)
                ws_summary.cell(legend_row, 1).value = CellRichText([
                    TextBlock(font_for_status, label),
                    TextBlock(default_font, f" - Количество переходов в статус '{st}'")
                ])
                legend_row += 1
            # Добавим легенду для активаций/деактиваций
            ws_summary.cell(legend_row, 1).value = CellRichText([
                TextBlock(green_font, "Актив"),
                TextBlock(default_font, " - Количество активаций")
            ])
            legend_row += 1
            ws_summary.cell(legend_row, 1).value = CellRichText([
                TextBlock(red_font, "Не-Актив"),
                TextBlock(default_font, " - Количество переходов в не-активное состояние (по старой логике)")
            ])

            # Добавляем таблицу в Summary - вычисляем правую нижнюю ячейку с учётом новых колонок
            last_col_index = col_deact
            last_row_index = row - 1
            tab_ref = f"A1:{ws_summary.cell(row=last_row_index, column=last_col_index).coordinate}"
            tab = Table(displayName="SummaryTable", ref=tab_ref)
            style = TableStyleInfo(name="TableStyleMedium2", showFirstColumn=True,
                                showLastColumn=False, showRowStripes=True, showColumnStripes=False)
            tab.tableStyleInfo = style
            ws_summary.add_table(tab)

            # Устанавливаем границы
            thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
            for r in range(1, last_row_index + 1):
                for c in range(1, last_col_index + 1):
                    ws_summary.cell(r, c).border = thin_border

            # Создаём листы для операторов
            green_fill = PatternFill(start_color="00FF00", end_color="00FF00", fill_type="solid")  # Active - зелёный
            orange_fill = PatternFill(start_color="FFA500", end_color="FFA500", fill_type="solid")  # Break - оранжевый
            yellow_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")  # Training - жёлтый
            purple_fill = PatternFill(start_color="800080", end_color="800080", fill_type="solid")  # Tech - фиолетовый
            red_fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")     # Inactive - красный
            blue_fill = PatternFill(start_color="5195F5", end_color="5195F5", fill_type="solid")  # IEsigning - голубой

            for op_id, name in operators_dict.items():
                ws = wb.create_sheet(title=name[:31])

                # Заголовки
                ws.cell(1, 1).value = "Дата"
                ws.cell(1, 2).value = "Время"
                ws.cell(1, 3).value = "Статус"
                ws.cell(1, 4).value = "Продолжительность состояния"
                ws.cell(1, 1).font = Font(bold=True)
                ws.cell(1, 2).font = Font(bold=True)
                ws.cell(1, 3).font = Font(bold=True)
                ws.cell(1, 4).font = Font(bold=True)

                # Заполняем события
                logs = sorted(logs_per_op[op_id], key=lambda x: x['change_time'])
                current_row = 2
                current_day = None
                day_active_time = timedelta(0)
                for i, log in enumerate(logs):
                    dt = log['date']
                    if current_day is None:
                        current_day = dt
                    if dt != current_day:
                        # Вставляем строку итого для предыдущего дня
                        ws.cell(current_row, 1).value = current_day.strftime("%Y-%m-%d")
                        ws.cell(current_row, 2).value = "Итого"
                        # Формируем rich text с итогами по статусам (кроме 'inactive')
                        parts = []
                        for st in summary_statuses:
                            cnt = counts_per_op[op_id][current_day].get(st, 0)
                            font = status_font_map.get(st, default_font)
                            if parts:
                                parts.append(TextBlock(default_font, " | "))
                            parts.append(TextBlock(font, f"{cnt}"))
                        # Добавим также (по старой логике) активации|деактивации в конце
                        parts.append(TextBlock(default_font, "  "))
                        parts.append(TextBlock(green_font, str(counts_per_op[op_id][current_day].get('active', 0))))
                        parts.append(TextBlock(default_font, " | "))
                        deacts_today = sum(count for st, count in counts_per_op[op_id][current_day].items() if st != 'active')
                        parts.append(TextBlock(red_font, str(deacts_today)))

                        ws.cell(current_row, 3).value = CellRichText(parts)
                        ws.cell(current_row, 4).value = format_duration(day_active_time)
                        ws.cell(current_row, 1).font = Font(bold=True)
                        ws.cell(current_row, 2).font = Font(bold=True)
                        ws.cell(current_row, 3).font = Font(bold=True)
                        ws.cell(current_row, 4).font = Font(bold=True)
                        current_row += 1

                        # Сбрасываем для нового дня
                        day_active_time = timedelta(0)
                        current_day = dt

                    if i < len(logs) - 1:
                        next_time = logs[i + 1]['change_time']
                    else:
                        next_time = end_time

                    duration = next_time - log['change_time']

                    # Проверка на дубликат состояния
                    if i > 0 and log['is_active'] == logs[i - 1]['is_active']:
                        dur_str = "N/A (дубликат состояния)"
                    else:
                        dur_str = format_duration(duration)
                        if log['is_active'] == 'active':
                            day_active_time += duration

                    ws.cell(current_row, 1).value = dt.strftime("%Y-%m-%d")
                    ws.cell(current_row, 2).value = log['change_time'].strftime("%H:%M:%S")
                    status_text = (log['is_active'] or "unknown").capitalize()
                    cell_status = ws.cell(current_row, 3)
                    cell_status.value = status_text
                    if log['is_active'] == 'active':
                        cell_status.fill = green_fill
                    elif log['is_active'] == 'break':
                        cell_status.fill = orange_fill
                    elif log['is_active'] == 'training':
                        cell_status.fill = yellow_fill
                    elif log['is_active'] == 'tech':
                        cell_status.fill = purple_fill
                    elif log['is_active'] == 'inactive':
                        cell_status.fill = red_fill
                    elif log['is_active'] == 'iesigning':
                        cell_status.fill = blue_fill
                    ws.cell(current_row, 4).value = dur_str

                    current_row += 1

                # Добавляем итого для последнего дня
                if current_day is not None:
                    ws.cell(current_row, 1).value = current_day.strftime("%Y-%m-%d")
                    ws.cell(current_row, 2).value = "Итого"
                    # Итоги по статусам (кроме inactive) для последнего дня
                    parts = []
                    for st in summary_statuses:
                        cnt = counts_per_op[op_id][current_day].get(st, 0)
                        font = status_font_map.get(st, default_font)
                        if parts:
                            parts.append(TextBlock(default_font, " | "))
                        parts.append(TextBlock(font, f"{cnt}"))
                    parts.append(TextBlock(default_font, "  "))
                    parts.append(TextBlock(green_font, str(counts_per_op[op_id][current_day].get('active', 0))))
                    parts.append(TextBlock(default_font, " | "))
                    deacts_today = sum(count for st, count in counts_per_op[op_id][current_day].items() if st != 'active')
                    parts.append(TextBlock(red_font, str(deacts_today)))

                    ws.cell(current_row, 3).value = CellRichText(parts)
                    ws.cell(current_row, 4).value = format_duration(day_active_time)
                    ws.cell(current_row, 1).font = Font(bold=True)
                    ws.cell(current_row, 2).font = Font(bold=True)
                    ws.cell(current_row, 3).font = Font(bold=True)
                    ws.cell(current_row, 4).font = Font(bold=True)
                    current_row += 1

                # Добавляем таблицу
                if current_row > 2:
                    sanitized_name = sanitize_table_name(f"{name}_{op_id}")
                    tab_op = Table(displayName=f"Table_{sanitized_name}", ref=f"A1:D{current_row-1}")
                    style_op = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False,
                                            showLastColumn=False, showRowStripes=True, showColumnStripes=False)
                    tab_op.tableStyleInfo = style_op
                    ws.add_table(tab_op)

                # Авто-подгонка ширины столбцов (приближённо)
                for col in ['A', 'B', 'C', 'D']:
                    ws.column_dimensions[col].auto_size = True

            # Сохраняем в BytesIO
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


    def add_training(self, operator_id, training_date, start_time, end_time, reason, comment, created_by):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO trainings (operator_id, training_date, start_time, end_time, reason, comment, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (operator_id, training_date, start_time, end_time, reason, comment, created_by))
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
            SELECT t.id, t.operator_id, t.training_date, t.start_time, t.end_time, t.reason, t.comment, t.created_at, cb.name as created_by_name
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
                    "created_by_name": row[8] if row[8] else "System"
                } for row in cursor.fetchall()
            ]
    
    def update_training(self, training_id, training_date=None, start_time=None, end_time=None, reason=None, comment=None):
        updates = []
        params = []
        if training_date:
            updates.append("training_date = %s")
            params.append(training_date)
        if start_time:
            updates.append("start_time = %s")
            params.append(start_time)
        if end_time:
            updates.append("end_time = %s")
            params.append(end_time)
        if reason:
            updates.append("reason = %s")
            params.append(reason)
        if comment is not None:
            updates.append("comment = %s")
            params.append(comment)
        
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

# Initialize database
db = Database()
