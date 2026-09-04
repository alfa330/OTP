"""SQL структуры раздела «Вики»: пространства, разделы, правила доступа, журнал.

Вынесено из queries.py, чтобы тот остался про периметр доступа, а этот — про
управление содержимым структуры. Все функции принимают готовый курсор.
"""

import json

from .access import ROLE_LEVELS
from .schema import space_features

# ─────────────────────────────────────────────────────────────────────────────
# Пространства
#
# Верхний уровень вики и единственная жёсткая граница между отделами: раздел
# живёт внутри пространства и за список его отделов не выходит, даже будучи
# публичным. Плюс набор тумблеров — из каких вкладок состоит раздел «Вики» у
# тех, кому пространство выдано. Историю переезда см. в wiki/schema.py.
# ─────────────────────────────────────────────────────────────────────────────

_SPACE_KEYS = ('id', 'code', 'name', 'description', 'icon', 'department_id',
               'department_name', 'status', 'position', 'sections_count',
               'department_ids', 'features')


def _shape_space(row):
    space = dict(zip(_SPACE_KEYS, row))
    space['department_ids'] = list(space['department_ids'] or [])
    # Наружу отдаём ПОЛНЫЙ набор тумблеров, а не то, что лежит в базе: пустой
    # объект означает «всё включено», и раскрывать его на каждой витрине
    # отдельно — прямой путь к расхождению интерфейса с сервером.
    space['features'] = space_features(space['features'])
    return space


def list_spaces(cursor, include_archived=False):
    """Пространства со списком отделов. Список — агрегатом, а не вторым
    запросом: пространств немного, но N+1 здесь повторялся бы на каждой
    перерисовке конструктора."""
    cursor.execute(
        """
        SELECT sp.id, sp.code, sp.name, sp.description, sp.icon, sp.department_id,
               d.name AS department_name, sp.status, sp.position,
               (SELECT count(*) FROM wiki_sections s
                 WHERE s.space_id = sp.id AND s.status = 'active') AS sections_count,
               COALESCE((SELECT array_agg(sd.department_id ORDER BY sd.department_id)
                           FROM wiki_space_departments sd
                          WHERE sd.space_id = sp.id), '{}') AS department_ids,
               sp.features
          FROM wiki_spaces sp
          LEFT JOIN departments d ON d.id = sp.department_id
         WHERE (%s OR sp.status = 'active')
         ORDER BY sp.position, sp.id
        """,
        (include_archived,),
    )
    return [_shape_space(row) for row in cursor.fetchall()]


def create_space(cursor, *, name, code, description, icon, department_id, created_by,
                 features=None):
    cursor.execute(
        """
        INSERT INTO wiki_spaces (code, name, description, icon, department_id,
                                 position, created_by, features)
        VALUES (%s, %s, %s, %s, %s,
                COALESCE((SELECT max(position) + 1 FROM wiki_spaces), 0), %s,
                %s::jsonb)
        RETURNING id
        """,
        (code or None, name, description, icon, department_id, created_by,
         json.dumps(features or {})),
    )
    return cursor.fetchone()[0]


_SPACE_UPDATABLE = ('name', 'description', 'icon', 'department_id', 'status', 'position',
                    'code', 'features')


def update_space(cursor, space_id, fields):
    """Частичное обновление. Пустой набор полей — в базу не ходим."""
    sets, values = [], []
    for key in _SPACE_UPDATABLE:
        if key in fields:
            # features приходит словарём: без явного каста psycopg2 отдал бы его
            # как строку, а колонка jsonb такую подстановку не принимает.
            sets.append(key + (' = %s::jsonb' if key == 'features' else ' = %s'))
            values.append(json.dumps(fields[key]) if key == 'features' else fields[key])
    if not sets:
        return False
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    values.append(space_id)
    cursor.execute('UPDATE wiki_spaces SET ' + ', '.join(sets) + ' WHERE id = %s', values)
    return cursor.rowcount > 0


def set_space_departments(cursor, space_id, department_ids):
    """Переписать список отделов пространства.

    Пустой список = видно всем: строк не остаётся, и периметр видит отсутствие
    ограничения. Ровно то же соглашение, что у публичного раздела
    (set_public_departments) — двух разных значений у «пусто» в одном разделе
    быть не должно.
    """
    cursor.execute('DELETE FROM wiki_space_departments WHERE space_id = %s', (space_id,))
    wanted = sorted({int(x) for x in (department_ids or []) if x})
    if not wanted:
        return
    cursor.executemany(
        'INSERT INTO wiki_space_departments (space_id, department_id) '
        'VALUES (%s, %s) ON CONFLICT DO NOTHING',
        [(space_id, dep) for dep in wanted],
    )


def space_open_to(cursor, space_id, department_ids):
    """Открыто ли пространство хотя бы одному из перечисленных отделов.

    Пустой список отделов у пространства = открыто всем, и это соглашение
    здесь ТОЖЕ действует: иначе право на структуру внутри «общего»
    пространства не получил бы никто, кроме супер-админа.
    """
    cursor.execute(
        """
        SELECT NOT EXISTS (SELECT 1 FROM wiki_space_departments WHERE space_id = %(space)s)
            OR EXISTS (SELECT 1 FROM wiki_space_departments
                        WHERE space_id = %(space)s AND department_id = ANY(%(depts)s))
        """,
        {'space': space_id, 'depts': list(department_ids or []) or [-1]},
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def space_ids_for_departments(cursor, department_codes):
    """id активных пространств, чья граница ЯВНО названа этими отделами.

    Нужно РАЗДЕЛАМ ВНЕ ВИКИ, которые читают её справочники. У статьи
    пространство спрашивают у человека (routes_structure.request_space): он пришёл в вики
    и переключателем выбрал, в какую. У «Обращений» и «Посылок» спрашивать некого и
    нечего: переключателя там нет, а вопрос стоит не «что мне показать», а
    «работает ли сегодня НАШ офис». Слово «наш» у раздела выражено ровно одним —
    отделом, которому раздел выдан (crm.access.SECTION_DEPARTMENT_CODE,
    parcels.access.SECTION_DEPARTMENT_CODES), а отдел с пространством связывает эта
    таблица. Ответ поэтому не зависит от того, КТО смотрит: и оператор СЗоВ,
    и глобальный админ без отдела заводят обращение про одни и те же офисы.

    «Пусто = видно всем» ЗДЕСЬ НЕ ДЕЙСТВУЕТ, в отличие от space_open_to и
    queries.spaces_for_user. Там вопрос разрешительный — «куда человек может
    прийти», и пространство без списка отделов честно открыто каждому. Здесь
    вопрос о ПРИНАДЛЕЖНОСТИ — «чей это справочник», и пространство, не
    назвавшее отделов, не назвало и этого. Иначе первое же полунастроенное
    пространство, заведённое конструктором, снова вылило бы свои офисы в чужой
    раздел — ровно та утечка, из-за которой справочники и стали принадлежать
    пространству (schema._scope_directories_to_space).

    Пустой ответ значит «отделу не выдано ни одного пространства», а НЕ
    «справочник пуст»: разница важна тому, кто по пустому списку собирается
    ответить водителю «офиса в городе нет» (crm.scenarios.office_verdict).
    """
    codes = sorted({str(code or '').strip().lower()
                    for code in (department_codes or []) if str(code or '').strip()})
    if not codes:
        return []
    cursor.execute(
        """
        SELECT DISTINCT sd.space_id
          FROM wiki_space_departments sd
          JOIN wiki_spaces sp ON sp.id = sd.space_id AND sp.status = 'active'
          JOIN departments d ON d.id = sd.department_id
         WHERE LOWER(TRIM(COALESCE(d.code, ''))) = ANY(%s)
         ORDER BY sd.space_id
        """,
        (codes,),
    )
    return [row[0] for row in cursor.fetchall()]


def space_department_ids(cursor, space_id):
    """Отделы, которым выдано ЭТО пространство. Обратная сторона
    space_ids_for_departments: там по отделам искали пространства, здесь по
    пространству — отделы.

    Нужна справочникам выбора субъекта и сотрудников. Пространство — граница
    между отделами (и между клиентами: «Таксопарки» и «Тез» — разные компании),
    поэтому предлагать в «Таксопарках» отдел «Тез КЦ» значит показывать чужую
    оргструктуру и обещать правило, которое ничего не откроет: до статей чужого
    пространства человека всё равно не пустит _SPACE_GATE_SQL.

    Пустой список — законный ответ («пространству не выдали ни одного отдела»),
    и подменять его на «все» НЕЛЬЗЯ: полунастроенное пространство снова вылило
    бы чужие отделы в справочник. Та же причина, по которой правила «нет отделов
    = видно всем» нет в space_ids_for_departments.
    """
    if not space_id:
        return []
    cursor.execute(
        """
        SELECT sd.department_id
          FROM wiki_space_departments sd
          JOIN departments d ON d.id = sd.department_id AND d.is_active
         WHERE sd.space_id = %s
         ORDER BY sd.department_id
        """,
        (space_id,),
    )
    return [row[0] for row in cursor.fetchall()]


def narrow_to_space(department_ids, space_department_ids):
    """Пересечение границы РАЗДАЮЩЕГО и границы ПРОСТРАНСТВА.

    Две разные границы, и складывать их надо, а не выбирать одну:
      * department_ids — «чей это человек» (супервайзер работает со своим
        отделом). None означает «без границы» — супер-админ, администратор вики;
      * space_department_ids — «чьё это пространство». None означает «про
        пространство не спрашивали» (например конструктор пространств, которому
        нужны ВСЕ отделы, чтобы было из чего раздавать).

    Обе None — None: список не сужается вовсе.
    """
    if space_department_ids is None:
        return department_ids
    if department_ids is None:
        return sorted(space_department_ids)
    return sorted(set(department_ids) & set(space_department_ids))


# ─────────────────────────────────────────────────────────────────────────────
# Разделы
# ─────────────────────────────────────────────────────────────────────────────

_SECTION_KEYS = ('id', 'space_id', 'parent_section_id', 'name', 'slug', 'description',
                 'icon', 'visibility_scope', 'owner_user_id', 'owner_name', 'status',
                 'position', 'department_id', 'department_name', 'section_kind',
                 'articles_count', 'rules_count')


def list_sections(cursor, space_id=None, include_archived=False):
    cursor.execute(
        """
        SELECT s.id, s.space_id, s.parent_section_id, s.name, s.slug, s.description,
               s.icon, s.visibility_scope, s.owner_user_id, u.name AS owner_name,
               s.status, s.position, s.department_id, d.name AS department_name,
               s.section_kind,
               (SELECT count(*) FROM wiki_article_sections a WHERE a.section_id = s.id),
               (SELECT count(*) FROM wiki_section_access_rules r WHERE r.section_id = s.id)
          FROM wiki_sections s
          LEFT JOIN users u ON u.id = s.owner_user_id
          LEFT JOIN departments d ON d.id = s.department_id
         WHERE (%(space)s::int IS NULL OR s.space_id = %(space)s::int)
           AND (%(archived)s OR s.status = 'active')
         ORDER BY s.space_id, s.position, s.id
        """,
        {'space': space_id, 'archived': include_archived},
    )
    return [dict(zip(_SECTION_KEYS, row)) for row in cursor.fetchall()]


def public_departments_by_section(cursor, section_ids):
    """{section_id: [department_id, ...]} — кому виден публичный раздел.

    Одним запросом на всё дерево, а не по разделу: иначе вкладка «Структура»
    повторила бы N+1, от которого весь этот модуль и уходил.
    """
    ids = list(section_ids or ())
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT section_id, department_id
          FROM wiki_section_public_departments
         WHERE section_id = ANY(%s)
         ORDER BY section_id, department_id
        """,
        (ids,),
    )
    result = {}
    for section_id, department_id in cursor.fetchall():
        result.setdefault(section_id, []).append(department_id)
    return result


def set_public_departments(cursor, section_id, department_ids):
    """Переписать список отделов публичного раздела.

    Пустой список означает «виден всем» — тогда строк не остаётся вовсе, и
    периметр видит отсутствие ограничения (см. _AUTO_SECTIONS_SQL). Хранить
    «пусто» как отдельный маркер было бы вторым способом сказать то же самое.
    """
    cursor.execute('DELETE FROM wiki_section_public_departments WHERE section_id = %s',
                   (section_id,))
    wanted = sorted({int(x) for x in (department_ids or []) if x})
    if not wanted:
        return
    cursor.executemany(
        'INSERT INTO wiki_section_public_departments (section_id, department_id) '
        'VALUES (%s, %s) ON CONFLICT DO NOTHING',
        [(section_id, dep) for dep in wanted],
    )


def article_counts_by_section(cursor, article_ids):
    """Сколько статей из переданного множества лежит в каждом разделе.

    Нужен дереву разделов: счётчик рядом с названием обязан совпадать с тем,
    что человек увидит, открыв раздел. Общий счётчик articles_count оставлен
    вкладке «Структура» — там вопрос другой: сколько статей в разделе вообще.
    """
    ids = list(article_ids or ())
    if not ids:
        return {}
    cursor.execute(
        """
        SELECT section_id, count(*)
          FROM wiki_article_sections
         WHERE article_id = ANY(%s)
         GROUP BY section_id
        """,
        (ids,),
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


def section_kind_of(department_id):
    """Вид раздела выводится из отдела, а не задаётся вторым полем.

    Два независимых поля разъезжаются: раздел с section_kind='department' и
    пустым department_id ничего не значит, а обратная пара молча теряет
    уникальный индекс uq_wiki_section_department. Источник истины один — отдел.
    """
    return 'department' if department_id else 'common'


def department_branch_taken(cursor, *, space_id, parent_section_id, department_id,
                            exclude_id=None):
    """Занят ли отдел соседней веткой того же родителя.

    На (space_id, parent, department_id) висит частичный UNIQUE, и без этой
    проверки повтор падал бы в обработчик ошибок — человек увидел бы
    «Внутреннюю ошибку раздела Вики» вместо внятного ответа. Ровно та же
    история, что со слагом раздела (см. free_section_slug).
    """
    if not department_id:
        return None
    cursor.execute(
        """
        SELECT name FROM wiki_sections
         WHERE space_id = %(space)s
           AND COALESCE(parent_section_id, 0) = COALESCE(%(parent)s::int, 0)
           AND department_id = %(dept)s
           AND section_kind = 'department' AND status = 'active'
           AND (%(exclude)s::int IS NULL OR id <> %(exclude)s::int)
         LIMIT 1
        """,
        {'space': space_id, 'parent': parent_section_id, 'dept': department_id,
         'exclude': exclude_id},
    )
    row = cursor.fetchone()
    return row[0] if row else None


def create_section(cursor, *, space_id, parent_section_id, name, slug, description,
                   icon, visibility_scope, owner_user_id, created_by,
                   department_id=None):
    cursor.execute(
        """
        INSERT INTO wiki_sections (space_id, parent_section_id, name, slug, description,
                                   icon, visibility_scope, owner_user_id, position,
                                   department_id, section_kind, created_by)
        VALUES (%(space)s, %(parent)s, %(name)s, %(slug)s, %(description)s, %(icon)s,
                %(scope)s, %(owner)s,
                COALESCE((SELECT max(position) + 1 FROM wiki_sections
                           WHERE space_id = %(space)s), 0),
                %(dept)s, %(kind)s, %(created_by)s)
        RETURNING id
        """,
        {'space': space_id, 'parent': parent_section_id, 'name': name, 'slug': slug,
         'description': description, 'icon': icon, 'scope': visibility_scope,
         'owner': owner_user_id, 'dept': department_id,
         'kind': section_kind_of(department_id), 'created_by': created_by},
    )
    return cursor.fetchone()[0]


_SECTION_UPDATABLE = ('name', 'slug', 'description', 'icon', 'visibility_scope',
                      'owner_user_id', 'status', 'position', 'parent_section_id',
                      'department_id', 'section_kind')


def update_section(cursor, section_id, fields):
    sets, values = [], []
    for key in _SECTION_UPDATABLE:
        if key in fields:
            sets.append(key + ' = %s')
            values.append(fields[key])
    if not sets:
        return False
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    values.append(section_id)
    cursor.execute('UPDATE wiki_sections SET ' + ', '.join(sets) + ' WHERE id = %s', values)
    return cursor.rowcount > 0


def free_section_slug(cursor, space_id, base, exclude_id=None):
    """Свободный слаг в пределах пространства: к занятому дописывается номер.

    В базе на (space_id, slug) висит UNIQUE, и попытка создать второй раздел с
    тем же названием падала прямо в обработчик ошибок — человек видел
    «Внутренняя ошибка раздела Вики» вместо внятного ответа. Занять слаг может
    и АРХИВНЫЙ раздел: архивируют обычно как раз одноимённый дубль, и имя после
    этого остаётся занятым.

    Так же ведут себя статьи (slug_is_free в wiki/edit.py) — конструктор не
    должен отличаться от них поведением.
    """
    base = (base or 'section')[:200]
    slug, suffix = base, 2
    while True:
        cursor.execute(
            'SELECT 1 FROM wiki_sections WHERE space_id = %s AND slug = %s '
            'AND (%s::int IS NULL OR id <> %s::int) LIMIT 1',
            (space_id, slug, exclude_id, exclude_id),
        )
        if cursor.fetchone() is None:
            return slug
        slug = '%s-%d' % (base[:190], suffix)
        suffix += 1


def section_would_cycle(cursor, section_id, new_parent_id):
    """Не станет ли раздел собственным предком.

    Без этой проверки дерево зацикливается одним перемещением, и любой
    рекурсивный запрос по нему уходит в бесконечность — включая тот, что
    считает периметр доступа.
    """
    if not new_parent_id:
        return False
    if int(new_parent_id) == int(section_id):
        return True
    cursor.execute(
        """
        WITH RECURSIVE up AS (
            SELECT id, parent_section_id FROM wiki_sections WHERE id = %s
            UNION ALL
            SELECT s.id, s.parent_section_id
              FROM wiki_sections s JOIN up ON s.id = up.parent_section_id
        )
        SELECT 1 FROM up WHERE id = %s LIMIT 1
        """,
        (new_parent_id, section_id),
    )
    return cursor.fetchone() is not None


def section_exists(cursor, section_id):
    cursor.execute('SELECT space_id FROM wiki_sections WHERE id = %s', (section_id,))
    row = cursor.fetchone()
    return row[0] if row else None


def section_subtree_ids(cursor, section_id):
    """Раздел и все его потомки, от корня вниз.

    Нужен переезду в другое пространство: подразделы обязаны ехать вместе с
    родителем (см. move_section_to_space).
    """
    cursor.execute(
        """
        WITH RECURSIVE down AS (
            SELECT id, 0 AS depth FROM wiki_sections WHERE id = %s
            UNION ALL
            SELECT s.id, down.depth + 1
              FROM wiki_sections s JOIN down ON s.parent_section_id = down.id
        )
        SELECT id FROM down ORDER BY depth, id
        """,
        (section_id,),
    )
    return [row[0] for row in cursor.fetchall()]


def move_section_to_space(cursor, section_id, *, space_id, parent_section_id=None):
    """Перенести раздел в другое пространство ВМЕСТЕ С ПОДДЕРЕВОМ.

    Почему space_id нет в _SECTION_UPDATABLE и переезд живёт отдельной функцией:
    пространство хранится у КАЖДОГО раздела своим полем, а не выводится из
    родителя. Увезти одну строку — значит оставить её подразделы числиться в
    старом пространстве: вкладка «Разделы» строит дерево внутри пространства, и
    ветка исчезла бы с обеих сторон — у родителя нет детей, у детей нет
    родителя. Поэтому переезд всегда групповой, и обычный частичный UPDATE к
    нему не подпускается.

    Родителем становится parent_section_id (None — корень целевого
    пространства); у остальных разделов поддерева родитель не меняется — он
    едет вместе с ними.

    Слаг уникален в пределах пространства, поэтому каждому переезжающему
    разделу он пересчитывается: в целевом пространстве такой мог быть уже
    занят, и переезд падал бы в обработчик ошибок вместо внятного ответа.
    Возвращает число перенесённых разделов.
    """
    ids = section_subtree_ids(cursor, section_id)
    if not ids:
        return 0
    cursor.execute('SELECT id, slug FROM wiki_sections WHERE id = ANY(%s)', (ids,))
    slugs = dict(cursor.fetchall())
    for member_id in ids:
        # Слаг подбирается по одному и сразу записывается: следующий раздел
        # поддерева обязан видеть занятое предыдущим.
        slug = free_section_slug(cursor, space_id, slugs.get(member_id),
                                 exclude_id=member_id)
        if member_id == section_id:
            cursor.execute(
                """
                UPDATE wiki_sections
                   SET space_id = %s, parent_section_id = %s, slug = %s,
                       updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                 WHERE id = %s
                """,
                (space_id, parent_section_id, slug, member_id),
            )
        else:
            cursor.execute(
                """
                UPDATE wiki_sections
                   SET space_id = %s, slug = %s,
                       updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                 WHERE id = %s
                """,
                (space_id, slug, member_id),
            )
    return len(ids)


# ─────────────────────────────────────────────────────────────────────────────
# Правила доступа к разделам
# ─────────────────────────────────────────────────────────────────────────────

_RULE_KEYS = ('id', 'section_id', 'section_name', 'subject_type', 'subject_id',
              'subject_role', 'can_read', 'can_create', 'can_edit', 'can_delete',
              'can_publish', 'can_approve', 'grant_subsections', 'manage_subsections',
              'min_role_level', 'subject_label')


def list_section_rules(cursor, section_id=None):
    """Правила с человекочитаемой подписью субъекта.

    Подпись собирается прямо в SQL: иначе экран управления доступом делал бы
    запрос на каждое правило и повторил бы N+1 из оригинала, где матрица
    доступа стоила двух запросов на каждую должность.
    """
    cursor.execute(
        """
        SELECT r.id, r.section_id, s.name AS section_name, r.subject_type, r.subject_id,
               r.subject_role, r.can_read, r.can_create, r.can_edit, r.can_delete,
               r.can_publish, r.can_approve, r.grant_subsections,
               r.manage_subsections, r.min_role_level,
               CASE r.subject_type
                   WHEN 'department'      THEN (SELECT name FROM departments WHERE id = r.subject_id)
                   WHEN 'department_head' THEN (SELECT 'Глава: ' || name FROM departments WHERE id = r.subject_id)
                   WHEN 'direction'  THEN (SELECT name FROM directions  WHERE id = r.subject_id)
                   WHEN 'group'      THEN (SELECT name FROM groups      WHERE id = r.subject_id)
                   WHEN 'wiki_role'  THEN (SELECT name FROM wiki_roles  WHERE id = r.subject_id)
                   WHEN 'user'       THEN (SELECT name FROM users       WHERE id = r.subject_id)
                   ELSE r.subject_role
               END AS subject_label
          FROM wiki_section_access_rules r
          JOIN wiki_sections s ON s.id = r.section_id
         WHERE (%(section)s::int IS NULL OR r.section_id = %(section)s::int)
         ORDER BY r.section_id, r.subject_type, r.id
        """,
        {'section': section_id},
    )
    return [dict(zip(_RULE_KEYS, row)) for row in cursor.fetchall()]


def upsert_section_rule(cursor, *, section_id, subject_type, subject_id, subject_role,
                        permissions, grant_subsections, created_by,
                        min_role_level=None, manage_subsections=False):
    """Создать или обновить правило. Уникальность — по паре (раздел, субъект).

    manage_subsections — право строить дерево внутри этой ветки. Отдельным
    аргументом, а не седьмым ключом permissions: шесть прав описывают
    содержимое раздела и попадают в capabilities_from_grants, а это — про
    устройство дерева, и в способности оно не превращается никогда.
    """
    cursor.execute(
        """
        INSERT INTO wiki_section_access_rules
            (section_id, subject_type, subject_id, subject_role,
             can_read, can_create, can_edit, can_delete, can_publish, can_approve,
             grant_subsections, manage_subsections, min_role_level, created_by)
        VALUES (%(section)s, %(stype)s, %(sid)s, %(srole)s,
                %(read)s, %(create)s, %(edit)s, %(delete)s, %(publish)s, %(approve)s,
                %(deep)s, %(manage)s, %(level)s, %(by)s)
        ON CONFLICT (section_id, subject_type,
                     COALESCE(subject_id, -1), COALESCE(subject_role, ''),
                     COALESCE(min_role_level, -1))
        DO UPDATE SET can_read          = EXCLUDED.can_read,
                      can_create        = EXCLUDED.can_create,
                      can_edit          = EXCLUDED.can_edit,
                      can_delete        = EXCLUDED.can_delete,
                      can_publish       = EXCLUDED.can_publish,
                      can_approve       = EXCLUDED.can_approve,
                      grant_subsections  = EXCLUDED.grant_subsections,
                      manage_subsections = EXCLUDED.manage_subsections,
                      min_role_level     = EXCLUDED.min_role_level,
                      updated_at        = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
        RETURNING id
        """,
        {'section': section_id, 'stype': subject_type, 'sid': subject_id,
         'srole': subject_role,
         'read': permissions.get('can_read', True),
         'create': permissions.get('can_create', False),
         'edit': permissions.get('can_edit', False),
         'delete': permissions.get('can_delete', False),
         'publish': permissions.get('can_publish', False),
         'approve': permissions.get('can_approve', False),
         'deep': grant_subsections, 'manage': bool(manage_subsections),
         'level': min_role_level, 'by': created_by},
    )
    return cursor.fetchone()[0]


def delete_section_rule(cursor, rule_id):
    cursor.execute(
        'DELETE FROM wiki_section_access_rules WHERE id = %s RETURNING section_id',
        (rule_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def section_branch_department(cursor, section_id):
    """Отдел ветки, в которой лежит раздел: его собственный или ближайшего предка.

    Тем же способом фронт считает подпись «в отделе …» (branchDepartment в
    WikiSectionAccess.jsx). Здесь это нужно для ГРАНИЦЫ: супервайзер и
    руководитель настраивают доступ только внутри веток своего отдела, и
    доверять расчёту на клиенте нельзя — запрос к API он не проходит.

    None означает «раздел не в отделе» (например витрина верхнего уровня):
    такие остаются за коммерческим директором.
    """
    cursor.execute(
        """
        WITH RECURSIVE up AS (
            SELECT id, parent_section_id, department_id, 0 AS depth
              FROM wiki_sections WHERE id = %s
            UNION ALL
            SELECT s.id, s.parent_section_id, s.department_id, up.depth + 1
              FROM wiki_sections s JOIN up ON s.id = up.parent_section_id
             -- Ограничитель на случай битого дерева: сервер петель не
             -- допускает, но зациклиться здесь значит подвесить запрос.
             WHERE up.depth < 50
        )
        SELECT department_id FROM up
         WHERE department_id IS NOT NULL
         ORDER BY depth LIMIT 1
        """,
        (section_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def branch_department_map(cursor):
    """Отдел ветки для ВСЕХ активных разделов разом: {section_id: department_id}.

    То же правило, что и в section_branch_department выше — собственный отдел
    раздела или ближайшего предка, — но одним запросом вместо запроса на раздел.
    Понадобилось гостевому доступу: там граница отдела применяется не к одному
    разделу, а к списку («что я вправе открыть гостю»), и поштучный обход дерева
    стоил бы сотни запросов на открытие формы.

    Две реализации одного правила — риск, и он здесь осознанный: набор-версию
    нельзя выразить через поштучную без N запросов. Держать их в согласии обязан
    тест tests/test_wiki_guests.py — он гоняет обе по одному дереву.

    Раздел без отдела в ключах ОТСУТСТВУЕТ, а не лежит со значением None:
    «отдела нет» и «отдел неизвестен» здесь одно и то же, и оба означают отказ
    для раздающего с границей (access.may_grant_to_subject).
    """
    cursor.execute(
        """
        WITH RECURSIVE up AS (
            SELECT s.id AS root, s.parent_section_id, s.department_id, 0 AS depth
              FROM wiki_sections s
             WHERE s.status = 'active'
            UNION ALL
            SELECT up.root, p.parent_section_id, p.department_id, up.depth + 1
              FROM wiki_sections p
              JOIN up ON p.id = up.parent_section_id
             -- Вверх идём только пока отдел не найден: первая же ветка с
             -- отделом и есть ответ. Ограничитель глубины — как в
             -- section_branch_department, на случай битого дерева.
             WHERE up.department_id IS NULL AND up.depth < 50
        )
        SELECT root, department_id FROM up WHERE department_id IS NOT NULL
        """
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


# Уровень должности, с которого раздел читают, — «высота» раздела на лестнице.
#
# Считается по МИНИМАЛЬНОМУ порогу правил НА ЧТЕНИЕ: раздел, открытый одним лишь
# правилом с min_role_level = 40, читают с уровня руководителя, и раздавать в
# нём доступ супервайзер не вправе (access.may_manage_section_level — там же
# написано, почему высота меряется именно так и почему правила без порога в счёт
# не идут).
#
# ПОДРАЗДЕЛ БЕЗ СВОИХ ПОРОГОВ НАСЛЕДУЕТ ВЫСОТУ БЛИЖАЙШЕГО ПРЕДКА, у которого она
# есть. Иначе дыра открывается одним движением директора: заведи он подраздел
# внутри «Руководителя группы» — своих правил у подраздела ещё нет, высоты нет,
# и супервайзер настраивает его как свой. Собственный порог всегда сильнее
# наследства: в дереве вики «Супервайзер» лежит ВНУТРИ «Руководителя группы», и
# без этого правила супервайзер потерял бы собственный раздел.
#
# Обход вверх — тот же приём и тот же ограничитель глубины, что в
# branch_department_map: идём вверх, пока высота не найдена.
_SECTION_ROLE_LEVELS_SQL = """
WITH RECURSIVE own AS (
    SELECT r.section_id AS id, MIN(r.min_role_level) AS level
      FROM wiki_section_access_rules r
     WHERE r.can_read AND r.min_role_level IS NOT NULL
     GROUP BY r.section_id
),
up AS (
    SELECT s.id AS root, s.parent_section_id, own.level, 0 AS depth
      FROM wiki_sections s
      LEFT JOIN own ON own.id = s.id
     WHERE s.status = 'active'
    UNION ALL
    SELECT up.root, p.parent_section_id, own.level, up.depth + 1
      FROM up
      JOIN wiki_sections p ON p.id = up.parent_section_id
      LEFT JOIN own ON own.id = p.id
     WHERE up.level IS NULL AND up.depth < 50
)
SELECT root, level FROM up WHERE level IS NOT NULL
"""


def section_role_levels(cursor):
    """Высота КАЖДОГО активного раздела на лестнице должностей: {section_id: level}.

    Разом, а не по разделу: карту спрашивает и список разделов (вкладка
    «Структура» считает по ней кнопку «Кому открыт раздел» для каждой строки), и
    проверка на записи — поштучный обход дерева стоил бы запрос на строку.

    Раздела без высоты в ключах НЕТ, а не лежит со значением None: «уровня нет»
    и «уровень неизвестен» здесь одно и то же — раздел закрыт только границей
    отдела, как было до 25.08.2026.
    """
    cursor.execute(_SECTION_ROLE_LEVELS_SQL)
    return {row[0]: row[1] for row in cursor.fetchall()}


def grantable_people(cursor, *, max_role_level, department_ids=None,
                     space_department_ids=None):
    """Сотрудники, которым этот человек вправе выдать доступ.

    space_department_ids — граница ПРОСТРАНСТВА, отдельная от границы отдела
    раздающего (см. narrow_to_space). Без неё в «Таксопарках» предлагался
    двадцать один сотрудник «Тез КЦ»: правило на них ничего не открыло бы (до
    чужого пространства не пускает _SPACE_GATE_SQL), а список людей чужой
    компании показывался целиком, с именами и должностями.

    Заменяет поле «ID сотрудника»: вводить туда число можно было, только
    подсмотрев его в базе, и опечатка выдавала доступ постороннему молча —
    несуществующий id не проверялся вовсе.

    Фильтр по status, а не по is_active: в боевой базе is_active снят почти у
    всех (10 строк из 347), а работающих 174 — по is_active список оказался бы
    почти пустым.

    department_ids=None означает «без границы отдела» (коммерческий директор).
    Порог должности отсекается в SQL по той же шкале ROLE_LEVELS, что и
    правила: держать вторую копию шкалы в питоне значило бы дать ей разойтись.
    """
    depts = narrow_to_space(department_ids, space_department_ids)
    cursor.execute(
        """
        SELECT u.id, u.name, u.role, d.name AS department_name
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.status = 'working'
           AND COALESCE(
                   (%(levels)s::jsonb ->> lower(coalesce(u.role, '')))::int, 0
               ) <= %(ceiling)s
           AND (%(depts)s::int[] IS NULL OR u.department_id = ANY(%(depts)s::int[]))
         ORDER BY u.name
        """,
        {'levels': json.dumps(ROLE_LEVELS),
         'ceiling': max_role_level,
         'depts': list(depts) if depts is not None else None},
    )
    return [{'id': r[0], 'name': r[1], 'role': r[2], 'department_name': r[3]}
            for r in cursor.fetchall()]


def subject_catalog(cursor, department_ids=None, space_department_ids=None):
    """Справочники для выбора субъекта правила — одним запросом на все четыре.

    ДВЕ ГРАНИЦЫ, и они разные (см. narrow_to_space):

    department_ids — граница отдела раздающего (None = без границы). С границей
    справочник сужается до своего отдела: супервайзеру и руководителю нельзя
    адресовать правило чужому отделу, чужой группе или чужому направлению, и
    предлагать их в форме значит обещать то, что сервер отвергнет
    (access.may_grant_to_subject). Роли вики и должности в таком справочнике нет
    вовсе — они адресуют людей по всей компании, мимо любого отдела.

    space_department_ids — граница ПРОСТРАНСТВА (None = про пространство не
    спрашивали). Отвечает на другой вопрос: не «чей это человек», а «чьё это
    пространство». Без неё в «Таксопарках» предлагался отдел «Тез КЦ» — чужая
    оргструктура и правило, которое ничего не откроет (до статей чужого
    пространства не пустит _SPACE_GATE_SQL).

    Складываются, а не заменяют друг друга: у супервайзера СЗоВ остаётся его
    отдел, у супер-админа — шесть отделов пространства вместо всех семи.

    ВАЖНО: наличие space_department_ids НЕ делает раздающего «ограниченным».
    Роли вики и должности гасятся по department_ids — иначе супер-админ,
    открывший форму в конкретном пространстве, потерял бы правило на должность.
    """
    bounded = department_ids is not None
    depts = narrow_to_space(department_ids, space_department_ids)
    cursor.execute(
        """
        SELECT 'department' AS kind, id, name FROM departments
         WHERE is_active AND (%(depts)s::int[] IS NULL OR id = ANY(%(depts)s::int[]))
        UNION ALL
        SELECT 'direction', id, name FROM directions
         WHERE is_active
           AND (%(depts)s::int[] IS NULL OR department_id = ANY(%(depts)s::int[]))
        UNION ALL
        SELECT 'group', id, name FROM groups
         WHERE status = 'active'
           AND (%(depts)s::int[] IS NULL OR department_id = ANY(%(depts)s::int[]))
        UNION ALL
        SELECT 'wiki_role', id, name FROM wiki_roles WHERE NOT %(bounded)s
        ORDER BY 1, 3
        """,
        {'depts': list(depts) if depts is not None else None, 'bounded': bounded},
    )
    catalog = {'department': [], 'direction': [], 'group': [], 'wiki_role': []}
    for kind, ident, name in cursor.fetchall():
        catalog[kind].append({'id': ident, 'name': name})
    return catalog


# Откуда достаётся отдел адресата правила. Ключ — subject_type; субъекты
# 'department' и 'department_head' сюда не попадают: у них отдел это сам
# subject_id, а 'otp_role' и 'wiki_role' отдела не имеют в принципе.
_SUBJECT_DEPARTMENT_SQL = {
    'user': 'SELECT department_id FROM users WHERE id = %s',
    'group': 'SELECT department_id FROM groups WHERE id = %s',
    'direction': 'SELECT department_id FROM directions WHERE id = %s',
}


def subject_department(cursor, subject_type, subject_id):
    """Отдел адресата правила. None — отдела нет или адресат не найден.

    Отдельная функция, а не запрос в роуте: границу отдела проверяют и выдача,
    и удаление правила, и считать её двумя разными запросами — способ однажды
    разойтись.
    """
    if subject_type in ('department', 'department_head'):
        return int(subject_id) if subject_id else None
    sql = _SUBJECT_DEPARTMENT_SQL.get(subject_type)
    if not sql or not subject_id:
        return None
    cursor.execute(sql, (subject_id,))
    row = cursor.fetchone()
    return row[0] if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Журнал
# ─────────────────────────────────────────────────────────────────────────────

_AUDIT_KEYS = ('id', 'actor_id', 'actor_name', 'action', 'entity_type', 'entity_id',
               'entity_name', 'entity_slug', 'entity_alive', 'subject_name',
               'target_user_id', 'target_user_name', 'details', 'created_at')

# Группы действий для фильтра «о чём запись». Перечислением, а не префиксом:
# 'article.update' и 'article_rule.grant' начинаются одинаково, но это разные
# вещи — текст статьи против доступа к ней. Действие, не попавшее ни в одну
# группу, остаётся видно во вкладке «Все»: фильтр не должен прятать событие
# только потому, что о нём здесь забыли.
# Действие, не попавшее ни в одну группу, теряется молча: чип его не считает и
# не показывает, а сумма чипов перестаёт сходиться с «Все». На 04.09.2026 так
# терялся 341 запись из 1390 — почти четверть журнала, и больше всех
# article.migrate (247). Новое действие обязано попадать сюда же, где ему
# дают русскую подпись (src/components/wiki/auditEvents.js: ACTION_META);
# страж на это стоит в tests/test_wiki_audit_space.py.
AUDIT_GROUPS = {
    'access': ('rule.upsert', 'rule.delete',
               'article_rule.grant', 'article_rule.deny', 'article_rule.delete',
               'article.strict_bypass',
               'guest.grant', 'guest.extend', 'guest.revoke'),
    'structure': ('space.create', 'space.update', 'space.archive',
                  'section.create', 'section.update', 'section.archive',
                  'section.move'),
    'articles': ('article.create', 'article.update', 'article.archive',
                 'article.restore', 'article.adopt', 'article.fork',
                 'article.import', 'article.ai_draft', 'article.ai_update',
                 'article.ai_edit',
                 'article.migrate', 'article.migrate_review',
                 'article.yandex_preview', 'article.yandex_import',
                 'article.yandex_link', 'article.yandex_unlink',
                 'article.yandex_sync'),
    'places': ('park.create', 'park.update', 'park.archive', 'park.logo_upload',
               'promotion.create', 'promotion.update', 'promotion.archive',
               'office.create', 'office.update', 'office.archive',
               'office.day.set', 'office.day.clear',
               'office.closure.set', 'office.closure.clear'),
    'ack': ('ack.assign', 'ack.confirm'),
}

# Название объекта берём из самой таблицы объекта, а не из details: details
# пишется в момент действия, у половины действий имени там нет вовсе, а у
# остальных лежит имя на тот момент. Читателю журнала нужно понять, О ЧЁМ
# запись, сегодня — поэтому имя актуальное, а факт «объекта больше нет»
# показываем отдельным признаком, а не пустой строкой.
_AUDIT_FROM = """
      FROM wiki_audit_log a
      LEFT JOIN users actor  ON actor.id  = a.actor_id
      LEFT JOIN users target ON target.id = a.target_user_id
      LEFT JOIN wiki_articles   art ON a.entity_type = 'article'   AND art.id = a.entity_id
      LEFT JOIN wiki_sections   sec ON a.entity_type = 'section'   AND sec.id = a.entity_id
      LEFT JOIN wiki_spaces     spc ON a.entity_type = 'space'     AND spc.id = a.entity_id
      LEFT JOIN wiki_taxi_parks prk ON a.entity_type = 'park'      AND prk.id = a.entity_id
      LEFT JOIN wiki_offices    off ON a.entity_type = 'office'    AND off.id = a.entity_id
      LEFT JOIN wiki_promotions pro ON a.entity_type = 'promotion' AND pro.id = a.entity_id
"""

_AUDIT_ENTITY_NAME = (
    "COALESCE(art.title, sec.name, spc.name, prk.name, off.name, pro.title)")

_AUDIT_ENTITY_ALIVE = (
    "(art.id IS NOT NULL OR sec.id IS NOT NULL OR spc.id IS NOT NULL"
    " OR prk.id IS NOT NULL OR off.id IS NOT NULL OR pro.id IS NOT NULL)")

# Кому выдали или у кого отобрали право. Субъект лежит в details парой
# (subject_type, subject_id) — без имени запись читается как «выдано право
# субъекту 17», то есть никак. Разворачиваем той же CASE-таблицей, что и
# список правил статьи (edit.list_article_rules).
_AUDIT_SUBJECT_ID = (
    "(CASE WHEN a.details->>'subject_id' ~ '^[0-9]+$'"
    " THEN (a.details->>'subject_id')::int END)")

_AUDIT_SUBJECT_NAME = """
    CASE a.details->>'subject_type'
        WHEN 'department'      THEN (SELECT name FROM departments WHERE id = {sid})
        WHEN 'department_head' THEN (SELECT name FROM departments WHERE id = {sid})
        WHEN 'direction'       THEN (SELECT name FROM directions  WHERE id = {sid})
        WHEN 'group'           THEN (SELECT name FROM groups      WHERE id = {sid})
        WHEN 'wiki_role'       THEN (SELECT name FROM wiki_roles  WHERE id = {sid})
        WHEN 'user'            THEN (SELECT name FROM users       WHERE id = {sid})
    END
""".format(sid=_AUDIT_SUBJECT_ID)


def _audit_filters(group=None, query=None, date_from=None, date_to=None,
                   space_id=None):
    """Условия WHERE и параметры к ним.

    Один код на выборку, счётчик и раскладку по группам: разъехавшись, они
    дают «показано 20 из 3» и чипы с чужими числами.

    space_id — граница пространства, и она же первая: журнал у «Таксопарков» и
    «Теза» свой, а не общий на двоих.

    ГРАНИЦА СТРОГАЯ. До 04.09.2026 здесь стояло «a.space_id = %s OR a.space_id
    IS NULL»: запись без пространства показывалась в ЛЮБОМ журнале, чтобы не
    вычеркнуть из аудита то, у чего объекта нет. Замысел был честный, а вышло
    ровно то, от чего лечились: у «Теза» на 99 своих записей приходилось 46
    ничьих, то есть треть ленты приезжала из чужой вики. Владелец это и назвал
    «журналы смешаны».

    Поэтому ничьих записей теперь не должно быть вовсе — их закрыли с двух
    сторон: на записи пространство называют сами роуты
    (routes_structure.log_space), а уже записанное разбирает миграция
    (schema._restore_audit_space_by_session). А чтобы строгая граница ничего не
    прятала МОЛЧА, остаток считается отдельно (count_audit_outside) и виден в
    подвале журнала.
    """
    where, params = [], []

    if space_id:
        where.append('a.space_id = %s')
        params.append(int(space_id))

    actions = AUDIT_GROUPS.get(group)
    if actions:
        where.append('a.action = ANY(%s)')
        params.append(list(actions))
    if date_from:
        where.append('a.created_at >= %s::date')
        params.append(date_from)
    if date_to:
        # Верхняя граница включительно по дню: человек выбирает дату, а не
        # момент, и «по 18 августа» обязано захватывать весь день.
        where.append("a.created_at < %s::date + INTERVAL '1 day'")
        params.append(date_to)
    if query:
        # Ищем по человеку, названию объекта, субъекту права и содержимому
        # details: журнал читают вопросом «что было с этой статьёй» и «что
        # выдали этой группе», а не «покажи мне action».
        where.append(
            '(actor.name ILIKE %s OR target.name ILIKE %s'
            ' OR ' + _AUDIT_ENTITY_NAME + ' ILIKE %s'
            ' OR (' + _AUDIT_SUBJECT_NAME.strip() + ') ILIKE %s'
            ' OR a.details::text ILIKE %s)')
        params.extend(['%%%s%%' % query] * 5)

    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    return clause, params


def count_audit(cursor, **filters):
    """Сколько записей под фильтром — чтобы «Показать ещё» знала, когда
    закончиться, а подпись могла назвать общее число."""
    clause, params = _audit_filters(**filters)
    cursor.execute('SELECT count(*) ' + _AUDIT_FROM + clause, params)
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def count_audit_outside(cursor, **filters):
    """Сколько записей под этим фильтром не отнесено ни к одному пространству.

    Страховка строгой границы. Граница отсекает чужое — и она же отсекла бы
    запись, у которой пространства не оказалось совсем: та не попала бы НИКУДА,
    и аудит потерял бы её беззвучно. Пересчитывать её тут дешевле, чем однажды
    искать пропажу: пока число ноль, в интерфейсе ничего не появляется, а если
    оно не ноль — в подвале журнала стоит строка, и есть с чего начать разбор.

    Фильтр тот же, что у выборки, но БЕЗ пространства: вопрос ровно в том,
    сколько записей остались вне пространств, а не вне текущего.
    """
    filters.pop('space_id', None)
    clause, params = _audit_filters(**filters)
    clause = (clause + ' AND ') if clause else 'WHERE '
    cursor.execute('SELECT count(*) ' + _AUDIT_FROM + clause
                   + 'a.space_id IS NULL', params)
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def audit_group_counts(cursor, **filters):
    """Число записей в каждой группе при текущем поиске — для чипов фильтра.

    Пустой чип рисовать незачем, а чтобы понять, что он пуст, число нужно
    заранее. Считаем одним запросом по всем действиям сразу, а группу из
    фильтра игнорируем: чипы должны показывать, что найдётся при переключении,
    а не сколько уже выбрано.
    """
    filters.pop('group', None)
    clause, params = _audit_filters(**filters)
    cursor.execute('SELECT a.action, count(*) ' + _AUDIT_FROM + clause
                   + ' GROUP BY a.action', params)
    by_action = {row[0]: int(row[1]) for row in cursor.fetchall()}
    counts = {'all': sum(by_action.values())}
    for name, actions in AUDIT_GROUPS.items():
        counts[name] = sum(by_action.get(action, 0) for action in actions)
    return counts


def list_audit(cursor, limit=100, offset=0, **filters):
    """Чтение журнала. В оригинале обе таблицы аудита писались, но не читались
    ни API, ни интерфейсом — то есть аудита фактически не существовало.

    Время отдаём строкой ISO без зоны. В базе оно уже местное (Asia/Almaty,
    см. schema._NOW), а Flask сериализует datetime как «… GMT» — браузер
    честно прибавлял к местному времени ещё пять часов, и журнал показывал
    события на пять часов вперёд. Остальной портал отдаёт даты isoformat().

    ip_address намеренно не отдаём: на проде во всех записях лежит внутренний
    адрес прокси Render (10.x), к человеку он отношения не имеет и в интерфейсе
    был бы ложным следом.
    """
    clause, params = _audit_filters(**filters)
    cursor.execute(
        """
        SELECT a.id, a.actor_id, actor.name, a.action, a.entity_type, a.entity_id,
               """ + _AUDIT_ENTITY_NAME + """,
               art.slug,
               """ + _AUDIT_ENTITY_ALIVE + """,
               (""" + _AUDIT_SUBJECT_NAME.strip() + """),
               a.target_user_id, target.name, a.details, a.created_at
        """ + _AUDIT_FROM + clause + """
         ORDER BY a.id DESC
         LIMIT %s OFFSET %s
        """,
        params + [limit, offset],
    )
    items = []
    for row in cursor.fetchall():
        item = dict(zip(_AUDIT_KEYS, row))
        created = item.get('created_at')
        if hasattr(created, 'isoformat'):
            item['created_at'] = created.isoformat()
        items.append(item)
    return items
