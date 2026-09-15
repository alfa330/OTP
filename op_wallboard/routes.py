# -*- coding: utf-8 -*-
"""HTTP «Табло ОП»: один снимок для всех зрителей.

    GET /api/op_wallboard/snapshot   снимок дня: итоги, линии, часы, люди

Доступ — как у табло Тез КЦ и СЗоВ: глобальные админы, глава отдела продаж и его
супервайзеры. Граница отдела строгая. Зависимости приходят аргументами фабрики:
проверки прав, кэш снимков и каталог статусов живут в bot_schedule2, и обратный
импорт был бы циклом.
"""

import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from cdr import directory as directory_mod, queries
from . import snapshot as snapshot_mod

log = logging.getLogger(__name__)

DEPARTMENT_CODE = 'op'
_DEPARTMENT_CACHE_TTL = 600  # отделы меняются раз в никогда
_ALMATY = ZoneInfo('Asia/Almaty')


def make_department_resolver(db, code=DEPARTMENT_CODE):
    """id отдела по коду, с кэшем. Хардкодить id нельзя — он засеян, а не задан."""
    cache = {'ts': 0.0, 'id': None}

    def resolve():
        now = time.time()
        if cache['id'] is not None and now - cache['ts'] < _DEPARTMENT_CACHE_TTL:
            return cache['id']
        found = None
        for dept in (db.get_departments() or []):
            if str(dept.get('code') or '').strip().lower() == code:
                found = int(dept['id'])
                break
        cache.update(ts=now, id=found)
        return found
    return resolve


def make_guard(*, db, department_id, get_authenticated_requester, normalize_user_role,
               is_global_admin_requester, headed_department_id, is_supervisor_role):
    """(requester_id, отказ|None) — тот же порядок проверок, что у _tez_wallboard_guard.

    Глава отдела с базовой ролью admin — не глобальный админ (у _is_global_admin_requester
    для него False именно потому, что он возглавляет отдел), поэтому ветка «глава» стоит
    отдельно; без неё руководитель получил бы 403 на собственном табло."""
    def guard():
        requester_id, requester, auth_error = get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return None, (jsonify({"error": message}), status_code)
        role = normalize_user_role(requester[3])
        if is_global_admin_requester(role, requester_id):
            return requester_id, None
        dept = department_id()
        if dept is not None:
            if headed_department_id(requester_id) == dept:
                return requester_id, None
            if is_supervisor_role(role) and db.get_user_department_id(requester_id) == dept:
                return requester_id, None
        return requester_id, (jsonify({"error": "forbidden"}), 403)
    return guard


def build_op_wallboard_blueprint(*, db, require_api_key, build_cors_preflight_response, guard,
                                 department_id, snapshot_with_cache, restore_cache, persist_cache,
                                 status_entry, load_people, live_statuses,
                                 ttl_seconds=10, stale_max_seconds=600, retry_after_seconds=30,
                                 lock_wait_seconds=3, persist_interval_seconds=60,
                                 sl_seconds=snapshot_mod.DEFAULT_SL_SECONDS,
                                 ar_min_percent=snapshot_mod.DEFAULT_AR_MIN_PERCENT,
                                 ar_max_percent=snapshot_mod.DEFAULT_AR_MAX_PERCENT):
    bp = Blueprint('op_wallboard', __name__, url_prefix='/api/op_wallboard')
    cache = {'ts': 0.0, 'payload': None}
    lock = threading.Lock()
    label = 'Табло ОП'

    def _resolve_name():
        """ФИО по внутреннему номеру — из справочника раздела «Касания». Справочник
        собирает и обновляет раздел; здесь только чтение того, что есть."""
        with db._get_cursor() as cursor:
            stored = queries.load_directory(cursor)
        if not stored:
            return lambda ext: ''
        resolve = directory_mod.resolver(stored)
        return lambda ext: resolve(ext, datetime.now(_ALMATY).strftime('%Y-%m-%d'))[0]

    def _fetch():
        now = datetime.now(_ALMATY).replace(tzinfo=None)
        day = now.date()
        with db._get_cursor() as cursor:
            touches = queries.day_touches_compact(cursor, day)
            bridge_state = queries.agent_state(cursor)
        dept = department_id()
        people = load_people(dept, day) if dept is not None else []
        try:
            statuses = live_statuses([p['id'] for p in people if p.get('id') is not None])
        except Exception:  # noqa: BLE001
            # Без статусов плитки дня остаются верными: список людей теряет разметку,
            # а не уносит с собой всё табло.
            log.exception('%s: статусы iCORE Phone не прочитались', label)
            statuses = {}
        return snapshot_mod.assemble(
            day=day, touches=touches, people=people, live_statuses=statuses,
            status_entry=status_entry, resolve_name=_resolve_name(),
            bridge_state=bridge_state, now=now, sl_seconds=sl_seconds,
            ar_min_percent=ar_min_percent, ar_max_percent=ar_max_percent)

    def _snapshot():
        """Снимок из общего кэша — один на всех зрителей и на отбивку. Бросает, если
        данных нет вовсе: ни свежих, ни сохранённых."""
        return snapshot_with_cache(
            cache=cache, lock=lock, fetch=_fetch, source='мост «Касаний»',
            ttl=ttl_seconds, stale_max=stale_max_seconds, retry_after=retry_after_seconds,
            lock_wait=lock_wait_seconds, label=label,
            before=lambda: restore_cache(cache, 'op', stale_max_seconds, label),
            after=lambda data, at: persist_cache(cache, 'op', persist_interval_seconds,
                                                 data, at, label))

    # Отбивка в Telegram берёт снимок отсюда же, чтобы картинка не расходилась с экраном.
    bp.snapshot = _snapshot

    @bp.route('/snapshot', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_snapshot():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        _, refusal = guard()
        if refusal is not None:
            return refusal
        try:
            payload = _snapshot()
        except Exception as exc:  # noqa: BLE001
            # Данных нет вовсе (ни свежих, ни сохранённых): экран говорит это словами,
            # а не пятисоткой — на стене разница между «портал упал» и «касаний ещё нет».
            log.warning('%s: снимок недоступен: %s', label, exc)
            return jsonify({"error": str(exc)[:200]}), 503
        return jsonify(payload)

    return bp
