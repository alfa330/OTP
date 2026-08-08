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
    отличить «раздел не разворачивался» от «раздел сломан»."""
    cursor.execute("SELECT to_regclass('public.wiki_articles') IS NOT NULL")
    return bool(cursor.fetchone()[0])


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
    spaces, sections, published, articles, roles = cursor.fetchone()
    return {
        'spaces': spaces,
        'sections': sections,
        'articles_published': published,
        'articles_total': articles,
        'roles': roles,
    }
