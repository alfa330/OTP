# -*- coding: utf-8 -*-
"""HTTP «Табло ОП»: один снимок для всех зрителей.

    GET /api/op_wallboard/snapshot   снимок дня: итоги, часы, люди, разрезы по группам
    GET /api/op_wallboard/journal    журнал статусов одного сотрудника за сутки (?operator_id=)

Доступ — как у табло Тез КЦ и СЗоВ: глобальные админы, глава отдела продаж и его
супервайзеры. Граница отдела строгая. Зависимости приходят аргументами фабрики:
проверки прав, кэш снимков и каталог статусов живут в bot_schedule2, и обратный
импорт был бы циклом.

Момент ответа (SL, ожидание, разговор) берётся не из CDR, а из событий iCORE Phone за
сутки — см. `snapshot.attach_answer_moments` и докстринг `snapshot`.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from cdr import directory as directory_mod, queries
from . import snapshot as snapshot_mod

log = logging.getLogger(__name__)

DEPARTMENT_CODE = 'op'
_DEPARTMENT_CACHE_TTL = 600  # отделы меняются раз в никогда
_ALMATY = ZoneInfo('Asia/Almaty')


def load_phone_events(cursor, operator_ids, day, lookback_hours=0):
    """События телефонов операторов за сутки табло: {operator_id, event_at, status_key}.

    Только живые события iCORE Phone (`client_event_id IS NOT NULL`) — в той же таблице
    лежат сегменты ночных импортов, а они не про секунду ответа. Окно с запасом на
    рассинхрон часов до полуночи и на звонок, начатый в 23:59 и закончившийся после.
    Индекс (operator_id, event_at) есть; за сутки ОП это единицы тысяч строк.

    `lookback_hours` раздвигает окно назад: снимку нужен вечер прошлых суток, чтобы вход
    ночной смены лёг на её начало, а не на полночь (`snapshot.entry_moment`). Сопоставлению
    ответов лишние вечерние события не мешают — касания у него только из этих суток."""
    ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
    if not ids:
        return []
    day_start = datetime.combine(day, datetime.min.time())
    cursor.execute("""
        SELECT operator_id, event_at, status_key
          FROM operator_status_events
         WHERE operator_id = ANY(%s)
           AND client_event_id IS NOT NULL
           AND event_at >= %s AND event_at < %s
         ORDER BY event_at, id
    """, (ids, day_start - timedelta(minutes=1, hours=lookback_hours),
          day_start + timedelta(hours=25)))
    return [{'operator_id': row[0], 'event_at': row[1], 'status_key': row[2]}
            for row in cursor.fetchall()]


def load_journal_events(cursor, operator_id, day, lookback_hours=snapshot_mod.ENTRY_LOOKBACK_HOURS):
    """События телефона одного сотрудника для журнала: [(время, ключ)] с вечера прошлых суток.

    То же окно и тот же фильтр, что у снимка, — иначе вход в журнале и в столбце разошёлся бы."""
    day_start = datetime.combine(day, datetime.min.time())
    cursor.execute("""
        SELECT event_at, status_key
          FROM operator_status_events
         WHERE operator_id = %s
           AND client_event_id IS NOT NULL
           AND event_at >= %s AND event_at < %s
         ORDER BY event_at, id
    """, (int(operator_id), day_start - timedelta(minutes=1, hours=lookback_hours),
          day_start + timedelta(hours=25)))
    return [(row[0], row[1]) for row in cursor.fetchall()]


def load_group_memberships(cursor, operator_ids, day):
    """{operator_id: {group_id, group_name, model}} — группа сотрудника на сутки табло.

    Та же выборка членства, что у состава Тез КЦ (`get_tez_wallboard_operators`): действующее на
    дату, при пересечении — с самым поздним началом. Индекс (operator_id, start_date) есть."""
    ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
    if not ids:
        return {}
    cursor.execute("""
        SELECT DISTINCT ON (m.operator_id)
               m.operator_id, g.id, g.name, LOWER(COALESCE(g.calculation_model_code, ''))
          FROM group_operator_memberships m
          JOIN groups g ON g.id = m.group_id
         WHERE m.operator_id = ANY(%s)
           AND m.start_date <= %s
           AND (m.end_date IS NULL OR m.end_date >= %s)
         ORDER BY m.operator_id, m.start_date DESC, m.id DESC
    """, (ids, day, day))
    return {int(row[0]): {'group_id': int(row[1]), 'group_name': row[2] or '', 'model': row[3] or ''}
            for row in cursor.fetchall()}


def load_queue_answers(cursor, day, days=snapshot_mod.QUEUE_OWNER_LOOKBACK_DAYS):
    """Тройки (очередь, внутренний номер, принятых) за неделю — сырьё для
    `snapshot.queue_owner_groups`. Тот же проход по call_day, что у длин автоинформаторов."""
    cursor.execute("""
        SELECT queue, ext, count(*)
          FROM cdr_touches
         WHERE call_day BETWEEN %s AND %s
           AND call_type = %s
           AND talk_seconds > 0
           AND queue <> ''
           AND ext <> ''
         GROUP BY 1, 2
    """, (day - timedelta(days=days), day, snapshot_mod.touches_mod.TYPE_IN))
    return [(row[0], row[1], int(row[2])) for row in cursor.fetchall()]


ANNOUNCEMENT_LOOKBACK_DAYS = 7


def load_announcement_deltas(cursor, day, days=ANNOUNCEMENT_LOOKBACK_DAYS):
    """Тройки (очередь, started_at − приход, число строк) по входящим за последние сутки —
    сырьё для `snapshot.announcement_seconds_from_deltas`.

    `started_at` — наивное время Алматы, а EXTRACT(EPOCH) считает наивное время UTC, поэтому
    перед вычитанием epoch прихода из linkedid снимаем те же пять часов, что и везде в разделе
    (у Казахстана одна зона без перевода, см. cdr/queries.now_almaty)."""
    cursor.execute("""
        SELECT split_part(queue, ',', 1) AS q,
               (EXTRACT(EPOCH FROM started_at - interval '5 hours')::bigint
                    - split_part(linkedid, '.', 1)::bigint) AS delta,
               count(*)
          FROM cdr_touches
         WHERE call_day BETWEEN %s AND %s
           AND call_type LIKE 'Входящий%%'
           AND queue <> ''
           AND linkedid ~ '^[0-9]+\\.[0-9]+$'
         GROUP BY 1, 2
    """, (day - timedelta(days=days), day))
    return [(row[0], int(row[1]), int(row[2])) for row in cursor.fetchall()]


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

    def _is_talking(status_key):
        return status_entry(status_key)[1] == 'talking'

    # Длины автоинформаторов по очередям — константы станции, вычисленные из недели касаний.
    # Считать их на каждый снимок (раз в десять секунд) незачем: раз в час, и в тот же день.
    announce_cache = {'ts': 0.0, 'day': None, 'value': {}}

    def _announcement_seconds(cursor, day, now_ts):
        if announce_cache['day'] == day and now_ts - announce_cache['ts'] < 3600:
            return announce_cache['value']
        try:
            value = snapshot_mod.announcement_seconds_from_deltas(
                load_announcement_deltas(cursor, day))
        except Exception:  # noqa: BLE001
            # Без длин ожидание считается от начала строки — так было до этой правки;
            # снимок при этом целый.
            log.exception('%s: длины автоинформаторов не посчитались', label)
            value = announce_cache['value'] or {}
        announce_cache.update(ts=now_ts, day=day, value=value)
        return value

    def _phone_events(cursor, people, day, lookback_hours=0):
        operator_ids = [p['id'] for p in people if p.get('id') is not None]
        try:
            return load_phone_events(cursor, operator_ids, day, lookback_hours=lookback_hours)
        except Exception:  # noqa: BLE001
            # Без событий телефонов теряются только SL, ожидание, точный разговор и время
            # входа — снимок отдаёт их прочерком, а не уносит с собой всё табло.
            log.exception('%s: события iCORE Phone не прочитались', label)
            return []

    def _measured_touches(cursor, day, people, announce_seconds, phone_events=None):
        """Касания суток с входом в очередь и моментом ответа — общее для снимка и для
        итогов прошлых суток, чтобы отбивка в полночь считала ровно так же, как экран.
        События телефонов снимок читает сам (ему нужно окно шире) и передаёт сюда."""
        ext_by_operator = {p['id']: str(p['sip_number']) for p in people
                           if p.get('id') is not None and p.get('sip_number')}
        touches = queries.day_touches_compact(cursor, day)
        if phone_events is None:
            phone_events = _phone_events(cursor, people, day)
        touches = snapshot_mod.attach_queue_entry(touches, announce_seconds)
        return snapshot_mod.attach_answer_moments(touches, phone_events, ext_by_operator,
                                                  _is_talking)

    def _day_parts(day):
        """Итоги и разрез по часам за ЛЮБЫЕ сутки, мимо кэша снимка.

        Нужны отбивке в полночь: в 00:00 последний полный час (23:00–24:00) и итог дня
        относятся к закончившимся суткам, а снимок табло уже живёт новыми. Длины
        автоинформаторов считаются на те сутки заново и в часовой кэш снимка не пишутся."""
        dept = department_id()
        people = load_people(dept, day) if dept is not None else []
        with db._get_cursor() as cursor:
            try:
                announce_seconds = snapshot_mod.announcement_seconds_from_deltas(
                    load_announcement_deltas(cursor, day))
            except Exception:  # noqa: BLE001
                log.exception('%s: длины автоинформаторов за %s не посчитались', label, day)
                announce_seconds = {}
            touches = _measured_touches(cursor, day, people, announce_seconds)
        parts = snapshot_mod.aggregate(touches, sl_seconds)
        return {'day': day.isoformat(), 'totals': parts['totals'], 'hourly': parts['hourly']}

    # Кто какую очередь принимает — тоже свойство недели, а не десяти секунд: сырьё раз в час.
    # Хозяин очереди считается на каждый снимок заново — он зависит от состава групп.
    queue_answers_cache = {'ts': 0.0, 'day': None, 'value': []}

    def _queue_answers(cursor, day, now_ts):
        if queue_answers_cache['day'] == day and now_ts - queue_answers_cache['ts'] < 3600:
            return queue_answers_cache['value']
        try:
            value = load_queue_answers(cursor, day)
        except Exception:  # noqa: BLE001
            # Без хозяев очередей брошенные до звонка человеку видны только в «Все» —
            # остальное в разрезах по группам верно.
            log.exception('%s: очереди групп не посчитались', label)
            value = queue_answers_cache['value'] or []
        queue_answers_cache.update(ts=now_ts, day=day, value=value)
        return value

    def _memberships(cursor, people, day):
        try:
            return load_group_memberships(cursor, [p.get('id') for p in people], day)
        except Exception:  # noqa: BLE001
            # Без групп табло остаётся табло отдела: фильтр просто не появляется.
            log.exception('%s: группы сотрудников не прочитались', label)
            return {}

    def _fetch():
        now = datetime.now(_ALMATY).replace(tzinfo=None)
        day = now.date()
        dept = department_id()
        people = load_people(dept, day) if dept is not None else []
        with db._get_cursor() as cursor:
            memberships = _memberships(cursor, people, day)
            people = snapshot_mod.on_board(people, memberships)
            bridge_state = queries.agent_state(cursor)
            announce_seconds = _announcement_seconds(cursor, day, time.time())
            catalog = snapshot_mod.group_catalog(memberships)
            queue_owners = snapshot_mod.queue_owner_groups(
                _queue_answers(cursor, day, time.time()) if catalog else [],
                snapshot_mod.group_ext_map(people, memberships, catalog))
            phone_events = _phone_events(cursor, people, day,
                                         lookback_hours=snapshot_mod.ENTRY_LOOKBACK_HOURS)
            touches = _measured_touches(cursor, day, people, announce_seconds, phone_events)
        operator_ids = [p['id'] for p in people if p.get('id') is not None]
        try:
            statuses = live_statuses(operator_ids)
        except Exception:  # noqa: BLE001
            # Без статусов плитки дня остаются верными: список людей теряет разметку,
            # а не уносит с собой всё табло.
            log.exception('%s: статусы iCORE Phone не прочитались', label)
            statuses = {}
        return snapshot_mod.assemble(
            day=day, touches=touches, people=people, live_statuses=statuses,
            status_entry=status_entry, resolve_name=_resolve_name(),
            bridge_state=bridge_state, now=now, sl_seconds=sl_seconds,
            ar_min_percent=ar_min_percent, ar_max_percent=ar_max_percent,
            announce_seconds=announce_seconds, memberships=memberships,
            queue_owners=queue_owners, phone_events=phone_events)

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

    # Отбивка в Telegram берёт снимок отсюда же, чтобы картинка не расходилась с экраном,
    # а в полночь — итоги закончившихся суток тем же расчётом.
    bp.snapshot = _snapshot
    bp.day_parts = _day_parts

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

    @bp.route('/journal', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_journal():
        """Журнал статусов сотрудника за сутки: отрезки «статус, начало, конец, длительность».

        Без кэша: запрос точечный (один человек, индекс по operator_id и времени), а панель
        перечитывает журнал только когда в снимке у человека сменился статус. Открыть можно лишь
        того, кто стоит на табло, — граница отдела та же, что у снимка, и чужие события телефона
        через эту ручку не читаются."""
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        _, refusal = guard()
        if refusal is not None:
            return refusal
        try:
            operator_id = int(str(request.args.get('operator_id') or '').strip())
        except ValueError:
            return jsonify({"error": "Не указан сотрудник"}), 400
        try:
            payload = _snapshot()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)[:200]}), 503
        row = next((item for item in payload.get('operators') or []
                    if item.get('id') == operator_id), None)
        if row is None:
            return jsonify({"error": "Сотрудника нет на табло"}), 404
        now = datetime.now(_ALMATY).replace(tzinfo=None)
        day = now.date()
        try:
            with db._get_cursor() as cursor:
                events = load_journal_events(cursor, operator_id, day)
        except Exception:  # noqa: BLE001
            log.exception('%s: журнал статусов %s не прочитался', label, operator_id)
            return jsonify({"error": "Журнал статусов сейчас недоступен"}), 503
        entry, segments = snapshot_mod.status_journal(events, day, now, status_entry)
        return jsonify({
            'operator_id': operator_id,
            'name': row.get('name') or '',
            'day': day.isoformat(),
            'captured_at': now.strftime('%Y-%m-%dT%H:%M:%S'),
            'entry_at': entry.strftime('%Y-%m-%dT%H:%M:%S') if entry else None,
            'segments': segments,
        })

    return bp
