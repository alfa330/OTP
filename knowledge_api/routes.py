# -*- coding: utf-8 -*-
"""Внешний вход ручки знаний: ключ, вопрос, пределы, журнал.

    GET  /api/knowledge/ping          ключ принят? чьи права, какие пределы
    GET  /api/knowledge/parks         парки, по которым можно спрашивать
    POST /api/knowledge/search        {"park_id": 5, "question": "…", "limit": 8}

Сотрудником партнёрский сервис не является: нет ни учётки, ни сессии, ни QR —
вместо них ключ партнёра в заголовке X-Api-Key (partner_keys) с областью
«Ручка знаний», выпущенный супер-админом во вкладке «Ключи» раздела «Сессии».
Что здесь сторожится и почему:

* **До ключа не делается ничего.** Ни разбор тела, ни поиск. Неверный ключ,
  отозванный ключ и ключ, которого нет, — один ответ 401: чужому незачем
  знать, на чём он споткнулся. Ключ настоящий, но без области — 403. Схема
  ключей не развёрнута — 503, а не «пускаем всех».

* **Пределы.** Вызовов в минуту на ключ (KNOWLEDGE_API_PER_MINUTE, 60) и
  одновременных поисков на всех (KNOWLEDGE_API_LANES, 4): поиск считает вектор
  у провайдера и ходит в базу, и очередь из ждущих запросов держала бы нити
  портала. Сверх предела — 429 с Retry-After, без ожидания.

* **Журнал — строкой на вызов** (partner_keys.journal), включая отказы (нет
  данных, плохой запрос, предел). Пишется отдельной транзакцией ПОСЛЕ поиска:
  падение поиска не должно унести запись о том, что спрашивали; сама запись не
  держит соединение на время поиска.

* **«Данных нет» уходит супервайзерам.** Вопрос без ответа ложится в «Вопросы
  операторов» отдела ключа (wiki/questions.py: escalate_external) — как отказ
  помощника вики. Повтор того же вопроса в открытой очереди не плодит строк,
  а считается в asked_count.
"""

import logging
import os
import threading
import time
import uuid

from flask import Blueprint, jsonify, request

from partner_keys import auth as partner_auth
from partner_keys import journal as partner_journal
from partner_keys import scopes as partner_scopes
from partner_keys.limits import RateLimiter  # noqa: F401 — общий ограничитель ключей партнёров
from wiki import questions as wiki_questions

from . import perimeter
from . import search as knowledge_search

DEFAULT_PER_MINUTE = 60
MAX_PER_MINUTE = 600
DEFAULT_LANES = 4
MAX_LANES = 16
MAX_QUESTION_CHARS = 1000
MAX_BODY_BYTES = 16 * 1024
RETRY_AFTER_BUSY_SEC = 2

STATUSES = ('ok', 'no_data')


def _setting(environ, name, default, ceiling):
    raw = str((environ or os.environ).get(name) or '').strip()
    if not raw.isdigit():
        return default
    return max(1, min(int(raw), ceiling))


def build_knowledge_blueprint(*, db, client_ip=None, search=None, environ=None,
                              per_minute=None, lanes=None, escalate=None):
    """Фабрика: зависимости аргументами, bot_schedule2 не импортируется.

    search(cursor, key=, question=, park_id=, park=, limit=) — подменяется в тестах;
    escalate(cursor, **fields) — запись «Вопроса операторов», по умолчанию
    wiki.questions.escalate_external.
    """
    bp = Blueprint('knowledge_api', __name__, url_prefix='/api/knowledge')
    environ = environ if environ is not None else os.environ
    per_minute = per_minute or _setting(environ, 'KNOWLEDGE_API_PER_MINUTE',
                                        DEFAULT_PER_MINUTE, MAX_PER_MINUTE)
    lanes = lanes or _setting(environ, 'KNOWLEDGE_API_LANES', DEFAULT_LANES, MAX_LANES)
    limiter = RateLimiter(per_minute)
    slots = threading.BoundedSemaphore(lanes)
    run_search = search or knowledge_search.run
    run_escalate = escalate or wiki_questions.escalate_external

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

    # ── Ключ ─────────────────────────────────────────────────────────────────
    def _authenticate():
        """(ключ, отказ). Проверка — partner_keys.auth с областью «Ручка знаний»."""
        status, key = partner_auth.lookup(
            db, partner_auth.presented_from(request.headers), scope=partner_scopes.KNOWLEDGE)
        if status == partner_auth.NOT_READY:
            logging.error('knowledge_api: таблиц ключей нет — вход закрыт; схема не встала')
            return None, _refuse(503, 'KNOWLEDGE_API_NOT_CONFIGURED',
                                 'Доступ по ключу на сервере не настроен')
        if status == partner_auth.NO_SCOPE:
            logging.warning('knowledge_api: ключу «%s» (%s) не открыта ручка знаний, ip %s',
                            key['name'], key['mark'], _ip() or '-')
            return None, _refuse(403, 'KNOWLEDGE_API_FORBIDDEN',
                                 'Ключу не открыта ручка знаний')
        if status != partner_auth.OK:
            logging.warning('knowledge_api: ключ не принят (%s, ip %s)', status, _ip() or '-')
            return None, _refuse(401, 'KNOWLEDGE_API_UNAUTHORIZED', 'Ключ не принят')
        return key, None

    def _acts_as(key):
        return {'role': key.get('otp_role'), 'department_id': key.get('department_id'),
                'department': key.get('department_name'),
                'department_code': key.get('department_code')}

    @bp.route('/ping', methods=['GET'])
    def knowledge_ping():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        return jsonify({'status': 'ok', 'key': key['name'], 'acts_as': _acts_as(key),
                        'limits': {'per_minute': per_minute, 'max_question_chars': MAX_QUESTION_CHARS,
                                   'max_fragments': knowledge_search.MAX_LIMIT}})

    @bp.route('/parks', methods=['GET'])
    def knowledge_parks():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        try:
            with db._get_cursor() as cursor:
                ctx = perimeter.service_context(cursor, otp_role=key['otp_role'],
                                                department_id=key['department_id'])
                spaces = perimeter.spaces(cursor, ctx)
                parks = [park for park in knowledge_search.load_parks(cursor, spaces)
                         if park['status'] == 'active']
        except Exception:                                # noqa: BLE001
            logging.exception('knowledge_api: список парков не прочитан')
            return _refuse(500, 'KNOWLEDGE_API_FAILED', 'Внутренняя ошибка — повторите запрос позже')
        return jsonify({'parks': [{'id': park['id'], 'slug': park['slug'], 'name': park['name'],
                                   'city': park['city']} for park in parks]})

    # ── Вопрос ───────────────────────────────────────────────────────────────
    def _read_body():
        if request.content_length and request.content_length > MAX_BODY_BYTES:
            return None, 'Запрос слишком большой'
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return None, 'Тело запроса должно быть JSON-объектом'
        return body, None

    def _parse(body):
        """{park_id | park, question, limit} → (поля, ошибка)."""
        question = ' '.join(str(body.get('question') or '').split())
        if not question:
            return None, 'Нужен вопрос: поле question'
        if len(question) > MAX_QUESTION_CHARS:
            return None, 'Вопрос длиннее %d знаков' % MAX_QUESTION_CHARS
        park_id = body.get('park_id')
        park = body.get('park')
        if park_id is not None:
            if isinstance(park_id, bool) or not str(park_id).strip().isdigit():
                return None, 'park_id должен быть целым числом'
            park_id = int(str(park_id).strip())
        elif park is None or not str(park).strip():
            return None, 'Нужен таксопарк: поле park_id (число) или park (slug или название)'
        limit = body.get('limit', knowledge_search.DEFAULT_LIMIT)
        if isinstance(limit, bool) or not str(limit).strip().isdigit():
            return None, 'limit должен быть целым числом от 1 до %d' % knowledge_search.MAX_LIMIT
        limit = int(str(limit).strip())
        if not 1 <= limit <= knowledge_search.MAX_LIMIT:
            return None, 'limit должен быть целым числом от 1 до %d' % knowledge_search.MAX_LIMIT
        return {'question': question, 'park_id': park_id,
                'park': str(park).strip() if park is not None else None, 'limit': limit}, None

    def _journal(key, *, request_id, park, question, outcome, http_status, fragments=(),
                 latency_ms=None, degraded=False, error_text=None, escalation=None):
        """Запись о вызове + «Вопрос операторов», одной короткой транзакцией.

        Отказ записи не меняет ответ партнёру: ответ уже посчитан, а падение
        здесь — наша беда, не его. В лог — без текста вопроса."""
        question_id = None
        try:
            with db._get_cursor() as cursor:
                if escalation is not None:
                    try:
                        cursor.execute('SAVEPOINT knowledge_escalate')
                        question_id = run_escalate(cursor, **escalation)
                        cursor.execute('RELEASE SAVEPOINT knowledge_escalate')
                    except Exception:                    # noqa: BLE001
                        cursor.execute('ROLLBACK TO SAVEPOINT knowledge_escalate')
                        logging.exception('knowledge_api: вопрос не ушёл в «Вопросы операторов»')
                        question_id = None
                details = {
                    'park_id': park['id'] if park else None,
                    'park_name': park['name'] if park else None,
                    'fragments': knowledge_search.journal_fragments(fragments),
                    'fragments_count': len(fragments or ()),
                    'question_id': question_id,
                    'degraded': bool(degraded) or None,
                    'error_text': error_text,
                }
                partner_journal.insert(
                    cursor, key, scope=partner_scopes.KNOWLEDGE, endpoint=request.path,
                    outcome=outcome, http_status=http_status, latency_ms=latency_ms,
                    summary=question or None,
                    details={name: value for name, value in details.items() if value is not None},
                    ip=_ip(), user_agent=request.headers.get('User-Agent'), request_id=request_id)
        except Exception:                                # noqa: BLE001
            logging.exception('knowledge_api: вызов ключа %s не записан в журнал', key.get('mark'))
        return question_id

    @bp.route('/search', methods=['POST'])
    def knowledge_search_route():
        key, refusal = _authenticate()
        if refusal is not None:
            return refusal
        request_id = str(uuid.uuid4())
        started = time.monotonic()

        body, problem = _read_body()
        fields = None
        if body is not None:
            fields, problem = _parse(body)
        if problem:
            _journal(key, request_id=request_id, park=None,
                     question=str((body or {}).get('question') or '')[:MAX_QUESTION_CHARS]
                     if isinstance(body, dict) else '',
                     outcome='bad_request', http_status=400, error_text=problem)
            return _refuse(400, 'KNOWLEDGE_API_BAD_REQUEST', problem, request_id=request_id)

        allowed, retry_after = limiter.allow(key['id'])
        if not allowed:
            _journal(key, request_id=request_id, park=None, question=fields['question'],
                     outcome='limit', http_status=429, error_text='per_minute')
            response = _refuse(429, 'KNOWLEDGE_API_LIMIT',
                               'Слишком много вызовов за минуту — подождите', request_id=request_id,
                               retry_after=retry_after)
            response.headers['Retry-After'] = str(retry_after)
            return response

        if not slots.acquire(blocking=False):
            _journal(key, request_id=request_id, park=None, question=fields['question'],
                     outcome='limit', http_status=429, error_text='busy')
            response = _refuse(429, 'KNOWLEDGE_API_BUSY',
                               'Слишком много одновременных запросов — повторите через пару секунд',
                               request_id=request_id, retry_after=RETRY_AFTER_BUSY_SEC)
            response.headers['Retry-After'] = str(RETRY_AFTER_BUSY_SEC)
            return response
        try:
            return _answer(key, request_id, started, fields)
        finally:
            slots.release()

    def _answer(key, request_id, started, fields):
        question = fields['question']
        try:
            with db._get_cursor() as cursor:
                result = run_search(cursor, key=key, question=question, park_id=fields['park_id'],
                                    park=fields['park'], limit=fields['limit'])
        except Exception as error:                       # noqa: BLE001
            logging.exception('knowledge_api: поиск упал (ключ %s)', key.get('mark'))
            _journal(key, request_id=request_id, park=None, question=question, outcome='error',
                     http_status=500, latency_ms=_elapsed(started),
                     error_text=type(error).__name__)
            return _refuse(500, 'KNOWLEDGE_API_FAILED', 'Внутренняя ошибка — повторите запрос позже',
                           request_id=request_id)

        park = result.get('park')
        if result['status'] == 'park_not_found':
            _journal(key, request_id=request_id, park=None, question=question,
                     outcome='bad_request', http_status=404, latency_ms=_elapsed(started),
                     error_text='park_not_found')
            return _refuse(404, 'KNOWLEDGE_API_PARK_NOT_FOUND',
                           'Таксопарк не найден среди открытых ключу — список в GET /api/knowledge/parks',
                           request_id=request_id)
        if result['status'] == 'park_unavailable':
            _journal(key, request_id=request_id, park=park, question=question,
                     outcome='park_unavailable', http_status=403, latency_ms=_elapsed(started))
            return _refuse(403, 'KNOWLEDGE_API_PARK_UNAVAILABLE',
                           'Этот таксопарк в архиве — по нему не спрашивают', request_id=request_id,
                           park=result['park'])

        latency = _elapsed(started)
        escalation = None
        if result['status'] == 'no_data':
            escalation = {
                'key_id': key['id'], 'key_name': key['name'],
                'department_id': key.get('department_id'),
                'space_id': (result.get('perimeter') or {}).get('space_id'),
                'park_id': park['id'], 'park_name': park['name'], 'question': question,
            }
        question_id = _journal(key, request_id=request_id, park=park, question=question,
                               outcome=result['status'], http_status=200,
                               fragments=result['fragments'], latency_ms=latency,
                               degraded=result['degraded'], escalation=escalation)
        payload = {
            'status': result['status'],
            'request_id': request_id,
            'park': result['park'],
            'question': question,
            'fragments': result['fragments'],
            'notes': result['notes'],
            'degraded': result['degraded'],
            'perimeter': {'articles': (result.get('perimeter') or {}).get('articles')},
        }
        if result['status'] == 'no_data':
            payload['message'] = 'Данных нет'
            payload['question_logged'] = question_id is not None
        return jsonify(payload)

    def _elapsed(started):
        return int((time.monotonic() - started) * 1000)

    return bp
