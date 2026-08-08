"""SQL-слой раздела «Вики».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и ничего не знают
про пул и транзакции — их открывает вызывающий, как это делает call_qa.

Почему один запрос вместо нескольких. Пул проекта — MIN_CONN=16 / MAX_CONN=40
(database.py), и эти же слоты делит SSE-фанаут аукциона смен; пул-старвейшн
в проекте уже случался. В оригинальной вики матрица доступа делала по два
запроса НА КАЖДУЮ должность, а просмотр статьи стоил двух отдельных запросов
без транзакции. Здесь контекст доступа собирается одним CTE.
"""

# Один запрос вместо пяти: профиль, возглавляемые отделы, активные группы
# (и как оператор, и как супервайзер), роли вики, режим доступа.
_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT id, role, department_id, direction_id
      FROM users
     WHERE id = %(user_id)s
),
headed AS (
    SELECT d.id
      FROM departments d
     WHERE d.head_user_id = %(user_id)s
       AND d.is_active
),
my_groups AS (
    SELECT gom.group_id
      FROM group_operator_memberships gom
      JOIN groups g ON g.id = gom.group_id AND g.status = 'active'
     WHERE gom.operator_id = %(user_id)s
       AND gom.start_date <= CURRENT_DATE
       AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
    UNION
    SELECT gsm.group_id
      FROM group_supervisor_memberships gsm
      JOIN groups g ON g.id = gsm.group_id AND g.status = 'active'
     WHERE gsm.supervisor_id = %(user_id)s
       AND gsm.start_date <= CURRENT_DATE
       AND (gsm.end_date IS NULL OR gsm.end_date >= CURRENT_DATE)
),
my_wiki_roles AS (
    SELECT r.id, r.code, r.can_read, r.can_create, r.can_edit, r.can_delete,
           r.can_publish, r.can_approve, r.can_manage_users,
           r.can_manage_structure, r.can_manage_access
      FROM wiki_roles r
      JOIN wiki_user_roles ur ON ur.wiki_role_id = r.id
     WHERE ur.user_id = %(user_id)s
)
SELECT
    (SELECT role          FROM me)                                   AS otp_role,
    (SELECT department_id FROM me)                                   AS department_id,
    (SELECT direction_id  FROM me)                                   AS direction_id,
    COALESCE((SELECT array_agg(id)       FROM headed),     '{}')     AS headed_department_ids,
    COALESCE((SELECT array_agg(group_id) FROM my_groups),  '{}')     AS group_ids,
    COALESCE((SELECT json_agg(row_to_json(my_wiki_roles)) FROM my_wiki_roles), '[]') AS wiki_roles,
    COALESCE((SELECT access_mode FROM wiki_user_access_settings
               WHERE user_id = %(user_id)s), 'auto')                 AS access_mode
"""


def load_access_context(cursor, user_id):
    """Всё, что нужно для вычисления прав, одним обращением к базе.

    Возвращает словарь; wiki_roles — список словарей с флагами способностей,
    пригодный для wiki.access.resolve_capabilities.
    """
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': user_id})
    row = cursor.fetchone()
    if not row:
        return None

    otp_role, department_id, direction_id, headed, groups, wiki_roles, access_mode = row
    return {
        'user_id': int(user_id),
        'otp_role': otp_role,
        'department_id': department_id,
        'direction_id': direction_id,
        'headed_department_ids': list(headed or []),
        'group_ids': list(groups or []),
        'wiki_roles': list(wiki_roles or []),
        'access_mode': access_mode or 'auto',
    }


# ─────────────────────────────────────────────────────────────────────────────
# Периметр доступа
# ─────────────────────────────────────────────────────────────────────────────

# Разделы, доступные на чтение в АВТОМАТИЧЕСКОМ режиме.
#
# Один рекурсивный CTE вместо двух расходившихся вычислителей оригинала
# (getRuleAllowedSectionIds и getUserAllowedSections считали доступ по-разному,
# из-за чего дерево навигации и список статей показывали разное).
#
# Порядок: правила по субъектам → потомки тех правил, где разрешено поддерево →
# публичные разделы → собственные разделы → действующий гостевой доступ.
_AUTO_SECTIONS_SQL = """
WITH RECURSIVE rule_hits AS (
    SELECT r.section_id, bool_or(r.grant_subsections) AS deep
      FROM wiki_section_access_rules r
      JOIN wiki_sections s ON s.id = r.section_id AND s.status = 'active'
     WHERE r.can_read
       AND (
            (r.subject_type = 'department' AND r.subject_id   = ANY(%(departments)s))
         OR (r.subject_type = 'direction'  AND r.subject_id   = ANY(%(directions)s))
         OR (r.subject_type = 'group'      AND r.subject_id   = ANY(%(groups)s))
         OR (r.subject_type = 'otp_role'   AND r.subject_role = ANY(%(roles)s))
         OR (r.subject_type = 'wiki_role'  AND r.subject_id   = ANY(%(wiki_roles)s))
         OR (r.subject_type = 'user'       AND r.subject_id   = %(user_id)s)
       )
     GROUP BY r.section_id
),
subtree AS (
    SELECT section_id AS id FROM rule_hits WHERE deep
    UNION
    SELECT child.id
      FROM wiki_sections child
      JOIN subtree parent ON child.parent_section_id = parent.id
     WHERE child.status = 'active'
)
SELECT id FROM subtree
UNION
SELECT section_id FROM rule_hits
UNION
SELECT id FROM wiki_sections WHERE status = 'active' AND visibility_scope = 'public'
UNION
SELECT id FROM wiki_sections WHERE status = 'active' AND owner_user_id = %(user_id)s
UNION
SELECT g.section_id
  FROM wiki_guest_access g
 WHERE g.user_id = %(user_id)s
   AND g.section_id IS NOT NULL
   AND g.revoked_at IS NULL
   AND g.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
"""

# Ручной режим: человек видит ровно то, что ему выдали, плюс публичное.
# Выдача отдела раскрывается во все разделы пространств этого отдела.
_MANUAL_SECTIONS_SQL = """
WITH RECURSIVE seed AS (
    SELECT s.id
      FROM wiki_sections s
      JOIN wiki_user_manual_access m ON m.section_id = s.id
     WHERE m.user_id = %(user_id)s AND s.status = 'active'
    UNION
    SELECT s.id
      FROM wiki_sections s
      JOIN wiki_spaces sp ON sp.id = s.space_id
      JOIN wiki_user_manual_access m ON m.department_id = sp.department_id
     WHERE m.user_id = %(user_id)s AND s.status = 'active'
),
subtree AS (
    SELECT id FROM seed
    UNION
    SELECT child.id
      FROM wiki_sections child
      JOIN subtree parent ON child.parent_section_id = parent.id
     WHERE child.status = 'active'
)
SELECT id FROM subtree
UNION
SELECT id FROM wiki_sections WHERE status = 'active' AND visibility_scope = 'public'
UNION
SELECT id FROM wiki_sections WHERE status = 'active' AND owner_user_id = %(user_id)s
UNION
SELECT g.section_id
  FROM wiki_guest_access g
 WHERE g.user_id = %(user_id)s
   AND g.section_id IS NOT NULL
   AND g.revoked_at IS NULL
   AND g.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
"""


def allowed_section_ids(cursor, ctx, subjects):
    """Идентификаторы разделов, доступных пользователю на чтение.

    Администратор доступов видит все активные разделы — короткое замыкание.
    ВАЖНО: проверка админа стоит ДО раннего выхода по пустому периметру.
    В оригинале порядок был обратный, и администратор вики не мог создать
    статью, пока ему не выдали хотя бы один раздел правилом.
    """
    if ctx['capabilities'].get('can_manage_access'):
        cursor.execute("SELECT id FROM wiki_sections WHERE status = 'active'")
        return {row[0] for row in cursor.fetchall()}

    params = {
        'user_id': ctx['user_id'],
        'departments': subjects['department'] or [-1],
        'directions': subjects['direction'] or [-1],
        'groups': subjects['group'] or [-1],
        'roles': subjects['otp_role'] or [''],
        'wiki_roles': subjects['wiki_role'] or [-1],
    }
    sql = _MANUAL_SECTIONS_SQL if ctx.get('access_mode') == 'manual' else _AUTO_SECTIONS_SQL
    cursor.execute(sql, params)
    return {row[0] for row in cursor.fetchall()}


def section_rules_for_user(cursor, section_ids, subjects, user_id):
    """Правила разделов, действующие на пользователя, — для расчёта прав записи.

    Возвращает {section_id: [правило, ...]}. Одним запросом на все разделы:
    в оригинале матрица доступа делала по два запроса на каждую должность.
    """
    if not section_ids:
        return {}
    cursor.execute(
        """
        SELECT r.section_id, r.can_read, r.can_create, r.can_edit,
               r.can_delete, r.can_publish, r.can_approve
          FROM wiki_section_access_rules r
         WHERE r.section_id = ANY(%(sections)s)
           AND (
                (r.subject_type = 'department' AND r.subject_id   = ANY(%(departments)s))
             OR (r.subject_type = 'direction'  AND r.subject_id   = ANY(%(directions)s))
             OR (r.subject_type = 'group'      AND r.subject_id   = ANY(%(groups)s))
             OR (r.subject_type = 'otp_role'   AND r.subject_role = ANY(%(roles)s))
             OR (r.subject_type = 'wiki_role'  AND r.subject_id   = ANY(%(wiki_roles)s))
             OR (r.subject_type = 'user'       AND r.subject_id   = %(user_id)s)
           )
        """,
        {
            'sections': list(section_ids),
            'user_id': user_id,
            'departments': subjects['department'] or [-1],
            'directions': subjects['direction'] or [-1],
            'groups': subjects['group'] or [-1],
            'roles': subjects['otp_role'] or [''],
            'wiki_roles': subjects['wiki_role'] or [-1],
        },
    )
    keys = ('can_read', 'can_create', 'can_edit', 'can_delete', 'can_publish', 'can_approve')
    result = {}
    for row in cursor.fetchall():
        result.setdefault(row[0], []).append(dict(zip(keys, row[1:])))
    return result


def log_action(cursor, *, actor_id, action, entity_type=None, entity_id=None,
               target_user_id=None, details=None, ip_address=None):
    """Запись в журнал раздела.

    В оригинале журналов было два (security_audit_logs и access_audit_logs),
    почти одинаковых, и ни один не читался ни API, ни интерфейсом. Здесь один,
    и к нему сразу будет эндпоинт чтения.
    """
    import json

    cursor.execute(
        """
        INSERT INTO wiki_audit_log (actor_id, action, entity_type, entity_id,
                                    target_user_id, details, ip_address)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        """,
        (actor_id, action, entity_type, entity_id, target_user_id,
         json.dumps(details or {}, ensure_ascii=False), ip_address),
    )


def schema_is_ready(cursor):
    """Созданы ли таблицы раздела. Нужно эндпоинту /api/wiki/ping, чтобы
    отличить «раздел не разворачивался» от «раздел сломан».

    Намеренно не падает ни при каких обстоятельствах: это диагностика, и
    эндпоинт, который сам отдаёт 500, бесполезен ровно тогда, когда он нужен.
    """
    try:
        cursor.execute("SELECT to_regclass('public.wiki_articles') IS NOT NULL")
        row = cursor.fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def counters(cursor):
    """Счётчики для диагностики раздела."""
    cursor.execute(
        """
        SELECT (SELECT count(*) FROM wiki_spaces   WHERE status = 'active'),
               (SELECT count(*) FROM wiki_sections WHERE status = 'active'),
               (SELECT count(*) FROM wiki_articles WHERE status = 'published'),
               (SELECT count(*) FROM wiki_articles),
               (SELECT count(*) FROM wiki_roles)
        """
    )
    row = cursor.fetchone() or (0, 0, 0, 0, 0)
    spaces, sections, published, articles, roles = row
    return {
        'spaces': spaces,
        'sections': sections,
        'articles_published': published,
        'articles_total': articles,
        'roles': roles,
    }
