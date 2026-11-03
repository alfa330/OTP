# -*- coding: utf-8 -*-
"""Действия с заявками на отпуск, Б/С и больничный.

Каждая функция работает в транзакции роута (курсор приходит готовым) и
возвращает заявку так, как её увидит тот, кто действовал. Telegram копится в
outbox и уходит после commit.

Зависимости от монолита приходят объектом deps — пакет не импортирует
database.py, иначе тесты на настоящем Postgres тянули бы всю схему портала:

    deps.resolve_supervisor(cursor, operator_id, day) -> id СВ группы или None
    deps.apply_period(cursor, operator_id=, kind=, start=, end=, created_by=,
                      exclude_from_tenure=, comment=) -> период графика (dict с 'id')
"""

from datetime import datetime

from . import notify, queries, rules
from .rules import LeaveError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

SCHEDULE_LABELS = {
    'annual_leave': 'Ежегодный отпуск',
    'bs': 'Б/С',
    'sick_leave': 'Больничный',
    'dismissal': 'Увольнение',
}


def _now():
    """«Сейчас» наивным временем Алматы — как его пишет база (queries.NOW_SQL)."""
    try:
        return datetime.now(ZoneInfo('Asia/Almaty')).replace(tzinfo=None)
    except Exception:  # noqa: BLE001 — без tzdata процесс и так живёт в Asia/Almaty
        return datetime.now()


# ── Общие проверки ──────────────────────────────────────────────────────────

def _require_operator(user):
    if not user or user.get('role') != 'operator':
        raise LeaveError('Заявку подаёт оператор', code='LEAVE_OPERATOR_ONLY', status=403)


def _own_locked(cursor, user, request_id):
    locked = queries.lock_request(cursor, request_id)
    if not locked or int(locked['operator_id']) != int(user['id']):
        raise LeaveError('Заявка не найдена', code='LEAVE_NOT_FOUND', status=404)
    return locked


def _ensure_free(cursor, operator_id, start, end, today, *, exclude_id=None):
    """Даты свободны и у оператора, и в его графике.

    Своя действующая заявка на те же дни — повтор. Отпуск, Б/С, больничный или
    увольнение уже в графике — тоже: утверждение перезаписало бы чужой период
    (save_schedule_status_period режет пересекающиеся молча).
    """
    clash = queries.own_active_overlap(cursor, operator_id, start, end, exclude_id=exclude_id)
    if clash:
        raise LeaveError(
            'На эти даты у вас уже есть заявка: %s' % rules.format_period(
                clash['start_date'], clash['end_date'], today),
            code='LEAVE_OVERLAP_OWN', status=409)
    for period in queries.schedule_periods(cursor, operator_id, start, end):
        label = SCHEDULE_LABELS.get(period['status_code'], period['status_code'])
        end_text = (rules.format_period(period['start_date'], period['end_date'], today)
                    if period['end_date'] else 'с %s' % rules.format_period(
                        period['start_date'], period['start_date'], today))
        raise LeaveError(
            'На эти даты в графике уже стоит «%s» (%s)' % (label, end_text),
            code='LEAVE_OVERLAP_SCHEDULE', status=409)


def _actor_event(cursor, request_id, actor, action, status_after, *, start=None, end=None, comment=None):
    queries.insert_event(
        cursor, request_id,
        actor_id=actor.get('id'), actor_name=actor.get('name'),
        action=action, status_after=status_after,
        start_date=start, end_date=end, comment=comment,
    )


def _serialize(cursor, raw, extra=None):
    events = queries.fetch_events(cursor, [raw['id']]).get(int(raw['id']), [])
    item = queries.serialize_request(raw, events)
    if extra:
        item.update(extra)
    return item


def _parse_period(data):
    start = rules.parse_date((data or {}).get('start_date'), 'start_date')
    end = rules.parse_date((data or {}).get('end_date'), 'end_date')
    return start, end


def _overlaps_for(cursor, raw, today, *, start=None, end=None, limit=8):
    return queries.colleague_overlaps(
        cursor, department_id=raw['department_id'], operator_id=raw['operator_id'],
        start=start or raw['start_date'], end=end or raw['end_date'], today=today, limit=limit)


# ── Оператор ────────────────────────────────────────────────────────────────

def operator_payload(cursor, user, today):
    """«Мои заявки»: доступные виды и свои заявки, свежие сверху."""
    settings = queries.load_settings(cursor, user.get('department_id')) if user else None
    kinds = rules.allowed_kinds(settings) if user and user.get('role') == 'operator' else ()
    raws = queries.fetch_requests(
        cursor, 'r.operator_id = %(operator_id)s', {'operator_id': int(user['id']), 'today': today},
        limit=100) if user else []
    events = queries.fetch_events(cursor, [raw['id'] for raw in raws])
    items = []
    attention = 0
    for raw in raws:
        item = queries.serialize_request(raw, events.get(int(raw['id']), []))
        item['actions'] = list(rules.operator_actions(raw['status']))
        item['unseen'] = raw['status'] in ('approved', 'rejected') and raw['operator_seen_at'] is None
        if raw['status'] == 'proposal' or item['unseen']:
            attention += 1
        items.append(item)
    return {
        'enabled': bool(kinds),
        'kinds': list(kinds),
        'supervisorStep': bool((settings or {}).get('supervisor_step', True)),
        'today': today.strftime('%Y-%m-%d'),
        'maxPeriodDays': rules.MAX_PERIOD_DAYS,
        'maxAheadDays': rules.MAX_AHEAD_DAYS,
        'sickBackDays': rules.SICK_BACK_DAYS,
        'requests': items,
        'attentionCount': attention,
    }


def _read_form(data, kinds, *, current_kind=None):
    data = data or {}
    kind = str(data.get('kind') or '').strip()
    if kind not in kinds and kind != current_kind:
        raise LeaveError('Выберите вид заявки', code='LEAVE_BAD_KIND')
    start, end = _parse_period(data)
    reason = rules.clean_text(data.get('reason'), rules.REASON_MAX_CHARS, required=True,
                              empty_message='Напишите причину')
    return kind, start, end, reason


def create(cursor, deps, outbox, *, user, data, today, base_url=None):
    _require_operator(user)
    settings = queries.load_settings(cursor, user['department_id'])
    kinds = rules.allowed_kinds(settings)
    if not kinds:
        raise LeaveError('В вашем отделе такие заявки не принимаются', code='LEAVE_DISABLED', status=403)
    kind, start, end, reason = _read_form(data, kinds)
    rules.validate_period(start, end, today, kind)
    _ensure_free(cursor, user['id'], start, end, today)

    supervisor_id = deps.resolve_supervisor(cursor, user['id'], today) if settings.get('supervisor_step') else None
    status = rules.first_status(settings, supervisor_id)
    request_id = queries.insert_request(
        cursor, operator_id=user['id'], department_id=user['department_id'], supervisor_id=supervisor_id,
        kind=kind, start_date=start, end_date=end, reason=reason, status=status)
    _actor_event(cursor, request_id, user, 'created', status, start=start, end=end, comment=reason)

    raw = queries.fetch_request(cursor, request_id, today)
    total, _ = _overlaps_for(cursor, raw, today, limit=1)
    notify.on_created(cursor, outbox, raw, actor_id=user['id'], today=today, overlaps_total=total,
                      base_url=base_url)
    return _serialize(cursor, raw, {'actions': list(rules.operator_actions(status)), 'unseen': False})


def edit(cursor, deps, outbox, *, user, request_id, data, today, base_url=None):
    _require_operator(user)
    locked = _own_locked(cursor, user, request_id)
    if 'edit' not in rules.operator_actions(locked['status']):
        raise LeaveError('Заявку уже не изменить: она %s' % rules.STATUS_LABELS[locked['status']].lower(),
                         code='LEAVE_LOCKED', status=409)
    settings = queries.load_settings(cursor, locked['department_id']) or {}
    kind, start, end, reason = _read_form(data, rules.allowed_kinds(settings), current_kind=locked['kind'])
    rules.validate_period(start, end, today, kind)
    _ensure_free(cursor, user['id'], start, end, today, exclude_id=locked['id'])

    # Правка начинает маршрут заново: СВ согласовывал другие даты.
    supervisor_id = deps.resolve_supervisor(cursor, user['id'], today) if settings.get('supervisor_step', True) else None
    status = rules.first_status(settings, supervisor_id)
    queries.update_request(
        cursor, locked['id'], kind=kind, start_date=start, end_date=end, reason=reason, status=status,
        supervisor_id=supervisor_id, proposed_start_date=None, proposed_end_date=None)
    _actor_event(cursor, locked['id'], user, 'edited', status, start=start, end=end, comment=reason)

    raw = queries.fetch_request(cursor, locked['id'], today)
    total, _ = _overlaps_for(cursor, raw, today, limit=1)
    notify.on_created(cursor, outbox, raw, actor_id=user['id'], today=today, overlaps_total=total,
                      base_url=base_url, edited=True)
    return _serialize(cursor, raw, {'actions': list(rules.operator_actions(status)), 'unseen': False})


def cancel(cursor, outbox, *, user, request_id, data, today, base_url=None):
    _require_operator(user)
    locked = _own_locked(cursor, user, request_id)
    if 'cancel' not in rules.operator_actions(locked['status']):
        raise LeaveError('Заявку уже не отозвать: она %s' % rules.STATUS_LABELS[locked['status']].lower(),
                         code='LEAVE_LOCKED', status=409)
    comment = rules.clean_text((data or {}).get('comment'), rules.COMMENT_MAX_CHARS)
    queries.update_request(
        cursor, locked['id'], status='cancelled', proposed_start_date=None, proposed_end_date=None,
        # Своё действие оператору новостью не показываем.
        operator_seen_at=_now())
    _actor_event(cursor, locked['id'], user, 'cancelled', 'cancelled', comment=comment)
    raw = queries.fetch_request(cursor, locked['id'], today)
    notify.on_cancelled(cursor, outbox, raw, previous_status=locked['status'], actor_id=user['id'],
                        today=today, base_url=base_url)
    return _serialize(cursor, raw, {'actions': [], 'unseen': False})


def answer_proposal(cursor, deps, outbox, *, user, request_id, accept, data, today, base_url=None):
    """Оператор отвечает на предложенные даты (ТЗ п. 3)."""
    _require_operator(user)
    locked = _own_locked(cursor, user, request_id)
    if locked['status'] != 'proposal':
        raise LeaveError('Предложение уже неактуально', code='LEAVE_LOCKED', status=409)
    comment = rules.clean_text((data or {}).get('comment'), rules.COMMENT_MAX_CHARS)
    events = queries.fetch_events(cursor, [locked['id']]).get(int(locked['id']), [])
    proposer_id = next((e['actor']['id'] for e in reversed(events) if e['action'] == 'proposed'), None)

    if accept:
        start, end = locked['proposed_start_date'], locked['proposed_end_date']
        if start < today:
            raise LeaveError('Предложенные даты уже начались — измените заявку', code='LEAVE_PERIOD_PAST')
        _ensure_free(cursor, user['id'], start, end, today, exclude_id=locked['id'])
        # Согласен — значит СВ эти даты уже одобрил: заявка идёт главе.
        queries.update_request(cursor, locked['id'], start_date=start, end_date=end, status='head',
                               proposed_start_date=None, proposed_end_date=None)
        _actor_event(cursor, locked['id'], user, 'proposal_accepted', 'head', start=start, end=end,
                     comment=comment)
    else:
        # Не согласен — заявка возвращается к СВ со своими датами: решать
        # ему (согласовать как было, отклонить или предложить ещё раз).
        queries.update_request(cursor, locked['id'], status='supervisor',
                               proposed_start_date=None, proposed_end_date=None)
        _actor_event(cursor, locked['id'], user, 'proposal_declined', 'supervisor', comment=comment)

    raw = queries.fetch_request(cursor, locked['id'], today)
    notify.on_proposal_answered(cursor, outbox, raw, accepted=accept, proposer_id=proposer_id,
                                actor_id=user['id'], comment=comment, today=today, base_url=base_url)
    return _serialize(cursor, raw, {'actions': list(rules.operator_actions(raw['status'])), 'unseen': False})


def mark_seen(cursor, user):
    cursor.execute(
        """
        UPDATE leave_requests SET operator_seen_at = """ + queries.NOW_SQL + """
         WHERE operator_id = %s AND status IN ('approved', 'rejected') AND operator_seen_at IS NULL
        """,
        (int(user['id']),),
    )
    return cursor.rowcount or 0


# ── Руководитель ────────────────────────────────────────────────────────────

def _review_extra(viewer, raw, settings_by_department):
    role = rules.reviewer_role(viewer, raw, in_scope=True)
    settings = settings_by_department.get(raw['department_id']) or {}
    days = rules.period_days(raw['start_date'], raw['end_date'])
    return {
        'role': role,
        'actions': (list(rules.reviewer_actions(role, raw['status'], raw['kind']))
                    if int(raw['operator_id']) != int(viewer['user_id']) else []),
        'awaitsMe': rules.awaits(role, raw['status']),
        # Утверждение СВ — промежуточное, главы и админа — окончательное.
        'approveIsFinal': rules.status_after_approve(role) == 'approved',
        'tenureDefault': rules.default_exclude_from_tenure(raw['kind'], days, settings.get('long_bs_days')),
        'longBsDays': settings.get('long_bs_days'),
    }


def _settings_map(cursor, department_ids):
    return {dep: queries.load_settings(cursor, dep) for dep in {d for d in department_ids if d is not None}}


def review_candidates(cursor):
    """Кандидаты в очередь: у кого вообще есть заявки. Границу отдела режет роут
    той же функцией, что «Запросы по сменам» (_filter_operators_for_requester_scope)."""
    cursor.execute("SELECT DISTINCT operator_id FROM leave_requests")
    return [int(row[0]) for row in cursor.fetchall()]


def review_payload(cursor, *, viewer, operator_ids, status_filter, today, limit=300):
    ids = sorted({int(i) for i in operator_ids or ()})
    viewer_departments = set(viewer.get('headed_department_ids') or ())
    if viewer.get('department_id') is not None:
        viewer_departments.add(int(viewer['department_id']))
    enabled_departments = queries.enabled_department_ids(cursor)
    payload = {
        'requests': [],
        'pendingCount': 0,
        'canManageSettings': bool(viewer.get('is_admin')),
        'enabled': bool(viewer.get('is_admin')) or bool(enabled_departments & viewer_departments),
        # Какие виды приходят в эту очередь — по ним она себя и называет.
        'kinds': list(queries.enabled_kinds(cursor, None if viewer.get('is_admin') else viewer_departments)),
        'today': today.strftime('%Y-%m-%d'),
    }
    if not ids:
        return payload

    params = {'ids': ids, 'today': today, 'open': list(rules.OPEN_STATUSES)}
    where = 'r.operator_id = ANY(%(ids)s)'
    if status_filter == 'pending':
        where += ' AND r.status = ANY(%(open)s)'
    raws = queries.fetch_requests(
        cursor, where, params,
        # Открытые — сверху и по дате начала: разбирают ближайший отпуск
        # первым. Разобранные — история, свежие сверху.
        order_sql="(r.status = ANY(%(open)s)) DESC, "
                  "CASE WHEN r.status = ANY(%(open)s) THEN r.start_date END ASC, "
                  "r.updated_at DESC, r.id DESC",
        limit=limit)
    events = queries.fetch_events(cursor, [raw['id'] for raw in raws])
    settings = _settings_map(cursor, [raw['department_id'] for raw in raws])
    items = []
    for raw in raws:
        item = queries.serialize_request(raw, events.get(int(raw['id']), []))
        item.update(_review_extra(viewer, raw, settings))
        if raw['status'] in rules.OPEN_STATUSES:
            start = raw['proposed_start_date'] if raw['status'] == 'proposal' else raw['start_date']
            end = raw['proposed_end_date'] if raw['status'] == 'proposal' else raw['end_date']
            total, overlaps = _overlaps_for(cursor, raw, today, start=start, end=end)
            item['overlaps'] = {'total': total, 'items': overlaps}
        items.append(item)
    payload['requests'] = items

    # Счётчик — по всем открытым заявкам периметра, а не по показанным: фильтр
    # «Все» с лимитом мог бы обрезать хвост.
    cursor.execute(
        """
        SELECT r.id, r.operator_id, r.department_id, r.status
          FROM leave_requests r
         WHERE r.operator_id = ANY(%s) AND r.status IN ('supervisor', 'head')
        """,
        (ids,),
    )
    payload['pendingCount'] = sum(
        1 for row in cursor.fetchall()
        if int(row[1]) != int(viewer['user_id']) and rules.awaits(
            rules.reviewer_role(viewer, {'department_id': row[2]}, in_scope=True), row[3]))
    return payload


def overlaps_preview(cursor, *, request_id, start, end, today):
    raw = queries.fetch_request(cursor, request_id, today)
    if not raw:
        raise LeaveError('Заявка не найдена', code='LEAVE_NOT_FOUND', status=404)
    if end < start:
        raise LeaveError('Дата окончания раньше даты начала', code='LEAVE_PERIOD_REVERSED')
    total, items = _overlaps_for(cursor, raw, today, start=start, end=end)
    return {'total': total, 'items': items}


def decide(cursor, deps, outbox, *, viewer, actor, request_id, data, in_scope, today, base_url=None):
    """Решение СВ или главы: согласовать / утвердить, предложить даты, отклонить."""
    locked = queries.lock_request(cursor, request_id)
    if not locked:
        raise LeaveError('Заявка не найдена', code='LEAVE_NOT_FOUND', status=404)
    role = rules.reviewer_role(viewer, locked, in_scope=in_scope(locked['operator_id']))
    if role is None:
        raise LeaveError('Эта заявка не в вашем периметре', code='LEAVE_FORBIDDEN', status=403)
    if int(locked['operator_id']) == int(viewer['user_id']):
        raise LeaveError('Свою заявку решить нельзя', code='LEAVE_FORBIDDEN', status=403)

    data = data or {}
    action = str(data.get('action') or '').strip().lower()
    if action not in rules.reviewer_actions(role, locked['status'], locked['kind']):
        if action == 'propose' and locked['kind'] == 'sick_leave':
            raise LeaveError('По больничному другие даты не предлагают — его согласуют или отклоняют',
                             code='LEAVE_LOCKED', status=409)
        raise LeaveError('Сейчас это недоступно: заявка — «%s»' % rules.STATUS_LABELS[locked['status']],
                         code='LEAVE_LOCKED', status=409)
    comment = rules.clean_text(data.get('comment'), rules.COMMENT_MAX_CHARS)
    now = _now()

    if action == 'reject':
        queries.update_request(cursor, locked['id'], status='rejected', decided_by=viewer['user_id'],
                               decided_at=now, proposed_start_date=None, proposed_end_date=None,
                               operator_seen_at=None)
        _actor_event(cursor, locked['id'], actor, 'rejected', 'rejected', comment=comment)
        raw = queries.fetch_request(cursor, locked['id'], today)
        notify.on_decided(cursor, outbox, raw, approved=False, actor_id=viewer['user_id'], comment=comment,
                          today=today, base_url=base_url)

    elif action == 'propose':
        start, end = _parse_period(data)
        rules.validate_period(start, end, today)
        if (start, end) == (locked['start_date'], locked['end_date']):
            raise LeaveError('Это те же даты, что в заявке — выберите другие', code='LEAVE_SAME_DATES')
        _ensure_free(cursor, locked['operator_id'], start, end, today, exclude_id=locked['id'])
        queries.update_request(cursor, locked['id'], status='proposal',
                               proposed_start_date=start, proposed_end_date=end)
        _actor_event(cursor, locked['id'], actor, 'proposed', 'proposal', start=start, end=end, comment=comment)
        raw = queries.fetch_request(cursor, locked['id'], today)
        notify.on_proposed(cursor, outbox, raw, actor_id=viewer['user_id'], comment=comment, today=today,
                           base_url=base_url)

    elif rules.status_after_approve(role) == 'head':
        queries.update_request(cursor, locked['id'], status='head')
        _actor_event(cursor, locked['id'], actor, 'supervisor_approved', 'head', comment=comment)
        raw = queries.fetch_request(cursor, locked['id'], today)
        total, _ = _overlaps_for(cursor, raw, today, limit=1)
        notify.on_supervisor_approved(cursor, outbox, raw, actor_id=viewer['user_id'], actor_name=actor.get('name'),
                                      today=today, overlaps_total=total, base_url=base_url)

    else:
        start, end, kind = locked['start_date'], locked['end_date'], locked['kind']
        # Даты свободны и сейчас: пока заявка шла, в график могли поставить
        # больничный, а утверждение молча его перезаписало бы.
        _ensure_free(cursor, locked['operator_id'], start, end, today, exclude_id=locked['id'])
        settings = queries.load_settings(cursor, locked['department_id']) or {}
        if kind != 'bs':
            exclude = False
        elif 'exclude_from_tenure' in data:
            exclude = bool(data.get('exclude_from_tenure'))
        else:
            exclude = rules.default_exclude_from_tenure(kind, rules.period_days(start, end),
                                                        settings.get('long_bs_days'))
        period = deps.apply_period(
            cursor, operator_id=locked['operator_id'], kind=kind, start=start, end=end,
            created_by=viewer['user_id'], exclude_from_tenure=exclude,
            comment='Заявка №%d' % int(locked['id']) + (': %s' % locked['reason'] if locked['reason'] else ''))
        queries.update_request(cursor, locked['id'], status='approved', decided_by=viewer['user_id'],
                               decided_at=now, exclude_from_tenure=exclude,
                               status_period_id=(period or {}).get('id'), operator_seen_at=None)
        _actor_event(cursor, locked['id'], actor, 'approved', 'approved', start=start, end=end, comment=comment)
        raw = queries.fetch_request(cursor, locked['id'], today)
        notify.on_decided(cursor, outbox, raw, approved=True, actor_id=viewer['user_id'], comment=comment,
                          today=today, base_url=base_url)

    settings = _settings_map(cursor, [raw['department_id']])
    extra = _review_extra(viewer, raw, settings)
    if raw['status'] in rules.OPEN_STATUSES:
        start = raw['proposed_start_date'] if raw['status'] == 'proposal' else raw['start_date']
        end = raw['proposed_end_date'] if raw['status'] == 'proposal' else raw['end_date']
        total, overlaps = _overlaps_for(cursor, raw, today, start=start, end=end)
        extra['overlaps'] = {'total': total, 'items': overlaps}
    return _serialize(cursor, raw, extra)
