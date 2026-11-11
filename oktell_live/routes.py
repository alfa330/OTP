# -*- coding: utf-8 -*-
"""Ручка коннектора Oktell (cdr_bridge/oktell_connector.py на VM шлюза).

Без логина портала: коннектор доказывает, что он шлюз, подписью Ed25519 — тем же ключом и тем
же протоколом, что мост «Касаний» и коннектор АТС (cdr/agent_auth.py, открытые ключи —
CDR_AGENT_KEYS). Общего токена нет вовсе.

    POST /api/oktell_live/agent/push
        {agent_id, hostname, version,
         db: {host, connected, error},
         wallboard: null | {rows: [строки запроса], params: {sl_seconds, test_keys},
                            taken_at, query_ms}}
      → {status, accepted, reason, params: {sl_seconds, test_keys}, interval_seconds}

Коннектор зовёт ручку раз в такт. Без `wallboard` — это «я на связи, какие параметры»: так он
начинает работу и так сообщает, что база Oktell не ответила. В ответе всегда текущие параметры
снимка (порог SL и ключи «Реестра тестовых номеров») и такт. Снимок, снятый с другими
параметрами, не принимается (`accepted: false, reason: 'params'`) — коннектор переснимает его
сразу: иначе после правки реестра табло один такт считало бы тестовые звонки.

Что закреплено здесь, а не в коннекторе: от строк остаются только известные колонки запроса и
простые значения (store.clean_rows), так что ничего, кроме чисел и коротких строк, в снимок
табло не попадёт, что бы ни прислали.

    POST /api/oktell_live/agent/jobs
        {agent_id, hostname, version, worker, catalog, datasets: [имена], unreadable: [таблицы],
         done: [{id, ok: true, rows, query_ms} | {id, ok: false, reason, error}], wait}
      → {status, jobs: [{id, dataset, params, digest}], delivered, catalog, wait_seconds}

Исполнители запросов коннектора: остальные запросы портала к базе Oktell (отбивка, перерывы,
выгрузки статусов и звонков, биллинг, выборка звонков на оценку) — очередь в `jobs.py`. Ручка
держит запрос, пока не появится задание (длинный опрос); в задании имя набора каталога и
параметры, а не SQL.
"""

import logging

from flask import Blueprint, jsonify, request

from cdr import agent_auth
from cdr import config as cdr_config
from cdr import oktell_datasets
from cdr import oktell_wallboard

from . import jobs as jobs_mod
from . import store as store_mod

log = logging.getLogger(__name__)


def build_oktell_live_blueprint(*, build_cors_preflight_response, wallboard_params, store=None,
                                job_queue=None):
    """wallboard_params() → {'sl_seconds': …, 'test_keys': […]} — параметры снимка табло, какими
    их сейчас считает портал (порог SL и реестр тестовых номеров)."""
    bp = Blueprint('oktell_live', __name__, url_prefix='/api/oktell_live')
    live_store = store or store_mod.store
    job_queue = job_queue or jobs_mod.jobs

    def _authenticate():
        """(идентификатор ключа | None, готовый отказ | None). Причина отказа — в журнал, не наружу."""
        raw_keys = cdr_config.agent_keys_raw()
        if not raw_keys:
            return None, (jsonify({"error": "Шлюз не настроен на сервере: нет открытых ключей"}), 503)
        try:
            keys = agent_auth.parse_public_keys(raw_keys)
        except ValueError as exc:
            log.error('Коннектор Oktell: CDR_AGENT_KEYS задан с ошибкой: %s', exc)
            return None, (jsonify({"error": "Шлюз не настроен на сервере: ключи заданы с ошибкой"}), 503)
        kid, reason = agent_auth.verify(request.headers, request.method, request.path,
                                        request.get_data(cache=True), keys)
        if kid is None:
            log.warning('Коннектор Oktell: не авторизован — %s', reason)
            return None, (jsonify({"error": "Шлюз не авторизован"}), 401)
        return kid, None

    @bp.route('/agent/push', methods=['POST', 'OPTIONS'])
    def agent_push():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        key_id, refusal = _authenticate()
        if refusal is not None:
            return refusal
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "Тело запроса — объект JSON"}), 400
            params = oktell_wallboard.clean_params(wallboard_params())
            accepted, reason = False, None
            snapshot = payload.get('wallboard')
            if snapshot is not None:
                if not isinstance(snapshot, dict):
                    return jsonify({"error": "Поле wallboard — объект снимка или null"}), 400
                try:
                    rows = store_mod.clean_rows(snapshot.get('rows'))
                except ValueError as exc:
                    log.warning('Коннектор Oktell: снимок не принят — %s', exc)
                    return jsonify({"error": str(exc)}), 400
                if oktell_wallboard.same_params(snapshot.get('params'), params):
                    live_store.put_wallboard(rows, params, taken_at=snapshot.get('taken_at'),
                                             query_ms=snapshot.get('query_ms'))
                    accepted = True
                else:
                    reason = 'params'
            live_store.agent_seen(hostname=payload.get('hostname'), version=payload.get('version'),
                                  key_id=key_id, db=payload.get('db'))
            return jsonify({'status': 'ok', 'accepted': accepted, 'reason': reason,
                            'params': params, 'interval_seconds': store_mod.interval_seconds()})
        except Exception as exc:  # noqa: BLE001
            log.exception('Коннектор Oktell: ошибка в ручке приёма')
            return jsonify({"error": "Внутренняя ошибка", "detail": str(exc)[:200]}), 500

    @bp.route('/agent/jobs', methods=['POST', 'OPTIONS'])
    def agent_jobs():
        """Исполнитель запросов: приносит результаты прошлых заданий и ждёт следующее.

        Длинный опрос: ответ приходит сразу, как только портал кладёт задание, а без заданий —
        через `wait_seconds`. В задании — имя набора каталога, параметры и отпечаток текста;
        самого SQL в нём нет (cdr/oktell_datasets.py)."""
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        key_id, refusal = _authenticate()
        if refusal is not None:
            return refusal
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "Тело запроса — объект JSON"}), 400
            worker = '%s#%s' % (str(payload.get('agent_id') or payload.get('hostname') or key_id or '')[:100],
                                str(payload.get('worker') or 0)[:8])
            delivered = job_queue.finish(payload.get('done'))
            job_queue.worker_seen(worker, version=payload.get('version'), catalog=payload.get('catalog'),
                                  datasets=payload.get('datasets'), unreadable=payload.get('unreadable'))
            try:
                wait = float(payload.get('wait') or 0)
            except (TypeError, ValueError):
                wait = 0.0
            taken = job_queue.take(worker, wait)
            return jsonify({'status': 'ok', 'jobs': taken, 'delivered': delivered,
                            'catalog': oktell_datasets.fingerprint(),
                            'wait_seconds': jobs_mod.POLL_WAIT_SECONDS})
        except Exception as exc:  # noqa: BLE001
            log.exception('Коннектор Oktell: ошибка в ручке заданий')
            return jsonify({"error": "Внутренняя ошибка", "detail": str(exc)[:200]}), 500

    return bp
