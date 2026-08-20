"""SQL раздела «Провайдер ЭДО».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
транзакцией, ни соединением — как в crm/queries.py и oktell_guard/queries.py.

Тело файла (BYTEA) читается ТОЛЬКО job_file(): список выгрузок и карточка его не
трогают, иначе каждый опрос прогресса тянул бы мегабайты.
"""

import json

import psycopg2
from psycopg2.extras import Json

# Колонки карточки, которые уходят в интерфейс. Тела файлов и куки здесь нет и
# быть не должно.
JOB_COLUMNS = (
    'id', 'created_by', 'created_by_name', 'created_at', 'started_at', 'finished_at',
    'status', 'source_name', 'source_size', 'rows_total', 'rows_resolved', 'rows_failed',
    'requests_count', 'progress_percent', 'progress_note', 'duration_ms', 'error',
    'error_code', 'stats', 'file_name', 'file_size',
)


def _columns(cursor):
    return [column[0] for column in (cursor.description or [])]


def _row_to_dict(cursor, row):
    if row is None:
        return None
    return dict(zip(_columns(cursor), row))


# ── Задания ──────────────────────────────────────────────────────────────────

def create_job(cursor, *, user_id, user_name, source_name, source_bytes):
    """Карточка «формируется» + исходник. Раздел показывает её сразу, не дожидаясь
    первого запроса в Fleet: обход занимает минуты, и человеку нужно видеть, что
    его файл принят."""
    cursor.execute(
        """
        INSERT INTO fleet_edm_jobs (created_by, created_by_name, source_name, source_size,
                                    status, started_at, progress_note)
        VALUES (%s, %s, %s, %s, 'running', NOW(), 'Файл принят, разбираем')
        RETURNING id
        """,
        (int(user_id) if user_id else None, (user_name or None),
         (source_name or None), len(source_bytes or b'')),
    )
    job_id = int(cursor.fetchone()[0])
    cursor.execute(
        """
        INSERT INTO fleet_edm_job_files (job_id, kind, file_name, content)
        VALUES (%s, 'source', %s, %s)
        ON CONFLICT (job_id, kind) DO UPDATE SET content = EXCLUDED.content,
                                                 file_name = EXCLUDED.file_name
        """,
        (job_id, (source_name or None), psycopg2.Binary(source_bytes or b'')),
    )
    return job_id


def update_progress(cursor, job_id, *, percent=None, note=None, rows_total=None,
                    rows_resolved=None, requests_count=None):
    """Прогресс живёт в базе, а не в памяти процесса: деплой посреди выгрузки не
    должен превращать «идёт 40%» в пустоту, а сам раздел люди открывают с разных
    вкладок и устройств."""
    sets, params = [], []
    if percent is not None:
        sets.append('progress_percent = %s')
        params.append(max(0, min(100, int(percent))))
    if note is not None:
        sets.append('progress_note = %s')
        params.append(str(note)[:500])
    if rows_total is not None:
        sets.append('rows_total = %s')
        params.append(int(rows_total))
    if rows_resolved is not None:
        sets.append('rows_resolved = %s')
        params.append(int(rows_resolved))
    if requests_count is not None:
        sets.append('requests_count = %s')
        params.append(int(requests_count))
    if not sets:
        return
    params.append(int(job_id))
    cursor.execute(
        "UPDATE fleet_edm_jobs SET {} WHERE id = %s".format(', '.join(sets)),
        params,
    )


def finish_job(cursor, job_id, *, file_bytes=None, file_name=None, stats=None,
               error=None, error_code=None, duration_ms=None, rows_total=None,
               rows_resolved=None, rows_failed=None, requests_count=None):
    stats = stats or {}
    cursor.execute(
        """
        UPDATE fleet_edm_jobs
           SET status = %s,
               error = %s,
               error_code = %s,
               file_name = %s,
               file_size = %s,
               rows_total = COALESCE(%s, rows_total),
               rows_resolved = COALESCE(%s, rows_resolved),
               rows_failed = COALESCE(%s, rows_failed),
               requests_count = COALESCE(%s, requests_count),
               duration_ms = %s,
               stats = %s,
               progress_percent = CASE WHEN %s THEN 100 ELSE progress_percent END,
               progress_note = NULL,
               finished_at = NOW()
         WHERE id = %s
        """,
        (
            'done' if file_bytes else 'error',
            (str(error)[:2000] if error else None),
            (str(error_code)[:64] if error_code else None),
            (file_name or None),
            (len(file_bytes) if file_bytes else None),
            (int(rows_total) if rows_total is not None else None),
            (int(rows_resolved) if rows_resolved is not None else None),
            (int(rows_failed) if rows_failed is not None else None),
            (int(requests_count) if requests_count is not None else None),
            (int(duration_ms) if duration_ms is not None else None),
            Json(stats),
            bool(file_bytes),
            int(job_id),
        ),
    )
    if file_bytes:
        cursor.execute(
            """
            INSERT INTO fleet_edm_job_files (job_id, kind, file_name, content)
            VALUES (%s, 'result', %s, %s)
            ON CONFLICT (job_id, kind) DO UPDATE SET content = EXCLUDED.content,
                                                     file_name = EXCLUDED.file_name
            """,
            (int(job_id), (file_name or None), psycopg2.Binary(file_bytes)),
        )


def list_jobs(cursor, limit=50, offset=0):
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    cursor.execute(
        """
        SELECT {columns},
               EXISTS (SELECT 1 FROM fleet_edm_job_files f
                        WHERE f.job_id = j.id AND f.kind = 'result') AS has_file
          FROM fleet_edm_jobs j
         ORDER BY created_at DESC, id DESC
         LIMIT %s OFFSET %s
        """.format(columns=', '.join('j.' + name for name in JOB_COLUMNS)),
        (limit, offset),
    )
    columns = _columns(cursor)
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_job(cursor, job_id):
    cursor.execute(
        """
        SELECT {columns},
               EXISTS (SELECT 1 FROM fleet_edm_job_files f
                        WHERE f.job_id = j.id AND f.kind = 'result') AS has_file
          FROM fleet_edm_jobs j
         WHERE j.id = %s
        """.format(columns=', '.join('j.' + name for name in JOB_COLUMNS)),
        (int(job_id),),
    )
    return _row_to_dict(cursor, cursor.fetchone())


def job_file(cursor, job_id, kind='result'):
    cursor.execute(
        """
        SELECT f.content, COALESCE(f.file_name, j.file_name) AS file_name
          FROM fleet_edm_job_files f
          JOIN fleet_edm_jobs j ON j.id = f.job_id
         WHERE f.job_id = %s AND f.kind = %s
        """,
        (int(job_id), kind),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {'content': bytes(row[0]), 'file_name': row[1]}


def has_running_job(cursor):
    """Идёт ли уже выгрузка. Второй параллельный обход не сломает данные, но
    вдвое ускорит темп запросов к Fleet — а его лимит мы не знаем."""
    cursor.execute(
        "SELECT id FROM fleet_edm_jobs WHERE status = 'running' ORDER BY id LIMIT 1"
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None


def fail_stale_jobs(cursor, minutes=180):
    """Задания, пережившие рестарт процесса, закрываем ошибкой.

    Поток выгрузки живёт в памяти инстанса: деплой или падение оставляют карточку
    вечно «формируется», и раздел до конца времён показывает прогресс, за которым
    никого нет. Вызывается при входе в раздел — там же, где читается список.
    """
    cursor.execute(
        """
        UPDATE fleet_edm_jobs
           SET status = 'error',
               error = 'Выгрузка прервана: приложение перезапустилось',
               error_code = 'interrupted',
               finished_at = NOW()
         WHERE status = 'running'
           AND started_at < NOW() - make_interval(mins => %s)
        RETURNING id
        """,
        (int(minutes),),
    )
    return [int(row[0]) for row in cursor.fetchall()]


def cleanup(cursor, files_days=60):
    """Тела файлов старше срока удаляем, карточки оставляем: история выгрузок —
    это две сотни коротких строк, а файлы — десятки мегабайт каждый."""
    cursor.execute(
        """
        DELETE FROM fleet_edm_job_files
         WHERE job_id IN (SELECT id FROM fleet_edm_jobs
                           WHERE created_at < NOW() - make_interval(days => %s))
        """,
        (int(files_days),),
    )
    return cursor.rowcount


# ── Сессия кабинета ──────────────────────────────────────────────────────────

def save_session(cursor, *, cookies, user_agent=None, account=None, parks_count=None,
                 updated_by=None):
    """cookies — список словарей playwright/браузера либо {name: value}."""
    payload = json.dumps(cookies, ensure_ascii=False)
    cursor.execute(
        """
        INSERT INTO fleet_edm_session (id, cookies, user_agent, account, parks_count,
                                       updated_at, updated_by, last_ok_at, last_error)
        VALUES (1, %s, %s, %s, %s, NOW(), %s, NOW(), NULL)
        ON CONFLICT (id) DO UPDATE
            SET cookies = EXCLUDED.cookies,
                user_agent = EXCLUDED.user_agent,
                account = EXCLUDED.account,
                parks_count = EXCLUDED.parks_count,
                updated_at = NOW(),
                updated_by = EXCLUDED.updated_by,
                last_ok_at = NOW(),
                last_error = NULL
        """,
        (payload, (user_agent or None), (account or None),
         (int(parks_count) if parks_count else None),
         (int(updated_by) if updated_by else None)),
    )


def load_session(cursor):
    """Полная строка ВМЕСТЕ с куками — только для клиента Fleet."""
    cursor.execute(
        """
        SELECT cookies, user_agent, account, parks_count, updated_at, last_ok_at, last_error
          FROM fleet_edm_session WHERE id = 1
        """
    )
    row = _row_to_dict(cursor, cursor.fetchone())
    if not row:
        return None
    try:
        row['cookies'] = json.loads(row['cookies'] or '[]')
    except (TypeError, ValueError):
        row['cookies'] = []
    return row


def session_status(cursor):
    """То же самое БЕЗ кук — это уходит в интерфейс."""
    row = load_session(cursor)
    if not row:
        return {'configured': False}
    row.pop('cookies', None)
    row['configured'] = True
    return row


def mark_session_ok(cursor):
    cursor.execute(
        "UPDATE fleet_edm_session SET last_ok_at = NOW(), last_error = NULL WHERE id = 1"
    )


def mark_session_error(cursor, message):
    cursor.execute(
        "UPDATE fleet_edm_session SET last_error = %s WHERE id = 1",
        (str(message)[:500],),
    )


# ── Кто пришёл ───────────────────────────────────────────────────────────────

def access_context(cursor, user_id):
    """Роль, отдел и возглавляет ли человек отдел.

    Отдельным запросом, а не разбором кортежа из _resolve_requester: там
    пользователь приходит СТРОКОЙ базы, обращение к ней по имени поля молча даёт
    None, а порядок столбцов меняется вместе с чужими правками. На этом уже
    обжигался «Ограничитель Перезвона» — раздел закрывался даже суперадмину.
    """
    if not user_id:
        return None
    cursor.execute(
        """
        SELECT u.id,
               u.name,
               u.role,
               COALESCE(d.code, '')  AS department_code,
               EXISTS (
                   SELECT 1 FROM departments h
                    WHERE h.head_user_id = u.id AND h.is_active
               )                     AS is_department_head,
               COALESCE((
                   SELECT h.code FROM departments h
                    WHERE h.head_user_id = u.id AND h.is_active
                    LIMIT 1
               ), '')                AS headed_department_code
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.id = %(user_id)s
        """,
        {'user_id': int(user_id)},
    )
    return _row_to_dict(cursor, cursor.fetchone())
