# -*- coding: utf-8 -*-
"""Склейка строк CDR FreePBX в «касания» — по одной записи на звонок.

Единица касания — ОДИН ВЫЗОВ, а не строка CDR. У входящего в очередь строк
бывает до двух десятков (каждая попытка дозвониться до агента — своя строка
BUSY), у исходящего автодозвона — одна-две. Склейка идёт по `linkedid`.

Логика перенесена один в один из офлайн-сборки, которой с июня 2026 собирались
файлы «Лиды + касания ОП» (outputs/kasaniya_leads_*/_build_touches.py). Оттуда
же и все ловушки, каждая из которых стоила отдельного разбора:

* **Внутренний номер оператора живёт в ИМЕНИ ФАЙЛА записи**, а не в `src`/`dst`.
  У ~78% звонков (весь автодозвон, `dst` вида `4242*<номер>`) внутреннего номера
  в `src`/`dst` нет вовсе: атрибуция только по ним даёт 18,5% покрытия, по имени
  файла — 89,8%.

* **`billsec` плеча ОЧЕРЕДИ включает ожидание в очереди** (медиана +3 с, бывает
  и +10 минут). Длительность разговора берётся только с плеча самого агента;
  плечо очереди годится лишь чтобы узнать, КТО ответил.

* **`disposition = ANSWERED` при `billsec = 0` — это не разговор**, а повторный
  набор автодозвонщика: соединение было, говорить не начали. Такому касанию
  ставится результат «Сброс без разговора», а не «Разговор».

* **Время касания — начало вызова**, а не момент, когда сняли трубку: у
  входящего через очередь между этим медиана 16 секунд, а бывает и 11 минут
  ожидания. Момент ответа отдаётся отдельным полем `answered_at`.

Модуль чистый: ни сети, ни базы, ни файлов — на вход список словарей-строк CDR,
на выходе список словарей-касаний. Поэтому его можно прогнать на сохранённых
сутках и сверить с уже собранными файлами (это и делает tests/test_cdr_touches.py).
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta

# Внутренний номер в имени канала: PJSIP/6650-0002ca2e, Local/6687@from-queue.
EXT_RE = re.compile(r"(?:PJSIP|SIP|Local)/(\d{3,4})[-@]")
# Очередь — четыре цифры, первая тройка. Человеку такой номер не принадлежит.
QUEUE_RE = re.compile(r"^3\d{3}$")

# Формы имени файла записи. Порядок проверки важен: он же в офлайн-сборке.
#   out-<транк>*<клиент>-<ext>-...      исходящий, есть и клиент, и оператор
#   q-<очередь>-<клиент>-...            плечо очереди, оператора в имени нет
#   external-<ext>-<клиент>-...         доставка входящего агенту
#   in-<did>-<клиент>-...               входящий; ПЕРВОЕ число — наш DID, не клиент
OUT_REC = re.compile(r"^out-(?:\d+\*)?\+?(\d{9,15})-(\d{3,4})-")
Q_REC = re.compile(r"^q-(\d{3,4})-\+?(\d{9,15})-")
EXT_REC = re.compile(r"^external-(\d{3,4})-\+?(\d{9,15})-")
IN_REC = re.compile(r"^in-(\d{9,15})-\+?(\d{9,15})-")

# Отвечающего агента входящего звонка называет ОТВЕТИВШЕЕ плечо очереди.
# На строках BUSY тот же Local/<ext> — всего лишь попытка дозвона, не ответ.
QUEUE_ANSWER_RE = re.compile(r"Local/(\d{3,4})@from-queue")
QUEUE_OUT_RE = re.compile(r"Local/(3\d{3})@ext-to-queue")
# Сторона оператора у попытки очереди: `Local/6728@from-queue-0005dc4a;2` → его телефон.
QUEUE_AGENT_SIDE_RE = re.compile(r"^Local/\d{3,4}@from-queue-[0-9a-f]+;2$")
# Собственный канал дозвонщика (автообзвон, заказ перезвона): `Local/3016@ext-to-queue-…;1`.
DIALER_CHANNEL_RE = re.compile(r"^Local/3\d{3}@ext-to-queue-")

# Наш номер — линия таксопарка: его набрал клиент или с него позвонили клиенту.
# Живёт в трёх местах, по доле касаний суток 23.09.2026:
#   входящий   `did` — номер, который набрал клиент (у всех 304 входящих);
#   исходящий  транк в канале назначения: PJSIP/+77475777778-00066790 (1589 из 1696);
#   заявка автообзвона (dst 3322*…, канал Local/3034@ext-to-queue, 104 из 1696) —
#              транка нет, но префикс набора тот же, что у звонков через транк.
# Внутренний номер в канале (PJSIP/6687-…) — не транк: десяти цифр в нём нет.
TRUNK_CHANNEL_RE = re.compile(r"^(?:PJSIP|SIP)/([^/]+?)-[0-9a-f]{8}$")
TRUNK_NUMBER_RE = re.compile(r"\d{10,12}")
DIAL_PREFIX_RE = re.compile(r"^(\d{3,6})\*")

# Куда сложены записи разговоров, когда CDR не отдал готовую ссылку.
RECORDINGS_BASE = "http://192.168.88.251/recordings"

TYPE_OUT = "Исходящий"
TYPE_IN = "Входящий"
TYPE_IN_MISSED = "Входящий (не приняли)"
# Клиент положил трубку на приветствии или в меню IVR — до очереди звонок не дошёл,
# оператору не звонили. В разделе такие звонки видны строками, но ни в одном итоге,
# ни на «Табло ОП», ни в режиме «Сделки» не считаются: работы оператора в них нет.
TYPE_IN_BEFORE_QUEUE = "Входящий (не дошёл до очереди)"

RESULT_TALK = "Разговор"
RESULT_DROPPED = "Сброс без разговора"
RESULT_NO_ANSWER = "Не ответил"
RESULT_BUSY = "Занято"
RESULT_FAILED = "Не соединился"
RESULT_BEFORE_QUEUE = "Сброс до очереди"

_DISPOSITION_RESULT = {
    "ANSWERED": "Отвечен",
    "NO ANSWER": RESULT_NO_ANSWER,
    "BUSY": RESULT_BUSY,
    "FAILED": RESULT_FAILED,
}


def norm_phone(value):
    """Номер клиента к десяти цифрам: 8 705…, +7 705…, 7705… — это один человек."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def parse_row(row):
    """Строка CDR → (телефон клиента, направление, внутренний номер) или None.

    None означает «в касание не годится»: клиентского номера в строке нет. Так
    отсеиваются внутренние переговоры сотрудников между собой — их в CDR около
    половины строк, и звонком клиенту они не являются.
    """
    recording = str(row.get("recordingfile") or "")
    src = str(row.get("src") or "")
    dst = str(row.get("dst") or "")
    client, kind, agent = "", None, None

    matched = OUT_REC.match(recording)
    if matched:
        client, agent, kind = norm_phone(matched.group(1)), matched.group(2), "out"
    elif Q_REC.match(recording):
        client, kind = norm_phone(Q_REC.match(recording).group(2)), "in"
    elif EXT_REC.match(recording):
        matched = EXT_REC.match(recording)
        client, agent, kind = norm_phone(matched.group(2)), matched.group(1), "in"
    elif IN_REC.match(recording):
        client, kind = norm_phone(IN_REC.match(recording).group(2)), "in"

    if not client:
        # Записи нет или имя незнакомой формы — читаем номера набора.
        if "*" in dst:
            # dst = 3322*77011253797: транк-префикс и за ним номер клиента.
            client, kind = norm_phone(dst.split("*", 1)[1]), "out"
        elif re.fullmatch(r"\d{3,4}", dst):
            # Набран наш внутренний номер или очередь — значит, звонят нам.
            client, kind = norm_phone(src), "in"

    if not client:
        return None
    return client, kind, agent


def parse_before_queue(row):
    """Строка звонка, не дошедшего до очереди → номер клиента или None.

    Так выглядит клиент, положивший трубку на автоинформаторе или в меню IVR:
    `dst = 's'` (точка входа диалплана), записи нет, плеча агента нет, в `src` —
    внешний номер. `parse_row` такие строки не берёт — ни одно его правило на них не
    срабатывает, — и это правильно для касаний с оператором. За 23.09.2026 таких 69 на
    304 входящих, медиана 6 секунд: у Jana приветствие 16 с, человек его не дослушал.
    """
    if str(row.get("dst") or "") != "s" or row.get("recordingfile"):
        return None
    if EXT_RE.search(str(row.get("dstchannel") or "")):
        return None
    src = str(row.get("src") or "")
    if re.fullmatch(r"\d{3,4}", src):
        return None
    return norm_phone(src) or None


def trunk_number(channel):
    """Номер из имени транка в канале: `PJSIP/87470939675_jana_olx-000667a7` → 7470939675.

    У транка без номера в имени (`PJSIP/Beeline-…`) и у внутреннего номера
    (`PJSIP/6687-…`) — пусто: десяти цифр подряд там нет."""
    matched = TRUNK_CHANNEL_RE.match(str(channel or ""))
    if not matched:
        return ""
    digits = TRUNK_NUMBER_RE.search(matched.group(1))
    return norm_phone(digits.group(0)) if digits else ""


def _leg_line(row, kind, client):
    """Наш номер на плече: у входящего — набранный клиентом, у исходящего — с которого
    звонили. `src` исходящего — это номер, показанный клиенту, но только когда в нём
    десять цифр: у заявки автообзвона там внутренний номер оператора."""
    if kind == "in":
        return norm_phone(row.get("did")) or trunk_number(row.get("channel"))
    number = trunk_number(row.get("dstchannel"))
    if number:
        return number
    src = norm_phone(row.get("src"))
    return src if src and src != client else ""


def _leg(row, kind, agent, client=""):
    """Строка CDR → плечо вызова: только то, что нужно для склейки."""
    src = str(row.get("src") or "")
    dst = str(row.get("dst") or "")
    disposition = row.get("disposition")

    # Плечо агента: набран внутренний номер и канал назначения — он же.
    if agent is None and re.fullmatch(r"\d{3,4}", dst) and not QUEUE_RE.match(dst):
        matched = EXT_RE.search(str(row.get("dstchannel") or ""))
        if matched and matched.group(1) == dst:
            agent = dst

    # Ответившее плечо очереди называет агента, но НЕ длительность разговора.
    queue_leg = False
    if agent is None and disposition == "ANSWERED" and QUEUE_RE.match(dst):
        matched = QUEUE_ANSWER_RE.search(str(row.get("dstchannel") or ""))
        if matched:
            agent, queue_leg = matched.group(1), True

    if (agent is None and kind == "out" and re.fullmatch(r"\d{3,4}", src)
            and not QUEUE_RE.match(src)):
        agent = src

    exts = set()
    for field in ("channel", "dstchannel"):
        for matched in EXT_RE.finditer(str(row.get(field) or "")):
            if not QUEUE_RE.match(matched.group(1)):
                exts.add(matched.group(1))

    queues = set()
    for value in (src, dst):
        if QUEUE_RE.match(value):
            queues.add(value)
    for matched in QUEUE_OUT_RE.finditer(str(row.get("channel") or "")):
        queues.add(matched.group(1))

    prefix = DIAL_PREFIX_RE.match(dst) if kind == "out" else None
    dialer = bool(DIALER_CHANNEL_RE.match(str(row.get("channel") or "")))

    return {
        "at": row.get("calldate"),
        "kind": kind,
        "agent": agent,
        "exts": sorted(exts),
        "queues": sorted(queues),
        "disposition": disposition,
        "billsec": int(row.get("billsec") or 0),
        "duration": int(row.get("duration") or 0),
        "recordingfile": str(row.get("recordingfile") or ""),
        "recording_url": row.get("recording_url"),
        "queue_leg": queue_leg,
        "agent_side": bool(QUEUE_AGENT_SIDE_RE.match(str(row.get("channel") or ""))),
        "dialer": dialer,
        # Служебная строка дозвонщика — Playback, Congestion, AGI на его же канале без канала
        # назначения: клиента она не набирала, и её ANSWERED с секундой — не ответ клиента.
        "service": kind == "out" and dialer and not str(row.get("dstchannel") or ""),
        "line": _leg_line(row, kind, client),
        "dial_prefix": prefix.group(1) if prefix else "",
    }


def _recording_url(leg):
    url = leg.get("recording_url") or ""
    if not url and leg["recordingfile"] and leg["at"]:
        day = str(leg["at"])[:10].replace("-", "/")
        url = "%s/%s/%s" % (RECORDINGS_BASE, day, leg["recordingfile"])
    return url


# Полные имена файлов записей — с датой, временем и uniqueid плеча в хвосте. Захватывается
# только uniqueid (третья группа у каждой формы): дата и время для проверки не нужны.
_REC_TAIL = r"-\d{8}-\d{6}-(\d+\.\d+)\.(?:wav|mp3|gsm)$"
REC_EXT_FULL = re.compile(r"external-(\d{3,4})-\+?(\d{9,15})" + _REC_TAIL)
REC_OUT_FULL = re.compile(r"out-(?:\d+\*)?\+?(\d{9,15})-(\d{3,4})" + _REC_TAIL)
REC_Q_FULL = re.compile(r"q-(\d{3,4})-\+?(\d{9,15})" + _REC_TAIL)


def recording_belongs_to(url, ext, phone, linkedid):
    """Достоверно ли запись по ссылке — этого звонка и этого оператора.

    С 09.09.2026 станция отдаёт одну строку на звонок и подставляет в неё ссылку
    на запись по номеру клиента: у трети принятых входящих это файл ДРУГОГО агента
    той же группы вызова (ring-all), которого клиент не слышал, — а бывает, что
    и файла такого нет, потому что тот агент не ответил. В журнал оценок такую
    запись класть нельзя: супервайзер слушал бы чужой разговор или тишину.

    Правило: `external-<ext>-<клиент>` и `out-…*<клиент>-<ext>` — только при
    совпадении и внутреннего номера, и клиента; `q-<очередь>-<клиент>` — запись
    плеча очереди, она одна на звонок, поэтому сверяется uniqueid файла с linkedid.
    Замер 01–16.09.2026 по направлениям отдела продаж: у исходящих проходит 100 %,
    у входящих 73 %, остальные — чужие файлы."""
    name = str(url or "").rsplit("/", 1)[-1]
    ext = str(ext or "")
    client = norm_phone(phone)
    if not name or not ext or not client:
        return False
    matched = REC_EXT_FULL.search(name)
    if matched:
        return matched.group(1) == ext and norm_phone(matched.group(2)) == client
    matched = REC_OUT_FULL.search(name)
    if matched:
        return matched.group(2) == ext and norm_phone(matched.group(1)) == client
    matched = REC_Q_FULL.search(name)
    if matched:
        return norm_phone(matched.group(2)) == client and matched.group(3) == str(linkedid or "")
    return False


def _line_number(legs, prefix_lines):
    """Наш номер звонка: первое плечо, которое его назвало; у заявки автообзвона — по
    префиксу набора. Плечи уже отсортированы по времени, так что у входящего первым
    идёт приход звонка с набранным номером."""
    for leg in legs:
        if leg["line"]:
            return leg["line"]
    for leg in legs:
        number = (prefix_lines or {}).get(leg["dial_prefix"]) if leg["dial_prefix"] else ""
        if number:
            return number
    return ""


def _moment(value):
    text = str(value or "")[:19].replace("T", " ")
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _call_seconds(legs):
    """Сколько длился звонок целиком: от начала первого плеча до конца последнего.

    Строка CDR — одно плечо, и у входящего через очередь каждая попытка дозвониться до
    оператора — своё плечо в пару секунд: самое длинное из них короче звонка. 29.09.2026
    клиент 07:19:07 ждал в очереди 3035 две с половиной минуты (70 плеч), а самое длинное
    плечо — 14 с. По этой величине портал судит, когда звонок кончился (минута ожидания
    перезвона, полнота журнала очередей), так что занижать её нельзя. У одной строки на
    звонок (так отдавала надстройка станции) это просто её `duration`."""
    longest = max((leg["duration"] for leg in legs), default=0)
    spans = [(moment, moment + timedelta(seconds=leg["duration"]))
             for leg in legs for moment in (_moment(leg["at"]),) if moment is not None]
    if not spans:
        return longest
    whole = int((max(end for _, end in spans) - min(start for start, _ in spans)).total_seconds())
    return max(longest, whole)


def _outcome_legs(legs, kind):
    """Плечи, по которым судят, чем кончился звонок.

    У исходящего — плечи К КЛИЕНТУ, и только они; список может быть и пустым. Автообзвон
    сначала соединяет оператора: очередь звонит ему, телефон снимает трубку сам, и в сыром CDR
    это плечо ANSWERED с billsec в секунду — даже когда клиент так и не ответил (51 звонок за
    29.09.2026 вышел бы «Разговором» в секунду). То же со служебными строками дозвонщика
    (Playback, Congestion, AGI без канала назначения): 28 исходящих за 22–29.09 получали от них
    «разговор» в 1–3 с без единого ответа транка. Робот пропущенных принял бы такой перезвон за
    дозвон и не завёл бы сделку. Ответил ли клиент, знает только строка, набиравшая клиента.

    У входящего — все плечи: разговор там берётся с плеча оператора (`_touch`)."""
    if kind == "out":
        return [leg for leg in legs if leg["kind"] == "out" and not leg["service"]]
    return legs


def _answer_moment(leg):
    """Когда на плече сняли трубку: начало плеча плюс звон (`duration − billsec`).

    `calldate` плеча — начало вызова, а не ответ: плечо оператора начинается, когда у него
    зазвонил телефон."""
    moment = _moment(leg["at"])
    if moment is None:
        return str(leg["at"] or "").replace("T", " ")
    ring = max(0, leg["duration"] - leg["billsec"])
    return (moment + timedelta(seconds=ring)).strftime("%Y-%m-%d %H:%M:%S")


def _last_rung(legs):
    """Кому очередь звонила последним — номер непринятого входящего.

    Непринятый звонок в сыром CDR — десятки попыток очереди по разным операторам, и ни
    одна не называет «того самого». Касание держит номер того, кому очередь звонила,
    когда клиент положил трубку (так по нему и считаются «потерянные» оператора и его
    группы на «Табло ОП»): последнее плечо, где назван ровно один внутренний номер.
    Плечи уже отсортированы по времени."""
    for leg in reversed(legs):
        if len(leg["exts"]) == 1:
            return leg["exts"][0]
    return None


def _own_recording(pick, legs, ext):
    """Запись звонка: файл выбранного плеча, а без него — файл того же оператора с другого
    плеча этого же звонка или запись самой очереди (`q-…`, она одна на звонок).

    Запись чужого оператора не берётся никогда: при параллельном дозвоне файл пишется у
    каждого, кому звонили, и супервайзер слушал бы не тот разговор."""
    url = _recording_url(pick)
    if url:
        return url
    for leg in reversed(legs):
        if leg["recordingfile"] and ext and leg["agent"] == ext:
            return _recording_url(leg)
    for leg in reversed(legs):
        if Q_REC.match(leg["recordingfile"]):
            return _recording_url(leg)
    return ""


def _queue_attempts_only(legs):
    """В группе одни попытки очереди дозвониться до операторов, а самого звонка в CDR нет.

    Так в сыром CDR выглядит заявка автообзвона, которой никто не ответил: она висит в
    очереди до четверти часа, очередь звонит операторам каждые пять секунд, и каждая
    попытка — строка `Local/<ext>@from-queue…;2` с номером клиента в `src`. Строки самой
    заявки нет никогда. Без этого правила склейка назвала бы её «Входящий (не приняли)»
    (28.09.2026 — четыре таких, 48–860 строк у каждой), и робот пропущенных завёл бы на
    неё сделку в amoCRM. Надстройка станции, отдававшая одну строку на звонок, их не
    показывала вовсе — показывать их и сейчас не за что: клиент нам не звонил.

    Разговор в такой группе — уже не призрак: оператор взял заявку, и касание остаётся."""
    return all(leg["agent_side"] and not (leg["disposition"] == "ANSWERED" and leg["billsec"] > 0)
               for leg in legs)


def _reached_nobody(legs):
    """Входящий, у которого ни одно плечо не дошло ни до очереди, ни до человека.

    Так выглядит звонок, который меню отправило туда, где никто не ответит: кнопка «2»
    меню линии Dongelek ведёт на `2030` через транк на другую машину, и этот маршрут с
    17.05.2025 не соединил ни одного звонка (`dst = 2030`, канала назначения нет). Туда же —
    объявление, у которого станция пишет запись `in-…`: по записи `parse_row` принимает
    строку, хотя дальше приветствия звонок не ушёл. За 19.08–28.09.2026 таких 48, и все они
    числились «не приняли» — как будто оператор их пропустил.

    Запись `q-<очередь>-…` — улика обратного: её пишет сама очередь. 12.09.2026 звонок
    простоял в 3001 десять минут, очередь сдалась по таймауту и отправила его в
    `app-blackhole`, и станция показала именно эту строку: `dst = hangup`, очереди нет."""
    return all(leg["kind"] == "in" and not leg["agent"] and not leg["exts"] and not leg["queues"]
               and not Q_REC.match(leg["recordingfile"])
               for leg in legs)


def _before_queue_touch(linkedid, client, legs, prefix_lines=None):
    """Касание без оператора: номер, наша линия, сколько человек слушал приветствие.

    Разговора в нём нет, хотя станция пишет ANSWERED и billsec в секунды: на звонок
    ответил автоинформатор, а не человек. Поэтому `_touch` здесь не годится — он
    назвал бы это «Разговором»."""
    legs.sort(key=lambda leg: (leg["at"] or "", leg["disposition"] or ""))
    started = min((leg["at"] for leg in legs if leg["at"]), default=None)
    # Запись — только собственная, по имени файла строки: объявление «Тенге Такси» станция
    # пишет. Ссылку без файла не берём: станция подставляет её по номеру клиента, и у
    # строки приветствия это была бы запись чужого звонка.
    url = next((_recording_url(leg) for leg in legs if leg["recordingfile"]), "")
    return {
        "started_at": str(started).replace("T", " ") if started else "",
        "answered_at": "",
        "phone": client,
        "operator": "",
        "ext": "",
        "direction": "",
        "call_type": TYPE_IN_BEFORE_QUEUE,
        "result": RESULT_BEFORE_QUEUE,
        "talk_seconds": 0,
        "dial_seconds": _call_seconds(legs),
        "queue": "",
        "line_number": _line_number(legs, prefix_lines),
        "recording_url": url,
        "has_recording": bool(url),
        "linkedid": linkedid,
        "legs": len(legs),
    }


def _touch(linkedid, client, legs, resolve_operator, prefix_lines=None):
    legs.sort(key=lambda leg: (leg["at"] or "", leg["disposition"] or ""))
    kind = "out" if any(leg["kind"] == "out" for leg in legs) else "in"
    decisive = _outcome_legs(legs, kind)

    if kind == "out":
        # Разговор исходящего — по плечу клиента, кто бы на нём ни значился: у строки транка
        # оператора называет только имя файла записи, а запись пишется не всегда. Плеча к
        # клиенту нет вовсе — клиента не набирали, и разговора с ним не было.
        talked = [leg for leg in decisive
                  if leg["disposition"] == "ANSWERED" and leg["billsec"] > 0]
        if talked:
            pick = max(talked, key=lambda leg: leg["billsec"])
        elif decisive:
            pick = decisive[-1]
        else:
            # Клиента не набирали (заказ перезвона, AGI): звонок называет собственная строка
            # заказа — в её src тот, кто заказал. Последнее по времени плечо здесь — попытка
            # очереди, а при равных секундах это чужое «Занято» (24.09.2026: 57 звонков).
            pick = next((leg for leg in reversed(legs) if leg["kind"] == "out"), legs[-1])
        dispositions = [leg["disposition"] for leg in (decisive or legs)]
    else:
        # Разговор считаем по плечу САМОГО АГЕНТА; плечо очереди — только на крайний
        # случай, его billsec раздут ожиданием в очереди.
        agent_legs = [leg for leg in decisive if leg["agent"]]
        real_legs = [leg for leg in agent_legs if not leg["queue_leg"]]
        talked_real = [leg for leg in real_legs
                       if leg["disposition"] == "ANSWERED" and leg["billsec"] > 0]
        talked_any = [leg for leg in agent_legs
                      if leg["disposition"] == "ANSWERED" and leg["billsec"] > 0]
        talked = talked_real or talked_any

        if talked:
            pick = max(talked, key=lambda leg: leg["billsec"])
        elif real_legs:
            pick = real_legs[-1]
        elif agent_legs:
            pick = agent_legs[-1]
        else:
            pick = decisive[-1]
        dispositions = [leg["disposition"] for leg in decisive]
    billsec = max((leg["billsec"] for leg in talked), default=0)
    if billsec > 0:
        result = RESULT_TALK
    elif "ANSWERED" in dispositions:
        result = RESULT_DROPPED
    elif "NO ANSWER" in dispositions:
        result = RESULT_NO_ANSWER
    elif "BUSY" in dispositions:
        result = RESULT_BUSY
    else:
        last = dispositions[-1] if dispositions else None
        result = _DISPOSITION_RESULT.get(last, last or "неизвестно")

    ext = pick["agent"]
    if ext is None and kind == "out":
        # Строка транка без записи оператора не называет — его называет плечо оператора,
        # снявшего трубку: у автообзвона это тот, кто взял заявку.
        took = [leg for leg in legs if leg["agent"] and leg["disposition"] == "ANSWERED"]
        if took:
            ext = max(took, key=lambda leg: leg["billsec"])["agent"]
    if ext is None:
        # Оператора не назвало ни одно плечо. Если во всей группе засветился
        # ровно один внутренний номер — это он; если несколько, гадать нельзя.
        candidates = sorted({e for leg in legs for e in leg["exts"]})
        ext = candidates[0] if len(candidates) == 1 else None
    if ext is None and kind == "in" and not talked:
        # Кроме непринятого: у него «тот самый» — кому очередь звонила последним.
        ext = _last_rung(legs)

    if kind == "out":
        call_type = TYPE_OUT
    else:
        call_type = TYPE_IN if billsec > 0 else TYPE_IN_MISSED

    started = min((leg["at"] for leg in legs if leg["at"]), default=pick["at"])
    started_text = str(started).replace("T", " ") if started else ""
    # Момент ответа — только у звонка, где поговорили: у брошенного в очереди его не было,
    # и на пустом поле держится склейка «принят / потерян» на портале. У строки дозвонщика
    # CDR отмечает ответ, когда трубку снял ОПЕРАТОР (с этой секунды набирают клиента), а не
    # клиент: в 82 из 93 таких строк за 22–29.09 он совпал с началом набора. Ответа клиента
    # там нет — поле честно пустое.
    answered = (_answer_moment(pick)
                if talked and pick["at"] and not (kind == "out" and pick["dialer"]) else "")
    if answered == started_text:
        answered = ""
    name, direction = resolve_operator(ext, started) if ext else ("", "")
    url = _own_recording(pick, legs, ext)

    return {
        "started_at": started_text,
        "answered_at": answered,
        "phone": client,
        "operator": name,
        "ext": ext or "",
        "direction": direction,
        "call_type": call_type,
        "result": result,
        "talk_seconds": billsec,
        "dial_seconds": _call_seconds(legs),
        "queue": ",".join(sorted({q for leg in legs for q in leg["queues"]})),
        "line_number": _line_number(legs, prefix_lines),
        "recording_url": url,
        "has_recording": bool(url),
        "linkedid": linkedid,
        "legs": len(legs),
    }


def build_touches(rows, resolve_operator=None, phones=None):
    """Строки CDR → список касаний, отсортированный по времени начала вызова.

    resolve_operator — (ext, время) -> (ФИО, направление). По умолчанию имя не
    подставляется: модуль не должен знать, откуда берётся справочник.

    phones — необязательное множество нормализованных номеров: если задано, в
    результат попадают только звонки по этим номерам (так офлайн-сборка
    оставляла касания по лидам amoCRM, и по этому же режиму логика сверяется
    с уже собранными файлами).
    """
    if resolve_operator is None:
        def resolve_operator(_ext, _when):
            return ("", "")

    groups = defaultdict(list)
    before_queue = defaultdict(list)
    seen_prefixes = defaultdict(lambda: defaultdict(int))
    for row in rows:
        parsed = parse_row(row)
        if not parsed:
            client = parse_before_queue(row)
            if client and (phones is None or client in phones):
                before_queue[(row.get("linkedid"), client)].append(_leg(row, "in", None, client))
            continue
        client, kind, agent = parsed
        leg = _leg(row, kind, agent, client)
        # Префикс набора учим по ВСЕМ строкам, а не только по отобранным номерам:
        # заявке автообзвона номер подсказывает чужой звонок через тот же транк.
        if leg["dial_prefix"] and leg["line"]:
            seen_prefixes[leg["dial_prefix"]][leg["line"]] += 1
        if phones is not None and client not in phones:
            continue
        groups[(row.get("linkedid"), client)].append(leg)

    prefix_lines = learn_prefix_lines(seen_prefixes)
    touches = [_before_queue_touch(linkedid, client, legs, prefix_lines) if _reached_nobody(legs)
               else _touch(linkedid, client, legs, resolve_operator, prefix_lines)
               for (linkedid, client), legs in groups.items()
               if not _queue_attempts_only(legs)]
    # Звонок, у которого есть хоть одна строка очереди или агента, — обычное касание, и
    # строки приветствия к нему не подмешиваются: иначе у него сдвинулись бы начало и
    # число плеч, а на них стоит расчёт ожидания на «Табло ОП».
    touches.extend(_before_queue_touch(linkedid, client, legs, prefix_lines)
                   for (linkedid, client), legs in before_queue.items()
                   if (linkedid, client) not in groups)
    touches.sort(key=lambda touch: (touch["started_at"], touch["phone"]))
    return touches


def learn_prefix_lines(seen):
    """{префикс набора: {номер: сколько раз}} → {префикс: номер}.

    Префикс — это исходящий маршрут станции (`7778*` уходит через транк +77475777778),
    поэтому у одного префикса один номер. Если в сутках их встретилось несколько
    (маршрут перенастроили посреди дня), берётся самый частый; ничья — меньший номер,
    чтобы повторная склейка тех же суток давала то же самое."""
    out = {}
    for prefix, numbers in seen.items():
        if numbers:
            out[prefix] = min(numbers, key=lambda number: (-numbers[number], number))
    return out


def summarize(touches):
    """Сводка по списку касаний — то, что показывается карточками над таблицей.

    Двойник queries.summary: не дошедшие до очереди в итоги не входят, их число — отдельно."""
    before_queue = sum(1 for t in touches if t["call_type"] == TYPE_IN_BEFORE_QUEUE)
    touches = [t for t in touches if t["call_type"] != TYPE_IN_BEFORE_QUEUE]
    talk = [t for t in touches if t["talk_seconds"] > 0]
    return {
        "before_queue": before_queue,
        "total": len(touches),
        "talks": len(talk),
        "outgoing": sum(1 for t in touches if t["call_type"] == TYPE_OUT),
        "incoming": sum(1 for t in touches if t["call_type"] == TYPE_IN),
        "incoming_missed": sum(1 for t in touches if t["call_type"] == TYPE_IN_MISSED),
        "talk_seconds": sum(t["talk_seconds"] for t in talk),
        "operators": len({t["ext"] for t in touches if t["ext"]}),
        "phones": len({t["phone"] for t in touches if t["phone"]}),
        "with_recording": sum(1 for t in touches if t["has_recording"]),
    }
