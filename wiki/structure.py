"""SQL структуры раздела «Вики»: пространства, разделы, правила доступа, журнал.

Вынесено из queries.py, чтобы тот остался про периметр доступа, а этот — про
управление содержимым структуры. Все функции принимают готовый курсор.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Пространства
# ─────────────────────────────────────────────────────────────────────────────

_SPACE_KEYS = ('id', 'code', 'name', 'description', 'icon', 'department_id',
               'department_name', 'status', 'position', 'sections_count')


def list_spaces(cursor, include_archived=False):
    cursor.execute(
        """
        SELECT sp.id, sp.code, sp.name, sp.description, sp.icon, sp.department_id,
               d.name AS department_name, sp.status, sp.position,
               (SELECT count(*) FROM wiki_sections s
                 WHERE s.space_id = sp.id AND s.status = 'active') AS sections_count
          FROM wiki_spaces sp
          LEFT JOIN departments d ON d.id = sp.department_id
         WHERE (%s OR sp.status = 'active')
         ORDER BY sp.position, sp.id
        """,
        (include_archived,),
    )
    return [dict(zip(_SPACE_KEYS, row)) for row in cursor.fetchall()]


def create_space(cursor, *, name, code, description, icon, department_id, created_by):
    cursor.execute(
        """
        INSERT INTO wiki_spaces (code, name, description, icon, department_id, position, created_by)
        VALUES (%s, %s, %s, %s, %s,
                COALESCE((SELECT max(position) + 1 FROM wiki_spaces), 0), %s)
        RETURNING id
        """,
        (code or None, name, description, icon, department_id, created_by),
    )
    return cursor.fetchone()[0]


_SPACE_UPDATABLE = ('name', 'description', 'icon', 'department_id', 'status', 'position', 'code')


def update_space(cursor, space_id, fields):
    """Частичное обновление. Пустой набор полей — в базу не ходим."""
    sets, values = [], []
    for key in _SPACE_UPDATABLE:
        if key in fields:
            sets.append(key + ' = %s')
            values.append(fields[key])
    if not sets:
        return False
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    values.append(space_id)
    cursor.execute('UPDATE wiki_spaces SET ' + ', '.join(sets) + ' WHERE id = %s', values)
    return cursor.rowcount > 0


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


# ─────────────────────────────────────────────────────────────────────────────
# Правила доступа к разделам
# ─────────────────────────────────────────────────────────────────────────────

_RULE_KEYS = ('id', 'section_id', 'section_name', 'subject_type', 'subject_id',
              'subject_role', 'can_read', 'can_create', 'can_edit', 'can_delete',
              'can_publish', 'can_approve', 'grant_subsections', 'min_role_level',
              'subject_label')


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
               r.can_publish, r.can_approve, r.grant_subsections, r.min_role_level,
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
                        min_role_level=None):
    """Создать или обновить правило. Уникальность — по паре (раздел, субъект)."""
    cursor.execute(
        """
        INSERT INTO wiki_section_access_rules
            (section_id, subject_type, subject_id, subject_role,
             can_read, can_create, can_edit, can_delete, can_publish, can_approve,
             grant_subsections, min_role_level, created_by)
        VALUES (%(section)s, %(stype)s, %(sid)s, %(srole)s,
                %(read)s, %(create)s, %(edit)s, %(delete)s, %(publish)s, %(approve)s,
                %(deep)s, %(level)s, %(by)s)
        ON CONFLICT (section_id, subject_type,
                     COALESCE(subject_id, -1), COALESCE(subject_role, ''),
                     COALESCE(min_role_level, -1))
        DO UPDATE SET can_read          = EXCLUDED.can_read,
                      can_create        = EXCLUDED.can_create,
                      can_edit          = EXCLUDED.can_edit,
                      can_delete        = EXCLUDED.can_delete,
                      can_publish       = EXCLUDED.can_publish,
                      can_approve       = EXCLUDED.can_approve,
                      grant_subsections = EXCLUDED.grant_subsections,
                      min_role_level    = EXCLUDED.min_role_level,
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
         'deep': grant_subsections, 'level': min_role_level, 'by': created_by},
    )
    return cursor.fetchone()[0]


def delete_section_rule(cursor, rule_id):
    cursor.execute(
        'DELETE FROM wiki_section_access_rules WHERE id = %s RETURNING section_id',
        (rule_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def subject_catalog(cursor):
    """Справочники для выбора субъекта правила — одним запросом на все четыре."""
    cursor.execute(
        """
        SELECT 'department' AS kind, id, name FROM departments WHERE is_active
        UNION ALL
        SELECT 'direction', id, name FROM directions WHERE is_active
        UNION ALL
        SELECT 'group', id, name FROM groups WHERE status = 'active'
        UNION ALL
        SELECT 'wiki_role', id, name FROM wiki_roles
        ORDER BY 1, 3
        """
    )
    catalog = {'department': [], 'direction': [], 'group': [], 'wiki_role': []}
    for kind, ident, name in cursor.fetchall():
        catalog[kind].append({'id': ident, 'name': name})
    return catalog


# ─────────────────────────────────────────────────────────────────────────────
# Журнал
# ─────────────────────────────────────────────────────────────────────────────

_AUDIT_KEYS = ('id', 'actor_id', 'actor_name', 'action', 'entity_type',
               'entity_id', 'target_user_id', 'target_user_name', 'details', 'created_at')


def list_audit(cursor, limit=100, offset=0):
    """Чтение журнала. В оригинале обе таблицы аудита писались, но не читались
    ни API, ни интерфейсом — то есть аудита фактически не существовало."""
    cursor.execute(
        """
        SELECT a.id, a.actor_id, actor.name, a.action, a.entity_type, a.entity_id,
               a.target_user_id, target.name, a.details, a.created_at
          FROM wiki_audit_log a
          LEFT JOIN users actor  ON actor.id  = a.actor_id
          LEFT JOIN users target ON target.id = a.target_user_id
         ORDER BY a.id DESC
         LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )
    return [dict(zip(_AUDIT_KEYS, row)) for row in cursor.fetchall()]
