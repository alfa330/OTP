"""SQL статей раздела «Вики».

Главное здесь — visible_article_ids: ЕДИНСТВЕННЫЙ источник правды о том, что
человек вправе прочитать. Через него обязаны проходить все читающие пути:
список, дерево, избранное, история, обратные ссылки, рейтинги, подсказки поиска.
Если хоть один путь фильтрует доступ по-своему, закрытая статья утечёт — пусть
не телом, так заголовком в «популярном».

Порядок разрешения (совпадает с wiki.access.resolve_article_permissions):
  1. visibility_mode='inherit'   — права от разделов статьи;
     visibility_mode='restricted'— ТОЛЬКО правила самой статьи;
  2. mode='deny' сильнее любого grant;
  3. can_manage_access обходит deny, но НЕ обходит strict_mode;
  4. strict_mode обходит только super_admin, и это пишется в журнал.
"""

# Подстановка субъектов повторяется в нескольких запросах — держим одной строкой,
# чтобы условие нигде не разъехалось.
_SUBJECT_MATCH = """
        (r.subject_type = 'department' AND r.subject_id   = ANY(%(departments)s))
     OR (r.subject_type = 'direction'  AND r.subject_id   = ANY(%(directions)s))
     OR (r.subject_type = 'group'      AND r.subject_id   = ANY(%(groups)s))
     OR (r.subject_type = 'otp_role'   AND r.subject_role = ANY(%(roles)s))
     OR (r.subject_type = 'wiki_role'  AND r.subject_id   = ANY(%(wiki_roles)s))
     OR (r.subject_type = 'user'       AND r.subject_id   = %(user_id)s)
"""

_VISIBLE_ARTICLES_SQL = """
WITH my_rules AS (
    SELECT r.article_id, r.mode, r.can_read
      FROM wiki_article_access_rules r
     WHERE (""" + _SUBJECT_MATCH + """)
),
denied AS (
    SELECT article_id FROM my_rules WHERE mode = 'deny' AND can_read
),
granted AS (
    SELECT article_id FROM my_rules WHERE mode = 'grant' AND can_read
),
inherited AS (
    SELECT DISTINCT a.id
      FROM wiki_articles a
      JOIN wiki_article_sections s ON s.article_id = a.id
     WHERE a.visibility_mode = 'inherit'
       AND s.section_id = ANY(%(sections)s)
)
SELECT a.id
  FROM wiki_articles a
 WHERE
   -- статус: черновики видит автор, владелец и те, кому вообще позволено править
   (a.status = 'published'
    OR a.author_id = %(user_id)s
    OR a.owner_user_id = %(user_id)s
    OR %(can_see_drafts)s)
   -- архив показываем только управляющим структурой
   AND (a.status <> 'archived' OR %(can_see_archived)s)
   AND (
        -- администратор доступов видит всё, кроме статей в строгом режиме
        %(is_wiki_admin)s
        OR a.id IN (SELECT id FROM inherited)
        OR a.id IN (SELECT article_id FROM granted)
        OR a.author_id = %(user_id)s
        OR a.owner_user_id = %(user_id)s
        OR EXISTS (SELECT 1 FROM wiki_guest_access g
                    WHERE g.article_id = a.id
                      AND g.user_id = %(user_id)s
                      AND g.revoked_at IS NULL
                      AND g.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
   )
   -- запрет сильнее разрешения; администратор доступов его перекрывает
   AND (%(is_wiki_admin)s OR a.id NOT IN (SELECT article_id FROM denied))
   -- строгий режим: нужен явный грант, обходит только super_admin
   AND (NOT a.strict_mode
        OR %(is_super_admin)s
        OR a.id IN (SELECT article_id FROM granted)
        OR a.author_id = %(user_id)s)
"""


def _subject_params(ctx, subjects, sections):
    caps = ctx['capabilities']
    role = str(ctx.get('otp_role') or '').strip().lower()
    return {
        'user_id': ctx['user_id'],
        'sections': list(sections) or [-1],
        'departments': subjects['department'] or [-1],
        'directions': subjects['direction'] or [-1],
        'groups': subjects['group'] or [-1],
        'roles': subjects['otp_role'] or [''],
        'wiki_roles': subjects['wiki_role'] or [-1],
        'is_wiki_admin': bool(caps.get('can_manage_access')),
        'is_super_admin': role in ('super_admin', 'superadmin'),
        'can_see_drafts': bool(caps.get('can_edit') or caps.get('can_publish')
                               or caps.get('can_manage_access')),
        'can_see_archived': bool(caps.get('can_manage_structure')
                                 or caps.get('can_manage_access')),
    }


def visible_article_ids(cursor, ctx, subjects, allowed_sections):
    """Множество статей, которые пользователь вправе прочитать."""
    cursor.execute(_VISIBLE_ARTICLES_SQL, _subject_params(ctx, subjects, allowed_sections))
    return {row[0] for row in cursor.fetchall()}


def article_rules_for_user(cursor, article_ids, subjects, user_id):
    """Правила статей, действующие на пользователя, — для расчёта прав записи."""
    if not article_ids:
        return {}
    cursor.execute(
        """
        SELECT r.article_id, r.mode, r.can_read, r.can_create, r.can_edit,
               r.can_delete, r.can_publish, r.can_approve
          FROM wiki_article_access_rules r
         WHERE r.article_id = ANY(%(articles)s) AND (""" + _SUBJECT_MATCH + """)
        """,
        {
            'articles': list(article_ids),
            'user_id': user_id,
            'departments': subjects['department'] or [-1],
            'directions': subjects['direction'] or [-1],
            'groups': subjects['group'] or [-1],
            'roles': subjects['otp_role'] or [''],
            'wiki_roles': subjects['wiki_role'] or [-1],
        },
    )
    keys = ('mode', 'can_read', 'can_create', 'can_edit',
            'can_delete', 'can_publish', 'can_approve')
    result = {}
    for row in cursor.fetchall():
        result.setdefault(row[0], []).append(dict(zip(keys, row[1:])))
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Чтение
# ─────────────────────────────────────────────────────────────────────────────

# Тело статьи в списке НЕ выбираем: в проде вики три статьи весят по 200-900 КБ
# из-за картинок в base64, и одна выдача списка тянула бы мегабайты.
_LIST_KEYS = ('id', 'slug', 'title', 'summary', 'article_type', 'status',
              'visibility_mode', 'strict_mode', 'views', 'author_id', 'author_name',
              'updated_at', 'published_at', 'section_ids', 'tags')

_LIST_SQL = """
SELECT a.id, a.slug, a.title, a.summary, a.article_type, a.status,
       a.visibility_mode, a.strict_mode, a.views, a.author_id, u.name,
       a.updated_at, a.published_at,
       COALESCE((SELECT array_agg(s.section_id) FROM wiki_article_sections s
                  WHERE s.article_id = a.id), '{}') AS section_ids,
       COALESCE((SELECT array_agg(t.tag_name) FROM wiki_article_tags t
                  WHERE t.article_id = a.id), '{}') AS tags
  FROM wiki_articles a
  LEFT JOIN users u ON u.id = a.author_id
 WHERE a.id = ANY(%(ids)s)
   AND (%(section)s::int IS NULL
        OR EXISTS (SELECT 1 FROM wiki_article_sections s
                    WHERE s.article_id = a.id AND s.section_id = %(section)s::int))
   AND (%(status)s::text IS NULL OR a.status = %(status)s::text)
   AND (%(query)s::text IS NULL
        OR a.title ILIKE '%%' || %(query)s::text || '%%'
        OR a.summary ILIKE '%%' || %(query)s::text || '%%')
 ORDER BY a.position, a.updated_at DESC
 LIMIT %(limit)s OFFSET %(offset)s
"""


def list_articles(cursor, visible_ids, *, section_id=None, status=None,
                  query=None, limit=50, offset=0):
    if not visible_ids:
        return []
    cursor.execute(_LIST_SQL, {
        'ids': list(visible_ids), 'section': section_id, 'status': status,
        'query': query or None, 'limit': limit, 'offset': offset,
    })
    rows = []
    for row in cursor.fetchall():
        item = dict(zip(_LIST_KEYS, row))
        item['section_ids'] = list(item['section_ids'] or [])
        item['tags'] = list(item['tags'] or [])
        rows.append(item)
    return rows


_ARTICLE_KEYS = ('id', 'slug', 'title', 'summary', 'content', 'article_type', 'status',
                 'visibility_mode', 'strict_mode', 'toc', 'views', 'author_id',
                 'author_name', 'owner_user_id', 'updated_by', 'updated_at',
                 'created_at', 'published_at', 'review_due_at', 'section_ids', 'tags')


def get_article(cursor, *, article_id=None, slug=None):
    cursor.execute(
        """
        SELECT a.id, a.slug, a.title, a.summary, a.content, a.article_type, a.status,
               a.visibility_mode, a.strict_mode, a.toc, a.views, a.author_id, u.name,
               a.owner_user_id, a.updated_by, a.updated_at, a.created_at,
               a.published_at, a.review_due_at,
               COALESCE((SELECT array_agg(s.section_id) FROM wiki_article_sections s
                          WHERE s.article_id = a.id), '{}'),
               COALESCE((SELECT array_agg(t.tag_name) FROM wiki_article_tags t
                          WHERE t.article_id = a.id), '{}')
          FROM wiki_articles a
          LEFT JOIN users u ON u.id = a.author_id
         WHERE (%(id)s::int IS NULL OR a.id = %(id)s::int)
           AND (%(slug)s::text IS NULL OR a.slug = %(slug)s::text)
         LIMIT 1
        """,
        {'id': article_id, 'slug': slug},
    )
    row = cursor.fetchone()
    if not row:
        return None
    item = dict(zip(_ARTICLE_KEYS, row))
    item['section_ids'] = list(item['section_ids'] or [])
    item['tags'] = list(item['tags'] or [])
    return item


def register_view(cursor, article_id, user_id, ip_address):
    """Инкремент счётчика и запись в лог — ОДНИМ запросом.

    В оригинале это были два отдельных запроса без транзакции, и счётчик
    articles.views расходился с article_views_log.
    """
    cursor.execute(
        """
        WITH bump AS (
            UPDATE wiki_articles SET views = views + 1 WHERE id = %(id)s
            RETURNING id
        )
        INSERT INTO wiki_article_views_log (article_id, user_id, ip_address)
        SELECT id, %(user)s, %(ip)s FROM bump
        """,
        {'id': article_id, 'user': user_id, 'ip': ip_address},
    )
    cursor.execute(
        """
        INSERT INTO wiki_user_reading_history (user_id, article_id, viewed_at)
        VALUES (%s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
        ON CONFLICT (user_id, article_id)
        DO UPDATE SET viewed_at = EXCLUDED.viewed_at
        """,
        (user_id, article_id),
    )


def recent_and_popular(cursor, visible_ids, user_id, limit=6):
    """Недавно просмотренное пользователем и популярное — в границах его периметра."""
    if not visible_ids:
        return {'recent': [], 'popular': [], 'favorites': []}
    ids = list(visible_ids)

    cursor.execute(
        """
        SELECT a.id, a.slug, a.title, a.summary, h.viewed_at
          FROM wiki_user_reading_history h
          JOIN wiki_articles a ON a.id = h.article_id
         WHERE h.user_id = %s AND a.id = ANY(%s)
         ORDER BY h.viewed_at DESC LIMIT %s
        """,
        (user_id, ids, limit),
    )
    keys = ('id', 'slug', 'title', 'summary', 'viewed_at')
    recent = [dict(zip(keys, row)) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT a.id, a.slug, a.title, a.summary, a.views
          FROM wiki_articles a
         WHERE a.id = ANY(%s) AND a.status = 'published'
         ORDER BY a.views DESC, a.updated_at DESC LIMIT %s
        """,
        (ids, limit),
    )
    keys = ('id', 'slug', 'title', 'summary', 'views')
    popular = [dict(zip(keys, row)) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT a.id, a.slug, a.title, a.summary, f.position
          FROM wiki_user_favorite_articles f
          JOIN wiki_articles a ON a.id = f.article_id
         WHERE f.user_id = %s AND a.id = ANY(%s)
         ORDER BY f.position, f.favorited_at DESC
        """,
        (user_id, ids),
    )
    keys = ('id', 'slug', 'title', 'summary', 'position')
    favorites = [dict(zip(keys, row)) for row in cursor.fetchall()]

    return {'recent': recent, 'popular': popular, 'favorites': favorites}


def backlinks(cursor, article_id, visible_ids):
    """Кто ссылается на статью — только среди доступных пользователю.

    Без фильтра по периметру обратные ссылки раскрыли бы существование и
    заголовок закрытой статьи.
    """
    if not visible_ids:
        return []
    cursor.execute(
        """
        SELECT a.id, a.slug, a.title
          FROM wiki_article_links l
          JOIN wiki_articles a ON a.id = l.source_id
         WHERE l.target_id = %s AND a.id = ANY(%s)
         ORDER BY a.title
        """,
        (article_id, list(visible_ids)),
    )
    return [dict(zip(('id', 'slug', 'title'), row)) for row in cursor.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Избранное
# ─────────────────────────────────────────────────────────────────────────────

def set_favorite(cursor, user_id, article_id, favorite):
    if favorite:
        cursor.execute(
            """
            INSERT INTO wiki_user_favorite_articles (user_id, article_id, position)
            VALUES (%s, %s, COALESCE((SELECT max(position) + 1
                                        FROM wiki_user_favorite_articles
                                       WHERE user_id = %s), 0))
            ON CONFLICT (user_id, article_id) DO NOTHING
            """,
            (user_id, article_id, user_id),
        )
    else:
        cursor.execute(
            'DELETE FROM wiki_user_favorite_articles WHERE user_id = %s AND article_id = %s',
            (user_id, article_id),
        )
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Файлы
# ─────────────────────────────────────────────────────────────────────────────

def get_file(cursor, file_id):
    cursor.execute(
        """
        SELECT id, article_id, bucket, blob_path, original_name, content_type
          FROM wiki_files WHERE id = %s
        """,
        (file_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(('id', 'article_id', 'bucket', 'blob_path',
                     'original_name', 'content_type'), row))


def register_file(cursor, *, article_id, bucket, blob_path, original_name,
                  content_type, file_size, width, height, uploaded_by):
    cursor.execute(
        """
        INSERT INTO wiki_files (article_id, bucket, blob_path, original_name,
                                content_type, file_size, width, height, uploaded_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (article_id, bucket, blob_path, original_name, content_type,
         file_size, width, height, uploaded_by),
    )
    return cursor.fetchone()[0]


def effective_permissions(cursor, ctx, article, subjects, allowed_sections,
                          section_rules_fn):
    """Эффективные права на статью — единая точка для чтения и для правки.

    Вынесено сюда, чтобы эндпоинт просмотра и эндпоинт сохранения считали права
    ОДНИМ способом. В оригинале удаление статьи гейтилось только ролью, причём
    «редактором» там считались восемь ролей — то есть любой супервайзер мог
    снести любую статью.

    section_rules_fn — queries.section_rules_for_user; передаётся аргументом,
    чтобы этот модуль не импортировал queries (там свои SQL периметра).
    """
    from . import access as wiki_access

    relevant = [s for s in (article.get('section_ids') or []) if s in allowed_sections]
    section_rules = section_rules_fn(cursor, relevant, subjects, ctx['user_id'])
    flat = [rule for rules in section_rules.values() for rule in rules]
    article_rules = article_rules_for_user(
        cursor, [article['id']], subjects, ctx['user_id']).get(article['id'], [])

    return wiki_access.resolve_article_permissions(
        capabilities=ctx['capabilities'],
        visibility_mode=article.get('visibility_mode', 'inherit'),
        strict_mode=article.get('strict_mode', False),
        section_rules=flat,
        article_rules=article_rules,
        otp_role=ctx['otp_role'],
        is_article_owner=(article.get('author_id') == ctx['user_id']
                          or article.get('owner_user_id') == ctx['user_id']),
    )
