# -*- coding: utf-8 -*-
"""Сценарии раздела «Дисциплина»: где правила (catalog, access) встречаются с базой.

Каждая функция работает на переданном курсоре — одна транзакция на действие.
Ошибки для человека — DisciplineError с текстом по-русски и кодом ответа.

Что происходит при фиксации нарушения (ТЗ п. 6, 7, 9, 10):

    1. проверка периметра: СВ — только оператор своей группы, руководитель —
       своего отдела;
    2. снимок группы, линии и смены на момент нарушения;
    3. если человек на особом контроле — мера обязательна: СВ её предлагает,
       руководитель назначает сразу (п. 10.4);
    4. номер в периоде и ступень лестницы — расчётом по истории человека;
    5. ступень «акт» — черновик акта сам и особый контроль сам, «без действий
       человека» (критерий 4); акт связан со всеми записями окна (п. 9);
    6. уведомления по таблице п. 12 и строка журнала действий.
"""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import access, catalog, notify, queries

ALMATY = ZoneInfo('Asia/Almaty')
TRAINING_REASON = 'Дисциплинарный тренинг'
MAX_BACKDATE_DAYS = 366
HISTORY_DAYS = 400
# С этого момента действуют правила, что были до первой правки окна или лестницы.
COUNTING_EPOCH = datetime(2000, 1, 1)


class DisciplineError(Exception):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def now_almaty():
    return datetime.now(ALMATY).replace(tzinfo=None, microsecond=0)


def today_almaty():
    return datetime.now(ALMATY).date()


# ─────────────────────────────────────────────────────────────────────────────
# Разбор полей
# ─────────────────────────────────────────────────────────────────────────────

def parse_moment(raw, now):
    if raw in (None, ''):
        return now
    text = str(raw).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d.%m.%Y %H:%M'):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    raise DisciplineError('Дата и время нарушения указаны неверно')


def parse_day(raw, field='Дата'):
    try:
        return datetime.strptime(str(raw or '').strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        raise DisciplineError('%s указана неверно' % field) from None


def parse_time(raw, field='Время'):
    try:
        return datetime.strptime(str(raw or '').strip()[:5], '%H:%M').time()
    except ValueError:
        raise DisciplineError('%s указано неверно' % field) from None


def _int(raw, field):
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise DisciplineError('Не указано: %s' % field) from None


def _text(raw, limit=4000):
    return str(raw or '').strip()[:limit]


# Время в полях записи: смена «ЧЧ:ММ–ЧЧ:ММ» (как её отдаёт график) и тайм-код
# записи экрана — фрагменты «ЧЧ:ММ» или «ЧЧ:ММ–ЧЧ:ММ» через запятую. Форма
# набирает их полями времени, а сервер приводит к одному виду и не пускает в
# базу свободный текст (владелец, 20.10.2026: «чтобы просто вводилось время, а
# не рандомные числа»).
_CLOCK = r'([01]?\d|2[0-3]):([0-5]\d)'
_SHIFT_RE = re.compile(r'^%s\s*[–—-]\s*%s$' % (_CLOCK, _CLOCK))
_TIMECODE_RE = re.compile(r'^%s(?:\s*[–—-]\s*%s)?$' % (_CLOCK, _CLOCK))
SHIFT_DASH = '–'
MAX_TIMECODES = 5


def _clock(hours, minutes):
    return '%02d:%02d' % (int(hours), int(minutes))


def _shift_value(raw):
    """Смена записи — «ЧЧ:ММ–ЧЧ:ММ»; None, если не указана. Конец раньше
    начала — ночная смена, это не ошибка."""
    text = _text(raw, 64)
    if not text:
        return None
    match = _SHIFT_RE.match(text)
    if not match:
        raise DisciplineError('Смена — время начала и конца, например 09:00–18:00', code='SHIFT_FORMAT')
    start, end = _clock(*match.group(1, 2)), _clock(*match.group(3, 4))
    if start == end:
        raise DisciplineError('Начало и конец смены совпадают', code='SHIFT_FORMAT')
    return start + SHIFT_DASH + end


def _timecodes_value(raw):
    """Тайм-код записи экрана — «14:32–14:41, 15:05»; None, если не указан."""
    text = _text(raw, 500)
    if not text:
        return None
    fragments = []
    for part in re.split(r'\s*[,;\n]\s*', text):
        if not part.strip():
            continue
        match = _TIMECODE_RE.match(part.strip())
        if not match:
            raise DisciplineError('Тайм-код — время на записи экрана, например 14:32–14:41', code='EVIDENCE_NOTE')
        start = _clock(*match.group(1, 2))
        end = _clock(*match.group(3, 4)) if match.group(3) is not None else None
        fragments.append(start + SHIFT_DASH + end if end and end != start else start)
    if len(fragments) > MAX_TIMECODES:
        raise DisciplineError('Тайм-код — не больше %d фрагментов записи' % MAX_TIMECODES, code='EVIDENCE_NOTE')
    return ', '.join(fragments) or None


_CLIENT_TOKEN = re.compile(r'[A-Za-z0-9_-]{8,64}')


def client_token(payload):
    """Метка отправки формы фиксации — одна на заполнение формы."""
    raw = str((payload or {}).get('client_token') or '').strip()
    return raw if _CLIENT_TOKEN.fullmatch(raw) else None


# ─────────────────────────────────────────────────────────────────────────────
# Настройки и лестница
# ─────────────────────────────────────────────────────────────────────────────

class SettingsCache:
    """Настройки отделов на один запрос. С моментом at — с окном и лестницей,
    которые действовали тогда: запись считается по правилам дня, когда её
    зафиксировали (catalog.COUNTING_KEYS)."""

    def __init__(self, cursor):
        self.cursor = cursor
        self.cache = {}
        self.versions = {}

    def __call__(self, department_id, at=None):
        key = int(department_id) if department_id is not None else None
        if key not in self.cache:
            self.cache[key] = (queries.get_settings(self.cursor, key) if key is not None
                               else catalog.normalize_settings({}))
        current = self.cache[key]
        if at is None or key is None:
            return current
        if key not in self.versions:
            versions = []
            for valid_from, rules in queries.counting_versions(self.cursor, key):
                try:
                    versions.append((valid_from, catalog.normalize_settings(rules, base=current)))
                except catalog.SettingsError:
                    continue
            self.versions[key] = versions
        versions = self.versions[key]
        # После последней правки действуют текущие настройки.
        if not versions or at >= versions[-1][0]:
            return current
        return catalog.rules_at(versions, at) or current


def save_counting_change(cursor, department_id, before, after, *, now, user_id):
    """Окно или лестница поменялись: с этого момента — новые правила, у прежних
    записей — свои. Первая правка заодно записывает, что действовало до неё."""
    old_rules, new_rules = catalog.counting_rules(before), catalog.counting_rules(after)
    if old_rules == new_rules:
        return False
    if not queries.counting_versions(cursor, department_id):
        queries.add_counting_version(cursor, department_id, old_rules, COUNTING_EPOCH, None)
    # С секунды после сохранения: время записей — с точностью до секунды, и
    # запись, зафиксированная в ту же секунду до правки, считается по прежним.
    queries.add_counting_version(cursor, department_id, new_rules, now + timedelta(seconds=1), user_id)
    return True


def operator_steps(cursor, operator_id, settings_for, since=None):
    history = queries.operator_histories(cursor, [operator_id], since=since).get(int(operator_id), [])
    return history, catalog.compute_steps(history, settings_for)


def shift_label_at(cursor, operator_id, moment):
    """Смена из графика на момент нарушения: «09:00–18:00». Ночная смена
    вчерашнего дня, ушедшая за полночь, тоже покрывает момент."""
    shifts = queries.shifts_around(cursor, operator_id, moment.date())
    fallback = None
    for shift in shifts:
        start = datetime.combine(shift['shift_date'], shift['start'])
        end = datetime.combine(shift['shift_date'], shift['end'])
        if end <= start:
            end += timedelta(days=1)
        label = '%s–%s' % (shift['start'].strftime('%H:%M'), shift['end'].strftime('%H:%M'))
        if start <= moment <= end:
            return label
        if shift['shift_date'] == moment.date() and fallback is None:
            fallback = label
    return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Вычислимые поля записи
# ─────────────────────────────────────────────────────────────────────────────

def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%dT%H:%M:%S')
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, time):
        return value.strftime('%H:%M')
    return value


def enrich(cursor, ctx, records, now, *, settings_for=None):
    """Номер, ступень, статус, просрочка, акт и права — к каждой записи."""
    if not records:
        return []
    settings_for = settings_for or SettingsCache(cursor)
    earliest = min(r['occurred_at'] for r in records) - timedelta(days=HISTORY_DAYS)
    histories = queries.operator_histories(cursor, {r['operator_id'] for r in records}, since=earliest)
    steps = {}
    for history in histories.values():
        steps.update(catalog.compute_steps(history, settings_for))
    operator_acts = queries.acts_by_record(cursor, [r['id'] for history in histories.values() for r in history])
    triggers, members = _act_sets(operator_acts)
    acts = {r['id']: operator_acts[r['id']] for r in records if r['id'] in operator_acts}
    controls = queries.controls_by_ids(cursor, [r.get('control_id') for r in records])
    result = []
    for record in records:
        info = steps.get(record['id']) or {}
        action = catalog.effective_step(info.get('action'), controls.get(record.get('control_id')), record['id'],
                                        steps, triggers, members)
        act = acts.get(record['id'])
        act_has_file = bool(act and act.get('has_file'))
        under_control = record.get('control_id') is not None
        settings = settings_for(record.get('department_id'))
        state = catalog.record_state(record, action, act_has_file=act_has_file, under_control=under_control)
        blockers = catalog.close_blockers(record, action, act_has_file=act_has_file, under_control=under_control)
        due = catalog.explanation_due(record, settings) if catalog.needs_explanation(record, action) else None
        item = dict(record)
        item.update({
            'ordinal': info.get('ordinal'),
            'weighted': info.get('weighted'),
            'action': action,
            'explanation_no': info.get('explanation_no'),
            'step_title': catalog.step_title(action, info.get('explanation_no')),
            # Ступень по лестнице до поправки на контроль — для истории и отчётов.
            'ladder_action': info.get('action'),
            # Номер ступени «акт» в лестнице отдела: по нему клиент красит шапку
            # «за шаг до акта», не зная самой лестницы.
            'act_step': catalog.act_step(settings.get('ladder')),
            'needs_explanation_now': catalog.needs_explanation(record, action),
            'state': state,
            'state_label': catalog.STATE_LABELS.get(state, ''),
            'state_tone': catalog.STATE_TONES.get(state, 'slate'),
            'explanation_due_at': due,
            'explanation_overdue': catalog.explanation_overdue(record, action, settings, now),
            'under_control': under_control,
            'close_blockers': [{'code': code, 'label': catalog.CLOSE_BLOCKERS[code]} for code in blockers],
            'act': _act_brief(act, record, steps),
            'permissions': {
                'can_edit': access.can_edit_record(ctx, record),
                'can_cancel': access.can_cancel_record(ctx, record),
                'can_close': access.can_close_record(ctx, record, under_control=under_control),
                'can_work': access.can_work_record(ctx, record),
                'can_upload_explanation': access.can_upload_explanation(ctx, record),
                'can_propose_measure': access.can_propose_measure(ctx, record, under_control=under_control),
                'can_approve_measure': access.can_approve_measure(ctx, record),
                'can_manage': access.manages_record(ctx, record),
            },
        })
        result.append(item)
    return result


def _act_brief(act, record, steps):
    if not act:
        return None
    trigger = act.get('trigger_record_id')
    trigger_info = steps.get(trigger) or {}
    basis_ok = trigger_info.get('action') == catalog.STEP_ACT
    return {
        'id': act['id'],
        'number': act['number'],
        'has_file': bool(act.get('has_file')),
        'is_trigger': trigger == record['id'],
        'composed_on': _iso(act.get('composed_on')),
        'approved_at': _iso(act.get('approved_at')),
        'decision_title': act.get('decision_title'),
        # ТЗ п. 7.2: «если в результате отмены у оператора стало меньше трёх
        # нарушений, а акт уже был создан — задача «Проверить основание акта»».
        'basis_check': not basis_ok,
    }


def public_record(item, ctx):
    """Что уходит клиенту. Оператору — только тип, дата, статус, мера (п. 3)."""
    data = {key: _iso(value) for key, value in item.items()}
    if access.can_see_internal(ctx, item):
        return data
    keep = ('id', 'number', 'operator_id', 'operator_name', 'occurred_at', 'type_title', 'state', 'state_label',
            'state_tone', 'step_title', 'action', 'explanation_no', 'ordinal', 'needs_explanation_now',
            'explanation_due_at', 'explanation_overdue', 'explanation_received_at', 'refused_at',
            'measure_title', 'measure_state', 'measure_amount', 'measure_percent', 'closed_at', 'cancelled_at')
    result = {key: data.get(key) for key in keep}
    if item.get('measure_state') != 'approved':
        # П. 3: оператор видит «применённую меру» — предложенную СВ, пока её не
        # утвердил руководитель, он не видит (руководитель может её сменить).
        for key in ('measure_title', 'measure_state', 'measure_amount', 'measure_percent'):
            result[key] = None
    result['permissions'] = {
        'can_upload_explanation': item['permissions'].get('can_upload_explanation'),
    }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Плашка у поля «Оператор» (ТЗ п. 6.2)
# ─────────────────────────────────────────────────────────────────────────────

def operator_context(cursor, ctx, operator_id, at, today, *, type_weight=1):
    profile = queries.operator_profiles(cursor, [operator_id], today).get(int(operator_id))
    if not access.operator_in_scope(ctx, profile):
        raise DisciplineError('Этот сотрудник вне вашего доступа', 403)
    department_id = profile['department_id']
    settings = queries.get_settings(cursor, department_id)
    history = queries.operator_histories(cursor, [operator_id]).get(int(operator_id), [])
    step = catalog.next_step(history, at, settings, new_weight=type_weight, settings_for=SettingsCache(cursor))
    control = queries.active_controls(cursor, [operator_id]).get(int(operator_id))
    if control and control['start_date'] > at.date():
        control = None
    plate = catalog.plate(step, control)
    group = queries.group_at(cursor, operator_id, at.date())
    return {
        'operator': {
            'id': profile['id'], 'name': profile['name'], 'department_id': department_id,
            'department_name': profile.get('department_name'),
            'group_name': (group or {}).get('group_name') or profile.get('group_name'),
            'direction_name': (group or {}).get('direction_name') or profile.get('direction_name'),
        },
        'shift_label': shift_label_at(cursor, operator_id, at),
        'plate': plate,
        'step': {'action': step['action'], 'ordinal': step['ordinal'],
                 'explanation_no': step['explanation_no'],
                 'title': catalog.step_title(step['action'], step['explanation_no'])},
        'previous': [{'id': r['id'], 'occurred_at': _iso(r['occurred_at']), 'type_title': r.get('type_title')}
                     for r in reversed(step['window'])],
        'control': {'id': control['id'], 'end_date': _iso(control['end_date']), 'reason': control['reason']}
        if control else None,
        'measure_required': bool(control),
        'settings': {'period_kind': settings['period_kind'], 'period_days': settings['period_days']},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Фиксация
# ─────────────────────────────────────────────────────────────────────────────

def _measure_values(measure, payload):
    amount = percent = None
    if measure['value_kind'] == 'amount':
        try:
            amount = round(float(re.sub(r'\s', '', str(payload.get('measure_amount') or '')).replace(',', '.')), 2)
        except ValueError:
            amount = None
        if not amount or amount <= 0:
            raise DisciplineError('Укажите сумму меры', code='MEASURE_AMOUNT')
    elif measure['value_kind'] == 'percent':
        try:
            percent = round(float(re.sub(r'\s', '', str(payload.get('measure_percent') or '')).replace(',', '.')), 2)
        except ValueError:
            percent = None
        if not percent or not 0 < percent <= 100:
            raise DisciplineError('Укажите процент от 1 до 100', code='MEASURE_PERCENT')
    return amount, percent


def _measure_changes(cursor, ctx, record_department_id, measure_id, payload, *, manager, now):
    measure = queries.get_measure(cursor, measure_id) if measure_id else None
    if not measure or measure['is_archived'] or int(measure['department_id']) != int(record_department_id):
        raise DisciplineError('Выберите меру из справочника отдела')
    amount, percent = _measure_values(measure, payload)
    changes = {
        'measure_id': measure['id'], 'measure_title': measure['title'], 'measure_finance': measure['finance'],
        'measure_value_kind': measure['value_kind'], 'measure_amount': amount, 'measure_percent': percent,
        'measure_proposed_by': ctx['user_id'],
    }
    if manager:
        changes.update({'measure_state': 'approved', 'measure_approved_by': ctx['user_id'],
                        'measure_approved_at': now})
    elif not measure_needs_approval(measure):
        # П. 5.2: «Устное замечание» утверждения не требует — мера применена сразу.
        # Закрывает запись на контроле всё равно руководитель (п. 10.4).
        changes.update({'measure_state': 'approved', 'measure_approved_by': None, 'measure_approved_at': now})
    else:
        changes.update({'measure_state': 'proposed', 'measure_approved_by': None, 'measure_approved_at': None})
    return measure, changes


def measure_needs_approval(measure):
    """Мера от СВ ждёт руководителя, если так помечена в справочнике (п. 5.2).
    Мера с деньгами — всегда: в выгрузку на удержания она идёт только после
    утверждения (п. 10.4)."""
    return bool(measure.get('needs_approval')) or (measure.get('finance') or 'none') != 'none'


def _checked_moment(raw, now, *, required):
    """Момент нарушения. При фиксации пустое — «сейчас» (п. 6.1: по умолчанию —
    текущее); при правке пустое — ошибка, а не тихий перенос на «сейчас»."""
    if required and not str(raw or '').strip():
        raise DisciplineError('Укажите дату и время нарушения', code='OCCURRED_AT')
    try:
        moment = parse_moment(raw, now)
    except DisciplineError as exc:
        raise DisciplineError(exc.message, code='OCCURRED_AT') from None
    if moment > now + timedelta(minutes=5):
        raise DisciplineError('Нарушение не может быть в будущем', code='OCCURRED_AT')
    if moment < now - timedelta(days=MAX_BACKDATE_DAYS):
        raise DisciplineError('Нарушение старше года зафиксировать нельзя', code='OCCURRED_AT')
    return moment


def _checked_type(cursor, raw, department_id):
    try:
        type_id = int(raw)
    except (TypeError, ValueError):
        raise DisciplineError('Выберите тип нарушения', code='TYPE_ID') from None
    type_row = queries.get_type(cursor, type_id)
    if not type_row or type_row['is_archived'] or int(type_row['department_id']) != int(department_id):
        raise DisciplineError('Выберите тип нарушения из справочника', code='TYPE_ID')
    return type_row


def replayed_record(cursor, ctx, payload):
    """Повтор уже сохранённой отправки формы: ответ потерялся в сети, человек
    нажал «Зафиксировать» ещё раз. Возвращает ту запись — второе нарушение не
    фиксируется, иначе лестница шагнула бы дважды. Очередь на человека берётся
    здесь же, поэтому и две одновременные одинаковые отправки дают одну запись."""
    token = client_token(payload)
    if not token or not str(payload.get('operator_id') or '').isdigit():
        return None
    queries.lock_operator(cursor, int(payload['operator_id']))
    row = queries.record_by_client_token(cursor, ctx['user_id'], token)
    if not row or row['cancelled_at'] or int(row['operator_id']) != int(payload['operator_id']):
        return None
    # Повтор — та же отправка: тот же тип и момент. Метка из черновика, где после
    # сбоя вписали другое нарушение, повтором не считается.
    try:
        same_type = str(row.get('type_id')) == str(int(payload.get('type_id')))
        same_moment = bool(payload.get('occurred_at')) and row.get('occurred_at') == parse_moment(
            payload.get('occurred_at'), None)
    except (TypeError, ValueError, DisciplineError):
        return None
    return row if same_type and same_moment else None


def _fresh_token(cursor, ctx, payload):
    # Метка уже занята записью на другого человека (форму после сбоя
    # переписали) — новая запись сохраняется без метки.
    token = client_token(payload)
    if token and queries.record_by_client_token(cursor, ctx['user_id'], token):
        return None
    return token


def create_record(cursor, ctx, payload, uploads, *, now, today, base_url=None, outbox=None):
    """Запись о нарушении. uploads — уже проверенные и залитые доказательства:
    [{'name', 'content_type', 'size', 'sha256', 'bucket', 'blob_path'}]."""
    if not (access.is_manager(ctx) or access.is_supervisor(ctx)):
        raise DisciplineError('Фиксировать нарушения вам не разрешено', 403)
    operator_id = _int(payload.get('operator_id'), 'оператор')
    profile = queries.operator_profiles(cursor, [operator_id], today).get(operator_id)
    if not access.operator_in_scope(ctx, profile):
        raise DisciplineError('Этот сотрудник вне вашего доступа', 403)
    department_id = int(profile['department_id'])
    queries.lock_operator(cursor, operator_id)
    queries.ensure_department_defaults(cursor, department_id)
    before = _ladder_snapshot(cursor, operator_id)

    occurred_at = _checked_moment(payload.get('occurred_at'), now, required=False)
    type_row = _checked_type(cursor, payload.get('type_id'), department_id)
    type_title = type_row['title']
    if type_row['is_other']:
        other = _text(payload.get('other_title'), 200)
        if len(other) < 3:
            raise DisciplineError('Опишите нарушение — для «Иное» название вводится вручную', code='OTHER_TITLE')
        type_title = other

    description = _text(payload.get('description'))
    if len(description) < catalog.DESCRIPTION_MIN_CHARS:
        raise DisciplineError('Опишите обстоятельства подробнее — минимум %d символов' % catalog.DESCRIPTION_MIN_CHARS,
                              code='DESCRIPTION')
    evidence_note = _timecodes_value(payload.get('evidence_note'))
    if len(uploads or ()) > catalog.FILE_KINDS['evidence']['max_files']:
        raise DisciplineError('Доказательств — не больше %d файлов' % catalog.FILE_KINDS['evidence']['max_files'],
                              code='EVIDENCE')
    if type_row['needs_evidence'] and not uploads and not evidence_note:
        raise DisciplineError('Для этого типа нужно доказательство: файл или тайм-код записи экрана', code='EVIDENCE')

    control = queries.active_controls(cursor, [operator_id]).get(operator_id)
    if control and control['start_date'] > occurred_at.date():
        control = None
    manager = access.can_manage_department(ctx, department_id)
    measure_changes = {}
    if control:
        measure_id = payload.get('measure_id')
        if not measure_id:
            raise DisciplineError('Оператор на особом контроле — выберите меру', code='MEASURE_REQUIRED')
        _measure, measure_changes = _measure_changes(cursor, ctx, department_id, _int(measure_id, 'мера'),
                                                     payload, manager=manager, now=now)

    group = queries.group_at(cursor, operator_id, occurred_at.date()) or {}
    shift_label = _shift_value(payload.get('shift_label')) or shift_label_at(cursor, operator_id, occurred_at)
    if not shift_label:
        # П. 6.1: поле обязательное. Подтягивается из графика; нет смены в
        # графике на это время — её вписывает тот, кто фиксирует.
        raise DisciplineError('Укажите смену — в графике на это время её нет', code='SHIFT_REQUIRED')
    number = queries.next_number(cursor, 'Д', now.year)
    data = {
        'number': number,
        'operator_id': operator_id,
        'department_id': department_id,
        'group_id': group.get('group_id'),
        'group_name': group.get('group_name') or profile.get('group_name'),
        'direction_name': group.get('direction_name') or profile.get('direction_name'),
        'shift_label': shift_label,
        'occurred_at': occurred_at,
        'type_id': type_row['id'],
        'type_title': type_title,
        'weight': int(type_row['weight'] or 1),
        'needs_explanation': bool(type_row['needs_explanation']),
        'description': description,
        'evidence_note': evidence_note,
        'control_id': control['id'] if control else None,
        'client_token': _fresh_token(cursor, ctx, payload),
        'created_by': ctx['user_id'],
        'created_at': now,
        'updated_at': now,
    }
    data.update(measure_changes)
    record_id = queries.insert_record(cursor, data)
    for upload in uploads or ():
        queries.insert_file(cursor, _file_row(upload, 'evidence', ctx, record_id=record_id, now=now))

    settings_for = SettingsCache(cursor)
    settings = settings_for(department_id)
    history, steps = operator_steps(cursor, operator_id, settings_for)
    info = dict(steps.get(record_id) or {})
    # Под контролем после акта и в периоде, уже покрытом актом, новый акт не
    # составляется — нужна мера или объяснительная (п. 7.3, 18.2).
    actions, _acts = operator_actions(cursor, history, steps)
    info['action'] = actions.get(record_id, info.get('action'))
    record = dict(data, id=record_id)
    queries.audit(cursor, ctx, 'create_record', 'record', entity_id=record_id, operator_id=operator_id,
                  department_id=department_id, record_id=record_id, changes={
                      'number': number, 'type': type_title, 'occurred_at': occurred_at,
                      'step': catalog.step_title(info.get('action'), info.get('explanation_no')),
                      'ordinal': info.get('ordinal'), 'files': len(uploads or ()),
                      'measure': measure_changes.get('measure_title')})

    settle_ladder(cursor, ctx, operator_id, before, since=occurred_at, now=now, today=today,
                  base_url=base_url, outbox=outbox, new_record_id=record_id)
    _notify_new_record(cursor, ctx, record, info, profile, settings, control, now=now, today=today,
                       base_url=base_url, outbox=outbox)
    return record_id


def _file_row(upload, kind, ctx, *, record_id=None, act_id=None, department_id=None, now=None):
    return {
        'record_id': record_id, 'act_id': act_id, 'department_id': department_id, 'kind': kind,
        'file_name': upload['name'], 'content_type': upload.get('content_type'),
        'size_bytes': int(upload.get('size') or 0), 'sha256': upload.get('sha256'),
        'bucket': upload.get('bucket'), 'blob_path': upload['blob_path'], 'uploaded_by': ctx['user_id'],
        'uploaded_at': now or now_almaty(),
    }


def _act_sets(acts_map):
    """Из {запись: акт}: записи-основания актов и записи, вошедшие в акты."""
    acts = {act['id']: act for act in (acts_map or {}).values()}
    triggers = {int(act['trigger_record_id']) for act in acts.values()}
    members = set()
    for act in acts.values():
        members.update(int(record_id) for record_id in act.get('record_ids') or ())
    return triggers, members - triggers


def operator_actions(cursor, history, steps):
    """Действующая ступень каждой неотменённой записи человека — с поправкой на
    контроль после акта и на период, уже покрытый актом (catalog.effective_step)."""
    controls = queries.controls_by_ids(cursor, [r.get('control_id') for r in history])
    acts_map = queries.acts_by_record(cursor, [r['id'] for r in history])
    triggers, members = _act_sets(acts_map)
    actions = {}
    for record in history:
        if record['cancelled']:
            continue
        info = steps.get(record['id']) or {}
        actions[record['id']] = catalog.effective_step(info.get('action'), controls.get(record.get('control_id')),
                                                       record['id'], steps, triggers, members)
    return actions, acts_map


def sync_acts(cursor, ctx, operator_id, *, since, settings_for, now):
    """Черновик акта каждой записи на ступени «акт», у которой его ещё нет (п. 9).

    Смотрим записи с момента since: запись задним числом сдвигает номера тех,
    что позже неё, и одна из них могла дойти до акта. Записи идут по времени:
    составленный акт покрывает свой период, и запись в том же окне второго акта
    уже не получает."""
    history, steps = operator_steps(cursor, operator_id, settings_for)
    created = {}
    controls = queries.controls_by_ids(cursor, [r.get('control_id') for r in history])
    triggers, members = _act_sets(queries.acts_by_record(cursor, [r['id'] for r in history]))
    for record in history:
        if record['cancelled'] or record['occurred_at'] < since or record['id'] in triggers:
            continue
        info = steps.get(record['id']) or {}
        action = catalog.effective_step(info.get('action'), controls.get(record.get('control_id')), record['id'],
                                        steps, triggers, members)
        if action != catalog.STEP_ACT:
            continue
        number = queries.next_number(cursor, 'А', now.year)
        act_id = queries.insert_act(cursor, number=number, operator_id=operator_id,
                                    department_id=record.get('department_id'), trigger_record_id=record['id'],
                                    record_ids=info['window_ids'], created_by=ctx.get('user_id'))
        if act_id:
            created[record['id']] = act_id
            triggers.add(record['id'])
            members.update(rid for rid in info['window_ids'] if rid != record['id'])
            queries.audit(cursor, ctx, 'act_draft', 'act', entity_id=act_id, operator_id=operator_id,
                          department_id=record.get('department_id'), record_id=record['id'],
                          changes={'number': number, 'records': len(info['window_ids'])})
    return created


def _auto_control(cursor, ctx, record, info, act_id, settings, *, now, today):
    """Ступень «акт» ставит человека на особый контроль сама (п. 7.3, критерий 4).
    Возвращает (контроль, записи, попавшие под него задним числом)."""
    start = record['occurred_at'].date()
    end = start + timedelta(days=int(settings.get('control_days') or 30))
    heads = queries.department_heads(cursor, [record['department_id']])
    act_number = None
    if act_id:
        act = queries.get_act(cursor, act_id)
        act_number = act['number'] if act else None
    count = int(info.get('ordinal') or 0)
    reason = '%d %s за период' % (count, catalog.plural_violations(count))
    if act_number:
        reason = 'Акт %s: %s' % (act_number, reason)
    control_id = queries.insert_control(cursor, {
        'operator_id': record['operator_id'], 'department_id': record['department_id'], 'source': 'act',
        'reason': reason, 'act_id': act_id, 'start_date': start, 'end_date': end,
        'responsible_id': heads.get(record['department_id']), 'created_by': ctx['user_id'],
        'created_at': now, 'updated_at': now,
    })
    # Акт встал раньше уже зафиксированных нарушений (запись задним числом): они
    # — в периоде контроля, им нужна мера (п. 7.3, 10.4).
    attached = queries.attach_to_control(cursor, record['operator_id'], control_id, record['occurred_at'],
                                         record['id'], end)
    queries.audit(cursor, ctx, 'control_auto', 'control', entity_id=control_id, operator_id=record['operator_id'],
                  department_id=record['department_id'], record_id=record['id'],
                  changes={'start_date': start, 'end_date': end, 'reason': reason,
                           'records': len(attached) or None})
    return control_id, attached


def _link_act_to_control(cursor, ctx, control, record, act_id, settings, *, now):
    """Акт во время испытательного срока или ручного контроля. Действующий
    контроль у человека один, поэтому акт привязывается к нему: основание —
    «После акта» (снять испытательный срок, не заметив, что на нём держится
    акт, теперь нельзя), начало — не позже акта, конец — не раньше срока «после
    акта» (п. 7.3, 10.3). Контроль, по которому уже приняли решение, не трогаем.
    Возвращает (привязан ли, записи, попавшие под контроль задним числом)."""
    locked = queries.get_control(cursor, control['id'], for_update=True)
    if not locked or locked.get('decided_at') or locked.get('act_id'):
        return False, []
    act = queries.get_act(cursor, act_id) or {}
    act_day = record['occurred_at'].date()
    start = min(locked['start_date'], act_day)
    end = max(locked['end_date'], act_day + timedelta(days=int(settings.get('control_days') or 30)))
    was = locked.get('reason') or catalog.CONTROL_SOURCE_LABELS.get(locked.get('source'), '')
    reason = ('Акт %s · %s' % (act.get('number') or '', was)).strip(' ·')[:1000]
    queries.update_control(cursor, locked['id'], {'act_id': act_id, 'source': 'act', 'start_date': start,
                                                  'end_date': end, 'reason': reason})
    attached = queries.attach_to_control(cursor, record['operator_id'], locked['id'], record['occurred_at'],
                                         record['id'], end)
    queries.audit(cursor, ctx, 'control_act_linked', 'control', entity_id=locked['id'],
                  operator_id=record['operator_id'], department_id=record.get('department_id'),
                  record_id=record['id'], changes={
                      'act': act.get('number'),
                      'source': [catalog.CONTROL_SOURCE_LABELS.get(locked.get('source')),
                                 catalog.CONTROL_SOURCE_LABELS.get('act')],
                      'start_date': [locked['start_date'], start], 'end_date': [locked['end_date'], end],
                      'records': len(attached) or None})
    return True, attached


def _ladder_snapshot(cursor, operator_id):
    """Ступени записей человека и основания его актов сейчас — чтобы после
    фиксации, правки или отмены уведомлять только о том, что поменялось."""
    settings_for = SettingsCache(cursor)
    history, steps = operator_steps(cursor, operator_id, settings_for)
    actions, acts_map = operator_actions(cursor, history, steps)
    acts = {act['id']: act for act in acts_map.values()}
    basis = {act_id: (steps.get(act['trigger_record_id']) or {}).get('action') == catalog.STEP_ACT
             for act_id, act in acts.items()}
    return {'history': {r['id']: r for r in history}, 'steps': steps, 'actions': actions, 'acts': acts,
            'basis': basis}


def _push_act_basis(cursor, outbox, act, body, settings_for, base_url):
    """«Проверить основание акта» — одна непрочитанная на акт: прежняя гаснет."""
    queries.clear_kind(cursor, record_id=act['trigger_record_id'], kinds=['act_basis'])
    managers = queries.department_managers(cursor, [act.get('department_id')])
    notify.push(cursor, outbox, event='act_basis', user_ids=managers, settings=settings_for(act.get('department_id')),
                title='Проверить основание акта %s' % act['number'], body=body,
                record_id=act['trigger_record_id'], tone='warning', base_url=base_url)


def settle_ladder(cursor, ctx, operator_id, before, *, since, now, today, base_url=None, outbox=None,
                  new_record_id=None, changed_record_id=None):
    """Всё, что следует из новых ступеней человека после фиксации, правки или отмены.

    * Запись, дошедшая до ступени «акт», получает черновик акта, а человек —
      особый контроль (критерий 4), даже если до акта её довела запись задним
      числом или правка соседней. Акт во время испытательного срока или ручного
      контроля привязывается к этому контролю. Уже зафиксированные позже акта
      нарушения попадают под контроль — им нужна мера.
    * Записи, которой теперь нужна объяснительная, — оператору «нужна
      объяснительная» со сроком от этого момента; которой больше не нужна —
      эти уведомления гаснут.
    * Акт, у которого пропало основание, или в период которого задним числом
      вошло нарушение, — руководителю «Проверить основание акта» (п. 7.2).
      Только при переходе; основание вернулось — задача гаснет.

    Саму новую запись (new_record_id) уведомляет _notify_new_record."""
    settings_for = SettingsCache(cursor)
    created = sync_acts(cursor, ctx, operator_id, since=since, settings_for=settings_for, now=now)
    after = _ladder_snapshot(cursor, operator_id)
    history = after['history']
    name = queries.user_names(cursor, [operator_id]).get(int(operator_id)) or 'Сотрудник'
    me = ctx.get('user_id')
    for record_id, act_id in sorted(created.items(), key=lambda item: (history[item[0]]['occurred_at'], item[0])):
        record = history[record_id]
        settings = settings_for(record.get('department_id'))
        info = after['steps'].get(record_id) or {}
        managers = queries.department_managers(cursor, [record.get('department_id')])
        control = queries.active_controls(cursor, [operator_id]).get(int(operator_id))
        attached = []
        if control and not control.get('act_id'):
            linked, attached = _link_act_to_control(cursor, ctx, control, record, act_id, settings, now=now)
            if not linked:
                control = None
        if not control:
            _control_id, attached = _auto_control(cursor, ctx, record, info, act_id, settings, now=now, today=today)
        for other_id in attached:
            other = history.get(other_id) or {}
            notify.push(cursor, outbox, event='under_control', user_ids=managers, settings=settings,
                        title='%s: нарушение на особом контроле' % name,
                        body='%s. Акт составлен по более раннему нарушению — нужна мера' % notify.describe(other),
                        record_id=other_id, tone='warning', base_url=base_url, exclude=[me])
        if record_id != new_record_id:
            notify.push(cursor, outbox, event='act', user_ids=managers, settings=settings,
                        title='%s: %s — нужен акт' % (name, notify.ordinal_text(info).lower()),
                        body='%s. Ступень сдвинулась после записи задним числом или правки'
                             % notify.describe(record),
                        record_id=record_id, tone='warning', base_url=base_url, exclude=[me])
    for record_id, action in after['actions'].items():
        if record_id == new_record_id or record_id not in before['actions']:
            continue
        record = history[record_id]
        previous = before['history'].get(record_id) or record
        if record.get('closed_at') or catalog.explanation_done(record):
            continue
        # «Было» — по прежней записи: правка типа меняет и сам признак «нужна объяснительная».
        was = catalog.needs_explanation(previous, before['actions'][record_id])
        now_needed = catalog.needs_explanation(record, action)
        if now_needed and not was:
            queries.update_record(cursor, record_id, {'explanation_requested_at': now})
            record = dict(record, explanation_requested_at=now)
            settings = settings_for(record.get('department_id'))
            due = catalog.explanation_due(record, settings)
            notify.push(cursor, outbox, event='explanation_required', user_ids=[operator_id], settings=settings,
                        title='Нужна объяснительная',
                        body='%s. Срок — до %s' % (notify.describe(record), due.strftime('%d.%m %H:%M') if due else ''),
                        record_id=record_id, tone='warning', base_url=base_url, exclude=[me])
        elif was and not now_needed:
            queries.clear_kind(cursor, record_id=record_id, kinds=['explanation_required', 'explanation_overdue'])
    changed = changed_record_id or new_record_id
    for act_id, act in after['acts'].items():
        ok, was_ok = after['basis'].get(act_id), before['basis'].get(act_id)
        if was_ok and not ok:
            _push_act_basis(cursor, outbox, act, 'Нарушений за период стало меньше порога акта: запись отменена '
                                                 'или изменена', settings_for, base_url)
            continue
        if ok and was_ok is False:
            queries.clear_kind(cursor, record_id=act['trigger_record_id'], kinds=['act_basis'])
        window = (after['steps'].get(act['trigger_record_id']) or {}).get('window_ids') or ()
        if changed and changed in window and changed not in (act.get('record_ids') or ()) and changed in history:
            _push_act_basis(cursor, outbox, act, 'В период акта задним числом вошло нарушение %s — в составе акта '
                                                 'его нет' % (history[changed].get('number') or ''),
                            settings_for, base_url)


def _notify_new_record(cursor, ctx, record, info, profile, settings, control, *, now, today, base_url, outbox):
    record_id = record['id']
    action = info.get('action')
    name = profile.get('name') or 'Сотрудник'
    line = notify.describe(record)
    me = ctx['user_id']
    # Оператору — одна строка, а не две: «нужна объяснительная» уже говорит,
    # что нарушение зафиксировано.
    if catalog.needs_explanation(record, action):
        due = catalog.explanation_due(record, settings)
        notify.push(cursor, outbox, event='explanation_required', user_ids=[record['operator_id']],
                    settings=settings, title='Нужна объяснительная',
                    body='%s. Срок — до %s' % (line, due.strftime('%d.%m %H:%M') if due else ''),
                    record_id=record_id, tone='warning', base_url=base_url, exclude=[me])
    else:
        notify.push(cursor, outbox, event='recorded', user_ids=[record['operator_id']], settings=settings,
                    title='Зафиксировано нарушение', body=line, record_id=record_id, base_url=base_url,
                    exclude=[me])
    managers = queries.department_managers(cursor, [record['department_id']])
    ladder = settings.get('ladder') or []
    act_index = catalog.act_step(ladder)
    if control:
        body = ('%s. Мера применена (%s) — закройте запись' % (line, record.get('measure_title') or '')
                if record.get('measure_state') == 'approved'
                else '%s. Мера ждёт утверждения' % line)
        notify.push(cursor, outbox, event='under_control', user_ids=managers, settings=settings,
                    title='%s: нарушение на особом контроле' % name, body=body, record_id=record_id,
                    tone='warning', base_url=base_url, exclude=[me])
    if action == catalog.STEP_ACT:
        notify.push(cursor, outbox, event='act', user_ids=managers, settings=settings,
                    title='%s: %s — нужен акт' % (name, notify.ordinal_text(info).lower()),
                    body=line, record_id=record_id, tone='warning', base_url=base_url, exclude=[me])
    elif act_index and info.get('weighted') == act_index - 1:
        supervisors = queries.group_supervisors(cursor, [record.get('group_id')], today)
        notify.push(cursor, outbox, event='pre_act', user_ids=supervisors, settings=settings,
                    title='%s: следующее нарушение — акт' % name, body=line, record_id=record_id,
                    base_url=base_url, exclude=[me])


# ─────────────────────────────────────────────────────────────────────────────
# Работа с записью
# ─────────────────────────────────────────────────────────────────────────────

def load_record(cursor, ctx, record_id, today, *, for_update=False):
    record = queries.get_record(cursor, record_id, today)
    if not record:
        raise DisciplineError('Запись не найдена', 404)
    if not access.can_view_record(ctx, record):
        raise DisciplineError('Запись вне вашего доступа', 403)
    if for_update:
        # Правка записи меняет лестницу человека — та же очередь, что у фиксации,
        # и запись перечитывается уже под блокировкой.
        queries.lock_operator(cursor, record['operator_id'])
        record = queries.get_record(cursor, record_id, today, for_update=True)
    return record


def record_step(cursor, record):
    settings_for = SettingsCache(cursor)
    history, steps = operator_steps(cursor, record['operator_id'], settings_for)
    info = dict(steps.get(record['id']) or {})
    actions, _acts = operator_actions(cursor, history, steps)
    if record['id'] in actions:
        info['action'] = actions[record['id']]
    return info, settings_for


def edit_record(cursor, ctx, record, payload, *, now, today=None, base_url=None, outbox=None):
    if not access.can_edit_record(ctx, record):
        raise DisciplineError('Править эту запись вам не разрешено', 403)
    changes = {}
    if 'occurred_at' in payload:
        occurred_at = _checked_moment(payload.get('occurred_at'), now, required=True)
        changes['occurred_at'] = occurred_at
        if occurred_at.date() != record['occurred_at'].date():
            # Группа и линия в записи — снимок на день нарушения: новый день — новый снимок.
            group = queries.group_at(cursor, record['operator_id'], occurred_at.date()) or {}
            changes.update({'group_id': group.get('group_id'), 'group_name': group.get('group_name'),
                            'direction_name': group.get('direction_name')})
    if 'type_id' in payload:
        type_row = _checked_type(cursor, payload.get('type_id'), record['department_id'])
        title = type_row['title']
        if type_row['is_other']:
            title = _text(payload.get('other_title'), 200)
            if len(title) < 3:
                raise DisciplineError('Для «Иное» название вводится вручную', code='OTHER_TITLE')
        changes.update({'type_id': type_row['id'], 'type_title': title, 'weight': int(type_row['weight'] or 1),
                        'needs_explanation': bool(type_row['needs_explanation'])})
    if 'description' in payload:
        description = _text(payload.get('description'))
        if len(description) < catalog.DESCRIPTION_MIN_CHARS:
            raise DisciplineError('Опишите обстоятельства подробнее — минимум %d символов'
                                  % catalog.DESCRIPTION_MIN_CHARS, code='DESCRIPTION')
        changes['description'] = description
    # Смену и тайм-код, набранные до полей времени, правка оставляет как были —
    # проверяется только новое значение.
    if 'evidence_note' in payload:
        raw = _text(payload.get('evidence_note'), 500) or None
        changes['evidence_note'] = raw if raw == record.get('evidence_note') else _timecodes_value(raw)
    if 'shift_label' in payload:
        raw = _text(payload.get('shift_label'), 64) or None
        if not raw:
            raise DisciplineError('Укажите смену', code='SHIFT_REQUIRED')
        changes['shift_label'] = raw if raw == record.get('shift_label') else _shift_value(raw)
    diff = {key: [record.get(key), value] for key, value in changes.items() if record.get(key) != value}
    if not diff:
        return False
    moves_ladder = bool({'occurred_at', 'type_id', 'weight'} & set(diff))
    before = _ladder_snapshot(cursor, record['operator_id']) if moves_ladder else None
    queries.update_record(cursor, record['id'], {key: changes[key] for key in diff})
    queries.audit(cursor, ctx, 'update_record', 'record', entity_id=record['id'], operator_id=record['operator_id'],
                  department_id=record['department_id'], record_id=record['id'], changes=diff)
    if moves_ladder:
        since = min(record['occurred_at'], changes.get('occurred_at') or record['occurred_at'])
        settle_ladder(cursor, ctx, record['operator_id'], before, since=since, now=now,
                      today=today or now.date(), base_url=base_url, outbox=outbox, changed_record_id=record['id'])
    return True


def cancel_record(cursor, ctx, record, reason, *, now, base_url=None, outbox=None):
    if not access.can_cancel_record(ctx, record):
        raise DisciplineError('Отменить запись может только руководитель', 403)
    reason = _text(reason, 1000)
    if len(reason) < 5:
        raise DisciplineError('Укажите причину отмены')
    before = _ladder_snapshot(cursor, record['operator_id'])
    queries.update_record(cursor, record['id'], {'cancelled_at': now, 'cancelled_by': ctx['user_id'],
                                                  'cancel_reason': reason})
    queries.audit(cursor, ctx, 'cancel_record', 'record', entity_id=record['id'], operator_id=record['operator_id'],
                  department_id=record['department_id'], record_id=record['id'], changes={'reason': reason})
    queries.clear_kind(cursor, record_id=record['id'],
                       kinds=['explanation_required', 'explanation_overdue', 'recorded', 'act', 'pre_act',
                              'under_control'])
    settle_ladder(cursor, ctx, record['operator_id'], before, since=record['occurred_at'], now=now,
                  today=now.date(), base_url=base_url, outbox=outbox)


def close_record(cursor, ctx, record, *, now):
    info, settings_for = record_step(cursor, record)
    act = queries.acts_by_record(cursor, [record['id']]).get(record['id'])
    under_control = record.get('control_id') is not None
    if not access.can_close_record(ctx, record, under_control=under_control):
        raise DisciplineError('Закрыть эту запись может только руководитель' if under_control
                              else 'Закрыть эту запись вам не разрешено', 403)
    blockers = catalog.close_blockers(record, info.get('action'), act_has_file=bool(act and act.get('has_file')),
                                      under_control=under_control)
    if blockers:
        raise DisciplineError('Закрыть нельзя: ' + '; '.join(catalog.CLOSE_BLOCKERS[b].lower() for b in blockers),
                              code='CLOSE_BLOCKED')
    queries.update_record(cursor, record['id'], {'closed_at': now, 'closed_by': ctx['user_id']})
    queries.clear_kind(cursor, record_id=record['id'],
                       kinds=['explanation_required', 'explanation_overdue', 'under_control', 'act', 'pre_act'])
    queries.audit(cursor, ctx, 'close_record', 'record', entity_id=record['id'], operator_id=record['operator_id'],
                  department_id=record['department_id'], record_id=record['id'])


def set_comment(cursor, ctx, record, text):
    if not access.manages_record(ctx, record):
        raise DisciplineError('Комментарий пишет руководитель', 403)
    comment = _text(text, 2000) or None
    if comment == record.get('manager_comment'):
        return
    queries.update_record(cursor, record['id'], {'manager_comment': comment})
    queries.audit(cursor, ctx, 'manager_comment', 'record', entity_id=record['id'], operator_id=record['operator_id'],
                  department_id=record['department_id'], record_id=record['id'],
                  changes={'manager_comment': [record.get('manager_comment'), comment]})


def add_explanation(cursor, ctx, record, upload, *, now):
    if not access.can_upload_explanation(ctx, record):
        raise DisciplineError('Загрузить объяснительную вам не разрешено', 403)
    if record.get('refused_at'):
        raise DisciplineError('По записи уже составлен акт об отказе')
    queries.supersede_files(cursor, kind='explanation', record_id=record['id'])
    file_id = queries.insert_file(cursor, _file_row(upload, 'explanation', ctx, record_id=record['id'], now=now))
    if not record.get('explanation_received_at'):
        queries.update_record(cursor, record['id'], {'explanation_received_at': now})
    queries.clear_kind(cursor, record_id=record['id'], kinds=['explanation_required', 'explanation_overdue'])
    queries.audit(cursor, ctx, 'upload_explanation', 'file', entity_id=file_id, operator_id=record['operator_id'],
                  department_id=record['department_id'], record_id=record['id'],
                  changes={'file': upload['name'], 'sha256': upload.get('sha256'),
                           'version': 'replace' if record.get('explanation_received_at') else 'first'})
    return file_id


def record_refusal(cursor, ctx, record, upload, witness_ids, *, now, base_url=None, outbox=None):
    """Отказ дать объяснение (п. 8.2): акт об отказе и не меньше двух свидетелей.
    Нарушение засчитывается в счётчик наравне с остальными."""
    if not access.can_work_record(ctx, record):
        raise DisciplineError('Оформить отказ вам не разрешено', 403)
    if record.get('explanation_received_at'):
        raise DisciplineError('Объяснительная по записи уже получена')
    info, _settings_for = record_step(cursor, record)
    if not catalog.needs_explanation(record, info.get('action')):
        raise DisciplineError('На этой ступени объяснительная не нужна — отказ не оформляется')
    witnesses = sorted({int(w) for w in witness_ids or () if str(w).strip().isdigit()} - {int(record['operator_id'])})
    if len(witnesses) < catalog.REFUSAL_MIN_WITNESSES:
        raise DisciplineError('Укажите не менее двух свидетелей')
    names = queries.user_names(cursor, witnesses)
    if len(names) != len(witnesses):
        raise DisciplineError('Свидетели должны быть сотрудниками из справочника')
    queries.supersede_files(cursor, kind='refusal_act', record_id=record['id'])
    file_id = queries.insert_file(cursor, _file_row(upload, 'refusal_act', ctx, record_id=record['id'], now=now))
    queries.update_record(cursor, record['id'], {'refused_at': now, 'refusal_witness_ids': witnesses})
    queries.clear_kind(cursor, record_id=record['id'], kinds=['explanation_required', 'explanation_overdue'])
    queries.audit(cursor, ctx, 'refusal', 'record', entity_id=record['id'], operator_id=record['operator_id'],
                  department_id=record['department_id'], record_id=record['id'],
                  changes={'file_id': file_id, 'witnesses': [names[w] for w in witnesses]})
    settings = queries.get_settings(cursor, record['department_id'])
    managers = queries.department_managers(cursor, [record['department_id']])
    notify.push(cursor, outbox, event='refusal', user_ids=managers, settings=settings,
                title='%s: отказ от объяснения' % record.get('operator_name', 'Сотрудник'),
                body=notify.describe(record), record_id=record['id'], tone='warning', base_url=base_url,
                exclude=[ctx['user_id']])


def record_training(cursor, ctx, record, payload, *, now, today):
    """Ступень «Тренинг»: проведённое занятие ложится в «Тренинги» с причиной
    «Дисциплинарный тренинг» и идёт в часы, как любой тренинг. Пересечение с
    другим занятием этого дня — отказ: часы сотрудника задвоились бы."""
    if not access.can_work_record(ctx, record):
        raise DisciplineError('Отметить тренинг вам не разрешено', 403)
    info, _settings_for = record_step(cursor, record)
    if info.get('action') != catalog.STEP_TRAINING:
        raise DisciplineError('Тренинг на этой ступени лестницы не нужен')
    if record.get('training_id'):
        raise DisciplineError('Тренинг по записи уже отмечен')
    day = parse_day(payload.get('date'), 'Дата тренинга')
    start = parse_time(payload.get('start'), 'Начало тренинга')
    end = parse_time(payload.get('end'), 'Конец тренинга')
    if day > today:
        raise DisciplineError('Отмечается проведённый тренинг — дата не может быть в будущем')
    if day < record['occurred_at'].date():
        raise DisciplineError('Тренинг не может быть раньше нарушения')
    if end <= start:
        raise DisciplineError('Конец тренинга должен быть позже начала')
    training_id = None
    for item in queries.trainings_on_day(cursor, record['operator_id'], day):
        same = item['start'] == start and item['end'] == end
        overlap = max(item['start'], start) < min(item['end'], end)
        if same and item['reason'] == TRAINING_REASON:
            training_id = item['id']
            break
        if overlap:
            raise DisciplineError('В это время у сотрудника уже есть тренинг %s–%s'
                                  % (item['start'].strftime('%H:%M'), item['end'].strftime('%H:%M')))
    if training_id is None:
        training_id = queries.insert_training(
            cursor, operator_id=record['operator_id'], day=day, start=start, end=end, reason=TRAINING_REASON,
            comment='Дисциплина %s: %s' % (record['number'], record['type_title']), created_by=ctx['user_id'])
    queries.update_record(cursor, record['id'], {'training_id': training_id})
    queries.audit(cursor, ctx, 'training', 'record', entity_id=record['id'], operator_id=record['operator_id'],
                  department_id=record['department_id'], record_id=record['id'],
                  changes={'training_id': training_id, 'date': day, 'start': start, 'end': end})
    return training_id


def set_measure(cursor, ctx, record, payload, *, now, base_url=None, outbox=None):
    under_control = record.get('control_id') is not None
    manager = access.manages_record(ctx, record)
    if access.measure_locked(record):
        raise DisciplineError('Удержание утверждено и могло уйти в выгрузку — меру больше не меняют', 403,
                              code='MEASURE_LOCKED')
    if not manager and record.get('measure_state') == 'approved' and record.get('measure_approved_by'):
        raise DisciplineError('Мера уже утверждена руководителем — изменить её может только он', 403)
    if not access.can_propose_measure(ctx, record, under_control=under_control):
        raise DisciplineError('Назначает меру руководитель', 403)
    measure, changes = _measure_changes(cursor, ctx, record['department_id'],
                                        _int(payload.get('measure_id'), 'мера'), payload, manager=manager, now=now)
    queries.update_record(cursor, record['id'], changes)
    applied = changes.get('measure_state') == 'approved'
    queries.audit(cursor, ctx, 'measure_approve' if manager else ('measure_apply' if applied else 'measure_propose'),
                  'measure',
                  entity_id=record['id'], operator_id=record['operator_id'], department_id=record['department_id'],
                  record_id=record['id'], changes={
                      'measure': [record.get('measure_title'), measure['title']],
                      'amount': changes.get('measure_amount'), 'percent': changes.get('measure_percent')})
    if manager:
        _notify_finance(cursor, ctx, record, measure, changes, base_url=base_url, outbox=outbox)
    queries.clear_kind(cursor, record_id=record['id'], kinds=['under_control'] if manager else [])


def invalidate_file(cursor, ctx, row, record, reason, *, now, base_url=None, outbox=None):
    """Файл признан недействительным: он остаётся в истории (ничего не удаляется,
    п. 13), но больше ничего не подтверждает. Если это была последняя действующая
    объяснительная или акт об отказе открытой записи — запись снова ждёт
    объяснительную (п. 8.1: «получена» — только с прикреплённым файлом)."""
    if not queries.invalidate_file(cursor, row['id'], ctx['user_id'], reason):
        raise DisciplineError('Файл уже признан недействительным')
    queries.audit(cursor, ctx, 'invalidate_file', 'file', entity_id=row['id'],
                  operator_id=(record or {}).get('operator_id'),
                  department_id=(record or {}).get('department_id') or row.get('department_id'),
                  record_id=(record or {}).get('id'), changes={'file': row['file_name'], 'reason': reason})
    kind = row['kind']
    if (kind not in ('explanation', 'refusal_act') or not record or row.get('record_id') != record['id']
            or record.get('closed_at') or record.get('cancelled_at')):
        return
    current = [f for f in queries.files_for(cursor, record_ids=[record['id']], kind=kind)
               if not f.get('invalid_at') and not f.get('superseded_at')]
    if current:
        return
    # Признали недействительной исправленную версию — действующей снова
    # становится прежняя, если её саму не признавали недействительной.
    restored = queries.restore_previous_version(cursor, record['id'], kind)
    if restored:
        queries.audit(cursor, ctx, 'version_restored', 'file', entity_id=restored['id'],
                      operator_id=record['operator_id'], department_id=record['department_id'],
                      record_id=record['id'], changes={'file': restored['file_name']})
        return
    if kind == 'explanation':
        if not record.get('explanation_received_at'):
            return
        changes = {'explanation_received_at': None}
    else:
        if not record.get('refused_at'):
            return
        changes = {'refused_at': None, 'refusal_witness_ids': None}
    # Снова ждём объяснительную — только там, где она нужна на ступени записи.
    info, _settings_for = record_step(cursor, record)
    needed = catalog.needs_explanation(record, info.get('action'))
    if needed:
        changes['explanation_requested_at'] = now
    queries.update_record(cursor, record['id'], changes)
    queries.audit(cursor, ctx, 'explanation_reset', 'record', entity_id=record['id'],
                  operator_id=record['operator_id'], department_id=record['department_id'], record_id=record['id'],
                  changes={'file': row['file_name']})
    if not needed:
        return
    settings = queries.get_settings(cursor, record['department_id'])
    due = catalog.explanation_due(dict(record, explanation_requested_at=now), settings)
    notify.push(cursor, outbox, event='explanation_required', user_ids=[record['operator_id']], settings=settings,
                title='Нужна объяснительная', body='%s. Прежний файл признан недействительным: %s. Срок — до %s'
                % (notify.describe(record), reason, due.strftime('%d.%m %H:%M') if due else ''),
                record_id=record['id'], tone='warning', base_url=base_url, exclude=[ctx['user_id']])


def approve_measure(cursor, ctx, record, *, now, base_url=None, outbox=None):
    if not access.can_approve_measure(ctx, record):
        raise DisciplineError('Утверждает меру руководитель', 403)
    if record.get('measure_state') != 'proposed':
        raise DisciplineError('Мера не ждёт утверждения')
    queries.update_record(cursor, record['id'], {'measure_state': 'approved', 'measure_approved_by': ctx['user_id'],
                                                  'measure_approved_at': now})
    queries.audit(cursor, ctx, 'measure_approve', 'measure', entity_id=record['id'],
                  operator_id=record['operator_id'], department_id=record['department_id'], record_id=record['id'],
                  changes={'measure': record.get('measure_title')})
    queries.clear_kind(cursor, record_id=record['id'], kinds=['under_control'])
    measure = {'title': record.get('measure_title'), 'finance': record.get('measure_finance')}
    _notify_finance(cursor, ctx, record, measure, {'measure_amount': record.get('measure_amount'),
                                                   'measure_percent': record.get('measure_percent')},
                    base_url=base_url, outbox=outbox)


def _notify_finance(cursor, ctx, record, measure, changes, *, base_url, outbox):
    if (measure.get('finance') or 'none') == 'none':
        return
    value = money_text(changes.get('measure_amount'), changes.get('measure_percent'))
    value = (' · ' + value) if value else ''
    settings = queries.get_settings(cursor, record['department_id'])
    managers = queries.department_managers(cursor, [record['department_id']])
    notify.push(cursor, outbox, event='measure_finance', user_ids=managers, settings=settings,
                title='Мера с финансовым эффектом: %s%s' % (measure.get('title') or '', value),
                body='%s — попадёт в выгрузку на удержания' % record.get('operator_name', ''),
                record_id=record['id'], base_url=base_url, exclude=[ctx['user_id']])


# ─────────────────────────────────────────────────────────────────────────────
# Акт
# ─────────────────────────────────────────────────────────────────────────────

def load_act(cursor, ctx, act_id, today):
    act = queries.get_act(cursor, act_id)
    if not act:
        raise DisciplineError('Акт не найден', 404)
    record = load_record(cursor, ctx, act['trigger_record_id'], today)
    return act, record


def act_draft_context(cursor, ctx, act, trigger, *, settings_for):
    """Чего нет в самом акте и его записях, а стандартному акту нужно
    (act_docx.standard_act): отдел акта, кто его составил и на каком он месте
    («супервайзером» или «руководителем»), руководитель для строки «ознакомлен»,
    пол сотрудника для «допустил/допустила», испытательный срок — срок особого
    контроля, начатого актом, — и окно подсчёта, по которому акт составлен.

    Составитель — супервайзер, как в образце организации: СВ, зафиксировавший
    нарушение-основание, иначе СВ, который скачивает черновик; СВ нет —
    скачивающий (руководитель). Отметка «составил» в самом акте (composed_by)
    в документ не идёт: её ставит руководитель вместе с датой, и тогда он
    оказывался бы в акте и составителем, и ознакомленным руководителем.
    Руководитель — утвердивший акт, иначе глава отдела акта; если это и есть
    составитель, его строка остаётся линией для подписи."""
    department_id = act.get('department_id')
    department = (queries.department_names(cursor, [department_id]).get(int(department_id)) or {}
                  if department_id is not None else {})
    head_id = queries.department_heads(cursor, [department_id]).get(int(department_id)) \
        if department_id is not None else None
    candidates = [int(uid) for uid in (trigger.get('created_by'), ctx['user_id']) if uid is not None]
    leader_id = act.get('approved_by') or head_id
    people = queries.people(cursor, candidates + [leader_id, act['operator_id']])

    def supervisor(user_id):
        return access.normalize_role((people.get(user_id) or {}).get('role')) == 'sv' and user_id != head_id

    composer_id = next((uid for uid in candidates if supervisor(uid)), int(ctx['user_id']))
    settings = settings_for(department_id, trigger.get('created_at'))
    control = queries.control_of_act(cursor, act['id'])
    days, end = int(settings.get('control_days') or 30), None
    if control and isinstance(trigger.get('occurred_at'), datetime):
        days, end = (control['end_date'] - trigger['occurred_at'].date()).days, control['end_date']
    leader_shown = leader_id is not None and int(leader_id) != composer_id
    return {
        'department_name': department.get('name'),
        'composer_name': (people.get(composer_id) or {}).get('name') or ctx.get('name'),
        'composer_role': 'supervisor' if supervisor(composer_id) else 'head',
        'head_name': (people.get(int(leader_id)) or {}).get('name') if leader_shown else None,
        'gender': (people.get(int(act['operator_id'])) or {}).get('gender'),
        'control_days': days if days > 0 else None,
        'control_end': end if days > 0 else None,
        'period_kind': settings.get('period_kind'),
        'period_days': settings.get('period_days'),
    }


def update_act(cursor, ctx, act, record, payload, *, now):
    if not access.manages_record(ctx, record):
        raise DisciplineError('Акт оформляет руководитель', 403)
    if act.get('approved_at'):
        # Утверждённый акт — документ: дата и решение (и удержание по нему, уже
        # ушедшее в выгрузку своего месяца) задним числом не переписываются.
        raise DisciplineError('Акт утверждён — дата и решение больше не меняются', code='ACT_APPROVED')
    changes = {}
    if 'composed_on' in payload:
        changes['composed_on'] = parse_day(payload.get('composed_on'), 'Дата составления') if payload.get(
            'composed_on') else None
        changes['composed_by'] = ctx['user_id'] if changes['composed_on'] else None
    if 'decision_measure_id' in payload:
        if payload.get('decision_measure_id'):
            measure = queries.get_measure(cursor, _int(payload.get('decision_measure_id'), 'решение'))
            if not measure or int(measure['department_id']) != int(record['department_id']):
                raise DisciplineError('Решение — мера из справочника отдела', code='DECISION_MEASURE')
            amount, percent = _measure_values(measure, {'measure_amount': payload.get('decision_amount'),
                                                        'measure_percent': payload.get('decision_percent')})
            changes.update({'decision_measure_id': measure['id'], 'decision_title': measure['title'],
                            'decision_finance': measure['finance'], 'decision_value_kind': measure['value_kind'],
                            'decision_amount': amount, 'decision_percent': percent})
        else:
            changes.update({'decision_measure_id': None, 'decision_title': None, 'decision_finance': None,
                            'decision_value_kind': None, 'decision_amount': None, 'decision_percent': None})
    if payload.get('approve'):
        if not (changes.get('composed_on') or act.get('composed_on')):
            raise DisciplineError('Укажите дату составления акта', code='ACT_COMPOSED_ON')
        if not (changes.get('decision_title') or act.get('decision_title')):
            raise DisciplineError('Укажите решение по акту (меру)', code='DECISION_MEASURE')
        changes.update({'approved_by': ctx['user_id'], 'approved_at': now})
    diff = {key: [act.get(key), value] for key, value in changes.items() if act.get(key) != value}
    if not diff:
        return False
    queries.update_act(cursor, act['id'], changes)
    queries.audit(cursor, ctx, 'act_approve' if payload.get('approve') else 'act_update', 'act', entity_id=act['id'],
                  operator_id=act['operator_id'], department_id=act.get('department_id'),
                  record_id=act['trigger_record_id'], changes=diff)
    return True


def add_act_file(cursor, ctx, act, record, upload, *, now):
    if not access.manages_record(ctx, record):
        raise DisciplineError('Подписанный акт прикладывает руководитель', 403)
    queries.supersede_files(cursor, kind='act', act_id=act['id'])
    file_id = queries.insert_file(cursor, _file_row(upload, 'act', ctx, act_id=act['id'], now=now))
    if not act.get('composed_on'):
        queries.update_act(cursor, act['id'], {'composed_on': now.date(), 'composed_by': ctx['user_id']})
    queries.audit(cursor, ctx, 'upload_act', 'file', entity_id=file_id, operator_id=act['operator_id'],
                  department_id=act.get('department_id'), record_id=act['trigger_record_id'],
                  changes={'act': act['number'], 'file': upload['name'], 'sha256': upload.get('sha256')})
    queries.clear_kind(cursor, record_id=act['trigger_record_id'], kinds=['act'])
    return file_id


# ─────────────────────────────────────────────────────────────────────────────
# Особый контроль
# ─────────────────────────────────────────────────────────────────────────────

def create_control(cursor, ctx, payload, *, now, today):
    operator_id = _int(payload.get('operator_id'), 'оператор')
    profile = queries.operator_profiles(cursor, [operator_id], today).get(operator_id)
    if not profile or not access.can_manage_department(ctx, profile.get('department_id')):
        raise DisciplineError('Ставит на контроль руководитель отдела сотрудника', 403)
    source = str(payload.get('source') or 'manual')
    if source not in ('manual', 'probation'):
        raise DisciplineError('Вручную ставится контроль по решению руководителя или испытательный срок')
    reason = _text(payload.get('reason'), 1000)
    if source == 'probation' and not reason:
        reason = 'Испытательный срок нового сотрудника'
    if len(reason) < 3:
        raise DisciplineError('Укажите причину постановки на контроль')
    start = parse_day(payload.get('start_date') or today.isoformat(), 'Дата начала')
    end = parse_day(payload.get('end_date'), 'Дата окончания')
    if end < start:
        raise DisciplineError('Окончание раньше начала')
    if end > start + timedelta(days=366):
        raise DisciplineError('Контроль ставится не больше чем на год')
    # Та же очередь на человека, что у фиксации: два руководителя одновременно
    # не ставят двух контролей (второй получает понятный отказ, а не 500).
    queries.lock_operator(cursor, operator_id)
    if queries.active_controls(cursor, [operator_id]).get(operator_id):
        raise DisciplineError('Сотрудник уже на особом контроле — продлите действующий', code='CONTROL_EXISTS')
    control_id = queries.insert_control(cursor, {
        'operator_id': operator_id, 'department_id': profile['department_id'], 'source': source,
        'reason': reason, 'start_date': start, 'end_date': end, 'responsible_id': ctx['user_id'],
        'created_by': ctx['user_id'], 'created_at': now, 'updated_at': now,
    })
    queries.audit(cursor, ctx, 'control_create', 'control', entity_id=control_id, operator_id=operator_id,
                  department_id=profile['department_id'],
                  changes={'source': source, 'reason': reason, 'start_date': start, 'end_date': end})
    return control_id


def decide_control(cursor, ctx, control_id, payload, *, now, today):
    control = queries.get_control(cursor, control_id, for_update=True)
    if not control:
        raise DisciplineError('Контроль не найден', 404)
    department = control.get('department_id') or control.get('operator_department_id')
    if department is None or not access.can_manage_department(ctx, department):
        raise DisciplineError('Решение принимает руководитель', 403)
    if control.get('decided_at'):
        raise DisciplineError('Решение по контролю уже принято')
    decision = str(payload.get('decision') or '')
    if decision not in catalog.CONTROL_DECISION_LABELS:
        raise DisciplineError('Выберите решение')
    comment = _text(payload.get('comment'), 2000) or None
    if decision == 'extended':
        try:
            days = int(payload.get('days'))
        except (TypeError, ValueError):
            days = 0
        if not 1 <= days <= 365:
            raise DisciplineError('Продлить можно на 1–365 дней')
        end = max(control['end_date'], today) + timedelta(days=days)
        queries.update_control(cursor, control['id'], {'end_date': end, 'warned_for': None, 'due_notified_for': None})
        queries.audit(cursor, ctx, 'control_extend', 'control', entity_id=control['id'],
                      operator_id=control['operator_id'], department_id=department,
                      changes={'end_date': [control['end_date'], end], 'days': days, 'comment': comment})
        return
    measure_fields = {}
    if decision == 'measure':
        measure = queries.get_measure(cursor, _int(payload.get('measure_id'), 'мера'))
        if not measure or int(measure['department_id']) != int(department):
            raise DisciplineError('Выберите меру из справочника отдела', code='DECISION_MEASURE')
        amount, percent = _measure_values(measure, payload)
        # Мера решением по контролю — такая же применённая мера: она идёт во
        # вкладку «Меры» и в выгрузку на удержания (п. 10.5, 11, 15.5).
        measure_fields = {'decision_measure_id': measure['id'], 'decision_measure_title': measure['title'],
                          'decision_finance': measure['finance'], 'decision_value_kind': measure['value_kind'],
                          'decision_amount': amount, 'decision_percent': percent}
    queries.update_control(cursor, control['id'], dict(measure_fields, decision=decision, decision_comment=comment,
                                                       decided_by=ctx['user_id'], decided_at=now))
    queries.clear_kind(cursor, control_id=control['id'], kinds=['control_ending', 'control_due'])
    queries.audit(cursor, ctx, 'control_decide', 'control', entity_id=control['id'],
                  operator_id=control['operator_id'], department_id=department,
                  changes={'decision': catalog.CONTROL_DECISION_LABELS[decision], 'comment': comment,
                           'measure': measure_fields.get('decision_measure_title'),
                           'amount': measure_fields.get('decision_amount'),
                           'percent': measure_fields.get('decision_percent')})


def control_rows(cursor, ctx, controls, today):
    """Строки блока «На особом контроле» с цветом по п. 10.2 и сортировкой п. 10.1:
    сначала с нарушением в период контроля, потом по оставшимся дням."""
    counts = queries.violations_in_controls(cursor, [c['id'] for c in controls])
    profiles = queries.operator_profiles(cursor, [c['operator_id'] for c in controls], today)
    rows = []
    for control in controls:
        stats = counts.get(control['id'], {'total': 0, 'without_measure': 0})
        days_left = (control['end_date'] - today).days
        active = control.get('decided_at') is None
        tone = 'normal'
        if active and stats['without_measure'] > 0:
            tone = 'red'
        elif active and days_left <= catalog.CONTROL_WARN_DAYS:
            tone = 'yellow'
        profile = profiles.get(int(control['operator_id'])) or {}
        rows.append({
            'id': control['id'],
            'department_id': control.get('department_id') or control.get('operator_department_id'),
            'operator_id': control['operator_id'],
            'operator_name': control.get('operator_name'),
            'group_name': profile.get('group_name'),
            'direction_name': profile.get('direction_name'),
            'source': control['source'],
            'source_label': catalog.CONTROL_SOURCE_LABELS.get(control['source'], ''),
            'reason': control['reason'],
            'start_date': _iso(control['start_date']),
            'end_date': _iso(control['end_date']),
            'days_left': days_left,
            'violations': stats['total'],
            'violations_without_measure': stats['without_measure'],
            'responsible_name': control.get('responsible_name'),
            'decision': control.get('decision'),
            'decision_label': catalog.CONTROL_DECISION_LABELS.get(control.get('decision') or '', ''),
            'decision_comment': control.get('decision_comment'),
            'decided_at': _iso(control.get('decided_at')),
            'decided_by_name': control.get('decided_by_name'),
            'decision_measure_title': control.get('decision_measure_title'),
            'decision_amount': float(control['decision_amount']) if control.get('decision_amount') is not None else None,
            'decision_percent': float(control['decision_percent']) if control.get('decision_percent') is not None else None,
            'decision_detail': _decision_detail(control),
            'tone': tone,
            'can_decide': active and access.can_manage_department(
                ctx, control.get('department_id') or control.get('operator_department_id')),
        })
    # Действующие — по п. 10.1 (сначала с нарушением в период, потом по сроку);
    # закрытые — свежие решения первыми.
    active_rows = sorted((r for r in rows if r['decided_at'] is None),
                         key=lambda r: (0 if r['violations'] else 1, r['days_left'], r['operator_name'] or ''))
    closed_rows = sorted((r for r in rows if r['decided_at'] is not None), key=lambda r: r['decided_at'], reverse=True)
    return active_rows + closed_rows


def money_text(amount=None, percent=None):
    """«15 000 ₸» или «20 %» — как в карточке и уведомлениях."""
    if amount:
        return '{:,.0f}'.format(float(amount)).replace(',', '\u00a0') + '\u00a0₸'
    if percent:
        return '%g\u00a0%%' % float(percent)
    return ''


def _decision_detail(control):
    """Решение по контролю одной строкой: мера с суммой и комментарий."""
    comment = control.get('decision_comment') or ''
    if control.get('decision') == 'measure' and control.get('decision_measure_title'):
        value = money_text(control.get('decision_amount'), control.get('decision_percent'))
        text = 'Мера: %s%s' % (control['decision_measure_title'], (' · ' + value) if value else '')
        return '%s. %s' % (text, comment) if comment else text
    return comment or None


def visible_controls(cursor, ctx, today, state='active', **period):
    """Контроли, которые видит пользователь. period — started_from/started_to для
    отчёта: тогда без LIMIT."""
    limit = None if period else 500
    managed = sorted(access.managed_department_ids(ctx))
    if managed:
        return queries.list_controls(cursor, managed, state=state, limit=limit, **period)
    if access.is_supervisor(ctx):
        return queries.list_controls(cursor, sorted(access.section_department_ids(ctx)), state=state, limit=limit,
                                     group_ids=ctx.get('group_ids') or [], today=today, **period)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Напоминания (джоба планировщика)
# ─────────────────────────────────────────────────────────────────────────────

def run_reminders(cursor, *, now, today, base_url=None, outbox=None):
    """Срок объяснительной (повтор оператору за 4 часа, просрочка — СВ и
    руководителю), 3 дня до конца контроля и «нужно решение» по его окончании.
    Идемпотентно: dedupe_key — одно напоминание на срок."""
    system = {'user_id': None, 'name': 'Система'}
    sections = queries.section_departments(cursor)
    section = set(sections.values())

    def managers_of(*department_ids):
        # Глава отдела вне раздела (человека перевели в Удалённый КЦ) раздела не
        # видит — напоминание ему ни к чему.
        return queries.department_managers(
            cursor, [d for d in department_ids if d is not None and int(d) in section], sections=sections)

    cursor.execute(
        """
        SELECT r.id FROM discipline_records r
         WHERE r.cancelled_at IS NULL AND r.closed_at IS NULL
           AND r.explanation_received_at IS NULL AND r.refused_at IS NULL
           AND r.needs_explanation AND r.created_at >= %s
        """, (now - timedelta(days=60),))
    record_ids = [row[0] for row in cursor.fetchall()]
    sent = 0
    if record_ids:
        records = [queries.get_record(cursor, rid, today) for rid in record_ids]
        settings_for = SettingsCache(cursor)
        for item in enrich(cursor, system, [r for r in records if r], now, settings_for=settings_for):
            if not item['needs_explanation_now'] or not item['explanation_due_at']:
                continue
            due = item['explanation_due_at']
            settings = settings_for(item['department_id'])
            key = due.strftime('%Y%m%d%H%M')
            if due - timedelta(hours=4) <= now < due:
                sent += len(notify.push(
                    cursor, outbox, event='explanation_required', user_ids=[item['operator_id']], settings=settings,
                    title='Срок объяснительной — до %s' % due.strftime('%d.%m %H:%M'),
                    body=notify.describe(item), record_id=item['id'], tone='warning',
                    dedupe_key='expl_soon:%d:%s' % (item['id'], key), base_url=base_url))
            elif now >= due:
                # Кто ведёт человека сегодня: главы отдела записи и нынешнего
                # отдела человека, СВ его нынешних групп. Завёдший запись СВ, от
                # которого человека перевели, напоминаний больше не получает.
                targets = managers_of(item['department_id'], item.get('operator_department_id'))
                targets += queries.group_supervisors(cursor, item.get('operator_group_ids') or [], today)
                sent += len(notify.push(
                    cursor, outbox, event='explanation_overdue', user_ids=targets, settings=settings,
                    title='%s: объяснительная не предоставлена в срок' % item.get('operator_name', ''),
                    body=notify.describe(item), record_id=item['id'], tone='warning',
                    dedupe_key='expl_overdue:%d:%s' % (item['id'], key), base_url=base_url))
    cursor.execute("SELECT id FROM discipline_controls WHERE decided_at IS NULL AND end_date <= %s",
                   (today + timedelta(days=catalog.CONTROL_WARN_DAYS),))
    for (control_id,) in cursor.fetchall():
        control = queries.get_control(cursor, control_id)
        department = control.get('department_id') or control.get('operator_department_id')
        settings = queries.get_settings(cursor, department) if department else catalog.normalize_settings({})
        # Решение принимает нынешний глава отдела, а не тот, кто был главой при
        # постановке. Отдел без главы — супер-админам: решение без человека не
        # принимается (п. 10.5), и напоминание не должно уйти в пустоту.
        targets = managers_of(department, control.get('operator_department_id')) or queries.super_admins(cursor)
        if not targets:
            continue
        name = control.get('operator_name') or 'Сотрудник'
        end = control['end_date']
        if end > today and control.get('warned_for') != end:
            sent += len(notify.push(
                cursor, outbox, event='control_ending', user_ids=targets, settings=settings,
                title='%s: особый контроль заканчивается %s' % (name, end.strftime('%d.%m')),
                body='Через %d дн. нужно принять решение' % (end - today).days, control_id=control_id,
                dedupe_key='ctl_warn:%d:%s' % (control_id, end.isoformat()), base_url=base_url))
            queries.update_control(cursor, control_id, {'warned_for': end})
        elif end <= today and control.get('due_notified_for') != end:
            sent += len(notify.push(
                cursor, outbox, event='control_due', user_ids=targets, settings=settings,
                title='%s: примите решение по особому контролю' % name,
                body='Снять, продлить, применить меру или инициировать расторжение', control_id=control_id,
                tone='warning', dedupe_key='ctl_due:%d:%s' % (control_id, end.isoformat()), base_url=base_url))
            queries.update_control(cursor, control_id, {'due_notified_for': end})
    return sent
