# -*- coding: utf-8 -*-
"""Выгрузка заявок Syntony: API заявок v1, только чтение.

    GET https://app.syntony.kz/api/leads-api/v1/leads?createdFrom=…|updatedSince=…
    X-Api-Key: slk_…

Ключ — SYNTONY_LEADS_API_KEY в окружении сервера. Он выпущен в главном аккаунте
сети, поэтому отдаёт заявки всех парков сети (на 19.11.2026 их 30). Ключ
секретный: в журналы и тексты ошибок не попадают ни он, ни заголовки, ни адрес с
параметрами — только HTTP-статус и код ошибки из ответа.

Пределы API: 60 запросов в минуту на ключ, страница до 500 заявок, порядок по
createdAt и id стабилен между запросами, следующая страница — `nextOffset`, пока он
не станет null. Даты в ответе — ISO 8601 в UTC.

Что хранится
------------
Отбивка считает лиды по тегам и utm_source (правила amocrm/leads.py), поэтому из
заявки берётся только это и то, что нужно склейке с amoCRM и разбору: время,
воронка, источник, канал, кто завёл, теги, utm-метки, форма, тип этапа, парк. Имя
и маска телефона отбивке не нужны и не хранятся.

Заявку, по которой отбивке считать нечего (без тегов и меток — чаты ИИ-агента,
рассылки по базе водителей), не храним вовсе: за 14 дней это ~3/4 из 8 тыс. заявок.

OLX (source=olx) не храним намеренно. Syntony заводит такие заявки из событий
нашего робота «Лиды OLX» (olx_amo/partner_events.py) на каждый чат — и без
телефона, — а старые чаты догружает пачками (12.11 — 68 заявок за три минуты,
19.11 — 26 за минуту). Лиды OLX отбивка считает по журналу самого робота.
"""

import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

ENV_KEY = 'SYNTONY_LEADS_API_KEY'
ENV_URL = 'SYNTONY_LEADS_API_URL'
DEFAULT_URL = 'https://app.syntony.kz/api/leads-api/v1/leads'
SYNTONY_DOMAIN = 'syntony.kz'

PAGE_LIMIT = 500
# 60 запросов в минуту на ключ: между страницами — чуть больше секунды. Обычная
# выгрузка «изменённое с прошлого раза» укладывается в одну-две страницы.
PAGE_PAUSE_SECONDS = 1.05
# Предохранитель от зацикливания на чужой ошибке пагинации: 400 страниц по 500 —
# двести тысяч заявок, больше, чем у сети за весь год.
MAX_PAGES = 400
REQUEST_TIMEOUT = (10, 30)

# Лид-формы рекламных кабинетов. Syntony сама ставит им utm_source (facebook /
# tiktok, utm_medium=lead_ads), но если метки однажды не окажется, источник
# заявки всё равно однозначен — храним и такие (см. amocrm.leads.syntony_row).
AD_SOURCES = ('facebook', 'tiktok')
# Источники, которые отбивка берёт не из Syntony (см. docstring модуля).
SKIPPED_SOURCES = ('olx',)

logger = logging.getLogger(__name__)


class SyntonyLeadsError(RuntimeError):
    """Выгрузка не удалась. Текст безопасен для журнала: без ключа и адреса."""


def config():
    """(адрес, ключ) или None, если выгрузку делать нечем.

    Адрес — только https на домене Syntony: ключ уходит туда заголовком, и
    отправить его на посторонний адрес опечаткой в окружении нельзя.
    """
    key = (os.getenv(ENV_KEY) or '').strip()
    url = (os.getenv(ENV_URL) or '').strip() or DEFAULT_URL
    if not key:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or '').lower()
    if parts.scheme != 'https' or not (host == SYNTONY_DOMAIN or host.endswith('.' + SYNTONY_DOMAIN)):
        logger.error('Syntony: %s не https на домене %s — выгрузка заявок выключена',
                     ENV_URL, SYNTONY_DOMAIN)
        return None
    return url, key


def is_configured():
    return config() is not None


def _iso_utc(value):
    """datetime → '2026-11-18T00:00:00Z' (API ждёт ISO 8601 в UTC)."""
    if value.tzinfo is None:
        raise ValueError('updated_since должен быть с часовым поясом')
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _get(session, url, key, params):
    """Одна страница. Обрыв соединения повторяется один раз: запрос только читает."""
    headers = {'X-Api-Key': key, 'Accept': 'application/json'}
    for attempt in (1, 2):
        try:
            response = session.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            break
        except requests.exceptions.ConnectionError as exc:
            if attempt == 2:
                raise SyntonyLeadsError('Syntony не отвечает: %s' % type(exc).__name__) from None
            time.sleep(1.0)
        except requests.exceptions.Timeout:
            raise SyntonyLeadsError('Syntony не ответила вовремя') from None
    if response.status_code == 429:
        raise SyntonyLeadsError('Syntony: превышен предел запросов (429)')
    try:
        body = response.json()
    except ValueError:
        raise SyntonyLeadsError('Syntony: ответ %s не JSON' % response.status_code) from None
    if response.status_code != 200:
        code = body.get('error') if isinstance(body, dict) else None
        raise SyntonyLeadsError(
            ('Syntony: HTTP %s %s' % (response.status_code, code or '')).strip())
    if not isinstance(body, dict) or not isinstance(body.get('data'), list):
        raise SyntonyLeadsError('Syntony: в ответе нет списка заявок')
    return body


def fetch(created_from=None, updated_since=None, session=None, sleep=time.sleep):
    """Все заявки под фильтром — список сырых заявок API.

    `created_from` — date (день в UTC, как велит API), `updated_since` — datetime с
    поясом. Ровно одно из двух: полная выгрузка окна или «изменённое с прошлого
    раза».
    """
    settings = config()
    if not settings:
        raise SyntonyLeadsError('Syntony: не задан %s' % ENV_KEY)
    if (created_from is None) == (updated_since is None):
        raise ValueError('нужен ровно один фильтр: created_from или updated_since')
    url, key = settings
    params = {'limit': PAGE_LIMIT, 'offset': 0}
    if created_from is not None:
        params['createdFrom'] = created_from.isoformat()
    else:
        params['updatedSince'] = _iso_utc(updated_since)

    own = session is None
    session = session or requests.Session()
    items = []
    try:
        for page in range(MAX_PAGES):
            body = _get(session, url, key, params)
            items.extend(body['data'])
            next_offset = body.get('nextOffset')
            if next_offset is None:
                return items
            if not isinstance(next_offset, int) or next_offset <= params['offset']:
                raise SyntonyLeadsError('Syntony: странная пагинация (nextOffset=%r)' % (next_offset,))
            params['offset'] = next_offset
            sleep(PAGE_PAUSE_SECONDS)
        raise SyntonyLeadsError('Syntony: больше %d страниц — выгрузка остановлена' % MAX_PAGES)
    finally:
        if own:
            session.close()


def _text(value):
    text = '' if value is None else str(value).strip()
    return text or None


def _when(value):
    """ISO 8601 из API → aware datetime. Пусто или мусор → None."""
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def map_lead(item):
    """Заявка API → строка таблицы syntony_leads. None — отбивке она не нужна.

    Теги — в порядке API, через «, » (как у сделок amoCRM). Порядок важен: первый
    тег — тот, с которым заявка заведена, остальные Syntony дописывает при
    повторных обращениях с того же номера, пока заявка открыта (19.11: один
    проверочный номер прозвонил линии парков, и у заявки iTaxi набралось 15
    тегов `call_itaxi_*`).
    """
    if not isinstance(item, dict):
        return None
    lead_id = _text(item.get('id'))
    created_at = _when(item.get('createdAt'))
    if not lead_id or created_at is None:
        return None
    source = (_text(item.get('source')) or '').lower()
    if source in SKIPPED_SOURCES:
        return None

    tags = []
    for tag in item.get('tags') or []:
        name = _text(tag.get('name')) if isinstance(tag, dict) else None
        if name:
            tags.append(name)
    attribution = item.get('attribution') if isinstance(item.get('attribution'), dict) else {}
    utm_source = _text(attribution.get('utmSource'))
    if not tags and not utm_source and source not in AD_SOURCES:
        return None

    pipeline = item.get('pipeline') if isinstance(item.get('pipeline'), dict) else {}
    stage = item.get('stage') if isinstance(item.get('stage'), dict) else {}
    park = item.get('park') if isinstance(item.get('park'), dict) else {}
    form = attribution.get('form') if isinstance(attribution.get('form'), dict) else {}
    return {
        'lead_id': lead_id,
        'created_at': created_at,
        'updated_at': _when(item.get('updatedAt')),
        'park': _text(park.get('name')),
        'pipeline_kind': _text(pipeline.get('kind')),
        'source': source or None,
        'created_by_channel': _text(item.get('createdByChannel')),
        'created_by': _text(item.get('createdBy')),
        'tags': ', '.join(tags),
        'utm_source': utm_source,
        'utm_medium': _text(attribution.get('utmMedium')),
        'utm_campaign': _text(attribution.get('utmCampaign')),
        'form_name': _text(form.get('name')),
        'stage_type': _text(stage.get('stageType')),
    }


def map_leads(items):
    """Сырые заявки → строки для записи, без нужных отбивке не заявок и без повторов id."""
    rows = {}
    for item in items or []:
        row = map_lead(item)
        if row:
            rows[row['lead_id']] = row
    return list(rows.values())
