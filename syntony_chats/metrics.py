"""Показатели чатов СЗоВ по данным Syntony — чистые функции: ни базы, ни сети, ни Flask.

Решения владельца 20.11.2026 (задача SZOV-12, перенос с Chat2Desk):

* **Онлайн** — чатник СЗоВ, ответивший водителю в Syntony за последние 15 минут (на любом
  номере сети); **офлайн** — остальные чатники СЗоВ из iCORE. Статусов операторов («в сети»,
  перерыв, тренинг) в API Syntony нет вовсе — ни методом, ни вебхуком.
* **Первая реакция** — первый ответ СОТРУДНИКА: от первого сообщения водителя, а если чат
  передал ИИ — от момента передачи (handoff.requestedAt). Чаты, которые целиком вёл ИИ, в
  среднее не идут.
* **Всего чатов** — диалоги, где водитель писал в этом промежутке; **поступивших** — диалоги,
  начатые в нём. Диалог Syntony сам не закрывается, водитель пишет в него несколько дней,
  поэтому числа и разные.

Остальное — как было у Chat2Desk, чтобы цифры читались так же:

* **Ответ внутри чата** — среднее время ответа сотрудника на сообщение водителя, сначала
  среднее внутри диалога, потом среднее по диалогам (у Chat2Desk это колонка
  average_replies_time, «простое среднее по обращениям», решение владельца 18.08.2026).
  Ход — подряд идущие сообщения водителя; отвечен ходом считается первый исходящий после
  них. Ответил ИИ — ход ИИ, сотруднику он не засчитывается; служебные строки (напоминания,
  опросы, строки звонков) ходов не рвут и ответом не считаются.
* **Час — промежуток**, а не накопление: 12:00:00–12:59:59.
* **Люди на линии по часам — головы**: у кого в часе набралась минута «онлайн» (порог
  владельца 19.08.2026); онлайн — те же 15 минут после каждого ответа.

Время везде — datetime с поясом; границы суток и часов передаёт вызывающий (Алматы).
"""

from dataclasses import dataclass, field
from datetime import timedelta

PRESENCE_WINDOW_SECONDS = 15 * 60
ONLINE_MIN_SECONDS = 60


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    direction: str
    sender_type: str
    author_name: str
    created_at: object
    delete_scope: str = None


@dataclass(frozen=True)
class Conversation:
    id: str
    channel_id: str
    created_at: object
    handoff_requested_at: object = None
    is_test: bool = False


def is_driver(message):
    return message.direction == 'inbound' and message.sender_type in (None, '', 'contact')


def is_human_reply(message):
    """Ответ сотрудника. Не ушедшее сообщение (удалено «unsent») ответом не считается."""
    return (message.direction == 'outbound' and message.sender_type == 'human'
            and message.delete_scope != 'unsent')


def is_ai_reply(message):
    return message.direction == 'outbound' and message.sender_type == 'ai'


def _seconds(later, earlier):
    return max(0.0, (later - earlier).total_seconds())


@dataclass
class ConversationFacts:
    """Что известно о диалоге СЗоВ по его сообщениям."""
    conversation: Conversation
    driver_times: list = field(default_factory=list)
    # Ходы, отвеченные сотрудником: (начало хода, ответ, автор ответа).
    turns: list = field(default_factory=list)
    # Сообщения сотрудников: (момент, автор) — кто участвовал в диалоге и когда.
    replies: list = field(default_factory=list)
    first_reaction_seconds: float = None
    first_reaction_author: str = None


def conversation_facts(conversation, messages):
    """Факты диалога по его сообщениям (любой порядок на входе)."""
    facts = ConversationFacts(conversation)
    ordered = sorted((m for m in messages if m.created_at is not None),
                     key=lambda m: (m.created_at, m.id))
    pending = None
    first_inbound = None
    first_human = None
    for message in ordered:
        if is_driver(message):
            facts.driver_times.append(message.created_at)
            if first_inbound is None:
                first_inbound = message.created_at
            if pending is None:
                pending = message.created_at
        elif is_ai_reply(message):
            pending = None
        elif is_human_reply(message):
            facts.replies.append((message.created_at, message.author_name))
            if first_inbound is not None and first_human is None:
                first_human = message
            if pending is not None:
                facts.turns.append((pending, message.created_at, message.author_name))
                pending = None
    if first_inbound is not None and first_human is not None:
        start = first_inbound
        handoff = conversation.handoff_requested_at
        # «Если чат передал ИИ — от момента передачи»: передача между первым сообщением
        # водителя и первым ответом сотрудника. Передача ПОСЛЕ ответа о первой реакции
        # ничего не говорит (Syntony помнит только последнюю передачу).
        if handoff is not None and first_inbound <= handoff <= first_human.created_at:
            start = handoff
        facts.first_reaction_seconds = _seconds(first_human.created_at, start)
        facts.first_reaction_author = first_human.author_name
    return facts


def analyze(conversations, messages, *, channels):
    """→ (факты диалогов СЗоВ {id: ConversationFacts}, ответы сотрудников по всей сети
    {автор: [моменты]}) — второе нужно присутствию: онлайн считается по ответу на ЛЮБОМ
    номере Syntony, а чаты — только по номерам СЗоВ.

    conversations — {id: Conversation}; messages — Message любых диалогов сети.
    Тестовые номера («Реестр тестовых номеров») в чаты не идут, но ответ на них — всё
    равно ответ: человек был на линии.
    """
    by_conversation = {}
    activity = {}
    for message in messages:
        if is_human_reply(message) and message.author_name and message.created_at is not None:
            activity.setdefault(message.author_name, []).append(message.created_at)
        conversation = conversations.get(message.conversation_id)
        if conversation is None or conversation.channel_id not in channels or conversation.is_test:
            continue
        by_conversation.setdefault(message.conversation_id, []).append(message)
    facts = {cid: conversation_facts(conversations[cid], items)
             for cid, items in by_conversation.items()}
    for moments in activity.values():
        moments.sort()
    return facts, activity


# ── Окна: чаты, первая реакция, ответ внутри чата ─────────────────────────────────────────────

def _within(moment, start, end):
    return moment is not None and start <= moment < end


def chat_counts(facts, start, end):
    """(всего, поступивших) за [start, end): всего — диалоги с сообщением водителя в окне,
    поступивших — начатые в окне диалоги, где водитель писал."""
    total = new = 0
    for item in facts.values():
        if any(start <= moment < end for moment in item.driver_times):
            total += 1
        if item.driver_times and _within(item.conversation.created_at, start, end):
            new += 1
    return total, new


def _empty_sums():
    return {'chats': 0, 'first_sum': 0.0, 'first_count': 0, 'inner_sum': 0.0, 'inner_count': 0}


def window_sums(facts, start, end):
    """Суммы окна — те же ключи, что у Chat2Desk (`reply_sums`): из сумм складываются час,
    сутки и период, а «среднее средних» дало бы третью, ни с чем не сходящуюся цифру.

    first_* — по диалогам, НАЧАТЫМ в окне («время до первого ответа на поступивший чат»);
    inner_* — по диалогам, где в окне начался отвеченный сотрудником ход: среднее хода
    внутри диалога, потом сумма по диалогам (единица — «диалог в этом окне»).
    """
    sums = _empty_sums()
    for item in facts.values():
        if any(start <= moment < end for moment in item.driver_times):
            sums['chats'] += 1
        if (item.first_reaction_seconds is not None
                and _within(item.conversation.created_at, start, end)):
            sums['first_sum'] += item.first_reaction_seconds
            sums['first_count'] += 1
        waits = [_seconds(reply_at, begun) for begun, reply_at, _author in item.turns
                 if start <= begun < end]
        if waits:
            sums['inner_sum'] += sum(waits) / len(waits)
            sums['inner_count'] += 1
    return sums


def operator_sums(facts, start, end, key_of):
    """Разрез по сотрудникам: {ключ: суммы}. key_of(имя автора) → ключ или None (не считать).

    chats — диалоги, где сотрудник отвечал в окне; first_* — первая реакция в начатых в окне
    диалогах, если первым ответил он; inner_* — его ответы на ходы, начатые в окне.
    """
    result = {}

    def bucket(key):
        if key not in result:
            result[key] = _empty_sums()
        return result[key]

    for item in facts.values():
        engaged = set()
        for moment, author in item.replies:
            key = key_of(author) if author else None
            if key is not None and start <= moment < end:
                engaged.add(key)
        for key in engaged:
            bucket(key)['chats'] += 1
        if (item.first_reaction_seconds is not None and item.first_reaction_author
                and _within(item.conversation.created_at, start, end)):
            key = key_of(item.first_reaction_author)
            if key is not None:
                bucket(key)['first_sum'] += item.first_reaction_seconds
                bucket(key)['first_count'] += 1
        waits = {}
        for begun, reply_at, author in item.turns:
            key = key_of(author) if author else None
            if key is not None and start <= begun < end:
                waits.setdefault(key, []).append(_seconds(reply_at, begun))
        for key, values in waits.items():
            bucket(key)['inner_sum'] += sum(values) / len(values)
            bucket(key)['inner_count'] += 1
    return result


def average(sums, kind):
    """Среднее из сумм. None — «мерить было не по чему», а не ноль."""
    count = int((sums or {}).get('%s_count' % kind) or 0)
    return (float((sums or {}).get('%s_sum' % kind) or 0.0) / count) if count else None


# ── Присутствие: онлайн по ответам ────────────────────────────────────────────────────────────

def online_intervals(moments, window_seconds=PRESENCE_WINDOW_SECONDS, until=None):
    """Отрезки «онлайн» по моментам ответов: каждый ответ держит человека на линии
    window_seconds; пересекающиеся отрезки сливаются. until — обрезать правый край («сейчас»)."""
    window = timedelta(seconds=window_seconds)
    intervals = []
    for moment in sorted(moments):
        end = moment + window
        if until is not None:
            if moment > until:
                break
            end = min(end, until)
        if intervals and moment <= intervals[-1][1]:
            intervals[-1][1] = max(intervals[-1][1], end)
        else:
            intervals.append([moment, end])
    return [(begin, end) for begin, end in intervals if end > begin]


def _stretch_start(moments, window_seconds):
    """Начало непрерывного «онлайн», в котором лежит последний ответ: идём назад, пока
    соседние ответы ближе окна друг к другу. moments — по возрастанию, непустой."""
    begin = moments[-1]
    for moment in reversed(moments[:-1]):
        if (begin - moment).total_seconds() > window_seconds:
            break
        begin = moment
    return begin


def presence_now(activity, roster, resolve, now, window_seconds=PRESENCE_WINDOW_SECONDS):
    """Кто из чатников СЗоВ сейчас онлайн и кто нет.

    activity — {имя автора в Syntony: [моменты ответов]}; resolve(имя) → сотрудник
    {'id', 'name', 'status'} или None (не чатник СЗоВ); roster — чатники СЗоВ из iCORE.
    → (онлайн [{operator_id, name, since, seconds}], офлайн [{operator_id, name, since}],
       не сопоставленные имена авторов).

    Человек с двумя учётками Syntony — одна строка: онлайн, если онлайн хоть одна. Уволенных
    в «офлайн» нет: «не на линии» они уже не стоят.
    """
    by_operator = {}
    unmatched = set()
    for author, moments in (activity or {}).items():
        operator = resolve(author)
        if operator is None:
            unmatched.add(author)
            continue
        by_operator.setdefault(int(operator['id']), (operator, []))[1].extend(moments)

    online, last_seen = [], {}
    for operator_id, (operator, moments) in by_operator.items():
        moments = sorted(moment for moment in moments if moment <= now)
        if not moments:
            continue
        last = moments[-1]
        last_seen[operator_id] = last
        if (now - last).total_seconds() >= window_seconds:
            continue
        begin = _stretch_start(moments, window_seconds)
        online.append({
            'operator_id': operator_id,
            'name': operator.get('name') or '',
            'since': begin,
            'seconds': int(_seconds(now, begin)),
            'last_reply_at': last,
        })
    online_ids = {item['operator_id'] for item in online}
    offline = []
    seen = set()
    for operator in roster or []:
        operator_id = int(operator['id'])
        if operator_id in online_ids or operator_id in seen:
            continue
        seen.add(operator_id)
        if str(operator.get('status') or '') in ('fired', 'dismissal'):
            continue
        # «Не в системе с» — момент, когда человек перестал быть онлайн по тому же правилу:
        # последний ответ плюс окно. Иначе список и график по часам говорили бы разное.
        last_reply = last_seen.get(operator_id)
        offline.append({'operator_id': operator_id, 'name': operator.get('name') or '',
                        'since': (last_reply + timedelta(seconds=window_seconds)
                                  if last_reply else None)})
    online.sort(key=lambda item: item['name'])
    # Свежие уходы сверху: со стены важнее, кто ушёл только что, чем кто не заходит неделю;
    # кого сегодня не было вовсе — в конце, по ФИО.
    offline.sort(key=lambda item: item['name'])
    offline.sort(key=lambda item: item['since'].timestamp() if item['since'] else float('-inf'),
                 reverse=True)
    return online, offline, sorted(unmatched)


def online_seconds_by_hour(activity, resolve, day_start, hours, now=None,
                           window_seconds=PRESENCE_WINDOW_SECONDS):
    """{час: {ФИО: секунды онлайн}} по чатникам СЗоВ за часы [0, hours) суток day_start.

    Человек с двумя учётками — одно ФИО, отрезки сливаются, а не складываются.
    """
    moments_by_name = {}
    for author, moments in (activity or {}).items():
        operator = resolve(author)
        if operator is None:
            continue
        name = operator.get('name') or author
        moments_by_name.setdefault(name, []).extend(moments)
    per_hour = {}
    for name, moments in moments_by_name.items():
        for begin, end in online_intervals(moments, window_seconds, until=now):
            for hour in range(hours):
                hour_start = day_start + timedelta(hours=hour)
                hour_end = hour_start + timedelta(hours=1)
                overlap = (min(end, hour_end) - max(begin, hour_start)).total_seconds()
                if overlap > 0:
                    people = per_hour.setdefault(hour, {})
                    people[name] = people.get(name, 0) + int(overlap)
    return per_hour


def hourly_rows(facts, activity, resolve, display_name, day_start, *, now=None, full_day=False,
                online_min_seconds=ONLINE_MIN_SECONDS, window_seconds=PRESENCE_WINDOW_SECONDS,
                online=None):
    """Строки графика «По часам» — та же форма, что у Chat2Desk (_szov_chat_wallboard_hourly):
    hour, chats, first/inner_reply_seconds, operators_online (головы), operators (ФИО с
    секундами), operators_reply (суммы по отвечавшим), operators_under_minute,
    online_seconds, partial. Плюс chats_new — поступивших за час.

    full_day — сутки закрыты: 24 часа целиком; иначе прошедшие часы и текущий, неполный.
    display_name(имя автора) → ФИО для разреза по отвечавшим.
    online — готовые {час: {ФИО: секунды на линии}} (по статусам Syntony, statuses.py); без
    них — онлайн по ответам за окно window_seconds, как до статусов.
    """
    if full_day:
        last_hour, until = 23, None
    else:
        elapsed = max(0.0, (now - day_start).total_seconds())
        last_hour, until = min(23, int(elapsed // 3600)), now
    if online is None:
        online = online_seconds_by_hour(activity, resolve, day_start, last_hour + 1, until,
                                        window_seconds)
    rows = []
    for hour in range(last_hour + 1):
        start = day_start + timedelta(hours=hour)
        end = start + timedelta(hours=1)
        sums = window_sums(facts, start, end)
        _total, new = chat_counts(facts, start, end)
        people = online.get(hour) or {}
        counted = sorted(((name, seconds) for name, seconds in people.items()
                          if seconds >= online_min_seconds),
                         key=lambda item: (-item[1], item[0]))
        replies = sorted(
            (dict(item, name=key) for key, item in operator_sums(
                facts, start, end, display_name).items()),
            key=lambda item: (-int(item.get('chats') or 0), item['name']))
        rows.append({
            'hour': hour,
            'chats': sums['chats'],
            'chats_new': new,
            'first_reply_seconds': average(sums, 'first'),
            'inner_reply_seconds': average(sums, 'inner'),
            'operators_online': len(counted),
            'operators': [{'name': name, 'seconds': seconds} for name, seconds in counted],
            'operators_reply': replies,
            'operators_under_minute': sum(1 for seconds in people.values()
                                          if seconds < online_min_seconds),
            'online_seconds': int(sum(people.values())),
            'partial': (not full_day) and hour == last_hour,
        })
    return rows
