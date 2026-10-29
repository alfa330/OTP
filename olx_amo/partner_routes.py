# -*- coding: utf-8 -*-
"""Чаты OLX для партнёров: вход по ключу партнёра с областью «Чаты OLX».

    GET    /api/olx/ping                                  ключ принят? кабинеты, события
    GET    /api/olx/cabinets                              девять кабинетов и их состояние
    GET    /api/olx/threads?cabinet=&awaiting=1&since=    чаты, которые видел опрос
    GET    /api/olx/threads/<cabinet>/<thread>            переписка живьём из OLX
    POST   /api/olx/threads/<cabinet>/<thread>/messages   {"text": "…"} — ответ кандидату
    GET    /api/olx/journal?cabinet=&from=&to=&result=    журнал откликов робота
    GET    /api/olx/webhook                               подписка на события
    PUT    /api/olx/webhook                               {"url", "secret"?, "events"?}
    DELETE /api/olx/webhook                               снять подписку
    GET    /api/olx/webhook/deliveries                    последние доставки
    POST   /api/olx/webhook/test                          прислать событие ping

У OLX вебхуков нет: новое в чатах узнаёт наш опрос раз в 30 секунд
(olx_amo/service.py), и события партнёру шлём мы сами (partner_events.py →
partner_keys/webhooks.py). Поэтому «подписка» здесь — адрес и секрет партнёра,
а не что-то на стороне OLX.

Сторож тот же, что у остальных входов партнёров: ключ (partner_keys.auth) с
областью, предел вызовов в минуту (OLX_API_PER_MINUTE, 120) и строка журнала
на вызов. Чтение переписки и ответ ходят в OLX живьём и тратят общий с роботом
лимит запросов OLX — частый опрос чатов через нас партнёру не нужен, для этого
есть события.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from partner_keys import auth as partner_auth
from partner_keys import journal as partner_journal
from partner_keys import scopes as partner_scopes
from partner_keys import webhooks as partner_webhooks
from partner_keys.limits import RateLimiter

from . import cabinets, partner_events, queries

log = logging.getLogger(__name__)

SCOPE = partner_scopes.OLX_CHATS
DEFAULT_PER_MINUTE = 120
MAX_PER_MINUTE = 600
MAX_BODY_BYTES = 64 * 1024
THREADS_PAGE_MAX = 200
JOURNAL_PAGE_MAX = 500

_ALMATY = timezone(timedelta(hours=5))

# Отказ отправки → ответ партнёру. Те же причины, что у раздела (routes._refusal).
_REFUSALS = {
    'empty': (400, 'OLX_API_BAD_REQUEST'),
    'too_long': (400, 'OLX_API_BAD_REQUEST'),
    'duplicate': (409, 'OLX_API_DUPLICATE'),
    'rate_limited': (429, 'OLX_API_RATE_LIMITED'),
    'needs_auth': (503, 'OLX_API_CABINET_UNAVAILABLE'),
    'not_configured': (503, 'OLX_API_CABINET_UNAVAILABLE'),
    'disabled': (503, 'OLX_API_CABINET_UNAVAILABLE'),
    'olx_error': (502, 'OLX_API_UPSTREAM'),
}


def _setting(environ, name, default, ceiling):
    raw = str((environ or os.environ).get(name) or '').strip()
    if not raw.isdigit():
        return default
    return max(1, min(int(raw), ceiling))


def parse_moment(value):
    """ISO 8601 → наивное время Алматы (как в базе). None — не разобрать."""
    text = str(value or '').strip().replace('Z', '+00:00')
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(_ALMATY).replace(tzinfo=None)
    return moment


class Services(object):
    """Всё, что ручка берёт у раздела и базы — одним объектом, чтобы тесты
    подменяли его целиком и не ходили ни в OLX, ни в Postgres."""

    def __init__(self, db):
        self.db = db

    def cabinets(self):
        with self.db._get_cursor() as cursor:
            rows = queries.health(cursor)
        by_code = {row.get('code'): row for row in rows}
        items = []
        for cabinet in cabinets.CABINETS:
            row = by_code.get(cabinet.code) or {}
            items.append({
                'code': cabinet.code, 'title': cabinet.title, 'olx_id': cabinet.olx_id,
                'line_phone': cabinet.line_phone,
                'enabled': bool(row.get('is_enabled', True)),
                'state': row.get('state') or 'not_configured',
                'last_poll_at': partner_events.iso(row.get('last_poll_at')),
                'last_message_at': partner_events.iso(row.get('last_message_at')),
                'last_lead_at': partner_events.iso(row.get('last_lead_at')),
                'stale': bool(row.get('is_stale', True)),
            })
        return items

    def threads(self, **filters):
        with self.db._get_cursor() as cursor:
            return queries.partner_threads(cursor, **filters)

    def conversation(self, cabinet, thread_id):
        from . import service
        return service.read_conversation(self.db, cabinet, thread_id)

    def reply(self, cabinet, thread_id, text, actor):
        from . import service
        return service.reply_from_portal(self.db, cabinet, thread_id, text, actor)

    def refused_type(self):
        from .service import ReplyRefused
        return ReplyRefused

    def journal(self, **filters):
        with self.db._get_cursor() as cursor:
            return queries.journal_page(cursor, **filters)

    def subscription(self, key_id):
        with self.db._get_cursor() as cursor:
            item = partner_webhooks.get_subscription(cursor, key_id, SCOPE)
            stats = partner_webhooks.delivery_stats(cursor, item['id']) if item else None
        return item, stats

    def save_subscription(self, key_id, *, url, secret, events):
        with self.db._get_cursor() as cursor:
            return partner_webhooks.upsert_subscription(cursor, key_id, SCOPE, url=url,
                                                        secret=secret, events=events)

    def delete_subscription(self, key_id):
        with self.db._get_cursor() as cursor:
            return partner_webhooks.delete_subscription(cursor, key_id, SCOPE)

    def deliveries(self, key_id, limit):
        with self.db._get_cursor() as cursor:
            item = partner_webhooks.get_subscription(cursor, key_id, SCOPE)
            if not item:
                return None
            return partner_webhooks.recent_deliveries(cursor, item['id'], limit)

    def ping(self, key_id):
        return partner_events.ping(self.db, key_id)


def build_olx_partner_blueprint(*, db, client_ip=None, services=None, environ=None, per_minute=None):
    """Фабрика: зависимости аргументами, bot_schedule2 не импортируется."""
    bp = Blueprint('olx_partner', __name__, url_prefix='/api/olx')
    environ = environ if environ is not None else os.environ
    per_minute = per_minute or _setting(environ, 'OLX_API_PER_MINUTE', DEFAULT_PER_MINUTE, MAX_PER_MINUTE)
    limiter = RateLimiter(per_minute)
    svc = services or Services(db)

    def _ip():
        try:
            return client_ip() if client_ip else None
        except Exception:                                # noqa: BLE001
            return None

    def _refuse(status, code, message, **extra):
        payload = {'error': message, 'code': code}
        payload.update(extra)
        response = jsonify(payload)
        response.status_code = status
        return response

    @bp.after_request
    def _never_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        return response

    def _authenticate():
        status, key = partner_auth.lookup(
            db, partner_auth.presented_from(request.headers), scope=SCOPE)
        if status == partner_auth.NOT_READY:
            logging.error('olx partner: таблиц ключей нет — вход закрыт; схема не встала')
            return None, _refuse(503, 'OLX_API_NOT_CONFIGURED', 'Доступ по ключу на сервере не настроен')
        if status == partner_auth.NO_SCOPE:
            logging.warning('olx partner: ключу «%s» (%s) не открыты чаты OLX, ip %s',
                            key['name'], key['mark'], _ip() or '-')
            return None, _refuse(403, 'OLX_API_FORBIDDEN', 'Ключу не открыты чаты OLX')
        if status != partner_auth.OK:
            logging.warning('olx partner: ключ не принят (%s, ip %s)', status, _ip() or '-')
            return None, _refuse(401, 'OLX_API_UNAUTHORIZED', 'Ключ не принят')
        allowed, retry_after = limiter.allow(key['id'])
        if not allowed:
            _journal(key, outcome='limit', status=429, summary='предел вызовов в минуту')
            response = _refuse(429, 'OLX_API_LIMIT', 'Слишком много вызовов за минуту — подождите',
                               retry_after=retry_after)
            response.headers['Retry-After'] = str(retry_after)
            return None, response
        return key, None

    def _journal(key, *, outcome, status, summary, details=None, started=None):
        partner_journal.record(
            db, key, scope=SCOPE, endpoint=request.path, outcome=outcome, http_status=status,
            latency_ms=int((time.monotonic() - started) * 1000) if started else None,
            summary=summary, details=details, ip=_ip(), user_agent=request.headers.get('User-Agent'))

    def _cabinet(code):
        cabinet = cabinets.get(code)
        if cabinet is None:
            return None, _refuse(404, 'OLX_API_UNKNOWN_CABINET',
                                 'Неизвестный кабинет OLX — список в GET /api/olx/cabinets')
        return cabinet, None

    def _body():
        if request.content_length and request.content_length > MAX_BODY_BYTES:
            return None
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else None

    def _int_arg(name, default, low, high):
        raw = (request.args.get(name) or '').strip()
        if not raw:
            return default
        try:
            return max(low, min(int(raw), high))
        except ValueError:
            return default

    def _flag(name):
        return str(request.args.get(name) or '').strip().lower() in ('1', 'true', 'yes')

    # ── Справка ──────────────────────────────────────────────────────────────
    @bp.route('/ping', methods=['GET'])
    def olx_partner_ping():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        return jsonify({'status': 'ok', 'key': key['name'],
                        'cabinets': [{'code': cab.code, 'title': cab.title} for cab in cabinets.CABINETS],
                        'events': list(partner_events.EVENTS),
                        'limits': {'per_minute': per_minute, 'threads_page_max': THREADS_PAGE_MAX,
                                   'journal_page_max': JOURNAL_PAGE_MAX}})

    @bp.route('/cabinets', methods=['GET'])
    def olx_partner_cabinets():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        try:
            items = svc.cabinets()
        except Exception:                                # noqa: BLE001
            log.exception('olx partner: кабинеты не прочитаны')
            _journal(key, outcome='error', status=500, summary='кабинеты', started=started)
            return _refuse(500, 'OLX_API_FAILED', 'Внутренняя ошибка — повторите запрос позже')
        _journal(key, outcome='ok', status=200, summary='кабинеты: %d' % len(items), started=started)
        return jsonify({'cabinets': items})

    # ── Чаты ─────────────────────────────────────────────────────────────────
    @bp.route('/threads', methods=['GET'])
    def olx_partner_threads():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        code = (request.args.get('cabinet') or '').strip() or None
        if code and cabinets.get(code) is None:
            return _refuse(404, 'OLX_API_UNKNOWN_CABINET',
                           'Неизвестный кабинет OLX — список в GET /api/olx/cabinets')
        since = None
        if request.args.get('since'):
            since = parse_moment(request.args.get('since'))
            if since is None:
                return _refuse(400, 'OLX_API_BAD_REQUEST', 'since — время в ISO 8601')
        limit = _int_arg('limit', 50, 1, THREADS_PAGE_MAX)
        offset = _int_arg('offset', 0, 0, 1000000)
        try:
            rows, total = svc.threads(cabinet_code=cabinets.get(code).code if code else None,
                                      awaiting_only=_flag('awaiting'), since=since,
                                      limit=limit, offset=offset)
        except Exception:                                # noqa: BLE001
            log.exception('olx partner: список чатов не прочитан')
            _journal(key, outcome='error', status=500, summary='чаты', started=started)
            return _refuse(500, 'OLX_API_FAILED', 'Внутренняя ошибка — повторите запрос позже')
        items = [_thread_item(row) for row in rows]
        _journal(key, outcome='ok', status=200, started=started,
                 summary='чаты%s: %d из %d' % (' кабинета %s' % code if code else '', len(items), total))
        return jsonify({'threads': items,
                        'pagination': {'limit': limit, 'offset': offset, 'total': total,
                                       'has_more': offset + len(items) < total}})

    @bp.route('/threads/<code>/<thread_id>', methods=['GET'])
    def olx_partner_thread(code, thread_id):
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        cabinet, refusal = _cabinet(code)
        if refusal is not None:
            return refusal
        started = time.monotonic()
        try:
            data = svc.conversation(cabinet, str(thread_id))
        except svc.refused_type() as exc:
            status, code_ = _REFUSALS.get(exc.code, (502, 'OLX_API_UPSTREAM'))
            _journal(key, outcome='error', status=status, started=started,
                     summary='переписка %s/%s: %s' % (cabinet.code, thread_id, exc.code))
            return _refuse(status, code_, str(exc), reason=exc.code)
        except Exception:                                # noqa: BLE001
            log.exception('olx partner: переписка не прочитана')
            _journal(key, outcome='error', status=500, started=started,
                     summary='переписка %s/%s' % (cabinet.code, thread_id))
            return _refuse(500, 'OLX_API_FAILED', 'Внутренняя ошибка — повторите запрос позже')
        messages, events = [], []
        for item in data.get('items') or []:
            (events if item.get('event') else messages).append(_timeline_item(item))
        _journal(key, outcome='ok', status=200, started=started,
                 summary='переписка %s/%s: %d сообщений' % (cabinet.code, thread_id, len(messages)))
        return jsonify({
            'cabinet': partner_events.cabinet_info(cabinet),
            'thread': partner_events.thread_info(thread_id, None, data.get('state') or {}),
            'messages': messages,
            'events': events,
            'url': 'https://www.olx.kz/mojolx/wiadomosci/#!thread=%s' % thread_id,
        })

    @bp.route('/threads/<code>/<thread_id>/messages', methods=['POST'])
    def olx_partner_reply(code, thread_id):
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        cabinet, refusal = _cabinet(code)
        if refusal is not None:
            return refusal
        started = time.monotonic()
        body = _body()
        text = str((body or {}).get('text') or '')
        if body is None or not text.strip():
            _journal(key, outcome='bad_request', status=400, started=started,
                     summary='ответ в %s/%s: пустой текст' % (cabinet.code, thread_id))
            return _refuse(400, 'OLX_API_BAD_REQUEST', 'Нужен текст сообщения: поле text')
        actor = {'user_id': None, 'name': 'Партнёр: %s' % key['name'], 'source': 'partner',
                 'key_id': key['id']}
        try:
            claim = svc.reply(cabinet, str(thread_id), text, actor)
        except svc.refused_type() as exc:
            status, code_ = _REFUSALS.get(exc.code, (502, 'OLX_API_UPSTREAM'))
            _journal(key, outcome='error' if status >= 500 else 'bad_request', status=status,
                     started=started, summary='ответ в %s/%s не ушёл: %s' % (cabinet.code, thread_id, exc.code),
                     details={'reason': exc.code, 'text_length': len(text.strip())})
            response = _refuse(status, code_, str(exc), reason=exc.code)
            if exc.retry_after:
                response.headers['Retry-After'] = str(int(exc.retry_after))
            return response
        except Exception:                                # noqa: BLE001
            log.exception('olx partner: ответ кандидату упал')
            _journal(key, outcome='error', status=500, started=started,
                     summary='ответ в %s/%s' % (cabinet.code, thread_id))
            return _refuse(500, 'OLX_API_FAILED', 'Внутренняя ошибка — повторите запрос позже')
        _journal(key, outcome='ok', status=200, started=started,
                 summary='ответ в %s/%s: %d знаков' % (cabinet.code, thread_id, len(text.strip())),
                 details={'text': text.strip()[:500], 'outbound_id': (claim or {}).get('id')})
        return jsonify({'status': 'sent', 'cabinet': cabinet.code, 'thread_id': str(thread_id),
                        'text': text.strip(), 'author': actor['name'],
                        'outbound_id': (claim or {}).get('id')})

    # ── Журнал откликов ──────────────────────────────────────────────────────
    @bp.route('/journal', methods=['GET'])
    def olx_partner_journal():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        code = (request.args.get('cabinet') or '').strip() or None
        if code and cabinets.get(code) is None:
            return _refuse(404, 'OLX_API_UNKNOWN_CABINET',
                           'Неизвестный кабинет OLX — список в GET /api/olx/cabinets')
        bounds = {}
        for name in ('from', 'to'):
            raw = request.args.get(name)
            if raw:
                moment = parse_moment(raw)
                if moment is None:
                    return _refuse(400, 'OLX_API_BAD_REQUEST', '%s — время в ISO 8601' % name)
                bounds[name] = moment
        limit = _int_arg('limit', 100, 1, JOURNAL_PAGE_MAX)
        offset = _int_arg('offset', 0, 0, 1000000)
        try:
            page = svc.journal(date_from=bounds.get('from'), date_to=bounds.get('to'),
                               cabinet_code=cabinets.get(code).code if code else None,
                               result=(request.args.get('result') or '').strip() or None,
                               limit=limit, offset=offset)
        except Exception:                                # noqa: BLE001
            log.exception('olx partner: журнал не прочитан')
            _journal(key, outcome='error', status=500, summary='журнал', started=started)
            return _refuse(500, 'OLX_API_FAILED', 'Внутренняя ошибка — повторите запрос позже')
        items = [_journal_item(row) for row in page.get('items') or []]
        total = int(page.get('total') or 0)
        _journal(key, outcome='ok', status=200, started=started,
                 summary='журнал: %d из %d' % (len(items), total))
        return jsonify({'items': items,
                        'pagination': {'limit': limit, 'offset': offset, 'total': total,
                                       'has_more': offset + len(items) < total}})

    # ── Подписка на события ──────────────────────────────────────────────────
    def _subscription_public(item, stats=None):
        if not item:
            return None
        public = {field: item.get(field) for field in (
            'url', 'events', 'enabled', 'created_at', 'updated_at', 'last_delivered_at',
            'last_error', 'last_error_at', 'failures_in_row')}
        public['events'] = item.get('events') or list(partner_events.EVENTS)
        if stats is not None:
            public['deliveries'] = stats
        return public

    def _signature_help():
        return {'header': partner_webhooks.SIGNATURE_HEADER,
                'timestamp_header': partner_webhooks.TIMESTAMP_HEADER,
                'scheme': 'sha256=hex(hmac_sha256(secret, timestamp + "." + body))',
                'event_header': partner_webhooks.EVENT_HEADER,
                'delivery_header': partner_webhooks.DELIVERY_HEADER}

    @bp.route('/webhook', methods=['GET'])
    def olx_partner_webhook_get():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        item, stats = svc.subscription(key['id'])
        return jsonify({'subscription': _subscription_public(item, stats),
                        'events': list(partner_events.EVENTS), 'signature': _signature_help()})

    @bp.route('/webhook', methods=['PUT'])
    def olx_partner_webhook_put():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        body = _body()
        if body is None:
            return _refuse(400, 'OLX_API_BAD_REQUEST', 'Тело запроса должно быть JSON-объектом')
        url, problem = partner_webhooks.validate_url(body.get('url'))
        if problem:
            return _refuse(400, 'OLX_API_BAD_REQUEST', problem)
        secret, problem = partner_webhooks.normalize_secret(body.get('secret'))
        if problem:
            return _refuse(400, 'OLX_API_BAD_REQUEST', problem)
        events, problem = partner_webhooks.normalize_events(body.get('events'), partner_events.EVENTS)
        if problem:
            return _refuse(400, 'OLX_API_BAD_REQUEST', problem)
        generated = secret is None
        if generated:
            secret = partner_webhooks.generate_secret()
        try:
            item = svc.save_subscription(key['id'], url=url, secret=secret, events=events)
        except Exception:                                # noqa: BLE001
            log.exception('olx partner: подписка не сохранена')
            _journal(key, outcome='error', status=500, summary='подписка', started=started)
            return _refuse(500, 'OLX_API_FAILED', 'Внутренняя ошибка — повторите запрос позже')
        _journal(key, outcome='ok', status=200, started=started,
                 summary='подписка на события: %s' % url,
                 details={'url': url, 'events': events or list(partner_events.EVENTS)})
        payload = {'status': 'saved', 'subscription': _subscription_public(item),
                   'signature': _signature_help()}
        if generated:
            # Секрет выпустили мы — показывается один раз, дальше его нигде не прочитать.
            payload['secret'] = secret
        return jsonify(payload)

    @bp.route('/webhook', methods=['DELETE'])
    def olx_partner_webhook_delete():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        deleted = svc.delete_subscription(key['id'])
        _journal(key, outcome='ok' if deleted else 'bad_request', status=200 if deleted else 404,
                 summary='подписка снята' if deleted else 'подписки не было', started=started)
        if not deleted:
            return _refuse(404, 'OLX_API_NO_SUBSCRIPTION', 'Подписки нет')
        return jsonify({'status': 'deleted'})

    @bp.route('/webhook/deliveries', methods=['GET'])
    def olx_partner_webhook_deliveries():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        items = svc.deliveries(key['id'], _int_arg('limit', 50, 1, 200))
        if items is None:
            return _refuse(404, 'OLX_API_NO_SUBSCRIPTION', 'Подписки нет')
        return jsonify({'deliveries': items})

    @bp.route('/webhook/test', methods=['POST'])
    def olx_partner_webhook_test():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        item, _stats = svc.subscription(key['id'])
        if not item:
            return _refuse(404, 'OLX_API_NO_SUBSCRIPTION', 'Подписки нет — сначала PUT /api/olx/webhook')
        queued = svc.ping(key['id'])
        _journal(key, outcome='ok' if queued else 'error', status=200 if queued else 409,
                 summary='проверка приёмника: %s' % ('поставлена в очередь' if queued else 'не поставлена'),
                 started=started)
        if not queued:
            return _refuse(409, 'OLX_API_SUBSCRIPTION_DISABLED',
                           'Подписка выключена или ключу закрыта область — событие не отправлено')
        return jsonify({'status': 'queued', 'event': partner_events.EVENT_PING})

    return bp


def _thread_item(row):
    cabinet = cabinets.BY_CODE.get(row.get('cabinet_code'))
    return {
        'cabinet': row.get('cabinet_code'),
        'cabinet_title': cabinet.title if cabinet else row.get('cabinet_code'),
        'thread_id': row.get('thread_id'),
        'advert_id': row.get('advert_id'),
        'advert_title': row.get('advert_title'),
        'interlocutor_id': row.get('interlocutor_id'),
        'interlocutor_name': row.get('interlocutor_name'),
        'last_message_id': row.get('last_message_id'),
        'last_message_at': partner_events.iso(row.get('last_message_at')),
        'messages_seen': row.get('messages_seen'),
        'awaiting_human_since': partner_events.iso(row.get('awaiting_human_since')),
        'robot_replied_at': partner_events.iso(row.get('canned_reply_sent_at')),
        'phone': row.get('phone_normalized'),
        'amo_lead_id': row.get('amo_lead_id'),
        'first_seen_at': partner_events.iso(row.get('first_seen_at')),
        'updated_at': partner_events.iso(row.get('updated_at')),
        'url': 'https://www.olx.kz/mojolx/wiadomosci/#!thread=%s' % row.get('thread_id'),
    }


def _timeline_item(item):
    if item.get('event'):
        return {'id': item['id'], 'event': item['event'], 'text': item.get('text'),
                'at': partner_events.iso(item.get('at')), 'amo_lead_id': item.get('amo_lead_id'),
                'phone': item.get('phone'), 'error': item.get('error'),
                'latency_ms': item.get('latency_ms')}
    return {'id': item['id'], 'direction': 'out' if item.get('outgoing') else 'in',
            'text': item.get('text') or '', 'at': partner_events.iso(item.get('at')),
            'author': item.get('author'), 'attachments': item.get('attachments') or [],
            'cvs': item.get('cvs') or [], 'failed': bool(item.get('failed')),
            'error': item.get('error')}


def _journal_item(row):
    return {
        'id': row.get('id'), 'cabinet': row.get('cabinet_code'), 'thread_id': row.get('thread_id'),
        'message_id': row.get('message_id'), 'message_at': partner_events.iso(row.get('message_at')),
        'result': row.get('result'), 'phone': row.get('phone_normalized'),
        'phone_raw': row.get('phone_raw'), 'amo_lead_id': row.get('amo_lead_id'),
        'amo_contact_id': row.get('amo_contact_id'), 'lead_created_at': partner_events.iso(row.get('lead_created_at')),
        'latency_ms': row.get('latency_ms'), 'error': row.get('error_text'),
        'excerpt': row.get('message_excerpt'), 'created_at': partner_events.iso(row.get('created_at')),
    }
