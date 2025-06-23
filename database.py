import os
import logging
import psycopg2
from contextlib import contextmanager
from passlib.hash import pbkdf2_sha256
from datetime import datetime
import uuid
import requests
import openpyxl
import re
import json
import csv
import io


logging.basicConfig(level=logging.INFO)

def process_timesheet(file_url):
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

        # Set limits (55 rows, 50 columns)
        MAX_ROWS = 55
        MAX_COLS = 50

        # Extract data
        data = []
        fio_col, hours_col, training_col = None, None, None

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
                if training_col is not None and training_col < len(row) and row[training_col] is not None:
                    try:
                        training_hours = float(row[training_col])
                    except (ValueError, TypeError):
                        training_hours = 0.0
                
                entry = {
                    'ФИО': operator_name,
                    'Кол-во часов': round(float(row[hours_col]) if row[hours_col] else 0.0, 2),
                    'Кол-во часов тренинга': round(training_hours, 2),
                    'Штрафы': round(fines_map.get(operator_name, 0.0), 2),
                    'Год-Месяц': current_month
                }
                data.append(entry)

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
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE,
                    name VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL CHECK(role IN ('admin', 'sv', 'operator')),
                    direction VARCHAR(20) CHECK(direction IN ('chat', 'moderator', 'line', NULL)),
                    hire_date DATE,
                    login VARCHAR(255) UNIQUE,
                    password_hash VARCHAR(255),
                    supervisor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    hours_table_url TEXT,
                    scores_table_url TEXT,
                    CONSTRAINT unique_name_role UNIQUE (name, role)
                );
            """)
            # Calls table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calls (
                    id SERIAL PRIMARY KEY,
                    evaluator_id INTEGER NOT NULL REFERENCES users(id),
                    operator_id INTEGER NOT NULL REFERENCES users(id),
                    month VARCHAR(7) NOT NULL DEFAULT TO_CHAR(CURRENT_DATE, 'YYYY-MM'),
                    call_number VARCHAR(255) NOT NULL,
                    phone_number VARCHAR(70) NOT NULL,
                    score FLOAT NOT NULL,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, month)
                );
            """)
            # Indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_calls_month ON calls(month);
                CREATE INDEX IF NOT EXISTS idx_calls_operator_id ON calls(operator_id);
                CREATE INDEX IF NOT EXISTS idx_work_hours_operator_id ON work_hours(operator_id);
                CREATE INDEX IF NOT EXISTS idx_work_hours_month ON work_hours(month);
            """)

    def create_user(self, telegram_id, name, role, direction=None, hire_date=None, supervisor_id=None, login=None, password=None):
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
            cursor.execute("""
                INSERT INTO users (telegram_id, name, role, direction, hire_date, supervisor_id, login, password_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (telegram_id) DO UPDATE
                SET name = EXCLUDED.name,
                    role = EXCLUDED.role,
                    direction = EXCLUDED.direction,
                    hire_date = EXCLUDED.hire_date,
                    supervisor_id = EXCLUDED.supervisor_id,
                    login = EXCLUDED.login,
                    password_hash = EXCLUDED.password_hash
                RETURNING id
            """, (telegram_id, name, role, direction, hire_date, supervisor_id, login, password_hash))
            return cursor.fetchone()[0]

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
            
    def get_operator_credentials(self, operator_id, supervisor_id):
        """Получить логин и хеш пароля оператора с проверкой принадлежности супервайзеру"""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.login 
                FROM users u
                WHERE u.id = %s AND u.role = 'operator' AND u.supervisor_id = %s
            """, (operator_id, supervisor_id))
            return cursor.fetchone()
    
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
            conditions.append(f"{key} = %s")
            params.append(value)
        
        query = f"SELECT id, telegram_id, name, role, direction, hire_date, supervisor_id, login, hours_table_url, scores_table_url FROM users WHERE {' AND '.join(conditions)}"
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()

    def update_user_table(self, user_id, hours_table_url=None, scores_table_url=None):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users 
                SET hours_table_url = COALESCE(%s, hours_table_url),
                    scores_table_url = COALESCE(%s, scores_table_url)
                WHERE id = %s
            """, (hours_table_url, scores_table_url, user_id))

    def add_call_evaluation(self, evaluator_id, operator_id, call_number, phone_number, score, comment=None, month=None):
        month = month or datetime.now().strftime('%Y-%m')
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO calls (evaluator_id, operator_id, month, call_number, phone_number, score, comment)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (evaluator_id, operator_id, month, call_number, phone_number, score, comment))

    def add_work_hours(self, operator_id, regular_hours, training_hours, fines=0.0, month=None):
        month = month or datetime.now().strftime('%Y-%m')
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO work_hours (operator_id, month, regular_hours, training_hours, fines)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (operator_id, month)
                DO UPDATE SET 
                    regular_hours = EXCLUDED.regular_hours,
                    training_hours = EXCLUDED.training_hours,
                    fines = EXCLUDED.fines
            """, (operator_id, month, regular_hours, training_hours, fines))

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
            timesheet_data = process_timesheet(hours_table_url)
            
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
                                fines
                            )
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (operator_id, month)
                            DO UPDATE SET 
                                regular_hours = EXCLUDED.regular_hours,
                                training_hours = EXCLUDED.training_hours,
                                fines = EXCLUDED.fines
                        """, (operator_id, month, regular_hours, training_hours, fines))
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

    def get_operators_by_supervisor(self, supervisor_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, direction, hire_date, hours_table_url, scores_table_url 
                FROM users 
                WHERE supervisor_id = %s AND role = 'operator'
            """, (supervisor_id,))
            return cursor.fetchall()

    def get_hours_summary(self, operator_id=None, month=None):
        query = """
            SELECT 
                u.id AS operator_id,
                u.name AS operator_name,
                u.direction,
                wh.regular_hours,
                wh.training_hours,
                wh.fines
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
            query += " GROUP BY u.id, u.name, u.direction, wh.regular_hours, wh.training_hours, wh.fines"
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "operator_id": row[0],
                    "operator_name": row[1],
                    "direction": row[2],
                    "regular_hours": float(row[3]) if row[3] is not None else 0,
                    "training_hours": float(row[4]) if row[4] is not None else 0,
                    "fines": float(row[5]) if row[5] is not None else 0
                } for row in cursor.fetchall()
            ]
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "operator_id": row[0],
                    "operator_name": row[1],
                    "direction": row[2],
                    "regular_hours": float(row[3]) if row[3] is not None else 0,
                    "training_hours": float(row[4]) if row[4] is not None else 0,
                    "fines": float(row[5]) if row[5] is not None else 0
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
                SELECT id, telegram_id, name, role, direction, hire_date, supervisor_id, login, password_hash, hours_table_url, scores_table_url 
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
            SELECT call_number, month, phone_number, score, comment
            FROM calls
            WHERE operator_id = %s
        """
        params = [operator_id]
        if month:
            query += " AND month = %s"
            params.append(month)
        query += " ORDER BY created_at DESC, call_number"
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "call_number": row[0],
                    "month": row[1],
                    "phone_number": row[2],
                    "score": float(row[3]),
                    "comment": row[4]
                } for row in cursor.fetchall()
            ]

    def get_all_operators(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, direction, hire_date, supervisor_id, scores_table_url, hours_table_url
                FROM users 
                WHERE role = 'operator'
            """)
            return cursor.fetchall()

# Initialize database
db = Database()
