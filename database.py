import os
import logging
import psycopg2
from contextlib import contextmanager
from passlib.hash import pbkdf2_sha256

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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE,
                    name VARCHAR(255) NOT NULL,
                    role VARCHAR(20) NOT NULL CHECK(role IN ('admin', 'sv', 'operator')),
                    login VARCHAR(50) UNIQUE,
                    password_hash VARCHAR(255),
                    supervisor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    table_url TEXT
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS calls (
                    id SERIAL PRIMARY KEY,
                    evaluator_id INTEGER NOT NULL REFERENCES users(id),
                    operator_id INTEGER NOT NULL REFERENCES users(id),
                    month VARCHAR(20) NOT NULL,
                    call_number INTEGER NOT NULL,
                    phone_number VARCHAR(20) NOT NULL,
                    score FLOAT NOT NULL,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_calls_month ON calls(month);
            """)
            # Add index for operator_id to optimize queries for call evaluations
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_calls_operator_id ON calls(operator_id);
            """)

    def create_user(self, telegram_id, name, role, supervisor_id=None, login=None, password="123321123"):
        if login is None:
            login = str(telegram_id)
        password_hash = pbkdf2_sha256.hash(password) if password else None
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO users (telegram_id, name, role, supervisor_id, login, password_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (telegram_id, name, role, supervisor_id, login, password_hash))
            return cursor.fetchone()[0]

    def get_user(self, **kwargs):
        conditions = []
        params = []
        for key, value in kwargs.items():
            conditions.append(f"{key} = %s")
            params.append(value)
        
        query = f"SELECT * FROM users WHERE {' AND '.join(conditions)}"
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()

    def update_user_table(self, user_id, table_url):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET table_url = %s WHERE id = %s
            """, (table_url, user_id))

    def add_call_evaluation(self, evaluator_id, operator_id, month, call_number, phone_number, score, comment):
        with self._get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO calls (evaluator_id, operator_id, month, call_number, phone_number, score, comment)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (evaluator_id, operator_id, month, call_number, phone_number, score, comment))

    def get_operators_by_supervisor(self, supervisor_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, table_url FROM users 
                WHERE supervisor_id = %s AND role = 'operator'
            """, (supervisor_id,))
            return cursor.fetchall()

    def get_calls_summary(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT 
                    u.id AS operator_id,
                    u.name AS operator_name,
                    COUNT(c.id) AS call_count,
                    AVG(c.score) AS avg_score
                FROM users u
                LEFT JOIN calls c ON u.id = c.operator_id
                WHERE u.role = 'operator'
                GROUP BY u.id, u.name
            """)
            return cursor.fetchall()

    def get_supervisors(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, table_url FROM users WHERE role = 'sv'
            """)
            return cursor.fetchall()

    def get_user_by_login(self, login):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM users WHERE login = %s
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

    def get_call_evaluations(self, operator_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT call_number, month, phone_number, score, comment
                FROM calls
                WHERE operator_id = %s
                ORDER BY created_at DESC, call_number
            """, (operator_id,))
            return [
                {
                    "call_number": row[0],
                    "month": row[1],
                    "phone_number": row[2],
                    "score": float(row[3]),
                    "comment": row[4]
                } for row in cursor.fetchall()
            ]

# Инициализация базы данных при импорте
db = Database()
