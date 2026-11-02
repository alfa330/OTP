"""База данных: конкурс регистраций.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    execute_values,
)


class RegContestMixin:
    def get_reg_contest_operator_directory(self):
        """Справочник для матчинга операторов CRM: все пользователи с их
        направлением и отделом. Уволенных не отсекаем — регистрации остаются
        за оператором и после увольнения."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.email, u.status, u.role,
                       d.name AS direction_name, dep.name AS department_name
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                LEFT JOIN departments dep ON dep.id = d.department_id
                WHERE u.role IN ('operator', 'trainee', 'sv')
            """)
            keys = ("id", "name", "email", "status", "role",
                    "direction_name", "department_name")
            return [dict(zip(keys, row)) for row in cursor.fetchall()]

    def get_reg_contest_avatar_refs(self, user_ids):
        """Аватарки участников рейтинга одной выборкой:
        {user_id: (avatar_bucket, avatar_blob_path)}. Подписанный URL из
        ссылки строит вызывающая сторона (кэш живёт в bot_schedule2)."""
        ids = [int(uid) for uid in set(user_ids or []) if uid is not None]
        if not ids:
            return {}
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, avatar_bucket, avatar_blob_path
                FROM users
                WHERE id = ANY(%s) AND avatar_blob_path IS NOT NULL
            """, (ids,))
            return {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

    def upsert_reg_contest_operators(self, contest_code, entries, split_origins=None):
        """Свежий срез CRM в строки операторов (одна транзакция).

        Обновляем на месте, а не сносим-вставляем: reached_at — наш
        собственный штамп времени тай-брейка, и полная замена стирала бы его
        на каждом прогоне. Штамп двигаем, только когда счёт засчитанных
        РЕАЛЬНО изменился; прочие поля (имя, группа, всего регистраций)
        обновляются свободно и на очерёдность не влияют.

        Операторов, пропавших из выдачи CRM, убираем — эндпоинт всегда
        отдаёт полный список, так что пропажа означает «регистраций за период
        больше не числится» (от обрезанного ответа страхует
        reg_contest.check_snapshot_shrink на стороне синка).

        Попутно ведём журнал: до перезаписи читаем прошлый срез в той же
        транзакции и складываем в reg_contest_operator_changes каждое реальное
        изменение счётчиков. Возвращаем сводку прогона — сколько строк, что
        изменилось и какие счётчики ушли ВНИЗ (CRM переписывает прошлое, см.
        п. 5 в шапке reg_contest.py; молча проглатывать это нельзя).

        split_origins — {ключ части: исходный ключ оператора} для тех, кто
        сменил направление посреди конкурса (reg_contest.apply_group_splits).
        Разделение — не событие CRM: строка не «просела» и не «пропала», а
        разошлась на две части, поэтому в журнал оно не попадает. Настоящие
        изменения счётчиков у самих частей журнал ловит как у всех."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT crm_operator_id, operator_name, user_name, registrations, successful
                FROM reg_contest_operators
                WHERE contest_code = %s
            """, (contest_code,))
            previous = {row[0]: {"operator_name": row[1], "user_name": row[2],
                                 "registrations": row[3], "successful": row[4]}
                        for row in cursor.fetchall()}
            fresh = {e["crm_operator_id"] for e in entries}
            split_origins = split_origins or {}
            # Исходные ключи, которых в срезе больше нет именно потому, что
            # оператор разошёлся на части.
            split_parents = {origin for part, origin in split_origins.items()
                             if part in fresh}
            changes = []
            for entry in entries:
                was = previous.get(entry["crm_operator_id"])
                if (was and was["registrations"] == entry["registrations"]
                        and was["successful"] == entry["successful"]):
                    continue
                if was is None and split_origins.get(entry["crm_operator_id"]) in previous:
                    # Часть появилась не «из ниоткуда»: это половина строки,
                    # которая до этого прогона лежала целой.
                    continue
                changes.append({
                    "crm_operator_id": entry["crm_operator_id"],
                    "name": (entry.get("user_name") or entry.get("operator_name")
                             or (was or {}).get("user_name") or (was or {}).get("operator_name")),
                    "registrations_before": was["registrations"] if was else None,
                    "registrations_after": entry["registrations"],
                    "successful_before": was["successful"] if was else None,
                    "successful_after": entry["successful"],
                })
            for key, was in previous.items():
                if key in fresh or key in split_parents:
                    continue
                changes.append({
                    "crm_operator_id": key,
                    "name": was["user_name"] or was["operator_name"],
                    "registrations_before": was["registrations"], "registrations_after": None,
                    "successful_before": was["successful"], "successful_after": None,
                })
            if changes:
                execute_values(cursor, """
                    INSERT INTO reg_contest_operator_changes (
                        contest_code, crm_operator_id, operator_name,
                        registrations_before, registrations_after,
                        successful_before, successful_after, changed_at)
                    VALUES %s
                """, [(contest_code, c["crm_operator_id"], c["name"],
                       c["registrations_before"], c["registrations_after"],
                       c["successful_before"], c["successful_after"]) for c in changes],
                    template="(%s,%s,%s,%s,%s,%s,%s,NOW())")
            decreases = [c for c in changes if self._reg_contest_change_is_drop(c)]
            note = self._reg_contest_decrease_note(decreases)
            if entries:
                args = [(contest_code, e["crm_operator_id"], e["operator_login"],
                         e["operator_name"], e["user_id"], e["user_name"],
                         e["contest_group"], e["match_method"],
                         e["registrations"], e["successful"]) for e in entries]
                execute_values(cursor, """
                    INSERT INTO reg_contest_operators (
                        contest_code, crm_operator_id, operator_login, operator_name,
                        user_id, user_name, contest_group, match_method,
                        registrations, successful, reached_at, updated_at)
                    VALUES %s
                    ON CONFLICT (contest_code, crm_operator_id) DO UPDATE SET
                        operator_login = EXCLUDED.operator_login,
                        operator_name  = EXCLUDED.operator_name,
                        user_id        = EXCLUDED.user_id,
                        user_name      = EXCLUDED.user_name,
                        contest_group  = EXCLUDED.contest_group,
                        match_method   = EXCLUDED.match_method,
                        registrations  = EXCLUDED.registrations,
                        successful     = EXCLUDED.successful,
                        reached_at     = CASE
                            WHEN reg_contest_operators.successful
                                 IS DISTINCT FROM EXCLUDED.successful
                            THEN NOW() ELSE reg_contest_operators.reached_at END,
                        updated_at     = NOW()
                """, args, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())")
                cursor.execute("""
                    DELETE FROM reg_contest_operators
                    WHERE contest_code = %s AND crm_operator_id <> ALL(%s)
                """, (contest_code, [e["crm_operator_id"] for e in entries]))
            else:
                cursor.execute("DELETE FROM reg_contest_operators WHERE contest_code = %s",
                               (contest_code,))
            cursor.execute("""
                INSERT INTO reg_contest_syncs (contest_code, synced_at, status, total_rows,
                                               error, decreases, decrease_note)
                VALUES (%s, NOW(), 'ok', %s, NULL, %s, %s)
                ON CONFLICT (contest_code) DO UPDATE SET
                    synced_at = NOW(), status = 'ok',
                    total_rows = EXCLUDED.total_rows, error = NULL,
                    decreases = EXCLUDED.decreases,
                    decrease_note = EXCLUDED.decrease_note
            """, (contest_code, len(entries), len(decreases), note))
        return {"total": len(entries), "changes": len(changes),
                "decreases": len(decreases), "decrease_note": note}

    @staticmethod
    def _reg_contest_change_is_drop(change):
        """Просадка — это уменьшение счётчика или пропажа строки из выдачи."""
        for field in ("registrations", "successful"):
            before, after = change[f"{field}_before"], change[f"{field}_after"]
            if before is None:
                continue
            if after is None or after < before:
                return True
        return False

    @staticmethod
    def _reg_contest_decrease_note(decreases):
        """Короткая расшифровка просадок для метки на синке и лога."""
        if not decreases:
            return None
        parts = []
        for change in decreases[:5]:
            name = change["name"] or change["crm_operator_id"] or "без имени"
            if change["registrations_after"] is None:
                parts.append(f"{name}: пропал из выдачи "
                             f"({change['successful_before']} из "
                             f"{change['registrations_before']} рег.)")
                continue
            moved = [f"{label} {change[f'{field}_before']} → {change[f'{field}_after']}"
                     for field, label in (("successful", "засчитано"),
                                          ("registrations", "регистрации"))
                     if change[f"{field}_before"] is not None
                     and change[f"{field}_after"] < change[f"{field}_before"]]
            parts.append(f"{name}: " + ", ".join(moved))
        if len(decreases) > 5:
            parts.append(f"и ещё {len(decreases) - 5}")
        return "; ".join(parts)[:2000]

    def mark_reg_contest_sync_error(self, contest_code, error):
        """Фиксируем неудачный синк, не трогая последний удачный срез."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO reg_contest_syncs (contest_code, status, error)
                VALUES (%s, 'error', %s)
                ON CONFLICT (contest_code) DO UPDATE SET
                    status = 'error', error = EXCLUDED.error
            """, (contest_code, str(error)[:2000]))

    def get_reg_contest_operators(self, contest_code):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT crm_operator_id, operator_login, operator_name,
                       user_id, user_name, contest_group, match_method,
                       registrations, successful, reached_at
                FROM reg_contest_operators
                WHERE contest_code = %s
            """, (contest_code,))
            keys = ("crm_operator_id", "operator_login", "operator_name",
                    "user_id", "user_name", "contest_group", "match_method",
                    "registrations", "successful", "reached_at")
            return [dict(zip(keys, row)) for row in cursor.fetchall()]

    def get_reg_contest_sync_state(self, contest_code):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT synced_at, status, total_rows, error, decreases, decrease_note
                FROM reg_contest_syncs
                WHERE contest_code = %s
            """, (contest_code,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(zip(("synced_at", "status", "total_rows", "error",
                             "decreases", "decrease_note"), row))

    def get_reg_contest_recent_decreases(self, contest_code, limit=20):
        """Последние просадки счётчиков из журнала — админский ответ на вопрос
        «почему у оператора стало меньше». NULL в *_after = строка пропала из
        выдачи CRM целиком."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT crm_operator_id, operator_name,
                       registrations_before, registrations_after,
                       successful_before, successful_after, changed_at
                FROM reg_contest_operator_changes
                WHERE contest_code = %s
                  AND ((registrations_before IS NOT NULL
                        AND (registrations_after IS NULL
                             OR registrations_after < registrations_before))
                    OR (successful_before IS NOT NULL
                        AND (successful_after IS NULL
                             OR successful_after < successful_before)))
                ORDER BY changed_at DESC, id DESC
                LIMIT %s
            """, (contest_code, limit))
            keys = ("crm_operator_id", "operator_name", "registrations_before",
                    "registrations_after", "successful_before", "successful_after",
                    "changed_at")
            return [dict(zip(keys, row)) for row in cursor.fetchall()]
