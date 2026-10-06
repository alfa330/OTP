# -*- coding: utf-8 -*-
"""Кому открыта статья — поимённо.

Решение владельца 06.10.2026: в окне «Расположение и доступ» показывать не
правила, а людей — «кому открыта статья, просто выводить списком… можно будет
просматривать, кому и как открыт раздел».

Список людей — это периметр чтения, посчитанный НАОБОРОТ. Периметр отвечает на
вопрос «что видит этот человек», и считается он пятью-восемью запросами на
человека; здесь вопрос «кто видит эту статью», и спрашивать периметр у каждого
из трёхсот сотрудников значило бы две тысячи запросов на одно открытие окна.
Поэтому расчёт множественный: один запрос на всех людей, один — на совпадение
правил с ними, остальное в памяти.

── Как обратный расчёт не расходится с прямым ───────────────────────────────
Он собран из тех же деталей, что и настоящий периметр, — своих копий правил
здесь нет:

  * совпадение правила с человеком — ТЕКСТ queries.SUBJECT_MATCH, в котором
    параметры одного человека заменены колонками набора людей;
  * субъекты человека — access.collect_subjects и queries.subject_params;
  * способности — access.resolve_capabilities (и queries.load_capabilities там,
    где способность могла поднять только выдача правилом);
  * права на статью — access.resolve_article_permissions;
  * ручной режим доступа — настоящий queries.allowed_section_ids по человеку:
    таких людей единицы, а правила у режима свои.

Своё здесь одно — сборка: кто из людей попадает в периметр раздела и проходит
условия видимости статьи. Это зеркало queries._AUTO_SECTIONS_SQL и
articles._VISIBLE_ARTICLES_SQL, и держит его в согласии с оригиналом тест,
который сверяет список с настоящим периметром по каждому человеку
(tests/test_wiki_article_access.py).

── Что список означает ──────────────────────────────────────────────────────
Тех, кто откроет статью, КОГДА ОНА ОПУБЛИКОВАНА. Статус в расчёт не входит
намеренно: редактор черновика спрашивает, кто прочитает его текст после выхода,
а не кто видит черновик сейчас (автор и те, кто вправе публиковать, — об этом
окно говорит отдельной строкой).

Не входят в список те, кому раздел «Вики» не выдан вовсе (тумблер отдела,
wiki/routes.py): правило на таких людей в базе лежать может, а войти им некуда.
"""

import json

from . import access as wiki_access
from . import queries
from .guests import NOW_SQL
from .schema import PERMISSION_COLUMNS

# Все действующие сотрудники — одним запросом, с тем же, что
# queries._ACCESS_CONTEXT_SQL собирает на одного: возглавляемые отделы,
# действующие группы (и оператором, и супервайзером), роли вики, режим доступа.
# Поля и условия обязаны совпадать с оригиналом дословно; за этим следит тест,
# сравнивающий строку отсюда с load_access_context по каждому человеку.
#
# Отсев по статусу — тот же, что у входа в портал (bot_schedule2: login):
# уволенным дверь закрыта, а отпуск и больничный читать не мешают. Выдан ли
# человеку логин, список не спрашивает: это вопрос учётной записи, а не доступа
# — получив её, человек откроет статью без единой правки правил.
_PEOPLE_SQL = """
SELECT u.id, u.name, u.role, u.department_id, d.name, u.direction_id,
       NULLIF(btrim(COALESCE(u.job_title, '')), ''),
       COALESCE(d.wiki_enabled, TRUE),
       COALESCE((SELECT array_agg(h.id)
                   FROM departments h
                  WHERE h.head_user_id = u.id AND h.is_active), '{}'),
       COALESCE((SELECT array_agg(m.group_id)
                   FROM (SELECT gom.group_id
                           FROM group_operator_memberships gom
                           JOIN groups g ON g.id = gom.group_id AND g.status = 'active'
                          WHERE gom.operator_id = u.id
                            AND gom.start_date <= CURRENT_DATE
                            AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
                          UNION
                         SELECT gsm.group_id
                           FROM group_supervisor_memberships gsm
                           JOIN groups g ON g.id = gsm.group_id AND g.status = 'active'
                          WHERE gsm.supervisor_id = u.id
                            AND gsm.start_date <= CURRENT_DATE
                            AND (gsm.end_date IS NULL OR gsm.end_date >= CURRENT_DATE)) m),
                '{}'),
       COALESCE((SELECT json_agg(row_to_json(w))
                   FROM (SELECT r.id, r.code, r.can_read, r.can_create, r.can_edit,
                                r.can_delete, r.can_publish, r.can_approve,
                                r.can_manage_users, r.can_manage_structure,
                                r.can_manage_access
                           FROM wiki_roles r
                           JOIN wiki_user_roles ur ON ur.wiki_role_id = r.id
                          WHERE ur.user_id = u.id) w), '[]'),
       COALESCE((SELECT s.access_mode
                   FROM wiki_user_access_settings s
                  WHERE s.user_id = u.id), 'auto'),
       EXISTS (SELECT 1
                 FROM wiki_guest_access g
                WHERE g.user_id = u.id
                  AND g.revoked_at IS NULL
                  AND g.expires_at > """ + NOW_SQL + """)
  FROM users u
  LEFT JOIN departments d ON d.id = u.department_id
 WHERE COALESCE(u.status, '') NOT IN ('fired', 'dismissal')
"""

_PEOPLE_KEYS = ('user_id', 'name', 'otp_role', 'department_id', 'department_name',
                'direction_id', 'job_title', 'wiki_enabled', 'headed_department_ids',
                'group_ids', 'wiki_roles', 'access_mode', 'has_guest_access')

# Параметры queries.SUBJECT_MATCH и их типы — колонки набора людей в запросе
# совпадений. Имена обязаны совпадать с ключами queries.subject_params: из них
# собирается и сам набор, и подстановка в текст условия.
_SUBJECT_COLUMNS = (
    ('user_id', 'int'), ('departments', 'int[]'), ('headed', 'int[]'),
    ('directions', 'int[]'), ('groups', 'int[]'), ('roles', 'text[]'),
    ('wiki_roles', 'int[]'), ('role_level', 'int'), ('job_title', 'text'),
)

# Порядок пометок «откуда доступ» в строке человека: от самого личного к общему.
# Те же слова знает интерфейс (articleAccess.js: VIA_CAPTION).
VIA_ORDER = ('user', 'department_head', 'group', 'direction', 'wiki_role', 'owner',
             'manual', 'guest', 'article_rule', 'author', 'article_owner', 'wiki_admin')


def load_people(cursor):
    """Все действующие сотрудники с субъектами и способностями должности.

    Строка человека повторяет queries.load_access_context ключ в ключ — её можно
    подать настоящим функциям периметра как контекст (так считается ручной
    режим). Сверх того: имя, отдел, субъекты и способности должности.
    """
    cursor.execute(_PEOPLE_SQL)
    people = []
    for row in cursor.fetchall():
        person = dict(zip(_PEOPLE_KEYS, row))
        person['user_id'] = int(person['user_id'])
        person['wiki_enabled'] = person['wiki_enabled'] is not False
        person['headed_department_ids'] = list(person['headed_department_ids'] or [])
        person['group_ids'] = list(person['group_ids'] or [])
        person['wiki_roles'] = list(person['wiki_roles'] or [])
        person['access_mode'] = person['access_mode'] or 'auto'
        person['has_guest_access'] = bool(person['has_guest_access'])
        person['subjects'] = wiki_access.collect_subjects(
            user_id=person['user_id'], otp_role=person['otp_role'],
            department_id=person['department_id'],
            headed_department_ids=person['headed_department_ids'],
            direction_id=person['direction_id'], group_ids=person['group_ids'],
            wiki_role_ids=[role.get('id') for role in person['wiki_roles']],
            job_title=person['job_title'],
        )
        person['role_capabilities'] = wiki_access.resolve_capabilities(
            person['otp_role'], person['wiki_roles'],
            is_department_head=bool(person['headed_department_ids']),
        )
        people.append(person)
    return people


def is_super_admin(person):
    return wiki_access.normalize_role(person['otp_role']) == 'super_admin'


def manages_access(person):
    """Несёт ли человек способность «управление доступами» — мастер-ключ вики.

    Способности управления из правил не поднимаются (в правиле живут только
    шесть прав содержимого), поэтому способностей должности здесь достаточно.
    """
    return bool(person['role_capabilities'].get('can_manage_access'))


def holds_master_key(person):
    """Видит ли человек все разделы без правил (queries.allowed_section_ids).

    Два разных основания, и расчёт статьи их различает: супер-админ — по РОЛИ,
    администратор вики — по способности. У супер-админа с назначенной ролью
    вики способности может и не быть (роли вики заменяют должностные), и тогда
    разделы он видит все, а запрет правилом статьи на него действует.
    """
    return is_super_admin(person) or manages_access(person)


def may_enter(person):
    """Пускает ли человека сам раздел «Вики» — зеркало гейта в wiki/routes.py.

    Тумблер «раздел выдан отделу» закрывает дверь целиком; не касается он
    супер-админа, управляющего структурой и гостя. Подтверждение QR сюда не
    входит: оно про сессию, а не про право.
    """
    return (person['wiki_enabled'] or is_super_admin(person)
            or bool(person['role_capabilities'].get('can_manage_structure'))
            or person['has_guest_access'])


def subject_match_for_people(alias='p'):
    """Текст queries.SUBJECT_MATCH для НАБОРА людей.

    Параметры одного человека (%(departments)s, %(role_level)s…) заменяются
    колонками набора — и условие совпадения остаётся тем же самым текстом, а
    не его пересказом. Новый параметр в оригинале здесь не потеряется молча:
    запрос с неподставленным параметром упадёт, а тест поймает это раньше.
    """
    text = queries.SUBJECT_MATCH
    for name, _kind in _SUBJECT_COLUMNS:
        text = text.replace('%%(%s)s' % name, '%s.%s' % (alias, name))
    return text


def _hits_sql(table, condition):
    columns = ', '.join('%s %s' % pair for pair in _SUBJECT_COLUMNS)
    return (
        'WITH p AS (SELECT * FROM jsonb_to_recordset(%(subjects)s::jsonb) AS x('
        + columns + '))\n'
        'SELECT r.id, p.user_id\n'
        '  FROM ' + table + ' r\n'
        '  JOIN p ON (' + subject_match_for_people() + ')\n'
        ' WHERE ' + condition
    )


def matched_rules(cursor, people, *, section_rule_ids=(), article_rule_ids=()):
    """Какие правила на кого действуют: ({id правила раздела: {люди}}, {…статьи}).

    Одним запросом на вид правил и на всех людей сразу. Условие совпадения —
    общее с периметром (subject_match_for_people), субъекты — те же, что
    периметр подставляет параметрами (queries.subject_params).
    """
    section_hits, article_hits = {}, {}
    if not people:
        return section_hits, article_hits
    subjects = json.dumps([queries.subject_params(person['subjects'], person['user_id'])
                           for person in people])
    for table, rule_ids, hits in (
            ('wiki_section_access_rules', section_rule_ids, section_hits),
            ('wiki_article_access_rules', article_rule_ids, article_hits)):
        wanted = sorted({int(value) for value in rule_ids})
        if not wanted:
            continue
        cursor.execute(_hits_sql(table, 'r.id = ANY(%(rules)s)'),
                       {'subjects': subjects, 'rules': wanted})
        for rule_id, user_id in cursor.fetchall():
            hits.setdefault(rule_id, set()).add(user_id)
    return section_hits, article_hits


def reaching_rules(rules, chain):
    """Правила, действующие в разделе: его собственные и спустившиеся сверху.

    chain — живая цепочка раздела, от него к корню. Правило предка доходит,
    только если выдано «вместе с подразделами». Права при этом идут вместе с
    правилом целиком (queries._SECTION_RIGHTS_CTE), а на чтение раздел
    открывают лишь те из них, где отмечено чтение (_AUTO_SECTIONS_SQL).
    """
    depth_of = {node['id']: index for index, node in enumerate(chain or ())}
    out = []
    for rule in rules or ():
        depth = depth_of.get(rule['section_id'])
        if depth is None:
            continue
        if depth and not rule.get('grant_subsections'):
            continue
        out.append(rule)
    return out


def exact_capabilities(cursor, person):
    """Способности человека настоящим расчётом (queries.load_capabilities).

    Нужен там, где недостающую способность могла поднять выдача правилом в
    ЛЮБОМ разделе вики, а не только в разделах этой статьи: у носителя
    мастер-ключа права на статью равны его способностям.
    """
    context = dict(person)
    return dict(queries.load_capabilities(cursor, context, person['subjects']))


def _capabilities(person, rules):
    """Способности человека для расчёта прав на эту статью.

    Тот же союз, что в queries.load_capabilities: должность плюс выписанное
    правилами. Из выписанного берём правила этой статьи и её разделов — других
    прав расчёту взять неоткуда, и чем человеку ещё выдали в других разделах,
    на результате не сказывается. В ручном режиме правила способность не
    поднимают вовсе.
    """
    if person['access_mode'] == 'manual':
        return person['role_capabilities']
    granted = {name: any(rule.get(name) for rule in rules) for name in PERMISSION_COLUMNS}
    return wiki_access.merge_capabilities(
        person['role_capabilities'], wiki_access.capabilities_from_grants(granted))


# Обычный путь к статье — по отделу, должности или публичности раздела: отдел и
# должность человека и так стоят в его строке, и пометка здесь была бы шумом.
_PLAIN = frozenset(('department', 'otp_role', 'public'))
# У этих адресатов есть имя, которое стоит назвать: «группа «Основа»».
_NAMED = frozenset(('group', 'direction', 'wiki_role'))
_READ = frozenset(('can_read',))
_AUTHOR = frozenset(('can_read', 'can_edit'))


def _rule_source(rule):
    """Правило как источник доступа: (вид адресата, имя, выданные права)."""
    kind = rule['subject_type']
    return (kind, rule.get('subject_label') if kind in _NAMED else None,
            frozenset(name for name in PERMISSION_COLUMNS if rule.get(name)))


def _captions(sources):
    """Пометки «откуда доступ» для строки человека.

    Называем только то, что требует объяснения. Если человек читает статью
    обычным путём, пометка нужна лишь источнику, который добавил ему прав:
    оператор, состоящий ещё и в группе с тем же чтением, остаётся без пометки,
    а оператор с личным правилом на правку получает «лично». Сам обычный путь
    под это правило не подпадает никогда — к самому себе он ничего не добавляет.
    """
    plain = [flags for kind, _label, flags in sources if kind in _PLAIN]
    covered = frozenset().union(*plain) if plain else frozenset()
    rank = {kind: index for index, kind in enumerate(VIA_ORDER)}
    shown, seen = [], set()
    for kind, label, flags in sorted(
            sources, key=lambda item: (rank.get(item[0], len(rank)), str(item[1] or '').lower())):
        if (kind, label) in seen or (plain and not flags - covered):
            continue
        seen.add((kind, label))
        shown.append({'kind': kind, 'label': label or None})
    return shown


def readers(article, people, places, *, space_departments, section_hits,
            article_rules, article_hits, article_guests,
            manual_perimeter=None, capabilities_of=None):
    """Кто откроет статью и с какими правами — по строке на человека.

    places — ВСЕ разделы статьи, в том числе не названные смотрящему:
        id, active, space_id, named, reach_rules, public (None или множество
        отделов; пустое — всем), owner_user_id, guests (множество людей).
    space_departments — {пространство: отделы}; без записи пространство открыто.
    manual_perimeter  — {человек в ручном режиме: его настоящие разделы}.
    capabilities_of   — {человек: способности настоящим расчётом}, где нужны.

    Возвращает всех, кому статья открыта. Поле listed — можно ли человека
    НАЗВАТЬ смотрящему: читающий только через раздел, которого смотрящий не
    видит, в список не выходит (тот же порядок, что у самих разделов).
    """
    mode = article.get('visibility_mode') or 'inherit'
    strict = bool(article.get('strict_mode'))
    by_sections = mode == 'inherit'
    manual_perimeter = manual_perimeter or {}
    capabilities_of = capabilities_of or {}

    out = []
    for person in people:
        if not may_enter(person):
            continue
        uid = person['user_id']
        super_admin = is_super_admin(person)
        wiki_admin = manages_access(person)
        master = super_admin or wiki_admin
        manual = person['access_mode'] == 'manual'
        departments = set(person['subjects']['department'])

        inside_any = named_inside = open_space_any = False
        rules, via = [], []
        for place in places:
            allowed = space_departments.get(place['space_id'])
            open_space = not allowed or bool(departments & allowed)
            open_space_any = open_space_any or open_space
            matched = [rule for rule in place['reach_rules']
                       if uid in section_hits.get(rule['id'], ())]

            # Источники чтения мимо правил: публичность, владение, гостевая выдача.
            reads = []
            if place['active'] and open_space:
                public = place['public']
                if public is not None and (not public or departments & public):
                    reads.append(('public', None, _READ))
                if place['owner_user_id'] == uid:
                    reads.append(('owner', None, _READ))
            if uid in place['guests']:
                reads.append(('guest', None, _READ))

            if master:
                # Мастер-ключ: все живые разделы, мимо правил, границы и режима.
                inside = place['active']
                reads = []
            elif manual:
                inside = place['id'] in manual_perimeter.get(uid, ())
                if inside and not reads:
                    reads.append(('manual', None, _READ))
            else:
                inside = bool(reads) or (place['active'] and open_space and any(
                    rule.get('can_read') for rule in matched))

            if not inside:
                continue
            inside_any = True
            # Права из правил раздела граница пространства режет без гостевой
            # щели (queries._SECTION_RIGHTS_CTE): гостю из чужого отдела
            # правило на его роль прав не добавляет.
            if open_space:
                rules.extend(matched)
            # Путём к статье раздел называем, только когда он её и открывает:
            # у статьи «по списку» читателя называет правило самой статьи, и
            # «владелец раздела» рядом с ним объяснял бы то, чего нет.
            if place['named'] and by_sections and not strict:
                named_inside = True
                via.extend(reads)
                if open_space and not master and not manual:
                    via.extend(_rule_source(rule) for rule in matched)

        mine = [rule for rule in article_rules if uid in article_hits.get(rule['id'], ())]
        grants = [rule for rule in mine if rule['mode'] == 'grant']
        granted = any(rule.get('can_read') for rule in grants)
        denied = any(rule['mode'] == 'deny' and rule.get('can_read') for rule in mine)
        author = article.get('author_id') == uid
        owner = article.get('owner_user_id') == uid
        guest = uid in article_guests

        # Зеркало articles._VISIBLE_ARTICLES_SQL без условий статуса.
        if not (wiki_admin or (by_sections and inside_any) or granted or author or owner
                or guest):
            continue
        if denied and not wiki_admin:
            continue
        # Граница пространства для самой статьи: её не проходит ни правило
        # статьи, ни авторство — только раздел в периметре, именная гостевая
        # выдача или пространство, открытое отделу человека.
        if not (super_admin or not places or inside_any or guest or open_space_any):
            continue
        if strict and not (super_admin or granted or author):
            continue

        capabilities = capabilities_of.get(uid) or _capabilities(person, rules + grants)
        permissions = wiki_access.permissions_only(wiki_access.resolve_article_permissions(
            capabilities=capabilities, visibility_mode=mode, strict_mode=strict,
            section_rules=rules, article_rules=mine, otp_role=person['otp_role'],
            is_article_owner=author or owner))
        # Человек в списке — значит, читает: чтение даёт и публичность раздела,
        # и гостевая выдача, о которых расчёт прав на запись не знает.
        permissions['can_read'] = True

        if grants:
            if by_sections and not strict:
                # Поверх раздела правило статьи — исключение, и названо оно как
                # есть. У статьи «по списку» оно и есть обычный путь, поэтому
                # там его называет адресат: отдел, группа, лично.
                via.append(('article_rule', None, frozenset(
                    name for name in PERMISSION_COLUMNS
                    if any(rule.get(name) for rule in grants))))
            else:
                via.extend(_rule_source(rule) for rule in grants)
        if author:
            via.append(('author', None, _AUTHOR))
        elif owner:
            via.append(('article_owner', None, _AUTHOR))
        if guest:
            via.append(('guest', None, _READ))
        if master:
            # Мастер-ключ объясняет доступ целиком: правило статьи или группа,
            # под которые его носитель заодно подпадает, ничего к нему не
            # добавляют. У супер-админа роль и так стоит в строке.
            via = [] if super_admin else [('wiki_admin', None, _READ)]

        out.append({
            'user_id': uid,
            'name': person['name'],
            'role': person['otp_role'],
            'job_title': person['job_title'],
            'department': person['department_name'],
            'permissions': permissions,
            'via': _captions(via),
            # Носитель мастер-ключа называется всегда: он читает статью не
            # через раздел, а поверх всех разделов.
            'listed': bool(named_inside or granted or author or owner or guest or master),
        })
    return out
