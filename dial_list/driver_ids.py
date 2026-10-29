# -*- coding: utf-8 -*-
"""ID водителя у базы «Удаленного КЦ» — Yandex Driver Id из Sapar (владелец, 29.10.2026: «в детализацию
и в отчеты добавить id и иин водителя»).

Откуда. Точечная ручка Sapar, которой идёт проверка подписания (by-iin), ID не отдаёт — отдаёт массовая,
статусы подписания всего парка (crm/sapar.py::driver_yandex_ids). Замер 29.10.2026: 62 884 водителя, ~8 МБ,
2–21 с, список один и тот же за любой месяц; у каждого хотя бы один ID — тот же «Yandex Driver Id», что в
выгрузке услуг iTaxi, — у 6 % несколько (аккаунты в разных парках). Базу октября Sapar знает на 98 %
(23 312 из 23 776): остальных в массовом списке нет вовсе, хотя by-iin находит их документы, — у них ID
пустой.

Хранение — dial_list_leads.yandex_driver_ids: NULL — Sapar ещё не спрашивали, '' — спросили, а он водителя
не знает, иначе ID через запятую по алфавиту (порядок у Sapar не обещан: без сортировки каждая сверка
переписывала бы те же строки).

Когда. Из прогона проверки подписания (SignChecker.run — раз в полчаса и сразу после загрузки базы), в его
же потоке: один запрос к Sapar за раз. Есть в базах текущего и прошлого месяца водители с ИИН, о которых
Sapar ещё не спрашивали (новая загрузка, первый прогон после выкладки, сменился ИИН), — один запрос списка, и
сверяются заодно все водители этих баз: список приходит целиком, стоимость одна — что для одного нового
водителя, что для всех. Спрашивать некого — один короткий запрос к базе. Sapar перестал знать водителя —
прежний ID не стираем: это всё ещё его ID.

Сеть — вне транзакции, запись — порциями своих транзакций: строки водителей нужны и звонкам, держать их
замком всю сверку незачем.
"""
import logging

from psycopg2.extras import execute_values

from . import signing

log = logging.getLogger(__name__)

SEPARATOR = ", "
WRITE_CHUNK = 500

_DUE_SQL = """
    SELECT EXISTS (SELECT 1 FROM dial_list_leads
                   WHERE period BETWEEN %(from_period)s AND %(to_period)s
                     AND iin <> '' AND yandex_driver_ids IS NULL)
"""
_LEADS_SQL = """
    SELECT id, iin, yandex_driver_ids FROM dial_list_leads
    WHERE period BETWEEN %(from_period)s AND %(to_period)s AND iin <> ''
"""
# ИИН — в условии: загрузка базы между чтением и записью могла сменить водителю ИИН, и ID был бы чужой.
_WRITE_SQL = """
    UPDATE dial_list_leads l SET yandex_driver_ids = v.ids
    FROM (VALUES %s) AS v(id, iin, ids)
    WHERE l.id = v.id AND l.iin = v.iin AND l.yandex_driver_ids IS DISTINCT FROM v.ids
    RETURNING l.id
"""


def ids_text(ids):
    """ID из Sapar → значение колонки: без пустых и повторов, по алфавиту, через запятую."""
    return SEPARATOR.join(sorted({str(i).strip() for i in ids or () if str(i or "").strip()}))


def changes(leads, known):
    """Что записать. leads — [(id, ИИН, значение колонки)], known — {ИИН: [ID, …]} из Sapar →
    [(id, ИИН, новое значение)]. Водителя нет в списке: не спрашивали (NULL) — '', был ID — не трогаем."""
    out = []
    for lead_id, iin, current in leads:
        found = known.get(iin)
        if found:
            value = ids_text(found)
        elif current is None:
            value = ""
        else:
            continue
        if value != current:
            out.append((str(lead_id), iin, value))
    return out


def sync(db, fetch, today, force=False):
    """Сверить ID водителей баз текущего и прошлого месяца (signing.check_window) со списком Sapar.

    fetch(month, year, wanted=…) — crm.sapar.driver_yandex_ids (в тестах подменяется); force — не глядя,
    есть ли неспрошенные. → None — спрашивать некого, иначе сводка {ok, leads, known, unknown, updated,
    error}: known/unknown — водителей баз, которых Sapar знает / не знает; updated — записанных строк."""
    from_period, to_period = signing.check_window(today)
    params = {"from_period": from_period, "to_period": to_period}
    with db._get_cursor() as cur:
        if not force:
            cur.execute(_DUE_SQL, params)
            if not cur.fetchone()[0]:
                return None
        cur.execute(_LEADS_SQL, params)
        leads = cur.fetchall()
    if not leads:
        return None
    month, year = signing.doc_month_for(to_period)
    result = fetch(month, year, wanted={iin for _id, iin, _value in leads})
    summary = {"ok": bool(result.get("ok")), "leads": len(leads), "known": 0, "unknown": 0, "updated": 0,
               "error": "" if result.get("ok") else str(result.get("error") or "Sapar не ответил")[:300]}
    if not summary["ok"]:
        log.warning("dial_list: ID водителей из Sapar не получены (водителей в базах %d): %s",
                    len(leads), summary["error"])
        return summary
    known = result.get("ids") or {}
    summary["known"] = sum(1 for _id, iin, _value in leads if known.get(iin))
    summary["unknown"] = len(leads) - summary["known"]
    pending = changes(leads, known)
    for start in range(0, len(pending), WRITE_CHUNK):
        with db._get_cursor() as cur:
            written = execute_values(cur, _WRITE_SQL, pending[start:start + WRITE_CHUNK],
                                     template="(%s::uuid, %s, %s)", page_size=WRITE_CHUNK, fetch=True)
        summary["updated"] += len(written)
    log.info("dial_list: ID водителей из Sapar: водителей в базах %d, Sapar знает %d, не знает %d, записано %d",
             summary["leads"], summary["known"], summary["unknown"], summary["updated"])
    return summary
