# -*- coding: utf-8 -*-
"""Где лежит статья и кому она открыта — справка для того, кто её правит.

Решение владельца 06.10.2026: «находясь в статье, человек, у которого есть
доступ на редактирование, мог просматривать, кому доступен данный раздел… и
показывать в дереве, где находится данная статья». До этого ответ лежал в
четырёх шагах от статьи — «Статьи» → «Структура» → найти раздел в дереве →
«Кому открыт раздел» — и открывался только тому, кто вправе РАЗДАВАТЬ доступ
(лестница GRANT_CEILING). Редактор статьи, которому раздавать не по чину, не мог
узнать, кто прочитает то, что он пишет.

В тот же день владелец уточнил вид ответа: не правила, а ЛЮДИ — «кому открыта
статья, просто выводить списком… с пагинацией и поиском… можно будет
просматривать, кому и как открыт раздел». Поэтому справка отдаёт две вещи:
дерево мест статьи и список тех, кому она открыта, — у каждого права и пометка,
откуда доступ, если он не по отделу. Кто именно читает, считает wiki/readers.py.

И развёл их по людям: «доступ к просмотру, кому открыт, — данная опция пусть
будет открыта у суперадмина, а именно где находится сама статья — редакторам и
выше». Дерево мест получает тот, кто вправе статью править; список людей
считается и отдаётся только супер-админу (sees_readers) — остальным в ответе
на его месте пусто, а не «никому не открыта».

ТОЛЬКО ЧТЕНИЕ. Модуль ничего не пишет и никаких прав не выдаёт: выдача живёт
там же, где была (routes_structure), со своими тремя границами. Отсюда и другой
гейт — право ПРАВИТЬ статью, а не право раздавать: вопрос «где это лежит»
задаёт автор текста, и лестница выдачи к нему отношения не имеет.

── Из чего собран список ────────────────────────────────────────────────────
Из тех же источников, что и периметр чтения (queries._AUTO_SECTIONS_SQL и
articles._VISIBLE_ARTICLES_SQL), — иначе окно назвало бы читателями тех, кто
статьи не видит:

  1. правила разделов статьи и разделов НАД ними с тумблером «вместе с
     подразделами» — в периметре они действуют наравне с собственными. Вверх
     идём только через ЖИВЫЕ разделы: правило над архивным предком не доходит;
  2. публичность раздела (со списком отделов, если он задан) и его владелец;
  3. гостевая выдача — на раздел, на раздел выше с подразделами, на всё
     пространство, на саму статью;
  4. правила самой статьи: выдача открывает её поверх раздела, запрет закрывает
     и тем, кому открыт раздел; автор статьи читает её всегда;
  5. супер-админ и администратор вики — мастер-ключом, поверх всех правил;
  6. граница пространства — последнее слово: правило на отдел, которому
     пространство не выдано, никого не открывает.

── Чужую ветку не называем ──────────────────────────────────────────────────
Статья лежит сразу в нескольких разделах. Раздел за периметром смотрящего в
ответ не попадает (только счётчиком), и люди, которые читают статью ТОЛЬКО
через такой раздел, — тоже: супервайзеру СЗоВ незачем узнавать отсюда состав
ветки ОП. Права человека при этом считаются по всем разделам статьи — иначе у
того, кто назван, они оказались бы занижены.
"""

from . import edit as wiki_edit
from . import queries
from . import readers
from . import structure
from .guests import NOW_SQL
from .schema import PERMISSION_COLUMNS

# Раздел и всё, что над ним, — от раздела к корню. Одним запросом на все разделы
# статьи: она лежит сразу в нескольких, и поштучный обход дерева стоил бы
# запроса на раздел. Ограничитель глубины тот же, что в
# structure.section_branch_department: петель сервер не допускает, но
# зациклиться здесь значит подвесить запрос.
#
# Статус и отдел едут у КАЖДОГО узла: по статусу решается, докуда доходят
# правила сверху, по отделу — какой раздел в дереве рисуется веткой отдела.
_CHAIN_SQL = """
WITH RECURSIVE up AS (
    SELECT s.id AS root, s.id, s.parent_section_id, 0 AS depth
      FROM wiki_sections s
     WHERE s.id = ANY(%(sections)s)
    UNION ALL
    SELECT up.root, p.id, p.parent_section_id, up.depth + 1
      FROM up
      JOIN wiki_sections p ON p.id = up.parent_section_id
     WHERE up.depth < 50
)
SELECT up.root, up.depth, s.id, s.name, s.status, s.space_id,
       s.department_id, s.visibility_scope, s.owner_user_id
  FROM up
  JOIN wiki_sections s ON s.id = up.id
"""

_CHAIN_KEYS = ('root', 'depth', 'id', 'name', 'status', 'space_id', 'department_id',
               'visibility_scope', 'owner_user_id')

# Действующие гостевые выдачи, которые МОГУТ касаться статьи: на неё саму, на
# её разделы и разделы над ними, на её пространства. Докуда каждая доходит,
# решает guests_of_place — тем же правилом, что queries._GUEST_SECTIONS_CTE.
_GUESTS_SQL = """
SELECT g.user_id, g.section_id, g.article_id, g.space_id, g.include_subsections
  FROM wiki_guest_access g
 WHERE g.revoked_at IS NULL
   AND g.expires_at > """ + NOW_SQL + """
   AND (g.article_id = %(article)s
        OR g.section_id = ANY(%(sections)s)
        OR g.space_id = ANY(%(spaces)s))
"""


# Права человека в ответе — буквами, в порядке лестницы: читать, создавать,
# править, публиковать, согласовывать, удалять.
RIGHT_LETTERS = (('r', 'can_read'), ('c', 'can_create'), ('e', 'can_edit'),
                 ('p', 'can_publish'), ('a', 'can_approve'), ('d', 'can_delete'))


def sees_readers(ctx):
    """Отдавать ли смотрящему поимённый список «кому открыта статья».

    Только супер-админу — решение владельца 06.10.2026. По РОЛИ, а не по
    способности (queries.from_super_admin): администратор вики с ролью,
    назначенной руками, списка не получает. Признак один на обе двери — на саму
    справку и на карточку статьи, по которой окно называет свою кнопку.
    """
    return queries.from_super_admin(ctx)


def section_chains(cursor, section_ids):
    """{id раздела: [узел, …]} — сам раздел и его предки, от раздела к корню."""
    ids = sorted({int(value) for value in (section_ids or ()) if value})
    if not ids:
        return {}
    cursor.execute(_CHAIN_SQL, {'sections': ids})
    chains = {}
    for row in cursor.fetchall():
        node = dict(zip(_CHAIN_KEYS, row))
        chains.setdefault(node['root'], []).append(node)
    # Порядок «от раздела к корню» держим сами, а не верой в порядок строк
    # ответа: на нём стоит всё дальнейшее — и докуда доходят правила, и путь на
    # экране, и чей это подраздел.
    for nodes in chains.values():
        nodes.sort(key=lambda node: node['depth'])
    return chains


def live_chain(chain):
    """Раздел и идущие подряд ЖИВЫЕ предки — докуда достают правила сверху.

    Архивный раздел не открывает ничего: правило на нём не действует
    (rule_hits и section_rights_all берут только status = 'active'), а через
    него не проходит и правило сверху. Поэтому у архивного раздела цепочка
    пуста, а у живого обрывается на первом же архивном предке.
    """
    out = []
    for node in chain or ():
        if node['status'] != 'active':
            break
        out.append(node)
    return out


def display_path(chain):
    """Путь до раздела для дерева на экране — от корня к разделу.

    Живой раздел под архивным родителем «Структура» рисует в корне пространства
    (structureTree.parentKeyOf), и здесь он обязан стоять там же: иначе одно и
    то же место в двух окнах выглядело бы по-разному. Сам раздел в пути остаётся
    всегда, даже архивный, — это он и есть место статьи.
    """
    if not chain:
        return []
    path = [chain[0]]
    for node in chain[1:]:
        if node['status'] != 'active':
            break
        path.append(node)
    return list(reversed(path))


def guests_of_place(chain, grants):
    """Сколько людей читают раздел гостевой выдачей (без выдач на саму статью).

    Правило покрытия — то же, что в периметре (queries._GUEST_SECTIONS_CTE):
      * выдача на сам раздел берётся как есть, даже если он уже в архиве;
      * выдача на раздел выше доходит сюда, только если выдана с подразделами
        и все разделы между ними живые (сам выданный раздел может быть любым);
      * выдача пространства целиком покрывает его ЖИВЫЕ разделы.
    """
    if not chain:
        return set()
    root = chain[0]
    reach = {root['id']: False}          # id раздела → нужна ли выдача «с подразделами»
    for index in range(1, len(chain)):
        if chain[index - 1]['status'] != 'active':
            break
        reach[chain[index]['id']] = True

    users = set()
    for grant in grants or ():
        section_id, space_id = grant.get('section_id'), grant.get('space_id')
        if section_id is not None:
            if section_id in reach and (not reach[section_id]
                                        or grant.get('include_subsections')):
                users.add(grant['user_id'])
        elif space_id is not None:
            if root['status'] == 'active' and space_id == root['space_id']:
                users.add(grant['user_id'])
    return users


def _spaces(cursor, space_ids):
    """{id: пространство} — имя и значок, для дерева на экране."""
    ids = sorted({int(value) for value in space_ids if value})
    if not ids:
        return {}
    cursor.execute('SELECT sp.id, sp.name, sp.icon FROM wiki_spaces sp WHERE sp.id = ANY(%s)',
                   (ids,))
    return {row[0]: {'id': row[0], 'name': row[1], 'icon': row[2]}
            for row in cursor.fetchall()}


def _space_departments(cursor, space_ids):
    """{id пространства: {id отделов}} — граница пространства.

    Пространства без единой строки в ответе нет, и это значит «открыто всем
    отделам» — ровно так читает границу периметр (queries._SPACE_GATE_SQL).
    Строки берём как есть, без оглядки на то, жив ли отдел: периметр на
    departments здесь тоже не смотрит.
    """
    ids = sorted({int(value) for value in space_ids if value})
    if not ids:
        return {}
    cursor.execute(
        'SELECT sd.space_id, sd.department_id FROM wiki_space_departments sd'
        ' WHERE sd.space_id = ANY(%s)', (ids,))
    out = {}
    for space_id, department_id in cursor.fetchall():
        out.setdefault(space_id, set()).add(department_id)
    return out


def _public_departments(cursor, section_ids):
    """{id раздела: {id отделов}} — кому виден публичный раздел.

    Раздела без строк в ответе нет: список отделов не заведён, раздел публичен
    «как раньше» — для всех (queries._AUTO_SECTIONS_SQL).
    """
    ids = sorted({int(value) for value in section_ids if value})
    if not ids:
        return {}
    cursor.execute(
        'SELECT pd.section_id, pd.department_id FROM wiki_section_public_departments pd'
        ' WHERE pd.section_id = ANY(%s)', (ids,))
    out = {}
    for section_id, department_id in cursor.fetchall():
        out.setdefault(section_id, set()).add(department_id)
    return out


def _guest_grants(cursor, article_id, section_ids, space_ids):
    cursor.execute(_GUESTS_SQL, {
        'article': article_id,
        'sections': sorted(section_ids) or [-1],
        'spaces': sorted(space_ids) or [-1],
    })
    keys = ('user_id', 'section_id', 'article_id', 'space_id', 'include_subsections')
    return [dict(zip(keys, row)) for row in cursor.fetchall()]


def who_reads(cursor, article, chains, named_ids):
    """Все, кому статья открыта (wiki/readers.py: readers), по её цепочкам.

    chains    — цепочки ВСЕХ разделов статьи (section_chains), а не только
                названных смотрящему: права человека и сама видимость статьи
                считаются по каждому её разделу, и по одним названным у того,
                кто попал в список, они оказались бы занижены;
    named_ids — разделы, которые смотрящему можно назвать: по ним решается,
                кого из читателей назвать (поле listed).
    """
    live = {root: live_chain(chain) for root, chain in chains.items()}
    rule_sections = sorted({node['id'] for nodes in live.values() for node in nodes})
    by_section = {}
    if rule_sections:
        for rule in structure.list_section_rules(cursor, section_ids=rule_sections):
            by_section.setdefault(rule['section_id'], []).append(rule)
    reaching = {root: readers.reaching_rules(
        [rule for node in nodes for rule in by_section.get(node['id'], ())], nodes)
        for root, nodes in live.items()}

    space_ids = {chain[0]['space_id'] for chain in chains.values()}
    public = _public_departments(cursor, [
        chain[0]['id'] for chain in chains.values()
        if chain[0]['status'] == 'active' and chain[0]['visibility_scope'] == 'public'])
    grants = _guest_grants(
        cursor, article['id'],
        {node['id'] for chain in chains.values() for node in chain}, space_ids)
    article_rules = wiki_edit.list_article_rules(cursor, article['id'])

    people = readers.load_people(cursor)
    section_hits, article_hits = readers.matched_rules(
        cursor, people,
        section_rule_ids=[rule['id'] for rules in reaching.values() for rule in rules],
        article_rule_ids=[rule['id'] for rule in article_rules])

    # Редкие люди — настоящим расчётом по одному. Ручной режим доступа живёт
    # по своим правилам (выдача отдела раскрывается в разделы его пространств),
    # и пересказывать их здесь значило бы завести второй вычислитель. У
    # носителя мастер-ключа права на статью равны его способностям, а
    # недостающую способность могло поднять правило в любом разделе вики.
    manual_perimeter, capabilities_of = {}, {}
    for person in people:
        if readers.holds_master_key(person):
            if not all(person['role_capabilities'].get(name) for name in PERMISSION_COLUMNS):
                capabilities_of[person['user_id']] = readers.exact_capabilities(cursor, person)
        elif person['access_mode'] == 'manual':
            manual_perimeter[person['user_id']] = queries.allowed_section_ids(
                cursor, dict(person, capabilities=person['role_capabilities']),
                person['subjects'])

    return readers.readers(
        article, people,
        [{
            'id': root,
            'active': chain[0]['status'] == 'active',
            'space_id': chain[0]['space_id'],
            'named': root in named_ids,
            'reach_rules': reaching[root],
            'public': (public.get(root, set())
                       if chain[0]['status'] == 'active'
                       and chain[0]['visibility_scope'] == 'public' else None),
            'owner_user_id': chain[0]['owner_user_id'],
            'guests': guests_of_place(chain, grants),
        } for root, chain in chains.items()],
        space_departments=_space_departments(cursor, space_ids),
        section_hits=section_hits,
        article_rules=article_rules,
        article_hits=article_hits,
        article_guests={grant['user_id'] for grant in grants
                        if grant.get('article_id') is not None},
        manual_perimeter=manual_perimeter,
        capabilities_of=capabilities_of,
    )


def _name_key(person):
    return str(person['name'] or '').lower().replace('ё', 'е')


def describe(cursor, ctx, article, allowed_sections, *, with_people):
    """Справка по статье: где она лежит и кому открыта.

    article          — строка из wiki_articles.list_articles: тело статьи здесь
                       не нужно, и тянуть сотни килобайт ради справки незачем;
    allowed_sections — периметр смотрящего (queries.allowed_section_ids): по
                       нему решается, какие разделы статьи ему можно НАЗВАТЬ;
    with_people      — считать ли список людей (sees_readers). Без него в ответе
                       people = None, и ни одного запроса за людьми не уходит:
                       не показанное не должно ни ехать по сети, ни считаться.

    Раздел за периметром смотрящего в ответ не попадает — только счётчиком
    hidden_places. Статья лежит сразу в нескольких разделах, и одна и та же
    инструкция у СЗоВ и у ОП — обычное дело: супервайзеру СЗоВ незачем узнавать
    отсюда устройство чужой ветки. Тот же порядок держит поле «Разделы» в
    редакторе: скрытые разделы считаются, но не называются.

    Архивный раздел называем тому же, кому его отдаёт «Структура»
    (routes_structure.wiki_structure): управляющему структурой или доступами —
    любой, держателю ветки — свои подразделы, то есть разделы, чей РОДИТЕЛЬ
    лежит в выданной ему ветке (решение владельца 07.09.2026: «раз он заводит
    подразделы, он же их и убирает»). Остальным это тоже «ещё один раздел».
    """
    caps = ctx['capabilities']
    can_manage = bool(caps.get('can_manage_structure') or caps.get('can_manage_access'))
    section_ids = list(dict.fromkeys(
        int(value) for value in (article.get('section_ids') or ()) if value))
    chains = section_chains(cursor, section_ids)

    # Выданные ветки спрашиваем, только когда архивное место у статьи есть и
    # способностью оно не открыто: это отдельный запрос, а архив у статьи —
    # редкость.
    branches = None

    def sees_archived(chain):
        nonlocal branches
        if can_manage:
            return True
        if len(chain) < 2:
            return False
        if branches is None:
            branches = queries.manage_section_ids(cursor, ctx, ctx['subjects'])
        return chain[1]['id'] in branches

    shown, hidden = [], 0
    for section_id in section_ids:
        chain = chains.get(section_id)
        if not chain:
            hidden += 1
            continue
        archived = chain[0]['status'] != 'active'
        named = sees_archived(chain) if archived else section_id in allowed_sections
        if not named:
            hidden += 1
            continue
        shown.append((chain, archived))
    named_ids = {chain[0]['id'] for chain, _archived in shown}

    found = who_reads(cursor, article, chains, named_ids) if with_people else None
    spaces = _spaces(cursor, {chain[0]['space_id'] for chain, _archived in shown})

    places = []
    for chain, archived in shown:
        root = chain[0]
        places.append({
            'section_id': root['id'],
            'name': root['name'],
            'archived': archived,
            'space': spaces.get(root['space_id'])
                     or {'id': root['space_id'], 'name': None, 'icon': None},
            'path': [{'id': node['id'], 'name': node['name'],
                      'branch': bool(node['department_id'])}
                     for node in display_path(chain)],
        })
    # Порядок мест — по пространству и пути: тот же раздел обязан стоять на том
    # же месте при каждом открытии окна, а array_agg порядка не обещает.
    places.sort(key=lambda place: (place['space']['id'] or 0,
                                   [node['name'] or '' for node in place['path']]))

    return {
        'article': {
            'id': article['id'],
            'title': article.get('title'),
            'status': article.get('status'),
            # Статья «только по списку» и статья в строгом режиме правилами
            # раздела НЕ открываются: список людей у них собран из правил самой
            # статьи, и окно говорит об этом отдельной строкой.
            'by_list_only': bool(article.get('strict_mode')
                                 or article.get('visibility_mode') == 'restricted'),
        },
        'places': places,
        'hidden_places': hidden,
        # Права — строкой из букв (r c e p a d), а не шестью флагами: людей в
        # ответе сотни, и на флагах он вырос бы вдвое. None — список смотрящему
        # не положен; пустой список значил бы «статья не открыта никому».
        'people': None if found is None else [{
            'name': person['name'],
            'role': person['role'],
            'job_title': person['job_title'],
            'department': person['department'],
            'rights': ''.join(letter for letter, name in RIGHT_LETTERS
                              if person['permissions'].get(name)),
            'via': person['via'],
        } for person in sorted(found, key=_name_key) if person['listed']],
    }
