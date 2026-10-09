# -*- coding: utf-8 -*-
"""HTTP «Табло ОП»: один снимок для всех зрителей.

    GET /api/op_wallboard/snapshot       снимок дня: итоги, часы, люди, разрезы по группам
    GET /api/op_wallboard/journal        журнал статусов одного сотрудника за сутки (?operator_id=)
    GET /api/op_wallboard/chat_snapshot  направление «Чат»: чаты верификаторов в Wazzup (#367)
    GET /api/op_wallboard/chat_export    то же за период файлом .xlsx (?date_from=&date_to=)

Доступ — как у табло Тез КЦ и СЗоВ: глобальные админы, глава отдела продаж и его
супервайзеры. Граница отдела строгая. Зависимости приходят аргументами фабрики:
проверки прав, кэш снимков и каталог статусов живут в bot_schedule2, и обратный
импорт был бы циклом.

Момент ответа (SL, ожидание, разговор) берётся не из CDR, а из событий iCORE Phone за
сутки — см. `snapshot.attach_answer_moments` и докстринг `snapshot`. «Принятие лида в
работу» — из снимка сделок «Воронки ОП» и звонков по их телефонам, см. `lead_speed`.
"""

import logging
import threading
import time
from io import BytesIO
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request, send_file

from cdr import directory as directory_mod, leads as cdr_leads_mod, queries, touches as touches_mod
from test_numbers import keys as test_keys
from . import (chat as chat_mod, chat_export as chat_export_mod, lead_speed as lead_speed_mod,
               snapshot as snapshot_mod)

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
           AND """ + test_keys.sql_not_test('cdr_touches.phone', digits=True) + """
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
           AND """ + test_keys.sql_not_test('cdr_touches.phone', digits=True) + """
         GROUP BY 1, 2
    """, (day - timedelta(days=days), day))
    return [(row[0], int(row[1]), int(row[2])) for row in cursor.fetchall()]


def load_lead_deals(cursor, day, direction=lead_speed_mod.LEAD_DIRECTION,
                    source=lead_speed_mod.LEAD_SOURCE):
    """Сделки «Основы» из снимка «Воронки ОП», созданные в эти сутки: ключ, создание, телефоны.

    Сутки сделки amoCRM в снимке — дата её создания (`op_funnel.sources.amo_rows`), поэтому
    `work_day` отбирает ровно созданные сегодня, по индексу (direction_code, work_day). Около
    тысячи строк в сутки.

    Сделка, все номера которой — из реестра тестовых (test_numbers), в плитку не идёт:
    её завёл робот пропущенных по тестовому звонку или сам тестировщик."""
    cursor.execute("""
        SELECT lead_key, created_at, phones, phone
          FROM op_funnel_leads
         WHERE direction_code = %s AND source = %s AND work_day = %s
           AND created_at IS NOT NULL
    """, (direction, source, day))
    deals = [{'lead_key': row[0], 'created_at': row[1], 'phones': row[2] or '', 'phone': row[3] or ''}
             for row in cursor.fetchall()]
    test_numbers = test_keys.load_keys(cursor)
    return [deal for deal in deals
            if not test_keys.all_test(cdr_leads_mod.lead_phones(deal), test_numbers)]


def load_lead_touches(cursor, day, grace=cdr_leads_mod.GRACE):
    """Исходящие и принятые входящие суток — сырьё `lead_speed.take_speed`.

    С хвостом прошлых суток длиной в допуск окна сделки: сделка, заведённая в 00:01, владеет
    звонком 23:59 — так же читает и режим «Сделки» (`cdr.lead_queries.TOUCH_LOOKBACK_DAYS`).
    Без фильтра по телефонам: тысяча номеров массивом стоит базе дороже, чем две тысячи строк
    суток по индексу call_day, а отбор по телефонам сделок — дело памяти."""
    day_start = datetime.combine(day, datetime.min.time())
    cursor.execute("""
        SELECT started_at, phone, ext, call_type, talk_seconds, dial_seconds, queue, recording_url
          FROM cdr_touches
         WHERE call_day BETWEEN %s AND %s
           AND started_at >= %s
           AND (call_type = %s OR (call_type = %s AND talk_seconds > 0))
           AND """ + test_keys.sql_not_test('cdr_touches.phone', digits=True) + """
    """, (day - timedelta(days=1), day, day_start - grace, touches_mod.TYPE_OUT, touches_mod.TYPE_IN))
    return [{'started_at': row[0], 'phone': row[1] or '', 'ext': row[2] or '', 'call_type': row[3],
             'talk_seconds': int(row[4] or 0), 'dial_seconds': int(row[5] or 0),
             'queue': row[6] or '', 'recording_url': row[7] or ''}
            for row in cursor.fetchall()]


def load_lead_sync_age(cursor, day, direction=lead_speed_mod.LEAD_DIRECTION,
                       source=lead_speed_mod.LEAD_SOURCE):
    """Сколько секунд назад сделки этих суток последний раз перечитались удачно; None — ни разу.

    Только прогоны, чей период накрывает сутки: инкремент пишет период «сегодня», а ночная
    выгрузка (вчера и позавчера) и догрузка прошлых дней из «Касаний» сегодняшних сделок не
    трогают — с ними замерший список выглядел бы свежим. Возраст считает база: метки журнала —
    её NOW(), то есть UTC без пояса, а у процесса часы Алматы, и разность «своё минус чужое»
    промахнулась бы на пять часов."""
    cursor.execute("""
        SELECT EXTRACT(EPOCH FROM (NOW() - MAX(finished_at)))
          FROM op_funnel_sync_runs
         WHERE direction_code = %s AND source = %s AND status = 'ok'
           AND period_from <= %s AND period_to >= %s
    """, (direction, source, day, day))
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


# Аккаунт Wazzup верификаторов. «Поток» (второй аккаунт) на табло не идёт — решение владельца
# 25.09.2026, да и приходит он забором раз в десять минут, а не вебхуком.
CHAT_ACCOUNT = 'op'


def load_chat_messages(cursor, since, until, account=CHAT_ACCOUNT):
    """Сообщения аккаунта за [since, until) — сырьё `chat.classify`.

    Границы — наивное время Алматы; `dt` в базе — timestamptz, поэтому границы переводим тем же
    `AT TIME ZONE`, что и аналитика «Чатов ОП» (иначе сутки съезжают на пять часов). Автор
    исходящего — через карту Wazzup, бот там помечен. Индекс (account, dt) есть; за сутки
    аккаунта это около шести тысяч строк.

    Без ORDER BY намеренно: порядок наводит `chat.split_episodes`, а сортировка недели в базе
    на 256 МБ уходила на диск (замер 25.09.2026: external merge 5 МБ)."""
    cursor.execute("""
        SELECT w.channel_id, w.chat_id, (w.dt AT TIME ZONE 'Asia/Almaty'), w.is_echo,
               map.user_id, COALESCE(map.is_bot, FALSE), w.message_id, w.author_id, w.type
          FROM wazzup_messages w
          LEFT JOIN wazzup_operator_map map ON map.author_id = w.author_id
         WHERE w.account = %s
           AND NOT w.is_deleted
           AND w.dt >= (%s::timestamp AT TIME ZONE 'Asia/Almaty')
           AND w.dt < (%s::timestamp AT TIME ZONE 'Asia/Almaty')
           AND """ + test_keys.sql_not_test(test_keys.wazzup_phone_sql('w'), digits=True) + """
    """, (account, since, until))
    return [{'channel_id': row[0], 'chat_id': row[1], 'at': row[2], 'is_echo': bool(row[3]),
             'user_id': row[4], 'is_bot': bool(row[5]), 'message_id': row[6],
             'author_id': row[7], 'type': row[8]}
            for row in cursor.fetchall()]


def load_chat_last_message_at(cursor, account=CHAT_ACCOUNT):
    """Время последнего сообщения аккаунта (наивное, Алматы) — жив ли поток вебхука.

    Любого сообщения, включая рассылки: вопрос здесь о доставке, а не о работе людей. MAX(dt)
    идёт обратным проходом по индексу (account, dt)."""
    cursor.execute("""
        SELECT MAX(dt) AT TIME ZONE 'Asia/Almaty' FROM wazzup_messages WHERE account = %s
    """, (account,))
    row = cursor.fetchone()
    return row[0] if row else None


def load_chat_verifiers(cursor, day_from, day_to=None):
    """{user_id: ФИО} — верификаторы: состоят в группе модели op_verificator.

    На одни сутки — действующее членство на дату (при пересечении — с самым поздним началом),
    та же выборка, что у групп телефонного табло. На период — все, кто был верификатором хоть
    день; по каким именно дням — `load_chat_verifier_days`."""
    return load_chat_verifier_days(cursor, day_from, day_to)[0]


def load_chat_verifier_days(cursor, day_from, day_to=None):
    """({user_id: ФИО}, {дата: множество id}) — верификаторы периода и состав на каждую дату.

    По дням, а не «хоть день периода»: сотрудник, перешедший в верификаторы 10-го, в выгрузке за
    месяц до 10-го остаётся сотрудником своей прежней группы — иначе один и тот же день давал бы
    разные цифры на стене и в файле за разные периоды."""
    day_to = day_to or day_from
    cursor.execute("""
        SELECT DISTINCT ON (m.operator_id, d.day) m.operator_id, u.name, d.day,
               LOWER(COALESCE(g.calculation_model_code, ''))
          FROM generate_series(%s::date, %s::date, interval '1 day') AS d(day)
          JOIN group_operator_memberships m
            ON m.start_date <= d.day::date AND (m.end_date IS NULL OR m.end_date >= d.day::date)
          JOIN groups g ON g.id = m.group_id
          JOIN users u ON u.id = m.operator_id
         ORDER BY m.operator_id, d.day, m.start_date DESC, m.id DESC
    """, (day_from, day_to))
    names, by_day = {}, {}
    for operator_id, name, day, model in cursor.fetchall():
        if model != chat_mod.VERIFIER_GROUP_MODEL:
            continue
        names[int(operator_id)] = name or ''
        by_day.setdefault(day.date() if hasattr(day, 'date') else day, set()).add(int(operator_id))
    return names, by_day


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
                                 ar_max_percent=snapshot_mod.DEFAULT_AR_MAX_PERCENT,
                                 chat_ttl_seconds=30, chat_stale_max_seconds=600,
                                 chat_settings=None, chat_export_max_days=chat_export_mod.MAX_DAYS,
                                 lead_ttl_seconds=60):
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

    def _day_parts(day, group_id=None, with_leads=False):
        """Итоги и разрез по часам за ЛЮБЫЕ сутки, мимо кэша снимка.

        Нужны отбивке в полночь: в 00:00 последний полный час (23:00–24:00) и итог дня
        относятся к закончившимся суткам, а снимок табло уже живёт новыми. Длины
        автоинформаторов считаются на те сутки заново и в часовой кэш снимка не пишутся.

        group_id — итоги одной группы (отбивка «по группе»): касания отбираются тем же
        правилом, что фильтр группы на табло (`snapshot.touches_of_group`).

        with_leads — ещё и «Принятие лида в работу» тех суток (`lead_speed`): только по
        запросу, отбивке в полночь. Разрезу группы днём блок берётся из снимка, и читать
        сделки второй раз незачем. У группы не «Основы» блока нет, как на стене."""
        dept = department_id()
        people = load_people(dept, day) if dept is not None else []
        memberships = {}
        with db._get_cursor() as cursor:
            try:
                announce_seconds = snapshot_mod.announcement_seconds_from_deltas(
                    load_announcement_deltas(cursor, day))
            except Exception:  # noqa: BLE001
                log.exception('%s: длины автоинформаторов за %s не посчитались', label, day)
                announce_seconds = {}
            touches = _measured_touches(cursor, day, people, announce_seconds)
            if group_id is not None or with_leads:
                memberships = _memberships(cursor, people, day)
            if group_id is not None:
                touches = snapshot_mod.touches_of_group(
                    touches, people, memberships, load_queue_answers(cursor, day), int(group_id))
        parts = snapshot_mod.aggregate(touches, sl_seconds)
        out = {'day': day.isoformat(), 'totals': parts['totals'], 'hourly': parts['hourly']}
        if with_leads:
            block = _lead_past_day(day, snapshot_mod.on_board(people, memberships), memberships)
            if block is not None and group_id is not None and int(group_id) not in block['group_ids']:
                block = None
            out['lead_speed'] = block
        return out

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

    # Сделки «Основы» и звонки по их телефонам — сырьё «Принятия лида в работу». Снимок сделок
    # обновляется раз в 3 минуты, касания — раз в 20 с: перечитывать их на каждый снимок (раз в
    # десять секунд) незачем, раз в минуту. Своя транзакция: сбой здесь не должен оборвать
    # запросы снимка — в общей транзакции следующий запрос упал бы «transaction is aborted».
    lead_cache = {'ts': 0.0, 'day': None, 'value': None}

    def _lead_raw(day, now_ts):
        if lead_cache['day'] == day and now_ts - lead_cache['ts'] < lead_ttl_seconds:
            return lead_cache['value']
        try:
            with db._get_cursor() as cursor:
                deals = load_lead_deals(cursor, day)
                phones = {phone for deal in deals for phone in cdr_leads_mod.lead_phones(deal)}
                touches = [touch for touch in load_lead_touches(cursor, day)
                           if touch['phone'] in phones]
                sync_age = load_lead_sync_age(cursor, day)
            value = {'deals': deals, 'touches': touches, 'sync_age': sync_age, 'read_ts': now_ts}
        except Exception:  # noqa: BLE001
            # Без сделок плитка показывает прочерк, остальное табло целое. Прошлое чтение тех же
            # суток лучше прочерка: звонки в нём отстают, но не больше чем на минуту-другую.
            log.exception('%s: сделки amoCRM для принятия лида не прочитались', label)
            value = lead_cache['value'] if lead_cache['day'] == day else None
        lead_cache.update(ts=now_ts, day=day, value=value)
        return value

    def _lead_speed(day, now, people, memberships, now_ts):
        """Блок снимка «Принятие лида в работу» или None, если сделки не прочитались."""
        raw = _lead_raw(day, now_ts)
        if raw is None:
            return None
        exts, group_ids = lead_speed_mod.osnova_scope(people, memberships)
        block = lead_speed_mod.take_speed(raw['deals'], raw['touches'], exts, now)
        age = raw['sync_age']
        block.update(group_ids=group_ids,
                     synced_age_seconds=None if age is None
                     else age + max(0, int(now_ts - raw['read_ts'])))
        return block

    def _lead_past_day(day, people, memberships):
        """«Принятие лида» за любые сутки мимо кэша; None, если сделки не прочитались.

        Наборы — до конца тех суток, но не позже «сейчас»: в полночь это итог закончившегося
        дня. Возраст списка сделок здесь не нужен — предупреждение живёт только на стене."""
        try:
            with db._get_cursor() as cursor:
                deals = load_lead_deals(cursor, day)
                phones = {phone for deal in deals for phone in cdr_leads_mod.lead_phones(deal)}
                touches = [touch for touch in load_lead_touches(cursor, day)
                           if touch['phone'] in phones]
        except Exception:  # noqa: BLE001
            log.exception('%s: сделки amoCRM за %s для принятия лида не прочитались', label, day)
            return None
        end = datetime.combine(day + timedelta(days=1), datetime.min.time()) - timedelta(microseconds=1)
        now = min(datetime.now(_ALMATY).replace(tzinfo=None), end)
        exts, group_ids = lead_speed_mod.osnova_scope(people, memberships)
        block = lead_speed_mod.take_speed(deals, touches, exts, now)
        block.update(group_ids=group_ids, synced_age_seconds=None)
        return block

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
        try:
            lead_speed = _lead_speed(day, now, people, memberships, time.time())
        except Exception:  # noqa: BLE001
            log.exception('%s: принятие лида в работу не посчиталось', label)
            lead_speed = None
        return snapshot_mod.assemble(
            day=day, touches=touches, people=people, live_statuses=statuses,
            status_entry=status_entry, resolve_name=_resolve_name(),
            bridge_state=bridge_state, now=now, sl_seconds=sl_seconds,
            ar_min_percent=ar_min_percent, ar_max_percent=ar_max_percent,
            announce_seconds=announce_seconds, memberships=memberships,
            queue_owners=queue_owners, phone_events=phone_events, lead_speed=lead_speed)

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

    # ── Направление «Чат»: чаты верификаторов в Wazzup (задача #367) ─────────────────────────
    # Свой кэш и свой замок: снимок линии и снимок чатов считаются из разных таблиц и с разной
    # частотой, и один не должен ждать другой. В БД снимок чатов не сохраняется — источник и так
    # наша база, после рестарта первый же запрос посчитает его заново за секунды.
    chat_cache = {'ts': 0.0, 'payload': None}
    chat_lock = threading.Lock()
    chat_label = 'Табло ОП · чат'
    # Снимок считается запросом к НАШЕЙ базе: замер снимка — это её сбой, а не Wazzup (о молчании
    # Wazzup говорит отдельный признак stream.silent). Имя источника уходит в «… не отвечает».
    chat_source = 'база iCORE'
    settings = dict(chat_settings or {})

    def _chat_fetch():
        now = datetime.now(_ALMATY).replace(tzinfo=None)
        day = now.date()
        gap = settings.get('gap_seconds', chat_mod.EPISODE_GAP_SECONDS)
        since = datetime.combine(day, datetime.min.time()) - timedelta(seconds=gap)
        with db._get_cursor() as cursor:
            names = load_chat_verifiers(cursor, day)
            rows = load_chat_messages(cursor, since, now + timedelta(minutes=1))
            last_message_at = load_chat_last_message_at(cursor)
        return chat_mod.assemble(rows, now, verifier_ids=names, names=names,
                                 last_message_at=last_message_at, **settings)

    def _chat_snapshot():
        """Снимок чатов из общего кэша — один на всех зрителей, виджет и отбивку."""
        return snapshot_with_cache(
            cache=chat_cache, lock=chat_lock, fetch=_chat_fetch, source=chat_source,
            ttl=chat_ttl_seconds, stale_max=chat_stale_max_seconds,
            retry_after=retry_after_seconds, lock_wait=lock_wait_seconds, label=chat_label)

    def _chat_blocks(days, now):
        """Показатели нескольких суток из базы — выгрузке и полуночной отбивке.

        Сообщения читаются с запасом паузы в обе стороны: диалог, начатый накануне вечером, знает
        своё начало, а ответ в 00:03 на диалог из 23:50 последних суток попадает в эти сутки."""
        gap = settings.get('gap_seconds', chat_mod.EPISODE_GAP_SECONDS)
        since = datetime.combine(days[0], datetime.min.time()) - timedelta(seconds=gap)
        until = min(datetime.combine(days[-1] + timedelta(days=1), datetime.min.time())
                    + timedelta(seconds=gap), now + timedelta(minutes=1))
        with db._get_cursor() as cursor:
            names, by_day = load_chat_verifier_days(cursor, days[0], days[-1])
            rows = load_chat_messages(cursor, since, until)
        return chat_mod.period_days(rows, days, verifier_ids=names, names=names,
                                    gap_seconds=gap, verifiers_by_day=by_day)

    def _chat_day(day):
        """Итог одних (закончившихся) суток — отбивке в полночь, про вчера целиком."""
        return _chat_blocks([day], datetime.now(_ALMATY).replace(tzinfo=None))[0]

    bp.chat_snapshot = _chat_snapshot
    bp.chat_day = _chat_day
    bp.chat_settings = settings

    @bp.route('/chat_snapshot', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_chat_snapshot():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        _, refusal = guard()
        if refusal is not None:
            return refusal
        try:
            payload = _chat_snapshot()
        except Exception as exc:  # noqa: BLE001
            log.warning('%s: снимок недоступен: %s', chat_label, exc)
            return jsonify({"error": str(exc)[:200]}), 503
        return jsonify(payload)

    @bp.route('/chat_export', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_chat_export():
        """Показатели чатов за период файлом .xlsx. Считает тот же расчёт, что экран, прямо из
        базы — включая сегодня: снимок экрана — это те же сообщения, только моложе на полминуты."""
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        _, refusal = guard()
        if refusal is not None:
            return refusal
        now = datetime.now(_ALMATY).replace(tzinfo=None)
        try:
            days = chat_export_mod.parse_period(request.args.get('date_from'),
                                                request.args.get('date_to'), now.date(),
                                                max_days=chat_export_max_days)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        try:
            blocks = _chat_blocks(days, now)
            content = chat_export_mod.workbook(
                blocks,
                first_target_seconds=settings.get('first_target_seconds', chat_mod.FIRST_TARGET_SECONDS),
                inner_target_seconds=settings.get('inner_target_seconds', chat_mod.INNER_TARGET_SECONDS),
                generated_at=now.strftime('%d.%m.%Y %H:%M'))
        except Exception as exc:  # noqa: BLE001
            log.exception('%s: выгрузка не собралась', chat_label)
            return jsonify({"error": "Не удалось собрать выгрузку", "detail": str(exc)[:300]}), 500
        return send_file(
            BytesIO(content), as_attachment=True, download_name=chat_export_mod.file_name(days),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    return bp
