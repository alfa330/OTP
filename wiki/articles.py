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

Периметра два, и это не дубль правил, а разные вопросы к одной модели:
  * ПОЛНЫЙ (master_key=True) — «вправе ли открыть»: сюда входит мастер-ключ
    администратора вики. По нему работают точечные пути: статья по слагу,
    файл, избранное, правка, ознакомление;
  * ЛИЧНЫЙ (master_key=False) — «имеет ли отношение»: только правила, авторство
    и гостевой доступ. По нему работают ВИТРИНЫ: список «Все статьи», поиск,
    подсказки, главная раздела, дерево разделов.
Разделение появилось потому, что мастер-ключ достаётся роли OTP 'admin'
автоматически, а её носят руководители разных служб: в «Все статьи» им
выкладывалось содержимое чужих отделов вместе с черновиками. Администратор
по-прежнему может увидеть всё — но по явной просьбе (?scope=all), а не молча.
"""

# Совпадение правила с пользователем и подстановка параметров — общие
# с разделами, из wiki/queries.py. Двух определений быть не должно:
# в оригинальной вике они разошлись, и список статей с деревом разделов
# показывали разное.
from . import schema as wiki_schema
from .queries import SUBJECT_MATCH as _SUBJECT_MATCH, subject_params

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


def _subject_params(ctx, subjects, sections, master_key=True):
    caps = ctx['capabilities']
    role = str(ctx.get('otp_role') or '').strip().lower()
    return dict(
        subject_params(subjects, ctx['user_id']),
        **{
        'sections': list(sections) or [-1],
        'is_wiki_admin': bool(caps.get('can_manage_access')) and master_key,
        # Супер-админ — всегда, даже в витрине: по решению владельца он видит
        # статьи всех отделов. У мастер-ключа выше оговорка про master_key
        # остаётся, потому что can_manage_access несёт и роль 'admin'.
        'is_super_admin': role in ('super_admin', 'superadmin'),
        # Черновик — незаконченный текст, и в списке чтения ему не место.
        # Гейтом было can_edit, а её в OTP по умолчанию получают и супервайзер,
        # и тренер: чужие неопубликованные статьи попадали к ним в «Все статьи».
        # Право видеть черновик оставляем тому, кто вправе его ОПУБЛИКОВАТЬ:
        # иначе выпускать нечего — черновик надо сперва увидеть. Автор и
        # владелец видят свой всегда (условие в SQL).
        #
        # С 19.08.2026 способность публиковать есть и у супервайзера
        # (wiki/access.py: решение владельца), поэтому чужие черновики В СВОЁМ
        # ПЕРИМЕТРЕ он видит — это следствие права выпуска, а не регресс того
        # ужесточения. Тренер по-прежнему за гейтом.
        'can_see_drafts': bool(caps.get('can_publish')
                               or caps.get('can_manage_access')),
        'can_see_archived': bool(caps.get('can_manage_structure')
                                 or caps.get('can_manage_access')),
        },
    )


def visible_article_ids(cursor, ctx, subjects, allowed_sections, *, master_key=True):
    """Множество статей, которые пользователь вправе прочитать.

    master_key=False считает ЛИЧНЫЙ периметр: мастер-ключ администратора вики
    (can_manage_access) и обход строгого режима super_admin не применяются, и
    остаются только правила разделов и статей, авторство и гостевой доступ.
    Так считают витрины чтения — см. allowed_section_ids с тем же флагом.
    """
    cursor.execute(_VISIBLE_ARTICLES_SQL,
                   _subject_params(ctx, subjects, allowed_sections, master_key))
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
        dict(subject_params(subjects, user_id), articles=list(article_ids)),
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
   -- Статья без единого раздела: отдельный запрос витрины, а не «section IS NULL».
   -- Такие статьи есть только в наследии импорта (сервер давно кладёт новую в
   -- отдел автора), и без этого условия они не открываются ни с одной плитки.
   AND (NOT %(orphans)s
        OR NOT EXISTS (SELECT 1 FROM wiki_article_sections s WHERE s.article_id = a.id))
   AND (%(statuses)s::text[] IS NULL OR a.status = ANY(%(statuses)s::text[]))
   AND (%(article_type)s::text IS NULL OR a.article_type = %(article_type)s::text)
   AND (%(query)s::text IS NULL
        OR a.title ILIKE '%%' || %(query)s::text || '%%'
        OR a.summary ILIKE '%%' || %(query)s::text || '%%')
 ORDER BY a.position, a.updated_at DESC
 LIMIT %(limit)s OFFSET %(offset)s
"""


def list_articles(cursor, visible_ids, *, section_id=None, status=None,
                  statuses=None, orphans_only=False,
                  article_type=None, query=None, limit=50, offset=0):
    """Список статей в границах уже посчитанного периметра.

    status и statuses — один и тот же фильтр с разной шириной: первый берёт один
    статус (так спрашивает ?status= в адресе), второй — корзину витрины целиком
    (ARTICLE_BUCKETS). Заданы оба — побеждает statuses.
    """
    if not visible_ids:
        return []
    wanted = list(statuses) if statuses else ([status] if status else None)
    cursor.execute(_LIST_SQL, {
        'ids': list(visible_ids), 'section': section_id, 'statuses': wanted,
        'orphans': bool(orphans_only),
        'article_type': article_type or None,
        'query': query or None, 'limit': limit, 'offset': offset,
    })
    rows = []
    for row in cursor.fetchall():
        item = dict(zip(_LIST_KEYS, row))
        item['section_ids'] = list(item['section_ids'] or [])
        item['tags'] = list(item['tags'] or [])
        rows.append(item)
    return rows


# ── Каталог: сколько статей в каждом разделе, по корзинам ────────────────────

_CATALOG_SECTION_SQL = """
SELECT s.section_id, a.status, count(*)
  FROM wiki_article_sections s
  JOIN wiki_articles a ON a.id = s.article_id
 WHERE s.article_id = ANY(%(ids)s)
 GROUP BY s.section_id, a.status
"""

# Статьи, не привязанные ни к одному разделу. Считаются ОТДЕЛЬНЫМ запросом, а не
# вычитанием из суммы по разделам: статья может лежать сразу в нескольких
# разделах, и в такой разности она вычлась бы дважды.
_CATALOG_ORPHAN_SQL = """
SELECT a.status, count(*)
  FROM wiki_articles a
 WHERE a.id = ANY(%(ids)s)
   AND NOT EXISTS (SELECT 1 FROM wiki_article_sections s WHERE s.article_id = a.id)
 GROUP BY a.status
"""

_CATALOG_TOTAL_SQL = """
SELECT a.status, count(*)
  FROM wiki_articles a
 WHERE a.id = ANY(%(ids)s)
 GROUP BY a.status
"""


def _empty_buckets():
    return {bucket: 0 for bucket in wiki_schema.ARTICLE_BUCKETS}


def _add_status(target, status, count):
    """Разложить статус по корзинам витрины. Неизвестный статус не теряем.

    В базе стоит CHECK, но корзины — код рядом, а не тот же CHECK: появись
    седьмой статус в схеме без правки ARTICLE_BUCKETS, статьи исчезли бы из
    раздела молча. Пусть лучше упадут в черновики и попадутся на глаза.
    """
    bucket = wiki_schema.BUCKET_OF_STATUS.get(status, 'draft')
    target[bucket] = target.get(bucket, 0) + count


def catalog_counts(cursor, visible_ids):
    """Счётчики каталога в границах периметра: по разделам, без раздела и всего.

    Возвращает {'sections': {id: {bucket: n}}, 'orphans': {bucket: n},
    'totals': {bucket: n}}. Три запроса на весь каталог, а не по разделу:
    N+1 здесь означал бы запрос на каждую плитку.
    """
    ids = list(visible_ids or ())
    empty = {'sections': {}, 'orphans': _empty_buckets(), 'totals': _empty_buckets()}
    if not ids:
        return empty

    by_section = {}
    cursor.execute(_CATALOG_SECTION_SQL, {'ids': ids})
    for section_id, status, count in cursor.fetchall():
        _add_status(by_section.setdefault(section_id, _empty_buckets()), status, count)

    orphans = _empty_buckets()
    cursor.execute(_CATALOG_ORPHAN_SQL, {'ids': ids})
    for status, count in cursor.fetchall():
        _add_status(orphans, status, count)

    totals = _empty_buckets()
    cursor.execute(_CATALOG_TOTAL_SQL, {'ids': ids})
    for status, count in cursor.fetchall():
        _add_status(totals, status, count)

    return {'sections': by_section, 'orphans': orphans, 'totals': totals}


_ARTICLE_KEYS = ('id', 'slug', 'title', 'summary', 'content', 'article_type', 'status',
                 'visibility_mode', 'strict_mode', 'ai_opt_out', 'toc', 'views',
                 'author_id',
                 'author_name', 'owner_user_id', 'updated_by', 'updated_at',
                 'created_at', 'published_at', 'review_due_at',
                 'cross_department', 'source_article_id', 'source_article_title',
                 'section_ids', 'tags')


def get_article(cursor, *, article_id=None, slug=None):
    cursor.execute(
        """
        SELECT a.id, a.slug, a.title, a.summary, a.content, a.article_type, a.status,
               a.visibility_mode, a.strict_mode, a.ai_opt_out, a.toc, a.views,
               a.author_id, u.name,
               a.owner_user_id, a.updated_by, a.updated_at, a.created_at,
               a.published_at, a.review_due_at,
               a.cross_department, a.source_article_id,
               (SELECT src.title FROM wiki_articles src WHERE src.id = a.source_article_id),
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

def is_favorite(cursor, user_id, article_id):
    """Лежит ли статья в избранном у этого человека.

    Ездит в теле статьи: без этого звёздочка в шапке не знала своего состояния и
    была кнопкой в одну сторону — интерфейс всегда предлагал «В избранное» и
    всегда слал POST, а вставка в базе идёт с ON CONFLICT DO NOTHING. Повторное
    нажатие честно ничего не меняло, но рапортовало «Добавлено в избранное»:
    убрать статью из избранного было нельзя вообще.
    """
    cursor.execute(
        'SELECT 1 FROM wiki_user_favorite_articles WHERE user_id = %s AND article_id = %s',
        (user_id, article_id),
    )
    return cursor.fetchone() is not None


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
        SELECT id, article_id, bucket, blob_path, original_name, content_type,
               uploaded_by
          FROM wiki_files WHERE id = %s
        """,
        (file_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(('id', 'article_id', 'bucket', 'blob_path',
                     'original_name', 'content_type', 'uploaded_by'), row))


def list_attachments(cursor, article_id):
    """Приложения статьи — то, что читатель скачивает под текстом.

    Только помеченные вложениями: в этой же таблице лежат картинки тела статьи,
    и без фильтра список приложений к инструкции состоял бы из её иллюстраций.

    Адреса отдаём ОТНОСИТЕЛЬНЫМИ (/api/wiki/file/<id>) — по той же причине, по
    которой они относительны внутри тела статьи: домен API меняется, а ссылка
    живёт годами. Абсолютным адрес делает фронт перед показом
    (src/components/wiki/fileUrls.js).
    """
    cursor.execute(
        """
        SELECT f.id, f.original_name, f.content_type, f.file_size, f.created_at,
               f.uploaded_by, u.name
          FROM wiki_files f
          LEFT JOIN users u ON u.id = f.uploaded_by
         WHERE f.article_id = %s AND f.is_attachment
         ORDER BY f.sort_order, f.created_at
        """,
        (article_id,),
    )
    items = []
    for row in cursor.fetchall():
        file_id = str(row[0])
        items.append({
            'id': file_id,
            'name': row[1],
            'content_type': row[2],
            'size': int(row[3] or 0),
            'created_at': row[4],
            'uploaded_by': row[5],
            'uploaded_by_name': row[6],
            'url': '/api/wiki/file/%s' % file_id,
            # Отдельный адрес, а не флажок на фронте: скачивание и просмотр
            # различаются заголовком Content-Disposition, который ставит уже
            # подпись GCS, — значит решать должен сервер, а не тег <a>.
            'download_url': '/api/wiki/file/%s?download=1' % file_id,
        })
    return items


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
