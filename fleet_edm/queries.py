"""SQL раздела «Провайдер ЭДО».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
транзакцией, ни соединением — как в crm/queries.py и oktell_guard/queries.py.

Тело файла (BYTEA) читается ТОЛЬКО job_file(): список выгрузок и карточка его не
трогают, иначе каждый опрос прогресса тянул бы мегабайты.
"""

import json

import psycopg2
from psycopg2.extras import Json, execute_values

# Колонки карточки, которые уходят в интерфейс. Тела файлов и куки здесь нет и
# быть не должно.
JOB_COLUMNS = (
    'id', 'created_by', 'created_by_name', 'created_at', 'started_at', 'finished_at',
    'status', 'source_name', 'source_size', 'rows_total', 'rows_resolved', 'rows_failed',
    'requests_count', 'progress_percent', 'progress_note', 'duration_ms', 'error',
    'error_code', 'stats', 'file_name', 'file_size', 'attempts',
)


def _columns(cursor):
    return [column[0] for column in (cursor.description or [])]


def _row_to_dict(cursor, row):
    if row is None:
        return None
    return dict(zip(_columns(cursor), row))


# ── Задания ──────────────────────────────────────────────────────────────────

def create_job(cursor, *, user_id, user_name, source_name, source_bytes,
               owner_instance=None):
    """Карточка «формируется» + исходник. Раздел показывает её сразу, не дожидаясь
    первого запроса в Fleet: обход занимает минуты, и человеку нужно видеть, что
    его файл принят.

    owner_instance — идентификатор процесса, который взялся считать. По нему
    следующий процесс отличает «работа идёт» от «процесс умер вместе с потоком».
    """
    cursor.execute(
        """
        INSERT INTO fleet_edm_jobs (created_by, created_by_name, source_name, source_size,
                                    status, started_at, progress_note, progress_at,
                                    owner_instance)
        VALUES (%s, %s, %s, %s, 'running', NOW(), 'Файл принят, разбираем', NOW(), %s)
        RETURNING id
        """,
        (int(user_id) if user_id else None, (user_name or None),
         (source_name or None), len(source_bytes or b''), (owner_instance or None)),
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
    # Отметку времени ставим всегда, даже если менять больше нечего: по ней
    # видно, что выгрузка жива, — см. orphan_jobs и touch_job.
    sets.append('progress_at = NOW()')
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
               -- Длительность считаем от ПЕРВОГО старта, а не от начала последней
               -- попытки: выгрузку могли подхватывать после каждого деплоя, а
               -- человек ждал всё это время целиком.
               duration_ms = COALESCE(
                   %s,
                   (EXTRACT(EPOCH FROM (NOW() - COALESCE(started_at, created_at))) * 1000)::bigint
               ),
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


def orphan_jobs(cursor, instance_id, silence_seconds=90, own_silence_seconds=600,
                max_attempts=12):
    """Выгрузки, за которыми больше никого нет, — их нужно подхватить.

    Поток выгрузки живёт в памяти процесса, а процесс на Render перезапускается
    от каждого пуша в main: 21.08.2026 деплоев было 61, и обе выгрузки того дня
    (6 962 и 15 738 строк) погибли ровно так. Раньше такую карточку закрывали
    ошибкой «запустите заново» — то есть перекладывали работу робота на человека,
    который к тому же не мог знать, сколько раз ещё придётся запускать.

    Отличаем смерть от жизни ДВУМЯ признаками, а не одним:

    * чужой owner_instance + полторы минуты молчания — это точно мёртвый процесс,
      потому что живая выгрузка отмечается в базе каждые 15 секунд (heartbeat);
    * свой owner_instance + десять минут молчания — это уже наш собственный
      подвисший поток; ждём дольше, чтобы не отнять работу у живого.

    Про попытки. Каждый подхват увеличивает attempts. Двенадцать — это заведомо
    больше, чем деплоев успевает случиться за одну выгрузку (медиана промежутка
    между деплоями 10 минут, самая долгая выгрузка укладывается в четверть часа);
    если попыток стало больше, дело не в деплоях, и карточку честно закрываем.
    """
    cursor.execute(
        """
        UPDATE fleet_edm_jobs
           SET status = 'error',
               error = 'Выгрузку прерывали слишком часто — она не смогла дойти до конца.',
               error_code = 'too_many_restarts',
               finished_at = NOW()
         WHERE status = 'running'
           AND attempts >= %s
           AND COALESCE(progress_at, started_at, created_at)
               < NOW() - make_interval(secs => %s)
        RETURNING id
        """,
        (int(max_attempts), int(silence_seconds)),
    )
    exhausted = [int(row[0]) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT id
          FROM fleet_edm_jobs
         WHERE status = 'running'
           AND attempts < %(max_attempts)s
           AND (
                 (owner_instance IS DISTINCT FROM %(instance)s
                  AND COALESCE(progress_at, started_at, created_at)
                      < NOW() - make_interval(secs => %(silence)s))
                 OR
                 (owner_instance = %(instance)s
                  AND COALESCE(progress_at, started_at, created_at)
                      < NOW() - make_interval(secs => %(own_silence)s))
               )
         ORDER BY id
        """,
        {'instance': instance_id, 'silence': int(silence_seconds),
         'own_silence': int(own_silence_seconds), 'max_attempts': int(max_attempts)},
    )
    return {'resume': [int(row[0]) for row in cursor.fetchall()], 'exhausted': exhausted}


def claim_job(cursor, job_id, instance_id, note=None):
    """Забрать выгрузку себе. Возвращает номер попытки либо None, если её уже
    забрал кто-то другой (на одном инстансе такого не бывает, но условие в
    UPDATE стоит дешевле, чем разбор гонки, если инстансов станет два)."""
    cursor.execute(
        """
        UPDATE fleet_edm_jobs
           SET owner_instance = %s,
               attempts = attempts + 1,
               progress_at = NOW(),
               progress_note = COALESCE(%s, progress_note)
         WHERE id = %s
           AND status = 'running'
           AND owner_instance IS DISTINCT FROM %s
        RETURNING attempts
        """,
        (instance_id, (str(note)[:500] if note else None), int(job_id), instance_id),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None


def release_job(cursor, job_id, instance_id, note=None):
    """Отдать выгрузку обратно в общую очередь, оставив её «идущей».

    Нужно, когда обход прервался не смертью процесса, а отказом кабинета: поток
    закончился, а работа — нет. Владельца обнуляем НАМЕРЕННО: подхват берёт только
    записи с чужим владельцем, и без этого обнуления выгрузка ждала бы своего же
    процесса, который её уже бросил.
    """
    cursor.execute(
        """
        UPDATE fleet_edm_jobs
           SET owner_instance = NULL,
               progress_note = COALESCE(%s, progress_note),
               progress_at = NOW()
         WHERE id = %s AND status = 'running' AND owner_instance = %s
        RETURNING id
        """,
        ((str(note)[:500] if note else None), int(job_id), instance_id),
    )
    return bool(cursor.fetchone())


def touch_job(cursor, job_id, instance_id):
    """Сердцебиение: «я живой». Возвращает статус и владельца — по ним поток
    понимает, что карточку у него отняли (или закрыли), и останавливается сам.

    Без этого удара пульса раздел убивал СВОИ ЖЕ живые выгрузки: прогресс
    писался только на границах раундов, а добор карточками на прогоне 21.08.2026
    молчал двадцать минут — сторож справедливо счёл выгрузку мёртвой, хотя она
    работала (и продолжала работать ещё двадцать минут, впустую).
    """
    cursor.execute(
        """
        UPDATE fleet_edm_jobs SET progress_at = NOW()
         WHERE id = %s AND status = 'running' AND owner_instance = %s
        RETURNING status, owner_instance, attempts
        """,
        (int(job_id), instance_id),
    )
    row = cursor.fetchone()
    if row:
        return {'status': row[0], 'owner_instance': row[1], 'attempts': int(row[2]),
                'mine': True}
    cursor.execute(
        "SELECT status, owner_instance, attempts FROM fleet_edm_jobs WHERE id = %s",
        (int(job_id),),
    )
    row = cursor.fetchone()
    if not row:
        return {'status': 'gone', 'owner_instance': None, 'attempts': 0, 'mine': False}
    return {'status': row[0], 'owner_instance': row[1], 'attempts': int(row[2]),
            'mine': False}


# ── Контрольная точка обхода ─────────────────────────────────────────────────

def save_checkpoint(cursor, job_id, *, rows=None, checkpoint=None):
    """Найденное — в базу. rows: [(contractor_id, park_id, payload|None), …].

    ON CONFLICT DO UPDATE, а не DO NOTHING: сначала прилетает «парк нашли»
    (payload NULL), позже по той же строке — сам провайдер. COALESCE у park_id
    защищает от обратного порядка: добор карточкой знает провайдера и парк, а
    строка «парк без провайдера» может прийти после него из соседнего сегмента.
    """
    if rows:
        execute_values(
            cursor,
            """
            INSERT INTO fleet_edm_job_rows (job_id, contractor_id, park_id, payload)
            VALUES %s
            ON CONFLICT (job_id, contractor_id) DO UPDATE
                SET park_id = COALESCE(EXCLUDED.park_id, fleet_edm_job_rows.park_id),
                    payload = COALESCE(EXCLUDED.payload, fleet_edm_job_rows.payload)
            """,
            [(int(job_id), str(cid), (park or None), (Json(payload) if payload else None))
             for cid, park, payload in rows],
            page_size=500,
        )
    if checkpoint is not None:
        cursor.execute(
            "UPDATE fleet_edm_jobs SET checkpoint = %s, progress_at = NOW() WHERE id = %s",
            (Json(checkpoint), int(job_id)),
        )


def load_checkpoint(cursor, job_id):
    """Что уже сделано по этой выгрузке: результаты, найденные парки и этапы."""
    cursor.execute(
        "SELECT checkpoint FROM fleet_edm_jobs WHERE id = %s", (int(job_id),))
    row = cursor.fetchone()
    stages = (row[0] if row else None) or {}
    cursor.execute(
        "SELECT contractor_id, park_id, payload FROM fleet_edm_job_rows WHERE job_id = %s",
        (int(job_id),),
    )
    results, parks = {}, {}
    for contractor_id, park_id, payload in cursor.fetchall():
        if park_id:
            parks[contractor_id] = park_id
        if payload:
            results[contractor_id] = payload
    return {'results': results, 'parks': parks, 'stages': stages}


def drop_checkpoint(cursor, job_id):
    """Выгрузка дошла до конца — контрольная точка больше не нужна. Файл с
    результатом уже лежит в fleet_edm_job_files, а держать рядом ещё и 15 тысяч
    строк с ФИО и телефонами по каждому заданию значит хранить одно и то же
    дважды (см. never-commit-personal-data: чем меньше копий, тем лучше)."""
    cursor.execute("DELETE FROM fleet_edm_job_rows WHERE job_id = %s", (int(job_id),))
    cursor.execute("UPDATE fleet_edm_jobs SET checkpoint = NULL WHERE id = %s",
                   (int(job_id),))
    return cursor.rowcount


def cleanup(cursor, files_days=60, checkpoint_days=3):
    """Тела файлов старше срока удаляем, карточки оставляем: история выгрузок —
    это две сотни коротких строк, а файлы — десятки мегабайт каждый.

    Контрольные точки живут гораздо меньше: они нужны, только пока выгрузку ещё
    можно продолжить. У завершённых заданий их сносит drop_checkpoint, здесь —
    подчистка за упавшими.
    """
    cursor.execute(
        """
        DELETE FROM fleet_edm_job_files
         WHERE job_id IN (SELECT id FROM fleet_edm_jobs
                           WHERE created_at < NOW() - make_interval(days => %s))
        """,
        (int(files_days),),
    )
    removed = cursor.rowcount
    cursor.execute(
        """
        DELETE FROM fleet_edm_job_rows
         WHERE job_id IN (SELECT id FROM fleet_edm_jobs
                           WHERE status <> 'running'
                             AND COALESCE(finished_at, created_at)
                                 < NOW() - make_interval(days => %s))
        """,
        (int(checkpoint_days),),
    )
    return removed


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
