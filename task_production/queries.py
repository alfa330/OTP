# -*- coding: utf-8 -*-
"""SQL раздела «Продакшн»: задачи продакшна, их история и материалы.

КТО ЧТО ВИДИТ (решение владельца 16.10.2026, план раздела):
  * маркетинг — все задачи продакшна и всё готовое: это их рабочее место;
  * админ без своего отдела и супер-админ — всё, как и в «Задачах»;
  * остальные — свои задачи: где они постановщик, поручитель или исполнитель.
Охват «все» или «свои» решает маршрут (routes.py); здесь он приходит флагом.

Действия с задачей (взять в работу, сдать, принять, вернуть) этот модуль не
делает: они идут обычным маршрутом статусов задачи, со всеми его правами и
уведомлениями. Раздел только показывает и хранит материалы.
"""

import task_production

from . import materials

TAG = task_production.PRODUCTION_TAG
OPEN_STATUSES = ('assigned', 'in_progress', 'returned', 'completed')

# Видимость задачи вне охвата «все». Параметр — id смотрящего (три раза).
_PERSONAL_SQL = """(
    t.created_by = %s
    OR t.requested_by_id = %s
    OR EXISTS (SELECT 1 FROM task_assignees ta_v WHERE ta_v.task_id = t.id AND ta_v.user_id = %s)
)"""

# Сколько раз задачу сдавали — это номер последней отправленной версии.
_COMPLETED_COUNT_SQL = """(
    SELECT COUNT(*) FROM task_status_history h
     WHERE h.task_id = t.id AND h.status_code = 'completed'
)"""


def _scope(scope_all, user_id):
    if scope_all:
        return 'TRUE', ()
    uid = int(user_id)
    return _PERSONAL_SQL, (uid, uid, uid)


def is_marketing_member(cursor, user_id):
    cursor.execute("""
        SELECT 1
          FROM users u
          JOIN departments d ON d.id = u.department_id
         WHERE u.id = %s AND LOWER(COALESCE(d.code, '')) = %s
    """, (int(user_id), task_production.PRODUCTION_DEPARTMENT_CODE))
    return cursor.fetchone() is not None


def _people(cursor, task_ids):
    """Исполнители задач: {task_id: [{id, name, avatar_bucket, avatar_blob_path}]} по порядку."""
    if not task_ids:
        return {}
    cursor.execute("""
        SELECT ta.task_id, u.id, u.name, u.avatar_bucket, u.avatar_blob_path
          FROM task_assignees ta
          JOIN users u ON u.id = ta.user_id
         WHERE ta.task_id = ANY(%s)
         ORDER BY ta.task_id, ta.position, ta.user_id
    """, (list(task_ids),))
    out = {}
    for task_id, uid, name, bucket, blob in cursor.fetchall():
        out.setdefault(task_id, []).append({
            'id': uid, 'name': name, 'avatar_bucket': bucket, 'avatar_blob_path': blob,
        })
    return out


_TASK_COLUMNS = """
    t.id, t.subject, t.description, t.status, t.priority, t.is_backlog,
    t.due_at, t.created_at, t.updated_at, t.started_at, t.completed_at,
    t.created_by, uc.name, t.requested_by_id, ur.name, t.requested_by_name,
    t.production_brief, {completed} AS completed_count, t.tag
""".format(completed=_COMPLETED_COUNT_SQL)


def _task_from_row(row):
    return {
        'id': row[0],
        'subject': row[1],
        'description': row[2] or '',
        'status': row[3],
        'priority': row[4],
        'is_backlog': bool(row[5]),
        'due_at': row[6],
        'created_at': row[7],
        'updated_at': row[8],
        'started_at': row[9],
        'completed_at': row[10],
        'created_by': row[11],
        'created_by_name': row[12],
        'requested_by_id': row[13],
        'requested_by_name': row[14] or row[15] or None,
        'brief': task_production.stored_brief(row[16]),
        'completed_count': int(row[17] or 0),
        'tag': row[18],
    }


def _covers(cursor, keys):
    """Обложка каждой задачи: первый показываемый материал её «текущей» версии.

    keys — [(task_id, version)]. Текущая версия у задачи в работе — та, что
    собирается сейчас, а если в ней пусто — последняя отправленная: карточка
    на доске не должна терять картинку в момент, когда задачу вернули.
    """
    if not keys:
        return {}
    task_ids = sorted({k[0] for k in keys})
    cursor.execute("""
        SELECT task_id, version, id, kind, content_type, blob_path, poster_blob_path,
               original_name, duration_seconds, created_at, bucket
          FROM task_production_materials
         WHERE task_id = ANY(%s) AND status = 'ready'
         ORDER BY task_id, version DESC, created_at
    """, (task_ids,))
    by_task = {}
    for row in cursor.fetchall():
        by_task.setdefault(row[0], []).append(row)
    out = {}
    for task_id, version in keys:
        rows = by_task.get(task_id) or []
        # Кандидаты: сначала нужная версия, затем более ранние.
        ordered = [r for r in rows if r[1] == version] + [r for r in rows if r[1] < version]
        counted = sum(1 for r in rows if r[1] == version)
        cover = None
        # Обложка — только кадр-миниатюра: сам ролик или баннер в 5 МБ на
        # доске из двадцати карточек — это сотня мегабайт на одно открытие.
        for r in ordered:
            if r[6] and r[3] in (materials.KIND_VIDEO, materials.KIND_IMAGE):
                cover = r
                break
        out[task_id] = {
            'count': counted,
            'cover': None if cover is None else {
                'id': str(cover[2]), 'kind': cover[3], 'content_type': cover[4],
                'blob_path': cover[5], 'poster_blob_path': cover[6], 'name': cover[7],
                'bucket': cover[10],
                'duration_seconds': float(cover[8]) if cover[8] is not None else None,
            },
        }
    return out


def _version_for_cover(task):
    working = materials.working_version(task['status'], task['completed_count'])
    return working or max(1, task['completed_count'])


def board(cursor, *, scope_all, user_id):
    """Задачи продакшна, которые ещё не приняты. Бэклог не показываем: задача из
    очереди планирования исполнителя не тревожит, как и в «Задачах»."""
    where, params = _scope(scope_all, user_id)
    cursor.execute("""
        SELECT {columns}
          FROM tasks t
          LEFT JOIN users uc ON uc.id = t.created_by
          LEFT JOIN users ur ON ur.id = t.requested_by_id
         WHERE t.tag = %s AND t.status = ANY(%s) AND NOT COALESCE(t.is_backlog, FALSE)
           AND {where}
         ORDER BY t.due_at NULLS LAST, t.created_at DESC
         LIMIT 500
    """.format(columns=_TASK_COLUMNS, where=where), (TAG, list(OPEN_STATUSES)) + params)
    tasks = [_task_from_row(row) for row in cursor.fetchall()]
    _attach_people_and_covers(cursor, tasks)
    return tasks


def _attach_people_and_covers(cursor, tasks):
    people = _people(cursor, [t['id'] for t in tasks])
    covers = _covers(cursor, [(t['id'], _version_for_cover(t)) for t in tasks])
    for task in tasks:
        task['assignees'] = people.get(task['id'], [])
        found = covers.get(task['id']) or {}
        task['materials_count'] = found.get('count', 0)
        task['cover'] = found.get('cover')


def item_codes_for_group(group):
    for code, _title, items in task_production.ITEM_GROUPS:
        if code == group:
            return [item_code for item_code, _label in items]
    return None


def done(cursor, *, scope_all, user_id, query='', group=None, limit=30, offset=0):
    """Принятые задачи продакшна, свежие сверху, с материалами принятой версии.

    Возвращает (задачи, всего). Поиск — по теме задачи и именам файлов.
    """
    where, params = _scope(scope_all, user_id)
    filters = ["t.tag = %s", "t.status = 'accepted'", where]
    values = [TAG] + list(params)
    text = str(query or '').strip()
    if text:
        like = _like(text)
        filters.append("""(t.subject ILIKE %s OR EXISTS (
            SELECT 1 FROM task_production_materials m_q
             WHERE m_q.task_id = t.id AND m_q.status = 'ready' AND m_q.original_name ILIKE %s))""")
        values += [like, like]
    codes = item_codes_for_group(group) if group else None
    if group and codes is None:
        return [], 0
    if codes:
        filters.append("(t.production_brief ->> 'item') = ANY(%s)")
        values.append(codes)
    where_sql = ' AND '.join(filters)
    cursor.execute("SELECT COUNT(*) FROM tasks t WHERE " + where_sql, tuple(values))
    total = int(cursor.fetchone()[0] or 0)
    cursor.execute("""
        SELECT {columns},
               (SELECT MAX(h.changed_at) FROM task_status_history h
                 WHERE h.task_id = t.id AND h.status_code = 'accepted') AS accepted_at
          FROM tasks t
          LEFT JOIN users uc ON uc.id = t.created_by
          LEFT JOIN users ur ON ur.id = t.requested_by_id
         WHERE {where}
         ORDER BY accepted_at DESC NULLS LAST, t.id DESC
         LIMIT %s OFFSET %s
    """.format(columns=_TASK_COLUMNS, where=where_sql),
        tuple(values) + (max(1, min(int(limit), 60)), max(0, int(offset))))
    rows = cursor.fetchall()
    tasks = []
    for row in rows:
        task = _task_from_row(row)
        task['accepted_at'] = row[19]
        tasks.append(task)
    _attach_people_and_covers(cursor, tasks)
    return tasks, total


def task(cursor, task_id):
    cursor.execute("""
        SELECT {columns}
          FROM tasks t
          LEFT JOIN users uc ON uc.id = t.created_by
          LEFT JOIN users ur ON ur.id = t.requested_by_id
         WHERE t.id = %s
    """.format(columns=_TASK_COLUMNS), (int(task_id),))
    row = cursor.fetchone()
    if not row:
        return None
    found = _task_from_row(row)
    found['assignees'] = _people(cursor, [found['id']]).get(found['id'], [])
    return found


def visible(cursor, task_row, *, scope_all, user_id):
    if scope_all:
        return True
    uid = int(user_id)
    if uid in (task_row.get('created_by'), task_row.get('requested_by_id')):
        return True
    return any(int(p['id']) == uid for p in task_row.get('assignees') or [])


def history(cursor, task_id):
    cursor.execute("""
        SELECT h.status_code, h.changed_at, h.changed_by, u.name, h.comment
          FROM task_status_history h
          LEFT JOIN users u ON u.id = h.changed_by
         WHERE h.task_id = %s
         ORDER BY h.changed_at, h.id
    """, (int(task_id),))
    return cursor.fetchall()


_MATERIAL_COLUMNS = """
    m.id, m.task_id, m.version, m.status, m.kind, m.original_name, m.extension,
    m.content_type, m.file_size, m.bucket, m.blob_path, m.upload_session, m.upload_origin,
    m.poster_blob_path, m.duration_seconds, m.width, m.height, m.uploaded_by, u.name,
    m.created_at, m.ready_at, m.library_category
"""


def _material_from_row(row):
    return {
        'id': str(row[0]),
        'task_id': row[1],
        'version': row[2],
        'status': row[3],
        'kind': row[4],
        'name': row[5],
        'extension': row[6],
        'content_type': row[7],
        'size': int(row[8] or 0),
        'bucket': row[9],
        'blob_path': row[10],
        'upload_session': row[11],
        'upload_origin': row[12],
        'poster_blob_path': row[13],
        'duration_seconds': float(row[14]) if row[14] is not None else None,
        'width': row[15],
        'height': row[16],
        'uploaded_by': row[17],
        'uploaded_by_name': row[18],
        'created_at': row[19],
        'ready_at': row[20],
        'library_category': row[21],
    }


def materials_of(cursor, task_id):
    cursor.execute("""
        SELECT {columns}
          FROM task_production_materials m
          LEFT JOIN users u ON u.id = m.uploaded_by
         WHERE m.task_id = %s
         ORDER BY m.version, m.created_at, m.id
    """.format(columns=_MATERIAL_COLUMNS), (int(task_id),))
    return [_material_from_row(row) for row in cursor.fetchall()]


def material(cursor, material_id, *, lock=False):
    cursor.execute("""
        SELECT {columns}
          FROM task_production_materials m
          LEFT JOIN users u ON u.id = m.uploaded_by
         WHERE m.id = %s {lock}
    """.format(columns=_MATERIAL_COLUMNS, lock='FOR UPDATE OF m' if lock else ''), (str(material_id),))
    row = cursor.fetchone()
    return _material_from_row(row) if row else None


def uploading_by_others(cursor, task_id, version, user_id):
    """Что в этой версии ещё грузится у соисполнителей — чтобы не сдать без их файлов."""
    cursor.execute("""
        SELECT m.original_name, u.name
          FROM task_production_materials m
          LEFT JOIN users u ON u.id = m.uploaded_by
         WHERE m.task_id = %s AND m.version = %s AND m.status = 'uploading'
           AND m.uploaded_by IS DISTINCT FROM %s
           AND m.created_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - INTERVAL '6 days'
         ORDER BY m.created_at
    """, (int(task_id), int(version), int(user_id)))
    return [{'name': row[0], 'uploaded_by_name': row[1] or ''} for row in cursor.fetchall()]


def count_in_version(cursor, task_id, version):
    cursor.execute("""
        SELECT COUNT(*) FROM task_production_materials
         WHERE task_id = %s AND version = %s
    """, (int(task_id), int(version)))
    return int(cursor.fetchone()[0] or 0)


def pending_for(cursor, *, task_id, user_id, name, size, fingerprint=None):
    """Незаконченная загрузка того же файла тем же человеком — для докачки
    после закрытой вкладки: тот же файл узнаём по имени, размеру и отпечатку
    браузера (дате изменения). Другой файл с тем же именем и размером начнётся
    заново, а не допишется в чужую сессию."""
    cursor.execute("""
        SELECT {columns}
          FROM task_production_materials m
          LEFT JOIN users u ON u.id = m.uploaded_by
         WHERE m.task_id IS NOT DISTINCT FROM %s AND m.uploaded_by = %s AND m.status = 'uploading'
           AND m.original_name = %s AND m.file_size = %s
           AND COALESCE(m.client_fingerprint, '') = COALESCE(%s, '')
           AND m.created_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - INTERVAL '6 days'
         ORDER BY m.created_at DESC
         LIMIT 1
    """.format(columns=_MATERIAL_COLUMNS), (None if task_id is None else int(task_id), int(user_id), name,
                                          int(size), fingerprint or None))
    row = cursor.fetchone()
    return _material_from_row(row) if row else None


def insert_material(cursor, *, material_id, task_id, version, kind, name, extension, content_type,
                    size, bucket, blob_path, upload_session, upload_origin, uploaded_by, fingerprint=None,
                    library_category=None):
    cursor.execute("""
        INSERT INTO task_production_materials (
            id, task_id, version, status, kind, original_name, extension, content_type,
            file_size, bucket, blob_path, upload_session, upload_origin, uploaded_by, client_fingerprint,
            library_category
        ) VALUES (%s, %s, %s, 'uploading', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (str(material_id), None if task_id is None else int(task_id), int(version), kind, name, extension,
          content_type, int(size), bucket, blob_path, upload_session, upload_origin, int(uploaded_by),
          fingerprint or None, library_category))


def mark_ready(cursor, material_id, *, version, duration, width, height):
    cursor.execute("""
        UPDATE task_production_materials
           SET status = 'ready', version = %s, upload_session = NULL, upload_origin = NULL,
               duration_seconds = %s, width = %s, height = %s,
               ready_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE id = %s
    """, (int(version), duration, width, height, str(material_id)))


def set_poster(cursor, material_id, poster_blob_path):
    cursor.execute("""
        UPDATE task_production_materials SET poster_blob_path = %s WHERE id = %s
    """, (poster_blob_path, str(material_id)))


def delete_material(cursor, material_id):
    cursor.execute("DELETE FROM task_production_materials WHERE id = %s", (str(material_id),))


def _like(text):
    """Образец ILIKE «содержит text»: %, _ и \\ в поиске — буквы, а не шаблон."""
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def library(cursor, *, query='', category=None, limit=30, offset=0):
    """Файлы «Медиатеки» (без задачи), свежие сверху; (файлы, всего).

    Отбор — по категории, поиск — по имени файла. Недогруженные сюда не
    попадают: их видит только тот, кто грузит, — в своей строке загрузки.
    """
    filters = ["m.task_id IS NULL", "m.status = 'ready'"]
    values = []
    if category:
        filters.append("m.library_category = %s")
        values.append(category)
    text = str(query or '').strip()
    if text:
        filters.append("m.original_name ILIKE %s")
        values.append(_like(text))
    where = ' AND '.join(filters)
    cursor.execute("SELECT COUNT(*) FROM task_production_materials m WHERE " + where, tuple(values))
    total = int(cursor.fetchone()[0] or 0)
    cursor.execute("""
        SELECT {columns}
          FROM task_production_materials m
          LEFT JOIN users u ON u.id = m.uploaded_by
         WHERE {where}
         ORDER BY m.created_at DESC, m.id
         LIMIT %s OFFSET %s
    """.format(columns=_MATERIAL_COLUMNS, where=where),
        tuple(values) + (max(1, min(int(limit), 60)), max(0, int(offset))))
    return [_material_from_row(row) for row in cursor.fetchall()], total


def library_counts(cursor):
    """Сколько готовых файлов в каждой категории — числа на плитках.

    Поиск их не меняет: плитка говорит, сколько всего лежит в категории, как
    папка в Finder. {код: число}; файлы без категории — под None.
    """
    cursor.execute("""
        SELECT library_category, COUNT(*) FROM task_production_materials
         WHERE task_id IS NULL AND status = 'ready'
         GROUP BY 1
    """)
    return {row[0]: int(row[1]) for row in cursor.fetchall()}


def set_library_category(cursor, material_id, category):
    cursor.execute("""
        UPDATE task_production_materials SET library_category = %s WHERE id = %s AND task_id IS NULL
    """, (category, str(material_id)))
    return cursor.rowcount


LIBRARY_EVENT_ADDED = 'added'
LIBRARY_EVENT_MOVED = 'moved'
LIBRARY_EVENT_DELETED = 'deleted'


def add_library_event(cursor, *, material, action, actor_id, category, from_category=None):
    """Строка «Истории»: имя, вид и размер берутся из файла — после удаления их больше негде взять."""
    cursor.execute("""
        INSERT INTO task_production_library_events
            (material_id, action, file_name, kind, file_size, category, from_category, actor_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (str(material['id']), action, material['name'], material['kind'], int(material.get('size') or 0),
          category, from_category, None if actor_id is None else int(actor_id)))


def library_history(cursor, *, query='', category=None, limit=50, offset=0):
    """«История» «Медиатеки», свежее сверху; (события, всего).

    Категория отбирает и перенос ИЗ неё: файл ушёл из «Рилсов» — это тоже история «Рилсов».
    Поиск — по имени файла и по имени сотрудника.
    """
    filters = ['TRUE']
    values = []
    if category:
        filters.append('(e.category = %s OR e.from_category = %s)')
        values += [category, category]
    text = str(query or '').strip()
    if text:
        filters.append('(e.file_name ILIKE %s OR u.name ILIKE %s)')
        values += [_like(text), _like(text)]
    where = ' AND '.join(filters)
    cursor.execute("""
        SELECT COUNT(*) FROM task_production_library_events e
          LEFT JOIN users u ON u.id = e.actor_id
         WHERE """ + where, tuple(values))
    total = int(cursor.fetchone()[0] or 0)
    cursor.execute("""
        SELECT e.id, e.material_id, e.action, e.file_name, e.kind, e.file_size, e.category, e.from_category,
               e.actor_id, u.name, u.avatar_bucket, u.avatar_blob_path, e.created_at
          FROM task_production_library_events e
          LEFT JOIN users u ON u.id = e.actor_id
         WHERE {where}
         ORDER BY e.created_at DESC, e.id DESC
         LIMIT %s OFFSET %s
    """.format(where=where), tuple(values) + (max(1, min(int(limit), 100)), max(0, int(offset))))
    events = []
    for row in cursor.fetchall():
        events.append({
            'id': int(row[0]),
            'material_id': str(row[1]),
            'action': row[2],
            'name': row[3],
            'kind': row[4],
            'size': int(row[5] or 0),
            'category': row[6],
            'from_category': row[7],
            'actor': None if row[8] is None else {
                'id': row[8], 'name': row[9] or '', 'avatar_bucket': row[10], 'avatar_blob_path': row[11],
            },
            'at': row[12],
        })
    return events, total


def library_materials(cursor, material_ids):
    """Готовые файлы «Медиатеки» по списку id одним запросом: {id: файл}."""
    ids = sorted({str(value) for value in material_ids or []})
    if not ids:
        return {}
    cursor.execute("""
        SELECT {columns}
          FROM task_production_materials m
          LEFT JOIN users u ON u.id = m.uploaded_by
         WHERE m.id = ANY(%s::uuid[]) AND m.task_id IS NULL AND m.status = 'ready'
    """.format(columns=_MATERIAL_COLUMNS), (ids,))
    found = [_material_from_row(row) for row in cursor.fetchall()]
    return {item['id']: item for item in found}


def stale_uploads(cursor, *, limit=20):
    """Брошенные загрузки старше недели: сессия в GCS уже истекла."""
    cursor.execute("""
        SELECT id, bucket, blob_path FROM task_production_materials
         WHERE status = 'uploading'
           AND created_at < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - INTERVAL '8 days'
         ORDER BY created_at
         LIMIT %s
    """, (int(limit),))
    return [{'id': str(r[0]), 'bucket': r[1], 'blob_path': r[2]} for r in cursor.fetchall()]


def blobs_of_task(cursor, task_id):
    """Все объекты материалов задачи — чтобы убрать их вместе с задачей.

    Незаконченные загрузки — вместе с адресом сессии: её надо отменить, иначе
    браузер догрузит файл уже после удаления задачи и объект останется без хозяина.
    """
    cursor.execute("""
        SELECT bucket, blob_path, poster_blob_path, status, upload_session, upload_origin
          FROM task_production_materials WHERE task_id = %s
    """, (int(task_id),))
    out = []
    for bucket, blob, poster, status, session, origin in cursor.fetchall():
        uploading = status == 'uploading'
        out.append({'bucket': bucket, 'blob_path': blob,
                    'upload_session': session if uploading else None,
                    'upload_origin': origin if uploading else None})
        if poster:
            out.append({'bucket': bucket, 'blob_path': poster, 'upload_session': None, 'upload_origin': None})
    return out
