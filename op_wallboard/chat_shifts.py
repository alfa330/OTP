# -*- coding: utf-8 -*-
"""«Отчёт по сменам» · «Чат» (SALES-1, задача #424): завершённые смены верификаторов за месяц.

Второе направление окна «Отчёт по сменам» «Табло ОП» — по аналогии с отчётом линии
(activity.py, #392): строка — одна смена одного верификатора, 14 колонок в порядке ТЗ, по строке —
журнал смены. Источники те же, что у «Табло ОП · Чат», второго расчёта ни у чего нет:

  * смена и статусы — кнопка смены в «Чатах ОП» (wazzup/shift.py): «Начать смену» ставит
    «Активный», дальше «Перерыв», «Тренинг», «Тех. пауза»; «Закончить смену» — выход. События
    раздела лежат в `operator_status_events` с меткой `wzws-` в client_event_id. События iCORE Phone
    тех же людей (двое верификаторов работают и на линии) — работа на линии, в смену чата они не
    идут; нужен только их выход (см. «Смена»);
  * чаты — переписка аккаунта «op» в `wazzup_messages`: первый ответ — по правилам раздела «Чаты ОП»
    (решение владельца 17.11.2026), что такое ответ, рассылка и бот — по правилам табло (chat.py).

Модуль чистый: ни базы, ни Flask. Чтение — `chat_shifts_routes.py`, проверка —
tests/test_op_chat_shifts.py.

Смена
-----
Смена — статусы раздела подряд, от «Начать смену» до выхода:

  * выход — «Закончить смену» или сторож раздела: портал молчал 10 минут («Портал закрыт», выход
    стоит моментом последней отметки) или статус не менялся 16 часов;
  * после «Портал закрыт» вернулся раньше чем через 4 часа — та же смена (компьютер уснул, вкладку
    закрыли), промежуток идёт отрезком «Вне системы»; после «Закончить смену» — только если
    вернулся в 15 минут (нажал по ошибке). Замер 08.10–17.11.2026: из 255 «Закончить смену» 235 —
    конец дня (возврат через 4 часа и позже), 13 — возврат через 0–14 минут, а 7 — вторая смена
    того же дня (через 48 минут — 4 часа: день и ночь, утро и вечер);
  * смену оставили открытой и ушли: статус висит без единого сообщения и нажатия, и смена тянулась
    через ночь до утра (замер: 16 из 288 смен длиннее 14 часов). Признак человека — его сообщение
    клиенту или нажатие статуса. Тишина рвёт смену: без возврата (дальше только закрытие портала
    сторожем, и в 4 часа после него человек не вернулся) — от 4 часов; в перерыве, тренинге и тех.
    паузе — от 4; в «Активном», после которого человек вернулся к работе сам (в том числе после
    закрытия портала), — от 8: ночью верификатор часами ждёт чатов, и это время ожидания, которое
    отчёт обязан показать, а не спрятать (замер: ночные тишины 4,8–5,7 часа, в одной из них пришло
    76 сообщений клиентов без ответа). Смена кончается на последнем признаке, выход
    «не зафиксирован», а следующая начинается со следующего признака (если это сообщение, а не
    нажатие, вход тоже «не зафиксирован»). Если в этой тишине вышел iCORE Phone —
    смена кончается на его выходе: «Закончить смену» сразу после выхода телефона база отбрасывает
    как повтор того же статуса (`append_operator_status_event`), и выхода раздела нет;
  * выход iCORE Phone посреди смены раздел видит как выход и снова просит «Начать смену»: повтор
    того же статуса с выходом телефона между ними — это выход в момент выхода телефона и новый
    вход в момент нажатия (`section_runs`);
  * разрез по выходу iCORE Phone — только на повторе ТОГО ЖЕ статуса: выход телефона в перерыве и
    следующее «Начать смену» — это смена статуса, и перерыв тянется до нажатия (на проде так
    4 перерыва за полтора месяца у двух человек, работающих и на линии);
  * смена принадлежит дню своего начала: ночная 21:00–09:00 целиком лежит в вечере; в отчёте
    только завершённые смены, как у линии, — те, которые уже ничто не продлит (`build_shifts`):
    после «Закончить смену» прошло 15 минут, после закрытия портала — 4 часа, тишина в «Активном»
    дольше 8 часов. Смена короче минуты без единого сообщения (нажал «Начать» и сразу «Закончить»)
    — не строка отчёта.

Обработка чата
--------------
Wazzup не говорит, когда оператор открыл чат и когда закрыл, — есть только сообщения. Переписку
чата режут на куски паузы дольше 15 минут — то же правило, что «в работе» у табло
(`chat.IN_WORK_SECONDS`; замер 01–16.11.2026: длиннее 15 минут 4,6 % пауз внутри диалога).
Обработка чата оператором в куске — от сообщения клиента, на которое он ответил (если до него
писал коллега — от сообщения коллеги), но не раньше чем за 5 минут до ответа (`LEAD_MAX`: дольше —
это клиент ждал в очереди, а не оператор читал и писал), до его последнего сообщения в куске.
Считается только время в «Активном»: писать клиентам можно только в нём (владелец 19.10.2026).
Порядок сообщений — по точному времени: ответы двух сотрудников в одну секунду не меняются местами.

Колонки
-------
  4  вход — первое нажатие статуса смены;
  5  среднее время первого ответа — как «Ср. ответ» «Аналитики» раздела «Чаты ОП»
     (`first_reply_days`): у чата за день одно значение — от первого сообщения клиента за день до
     первого исходящего после него, засчитано ответившему первым; ответ — к смене, в которую дан.
     Сумма задержек делится на число чатов с его первым ответом. У дневной смены цифра совпадает с
     «Чатами ОП» за этот день;
  6  общее время обработки чатов — обработка каждого чата, параллельные складываются;
  7  активное время работы — то же время одной лентой на все чаты: секунда, когда в обработке
     было три чата, считается один раз;
  8  время ожидания — в системе без обработки чатов: «Активный», когда он не вёл ни одного чата,
     и «Тренинг». По формуле ТЗ из факта вычитаются только перерывы, и тренинг остаётся в нём;
     7 + 8 + 10 = 14;
  9  перерывы; 10 тех. перерывы — статус «Тех. пауза»;
  11 взятые в работу — разные чаты, где оператор действовал в эту смену: писал клиенту или нажал
     «Ответ не нужен» (журнал нажатий — с 17.11.2026); повторное открытие того же чата — не новый чат;
  12 обработанные — из них те, где разговор закончил он: его последнее действие в смене (сообщение
     или «Ответ не нужен») — последнее действие сотрудников в своём куске переписки (решение
     владельца 17.11.2026). Каждый кусок так закрывает ровно один человек — как табло числит чат
     «у того, кто писал последним» (chat.now_block). Кусок, а не
     6-часовой диалог: клиент, вернувшийся через несколько часов с новым вопросом, — новый
     разговор, и цифра прошедшей смены от него задним числом не меняется. Верификаторы работают
     общим потоком: замер 08.10–17.11.2026 — в 30 % диалогов пишут двое и больше, и чаще всего «не
     закончил» значит «клиент ответил, а продолжил коллега». Реплику клиента, после которой ответа
     не было, правило само не судит: её закрывает нажатие «Ответ не нужен»;
  13 выход — последний выход смены;
  14 фактически отработанное — выход − вход − перерывы. Время «Вне системы» внутри смены (портал
     закрыт) в факт не идёт: ТЗ собирает факт из активной работы, ожидания и тех. перерывов, а
     время вне системы — ни то, ни другое.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import timedelta

from wazzup import shift as shift_mod
from . import activity as activity_mod
from . import chat as chat_mod
from . import chat_export as chat_export_mod

GROUP_KEY = 'verifier'
GROUP_LABEL = 'Верификаторы'
GROUP_LABELS = {GROUP_KEY: GROUP_LABEL}
GROUP_ORDER = (GROUP_KEY,)

# Переписка хранится 45 суток (`cleanup_wazzup_messages`) — граница та же, что у выгрузки табло
# чата; статусы — дольше. День, от которого переписки уже нет, показал бы смену с нулями вместо
# чатов — это выдуманные цифры.
RETENTION_DAYS = chat_export_mod.RETENTION_DAYS

RETURN_AFTER_AUTO = timedelta(hours=4)
RETURN_AFTER_MANUAL = timedelta(minutes=15)
# Тишина без признаков человека (докстринг модуля, «Смена»): после неё никто не вернулся — от
# 4 часов; вернулся, но всё это время стоял в перерыве, тренинге или тех. паузе — тоже от 4;
# вернулся из «Активного» — от 8: ночью верификатор ждёт чатов часами, и это ожидание, а не уход
# (то же правило «тихой ночи», что у линии, activity.QUIET_WORK_GAP).
SILENCE = timedelta(hours=4)
QUIET_ACTIVE = timedelta(hours=8)
WORK_PAUSE_SECONDS = chat_mod.IN_WORK_SECONDS
# Подводка к ответу — не больше 5 минут: столько оператор читает и пишет, а дольше — это клиент ждал
# в очереди, пока оператор вёл другие чаты или не работал (замер: ответ дольше 5 минут — 5,7 %).
LEAD_MAX = timedelta(minutes=5)
# Сообщение относится к смене с запасом: Wazzup ставит время, когда очередь его отправила.
MESSAGE_LEAD = timedelta(minutes=1)
MESSAGE_TAIL = timedelta(minutes=5)
# Нажал «Начать смену» и сразу «Закончить» (на проде — смены по 1, 5 и 77 секунд): без единого
# сообщения такая смена — шум, а не строка отчёта.
MIN_SHIFT = timedelta(minutes=1)

KIND_ACTIVE = 'active'      # статус «Активный»; в журнале делится на обработку и ожидание
KIND_WORK = 'work'
KIND_WAIT = 'wait'
KIND_BREAK = 'break'
KIND_TRAINING = 'training'
KIND_TECH = 'tech'
KIND_OFFLINE = 'offline'
KIND_LOGOUT = 'logout'

# Ключи статусов — из меню смены раздела, своего списка здесь нет.
_TONE_KINDS = {'work': KIND_ACTIVE, 'break': KIND_BREAK, 'training': KIND_TRAINING, 'tech': KIND_TECH}
STATUS_KINDS = {item['key']: _TONE_KINDS[item['tone']] for item in shift_mod.STATUSES}

CLOSE_STATUS = 'status'     # следующий статус смены
CLOSE_MANUAL = 'manual'     # «Закончить смену»
CLOSE_AUTO = 'auto'         # сторож: портал закрыт
CLOSE_LONG = 'long'         # сторож: статус не менялся 16 часов
CLOSE_PHONE = 'phone'       # выход iCORE Phone, съевший «Закончить смену»
CLOSE_CUT = 'cut'           # 4 часа без признаков человека — выход не зафиксирован
CLOSE_OPEN = 'open'         # идёт до сих пор
_LOGOUT_CLOSES = frozenset({CLOSE_MANUAL, CLOSE_AUTO, CLOSE_LONG, CLOSE_PHONE})

OPENED_STATUS = 'status'    # начат нажатием статуса
OPENED_RESUME = 'resume'    # после разрыва по тишине: первым признаком было сообщение

JOURNAL_LABELS = {
    KIND_WORK: 'Обработка чатов',
    KIND_WAIT: 'Ожидание',
    KIND_BREAK: 'Перерыв',
    KIND_TRAINING: 'Тренинг',
    KIND_TECH: 'Технический перерыв',
    KIND_OFFLINE: 'Вне системы',
    KIND_LOGOUT: 'Выход',
}
_OFFLINE_NOTES = {
    CLOSE_AUTO: 'портал закрыт',
    CLOSE_LONG: 'статус не менялся 16 часов',
    CLOSE_MANUAL: 'смену закончили и начали снова',
    CLOSE_PHONE: 'смену закончили и начали снова',
}
_LOGOUT_NOTES = {
    CLOSE_MANUAL: 'закончил смену',
    CLOSE_AUTO: 'портал закрыт — смену закрыла система',
    CLOSE_LONG: 'статус не менялся 16 часов — смену закрыла система',
    CLOSE_PHONE: 'выход из iCORE Phone',
}

stamp = activity_mod.stamp
parse_moment = activity_mod.parse_moment


def normalize_key(value):
    return shift_mod.normalize_key(value)


def _second(moment):
    """Момент без долей секунды: все отрезки отчёта — в целых секундах (см. build_report)."""
    return moment.replace(microsecond=0) if moment is not None else None


# ── Смена ────────────────────────────────────────────────────────────────────────────────────


def section_runs(events, now, phone_offs=()):
    """Отрезки статусов смены подряд: [{kind, start, end, close, presses}] по времени.

    events — [(время, ключ, пометка)] событий раздела по возрастанию; phone_offs — отсортированные
    выходы iCORE Phone. Выход без идущей смены и незнакомый ключ пропускаются: угадывать, работа
    это или пауза, хуже, чем не заметить. Последний статус без продолжения идёт до `now`
    (close='open').

    Повтор того же статуса — не переход, но нажатие (presses — признак человека). База пишет повтор,
    только если статус между ними сменил другой источник; если это был выход iCORE Phone, раздел
    показывал «Не на смене», и повтор — это «Начать смену»: отрезок кончается выходом телефона, а
    новый начинается нажатием (замер: у верификатора, который утром на линии, выход телефона в 08:03
    закрыл и смену чата, а «Начать смену» в 21:01 пришло тем же «Активным»)."""
    runs = []
    current = None   # {kind, start, last, presses}
    now = _second(now)
    offs = list(phone_offs or ())

    def close_current(end, close):
        runs.append({'kind': current['kind'], 'start': current['start'], 'end': end, 'close': close,
                     'presses': current['presses']})

    for at, raw_key, note in events or ():
        at = _second(parse_moment(at))
        if at is None:
            continue
        key = normalize_key(raw_key)
        kind = STATUS_KINDS.get(key)
        if kind is not None:
            if current is not None and current['kind'] == kind:
                index = bisect_right(offs, current['last'])
                if index < len(offs) and offs[index] < at:
                    close_current(offs[index], CLOSE_PHONE)
                    current = {'kind': kind, 'start': at, 'last': at, 'presses': []}
                else:
                    current['presses'].append(at)
                    current['last'] = at
                continue
            if current is not None:
                close_current(at, CLOSE_STATUS)
            current = {'kind': kind, 'start': at, 'last': at, 'presses': []}
            continue
        if key != shift_mod.LOGOUT_KEY or current is None:
            continue
        note = str(note or '').strip()
        close_current(at, CLOSE_AUTO if note == shift_mod.AUTO_LOGOUT_NOTE
                      else CLOSE_LONG if note == shift_mod.LONG_SHIFT_NOTE else CLOSE_MANUAL)
        current = None
    if current is not None:
        close_current(max(now, current['start']), CLOSE_OPEN)
    return runs


def _cut_piece(kind, start, last_mark, opened, phone_offs, silence):
    """Кусок, оборванный тишиной: кончается на последнем признаке человека, а если в тишине вышел
    iCORE Phone — на этом выходе (докстринг модуля, «Смена»)."""
    index = bisect_right(phone_offs, last_mark)
    if index < len(phone_offs) and phone_offs[index] - last_mark <= silence:
        return {'kind': kind, 'start': start, 'end': phone_offs[index], 'close': CLOSE_PHONE,
                'opened': opened}
    return {'kind': kind, 'start': start, 'end': last_mark, 'close': CLOSE_CUT, 'opened': opened}


def _return_window(close):
    """Сколько ждать возврата после выхода, чтобы это была та же смена (докстринг, «Смена»)."""
    if close in (CLOSE_MANUAL, CLOSE_PHONE):
        return RETURN_AFTER_MANUAL
    if close in (CLOSE_AUTO, CLOSE_LONG):
        return RETURN_AFTER_AUTO
    return timedelta(0)


def _returned(run, following, now):
    """Вернулся ли человек после конца отрезка: True, False или None — ещё не ясно.

    Конец-нажатие (следующий статус, «Закончить смену») — сам человек. После выхода, который
    поставили сторож или телефон, возврат — следующее нажатие раньше, чем кончится окно возврата;
    у последнего отрезка, пока окно не прошло, это ещё не ясно, как и у идущего."""
    close = run['close']
    if close in (CLOSE_STATUS, CLOSE_MANUAL):
        return True
    if close == CLOSE_OPEN:
        return None
    window = _return_window(close)
    if following is not None:
        return following['start'] - run['end'] < window
    return None if now - run['end'] < window else False


def cut_unattended(runs, own_messages=(), phone_offs=(), silence=SILENCE, quiet_active=QUIET_ACTIVE,
                   now=None):
    """Отрезки без времени, когда человека не было: [{kind, start, end, close, opened}].

    own_messages, phone_offs — отсортированные моменты его сообщений клиентам и выходов iCORE
    Phone. Признак человека — начало отрезка (нажатие статуса), повторное нажатие, его сообщение и
    конец отрезка, если это нажатие (следующий статус или «Закончить смену»). Конец, который
    поставил сторож, и «сейчас» у идущего отрезка признаком не являются: открытый портал — ещё не
    человек на месте. Пороги — SILENCE и QUIET_ACTIVE (см. их комментарий): тишина, после которой
    человек вернулся (или ещё может вернуться: смена идёт, окно возврата не прошло), судится как
    тишина с возвратом — в «Активном» от 8 часов."""
    own_messages = list(own_messages or ())
    phone_offs = list(phone_offs or ())
    runs = list(runs or ())
    pieces = []
    for position, run in enumerate(runs):
        start, end, close, kind = run['start'], run['end'], run['close'], run['kind']
        inner = quiet_active if kind == KIND_ACTIVE else silence
        following_run = runs[position + 1] if position + 1 < len(runs) else None
        returned = _returned(run, following_run, now if now is not None else end)
        # Хвост до конца, который поставил не человек: вернулся (или может вернуться) — как тишина
        # внутри смены, не вернулся — 4 часа.
        tail = inner if returned is not False else silence
        lo = bisect_right(own_messages, start)
        hi = bisect_left(own_messages, end)
        # (момент, нажатие ли): после разрыва смена продолжается со следующего признака, и вход
        # «зафиксирован», только если это нажатие, а не сообщение.
        marks = sorted([(start, True)] + [(at, True) for at in run.get('presses') or ()]
                       + [(at, False) for at in own_messages[lo:hi]])
        end_is_mark = close in (CLOSE_STATUS, CLOSE_MANUAL)
        piece_start, opened = start, OPENED_STATUS
        closed = False
        for index, (at, _press) in enumerate(marks):
            if index + 1 < len(marks):
                following, following_press = marks[index + 1]
            elif end_is_mark:
                following, following_press = end, True
            else:
                break
            if following - at < inner:
                continue
            pieces.append(_cut_piece(kind, piece_start, at, opened, phone_offs, silence))
            if index + 1 >= len(marks):
                # Тишину прервало само нажатие (статус или «Закончить смену»): следующий отрезок
                # начнётся с него как обычно, а выход после долгой тишины — уже не выход смены.
                closed = True
                break
            piece_start = following
            opened = OPENED_STATUS if following_press else OPENED_RESUME
        if closed:
            continue
        last_mark = marks[-1][0]
        if not end_is_mark and end - last_mark >= tail:
            pieces.append(_cut_piece(kind, piece_start, last_mark, opened, phone_offs, silence))
            continue
        pieces.append({'kind': kind, 'start': piece_start, 'end': end, 'close': close, 'opened': opened})
    return pieces


def _joins(last, piece):
    """Продолжает ли кусок смену, в которой `last` — последний (докстринг модуля, «Смена»)."""
    if piece['opened'] != OPENED_STATUS:
        return False
    gap = piece['start'] - last['end']
    if last['close'] == CLOSE_STATUS:
        return gap <= timedelta(0)
    if last['close'] in (CLOSE_MANUAL, CLOSE_PHONE):
        return gap < RETURN_AFTER_MANUAL
    if last['close'] in (CLOSE_AUTO, CLOSE_LONG):
        return gap < RETURN_AFTER_AUTO
    return False


def build_shifts(events, now, *, own_messages=(), phone_offs=(), silence=SILENCE,
                 quiet_active=QUIET_ACTIVE):
    """Смены одного человека: [{login, logout, close, opened, segments, finished}] по времени.

    segments — [(вид, начало, конец, пометка)]: статусы и «Вне системы» между выходом и возвратом;
    «Активный» здесь ещё целиком — на обработку и ожидание его делит `build_report`. Смена без
    единой секунды (нажали «Начать» и «Закончить» разом) — не смена.

    finished — смену уже ничто не продлит: за ней началась другая, её оборвала тишина или после
    выхода прошло окно возврата (15 минут после «Закончить смену», 4 часа после закрытия портала).
    Иначе отчёт показал бы строку, которая через минуту склеится с продолжением и пропадёт."""
    now = _second(now)
    phone_offs = sorted(phone_offs or ())
    pieces = cut_unattended(section_runs(events, now, phone_offs), own_messages, phone_offs,
                            silence, quiet_active, now=now)
    grouped = []
    for piece in pieces:
        if grouped and _joins(grouped[-1][-1], piece):
            grouped[-1].append(piece)
        else:
            grouped.append([piece])
    shifts = []
    for index, group in enumerate(grouped):
        close = group[-1]['close']
        if close == CLOSE_OPEN:
            finished = False
        elif index + 1 < len(grouped) or close == CLOSE_CUT:
            finished = True
        else:
            finished = now - group[-1]['end'] >= _return_window(close)
        segments = []
        for previous, piece in zip([None] + group[:-1], group):
            if previous is not None and piece['start'] > previous['end']:
                segments.append((KIND_OFFLINE, previous['end'], piece['start'],
                                 _OFFLINE_NOTES.get(previous['close'])))
            if piece['end'] > piece['start']:
                segments.append((piece['kind'], piece['start'], piece['end'], None))
        login, logout = group[0]['start'], group[-1]['end']
        if logout <= login:
            continue
        shifts.append({
            'login': login,
            'logout': logout,
            'close': close,
            'opened': group[0]['opened'],
            'segments': segments,
            'finished': finished,
        })
    return shifts


# ── Обработка чатов ──────────────────────────────────────────────────────────────────────────


def _ordered_by_chat(messages):
    """{(канал, чат): [сообщение]} в порядке времени — тем же порядком, что у chat.split_episodes:
    в одну и ту же миллисекунду реплика клиента идёт раньше ответа, дальше — по message_id.

    Порядок строится по ТОЧНОМУ времени (до округления до секунд): два ответа в одну секунду иначе
    поменялись бы местами, и разговор «закончил» бы не тот. Место в чате — `_pos` сообщения."""
    by_chat = defaultdict(list)
    for index, message in enumerate(messages or ()):
        by_chat[(message['channel_id'], message['chat_id'])].append(
            (message['at'], 0 if message['kind'] == chat_mod.KIND_CLIENT else 1,
             message.get('message_id') or '', index, message))
    out = {}
    for chat, items in by_chat.items():
        items.sort(key=lambda item: item[:4])
        ordered = [item[4] for item in items]
        for position, message in enumerate(ordered):
            message['_pos'] = position
        out[chat] = ordered
    return out


def _lead_start(items, position, lead_max):
    """Начало обработки перед первым сообщением верификатора в куске: первая из подряд идущих
    реплик клиента, на которые он отвечает (или реплика коллеги, после которой он подключился), но не
    раньше чем за `lead_max` до ответа. Ответ после долгого ожидания получает ту же подводку, что и
    быстрый, — без скачка на границе куска."""
    message = items[position]
    if position == 0:
        return message['at']
    previous = position - 1
    if items[previous]['kind'] == chat_mod.KIND_CLIENT:
        while previous > 0 and items[previous - 1]['kind'] == chat_mod.KIND_CLIENT:
            previous -= 1
    return max(items[previous]['at'], message['at'] - lead_max)


def _stretch(items, lo, hi, chat, out, closers, lead_max):
    """Кусок переписки items[lo:hi] без пауз дольше порога: каждому верификатору одна лента — от
    подводки к его первому сообщению до последнего; последнее сообщение сотрудника в куске —
    «закрывающее» (`closers`): разговор закончил его автор."""
    spans = {}
    last_staff = None
    for position in range(lo, hi):
        message = items[position]
        if message['kind'] == chat_mod.KIND_CLIENT:
            continue
        last_staff = message
        if message['kind'] != chat_mod.KIND_VERIFIER or message['user_id'] is None:
            continue
        user_id = message['user_id']
        if user_id in spans:
            spans[user_id][1] = message['at']
            continue
        spans[user_id] = [_lead_start(items, position, lead_max), message['at']]
    if last_staff is not None:
        closers.add(id(last_staff))
    for user_id, (start, end) in spans.items():
        out[user_id].append((start, end, chat))


def processing_intervals(ordered, pause_seconds=WORK_PAUSE_SECONDS, lead_max=LEAD_MAX):
    """({user_id: [(начало, конец, чат)]} по возрастанию начала, closers) — обработка чатов
    верификаторами и закрывающие сообщения кусков (id объектов сообщений).

    ordered — `_ordered_by_chat` от строк chat.classify. Пауза дольше `pause_seconds` между соседними
    сообщениями чата (любыми) режет переписку на куски (докстринг модуля, «Обработка чата»)."""
    pause = timedelta(seconds=pause_seconds)
    out = defaultdict(list)
    closers = set()
    for chat, items in ordered.items():
        start = 0
        for index in range(1, len(items) + 1):
            if index == len(items) or items[index]['at'] - items[index - 1]['at'] > pause:
                _stretch(items, start, index, chat, out, closers, lead_max)
                start = index
    for spans in out.values():
        spans.sort(key=lambda item: (item[0], item[1]))
    return out, closers


def clip(intervals, segments):
    """Пересечение отрезков с отрезками смены: [(начало, конец, чат)], без нулевых."""
    out = []
    for start, end, chat in intervals:
        for seg_start, seg_end in segments:
            lo, hi = max(start, seg_start), min(end, seg_end)
            if hi > lo:
                out.append((lo, hi, chat))
    return out


def union(intervals):
    """Слитые отрезки [(начало, конец)] по возрастанию: параллельное — один раз."""
    merged = []
    for start, end, *_rest in sorted(intervals, key=lambda item: (item[0], item[1])):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
            continue
        merged.append([start, end])
    return [(start, end) for start, end in merged]


def _seconds(intervals):
    return sum((end - start).total_seconds() for start, end, *_rest in intervals)


def merge_adjacent(segments):
    """Соседние отрезки одного вида встык — одним: «Закончить смену» и «Начать» в ту же секунду не
    делят «Активный» надвое, и обработка через этот стык не рвётся на две строки журнала."""
    out = []
    for kind, start, end, note in segments:
        if out and out[-1][0] == kind and out[-1][2] == start:
            out[-1] = (kind, out[-1][1], end, out[-1][3])
            continue
        out.append((kind, start, end, note))
    return out


def split_active(segments, busy, clipped):
    """«Активный» на «Обработку чатов» и «Ожидание»: [(вид, начало, конец, пометка, чатов)].

    segments — после `merge_adjacent`; busy — `union` обработки; clipped — обработка по чатам
    (для числа чатов в отрезке). Обработка обрезана по отрезкам «Активного», а соседние отрезки
    слиты, поэтому каждый кусок `busy` целиком лежит в одном отрезке, а каждая лента `clipped` —
    в одном куске: чаты куска собираются одним проходом."""
    ordered = sorted(clipped, key=lambda item: (item[0], item[1]))
    chats_in = []
    cursor = 0
    for busy_start, busy_end in busy:
        chats = set()
        while cursor < len(ordered) and ordered[cursor][0] < busy_end:
            if ordered[cursor][1] > busy_start:
                chats.add(ordered[cursor][2])
            cursor += 1
        chats_in.append(len(chats))
    out = []
    index = 0
    for kind, start, end, note in segments:
        if kind != KIND_ACTIVE:
            out.append((kind, start, end, note, None))
            continue
        cursor = start
        while index < len(busy) and busy[index][1] <= start:
            index += 1
        while index < len(busy) and busy[index][0] < end:
            lo, hi = max(busy[index][0], start), min(busy[index][1], end)
            if lo > cursor:
                out.append((KIND_WAIT, cursor, lo, None, None))
            out.append((KIND_WORK, lo, hi, None, chats_in[index]))
            cursor = hi
            index += 1
        if end > cursor:
            out.append((KIND_WAIT, cursor, end, None, None))
    return out


# ── Сборка отчёта ────────────────────────────────────────────────────────────────────────────


def _chat_counts(shift_messages, closers):
    """(взято, обработано): разные чаты, где он действовал в смену (писал клиенту или нажал «Ответ не
    нужен»); из них — те, где разговор закончил он: его последнее действие в смене — последнее
    действие сотрудников в своём куске переписки (после него до паузы дольше 15 минут никто из
    сотрудников не писал и «Ответ не нужен» не нажимал).

    Кусок, а не 6-часовой диалог: клиент, вернувшийся через три часа с новым вопросом, — новый
    разговор, и цифра прошедшей смены от него не меняется. Реплику клиента, после которой ответа не
    было, правило само не судит («спасибо» или брошенный вопрос): её закрывает нажатие «Ответ не
    нужен» — с 17.11.2026 журнал нажатий есть (wazzup/unread.py), раньше этого дня его нет."""
    last_by_chat = {}
    for message in shift_messages:
        chat = (message['channel_id'], message['chat_id'])
        if chat not in last_by_chat or message['_pos'] > last_by_chat[chat]['_pos']:
            last_by_chat[chat] = message
    processed = sum(1 for message in last_by_chat.values() if id(message) in closers)
    return len(last_by_chat), processed


def _journal(segments, logout, close):
    out = []
    for kind, start, end, note, chats in segments:
        seconds = int(round((end - start).total_seconds()))
        if seconds <= 0:
            continue
        if kind == KIND_WORK and chats:
            note = '%d %s' % (chats, _plural(chats, 'чат', 'чата', 'чатов'))
        if out and out[-1]['kind'] == kind and out[-1]['_end'] == start and kind not in (KIND_WORK,):
            out[-1]['_end'] = end
            out[-1]['seconds'] += seconds
            continue
        out.append({'kind': kind, 'label': JOURNAL_LABELS[kind], 'note': note, '_start': start,
                    '_end': end, 'seconds': seconds})
    rows = [{'kind': row['kind'], 'style': row['kind'], 'label': row['label'], 'note': row['note'],
             'start_at': stamp(row['_start']), 'end_at': stamp(row['_end']), 'seconds': row['seconds']}
            for row in out]
    if close in _LOGOUT_CLOSES:
        rows.append({'kind': KIND_LOGOUT, 'style': KIND_LOGOUT, 'label': JOURNAL_LABELS[KIND_LOGOUT],
                     'note': _LOGOUT_NOTES.get(close), 'start_at': stamp(logout), 'end_at': stamp(logout),
                     'seconds': 0})
    return rows


def _plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 10 < n < 20:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def _tap_first_reply_days(rows, days):
    """Пропускает строки дальше как есть и попутно собирает в `days` по каждому чату за каждый
    календарный день: первое сообщение клиента и все исходящие, кроме ботов (см. first_reply_days)."""
    for row in rows or ():
        at = row.get('at')
        if at is not None:
            entry = days.setdefault((row.get('channel_id'), row.get('chat_id'), at.date()), [None, []])
            if not row.get('is_echo'):
                if entry[0] is None or at < entry[0]:
                    entry[0] = at
            elif not row.get('is_bot'):
                entry[1].append((at, str(row.get('message_id') or ''), row.get('user_id')))
        yield row


def first_reply_days(days, verifiers):
    """{user_id: [(момент ответа, задержка в секундах)]} — первый ответ так, как его считает
    «Аналитика» раздела «Чаты ОП» (`db.wazzup_operator_analytics`, «Ср. ответ», решение владельца
    17.11.2026: «правила чатов для ОП — из раздела «Чаты ОП»», период — день):

      * у чата за календарный день одно значение — от первого сообщения клиента за этот день до
        первого исходящего после него (любого, кроме ботов: рассылка или эхо без автора тоже
        «первый исходящий», и тогда первый ответ не засчитан никому);
      * засчитано тому, кто ответил первым, — здесь только верификаторам;
      * к смене ответ относит build_report — по времени ответа.

    Клиент, написавший вечером, а получивший ответ после полуночи, первого ответа не даёт (в новом
    дне от него нет сообщения) — так же, как в разделе за один день."""
    out = defaultdict(list)
    for client_at, outgoing in days.values():
        if client_at is None:
            continue
        after = [item for item in outgoing if item[0] > client_at]
        if not after:
            continue
        at, _message_id, user_id = min(after, key=lambda item: (item[0], item[1]))
        if user_id is None or int(user_id) not in verifiers:
            continue
        out[int(user_id)].append((_second(at), (at - client_at).total_seconds()))
    return out


def dismissal_actions(dismissals, verifiers):
    """Нажатия «Ответ не нужен» как действия сотрудников в переписке (журнал wazzup/unread.py, с
    17.11.2026): разговор, где последним действием было нажатие, закончил нажавший. Сообщением они
    не являются: в первый ответ «Чатов ОП» и в число сообщений не идут."""
    out = []
    for index, item in enumerate(dismissals or ()):
        at = parse_moment(item.get('at'))
        if at is None:
            continue
        user_id = item.get('user_id')
        user_id = int(user_id) if user_id is not None else None
        out.append({'channel_id': item.get('channel_id'), 'chat_id': item.get('chat_id'), 'at': at,
                    'kind': chat_mod.KIND_VERIFIER if user_id in verifiers else chat_mod.KIND_OTHER,
                    'message_id': 'dismissal:%d' % index, 'user_id': user_id, 'dismissal': True})
    return out


def build_report(*, events_by_operator, names, rows, day_from, day_to, now, phone_offs=None,
                 verifier_ids=None, silence=SILENCE, quiet_active=QUIET_ACTIVE,
                 pause_seconds=WORK_PAUSE_SECONDS, lead_max=LEAD_MAX, dismissals=None):
    """Завершённые смены с началом в [day_from, day_to], список операторов и журналы смен.

    events_by_operator — {id: [(время, ключ, пометка)]} событий раздела с запасом вокруг периода;
    names — {id: ФИО}; rows — сообщения аккаунта в формате входа `chat.classify` (вместе с
    рассылками и эхом без автора — они нужны первому ответу «Чатов ОП») с запасом до периода и после
    (ночная смена последнего дня, продолжение разговора); можно генератором — строки читаются один
    раз; phone_offs —
    {id: [моменты выхода iCORE Phone]}; verifier_ids — чьи ответы — ответы верификаторов (по
    умолчанию — все, у кого есть события раздела); dismissals — нажатия «Ответ не нужен»
    [{channel_id, chat_id, at, user_id}] (`dismissal_actions`).

    Возвращает {'shifts': [...], 'operators': [...], 'journals': {key: [...]},
    'tech_breaks': [...]}: журналы и тех. перерывы — для ручки журнала и выгрузки, экрану месяца
    они не нужны."""
    events_by_operator = events_by_operator or {}
    now = _second(now)
    verifiers = {int(v) for v in (verifier_ids if verifier_ids is not None else events_by_operator)}
    # Первый ответ — по сырым сообщениям, как в «Чатах ОП» (`first_reply_days`): рассылка без автора
    # там тоже «первый исходящий», а разбор табло (`chat.classify`) её отбрасывает. Отвод собирает
    # своё по ходу того же единственного прохода по строкам.
    days = {}
    messages = chat_mod.classify(_tap_first_reply_days(rows, days), verifiers)
    first_replies = first_reply_days(days, verifiers)
    messages.extend(dismissal_actions(dismissals, verifiers))
    # Задержки первого ответа и порядок сообщений — по точному времени; все отрезки — по целым
    # секундам: время в базе с долями, и без этого строка и её журнал расходились бы на секунды
    # суммами округлений.
    ordered = _ordered_by_chat(messages)
    for message in messages:
        message['at'] = _second(message['at'])
    spans_by_user, closers = processing_intervals(ordered, pause_seconds, lead_max)
    own_by_user = defaultdict(list)
    for message in messages:
        if message['kind'] == chat_mod.KIND_VERIFIER and message['user_id'] is not None:
            own_by_user[message['user_id']].append(message)

    shifts, journals, tech_breaks = [], {}, []
    operators = {}
    for operator_id, events in events_by_operator.items():
        operator_id = int(operator_id)
        own = sorted(own_by_user.get(operator_id, ()), key=lambda m: m['at'])
        own_times = [m['at'] for m in own]
        replies = sorted(first_replies.get(operator_id, ()))
        reply_times = [at for at, _delay in replies]
        spans = spans_by_user.get(operator_id, ())
        span_starts = [span[0] for span in spans]
        # Обработка, начатая раньше входа, задевает смену, только если длится до него: дальше
        # самой длинной ленты назад искать нечего.
        longest = max((end - start for start, end, _chat in spans), default=timedelta(0))
        offs = sorted(_second(parse_moment(v)) for v in (phone_offs or {}).get(operator_id, ())
                      if parse_moment(v) is not None)
        for shift in build_shifts(events, now, own_messages=own_times, phone_offs=offs, silence=silence,
                                  quiet_active=quiet_active):
            login, logout = shift['login'], shift['logout']
            day = login.date()
            if not shift['finished'] or day < day_from or day > day_to:
                continue
            window_from, window_to = login - MESSAGE_LEAD, logout + MESSAGE_TAIL
            shift_messages = own[bisect_left(own_times, window_from):bisect_right(own_times, window_to)]
            if not shift_messages and logout - login < MIN_SHIFT:
                continue
            taken, processed = _chat_counts(shift_messages, closers)
            first = replies[bisect_left(reply_times, window_from):bisect_right(reply_times, window_to)]
            first_sum = sum(delay for _at, delay in first)
            segments = merge_adjacent(shift['segments'])
            active = [(start, end) for kind, start, end, _note in segments if kind == KIND_ACTIVE]
            candidate = spans[bisect_left(span_starts, login - longest):bisect_right(span_starts, logout)]
            clipped = clip([span for span in candidate if span[1] > login], active)
            busy = union(clipped)
            totals = defaultdict(float)
            for kind, start, end, _note in segments:
                totals[kind] += (end - start).total_seconds()
            active_seconds = int(round(_seconds(busy)))
            worked = int(round(totals[KIND_ACTIVE] + totals[KIND_TRAINING] + totals[KIND_TECH]))
            tech = int(round(totals[KIND_TECH]))
            key = '%s:%s' % (operator_id, stamp(login))
            row = {
                'key': key,
                'date': day.isoformat(),
                'operator_id': operator_id,
                'name': (names or {}).get(operator_id) or '',
                'group_key': GROUP_KEY,
                'group_label': GROUP_LABEL,
                'login_at': stamp(login),
                'login_missing': shift['opened'] != OPENED_STATUS,
                'logout_at': stamp(logout),
                'logout_missing': shift['close'] == CLOSE_CUT,
                'logout_kind': shift['close'],
                'logout_next_day': logout.date() != day,
                'first_reply_seconds': int(round(first_sum / len(first))) if first else None,
                'first_sum': round(first_sum, 1),
                'first_count': len(first),
                'handling_seconds': int(round(_seconds(clipped))),
                'active_seconds': active_seconds,
                'waiting_seconds': max(0, worked - active_seconds - tech),
                'break_seconds': int(round(totals[KIND_BREAK])),
                'tech_seconds': tech,
                'taken_chats': taken,
                'processed_chats': processed,
                'worked_seconds': worked,
                'training_seconds': int(round(totals[KIND_TRAINING])),
                'offline_seconds': int(round(totals[KIND_OFFLINE])),
                'messages': sum(1 for message in shift_messages if not message.get('dismissal')),
            }
            shifts.append(row)
            journals[key] = _journal(split_active(segments, busy, clipped), logout, shift['close'])
            for kind, start, end, _note in segments:
                if kind == KIND_TECH:
                    tech_breaks.append({'key': key, 'date': row['date'], 'operator_id': operator_id,
                                        'name': row['name'], 'start_at': stamp(start), 'end_at': stamp(end),
                                        'seconds': int(round((end - start).total_seconds()))})
            entry = operators.setdefault(operator_id, {'id': operator_id, 'name': row['name'],
                                                       'group_keys': [GROUP_KEY]})
            entry['name'] = entry['name'] or row['name']
    shifts.sort(key=lambda r: (r['date'], r['name'], r['login_at']))
    tech_breaks.sort(key=lambda r: (r['date'], r['name'], r['start_at']))
    roster = sorted(operators.values(), key=lambda item: item['name'])
    return {'shifts': shifts, 'operators': roster, 'journals': journals, 'tech_breaks': tech_breaks}


def filter_tech_breaks(tech_breaks, shift_keys):
    """Тех. перерывы тех смен, что прошли фильтр экрана."""
    keys = set(shift_keys or ())
    return [row for row in tech_breaks or () if row['key'] in keys]


def available_months(today, retention_days=RETENTION_DAYS):
    """Месяцы, за которые переписка ещё хранится, — от нового к старому, как у линии. День на краю
    хранения уже наполовину вычищен, поэтому граница — на сутки позже (как у выгрузки табло чата)."""
    return activity_mod.available_months(today, int(retention_days) - 1)


def retention_floor(today, retention_days=RETENTION_DAYS):
    return today - timedelta(days=int(retention_days) - 1)
