"""Таксопарк разговора для UI, поверх готовых оценок и после проверки доступа.

Не участвует в промпте/отпечатке оценки. Читаем только переданные субъекты,
пакетом на страницу; номер водителя сам по себе не определяет его таксопарк.
"""
from collections import OrderedDict, defaultdict
from datetime import timedelta
import logging
import re
from threading import Lock
from time import monotonic
from uuid import UUID

from . import config
from cdr import lines, queries


_SOURCES = {
    "call": """SELECT c.id, dep.code, ic.external_id,
                       COALESCE(NULLIF(btrim(c.phone_number), ''), ic.phone_number),
                       s.channel_name, s.wz_channel_id
                  FROM calls c
                  LEFT JOIN imported_calls ic ON ic.id = c.imported_call_id
                  LEFT JOIN c2d_chat_snapshots s ON s.id = c.c2d_snapshot_id
                  LEFT JOIN directions d ON d.id = c.direction_id
                  LEFT JOIN departments dep ON dep.id = d.department_id
                 WHERE c.id = ANY(%s)""",
    "imported_call": """SELECT ic.id, dep.code, ic.external_id, ic.phone_number,
                                NULL, NULL
                           FROM imported_calls ic
                           LEFT JOIN users u ON u.id = ic.operator_id
                           LEFT JOIN directions d ON d.id = u.direction_id
                           LEFT JOIN departments dep
                             ON dep.id = COALESCE(d.department_id, u.department_id)
                          WHERE ic.id = ANY(%s)""",
    "wz_episode": """SELECT e.id, dep.code, NULL, e.contact_phone, NULL, e.channel_id
                        FROM wazzup_episodes e
                        LEFT JOIN users u ON u.id = e.operator_user_id
                        LEFT JOIN directions d ON d.id = u.direction_id
                        LEFT JOIN departments dep ON dep.id = d.department_id
                       WHERE e.id = ANY(%s)""",
    "c2d_snapshot": """SELECT s.id, dep.code, NULL, s.client_phone, s.channel_name, NULL
                          FROM c2d_chat_snapshots s
                          LEFT JOIN users u ON u.id = s.operator_id
                          LEFT JOIN directions d ON d.id = u.direction_id
                          LEFT JOIN departments dep
                            ON dep.id = COALESCE(d.department_id, u.department_id)
                         WHERE s.id = ANY(%s) AND s.source = 'chat2desk'""",
}

_oktell_cache = OrderedDict()
_cache_lock = Lock()


def _text(value):
    return str(value or "").strip()


def _phone(value):
    return re.sub(r"\D", "", _text(value))[-10:]


def _conn_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def oktell_parks_sql(conn_ids):
    # Прокси принимает SQL без параметров: в литералы идут ТОЛЬКО UUID.
    ids = sorted({value for raw in conn_ids if (value := _conn_id(raw))})
    if not ids:
        return None
    literals = ", ".join(f"'{value}'" for value in ids)
    return (
        "SELECT LOWER(CONVERT(varchar(36), s.Id)) AS conn_id, p.taxi_park "
        "FROM oktell.dbo.A_Stat_Connections_1x1 s "
        "OUTER APPLY (SELECT TOP 1 t.taxi_park FROM oktell.dbo.Call_Systems_hst t "
        "WHERE t.chainid = CONVERT(varchar(36), s.IdChain) "
        "AND NULLIF(LTRIM(RTRIM(t.taxi_park)), '') IS NOT NULL "
        "ORDER BY t.dt_insert DESC, t.Id DESC) p "
        f"WHERE s.Id IN ({literals})"
    )


def _oktell_parks(conn_ids, query):
    result, missing = {}, []
    now = monotonic()
    with _cache_lock:
        for conn_id in sorted(set(conn_ids)):
            cached = _oktell_cache.get(conn_id)
            if cached and cached[0] > now:
                result[conn_id] = cached[1]
                _oktell_cache.move_to_end(conn_id)
            else:
                missing.append(conn_id)
    if missing:
        try:
            found = {_conn_id(row.get("conn_id")): _text(row.get("taxi_park"))
                     for row in query(oktell_parks_sql(missing), timeout=5)}
        except Exception:
            logging.warning("ai-qa: таксопарки Oktell недоступны", exc_info=True)
            found = {}
        with _cache_lock:
            for conn_id in missing:
                park = found.get(conn_id) or None
                result[conn_id] = park
                _oktell_cache[conn_id] = (now + (600 if park else 60), park)
                _oktell_cache.move_to_end(conn_id)
            while len(_oktell_cache) > 4096:
                _oktell_cache.popitem(last=False)
    return result


def _cdr_parks(cur, linkedids):
    cur.execute("""SELECT linkedid, phone, call_type, queue, line_number
                     FROM cdr_touches WHERE linkedid = ANY(%s)""", (sorted(linkedids),))
    touches = [dict(zip(("linkedid", "phone", "call_type", "queue", "line_number"), row))
               for row in cur.fetchall()]
    # Те же правила, что в «Касаниях»: у исходящего парк по линии, а не
    # по очереди кампании автообзвона. Справочник строим только при необходимости.
    needs_lines = any(t["line_number"] and
                      (t["call_type"] == "Исходящий" or not t["queue"]) for t in touches)
    known = lines.line_queues(queries.line_queue_rows(
        cur, queries.today_almaty() - timedelta(days=60))) if needs_lines else {}
    return {(t["linkedid"], _phone(t["phone"])): lines.park(t, known) for t in touches}


def attach_taxi_parks(items, *, department=None, oktell_query, wazzup_channels,
                     park_label=lambda value: value):
    """Добавляет taxi_park (имя или None) у СЗоВ/ОП. Остальные отделы не меняет.

    items уже отобраны проверкой прав маршрута. Ошибка справочника не должна
    прятать оценку; None означает «источник не сообщил парк», без догадок по CRM.
    """
    if not items or department not in (None, "op", "szov"):
        return
    grouped = defaultdict(list)
    for item in items:
        if department in ("op", "szov"):
            item["taxi_park"] = None
        kind = item.get("subject_kind") or item.get("subject") or "call"
        if kind in _SOURCES:
            grouped[kind].append(item)

    facts = []
    cdr_items = [(item, _text(item.get("linkedid") or item["id"]), _phone(item.get("phone")))
                 for item in items if item.get("subject") == "cdr_touch"]
    conn = None
    try:
        conn = config.connect_ro()
        cur = conn.cursor()
        try:
            for kind, rows in grouped.items():
                cur.execute(_SOURCES[kind], (sorted({int(item["id"]) for item in rows}),))
                by_id = {row[0]: row for row in cur.fetchall()}
                for item in rows:
                    fact = by_id.get(int(item["id"]))
                    if fact and config.normalise_department_code(fact[1]) in ("op", "szov"):
                        item["taxi_park"] = _text(fact[4]) or None
                        facts.append((item, fact))
                        if fact[2] and not _conn_id(fact[2]):
                            cdr_items.append((item, _text(fact[2]), _phone(fact[3])))
            if cdr_items:
                parks = _cdr_parks(cur, {linkedid for _, linkedid, _ in cdr_items})
                for item, linkedid, phone in cdr_items:
                    item["taxi_park"] = parks.get((linkedid, phone)) or item.get("taxi_park")
        finally:
            cur.close()
    except Exception:
        logging.warning("ai-qa: источники таксопарков недоступны", exc_info=True)
    finally:
        if conn is not None:
            conn.close()

    conn_ids = [conn_id for _, fact in facts if (conn_id := _conn_id(fact[2]))]
    if conn_ids:
        parks = _oktell_parks(conn_ids, oktell_query)
        for item, fact in facts:
            park = parks.get(_conn_id(fact[2]))
            if park:
                item["taxi_park"] = park_label(park)
    if any(fact[5] and not item.get("taxi_park") for item, fact in facts):
        try:
            channels = {_text(ch.get("channelId")): _text(ch.get("name"))
                        for ch in wazzup_channels()}
            for item, fact in facts:
                if fact[5] and not item.get("taxi_park"):
                    item["taxi_park"] = channels.get(_text(fact[5])) or None
        except Exception:
            logging.warning("ai-qa: названия каналов Wazzup недоступны", exc_info=True)
