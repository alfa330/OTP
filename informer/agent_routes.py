# -*- coding: utf-8 -*-
"""Ручки диспетчера автоинформатора на шлюзе (cdr_bridge/informer.py).

Без логина портала: диспетчер доказывает, что он шлюз, подписью Ed25519 — тем же ключом и тем же
протоколом, что мост «Касаний» (cdr/agent_auth.py, открытые ключи — CDR_AGENT_KEYS). Общего
токена здесь нет вовсе: новый канал сразу живёт по ключам.

    POST /api/informer/agent/poll    {agent_id, version, ami_ok, error, lines: {линия: {capacity, active}}}
                                     → {calls: [{call_id, campaign_id, phone, line, audio_sha256, audio_ms}],
                                        next_poll_sec}
    POST /api/informer/agent/audio   {campaign_id} → {sha256, wav_base64}
    POST /api/informer/agent/result  {call_id, result, dialed_at, answered_at, ended_at, ring_ms,
                                      listened_ms, played_to_end, hangup_by, hangup_cause, dtmf, details}
"""
import base64
import logging
from functools import wraps

from flask import Blueprint, jsonify, request

from cdr import agent_auth
from cdr import config as cdr_config

from .service import InformerError

log = logging.getLogger(__name__)


def build_informer_agent_blueprint(*, service, build_cors_preflight_response):
    bp = Blueprint('informer_agent', __name__)

    def _authenticate():
        """(идентификатор ключа | None, готовый отказ | None). Причина отказа — в журнал, не наружу."""
        raw_keys = cdr_config.agent_keys_raw()
        if not raw_keys:
            return None, (jsonify({"error": "Шлюз не настроен на сервере: нет открытых ключей"}), 503)
        try:
            keys = agent_auth.parse_public_keys(raw_keys)
        except ValueError as exc:
            log.error('Автоинформатор: CDR_AGENT_KEYS задан с ошибкой: %s', exc)
            return None, (jsonify({"error": "Шлюз не настроен на сервере: ключи заданы с ошибкой"}), 503)
        kid, reason = agent_auth.verify(request.headers, request.method, request.path,
                                        request.get_data(cache=True), keys)
        if kid is None:
            log.warning('Автоинформатор: диспетчер не авторизован — %s', reason)
            return None, (jsonify({"error": "Шлюз не авторизован"}), 401)
        return kid, None

    def agent_route(rule):
        def decorator(handler):
            @bp.route(rule, methods=['POST', 'OPTIONS'], endpoint='informer_agent_' + handler.__name__)
            @wraps(handler)
            def wrapper():
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                key_id, refusal = _authenticate()
                if refusal is not None:
                    return refusal
                try:
                    payload = request.get_json(silent=True) or {}
                    return handler(payload, key_id)
                except InformerError as exc:
                    return jsonify({"error": str(exc)}), exc.status
                except Exception as exc:  # noqa: BLE001
                    log.exception('Автоинформатор: ошибка в ручке диспетчера %s', rule)
                    return jsonify({"error": "Внутренняя ошибка", "detail": str(exc)[:200]}), 500
            return wrapper
        return decorator

    @agent_route('/api/informer/agent/poll')
    def poll(payload, key_id):
        return jsonify({"status": "success", **service.agent_poll(payload, key_id)}), 200

    @agent_route('/api/informer/agent/audio')
    def audio(payload, key_id):
        sha, data = service.agent_audio(payload.get('campaign_id'))
        return jsonify({"status": "success", "sha256": sha,
                        "wav_base64": base64.b64encode(data).decode('ascii')}), 200

    @agent_route('/api/informer/agent/result')
    def result(payload, key_id):
        return jsonify({"status": "success", **service.agent_result(payload)}), 200

    return bp
