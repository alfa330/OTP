# -*- coding: utf-8 -*-
"""Отклики HeadHunter (ТЗ 5.2): API hh.ru от имени работодателя.

Нужны переменные окружения (без них интеграция «не настроена», раздел это показывает):

    HH_ACCESS_TOKEN   токен приложения работодателя (OAuth, выдаётся в dev.hh.ru)
    HH_USER_AGENT     обязательный у hh.ru заголовок вида «iCore/1.0 (почта)»
    HH_EMPLOYER_ID    id работодателя; без него берётся из GET /me

Как опрашивается: активные вакансии работодателя → по каждой отклики (коллекция
`response`), новые с прошлого опроса → по каждому резюме контакты (GET /resumes/{id}
?with_contact=true, телефон типа cell). Отклик без телефона в пул не попадает — телефон
здесь ключ (ТЗ 5.3), раздел покажет это в состоянии интеграции.

Транспорт подменяемый — тесты ходят в словари, не в сеть.
"""

import logging
import os
from datetime import datetime, timedelta

from . import rules

log = logging.getLogger(__name__)

API = 'https://api.hh.ru'
PAGES_PER_VACANCY = 2
PER_PAGE = 50
SEEN_LIMIT = 3000
TIMEOUT = 20


class HHError(Exception):
    pass


def config_from_env(environ=None):
    env = environ if environ is not None else os.environ
    token = str(env.get('HH_ACCESS_TOKEN') or '').strip()
    agent = str(env.get('HH_USER_AGENT') or '').strip()
    if not token or not agent:
        return None
    return {'token': token, 'user_agent': agent, 'employer_id': str(env.get('HH_EMPLOYER_ID') or '').strip() or None}


def _requests_transport(method, url, headers, params):
    import requests
    response = requests.request(method, url, headers=headers, params=params, timeout=TIMEOUT)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return response.status_code, payload


def _get(config, path, params=None, transport=None):
    transport = transport or _requests_transport
    headers = {'Authorization': 'Bearer ' + config['token'], 'HH-User-Agent': config['user_agent'],
               'User-Agent': config['user_agent']}
    status, payload = transport('GET', API + path, headers, params or {})
    if status in (401, 403):
        raise HHError('hh.ru отверг токен (%s)' % status)
    if status == 429:
        raise HHError('hh.ru: превышен лимит запросов')
    if status >= 400 or payload is None:
        raise HHError('hh.ru ответил %s на %s' % (status, path))
    return payload


def _parse_moment(text):
    """«2026-10-29T12:34:56+0300» → наивное время Алматы."""
    if not text:
        return None
    value = str(text).strip()
    if len(value) >= 5 and value[-5] in '+-' and value[-3] != ':':
        value = value[:-2] + ':' + value[-2:]
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(rules.ALMATY).replace(tzinfo=None)
    return moment


def _phone_from_resume(resume):
    best = None
    for contact in (resume or {}).get('contact') or []:
        kind = ((contact.get('type') or {}).get('id') or '')
        value = contact.get('value')
        raw = None
        if isinstance(value, dict):
            raw = value.get('formatted') or ''.join(str(value.get(key) or '') for key in ('country', 'city', 'number'))
        elif value:
            raw = str(value)
        phone = rules.normalize_phone(raw)
        if not phone:
            continue
        if kind == 'cell' or contact.get('preferred'):
            return phone
        best = best or phone
    return best


def _full_name(resume):
    return ' '.join(str(resume.get(key) or '').strip() for key in ('last_name', 'first_name', 'middle_name')).strip()


def fetch_responses(config, state, transport=None):
    """(отклики, новая закладка, замечания). Отклик — {full_name, phone, city, vacancy, ref, received_at}."""
    state = dict(state or {})
    seen = list(state.get('seen') or [])
    seen_set = set(seen)
    since = _parse_moment(state.get('since')) or (rules.now_almaty() - timedelta(days=1))
    problems = []

    employer_id = config.get('employer_id')
    if not employer_id:
        me = _get(config, '/me', transport=transport)
        employer_id = str(((me or {}).get('employer') or {}).get('id') or '')
        if not employer_id:
            raise HHError('Токен не принадлежит работодателю: у /me нет employer.id')

    vacancies = []
    page = 0
    while True:
        payload = _get(config, '/employers/%s/vacancies/active' % employer_id,
                       {'page': page, 'per_page': PER_PAGE}, transport=transport)
        vacancies.extend(payload.get('items') or [])
        page += 1
        if page >= int(payload.get('pages') or 1) or page > 10:
            break

    items = []
    newest = since
    for vacancy in vacancies:
        vacancy_id = vacancy.get('id')
        if not vacancy_id:
            continue
        for page in range(PAGES_PER_VACANCY):
            try:
                payload = _get(config, '/negotiations/response',
                               {'vacancy_id': vacancy_id, 'page': page, 'per_page': PER_PAGE,
                                'order_by': 'created_at', 'order': 'desc'}, transport=transport)
            except HHError as exc:
                problems.append('%s: %s' % (vacancy.get('name') or vacancy_id, exc))
                break
            batch = payload.get('items') or []
            stop = False
            for negotiation in batch:
                negotiation_id = str(negotiation.get('id') or '')
                created = _parse_moment(negotiation.get('created_at'))
                if created and created < since:
                    stop = True
                    continue
                if not negotiation_id or negotiation_id in seen_set:
                    continue
                resume = negotiation.get('resume') or {}
                resume_id = resume.get('id')
                phone = _phone_from_resume(resume)
                if not phone and resume_id:
                    try:
                        full = _get(config, '/resumes/%s' % resume_id, {'with_contact': 'true'}, transport=transport)
                        phone = _phone_from_resume(full)
                        resume = dict(full, **{k: v for k, v in resume.items() if k not in full})
                    except HHError as exc:
                        problems.append('резюме %s: %s' % (resume_id, exc))
                seen.append(negotiation_id)
                seen_set.add(negotiation_id)
                if created and created > newest:
                    newest = created
                items.append({
                    'full_name': _full_name(resume),
                    'phone': phone,
                    'city': ((resume.get('area') or {}).get('name')),
                    'vacancy': (negotiation.get('vacancy') or vacancy).get('name') or vacancy.get('name'),
                    'ref': resume.get('alternate_url') or ('hh:negotiation:%s' % negotiation_id),
                    'received_at': created,
                })
            if stop or len(batch) < PER_PAGE:
                break

    new_state = {'since': (newest - timedelta(hours=1)).isoformat(timespec='seconds'), 'seen': seen[-SEEN_LIMIT:],
                 'employer_id': employer_id}
    return items, new_state, problems
