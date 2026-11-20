"""Чаты СЗоВ по Syntony: табло СЗоВ «Чат», почасовой отчёт по чатам и выгрузка (SZOV-12).

С 20.11.2026 чаты СЗоВ считаются по Syntony, а не по Chat2Desk (Chat2Desk отключается):
те же 17 номеров с 16–19.11 подключены в Syntony, и чатники отвечают уже там. Формы
данных — те же, что были у Chat2Desk (снимок табло now / today / hourly, данные отчёта),
поэтому экраны, отбивка и выгрузка читают их одинаково; чего у Syntony нет, то пустое:
статусов операторов (занят, перерыв, тренинг) и счётчика открытых чатов в её API нет.

Откатиться на Chat2Desk — SZOV_CHAT_SOURCE=chat2desk в окружении. Без ключа истории и без
секрета вебхука Syntony источник тоже остаётся прежним: брать данные было бы неоткуда.

Правила счёта — metrics.py; чтение — queries.py. Данные приносят вебхук (routes.py) и
сторож API истории (sync.py, раз в 3 минуты); здесь только читаем.
"""

import os
import re
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import client, metrics, queries, rules, sync

SOURCE = 'syntony'
SOURCE_LABEL = 'Syntony'
LOCAL_TZ = ZoneInfo('Asia/Almaty')

ENV_SOURCE = 'SZOV_CHAT_SOURCE'
ENV_CHANNELS = 'SZOV_SYNTONY_CHANNEL_IDS'

# Номера СЗоВ в Syntony — те же, что были в Chat2Desk (совпадение по номеру канала, сверено
# 20.11.2026 по /channels Syntony и /v1/channels Chat2Desk: 17 из 17). Полных номеров здесь
# нет намеренно — репозиторий публичный; номер канала виден в Syntony по его id.
SZOV_CHANNEL_IDS = frozenset({
    '22970e91-2056-4611-9bbf-5a3f24b7abee',  # IPartner, …0094
    'ae11b9f5-cada-49b5-b7bc-c3c01ae695f0',  # Salam Taxi, …1198 (WABA)
    'b19365ae-210f-4a87-bdfd-ef0397639b75',  # iTaxi, …0880 (WABA; «Техподдержка iTaxi»)
    '631f59ac-6815-4164-955d-a27e7613fae3',  # Жана такси, …4502 (WABA)
    'b40df588-75cc-4562-be29-b1e73580fd49',  # Ноль такси, …4222
    '39114c57-557e-4549-9af2-7d169524272e',  # Ноль такси, …5487 («Ноль такси 2»)
    '9aaf8a0d-aa84-4e34-afb2-6e1af2d47ba1',  # Такси 24, …7639
    '95c5f818-021a-45fa-a003-34d455916f07',  # Таксопарк Amanat, …1111
    'cae48e5d-ad73-4727-ad5f-ccfd33833a54',  # Таксопарк Qazaq, …8988
    '5be651eb-c630-45af-b0dc-388ccd72b0cd',  # Таксопарк Tenge, …8543
    '3660595b-557f-4c42-bada-61713ec5251c',  # Таксопарк Бизнес Партнер, …2288
    'bc40028b-b5df-411c-93ed-16d9032b9d56',  # Таксопарк Достойный, …2000
    '0b25d563-3c4a-4c49-93fb-1f3e4418416b',  # Таксопарк Стабильный, …4222
    'c5e58281-1991-4081-9a10-42c0fac6dc72',  # Таксопарк Халык, …7070
    '4332d1e6-d9dd-4053-9daf-8e05f5d41153',  # Таксопарк Честный, …0402
    '4806fda0-458e-44b1-a79e-1682d921d409',  # Центр регистрации, …7584
    '6f96c35f-08b2-4f47-ad72-607d65bb1b57',  # Центр регистрации, …7777 (WABA)
})

# Данные замерли: сторож API давно не отмечал удачный прогон (он идёт раз в 3 минуты) и
# вебхук ничего не приносил. Тогда сборка снимка падает, и табло показывает прошлый снимок
# с пометкой «Syntony не отвечает» — цифры из базы, которая перестала пополняться,
# выглядели бы свежими.
STALE_SECONDS = 10 * 60
# Сторож ещё догоняет ленту: его отметка (последнее полученное минус 5 минут) отстаёт от
# момента прогона больше чем на час — сутки в базе неполные, и показывать их нельзя.
CATCHUP_SECONDS = 60 * 60
# Сутки считаются полными, если самое раннее сообщение в базе не позже такого запаса от
# их начала: сеть пишет круглые сутки, и первое сообщение ночи приходит в первые минуты.
COVERAGE_GRACE = timedelta(minutes=30)
# Сколько читать ДО начала суток. Ход водителя, начатый вечером и отвеченный после полуночи,
# принадлежит вчерашним суткам; без его начала в базе ход «начался» бы с ночного сообщения
# и попал бы в сегодня с заниженным ожиданием. Окно онлайна (15 минут) в этот запас входит.
LOOKBACK = timedelta(hours=6)


def channel_ids(environ=None):
    """Каналы СЗоВ: из SZOV_SYNTONY_CHANNEL_IDS (через запятую или пробел), иначе список выше.
    Окружение — чтобы добавить номер, не дожидаясь выкладки."""
    environ = os.environ if environ is None else environ
    raw = str(environ.get(ENV_CHANNELS) or '').strip()
    if raw:
        ids = frozenset(part for part in re.split(r'[\s,;]+', raw) if part)
        if ids:
            return ids
    return SZOV_CHANNEL_IDS


def _puller_on(environ=None):
    """Сторож API истории работает: ключ есть и выключатель не опущен (sync.start_worker)."""
    return client.is_configured(environ) and sync.is_enabled(environ)


def source_enabled(environ=None):
    """Считать ли чаты СЗоВ по Syntony. Нет — прежний Chat2Desk."""
    environ = os.environ if environ is None else environ
    if str(environ.get(ENV_SOURCE) or '').strip().lower() == 'chat2desk':
        return False
    return _puller_on(environ) or bool(rules.load_secrets(environ))


def local_day_bounds(day):
    """Сутки Алматы → (начало, конец) с поясом."""
    start = datetime.combine(day, dtime(0, 0), tzinfo=LOCAL_TZ)
    return start, start + timedelta(days=1)


def _local(moment):
    return moment.astimezone(LOCAL_TZ) if moment is not None else None


def _stamp(moment):
    """Время для экрана и выгрузки — стенными часами Алматы, как было у Chat2Desk."""
    return _local(moment).strftime('%Y-%m-%d %H:%M:%S') if moment is not None else ''


class Context:
    """Что нужно сборке, кроме базы: кто чатник СЗоВ и как его зовут в iCORE.

    resolve(имя автора в Syntony) → {'id', 'name', 'status'} или None — сопоставление по
    ФИО (казахские буквы и отчество сводятся тем же правилом, что у Chat2Desk); roster —
    все чатники СЗоВ (для «офлайн»); is_test_sql(выражение) → SQL «номер тестовый».
    """

    def __init__(self, *, resolve, roster, is_test_sql=None, channels=None,
                 window_seconds=metrics.PRESENCE_WINDOW_SECONDS,
                 online_min_seconds=metrics.ONLINE_MIN_SECONDS):
        self.resolve = resolve
        self.roster = list(roster or [])
        self.is_test_sql = is_test_sql
        self.channels = frozenset(channels) if channels is not None else channel_ids()
        self.window_seconds = window_seconds
        self.online_min_seconds = online_min_seconds
        self._names = {}

    def display_name(self, author):
        """ФИО сотрудника iCORE, а несопоставленное имя — как в Syntony: выкидывать его нельзя,
        иначе ответы есть, а человека под ними нет."""
        if author not in self._names:
            operator = self.resolve(author) if author else None
            self._names[author] = (operator.get('name') if operator and operator.get('name')
                                   else author)
        return self._names[author]


def _load(db, ctx, start, end):
    with db._get_cursor() as cursor:
        conversations, messages = queries.load_window(
            cursor, start=start, end=end, channels=ctx.channels, is_test_sql=ctx.is_test_sql)
        state = queries.load_state(cursor)
    facts, activity = metrics.analyze(conversations, messages, channels=ctx.channels)
    return facts, activity, state, messages


def _check_fresh(state, messages, now):
    """Падает, если показывать нечего или данные замерли. Без сторожа API (только вебхук)
    судить не по чему — не падаем.

    * Сторож ещё ни разу не прошёл ленту — данных за сутки нет.
    * Сторож давно не отмечал удачный прогон, а вебхук за то же время ничего не завёл —
      данные замерли (ключ отозван, Syntony лежит, поток встал).
    * Сторож догоняет ленту (после выкладки, после простоя) — сутки неполные: неполный день
      на стене читался бы как спад.
    """
    if not _puller_on():
        return
    marks = state.get('marks') or {}
    feed, conversations = marks.get(sync.MARK_FEED), marks.get(sync.MARK_CONVERSATIONS)
    if feed is None or conversations is None:
        raise RuntimeError('Syntony: первая загрузка чатов ещё не прошла')
    mark, written_at = feed
    newest = max((m.created_at for m in messages if m.created_at is not None), default=None)
    webhook_alive = newest is not None and (now - newest).total_seconds() <= STALE_SECONDS
    # Лента сообщений и снимки диалогов — два шага одного прогона: встал любой — данные
    # неполные (сообщения без снимка диалога не знают своего номера и выпадают из чатов).
    stalled = max(now - written_at, now - conversations[1]).total_seconds()
    if stalled > STALE_SECONDS and not webhook_alive:
        raise RuntimeError('Syntony не отвечает %d мин: чаты не догружаются' % int(stalled // 60))
    if (written_at - mark).total_seconds() > CATCHUP_SECONDS:
        raise RuntimeError('Syntony: чаты ещё догружаются — пока есть по %s'
                           % _local(mark).strftime('%d.%m %H:%M'))


def _unmatched(facts, ctx, start, end):
    """Кто отвечал на номерах СЗоВ в эти сутки, но не сопоставился с чатником СЗоВ в iCORE:
    человек выпал из «онлайн» — по этому списку видно, что имя в Syntony разошлось с карточкой."""
    names = set()
    for item in facts.values():
        for moment, author in item.replies:
            if author and start <= moment < end and ctx.resolve(author) is None:
                names.add(author)
    return sorted(names)


def _without_start(facts):
    """Диалоги СЗоВ без момента начала в снимке: «поступившими» и первой реакцией они не
    считаются. Если число растёт — снимок диалога приходит неполным (смотреть вебхук)."""
    return sum(1 for item in facts.values() if item.conversation.created_at is None)


def _operators_day(per_person):
    """Разрез по сотрудникам за сутки — суммами, для итогов выгрузки: чат, где человек отвечал
    в разные часы, в сутках один, а в часах — в каждом."""
    return [dict(sums, name=name) for name, sums in sorted(per_person.items())]


def _coverage_gap(state, start):
    """Нет ли в базе дыры в начале суток: сторож начинает с полуночи дня запуска, а старше
    30 дней чистит. → None (сутки полные) или «чаты есть с …» / «чатов в базе нет»."""
    earliest = state.get('earliest')
    if earliest is not None and earliest <= start + COVERAGE_GRACE:
        return None
    if earliest is None:
        return 'чатов в базе нет'
    return 'чаты есть с %s' % _local(earliest).strftime('%d.%m.%Y %H:%M')


def _covered_error(state, start):
    """«Нет данных» для выгрузки и отчёта: нули вместо этого читались бы как простой смены."""
    gap = _coverage_gap(state, start)
    return None if gap is None else 'Нет данных Syntony за эти сутки: ' + gap


def wallboard_snapshot(db, ctx, *, now=None, target_seconds=120):
    """Снимок табло «Чат» за сегодня: та же форма, что у Chat2Desk (_szov_chat_wallboard_fetch_snapshot)."""
    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    day_start, _day_end = local_day_bounds(now.date())
    # Запас до полуночи: ответы последних 15 минут прошлых суток держат человека онлайн в
    # 00:00–00:15, а вечерние ходы водителей не должны «начаться» после полуночи.
    facts, activity, state, messages = _load(db, ctx, day_start - LOOKBACK, now + timedelta(seconds=1))
    _check_fresh(state, messages, now)
    gap = _coverage_gap(state, day_start)
    if gap:
        # Сутки в базе неполные (данные начались позже полуночи) — на стене это читалось бы
        # как спад, поэтому табло говорит об этом словами.
        raise RuntimeError('Syntony: сутки неполные — ' + gap)

    online, offline, _unused = metrics.presence_now(
        activity, ctx.roster, ctx.resolve, now, ctx.window_seconds)
    end = now + timedelta(seconds=1)
    sums = metrics.window_sums(facts, day_start, end)
    total, new = metrics.chat_counts(facts, day_start, end)
    per_person = metrics.operator_sums(facts, day_start, end, ctx.display_name)

    people = []
    for item in online:
        own = per_person.get(item['name']) or {}
        people.append({
            'operator_id': item['operator_id'],
            'name': item['name'],
            'status': 'Онлайн',
            'status_key': 'online',
            'since': _stamp(item['since']),
            'seconds': item['seconds'],
            'last_reply_at': _stamp(item['last_reply_at']),
            'open_chats': None,
            'chats': int(own.get('chats') or 0),
            'first_reply_seconds': metrics.average(own, 'first'),
            'inner_reply_seconds': metrics.average(own, 'inner'),
        })
    # Сверху — кто сегодня взял больше чатов: со стены видно, кто держит основной поток.
    people.sort(key=lambda item: (-item['chats'], item['name']))
    offline_people = [{'operator_id': item['operator_id'], 'name': item['name'],
                       'since': _stamp(item['since'])} for item in offline]

    return {
        'day': now.strftime('%Y-%m-%d'),
        'source': SOURCE,
        'source_label': SOURCE_LABEL,
        # Статусов операторов у Syntony нет: экран и отбивка прячут «Занят / Тренинг / Перерыв».
        'statuses': False,
        'source_now': now.strftime('%Y-%m-%d %H:%M:%S'),
        'target_seconds': target_seconds,
        'now': {
            'operators_online': len(people),
            'operators_busy': None,
            'operators_on_training': None,
            'operators_on_break': None,
            'operators_on_holiday': None,
            'operators_other': None,
            'operators_offline': len(offline_people),
            'open_chats': None,
            'operators': people,
            'offline_operators': offline_people,
        },
        'today': {
            'chats': total,
            'chats_new': new,
            'chats_open': None,
            'first_reply_seconds': metrics.average(sums, 'first'),
            'inner_reply_seconds': metrics.average(sums, 'inner'),
            'reply_sums': {key: sums[key] for key in
                           ('first_sum', 'first_count', 'inner_sum', 'inner_count')},
            'operators_day': _operators_day(per_person),
        },
        'hourly': metrics.hourly_rows(
            facts, activity, ctx.resolve, ctx.display_name, day_start, now=now,
            online_min_seconds=ctx.online_min_seconds, window_seconds=ctx.window_seconds),
        'diagnostics': {
            'unmatched_names': _unmatched(facts, ctx, day_start, end),
            'events_truncated': False,
            'coverage_error': None,
            'conversations_without_start': _without_start(facts),
            'feed_mark_at': _stamp(((state.get('marks') or {}).get(sync.MARK_FEED) or (None, None))[1]),
            'channels': len(ctx.channels),
        },
    }


def day_block(db, ctx, day, *, now=None):
    """Прошедшие сутки для выгрузки: (часы, итоги, ошибка) в форме _szov_chat_export_finish_day.

    Ответ на чат, начатый в 23:50, приходит уже после полуночи — поэтому сообщения читаем с
    запасом в 12 часов после конца суток; считаются они всё равно в своих сутках.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    start, end = local_day_bounds(day)
    facts, activity, state, _messages = _load(db, ctx, start - LOOKBACK,
                                              min(end + timedelta(hours=12), now))
    error = _covered_error(state, start)
    if error:
        return [], {}, error
    sums = metrics.window_sums(facts, start, end)
    total, new = metrics.chat_counts(facts, start, end)
    hourly = metrics.hourly_rows(facts, activity, ctx.resolve, ctx.display_name, start,
                                 full_day=True, online_min_seconds=ctx.online_min_seconds,
                                 window_seconds=ctx.window_seconds)
    totals = {
        'chats': total,
        'chats_new': new,
        'chats_open': None,
        'first_reply_seconds': metrics.average(sums, 'first'),
        'inner_reply_seconds': metrics.average(sums, 'inner'),
        'reply_sums': {key: sums[key] for key in
                       ('first_sum', 'first_count', 'inner_sum', 'inner_count')},
        'operators_day': _operators_day(metrics.operator_sums(facts, start, end, ctx.display_name)),
        'unmatched_names': _unmatched(facts, ctx, start, end),
    }
    return hourly, totals, None


_PRESENCE_RANK = {'online': 0, 'offline': 1, 'unknown': 2}


def hourly_report(db, ctx, *, now=None):
    """Данные почасового отчёта по чатам — те же ключи, что у Chat2Desk (_chat_hourly_collect).

    Отчёт уходит в начале часа и рассказывает про ЗАКОНЧИВШИЙСЯ час; в 00:00 — про вчерашние
    сутки целиком. Сверх прежнего: офлайн числом и «поступивших» (новых) чатов — за час и за
    сутки (показатели из ТЗ SZOV-12).
    """
    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    hour_end = now.replace(minute=0, second=0, microsecond=0)
    hour_start = hour_end - timedelta(hours=1)
    day = hour_start.date()
    day_start, day_end = local_day_bounds(day)
    day_end = min(day_end, now + timedelta(seconds=1))
    facts, activity, state, messages = _load(db, ctx, day_start - LOOKBACK, now + timedelta(seconds=1))
    _check_fresh(state, messages, now)

    online, offline, _unused = metrics.presence_now(
        activity, ctx.roster, ctx.resolve, now, ctx.window_seconds)
    online_names = {item['name'] for item in online}
    roster_names = {str(item.get('name') or '') for item in ctx.roster}

    day_sums = metrics.window_sums(facts, day_start, day_end)
    hour_sums = metrics.window_sums(facts, hour_start, hour_end)
    day_total, day_new = metrics.chat_counts(facts, day_start, day_end)
    hour_total, hour_new = metrics.chat_counts(facts, hour_start, hour_end)
    by_day = metrics.operator_sums(facts, day_start, day_end, ctx.display_name)
    by_hour = metrics.operator_sums(facts, hour_start, hour_end, ctx.display_name)

    operators = []
    for name in set(by_day) | online_names:
        own_day = by_day.get(name) or {}
        own_hour = by_hour.get(name) or {}
        if name in online_names:
            status, presence = 'онлайн', 'online'
        elif name in roster_names:
            status, presence = 'офлайн', 'offline'
        else:
            # Отвечал на номерах СЗоВ, но не чатник СЗоВ (или имя не сопоставилось): о его
            # присутствии судить не по чему.
            status, presence = '—', 'unknown'
        operators.append({
            'name': name,
            'status': status,
            'presence': presence,
            'chats': int(own_day.get('chats') or 0),
            'chats_hour': int(own_hour.get('chats') or 0),
            'first_reply_day': metrics.average(own_day, 'first'),
            'first_reply_hour': metrics.average(own_hour, 'first'),
            'inner_reply_day': metrics.average(own_day, 'inner'),
            'inner_reply_hour': metrics.average(own_hour, 'inner'),
            'open_chats': None,
        })
    operators.sort(key=lambda item: (_PRESENCE_RANK.get(item['presence'], 3), -item['chats'],
                                     item['name']))

    return {
        'source': SOURCE,
        'generated_at': now.strftime('%d.%m.%Y %H:%M'),
        'day': day.strftime('%Y-%m-%d'),
        'is_previous_day': day != now.date(),
        'hour_label': f"{hour_start.strftime('%H:%M')}–{hour_end.strftime('%H:%M')}",
        'chats_day': day_total,
        'chats_hour': hour_total,
        'chats_new_day': day_new,
        'chats_new_hour': hour_new,
        'chats_open': None,
        'first_reply_day': metrics.average(day_sums, 'first'),
        'first_reply_hour': metrics.average(hour_sums, 'first'),
        'inner_reply_day': metrics.average(day_sums, 'inner'),
        'inner_reply_hour': metrics.average(hour_sums, 'inner'),
        'operators': operators,
        'online': [{'name': item['name'], 'open_chats': None} for item in online],
        'offline_count': len(offline),
        'on_status': [],
        'online_error': None,
        'coverage_error': _covered_error(state, day_start),
    }
