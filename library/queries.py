# -*- coding: utf-8 -*-
"""SQL «Библиотеки». Курсор приходит снаружи, транзакцией управляет роут.

Здесь нет ни Flask, ни бакета: только строки базы и правила прогресса.
"""

import json

from .epub import CHARS_PER_PAGE

# Статусы чтения — ровно три, по ТЗ: «Не начато», «В процессе», «Закончено».
STATUS_NOT_STARTED = 'not_started'
STATUS_IN_PROGRESS = 'in_progress'
STATUS_FINISHED = 'finished'

# Потолок строк мониторинга за один запрос. Фильтры (сотрудник, книга, отдел)
# сужают выборку на сервере; без фильтров сотни строк — уже повод их включить.
ANALYTICS_LIMIT = 1000

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_BOOK_COLUMNS = """
    b.id, b.title, b.author, b.language, b.bucket, b.cover_blob, b.cover_type,
    b.total_pages, b.created_at
"""


def _rows(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _one(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip([column[0] for column in cursor.description], row))


def status_of(progress_exists, finished_at):
    if finished_at:
        return STATUS_FINISHED
    return STATUS_IN_PROGRESS if progress_exists else STATUS_NOT_STARTED


def _iso(value):
    return value.isoformat(timespec='seconds') if value else None


def book_view(row, cover_url=None):
    """Строка базы -> то, что уходит фронту. Путей бакета наружу нет."""
    has_progress = row.get('progress_updated_at') is not None
    finished_at = row.get('finished_at')
    percent = float(row.get('percent') or 0)
    return {
        'id': row['id'],
        'title': row['title'],
        'author': row.get('author') or '',
        'language': row.get('language') or '',
        'cover_url': cover_url,
        'total_pages': int(row.get('total_pages') or 1),
        'created_at': _iso(row.get('created_at')),
        'saved': bool(row.get('saved')),
        'progress': {
            'status': status_of(has_progress, finished_at),
            # Закончена — значит 100 %, даже если потом открыли начало:
            # «Закончено 5 %» на карточке читалось бы как противоречие.
            'percent': 100.0 if finished_at else round(percent, 1),
            'page': int(row.get('page') or 1),
            'updated_at': _iso(row.get('progress_updated_at')),
            'finished_at': _iso(finished_at),
        },
    }


def list_books(cursor, user_id):
    """Весь каталог с закладкой и прогрессом смотрящего — одним запросом.

    Вкладки «Общий доступ» и «Сохранённые» — это один и тот же список с
    разным фильтром, поэтому фронт получает его один раз и делит сам.
    """
    cursor.execute(f"""
        SELECT {_BOOK_COLUMNS},
               (s.user_id IS NOT NULL) AS saved,
               p.percent, p.page, p.finished_at, p.updated_at AS progress_updated_at
          FROM library_books b
          LEFT JOIN library_saved s ON s.book_id = b.id AND s.user_id = %s
          LEFT JOIN library_progress p ON p.book_id = b.id AND p.user_id = %s
         ORDER BY b.created_at DESC, b.id DESC
    """, (user_id, user_id))
    return _rows(cursor)


def get_book(cursor, book_id, user_id):
    """Книга для ридера: оглавление, разметка страниц и место, где остановились."""
    cursor.execute(f"""
        SELECT {_BOOK_COLUMNS}, b.file_blob, b.file_size, b.spine, b.toc, b.total_chars,
               (s.user_id IS NOT NULL) AS saved,
               p.position, p.percent, p.page, p.finished_at,
               p.updated_at AS progress_updated_at
          FROM library_books b
          LEFT JOIN library_saved s ON s.book_id = b.id AND s.user_id = %s
          LEFT JOIN library_progress p ON p.book_id = b.id AND p.user_id = %s
         WHERE b.id = %s
    """, (user_id, user_id, book_id))
    return _one(cursor)


def reader_view(row, cover_url=None):
    view = book_view(row, cover_url)
    spine = row.get('spine') or []
    if isinstance(spine, str):
        spine = json.loads(spine)
    toc = row.get('toc') or []
    if isinstance(toc, str):
        toc = json.loads(toc)
    view.update({
        'chars_per_page': CHARS_PER_PAGE,
        'total_chars': int(row.get('total_chars') or 0),
        'file_size': int(row.get('file_size') or 0),
        # Для ридера — только то, что нужно для счёта: начало и объём главы.
        'spine': [{'start': int(item.get('start') or 0), 'chars': int(item.get('chars') or 0)}
                  for item in spine],
        'toc': toc,
    })
    view['progress']['position'] = row.get('position') or ''
    return view


def book_file_ref(cursor, book_id):
    cursor.execute("SELECT bucket, file_blob, original_name FROM library_books WHERE id = %s", (book_id,))
    return _one(cursor)


def insert_book(cursor, *, parsed, bucket, file_blob, file_size, original_name,
                cover_blob, cover_type, uploaded_by):
    cursor.execute(f"""
        INSERT INTO library_books (
            title, author, language, bucket, file_blob, file_size, original_name,
            cover_blob, cover_type, spine, toc, total_chars, total_pages, uploaded_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
        RETURNING id
    """, (
        parsed['title'], parsed['author'] or '', parsed['language'] or '', bucket, file_blob,
        int(file_size), str(original_name or '')[:255], cover_blob, cover_type,
        json.dumps(parsed['spine'], ensure_ascii=False),
        json.dumps(parsed['toc'], ensure_ascii=False),
        int(parsed['total_chars']), int(parsed['total_pages']), uploaded_by,
    ))
    return cursor.fetchone()[0]


def delete_book(cursor, book_id):
    """Удаляет книгу (закладки и прогресс уходят каскадом). -> блобы или None."""
    cursor.execute("""
        DELETE FROM library_books WHERE id = %s
        RETURNING bucket, file_blob, cover_blob
    """, (book_id,))
    row = cursor.fetchone()
    if not row:
        return None
    bucket, file_blob, cover_blob = row
    return [(bucket, file_blob), (bucket, cover_blob)]


def set_saved(cursor, user_id, book_id, saved):
    """Добавить в «Сохранённые» или убрать. -> False, если книги нет."""
    cursor.execute("SELECT 1 FROM library_books WHERE id = %s", (book_id,))
    if not cursor.fetchone():
        return False
    if saved:
        cursor.execute("""
            INSERT INTO library_saved (user_id, book_id) VALUES (%s, %s)
            ON CONFLICT (user_id, book_id) DO NOTHING
        """, (user_id, book_id))
    else:
        cursor.execute("DELETE FROM library_saved WHERE user_id = %s AND book_id = %s",
                       (user_id, book_id))
    return True


def book_pages(cursor, book_id):
    cursor.execute("SELECT total_pages FROM library_books WHERE id = %s", (book_id,))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def save_progress(cursor, user_id, book_id, *, position, percent, page, at_end):
    """Запоминает место и прогресс. -> строка прогресса.

    Дошёл до последней страницы — книга «Закончена» (ТЗ 4.3), и отметка
    остаётся: finished_at однажды поставлен и не снимается, даже если потом
    вернуться к началу. Процент же — ТЕКУЩЕЕ место, как в ТЗ («текущий
    прогресс»): вернувшийся к первой главе видит, где он сейчас.
    """
    cursor.execute(f"""
        INSERT INTO library_progress (user_id, book_id, position, percent, page, finished_at)
        VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN {_NOW} END)
        ON CONFLICT (user_id, book_id) DO UPDATE SET
            position = EXCLUDED.position,
            percent = EXCLUDED.percent,
            page = EXCLUDED.page,
            updated_at = {_NOW},
            finished_at = COALESCE(library_progress.finished_at, EXCLUDED.finished_at)
        RETURNING percent, page, finished_at, updated_at AS progress_updated_at
    """, (user_id, book_id, position, percent, page, bool(at_end)))
    return _one(cursor)


def analytics(cursor, *, query='', book_id=None, department_id=None, limit=ANALYTICS_LIMIT):
    """Мониторинг: кто что читает. Только те, кто книгу открывал (ТЗ, раздел 5:
    статусы «В процессе» и «Закончено»)."""
    where = []
    params = []
    if query:
        where.append("(u.name ILIKE %s OR u.login ILIKE %s)")
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        params.extend([pattern, pattern])
    if book_id:
        where.append("p.book_id = %s")
        params.append(book_id)
    if department_id == 'none':
        where.append("u.department_id IS NULL")
    elif department_id:
        where.append("u.department_id = %s")
        params.append(department_id)
    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    cursor.execute(f"""
        SELECT p.user_id, u.name AS user_name, u.login, u.status AS employment_status,
               d.id AS department_id, d.name AS department_name,
               b.id AS book_id, b.title AS book_title, b.author AS book_author, b.total_pages,
               p.percent, p.page, p.started_at, p.finished_at, p.updated_at
          FROM library_progress p
          JOIN users u ON u.id = p.user_id
          JOIN library_books b ON b.id = p.book_id
          LEFT JOIN departments d ON d.id = u.department_id
          {clause}
         ORDER BY p.updated_at DESC, p.user_id, p.book_id
         LIMIT %s
    """, (*params, int(limit) + 1))
    rows = _rows(cursor)
    truncated = len(rows) > limit
    return [{
        'user_id': row['user_id'],
        'user_name': row['user_name'],
        'login': row['login'] or '',
        'fired': row['employment_status'] == 'fired',
        'department_id': row['department_id'],
        'department_name': row['department_name'] or '',
        'book_id': row['book_id'],
        'book_title': row['book_title'],
        'book_author': row['book_author'] or '',
        'total_pages': int(row['total_pages'] or 1),
        'percent': 100.0 if row['finished_at'] else round(float(row['percent'] or 0), 1),
        'page': int(row['page'] or 1),
        'status': status_of(True, row['finished_at']),
        # Когда читал: открыл впервые, закончил, открывал последний раз.
        'started_at': _iso(row['started_at']),
        'finished_at': _iso(row['finished_at']),
        'updated_at': _iso(row['updated_at']),
    } for row in rows[:limit]], truncated


def analytics_filters(cursor):
    """Списки для фильтров мониторинга: книги и отделы, где кто-то читает."""
    cursor.execute("SELECT id, title FROM library_books ORDER BY title, id")
    books = [{'id': row[0], 'title': row[1]} for row in cursor.fetchall()]
    cursor.execute("""
        SELECT DISTINCT d.id, d.name
          FROM library_progress p
          JOIN users u ON u.id = p.user_id
          JOIN departments d ON d.id = u.department_id
         ORDER BY d.name
    """)
    departments = [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]
    return books, departments
