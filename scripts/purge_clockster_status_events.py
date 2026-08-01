"""Чистка исторических статусов ОП, импортированных из Clockster.

Интеграция Clockster удалена: новых отметок не будет, но в БД остались события
`operator_status_events` с `state_note` clockster / clockster-manual /
clockster-auto, из которых собраны сегменты и посчитаны часы прошлых дней.
Скрипт удаляет эти события и пересобирает сегменты + авто-часы ровно тем же
кодом, что и приложение (`_rebuild_operator_status_segments_tx` →
`_recalculate_auto_daily_hours_tx`), чтобы результат совпал с прод-путём.

ВАЖНО — почему нельзя чистить выборочно: удалить только `clockster-auto`
(синтетические уходы) значит оставить «приход» без пары. Статус «готов» —
рабочий (SCHEDULE_AUTO_WORK_STATUS_KEYS), и при пересборке он потечёт в
следующие сутки, надув часы. Поэтому удаляются ВСЕ `state_note LIKE
'clockster%'` за период, либо не удаляется ничего.

Границы применимости: сырые события старше STATUS_EVENTS_RETENTION_DAYS (по
умолчанию 120) уже вычищены ночным purge, а пересборка сегментов ниже этой
границы намеренно отключена — сегменты там durable-источник отчётов, заново их
не вывести. Такие дни скрипт только показывает, не трогая (см. вывод
`below_retention`).

Подключение — как у приложения: POSTGRES_DB/USER/PASSWORD/HOST/PORT из
окружения или .env.codex.local. Запускать на Linux (Render shell): database.py
на импорте делает `time.tzset()` и создаёт пул — на Windows импорт падает.

Запуск:
    python scripts/purge_clockster_status_events.py                  # только отчёт (dry-run)
    python scripts/purge_clockster_status_events.py --from 2026-06-01 --to 2026-07-31
    python scripts/purge_clockster_status_events.py --apply          # запись в БД
    python scripts/purge_clockster_status_events.py --apply --all-operators

По умолчанию затрагиваются только сотрудники отдела продаж (code='op') —
там и жил Clockster. `--all-operators` снимает это ограничение (например, если
кого-то уже перевели в другой отдел).
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CLOCKSTER_NOTE_PREFIX = 'clockster'
OP_DEPARTMENT_CODE = 'op'


def _load_env(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(os.path.join(ROOT, ".env.codex.local"))

import database as db_module  # noqa: E402


def _connect():
    """Тот же singleton, что использует приложение.

    `import database` сам создаёт `db = Database()` и прогоняет init_database —
    ровно как на старте приложения (идемпотентно), отдельного подключения не
    требуется.
    """
    return db_module.db


def _parse_date(value):
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _target_operator_ids(db, all_operators):
    if all_operators:
        return None
    with db._get_cursor() as cursor:
        cursor.execute(
            "SELECT id FROM departments WHERE LOWER(TRIM(code)) = %s",
            (OP_DEPARTMENT_CODE,)
        )
        row = cursor.fetchone()
        if not row:
            raise SystemExit("Отдел продаж (code='op') не найден — уточните --all-operators")
        cursor.execute("SELECT id FROM users WHERE department_id = %s", (int(row[0]),))
        ids = sorted({int(r[0]) for r in cursor.fetchall()})
    if not ids:
        raise SystemExit("В отделе продаж нет пользователей")
    return ids


def _scan(db, operator_ids, date_from, date_to):
    """Что лежит в базе: события по типу заметки, операторы, дни."""
    where = ["state_note LIKE %s"]
    params = [CLOCKSTER_NOTE_PREFIX + '%']
    if operator_ids is not None:
        where.append("operator_id = ANY(%s)")
        params.append(operator_ids)
    if date_from:
        where.append("event_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("event_date <= %s")
        params.append(date_to)
    clause = " AND ".join(where)

    with db._get_cursor() as cursor:
        cursor.execute(
            "SELECT state_note, COUNT(*) FROM operator_status_events "
            "WHERE " + clause + " GROUP BY state_note ORDER BY state_note",
            params
        )
        by_note = [(str(r[0] or ''), int(r[1])) for r in cursor.fetchall()]

        cursor.execute(
            "SELECT MIN(event_date), MAX(event_date), COUNT(DISTINCT operator_id), "
            "COUNT(DISTINCT event_date) FROM operator_status_events WHERE " + clause,
            params
        )
        min_date, max_date, operators_count, days_count = cursor.fetchone()

        cursor.execute(
            "SELECT DISTINCT operator_id FROM operator_status_events WHERE " + clause
            + " ORDER BY operator_id",
            params
        )
        affected_ids = [int(r[0]) for r in cursor.fetchall()]

    return {
        'by_note': by_note,
        'total': sum(count for _, count in by_note),
        'min_date': min_date,
        'max_date': max_date,
        'operators': int(operators_count or 0),
        'days': int(days_count or 0),
        'operator_ids': affected_ids,
    }


def _apply(db, operator_ids, date_from, date_to):
    """Удаление + пересборка сегментов + пересчёт часов одной транзакцией."""
    where = ["state_note LIKE %s", "operator_id = ANY(%s)"]
    params = [CLOCKSTER_NOTE_PREFIX + '%', operator_ids]
    if date_from:
        where.append("event_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("event_date <= %s")
        params.append(date_to)

    with db._get_cursor() as cursor:
        cursor.execute(
            "DELETE FROM operator_status_events WHERE " + " AND ".join(where),
            params
        )
        deleted = cursor.rowcount
        # Пересборка с запасом в сутки: сегмент мог начаться в предыдущий день.
        rebuild = db._rebuild_operator_status_segments_tx(
            cursor, operator_ids, date_from - timedelta(days=1), date_to + timedelta(days=1)
        )
        recalc = db._recalculate_auto_daily_hours_tx(
            cursor, operator_ids, date_from - timedelta(days=1), date_to + timedelta(days=1)
        )
    return {'deleted_events': int(deleted or 0), 'rebuild': rebuild, 'recalc': recalc}


def main():
    parser = argparse.ArgumentParser(description="Чистка clockster-статусов ОП")
    parser.add_argument("--from", dest="date_from", help="YYYY-MM-DD (по умолчанию — самая ранняя запись)")
    parser.add_argument("--to", dest="date_to", help="YYYY-MM-DD (по умолчанию — самая поздняя запись)")
    parser.add_argument("--all-operators", action="store_true",
                        help="не ограничивать отделом продаж")
    parser.add_argument("--apply", action="store_true",
                        help="реально писать в БД (без флага — только отчёт)")
    args = parser.parse_args()

    date_from = _parse_date(args.date_from) if args.date_from else None
    date_to = _parse_date(args.date_to) if args.date_to else None
    if date_from and date_to and date_to < date_from:
        date_from, date_to = date_to, date_from

    db = _connect()
    operator_ids = _target_operator_ids(db, args.all_operators)
    scan = _scan(db, operator_ids, date_from, date_to)

    print("Найдено clockster-событий: %d" % scan['total'])
    for note, count in scan['by_note']:
        print("  %-18s %6d" % (note, count))
    if not scan['total']:
        print("Чистить нечего.")
        return 0
    print("Период: %s .. %s (дней с событиями: %d), операторов: %d"
          % (scan['min_date'], scan['max_date'], scan['days'], scan['operators']))

    retention_floor = datetime.now().date() - timedelta(days=db_module.STATUS_EVENTS_RETENTION_DAYS)
    effective_from = date_from or scan['min_date']
    effective_to = date_to or scan['max_date']
    if effective_from < retention_floor:
        print("below_retention: дни до %s пересобрать нельзя (граница retention %d дней) — "
              "их сегменты и часы останутся как есть"
              % (retention_floor.strftime('%Y-%m-%d'), db_module.STATUS_EVENTS_RETENTION_DAYS))

    if not args.apply:
        print("\nDRY-RUN: ничего не изменено. Для записи повторите с --apply")
        return 0

    result = _apply(db, scan['operator_ids'], effective_from, effective_to)
    print("\nУдалено событий: %d" % result['deleted_events'])
    print("Пересобрано сегментов: %s (удалено %s)"
          % (result['rebuild'].get('segments_saved'), result['rebuild'].get('deleted_segments')))
    if result['rebuild'].get('skipped_below_retention'):
        print("ВНИМАНИЕ: пересборка пропущена — весь период ниже границы retention")
    print("Пересчёт часов: %s" % (result['recalc'],))
    return 0


if __name__ == "__main__":
    sys.exit(main())
