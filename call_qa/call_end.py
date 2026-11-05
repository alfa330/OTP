"""Кто завершил звонок — факт телефонии, а не вывод из расшифровки.

Оценщику он нужен для критериев о завершении разговора: по одной расшифровке не
видно, кто положил трубку, — оператор, не попрощавшись, или клиент посреди фразы.
Источник у каждой АТС свой (Oktell — StopSide/ReasonStop, Binotel — кабинет,
FreePBX — журнал очереди у входящих и CEL станции у исходящих, cdr/hangups.py) и
сведён к одному словарю imported_calls.call_end_party / calls.call_end_party.

Рядом со стороной модель видит и КОГДА положили трубку: кто из голосов прощался и
сколько секунд прошло от этого до отбоя (timeline), — и правило владельца: уточнить,
остались ли вопросы, оператор должен до своего прощания. Без этого один и тот же
звонок она то штрафовала, то нет, а «клиент бросил трубку, не дав оператору
ответить» не отличала от «оператор попрощался, не спросив».

Неизвестная сторона в модель не передаётся ВОВСЕ: тогда вход оценки остаётся
байт-в-байт прежним, и отпечаток уже сделанных оценок таких звонков не меняется.
Известная — отдельной строкой рядом с расшифровкой и в отпечатке оценки: пришла
новая вводная, значит это уже другая оценка."""
from __future__ import annotations

import re

from .evaluation.fingerprint import content_hash

CALL_END_PARTIES = frozenset({"operator", "client", "system", "transfer"})

# Как сторона называется для модели. Шкалы и вступление промпта говорят «клиент»,
# поэтому и здесь клиент, а не водитель.
_FOR_MODEL = {
    "operator": "оператор",
    "client": "клиент",
    "system": "станция (обрыв связи или сбой), не оператор и не клиент",
    "transfer": "звонок переведён на другого сотрудника",
}

# Правило владельца (05.11.2026): «оператор должен уточнить доп. вопросы до
# прощания». Попрощался, не уточнив, — ошибка, как бы быстро ни положили трубку.
# Не ошибка — только когда оператор не успел дойти до прощания: клиент прервал
# разговор или сам попрощался и положил трубку, не дав ответить. Порога в секундах
# нет: прежняя поблажка «клиент положил трубку быстрее 2 с после прощания оператора»
# владелец отменил — спрашивать надо было до прощания.
_ASK_BEFORE_GOODBYE = (
    "уточнить, остались ли у клиента вопросы, оператор должен до своего прощания — если "
    "это было нужно, а оператор попрощался, не уточнив, это ошибка, как бы быстро после "
    "этого ни положили трубку.")

# Отделы, где модель видит время до отбоя. «Конец записи = отбой» сверен только на
# записях FreePBX отдела продаж (05.11.2026), и правило владельца — про его шкалы.
# У Oktell (СЗоВ) и Binotel (Тез КЦ) запись может резаться иначе — им прежняя строка.
TIMELINE_DEPARTMENTS = frozenset({"op"})

# В скольких последних репликах искать прощание каждого голоса: в окно попадают
# прощание оператора, ответ клиента и короткие «Аха» после них. Длинная реплика
# обрезается с начала — модель видит её целиком в расшифровке, здесь нужен конец.
_TIMELINE_LINES = 6
_TIMELINE_CHARS = 90
# Конец записи раньше последнего слова больше чем на это — длительность не та,
# время до отбоя не считаем (25 из 879 звонков ОП на 05.11 — в пределах 0,5 с).
_DURATION_SLACK_MS = 500
# Прощание и закрывающие фразы — по-русски и по-казахски, как их пишет Soniox.
# «Спасибо»/«рахмет» сами по себе не прощание: их говорят и посреди разговора.
_GOODBYE_RE = re.compile(
    r"до свидан|всего добр|всего хорош|хорошего (?:дня|вечера)|удачи|до связи|всех благ"
    r"|за уделённое время|за уделенное время|за ваше время"
    r"|сау\s*бол|сау-сау|қош бол|көріскенше|сәттілік|уақыт бөлгеніңіз")


def normalise_call_end_party(*values) -> str:
    """Use the first known telephony value, including a linked import fallback."""
    for value in values:
        party = str(value or "").strip().lower()
        if party in CALL_END_PARTIES:
            return party
    return "unknown"


def _line_text(line: dict) -> str:
    text = line.get("text")
    if text is None:
        text = "".join(str(token.get("t") or token.get("text") or "")
                       for token in line.get("seg") or [] if isinstance(token, dict))
    return " ".join(str(text).split())


def _line_end(line: dict):
    for key in ("end_ms", "end_time_ms"):
        value = line.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _clip(text: str) -> str:
    return "…" + text[-_TIMELINE_CHARS:].lstrip() if len(text) > _TIMELINE_CHARS else text


def timeline(lines, duration_ms) -> list[dict] | None:
    """Конец звонка по голосам: для каждого голоса (метка «S…» из расшифровки) в
    последних репликах — его последнее прощание и последняя реплика, у каждой —
    сколько миллисекунд прошло от её конца до отбоя.

    По голосам, а не списком реплик: на пробном A/B 05.11.2026 модель из списка брала
    время последней реплики клиента («До свидания» за 0,6 с) вместо прощания оператора
    (за 2,6 с). Какой голос — оператор, код до оценки не знает (по самопредставлению —
    лишь у половины звонков ОП), это решает модель; число у голоса она не перепутает.

    Запись идёт от ответа до отбоя — сверка 05.11.2026 по ОП: длина записи совпала
    с разговором по АТС с точностью до секунды у 853 из 879 звонков, с метками
    AMI-коннектора — у 219 из 240. Значит, отбой — это конец записи (duration_ms,
    длительность от вендора). None — время не знаем: нет длительности, реплик или
    метки голоса у последних реплик; длительность взята по последнему слову (тогда
    тишины до отбоя не измерить) или короче последнего слова больше чем на полсекунды."""
    try:
        duration = int(duration_ms)
    except (TypeError, ValueError):
        return None
    spoken = [line for line in (lines or [])
              if isinstance(line, dict) and _line_text(line) and _line_end(line) is not None]
    if duration <= 0 or not spoken:
        return None
    last_end = max(_line_end(line) for line in spoken)
    if duration == last_end or duration < last_end - _DURATION_SLACK_MS:
        return None
    tail = spoken[-_TIMELINE_LINES:]
    if any(line.get("spk") in (None, "") for line in tail):
        return None
    voices: dict[str, dict] = {}
    for line in tail:
        text = _line_text(line)
        moment = {"text": _clip(text), "before_end_ms": max(0, duration - _line_end(line))}
        voice = voices.setdefault(str(line["spk"]), {"spk": str(line["spk"]), "goodbye": None})
        voice["last"] = moment
        if _GOODBYE_RE.search(text.lower()):
            voice["goodbye"] = moment
    return list(voices.values())


def _seconds(ms) -> str:
    return f"{int(ms) / 1000:.1f}".replace(".", ",") + " с"


def _voice_line(voice: dict) -> str:
    last, goodbye = voice["last"], voice.get("goodbye")
    if goodbye is None:
        return (f"— голос S{voice['spk']}: не прощался; последняя реплика («{last['text']}») "
                f"— за {_seconds(last['before_end_ms'])} до отбоя")
    said = (f"— голос S{voice['spk']}: попрощался («{goodbye['text']}») "
            f"за {_seconds(goodbye['before_end_ms'])} до отбоя")
    # По значению, а не по ссылке: манифест пакета хранит конец звонка в JSON.
    if goodbye != last:
        said += (f", после этого говорил ещё («{last['text']}») — "
                 f"за {_seconds(last['before_end_ms'])} до отбоя")
    return said


def prompt_block(party, end_timeline=None) -> str:
    """Строка для пользовательского сообщения оценки; '' — сторона неизвестна.

    end_timeline — конец звонка по голосам (timeline()); без него — одна сторона,
    как до 05.11.2026."""
    party = normalise_call_end_party(party)
    if party == "unknown":
        return ""
    if party not in ("operator", "client") or not end_timeline:
        return ("\n\nКТО ЗАВЕРШИЛ ЗВОНОК (данные телефонии, а не вывод из транскрипта): "
                f"{_FOR_MODEL[party]}. Учитывай это в критериях о завершении разговора.")
    voices = "\n".join(_voice_line(voice) for voice in end_timeline)
    if party == "client":
        rule = (f"{_ASK_BEFORE_GOODBYE} Не ошибка, только если оператор не успел дойти до "
                "прощания: клиент прервал разговор или сам попрощался и положил трубку, не "
                "дав оператору ответить.")
    else:
        rule = f"{_ASK_BEFORE_GOODBYE} Трубку положил сам оператор — клиент его не прерывал."
    return ("\n\nКТО ЗАВЕРШИЛ ЗВОНОК (данные телефонии и записи, а не вывод из транскрипта): "
            f"трубку положил {_FOR_MODEL[party]}.\n"
            f"Конец звонка по записи:\n{voices}\n"
            f"Как учитывать в критериях о завершении разговора: {rule}")


def identity(transcript_identity: str, party) -> str:
    """Транскрипт-компонент отпечатка с учётом стороны завершения. Неизвестная
    сторона компонент не меняет — прежние оценки таких звонков остаются свежими.

    Время до отбоя в отпечаток отдельно не входит: оно выводится из той же записи,
    что и транскрипт. Текст строки для модели — тоже: правка 05.11.2026 не пометила
    устаревшими все оценки звонков разом, прежние пересчитывает «Переоценить»."""
    party = normalise_call_end_party(party)
    if party == "unknown":
        return transcript_identity
    return content_hash({"transcript": transcript_identity, "call_end_party": party})
