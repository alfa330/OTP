# -*- coding: utf-8 -*-
"""Мост hh.kz: отклики кабинета работодателя → «Воронка найма» портала.

Зачем
-----
Владелец 23.11.2026: «по данному сайту нужно автоматизировать передачу откликов в icore в воронку найма». До этого
рекрутеры переносили отклики hh в портал руками: ФИО и телефон — набором (184 карточки за 18–20.11). API hh.ru отдаёт
отклики работодателю только с платным доступом к API и приложением, прошедшим модерацию, поэтому мост читает кабинет
так же, как его читает рекрутер на сайте, — под входом владельца (он вошёл в кабинет в браузере, мост живёт его
сессией).

Как устроено
------------
    hh.kz, кабинет работодателя ◄── раз в такт: счётчики откликов каждой вакансии (килобайт)
                                 ├─ счётчик сдвинулся — свежие отклики вакансии (выдача страницы «Отклики»)
                                 └─ новый отклик — его резюме и сопроводительное письмо (страница резюме из отклика)
    мост (VM шлюза) ── POST /api/hiring/hh/agent/push ──► портал: карточки в «Отклики» пула

Паузу до следующего опроса называет портал, и она случайная (владелец 23.11.2026: «добавь случайный тайминг в опросе
лидов а ночью от 45 мин до 2 часов»): днём (8–21 по Алматы) от 2 до 5 минут, ночью от 45 минут до 2 часов — мост
выдерживает её как есть. Раз в час он перечитывает список вакансий кабинета (страница «Вакансии»). В каждый такт — по
одному маленькому запросу счётчиков на вакансию; полную выдачу откликов (сотни килобайт) он берёт, только когда
счётчики сдвинулись. Портал отвечает исходом по каждому отклику и настройками: с какого момента отклики нужны (у
вакансии, которую включили позже, — свой момент) и какие вакансии выключены (их мост не читает вовсе).

Резюме и письмо
---------------
Владелец 23.11.2026: «надо бы сделать так что бы все резюме и письмо человека который обратился отобразились сразу в
карточке». Полное резюме (опыт, образование, «о себе») и сопроводительное письмо hh отдаёт только на странице резюме,
открытой из отклика, — и тогда отклик в hh становится «Просмотрен» (кандидат это видит, у рекрутеров в hh пропадает
отметка «новый»). Владелец выбрал это осознанно («Полное резюме и письмо»), поэтому мост открывает каждый новый отклик
один раз — один запрос, как рекрутер, открывший отклик. Контакты (email, адрес, дата рождения) из резюме не берутся:
телефон и так приходит в выдаче.

Что мост НЕ делает
------------------
Ничего не меняет в hh: только GET тех же адресов, что открывает страница рекрутера; кроме отметки «Просмотрен» у нового
отклика отклики остаются как были — неразобранными, на своих этапах. Не ходит в hh чаще одного запроса за раз, между
запросами — случайная пауза 1,5–3,5 с, больше DAILY_REQUEST_CAP запросов в сутки не делает (предохранитель от ошибки в
коде). Переменные HTTPS_PROXY и подобные не читаются: наружу только через прокси из настроек, как у моста «Касаний».

Что будет, если что-то лежит
----------------------------
  * вход потерян (вместо кабинета — страница входа) — портал узнаёт «нужен вход», руководителям рекрутинга уходит
    тревога, мост проверяет вход раз в полчаса. Лечится новым входом владельца и заменой файла сессии;
  * hh не пускает (защита сайта) — то же, мост отступает на 15 минут, потом на полчаса и на час: стук в закрытую
    дверь — худшее, что можно сделать; прочие сбои hh — паузы 2, 5, 10 минут. Отступ не короче паузы портала: ночью
    hh не опрашивается чаще ночного правила и при сбоях;
  * портал не отвечает — отклики ждут в очереди моста: отданными они считаются только по ответу портала. Пока портал
    не ответил, мост стучится только к нему, hh не читает: в простой портала опрос hh не учащается.

Запуск
------
    python -m cdr_bridge.hh_bridge            рабочий режим (служба hh-bridge)
    python -m cdr_bridge.hh_bridge --once     один такт и выход
    python -m cdr_bridge.hh_bridge --check    проверить вход в hh и подпись на портале; откликов не отправляет

Настройки — окружение (или .env.codex.local при отладке):
    CDR_BRIDGE_PORTAL, CDR_AGENT_KEY_FILE | CDR_AGENT_PRIVATE_KEY, CDR_PORTAL_PROXY,
    CDR_PORTAL_CA_BUNDLE          — как у моста;
    HH_BRIDGE_SESSION_FILE        сессия hh: {"user_agent": "…", "cookies": [{name, value, domain, path, expires}]};
                                  hh обновляет часть cookie в ответах — мост дописывает их в этот же файл
    HH_BRIDGE_STATE_FILE          что мост уже отдал порталу (по умолчанию — рядом с файлом сессии)
    HH_BRIDGE_HOST                кабинет, https://almaty.hh.kz
    HH_BRIDGE_PROXY               прокси к hh.kz (по умолчанию тот же, что к порталу)
    HH_BRIDGE_INTERVAL_SECONDS    пауза, пока портал не назвал свою (180)
    HH_BRIDGE_HEARTBEAT_FILE      файл пульса для проверки живости контейнера (после такта и раз в 5 минут паузы)
"""

import argparse
import html as html_lib
import json
import logging
import os
import platform
import random
import re
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr_bridge.agent import Bridge, _env_file_values  # noqa: E402

VERSION = 'hh/1.3.0'
log = logging.getLogger('cdr_bridge.hh_bridge')

PUSH_PATH = '/api/hiring/hh/agent/push'
PORTAL_TIMEOUT = (10, 30)
SITE_TIMEOUT = (10, 40)
DEFAULT_HOST = 'https://almaty.hh.kz'

# Бережность к hh (владелец 23.11.2026: «максимально безопасно… чтобы не получить банов»). Паузу до следующего опроса
# называет портал, уже случайную: днём 2–5 минут, ночью 45 минут – 2 часа (hiring/rules.hh_poll_seconds) — мост её не
# разбавляет, иначе вышел бы за названные владельцем границы. Чаще минуты hh не опрашиваем, что бы ни назвал портал.
DEFAULT_INTERVAL_SECONDS = 180.0
MIN_INTERVAL_SECONDS = 60.0
MAX_INTERVAL_SECONDS = 3 * 3600.0
# Проверка живости контейнера ждёт пульс не реже раза в 15 минут, а ночная пауза — до двух часов: пока последний такт
# прошёл как надо, пульс обновляется и во время паузы.
HEARTBEAT_SECONDS = 300.0
# Беда с hh — отступить надолго, а не стучать: вход потерян — сам он не вернётся (нужен человек), проверка раз в
# полчаса; защита сайта не пускает — 15 минут, потом полчаса и час; прочие сбои hh — 2, 5, 10 минут. Отступ не короче
# паузы портала: ночное правило действует и при сбоях.
TROUBLE_PAUSES = {'login_required': (1800,), 'blocked': (900, 1800, 3600), 'error': (120, 300, 600)}
PORTAL_BACKOFF_SECONDS = (5, 15, 30, 60, 120)
# Пауза между запросами к hh внутри такта — случайная, как у человека, листающего страницы.
REQUEST_PAUSE_SECONDS = (1.5, 3.5)
# Список вакансий кабинета перечитывается раз в час (новая вакансия попадёт в опрос не позже).
CATALOG_SECONDS = 3600
# Предохранитель от ошибки в коде: больше стольких запросов к hh за сутки (Алматы) мост не делает. Обычный день при
# трёх вакансиях — около тысячи маленьких запросов счётчиков.
DAILY_REQUEST_CAP = 3000
# Выдача откликов: 50 на страницу, порядок «по дате»; глубже десяти страниц не листаем.
PAGE_SIZE = 50
MAX_PAGES = 10
# Порядок «по дате» у hh почти строгий: страница, где все отклики старше отсечки на сутки, — последняя нужная.
ORDER_SLACK = timedelta(days=1)
# Отклик с резюме — до десятков килобайт текста: порция поменьше, чтобы доклад порталу оставался коротким.
PUSH_BATCH = 20
# Хеш резюме hh — только шестнадцатеричный: из него собирается адрес страницы, чужие символы в путь не попадают.
RESUME_HASH = re.compile(r'^[0-9a-f]{16,64}$')
# Потолки текста резюме: hh разрешает описание места работы до 4000 знаков; письмо и «о себе» — длиннее не бывает.
TEXT_LIMITS = {'description': 4000, 'about': 6000, 'letter': 10000, 'short': 300}
SEEN_LIMIT = 20000
# Рекрутер в hh успевает перевести свежий отклик дальше по этапам («Телефонное интервью» у «Оператора call-центра» —
# 71 из 153 на 23.11.2026) раньше, чем мост его увидит в «Откликах». Поэтому, когда растёт счётчик этапа, мост смотрит
# и первую страницу его папки (имя папки — состояние строчными буквами). Отказы — нет: отклонённый в hh кандидат в
# пул рекрутеров не нужен.
WATCHED_STATES = ('CONSIDER', 'PHONE_INTERVIEW', 'ASSESSMENT', 'INTERVIEW', 'OFFER', 'HIRED')
SUMMARY_SECONDS = 600
CRASH_PAUSE_SECONDS = 30.0

SESSION_OK, LOGIN_REQUIRED, BLOCKED, SITE_ERROR = 'ok', 'login_required', 'blocked', 'error'


class SiteTrouble(Exception):
    """hh не отдал кабинет: kind — login_required (вход потерян) | blocked (защита сайта) | error (сбой)."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _clamp_interval(value, default=DEFAULT_INTERVAL_SECONDS):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return float(default)
    if seconds != seconds:  # NaN
        return float(default)
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, seconds))


def load_config():
    """Окружение важнее файла: на VM переменные ставит служба, файл нужен только при отладке."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dev = _env_file_values(os.path.join(root, '.env.codex.local'))

    def value(key, default=''):
        return (os.environ.get(key) or dev.get(key) or default).strip()

    session_file = value('HH_BRIDGE_SESSION_FILE')
    default_state = os.path.join(os.path.dirname(session_file), 'hh-bridge-state.json') if session_file else ''
    return {
        'portal': value('CDR_BRIDGE_PORTAL').rstrip('/'),
        'key_file': value('CDR_AGENT_KEY_FILE'),
        'private_key': value('CDR_AGENT_PRIVATE_KEY'),
        'proxy': value('CDR_PORTAL_PROXY'),
        'ca_bundle': value('CDR_PORTAL_CA_BUNDLE'),
        'session_file': session_file,
        'state_file': value('HH_BRIDGE_STATE_FILE', default_state),
        'host': value('HH_BRIDGE_HOST', DEFAULT_HOST).rstrip('/'),
        'site_proxy': value('HH_BRIDGE_PROXY') or value('CDR_PORTAL_PROXY'),
        'interval': _clamp_interval(value('HH_BRIDGE_INTERVAL_SECONDS', str(DEFAULT_INTERVAL_SECONDS))),
        'heartbeat_file': value('HH_BRIDGE_HEARTBEAT_FILE'),
    }


def _write_json_atomic(path, data):
    """Файл целиком или прежний: полуфайл после обрыва питания хуже старых данных."""
    tmp = '%s.tmp' % path
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False)
    os.replace(tmp, path)


# ─── Разбор того, что отдаёт hh (чистые функции) ──────────────────────────────

_STATE_RE = re.compile(r'<template[^>]*id="HH-Lux-InitialState"[^>]*>(.*?)</template>', re.S)


def initial_state(html_text):
    """Начальное состояние страницы кабинета (шаблон HH-Lux-InitialState) → словарь или None."""
    match = _STATE_RE.search(html_text or '')
    if not match:
        return None
    raw = match.group(1)
    for text in (raw, html_lib.unescape(raw)):
        try:
            state = json.loads(text)
        except ValueError:
            continue
        return state if isinstance(state, dict) else None
    return None


def catalog_from_state(state):
    """Страница «Вакансии» → (кто вошёл, активные вакансии). SiteTrouble — это не кабинет работодателя."""
    if not isinstance(state, dict):
        raise SiteTrouble(SITE_ERROR, 'страница вакансий без начального состояния')
    if state.get('userType') != 'employer':
        raise SiteTrouble(LOGIN_REQUIRED, 'hh открыл страницу не работодателю (userType=%s)' % state.get('userType'))
    manager = state.get('employerManager') or {}
    account = {'manager': ' '.join(str(manager.get(key) or '').strip() for key in ('firstName', 'lastName')).strip() or None,
               'employer': state.get('employerName') or None,
               'employer_id': str(state.get('employerId') or '') or None}
    groups = ((state.get('managerVacancies') or {}).get('vacanciesGroups')) or {}
    vacancies = []
    for item in groups.get('list') or []:
        if not isinstance(item, dict) or not item.get('vacancyId'):
            continue
        status = item.get('status') or {}
        if status.get('archived') or status.get('disabled'):
            continue
        area = item.get('area') or {}
        negotiations = item.get('negotiations') or {}
        vacancies.append({'id': str(item['vacancyId']), 'name': str(item.get('name') or '').strip()[:200],
                          'area': area.get('name') if isinstance(area, dict) else None,
                          'responses': negotiations.get('responsesCount')})
    return account, vacancies


def counts_fingerprint(payload):
    """Счётчики откликов вакансии по состояниям → строка: сдвинулась — пора читать выдачу. Учтены и непрочитанные."""
    counts = (payload or {}).get('counts') if isinstance(payload, dict) else None
    if not isinstance(counts, dict):
        raise SiteTrouble(SITE_ERROR, 'счётчики откликов без поля counts')
    return json.dumps({'total': counts.get('totalCandidatesByEmployerState'),
                       'unread': counts.get('totalUnreadCandidatesByEmployerState')}, sort_keys=True)


def _first(resume, field):
    """Поле резюме из выдачи откликов: [{"string": "…"}] → значение."""
    values = resume.get(field)
    if isinstance(values, list):
        values = values[0] if values else None
    if isinstance(values, dict):
        return values.get('string')
    return values


def items_from_page(payload, vacancy):
    """Страница выдачи откликов → отклики для портала. Только отклики кандидатов (не наши приглашения)."""
    block = (payload or {}).get('contactCenterCandidates') if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        raise SiteTrouble(SITE_ERROR, 'выдача откликов без contactCenterCandidates')
    resumes = {}
    for resume in (((block.get('rawEnrichingData') or {}).get('resumes') or {}).get('resumes') or []):
        if isinstance(resume, dict) and resume.get('topicId'):
            resumes[str(resume['topicId'])] = resume
    items = []
    for candidate in block.get('candidates') or []:
        if not isinstance(candidate, dict) or not candidate.get('topicId'):
            continue
        if not str(candidate.get('initialTopicType') or 'RESPONSE').startswith('RESPONSE'):
            continue
        topic = str(candidate['topicId'])
        resume = resumes.get(topic) or {}
        phone = resume.get('phone') if isinstance(resume.get('phone'), list) else []
        phone = phone[0] if phone and isinstance(phone[0], dict) else {}
        area = resume.get('area') if isinstance(resume.get('area'), list) else []
        area = area[0] if area and isinstance(area[0], dict) else {}
        attributes = resume.get('_attributes') if isinstance(resume.get('_attributes'), dict) else {}
        items.append({
            'topic_id': topic,
            'vacancy_id': str(candidate.get('vacancyId') or vacancy['id']),
            'vacancy_name': vacancy.get('name'),
            'responded_at': candidate.get('creationTime'),
            'last_name': _first(resume, 'lastName'),
            'first_name': _first(resume, 'firstName'),
            'middle_name': _first(resume, 'middleName'),
            'phone': phone.get('formatted') or phone.get('raw') or None,
            'city': area.get('trl') or None,
            'resume_hash': attributes.get('hash'),
        })
    return items


def _value(field):
    """Поле страницы резюме: часть полей обёрнута в {value, type, block, _attributes}, часть — нет."""
    if isinstance(field, dict) and 'value' in field and 'type' in field:
        return field['value']
    return field


def _clip(value, limit):
    """Текст резюме: строки и переносы сохраняются, края и пустые хвосты строк — нет; длиннее limit — обрезается."""
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = '\n'.join(line.rstrip() for line in str(value).replace('\r\n', '\n').replace('\r', '\n').split('\n')).strip()
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text[:limit] or None


def _strings(value):
    """[{"string": "full"}, …] или ["full", …] → ["full", …]."""
    out = []
    for item in value if isinstance(value, list) else []:
        text = item.get('string') or item.get('id') if isinstance(item, dict) else item
        if isinstance(text, (str, int)) and str(text).strip():
            out.append(str(text).strip()[:TEXT_LIMITS['short']])
    return out


def _education(items):
    short = TEXT_LIMITS['short']
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            year = item.get('year')
            out.append({'name': _clip(item.get('name'), short), 'organization': _clip(item.get('organization'), short),
                        'result': _clip(item.get('result'), short),
                        'year': year if isinstance(year, int) and not isinstance(year, bool) else None})
    return [item for item in out if item['name'] or item['organization'] or item['result']]


def resume_from_state(state, resume_hash):
    """Страница резюме, открытая из отклика → (резюме без контактов, сопроводительное письмо или None). Коды hh
    (занятость, переезд, уровень языка) уходят как есть — подписи им даёт портал."""
    resume = (state or {}).get('resume') if isinstance(state, dict) else None
    if not isinstance(resume, dict):
        raise SiteTrouble(SITE_ERROR, 'страница резюме без резюме')
    short = TEXT_LIMITS['short']

    def field(name):
        return _value(resume.get(name))

    def title_of(name):
        value = field(name)
        return _clip(value.get('title') or value.get('trl'), short) if isinstance(value, dict) else None

    salary = field('salary')
    total = field('totalExperience')
    experience = []
    for item in field('experience') or []:
        if not isinstance(item, dict):
            continue
        interval = item.get('interval') if isinstance(item.get('interval'), dict) else {}
        experience.append({
            'company': _clip(item.get('companyName'), short), 'area': _clip(item.get('companyAreaTitle'), short),
            'position': _clip(item.get('position'), short), 'start': _clip(item.get('startDate'), 10),
            'end': _clip(item.get('endDate'), 10), 'description': _clip(item.get('description'), TEXT_LIMITS['description']),
            'years': interval.get('years'), 'months': interval.get('months')})
    languages = [{'title': _clip(item.get('title'), short), 'degree': _clip(item.get('degree'), 20)}
                 for item in field('language') or [] if isinstance(item, dict) and item.get('title')]
    skills = _strings(field('keySkills')) or [str(item.get('name')).strip()[:short] for item in field('advancedKeySkills') or []
                                               if isinstance(item, dict) and item.get('name')]
    age = field('age')
    out = {
        'title': _clip(field('title'), short),
        'salary': ({'amount': salary.get('amount'), 'currency': _clip(salary.get('currency'), 8)}
                   if isinstance(salary, dict) and isinstance(salary.get('amount'), int) else None),
        'age': age if isinstance(age, int) and not isinstance(age, bool) else None,
        'gender': _clip(field('gender'), 20),
        'area': title_of('area'),
        'metro': title_of('metro'),
        'citizenship': [_clip(item.get('title'), short) for item in field('citizenship') or []
                        if isinstance(item, dict) and item.get('title')],
        'relocation': _clip(field('relocation'), 40),
        'business_trips': _clip(field('businessTripReadiness'), 40),
        'travel_time': _clip(field('travelTime'), 40),
        'employment': _strings(field('employment')),
        'schedule': _strings(field('workSchedule')),
        'formats': _strings(field('workFormats')),
        'experience_total': ({'years': total.get('years'), 'months': total.get('months')} if isinstance(total, dict) else None),
        'experience': experience[:30],
        'education_level': _clip(field('educationLevel'), 40),
        'education': _education(field('primaryEducation'))[:15],
        'courses': (_education(field('additionalEducation')) + _education(field('attestationEducation')))[:30],
        'languages': languages[:10],
        'key_skills': skills[:60],
        'about': _clip(field('skills'), TEXT_LIMITS['about']),
        'driver_license': _strings(field('driverLicenseTypes')),
        'has_vehicle': field('hasVehicle') if isinstance(field('hasVehicle'), bool) else None,
        'medical_book': field('hasMedicalBook') if isinstance(field('hasMedicalBook'), bool) else None,
    }
    letters = ((state.get('employerNegotiations') or {}).get('responseLetters') or {}) \
        if isinstance(state.get('employerNegotiations'), dict) else {}
    letter = letters.get(resume_hash) if isinstance(letters, dict) else None
    if letter is None and isinstance(letters, dict) and len(letters) == 1:
        letter = next(iter(letters.values()))
    return out, _clip(letter, TEXT_LIMITS['letter'])


def parse_moment(value):
    """ISO-время hh или портала → aware datetime (UTC) или None."""
    text = str(value or '').strip()
    if not text:
        return None
    if len(text) >= 5 and text[-5] in '+-' and text[-3] != ':':
        text = text[:-2] + ':' + text[-2:]
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone(timedelta(hours=5)))   # без пояса — время Алматы, как у портала
    return moment.astimezone(timezone.utc)


# ─── Кабинет hh ───────────────────────────────────────────────────────────────

class Site:
    """Кабинет работодателя hh.kz под сессией владельца. Только GET и только по одному запросу за раз."""

    def __init__(self, config, http=None, sleep=time.sleep, clock=time.time):
        self.host = config['host']
        self.session_file = config['session_file']
        self.http = http or self._build_http(config)
        self.sleep = sleep
        self.clock = clock
        self.user_agent = ''
        self._cookie_marks = None
        self._day = None
        self.requests_today = 0
        self.load_session()

    def _count_request(self):
        """Суточный предохранитель (DAILY_REQUEST_CAP): сутки — по Алматы."""
        day = datetime.fromtimestamp(self.clock(), timezone(timedelta(hours=5))).date()
        if day != self._day:
            self._day, self.requests_today = day, 0
        if self.requests_today >= DAILY_REQUEST_CAP:
            raise SiteTrouble(SITE_ERROR, 'суточный предел запросов к hh (%d) исчерпан — продолжу завтра'
                              % DAILY_REQUEST_CAP)
        self.requests_today += 1

    @staticmethod
    def _build_http(config):
        http = requests.Session()
        http.trust_env = False
        if config.get('site_proxy'):
            http.proxies = {'http': config['site_proxy'], 'https': config['site_proxy']}
        return http

    def load_session(self):
        """Cookie и браузер, в котором владелец вошёл в кабинет. Без файла сессии мост не стартует."""
        if not self.session_file:
            raise ValueError('не задан файл сессии hh (HH_BRIDGE_SESSION_FILE)')
        try:
            with open(self.session_file, encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ValueError('файл сессии hh %s не читается: %s' % (self.session_file, exc))
        cookies = data.get('cookies') if isinstance(data, dict) else None
        if not cookies:
            raise ValueError('в файле сессии hh нет cookie')
        self.user_agent = str(data.get('user_agent') or '').strip() or 'Mozilla/5.0'
        self.http.cookies.clear()
        for cookie in cookies:
            if not isinstance(cookie, dict) or not cookie.get('name'):
                continue
            expires = cookie.get('expires')
            self.http.cookies.set(cookie['name'], str(cookie.get('value') or ''), domain=cookie.get('domain') or '',
                                  path=cookie.get('path') or '/', secure=bool(cookie.get('secure', True)),
                                  expires=int(expires) if isinstance(expires, (int, float)) and expires > 0 else None,
                                  rest={'HttpOnly': None} if cookie.get('httpOnly') else {})
        self._cookie_marks = self._marks()

    def _marks(self):
        return sorted((c.name, c.domain, c.path, c.value) for c in self.http.cookies)

    def save_session(self):
        """hh обновляет часть cookie в ответах (защита сайта, _xsrf): дописать их в файл, чтобы перезапуск не терял."""
        marks = self._marks()
        if marks == self._cookie_marks:
            return False
        cookies = [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path, 'secure': bool(c.secure),
                    'expires': c.expires, 'httpOnly': c.has_nonstandard_attr('HttpOnly')} for c in self.http.cookies]
        _write_json_atomic(self.session_file, {'user_agent': self.user_agent, 'cookies': cookies,
                                               'saved_at': datetime.now(timezone.utc).isoformat(timespec='seconds')})
        self._cookie_marks = marks
        return True

    def _get(self, path, params=None, json_body=True):
        headers = {'User-Agent': self.user_agent, 'Accept-Language': 'ru-RU,ru;q=0.9',
                   'Referer': self.host + '/employer/vacancies'}
        if json_body:
            headers.update({'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})
        else:
            headers['Accept'] = 'text/html,application/xhtml+xml'
        self._count_request()
        try:
            response = self.http.get(self.host + path, params=params or {}, headers=headers, timeout=SITE_TIMEOUT,
                                     allow_redirects=True)
        except requests.RequestException as exc:
            raise SiteTrouble(SITE_ERROR, 'hh не ответил: %s' % type(exc).__name__)
        finally:
            self.sleep(random.uniform(*REQUEST_PAUSE_SECONDS))
        final = urlsplit(response.url or '').path
        if final.startswith('/account/login') or final.startswith('/auth'):
            raise SiteTrouble(LOGIN_REQUIRED, 'hh отправил на страницу входа')
        server = (response.headers.get('Server') or '').lower()
        if response.status_code in (401, 403, 429, 503) and ('ddos-guard' in server or 'ddos-guard' in (response.text or '')[:3000].lower()):
            raise SiteTrouble(BLOCKED, 'защита hh не пускает (%d)' % response.status_code)
        if response.status_code in (401, 403):
            raise SiteTrouble(LOGIN_REQUIRED, 'hh ответил %d — кабинет закрыт' % response.status_code)
        if response.status_code != 200:
            raise SiteTrouble(SITE_ERROR, 'hh ответил %d на %s' % (response.status_code, path))
        if not json_body:
            return response.text
        try:
            return response.json()
        except ValueError:
            raise SiteTrouble(SITE_ERROR, 'hh вернул не JSON на %s' % path)

    def catalog(self):
        """(кто вошёл, активные вакансии) — страница «Вакансии» кабинета."""
        return catalog_from_state(initial_state(self._get('/employer/vacancies', json_body=False)))

    def counts(self, vacancy_id):
        return counts_fingerprint(self._get('/shards/employer/candidates/counts', {'vacancyId': vacancy_id}))

    def responses(self, vacancy, page, collection=None):
        """Страница выдачи «по дате»: без collection — папка «Отклики», иначе папка этапа (phone_interview и т. п.)."""
        params = {'vacancyId': vacancy['id'], 'order': 'DATE', 'page': page}
        if collection:
            params['collection'] = collection
        return items_from_page(self._get('/shards/employer/vacancyresponses/candidates_list', params), vacancy)

    def resume(self, item):
        """Резюме и письмо отклика — страница резюме, открытая из отклика (в hh он после этого «Просмотрен»)."""
        resume_hash = str(item.get('resume_hash') or '')
        if not RESUME_HASH.match(resume_hash):
            raise SiteTrouble(SITE_ERROR, 'у отклика нет хеша резюме')
        page = self._get('/resume/%s' % resume_hash, {'vacancyId': item['vacancy_id'], 't': item['topic_id']},
                         json_body=False)
        return resume_from_state(initial_state(page), resume_hash)


def grown_states(previous, current):
    """Этапы из WATCHED_STATES, у которых счётчик вырос (previous None — первый взгляд: все непустые)."""
    now_total = (json.loads(current).get('total') or {}) if current else {}
    before_total = (json.loads(previous).get('total') or {}) if previous else None
    grown = []
    for state in WATCHED_STATES:
        count = now_total.get(state) or 0
        if count and (before_total is None or count > (before_total.get(state) or 0)):
            grown.append(state)
    return grown


# ─── Мост ─────────────────────────────────────────────────────────────────────

class HhBridge:
    def __init__(self, config, site=None, portal_session=None, clock=time.time):
        self.config = config
        self.portal = config['portal']
        self.session = portal_session or Bridge._build_session(config)
        self.signer = Bridge._build_signer(config)
        self.site = site if site is not None else Site(config)
        self.clock = clock
        self.agent_id = '%s-%d' % (socket.gethostname()[:60], os.getpid())
        self.interval = _clamp_interval(config.get('interval'))
        self.stop_event = threading.Event()
        # Что сказал портал: с какого момента отклики нужны и какие вакансии выключены. Пока не спросили — None.
        self.since = None
        self.vacancy_since = {}        # вакансии, включённые позже общей отсечки: id → с какого момента
        self.off = set()
        self.account = None
        self.vacancies = None
        self.catalog_at = None
        self.catalog_unsent = False    # список вакансий перечитан, а портал его ещё не получил
        self.trouble = None            # (kind, текст) — беда со входом или сайтом
        self.trouble_count = 0
        self.portal_failures = 0
        self.healthy = False           # последний такт прошёл как надо — пульс можно обновлять и в паузе
        self.pending = []              # отклики, которые портал ещё не подтвердил
        self.state = self._load_state()
        self._summary_at = None
        self._sent = 0
        self._outcomes = {}

    # ── состояние на диске ───────────────────────────────────────────────────

    def _load_state(self):
        """Что уже отдано порталу, отпечатки счётчиков вакансий и очередь, которую портал ещё не подтвердил: очередь
        на диске — иначе перезапуск между чтением выдачи и ответом портала терял бы отклики (счётчики-то уже сошлись)."""
        path = self.config.get('state_file')
        state = {'seen': [], 'fingerprints': {}}
        if path and os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    state['seen'] = [str(x) for x in loaded.get('seen') or []][-SEEN_LIMIT:]
                    state['fingerprints'] = {str(k): str(v) for k, v in (loaded.get('fingerprints') or {}).items()}
                    self.pending = [item for item in loaded.get('pending') or []
                                    if isinstance(item, dict) and item.get('topic_id')]
            except (OSError, ValueError) as exc:
                log.warning('состояние моста не прочиталось (%s) — начинаю с чистого: повторы отсеет портал', exc)
        self._seen = set(state['seen'])
        return state

    def _save_state(self):
        path = self.config.get('state_file')
        if not path:
            return
        self.state['seen'] = self.state['seen'][-SEEN_LIMIT:]
        self.state['pending'] = self.pending
        self._seen = set(self.state['seen'])
        try:
            _write_json_atomic(path, self.state)
        except OSError as exc:
            log.warning('состояние моста не записалось: %s', exc)

    def _heartbeat(self):
        """Пульс — только после обмена, который портал принял, и при живом входе в hh."""
        path = self.config.get('heartbeat_file')
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(str(int(self.clock())))
        except OSError as exc:
            log.warning('пульс не записался: %s', exc)

    # ── портал ───────────────────────────────────────────────────────────────

    def _post(self, payload):
        if self.signer is None:
            raise RuntimeError('у моста нет ключа подписи (CDR_AGENT_KEY_FILE)')
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')

        def send():
            headers = {'Content-Type': 'application/json'}
            headers.update(self.signer.headers('POST', PUSH_PATH, body))
            return self.session.post(self.portal + PUSH_PATH, data=body, timeout=PORTAL_TIMEOUT, headers=headers)

        try:
            response = send()
        except requests.exceptions.ConnectionError as exc:
            if isinstance(exc, requests.exceptions.Timeout):
                raise
            response = send()          # протухшее соединение из пула — один повтор по новому
        if response.status_code == 401:
            raise RuntimeError('портал не принял подпись: ключ %s не внесён в CDR_AGENT_KEYS или разошлись часы'
                               % self.signer.key_id)
        if response.status_code != 200:
            raise RuntimeError('портал ответил %d: %s' % (response.status_code, (response.text or '')[:200]))
        try:
            reply = response.json()
        except ValueError as exc:
            raise RuntimeError('портал вернул не JSON: %s' % (response.text or '')[:200]) from exc
        if not isinstance(reply, dict):
            raise RuntimeError('портал вернул не объект')
        return reply

    def _payload(self, items, with_catalog):
        session, detail = (SESSION_OK, None) if self.trouble is None else self.trouble
        return {'version': '%s/%s' % (VERSION, platform.python_version()), 'agent_id': self.agent_id,
                'session': session, 'detail': detail, 'pause_sec': self._trouble_pause(), 'account': self.account,
                'vacancies': self.vacancies if with_catalog else None, 'items': items}

    def _apply_reply(self, reply):
        settings = reply.get('settings') if isinstance(reply.get('settings'), dict) else {}
        since = parse_moment(settings.get('since'))
        if since is not None:
            if self.since != since:
                log.info('портал: отклики нужны с %s', since.astimezone(timezone(timedelta(hours=5))).strftime('%d.%m %H:%M'))
            self.since = since
        off = settings.get('off')
        if isinstance(off, list):
            off = {str(x) for x in off}
            if off != self.off:
                log.info('портал: выключены вакансии %s', ', '.join(sorted(off)) or '—')
            self.off = off
        by_vacancy = settings.get('since_by_vacancy')
        if isinstance(by_vacancy, dict):
            parsed = {str(key): moment for key, moment in ((key, parse_moment(value)) for key, value in by_vacancy.items())
                      if moment is not None}
            for key in sorted(key for key in parsed if parsed[key] != self.vacancy_since.get(key)):
                log.info('портал: вакансия %s — отклики с %s', key,
                         parsed[key].astimezone(timezone(timedelta(hours=5))).strftime('%d.%m %H:%M'))
            self.vacancy_since = parsed
        if 'next_poll_sec' in reply:
            self.interval = _clamp_interval(reply.get('next_poll_sec'), self.interval)
        for row in reply.get('results') or []:
            if isinstance(row, dict) and row.get('topic_id'):
                outcome = str(row.get('outcome') or '?')
                self._outcomes[outcome] = self._outcomes.get(outcome, 0) + 1

    def exchange(self):
        """Отдать порталу очередь порциями и состояние; отклик с исходом портала — «отдан», без исхода остаётся в
        очереди до следующего такта. Пустая очередь — один доклад «я на связи». Перечитанный список вакансий едет с
        первой порцией — и со следующим обменом, если этот не дошёл."""
        batches = [self.pending[start:start + PUSH_BATCH] for start in range(0, len(self.pending), PUSH_BATCH)] or [[]]
        for index, batch in enumerate(batches):
            reply = self._post(self._payload(batch, self.catalog_unsent and index == 0))
            self.catalog_unsent = False
            self._apply_reply(reply)
            done = {str(row.get('topic_id')) for row in reply.get('results') or [] if isinstance(row, dict)}
            for item in batch:
                if item['topic_id'] in done and item['topic_id'] not in self._seen:
                    self.state['seen'].append(item['topic_id'])
                    self._seen.add(item['topic_id'])
            self._sent += sum(1 for item in batch if item['topic_id'] in done)
            self.pending = [item for item in self.pending if item['topic_id'] not in done]
            if batch and not done:
                raise RuntimeError('портал не вернул исходов по откликам')
        self._save_state()

    def _portal_failed(self, exc):
        self.portal_failures += 1
        self.healthy = False
        pause = PORTAL_BACKOFF_SECONDS[min(self.portal_failures, len(PORTAL_BACKOFF_SECONDS)) - 1]
        if self.portal_failures in (1, 5) or self.portal_failures % 20 == 0:
            log.warning('портал недоступен (%s), повтор через %d с', exc, pause)
        return float(pause)

    # ── hh ──────────────────────────────────────────────────────────────────

    def _scan(self, vacancy, collection=None):
        """Свежие отклики вакансии, которых портал ещё не видел. Выдача «по дате»: следующая страница нужна, только
        если на этой все отклики ещё новее отсечки (с запасом в сутки — порядок у hh почти, а не строго, по дате).
        Отсечка — общая или своя у вакансии, которую включили позже: её прежние отклики порталу не нужны."""
        since = self.since
        own = self.vacancy_since.get(vacancy['id'])
        if own is not None and (since is None or own > since):
            since = own
        horizon = (since - ORDER_SLACK) if since else None
        fresh = []
        for page in range(MAX_PAGES):
            items = self.site.responses(vacancy, page, collection)
            reached_old = False
            for item in items:
                moment = parse_moment(item.get('responded_at'))
                if moment is not None and horizon is not None and moment < horizon:
                    reached_old = True
                if (item['topic_id'] in self._seen or moment is None
                        or (since is not None and moment < since)):
                    continue
                fresh.append(item)
            if len(items) < PAGE_SIZE or reached_old:
                break
        return fresh

    def read_site(self):
        """Один проход по кабинету: список вакансий (раз в час), счётчики, выдача сдвинувшихся."""
        now = self.clock()
        if self.vacancies is None or self.catalog_at is None or now - self.catalog_at >= CATALOG_SECONDS:
            self.account, self.vacancies = self.site.catalog()
            self.catalog_at = now
            self.catalog_unsent = True
        queued = {item['topic_id'] for item in self.pending}
        fingerprints = self.state['fingerprints']
        for vacancy in self.vacancies:
            if vacancy['id'] in self.off:
                continue
            mark = self.site.counts(vacancy['id'])
            previous = fingerprints.get(vacancy['id'])
            if previous == mark:
                continue
            # «Отклики» — всегда; этапы, где прибавилось, — тоже: свежий отклик могли успеть перевести туда.
            collections = [None] + [state.lower() for state in grown_states(previous, mark)]
            for collection in collections:
                for item in self._scan(vacancy, collection):
                    if item['topic_id'] not in queued:
                        self._attach_resume(item)
                        self.pending.append(item)
                        queued.add(item['topic_id'])
            fingerprints[vacancy['id']] = mark
        live = {vacancy['id'] for vacancy in self.vacancies}
        for key in [key for key in fingerprints if key not in live]:
            fingerprints.pop(key)
        self.site.save_session()

    def _attach_resume(self, item):
        """Резюме и письмо нового отклика (владелец 23.11.2026 выбрал «Полное резюме и письмо»). Страница не открылась —
        отклик всё равно уходит порталу: карточка важнее, резюме остаётся ссылкой на hh. Вход потерян или защита сайта —
        такт прерывается, как на любом запросе (отклик найдётся снова: счётчики вакансии ещё не записаны)."""
        try:
            item['resume'], item['letter'] = self.site.resume(item)
        except SiteTrouble as exc:
            if exc.kind != SITE_ERROR:
                raise
            log.warning('резюме отклика %s не открылось: %s — отклик уйдёт без него', item['topic_id'], exc)
        except Exception:  # noqa: BLE001 — hh поменял страницу резюме: перенос откликов из-за этого не встаёт
            item.pop('resume', None)
            item.pop('letter', None)
            log.exception('резюме отклика %s не разобралось — отклик уйдёт без него', item['topic_id'])

    def _trouble_pause(self):
        """Свой отступ моста, пока кабинет hh не читается; без беды — None."""
        if self.trouble is None:
            return None
        pauses = TROUBLE_PAUSES.get(self.trouble[0]) or TROUBLE_PAUSES['error']
        return float(pauses[min(max(self.trouble_count, 1), len(pauses)) - 1])

    def _next_wait(self):
        """Пауза до следующего чтения hh — та, что назвал портал; кабинет не читается — свой отступ, но не короче
        паузы портала. Пульс — только при живом входе и принятом докладе."""
        pause = self._trouble_pause()
        self.healthy = pause is None
        if pause is None:
            self._heartbeat()
            return self.interval
        return max(pause, self.interval)

    def cycle(self):
        """Один такт. Возвращает паузу до следующего."""
        first = self.since is None
        if first or self.portal_failures:
            # Первый обмен — за настройками: без отсечки мост hh не читает, чтобы не нести порталу всю историю. Прошлый
            # доклад не дошёл — сначала дослать его, а hh не читать, пока портал не ответит: иначе в простой портала
            # мост опрашивал бы hh каждые пару минут, и ночью тоже.
            try:
                self.exchange()
            except Exception as exc:  # noqa: BLE001
                return self._portal_failed(exc)
            if self.portal_failures:
                log.info('портал снова отвечает (после %d отказов)', self.portal_failures)
            self.portal_failures = 0
            if not first:
                return self._next_wait()
        try:
            self.read_site()
            if self.trouble is not None:
                log.info('кабинет hh снова читается (после %d неудачных попыток)', self.trouble_count)
            self.trouble, self.trouble_count = None, 0
        except SiteTrouble as exc:
            self.trouble_count += 1
            if self.trouble is None or self.trouble[0] != exc.kind or self.trouble_count in (5, 20):
                log.warning('кабинет hh: %s', exc)
            self.trouble = (exc.kind, str(exc)[:300])
        try:
            self.exchange()
        except Exception as exc:  # noqa: BLE001
            return self._portal_failed(exc)
        return self._next_wait()

    def _summary(self):
        now = self.clock()
        if self._summary_at is None:
            self._summary_at = now
            return
        if now - self._summary_at < SUMMARY_SECONDS:
            return
        log.info('за %d мин: отдано откликов %d, исходы %s, вход %s, вакансий в опросе %d',
                 int((now - self._summary_at) // 60), self._sent, self._outcomes or '—',
                 'в порядке' if self.trouble is None else self.trouble[0],
                 len([v for v in self.vacancies or [] if v['id'] not in self.off]))
        self._summary_at, self._sent, self._outcomes = now, 0, {}

    def run(self):
        log.info('Мост hh %s запущен. Портал: %s (ключ %s), кабинет: %s, id: %s', VERSION, self.portal,
                 self.signer.key_id if self.signer else '—', self.config['host'], self.agent_id)
        while not self.stop_event.is_set():
            try:
                wait = self.cycle()
            except Exception:  # noqa: BLE001
                log.exception('такт упал')
                # Упал разбор кабинета — следующая попытка не раньше обычной паузы: ошибка в коде не должна учащать
                # опрос hh.
                self.healthy = False
                wait = max(CRASH_PAUSE_SECONDS, self.interval)
            self._summary()
            if self.rest(max(1.0, wait)):
                break
        log.info('Мост hh остановлен')
        return 0

    def rest(self, wait):
        """Пауза до следующего такта; пока последний такт прошёл как надо, пульс обновляется раз в HEARTBEAT_SECONDS
        (ночная пауза — до двух часов, а проверка контейнера ждёт пульс не реже раза в 15 минут). True — мост
        останавливают."""
        left = wait
        while left > 0:
            step = min(left, HEARTBEAT_SECONDS)
            if self.stop_event.wait(step):
                return True
            left -= step
            if left > 0 and self.healthy:
                self._heartbeat()
        return False

    def stop(self, *_args):
        self.stop_event.set()


def check(bridge):
    """Проверка установки: сессия открывает кабинет, вакансии видны, выдача откликов читается, портал принимает
    подпись. Отклики порталу не отправляются — уходит только «я на связи» со списком вакансий. Персональных данных
    кандидатов на экран не выводит: только счёт."""
    ok = True
    print('ключ     %s' % (bridge.signer.key_id if bridge.signer else 'НЕ ЗАДАН'))
    try:
        bridge.account, bridge.vacancies = bridge.site.catalog()
        bridge.catalog_at = bridge.clock()
        account = bridge.account or {}
        print('кабинет  %s — вход: %s, работодатель %s (%s), активных вакансий %d'
              % (bridge.config['host'], account.get('manager'), account.get('employer'), account.get('employer_id'),
                 len(bridge.vacancies)))
        for vacancy in bridge.vacancies:
            mark = json.loads(bridge.site.counts(vacancy['id']))
            items = bridge.site.responses(vacancy, 0)
            with_phone = sum(1 for item in items if item.get('phone'))
            print('вакансия %s «%s» (%s): откликов %s, по состояниям %s; первая страница — %d, с телефоном %d'
                  % (vacancy['id'], vacancy['name'], vacancy.get('area'), vacancy.get('responses'),
                     json.dumps(mark['total'], ensure_ascii=False, sort_keys=True), len(items), with_phone))
        bridge.site.save_session()
    except SiteTrouble as exc:
        ok = False
        bridge.trouble = (exc.kind, str(exc))
        print('кабинет  %s — НЕ читается: %s (%s)' % (bridge.config['host'], exc, exc.kind))
    if not bridge.portal:
        print('портал   не задан (CDR_BRIDGE_PORTAL) — пропущен')
        return 0 if ok else 1
    try:
        reply = bridge._post(bridge._payload([], with_catalog=bridge.vacancies is not None))
        settings = reply.get('settings') or {}
        print('портал   %s — принял: отклики с %s, выключены вакансии %s, пауза до опроса %s с'
              % (bridge.portal, settings.get('since'), ', '.join(settings.get('off') or []) or '—', reply.get('next_poll_sec')))
    except Exception as exc:  # noqa: BLE001
        ok = False
        print('портал   %s — НЕ принял: %s' % (bridge.portal, exc))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Мост hh.kz: отклики кабинета работодателя → «Воронка найма» портала')
    parser.add_argument('--once', action='store_true', help='один такт и выход')
    parser.add_argument('--check', action='store_true', help='проверить кабинет hh и портал, отклики не отправлять')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    config = load_config()
    if not config['session_file']:
        print('Не задано: HH_BRIDGE_SESSION_FILE')
        return 2
    if not args.check and not config['portal']:
        print('Не задано: CDR_BRIDGE_PORTAL')
        return 2
    if not args.check and not (config['key_file'] or config['private_key']):
        print('Не задан ключ подписи (CDR_AGENT_KEY_FILE)')
        return 2
    try:
        bridge = HhBridge(config)
    except ValueError as exc:
        print('Мост не запущен: %s' % exc)
        return 2
    if args.check:
        return check(bridge)
    if args.once:
        wait = bridge.cycle()
        print('такт выполнен, следующий через %.0f с' % wait)
        return 0
    signal.signal(signal.SIGTERM, bridge.stop)
    try:
        return bridge.run()
    except KeyboardInterrupt:
        log.info('Остановлен с клавиатуры')
        return 0


if __name__ == '__main__':
    sys.exit(main())
