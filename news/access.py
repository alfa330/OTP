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
import re
from datetime import datetime, timedelta

from wiki import access as wiki_access
from wiki.access import ROLE_LEVELS, normalize_role, role_level_of  # noqa: F401  (реэкспорт)
# Периметр программы «Ограничитель Перезвона» — им же ограничен канал Oktell
# (см. channels_for_departments в конце файла). Модуль чистый, как и этот:
# ни flask, ни базы он не тянет.
from oktell_guard.access import SECTION_DEPARTMENT_CODE as OKTELL_DEPARTMENT_CODE

from .schema import (DEFAULT_CONFIRM_DELAY_SECONDS, DEFAULT_NEWS_KIND,
                     DEFAULT_PASS_SCORE_PERCENT, DEFAULT_PUBLISH_MODE,
                     MAX_CONFIRM_DELAY_SECONDS, MAX_SPENT_SECONDS,
                     MAX_SPREAD_MINUTES, MAX_TIME_LIMIT_SECONDS, MAX_WAVES,
                     MIN_PASS_SCORE_PERCENT, MIN_SPREAD_MINUTES, MIN_TIME_LIMIT_SECONDS,
                     MIN_WAVE_INTERVAL_MINUTES, NEWS_KINDS, PUBLISH_MODES,
                     QUIZ_MAX_OPTION_LENGTH, QUIZ_MAX_OPTIONS, QUIZ_MAX_PROMPT_LENGTH,
                     QUIZ_MAX_QUESTIONS, QUIZ_MIN_OPTIONS, QUIZ_MIN_QUESTIONS,
                     TRAINER_KEY_MAX_LENGTH)

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
                     target_roles=None, space_departments=None):
    """Почему автор не вправе выписать такой набор адресатов. None — вправе.

    rules                — список словарей {'subject_type', 'subject_id',
                           'subject_role', 'min_role_level'};
    subject_departments  — {(subject_type, subject_id): department_id} для
                           числовых адресатов, собранный вызывающим из базы;
    target_roles         — {user_id: role} для адресата-человека;
    space_departments    — отделы ПРОСТРАНСТВА, в котором пишут новость
                           (None — про пространство не спрашивали).

    Границы отдела и пространства СКЛАДЫВАЮТСЯ, а не заменяют друг друга — как
    в вике (wiki/structure.py: narrow_to_space): первая отвечает «чей это
    человек», вторая — «чьей компании эта вика». Супервайзер СЗоВ не адресует
    новость отделу продаж, а из «Тез» никто не адресует её Таксопаркам.

    Должность (`otp_role`) этой границей НЕ режется намеренно: она адресует
    людей по всей компании, и запретить её в пространстве значило бы отнять у
    директора «всем операторам» вовсе. Такую новость сужает сама граница
    пространства при показе (SPACE_MATCH_TEMPLATE): из «Тез» она дойдёт до
    операторов Тез КЦ, из «Таксопарков» — до операторов Таксопарков.

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
        # Граница пространства. Стоит ПОСЛЕ границы отдела, чтобы человек
        # сначала услышал про свой периметр, а не про чужую вику.
        if (space_departments is not None
                and subject_departments[key] not in set(space_departments)):
            return 'Этот адресат из другого пространства вики'

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


# ГРАНИЦА ПРОСТРАНСТВА — третья и последняя (решение владельца 18.09.2026:
# «чтобы по пространствам новости Таксопарков и Тез не смешивались»).
#
# Потолок должности отвечает «кому по чину», правило адресата — «чьим людям», а
# эта граница — «чьей КОМПАНИИ»: «Таксопарки» и «Тез» — две разные вики и два
# разных заказчика, и объявление одной из них не должно доезжать до другой
# просто потому, что супер-админ выбрал адресатом должность, а не отдел.
#
# Стоит ЗДЕСЬ, в общем шаблоне, а не поверх выборки окна: журнал редактора
# обязан считать адресатов ровно теми же правилами, иначе «подтвердили 12 из
# 30» считалось бы не по тем тридцати, кому окно показали.
#
# Три ветки «пусто = не сужаем» — не перестраховка, каждая отвечает за своё:
#   * space_id IS NULL — новость выпущена до этой границы или мимо вкладки
#     (см. news/schema.py). Спрятать её значило бы молча отнять у людей уже
#     показанное объявление;
#   * у пространства нет отделов — оно ничего про своих людей не сказало.
#     То же соглашение, что и у вики (wiki/queries.py: _SPACE_GATE_SQL);
#   * дальше — обычное «отдел человека выдан этому пространству».
SPACE_MATCH_TEMPLATE = """
    AND (
        p.space_id IS NULL
        OR NOT EXISTS (SELECT 1 FROM wiki_space_departments sd
                        WHERE sd.space_id = p.space_id)
        OR EXISTS (SELECT 1 FROM wiki_space_departments sd
                    WHERE sd.space_id = p.space_id
                      AND sd.department_id = ANY({departments}))
    )
"""


def _audience_match(*, role_level, departments, directions, groups, roles, user_id,
                    with_space):
    """Шаблон адресата, собранный под одну из двух сторон. Один на обе."""
    match = AUDIENCE_MATCH_TEMPLATE.format(
        role_level=role_level, departments=departments, directions=directions,
        groups=groups, roles=roles, rule_role=canon_role_sql('r.subject_role'),
        user_id=user_id,
    )
    if with_space:
        match += SPACE_MATCH_TEMPLATE.format(departments=departments)
    return match


# Выдача окна: субъекты зрителя посчитаны в питоне и приезжают параметрами.
AUDIENCE_MATCH_FOR_VIEWER = _audience_match(
    role_level='%(role_level)s',
    departments='%(departments)s',
    directions='%(directions)s',
    groups='%(groups)s',
    roles='%(roles)s',
    user_id='%(user_id)s',
    with_space=False,
)

# Журнал редактора: те же правила, но субъекты считает SQL по каждому
# сотруднику (CTE `v` в news/queries.py). Шаблон один на оба вопроса намеренно —
# разъедься они, журнал показывал бы не тех, кто видел окно, и перестал бы
# отвечать на вопрос «был ли человек проинформирован».
AUDIENCE_MATCH_FOR_REPORT = _audience_match(
    role_level='v.role_level',
    departments='v.department_ids',
    directions='v.direction_ids',
    groups='v.group_ids',
    roles='v.roles',
    user_id='v.id',
    with_space=False,
)

# Те же два, но с границей пространства. Отдельными значениями, а не флагом в
# каждом запросе: таблицу wiki_space_departments приносит ЧУЖОЙ пакет, и пока
# её нет (news/schema.py: space_ready), запросы обязаны уходить в базу вовсе без
# этой ветки — упоминание несуществующей таблицы валит запрос на разборе, то
# есть уронило бы окно у всех вошедших в портал.
AUDIENCE_MATCH_FOR_VIEWER_IN_SPACE = _audience_match(
    role_level='%(role_level)s',
    departments='%(departments)s',
    directions='%(directions)s',
    groups='%(groups)s',
    roles='%(roles)s',
    user_id='%(user_id)s',
    with_space=True,
)

AUDIENCE_MATCH_FOR_REPORT_IN_SPACE = _audience_match(
    role_level='v.role_level',
    departments='v.department_ids',
    directions='v.direction_ids',
    groups='v.group_ids',
    roles='v.roles',
    user_id='v.id',
    with_space=True,
)


def viewer_match(with_space):
    """Условие «эта новость адресована ЭТОМУ человеку» для выдачи окна."""
    return AUDIENCE_MATCH_FOR_VIEWER_IN_SPACE if with_space else AUDIENCE_MATCH_FOR_VIEWER


def report_match(with_space):
    """То же условие для журнала: субъекты считает SQL по каждому сотруднику."""
    return AUDIENCE_MATCH_FOR_REPORT_IN_SPACE if with_space else AUDIENCE_MATCH_FOR_REPORT


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


# ─────────────────────────────────────────────────────────────────────────────
# ТЕСТ В ОКНЕ НОВОСТИ
#
# Постановка «Вопросов операторов» (задача #321): «после ознакомления оператор
# проходит небольшой тест из 2–3 вопросов … при правильных ответах нажимает
# «Подтвердить», после чего плашка исчезает». Обе функции чистые: проверку
# теста при выпуске и проверку ответов при подтверждении можно тестировать без
# базы, а правило одно на ИИ-черновик, форму и сервер.
# ─────────────────────────────────────────────────────────────────────────────

def normalize_quiz(raw):
    """Тест из формы или от ИИ, приведённый к виду таблицы. (вопросы, отказ).

    Отказ — готовая строка для человека и называет НОМЕР вопроса: тест правят
    на экране, и «тест заполнен неверно» не сказало бы, где именно.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        return [], 'Тест не заполнен'
    quiz = []
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return [], 'Вопрос %d заполнен неверно' % number
        prompt = ' '.join(str(item.get('prompt') or '').split())[:QUIZ_MAX_PROMPT_LENGTH]
        if not prompt:
            return [], 'В вопросе %d нет текста' % number
        raw_options = item.get('options')
        if not isinstance(raw_options, (list, tuple)):
            return [], 'В вопросе %d нет вариантов ответа' % number
        options = [' '.join(str(option or '').split())[:QUIZ_MAX_OPTION_LENGTH]
                   for option in raw_options]
        if not all(options):
            return [], 'В вопросе %d есть пустой вариант ответа' % number
        if not QUIZ_MIN_OPTIONS <= len(options) <= QUIZ_MAX_OPTIONS:
            return [], 'В вопросе %d должно быть от %d до %d вариантов' % (
                number, QUIZ_MIN_OPTIONS, QUIZ_MAX_OPTIONS)
        if len({option.lower() for option in options}) != len(options):
            return [], 'В вопросе %d варианты повторяются' % number
        correct = item.get('correct')
        # bool — подкласс int: True прошёл бы как «второй вариант».
        if isinstance(correct, bool) or not isinstance(correct, int) \
                or not 0 <= correct < len(options):
            return [], 'В вопросе %d не отмечен верный вариант' % number
        quiz.append({'prompt': prompt, 'options': options, 'correct': correct})
    if not QUIZ_MIN_QUESTIONS <= len(quiz) <= QUIZ_MAX_QUESTIONS:
        return [], 'В тесте должно быть от %d до %d вопросов' % (QUIZ_MIN_QUESTIONS,
                                                                 QUIZ_MAX_QUESTIONS)
    return quiz, None


def quiz_mistakes(answer_key, answers):
    """Вопросы, на которые ответили неверно или не ответили вовсе.

    answer_key — [(id вопроса, индекс верного варианта)], из базы;
    answers    — {id вопроса: индекс выбранного}, из тела запроса. Ключи в JSON
                 приезжают строками, поэтому сверяем оба написания.

    Верные ответы клиенту не отдаются вовсе (news/queries.py: pending_for_user),
    поэтому проверка возможна только здесь, и она же — единственная граница:
    подтверждение из консоли без ответов упрётся в тот же список ошибок.
    """
    given = answers if isinstance(answers, dict) else {}
    wrong = []
    for question_id, correct in answer_key:
        value = given.get(str(question_id), given.get(question_id))
        if isinstance(value, bool):
            value = None
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = None
        if value != int(correct):
            wrong.append(int(question_id))
    return wrong


def clean_answers(answer_key, answers):
    """Ответы человека, приведённые к {id вопроса: индекс} — для журнала попыток.

    Только вопросы ИЗ ЭТОГО теста и только числа: тело запроса приходит от
    клиента, и складывать его в базу как есть значило бы хранить чужой JSON под
    видом ответов. Неотвеченный вопрос в словарь не попадает вовсе — «не
    ответил» и «ответил неверно» это разные вещи, и аналитика их различает.
    """
    given = answers if isinstance(answers, dict) else {}
    clean = {}
    for question_id, _correct in (answer_key or ()):
        value = given.get(str(question_id), given.get(question_id))
        if isinstance(value, bool):
            continue
        try:
            clean[str(int(question_id))] = int(value)
        except (TypeError, ValueError):
            continue
    return clean


def normalize_pass_score(raw, default=DEFAULT_PASS_SCORE_PERCENT):
    """Проходной результат из формы, в процентах. Мусор — умолчание.

    Режем в диапазон, а не отказываем: поле числовое, и «120» означает «хочу
    строже некуда», а не ошибку, которую стоит показывать отдельным экраном.
    Форма старого бандла балла не присылает вовсе — там умолчание и есть
    прежнее поведение теста (все ответы верны).
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(MIN_PASS_SCORE_PERCENT, min(100, value))


def needed_correct(total, pass_score_percent=DEFAULT_PASS_SCORE_PERCENT):
    """Сколько вопросов надо взять верно при таком проходном балле.

    Порог автор ставит в ПРОЦЕНТАХ (так написано в ТЗ), а считается он в
    вопросах: «нужно не меньше 2 из 3» человек проверяет на пальцах, а 66% ему
    пришлось бы делить в уме.

    Округление ВВЕРХ: 80% от пяти вопросов — это четыре, а не три с хвостом. И
    хотя бы один верный нужен всегда, иначе «проходной балл 1%» означал бы
    тест, который проходит пустой бланк.
    """
    count = int(total or 0)
    if count <= 0:
        return 0
    percent = normalize_pass_score(pass_score_percent)
    return max(1, min(count, -(-count * percent // 100)))


def quiz_result(answer_key, answers, pass_score_percent=DEFAULT_PASS_SCORE_PERCENT):
    """Итог попытки: {correct, total, needed, passed, wrong}.

    Единственное место, где решается «сдал или нет», — и у окна портала, и у
    окна поверх клиента АТС, и у кнопки «Проверить». При проходном балле 100
    ответ тот же, что до ТЗ #300: непройденной попытку делает любая ошибка.

    `wrong` остаётся в итоге, но НАРУЖУ не отдаётся (решение владельца
    21.09.2026): подсветка вопроса вернула бы подбор ответа переключением
    одного варианта. Он нужен журналу и разбору — тому, кто смотрит в логи, а
    не в окно.
    """
    total = len(answer_key or ())
    wrong = quiz_mistakes(answer_key, answers)
    correct = total - len(wrong)
    needed = needed_correct(total, pass_score_percent)
    return {'correct': correct, 'total': total, 'needed': needed,
            'passed': correct >= needed, 'wrong': wrong}


# ─────────────────────────────────────────────────────────────────────────────
# ТРЕНАЖЁР И ОБЯЗАТЕЛЬНОСТЬ ПРОХОЖДЕНИЯ (задача #342)
#
# «При создании новости должна быть возможность прикрепить тренажёр или тест…
# Если тест обязательный, оператор не должен иметь возможности закрыть новость,
# не пройдя тест. Если необязательный — может ознакомиться без прохождения».
# ─────────────────────────────────────────────────────────────────────────────

_TRAINER_KEY = re.compile(r'^[a-z0-9][a-z0-9-]*$')


def normalize_trainer_key(raw):
    """(ключ, отказ). Пустое значение — тренажёра нет, и это не ошибка.

    Сценарии живут в коде фронта (trainers/registry.js), поэтому сервер
    проверяет только форму ключа. Незнакомый, но правильный по форме ключ
    окно покажет честной заглушкой «тренажёр недоступен», а не упадёт.
    """
    key = str(raw or '').strip()
    if not key:
        return None, None
    if len(key) > TRAINER_KEY_MAX_LENGTH or not _TRAINER_KEY.match(key):
        return None, 'Неизвестный тренажёр'
    return key, None


def must_pass(*, pass_required, has_quiz, has_trainer):
    """Обязано ли прохождение держать подтверждение новости.

    Нечего проходить — нечего и требовать: признак pass_required у новости без
    теста и тренажёра ничего не значит (он остаётся от формы и по умолчанию
    TRUE).
    """
    return bool(pass_required) and bool(has_quiz or has_trainer)


def outstanding_passes(*, pass_required, has_quiz, has_trainer, quiz_passed, trainer_passed):
    """Что человеку ещё осталось пройти, чтобы подтвердить новость. Список видов.

    Порядок — порядок в окне: сначала тренажёр (он показывает, КАК делать),
    потом тест (он проверяет, понял ли). Необязательное прохождение сюда не
    попадает никогда — его отсутствие подтверждение не держит.
    """
    if not must_pass(pass_required=pass_required, has_quiz=has_quiz, has_trainer=has_trainer):
        return []
    left = []
    if has_trainer and not trainer_passed:
        left.append('trainer')
    if has_quiz and not quiz_passed:
        left.append('quiz')
    return left


# ─────────────────────────────────────────────────────────────────────────────
# КУДА ОТПРАВИТЬ ОБЪЯВЛЕНИЕ (решение владельца 18.09.2026)
#
# 'icore'  — портал: окно «Новость дня» у каждого, кому новость адресована;
# 'oktell' — клиент АТС: то же объявление рисует поверх окна Oktell программа
#            «Ограничитель Перезвона» (oktell_recall_guard), она же снимает
#            человека с линии на время чтения.
#
# Канал ОДИН, а не два флажка рядом. Показанное в обоих местах объявление
# человек подтвердит дважды и дважды пройдёт тест, а журнал «Кто прочитал» у
# новости один — вторая отметка легла бы поверх первой и соврала про время.
# ─────────────────────────────────────────────────────────────────────────────

CHANNELS = ('icore', 'oktell')

# Умолчание — портал: его видят все, а программа стоит у части операторов.
DEFAULT_CHANNEL = 'icore'


def normalize_channel(raw, default=DEFAULT_CHANNEL):
    """Канал из формы. Незнакомое значение — умолчание, а не отказ.

    Форма старого бандла канала не присылает вовсе, и новость обязана уйти
    туда, куда уходила до этой задачи, — в портал.
    """
    value = str(raw or '').strip().lower()
    return value if value in CHANNELS else default


def channels_for_departments(department_codes):
    """Какие каналы предлагать в этом пространстве. Всегда хотя бы портал.

    Oktell — только там, где программа вообще стоит: её периметр — отдел СЗоВ
    (oktell_guard/access.py: SECTION_DEPARTMENT_CODE), и код оттуда взят
    константой, а не переписан сюда. В «Тез» такой программы нет, поэтому
    вкладка там про выбор и не спрашивает: единственный вариант — не выбор, а
    лишний вопрос на экране.
    """
    codes = {str(code or '').strip().lower() for code in (department_codes or ())}
    if OKTELL_DEPARTMENT_CODE in codes:
        return list(CHANNELS)
    return [DEFAULT_CHANNEL]


# ─────────────────────────────────────────────────────────────────────────────
# ВРЕМЯ НА ЧТЕНИЕ И НА ТЕСТ В ОКНЕ OKTELL (решение владельца 23.09.2026)
#
# Окно поверх клиента АТС держит оператора в «Тренинге», вне линии, и лимит
# отвечает на вопрос «сколько на это отведено». Три решения владельца, и все
# три держатся здесь, а не в форме:
#
#   * настройка есть ТОЛЬКО у объявления в Oktell — в портале человек на линии,
#     и засиживаться в окне ему незачем;
#   * это ПОТОЛОК, а не задержка кнопки: задержка остаётся своей настройкой;
#   * время вышло — НИЧЕГО не прерывается: окно показывает перерасход, журнал
#     отмечает, на сколько человек превысил время.
# ─────────────────────────────────────────────────────────────────────────────

def normalize_time_limit(raw):
    """Лимит из формы, в секундах. None — «без ограничения».

    Не отказ, а ближайшее допустимое — как у задержки кнопки: число приезжает
    из листалки, и придираться к нему ценой отказа в публикации незачем.
    Ноль и мусор — «без ограничения»: лимит в ноль секунд означал бы, что
    каждый оператор превысил время ещё до того, как открыл окно.
    """
    if raw is None or raw is False or raw == '':
        return None
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return max(MIN_TIME_LIMIT_SECONDS, min(MAX_TIME_LIMIT_SECONDS, seconds))


def time_limits(*, channel, is_mandatory, has_quiz, read_limit, quiz_limit):
    """Какие лимиты у новости останутся после сохранения: (чтение, тест).

    Только у объявления в Oktell и только у обязательного: необязательное в
    клиент АТС не уходит вовсе (oktell_guard/routes.py: /news), и лимит у него
    был бы настройкой окна, которого не будет. Время на тест — только когда
    тест есть. Лишнее не отвергаем, а снимаем: автор, переключивший канал на
    портал, не должен получать отказ за строку, которую форма уже спрятала.
    """
    if channel != 'oktell' or not is_mandatory:
        return None, None
    return read_limit, (quiz_limit if has_quiz else None)


def time_limit_refusal(*, read_limit, confirm_delay_seconds):
    """Текст отказа или None. Время на чтение не короче задержки кнопки.

    Иначе превышение было бы у каждого, кто прочитал бы объявление мгновенно:
    кнопка «Ознакомлен» загорается позже, чем кончается отведённое время.
    """
    delay = int(confirm_delay_seconds or 0)
    if read_limit and delay and int(read_limit) < delay:
        return ('Время на чтение короче задержки кнопки «Прочитал» — '
                'оператор не уложится, даже если прочтёт сразу')
    return None


def clean_spent(raw):
    """Потраченные секунды от агента. None — агент не прислал замер.

    Старые сборки агента поля не шлют, и «ноль секунд» вместо «не знаем»
    записал бы им, что они прочитали объявление мгновенно.
    """
    if raw is None or raw == '':
        return None
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return None
    return max(0, min(MAX_SPENT_SECONDS, seconds))


def overtime(spent, limit):
    """На сколько секунд человек превысил отведённое время.

    None — сравнивать не с чем: лимита у новости нет или замера нет (старый
    агент, окно ещё не открывали). Ноль — уложился. Одна функция на журнал,
    сводку и выгрузку в Excel: «превысил» не может значить в них разное.
    """
    if not limit or spent is None:
        return None
    return max(0, int(spent) - int(limit))


# ─────────────────────────────────────────────────────────────────────────────
# СОСТОЯНИЕ СОТРУДНИКА В ЖУРНАЛЕ (ТЗ #300, п.11.2 и п.13)
#
# «Рекомендуемые статусы: не выходил на смену после публикации; ожидает
# ознакомления; ознакомился, тест не пройден; проходит повторно; успешно
# пройден».
#
# Правило ОДНО на три витрины: журнал у редактора, сводка для руководителя и
# выгрузка в Excel. Две копии разошлись бы ровно там, где по ним принимают
# решение о человеке.
#
# ЧЕСТНАЯ ОГОВОРКА ПРО СМЕНУ. «Нет смен после публикации» мы говорим только когда
# источник посещаемости по этому кругу людей вообще отвечает (у кого-то из них
# часы есть). Молчит источник — человек просто «не открывал объявление»:
# обвинять в прогуле по отсутствию данных нельзя.
# ─────────────────────────────────────────────────────────────────────────────

PERSON_STATUSES = ('passed', 'done', 'retrying', 'failed', 'pending', 'absent', 'not_seen')

# Должности по-русски — их печатает выгрузка в Excel. На экране ту же подпись
# даёт newsShared.js (ROLE_TITLES), и совпадение сверяет тест: файл и журнал
# обязаны называть должность одинаково, иначе их не свести.
ROLE_TITLES = {
    'super_admin': 'коммерческий директор',
    'admin': 'руководитель',
    'sv': 'супервайзер',
    'supervisor': 'супервайзер',
    'trainer': 'тренер',
    'operator': 'оператор',
    'trainee': 'стажёр',
    'hr_manager': 'HR',
    'accounting_manager': 'бухгалтерия',
    'marketing_manager': 'маркетинг',
}

# Подписи нужны СЕРВЕРУ — их печатает выгрузка в Excel. Фронт держит свою копию
# (WikiNews.jsx: STATUS_LABELS), и совпадение сверяет тест: файл и экран обязаны
# называть одно состояние одним словом.
#
# БЕЗ РОДА. «Ознакомился», «не выходил», «не открывал» — глаголы мужского рода,
# а стоят они рядом с именем конкретного человека. По имени пол не угадывают,
# поэтому формулировки — о событии, а не о человеке: «ознакомление
# подтверждено», «нет смен после публикации», «объявление не открыто».
PERSON_STATUS_LABELS = {
    'passed': 'Успешно пройден',
    'done': 'Ознакомление подтверждено',
    'retrying': 'Проходит повторно',
    'failed': 'Тест не пройден',
    'pending': 'Ожидает ознакомления',
    'absent': 'Нет смен после публикации',
    'not_seen': 'Объявление не открыто',
}


def person_status(*, has_quiz, shown_at, confirmed_at, quiz_passed_at,
                  attempts=0, worked_after=False, attendance_known=False):
    """Состояние одного адресата. Код из PERSON_STATUSES.

    Порядок ветвей — порядок вопросов, которые задаёт руководитель: сначала
    «сделал ли», потом «застрял ли», и только в конце «а был ли вообще».
    """
    tries = int(attempts or 0)
    if confirmed_at:
        if not has_quiz or quiz_passed_at:
            return 'passed' if has_quiz else 'done'
        return 'retrying' if tries >= 2 else 'failed'
    if quiz_passed_at:
        # Тест сдан, а подтверждения нет: так бывает у необязательного теста,
        # пройденного во вкладке «Новости». Ознакомления это не заменяет.
        return 'pending'
    if tries:
        return 'retrying' if tries >= 2 else 'failed'
    if shown_at:
        return 'pending'
    if attendance_known and not worked_after:
        return 'absent'
    return 'not_seen'


def report_summary(rows, *, has_quiz=False, has_trainer=False):
    """Сводка по новости (ТЗ п.11.1 и п.13). Чистая функция над строками журнала.

    Знаменатель — НЫНЕШНИЕ адресаты: «из скольких» отвечает на вопрос «сколько
    человек это касается сейчас». Подтвердившие, которых в круге уже нет,
    считаются отдельной строкой и в проценты не идут — иначе прохождение
    оказалось бы выше ста.

    Среднее число попыток — только по тем, кто хоть раз отвечал: деля на всех,
    мы получили бы «0,4 попытки», то есть число, которого ни у кого нет.
    """
    addressed = [row for row in rows if row.get('in_audience')]
    assigned = len(addressed)
    confirmed = sum(1 for row in addressed if row.get('confirmed_at'))
    quiz_passed = sum(1 for row in addressed if row.get('quiz_passed_at'))
    trainer_passed = sum(1 for row in addressed if row.get('trainer_passed_at'))
    # «Не прошли тест» — это те, кто ПРОБОВАЛ и не сдал, а не все несдавшие:
    # человек, который до теста ещё не дошёл, его не заваливал.
    quiz_failed = sum(1 for row in addressed
                      if not row.get('quiz_passed_at') and int(row.get('attempts') or 0))
    tries = [int(row.get('attempts') or 0) for row in addressed if row.get('attempts')]
    done = quiz_passed if has_quiz else confirmed
    return {
        'assigned': assigned,
        'confirmed': confirmed,
        'not_confirmed': assigned - confirmed,
        'quiz_passed': quiz_passed,
        'quiz_failed': quiz_failed,
        'trainer_passed': trainer_passed,
        'confirmed_outside': sum(1 for row in rows
                                 if row.get('confirmed_at') and not row.get('in_audience')),
        # Процент прохождения: по тесту, если он есть, иначе по подтверждениям.
        'percent': round(done * 100 / assigned) if assigned else 0,
        'avg_attempts': round(sum(tries) / len(tries), 1) if tries else 0,
        'needs_attention': sum(1 for row in addressed
                               if row.get('status') not in ('passed', 'done')),
        # Превысили отведённое время в окне Oktell — на чтении или на тесте.
        # Перерасход у строки считает overtime(); без лимитов здесь ноль.
        'overtime': sum(1 for row in addressed
                        if (row.get('read_over_seconds') or 0) > 0
                        or (row.get('quiz_over_seconds') or 0) > 0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ТИП НОВОСТИ (ТЗ #300, п.5)
#
# Таблица из постановки дословно:
#
#   Информационная — ознакомление подтверждением, тест необязателен, работу не
#                    блокирует;
#   Важная         — ознакомление обязательное, тест настраиваемый;
#   Критичная      — ознакомление обязательное, тест ОБЯЗАТЕЛЕН, и работа
#                    заперта до успешного прохождения.
#
# ЗАЧЕМ ТИП, ЕСЛИ ЕСТЬ ДВА ТУМБЛЕРА. Автор думает не про «обязательность» и
# «обязательность прохождения» по отдельности — он думает «насколько это
# важно». Два независимых тумблера позволяли собрать и бессмысленное
# («необязательная новость с обязательным тестом»), и опасное («критичное
# изменение, которое закрывают крестиком»). Тип отвечает на вопрос один раз, а
# тумблеры остаются тем, чем были, — способом исполнения.
#
# ПРАВИЛО ЖИВЁТ ЗДЕСЬ И ТОЛЬКО ЗДЕСЬ. Сервер зовёт kind_flags при создании и
# правке, форма рисует по нему же (src/components/wiki/WikiNews.jsx: NEWS_KINDS).
# Разъехавшись, они дали бы новость, выглядящую в форме не тем, чем она
# записана в базу.
# ─────────────────────────────────────────────────────────────────────────────

# None у pass_required означает «решает автор» — это и есть «настраиваемая»
# строка таблицы ТЗ у важной новости.
KIND_RULES = {
    'info':      {'mandatory': False, 'quiz_required': False, 'pass_required': False},
    'important': {'mandatory': True,  'quiz_required': False, 'pass_required': None},
    'critical':  {'mandatory': True,  'quiz_required': True,  'pass_required': True},
}


def normalize_kind(raw, default=DEFAULT_NEWS_KIND):
    """Тип из формы. Незнакомое значение — умолчание, а не отказ.

    Так же, как канал: форма старого бандла типа не присылает, и новость
    обязана лечь такой, какой её положили бы до этой задачи, — обязательной.
    """
    value = str(raw or '').strip().lower()
    return value if value in NEWS_KINDS else default


def kind_of(*, is_mandatory, pass_required, has_quiz):
    """Тип новости, выведенный из того, как она себя ведёт.

    Нужен там, где колонки типа ещё нет (schema.plan_ready сказал «нет») и
    старым строкам, которым его проставляет бэкфилл. Правило то же, что в DDL:
    одно поведение — один тип, и в двух местах оно записано одинаково.
    """
    if not is_mandatory:
        return 'info'
    if pass_required and has_quiz:
        return 'critical'
    return 'important'


def kind_flags(kind, *, pass_required=True):
    """(обязательность, обязательность прохождения), которые навязывает тип."""
    rule = KIND_RULES[normalize_kind(kind)]
    forced = rule['pass_required']
    return rule['mandatory'], (bool(pass_required) if forced is None else forced)


def kind_refusal(kind, *, has_quiz):
    """Отказ, если тип требует того, чего в новости нет. Иначе None.

    Критичная без теста — это важная, названная критичной: блокировать работу
    «до успешного прохождения» нечем. Молча понизить тип нельзя — автор
    выпустил бы объявление не тем, каким собрал.
    """
    if normalize_kind(kind) == 'critical' and not has_quiz:
        return ('Критичная новость выпускается с тестом: добавьте вопросы '
                'или выберите тип «Важная»')
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ПЛАНИРОВЩИК ПУБЛИКАЦИИ (ТЗ #300, п.8)
#
# «Чтобы при запуске обновления не блокировать одновременно всех операторов
# выбранной аудитории»: обязательное объявление снимает человека с линии, и
# выпуск на весь отдел разом — это отдел, одновременно вышедший из очереди.
#
# Три режима: сразу, отложенно и растяжкой по волнам. Всё, что ниже, — чистая
# арифметика над списком адресатов: её зовут и сервер при выпуске, и роут
# предварительного расчёта, и крон. Общая функция здесь означает, что
# обещанное автору «12 волн примерно по 10 человек» и то, что ляжет в базу, —
# один и тот же счёт.
# ─────────────────────────────────────────────────────────────────────────────

def normalize_publish_mode(raw, default=DEFAULT_PUBLISH_MODE):
    """Режим запуска из формы. Незнакомое — «сразу»."""
    value = str(raw or '').strip().lower()
    return value if value in PUBLISH_MODES else default


def post_state(status, scheduled_at, scheduled_armed=True):
    """Состояние новости для витрин: draft | scheduled | published | archived.

    «Запланирована» своего статуса не имеет (см. news/schema.py): это черновик
    со ВЗВЕДЁННЫМ запуском. Выводится оно здесь — одной функцией на список,
    карточку и крон, чтобы «Запланирована» везде значило одно и то же.

    Взведённость обязательна вместе с датой: время запуска живёт в черновике с
    того момента, как автор выбрал его в форме, а взводит запуск только нажатие
    «Опубликовать». Считай мы по одной дате — черновик, отложенный «на всякий
    случай», крон выпустил бы сам.
    """
    if status == 'draft' and scheduled_at and scheduled_armed:
        return 'scheduled'
    return status


def wave_count(spread_minutes, wave_interval_minutes):
    """Сколько волн уместится в период. Не меньше одной.

    Делим период на интервал БЕЗ +1: последняя волна включается за интервал до
    конца периода, то есть внутри него. Пример из ТЗ — два часа с шагом десять
    минут — даёт ровно двенадцать волн, как там и написано.
    """
    period = int(spread_minutes or 0)
    step = int(wave_interval_minutes or 0)
    if period <= 0 or step <= 0:
        return 1
    return max(1, min(MAX_WAVES, period // step))


def plan_waves(*, user_ids, start_at, spread_minutes, wave_interval_minutes):
    """Расписание: [(user_id, номер волны, когда включится)].

    Аудитория делится на равные части номером по порядку: `i * волн // всего`.
    Это тот же приём, что раскладывает n предметов по k корзинам без остатка в
    одной из них — 125 человек на 12 волн лягут как 11, 11, 11, 11, 11, 10, 10…,
    а не 11×11 и одна из четырёх.

    Порядок списка ЗНАЧИМ и приходит из запроса (по имени): волна должна быть
    воспроизводимой, а не зависеть от того, в каком порядке Postgres сегодня
    вернул строки.
    """
    ids = list(user_ids or ())
    total = len(ids)
    if not total:
        return []
    waves = wave_count(spread_minutes, wave_interval_minutes)
    step = max(1, int(wave_interval_minutes or 0))
    plan = []
    for index, user_id in enumerate(ids):
        wave_no = index * waves // total
        plan.append((user_id, wave_no, start_at + timedelta(minutes=wave_no * step)))
    return plan


def spread_preview(*, recipients, start_at, spread_minutes, wave_interval_minutes):
    """Расчёт, который показывается ДО подтверждения публикации (ТЗ п.8.4).

    «Это позволит администратору оценить влияние обязательной новости на
    доступность операторов до её запуска» — то есть считать надо ровно то, что
    потом и произойдёт. Поэтому число волн берётся той же wave_count, что и у
    plan_waves, а не считается в форме второй формулой.

    Завершение — включение ПОСЛЕДНЕЙ волны, а не конец периода: администратор
    спрашивает «когда объявление дойдёт до всех», и ответ на это — момент
    последней волны.
    """
    count = max(0, int(recipients or 0))
    waves = wave_count(spread_minutes, wave_interval_minutes)
    step = max(1, int(wave_interval_minutes or 0))
    return {
        'recipients': count,
        'waves': waves,
        'per_wave': -(-count // waves) if count else 0,
        'interval_minutes': step,
        'spread_minutes': int(spread_minutes or 0),
        'starts_at': start_at,
        'ends_at': start_at + timedelta(minutes=(waves - 1) * step),
    }


def schedule_refusal(*, mode, scheduled_at, spread_minutes, wave_interval_minutes,
                     publishing, now=None):
    """Отказ по режиму запуска либо None.

    publishing=False — новость сохраняют черновиком, и время запуска не
    взводится вовсе: проверять «дата в прошлом» у того, что никуда не уйдёт,
    значило бы мешать автору собирать объявление заранее.

    Прошедшее время у ВЫПУСКА отвергаем: отложенный запуск «на вчера» крон
    выполнит в ближайшую минуту, и автор получит немедленную публикацию там,
    где просил отложенную, — молча, как сгоревший срок показа 18.09.2026.
    """
    if not publishing:
        return None
    mode = normalize_publish_mode(mode)
    if mode == 'now':
        return None
    moment = now or datetime.now()
    if mode == 'later' and not scheduled_at:
        return 'Укажите дату и время запуска'
    if scheduled_at and scheduled_at <= moment:
        return 'Время запуска уже прошло — укажите будущее время'
    if mode != 'spread':
        return None
    period = int(spread_minutes or 0)
    step = int(wave_interval_minutes or 0)
    if not MIN_SPREAD_MINUTES <= period <= MAX_SPREAD_MINUTES:
        return 'Период растяжки — от %d минут до %d часов' % (
            MIN_SPREAD_MINUTES, MAX_SPREAD_MINUTES // 60)
    if step < MIN_WAVE_INTERVAL_MINUTES:
        return 'Интервал между волнами — не меньше %d минут' % MIN_WAVE_INTERVAL_MINUTES
    if step > period:
        return 'Интервал между волнами не может быть длиннее самого периода'
    return None
