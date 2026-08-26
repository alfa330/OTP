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
Разделение появилось потому, что мастер-ключ доставался роли OTP 'admin'
автоматически, а её носят руководители разных служб: в «Все статьи» им
выкладывалось содержимое чужих отделов вместе с черновиками. С 21.08.2026 роль
'admin' мастер-ключа не носит вовсе (access.capabilities_from_otp_role), и
остался он у супер-админа и у роли вики, назначенной руками; разделение
периметров при этом нужно ровно так же — теперь для неё.
"""

# Совпадение правила с пользователем и подстановка параметров — общие
# с разделами, из wiki/queries.py. Двух определений быть не должно:
# в оригинальной вике они разошлись, и список статей с деревом разделов
# показывали разное.
from . import links as wiki_links
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
    OR %(can_see_drafts)s
    -- ...а также тот, кому право ВЫПУСКАТЬ выписано правилом раздела — но
    -- только в этих разделах. Выпустить можно лишь то, что видишь, и молчать
    -- об этом нельзя; открывать же ему заодно черновики остальных разделов
    -- периметра правило не просило.
    OR EXISTS (SELECT 1 FROM wiki_article_sections ds
                WHERE ds.article_id = a.id
                  AND ds.section_id = ANY(%(draft_sections)s)))
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
   -- ГРАНИЦА ПРОСТРАНСТВА действует и на саму СТАТЬЮ, а не только на её раздел.
   --
   -- Без этой ветки правило, выписанное на СТАТЬЮ, пробивало границу насквозь:
   -- статья-классификатор роздана всем ролям OTP (visibility_mode='restricted',
   -- семь grant-правил), разделы для неё игнорируются по определению режима — и
   -- она открывалась Тез КЦ, которому вики не выдана ни одним пространством.
   -- Через ту же щель прошли бы авторство, владение и гостевая ссылка.
   --
   -- Статья без разделов не принадлежит никакому пространству и границей не
   -- закрывается: закрыть её было бы нечем, а видно её и так только тому, кому
   -- её выдали лично.
   --
   -- ИМЕННАЯ ГОСТЕВАЯ ВЫДАЧА — исключение, ровно такое же, как в границе
   -- разделов (queries._SPACE_GATE_SQL, решение владельца 25.08.2026). Ставить
   -- его обязательно ЗДЕСЬ ТОЖЕ: границ две, и они независимы. Открой мы гостю
   -- только разделы — раздел из чужого отдела появился бы в дереве, а статьи в
   -- нём остались бы отфильтрованы вот этой веткой. Пустая папка вместо
   -- регламента и есть тот молчаливый отказ, ради которого исключение и делали.
   --
   -- Два способа покрытия, и оба именные:
   --   * раздел статьи уже в периметре (%(sections)s) — туда его пустила
   --     гостевая выдача на РАЗДЕЛ, и второй раз проверять пространство незачем:
   --     для НЕгостя список разделов сам собран уже за границей;
   --   * выдача на саму СТАТЬЮ — её раздела в периметре может не быть вовсе.
   -- Правило на статью, авторство и владение через эту щель по-прежнему не
   -- проходят: статья-классификатор так и остаётся закрытой для Тез КЦ.
   AND (
        %(is_super_admin)s
        OR NOT EXISTS (SELECT 1 FROM wiki_article_sections xs
                        WHERE xs.article_id = a.id)
        OR EXISTS (SELECT 1 FROM wiki_article_sections xs
                    WHERE xs.article_id = a.id
                      AND xs.section_id = ANY(%(sections)s))
        OR EXISTS (SELECT 1 FROM wiki_guest_access g
                    WHERE g.article_id = a.id
                      AND g.user_id = %(user_id)s
                      AND g.revoked_at IS NULL
                      AND g.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
        OR EXISTS (
            SELECT 1
              FROM wiki_article_sections xs
              JOIN wiki_sections xsec ON xsec.id = xs.section_id
             WHERE xs.article_id = a.id
               AND (NOT EXISTS (SELECT 1 FROM wiki_space_departments sd
                                 WHERE sd.space_id = xsec.space_id)
                    OR EXISTS (SELECT 1 FROM wiki_space_departments sd
                                WHERE sd.space_id = xsec.space_id
                                  AND sd.department_id = ANY(%(departments)s)))
        )
   )
   -- строгий режим: нужен явный грант, обходит только super_admin
   AND (NOT a.strict_mode
        OR %(is_super_admin)s
        OR a.id IN (SELECT article_id FROM granted)
        OR a.author_id = %(user_id)s)
"""


def _subject_params(ctx, subjects, sections, master_key=True):
    caps = ctx['capabilities']
    # Черновики и архив — вопрос про ВСЮ витрину сразу, поэтому считаются по
    # способностям ДОЛЖНОСТИ, а не по итоговым: право, выписанное правилом на
    # один раздел, не должно показывать чужие черновики в остальных
    # (queries.load_capabilities, инцидент 21.08.2026). Выписанное право
    # выпускать учитывается ниже — поразделно, через draft_sections.
    # Ключ обязателен, а не «если есть»: умолчание пришлось бы выбрать, и любое
    # из двух плохо. Широкое молча открыло бы чужие черновики тому, кто просто
    # не прошёл через load_capabilities; узкое так же молча спрятало бы их от
    # руководителя. Отсутствие ключа — это ошибка сборки контекста, и падать ей
    # положено громко.
    role_caps = ctx['role_capabilities']
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
        'can_see_drafts': bool(role_caps.get('can_publish')
                               or role_caps.get('can_manage_access')),
        'can_see_archived': bool(role_caps.get('can_manage_structure')
                                 or role_caps.get('can_manage_access')),
        # Разделы, где право выпускать пришло из правила. Пусто — заведомо
        # непопадающее значение, как и у 'sections': NULL сравнивать нельзя.
        'draft_sections': list(ctx.get('publish_sections') or ()) or [-1],
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
# owner_user_id выбирается ради ПРАВ: автор и назначенный владелец правят
# статью всегда (resolve_article_permissions), и без этого поля меню действий в
# каталоге скрывало бы «Редактировать» у владельца его же статьи.
_LIST_KEYS = ('id', 'slug', 'title', 'summary', 'article_type', 'status',
              'visibility_mode', 'strict_mode', 'views', 'author_id', 'author_name',
              'owner_user_id', 'updated_at', 'published_at', 'section_ids', 'tags')

_LIST_SQL = """
SELECT a.id, a.slug, a.title, a.summary, a.article_type, a.status,
       a.visibility_mode, a.strict_mode, a.views, a.author_id, u.name,
       a.owner_user_id, a.updated_at, a.published_at,
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
   AND (%(article_types)s::text[] IS NULL
        OR a.article_type = ANY(%(article_types)s::text[]))
   AND (%(query)s::text IS NULL
        OR a.title ILIKE '%%' || %(query)s::text || '%%'
        OR a.summary ILIKE '%%' || %(query)s::text || '%%')
 -- id в хвосте сортировки — ради СТРАНИЦ: оглавление витрины забирает список
 -- несколькими запросами со сдвигом, и при неоднозначном порядке одна статья
 -- попала бы в две страницы, а другая — ни в одну. Порядок держится на одном
 -- updated_at: position на бою у всех статей нулевой.
 ORDER BY a.position, a.updated_at DESC, a.id DESC
 LIMIT %(limit)s OFFSET %(offset)s
"""


def list_articles(cursor, visible_ids, *, section_id=None, status=None,
                  statuses=None, orphans_only=False,
                  article_types=None, query=None, limit=50, offset=0):
    """Список статей в границах уже посчитанного периметра.

    status и statuses — один и тот же фильтр с разной шириной: первый берёт один
    статус (так спрашивает ?status= в адресе), второй — корзину витрины целиком
    (ARTICLE_BUCKETS). Заданы оба — побеждает statuses.

    article_types — СПИСОК типов, а не один: фильтр витрины и фильтр поиска
    читают один и тот же параметр адреса, и «регламенты и инструкции» одним
    запросом дешевле двух. Пустой список равен None — «фильтр не задан».
    """
    if not visible_ids:
        return []
    wanted = list(statuses) if statuses else ([status] if status else None)
    cursor.execute(_LIST_SQL, {
        'ids': list(visible_ids), 'section': section_id, 'statuses': wanted,
        'orphans': bool(orphans_only),
        'article_types': list(article_types) if article_types else None,
        'query': query or None, 'limit': limit, 'offset': offset,
    })
    rows = []
    for row in cursor.fetchall():
        item = dict(zip(_LIST_KEYS, row))
        item['section_ids'] = list(item['section_ids'] or [])
        item['tags'] = list(item['tags'] or [])
        rows.append(item)
    return rows


# ── Авторы: кем написаны статьи периметра ────────────────────────────────────
#
# Нужен фильтру поиска «по создателю». Считается ПО ПЕРИМЕТРУ, а не по таблице
# users: список людей — тоже данные, и выкладывать в выпадающий список всех
# сотрудников портала тому, кому видно три статьи, незачем. Заодно это снимает
# вопрос о ПДн: в списке ровно те, чьё авторство человек и так видит в выдаче.
#
# Автор — a.author_id, то есть КТО СОЗДАЛ статью. Не owner_user_id (владелец,
# отвечающий за актуальность, — его назначают и меняют) и не updated_by (кто
# правил последним). Спрашивают обычно именно про создателя.
_AUTHORS_SQL = """
SELECT a.author_id, u.name, count(*) AS articles
  FROM wiki_articles a
  JOIN users u ON u.id = a.author_id
 WHERE a.id = ANY(%(ids)s)
 GROUP BY a.author_id, u.name
 ORDER BY count(*) DESC, u.name
 -- Потолок на всякий случай: выпадающий список длиннее двух сотен человек
 -- списком быть перестаёт. Сортировка по убыванию вклада делает отсечение
 -- осмысленным — режется хвост из тех, у кого одна статья.
 LIMIT 200
"""


def authors_of(cursor, visible_ids):
    """[{id, name, articles}] — создатели статей периметра, по убыванию вклада.

    Статьи без автора (учётку удалили — ON DELETE SET NULL) в список не
    попадают: строка «— (4)» в фильтре ничего не выбирает и ни о чём не говорит.
    Из этого следует, что сумма по создателям МЕНЬШЕ числа статей периметра.

    УВОЛЕННЫЕ ОСТАЮТСЯ, и это расхождение с соседями намеренное. Списки для
    ВЫДАЧИ доступа (structure.grantable_people, guests.guest_candidates) отсекают
    всех, кроме status='working', — там спрашивают «кому дать право», а право
    уволенному не нужно. Здесь спрашивают «кто это написал», и регламенты
    ушедшего сотрудника никуда не делись: отсеки мы его — статьи стали бы
    ненаходимыми фильтром, а человек решил бы, что их нет.

    Наружу уходят только id и имя. В users лежат телефон, почта и дата
    рождения — фильтру они не нужны, а раздел читают все.
    """
    ids = list(visible_ids or ())
    if not ids:
        return []
    cursor.execute(_AUTHORS_SQL, {'ids': ids})
    return [{'id': row[0], 'name': row[1], 'articles': row[2]}
            for row in cursor.fetchall()]


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
                 'visibility_mode', 'strict_mode', 'ai_opt_out', 'copy_protected',
                 'toc', 'views',
                 'author_id',
                 'author_name', 'owner_user_id', 'updated_by', 'updated_at',
                 'created_at', 'published_at', 'review_due_at',
                 'cross_department', 'source_article_id', 'source_article_title',
                 'section_ids', 'tags')


def get_article(cursor, *, article_id=None, slug=None):
    cursor.execute(
        """
        SELECT a.id, a.slug, a.title, a.summary, a.content, a.article_type, a.status,
               a.visibility_mode, a.strict_mode, a.ai_opt_out, a.copy_protected,
               a.toc, a.views,
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


def register_view(cursor, article_id, user_id, ip_address,
                  department_id=None, role=None):
    """Инкремент счётчика и запись в лог — ОДНИМ запросом.

    В оригинале это были два отдельных запроса без транзакции, и счётчик
    articles.views расходился с article_views_log.

department_id и role — снимок отдела и должности читателя на момент
    чтения, как в тренажёрах и в ознакомлениях. Берутся из уже посчитанного
    контекста запроса, поэтому стоят ноль запросов; смысл их в том, что отчёт
    «кто читает» обязан показывать отдел, в котором человек был ТОГДА (см.
    шапку колонок в schema.py).
    """
    cursor.execute(
        """
        WITH bump AS (
            UPDATE wiki_articles SET views = views + 1 WHERE id = %(id)s
            RETURNING id
        )
        INSERT INTO wiki_article_views_log (article_id, user_id, ip_address,
                                            snapshot_department_id, snapshot_role)
        SELECT id, %(user)s, %(ip)s, %(dept)s, %(role)s FROM bump
        """,
        {'id': article_id, 'user': user_id, 'ip': ip_address,
         'dept': department_id, 'role': role},
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


def recent_and_popular(cursor, visible_ids, user_id, limit=6, recent_limit=10):
    """Недавно просмотренное пользователем и популярное — в границах его периметра.

    У истории чтения свой потолок, и он глубже общего: полка «Продолжить
    чтение» листается страницами по четыре, и шесть строк дали бы полторы
    страницы вместо обещанного десятка. Популярное страниц не имеет — ему
    хватает шести.
    """
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
        (user_id, ids, recent_limit),
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


# ── Где стоят тренажёры ──────────────────────────────────────────────────────
#
# Кнопка тренажёра живёт ВНУТРИ тела статьи (data-wiki-trainer="<ключ>"), своей
# таблицы у неё нет — и правильно, что нет: связь «статья ↔ тренажёр» ровно
# такая же, как «статья ↔ картинка», её источник правды один, и это текст.
#
# Ключи вынимаем регуляркой в САМОМ ПОСТГРЕСЕ, а не тянем тела статей в питон:
# в проде вики 81 % объёма контента — base64-картинки, и вкладка «Тренажёры»
# качала бы мегабайты, чтобы посчитать десяток кнопок.
_TRAINER_USAGE_SQL = """
SELECT a.id, a.slug, a.title, a.status, m.groups[1] AS trainer_key
  FROM wiki_articles a
  CROSS JOIN LATERAL regexp_matches(a.content,
                                    'data-wiki-trainer="([A-Za-z0-9_-]{1,64})"',
                                    'g') AS m(groups)
 WHERE a.id = ANY(%(ids)s)
   AND a.content LIKE '%%data-wiki-trainer=%%'
 ORDER BY a.title
"""


def trainer_usages(cursor, visible_ids):
    """{ключ тренажёра: [статьи, где он вставлен]} в границах периметра.

    Одна статья может вставить один тренажёр дважды (например, в начале и в
    конце длинной инструкции) — в списке она обязана остаться ОДНОЙ строкой,
    иначе «используется в 2 статьях» врёт при одной. Поэтому дедупликация по
    (ключ, id), а не DISTINCT в SQL: distinct по всей строке от повтора внутри
    одной статьи не спасает — regexp_matches отдаёт по строке на вхождение.
    """
    if not visible_ids:
        return {}
    cursor.execute(_TRAINER_USAGE_SQL, {'ids': list(visible_ids)})
    usages = {}
    for article_id, slug, title, status, key in cursor.fetchall():
        bucket = usages.setdefault(key, {})
        bucket.setdefault(article_id, {
            'id': article_id, 'slug': slug, 'title': title, 'status': status,
        })
    return {key: list(items.values()) for key, items in usages.items()}


_LINK_KEYS = ('id', 'slug', 'title', 'status')


def backlinks(cursor, article_id, visible_ids):
    """Кто ссылается на статью — только среди доступных пользователю.

    Без фильтра по периметру обратные ссылки раскрыли бы существование и
    заголовок закрытой статьи.

    Статус едет вместе с заголовком НЕ для красоты. В проде сегодня ни одна цель
    внутренней ссылки не опубликована (238 черновиков и 15 архивных на 253 пары),
    то есть у тех, кто вправе видеть черновики, список будет состоять из
    недописанного. Без подписи «Черновик» такая строка читается как обещание
    готовой статьи.
    """
    if not visible_ids:
        return []
    cursor.execute(
        """
        SELECT a.id, a.slug, a.title, a.status
          FROM wiki_article_links l
          JOIN wiki_articles a ON a.id = l.source_id
         WHERE l.target_id = %s AND a.id = ANY(%s) AND a.id <> %s
         ORDER BY a.title
        """,
        (article_id, list(visible_ids), article_id),
    )
    return [dict(zip(_LINK_KEYS, row)) for row in cursor.fetchall()]


def related_articles(cursor, content, article_id, visible_ids):
    """«Связанные материалы» — статьи, на которые ссылается ТЕКСТ этой статьи.

    Собирается из тела, а НЕ из wiki_article_links, и это главное решение всей
    фичи. Тело уже лежит в памяти обработчика (get_article выбирает a.content),
    поэтому блок стоит ровно один добавочный запрос — и при этом физически не
    может разойтись с тем, что человек видит в тексте. Читай мы таблицу, блок
    начал бы врать в тот же день, когда кто-нибудь добавит пятый путь записи
    тела и забудет позвать пересборку, — а один такой путь (restore_version)
    в разделе уже был.

    Порядок — как в тексте: список читается как оглавление к статье, и
    сортировка по алфавиту оторвала бы его от места, где ссылка встретилась.
    """
    if not visible_ids:
        return []
    slugs = wiki_links.article_slugs(content)
    if not slugs:
        return []
    # Периметр здесь обязателен ровно по той же причине, что и в backlinks:
    # иначе блок раскрыл бы заголовок статьи, которую человеку видеть нельзя.
    # Ссылка в тексте при этом остаётся — по ней он получит честные 404.
    cursor.execute(
        """
        SELECT a.id, a.slug, a.title, a.status
          FROM wiki_articles a
         WHERE a.slug = ANY(%s::text[]) AND a.id = ANY(%s) AND a.id <> %s
        """,
        (list(slugs), list(visible_ids), article_id),
    )
    found = {row[1]: dict(zip(_LINK_KEYS, row)) for row in cursor.fetchall()}
    return [found[slug] for slug in slugs if slug in found]


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


def permissions_for_articles(cursor, ctx, articles, subjects, allowed_sections,
                             section_rules_fn):
    """Эффективные права на КАЖДУЮ статью списка: {id статьи: права}.

    Двумя запросами на весь список, а не по статье: правила разделов и правила
    статей и так спрашиваются по массиву идентификаторов, а расчёт поверх них
    (resolve_article_permissions) базы не касается вовсе. Считать построчно
    значило бы пару запросов на каждую строку выдачи — тот же N+1, от которого
    в каталоге отдельно ушли счётчики (catalog_counts).

    Пустой список базу не трогает: выдача бывает пустой на каждом втором
    фильтре, и два запроса «ни о чём» здесь ни к чему.

    section_rules_fn — queries.section_rules_for_user; передаётся аргументом,
    чтобы этот модуль не импортировал queries (там свои SQL периметра).
    """
    from . import access as wiki_access

    rows = [a for a in (articles or ()) if a and a.get('id')]
    if not rows:
        return {}

    # Разделы спрашиваем ОДНИМ множеством по всей выдаче: статьи лежат в общих
    # ветках, и по строкам это были бы одни и те же правила, запрошенные заново.
    wanted = sorted({s for a in rows for s in (a.get('section_ids') or [])
                     if s in allowed_sections})
    section_rules = section_rules_fn(cursor, wanted, subjects, ctx['user_id'])
    article_rules = article_rules_for_user(
        cursor, [a['id'] for a in rows], subjects, ctx['user_id'])

    result = {}
    for article in rows:
        relevant = [s for s in (article.get('section_ids') or []) if s in allowed_sections]
        result[article['id']] = wiki_access.resolve_article_permissions(
            capabilities=ctx['capabilities'],
            visibility_mode=article.get('visibility_mode', 'inherit'),
            strict_mode=article.get('strict_mode', False),
            section_rules=[rule for s in relevant for rule in section_rules.get(s, ())],
            article_rules=article_rules.get(article['id'], []),
            otp_role=ctx['otp_role'],
            is_article_owner=(article.get('author_id') == ctx['user_id']
                              or article.get('owner_user_id') == ctx['user_id']),
        )
    return result


def effective_permissions(cursor, ctx, article, subjects, allowed_sections,
                          section_rules_fn):
    """Эффективные права на ОДНУ статью — тот же расчёт, что у списка.

    Вынесено сюда, чтобы эндпоинт просмотра и эндпоинт сохранения считали права
    ОДНИМ способом. В оригинале удаление статьи гейтилось только ролью, причём
    «редактором» там считались восемь ролей — то есть любой супервайзер мог
    снести любую статью.
    """
    return permissions_for_articles(cursor, ctx, [article], subjects,
                                    allowed_sections, section_rules_fn)[article['id']]
