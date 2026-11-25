# -*- coding: utf-8 -*-
"""Отклики hh.kz (ТЗ 5.2): что приносит мост на шлюзе и как это читает портал. Чистая логика.

Откуда отклики
--------------
API hh.ru отдаёт отклики работодателю только с платным доступом к API и приложением, прошедшим модерацию.
Поэтому их носит мост на шлюзе (cdr_bridge/hh_bridge.py, владелец 23.11.2026): под входом владельца в кабинет
работодателя hh.kz он через случайную паузу (её называет портал — rules.hh_poll_seconds) смотрит счётчики откликов
вакансий и, если они сдвинулись, читает свежие отклики той же выдачей, что страница «Отклики» сайта. Отклик у hh —
тема (topic): кандидат + вакансия, со своим номером.

Порталу мост присылает (POST /api/hiring/hh/agent/push, подпись шлюза):

    {"version": "hh/1.1.0", "session": "ok" | "login_required" | "blocked" | "error", "detail": "…",
     "pause_sec": 1800   (только когда кабинет не читается: свой отступ моста),
     "account": {"manager": "…", "employer": "…", "employer_id": "…"},
     "vacancies": [{"id": "139079696", "name": "…", "area": "Алматы", "responses": 152}]   (null — не перечитывал),
     "items": [{"topic_id": "…", "vacancy_id": "…", "vacancy_name": "…",
                "responded_at": "2026-11-23T12:30:00+03:00", "last_name": "…", "first_name": "…",
                "middle_name": "…", "phone": "+7 (701) 234-56-78", "city": "Алматы", "resume_hash": "…",
                "resume": {"title": "…", "experience": [...], "education": [...], …}   (с hh/1.3.0, без контактов),
                "letter": "сопроводительное письмо или null"}]}

Портал отвечает исходом по каждой теме, настройками (с какого момента отклики переносятся и какие вакансии
выключены — их мост не читает вовсе) и паузой до следующего опроса (next_poll_sec). Настройки руководителя
(hiring_integrations.settings у кода hh):

    {"since": "2026-11-23T12:08:00",
     "vacancies": {"139508734": {"on": false}, "139079696": {"on": true, "vacancy_id": 2},
                   "138923446": {"on": true, "since": "2026-11-24T10:15:00"}}}

Вакансия hh, которой в настройках нет, переносится без вакансии iCORE: новые вакансии hh идут сами (владелец
23.11.2026). Какие отслеживать, руководитель выбирает тумблером (владелец 23.11.2026: «Добавь в настройки возможность
выбрать какие вакансии отслеживать»); включённая после выключения — с минуты включения (since у вакансии). Последний доклад моста (кто вошёл, какие вакансии видит, когда был на связи) — в cursor той же строки.
"""

import re
from datetime import datetime

from . import rules

PLATFORM = 'hh'
MAX_ITEMS = 100
PAUSE_MAX_SECONDS = 4 * 3600
# Состояние входа моста в кабинет hh.kz. Не «ok» — интеграция с ошибкой, руководителям тревога.
SESSION_OK = 'ok'
SESSION_TEXTS = {
    'login_required': 'Мост потерял вход в кабинет hh.kz — новые отклики не приходят. Нужно войти заново',
    'blocked': 'hh.kz не пускает мост (защита сайта) — новые отклики не приходят',
    'error': 'hh.kz отвечает мосту с ошибкой — новые отклики не приходят',
}
SESSIONS = (SESSION_OK,) + tuple(SESSION_TEXTS)
# Исходы темы: created/repeat/employee — как у _intake_one; остальные — почему отклик не перенесён.
OUTCOMES = ('created', 'repeat', 'employee', 'known', 'vacancy_off', 'before_since', 'no_phone', 'invalid')

# Номера тем и вакансий hh — 9–10 цифр; больше 18 не влезло бы в BIGINT, и доклад моста падал бы целиком.
_DIGITS = re.compile(r'^\d{1,18}$')
_HASH = re.compile(r'^[0-9a-f]{16,64}$')
_RESUME_HOST = 'https://hh.kz'


def _text(value, limit):
    text = ' '.join(str(value or '').split())
    return text[:limit] if text else None


def _digits(value):
    text = str(value if value is not None else '').strip()
    return text if _DIGITS.match(text) else None


def parse_moment(value):
    """«2026-11-23T12:30:00.37+03:00» (время hh — московское, с поясом) → наивное время Алматы. Без пояса — уже Алматы."""
    text = str(value or '').strip()
    if not text:
        return None
    if len(text) >= 5 and text[-5] in '+-' and text[-3] != ':':
        text = text[:-2] + ':' + text[-2:]
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(rules.ALMATY).replace(tzinfo=None)
    return moment


def resume_url(resume_hash, vacancy_id, topic_id):
    """Резюме кандидата в кабинете работодателя — с откликом (так ссылку собирает сам сайт: vacancyId и t)."""
    if not resume_hash or not _HASH.match(resume_hash):
        return None
    return '%s/resume/%s?vacancyId=%s&t=%s' % (_RESUME_HOST, resume_hash, vacancy_id, topic_id)


def clean_item(raw):
    """Одна тема от моста → поля для приёма или None (не разобрать: нет номера темы, вакансии или времени отклика)."""
    if not isinstance(raw, dict):
        return None
    topic_id = _digits(raw.get('topic_id'))
    vacancy_id = _digits(raw.get('vacancy_id'))
    responded_at = parse_moment(raw.get('responded_at'))
    if not topic_id or not vacancy_id or responded_at is None:
        return None
    full_name = ' '.join(part for part in (_text(raw.get(key), 80) for key in ('last_name', 'first_name', 'middle_name'))
                         if part)
    resume_hash = str(raw.get('resume_hash') or '').strip().lower()
    return {
        'topic_id': int(topic_id),
        'vacancy_id': vacancy_id,
        'vacancy_name': _text(raw.get('vacancy_name'), 200),
        'responded_at': responded_at,
        'full_name': full_name[:200],
        'phone': _text(raw.get('phone'), 40),
        'city': _text(raw.get('city'), 120),
        'resume_url': resume_url(resume_hash, vacancy_id, topic_id),
        'resume_hash': resume_hash if _HASH.match(resume_hash) else None,
        'resume': clean_resume(raw.get('resume')),
        'letter': _long_text(raw.get('letter'), LETTER_MAX),
    }


# ─── Резюме и сопроводительное письмо отклика ──────────────────────────────────
# Владелец 23.11.2026: «надо бы сделать так что бы все резюме и письмо человека который обратился отобразились сразу в
# карточке» — и выбрал «Полное резюме и письмо»: мост открывает новый отклик (в hh он «Просмотрен») и приносит резюме без
# контактов. Здесь — проверка формы и подписи к кодам hh (как на сайте hh), чтобы карточка показывала готовый текст.

LETTER_MAX = 10000
_SHORT, _DESCRIPTION, _ABOUT = 300, 4000, 6000
RESUME_LABELS = {
    'gender': {'male': 'мужчина', 'female': 'женщина'},
    'relocation': {'no_relocation': 'не готов к переезду', 'relocation_possible': 'готов к переезду',
                   'relocation_desirable': 'хочет переехать'},
    'business_trips': {'never': 'не готов к командировкам', 'ready': 'готов к командировкам',
                       'sometimes': 'готов к редким командировкам'},
    'travel_time': {'any': 'не имеет значения', 'less_than_hour': 'не более часа',
                    'from_hour_to_one_and_half': 'не более полутора часов'},
    'employment': {'full': 'полная занятость', 'part': 'частичная занятость', 'project': 'проектная работа',
                   'volunteer': 'волонтёрство', 'probation': 'стажировка'},
    'schedule': {'fullDay': 'полный день', 'shift': 'сменный график', 'flexible': 'гибкий график',
                 'remote': 'удалённая работа', 'flyInFlyOut': 'вахтовый метод'},
    'formats': {'ON_SITE': 'на месте работодателя', 'REMOTE': 'удалённо', 'HYBRID': 'гибрид', 'FIELD_WORK': 'разъездной'},
    'education_level': {'secondary': 'среднее', 'special_secondary': 'среднее специальное',
                        'unfinished_higher': 'неоконченное высшее', 'higher': 'высшее', 'bachelor': 'бакалавр',
                        'master': 'магистр', 'candidate': 'кандидат наук', 'doctor': 'доктор наук'},
    'language': {'l1': 'родной', 'a1': 'A1 — начальный', 'a2': 'A2 — элементарный', 'b1': 'B1 — средний',
                 'b2': 'B2 — средне-продвинутый', 'c1': 'C1 — продвинутый', 'c2': 'C2 — в совершенстве'},
}
CURRENCY_SIGNS = {'KZT': '₸', 'RUR': '₽', 'RUB': '₽', 'USD': '$', 'EUR': '€', 'UZS': 'сум', 'KGS': 'сом', 'BYR': 'Br'}


def _long_text(value, limit):
    """Текст с абзацами (письмо, описание работы, «о себе»): переносы строк сохраняются, лишние пустые строки — нет."""
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = '\n'.join(line.rstrip() for line in str(value).replace('\r\n', '\n').replace('\r', '\n').split('\n')).strip()
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text[:limit] or None


def _label(kind, code):
    code = _text(code, 60)
    return RESUME_LABELS[kind].get(code, code) if code else None


def _labels(kind, codes):
    return [label for label in (_label(kind, code) for code in (codes if isinstance(codes, list) else [])[:12]) if label]


def _small_int(value, limit):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= limit else None


def _span(years, months):
    """Стаж {years, months}: месяцы — остаток после лет; пришли общим числом (63) — раскладываются (5 лет 3 мес.)."""
    years, months = _small_int(years, 100), _small_int(months, 1200)
    if months is not None and months >= 12:
        years, months = months // 12, months % 12
    if not years and not months:
        return None
    return {'years': years or 0, 'months': months or 0}


def _schools(items, limit):
    out = []
    for item in (items if isinstance(items, list) else [])[:limit]:
        if not isinstance(item, dict):
            continue
        entry = {'name': _text(item.get('name'), _SHORT), 'organization': _text(item.get('organization'), _SHORT),
                 'result': _text(item.get('result'), _SHORT), 'year': _small_int(item.get('year'), 2100)}
        if entry['name'] or entry['organization'] or entry['result']:
            out.append(entry)
    return out


def clean_resume(raw):
    """Резюме от моста → то, что видит карточка: подписи вместо кодов hh, длины в потолках, ничего лишнего. Контактов
    здесь нет и быть не должно — мост их не берёт, а лишние ключи сюда не проходят. None — резюме не пришло."""
    if not isinstance(raw, dict):
        return None
    salary = raw.get('salary') if isinstance(raw.get('salary'), dict) else {}
    amount = _small_int(salary.get('amount'), 10 ** 10)
    currency = _text(salary.get('currency'), 8)
    total = raw.get('experience_total') if isinstance(raw.get('experience_total'), dict) else {}
    experience = []
    for item in (raw.get('experience') if isinstance(raw.get('experience'), list) else [])[:30]:
        if not isinstance(item, dict):
            continue
        entry = {'company': _text(item.get('company'), _SHORT), 'area': _text(item.get('area'), _SHORT),
                 'position': _text(item.get('position'), _SHORT),
                 'start': _text(item.get('start'), 10), 'end': _text(item.get('end'), 10),
                 'span': _span(item.get('years'), item.get('months')),
                 'description': _long_text(item.get('description'), _DESCRIPTION)}
        if entry['company'] or entry['position']:
            experience.append(entry)
    languages = []
    for item in (raw.get('languages') if isinstance(raw.get('languages'), list) else [])[:10]:
        if isinstance(item, dict) and _text(item.get('title'), 80):
            languages.append({'title': _text(item.get('title'), 80), 'level': _label('language', item.get('degree'))})
    resume = {
        'title': _text(raw.get('title'), _SHORT),
        'salary': ({'amount': amount, 'currency': currency, 'sign': CURRENCY_SIGNS.get(currency or '', currency)}
                   if amount else None),
        'age': _small_int(raw.get('age'), 120),
        'gender': _label('gender', raw.get('gender')),
        'area': _text(raw.get('area'), _SHORT),
        'metro': _text(raw.get('metro'), _SHORT),
        'citizenship': [text for text in (_text(item, 80) for item in (raw.get('citizenship') or [])[:5]
                                          if isinstance(item, str)) if text],
        'relocation': _label('relocation', raw.get('relocation')),
        'business_trips': _label('business_trips', raw.get('business_trips')),
        'travel_time': _label('travel_time', raw.get('travel_time')),
        'employment': _labels('employment', raw.get('employment')),
        'schedule': _labels('schedule', raw.get('schedule')),
        'formats': _labels('formats', raw.get('formats')),
        'experience_total': _span(total.get('years'), total.get('months')),
        'experience': experience,
        'education_level': _label('education_level', raw.get('education_level')),
        'education': _schools(raw.get('education'), 15),
        'courses': _schools(raw.get('courses'), 30),
        'languages': languages,
        'key_skills': [text for text in (_text(item, 100) for item in (raw.get('key_skills') or [])[:60]
                                         if isinstance(item, (str, int))) if text],
        'about': _long_text(raw.get('about'), _ABOUT),
        'driver_license': [text for text in (_text(item, 10) for item in (raw.get('driver_license') or [])[:12]
                                             if isinstance(item, str)) if text],
        'has_vehicle': raw.get('has_vehicle') if isinstance(raw.get('has_vehicle'), bool) else None,
        'medical_book': raw.get('medical_book') if isinstance(raw.get('medical_book'), bool) else None,
    }
    return resume if any(value not in (None, [], False) for value in resume.values()) else None


def clean_vacancies(raw):
    """Вакансии, которые мост видит в кабинете. None — мост в этот раз их не перечитывал (каталог остаётся прежним)."""
    if not isinstance(raw, list):
        return None
    out, seen = [], set()
    for item in raw[:200]:
        if not isinstance(item, dict):
            continue
        vacancy_id = _digits(item.get('id'))
        name = _text(item.get('name'), 200)
        if not vacancy_id or not name or vacancy_id in seen:
            continue
        seen.add(vacancy_id)
        responses = item.get('responses')
        area_id = _digits(item.get('area_id'))
        out.append({'id': vacancy_id, 'name': name, 'area': _text(item.get('area'), 120),
                    'area_id': int(area_id) if area_id and len(area_id) <= 9 else None,
                    'responses': responses if isinstance(responses, int) and responses >= 0 else None})
    return out


def clean_session(payload):
    """(состояние входа, пояснение моста)."""
    session = str((payload or {}).get('session') or '').strip()
    if session not in SESSIONS:
        session = 'error'
    return session, _text((payload or {}).get('detail'), 300)


def clean_pause(payload):
    """Своя пауза моста до следующего опроса, когда кабинет hh не читается (вход потерян, защита сайта, сбой): мост
    отступает на неё, если она длиннее паузы портала. Секунды; без беды или мусор — 0."""
    value = (payload or {}).get('pause_sec')
    if isinstance(value, bool):
        return 0
    try:
        seconds = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(seconds, PAUSE_MAX_SECONDS))


def clean_account(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {'manager': _text(raw.get('manager'), 120), 'employer': _text(raw.get('employer'), 160),
            'employer_id': _digits(raw.get('employer_id'))}


# ─── Настройки руководителя ───────────────────────────────────────────────────

def since(settings):
    """С какого момента отклики hh переносятся (решение владельца 23.11.2026 — «только новые с этой минуты»)."""
    moment = parse_moment((settings or {}).get('since'))
    return moment or rules.HH_SINCE_DEFAULT


def vacancy_rule(settings, vacancy_id):
    """{'on': отслеживать ли, 'vacancy_id': вакансия iCORE или None, 'since': с какого момента её отклики нужны, если
    её включили позже общей отсечки, иначе None}. Вакансии нет в настройках — отслеживается, без вакансии iCORE."""
    rule = ((settings or {}).get('vacancies') or {}).get(str(vacancy_id)) or {}
    icore = rule.get('vacancy_id')
    on = rule.get('on') is not False
    own = parse_moment(rule.get('since')) if on else None
    return {'on': on,
            'vacancy_id': icore if isinstance(icore, int) and not isinstance(icore, bool) and icore > 0 else None,
            'since': own if own and own > since(settings) else None}


def vacancy_since(settings, vacancy_id):
    """С какого момента переносятся отклики вакансии: общая отсечка или минута, когда её включили."""
    return vacancy_rule(settings, vacancy_id)['since'] or since(settings)


def since_by_vacancy(settings):
    """{id вакансии hh: момент} — вакансии, включённые позже общей отсечки: мост не листает их выдачу глубже."""
    out = {}
    for key in ((settings or {}).get('vacancies') or {}):
        moment = vacancy_rule(settings, key)['since']
        if moment:
            out[str(key)] = moment
    return out


def merge_vacancy_settings(previous, cleaned, now):
    """Правка руководителя поверх прежних правил. Вакансию, которая была выключена, включили — она отслеживается с
    этой минуты: её прежние отклики в воронку не идут (как решение владельца 23.11.2026 «только новые с этой минуты»
    для всего моста). Уже отслеживаемая сохраняет свой момент; выключенная его теряет."""
    previous = previous if isinstance(previous, dict) else {}
    out = {}
    for key, rule in cleaned.items():
        entry = dict(rule)
        before = previous.get(key) if isinstance(previous.get(key), dict) else None
        if entry.get('on') is not False and before is not None:
            if before.get('on') is False:
                entry['since'] = now.isoformat(timespec='seconds')
            elif before.get('since'):
                entry['since'] = before['since']
        out[key] = entry
    return out


def off_ids(settings):
    return sorted(key for key, rule in ((settings or {}).get('vacancies') or {}).items()
                  if isinstance(rule, dict) and rule.get('on') is False)


def clean_vacancy_settings(raw, known_icore_ids):
    """Правка руководителя: {id вакансии hh: {on, vacancy_id}} → чистый словарь. ValueError — с текстом для человека."""
    if not isinstance(raw, dict):
        raise ValueError('Нужен список вакансий hh')
    out = {}
    for key, rule in list(raw.items())[:200]:
        vacancy_id = _digits(key)
        if not vacancy_id or not isinstance(rule, dict):
            raise ValueError('Неизвестная вакансия hh: %s' % str(key)[:40])
        icore = rule.get('vacancy_id')
        if icore in (None, '', 0):
            icore = None
        else:
            try:
                icore = int(icore)
            except (TypeError, ValueError):
                raise ValueError('Вакансия iCORE задана неверно')
            if icore not in known_icore_ids:
                raise ValueError('Такой вакансии iCORE нет')
        entry = {'on': rule.get('on') is not False}
        if icore:
            entry['vacancy_id'] = icore
        out[vacancy_id] = entry
    return out


def view(row, now):
    """Строка интеграции hh для «Настроек»: мост, вход, вакансии с правилами руководителя."""
    row = row or {}
    report = row.get('cursor') or {}
    settings = row.get('settings') or {}
    bridge = report.get('bridge') or {}
    seen_at = parse_moment(bridge.get('seen_at'))
    vacancies = []
    for item in report.get('vacancies') or []:
        rule = vacancy_rule(settings, item.get('id'))
        vacancies.append(dict(item, on=rule['on'], vacancy_id=rule['vacancy_id'],
                              tracked_since=rule['since'].isoformat(timespec='minutes') if rule['since'] else None))
    return {
        'since': since(settings).isoformat(timespec='minutes'),
        'seen_at': seen_at.isoformat(timespec='seconds') if seen_at else None,
        'silent': bool(seen_at and now - seen_at > rules.hh_silent_after(bridge.get('expect_sec'))),
        'session': bridge.get('session'),
        'version': bridge.get('version'),
        'account': report.get('account') or {},
        'vacancies': vacancies,
    }
