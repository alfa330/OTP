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


def _before_queue_touch(linkedid, client, legs, prefix_lines=None):
    """Касание без оператора: номер, наша линия, сколько человек слушал приветствие.

    Разговора в нём нет, хотя станция пишет ANSWERED и billsec в секунды: на звонок
    ответил автоинформатор, а не человек. Поэтому `_touch` здесь не годится — он
    назвал бы это «Разговором»."""
    legs.sort(key=lambda leg: (leg["at"] or "", leg["disposition"] or ""))
    started = min((leg["at"] for leg in legs if leg["at"]), default=None)
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
        "dial_seconds": max((leg["duration"] for leg in legs), default=0),
        "queue": "",
        "line_number": _line_number(legs, prefix_lines),
        "recording_url": "",
        "has_recording": False,
        "linkedid": linkedid,
        "legs": len(legs),
    }


def _touch(linkedid, client, legs, resolve_operator, prefix_lines=None):
    legs.sort(key=lambda leg: (leg["at"] or "", leg["disposition"] or ""))
    kind = "out" if any(leg["kind"] == "out" for leg in legs) else "in"

    # Разговор считаем по плечу САМОГО АГЕНТА; плечо очереди — только на крайний
    # случай, его billsec раздут ожиданием в очереди.
    agent_legs = [leg for leg in legs if leg["agent"]]
    real_legs = [leg for leg in agent_legs if not leg["queue_leg"]]
    talked_real = [leg for leg in real_legs
                   if leg["disposition"] == "ANSWERED" and leg["billsec"] > 0]
    talked_any = [leg for leg in agent_legs
                  if leg["disposition"] == "ANSWERED" and leg["billsec"] > 0]
    talked = talked_real or talked_any
    billsec = max((leg["billsec"] for leg in talked), default=0)

    if talked:
        pick = max(talked, key=lambda leg: leg["billsec"])
    elif real_legs:
        pick = real_legs[-1]
    elif agent_legs:
        pick = agent_legs[-1]
    else:
        pick = legs[-1]

    dispositions = [leg["disposition"] for leg in legs]
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
    if ext is None:
        # Оператора не назвало ни одно плечо. Если во всей группе засветился
        # ровно один внутренний номер — это он; если несколько, гадать нельзя.
        candidates = sorted({e for leg in legs for e in leg["exts"]})
        ext = candidates[0] if len(candidates) == 1 else None

    if kind == "out":
        call_type = TYPE_OUT
    else:
        call_type = TYPE_IN if billsec > 0 else TYPE_IN_MISSED

    started = min((leg["at"] for leg in legs if leg["at"]), default=pick["at"])
    answered = pick["at"] if pick["at"] and pick["at"] != started else None
    name, direction = resolve_operator(ext, started) if ext else ("", "")
    url = _recording_url(pick)

    return {
        "started_at": str(started).replace("T", " ") if started else "",
        "answered_at": str(answered).replace("T", " ") if answered else "",
        "phone": client,
        "operator": name,
        "ext": ext or "",
        "direction": direction,
        "call_type": call_type,
        "result": result,
        "talk_seconds": billsec,
        "dial_seconds": max((leg["duration"] for leg in legs), default=0),
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
    touches = [_touch(linkedid, client, legs, resolve_operator, prefix_lines)
               for (linkedid, client), legs in groups.items()]
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
