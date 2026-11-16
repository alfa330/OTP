# -*- coding: utf-8 -*-
"""Журнал сторожа линий в базе: весь SQL пакета живёт здесь.

Сервис (service.py) с базой напрямую не говорит — ему хватает этих восьми действий, поэтому
правила сверки проверяются без Postgres, а SQL — отдельным тестом на живой схеме.
"""
import json
from datetime import datetime, timezone

from . import schema


def _utc(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc)


class Journal(object):
    def __init__(self, db):
        self.db = db

    def _cursor(self):
        return self.db._get_cursor()

    def ready(self):
        """Таблица журнала создана (её разворачивает старт приложения)."""
        with self._cursor() as cursor:
            return schema.schema_is_ready(cursor)

    def load_open(self):
        """Незакрытые расхождения состояния — их продолжает новый процесс:
        [(id, линия, вид, когда чинили (unix-секунды или None), итог починки или None)].
        Момент и итог прошлой починки нужны, чтобы перезапуск не начинал попытки заново."""
        with self._cursor() as cursor:
            cursor.execute("""
                SELECT id, line, kind, EXTRACT(EPOCH FROM heal_at), heal_result
                FROM binotel_line_watch
                WHERE ended_at IS NULL AND general_call_id IS NULL
            """)
            return [(row[0], str(row[1]), str(row[2]),
                     float(row[3]) if row[3] is not None else None, row[4])
                    for row in cursor.fetchall()]

    def open(self, kind, line, snap, details, started, now):
        """Открыть расхождение; если такое у линии уже открыто — продолжить его. Возвращает id."""
        with self._cursor() as cursor:
            cursor.execute("""
                INSERT INTO binotel_line_watch
                    (kind, line, operator_id, direction, started_at, last_seen_at,
                     phone_status, presence, sip_status, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (line, kind) WHERE ended_at IS NULL
                DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at
                RETURNING id
            """, (kind, line, snap.get('operator_id'), snap.get('direction'), _utc(started), _utc(now),
                  snap.get('phone'), snap.get('presence'), snap.get('sip'),
                  json.dumps(details or {}, ensure_ascii=False)))
            return cursor.fetchone()[0]

    def touch(self, row_id, snap, details, now):
        with self._cursor() as cursor:
            cursor.execute("""
                UPDATE binotel_line_watch
                SET last_seen_at = %s, phone_status = %s, presence = %s, sip_status = %s,
                    details = details || %s::jsonb
                WHERE id = %s
            """, (_utc(now), snap.get('phone'), snap.get('presence'), snap.get('sip'),
                  json.dumps(details or {}, ensure_ascii=False), row_id))

    def close(self, row_id, now):
        with self._cursor() as cursor:
            cursor.execute("""
                UPDATE binotel_line_watch SET ended_at = %s, last_seen_at = %s
                WHERE id = %s AND ended_at IS NULL
            """, (_utc(now), _utc(now), row_id))

    def heal(self, row_id, action, result, now):
        with self._cursor() as cursor:
            cursor.execute("""
                UPDATE binotel_line_watch SET heal_action = %s, heal_at = %s, heal_result = %s
                WHERE id = %s
            """, (str(action)[:32], _utc(now), str(result)[:255], row_id))

    def add_events(self, general_call_id, events):
        """События одного звонка. Повтор вебхука того же звонка ничего не добавляет.
        Событие: {'kind', 'line', 'operator_id', 'direction', 'start', 'end', 'phone',
        'presence', 'sip', 'details'}. Возвращает число новых строк."""
        saved = 0
        with self._cursor() as cursor:
            for event in events or []:
                cursor.execute("""
                    INSERT INTO binotel_line_watch
                        (kind, line, operator_id, direction, started_at, last_seen_at, ended_at,
                         phone_status, presence, sip_status, general_call_id, details)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (general_call_id, kind, line) WHERE general_call_id IS NOT NULL
                    DO NOTHING
                """, (event['kind'], event['line'], event.get('operator_id'), event.get('direction'),
                      _utc(event['start']), _utc(event['end']), _utc(event['end']),
                      event.get('phone'), event.get('presence'), event.get('sip'),
                      str(general_call_id)[:32], json.dumps(event.get('details') or {}, ensure_ascii=False)))
                saved += cursor.rowcount
        return saved

    def cleanup(self, keep_days=schema.KEEP_DAYS):
        with self._cursor() as cursor:
            cursor.execute("""
                DELETE FROM binotel_line_watch
                WHERE ended_at IS NOT NULL AND started_at < NOW() - make_interval(days => %s)
            """, (int(keep_days),))
            return cursor.rowcount
