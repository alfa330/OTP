# -*- coding: utf-8 -*-
"""Звонки базы удалённого КЦ как субъект ИИ-оценки (задача SZOV-2, 10.11.2026).

Решение владельца: звонки удалённого КЦ оцениваются «через направление»
«Обзвон — подписание АВР» отдела СЗоВ — все звонки его базы, кто бы ни звонил:
фронт-офисы (в октябре 55 % звонков), удалённые операторы, СЗоВ. Поэтому у
строки imported_calls две пометки одним направлением:

  * qa_direction_id — шкала оценки (как у звонков обзвона Oktell с 22.10);
  * scope_direction_id — чей это разговор: отдел, видимость и раздел сводки
    считаются по нему, а не по направлению звонившего (у фронт-офиса своего
    отдела в «ИИ-оценке» нет, и звонок пропал бы из всех списков).

Звонки берутся из журнала раздела «Удаленный КЦ» (dial_list_attempts) —
там точно известен оператор попытки, длительность, итог и кто положил трубку,
а запись лежит у Binotel кабинета удалённого КЦ по generalCallID. Ночная
выборка (call_qa.daily_sample) берёт отсюда 30 случайных разговоров в день
и СВЕРХ них — все звонки под триггерами фрода (call_qa.fraud; владелец:
«Да, сверх 30 случайных»).

Скачивание записи и строка пула — в bot_schedule2 (там хелперы GCS и Binotel
раздела), сюда они приходят функциями: модуль не тянет монолит.
"""
from __future__ import annotations

import logging
from datetime import date

from . import config
from . import fraud

# Пометка импорта (imported_calls.notes). Хвост «remote_cc» по ней узнают докачка
# записи (_ensure_imported_call_audio) и маршрут звонка в карточке (call_route).
NOTES = "aiqa:auto:remote_cc"
NOTES_TAIL = "remote_cc"


def enabled() -> bool:
    return bool(config.REMOTE_CC_QA_DIRECTION_ID)


def _dt_raw(started_at) -> str:
    """Время звонка для imported_calls.datetime_raw — часы Алматы, как у Binotel и
    Oktell выборки (по ним «Звонки» раскладывают разговор на день)."""
    return started_at.astimezone(fraud.ALMATY).strftime("%d.%m.%Y %H:%M:%S")


def _candidate(call: dict, department_id: int, reasons=None) -> dict:
    return {"direction_id": config.REMOTE_CC_QA_DIRECTION_ID,
            "operator_id": call["operator_id"], "operator_name": call["operator"],
            "key": call["general_call_id"], "attempt_id": call["id"],
            "phone": call["phone"], "billsec": call["billsec"],
            "dt_raw": _dt_raw(call["started_at"]),
            "call_end_party": call["end_party"], "department_id": department_id,
            "reasons": list(reasons or [])}


def _load(day: date):
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute("SET client_encoding TO 'UTF8'")
            department_id = fraud.remote_cc_department_id(cur)
            if department_id is None:
                return None, []
            return department_id, fraud.load_day(cur, day, department_id)
    finally:
        conn.close()


def _durations():
    min_d = config.AI_QA_DAILY_SAMPLE_MIN_DURATION_S
    max_d = config.AI_QA_DAILY_SAMPLE_MAX_DURATION_S
    if max_d and min_d and max_d < min_d:
        min_d, max_d = max_d, min_d
    return min_d, max_d


def candidates(day: date) -> list[dict]:
    """Разговоры дня с записью — кандидаты случайной выборки направления."""
    if not enabled():
        return []
    department_id, calls = _load(day)
    min_d, max_d = _durations()
    return [_candidate(call, department_id) for call in calls
            if call["general_call_id"]
            and not (min_d and call["billsec"] < min_d)
            and not (max_d and call["billsec"] > max_d)]


def triggered(day: date) -> list[dict]:
    """Звонки дня под триггерами фрода — на прослушку сверх случайной выборки, по
    важности шаблона (fraud.flagged_calls). Без записи (нет generalCallID) не берутся."""
    if not enabled():
        return []
    department_id, calls = _load(day)
    by_id = {call["id"]: call for call in calls}
    out = []
    for attempt_id, reasons in fraud.flagged_calls(fraud.find(calls)):
        call = by_id.get(attempt_id)
        if call and call["general_call_id"]:
            out.append(_candidate(call, department_id, reasons))
    return out


def import_candidate(candidate: dict, *, fetch_record, import_call, status: str):
    """Звонок → строка imported_calls с записью. Запись качается ДО строки: звонок без
    записи выборке не нужен (как у Binotel Тез и Oktell). None — взять нельзя."""
    month = candidate["dt_raw"][6:10] + "-" + candidate["dt_raw"][3:5]
    audio_path = fetch_record(candidate["department_id"], candidate["key"])
    if not audio_path:
        logging.info("ai-qa удалённый КЦ: у звонка %s нет записи", candidate["key"])
        return None
    direction_id = candidate["direction_id"]
    return import_call(
        operator_id=candidate["operator_id"], operator_name=candidate["operator_name"],
        external_id=str(candidate["key"]), month=month, datetime_raw=candidate["dt_raw"],
        phone=candidate["phone"], duration_sec=candidate["billsec"], notes=NOTES,
        audio_path=audio_path, call_end_party=candidate.get("call_end_party") or "unknown",
        status=status, qa_direction_id=direction_id, scope_direction_id=direction_id)


__all__ = ["NOTES", "NOTES_TAIL", "enabled", "candidates", "triggered", "import_candidate"]
