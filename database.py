import os
import logging
import psycopg2
from contextlib import contextmanager
from passlib.hash import pbkdf2_sha256
from datetime import datetime, timedelta
import uuid
import requests
import openpyxl
import re
import json
import csv
import io
import asyncio
from concurrent.futures import ThreadPoolExecutor
import gc

logging.basicConfig(level=logging.INFO)

def process_timesheet(file_url, supervisor_id):
    """
    Process the last sheet of a Google Sheets timesheet to extract operator details.
    Args:
        file_url (str): Google Sheets URL (e.g., https://docs.google.com/spreadsheets/d/.../edit?gid=...).
    Returns:
        list: List of dicts with ФИО, Кол-во часов, Кол-во часов тренинга, Штрафы, Год-Месяц.
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
        fio_col, hours_col, training_col, norm_col  = None, None, None, None

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
                    if hours_col is None:  # Only set if not already found
                        hours_col = idx
                elif 'часы тренинга' in header_lower or 'тренинг' in header_lower:
                    if training_col is None:  # Only set if not already found
                        training_col = idx
                elif 'норма' in header_lower or 'норма часов' in header_lower:
                    if norm_col is None:  # Only set if not already found
                        norm_col = idx
            
            if fio_col is None or hours_col is None:
                os.remove(local_file)
                return {"error": f"Required columns (ФИО, Итого) not found in sheet '{last_sheet.title}'."}

        # Find the end of main data (first empty cell in ФИО column after header)
        main_data_end_row = None
        for row_idx, row in enumerate(last_sheet.iter_rows(min_row=2, max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True), 2):
            if not row[fio_col]:  # Empty ФИО cell
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

                entry = {
                    'ФИО': operator_name,
                    'Кол-во часов': round(float(row[hours_col]) if row[hours_col] else 0.0, 2),
                    'Кол-во часов тренинга': round(training_hours, 2),
                    'Штрафы': round(fines_map.get(operator_name, 0.0), 2),
                    'Год-Месяц': current_month,
                    'Норма часов': norm_hours
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
        os.remove(local_file) if os.path.exists(local_file) else None
        return {"error": f"Error processing file: {str(e)}"}


class Database:
    def __init__(self):
        self.conn_params = {
            'dbname': os.getenv('POSTGRES_DB'),
            'user': os.getenv('POSTGRES_USER'),
            'password': os.getenv('POSTGRES_PASSWORD'),
            'host': os.getenv('POSTGRES_HOST'),
            'port': os.getenv('POSTGRES_PORT', 5432)
        }
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = psycopg2.connect(**self.conn_params)
        try:
            yield conn
        finally:
            conn.close()

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
                    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    version INTEGER NOT NULL DEFAULT 1,
                    previous_version_id INTEGER REFERENCES directions(id)
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS call_directions (
                    call_id INTEGER NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                    direction_id INTEGER NOT NULL REFERENCES directions(id) ON DELETE RESTRICT,
                    PRIMARY KEY (call_id)
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
                CREATE TABLE IF NOT EXISTS calls (
                    id SERIAL PRIMARY KEY,
                    evaluator_id INTEGER NOT NULL REFERENCES users(id),
                    operator_id INTEGER NOT NULL REFERENCES users(id),
                    month VARCHAR(7) NOT NULL DEFAULT TO_CHAR(CURRENT_DATE, 'YYYY-MM'),
                    phone_number VARCHAR(255) NOT NULL,
                    score FLOAT NOT NULL,
                    comment TEXT,
                    audio_path TEXT,
                    is_draft BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_correction BOOLEAN DEFAULT FALSE,
                    previous_version_id INTEGER REFERENCES calls(id),
                    UNIQUE(evaluator_id, operator_id, month, phone_number, score, comment, is_draft)
                );
            """)
            cursor.execute("""
                ALTER TABLE calls
                ADD COLUMN IF NOT EXISTS scores JSONB,
                ADD COLUMN IF NOT EXISTS criterion_comments JSONB;
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, month)
                );
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

            # Indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_calls_month ON calls(month);
                CREATE INDEX IF NOT EXISTS idx_calls_operator_id ON calls(operator_id);
                CREATE INDEX IF NOT EXISTS idx_work_hours_operator_id ON work_hours(operator_id);
                CREATE INDEX IF NOT EXISTS idx_work_hours_month ON work_hours(month);
                CREATE INDEX IF NOT EXISTS idx_calls_operator_month ON calls(operator_id, month);
                CREATE INDEX IF NOT EXISTS idx_directions_name ON directions(name);           
            """)

    def create_user(self, telegram_id, name, role, direction_id=None, hire_date=None, supervisor_id=None, login=None, password=None, scores_table_url=None):
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
                    INSERT INTO users (telegram_id, name, role, direction_id, hire_date, supervisor_id, login, password_hash, scores_table_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (telegram_id, name, role, direction_id, hire_date, supervisor_id, login, password_hash, scores_table_url))
                return cursor.fetchone()[0]
            except psycopg2.IntegrityError as e:
                cursor.execute("ROLLBACK TO SAVEPOINT before_insert")
                if 'unique_name_role' in str(e):
                    cursor.execute("""
                        UPDATE users
                        SET direction_id = COALESCE(%s, direction_id),
                            supervisor_id = COALESCE(%s, supervisor_id),
                            scores_table_url = COALESCE(%s, scores_table_url)
                        WHERE name = %s AND role = %s
                        RETURNING id
                    """, (direction_id, supervisor_id, scores_table_url, name, role))
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
                            scores_table_url = COALESCE(%s, scores_table_url)
                        WHERE telegram_id = %s
                        RETURNING id
                    """, (name, role, direction_id, hire_date, supervisor_id, login, password_hash, scores_table_url, telegram_id))
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
                SELECT id, name, has_file_upload, criteria
                FROM directions
                ORDER BY name
            """)
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "hasFileUpload": row[2],
                    "criteria": row[3]
                } for row in cursor.fetchall()
            ]

    def save_directions(self, directions, admin_id):
        """Сохранить направления в таблицу directions, создавая новые версии при изменениях."""
        with self._get_cursor() as cursor:
            # Получаем текущие активные направления
            cursor.execute("""
                SELECT id, name, has_file_upload, criteria, version
                FROM directions
                WHERE created_by = %s AND is_active = TRUE
            """, (admin_id,))
            existing_directions = {row[1]: {"id": row[0], "has_file_upload": row[2], "criteria": row[3], "version": row[4]} 
                                for row in cursor.fetchall()}
            
            new_direction_names = {d['name'] for d in directions}
            
            for direction in directions:
                name = direction['name']
                has_file_upload = direction['hasFileUpload']
                criteria = json.dumps(direction['criteria'])
                
                if name in existing_directions:
                    # Проверяем, изменились ли критерии или настройки
                    existing = existing_directions[name]
                    if (existing['has_file_upload'] == has_file_upload and 
                        existing['criteria'] == criteria):
                        continue  # Ничего не изменилось, пропускаем
                    
                    # Деактивируем старую версию
                    cursor.execute("""
                        UPDATE directions
                        SET is_active = FALSE
                        WHERE id = %s
                    """, (existing['id'],))
                    
                    # Создаем новую версию
                    cursor.execute("""
                        INSERT INTO directions (
                            name, has_file_upload, criteria, created_by, 
                            version, previous_version_id
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        name, has_file_upload, criteria, admin_id,
                        existing['version'] + 1, existing['id']
                    ))
                else:
                    # Добавляем новое направление
                    cursor.execute("""
                        INSERT INTO directions (
                            name, has_file_upload, criteria, created_by
                        )
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                    """, (name, has_file_upload, criteria, admin_id))
            
            # Деактивируем направления, которых нет в новом списке
            cursor.execute("""
                UPDATE directions
                SET is_active = FALSE
                WHERE created_by = %s AND is_active = TRUE AND name NOT IN %s
            """, (admin_id, tuple(new_direction_names) if new_direction_names else ('',)))
            
            # Обновляем direction_id в users для удалённых направлений
            cursor.execute("""
                UPDATE users
                SET direction_id = NULL
                WHERE direction_id NOT IN (
                    SELECT id FROM directions WHERE is_active = TRUE
                )
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
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET login = %s
                WHERE id = %s AND role = 'operator' AND supervisor_id = %s
                RETURNING id
            """, (new_login, operator_id, supervisor_id))
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
            SELECT u.id, u.telegram_id, u.name, u.role, d.name, u.hire_date, u.supervisor_id, u.login, u.hours_table_url, u.scores_table_url
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
                        scores=None, criterion_comments=None, direction_id=None):
        month = month or datetime.now().strftime('%Y-%m')
        with self._get_cursor() as cursor:
            # Если direction_id не указан, получаем текущий direction оператора
            if not direction_id:
                cursor.execute("""
                    SELECT direction_id FROM users WHERE id = %s
                """, (operator_id,))
                direction_id = cursor.fetchone()[0]
            
            # Проверяем существование direction_id
            if direction_id:
                cursor.execute("""
                    SELECT 1 FROM directions WHERE id = %s
                """, (direction_id,))
                if not cursor.fetchone():
                    direction_id = None
            
            # Проверка на существующий черновик
            if is_draft:
                cursor.execute("""
                    SELECT id, audio_path FROM calls 
                    WHERE evaluator_id = %s 
                    AND operator_id = %s 
                    AND month = %s 
                    AND is_draft = TRUE
                """, (evaluator_id, operator_id, month))
                existing_draft = cursor.fetchone()
                if existing_draft:
                    old_audio_path = existing_draft[1]
                    cursor.execute("""
                        UPDATE calls
                        SET phone_number = %s,
                            score = %s,
                            comment = %s,
                            audio_path = COALESCE(%s, audio_path),
                            created_at = CURRENT_TIMESTAMP,
                            scores = %s,
                            criterion_comments = %s
                        WHERE id = %s
                        RETURNING id
                    """, (phone_number, score, comment, audio_path, 
                        json.dumps(scores) if scores else None,
                        json.dumps(criterion_comments) if criterion_comments else None,
                        existing_draft[0]))
                    if old_audio_path and audio_path and old_audio_path != audio_path:
                        if os.path.exists(old_audio_path):
                            os.remove(old_audio_path)
                    call_id = cursor.fetchone()[0]
                    
                    # Обновляем связь с направлением, если она есть
                    if direction_id:
                        cursor.execute("""
                            INSERT INTO call_directions (call_id, direction_id)
                            VALUES (%s, %s)
                            ON CONFLICT (call_id) 
                            DO UPDATE SET direction_id = EXCLUDED.direction_id
                        """, (call_id, direction_id))
                    
                    return call_id
        
            # Проверка на существующую оценку
            cursor.execute("""
                SELECT id FROM calls 
                WHERE operator_id = %s 
                AND month = %s 
                AND phone_number = %s 
                AND is_draft = FALSE
                ORDER BY created_at DESC
                LIMIT 1
            """, (operator_id, month, phone_number))
            
            existing_call = cursor.fetchone()
            is_correction = existing_call is not None and not is_draft
            
            # Вставляем новую оценку
            cursor.execute("""
                INSERT INTO calls (
                    evaluator_id, operator_id, month, phone_number, score, comment,
                    audio_path, is_draft, is_correction, previous_version_id,
                    scores, criterion_comments
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                evaluator_id, operator_id, month, phone_number, score, comment,
                audio_path, is_draft, is_correction, existing_call[0] if is_correction else None,
                json.dumps(scores) if scores else None,
                json.dumps(criterion_comments) if criterion_comments else None
            ))
            call_id = cursor.fetchone()[0]
            
            # Создаем связь с направлением, если оно указано
            if direction_id:
                cursor.execute("""
                    INSERT INTO call_directions (call_id, direction_id)
                    VALUES (%s, %s)
                """, (call_id, direction_id))
            
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
            supervisor_id, supervisor_name, _, hours_table_url = supervisor
            
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
                    norm_hours = entry.get('Норма часов', 0.0)  # Use get() with default value

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
                                norm_hours
                            )
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (operator_id, month)
                            DO UPDATE SET 
                                regular_hours = EXCLUDED.regular_hours,
                                training_hours = EXCLUDED.training_hours,
                                fines = EXCLUDED.fines,
                                norm_hours = EXCLUDED.norm_hours
                        """, (operator_id, month, regular_hours, training_hours, fines, norm_hours))
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
            
            # Получаем данные о часах
            cursor.execute("""
                SELECT regular_hours, training_hours, fines, norm_hours 
                FROM work_hours 
                WHERE operator_id = %s AND month = %s
            """, (operator_id, current_month))
            hours_data = cursor.fetchone()
            
            # Получаем данные об оценках
            cursor.execute("""
                SELECT COUNT(*), AVG(score) 
                FROM calls 
                WHERE operator_id = %s AND month = %s
            """, (operator_id, current_month))
            evaluations_data = cursor.fetchone()
            
            # Рассчитываем процент выполнения нормы
            norm_hours = hours_data[3] if hours_data and hours_data[3] else 0
            regular_hours = hours_data[0] if hours_data and hours_data[0] else 0
            percent_complete = (regular_hours / norm_hours * 100) if norm_hours > 0 else 0
            
            return {
                'regular_hours': hours_data[0] if hours_data else 0,
                'training_hours': hours_data[1] if hours_data else 0,
                'fines': hours_data[2] if hours_data else 0,
                'norm_hours': norm_hours,
                'percent_complete': round(percent_complete, 2),
                'call_count': evaluations_data[0] or 0,
                'avg_score': float(evaluations_data[1]) if evaluations_data[1] else 0
            }

    def get_operators_by_supervisor(self, supervisor_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, d.name as direction, u.hire_date, u.hours_table_url, u.scores_table_url, s.name as supervisor_name
                FROM users u
                LEFT JOIN directions d ON u.direction_id = d.id
                LEFT JOIN users s ON u.supervisor_id = s.id
                WHERE u.supervisor_id = %s AND u.role = 'operator'
            """, (supervisor_id,))
            return [
                {
                    'id': row[0],
                    'name': row[1],
                    'direction': row[2],
                    'hire_date': row[3],
                    'hours_table_url': row[4],
                    'scores_table_url': row[5],
                    'supervisor_name': row[6]
                } for row in cursor.fetchall()
            ]

    def get_hours_summary(self, operator_id=None, month=None):
        query = """
            SELECT 
                u.id AS operator_id,
                u.name AS operator_name,
                u.direction_id,
                wh.regular_hours,
                wh.training_hours,
                wh.fines,
                wh.norm_hours
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
            query += " GROUP BY u.id, u.name, u.direction_id, wh.regular_hours, wh.training_hours, wh.fines, wh.norm_hours"
        
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
                    "norm_hours": float(row[6]) if row[6] is not None else 0
                } for row in cursor.fetchall()
            ]

    def get_supervisors(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, scores_table_url, hours_table_url 
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
            WITH latest_calls AS (
                SELECT 
                    id,
                    MAX(created_at) as latest_date
                FROM calls
                WHERE operator_id = %s
                GROUP BY id
            )
            SELECT 
                c.id,
                c.month, 
                c.phone_number, 
                c.score, 
                c.comment,
                c.audio_path,
                c.is_draft,
                c.is_correction,
                TO_CHAR(c.created_at, 'YYYY-MM-DD HH24:MI'),
                c.scores,
                c.criterion_comments,
                d.name as direction_name,
                d.criteria as direction_criteria,
                d.has_file_upload as direction_has_file_upload
            FROM calls c
            JOIN latest_calls lc ON c.id = lc.id AND c.created_at = lc.latest_date
            LEFT JOIN call_directions cd ON c.id = cd.call_id
            LEFT JOIN directions d ON cd.direction_id = d.id
            WHERE c.operator_id = %s
        """
        params = [operator_id, operator_id]
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
                    "score": float(row[3]),
                    "comment": row[4],
                    "audio_path": row[5],
                    "is_draft": row[6],
                    "is_correction": row[7],
                    "evaluation_date": row[8],
                    "scores": row[9] if row[9] else [],
                    "criterion_comments": row[10] if row[10] else [],
                    "direction": {
                        "name": row[11],
                        "criteria": row[12] if row[12] else [],
                        "hasFileUpload": row[13] if row[13] is not None else True
                    } if row[11] else None
                } for row in cursor.fetchall()
            ]

    def get_all_operators(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, direction_id, hire_date, supervisor_id, scores_table_url, hours_table_url
                FROM users 
                WHERE role = 'operator'
            """)
            return cursor.fetchall()

# Initialize database
db = Database()
