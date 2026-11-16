# -*- coding: utf-8 -*-
"""Правила сторожа линий Binotel — чистые функции: ни базы, ни сети.

Три взгляда на одного оператора:
  * телефон — последнее событие iCORE Phone («готов», «занят», «перезвон», «перерыв»,
    «выключен»…): что оператор выбрал сам;
  * кабинет — presence в карточке сотрудника Binotel (active / work in crm / break in work /
    inactive): по нему Binotel решает, слать ли оператору звонки;
  * линия — видит ли Binotel телефон (online / inuse / offline).

Сторож ничего не считает «по ощущению»: расхождение — это названный ниже вид, и у каждого
своё условие по времени, чтобы секунды между событием телефона и записью в кабинет не
превращались в ложные срабатывания.
"""
import re

PRESENCE_ACTIVE = 'active'
PRESENCE_CRM = 'work in crm'
PRESENCE_BREAK = 'break in work'
PRESENCE_INACTIVE = 'inactive'
# Коды presence — те же, что телефон шлёт в кабинет (BinotelClient.h телефона). Кода 2 нет.
PRESENCE_CODES = {PRESENCE_ACTIVE: 0, PRESENCE_CRM: 1, PRESENCE_BREAK: 3, PRESENCE_INACTIVE: 4}

PHONE_READY = 'готов'
PHONE_TALK = 'занят'
PHONE_OFF = 'выключен'
# Что телефон ставит в кабинет на каждый свой статус (таблица ICoreStatus.h телефона).
# «занят» здесь нет намеренно: это разговор, а выбранный под ним статус неизвестен.
DESIRED_BY_PHONE = {
    PHONE_READY: PRESENCE_ACTIVE,
    'перезвон': PRESENCE_CRM,
    'исход': PRESENCE_CRM,
    'перерыв': PRESENCE_BREAK,
    'тренинг': PRESENCE_BREAK,
    'тех причина': PRESENCE_BREAK,
    'офлайн': PRESENCE_BREAK,
    PHONE_OFF: PRESENCE_INACTIVE,
}

SIP_OFFLINE = 'offline'

# ── виды расхождений ────────────────────────────────────────────────────────
# Оператор принимает звонки («готов» или уже говорит), а кабинет его закрыл.
KIND_CABINET_CLOSED = 'cabinet_closed'
# Оператор закрыт («Исход», перерыв…), а в кабинете «Активен»: очередь звонит на телефон,
# который ответит «занято».
KIND_CABINET_OPEN = 'cabinet_open'
# Телефон выключен, а в кабинете не «Неактивен»: статус остался от прошлого сеанса.
KIND_CABINET_STALE = 'cabinet_stale'
# И телефон, и кабинет «Активен», а Binotel не видит линию.
KIND_LINE_OFFLINE = 'line_offline'
# Клиент ждал в очереди, пока оператор был свободен («готов» и «Активен» всё ожидание).
KIND_QUEUE_HELD = 'queue_held'
# Звонок шёл оператору 10+ секунд и не был принят, хотя телефон «готов» (автоприём 2–3 с).
KIND_NO_PICKUP = 'no_pickup'
# Звонок на группу (линия отдела продаж) сорвался, хотя оператор отдела был готов.
KIND_GROUP_DROP = 'group_drop'

STATE_KINDS = (KIND_CABINET_CLOSED, KIND_CABINET_OPEN, KIND_CABINET_STALE, KIND_LINE_OFFLINE)
CALL_KINDS = (KIND_QUEUE_HELD, KIND_NO_PICKUP, KIND_GROUP_DROP)

# Сколько секунд обе стороны должны простоять без изменений, чтобы расхождение считалось:
# телефон шлёт событие в iCORE и запись в кабинет разными запросами, между ними 1–3 с.
SETTLE_SECONDS = 90
# «Выключен» и не-«Неактивен»: телефон при выходе ставит «Неактивен» сам и ждёт до 8 с;
# дольше десяти минут — уже не выход в процессе, а оставшийся статус.
STALE_AFTER_SECONDS = 600
# Линию «не в сети» Binotel отмечает обходом, а не по регистрации — короткий провал не в счёт.
OFFLINE_AFTER_SECONDS = 180
# Ожидание в очереди, с которого клиент считается задержанным (порог SL кабинета — 20 с).
QUEUE_HELD_SECONDS = 20
# Плечо оператора без ответа, с которого это «не снял трубку»: автоприём — 2–3 с, сброс клиентом
# в первые секунды — не вина телефона.
NO_PICKUP_SECONDS = 10

ANSWERED = 'ANSWER'
# Плечо кончилось не разговором и не отбоем клиента.
FAILED_LEG_DISPOSITIONS = ('BUSY', 'NOANSWER', 'CHANUNAVAIL', 'CONGESTION')
DROPPED_DISPOSITIONS = ('CHANUNAVAIL', 'BUSY', 'CONGESTION')

_GROUP_LEG_RE = re.compile(r'^\s*(\d+)\s*>\s*(\d+)\s*$')
_HISTORY_KEY_RE = re.compile(r'^callDetails\[historyData\]\[(\d+)\]\[([A-Za-z]+)\]$')


def normalize_presence(value):
    return str(value or '').strip().lower()


def desired_presence(phone_key):
    """Presence, который телефон ставит в кабинет при этом статусе; None — не знаем."""
    return DESIRED_BY_PHONE.get(str(phone_key or '').strip().lower())


def _age_at_least(age, seconds):
    """Возраст не меньше порога; неизвестный возраст порог проходит (стоит давно)."""
    return age is None or age >= seconds


def classify(phone_key, phone_age, presence, presence_age, sip=None, sip_age=None):
    """Вид расхождения по состоянию линии или None.

    phone_key — последнее событие телефона (None — событий нет в окне), phone_age — сколько
    секунд назад; presence — presence кабинета, presence_age — сколько секунд он стоит;
    sip — online / inuse / offline, sip_age — сколько секунд в этом состоянии."""
    presence = normalize_presence(presence)
    if presence not in PRESENCE_CODES:
        return None                       # кабинет не знает этого сотрудника — сверять не с чем
    key = str(phone_key or '').strip().lower()
    if not key or key == PHONE_OFF:
        if presence == PRESENCE_INACTIVE:
            return None
        if _age_at_least(phone_age, STALE_AFTER_SECONDS) and _age_at_least(presence_age, STALE_AFTER_SECONDS):
            return KIND_CABINET_STALE
        return None
    if key == PHONE_TALK:
        # Разговор идёт: «Работа в CRM» под ним законна (звонок из «Исхода»), а «Перерыв» и
        # «Неактивен» — нет: из них телефон не звонит и входящих не принимает.
        if presence in (PRESENCE_INACTIVE, PRESENCE_BREAK) and _age_at_least(presence_age, SETTLE_SECONDS):
            return KIND_CABINET_CLOSED
        return None
    want = desired_presence(key)
    if want is None:
        return None
    settled = _age_at_least(phone_age, SETTLE_SECONDS) and _age_at_least(presence_age, SETTLE_SECONDS)
    if want == PRESENCE_ACTIVE:
        if presence != PRESENCE_ACTIVE:
            return KIND_CABINET_CLOSED if settled else None
        if (str(sip or '').strip().lower() == SIP_OFFLINE
                and _age_at_least(phone_age, OFFLINE_AFTER_SECONDS)
                and _age_at_least(sip_age, OFFLINE_AFTER_SECONDS)):
            return KIND_LINE_OFFLINE
        return None
    if want == PRESENCE_INACTIVE:
        return None
    # Телефон закрыт от входящих. «Работа в CRM» вместо «Перерыва» и наоборот очередь закрывают
    # одинаково — расхождением считаем только «Активен».
    if presence == PRESENCE_ACTIVE and settled:
        return KIND_CABINET_OPEN
    return None


def heal_target(kind, phone_key):
    """Какой presence поставить в кабинет, чтобы он совпал с телефоном; None — не чинится записью.

    line_offline и события звонков записью presence не лечатся: там кабинет уже «прав»."""
    key = str(phone_key or '').strip().lower()
    if kind == KIND_CABINET_STALE:
        return PRESENCE_INACTIVE
    if kind == KIND_CABINET_CLOSED:
        return PRESENCE_ACTIVE if key == PHONE_READY else None
    if kind == KIND_CABINET_OPEN:
        return desired_presence(key)
    return None


# ── звонок из вебхука «API Call Completed» ──────────────────────────────────
def _to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _field(fields, name):
    value = fields.get('callDetails[%s]' % name)
    if value in (None, ''):
        value = fields.get(name)
    return value


def call_from_webhook(fields):
    """Поля вебхука (dial_list.service.webhook_fields) → звонок с плечами или None.

    Плечи historyData Binotel отдаёт от последнего к первому; здесь они в порядке звонка.
    Номер клиента и данные сотрудника не берём вовсе — сторожу нужны только линии и секунды."""
    if not hasattr(fields, 'get'):
        return None
    general_call_id = str(_field(fields, 'generalCallID') or '').strip()
    start = _to_int(_field(fields, 'startTime'))
    if not general_call_id or start <= 0:
        return None
    by_index = {}
    for key, value in fields.items():
        match = _HISTORY_KEY_RE.match(str(key))
        if not match:
            continue
        by_index.setdefault(int(match.group(1)), {})[match.group(2)] = value
    legs = []
    for index in sorted(by_index, reverse=True):
        raw = by_index[index]
        legs.append({
            'target': str(raw.get('internalNumber') or '').strip(),
            'waitsec': max(0, _to_int(raw.get('waitsec'))),
            'billsec': max(0, _to_int(raw.get('billsec'))),
            'disposition': str(raw.get('disposition') or '').strip().upper(),
        })
    # Момент начала каждого плеча: всё, что было до него, — ожидание и разговор прежних плеч.
    moment = start
    for leg in legs:
        leg['start'] = moment
        moment += leg['waitsec'] + leg['billsec']
    return {
        'general_call_id': general_call_id[:32],
        'incoming': _to_int(_field(fields, 'callType'), -1) == 0,
        'start': start,
        'waitsec': max(0, _to_int(_field(fields, 'waitsec'))),
        'billsec': max(0, _to_int(_field(fields, 'billsec'))),
        'disposition': str(_field(fields, 'disposition') or '').strip().upper(),
        'legs': legs,
    }


def leg_line(target):
    """Линия оператора, которому шло плечо: «915» или «802 > 924» (группа → линия); иначе None."""
    text = str(target or '').strip()
    if text.isdigit():
        return text
    match = _GROUP_LEG_RE.match(text)
    return match.group(2) if match else None


def leg_group(target):
    """Номер группы, если плечо шло через группу («802 > 924»), иначе None."""
    match = _GROUP_LEG_RE.match(str(target or ''))
    return match.group(1) if match else None


def is_queue_leg(target):
    """Плечо ожидания в очереди. Узнаём по слову кабинета: переименуют — сторож перестанет
    видеть задержки, и это будет видно по пустому журналу, а не по ложным записям."""
    return 'очеред' in str(target or '').strip().lower()


def queue_wait(call):
    """(момент входа в очередь, секунд в очереди) или None, если в очереди не ждали."""
    if not call or not call.get('incoming'):
        return None
    start, total = None, 0
    for leg in call.get('legs') or []:
        if is_queue_leg(leg.get('target')):
            if start is None:
                start = leg['start']
            total += leg['waitsec']
    if start is None or total <= 0:
        return None
    return start, total


def failed_operator_legs(call, lines):
    """Плечи «звонил оператору — не взял»: [(линия, момент, секунд, итог плеча)].

    lines — линии нашего состава: по ним плечо оператора отличается от плеча группы («802»)."""
    out = []
    if not call or not call.get('incoming'):
        return out
    known = {str(n) for n in (lines or [])}
    for leg in call.get('legs') or []:
        line = leg_line(leg.get('target'))
        if not line or line not in known:
            continue
        if leg['disposition'] in FAILED_LEG_DISPOSITIONS and leg['waitsec'] >= NO_PICKUP_SECONDS:
            out.append((line, leg['start'], leg['waitsec'], leg['disposition']))
    return out


def group_drop(call, lines):
    """(момент, номер группы), если входящий сорвался на группе и до оператора не дошёл; иначе None.

    Группа — числовая цель плеча, которой нет среди линий состава («802»), либо «802 > 924»
    с отказом. Очередь ТП сюда не попадает: у неё своё плечо и свой вид (queue_held)."""
    if not call or not call.get('incoming') or call.get('billsec'):
        return None
    if call.get('disposition') not in DROPPED_DISPOSITIONS:
        return None
    known = {str(n) for n in (lines or [])}
    for leg in call.get('legs') or []:
        target = str(leg.get('target') or '').strip()
        if leg['disposition'] not in DROPPED_DISPOSITIONS:
            continue
        group = leg_group(target)
        if group:
            return leg['start'], group
        if target.isdigit() and target not in known:
            return leg['start'], target
    return None
