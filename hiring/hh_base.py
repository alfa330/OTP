# -*- coding: utf-8 -*-
"""Поиск по базе резюме hh.kz (владелец 25.11.2026). Чистая логика: ни базы, ни Flask.

Что просил владелец
-------------------
«Нужно добавить возможность поиска кандидатов по конкретной вакансии (для которой включен такой поиск) с помощью
ключевых слов с ИИ, а дальше чтобы ИИ могла по портрету сотрудника определять и давать скорбалл кандидату и сохранять
в нашу базу резюме, и добавить кнопку у нас для открытия контактов… взять в работу… с источником База HeadHunter».
«Так же надо предусмотреть обновление резюмешки в момент просмотра через наш сайт».

Как устроено
------------
    руководитель: вакансия hh → «Искать в базе», портрет сотрудника, ключевые слова (подбирает ИИ — hh_ai.py)
    портал ── задание «поиск» ──► мост на шлюзе: страница поиска hh (как рекрутер) ── найденное ──► портал
    портал: резюме в hiring_hh_resumes, балл ИИ по портрету (hh_ai.py), список «База hh» по баллу
    рекрутер открыл резюме ── задание «резюме» ──► мост перечитывает страницу резюме ──► свежее резюме
    «Взять в работу» ── рекрутер жмёт «Открыть контакты» на hh ──► мост читает ФИО и телефон ──► карточка

Контакты открывает человек (решение владельца 25.11.2026, «Щелчок рекрутера на hh»): кнопка «Открыть контакты» на
hh подписывает запрос настоящим щелчком мыши (заголовок X-Requested-GA, код подписи запутан), а программная подделка
подписи — обход защиты, за который hh блокирует аккаунт. Карточка — в «В работе» за тем, кто нажал «Взять в работу»
(владелец 25.11.2026: «В работе» за нажавшим).

Бережность к hh — та же, что у откликов: только GET тех же адресов, что открывает рекрутер; автопоиск — в рабочее
время раз в несколько часов и неглубоко; полное резюме мост открывает, когда его смотрит рекрутер, а сам — только у
сильных по краткой карточке кандидатов и не больше AUTO_FULL_PER_DAY в сутки (просмотр кандидат видит у себя на hh).
"""

import base64
import binascii
import re
from datetime import timedelta

from . import hh, rules

# ─── Настройки поиска вакансии ────────────────────────────────────────────────

PORTRAIT_MAX = 3000
KEYWORDS_MAX = 10
KEYWORD_MAX = 60
# Регион поиска по умолчанию — Алматы (ссылка владельца: area=160), пока мост не назвал регион вакансии.
DEFAULT_AREA = 160

# Автопоиск — в рабочее время Алматы, раз в SEARCH_EVERY: новые резюме появляются в течение дня, ночью их всё
# равно никто не разбирает. Первый поиск — сразу после включения, за 30 дней (как ссылка владельца, search_period=30)
# и по релевантности; дальше — свежие («по дате обновления», за 3 дня), пока не дошли до уже виденных.
SEARCH_HOURS = (9, 19)
SEARCH_EVERY = timedelta(hours=3)
FIRST_PERIOD_DAYS = 30
NEXT_PERIOD_DAYS = 3
FIRST_PAGES = 5
NEXT_PAGES = 3
# «Искать сейчас» — не чаще раза в 10 минут на вакансию: hh видит поиск человека, а не очередь кнопок.
RUN_NOW_GAP = timedelta(minutes=10)
# Свежая выдача «по дате» почти строго упорядочена: страница, где все резюме старше прошлого поиска на сутки, — последняя.
ORDER_SLACK = timedelta(days=1)
PAGE_SIZE = 20

# Полное резюме: рекрутер открыл — мост перечитывает его, если прошлое чтение старше VIEW_FRESH. Сам мост открывает
# полное резюме только у сильных по краткой карточке кандидатов — чтобы балл ИИ учёл опыт, языки и «о себе».
VIEW_FRESH = timedelta(minutes=10)
AUTO_FULL_MIN_SCORE = 70
AUTO_FULL_PER_DAY = 30
# Имя на карточке (владелец 26.11.2026: «чтобы в карточках отображались имена кандидатов, а не вакансия»): hh показывает
# его только у резюме с контактами без списания или уже открытыми — новые приходят с именем прямо из поиска, а найденным
# раньше (мост до hh/1.4.2 имён не брал) мост дочитывает страницу резюме — тем же фоновым темпом, своим суточным пределом.
# Резюме, где hh имени так и не показал, повторно не читаются неделю.
AUTO_NAME_PER_DAY = 40
NAME_RETRY = timedelta(days=7)
BACKGROUND_REASONS = ('score', 'name')

# Задание ушло мосту, а результата нет (мост перезапустился, связь оборвалась) — через TASK_LEASE оно уходит снова; после
# TASK_ATTEMPTS попыток считается неудачным.
TASK_LEASE = timedelta(minutes=5)
TASK_ATTEMPTS = 3
TASK_KINDS = ('search', 'resume')
# Чем меньше, тем раньше мост берёт задание: контакты после «Взять в работу» и резюме, открытое рекрутером, — сразу;
# поиск — следом; полное резюме для балла и имя — когда больше нечего делать.
PRIORITY = {'contacts': 1, 'view': 2, 'manual': 3, 'auto': 5, 'score': 7, 'name': 8}
# После «Взять в работу» мост проверяет, открыл ли рекрутер контакты на hh: сразу, потом через минуту, три и десять. Плюс
# проверка, когда рекрутер возвращается во вкладку iCORE. Дальше — только кнопкой «Проверить контакты».
CONTACTS_WATCH_SECONDS = (60, 180, 600)
CONTACTS_FIRST_CHECK = timedelta(seconds=20)
TAKING_STALE = timedelta(minutes=30)

STATUSES = ('new', 'taking', 'taken', 'rejected')
ACCESS = ('closed', 'free', 'opened')
ACCESS_LABELS = {
    'closed': 'Контакты закрыты: откроются на hh за 1 контакт',
    'free': 'Контакты доступны без списания: кандидат уже общался с компанией',
    'opened': 'Контакты уже открыты',
}
# Статус поиска работы на hh (job_search_status) — подписи как на сайте.
JOB_STATUS_LABELS = {
    'active_search': 'Активно ищет работу',
    'looking_for_offers': 'Рассматривает предложения',
    'has_job_offer': 'Предложили работу, решает',
    'accepted_job_offer': 'Вышел на новое место',
    'not_looking_for_job': 'Не ищет работу',
}
# Тип переписки кандидата с компанией на hh (initialTopicType).
TOPIC_LABELS = {'RESPONSE_BY_APPLICANT': 'откликался', 'INVITATION_BY_EMPLOYER': 'приглашали'}

_HASH = re.compile(r'^[0-9a-f]{16,64}$')
_DIGITS = re.compile(r'^\d{1,18}$')
_HOST = 'https://hh.kz'
# Слова языка запросов hh: отдельной «фразой» они ломали бы запрос (… OR OR …).
_OPERATORS = ('or', 'and', 'not', 'или', 'и', 'не')


class ProfileError(ValueError):
    """Настройка поиска с ошибкой — текст для человека."""


def _text(value, limit):
    text = ' '.join(str(value or '').split())
    return text[:limit] if text else None


def _digits(value):
    text = str(value if value is not None else '').strip()
    return text if _DIGITS.match(text) else None


def _small_int(value, limit):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= limit else None


def is_hash(value):
    return bool(_HASH.match(str(value or '')))


def resume_url(resume_hash):
    """Резюме в кабинете работодателя на hh — здесь рекрутер жмёт «Открыть контакты»."""
    return '%s/resume/%s' % (_HOST, resume_hash) if is_hash(resume_hash) else None


def clean_keywords(raw):
    """Ключевые слова поиска: фраза — как кандидаты называют должность в резюме. Повторы и пустые выпадают; кавычки и
    скобки — синтаксис языка запросов hh — убираются, чтобы фраза не сломала запрос."""
    if isinstance(raw, str):
        raw = re.split(r'[\n,;]+', raw)
    out, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        text = _text(re.sub(r'["«»()\[\]{}!*]+', ' ', str(item or '')), KEYWORD_MAX)
        if not text:
            continue
        key = text.lower()
        if key in seen or key in _OPERATORS:
            continue
        seen.add(key)
        out.append(text)
    return out[:KEYWORDS_MAX]


def clean_profile(raw):
    """Правка руководителя: {enabled, portrait, keywords} → чистый словарь. Включить поиск без ключевых слов нельзя:
    hh нечего искать; без портрета — тоже: ИИ не по чему ставить балл."""
    raw = raw if isinstance(raw, dict) else {}
    portrait = hh._long_text(raw.get('portrait'), PORTRAIT_MAX)
    keywords = clean_keywords(raw.get('keywords'))
    enabled = raw.get('enabled') is True
    if enabled and not keywords:
        raise ProfileError('Добавьте хотя бы одно ключевое слово — по ним hh ищет резюме')
    if enabled and not portrait:
        raise ProfileError('Опишите портрет сотрудника — по нему ИИ ставит балл кандидату')
    return {'enabled': enabled, 'portrait': portrait, 'keywords': keywords}


def search_text(keywords):
    """Строка запроса hh: фразы через OR, многословные — в кавычках (поиск фразой, а не по отдельным словам)."""
    parts = []
    for keyword in clean_keywords(keywords):
        parts.append('"%s"' % keyword if ' ' in keyword or '-' in keyword else keyword)
    return ' OR '.join(parts)


def search_task(profile, now, *, manual=False):
    """Задание мосту «поиск»: параметры страницы поиска hh и сколько страниц листать. Первый поиск вакансии (или после
    смены ключевых слов) — за 30 дней по релевантности, дальше — свежие резюме по дате обновления до уже виденных."""
    first = profile.get('last_run_at') is None
    params = {
        'text': search_text(profile.get('keywords')),
        'area': str(profile.get('area_id') or DEFAULT_AREA),
        'search_period': str(FIRST_PERIOD_DAYS if first else NEXT_PERIOD_DAYS),
        'order_by': 'relevance' if first else 'publication_time',
        # Только в названии резюме — желаемой должности (проба 25.11.2026: по всему тексту резюме на «оператор колл-центра»
        # приходили аналитики и дизайнеры — фраза в описании опыта; по названию 400 из 400 — операторы).
        'pos': 'position', 'logic': 'normal', 'exp_period': 'all_time',
        'relocation': 'living_or_relocation', 'currency_code': 'KZT', 'ored_clusters': 'true',
    }
    stop_before = None
    if not first and profile.get('last_run_at'):
        stop_before = (profile['last_run_at'] - ORDER_SLACK).replace(tzinfo=rules.ALMATY).isoformat(timespec='seconds')
    return {'params': params, 'pages': FIRST_PAGES if first else NEXT_PAGES, 'stop_before': stop_before,
            'first': first, 'reason': 'manual' if manual else 'auto'}


def search_due(profile, now):
    """Пора ли автопоиску этой вакансии: включена, есть что искать, рабочее время и прошло SEARCH_EVERY. Первый поиск —
    сразу после включения, в любое время дня."""
    if not profile.get('enabled') or not profile.get('keywords'):
        return False
    last = profile.get('last_run_at')
    if last is None:
        return True
    start, end = SEARCH_HOURS
    if not start <= now.hour < end:
        return False
    return now - last >= SEARCH_EVERY


def run_now_allowed(profile, now):
    """«Искать сейчас»: не чаще RUN_NOW_GAP после прошлого поиска или прошлой просьбы."""
    moments = [moment for moment in (profile.get('last_run_at'), profile.get('requested_at')) if moment]
    return not moments or now - max(moments) >= RUN_NOW_GAP


# ─── Что приносит мост ─────────────────────────────────────────────────────────

def _salary(raw):
    raw = raw if isinstance(raw, dict) else {}
    amount = _small_int(raw.get('amount'), 10 ** 10)
    currency = _text(raw.get('currency'), 8)
    if not amount:
        return None
    return {'amount': amount, 'currency': currency, 'sign': hh.CURRENCY_SIGNS.get(currency or '', currency)}


def _jobs(raw, limit):
    out = []
    for item in (raw if isinstance(raw, list) else [])[:limit]:
        if not isinstance(item, dict):
            continue
        entry = {'company': _text(item.get('company'), 200), 'position': _text(item.get('position'), 200),
                 'start': _text(item.get('start'), 10), 'end': _text(item.get('end'), 10)}
        if entry['company'] or entry['position']:
            out.append(entry)
    return out


PHOTO_MAX_BYTES = 200 * 1024
_PHOTO_MAGIC = (('image/jpeg', b'\xff\xd8\xff'), ('image/png', b'\x89PNG\r\n\x1a\n'))


def clean_photo(raw):
    """Фото от моста ({type, data: base64}) → (тип, байты) или None. Тип — по самим байтам (JPEG, PNG, WebP), а не по
    слову моста: отдаётся браузеру как картинка, чужое содержимое под видом фото не пройдёт."""
    if not isinstance(raw, dict) or not isinstance(raw.get('data'), str):
        return None
    try:
        data = base64.b64decode(raw['data'], validate=True)
    except (ValueError, binascii.Error):
        return None
    if not 0 < len(data) <= PHOTO_MAX_BYTES:
        return None
    for content_type, magic in _PHOTO_MAGIC:
        if data.startswith(magic):
            return content_type, data
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp', data
    return None


def clean_name(raw):
    """ФИО от моста ({last_name, first_name, middle_name}) → «Фамилия Имя Отчество» или None."""
    if not isinstance(raw, dict):
        return None
    name = ' '.join(part for part in (_text(raw.get(key), 80) for key in ('last_name', 'first_name', 'middle_name')) if part)
    return name[:200] or None


def clean_found(raw):
    """Резюме из выдачи поиска (краткая карточка) → поля для базы или None (нет хеша — не резюме). Контактов в выдаче
    портал не берёт; имя — только если hh его показал (контакты без списания или уже открыты)."""
    if not isinstance(raw, dict):
        return None
    resume_hash = str(raw.get('hash') or '').strip().lower()
    if not is_hash(resume_hash):
        return None
    topic = raw.get('topic') if isinstance(raw.get('topic'), dict) else None
    if topic:
        topic = {'vacancy_id': _digits(topic.get('vacancy_id')), 'topic_id': _digits(topic.get('topic_id')),
                 'type': _text(topic.get('type'), 40), 'at': hh.parse_moment(topic.get('at'))}
        topic['at'] = topic['at'].isoformat(timespec='minutes') if topic['at'] else None
        if not topic['topic_id']:
            topic = None
    access = raw.get('access') if raw.get('access') in ACCESS else None
    job_status = raw.get('job_status') if raw.get('job_status') in JOB_STATUS_LABELS else None
    snippet = {
        'title': _text(raw.get('title'), 300),
        'age': _small_int(raw.get('age'), 120),
        'gender': hh._label('gender', raw.get('gender')),
        'area': _text(raw.get('area'), 120),
        'salary': _salary(raw.get('salary')),
        'experience_months': _small_int(raw.get('experience_months'), 1200),
        'experience': _jobs(raw.get('experience'), 8),
        'skills': [text for text in (_text(item, 100) for item in (raw.get('skills') or [])[:40]
                                     if isinstance(item, (str, int))) if text],
        'job_status': job_status,
        'online': raw.get('online') is True,
    }
    resume_id = _digits(raw.get('resume_id'))
    return {'hash': resume_hash, 'resume_id': int(resume_id) if resume_id else None, 'snippet': snippet,
            'hh_updated_at': hh.parse_moment(raw.get('updated_at')),
            'last_activity_at': hh.parse_moment(raw.get('last_activity_at')),
            'access': access, 'topic': topic, 'full_name': clean_name(raw.get('name'))}


def clean_contacts(raw):
    """ФИО, телефон и почта со страницы резюме, где контакты уже открыты. Телефон — казахстанский, к виду +7…;
    другого нет — карточку не завести (ключ дубля — телефон), и это видно рекрутеру."""
    if not isinstance(raw, dict):
        return None
    full_name = clean_name(raw) or ''
    phones = raw.get('phones') if isinstance(raw.get('phones'), list) else [raw.get('phone')]
    phone = next((normalized for normalized in (rules.normalize_phone(item) for item in phones[:5] if item) if normalized),
                 None)
    email = _text(raw.get('email'), 120)
    if not (full_name or phone or email):
        return None
    return {'full_name': full_name[:200], 'phone': phone, 'email': email,
            'phone_raw': _text(next((item for item in phones[:5] if item), None), 40)}


# ─── Что видит рекрутер ────────────────────────────────────────────────────────

def experience_text(months):
    """«3 года 2 мес.» из числа месяцев."""
    span = hh._span(None, months)
    if not span:
        return None
    years, rest = span['years'], span['months']
    parts = []
    if years:
        parts.append('%d %s' % (years, _plural(years, 'год', 'года', 'лет')))
    if rest:
        parts.append('%d мес.' % rest)
    return ' '.join(parts)


def _plural(number, one, few, many):
    number = abs(int(number)) % 100
    if 11 <= number <= 19:
        return many
    number %= 10
    if number == 1:
        return one
    if 2 <= number <= 4:
        return few
    return many


def candidate_text(snippet, resume=None):
    """Резюме кандидата словами — для ИИ: краткая карточка из поиска, полное резюме, если мост его уже открыл. Имён и
    контактов здесь нет и быть не может."""
    snippet = snippet or {}
    resume = resume or {}
    lines = []
    title = resume.get('title') or snippet.get('title')
    if title:
        lines.append('Желаемая должность: %s' % title)
    facts = []
    age = resume.get('age') or snippet.get('age')
    if age:
        facts.append('%s лет' % age)
    gender = resume.get('gender') or snippet.get('gender')
    if gender:
        facts.append(gender)
    area = resume.get('area') or snippet.get('area')
    if area:
        facts.append(area)
    salary = resume.get('salary') or snippet.get('salary')
    if salary and salary.get('amount'):
        facts.append('ожидает %s %s' % ('{:,}'.format(salary['amount']).replace(',', ' '), salary.get('sign') or ''))
    if facts:
        lines.append('О кандидате: ' + ', '.join(fact.strip() for fact in facts))
    total = None
    if resume.get('experience_total'):
        span = resume['experience_total']
        total = experience_text(span['years'] * 12 + span['months'])
    elif snippet.get('experience_months') is not None:
        total = experience_text(snippet['experience_months']) or 'нет'
    if total:
        lines.append('Общий стаж: %s' % total)
    jobs = resume.get('experience') or snippet.get('experience') or []
    if jobs:
        lines.append('Опыт работы:')
        for job in jobs[:8]:
            period = '%s — %s' % (job.get('start') or '?', job.get('end') or 'сейчас')
            line = '- %s, %s (%s)' % (job.get('position') or 'должность не указана', job.get('company') or 'компания не указана',
                                      period)
            if job.get('description'):
                line += ': ' + ' '.join(str(job['description']).split())[:400]
            lines.append(line)
    for key, label in (('employment', 'Занятость'), ('schedule', 'График'), ('formats', 'Формат работы')):
        if resume.get(key):
            lines.append('%s: %s' % (label, ', '.join(resume[key])))
    if resume.get('education_level'):
        lines.append('Образование: %s' % resume['education_level'])
    if resume.get('languages'):
        lines.append('Языки: %s' % ', '.join('%s%s' % (item['title'], ' (%s)' % item['level'] if item.get('level') else '')
                                            for item in resume['languages']))
    skills = resume.get('key_skills') or snippet.get('skills') or []
    if skills:
        lines.append('Навыки: %s' % ', '.join(skills[:25]))
    if resume.get('about'):
        lines.append('О себе: %s' % ' '.join(str(resume['about']).split())[:700])
    if snippet.get('job_status'):
        lines.append('Статус на hh: %s' % JOB_STATUS_LABELS[snippet['job_status']])
    if not resume:
        lines.append('(это краткая карточка из поиска: полного резюме — описаний опыта, языков, «о себе» — ещё нет)')
    return '\n'.join(lines)


def clean_score(raw):
    """Ответ ИИ по одному кандидату → {score, verdict, pros, cons} или None."""
    if not isinstance(raw, dict):
        return None
    try:
        score = int(round(float(raw.get('score'))))
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0 <= score <= 100:
        return None
    def points(value):
        return [text for text in (_text(item, 120) for item in (value if isinstance(value, list) else [])[:10]) if text][:3]

    return {'score': score, 'verdict': _text(raw.get('verdict'), 200), 'pros': points(raw.get('pros')),
            'cons': points(raw.get('cons'))}


def snippet_resume(snippet):
    """Краткая карточка из поиска в виде резюме карточки (его умеет показать вкладка «Резюме»), пока полного нет."""
    snippet = snippet or {}
    months = snippet.get('experience_months')
    return {
        'title': snippet.get('title'), 'salary': snippet.get('salary'), 'age': snippet.get('age'),
        'gender': snippet.get('gender'), 'area': snippet.get('area'), 'metro': None, 'citizenship': [], 'relocation': None,
        'business_trips': None, 'travel_time': None, 'employment': [], 'schedule': [], 'formats': [],
        'experience_total': hh._span(None, months) if months else None,
        'experience': [{'company': job.get('company'), 'area': None, 'position': job.get('position'),
                        'start': job.get('start'), 'end': job.get('end'), 'span': None, 'description': None}
                       for job in snippet.get('experience') or []],
        'education_level': None, 'education': [], 'courses': [], 'languages': [],
        'key_skills': list(snippet.get('skills') or []), 'about': None, 'driver_license': [], 'has_vehicle': None,
        'medical_book': None,
    }


def card_entries(rows):
    """Резюме из базы hh у карточки — в том же виде, что резюме откликов (вкладка «Резюме» карточки)."""
    out = []
    for row in rows or []:
        data = row.get('score_data') or {}
        out.append({'topic_id': 'base:%s' % row['resume_hash'], 'vacancy_id': str(row.get('hh_vacancy_id') or ''),
                    'vacancy_name': row.get('vacancy_name'), 'responded_at': row.get('status_at'),
                    'resume_url': resume_url(row['resume_hash']), 'letter': None,
                    'resume': row.get('resume') or snippet_resume(row.get('snippet')), 'source': 'base',
                    'score': row.get('score'), 'verdict': data.get('verdict')})
    return out


def bridge_ready(version):
    """Мост умеет задания поиска по базе — с hh/1.4.0 (до него задания некому забирать)."""
    match = re.match(r'^hh/(\d+)\.(\d+)', str(version or ''))
    return bool(match) and (int(match.group(1)), int(match.group(2))) >= (1, 4)


def contacts_next_check(taken_at, now):
    """Когда мосту в следующий раз смотреть, открыл ли рекрутер контакты на hh: по CONTACTS_WATCH_SECONDS от «Взять в
    работу»; все проверки прошли — None (дальше кнопкой)."""
    for seconds in CONTACTS_WATCH_SECONDS:
        moment = taken_at + timedelta(seconds=seconds)
        if moment > now:
            return moment
    return None
