# -*- coding: utf-8 -*-
"""Гостевой доступ раздела «Вики»: разовая именная выдача с часами.

Читающая сторона этого механизма существовала с самого начала и работала:
таблица wiki_guest_access заведена вместе с базовыми, выданный раздел попадает в
периметр (queries._AUTO_SECTIONS_SQL), выданная статья — в витрину
(articles._VISIBLE_ARTICLES_SQL), а истёкшая и отозванная выдачи отсекаются там
же. Не было ВЫДАЮЩЕЙ стороны: ни двери, ни права, ни срока в интерфейсе. Модуль
закрывает ровно эту дыру и ничего не меняет в том, как доступ ЧИТАЕТСЯ.

── Три границы выдачи (решения владельца 25.08.2026) ────────────────────────
1. ПРАВО. «В структуре, кто видит раздел, добавить переключатель гостевой
   доступ» — право адресное и живёт в правиле раздела
   (wiki_section_access_rules.can_grant_guest), а не в должности. Кому правило
   выписано — тот и раздаёт, на той ветке, где выписано.
2. ОБЪЕКТ. «Гостевой доступ можно предоставить только внутри своего отдела» —
   отделом ограничен ОБЪЕКТ: раздел или статья обязаны лежать в ветке отдела
   выдающего (structure.section_branch_department ∈ _grant_departments).
3. ПОЛУЧАТЕЛЬ. «Любому сотруднику из icore… тем, кто стоит ниже себя по
   оргструктуре» — отдел получателя не проверяется вовсе, проверяется уровень
   должности, строго ниже своего (access.may_grant_guest_to).

Границы независимы, и ни одна не выводится из другой. Право без объекта открыло
бы чужую ветку тому, кому тумблер поставили щедро; объект без права позволил бы
раздавать всякому, кто просто числится в отделе.

── Срок ─────────────────────────────────────────────────────────────────────
Считается КОНЦОМ ДНЯ по Алматы, а не «моментом плюс N часов». Человек читает
«доступ до 5 сентября» и понимает это как весь день пятого; выдача, истекающая
в 14:37, выглядит как сбой, а не как срок. Поэтому и пресет «7 дней», и выбор
даты приходят к одному выражению — конец выбранного дня, и потолок
(schema.MAX_GUEST_DAYS) считается в днях, а не в часах: иначе «14 дней» и «дата
через 14 дней» означали бы разное.

Время наивное и по Алматы — как во всей вике: колонки TIMESTAMP без зоны, а
«сейчас» в SQL пишется как (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty').
Считать срок в питоне, а сравнивать в SQL — единственный способ разойтись,
поэтому здесь наивное «сейчас» берётся ровно из той же зоны.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import structure as wiki_structure
from .access import ROLE_LEVELS
from .queries import SUBJECT_MATCH as _SUBJECT_MATCH, subject_params
from .schema import MAX_GUEST_DAYS

ALMATY = ZoneInfo('Asia/Almaty')

# «Сейчас» в SQL — одним выражением на весь модуль. Разложенное по запросам, оно
# однажды разойдётся с тем, что считает питон, и выдача «истечёт» на шесть часов
# раньше срока у половины запросов.
NOW_SQL = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Потолок ленты выдач за один запрос. Как и у тренажёров, ограничение про
# браузер, а не про базу: список выдач — таблица, и рисовать её на десять тысяч
# строк незачем.
MAX_PAGE = 200


def now_almaty():
    """Наивное «сейчас» по Алматы — в той же зоне, в какой считает SQL."""
    return datetime.now(ALMATY).replace(tzinfo=None)


# ─────────────────────────────────────────────────────────────────────────────
# Срок: чистая арифметика, проверяемая без базы
# ─────────────────────────────────────────────────────────────────────────────

def resolve_expiry(now, *, days=None, until=None):
    """Момент истечения выдачи: конец выбранного дня по Алматы.

    days  — пресет «на N дней»: последним будет день now.date() + N.
    until — дата в формате ГГГГ-ММ-ДД: последним будет этот день.
    Ровно одно из двух; ни одного — ошибка, оба — тоже (умолчание пришлось бы
    выбрать, и тихий выбор здесь означал бы срок, которого человек не просил).

    Поднимает ValueError с текстом для человека: его показывают в форме, и
    «invalid literal for int()» там читать некому.
    """
    has_days = days is not None and str(days).strip() != ''
    has_until = until is not None and str(until).strip() != ''
    if has_days and has_until:
        return _fail('Укажите либо срок в днях, либо дату — но не оба сразу')
    if not has_days and not has_until:
        return _fail('Укажите срок гостевого доступа')

    today = now.date()
    if has_days:
        try:
            count = int(days)
        except (TypeError, ValueError):
            return _fail('Срок в днях — целое число')
        if count < 1:
            return _fail('Срок гостевого доступа — минимум один день')
        if count > MAX_GUEST_DAYS:
            return _fail('Гостевой доступ выдают не больше чем на %d дней'
                         % MAX_GUEST_DAYS)
        last_day = today + timedelta(days=count)
    else:
        try:
            last_day = datetime.strptime(str(until).strip()[:10], '%Y-%m-%d').date()
        except ValueError:
            return _fail('Дата окончания — в формате ГГГГ-ММ-ДД')
        if last_day < today:
            return _fail('Дата окончания уже прошла')
        if last_day > today + timedelta(days=MAX_GUEST_DAYS):
            return _fail('Гостевой доступ выдают не больше чем на %d дней'
                         % MAX_GUEST_DAYS)

    # Конец дня, а не полночь следующего: полночь следующего дня в подписи
    # «до 06.09» читалась бы как «шестого уже нельзя», хотя доступ ещё жив.
    return datetime.combine(last_day, time(23, 59, 59))


def _fail(message):
    raise ValueError(message)


def days_left(expires_at, now):
    """Сколько дней осталось, как их считает человек: сегодня — это 0.

    По календарным дням, а не по часам: «осталось 0 дней» рядом с «до 25.08»
    означает «сегодня последний», и это ровно то, что человек видит в календаре.
    Отрицательное значение — выдача уже истекла.
    """
    if not expires_at:
        return None
    return (expires_at.date() - now.date()).days


def grant_status(row, now):
    """Состояние выдачи: отозвана, истекла или действует.

    Порядок проверок именно такой: отозванная вчера выдача с сегодняшним сроком
    остаётся ОТОЗВАННОЙ, а не «истекает завтра». Отзыв — событие, срок — просто
    дата, и событие сильнее.
    """
    if row.get('revoked_at'):
        return 'revoked'
    expires = row.get('expires_at')
    if expires and expires <= now:
        return 'expired'
    return 'active'


# ─────────────────────────────────────────────────────────────────────────────
# Где человек вправе выдавать гостевой доступ
# ─────────────────────────────────────────────────────────────────────────────
#
# Тот же рекурсивный обход, что и у чтения (queries._AUTO_SECTIONS_SQL):
# правило, помеченное «на подразделы», раздаёт право и вглубь. Иначе тумблер
# вёл бы себя не так, как соседний с ним grant_subsections, — на одной форме, в
# одной строке.
_GRANTABLE_SECTIONS_SQL = """
WITH RECURSIVE rule_hits AS (
    SELECT r.section_id, bool_or(r.grant_subsections) AS deep
      FROM wiki_section_access_rules r
      JOIN wiki_sections s ON s.id = r.section_id AND s.status = 'active'
     WHERE r.can_grant_guest
       -- И ЧИТАТЬ. Раздавать раздел, которого сам не видишь, нельзя: тот же
       -- принцип, что и у прав в правиле («право без чтения бессмысленно»,
       -- routes_structure), и та же проверка стоит на пишущей стороне.
       AND r.can_read
       AND (
""" + _SUBJECT_MATCH + """
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
"""


def grantable_section_ids(cursor, subjects, user_id):
    """Разделы, на которые человеку выписано право выдавать гостевой доступ.

    Только правилами: мастер-ключ сюда не подмешивается намеренно. Кто вправе
    выдавать ВЕЗДЕ (супер-админ, роль вики с can_manage_access), спрашивает не
    список, а признак unbounded, — иначе на каждый его запрос считалось бы
    дерево целиком ради ответа «да».
    """
    cursor.execute(_GRANTABLE_SECTIONS_SQL, subject_params(subjects, user_id))
    return {row[0] for row in cursor.fetchall()}


# Есть ли право выдавать ХОТЬ ГДЕ. Отдельным запросом, а не длиной списка из
# grantable_section_ids: признак спрашивает /ping на каждом заходе в раздел, и
# ради ответа «да/нет» разворачивать всё дерево подразделов незачем.
_MAY_GRANT_ANYWHERE_SQL = """
SELECT EXISTS (
    SELECT 1
      FROM wiki_section_access_rules r
      JOIN wiki_sections s ON s.id = r.section_id AND s.status = 'active'
     WHERE r.can_grant_guest
       AND r.can_read
       AND (
""" + _SUBJECT_MATCH + """
       )
)
"""


def may_grant_guest_anywhere(cursor, subjects, user_id):
    """Выписано ли человеку право выдавать гостевой доступ хотя бы на одном разделе."""
    cursor.execute(_MAY_GRANT_ANYWHERE_SQL, subject_params(subjects, user_id))
    return bool(cursor.fetchone()[0])


def shareable_section_ids(cursor, subjects, user_id, *, unbounded=False,
                          departments=None, branch_map=None):
    """Разделы, которые человек вправе открыть гостю.

    Пересечение двух независимых границ (см. шапку модуля): ПРАВО — правило с
    can_grant_guest, ОБЪЕКТ — ветка своего отдела. Пересечение, а не «или»:
    право на чужой ветке не делает её своей, а своя ветка без права не делает
    раздающим.

    unbounded — супер-админ и роль вики с can_manage_access: обе границы для них
    сняты, как и всюду в разделе. Возвращаем None — «все активные разделы»,
    чтобы вызывающий не тащил в память список ради ответа «любой».
    """
    if unbounded:
        return None
    allowed = grantable_section_ids(cursor, subjects, user_id)
    if departments is None:
        return allowed
    branches = branch_map if branch_map is not None else wiki_structure.branch_department_map(cursor)
    inside = {int(value) for value in departments}
    return {sid for sid in allowed if branches.get(sid) in inside}


# Получатели гостевого доступа: любой РАБОТАЮЩИЙ сотрудник компании, чей уровень
# должности строго ниже уровня выдающего (access.may_grant_guest_to). Отдел не
# при чём — отделом ограничен объект, а не человек.
#
# Фильтр по status = 'working', а не по is_active: в боевой базе is_active снят
# почти у всех, и по нему список оказался бы почти пустым (см.
# structure.grantable_people — та же ловушка и то же решение).
#
# BETWEEN 1 AND потолок отсекает и незнакомую должность: у неё уровень 0, а ноль
# меньше любого потолка — без нижней границы опечатка в поле role прошла бы
# проверку, которая должна была её отклонить.
_CANDIDATES_SQL = """
SELECT u.id, u.name, u.role, d.name
  FROM users u
  LEFT JOIN departments d ON d.id = u.department_id
 WHERE u.status = 'working'
   AND u.id <> %(actor)s
   AND (%(all)s
        OR COALESCE((%(levels)s::jsonb ->> lower(coalesce(u.role, '')))::int, 0)
           BETWEEN 1 AND %(ceiling)s)
   AND (%(query)s = '' OR u.name ILIKE %(like)s)
 ORDER BY u.name
 LIMIT %(limit)s
"""


def guest_candidates(cursor, *, actor_id, actor_level, unbounded=False,
                     query='', limit=MAX_PAGE):
    """Кому этот человек вправе выдать гостевой доступ.

    Список считается тем же правилом, что и проверка на записи, поэтому форма
    физически не может предложить того, кого сервер потом отвергнет. Шкала
    должностей уезжает в SQL из ROLE_LEVELS, а не переписывается в запросе:
    вторая копия шкалы однажды разойдётся с первой.
    """
    import json

    text = (query or '').strip()
    cursor.execute(_CANDIDATES_SQL, {
        'actor': actor_id,
        'all': bool(unbounded),
        'levels': json.dumps(ROLE_LEVELS),
        'ceiling': max(0, int(actor_level) - 1),
        'query': text,
        'like': '%%%s%%' % text,
        'limit': max(1, min(int(limit), MAX_PAGE)),
    })
    return [{'id': r[0], 'name': r[1], 'role': r[2], 'department_name': r[3]}
            for r in cursor.fetchall()]


# Статьи, которые можно открыть гостю: лежащие в разделах, на которые у человека
# есть право выдачи.
#
# ФОРМА ПРЕДЛАГАЕТ ТОЛЬКО ТО, ЧТО ГОСТЬ ДЕЙСТВИТЕЛЬНО УВИДИТ. Два отсева, и оба
# про читающую сторону, а не про вкус:
#
#   * только 'published'. Статус-условие в articles._VISIBLE_ARTICLES_SQL
#     пускает черновик автору, владельцу и тому, у кого есть право выпускать
#     (can_see_drafts); гостевой ветки там нет вовсе. Выдай мы черновик — сервер
#     ответил бы 200, строка встала бы в список «действует», а получатель не
#     увидел бы ничего. Молчаливый отказ с обеих сторон стола сразу.
#
#   * не strict_mode. Строгий режим снимают только супер-админ, явное правило на
#     статью и авторство (articles._VISIBLE_ARTICLES_SQL) — гостевая выдача его
#     не открывает и открывать не должна: смысл режима в том, что каждое чтение
#     именное и попадает в журнал.
#
# Архив отсеян по той же логике, что и всегда: открывать гостю то, что убрано из
# оборота, незачем.
_SHAREABLE_ARTICLES_SQL = """
SELECT a.id, a.title, a.slug, a.status
  FROM wiki_articles a
 WHERE a.status = 'published'
   AND NOT a.strict_mode
   AND (%(all)s
        OR EXISTS (SELECT 1 FROM wiki_article_sections xs
                    WHERE xs.article_id = a.id
                      AND xs.section_id = ANY(%(sections)s)))
   AND (%(space)s::int IS NULL
        OR EXISTS (SELECT 1 FROM wiki_article_sections xs
                     JOIN wiki_sections s ON s.id = xs.section_id
                    WHERE xs.article_id = a.id AND s.space_id = %(space)s::int))
   AND (%(query)s = '' OR a.title ILIKE %(like)s)
 ORDER BY a.title
 LIMIT %(limit)s
"""


def shareable_articles(cursor, *, section_ids, space_id=None, query='', limit=50):
    """Статьи, которые человек вправе открыть гостю. section_ids=None — любые."""
    text = (query or '').strip()
    cursor.execute(_SHAREABLE_ARTICLES_SQL, {
        'all': section_ids is None,
        'sections': list(section_ids or ()) or [-1],
        'space': space_id,
        'query': text,
        'like': '%%%s%%' % text,
        'limit': max(1, min(int(limit), MAX_PAGE)),
    })
    return [{'id': r[0], 'title': r[1], 'slug': r[2], 'status': r[3]}
            for r in cursor.fetchall()]


def article_section_ids(cursor, article_id):
    """Разделы статьи. Пустой набор — статья не разложена ни в один раздел."""
    cursor.execute(
        'SELECT section_id FROM wiki_article_sections WHERE article_id = %s',
        (article_id,),
    )
    return {row[0] for row in cursor.fetchall()}


# ─────────────────────────────────────────────────────────────────────────────
# Что человеку уже выдали
# ─────────────────────────────────────────────────────────────────────────────
#
# Что человеку ВЫДАЛИ — списком выдач, а не списком открывшихся разделов.
# Раскрытие по дереву здесь намеренно НЕ повторяется: баннер называет выдачу
# («Регламент и подразделы»), а перечислять в шапке всё, что под разделом,
# незачем. Раскрытие живёт там, где считается доступ, — в периметре чтения
# (queries._GUEST_SECTIONS_CTE) и в _ARTICLE_GRANT_SQL ниже.
#
# Условие «живой выдачи» здесь ВТОРОЕ по счёту в модуле, и это осознанно: в
# периметре оно вклеено в два больших запроса как кусок текста, а тут нужен
# самостоятельный запрос про одного человека. Держать их в согласии обязан
# тест tests/test_wiki_guests.py — он сверяет тексты всех трёх.
_MY_GRANTS_SQL = """
WITH guest_seed AS (
    SELECT g.id, g.section_id, g.article_id, g.include_subsections,
           g.expires_at, g.reason, g.created_at
      FROM wiki_guest_access g
     WHERE g.user_id = %(user)s
       AND g.revoked_at IS NULL
       AND g.expires_at > """ + NOW_SQL + """
)
SELECT s.id, s.section_id, s.article_id, s.include_subsections,
       s.expires_at, s.reason, s.created_at,
       sec.name AS section_name, a.title AS article_title, a.slug AS article_slug
  FROM guest_seed s
  LEFT JOIN wiki_sections sec ON sec.id = s.section_id
  LEFT JOIN wiki_articles a   ON a.id   = s.article_id
 ORDER BY s.expires_at, s.id
"""

_MY_GRANT_KEYS = ('id', 'section_id', 'article_id', 'include_subsections',
                  'expires_at', 'reason', 'created_at',
                  'section_name', 'article_title', 'article_slug')


def my_active_grants(cursor, user_id, now=None):
    """Действующие выдачи текущего человека — чтобы он видел свой срок.

    Отдаётся в /api/wiki/ping, а не отдельной ручкой: срок должен быть виден на
    ЛЮБОЙ вкладке вики, а ping и так запрашивается на каждом заходе в раздел.
    Отдельная ручка означала бы второй запрос ради подписи в шапке — и вкладку,
    на которой подпись почему-то не появляется.
    """
    now = now or now_almaty()
    cursor.execute(_MY_GRANTS_SQL, {'user': user_id})
    items = []
    for row in cursor.fetchall():
        item = dict(zip(_MY_GRANT_KEYS, row))
        item['kind'] = 'article' if item['article_id'] else 'section'
        item['title'] = item['article_title'] or item['section_name'] or 'Без названия'
        item['days_left'] = days_left(item['expires_at'], now)
        item['expires_at'] = item['expires_at'].isoformat() if item['expires_at'] else None
        item['created_at'] = item['created_at'].isoformat() if item['created_at'] else None
        items.append(item)
    return items


# Покрыта ли ЭТА статья гостевой выдачей — и до какого срока.
#
# Три способа покрытия, и все три обязаны сойтись в один ответ: выдача на саму
# статью, выдача на её раздел и выдача на предка её раздела с раскрытием на
# подразделы. Ответ — САМЫЙ ПОЗДНИЙ срок из покрывающих: доступ живёт, пока жива
# хоть одна выдача, и показывать более раннюю дату значило бы обещать, что
# доступ пропадёт, когда он не пропадёт.
_ARTICLE_GRANT_SQL = """
WITH RECURSIVE guest_seed AS (
    SELECT g.section_id AS id, g.include_subsections AS deep, g.expires_at
      FROM wiki_guest_access g
     WHERE g.user_id = %(user)s
       AND g.section_id IS NOT NULL
       AND g.revoked_at IS NULL
       AND g.expires_at > """ + NOW_SQL + """
),
guest_tree AS (
    SELECT id, expires_at FROM guest_seed WHERE deep
    UNION
    SELECT child.id, parent.expires_at
      FROM wiki_sections child
      JOIN guest_tree parent ON child.parent_section_id = parent.id
     WHERE child.status = 'active'
),
covering AS (
    SELECT g.expires_at
      FROM wiki_guest_access g
     WHERE g.user_id = %(user)s
       AND g.article_id = %(article)s
       AND g.revoked_at IS NULL
       AND g.expires_at > """ + NOW_SQL + """
    UNION ALL
    SELECT t.expires_at FROM guest_tree t
     WHERE t.id = ANY(%(sections)s)
    UNION ALL
    SELECT s.expires_at FROM guest_seed s
     WHERE s.id = ANY(%(sections)s)
)
SELECT max(expires_at) FROM covering
"""


def article_grant_expiry(cursor, user_id, article_id, section_ids):
    """До какого срока статья открыта человеку гостевым доступом. None — не открыта.

    section_ids — разделы САМОЙ статьи; пустой список подставляем как заведомо
    непопадающее значение, потому что NULL в ANY(...) не сравнивается.
    """
    cursor.execute(_ARTICLE_GRANT_SQL, {
        'user': user_id, 'article': article_id,
        'sections': list(section_ids) or [-1],
    })
    row = cursor.fetchone()
    return row[0] if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Список выдач: что видит тот, кто раздаёт
# ─────────────────────────────────────────────────────────────────────────────
#
# Периметр списка — «мои ветки плюс мои выдачи». Не «всё, что есть»: список
# выдач это персональные данные (кто, кому, зачем и с какой формулировкой), и
# супервайзеру СЗоВ незачем читать, кому руководитель продаж открыл свой
# регламент. Не «только мои выдачи» тоже: вопрос «кому сейчас открыт этот
# раздел» задаёт владелец ветки, а выдать мог его коллега.
_GRANT_LIST_SQL = """
SELECT g.id, g.user_id, u.name, u.role, d.name,
       g.section_id, sec.name, g.article_id, a.title, a.slug,
       g.include_subsections, g.reason, g.expires_at, g.created_at,
       g.revoked_at, g.granted_by, gb.name, g.revoked_by, rb.name,
       COALESCE(sec.space_id, asec.space_id)
  FROM wiki_guest_access g
  JOIN users u              ON u.id = g.user_id
  LEFT JOIN departments d   ON d.id = u.department_id
  LEFT JOIN wiki_sections sec ON sec.id = g.section_id
  LEFT JOIN wiki_articles a ON a.id = g.article_id
  LEFT JOIN users gb        ON gb.id = g.granted_by
  LEFT JOIN users rb        ON rb.id = g.revoked_by
  -- Пространство статьи — по любому её разделу: статья лежит в одном
  -- пространстве (границу держит wiki/queries.py), и LIMIT 1 здесь не выбор
  -- наугад, а единственное значение.
  LEFT JOIN LATERAL (
      SELECT s2.space_id
        FROM wiki_article_sections xs
        JOIN wiki_sections s2 ON s2.id = xs.section_id
       WHERE xs.article_id = g.article_id
       LIMIT 1
  ) asec ON TRUE
 WHERE (%(all)s
        OR g.granted_by = %(actor)s
        OR g.section_id = ANY(%(sections)s)
        OR EXISTS (SELECT 1 FROM wiki_article_sections xs
                    WHERE xs.article_id = g.article_id
                      AND xs.section_id = ANY(%(sections)s)))
   AND (%(space)s::int IS NULL
        OR COALESCE(sec.space_id, asec.space_id) = %(space)s::int)
   AND (%(query)s = ''
        OR u.name ILIKE %(like)s
        OR sec.name ILIKE %(like)s
        OR a.title ILIKE %(like)s)
 ORDER BY (g.revoked_at IS NULL AND g.expires_at > """ + NOW_SQL + """) DESC,
          g.expires_at DESC, g.id DESC
 LIMIT %(limit)s OFFSET %(offset)s
"""

_GRANT_KEYS = ('id', 'user_id', 'user_name', 'user_role', 'user_department',
               'section_id', 'section_name', 'article_id', 'article_title',
               'article_slug', 'include_subsections', 'reason', 'expires_at',
               'created_at', 'revoked_at', 'granted_by', 'granted_by_name',
               'revoked_by', 'revoked_by_name', 'space_id')


def list_grants(cursor, *, actor_id, section_ids, unbounded=False, space_id=None,
                query='', limit=MAX_PAGE, offset=0, now=None):
    """Выдачи, которые вправе видеть этот человек, свежими сверху.

    Действующие идут первыми независимо от даты: список открывают, чтобы
    отозвать или продлить, а не чтобы читать историю. Истёкшие и отозванные
    остаются ниже — они и есть история, ради которой отзыв не удаляет строку.
    """
    now = now or now_almaty()
    cursor.execute(_GRANT_LIST_SQL, {
        'all': bool(unbounded),
        'actor': actor_id,
        'sections': list(section_ids) or [-1],
        'space': space_id,
        'query': (query or '').strip(),
        'like': '%%%s%%' % (query or '').strip(),
        'limit': max(1, min(int(limit), MAX_PAGE)),
        'offset': max(0, int(offset)),
    })
    items = []
    for row in cursor.fetchall():
        item = dict(zip(_GRANT_KEYS, row))
        item['status'] = grant_status(item, now)
        item['days_left'] = days_left(item['expires_at'], now)
        item['kind'] = 'article' if item['article_id'] else 'section'
        item['title'] = item['article_title'] or item['section_name'] or 'Удалённый объект'
        for field in ('expires_at', 'created_at', 'revoked_at'):
            item[field] = item[field].isoformat() if item[field] else None
        items.append(item)
    return items


def get_grant(cursor, grant_id):
    """Одна выдача — для проверки прав перед отзывом и продлением."""
    cursor.execute(
        """
        SELECT id, user_id, section_id, article_id, granted_by,
               expires_at, revoked_at, include_subsections, reason
          FROM wiki_guest_access WHERE id = %s
        """,
        (grant_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(('id', 'user_id', 'section_id', 'article_id', 'granted_by',
                     'expires_at', 'revoked_at', 'include_subsections', 'reason'), row))


def create_grant(cursor, *, user_id, section_id=None, article_id=None,
                 granted_by, expires_at, reason=None, include_subsections=True):
    """Завести выдачу. Повторная на тот же объект ПРОДЛЕВАЕТ прежнюю.

    Продлевает, а не плодит вторую строку: две действующие выдачи на один и тот
    же раздел одному и тому же человеку — это две даты в списке и вопрос «какая
    из них настоящая», на который ответ «поздняя», но узнать его можно только
    из кода. Отозванную и истёкшую строку при этом не трогаем — они история.

    granted_by НЕ переписывается, и это не мелочь. Право отозвать выданное собой
    остаётся у выдавшего даже после того, как право на разделе у него сняли
    (routes_guests._may_touch), и держится оно ровно на этом поле. Перепиши мы
    его на продлившего — первый выдавший потерял бы возможность отозвать свою же
    выдачу вообще ничем. Кто продлил, знает журнал: log_action зовётся всегда.
    """
    cursor.execute(
        """
        UPDATE wiki_guest_access
           SET expires_at = %(expires)s,
               reason = COALESCE(%(reason)s, reason),
               include_subsections = %(deep)s
         WHERE user_id = %(user)s
           AND section_id IS NOT DISTINCT FROM %(section)s
           AND article_id IS NOT DISTINCT FROM %(article)s
           AND revoked_at IS NULL
           AND expires_at > """ + NOW_SQL + """
        RETURNING id
        """,
        {'user': user_id, 'section': section_id, 'article': article_id,
         'expires': expires_at, 'reason': reason, 'deep': bool(include_subsections)},
    )
    row = cursor.fetchone()
    if row:
        return row[0], False

    cursor.execute(
        """
        INSERT INTO wiki_guest_access
            (user_id, section_id, article_id, granted_by, reason,
             expires_at, include_subsections)
        VALUES (%(user)s, %(section)s, %(article)s, %(by)s, %(reason)s,
                %(expires)s, %(deep)s)
        RETURNING id
        """,
        {'user': user_id, 'section': section_id, 'article': article_id,
         'by': granted_by, 'reason': reason, 'expires': expires_at,
         'deep': bool(include_subsections)},
    )
    return cursor.fetchone()[0], True


def extend_grant(cursor, grant_id, expires_at):
    """Продлить. Срок считается от «сейчас» — см. schema.MAX_GUEST_DAYS."""
    cursor.execute(
        'UPDATE wiki_guest_access SET expires_at = %s WHERE id = %s RETURNING id',
        (expires_at, grant_id),
    )
    return bool(cursor.fetchone())


def revoke_grant(cursor, grant_id, revoked_by):
    """Отозвать. Строка остаётся: отзыв — событие, и история выдач это история.

    Повторный отзыв ничего не меняет (revoked_at IS NULL в условии): кнопку
    нажимают дважды, и второе нажатие не должно переписывать, КОГДА отозвали.
    """
    cursor.execute(
        """
        UPDATE wiki_guest_access
           SET revoked_at = """ + NOW_SQL + """, revoked_by = %(by)s
         WHERE id = %(id)s AND revoked_at IS NULL
        RETURNING id
        """,
        {'id': grant_id, 'by': revoked_by},
    )
    return bool(cursor.fetchone())
