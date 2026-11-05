# -*- coding: utf-8 -*-
"""«Звонки Binotel» для партнёров: вход по ключу партнёра с областью «Звонки Binotel».

    GET    /api/calls/ping                       ключ принят? события, пределы
    GET    /api/calls?from=&to=&phone=&…         история звонков области, от новых к старым
    GET    /api/calls/operators                  кто из области на какой линии, режим телефона
    GET    /api/calls/<id>                       один звонок (id = generalCallID Binotel)
    GET    /api/calls/<id>/recording             ссылка на запись разговора (живёт около часа)
    GET    /api/calls/webhook                    подписка на события
    PUT    /api/calls/webhook                    {"url", "secret"?, "events"?}
    DELETE /api/calls/webhook                    снять подписку
    GET    /api/calls/webhook/deliveries         последние доставки
    POST   /api/calls/webhook/test               прислать событие ping

Звонки области и события — calls_api/service.py; вход, предел вызовов в минуту
(CALLS_API_PER_MINUTE, 120) и строка журнала на вызов — как у остальных входов партнёров
(partner_keys.auth / journal). Ссылка на запись ходит в Binotel живьём и делит темп запросов
кабинета с разделом «Удаленный КЦ».
"""

import logging
import os
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

from partner_keys import auth as partner_auth
from partner_keys import journal as partner_journal
from partner_keys import scopes as partner_scopes
from partner_keys import webhooks as partner_webhooks
from partner_keys.limits import RateLimiter

from . import rules
from .service import EVENT_PING, EVENTS, CallsApiError

log = logging.getLogger(__name__)

SCOPE = partner_scopes.BINOTEL_CALLS
DEFAULT_PER_MINUTE = 120
MAX_PER_MINUTE = 600
MAX_BODY_BYTES = 16 * 1024
PAGE_MAX = 500
_DIRECTIONS = {'outgoing': 'out', 'incoming': 'in'}


def _setting(environ, name, default, ceiling):
    raw = str((environ or os.environ).get(name) or '').strip()
    if not raw.isdigit():
        return default
    return max(1, min(int(raw), ceiling))


def parse_moment(value):
    """ISO 8601 → момент с поясом (без пояса — Алматы). None — не разобрать."""
    text = str(value or '').strip().replace('Z', '+00:00')
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=rules.ALMATY)
    return moment


def build_calls_api_blueprint(*, db, service, client_ip=None, environ=None, per_minute=None):
    """Фабрика: зависимости аргументами, bot_schedule2 не импортируется. service —
    calls_api.service.CallsApiService или двойник с теми же методами."""
    bp = Blueprint('calls_api', __name__, url_prefix='/api/calls')
    environ = environ if environ is not None else os.environ
    per_minute = per_minute or _setting(environ, 'CALLS_API_PER_MINUTE', DEFAULT_PER_MINUTE, MAX_PER_MINUTE)
    limiter = RateLimiter(per_minute)

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

    def _journal(key, *, outcome, status, summary, details=None, started=None):
        partner_journal.record(
            db, key, scope=SCOPE, endpoint=request.path, outcome=outcome, http_status=status,
            latency_ms=int((time.monotonic() - started) * 1000) if started else None,
            summary=summary, details=details, ip=_ip(), user_agent=request.headers.get('User-Agent'))

    def _authenticate():
        status, key = partner_auth.lookup(
            db, partner_auth.presented_from(request.headers), scope=SCOPE)
        if status == partner_auth.NOT_READY:
            logging.error('calls_api: таблиц ключей нет — вход закрыт; схема не встала')
            return None, _refuse(503, 'CALLS_API_NOT_CONFIGURED', 'Доступ по ключу на сервере не настроен')
        if status == partner_auth.NO_SCOPE:
            logging.warning('calls_api: ключу «%s» (%s) не открыты звонки, ip %s',
                            key['name'], key['mark'], _ip() or '-')
            return None, _refuse(403, 'CALLS_API_FORBIDDEN', 'Ключу не открыты звонки Binotel')
        if status != partner_auth.OK:
            logging.warning('calls_api: ключ не принят (%s, ip %s)', status, _ip() or '-')
            return None, _refuse(401, 'CALLS_API_UNAUTHORIZED', 'Ключ не принят')
        allowed, retry_after = limiter.allow(key['id'])
        if not allowed:
            _journal(key, outcome='limit', status=429, summary='предел вызовов в минуту')
            response = _refuse(429, 'CALLS_API_LIMIT', 'Слишком много вызовов за минуту — подождите',
                               retry_after=retry_after)
            response.headers['Retry-After'] = str(retry_after)
            return None, response
        if not service.ready():
            return None, _refuse(503, 'CALLS_API_NOT_CONFIGURED', 'Звонки на сервере ещё не готовы')
        return key, None

    def _int_arg(name, default, low, high):
        raw = (request.args.get(name) or '').strip()
        if not raw:
            return default
        try:
            return max(low, min(int(raw), high))
        except ValueError:
            return default

    def _failure(key, started, summary):
        log.exception('calls_api: %s', summary)
        _journal(key, outcome='error', status=500, summary=summary, started=started)
        return _refuse(500, 'CALLS_API_FAILED', 'Внутренняя ошибка — повторите запрос позже')

    # ── Справка ──────────────────────────────────────────────────────────────
    @bp.route('/ping', methods=['GET'])
    def calls_api_ping():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        return jsonify({'status': 'ok', 'key': key['name'], 'events': list(EVENTS),
                        'results': list(rules.RESULTS),
                        'limits': {'per_minute': per_minute, 'page_max': PAGE_MAX}})

    @bp.route('/operators', methods=['GET'])
    def calls_api_operators():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        try:
            items = service.operators()
        except Exception:                                # noqa: BLE001
            return _failure(key, started, 'операторы')
        _journal(key, outcome='ok', status=200, summary='операторы: %d' % len(items), started=started)
        return jsonify({'operators': items})

    # ── История ──────────────────────────────────────────────────────────────
    @bp.route('', methods=['GET'])
    def calls_api_list():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        filters = {}
        for name, target in (('from', 'date_from'), ('to', 'date_to'), ('updated_since', 'updated_since')):
            raw = request.args.get(name)
            if raw:
                moment = parse_moment(raw)
                if moment is None:
                    return _refuse(400, 'CALLS_API_BAD_REQUEST', '%s — время в ISO 8601' % name)
                filters[target] = moment
        raw_phone = (request.args.get('phone') or '').strip()
        if raw_phone:
            phone = rules.normalize_phone(raw_phone)
            if not phone:
                return _refuse(400, 'CALLS_API_BAD_REQUEST', 'phone — казахстанский номер, например +77011234567')
            filters['phone'] = phone
        raw_operator = (request.args.get('operator') or '').strip()
        if raw_operator:
            if raw_operator.isdigit():
                filters['user_id'] = int(raw_operator)
            elif '@' in raw_operator:
                filters['email'] = raw_operator
            else:
                return _refuse(400, 'CALLS_API_BAD_REQUEST', 'operator — id сотрудника или его почта')
        line = (request.args.get('line') or '').strip()
        if line:
            filters['line'] = line[:64]
        result = (request.args.get('result') or '').strip().lower()
        if result:
            if result not in rules.RESULTS:
                return _refuse(400, 'CALLS_API_BAD_REQUEST', 'result — одно из: %s' % ', '.join(rules.RESULTS))
            filters['result'] = result
        direction = (request.args.get('direction') or '').strip().lower()
        if direction:
            if direction not in _DIRECTIONS:
                return _refuse(400, 'CALLS_API_BAD_REQUEST', 'direction — outgoing или incoming')
            filters['direction'] = _DIRECTIONS[direction]
        limit = _int_arg('limit', 100, 1, PAGE_MAX)
        offset = _int_arg('offset', 0, 0, 1000000)
        try:
            items, total = service.list_calls(limit=limit, offset=offset, **filters)
        except Exception:                                # noqa: BLE001
            return _failure(key, started, 'история звонков')
        _journal(key, outcome='ok', status=200, started=started,
                 summary='звонки: %d из %d' % (len(items), total))
        return jsonify({'calls': items,
                        'pagination': {'limit': limit, 'offset': offset, 'total': total,
                                       'has_more': offset + len(items) < total}})

    @bp.route('/<general_call_id>', methods=['GET'])
    def calls_api_get(general_call_id):
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        if not str(general_call_id).isdigit():
            return _refuse(404, 'CALLS_API_NOT_FOUND', 'Звонок не найден')
        try:
            item = service.get_call(general_call_id)
        except Exception:                                # noqa: BLE001
            return _failure(key, started, 'звонок %s' % general_call_id)
        if item is None:
            _journal(key, outcome='no_data', status=404, summary='звонок %s: нет' % general_call_id,
                     started=started)
            return _refuse(404, 'CALLS_API_NOT_FOUND', 'Звонок не найден')
        _journal(key, outcome='ok', status=200, summary='звонок %s' % general_call_id, started=started)
        return jsonify({'call': item})

    @bp.route('/<general_call_id>/recording', methods=['GET'])
    def calls_api_recording(general_call_id):
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        if not str(general_call_id).isdigit():
            return _refuse(404, 'CALLS_API_NOT_FOUND', 'Звонок не найден')
        try:
            data = service.recording_url(general_call_id)
        except CallsApiError as exc:
            _journal(key, outcome='no_data' if exc.status in (404, 409) else 'error', status=exc.status,
                     summary='запись %s: %s' % (general_call_id, exc.code), started=started)
            response = _refuse(exc.status, exc.code, str(exc))
            if exc.status in (409, 503):
                response.headers['Retry-After'] = '60' if exc.status == 409 else '5'
            return response
        except Exception:                                # noqa: BLE001
            return _failure(key, started, 'запись %s' % general_call_id)
        _journal(key, outcome='ok', status=200, summary='запись %s' % general_call_id, started=started)
        return jsonify(data)

    # ── Подписка на события ──────────────────────────────────────────────────
    def _subscription_public(item, stats=None):
        if not item:
            return None
        public = {field: item.get(field) for field in (
            'url', 'events', 'enabled', 'created_at', 'updated_at', 'last_delivered_at',
            'last_error', 'last_error_at', 'failures_in_row')}
        public['events'] = item.get('events') or list(EVENTS)
        if stats is not None:
            public['deliveries'] = stats
        return public

    def _signature_help():
        return {'header': partner_webhooks.SIGNATURE_HEADER,
                'timestamp_header': partner_webhooks.TIMESTAMP_HEADER,
                'scheme': 'sha256=hex(hmac_sha256(secret, timestamp + "." + body))',
                'event_header': partner_webhooks.EVENT_HEADER,
                'delivery_header': partner_webhooks.DELIVERY_HEADER}

    def _body():
        if request.content_length and request.content_length > MAX_BODY_BYTES:
            return None
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else None

    def _subscription(key_id):
        with db._get_cursor() as cursor:
            item = partner_webhooks.get_subscription(cursor, key_id, SCOPE)
            stats = partner_webhooks.delivery_stats(cursor, item['id']) if item else None
        return item, stats

    @bp.route('/webhook', methods=['GET'])
    def calls_api_webhook_get():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        item, stats = _subscription(key['id'])
        return jsonify({'subscription': _subscription_public(item, stats),
                        'events': list(EVENTS), 'signature': _signature_help()})

    @bp.route('/webhook', methods=['PUT'])
    def calls_api_webhook_put():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        body = _body()
        if body is None:
            return _refuse(400, 'CALLS_API_BAD_REQUEST', 'Тело запроса должно быть JSON-объектом')
        url, problem = partner_webhooks.validate_url(body.get('url'))
        if problem:
            return _refuse(400, 'CALLS_API_BAD_REQUEST', problem)
        secret, problem = partner_webhooks.normalize_secret(body.get('secret'))
        if problem:
            return _refuse(400, 'CALLS_API_BAD_REQUEST', problem)
        events, problem = partner_webhooks.normalize_events(body.get('events'), EVENTS)
        if problem:
            return _refuse(400, 'CALLS_API_BAD_REQUEST', problem)
        generated = secret is None
        if generated:
            secret = partner_webhooks.generate_secret()
        try:
            with db._get_cursor() as cursor:
                item = partner_webhooks.upsert_subscription(cursor, key['id'], SCOPE, url=url,
                                                            secret=secret, events=events)
        except Exception:                                # noqa: BLE001
            return _failure(key, started, 'подписка')
        _journal(key, outcome='ok', status=200, started=started,
                 summary='подписка на события: %s' % url,
                 details={'url': url, 'events': events or list(EVENTS)})
        payload = {'status': 'saved', 'subscription': _subscription_public(item),
                   'signature': _signature_help()}
        if generated:
            # Секрет выпустили мы — показывается один раз, дальше его нигде не прочитать.
            payload['secret'] = secret
        return jsonify(payload)

    @bp.route('/webhook', methods=['DELETE'])
    def calls_api_webhook_delete():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        with db._get_cursor() as cursor:
            deleted = partner_webhooks.delete_subscription(cursor, key['id'], SCOPE)
        _journal(key, outcome='ok' if deleted else 'bad_request', status=200 if deleted else 404,
                 summary='подписка снята' if deleted else 'подписки не было', started=started)
        if not deleted:
            return _refuse(404, 'CALLS_API_NO_SUBSCRIPTION', 'Подписки нет')
        return jsonify({'status': 'deleted'})

    @bp.route('/webhook/deliveries', methods=['GET'])
    def calls_api_webhook_deliveries():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        with db._get_cursor() as cursor:
            item = partner_webhooks.get_subscription(cursor, key['id'], SCOPE)
            items = partner_webhooks.recent_deliveries(cursor, item['id'], _int_arg('limit', 50, 1, 200)) \
                if item else None
        if items is None:
            return _refuse(404, 'CALLS_API_NO_SUBSCRIPTION', 'Подписки нет')
        return jsonify({'deliveries': items})

    @bp.route('/webhook/test', methods=['POST'])
    def calls_api_webhook_test():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        started = time.monotonic()
        item, _stats = _subscription(key['id'])
        if not item:
            return _refuse(404, 'CALLS_API_NO_SUBSCRIPTION', 'Подписки нет — сначала PUT /api/calls/webhook')
        queued = service.ping(key['id'])
        _journal(key, outcome='ok' if queued else 'error', status=200 if queued else 409,
                 summary='проверка приёмника: %s' % ('поставлена в очередь' if queued else 'не поставлена'),
                 started=started)
        if not queued:
            return _refuse(409, 'CALLS_API_SUBSCRIPTION_DISABLED',
                           'Подписка выключена или ключу закрыта область — событие не отправлено')
        return jsonify({'status': 'queued', 'event': EVENT_PING})

    return bp
