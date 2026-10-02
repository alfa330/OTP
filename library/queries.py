# -*- coding: utf-8 -*-
"""SQL «Библиотеки». Курсор приходит снаружи, транзакцией управляет роут.

Здесь нет ни Flask, ни бакета: только строки базы и правила прогресса.
"""

import json
from decimal import ROUND_DOWN, Decimal

from .epub import CHARS_PER_PAGE

# Статусы чтения — ровно три, по ТЗ: «Не начато», «В процессе», «Закончено».
STATUS_NOT_STARTED = 'not_started'
STATUS_IN_PROGRESS = 'in_progress'
STATUS_FINISHED = 'finished'

# Потолок строк мониторинга за один запрос. Фильтры (сотрудник, книга, отдел)
# сужают выборку на сервере; без фильтров сотни строк — уже повод их включить.
ANALYTICS_LIMIT = 1000

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Каким отделам книгу можно выдать. Решение владельца 02.10.2026: «из доступных
# отделов сделать только пока СЗоВ и ОП». По коду отдела, а не по названию:
# название отдела переименовывают, код — нет. Отдел вне списка, которому
# книга уже выдана, остаётся у неё (и в выборе — чтобы его можно было снять).
LIBRARY_DEPARTMENT_CODES = ('szov', 'op')

# Отделы и жанры книги — массивами прямо в строке: книг десятки, подзапрос по
# индексу дешевле второго похода в базу и склейки в питоне.
_BOOK_COLUMNS = """
    b.id, b.title, b.author, b.language, b.bucket, b.cover_blob, b.cover_type,
    b.total_pages, b.created_at, b.archived_at,
    ARRAY(SELECT bd.department_id FROM library_book_departments bd
           WHERE bd.book_id = b.id ORDER BY bd.department_id) AS department_ids,
    ARRAY(SELECT bg.genre_id FROM library_book_genres bg
           WHERE bg.book_id = b.id ORDER BY bg.genre_id) AS genre_ids
"""

# Длина имени жанра: «Личная эффективность» — 20 знаков, сорок хватает с
# запасом, а длиннее — уже не жанр, а описание, и на чипе оно не уместится.
GENRE_NAME_MAX = 40

# Что видно читателю: книга не в архиве и выдана ЕГО отделу. Отдел берётся из
# users тем же запросом, а не отдельным походом: параметр — id смотрящего.
# Сотрудник без отдела не видит ничего — книга всегда выдана конкретным отделам.
# Управляющие (супер-админ, тренер) видят всё, включая архив, — условие к ним
# не применяется (library/routes.py решает по роли).
_READER_SEES = """
    b.archived_at IS NULL AND EXISTS (
        SELECT 1 FROM library_book_departments bd
          JOIN users viewer ON viewer.department_id = bd.department_id
         WHERE bd.book_id = b.id AND viewer.id = %s
    )
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
        'department_ids': [int(value) for value in (row.get('department_ids') or [])],
        'genre_ids': [int(value) for value in (row.get('genre_ids') or [])],
        'archived': row.get('archived_at') is not None,
        'archived_at': _iso(row.get('archived_at')),
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


def list_books(cursor, user_id, *, manager):
    """Каталог с закладкой и прогрессом смотрящего — одним запросом.

    Управляющему — все книги всех отделов вместе с архивом: отдел и вкладку
    («Общий доступ», «Сохранённые», «Архив») фронт выбирает на месте, без
    второго похода на сервер. Читателю — только книги его отдела не из архива.
    """
    where = '' if manager else f'WHERE {_READER_SEES}'
    params = (user_id, user_id) if manager else (user_id, user_id, user_id)
    cursor.execute(f"""
        SELECT {_BOOK_COLUMNS},
               (s.user_id IS NOT NULL) AS saved,
               p.percent, p.page, p.finished_at, p.updated_at AS progress_updated_at
          FROM library_books b
          LEFT JOIN library_saved s ON s.book_id = b.id AND s.user_id = %s
          LEFT JOIN library_progress p ON p.book_id = b.id AND p.user_id = %s
          {where}
         ORDER BY b.created_at DESC, b.id DESC
    """, params)
    return _rows(cursor)


def open_book(cursor, book_id, user_id, *, manager):
    """-> число страниц книги, если смотрящему её можно открыть, иначе None.

    Одна дверь для всех ручек одной книги (ридер, файл, закладка, прогресс):
    чужую по отделу или архивную книгу читатель не получит ни прямым адресом,
    ни перебором id. Ответ тот же, что у несуществующей, — 404.
    """
    if manager:
        cursor.execute("SELECT b.total_pages FROM library_books b WHERE b.id = %s", (book_id,))
    else:
        cursor.execute(f"SELECT b.total_pages FROM library_books b WHERE b.id = %s AND {_READER_SEES}",
                       (book_id, user_id))
    row = cursor.fetchone()
    return int(row[0] or 1) if row else None


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
                cover_blob, cover_type, uploaded_by, department_ids, genre_ids=()):
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
    book_id = cursor.fetchone()[0]
    set_book_departments(cursor, book_id, department_ids)
    if genre_ids:
        set_book_genres(cursor, book_id, genre_ids)
    return book_id


# Отдел, которому можно выдать книгу: действующий и из списка библиотеки.
_DEPARTMENT_SELECTABLE = "(d.is_active IS NOT FALSE AND LOWER(d.code) = ANY(%s))"


def library_departments(cursor):
    """Отделы для выбора: те, кому можно выдать книгу (active), и те, у кого
    книги уже есть (active = False, если выдать им новую уже нельзя).

    Второе — чтобы книга, выданная отделу, который потом выключили или убрали
    из списка библиотеки, не показывала в окне отделов безымянное «№ 42».
    """
    codes = list(LIBRARY_DEPARTMENT_CODES)
    cursor.execute(f"""
        SELECT d.id, d.name, {_DEPARTMENT_SELECTABLE} AS active
          FROM departments d
         WHERE {_DEPARTMENT_SELECTABLE}
            OR EXISTS (SELECT 1 FROM library_book_departments bd WHERE bd.department_id = d.id)
         ORDER BY d.name, d.id
    """, (codes, codes))
    return [{'id': row[0], 'name': row[1], 'active': bool(row[2])} for row in cursor.fetchall()]


def unknown_departments(cursor, department_ids, *, book_id=None):
    """-> id из списка, которым книгу выдать нельзя (нет такого отдела, он
    выключен или не из списка библиотеки). Отдел, которому книга УЖЕ выдана,
    можно оставить: правка отделов книги не должна требовать сначала его снять."""
    ids = sorted({int(value) for value in department_ids})
    if not ids:
        return []
    cursor.execute(f"""
        SELECT d.id FROM departments d
         WHERE d.id = ANY(%s)
           AND ({_DEPARTMENT_SELECTABLE} OR EXISTS (
                SELECT 1 FROM library_book_departments bd
                 WHERE bd.department_id = d.id AND bd.book_id = %s))
    """, (ids, list(LIBRARY_DEPARTMENT_CODES), book_id))
    known = {row[0] for row in cursor.fetchall()}
    return [value for value in ids if value not in known]


def normalize_genre_name(value):
    """Имя жанра из запроса -> чистая строка или '' (такого имени не бывает).

    Пробелы схлопываются (двойной пробел из копипаста дал бы «второй» жанр),
    непечатаемые знаки выбрасываются: NUL Postgres в TEXT не примет вовсе.
    """
    if not isinstance(value, str):
        return ''
    text = ' '.join(''.join(ch if ch.isprintable() else ' ' for ch in value).split())
    return text if len(text) <= GENRE_NAME_MAX else ''


def list_genres(cursor):
    cursor.execute("SELECT id, name FROM library_genres ORDER BY LOWER(name), id")
    return [{'id': row[0], 'name': row[1]} for row in cursor.fetchall()]


def create_genre(cursor, name, user_id):
    """-> (жанр, создан ли). Жанр с таким именем (без учёта регистра) уже
    есть — возвращается он: два тренера, создающих «Психологию» одновременно,
    получат один жанр, а не ошибку у второго.

    Одним запросом: DO UPDATE (а не DO NOTHING) возвращает и существующую
    строку, так что между «не вставилось» и «найди» не остаётся окна, в
    которое жанр успели бы удалить. Имя существующего жанра не меняется;
    xmax = 0 бывает только у только что вставленной строки.
    """
    cursor.execute("""
        INSERT INTO library_genres (name, created_by) VALUES (%s, %s)
        ON CONFLICT ((LOWER(name))) DO UPDATE SET name = library_genres.name
        RETURNING id, name, (xmax = 0) AS created
    """, (name, user_id))
    row = cursor.fetchone()
    return {'id': row[0], 'name': row[1]}, bool(row[2])


GENRE_RENAMED = 'renamed'
GENRE_NOT_FOUND = 'not_found'
GENRE_NAME_TAKEN = 'taken'


def rename_genre(cursor, genre_id, name):
    """-> (GENRE_*, жанр или None). Смена одного регистра («бизнес» ->
    «Бизнес») — переименование того же жанра, а не занятое имя."""
    cursor.execute("SELECT 1 FROM library_genres WHERE LOWER(name) = LOWER(%s) AND id <> %s",
                   (name, genre_id))
    if cursor.fetchone():
        return GENRE_NAME_TAKEN, None
    cursor.execute("UPDATE library_genres SET name = %s WHERE id = %s RETURNING id, name", (name, genre_id))
    row = cursor.fetchone()
    if not row:
        return GENRE_NOT_FOUND, None
    return GENRE_RENAMED, {'id': row[0], 'name': row[1]}


def delete_genre(cursor, genre_id):
    """Удаляет жанр; с книг он снимается каскадом, сами книги остаются.
    -> False, если жанра нет."""
    cursor.execute("DELETE FROM library_genres WHERE id = %s RETURNING id", (genre_id,))
    return cursor.fetchone() is not None


def unknown_genres(cursor, genre_ids):
    """-> id из списка, которых нет в справочнике (жанр удалили, пока окно
    книги было открыто)."""
    ids = sorted({int(value) for value in genre_ids})
    if not ids:
        return []
    cursor.execute("SELECT id FROM library_genres WHERE id = ANY(%s)", (ids,))
    known = {row[0] for row in cursor.fetchall()}
    return [value for value in ids if value not in known]


def set_book_genres(cursor, book_id, genre_ids):
    """Заменяет жанры книги на данный набор (проверенный заранее). Пустой
    набор — книга без жанра, это не ошибка.

    Вставляются только жанры, которые есть, и под FOR KEY SHARE: проверка
    набора и запись разнесены (при загрузке между ними — файл в бакет), и
    жанр, удалённый другим управляющим в эту секунду, иначе ронял бы вставку
    внешним ключом в 500. Так книга ложится без него — ровно то же, что
    сделал бы каскад, удали его секундой позже.
    """
    ids = sorted({int(value) for value in genre_ids})
    cursor.execute("DELETE FROM library_book_genres WHERE book_id = %s AND NOT (genre_id = ANY(%s))",
                   (book_id, ids))
    if ids:
        cursor.execute("""
            INSERT INTO library_book_genres (book_id, genre_id)
            SELECT %s, g.id FROM library_genres g WHERE g.id = ANY(%s)
            FOR KEY SHARE
            ON CONFLICT DO NOTHING
        """, (book_id, ids))


def set_book_departments(cursor, book_id, department_ids):
    """Заменяет отделы книги на данный набор (проверенный заранее)."""
    ids = sorted({int(value) for value in department_ids})
    cursor.execute("DELETE FROM library_book_departments WHERE book_id = %s AND NOT (department_id = ANY(%s))",
                   (book_id, ids))
    cursor.execute("""
        INSERT INTO library_book_departments (book_id, department_id)
        SELECT %s, unnest(%s::int[])
        ON CONFLICT DO NOTHING
    """, (book_id, ids))


def update_book(cursor, book_id, user_id, *, department_ids=None, genre_ids=None, archived=None):
    """Отделы, жанры и архив книги. -> False, если книги нет.

    Строка книги берётся под замок: две правки подряд (снять отдел и тут же
    убрать в архив) не должны перемешать наборы отделов.
    """
    cursor.execute("SELECT id FROM library_books WHERE id = %s FOR UPDATE", (book_id,))
    if not cursor.fetchone():
        return False
    if department_ids is not None:
        set_book_departments(cursor, book_id, department_ids)
    if genre_ids is not None:
        set_book_genres(cursor, book_id, genre_ids)
    if archived is True:
        # Повторное «в архив» не переписывает дату: книга в архиве с того
        # дня, когда её туда убрали впервые.
        cursor.execute(f"""
            UPDATE library_books
               SET archived_at = COALESCE(archived_at, {_NOW}),
                   archived_by = CASE WHEN archived_at IS NULL THEN %s ELSE archived_by END
             WHERE id = %s
        """, (user_id, book_id))
    elif archived is False:
        cursor.execute("UPDATE library_books SET archived_at = NULL, archived_by = NULL WHERE id = %s",
                       (book_id,))
    return True


DELETE_NOT_FOUND = 'not_found'
DELETE_NOT_ARCHIVED = 'not_archived'
DELETE_DONE = 'deleted'


def delete_book(cursor, book_id):
    """Удаляет книгу насовсем (закладки, прогресс, отделы — каскадом).

    -> (DELETE_*, блобы). Удаляется только книга из архива: книгу на полке
    сначала убирают в архив — два разных нажатия, и случайное «Удалить»
    рядом с «Сохранить» не сотрёт прогресс всего отдела.
    """
    cursor.execute("SELECT archived_at IS NOT NULL FROM library_books WHERE id = %s FOR UPDATE", (book_id,))
    row = cursor.fetchone()
    if not row:
        return DELETE_NOT_FOUND, []
    if not row[0]:
        return DELETE_NOT_ARCHIVED, []
    cursor.execute("""
        DELETE FROM library_books WHERE id = %s
        RETURNING bucket, file_blob, cover_blob
    """, (book_id,))
    bucket, file_blob, cover_blob = cursor.fetchone()
    return DELETE_DONE, [(bucket, file_blob), (bucket, cover_blob)]


def set_saved(cursor, user_id, book_id, saved):
    """Добавить в «Сохранённые» или убрать. Доступ к книге проверен open_book."""
    if saved:
        cursor.execute("""
            INSERT INTO library_saved (user_id, book_id) VALUES (%s, %s)
            ON CONFLICT (user_id, book_id) DO NOTHING
        """, (user_id, book_id))
    else:
        cursor.execute("DELETE FROM library_saved WHERE user_id = %s AND book_id = %s",
                       (user_id, book_id))


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
    статусы «В процессе» и «Закончено»).

    Отдел — отдел ЧИТАТЕЛЯ: «как читает СЗоВ», а не «кто открывал книги
    полки СЗоВ». Книги из архива остаются: кто их прочитал — уже история.
    """
    where = []
    params = []
    if query:
        where.append("(u.name ILIKE %s OR u.login ILIKE %s)")
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        params.extend([pattern, pattern])
    if book_id:
        where.append("p.book_id = %s")
        params.append(book_id)
    if department_id:
        where.append("u.department_id = %s")
        params.append(department_id)
    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    cursor.execute(f"""
        SELECT p.user_id, u.name AS user_name, u.login, u.status AS employment_status,
               d.id AS department_id, d.name AS department_name,
               b.id AS book_id, b.title AS book_title, b.author AS book_author, b.total_pages,
               (b.archived_at IS NOT NULL) AS book_archived,
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
        'book_archived': bool(row.get('book_archived')),
        'total_pages': int(row['total_pages'] or 1),
        'percent': 100.0 if row['finished_at'] else round(float(row['percent'] or 0), 1),
        'page': int(row['page'] or 1),
        'status': status_of(True, row['finished_at']),
        # Когда читал: открыл впервые, закончил, открывал последний раз.
        'started_at': _iso(row['started_at']),
        'finished_at': _iso(row['finished_at']),
        'updated_at': _iso(row['updated_at']),
    } for row in rows[:limit]], truncated


def analytics_books(cursor):
    """Книги для фильтра мониторинга — все, и архивные тоже: их прогресс
    по-прежнему в списке, и найти его фильтром должно быть можно."""
    cursor.execute("SELECT id, title, (archived_at IS NOT NULL) FROM library_books ORDER BY title, id")
    return [{'id': row[0], 'title': row[1], 'archived': bool(row[2])} for row in cursor.fetchall()]


def _reading_stats(readers=0, in_progress=0, finished=0, percent_sum=0):
    """Строка статистики чтения. Средний прогресс — по всем прочтениям,
    законченное считается за 100 %: иначе перечитавший начало тянул бы вниз.

    Среднее округляется ВНИЗ: 20 дочитавших и один на 99.9 % дают 99.99, и
    обычное округление показало бы «100 %» рядом с «В процессе 1» — ровно то,
    чего ридер и сервер не допускают у одной книги. Decimal — чтобы 65 не
    превратилось в 64.9 из-за двоичной дроби.
    """
    records = int(in_progress or 0) + int(finished or 0)
    average = None
    if records:
        average = float((Decimal(str(percent_sum or 0)) / records).quantize(Decimal('0.1'), rounding=ROUND_DOWN))
    return {
        'readers': int(readers or 0),
        'in_progress': int(in_progress or 0),
        'finished': int(finished or 0),
        'avg_percent': average,
    }


def analytics_summary(cursor, *, department_id=None):
    """Статистика мониторинга: общая или одного отдела.

    -> {'total': {...}, 'departments': [...] или None}.

    `total` — книги на полке и чтение в охвате: весь портал либо люди одного
    отдела. `departments` — та же статистика по каждому отделу (только для
    общей): книги — выданные отделу, чтение — его сотрудников. Отдел читателя
    у человека один, поэтому люди и записи по отделам складываются в общую
    сумму ровно; книги — нет (одна книга бывает у нескольких отделов), их
    общий счёт отдельный.

    Три запроса на весь ответ, без запроса на отдел.
    """
    params = (department_id,) if department_id else ()
    reader_clause = 'WHERE u.department_id = %s' if department_id else ''
    cursor.execute(f"""
        SELECT u.department_id,
               COUNT(DISTINCT p.user_id),
               COUNT(*) FILTER (WHERE p.finished_at IS NULL),
               COUNT(*) FILTER (WHERE p.finished_at IS NOT NULL),
               COALESCE(SUM(CASE WHEN p.finished_at IS NOT NULL THEN 100 ELSE p.percent END), 0)
          FROM library_progress p
          JOIN users u ON u.id = p.user_id
          {reader_clause}
         GROUP BY u.department_id
    """, params)
    reading = {row[0]: row[1:] for row in cursor.fetchall()}

    # Книги на полке (не в архиве): по отделам и всего — одним запросом.
    cursor.execute("""
        SELECT bd.department_id, COUNT(*)
          FROM library_books b
          JOIN library_book_departments bd ON bd.book_id = b.id
         WHERE b.archived_at IS NULL
         GROUP BY bd.department_id
        UNION ALL
        SELECT NULL, COUNT(*) FROM library_books WHERE archived_at IS NULL
    """)
    shelf = {}
    shelf_total = 0
    for dept, count in cursor.fetchall():
        if dept is None:
            shelf_total = int(count)
        else:
            shelf[dept] = int(count)

    readers = sum(int(values[0]) for values in reading.values())
    in_progress = sum(int(values[1]) for values in reading.values())
    finished = sum(int(values[2]) for values in reading.values())
    percent_sum = sum((Decimal(str(values[3] or 0)) for values in reading.values()), Decimal(0))
    total = {
        'books': shelf.get(department_id, 0) if department_id else shelf_total,
        **_reading_stats(readers, in_progress, finished, percent_sum),
    }
    if department_id:
        return {'total': total, 'departments': None}

    cursor.execute("SELECT id, name FROM departments ORDER BY name, id")
    departments = []
    for dept_id, name in cursor.fetchall():
        # Отдел без книг и без читателей — строка из одних нулей: десяток
        # таких строк заслонил бы те, где что-то происходит.
        if dept_id not in shelf and dept_id not in reading:
            continue
        departments.append({
            'id': dept_id,
            'name': name,
            'books': shelf.get(dept_id, 0),
            **_reading_stats(*reading.get(dept_id, ())),
        })
    # Читают и люди без отдела (управляющие бывают без него) — их строкой в
    # конце, иначе сумма по отделам не сошлась бы с общей.
    if None in reading:
        departments.append({'id': None, 'name': 'Без отдела', 'books': None,
                            **_reading_stats(*reading[None])})
    return {'total': total, 'departments': departments}
