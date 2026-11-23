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
                "middle_name": "…", "phone": "+7 (701) 234-56-78", "city": "Алматы", "resume_hash": "…"}]}

Портал отвечает исходом по каждой теме, настройками (с какого момента отклики переносятся и какие вакансии
выключены — их мост не читает вовсе) и паузой до следующего опроса (next_poll_sec). Настройки руководителя
(hiring_integrations.settings у кода hh):

    {"since": "2026-11-23T12:08:00",
     "vacancies": {"139508734": {"on": false}, "139079696": {"on": true, "vacancy_id": 2}}}

Вакансия hh, которой в настройках нет, переносится без вакансии iCORE: новые вакансии hh идут сами (владелец
23.11.2026). Последний доклад моста (кто вошёл, какие вакансии видит, когда был на связи) — в cursor той же строки.
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
    }


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
        out.append({'id': vacancy_id, 'name': name, 'area': _text(item.get('area'), 120),
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
    """{'on': переносить ли, 'vacancy_id': вакансия iCORE или None}. Вакансии нет в настройках — переносится, без вакансии."""
    rule = ((settings or {}).get('vacancies') or {}).get(str(vacancy_id)) or {}
    icore = rule.get('vacancy_id')
    return {'on': rule.get('on') is not False,
            'vacancy_id': icore if isinstance(icore, int) and not isinstance(icore, bool) and icore > 0 else None}


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
        vacancies.append(dict(item, on=rule['on'], vacancy_id=rule['vacancy_id']))
    return {
        'since': since(settings).isoformat(timespec='minutes'),
        'seen_at': seen_at.isoformat(timespec='seconds') if seen_at else None,
        'silent': bool(seen_at and now - seen_at > rules.hh_silent_after(bridge.get('expect_sec'))),
        'session': bridge.get('session'),
        'version': bridge.get('version'),
        'account': report.get('account') or {},
        'vacancies': vacancies,
    }
