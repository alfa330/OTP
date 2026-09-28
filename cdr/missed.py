# -*- coding: utf-8 -*-
"""Пропущенные входящие → amoCRM: решение «передавать или нет» (задача #291).

Постановка отдела продаж: клиента, которому не ответили на входящей линии, надо
автоматически завести в amoCRM новой сделкой с тегом «Пропущенный входящий», чтобы
оператор ему перезвонил. Но не сразу: после пропущенного звонка система ждёт минуту и
смотрит, не дозвонился ли клиент за это время. Дозвонился — ничего не передаём.

Модуль чистый: ни сети, ни базы. На вход — сам непринятый звонок, остальные звонки того же
номера и то, что станция видит в очередях прямо сейчас; на выход — решение. Поэтому его
можно прогнать на сохранённых сутках и сверить руками (tests/test_cdr_missed_decision.py).

Что считается пропущенным
-------------------------
Касание «Входящий (не приняли)»: клиент дошёл до очереди, а разговора с оператором не
было — «Занято», «Не ответил», сброс в очереди. Это те же «потерянные», что на «Табло ОП».
Положившие трубку на приветствии («не дошёл до очереди») сюда не входят — решение владельца
28.09.2026: оператору такой звонок не поступал, и половина их случайна (медиана 6–7 с).

От какой минуты ждём
--------------------
От КОНЦА пропущенного звонка, а не от начала: клиент, прождавший в очереди две минуты,
кладёт трубку и перезванивает — «минута после звонка» начинается с отбоя. Конец —
вход в очередь плюс ожидание по журналу очередей станции, а без них — начало строки CDR
плюс её длительность.

Ловушки, из-за которых код такой
--------------------------------
* **CDR пишет звонок только после отбоя.** Клиент перезвонил через 30 секунд и ещё говорит
  с оператором — в касаниях его нет. За две недели 14–27.09.2026 так было 19 раз из 678:
  перезвонил в течение минуты и дозвонился, и почти все эти разговоры длиннее минуты.
  Поэтому мост присылает звонки из журнала очередей, которые идут прямо сейчас
  (`queue_calls`): ответили — не передаём, клиент ещё ждёт в очереди — ждём и мы.
* **Перезвон на приветствии не виден нигде** — ни в CDR, ни в журнале очередей, пока
  клиент не вошёл в очередь (до 30 с). Поэтому решение ждёт данных, снятых позже конца
  минуты на приветствие и доставку (SETTLE_SECONDS), а устаревший снимок очередей при
  живом мосте — повод ждать, а не решать по одним касаниям.
* **Перезвонил и снова не дозвонился** — это тот же клиент с тем же вопросом. Решение
  переносится на последний звонок цепочки (`chained`): иначе клиент, трижды набравший
  подряд и дозвонившийся с третьего раза, получил бы сделку по первому звонку.
* **Решение бывает запоздалым** (портал перезапускался, мост молчал). Тогда «дозвонился»
  засчитывается и позже минуты: сделка на перезвон клиенту, который уже поговорил с
  оператором, — чистый шум, ради которого ТЗ и просит ждать.
* **Исходящий с разговором тоже считается ответом.** ТЗ говорит о входящем, но цель
  его — «только те клиенты, которым действительно не ответили»: если оператор сам
  перезвонил и поговорил, перезванивать ещё раз незачем. Ограничение: исходящий виден
  только после отбоя (журнал очередей его не знает), поэтому перезвон оператора, который
  ещё идёт в момент решения, сделку не остановит — на неделе 21–27.09.2026 так был 1 звонок.
"""

from datetime import datetime, timedelta

from cdr import queue_facts, touches as touches_mod

# Сколько ждать после пропущенного звонка — из ТЗ.
WAIT_SECONDS = 60

# Запас на доставку: строка CDR появляется у станции после отбоя, мост читает её раз в
# двадцать секунд.
DATA_GRACE_SECONDS = 20

# Самое долгое приветствие перед очередью. Клиент, перезвонивший в последние секунды
# минуты, до входа в очередь не виден НИГДЕ: строки CDR нет (звонок идёт), в журнале
# очередей его ещё нет (он слушает автоинформатор). Замер по входящим 21–28.09.2026
# (2027 звонков): медиана 14 с, p99 26 с, максимум 30 с — у 3040; у 3001 и 3041 26–29 с.
GREETING_SECONDS = 30

# Итого решение принимается, только когда портал получил данные, снятые позже конца
# минуты ожидания на приветствие и доставку: перезвон, пришедший в последнюю секунду
# минуты, к этому моменту уже в очереди и виден мосту.
SETTLE_SECONDS = GREETING_SECONDS + DATA_GRACE_SECONDS

# Дольше этого после конца минуты не ждём ничего — ни моста, ни клиента в очереди.
# Иначе застрявший звонок в очереди или замолчавший мост держали бы решение вечно,
# а сделка на перезвон, заведённая через час, клиенту уже не поможет.
MAX_HOLD_SECONDS = 15 * 60

WAIT = 'wait'
ANSWERED = 'answered'
CHAINED = 'chained'
TRANSFER = 'transfer'


def _moment(value):
    """Время касания → наивное местное время. Из базы приходит datetime, из тестов —
    строка «ГГГГ-ММ-ДД ЧЧ:ММ:СС»; пустое и мусор — None."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value or '').strip()[:19].replace('T', ' ')
    if len(text) < 19:
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def _hms(moment):
    return moment.strftime('%H:%M:%S') if moment else '—'


def call_end(touch):
    """Когда закончился звонок: самая поздняя из оценок, которые у касания есть.

    Журнал очередей называет вход в очередь и ожидание до отбоя точно; строка CDR —
    начало и длительность того плеча, которое выбрала надстройка станции. Берём позднюю:
    ранняя оценка сократила бы минуту ожидания, поздняя — лишь чуть отодвинет решение."""
    started = _moment(touch.get('started_at'))
    candidates = []
    if started is not None:
        candidates.append(started + timedelta(seconds=int(touch.get('dial_seconds') or 0)))
    queued = _moment(touch.get('queued_at'))
    wait = touch.get('wait_seconds')
    if queued is not None and wait is not None:
        candidates.append(queued + timedelta(seconds=int(wait)))
    return max(candidates) if candidates else None


def _answered_touch(touch):
    """Касание, в котором с клиентом поговорили: принятый входящий или исходящий с
    разговором. Ноль секунд разговора — не разговор (повторный набор автодозвона)."""
    talk = int(touch.get('talk_seconds') or 0)
    if talk <= 0:
        return None
    if touch.get('call_type') == touches_mod.TYPE_IN:
        return 'in'
    if touch.get('call_type') == touches_mod.TYPE_OUT:
        return 'out'
    return None


def _queue_start(call):
    """Когда звонок из очереди станции пришёл: секунда прихода из linkedid, а без неё —
    вход в очередь.

    Приход, а не вход: касание в CDR начинается с прихода, и «перезвонил в течение минуты»
    обязано считаться одинаково, пока звонок в очереди и когда он уже касание. Иначе
    перезвон, пришедший за 10 с до конца минуты и попавший в очередь после приветствия
    в 26 с, в очереди не удерживал бы решение, а касанием — сводился бы в цепочку.
    Сверено прогоном по неделе 21–27.09.2026: так было с одним звонком из 256.

    Приход верим, только если он не позже входа и не раньше его больше чем на длину самого
    долгого приветствия: иначе linkedid не метка времени, а мусор."""
    queued = _moment(call.get('queued_at'))
    arrival = queue_facts.arrival_from_linkedid(call.get('linkedid'))
    if arrival is None:
        return queued
    if queued is None:
        return arrival
    greeting = (queued - arrival).total_seconds()
    return arrival if 0 <= greeting <= queue_facts.MAX_IVR_SECONDS else queued


class Decision(object):
    """Итог разбора одного пропущенного звонка.

    kind      — WAIT | ANSWERED | CHAINED | TRANSFER
    reason    — фраза для человека: почему так (в журнал и во всплывающую подсказку)
    next_linkedid — у CHAINED: звонок, на который перенесено решение
    ended_at, deadline — конец звонка и конец минуты ожидания
    """

    __slots__ = ('kind', 'reason', 'next_linkedid', 'ended_at', 'deadline')

    def __init__(self, kind, reason, ended_at=None, deadline=None, next_linkedid=None):
        self.kind = kind
        self.reason = reason
        self.ended_at = ended_at
        self.deadline = deadline
        self.next_linkedid = next_linkedid

    def __repr__(self):  # pragma: no cover - для отладки
        return 'Decision(%s, %r)' % (self.kind, self.reason)


def decide(missed, others, now, fresh_until, queue_calls=None, queue_fresh_until=None):
    """Что делать с пропущенным звонком прямо сейчас.

    missed      — касание «Входящий (не приняли)» (словарь, как в cdr_touches);
    others      — остальные касания ТОГО ЖЕ номера (порядок не важен, сам звонок
                  среди них может быть — он пропускается);
    now         — текущее местное время;
    fresh_until — до какого момента касания на портале полные (когда мост в последний раз
                  прислал живое приращение), местное время или None;
    queue_calls — звонки этого номера из журнала очередей, которых ещё нет в CDR
                  (идут прямо сейчас или только что кончились), или None — мост их не
                  присылал НИКОГДА (старая версия моста): тогда решение по одним касаниям;
    queue_fresh_until — когда этот список снят.
    """
    ended = call_end(missed)
    started = _moment(missed.get('started_at'))
    if ended is None or started is None:
        # Без времени звонка минуту не отсчитать. Такого касания склейка не даёт, но
        # передавать «на всякий случай» нельзя: решение должно быть объяснимо.
        return Decision(WAIT, 'у звонка нет времени начала')
    deadline = ended + timedelta(seconds=WAIT_SECONDS)

    if str(missed.get('hangup_side') or ''):
        # Журнал очередей назвал сторону отбоя разговора — значит, оператор трубку снял,
        # и «не приняли» — ошибка представителя строки у надстройки станции.
        return Decision(ANSWERED, 'По журналу очередей разговор с оператором состоялся',
                        ended, deadline)

    if now < deadline:
        return Decision(WAIT, 'идёт минута ожидания', ended, deadline)
    held_too_long = now > deadline + timedelta(seconds=MAX_HOLD_SECONDS)

    later = []
    for touch in others or ():
        if str(touch.get('linkedid') or '') == str(missed.get('linkedid') or ''):
            continue
        moment = _moment(touch.get('started_at'))
        if moment is not None and moment > started:
            later.append((moment, touch))
    later.sort(key=lambda pair: pair[0])

    # Дозвонился — не передаём. Смотрим до «сейчас», а не только до конца минуты: решение
    # могло запоздать, и заводить перезвон тому, кто уже поговорил, незачем.
    horizon = max(deadline, now)
    for moment, touch in later:
        if moment > horizon:
            break
        kind = _answered_touch(touch)
        if kind == 'in':
            return Decision(ANSWERED, 'Клиент перезвонил в %s и поговорил с оператором'
                            % _hms(moment), ended, deadline)
        if kind == 'out':
            return Decision(ANSWERED, 'Оператор перезвонил клиенту в %s и поговорил'
                            % _hms(moment), ended, deadline)

    # Перезвонил в течение минуты и снова не дозвонился — решает последний звонок.
    for moment, touch in later:
        if moment > deadline:
            break
        if touch.get('call_type') == touches_mod.TYPE_IN_MISSED:
            return Decision(CHAINED, 'Клиент перезвонил в %s и снова не дозвонился — '
                            'решение по последнему звонку' % _hms(moment),
                            ended, deadline, next_linkedid=str(touch.get('linkedid') or ''))

    # Решать можно только по данным, снятым позже конца минуты на приветствие и доставку:
    # перезвон последней секунды минуты к этому моменту уже вошёл в очередь.
    settled = deadline + timedelta(seconds=SETTLE_SECONDS)

    # То, чего в CDR ещё нет: звонок того же номера сейчас в очереди или на разговоре.
    # Снимок годится и устаревшим — то, что в нём есть, уже случилось: ответ оператора не
    # отменяется, а перезвон в течение минуты кончится либо разговором, либо цепочкой.
    if queue_calls is not None:
        for call in sorted(queue_calls, key=lambda item: _queue_start(item) or datetime.min):
            if str(call.get('linkedid') or '') == str(missed.get('linkedid') or ''):
                continue
            moment = _queue_start(call)
            if moment is None or moment <= started or moment > horizon:
                continue
            if _moment(call.get('answered_at')) is not None:
                return Decision(ANSWERED, 'Клиент перезвонил в %s, оператор ответил'
                                % _hms(moment), ended, deadline)
            if moment <= deadline and not held_too_long:
                # Ждёт в очереди — или уже положил трубку, но строки CDR ещё нет: тогда
                # следующий цикл увидит его касанием и сведёт в цепочку.
                return Decision(WAIT, 'клиент перезвонил и ещё в очереди', ended, deadline)
        # А вот «в очередях никого» говорит только свежий снимок. Мост его присылал, но
        # в последних циклах журнал очередей не ответил — ждём, а не решаем по одним
        # касаниям: иначе клиент, который сейчас говорит с оператором, получил бы сделку.
        queue_settled = queue_fresh_until is not None and queue_fresh_until >= settled
        if not queue_settled and not held_too_long:
            return Decision(WAIT, 'журнал очередей моста ещё не прислал минуту ожидания',
                            ended, deadline)

    fresh = fresh_until is not None and fresh_until >= settled
    if not fresh and not held_too_long:
        return Decision(WAIT, 'мост ещё не прислал данные за минуту ожидания', ended, deadline)

    return Decision(TRANSFER, 'За минуту после звонка клиенту не ответили', ended, deadline)


# ─────────────────────────────────────────────────────────────────────────────
# Тексты сделки в amoCRM
# ─────────────────────────────────────────────────────────────────────────────

def amo_phone(phone):
    """Десять цифр → 77XXXXXXXXX: так робот OLX пишет номер в сделку и контакт, и по нему
    же в amoCRM ищут клиента. Пустое — пусто."""
    digits = touches_mod.norm_phone(phone)
    return '7' + digits if digits else ''


def pretty_phone(phone):
    """7475777778 → +7 747 577 77 78 — для текста примечания."""
    digits = touches_mod.norm_phone(phone)
    if not digits:
        return ''
    return '+7 %s %s %s %s' % (digits[:3], digits[3:6], digits[6:8], digits[8:])


def lead_name(phone):
    """Название сделки: номер и что случилось. Интеграция станции называет свои сделки
    «<номер> - Входящий»; наши в списке отличаются с первого взгляда."""
    return '%s - Пропущенный входящий' % amo_phone(phone)


def note_text(missed, park='', repeat=False):
    """Примечание к сделке: когда звонил, на какую линию и сколько ждал.

    Оператору, который перезванивает, важно, какой парк клиент набирал — по нему он
    понимает, от чьего имени говорить."""
    started = _moment(missed.get('started_at'))
    when = started.strftime('%d.%m.%Y в %H:%M:%S') if started else 'время неизвестно'
    line = pretty_phone(missed.get('line_number'))
    where = ''
    if line and park:
        where = ' на линию %s (%s)' % (line, park)
    elif line:
        where = ' на линию %s' % line
    elif park:
        where = ' на линию %s' % park
    wait = missed.get('wait_seconds')
    waited = (' Ждал в очереди %d с, оператор не ответил.' % int(wait)) if wait is not None \
        else ' Оператор не ответил.'
    head = 'Повторный пропущенный входящий' if repeat else 'Пропущенный входящий'
    tail = ('' if repeat else ' За минуту после звонка клиенту не ответили — нужно перезвонить.')
    return '%s звонок %s%s.%s%s' % (head, when, where, waited, tail)
