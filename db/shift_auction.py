"""База данных: аукцион смен.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
import hashlib
from collections import Counter

from db.core import (  # noqa: F401
    _invalidate_shift_auction_runtime_caches,
    _is_schema_lock_conflict,
    _is_shift_auction_operational_participant_row,
    _minutes_to_time,
    _shift_auction_participant_cache_slot,
    _shift_auction_snapshot_cache_slot,
    _time_to_minutes,
    Alignment,
    Border,
    BytesIO,
    date,
    datetime,
    dt_time,
    execute_values,
    Font,
    get_column_letter,
    Json,
    logging,
    normalize_role_value,
    normalize_shift_auction_mode,
    PatternFill,
    shift_auction_direction_scope_sql,
    SHIFT_AUCTION_MODE_CHAT,
    SHIFT_AUCTION_MODE_DIRECTION_NAME,
    SHIFT_AUCTION_MODE_LINE,
    shift_auction_mode_matches_direction,
    SHIFT_AUCTION_MODES,
    SHIFT_AUCTION_DEPARTMENT_CODE,
    shift_auction_modes_for_departments,
    shift_auction_night_shifts,
    SHIFT_AUCTION_SEVERAL_SHIFTS_PER_DAY_MODES,
    SHIFT_AUCTION_UPLOAD_MODES,
    SHIFT_AUCTION_PARTICIPANT_CACHE_LOCK,
    SHIFT_AUCTION_PARTICIPANT_CACHE_TTL_SECONDS,
    shift_auction_settings_row_id,
    SHIFT_AUCTION_SNAPSHOT_CACHE_TTL_SECONDS,
    SHIFT_AUCTION_SNAPSHOT_COMMON_CACHE_LOCK,
    SHIFT_AUCTION_TEST_EVENT_NOTIFY_CHANNEL,
    Side,
    time,
    timedelta,
    Workbook,
)


class ShiftAuctionMixin:
    def claim_training_report_send(self, period, period_start, user_id) -> bool:
        """Занять отправку сводки: True — отправляем, False — уже отправлена.

        Атомарно на уровне базы: INSERT ... ON CONFLICT DO NOTHING RETURNING.
        Два процесса портала (Render поднимает новый до остановки старого) на
        одном и том же периоде получат True ровно один раз, и получатель не
        увидит две одинаковых сводки.

        Заявка берётся ДО отправки, а не отмечается после: «отмечу потом»
        оставляет между проверкой и отправкой окно, ради которого всё это и
        делается. Провалившуюся отправку заявка не съедает —
        release_training_report_send снимает её обратно.
        """
        column = self.TRAINING_REPORT_COLUMNS.get(str(period or '').strip().lower())
        if not column:
            return False
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return False

        with self._get_cursor() as cursor:
            # Таблица разворачивается схемой раздела под SAVEPOINT и на
            # свежем проде может не успеть появиться. Без неё рассылка обязана
            # работать (просто без защиты от дубля), а не молчать совсем.
            cursor.execute("SELECT to_regclass('public.training_report_sends') IS NOT NULL")
            row = cursor.fetchone()
            if not (row and row[0]):
                logging.warning("training_report_sends отсутствует — отправка без защиты от дубля")
                return True

            cursor.execute("""
                INSERT INTO training_report_sends (period, period_start, user_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (period, period_start, user_id) DO NOTHING
                RETURNING user_id
            """, (str(period).strip().lower(), period_start, uid))
            return cursor.fetchone() is not None

    def _get_shift_auction_test_status(self, enabled, starts_at=None, ends_at=None, paused_at=None, finished_at=None):
        if not enabled:
            return "disabled"
        if finished_at:
            return "closed"
        if paused_at:
            return "paused"
        now = datetime.now()
        if starts_at and now < starts_at:
            return "scheduled"
        if ends_at and now >= ends_at:
            return "closed"
        return "open"

    def _shift_auction_effective_window(self, starts_at, ends_at, group_starts_at=None, group_ends_at=None):
        """Auction window that gates a given operator.

        A week time group may open earlier *or* later than everyone else, so its
        start is used as given — unlike the retired favored group, it is never
        clamped to the main start. A group without its own end inherits the main
        ``ends_at``.
        """
        return (
            group_starts_at if group_starts_at else starts_at,
            group_ends_at if group_ends_at else ends_at,
        )

    def _shift_auction_run_bounds_tx(self, cursor, plan_id, starts_at, ends_at):
        """Widest window across the main one and every time group of the week.

        Manager controls (pause / finish / publish) act on the run as a whole: it
        is live from the earliest group start until the latest group end. ``None``
        keeps its "no gate at this edge" meaning — an unscheduled start or a
        missing end widens the run instead of narrowing it.
        """
        windows = [(starts_at, ends_at)]
        if plan_id is not None:
            cursor.execute("""
                SELECT starts_at, ends_at
                FROM shift_auction_time_groups
                WHERE plan_id = %s
            """, (plan_id,))
            for row in (cursor.fetchall() or []):
                windows.append(self._shift_auction_effective_window(starts_at, ends_at, row[0], row[1]))
        starts = [window[0] for window in windows]
        ends = [window[1] for window in windows]
        earliest = None if any(item is None for item in starts) else min(starts)
        latest = None if any(item is None for item in ends) else max(ends)
        return earliest, latest

    @staticmethod
    def _shift_auction_group_window(group):
        """(starts_at, ends_at) of an already-serialized group, back as datetimes.

        The snapshot's group list is shared (and cached) across viewers, so the
        per-viewer window is derived from it instead of re-querying per operator.
        """
        window = []
        for key in ("starts_at", "ends_at"):
            text = str((group or {}).get(key) or '').strip()
            try:
                window.append(datetime.fromisoformat(text) if text else None)
            except ValueError:
                window.append(None)
        return window[0], window[1]

    def _serialize_shift_auction_time_group_row(self, row):
        return {
            "id": int(row[0]),
            "plan_id": int(row[1]) if row[1] is not None else None,
            "name": row[2] or "",
            "starts_at": row[3].isoformat() if row[3] else None,
            "ends_at": row[4].isoformat() if row[4] else None,
            "self_schedule_enabled": bool(row[5]),
            "created_by": row[7],
            "created_by_name": row[8] or "",
            "created_at": row[9].isoformat() if row[9] else None,
            "operator_ids": [int(item) for item in (row[10] or []) if item is not None],
        }

    def _get_shift_auction_time_groups_tx(self, cursor, plan_id=None, plan_only=False,
                                          direction_mode=SHIFT_AUCTION_MODE_LINE):
        """Time groups with their members.

        ``plan_only`` returns just the given week; otherwise the result covers the
        weeks a manager can still configure (current and future) plus the active
        one, which keeps the payload bounded as weeks accumulate.

        Направление берём с ПЛАНА недели: у групп своей колонки нет и не нужно —
        группа привязана к неделе, а неделя уже принадлежит линии или чату.
        """
        mode = normalize_shift_auction_mode(direction_mode)
        if plan_only:
            if plan_id is None:
                return []
            scope_sql = "g.plan_id = %s"
            params = (plan_id,)
        else:
            scope_sql = (
                "COALESCE(p.direction_mode, 'line') = %s"
                " AND p.archived_at IS NULL AND (p.date_to >= CURRENT_DATE OR g.plan_id = %s)"
            )
            params = (mode, plan_id)
        cursor.execute(f"""
            SELECT
                g.id,
                g.plan_id,
                g.name,
                g.starts_at,
                g.ends_at,
                COALESCE(g.self_schedule_enabled, FALSE) AS self_schedule_enabled,
                g.sort_order,
                g.created_by,
                cu.name AS created_by_name,
                g.created_at,
                COALESCE(
                    ARRAY_AGG(m.operator_id ORDER BY m.operator_id)
                        FILTER (WHERE m.operator_id IS NOT NULL),
                    '{{}}'::INTEGER[]
                ) AS operator_ids
            FROM shift_auction_time_groups g
            JOIN resource_saved_schedule_plans p ON p.id = g.plan_id
            LEFT JOIN shift_auction_time_group_members m ON m.group_id = g.id
            LEFT JOIN users cu ON cu.id = g.created_by
            WHERE {scope_sql}
            GROUP BY g.id, cu.name
            ORDER BY g.plan_id, g.sort_order, g.id
        """, params)
        return [self._serialize_shift_auction_time_group_row(row) for row in (cursor.fetchall() or [])]

    def _get_shift_auction_operator_group_tx(self, cursor, operator_id, plan_id):
        """The operator's time group for the week, or ``None``."""
        try:
            operator_id = int(operator_id or 0)
        except Exception:
            operator_id = 0
        if operator_id <= 0 or plan_id is None:
            return None
        cursor.execute("""
            SELECT g.id, g.name, g.starts_at, g.ends_at, COALESCE(g.self_schedule_enabled, FALSE)
            FROM shift_auction_time_group_members m
            JOIN shift_auction_time_groups g ON g.id = m.group_id
            WHERE m.plan_id = %s
              AND m.operator_id = %s
            LIMIT 1
        """, (plan_id, operator_id))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "name": row[1] or "",
            "starts_at": row[2],
            "ends_at": row[3],
            "self_schedule_enabled": bool(row[4]),
        }

    def _serialize_shift_auction_lot_row(self, row):
        return {
            "id": row[0],
            "shift_date": row[1].strftime('%Y-%m-%d') if row[1] else None,
            "start_time": row[2].strftime('%H:%M') if row[2] else None,
            "end_time": row[3].strftime('%H:%M') if row[3] else None,
            "rate_min": float(row[4]) if row[4] is not None else 0,
            "status": row[5],
            "claimed_by": row[6],
            "claimed_by_name": row[7] or "",
            "claimed_at": row[8].isoformat() if row[8] else None,
            "breaks": row[9] if len(row) > 9 and isinstance(row[9], list) else [],
            "source_schedule_plan_id": row[10] if len(row) > 10 else None,
            "source_schedule_shift_id": row[11] if len(row) > 11 else None,
            "post_auction_claimed": bool(row[12]) if len(row) > 12 and row[12] is not None else False,
            "claimed_by_direction_id": row[13] if len(row) > 13 else None,
            "claimed_by_direction": (row[14] or "") if len(row) > 14 else "",
            "source_start_minute": int(row[15]) if len(row) > 15 and row[15] is not None else None,
            "source_end_minute": int(row[16]) if len(row) > 16 and row[16] is not None else None,
            "claim_start_time": row[17].strftime('%H:%M') if len(row) > 17 and row[17] else None,
            "claim_end_time": row[18].strftime('%H:%M') if len(row) > 18 and row[18] else None,
            "claim_segments": row[19] if len(row) > 19 and isinstance(row[19], list) else [],
            "added_by": row[20] if len(row) > 20 else None,
            "added_by_name": (row[21] or "") if len(row) > 21 else "",
            "self_scheduled": bool(row[22]) if len(row) > 22 else False,
            # Вид исходной смены плана: телефонная (постановка #304) выделяется
            # зелёным и в сетке аукциона, и обратно в планировщике.
            "shift_kind": (row[23] or "") if len(row) > 23 else "",
        }

    def _get_shift_auction_operator_work_shifts_tx(self, cursor, operator_id, lot_dates=None):
        try:
            op_id = int(operator_id)
        except Exception:
            return []
        dates = sorted({
            item for item in (lot_dates or [])
            if item is not None
        })
        if not dates:
            return []

        cursor.execute("""
            SELECT id, shift_date, start_time, end_time
            FROM work_shifts
            WHERE operator_id = %s
              AND shift_date >= %s
              AND shift_date <= %s
            ORDER BY shift_date, start_time, end_time, id
        """, (op_id, dates[0], dates[-1]))

        return [
            {
                "id": row[0],
                "shift_date": row[1].strftime('%Y-%m-%d') if row[1] else None,
                "start_time": row[2].strftime('%H:%M') if row[2] else None,
                "end_time": row[3].strftime('%H:%M') if row[3] else None,
            }
            for row in (cursor.fetchall() or [])
        ]

    def _cache_shift_auction_participant_ids(self, participant_rows, direction_mode=SHIFT_AUCTION_MODE_LINE):
        if SHIFT_AUCTION_PARTICIPANT_CACHE_TTL_SECONDS <= 0:
            return
        ids = frozenset(
            int(row[0])
            for row in (participant_rows or [])
            if row and row[0] is not None
        )
        with SHIFT_AUCTION_PARTICIPANT_CACHE_LOCK:
            slot = _shift_auction_participant_cache_slot(direction_mode)
            slot["ids"] = ids
            slot["expires_at"] = time.time() + SHIFT_AUCTION_PARTICIPANT_CACHE_TTL_SECONDS

    def _get_shift_auction_participant_ids_tx(self, cursor, direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        scope_sql, scope_params = shift_auction_direction_scope_sql(mode)
        cursor.execute(f"""
            SELECT p.operator_id
            FROM shift_auction_test_participants p
            JOIN users u ON u.id = p.operator_id
            JOIN directions d ON d.id = u.direction_id
            JOIN departments dep ON dep.id = d.department_id
            WHERE COALESCE(p.direction_mode, 'line') = %s
              AND {scope_sql}
              AND LOWER(COALESCE(u.role, '')) = 'operator'
              AND COALESCE(u.status, '') NOT IN ('fired', 'dismissal')
        """, (mode, *scope_params))
        participant_ids = {
            int(row[0])
            for row in (cursor.fetchall() or [])
            if row and row[0] is not None
        }
        return participant_ids

    def _get_shift_auction_participant_ids_cached_tx(self, cursor, direction_mode=SHIFT_AUCTION_MODE_LINE):
        now = time.time()
        if SHIFT_AUCTION_PARTICIPANT_CACHE_TTL_SECONDS > 0:
            with SHIFT_AUCTION_PARTICIPANT_CACHE_LOCK:
                slot = _shift_auction_participant_cache_slot(direction_mode)
                if slot["expires_at"] > now:
                    return set(slot["ids"])

        participant_ids = self._get_shift_auction_participant_ids_tx(cursor, direction_mode=direction_mode)
        if SHIFT_AUCTION_PARTICIPANT_CACHE_TTL_SECONDS > 0:
            with SHIFT_AUCTION_PARTICIPANT_CACHE_LOCK:
                slot = _shift_auction_participant_cache_slot(direction_mode)
                slot["ids"] = frozenset(participant_ids)
                slot["expires_at"] = time.time() + SHIFT_AUCTION_PARTICIPANT_CACHE_TTL_SECONDS
        return participant_ids

    def _shift_auction_snapshot_common_cache_key(self, settings_row, include_admin_fields,
                                                 direction_mode=SHIFT_AUCTION_MODE_LINE):
        if not settings_row:
            return None
        # settings_row tail layout: ... published_to_work_schedules_by_name,
        # topup_started_at, topup_started_by, topup_started_by_name, last_event_id.
        # Probe the last position since the same row shape feeds multiple callers.
        last_event_id = settings_row[-1] if isinstance(settings_row, (list, tuple)) and len(settings_row) >= 14 else 0
        return (
            normalize_shift_auction_mode(direction_mode),
            bool(include_admin_fields),
            int(last_event_id or 0),
            int(settings_row[6]) if settings_row[6] is not None else None
        )

    def _build_shift_auction_snapshot_common_tx(self, cursor, settings_row, include_admin_fields=False,
                                                direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        scope_sql, scope_params = shift_auction_direction_scope_sql(mode)
        # Ставка участника — «на дату прогона», а не из карточки: временную подмену
        # (operator_rate_overrides) обязаны видеть и норма часов, и режим «только
        # своя ставка», и подпись в шапке. Иначе на соседних экранах у одного
        # человека оказались бы две разные ставки.
        rate_on_date = self._shift_auction_rate_on_date_tx(cursor, settings_row[6])
        cursor.execute(f"""
            SELECT
                u.id,
                u.name,
                u.role,
                u.status,
                operator_effective_rate(u.id, COALESCE(%s::date, CURRENT_DATE)) AS rate,
                u.direction_id,
                d.name AS direction_name,
                u.supervisor_id,
                s.name AS supervisor_name,
                p.created_at
            FROM shift_auction_test_participants p
            JOIN users u ON u.id = p.operator_id
            JOIN directions d ON d.id = u.direction_id
            JOIN departments dep ON dep.id = d.department_id
            LEFT JOIN users s ON s.id = u.supervisor_id
            WHERE COALESCE(p.direction_mode, 'line') = %s
              AND {scope_sql}
              AND LOWER(COALESCE(u.role, '')) = 'operator'
              AND COALESCE(u.status, '') NOT IN ('fired', 'dismissal')
            ORDER BY u.name
        """, (rate_on_date, mode, *scope_params))
        participant_rows = cursor.fetchall() or []
        self._cache_shift_auction_participant_ids(participant_rows, direction_mode=mode)

        cursor.execute("""
            SELECT
                l.id,
                l.shift_date,
                l.start_time,
                l.end_time,
                l.rate_min,
                l.status,
                l.claimed_by,
                u.name AS claimed_by_name,
                l.claimed_at,
                l.breaks,
                l.source_schedule_plan_id,
                l.source_schedule_shift_id,
                COALESCE(l.post_auction_claimed, FALSE) AS post_auction_claimed,
                u.direction_id AS claimed_by_direction_id,
                claimed_dir.name AS claimed_by_direction,
                -- Remainder lots share the source shift id (for grouping) but cover
                -- only a gap of it; null the source minutes so the frontend uses the
                -- lot's own start/end (the gap), not the full original shift range.
                source_shift.start_minute AS source_start_minute,
                source_shift.end_minute AS source_end_minute,
                l.post_claim_start_time,
                l.post_claim_end_time,
                COALESCE((
                    SELECT jsonb_agg(jsonb_build_object(
                        'claimed_by', hc.claimed_by,
                        'claimed_by_name', cu.name,
                        'start_time', to_char(hc.claimed_start_time, 'HH24:MI'),
                        'end_time', to_char(hc.claimed_end_time, 'HH24:MI'),
                        -- Стадия отличает часть, взятую В ХОДЕ аукциона (её можно
                        -- вернуть кнопкой «Вернуть»), от пост-аукционного добора
                        -- (он уже в графике, и возврат идёт через «Мои доп. смены»).
                        'stage', COALESCE(hc.claim_stage, 'post_auction')
                    ) ORDER BY hc.claimed_start_time)
                    FROM shift_auction_historical_claims hc
                    LEFT JOIN users cu ON cu.id = hc.claimed_by
                    WHERE hc.plan_id = l.source_schedule_plan_id
                      AND hc.source_schedule_shift_id = l.source_schedule_shift_id
                      AND hc.claimed_start_time IS NOT NULL
                      AND hc.claimed_end_time IS NOT NULL
                ), '[]'::jsonb) AS claim_segments,
                l.added_by,
                added_user.name AS added_by_name,
                l.self_scheduled_by,
                source_shift.meta->>'shiftKind' AS shift_kind
            FROM shift_auction_test_lots l
            LEFT JOIN users u ON u.id = l.claimed_by
            LEFT JOIN directions claimed_dir ON claimed_dir.id = u.direction_id
            LEFT JOIN resource_saved_schedule_shifts source_shift ON source_shift.id = l.source_schedule_shift_id
            LEFT JOIN users added_user ON added_user.id = l.added_by
            WHERE COALESCE(l.direction_mode, 'line') = %s
            ORDER BY l.shift_date, l.start_time, l.id
        """, (mode,))
        lot_rows = cursor.fetchall() or []
        lots = [self._serialize_shift_auction_lot_row(row) for row in lot_rows]
        lot_dates = sorted({row[1] for row in lot_rows if row and row[1]})

        selected_period_row = self._get_shift_auction_period_row_tx(cursor, settings_row[6], direction_mode=mode)
        available_periods = self._get_shift_auction_available_periods_tx(cursor, direction_mode=mode)
        # Groups of every configurable week: the manager's form needs them for the
        # week it is editing, and each viewer's own window is derived from the same
        # list — no per-operator query on top of the cached snapshot.
        time_groups = self._get_shift_auction_time_groups_tx(cursor, settings_row[6], direction_mode=mode)
        claim_journal_rows = []
        participant_workloads = []
        if include_admin_fields:
            participant_workloads = self._get_shift_auction_participant_workloads_tx(
                cursor, participant_rows, lots, lot_dates,
                direction_mode=mode,
                self_schedule_operator_ids={
                    operator_id
                    for group in time_groups
                    if group.get("self_schedule_enabled") and group.get("plan_id") == settings_row[6]
                    for operator_id in (group.get("operator_ids") or [])
                }
            )
            cursor.execute("""
                SELECT
                    e.id,
                    e.created_at,
                    NULLIF(e.payload->'lot'->>'shift_date', '') AS shift_date,
                    NULLIF(e.payload->'lot'->>'start_time', '') AS start_time,
                    NULLIF(e.payload->'lot'->>'end_time', '') AS end_time,
                    COALESCE(
                        NULLIF(e.payload->'lot'->>'claimed_by', ''),
                        NULLIF(e.payload->>'operator_id', '')
                    )::INTEGER AS claimed_by,
                    COALESCE(u.name, NULLIF(e.payload->>'operator_name', ''), '') AS claimed_by_name,
                    NULLIF(e.payload->'lot'->>'source_schedule_plan_id', '')::INTEGER AS source_schedule_plan_id,
                    p.date_from,
                    p.date_to
                FROM shift_auction_test_events e
                LEFT JOIN users u
                  ON u.id = COALESCE(
                      NULLIF(e.payload->'lot'->>'claimed_by', ''),
                      NULLIF(e.payload->>'operator_id', '')
                  )::INTEGER
                LEFT JOIN resource_saved_schedule_plans p
                  ON p.id = NULLIF(e.payload->'lot'->>'source_schedule_plan_id', '')::INTEGER
                WHERE e.event_type = 'lot_claimed'
                  AND NULLIF(e.payload->'lot'->>'source_schedule_plan_id', '')::INTEGER = %s
                ORDER BY e.id DESC
                LIMIT 50
            """, (settings_row[6],))
            claim_journal_rows = cursor.fetchall() or []

        return {
            "participant_rows": participant_rows,
            "lots": lots,
            "lot_dates": lot_dates,
            "selected_period_row": selected_period_row,
            "available_periods": available_periods,
            "time_groups": time_groups,
            "claim_journal_rows": claim_journal_rows,
            "participant_workloads": participant_workloads
        }

    def _get_shift_auction_snapshot_common_tx(self, cursor, settings_row, include_admin_fields=False,
                                              direction_mode=SHIFT_AUCTION_MODE_LINE):
        cache_key = self._shift_auction_snapshot_common_cache_key(
            settings_row, include_admin_fields, direction_mode=direction_mode
        )
        if not cache_key or SHIFT_AUCTION_SNAPSHOT_CACHE_TTL_SECONDS <= 0:
            return self._build_shift_auction_snapshot_common_tx(
                cursor, settings_row, include_admin_fields, direction_mode=direction_mode
            )

        now = time.time()
        with SHIFT_AUCTION_SNAPSHOT_COMMON_CACHE_LOCK:
            slot = _shift_auction_snapshot_cache_slot(direction_mode)
            if (
                slot["key"] == cache_key
                and slot["expires_at"] > now
                and slot["value"] is not None
            ):
                return slot["value"]

            value = self._build_shift_auction_snapshot_common_tx(
                cursor, settings_row, include_admin_fields, direction_mode=direction_mode
            )
            slot["key"] = cache_key
            slot["value"] = value
            slot["expires_at"] = time.time() + SHIFT_AUCTION_SNAPSHOT_CACHE_TTL_SECONDS
            return value

    def _insert_shift_auction_test_event(self, cursor, event_type, payload,
                                         direction_mode=SHIFT_AUCTION_MODE_LINE):
        cursor.execute("""
            INSERT INTO shift_auction_test_events (event_type, payload, direction_mode)
            VALUES (%s, %s, %s)
            RETURNING id, created_at
        """, (
            str(event_type or '').strip()[:64],
            Json(payload or {}),
            normalize_shift_auction_mode(direction_mode),
        ))
        row = cursor.fetchone()
        event_id = row[0] if row else None
        if event_id is not None:
            cursor.execute(
                "SELECT pg_notify(%s, %s)",
                (SHIFT_AUCTION_TEST_EVENT_NOTIFY_CHANNEL, str(event_id))
            )
            # Only invalidate the global snapshot cache when structural fields
            # change (auction settings, participant list, lot collection, etc.).
            # `lot_claimed` / `lot_released` / `day_off_*` events ride on SSE for
            # real-time UI updates, and the 1.5s TTL keeps fresh views close
            # enough. Aggressive invalidation here caused a thundering-herd
            # rebuild storm under load — every claim made all polling clients
            # miss the cache at once and stampede the DB pool.
            structural_events = {
                "settings_updated",
                "lots_seeded",
                "lot_added",
                "auction_published",
                "auction_paused",
                "auction_resumed",
                "auction_finished",
                "auction_restarted",
                "auction_topup_started",
                "auction_topup_stopped",
                "auction_rate_lock_updated",
                "resource_schedule_saved",
            }
            participant_invalidating_events = {"settings_updated"}
            normalized_event_type = str(event_type or '').strip()
            if normalized_event_type in structural_events:
                _invalidate_shift_auction_runtime_caches(
                    include_participants=normalized_event_type in participant_invalidating_events,
                    direction_mode=direction_mode,
                )
        return {
            "id": event_id,
            "event_type": event_type,
            "payload": payload or {},
            "created_at": row[1].isoformat() if row and row[1] else None
        }

    def _lock_shift_auction_operator_tx(self, cursor, operator_id):
        # Serialize one operator's auction mutations so shared invariants are
        # checked against a stable per-operator view inside this transaction.
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (self.SHIFT_AUCTION_OPERATOR_LOCK_NAMESPACE, int(operator_id))
        )

    def _shift_auction_break_minutes(self, breaks, range_start_min, range_end_min):
        """Минуты перерывов ВНУТРИ окна — ровно та их часть, что в него попала.

        Окно не всегда равно смене: в чате её разбирают кусками, и у куска
        17:00-20:00 смены 16:00-01:00 обед смены может быть на 20:15 (не его
        вовсе) или наполовину торчать за границу. Целый перерыв тут занижал бы
        часы, пропущенный — завышал, а экран оператора считает именно
        пересечение (getAuctionLotBreakMinutes): норма обязана считать так же,
        иначе человек видит «осталось 4 ч» и получает отказ на трёх.
        """
        total = 0
        window_start = min(range_start_min, range_end_min)
        window_end = max(range_start_min, range_end_min)
        for item in (breaks or []):
            if not isinstance(item, dict):
                continue
            try:
                start_minute = int(float(item.get("start", 0) or 0))
                end_minute = int(float(item.get("end", 0) or 0))
            except Exception:
                continue
            if end_minute <= start_minute:
                end_minute += 24 * 60
            # Окно за полночь живёт минутами «после 24:00», а перерыв хранится
            # минутами суток — поднимаем его в те же сутки (правило
            # _break_overlaps_minute_range).
            if window_end > 24 * 60 and start_minute < window_start:
                start_minute += 24 * 60
                end_minute += 24 * 60
            total += max(0, min(end_minute, window_end) - max(start_minute, window_start))
        return max(0, int(total))

    def _shift_auction_lot_minutes(self, start_time_value, end_time_value, breaks=None):
        start_minute, end_minute = self._schedule_interval_minutes(start_time_value, end_time_value)
        duration_minutes = max(0, end_minute - start_minute)
        break_minutes = min(duration_minutes, self._shift_auction_break_minutes(
            breaks if isinstance(breaks, list) else [], start_minute, end_minute))
        return {
            "duration_minutes": duration_minutes,
            "break_minutes": break_minutes,
            "net_minutes": max(0, duration_minutes - break_minutes),
        }

    def _shift_auction_day_off_quota(self, total_days):
        # Две выходные — это норма НЕДЕЛИ, а не «две на любой прогон». Период
        # аукциона задаётся датами в «Расчёте ресурсов» и бывает короче семи дней;
        # с прежним min(2, ...) четырёхдневный прогон оставлял оператору два
        # рабочих дня (16 ч при ставке 1,0), и раздать сгенерированные смены было
        # некому. На семи днях формула даёт прежние 2, поэтому боевые недели
        # линии и чата не сдвигаются.
        try:
            total = int(total_days or 0)
        except Exception:
            total = 0
        total = max(0, total)
        return (total * 2) // 7

    @staticmethod
    def _shift_auction_rate_bucket(rate_value):
        # The same buckets the UI grid uses: anything up to 0.5 is a 0.5-rate
        # shift/operator, up to 0.75 is 0.75, everything above is a full rate.
        try:
            rate = float(rate_value or 0)
        except Exception:
            rate = 0.0
        if rate <= 0.5 + 0.001:
            return 0.5
        if rate <= 0.75 + 0.001:
            return 0.75
        return 1.0

    def _shift_auction_lot_rate_bucket(self, start_time_value, end_time_value):
        # A lot's rate group is derived from its clock duration, mirroring the
        # frontend grid grouping: <5.5h -> 0.5, 5.5-7.5h -> 0.75, >=7.5h -> 1
        # (the 20*08 night shift lands in 1.0 by duration as well).
        start_minute, end_minute = self._schedule_interval_minutes(start_time_value, end_time_value)
        duration_minutes = max(0, end_minute - start_minute)
        if duration_minutes < int(5.5 * 60):
            return 0.5
        if duration_minutes < int(7.5 * 60):
            return 0.75
        return 1.0

    def _get_shift_auction_participant_workloads_tx(
        self, cursor, participant_rows, lots, lot_dates,
        day_offs_by_operator=None, self_schedule_operator_ids=None,
        direction_mode=SHIFT_AUCTION_MODE_LINE
    ):
        # Per-operator workload (norm vs claimed) computed in one pass.
        # Only the day-off / blocked-period counters need the DB; lot stats are
        # derived from the already-loaded `lots` list.
        # Temporary statuses (Б/С, sick/annual leave, etc.) remain selectable in
        # auction settings, but must not create rows in operational progress.
        operational_participant_rows = [
            row for row in (participant_rows or [])
            if _is_shift_auction_operational_participant_row(row, direction_mode)
        ]
        if not operational_participant_rows:
            return []
        operator_ids = [
            int(row[0])
            for row in operational_participant_rows
            if row and row[0] is not None
        ]
        if not operator_ids:
            return []
        lot_dates_list = [d for d in (lot_dates or []) if d]
        total_days = len(lot_dates_list)

        day_offs_count = {op_id: 0 for op_id in operator_ids}
        day_off_dates_by_op = {op_id: [] for op_id in operator_ids}
        if day_offs_by_operator is not None:
            for op_id in operator_ids:
                dates = sorted(day_offs_by_operator.get(op_id, set()) or [])
                day_offs_count[op_id] = len(dates)
                day_off_dates_by_op[op_id] = [
                    d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
                    for d in dates
                ]
        else:
            cursor.execute(
                """
                SELECT operator_id, day_off_date
                FROM shift_auction_test_day_offs
                WHERE operator_id = ANY(%s)
                  AND COALESCE(direction_mode, 'line') = %s
                ORDER BY operator_id, day_off_date
                """,
                (operator_ids, normalize_shift_auction_mode(direction_mode))
            )
            for op_id, day_off_date in (cursor.fetchall() or []):
                if op_id is None or day_off_date is None:
                    continue
                key = int(op_id)
                day_off_dates_by_op.setdefault(key, []).append(
                    day_off_date.strftime('%Y-%m-%d') if hasattr(day_off_date, 'strftime') else str(day_off_date)
                )
            for op_id in operator_ids:
                day_offs_count[op_id] = len(day_off_dates_by_op.get(op_id, []))

        blocked_days_count = {op_id: 0 for op_id in operator_ids}
        if lot_dates_list:
            period_start = min(lot_dates_list)
            period_end = max(lot_dates_list)
            cursor.execute(
                """
                SELECT p.operator_id, COUNT(DISTINCT d.shift_date)::int
                FROM operator_schedule_status_periods p
                JOIN UNNEST(%s::date[]) AS d(shift_date)
                  ON d.shift_date >= p.start_date
                 AND d.shift_date <= COALESCE(p.end_date, %s::date)
                WHERE p.operator_id = ANY(%s)
                  AND p.start_date <= %s::date
                  AND COALESCE(p.end_date, %s::date) >= %s::date
                GROUP BY p.operator_id
                """,
                (lot_dates_list, period_end, operator_ids, period_end, period_end, period_start)
            )
            for op_id, count_value in (cursor.fetchall() or []):
                if op_id is None:
                    continue
                blocked_days_count[int(op_id)] = int(count_value or 0)

        # Fully-claimed lots are attributed to their single owner. Lots carrying
        # claim_segments (partial доборы — possibly several operators per shift) are
        # attributed per-segment instead, to avoid double counting.
        claimed_by_operator = {op_id: [] for op_id in operator_ids}
        segments_by_operator = {op_id: [] for op_id in operator_ids}
        for lot in (lots or []):
            if not isinstance(lot, dict):
                continue
            segs = lot.get('claim_segments')
            if isinstance(segs, list) and segs:
                lot_breaks = lot.get('breaks') if isinstance(lot.get('breaks'), list) else []
                for seg in segs:
                    try:
                        seg_op = int(seg.get('claimed_by')) if seg.get('claimed_by') is not None else None
                    except Exception:
                        seg_op = None
                    if seg_op is None or seg_op not in segments_by_operator:
                        continue
                    segments_by_operator[seg_op].append({
                        'start_time': seg.get('start_time'),
                        'end_time': seg.get('end_time'),
                        'breaks': lot_breaks,
                    })
                continue
            if str(lot.get('status') or '') != 'claimed':
                continue
            owner = lot.get('claimed_by')
            try:
                owner_id = int(owner) if owner is not None else None
            except Exception:
                owner_id = None
            if owner_id is None or owner_id not in claimed_by_operator:
                continue
            claimed_by_operator[owner_id].append(lot)

        workloads = []
        for row in operational_participant_rows:
            if not row or row[0] is None:
                continue
            op_id = int(row[0])
            try:
                rate = float(row[4]) if row[4] is not None else 1.0
            except Exception:
                rate = 1.0
            if rate <= 0:
                rate = 1.0
            blocked = blocked_days_count.get(op_id, 0)
            workday_count = self._shift_auction_norm_workday_count(total_days, blocked_days=blocked)
            norm_minutes = int(round(workday_count * 8 * 60 * rate))
            claimed_lots = claimed_by_operator.get(op_id, [])
            op_segments = segments_by_operator.get(op_id, [])
            claimed_net = 0
            claimed_break = 0
            for lot in claimed_lots:
                minutes = self._shift_auction_lot_minutes(
                    lot.get('claim_start_time') or lot.get('post_claim_start_time') or lot.get('start_time'),
                    lot.get('claim_end_time') or lot.get('post_claim_end_time') or lot.get('end_time'),
                    lot.get('breaks') if isinstance(lot.get('breaks'), list) else []
                )
                claimed_net += int(minutes.get('net_minutes') or 0)
                claimed_break += int(minutes.get('break_minutes') or 0)
            for seg in op_segments:
                minutes = self._shift_auction_lot_minutes(seg['start_time'], seg['end_time'], seg['breaks'])
                claimed_net += int(minutes.get('net_minutes') or 0)
                claimed_break += int(minutes.get('break_minutes') or 0)
            # What actually limits the operator: the norm, plus the allowance of a
            # "свой график" group. Progress is measured against it so an intended
            # overshoot is not reported as a violation, while the plain norm stays
            # visible for payroll.
            ceiling_minutes = norm_minutes + (
                self.SHIFT_AUCTION_SELF_SCHEDULE_EXTRA_MINUTES
                if op_id in (self_schedule_operator_ids or set()) else 0
            )
            workloads.append({
                "operator_id": op_id,
                "rate": rate,
                "norm_minutes": norm_minutes,
                "ceiling_minutes": ceiling_minutes,
                "self_schedule": ceiling_minutes != norm_minutes,
                "workday_count": int(workday_count),
                "claimed_net_minutes": int(claimed_net),
                "claimed_break_minutes": int(claimed_break),
                "remaining_minutes": max(0, ceiling_minutes - int(claimed_net)),
                "over_minutes": max(0, int(claimed_net) - ceiling_minutes),
                "blocked_days": int(blocked),
                "selected_day_offs": int(day_offs_count.get(op_id, 0)),
                "day_off_dates": list(day_off_dates_by_op.get(op_id, [])),
                "lots_claimed_count": len(claimed_lots) + len(op_segments),
                "is_complete": bool(ceiling_minutes > 0 and claimed_net >= ceiling_minutes - 1),
            })
        return workloads

    def _shift_auction_norm_workday_count(self, total_days, blocked_days=0):
        try:
            total = int(total_days or 0)
        except Exception:
            total = 0
        try:
            blocked = int(blocked_days or 0)
        except Exception:
            blocked = 0
        blocked = min(max(0, blocked), max(0, total))
        available_days = max(0, total - blocked)
        if available_days <= 0:
            return 0
        day_off_quota = self._shift_auction_day_off_quota(total)
        manual_day_off_quota = max(0, day_off_quota - blocked)
        return max(1, available_days - manual_day_off_quota)

    def _shift_auction_status_period_label(self, status_code, dismissal_reason=None, is_blacklist=False):
        code = str(status_code or '').strip().lower()
        if code in ('bs', 'unpaid_leave'):
            return 'Б/С'
        if code == 'sick_leave':
            return 'Больничный'
        if code == 'annual_leave':
            return 'Отпуск'
        if code == 'dismissal':
            reason = str(dismissal_reason or '').strip()
            if is_blacklist:
                return 'ЧС'
            return reason or 'Увольнение'
        return str(status_code or '').strip() or 'Период'

    def _get_shift_auction_lot_dates_tx(self, cursor, direction_mode=SHIFT_AUCTION_MODE_LINE):
        cursor.execute("""
            SELECT DISTINCT shift_date
            FROM shift_auction_test_lots
            WHERE shift_date IS NOT NULL
              AND COALESCE(direction_mode, 'line') = %s
            ORDER BY shift_date
        """, (normalize_shift_auction_mode(direction_mode),))
        return [row[0] for row in (cursor.fetchall() or []) if row and row[0]]

    def _get_shift_auction_operator_claimed_intervals_tx(self, cursor, operator_id,
                                                         direction_mode=SHIFT_AUCTION_MODE_LINE):
        """Всё, что оператор уже занял в ЭТОМ аукционе: целые лоты и взятые части смен.

        Лот, у которого есть части, целиком не считается ни разу — иначе последний
        забравший кусок получил бы в норму всю смену.
        """
        mode = normalize_shift_auction_mode(direction_mode)
        cursor.execute(f"""
            SELECT
                to_char(l.shift_date, 'YYYY-MM-DD') AS shift_date,
                to_char(COALESCE(l.post_claim_start_time, l.start_time), 'HH24:MI') AS start_time,
                to_char(COALESCE(l.post_claim_end_time, l.end_time), 'HH24:MI') AS end_time,
                l.breaks
            FROM shift_auction_test_lots l
            WHERE l.claimed_by = %s
              AND l.status = 'claimed'
              AND COALESCE(l.direction_mode, 'line') = %s
              AND NOT EXISTS (
                  SELECT 1 FROM shift_auction_historical_claims hc
                  WHERE hc.plan_id = l.source_schedule_plan_id
                    AND hc.source_schedule_shift_id = l.source_schedule_shift_id
                    AND hc.claimed_start_time IS NOT NULL
                    AND hc.claimed_end_time IS NOT NULL
              )
            UNION ALL
            SELECT
                to_char({self.SHIFT_AUCTION_CLAIM_DATE_SQL}, 'YYYY-MM-DD') AS shift_date,
                to_char(hc.claimed_start_time, 'HH24:MI') AS start_time,
                to_char(hc.claimed_end_time, 'HH24:MI') AS end_time,
                -- Перерывы исходной смены (в лоте лежит их копия): без них кусок
                -- шёл в норму валовым, и у разобравшего неделю кусками набегал
                -- лишний час-другой против того, что показывает его же экран.
                sh.breaks
            FROM shift_auction_historical_claims hc
            JOIN resource_saved_schedule_shifts sh
              ON sh.id = hc.source_schedule_shift_id
             AND sh.plan_id = hc.plan_id
            WHERE hc.claimed_by = %s
              AND hc.claimed_start_time IS NOT NULL
              AND hc.claimed_end_time IS NOT NULL
              -- Ограничение ТЕКУЩИМ прогоном и его направлением — через EXISTS, а не
              -- JOIN: на одну исходную смену теоретически может приходиться больше
              -- одного лота, и join удвоил бы часы в норме.
              AND EXISTS (
                  SELECT 1 FROM shift_auction_test_lots l2
                  WHERE l2.source_schedule_plan_id = hc.plan_id
                    AND l2.source_schedule_shift_id = hc.source_schedule_shift_id
                    AND COALESCE(l2.direction_mode, 'line') = %s
              )
        """, (operator_id, mode, operator_id, mode))
        rows = cursor.fetchall() or []
        return [
            {
                "shift_date": row[0],
                "start_time": row[1],
                "end_time": row[2],
                "breaks": row[3] if isinstance(row[3], list) else [],
            }
            for row in rows
        ]

    def _serialize_shift_auction_period_row(self, row):
        if not row:
            return None
        date_from = row[1]
        date_to = row[2]
        shift_count = int(row[5] or 0) if len(row) > 5 else 0
        can_restart = bool(row[6]) if len(row) > 6 else bool(date_to and date_to >= date.today())
        return {
            "id": int(row[0]),
            "date_from": date_from.strftime('%Y-%m-%d') if date_from else None,
            "date_to": date_to.strftime('%Y-%m-%d') if date_to else None,
            "title": row[3] or "",
            "updated_at": row[4].isoformat() if row[4] else None,
            "shift_count": shift_count,
            "can_restart": can_restart,
        }

    def _shift_auction_rate_on_date_tx(self, cursor, plan_id):
        """Дата, на которую аукцион берёт ставку оператора.

        Это первый день периода выбранного плана: временная подмена ставки
        (operator_rate_overrides) действует окном, и прогон должен целиком лежать
        по одну его сторону. Иначе норма часов у человека менялась бы посреди
        недели — в понедельник 30 ч, в четверг 20 ч.

        Плана нет (аукцион ещё не привязан к периоду) — возвращаем None, и SQL
        подставит CURRENT_DATE.
        """
        try:
            plan_id = int(plan_id or 0)
        except Exception:
            plan_id = 0
        if plan_id <= 0:
            return None
        cursor.execute(
            "SELECT date_from FROM resource_saved_schedule_plans WHERE id = %s", (plan_id,))
        row = cursor.fetchone()
        return row[0] if row else None

    def _get_shift_auction_period_row_tx(self, cursor, plan_id, direction_mode=SHIFT_AUCTION_MODE_LINE):
        try:
            plan_id = int(plan_id or 0)
        except Exception:
            plan_id = 0
        if plan_id <= 0:
            return None
        cursor.execute("""
            SELECT
                p.id,
                p.date_from,
                p.date_to,
                p.title,
                p.updated_at,
                COUNT(s.id) AS shift_count,
                p.date_to >= CURRENT_DATE AS can_restart
            FROM resource_saved_schedule_plans p
            LEFT JOIN resource_saved_schedule_shifts s
              ON s.plan_id = p.id
             AND COALESCE(s.meta->>'excludeFromAuction', 'false') <> 'true'
            WHERE p.id = %s
              AND p.archived_at IS NULL
              -- То же условие и по прямому номеру плана: через эту выборку идут проверка
              -- периода и все засевы лотов, поэтому она закрывает запись, а не только показ.
              AND COALESCE(p.direction_mode, 'line') = %s
            GROUP BY p.id, p.date_from, p.date_to, p.title, p.updated_at
        """, (plan_id, normalize_shift_auction_mode(direction_mode)))
        return cursor.fetchone()

    def _get_shift_auction_available_periods_tx(self, cursor, direction_mode=SHIFT_AUCTION_MODE_LINE):
        cursor.execute("""
            SELECT
                p.id,
                p.date_from,
                p.date_to,
                p.title,
                p.updated_at,
                COUNT(s.id) AS shift_count,
                p.date_to >= CURRENT_DATE AS can_restart
            FROM resource_saved_schedule_plans p
            LEFT JOIN resource_saved_schedule_shifts s
              ON s.plan_id = p.id
             AND COALESCE(s.meta->>'excludeFromAuction', 'false') <> 'true'
            WHERE p.archived_at IS NULL
              -- Планы линии и чата лежат в ОДНОЙ таблице и на одну неделю выглядят
              -- одинаково (те же даты, тот же пустой заголовок) — без фильтра по
              -- направлению неделя чата встала бы в список периодов аукциона линии.
              AND COALESCE(p.direction_mode, 'line') = %s
              -- Длину периода не проверяем: график в «Расчёте ресурсов» строится
              -- по произвольному отрезку дат, и план на 4 дня — такой же законный
              -- предмет аукциона, как неделя. Раньше он молча выпадал из списка,
              -- аукцион оставался на прошлом семидневном плане и показывал 7 дней
              -- вместо четырёх вместе с чужими сменами.
            GROUP BY p.id, p.date_from, p.date_to, p.title, p.updated_at
            HAVING COUNT(s.id) > 0
            ORDER BY p.date_from, p.updated_at DESC, p.id DESC
        """, (normalize_shift_auction_mode(direction_mode),))
        return [self._serialize_shift_auction_period_row(row) for row in (cursor.fetchall() or [])]

    def _validate_shift_auction_period_tx(self, cursor, schedule_plan_id, require_restartable=False,
                                          direction_mode=SHIFT_AUCTION_MODE_LINE):
        row = self._get_shift_auction_period_row_tx(cursor, schedule_plan_id, direction_mode=direction_mode)
        if not row:
            raise ValueError("AUCTION_PERIOD_NOT_FOUND")
        date_from = row[1]
        date_to = row[2]
        if not date_from or not date_to or date_to < date_from:
            raise ValueError("AUCTION_PERIOD_INVALID")
        if int(row[5] or 0) <= 0:
            raise ValueError("AUCTION_PERIOD_EMPTY")
        if require_restartable and not bool(row[6]):
            raise ValueError("AUCTION_PERIOD_PAST")
        return row

    def _get_shift_auction_operator_blocked_dates_tx(self, cursor, operator_id, lot_dates=None):
        try:
            operator_id = int(operator_id or 0)
        except Exception:
            operator_id = 0
        if operator_id <= 0:
            return []

        auction_dates = list(lot_dates) if lot_dates is not None else self._get_shift_auction_lot_dates_tx(cursor)
        auction_dates = [item for item in auction_dates if item]
        if not auction_dates:
            return []

        period_start = min(auction_dates)
        period_end = max(auction_dates)
        cursor.execute("""
            SELECT DISTINCT ON (d.shift_date)
                d.shift_date,
                p.id,
                p.status_code,
                p.start_date,
                p.end_date,
                p.dismissal_reason,
                p.is_blacklist,
                p.comment
            FROM UNNEST(%s::date[]) AS d(shift_date)
            JOIN operator_schedule_status_periods p
              ON p.operator_id = %s
             AND d.shift_date >= p.start_date
             AND d.shift_date <= COALESCE(p.end_date, %s::date)
            WHERE p.start_date <= %s::date
              AND COALESCE(p.end_date, %s::date) >= %s::date
            ORDER BY d.shift_date,
                     p.start_date DESC,
                     COALESCE(p.end_date, p.start_date) DESC,
                     p.id DESC
        """, (auction_dates, operator_id, period_end, period_end, period_end, period_start))

        results = []
        for shift_date, period_id, status_code, start_date, end_date, dismissal_reason, is_blacklist, comment in (cursor.fetchall() or []):
            if not shift_date:
                continue
            label = self._shift_auction_status_period_label(status_code, dismissal_reason, is_blacklist)
            results.append({
                "date": shift_date.strftime('%Y-%m-%d'),
                "period_id": period_id,
                "status_code": status_code,
                "label": label,
                "start_date": start_date.strftime('%Y-%m-%d') if start_date else None,
                "end_date": end_date.strftime('%Y-%m-%d') if end_date else None,
                "dismissal_reason": dismissal_reason or "",
                "is_blacklist": bool(is_blacklist),
                "comment": comment or ""
            })
        return results

    def _get_shift_auction_operator_blocked_date_map_tx(self, cursor, operator_id, lot_dates=None):
        return {
            item["date"]: item
            for item in self._get_shift_auction_operator_blocked_dates_tx(cursor, operator_id, lot_dates=lot_dates)
            if item.get("date")
        }

    def _get_shift_auction_operator_workload_tx(
        self, cursor, operator_id, operator_rate,
        lot_dates=None, blocked_dates=None, claimed_rows=None,
        direction_mode=SHIFT_AUCTION_MODE_LINE
    ):
        if lot_dates is None:
            lot_dates = self._get_shift_auction_lot_dates_tx(cursor, direction_mode=direction_mode)
        period_days = len(lot_dates)
        if blocked_dates is None:
            blocked_dates = self._get_shift_auction_operator_blocked_dates_tx(cursor, operator_id, lot_dates=lot_dates)
        workday_count = self._shift_auction_norm_workday_count(period_days, len(blocked_dates))
        norm_minutes = int(round(workday_count * 8 * 60 * max(0.0, float(operator_rate or 0))))

        if claimed_rows is None:
            claimed_rows = [
                (item["start_time"], item["end_time"], item["breaks"])
                for item in self._get_shift_auction_operator_claimed_intervals_tx(
                    cursor, operator_id, direction_mode=direction_mode
                )
            ]
        claimed_net_minutes = 0
        claimed_break_minutes = 0
        for start_time_value, end_time_value, breaks in claimed_rows:
            minutes = self._shift_auction_lot_minutes(
                start_time_value,
                end_time_value,
                breaks if isinstance(breaks, list) else []
            )
            claimed_net_minutes += minutes["net_minutes"]
            claimed_break_minutes += minutes["break_minutes"]

        return {
            "period_days": period_days,
            "blocked_days": len(blocked_dates),
            "workday_count": workday_count,
            "norm_minutes": norm_minutes,
            "claimed_net_minutes": int(claimed_net_minutes),
            "claimed_break_minutes": int(claimed_break_minutes),
            "remaining_minutes": max(0, norm_minutes - int(claimed_net_minutes)),
        }

    def get_shift_auction_test_access(self, current_user_id=None, direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        settings_id = shift_auction_settings_row_id(mode)
        scope_sql, scope_params = shift_auction_direction_scope_sql(mode)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    s.enabled,
                    s.launch_note,
                    s.starts_at,
                    s.ends_at,
                    s.paused_at,
                    s.finished_at,
                    COALESCE(
                        s.selected_schedule_plan_id,
                        (
                            SELECT source_schedule_plan_id
                            FROM shift_auction_test_lots
                            WHERE source_schedule_plan_id IS NOT NULL
                              AND COALESCE(direction_mode, 'line') = s.direction_mode
                            ORDER BY id
                            LIMIT 1
                        )
                    ) AS selected_schedule_plan_id,
                    s.updated_by,
                    u.name AS updated_by_name,
                    s.updated_at,
                    s.published_to_work_schedules_at,
                    s.published_to_work_schedules_by,
                    publisher.name AS published_to_work_schedules_by_name,
                    s.topup_started_at,
                    s.topup_started_by,
                    topup_user.name AS topup_started_by_name
                FROM shift_auction_test_access s
                LEFT JOIN users u ON u.id = s.updated_by
                LEFT JOIN users publisher ON publisher.id = s.published_to_work_schedules_by
                LEFT JOIN users topup_user ON topup_user.id = s.topup_started_by
                WHERE s.id = %s
            """, (settings_id,))
            settings_row = cursor.fetchone()
            if not settings_row:
                cursor.execute("""
                    INSERT INTO shift_auction_test_access (id, direction_mode)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (settings_id, mode))
                settings_row = (False, '', None, None, None, None, None, None, None, None, None, None, None, None, None, None)

            cursor.execute(f"""
                SELECT
                    u.id,
                    u.name,
                    u.role,
                    u.status,
                    -- Та же ставка «на дату прогона», что видит оператор в аукционе.
                    -- Иначе в настройках у человека 0,50, а у него самого 0,75 —
                    -- ровно то расхождение между соседними экранами, из-за которого
                    -- ставку и приходится объяснять.
                    operator_effective_rate(u.id, COALESCE(
                        (SELECT plan.date_from
                           FROM shift_auction_test_access acc
                           JOIN resource_saved_schedule_plans plan
                             ON plan.id = acc.selected_schedule_plan_id
                          WHERE acc.id = %s),
                        CURRENT_DATE)) AS rate,
                    u.direction_id,
                    d.name AS direction_name,
                    u.supervisor_id,
                    s.name AS supervisor_name,
                    p.created_at
                FROM shift_auction_test_participants p
                JOIN users u ON u.id = p.operator_id
                JOIN directions d ON d.id = u.direction_id
                JOIN departments dep ON dep.id = d.department_id
                LEFT JOIN users s ON s.id = u.supervisor_id
                WHERE COALESCE(p.direction_mode, 'line') = %s
                  AND {scope_sql}
                  AND LOWER(COALESCE(u.role, '')) = 'operator'
                  AND COALESCE(u.status, '') NOT IN ('fired', 'dismissal')
                ORDER BY u.name
            """, (settings_id, mode, *scope_params))
            participant_rows = cursor.fetchall() or []
            cursor.execute(
                "SELECT COALESCE(rate_lock_enabled, FALSE) FROM shift_auction_test_access WHERE id = %s",
                (settings_id,)
            )
            rate_lock_row = cursor.fetchone()
            rate_lock_enabled = bool(rate_lock_row[0]) if rate_lock_row else False
            selected_period_row = self._get_shift_auction_period_row_tx(cursor, settings_row[6], direction_mode=mode)
            time_groups = self._get_shift_auction_time_groups_tx(cursor, settings_row[6], direction_mode=mode)

        selected_operator_ids = [int(row[0]) for row in participant_rows if row and row[0] is not None]
        current_id = None
        try:
            current_id = int(current_user_id) if current_user_id is not None else None
        except Exception:
            current_id = None

        return {
            "enabled": bool(settings_row[0]) if settings_row else False,
            "launch_note": settings_row[1] or "",
            "starts_at": settings_row[2].isoformat() if settings_row and settings_row[2] else None,
            "ends_at": settings_row[3].isoformat() if settings_row and settings_row[3] else None,
            "time_groups": time_groups,
            "rate_lock_enabled": rate_lock_enabled,
            "paused_at": settings_row[4].isoformat() if settings_row and settings_row[4] else None,
            "finished_at": settings_row[5].isoformat() if settings_row and settings_row[5] else None,
            "selected_schedule_plan_id": settings_row[6] if settings_row else None,
            "selected_period": self._serialize_shift_auction_period_row(selected_period_row),
            "status": self._get_shift_auction_test_status(
                bool(settings_row[0]) if settings_row else False,
                settings_row[2] if settings_row else None,
                settings_row[3] if settings_row else None,
                settings_row[4] if settings_row else None,
                settings_row[5] if settings_row else None
            ),
            "updated_by": settings_row[7] if settings_row else None,
            "updated_by_name": settings_row[8] or "",
            "updated_at": settings_row[9].isoformat() if settings_row and settings_row[9] else None,
            "published_to_work_schedules_at": settings_row[10].isoformat() if settings_row and settings_row[10] else None,
            "published_to_work_schedules_by": settings_row[11] if settings_row else None,
            "published_to_work_schedules_by_name": settings_row[12] or "",
            "topup_started_at": settings_row[13].isoformat() if settings_row and len(settings_row) > 13 and settings_row[13] else None,
            "topup_started_by": settings_row[14] if settings_row and len(settings_row) > 14 else None,
            "topup_started_by_name": (settings_row[15] or "") if settings_row and len(settings_row) > 15 else "",
            "selected_operator_ids": selected_operator_ids,
            "selected_operators": [
                {
                    "id": row[0],
                    "name": row[1],
                    "role": normalize_role_value(row[2]),
                    "status": row[3],
                    "rate": float(row[4]) if row[4] is not None else 1.0,
                    "direction_id": row[5],
                    "direction": row[6] or "",
                    "supervisor_id": row[7],
                    "supervisor_name": row[8] or "",
                    "selected_at": row[9].isoformat() if row[9] else None,
                }
                for row in participant_rows
            ],
            "is_current_user_tester": current_id in set(selected_operator_ids) if current_id is not None else False
        }

    def is_shift_auction_test_participant(self, operator_id):
        try:
            operator_id = int(operator_id)
        except Exception:
            return False
        if operator_id <= 0:
            return False

        with self._get_cursor() as cursor:
            # Человек участвует ровно в одном направлении — спрашиваем оба, чтобы не
            # читать его карточку ради ответа «да/нет».
            return any(
                operator_id in self._get_shift_auction_participant_ids_cached_tx(cursor, direction_mode=mode)
                for mode in SHIFT_AUCTION_MODES
            )

    def _normalize_shift_auction_time_groups(self, raw_groups, starts_at, ends_at):
        """Validate the week's time groups coming from the manager's form.

        Only shape and window sanity here — membership is filtered against the
        validated participants later, inside the transaction.
        """
        groups = []
        if raw_groups is None:
            return None
        if not isinstance(raw_groups, (list, tuple)):
            raise ValueError("AUCTION_GROUP_INVALID")
        if len(raw_groups) > self.SHIFT_AUCTION_TIME_GROUP_LIMIT:
            raise ValueError("AUCTION_GROUP_LIMIT")
        for index, raw_group in enumerate(raw_groups):
            if not isinstance(raw_group, dict):
                raise ValueError("AUCTION_GROUP_INVALID")
            group_starts_at = raw_group.get('starts_at')
            if not group_starts_at:
                raise ValueError("AUCTION_GROUP_START_REQUIRED")
            group_ends_at = raw_group.get('ends_at') or None
            # A group whose own end (or the inherited main end) is not after its
            # start would never open for anybody — reject instead of storing it.
            effective_end = group_ends_at or ends_at
            if effective_end and effective_end <= group_starts_at:
                raise ValueError("AUCTION_GROUP_WINDOW_EMPTY")
            try:
                group_id = int(raw_group.get('id') or 0)
            except Exception:
                group_id = 0
            member_ids = []
            seen_members = set()
            for raw_member in (raw_group.get('operator_ids') or []):
                try:
                    member_id = int(raw_member)
                except Exception:
                    continue
                if member_id > 0 and member_id not in seen_members:
                    seen_members.add(member_id)
                    member_ids.append(member_id)
            groups.append({
                "id": group_id if group_id > 0 else None,
                "name": str(raw_group.get('name') or '').strip()[:80] or f"Группа {index + 1}",
                "starts_at": group_starts_at,
                "ends_at": group_ends_at,
                "self_schedule_enabled": bool(raw_group.get('self_schedule_enabled')),
                "operator_ids": member_ids,
            })
        return groups

    def _save_shift_auction_time_groups_tx(self, cursor, plan_id, groups, valid_operator_ids, updated_by=None):
        """Rewrite the week's groups; returns them serialized for the response.

        ``valid_operator_ids`` is everything this save validated against the
        direction — participants and group members alike — so a group may hold any
        operator of the direction. Membership is deduped across groups (the first
        group listed wins), so the DB-level "one group per operator per week"
        index can never be hit by a legitimate save.
        """
        if groups is None:
            return None
        if not groups:
            cursor.execute("DELETE FROM shift_auction_time_groups WHERE plan_id = %s", (plan_id,))
            return []

        cursor.execute("SELECT id FROM shift_auction_time_groups WHERE plan_id = %s", (plan_id,))
        existing_ids = {int(row[0]) for row in (cursor.fetchall() or [])}

        kept_ids = []
        assigned_members = set()
        member_rows = []
        for index, group in enumerate(groups):
            if group["id"] and group["id"] in existing_ids:
                cursor.execute("""
                    UPDATE shift_auction_time_groups
                    SET name = %s,
                        starts_at = %s,
                        ends_at = %s,
                        self_schedule_enabled = %s,
                        sort_order = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND plan_id = %s
                    RETURNING id
                """, (
                    group["name"], group["starts_at"], group["ends_at"],
                    group["self_schedule_enabled"], index, group["id"], plan_id,
                ))
            else:
                cursor.execute("""
                    INSERT INTO shift_auction_time_groups (
                        plan_id, name, starts_at, ends_at, self_schedule_enabled, sort_order, created_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    plan_id, group["name"], group["starts_at"], group["ends_at"],
                    group["self_schedule_enabled"], index, updated_by,
                ))
            group_id = int(cursor.fetchone()[0])
            kept_ids.append(group_id)
            for operator_id in group["operator_ids"]:
                if operator_id in valid_operator_ids and operator_id not in assigned_members:
                    assigned_members.add(operator_id)
                    member_rows.append((group_id, plan_id, operator_id))

        cursor.execute("""
            DELETE FROM shift_auction_time_groups
            WHERE plan_id = %s AND NOT (id = ANY(%s))
        """, (plan_id, kept_ids))
        cursor.execute("DELETE FROM shift_auction_time_group_members WHERE plan_id = %s", (plan_id,))
        if member_rows:
            execute_values(
                cursor,
                """
                INSERT INTO shift_auction_time_group_members (group_id, plan_id, operator_id)
                VALUES %s
                ON CONFLICT DO NOTHING
                """,
                member_rows
            )
        return self._get_shift_auction_time_groups_tx(cursor, plan_id, plan_only=True)

    def update_shift_auction_test_access(
        self,
        enabled=False,
        operator_ids=None,
        launch_note='',
        updated_by=None,
        starts_at=None,
        ends_at=None,
        schedule_plan_id=None,
        time_groups=None,
        direction_mode=SHIFT_AUCTION_MODE_LINE,
    ):
        mode = normalize_shift_auction_mode(direction_mode)
        settings_id = shift_auction_settings_row_id(mode)
        scope_sql, scope_params = shift_auction_direction_scope_sql(mode)
        if starts_at and ends_at and ends_at <= starts_at:
            raise ValueError("AUCTION_END_BEFORE_START")
        normalized_groups = self._normalize_shift_auction_time_groups(time_groups, starts_at, ends_at)
        normalized_ids = []
        seen = set()
        for raw_id in (operator_ids or []):
            try:
                operator_id = int(raw_id)
            except Exception:
                continue
            if operator_id > 0 and operator_id not in seen:
                seen.add(operator_id)
                normalized_ids.append(operator_id)
        # A time group takes any operator of the direction, not only those already
        # ticked as participants — joining a group is what puts them in the auction.
        for group in (normalized_groups or []):
            for member_id in group["operator_ids"]:
                if member_id not in seen:
                    seen.add(member_id)
                    normalized_ids.append(member_id)

        selected_period_row = None
        selected_plan_id = None
        if schedule_plan_id not in (None, ''):
            try:
                raw_plan_id = int(schedule_plan_id or 0)
            except Exception:
                raw_plan_id = 0
            if raw_plan_id <= 0:
                raise ValueError("AUCTION_PERIOD_NOT_FOUND")

        with self._get_cursor() as cursor:
            saved_shift_rows = []
            if schedule_plan_id not in (None, ''):
                selected_period_row = self._validate_shift_auction_period_tx(
                    cursor,
                    schedule_plan_id,
                    require_restartable=True,
                    direction_mode=mode
                )
                selected_plan_id = int(selected_period_row[0])
                cursor.execute("""
                    SELECT id, shift_date, start_time, end_time, rate_min, breaks
                    FROM resource_saved_schedule_shifts
                    WHERE plan_id = %s
                      AND COALESCE(meta->>'excludeFromAuction', 'false') <> 'true'
                    ORDER BY shift_date, start_minute, end_minute, id
                """, (selected_plan_id,))
                saved_shift_rows = cursor.fetchall() or []

            cursor.execute("""
                SELECT selected_schedule_plan_id, enabled, finished_at
                FROM shift_auction_test_access
                WHERE id = %s
                FOR UPDATE
            """, (settings_id,))
            current_settings_row = cursor.fetchone()
            current_selected_plan_id = current_settings_row[0] if current_settings_row else None
            current_enabled = bool(current_settings_row[1]) if current_settings_row and len(current_settings_row) > 1 else False
            current_finished_at = current_settings_row[2] if current_settings_row and len(current_settings_row) > 2 else None

            valid_ids = []
            if normalized_ids:
                cursor.execute(f"""
                    SELECT u.id
                    FROM users u
                    JOIN directions d ON d.id = u.direction_id
                    JOIN departments dep ON dep.id = d.department_id
                    WHERE u.id = ANY(%s)
                      AND LOWER(COALESCE(u.role, '')) = 'operator'
                      AND COALESCE(u.status, '') NOT IN ('fired', 'dismissal')
                      AND {scope_sql}
                """, (
                    normalized_ids,
                    *scope_params,
                ))
                valid_ids = [int(row[0]) for row in (cursor.fetchall() or [])]

            should_refresh_lots = False
            if selected_plan_id is not None:
                cursor.execute("""
                    SELECT
                        COUNT(*)::int AS total_lots,
                        COUNT(*) FILTER (WHERE source_schedule_plan_id = %s)::int AS selected_plan_lots
                    FROM shift_auction_test_lots
                    WHERE COALESCE(direction_mode, 'line') = %s
                """, (selected_plan_id, mode))
                lot_counts = cursor.fetchone() or (0, 0)
                total_lots = int(lot_counts[0] or 0)
                selected_plan_lots = int(lot_counts[1] or 0)
                should_refresh_lots = (
                    current_selected_plan_id != selected_plan_id
                    or total_lots == 0
                    or selected_plan_lots != total_lots
                )

            next_selected_plan_id = selected_plan_id if selected_plan_id is not None else current_selected_plan_id
            # Saving an enabled auction after a previous explicit finish starts a
            # new run even when the weekly plan (and therefore its lots) stays the
            # same. Otherwise stale finished_at wins over the new future start and
            # the UI correctly—but unexpectedly—shows "closed" with no countdown.
            should_reset_run_state = bool(
                should_refresh_lots
                or (bool(enabled) and (not current_enabled or current_finished_at is not None))
            )
            # Top-up belongs to one auction run, not to the singleton settings
            # row forever. Reset it whenever another period is loaded or a
            # disabled auction is started again.
            should_reset_topup = should_reset_run_state
            cursor.execute("""
                UPDATE shift_auction_test_access
                SET enabled = %s,
                    launch_note = %s,
                    starts_at = %s,
                    ends_at = %s,
                    selected_schedule_plan_id = %s,
                    paused_at = CASE WHEN %s THEN NULL ELSE paused_at END,
                    finished_at = CASE WHEN %s THEN NULL ELSE finished_at END,
                    published_to_work_schedules_at = CASE WHEN %s THEN NULL ELSE published_to_work_schedules_at END,
                    published_to_work_schedules_by = CASE WHEN %s THEN NULL ELSE published_to_work_schedules_by END,
                    topup_started_at = CASE WHEN %s THEN NULL ELSE topup_started_at END,
                    topup_started_by = CASE WHEN %s THEN NULL ELSE topup_started_by END,
                    updated_by = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                bool(enabled),
                str(launch_note or '').strip()[:1000],
                starts_at,
                ends_at,
                next_selected_plan_id,
                should_reset_run_state,
                should_reset_run_state,
                should_reset_run_state,
                should_reset_run_state,
                should_reset_topup,
                should_reset_topup,
                updated_by,
                settings_id,
            ))

            if should_refresh_lots:
                cursor.execute("""
                    DELETE FROM shift_auction_test_events
                    WHERE event_type = 'lot_claimed'
                      AND NULLIF(payload->'lot'->>'source_schedule_plan_id', '')::INTEGER = %s
                """, (selected_plan_id,))
                # Части смен, взятые в ходе прошлого прогона, живут отдельной таблицей —
                # без этой чистки они пережили бы пересев лотов и вычитались из нормы.
                cursor.execute("""
                    DELETE FROM shift_auction_historical_claims hc
                    USING shift_auction_test_lots l
                    WHERE l.source_schedule_plan_id = hc.plan_id
                      AND l.source_schedule_shift_id = hc.source_schedule_shift_id
                      AND COALESCE(l.direction_mode, 'line') = %s
                      AND COALESCE(hc.claim_stage, 'post_auction') = %s
                """, (mode, self.SHIFT_AUCTION_CLAIM_STAGE_AUCTION))
                cursor.execute(
                    "DELETE FROM shift_auction_test_day_offs WHERE COALESCE(direction_mode, 'line') = %s",
                    (mode,)
                )
                cursor.execute(
                    "DELETE FROM shift_auction_test_lots WHERE COALESCE(direction_mode, 'line') = %s",
                    (mode,)
                )
                if saved_shift_rows:
                    execute_values(
                        cursor,
                        """
                        INSERT INTO shift_auction_test_lots (
                            shift_date, start_time, end_time, rate_min,
                            breaks, source_schedule_plan_id, source_schedule_shift_id,
                            direction_mode
                        )
                        VALUES %s
                        """,
                        [
                            (
                                row[1],
                                row[2],
                                row[3],
                                row[4],
                                Json(row[5] if isinstance(row[5], list) else []),
                                selected_plan_id,
                                row[0],
                                mode,
                            )
                            for row in saved_shift_rows
                        ],
                    )

            cursor.execute(
                "DELETE FROM shift_auction_test_participants WHERE COALESCE(direction_mode, 'line') = %s",
                (mode,)
            )
            if valid_ids:
                rows = [(operator_id, updated_by, mode) for operator_id in valid_ids]
                execute_values(
                    cursor,
                    """
                    INSERT INTO shift_auction_test_participants (operator_id, added_by, direction_mode)
                    VALUES %s
                    ON CONFLICT (operator_id) DO UPDATE
                        SET added_by = EXCLUDED.added_by,
                            direction_mode = EXCLUDED.direction_mode
                    """,
                    rows
                )

            # Time groups belong to their week, so they are written against the
            # plan being saved and left untouched for every other week.
            saved_groups = None
            if normalized_groups is not None:
                if next_selected_plan_id is None:
                    if normalized_groups:
                        raise ValueError("AUCTION_GROUP_WEEK_REQUIRED")
                else:
                    saved_groups = self._save_shift_auction_time_groups_tx(
                        cursor,
                        next_selected_plan_id,
                        normalized_groups,
                        set(valid_ids),
                        updated_by=updated_by,
                    )

            self._insert_shift_auction_test_event(cursor, "settings_updated", {
                "enabled": bool(enabled),
                "starts_at": starts_at.isoformat() if starts_at else None,
                "ends_at": ends_at.isoformat() if ends_at else None,
                "time_groups": [
                    {
                        "id": group["id"],
                        "name": group["name"],
                        "starts_at": group["starts_at"],
                        "ends_at": group["ends_at"],
                        "self_schedule_enabled": group["self_schedule_enabled"],
                        "operator_ids": group["operator_ids"],
                    }
                    for group in (saved_groups or [])
                ],
                "selected_operator_ids": valid_ids,
                "selected_schedule_plan_id": next_selected_plan_id,
                "lots_refreshed": should_refresh_lots,
                "run_state_reset": should_reset_run_state,
                "topup_reset": should_reset_topup,
                "date_from": selected_period_row[1].strftime('%Y-%m-%d') if selected_period_row and selected_period_row[1] else None,
                "date_to": selected_period_row[2].strftime('%Y-%m-%d') if selected_period_row and selected_period_row[2] else None,
            # Событие — своему направлению: без явного режима оно уходило в поток
            # линии, и экран чата (и Теза) не узнавал о собственных настройках.
            }, direction_mode=mode)

        _invalidate_shift_auction_runtime_caches(include_participants=True, direction_mode=mode)
        return self.get_shift_auction_test_access(current_user_id=updated_by, direction_mode=mode)

    def control_shift_auction_test(self, action, updated_by=None, direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        settings_id = shift_auction_settings_row_id(mode)
        action_norm = str(action or '').strip().lower()
        if action_norm not in {'pause', 'resume', 'finish'}:
            raise ValueError("INVALID_AUCTION_CONTROL_ACTION")

        now = datetime.now()
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT enabled, starts_at, ends_at, paused_at, finished_at, selected_schedule_plan_id
                FROM shift_auction_test_access
                WHERE id = %s
                FOR UPDATE
            """, (settings_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("AUCTION_NOT_AVAILABLE")

            enabled, starts_at, ends_at, paused_at, finished_at, selected_plan_id = row
            # The run is "live" (and therefore pausable) from the earliest time group
            # start until the latest group end — not only inside the main window.
            earliest_start, latest_end = self._shift_auction_run_bounds_tx(
                cursor, selected_plan_id, starts_at, ends_at
            )
            current_status = self._get_shift_auction_test_status(
                enabled,
                earliest_start,
                latest_end,
                paused_at,
                finished_at
            )

            if action_norm == 'pause':
                if current_status != 'open':
                    raise ValueError("AUCTION_CANNOT_PAUSE")
                cursor.execute("""
                    UPDATE shift_auction_test_access
                    SET paused_at = %s,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (now, updated_by, settings_id))
                event = self._insert_shift_auction_test_event(cursor, "auction_paused", {
                    "paused_at": now.isoformat(),
                    "updated_by": updated_by,
                }, direction_mode=mode)
            elif action_norm == 'resume':
                if current_status != 'paused' or paused_at is None:
                    raise ValueError("AUCTION_NOT_PAUSED")
                pause_delta = now - paused_at
                next_ends_at = ends_at + pause_delta if ends_at else None
                cursor.execute("""
                    UPDATE shift_auction_test_access
                    SET paused_at = NULL,
                        ends_at = %s,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (next_ends_at, updated_by, settings_id))
                # Groups with their own end lose the same minutes to the pause as
                # the main window, otherwise a pause would silently shorten them.
                if selected_plan_id is not None:
                    cursor.execute("""
                        UPDATE shift_auction_time_groups
                        SET ends_at = ends_at + %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE plan_id = %s
                          AND ends_at IS NOT NULL
                    """, (pause_delta, selected_plan_id))
                event = self._insert_shift_auction_test_event(cursor, "auction_resumed", {
                    "resumed_at": now.isoformat(),
                    "paused_seconds": max(0, int(pause_delta.total_seconds())),
                    "ends_at": next_ends_at.isoformat() if next_ends_at else None,
                    "updated_by": updated_by,
                }, direction_mode=mode)
            else:
                if current_status in {'disabled', 'closed'}:
                    raise ValueError("AUCTION_ALREADY_CLOSED")
                cursor.execute("""
                    UPDATE shift_auction_test_access
                    SET paused_at = NULL,
                        finished_at = %s,
                        topup_started_at = NULL,
                        topup_started_by = NULL,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (now, updated_by, settings_id))
                event = self._insert_shift_auction_test_event(cursor, "auction_finished", {
                    "finished_at": now.isoformat(),
                    "updated_by": updated_by,
                }, direction_mode=mode)

        return {
            "event": event,
            "snapshot": self.get_shift_auction_test_snapshot(
                current_user_id=updated_by,
                include_admin_fields=True,
                direction_mode=mode
            ),
        }

    def set_shift_auction_test_topup(self, enabled, updated_by=None, direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        settings_id = shift_auction_settings_row_id(mode)
        # Toggle the "top-up" claim mode: when enabled, operators may claim
        # additional shifts that do not time-overlap with their existing claims
        # even if the total exceeds their norm. Only the admin can flip it.
        enable = bool(enabled)
        now = datetime.now()
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT enabled, starts_at, ends_at, paused_at, finished_at,
                       topup_started_at, selected_schedule_plan_id
                FROM shift_auction_test_access
                WHERE id = %s
                FOR UPDATE
            """, (settings_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("AUCTION_NOT_AVAILABLE")
            current_topup_started_at = row[5]
            selected_schedule_plan_id = row[6]
            # Top-up is a run-wide switch: it may be flipped while any time group
            # window is still open, not only inside the main one.
            run_starts_at, run_ends_at = self._shift_auction_run_bounds_tx(
                cursor, selected_schedule_plan_id, row[1], row[2]
            )
            current_status = self._get_shift_auction_test_status(row[0], run_starts_at, run_ends_at, row[3], row[4])

            if enable:
                if current_status != 'open':
                    raise ValueError("AUCTION_NOT_OPEN")
                if current_topup_started_at is not None:
                    raise ValueError("TOPUP_ALREADY_ACTIVE")
                cursor.execute("""
                    UPDATE shift_auction_test_access
                    SET topup_started_at = %s,
                        topup_started_by = %s,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING topup_started_at
                """, (now, updated_by, updated_by, settings_id))
                started_at_row = cursor.fetchone()
                topup_started_at = started_at_row[0] if started_at_row else now
                event = self._insert_shift_auction_test_event(cursor, "auction_topup_started", {
                    "topup_started_at": topup_started_at.isoformat() if topup_started_at else None,
                    "plan_id": selected_schedule_plan_id,
                    "updated_by": updated_by,
                }, direction_mode=mode)
            else:
                if current_topup_started_at is None:
                    raise ValueError("TOPUP_NOT_ACTIVE")
                cursor.execute("""
                    UPDATE shift_auction_test_access
                    SET topup_started_at = NULL,
                        topup_started_by = NULL,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (updated_by, settings_id))
                event = self._insert_shift_auction_test_event(cursor, "auction_topup_stopped", {
                    "stopped_at": now.isoformat(),
                    "plan_id": selected_schedule_plan_id,
                    "updated_by": updated_by,
                }, direction_mode=mode)

        return {
            "event": event,
            "snapshot": self.get_shift_auction_test_snapshot(
                current_user_id=updated_by,
                include_admin_fields=True,
                direction_mode=mode
            ),
        }

    def set_shift_auction_test_rate_lock(self, enabled, updated_by=None, direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        settings_id = shift_auction_settings_row_id(mode)
        # Toggle the "own rate only" claim mode: when enabled, operators may
        # claim only lots whose rate group matches their own rate. Unlike
        # top-up this is a standing setting, so it can be flipped at any time.
        enable = bool(enabled)
        now = datetime.now()
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT COALESCE(rate_lock_enabled, FALSE), selected_schedule_plan_id
                FROM shift_auction_test_access
                WHERE id = %s
                FOR UPDATE
            """, (settings_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("AUCTION_NOT_AVAILABLE")
            current_enabled = bool(row[0])
            selected_schedule_plan_id = row[1]
            if current_enabled != enable:
                cursor.execute("""
                    UPDATE shift_auction_test_access
                    SET rate_lock_enabled = %s,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (enable, updated_by, settings_id))
                event = self._insert_shift_auction_test_event(cursor, "auction_rate_lock_updated", {
                    "enabled": enable,
                    "changed_at": now.isoformat(),
                    "plan_id": selected_schedule_plan_id,
                    "updated_by": updated_by,
                }, direction_mode=mode)
            else:
                event = None

        return {
            "event": event,
            "snapshot": self.get_shift_auction_test_snapshot(
                current_user_id=updated_by,
                include_admin_fields=True,
                direction_mode=mode
            ),
        }

    def _merge_shift_auction_claimed_shifts_for_publish(self, shifts):
        ranges = []
        for shift in shifts or []:
            if not isinstance(shift, dict):
                continue
            start_time_value = shift.get("start_time")
            end_time_value = shift.get("end_time")
            if start_time_value is None or end_time_value is None:
                continue
            start_min, end_min = self._schedule_interval_minutes(start_time_value, end_time_value)
            if end_min <= start_min:
                continue
            ranges.append({"start": start_min, "end": end_min})

        if not ranges:
            return []

        ranges.sort(key=lambda item: (item["start"], item["end"]))
        merged = []
        for item in ranges:
            if not merged or item["start"] > merged[-1]["end"]:
                merged.append(dict(item))
                continue
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])

        result = []
        for item in merged:
            result.append({
                "start_time": self._normalize_schedule_time(_minutes_to_time(item["start"]), "start_time"),
                "end_time": self._normalize_schedule_time(_minutes_to_time(item["end"]), "end_time"),
            })
        return result

    def _resolve_post_auction_merged_shift_range(self, existing_shifts, new_start_min, new_end_min):
        merged_start_min = int(new_start_min)
        merged_end_min = int(new_end_min)
        merge_ids = []
        remaining = []

        for sh_id, ex_start, ex_end in existing_shifts or []:
            ex_start_min, ex_end_min = self._schedule_interval_minutes(ex_start, ex_end)
            remaining.append({
                "id": int(sh_id),
                "start": ex_start_min,
                "end": ex_end_min,
            })

        changed = True
        while changed:
            changed = False
            next_remaining = []
            for item in remaining:
                if merged_start_min < item["end"] and item["start"] < merged_end_min:
                    raise ValueError("SHIFT_OVERLAPS_EXISTING")
                if item["end"] == merged_start_min or merged_end_min == item["start"]:
                    merge_ids.append(item["id"])
                    merged_start_min = min(merged_start_min, item["start"])
                    merged_end_min = max(merged_end_min, item["end"])
                    changed = True
                else:
                    next_remaining.append(item)
            remaining = next_remaining

        return merged_start_min, merged_end_min, merge_ids

    def _split_post_auction_shift_slice_tx(self, cursor, operator_id, shift_date,
                                           slice_start_time, slice_end_time):
        """
        Корректное «разъединение» при отмене добора — операция, обратная слиянию в
        _resolve_post_auction_merged_shift_range. Находит смену графика, которая
        содержит отменяемый отрезок (при взятии добор мог слиться с соседней сменой
        в один блок), удаляет её и заново создаёт оставшиеся части (0, 1 или 2).

        Для каждой оставшейся части перерывы пересчитываются по ТЕКУЩИМ правилам
        направления (_save_shift_tx с breaks=None) — чтобы на укоротившейся смене не
        остался перерыв от объединённого блока и у операторов не было проблем позже.

        Работает в минутах с учётом смен через полночь: отрезок выравнивается в
        систему координат смены сдвигом на сутки. Возвращает True, если содержащая
        смена найдена и обработана, иначе False (вызывающий код делает запасное
        точное удаление).
        """
        if not (shift_date and slice_start_time and slice_end_time):
            return False

        def _slice_minute(value):
            if isinstance(value, dt_time):
                return value.hour * 60 + value.minute
            return _time_to_minutes(str(value))

        DAY = 24 * 60
        raw_start = _slice_minute(slice_start_time)
        raw_end = _slice_minute(slice_end_time)

        cursor.execute("""
            SELECT id, start_time, end_time
            FROM work_shifts
            WHERE operator_id = %s AND shift_date = %s
            ORDER BY start_time
        """, (operator_id, shift_date))
        shifts = cursor.fetchall() or []

        for sh_id, ws_start, ws_end in shifts:
            ws_start_min, ws_end_min = self._schedule_interval_minutes(ws_start, ws_end)
            s_start = raw_start
            while s_start < ws_start_min:
                s_start += DAY
            s_end = raw_end
            while s_end <= s_start:
                s_end += DAY
            # Отрезок добора должен целиком лежать внутри этой смены.
            if s_start < ws_start_min or s_end > ws_end_min:
                continue

            # Снимок до удаления: иначе прошедшие перерывы уедут каскадом вместе со сменой.
            day_breaks_snapshot = self._load_day_shift_breaks_tx(cursor, operator_id, shift_date)
            cursor.execute("DELETE FROM work_shifts WHERE id = %s", (sh_id,))
            remaining = []
            if s_start > ws_start_min:
                remaining.append((ws_start_min, s_start))
            if s_end < ws_end_min:
                remaining.append((s_end, ws_end_min))
            for part_start, part_end in remaining:
                start_obj = self._normalize_schedule_time(_minutes_to_time(part_start % DAY), 'start')
                end_obj = self._normalize_schedule_time(_minutes_to_time(part_end), 'end')
                self._save_shift_tx(
                    cursor=cursor,
                    operator_id=operator_id,
                    shift_date=shift_date,
                    start_time=start_obj,
                    end_time=end_obj,
                    breaks=None,
                    shift_type=None,
                    day_breaks_snapshot=day_breaks_snapshot,
                )
            return True
        return False

    def _normalize_post_auction_claim_range(self, source_start_time, source_end_time, claim_start_time=None, claim_end_time=None):
        source_start_min, source_end_min = self._schedule_interval_minutes(source_start_time, source_end_time)
        start_obj = self._normalize_schedule_time(
            claim_start_time if claim_start_time not in (None, '') else source_start_time,
            'claim_start_time'
        )
        end_obj = self._normalize_schedule_time(
            claim_end_time if claim_end_time not in (None, '') else source_end_time,
            'claim_end_time'
        )

        claim_start_min = _time_to_minutes(start_obj.strftime('%H:%M'))
        claim_end_min = _time_to_minutes(end_obj.strftime('%H:%M'))
        if source_end_min > 24 * 60 and claim_start_min < source_start_min:
            claim_start_min += 24 * 60
        if claim_end_min <= claim_start_min:
            claim_end_min += 24 * 60

        if claim_end_min <= claim_start_min:
            raise ValueError("INVALID_PARTIAL_SHIFT_RANGE")
        if claim_start_min < source_start_min or claim_end_min > source_end_min:
            raise ValueError("PARTIAL_SHIFT_OUT_OF_RANGE")

        return {
            "start_time": start_obj,
            "end_time": end_obj,
            "start_minute": int(claim_start_min),
            "end_minute": int(claim_end_min),
            "is_partial": bool(claim_start_min != source_start_min or claim_end_min != source_end_min),
        }

    @staticmethod
    def _shift_auction_hhmm(value):
        """'HH:MM' из time, datetime или строки — источники дают все три вида."""
        if value is None:
            return ''
        if hasattr(value, 'strftime'):
            return value.strftime('%H:%M')
        return str(value)[:5]

    @classmethod
    def _is_shift_auction_night(cls, start_time, end_time, direction_mode=None):
        """Ночная ли смена. В СЗоВ ночь — ровно «20*08»; у направления, переданного
        явно, — пары из shift_auction_night_shifts (у Тез КЦ ещё и «21*09»)."""
        start = cls._shift_auction_hhmm(start_time)
        end = cls._shift_auction_hhmm(end_time)
        if start == cls.SHIFT_AUCTION_NIGHT_START_HHMM and end == cls.SHIFT_AUCTION_NIGHT_END_HHMM:
            return True
        if direction_mode is None:
            return False
        return (start, end) in shift_auction_night_shifts(direction_mode)

    @classmethod
    def _shift_auction_adjacent_night_date(cls, claimed_rows, shift_date, start_time, end_time,
                                           direction_mode=None):
        """Дата соседней ночи у того же оператора, иначе None.

        Две ночи 20:00–08:00 подряд брать нельзя: первая кончается в 08:00, вторая
        начинается в 20:00 ТОГО ЖЕ дня — двенадцать часов между сменами и двое
        суток без нормального сна. Календарная проверка «день уже занят» этого не
        ловит: у ночей разные даты начала, хвост первой лежит в дне второй.
        У Тез КЦ то же правило действует и для его ночи «21*09» (direction_mode).

        Смотрим обе стороны — и «вчера была ночь», и «на завтра ночь уже взята»:
        смены разбирают в произвольном порядке, и запрет обязан работать одинаково.
        """
        if not cls._is_shift_auction_night(start_time, end_time, direction_mode):
            return None
        try:
            target = datetime.strptime(str(shift_date)[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return None
        neighbours = {target - timedelta(days=1), target + timedelta(days=1)}
        for row in (claimed_rows or []):
            if not cls._is_shift_auction_night(row.get('start_time'), row.get('end_time'), direction_mode):
                continue
            try:
                other = datetime.strptime(str(row.get('shift_date') or '')[:10], '%Y-%m-%d').date()
            except (TypeError, ValueError):
                continue
            if other in neighbours:
                return other.isoformat()
        return None

    @staticmethod
    def _shift_auction_day_ordinal(shift_date):
        """Дата смены как номер дня — чтобы считать время сквозным, а не внутри суток."""
        try:
            return datetime.strptime(str(shift_date)[:10], '%Y-%m-%d').date().toordinal()
        except (TypeError, ValueError):
            return None

    def _shift_auction_claim_conflict(self, claimed_rows, shift_date, start_minute, end_minute):
        """Уже взятая смена, которая перекрывается с кандидатом, иначе None.

        Правило владельца для чата: в один день можно взять ЕЩЁ часы, если они не
        накладываются на уже взятое (недельная норма при этом остаётся). Значит
        вместо «одна смена в день» нужна точная проверка пересечения.

        Считаем по СКВОЗНОМУ времени (номер дня × 1440 + минуты), а не внутри
        одних суток: ночная смена 20:00–08:00 кончается уже в следующем дне, и
        утренняя смена того дня наложилась бы на её хвост, оставаясь при этом
        другой `shift_date` — посуточное сравнение такую пару пропускает.
        Минуты кандидата приходят из окна ЛОТА и у ночной смены уже больше 1440.
        """
        candidate_day = self._shift_auction_day_ordinal(shift_date)
        if candidate_day is None:
            return None
        candidate_start = candidate_day * 1440 + start_minute
        candidate_end = candidate_day * 1440 + end_minute
        for claimed in (claimed_rows or []):
            other_day = self._shift_auction_day_ordinal(claimed.get('shift_date'))
            if other_day is None:
                continue
            other_start_minute, other_end_minute = self._schedule_interval_minutes(
                claimed.get('start_time'), claimed.get('end_time')
            )
            other_start = other_day * 1440 + other_start_minute
            other_end = other_day * 1440 + other_end_minute
            if candidate_start < other_end and other_start < candidate_end:
                return claimed
        return None

    def _create_post_auction_remainder_lots(self, cursor, parent_lot_id, shift_date,
                                            full_start_time, full_end_time,
                                            claim_start_min, claim_end_min,
                                            rate_min, breaks, source_plan_id, source_shift_id=None):
        """
        После частичного добора вернуть незабранный остаток смены в аукцион:
        для каждого свободного промежутка слева/справа от взятого куска создаём
        новый доступный лот (status='available'), доступный другим операторам.

        Остаточные лоты несут тот же source_schedule_shift_id, что и исходная смена —
        так в мониторинге они группируются с ней (видно «кто какую часть взял»), а
        повторный claim не конфликтует благодаря PK (plan, shift, claimed_by). В
        снапшоте source_*_minute для них обнуляются (remainder_of_lot_id IS NOT NULL),
        поэтому фронт считает диапазон по их собственным start/end (по промежутку).
        Связь с родителем хранится в remainder_of_lot_id для свода в admin_unclaim.
        """
        full_start_min, full_end_min = self._schedule_interval_minutes(full_start_time, full_end_time)
        candidate_gaps = []
        if claim_start_min > full_start_min:
            candidate_gaps.append((full_start_min, claim_start_min))
        if full_end_min > claim_end_min:
            candidate_gaps.append((claim_end_min, full_end_min))
        # Re-offer only gaps that are long enough AND stay within a single calendar day.
        # A gap that extends past midnight (gap_end_min > 1440) cannot be unambiguously
        # stored on the original shift_date, so that sliver is left unallocated.
        gaps = [
            (gap_start, gap_end) for (gap_start, gap_end) in candidate_gaps
            if gap_end - gap_start >= self.POST_AUCTION_MIN_REMAINDER_MINUTES
            and gap_end <= 24 * 60
        ]
        if not gaps:
            return []

        break_list = breaks if isinstance(breaks, list) else []
        created_ids = []
        for gap_start_min, gap_end_min in gaps:
            gap_start_obj = self._normalize_schedule_time(
                _minutes_to_time(gap_start_min % (24 * 60)), 'start'
            )
            gap_end_obj = self._normalize_schedule_time(
                _minutes_to_time(gap_end_min % (24 * 60)), 'end'
            )
            gap_breaks = [
                brk for brk in break_list
                if self._break_overlaps_minute_range(brk, gap_start_min, gap_end_min)
            ]
            cursor.execute("""
                INSERT INTO shift_auction_test_lots (
                    shift_date, start_time, end_time, rate_min, breaks,
                    source_schedule_plan_id, source_schedule_shift_id,
                    status, remainder_of_lot_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'available', %s)
                RETURNING id
            """, (shift_date, gap_start_obj, gap_end_obj, rate_min, Json(gap_breaks),
                  source_plan_id, source_shift_id, parent_lot_id))
            new_row = cursor.fetchone()
            if new_row:
                created_ids.append(int(new_row[0]))
        return created_ids

    def _consolidate_post_auction_lots(self, cursor):
        """
        Привести лоты к единой модели «один лот на смену»: удалить отдельные остаточные
        лоты (наследие прежней логики) и СНОВА ОТКРЫТЬ частично-взятые лоты — их взятые
        части лежат в shift_auction_historical_claims, а сам лот показывает свободную
        часть. Полностью взятые остаются 'claimed'. Идемпотентно; запускается на старте.
        """
        try:
            cursor.execute("DELETE FROM shift_auction_test_lots WHERE remainder_of_lot_id IS NOT NULL")
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            pass
        try:
            cursor.execute("""
                SELECT l.id, l.start_time, l.end_time, l.source_schedule_plan_id, l.source_schedule_shift_id
                FROM shift_auction_test_lots l
                WHERE COALESCE(l.post_auction_claimed, FALSE) = TRUE
                  AND l.source_schedule_shift_id IS NOT NULL
            """)
            rows = cursor.fetchall() or []
        except Exception as exc:
            if _is_schema_lock_conflict(exc):
                raise
            return
        for lot_id, start_time, end_time, plan_id, shift_id in rows:
            try:
                cursor.execute("""
                    SELECT claimed_start_time, claimed_end_time
                    FROM shift_auction_historical_claims
                    WHERE plan_id = %s AND source_schedule_shift_id = %s
                      AND claimed_start_time IS NOT NULL AND claimed_end_time IS NOT NULL
                """, (plan_id, shift_id))
                claims = cursor.fetchall() or []
                full_start_min, full_end_min = self._schedule_interval_minutes(start_time, end_time)
                claimed_ranges = []
                for cs, ce in claims:
                    rng = self._normalize_post_auction_claim_range(
                        start_time, end_time, cs.strftime('%H:%M'), ce.strftime('%H:%M')
                    )
                    claimed_ranges.append((rng["start_minute"], rng["end_minute"]))
                free_gaps = [g for g in self._subtract_ranges(full_start_min, full_end_min, claimed_ranges) if g[1] - g[0] > 0]
                # Only re-open if we actually know the taken parts AND something is free.
                if claimed_ranges and free_gaps:
                    cursor.execute("""
                        UPDATE shift_auction_test_lots
                        SET status = 'available', claimed_by = NULL, claimed_at = NULL,
                            post_auction_claimed = FALSE, post_claim_start_time = NULL,
                            post_claim_end_time = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                    """, (lot_id,))
            except Exception as exc:
                if _is_schema_lock_conflict(exc):
                    raise
                continue

    @staticmethod
    def _subtract_ranges(start_min, end_min, busy_ranges):
        """Free sub-ranges of [start_min, end_min] after removing the busy ranges."""
        free = [(int(start_min), int(end_min))]
        for raw_start, raw_end in sorted(busy_ranges):
            b_start = max(int(start_min), int(raw_start))
            b_end = min(int(end_min), int(raw_end))
            if b_end <= b_start:
                continue
            next_free = []
            for f_start, f_end in free:
                if b_end <= f_start or b_start >= f_end:
                    next_free.append((f_start, f_end))
                    continue
                if b_start > f_start:
                    next_free.append((f_start, b_start))
                if b_end < f_end:
                    next_free.append((b_end, f_end))
            free = next_free
        return free

    def publish_shift_auction_test_to_work_schedules(self, updated_by=None, direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        settings_id = shift_auction_settings_row_id(mode)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT enabled, starts_at, ends_at, paused_at, finished_at, selected_schedule_plan_id
                FROM shift_auction_test_access
                WHERE id = %s
                FOR UPDATE
            """, (settings_id,))
            settings_row = cursor.fetchone()
            if not settings_row:
                raise ValueError("AUCTION_NOT_AVAILABLE")
            selected_schedule_plan_id = int(settings_row[5]) if settings_row[5] is not None else None
            # "Closed" must hold for the whole run: a late time group may still be
            # picking shifts after the main window has ended.
            publish_starts_at, publish_ends_at = self._shift_auction_run_bounds_tx(
                cursor, selected_schedule_plan_id, settings_row[1], settings_row[2]
            )
            if self._get_shift_auction_test_status(
                settings_row[0], publish_starts_at, publish_ends_at, settings_row[3], settings_row[4]
            ) != "closed":
                raise ValueError("AUCTION_NOT_CLOSED")

            lot_dates = self._get_shift_auction_lot_dates_tx(cursor, direction_mode=mode)
            if not lot_dates:
                raise ValueError("AUCTION_NO_LOTS")
            # Прошедший период в графики не сохраняем: публикация стирает дни
            # участников и пишет взятое заново, а у прошедших дней уже есть
            # отработанные часы — пересчёт обнулил бы их вместе со сменами.
            # 04.10.2026 так стёрся сентябрь у 36 операторов Основы: опубликовали
            # давно остановленный аукцион линии на 14–20.09.
            if max(lot_dates) < self._almaty_now().date():
                raise ValueError("AUCTION_PERIOD_ALREADY_PASSED")

            operator_ids = sorted(self._get_shift_auction_participant_ids_tx(cursor, direction_mode=mode))
            if not operator_ids:
                raise ValueError("AUCTION_NO_PARTICIPANTS")

            # В графики уезжают и целые лоты, и ЧАСТИ смен, взятые в ходе аукциона
            # (чат): последние лежат в shift_auction_historical_claims, а сам лот при
            # этом мог остаться 'available' — свободный кусок ещё ждёт хозяина.
            cursor.execute(f"""
                SELECT claimed_by, shift_date, start_time, end_time
                FROM (
                    SELECT l.claimed_by, l.shift_date, l.start_time, l.end_time,
                           l.claimed_at AS ordered_at, l.id AS ordered_id
                    FROM shift_auction_test_lots l
                    WHERE l.status = 'claimed'
                      AND l.claimed_by = ANY(%s)
                      AND COALESCE(l.direction_mode, 'line') = %s
                      AND NOT EXISTS (
                          SELECT 1 FROM shift_auction_historical_claims hc
                          WHERE hc.plan_id = l.source_schedule_plan_id
                            AND hc.source_schedule_shift_id = l.source_schedule_shift_id
                            AND hc.claimed_start_time IS NOT NULL
                            AND hc.claimed_end_time IS NOT NULL
                      )
                    UNION ALL
                    SELECT hc.claimed_by, {self.SHIFT_AUCTION_CLAIM_DATE_SQL} AS shift_date,
                           hc.claimed_start_time, hc.claimed_end_time,
                           hc.claimed_at AS ordered_at, sh.id AS ordered_id
                    FROM shift_auction_historical_claims hc
                    JOIN resource_saved_schedule_shifts sh
                      ON sh.id = hc.source_schedule_shift_id
                     AND sh.plan_id = hc.plan_id
                    WHERE hc.claimed_by = ANY(%s)
                      AND hc.claimed_start_time IS NOT NULL
                      AND hc.claimed_end_time IS NOT NULL
                      -- EXISTS, а не JOIN: несколько лотов на одну смену продублировали
                      -- бы взятый кусок и сохранили его в график дважды.
                      AND EXISTS (
                          SELECT 1 FROM shift_auction_test_lots l2
                          WHERE l2.source_schedule_plan_id = hc.plan_id
                            AND l2.source_schedule_shift_id = hc.source_schedule_shift_id
                            AND COALESCE(l2.direction_mode, 'line') = %s
                      )
                ) claimed
                ORDER BY claimed_by, shift_date, ordered_at, ordered_id
            """, (operator_ids, mode, operator_ids, mode))
            claimed_by_operator_date = {}
            for claimed_by, shift_date_value, start_time_value, end_time_value in (cursor.fetchall() or []):
                if claimed_by is None or shift_date_value is None:
                    continue
                claimed_by_operator_date.setdefault((int(claimed_by), shift_date_value), []).append({
                    "start_time": start_time_value,
                    "end_time": end_time_value,
                })
            # Никто ничего не взял — сохранять нечего: такая публикация только
            # очистила бы период у всех участников (так и было 04.10.2026).
            if not claimed_by_operator_date:
                raise ValueError("AUCTION_NOTHING_CLAIMED")

            cursor.execute("""
                SELECT operator_id, day_off_date
                FROM shift_auction_test_day_offs
                WHERE operator_id = ANY(%s)
                  AND COALESCE(direction_mode, 'line') = %s
                ORDER BY operator_id, day_off_date
            """, (operator_ids, mode))
            day_off_dates = {
                (int(operator_id), day_off_date)
                for operator_id, day_off_date in (cursor.fetchall() or [])
                if operator_id is not None and day_off_date is not None
            }

            summary = {
                "operators_total": len(operator_ids),
                "days_total": len(lot_dates),
                "cleared_days": 0,
                "shifts_saved": 0,
                "days_off_saved": 0,
                "blocked_days": 0,
            }

            first_date = min(lot_dates)
            last_date = max(lot_dates)
            lot_date_set = set(lot_dates)
            cursor.execute(
                """
                SELECT u.id, d.name
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE u.id = ANY(%s)
                """,
                (operator_ids,)
            )
            operator_direction_map = {
                int(op_id): direction_name
                for op_id, direction_name in (cursor.fetchall() or [])
                if op_id is not None
            }
            direction_rules_cache = {}
            direction_cross_gap_cache = {}

            def _direction_rules_for_sort(direction_name):
                direction_key = self._normalize_direction_key(direction_name)
                if direction_key not in direction_rules_cache:
                    direction_rules_cache[direction_key] = self._get_work_schedule_break_rules_for_direction_tx(
                        cursor,
                        direction_name
                    )
                return direction_rules_cache[direction_key]

            def _direction_cross_gap(direction_name):
                direction_key = self._normalize_direction_key(direction_name)
                if direction_key not in direction_cross_gap_cache:
                    direction_cross_gap_cache[direction_key] = self._get_break_cross_operator_gap_for_direction_tx(
                        cursor,
                        direction_name
                    )
                return direction_cross_gap_cache[direction_key]

            def _publish_shift_sort_key(item):
                start_min, end_min = self._schedule_interval_minutes(item["start_time"], item["end_time"])
                duration = max(0, end_min - start_min)
                direction_name = item.get("direction")
                break_durations = self._pick_break_durations_for_shift(
                    duration,
                    direction_name=direction_name,
                    direction_rules=_direction_rules_for_sort(direction_name)
                )
                edge_margin, min_gap = self._break_layout_spacing(start_min, end_min, break_durations)
                required_minutes = (
                    sum(max(0, int(x)) for x in (break_durations or []))
                    + (2 * int(edge_margin))
                    + (int(min_gap) * max(0, len(break_durations or []) - 1))
                )
                flexibility = duration - required_minutes
                return (
                    item["shift_date"],
                    self._normalize_direction_key(direction_name),
                    flexibility,
                    start_min,
                    end_min,
                    int(item["operator_id"])
                )

            shifts_to_save = []
            day_breaks_snapshots = {}
            publish_now = self._almaty_now()
            publish_direction_keys = {}
            # Публикация стирает неделю и собирает заново. В историю попадёт только
            # то, что реально изменилось: повторная публикация без правок молчит.
            audit_actor = self._schedule_change_actor(cursor, actor_id=updated_by, source='auction')
            audit_before = self._snapshot_schedule_days_tx(cursor, [
                (int(operator_id), lot_date)
                for operator_id in operator_ids
                for lot_date in lot_dates
            ])
            for operator_id in operator_ids:
                blocked_date_map = self._get_shift_auction_operator_blocked_date_map_tx(
                    cursor,
                    operator_id,
                    lot_dates=lot_dates
                )
                for lot_date in lot_dates:
                    # Снимок перерывов дня до очистки: прошедшие переписывать нельзя,
                    # а очистка уносит их вместе со сменой.
                    day_breaks_snapshots[(int(operator_id), lot_date.strftime('%Y-%m-%d'))] = (
                        self._load_day_shift_breaks_tx(cursor, operator_id, lot_date)
                    )
                    self._clear_day_schedule_tx(cursor, operator_id, lot_date)
                    summary["cleared_days"] += 1
                    lot_date_key = lot_date.strftime('%Y-%m-%d')
                    if lot_date_key in blocked_date_map:
                        summary["blocked_days"] += 1
                        continue

                    claimed_shifts = claimed_by_operator_date.get((operator_id, lot_date), [])
                    # Выходной ставим, только если в этот день ничего не взято: хвост
                    # ночи с прошлого дня приходит сюда уже после того, как человек
                    # отметил день выходным, и смена важнее отметки.
                    if (operator_id, lot_date) in day_off_dates and not claimed_shifts:
                        self._set_day_off_tx(cursor, operator_id, lot_date)
                        summary["days_off_saved"] += 1
                        continue

                    if not claimed_shifts:
                        continue
                    for claimed in self._merge_shift_auction_claimed_shifts_for_publish(claimed_shifts):
                        shifts_to_save.append({
                            "operator_id": operator_id,
                            "shift_date": lot_date,
                            "start_time": claimed["start_time"],
                            "end_time": claimed["end_time"],
                            "direction": operator_direction_map.get(operator_id)
                        })

                # Хвост ночи ПОСЛЕДНЕГО дня прогона лежит уже за периодом. Этот день
                # не наш, чистить его нельзя, но смену записать надо — иначе ночь с
                # воскресенья на понедельник уедет в график наполовину.
                for extra_date in sorted(
                    day for (claim_operator_id, day) in claimed_by_operator_date
                    if claim_operator_id == operator_id and day not in lot_date_set
                ):
                    for claimed in self._merge_shift_auction_claimed_shifts_for_publish(
                        claimed_by_operator_date[(operator_id, extra_date)]
                    ):
                        shifts_to_save.append({
                            "operator_id": operator_id,
                            "shift_date": extra_date,
                            "start_time": claimed["start_time"],
                            "end_time": claimed["end_time"],
                            "direction": operator_direction_map.get(operator_id)
                        })

            for item in sorted(shifts_to_save, key=_publish_shift_sort_key):
                item_date_key = item["shift_date"].strftime('%Y-%m-%d')
                self._save_shift_tx(
                    cursor=cursor,
                    operator_id=item["operator_id"],
                    shift_date=item["shift_date"],
                    start_time=item["start_time"],
                    end_time=item["end_time"],
                    breaks=None,
                    cross_gap_minutes=_direction_cross_gap(item.get("direction")),
                    now=publish_now,
                    day_breaks_snapshot=day_breaks_snapshots.get(
                        (int(item["operator_id"]), item_date_key)
                    ),
                    extra_occupied=self._extra_occupied_from_day_snapshots(
                        cursor=cursor,
                        day_breaks_snapshots=day_breaks_snapshots,
                        operator_id=item["operator_id"],
                        shift_date=item["shift_date"],
                        now=publish_now,
                        direction_key_cache=publish_direction_keys
                    ),
                )
                summary["shifts_saved"] += 1

            self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)

            for operator_id in operator_ids:
                self._recalculate_auto_daily_hours_tx(
                    cursor=cursor,
                    operator_ids=[operator_id],
                    start_date=first_date,
                    end_date=last_date
                )

            cursor.execute("""
                UPDATE shift_auction_test_access
                SET published_to_work_schedules_at = CURRENT_TIMESTAMP,
                    published_to_work_schedules_by = %s,
                    updated_by = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING published_to_work_schedules_at
            """, (updated_by, updated_by, settings_id))
            published_at_row = cursor.fetchone()
            published_at = published_at_row[0] if published_at_row else None

            if selected_schedule_plan_id and first_date and last_date:
                cursor.execute("""
                    INSERT INTO shift_auction_published_periods
                        (plan_id, date_from, date_to, published_at, published_by, participant_ids, updated_at)
                    VALUES (%s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP), %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (plan_id) DO UPDATE
                      SET date_from = EXCLUDED.date_from,
                          date_to = EXCLUDED.date_to,
                          published_at = EXCLUDED.published_at,
                          published_by = EXCLUDED.published_by,
                          participant_ids = EXCLUDED.participant_ids,
                          updated_at = CURRENT_TIMESTAMP
                """, (
                    selected_schedule_plan_id,
                    first_date,
                    last_date,
                    published_at,
                    updated_by,
                    operator_ids,
                ))
                cursor.execute("""
                    INSERT INTO shift_auction_historical_claims
                        (plan_id, source_schedule_shift_id, claimed_by, claimed_at)
                    SELECT source_schedule_plan_id, source_schedule_shift_id, claimed_by,
                           COALESCE(claimed_at, CURRENT_TIMESTAMP)
                    FROM shift_auction_test_lots
                    WHERE status = 'claimed'
                      AND source_schedule_plan_id = %s
                      AND source_schedule_shift_id IS NOT NULL
                      AND claimed_by IS NOT NULL
                      AND COALESCE(direction_mode, 'line') = %s
                    ON CONFLICT (plan_id, source_schedule_shift_id, claimed_by) DO NOTHING
                """, (selected_schedule_plan_id, mode))

            event = self._insert_shift_auction_test_event(cursor, "auction_published", {
                **summary,
                "published_at": published_at.isoformat() if published_at else None,
                "published_by": updated_by,
                "selected_schedule_plan_id": selected_schedule_plan_id,
                "date_from": first_date.strftime('%Y-%m-%d') if first_date else None,
                "date_to": last_date.strftime('%Y-%m-%d') if last_date else None,
            }, direction_mode=mode)

        return {
            "summary": summary,
            "event": event,
            "snapshot": self.get_shift_auction_test_snapshot(
                current_user_id=updated_by,
                include_admin_fields=True,
                direction_mode=mode
            ),
        }

    def get_shift_auction_entries_for_simulation(self, direction_mode=SHIFT_AUCTION_MODE_LINE):
        """
        Возвращает claimed лоты текущего аукциона в формате entries для Python-симуляции перерывов.
        Включает direction оператора из users → directions.
        Результат: [{operator_id, date (str 'YYYY-MM-DD'), direction (str|None),
                     shifts: [{'start': 'HH:MM', 'end': 'HH:MM'}]}]
        Отсортировано по operator_id, shift_date — детерминированный порядок,
        совпадающий с порядком обработки в publish_shift_auction_test_to_work_schedules.
        """
        mode = normalize_shift_auction_mode(direction_mode)
        with self._get_cursor() as cursor:
            operator_ids = sorted(self._get_shift_auction_participant_ids_tx(cursor, direction_mode=mode))
            if not operator_ids:
                return []

            cursor.execute("""
                SELECT l.claimed_by,
                       l.shift_date,
                       l.start_time,
                       l.end_time,
                       d.name AS direction_name
                FROM shift_auction_test_lots l
                JOIN shift_auction_test_participants p ON p.operator_id = l.claimed_by
                LEFT JOIN users u ON u.id = l.claimed_by
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE l.status = 'claimed'
                  AND l.claimed_by = ANY(%s)
                  AND COALESCE(l.direction_mode, 'line') = %s
                ORDER BY l.claimed_by, l.shift_date, l.claimed_at, l.id
            """, (operator_ids, mode))

            entry_map = {}
            for claimed_by, shift_date_value, start_time_value, end_time_value, direction_name in (cursor.fetchall() or []):
                if claimed_by is None or shift_date_value is None:
                    continue
                op_id = int(claimed_by)
                date_str = shift_date_value.strftime('%Y-%m-%d')
                key = (op_id, date_str)
                if key not in entry_map:
                    entry_map[key] = {
                        'operator_id': op_id,
                        'date': date_str,
                        'direction': direction_name,
                        'shifts': []
                    }
                entry_map[key]['shifts'].append({
                    'start': start_time_value.strftime('%H:%M'),
                    'end': end_time_value.strftime('%H:%M')
                })

            return [entry_map[k] for k in sorted(entry_map.keys())]

    def get_shift_auction_test_export_data(self, direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        settings_id = shift_auction_settings_row_id(mode)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT enabled, starts_at, ends_at, paused_at, finished_at, selected_schedule_plan_id
                FROM shift_auction_test_access
                WHERE id = %s
            """, (settings_id,))
            settings_row = cursor.fetchone()
            if not settings_row:
                raise ValueError("AUCTION_NOT_AVAILABLE")

            selected_period_row = self._get_shift_auction_period_row_tx(cursor, settings_row[5], direction_mode=mode)
            selected_period = self._serialize_shift_auction_period_row(selected_period_row)

            cursor.execute("""
                SELECT
                    l.id,
                    l.shift_date,
                    l.start_time,
                    l.end_time,
                    l.rate_min,
                    l.status,
                    l.claimed_by,
                    u.name AS claimed_by_name,
                    l.claimed_at,
                    l.breaks,
                    l.source_schedule_plan_id,
                    l.source_schedule_shift_id,
                    COALESCE(l.post_auction_claimed, FALSE) AS post_auction_claimed,
                    u.direction_id AS claimed_by_direction_id,
                    claimed_dir.name AS claimed_by_direction,
                    source_shift.start_minute AS source_start_minute,
                    source_shift.end_minute AS source_end_minute,
                    l.post_claim_start_time,
                    l.post_claim_end_time,
                    -- Keep column positions aligned with the serializer: row[19] is
                    -- claim_segments (unused here), row[20]/row[21] are added_by info,
                    -- row[22] marks a self-scheduled shift, row[23] is the shift kind.
                    '[]'::jsonb AS claim_segments,
                    l.added_by,
                    added_user.name AS added_by_name,
                    l.self_scheduled_by,
                    source_shift.meta->>'shiftKind' AS shift_kind
                FROM shift_auction_test_lots l
                LEFT JOIN users u ON u.id = l.claimed_by
                LEFT JOIN directions claimed_dir ON claimed_dir.id = u.direction_id
                LEFT JOIN resource_saved_schedule_shifts source_shift ON source_shift.id = l.source_schedule_shift_id
                LEFT JOIN users added_user ON added_user.id = l.added_by
                WHERE COALESCE(l.direction_mode, 'line') = %s
                ORDER BY l.shift_date, l.start_time, l.id
            """, (mode,))
            lot_rows = cursor.fetchall() or []
            if not lot_rows:
                raise ValueError("AUCTION_NO_LOTS")

            lots = [self._serialize_shift_auction_lot_row(row) for row in lot_rows]
            lot_dates = sorted({row[1] for row in lot_rows if row and row[1]})

            cursor.execute("""
                WITH report_operators AS (
                    SELECT operator_id AS id
                    FROM shift_auction_test_participants
                    WHERE COALESCE(direction_mode, 'line') = %s
                    UNION
                    SELECT claimed_by AS id
                    FROM shift_auction_test_lots
                    WHERE claimed_by IS NOT NULL
                      AND COALESCE(direction_mode, 'line') = %s
                )
                SELECT
                    u.id,
                    u.name,
                    u.rate,
                    u.status,
                    d.name AS direction_name,
                    u.supervisor_id,
                    s.name AS supervisor_name
                FROM report_operators ro
                JOIN users u ON u.id = ro.id
                LEFT JOIN directions d ON d.id = u.direction_id
                LEFT JOIN users s ON s.id = u.supervisor_id
                ORDER BY LOWER(COALESCE(u.name, '')), u.id
            """, (mode, mode))
            operator_rows = cursor.fetchall() or []
            operators = [
                {
                    "id": int(row[0]),
                    "name": row[1] or f"Operator #{row[0]}",
                    "rate": float(row[2]) if row[2] is not None else None,
                    "status": row[3] or "",
                    "direction": row[4] or "",
                    "supervisor_id": row[5],
                    "supervisor_name": row[6] or "",
                }
                for row in operator_rows
                if row and row[0] is not None
            ]

            cursor.execute("""
                SELECT operator_id, day_off_date
                FROM shift_auction_test_day_offs
                WHERE COALESCE(direction_mode, 'line') = %s
                ORDER BY operator_id, day_off_date
            """, (mode,))
            day_offs = [
                {
                    "operator_id": int(row[0]),
                    "date": row[1].strftime('%Y-%m-%d') if row[1] else None,
                }
                for row in (cursor.fetchall() or [])
                if row and row[0] is not None and row[1] is not None
            ]

            return {
                "settings": {
                    "enabled": bool(settings_row[0]),
                    "starts_at": settings_row[1].isoformat() if settings_row[1] else None,
                    "ends_at": settings_row[2].isoformat() if settings_row[2] else None,
                    "paused_at": settings_row[3].isoformat() if settings_row[3] else None,
                    "finished_at": settings_row[4].isoformat() if settings_row[4] else None,
                    "selected_schedule_plan_id": settings_row[5],
                    "status": self._get_shift_auction_test_status(
                        settings_row[0],
                        settings_row[1],
                        settings_row[2],
                        settings_row[3],
                        settings_row[4]
                    ),
                },
                "selected_period": selected_period,
                "lot_dates": [item.strftime('%Y-%m-%d') for item in lot_dates],
                "operators": operators,
                "lots": lots,
                "day_offs": day_offs,
            }

    @staticmethod
    def _parse_shift_auction_export_date(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or '').strip()
        if not text:
            return None
        try:
            return datetime.strptime(text[:10], '%Y-%m-%d').date()
        except Exception:
            return None

    @staticmethod
    def _format_shift_auction_excel_time(value):
        if hasattr(value, 'strftime'):
            text = value.strftime('%H:%M')
        else:
            text = str(value or '').strip()
        try:
            hh, mm = text.split(':', 1)
            h = int(hh)
            m = int(str(mm)[:2])
        except Exception:
            return text
        if m == 0:
            return f"{h:02d}"
        return f"{h:02d}/{m:02d}"

    def _format_shift_auction_report_shift(self, lot):
        start = self._format_shift_auction_excel_time(lot.get('start_time') or lot.get('start'))
        end = self._format_shift_auction_excel_time(lot.get('end_time') or lot.get('end'))
        return f"{start}*{end}" if start or end else ""

    def generate_shift_auction_test_excel_report(self, direction_mode=SHIFT_AUCTION_MODE_LINE):
        report = self.get_shift_auction_test_export_data(direction_mode=direction_mode)
        selected_period = report.get('selected_period') or {}
        lot_dates = [
            self._parse_shift_auction_export_date(value)
            for value in (report.get('lot_dates') or [])
        ]
        lot_dates = sorted({value for value in lot_dates if value})
        start_date_obj = self._parse_shift_auction_export_date(selected_period.get('date_from'))
        end_date_obj = self._parse_shift_auction_export_date(selected_period.get('date_to'))
        if not start_date_obj and lot_dates:
            start_date_obj = min(lot_dates)
        if not end_date_obj and lot_dates:
            end_date_obj = max(lot_dates)
        if not start_date_obj or not end_date_obj or end_date_obj < start_date_obj:
            raise ValueError("AUCTION_NO_LOTS")

        date_list = []
        current_date = start_date_obj
        while current_date <= end_date_obj:
            date_list.append(current_date)
            current_date += timedelta(days=1)

        operators = report.get('operators') or []
        lots = report.get('lots') or []
        settings = report.get('settings') or {}

        claimed_by_operator_date = {}
        unclaimed_lots = []
        for lot in lots:
            status = str(lot.get('status') or '').strip().lower()
            if status == 'claimed' and lot.get('claimed_by') is not None:
                key = (int(lot.get('claimed_by')), str(lot.get('shift_date') or ''))
                claimed_by_operator_date.setdefault(key, []).append(lot)
            elif status != 'claimed':
                unclaimed_lots.append(lot)

        day_off_dates = {
            (int(item.get('operator_id')), str(item.get('date') or ''))
            for item in (report.get('day_offs') or [])
            if item.get('operator_id') is not None and item.get('date')
        }

        wb = Workbook()
        ws = wb.active
        ws.title = 'Итоги аукциона'

        total_columns = max(2 + len(date_list), 6)

        thin_side = Side(style='thin', color='CBD5E1')
        border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)
        title_fill = PatternFill(fill_type='solid', fgColor='0F172A')
        subtitle_fill = PatternFill(fill_type='solid', fgColor='E0F2FE')
        matrix_header_fill = PatternFill(fill_type='solid', fgColor='1E3A8A')
        unclaimed_header_fill = PatternFill(fill_type='solid', fgColor='92400E')
        claimed_fill = PatternFill(fill_type='solid', fgColor='DCFCE7')
        day_off_fill = PatternFill(fill_type='solid', fgColor='F1F5F9')
        zebra_fill = PatternFill(fill_type='solid', fgColor='F8FAFC')
        empty_fill = PatternFill(fill_type='solid', fgColor='FFFFFF')

        title_font = Font(bold=True, color='FFFFFF', size=14)
        subtitle_font = Font(color='0F172A', size=10)
        header_font = Font(bold=True, color='FFFFFF')
        section_font = Font(bold=True, color='FFFFFF', size=12)
        base_alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        left_alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)

        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_columns)
        title_cell = ws.cell(row=1, column=1, value='Отчет по аукциону смен')
        title_cell.fill = title_fill
        title_cell.font = title_font
        title_cell.alignment = left_alignment

        period_label = f"{start_date_obj.strftime('%d.%m.%Y')} - {end_date_obj.strftime('%d.%m.%Y')}"
        generated_label = datetime.now().strftime('%d.%m.%Y %H:%M')
        status_label = {
            'disabled': 'выключен',
            'scheduled': 'запланирован',
            'open': 'открыт',
            'paused': 'приостановлен',
            'closed': 'завершен',
        }.get(str(settings.get('status') or '').lower(), str(settings.get('status') or ''))
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=total_columns)
        subtitle_cell = ws.cell(
            row=2,
            column=1,
            value=f"Период: {period_label} | Статус: {status_label or '-'} | Сформировано: {generated_label}"
        )
        subtitle_cell.fill = subtitle_fill
        subtitle_cell.font = subtitle_font
        subtitle_cell.alignment = left_alignment

        matrix_header_row = 4
        ws.cell(row=matrix_header_row, column=1, value='ФИО')
        ws.cell(row=matrix_header_row, column=2, value='Ставка')
        for idx, day_obj in enumerate(date_list, start=3):
            cell = ws.cell(row=matrix_header_row, column=idx, value=day_obj)
            cell.number_format = 'DD.MM.YYYY'

        for col_idx in range(1, total_columns + 1):
            cell = ws.cell(row=matrix_header_row, column=col_idx)
            cell.fill = matrix_header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = base_alignment

        matrix_first_data_row = matrix_header_row + 1
        if operators:
            for row_idx, operator in enumerate(operators, start=matrix_first_data_row):
                operator_id = int(operator.get('id'))
                ws.cell(row=row_idx, column=1, value=operator.get('name') or '')
                rate_cell = ws.cell(row=row_idx, column=2, value=operator.get('rate') if operator.get('rate') is not None else '')
                if isinstance(rate_cell.value, (int, float)):
                    rate_cell.number_format = '0.00'

                for col_idx, day_obj in enumerate(date_list, start=3):
                    date_key = day_obj.strftime('%Y-%m-%d')
                    shifts = claimed_by_operator_date.get((operator_id, date_key), [])
                    cell = ws.cell(row=row_idx, column=col_idx)
                    if shifts:
                        cell.value = ', '.join(filter(None, (self._format_shift_auction_report_shift(lot) for lot in shifts)))
                        cell.fill = claimed_fill
                    elif (operator_id, date_key) in day_off_dates:
                        cell.value = 'Выходной'
                        cell.fill = day_off_fill
                    else:
                        cell.value = ''
                        cell.fill = zebra_fill if ((row_idx - matrix_first_data_row) % 2) else empty_fill

                for col_idx in range(1, total_columns + 1):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.border = border
                    cell.alignment = left_alignment if col_idx == 1 else base_alignment
                    if col_idx <= 2 and ((row_idx - matrix_first_data_row) % 2):
                        cell.fill = zebra_fill
            matrix_last_row = matrix_first_data_row + len(operators) - 1
        else:
            matrix_last_row = matrix_first_data_row
            ws.merge_cells(start_row=matrix_first_data_row, start_column=1, end_row=matrix_first_data_row, end_column=total_columns)
            cell = ws.cell(row=matrix_first_data_row, column=1, value='Нет выбранных операторов для отчета')
            cell.alignment = left_alignment
            cell.fill = zebra_fill
            for col_idx in range(1, total_columns + 1):
                ws.cell(row=matrix_first_data_row, column=col_idx).border = border

        ws.freeze_panes = 'C5'
        ws.auto_filter.ref = f"A{matrix_header_row}:{get_column_letter(2 + len(date_list))}{max(matrix_last_row, matrix_header_row)}"

        unclaimed_title_row = matrix_last_row + 3
        ws.merge_cells(start_row=unclaimed_title_row, start_column=1, end_row=unclaimed_title_row, end_column=total_columns)
        section_cell = ws.cell(row=unclaimed_title_row, column=1, value='Неразобранные смены')
        section_cell.fill = unclaimed_header_fill
        section_cell.font = section_font
        section_cell.alignment = left_alignment

        def _unclaimed_start_key(lot):
            text = str(lot.get('start_time') or '')
            try:
                hh, mm = text.split(':')[:2]
                return (int(hh), int(mm))
            except Exception:
                return (24, 0)

        unclaimed_by_date = {}
        for lot in unclaimed_lots:
            shift_date = self._parse_shift_auction_export_date(lot.get('shift_date'))
            if not shift_date:
                continue
            unclaimed_by_date.setdefault(shift_date, []).append(lot)
        for shifts in unclaimed_by_date.values():
            shifts.sort(key=_unclaimed_start_key)

        unclaimed_header_row = unclaimed_title_row + 1
        for col_idx in (1, 2):
            cell = ws.cell(row=unclaimed_header_row, column=col_idx)
            cell.fill = unclaimed_header_fill
            cell.border = border
        for idx, day_obj in enumerate(date_list, start=3):
            cell = ws.cell(row=unclaimed_header_row, column=idx, value=day_obj)
            cell.number_format = 'DD.MM.YYYY'
            cell.fill = unclaimed_header_fill
            cell.font = header_font
            cell.border = border
            cell.alignment = base_alignment

        max_shifts_per_day = max((len(shifts) for shifts in unclaimed_by_date.values()), default=0)

        if max_shifts_per_day == 0:
            info_row = unclaimed_header_row + 1
            ws.merge_cells(start_row=info_row, start_column=1, end_row=info_row, end_column=total_columns)
            cell = ws.cell(row=info_row, column=1, value='Все смены разобраны')
            cell.alignment = left_alignment
            cell.fill = claimed_fill
            for col_idx in range(1, total_columns + 1):
                ws.cell(row=info_row, column=col_idx).border = border
        else:
            free_fill = PatternFill(fill_type='solid', fgColor='FEF3C7')
            cancelled_fill = PatternFill(fill_type='solid', fgColor='FECACA')
            for row_offset in range(max_shifts_per_day):
                row_idx = unclaimed_header_row + 1 + row_offset
                base_fill = zebra_fill if (row_offset % 2) else empty_fill
                for col_idx in (1, 2):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.border = border
                    cell.fill = base_fill
                for col_idx, day_obj in enumerate(date_list, start=3):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    cell.border = border
                    cell.alignment = base_alignment
                    day_shifts = unclaimed_by_date.get(day_obj, [])
                    if row_offset < len(day_shifts):
                        lot = day_shifts[row_offset]
                        cell.value = self._format_shift_auction_report_shift(lot)
                        status = str(lot.get('status') or '').strip().lower()
                        cell.fill = cancelled_fill if status == 'cancelled' else free_fill
                    else:
                        cell.fill = base_fill

        ws.row_dimensions[1].height = 26
        ws.row_dimensions[2].height = 22
        ws.row_dimensions[matrix_header_row].height = 24
        ws.row_dimensions[unclaimed_header_row].height = 24
        ws.column_dimensions['A'].width = 34
        ws.column_dimensions['B'].width = 10
        for idx in range(len(date_list)):
            ws.column_dimensions[get_column_letter(3 + idx)].width = 15

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return output, start_date_obj, end_date_obj

    def get_shift_auction_test_snapshot(self, current_user_id=None, include_admin_fields=False,
                                        direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        settings_id = shift_auction_settings_row_id(mode)
        current_id = None
        try:
            current_id = int(current_user_id) if current_user_id is not None else None
        except Exception:
            current_id = None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    s.enabled,
                    s.launch_note,
                    s.starts_at,
                    s.ends_at,
                    s.paused_at,
                    s.finished_at,
                    COALESCE(
                        s.selected_schedule_plan_id,
                        (
                            SELECT source_schedule_plan_id
                            FROM shift_auction_test_lots
                            WHERE source_schedule_plan_id IS NOT NULL
                              AND COALESCE(direction_mode, 'line') = s.direction_mode
                            ORDER BY id
                            LIMIT 1
                        )
                    ) AS selected_schedule_plan_id,
                    s.updated_by,
                    u.name AS updated_by_name,
                    s.updated_at,
                    s.published_to_work_schedules_at,
                    s.published_to_work_schedules_by,
                    publisher.name AS published_to_work_schedules_by_name,
                    s.topup_started_at,
                    s.topup_started_by,
                    topup_user.name AS topup_started_by_name,
                    (
                        SELECT COALESCE(MAX(id), 0)
                        FROM shift_auction_test_events
                        WHERE COALESCE(direction_mode, 'line') = s.direction_mode
                    ) AS last_event_id
                FROM shift_auction_test_access s
                LEFT JOIN users u ON u.id = s.updated_by
                LEFT JOIN users publisher ON publisher.id = s.published_to_work_schedules_by
                LEFT JOIN users topup_user ON topup_user.id = s.topup_started_by
                WHERE s.id = %s
            """, (settings_id,))
            settings_row = cursor.fetchone()
            if not settings_row:
                cursor.execute("""
                    INSERT INTO shift_auction_test_access (id, direction_mode)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING
                """, (settings_id, mode))
                settings_row = (False, '', None, None, None, None, None, None, None, None, None, None, None, None, None, None, 0)

            common_snapshot = self._get_shift_auction_snapshot_common_tx(
                cursor,
                settings_row,
                include_admin_fields=include_admin_fields,
                direction_mode=mode
            )
            participant_rows = common_snapshot["participant_rows"]
            lots = common_snapshot["lots"]
            lot_dates = common_snapshot["lot_dates"]
            selected_period_row = common_snapshot["selected_period_row"]
            available_periods = common_snapshot["available_periods"]
            claim_journal_rows = common_snapshot["claim_journal_rows"]
            participant_workloads = common_snapshot.get("participant_workloads") or []

            my_day_offs = []
            if current_id:
                cursor.execute("""
                    SELECT day_off_date
                    FROM shift_auction_test_day_offs
                    WHERE operator_id = %s
                      AND COALESCE(direction_mode, 'line') = %s
                    ORDER BY day_off_date
                """, (current_id, mode))
                my_day_offs = [
                    row[0].strftime('%Y-%m-%d')
                    for row in (cursor.fetchall() or [])
                    if row and row[0]
                ]

            my_blocked_dates = (
                self._get_shift_auction_operator_blocked_dates_tx(cursor, current_id, lot_dates=lot_dates)
                if current_id else []
            )
            my_work_shifts = (
                self._get_shift_auction_operator_work_shifts_tx(cursor, current_id, lot_dates)
                if current_id else []
            )

            cursor.execute(
                "SELECT COALESCE(rate_lock_enabled, FALSE) FROM shift_auction_test_access WHERE id = %s",
                (settings_id,)
            )
            rate_lock_row = cursor.fetchone()
            rate_lock_enabled = bool(rate_lock_row[0]) if rate_lock_row else False

        selected_operator_ids = [int(row[0]) for row in participant_rows if row and row[0] is not None]
        time_groups = common_snapshot.get("time_groups") or []
        active_plan_id = settings_row[6] if settings_row else None
        # The viewer's own group for the active week — groups of other weeks are in
        # the payload for the manager's form only and must not gate anybody.
        my_time_group = next(
            (
                group for group in time_groups
                if current_id is not None
                and group.get("plan_id") == active_plan_id
                and current_id in (group.get("operator_ids") or [])
            ),
            None
        )
        my_group_starts_at, my_group_ends_at = self._shift_auction_group_window(my_time_group)
        my_effective_starts_at, my_effective_ends_at = self._shift_auction_effective_window(
            settings_row[2] if settings_row else None,
            settings_row[3] if settings_row else None,
            my_group_starts_at,
            my_group_ends_at,
        )
        post_auction_active = bool(
            settings_row and settings_row[10]
            and current_id is not None
            and current_id in set(selected_operator_ids)
        )
        notify_post_claim_enabled = (
            self.get_admin_post_claim_notify_setting(current_id)
            if include_admin_fields and current_id is not None else False
        )
        return {
            "enabled": bool(settings_row[0]) if settings_row else False,
            "launch_note": settings_row[1] or "",
            "starts_at": settings_row[2].isoformat() if settings_row and settings_row[2] else None,
            "ends_at": settings_row[3].isoformat() if settings_row and settings_row[3] else None,
            "paused_at": settings_row[4].isoformat() if settings_row and settings_row[4] else None,
            "finished_at": settings_row[5].isoformat() if settings_row and settings_row[5] else None,
            "selected_schedule_plan_id": settings_row[6] if settings_row else None,
            "selected_period": self._serialize_shift_auction_period_row(selected_period_row),
            # Only the manager's form needs the whole roster of groups; an operator
            # is told about their own group and nothing more.
            "time_groups": time_groups if include_admin_fields else [],
            "my_time_group": my_time_group,
            "my_effective_starts_at": my_effective_starts_at.isoformat() if my_effective_starts_at else None,
            "my_effective_ends_at": my_effective_ends_at.isoformat() if my_effective_ends_at else None,
            # Operator-aware status: a member of a week time group sees their own
            # window (earlier or later); everyone else sees the main timeline.
            "status": self._get_shift_auction_test_status(
                bool(settings_row[0]) if settings_row else False,
                my_effective_starts_at,
                my_effective_ends_at,
                settings_row[4] if settings_row else None,
                settings_row[5] if settings_row else None
            ),
            "updated_by": settings_row[7] if settings_row else None,
            "updated_by_name": settings_row[8] or "",
            "updated_at": settings_row[9].isoformat() if settings_row and settings_row[9] else None,
            "published_to_work_schedules_at": settings_row[10].isoformat() if settings_row and settings_row[10] else None,
            "published_to_work_schedules_by": settings_row[11] if settings_row else None,
            "published_to_work_schedules_by_name": settings_row[12] or "",
            "topup_started_at": settings_row[13].isoformat() if settings_row and len(settings_row) > 13 and settings_row[13] else None,
            "topup_started_by": settings_row[14] if settings_row and len(settings_row) > 14 else None,
            "topup_started_by_name": (settings_row[15] or "") if settings_row and len(settings_row) > 15 else "",
            "rate_lock_enabled": rate_lock_enabled,
            "selected_operator_ids": selected_operator_ids,
            "selected_operators": [
                {
                    "id": row[0],
                    "name": row[1],
                    "role": normalize_role_value(row[2]),
                    "status": row[3],
                    "rate": float(row[4]) if row[4] is not None else 1.0,
                    "direction_id": row[5],
                    "direction": row[6] or "",
                    "supervisor_id": row[7],
                    "supervisor_name": row[8] or "",
                    "selected_at": row[9].isoformat() if row[9] else None,
                }
                for row in participant_rows
            ],
            "is_current_user_tester": current_id in set(selected_operator_ids) if current_id is not None else False,
            "lots": lots,
            "my_day_offs": my_day_offs,
            "my_blocked_dates": my_blocked_dates,
            "my_work_shifts": my_work_shifts,
            "available_periods": available_periods,
            "participant_workloads": participant_workloads,
            "claim_journal": [
                {
                    "id": row[0],
                    "claimed_at": row[1].isoformat() if row[1] else None,
                    "shift_date": row[2],
                    "start_time": row[3],
                    "end_time": row[4],
                    "claimed_by": row[5],
                    "claimed_by_name": row[6] or "",
                    "source_schedule_plan_id": row[7],
                    "period_start": row[8].strftime('%Y-%m-%d') if row[8] else None,
                    "period_end": row[9].strftime('%Y-%m-%d') if row[9] else None,
                }
                for row in claim_journal_rows
            ],
            "last_event_id": int(settings_row[16] or 0) if settings_row and len(settings_row) > 16 else 0,
            "post_auction_active": post_auction_active,
            "notify_post_claim_enabled": notify_post_claim_enabled,
        }

    def get_shift_auction_period_preview(self, schedule_plan_id, current_user_id=None,
                                         direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        scope_sql, scope_params = shift_auction_direction_scope_sql(mode)
        current_id = None
        try:
            current_id = int(current_user_id) if current_user_id is not None else None
        except Exception:
            current_id = None

        with self._get_cursor() as cursor:
            period_row = self._validate_shift_auction_period_tx(
                cursor,
                schedule_plan_id,
                require_restartable=False,
                direction_mode=mode
            )
            plan_id = int(period_row[0])
            period_start = period_row[1]
            period_end = period_row[2]
            cursor.execute("""
                SELECT id, shift_date, start_time, end_time, rate_min, breaks, start_minute, end_minute,
                       meta->>'shiftKind' AS shift_kind
                FROM resource_saved_schedule_shifts
                WHERE plan_id = %s
                  AND COALESCE(meta->>'excludeFromAuction', 'false') <> 'true'
                ORDER BY shift_date, start_minute, end_minute, id
            """, (plan_id,))
            shift_rows = cursor.fetchall() or []
            lot_dates = sorted({row[1] for row in shift_rows if row and row[1]})
            my_blocked_dates = (
                self._get_shift_auction_operator_blocked_dates_tx(cursor, current_id, lot_dates=lot_dates)
                if current_id else []
            )
            my_work_shifts = (
                self._get_shift_auction_operator_work_shifts_tx(cursor, current_id, lot_dates)
                if current_id else []
            )

            cursor.execute("""
                SELECT e.id, e.event_type, e.payload, e.created_at
                FROM shift_auction_test_events e
                WHERE (
                    e.event_type IN ('lot_claimed', 'lot_released', 'lot_post_auction_claimed')
                    AND NULLIF(e.payload->'lot'->>'source_schedule_plan_id', '')::INTEGER = %s
                )
                OR (
                    e.event_type IN ('day_off_selected', 'day_off_removed')
                    AND NULLIF(e.payload->>'date', '')::DATE >= %s
                    AND NULLIF(e.payload->>'date', '')::DATE <= %s
                )
                OR (
                    e.event_type = 'settings_updated'
                    AND (
                        NULLIF(e.payload->>'selected_schedule_plan_id', '')::INTEGER = %s
                        OR (
                            e.payload->>'date_from' = %s
                            AND e.payload->>'date_to' = %s
                        )
                    )
                )
                ORDER BY e.id
            """, (
                plan_id,
                period_start,
                period_end,
                plan_id,
                period_start.strftime('%Y-%m-%d') if period_start else None,
                period_end.strftime('%Y-%m-%d') if period_end else None,
            ))
            event_rows = cursor.fetchall() or []

            lots_by_shift_id = {}
            lots_by_time_key = {}
            lots = []
            for row in shift_rows:
                shift_date_text = row[1].strftime('%Y-%m-%d') if row[1] else None
                start_text = row[2].strftime('%H:%M') if row[2] else None
                end_text = row[3].strftime('%H:%M') if row[3] else None
                lot = {
                    "id": f"preview-{row[0]}",
                    "shift_date": shift_date_text,
                    "start_time": start_text,
                    "end_time": end_text,
                    "rate_min": float(row[4]) if row[4] is not None else 0,
                    "status": "available",
                    "claimed_by": None,
                    "claimed_by_name": "",
                    "claimed_at": None,
                    "breaks": row[5] if len(row) > 5 and isinstance(row[5], list) else [],
                    "source_schedule_plan_id": plan_id,
                    "source_schedule_shift_id": row[0],
                    "post_auction_claimed": False,
                    "claimed_by_direction_id": None,
                    "claimed_by_direction": "",
                    "source_start_minute": int(row[6]) if len(row) > 6 and row[6] is not None else None,
                    "source_end_minute": int(row[7]) if len(row) > 7 and row[7] is not None else None,
                    "claim_start_time": None,
                    "claim_end_time": None,
                    "shift_kind": (row[8] or "") if len(row) > 8 else "",
                    "preview_only": True,
                }
                lots.append(lot)
                lots_by_shift_id[int(row[0])] = lot
                lots_by_time_key[(shift_date_text, start_text, end_text)] = lot

            selected_operator_ids = []
            day_offs_by_operator = {}
            claim_journal_items = []
            claimed_operator_ids = set()

            def _event_int(value):
                try:
                    result = int(value)
                except Exception:
                    return None
                return result if result > 0 else None

            def _event_lot_target(lot_payload):
                shift_id = _event_int((lot_payload or {}).get("source_schedule_shift_id"))
                if shift_id and shift_id in lots_by_shift_id:
                    return lots_by_shift_id[shift_id]
                key = (
                    str((lot_payload or {}).get("shift_date") or ""),
                    str((lot_payload or {}).get("start_time") or "")[:5],
                    str((lot_payload or {}).get("end_time") or "")[:5],
                )
                return lots_by_time_key.get(key)

            def _date_in_period(date_text):
                if not date_text:
                    return False
                try:
                    value = datetime.strptime(str(date_text), "%Y-%m-%d").date()
                except Exception:
                    return False
                return bool(period_start and period_end and period_start <= value <= period_end)

            for event_id, event_type, payload, created_at in event_rows:
                event_payload = payload or {}
                event_type_text = str(event_type or '')
                if event_type_text == "settings_updated":
                    raw_ids = event_payload.get("selected_operator_ids")
                    if isinstance(raw_ids, list):
                        selected_operator_ids = [
                            op_id for op_id in (_event_int(item) for item in raw_ids)
                            if op_id
                        ]
                    continue

                if event_type_text in ("day_off_selected", "day_off_removed"):
                    op_id = _event_int(event_payload.get("operator_id"))
                    if not op_id:
                        continue
                    current_dates = day_offs_by_operator.setdefault(op_id, set())
                    raw_day_offs = event_payload.get("my_day_offs")
                    if isinstance(raw_day_offs, list):
                        day_offs_by_operator[op_id] = {
                            str(item)
                            for item in raw_day_offs
                            if _date_in_period(item)
                        }
                    else:
                        date_text = str(event_payload.get("date") or "")
                        if not _date_in_period(date_text):
                            continue
                        if event_type_text == "day_off_selected":
                            current_dates.add(date_text)
                        else:
                            current_dates.discard(date_text)
                    continue

                if event_type_text not in ("lot_claimed", "lot_released", "lot_post_auction_claimed"):
                    continue
                lot_payload = event_payload.get("lot") if isinstance(event_payload, dict) else {}
                if not isinstance(lot_payload, dict):
                    continue
                target_lot = _event_lot_target(lot_payload)
                if not target_lot:
                    continue
                if event_type_text == "lot_released":
                    target_lot.update({
                        "status": "available",
                        "claimed_by": None,
                        "claimed_by_name": "",
                        "claimed_at": None,
                        "post_auction_claimed": False,
                        "claimed_by_direction_id": None,
                        "claimed_by_direction": "",
                    })
                    continue
                claimed_by = _event_int(lot_payload.get("claimed_by"))
                if not claimed_by and event_type_text == "lot_post_auction_claimed":
                    claimed_by = _event_int(event_payload.get("operator_id"))
                if claimed_by:
                    claimed_operator_ids.add(claimed_by)
                target_lot.update({
                    "status": "claimed",
                    "claimed_by": claimed_by,
                    "claimed_at": lot_payload.get("claimed_at") or (created_at.isoformat() if created_at else None),
                    "post_auction_claimed": bool(lot_payload.get("post_auction_claimed")),
                    "claim_start_time": lot_payload.get("claim_start_time") or lot_payload.get("post_claim_start_time"),
                    "claim_end_time": lot_payload.get("claim_end_time") or lot_payload.get("post_claim_end_time"),
                })
                claim_journal_items.append({
                    "id": event_id,
                    "claimed_at": target_lot.get("claimed_at"),
                    "shift_date": target_lot.get("shift_date"),
                    "start_time": target_lot.get("claim_start_time") or target_lot.get("start_time"),
                    "end_time": target_lot.get("claim_end_time") or target_lot.get("end_time"),
                    "claimed_by": claimed_by,
                    "source_schedule_plan_id": plan_id,
                    "period_start": period_start.strftime('%Y-%m-%d') if period_start else None,
                    "period_end": period_end.strftime('%Y-%m-%d') if period_end else None,
                })

            # Historical claims are the source of truth for a published period and may
            # hold SEVERAL disjoint partial claims per shift (partial добор). Load them
            # up-front so each claiming operator is included in the participant lookup.
            cursor.execute("""
                SELECT source_schedule_shift_id, claimed_by, claimed_at, claimed_start_time, claimed_end_time
                FROM shift_auction_historical_claims
                WHERE plan_id = %s
                ORDER BY source_schedule_shift_id, claimed_start_time NULLS FIRST
            """, (plan_id,))
            claims_by_shift = {}
            for sid, cby, cat, cs, ce in (cursor.fetchall() or []):
                if sid is None:
                    continue
                claims_by_shift.setdefault(int(sid), []).append({
                    "claimed_by": int(cby) if cby is not None else None,
                    "claimed_at": cat,
                    "claim_start": cs,
                    "claim_end": ce,
                })
                if cby is not None:
                    claimed_operator_ids.add(int(cby))

            operator_ids = []
            seen_operator_ids = set()
            for op_id in [*selected_operator_ids, *sorted(claimed_operator_ids), *sorted(day_offs_by_operator.keys())]:
                if op_id and op_id not in seen_operator_ids:
                    seen_operator_ids.add(op_id)
                    operator_ids.append(op_id)

            participant_rows = []
            if operator_ids:
                cursor.execute(f"""
                    SELECT
                        u.id,
                        u.name,
                        u.role,
                        u.status,
                        u.rate,
                        u.direction_id,
                        d.name AS direction_name,
                        u.supervisor_id,
                        s.name AS supervisor_name,
                        NULL::timestamp AS selected_at
                    FROM users u
                    JOIN directions d ON d.id = u.direction_id
                    JOIN departments dep ON dep.id = d.department_id
                    LEFT JOIN users s ON s.id = u.supervisor_id
                    WHERE u.id = ANY(%s)
                      AND LOWER(COALESCE(u.role, '')) = 'operator'
                      AND COALESCE(u.status, '') NOT IN ('fired', 'dismissal')
                      AND {scope_sql}
                    ORDER BY u.name
                """, (
                    operator_ids,
                    *scope_params,
                ))
                participant_rows = cursor.fetchall() or []

            operator_info = {
                int(row[0]): {
                    "id": row[0],
                    "name": row[1],
                    "role": normalize_role_value(row[2]),
                    "status": row[3],
                    "rate": float(row[4]) if row[4] is not None else 1.0,
                    "direction_id": row[5],
                    "direction": row[6] or "",
                    "supervisor_id": row[7],
                    "supervisor_name": row[8] or "",
                    "selected_at": row[9].isoformat() if row[9] else None,
                }
                for row in participant_rows
                if row and row[0] is not None
            }
            for lot in lots:
                claimed_by = _event_int(lot.get("claimed_by"))
                if not claimed_by:
                    continue
                info = operator_info.get(claimed_by) or {}
                lot["claimed_by_name"] = info.get("name") or ""
                lot["claimed_by_direction_id"] = info.get("direction_id")
                lot["claimed_by_direction"] = info.get("direction") or ""

            # SINGLE-LOT model: keep ONE lot per shift and attach claim_segments (the
            # taken parts). Fully-covered shifts → 'claimed' (grey); partially-taken →
            # stay 'available' and the frontend shows the free part with a marker.
            for lot in lots:
                shift_id = _event_int(lot.get("source_schedule_shift_id"))
                claims = claims_by_shift.get(shift_id) if shift_id else None
                if not claims:
                    lot["claim_segments"] = []
                    continue
                full_start = lot.get("source_start_minute")
                full_end = lot.get("source_end_minute")
                has_range = isinstance(full_start, int) and isinstance(full_end, int) and full_end > full_start
                segments = []
                claimed_ranges = []
                full_block = False
                for claim in claims:
                    cs = claim.get("claim_start")
                    ce = claim.get("claim_end")
                    info = operator_info.get(int(claim["claimed_by"])) if claim.get("claimed_by") else None
                    segments.append({
                        "claimed_by": claim.get("claimed_by"),
                        "claimed_by_name": (info or {}).get("name") or "",
                        "start_time": cs.strftime('%H:%M') if cs else lot.get("start_time"),
                        "end_time": ce.strftime('%H:%M') if ce else lot.get("end_time"),
                    })
                    if cs is None or ce is None:
                        full_block = True
                    elif has_range:
                        cs_min = _time_to_minutes(cs.strftime('%H:%M'))
                        ce_min = _time_to_minutes(ce.strftime('%H:%M'))
                        if full_end > 24 * 60 and cs_min < full_start:
                            cs_min += 24 * 60
                        if ce_min <= cs_min:
                            ce_min += 24 * 60
                        claimed_ranges.append((cs_min, ce_min))
                lot["claim_segments"] = segments
                free_gaps = (
                    [g for g in self._subtract_ranges(full_start, full_end, claimed_ranges) if g[1] - g[0] > 0]
                    if (has_range and not full_block) else []
                )
                if not free_gaps:
                    first = claims[0]
                    finfo = operator_info.get(int(first["claimed_by"])) if first.get("claimed_by") else None
                    lot.update({
                        "status": "claimed",
                        "post_auction_claimed": True,
                        "claimed_by": first.get("claimed_by"),
                        "claimed_by_name": (finfo or {}).get("name") or lot.get("claimed_by_name") or "",
                    })
                else:
                    lot.update({
                        "status": "available",
                        "post_auction_claimed": False,
                        "claimed_by": None,
                        "claimed_by_name": "",
                        "claim_start_time": None,
                        "claim_end_time": None,
                    })

            participant_workloads = self._get_shift_auction_participant_workloads_tx(
                cursor,
                participant_rows,
                lots,
                lot_dates,
                day_offs_by_operator=day_offs_by_operator,
                direction_mode=mode,
                # The previewed week has its own groups — and its own allowances.
                self_schedule_operator_ids={
                    operator_id
                    for group in self._get_shift_auction_time_groups_tx(
                        cursor, plan_id, plan_only=True, direction_mode=mode
                    )
                    if group.get("self_schedule_enabled")
                    for operator_id in (group.get("operator_ids") or [])
                },
            )
            my_day_offs = sorted(day_offs_by_operator.get(current_id, set()) or []) if current_id else []
            for item in claim_journal_items:
                item["claimed_by_name"] = (operator_info.get(int(item["claimed_by"] or 0)) or {}).get("name", "")
            cursor.execute(
                "SELECT 1 FROM shift_auction_published_periods WHERE plan_id = %s",
                (plan_id,)
            )
            post_auction_active = cursor.fetchone() is not None

        return {
            "period": self._serialize_shift_auction_period_row(period_row),
            "lots": lots,
            "my_blocked_dates": my_blocked_dates,
            "my_day_offs": my_day_offs,
            "my_work_shifts": my_work_shifts,
            "selected_operator_ids": sorted(operator_info.keys()),
            "selected_operators": list(operator_info.values()),
            "participant_workloads": participant_workloads,
            "claim_journal": list(reversed(claim_journal_items[-50:])),
            "history_available": bool(event_rows),
            "post_auction_active": post_auction_active,
        }

    def shift_auction_mode_for_operator(self, operator_id):
        """Направление аукциона по карточке человека, либо None — он вне аукциона.

        Оператор принадлежит ровно одному направлению, поэтому режим НЕ приходит с
        клиента: подменив его в запросе, человек попал бы в чужой аукцион.
        """
        try:
            operator_id = int(operator_id)
        except Exception:
            return None
        if operator_id <= 0:
            return None
        with self._get_cursor() as cursor:
            return self._shift_auction_mode_for_operator_tx(cursor, operator_id)

    def _shift_auction_mode_for_operator_tx(self, cursor, operator_id):
        """Прогон человека по карточке: направление и отдел направления."""
        cursor.execute("""
            SELECT BTRIM(COALESCE(d.name, '')), LOWER(BTRIM(COALESCE(dep.code, '')))
            FROM users u
            LEFT JOIN directions d ON d.id = u.direction_id
            LEFT JOIN departments dep ON dep.id = d.department_id
            WHERE u.id = %s
        """, (operator_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return shift_auction_mode_matches_direction(row[0], row[1])

    def _shift_auction_mode_for_plan_tx(self, cursor, plan_id):
        """Направление аукциона по недельному плану — он и есть носитель режима."""
        cursor.execute(
            "SELECT COALESCE(direction_mode, 'line') FROM resource_saved_schedule_plans WHERE id = %s",
            (plan_id,)
        )
        row = cursor.fetchone()
        return normalize_shift_auction_mode(row[0] if row else None)

    def has_shift_auction_history_access(self, operator_id):
        try:
            operator_id = int(operator_id)
        except Exception:
            return False
        if operator_id <= 0:
            return False
        with self._get_cursor() as cursor:
            # Участником человек может быть только в СВОЁМ направлении, но спрашивать
            # оба дешевле, чем читать карточку: кеш участников уже прогрет обоими.
            if any(
                operator_id in self._get_shift_auction_participant_ids_cached_tx(cursor, direction_mode=mode)
                for mode in SHIFT_AUCTION_MODES
            ):
                return True
            cursor.execute("""
                SELECT 1
                FROM shift_auction_published_periods
                WHERE participant_ids @> ARRAY[%s]::INTEGER[]
                LIMIT 1
            """, (operator_id,))
            return cursor.fetchone() is not None

    def is_shift_auction_period_participant(self, operator_id, schedule_plan_id):
        try:
            operator_id = int(operator_id)
            plan_id = int(schedule_plan_id) if schedule_plan_id not in (None, '') else 0
        except Exception:
            return False
        if operator_id <= 0 or plan_id <= 0:
            return False
        with self._get_cursor() as cursor:
            mode = self._shift_auction_mode_for_plan_tx(cursor, plan_id)
            cursor.execute("""
                SELECT selected_schedule_plan_id
                FROM shift_auction_test_access
                WHERE id = %s
            """, (shift_auction_settings_row_id(mode),))
            settings_row = cursor.fetchone()
            current_plan_id = None
            try:
                current_plan_id = int(settings_row[0]) if settings_row and settings_row[0] is not None else None
            except Exception:
                current_plan_id = None
            if (
                current_plan_id == plan_id
                and operator_id in self._get_shift_auction_participant_ids_tx(cursor, direction_mode=mode)
            ):
                return True
            cursor.execute("""
                SELECT 1
                FROM shift_auction_published_periods
                WHERE plan_id = %s
                  AND participant_ids @> ARRAY[%s]::INTEGER[]
                LIMIT 1
            """, (plan_id, operator_id))
            return cursor.fetchone() is not None

    def get_shift_auction_lots_for_planner_date(self, target_date, direction_mode=SHIFT_AUCTION_MODE_LINE):
        """
        Возвращает «выбранные смены» (claimed лоты) для указанной даты, чтобы
        раздел «Графики работы → почасовая группировка → План» мог учитывать
        и активный аукцион, и уже опубликованные исторические периоды.

        Сначала проверяем shift_auction_test_lots (активный план). Если на эту
        дату там лотов нет — собираем «виртуальные» лоты из
        resource_saved_schedule_shifts + shift_auction_historical_claims по
        опубликованным периодам, в чьи даты попадает target_date.
        Формат лотов совместим со snapshot.lots.
        """
        if hasattr(target_date, 'strftime'):
            date_obj = target_date
        else:
            try:
                date_obj = datetime.strptime(str(target_date), '%Y-%m-%d').date()
            except Exception:
                return {"lots": [], "selected_operators": []}

        mode = normalize_shift_auction_mode(direction_mode)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    l.id, l.shift_date, l.start_time, l.end_time, l.rate_min,
                    l.status, l.claimed_by, l.claimed_at, l.breaks,
                    l.source_schedule_plan_id, l.source_schedule_shift_id,
                    COALESCE(l.post_auction_claimed, FALSE) AS post_auction_claimed,
                    u.name AS claimed_by_name,
                    u.direction_id AS claimed_by_direction_id,
                    cdir.name AS claimed_by_direction,
                    s.start_minute, s.end_minute,
                    l.post_claim_start_time,
                    l.post_claim_end_time,
                    l.added_by,
                    added_user.name AS added_by_name,
                    s.meta->>'shiftKind' AS shift_kind
                FROM shift_auction_test_lots l
                LEFT JOIN users u ON u.id = l.claimed_by
                LEFT JOIN directions cdir ON cdir.id = u.direction_id
                LEFT JOIN resource_saved_schedule_shifts s
                    ON s.id = l.source_schedule_shift_id
                LEFT JOIN users added_user ON added_user.id = l.added_by
                WHERE l.shift_date BETWEEN %s::date - INTERVAL '1 day' AND %s::date
                  AND COALESCE(l.direction_mode, 'line') = %s
                ORDER BY l.shift_date, l.start_time, l.end_time, l.id
            """, (date_obj, date_obj, mode))
            active_rows = cursor.fetchall() or []

            lots = []
            for row in active_rows:
                lots.append({
                    "id": row[0],
                    "shift_date": row[1].strftime('%Y-%m-%d') if row[1] else None,
                    "start_time": row[2].strftime('%H:%M') if row[2] else None,
                    "end_time": row[3].strftime('%H:%M') if row[3] else None,
                    "rate_min": float(row[4]) if row[4] is not None else 0,
                    "status": row[5],
                    "claimed_by": row[6],
                    "claimed_at": row[7].isoformat() if row[7] else None,
                    "breaks": row[8] if isinstance(row[8], list) else [],
                    "source_schedule_plan_id": row[9],
                    "source_schedule_shift_id": row[10],
                    "post_auction_claimed": bool(row[11]),
                    "claimed_by_name": row[12] or "",
                    "claimed_by_direction_id": row[13],
                    "claimed_by_direction": row[14] or "",
                    "source_start_minute": int(row[15]) if row[15] is not None else None,
                    "source_end_minute": int(row[16]) if row[16] is not None else None,
                    "claim_start_time": row[17].strftime('%H:%M') if row[17] else None,
                    "claim_end_time": row[18].strftime('%H:%M') if row[18] else None,
                    "added_by": row[19] if len(row) > 19 else None,
                    "added_by_name": (row[20] or "") if len(row) > 20 else "",
                    "shift_kind": (row[21] or "") if len(row) > 21 else "",
                })

            if lots:
                participant_plan_ids = sorted({lot.get("source_schedule_plan_id") for lot in lots if lot.get("source_schedule_plan_id")})
                cursor.execute("""
                    SELECT DISTINCT u.id, u.name, u.role, u.status, u.rate, u.direction_id, d.name AS direction_name
                    FROM shift_auction_test_participants p
                    JOIN users u ON u.id = p.operator_id
                    LEFT JOIN directions d ON d.id = u.direction_id
                    WHERE COALESCE(p.direction_mode, 'line') = %s
                """, (mode,))
                participants = cursor.fetchall() or []
                selected_operators = [
                    {
                        "id": r[0], "name": r[1],
                        "role": normalize_role_value(r[2]) if r[2] else None,
                        "status": r[3], "rate": float(r[4]) if r[4] is not None else 1.0,
                        "direction_id": r[5], "direction": r[6] or "",
                    } for r in participants
                ]
                return {"lots": lots, "selected_operators": selected_operators}

            cursor.execute("""
                SELECT pp.plan_id, pp.participant_ids
                FROM shift_auction_published_periods pp
                JOIN resource_saved_schedule_plans p ON p.id = pp.plan_id
                WHERE pp.date_from <= %s AND pp.date_to >= %s
                  AND COALESCE(p.direction_mode, 'line') = %s
                ORDER BY pp.published_at DESC
            """, (date_obj, date_obj, mode))
            historical_periods = cursor.fetchall() or []
            if not historical_periods:
                return {"lots": [], "selected_operators": []}

            plan_ids = [int(row[0]) for row in historical_periods if row and row[0] is not None]
            participant_ids_union = set()
            for row in historical_periods:
                for op_id in (row[1] or []):
                    try:
                        op_int = int(op_id)
                    except Exception:
                        continue
                    if op_int > 0:
                        participant_ids_union.add(op_int)

            cursor.execute("""
                SELECT
                    s.id, s.plan_id, s.shift_date, s.start_time, s.end_time,
                    s.rate_min, s.breaks, s.start_minute, s.end_minute,
                    hc.claimed_by, hc.claimed_at,
                    hc.claimed_start_time, hc.claimed_end_time,
                    u.name AS claimed_by_name,
                    u.direction_id AS claimed_by_direction_id,
                    cdir.name AS claimed_by_direction,
                    s.meta->>'shiftKind' AS shift_kind
                FROM resource_saved_schedule_shifts s
                LEFT JOIN shift_auction_historical_claims hc
                  ON hc.plan_id = s.plan_id AND hc.source_schedule_shift_id = s.id
                LEFT JOIN users u ON u.id = hc.claimed_by
                LEFT JOIN directions cdir ON cdir.id = u.direction_id
                WHERE s.plan_id = ANY(%s)
                  AND s.shift_date BETWEEN %s::date - INTERVAL '1 day' AND %s::date
                  AND COALESCE(s.meta->>'excludeFromAuction', 'false') <> 'true'
                ORDER BY s.shift_date, s.start_time, s.end_time, s.id
            """, (plan_ids, date_obj, date_obj))
            historical_rows = cursor.fetchall() or []
            for row in historical_rows:
                shift_id = int(row[0])
                plan_id = int(row[1])
                # A shift may now have several disjoint claims → several rows here.
                # Keep ids unique per operator so claimed slices don't collide.
                lot_id = f"preview-{shift_id}-c{int(row[9])}" if row[9] else f"preview-{shift_id}"
                lots.append({
                    "id": lot_id,
                    "shift_date": row[2].strftime('%Y-%m-%d') if row[2] else None,
                    "start_time": row[3].strftime('%H:%M') if row[3] else None,
                    "end_time": row[4].strftime('%H:%M') if row[4] else None,
                    "rate_min": float(row[5]) if row[5] is not None else 0,
                    "status": "claimed" if row[9] else "available",
                    "claimed_by": row[9],
                    "claimed_at": row[10].isoformat() if row[10] else None,
                    "breaks": row[6] if isinstance(row[6], list) else [],
                    "source_schedule_plan_id": plan_id,
                    "source_schedule_shift_id": shift_id,
                    "source_start_minute": int(row[7]) if row[7] is not None else None,
                    "source_end_minute": int(row[8]) if row[8] is not None else None,
                    "post_auction_claimed": bool(row[9]),
                    "claim_start_time": row[11].strftime('%H:%M') if row[11] else None,
                    "claim_end_time": row[12].strftime('%H:%M') if row[12] else None,
                    "claimed_by_name": row[13] or "",
                    "claimed_by_direction_id": row[14],
                    "claimed_by_direction": row[15] or "",
                    "shift_kind": row[16] or "",
                    "preview_only": True,
                })

            selected_operators = []
            if participant_ids_union:
                cursor.execute("""
                    SELECT u.id, u.name, u.role, u.status, u.rate, u.direction_id, d.name AS direction_name
                    FROM users u
                    LEFT JOIN directions d ON d.id = u.direction_id
                    WHERE u.id = ANY(%s)
                """, (sorted(participant_ids_union),))
                selected_operators = [
                    {
                        "id": r[0], "name": r[1],
                        "role": normalize_role_value(r[2]) if r[2] else None,
                        "status": r[3], "rate": float(r[4]) if r[4] is not None else 1.0,
                        "direction_id": r[5], "direction": r[6] or "",
                    } for r in (cursor.fetchall() or [])
                ]

            return {"lots": lots, "selected_operators": selected_operators}

    def _shift_auction_period_has_published_history_tx(self, cursor, plan_id, period_start=None, period_end=None):
        try:
            plan_id = int(plan_id or 0)
        except Exception:
            plan_id = 0
        if plan_id <= 0:
            return False
        cursor.execute(
            "SELECT 1 FROM shift_auction_published_periods WHERE plan_id = %s",
            (plan_id,)
        )
        return cursor.fetchone() is not None

    def restart_shift_auction_test(self, schedule_plan_id, updated_by=None,
                                   direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        settings_id = shift_auction_settings_row_id(mode)
        with self._get_cursor() as cursor:
            period_row = self._validate_shift_auction_period_tx(
                cursor,
                schedule_plan_id,
                require_restartable=True,
                direction_mode=mode
            )
            cursor.execute("""
                SELECT id, shift_date, start_time, end_time, rate_min, breaks
                FROM resource_saved_schedule_shifts
                WHERE plan_id = %s
                  AND COALESCE(meta->>'excludeFromAuction', 'false') <> 'true'
                ORDER BY shift_date, start_minute, end_minute, id
            """, (int(period_row[0]),))
            saved_shift_rows = cursor.fetchall() or []
            cursor.execute("""
                UPDATE shift_auction_test_access
                SET selected_schedule_plan_id = %s,
                    paused_at = NULL,
                    finished_at = NULL,
                    published_to_work_schedules_at = NULL,
                    published_to_work_schedules_by = NULL,
                    topup_started_at = NULL,
                    topup_started_by = NULL,
                    updated_by = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (int(period_row[0]), updated_by, settings_id))
            cursor.execute("""
                DELETE FROM shift_auction_test_events
                WHERE event_type = 'lot_claimed'
                  AND NULLIF(payload->'lot'->>'source_schedule_plan_id', '')::INTEGER = %s
            """, (int(period_row[0]),))
            # Только своего направления: у прогонов разных направлений периоды
            # совпадают (неделя линии, чата и Теза одна и та же), и без фильтра
            # перезапуск одного стирал бы журнал выходных другого.
            cursor.execute("""
                DELETE FROM shift_auction_test_events
                WHERE event_type IN ('day_off_selected', 'day_off_removed')
                  AND NULLIF(payload->>'date', '')::DATE BETWEEN %s AND %s
                  AND COALESCE(direction_mode, 'line') = %s
            """, (period_row[1], period_row[2], mode))
            # Части смен прошлого прогона надо снять вместе с лотами — иначе они
            # продолжили бы вычитаться из нормы уже в новом.
            cursor.execute("""
                DELETE FROM shift_auction_historical_claims hc
                USING shift_auction_test_lots l
                WHERE l.source_schedule_plan_id = hc.plan_id
                  AND l.source_schedule_shift_id = hc.source_schedule_shift_id
                  AND COALESCE(l.direction_mode, 'line') = %s
                  AND COALESCE(hc.claim_stage, 'post_auction') = %s
            """, (mode, self.SHIFT_AUCTION_CLAIM_STAGE_AUCTION))
            cursor.execute(
                "DELETE FROM shift_auction_test_day_offs WHERE COALESCE(direction_mode, 'line') = %s",
                (mode,)
            )
            cursor.execute(
                "DELETE FROM shift_auction_test_lots WHERE COALESCE(direction_mode, 'line') = %s",
                (mode,)
            )
            execute_values(
                cursor,
                """
                INSERT INTO shift_auction_test_lots (
                    shift_date, start_time, end_time, rate_min,
                    breaks, source_schedule_plan_id, source_schedule_shift_id,
                    direction_mode
                )
                VALUES %s
                """,
                [
                    (
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        Json(row[5] if isinstance(row[5], list) else []),
                        int(period_row[0]),
                        row[0],
                        mode,
                    )
                    for row in saved_shift_rows
                ],
            )
            event = self._insert_shift_auction_test_event(cursor, "auction_restarted", {
                "plan_id": int(period_row[0]),
                "count": len(saved_shift_rows),
                "date_from": period_row[1].strftime('%Y-%m-%d'),
                "date_to": period_row[2].strftime('%Y-%m-%d'),
                "topup_reset": True,
                "updated_by": updated_by,
            }, direction_mode=mode)
        return {
            "count": len(saved_shift_rows),
            "period": self._serialize_shift_auction_period_row(period_row),
            "event": event,
            "snapshot": self.get_shift_auction_test_snapshot(
                current_user_id=updated_by,
                include_admin_fields=True,
                direction_mode=mode
            ),
        }

    def seed_shift_auction_test_lots(self, updated_by=None, start_date=None,
                                     direction_mode=SHIFT_AUCTION_MODE_LINE):
        mode = normalize_shift_auction_mode(direction_mode)
        base_date = start_date or (date.today() + timedelta(days=1))
        template_labels = [
            (1.0, ["7*16", "8*17", "9*18", "10*19", "11*20", "13*22", "15*00", "17*02"]),
            (0.75, ["7*13/30", "8*14/30", "9*15/30", "10*16/30", "11*17/30", "12*18/30", "13*19/30", "14*20", "15*21/30", "15/30*22", "16/30*23", "17/30*00", "19/30*02"]),
            (0.5, ["7*11", "8*12", "9*13", "10*14", "11*15", "12*16", "13*17", "14*18", "15*19", "16*20", "17*21", "18*22", "19*23", "20*00", "22*02"]),
            (1.0, ["20*08"]),
        ]

        def parse_template_time(part):
            raw = str(part or '').strip()
            if '/' in raw:
                hour_raw, minute_raw = raw.split('/', 1)
                minute = int(minute_raw or 0)
            else:
                hour_raw, minute = raw, 0
            hour = int(hour_raw or 0) % 24
            return f"{hour:02d}:{minute:02d}"

        templates = []
        for rate_min, labels in template_labels:
            for label in labels:
                start_raw, end_raw = str(label).split('*', 1)
                templates.append((parse_template_time(start_raw), parse_template_time(end_raw), rate_min))

        rows = []
        for day_offset in range(7):
            lot_date = base_date + timedelta(days=day_offset)
            for start_time, end_time, rate_min in templates:
                rows.append((lot_date, start_time, end_time, rate_min))

        rows = [(*row, mode) for row in rows]

        with self._get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM shift_auction_test_day_offs WHERE COALESCE(direction_mode, 'line') = %s",
                (mode,)
            )
            cursor.execute(
                "DELETE FROM shift_auction_test_lots WHERE COALESCE(direction_mode, 'line') = %s",
                (mode,)
            )
            if rows:
                execute_values(
                    cursor,
                    """
                    INSERT INTO shift_auction_test_lots (shift_date, start_time, end_time, rate_min, direction_mode)
                    VALUES %s
                    """,
                    rows
                )
            event = self._insert_shift_auction_test_event(cursor, "lots_seeded", {
                "count": len(rows),
                "start_date": base_date.strftime('%Y-%m-%d'),
                "updated_by": updated_by
            }, direction_mode=mode)
        return {
            "count": len(rows),
            "event": event,
            "snapshot": self.get_shift_auction_test_snapshot(
                current_user_id=updated_by,
                include_admin_fields=True,
                direction_mode=mode
            )
        }

    def get_shift_auction_test_journal(self, page=1, per_page=50, direction_mode=SHIFT_AUCTION_MODE_LINE):
        settings_id = shift_auction_settings_row_id(direction_mode)
        # Paginated claim journal for the currently-selected auction period.
        # Returns rows ordered newest-first plus a total count for UI paging.
        try:
            page = max(1, int(page or 1))
        except Exception:
            page = 1
        try:
            per_page = max(1, min(200, int(per_page or 50)))
        except Exception:
            per_page = 50
        offset = (page - 1) * per_page
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT selected_schedule_plan_id FROM shift_auction_test_access WHERE id = %s
            """, (settings_id,))
            settings_row = cursor.fetchone()
            plan_id = settings_row[0] if settings_row else None
            if plan_id is None:
                return {"entries": [], "total": 0, "page": page, "per_page": per_page, "has_more": False}

            cursor.execute("""
                SELECT COUNT(*)::int
                FROM shift_auction_test_events e
                WHERE e.event_type IN ('lot_claimed', 'lot_post_auction_claimed')
                  AND NULLIF(e.payload->'lot'->>'source_schedule_plan_id', '')::INTEGER = %s
            """, (plan_id,))
            total_row = cursor.fetchone()
            total = int(total_row[0] or 0) if total_row else 0

            cursor.execute("""
                SELECT
                    e.id,
                    e.created_at,
                    NULLIF(e.payload->'lot'->>'shift_date', '') AS shift_date,
                    NULLIF(e.payload->'lot'->>'start_time', '') AS start_time,
                    NULLIF(e.payload->'lot'->>'end_time', '') AS end_time,
                    COALESCE(
                        NULLIF(e.payload->'lot'->>'claimed_by', ''),
                        NULLIF(e.payload->>'operator_id', '')
                    )::INTEGER AS claimed_by,
                    COALESCE(u.name, NULLIF(e.payload->>'operator_name', ''), '') AS claimed_by_name,
                    NULLIF(e.payload->'lot'->>'source_schedule_plan_id', '')::INTEGER AS source_schedule_plan_id,
                    p.date_from,
                    p.date_to,
                    e.event_type,
                    NULLIF(e.payload->'lot'->>'claim_start_time', '') AS claim_start_time,
                    NULLIF(e.payload->'lot'->>'claim_end_time', '') AS claim_end_time
                FROM shift_auction_test_events e
                LEFT JOIN users u
                  ON u.id = COALESCE(
                      NULLIF(e.payload->'lot'->>'claimed_by', ''),
                      NULLIF(e.payload->>'operator_id', '')
                  )::INTEGER
                LEFT JOIN resource_saved_schedule_plans p
                  ON p.id = NULLIF(e.payload->'lot'->>'source_schedule_plan_id', '')::INTEGER
                WHERE e.event_type IN ('lot_claimed', 'lot_post_auction_claimed')
                  AND NULLIF(e.payload->'lot'->>'source_schedule_plan_id', '')::INTEGER = %s
                ORDER BY e.id DESC
                LIMIT %s OFFSET %s
            """, (plan_id, per_page, offset))
            rows = cursor.fetchall() or []

        entries = []
        for row in rows:
            is_post_auction = (row[10] == 'lot_post_auction_claimed')
            claim_start_time = row[11]
            claim_end_time = row[12]
            # Partial добор: a piece of the original shift window was taken.
            is_partial = bool(
                is_post_auction
                and claim_start_time and claim_end_time
                and (claim_start_time != row[3] or claim_end_time != row[4])
            )
            entries.append({
                "id": row[0],
                "claimed_at": row[1].isoformat() if row[1] else None,
                "shift_date": row[2],
                "start_time": row[3],
                "end_time": row[4],
                "claimed_by": row[5],
                "claimed_by_name": row[6] or "",
                "source_schedule_plan_id": row[7],
                "period_start": row[8].strftime('%Y-%m-%d') if row[8] else None,
                "period_end": row[9].strftime('%Y-%m-%d') if row[9] else None,
                "is_post_auction": is_post_auction,
                "claim_start_time": claim_start_time,
                "claim_end_time": claim_end_time,
                "is_partial": is_partial,
            })
        return {
            "entries": entries,
            "total": total,
            "page": page,
            "per_page": per_page,
            "has_more": (page * per_page) < total,
        }

    def get_shift_auction_test_events_after(self, after_id=0, limit=100, direction_mode=None):
        """События потока после ``after_id``. ``direction_mode=None`` — оба направления.

        Поток SSE читает ВСЕ события специально: курсор клиента должен переезжать и
        через чужие события, иначе он застревал бы на них и перечитывал одно и то же.
        Отсев по направлению делает уже сама выдача — см. api_shift_auction_test_events.
        """
        try:
            after_id = int(after_id or 0)
        except Exception:
            after_id = 0
        limit = max(1, min(int(limit or 100), 500))
        mode_filter = None if direction_mode is None else normalize_shift_auction_mode(direction_mode)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, event_type, payload, created_at, COALESCE(direction_mode, 'line')
                FROM shift_auction_test_events
                WHERE id > %s
                  AND (%s::text IS NULL OR COALESCE(direction_mode, 'line') = %s)
                ORDER BY id
                LIMIT %s
            """, (after_id, mode_filter, mode_filter, limit))
            return [
                {
                    "id": row[0],
                    "event_type": row[1],
                    "payload": row[2] or {},
                    "created_at": row[3].isoformat() if row[3] else None,
                    "direction_mode": row[4] if len(row) > 4 else SHIFT_AUCTION_MODE_LINE,
                }
                for row in (cursor.fetchall() or [])
            ]

    def claim_shift_auction_test_lot(self, operator_id, lot_id,
                                     claim_start_time=None, claim_end_time=None):
        """Взять смену аукциона — целиком или (в чате) выбранной частью.

        Направление НЕ приходит с клиента: оно выводится из карточки оператора, иначе
        подменённым параметром можно было бы забрать смену чужого аукциона.
        """
        operator_id = int(operator_id)
        lot_id = int(lot_id)
        timings = {}
        lock_started_at = time.perf_counter()
        with self._get_cursor() as cursor:
            self._lock_shift_auction_operator_tx(cursor, operator_id)
            mode = self._shift_auction_mode_for_operator_tx(cursor, operator_id)
            if mode is None:
                raise ValueError("NOT_TEST_PARTICIPANT")
            settings_id = shift_auction_settings_row_id(mode)
            wants_partial = bool(claim_start_time) and bool(claim_end_time)
            if wants_partial and mode != SHIFT_AUCTION_MODE_CHAT:
                # Частями смену разбирает только чат: у линии лот берётся целиком.
                raise ValueError("PARTIAL_SHIFT_NOT_ALLOWED")
            timings["lock"] = (time.perf_counter() - lock_started_at) * 1000
            started_at = time.perf_counter()
            cursor.execute("""
                SELECT
                    s.enabled,
                    s.starts_at,
                    s.ends_at,
                    s.paused_at,
                    s.finished_at,
                    EXISTS(SELECT 1 FROM shift_auction_test_participants WHERE operator_id = %s) AS is_participant,
                    (
                        -- Ставка «на дату прогона»: временная подмена
                        -- (operator_rate_overrides) действует в аукционе, но не в
                        -- учёте часов и ЗП. Дату берём из периода выбранного плана
                        -- по корреляции с `s` — новых параметров не добавляется.
                        -- Литерального знака процента здесь быть не должно:
                        -- psycopg2 ищет плейсхолдеры и в комментариях тоже.
                        SELECT operator_effective_rate(u.id, COALESCE(
                            (SELECT p.date_from FROM resource_saved_schedule_plans p
                              WHERE p.id = s.selected_schedule_plan_id),
                            CURRENT_DATE))
                        FROM users u
                        WHERE u.id = %s
                          AND LOWER(COALESCE(u.role, '')) = 'operator'
                          AND COALESCE(u.status, '') NOT IN ('fired', 'dismissal')
                    ) AS operator_rate,
                    s.topup_started_at,
                    tg.starts_at AS group_starts_at,
                    tg.ends_at AS group_ends_at,
                    COALESCE(s.rate_lock_enabled, FALSE) AS rate_lock_enabled,
                    COALESCE(tg.self_schedule_enabled, FALSE) AS self_schedule_enabled
                FROM shift_auction_test_access s
                -- The operator's time group for the active week, if any: their window
                -- and whether they run on their own schedule (wider norm allowance).
                LEFT JOIN LATERAL (
                    SELECT g.starts_at, g.ends_at, COALESCE(g.self_schedule_enabled, FALSE) AS self_schedule_enabled
                    FROM shift_auction_time_group_members m
                    JOIN shift_auction_time_groups g ON g.id = m.group_id
                    WHERE m.plan_id = s.selected_schedule_plan_id
                      AND m.operator_id = %s
                    LIMIT 1
                ) tg ON TRUE
                WHERE s.id = %s
            """, (operator_id, operator_id, operator_id, settings_id))
            header = cursor.fetchone()
            timings["header"] = (time.perf_counter() - started_at) * 1000
            effective_start, effective_end = (
                self._shift_auction_effective_window(header[1], header[2], header[8], header[9])
                if header else (None, None)
            )
            if not header or self._get_shift_auction_test_status(header[0], effective_start, effective_end, header[3], header[4]) != "open":
                raise ValueError("AUCTION_NOT_OPEN")
            if (
                not header[5]
                or operator_id not in self._get_shift_auction_participant_ids_tx(cursor, direction_mode=mode)
            ):
                raise ValueError("NOT_TEST_PARTICIPANT")
            if header[6] is None:
                raise ValueError("OPERATOR_NOT_FOUND")
            operator_rate = float(header[6] or 1)
            is_topup_mode = bool(header[7]) and not header[4]  # topup_started_at set, auction not finished
            rate_lock_enabled = bool(header[10]) if len(header) > 10 else False
            self_schedule_enabled = bool(header[11]) if len(header) > 11 else False

            started_at = time.perf_counter()
            cursor.execute("""
                SELECT id, shift_date, start_time, end_time, rate_min, status, claimed_by, claimed_at, breaks,
                       source_schedule_plan_id, source_schedule_shift_id
                FROM shift_auction_test_lots
                WHERE id = %s
                  AND COALESCE(direction_mode, 'line') = %s
            """, (lot_id, mode))
            lot = cursor.fetchone()
            timings["lot"] = (time.perf_counter() - started_at) * 1000
            if not lot:
                raise ValueError("LOT_NOT_FOUND")
            if lot[5] != 'available':
                raise ValueError("LOT_ALREADY_CLAIMED")
            source_plan_id = int(lot[9]) if lot[9] is not None else None
            source_shift_id = int(lot[10]) if lot[10] is not None else None
            if wants_partial and not (source_plan_id and source_shift_id):
                # Часть можно взять только у смены из плана: занятые куски хранятся
                # парой (план, смена), а у ручного лота её нет.
                raise ValueError("PARTIAL_SHIFT_NOT_ALLOWED")
            # Ставка сверяется по ЛОТУ целиком: длину куска сторожит недельная норма,
            # а не группа лота — иначе 0,75 не смог бы взять часть длинной смены.
            if rate_lock_enabled:
                lot_rate_bucket = self._shift_auction_lot_rate_bucket(lot[2], lot[3])
                if abs(lot_rate_bucket - self._shift_auction_rate_bucket(operator_rate)) > 0.001:
                    raise ValueError("SHIFT_RATE_MISMATCH")

            lot_date = lot[1]
            started_at = time.perf_counter()
            cursor.execute("""
                SELECT
                    EXISTS(
                        SELECT 1 FROM shift_auction_test_day_offs
                        WHERE operator_id = %s AND day_off_date = %s
                          AND COALESCE(direction_mode, 'line') = %s
                    ) AS has_day_off,
                    COALESCE((
                        SELECT array_agg(DISTINCT shift_date ORDER BY shift_date)
                        FROM shift_auction_test_lots
                        WHERE shift_date IS NOT NULL
                          AND COALESCE(direction_mode, 'line') = %s
                    ), ARRAY[]::date[]) AS lot_dates
            """, (operator_id, lot_date, mode, mode))
            checks = cursor.fetchone()
            timings["checks"] = (time.perf_counter() - started_at) * 1000
            has_day_off, lot_dates = checks
            # Занятое оператором = целые лоты + взятые части смен. Раньше здесь читались
            # только claimed-лоты, и частично разобранная смена (лот остаётся
            # 'available') не попадала ни в проверку дня, ни в норму.
            claimed_rows_json = self._get_shift_auction_operator_claimed_intervals_tx(
                cursor, operator_id, direction_mode=mode
            )
            has_shift_on_date = any(
                str(item.get('shift_date') or '') == (lot_date.strftime('%Y-%m-%d') if lot_date else '')
                for item in claimed_rows_json
            )

            started_at = time.perf_counter()
            blocked_dates = self._get_shift_auction_operator_blocked_dates_tx(
                cursor, operator_id, lot_dates=list(lot_dates or [])
            )
            timings["blocked"] = (time.perf_counter() - started_at) * 1000
            blocked_date_map = {item["date"]: item for item in blocked_dates if item.get("date")}
            lot_date_key = lot_date.strftime('%Y-%m-%d') if lot_date else ''
            if lot_date_key and lot_date_key in blocked_date_map:
                raise ValueError("SHIFT_AUCTION_STATUS_PERIOD_BLOCKED")
            if has_day_off:
                raise ValueError("DAY_OFF_SELECTED")

            # Уже занятые части ЭТОЙ смены (чат разбирает смену несколькими людьми).
            existing_segments = []
            if source_plan_id and source_shift_id:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (self.SHIFT_AUCTION_SAVED_SHIFT_LOCK_NAMESPACE, source_shift_id)
                )
                cursor.execute("""
                    SELECT claimed_by, claimed_start_time, claimed_end_time
                    FROM shift_auction_historical_claims
                    WHERE plan_id = %s AND source_schedule_shift_id = %s
                      AND claimed_start_time IS NOT NULL
                      AND claimed_end_time IS NOT NULL
                    FOR UPDATE
                """, (source_plan_id, source_shift_id))
                existing_segments = cursor.fetchall() or []
            if any(row[0] is not None and int(row[0]) == operator_id for row in existing_segments):
                raise ValueError("LOT_ALREADY_CLAIMED")

            lot_start_min, lot_end_min = self._schedule_interval_minutes(lot[2], lot[3])
            busy_ranges = []
            for _, seg_start, seg_end in existing_segments:
                seg_range = self._normalize_post_auction_claim_range(
                    lot[2], lot[3], seg_start.strftime('%H:%M'), seg_end.strftime('%H:%M')
                )
                busy_ranges.append((seg_range["start_minute"], seg_range["end_minute"]))
            free_gaps = [
                gap for gap in self._subtract_ranges(lot_start_min, lot_end_min, busy_ranges)
                if gap[1] - gap[0] > 0
            ]

            if existing_segments and not wants_partial:
                # Клик по частично разобранной смене без явного интервала: берём
                # единственный свободный кусок, иначе просим выбрать.
                if len(free_gaps) != 1:
                    raise ValueError("INVALID_PARTIAL_SHIFT_RANGE")
                claim_start_time = _minutes_to_time(free_gaps[0][0] % (24 * 60)).strftime('%H:%M')
                claim_end_time = _minutes_to_time(free_gaps[0][1] % (24 * 60)).strftime('%H:%M')
                wants_partial = True

            claim_range = self._normalize_post_auction_claim_range(
                lot[2], lot[3],
                claim_start_time=claim_start_time if wants_partial else None,
                claim_end_time=claim_end_time if wants_partial else None,
            )
            candidate_start_min = claim_range["start_minute"]
            candidate_end_min = claim_range["end_minute"]
            is_partial_claim = bool(wants_partial and claim_range["is_partial"])
            if is_partial_claim and not (source_plan_id and source_shift_id):
                raise ValueError("PARTIAL_SHIFT_NOT_ALLOWED")
            # Кусок обязан лечь в свободную часть смены целиком.
            for busy_start, busy_end in busy_ranges:
                if candidate_start_min < busy_end and busy_start < candidate_end_min:
                    raise ValueError("SHIFT_OVERLAPS_EXISTING")

            # Лимит «одна смена в день» снимают два случая, и по разным причинам:
            #  - режим добора — там сняты вообще все потолки, включая норму;
            #  - направления из SHIFT_AUCTION_SEVERAL_SHIFTS_PER_DAY_MODES: ЧАТ (решение
            #    владельца 29.08.2026) — человек вправе добрать часы в том же дне, и
            #    Тез КЦ (12.11.2026) — клетка «11*15 18*20» загружается двумя сменами,
            #    и обе должен мочь взять один человек. Смена не должна перекрывать уже
            #    взятое, а недельная норма ниже по коду продолжает работать.
            # В обоих случаях жёсткий календарный лимит заменяется точной проверкой
            # пересечения по времени.
            allows_extra_shifts_per_day = is_topup_mode or mode in SHIFT_AUCTION_SEVERAL_SHIFTS_PER_DAY_MODES
            if allows_extra_shifts_per_day:
                if self._shift_auction_claim_conflict(
                        claimed_rows_json, lot_date_key,
                        candidate_start_min, candidate_end_min):
                    raise ValueError("SHIFT_OVERLAPS_EXISTING")
            else:
                if has_shift_on_date:
                    raise ValueError("DAY_ALREADY_HAS_SHIFT")

            # Две ночи 20:00–08:00 подряд — нельзя, и в режиме добора тоже: там
            # снят лимит «одна смена в день», но не право на человеческий отдых.
            if self._shift_auction_adjacent_night_date(
                    claimed_rows_json, lot_date, lot[2], lot[3], direction_mode=mode):
                raise ValueError("NIGHT_SHIFT_ALREADY_ADJACENT")

            claimed_rows = [
                (row.get('start_time'), row.get('end_time'), row.get('breaks'))
                for row in (claimed_rows_json or [])
            ]
            workload = self._get_shift_auction_operator_workload_tx(
                cursor, operator_id, operator_rate,
                lot_dates=list(lot_dates or []),
                blocked_dates=blocked_dates,
                claimed_rows=claimed_rows,
                direction_mode=mode
            )
            # Перерывы смены отдаём целиком: в норму пойдёт ровно та их часть,
            # что попала во взятое окно (_shift_auction_break_minutes).
            lot_breaks = lot[8] if isinstance(lot[8], list) else []
            candidate_minutes = self._shift_auction_lot_minutes(
                claim_range["start_time"],
                claim_range["end_time"],
                lot_breaks
            )
            # "Свой график" buys the group a fixed allowance over the norm — it is the
            # only ceiling such an operator has, so it applies to ordinary claims too.
            norm_allowance_minutes = workload["norm_minutes"] + (
                self.SHIFT_AUCTION_SELF_SCHEDULE_EXTRA_MINUTES if self_schedule_enabled else 0
            )
            if (
                not is_topup_mode
                and workload["norm_minutes"] > 0
                and workload["claimed_net_minutes"] + candidate_minutes["net_minutes"] > norm_allowance_minutes + 1
            ):
                raise ValueError("SHIFT_NORM_EXCEEDED")

            cas_started_at = time.perf_counter()
            # Взятая часть живёт отдельной строкой, а лот закрывается только когда
            # покрыта КАЖДАЯ его минута: пока остаётся свободный кусок, смена должна
            # быть видна остальным как доступная.
            remaining_after_claim = [
                gap for gap in self._subtract_ranges(
                    lot_start_min, lot_end_min, busy_ranges + [(candidate_start_min, candidate_end_min)]
                )
                if gap[1] - gap[0] >= self.POST_AUCTION_MIN_REMAINDER_MINUTES
            ]
            keeps_lot_open = bool(is_partial_claim and remaining_after_claim)
            if is_partial_claim:
                cursor.execute("""
                    INSERT INTO shift_auction_historical_claims
                        (plan_id, source_schedule_shift_id, claimed_by, claimed_at,
                         claimed_start_time, claimed_end_time, claim_stage)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s)
                    ON CONFLICT (plan_id, source_schedule_shift_id, claimed_by) DO NOTHING
                """, (
                    source_plan_id, source_shift_id, operator_id,
                    claim_range["start_time"], claim_range["end_time"],
                    self.SHIFT_AUCTION_CLAIM_STAGE_AUCTION,
                ))

            if keeps_lot_open:
                cursor.execute("""
                    UPDATE shift_auction_test_lots
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status = 'available'
                    RETURNING id, shift_date, start_time, end_time, rate_min, status, claimed_by, claimed_at,
                              breaks, source_schedule_plan_id, source_schedule_shift_id
                """, (lot_id,))
            else:
                cursor.execute("""
                    UPDATE shift_auction_test_lots
                    SET status = 'claimed',
                        claimed_by = %s,
                        claimed_at = CURRENT_TIMESTAMP,
                        post_claim_start_time = %s,
                        post_claim_end_time = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status = 'available'
                    RETURNING id, shift_date, start_time, end_time, rate_min, status, claimed_by, claimed_at,
                              breaks, source_schedule_plan_id, source_schedule_shift_id
                """, (
                    operator_id,
                    claim_range["start_time"] if is_partial_claim else None,
                    claim_range["end_time"] if is_partial_claim else None,
                    lot_id,
                ))
            updated = cursor.fetchone()
            timings["cas_update"] = (time.perf_counter() - cas_started_at) * 1000
            if not updated:
                raise ValueError("LOT_ALREADY_CLAIMED")
            event_started_at = time.perf_counter()
            lot_payload = {
                "id": updated[0],
                "shift_date": updated[1].strftime('%Y-%m-%d') if updated[1] else None,
                "start_time": updated[2].strftime('%H:%M') if updated[2] else None,
                "end_time": updated[3].strftime('%H:%M') if updated[3] else None,
                "rate_min": float(updated[4] or 0),
                "status": updated[5],
                # У частично взятой смены лот остаётся свободным, но событие всё равно
                # обязано назвать, КТО взял кусок: иначе журнал и патч по SSE потеряют
                # автора, а фронт нарисует «взяли, но никто».
                "claimed_by": updated[6] if updated[6] is not None else operator_id,
                "claimed_at": updated[7].isoformat() if updated[7] else datetime.now().isoformat(),
                "breaks": updated[8] if isinstance(updated[8], list) else [],
                "source_schedule_plan_id": updated[9],
                "source_schedule_shift_id": updated[10],
                "claim_start_time": claim_range["start_time"].strftime('%H:%M') if is_partial_claim else None,
                "claim_end_time": claim_range["end_time"].strftime('%H:%M') if is_partial_claim else None,
                "partial": bool(is_partial_claim),
                "lot_still_available": bool(keeps_lot_open),
            }
            event = self._insert_shift_auction_test_event(
                cursor, "lot_claimed", {"lot": lot_payload}, direction_mode=mode
            )
            timings["event"] = (time.perf_counter() - event_started_at) * 1000
            timings["write"] = timings["cas_update"] + timings["event"]
        return {"lot": lot_payload, "event": event, "_timings": timings}

    def release_shift_auction_test_lot(self, operator_id, lot_id):
        operator_id = int(operator_id)
        lot_id = int(lot_id)
        with self._get_cursor() as cursor:
            self._lock_shift_auction_operator_tx(cursor, operator_id)
            mode = self._shift_auction_mode_for_operator_tx(cursor, operator_id)
            if mode is None:
                raise ValueError("NOT_TEST_PARTICIPANT")
            settings_id = shift_auction_settings_row_id(mode)
            cursor.execute("""
                SELECT
                    s.enabled,
                    s.starts_at,
                    s.ends_at,
                    s.paused_at,
                    s.finished_at,
                    EXISTS(SELECT 1 FROM shift_auction_test_participants WHERE operator_id = %s) AS is_participant,
                    tg.starts_at AS group_starts_at,
                    tg.ends_at AS group_ends_at
                FROM shift_auction_test_access s
                LEFT JOIN LATERAL (
                    SELECT g.starts_at, g.ends_at
                    FROM shift_auction_time_group_members m
                    JOIN shift_auction_time_groups g ON g.id = m.group_id
                    WHERE m.plan_id = s.selected_schedule_plan_id
                      AND m.operator_id = %s
                    LIMIT 1
                ) tg ON TRUE
                WHERE s.id = %s
            """, (operator_id, operator_id, settings_id))
            header = cursor.fetchone()
            effective_start, effective_end = (
                self._shift_auction_effective_window(header[1], header[2], header[6], header[7])
                if header else (None, None)
            )
            if not header or self._get_shift_auction_test_status(header[0], effective_start, effective_end, header[3], header[4]) != "open":
                raise ValueError("AUCTION_NOT_OPEN")
            if (
                not header[5]
                or operator_id not in self._get_shift_auction_participant_ids_tx(cursor, direction_mode=mode)
            ):
                raise ValueError("NOT_TEST_PARTICIPANT")

            cursor.execute("""
                SELECT id, shift_date, start_time, end_time, rate_min, status,
                       claimed_by, claimed_at, breaks, source_schedule_plan_id, source_schedule_shift_id,
                       COALESCE(post_auction_claimed, FALSE), self_scheduled_by
                FROM shift_auction_test_lots
                WHERE id = %s
                  AND COALESCE(direction_mode, 'line') = %s
                FOR UPDATE
            """, (lot_id, mode))
            lot = cursor.fetchone()
            if lot is None:
                raise ValueError("LOT_NOT_FOUND")
            if bool(lot[11]):
                raise ValueError("POST_AUCTION_LOT_NOT_RELEASABLE")

            # Взятая ЧАСТЬ смены: лот мог остаться свободным, владелец записан строкой
            # части. Такой возврат снимает только свой кусок и не трогает чужие.
            released_segment = None
            if lot[9] is not None and lot[10] is not None:
                cursor.execute("""
                    DELETE FROM shift_auction_historical_claims
                    WHERE plan_id = %s AND source_schedule_shift_id = %s
                      AND claimed_by = %s
                      AND COALESCE(claim_stage, 'post_auction') = %s
                    RETURNING claimed_start_time, claimed_end_time
                """, (
                    int(lot[9]), int(lot[10]), operator_id,
                    self.SHIFT_AUCTION_CLAIM_STAGE_AUCTION,
                ))
                released_segment = cursor.fetchone()

            if released_segment is None:
                if lot[5] != 'claimed':
                    raise ValueError("LOT_NOT_CLAIMED")
                if lot[6] is None or int(lot[6]) != operator_id:
                    raise ValueError("LOT_NOT_OWNED")

            # A self-scheduled shift is the operator's own creation, not a slot from
            # the plan: giving it up removes it instead of offering it to everybody.
            if lot[12] is not None and int(lot[12]) == operator_id:
                return self._remove_self_scheduled_shift_tx(cursor, operator_id, lot, direction_mode=mode)

            # Смена снова свободна целиком; если часть отдал один из нескольких —
            # тоже снимаем закрепление, оставшиеся части живут своими строками.
            cursor.execute("""
                UPDATE shift_auction_test_lots
                SET status = 'available',
                    claimed_by = NULL,
                    claimed_at = NULL,
                    post_claim_start_time = NULL,
                    post_claim_end_time = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING id, shift_date, start_time, end_time, rate_min, status, claimed_by, claimed_at,
                          breaks, source_schedule_plan_id, source_schedule_shift_id
            """, (lot_id,))
            updated = cursor.fetchone()
            lot_payload = {
                "id": updated[0],
                "shift_date": updated[1].strftime('%Y-%m-%d') if updated[1] else None,
                "start_time": updated[2].strftime('%H:%M') if updated[2] else None,
                "end_time": updated[3].strftime('%H:%M') if updated[3] else None,
                "rate_min": float(updated[4] or 0),
                "status": updated[5],
                "claimed_by": updated[6],
                "claimed_at": updated[7].isoformat() if updated[7] else None,
                "breaks": updated[8] if isinstance(updated[8], list) else [],
                "source_schedule_plan_id": updated[9],
                "source_schedule_shift_id": updated[10],
                "claim_start_time": released_segment[0].strftime('%H:%M') if released_segment and released_segment[0] else None,
                "claim_end_time": released_segment[1].strftime('%H:%M') if released_segment and released_segment[1] else None,
                "partial": bool(released_segment),
            }
            event = self._insert_shift_auction_test_event(cursor, "lot_released", {
                "lot": lot_payload,
                "operator_id": operator_id
            }, direction_mode=mode)
        return {"lot": lot_payload, "event": event}

    def self_schedule_shift_auction_shift(self, operator_id, shift_date, start_time):
        """Operator of a "свой график" group puts a shift on a day themselves.

        The shift length follows their rate (a 20:00 start of a full-rate operator
        is the 20*08 night shift, exactly as in the grid), and the total may not
        exceed their norm plus ``SHIFT_AUCTION_SELF_SCHEDULE_EXTRA_MINUTES``.
        """
        operator_id = int(operator_id)
        if hasattr(shift_date, 'strftime'):
            date_obj = shift_date
        else:
            try:
                date_obj = datetime.strptime(str(shift_date), '%Y-%m-%d').date()
            except Exception:
                raise ValueError("INVALID_DATE")
        start_obj = self._normalize_schedule_time(start_time, "start_time")

        with self._get_cursor() as cursor:
            self._lock_shift_auction_operator_tx(cursor, operator_id)
            mode = self._shift_auction_mode_for_operator_tx(cursor, operator_id)
            if mode is None:
                raise ValueError("NOT_TEST_PARTICIPANT")
            settings_id = shift_auction_settings_row_id(mode)
            cursor.execute("""
                SELECT
                    s.enabled,
                    s.starts_at,
                    s.ends_at,
                    s.paused_at,
                    s.finished_at,
                    s.selected_schedule_plan_id,
                    EXISTS(SELECT 1 FROM shift_auction_test_participants WHERE operator_id = %s) AS is_participant,
                    (
                        -- Ставка «на дату прогона»: временная подмена
                        -- (operator_rate_overrides) действует в аукционе, но не в
                        -- учёте часов и ЗП. Дату берём из периода выбранного плана
                        -- по корреляции с `s` — новых параметров не добавляется.
                        -- Литерального знака процента здесь быть не должно:
                        -- psycopg2 ищет плейсхолдеры и в комментариях тоже.
                        SELECT operator_effective_rate(u.id, COALESCE(
                            (SELECT p.date_from FROM resource_saved_schedule_plans p
                              WHERE p.id = s.selected_schedule_plan_id),
                            CURRENT_DATE))
                        FROM users u
                        WHERE u.id = %s
                          AND LOWER(COALESCE(u.role, '')) = 'operator'
                          AND COALESCE(u.status, '') NOT IN ('fired', 'dismissal')
                    ) AS operator_rate,
                    tg.starts_at AS group_starts_at,
                    tg.ends_at AS group_ends_at,
                    COALESCE(tg.self_schedule_enabled, FALSE) AS self_schedule_enabled
                FROM shift_auction_test_access s
                LEFT JOIN LATERAL (
                    SELECT g.starts_at, g.ends_at, COALESCE(g.self_schedule_enabled, FALSE) AS self_schedule_enabled
                    FROM shift_auction_time_group_members m
                    JOIN shift_auction_time_groups g ON g.id = m.group_id
                    WHERE m.plan_id = s.selected_schedule_plan_id
                      AND m.operator_id = %s
                    LIMIT 1
                ) tg ON TRUE
                WHERE s.id = %s
            """, (operator_id, operator_id, operator_id, settings_id))
            header = cursor.fetchone()
            if not header:
                raise ValueError("AUCTION_NOT_AVAILABLE")
            effective_start, effective_end = self._shift_auction_effective_window(
                header[1], header[2], header[8], header[9]
            )
            if self._get_shift_auction_test_status(header[0], effective_start, effective_end, header[3], header[4]) != "open":
                raise ValueError("AUCTION_NOT_OPEN")
            if (
                not header[6]
                or operator_id not in self._get_shift_auction_participant_ids_tx(cursor, direction_mode=mode)
            ):
                raise ValueError("NOT_TEST_PARTICIPANT")
            if not header[10]:
                raise ValueError("SELF_SCHEDULE_NOT_ALLOWED")
            if header[7] is None:
                raise ValueError("OPERATOR_NOT_FOUND")

            plan_id = int(header[5]) if header[5] is not None else None
            operator_rate = float(header[7] or 1)
            # Shift length comes from the grid's rate buckets, so an off-catalogue
            # rate still maps to a real shift instead of being rejected. The norm
            # below keeps using the operator's actual rate, as everywhere else.
            rate_bucket = self._shift_auction_rate_bucket(operator_rate)
            is_night = rate_bucket == 1.0 and start_obj.strftime('%H:%M') == '20:00'
            shift_minutes = (
                self.SHIFT_AUCTION_NIGHT_SHIFT_MINUTES if is_night
                else self.SHIFT_AUCTION_RATE_SHIFT_MINUTES.get(rate_bucket)
            )
            if not shift_minutes:
                raise ValueError("INVALID_RATE")
            start_min = _time_to_minutes(start_obj.strftime('%H:%M'))
            end_min = start_min + shift_minutes
            end_obj = dt_time(hour=(end_min // 60) % 24, minute=end_min % 60)

            if datetime.combine(date_obj, start_obj) <= datetime.now():
                raise ValueError("SHIFT_ALREADY_STARTED")

            lot_dates = self._get_shift_auction_lot_dates_tx(cursor, direction_mode=mode)
            if not lot_dates:
                raise ValueError("AUCTION_NO_LOTS")
            if date_obj not in set(lot_dates):
                raise ValueError("DATE_OUT_OF_RANGE")

            date_key = date_obj.strftime('%Y-%m-%d')
            blocked_dates = self._get_shift_auction_operator_blocked_dates_tx(
                cursor, operator_id, lot_dates=lot_dates
            )
            if any(item.get("date") == date_key for item in blocked_dates):
                raise ValueError("SHIFT_AUCTION_STATUS_PERIOD_BLOCKED")

            cursor.execute("""
                SELECT 1 FROM shift_auction_test_day_offs
                WHERE operator_id = %s AND day_off_date = %s
                  AND COALESCE(direction_mode, 'line') = %s
            """, (operator_id, date_obj, mode))
            if cursor.fetchone():
                raise ValueError("DAY_OFF_SELECTED")

            own_claims = self._get_shift_auction_operator_claimed_intervals_tx(
                cursor, operator_id, direction_mode=mode
            )
            # У чата и Теза день не запирается первой сменой: можно поставить ещё часы,
            # если они не перекрывают уже взятое (потолок держит норма ниже по коду).
            if mode in SHIFT_AUCTION_SEVERAL_SHIFTS_PER_DAY_MODES:
                if self._shift_auction_claim_conflict(own_claims, date_key, start_min, end_min):
                    raise ValueError("SHIFT_OVERLAPS_EXISTING")
            elif any(str(item.get('shift_date') or '') == date_key for item in own_claims):
                raise ValueError("DAY_ALREADY_HAS_SHIFT")

            # «Свой график» тоже не даёт поставить себе две ночи 20:00–08:00 подряд.
            if self._shift_auction_adjacent_night_date(
                    own_claims, date_obj,
                    start_obj.strftime('%H:%M'), end_obj.strftime('%H:%M'),
                    direction_mode=mode):
                raise ValueError("NIGHT_SHIFT_ALREADY_ADJACENT")

            workload = self._get_shift_auction_operator_workload_tx(
                cursor, operator_id, operator_rate,
                lot_dates=lot_dates,
                blocked_dates=blocked_dates,
                direction_mode=mode
            )
            allowance = workload["norm_minutes"] + self.SHIFT_AUCTION_SELF_SCHEDULE_EXTRA_MINUTES
            if workload["norm_minutes"] > 0 and workload["claimed_net_minutes"] + shift_minutes > allowance + 1:
                raise ValueError("SELF_SCHEDULE_LIMIT_EXCEEDED")

            # Backed by a real saved shift (like manager-added ones) so publishing to
            # work schedules and the post-auction flow treat it as an ordinary shift.
            source_shift_id = None
            if plan_id is not None:
                client_shift_id = (
                    f"auction-self-{operator_id}-{date_obj.isoformat()}-{start_obj.strftime('%H%M')}"
                )[:180]
                cursor.execute("""
                    INSERT INTO resource_saved_schedule_shifts (
                        plan_id, client_shift_id, shift_date, start_minute, end_minute,
                        start_time, end_time, duration_minutes, rate_min, label,
                        template_id, source, tone, overnight, breaks, meta
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', 'auction-self', '', %s, '[]'::jsonb, %s)
                    RETURNING id
                """, (
                    plan_id, client_shift_id, date_obj, start_min, end_min,
                    start_obj, end_obj, shift_minutes, rate_bucket,
                    f"{start_obj.strftime('%H:%M')}-{end_obj.strftime('%H:%M')}",
                    end_min >= 24 * 60,
                    Json({"selfScheduledBy": operator_id}),
                ))
                shift_row = cursor.fetchone()
                source_shift_id = int(shift_row[0]) if shift_row else None

            cursor.execute("""
                INSERT INTO shift_auction_test_lots (
                    shift_date, start_time, end_time, rate_min, breaks,
                    source_schedule_plan_id, source_schedule_shift_id,
                    status, claimed_by, claimed_at, self_scheduled_by, direction_mode
                )
                VALUES (%s, %s, %s, %s, '[]'::jsonb, %s, %s, 'claimed', %s, CURRENT_TIMESTAMP, %s, %s)
                RETURNING id, claimed_at
            """, (
                date_obj, start_obj, end_obj, rate_bucket,
                (plan_id if source_shift_id else None), source_shift_id,
                operator_id, operator_id, mode,
            ))
            new_row = cursor.fetchone()

            lot_payload = {
                "id": int(new_row[0]),
                "shift_date": date_key,
                "start_time": start_obj.strftime('%H:%M'),
                "end_time": end_obj.strftime('%H:%M'),
                "rate_min": rate_bucket,
                "status": "claimed",
                "claimed_by": operator_id,
                "claimed_at": new_row[1].isoformat() if new_row[1] else None,
                "breaks": [],
                "source_schedule_plan_id": (plan_id if source_shift_id else None),
                "source_schedule_shift_id": source_shift_id,
                "self_scheduled": True,
            }
            # A brand-new row cannot be patched into existing client state, so this
            # event intentionally makes clients pull a fresh snapshot.
            event = self._insert_shift_auction_test_event(cursor, "shift_self_scheduled", {
                "lot": lot_payload,
                "operator_id": operator_id,
            }, direction_mode=mode)

        return {
            "lot": lot_payload,
            "event": event,
            "net_minutes": shift_minutes,
        }

    def post_auction_claim_lot(self, operator_id, lot_id, claim_start_time=None, claim_end_time=None):
        """
        Забрать смену после окончания аукциона (после публикации в графики).
        Лот должен быть available или cancelled, смена ещё не началась,
        и не должен пересекаться (только TOUCH допустим) с реальными сменами оператора.
        Смена сразу сохраняется в work_shifts. Если стыкуется с соседней —
        соседние объединяются в одну, перерывы пересчитываются автоматически.
        """
        operator_id = int(operator_id)
        lot_id = int(lot_id)
        with self._get_cursor() as cursor:
            self._lock_shift_auction_operator_tx(cursor, operator_id)
            mode = self._shift_auction_mode_for_operator_tx(cursor, operator_id)
            if mode is None:
                raise ValueError("NOT_TEST_PARTICIPANT")
            settings_id = shift_auction_settings_row_id(mode)

            cursor.execute("""
                SELECT
                    s.published_to_work_schedules_at,
                    EXISTS(SELECT 1 FROM shift_auction_test_participants WHERE operator_id = %s) AS is_participant,
                    (
                        SELECT name FROM users WHERE id = %s
                    ) AS operator_name,
                    (
                        SELECT COALESCE(LOWER(role), '') FROM users WHERE id = %s
                    ) AS operator_role,
                    (
                        SELECT COALESCE(status, '') FROM users WHERE id = %s
                    ) AS operator_status
                FROM shift_auction_test_access s
                WHERE s.id = %s
            """, (operator_id, operator_id, operator_id, operator_id, settings_id))
            header = cursor.fetchone()
            if not header:
                raise ValueError("AUCTION_NOT_AVAILABLE")
            if not header[0]:
                raise ValueError("POST_AUCTION_NOT_ACTIVE")
            if (
                not header[1]
                or operator_id not in self._get_shift_auction_participant_ids_tx(cursor, direction_mode=mode)
            ):
                raise ValueError("NOT_TEST_PARTICIPANT")
            if header[3] != 'operator' or header[4] in ('fired', 'dismissal'):
                raise ValueError("OPERATOR_NOT_FOUND")
            operator_name = header[2] or f"Operator #{operator_id}"

            cursor.execute("""
                SELECT id, shift_date, start_time, end_time, rate_min, status,
                       claimed_by, claimed_at, breaks, source_schedule_plan_id, source_schedule_shift_id,
                       COALESCE(post_auction_claimed, FALSE)
                FROM shift_auction_test_lots
                WHERE id = %s
                  AND COALESCE(direction_mode, 'line') = %s
                FOR UPDATE
            """, (lot_id, mode))
            lot = cursor.fetchone()
            if not lot:
                raise ValueError("LOT_NOT_FOUND")
            if bool(lot[11]):
                raise ValueError("LOT_ALREADY_CLAIMED")
            if lot[5] not in ('available', 'cancelled'):
                raise ValueError("LOT_NOT_OPEN_FOR_POST_CLAIM")

            shift_date = lot[1]
            start_time = lot[2]
            end_time = lot[3]
            if shift_date is None or start_time is None or end_time is None:
                raise ValueError("LOT_INVALID")

            source_plan_id = int(lot[9]) if lot[9] is not None else None
            source_shift_id = int(lot[10]) if lot[10] is not None else None
            if source_shift_id:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (self.SHIFT_AUCTION_SAVED_SHIFT_LOCK_NAMESPACE, source_shift_id)
                )

            claim_range = self._normalize_post_auction_claim_range(
                start_time,
                end_time,
                claim_start_time=claim_start_time,
                claim_end_time=claim_end_time
            )
            selected_start_time = claim_range["start_time"]
            selected_end_time = claim_range["end_time"]

            # День, в котором кусок реально НАЧИНАЕТСЯ: у ночи 20:00–08:00 хвост
            # 00:00–08:00 принадлежит следующему дню. В график, выходные, перерывы и
            # часы он обязан лечь именно туда, иначе ночь уезжает на сутки назад —
            # в среду оказываются две ночи, а ночь на четверг остаётся без человека.
            claim_day_shift = 1 if claim_range["start_minute"] >= 24 * 60 else 0
            shift_start_date = shift_date + timedelta(days=claim_day_shift)
            shift_start_dt = datetime.combine(shift_start_date, selected_start_time)
            if shift_start_dt <= datetime.now():
                raise ValueError("SHIFT_ALREADY_STARTED")

            blocked_dates = self._get_shift_auction_operator_blocked_dates_tx(
                cursor, operator_id, lot_dates=[shift_start_date]
            )
            claim_date_key = shift_start_date.strftime('%Y-%m-%d')
            if any((item.get("date") or '') == claim_date_key for item in blocked_dates):
                raise ValueError("SHIFT_AUCTION_STATUS_PERIOD_BLOCKED")

            cursor.execute(
                "SELECT 1 FROM days_off WHERE operator_id = %s AND day_off_date = %s",
                (operator_id, shift_start_date)
            )
            day_off_row = cursor.fetchone()

            # Минуты куска приходят сквозными от начала ЛОТА (00:00 ночи — это 1440);
            # для сравнения со сменами своего дня их надо вернуть внутрь суток.
            new_start_min = claim_range["start_minute"]
            new_end_min = claim_range["end_minute"]
            day_start_min = new_start_min - claim_day_shift * 24 * 60
            day_end_min = new_end_min - claim_day_shift * 24 * 60

            # Что оператор уже держит в этом прогоне. Раньше добор сверялся ТОЛЬКО с
            # частями той же исходной смены, а свои же ДРУГИЕ лоты того дня не смотрел
            # вовсе: пересечение молча уходило в _resolve_post_auction_merged_shift_range
            # и склеивалось. Так 06.09 у одного человека оказались разом 20:00–00:00 и
            # 20:00–01:00 — два лота в аукционе, одна смена в графике, двойной счёт в норме.
            own_claims = self._get_shift_auction_operator_claimed_intervals_tx(
                cursor, operator_id, direction_mode=mode
            )
            # Сквозное время, а не сравнение внутри одного дня: хвост ночи лежит уже
            # в следующем дне, и посуточная проверка его пару не видела.
            if self._shift_auction_claim_conflict(
                    own_claims, claim_date_key, day_start_min, day_end_min):
                raise ValueError("SHIFT_OVERLAPS_EXISTING")

            # Две ночи 20:00–08:00 подряд не даём и в доборе.
            if self._shift_auction_adjacent_night_date(
                    own_claims, shift_date, start_time, end_time, direction_mode=mode):
                raise ValueError("NIGHT_SHIFT_ALREADY_ADJACENT")

            cursor.execute("""
                SELECT id, start_time, end_time
                FROM work_shifts
                WHERE operator_id = %s AND shift_date = %s
                ORDER BY start_time
            """, (operator_id, shift_start_date))
            existing_shifts = cursor.fetchall() or []

            merged_start_min, merged_end_min, merge_ids = self._resolve_post_auction_merged_shift_range(
                existing_shifts,
                day_start_min,
                day_end_min
            )

            # SINGLE-LOT model for partial доборы: the shift stays ONE lot. Each taken
            # piece is recorded in shift_auction_historical_claims (several operators may
            # take disjoint parts). The lot only becomes fully 'claimed' once every minute
            # is covered; otherwise it stays 'available' and shows the free part.
            existing_claims = []
            if source_plan_id and source_shift_id:
                cursor.execute("""
                    SELECT claimed_by, claimed_start_time, claimed_end_time
                    FROM shift_auction_historical_claims
                    WHERE plan_id = %s AND source_schedule_shift_id = %s
                    FOR UPDATE
                """, (source_plan_id, source_shift_id))
                existing_claims = cursor.fetchall() or []
                for ex_by, ex_start, ex_end in existing_claims:
                    if ex_by is not None and int(ex_by) == operator_id:
                        raise ValueError("LOT_ALREADY_CLAIMED")
                    if ex_start is None or ex_end is None:
                        raise ValueError("LOT_ALREADY_CLAIMED")
                    ex_range = self._normalize_post_auction_claim_range(
                        start_time, end_time, ex_start.strftime('%H:%M'), ex_end.strftime('%H:%M')
                    )
                    if new_start_min < ex_range["end_minute"] and ex_range["start_minute"] < new_end_min:
                        raise ValueError("SHIFT_OVERLAPS_EXISTING")
                cursor.execute("""
                    INSERT INTO shift_auction_historical_claims
                        (plan_id, source_schedule_shift_id, claimed_by, claimed_at, claimed_start_time, claimed_end_time)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s)
                    ON CONFLICT (plan_id, source_schedule_shift_id, claimed_by) DO NOTHING
                """, (source_plan_id, source_shift_id, operator_id, selected_start_time, selected_end_time))

            # Does this claim complete the shift? (no source shift = single-claim lot)
            full_start_min, full_end_min = self._schedule_interval_minutes(start_time, end_time)
            claimed_ranges = [(new_start_min, new_end_min)]
            for ex_by, ex_start, ex_end in existing_claims:
                if ex_start is None or ex_end is None:
                    continue
                ex_range = self._normalize_post_auction_claim_range(
                    start_time, end_time, ex_start.strftime('%H:%M'), ex_end.strftime('%H:%M')
                )
                claimed_ranges.append((ex_range["start_minute"], ex_range["end_minute"]))
            free_gaps = [g for g in self._subtract_ranges(full_start_min, full_end_min, claimed_ranges) if g[1] - g[0] > 0]
            fully_covered = (not source_shift_id) or (not free_gaps)

            if fully_covered:
                cursor.execute("""
                    UPDATE shift_auction_test_lots
                    SET status = 'claimed',
                        claimed_by = %s,
                        claimed_at = CURRENT_TIMESTAMP,
                        post_auction_claimed = TRUE,
                        post_claim_start_time = %s,
                        post_claim_end_time = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status IN ('available', 'cancelled')
                    RETURNING id, shift_date, start_time, end_time, rate_min, status, claimed_by, claimed_at,
                              breaks, source_schedule_plan_id, source_schedule_shift_id,
                              COALESCE(post_auction_claimed, FALSE), post_claim_start_time, post_claim_end_time
                """, (operator_id, selected_start_time, selected_end_time, lot_id))
            else:
                # Partial: keep the lot available so others can take the rest.
                cursor.execute("""
                    UPDATE shift_auction_test_lots
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status IN ('available', 'cancelled')
                    RETURNING id, shift_date, start_time, end_time, rate_min, status, claimed_by, claimed_at,
                              breaks, source_schedule_plan_id, source_schedule_shift_id,
                              COALESCE(post_auction_claimed, FALSE), post_claim_start_time, post_claim_end_time
                """, (lot_id,))
            updated = cursor.fetchone()
            if not updated:
                raise ValueError("LOT_ALREADY_CLAIMED")

            audit_actor = self._schedule_change_actor(
                cursor, actor_id=operator_id, source='auction_topup'
            )
            audit_before = self._snapshot_schedule_days_tx(cursor, [(operator_id, shift_start_date)])

            if day_off_row:
                cursor.execute(
                    "DELETE FROM days_off WHERE operator_id = %s AND day_off_date = %s",
                    (operator_id, shift_start_date)
                )

            # Снимок до слияния смен: добор к идущей смене не должен переставлять
            # уже отсиженные перерывы (они уедут каскадом вместе со старой строкой).
            day_breaks_snapshot = self._load_day_shift_breaks_tx(cursor, operator_id, shift_start_date)
            if merge_ids:
                cursor.execute("DELETE FROM work_shifts WHERE id = ANY(%s)", (merge_ids,))
                merged_start_obj = self._normalize_schedule_time(
                    _minutes_to_time(merged_start_min % (24 * 60)), 'start'
                )
                merged_end_obj = self._normalize_schedule_time(
                    _minutes_to_time(merged_end_min),
                    'end'
                )
                self._save_shift_tx(
                    cursor=cursor,
                    operator_id=operator_id,
                    shift_date=shift_start_date,
                    start_time=merged_start_obj,
                    end_time=merged_end_obj,
                    breaks=None,
                    shift_type=None,
                    day_breaks_snapshot=day_breaks_snapshot,
                )
            else:
                self._save_shift_tx(
                    cursor=cursor,
                    operator_id=operator_id,
                    shift_date=shift_start_date,
                    start_time=selected_start_time,
                    end_time=selected_end_time,
                    breaks=None,
                    shift_type=None,
                    day_breaks_snapshot=day_breaks_snapshot,
                )

            self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)

            self._recalculate_auto_daily_hours_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=shift_start_date,
                end_date=shift_start_date
            )

            lot_payload = {
                "id": updated[0],
                "shift_date": updated[1].strftime('%Y-%m-%d') if updated[1] else None,
                "start_time": updated[2].strftime('%H:%M') if updated[2] else None,
                "end_time": updated[3].strftime('%H:%M') if updated[3] else None,
                "rate_min": float(updated[4] or 0),
                "status": updated[5],
                "claimed_by": updated[6],
                "claimed_at": updated[7].isoformat() if updated[7] else None,
                "breaks": updated[8] if isinstance(updated[8], list) else [],
                "source_schedule_plan_id": updated[9],
                "source_schedule_shift_id": updated[10],
                "post_auction_claimed": bool(updated[11]),
                # The journal / Telegram / monitoring need the part this operator took,
                # even for a partial claim where the lot stays available (post_claim NULL).
                "claim_start_time": (updated[12].strftime('%H:%M') if updated[12] else (selected_start_time.strftime('%H:%M') if selected_start_time else None)),
                "claim_end_time": (updated[13].strftime('%H:%M') if updated[13] else (selected_end_time.strftime('%H:%M') if selected_end_time else None)),
            }
            event = self._insert_shift_auction_test_event(cursor, "lot_post_auction_claimed", {
                "lot": lot_payload,
                "operator_id": operator_id,
                "operator_name": operator_name,
                "merged_with_existing": bool(merge_ids),
            }, direction_mode=mode)

        return {
            "lot": lot_payload,
            "event": event,
            "operator": {"id": operator_id, "name": operator_name},
            "merged_with_existing": bool(merge_ids),
        }

    def post_auction_claim_saved_shift(self, operator_id, schedule_plan_id, source_schedule_shift_id, claim_start_time=None, claim_end_time=None):
        """
        Забор смены из УЖЕ ОПУБЛИКОВАННОГО прошлого периода (исторический добор).
        Источник правды о занятости — таблица shift_auction_historical_claims
        с уникальным ключом (plan_id, source_schedule_shift_id): двойной claim
        отлавливается атомарно на уровне БД, без сканирования журнала событий.
        """
        operator_id = int(operator_id)
        schedule_plan_id = int(schedule_plan_id)
        source_schedule_shift_id = int(source_schedule_shift_id)
        if schedule_plan_id <= 0 or source_schedule_shift_id <= 0:
            raise ValueError("LOT_NOT_FOUND")

        with self._get_cursor() as cursor:
            self._lock_shift_auction_operator_tx(cursor, operator_id)
            # Направление берём с ПЛАНА: пост-аукционный добор идёт по опубликованной
            # неделе, и именно она говорит, чей это аукцион.
            mode = self._shift_auction_mode_for_plan_tx(cursor, schedule_plan_id)
            settings_id = shift_auction_settings_row_id(mode)
            scope_sql, scope_params = shift_auction_direction_scope_sql(mode)
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                (self.SHIFT_AUCTION_SAVED_SHIFT_LOCK_NAMESPACE, source_schedule_shift_id)
            )

            cursor.execute("""
                SELECT plan_id, date_from, date_to, participant_ids
                FROM shift_auction_published_periods
                WHERE plan_id = %s
            """, (schedule_plan_id,))
            period_row = cursor.fetchone()
            if not period_row:
                raise ValueError("POST_AUCTION_NOT_ACTIVE")
            plan_id = int(period_row[0])
            participant_ids = set(int(x) for x in (period_row[3] or []) if x is not None)

            cursor.execute("""
                SELECT selected_schedule_plan_id
                FROM shift_auction_test_access
                WHERE id = %s
            """, (settings_id,))
            settings_row = cursor.fetchone()
            current_plan_id = None
            try:
                current_plan_id = int(settings_row[0]) if settings_row and settings_row[0] is not None else None
            except Exception:
                current_plan_id = None
            if current_plan_id == plan_id:
                participant_ids.update(self._get_shift_auction_participant_ids_tx(cursor, direction_mode=mode))
            if operator_id not in participant_ids:
                raise ValueError("NOT_TEST_PARTICIPANT")

            cursor.execute(f"""
                SELECT u.name, u.role, COALESCE(u.status, ''), u.direction_id, d.name AS direction_name
                FROM users u
                JOIN directions d ON d.id = u.direction_id
                JOIN departments dep ON dep.id = d.department_id
                WHERE u.id = %s
                  AND LOWER(COALESCE(u.role, '')) = 'operator'
                  AND COALESCE(u.status, '') NOT IN ('fired', 'dismissal')
                  AND {scope_sql}
            """, (
                operator_id,
                *scope_params,
            ))
            operator_row = cursor.fetchone()
            if (
                not operator_row
                or normalize_role_value(operator_row[1]) != 'operator'
                or str(operator_row[2] or '').strip().lower() in ('fired', 'dismissal')
            ):
                raise ValueError("OPERATOR_NOT_FOUND")
            operator_name = operator_row[0] or f"Operator #{operator_id}"

            cursor.execute("""
                SELECT id, shift_date, start_time, end_time, rate_min, breaks, start_minute, end_minute
                FROM resource_saved_schedule_shifts
                WHERE id = %s
                  AND plan_id = %s
                  AND COALESCE(meta->>'excludeFromAuction', 'false') <> 'true'
                FOR UPDATE
            """, (source_schedule_shift_id, plan_id))
            saved_shift = cursor.fetchone()
            if not saved_shift:
                raise ValueError("LOT_NOT_FOUND")

            shift_date = saved_shift[1]
            start_time = saved_shift[2]
            end_time = saved_shift[3]
            if shift_date is None or start_time is None or end_time is None:
                raise ValueError("LOT_INVALID")

            claim_range = self._normalize_post_auction_claim_range(
                start_time,
                end_time,
                claim_start_time=claim_start_time,
                claim_end_time=claim_end_time
            )
            selected_start_time = claim_range["start_time"]
            selected_end_time = claim_range["end_time"]

            # День, в котором кусок реально НАЧИНАЕТСЯ: у ночи 20:00–08:00 хвост
            # 00:00–08:00 принадлежит следующему дню. В график, выходные, перерывы и
            # часы он обязан лечь именно туда, иначе ночь уезжает на сутки назад —
            # в среду оказываются две ночи, а ночь на четверг остаётся без человека.
            claim_day_shift = 1 if claim_range["start_minute"] >= 24 * 60 else 0
            shift_start_date = shift_date + timedelta(days=claim_day_shift)
            shift_start_dt = datetime.combine(shift_start_date, selected_start_time)
            if shift_start_dt <= datetime.now():
                raise ValueError("SHIFT_ALREADY_STARTED")

            blocked_dates = self._get_shift_auction_operator_blocked_dates_tx(
                cursor, operator_id, lot_dates=[shift_start_date]
            )
            claim_date_key = shift_start_date.strftime('%Y-%m-%d')
            if any((item.get("date") or '') == claim_date_key for item in blocked_dates):
                raise ValueError("SHIFT_AUCTION_STATUS_PERIOD_BLOCKED")

            # Disjoint partial доборы are allowed: several operators may each take a
            # non-overlapping slice of the same shift. Reject only if the requested
            # range overlaps an existing claim (or a full-shift claim already exists).
            cursor.execute("""
                SELECT claimed_by, claimed_start_time, claimed_end_time
                FROM shift_auction_historical_claims
                WHERE plan_id = %s AND source_schedule_shift_id = %s
                FOR UPDATE
            """, (plan_id, source_schedule_shift_id))
            existing_claims = cursor.fetchall() or []
            req_start_min = claim_range["start_minute"]
            req_end_min = claim_range["end_minute"]
            for ex_by, ex_start, ex_end in existing_claims:
                if ex_by is not None and int(ex_by) == operator_id:
                    raise ValueError("LOT_ALREADY_CLAIMED")
                if ex_start is None or ex_end is None:
                    raise ValueError("LOT_ALREADY_CLAIMED")
                ex_range = self._normalize_post_auction_claim_range(
                    start_time, end_time,
                    ex_start.strftime('%H:%M'), ex_end.strftime('%H:%M')
                )
                if req_start_min < ex_range["end_minute"] and ex_range["start_minute"] < req_end_min:
                    raise ValueError("LOT_ALREADY_CLAIMED")

            # Проверки СВОИХ смен — до вставки: после неё откатывать пришлось бы
            # исключением, а строка добора уже была бы в таблице.
            own_claims = self._get_shift_auction_operator_claimed_intervals_tx(
                cursor, operator_id, direction_mode=mode
            )
            # Сквозное время, а не сравнение внутри одного дня: хвост ночи лежит уже
            # в следующем дне, и посуточная проверка его пару не видела.
            if self._shift_auction_claim_conflict(
                    own_claims, claim_date_key,
                    req_start_min - claim_day_shift * 24 * 60,
                    req_end_min - claim_day_shift * 24 * 60):
                raise ValueError("SHIFT_OVERLAPS_EXISTING")
            if self._shift_auction_adjacent_night_date(
                    own_claims, shift_date, start_time, end_time, direction_mode=mode):
                raise ValueError("NIGHT_SHIFT_ALREADY_ADJACENT")

            cursor.execute("""
                INSERT INTO shift_auction_historical_claims
                    (plan_id, source_schedule_shift_id, claimed_by, claimed_at, claimed_start_time, claimed_end_time)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s)
                ON CONFLICT (plan_id, source_schedule_shift_id, claimed_by) DO NOTHING
                RETURNING claimed_at
            """, (plan_id, source_schedule_shift_id, operator_id, selected_start_time, selected_end_time))
            claim_row = cursor.fetchone()
            if not claim_row:
                raise ValueError("LOT_ALREADY_CLAIMED")
            claimed_at = claim_row[0]

            cursor.execute(
                "SELECT 1 FROM days_off WHERE operator_id = %s AND day_off_date = %s",
                (operator_id, shift_start_date)
            )
            day_off_row = cursor.fetchone()

            # Минуты куска приходят сквозными от начала ЛОТА (00:00 ночи — это 1440);
            # для сравнения со сменами своего дня их надо вернуть внутрь суток.
            new_start_min = claim_range["start_minute"]
            new_end_min = claim_range["end_minute"]
            day_start_min = new_start_min - claim_day_shift * 24 * 60
            day_end_min = new_end_min - claim_day_shift * 24 * 60

            cursor.execute("""
                SELECT id, start_time, end_time
                FROM work_shifts
                WHERE operator_id = %s AND shift_date = %s
                ORDER BY start_time
            """, (operator_id, shift_start_date))
            existing_shifts = cursor.fetchall() or []

            merged_start_min, merged_end_min, merge_ids = self._resolve_post_auction_merged_shift_range(
                existing_shifts,
                day_start_min,
                day_end_min
            )

            audit_actor = self._schedule_change_actor(
                cursor, actor_id=operator_id, source='auction_topup'
            )
            audit_before = self._snapshot_schedule_days_tx(cursor, [(operator_id, shift_start_date)])

            if day_off_row:
                cursor.execute(
                    "DELETE FROM days_off WHERE operator_id = %s AND day_off_date = %s",
                    (operator_id, shift_start_date)
                )

            # Снимок до слияния смен: добор к идущей смене не должен переставлять
            # уже отсиженные перерывы (они уедут каскадом вместе со старой строкой).
            day_breaks_snapshot = self._load_day_shift_breaks_tx(cursor, operator_id, shift_start_date)
            if merge_ids:
                cursor.execute("DELETE FROM work_shifts WHERE id = ANY(%s)", (merge_ids,))
                merged_start_obj = self._normalize_schedule_time(
                    _minutes_to_time(merged_start_min % (24 * 60)), 'start'
                )
                merged_end_obj = self._normalize_schedule_time(
                    _minutes_to_time(merged_end_min),
                    'end'
                )
                self._save_shift_tx(
                    cursor=cursor,
                    operator_id=operator_id,
                    shift_date=shift_start_date,
                    start_time=merged_start_obj,
                    end_time=merged_end_obj,
                    breaks=None,
                    shift_type=None,
                    day_breaks_snapshot=day_breaks_snapshot,
                )
            else:
                self._save_shift_tx(
                    cursor=cursor,
                    operator_id=operator_id,
                    shift_date=shift_start_date,
                    start_time=selected_start_time,
                    end_time=selected_end_time,
                    breaks=None,
                    shift_type=None,
                    day_breaks_snapshot=day_breaks_snapshot,
                )

            self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)

            self._recalculate_auto_daily_hours_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=shift_start_date,
                end_date=shift_start_date
            )

            breaks = saved_shift[5] if isinstance(saved_shift[5], list) else []
            lot_payload = {
                "id": f"preview-{int(saved_shift[0])}",
                "shift_date": shift_date.strftime('%Y-%m-%d') if shift_date else None,
                "start_time": start_time.strftime('%H:%M') if start_time else None,
                "end_time": end_time.strftime('%H:%M') if end_time else None,
                "rate_min": float(saved_shift[4] or 0),
                "status": "claimed",
                "claimed_by": operator_id,
                "claimed_by_name": operator_name,
                "claimed_at": claimed_at.isoformat() if claimed_at else None,
                "breaks": breaks,
                "source_schedule_plan_id": plan_id,
                "source_schedule_shift_id": int(saved_shift[0]),
                "post_auction_claimed": True,
                "claim_start_time": selected_start_time.strftime('%H:%M') if selected_start_time else None,
                "claim_end_time": selected_end_time.strftime('%H:%M') if selected_end_time else None,
                "claimed_by_direction_id": operator_row[3],
                "claimed_by_direction": operator_row[4] or "",
                "source_start_minute": int(saved_shift[6]) if saved_shift[6] is not None else None,
                "source_end_minute": int(saved_shift[7]) if saved_shift[7] is not None else None,
                "preview_only": True,
            }
            event = self._insert_shift_auction_test_event(cursor, "lot_post_auction_claimed", {
                "lot": lot_payload,
                "operator_id": operator_id,
                "operator_name": operator_name,
                "merged_with_existing": bool(merge_ids),
                "historical_period_claim": True,
            }, direction_mode=mode)

        return {
            "lot": lot_payload,
            "event": event,
            "operator": {"id": operator_id, "name": operator_name},
            "merged_with_existing": bool(merge_ids),
        }

    def admin_unclaim_shift(self, admin_id, lot_id=None, plan_id=None, source_schedule_shift_id=None, claimed_by=None):
        """
        Снять claim с лота админом — для таблицы смен.
        Принимает либо lot_id (активный план), либо (plan_id, source_schedule_shift_id)
        (исторический добор). claimed_by адресует КОНКРЕТНЫЙ частичный claim, если на
        смене их несколько. Удаляет work_shifts если есть.
        """
        try:
            admin_id = int(admin_id) if admin_id is not None else None
        except Exception:
            admin_id = None
        try:
            lot_id_int = int(lot_id) if lot_id not in (None, '') else None
        except Exception:
            lot_id_int = None
        try:
            plan_id_int = int(plan_id) if plan_id not in (None, '') else None
        except Exception:
            plan_id_int = None
        try:
            shift_id_int = int(source_schedule_shift_id) if source_schedule_shift_id not in (None, '') else None
        except Exception:
            shift_id_int = None
        try:
            target_claimed_by = int(claimed_by) if claimed_by not in (None, '') else None
        except Exception:
            target_claimed_by = None

        with self._get_cursor() as cursor:
            shift_date = None
            start_time = None
            end_time = None
            operator_id = None
            resolved_plan_id = plan_id_int
            resolved_shift_id = shift_id_int
            # Направление берём с самого лота (или с его плана) — админ адресует смену,
            # а не аукцион, и событие обязано уйти в журнал своего направления.
            mode = SHIFT_AUCTION_MODE_LINE

            if lot_id_int:
                cursor.execute("""
                    SELECT id, shift_date, start_time, end_time, claimed_by,
                           source_schedule_plan_id, source_schedule_shift_id, status,
                           post_claim_start_time, post_claim_end_time,
                           COALESCE(direction_mode, 'line')
                    FROM shift_auction_test_lots
                    WHERE id = %s
                    FOR UPDATE
                """, (lot_id_int,))
                lot = cursor.fetchone()
                if not lot:
                    raise ValueError("LOT_NOT_FOUND")
                mode = normalize_shift_auction_mode(lot[10])
                if lot[7] != 'claimed' or lot[4] is None:
                    raise ValueError("LOT_NOT_CLAIMED")
                shift_date = lot[1]
                start_time = lot[8] or lot[2]
                end_time = lot[9] or lot[3]
                operator_id = int(lot[4])
                resolved_plan_id = int(lot[5]) if lot[5] is not None else resolved_plan_id
                resolved_shift_id = int(lot[6]) if lot[6] is not None else resolved_shift_id

                if resolved_shift_id:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(%s, %s)",
                        (self.SHIFT_AUCTION_SAVED_SHIFT_LOCK_NAMESPACE, resolved_shift_id)
                    )
                self._lock_shift_auction_operator_tx(cursor, operator_id)

                cursor.execute("""
                    UPDATE shift_auction_test_lots
                    SET status = 'available',
                        claimed_by = NULL,
                        claimed_at = NULL,
                        post_auction_claimed = FALSE,
                        post_claim_start_time = NULL,
                        post_claim_end_time = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (lot_id_int,))

                # Reconcile any remainder lots that were split off from this lot during
                # a partial добор (see _create_post_auction_remainder_lots).
                cursor.execute("""
                    SELECT COUNT(*) FROM shift_auction_test_lots
                    WHERE remainder_of_lot_id = %s AND status = 'claimed'
                """, (lot_id_int,))
                claimed_remainders = (cursor.fetchone() or [0])[0]
                if claimed_remainders:
                    # Another operator already took part of the leftover — keep their lots
                    # and shrink this restored lot back to the slice it actually occupied,
                    # so the full window is not offered on top of an active claim.
                    if lot[8] and lot[9]:
                        cursor.execute("""
                            UPDATE shift_auction_test_lots
                            SET start_time = %s,
                                end_time = %s,
                                source_schedule_shift_id = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                        """, (lot[8], lot[9], lot_id_int))
                else:
                    # Nobody picked up the leftover — fold the free remainder lots back
                    # into the original lot, which again covers the full window.
                    cursor.execute("""
                        DELETE FROM shift_auction_test_lots
                        WHERE remainder_of_lot_id = %s AND status = 'available'
                    """, (lot_id_int,))
            else:
                if not (resolved_plan_id and resolved_shift_id):
                    raise ValueError("MISSING_TARGET")
                mode = self._shift_auction_mode_for_plan_tx(cursor, resolved_plan_id)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (self.SHIFT_AUCTION_SAVED_SHIFT_LOCK_NAMESPACE, resolved_shift_id)
                )
                cursor.execute("""
                    SELECT shift_date, start_time, end_time
                    FROM resource_saved_schedule_shifts
                    WHERE id = %s AND plan_id = %s
                """, (resolved_shift_id, resolved_plan_id))
                saved = cursor.fetchone()
                if not saved:
                    raise ValueError("SHIFT_NOT_FOUND")
                shift_date, start_time, end_time = saved[0], saved[1], saved[2]
                if target_claimed_by:
                    cursor.execute("""
                        SELECT claimed_by, claimed_start_time, claimed_end_time FROM shift_auction_historical_claims
                        WHERE plan_id = %s AND source_schedule_shift_id = %s AND claimed_by = %s
                        FOR UPDATE
                    """, (resolved_plan_id, resolved_shift_id, target_claimed_by))
                else:
                    cursor.execute("""
                        SELECT claimed_by, claimed_start_time, claimed_end_time FROM shift_auction_historical_claims
                        WHERE plan_id = %s AND source_schedule_shift_id = %s
                        ORDER BY claimed_start_time NULLS FIRST
                        FOR UPDATE
                    """, (resolved_plan_id, resolved_shift_id))
                row = cursor.fetchone()
                if not row:
                    raise ValueError("LOT_NOT_CLAIMED")
                operator_id = int(row[0])
                start_time = row[1] or start_time
                end_time = row[2] or end_time
                self._lock_shift_auction_operator_tx(cursor, operator_id)
                cursor.execute("""
                    UPDATE shift_auction_test_lots
                    SET status = 'available',
                        claimed_by = NULL,
                        claimed_at = NULL,
                        post_auction_claimed = FALSE,
                        post_claim_start_time = NULL,
                        post_claim_end_time = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE source_schedule_plan_id = %s
                      AND source_schedule_shift_id = %s
                      AND status = 'claimed'
                """, (resolved_plan_id, resolved_shift_id))

            if resolved_plan_id and resolved_shift_id:
                # Remove only THIS operator's claim so other operators' disjoint
                # partial доборы of the same shift stay intact.
                if operator_id:
                    cursor.execute("""
                        DELETE FROM shift_auction_historical_claims
                        WHERE plan_id = %s AND source_schedule_shift_id = %s AND claimed_by = %s
                    """, (resolved_plan_id, resolved_shift_id, operator_id))
                else:
                    cursor.execute("""
                        DELETE FROM shift_auction_historical_claims
                        WHERE plan_id = %s AND source_schedule_shift_id = %s
                    """, (resolved_plan_id, resolved_shift_id))

            if operator_id and shift_date and start_time and end_time:
                audit_actor = self._schedule_change_actor(
                    cursor, actor_id=admin_id, source='auction_admin'
                )
                audit_before = self._snapshot_schedule_days_tx(cursor, [(operator_id, shift_date)])
                cursor.execute("""
                    DELETE FROM work_shifts
                    WHERE operator_id = %s
                      AND shift_date = %s
                      AND start_time = %s
                      AND end_time = %s
                """, (operator_id, shift_date, start_time, end_time))
                self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)
                self._recalculate_auto_daily_hours_tx(
                    cursor=cursor,
                    operator_ids=[operator_id],
                    start_date=shift_date,
                    end_date=shift_date
                )

            event_payload = {
                "operator_id": operator_id,
                "lot_id": lot_id_int,
                "plan_id": resolved_plan_id,
                "source_schedule_shift_id": resolved_shift_id,
                "shift_date": shift_date.strftime('%Y-%m-%d') if hasattr(shift_date, 'strftime') else None,
                "start_time": start_time.strftime('%H:%M') if hasattr(start_time, 'strftime') else None,
                "end_time": end_time.strftime('%H:%M') if hasattr(end_time, 'strftime') else None,
                "admin_id": admin_id,
            }
            event = self._insert_shift_auction_test_event(
                cursor, "lot_admin_unclaimed", event_payload, direction_mode=mode
            )

        return {
            "operator_id": operator_id,
            "lot_id": lot_id_int,
            "plan_id": resolved_plan_id,
            "source_schedule_shift_id": resolved_shift_id,
            "shift_date": event_payload["shift_date"],
            "start_time": event_payload["start_time"],
            "end_time": event_payload["end_time"],
            "event": event,
        }

    def get_operator_recent_post_auction_claims(self, operator_id, window_hours=24, limit=30, cancel_window_minutes=10):
        """
        Список НЕДАВНИХ доборов оператора (взятых доп. смен) за последние
        window_hours часов, новые сверху. Используется панелью «Мои доборы»:
        оператор видит, что он взял, и может отменить смену, если с момента
        взятия прошло не больше cancel_window_minutes (по умолчанию 10 минут).

        Объединяет два источника:
          • shift_auction_historical_claims — доборы с исходной сменой (как из
            активного периода, так и из опубликованных прошлых периодов);
          • shift_auction_test_lots — синтетические лоты без исходной смены.

        cancel_seconds_left считается на часах БД (CURRENT_TIMESTAMP) — один
        источник времени, чтобы отсчёт на клиенте не зависел от рассинхронизации
        часов браузера.
        """
        try:
            operator_id = int(operator_id)
        except Exception:
            return []
        window_hours = int(window_hours) if window_hours else 24
        limit = max(1, int(limit) if limit else 30)
        cancel_window_minutes = int(cancel_window_minutes) if cancel_window_minutes else 10

        def _fmt_time(value):
            return value.strftime('%H:%M') if hasattr(value, 'strftime') else None

        entries = []
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT hc.plan_id, hc.source_schedule_shift_id, hc.claimed_at,
                       hc.claimed_start_time, hc.claimed_end_time,
                       s.shift_date, s.start_time, s.end_time, s.rate_min,
                       l.id AS lot_id,
                       GREATEST(0, EXTRACT(EPOCH FROM (
                           hc.claimed_at + (%s * INTERVAL '1 minute') - CURRENT_TIMESTAMP
                       )))::int AS cancel_seconds_left
                FROM shift_auction_historical_claims hc
                JOIN resource_saved_schedule_shifts s
                  ON s.id = hc.source_schedule_shift_id AND s.plan_id = hc.plan_id
                LEFT JOIN shift_auction_test_lots l
                  ON l.source_schedule_plan_id = hc.plan_id
                 AND l.source_schedule_shift_id = hc.source_schedule_shift_id
                WHERE hc.claimed_by = %s
                  AND hc.claimed_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')
                  -- Только пост-аукционные доборы: части смен, взятые В ХОДЕ аукциона
                  -- (чат), возвращаются кнопкой «Вернуть» в самом аукционе, а здесь
                  -- висели бы с уже истёкшим окном отмены.
                  AND COALESCE(hc.claim_stage, 'post_auction') = 'post_auction'
                ORDER BY hc.claimed_at DESC
                LIMIT %s
            """, (cancel_window_minutes, operator_id, window_hours, limit))
            for row in (cursor.fetchall() or []):
                claimed_at = row[2]
                seconds_left = int(row[10] or 0)
                entries.append({
                    "_claimed_at": claimed_at,
                    "lot_id": int(row[9]) if row[9] is not None else None,
                    "plan_id": int(row[0]) if row[0] is not None else None,
                    "source_schedule_shift_id": int(row[1]) if row[1] is not None else None,
                    "shift_date": row[5].strftime('%Y-%m-%d') if row[5] else None,
                    "start_time": _fmt_time(row[3]) or _fmt_time(row[6]),
                    "end_time": _fmt_time(row[4]) or _fmt_time(row[7]),
                    "rate_min": float(row[8]) if row[8] is not None else 0.0,
                    "claimed_at": claimed_at.isoformat() if claimed_at else None,
                    "cancel_seconds_left": seconds_left,
                    "can_cancel": seconds_left > 0,
                })

            cursor.execute("""
                SELECT l.id, l.shift_date, l.start_time, l.end_time, l.rate_min,
                       l.claimed_at, l.post_claim_start_time, l.post_claim_end_time,
                       GREATEST(0, EXTRACT(EPOCH FROM (
                           l.claimed_at + (%s * INTERVAL '1 minute') - CURRENT_TIMESTAMP
                       )))::int AS cancel_seconds_left
                FROM shift_auction_test_lots l
                WHERE l.claimed_by = %s
                  AND COALESCE(l.post_auction_claimed, FALSE) = TRUE
                  AND l.source_schedule_shift_id IS NULL
                  AND l.claimed_at IS NOT NULL
                  AND l.claimed_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 hour')
                ORDER BY l.claimed_at DESC
                LIMIT %s
            """, (cancel_window_minutes, operator_id, window_hours, limit))
            for row in (cursor.fetchall() or []):
                claimed_at = row[5]
                seconds_left = int(row[8] or 0)
                entries.append({
                    "_claimed_at": claimed_at,
                    "lot_id": int(row[0]) if row[0] is not None else None,
                    "plan_id": None,
                    "source_schedule_shift_id": None,
                    "shift_date": row[1].strftime('%Y-%m-%d') if row[1] else None,
                    "start_time": _fmt_time(row[6]) or _fmt_time(row[2]),
                    "end_time": _fmt_time(row[7]) or _fmt_time(row[3]),
                    "rate_min": float(row[4]) if row[4] is not None else 0.0,
                    "claimed_at": claimed_at.isoformat() if claimed_at else None,
                    "cancel_seconds_left": seconds_left,
                    "can_cancel": seconds_left > 0,
                })

        entries.sort(key=lambda item: item["_claimed_at"] or datetime.min, reverse=True)
        entries = entries[:limit]
        for item in entries:
            item.pop("_claimed_at", None)
        return entries

    def operator_cancel_post_auction_claim(self, operator_id, lot_id=None, plan_id=None,
                                           source_schedule_shift_id=None, cancel_window_minutes=10):
        """
        Оператор сам отменяет недавно взятую дополнительную смену (добор), если с
        момента взятия прошло не больше cancel_window_minutes (по умолчанию 10).
        Смена возвращается в доступные и убирается из графика. Это «зеркало»
        admin_unclaim_shift, но строго для СВОЕГО claim'а и с лимитом по времени.
        Окно проверяется по часам БД (CURRENT_TIMESTAMP), а не по часам клиента.

        Смена убирается из графика «разъединением» (_split_post_auction_shift_slice_tx):
        если добор был слит с соседней сменой, отрезок добора вычитается, а оставшиеся
        части пересоздаются с пересчётом перерывов по текущим правилам направления.
        """
        operator_id = int(operator_id)
        try:
            lot_id_int = int(lot_id) if lot_id not in (None, '') else None
        except Exception:
            lot_id_int = None
        try:
            plan_id_int = int(plan_id) if plan_id not in (None, '') else None
        except Exception:
            plan_id_int = None
        try:
            shift_id_int = int(source_schedule_shift_id) if source_schedule_shift_id not in (None, '') else None
        except Exception:
            shift_id_int = None
        cancel_window_minutes = int(cancel_window_minutes) if cancel_window_minutes else 10

        with self._get_cursor() as cursor:
            self._lock_shift_auction_operator_tx(cursor, operator_id)

            shift_date = None
            start_time = None
            end_time = None
            claimed_at = None
            resolved_plan_id = plan_id_int
            resolved_shift_id = shift_id_int
            used_lot_id = None

            if resolved_plan_id and resolved_shift_id:
                # Добор с исходной сменой — источник правды shift_auction_historical_claims.
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (self.SHIFT_AUCTION_SAVED_SHIFT_LOCK_NAMESPACE, resolved_shift_id)
                )
                cursor.execute("""
                    SELECT claimed_at, claimed_start_time, claimed_end_time
                    FROM shift_auction_historical_claims
                    WHERE plan_id = %s AND source_schedule_shift_id = %s AND claimed_by = %s
                      AND COALESCE(claim_stage, 'post_auction') = 'post_auction'
                    FOR UPDATE
                """, (resolved_plan_id, resolved_shift_id, operator_id))
                claim_row = cursor.fetchone()
                if not claim_row:
                    raise ValueError("CLAIM_NOT_FOUND")
                claimed_at = claim_row[0]
                cursor.execute("""
                    SELECT shift_date, start_time, end_time
                    FROM resource_saved_schedule_shifts
                    WHERE id = %s AND plan_id = %s
                """, (resolved_shift_id, resolved_plan_id))
                saved = cursor.fetchone()
                if not saved:
                    raise ValueError("SHIFT_NOT_FOUND")
                shift_date = saved[0]
                start_time = claim_row[1] or saved[1]
                end_time = claim_row[2] or saved[2]
            elif lot_id_int:
                # Синтетический лот активного плана без исходной смены.
                cursor.execute("""
                    SELECT shift_date, start_time, end_time, claimed_by, claimed_at, status,
                           source_schedule_plan_id, source_schedule_shift_id,
                           post_claim_start_time, post_claim_end_time,
                           COALESCE(post_auction_claimed, FALSE)
                    FROM shift_auction_test_lots
                    WHERE id = %s
                    FOR UPDATE
                """, (lot_id_int,))
                lot = cursor.fetchone()
                if not lot:
                    raise ValueError("LOT_NOT_FOUND")
                if lot[5] != 'claimed' or lot[3] is None:
                    raise ValueError("LOT_NOT_CLAIMED")
                if int(lot[3]) != operator_id:
                    raise ValueError("LOT_NOT_OWNED")
                if not bool(lot[10]):
                    # Отменять так можно только пост-аукционные доборы; обычный
                    # claim во время аукциона возвращается через release.
                    raise ValueError("POST_AUCTION_LOT_NOT_RELEASABLE")
                used_lot_id = lot_id_int
                shift_date = lot[0]
                start_time = lot[8] or lot[1]
                end_time = lot[9] or lot[2]
                claimed_at = lot[4]
                resolved_plan_id = int(lot[6]) if lot[6] is not None else None
                resolved_shift_id = int(lot[7]) if lot[7] is not None else None
                if resolved_shift_id:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(%s, %s)",
                        (self.SHIFT_AUCTION_SAVED_SHIFT_LOCK_NAMESPACE, resolved_shift_id)
                    )
            else:
                raise ValueError("MISSING_TARGET")

            if claimed_at is None:
                raise ValueError("CLAIM_NOT_FOUND")

            # Окно отмены — авторитетно по часам БД.
            cursor.execute(
                "SELECT (%s + (%s * INTERVAL '1 minute')) < CURRENT_TIMESTAMP",
                (claimed_at, cancel_window_minutes)
            )
            window_expired = bool((cursor.fetchone() or [True])[0])
            if window_expired:
                raise ValueError("CANCEL_WINDOW_EXPIRED")

            # Возвращаем лот(ы) в доступные.
            if used_lot_id and not (resolved_plan_id and resolved_shift_id):
                cursor.execute("""
                    UPDATE shift_auction_test_lots
                    SET status = 'available', claimed_by = NULL, claimed_at = NULL,
                        post_auction_claimed = FALSE, post_claim_start_time = NULL,
                        post_claim_end_time = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (used_lot_id,))

            if resolved_plan_id and resolved_shift_id:
                cursor.execute("""
                    UPDATE shift_auction_test_lots
                    SET status = 'available', claimed_by = NULL, claimed_at = NULL,
                        post_auction_claimed = FALSE, post_claim_start_time = NULL,
                        post_claim_end_time = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE source_schedule_plan_id = %s
                      AND source_schedule_shift_id = %s
                      AND status = 'claimed'
                      AND claimed_by = %s
                """, (resolved_plan_id, resolved_shift_id, operator_id))
                # Снимаем ТОЛЬКО claim этого оператора — частичные доборы других
                # операторов на той же смене остаются нетронутыми.
                cursor.execute("""
                    DELETE FROM shift_auction_historical_claims
                    WHERE plan_id = %s AND source_schedule_shift_id = %s AND claimed_by = %s
                """, (resolved_plan_id, resolved_shift_id, operator_id))

            if shift_date and start_time and end_time:
                # Корректное разъединение: вычитаем отрезок добора, оставшиеся части
                # пересоздаём с пересчётом перерывов по текущим правилам. Если смена,
                # содержащая отрезок, не найдена — запасное точное удаление.
                audit_actor = self._schedule_change_actor(
                    cursor, actor_id=operator_id, source='auction_topup_cancel'
                )
                audit_before = self._snapshot_schedule_days_tx(cursor, [(operator_id, shift_date)])
                split_done = self._split_post_auction_shift_slice_tx(
                    cursor, operator_id, shift_date, start_time, end_time
                )
                if not split_done:
                    cursor.execute("""
                        DELETE FROM work_shifts
                        WHERE operator_id = %s AND shift_date = %s
                          AND start_time = %s AND end_time = %s
                    """, (operator_id, shift_date, start_time, end_time))
                self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)
                self._recalculate_auto_daily_hours_tx(
                    cursor=cursor,
                    operator_ids=[operator_id],
                    start_date=shift_date,
                    end_date=shift_date
                )

            event_payload = {
                "operator_id": operator_id,
                "lot_id": used_lot_id,
                "plan_id": resolved_plan_id,
                "source_schedule_shift_id": resolved_shift_id,
                "shift_date": shift_date.strftime('%Y-%m-%d') if hasattr(shift_date, 'strftime') else None,
                "start_time": start_time.strftime('%H:%M') if hasattr(start_time, 'strftime') else None,
                "end_time": end_time.strftime('%H:%M') if hasattr(end_time, 'strftime') else None,
                "self_cancelled": True,
            }
            event = self._insert_shift_auction_test_event(
                cursor, "lot_post_auction_cancelled", event_payload,
                direction_mode=self._shift_auction_mode_for_operator_tx(cursor, operator_id) or SHIFT_AUCTION_MODE_LINE
            )

        return {
            "operator_id": operator_id,
            "lot_id": used_lot_id,
            "plan_id": resolved_plan_id,
            "source_schedule_shift_id": resolved_shift_id,
            "shift_date": event_payload["shift_date"],
            "start_time": event_payload["start_time"],
            "end_time": event_payload["end_time"],
            "event": event,
        }

    def admin_claim_shift_for_operator(self, admin_id, operator_id, lot_id=None, plan_id=None, source_schedule_shift_id=None,
                                       claim_start_time=None, claim_end_time=None):
        """
        Назначить лот/часть смены оператору админом. Аналог post_auction_claim_lot/_saved_shift,
        но без проверки is_participant — админ имеет право. Если переданы claim_start_time/
        claim_end_time — назначается ЧАСТЬ смены (свободный кусок), с проверкой пересечения
        с уже взятыми частями. Иначе — вся смена. Проверяет непересечение со сменами оператора.
        """
        try:
            admin_id = int(admin_id) if admin_id is not None else None
        except Exception:
            admin_id = None
        try:
            operator_id_int = int(operator_id)
        except Exception:
            raise ValueError("OPERATOR_NOT_FOUND")
        try:
            lot_id_int = int(lot_id) if lot_id not in (None, '') else None
        except Exception:
            lot_id_int = None
        try:
            plan_id_int = int(plan_id) if plan_id not in (None, '') else None
        except Exception:
            plan_id_int = None
        try:
            shift_id_int = int(source_schedule_shift_id) if source_schedule_shift_id not in (None, '') else None
        except Exception:
            shift_id_int = None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.name, u.role, COALESCE(u.status, '')
                FROM users u WHERE u.id = %s
            """, (operator_id_int,))
            op_row = cursor.fetchone()
            if not op_row or normalize_role_value(op_row[1]) != 'operator' or str(op_row[2] or '').strip().lower() in ('fired', 'dismissal'):
                raise ValueError("OPERATOR_NOT_FOUND")
            operator_name = op_row[0] or f"Operator #{operator_id_int}"
            # Направление аукциона — по человеку, которому назначают смену. Человеку вне
            # всех прогонов по-прежнему достаётся линия (так было у СЗоВ); чей это прогон
            # и вправе ли руководитель назначать в нём, сверяет ручка.
            mode = self._shift_auction_mode_for_operator_tx(cursor, operator_id_int) or SHIFT_AUCTION_MODE_LINE

            if lot_id_int:
                # Смена — только из аукциона направления этого человека: иначе смену
                # Тез КЦ можно было бы назначить оператору СЗоВ (и наоборот), а норма
                # и публикация считали бы её в чужом прогоне.
                cursor.execute("""
                    SELECT id, shift_date, start_time, end_time, status, claimed_by,
                           source_schedule_plan_id, source_schedule_shift_id, rate_min, breaks
                    FROM shift_auction_test_lots
                    WHERE id = %s
                      AND COALESCE(direction_mode, 'line') = %s
                    FOR UPDATE
                """, (lot_id_int, mode))
                lot = cursor.fetchone()
                if not lot:
                    raise ValueError("LOT_NOT_FOUND")
                if lot[4] == 'claimed':
                    raise ValueError("LOT_ALREADY_CLAIMED")
                shift_date, start_time, end_time = lot[1], lot[2], lot[3]
                resolved_plan_id = int(lot[6]) if lot[6] is not None else plan_id_int
                resolved_shift_id = int(lot[7]) if lot[7] is not None else shift_id_int
            else:
                if not (plan_id_int and shift_id_int):
                    raise ValueError("MISSING_TARGET")
                resolved_plan_id = plan_id_int
                resolved_shift_id = shift_id_int
                if self._shift_auction_mode_for_plan_tx(cursor, resolved_plan_id) != mode:
                    raise ValueError("SHIFT_NOT_FOUND")
                cursor.execute("""
                    SELECT shift_date, start_time, end_time
                    FROM resource_saved_schedule_shifts
                    WHERE id = %s AND plan_id = %s
                      AND COALESCE(meta->>'excludeFromAuction', 'false') <> 'true'
                """, (resolved_shift_id, resolved_plan_id))
                saved = cursor.fetchone()
                if not saved:
                    raise ValueError("SHIFT_NOT_FOUND")
                shift_date, start_time, end_time = saved[0], saved[1], saved[2]

            if resolved_shift_id:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)",
                    (self.SHIFT_AUCTION_SAVED_SHIFT_LOCK_NAMESPACE, resolved_shift_id)
                )
            self._lock_shift_auction_operator_tx(cursor, operator_id_int)

            # When claim times are given, the admin assigns only a PART of the shift.
            is_partial_assign = bool(claim_start_time and claim_end_time)
            if is_partial_assign:
                claim_range = self._normalize_post_auction_claim_range(start_time, end_time, claim_start_time, claim_end_time)
                eff_start_time = claim_range["start_time"]
                eff_end_time = claim_range["end_time"]
                new_start_min = claim_range["start_minute"]
                new_end_min = claim_range["end_minute"]
            else:
                eff_start_time, eff_end_time = start_time, end_time
                new_start_min, new_end_min = self._schedule_interval_minutes(start_time, end_time)

            if lot_id_int is None and resolved_plan_id and resolved_shift_id:
                # A full assign is refused if any claim exists; a partial assign only if
                # it overlaps one (disjoint parts are allowed).
                cursor.execute("""
                    SELECT claimed_by, claimed_start_time, claimed_end_time
                    FROM shift_auction_historical_claims
                    WHERE plan_id = %s AND source_schedule_shift_id = %s
                    FOR UPDATE
                """, (resolved_plan_id, resolved_shift_id))
                for ex_by, ex_s, ex_e in (cursor.fetchall() or []):
                    if ex_by is not None and int(ex_by) == operator_id_int:
                        raise ValueError("LOT_ALREADY_CLAIMED")
                    if not is_partial_assign or ex_s is None or ex_e is None:
                        raise ValueError("LOT_ALREADY_CLAIMED")
                    ex_rng = self._normalize_post_auction_claim_range(start_time, end_time, ex_s.strftime('%H:%M'), ex_e.strftime('%H:%M'))
                    if new_start_min < ex_rng["end_minute"] and ex_rng["start_minute"] < new_end_min:
                        raise ValueError("SHIFT_OVERLAPS_EXISTING")

            cursor.execute("""
                SELECT id, start_time, end_time
                FROM work_shifts
                WHERE operator_id = %s AND shift_date = %s
                ORDER BY start_time
            """, (operator_id_int, shift_date))
            existing_shifts = cursor.fetchall() or []
            for _ws_id, ws_start, ws_end in existing_shifts:
                ws_s, ws_e = self._schedule_interval_minutes(ws_start, ws_end)
                if ws_s < new_end_min and new_start_min < ws_e:
                    raise ValueError("SHIFT_OVERLAP")

            if lot_id_int:
                cursor.execute("""
                    UPDATE shift_auction_test_lots
                    SET status = 'claimed', claimed_by = %s, claimed_at = CURRENT_TIMESTAMP,
                        post_auction_claimed = TRUE, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status <> 'claimed'
                    RETURNING claimed_at
                """, (operator_id_int, lot_id_int))
                updated_row = cursor.fetchone()
                if not updated_row:
                    raise ValueError("LOT_ALREADY_CLAIMED")
                claimed_at = updated_row[0]
            else:
                claimed_at = None

            if resolved_plan_id and resolved_shift_id:
                cursor.execute("""
                    INSERT INTO shift_auction_historical_claims
                        (plan_id, source_schedule_shift_id, claimed_by, claimed_at, claimed_start_time, claimed_end_time)
                    VALUES (%s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP), %s, %s)
                    ON CONFLICT (plan_id, source_schedule_shift_id, claimed_by) DO NOTHING
                    RETURNING claimed_at
                """, (resolved_plan_id, resolved_shift_id, operator_id_int, claimed_at,
                      eff_start_time if is_partial_assign else None,
                      eff_end_time if is_partial_assign else None))
                row = cursor.fetchone()
                if not row and lot_id_int is None:
                    raise ValueError("LOT_ALREADY_CLAIMED")
                if row:
                    claimed_at = row[0]

            audit_actor = self._schedule_change_actor(
                cursor, actor_id=admin_id, source='auction_admin'
            )
            audit_before = self._snapshot_schedule_days_tx(cursor, [(operator_id_int, shift_date)])
            self._save_shift_tx(
                cursor=cursor,
                operator_id=operator_id_int,
                shift_date=shift_date,
                start_time=eff_start_time,
                end_time=eff_end_time,
                breaks=None,
                shift_type=None,
            )
            self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)
            self._recalculate_auto_daily_hours_tx(
                cursor=cursor,
                operator_ids=[operator_id_int],
                start_date=shift_date,
                end_date=shift_date
            )

            # Partial assign on a single lot: if every minute is now taken, close it.
            if is_partial_assign and lot_id_int is None and resolved_plan_id and resolved_shift_id:
                cursor.execute("""
                    SELECT claimed_start_time, claimed_end_time FROM shift_auction_historical_claims
                    WHERE plan_id = %s AND source_schedule_shift_id = %s AND claimed_start_time IS NOT NULL
                """, (resolved_plan_id, resolved_shift_id))
                fs, fe = self._schedule_interval_minutes(start_time, end_time)
                cov = []
                for cs, ce in (cursor.fetchall() or []):
                    rng = self._normalize_post_auction_claim_range(start_time, end_time, cs.strftime('%H:%M'), ce.strftime('%H:%M'))
                    cov.append((rng["start_minute"], rng["end_minute"]))
                if not [g for g in self._subtract_ranges(fs, fe, cov) if g[1] - g[0] > 0]:
                    cursor.execute("""
                        UPDATE shift_auction_test_lots
                        SET status = 'claimed', post_auction_claimed = TRUE, updated_at = CURRENT_TIMESTAMP
                        WHERE source_schedule_plan_id = %s AND source_schedule_shift_id = %s AND status IN ('available', 'cancelled')
                    """, (resolved_plan_id, resolved_shift_id))

            event_payload = {
                "operator_id": operator_id_int,
                "operator_name": operator_name,
                "lot_id": lot_id_int,
                "plan_id": resolved_plan_id,
                "source_schedule_shift_id": resolved_shift_id,
                "shift_date": shift_date.strftime('%Y-%m-%d') if hasattr(shift_date, 'strftime') else None,
                "start_time": eff_start_time.strftime('%H:%M') if hasattr(eff_start_time, 'strftime') else None,
                "end_time": eff_end_time.strftime('%H:%M') if hasattr(eff_end_time, 'strftime') else None,
                "admin_id": admin_id,
            }
            event = self._insert_shift_auction_test_event(
                cursor, "lot_admin_claimed", event_payload, direction_mode=mode
            )

        return {
            **event_payload,
            "claimed_at": claimed_at.isoformat() if claimed_at else None,
            "event": event,
        }

    def admin_add_shift_auction_lot(self, admin_id, shift_date, start_time, end_time, rate_min,
                                    direction_mode=SHIFT_AUCTION_MODE_LINE):
        """
        Супервайзер/админ добавляет дополнительную смену в текущий аукцион (кнопка «+»
        в сетке по группе ставки), в том числе после его закрытия. Ставка берётся из
        группы, длина смены фиксирована для этой ставки (или ночная 20:00→08:00).
        Лот создаётся доступным
        (status='available') и помечается added_by — его видно другим цветом в сетке и
        в генерации графиков, и его можно забрать как обычную смену.

        Лот не привязан к saved-shift (source_schedule_* = NULL) — ведёт себя так же,
        как seed-шаблонные лоты: claim идёт по lot_id, публикация берёт все claimed-лоты.
        """
        try:
            admin_id_int = int(admin_id) if admin_id is not None else None
        except Exception:
            admin_id_int = None

        try:
            rate_value = round(float(rate_min), 2)
        except Exception:
            raise ValueError("INVALID_RATE")

        if hasattr(shift_date, 'strftime'):
            date_obj = shift_date
        else:
            try:
                date_obj = datetime.strptime(str(shift_date), '%Y-%m-%d').date()
            except Exception:
                raise ValueError("INVALID_DATE")

        start_obj = self._normalize_schedule_time(start_time, "start_time")
        end_obj = self._normalize_schedule_time(end_time, "end_time")
        start_min, end_min = self._schedule_interval_minutes(start_obj, end_obj)
        duration = end_min - start_min

        is_night = (start_obj.strftime('%H:%M') == '20:00' and end_obj.strftime('%H:%M') == '08:00')
        if is_night:
            expected_duration = self.SHIFT_AUCTION_NIGHT_SHIFT_MINUTES
        else:
            expected_duration = self.SHIFT_AUCTION_RATE_SHIFT_MINUTES.get(rate_value)
        if expected_duration is None:
            raise ValueError("INVALID_RATE")
        if duration != expected_duration:
            raise ValueError("INVALID_DURATION")

        # A closed auction may still contain future shifts, but a manager must never
        # be able to add a lot whose start moment has already passed.
        shift_start_dt = datetime.combine(date_obj, start_obj)
        if shift_start_dt <= datetime.now():
            raise ValueError("SHIFT_ALREADY_STARTED")

        mode = normalize_shift_auction_mode(direction_mode)
        with self._get_cursor() as cursor:
            # Shifts may be added while the auction is being prepared, running, paused
            # or closed. A disabled auction has no active context to add them to.
            cursor.execute("""
                SELECT enabled, starts_at, ends_at, paused_at, finished_at, selected_schedule_plan_id
                FROM shift_auction_test_access WHERE id = %s
            """, (shift_auction_settings_row_id(mode),))
            settings_row = cursor.fetchone()
            if not settings_row:
                raise ValueError("AUCTION_NOT_AVAILABLE")
            if self._get_shift_auction_test_status(*settings_row[:5]) == "disabled":
                raise ValueError("AUCTION_NOT_OPEN")
            plan_id = int(settings_row[5]) if settings_row[5] is not None else None

            # The "+" button only appears for dates already present in the grid, so the
            # new shift must land on one of the auction's existing lot dates.
            cursor.execute(
                "SELECT DISTINCT shift_date FROM shift_auction_test_lots "
                "WHERE COALESCE(direction_mode, 'line') = %s",
                (mode,)
            )
            existing_dates = {row[0] for row in (cursor.fetchall() or []) if row and row[0]}
            if not existing_dates:
                raise ValueError("AUCTION_NO_LOTS")
            if date_obj not in existing_dates:
                raise ValueError("DATE_OUT_OF_RANGE")

            admin_name = ""
            if admin_id_int is not None:
                cursor.execute("SELECT name FROM users WHERE id = %s", (admin_id_int,))
                name_row = cursor.fetchone()
                admin_name = (name_row[0] or "") if name_row else ""

            # Back the lot with a real saved-shift row in the auction's plan so it behaves
            # EXACTLY like a normal auction shift: partial/post-auction (добор) claims,
            # "кто какую часть взял" segment display, admin partial assign and publish +
            # historical claims all key on (plan_id, source_schedule_shift_id). Without a
            # source shift, post_auction_claim_lot treats every claim as taking the WHOLE
            # shift, so partial доборы are impossible. A template-seeded auction has no
            # plan — then we fall back to a plain source-less lot (whole-shift claim only).
            source_shift_id = None
            if plan_id is not None:
                overnight = end_min >= 24 * 60
                label = f"{start_obj.strftime('%H:%M')}-{end_obj.strftime('%H:%M')}"
                client_shift_id = (
                    f"auction-added-{admin_id_int}-{date_obj.isoformat()}"
                    f"-{start_obj.strftime('%H%M')}-{end_obj.strftime('%H%M')}"
                )[:180]
                cursor.execute("""
                    INSERT INTO resource_saved_schedule_shifts (
                        plan_id, client_shift_id, shift_date, start_minute, end_minute,
                        start_time, end_time, duration_minutes, rate_min, label,
                        template_id, source, tone, overnight, breaks, meta
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', 'auction-added', '', %s, '[]'::jsonb, %s)
                    RETURNING id
                """, (
                    plan_id, client_shift_id, date_obj, start_min, end_min,
                    start_obj, end_obj, duration, rate_value, label, overnight,
                    Json({"addedBy": admin_id_int, "addedByName": admin_name})
                ))
                shift_row = cursor.fetchone()
                source_shift_id = int(shift_row[0]) if shift_row else None

            cursor.execute("""
                INSERT INTO shift_auction_test_lots (
                    shift_date, start_time, end_time, rate_min, breaks,
                    source_schedule_plan_id, source_schedule_shift_id,
                    status, added_by, added_at, direction_mode
                )
                VALUES (%s, %s, %s, %s, '[]'::jsonb, %s, %s, 'available', %s, CURRENT_TIMESTAMP, %s)
                RETURNING id
            """, (date_obj, start_obj, end_obj, rate_value,
                  (plan_id if source_shift_id else None), source_shift_id, admin_id_int, mode))
            new_row = cursor.fetchone()
            new_lot_id = int(new_row[0]) if new_row else None

            event_payload = {
                "lot_id": new_lot_id,
                "shift_date": date_obj.strftime('%Y-%m-%d'),
                "start_time": start_obj.strftime('%H:%M'),
                "end_time": end_obj.strftime('%H:%M'),
                "rate_min": rate_value,
                "source_schedule_plan_id": (plan_id if source_shift_id else None),
                "source_schedule_shift_id": source_shift_id,
                "added_by": admin_id_int,
                "added_by_name": admin_name,
            }
            event = self._insert_shift_auction_test_event(
                cursor, "lot_added", event_payload, direction_mode=mode
            )

        return {
            **event_payload,
            "event": event,
            "snapshot": self.get_shift_auction_test_snapshot(
                current_user_id=admin_id_int,
                include_admin_fields=True,
                direction_mode=mode
            ),
        }

    def get_admin_post_claim_notify_recipients(self, direction_mode=None):
        """Список admin'ов с включённым тумблером уведомлений (id, name, telegram_id).

        С направлением — только те, чей это прогон: суперадмину приходит всё, админу —
        прогоны его отдела и отделов, которые он возглавляет (админам отделов без
        своего аукциона — СЗоВ, как было до Тез КЦ). Иначе добор оператора Теза будил
        бы руководство СЗоВ и наоборот.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.telegram_id, LOWER(COALESCE(u.role, '')),
                       ARRAY(
                           SELECT LOWER(BTRIM(COALESCE(dep.code, '')))
                           FROM departments dep
                           WHERE dep.id = u.department_id
                              OR (dep.head_user_id = u.id AND COALESCE(dep.is_active, TRUE) = TRUE)
                       )
                FROM admin_profiles ap
                JOIN users u ON u.id = ap.user_id
                WHERE COALESCE(ap.auction_post_claim_notify_enabled, FALSE) = TRUE
                  AND u.telegram_id IS NOT NULL
                  AND LOWER(COALESCE(u.role, '')) IN ('admin', 'super_admin')
            """)
            rows = cursor.fetchall() or []
        mode = normalize_shift_auction_mode(direction_mode) if direction_mode else None
        recipients = []
        for row in rows:
            if not row or row[0] is None or row[2] is None:
                continue
            if mode is not None and row[3] != 'super_admin':
                own_modes = (
                    shift_auction_modes_for_departments(row[4] or [])
                    or shift_auction_modes_for_departments((SHIFT_AUCTION_DEPARTMENT_CODE,))
                )
                if mode not in own_modes:
                    continue
            recipients.append({"id": int(row[0]), "name": row[1] or '', "telegram_id": int(row[2])})
        return recipients

    def get_admin_post_claim_notify_setting(self, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT COALESCE(auction_post_claim_notify_enabled, FALSE)
                FROM admin_profiles
                WHERE user_id = %s
            """, (int(user_id),))
            row = cursor.fetchone()
            return bool(row[0]) if row else False

    def set_admin_post_claim_notify_setting(self, user_id, enabled):
        enabled_value = bool(enabled)
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO admin_profiles (user_id, auction_post_claim_notify_enabled)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                SET auction_post_claim_notify_enabled = EXCLUDED.auction_post_claim_notify_enabled
            """, (int(user_id), enabled_value))
            return enabled_value

    def set_shift_auction_test_day_off(self, operator_id, day_off_date, selected=True):
        operator_id = int(operator_id)
        if isinstance(day_off_date, str):
            day_value = datetime.strptime(day_off_date, "%Y-%m-%d").date()
        else:
            day_value = day_off_date

        with self._get_cursor() as cursor:
            self._lock_shift_auction_operator_tx(cursor, operator_id)
            mode = self._shift_auction_mode_for_operator_tx(cursor, operator_id)
            if mode is None:
                raise ValueError("NOT_TEST_PARTICIPANT")
            cursor.execute("""
                SELECT enabled, starts_at, ends_at, paused_at, finished_at, selected_schedule_plan_id
                FROM shift_auction_test_access
                WHERE id = %s
            """, (shift_auction_settings_row_id(mode),))
            settings = cursor.fetchone()
            if not settings:
                raise ValueError("AUCTION_NOT_AVAILABLE")
            # Day-offs follow the operator's own window: a group that starts later
            # must still be able to mark days off before its turn comes.
            operator_group = self._get_shift_auction_operator_group_tx(cursor, operator_id, settings[5])
            day_off_starts_at, day_off_ends_at = self._shift_auction_effective_window(
                settings[1],
                settings[2],
                (operator_group or {}).get("starts_at"),
                (operator_group or {}).get("ends_at"),
            )
            if self._get_shift_auction_test_status(
                settings[0], day_off_starts_at, day_off_ends_at, settings[3], settings[4]
            ) not in ("scheduled", "open"):
                raise ValueError("AUCTION_NOT_AVAILABLE")

            if operator_id not in self._get_shift_auction_participant_ids_tx(cursor, direction_mode=mode):
                raise ValueError("NOT_TEST_PARTICIPANT")

            cursor.execute(
                "SELECT 1 FROM shift_auction_test_lots WHERE shift_date = %s "
                "AND COALESCE(direction_mode, 'line') = %s LIMIT 1",
                (day_value, mode)
            )
            if not cursor.fetchone():
                raise ValueError("LOT_NOT_FOUND")

            lot_dates = self._get_shift_auction_lot_dates_tx(cursor, direction_mode=mode)
            blocked_date_map = self._get_shift_auction_operator_blocked_date_map_tx(cursor, operator_id, lot_dates=lot_dates)
            day_key = day_value.strftime('%Y-%m-%d') if day_value else ''
            if selected and day_key and day_key in blocked_date_map:
                raise ValueError("SHIFT_AUCTION_STATUS_PERIOD_BLOCKED")

            if selected:
                # Часть смены тоже занимает день: она живёт своей строкой, а лот при
                # этом мог остаться 'available'.
                if any(
                    str(item.get('shift_date') or '') == day_key
                    for item in self._get_shift_auction_operator_claimed_intervals_tx(
                        cursor, operator_id, direction_mode=mode
                    )
                ):
                    raise ValueError("DAY_HAS_CLAIMED_SHIFT")

                cursor.execute("""
                    SELECT day_off_date
                    FROM shift_auction_test_day_offs
                    WHERE operator_id = %s
                      AND COALESCE(direction_mode, 'line') = %s
                    ORDER BY day_off_date
                """, (operator_id, mode))
                selected_day_offs = [
                    row[0].strftime('%Y-%m-%d')
                    for row in (cursor.fetchall() or [])
                    if row and row[0]
                ]
                day_off_quota = self._shift_auction_day_off_quota(len(lot_dates))
                blocked_quota_count = min(day_off_quota, len(blocked_date_map))
                count = len([item for item in selected_day_offs if item not in blocked_date_map])
                already_selected = day_key in selected_day_offs
                if count + blocked_quota_count >= day_off_quota and not already_selected:
                    raise ValueError("DAY_OFF_LIMIT")
                cursor.execute("""
                    INSERT INTO shift_auction_test_day_offs (operator_id, day_off_date, direction_mode)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (operator_id, day_off_date) DO UPDATE
                        SET direction_mode = EXCLUDED.direction_mode
                """, (operator_id, day_value, mode))
                event_type = "day_off_selected"
            else:
                cursor.execute("""
                    DELETE FROM shift_auction_test_day_offs
                    WHERE operator_id = %s AND day_off_date = %s
                      AND COALESCE(direction_mode, 'line') = %s
                """, (operator_id, day_value, mode))
                event_type = "day_off_removed"

            cursor.execute("""
                SELECT day_off_date
                FROM shift_auction_test_day_offs
                WHERE operator_id = %s
                  AND COALESCE(direction_mode, 'line') = %s
                ORDER BY day_off_date
            """, (operator_id, mode))
            my_day_offs = [row[0].strftime('%Y-%m-%d') for row in (cursor.fetchall() or [])]
            event = self._insert_shift_auction_test_event(cursor, event_type, {
                "operator_id": operator_id,
                "date": day_value.strftime('%Y-%m-%d'),
                "my_day_offs": my_day_offs
            }, direction_mode=mode)
        return {"my_day_offs": my_day_offs, "event": event}

    @staticmethod
    def _hourly_merge_minutes(intervals):
        """Суммарная длина объединённых интервалов (перекрытия не считаем дважды)."""
        cleaned = sorted(
            ((int(a), int(b)) for a, b in intervals if b > a),
            key=lambda pair: pair[0],
        )
        total = 0
        cur_start = cur_end = None
        for start, end in cleaned:
            if cur_end is None or start > cur_end:
                if cur_end is not None:
                    total += cur_end - cur_start
                cur_start, cur_end = start, end
            elif end > cur_end:
                cur_end = end
        if cur_end is not None:
            total += cur_end - cur_start
        return total

    @staticmethod
    def _hourly_iso_to_minutes(value, date_obj):
        """ISO-время сегмента -> минуты от полуночи целевой даты (может выйти за сутки)."""
        text = str(value or '').strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
        if moment.tzinfo is not None:
            moment = moment.replace(tzinfo=None)
        delta = moment - datetime.combine(date_obj, datetime.min.time())
        return int(delta.total_seconds() // 60)

    @staticmethod
    def _hourly_lot_parts_for_date(lot, day_key):
        """Минутный отрезок лота внутри указанных суток. Повторяет auctionLotPartsForDate
        из src/App.jsx: ночная смена приходит с датой начала, поэтому её хвост попадает
        в следующие сутки со сдвигом на 1440 минут."""
        lot_date = str(lot.get('shift_date') or '').strip()
        if not lot_date or not day_key:
            return []

        def _day_number(value):
            try:
                year, month, day = (int(part) for part in str(value).split('-'))
                return date(year, month, day).toordinal()
            except Exception:
                return None

        lot_day = _day_number(lot_date)
        target_day = _day_number(day_key)
        if lot_day is None or target_day is None:
            return []

        def _to_minutes(text):
            try:
                hours, minutes = (int(part) for part in str(text).split(':')[:2])
                return hours * 60 + minutes
            except Exception:
                return None

        source_start = lot.get('source_start_minute')
        source_end = lot.get('source_end_minute')
        has_source = (source_start is not None and source_end is not None
                      and int(source_end) > int(source_start))
        if has_source:
            start, end = int(source_start), int(source_end)
        else:
            start = _to_minutes(lot.get('start_time'))
            end = _to_minutes(lot.get('end_time'))
            if start is None or end is None:
                return []
            if end <= start:
                end += 1440
        if end <= start:
            return []

        offset = (lot_day - target_day) * 1440
        part_start = max(0, offset + start)
        part_end = min(1440, offset + end)
        return [(part_start, part_end)] if part_end > part_start else []

    def claim_schedule_change_report_send(self, period_start, user_id) -> bool:
        """Занять отправку сводки за сутки: True — отправляем, False — уже ушла.

        Атомарно на уровне базы. Заявка берётся ДО отправки, а не отмечается
        после: «отмечу потом» оставляет между проверкой и отправкой то самое
        окно, ради которого всё это и делается.
        """
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return False

        with self._get_cursor() as cursor:
            # На свежем проде таблица может ещё не появиться. Без неё рассылка
            # обязана работать (просто без защиты от дубля), а не молчать.
            cursor.execute("SELECT to_regclass('public.schedule_change_report_sends') IS NOT NULL")
            row = cursor.fetchone()
            if not (row and row[0]):
                logging.warning("schedule_change_report_sends отсутствует — отправка без защиты от дубля")
                return True

            cursor.execute("""
                INSERT INTO schedule_change_report_sends (period_start, user_id)
                VALUES (%s, %s)
                ON CONFLICT (period_start, user_id) DO NOTHING
                RETURNING user_id
            """, (period_start, uid))
            return cursor.fetchone() is not None

    def glb_claim_event(self, event):
        """Занять событие под отправку. None = такой ключ уже есть в базе
        (в том числе с прошлого запуска сервиса — дедупликация переживает рестарт)."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO glb_events (
                    event_key, event_type, event_date, employee_name, employee_ext_id,
                    department_name, schedule_name, plan_at, fact_at, minutes, location, message_text
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_key) DO NOTHING
                RETURNING id
            """, (
                event['event_key'], event['event_type'], event['event_date'],
                event.get('employee_name'), event.get('employee_ext_id'),
                event.get('department_name'), event.get('schedule_name'),
                event.get('plan_at'), event.get('fact_at'), event.get('minutes'),
                event.get('location'), event.get('message_text'),
            ))
            row = cursor.fetchone()
            return int(row[0]) if row else None

    # ---- Готовые смены файлом: аукцион Тез КЦ (задача #124) ----------------------------
    #
    # Отделу Тез КЦ «Расчёт ресурсов» смены не генерирует: нагрузка распределена не так,
    # как в СЗоВ. Руководитель загружает тот же Excel, что и в «Графики работы» (первая
    # строка — даты, в клетках «9*18», «13*19/30», «21*09»), но имена и ставки строк не
    # учитываются: каждая заполненная клетка — это смена, которую выбирают в аукционе.
    # Загрузка ложится обычным планом графика (resource_saved_schedule_plans) с режимом
    # прогона, поэтому дальше работает весь аукцион как есть: список периодов, засев
    # лотов, норма, добор и публикация в графики.

    SHIFT_AUCTION_UPLOAD_MAX_DAYS = 31
    SHIFT_AUCTION_UPLOAD_MAX_SHIFTS = 3000
    SHIFT_AUCTION_UPLOAD_INVALID_PREVIEW_LIMIT = 30

    SHIFT_AUCTION_UPLOAD_MESSAGES = {
        "AUCTION_UPLOAD_NOT_SUPPORTED": "Для этого направления смены не загружаются файлом",
        "AUCTION_UPLOAD_EMPTY": "В файле не нашлось ни одной смены",
        "AUCTION_UPLOAD_PERIOD_PAST": "Все смены файла — сегодня или раньше: аукцион идёт только по будущим дням",
        "AUCTION_UPLOAD_PERIOD_TOO_LONG": "В файле больше 31 дня — загрузите период не длиннее месяца",
        "AUCTION_UPLOAD_TOO_MANY_SHIFTS": "В файле больше 3000 смен — разбейте его на периоды",
        "AUCTION_UPLOAD_PERIOD_PUBLISHED": "Итоги аукциона за этот период уже сохранены в «Графики работы» — смены периода заменить нельзя",
        "AUCTION_UPLOAD_PERIOD_CLAIMED": "По этому периоду операторы уже выбирают смены. Чтобы заменить файл, сначала перезапустите аукцион — выбор при этом сбросится",
        "AUCTION_UPLOAD_CHANGED": "Смены периода поменялись, пока шла сверка — загрузите файл ещё раз",
    }

    def get_shift_auction_staff_department_codes(self, user_id):
        """Коды отделов человека для прав в аукционе: свой отдел и отделы, которые он возглавляет."""
        try:
            user_id = int(user_id)
        except Exception:
            return []
        if user_id <= 0:
            return []
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT LOWER(BTRIM(COALESCE(dep.code, '')))
                FROM users u
                JOIN departments dep ON dep.id = u.department_id
                WHERE u.id = %s
                UNION
                SELECT LOWER(BTRIM(COALESCE(code, '')))
                FROM departments
                WHERE head_user_id = %s
                  AND COALESCE(is_active, TRUE) = TRUE
            """, (user_id, user_id))
            return sorted({row[0] for row in (cursor.fetchall() or []) if row and row[0]})

    def shift_auction_mode_for_lot(self, lot_id):
        """Направление лота аукциона или None, если лота нет."""
        try:
            lot_id = int(lot_id)
        except Exception:
            return None
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(direction_mode, 'line') FROM shift_auction_test_lots WHERE id = %s",
                (lot_id,)
            )
            row = cursor.fetchone()
            return normalize_shift_auction_mode(row[0]) if row else None

    def shift_auction_mode_for_plan(self, plan_id):
        """Направление плана графика или None, если плана нет."""
        try:
            plan_id = int(plan_id)
        except Exception:
            return None
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(direction_mode, 'line') FROM resource_saved_schedule_plans WHERE id = %s",
                (plan_id,)
            )
            row = cursor.fetchone()
            return normalize_shift_auction_mode(row[0]) if row else None

    @staticmethod
    def _shift_auction_upload_clock(value):
        """'HH:MM' → минуты суток, иначе None."""
        try:
            hours, minutes = str(value or '').strip()[:5].split(':')
            hours, minutes = int(hours), int(minutes)
        except Exception:
            return None
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            return None
        return hours * 60 + minutes

    def _shift_auction_upload_direction_tx(self, cursor, mode):
        """Живое направление прогона: id и имя — по имени берутся правила перерывов."""
        scope_sql, scope_params = shift_auction_direction_scope_sql(mode)
        cursor.execute(f"""
            SELECT d.id, d.name
            FROM directions d
            JOIN departments dep ON dep.id = d.department_id
            WHERE {scope_sql}
              AND COALESCE(d.is_active, TRUE) = TRUE
            ORDER BY d.id DESC
            LIMIT 1
        """, scope_params)
        row = cursor.fetchone()
        if row:
            return int(row[0]), (row[1] or '').strip()
        return None, SHIFT_AUCTION_MODE_DIRECTION_NAME.get(mode, '')

    def _shift_auction_upload_state_tx(self, cursor, mode, parsed, lock=False):
        """Сверка файла с тем, что уже загружено на этот период этого направления.

        Одна функция и для показа сверки, и для записи: запись обязана делать ровно то,
        что человек увидел и подтвердил.
        """
        safe = parsed if isinstance(parsed, dict) else {}
        direction_id, direction_name = self._shift_auction_upload_direction_tx(cursor, mode)
        direction_rules = (
            self._get_work_schedule_break_rules_for_direction_tx(cursor, direction_name)
            if direction_name else []
        )

        # Аукцион идёт только по БУДУЩИМ дням. Публикация итогов чистит и переписывает
        # каждый день периода у участников, а запрет у неё — лишь на целиком прошедший
        # период: файл текущей недели («График ТП 9-15», загруженный 12-го) стёр бы
        # уже отработанные смены 9–12-го. Поэтому дни по сегодняшний включительно
        # сверка пропускает и показывает отдельной строкой.
        today = self._almaty_now().date()
        days = {}
        skipped_past_days = set()
        skipped_past_shifts = 0
        for raw_date in (safe.get("dates") or []):
            try:
                day = datetime.strptime(str(raw_date)[:10], '%Y-%m-%d').date()
            except (TypeError, ValueError):
                continue
            if day <= today:
                skipped_past_days.add(day)
                continue
            days.setdefault(day, [])
        shifts = []
        for item in (safe.get("shifts") or []):
            if not isinstance(item, dict):
                continue
            try:
                day = datetime.strptime(str(item.get("date") or '')[:10], '%Y-%m-%d').date()
            except (TypeError, ValueError):
                continue
            if day <= today:
                skipped_past_days.add(day)
                skipped_past_shifts += 1
                continue
            start_min = self._shift_auction_upload_clock(item.get("start"))
            end_min = self._shift_auction_upload_clock(item.get("end"))
            if start_min is None or end_min is None or start_min == end_min:
                continue
            if end_min <= start_min:
                # «21*09» — ночь: конец уже в следующих сутках, как у планов графика.
                end_min += 24 * 60
            duration = end_min - start_min
            breaks = self._compute_auto_shift_breaks_minutes(
                start_min, end_min, direction_name=direction_name, direction_rules=direction_rules
            )
            break_minutes = sum(max(0, int(b.get("end", 0)) - int(b.get("start", 0))) for b in (breaks or []) if isinstance(b, dict))
            shift = {
                "date": day,
                "start_minute": start_min,
                "end_minute": end_min,
                "duration_minutes": duration,
                "net_minutes": max(0, duration - break_minutes),
                "cell": str(item.get("cell") or '')[:12],
            }
            shifts.append(shift)
            days.setdefault(day, []).append(shift)
        for day_shifts in days.values():
            day_shifts.sort(key=lambda s: (s["start_minute"], s["end_minute"]))

        date_from = min(days) if days else None
        date_to = max(days) if days else None

        blocked_code = None
        if mode not in SHIFT_AUCTION_UPLOAD_MODES:
            blocked_code = "AUCTION_UPLOAD_NOT_SUPPORTED"
        elif not shifts and skipped_past_shifts:
            blocked_code = "AUCTION_UPLOAD_PERIOD_PAST"
        elif not shifts:
            blocked_code = "AUCTION_UPLOAD_EMPTY"
        elif (date_to - date_from).days + 1 > self.SHIFT_AUCTION_UPLOAD_MAX_DAYS:
            blocked_code = "AUCTION_UPLOAD_PERIOD_TOO_LONG"
        elif len(shifts) > self.SHIFT_AUCTION_UPLOAD_MAX_SHIFTS:
            blocked_code = "AUCTION_UPLOAD_TOO_MANY_SHIFTS"

        existing = None
        existing_keys = None
        settings_id = shift_auction_settings_row_id(mode)
        # Текущий план прогона — тем же правилом, что у снимка аукциона: выбранный в
        # настройках, а без него — план лотов направления.
        cursor.execute("""
            SELECT COALESCE(
                s.selected_schedule_plan_id,
                (
                    SELECT l.source_schedule_plan_id
                    FROM shift_auction_test_lots l
                    WHERE l.source_schedule_plan_id IS NOT NULL
                      AND COALESCE(l.direction_mode, 'line') = s.direction_mode
                    ORDER BY l.id
                    LIMIT 1
                )
            )
            FROM shift_auction_test_access s
            WHERE s.id = %s
        """ + (" FOR UPDATE OF s" if lock else ""), (settings_id,))
        settings_row = cursor.fetchone()
        active_plan_id = int(settings_row[0]) if settings_row and settings_row[0] is not None else None
        active_in_auction = False
        locked_lot_ids = []
        if date_from and date_to:
            # Тот же поиск плана, что у записи (_save_resource_saved_schedule_tx):
            # точная пара дат и направление.
            cursor.execute("""
                SELECT p.id, p.meta, p.updated_at, u.name
                FROM resource_saved_schedule_plans p
                LEFT JOIN users u ON u.id = p.updated_by
                WHERE p.date_from = %s
                  AND p.date_to = %s
                  AND p.direction_mode = %s
                  AND p.archived_at IS NULL
                ORDER BY p.updated_at DESC, p.id DESC
                LIMIT 1
            """ + (" FOR UPDATE OF p" if lock else ""), (date_from, date_to, mode))
            plan_row = cursor.fetchone()
            if plan_row:
                plan_id = int(plan_row[0])
                plan_meta = plan_row[1] if isinstance(plan_row[1], dict) else {}
                cursor.execute("""
                    SELECT shift_date, start_minute, end_minute
                    FROM resource_saved_schedule_shifts
                    WHERE plan_id = %s
                      AND COALESCE(meta->>'excludeFromAuction', 'false') <> 'true'
                """, (plan_id,))
                existing_keys = [
                    (row[0], int(row[1] or 0), int(row[2] or 0))
                    for row in (cursor.fetchall() or [])
                ]
                active_in_auction = active_plan_id == plan_id
                if lock and active_in_auction:
                    # Записи пересев сотрёт лоты прогона. Блокируем их ДО подсчёта выбора:
                    # иначе оператор, успевший взять смену между подсчётом и пересевом,
                    # потерял бы её молча. Его взятие (CAS по status) дождётся загрузки и
                    # получит отказ, а не исчезнувшую смену.
                    cursor.execute(
                        "SELECT id FROM shift_auction_test_lots WHERE COALESCE(direction_mode, 'line') = %s FOR UPDATE",
                        (mode,)
                    )
                    locked_lot_ids = [int(row[0]) for row in (cursor.fetchall() or [])]
                cursor.execute(
                    "SELECT 1 FROM shift_auction_published_periods WHERE plan_id = %s",
                    (plan_id,)
                )
                is_published = cursor.fetchone() is not None
                cursor.execute(
                    "SELECT COUNT(*)::int FROM shift_auction_historical_claims WHERE plan_id = %s",
                    (plan_id,)
                )
                claims_count = int((cursor.fetchone() or [0])[0] or 0)
                if active_in_auction:
                    # Выбор операторов в текущем прогоне: взятые лоты (в том числе «свой
                    # график») и отмеченные выходные — их сотрёт пересев.
                    cursor.execute("""
                        SELECT
                            (SELECT COUNT(*)::int FROM shift_auction_test_lots
                              WHERE COALESCE(direction_mode, 'line') = %s
                                AND (status = 'claimed' OR claimed_by IS NOT NULL)),
                            (SELECT COUNT(*)::int FROM shift_auction_test_day_offs
                              WHERE COALESCE(direction_mode, 'line') = %s)
                    """, (mode, mode))
                    counts = cursor.fetchone() or (0, 0)
                    claims_count += int(counts[0] or 0) + int(counts[1] or 0)
                if blocked_code is None and is_published:
                    blocked_code = "AUCTION_UPLOAD_PERIOD_PUBLISHED"
                if blocked_code is None and claims_count > 0:
                    blocked_code = "AUCTION_UPLOAD_PERIOD_CLAIMED"
                existing = {
                    "plan_id": plan_id,
                    "shift_count": len(existing_keys),
                    "file_name": str(plan_meta.get("uploadFileName") or ''),
                    "file_sha256": str(plan_meta.get("uploadFileSha256") or ''),
                    "uploaded_at": str(plan_meta.get("uploadedAt") or '') or (
                        plan_row[2].isoformat() if plan_row[2] else ''),
                    "uploaded_by_name": str(plan_meta.get("uploadedByName") or plan_row[3] or ''),
                    "is_upload": str(plan_meta.get("source") or '') == 'upload',
                }

        # Другой период того же прогона, который сейчас в аукционе и пересекается с
        # файлом: он остаётся как есть (новый файл ляжет отдельным периодом), а переход на
        # новый период сбросит выбор операторов — об этом сверка обязана сказать.
        overlaps_active = None
        if active_plan_id and date_from and date_to and (not existing or existing["plan_id"] != active_plan_id):
            cursor.execute(
                "SELECT date_from, date_to FROM resource_saved_schedule_plans WHERE id = %s",
                (active_plan_id,)
            )
            active_row = cursor.fetchone()
            if active_row and active_row[0] and active_row[1] and active_row[0] <= date_to and date_from <= active_row[1]:
                overlaps_active = {
                    "date_from": active_row[0].isoformat(),
                    "date_to": active_row[1].isoformat(),
                }

        new_keys = [(s["date"], s["start_minute"], s["end_minute"]) for s in shifts]
        fingerprint_source = sorted(
            f"{key[0].isoformat()}|{key[1]}|{key[2]}" for key in (existing_keys or [])
        )
        base_fingerprint = hashlib.sha256(
            ("none" if existing_keys is None else "\n".join(fingerprint_source)).encode('utf-8')
        ).hexdigest()[:24]

        new_counter = Counter(new_keys)
        old_counter = Counter(existing_keys or [])
        added = new_counter - old_counter
        removed = old_counter - new_counter
        unchanged = new_counter & old_counter
        is_unchanged = existing_keys is not None and not added and not removed

        def _clock(minute):
            minute = int(minute) % (24 * 60)
            return f"{minute // 60:02d}:{minute % 60:02d}"

        day_rows = []
        all_days = sorted(set(days) | {key[0] for key in (existing_keys or []) if date_from and date_from <= key[0] <= date_to})
        has_baseline = existing_keys is not None
        for day in all_days:
            day_shifts = days.get(day, [])
            # Первая загрузка периода сравнивать не с чем: отметка «добавится» на
            # каждой смене была бы шумом, а не сверкой.
            day_added = sorted(k for k in added.elements() if k[0] == day) if has_baseline else []
            day_removed = sorted(k for k in removed.elements() if k[0] == day) if has_baseline else []
            previous_count = (
                sum(1 for key in existing_keys if key[0] == day) if existing_keys is not None else None
            )
            day_rows.append({
                "date": day.isoformat(),
                "count": len(day_shifts),
                "previous_count": previous_count,
                "shifts": [
                    {"start": _clock(s["start_minute"]), "end": _clock(s["end_minute"])}
                    for s in day_shifts
                ],
                "added": [{"start": _clock(k[1]), "end": _clock(k[2])} for k in day_added],
                "removed": [{"start": _clock(k[1]), "end": _clock(k[2])} for k in day_removed],
            })

        invalid_cells = [item for item in (safe.get("invalid_cells") or []) if isinstance(item, dict)]
        return {
            "direction_mode": mode,
            "direction_id": direction_id,
            "direction_name": direction_name,
            "date_from": date_from,
            "date_to": date_to,
            "shifts": shifts,
            "days": day_rows,
            "shift_count": len(shifts),
            "total_minutes": sum(s["duration_minutes"] for s in shifts),
            "net_minutes": sum(s["net_minutes"] for s in shifts),
            "day_off_cells": int(safe.get("day_off_cells") or 0),
            "skipped_past": {
                "shift_count": skipped_past_shifts,
                "date_from": min(skipped_past_days).isoformat() if skipped_past_days else None,
                "date_to": max(skipped_past_days).isoformat() if skipped_past_days else None,
            } if skipped_past_shifts else None,
            "invalid_cells": invalid_cells[:self.SHIFT_AUCTION_UPLOAD_INVALID_PREVIEW_LIMIT],
            "invalid_total": len(invalid_cells),
            "existing": existing,
            "changes": {
                "added": sum(added.values()),
                "removed": sum(removed.values()),
                "unchanged": sum(unchanged.values()),
            } if existing_keys is not None else None,
            "is_unchanged": is_unchanged,
            "active_in_auction": active_in_auction,
            "active_plan_id": active_plan_id,
            "overlaps_active": overlaps_active,
            "sheet_name": str(safe.get("sheet_name") or '')[:120],
            "sheet_count": int(safe.get("sheet_count") or 1),
            "truncated_after_row": safe.get("truncated_after_row"),
            "locked_lot_ids": locked_lot_ids,
            "blocked_code": blocked_code,
            "base_fingerprint": base_fingerprint,
        }

    def _serialize_shift_auction_upload_state(self, state, file_name='', file_sha256=''):
        existing = state.get("existing")
        blocked_code = state.get("blocked_code")
        return {
            "direction_mode": state["direction_mode"],
            "file_name": str(file_name or '')[:255],
            "file_sha256": str(file_sha256 or ''),
            "date_from": state["date_from"].isoformat() if state.get("date_from") else None,
            "date_to": state["date_to"].isoformat() if state.get("date_to") else None,
            "days": state["days"],
            "shift_count": state["shift_count"],
            "total_minutes": state["total_minutes"],
            "net_minutes": state["net_minutes"],
            "day_off_cells": state["day_off_cells"],
            "skipped_past": state.get("skipped_past"),
            "invalid_cells": state["invalid_cells"],
            "invalid_total": state["invalid_total"],
            "existing": existing,
            "same_file": bool(existing and file_sha256 and existing.get("file_sha256") == file_sha256),
            "changes": state["changes"],
            "is_unchanged": bool(state["is_unchanged"]),
            "active_in_auction": bool(state["active_in_auction"]),
            "overlaps_active": state.get("overlaps_active"),
            "sheet_name": state.get("sheet_name") or '',
            "sheet_count": state.get("sheet_count") or 1,
            "truncated_after_row": state.get("truncated_after_row"),
            "blocked": (
                {"code": blocked_code, "message": self.SHIFT_AUCTION_UPLOAD_MESSAGES.get(blocked_code, blocked_code)}
                if blocked_code else None
            ),
            "base_fingerprint": state["base_fingerprint"],
        }

    def preview_shift_auction_upload(self, direction_mode, parsed, file_name='', file_sha256=''):
        """Сверка загруженного файла смен — ничего не пишет."""
        mode = normalize_shift_auction_mode(direction_mode)
        with self._get_cursor() as cursor:
            state = self._shift_auction_upload_state_tx(cursor, mode, parsed, lock=False)
        return self._serialize_shift_auction_upload_state(state, file_name=file_name, file_sha256=file_sha256)

    def save_shift_auction_upload(self, direction_mode, parsed, updated_by=None, file_name='',
                                  file_sha256='', base_fingerprint=None):
        """Записать подтверждённые смены файла планом периода.

        Пишется только то, что показала сверка: если смены периода поменялись после неё,
        запись отказывает (AUCTION_UPLOAD_CHANGED). Совпавший файл не пишется вовсе.
        Если период уже стоит в аукционе и выбора ещё нет, лоты пересеваются тут же —
        в той же транзакции, что и план.
        """
        mode = normalize_shift_auction_mode(direction_mode)
        with self._get_cursor() as cursor:
            state = self._shift_auction_upload_state_tx(cursor, mode, parsed, lock=True)
            if state["blocked_code"]:
                raise ValueError(state["blocked_code"])
            if base_fingerprint and str(base_fingerprint) != state["base_fingerprint"]:
                raise ValueError("AUCTION_UPLOAD_CHANGED")
            result = self._serialize_shift_auction_upload_state(
                state, file_name=file_name, file_sha256=file_sha256)
            if state["is_unchanged"]:
                result.update({"saved": False, "plan_id": state["existing"]["plan_id"], "lots_reloaded": False})
                return result

            uploader_name = ''
            if updated_by is not None:
                cursor.execute("SELECT name FROM users WHERE id = %s", (updated_by,))
                name_row = cursor.fetchone()
                uploader_name = (name_row[0] or '') if name_row else ''
            days_payload = []
            shifts_by_day = {}
            for shift in state["shifts"]:
                shifts_by_day.setdefault(shift["date"], []).append(shift)
            for day in sorted(shifts_by_day):
                days_payload.append({
                    "date": day.isoformat(),
                    "shifts": [
                        {
                            "id": f"upload-{day.isoformat()}-{index}",
                            "startMinute": shift["start_minute"],
                            "endMinute": shift["end_minute"],
                            "rate": self._shift_auction_lot_rate_bucket(
                                _minutes_to_time(shift["start_minute"] % (24 * 60)),
                                _minutes_to_time(shift["end_minute"] % (24 * 60)),
                            ),
                            "source": "upload",
                            "uploadCell": shift["cell"],
                        }
                        for index, shift in enumerate(shifts_by_day[day])
                    ],
                })
            uploaded_at = self._almaty_now().isoformat(timespec='seconds')
            payload = {
                "date_from": state["date_from"].isoformat(),
                "date_to": state["date_to"].isoformat(),
                "title": f"Загружено из файла «{str(file_name or '').strip()[:200]}»" if file_name else "Загружено из файла",
                "days": days_payload,
                "capacity": {"selectedDirectionIds": [state["direction_id"]] if state["direction_id"] else []},
                "meta": {
                    "source": "upload",
                    "directionName": state["direction_name"],
                    "uploadFileName": str(file_name or '')[:255],
                    "uploadFileSha256": str(file_sha256 or ''),
                    "uploadedAt": uploaded_at,
                    "uploadedBy": updated_by,
                    "uploadedByName": uploader_name,
                },
            }
            saved = self._save_resource_saved_schedule_tx(
                cursor, payload, updated_by=updated_by, direction_mode=mode)
            plan_id = int(saved["plan_id"])

            lots_reloaded = False
            if state["active_plan_id"] == plan_id:
                # Период уже стоит в аукционе, а выбора ещё нет (иначе сверка бы
                # отказала): старые лоты меняем на смены файла сразу, без «Перезапустить».
                # Отмеченных выходных нет по той же проверке, даты периода прежние.
                cursor.execute("""
                    SELECT id, shift_date, start_time, end_time, rate_min, breaks
                    FROM resource_saved_schedule_shifts
                    WHERE plan_id = %s
                      AND COALESCE(meta->>'excludeFromAuction', 'false') <> 'true'
                    ORDER BY shift_date, start_minute, end_minute, id
                """, (plan_id,))
                saved_shift_rows = cursor.fetchall() or []
                # Только лоты, заблокированные сверкой: смена «своего графика», поставленная
                # оператором после неё, — это его выбор, и пересев её не сотрёт.
                cursor.execute(
                    "DELETE FROM shift_auction_test_lots WHERE id = ANY(%s)",
                    (state["locked_lot_ids"] or [],)
                )
                if saved_shift_rows:
                    execute_values(
                        cursor,
                        """
                        INSERT INTO shift_auction_test_lots (
                            shift_date, start_time, end_time, rate_min,
                            breaks, source_schedule_plan_id, source_schedule_shift_id,
                            direction_mode
                        )
                        VALUES %s
                        """,
                        [
                            (
                                row[1], row[2], row[3], row[4],
                                Json(row[5] if isinstance(row[5], list) else []),
                                plan_id, row[0], mode,
                            )
                            for row in saved_shift_rows
                        ],
                    )
                self._insert_shift_auction_test_event(cursor, "lots_seeded", {
                    "count": len(saved_shift_rows),
                    "plan_id": plan_id,
                    "source": "upload",
                    "updated_by": updated_by,
                }, direction_mode=mode)
                lots_reloaded = True

            period_row = self._get_shift_auction_period_row_tx(cursor, plan_id, direction_mode=mode)

        result.update({
            "saved": True,
            "plan_id": plan_id,
            "period": self._serialize_shift_auction_period_row(period_row),
            "lots_reloaded": lots_reloaded,
        })
        return result
