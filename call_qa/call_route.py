"""Маршрут звонка для карточки ИИ-оценки: входящий он или исходящий, номер клиента
и наш номер — тот, что набрал клиент, или тот, с которого звонил оператор.

Только для показа, как таксопарк (taxi_parks.py): в промпт и отпечаток оценки не
входит, читается после проверки доступа и на один разговор — в списках его нет.
Источник — та телефония, из которой звонок пришёл в раздел:

* отдел продаж — касание станции (cdr_touches): тип, номер клиента и линия есть
  у обоих направлений;
* СЗоВ — соединение Oktell, та же строка и тот же кэш, что у таксопарка: у
  входящего клиент — A-сторона, наш номер — набранный (ANumberDialed); у
  исходящего клиент — B-сторона, а на A-стороне внутренний номер оператора,
  поэтому нашего номера нет;
* Тез КЦ — зеркало Binotel (tez_lead_calls): только направление, линию зеркало
  не хранит.

Неизвестное не выдумывается: нет данных — поле None, и карточка его не рисует.
"""
import logging
import re

from binotel.client import CALL_TYPE_INCOMING, CALL_TYPE_OUTGOING
from cdr import touches as cdr_touches

from . import config
from .taxi_parks import _conn_id, oktell_connections

IN, OUT = "in", "out"

_BASE_SQL = {
    # Номер из АТС точнее набранного руками в журнале — он и показывается.
    config.SUBJECT_CALL: """SELECT COALESCE(NULLIF(btrim(ic.phone_number), ''), c.phone_number),
                                   ic.external_id, ic.notes
                              FROM calls c
                              LEFT JOIN imported_calls ic ON ic.id = c.imported_call_id
                             WHERE c.id = %s""",
    config.SUBJECT_IMPORTED_CALL: """SELECT ic.phone_number, ic.external_id, ic.notes
                                       FROM imported_calls ic WHERE ic.id = %s""",
}

_OKTELL_INCOMING, _OKTELL_OUTGOING = 5, 1   # A_Stat_Connections_1x1.ConnectionType


def phone(value):
    """Номер цифрами: казахстанский — десять цифр без кода страны (так его хранят
    касания и форматирует фронт), чужой — как есть. Не номер — None."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 11 and digits[0] in "78":
        return digits[1:]
    return digits if len(digits) >= 5 else None


def source_of(notes, external_id):
    """Телефония звонка: хвост пометки импорта (…:cdr / …:oktell / …:binotel), у
    старых строк без хвоста — вид идентификатора: UUID соединения Oktell, linkedid
    станции «секунды.номер», голые цифры — generalCallID Binotel."""
    tail = str(notes or "").rsplit(":", 1)[-1]
    if tail in ("cdr", "oktell", "binotel"):
        return tail
    ext = str(external_id or "").strip()
    if not ext:
        return None
    if _conn_id(ext):
        return "oktell"
    if re.fullmatch(r"\d+\.\d+", ext):
        return "cdr"
    return "binotel" if ext.isdigit() else None


def _cdr_route(cur, linkedid, client):
    cur.execute("SELECT call_type, phone, line_number FROM cdr_touches WHERE linkedid = %s",
                (str(linkedid),))
    touches = cur.fetchall()
    # У перевода касаний несколько (по номеру на плечо) — берём то, что с клиентом
    # этого звонка; единственное касание годится и без сверки номера.
    own = [t for t in touches if client and phone(t[1]) == client]
    touch = own[0] if own else (touches[0] if len(touches) == 1 else None)
    if not touch:
        return {}
    call_type = str(touch[0] or "")
    direction = (OUT if call_type == cdr_touches.TYPE_OUT
                 else IN if call_type.startswith(cdr_touches.TYPE_IN) else None)
    return {"direction": direction, "client": phone(touch[1]) or client,
            "line": phone(touch[2])}


def _oktell_route(conn_id, client, oktell_query):
    if oktell_query is None:
        return {}
    facts = oktell_connections([conn_id], oktell_query).get(conn_id) or {}
    try:
        kind = int(facts.get("connection_type"))
    except (TypeError, ValueError):
        return {}
    if kind == _OKTELL_INCOMING:
        return {"direction": IN, "client": phone(facts.get("a_out")) or client,
                "line": phone(facts.get("a_dialed"))}
    if kind == _OKTELL_OUTGOING:
        return {"direction": OUT, "client": phone(facts.get("b_out")) or client, "line": None}
    return {}


def _binotel_route(cur, general_call_id):
    cur.execute("SELECT call_type FROM tez_lead_calls WHERE general_call_id = %s",
                (str(general_call_id),))
    row = cur.fetchone()
    if not row:
        return {}
    return {"direction": {CALL_TYPE_INCOMING: IN, CALL_TYPE_OUTGOING: OUT}.get(row[0])}


def load(subject_kind, call_id, *, oktell_query=None):
    """{direction, client, line} звонка или None, если о нём не известно ничего.
    Ошибка источника карточку не ломает: остаётся то, что успели узнать."""
    sql = _BASE_SQL.get(subject_kind)
    if not sql:
        return None
    route = {"direction": None, "client": None, "line": None}
    found, oktell_conn = {}, None
    conn = None
    try:
        conn = config.connect_ro()
        cur = conn.cursor()
        try:
            cur.execute(sql, (int(call_id),))
            row = cur.fetchone()
            if not row:
                return None
            raw_client, external_id, notes = row
            route["client"] = phone(raw_client)
            source = source_of(notes, external_id) if external_id else None
            if source == "cdr":
                found = _cdr_route(cur, external_id, route["client"])
            elif source == "binotel":
                found = _binotel_route(cur, external_id)
            elif source == "oktell":
                oktell_conn = _conn_id(external_id)
        finally:
            cur.close()
    except Exception:
        logging.warning("ai-qa: маршрут звонка %s %s не прочитан", subject_kind, call_id,
                        exc_info=True)
    finally:
        if conn is not None:
            conn.close()
    # К станции — уже без открытого соединения с базой.
    if oktell_conn:
        try:
            found = _oktell_route(oktell_conn, route["client"], oktell_query)
        except Exception:
            logging.warning("ai-qa: маршрут звонка Oktell %s не прочитан", oktell_conn,
                            exc_info=True)
    route.update({key: value for key, value in found.items() if value})
    return route if any(route.values()) else None
