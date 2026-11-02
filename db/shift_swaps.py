"""База данных: обмен сменами.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _merge_shifts_for_date,
    _minutes_to_time,
    _time_to_minutes,
    date,
    datetime,
    json,
    role_has_min,
    timedelta,
)


class ShiftSwapsMixin:
    def _are_swap_directions_compatible(self, requester_direction_name, target_direction_name):
        requester_key = self._normalize_direction_key(requester_direction_name)
        target_key = self._normalize_direction_key(target_direction_name)
        if not requester_key or not target_key:
            return False
        if requester_key == target_key:
            return True
        requester_is_smz_or_line = self._is_smz_direction(requester_direction_name) or self._is_line_direction(requester_direction_name)
        target_is_smz_or_line = self._is_smz_direction(target_direction_name) or self._is_line_direction(target_direction_name)
        if requester_is_smz_or_line and target_is_smz_or_line:
            return True
        return False

    def _normalize_swap_time_range(self, start_time, end_time):
        start_obj = self._normalize_schedule_time(start_time, 'start_time')
        end_obj = self._normalize_schedule_time(end_time, 'end_time')
        start_str = start_obj.strftime('%H:%M')
        end_str = end_obj.strftime('%H:%M')
        start_min = _time_to_minutes(start_str)
        end_min = _time_to_minutes(end_str)
        if end_min == start_min:
            raise ValueError("end_time must be different from start_time")
        if end_min < start_min:
            # Интервал через полночь, например 22:00 -> 02:00.
            end_min += 24 * 60
        if (end_min - start_min) > 24 * 60:
            raise ValueError("swap interval cannot be longer than 24 hours")
        return {
            'startTime': start_str,
            'endTime': end_str,
            'startMin': int(start_min),
            'endMin': int(end_min)
        }

    def _normalize_swap_time_range_for_date(self, swap_date, start_time, end_time, end_date=None):
        """
        Нормализует интервал запроса замены относительно стартовой даты swap_date.
        Возвращает start/end в минутах в окне [0..2880) и итоговую дату окончания.
        """
        swap_date_obj = self._normalize_schedule_date(swap_date)
        base_interval = self._normalize_swap_time_range(start_time, end_time)
        start_raw_min = _time_to_minutes(base_interval['startTime'])
        end_raw_min = _time_to_minutes(base_interval['endTime'])
        start_raw_min = int(start_raw_min)
        end_raw_min = int(end_raw_min)

        def _build_valid_candidate(candidate_start, candidate_end):
            c_start = int(candidate_start)
            c_end = int(candidate_end)
            if c_end <= c_start:
                return None
            duration = c_end - c_start
            if duration <= 0 or duration > (24 * 60):
                return None
            if c_start < 0 or c_end > (2 * 24 * 60):
                return None
            return {'start': c_start, 'end': c_end, 'duration': duration}

        if end_date is None or str(end_date).strip() == '':
            resolved_end_date_obj = swap_date_obj + timedelta(days=1) if int(base_interval['endMin']) > 1440 else swap_date_obj
            return {
                'startTime': base_interval['startTime'],
                'endTime': base_interval['endTime'],
                'startMin': int(base_interval['startMin']),
                'endMin': int(base_interval['endMin']),
                'swapDateObj': swap_date_obj,
                'endDateObj': resolved_end_date_obj
            }

        end_date_obj = self._normalize_schedule_date(end_date)
        day_diff = int((end_date_obj - swap_date_obj).days)
        if day_diff < 0:
            raise ValueError("end_date must be >= swap_date")
        if day_diff > 1:
            raise ValueError("end_date can be only swap_date or next day")

        candidates = []
        # Вариант A: интервал начинается в swap_date.
        anchored_end = day_diff * 1440 + end_raw_min
        if day_diff == 0 and end_raw_min < start_raw_min:
            anchored_end += 1440
        candidate_a = _build_valid_candidate(start_raw_min, anchored_end)
        if candidate_a:
            candidate_a['kind'] = 'anchored'
            candidates.append(candidate_a)

        # Вариант B: при end_date=next day считаем, что интервал может быть полностью на следующем дне.
        if day_diff > 0:
            shifted_start = day_diff * 1440 + start_raw_min
            shifted_end = day_diff * 1440 + end_raw_min
            if shifted_end <= shifted_start:
                shifted_end += 1440
            candidate_b = _build_valid_candidate(shifted_start, shifted_end)
            if candidate_b:
                candidate_b['kind'] = 'shifted'
                candidates.append(candidate_b)

        if not candidates:
            raise ValueError("Invalid swap interval for selected dates")

        # Предпочитаем минимальную длительность; при равенстве — anchored.
        candidates.sort(key=lambda x: (int(x['duration']), 0 if x.get('kind') == 'anchored' else 1))
        chosen = candidates[0]
        start_min = int(chosen['start'])
        end_min = int(chosen['end'])

        end_day_offset = int(end_min // 1440)
        resolved_end_date_obj = swap_date_obj + timedelta(days=end_day_offset)

        return {
            'startTime': base_interval['startTime'],
            'endTime': base_interval['endTime'],
            'startMin': int(start_min),
            'endMin': int(end_min),
            'swapDateObj': swap_date_obj,
            'endDateObj': resolved_end_date_obj
        }

    def _normalize_swap_request_payload(self, raw_payload, fallback_date=None):
        payload = raw_payload
        if payload is None:
            payload = {}
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                payload = {}
            else:
                try:
                    payload = json.loads(text)
                except Exception as exc:
                    raise ValueError("Invalid swap payload format") from exc
        if not isinstance(payload, dict):
            payload = {}

        swap_date = payload.get('swapDate') or payload.get('swap_date') or fallback_date
        if swap_date:
            try:
                swap_date = self._normalize_schedule_date(swap_date).strftime('%Y-%m-%d')
            except Exception:
                swap_date = None
        end_date = payload.get('endDate') or payload.get('end_date')
        if end_date:
            try:
                end_date = self._normalize_schedule_date(end_date).strftime('%Y-%m-%d')
            except Exception:
                end_date = None

        interval_raw = payload.get('interval') if isinstance(payload.get('interval'), dict) else {}
        start_time = interval_raw.get('start') or interval_raw.get('start_time')
        end_time = interval_raw.get('end') or interval_raw.get('end_time')
        start_min = interval_raw.get('startMin')
        end_min = interval_raw.get('endMin')

        if start_time and end_time:
            try:
                if swap_date:
                    normalized_interval = self._normalize_swap_time_range_for_date(
                        swap_date=swap_date,
                        start_time=start_time,
                        end_time=end_time,
                        end_date=end_date
                    )
                    start_time = normalized_interval['startTime']
                    end_time = normalized_interval['endTime']
                    start_min = normalized_interval['startMin']
                    end_min = normalized_interval['endMin']
                    end_date_obj = normalized_interval.get('endDateObj')
                    if isinstance(end_date_obj, date):
                        end_date = end_date_obj.strftime('%Y-%m-%d')
                else:
                    normalized_interval = self._normalize_swap_time_range(start_time, end_time)
                    start_time = normalized_interval['startTime']
                    end_time = normalized_interval['endTime']
                    start_min = normalized_interval['startMin']
                    end_min = normalized_interval['endMin']
            except Exception:
                start_time = None
                end_time = None
                start_min = None
                end_min = None

        if start_min is not None and end_min is not None:
            try:
                start_min = int(start_min)
                end_min = int(end_min)
            except Exception:
                start_min = None
                end_min = None
            if start_min is not None and end_min is not None:
                if end_min == start_min:
                    start_min = None
                    end_min = None
                elif end_min < start_min:
                    end_min += 24 * 60
                if start_min is not None and end_min is not None and (end_min - start_min) > (24 * 60):
                    start_min = None
                    end_min = None
                if swap_date and start_min is not None and end_min is not None:
                    try:
                        base_date_obj = self._normalize_schedule_date(swap_date)
                        end_offset_days = int(end_min // (24 * 60))
                        end_date = (base_date_obj + timedelta(days=end_offset_days)).strftime('%Y-%m-%d')
                    except Exception:
                        pass

        requested_segments_raw = payload.get('requestedSegments')
        if not isinstance(requested_segments_raw, list):
            requested_segments_raw = payload.get('requested_segments')
        requested_segments_intervals = self._swap_parse_payload_segments(
            requested_segments_raw,
            swap_date=swap_date
        )
        requested_segments = self._swap_serialize_intervals_with_day_offset(requested_segments_intervals)

        target_segments_raw = payload.get('targetSegments')
        if not isinstance(target_segments_raw, list):
            target_segments_raw = payload.get('target_segments')
        target_segments_intervals = self._swap_parse_payload_segments(
            target_segments_raw,
            swap_date=swap_date
        )
        target_segments = self._swap_serialize_intervals_with_day_offset(target_segments_intervals)

        mode_raw = payload.get('mode')
        mode = str(mode_raw).strip() if mode_raw is not None else ''
        if not mode:
            mode = 'shift_exchange_v2' if target_segments else 'shift_replacement_v1'

        return {
            'mode': mode,
            'swapDate': swap_date,
            'endDate': end_date,
            'startTime': start_time,
            'endTime': end_time,
            'startMin': start_min if start_min is not None else None,
            'endMin': end_min if end_min is not None else None,
            'requestedSegments': requested_segments,
            'targetSegments': target_segments
        }

    def _swap_parse_payload_segments(self, raw_segments, swap_date=None):
        if not isinstance(raw_segments, list):
            return []

        swap_date_obj = None
        if swap_date:
            try:
                swap_date_obj = self._normalize_schedule_date(swap_date)
            except Exception:
                swap_date_obj = None

        intervals = []
        for seg in raw_segments:
            if not isinstance(seg, dict):
                continue

            seg_start = seg.get('start') or seg.get('start_time')
            seg_end = seg.get('end') or seg.get('end_time')
            if not seg_start or not seg_end:
                continue
            try:
                seg_start_obj = self._normalize_schedule_time(seg_start, 'segment_start')
                seg_end_obj = self._normalize_schedule_time(seg_end, 'segment_end')
            except Exception:
                continue

            day_offset_raw = seg.get('dayOffset')
            if day_offset_raw is None:
                day_offset_raw = seg.get('day_offset')
            if day_offset_raw is None and swap_date_obj is not None:
                seg_date_raw = seg.get('date') or seg.get('shiftDate') or seg.get('shift_date')
                if seg_date_raw:
                    try:
                        seg_date_obj = self._normalize_schedule_date(seg_date_raw)
                        day_offset_raw = int((seg_date_obj - swap_date_obj).days)
                    except Exception:
                        day_offset_raw = 0

            try:
                day_offset = int(day_offset_raw) if day_offset_raw is not None else 0
            except Exception:
                day_offset = 0
            if day_offset < -1 or day_offset > 1:
                continue

            seg_start_min = _time_to_minutes(seg_start_obj.strftime('%H:%M')) + (day_offset * 1440)
            seg_end_min = _time_to_minutes(seg_end_obj.strftime('%H:%M')) + (day_offset * 1440)
            if seg_end_min <= seg_start_min:
                seg_end_min += 1440

            duration = int(seg_end_min) - int(seg_start_min)
            if duration <= 0 or duration > (24 * 60):
                continue
            if int(seg_start_min) < -1440 or int(seg_end_min) > 2880:
                continue

            intervals.append({
                'start': int(seg_start_min),
                'end': int(seg_end_min)
            })

        return self._merge_break_intervals(intervals)

    def _swap_serialize_intervals_with_day_offset(self, intervals):
        result = []
        for seg in self._merge_break_intervals(intervals or []):
            seg_start = int(seg.get('start', 0))
            seg_end = int(seg.get('end', 0))
            if seg_end <= seg_start:
                continue

            cursor = seg_start
            while cursor < seg_end:
                day_offset = int(cursor // 1440)
                day_start_abs = day_offset * 1440
                day_end_abs = day_start_abs + 1440
                chunk_end = min(seg_end, day_end_abs)
                if chunk_end <= cursor:
                    break

                local_start = int(cursor - day_start_abs)
                local_end = int(chunk_end - day_start_abs)
                if local_end > local_start:
                    result.append({
                        'start': _minutes_to_time(local_start),
                        'end': _minutes_to_time(local_end),
                        'dayOffset': int(day_offset),
                        'breaks': []
                    })
                cursor = chunk_end
        return result

    def _swap_payload_segments_to_day_map(self, segments, swap_date):
        if not swap_date:
            return {}
        try:
            swap_date_obj = self._normalize_schedule_date(swap_date)
        except Exception:
            return {}
        intervals = self._swap_parse_payload_segments(segments, swap_date=swap_date_obj.strftime('%Y-%m-%d'))
        if not intervals:
            return {}
        return self._swap_serialize_intervals_to_day_map(
            intervals,
            swap_date_obj,
            allowed_day_offsets=[-1, 0, 1]
        )

    def _build_swap_window_intervals(self, prev_day_shifts=None, day_shifts=None, next_day_shifts=None):
        intervals = []

        def append_shifts(shifts, day_offset_minutes):
            for seg in (shifts or []):
                if not isinstance(seg, dict):
                    continue
                start_val = seg.get('start')
                end_val = seg.get('end')
                if not start_val or not end_val:
                    continue
                seg_start, seg_end = self._schedule_interval_minutes(start_val, end_val)
                seg_start = int(seg_start) + int(day_offset_minutes)
                seg_end = int(seg_end) + int(day_offset_minutes)
                if seg_end <= seg_start:
                    continue
                intervals.append({'start': seg_start, 'end': seg_end})

        append_shifts(prev_day_shifts, -1440)
        append_shifts(day_shifts, 0)
        append_shifts(next_day_shifts, 1440)
        return self._merge_break_intervals(intervals)

    def _swap_extract_interval(self, intervals, interval_start_min, interval_end_min):
        interval_start_min = int(interval_start_min)
        interval_end_min = int(interval_end_min)
        if interval_end_min <= interval_start_min:
            return []

        overlaps = []
        for seg in (intervals or []):
            seg_start = int(seg.get('start', 0))
            seg_end = int(seg.get('end', 0))
            if seg_end <= seg_start:
                continue
            overlap_start = max(seg_start, interval_start_min)
            overlap_end = min(seg_end, interval_end_min)
            if overlap_end <= overlap_start:
                continue
            overlaps.append({'start': overlap_start, 'end': overlap_end})
        return self._merge_break_intervals(overlaps)

    def _swap_intervals_overlap(self, intervals, interval_start_min, interval_end_min):
        return bool(self._swap_extract_interval(intervals, interval_start_min, interval_end_min))

    def _swap_is_interval_fully_covered(self, intervals, interval_start_min, interval_end_min):
        interval_start_min = int(interval_start_min)
        interval_end_min = int(interval_end_min)
        if interval_end_min <= interval_start_min:
            return False
        extracted = self._swap_extract_interval(intervals, interval_start_min, interval_end_min)
        if not extracted:
            return False
        covered_until = interval_start_min
        for seg in extracted:
            seg_start = int(seg.get('start', 0))
            seg_end = int(seg.get('end', 0))
            if seg_end <= covered_until:
                continue
            if seg_start > covered_until:
                return False
            covered_until = max(covered_until, seg_end)
            if covered_until >= interval_end_min:
                return True
        return covered_until >= interval_end_min

    def _swap_subtract_interval(self, intervals, interval_start_min, interval_end_min):
        interval_start_min = int(interval_start_min)
        interval_end_min = int(interval_end_min)
        if interval_end_min <= interval_start_min:
            return self._merge_break_intervals(intervals or [])

        result = []
        for seg in (intervals or []):
            seg_start = int(seg.get('start', 0))
            seg_end = int(seg.get('end', 0))
            if seg_end <= seg_start:
                continue
            if seg_end <= interval_start_min or seg_start >= interval_end_min:
                result.append({'start': seg_start, 'end': seg_end})
                continue
            if seg_start < interval_start_min:
                result.append({'start': seg_start, 'end': interval_start_min})
            if seg_end > interval_end_min:
                result.append({'start': interval_end_min, 'end': seg_end})
        return self._merge_break_intervals(result)

    def _swap_subtract_intervals(self, intervals, subtracted_intervals):
        result = self._merge_break_intervals(intervals or [])
        for seg in self._merge_break_intervals(subtracted_intervals or []):
            result = self._swap_subtract_interval(
                result,
                int(seg.get('start', 0)),
                int(seg.get('end', 0))
            )
            if not result:
                return []
        return self._merge_break_intervals(result)

    def _swap_interval_sets_overlap(self, left_intervals, right_intervals):
        left = self._merge_break_intervals(left_intervals or [])
        right = self._merge_break_intervals(right_intervals or [])
        if not left or not right:
            return False
        for left_seg in left:
            l_start = int(left_seg.get('start', 0))
            l_end = int(left_seg.get('end', 0))
            if l_end <= l_start:
                continue
            for right_seg in right:
                r_start = int(right_seg.get('start', 0))
                r_end = int(right_seg.get('end', 0))
                if r_end <= r_start:
                    continue
                if l_start < r_end and r_start < l_end:
                    return True
        return False

    def _swap_serialize_intervals_for_payload(self, intervals):
        return self._swap_serialize_intervals_with_day_offset(intervals)

    def _swap_intervals_signature(self, segments):
        sign = []
        for seg in (segments or []):
            if not isinstance(seg, dict):
                continue
            start_val = seg.get('start')
            end_val = seg.get('end')
            if not start_val or not end_val:
                continue
            day_offset_raw = seg.get('dayOffset')
            if day_offset_raw is None:
                day_offset_raw = seg.get('day_offset')
            try:
                day_offset = int(day_offset_raw) if day_offset_raw is not None else 0
            except Exception:
                day_offset = 0
            sign.append(f"{day_offset}|{start_val}|{end_val}")
        sign.sort()
        return sign

    def _swap_serialize_intervals_to_day_map(self, intervals, swap_date_obj, allowed_day_offsets):
        if not isinstance(allowed_day_offsets, (list, tuple, set)):
            return {}
        allowed_offsets = {int(v) for v in allowed_day_offsets}
        if not allowed_offsets:
            return {}

        day_map = {}
        for seg in self._merge_break_intervals(intervals or []):
            seg_start = int(seg.get('start', 0))
            seg_end = int(seg.get('end', 0))
            if seg_end <= seg_start:
                continue

            cursor = seg_start
            while cursor < seg_end:
                day_offset = int(cursor // 1440)
                chunk_end = min(seg_end, cursor + 1440)
                if chunk_end <= cursor:
                    break
                if day_offset in allowed_offsets:
                    day_start_abs = day_offset * 1440
                    local_start = int(cursor - day_start_abs)
                    local_end = int(chunk_end - day_start_abs)
                    if local_end > local_start:
                        day_key = (swap_date_obj + timedelta(days=day_offset)).strftime('%Y-%m-%d')
                        day_map.setdefault(day_key, []).append({
                            'start': _minutes_to_time(local_start),
                            'end': _minutes_to_time(local_end)
                        })
                cursor = chunk_end

        normalized = {}
        for day_key, shifts in day_map.items():
            merged = _merge_shifts_for_date(shifts or [])
            if merged:
                normalized[day_key] = merged
        return normalized

    def _serialize_shift_swap_request_row(self, row):
        if not row:
            return None
        (
            request_id,
            requester_id,
            requester_name,
            target_id,
            target_name,
            direction_id,
            direction_name,
            start_date,
            end_date,
            status,
            request_comment,
            response_comment,
            requested_shifts_json,
            created_at,
            updated_at,
            responded_at,
            accepted_at,
            responded_by,
            responded_by_name
        ) = row

        payload = self._normalize_swap_request_payload(
            requested_shifts_json,
            fallback_date=start_date.strftime('%Y-%m-%d') if isinstance(start_date, date) else None
        )
        requested_segments = payload.get('requestedSegments') or []
        target_segments = payload.get('targetSegments') or []
        swap_date_value = payload.get('swapDate') or (start_date.strftime('%Y-%m-%d') if isinstance(start_date, date) else '')
        requested_day_map = self._swap_payload_segments_to_day_map(requested_segments, swap_date_value)
        target_day_map = self._swap_payload_segments_to_day_map(target_segments, swap_date_value)
        requested_totals = self._snapshot_shift_totals(requested_day_map or {})
        target_totals = self._snapshot_shift_totals(target_day_map or {})

        def _fmt_dt(value):
            if isinstance(value, datetime):
                return value.isoformat()
            return None

        exchange_mode = str(payload.get('mode') or '').strip()
        if not exchange_mode:
            exchange_mode = 'shift_exchange_v2' if target_segments else 'shift_replacement_v1'

        return {
            'id': int(request_id),
            'requester': {
                'id': int(requester_id),
                'name': requester_name
            },
            'target': {
                'id': int(target_id),
                'name': target_name
            },
            'direction': {
                'id': int(direction_id) if direction_id is not None else None,
                'name': direction_name
            },
            'swapDate': payload.get('swapDate') or (start_date.strftime('%Y-%m-%d') if isinstance(start_date, date) else None),
            'startTime': payload.get('startTime'),
            'endTime': payload.get('endTime'),
            'startDate': start_date.strftime('%Y-%m-%d') if isinstance(start_date, date) else str(start_date),
            'endDate': end_date.strftime('%Y-%m-%d') if isinstance(end_date, date) else str(end_date),
            'status': str(status),
            'requestComment': request_comment,
            'responseComment': response_comment,
            'requestedShifts': requested_day_map,
            'requestedSegments': requested_segments,
            'targetShifts': target_day_map,
            'targetSegments': target_segments,
            'summary': requested_totals,
            'exchangeSummary': target_totals,
            'exchangeMode': exchange_mode,
            'createdAt': _fmt_dt(created_at),
            'updatedAt': _fmt_dt(updated_at),
            'respondedAt': _fmt_dt(responded_at),
            'acceptedAt': _fmt_dt(accepted_at),
            'respondedBy': {
                'id': int(responded_by) if responded_by is not None else None,
                'name': responded_by_name
            }
        }

    def _select_shift_swap_request_by_id_tx(self, cursor, request_id):
        cursor.execute(
            """
            SELECT
                r.id,
                r.requester_operator_id, req.name,
                r.target_operator_id, tgt.name,
                r.direction_id, d.name,
                r.start_date, r.end_date,
                r.status,
                r.request_comment,
                r.response_comment,
                r.requested_shifts_json,
                r.created_at,
                r.updated_at,
                r.responded_at,
                r.accepted_at,
                r.responded_by,
                resp.name
            FROM work_shift_swap_requests r
            JOIN users req ON req.id = r.requester_operator_id
            JOIN users tgt ON tgt.id = r.target_operator_id
            LEFT JOIN directions d ON d.id = r.direction_id
            LEFT JOIN users resp ON resp.id = r.responded_by
            WHERE r.id = %s
            LIMIT 1
            """,
            (int(request_id),)
        )
        return cursor.fetchone()

    def get_shift_swap_candidates(
        self,
        requester_operator_id,
        swap_date,
        start_time,
        end_time,
        end_date=None,
        overlap_only=False,
        non_overlap_only=False
    ):
        requester_operator_id = int(requester_operator_id)
        overlap_only = bool(overlap_only)
        non_overlap_only = bool(non_overlap_only)
        interval = self._normalize_swap_time_range_for_date(
            swap_date=swap_date,
            start_time=start_time,
            end_time=end_time,
            end_date=end_date
        )
        swap_date_obj = interval['swapDateObj']
        swap_date_str = swap_date_obj.strftime('%Y-%m-%d')
        prev_date_obj = swap_date_obj - timedelta(days=1)
        next_date_obj = swap_date_obj + timedelta(days=1)
        prev_date_str = prev_date_obj.strftime('%Y-%m-%d')
        next_date_str = next_date_obj.strftime('%Y-%m-%d')
        interval_start_min = interval['startMin']
        interval_end_min = interval['endMin']

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.role, u.direction_id, COALESCE(d.name, ''), u.department_id
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE u.id = %s
                LIMIT 1
                """,
                (requester_operator_id,)
            )
            requester_row = cursor.fetchone()
            if not requester_row:
                raise ValueError("Requester not found")
            if str(requester_row[1]) != 'operator':
                raise ValueError("Only operators can request swap candidates")
            requester_direction_id = requester_row[2]
            requester_direction_name = requester_row[3]
            requester_department_id = requester_row[4]
            if requester_direction_id is None:
                raise ValueError("У вашего профиля не задано направление")
            if requester_department_id is None:
                raise ValueError("У вашего профиля не задан отдел")

            cursor.execute(
                """
                SELECT
                    u.id,
                    u.name,
                    COALESCE(d.name, ''),
                    COALESCE(u.status, 'working'),
                    u.rate,
                    s.name
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                LEFT JOIN users s ON s.id = u.supervisor_id
                WHERE u.role = 'operator'
                  AND u.id <> %s
                  AND u.department_id = %s
                  AND COALESCE(u.status, 'working') <> 'fired'
                ORDER BY LOWER(u.name), u.id
                """,
                (requester_operator_id, int(requester_department_id))
            )
            candidate_rows = cursor.fetchall() or []
            candidate_rows = [
                row for row in candidate_rows
                if self._are_swap_directions_compatible(requester_direction_name, row[2])
            ]
            if not candidate_rows:
                return []

            candidate_ids = [int(row[0]) for row in candidate_rows]
            day_shift_map_by_operator = {}
            day_off_map_by_operator = {
                int(op_id): {
                    prev_date_str: False,
                    swap_date_str: False,
                    next_date_str: False
                }
                for op_id in candidate_ids
            }

            cursor.execute(
                """
                SELECT operator_id, day_off_date
                FROM days_off
                WHERE operator_id = ANY(%s)
                  AND day_off_date >= %s
                  AND day_off_date <= %s
                """,
                (candidate_ids, prev_date_obj, next_date_obj)
            )
            for op_id_value, day_off_date in (cursor.fetchall() or []):
                op_id_int = int(op_id_value)
                bucket = day_off_map_by_operator.get(op_id_int)
                if bucket is None:
                    continue
                day_key = day_off_date.strftime('%Y-%m-%d')
                if day_key in bucket:
                    bucket[day_key] = True

            for op_id in candidate_ids:
                shift_map = self._load_operator_shift_map_for_period_tx(
                    cursor=cursor,
                    operator_id=op_id,
                    start_date_obj=prev_date_obj,
                    end_date_obj=next_date_obj
                )
                day_shift_map_by_operator[int(op_id)] = {
                    'prevDayShifts': shift_map.get(prev_date_str, []),
                    'dayShifts': shift_map.get(swap_date_str, []),
                    'nextDayShifts': shift_map.get(next_date_str, [])
                }

        result = []
        for row in candidate_rows:
            op_id = int(row[0])
            shift_payload = day_shift_map_by_operator.get(op_id) or {}
            day_off_payload = day_off_map_by_operator.get(op_id) or {}
            op_prev_day_shifts = shift_payload.get('prevDayShifts') or []
            op_day_shifts = shift_payload.get('dayShifts') or []
            op_next_day_shifts = shift_payload.get('nextDayShifts') or []
            op_is_day_off = bool(day_off_payload.get(swap_date_str))
            op_is_next_day_off = bool(day_off_payload.get(next_date_str))

            window_intervals = self._build_swap_window_intervals(
                prev_day_shifts=op_prev_day_shifts,
                day_shifts=op_day_shifts,
                next_day_shifts=op_next_day_shifts
            )
            overlap_intervals = self._swap_extract_interval(window_intervals, interval_start_min, interval_end_min)
            has_overlap = bool(overlap_intervals)
            if overlap_only and not has_overlap:
                continue
            if non_overlap_only and has_overlap:
                continue

            day_display_intervals = self._swap_extract_interval(window_intervals, 0, 1440)
            next_day_display_intervals_abs = self._swap_extract_interval(window_intervals, 1440, 2880)

            normalized_day_shifts = []
            for seg in (day_display_intervals or []):
                seg_start = int(seg.get('start', 0))
                seg_end = int(seg.get('end', 0))
                if seg_end <= seg_start:
                    continue
                continues_next_day = any(
                    int(iv.get('start', 0)) < 1440
                    and int(iv.get('end', 0)) > 1440
                    and int(iv.get('start', 0)) < seg_end
                    and seg_start < int(iv.get('end', 0))
                    for iv in (window_intervals or [])
                )
                normalized_day_shifts.append({
                    'start': _minutes_to_time(seg_start),
                    'end': _minutes_to_time(seg_end),
                    'continuesNextDay': bool(continues_next_day)
                })

            normalized_next_day_shifts = []
            for seg in (next_day_display_intervals_abs or []):
                seg_start = int(seg.get('start', 0))
                seg_end = int(seg.get('end', 0))
                if seg_end <= seg_start:
                    continue
                normalized_next_day_shifts.append({
                    'start': _minutes_to_time(seg_start - 1440),
                    'end': _minutes_to_time(seg_end - 1440)
                })

            match_starts_at_request_end = any(int(iv.get('start', -1)) == int(interval_end_min) for iv in (window_intervals or []))
            match_ends_at_request_start = any(int(iv.get('end', -1)) == int(interval_start_min) for iv in (window_intervals or []))
            overlap_minutes = sum(
                max(0, int(seg.get('end', 0)) - int(seg.get('start', 0)))
                for seg in (overlap_intervals or [])
            )

            priority_score = int(overlap_minutes)
            if match_starts_at_request_end:
                priority_score += 10
            if match_ends_at_request_start:
                priority_score += 10
            result.append({
                'id': op_id,
                'name': row[1],
                'direction': row[2],
                'status': row[3],
                'rate': float(row[4]) if row[4] is not None else None,
                'supervisorName': row[5],
                'dayShifts': normalized_day_shifts,
                'isDayOff': op_is_day_off,
                'is_day_off': op_is_day_off,
                'nextDayDate': next_date_str,
                'nextDayShifts': normalized_next_day_shifts,
                'isNextDayOff': op_is_next_day_off,
                'is_next_day_off': op_is_next_day_off,
                'matchStartsAtRequestEnd': bool(match_starts_at_request_end),
                'matchEndsAtRequestStart': bool(match_ends_at_request_start),
                'hasOverlap': bool(has_overlap),
                'overlapMinutes': int(overlap_minutes),
                'priorityScore': int(priority_score)
            })

        result.sort(
            key=lambda item: (
                -int(item.get('priorityScore') or 0),
                str(item.get('name') or '').lower(),
                int(item.get('id') or 0)
            )
        )
        return result

    def create_shift_swap_request(
        self,
        requester_operator_id,
        target_operator_id,
        swap_date,
        start_time,
        end_time,
        end_date=None,
        request_comment=None,
        target_segments=None
    ):
        requester_operator_id = int(requester_operator_id)
        target_operator_id = int(target_operator_id)
        if requester_operator_id == target_operator_id:
            raise ValueError("Нельзя отправить запрос самому себе")

        interval = self._normalize_swap_time_range_for_date(
            swap_date=swap_date,
            start_time=start_time,
            end_time=end_time,
            end_date=end_date
        )
        swap_date_obj = interval['swapDateObj']
        swap_date_str = swap_date_obj.strftime('%Y-%m-%d')
        prev_date_obj = swap_date_obj - timedelta(days=1)
        next_date_obj = swap_date_obj + timedelta(days=1)
        prev_date_str = prev_date_obj.strftime('%Y-%m-%d')
        next_date_str = next_date_obj.strftime('%Y-%m-%d')
        interval_start_min = interval['startMin']
        interval_end_min = interval['endMin']
        request_end_date_obj = interval['endDateObj']
        target_requested_intervals = self._swap_parse_payload_segments(
            target_segments,
            swap_date=swap_date_str
        )
        exchange_requested = bool(target_requested_intervals)

        request_comment_norm = None
        if request_comment is not None:
            text = str(request_comment).strip()
            request_comment_norm = text or None

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.role,
                    u.direction_id,
                    u.name,
                    COALESCE(u.status, 'working'),
                    COALESCE(d.name, ''),
                    u.department_id
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE u.id = ANY(%s)
                FOR SHARE OF u
                """,
                ([requester_operator_id, target_operator_id],)
            )
            users_rows = cursor.fetchall() or []
            users_map = {int(row[0]): row for row in users_rows}
            requester = users_map.get(requester_operator_id)
            target = users_map.get(target_operator_id)
            if not requester:
                raise ValueError("Requester not found")
            if not target:
                raise ValueError("Target operator not found")
            if str(requester[1]) != 'operator' or str(target[1]) != 'operator':
                raise ValueError("Swap is available only between operators")
            if str(target[4]) == 'fired':
                raise ValueError("Выбранный оператор уволен")

            requester_department_id = requester[6]
            target_department_id = target[6]
            if requester_department_id is None:
                raise ValueError("У вашего профиля не задан отдел")
            if target_department_id is None or int(target_department_id) != int(requester_department_id):
                raise ValueError("Оператор для замены должен быть из вашего отдела")

            requester_direction_id = requester[2]
            target_direction_id = target[2]
            requester_direction_name = requester[5]
            target_direction_name = target[5]
            if requester_direction_id is None or target_direction_id is None:
                raise ValueError("У операторов должно быть указано направление")
            if not self._are_swap_directions_compatible(requester_direction_name, target_direction_name):
                raise ValueError("Оператор для замены должен быть в том же направлении (СМЗ и Основа взаимозаменяемы)")

            cursor.execute(
                """
                SELECT requested_shifts_json
                FROM work_shift_swap_requests
                WHERE status = 'pending'
                  AND requester_operator_id = %s
                  AND target_operator_id = %s
                  AND start_date = %s
                """,
                (requester_operator_id, target_operator_id, swap_date_obj)
            )
            existing_pending_payloads = cursor.fetchall() or []
            for (existing_raw_payload,) in existing_pending_payloads:
                existing_payload = self._normalize_swap_request_payload(existing_raw_payload, fallback_date=swap_date_str)
                ex_start = existing_payload.get('startMin')
                ex_end = existing_payload.get('endMin')
                if ex_start is None or ex_end is None:
                    continue
                if int(ex_start) < interval_end_min and interval_start_min < int(ex_end):
                    raise ValueError("Уже есть активный запрос на пересекающийся интервал для этого оператора")

            requester_day_map = self._load_operator_shift_map_for_period_tx(
                cursor=cursor,
                operator_id=requester_operator_id,
                start_date_obj=prev_date_obj,
                end_date_obj=next_date_obj
            )
            target_day_map = self._load_operator_shift_map_for_period_tx(
                cursor=cursor,
                operator_id=target_operator_id,
                start_date_obj=prev_date_obj,
                end_date_obj=next_date_obj
            )

            requester_window_intervals = self._build_swap_window_intervals(
                prev_day_shifts=requester_day_map.get(prev_date_str, []),
                day_shifts=requester_day_map.get(swap_date_str, []),
                next_day_shifts=requester_day_map.get(next_date_str, [])
            )
            target_window_intervals = self._build_swap_window_intervals(
                prev_day_shifts=target_day_map.get(prev_date_str, []),
                day_shifts=target_day_map.get(swap_date_str, []),
                next_day_shifts=target_day_map.get(next_date_str, [])
            )

            if not self._swap_is_interval_fully_covered(requester_window_intervals, interval_start_min, interval_end_min):
                raise ValueError("Выбранный интервал должен полностью находиться внутри ваших смен")
            if exchange_requested:
                is_full_shift_interval = any(
                    int(seg.get('start', -1)) == int(interval_start_min)
                    and int(seg.get('end', -1)) == int(interval_end_min)
                    for seg in (requester_window_intervals or [])
                )
                if not is_full_shift_interval:
                    raise ValueError("Для обмена нужно выбрать полную смену без обрезки интервала")

            requested_intervals = self._swap_extract_interval(requester_window_intervals, interval_start_min, interval_end_min)
            requested_segments = self._swap_serialize_intervals_for_payload(requested_intervals)
            if not requested_segments:
                raise ValueError("Не удалось сформировать сегменты смен для замены")
            target_segments_norm = []
            if exchange_requested:
                for seg in target_requested_intervals:
                    seg_start = int(seg.get('start', 0))
                    seg_end = int(seg.get('end', 0))
                    if seg_start < 0 or seg_end > 1440:
                        raise ValueError("Для обмена можно выбрать только смены кандидата на ту же дату")
                    if not self._swap_is_interval_fully_covered(target_window_intervals, seg_start, seg_end):
                        raise ValueError("У выбранного оператора больше нет одной из выбранных смен")

                target_selected_intervals = self._merge_break_intervals(target_requested_intervals)
                target_segments_norm = self._swap_serialize_intervals_for_payload(target_selected_intervals)
                if not target_segments_norm:
                    raise ValueError("Не удалось обработать выбранные смены оператора")

                target_remaining_intervals = self._swap_subtract_intervals(
                    target_window_intervals,
                    target_selected_intervals
                )
                if self._swap_interval_sets_overlap(target_remaining_intervals, requested_intervals):
                    raise ValueError("Выбранные смены оператора не покрывают интервал обмена")

            payload = {
                'mode': 'shift_exchange_v2' if exchange_requested else 'shift_replacement_v1',
                'swapDate': swap_date_str,
                'endDate': request_end_date_obj.strftime('%Y-%m-%d'),
                'interval': {
                    'start': interval['startTime'],
                    'end': interval['endTime'],
                    'startMin': interval_start_min,
                    'endMin': interval_end_min
                },
                'requestedSegments': requested_segments,
                'targetSegments': target_segments_norm
            }

            cursor.execute(
                """
                INSERT INTO work_shift_swap_requests (
                    requester_operator_id,
                    target_operator_id,
                    direction_id,
                    start_date,
                    end_date,
                    status,
                    request_comment,
                    requested_shifts_json,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (
                    requester_operator_id,
                    target_operator_id,
                    int(requester_direction_id),
                    swap_date_obj,
                    request_end_date_obj,
                    request_comment_norm,
                    json.dumps(payload, ensure_ascii=False)
                )
            )
            request_id = int(cursor.fetchone()[0])
            row = self._select_shift_swap_request_by_id_tx(cursor, request_id)
            return self._serialize_shift_swap_request_row(row)

    def get_shift_swap_requests_for_operator(self, operator_id, limit=200):
        operator_id = int(operator_id)
        limit = max(1, min(int(limit), 500))

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    r.id,
                    r.requester_operator_id, req.name,
                    r.target_operator_id, tgt.name,
                    r.direction_id, d.name,
                    r.start_date, r.end_date,
                    r.status,
                    r.request_comment,
                    r.response_comment,
                    r.requested_shifts_json,
                    r.created_at,
                    r.updated_at,
                    r.responded_at,
                    r.accepted_at,
                    r.responded_by,
                    resp.name
                FROM work_shift_swap_requests r
                JOIN users req ON req.id = r.requester_operator_id
                JOIN users tgt ON tgt.id = r.target_operator_id
                LEFT JOIN directions d ON d.id = r.direction_id
                LEFT JOIN users resp ON resp.id = r.responded_by
                WHERE r.requester_operator_id = %s
                   OR r.target_operator_id = %s
                ORDER BY
                    CASE WHEN r.status = 'pending' THEN 0 ELSE 1 END,
                    r.created_at DESC,
                    r.id DESC
                LIMIT %s
                """,
                (operator_id, operator_id, limit)
            )
            rows = cursor.fetchall() or []

        serialized = [self._serialize_shift_swap_request_row(row) for row in rows]
        incoming = [item for item in serialized if int(item['target']['id']) == operator_id]
        outgoing = [item for item in serialized if int(item['requester']['id']) == operator_id]
        return {
            'incoming': incoming,
            'outgoing': outgoing
        }

    def get_shift_swap_journal_for_month(self, month, requester_role, requester_id, limit=500):
        month_text = str(month or '').strip()
        if not month_text:
            raise ValueError("month is required (YYYY-MM)")
        try:
            month_start = datetime.strptime(month_text, '%Y-%m').date().replace(day=1)
        except Exception:
            raise ValueError("Invalid month format, expected YYYY-MM")

        if month_start.month == 12:
            month_end = date(month_start.year + 1, 1, 1)
        else:
            month_end = date(month_start.year, month_start.month + 1, 1)

        role = str(requester_role or '').strip().lower()
        if not (role_has_min(role, 'admin') or role == 'sv'):
            raise ValueError("Only admin and sv can view swap journal")

        int(requester_id)
        limit = max(1, min(int(limit), 1000))

        with self._get_cursor() as cursor:
            query = """
                SELECT
                    r.id,
                    r.requester_operator_id, req.name,
                    r.target_operator_id, tgt.name,
                    r.direction_id, d.name,
                    r.start_date, r.end_date,
                    r.status,
                    r.request_comment,
                    r.response_comment,
                    r.requested_shifts_json,
                    r.created_at,
                    r.updated_at,
                    r.responded_at,
                    r.accepted_at,
                    r.responded_by,
                    resp.name
                FROM work_shift_swap_requests r
                JOIN users req ON req.id = r.requester_operator_id
                JOIN users tgt ON tgt.id = r.target_operator_id
                LEFT JOIN directions d ON d.id = r.direction_id
                LEFT JOIN users resp ON resp.id = r.responded_by
                WHERE r.start_date >= %s
                  AND r.start_date < %s
            """
            params = [month_start, month_end]

            query += """
                ORDER BY r.start_date DESC, r.created_at DESC, r.id DESC
                LIMIT %s
            """
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall() or []

        items = [self._serialize_shift_swap_request_row(row) for row in rows]
        return {
            'month': month_start.strftime('%Y-%m'),
            'items': items
        }

    def respond_shift_swap_request(self, request_id, responder_operator_id, action, response_comment=None):
        request_id = int(request_id)
        responder_operator_id = int(responder_operator_id)
        action_norm = str(action or '').strip().lower()
        if action_norm not in ('accept', 'reject', 'cancel'):
            raise ValueError("action must be 'accept', 'reject' or 'cancel'")

        response_comment_norm = None
        if response_comment is not None:
            text = str(response_comment).strip()
            response_comment_norm = text or None

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, role
                FROM users
                WHERE id = %s
                LIMIT 1
                """,
                (responder_operator_id,)
            )
            responder_row = cursor.fetchone()
            if not responder_row:
                raise ValueError("Responder not found")
            if str(responder_row[1]) != 'operator':
                raise ValueError("Only operators can respond to swap requests")

            cursor.execute(
                """
                SELECT
                    id,
                    requester_operator_id,
                    target_operator_id,
                    start_date,
                    end_date,
                    status,
                    requested_shifts_json
                FROM work_shift_swap_requests
                WHERE id = %s
                FOR UPDATE
                """,
                (request_id,)
            )
            locked_row = cursor.fetchone()
            if not locked_row:
                raise ValueError("Swap request not found")

            (
                _locked_id,
                requester_operator_id,
                target_operator_id,
                start_date_obj,
                end_date_obj,
                current_status,
                requested_shifts_raw
            ) = locked_row

            requester_operator_id = int(requester_operator_id)
            target_operator_id = int(target_operator_id)
            if str(current_status) != 'pending':
                raise ValueError(f"Запрос уже обработан (статус: {current_status})")

            if action_norm == 'cancel':
                if responder_operator_id != requester_operator_id:
                    raise ValueError("Только отправитель запроса может отменить замену")
                cursor.execute(
                    """
                    UPDATE work_shift_swap_requests
                    SET status = 'cancelled',
                        response_comment = %s,
                        responded_by = %s,
                        responded_at = CURRENT_TIMESTAMP,
                        accepted_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (response_comment_norm, responder_operator_id, request_id)
                )
                row = self._select_shift_swap_request_by_id_tx(cursor, request_id)
                return self._serialize_shift_swap_request_row(row)

            if responder_operator_id != target_operator_id:
                raise ValueError("Только получатель запроса может принять или отклонить замену")

            if action_norm == 'reject':
                cursor.execute(
                    """
                    UPDATE work_shift_swap_requests
                    SET status = 'rejected',
                        response_comment = %s,
                        responded_by = %s,
                        responded_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    """,
                    (response_comment_norm, responder_operator_id, request_id)
                )
                row = self._select_shift_swap_request_by_id_tx(cursor, request_id)
                return self._serialize_shift_swap_request_row(row)

            cursor.execute(
                """
                SELECT id, department_id
                FROM users
                WHERE id = ANY(%s)
                FOR SHARE
                """,
                ([requester_operator_id, target_operator_id],)
            )
            participant_departments = {
                int(user_id): department_id
                for user_id, department_id in (cursor.fetchall() or [])
            }
            requester_department_id = participant_departments.get(requester_operator_id)
            target_department_id = participant_departments.get(target_operator_id)
            if (
                requester_department_id is None
                or target_department_id is None
                or int(requester_department_id) != int(target_department_id)
            ):
                raise ValueError("Нельзя принять запрос: операторы находятся в разных отделах")

            fallback_date = start_date_obj.strftime('%Y-%m-%d') if isinstance(start_date_obj, date) else None
            payload = self._normalize_swap_request_payload(requested_shifts_raw, fallback_date=fallback_date)
            swap_date_str = payload.get('swapDate') or fallback_date
            if not swap_date_str:
                raise ValueError("Запрос не содержит дату замены")
            swap_date_obj = self._normalize_schedule_date(swap_date_str)
            requested_segments = payload.get('requestedSegments') or []
            if not requested_segments:
                raise ValueError("Запрос не содержит сегменты смен для переноса")
            target_segments = payload.get('targetSegments') or []
            is_replacement_request = len(target_segments) == 0

            requested_intervals_payload = self._swap_parse_payload_segments(
                requested_segments,
                swap_date=swap_date_str
            )
            if not requested_intervals_payload:
                raise ValueError("Запрос не содержит корректные сегменты смен инициатора")

            target_intervals_payload = self._swap_parse_payload_segments(
                target_segments,
                swap_date=swap_date_str
            )
            if target_segments and not target_intervals_payload:
                raise ValueError("Запрос не содержит корректные сегменты смен получателя")

            prev_date_obj = swap_date_obj - timedelta(days=1)
            next_date_obj = swap_date_obj + timedelta(days=1)
            prev_date_str = prev_date_obj.strftime('%Y-%m-%d')
            next_date_str = next_date_obj.strftime('%Y-%m-%d')

            requester_day_map = self._load_operator_shift_map_for_period_tx(
                cursor=cursor,
                operator_id=requester_operator_id,
                start_date_obj=prev_date_obj,
                end_date_obj=next_date_obj
            )
            target_day_map = self._load_operator_shift_map_for_period_tx(
                cursor=cursor,
                operator_id=target_operator_id,
                start_date_obj=prev_date_obj,
                end_date_obj=next_date_obj
            )

            requester_window_intervals = self._build_swap_window_intervals(
                prev_day_shifts=requester_day_map.get(prev_date_str, []),
                day_shifts=requester_day_map.get(swap_date_str, []),
                next_day_shifts=requester_day_map.get(next_date_str, [])
            )
            target_window_intervals = self._build_swap_window_intervals(
                prev_day_shifts=target_day_map.get(prev_date_str, []),
                day_shifts=target_day_map.get(swap_date_str, []),
                next_day_shifts=target_day_map.get(next_date_str, [])
            )
            current_requested_intervals = []
            for seg in requested_intervals_payload:
                seg_start = int(seg.get('start', 0))
                seg_end = int(seg.get('end', 0))
                if not self._swap_is_interval_fully_covered(requester_window_intervals, seg_start, seg_end):
                    raise ValueError("Нельзя принять запрос: у инициатора больше нет выбранной смены")
                current_requested_intervals.extend(
                    self._swap_extract_interval(requester_window_intervals, seg_start, seg_end)
                )
            current_requested_intervals = self._merge_break_intervals(current_requested_intervals)
            current_requested_segments = self._swap_serialize_intervals_for_payload(current_requested_intervals)
            if not current_requested_intervals:
                raise ValueError("Нельзя принять запрос: у инициатора больше нет смен в этом интервале")

            requested_sign = self._swap_intervals_signature(requested_segments)
            current_sign = self._swap_intervals_signature(current_requested_segments)
            if requested_sign and requested_sign != current_sign:
                raise ValueError("Нельзя принять запрос: интервалы инициатора изменились")

            current_target_intervals = []
            for seg in target_intervals_payload:
                seg_start = int(seg.get('start', 0))
                seg_end = int(seg.get('end', 0))
                if not self._swap_is_interval_fully_covered(target_window_intervals, seg_start, seg_end):
                    raise ValueError("Нельзя принять запрос: одна из ваших выбранных смен уже изменилась")
                current_target_intervals.extend(
                    self._swap_extract_interval(target_window_intervals, seg_start, seg_end)
                )
            current_target_intervals = self._merge_break_intervals(current_target_intervals)
            current_target_segments = self._swap_serialize_intervals_for_payload(current_target_intervals)
            target_sign = self._swap_intervals_signature(target_segments)
            current_target_sign = self._swap_intervals_signature(current_target_segments)
            if target_sign and target_sign != current_target_sign:
                raise ValueError("Нельзя принять запрос: выбранные вами интервалы изменились")
            if target_sign and not current_target_intervals:
                raise ValueError("Нельзя принять запрос: выбранные вами смены больше недоступны")

            requester_remaining_intervals = self._swap_subtract_intervals(
                requester_window_intervals,
                current_requested_intervals
            )
            target_remaining_intervals = self._swap_subtract_intervals(
                target_window_intervals,
                current_target_intervals
            )

            if self._swap_interval_sets_overlap(target_remaining_intervals, current_requested_intervals):
                if target_sign:
                    raise ValueError("Нельзя принять запрос: выбранные вами смены не покрывают интервал обмена")
                raise ValueError("Нельзя принять запрос: у вас уже есть смена в этом интервале")

            requester_next_intervals = self._merge_break_intervals([
                *(requester_remaining_intervals or []),
                *(current_target_intervals or [])
            ])
            target_next_intervals = self._merge_break_intervals([
                *(target_remaining_intervals or []),
                *(current_requested_intervals or [])
            ])

            # Важно: swap может затронуть "соседние" дни даже если сам интервал
            # заканчивается ровно в 00:00. Пример: смена 17:00-02:00 и передача 17:00-00:00.
            # В этом случае остаток 00:00-02:00 должен сохраниться на следующий день.
            # Раньше это терялось из-за определения "следующего дня" только по interval_end_min > 1440.

            window_offsets = [-1, 0, 1]
            requester_save_day_map = self._swap_serialize_intervals_to_day_map(
                requester_next_intervals,
                swap_date_obj,
                allowed_day_offsets=window_offsets
            )
            target_save_day_map = self._swap_serialize_intervals_to_day_map(
                target_next_intervals,
                swap_date_obj,
                allowed_day_offsets=window_offsets
            )

            def _shift_signature(shifts):
                sign = []
                for seg in (shifts or []):
                    if not isinstance(seg, dict):
                        continue
                    start_val = seg.get('start')
                    end_val = seg.get('end')
                    if not start_val or not end_val:
                        continue
                    sign.append(f"{start_val}|{end_val}")
                sign.sort()
                return sign

            day_candidates = [
                (prev_date_obj, prev_date_str),
                (swap_date_obj, swap_date_str),
                (next_date_obj, next_date_str)
            ]

            requester_affected_dates = []
            target_affected_dates = []
            # Plain replacements should leave an explicit day off instead of an empty day cell.
            requester_day_off_dates = []
            for day_obj, day_key in day_candidates:
                before_req_shifts = requester_day_map.get(day_key) or []
                after_req_shifts = requester_save_day_map.get(day_key) or []
                before_req = _shift_signature(before_req_shifts)
                after_req = _shift_signature(after_req_shifts)
                if before_req != after_req:
                    requester_affected_dates.append(day_obj)
                if is_replacement_request and before_req_shifts and not after_req_shifts:
                    requester_day_off_dates.append(day_obj)

                before_tgt = _shift_signature(target_day_map.get(day_key) or [])
                after_tgt = _shift_signature(target_save_day_map.get(day_key) or [])
                if before_tgt != after_tgt:
                    target_affected_dates.append(day_obj)

            # Снимки перерывов обеих сторон до очистки дней: перерывы, которые уже
            # отсижены к моменту передачи часов, переносятся как есть — их нельзя
            # ни подвинуть, ни списать со второй стороны.
            swap_now = self._almaty_now()
            swap_direction_keys = {}
            swap_breaks_snapshots = {}
            for day_obj in requester_affected_dates:
                swap_breaks_snapshots[(int(requester_operator_id), day_obj.strftime('%Y-%m-%d'))] = (
                    self._load_day_shift_breaks_tx(cursor, requester_operator_id, day_obj)
                )
            for day_obj in target_affected_dates:
                swap_breaks_snapshots[(int(target_operator_id), day_obj.strftime('%Y-%m-%d'))] = (
                    self._load_day_shift_breaks_tx(cursor, target_operator_id, day_obj)
                )

            # Обмен меняет графики обоих операторов, а инициатор действия один —
            # тот, кто согласовал заявку. В истории у обеих сторон будет он.
            audit_actor = self._schedule_change_actor(
                cursor, actor_id=responder_operator_id, source='swap'
            )
            audit_before = self._snapshot_schedule_days_tx(
                cursor,
                [(int(requester_operator_id), day_obj) for day_obj in requester_affected_dates]
                + [(int(target_operator_id), day_obj) for day_obj in target_affected_dates]
                + [(int(requester_operator_id), day_obj) for day_obj in requester_day_off_dates]
            )

            for day_obj in requester_affected_dates:
                self._clear_day_schedule_tx(cursor, requester_operator_id, day_obj)
            for day_obj in target_affected_dates:
                self._clear_day_schedule_tx(cursor, target_operator_id, day_obj)

            def _save_swap_segments(op_id, affected_dates, save_day_map):
                for day_value in affected_dates:
                    day_key_value = day_value.strftime('%Y-%m-%d')
                    for seg in (save_day_map.get(day_key_value) or []):
                        seg_start_obj = self._normalize_schedule_time(seg.get('start'), 'start_time')
                        seg_end_obj = self._normalize_schedule_time(seg.get('end'), 'end_time')
                        self._save_shift_tx(
                            cursor=cursor,
                            operator_id=op_id,
                            shift_date=day_value,
                            start_time=seg_start_obj,
                            end_time=seg_end_obj,
                            shift_type=seg.get('shift_type'),
                            # Будущие перерывы раскладываем по правилам после обмена,
                            # уже прошедшие остаются у того, кто их отсидел.
                            breaks=None,
                            now=swap_now,
                            day_breaks_snapshot=swap_breaks_snapshots.get((int(op_id), day_key_value)),
                            extra_occupied=self._extra_occupied_from_day_snapshots(
                                cursor=cursor,
                                day_breaks_snapshots=swap_breaks_snapshots,
                                operator_id=op_id,
                                shift_date=day_value,
                                now=swap_now,
                                direction_key_cache=swap_direction_keys
                            )
                        )

            _save_swap_segments(requester_operator_id, requester_affected_dates, requester_save_day_map)
            _save_swap_segments(target_operator_id, target_affected_dates, target_save_day_map)

            for day_obj in requester_day_off_dates:
                self._set_day_off_tx(cursor, requester_operator_id, day_obj)

            self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)

            cursor.execute(
                """
                UPDATE work_shift_swap_requests
                SET status = 'accepted',
                    response_comment = %s,
                    responded_by = %s,
                    responded_at = CURRENT_TIMESTAMP,
                    accepted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (response_comment_norm, responder_operator_id, request_id)
            )
            all_affected_dates = sorted(set(requester_affected_dates + target_affected_dates))
            if all_affected_dates:
                self._recalculate_auto_daily_hours_tx(
                    cursor=cursor,
                    operator_ids=[requester_operator_id, target_operator_id],
                    start_date=all_affected_dates[0],
                    end_date=all_affected_dates[-1]
                )
            row = self._select_shift_swap_request_by_id_tx(cursor, request_id)
            return self._serialize_shift_swap_request_row(row)

    def swap_task_attachment_file(self, attachment_id, gcs_bucket, old_blob_path, new):
        """Подменяет файл вложения переведённым (task_photos.convert_legacy).

        Подмена — только если запись всё ещё указывает на старый файл: за время
        перевода её могли удалить вместе с задачей или перевести другим
        прогоном. False — подмены не было, и новый файл вызывающему не нужен.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE task_attachments
                SET file_name = %s, content_type = %s, file_size = %s,
                    gcs_blob_path = %s, thumb_blob_path = %s
                WHERE id = %s AND storage_type = 'gcs'
                  AND gcs_bucket = %s AND gcs_blob_path = %s
                RETURNING id
            """, (
                new['file_name'], new['content_type'], int(new['file_size']),
                new['gcs_blob_path'], new.get('thumb_blob_path'),
                int(attachment_id), gcs_bucket, old_blob_path,
            ))
            return cursor.fetchone() is not None
