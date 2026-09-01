# -*- coding: utf-8 -*-
"""Кто вправе писать новость и кому её вправе адресовать.

Модуль чистый: ни database, ни flask, ни bot_schedule2 — только функции над
данными, как wiki/access.py. Его можно импортировать в тестах напрямую.

── ГЛАВНОЕ ПРАВИЛО РАЗДЕЛА ──────────────────────────────────────────────────
«Новость он может опубликовать только тем, кто ниже него, но не выше»
(постановка владельца). Правило держится ДВУМЯ независимыми границами, и обе
обязательны — каждая по отдельности дырявая:

  ПОТОЛОК ДОЛЖНОСТИ (audience_max_role_level) — «кому по чину». Считается из
  должности автора при публикации и накладывается на выдачу поверх правил.
  Без него правило «отдел СЗоВ» без порога адресовало бы новость и
  супервайзерам, и руководителю этого отдела: min_role_level в модели вики
  сужает адресатов СНИЗУ («не ниже такой-то должности»), а нам нужно сверху.

  ГРАНИЦА ОТДЕЛА — «чьим людям». Супервайзер СЗоВ не адресует новость отделу
  продаж, чужой группе и чужому направлению.

Обе взяты у вики (wiki/access.py) целиком, а не написаны заново: вопрос там и
здесь буквально один — «кого этот человек вправе адресовать». Вторая лестница
рядом с первой однажды разошлась бы, и разошлась бы молча.
"""

import json

from wiki import access as wiki_access
from wiki.access import ROLE_LEVELS, normalize_role, role_level_of  # noqa: F401  (реэкспорт)

from .schema import DEFAULT_CONFIRM_DELAY_SECONDS, MAX_CONFIRM_DELAY_SECONDS

# Длина заголовка — колонка VARCHAR(255); режем на входе, чтобы отказ был
# внятным, а не «value too long for type character varying(255)».
MAX_TITLE_LENGTH = 255


def publish_ceiling(otp_role, *, is_wiki_admin=False):
    """До какого уровня должности человек вправе адресовать новость.

    None — не вправе публиковать вовсе, и по этому же признаку раздел
    «Новости» не показывается: вопрос «вижу ли я раздел» и вопрос «кому вправе
    писать» здесь один и тот же, а два признака на один вопрос дают вкладку,
    на которую сервер отвечает 403.

    Лестница — GRANT_CEILING вики:
        супервайзер → оператор (тренер пропущен намеренно, см. wiki/access.py);
        руководитель → свой отдел целиком, включая других руководителей;
        супер-админ → все.
    Тренер и оператор не публикуют: их в таблице нет.
    """
    return wiki_access.grant_ceiling(otp_role, is_wiki_admin=is_wiki_admin)


def publish_departments(otp_role, *, headed_department_ids=(), department_id=None,
                        is_wiki_admin=False):
    """Отделы, людям которых человек вправе адресовать новость.

    None — без границы (супер-админ, администратор вики). Точная копия правила
    выдачи доступа вики (routes_structure._grant_departments): «супервайзер и
    руководитель работают только со своим отделом».
    """
    if normalize_role(otp_role) == 'super_admin':
        return None
    if is_wiki_admin:
        return None
    own = {int(value) for value in (headed_department_ids or ()) if value}
    if department_id:
        own.add(int(department_id))
    return sorted(own)


def may_target_subject(subject_type, *, publish_departments, subject_department=None):
    """Проходит ли адресат под границу отдела автора.

    Делегирует wiki_access.may_grant_to_subject, включая его правило про
    'otp_role': роль адресует людей ПО ВСЕЙ КОМПАНИИ, мимо любого отдела, и
    автору с границей она недоступна вовсе. Для новостей это не ограничение, а
    ровно то, что нужно: «всем операторам компании» от супервайзера одного
    отдела — это и есть та рассылка, которой быть не должно. Свой отдел,
    направление, группу и человека он адресует как обычно.
    """
    return wiki_access.may_grant_to_subject(
        subject_type, grant_departments=publish_departments,
        subject_department=subject_department)


def may_target_role(subject_role, ceiling):
    """Вправе ли автор адресовать новость носителям этой должности."""
    if ceiling is None:
        return False
    role = canon_role(subject_role)
    # Незнакомая роль не проходит: её ноль от role_level_of оказался бы ниже
    # любого потолка, то есть опечатка в должности проходила бы проверку,
    # которая обязана её отклонить. Тот же приём, что в may_grant_guest_to.
    if role not in ROLE_LEVELS:
        return False
    return ROLE_LEVELS[role] <= ceiling


def normalize_delay(value):
    """Задержка кнопки «Прочитал» в секундах, приведённая к допустимому.

    Не ошибка, а ближайшее допустимое: значение приезжает из формы, и
    придираться к нему ценой отказа в публикации незачем. Мусор — умолчание.
    """
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_CONFIRM_DELAY_SECONDS
    return max(0, min(MAX_CONFIRM_DELAY_SECONDS, seconds))


def normalize_title(value):
    return str(value or '').strip()[:MAX_TITLE_LENGTH]


def audience_refusal(rules, *, ceiling, departments, subject_departments,
                     target_roles=None):
    """Почему автор не вправе выписать такой набор адресатов. None — вправе.

    rules                — список словарей {'subject_type', 'subject_id',
                           'subject_role', 'min_role_level'};
    subject_departments  — {(subject_type, subject_id): department_id} для
                           числовых адресатов, собранный вызывающим из базы;
    target_roles         — {user_id: role} для адресата-человека.

    Возвращает готовую строку отказа — её показывают автору, поэтому она
    называет КОНКРЕТНОГО адресата, а не «недостаточно прав».
    """
    if ceiling is None:
        return 'Новости публикуют супервайзер и выше'
    if not rules:
        return 'Укажите, кому адресована новость'

    for rule in rules:
        subject_type = rule.get('subject_type')
        if subject_type not in ('department', 'direction', 'group', 'otp_role', 'user'):
            return 'Неизвестный вид адресата: %s' % (subject_type,)

        if subject_type == 'otp_role':
            if not may_target_subject(subject_type, publish_departments=departments):
                return ('Должность адресует людей по всей компании — '
                        'выберите свой отдел, направление, группу или человека')
            if not may_target_role(rule.get('subject_role'), ceiling):
                return 'Новость адресуется только тем, кто ниже вас по должности'
            continue

        key = (subject_type, rule.get('subject_id'))
        if key not in subject_departments:
            return 'Адресат не найден'
        if not may_target_subject(subject_type, publish_departments=departments,
                                  subject_department=subject_departments[key]):
            return 'Этот адресат относится к другому отделу'

        if subject_type == 'user':
            role = (target_roles or {}).get(rule.get('subject_id'))
            # effective_role_level, а НЕ role_level_of: в базе встречается
            # написание 'supervisor', которого нет в ROLE_LEVELS, и его ноль
            # проходил бы любой потолок. Супервайзер добавлял такого коллегу
            # в адресаты, форма принимала — а окно ему не показывалось никогда,
            # потому что сторона показа считает его уровнем 30. Молчаливый
            # отказ: автор уверен, что человека предупредил.
            if effective_role_level(role) > ceiling:
                return 'Новость адресуется только тем, кто ниже вас по должности'

        # Порог снизу не может оказаться выше потолка сверху: правило «не ниже
        # руководителя» у супервайзера адресовало бы пустоту, и это была бы
        # молчаливая публикация в никуда.
        min_level = rule.get('min_role_level')
        if min_level is not None and int(min_level) > ceiling:
            return 'Порог должности выше того, кому вы вправе писать'

    return None


# ─────────────────────────────────────────────────────────────────────────────
# СОВПАДЕНИЕ АДРЕСАТА С ЧЕЛОВЕКОМ — одно определение на весь раздел.
#
# Его используют ОБА вопроса: «что показать этому человеку» (выдача окна) и
# «кому эта новость ушла» (журнал редактора). Разъедься они — журнал показывал
# бы не тех, кто видел окно, и весь смысл журнала («был ли сотрудник
# проинформирован») пропал бы.
#
# Потолок новости стоит ЗДЕСЬ, рядом с правилами, а не поверх выборки в одном
# из двух мест: забыть его во втором месте — значит показать новость тому, кому
# автор не вправе был её адресовать.
# ─────────────────────────────────────────────────────────────────────────────
AUDIENCE_MATCH_TEMPLATE = """
    (
        p.audience_max_role_level IS NULL
        OR {role_level} <= p.audience_max_role_level
    )
    AND EXISTS (
        SELECT 1 FROM news_audience_rules r
         WHERE r.news_id = p.id
           AND (
                (r.subject_type = 'department' AND r.subject_id = ANY({departments}))
             OR (r.subject_type = 'direction'  AND r.subject_id = ANY({directions}))
             OR (r.subject_type = 'group'      AND r.subject_id = ANY({groups}))
             OR (r.subject_type = 'otp_role'   AND {rule_role} = ANY({roles}))
             OR (r.subject_type = 'user'       AND r.subject_id = {user_id})
           )
           AND (r.min_role_level IS NULL OR {role_level} >= r.min_role_level)
    )
"""

# Приведение написания должности к канону — ОДНО выражение на обе стороны
# сравнения. В базе встречаются и 'supervisor', и 'sv', и 'superadmin' без
# подчёркивания (wiki/access.py: _ROLE_ALIASES); сравнивать сырые строки значит
# промахнуться мимо адресата ровно там, где написание разошлось.
ROLE_CANON = {
    'supervisor': 'sv',
    'superadmin': 'super_admin',
    'super admin': 'super_admin',
}

# Шкала для SQL. Ключи уже в каноне, поэтому 'supervisor' здесь не нужен —
# его приводит ROLE_CANON до обращения к шкале.
SQL_ROLE_LEVELS = dict(ROLE_LEVELS)


def canon_role_sql(expression):
    """SQL-выражение «должность в каноне». Требует параметра %(role_canon)s."""
    lowered = "lower(btrim(coalesce(%s, '')))" % (expression,)
    return "COALESCE(%%(role_canon)s::jsonb ->> %s, %s)" % (lowered, lowered)


def role_level_sql(canon_expression):
    """SQL-выражение «уровень должности». Требует параметра %(role_levels)s.

    Незнакомая должность даёт ноль — так же, как role_level_of в питоне.
    """
    return "COALESCE((%%(role_levels)s::jsonb ->> %s)::int, 0)" % (canon_expression,)


# Выдача окна: субъекты зрителя посчитаны в питоне и приезжают параметрами.
AUDIENCE_MATCH_FOR_VIEWER = AUDIENCE_MATCH_TEMPLATE.format(
    role_level='%(role_level)s',
    departments='%(departments)s',
    directions='%(directions)s',
    groups='%(groups)s',
    roles='%(roles)s',
    rule_role=canon_role_sql('r.subject_role'),
    user_id='%(user_id)s',
)

# Журнал редактора: те же правила, но субъекты считает SQL по каждому
# сотруднику (CTE `v` в news/queries.py). Шаблон один на оба вопроса намеренно —
# разъедься они, журнал показывал бы не тех, кто видел окно, и перестал бы
# отвечать на вопрос «был ли человек проинформирован».
AUDIENCE_MATCH_FOR_REPORT = AUDIENCE_MATCH_TEMPLATE.format(
    role_level='v.role_level',
    departments='v.department_ids',
    directions='v.direction_ids',
    groups='v.group_ids',
    roles='v.roles',
    rule_role=canon_role_sql('r.subject_role'),
    user_id='v.id',
)


def canon_role(otp_role):
    """Должность в каноне — та же таблица, что и в SQL (ROLE_CANON)."""
    role = normalize_role(otp_role)
    return ROLE_CANON.get(role, role)


def effective_role_level(otp_role):
    """Уровень должности зрителя, с приведением 'supervisor' к 'sv'.

    В ROLE_LEVELS роли 'supervisor' нет (её уровень 0), хотя в CHECK на
    users.role она есть. Здесь это не мелочь: ноль проходит ЛЮБОЙ потолок
    сверху, то есть носитель такого написания получал бы новости, адресованные
    операторам, — ровно то, что правило «только тем, кто ниже» запрещает.

    Незнакомая должность остаётся нулём: ранжировать её нечем, а под правило
    адресата (отдел, направление, группа, человек) она всё равно обязана
    попасть отдельно — одного лишь уровня, чтобы увидеть новость, не хватает.
    """
    return ROLE_LEVELS.get(canon_role(otp_role), 0)


def viewer_roles(otp_role):
    """Должности, под правило которых подпадает зритель — в каноне.

    Раскрытия вниз по иерархии здесь НЕТ намеренно (в отличие от
    wiki_access.expand_otp_roles): новость идёт вниз, и правило «операторам» не
    должно доставать до супервайзера, а «супервайзерам» — до руководителя.
    Раскрытие вики отвечает на обратный вопрос — «что человеку открыто».
    """
    return [canon_role(otp_role) or '']


def audience_params(subjects, user_id, otp_role):
    """Параметры подстановки для AUDIENCE_MATCH_FOR_VIEWER.

    Пустые списки заменяются заведомо непопадающим значением: `= ANY('{}')`
    в постгресе не ошибка, но и не совпадение, а NULL сравнивать нельзя.
    Отдел, направление и группы приезжают готовыми из
    wiki_access.collect_subjects — второй раз выводить их из профиля было бы
    вторым источником истины. Должность и уровень считаются ЗДЕСЬ: в вике они
    отвечают на обратный вопрос (что человеку открыто СНИЗУ), и взять их
    оттуда значило бы получить ноль у супервайзера и раскрытие вниз у роли.
    """
    return {
        'user_id': user_id,
        'departments': subjects['department'] or [-1],
        'directions': subjects['direction'] or [-1],
        'groups': subjects['group'] or [-1],
        'roles': viewer_roles(otp_role),
        'role_level': effective_role_level(otp_role),
        'role_canon': json.dumps(ROLE_CANON),
    }
