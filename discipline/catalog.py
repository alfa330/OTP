# -*- coding: utf-8 -*-
"""Справочники и правила раздела «Дисциплина». Чистый модуль: ни базы, ни Flask.

Здесь живёт всё, что ТЗ #254 задаёт словами: стартовые типы нарушений (п. 5.1),
меры (п. 5.2), лестница эскалации (п. 7.3), окно подсчёта (п. 7.1), плашка у
поля «Оператор» (п. 6.2) и статусы записи (п. 5.3). Фронт держит зеркало этих
правил в src/components/discipline/disciplineRules.js; тест сверяет их.

Ключевые решения:

* **Лестница — данные, а не код.** В постановке два разных порядка: на
  скриншоте заказчика «тренинг → объясн. №1 → №2 → №3 + акт», в тексте ТЗ
  «объяснительная → объяснительная → объяснительная + акт». ТЗ при этом прямо
  требует, чтобы лестницу менял руководитель без разработки, поэтому лестница —
  список шагов в настройках отдела, а по умолчанию стоит порядок со скриншота
  (его заказчик нарисовал сам, и он совпадает с практикой: «Дисциплинарный
  тренинг» в «Тренингах» уже ведут). Порядок из текста ТЗ — второй готовый
  вариант в настройках.
* **Номер нарушения и шаг лестницы не хранятся, а считаются.** ТЗ п. 7.2:
  «при отмене записи счётчик пересчитывается автоматически, а порядковые номера
  последующих нарушений сдвигаются». Храни номер в строке — его пришлось бы
  переписывать у всех соседей при каждой отмене и каждой записи задним числом.
* **Два числа, а не одно.** Порядковый номер в периоде считается без веса
  (ТЗ п. 6.3), а шаг лестницы — по сумме весов («грубое считается за два»,
  п. 5.1). Пока веса равны 1, числа совпадают.
* **Статус записи выводится из фактов** (есть объяснительная, акт, мера,
  закрыта), а не хранится машиной из восьми состояний: так у записи не бывает
  статуса, противоречащего её же полям.
"""

from datetime import datetime, timedelta

SECTION_DEPARTMENT_CODES = ('szov', 'op', 'tez')

# ── Типы нарушений (ТЗ п. 5.1) ───────────────────────────────────────────────

SEVERITIES = (
    ('light', 'Лёгкое'),
    ('medium', 'Среднее'),
    ('gross', 'Грубое'),
    ('manual', 'Задаётся вручную'),
)
SEVERITY_LABELS = dict(SEVERITIES)

# Стартовый набор — дословно таблица 5.1. Все веса 1, объяснительная
# обязательна у всех; руководитель правит набор в «Настройках».
DEFAULT_TYPES = (
    {'title': 'Сон на рабочем месте', 'severity': 'gross'},
    {'title': 'Посторонние сайты, игры, соцсети на рабочем ПК', 'severity': 'medium'},
    {'title': 'Отсутствие на рабочем месте сверх регламента перерывов', 'severity': 'medium'},
    {'title': 'Опоздание к началу смены', 'severity': 'light'},
    {'title': 'Уход со смены раньше времени без согласования', 'severity': 'medium'},
    {'title': 'Использование личного телефона на линии', 'severity': 'light'},
    {'title': 'Невыход на смену без предупреждения', 'severity': 'gross'},
    {'title': 'Нарушение этики в общении с коллегами', 'severity': 'medium'},
    # «Иное» — название нарушения вводит тот, кто фиксирует.
    {'title': 'Иное', 'severity': 'manual', 'is_other': True},
)

MAX_WEIGHT = 5

# ── Меры воздействия (ТЗ п. 5.2) ─────────────────────────────────────────────

# Финансовый эффект — колонка таблицы 5.2. «Да» и «возможен» — два разных
# ответа, и в выгрузку на удержания попадают оба: «возможен» без суммы — это
# повод бухгалтерии спросить, а не промолчать.
FINANCE_KINDS = (
    ('none', 'Нет'),
    ('yes', 'Да'),
    ('possible', 'Возможен'),
)
FINANCE_LABELS = dict(FINANCE_KINDS)

# Чем выражена мера в деньгах: процент от переменной части или сумма.
VALUE_KINDS = (
    ('none', 'Без суммы'),
    ('percent', 'Процент'),
    ('amount', 'Сумма'),
)
VALUE_LABELS = dict(VALUE_KINDS)

DEFAULT_MEASURES = (
    {'title': 'Устное замечание', 'needs_approval': False, 'finance': 'none', 'value_kind': 'none'},
    {'title': 'Письменное замечание', 'needs_approval': True, 'finance': 'none', 'value_kind': 'none'},
    {'title': 'Выговор', 'needs_approval': True, 'finance': 'none', 'value_kind': 'none'},
    {'title': 'Лишение переменной части (процент)', 'needs_approval': True, 'finance': 'yes',
     'value_kind': 'percent'},
    {'title': 'Лишение переменной части (фиксированная сумма)', 'needs_approval': True, 'finance': 'yes',
     'value_kind': 'amount'},
    {'title': 'Снятие с проекта / перевод на другую линию', 'needs_approval': True, 'finance': 'possible',
     'value_kind': 'none'},
    {'title': 'Расторжение трудового договора', 'needs_approval': True, 'finance': 'yes',
     'value_kind': 'none'},
)

# ── Лестница эскалации (ТЗ п. 7.3) ───────────────────────────────────────────

STEP_TRAINING = 'training'
STEP_EXPLANATION = 'explanation'
STEP_ACT = 'act'
STEP_ACTIONS = (STEP_TRAINING, STEP_EXPLANATION, STEP_ACT)
STEP_LABELS = {
    STEP_TRAINING: 'Тренинг',
    STEP_EXPLANATION: 'Объяснительная',
    STEP_ACT: 'Объяснительная + акт',
}
EXPLANATION_STEPS = (STEP_EXPLANATION, STEP_ACT)

# Как в тексте ТЗ п. 7.3: объяснительная, объяснительная, объяснительная + акт.
# По ней написаны критерии приёмки 2–4 (красная плашка при двух нарушениях,
# третье не закрыть без акта), поэтому она — по умолчанию.
LADDER_TZ = (STEP_EXPLANATION, STEP_EXPLANATION, STEP_ACT)
# Как на скриншоте заказчика: 1-е — тренинг, 2–4-е — объяснительные №1–3,
# на 4-м акт. За последней ступенью повторяется последняя.
LADDER_SAMPLE = (STEP_TRAINING, STEP_EXPLANATION, STEP_EXPLANATION, STEP_ACT)
LADDER_PRESETS = (
    ('tz', 'Как в ТЗ', LADDER_TZ),
    ('sample', 'Как на образце', LADDER_SAMPLE),
)
DEFAULT_LADDER = LADDER_TZ
MAX_LADDER = 10

PERIOD_ROLLING = 'rolling'
PERIOD_MONTH = 'month'
PERIOD_KINDS = (
    (PERIOD_ROLLING, 'Скользящее окно'),
    (PERIOD_MONTH, 'Календарный месяц'),
)

# ── Уведомления (ТЗ п. 12) ───────────────────────────────────────────────────
# Колокол iCore — всегда. Вместо почты — Telegram (отправки почты в портале нет;
# адреса есть, транспорта нет); его руководитель включает по событию.
# (код, событие, кому, Telegram по умолчанию)
NOTIFY_EVENTS = (
    ('recorded', 'Зафиксировано нарушение', 'Оператору', False),
    ('explanation_required', 'Требуется объяснительная', 'Оператору', False),
    ('pre_act', 'Нарушение за шаг до акта', 'Супервайзеру группы', False),
    ('act', 'Нарушение на шаге акта', 'Руководителю', True),
    ('act_basis', 'Проверить основание акта', 'Руководителю', True),
    ('refusal', 'Отказ от дачи объяснения', 'Руководителю', True),
    ('under_control', 'Нарушение у оператора на особом контроле', 'Руководителю', True),
    ('explanation_overdue', 'Объяснительная не предоставлена в срок', 'Супервайзеру и руководителю', False),
    ('control_ending', '3 дня до окончания особого контроля', 'Руководителю', True),
    ('control_due', 'Особый контроль закончился — нужно решение', 'Руководителю', True),
    ('measure_finance', 'Утверждена мера с финансовым эффектом', 'Руководителю', False),
)
NOTIFY_EVENT_CODES = tuple(code for code, *_rest in NOTIFY_EVENTS)

DEFAULT_SETTINGS = {
    'period_kind': PERIOD_ROLLING,
    'period_days': 30,
    'ladder': list(DEFAULT_LADDER),
    'control_days': 30,
    'explanation_days': 2,
    'telegram': {code: default for code, _label, _who, default in NOTIFY_EVENTS},
}

LIMITS = {
    'period_days': (1, 365),
    'control_days': (1, 365),
    'explanation_days': (1, 30),
}
LIMIT_LABELS = {
    'period_days': 'Длительность окна',
    'control_days': 'Срок особого контроля',
    'explanation_days': 'Срок объяснительной',
}

# Правила счёта — окно и лестница. Они действуют на записи, зафиксированные
# после сохранения: прежние записи считаются по правилам своего дня. Иначе
# смена лестницы задним числом требовала бы акт там, где его не составляли,
# и ставила бы «проверить основание» прошлым актам.
COUNTING_KEYS = ('period_kind', 'period_days', 'ladder')

# ── Особый контроль (ТЗ п. 10) ───────────────────────────────────────────────

CONTROL_SOURCES = (
    ('act', 'После акта'),
    ('manual', 'Решение руководителя'),
    ('probation', 'Испытательный срок'),
)
CONTROL_SOURCE_LABELS = dict(CONTROL_SOURCES)

# Решение по окончании (п. 10.5). «Продлить» не закрывает контроль, а двигает
# срок; остальные три его завершают.
CONTROL_DECISIONS = (
    ('removed', 'Снять контроль'),
    ('extended', 'Продлить'),
    ('measure', 'Применить меру'),
    ('termination', 'Инициировать расторжение'),
)
CONTROL_DECISION_LABELS = dict(CONTROL_DECISIONS)
CONTROL_FINAL_DECISIONS = ('removed', 'measure', 'termination')
CONTROL_WARN_DAYS = 3

# ── Файлы (ТЗ п. 8.1, 9, 13) ─────────────────────────────────────────────────

MB = 1024 * 1024
_DOCS = ('pdf', 'jpg', 'jpeg', 'png', 'heic', 'doc', 'docx')
FILE_KINDS = {
    'evidence': {'label': 'Доказательство', 'ext': _DOCS + ('mp4',), 'max_bytes': 10 * MB,
                 'video_max_bytes': 50 * MB, 'max_files': 5},
    'explanation': {'label': 'Объяснительная', 'ext': _DOCS, 'max_bytes': 10 * MB, 'max_files': 1},
    'act': {'label': 'Акт', 'ext': ('pdf', 'jpg', 'jpeg', 'png'), 'max_bytes': 20 * MB, 'max_files': 1},
    'refusal_act': {'label': 'Акт об отказе', 'ext': ('pdf', 'jpg', 'jpeg', 'png'), 'max_bytes': 20 * MB,
                    'max_files': 1},
    'act_template': {'label': 'Шаблон акта', 'ext': ('docx',), 'max_bytes': 5 * MB, 'max_files': 1},
}
REFUSAL_MIN_WITNESSES = 2
DESCRIPTION_MIN_CHARS = 30

# ── Статусы записи (ТЗ п. 5.3) ───────────────────────────────────────────────
# (код, подпись, тон). Тон — смысл, а не украшение: красный — нужен акт или
# мера, янтарный — ждём действия, зелёный — сделано, серый — нейтрально.
STATES = (
    ('explanation_requested', 'Объяснительная запрошена', 'amber'),
    ('explanation_received', 'Объяснительная получена', 'slate'),
    ('explanation_refused', 'Отказ от объяснения', 'amber'),
    ('training_required', 'Провести тренинг', 'rose'),
    ('training_done', 'Тренинг проведён', 'green'),
    ('act_required', 'Требуется акт', 'red'),
    ('act_done', 'Акт составлен', 'red'),
    ('measure_required', 'Нужна мера', 'red'),
    ('measure_pending', 'Мера на утверждении', 'amber'),
    ('measure_applied', 'Мера применена', 'blue'),
    ('recorded', 'Зафиксировано', 'slate'),
    ('closed', 'Закрыто', 'slate'),
    ('cancelled', 'Отменено', 'slate'),
)
STATE_LABELS = {code: label for code, label, _tone in STATES}
STATE_TONES = {code: tone for code, _label, tone in STATES}

CLOSE_BLOCKERS = {
    'explanation': 'Нет объяснительной или акта об отказе',
    'training': 'Тренинг не проведён',
    'act': 'Не приложен подписанный акт',
    'measure': 'Мера не утверждена руководителем',
}

_ORDINALS = ('первое', 'второе', 'третье', 'четвёртое', 'пятое', 'шестое', 'седьмое',
             'восьмое', 'девятое', 'десятое')


def ordinal_word(number):
    number = int(number or 0)
    if 1 <= number <= len(_ORDINALS):
        return _ORDINALS[number - 1]
    return '%d-е' % number


def plural_violations(count):
    count = abs(int(count or 0))
    if count % 10 == 1 and count % 100 != 11:
        return 'нарушение'
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return 'нарушения'
    return 'нарушений'


# ── Настройки ────────────────────────────────────────────────────────────────

class SettingsError(ValueError):
    pass


def normalize_ladder(raw):
    if not isinstance(raw, (list, tuple)) or not raw:
        raise SettingsError('В лестнице должна быть хотя бы одна ступень')
    steps = [str(item or '').strip().lower() for item in raw]
    if len(steps) > MAX_LADDER:
        raise SettingsError('В лестнице не больше %d ступеней' % MAX_LADDER)
    for step in steps:
        if step not in STEP_ACTIONS:
            raise SettingsError('Неизвестная ступень лестницы')
    return steps


def _int_in(raw, key):
    low, high = LIMITS[key]
    label = LIMIT_LABELS.get(key, 'Значение')
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise SettingsError('%s — укажите число дней' % label) from None
    if not low <= value <= high:
        raise SettingsError('%s — от %d до %d дней' % (label, low, high))
    return value


def normalize_settings(raw, base=None):
    """Настройки отдела: недостающее берётся из base (или из значений по умолчанию),
    присланное проверяется. Неизвестные ключи отбрасываются."""
    source = dict(base or DEFAULT_SETTINGS)
    raw = raw or {}
    result = {
        'period_kind': source.get('period_kind') or PERIOD_ROLLING,
        'period_days': int(source.get('period_days') or 30),
        'ladder': list(source.get('ladder') or DEFAULT_LADDER),
        'control_days': int(source.get('control_days') or 30),
        'explanation_days': int(source.get('explanation_days') or 2),
        'telegram': dict(DEFAULT_SETTINGS['telegram']),
    }
    result['telegram'].update({k: bool(v) for k, v in (source.get('telegram') or {}).items()
                               if k in NOTIFY_EVENT_CODES})
    if 'period_kind' in raw:
        kind = str(raw.get('period_kind') or '').strip()
        if kind not in dict(PERIOD_KINDS):
            raise SettingsError('Неизвестный тип периода')
        result['period_kind'] = kind
    for key in ('period_days', 'control_days', 'explanation_days'):
        if key in raw:
            result[key] = _int_in(raw.get(key), key)
    if 'ladder' in raw:
        result['ladder'] = normalize_ladder(raw.get('ladder'))
    if 'telegram' in raw:
        telegram = raw.get('telegram') or {}
        if not isinstance(telegram, dict):
            raise SettingsError('Каналы уведомлений переданы неверно')
        for code, value in telegram.items():
            if code in NOTIFY_EVENT_CODES:
                result['telegram'][code] = bool(value)
    return result


def counting_rules(settings):
    """Окно и лестница из настроек — то, по чему считаются номер и ступень."""
    settings = settings or {}
    return {
        'period_kind': settings.get('period_kind') or PERIOD_ROLLING,
        'period_days': int(settings.get('period_days') or 30),
        'ladder': list(settings.get('ladder') or DEFAULT_LADDER),
    }


def rules_at(versions, at):
    """Правила счёта, действовавшие в момент at. versions — [(с какого момента,
    правила)] по возрастанию; пусто — истории правок нет, действуют текущие."""
    chosen = None
    for valid_from, rules in versions or ():
        if valid_from <= at:
            chosen = rules
        else:
            break
    return chosen


def act_step(ladder):
    """Номер (с 1) первой ступени с актом или None, если акта в лестнице нет."""
    for index, step in enumerate(ladder or (), start=1):
        if step == STEP_ACT:
            return index
    return None


def step_action(weighted, ladder):
    """Ступень для суммы весов: за последней ступенью повторяется последняя."""
    ladder = list(ladder or ())
    if weighted <= 0 or not ladder:
        return None
    return ladder[min(int(weighted), len(ladder)) - 1]


def effective_action(action, control=None, record_id=None):
    """Ступень с поправкой на особый контроль после акта.

    ТЗ п. 7.3 и 18.2: нарушение в период особого контроля — это обязательная
    мера, а не новый акт («четвёртое подряд идёт уже как нарушение в период
    особого контроля»). Поэтому под контролем, начатым актом, ступень «акт»
    читается как объяснительная: второй акт на тот же период не составляется,
    а закрыть запись нельзя без утверждённой меры. Испытательный срок и
    контроль по решению руководителя акт не заменяют — там лестница как есть,
    пока по ней не составлен акт (тогда акт привязывается к этому контролю).
    Сама запись-основание акта остаётся актом."""
    if action == STEP_ACT and control and control.get('act_id') and (
            record_id is None or control.get('act_record_id') != record_id):
        return STEP_EXPLANATION
    return action


def covered_by_act(record_id, steps=None, act_triggers=(), act_members=()):
    """Период записи на ступени «акт» уже покрыт актом: запись входит в состав
    акта, в её окне есть основание другого акта или она сама — в окне более
    позднего акта (запись задним числом встала раньше уже составленного).
    Второй акт на тот же период не составляется (ТЗ п. 7.3, 18.2).

    steps — {запись: {'window_ids', …}} из compute_steps."""
    if record_id is None or record_id in act_triggers:
        return False
    if record_id in act_members:
        return True
    steps = steps or {}
    window = (steps.get(record_id) or {}).get('window_ids') or ()
    if any(other != record_id and other in act_triggers for other in window):
        return True
    return any(record_id in ((steps.get(trigger) or {}).get('window_ids') or ()) for trigger in act_triggers)


def effective_step(action, control=None, record_id=None, steps=None, act_triggers=(), act_members=()):
    """Ступень записи со всеми поправками: контроль после акта и период,
    уже покрытый актом, — объяснительная вместо второго акта."""
    action = effective_action(action, control, record_id)
    if action == STEP_ACT and covered_by_act(record_id, steps, act_triggers, act_members):
        return STEP_EXPLANATION
    return action


def step_title(action, explanation_no=None):
    """Подпись ступени в строке журнала — как на скриншоте заказчика."""
    if action == STEP_TRAINING:
        return 'Тренинг'
    if action == STEP_EXPLANATION:
        return 'Объясн. №%d' % explanation_no if explanation_no else 'Объяснительная'
    if action == STEP_ACT:
        return 'Объясн. №%d + акт' % explanation_no if explanation_no else 'Объяснительная + акт'
    return ''


# ── Окно подсчёта (ТЗ п. 7.1) ────────────────────────────────────────────────

def window_start(at, settings):
    """(граница, включительно ли). Скользящее окно — N суток до момента
    нарушения, не включая границу; месяц — с первого числа месяца нарушения."""
    if (settings or {}).get('period_kind') == PERIOD_MONTH:
        return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0), True
    days = int((settings or {}).get('period_days') or 30)
    return at - timedelta(days=days), False


def in_window(moment, at, settings):
    start, inclusive = window_start(at, settings)
    if moment > at:
        return False
    return moment >= start if inclusive else moment > start


def _weight(record):
    try:
        return max(1, min(MAX_WEIGHT, int(record.get('weight') or 1)))
    except (TypeError, ValueError):
        return 1


def _order_key(record):
    return (record['occurred_at'], int(record['id']))


def compute_steps(records, settings_for):
    """Номер, сумма весов и ступень каждой НЕотменённой записи ОДНОГО оператора.

    records — [{'id', 'occurred_at', 'created_at', 'weight', 'department_id', 'cancelled'}].
    settings_for(department_id, at) — настройки отдела записи: лестница и окно
    берутся по отделу, где нарушение зафиксировано, и по правилам, которые
    действовали в момент фиксации (at — created_at записи). История при этом
    общая — в счётчик идут все записи человека, в каком бы отделе их ни завели
    (при переводе история едет вместе с ним).

    Возвращает {id: {'ordinal', 'weighted', 'action', 'explanation_no', 'window_ids'}}.
    """
    live = sorted((r for r in records or () if not r.get('cancelled')), key=_order_key)
    result = {}
    for index, record in enumerate(live):
        settings = settings_for(record.get('department_id'), record.get('created_at'))
        window = [item for item in live[:index + 1]
                  if in_window(item['occurred_at'], record['occurred_at'], settings)]
        weighted = sum(_weight(item) for item in window)
        result[record['id']] = {
            'ordinal': len(window),
            'weighted': weighted,
            'action': step_action(weighted, settings.get('ladder')),
            'window_ids': [item['id'] for item in window],
        }
    for record in live:
        info = result[record['id']]
        if info['action'] in EXPLANATION_STEPS:
            info['explanation_no'] = sum(
                1 for item_id in info['window_ids'] if result[item_id]['action'] in EXPLANATION_STEPS)
        else:
            info['explanation_no'] = None
    return result


def current_window(records, at, settings):
    """Неотменённые записи человека в окне, которое заканчивается моментом at."""
    return sorted((r for r in records or () if not r.get('cancelled')
                   and in_window(r['occurred_at'], at, settings)), key=_order_key)


def next_step(records, at, settings, new_weight=1, settings_for=None):
    """Что будет, если зафиксировать нарушение сейчас: номер, ступень, № объяснительной.

    records — вся история человека: ступени записей окна считаются так же, как
    после сохранения (по всей истории и по правилам своего дня), иначе номер
    объяснительной на плашке расходился бы с номером в журнале."""
    window = current_window(records, at, settings)
    infos = compute_steps(records, settings_for or (lambda *_: settings))
    weighted = sum(_weight(item) for item in window) + max(1, int(new_weight or 1))
    action = step_action(weighted, settings.get('ladder'))
    explanation_no = None
    if action in EXPLANATION_STEPS:
        explanation_no = 1 + sum(1 for item in window
                                 if (infos.get(item['id']) or {}).get('action') in EXPLANATION_STEPS)
    return {
        'count': len(window),
        'weighted': weighted - max(1, int(new_weight or 1)),
        'ordinal': len(window) + 1,
        'action': action,
        'explanation_no': explanation_no,
        'window': window,
    }


def _step_suffix(action, explanation_no):
    if action == STEP_TRAINING:
        return ' — тренинг'
    if action == STEP_EXPLANATION:
        return ' — объяснительная №%d' % explanation_no if explanation_no else ' — объяснительная'
    return ''


def _short_date(value):
    return value.strftime('%d.%m') if hasattr(value, 'strftime') else ''


def plate(step, control=None):
    """Плашка под полем «Оператор» (ТЗ п. 6.2): тон, заголовок, текст.

    step — результат next_step; control — активный особый контроль человека
    ({'end_date', 'reason'}) или None. Особый контроль кладётся «поверх
    остальных»: при нём речь уже не о номере, а об обязательной мере.
    """
    count = step['count']
    action = step['action']
    ordinal = ordinal_word(step['ordinal'])
    window = step['window']
    if control:
        end = control.get('end_date')
        end_text = end.strftime('%d.%m.%Y') if hasattr(end, 'strftime') else str(end or '')
        reason = str(control.get('reason') or '').strip()
        text = 'Причина: %s. ' % reason if reason else ''
        return {
            'tone': 'red',
            'title': 'Оператор на особом контроле до %s' % end_text,
            'text': text + 'Обязательно назначение меры. Уведомление уйдёт руководителю.',
            'count': count,
        }
    if action == STEP_ACT:
        return {
            'tone': 'red',
            'title': 'За период уже %d %s' % (count, plural_violations(count)),
            'text': 'Это %s — потребуется акт. Объяснительная обязательна, запись нельзя будет '
                    'закрыть без прикреплённого акта.' % ordinal,
            'count': count,
        }
    if count == 0:
        return {
            'tone': 'gray',
            'title': 'Нарушений за период нет',
            'text': 'Это будет первое%s.' % _step_suffix(action, step['explanation_no']),
            'count': 0,
        }
    last = window[-1]
    detail = ''
    if count == 1:
        detail = ' (%s, %s)' % (_short_date(last['occurred_at']), last.get('type_title') or '')
    return {
        'tone': 'yellow',
        'title': 'За период уже %d %s%s' % (count, plural_violations(count), detail),
        'text': 'Это будет %s%s.' % (ordinal, _step_suffix(action, step['explanation_no'])),
        'count': count,
    }


# ── Сроки ────────────────────────────────────────────────────────────────────

def add_working_days(moment, days):
    """Плюс N рабочих дней (пн–пт), время суток сохраняется. Срок объяснительной
    по умолчанию — 2 рабочих дня с фиксации (ТЗ п. 8.3)."""
    result = moment
    left = int(days or 0)
    while left > 0:
        result = result + timedelta(days=1)
        if result.weekday() < 5:
            left -= 1
    return result


# ── Статус записи (ТЗ п. 5.3) ────────────────────────────────────────────────

def needs_explanation(record, action):
    return action in EXPLANATION_STEPS and bool(record.get('needs_explanation', True))


def explanation_done(record):
    return bool(record.get('explanation_received_at') or record.get('refused_at'))


def record_state(record, action, *, act_has_file=False, under_control=False):
    """Код статуса записи по её фактам. Порядок — как в ТЗ: объяснительная →
    акт → мера → закрыто; тренинг — первая ступень образца заказчика."""
    if record.get('cancelled_at'):
        return 'cancelled'
    if record.get('closed_at'):
        return 'closed'
    expl_needed = needs_explanation(record, action)
    if expl_needed and not explanation_done(record):
        return 'explanation_requested'
    if action == STEP_ACT and not act_has_file:
        return 'act_required'
    if action == STEP_TRAINING and not record.get('training_id'):
        return 'training_required'
    measure_state = record.get('measure_state')
    if under_control or measure_state:
        if measure_state == 'approved':
            return 'measure_applied'
        if measure_state == 'proposed':
            return 'measure_pending'
        if under_control:
            return 'measure_required'
    if action == STEP_ACT:
        return 'act_done'
    if record.get('refused_at'):
        return 'explanation_refused'
    if expl_needed:
        return 'explanation_received'
    if action == STEP_TRAINING:
        return 'training_done'
    return 'recorded'


def close_blockers(record, action, *, act_has_file=False, under_control=False):
    """Чего не хватает, чтобы закрыть запись. Пусто — можно закрывать."""
    blockers = []
    if needs_explanation(record, action) and not explanation_done(record):
        blockers.append('explanation')
    if action == STEP_TRAINING and not record.get('training_id'):
        blockers.append('training')
    if action == STEP_ACT and not act_has_file:
        blockers.append('act')
    if under_control and record.get('measure_state') != 'approved':
        blockers.append('measure')
    return blockers


def explanation_due(record, settings):
    """Срок объяснительной — от момента, когда её запросили: при фиксации или
    позже (ступень сдвинулась, прежний файл признан недействительным). Иначе
    позднему запросу срок приходил бы уже прошедшим."""
    asked = record.get('explanation_requested_at') or record.get('created_at')
    if not isinstance(asked, datetime):
        return None
    return add_working_days(asked, int((settings or {}).get('explanation_days') or 2))


def explanation_overdue(record, action, settings, now):
    if not needs_explanation(record, action) or explanation_done(record):
        return False
    if record.get('cancelled_at') or record.get('closed_at'):
        return False
    due = explanation_due(record, settings)
    return bool(due and now > due)


def format_number(prefix, year, number):
    return '%s-%d-%04d' % (prefix, int(year), int(number))
