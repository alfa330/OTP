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

# Куда сложены записи разговоров, когда CDR не отдал готовую ссылку.
RECORDINGS_BASE = "http://192.168.88.251/recordings"

TYPE_OUT = "Исходящий"
TYPE_IN = "Входящий"
TYPE_IN_MISSED = "Входящий (не приняли)"

RESULT_TALK = "Разговор"
RESULT_DROPPED = "Сброс без разговора"
RESULT_NO_ANSWER = "Не ответил"
RESULT_BUSY = "Занято"
RESULT_FAILED = "Не соединился"

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


def _leg(row, kind, agent):
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
    }


def _recording_url(leg):
    url = leg.get("recording_url") or ""
    if not url and leg["recordingfile"] and leg["at"]:
        day = str(leg["at"])[:10].replace("-", "/")
        url = "%s/%s/%s" % (RECORDINGS_BASE, day, leg["recordingfile"])
    return url


def _touch(linkedid, client, legs, resolve_operator):
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
    for row in rows:
        parsed = parse_row(row)
        if not parsed:
            continue
        client, kind, agent = parsed
        if phones is not None and client not in phones:
            continue
        groups[(row.get("linkedid"), client)].append(_leg(row, kind, agent))

    touches = [_touch(linkedid, client, legs, resolve_operator)
               for (linkedid, client), legs in groups.items()]
    touches.sort(key=lambda touch: (touch["started_at"], touch["phone"]))
    return touches


def summarize(touches):
    """Сводка по списку касаний — то, что показывается карточками над таблицей."""
    talk = [t for t in touches if t["talk_seconds"] > 0]
    return {
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
