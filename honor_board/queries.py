# -*- coding: utf-8 -*-
"""SQL «Доски почёта». Курсор приходит снаружи, транзакцией управляет роут.

Здесь нет ни Flask, ни подписи ссылок: только строки базы.
"""

import json

from . import catalog

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Кто правил и кто опубликовал — именами тем же запросом: окно тренера пишет
# «Опубликовал Иванов А. 3 нояб.», а вторым походом за именем ходить незачем.
_BOARD_SELECT = """
    SELECT b.id, b.period_month, b.department_key, b.draft_entries, b.revision,
           b.updated_at, upd.name AS updated_by_name,
           b.published_entries, b.published_at, pub.name AS published_by_name
      FROM honor_boards b
      LEFT JOIN users upd ON upd.id = b.updated_by
      LEFT JOIN users pub ON pub.id = b.published_by
     WHERE b.period_month = %s AND b.department_key = %s
"""


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


def previous_custom_titles(cursor, period, department_key):
    """Свои номинации направления из последней доски до этого месяца — ими
    окно заполняет новую доску, чтобы тренер не набирал их заново.

    Из черновика, а не из опубликованного: снимок хранит только номинации с
    победителем, а черновик — все, в том числе ту, где в прошлый раз никого не
    выбрали."""
    cursor.execute("""
        SELECT draft_entries AS entries
          FROM honor_boards
         WHERE department_key = %s AND period_month < %s
         ORDER BY period_month DESC
         LIMIT 1
    """, (department_key, period))
    row = cursor.fetchone()
    entries = row[0] if row and isinstance(row[0], list) else []
    return [
        str(entry.get('title') or '')
        for entry in entries
        if isinstance(entry, dict) and entry.get('nomination') == catalog.CUSTOM_NOMINATION
    ]


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
    cursor.execute(f"""
        UPDATE honor_boards
           SET published_entries = %s::jsonb, published_by = %s, published_at = {_NOW}
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
    ids = sorted({int(value) for value in user_ids if value})
    if not ids:
        return set()
    cursor.execute("SELECT id FROM users WHERE id = ANY(%s)", (ids,))
    return {row[0] for row in cursor.fetchall()}


def user_avatars(cursor, user_ids):
    """[(id, бакет, путь миниатюры)] тех, у кого в профиле есть фото."""
    ids = sorted({int(value) for value in user_ids if value})
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
    ids = sorted({int(value) for value in user_ids if value})
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
