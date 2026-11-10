"""База данных: смены и заявки на смены.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _merge_shifts_for_date,
    _minutes_to_time,
    _normalize_work_shift_type_value,
    _time_to_minutes,
    _work_shift_type_priority,
    CALCULATION_MODEL_CHAT_MANAGER,
    CHAT_MANAGER_TRAINING_STATUS_KEYS,
    date,
    datetime,
    dt_time,
    json,
    normalize_role_value,
    SCHEDULE_AUTO_TRAINING_STATUS_KEY,
    SHIFT_AUCTION_MODE_LINE,
    SHIFT_BREAK_PLANNING_BUFFER_MINUTES,
    SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES,
    supervisor_hours,
    timedelta,
    WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT,
    WORK_SHIFT_TYPE_OFFICE_PRACTICE,
    WORK_SHIFT_TYPE_PHONE_SHIFT,
)


class ShiftsMixin:
    # Текущая группа человека для строки «Графиков работы». Подзапрос, а не JOIN:
    # членств у человека бывает несколько подряд, и JOIN размножил бы строку.
    # Пустое начало членства читается как «с давних пор», пустой конец — «по сей день».
    _SCHEDULE_ROW_GROUP_NAME_SQL = """(
                               SELECT g.name
                               FROM group_operator_memberships gm
                               JOIN groups g ON g.id = gm.group_id
                               WHERE gm.operator_id = u.id
                                 AND COALESCE(gm.start_date, DATE '1900-01-01') <= CURRENT_DATE
                                 AND (gm.end_date IS NULL OR gm.end_date >= CURRENT_DATE)
                               ORDER BY gm.start_date DESC NULLS LAST, gm.id DESC
                               LIMIT 1
                           )"""

    def _normalize_resource_saved_schedule_shifts(self, days, direction_name=None, direction_rules=None):
        rows = []
        for day_index, day in enumerate(days or []):
            if not isinstance(day, dict):
                continue
            try:
                shift_date = self._parse_resource_schedule_date_value(day.get("date"), "shift_date")
            except ValueError:
                continue
            for shift_index, shift in enumerate(day.get("shifts") or []):
                if not isinstance(shift, dict):
                    continue
                try:
                    start_minute = int(round(float(shift.get("startMinute", 0) or 0)))
                    end_minute = int(round(float(shift.get("endMinute", start_minute + 60) or (start_minute + 60))))
                except Exception:
                    continue
                start_minute = max(0, min(start_minute, (24 * 60) - 1))
                end_minute = max(start_minute + 1, min(end_minute, 32 * 60))
                duration_minutes = max(1, end_minute - start_minute)
                incoming_breaks = shift.get("breaks") if isinstance(shift.get("breaks"), list) else []
                computed_breaks = self._compute_auto_shift_breaks_minutes(
                    start_minute,
                    end_minute,
                    direction_name=direction_name,
                    direction_rules=direction_rules
                )
                meta = {
                    key: value for key, value in shift.items()
                    if key not in {
                        "id", "templateId", "rate", "label", "start", "end",
                        "startMinute", "endMinute", "durationMinutes", "overnight",
                        "source", "tone", "breaks"
                    }
                }
                if incoming_breaks:
                    meta["clientBreaks"] = incoming_breaks
                # Вид смены. Телефонная (постановка #304) в расчёт ресурсов не входит
                # и красится зелёным, поэтому признак обязан пережить сохранение:
                # держим его в meta ЯВНО и с проверенным значением, а не «как пришло».
                shift_kind = str(shift.get("shiftKind") or shift.get("shift_kind") or "").strip().lower()
                meta.pop("shift_kind", None)
                if shift_kind == self.RESOURCE_SHIFT_KIND_PHONE:
                    meta["shiftKind"] = self.RESOURCE_SHIFT_KIND_PHONE
                else:
                    meta.pop("shiftKind", None)
                meta["breakSource"] = "work_schedule_break_rules"
                if direction_name:
                    meta["breakDirectionName"] = direction_name
                rows.append({
                    "client_shift_id": str(shift.get("id") or f"saved-{day_index}-{shift_index}")[:180],
                    "shift_date": shift_date,
                    "start_minute": start_minute,
                    "end_minute": end_minute,
                    "start_time": self._format_resource_schedule_clock(start_minute),
                    "end_time": self._format_resource_schedule_clock(end_minute),
                    "duration_minutes": duration_minutes,
                    "rate_min": float(shift.get("rate") or shift.get("rate_min") or 0),
                    "label": str(shift.get("label") or "")[:255],
                    "template_id": str(shift.get("templateId") or shift.get("template_id") or "")[:180],
                    "source": str(shift.get("source") or "")[:80],
                    "tone": str(shift.get("tone") or "")[:40],
                    "overnight": bool(shift.get("overnight") or end_minute >= 24 * 60),
                    "breaks": computed_breaks,
                    "meta": meta,
                })
        rows.sort(key=lambda item: (item["shift_date"], item["start_minute"], item["end_minute"], item["client_shift_id"]))
        return rows

    def _serialize_resource_saved_schedule_shift_row(self, row):
        shift_meta = row[14] if isinstance(row[14], dict) else {}
        payload = {
            **shift_meta,
            "id": row[1] or f"saved-{row[0]}",
            "savedShiftId": row[0],
            "templateId": row[10] or "",
            "rate": float(row[8]) if row[8] is not None else 0,
            "label": row[9] or "",
            "start": row[5].strftime('%H:%M') if row[5] else None,
            "end": row[6].strftime('%H:%M') if row[6] else None,
            "startMinute": int(row[3] or 0),
            "endMinute": int(row[4] or 0),
            "durationMinutes": int(row[7] or 0),
            "overnight": bool(row[13]),
            "source": row[11] or "",
            "tone": row[12] or "",
            "breaks": row[15] if isinstance(row[15], list) else [],
        }
        return payload

    def _build_preview_shift_lots(self, base_lot, claims, operator_info):
        """
        Expand one previewed saved-shift lot into per-operator claimed slices plus
        available remainder lots for the still-free gaps, so other operators can
        добор the leftover part of a partially-claimed shift in a published period.
        """
        full_start = base_lot.get("source_start_minute")
        full_end = base_lot.get("source_end_minute")
        if not (isinstance(full_start, int) and isinstance(full_end, int) and full_end > full_start):
            # No reliable minute range — fall back to the legacy single claimed lot.
            first = claims[0] if claims else None
            if first:
                info = operator_info.get(int(first["claimed_by"])) if first.get("claimed_by") else None
                cs = first.get("claim_start")
                ce = first.get("claim_end")
                base_lot.update({
                    "status": "claimed",
                    "claimed_by": first.get("claimed_by"),
                    "claimed_at": first["claimed_at"].isoformat() if first.get("claimed_at") else base_lot.get("claimed_at"),
                    "post_auction_claimed": True,
                    "claim_start_time": cs.strftime('%H:%M') if cs else base_lot.get("claim_start_time"),
                    "claim_end_time": ce.strftime('%H:%M') if ce else base_lot.get("claim_end_time"),
                    "claimed_by_name": (info or {}).get("name") or "",
                    "claimed_by_direction_id": (info or {}).get("direction_id"),
                    "claimed_by_direction": (info or {}).get("direction") or "",
                })
            return [base_lot]

        result = []
        claimed_ranges = []
        full_block = False
        for idx, claim in enumerate(claims):
            cs = claim.get("claim_start")
            ce = claim.get("claim_end")
            claimed_by = claim.get("claimed_by")
            info = operator_info.get(int(claimed_by)) if claimed_by else None
            slice_lot = dict(base_lot)
            slice_lot.update({
                "id": f"{base_lot['id']}-c{claimed_by if claimed_by is not None else idx}",
                "status": "claimed",
                "claimed_by": claimed_by,
                "claimed_at": claim["claimed_at"].isoformat() if claim.get("claimed_at") else base_lot.get("claimed_at"),
                "post_auction_claimed": True,
                "claim_start_time": cs.strftime('%H:%M') if cs else None,
                "claim_end_time": ce.strftime('%H:%M') if ce else None,
                "claimed_by_name": (info or {}).get("name") or "",
                "claimed_by_direction_id": (info or {}).get("direction_id"),
                "claimed_by_direction": (info or {}).get("direction") or "",
            })
            result.append(slice_lot)
            if cs is None or ce is None:
                full_block = True
                continue
            cs_min = _time_to_minutes(cs.strftime('%H:%M'))
            ce_min = _time_to_minutes(ce.strftime('%H:%M'))
            if full_end > 24 * 60 and cs_min < full_start:
                cs_min += 24 * 60
            if ce_min <= cs_min:
                ce_min += 24 * 60
            claimed_ranges.append((cs_min, ce_min))

        if not full_block:
            for gi, (gap_start, gap_end) in enumerate(self._subtract_ranges(full_start, full_end, claimed_ranges)):
                if gap_end - gap_start < self.POST_AUCTION_MIN_REMAINDER_MINUTES or gap_end > 24 * 60:
                    continue
                gap_lot = dict(base_lot)
                gap_lot.update({
                    "id": f"{base_lot['id']}-r{gi}",
                    "status": "available",
                    "claimed_by": None,
                    "claimed_by_name": "",
                    "claimed_at": None,
                    "post_auction_claimed": False,
                    "claim_start_time": None,
                    "claim_end_time": None,
                    "claimed_by_direction_id": None,
                    "claimed_by_direction": "",
                    "start_time": _minutes_to_time(gap_start % (24 * 60)),
                    "end_time": _minutes_to_time(gap_end % (24 * 60)),
                    "source_start_minute": gap_start,
                    "source_end_minute": gap_end,
                })
                result.append(gap_lot)
        return result

    def _remove_self_scheduled_shift_tx(self, cursor, operator_id, lot,
                                        direction_mode=SHIFT_AUCTION_MODE_LINE):
        """Drop a shift the operator had put on the calendar themselves.

        The lot has no counterpart in the weekly plan, so nothing is left behind
        for other operators — the row and its backing shift both go away.
        """
        lot_id = int(lot[0])
        source_shift_id = lot[10]
        cursor.execute("DELETE FROM shift_auction_test_lots WHERE id = %s", (lot_id,))
        if source_shift_id is not None:
            cursor.execute("""
                DELETE FROM resource_saved_schedule_shifts
                WHERE id = %s AND source = 'auction-self'
            """, (source_shift_id,))
        lot_payload = {
            "id": lot_id,
            "shift_date": lot[1].strftime('%Y-%m-%d') if lot[1] else None,
            "start_time": lot[2].strftime('%H:%M') if lot[2] else None,
            "end_time": lot[3].strftime('%H:%M') if lot[3] else None,
            "rate_min": float(lot[4] or 0),
            "status": "removed",
            "source_schedule_plan_id": lot[9],
            "source_schedule_shift_id": source_shift_id,
            "self_scheduled": True,
        }
        # Deliberately not a lot-patch event: the row is gone, so every client has
        # to reconcile with a fresh snapshot instead of patching it in place.
        event = self._insert_shift_auction_test_event(cursor, "self_scheduled_shift_removed", {
            "lot": lot_payload,
            "operator_id": operator_id,
        }, direction_mode=direction_mode)
        return {"lot": lot_payload, "event": event, "removed": True}

    def _phone_shift_training_status_keys(self):
        return sorted({
            self._normalize_import_status_key(item)
            for item in (
                set(CHAT_MANAGER_TRAINING_STATUS_KEYS)
                | {SCHEDULE_AUTO_TRAINING_STATUS_KEY, 'training', 'study'}
            )
            if self._normalize_import_status_key(item)
        })

    def _load_phone_shift_training_offline_intervals_by_operator_day_tx(
        self,
        cursor,
        operator_ids,
        start_date,
        end_date,
        group_id=None
    ):
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        result = {op_id: {} for op_id in op_ids}
        totals = {op_id: 0.0 for op_id in op_ids}
        if not op_ids:
            return result, totals

        start_date_obj = self._normalize_schedule_date(start_date)
        end_date_obj = self._normalize_schedule_date(end_date)
        if end_date_obj < start_date_obj:
            start_date_obj, end_date_obj = end_date_obj, start_date_obj

        shift_source_start = start_date_obj - timedelta(days=1)
        shift_source_end = end_date_obj
        cursor.execute(
            """
            SELECT id, operator_id, shift_date, start_time, end_time
            FROM work_shifts
            WHERE operator_id = ANY(%s)
              AND COALESCE(shift_type, 'regular') = %s
              AND shift_date >= %s
              AND shift_date <= %s
            ORDER BY operator_id, shift_date, start_time, end_time, id
            """,
            (op_ids, WORK_SHIFT_TYPE_PHONE_SHIFT, shift_source_start, shift_source_end)
        )

        phone_shift_intervals_by_day = {}
        for shift_id, op_id, shift_date_value, start_time_value, end_time_value in cursor.fetchall() or []:
            op_id_int = int(op_id)
            if op_id_int not in result or shift_date_value is None:
                continue
            start_min, end_min = self._schedule_interval_minutes(start_time_value, end_time_value)
            day_chunks = self._split_absolute_minutes_intervals_by_day(
                [{'start': int(start_min), 'end': int(end_min)}],
                shift_date_value
            )
            for chunk in day_chunks:
                chunk_date = chunk.get('date')
                if not chunk_date:
                    continue
                if chunk_date < start_date_obj or chunk_date > end_date_obj:
                    continue
                start_sec = int(chunk.get('start_min') or 0) * 60
                end_sec = int(chunk.get('end_min') or 0) * 60
                if end_sec <= start_sec:
                    continue
                phone_shift_intervals_by_day.setdefault((op_id_int, chunk_date), []).append({
                    'start': start_sec,
                    'end': end_sec,
                    'shift_id': int(shift_id)
                })

        if not phone_shift_intervals_by_day:
            return result, totals

        group_filter_sql = ""
        params = [op_ids, self._phone_shift_training_status_keys(), start_date_obj, end_date_obj]
        if group_id is not None:
            group_filter_sql = """
              AND EXISTS (
                  SELECT 1
                  FROM group_operator_memberships gom
                  WHERE gom.operator_id = oss.operator_id
                    AND gom.group_id = %s
                    AND gom.start_date <= oss.status_date
                    AND (gom.end_date IS NULL OR gom.end_date >= oss.status_date)
              )
            """
            params.append(int(group_id))

        cursor.execute(
            f"""
            SELECT
                oss.id,
                oss.operator_id,
                oss.status_date,
                oss.start_at,
                oss.end_at,
                oss.status_key
            FROM operator_status_segments oss
            WHERE oss.operator_id = ANY(%s)
              AND LOWER(BTRIM(oss.status_key)) = ANY(%s)
              AND oss.status_date >= %s
              AND oss.status_date <= %s
              {group_filter_sql}
            ORDER BY oss.operator_id, oss.status_date, oss.start_at, oss.end_at, oss.id
            """,
            params
        )

        raw_intersections = {op_id: {} for op_id in op_ids}
        for status_id, op_id, status_date_value, start_at_value, end_at_value, status_key in cursor.fetchall() or []:
            op_id_int = int(op_id)
            if op_id_int not in result:
                continue
            for chunk in self._schedule_auto_split_status_segment_by_day(status_date_value, start_at_value, end_at_value):
                chunk_day = chunk.get('day')
                if not isinstance(chunk_day, date):
                    continue
                if chunk_day < start_date_obj or chunk_day > end_date_obj:
                    continue
                phone_intervals = phone_shift_intervals_by_day.get((op_id_int, chunk_day)) or []
                if not phone_intervals:
                    continue
                status_interval = {
                    'start': int(chunk.get('start', 0)),
                    'end': int(chunk.get('end', 0))
                }
                intersections = self._schedule_auto_intersect_interval_with_list(
                    status_interval,
                    phone_intervals
                )
                if not intersections:
                    continue
                day_key = chunk_day.strftime('%Y-%m-%d')
                for interval in intersections:
                    start_sec = int(interval.get('start', 0))
                    end_sec = int(interval.get('end', 0))
                    if end_sec <= start_sec:
                        continue
                    raw_intersections.setdefault(op_id_int, {}).setdefault(day_key, []).append({
                        'start': start_sec,
                        'end': end_sec,
                        'status_id': int(status_id),
                        'status_key': self._normalize_import_status_key(status_key)
                    })

        for op_id_int, days_map in raw_intersections.items():
            for day_key, intervals in (days_map or {}).items():
                merged = self._merge_break_intervals(intervals or [])
                for idx, interval in enumerate(merged):
                    start_sec = int(interval.get('start', 0))
                    end_sec = int(interval.get('end', 0))
                    duration_seconds = max(0, end_sec - start_sec)
                    if duration_seconds <= 0:
                        continue
                    result.setdefault(op_id_int, {}).setdefault(day_key, []).append({
                        'id': f"phone-shift-training-{op_id_int}-{day_key}-{idx}-{start_sec}-{end_sec}",
                        'date': day_key,
                        'start_seconds': start_sec,
                        'end_seconds': end_sec,
                        'duration_seconds': duration_seconds,
                        'start_time': self._time_label_from_seconds(start_sec),
                        'end_time': self._time_label_from_seconds(end_sec, round_end=True),
                        'comment': WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT,
                        'source': 'work_shift_training_status',
                        'shift_type': WORK_SHIFT_TYPE_PHONE_SHIFT,
                        'read_only': True,
                        'is_phone_shift_training': True
                    })
                    totals[op_id_int] = round(
                        float(totals.get(op_id_int, 0.0)) + (float(duration_seconds) / 3600.0),
                        4
                    )

        return result, totals

    def _load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
        self,
        cursor,
        operator_ids,
        start_date,
        end_date,
        group_id=None,
        counted_only=False
    ):
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        result = {op_id: {} for op_id in op_ids}
        totals = {op_id: 0.0 for op_id in op_ids}
        if not op_ids:
            return result, totals

        start_date_obj = self._normalize_schedule_date(start_date)
        end_date_obj = self._normalize_schedule_date(end_date)
        if end_date_obj < start_date_obj:
            start_date_obj, end_date_obj = end_date_obj, start_date_obj

        shift_source_start = start_date_obj - timedelta(days=1)
        shift_source_end = end_date_obj
        cursor.execute(
            """
            SELECT id, operator_id, shift_date, start_time, end_time
            FROM work_shifts
            WHERE operator_id = ANY(%s)
              AND COALESCE(shift_type, 'regular') = %s
              AND shift_date >= %s
              AND shift_date <= %s
            ORDER BY operator_id, shift_date, start_time, end_time, id
            """,
            (op_ids, WORK_SHIFT_TYPE_PHONE_SHIFT, shift_source_start, shift_source_end)
        )

        phone_shift_intervals_by_day = {}
        for shift_id, op_id, shift_date_value, start_time_value, end_time_value in cursor.fetchall() or []:
            op_id_int = int(op_id)
            if op_id_int not in result or shift_date_value is None:
                continue
            start_min, end_min = self._schedule_interval_minutes(start_time_value, end_time_value)
            day_chunks = self._split_absolute_minutes_intervals_by_day(
                [{'start': int(start_min), 'end': int(end_min)}],
                shift_date_value
            )
            for chunk in day_chunks:
                chunk_date = chunk.get('date')
                if not chunk_date:
                    continue
                if chunk_date < start_date_obj or chunk_date > end_date_obj:
                    continue
                start_sec = int(chunk.get('start_min') or 0) * 60
                end_sec = int(chunk.get('end_min') or 0) * 60
                if end_sec <= start_sec:
                    continue
                phone_shift_intervals_by_day.setdefault((op_id_int, chunk_date), []).append({
                    'start': start_sec,
                    'end': end_sec,
                    'shift_id': int(shift_id)
                })

        if not phone_shift_intervals_by_day:
            return result, totals

        group_filter_sql = ""
        counted_filter_sql = "AND t.count_in_hours = TRUE" if counted_only else ""
        params = [op_ids, start_date_obj - timedelta(days=1), end_date_obj]
        if group_id is not None:
            group_filter_sql = """
              AND EXISTS (
                  SELECT 1
                  FROM group_operator_memberships gom
                  WHERE gom.operator_id = t.operator_id
                    AND gom.group_id = %s
                    AND gom.start_date <= t.training_date
                    AND (gom.end_date IS NULL OR gom.end_date >= t.training_date)
              )
            """
            params.append(int(group_id))

        cursor.execute(
            f"""
            SELECT
                t.id,
                t.operator_id,
                t.training_date,
                t.start_time,
                t.end_time,
                t.count_in_hours
            FROM trainings t
            WHERE t.operator_id = ANY(%s)
              AND t.training_date >= %s
              AND t.training_date <= %s
              {counted_filter_sql}
              {group_filter_sql}
            ORDER BY t.operator_id, t.training_date, t.start_time, t.end_time, t.id
            """,
            params
        )

        raw_intersections = {op_id: {} for op_id in op_ids}
        for training_id, op_id, training_date_value, start_time_value, end_time_value, count_in_hours in cursor.fetchall() or []:
            op_id_int = int(op_id)
            if op_id_int not in result or training_date_value is None:
                continue
            start_min, end_min = self._schedule_interval_minutes(start_time_value, end_time_value)
            day_chunks = self._split_absolute_minutes_intervals_by_day(
                [{'start': int(start_min), 'end': int(end_min)}],
                training_date_value
            )
            for chunk in day_chunks:
                chunk_day = chunk.get('date')
                if not isinstance(chunk_day, date):
                    continue
                if chunk_day < start_date_obj or chunk_day > end_date_obj:
                    continue
                phone_intervals = phone_shift_intervals_by_day.get((op_id_int, chunk_day)) or []
                if not phone_intervals:
                    continue
                training_interval = {
                    'start': int(chunk.get('start_min') or 0) * 60,
                    'end': int(chunk.get('end_min') or 0) * 60
                }
                intersections = self._schedule_auto_intersect_interval_with_list(
                    training_interval,
                    phone_intervals
                )
                if not intersections:
                    continue
                day_key = chunk_day.strftime('%Y-%m-%d')
                for interval in intersections:
                    start_sec = int(interval.get('start', 0))
                    end_sec = int(interval.get('end', 0))
                    if end_sec <= start_sec:
                        continue
                    raw_intersections.setdefault(op_id_int, {}).setdefault(day_key, []).append({
                        'start': start_sec,
                        'end': end_sec,
                        'training_id': int(training_id),
                        'count_in_hours': bool(count_in_hours)
                    })

        for op_id_int, days_map in raw_intersections.items():
            for day_key, intervals in (days_map or {}).items():
                merged = self._merge_break_intervals(intervals or [])
                for idx, interval in enumerate(merged):
                    start_sec = int(interval.get('start', 0))
                    end_sec = int(interval.get('end', 0))
                    duration_seconds = max(0, end_sec - start_sec)
                    if duration_seconds <= 0:
                        continue
                    result.setdefault(op_id_int, {}).setdefault(day_key, []).append({
                        'id': f"phone-shift-manual-training-{op_id_int}-{day_key}-{idx}-{start_sec}-{end_sec}",
                        'date': day_key,
                        'start_seconds': start_sec,
                        'end_seconds': end_sec,
                        'duration_seconds': duration_seconds,
                        'start_time': self._time_label_from_seconds(start_sec),
                        'end_time': self._time_label_from_seconds(end_sec, round_end=True),
                        'comment': WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT,
                        'source': 'training_phone_shift',
                        'shift_type': WORK_SHIFT_TYPE_PHONE_SHIFT,
                        'read_only': True,
                        'is_phone_shift_training': True
                    })
                    totals[op_id_int] = round(
                        float(totals.get(op_id_int, 0.0)) + (float(duration_seconds) / 3600.0),
                        4
                    )

        return result, totals

    def _sum_phone_shift_offline_interval_maps(self, operator_ids, *interval_maps):
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        intervals_by_operator_day = {op_id: {} for op_id in op_ids}
        for interval_map in interval_maps:
            for op_id_raw, days_map in (interval_map or {}).items():
                try:
                    op_id_int = int(op_id_raw)
                except Exception:
                    continue
                if op_id_int not in intervals_by_operator_day:
                    continue
                for day_key, items in (days_map or {}).items():
                    for item in (items or []):
                        start_sec = int(item.get('start_seconds') or 0)
                        end_sec = int(item.get('end_seconds') or 0)
                        if end_sec <= start_sec:
                            continue
                        day_key_text = str(item.get('date') or day_key)
                        intervals_by_operator_day[op_id_int].setdefault(day_key_text, []).append({
                            'start': start_sec,
                            'end': end_sec
                        })

        totals = {op_id: 0.0 for op_id in op_ids}
        for op_id, days_map in intervals_by_operator_day.items():
            total_seconds = 0
            for intervals in (days_map or {}).values():
                for interval in self._merge_break_intervals(intervals or []):
                    total_seconds += max(0, int(interval.get('end', 0)) - int(interval.get('start', 0)))
            totals[op_id] = round(float(total_seconds) / 3600.0, 4)
        return totals

    def get_hourly_shift_grouping(self, target_date, department_id=None):
        """План и факт смен по часам за дату — тот же расчёт, что в «Группировке по операторам».

        План — взятые (claimed) лоты аукциона смен: сколько операторов должно быть в часе.
        Факт — операторы, у которых статусы «на смене» занимают не меньше 30 минут часа.
        Чат-менеджеры не участвуют: они живут на статусах Chat2Desk, а не Oktell.

        Возвращает [{hour, planned, fact}] на 24 часа. Это частный случай
        get_hourly_shift_grouping_range на один день — правило живёт в одном месте."""
        if hasattr(target_date, 'strftime'):
            date_obj = target_date
        else:
            try:
                date_obj = datetime.strptime(str(target_date), '%Y-%m-%d').date()
            except Exception:
                return [{'hour': hour, 'planned': 0, 'fact': 0} for hour in range(24)]
        by_day = self.get_hourly_shift_grouping_range(date_obj, date_obj, department_id)
        return by_day.get(date_obj.strftime('%Y-%m-%d')) or []

    def get_hourly_shift_grouping_range(self, date_from, date_to, department_id=None):
        """{'ГГГГ-ММ-ДД': [{hour, planned, fact}] на 24 часа} за каждый день периода.

        Правила — как у get_hourly_shift_grouping. Пакетом ради «Биллинг Oktell →
        Группировка», где период доходит до 31 дня: состав отдела и модели расчёта
        читаются один раз, статусы — одним запросом на весь период. Лоты аукциона
        собираются по дню: для каждой даты отдельно решается, брать активный план
        или опубликованную историю (get_shift_auction_lots_for_planner_date)."""
        days = []
        current = date_from
        while current <= date_to:
            days.append(current)
            current += timedelta(days=1)
        if not days:
            return {}

        planned_by_day = {}
        for day in days:
            day_key = day.strftime('%Y-%m-%d')
            # Лоты тянем отдельным вызовом: у него свой курсор, вложенность тут ни к чему.
            auction = self.get_shift_auction_lots_for_planner_date(day) or {}
            planned_by_hour = [set() for _ in range(24)]
            for lot in (auction.get('lots') or []):
                if str(lot.get('status') or '').strip().lower() != 'claimed':
                    continue
                try:
                    operator_id = int(lot.get('claimed_by') or 0)
                except (TypeError, ValueError):
                    continue
                if operator_id <= 0:
                    continue
                for start, end in self._hourly_lot_parts_for_date(lot, day_key):
                    for hour in range(24):
                        if min(end, hour * 60 + 60) - max(start, hour * 60) > 0:
                            planned_by_hour[hour].add(operator_id)
            planned_by_day[day_key] = planned_by_hour

        with self._get_cursor() as cursor:
            if department_id is not None:
                cursor.execute(
                    "SELECT id FROM users WHERE department_id = %s", (int(department_id),))
            else:
                cursor.execute("SELECT id FROM users")
            operator_ids = [int(r[0]) for r in (cursor.fetchall() or [])]
            if operator_ids:
                models = self._load_operator_calculation_models_tx(cursor, operator_ids)
                operator_ids = [
                    op_id for op_id in operator_ids
                    if str(models.get(op_id) or '').strip().lower() != CALCULATION_MODEL_CHAT_MANAGER
                ]
            timeline = self._load_imported_status_segments_for_operators(
                cursor, operator_ids, days[0], days[-1]) if operator_ids else {}

        result = {}
        for day in days:
            day_key = day.strftime('%Y-%m-%d')
            fact_by_hour = [set() for _ in range(24)]
            # Докуда вообще доехали статусы. Синк Oktell идёт несколько раз в сутки и отстаёт,
            # поэтому за последним известным моментом «ноль на смене» означает не «никто не
            # работал», а «данных ещё нет» — и такие часы отдаём как None, а не 0.
            data_until_minute = 0
            for operator_id, operator_days in (timeline or {}).items():
                segments = (operator_days or {}).get(day_key) or []
                on_shift = []
                for segment in segments:
                    key = str(segment.get('stateKey') or '').strip().lower()
                    if not key or key in self._HOURLY_NOT_ON_SHIFT_STATUS_KEYS:
                        continue
                    start_min = self._hourly_iso_to_minutes(segment.get('start'), day)
                    end_min = self._hourly_iso_to_minutes(segment.get('end'), day)
                    if start_min is None or end_min is None or end_min <= start_min:
                        continue
                    clipped_start, clipped_end = max(0, start_min), min(1440, end_min)
                    on_shift.append((clipped_start, clipped_end))
                    data_until_minute = max(data_until_minute, clipped_end)
                if not on_shift:
                    continue
                for hour in range(24):
                    h_start, h_end = hour * 60, hour * 60 + 60
                    clipped = [(max(h_start, s), min(h_end, e)) for s, e in on_shift]
                    if self._hourly_merge_minutes(clipped) >= self._HOURLY_FACT_MIN_MINUTES:
                        fact_by_hour[hour].add(int(operator_id))

            rows = []
            for hour in range(24):
                # Час считается «известным», если статусы доехали хотя бы до его половины —
                # иначе засчитать 30 минут в нём всё равно было бы невозможно.
                has_data = data_until_minute >= hour * 60 + self._HOURLY_FACT_MIN_MINUTES
                rows.append({
                    'hour': hour,
                    'planned': len(planned_by_day[day_key][hour]),
                    'fact': len(fact_by_hour[hour]) if has_data else None,
                })
            result[day_key] = rows
        return result

    def _find_shift_overlap_intervals_for_technical_issue_tx(
        self,
        cursor,
        operator_ids,
        issue_date_obj,
        start_time_obj,
        end_time_obj
    ):
        operator_ids_norm = self._coerce_int_list(operator_ids)
        if not operator_ids_norm:
            return {}

        issue_date_obj = self._parse_technical_issue_date(issue_date_obj, field_name='date')
        issue_start_min, issue_end_min = self._schedule_interval_minutes(start_time_obj, end_time_obj)
        issue_start_abs = int(issue_start_min)
        issue_end_abs = int(issue_end_min)
        if issue_end_abs <= issue_start_abs:
            return {}

        shifts_source_start = issue_date_obj - timedelta(days=1)
        shifts_source_end = issue_date_obj + timedelta(days=1)
        cursor.execute(
            """
            SELECT operator_id, shift_date, start_time, end_time
            FROM work_shifts
            WHERE operator_id = ANY(%s)
              AND shift_date >= %s
              AND shift_date <= %s
            ORDER BY operator_id, shift_date, start_time, end_time
            """,
            (operator_ids_norm, shifts_source_start, shifts_source_end)
        )
        rows = cursor.fetchall() or []

        overlaps_by_operator = {}
        for operator_id, shift_date_value, shift_start_value, shift_end_value in rows:
            if shift_date_value is None:
                continue
            day_offset = int((shift_date_value - issue_date_obj).days)
            shift_start_min, shift_end_min = self._schedule_interval_minutes(shift_start_value, shift_end_value)
            shift_start_abs = day_offset * 1440 + int(shift_start_min)
            shift_end_abs = day_offset * 1440 + int(shift_end_min)

            overlap_start = max(issue_start_abs, shift_start_abs)
            overlap_end = min(issue_end_abs, shift_end_abs)
            if overlap_end <= overlap_start:
                continue
            overlaps_by_operator.setdefault(int(operator_id), []).append({
                'start': int(overlap_start),
                'end': int(overlap_end)
            })

        for op_id in list(overlaps_by_operator.keys()):
            overlaps_by_operator[op_id] = self._merge_break_intervals(overlaps_by_operator.get(op_id) or [])
            if not overlaps_by_operator[op_id]:
                overlaps_by_operator.pop(op_id, None)

        return overlaps_by_operator

    def _normalize_work_shift_type(self, value):
        return _normalize_work_shift_type_value(value)

    def _is_office_practice_shift_type(self, value):
        return self._normalize_work_shift_type(value) == WORK_SHIFT_TYPE_OFFICE_PRACTICE

    def _is_phone_shift_type(self, value):
        return self._normalize_work_shift_type(value) == WORK_SHIFT_TYPE_PHONE_SHIFT

    def _planned_occupied_for_shift(self, planned_by_shift, operator_id, shift_date, direction_key,
                                    shift_meta):
        """
        Перерывы коллег направления из ещё не записанного плана.

        Пересчёт раскладывает направление в несколько проходов и пишет в базу
        только в конце, поэтому «занятое время» коллег на промежуточных проходах
        живёт в памяти, а не в shift_breaks. Соседний день сдвигается на сутки —
        так же, как это делает загрузка из базы.
        """
        if not planned_by_shift:
            return []
        date_obj = self._normalize_schedule_date(shift_date)
        result = []
        for other_shift_id, breaks in planned_by_shift.items():
            meta = shift_meta.get(other_shift_id)
            if not meta:
                continue
            other_operator_id, other_date, other_direction_key = meta
            if int(other_operator_id) == int(operator_id):
                continue
            if other_direction_key != direction_key:
                continue
            offset_days = (self._normalize_schedule_date(other_date) - date_obj).days
            if abs(offset_days) > 1:
                continue
            shift_minutes = offset_days * 1440
            for item in self._normalize_break_intervals_soft(breaks, dedupe=False):
                start = int(item['start']) + shift_minutes
                end = int(item['end']) + shift_minutes
                start = max(0, min(2880, start))
                end = max(0, min(2880, end))
                if end > start:
                    result.append({'start': start, 'end': end})
        return sorted(result, key=lambda item: (int(item['start']), int(item['end'])))

    def _schedule_auto_shift_start_excused_boundary(self, shift_interval, excused_intervals):
        shift_start = int(shift_interval.get('start', 0))
        shift_end = int(shift_interval.get('end', 0))
        if shift_end <= shift_start:
            return shift_start

        covered_until = shift_start
        for seg in self._merge_break_intervals(excused_intervals or []):
            seg_start = int(seg.get('start', 0))
            seg_end = int(seg.get('end', 0))
            if seg_end <= seg_start or seg_end <= covered_until:
                continue
            if seg_start > covered_until:
                break
            covered_until = min(shift_end, max(covered_until, seg_end))
            if covered_until >= shift_end:
                break
        return covered_until

    def _schedule_auto_shift_end_excused_boundary(self, shift_interval, excused_intervals):
        shift_start = int(shift_interval.get('start', 0))
        shift_end = int(shift_interval.get('end', 0))
        if shift_end <= shift_start:
            return shift_end

        covered_from = shift_end
        merged = self._merge_break_intervals(excused_intervals or [])
        for seg in reversed(merged):
            seg_start = int(seg.get('start', 0))
            seg_end = int(seg.get('end', 0))
            if seg_end <= seg_start:
                continue
            seg_start = max(shift_start, seg_start)
            seg_end = min(shift_end, seg_end)
            if seg_end <= seg_start:
                continue

            # Нет непрерывного покрытия до конца смены — останавливаемся.
            if seg_end < covered_from:
                break
            covered_from = max(shift_start, min(covered_from, seg_start))
            if covered_from <= shift_start:
                break
        return covered_from

    def _save_shift_tx(
        self,
        cursor,
        operator_id,
        shift_date,
        start_time,
        end_time,
        breaks=None,
        shift_type=None,
        previous_start_time=None,
        previous_end_time=None,
        cross_gap_minutes=None,
        now=None,
        day_breaks_snapshot=None,
        extra_occupied=None
    ):
        shift_type_norm = self._normalize_work_shift_type(shift_type)
        new_start_min, new_end_min = self._schedule_interval_minutes(start_time, end_time)
        new_duration_min = max(0, new_end_min - new_start_min)
        direction_name = self._get_operator_direction_name_tx(cursor, operator_id)
        direction_rules = self._get_work_schedule_break_rules_for_direction_tx(cursor, direction_name)

        # Снимок перерывов дня снимаем ДО любых удалений: перерывы уезжают из базы
        # каскадом вместе со строкой смены, а прошедшие переписывать нельзя.
        # На путях, которые чистят день заранее, снимок приходит сверху.
        if day_breaks_snapshot is None:
            day_breaks_snapshot = self._load_day_shift_breaks_tx(cursor, operator_id, shift_date)
        freeze_boundary_min = self._break_freeze_boundary_minutes(
            shift_date,
            seg_start=new_start_min,
            now=now
        )
        planning_from_min = freeze_boundary_min + SHIFT_BREAK_PLANNING_BUFFER_MINUTES
        frozen_breaks, stored_upcoming_breaks, used_breaks = self._split_breaks_by_freeze_boundary(
            day_breaks_snapshot,
            planning_from_min,
            seg_start=new_start_min,
            seg_end=new_end_min
        )
        # Смена целиком в прошлом и сохранять нечего (заводят задним числом) —
        # раскладываем по всей смене, как раньше: переписывать нечего.
        nothing_to_protect = not frozen_breaks and not used_breaks and not stored_upcoming_breaks
        planning_window_start = (
            new_start_min
            if nothing_to_protect and planning_from_min >= new_end_min
            else max(new_start_min, planning_from_min)
        )

        prev_start_obj = None
        prev_end_obj = None
        if previous_start_time is not None and previous_end_time is not None:
            prev_start_obj = self._normalize_schedule_time(previous_start_time, 'previous_start_time')
            prev_end_obj = self._normalize_schedule_time(previous_end_time, 'previous_end_time')

        comparison_start_obj = prev_start_obj if prev_start_obj is not None else start_time
        comparison_end_obj = prev_end_obj if prev_end_obj is not None else end_time
        cursor.execute(
            """
            SELECT id, start_time, end_time
            FROM work_shifts
            WHERE operator_id = %s AND shift_date = %s
              AND start_time = %s AND end_time = %s
            LIMIT 1
            """,
            (operator_id, shift_date, comparison_start_obj, comparison_end_obj)
        )
        previous_shift_row = cursor.fetchone()
        previous_shift_breaks = []
        previous_shift_breaks_were_auto = False
        previous_duration_min = 0
        if previous_shift_row:
            previous_shift_id, previous_start_value, previous_end_value = previous_shift_row
            previous_start_min, previous_end_min = self._schedule_interval_minutes(previous_start_value, previous_end_value)
            previous_duration_min = max(0, previous_end_min - previous_start_min)
            previous_shift_breaks = self._load_shift_breaks_tx(cursor, previous_shift_id)
            previous_auto_breaks = self._compute_auto_shift_breaks_minutes(
                previous_start_min,
                previous_end_min,
                direction_name=direction_name,
                direction_rules=direction_rules
            )
            previous_shift_breaks_were_auto = (previous_shift_breaks == previous_auto_breaks)

        def _auto_breaks_for_remaining_shift():
            """
            Автогенерация с учётом прошлого: норма по длительности смены минус уже
            отсиженное, разложенная только по остатку смены. Перерывы, которые уже
            стоят в графике и ещё не начались, остаются на своих местах.
            """
            full_durations = self._pick_break_durations_for_shift(
                duration_minutes=new_duration_min,
                direction_name=direction_name,
                direction_rules=direction_rules
            )
            if planning_window_start <= new_start_min and not used_breaks:
                # Ничего не заморожено — прежняя раскладка по всей смене.
                planned = self._place_break_durations_centered_minutes(
                    new_start_min,
                    new_end_min,
                    full_durations
                )
            else:
                remaining_durations = self._remaining_break_durations_after_used(
                    full_durations,
                    used_breaks
                )
                planned = self._place_break_durations_centered_minutes(
                    planning_window_start,
                    new_end_min,
                    self._fit_break_durations_to_window(
                        planning_window_start,
                        new_end_min,
                        remaining_durations
                    )
                )
            return self._seed_break_positions_from_existing(planned, stored_upcoming_breaks)

        # Перерывы пришли готовыми (руками в окне смены или разложены клиентом),
        # а не сгенерированы здесь: у направления с «Разрешить пересечение» они
        # остаются где стоят.
        breaks_are_placed = False
        if breaks is None:
            breaks_norm = _auto_breaks_for_remaining_shift()
        else:
            incoming_breaks_norm = self._normalize_shift_breaks(breaks)
            should_regenerate_auto_breaks = (
                previous_shift_row is not None
                and previous_shift_breaks_were_auto
                and incoming_breaks_norm == previous_shift_breaks
                and new_duration_min < previous_duration_min
            )
            if should_regenerate_auto_breaks:
                breaks_norm = _auto_breaks_for_remaining_shift()
            else:
                # Прошлое клиент менять не может: прошедшие перерывы берём из снимка,
                # из присланного списка используем только ещё не наступившие.
                # Границами смены присланное не режем — их, как и раньше,
                # приводит в порядок анти-пересечение.
                breaks_norm = [
                    item for item in incoming_breaks_norm
                    if int(item['start']) >= planning_from_min
                ]
                breaks_are_placed = True

        # Приводим перерывы к правилам анти-пересечения между операторами одного направления
        # (включая минимальный промежуток между перерывами разных операторов).
        breaks_norm = self._adjust_shift_breaks_against_occupied_tx(
            cursor=cursor,
            operator_id=operator_id,
            shift_date=shift_date,
            start_time=start_time,
            end_time=end_time,
            breaks=breaks_norm,
            cross_gap_minutes=(
                cross_gap_minutes
                if cross_gap_minutes is not None
                else self._get_break_cross_operator_gap_for_direction_tx(cursor, direction_name)
            ),
            frozen_breaks=frozen_breaks,
            planning_from_minutes=planning_window_start,
            extra_occupied=extra_occupied,
            allow_overlap=self._break_overlap_allowed_for_direction_tx(cursor, direction_name),
            keep_positions=breaks_are_placed
        )
        # Смене СВ перерывы ставит только РОП руками (задача #352): автоматика и
        # правила направлений тут не работают (у СВ направления нет — сработал бы
        # запасной профиль операторов 15/30/15), соседей по перерывам у СВ тоже нет.
        # Берём присланное как есть, в пределах смены, в том числе в прошлом: по
        # этим перерывам считается вычет из часов СВ за день.
        if (
            self._clockster_hours_supervisor_ids_tx(cursor, [operator_id])
            and not self._operator_membership_days_tx(
                cursor, [operator_id],
                self._normalize_schedule_date(shift_date), self._normalize_schedule_date(shift_date))
        ):
            shift_start_min, shift_end_min = self._schedule_interval_minutes(start_time, end_time)
            breaks_norm = [
                item for item in self._normalize_shift_breaks(breaks)
                if int(item['start']) >= shift_start_min and int(item['end']) <= shift_end_min
            ]

        # Если редактировали существующую смену по previous_* и время изменилось,
        # удаляем старую запись, чтобы не оставлять дубль.
        if prev_start_obj is not None and prev_end_obj is not None:
            if (
                prev_start_obj != start_time or prev_end_obj != end_time
            ):
                cursor.execute(
                    """
                    DELETE FROM work_shifts
                    WHERE operator_id = %s AND shift_date = %s
                      AND start_time = %s AND end_time = %s
                    """,
                    (operator_id, shift_date, prev_start_obj, prev_end_obj)
                )

        cursor.execute(
            """
            SELECT id, start_time, end_time
            FROM work_shifts
            WHERE operator_id = %s AND shift_date = %s
            """,
            (operator_id, shift_date)
        )
        existing_rows = cursor.fetchall()

        overlap_ids = []
        for shift_id, existing_start, existing_end in existing_rows:
            if existing_start == start_time and existing_end == end_time:
                continue
            ex_start_min, ex_end_min = self._schedule_interval_minutes(existing_start, existing_end)
            if new_start_min < ex_end_min and ex_start_min < new_end_min:
                overlap_ids.append(shift_id)

        if overlap_ids:
            cursor.execute(
                "DELETE FROM work_shifts WHERE id = ANY(%s)",
                (overlap_ids,)
            )

        cursor.execute(
            """
            INSERT INTO work_shifts (operator_id, shift_date, start_time, end_time, shift_type, updated_at)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (operator_id, shift_date, start_time, end_time)
            DO UPDATE SET shift_type = EXCLUDED.shift_type, updated_at = CURRENT_TIMESTAMP
            RETURNING id
            """,
            (operator_id, shift_date, start_time, end_time, shift_type_norm)
        )
        shift_id = cursor.fetchone()[0]
        self._insert_shift_breaks(cursor, shift_id, breaks_norm)

        cursor.execute(
            """
            DELETE FROM days_off
            WHERE operator_id = %s AND day_off_date = %s
            """,
            (operator_id, shift_date)
        )

        # Если на дату сохранённой смены приходился период "увольнение",
        # считаем увольнение прерванным (не продолжаем его после рабочего дня).
        self._interrupt_dismissal_period_by_work_day_tx(cursor, operator_id, shift_date)
        self._sync_user_statuses_from_schedule_periods_tx(cursor, operator_ids=[operator_id])

        return shift_id

    def get_operators_with_shifts(
        self,
        start_date=None,
        end_date=None,
        direction_name=None,
        department_id=None,
        include_imported_statuses=False,
        include_technical_issues=False,
        include_offline_activities=False,
        include_all_staff=False
    ):
        """
        Получить всех операторов со сменами и выходными днями за период.
        Если direction_name задан — только операторы этого направления.
        Если department_id задан — только операторы этого отдела.
        include_all_staff — строки планировщика «Графиков работы»: ВСЕ сотрудники,
        а не только операторы. Сначала (задача #352) сюда добавили СВ отдела
        продаж; решение владельца 09.11.2026 — «по всем нужно сделать возможность
        вести графики»: бэк-офис, руководители, СВ, тренеры, новые отделы без линии.
        Только для строк планировщика: импорт и выгрузка Excel, симуляция и «смены
        коллег» зовут без флага — там людей ищут по ФИО и раскладывают перерывы
        соседям по линии, и не-оператор подставился бы туда чужим графиком.
        group_name — текущая группа человека: у отделов без направлений сетка
        подписывает строки ей, а не пустым «Без направления».
        Возвращает список операторов с их сменами и выходными.
        """
        start_date_obj = self._normalize_schedule_date(start_date) if start_date else None
        end_date_obj = self._normalize_schedule_date(end_date) if end_date else None

        with self._get_cursor() as cursor:
            self._sync_user_statuses_from_schedule_periods_tx(cursor)
            department_filter_id = int(department_id) if department_id is not None else None
            if direction_name:
                operator_filters = [
                    "u.role = 'operator'",
                    "LOWER(d.name) = LOWER(%s)",
                    "(u.status IS NULL OR u.status != 'fired')"
                ]
                operator_params = [direction_name]
                if department_filter_id is not None:
                    operator_filters.append("u.department_id = %s")
                    operator_params.append(department_filter_id)
                cursor.execute("""
                    SELECT u.id, u.name, u.supervisor_id, s.name as supervisor_name,
                           d.name as direction, u.status, u.rate, u.department_id, u.role,
                           """ + self._SCHEDULE_ROW_GROUP_NAME_SQL + """ AS group_name
                    FROM users u
                    LEFT JOIN users s ON u.supervisor_id = s.id
                    LEFT JOIN directions d ON u.direction_id = d.id
                    WHERE """ + " AND ".join(operator_filters) + """
                    ORDER BY u.name
                """, operator_params)
            else:
                # Без флага — только линия: операторы. С флагом — все роли, и
                # условие по роли не ставится вовсе (см. include_all_staff выше).
                operator_filters = [] if include_all_staff else ["u.role = 'operator'"]
                operator_params = []
                if department_filter_id is not None:
                    operator_filters.append("u.department_id = %s")
                    operator_params.append(department_filter_id)
                where_sql = (" WHERE " + " AND ".join(operator_filters)) if operator_filters else ""
                cursor.execute("""
                    SELECT u.id, u.name, u.supervisor_id, s.name as supervisor_name,
                           d.name as direction, u.status, u.rate, u.department_id, u.role,
                           """ + self._SCHEDULE_ROW_GROUP_NAME_SQL + """ AS group_name
                    FROM users u
                    LEFT JOIN users s ON u.supervisor_id = s.id
                    LEFT JOIN directions d ON u.direction_id = d.id""" + where_sql + """
                    ORDER BY d.name, u.name
                """, operator_params)
            operators = cursor.fetchall()
            if not operators:
                return []

            operator_ids = [row[0] for row in operators]
            result_map = {}
            # department_id едет во фронт: в «Графиках работы» админ переключает
            # отдел, а состав операторов в ответе один на все отделы.
            for (op_id, name, supervisor_id, supervisor_name, direction, status, rate,
                 department_id, role, group_name) in operators:
                result_map[op_id] = {
                    'id': op_id,
                    'name': name,
                    'role': role,
                    'supervisor_id': supervisor_id,
                    'supervisor_name': supervisor_name,
                    'direction': direction,
                    'group_name': group_name,
                    'status': status,
                    'rate': float(rate) if rate else 1.0,
                    'department_id': department_id,
                    'shifts': {},
                    'daysOff': [],
                    'scheduleStatusPeriods': [],
                    'scheduleStatusDays': {},
                    'aggregatedScheduleFlagsDays': {},
                    'importedStatusTimelineDays': {},
                    'technicalIssueTimelineDays': {},
                    'offlineActivityTimelineDays': {}
                }

            shifts_query = """
                SELECT ws.id, ws.operator_id, ws.shift_date, ws.start_time, ws.end_time, ws.shift_type
                FROM work_shifts ws
                WHERE ws.operator_id = ANY(%s)
            """
            shifts_params = [operator_ids]
            if start_date_obj:
                shifts_query += " AND ws.shift_date >= %s"
                shifts_params.append(start_date_obj)
            if end_date_obj:
                shifts_query += " AND ws.shift_date <= %s"
                shifts_params.append(end_date_obj)
            shifts_query += " ORDER BY ws.operator_id, ws.shift_date, ws.start_time"
            cursor.execute(shifts_query, shifts_params)
            shifts_rows = cursor.fetchall()

            shift_ids = []
            shift_ref = {}
            for shift_id, operator_id, shift_date, start_time_value, end_time_value, shift_type_value in shifts_rows:
                op_entry = result_map.get(operator_id)
                if not op_entry:
                    continue
                date_str = shift_date.strftime('%Y-%m-%d')
                shift_item = {
                    'id': shift_id,
                    'start': start_time_value.strftime('%H:%M'),
                    'end': end_time_value.strftime('%H:%M'),
                    'shift_type': self._normalize_work_shift_type(shift_type_value),
                    'breaks': []
                }
                op_entry['shifts'].setdefault(date_str, []).append(shift_item)
                shift_ids.append(shift_id)
                shift_ref[shift_id] = shift_item

            if shift_ids:
                cursor.execute(
                    """
                    SELECT shift_id, start_minutes, end_minutes
                    FROM shift_breaks
                    WHERE shift_id = ANY(%s)
                    ORDER BY shift_id, start_minutes, end_minutes
                    """,
                    (shift_ids,)
                )
                for shift_id, br_start, br_end in cursor.fetchall():
                    item = shift_ref.get(shift_id)
                    if item is None:
                        continue
                    item['breaks'].append({'start': int(br_start), 'end': int(br_end)})

            # Keep backward-compatible read behavior: frontend expects already merged segments on load.
            for op_entry in result_map.values():
                for day_key, day_shifts in list(op_entry['shifts'].items()):
                    op_entry['shifts'][day_key] = _merge_shifts_for_date(day_shifts)

            days_off_query = """
                SELECT operator_id, day_off_date
                FROM days_off
                WHERE operator_id = ANY(%s)
            """
            days_off_params = [operator_ids]
            if start_date_obj:
                days_off_query += " AND day_off_date >= %s"
                days_off_params.append(start_date_obj)
            if end_date_obj:
                days_off_query += " AND day_off_date <= %s"
                days_off_params.append(end_date_obj)
            days_off_query += " ORDER BY operator_id, day_off_date"
            cursor.execute(days_off_query, days_off_params)
            for operator_id, day_off_date in cursor.fetchall():
                op_entry = result_map.get(operator_id)
                if op_entry is None:
                    continue
                op_entry['daysOff'].append(day_off_date.strftime('%Y-%m-%d'))

            self._attach_schedule_status_periods_to_operators(
                cursor=cursor,
                operators_map=result_map,
                operator_ids=operator_ids,
                start_date_obj=start_date_obj,
                end_date_obj=end_date_obj
            )
            self._attach_auto_schedule_flags_to_operators(
                cursor=cursor,
                operators_map=result_map,
                operator_ids=operator_ids,
                start_date_obj=start_date_obj,
                end_date_obj=end_date_obj
            )
            if include_imported_statuses:
                self._attach_imported_status_segments_to_operators(
                    cursor=cursor,
                    operators_map=result_map,
                    operator_ids=operator_ids,
                    start_date_obj=start_date_obj,
                    end_date_obj=end_date_obj
                )
                # Текущий статус в сегментах не лежит — см. «Живой хвост
                # статусов». Просим его, только если в периоде есть сегодня.
                if self._status_range_covers_today(start_date_obj, end_date_obj):
                    self._attach_live_status_tail_to_operators(
                        cursor=cursor,
                        operators_map=result_map,
                        operator_ids=operator_ids
                    )
            if include_technical_issues:
                self._attach_technical_issue_segments_to_operators(
                    cursor=cursor,
                    operators_map=result_map,
                    operator_ids=operator_ids,
                    start_date_obj=start_date_obj,
                    end_date_obj=end_date_obj
                )
            if include_offline_activities:
                self._attach_offline_activity_segments_to_operators(
                    cursor=cursor,
                    operators_map=result_map,
                    operator_ids=operator_ids,
                    start_date_obj=start_date_obj,
                    end_date_obj=end_date_obj
                )

            return [result_map[row[0]] for row in operators]

    def get_operator_with_shifts(
        self,
        operator_id,
        start_date=None,
        end_date=None,
        include_imported_statuses=False,
        include_technical_issues=False,
        include_offline_activities=False
    ):
        """
        Получить одного оператора с его сменами/перерывами/выходными за период.
        Возвращает структуру, совместимую с get_operators_with_shifts()[i].
        """
        operator_id = int(operator_id)
        start_date_obj = self._normalize_schedule_date(start_date) if start_date else None
        end_date_obj = self._normalize_schedule_date(end_date) if end_date else None

        with self._get_cursor() as cursor:
            self._sync_user_statuses_from_schedule_periods_tx(cursor, operator_ids=[operator_id])
            cursor.execute("""
                SELECT u.id, u.name, u.supervisor_id, s.name as supervisor_name,
                       d.name as direction, u.status, u.rate
                FROM users u
                LEFT JOIN users s ON u.supervisor_id = s.id
                LEFT JOIN directions d ON u.direction_id = d.id
                WHERE u.id = %s AND u.role = 'operator'
                LIMIT 1
            """, (operator_id,))
            row = cursor.fetchone()
            if not row:
                return None

            op_id, name, supervisor_id, supervisor_name, direction, status, rate = row
            result = {
                'id': op_id,
                'name': name,
                'supervisor_id': supervisor_id,
                'supervisor_name': supervisor_name,
                'direction': direction,
                'status': status,
                'rate': float(rate) if rate else 1.0,
                'shifts': {},
                'daysOff': [],
                'scheduleStatusPeriods': [],
                'scheduleStatusDays': {},
                'aggregatedScheduleFlagsDays': {},
                'importedStatusTimelineDays': {},
                'technicalIssueTimelineDays': {},
                'offlineActivityTimelineDays': {}
            }

            shifts_query = """
                SELECT ws.id, ws.shift_date, ws.start_time, ws.end_time, ws.shift_type
                FROM work_shifts ws
                WHERE ws.operator_id = %s
            """
            shifts_params = [operator_id]
            if start_date_obj:
                shifts_query += " AND ws.shift_date >= %s"
                shifts_params.append(start_date_obj)
            if end_date_obj:
                shifts_query += " AND ws.shift_date <= %s"
                shifts_params.append(end_date_obj)
            shifts_query += " ORDER BY ws.shift_date, ws.start_time"
            cursor.execute(shifts_query, shifts_params)
            shifts_rows = cursor.fetchall()

            shift_ids = []
            shift_ref = {}
            for shift_id, shift_date, start_time_value, end_time_value, shift_type_value in shifts_rows:
                date_str = shift_date.strftime('%Y-%m-%d')
                shift_item = {
                    'id': shift_id,
                    'start': start_time_value.strftime('%H:%M'),
                    'end': end_time_value.strftime('%H:%M'),
                    'shift_type': self._normalize_work_shift_type(shift_type_value),
                    'breaks': []
                }
                result['shifts'].setdefault(date_str, []).append(shift_item)
                shift_ids.append(shift_id)
                shift_ref[shift_id] = shift_item

            if shift_ids:
                cursor.execute(
                    """
                    SELECT shift_id, start_minutes, end_minutes
                    FROM shift_breaks
                    WHERE shift_id = ANY(%s)
                    ORDER BY shift_id, start_minutes, end_minutes
                    """,
                    (shift_ids,)
                )
                for shift_id, br_start, br_end in cursor.fetchall():
                    item = shift_ref.get(shift_id)
                    if item is None:
                        continue
                    item['breaks'].append({'start': int(br_start), 'end': int(br_end)})

            for day_key, day_shifts in list(result['shifts'].items()):
                result['shifts'][day_key] = _merge_shifts_for_date(day_shifts)

            days_off_query = """
                SELECT day_off_date
                FROM days_off
                WHERE operator_id = %s
            """
            days_off_params = [operator_id]
            if start_date_obj:
                days_off_query += " AND day_off_date >= %s"
                days_off_params.append(start_date_obj)
            if end_date_obj:
                days_off_query += " AND day_off_date <= %s"
                days_off_params.append(end_date_obj)
            days_off_query += " ORDER BY day_off_date"
            cursor.execute(days_off_query, days_off_params)
            for (day_off_date,) in cursor.fetchall():
                result['daysOff'].append(day_off_date.strftime('%Y-%m-%d'))

            self._attach_schedule_status_periods_to_operators(
                cursor=cursor,
                operators_map={operator_id: result},
                operator_ids=[operator_id],
                start_date_obj=start_date_obj,
                end_date_obj=end_date_obj
            )
            self._attach_auto_schedule_flags_to_operators(
                cursor=cursor,
                operators_map={operator_id: result},
                operator_ids=[operator_id],
                start_date_obj=start_date_obj,
                end_date_obj=end_date_obj
            )
            if include_imported_statuses:
                self._attach_imported_status_segments_to_operators(
                    cursor=cursor,
                    operators_map={operator_id: result},
                    operator_ids=[operator_id],
                    start_date_obj=start_date_obj,
                    end_date_obj=end_date_obj
                )
                if self._status_range_covers_today(start_date_obj, end_date_obj):
                    self._attach_live_status_tail_to_operators(
                        cursor=cursor,
                        operators_map={operator_id: result},
                        operator_ids=[operator_id]
                    )
            if include_technical_issues:
                self._attach_technical_issue_segments_to_operators(
                    cursor=cursor,
                    operators_map={operator_id: result},
                    operator_ids=[operator_id],
                    start_date_obj=start_date_obj,
                    end_date_obj=end_date_obj
                )
            if include_offline_activities:
                self._attach_offline_activity_segments_to_operators(
                    cursor=cursor,
                    operators_map={operator_id: result},
                    operator_ids=[operator_id],
                    start_date_obj=start_date_obj,
                    end_date_obj=end_date_obj
                )

            return result

    def _load_operator_shift_map_for_period_tx(self, cursor, operator_id, start_date_obj, end_date_obj):
        operator_id = int(operator_id)
        start_date_obj = self._normalize_schedule_date(start_date_obj)
        end_date_obj = self._normalize_schedule_date(end_date_obj)
        if end_date_obj < start_date_obj:
            raise ValueError("end_date must be >= start_date")

        cursor.execute(
            """
            SELECT ws.id, ws.shift_date, ws.start_time, ws.end_time, ws.shift_type
            FROM work_shifts ws
            WHERE ws.operator_id = %s
              AND ws.shift_date >= %s
              AND ws.shift_date <= %s
            ORDER BY ws.shift_date, ws.start_time
            """,
            (operator_id, start_date_obj, end_date_obj)
        )
        rows = cursor.fetchall() or []

        shifts_by_date = {}
        shift_ref = {}
        shift_ids = []
        for shift_id, shift_date, start_time_value, end_time_value, shift_type_value in rows:
            day_key = shift_date.strftime('%Y-%m-%d')
            item = {
                'start': start_time_value.strftime('%H:%M'),
                'end': end_time_value.strftime('%H:%M'),
                'shift_type': self._normalize_work_shift_type(shift_type_value),
                'breaks': []
            }
            shifts_by_date.setdefault(day_key, []).append(item)
            shift_ref[int(shift_id)] = item
            shift_ids.append(int(shift_id))

        if shift_ids:
            cursor.execute(
                """
                SELECT shift_id, start_minutes, end_minutes
                FROM shift_breaks
                WHERE shift_id = ANY(%s)
                ORDER BY shift_id, start_minutes, end_minutes
                """,
                (shift_ids,)
            )
            for shift_id, br_start, br_end in cursor.fetchall() or []:
                target = shift_ref.get(int(shift_id))
                if target is None:
                    continue
                target['breaks'].append({
                    'start': int(br_start),
                    'end': int(br_end)
                })

        normalized = {}
        for day_key, segs in shifts_by_date.items():
            merged = _merge_shifts_for_date(segs or [])
            if merged:
                normalized[day_key] = merged
        return dict(sorted(normalized.items(), key=lambda x: x[0]))

    def _normalize_shift_snapshot_map(self, raw_snapshot):
        if raw_snapshot is None:
            return {}

        source = raw_snapshot
        if isinstance(source, str):
            text = source.strip()
            if not text:
                return {}
            try:
                source = json.loads(text)
            except Exception as exc:
                raise ValueError("Invalid requested_shifts_json format") from exc

        if not isinstance(source, dict):
            raise ValueError("requested_shifts_json must be an object")

        normalized = {}
        for raw_day, raw_shifts in source.items():
            try:
                day_obj = self._normalize_schedule_date(raw_day)
            except Exception as exc:
                raise ValueError("Invalid date in requested_shifts_json") from exc
            day_key = day_obj.strftime('%Y-%m-%d')

            if not isinstance(raw_shifts, list):
                continue

            parsed_shifts = []
            for seg in raw_shifts:
                if not isinstance(seg, dict):
                    continue
                start_raw = seg.get('start') or seg.get('start_time')
                end_raw = seg.get('end') or seg.get('end_time')
                if not start_raw or not end_raw:
                    continue
                start_obj = self._normalize_schedule_time(start_raw, 'start_time')
                end_obj = self._normalize_schedule_time(end_raw, 'end_time')
                breaks_raw = seg.get('breaks') if isinstance(seg.get('breaks'), list) else []
                breaks_norm = self._normalize_shift_breaks(breaks_raw)
                parsed_shifts.append({
                    'start': start_obj.strftime('%H:%M'),
                    'end': end_obj.strftime('%H:%M'),
                    'shift_type': self._normalize_work_shift_type(
                        seg.get('shift_type') if isinstance(seg, dict) and ('shift_type' in seg) else seg.get('shiftType')
                    ),
                    'breaks': breaks_norm
                })

            merged = _merge_shifts_for_date(parsed_shifts)
            if merged:
                normalized[day_key] = merged

        return dict(sorted(normalized.items(), key=lambda x: x[0]))

    def _snapshot_shift_totals(self, snapshot):
        days_count = 0
        shifts_count = 0
        total_minutes = 0
        for day_key, shifts in (snapshot or {}).items():
            if not isinstance(shifts, list) or not shifts:
                continue
            days_count += 1
            for seg in shifts:
                start_val = seg.get('start')
                end_val = seg.get('end')
                if not start_val or not end_val:
                    continue
                start_min, end_min = self._schedule_interval_minutes(start_val, end_val)
                shifts_count += 1
                total_minutes += max(0, int(end_min) - int(start_min))
        return {
            'daysCount': int(days_count),
            'shiftsCount': int(shifts_count),
            'totalMinutes': int(total_minutes)
        }

    def _extract_day_interval_segments_from_shifts(self, day_shifts, interval_start_min, interval_end_min):
        interval_start_min = int(interval_start_min)
        interval_end_min = int(interval_end_min)
        if interval_end_min <= interval_start_min:
            return []

        result = []
        for seg in (day_shifts or []):
            start_val = seg.get('start')
            end_val = seg.get('end')
            if not start_val or not end_val:
                continue
            seg_start, seg_end = self._schedule_interval_minutes(start_val, end_val)
            seg_start = max(0, min(1440, int(seg_start)))
            seg_end = max(0, min(1440, int(seg_end)))
            if seg_end <= seg_start:
                continue

            overlap_start = max(seg_start, interval_start_min)
            overlap_end = min(seg_end, interval_end_min)
            if overlap_end <= overlap_start:
                continue

            raw_breaks = self._normalize_shift_breaks(seg.get('breaks') if isinstance(seg.get('breaks'), list) else [])
            clipped_breaks = []
            for b in raw_breaks:
                b_start = max(overlap_start, int(b.get('start', 0)))
                b_end = min(overlap_end, int(b.get('end', 0)))
                if b_end > b_start:
                    clipped_breaks.append({'start': b_start, 'end': b_end})

            result.append({
                'start': _minutes_to_time(overlap_start),
                'end': _minutes_to_time(overlap_end),
                'breaks': clipped_breaks
            })

        if not result:
            return []
        return _merge_shifts_for_date(result)

    def _is_interval_fully_covered_by_day_shifts(self, day_shifts, interval_start_min, interval_end_min):
        extracted = self._extract_day_interval_segments_from_shifts(day_shifts, interval_start_min, interval_end_min)
        if not extracted:
            return False
        intervals = []
        for seg in extracted:
            s, e = self._schedule_interval_minutes(seg.get('start'), seg.get('end'))
            s = max(0, min(1440, int(s)))
            e = max(0, min(1440, int(e)))
            if e > s:
                intervals.append({'start': s, 'end': e})
        merged = self._merge_break_intervals(intervals)
        if len(merged) != 1:
            return False
        return int(merged[0]['start']) <= int(interval_start_min) and int(merged[0]['end']) >= int(interval_end_min)

    def _day_shifts_overlap_interval(self, day_shifts, interval_start_min, interval_end_min):
        interval_start_min = int(interval_start_min)
        interval_end_min = int(interval_end_min)
        if interval_end_min <= interval_start_min:
            return False

        for seg in (day_shifts or []):
            start_val = seg.get('start')
            end_val = seg.get('end')
            if not start_val or not end_val:
                continue
            seg_start, seg_end = self._schedule_interval_minutes(start_val, end_val)
            seg_start = max(0, min(1440, int(seg_start)))
            seg_end = max(0, min(1440, int(seg_end)))
            if seg_end <= seg_start:
                continue
            if seg_start < interval_end_min and interval_start_min < seg_end:
                return True
        return False

    def _subtract_interval_from_day_shifts(self, day_shifts, interval_start_min, interval_end_min):
        interval_start_min = int(interval_start_min)
        interval_end_min = int(interval_end_min)
        if interval_end_min <= interval_start_min:
            return _merge_shifts_for_date(day_shifts or [])

        result = []
        for seg in (day_shifts or []):
            start_val = seg.get('start')
            end_val = seg.get('end')
            if not start_val or not end_val:
                continue
            seg_start, seg_end = self._schedule_interval_minutes(start_val, end_val)
            seg_start = int(seg_start)
            seg_end = int(seg_end)
            if seg_end <= seg_start:
                continue

            # Для overlap-проверки учитываем только часть, принадлежащую выбранной дате (00:00-24:00).
            seg_day_start = max(0, min(1440, seg_start))
            seg_day_end = max(0, min(1440, seg_end))

            if seg_day_end <= interval_start_min or seg_day_start >= interval_end_min:
                result.append({
                    'start': _minutes_to_time(seg_start),
                    'end': _minutes_to_time(seg_end),
                    'breaks': self._normalize_shift_breaks(seg.get('breaks') if isinstance(seg.get('breaks'), list) else [])
                })
                continue

            left_start = seg_start
            left_end = min(seg_end, interval_start_min)
            right_start = max(seg_start, interval_end_min)
            right_end = seg_end

            raw_breaks = self._normalize_shift_breaks(seg.get('breaks') if isinstance(seg.get('breaks'), list) else [])

            def append_piece(piece_start, piece_end):
                if piece_end <= piece_start:
                    return
                piece_breaks = []
                for b in raw_breaks:
                    b_start = max(piece_start, int(b.get('start', 0)))
                    b_end = min(piece_end, int(b.get('end', 0)))
                    if b_end > b_start:
                        piece_breaks.append({'start': b_start, 'end': b_end})
                result.append({
                    'start': _minutes_to_time(piece_start),
                    'end': _minutes_to_time(piece_end),
                    'breaks': piece_breaks
                })

            append_piece(left_start, left_end)
            append_piece(right_start, right_end)

        if not result:
            return []
        return _merge_shifts_for_date(result)

    def _normalize_shift_change_kind(self, value):
        kind = str(value or '').strip().lower()
        if kind not in self.SHIFT_CHANGE_REQUEST_KINDS:
            # Текст уходит оператору как есть, поэтому по-русски и без перечня видов.
            raise ValueError("Неизвестный вид заявки")
        return kind

    def _shift_change_minutes(self, start_value, end_value):
        """Интервал в минутах от полуночи дня заявки, конец может уйти за 1440.

        Отдельная функция, а не _schedule_interval_minutes: там «конец меньше
        начала» всегда означает ночную смену, а здесь надо ещё и отсечь
        интервал длиннее суток — иначе опечатка превращается в 30-часовую смену.
        """
        start_obj = self._normalize_schedule_time(start_value, 'start_time')
        end_obj = self._normalize_schedule_time(end_value, 'end_time')
        start_min = start_obj.hour * 60 + start_obj.minute
        end_min = end_obj.hour * 60 + end_obj.minute
        if end_min <= start_min:
            # Полночь как конец смены пишут и как 00:00, и как 24:00.
            end_min += 1440
        # Ровно сутки — это «09:00 — 09:00», то есть чаще всего оператор
        # просто не поменял второе поле. Пропустив такое, мы записали бы ему
        # круглосуточную смену, поэтому граница строгая.
        if end_min - start_min >= 1440:
            raise ValueError("Интервал должен быть короче суток")
        return int(start_min), int(end_min)

    def _load_shift_change_window_tx(self, cursor, operator_id, shift_date_obj):
        """Смены оператора за окно вчера/сегодня/завтра одним набором интервалов."""
        prev_date_obj = shift_date_obj - timedelta(days=1)
        next_date_obj = shift_date_obj + timedelta(days=1)
        day_map = self._load_operator_shift_map_for_period_tx(
            cursor=cursor,
            operator_id=operator_id,
            start_date_obj=prev_date_obj,
            end_date_obj=next_date_obj
        )
        window = self._build_swap_window_intervals(
            prev_day_shifts=day_map.get(prev_date_obj.strftime('%Y-%m-%d'), []),
            day_shifts=day_map.get(shift_date_obj.strftime('%Y-%m-%d'), []),
            next_day_shifts=day_map.get(next_date_obj.strftime('%Y-%m-%d'), [])
        )
        return day_map, window

    def _normalize_shift_change_payload(self, kind, raw_payload, shift_date_obj=None):
        """Привести тело заявки к каноничному виду и проверить его самосогласованность.

        Проверка «а есть ли такая смена на самом деле» здесь НЕ делается: она
        нужна и при подаче, и повторно при согласовании (график мог измениться),
        поэтому живёт в _validate_shift_change_against_schedule и в сверках
        переноса и выходного.
        """
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        if kind == 'extra':
            start_min, end_min = self._shift_change_minutes(
                payload.get('start'), payload.get('end')
            )
            return {
                'start': _minutes_to_time(start_min),
                'end': _minutes_to_time(end_min),
                'startMin': start_min,
                'endMin': end_min,
            }
        if kind == 'day_off':
            # Что именно снимется, клиент не присылает: снимок смен дня берёт
            # сервер из графика (_shift_change_day_off_payload), иначе в заявке
            # можно было бы показать руководителю одно, а снять другое.
            return {}
        if kind not in ('shorten', 'change'):
            raise ValueError("Неизвестный вид заявки")

        segment = payload.get('segment') if isinstance(payload.get('segment'), dict) else {}
        seg_start_min, seg_end_min = self._shift_change_minutes(
            segment.get('start'), segment.get('end')
        )
        new_start_min, new_end_min = self._shift_change_minutes(
            payload.get('newStart'), payload.get('newEnd')
        )
        if kind == 'change':
            if shift_date_obj is None:
                raise ValueError("shift_date is required")
            new_date_raw = payload.get('newDate')
            new_date_obj = (
                self._normalize_schedule_date(new_date_raw) if new_date_raw else shift_date_obj
            )
            day_shift = (new_date_obj - shift_date_obj).days
            # Начало всегда относится к новому дню, конец может уйти за полночь.
            # «01:00» в координаты старого куска здесь НЕ поднимаем, как это
            # делает сокращение: перенос на раннее утро и хвост ночной смены
            # иначе не различить. Хвост просят сокращением либо следующим днём.
            if day_shift == 0 and new_start_min == seg_start_min and new_end_min == seg_end_min:
                raise ValueError("Смена не изменилась")
            return {
                'segment': {
                    'start': _minutes_to_time(seg_start_min),
                    'end': _minutes_to_time(seg_end_min),
                    'startMin': seg_start_min,
                    'endMin': seg_end_min,
                },
                'newDate': new_date_obj.strftime('%Y-%m-%d'),
                'dayShift': day_shift,
                'newStart': _minutes_to_time(new_start_min),
                'newEnd': _minutes_to_time(new_end_min),
                'newStartMin': new_start_min,
                'newEndMin': new_end_min,
            }
        # Ночная смена: кусок 17:00-02:00 приезжает как 1020-1560, а новые
        # границы оператор выбирает из того же списка времён, поэтому 01:00
        # доедет как 60. Поднимаем их в систему координат самого куска.
        if new_start_min < seg_start_min and new_start_min + 1440 <= seg_end_min:
            new_start_min += 1440
            new_end_min += 1440
        while new_end_min <= new_start_min:
            new_end_min += 1440
        if new_start_min < seg_start_min or new_end_min > seg_end_min:
            raise ValueError("Новые границы должны быть внутри смены")
        if new_start_min == seg_start_min and new_end_min == seg_end_min:
            raise ValueError("Границы смены не изменились")
        return {
            'segment': {
                'start': _minutes_to_time(seg_start_min),
                'end': _minutes_to_time(seg_end_min),
                'startMin': seg_start_min,
                'endMin': seg_end_min,
            },
            'newStart': _minutes_to_time(new_start_min),
            'newEnd': _minutes_to_time(new_end_min),
            'newStartMin': new_start_min,
            'newEndMin': new_end_min,
        }

    def _validate_shift_change_against_schedule(self, kind, payload, window_intervals):
        """Сверить заявку с ГРАФИКОМ НА СЕЙЧАС. Зовётся дважды: при подаче и при согласовании.

        Между подачей и решением руководителя смену могли подвинуть, обменять
        или стереть отпуском. Принять заявку «вслепую» значило бы вписать в
        график интервал, которого оператор уже не просил.
        """
        if kind == 'extra':
            start_min = int(payload['startMin'])
            end_min = int(payload['endMin'])
            if self._swap_extract_interval(window_intervals, start_min, end_min):
                raise ValueError("В это время у оператора уже есть смена")
            return
        if kind != 'shorten':
            # Перенос и выходной сверяются своими функциями ниже: молча принять
            # их здесь за сокращение значило бы проверить не то, что просили.
            raise ValueError("Неизвестный вид заявки")

        seg = payload['segment']
        if not self._swap_is_interval_fully_covered(
            window_intervals, int(seg['startMin']), int(seg['endMin'])
        ):
            raise ValueError("Смена изменилась — заявка больше не соответствует графику")

    def _shift_change_pending_conflict(self, kind, pending_kinds):
        """Мешает ли новой заявке вида kind уже ожидающая заявка на тот же день.

        pending_kinds — виды ожидающих заявок оператора на этот день.
        """
        pending = {str(item) for item in (pending_kinds or ())}
        if not pending:
            return False
        if kind in pending:
            return True
        exclusive = set(self.SHIFT_CHANGE_EXCLUSIVE_KINDS)
        return kind in exclusive or bool(pending & exclusive)

    def _shift_change_day_pieces(self, day_shifts):
        """Смены одного дня кусками — так, как их видит оператор в «Моих сменах».

        Соприкасающиеся строки слиты, минуты считаются от полуночи этого дня,
        конец ночной смены уходит за 1440.
        """
        intervals = []
        for seg in (day_shifts or []):
            if not isinstance(seg, dict) or not seg.get('start') or not seg.get('end'):
                continue
            start_min, end_min = self._schedule_interval_minutes(seg['start'], seg['end'])
            if int(end_min) > int(start_min):
                intervals.append({'start': int(start_min), 'end': int(end_min)})
        return [
            {
                'start': _minutes_to_time(item['start']),
                'end': _minutes_to_time(item['end']),
                'startMin': int(item['start']),
                'endMin': int(item['end']),
            }
            for item in self._merge_break_intervals(intervals)
        ]

    def _shift_change_started(self, day_obj, start_min, now):
        """Наступило ли уже время start_min (минуты от полуночи дня day_obj).

        now — «сейчас» по Алматы без пояса. Одобрение бывает и через сутки после
        подачи; снять или увезти смену, которая уже идёт или прошла, значит
        обнулить отработанные часы — они считаются как пересечение смены со статусами.
        """
        start_dt = datetime.combine(day_obj, dt_time(0, 0)) + timedelta(minutes=int(start_min))
        return start_dt <= now

    def _shift_change_target_date(self, shift_date_obj, payload):
        """День, в который переезжает смена по заявке-переносу."""
        raw = (payload or {}).get('newDate')
        return self._normalize_schedule_date(raw) if raw else shift_date_obj

    def _validate_shift_change_move(self, shift_date_obj, payload, day_map, now):
        """Сверить перенос с ГРАФИКОМ НА СЕЙЧАС — при подаче и при согласовании.

        day_map — смены оператора по дням ('YYYY-MM-DD' -> смены), как их отдаёт
        _load_operator_shift_map_for_period_tx: нужны день заявки, новый день и
        по соседнему дню с каждой его стороны.
        """
        seg = payload['segment']
        seg_start = int(seg['startMin'])
        seg_end = int(seg['endMin'])
        # Точное совпадение, а не «кусок покрыт графиком», как у сокращения:
        # перенос вычитает старый кусок целиком, и если смену успели продлить
        # или сдвинуть, от неё остался бы обрубок.
        if not any(
            piece['startMin'] == seg_start and piece['endMin'] == seg_end
            for piece in self._shift_change_day_pieces(
                day_map.get(shift_date_obj.strftime('%Y-%m-%d'))
            )
        ):
            raise ValueError("Смена изменилась — заявка больше не соответствует графику")
        if self._shift_change_started(shift_date_obj, seg_start, now):
            raise ValueError("Смена уже началась — перенести её нельзя")

        target_date_obj = self._shift_change_target_date(shift_date_obj, payload)
        new_start = int(payload['newStartMin'])
        new_end = int(payload['newEndMin'])
        if self._shift_change_started(target_date_obj, new_start, now):
            raise ValueError("Новое время смены уже прошло")

        # Остальные смены вокруг нового дня — в минутах от его полуночи. Сам
        # переносимый кусок в них не входит: сдвиг на пару часов законно
        # пересекается со своим прежним местом.
        occupied = []
        for offset in (-1, 0, 1):
            neighbour = target_date_obj + timedelta(days=offset)
            for piece in self._shift_change_day_pieces(
                day_map.get(neighbour.strftime('%Y-%m-%d'))
            ):
                if (
                    neighbour == shift_date_obj
                    and piece['startMin'] == seg_start
                    and piece['endMin'] == seg_end
                ):
                    continue
                occupied.append({
                    'start': piece['startMin'] + offset * 1440,
                    'end': piece['endMin'] + offset * 1440,
                })
        if self._swap_extract_interval(occupied, new_start, new_end):
            raise ValueError("В это время у оператора уже есть смена")

    def _shift_change_day_off_payload(self, day_shifts):
        """Снимок смен дня для заявки на выходной: что именно снимется.

        Берётся из графика, а не от клиента, и хранится в заявке — руководитель
        видит цену решения, а одобрение сверяет, что день с тех пор не менялся.
        """
        pieces = self._shift_change_day_pieces(day_shifts)
        return {
            'shifts': pieces,
            'totalMin': sum(piece['endMin'] - piece['startMin'] for piece in pieces),
        }

    def _validate_shift_change_day_off(self, shift_date_obj, payload, day_shifts, now):
        """Сверить заявку на выходной с графиком на сейчас. День берётся по
        строкам смен ЭТОГО дня, а не по окну: хвост вчерашней ночной смены
        принадлежит вчерашнему дню и выходным не снимается."""
        current = sorted(
            (piece['startMin'], piece['endMin'])
            for piece in self._shift_change_day_pieces(day_shifts)
        )
        if not current:
            raise ValueError("В этот день смен нет")
        snapshot = sorted(
            (int(item.get('startMin') or 0), int(item.get('endMin') or 0))
            for item in ((payload or {}).get('shifts') or [])
            if isinstance(item, dict)
        )
        if snapshot != current:
            raise ValueError("Смена изменилась — заявка больше не соответствует графику")
        if self._shift_change_started(shift_date_obj, current[0][0], now):
            raise ValueError("Смена уже началась — выходной на этот день взять нельзя")

    def _load_shift_change_move_map_tx(self, cursor, operator_id, shift_date_obj, payload):
        """Смены оператора для сверки переноса: от дня до старого куска и нового
        дня до дня после — одним запросом."""
        target_date_obj = self._shift_change_target_date(shift_date_obj, payload)
        return self._load_operator_shift_map_for_period_tx(
            cursor=cursor,
            operator_id=operator_id,
            start_date_obj=min(shift_date_obj, target_date_obj) - timedelta(days=1),
            end_date_obj=max(shift_date_obj, target_date_obj) + timedelta(days=1)
        )

    def _apply_shift_change_move_tx(self, cursor, operator_id, shift_date_obj, payload, actor_id):
        """Перенести кусок смены по одобренной заявке. Возвращает затронутые даты.

        Пишем точечно: строки старого куска удаляются, новый кусок сохраняет
        _save_shift_tx. Остальные смены обоих дней не перезаписываются — их вид
        и перерывы остаются как были.
        """
        operator_id = int(operator_id)
        now = self._almaty_now()
        day_map = self._load_shift_change_move_map_tx(cursor, operator_id, shift_date_obj, payload)
        self._validate_shift_change_move(shift_date_obj, payload, day_map, now.replace(tzinfo=None))

        target_date_obj = self._shift_change_target_date(shift_date_obj, payload)
        affected_dates = sorted({shift_date_obj, target_date_obj})
        # Перерывы уезжают из базы каскадом вместе со строкой смены, поэтому
        # снимок нового дня снимаем до удаления старого куска.
        target_breaks_snapshot = self._load_day_shift_breaks_tx(cursor, operator_id, target_date_obj)
        audit_actor = self._schedule_change_actor(
            cursor, actor_id=actor_id, source='shift_request'
        )
        audit_before = self._snapshot_schedule_days_tx(
            cursor, [(operator_id, day_obj) for day_obj in affected_dates]
        )

        seg = payload['segment']
        seg_start = int(seg['startMin'])
        seg_end = int(seg['endMin'])
        cursor.execute(
            """
            SELECT id, start_time, end_time, shift_type
            FROM work_shifts
            WHERE operator_id = %s AND shift_date = %s
            ORDER BY start_time
            """,
            (operator_id, shift_date_obj)
        )
        piece_ids = []
        piece_type = None
        for shift_id, row_start, row_end, row_type in (cursor.fetchall() or []):
            row_start_min, row_end_min = self._schedule_interval_minutes(row_start, row_end)
            if row_start_min < seg_start or row_end_min > seg_end:
                continue
            piece_ids.append(int(shift_id))
            # Кусок бывает слит из нескольких строк — вид берём самый
            # специфичный, как при слиянии для показа (_merge_shifts_for_date).
            if piece_type is None or (
                _work_shift_type_priority(row_type) > _work_shift_type_priority(piece_type)
            ):
                piece_type = row_type
        if not piece_ids:
            raise ValueError("Смена изменилась — заявка больше не соответствует графику")
        cursor.execute("DELETE FROM work_shifts WHERE id = ANY(%s)", (piece_ids,))

        # Свой INSERT в work_shifts здесь был бы дырой: именно _save_shift_tx
        # раскладывает перерывы по правилам направления и снимает выходной.
        self._save_shift_tx(
            cursor=cursor,
            operator_id=operator_id,
            shift_date=target_date_obj,
            start_time=self._normalize_schedule_time(payload['newStart'], 'start_time'),
            end_time=self._normalize_schedule_time(payload['newEnd'], 'end_time'),
            shift_type=piece_type,
            breaks=None,
            now=now,
            day_breaks_snapshot=target_breaks_snapshot
        )

        # День, из которого смена уехала целиком, — выходной, а не пустая
        # ячейка: пустая читается как «график ещё не составили» (задача #37).
        if target_date_obj != shift_date_obj:
            cursor.execute(
                "SELECT 1 FROM work_shifts WHERE operator_id = %s AND shift_date = %s LIMIT 1",
                (operator_id, shift_date_obj)
            )
            if not cursor.fetchone():
                self._set_day_off_tx(cursor, operator_id, shift_date_obj)

        self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)
        for day_obj in affected_dates:
            self._recalculate_auto_daily_hours_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=day_obj,
                end_date=day_obj
            )
        return affected_dates

    def _apply_shift_change_day_off_tx(self, cursor, operator_id, shift_date_obj, payload, actor_id):
        """Поставить выходной по одобренной заявке. Трогает только день заявки."""
        operator_id = int(operator_id)
        day_map = self._load_operator_shift_map_for_period_tx(
            cursor=cursor,
            operator_id=operator_id,
            start_date_obj=shift_date_obj,
            end_date_obj=shift_date_obj
        )
        self._validate_shift_change_day_off(
            shift_date_obj,
            payload,
            day_map.get(shift_date_obj.strftime('%Y-%m-%d')),
            self._almaty_now().replace(tzinfo=None)
        )
        audit_actor = self._schedule_change_actor(
            cursor, actor_id=actor_id, source='shift_request'
        )
        audit_before = self._snapshot_schedule_days_tx(cursor, [(operator_id, shift_date_obj)])
        self._set_day_off_tx(cursor, operator_id, shift_date_obj)
        self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)
        self._recalculate_auto_daily_hours_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=shift_date_obj,
            end_date=shift_date_obj
        )
        return [shift_date_obj]

    def _apply_shift_change_request_tx(self, cursor, operator_id, shift_date_obj,
                                       kind, payload, actor_id):
        """Вписать одобренную заявку в график. Возвращает список затронутых дат.

        Сокращение и доп. часть пишем ровно тем же путём, что и обмен сменами:
        снимок дня до правки, очистка затронутых дней, _save_shift_tx на каждый
        кусок, запись в историю изменений. Свой INSERT в work_shifts здесь был
        бы дырой — именно _save_shift_tx чистит пересечения, раскладывает
        перерывы по правилам направления и снимает выходной. Перенос и выходной
        уходят в свои функции: им перезапись окна противопоказана.
        """
        if kind == 'change':
            return self._apply_shift_change_move_tx(
                cursor, operator_id, shift_date_obj, payload, actor_id
            )
        if kind == 'day_off':
            return self._apply_shift_change_day_off_tx(
                cursor, operator_id, shift_date_obj, payload, actor_id
            )
        day_map, window = self._load_shift_change_window_tx(cursor, operator_id, shift_date_obj)
        self._validate_shift_change_against_schedule(kind, payload, window)

        if kind == 'extra':
            next_intervals = self._merge_break_intervals([
                *window,
                {'start': int(payload['startMin']), 'end': int(payload['endMin'])},
            ])
        else:
            seg = payload['segment']
            next_intervals = self._merge_break_intervals([
                *self._swap_subtract_intervals(
                    window, [{'start': int(seg['startMin']), 'end': int(seg['endMin'])}]
                ),
                {'start': int(payload['newStartMin']), 'end': int(payload['newEndMin'])},
            ])

        window_offsets = [-1, 0, 1]
        save_day_map = self._swap_serialize_intervals_to_day_map(
            next_intervals, shift_date_obj, allowed_day_offsets=window_offsets
        )

        def _signature(shifts):
            sign = []
            for seg_item in (shifts or []):
                if not isinstance(seg_item, dict):
                    continue
                if not seg_item.get('start') or not seg_item.get('end'):
                    continue
                sign.append(f"{seg_item['start']}|{seg_item['end']}")
            sign.sort()
            return sign

        affected_dates = []
        day_off_dates = []
        for offset in window_offsets:
            day_obj = shift_date_obj + timedelta(days=offset)
            day_key = day_obj.strftime('%Y-%m-%d')
            before_shifts = day_map.get(day_key) or []
            after_shifts = save_day_map.get(day_key) or []
            if _signature(before_shifts) != _signature(after_shifts):
                affected_dates.append(day_obj)
                # День опустел — ставим выходной, а не пустую ячейку: пустая
                # читается как «график ещё не составили» (задача #37).
                if before_shifts and not after_shifts:
                    day_off_dates.append(day_obj)
        if not affected_dates:
            return []

        now = self._almaty_now()
        breaks_snapshots = {}
        for day_obj in affected_dates:
            breaks_snapshots[(int(operator_id), day_obj.strftime('%Y-%m-%d'))] = (
                self._load_day_shift_breaks_tx(cursor, operator_id, day_obj)
            )

        audit_actor = self._schedule_change_actor(
            cursor, actor_id=actor_id, source='shift_request'
        )
        audit_before = self._snapshot_schedule_days_tx(
            cursor, [(int(operator_id), day_obj) for day_obj in affected_dates]
        )

        for day_obj in affected_dates:
            self._clear_day_schedule_tx(cursor, operator_id, day_obj)

        direction_keys = {}
        for day_obj in affected_dates:
            day_key = day_obj.strftime('%Y-%m-%d')
            for seg_item in (save_day_map.get(day_key) or []):
                self._save_shift_tx(
                    cursor=cursor,
                    operator_id=operator_id,
                    shift_date=day_obj,
                    start_time=self._normalize_schedule_time(seg_item.get('start'), 'start_time'),
                    end_time=self._normalize_schedule_time(seg_item.get('end'), 'end_time'),
                    shift_type=seg_item.get('shift_type'),
                    breaks=None,
                    now=now,
                    day_breaks_snapshot=breaks_snapshots.get((int(operator_id), day_key)),
                    extra_occupied=self._extra_occupied_from_day_snapshots(
                        cursor=cursor,
                        day_breaks_snapshots=breaks_snapshots,
                        operator_id=operator_id,
                        shift_date=day_obj,
                        now=now,
                        direction_key_cache=direction_keys
                    )
                )

        for day_obj in day_off_dates:
            self._set_day_off_tx(cursor, operator_id, day_obj)

        self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)
        self._recalculate_auto_daily_hours_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=min(affected_dates),
            end_date=max(affected_dates)
        )
        return affected_dates

    def _serialize_shift_change_request_row(self, row):
        if not row:
            return None
        (
            request_id, operator_id, operator_name, department_id, department_name,
            supervisor_id, supervisor_name, request_kind, shift_date, payload_json,
            status, request_comment, response_comment, reviewed_by, reviewed_by_name,
            reviewed_at, operator_seen_at, created_at, updated_at, direction_name,
            group_id, group_name, group_supervisor_id, group_supervisor_name
        ) = row

        payload = payload_json if isinstance(payload_json, dict) else {}

        def _fmt_dt(value):
            return value.isoformat() if isinstance(value, datetime) else None

        kind = str(request_kind or '')
        # Итоговый интервал считаем на сервере: клиентов у заявки трое
        # (свой список оператора, очередь руководителя, колокол), и три копии
        # арифметики «сколько часов уходит» разошлись бы на ночных сменах.
        if kind == 'extra':
            before_min = 0
            after_min = max(0, int(payload.get('endMin') or 0) - int(payload.get('startMin') or 0))
        elif kind == 'day_off':
            # Снимаются все смены дня: «было» — их сумма из снимка, «станет» — ноль.
            before_min = max(0, int(payload.get('totalMin') or 0))
            after_min = 0
        else:
            # shorten и change: кусок смены и его новые границы.
            seg = payload.get('segment') if isinstance(payload.get('segment'), dict) else {}
            before_min = max(0, int(seg.get('endMin') or 0) - int(seg.get('startMin') or 0))
            after_min = max(0, int(payload.get('newEndMin') or 0) - int(payload.get('newStartMin') or 0))

        return {
            'id': int(request_id),
            'operator': {
                'id': int(operator_id),
                'name': operator_name,
                'direction': direction_name,
            },
            'department': {
                'id': int(department_id) if department_id is not None else None,
                'name': department_name,
            },
            'supervisor': {
                'id': int(supervisor_id) if supervisor_id is not None else None,
                'name': supervisor_name,
            },
            # Группа оператора на дату смены и её СВ — не снимок на момент
            # подачи, как supervisor выше: очередь делится по группам сейчас.
            'group': {
                'id': int(group_id) if group_id is not None else None,
                'name': group_name,
                'supervisorId': int(group_supervisor_id) if group_supervisor_id is not None else None,
                'supervisorName': group_supervisor_name,
            },
            'kind': kind,
            'shiftDate': shift_date.strftime('%Y-%m-%d') if isinstance(shift_date, date) else str(shift_date),
            'payload': payload,
            'status': str(status),
            'requestComment': request_comment,
            'responseComment': response_comment,
            'reviewedBy': {
                'id': int(reviewed_by) if reviewed_by is not None else None,
                'name': reviewed_by_name,
            },
            'reviewedAt': _fmt_dt(reviewed_at),
            'operatorSeenAt': _fmt_dt(operator_seen_at),
            'createdAt': _fmt_dt(created_at),
            'updatedAt': _fmt_dt(updated_at),
            'minutesBefore': before_min,
            'minutesAfter': after_min,
            'minutesDelta': after_min - before_min,
        }

    def _select_shift_change_request_by_id_tx(self, cursor, request_id):
        cursor.execute(
            self.SHIFT_CHANGE_REQUEST_SELECT + " WHERE r.id = %s",
            (int(request_id),)
        )
        return cursor.fetchone()

    def create_shift_change_request(self, operator_id, request_kind, shift_date,
                                    payload, request_comment=None):
        """Подать заявку по своей смене: сократить, добавить часть, перенести или взять выходной."""
        operator_id = int(operator_id)
        kind = self._normalize_shift_change_kind(request_kind)
        shift_date_obj = self._normalize_schedule_date(shift_date)
        normalized_payload = self._normalize_shift_change_payload(kind, payload, shift_date_obj)

        comment = str(request_comment or '').strip() or None

        now = self._almaty_now()
        today = now.date()
        if shift_date_obj < today:
            raise ValueError("Прошедшую смену изменить нельзя")
        if (shift_date_obj - today).days > self.SHIFT_CHANGE_REQUEST_MAX_AHEAD_DAYS:
            raise ValueError(
                f"Смена слишком далеко: заявки принимаются на "
                f"{self.SHIFT_CHANGE_REQUEST_MAX_AHEAD_DAYS} дней вперёд"
            )
        if kind == 'change':
            # Новый день живёт в том же окне, что и день заявки.
            target_date_obj = self._shift_change_target_date(shift_date_obj, normalized_payload)
            if target_date_obj < today:
                raise ValueError("Перенести смену на прошедший день нельзя")
            if (target_date_obj - today).days > self.SHIFT_CHANGE_REQUEST_MAX_AHEAD_DAYS:
                raise ValueError(
                    f"Новый день слишком далеко: смену можно перенести не дальше чем на "
                    f"{self.SHIFT_CHANGE_REQUEST_MAX_AHEAD_DAYS} дней вперёд"
                )

        with self._get_cursor() as cursor:
            cursor.execute("SELECT id, role FROM users WHERE id = %s", (operator_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("Оператор не найден")
            if normalize_role_value(row[1]) != 'operator':
                raise ValueError("Заявку на изменение смены подаёт оператор")

            # Один незакрытый запрос на день и вид. Второй такой же — это не
            # новая просьба, а повтор: руководитель увидел бы в очереди две
            # строки об одном и том же и не понял бы, какую разбирать.
            # Перенос и выходной не уживаются и с заявкой другого вида на тот же
            # день (SHIFT_CHANGE_EXCLUSIVE_KINDS); сокращение с доп. частью
            # вместе подавать можно, как и раньше.
            cursor.execute(
                """
                SELECT request_kind FROM work_shift_change_requests
                WHERE operator_id = %s AND shift_date = %s AND status = 'pending'
                """,
                (operator_id, shift_date_obj)
            )
            if self._shift_change_pending_conflict(
                kind, {str(row[0]) for row in (cursor.fetchall() or [])}
            ):
                raise ValueError("По этому дню уже есть заявка на рассмотрении")

            if kind == 'change':
                self._validate_shift_change_move(
                    shift_date_obj,
                    normalized_payload,
                    self._load_shift_change_move_map_tx(
                        cursor, operator_id, shift_date_obj, normalized_payload
                    ),
                    now.replace(tzinfo=None)
                )
            elif kind == 'day_off':
                day_shifts = self._load_operator_shift_map_for_period_tx(
                    cursor=cursor,
                    operator_id=operator_id,
                    start_date_obj=shift_date_obj,
                    end_date_obj=shift_date_obj
                ).get(shift_date_obj.strftime('%Y-%m-%d'))
                normalized_payload = self._shift_change_day_off_payload(day_shifts)
                self._validate_shift_change_day_off(
                    shift_date_obj, normalized_payload, day_shifts, now.replace(tzinfo=None)
                )
            else:
                _day_map, window = self._load_shift_change_window_tx(cursor, operator_id, shift_date_obj)
                if kind == 'shorten' and not window:
                    raise ValueError("В этот день смен нет")
                self._validate_shift_change_against_schedule(kind, normalized_payload, window)

            # Отдел берём этим же курсором. get_user_department_id открыл бы
            # ВТОРОЕ соединение, пока первое ещё держит транзакцию, а пул тут
            # общий с SSE аукциона и циклом бота.
            cursor.execute("SELECT department_id FROM users WHERE id = %s", (operator_id,))
            dep_row = cursor.fetchone()
            department_id = dep_row[0] if dep_row else None
            group_id = self._get_operator_group_id_tx(cursor, operator_id, shift_date_obj)
            supervisor_id = self._group_active_supervisor_id_tx(cursor, group_id) if group_id else None

            cursor.execute(
                """
                INSERT INTO work_shift_change_requests
                    (operator_id, department_id, supervisor_id, request_kind,
                     shift_date, payload_json, request_comment)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (operator_id, department_id, supervisor_id, kind,
                 shift_date_obj, json.dumps(normalized_payload, ensure_ascii=False), comment)
            )
            new_id = cursor.fetchone()[0]
            return self._serialize_shift_change_request_row(
                self._select_shift_change_request_by_id_tx(cursor, new_id)
            )

    def get_shift_change_requests_for_operator(self, operator_id, limit=100):
        """Свои заявки оператора, свежие сверху."""
        operator_id = int(operator_id)
        limit = max(1, min(int(limit or 100), 500))
        with self._get_cursor() as cursor:
            cursor.execute(
                self.SHIFT_CHANGE_REQUEST_SELECT
                + " WHERE r.operator_id = %s ORDER BY r.created_at DESC, r.id DESC LIMIT %s",
                (operator_id, limit)
            )
            rows = cursor.fetchall() or []
        items = [self._serialize_shift_change_request_row(row) for row in rows]
        return {
            'requests': items,
            'pendingCount': sum(1 for it in items if it['status'] == 'pending'),
        }

    def get_shift_change_requests_for_review(self, operator_ids, statuses=None,
                                             start_date=None, end_date=None, limit=300):
        """Очередь «Запросы» для руководителя: заявки операторов из его периметра.

        Периметр приходит СПИСКОМ id, а не ролью: границу отдела считает
        bot_schedule2._filter_operators_for_requester_scope, и второй копии
        этого правила в SQL быть не должно.
        """
        ids = [int(v) for v in (operator_ids or []) if v is not None]
        if not ids:
            return {'requests': [], 'pendingCount': 0}

        limit = max(1, min(int(limit or 300), 1000))
        conditions = ["r.operator_id = ANY(%(ids)s)"]
        params = {'ids': ids, 'limit': limit}

        status_list = [
            str(s).strip().lower() for s in (statuses or [])
            if str(s).strip().lower() in self.SHIFT_CHANGE_REQUEST_STATUSES
        ]
        if status_list:
            conditions.append("r.status = ANY(%(statuses)s)")
            params['statuses'] = status_list
        if start_date:
            conditions.append("r.shift_date >= %(start_date)s")
            params['start_date'] = self._normalize_schedule_date(start_date)
        if end_date:
            conditions.append("r.shift_date <= %(end_date)s")
            params['end_date'] = self._normalize_schedule_date(end_date)

        with self._get_cursor() as cursor:
            cursor.execute(
                self.SHIFT_CHANGE_REQUEST_SELECT
                + " WHERE " + " AND ".join(conditions)
                # Ожидающие всегда сверху: очередь существует ради них, а
                # разобранные заявки в ней — только история.
                + " ORDER BY (r.status = 'pending') DESC, r.shift_date ASC,"
                  " r.created_at ASC, r.id ASC LIMIT %(limit)s",
                params
            )
            rows = cursor.fetchall() or []
            cursor.execute(
                """
                SELECT COUNT(*) FROM work_shift_change_requests
                WHERE operator_id = ANY(%s) AND status = 'pending'
                """,
                (ids,)
            )
            pending_total = int((cursor.fetchone() or [0])[0])

        return {
            'requests': [self._serialize_shift_change_request_row(row) for row in rows],
            'pendingCount': pending_total,
        }

    def respond_shift_change_request(self, request_id, reviewer_id, action,
                                     response_comment=None):
        """Согласовать, отклонить или отозвать заявку.

        `approve` сразу правит график — это и есть требование задачи #17
        («при одобрении смена должна проставиться автоматически»). Отзыв
        (`cancel`) доступен только автору и график не трогает.
        """
        request_id = int(request_id)
        reviewer_id = int(reviewer_id)
        action_norm = str(action or '').strip().lower()
        if action_norm not in ('approve', 'reject', 'cancel'):
            raise ValueError("action must be 'approve', 'reject' or 'cancel'")

        comment = str(response_comment or '').strip() or None

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, operator_id, request_kind, shift_date, payload_json, status
                FROM work_shift_change_requests
                WHERE id = %s
                FOR UPDATE
                """,
                (request_id,)
            )
            locked = cursor.fetchone()
            if not locked:
                raise ValueError("Заявка не найдена")
            _lid, operator_id, request_kind, shift_date_obj, payload_raw, current_status = locked
            operator_id = int(operator_id)
            if str(current_status) != 'pending':
                raise ValueError(f"Заявка уже обработана (статус: {current_status})")

            if action_norm == 'cancel':
                if reviewer_id != operator_id:
                    raise ValueError("Отозвать заявку может только её автор")
                cursor.execute(
                    """
                    UPDATE work_shift_change_requests
                    SET status = 'cancelled',
                        response_comment = %s,
                        reviewed_by = %s,
                        reviewed_at = CURRENT_TIMESTAMP,
                        -- Своё же действие оператору не показываем как новость.
                        operator_seen_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (comment, reviewer_id, request_id)
                )
                return self._serialize_shift_change_request_row(
                    self._select_shift_change_request_by_id_tx(cursor, request_id)
                )

            if reviewer_id == operator_id:
                raise ValueError("Свою заявку согласовать нельзя")

            if action_norm == 'reject':
                cursor.execute(
                    """
                    UPDATE work_shift_change_requests
                    SET status = 'rejected',
                        response_comment = %s,
                        reviewed_by = %s,
                        reviewed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (comment, reviewer_id, request_id)
                )
                return self._serialize_shift_change_request_row(
                    self._select_shift_change_request_by_id_tx(cursor, request_id)
                )

            kind = self._normalize_shift_change_kind(request_kind)
            payload = payload_raw if isinstance(payload_raw, dict) else {}
            self._apply_shift_change_request_tx(
                cursor=cursor,
                operator_id=operator_id,
                shift_date_obj=shift_date_obj,
                kind=kind,
                payload=payload,
                actor_id=reviewer_id
            )
            cursor.execute(
                """
                UPDATE work_shift_change_requests
                SET status = 'approved',
                    response_comment = %s,
                    reviewed_by = %s,
                    reviewed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (comment, reviewer_id, request_id)
            )
            return self._serialize_shift_change_request_row(
                self._select_shift_change_request_by_id_tx(cursor, request_id)
            )

    def mark_shift_change_requests_seen(self, operator_id):
        """Погасить у оператора уведомления о разобранных заявках."""
        operator_id = int(operator_id)
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE work_shift_change_requests
                SET operator_seen_at = CURRENT_TIMESTAMP
                WHERE operator_id = %s
                  AND status IN ('approved', 'rejected')
                  AND operator_seen_at IS NULL
                """,
                (operator_id,)
            )
            return cursor.rowcount or 0

    def get_shift_change_notify_optin(self, user_id):
        """Подписан ли глава отдела на уведомления о заявках. Нет строки — нет подписки."""
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT enabled FROM shift_change_notify_optin WHERE user_id = %s",
                (int(user_id),)
            )
            row = cursor.fetchone()
            return bool(row[0]) if row else False

    def set_shift_change_notify_optin(self, user_id, enabled):
        """Включить/выключить подписку. Выключение удаляет строку, а не хранит
        FALSE: «не подписан» — одно состояние, и двух его записей быть не должно."""
        user_id = int(user_id)
        with self._get_cursor() as cursor:
            if enabled:
                cursor.execute(
                    """
                    INSERT INTO shift_change_notify_optin (user_id, enabled)
                    VALUES (%s, TRUE)
                    ON CONFLICT (user_id) DO UPDATE
                        SET enabled = TRUE, updated_at = CURRENT_TIMESTAMP
                    """,
                    (user_id,)
                )
            else:
                cursor.execute(
                    "DELETE FROM shift_change_notify_optin WHERE user_id = %s",
                    (user_id,)
                )
            return bool(enabled)

    def get_shift_change_request_operator(self, request_id):
        """Чью смену просят изменить. Нужен роуту, чтобы проверить периметр
        согласующего ДО того, как заявка будет тронута."""
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT operator_id FROM work_shift_change_requests WHERE id = %s",
                (int(request_id),)
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else None

    def get_shift_change_request_operator_ids(self, statuses=None,
                                              start_date=None, end_date=None):
        """Кандидаты для очереди «Запросы»: у кого вообще есть заявки в периоде.

        Отдаём голые id, потому что границу отдела считает вызывающая сторона
        (_filter_operators_for_requester_scope). Так же устроена история
        изменений графика — одно правило видимости на весь раздел.
        """
        conditions = []
        params = {}
        status_list = [
            str(s).strip().lower() for s in (statuses or [])
            if str(s).strip().lower() in self.SHIFT_CHANGE_REQUEST_STATUSES
        ]
        if status_list:
            conditions.append("status = ANY(%(statuses)s)")
            params['statuses'] = status_list
        if start_date:
            conditions.append("shift_date >= %(start_date)s")
            params['start_date'] = self._normalize_schedule_date(start_date)
        if end_date:
            conditions.append("shift_date <= %(end_date)s")
            params['end_date'] = self._normalize_schedule_date(end_date)

        where_sql = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT operator_id FROM work_shift_change_requests" + where_sql,
                params
            )
            return [int(row[0]) for row in (cursor.fetchall() or []) if row[0] is not None]

    def _pair_shift_changes(self, removed, added):
        """Сопоставить снятые и появившиеся смены. Пара с наибольшим пересечением —
        это одна и та же смена, которой подвинули время, а не «удалили и добавили»:
        именно так изменение видит человек, который смотрит в график."""
        scored = []
        for removed_index, removed_item in enumerate(removed):
            removed_start, removed_end = self._schedule_interval_minutes(removed_item[0], removed_item[1])
            for added_index, added_item in enumerate(added):
                added_start, added_end = self._schedule_interval_minutes(added_item[0], added_item[1])
                overlap = min(removed_end, added_end) - max(removed_start, added_start)
                if overlap <= 0:
                    continue
                scored.append((-overlap, removed_index, added_index))
        scored.sort()

        used_removed = set()
        used_added = set()
        pairs = []
        for _, removed_index, added_index in scored:
            if removed_index in used_removed or added_index in used_added:
                continue
            used_removed.add(removed_index)
            used_added.add(added_index)
            pairs.append((removed[removed_index], added[added_index]))

        rest_removed = [item for index, item in enumerate(removed) if index not in used_removed]
        rest_added = [item for index, item in enumerate(added) if index not in used_added]
        return pairs, rest_removed, rest_added

    @staticmethod
    def _serialize_work_shift_change_row(row):
        (
            entry_id, operator_id, operator_name, shift_date, action, source,
            start_time, end_time, prev_start_time, prev_end_time,
            shift_type, prev_shift_type, actor_id, actor_name, actor_role, changed_at
        ) = row

        def as_time(value):
            return value.strftime('%H:%M') if hasattr(value, 'strftime') else None

        return {
            'id': int(entry_id),
            'operatorId': int(operator_id),
            'operatorName': operator_name or '',
            'date': shift_date.strftime('%Y-%m-%d') if shift_date else None,
            'action': action,
            'source': source,
            'start': as_time(start_time),
            'end': as_time(end_time),
            'prevStart': as_time(prev_start_time),
            'prevEnd': as_time(prev_end_time),
            'shiftType': shift_type or None,
            'prevShiftType': prev_shift_type or None,
            'actorId': int(actor_id) if actor_id is not None else None,
            'actorName': actor_name or '',
            'actorRole': actor_role or '',
            'changedAt': changed_at.isoformat() if hasattr(changed_at, 'isoformat') else None
        }

    def get_work_shift_change_operator_ids(self, start_date, end_date):
        """Операторы, у которых в периоде вообще есть история. Роут пересекает их
        со своей зоной видимости — так не приходится тянуть весь список сотрудников."""
        start_obj = self._normalize_schedule_date(start_date)
        end_obj = self._normalize_schedule_date(end_date)
        if end_obj < start_obj:
            raise ValueError("end_date must be >= start_date")

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT operator_id
                FROM work_shift_changes
                WHERE shift_date BETWEEN %s AND %s
            """, (start_obj, end_obj))
            return [int(row[0]) for row in (cursor.fetchall() or []) if row[0] is not None]

    def get_work_shift_change_summary(self, operator_ids, start_date, end_date):
        """Сводка «в каком дне что-то меняли» — один запрос на весь видимый период.
        Сетке нужен только факт и последняя правка, поэтому подробности не тянем."""
        operator_ids = [int(value) for value in (operator_ids or []) if value is not None]
        if not operator_ids:
            return {}

        start_obj = self._normalize_schedule_date(start_date)
        end_obj = self._normalize_schedule_date(end_date)
        if end_obj < start_obj:
            raise ValueError("end_date must be >= start_date")

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    operator_id,
                    shift_date,
                    COUNT(*)::int AS changes_count,
                    MAX(changed_at) AS last_changed_at,
                    (array_agg(action ORDER BY changed_at DESC, id DESC))[1] AS last_action,
                    (array_agg(source ORDER BY changed_at DESC, id DESC))[1] AS last_source,
                    (array_agg(actor_name ORDER BY changed_at DESC, id DESC))[1] AS last_actor_name,
                    (array_agg(COALESCE(actor_role, '') ORDER BY changed_at DESC, id DESC))[1] AS last_actor_role
                FROM work_shift_changes
                WHERE operator_id = ANY(%s)
                  AND shift_date BETWEEN %s AND %s
                GROUP BY operator_id, shift_date
            """, (operator_ids, start_obj, end_obj))
            rows = cursor.fetchall() or []

        summary = {}
        for operator_id, shift_date, changes_count, last_changed_at, last_action, last_source, last_actor_name, last_actor_role in rows:
            key = f"{int(operator_id)}|{shift_date.strftime('%Y-%m-%d')}"
            summary[key] = {
                'count': int(changes_count or 0),
                'lastAt': last_changed_at.isoformat() if hasattr(last_changed_at, 'isoformat') else None,
                'lastAction': last_action,
                'lastSource': last_source,
                'lastActorName': last_actor_name or '',
                'lastActorRole': last_actor_role or ''
            }
        return summary

    def get_work_shift_changes(self, operator_ids, start_date, end_date, limit=200, offset=0):
        """Сами записи истории: для одной ячейки (один оператор, один день)
        или для всего периода — журналом."""
        operator_ids = [int(value) for value in (operator_ids or []) if value is not None]
        if not operator_ids:
            return {'items': [], 'total': 0}

        start_obj = self._normalize_schedule_date(start_date)
        end_obj = self._normalize_schedule_date(end_date)
        if end_obj < start_obj:
            raise ValueError("end_date must be >= start_date")

        limit = max(1, min(int(limit or 200), 500))
        offset = max(0, int(offset or 0))

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*)::int
                FROM work_shift_changes
                WHERE operator_id = ANY(%s)
                  AND shift_date BETWEEN %s AND %s
            """, (operator_ids, start_obj, end_obj))
            total_row = cursor.fetchone()
            total = int(total_row[0]) if total_row else 0

            cursor.execute("""
                SELECT
                    c.id, c.operator_id, op.name, c.shift_date, c.action, c.source,
                    c.start_time, c.end_time, c.prev_start_time, c.prev_end_time,
                    c.shift_type, c.prev_shift_type, c.actor_id,
                    COALESCE(NULLIF(c.actor_name, ''), actor.name, ''),
                    COALESCE(c.actor_role, ''),
                    c.changed_at
                FROM work_shift_changes c
                LEFT JOIN users op ON op.id = c.operator_id
                LEFT JOIN users actor ON actor.id = c.actor_id
                WHERE c.operator_id = ANY(%s)
                  AND c.shift_date BETWEEN %s AND %s
                ORDER BY c.changed_at DESC, c.id DESC
                LIMIT %s OFFSET %s
            """, (operator_ids, start_obj, end_obj, limit, offset))
            rows = cursor.fetchall() or []

        return {
            'items': [self._serialize_work_shift_change_row(row) for row in rows],
            'total': total
        }

    def save_shift(
        self,
        operator_id,
        shift_date,
        start_time,
        end_time,
        breaks=None,
        shift_type=None,
        previous_start_time=None,
        previous_end_time=None,
        actor_id=None,
        change_source='supervisor'
    ):
        """
        Сохранить смену для оператора и синхронизировать её с фронтовым merged-состоянием.
        - заменяет пересекающиеся смены на этой дате одной присланной сменой
        - обновляет/перезаписывает перерывы
        - снимает day off на эту дату, если он был
        """
        shift_date_obj = self._normalize_schedule_date(shift_date)
        start_time_obj = self._normalize_schedule_time(start_time, 'start_time')
        end_time_obj = self._normalize_schedule_time(end_time, 'end_time')

        with self._get_cursor() as cursor:
            with self._schedule_change_audit(
                cursor,
                [(int(operator_id), shift_date_obj)],
                actor_id=actor_id,
                source=change_source
            ):
                shift_id = self._save_shift_tx(
                    cursor=cursor,
                    operator_id=int(operator_id),
                    shift_date=shift_date_obj,
                    start_time=start_time_obj,
                    end_time=end_time_obj,
                    breaks=breaks,
                    shift_type=shift_type,
                    previous_start_time=previous_start_time,
                    previous_end_time=previous_end_time
                )
            self._recalculate_auto_daily_hours_tx(
                cursor=cursor,
                operator_ids=[int(operator_id)],
                start_date=shift_date_obj,
                end_date=shift_date_obj
            )
        # Перерывы на смене СВ — вычет из его часов за день: пересчитываем сразу,
        # уже после записи (пересчёт читает базу своим соединением).
        self._recalculate_supervisor_shift_day(operator_id, shift_date_obj)
        return shift_id

    def delete_shift(self, operator_id, shift_date, start_time, end_time, actor_id=None, change_source='supervisor'):
        """
        Удалить конкретную смену оператора.
        """
        shift_date_obj = self._normalize_schedule_date(shift_date)
        start_time_obj = self._normalize_schedule_time(start_time, 'start_time')
        end_time_obj = self._normalize_schedule_time(end_time, 'end_time')

        with self._get_cursor() as cursor:
            with self._schedule_change_audit(
                cursor,
                [(int(operator_id), shift_date_obj)],
                actor_id=actor_id,
                source=change_source
            ):
                cursor.execute("""
                    DELETE FROM work_shifts
                    WHERE operator_id = %s AND shift_date = %s
                      AND start_time = %s AND end_time = %s
                    RETURNING id
                """, (int(operator_id), shift_date_obj, start_time_obj, end_time_obj))
                deleted = cursor.fetchone() is not None
            if deleted:
                self._recalculate_auto_daily_hours_tx(
                    cursor=cursor,
                    operator_ids=[int(operator_id)],
                    start_date=shift_date_obj,
                    end_date=shift_date_obj
                )
        if deleted:
            self._recalculate_supervisor_shift_day(operator_id, shift_date_obj)
        return deleted

    def _recalculate_supervisor_shift_day(self, operator_id, shift_date):
        """После правки смены СВ пересчитать его часы за день: поставленные на смене
        перерывы — это вычет из часов (задача #352). Операторов не касается."""
        with self._get_cursor() as cursor:
            if not self._clockster_hours_supervisor_ids_tx(cursor, [int(operator_id)]):
                return None
        day = self._normalize_schedule_date(shift_date)
        today = datetime.now(supervisor_hours.TZ).date()
        return self.recalculate_supervisor_clockster_hours(
            day, day, [int(operator_id)], keep_break_before=today.replace(day=1))

    def _delete_all_shifts_for_day_tx(self, cursor, operator_id, shift_date):
        operator_id = int(operator_id)
        shift_date_obj = self._normalize_schedule_date(shift_date)
        cursor.execute("""
            DELETE FROM work_shifts
            WHERE operator_id = %s AND shift_date = %s
            RETURNING id
        """, (operator_id, shift_date_obj))
        deleted_rows = cursor.fetchall() or []
        return len(deleted_rows)

    def _delete_shifts_for_period_tx(self, cursor, operator_id, start_date, end_date=None, keep_days=None):
        """
        Удалить смены и выходные оператора в периоде [start_date, end_date] включительно.
        Если end_date не указан, удаляются записи начиная с start_date и далее.
        keep_days — даты, смены за которые снимать нельзя (уже отработанные дни).
        """
        operator_id = int(operator_id)
        start_date_obj = self._normalize_schedule_date(start_date)
        end_date_obj = self._normalize_schedule_date(end_date) if end_date is not None else None
        if end_date_obj is not None and end_date_obj < start_date_obj:
            raise ValueError("end_date must be >= start_date")

        keep_days_list = sorted({
            self._normalize_schedule_date(day)
            for day in (keep_days or [])
            if day is not None
        })

        if end_date_obj is None:
            cursor.execute("""
                DELETE FROM work_shifts
                WHERE operator_id = %s
                  AND shift_date >= %s
                  AND (%s::date[] IS NULL OR NOT (shift_date = ANY(%s::date[])))
                RETURNING id, shift_date
            """, (operator_id, start_date_obj, keep_days_list or None, keep_days_list or None))
        else:
            cursor.execute("""
                DELETE FROM work_shifts
                WHERE operator_id = %s
                  AND shift_date >= %s
                  AND shift_date <= %s
                  AND (%s::date[] IS NULL OR NOT (shift_date = ANY(%s::date[])))
                RETURNING id, shift_date
            """, (operator_id, start_date_obj, end_date_obj, keep_days_list or None, keep_days_list or None))

        deleted_rows = cursor.fetchall() or []
        deleted_shifts_count = len(deleted_rows)
        max_deleted_shift_date = None
        for _, shift_date_value in deleted_rows:
            if shift_date_value is None:
                continue
            if max_deleted_shift_date is None or shift_date_value > max_deleted_shift_date:
                max_deleted_shift_date = shift_date_value

        if end_date_obj is None:
            cursor.execute("""
                DELETE FROM days_off
                WHERE operator_id = %s
                  AND day_off_date >= %s
                RETURNING day_off_date
            """, (operator_id, start_date_obj))
        else:
            cursor.execute("""
                DELETE FROM days_off
                WHERE operator_id = %s
                  AND day_off_date >= %s
                  AND day_off_date <= %s
                RETURNING day_off_date
            """, (operator_id, start_date_obj, end_date_obj))

        deleted_day_off_rows = cursor.fetchall() or []
        deleted_days_off_count = len(deleted_day_off_rows)
        max_deleted_day_off_date = None
        for row in deleted_day_off_rows:
            day_off_date_value = row[0] if isinstance(row, tuple) and row else None
            if day_off_date_value is None:
                continue
            if max_deleted_day_off_date is None or day_off_date_value > max_deleted_day_off_date:
                max_deleted_day_off_date = day_off_date_value

        return {
            'deleted_shifts': deleted_shifts_count,
            'deleted_day_off_rows': deleted_days_off_count,
            'max_deleted_shift_date': max_deleted_shift_date,
            'max_deleted_day_off_date': max_deleted_day_off_date,
            'kept_shift_days': keep_days_list
        }

    def save_shifts_bulk(self, operator_id, shifts_data, actor_id=None, change_source='supervisor'):
        """
        Массовое сохранение смен для оператора.
        shifts_data: [{'date': 'YYYY-MM-DD', 'start': 'HH:MM', 'end': 'HH:MM', 'breaks': [...]}, ...]
        """
        operator_id = int(operator_id)
        if not isinstance(shifts_data, list):
            raise ValueError("shifts must be a list")

        result_ids = []
        min_date_obj = None
        max_date_obj = None
        with self._get_cursor() as cursor:
            audit_actor = self._schedule_change_actor(cursor, actor_id=actor_id, source=change_source)
            audit_before = self._snapshot_schedule_days_tx(cursor, [
                (operator_id, shift.get('date'))
                for shift in shifts_data
                if isinstance(shift, dict)
            ])

            for shift in shifts_data:
                if not isinstance(shift, dict):
                    raise ValueError("Each shift must be an object")
                shift_date_obj = self._normalize_schedule_date(shift.get('date'))
                if min_date_obj is None or shift_date_obj < min_date_obj:
                    min_date_obj = shift_date_obj
                if max_date_obj is None or shift_date_obj > max_date_obj:
                    max_date_obj = shift_date_obj
                start_time_obj = self._normalize_schedule_time(shift.get('start'), 'start')
                end_time_obj = self._normalize_schedule_time(shift.get('end'), 'end')
                shift_id = self._save_shift_tx(
                    cursor=cursor,
                    operator_id=operator_id,
                    shift_date=shift_date_obj,
                    start_time=start_time_obj,
                    end_time=end_time_obj,
                    breaks=shift.get('breaks'),
                    shift_type=shift.get('shift_type')
                )
                result_ids.append(shift_id)

            self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)

            if min_date_obj is not None and max_date_obj is not None:
                self._recalculate_auto_daily_hours_tx(
                    cursor=cursor,
                    operator_ids=[operator_id],
                    start_date=min_date_obj,
                    end_date=max_date_obj
                )

        return result_ids
