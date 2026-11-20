"""Приёмник вебхуков чатов Syntony (Flask Blueprint /api/syntony/chats).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2 сам
подключает этот модуль, обратный импорт был бы циклом (как у syntony_window/).

Ручки:
    POST /webhook   события Syntony: message.created, message.status, webhook.test (§9)
    GET  /webhook   «адрес живой»: {"ok": true, "configured": …} — для того, кто открыл
                    адрес в браузере; секретов и данных не показывает

ОТВЕТЫ ВЫБРАНЫ ПОД ПРАВИЛА ПОВТОРОВ SYNTONY (§9.5): 2xx — доставлено; 5xx — повторит
(30 с, 2 мин, 10 мин, 30 мин, 1 ч, не дольше суток), а следующие события вебхука подождут;
3xx и прочие 4xx — не повторит, событие ляжет в «неудачные» (их можно отправить снова
кнопкой «Повторить неудачные»).
    * секрета в окружении нет / база не ответила        → 503: событие не потеряется
    * подпись не сошлась, время подписи старше 5 минут  → 403: чужой запрос, повторять нечего
      (не 401: общий хук приложения пишет ERROR на каждый 401, а перепутанный секрет —
      это тысяча событий в час)
    * тело не разобрать, ошибка данных при записи       → 400 / 422: повтор дал бы то же
      самое и держал бы очередь вебхука до суток
    * незнакомое событие                                → 200: новые события добавляются
      в рамках версии 1, отказ копил бы «неудачные»
Ответ — за десятки миллисекунд: Syntony ждёт 10 секунд, дальше считает таймаутом.

В журнал — коды и id, без номеров, имён и текстов: тело события — переписка с клиентами.
"""

import logging
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from . import rules, store

logger = logging.getLogger(__name__)

_LOG_EVERY_SECONDS = 600.0


class _Throttle:
    """Одна строка журнала на причину раз в 10 минут: Syntony повторяет события, чужие
    запросы могут идти потоком — журнал от этого не должен расти."""

    def __init__(self, clock):
        self._clock = clock
        self._last = {}
        self._lock = threading.Lock()

    def due(self, key):
        now = self._clock()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < _LOG_EVERY_SECONDS:
                return False
            self._last[key] = now
            return True


def _is_data_error(exc):
    """Повтор того же события упадёт так же: ошибка данных в базе (класс 22), ограничения
    (23) или значение, которое драйвер не смог передать вовсе (ValueError/UnicodeError
    psycopg2 бросает до базы — без pgcode)."""
    code = str(getattr(exc, 'pgcode', '') or '')
    if code:
        return code[:2] in ('22', '23')
    return isinstance(exc, (ValueError, UnicodeError))


def build_syntony_chats_blueprint(*, db, environ=None, clock=time.time, monotonic=time.monotonic):
    blueprint = Blueprint('syntony_chats', __name__, url_prefix='/api/syntony/chats')
    throttle = _Throttle(monotonic)
    seen_first = {}

    def _refuse(code, status):
        return jsonify({'ok': False, 'error': code}), status

    @blueprint.route('/webhook', methods=['GET'])
    def webhook_info():
        return jsonify({'ok': True, 'service': 'syntony-chats-webhook',
                        'configured': bool(rules.load_secrets(environ))}), 200

    @blueprint.route('/webhook', methods=['POST'])
    def webhook():
        secrets = rules.load_secrets(environ)
        if not secrets:
            if throttle.due('not_configured'):
                logger.warning('Syntony, вебхук чатов: событие пришло, а секрета подписи нет '
                               '(%s) — отвечаем 503, Syntony повторит', rules.ENV_SECRET)
            return _refuse('not_configured', 503)

        length = request.content_length
        if length is not None and length > rules.MAX_BODY_BYTES:
            return _refuse('too_large', 413)
        raw_body = request.get_data(cache=True)
        if len(raw_body) > rules.MAX_BODY_BYTES:
            return _refuse('too_large', 413)

        refusal = rules.verify_signature(raw_body, request.headers, secrets, clock())
        if refusal:
            if throttle.due('signature:' + refusal):
                logger.warning('Syntony, вебхук чатов: отказ %s (подпись %s знаков, время подписи '
                               '«%s»)', refusal,
                               len(request.headers.get(rules.SIGNATURE_HEADER) or ''),
                               (request.headers.get(rules.TIMESTAMP_HEADER) or '')[:20])
            return _refuse(refusal, 403)

        try:
            event = rules.parse_event(raw_body)
        except rules.PayloadError as exc:
            logger.warning('Syntony, вебхук чатов: тело не разобрано (%s)', exc.code)
            return _refuse(exc.code, 400)

        kind = event['event']
        if kind == rules.EVENT_TEST:
            logger.info('Syntony, вебхук чатов: проверочное событие принято (вебхук %s)',
                        event['webhook_id'] or '-')
            return jsonify({'ok': True, 'event': kind}), 200
        if kind == rules.EVENT_MESSAGE_CREATED:
            apply = store.apply_message_created
        elif kind == rules.EVENT_MESSAGE_STATUS:
            apply = store.apply_message_status
        else:
            if throttle.due('unknown:' + kind[:60]):
                logger.info('Syntony, вебхук чатов: незнакомое событие %s — принято без записи',
                            kind[:60])
            return jsonify({'ok': True, 'event': kind, 'ignored': True}), 200

        # Время события — момент снимка: им повтор старого события не перепишет новое.
        occurred_at = event['occurred_at'] or datetime.fromtimestamp(clock(), tz=timezone.utc)
        try:
            with db._get_cursor() as cursor:
                outcome = apply(cursor, event['data'], occurred_at)
        except rules.PayloadError as exc:
            logger.warning('Syntony, вебхук чатов: %s без нужного поля (%s), событие %s',
                           kind, exc.code, event['event_id'])
            return _refuse(exc.code, 400)
        except Exception as exc:  # noqa: BLE001 — любой сбой записи: решаем, повторять ли
            # Текст ошибки драйвера может нести значения строки — в журнал только класс и код.
            if _is_data_error(exc):
                logger.error('Syntony, вебхук чатов: %s не записано — ошибка данных %s (%s), '
                             'событие %s', kind, type(exc).__name__,
                             getattr(exc, 'pgcode', ''), event['event_id'])
                return _refuse('unprocessable', 422)
            logger.error('Syntony, вебхук чатов: база не ответила (%s) — 503, Syntony повторит',
                         type(exc).__name__)
            return _refuse('storage_unavailable', 503)

        if kind not in seen_first:
            seen_first[kind] = True
            logger.info('Syntony, вебхук чатов: первое событие %s с запуска записано (%s)',
                        kind, outcome)
        return jsonify({'ok': True, 'event': kind}), 200

    return blueprint
