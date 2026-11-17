# -*- coding: utf-8 -*-
"""HTTP «Отчёт по сменам» · «Чат» (SALES-1, задача #424) — окно «Отчёт по сменам» «Табло ОП».

    GET /api/op_wallboard/chat_shifts          ?month=ГГГГ-ММ — завершённые смены верификаторов
                                               месяца, операторы для фильтра, доступные месяцы
    GET /api/op_wallboard/chat_shifts/journal  ?operator_id=&start= — журнал одной смены
    GET /api/op_wallboard/chat_shifts/export   ?month=&operator_ids=&days= — те же строки .xlsx

Расчёт — `chat_shifts.py`, здесь только чтение базы, кэш месяца и права. Как у линии
(activity_routes.py): фильтры экран применяет сам, месяц приезжает целиком, выгрузка проходит их на
сервере тем же `activity.filter_shifts`. Журнал смены — из того же расчёта месяца, а не отдельным
чтением окна: смена чата зависит от соседей (вернулся после закрытия портала, тишина), и окно
одной смены посчитало бы её иначе, чем строка.

Доступ — тот же, что у отчёта линии: guard табло плюс поимённый список
(`activity_routes.make_activity_guard`).
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request, send_file

from test_numbers import keys as test_keys
from wazzup import shift as shift_mod
from . import activity as activity_mod
from . import chat as chat_mod
from . import chat_shifts as shifts_mod
from . import chat_shifts_export as export_mod
from .activity_routes import json_response

log = logging.getLogger(__name__)

_ALMATY = ZoneInfo('Asia/Almaty')
ACCOUNT = 'op'

CURRENT_MONTH_TTL_SECONDS = 90
PAST_MONTH_TTL_SECONDS = 600

# Запас вокруг месяца. До: смена 1-го числа решается вечером накануне (не закрытая смена, тишина),
# а диалогу нужно его начало (неотвеченный клиент держит диалог до 12 часов). После: ночная смена
# последнего дня кончается утром, а «закончил ли разговор он» решает продолжение диалога.
EVENTS_BEFORE = timedelta(days=2)
MESSAGES_BEFORE = EVENTS_BEFORE + timedelta(hours=12)
AFTER = timedelta(days=2)


def load_roster(cursor, day_from, day_to):
    """Верификаторы периода: кто хоть день был в группе модели op_verificator, и те, у кого модель
    верификатора — у направления (без членства в группе раздел судит по нему, wazzup/access.py)."""
    cursor.execute("""
        SELECT DISTINCT m.operator_id
          FROM group_operator_memberships m
          JOIN groups g ON g.id = m.group_id
         WHERE LOWER(COALESCE(g.calculation_model_code, '')) = %(model)s
           AND m.start_date <= %(day_to)s
           AND (m.end_date IS NULL OR m.end_date >= %(day_from)s)
        UNION
        SELECT u.id
          FROM users u
          JOIN directions d ON d.id = u.direction_id
         WHERE LOWER(COALESCE(d.calculation_model_code, '')) = %(model)s
    """, {'model': chat_mod.VERIFIER_GROUP_MODEL, 'day_from': day_from, 'day_to': day_to})
    return sorted({int(row[0]) for row in cursor.fetchall()})


def load_section_events(cursor, operator_ids, since, until):
    """{id: [(время, ключ, пометка)]} — события смены РАЗДЕЛА (метка `wzws-`). Индекс
    (operator_id, event_at) есть; события iCORE Phone тех же людей сюда не попадают."""
    if not operator_ids:
        return {}
    cursor.execute("""
        SELECT operator_id, event_at, status_key, state_note
          FROM operator_status_events
         WHERE operator_id = ANY(%s)
           AND client_event_id LIKE %s
           AND event_at >= %s AND event_at < %s
           AND COALESCE(event_kind, 'status') <> 'action'
         ORDER BY operator_id, event_at, id
    """, (list(operator_ids), shift_mod.CLIENT_EVENT_PREFIX + '%', since, until))
    out = {}
    for operator_id, event_at, status_key, note in cursor.fetchall():
        out.setdefault(int(operator_id), []).append((event_at, status_key, note))
    return out


def load_phone_offs(cursor, operator_ids, since, until):
    """{id: [моменты]} — выходы iCORE Phone тех же людей (chat_shifts.section_runs и разрыв по тишине)."""
    if not operator_ids:
        return {}
    cursor.execute("""
        SELECT operator_id, event_at
          FROM operator_status_events
         WHERE operator_id = ANY(%s)
           AND client_event_id IS NOT NULL
           AND client_event_id NOT LIKE %s
           AND LOWER(TRIM(status_key)) = %s
           AND event_at >= %s AND event_at < %s
           AND COALESCE(event_kind, 'status') <> 'action'
         ORDER BY operator_id, event_at
    """, (list(operator_ids), shift_mod.CLIENT_EVENT_PREFIX + '%', shift_mod.LOGOUT_KEY, since, until))
    out = {}
    for operator_id, event_at in cursor.fetchall():
        out.setdefault(int(operator_id), []).append(event_at)
    return out


def load_names(cursor, operator_ids):
    if not operator_ids:
        return {}
    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (list(operator_ids),))
    return {int(row[0]): row[1] or '' for row in cursor.fetchall()}


def dismissals_journal_exists(cursor):
    """Журнал «Ответ не нужен» заводит схема счётчика «Чатов ОП» (wazzup/unread.py) — там, где раздел
    не разворачивали, его нет, и отчёт без него просто не видит нажатий."""
    cursor.execute("SELECT to_regclass('wazzup_reply_dismissals') IS NOT NULL")
    row = cursor.fetchone()
    return bool(row and row[0])


def load_dismissals(cursor, since, until, account=ACCOUNT):
    """Нажатия «Ответ не нужен» за [since, until): [{channel_id, chat_id, at, user_id}] — все, кто бы ни
    нажал: нажатие коллеги после ответа оператора тоже значит «разговор закончил не он»."""
    cursor.execute("""
        SELECT channel_id, chat_id, (dismissed_at AT TIME ZONE 'Asia/Almaty'), user_id
          FROM wazzup_reply_dismissals
         WHERE account = %s
           AND dismissed_at >= (%s::timestamp AT TIME ZONE 'Asia/Almaty')
           AND dismissed_at < (%s::timestamp AT TIME ZONE 'Asia/Almaty')
    """, (account, since, until))
    return [{'channel_id': row[0], 'chat_id': row[1], 'at': row[2], 'user_id': row[3]}
            for row in cursor.fetchall()]


def load_messages(cursor, operator_ids, since, until, account=ACCOUNT, with_dismissals=False):
    """Сообщения аккаунта за [since, until) кортежами `MESSAGE_FIELDS` — только в чатах, где за это
    время писал кто-то из верификаторов: в остальных у смен считать нечего, а чатов, где клиенту не
    ответил никто, в аккаунте больше половины (замер 01–16.11.2026). Словари входа `chat.classify`
    строит `message_rows` по одному: месяц переписки — сотня с лишним тысяч строк, и держать их
    словарями рядом с копиями разбора — лишние сотни мегабайт на пике.

    Боты отсекаются ещё в базе; рассылки и эхо без автора — нет: в «Чатах ОП» они тоже «первый
    исходящий» и забирают у чата первый ответ (`chat_shifts.first_reply_days`), а для всего
    остального их отбросит `chat.classify`. Тестовые номера — как у табло. Без ORDER BY: порядок
    наводит расчёт, а сортировка месяца в базе ушла бы на диск."""
    if not operator_ids:
        return []
    # Чаты, где верификатор только нажал «Ответ не нужен», — тоже его: без их сообщений клиента нажатие
    # не знало бы, на что отвечает.
    dismissed_chats = ("""
            UNION
            SELECT d.channel_id, d.chat_id
              FROM wazzup_reply_dismissals d
             WHERE d.account = %(account)s
               AND d.user_id = ANY(%(ids)s)
               AND d.dismissed_at >= (%(since)s::timestamp AT TIME ZONE 'Asia/Almaty')
               AND d.dismissed_at < (%(until)s::timestamp AT TIME ZONE 'Asia/Almaty')"""
                       if with_dismissals else '')
    cursor.execute("""
        WITH chats AS (
            SELECT DISTINCT w.channel_id, w.chat_id
              FROM wazzup_messages w
              JOIN wazzup_operator_map map ON map.author_id = w.author_id
             WHERE w.account = %(account)s
               AND w.is_echo
               AND NOT w.is_deleted
               AND map.user_id = ANY(%(ids)s)
               AND NOT COALESCE(map.is_bot, FALSE)
               AND w.dt >= (%(since)s::timestamp AT TIME ZONE 'Asia/Almaty')
               AND w.dt < (%(until)s::timestamp AT TIME ZONE 'Asia/Almaty')""" + dismissed_chats + """
        )
        SELECT w.channel_id, w.chat_id, (w.dt AT TIME ZONE 'Asia/Almaty'), w.is_echo,
               map.user_id, COALESCE(map.is_bot, FALSE), w.message_id, w.author_id, w.type
          FROM wazzup_messages w
          JOIN chats c ON c.channel_id = w.channel_id AND c.chat_id = w.chat_id
          LEFT JOIN wazzup_operator_map map ON map.author_id = w.author_id
         WHERE w.account = %(account)s
           AND NOT w.is_deleted
           AND w.dt >= (%(since)s::timestamp AT TIME ZONE 'Asia/Almaty')
           AND w.dt < (%(until)s::timestamp AT TIME ZONE 'Asia/Almaty')
           AND NOT (w.is_echo AND COALESCE(map.is_bot, FALSE))
           AND """ + test_keys.sql_not_test(test_keys.wazzup_phone_sql('w'), digits=True) + """
    """, {'account': account, 'ids': list(operator_ids), 'since': since, 'until': until})
    return cursor.fetchall()


MESSAGE_FIELDS = ('channel_id', 'chat_id', 'at', 'is_echo', 'user_id', 'is_bot', 'message_id',
                  'author_id', 'type')


def message_rows(rows):
    """Кортежи `load_messages` → словари входа `chat.classify`, по одному (см. load_messages)."""
    for row in rows:
        item = dict(zip(MESSAGE_FIELDS, row))
        item['is_echo'] = bool(item['is_echo'])
        item['is_bot'] = bool(item['is_bot'])
        yield item


def public_payload(payload):
    """Ответ экрану: без журналов и тех. перерывов — они нужны ручке журнала и выгрузке."""
    return {key: value for key, value in payload.items() if key not in ('journals', 'tech_breaks')}


def build_chat_shifts_blueprint(*, db, require_api_key, build_cors_preflight_response, guard,
                                retention_days=shifts_mod.RETENTION_DAYS, now=None):
    bp = Blueprint('op_chat_shifts', __name__, url_prefix='/api/op_wallboard/chat_shifts')
    cache = {}
    cache_lock = threading.Lock()
    label = 'Отчёт по сменам · чат'

    def _now():
        return now() if now else datetime.now(_ALMATY).replace(tzinfo=None)

    def _compute(month_first, month_last):
        moment = _now()
        today = moment.date()
        day_from = max(month_first, shifts_mod.retention_floor(today, retention_days))
        start = datetime.combine(day_from, datetime.min.time())
        until = min(datetime.combine(month_last + timedelta(days=1), datetime.min.time()) + AFTER,
                    moment + timedelta(minutes=1))
        with db._get_cursor() as cursor:
            roster = load_roster(cursor, day_from - timedelta(days=2), month_last)
            events = load_section_events(cursor, roster, start - EVENTS_BEFORE, until)
            people = sorted(events)
            phone_offs = load_phone_offs(cursor, people, start - EVENTS_BEFORE, until)
            names = load_names(cursor, people)
            journal = dismissals_journal_exists(cursor)
            rows = load_messages(cursor, people, start - MESSAGES_BEFORE, until, with_dismissals=journal)
            dismissals = load_dismissals(cursor, start - MESSAGES_BEFORE, until) if journal else []
        # Расчёт — после того как соединение вернулось в пул: месяц переписки считается секунды.
        report = shifts_mod.build_report(
            events_by_operator=events, names=names, rows=message_rows(rows), day_from=day_from,
            day_to=month_last, now=moment, phone_offs=phone_offs, verifier_ids=set(roster) | set(events),
            dismissals=dismissals)
        del rows
        return {
            'month': '%04d-%02d' % (month_first.year, month_first.month),
            'month_label': activity_mod.month_label(month_first),
            'day_from': day_from.isoformat(),
            'day_to': month_last.isoformat(),
            'generated_at': moment.strftime('%Y-%m-%dT%H:%M:%S'),
            'months': shifts_mod.available_months(today, retention_days),
            'groups': [{'key': shifts_mod.GROUP_KEY, 'label': shifts_mod.GROUP_LABEL}],
            'operators': report['operators'],
            'shifts': report['shifts'],
            'days_with_shifts': sorted({row['date'] for row in report['shifts']}),
            'journals': report['journals'],
            'tech_breaks': report['tech_breaks'],
        }

    def _month_report(month_value):
        today = _now().date()
        month_first, month_last = activity_mod.month_bounds(month_value, today)
        if month_first > today:
            raise ValueError('Этот месяц ещё не начался')
        if month_last < shifts_mod.retention_floor(today, retention_days):
            raise ValueError('Переписка хранится %d суток — за этот месяц её уже нет' % int(retention_days))
        key = month_first.isoformat()
        ttl = CURRENT_MONTH_TTL_SECONDS if month_last >= today - timedelta(days=1) else PAST_MONTH_TTL_SECONDS
        entry = cache.get(key)
        if entry and time.time() - entry[0] < ttl:
            return entry[1]
        # Один расчёт месяца на всех: второй зритель ждёт первого, а не читает то же самое.
        with cache_lock:
            entry = cache.get(key)
            if entry and time.time() - entry[0] < ttl:
                return entry[1]
            payload = _compute(month_first, month_last)
            cache[key] = (time.time(), payload)
            for stale in [k for k, (ts, _) in cache.items() if time.time() - ts > PAST_MONTH_TTL_SECONDS]:
                cache.pop(stale, None)
            return payload

    @bp.route('', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_chat_shifts():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        _, refusal = guard()
        if refusal is not None:
            return refusal
        try:
            payload = _month_report(request.args.get('month'))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            log.exception('%s: отчёт не собрался', label)
            return jsonify({'error': 'Отчёт сейчас не собирается', 'detail': str(exc)[:300]}), 503
        return json_response(public_payload(payload))

    @bp.route('/journal', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_chat_shifts_journal():
        """Журнал смены из того же расчёта месяца: смену ищем по человеку и входу. Чужих событий
        по id ручка не читает — только смены, которые отчёт уже показал."""
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        _, refusal = guard()
        if refusal is not None:
            return refusal
        try:
            operator_id = int(str(request.args.get('operator_id') or '').strip())
        except ValueError:
            return jsonify({'error': 'Не указан сотрудник'}), 400
        start = activity_mod.parse_moment(request.args.get('start'))
        if start is None:
            return jsonify({'error': 'Не указана смена'}), 400
        try:
            payload = _month_report('%04d-%02d' % (start.year, start.month))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 404
        except Exception:  # noqa: BLE001
            log.exception('%s: журнал %s не собрался', label, operator_id)
            return jsonify({'error': 'Журнал смены сейчас недоступен'}), 503
        key = '%s:%s' % (operator_id, activity_mod.stamp(start))
        row = next((item for item in payload['shifts'] if item['key'] == key), None)
        if row is None:
            return jsonify({'error': 'Смена не найдена — обновите отчёт'}), 404
        return jsonify({
            'operator_id': operator_id,
            'name': row['name'],
            'group_label': row['group_label'],
            'start_at': row['login_at'],
            'end_at': row['logout_at'],
            'logout_missing': row['logout_missing'],
            'login_missing': row['login_missing'],
            'segments': payload['journals'].get(key) or [],
        })

    @bp.route('/export', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_chat_shifts_export():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        _, refusal = guard()
        if refusal is not None:
            return refusal
        try:
            payload = _month_report(request.args.get('month'))
            first = datetime.strptime(payload['day_from'], '%Y-%m-%d').date()
            last = datetime.strptime(payload['day_to'], '%Y-%m-%d').date()
            group = str(request.args.get('group') or '').strip() or None
            if group and group not in shifts_mod.GROUP_LABELS:
                raise ValueError('Неизвестная группа')
            operator_ids = activity_mod.parse_ids(request.args.get('operator_ids'))
            days = activity_mod.parse_days(request.args.get('days'), first, last)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            log.exception('%s: выгрузка не собралась', label)
            return jsonify({'error': 'Не удалось собрать выгрузку', 'detail': str(exc)[:300]}), 503
        rows = activity_mod.filter_shifts(payload['shifts'], group, operator_ids, days)
        try:
            content = export_mod.workbook(
                rows, shifts_mod.filter_tech_breaks(payload['tech_breaks'], [row['key'] for row in rows]),
                month_label=payload['month_label'],
                generated_at=_now().strftime('%d.%m.%Y %H:%M'))
        except Exception as exc:  # noqa: BLE001
            log.exception('%s: файл не собрался', label)
            return jsonify({'error': 'Не удалось собрать выгрузку', 'detail': str(exc)[:300]}), 500
        return send_file(
            BytesIO(content), as_attachment=True,
            download_name=export_mod.file_name(payload['month']),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    bp.month_report = _month_report
    return bp
