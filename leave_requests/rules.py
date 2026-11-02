# -*- coding: utf-8 -*-
"""Чистые правила заявок на отпуск и Б/С: без базы, без Flask.

Маршрут заявки (ТЗ #358, п. 1–5):

    подана ─► supervisor ─(СВ согласовал)─► head ─(глава утвердил)─► approved
                  │  ▲                         │
     СВ предложил │  │ оператор не согласен     └─(отклонил)─► rejected
        даты      ▼  │
               proposal ─(оператор согласен)─► head

Шаг супервайзера настраивается отделом (leave_request_settings.supervisor_step);
у оператора без СВ группы заявка сразу идёт главе — иначе её не увидел бы никто.

Глава отдела и админ решают ОКОНЧАТЕЛЬНО на любом открытом шаге: глава может
оказаться и СВ своей группы, а СВ — уйти в тот самый отпуск, и тогда заявка
стояла бы, пока он не вернётся. Предлагать другие даты по ТЗ может супервайзер —
глава тоже, но только пока заявка на шаге супервайзера (он там его заменяет).

Оператор правит и отзывает заявку, пока она не утверждена (п. 5). Правка
возвращает заявку на первый шаг: СВ согласовывал другие даты.
"""

from datetime import date, datetime, timedelta

# Коды видов — те же, что у статусов графика (SCHEDULE_SPECIAL_STATUS_META в
# database.py): утверждённая заявка ставит в график ровно такой период, какой
# руководитель ставит руками, и второго словаря соответствий не нужно.
KINDS = ('annual_leave', 'bs')
KIND_LABELS = {'annual_leave': 'Ежегодный отпуск', 'bs': 'Б/С'}
KIND_TITLES = {'annual_leave': 'Отпуск', 'bs': 'Б/С'}

STATUSES = ('supervisor', 'head', 'proposal', 'approved', 'rejected', 'cancelled')
OPEN_STATUSES = ('supervisor', 'head', 'proposal')
# Заявки, которые держат даты оператора: вторая на те же дни — повтор.
ACTIVE_STATUSES = OPEN_STATUSES + ('approved',)

STATUS_LABELS = {
    'supervisor': 'Ждёт супервайзера',
    'head': 'Ждёт руководителя',
    'proposal': 'Предложены другие даты',
    'approved': 'Утверждена',
    'rejected': 'Отклонена',
    'cancelled': 'Отозвана',
}

# Действия журнала (leave_request_events.action).
EVENT_ACTIONS = (
    'created', 'edited', 'cancelled',
    'supervisor_approved', 'proposed', 'proposal_accepted', 'proposal_declined',
    'approved', 'rejected',
)

MAX_PERIOD_DAYS = 365
MAX_AHEAD_DAYS = 365
REASON_MAX_CHARS = 1000
COMMENT_MAX_CHARS = 1000
LONG_BS_DAYS_MAX = 366


class LeaveError(Exception):
    """Отказ, который пользователь видит текстом: 400 по умолчанию."""

    def __init__(self, message, code='LEAVE_INVALID', status=400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


# ── Даты ────────────────────────────────────────────────────────────────────

def parse_date(value, field='date'):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or '').strip()[:10]
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        raise LeaveError('Укажите дату', code='LEAVE_BAD_DATE') from None


def period_days(start, end):
    """Сколько календарных дней в периоде, оба края включительно (ТЗ п. 1)."""
    return (end - start).days + 1


def days_word(count):
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return 'день'
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return 'дня'
    return 'дней'


def days_text(count):
    return '%d %s' % (count, days_word(count))


def format_period(start, end, today=None):
    """«01.07 — 14.07»; год пишем, только когда он не текущий."""
    year = (today or date.today()).year

    def one(day):
        return day.strftime('%d.%m') if day.year == year else day.strftime('%d.%m.%Y')

    if start == end:
        return one(start)
    return '%s — %s' % (one(start), one(end))


def validate_period(start, end, today):
    """Период заявки или предложенных дат. Отказ — словами, которые видит человек."""
    if end < start:
        raise LeaveError('Дата окончания раньше даты начала', code='LEAVE_PERIOD_REVERSED')
    if start < today:
        raise LeaveError('Начало уже прошло — выберите сегодняшний день или позже', code='LEAVE_PERIOD_PAST')
    if (start - today).days > MAX_AHEAD_DAYS:
        raise LeaveError('Слишком далеко: заявки принимаются на год вперёд', code='LEAVE_PERIOD_TOO_FAR')
    if period_days(start, end) > MAX_PERIOD_DAYS:
        raise LeaveError('Слишком длинный период: не больше %d дней' % MAX_PERIOD_DAYS,
                         code='LEAVE_PERIOD_TOO_LONG')


def periods_overlap(a_start, a_end, b_start, b_end):
    return a_start <= b_end and b_start <= a_end


# ── Текст ───────────────────────────────────────────────────────────────────

def clean_text(value, limit, *, required=False, empty_message='Заполните поле'):
    text = str(value or '').strip()
    if required and not text:
        raise LeaveError(empty_message, code='LEAVE_TEXT_REQUIRED')
    if len(text) > limit:
        raise LeaveError('Слишком длинный текст: не больше %d символов' % limit, code='LEAVE_TEXT_TOO_LONG')
    return text or None


# ── Настройки отдела ────────────────────────────────────────────────────────

def allowed_kinds(settings):
    if not settings or not settings.get('enabled'):
        return ()
    return tuple(kind for kind in KINDS if settings.get('allow_' + kind))


def first_status(settings, supervisor_id):
    """С какого шага начинается заявка: со СВ, если шаг включён и СВ у оператора есть."""
    if (settings or {}).get('supervisor_step', True) and supervisor_id:
        return 'supervisor'
    return 'head'


def default_exclude_from_tenure(kind, days, long_bs_days):
    """Подсказка главе: длинный Б/С по настройке отдела не идёт в стаж (ТЗ п. 8).
    Окончательно решает глава переключателем при утверждении."""
    return kind == 'bs' and bool(long_bs_days) and days >= int(long_bs_days)


def normalize_settings_patch(data):
    """Тело PUT настроек отдела → значения колонок. Неизвестные поля молча не берём."""
    data = data or {}
    patch = {}
    for key in ('enabled', 'allow_annual_leave', 'allow_bs', 'supervisor_step'):
        if key in data:
            patch[key] = bool(data.get(key))
    if 'long_bs_days' in data:
        raw = data.get('long_bs_days')
        if raw in (None, '', 0, '0'):
            patch['long_bs_days'] = None
        else:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise LeaveError('Укажите число дней', code='LEAVE_SETTINGS_INVALID') from None
            if not 1 <= value <= LONG_BS_DAYS_MAX:
                raise LeaveError('Число дней — от 1 до %d' % LONG_BS_DAYS_MAX, code='LEAVE_SETTINGS_INVALID')
            patch['long_bs_days'] = value
    return patch


def check_settings(settings):
    if settings.get('enabled') and not (settings.get('allow_annual_leave') or settings.get('allow_bs')):
        raise LeaveError('Оставьте хотя бы один вид заявки — отпуск или Б/С', code='LEAVE_SETTINGS_INVALID')


# ── Кто что может ───────────────────────────────────────────────────────────

def reviewer_role(viewer, request, in_scope):
    """Роль смотрящего по отношению к заявке: 'head', 'supervisor' или None.

    'head' — глава отдела заявки или админ без своего отдела: решает
    окончательно. 'supervisor' — СВ, в чьём периметре оператор (тот же
    периметр, что у «Запросов по сменам»).
    """
    if viewer.get('is_admin'):
        return 'head'
    department_id = request.get('department_id')
    if department_id is not None and int(department_id) in set(viewer.get('headed_department_ids') or ()):
        return 'head'
    if viewer.get('is_supervisor') and in_scope:
        return 'supervisor'
    return None


def reviewer_actions(role, status):
    if role == 'head':
        if status == 'supervisor':
            return ('approve', 'propose', 'reject')
        if status == 'head':
            return ('approve', 'reject')
        return ()
    if role == 'supervisor' and status == 'supervisor':
        return ('approve', 'propose', 'reject')
    return ()


def awaits(role, status):
    """Ждёт ли заявка именно этого человека: для счётчика «Ожидают решения»."""
    return (role == 'supervisor' and status == 'supervisor') or (role == 'head' and status == 'head')


def status_after_approve(role):
    return 'approved' if role == 'head' else 'head'


def operator_actions(status):
    if status in ('supervisor', 'head'):
        return ('edit', 'cancel')
    if status == 'proposal':
        return ('accept', 'decline', 'edit', 'cancel')
    return ()
