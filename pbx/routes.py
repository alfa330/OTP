# -*- coding: utf-8 -*-
"""Ручки раздела «АТС».

Коннектор (без логина портала, подпись Ed25519 — cdr/agent_auth.py, ключи CDR_AGENT_KEYS):
    POST /api/pbx/agent/sync     {agent_id, hostname, version, ami: {connected, host, error,
                                  events_total}, snapshot, stream, events: [звонки],
                                  results: [итоги], lines?: {префикс: {number, name}}}
                                 → {status, stored, commands}
                                 Коннектор шлёт пачку СРАЗУ по изменению на станции (и пульс раз
                                 в 15 с). Команды в ответе — только если у него нет канала команд
                                 (`stream: false`): это запасной путь, а не основной.
    POST /api/pbx/agent/stream   канал команд: SSE, держится открытым; команда уходит коннектору
                                 в момент постановки (тычок NOTIFY), без опроса.

Люди (cookie/JWT портала, периметр «Касаний»):
    GET  /api/pbx/stream               SSE: живые звонки и коннектор, изменения — сразу
    GET  /api/pbx/live                 то же одним ответом (запасной путь, если поток недоступен)
    POST /api/pbx/call                 {phone, prefix?, line_number?, linkedid?} → {command_id}
    POST /api/pbx/hangup               {linkedid} → {command_id}
    POST /api/pbx/spy                  {linkedid, mode: listen|whisper|barge} → {command_id}
    GET  /api/pbx/commands/<id>        {status, message, linkedid}

Что закреплено в ручках, а не в SQL: тело от коннектора режется и нормализуется до записи
(queries.clean_call); команда ставится в очередь только после проверки прав и формы набора
(dial_string) — коннектор исполняет её как есть, вторая проверка у него только на форму.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, Response, g, jsonify, request

from cdr import (agent_auth, config as cdr_config, directory as directory_mod, lines as lines_mod,
                 pbx_calls, queries as cdr_queries, touches as touches_mod)

from . import access, queries, realtime, schema

log = logging.getLogger(__name__)

# Пачка изменений коннектора — до 500 звонков, полный снимок — до 2000 (идущие и законченные за
# минуту; cdr_bridge/pbx_connector.py MAX_SNAPSHOT_CALLS).
MAX_EVENTS = 2000
MAX_RESULTS = 200
MAX_LINES = 200
CONNECTOR_SILENT_SECONDS = realtime.CONNECTOR_SILENT_SECONDS
# Законченные звонки держатся на экране столько секунд: человек видит «только что
# закончился» и чем кончилось, потом строка уходит сама.
ENDED_VISIBLE_SECONDS = 90
LINE_PARKS_WINDOW_DAYS = 60
SPY_MODES = ('listen', 'whisper', 'barge')

# Канал команд коннектора: пульс (он же «коннектор жив» и страховка от потерянного тычка) и
# предел жизни соединения — после него коннектор переподключается сам, а нить waitress не
# висит сутками на соединении, которое могло умереть без единого байта.
AGENT_HEARTBEAT_SECONDS = 15
AGENT_STREAM_MAX_SECONDS = 600
# Поток браузера: пульс SSE-комментарием и тот же предел жизни (переподключение заодно
# освежает справочник имён и линий потока).
STREAM_HEARTBEAT_SECONDS = 20
STREAM_MAX_SECONDS = 600

_ALMATY = timezone(timedelta(hours=5))
_schema_ready = {'ok': False}


def _stream_response(generate, release):
    response = Response(generate(), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'
    # Место возвращается на закрытии ответа — надёжнее finally в генераторе: у ни разу не
    # прочитанного генератора finally не выполнится, и место утекло бы.
    response.call_on_close(release)
    return response


def build_pbx_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                        listen_connect=None, stream_limit=10, agent_stream_limit=2, hub=None):
    """listen_connect() → новое соединение Postgres для LISTEN (вне пула). Без него живых
    потоков нет: ручки потоков отвечают 503, коннектор и браузер остаются на запасном опросе."""
    bp = Blueprint('pbx', __name__, url_prefix='/api/pbx')
    live_hub = hub or realtime.hub

    def _ready(cursor):
        if not _schema_ready['ok']:
            _schema_ready['ok'] = schema.schema_is_ready(cursor)
        return _schema_ready['ok']

    def _start_listener():
        if listen_connect is None:
            return False
        realtime.ensure_listener(listen_connect, live_hub)
        return True

    # ── каркас роута моста ───────────────────────────────────────────────────

    def _authenticate_agent():
        raw_keys = cdr_config.agent_keys_raw()
        if not raw_keys:
            return None, (jsonify({"error": "Коннектор не настроен на сервере: нет открытых ключей"}), 503)
        try:
            keys = agent_auth.parse_public_keys(raw_keys)
        except ValueError as exc:
            log.error('АТС: CDR_AGENT_KEYS задан с ошибкой: %s', exc)
            return None, (jsonify({"error": "Коннектор не настроен на сервере: ключи заданы с ошибкой"}), 503)
        kid, reason = agent_auth.verify(request.headers, request.method, request.path,
                                        request.get_data(cache=True), keys)
        if kid is None:
            log.warning('АТС: коннектор не авторизован — %s', reason)
            return None, (jsonify({"error": "Коннектор не авторизован"}), 401)
        return kid, None

    def agent_route(rule):
        def decorator(handler):
            @bp.route(rule, methods=['POST', 'OPTIONS'], endpoint='agent_' + handler.__name__)
            @wraps(handler)
            def wrapper():
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                key_id, refusal = _authenticate_agent()
                if refusal is not None:
                    return refusal
                g.pbx_agent_key = key_id
                try:
                    payload = request.get_json(silent=True) or {}
                    if not _schema_ready['ok']:
                        # Соединение из пула — только пока схема не подтверждена: коннектор
                        # приходит раз в несколько секунд, и каждый раз занимать его незачем.
                        with db._get_cursor() as cursor:
                            if not _ready(cursor):
                                return jsonify({"error": "Раздел «АТС» не развернулся: нет таблиц",
                                                "code": "PBX_SCHEMA_MISSING"}), 503
                    return handler(payload)
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
                except Exception as exc:  # noqa: BLE001
                    log.exception('АТС: ошибка в ручке коннектора %s', rule)
                    return jsonify({"error": "Внутренняя ошибка", "detail": str(exc)[:200]}), 500
            return wrapper
        return decorator

    # ── каркас пользовательского роута ───────────────────────────────────────

    def pbx_route(rule, methods=('GET',)):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _row, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    with db._get_cursor() as cursor:
                        if not _ready(cursor):
                            return jsonify({"error": "Раздел «АТС» не развернулся: нет таблиц.",
                                            "code": "PBX_SCHEMA_MISSING"}), 503
                        ctx = queries.load_access_context(cursor, requester_id)
                        my_ext = queries.user_ext(cursor, requester_id) if ctx else ''
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    if not access.can_open_section(ctx):
                        return jsonify({"error": "Раздел вам не открыт", "code": "PBX_SECTION_CLOSED"}), 403
                    return handler(*args, ctx=ctx, my_ext=my_ext, **kwargs)
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
                except Exception as exc:  # noqa: BLE001
                    log.exception('АТС: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «АТС»",
                                    "detail": str(exc)[:200]}), 500
            return wrapper
        return decorator

    # ── коннектор ────────────────────────────────────────────────────────────

    @agent_route('/agent/sync')
    def sync(payload):
        events = payload.get('events')
        if events is None:
            events = []
        if not isinstance(events, list) or len(events) > MAX_EVENTS:
            raise ValueError('Поле events — список не длиннее %d' % MAX_EVENTS)
        results = payload.get('results') or []
        if not isinstance(results, list) or len(results) > MAX_RESULTS:
            raise ValueError('Поле results — список не длиннее %d' % MAX_RESULTS)
        lines = payload.get('lines')
        if lines is not None and (not isinstance(lines, dict) or len(lines) > MAX_LINES):
            raise ValueError('Поле lines — словарь не больше %d линий' % MAX_LINES)
        ami = payload.get('ami') if isinstance(payload.get('ami'), dict) else {}
        snapshot = bool(payload.get('snapshot'))
        # У коннектора есть живой канал команд — в ответе на пачку команды ему не нужны:
        # раздай их здесь, и команда ушла бы дважды разными путями (сюда и в канал).
        has_stream = bool(payload.get('stream'))
        calls = [call for call in (queries.clean_call(item) for item in events) if call]
        version = str(payload.get('version') or '')[:40] or None
        with db._get_cursor() as cursor:
            queries.connector_seen(
                cursor, hostname=str(payload.get('hostname') or '')[:120] or None,
                version=version, agent_key=g.get('pbx_agent_key'), ami=ami, snapshot=snapshot)
            changed = queries.upsert_calls(cursor, calls)
            closed = queries.close_missing(cursor, [c['linkedid'] for c in calls]) if snapshot else 0
            for call in calls:
                # Звонок, набранный отсюда: команде — её linkedid, чтобы кнопка знала, что дальше.
                if call['origin'] == 'portal' and call['command_id']:
                    queries.finish_command(cursor, call['command_id'], 'accepted', '', call['linkedid'])
            for result in results:
                if not isinstance(result, dict):
                    continue
                try:
                    command_id = int(result.get('command_id'))
                except (TypeError, ValueError):
                    continue
                queries.finish_command(cursor, command_id, str(result.get('status') or ''),
                                       str(result.get('message') or '')[:500],
                                       str(result.get('linkedid') or '')[:64] or None)
            if lines is not None:
                queries.save_lines(cursor, _clean_lines(lines))
            commands = [] if has_stream else queries.claim_commands(cursor)
            # Тычки уходят при коммите — вместе с тем, о чём сообщают.
            realtime.notify(cursor, 'conn', seen=time.time(), ami=bool(ami.get('connected')),
                            err=(str(ami.get('error'))[:200] if ami.get('error') else None), ver=version)
            if changed or closed:
                realtime.notify(cursor, 'calls')
            if not calls and not commands and queries.cleanup_due(cursor):
                removed = queries.drop_expired(cursor)
                if removed:
                    log.info('АТС: убрано %d старых строк', removed)
        if closed:
            log.info('АТС: по снимку закрыто %d звонков без события отбоя', closed)
        return jsonify({'status': 'ok', 'stored': len(changed), 'commands': commands})

    @agent_route('/agent/stream')
    def agent_stream(payload):
        """Канал команд коннектора. Команды уходят в момент постановки; пульс раз в
        AGENT_HEARTBEAT_SECONDS отмечает коннектор живым и подбирает команду, чей тычок мог
        потеряться (слушатель переподключался)."""
        if not _start_listener():
            return jsonify({"error": "Живой канал выключен на сервере", "code": "PBX_STREAM_OFF"}), 503
        if not live_hub.acquire('agent', agent_stream_limit):
            response = jsonify({"error": "Канал команд уже занят", "code": "PBX_STREAM_BUSY"})
            response.headers['Retry-After'] = '15'
            return response, 503
        generation = live_hub.new_agent_stream()
        agent_key = g.get('pbx_agent_key')
        hostname = str(payload.get('hostname') or '')[:120] or None
        version = str(payload.get('version') or '')[:40] or None

        def beat():
            with db._get_cursor() as cursor:
                queries.connector_seen(cursor, hostname=hostname, version=version,
                                       agent_key=agent_key, stream=True)
                realtime.notify(cursor, 'conn', seen=time.time(), stream=time.time())

        def claim():
            with db._get_cursor() as cursor:
                return queries.claim_commands(cursor)

        def generate():
            cmd_cursor = live_hub.current_cmd_seq()
            deadline = time.monotonic() + AGENT_STREAM_MAX_SECONDS
            next_beat = 0.0
            claim_now = True
            yield ': connected %d\n\n' % int(time.time())
            while True:
                now = time.monotonic()
                if now >= next_beat:
                    beat()
                    next_beat = now + AGENT_HEARTBEAT_SECONDS
                    yield ': ping %d\n\n' % int(time.time())
                    claim_now = True
                if claim_now:
                    claim_now = False
                    # Сначала байт в канал, потом выдача: соединение, которое коннектор уже
                    # закрыл, waitress обнаружит на этой записи — и команда останется в очереди
                    # для живого канала, а не уйдёт «отправленной» в никуда.
                    yield ': ping %d\n\n' % int(time.time())
                    for command in claim():
                        yield realtime.sse('command', command)
                if now >= deadline:
                    yield realtime.sse('bye', {'reason': 'lifetime'})
                    return
                timeout = max(0.0, min(next_beat, deadline) - time.monotonic())
                woke, cmd_cursor, alive = live_hub.wait_commands(cmd_cursor, generation, timeout)
                if not alive:
                    yield realtime.sse('bye', {'reason': 'superseded'})
                    return
                if woke:
                    claim_now = True

        return _stream_response(generate, lambda: live_hub.release('agent'))

    # ── люди ─────────────────────────────────────────────────────────────────

    def _resolver(cursor):
        return directory_mod.resolver(cdr_queries.load_directory(cursor))

    def _known_lines(cursor):
        since = cdr_queries.today_almaty() - timedelta(days=LINE_PARKS_WINDOW_DAYS)
        return lines_mod.line_queues(cdr_queries.line_queue_rows(cursor, since))

    def _line_options(state_lines, known):
        """Линии для выбора: префикс, номер, подпись парка (по очереди, куда звонят на номер)
        или имя маршрута станции."""
        out = []
        for prefix, line in sorted((state_lines or {}).items()):
            number = str((line or {}).get('number') or '')
            park = lines_mod.park_of_queue(known.get(number)) if number in known else ''
            out.append({'prefix': prefix, 'number': number,
                        'label': park or str((line or {}).get('name') or '') or number})
        return out

    @pbx_route('/live')
    def live(ctx, my_ext):
        with db._get_cursor() as cursor:
            state = queries.connector_state(cursor)
            calls = queries.live_calls(cursor, ENDED_VISIBLE_SECONDS)
            resolve = _resolver(cursor)
            known = _known_lines(cursor)
        return jsonify({
            'connector': _connector_view(state),
            'calls': [_call_view(call, resolve, known, my_ext) for call in calls],
            'me': access.capabilities(ctx, my_ext),
            'lines': _line_options(state.get('lines'), known),
            'now': _local_now(),
        })

    @pbx_route('/stream')
    def stream(ctx, my_ext):
        """Живой поток раздела: первым кадром `state` (то же, что /live), дальше `calls` —
        изменившиеся звонки — и `connector`, когда коннектор теряет или находит станцию."""
        if not _start_listener():
            return jsonify({"error": "Живой канал выключен на сервере", "code": "PBX_STREAM_OFF"}), 503
        if not live_hub.acquire('browser', stream_limit):
            response = jsonify({"status": "busy"})
            response.headers['Retry-After'] = '120'
            return response, 503
        try:
            realtime.wait_ready(live_hub, timeout=5.0)
            with db._get_cursor() as cursor:
                state = queries.connector_state(cursor)
                resolve = _resolver(cursor)
                known = _known_lines(cursor)
        except Exception:
            live_hub.release('browser')
            raise
        me = access.capabilities(ctx, my_ext)
        lines = _line_options(state.get('lines'), known)

        def views(calls):
            return [_call_view(call, resolve, known, my_ext) for call in calls]

        def visible(calls):
            edge = (datetime.now(_ALMATY).replace(tzinfo=None)
                    - timedelta(seconds=ENDED_VISIBLE_SECONDS)).strftime('%Y-%m-%dT%H:%M:%S')
            return [c for c in calls if c.get('state') != 'ended' or str(c.get('ended_at') or '') >= edge]

        def generate():
            cursor_seq, calls, _ = live_hub.snapshot()
            connector = live_hub.connector_view()
            yield realtime.sse('state', {'connector': connector, 'calls': views(visible(calls)),
                                         'me': me, 'lines': lines, 'now': _local_now()})
            last_write = time.monotonic()
            deadline = time.monotonic() + STREAM_MAX_SECONDS
            while time.monotonic() < deadline:
                # Пульс — по времени с последней записи, а не «если событий не было»: тычки о
                # статусах телефонов приходят со всей компании каждые несколько секунд, и без
                # этого поток мог бы минутами молчать — браузер счёл бы соединение мёртвым.
                if time.monotonic() - last_write >= STREAM_HEARTBEAT_SECONDS:
                    yield ': ping %d\n\n' % int(time.time())
                    last_write = time.monotonic()
                timeout = max(0.05, STREAM_HEARTBEAT_SECONDS - (time.monotonic() - last_write))
                events, cursor_seq = live_hub.wait(cursor_seq, timeout)
                fresh, reset, unavailable = {}, False, False
                for event in events:
                    kind = event.get('kind')
                    if kind == 'calls':
                        for call in event.get('calls') or []:
                            fresh[call['linkedid']] = call
                    elif kind == 'reset':
                        reset = True
                    elif kind == 'unavailable':
                        unavailable = True
                if reset:
                    _seq, calls, _ = live_hub.snapshot()
                    yield realtime.sse('state', {'connector': live_hub.connector_view(),
                                                 'calls': views(visible(calls)), 'me': me,
                                                 'lines': lines, 'now': _local_now()})
                    last_write = time.monotonic()
                    connector = live_hub.connector_view()
                    continue
                if unavailable:
                    # Слушатель потерял базу: показывать «тишину» вместо данных нельзя.
                    yield realtime.sse('unavailable', {'now': _local_now()})
                    last_write = time.monotonic()
                if fresh:
                    yield realtime.sse('calls', {'calls': views(fresh.values()), 'now': _local_now()})
                    last_write = time.monotonic()
                current = live_hub.connector_view()
                if current != connector:
                    connector = current
                    yield realtime.sse('connector', {'connector': connector, 'now': _local_now()})
                    last_write = time.monotonic()
            yield realtime.sse('bye', {'reason': 'lifetime'})

        return _stream_response(generate, lambda: live_hub.release('browser'))

    @pbx_route('/call', methods=('POST',))
    def call(ctx, my_ext):
        if not access.can_call(ctx, my_ext):
            return jsonify({"error": "У вас не указан внутренний номер станции — звонить нечем. "
                                     "Номер заполняется в карточке сотрудника."}), 400
        body = request.get_json(silent=True) or {}
        phone = touches_mod.norm_phone(body.get('phone'))
        if not phone:
            raise ValueError('Номер клиента должен содержать десять цифр')
        prefix = str(body.get('prefix') or '').strip()
        with db._get_cursor() as cursor:
            state = queries.connector_state(cursor)
            if not prefix:
                prefix = _prefix_for(cursor, state.get('lines') or {}, body, my_ext)
            if not prefix:
                return jsonify({"error": "Не выбрана линия, с которой звонить", "code": "PBX_LINE_NEEDED"}), 400
            if prefix not in (state.get('lines') or {}):
                raise ValueError('Линии с префиксом %s на станции нет' % prefix)
            if not _connector_alive(state):
                return jsonify({"error": "Коннектор станции не на связи — позвонить сейчас нельзя",
                                "code": "PBX_CONNECTOR_DOWN"}), 503
            dial = pbx_calls.dial_string(prefix, phone)
            command_id = queries.enqueue_command(cursor, 'originate', {
                'ext': my_ext, 'dial': dial, 'caller_name': _pretty_phone(phone),
            }, requested_by=ctx['user_id'])
            realtime.notify(cursor, 'cmd')
        log.info('АТС: пользователь %s звонит с %s на …%s через %s (команда %d)',
                 ctx['user_id'], my_ext, phone[-4:], prefix, command_id)
        return jsonify({'status': 'queued', 'command_id': command_id, 'dial': dial})

    def _prefix_for(cursor, lines, body, my_ext):
        """Линия, если её не назвали: по номеру линии звонка (перезвон туда, откуда звонили),
        иначе — последний маршрут, которым этот оператор набирал."""
        number = touches_mod.norm_phone(body.get('line_number'))
        linkedid = str(body.get('linkedid') or '')[:64]
        if not number and linkedid:
            source = queries.call_by_linkedid(cursor, linkedid)
            if source:
                if source.get('dial_prefix'):
                    return source['dial_prefix']
                number = touches_mod.norm_phone(source.get('did'))
        if number:
            for prefix, line in lines.items():
                if str((line or {}).get('number') or '') == number:
                    return prefix
        return queries.last_prefix_for_ext(cursor, my_ext)

    @pbx_route('/hangup', methods=('POST',))
    def hangup(ctx, my_ext):
        body = request.get_json(silent=True) or {}
        linkedid = str(body.get('linkedid') or '')[:64]
        if not linkedid:
            raise ValueError('Не назван звонок')
        with db._get_cursor() as cursor:
            target = queries.call_by_linkedid(cursor, linkedid)
            if not target or target['state'] == 'ended':
                return jsonify({"error": "Звонок уже закончился"}), 409
            if not access.can_hangup(ctx, target, my_ext):
                return jsonify({"error": "Положить трубку можно только в своём звонке"}), 403
            channel = target['agent_channel'] or target['channel']
            if not channel:
                return jsonify({"error": "У звонка ещё нет канала, который можно отбить"}), 409
            command_id = queries.enqueue_command(cursor, 'hangup', {'channel': channel},
                                                 requested_by=ctx['user_id'], linkedid=linkedid)
            realtime.notify(cursor, 'cmd')
        return jsonify({'status': 'queued', 'command_id': command_id})

    @pbx_route('/spy', methods=('POST',))
    def spy(ctx, my_ext):
        if not access.can_spy(ctx):
            return jsonify({"error": "Подключаться к разговорам могут руководители и супервайзеры"}), 403
        if not my_ext:
            return jsonify({"error": "У вас не указан внутренний номер станции — подключаться нечем"}), 400
        body = request.get_json(silent=True) or {}
        linkedid = str(body.get('linkedid') or '')[:64]
        mode = str(body.get('mode') or 'listen')
        if mode not in SPY_MODES:
            raise ValueError('Режим: listen, whisper или barge')
        with db._get_cursor() as cursor:
            target = queries.call_by_linkedid(cursor, linkedid)
            if not target or target['state'] != 'talking' or not target['agent_channel']:
                return jsonify({"error": "Подключиться можно только к идущему разговору"}), 409
            if pbx_calls.ext_of_channel(target['agent_channel']) == my_ext or my_ext == target.get('peer_ext'):
                return jsonify({"error": "Это ваш собственный разговор"}), 409
            command_id = queries.enqueue_command(cursor, 'spy', {
                'ext': my_ext, 'target_channel': target['agent_channel'], 'mode': mode,
            }, requested_by=ctx['user_id'], linkedid=linkedid)
            realtime.notify(cursor, 'cmd')
        log.info('АТС: пользователь %s подключается (%s) к звонку %s с %s (команда %d)',
                 ctx['user_id'], mode, linkedid, my_ext, command_id)
        return jsonify({'status': 'queued', 'command_id': command_id})

    @pbx_route('/commands/<int:command_id>')
    def command_status(ctx, my_ext, command_id):
        with db._get_cursor() as cursor:
            item = queries.command(cursor, command_id)
        if not item:
            return jsonify({"error": "Команда не найдена"}), 404
        if item['requested_by'] != ctx['user_id'] and not access.can_spy(ctx):
            return jsonify({"error": "Чужая команда"}), 403
        return jsonify({'id': item['id'], 'kind': item['kind'], 'status': item['status'],
                        'message': item['message'], 'linkedid': item['linkedid']})

    return bp


# ── представления ────────────────────────────────────────────────────────────

def _local_now():
    return datetime.now(_ALMATY).replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M:%S')


def _connector_alive(state, now=None):
    """«На связи» — здоровался не дольше CONNECTOR_SILENT_SECONDS назад. last_seen_at —
    ISO с поясом (TIMESTAMPTZ, сессия Postgres в UTC), сравниваем в абсолютном времени."""
    last_seen = state.get('last_seen_at')
    if not last_seen:
        return False
    try:
        parsed = datetime.fromisoformat(str(last_seen))
    except ValueError:
        return False
    if parsed.tzinfo is not None:
        # now_almaty() — наивное время Алматы (UTC+5); приводим отметку к нему же.
        parsed = parsed.astimezone(_ALMATY).replace(tzinfo=None)
    moment = now or cdr_queries.now_almaty()
    return (moment - parsed).total_seconds() <= CONNECTOR_SILENT_SECONDS


def _connector_view(state):
    """Состояние коннектора для экрана — та же форма, что Hub.connector_view() в потоке."""
    state = dict(state or {})
    connected = _connector_alive(state)
    stream_at = state.get('stream_at')
    return {
        'connected': connected,
        'ami_connected': bool(state.get('ami_connected')),
        'online': connected and bool(state.get('ami_connected')),
        'stream': bool(stream_at) and _connector_alive({'last_seen_at': stream_at}),
        'ami_error': state.get('ami_error') or None,
        'version': state.get('version') or None,
    }


def _pretty_phone(digits):
    digits = str(digits or '')
    if len(digits) != 10:
        return digits
    return '+7 %s %s-%s-%s' % (digits[:3], digits[3:6], digits[6:8], digits[8:])


def _call_view(call, resolve, known, my_ext):
    """Звонок для экрана: номер клиента, оператор по справочнику, парк линии, мои ли."""
    out = {key: call.get(key) for key in (
        'linkedid', 'direction', 'phone', 'did', 'queue', 'ext', 'peer_ext', 'ringing', 'state',
        'started_at', 'queued_at', 'ringing_at', 'answered_at', 'ended_at', 'wait_seconds',
        'hangup_cause', 'hangup_side', 'dial_prefix', 'dial_status', 'recording_file', 'origin',
        'command_id', 'rev')}
    out['ringing'] = list(call.get('ringing') or [])
    out['has_agent_channel'] = bool(call.get('agent_channel'))
    when = call.get('started_at')
    out['operator'] = resolve(call['ext'], when)[0] if call.get('ext') else ''
    out['peer_operator'] = resolve(call['peer_ext'], when)[0] if call.get('peer_ext') else ''
    out['ringing_operators'] = [resolve(ext, when)[0] for ext in out['ringing']]
    number = touches_mod.norm_phone(call.get('did')) if call.get('direction') == pbx_calls.DIRECTION_IN else ''
    queue = call.get('queue') or (known.get(number) if number else '')
    out['park'] = lines_mod.park_of_queue(queue) if queue else ''
    out['line_number'] = number
    out['mine'] = access.is_own_call(call, my_ext)
    return out


def _clean_lines(lines):
    out = {}
    for prefix, line in list(lines.items())[:MAX_LINES]:
        prefix = str(prefix or '')[:8]
        if not prefix.isdigit() or not isinstance(line, dict):
            continue
        number = touches_mod.norm_phone(line.get('number'))
        if not number:
            continue
        out[prefix] = {'number': number, 'name': str(line.get('name') or '')[:60]}
    return out
