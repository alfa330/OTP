# -*- coding: utf-8 -*-
"""SQL «Доски почёта». Курсор приходит снаружи, транзакцией управляет роут.

Здесь нет ни Flask, ни подписи ссылок: только строки базы.
"""

import json

from . import catalog

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Сколько дней колокол помнит вышедшую доску. Дольше уведомление «вышли итоги
# месяца» уже не новость: человек, который не заходил в портал два месяца, не
# должен получить пачку прошлогодних досок.
NOTIFY_DAYS = 45

# Кто правил и кто опубликовал — именами тем же запросом: окно тренера пишет
# «Опубликовал Иванов А. 3 нояб.», а вторым походом за именем ходить незачем.
_BOARD_SELECT = """
    SELECT b.id, b.period_month, b.department_key, b.draft_entries, b.revision,
           b.updated_at, upd.name AS updated_by_name,
           b.published_entries, b.published_at, pub.name AS published_by_name,
           b.first_published_at
      FROM honor_boards b
      LEFT JOIN users upd ON upd.id = b.updated_by
      LEFT JOIN users pub ON pub.id = b.published_by
     WHERE b.period_month = %s AND b.department_key = %s
"""

_PHOTO_COLUMNS = "p.id, p.user_id, p.bucket, p.blob_path, p.width, p.height, p.cutout"


def _rows(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _one(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip([column[0] for column in cursor.description], row))


def _dump(entries):
    return json.dumps(entries, ensure_ascii=False)


def _ids(values):
    return sorted({int(value) for value in values if value})


def board(cursor, period, department_key, *, lock=False):
    """Доска месяца и направления или None. lock — строка под FOR UPDATE:
    сохранение и публикация сверяют revision и пишут в одной транзакции."""
    cursor.execute(_BOARD_SELECT + (" FOR UPDATE OF b" if lock else ""), (period, department_key))
    return _one(cursor)


def published_periods(cursor):
    """Месяцы, за которые опубликована хоть одна доска, свежие первыми."""
    cursor.execute("""
        SELECT DISTINCT period_month
          FROM honor_boards
         WHERE published_at IS NOT NULL
         ORDER BY period_month DESC
    """)
    return [row[0] for row in cursor.fetchall()]


def published_boards(cursor, period):
    cursor.execute("""
        SELECT department_key, published_entries, published_at
          FROM honor_boards
         WHERE period_month = %s AND published_at IS NOT NULL
    """, (period,))
    return _rows(cursor)


def boards_between(cursor, first, last):
    """Доски месяцев окна тренера — для отметок «опубликовано» и «черновик»."""
    cursor.execute("""
        SELECT period_month, department_key, draft_entries, published_entries, published_at
          FROM honor_boards
         WHERE period_month BETWEEN %s AND %s
    """, (first, last))
    return _rows(cursor)


def previous_custom_nominations(cursor, period, department_key):
    """Свои номинации направления из последней доски до этого месяца:
    [(название, значок)] — ими окно заполняет новую доску, чтобы тренер не
    набирал их заново.

    Из черновика, а не из опубликованного: снимок хранит только номинации с
    победителем, а черновик — все, в том числе ту, где в прошлый раз никого не
    выбрали. Доска до 06.11.2026 хранит номинации ОП и СЗоВ кодами — их
    переводит restore_slots."""
    cursor.execute("""
        SELECT draft_entries AS entries
          FROM honor_boards
         WHERE department_key = %s AND period_month < %s
         ORDER BY period_month DESC
         LIMIT 1
    """, (department_key, period))
    row = cursor.fetchone()
    if not row or not isinstance(row[0], list):
        return []
    return catalog.custom_nominations(catalog.restore_slots(department_key, row[0]))


def insert_board(cursor, period, department_key, entries, actor_id):
    """Первая запись доски. None — её только что завела другая вкладка."""
    cursor.execute("""
        INSERT INTO honor_boards (period_month, department_key, draft_entries, updated_by)
        VALUES (%s, %s, %s::jsonb, %s)
        ON CONFLICT (period_month, department_key) DO NOTHING
        RETURNING id
    """, (period, department_key, _dump(entries), actor_id))
    row = cursor.fetchone()
    return row[0] if row else None


def update_draft(cursor, board_id, entries, actor_id):
    cursor.execute(f"""
        UPDATE honor_boards
           SET draft_entries = %s::jsonb, revision = revision + 1,
               updated_by = %s, updated_at = {_NOW}
         WHERE id = %s
    """, (_dump(entries), actor_id, board_id))


def publish(cursor, board_id, snapshot, actor_id):
    """Выложить снимок. first_published_at ставится один раз — по нему колокол
    сообщает о вышедшей доске; правки и повторная публикация его не трогают."""
    cursor.execute(f"""
        UPDATE honor_boards
           SET published_entries = %s::jsonb, published_by = %s, published_at = {_NOW},
               first_published_at = COALESCE(first_published_at, {_NOW})
         WHERE id = %s
    """, (_dump(snapshot), actor_id, board_id))


def unpublish(cursor, board_id):
    """Снять с публикации; черновик остаётся — его можно поправить и выложить снова."""
    cursor.execute("""
        UPDATE honor_boards
           SET published_entries = NULL, published_by = NULL, published_at = NULL
         WHERE id = %s
    """, (board_id,))


def existing_user_ids(cursor, user_ids):
    ids = _ids(user_ids)
    if not ids:
        return set()
    cursor.execute("SELECT id FROM users WHERE id = ANY(%s)", (ids,))
    return {row[0] for row in cursor.fetchall()}


def user_avatars(cursor, user_ids):
    """[(id, бакет, путь миниатюры)] тех, у кого в профиле есть фото."""
    ids = _ids(user_ids)
    if not ids:
        return []
    cursor.execute("""
        SELECT id, avatar_bucket, avatar_blob_path
          FROM users
         WHERE id = ANY(%s) AND avatar_blob_path IS NOT NULL AND avatar_blob_path <> ''
    """, (ids,))
    return [tuple(row) for row in cursor.fetchall()]


def people_cards(cursor, user_ids):
    """Карточки выбранных в черновике людей для окна тренера: ФИО из профиля,
    направление, роль и фото — то, что окно показывает над полем «Имя и фамилия»."""
    ids = _ids(user_ids)
    if not ids:
        return []
    cursor.execute("""
        SELECT u.id, u.name, u.role, u.avatar_bucket, u.avatar_blob_path,
               d.name AS direction_name
          FROM users u
          LEFT JOIN directions d ON d.id = u.direction_id
         WHERE u.id = ANY(%s)
    """, (ids,))
    return _rows(cursor)


def department_by_code(cursor, code):
    cursor.execute("""
        SELECT id, name
          FROM departments
         WHERE LOWER(code) = %s AND COALESCE(is_active, TRUE) = TRUE
         ORDER BY id
         LIMIT 1
    """, (str(code or '').strip().lower(),))
    return _one(cursor)


def candidates(cursor, department_id):
    """Кого можно наградить: сотрудники отдела, кроме уволенных. Отпуск,
    больничный и увольнение в процессе — не повод: итоги подводят за месяц,
    который человек отработал."""
    cursor.execute("""
        SELECT u.id, u.name, u.role, u.avatar_bucket, u.avatar_blob_path,
               d.name AS direction_name
          FROM users u
          LEFT JOIN directions d ON d.id = u.direction_id
         WHERE u.department_id = %s AND COALESCE(u.status, 'working') <> 'fired'
         ORDER BY LOWER(u.name), u.id
    """, (department_id,))
    return _rows(cursor)


# ── Фото для доски ──────────────────────────────────────────────────────

def user_department_code(cursor, user_id):
    """Код отдела сотрудника (как в departments.code) или None — так ручка
    загрузки фото узнаёт, ведёт ли тренер его направление. Уволенный — None:
    его в победители не предлагают."""
    cursor.execute("""
        SELECT LOWER(d.code)
          FROM users u
          JOIN departments d ON d.id = u.department_id
         WHERE u.id = %s AND COALESCE(u.status, 'working') <> 'fired'
    """, (int(user_id),))
    row = cursor.fetchone()
    return row[0] if row else None


def insert_photo(cursor, *, user_id, bucket, blob_path, content_type, width, height, file_size,
                 cutout, actor_id):
    cursor.execute("""
        INSERT INTO honor_board_photos
            (user_id, bucket, blob_path, content_type, width, height, file_size, cutout, uploaded_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (int(user_id), bucket, blob_path, content_type, int(width), int(height), int(file_size),
          bool(cutout), actor_id))
    return cursor.fetchone()[0]


def photos_by_ids(cursor, photo_ids):
    """{id: строка фото} — фото, на которые ссылаются записи доски."""
    ids = _ids(photo_ids)
    if not ids:
        return {}
    cursor.execute(f"SELECT {_PHOTO_COLUMNS} FROM honor_board_photos p WHERE p.id = ANY(%s)", (ids,))
    return {row['id']: row for row in _rows(cursor)}


def latest_photos(cursor, user_ids):
    """{user_id: строка последнего фото} — его окно подставляет победителю,
    у которого фото для доски уже загружали.

    Только фото, которое стоит в записи хоть одной доски (черновика или
    опубликованной): снимок, который тренер загрузил и тут же убрал («Убрать»,
    неудачно вырезанный фон, чужое фото), сам больше не подставляется."""
    ids = _ids(user_ids)
    if not ids:
        return {}
    cursor.execute(f"""
        SELECT DISTINCT ON (p.user_id) {_PHOTO_COLUMNS}
          FROM honor_board_photos p
         WHERE p.user_id = ANY(%s)
           AND EXISTS (
                SELECT 1 FROM honor_boards b
                 WHERE b.draft_entries @> jsonb_build_array(jsonb_build_object('photo_id', p.id))
                    OR b.published_entries @> jsonb_build_array(jsonb_build_object('photo_id', p.id))
           )
         ORDER BY p.user_id, p.id DESC
    """, (ids,))
    return {row['user_id']: row for row in _rows(cursor)}


def user_on_boards(cursor, user_id, department_keys):
    """Стоит ли человек в записи доски этих направлений (черновик или
    опубликованная) — победитель прошлого месяца, который с тех пор перешёл в
    другой отдел или уволился: фото ему всё равно можно загрузить."""
    keys = [str(key) for key in department_keys or ()]
    if not keys:
        return False
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM honor_boards b
             WHERE b.department_key = ANY(%(keys)s)
               AND (b.draft_entries @> jsonb_build_array(jsonb_build_object('user_id', %(user_id)s))
                    OR b.published_entries @> jsonb_build_array(jsonb_build_object('user_id', %(user_id)s)))
        )
    """, {'keys': keys, 'user_id': int(user_id)})
    row = cursor.fetchone()
    return bool(row and row[0])


# ── Колокол: вышедшие доски ─────────────────────────────────────────────

# COALESCE с published_at — на время выкладки: доску, выложенную старым
# экземпляром API (без колонки first_published_at), колокол видит и до
# бэкфилла при следующем старте.
_UNSEEN_SQL = f"""
    SELECT b.period_month, b.department_key,
           COALESCE(b.first_published_at, b.published_at) AS first_published_at
      FROM honor_boards b
     WHERE b.published_at IS NOT NULL
       AND COALESCE(b.first_published_at, b.published_at) > {_NOW} - INTERVAL '{NOTIFY_DAYS} days'
       AND COALESCE(b.first_published_at, b.published_at) > COALESCE(
               (SELECT r.last_seen_at FROM honor_board_reads r WHERE r.user_id = %(user_id)s),
               '-infinity'::timestamp)
"""


def unseen_periods(cursor, user_id, limit):
    """(сколько месяцев, [(месяц, [ключи направлений], когда вышла последняя)])
    — доски, вышедшие после того, как человек последний раз смотрел доску
    почёта: строка колокола на месяц, свежие первыми."""
    cursor.execute(f"""
        SELECT period_month, array_agg(department_key ORDER BY department_key) AS departments,
               MAX(first_published_at) AS published_at, COUNT(*) OVER () AS total
          FROM ({_UNSEEN_SQL}) fresh
         GROUP BY period_month
         ORDER BY period_month DESC
         LIMIT %(limit)s
    """, {'user_id': int(user_id), 'limit': int(limit)})
    rows = cursor.fetchall()
    total = int(rows[0][3]) if rows else 0
    return total, [(row[0], list(row[1] or []), row[2]) for row in rows]


def has_unseen(cursor, user_id):
    cursor.execute(f"SELECT EXISTS ({_UNSEEN_SQL})", {'user_id': int(user_id)})
    row = cursor.fetchone()
    return bool(row and row[0])


def mark_seen(cursor, user_id):
    cursor.execute(f"""
        INSERT INTO honor_board_reads (user_id, last_seen_at)
        VALUES (%s, {_NOW})
        ON CONFLICT (user_id) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at
    """, (int(user_id),))
