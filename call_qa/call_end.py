"""Кто завершил звонок — факт телефонии, а не вывод из расшифровки.

Оценщику он нужен для критериев о завершении разговора: по одной расшифровке не
видно, кто положил трубку, — оператор, не попрощавшись, или клиент посреди фразы.
Источник у каждой АТС свой (Oktell — StopSide/ReasonStop, Binotel — кабинет,
FreePBX — журнал очереди, только у входящих) и сведён к одному словарю
imported_calls.call_end_party / calls.call_end_party.

Неизвестная сторона в модель не передаётся ВОВСЕ: тогда вход оценки остаётся
байт-в-байт прежним, и отпечаток уже сделанных оценок таких звонков не меняется.
Известная — отдельной строкой рядом с расшифровкой и в отпечатке оценки: пришла
новая вводная, значит это уже другая оценка."""
from __future__ import annotations

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


def normalise_call_end_party(*values) -> str:
    """Use the first known telephony value, including a linked import fallback."""
    for value in values:
        party = str(value or "").strip().lower()
        if party in CALL_END_PARTIES:
            return party
    return "unknown"


def prompt_block(party) -> str:
    """Строка для пользовательского сообщения оценки; '' — сторона неизвестна."""
    party = normalise_call_end_party(party)
    if party == "unknown":
        return ""
    return ("\n\nКТО ЗАВЕРШИЛ ЗВОНОК (данные телефонии, а не вывод из транскрипта): "
            f"{_FOR_MODEL[party]}. Учитывай это в критериях о завершении разговора.")


def identity(transcript_identity: str, party) -> str:
    """Транскрипт-компонент отпечатка с учётом стороны завершения. Неизвестная
    сторона компонент не меняет — прежние оценки таких звонков остаются свежими."""
    party = normalise_call_end_party(party)
    if party == "unknown":
        return transcript_identity
    return content_hash({"transcript": transcript_identity, "call_end_party": party})
