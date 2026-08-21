"""Раздел «Тренажёр»: голосовой разговор с ИИ и разбор после него.

Два режима одной механики:

    driver  — ИИ играет ВОДИТЕЛЯ, стажёр отвечает как оператор, в конце разбор.
    mentor  — наоборот: человек спрашивает, ИИ отвечает как ОПЫТНЫЙ ОПЕРАТОР,
              опираясь на базу знаний вики.

Режим «наставник» НЕ строит свой поиск. Он вызывает тот же движок, что и
чат-помощник вики: тот же периметр статей (человек слышит только то, что ему
разрешено читать), тот же гибридный поиск, тот же генератор. Второй RAG рядом с
существующим означал бы вторую точку правды и второй набор прав.

ЗВУК ЧЕРЕЗ ЭТОТ СЕРВЕР НЕ ИДЁТ. Прод работает на waitress (WSGI), где WebSocket
невозможен, а гнать аудио через SSE значит платить лишним кругом и base64.
Поэтому микрофон соединяется с Soniox НАПРЯМУЮ по короткому ключу из /tokens, а
озвучку отдаёт сервер: Live API эфемерные токены в браузере не принимает. Наш
сервер держит права, роль собеседника и замеры — ровно то, ради чего он нужен.

Раздел закрыт для всех, кроме супер-админа: он тестовый, тратит платные квоты и
выдаёт браузеру ключи к внешним сервисам.
"""
from __future__ import annotations

import json
import logging
import time
from functools import wraps

import httpx
from flask import Blueprint, jsonify, request

from . import scenarios

# ── тарифы, по которым считается стоимость прогона ───────────────────────────
# Лежат здесь и копируются в саму сессию (rates jsonb): тарифы меняются, а
# вопрос «сколько стоил вон тот прогон» задаётся задним числом.
RATES_USD = {
    'stt_per_min': 0.0020,        # Soniox stt-rt-v5: $0.12/час
    'tts_out_per_min': 0.0180,    # Gemini Live, выходное аудио: $12/1M при 25 ток/с
    'tts_in_per_mtok': 0.75,      # текст, который отдаём на озвучку
    'llm_in_per_mtok': 1.50,      # gemini-3.5-flash, вход
    'llm_out_per_mtok': 9.00,     # он же, выход
    'note': 'прайс ai.google.dev на 22.08.2026; проверять при смене моделей',
}

SONIOX_TEMP_KEY_URL = 'https://api.soniox.com/v1/auth/temporary-api-key'
GEMINI_GENERATE = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
GEMINI_LIVE_WS = ('wss://generativelanguage.googleapis.com/ws/'
                  'google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent')


def _gemini_headers(key):
    """Ключ Gemini передаётся ЗАГОЛОВКОМ, а не '?key=' в адресе.

    httpx логирует полный URL на уровне INFO, и query-параметр с ключом попадал
    в логи Render открытым текстом (обнаружено 22.08.2026). Заголовок в лог не
    пишется. Проверено, что и REST, и WebSocket Live принимают x-goog-api-key.
    """
    return {'x-goog-api-key': key}
CLAUDE_URL = 'https://api.anthropic.com/v1/messages'

MAX_TURNS = 60
MAX_TEXT = 2000


def build_trainer_blueprint(*, db, require_api_key, build_cors_preflight_response,
                            resolve_requester, is_super_admin_role, env):
    """Собирает Blueprint раздела.

    is_super_admin_role — (role) -> bool из монолита: нормализация ролей и
    таблица уровней живут там, дублировать их здесь значило бы завести вторую
    трактовку слова «супер-админ».

    env — (key, default=None) -> str: доступ к секретам. Приходит аргументом,
    чтобы раздел не решал сам, откуда берутся ключи (на проде это окружение
    Render, локально — .env.codex.local).
    """
    bp = Blueprint('trainer', __name__, url_prefix='/api/trainer')

    def trainer_route(rule, methods=('GET',)):
        """Каркас роута: preflight, авторизация, гейт супер-админа, ошибки."""
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({'error': message}), status

                    with db._get_cursor() as cursor:
                        cursor.execute('SELECT id, name, role FROM users WHERE id = %s',
                                       (requester_id,))
                        row = cursor.fetchone()
                    if not row:
                        return jsonify({'error': 'Пользователь не найден'}), 404
                    user = {'id': row[0], 'name': row[1], 'role': row[2]}
                    # Гейт здесь, а не во фронте: спрятанный пункт меню доступом
                    # не является, раздел открывается и прямым адресом.
                    if not is_super_admin_role(user['role']):
                        return jsonify({
                            'error': 'Раздел «Тренажёр» доступен только супер-админу',
                            'code': 'TRAINER_FORBIDDEN',
                        }), 403
                    return handler(*args, user=user, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('trainer: ошибка в %s', rule)
                    return jsonify({
                        'error': 'Внутренняя ошибка раздела «Тренажёр»',
                        'detail': str(exc)[:200],
                    }), 500

            return wrapper

        return decorator

    def _body():
        return request.get_json(silent=True) or {}

    def _log_event(cursor, session_id, user_id, level, code, message=None, payload=None):
        cursor.execute(
            """
            INSERT INTO trainer_events (session_id, user_id, level, code, message, payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (session_id, user_id, level, code, (message or '')[:500],
             json.dumps(payload, ensure_ascii=False) if payload else None))

    # ── служебное ────────────────────────────────────────────────────────────

    @trainer_route('/ping')
    def trainer_ping(user):
        """Готовность раздела: есть ли схема и все ли ключи на месте.

        Раздел зависит от трёх внешних сервисов, и молчаливый отказ одного из
        них выглядит как «оно не работает». Пусть будет видно, чего именно нет.
        """
        schema_ready = True
        try:
            with db._get_cursor() as cursor:
                cursor.execute('SELECT 1 FROM trainer_sessions LIMIT 1')
        except Exception:
            schema_ready = False
        return jsonify({
            'ok': True,
            'schema_ready': schema_ready,
            'keys': {
                'soniox': bool(env('SONIOX_API_KEY')),
                'gemini': bool(env('GEMINI_API_KEY')),
                'claude': bool(env('CLAUDE_API_KEY') or env('ANTHROPIC_API_KEY')),
            },
            'modes': ['driver', 'mentor'],
            'rates': RATES_USD,
        })

    @trainer_route('/scenarios')
    def trainer_scenarios(user):
        return jsonify({'scenarios': [
            {'key': key, 'title': value['title'], 'difficulty': value['difficulty'],
             'lang': value['lang'], 'opening': value['opening'],
             'expected': value['expected']}
            for key, value in scenarios.SCENARIOS.items()
        ]})

    @trainer_route('/tokens', methods=('POST',))
    def trainer_tokens(user):
        """Короткоживущий ключ для браузера: только распознавание.

        Постоянные ключи в браузер не попадают: ключ Soniox живёт 10 минут и
        годен единственно на транскрипцию. Ключ Gemini браузеру не нужен вовсе —
        озвучивает сервер.
        """
        out, problems = {}, []

        soniox_key = env('SONIOX_API_KEY')
        if soniox_key:
            try:
                response = httpx.post(
                    SONIOX_TEMP_KEY_URL,
                    headers={'Authorization': f'Bearer {soniox_key}'},
                    json={'usage_type': 'transcribe_websocket', 'expires_in_seconds': 600},
                    timeout=20)
                if response.status_code in (200, 201):
                    data = response.json()
                    out['soniox'] = {'api_key': data.get('api_key'),
                                     'expires_at': data.get('expires_at')}
                else:
                    problems.append(f'soniox: HTTP {response.status_code}')
            except Exception as exc:  # noqa: BLE001
                problems.append(f'soniox: {type(exc).__name__}')
        else:
            problems.append('soniox: нет ключа в окружении')

        # Токен Gemini браузеру НЕ выдаём: Live API эфемерные токены в
        # браузерном соединении не принимает (сокет закрывается с 1008), а
        # озвучку всё равно отдаёт сервер. Лишний вызов только жёг квоту — и
        # именно он светил ключ в логах.
        if not env('GEMINI_API_KEY'):
            problems.append('gemini: нет ключа в окружении')

        out['problems'] = problems
        out['stt'] = {'model': 'stt-rt-v5',
                      'url': 'wss://stt-rt.soniox.com/transcribe-websocket',
                      'endpoint_ms': int(env('TRAINER_ENDPOINT_MS', '600'))}
        out['tts'] = {'model': env('TRAINER_LIVE_MODEL', 'gemini-3.1-flash-live-preview'),
                      'rate': 24000}
        return jsonify(out)

    @trainer_route('/speak', methods=('POST',))
    def trainer_speak(user):
        """Озвучка ответа: сервер держит сокет к Live API, браузер слушает SSE.

        Почему не напрямую из браузера, как распознавание: эфемерные токены Live
        соединение не принимают (проверено 22.08.2026 — сокет закрывается с 1008
        на всех вариантах), а постоянному ключу в браузере не место. Цена
        решения — один лишний сетевой круг на реплику; звук при этом всё равно
        течёт кусками, а не ждёт полной генерации.

        Тайминги считает сервер и он же кладёт их в реплику: браузеру остаётся
        досказать своё — когда звук реально зазвучал в колонках.
        """
        payload = _body()
        text = (payload.get('text') or '').strip()[:MAX_TEXT]
        turn_id = payload.get('turn_id')
        session_id = payload.get('session_id')
        if not text:
            return jsonify({'error': 'нечего произносить'}), 400

        model = env('TRAINER_LIVE_MODEL', 'gemini-3.1-flash-live-preview')
        api_key = env('GEMINI_API_KEY')
        # Голос берём из сессии, а не из тела запроса: иначе его можно было бы
        # подменить из браузера. Проверка по списку — там только мужские голоса,
        # отобранные замером основного тона.
        voice = scenarios.MENTOR_VOICE
        if session_id:
            with db._get_cursor() as cursor:
                cursor.execute('SELECT tts_voice FROM trainer_sessions WHERE id = %s AND user_id = %s',
                               (session_id, user['id']))
                row = cursor.fetchone()
            if row and row[0] in scenarios.MALE_VOICES:
                voice = row[0]

        def generate():
            import websocket  # локальный импорт: нужен только этой ручке

            started = time.perf_counter()
            first_ms, total_bytes = None, 0
            socket = None
            try:
                socket = websocket.create_connection(
                    GEMINI_LIVE_WS, header=[f'x-goog-api-key: {api_key}'], timeout=60)
                socket.send(json.dumps({'setup': {
                    'model': f'models/{model}',
                    'generationConfig': {
                        'responseModalities': ['AUDIO'],
                        'speechConfig': {'voiceConfig': {
                            'prebuiltVoiceConfig': {'voiceName': voice}}},
                    }}}))
                socket.send(json.dumps({'clientContent': {
                    'turns': [{'role': 'user',
                               'parts': [{'text': scenarios.SAY_EXACTLY + text}]}],
                    'turnComplete': True}}))
                while True:
                    raw = socket.recv()
                    if isinstance(raw, bytes):
                        raw = raw.decode('utf-8', 'ignore')
                    if not raw:
                        break
                    message = json.loads(raw)
                    if 'setupComplete' in message:
                        continue
                    content = message.get('serverContent') or {}
                    for part in ((content.get('modelTurn') or {}).get('parts') or []):
                        chunk = (part.get('inlineData') or {}).get('data')
                        if not chunk:
                            continue
                        if first_ms is None:
                            first_ms = int((time.perf_counter() - started) * 1000)
                        total_bytes += len(chunk) * 3 // 4
                        yield f'data: {json.dumps({"t": "audio", "b64": chunk})}\n\n'
                    if content.get('turnComplete') or content.get('generationComplete'):
                        break
            except Exception as exc:  # noqa: BLE001
                yield f'data: {json.dumps({"t": "error", "message": str(exc)[:200]})}\n\n'
            finally:
                if socket is not None:
                    try:
                        socket.close()
                    except Exception:
                        pass

            audio_ms = int(total_bytes / 2 / 24000 * 1000)
            if turn_id:
                try:
                    with db._get_cursor() as cursor:
                        cursor.execute(
                            """
                            UPDATE trainer_turns SET tts_model = %s, tts_ttfb_ms = %s,
                                   tts_audio_ms = %s, tts_bytes = %s
                             WHERE id = %s
                            """, (model, first_ms, audio_ms, total_bytes, turn_id))
                        cursor.execute(
                            """
                            UPDATE trainer_sessions s SET audio_out_ms = s.audio_out_ms + %s
                              FROM trainer_turns t
                             WHERE t.id = %s AND t.session_id = s.id
                            """, (audio_ms, turn_id))
                except Exception:
                    logging.exception('trainer: не удалось записать замеры озвучки')
            yield ('data: ' + json.dumps({'t': 'done', 'ttfb_ms': first_ms,
                                          'audio_ms': audio_ms, 'bytes': total_bytes,
                                          'rate': 24000, 'model': model}) + '\n\n')

        from flask import Response, stream_with_context
        response = Response(stream_with_context(generate()), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Accel-Buffering'] = 'no'
        return response

    # ── жизненный цикл разговора ─────────────────────────────────────────────

    @trainer_route('/sessions', methods=('POST',))
    def trainer_session_create(user):
        payload = _body()
        mode = payload.get('mode') if payload.get('mode') in ('driver', 'mentor') else 'driver'
        key = payload.get('scenario') or scenarios.DEFAULT
        scenario = scenarios.SCENARIOS.get(key) if mode == 'driver' else None
        if mode == 'driver' and not scenario:
            return jsonify({'error': 'неизвестный сценарий'}), 400

        with db._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO trainer_sessions
                    (user_id, mode, scenario_key, scenario_title, difficulty, lang,
                     stt_model, llm_provider, llm_model, tts_model, tts_voice,
                     rates, client)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, started_at
                """,
                (user['id'], mode,
                 key if mode == 'driver' else None,
                 scenario['title'] if scenario else 'Наставник по базе знаний',
                 scenario['difficulty'] if scenario else None,
                 scenario['lang'] if scenario else (payload.get('lang') or 'ru'),
                 'stt-rt-v5',
                 _driver_provider(), _driver_model(),
                 env('TRAINER_LIVE_MODEL', 'gemini-3.1-flash-live-preview'),
                 (scenario or {}).get('voice') if scenario
                 else scenarios.MENTOR_VOICE,
                 json.dumps(RATES_USD, ensure_ascii=False),
                 json.dumps(payload.get('client') or {}, ensure_ascii=False)))
            row = cursor.fetchone()
            session_id = row[0]
            _log_event(cursor, session_id, user['id'], 'info', 'session_start',
                       f'режим {mode}', {'scenario': key})

        opening = scenario['opening'] if scenario else None
        if opening:
            with db._get_cursor() as cursor:
                _save_turn(cursor, session_id, 0, 'driver', opening, {})
                cursor.execute('UPDATE trainer_sessions SET turns_count = 1 WHERE id = %s',
                               (session_id,))
        return jsonify({
            'session_id': session_id,
            'mode': mode,
            'started_at': row[1].isoformat() if row[1] else None,
            'opening': opening,
            'title': scenario['title'] if scenario else 'Наставник по базе знаний',
            'difficulty': scenario['difficulty'] if scenario else None,
        })

    @trainer_route('/sessions/<int:session_id>/turn', methods=('POST',))
    def trainer_turn(user, session_id):
        """Реплика человека → ответ собеседника. Замеры пишутся на обе реплики."""
        payload = _body()
        text = (payload.get('text') or '').strip()[:MAX_TEXT]
        if not text:
            return jsonify({'error': 'пустая реплика'}), 400

        with db._get_cursor() as cursor:
            session = _load_session(cursor, session_id, user['id'])
            if not session:
                return jsonify({'error': 'сессия не найдена'}), 404
            if session['status'] != 'active':
                return jsonify({'error': 'сессия уже закрыта'}), 409
            history = _load_history(cursor, session_id)
            if len(history) >= MAX_TURNS:
                return jsonify({'error': 'слишком длинный разговор'}), 409
            idx = len(history)
            # Реплика человека со всеми замерами распознавания, которые пришли
            # из браузера: там они и измеряются, сюда попадают как есть.
            human_role = 'trainee' if session['mode'] == 'driver' else 'asker'
            _save_turn(cursor, session_id, idx, human_role, text, {
                'stt_lang': payload.get('stt_lang'),
                'stt_langs': payload.get('stt_langs'),
                'stt_confidence': payload.get('stt_confidence'),
                'stt_tokens': payload.get('stt_tokens'),
                'stt_audio_ms': payload.get('stt_audio_ms'),
                'endpoint_delay_ms': payload.get('endpoint_delay_ms'),
                'barge_in': bool(payload.get('barge_in')),
            })

        started = time.perf_counter()
        if session['mode'] == 'driver':
            reply, meta = _driver_reply(session, history, text)
        else:
            reply, meta = _mentor_reply(user, history, text)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        with db._get_cursor() as cursor:
            if meta.get('error'):
                _log_event(cursor, session_id, user['id'], 'error', 'reply_failed',
                           meta['error'], meta)
                return jsonify({'error': 'ИИ не ответил', 'detail': meta['error']}), 503
            if meta.get('switched'):
                _log_event(cursor, session_id, user['id'], 'warn', 'provider_switched',
                           meta['switched'], meta)
            reply_role = 'driver' if session['mode'] == 'driver' else 'mentor'
            turn_id = _save_turn(cursor, session_id, idx + 1, reply_role, reply, {
                'llm_provider': meta.get('provider'),
                'llm_model': meta.get('model'),
                'llm_total_ms': elapsed_ms,
                'llm_first_token_ms': meta.get('first_token_ms'),
                'llm_input_tokens': meta.get('input_tokens'),
                'llm_output_tokens': meta.get('output_tokens'),
                'llm_cached_tokens': meta.get('cached_tokens'),
                'sources': meta.get('sources'),
                'raw': meta.get('raw'),
            })
            cursor.execute(
                """
                UPDATE trainer_sessions
                   SET turns_count = %s,
                       llm_provider = COALESCE(%s, llm_provider),
                       llm_model = COALESCE(%s, llm_model),
                       barge_ins = barge_ins + %s,
                       audio_in_ms = audio_in_ms + %s
                 WHERE id = %s
                """,
                (idx + 2, meta.get('provider'), meta.get('model'),
                 1 if payload.get('barge_in') else 0,
                 int(payload.get('stt_audio_ms') or 0), session_id))

        return jsonify({
            'turn_id': turn_id,
            'idx': idx + 1,
            'role': reply_role,
            'text': reply,
            'sources': meta.get('sources') or [],
            'kind': meta.get('kind'),
            'llm_ms': elapsed_ms,
            'provider': meta.get('provider'),
            'model': meta.get('model'),
        })

    @trainer_route('/turns/<int:turn_id>', methods=('PATCH',))
    def trainer_turn_metrics(user, turn_id):
        """Замеры, которые есть только у браузера: озвучка и слышимая пауза.

        Сервер не знает, когда у стажёра в колонках появился звук, — измерить
        это можно лишь на стороне, где он играет.
        """
        payload = _body()
        fields = {
            'tts_ttfb_ms': payload.get('tts_ttfb_ms'),
            'tts_audio_ms': payload.get('tts_audio_ms'),
            'tts_bytes': payload.get('tts_bytes'),
            'tts_model': payload.get('tts_model'),
            'voice_to_voice_ms': payload.get('voice_to_voice_ms'),
        }
        sets = [f'{name} = %s' for name, value in fields.items() if value is not None]
        values = [value for value in fields.values() if value is not None]
        if not sets:
            return jsonify({'ok': True, 'updated': 0})
        with db._get_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE trainer_turns t SET {', '.join(sets)}
                  FROM trainer_sessions s
                 WHERE t.id = %s AND t.session_id = s.id AND s.user_id = %s
                RETURNING t.session_id
                """,
                (*values, turn_id, user['id']))
            row = cursor.fetchone()
            if row and payload.get('tts_audio_ms'):
                cursor.execute(
                    'UPDATE trainer_sessions SET audio_out_ms = audio_out_ms + %s WHERE id = %s',
                    (int(payload['tts_audio_ms']), row[0]))
        return jsonify({'ok': True, 'updated': 1 if row else 0})

    @trainer_route('/sessions/<int:session_id>/event', methods=('POST',))
    def trainer_event(user, session_id):
        payload = _body()
        with db._get_cursor() as cursor:
            session = _load_session(cursor, session_id, user['id'])
            if not session:
                return jsonify({'error': 'сессия не найдена'}), 404
            _log_event(cursor, session_id, user['id'],
                       payload.get('level') or 'info',
                       (payload.get('code') or 'client')[:60],
                       payload.get('message'), payload.get('payload'))
        return jsonify({'ok': True})

    @trainer_route('/sessions/<int:session_id>/finish', methods=('POST',))
    def trainer_finish(user, session_id):
        """Закрывает разговор: сводные тайминги, стоимость и — в режиме
        водителя — разбор вторым ИИ."""
        with db._get_cursor() as cursor:
            session = _load_session(cursor, session_id, user['id'])
            if not session:
                return jsonify({'error': 'сессия не найдена'}), 404
            history = _load_history(cursor, session_id)
            cursor.execute(
                """
                SELECT voice_to_voice_ms, tts_audio_ms, stt_audio_ms,
                       llm_input_tokens, llm_output_tokens
                  FROM trainer_turns WHERE session_id = %s
                """, (session_id,))
            rows = cursor.fetchall()

        paces = sorted(r[0] for r in rows if r[0])
        p50 = paces[len(paces) // 2] if paces else None
        worst = paces[-1] if paces else None
        tts_ms = sum(r[1] or 0 for r in rows)
        stt_ms = sum(r[2] or 0 for r in rows)
        tok_in = sum(r[3] or 0 for r in rows)
        tok_out = sum(r[4] or 0 for r in rows)
        cost = {
            'stt': round(stt_ms / 60000 * RATES_USD['stt_per_min'], 6),
            'tts': round(tts_ms / 60000 * RATES_USD['tts_out_per_min'], 6),
            'llm': round(tok_in / 1e6 * RATES_USD['llm_in_per_mtok']
                         + tok_out / 1e6 * RATES_USD['llm_out_per_mtok'], 6),
        }
        cost['total'] = round(sum(cost.values()), 6)

        review, review_meta = None, {}
        if session['mode'] == 'driver' and history:
            review, review_meta = _review(session, history)

        with db._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE trainer_sessions
                   SET status = %s, finished_at = NOW(),
                       duration_ms = EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000,
                       voice_to_voice_p50 = %s, voice_to_voice_max = %s,
                       score = %s, review = %s, review_provider = %s,
                       review_model = %s, review_ms = %s,
                       cost_usd = %s, cost_breakdown = %s, error = %s
                 WHERE id = %s
                """,
                ('finished' if not review_meta.get('error') else 'error',
                 p50, worst,
                 (review or {}).get('score'),
                 json.dumps(review, ensure_ascii=False) if review else None,
                 review_meta.get('provider'), review_meta.get('model'),
                 review_meta.get('elapsed_ms'), cost['total'],
                 json.dumps(cost, ensure_ascii=False),
                 review_meta.get('error'), session_id))
            if review_meta.get('error'):
                _log_event(cursor, session_id, user['id'], 'error', 'review_failed',
                           review_meta['error'])

        return jsonify({'ok': True, 'review': review, 'cost': cost,
                        'voice_to_voice_p50': p50, 'voice_to_voice_max': worst,
                        'review_error': review_meta.get('error')})

    # ── журнал ───────────────────────────────────────────────────────────────

    @trainer_route('/sessions')
    def trainer_sessions(user):
        mode = request.args.get('mode')
        with db._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id, s.mode, s.scenario_title, s.difficulty, s.lang, s.status,
                       s.started_at, s.duration_ms, s.turns_count, s.score,
                       s.voice_to_voice_p50, s.voice_to_voice_max, s.cost_usd,
                       s.llm_provider, s.llm_model, u.name,
                       (SELECT COUNT(*) FROM trainer_events e
                         WHERE e.session_id = s.id AND e.level = 'error')
                  FROM trainer_sessions s
                  LEFT JOIN users u ON u.id = s.user_id
                 WHERE (%s IS NULL OR s.mode = %s)
                 ORDER BY s.started_at DESC
                 LIMIT 200
                """, (mode, mode))
            rows = cursor.fetchall()
        return jsonify({'sessions': [{
            'id': r[0], 'mode': r[1], 'title': r[2], 'difficulty': r[3], 'lang': r[4],
            'status': r[5], 'started_at': r[6].isoformat() if r[6] else None,
            'duration_ms': r[7], 'turns': r[8], 'score': r[9],
            'pace_p50': r[10], 'pace_max': r[11],
            'cost_usd': float(r[12]) if r[12] is not None else None,
            'provider': r[13], 'model': r[14], 'user': r[15], 'errors': r[16],
        } for r in rows]})

    @trainer_route('/sessions/<int:session_id>')
    def trainer_session_detail(user, session_id):
        with db._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, mode, scenario_key, scenario_title, difficulty, lang, status,
                       started_at, finished_at, duration_ms, turns_count, barge_ins,
                       audio_in_ms, audio_out_ms, voice_to_voice_p50, voice_to_voice_max,
                       score, review, cost_usd, cost_breakdown, rates, client,
                       llm_provider, llm_model, tts_model, error
                  FROM trainer_sessions WHERE id = %s
                """, (session_id,))
            s = cursor.fetchone()
            if not s:
                return jsonify({'error': 'сессия не найдена'}), 404
            cursor.execute(
                """
                SELECT id, idx, role, text, created_at, stt_lang, stt_langs,
                       stt_confidence, stt_tokens, stt_audio_ms, endpoint_delay_ms,
                       llm_provider, llm_model, llm_first_token_ms, llm_total_ms,
                       llm_input_tokens, llm_output_tokens, tts_model, tts_ttfb_ms,
                       tts_audio_ms, tts_bytes, voice_to_voice_ms, barge_in, sources
                  FROM trainer_turns WHERE session_id = %s ORDER BY idx
                """, (session_id,))
            turns = cursor.fetchall()
            cursor.execute(
                """
                SELECT at, level, code, message, payload FROM trainer_events
                 WHERE session_id = %s ORDER BY at
                """, (session_id,))
            events = cursor.fetchall()

        return jsonify({
            'session': {
                'id': s[0], 'mode': s[1], 'scenario_key': s[2], 'title': s[3],
                'difficulty': s[4], 'lang': s[5], 'status': s[6],
                'started_at': s[7].isoformat() if s[7] else None,
                'finished_at': s[8].isoformat() if s[8] else None,
                'duration_ms': s[9], 'turns': s[10], 'barge_ins': s[11],
                'audio_in_ms': s[12], 'audio_out_ms': s[13],
                'pace_p50': s[14], 'pace_max': s[15], 'score': s[16], 'review': s[17],
                'cost_usd': float(s[18]) if s[18] is not None else None,
                'cost_breakdown': s[19], 'rates': s[20], 'client': s[21],
                'provider': s[22], 'model': s[23], 'tts_model': s[24], 'error': s[25],
            },
            'turns': [{
                'id': t[0], 'idx': t[1], 'role': t[2], 'text': t[3],
                'at': t[4].isoformat() if t[4] else None,
                'stt': {'lang': t[5], 'langs': t[6], 'confidence': t[7],
                        'tokens': t[8], 'audio_ms': t[9], 'endpoint_ms': t[10]},
                'llm': {'provider': t[11], 'model': t[12], 'first_token_ms': t[13],
                        'total_ms': t[14], 'in': t[15], 'out': t[16]},
                'tts': {'model': t[17], 'ttfb_ms': t[18], 'audio_ms': t[19], 'bytes': t[20]},
                'pace_ms': t[21], 'barge_in': t[22], 'sources': t[23],
            } for t in turns],
            'events': [{'at': e[0].isoformat() if e[0] else None, 'level': e[1],
                        'code': e[2], 'message': e[3], 'payload': e[4]} for e in events],
        })

    # ── внутреннее: хранение ─────────────────────────────────────────────────

    def _load_session(cursor, session_id, user_id):
        cursor.execute(
            'SELECT id, mode, scenario_key, status, user_id FROM trainer_sessions WHERE id = %s',
            (session_id,))
        row = cursor.fetchone()
        if not row or row[4] != user_id:
            return None
        return {'id': row[0], 'mode': row[1], 'scenario_key': row[2], 'status': row[3]}

    def _load_history(cursor, session_id):
        cursor.execute(
            'SELECT role, text FROM trainer_turns WHERE session_id = %s ORDER BY idx',
            (session_id,))
        return [{'role': r[0], 'text': r[1]} for r in cursor.fetchall()]

    def _save_turn(cursor, session_id, idx, role, text, extra):
        columns = ['session_id', 'idx', 'role', 'text']
        values = [session_id, idx, role, text]
        for name, value in extra.items():
            if value is None:
                continue
            columns.append(name)
            values.append(json.dumps(value, ensure_ascii=False)
                          if name in ('stt_langs', 'sources', 'raw') else value)
        placeholders = ', '.join(['%s'] * len(values))
        cursor.execute(
            f'INSERT INTO trainer_turns ({", ".join(columns)}) VALUES ({placeholders}) '
            f'ON CONFLICT (session_id, idx) DO NOTHING RETURNING id',
            values)
        row = cursor.fetchone()
        return row[0] if row else None

    # ── внутреннее: собеседники ──────────────────────────────────────────────

    def _driver_provider():
        return env('TRAINER_LLM', 'gemini')

    def _driver_model():
        return (env('TRAINER_GEMINI_MODEL', 'gemini-3.5-flash')
                if _driver_provider() == 'gemini'
                else env('TRAINER_CLAUDE_MODEL', 'claude-sonnet-5'))

    def _driver_reply(session, history, text):
        """Роль водителя. Порядок провайдеров — по факту доступности денег.

        Gemini стоит первым НЕ из предпочтения: пока у Anthropic нет баланса,
        попытка Claude — это гарантированный лишний круг к серверу на каждой
        реплике, то есть подаренная задержка.
        """
        system = scenarios.system_prompt(session['scenario_key'] or scenarios.DEFAULT)
        turns = [{'role': 'assistant' if h['role'] == 'driver' else 'user',
                  'content': h['text']} for h in history]
        turns.append({'role': 'user', 'content': text})

        first = _driver_provider()
        order = [first] + [p for p in ('gemini', 'claude') if p != first]
        fails = []
        for name in order:
            try:
                reply, meta = (_gemini_chat(system, turns) if name == 'gemini'
                               else _claude_chat(system, turns))
                if name != first:
                    meta['switched'] = f'{first} не ответил, ушли на {name}'
                return reply, meta
            except Exception as exc:  # noqa: BLE001
                fails.append(f'{name} — {str(exc)[:160]}')
        return '', {'error': '; '.join(fails)}

    def _mentor_reply(user, history, question):
        """Роль опытного оператора: ответ строится движком чат-помощника вики.

        Здесь намеренно нет ни своего поиска, ни своего промпта: и периметр
        статей, и правила «не выдумывать» уже реализованы там и покрыты
        замерами. Наш вклад — голос и запись метрик.
        """
        try:
            from wiki import (queries as wiki_queries, perimeter as wiki_perimeter,
                              access as wiki_access)
            from wiki.ai import (answer as ai_answer, embed as ai_embed,
                                 retrieve as ai_retrieve, providers as ai_providers)
        except Exception as exc:  # noqa: BLE001
            return '', {'error': f'помощник вики недоступен: {exc}'}

        prior = [{'kind': 'question' if h['role'] == 'asker' else 'answer',
                  'text': h['text']} for h in history][-6:]
        try:
            with db._get_cursor() as cursor:
                wctx = wiki_queries.load_access_context(cursor, user['id'])
                if not wctx:
                    return '', {'error': 'нет доступа к вики'}
                # Субъекты доступа контекст НЕ содержит: их досчитывает роут
                # вики и кладёт в ctx, а периметр их требует (KeyError:
                # 'subjects'). Считаем той же функцией, а не своим выводом —
                # второй источник истины здесь уже ломал исходную вику.
                wctx['subjects'] = wiki_access.collect_subjects(
                    user_id=wctx['user_id'],
                    otp_role=wctx['otp_role'],
                    department_id=wctx['department_id'],
                    headed_department_ids=wctx['headed_department_ids'],
                    direction_id=wctx['direction_id'],
                    group_ids=wctx['group_ids'],
                    wiki_role_ids=[r.get('id') for r in wctx['wiki_roles']],
                )
                # load_capabilities не просто возвращает права, а КЛАДЁТ их в
                # контекст — периметр читает ctx['capabilities'] напрямую.
                wiki_queries.load_capabilities(cursor, wctx, wctx['subjects'])
                scope = wiki_perimeter.assistant_perimeter(cursor, wctx, None)
                if not scope.get('article_ids'):
                    return '', {'error': 'помощнику не выдан доступ ни к одной статье'}
                search_query = ai_answer.enrich_query(question, prior)
                try:
                    vector = ai_embed.embed_query(search_query)
                except Exception:
                    vector = None          # деградация до лексики, не отказ
                found = ai_retrieve.search_hybrid(
                    cursor, article_ids=scope['article_ids'], query=search_query,
                    query_vector=vector, limit=8, per_article=3)
            result = ai_answer.compose(question, found['rows'], ai_providers.generate,
                                       history=prior, allow_clarify=True)
        except Exception as exc:  # noqa: BLE001
            return '', {'error': f'{type(exc).__name__}: {str(exc)[:200]}'}

        meta = result.get('meta') or {}
        usage = meta.get('usage') or {}
        return result.get('text') or '', {
            'provider': meta.get('provider'), 'model': meta.get('model'),
            'input_tokens': usage.get('prompt_tokens'),
            'output_tokens': usage.get('completion_tokens'),
            'kind': result.get('kind'),
            'sources': [{'title': s.get('title'), 'slug': s.get('slug'),
                         'quote': s.get('quote'), 'article_id': s.get('article_id')}
                        for s in (result.get('sources') or [])],
        }

    def _gemini_chat(system, turns):
        model = env('TRAINER_GEMINI_MODEL', 'gemini-3.5-flash')
        body = {
            'contents': [{'role': 'model' if t['role'] == 'assistant' else 'user',
                          'parts': [{'text': t['content']}]} for t in turns],
            'systemInstruction': {'parts': [{'text': system}]},
            'generationConfig': {'maxOutputTokens': 300, 'temperature': 0.9,
                                 'thinkingConfig': {'thinkingBudget': 0}},
        }
        response = httpx.post(GEMINI_GENERATE.format(model=model),
                              headers=_gemini_headers(env('GEMINI_API_KEY')),
                              json=body, timeout=45)
        if response.status_code >= 400:
            raise RuntimeError(f'HTTP {response.status_code} {response.text[:160]}')
        data = response.json()
        parts = ((data.get('candidates') or [{}])[0].get('content') or {}).get('parts') or []
        usage = data.get('usageMetadata') or {}
        return ''.join(p.get('text', '') for p in parts).strip(), {
            'provider': 'gemini', 'model': model,
            'input_tokens': usage.get('promptTokenCount'),
            'output_tokens': usage.get('candidatesTokenCount'),
            'cached_tokens': usage.get('cachedContentTokenCount'),
        }

    def _claude_chat(system, turns):
        model = env('TRAINER_CLAUDE_MODEL', 'claude-sonnet-5')
        response = httpx.post(
            CLAUDE_URL,
            headers={'x-api-key': env('CLAUDE_API_KEY') or env('ANTHROPIC_API_KEY'),
                     'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
            json={'model': model, 'max_tokens': 300,
                  'system': [{'type': 'text', 'text': system,
                              'cache_control': {'type': 'ephemeral'}}],
                  'messages': turns},
            timeout=45)
        if response.status_code >= 400:
            raise RuntimeError(f'HTTP {response.status_code} {response.text[:160]}')
        data = response.json()
        usage = data.get('usage') or {}
        text = ''.join(b.get('text', '') for b in data.get('content', [])
                       if b.get('type') == 'text').strip()
        return text, {
            'provider': 'claude', 'model': model,
            'input_tokens': usage.get('input_tokens'),
            'output_tokens': usage.get('output_tokens'),
            'cached_tokens': usage.get('cache_read_input_tokens'),
        }

    def _review(session, history):
        """Разбор работы стажёра вторым ИИ по стенограмме."""
        transcript = '\n'.join(
            f"{'Стажёр' if h['role'] == 'trainee' else 'Водитель'}: {h['text']}"
            for h in history)
        prompt = scenarios.review_prompt(session['scenario_key'] or scenarios.DEFAULT)
        started = time.perf_counter()
        model = env('TRAINER_REVIEW_MODEL', 'gemini-3.5-flash')
        body = {
            'contents': [{'role': 'user', 'parts': [{'text': f'СТЕНОГРАММА:\n{transcript}'}]}],
            'systemInstruction': {'parts': [{'text': prompt}]},
            'generationConfig': {'maxOutputTokens': 4000,
                                 'responseMimeType': 'application/json',
                                 'responseSchema': _gemini_schema(scenarios.REVIEW_SCHEMA)},
        }
        try:
            response = httpx.post(GEMINI_GENERATE.format(model=model),
                                  headers=_gemini_headers(env('GEMINI_API_KEY')),
                                  json=body, timeout=120)
            if response.status_code >= 400:
                raise RuntimeError(f'HTTP {response.status_code} {response.text[:200]}')
            parts = ((response.json().get('candidates') or [{}])[0]
                     .get('content') or {}).get('parts') or []
            blob = ''.join(p.get('text', '') for p in parts)
            return json.loads(blob), {
                'provider': 'gemini', 'model': model,
                'elapsed_ms': int((time.perf_counter() - started) * 1000)}
        except Exception as exc:  # noqa: BLE001
            return None, {'error': f'{type(exc).__name__}: {str(exc)[:200]}',
                          'elapsed_ms': int((time.perf_counter() - started) * 1000)}

    return bp


def _gemini_schema(schema):
    """Gemini не принимает additionalProperties — вычищаем, остальное совпадает."""
    if isinstance(schema, dict):
        return {k: _gemini_schema(v) for k, v in schema.items() if k != 'additionalProperties'}
    if isinstance(schema, list):
        return [_gemini_schema(x) for x in schema]
    return schema
