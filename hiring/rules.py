# -*- coding: utf-8 -*-
"""Правила воронки найма (ТЗ #404, разделы 4–7). Чистая логика: ни базы, ни Flask.

Колонки доски — десять из ТЗ 4.1 и в его порядке, одиннадцатая — «Не отвечает звонок/чат» (архив молчащих,
владелец 20.11.2026); «Отказ» и «Отсев» — завершающие,
из них карточку возвращает только руководитель рекрутинга. Переходы — таблица ТЗ 4.2:
кто и при каком условии. Справочники причин (6.2, 6.3) и источников (5.1, 5.2) — отсюда
же, одним списком с кодами; в базе они живут строками и правятся руководителем, а здесь —
первичное наполнение.
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ALMATY = ZoneInfo('Asia/Almaty')

# ─── Этапы ────────────────────────────────────────────────────────────────────

CONTACTS, RESPONSES, IN_WORK, INVITED_OFFICE, INVITED_TRAINING = (
    'contacts', 'responses', 'in_work', 'invited_office', 'invited_training')
REFUSED, ON_TRAINING, ON_LINE, DROPPED, HIRED = 'refused', 'on_training', 'on_line', 'dropped', 'hired'
# «Не отвечает звонок/чат» (владелец 20.11.2026: «те, кто в работе, не отвечают ни на звонок, ни на ватсап в течение
# 72 часов, уходили в «Не отвечает звонок/чат». Это отдельный список, как архив»): последняя колонка доски, свёрнута
# и серая, пока её не раскрыли. Карточка остаётся за рекрутером и открытой; ответ кандидата возвращает её «В работу».
NO_RESPONSE = 'no_response'

STAGES = (
    (CONTACTS, 'Контакты'),
    (RESPONSES, 'Отклики'),
    (IN_WORK, 'В работе'),
    (INVITED_OFFICE, 'Приглашен в офис'),
    (INVITED_TRAINING, 'Приглашен на обучение'),
    (REFUSED, 'Отказ'),
    (ON_TRAINING, 'Вышел на обучение'),
    (ON_LINE, 'Вышел на линию'),
    (DROPPED, 'Отсев'),
    (HIRED, 'Отработал 7 дней'),
    (NO_RESPONSE, 'Не отвечает звонок/чат'),
)
STAGE_CODES = tuple(code for code, _ in STAGES)
STAGE_LABELS = dict(STAGES)
STAGE_ORDER = {code: index for index, code in enumerate(STAGE_CODES)}

# «Отработал 10 дней» (SZOV-10, 23.11.2026: «отдельная категория для учёта отработанных смен кандидатов тренером») —
# категория тренера, а не этап: карточка остаётся в «Отработал 7 дней» (найм закрыт, рекрутеру засчитано), а с десятой
# смены попадает ещё и сюда — «отображается одновременно в категориях «Отработал 7 смен» и «Отработал 10 смен»». Отметка —
# hiring_cards.hired10_at и строка истории этапов с этим кодом: по ней её видят воронка, «дата этапа» и история карточки.
HIRED_10 = 'hired_10'
HIRED_10_LABEL = 'Отработал 10 дней'
# Колонки доски: этапы и категория тренера сразу за «Отработал 7 дней».
BOARD_COLUMNS = tuple(item for code, label in STAGES
                      for item in (((code, label), (HIRED_10, HIRED_10_LABEL)) if code == HIRED else ((code, label),)))

POOL_STAGES = (CONTACTS, RESPONSES)
FINAL_STAGES = (REFUSED, DROPPED, HIRED)
# Карточка «в работе» у рекрутера — закреплена и ещё не на обучении: по этим этапам
# считается срок без касаний (ТЗ 7.1) и задачи на сегодня.
ASSIGNED_STAGES = (IN_WORK, INVITED_OFFICE, INVITED_TRAINING)
# Сколько кандидат может молчать с первой попытки связаться (звонок без ответа или наше сообщение в WhatsApp),
# прежде чем карточка уйдёт из «В работе» в «Не отвечает звонок/чат» (решение владельца 20.11.2026: «с первой попытки»).
NO_RESPONSE_HOURS = 72
# В пул руководитель возвращает и карточку из «Не отвечает»: её закрепление то же, что «В работе».
RELEASABLE_STAGES = ASSIGNED_STAGES + (NO_RESPONSE,)
# Этапы, по которым строится воронка дашборда (ТЗ 8.1, блок 2), в порядке ТЗ; «Отработал 10 дней» — за 7 днями.
FUNNEL_STAGES = (IN_WORK, 'dialed', INVITED_OFFICE, INVITED_TRAINING, ON_TRAINING, ON_LINE, HIRED, HIRED_10)
FUNNEL_LABELS = dict(STAGES, dialed='Дозвон (хотя бы один успешный)', **{HIRED_10: HIRED_10_LABEL})

KIND_CONTACT, KIND_RESPONSE = 'contact', 'response'
KIND_LABELS = {KIND_CONTACT: 'Контакт', KIND_RESPONSE: 'Отклик'}


def pool_stage_for(kind):
    return CONTACTS if kind == KIND_CONTACT else RESPONSES


# ─── Подстатусы ───────────────────────────────────────────────────────────────

# «Недозвон / не ответил» — кандидат не ответил ни на звонок, ни на сообщение в WhatsApp (владелец
# 09.11.2026; их число — плитка в «Отчётах»). Исход самого звонка по-прежнему «Недозвон» (CALL_RESULTS).
SUBSTAGES = {
    IN_WORK: (('dial', 'Дозвон'), ('no_answer', 'Недозвон / не ответил'), ('whatsapp', 'Переписка в WhatsApp')),
    INVITED_OFFICE: (('expected', 'Ожидается'), ('came', 'Пришел'), ('no_show', 'Не пришел')),
    REFUSED: (('ours', 'Наш отказ'), ('candidate', 'Отказ кандидата')),
    DROPPED: (('self', 'Сам'), ('we', 'Мы')),
}
SUBSTAGE_LABELS = {stage: dict(items) for stage, items in SUBSTAGES.items()}


def substage_allowed(stage, substage):
    if substage is None:
        return stage not in (REFUSED, DROPPED)
    return substage in SUBSTAGE_LABELS.get(stage, {})


# После стольких недозвонов подряд рекрутеру предлагается «Отказ» с причиной
# «Не выходит на связь» (ТЗ 4.2, предложение). Только предложение — решает человек.
NO_ANSWER_SUGGEST_AFTER = 3
# Без касаний столько дней — карточка сама возвращается в пул (ТЗ 7.1: «1 месяц»).
IDLE_RETURN_DAYS = 30
# «Отработал 7 дней» = семь отработанных смен на линии (ТЗ 7.2) — засчитывается рекрутеру.
SHIFTS_TO_HIRE = 7
# «Отработал 10 дней» = десять смен — засчитывается тренеру группы (SZOV-10). 8–9 смен: рекрутеру 7 засчитаны, тренеру
# 10 — нет.
SHIFTS_FOR_TRAINER = 10
# Смена считается из статусов работы сотрудника (daily_hours.work_time), а не руками (владелец
# 30.10.2026): день, где по статусам отработано не меньше часа, — выход на линию. Обе категории ставит
# сама система, без подтверждения в карточке (23.11.2026: «проверку смен надо сделать автоматической»).
SHIFT_MIN_HOURS = 1.0


def shift_credits(stage, hired10_at, shifts):
    """Какие категории засчитать по числу смен — одним решением для обеих (SZOV-10): «Отработал 7 дней» — карточке на
    линии с семью сменами и больше, «Отработал 10 дней» — с десятью и больше, если ещё не засчитано. Десять смен за один
    пересчёт дают обе сразу; недобор до десяти ничего не отклоняет — засчитываются 7. Раз засчитанное не снимается,
    даже если статусы потом пересчитали в меньшее число."""
    shifts = int(shifts or 0)
    credits = []
    if stage == ON_LINE and shifts >= SHIFTS_TO_HIRE:
        credits.append(HIRED)
    if stage in (ON_LINE, HIRED) and not hired10_at and shifts >= SHIFTS_FOR_TRAINER:
        credits.append(HIRED_10)
    return credits

# ─── Группы на обучение ───────────────────────────────────────────────────────
# Вместимость группы задаёт тренер (владелец 18.11.2026): рекрутер, приглашая на обучение, видит, сколько
# ещё кандидатов войдёт в каждую группу, а в полную пригласить нельзя. Место занимает кандидат, которого
# пригласили в группу и который не отпал: приглашён, вышел на обучение, на линию, отработал 7 дней.
# Отказ до обучения и отсев место освобождают.
SEAT_STAGES = (INVITED_TRAINING, ON_TRAINING, ON_LINE, HIRED)
GROUP_CAPACITY_MAX = 500
# Этапы, на которых тренер карточку своей группы не видит: до приглашения на обучение она — работа рекрутера.
# Повторная активация и возврат в пул оставляют у карточки старую группу, а в пуле — телефон (разбор 18.11.2026).
TRAINER_HIDDEN_STAGES = POOL_STAGES + (IN_WORK, INVITED_OFFICE, NO_RESPONSE)


def parse_capacity(value):
    """Вместимость из формы: целое от 1 до GROUP_CAPACITY_MAX, иначе None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= GROUP_CAPACITY_MAX else None


def seats_free(capacity, taken):
    """Сколько ещё кандидатов войдёт в группу. None — вместимость не задана (группы до 18.11.2026)."""
    if capacity is None:
        return None
    return max(0, int(capacity) - int(taken or 0))

# ─── Касания ──────────────────────────────────────────────────────────────────

TOUCH_CALL, TOUCH_WHATSAPP, TOUCH_COMMENT, TOUCH_REPEAT, TOUCH_SYSTEM = (
    'call', 'whatsapp', 'comment', 'repeat_response', 'system')
TOUCH_KINDS = (TOUCH_CALL, TOUCH_WHATSAPP, TOUCH_COMMENT, TOUCH_REPEAT, TOUCH_SYSTEM)
# Касания, которые делает сам рекрутер: ими считается «есть хотя бы одно касание» (ТЗ 4.2)
# и срок без касаний (ТЗ 7.1). Повторный отклик приходит сам, системные записи — тем более.
# Для перехода из «В работе» связью считается и наше сообщение с номера WhatsApp найма — переписку
# портал видит сам (владелец 09.11.2026, service.move → queries.wa_our_message).
HUMAN_TOUCHES = (TOUCH_CALL, TOUCH_WHATSAPP, TOUCH_COMMENT)
CALL_RESULTS = (('dial', 'Дозвон'), ('no_answer', 'Недозвон'))
CALL_RESULT_CODES = tuple(code for code, _ in CALL_RESULTS)
TOUCH_LABELS = {TOUCH_CALL: 'Звонок', TOUCH_WHATSAPP: 'WhatsApp', TOUCH_COMMENT: 'Комментарий',
                TOUCH_REPEAT: 'Повторный отклик', TOUCH_SYSTEM: 'Система'}

# ─── Переходы (ТЗ 4.2) ────────────────────────────────────────────────────────
#
# who — кто вправе: owner (закреплённый рекрутер), recruiter (любой рекрутер — только
# «Взять в работу»), lead (руководитель рекрутинга / HR), trainer (тренер группы карточки),
# dept_head (руководитель отдела, куда был найм).
# needs — что обязано прийти с переходом: office (дата визита), group (группа на обучение),
# refusal (инициатор и причина), drop (инициатор, причина, день), line (дата выхода).
# «Вышел на линию» → «Отработал 7 дней» здесь нет: его делает сама система на седьмой смене из статусов
# (shift_credits, HiringService._credit_shifts), руками не переводят (23.11.2026).

TRANSITIONS = {
    (CONTACTS, IN_WORK): {'who': ('recruiter', 'lead'), 'take': True},
    (RESPONSES, IN_WORK): {'who': ('recruiter', 'lead'), 'take': True},
    (IN_WORK, INVITED_OFFICE): {'who': ('owner', 'lead'), 'needs_touch': True, 'needs': ('office',)},
    (IN_WORK, INVITED_TRAINING): {'who': ('owner', 'lead'), 'needs_touch': True, 'needs': ('group',)},
    (IN_WORK, REFUSED): {'who': ('owner', 'lead'), 'needs_touch': True, 'needs': ('refusal',)},
    (INVITED_OFFICE, INVITED_TRAINING): {'who': ('owner', 'lead'), 'substage_in': ('came',), 'needs': ('group',)},
    (INVITED_OFFICE, REFUSED): {'who': ('owner', 'lead'), 'needs': ('refusal',)},
    (INVITED_TRAINING, ON_TRAINING): {'who': ('trainer', 'owner', 'lead')},
    (INVITED_TRAINING, REFUSED): {'who': ('owner', 'lead'), 'needs': ('refusal',)},
    (ON_TRAINING, ON_LINE): {'who': ('trainer', 'owner', 'lead'), 'needs': ('line',)},
    (ON_TRAINING, DROPPED): {'who': ('trainer', 'owner', 'lead'), 'needs': ('drop',)},
    (ON_LINE, DROPPED): {'who': ('dept_head', 'owner', 'lead'), 'needs': ('drop',)},
    (REFUSED, IN_WORK): {'who': ('lead',), 'reactivate': True},
    (DROPPED, IN_WORK): {'who': ('lead',), 'reactivate': True},
    # Из «Не отвечает звонок/чат» уходят сами (ответ кандидата) или руками закреплённого рекрутера: снова в работу —
    # 72 часа считаются заново, — или в «Отказ». Туда карточку переводит только сторож (HiringService.archive_silent).
    (NO_RESPONSE, IN_WORK): {'who': ('owner', 'lead')},
    (NO_RESPONSE, REFUSED): {'who': ('owner', 'lead'), 'needs': ('refusal',)},
}

# Подсказки интерфейсу: какие переходы вообще есть из этапа (в порядке колонок).
TARGETS_FROM = {}
for (_src, _dst) in TRANSITIONS:
    TARGETS_FROM.setdefault(_src, []).append(_dst)
for _src in TARGETS_FROM:
    TARGETS_FROM[_src].sort(key=STAGE_ORDER.get)


class TransitionError(ValueError):
    """Переход невозможен. code — короткий код для ответа ручки, message — человеку."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def actor_kinds(ctx_caps, card, ctx_user_id, trainer_group_ids=()):
    """Кем выступает человек по отношению к карточке.

    ctx_caps — словарь access.capabilities(ctx); card — словарь карточки с recruiter_id,
    group_id и department_id (отдел вакансии); trainer_group_ids — группы, где человек тренер.
    """
    kinds = set()
    if ctx_caps.get('lead'):
        kinds.add('lead')
    if ctx_caps.get('recruiter') or ctx_caps.get('lead'):
        kinds.add('recruiter')
    # Владелец — только действующий рекрутер или руководитель: снятая выдача снимает и права на свои карточки.
    if (kinds & {'recruiter', 'lead'}) and card.get('recruiter_id') and int(card['recruiter_id']) == int(ctx_user_id):
        kinds.add('owner')
    if card.get('group_id') and int(card['group_id']) in {int(g) for g in trainer_group_ids}:
        kinds.add('trainer')
    headed = {int(x) for x in (ctx_caps.get('headed_ids') or ())}
    if card.get('department_id') and int(card['department_id']) in headed:
        kinds.add('dept_head')
    if ctx_caps.get('super_admin'):
        kinds.add('dept_head')
    return kinds


def check_transition(card, target, kinds, *, has_touch, payload):
    """Проверить переход карточки в target. Возвращает описание перехода или бросает
    TransitionError. payload — что пришло с переходом (office_visit_at, group_id, refusal…)."""
    source = card.get('stage')
    rule = TRANSITIONS.get((source, target))
    if rule is None:
        raise TransitionError('HIRING_BAD_TRANSITION', 'Из «%s» в «%s» карточка не переходит' % (
            STAGE_LABELS.get(source, source), STAGE_LABELS.get(target, target)))
    if not kinds.intersection(rule['who']):
        raise TransitionError('HIRING_FORBIDDEN', _who_message(rule['who']))
    if rule.get('take') and card.get('recruiter_id'):
        raise TransitionError('HIRING_ALREADY_TAKEN', 'Уже в работе')
    if rule.get('needs_touch') and not has_touch:
        raise TransitionError('HIRING_NO_TOUCH', 'Сначала свяжитесь с кандидатом: звонок, сообщение с номера WhatsApp найма или комментарий')
    if rule.get('substage_in') and card.get('substage') not in rule['substage_in']:
        raise TransitionError('HIRING_NOT_CAME', 'Переход только при отметке «Пришел»; не пришёл — назначьте новую дату или оформите отказ')
    for need in rule.get('needs', ()):
        _check_need(need, payload)
    return rule


_WHO_LABELS = {
    'owner': 'закреплённый рекрутер', 'recruiter': 'рекрутер', 'lead': 'руководитель рекрутинга',
    'trainer': 'тренер группы', 'dept_head': 'руководитель отдела, куда был найм',
}


def _who_message(who):
    return 'Этот переход делает ' + ' или '.join(_WHO_LABELS[w] for w in who)


def _check_need(need, payload):
    payload = payload or {}
    if need == 'office' and not payload.get('office_visit_at'):
        raise TransitionError('HIRING_NEED_OFFICE_DATE', 'Укажите дату и время собеседования в офисе')
    if need == 'group' and not payload.get('group_id'):
        raise TransitionError('HIRING_NEED_GROUP', 'Укажите группу на обучение')
    if need == 'refusal':
        if payload.get('initiator') not in SUBSTAGE_LABELS[REFUSED]:
            raise TransitionError('HIRING_NEED_INITIATOR', 'Укажите, чей это отказ: наш или кандидата')
        if not payload.get('reason_id'):
            raise TransitionError('HIRING_NEED_REASON', 'Укажите причину отказа')
    if need == 'drop':
        if payload.get('initiator') not in SUBSTAGE_LABELS[DROPPED]:
            raise TransitionError('HIRING_NEED_INITIATOR', 'Укажите инициатора отсева: сам или мы')
        if not payload.get('reason_id'):
            raise TransitionError('HIRING_NEED_REASON', 'Укажите причину отсева')
        if not _positive_int(payload.get('day')):
            raise TransitionError('HIRING_NEED_DAY', 'Укажите день отсева — номер дня обучения или день на линии')
    if need == 'line' and not payload.get('line_started_at'):
        raise TransitionError('HIRING_NEED_LINE_DATE', 'Укажите дату выхода на линию')


def _positive_int(value):
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def drop_stage_for(source_stage):
    """Этап отсева: на обучении или на линии (ТЗ 6.3)."""
    return 'line' if source_stage == ON_LINE else 'training'


DROP_STAGE_LABELS = {'training': 'Обучение', 'line': 'Линия'}

# ─── Справочники из ТЗ ────────────────────────────────────────────────────────

REASON_REFUSAL_OURS, REASON_REFUSAL_CANDIDATE = 'refusal_ours', 'refusal_candidate'
REASON_DROP_SELF, REASON_DROP_WE = 'drop_self', 'drop_we'
REASON_KINDS = (REASON_REFUSAL_OURS, REASON_REFUSAL_CANDIDATE, REASON_DROP_SELF, REASON_DROP_WE)
REASON_KIND_LABELS = {
    REASON_REFUSAL_OURS: 'Наш отказ', REASON_REFUSAL_CANDIDATE: 'Отказ кандидата',
    REASON_DROP_SELF: 'Отсев «сам»', REASON_DROP_WE: 'Отсев «мы»',
}
# Инициатор перехода → вид причины.
REASON_KIND_FOR = {
    (REFUSED, 'ours'): REASON_REFUSAL_OURS, (REFUSED, 'candidate'): REASON_REFUSAL_CANDIDATE,
    (DROPPED, 'self'): REASON_DROP_SELF, (DROPPED, 'we'): REASON_DROP_WE,
}

OTHER = 'Другое'
SEED_REASONS = {
    REASON_REFUSAL_OURS: (
        'Не подходит по возрасту',
        'Недостаточный водительский стаж / нет прав нужной категории',
        'Нет необходимых документов',
        'Не прошел проверку (штрафы, лишение прав и т. п.)',
        'Не прошел собеседование в офисе',
        'Языковой барьер',
        'Некорректное поведение',
        'Нет потребности по вакансии / в городе',
        'Уже работает у нас / дубль',
        OTHER,
    ),
    REASON_REFUSAL_CANDIDATE: (
        'Не устроил доход',
        'Не устроили условия: комиссия, аренда авто, график',
        'Далеко добираться / другой город',
        'Нашел другую работу',
        'Передумал / неактуально',
        'Не пришел в офис',
        'Не пришел на обучение',
        'Не выходит на связь',
        OTHER,
    ),
    REASON_DROP_SELF: (
        'Не устроил доход / условия',
        'Не понравилась работа',
        'Нашел другую работу',
        'Личные обстоятельства',
        'Пропал без объяснения',
        OTHER,
    ),
    REASON_DROP_WE: (
        'Пропуски обучения',
        'Не сдал аттестацию',
        'Низкое качество работы на линии',
        'Нарушение правил и дисциплины',
        OTHER,
    ),
}
# Причины, которые интерфейс подставляет сам (ТЗ 4.2): не пришёл в офис / на обучение,
# три недозвона подряд.
REASON_NO_SHOW_OFFICE = 'Не пришел в офис'
REASON_NO_SHOW_TRAINING = 'Не пришел на обучение'
REASON_NO_CONTACT = 'Не выходит на связь'

SOURCE_CONTACTS, SOURCE_RESPONSES = 'contacts', 'responses'
# (code, name, kind, platform) — platform: откуда приходит само (hh, olx, webhook, whatsapp) или None.
SEED_SOURCES = (
    ('hh', 'HeadHunter', SOURCE_RESPONSES, 'hh'),
    ('kaspi', 'Kaspi', SOURCE_RESPONSES, 'webhook'),
    ('olx', 'OLX', SOURCE_RESPONSES, 'olx'),
    ('target', 'Таргет', SOURCE_RESPONSES, 'webhook'),
    ('manual', 'Вручную', SOURCE_RESPONSES, None),
    ('referral', 'Приведи друга', SOURCE_RESPONSES, None),
    ('base_ex_drivers', 'База бывших водителей', SOURCE_CONTACTS, None),
    ('base_bought', 'Купленная база', SOURCE_CONTACTS, None),
    ('base_partners', 'База партнеров', SOURCE_CONTACTS, None),
)
# Появились после выкладки раздела — доливаются в заполненный справочник по коду (schema._seed).
# WhatsApp — номер найма, куда ведёт таргет (владелец 06.11.2026, hiring/whatsapp.py).
LATE_SOURCES = (
    ('whatsapp', 'WhatsApp', SOURCE_RESPONSES, 'whatsapp'),
)

# Цвет источника (владелец 20.11.2026: «понятие Канал надо убрать и всё запихать в источник… добавить возможность
# выбрать цвет прямо в источниках»). Каналы — бывшие метки откликов с цветом (09.11.2026) — слиты с источниками
# (schema._merge_channels_into_sources): то, откуда кандидат пришёл, теперь одно поле карточки. Цвет — из палитры, как
# у списков «Напоминаний», коды те же, что SOURCE_COLORS в hiringMeta.js; источник без цвета — серый. Стартовые цвета —
# словами владельца о каналах 09.11.2026: Таргет — синий, hh — красный, Каспи — розовый.
SOURCE_COLORS = ('red', 'orange', 'yellow', 'green', 'teal', 'blue', 'violet', 'pink', 'gray')
SEED_SOURCE_COLORS = {'target': 'blue', 'hh': 'red', 'kaspi': 'pink'}
SOURCE_NAME_MAX = 120
# «Таргет» ставится сам (владелец 09.11.2026: «те что пришли с таргета нужно автоматом отмечать как Таргет»): отклик
# WhatsApp по рекламе (whatsapp.from_ads) приходит с источником «Таргет», а не «WhatsApp».
SOURCE_TARGET = 'target'
# Источники, которые говорят, КАК отклик пришёл, а не откуда человек узнал о вакансии: номер WhatsApp найма и ввод
# руками. Повторный отклик с «Таргета» делает такую карточку «Таргетом» — как раньше ставилась метка «Таргет» карточке
# без метки; площадки (hh, OLX, Kaspi) и «Приведи друга» — уже канал, их источник не перетирается.
SOURCES_WITHOUT_CHANNEL = ('whatsapp', 'manual')

MOTIVATION_PENDING, MOTIVATION_COUNTED, MOTIVATION_PAID, MOTIVATION_CANCELLED = (
    'pending', 'counted', 'paid', 'cancelled')
# «Ждёт 7 смен»: подтверждать начисление больше некому — его засчитывает седьмая смена сама (SZOV-10).
MOTIVATION_LABELS = {MOTIVATION_PENDING: 'Ждёт 7 смен', MOTIVATION_COUNTED: 'Засчитано',
                     MOTIVATION_PAID: 'Выплачено', MOTIVATION_CANCELLED: 'Отменено'}

GRANT_LEAD, GRANT_STAFF, GRANT_REMOTE = 'lead', 'staff', 'remote'
GRANT_KINDS = (GRANT_LEAD, GRANT_STAFF, GRANT_REMOTE)
GRANT_LABELS = {GRANT_LEAD: 'Руководитель рекрутинга', GRANT_STAFF: 'Штатный рекрутер',
                GRANT_REMOTE: 'Удалённый рекрутер'}

# ─── Телефон ──────────────────────────────────────────────────────────────────

_DIGITS = re.compile(r'\D+')


def normalize_phone(value):
    """К виду +7XXXXXXXXXX (ТЗ 5.1). None — номер не казахстанский мобильный/городской:
    принимаются 10 цифр, 11 с ведущей 7 или 8, а также с «+7»."""
    digits = _DIGITS.sub('', str(value or ''))
    if len(digits) == 11 and digits[0] in '78':
        digits = digits[1:]
    if len(digits) != 10 or not digits.isdigit():
        return None
    return '+7' + digits


def phone_key(value):
    """Последние десять цифр — ключ сравнения с телефонами сотрудников в любой записи."""
    normalized = normalize_phone(value)
    return normalized[-10:] if normalized else None


def pretty_phone(value):
    """«+7 705 123 45 67» — так телефон читается в карточке и в Telegram."""
    normalized = normalize_phone(value)
    if not normalized:
        return str(value or '')
    d = normalized[2:]
    return '+7 %s %s %s %s' % (d[:3], d[3:6], d[6:8], d[8:])


def whatsapp_link(value):
    normalized = normalize_phone(value)
    return 'https://web.whatsapp.com/send?phone=%s' % normalized[1:] if normalized else None

# ─── Время ────────────────────────────────────────────────────────────────────


def now_almaty():
    return datetime.now(ALMATY).replace(tzinfo=None)


def month_bounds(month):
    """'2026-10' → (1 октября 00:00, 1 ноября 00:00)."""
    year, mon = (int(x) for x in str(month).split('-')[:2])
    start = datetime(year, mon, 1)
    end = datetime(year + (mon // 12), mon % 12 + 1, 1)
    return start, end


def month_of(moment):
    return '%04d-%02d' % (moment.year, moment.month)


def idle_deadline(now, days=IDLE_RETURN_DAYS):
    return now - timedelta(days=days)


def through_rate(reached, base):
    """Конверсия в процентах с одним знаком; None — делить не на что."""
    if not base:
        return None
    return round(100.0 * reached / base, 1)
