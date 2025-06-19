import os
import logging
import psycopg2
from contextlib import contextmanager
from passlib.hash import pbkdf2_sha256
from datetime import datetime

logging.basicConfig(level=logging.INFO)

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
                    # Users table with hire_date and operator direction
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            id SERIAL PRIMARY KEY,
                            telegram_id BIGINT UNIQUE,
                            name VARCHAR(255) NOT NULL,
                            role VARCHAR(20) NOT NULL CHECK(role IN ('admin', 'sv', 'operator')),
                            direction VARCHAR(20) CHECK(direction IN ('chat', 'moderator', 'line', NULL)),
                            hire_date DATE,
                            login VARCHAR(50) UNIQUE,
                            password_hash VARCHAR(255),
                            supervisor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                            hours_table_url TEXT,
                            scores_table_url TEXT,
                            CONSTRAINT unique_name_role UNIQUE (name, role)
                        );
                    """)
            # Calls table with automatic month in 'YYYY-MM' format
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calls (
                    id SERIAL PRIMARY KEY,
                    evaluator_id INTEGER NOT NULL REFERENCES users(id),
                    operator_id INTEGER NOT NULL REFERENCES users(id),
                    month VARCHAR(7) NOT NULL DEFAULT TO_CHAR(CURRENT_DATE, 'YYYY-MM'),
                    call_number INTEGER NOT NULL,
                    phone_number VARCHAR(20) NOT NULL,
                    score FLOAT NOT NULL,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Work hours table for regular and training hours
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS work_hours (
                    id SERIAL PRIMARY KEY,
                    operator_id INTEGER NOT NULL REFERENCES users(id),
                    month VARCHAR(7) NOT NULL DEFAULT TO_CHAR(CURRENT_DATE, 'YYYY-MM'),
                    regular_hours FLOAT NOT NULL DEFAULT 0,
                    training_hours FLOAT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(operator_id, month)
                );
            """)
            # Indexes for performance
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_calls_month ON calls(month);
                CREATE INDEX IF NOT EXISTS idx_calls_operator_id ON calls(operator_id);
                CREATE INDEX IF NOT EXISTS idx_work_hours_operator_id ON work_hours(operator_id);
                CREATE INDEX IF NOT EXISTS idx_work_hours_month ON work_hours(month);
            """)

    def create_user(self, telegram_id, name, role, direction=None, hire_date=None, supervisor_id=None, login=None, password=None):
        # Set default login and password if None
        login = login or str(telegram_id)
        password = password or "123321123"
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
        """
        Update telegram_id for a specific user
        """
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

    def add_call_evaluation(self, evaluator_id, operator_id, call_number, phone_number, score, comment, month=None):
        # Use provided month or default to current YYYY-MM
        month = month or datetime.now().strftime('%Y-%m')
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO calls (evaluator_id, operator_id, month, call_number, phone_number, score, comment)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (evaluator_id, operator_id, month, call_number, phone_number, score, comment))

    def add_work_hours(self, operator_id, regular_hours, training_hours, month=None):
        # Use provided month or default to current YYYY-MM
        month = month or datetime.now().strftime('%Y-%m')
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO work_hours (operator_id, month, regular_hours, training_hours)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (operator_id, month)
                DO UPDATE SET 
                    regular_hours = EXCLUDED.regular_hours,
                    training_hours = EXCLUDED.training_hours
            """, (operator_id, month, regular_hours, training_hours))

    def get_operators_by_supervisor(self, supervisor_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, direction, hire_date, hours_table_url, scores_table_url 
                FROM users 
                WHERE supervisor_id = %s AND role = 'operator'
            """, (supervisor_id,))
            return cursor.fetchall()

    def get_calls_summary(self, month=None):
        # Optional month filter for quality table
        query = """
            SELECT 
                u.id AS operator_id,
                u.name AS operator_name,
                u.direction,
                COUNT(c.id) AS call_count,
                AVG(c.score) AS avg_score
            FROM users u
            LEFT JOIN calls c ON u.id = c.operator_id
            WHERE u.role = 'operator'
        """
        params = []
        if month:
            query += " AND c.month = %s"
            params.append(month)
        query += " GROUP BY u.id, u.name, u.direction"
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "operator_id": row[0],
                    "operator_name": row[1],
                    "direction": row[2],
                    "call_count": row[3],
                    "avg_score": float(row[4]) if row[4] is not None else None
                } for row in cursor.fetchall()
            ]

    def get_hours_summary(self, month=None):
        # Optional month filter for hours table
        query = """
            SELECT 
                u.id AS operator_id,
                u.name AS operator_name,
                u.direction,
                wh.regular_hours,
                wh.training_hours
            FROM users u
            LEFT JOIN work_hours wh ON u.id = wh.operator_id
            WHERE u.role = 'operator'
        """
        params = []
        if month:
            query += " AND wh.month = %s"
            params.append(month)
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return [
                {
                    "operator_id": row[0],
                    "operator_name": row[1],
                    "direction": row[2],
                    "regular_hours": float(row[3]) if row[3] is not None else 0,
                    "training_hours": float(row[4]) if row[4] is not None else 0
                } for row in cursor.fetchall()
            ]

    def get_supervisors(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, table_url FROM users WHERE role = 'sv'
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
        # For admin access to all operator data
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, direction, hire_date, supervisor_id, table_url
                FROM users 
                WHERE role = 'operator'
            """)
            return cursor.fetchall()

# Initialize database
db = Database()