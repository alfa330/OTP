"""База данных: графики.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _time_to_minutes,
    _UNSET,
    Any,
    contextmanager,
    date,
    datetime,
    defaultdict,
    Dict,
    dt_time,
    execute_values,
    Json,
    json,
    List,
    logging,
    math,
    normalize_calculation_model_code,
    normalize_role_value,
    Optional,
    re,
    SCHEDULE_AUTO_AGGREGATION_START_DATE,
    SCHEDULE_AUTO_FINE_RATE_PER_MINUTE,
    SCHEDULE_AUTO_FLAG_ALLOWED,
    SCHEDULE_AUTO_FLAG_CONFIRMED,
    SCHEDULE_AUTO_FLAG_PENDING,
    SCHEDULE_AUTO_FLAG_REJECTED,
    SCHEDULE_AUTO_TRAINING_STATUS_KEY,
    SCHEDULE_DISMISSAL_REASONS,
    SCHEDULE_SPECIAL_STATUS_META,
    SCHEDULE_STATUS_TO_USER_STATUS,
    SHIFT_BREAK_PLANNING_BUFFER_MINUTES,
    task_production,
    task_projects,
    timedelta,
    WORK_SHIFT_CHANGE_SOURCES,
    ZoneInfo,
)


class SchedulesMixin:
    def _parse_resource_schedule_date_value(self, value, field_name="date"):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value or '').strip()
        if not text:
            raise ValueError(f"{field_name.upper()}_REQUIRED")
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except Exception as exc:
            raise ValueError("INVALID_RESOURCE_SCHEDULE_DATE") from exc

    def _format_resource_schedule_clock(self, minutes):
        try:
            minute_value = int(round(float(minutes or 0)))
        except Exception:
            minute_value = 0
        normalized = minute_value % (24 * 60)
        return f"{normalized // 60:02d}:{normalized % 60:02d}"

    def _coerce_resource_schedule_direction_ids(self, value):
        if value is None:
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = [value]
        if not isinstance(value, (list, tuple, set)):
            value = [value]
        result = []
        seen = set()
        for item in value:
            try:
                direction_id = int(item)
            except Exception:
                continue
            if direction_id <= 0 or direction_id in seen:
                continue
            seen.add(direction_id)
            result.append(direction_id)
        return result

    def _resolve_resource_schedule_direction_context_tx(self, cursor, payload):
        safe_payload = payload or {}
        capacity = safe_payload.get("capacity") if isinstance(safe_payload.get("capacity"), dict) else {}
        meta = safe_payload.get("meta") if isinstance(safe_payload.get("meta"), dict) else {}
        explicit_name = (
            meta.get("directionName")
            or meta.get("direction_name")
            or safe_payload.get("directionName")
            or safe_payload.get("direction_name")
        )
        direction_ids = (
            capacity.get("selectedDirectionIds")
            or capacity.get("selected_direction_ids")
            or meta.get("selectedDirectionIds")
            or meta.get("selected_direction_ids")
            or safe_payload.get("selectedDirectionIds")
            or safe_payload.get("selected_direction_ids")
        )
        direction_ids = self._coerce_resource_schedule_direction_ids(direction_ids)
        if not direction_ids:
            cursor.execute("SELECT selected_direction_ids FROM resource_settings WHERE id = 1")
            settings_row = cursor.fetchone()
            direction_ids = self._coerce_resource_schedule_direction_ids(settings_row[0] if settings_row else [])

        directions = []
        if direction_ids:
            cursor.execute("""
                SELECT id, name
                FROM directions
                WHERE id = ANY(%s)
                ORDER BY array_position(%s::int[], id), name
            """, (direction_ids, direction_ids))
            directions = [
                {"id": int(row[0]), "name": row[1] or ""}
                for row in (cursor.fetchall() or [])
            ]

        direction_name = str(explicit_name or '').strip()
        if not direction_name and directions:
            direction_name = directions[0]["name"]
        direction_rules = self._get_work_schedule_break_rules_for_direction_tx(cursor, direction_name) if direction_name else []
        if not explicit_name and directions and not direction_rules:
            for item in directions[1:]:
                candidate_name = item.get("name") or ""
                candidate_rules = self._get_work_schedule_break_rules_for_direction_tx(cursor, candidate_name)
                if candidate_rules:
                    direction_name = candidate_name
                    direction_rules = candidate_rules
                    break
        return {
            "direction_id": directions[0]["id"] if directions else None,
            "direction_name": direction_name,
            "selected_direction_ids": direction_ids,
            "selected_directions": directions,
            "break_rules": direction_rules,
        }

    def _serialize_resource_saved_schedule_plan(self, plan_row, shift_rows):
        if not plan_row:
            return None
        meta = plan_row[9] if isinstance(plan_row[9], dict) else {}
        shifts_by_date = defaultdict(list)
        for row in shift_rows or []:
            date_key = row[2].strftime('%Y-%m-%d') if row[2] else None
            if date_key:
                shifts_by_date[date_key].append(self._serialize_resource_saved_schedule_shift_row(row))

        stored_days = meta.get("days") if isinstance(meta.get("days"), list) else []
        days = []
        if stored_days:
            for day in stored_days:
                if not isinstance(day, dict):
                    continue
                date_key = str(day.get("date") or "")
                days.append({
                    **day,
                    "shifts": shifts_by_date.pop(date_key, []),
                })
        for date_key in sorted(shifts_by_date.keys()):
            days.append({
                "date": date_key,
                "short": date_key[5:],
                "label": date_key,
                "coverage": [],
                "stats": {},
                "shifts": shifts_by_date[date_key],
            })

        return {
            "id": plan_row[0],
            "date_from": plan_row[1].strftime('%Y-%m-%d') if plan_row[1] else None,
            "date_to": plan_row[2].strftime('%Y-%m-%d') if plan_row[2] else None,
            "title": plan_row[3] or "",
            "selectedVariantKey": plan_row[4] or "",
            "includeIncidentUplift": bool(plan_row[5]),
            "summary": plan_row[6] or {},
            "capacity": plan_row[7] or {},
            "templates": plan_row[8] or [],
            "meta": {key: value for key, value in meta.items() if key != "days"},
            "days": days,
            "created_by": plan_row[10],
            "updated_by": plan_row[11],
            "created_at": plan_row[12].isoformat() if plan_row[12] else None,
            "updated_at": plan_row[13].isoformat() if plan_row[13] else None,
        }

    def get_resource_saved_schedule(self, date_from=None, date_to=None, plan_id=None,
                                    direction_mode='line'):
        mode = 'chat' if str(direction_mode or 'line').lower() == 'chat' else 'line'
        with self._get_cursor() as cursor:
            if plan_id:
                cursor.execute("""
                    SELECT id, date_from, date_to, title, selected_variant_key,
                           include_incident_uplift, summary, capacity, templates, meta,
                           created_by, updated_by, created_at, updated_at
                    FROM resource_saved_schedule_plans
                    WHERE id = %s AND archived_at IS NULL
                """, (int(plan_id),))
            else:
                start_date = self._parse_resource_schedule_date_value(date_from, "date_from")
                end_date = self._parse_resource_schedule_date_value(date_to or date_from, "date_to")
                # Планы линии и чата лежат в одной таблице и различаются только
                # направлением: без фильтра чат подхватил бы график линии на тот же период.
                cursor.execute("""
                    SELECT id, date_from, date_to, title, selected_variant_key,
                           include_incident_uplift, summary, capacity, templates, meta,
                           created_by, updated_by, created_at, updated_at
                    FROM resource_saved_schedule_plans
                    WHERE date_from = %s
                      AND date_to = %s
                      AND direction_mode = %s
                      AND archived_at IS NULL
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                """, (start_date, end_date, mode))
            plan_row = cursor.fetchone()
            if not plan_row:
                return None
            cursor.execute("""
                SELECT id, client_shift_id, shift_date, start_minute, end_minute,
                       start_time, end_time, duration_minutes, rate_min, label,
                       template_id, source, tone, overnight, meta, breaks
                FROM resource_saved_schedule_shifts
                WHERE plan_id = %s
                ORDER BY shift_date, start_minute, end_minute, id
            """, (plan_row[0],))
            shift_rows = cursor.fetchall() or []
            return self._serialize_resource_saved_schedule_plan(plan_row, shift_rows)

    def save_resource_saved_schedule(self, payload, updated_by=None, direction_mode='line'):
        mode = 'chat' if str(direction_mode or 'line').lower() == 'chat' else 'line'
        safe_payload = payload or {}
        days = safe_payload.get("days") if isinstance(safe_payload.get("days"), list) else []
        if not days:
            raise ValueError("RESOURCE_SCHEDULE_EMPTY")

        date_from = safe_payload.get("date_from") or days[0].get("date")
        date_to = safe_payload.get("date_to") or days[-1].get("date") or date_from
        start_date = self._parse_resource_schedule_date_value(date_from, "date_from")
        end_date = self._parse_resource_schedule_date_value(date_to, "date_to")
        if end_date < start_date:
            raise ValueError("INVALID_RESOURCE_SCHEDULE_PERIOD")

        summary = safe_payload.get("summary") if isinstance(safe_payload.get("summary"), dict) else {}
        capacity = safe_payload.get("capacity") if isinstance(safe_payload.get("capacity"), dict) else {}
        templates = safe_payload.get("templates") if isinstance(safe_payload.get("templates"), list) else []
        base_meta = safe_payload.get("meta") if isinstance(safe_payload.get("meta"), dict) else {}
        title = str(safe_payload.get("title") or "")[:500]
        variant_key = str(safe_payload.get("selected_variant_key") or safe_payload.get("selectedVariantKey") or "")[:120]
        include_incident_uplift = bool(safe_payload.get("include_incident_uplift") or safe_payload.get("includeIncidentUplift"))

        plan_id = None
        raw_plan_id = safe_payload.get("id") or safe_payload.get("plan_id") or safe_payload.get("planId")
        try:
            plan_id = int(raw_plan_id) if raw_plan_id else None
        except Exception:
            plan_id = None

        with self._get_cursor() as cursor:
            direction_context = self._resolve_resource_schedule_direction_context_tx(cursor, {
                **safe_payload,
                "capacity": capacity,
                "meta": base_meta,
            })
            shift_rows = self._normalize_resource_saved_schedule_shifts(
                days,
                direction_name=direction_context.get("direction_name"),
                direction_rules=direction_context.get("break_rules")
            )
            meta = {
                **base_meta,
                "days": days,
                "shiftCount": len(shift_rows),
                "savedAt": datetime.now().isoformat(),
                "breakDirectionId": direction_context.get("direction_id"),
                "breakDirectionName": direction_context.get("direction_name"),
                "selectedDirectionIds": direction_context.get("selected_direction_ids") or [],
                "selectedDirections": direction_context.get("selected_directions") or [],
                "breakRules": direction_context.get("break_rules") or [],
            }

            if plan_id:
                cursor.execute("""
                    SELECT id
                    FROM resource_saved_schedule_plans
                    WHERE id = %s AND archived_at IS NULL
                    FOR UPDATE
                """, (plan_id,))
                if not cursor.fetchone():
                    plan_id = None

            if not plan_id:
                cursor.execute("""
                    SELECT id
                    FROM resource_saved_schedule_plans
                    WHERE date_from = %s
                      AND date_to = %s
                      AND direction_mode = %s
                      AND archived_at IS NULL
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 1
                    FOR UPDATE
                """, (start_date, end_date, mode))
                existing = cursor.fetchone()
                plan_id = int(existing[0]) if existing else None

            if plan_id:
                cursor.execute("""
                    UPDATE resource_saved_schedule_plans
                    SET date_from = %s,
                        date_to = %s,
                        title = %s,
                        selected_variant_key = %s,
                        include_incident_uplift = %s,
                        summary = %s,
                        capacity = %s,
                        templates = %s,
                        meta = %s,
                        direction_mode = %s,
                        updated_by = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    start_date,
                    end_date,
                    title,
                    variant_key,
                    include_incident_uplift,
                    Json(summary),
                    Json(capacity),
                    Json(templates),
                    Json(meta),
                    mode,
                    updated_by,
                    plan_id,
                ))
            else:
                cursor.execute("""
                    INSERT INTO resource_saved_schedule_plans (
                        date_from, date_to, title, selected_variant_key,
                        include_incident_uplift, summary, capacity, templates,
                        meta, direction_mode, created_by, updated_by
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    start_date,
                    end_date,
                    title,
                    variant_key,
                    include_incident_uplift,
                    Json(summary),
                    Json(capacity),
                    Json(templates),
                    Json(meta),
                    mode,
                    updated_by,
                    updated_by,
                ))
                plan_id = int(cursor.fetchone()[0])

            cursor.execute("DELETE FROM resource_saved_schedule_shifts WHERE plan_id = %s", (plan_id,))
            if shift_rows:
                execute_values(
                    cursor,
                    """
                    INSERT INTO resource_saved_schedule_shifts (
                        plan_id, client_shift_id, shift_date, start_minute, end_minute,
                        start_time, end_time, duration_minutes, rate_min, label,
                        template_id, source, tone, overnight, breaks, meta
                    )
                    VALUES %s
                    """,
                    [
                        (
                            plan_id,
                            row["client_shift_id"],
                            row["shift_date"],
                            row["start_minute"],
                            row["end_minute"],
                            row["start_time"],
                            row["end_time"],
                            row["duration_minutes"],
                            row["rate_min"],
                            row["label"],
                            row["template_id"],
                            row["source"],
                            row["tone"],
                            row["overnight"],
                            Json(row["breaks"]),
                            Json(row["meta"]),
                        )
                        for row in shift_rows
                    ],
                )

            cursor.execute("""
                SELECT id, shift_date, start_time, end_time, rate_min, breaks
                FROM resource_saved_schedule_shifts
                WHERE plan_id = %s
                  AND COALESCE(meta->>'excludeFromAuction', 'false') <> 'true'
                ORDER BY shift_date, start_minute, end_minute, id
            """, (plan_id,))
            saved_shift_rows = cursor.fetchall() or []
            event = self._insert_shift_auction_test_event(cursor, "resource_schedule_saved", {
                "plan_id": plan_id,
                "count": len(saved_shift_rows),
                "date_from": start_date.strftime('%Y-%m-%d'),
                "date_to": end_date.strftime('%Y-%m-%d'),
                "updated_by": updated_by,
                "break_direction_name": direction_context.get("direction_name"),
            }, direction_mode=mode)

        return {
            "schedule": self.get_resource_saved_schedule(plan_id=plan_id),
            "auction_count": sum(1 for row in shift_rows if not bool((row.get("meta") or {}).get("excludeFromAuction"))),
            "event": event,
        }

    def _fill_supervisor_norm_hours_tx(self, cursor, month, user_ids):
        """Норма СВ за месяц — та же формула, что у операторов (auto_fill_norm_hours):
        рабочие дни × 8 × ставка. Пишется только там, где норма ещё ноль, чтобы
        не затереть норму, которую РОП поставил руками."""
        ids = sorted({int(v) for v in (user_ids or []) if v is not None})
        if not ids:
            return 0
        work_days = self._get_month_work_days(month)
        cursor.execute("""
            INSERT INTO work_hours (operator_id, month, norm_hours)
            SELECT u.id, %s, (%s::float * 8.0 * COALESCE(
                (SELECT wr.rate FROM work_hours wr
                 WHERE wr.operator_id = u.id AND wr.rate IS NOT NULL AND wr.month <= %s
                 ORDER BY wr.month DESC LIMIT 1),
                u.rate, 1.0))::float
            FROM users u
            WHERE u.id = ANY(%s)
            ON CONFLICT (operator_id, month) DO UPDATE
              SET norm_hours = EXCLUDED.norm_hours
              WHERE COALESCE(work_hours.norm_hours, 0) = 0
        """, (month, work_days, month, ids))
        return cursor.rowcount or 0

    def auto_fill_norm_hours(self, month):
        """
        Авто-подсчет `norm_hours` для всех операторов за указанный месяц (формат 'YYYY-MM'),
        где текущая `norm_hours` равна 0. Формула: рабочие дни * 8 * rate,
        где рабочие дни = округл(дни месяца / 7 * 5) — не календарные пн-пт.

        Возвращает словарь: {"month": month, "work_days": int, "processed": int}
        processed — число строк, на которые сработал INSERT/UPDATE (оценочно).
        """
        try:
            year, mon = map(int, month.split('-'))
        except Exception as e:
            raise ValueError("Invalid month format, expected YYYY-MM") from e

        work_days = self._get_month_work_days(f"{year}-{mon:02d}")

        with self._get_cursor() as cursor:
            # Вставляем/обновляем norm_hours для всех операторов.
            # Для операторов без строки в work_hours будет INSERT, для существующих с norm_hours=0 — UPDATE.
            cursor.execute("""
                INSERT INTO work_hours (operator_id, month, norm_hours)
                SELECT u.id, %s as month, (%s::float * 8.0 * COALESCE(
                    (SELECT wr.rate FROM work_hours wr
                     WHERE wr.operator_id = u.id AND wr.rate IS NOT NULL AND wr.month <= %s
                     ORDER BY wr.month DESC LIMIT 1),
                    u.rate, 1.0))::float
                FROM users u
                WHERE u.role = 'operator'
                ON CONFLICT (operator_id, month) DO UPDATE
                  SET norm_hours = EXCLUDED.norm_hours
                  WHERE COALESCE(work_hours.norm_hours, 0) = 0
            """, (month, work_days, month))

            # rowcount может быть не точен при ON CONFLICT, но даёт оценку затронутых строк
            processed = cursor.rowcount if cursor.rowcount is not None else 0

        return {"month": month, "work_days": work_days, "processed": int(processed)}

    @staticmethod
    def normalize_sip_domain(value) -> str:
        """Домен для сравнения: без пробелов и регистра. '' = общий из sip_config."""
        return str(value or '').strip().lower()

    @classmethod
    def normalize_icore_phone_variant(cls, value) -> str:
        """Неизвестный вариант — это online: телефон, спросивший ерунду, должен
        получить тот инсталлятор, который ему и нужен, а не 400."""
        variant = str(value or '').strip().lower()
        return variant if variant in cls.ICORE_PHONE_VARIANTS else 'online'

    def _get_full_rate_norm_hours(self, month: Optional[str]) -> float:
        return float(self._get_month_work_days(month) * 8.0)

    @staticmethod
    def _normalize_feedback_sla_date(value) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value

        text = str(value or '').strip()
        if not text:
            return None

        for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
            try:
                return datetime.strptime(text, fmt).date()
            except Exception:
                continue
        return None

    def _normalize_technical_issue_role(self, role_value):
        return normalize_role_value(role_value)

    def _normalize_direction_ids_json_payload(self, payload):
        if payload is None:
            return []
        parsed = payload
        if isinstance(payload, str):
            payload = payload.strip()
            if not payload:
                return []
            try:
                parsed = json.loads(payload)
            except Exception:
                return []
        if not isinstance(parsed, list):
            return []
        result = []
        seen = set()
        for item in parsed:
            try:
                direction_id = int(item)
            except (TypeError, ValueError):
                continue
            if direction_id <= 0 or direction_id in seen:
                continue
            seen.add(direction_id)
            result.append(direction_id)
        return result

    # ==================== Work Shifts Methods ====================
    
    def _normalize_schedule_date(self, value):
        if value is None:
            raise ValueError("shift_date is required")
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return datetime.strptime(value, '%Y-%m-%d').date()
        raise ValueError("Invalid date format, expected YYYY-MM-DD")

    def _normalize_schedule_time(self, value, field_name):
        if value is None:
            raise ValueError(f"{field_name} is required")
        if isinstance(value, dt_time):
            return value
        if isinstance(value, str):
            return datetime.strptime(value, '%H:%M').time()
        raise ValueError(f"Invalid {field_name} format, expected HH:MM")

    def _schedule_interval_minutes(self, start_time_value, end_time_value):
        if isinstance(start_time_value, dt_time):
            start_str = start_time_value.strftime('%H:%M')
        else:
            start_str = str(start_time_value)
        if isinstance(end_time_value, dt_time):
            end_str = end_time_value.strftime('%H:%M')
        else:
            end_str = str(end_time_value)

        start_min = _time_to_minutes(start_str)
        end_min = _time_to_minutes(end_str)
        if end_min <= start_min:
            end_min += 24 * 60
        return start_min, end_min

    def _normalize_direction_key(self, direction_name):
        return ' '.join(str(direction_name or '').strip().lower().split())

    def _normalize_calculation_model_code(self, model_code=None, direction_name=None):
        return normalize_calculation_model_code(model_code, direction_name)

    def _almaty_now(self):
        """
        «Сейчас» в рабочем часовом поясе. ZoneInfo без пакета tzdata кидает исключение,
        а эта функция стоит на пути каждого сохранения смены — поэтому есть запасной
        вариант: процесс и так живёт в Asia/Almaty (os.environ['TZ'] в начале модуля).
        """
        try:
            return datetime.now(ZoneInfo('Asia/Almaty'))
        except Exception:
            return datetime.now()

    def _extra_occupied_from_day_snapshots(self, cursor, day_breaks_snapshots, operator_id, shift_date,
                                           now=None, direction_key_cache=None):
        """
        Прошедшие перерывы коллег того же направления, которых в этот момент нет в базе.

        Пути, которые чистят день заранее (публикация аукциона, пакетные действия,
        импорт, обмен сменами), удаляют смены до планирования — замороженные перерывы
        коллег уезжают каскадом и перестают быть «занятым временем». Без этого коллеге
        поставят перерыв на то же время, а замороженный вернётся на своё место.

        day_breaks_snapshots: {(operator_id, 'YYYY-MM-DD'): [перерывы]}
        """
        if not day_breaks_snapshots:
            return []

        date_obj = self._normalize_schedule_date(shift_date)
        cache = direction_key_cache if direction_key_cache is not None else {}

        def _direction_key(op_id):
            op_id = int(op_id)
            if op_id not in cache:
                cache[op_id] = self._normalize_direction_key(
                    self._get_operator_direction_name_tx(cursor, op_id)
                )
            return cache[op_id]

        own_direction_key = _direction_key(operator_id)
        boundary = self._break_freeze_boundary_minutes(date_obj, now=now)
        result = []
        for key, snapshot_breaks in day_breaks_snapshots.items():
            try:
                snapshot_operator_id, snapshot_date = key
            except (TypeError, ValueError):
                continue
            if int(snapshot_operator_id) == int(operator_id):
                continue
            if self._normalize_schedule_date(snapshot_date) != date_obj:
                continue
            if _direction_key(snapshot_operator_id) != own_direction_key:
                continue
            for item in self._normalize_break_intervals_soft(snapshot_breaks):
                if int(item['start']) < boundary:
                    result.append(item)
        # Сливать нельзя: перерывы принадлежат разным операторам, а по ним считаются
        # и «одновременно не больше двух», и лимит пересечения пары перерывов.
        return sorted(result, key=lambda item: (int(item['start']), int(item['end'])))

    def _plan_recalc_pass(self, cursor, ordered_rows, planned_by_shift, frozen_by_shift, shift_meta,
                          day_breaks_snapshots, rules_for_direction, cross_gap_for_direction,
                          recalc_now, recalc_direction_keys, overlap_allowed_for_direction=None):
        """
        Один проход пересчёта по всем сменам: каждая раскладывается с оглядкой на
        текущий план коллег. Результат кладётся в planned_by_shift, база не трогается.
        overlap_allowed_for_direction — галочка «Разрешить пересечение»: у такого
        направления на коллег не оглядываемся вовсе.
        """
        for shift_id, operator_id, shift_date, start_time_value, end_time_value, direction_name in ordered_rows:
            start_min, end_min = self._schedule_interval_minutes(start_time_value, end_time_value)
            direction_rules = rules_for_direction(direction_name)
            boundary_min = self._break_freeze_boundary_minutes(
                shift_date,
                seg_start=start_min,
                now=recalc_now
            )
            planning_from_min = boundary_min + SHIFT_BREAK_PLANNING_BUFFER_MINUTES
            snapshot = day_breaks_snapshots.get((int(operator_id), shift_date.strftime('%Y-%m-%d'))) or []
            frozen_breaks, _, used_breaks = self._split_breaks_by_freeze_boundary(
                snapshot,
                planning_from_min,
                seg_start=start_min,
                seg_end=end_min
            )
            full_durations = self._pick_break_durations_for_shift(
                duration_minutes=max(0, end_min - start_min),
                direction_name=direction_name,
                direction_rules=direction_rules
            )
            if planning_from_min <= start_min and not used_breaks:
                window_start = start_min
                computed_breaks = self._place_break_durations_centered_minutes(
                    start_min,
                    end_min,
                    full_durations
                )
            else:
                window_start = max(start_min, planning_from_min)
                computed_breaks = self._place_break_durations_centered_minutes(
                    window_start,
                    end_min,
                    self._fit_break_durations_to_window(
                        window_start,
                        end_min,
                        self._remaining_break_durations_after_used(full_durations, used_breaks)
                    )
                )
            allow_overlap = (
                bool(overlap_allowed_for_direction(direction_name))
                if callable(overlap_allowed_for_direction) else None
            )
            if allow_overlap:
                extra_occupied = []
            else:
                extra_occupied = self._extra_occupied_from_day_snapshots(
                    cursor=cursor,
                    day_breaks_snapshots=day_breaks_snapshots,
                    operator_id=operator_id,
                    shift_date=shift_date,
                    now=recalc_now,
                    direction_key_cache=recalc_direction_keys
                )
                extra_occupied = list(extra_occupied) + self._planned_occupied_for_shift(
                    planned_by_shift=planned_by_shift,
                    operator_id=operator_id,
                    shift_date=shift_date,
                    direction_key=self._normalize_direction_key(direction_name),
                    shift_meta=shift_meta
                )
            adjusted_breaks = self._adjust_shift_breaks_against_occupied_tx(
                cursor=cursor,
                operator_id=operator_id,
                shift_date=shift_date,
                start_time=start_time_value,
                end_time=end_time_value,
                breaks=computed_breaks,
                cross_gap_minutes=cross_gap_for_direction(direction_name),
                frozen_breaks=frozen_breaks,
                planning_from_minutes=window_start,
                extra_occupied=extra_occupied,
                allow_overlap=allow_overlap
            )
            planned_by_shift[int(shift_id)] = [dict(item) for item in adjusted_breaks]
            frozen_by_shift[int(shift_id)] = [dict(item) for item in frozen_breaks]

    def _schedule_auto_normalize_flag_status(self, value):
        status = str(value or '').strip().lower()
        if status in SCHEDULE_AUTO_FLAG_ALLOWED:
            return status
        return None

    def _schedule_auto_clamp_range(self, start_date_obj, end_date_obj):
        if start_date_obj is None or end_date_obj is None:
            return None, None
        if end_date_obj < SCHEDULE_AUTO_AGGREGATION_START_DATE:
            return None, None
        return max(start_date_obj, SCHEDULE_AUTO_AGGREGATION_START_DATE), end_date_obj

    def _schedule_auto_total_minutes(self, intervals):
        total = 0.0
        for item in (intervals or []):
            start = float(item.get('start', 0))
            end = float(item.get('end', 0))
            if end > start:
                total += (end - start)
        return float(total)

    def _schedule_auto_seconds_to_display_minutes(self, seconds_value):
        seconds_int = max(0, int(seconds_value or 0))
        if seconds_int <= 0:
            return 0
        return int((seconds_int + 59) // 60)

    def _schedule_auto_subtract_intervals(self, source_intervals, blocked_intervals):
        source = self._merge_break_intervals(source_intervals or [])
        blocked = self._merge_break_intervals(blocked_intervals or [])
        if not source:
            return []
        if not blocked:
            return source

        current = source
        for block in blocked:
            b_start = int(block.get('start', 0))
            b_end = int(block.get('end', 0))
            if b_end <= b_start:
                continue
            next_items = []
            for seg in current:
                s_start = int(seg.get('start', 0))
                s_end = int(seg.get('end', 0))
                if s_end <= s_start:
                    continue
                if s_end <= b_start or s_start >= b_end:
                    next_items.append({'start': s_start, 'end': s_end})
                    continue
                if s_start < b_start:
                    next_items.append({'start': s_start, 'end': b_start})
                if s_end > b_end:
                    next_items.append({'start': b_end, 'end': s_end})
            current = self._merge_break_intervals(next_items)
            if not current:
                break
        return current

    def _schedule_auto_overlap_minutes(self, a_intervals, b_intervals):
        a_list = self._merge_break_intervals(a_intervals or [])
        b_list = self._merge_break_intervals(b_intervals or [])
        if not a_list or not b_list:
            return 0.0

        total = 0.0
        i = 0
        j = 0
        while i < len(a_list) and j < len(b_list):
            a = a_list[i]
            b = b_list[j]
            start = max(int(a['start']), int(b['start']))
            end = min(int(a['end']), int(b['end']))
            if end > start:
                total += (end - start)
            if int(a['end']) <= int(b['end']):
                i += 1
            else:
                j += 1
        return float(total)

    def _schedule_auto_intersect_interval_with_list(self, interval, intervals):
        start = int(interval.get('start', 0))
        end = int(interval.get('end', 0))
        if end <= start:
            return []
        result = []
        for seg in self._merge_break_intervals(intervals or []):
            seg_start = int(seg.get('start', 0))
            seg_end = int(seg.get('end', 0))
            if seg_end <= seg_start:
                continue
            overlap_start = max(start, seg_start)
            overlap_end = min(end, seg_end)
            if overlap_end > overlap_start:
                result.append({'start': overlap_start, 'end': overlap_end})
        return self._merge_break_intervals(result)

    def _schedule_auto_split_status_segment_by_day(self, status_date_value, start_at_value, end_at_value):
        """
        Разбивает статус-сегмент на дневные куски в секундах [0..86400].
        Корректно обрабатывает переход через полночь и интервалы, где end_at
        лежит в следующем календарном дне.
        """
        chunks = []

        if isinstance(start_at_value, datetime) and isinstance(end_at_value, datetime):
            if end_at_value <= start_at_value:
                return []

            cursor = start_at_value
            while cursor < end_at_value:
                day_start = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + timedelta(days=1)
                seg_end = end_at_value if end_at_value <= day_end else day_end
                if seg_end <= cursor:
                    break

                start_delta = (cursor - day_start).total_seconds()
                end_delta = (seg_end - day_start).total_seconds()
                start_sec = int(start_delta)
                end_sec = int(end_delta)
                if end_delta > float(end_sec):
                    end_sec += 1
                if end_sec <= start_sec and seg_end > cursor:
                    end_sec = min(86400, start_sec + 1)

                start_sec = max(0, min(86399, int(start_sec)))
                end_sec = max(1, min(86400, int(end_sec)))
                if end_sec > start_sec:
                    chunks.append({
                        'day': cursor.date(),
                        'start': int(start_sec),
                        'end': int(end_sec)
                    })
                cursor = seg_end

            if chunks:
                return chunks

        # Fallback для старых/нестандартных данных: якоримся на status_date.
        if not isinstance(status_date_value, date):
            return []

        if isinstance(start_at_value, datetime):
            start_sec = int((start_at_value.hour * 3600) + (start_at_value.minute * 60) + start_at_value.second)
        elif isinstance(start_at_value, dt_time):
            start_sec = int((start_at_value.hour * 3600) + (start_at_value.minute * 60) + start_at_value.second)
        else:
            return []

        if isinstance(end_at_value, datetime):
            end_sec = int((end_at_value.hour * 3600) + (end_at_value.minute * 60) + end_at_value.second)
            if end_at_value.microsecond > 0:
                end_sec += 1
        elif isinstance(end_at_value, dt_time):
            end_sec = int((end_at_value.hour * 3600) + (end_at_value.minute * 60) + end_at_value.second)
            if getattr(end_at_value, 'microsecond', 0) > 0:
                end_sec += 1
        else:
            return []

        start_sec = max(0, min(86399, start_sec))
        end_sec = max(1, min(86400, end_sec))
        if end_sec <= start_sec:
            end_sec = min(86400, start_sec + 1)

        if end_sec > start_sec:
            chunks.append({
                'day': status_date_value,
                'start': int(start_sec),
                'end': int(end_sec)
            })
        return chunks

    def _normalize_import_status_key(self, status_key_value):
        return ' '.join(str(status_key_value or '').strip().lower().split())

    def _schedule_auto_compact_status_key(self, status_key_value):
        key = str(status_key_value or '').strip().lower()
        if not key:
            return ''
        return re.sub(r'[\s._-]+', '', key)

    def _schedule_auto_is_tech_reason_status_key(self, status_key_value):
        compact_key = self._schedule_auto_compact_status_key(status_key_value)
        return compact_key in {'техпричина', 'techbreak', 'statustechbreak'}

    def _schedule_auto_is_late_excused_status_key(self, status_key_value):
        key = self._normalize_import_status_key(status_key_value)
        if not key:
            return False
        if key in (SCHEDULE_AUTO_TRAINING_STATUS_KEY, 'training', 'study'):
            return True
        return self._schedule_auto_is_tech_reason_status_key(key)

    def _schedule_auto_flag_status_for_minutes(self, status_value, minutes_value):
        minutes_int = max(0, int(minutes_value or 0))
        if minutes_int <= 0:
            return None
        normalized = self._schedule_auto_normalize_flag_status(status_value)
        return normalized or SCHEDULE_AUTO_FLAG_PENDING

    def _schedule_auto_flag_meta(self, flag_type):
        key = str(flag_type or '').strip().lower()
        if key == 'late':
            return {
                'reason': 'Опоздание',
                'label': 'Опоздание'
            }
        if key == 'early_leave':
            return {
                'reason': 'Опоздание',
                'label': 'Ранний уход'
            }
        if key == 'training':
            return {
                'reason': 'Тренинг',
                'label': 'Тренинг'
            }
        if key in ('technical_reason', 'tech_reason', 'tech', 'technical'):
            return {
                'reason': 'Тех.причина',
                'label': 'Тех.причина'
            }
        raise ValueError("Unsupported auto flag type")

    def _schedule_auto_build_fine_payload(self, flag_type, minutes_value):
        flag_key = str(flag_type or '').strip().lower()
        if flag_key in ('training', 'technical_reason', 'tech_reason', 'tech', 'technical'):
            raise ValueError("This auto flag must not be stored in daily_fines")
        meta = self._schedule_auto_flag_meta(flag_type)
        minutes_int = max(0, int(minutes_value or 0))
        rate_per_minute = float(SCHEDULE_AUTO_FINE_RATE_PER_MINUTE or 0.0)
        amount = round(float(minutes_int) * rate_per_minute, 2)
        comment = f"Автоагрегация: {meta['label']} {minutes_int} мин"
        return {
            'amount': float(amount),
            'reason': meta['reason'],
            'comment': comment
        }

    def _schedule_auto_sync_flag_fines_for_range_tx(self, cursor, operator_ids, start_date, end_date):
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        if not op_ids:
            return {'inserted': 0, 'updated': 0, 'deleted': 0}

        start_date_obj = self._normalize_schedule_date(start_date)
        end_date_obj = self._normalize_schedule_date(end_date)
        if end_date_obj < start_date_obj:
            start_date_obj, end_date_obj = end_date_obj, start_date_obj

        cursor.execute("""
            SELECT
                id,
                operator_id,
                day,
                COALESCE(late_minutes, 0),
                COALESCE(early_leave_minutes, 0),
                COALESCE(training_minutes, 0),
                COALESCE(technical_reason_minutes, 0),
                late_status,
                early_leave_status,
                training_status,
                technical_reason_status,
                late_fine_id,
                early_leave_fine_id,
                training_fine_id,
                technical_reason_fine_id
            FROM daily_hours
            WHERE operator_id = ANY(%s)
              AND day >= %s
              AND day <= %s
        """, (op_ids, start_date_obj, end_date_obj))
        rows = cursor.fetchall() or []
        if not rows:
            return {'inserted': 0, 'updated': 0, 'deleted': 0}

        inserted_count = 0
        updated_count = 0
        deleted_count = 0

        def sync_one_flag(daily_id, operator_id, day_value, flag_type, minutes_value, status_value, fine_id_value):
            nonlocal inserted_count, updated_count, deleted_count

            minutes_int = max(0, int(minutes_value or 0))
            status_norm = self._schedule_auto_flag_status_for_minutes(status_value, minutes_int)
            fine_id_norm = int(fine_id_value) if fine_id_value is not None else None

            flag_key = str(flag_type or '').strip().lower()
            # Штрафы из авто-флагов создаем только для нарушений дисциплины.
            # Тренинг подтверждается/отклоняется, но не должен попадать в daily_fines.
            supports_fine = flag_key in ('late', 'early', 'early_leave')
            should_keep_fine = supports_fine and (minutes_int > 0 and status_norm == SCHEDULE_AUTO_FLAG_CONFIRMED)
            if should_keep_fine:
                payload = self._schedule_auto_build_fine_payload(flag_type, minutes_int)
                if fine_id_norm is None:
                    cursor.execute("""
                        INSERT INTO daily_fines (daily_hours_id, operator_id, day, amount, reason, comment)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (
                        daily_id,
                        int(operator_id),
                        day_value,
                        float(payload['amount']),
                        payload['reason'],
                        payload['comment']
                    ))
                    fine_id_norm = int(cursor.fetchone()[0])
                    inserted_count += 1
                else:
                    cursor.execute("""
                        UPDATE daily_fines
                        SET daily_hours_id = %s,
                            operator_id = %s,
                            day = %s,
                            amount = %s,
                            reason = %s,
                            comment = %s
                        WHERE id = %s
                    """, (
                        daily_id,
                        int(operator_id),
                        day_value,
                        float(payload['amount']),
                        payload['reason'],
                        payload['comment'],
                        fine_id_norm
                    ))
                    if int(cursor.rowcount or 0) > 0:
                        updated_count += 1
                    else:
                        cursor.execute("""
                            INSERT INTO daily_fines (daily_hours_id, operator_id, day, amount, reason, comment)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            RETURNING id
                        """, (
                            daily_id,
                            int(operator_id),
                            day_value,
                            float(payload['amount']),
                            payload['reason'],
                            payload['comment']
                        ))
                        fine_id_norm = int(cursor.fetchone()[0])
                        inserted_count += 1
            else:
                if fine_id_norm is not None:
                    cursor.execute("DELETE FROM daily_fines WHERE id = %s", (fine_id_norm,))
                    if int(cursor.rowcount or 0) > 0:
                        deleted_count += 1
                fine_id_norm = None

            return status_norm, fine_id_norm

        for row in rows:
            (
                daily_id,
                operator_id,
                day_value,
                late_minutes,
                early_leave_minutes,
                training_minutes,
                technical_reason_minutes,
                late_status,
                early_status,
                training_status,
                technical_reason_status,
                late_fine_id,
                early_fine_id,
                training_fine_id,
                technical_reason_fine_id
            ) = row

            late_status_norm, late_fine_id_norm = sync_one_flag(
                daily_id=daily_id,
                operator_id=operator_id,
                day_value=day_value,
                flag_type='late',
                minutes_value=late_minutes,
                status_value=late_status,
                fine_id_value=late_fine_id
            )
            early_status_norm, early_fine_id_norm = sync_one_flag(
                daily_id=daily_id,
                operator_id=operator_id,
                day_value=day_value,
                flag_type='early_leave',
                minutes_value=early_leave_minutes,
                status_value=early_status,
                fine_id_value=early_fine_id
            )
            training_status_norm, training_fine_id_norm = sync_one_flag(
                daily_id=daily_id,
                operator_id=operator_id,
                day_value=day_value,
                flag_type='training',
                minutes_value=training_minutes,
                status_value=training_status,
                fine_id_value=training_fine_id
            )
            technical_reason_status_norm, technical_reason_fine_id_norm = sync_one_flag(
                daily_id=daily_id,
                operator_id=operator_id,
                day_value=day_value,
                flag_type='technical_reason',
                minutes_value=technical_reason_minutes,
                status_value=technical_reason_status,
                fine_id_value=technical_reason_fine_id
            )

            if (
                self._schedule_auto_normalize_flag_status(late_status) != late_status_norm
                or self._schedule_auto_normalize_flag_status(early_status) != early_status_norm
                or self._schedule_auto_normalize_flag_status(training_status) != training_status_norm
                or self._schedule_auto_normalize_flag_status(technical_reason_status) != technical_reason_status_norm
                or (int(late_fine_id) if late_fine_id is not None else None) != late_fine_id_norm
                or (int(early_fine_id) if early_fine_id is not None else None) != early_fine_id_norm
                or (int(training_fine_id) if training_fine_id is not None else None) != training_fine_id_norm
                or (int(technical_reason_fine_id) if technical_reason_fine_id is not None else None) != technical_reason_fine_id_norm
            ):
                cursor.execute("""
                    UPDATE daily_hours
                    SET late_status = %s,
                        early_leave_status = %s,
                        training_status = %s,
                        technical_reason_status = %s,
                        late_fine_id = %s,
                        early_leave_fine_id = %s,
                        training_fine_id = %s,
                        technical_reason_fine_id = %s,
                        created_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (
                    late_status_norm,
                    early_status_norm,
                    training_status_norm,
                    technical_reason_status_norm,
                    late_fine_id_norm,
                    early_fine_id_norm,
                    training_fine_id_norm,
                    technical_reason_fine_id_norm,
                    daily_id
                ))

        return {
            'inserted': int(inserted_count),
            'updated': int(updated_count),
            'deleted': int(deleted_count)
        }

    def _schedule_auto_flag_columns(self, flag_type):
        flag_key = str(flag_type or '').strip().lower()
        if flag_key == 'late':
            return ('late_minutes', 'late_status', 'late_fine_id')
        if flag_key in ('early', 'early_leave'):
            return ('early_leave_minutes', 'early_leave_status', 'early_leave_fine_id')
        if flag_key == 'training':
            return ('training_minutes', 'training_status', 'training_fine_id')
        if flag_key in ('technical_reason', 'tech_reason', 'tech', 'technical'):
            return ('technical_reason_minutes', 'technical_reason_status', 'technical_reason_fine_id')
        raise ValueError("Unsupported flag_type. Allowed: late, early_leave, training, technical_reason")

    def resolve_auto_schedule_flag(self, operator_id, day, flag_type, action):
        operator_id_int = int(operator_id)
        day_obj = self._normalize_schedule_date(day)
        _, status_col, _ = self._schedule_auto_flag_columns(flag_type)

        action_norm = str(action or '').strip().lower()
        if action_norm in ('confirm', 'confirmed'):
            target_status = SCHEDULE_AUTO_FLAG_CONFIRMED
        elif action_norm in ('reject', 'rejected'):
            target_status = SCHEDULE_AUTO_FLAG_REJECTED
        elif action_norm in ('pending', 'reset'):
            target_status = SCHEDULE_AUTO_FLAG_PENDING
        else:
            raise ValueError("Unsupported action. Allowed: confirm, reject, pending")

        with self._get_cursor() as cursor:
            self._recalculate_auto_daily_hours_tx(
                cursor=cursor,
                operator_ids=[operator_id_int],
                start_date=day_obj,
                end_date=day_obj
            )

            cursor.execute("""
                SELECT
                    id,
                    COALESCE(late_minutes, 0),
                    COALESCE(late_seconds, 0),
                    COALESCE(early_leave_minutes, 0),
                    COALESCE(early_leave_seconds, 0),
                    COALESCE(training_minutes, 0),
                    COALESCE(training_seconds, 0),
                    COALESCE(technical_reason_minutes, 0),
                    COALESCE(technical_reason_seconds, 0),
                    late_status,
                    early_leave_status,
                    training_status,
                    technical_reason_status
                FROM daily_hours
                WHERE operator_id = %s
                  AND day = %s
                FOR UPDATE
            """, (operator_id_int, day_obj))
            row = cursor.fetchone()
            if not row:
                raise ValueError("Нет агрегированных данных за выбранный день")

            (
                daily_id,
                late_minutes,
                late_seconds,
                early_leave_minutes,
                early_leave_seconds,
                training_minutes,
                training_seconds,
                technical_reason_minutes,
                technical_reason_seconds,
                late_status,
                early_status,
                training_status,
                technical_reason_status
            ) = row

            late_seconds_int = max(0, int(late_seconds or 0))
            early_seconds_int = max(0, int(early_leave_seconds or 0))
            training_seconds_int = max(0, int(training_seconds or 0))
            technical_reason_seconds_int = max(0, int(technical_reason_seconds or 0))

            late_minutes_int = max(
                max(0, int(late_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(late_seconds_int)
            )
            early_minutes_int = max(
                max(0, int(early_leave_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(early_seconds_int)
            )
            training_minutes_int = max(
                max(0, int(training_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(training_seconds_int)
            )
            technical_reason_minutes_int = max(
                max(0, int(technical_reason_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(technical_reason_seconds_int)
            )

            status_map = {
                'late_status': self._schedule_auto_flag_status_for_minutes(late_status, late_minutes_int),
                'early_leave_status': self._schedule_auto_flag_status_for_minutes(early_status, early_minutes_int),
                'training_status': self._schedule_auto_flag_status_for_minutes(training_status, training_minutes_int),
                'technical_reason_status': self._schedule_auto_flag_status_for_minutes(technical_reason_status, technical_reason_minutes_int)
            }
            seconds_by_status_col = {
                'late_status': late_seconds_int,
                'early_leave_status': early_seconds_int,
                'training_status': training_seconds_int,
                'technical_reason_status': technical_reason_seconds_int
            }

            current_seconds = seconds_by_status_col.get(status_col, 0)
            if target_status == SCHEDULE_AUTO_FLAG_CONFIRMED and current_seconds <= 0:
                raise ValueError("Нельзя подтвердить флаг без рассчитанного времени")

            if current_seconds <= 0:
                status_map[status_col] = None
            else:
                status_map[status_col] = target_status

            cursor.execute("""
                UPDATE daily_hours
                SET late_status = %s,
                    early_leave_status = %s,
                    training_status = %s,
                    technical_reason_status = %s,
                    created_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                status_map['late_status'],
                status_map['early_leave_status'],
                status_map['training_status'],
                status_map['technical_reason_status'],
                daily_id
            ))

            fine_sync_result = self._schedule_auto_sync_flag_fines_for_range_tx(
                cursor=cursor,
                operator_ids=[operator_id_int],
                start_date=day_obj,
                end_date=day_obj
            )
            self._aggregate_month_from_daily_tx(cursor, operator_id_int, day_obj.strftime('%Y-%m'))

            flags_map = self._load_auto_schedule_flags_for_operators(
                cursor=cursor,
                operator_ids=[operator_id_int],
                start_date_obj=day_obj,
                end_date_obj=day_obj
            )
            day_key = day_obj.strftime('%Y-%m-%d')
            day_flags = (flags_map.get(operator_id_int) or {}).get(day_key, {})

            resolved_minutes = int(day_flags.get({
                'late_status': 'lateMinutes',
                'early_leave_status': 'earlyLeaveMinutes',
                'training_status': 'trainingMinutes',
                'technical_reason_status': 'technicalReasonMinutes'
            }.get(status_col, ''), 0))

            return {
                'operator_id': operator_id_int,
                'day': day_key,
                'flag_type': str(flag_type),
                'status': day_flags.get({
                    'late_status': 'lateStatus',
                    'early_leave_status': 'earlyLeaveStatus',
                    'training_status': 'trainingStatus',
                    'technical_reason_status': 'technicalReasonStatus'
                }.get(status_col, ''), None),
                'minutes': resolved_minutes,
                'day_flags': day_flags,
                'fine_sync': fine_sync_result
            }

    def _normalize_schedule_status_code(self, status_code):
        code = str(status_code or '').strip()
        if code not in SCHEDULE_SPECIAL_STATUS_META:
            raise ValueError("Invalid status_code")
        return code

    def _serialize_schedule_status_period(self, row):
        period_id, operator_id, status_code, start_date_value, end_date_value, dismissal_reason, comment = row[:7]
        is_blacklist = bool(row[7]) if len(row) > 7 else False
        exclude_from_tenure = bool(row[8]) if len(row) > 8 else False
        meta = SCHEDULE_SPECIAL_STATUS_META.get(status_code, {})
        return {
            'excludeFromTenure': exclude_from_tenure,
            'id': int(period_id),
            'operatorId': int(operator_id),
            'statusCode': status_code,
            'label': meta.get('label') or status_code,
            'kind': meta.get('kind') or 'absence',
            'startDate': start_date_value.strftime('%Y-%m-%d') if start_date_value else None,
            'endDate': end_date_value.strftime('%Y-%m-%d') if end_date_value else None,
            'dismissalReason': dismissal_reason,
            'comment': comment or '',
            'isBlacklist': is_blacklist,
            'is_blacklist': is_blacklist
        }

    def _load_schedule_status_periods_for_operators(self, cursor, operator_ids, start_date_obj=None, end_date_obj=None):
        operator_ids = [int(v) for v in (operator_ids or []) if v is not None]
        result = {
            op_id: {
                'scheduleStatusPeriods': [],
                'scheduleStatusDays': {}
            }
            for op_id in operator_ids
        }
        if not operator_ids:
            return result

        query = """
            SELECT id, operator_id, status_code, start_date, end_date, dismissal_reason, comment, COALESCE(is_blacklist, FALSE)
            FROM operator_schedule_status_periods
            WHERE operator_id = ANY(%s)
        """
        params = [operator_ids]

        if start_date_obj and end_date_obj:
            query += " AND start_date <= %s AND COALESCE(end_date, DATE '9999-12-31') >= %s"
            params.extend([end_date_obj, start_date_obj])
        elif start_date_obj:
            query += " AND COALESCE(end_date, DATE '9999-12-31') >= %s"
            params.append(start_date_obj)
        elif end_date_obj:
            query += " AND start_date <= %s"
            params.append(end_date_obj)

        query += " ORDER BY operator_id, start_date, id"
        cursor.execute(query, params)
        rows = cursor.fetchall()

        should_expand_days = bool(start_date_obj and end_date_obj)
        for row in rows:
            op_id = int(row[1])
            bucket = result.get(op_id)
            if bucket is None:
                continue

            period_payload = self._serialize_schedule_status_period(row)
            bucket['scheduleStatusPeriods'].append(period_payload)

            if not should_expand_days:
                continue

            period_start = row[3]
            period_end = row[4] or end_date_obj
            overlap_start = max(period_start, start_date_obj)
            overlap_end = min(period_end, end_date_obj)
            if overlap_end < overlap_start:
                continue

            day_value = {
                'id': period_payload['id'],
                'statusCode': period_payload['statusCode'],
                'label': period_payload['label'],
                'kind': period_payload['kind'],
                'startDate': period_payload['startDate'],
                'endDate': period_payload['endDate'],
                'dismissalReason': period_payload['dismissalReason'],
                'comment': period_payload['comment'],
                # Окно дня в «Графиках» берёт статус отсюда: без признака ЧС-увольнение
                # выглядело обычным — без плашки «ЧС» и кнопки «Убрать из ЧС».
                'isBlacklist': period_payload['isBlacklist']
            }
            cur_day = overlap_start
            while cur_day <= overlap_end:
                bucket['scheduleStatusDays'][cur_day.strftime('%Y-%m-%d')] = day_value
                cur_day += timedelta(days=1)

        return result

    def _attach_schedule_status_periods_to_operators(self, cursor, operators_map, operator_ids, start_date_obj=None, end_date_obj=None):
        statuses_map = self._load_schedule_status_periods_for_operators(
            cursor=cursor,
            operator_ids=operator_ids,
            start_date_obj=start_date_obj,
            end_date_obj=end_date_obj
        )
        for op_id in (operator_ids or []):
            target = operators_map.get(op_id)
            if target is None:
                continue
            data = statuses_map.get(op_id) or {}
            target['scheduleStatusPeriods'] = data.get('scheduleStatusPeriods', [])
            target['scheduleStatusDays'] = data.get('scheduleStatusDays', {})

    def _load_auto_schedule_flags_for_operators(self, cursor, operator_ids, start_date_obj=None, end_date_obj=None):
        op_ids = [int(v) for v in (operator_ids or []) if v is not None]
        result = {op_id: {} for op_id in op_ids}
        if not op_ids:
            return result

        query = """
            SELECT
                operator_id,
                day,
                COALESCE(late_minutes, 0),
                COALESCE(late_seconds, 0),
                COALESCE(early_leave_minutes, 0),
                COALESCE(early_leave_seconds, 0),
                COALESCE(overtime_minutes, 0),
                COALESCE(overtime_seconds, 0),
                COALESCE(training_minutes, 0),
                COALESCE(training_seconds, 0),
                COALESCE(technical_reason_minutes, 0),
                COALESCE(technical_reason_seconds, 0),
                COALESCE(no_phone_minutes, 0),
                COALESCE(no_phone_seconds, 0),
                late_status,
                early_leave_status,
                training_status,
                technical_reason_status,
                late_fine_id,
                early_leave_fine_id,
                training_fine_id,
                technical_reason_fine_id
            FROM daily_hours
            WHERE operator_id = ANY(%s)
              AND (
                    COALESCE(late_minutes, 0) > 0
                 OR COALESCE(late_seconds, 0) > 0
                 OR COALESCE(early_leave_minutes, 0) > 0
                 OR COALESCE(early_leave_seconds, 0) > 0
                 OR COALESCE(overtime_minutes, 0) > 0
                 OR COALESCE(overtime_seconds, 0) > 0
                 OR COALESCE(training_minutes, 0) > 0
                 OR COALESCE(training_seconds, 0) > 0
                 OR COALESCE(technical_reason_minutes, 0) > 0
                 OR COALESCE(technical_reason_seconds, 0) > 0
                 OR COALESCE(no_phone_minutes, 0) > 0
                 OR COALESCE(no_phone_seconds, 0) > 0
                 OR late_status IS NOT NULL
                 OR early_leave_status IS NOT NULL
                 OR training_status IS NOT NULL
                 OR technical_reason_status IS NOT NULL
              )
        """
        params = [op_ids]

        if start_date_obj and end_date_obj:
            query += " AND day >= %s AND day <= %s"
            params.extend([start_date_obj, end_date_obj])
        elif start_date_obj:
            query += " AND day >= %s"
            params.append(start_date_obj)
        elif end_date_obj:
            query += " AND day <= %s"
            params.append(end_date_obj)

        query += " ORDER BY operator_id, day"
        cursor.execute(query, params)
        rows = cursor.fetchall() or []

        for row in rows:
            (
                operator_id,
                day_value,
                late_minutes,
                late_seconds,
                early_leave_minutes,
                early_leave_seconds,
                overtime_minutes,
                overtime_seconds,
                training_minutes,
                training_seconds,
                technical_reason_minutes,
                technical_reason_seconds,
                no_phone_minutes,
                no_phone_seconds,
                late_status,
                early_status,
                training_status,
                technical_reason_status,
                late_fine_id,
                early_fine_id,
                training_fine_id,
                technical_reason_fine_id
            ) = row
            op_id = int(operator_id)
            day_key = day_value.strftime('%Y-%m-%d') if hasattr(day_value, 'strftime') else str(day_value or '')
            if not day_key:
                continue

            late_seconds_int = max(0, int(late_seconds or 0))
            early_leave_seconds_int = max(0, int(early_leave_seconds or 0))
            overtime_seconds_int = max(0, int(overtime_seconds or 0))
            training_seconds_int = max(0, int(training_seconds or 0))
            technical_reason_seconds_int = max(0, int(technical_reason_seconds or 0))
            no_phone_seconds_int = max(0, int(no_phone_seconds or 0))

            # Для совместимости с историческими данными берем максимум из старых минут
            # и минут, рассчитанных по секундам.
            late_minutes_int = max(
                max(0, int(late_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(late_seconds_int)
            )
            early_leave_minutes_int = max(
                max(0, int(early_leave_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(early_leave_seconds_int)
            )
            overtime_minutes_int = max(
                max(0, int(overtime_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(overtime_seconds_int)
            )
            training_minutes_int = max(
                max(0, int(training_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(training_seconds_int)
            )
            technical_reason_minutes_int = max(
                max(0, int(technical_reason_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(technical_reason_seconds_int)
            )
            no_phone_minutes_int = max(
                max(0, int(no_phone_minutes or 0)),
                self._schedule_auto_seconds_to_display_minutes(no_phone_seconds_int)
            )

            late_status_norm = self._schedule_auto_flag_status_for_minutes(late_status, late_minutes_int)
            early_status_norm = self._schedule_auto_flag_status_for_minutes(early_status, early_leave_minutes_int)
            training_status_norm = self._schedule_auto_flag_status_for_minutes(training_status, training_minutes_int)
            technical_reason_status_norm = self._schedule_auto_flag_status_for_minutes(technical_reason_status, technical_reason_minutes_int)

            has_late = late_minutes_int > 0 and late_status_norm != SCHEDULE_AUTO_FLAG_REJECTED
            has_early_leave = early_leave_minutes_int > 0 and early_status_norm != SCHEDULE_AUTO_FLAG_REJECTED
            has_training = training_minutes_int > 0 and training_status_norm != SCHEDULE_AUTO_FLAG_REJECTED
            has_technical_reason = technical_reason_minutes_int > 0 and technical_reason_status_norm != SCHEDULE_AUTO_FLAG_REJECTED
            has_no_phone = no_phone_minutes_int > 0
            has_pending = (
                (late_status_norm == SCHEDULE_AUTO_FLAG_PENDING and late_minutes_int > 0)
                or (early_status_norm == SCHEDULE_AUTO_FLAG_PENDING and early_leave_minutes_int > 0)
                or (training_status_norm == SCHEDULE_AUTO_FLAG_PENDING and training_minutes_int > 0)
                or (technical_reason_status_norm == SCHEDULE_AUTO_FLAG_PENDING and technical_reason_minutes_int > 0)
            )

            result.setdefault(op_id, {})[day_key] = {
                'lateMinutes': late_minutes_int,
                'lateSeconds': late_seconds_int,
                'earlyLeaveMinutes': early_leave_minutes_int,
                'earlyLeaveSeconds': early_leave_seconds_int,
                'overtimeMinutes': overtime_minutes_int,
                'overtimeSeconds': overtime_seconds_int,
                'trainingMinutes': training_minutes_int,
                'trainingSeconds': training_seconds_int,
                'technicalReasonMinutes': technical_reason_minutes_int,
                'technicalReasonSeconds': technical_reason_seconds_int,
                'noPhoneMinutes': no_phone_minutes_int,
                'noPhoneSeconds': no_phone_seconds_int,
                'lateStatus': late_status_norm,
                'earlyLeaveStatus': early_status_norm,
                'trainingStatus': training_status_norm,
                'technicalReasonStatus': technical_reason_status_norm,
                'lateFineId': int(late_fine_id) if late_fine_id is not None else None,
                'earlyLeaveFineId': int(early_fine_id) if early_fine_id is not None else None,
                'trainingFineId': int(training_fine_id) if training_fine_id is not None else None,
                'technicalReasonFineId': int(technical_reason_fine_id) if technical_reason_fine_id is not None else None,
                'hasLate': bool(has_late),
                'hasEarlyLeave': bool(has_early_leave),
                'hasTraining': bool(has_training),
                'hasTechnicalReason': bool(has_technical_reason),
                'hasNoPhone': bool(has_no_phone),
                'hasDefect': bool(has_late or has_early_leave or has_training or has_technical_reason or has_no_phone),
                'hasOvertime': bool(overtime_minutes_int > 0),
                'hasPending': bool(has_pending)
            }

        return result

    def _attach_auto_schedule_flags_to_operators(self, cursor, operators_map, operator_ids, start_date_obj=None, end_date_obj=None):
        flags_map = self._load_auto_schedule_flags_for_operators(
            cursor=cursor,
            operator_ids=operator_ids,
            start_date_obj=start_date_obj,
            end_date_obj=end_date_obj
        )
        for op_id in (operator_ids or []):
            target = operators_map.get(op_id)
            if target is None:
                continue
            target['aggregatedScheduleFlagsDays'] = flags_map.get(int(op_id), {})

    def _sync_user_statuses_from_schedule_periods_tx(self, cursor, operator_ids=None, as_of_date=None):
        """
        Синхронизировать users.status по активным периодным статусам на дату.
        Активные периодные статусы имеют приоритет; при их отсутствии временные статусные значения
        (bs/sick_leave/annual_leave) сбрасываются в working.
        """
        as_of_date_obj = self._normalize_schedule_date(as_of_date) if as_of_date else datetime.now().date()

        if operator_ids is None:
            # Операторы — и все, кому период ставили в «Графиках работы»: с
            # 09.11.2026 график ведут по любой роли, и отпуск бухгалтера иначе
            # не вернулся бы в «работает» по окончании — общий проход его бы не
            # видел. Людей без единого периода не трогаем: их статус ведёт
            # карточка, а не график.
            cursor.execute("""
                SELECT id
                FROM users
                WHERE role = 'operator'
                UNION
                SELECT DISTINCT operator_id
                FROM operator_schedule_status_periods
                WHERE operator_id IS NOT NULL
            """)
            target_operator_ids = [int(row[0]) for row in cursor.fetchall()]
        else:
            target_operator_ids = [int(v) for v in operator_ids if v is not None]

        if not target_operator_ids:
            return 0

        cursor.execute("""
            SELECT id, status, role
            FROM users
            WHERE id = ANY(%s)
        """, (target_operator_ids,))
        role_rows = cursor.fetchall()
        current_status_map = {int(row[0]): (row[1] or 'working') for row in role_rows}
        if not current_status_map:
            return 0
        # Кого общий проход (operator_ids=None) ведёт только по временным
        # статусам: не-операторы. Увольнение и возврат из него у них — только при
        # явном сохранении периода (операция над конкретным человеком), а не
        # побочным эффектом прохода: на проде 09.11.2026 у действующей учётки СВ
        # висел открытый период «Увольнение» с марта, и проход молча закрыл бы ей
        # вход в портал.
        temporary_only_ids = set()
        if operator_ids is None:
            temporary_only_ids = {
                int(row[0]) for row in role_rows
                if str(row[2] or '').strip().lower() != 'operator'
            }

        cursor.execute("""
            SELECT DISTINCT ON (operator_id) operator_id, status_code
            FROM operator_schedule_status_periods
            WHERE operator_id = ANY(%s)
              AND start_date <= %s
              AND COALESCE(end_date, DATE '9999-12-31') >= %s
            ORDER BY operator_id, start_date DESC, id DESC
        """, (target_operator_ids, as_of_date_obj, as_of_date_obj))
        active_status_by_operator = {int(row[0]): str(row[1]) for row in cursor.fetchall()}

        cursor.execute("""
            SELECT DISTINCT operator_id
            FROM operator_schedule_status_periods
            WHERE operator_id = ANY(%s)
              AND status_code = 'dismissal'
        """, (target_operator_ids,))
        has_dismissal_history = {int(row[0]) for row in cursor.fetchall()}

        updated_count = 0
        temporary_schedule_statuses = {'bs', 'sick_leave', 'annual_leave', 'dismissal'}
        for op_id, current_status in current_status_map.items():
            current_status_norm = str(current_status or 'working')
            active_schedule_status = active_status_by_operator.get(op_id)
            desired_status = None

            if active_schedule_status:
                desired_status = SCHEDULE_STATUS_TO_USER_STATUS.get(active_schedule_status, active_schedule_status)
            else:
                if current_status_norm in temporary_schedule_statuses:
                    desired_status = 'working'
                elif current_status_norm == 'fired' and op_id in has_dismissal_history:
                    desired_status = 'working'

            if op_id in temporary_only_ids and (
                desired_status == 'fired' or current_status_norm in ('fired', 'dismissal')
            ):
                continue

            if not desired_status or desired_status == current_status_norm:
                continue

            cursor.execute("""
                UPDATE users
                SET status = %s
                WHERE id = %s
            """, (desired_status, op_id))
            cursor.execute("""
                INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
                VALUES (%s, %s, %s, %s, %s)
            """, (op_id, None, 'status', current_status_norm, desired_status))
            updated_count += 1

        return updated_count

    def sync_user_statuses_from_schedule_periods(self, operator_ids=None, as_of_date=None):
        """
        Публичный wrapper для фонового/ручного запуска синхронизации users.status
        по периодным статусам графика.
        """
        with self._get_cursor() as cursor:
            return self._sync_user_statuses_from_schedule_periods_tx(
                cursor=cursor,
                operator_ids=operator_ids,
                as_of_date=as_of_date
            )

    def _iter_schedule_dates_inclusive(self, start_date_obj, end_date_obj):
        current = start_date_obj
        while current <= end_date_obj:
            yield current
            current = current + timedelta(days=1)

    # ------------------------------------------------------------------
    # История изменений графика
    # ------------------------------------------------------------------
    #
    # Пишем не построчно по work_shifts, а сравнением дня «до» и «после».
    # Причин две. Первая: правка смены физически выглядит как удаление старой
    # строки и вставка новой (см. _save_shift_tx), а публикация аукциона стирает
    # день целиком и собирает заново — построчный аудит выдавал бы пару
    # «удалена + добавлена» на каждую смену при каждой перепубликации, даже когда
    # ничего не изменилось. Вторая: у смены нет устойчивого идентификатора —
    # SERIAL id меняется при каждой перезаписи, и на фронт он вообще не доезжает.
    # Сравнение по значениям (начало, конец, вид) даёт ровно те записи, которые
    # человек и считает изменением.

    def _schedule_change_actor(self, cursor, actor_id=None, source='supervisor', actor_name=None):
        """Кто и откуда правит. Имя и роль запоминаем копией — чтобы запись читалась
        и после переименования автора, и после его увольнения."""
        try:
            resolved_id = int(actor_id) if actor_id is not None else None
        except (TypeError, ValueError):
            resolved_id = None

        name = str(actor_name or '').strip()
        role = ''
        if resolved_id is not None:
            cursor.execute("SELECT name, role FROM users WHERE id = %s", (resolved_id,))
            row = cursor.fetchone()
            if row:
                if not name:
                    name = str(row[0] or '').strip()
                role = normalize_role_value(row[1]) or ''

        source_norm = str(source or '').strip() or 'system'
        if source_norm not in WORK_SHIFT_CHANGE_SOURCES:
            source_norm = 'system'

        return {
            'id': resolved_id,
            'name': name[:255],
            'role': str(role or '')[:32],
            'source': source_norm
        }

    def _normalize_schedule_day_keys(self, keys):
        normalized = []
        seen = set()
        for item in (keys or []):
            if item is None:
                continue
            operator_id, day = item
            try:
                operator_id = int(operator_id)
            except (TypeError, ValueError):
                continue
            try:
                day_obj = self._normalize_schedule_date(day)
            except (TypeError, ValueError):
                continue
            key = (operator_id, day_obj)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(key)
        return normalized

    def _snapshot_schedule_days_tx(self, cursor, keys):
        """Состояние дней (смены + признак выходного) двумя запросами на всю пачку."""
        normalized = self._normalize_schedule_day_keys(keys)
        if not normalized:
            return {}

        snapshot = {key: {'shifts': [], 'day_off': False} for key in normalized}
        operator_ids = sorted({operator_id for operator_id, _ in normalized})
        dates = sorted({day for _, day in normalized})

        cursor.execute("""
            SELECT operator_id, shift_date, start_time, end_time, shift_type
            FROM work_shifts
            WHERE operator_id = ANY(%s) AND shift_date = ANY(%s)
        """, (operator_ids, dates))
        for operator_id, day, start_time, end_time, shift_type in (cursor.fetchall() or []):
            bucket = snapshot.get((int(operator_id), day))
            if bucket is None:
                continue
            bucket['shifts'].append((start_time, end_time, self._normalize_work_shift_type(shift_type)))

        cursor.execute("""
            SELECT operator_id, day_off_date
            FROM days_off
            WHERE operator_id = ANY(%s) AND day_off_date = ANY(%s)
        """, (operator_ids, dates))
        for operator_id, day in (cursor.fetchall() or []):
            bucket = snapshot.get((int(operator_id), day))
            if bucket is not None:
                bucket['day_off'] = True

        for bucket in snapshot.values():
            bucket['shifts'].sort(key=lambda item: (item[0], item[1]))
        return snapshot

    def _schedule_days_with_records_tx(self, cursor, operator_id, start_date, end_date=None):
        """Дни периода, где вообще есть что менять. Открытый период (увольнение)
        иначе пришлось бы снимать «до конца времён»."""
        operator_id = int(operator_id)
        start_obj = self._normalize_schedule_date(start_date)
        end_obj = self._normalize_schedule_date(end_date) if end_date is not None else None

        if end_obj is None:
            cursor.execute("""
                SELECT shift_date FROM work_shifts
                WHERE operator_id = %s AND shift_date >= %s
                UNION
                SELECT day_off_date FROM days_off
                WHERE operator_id = %s AND day_off_date >= %s
            """, (operator_id, start_obj, operator_id, start_obj))
        else:
            cursor.execute("""
                SELECT shift_date FROM work_shifts
                WHERE operator_id = %s AND shift_date BETWEEN %s AND %s
                UNION
                SELECT day_off_date FROM days_off
                WHERE operator_id = %s AND day_off_date BETWEEN %s AND %s
            """, (operator_id, start_obj, end_obj, operator_id, start_obj, end_obj))
        return [(operator_id, row[0]) for row in (cursor.fetchall() or [])]

    def _diff_schedule_day(self, operator_id, day, before_state, after_state, actor):
        before_map = {(item[0], item[1]): item[2] for item in (before_state or {}).get('shifts') or []}
        after_map = {(item[0], item[1]): item[2] for item in (after_state or {}).get('shifts') or []}

        entries = []

        # Границы те же, поменялся только вид смены (обычная / практика / телефонная).
        for key in sorted(set(before_map) & set(after_map)):
            if before_map[key] != after_map[key]:
                entries.append((
                    'changed', key[0], key[1], key[0], key[1], after_map[key], before_map[key]
                ))

        removed = sorted(set(before_map) - set(after_map))
        added = sorted(set(after_map) - set(before_map))
        pairs, rest_removed, rest_added = self._pair_shift_changes(removed, added)

        for previous_key, next_key in pairs:
            entries.append((
                'changed', next_key[0], next_key[1], previous_key[0], previous_key[1],
                after_map[next_key], before_map[previous_key]
            ))
        for key in rest_added:
            entries.append(('added', key[0], key[1], None, None, after_map[key], None))
        for key in rest_removed:
            entries.append(('removed', None, None, key[0], key[1], None, before_map[key]))

        before_day_off = bool((before_state or {}).get('day_off'))
        after_day_off = bool((after_state or {}).get('day_off'))
        if before_day_off != after_day_off:
            entries.append((
                'day_off_set' if after_day_off else 'day_off_cleared',
                None, None, None, None, None, None
            ))

        # Первичное внесение или правка — решает сводка, здесь только факт.
        day_was_empty = not before_map and not before_day_off

        return [
            (
                int(operator_id), day, action, actor['source'],
                start_time, end_time, prev_start_time, prev_end_time,
                shift_type, prev_shift_type, actor['id'], actor['name'], actor.get('role') or '',
                day_was_empty
            )
            for action, start_time, end_time, prev_start_time, prev_end_time, shift_type, prev_shift_type
            in entries
        ]

    def _record_schedule_day_changes_tx(self, cursor, before, actor):
        if not before or not actor:
            return 0

        after = self._snapshot_schedule_days_tx(cursor, before.keys())
        rows = []
        for (operator_id, day), before_state in before.items():
            after_state = after.get((operator_id, day)) or {'shifts': [], 'day_off': False}
            rows.extend(self._diff_schedule_day(operator_id, day, before_state, after_state, actor))

        if not rows:
            return 0

        execute_values(cursor, """
            INSERT INTO work_shift_changes (
                operator_id, shift_date, action, source,
                start_time, end_time, prev_start_time, prev_end_time,
                shift_type, prev_shift_type, actor_id, actor_name, actor_role,
                day_was_empty
            )
            VALUES %s
        """, rows)
        self._glb_reopen_attendance_days_tx(cursor, rows)
        return len(rows)

    @contextmanager
    def _schedule_change_audit(self, cursor, keys, actor_id=None, source='supervisor', actor_name=None):
        """Обернуть правку графика: снимок до, операция, запись отличий после.
        Всё внутри той же транзакции — история не может разойтись с графиком."""
        actor = self._schedule_change_actor(
            cursor, actor_id=actor_id, source=source, actor_name=actor_name
        )
        before = self._snapshot_schedule_days_tx(cursor, keys)
        yield
        self._record_schedule_day_changes_tx(cursor, before, actor)

    def get_schedule_change_report_subscription(self, user_id) -> Optional[bool]:
        """Включена ли сводка об изменениях графика.

        None — пользователя нет или подписываться ему нельзя. Пустого профиля
        достаточно: строку в admin_profiles заводит только запись.
        """
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT COALESCE(ap.schedule_change_report_enabled, FALSE)
                  FROM users u
                  LEFT JOIN admin_profiles ap ON ap.user_id = u.id
                 WHERE u.id = %%s
                   AND (%s)
                 LIMIT 1
            """ % self.SCHEDULE_CHANGE_REPORT_SUBSCRIBER_SQL, (uid,))
            row = cursor.fetchone()
            return bool(row[0]) if row else None

    def set_schedule_change_report_subscription(self, user_id, enabled) -> Optional[bool]:
        """Включить/выключить сводку.

        Право проверяется и на записи, а не только на чтении: UPDATE молча
        ничего не изменил бы, но INSERT в admin_profiles прошёл бы — и
        настройка появилась бы у того, кому она не положена.
        """
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT 1 FROM users u
                 WHERE u.id = %%s AND (%s) LIMIT 1
            """ % self.SCHEDULE_CHANGE_REPORT_SUBSCRIBER_SQL, (uid,))
            if not cursor.fetchone():
                return None

            cursor.execute("""
                INSERT INTO admin_profiles (user_id, schedule_change_report_enabled)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE
                   SET schedule_change_report_enabled = EXCLUDED.schedule_change_report_enabled
                RETURNING schedule_change_report_enabled
            """, (uid, bool(enabled)))
            row = cursor.fetchone()
            return bool(row[0]) if row else None

    @staticmethod
    def _schedule_change_report_scope_row(role, headed_ids, headed_names):
        """Область одного получателя по правилу раздела (см. комментарий выше)."""
        role_norm = normalize_role_value(role)
        department_ids = [int(value) for value in (headed_ids or []) if value is not None]
        department_names = sorted(str(value) for value in (headed_names or []) if value)
        # Супер-админ видит всё даже будучи главой отдела: так же считает
        # _is_global_admin_requester, и сводка не должна расходиться с экраном.
        if department_ids and role_norm != 'super_admin':
            return {
                'scope': 'department',
                'department_ids': department_ids,
                'department_names': department_names,
                'scope_label': ', '.join(department_names) or 'Отдел главы',
            }
        return {
            'scope': 'global',
            'department_ids': None,
            'department_names': [],
            'scope_label': 'Все отделы',
        }

    def get_schedule_change_report_recipients(self) -> List[Dict[str, Any]]:
        """Кому уходит суточная сводка об изменениях графика.

        Без привязанного Telegram получателя в списке нет — отправлять некуда.
        Уволенный тоже: увольнение не снимает ни telegram_id, ни роль 'admin',
        и без фильтра человек продолжал бы получать сводку по отделу, из
        которого ушёл.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    u.id,
                    u.name,
                    u.telegram_id,
                    LOWER(COALESCE(u.role, '')) AS role_norm,
                    COALESCE(
                        ARRAY_AGG(DISTINCT d.id ORDER BY d.id) FILTER (WHERE d.id IS NOT NULL),
                        ARRAY[]::INTEGER[]
                    ) AS headed_ids,
                    COALESCE(
                        ARRAY_AGG(DISTINCT d.name) FILTER (WHERE d.name IS NOT NULL),
                        ARRAY[]::VARCHAR[]
                    ) AS headed_names
                  FROM users u
                  JOIN admin_profiles ap ON ap.user_id = u.id
                  LEFT JOIN departments d
                    ON d.head_user_id = u.id
                   AND COALESCE(d.is_active, TRUE) = TRUE
                 WHERE u.telegram_id IS NOT NULL
                   -- 'dismissal' — такое же увольнение, как 'fired': триггер
                   -- гасит по нему сессии, и остальные рассылки портала
                   -- отсекают оба значения.
                   AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
                   AND LOWER(COALESCE(u.role, '')) <> 'trainer'
                   AND (
                       LOWER(COALESCE(u.role, '')) IN ('admin', 'super_admin', 'superadmin', 'super-admin', 'super admin')
                       OR d.id IS NOT NULL
                   )
                   AND COALESCE(ap.schedule_change_report_enabled, FALSE) = TRUE
                 GROUP BY u.id, u.name, u.telegram_id, u.role
                 ORDER BY u.name
            """)
            rows = cursor.fetchall() or []

        recipients = []
        for row in rows:
            recipient = {
                'id': int(row[0]),
                'name': row[1] or '',
                'telegram_id': int(row[2]),
            }
            recipient.update(self._schedule_change_report_scope_row(row[3], row[4], row[5]))
            recipients.append(recipient)
        return recipients

    def get_schedule_change_report_entries(self, window_start, window_end,
                                           department_ids=None) -> List[Dict[str, Any]]:
        """Записи журнала, ВНЕСЁННЫЕ в окно [window_start, window_end).

        window_start/window_end — naive-datetime в локальном времени Алматы:
        именно так лежит `changed_at` (DEFAULT `CURRENT_TIMESTAMP AT TIME ZONE
        'Asia/Almaty'`), а сессия Postgres на Render живёт в UTC. Границы
        поэтому считаются в Python и приходят параметрами.

        Окно по changed_at, а не по shift_date: три существующих читателя
        журнала (get_work_shift_change_operator_ids / _summary / _changes)
        фильтруют по дню ГРАФИКА и для суточной сводки не годятся ни один —
        правка, внесённая вчера на октябрь, им не видна.

        department_ids — отдел ОПЕРАТОРА, чей график меняли, а не автора
        правки: операторам отдела регулярно правит график глобальный админ, и
        такая правка обязана попасть в сводку главы этого отдела. None —
        все отделы.
        """
        if department_ids is not None:
            department_ids = [int(value) for value in department_ids if value is not None]
            if not department_ids:
                # Пустая область — честно пустая сводка, а не весь портал.
                return []

        with self._get_cursor() as cursor:
            # Группа — на день графика: оператора переводят между группами, и
            # правка на прошлую неделю относится к той группе, где он тогда был.
            # Если на тот день членства нет (день до зачисления), берётся
            # ближайшее — пустая ячейка сказала бы меньше, чем прежняя группа.
            cursor.execute("""
                SELECT """ + self.SCHEDULE_CHANGE_REPORT_COLUMNS + """,
                       COALESCE(grp.name, '')
                  FROM work_shift_changes c
                  JOIN users op ON op.id = c.operator_id
                  LEFT JOIN users actor ON actor.id = c.actor_id
                  LEFT JOIN LATERAL (
                        SELECT g.name
                          FROM group_operator_memberships m
                          JOIN groups g ON g.id = m.group_id
                         WHERE m.operator_id = c.operator_id
                         ORDER BY (m.start_date <= c.shift_date
                                   AND (m.end_date IS NULL OR m.end_date >= c.shift_date)) DESC,
                                  (m.end_date IS NULL) DESC,
                                  m.start_date DESC
                         LIMIT 1
                  ) grp ON TRUE
                 WHERE c.changed_at >= %s AND c.changed_at < %s
                   AND (%s::INTEGER[] IS NULL OR op.department_id = ANY(%s::INTEGER[]))
                 ORDER BY c.changed_at, c.id
            """, (window_start, window_end, department_ids, department_ids))
            rows = cursor.fetchall() or []

        entries = []
        for row in rows:
            entry = self._schedule_change_report_entry(row)
            entry['group_name'] = row[-1] or ''
            entries.append(entry)
        return entries

    @staticmethod
    def _schedule_change_report_entry(row):
        return {
            'id': int(row[0]),
            'operator_id': int(row[1]),
            'operator_name': row[2] or '',
            'shift_date': row[3],
            'action': row[4],
            'source': row[5],
            'start': row[6],
            'end': row[7],
            'prev_start': row[8],
            'prev_end': row[9],
            'shift_type': row[10],
            'prev_shift_type': row[11],
            'actor_id': int(row[12]) if row[12] is not None else None,
            'actor_name': row[13] or '',
            'actor_role': row[14] or '',
            'changed_at': row[15],
            'day_was_empty': row[16],
        }

    def get_schedule_change_report_day_states(self, keys, since):
        """Для отката «было / стало»: (текущее состояние дней, правки после since).

        keys — пары (operator_id, shift_date) из окна сводки. Возвращает
        ({(operator_id, shift_date): {'shifts': [(start, end, shift_type)],
        'day_off': bool}}, [строки журнала тех дней с changed_at >= since]).

        График и журнал читаются одним снимком базы (REPEATABLE READ): в READ
        COMMITTED у каждого запроса свой снимок, и правка, внесённая между ними,
        попала бы в журнал без графика или в график без журнала — «Стало» тихо
        показало бы чужое изменение или задвоенную смену.
        """
        pairs = set()
        for operator_id, shift_date in keys or []:
            try:
                pairs.add((int(operator_id), shift_date))
            except (TypeError, ValueError):
                continue
        if not pairs:
            return {}, []
        operator_ids = sorted({operator_id for operator_id, _ in pairs})
        dates = sorted({shift_date for _, shift_date in pairs})

        states = {pair: {'shifts': [], 'day_off': False} for pair in pairs}
        with self._get_cursor() as cursor:
            # Первой командой транзакции — иначе Postgres уровень не сменит.
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            # Запросы по всей паре списков, а не по каждому дню: пар за сутки
            # сотни. Лишние пересечения отсекаются здесь же.
            cursor.execute("""
                SELECT operator_id, shift_date, start_time, end_time, shift_type
                  FROM work_shifts
                 WHERE operator_id = ANY(%s) AND shift_date = ANY(%s)
            """, (operator_ids, dates))
            for operator_id, shift_date, start_time, end_time, shift_type in (cursor.fetchall() or []):
                state = states.get((int(operator_id), shift_date))
                if state is not None:
                    # Вид — как лежит: нормализация с исключением уронила бы
                    # сводку на одной испорченной строке графика.
                    state['shifts'].append((start_time, end_time, shift_type))

            cursor.execute("""
                SELECT operator_id, day_off_date
                  FROM days_off
                 WHERE operator_id = ANY(%s) AND day_off_date = ANY(%s)
            """, (operator_ids, dates))
            for operator_id, day_off_date in (cursor.fetchall() or []):
                state = states.get((int(operator_id), day_off_date))
                if state is not None:
                    state['day_off'] = True

            cursor.execute("""
                SELECT """ + self.SCHEDULE_CHANGE_REPORT_COLUMNS + """
                  FROM work_shift_changes c
                  JOIN users op ON op.id = c.operator_id
                  LEFT JOIN users actor ON actor.id = c.actor_id
                 WHERE c.changed_at >= %s
                   AND c.operator_id = ANY(%s) AND c.shift_date = ANY(%s)
                 ORDER BY c.changed_at, c.id
            """, (since, operator_ids, dates))
            later = [
                self._schedule_change_report_entry(row)
                for row in (cursor.fetchall() or [])
                if (int(row[1]), row[3]) in pairs
            ]
        return states, later

    def release_schedule_change_report_send(self, period_start, user_id) -> None:
        """Снять заявку — отправка не удалась, следующий прогон попробует снова."""
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return
        try:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    DELETE FROM schedule_change_report_sends
                     WHERE period_start = %s AND user_id = %s
                """, (period_start, uid))
        except Exception:
            # Не смогли снять — хуже пропущенной сводки только упавшая джоба.
            logging.exception("Не удалось снять заявку на сводку об изменениях графика")

    def prune_schedule_change_report_sends(self, keep_days: int = 400) -> int:
        """Подчистить журнал заявок: он нужен только чтобы отличить «уже
        отправляли» от «ещё нет», и расти вечно ему незачем."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.schedule_change_report_sends') IS NOT NULL")
            row = cursor.fetchone()
            if not (row and row[0]):
                return 0
            cursor.execute("""
                DELETE FROM schedule_change_report_sends
                 WHERE period_start < (CURRENT_DATE - %s::INTEGER)
            """, (int(keep_days),))
            return cursor.rowcount or 0

    def _schedule_days_with_worked_time_tx(self, cursor, operator_id, start_date, end_date=None):
        """
        Дни периода, за которые у оператора уже есть отработанное время.

        Такой день нельзя чистить вслед за периодом статуса: часы считаются как
        пересечение смены со статусами, поэтому снятие смены обнуляет уже
        отработанное. Именно так терялись часы за последнюю смену при увольнении
        в день этой смены.

        Отработанным день считается по прямым признакам: в учёте часов за день уже
        стоят часы или звонки, либо есть рабочие статусы, пересекающиеся со сменой
        (часы за день могли ещё не посчитаться — агрегация идёт следом за импортом).
        """
        operator_id = int(operator_id)
        start_date_obj = self._normalize_schedule_date(start_date)
        end_date_obj = self._normalize_schedule_date(end_date) if end_date is not None else None

        # Будущий день отработать нельзя, поэтому дальше сегодняшнего не смотрим:
        # у открытого увольнения диапазон иначе уходит «до конца времён».
        today = datetime.now().date()
        range_end = today if end_date_obj is None else min(end_date_obj, today)
        if range_end < start_date_obj:
            return set()

        cursor.execute("""
            SELECT DISTINCT shift_date
            FROM work_shifts
            WHERE operator_id = %s
              AND shift_date >= %s
              AND shift_date <= %s
            ORDER BY shift_date
        """, (operator_id, start_date_obj, range_end))
        shift_days = [row[0] for row in (cursor.fetchall() or []) if row and row[0] is not None]
        if not shift_days:
            return set()

        worked_days = set()
        cursor.execute("""
            SELECT day
            FROM daily_hours
            WHERE operator_id = %s
              AND day = ANY(%s)
              AND (COALESCE(work_time, 0) > 0 OR COALESCE(calls, 0) > 0)
        """, (operator_id, shift_days))
        for row in cursor.fetchall() or []:
            if row and row[0] is not None:
                worked_days.add(row[0])

        for day_value in shift_days:
            if day_value in worked_days:
                continue
            status_profile = self._status_profile_for_calculation_model(
                self._get_operator_calculation_model_tx(cursor, operator_id, as_of=day_value)
            )
            work_status_keys = sorted({
                str(key).strip().lower()
                for key in (status_profile.get('work') or set())
                if str(key or '').strip()
            })
            if not work_status_keys:
                continue
            # Ночная смена уходит за полночь, поэтому окно смены собираем как
            # пару отметок времени, а сегменты берём с запасом в сутки по обе стороны.
            cursor.execute("""
                SELECT 1
                FROM work_shifts ws
                JOIN operator_status_segments oss
                  ON oss.operator_id = ws.operator_id
                 AND oss.status_date >= ws.shift_date - 1
                 AND oss.status_date <= ws.shift_date + 1
                 AND LOWER(oss.status_key) = ANY(%s)
                 AND oss.start_at < (
                        ws.shift_date + ws.end_time
                        + CASE WHEN ws.end_time <= ws.start_time
                               THEN INTERVAL '1 day' ELSE INTERVAL '0 day' END
                     )
                 AND oss.end_at > (ws.shift_date + ws.start_time)
                WHERE ws.operator_id = %s
                  AND ws.shift_date = %s
                LIMIT 1
            """, (work_status_keys, operator_id, day_value))
            if cursor.fetchone():
                worked_days.add(day_value)

        return worked_days

    def _clear_day_schedule_tx(self, cursor, operator_id, target_date):
        """
        Очистка дня: удаляет все смены и снимает выходной на указанную дату.
        """
        operator_id = int(operator_id)
        date_obj = self._normalize_schedule_date(target_date)
        deleted_shifts = self._delete_all_shifts_for_day_tx(cursor, operator_id, date_obj)
        cursor.execute("""
            DELETE FROM days_off
            WHERE operator_id = %s AND day_off_date = %s
        """, (operator_id, date_obj))
        deleted_day_off_rows = int(cursor.rowcount or 0)
        return {
            'operator_id': operator_id,
            'date': date_obj.strftime('%Y-%m-%d'),
            'deleted_shifts': deleted_shifts,
            'deleted_day_off_rows': deleted_day_off_rows
        }

    def apply_work_schedule_bulk_actions(self, actions, actor_id=None, change_source='supervisor'):
        """
        Атомарное выполнение массовых действий по графикам в одном запросе.
        Поддерживаемые actions:
        - {action: "set_shift", operator_id, date, start, end, breaks?}
        - {action: "set_day_off", operator_id, date}
        - {action: "delete_shifts", operator_id, date}  # очистка дня: удаляет смены и снимает выходной
        """
        if not isinstance(actions, list) or not actions:
            raise ValueError("actions must be a non-empty list")

        # Пары «оператор + день», которые тронет пачка: снимок для истории снимаем
        # один раз на всю транзакцию, а не по действию — иначе соседние действия
        # по одному дню записались бы как несколько отдельных правок.
        audit_keys = [
            (item.get('operator_id'), item.get('date') or item.get('shift_date') or item.get('day_off_date'))
            for item in actions
            if isinstance(item, dict)
        ]

        summary = {
            'total': 0,
            'set_shift': 0,
            'set_day_off': 0,
            'delete_shifts': 0,
            'deleted_shift_rows': 0,
            'deleted_day_off_rows': 0,
            'shift_ids': [],
            'affected_operator_ids': []
        }
        affected_operator_ids = set()
        affected_range_by_operator = {}
        bulk_now = self._almaty_now()
        bulk_breaks_snapshots = {}
        bulk_direction_keys = {}

        def register_affected_day(op_id, day_obj):
            op_id_int = int(op_id)
            prev = affected_range_by_operator.get(op_id_int)
            if prev is None:
                affected_range_by_operator[op_id_int] = [day_obj, day_obj]
                return
            if day_obj < prev[0]:
                prev[0] = day_obj
            if day_obj > prev[1]:
                prev[1] = day_obj

        with self._get_cursor() as cursor:
            audit_actor = self._schedule_change_actor(cursor, actor_id=actor_id, source=change_source)
            audit_before = self._snapshot_schedule_days_tx(cursor, audit_keys)

            for item in actions:
                if not isinstance(item, dict):
                    raise ValueError("Each action must be an object")

                action_type = str(item.get('action') or item.get('type') or '').strip()
                operator_id = item.get('operator_id')
                date_value = item.get('date') or item.get('shift_date') or item.get('day_off_date')

                if not action_type:
                    raise ValueError("Missing action type")
                if operator_id is None:
                    raise ValueError("Missing operator_id")
                if not date_value:
                    raise ValueError("Missing date")

                operator_id = int(operator_id)
                date_obj = self._normalize_schedule_date(date_value)
                affected_operator_ids.add(operator_id)
                register_affected_day(operator_id, date_obj)

                if action_type == 'set_day_off':
                    result = self._set_day_off_tx(cursor, operator_id, date_obj)
                    summary['set_day_off'] += 1
                    summary['deleted_shift_rows'] += int(result.get('deleted_shifts') or 0)
                    summary['total'] += 1
                    continue

                if action_type == 'delete_shifts':
                    result = self._clear_day_schedule_tx(cursor, operator_id, date_obj)
                    summary['delete_shifts'] += 1
                    summary['deleted_shift_rows'] += int(result.get('deleted_shifts') or 0)
                    summary['deleted_day_off_rows'] += int(result.get('deleted_day_off_rows') or 0)
                    summary['total'] += 1
                    continue

                if action_type == 'set_shift':
                    start_time_obj = self._normalize_schedule_time(item.get('start') or item.get('start_time'), 'start')
                    end_time_obj = self._normalize_schedule_time(item.get('end') or item.get('end_time'), 'end')
                    resolved_breaks = self._resolve_breaks_for_day_replacement_tx(
                        cursor=cursor,
                        operator_id=operator_id,
                        shift_date=date_obj,
                        start_time=start_time_obj,
                        end_time=end_time_obj,
                        breaks=item.get('breaks')
                    )
                    # Снимок до очистки дня — прошедшие перерывы не переписываем.
                    day_key = date_obj.strftime('%Y-%m-%d')
                    bulk_breaks_snapshots[(operator_id, day_key)] = self._load_day_shift_breaks_tx(
                        cursor,
                        operator_id,
                        date_obj
                    )
                    deleted_count = self._delete_all_shifts_for_day_tx(cursor, operator_id, date_obj)
                    shift_id = self._save_shift_tx(
                        cursor=cursor,
                        operator_id=operator_id,
                        shift_date=date_obj,
                        start_time=start_time_obj,
                        end_time=end_time_obj,
                        breaks=resolved_breaks,
                        shift_type=item.get('shift_type'),
                        now=bulk_now,
                        day_breaks_snapshot=bulk_breaks_snapshots.get((operator_id, day_key)),
                        extra_occupied=self._extra_occupied_from_day_snapshots(
                            cursor=cursor,
                            day_breaks_snapshots=bulk_breaks_snapshots,
                            operator_id=operator_id,
                            shift_date=date_obj,
                            now=bulk_now,
                            direction_key_cache=bulk_direction_keys
                        )
                    )
                    summary['set_shift'] += 1
                    summary['deleted_shift_rows'] += deleted_count
                    summary['shift_ids'].append(int(shift_id))
                    summary['total'] += 1
                    continue

                raise ValueError(f"Unsupported action type: {action_type}")

            self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)

            for op_id, bounds in affected_range_by_operator.items():
                self._recalculate_auto_daily_hours_tx(
                    cursor=cursor,
                    operator_ids=[op_id],
                    start_date=bounds[0],
                    end_date=bounds[1]
                )

        summary['affected_operator_ids'] = sorted(affected_operator_ids)
        return summary

    def import_work_schedule_excel_entries(self, entries, actor_id=None, change_source='import'):
        """
        Импорт расписания из Excel-матрицы (ФИО x Даты), уже после парсинга.
        Каждый entry:
          {
            "operator_id": int,
            "date": "YYYY-MM-DD",
            "is_day_off": bool,
            "shifts": [{"start": "HH:MM", "end": "HH:MM", "breaks": [...]?}, ...]
          }
        Поведение: день заменяется целиком (очистка смен/выходного), затем вставляются смены или выходной.
        """
        if not isinstance(entries, list) or not entries:
            raise ValueError("entries must be a non-empty list")

        summary = {
            'days_processed': 0,
            'set_day_off_days': 0,
            'set_shift_days': 0,
            'shift_rows_saved': 0,
            'deleted_shift_rows': 0,
            'deleted_day_off_rows': 0,
            'blacklist_skipped_entries': [],
            'blacklist_skipped_total': 0,
            'affected_operator_ids': []
        }
        affected_operator_ids = set()
        affected_range_by_operator = {}
        import_now = self._almaty_now()
        import_breaks_snapshots = {}
        import_direction_keys = {}

        def register_affected_day(op_id, day_obj):
            op_id_int = int(op_id)
            prev = affected_range_by_operator.get(op_id_int)
            if prev is None:
                affected_range_by_operator[op_id_int] = [day_obj, day_obj]
                return
            if day_obj < prev[0]:
                prev[0] = day_obj
            if day_obj > prev[1]:
                prev[1] = day_obj

        def _is_blacklist_shift_block_error(error):
            text = str(error or '').strip().lower()
            return ('чс-увольнение' in text and 'прервать' in text and 'смен' in text)

        def _has_blacklist_dismissal_on_day_tx(cursor, operator_id, day_obj):
            cursor.execute(
                """
                SELECT 1
                FROM operator_schedule_status_periods
                WHERE operator_id = %s
                  AND status_code = 'dismissal'
                  AND COALESCE(is_blacklist, FALSE) = TRUE
                  AND start_date <= %s
                  AND COALESCE(end_date, DATE '9999-12-31') >= %s
                LIMIT 1
                """,
                (int(operator_id), day_obj, day_obj)
            )
            return cursor.fetchone() is not None

        def _append_blacklist_skipped_entry(operator_id, date_obj, operator_name=None):
            payload = {
                'operator_id': int(operator_id),
                'date': date_obj.strftime('%Y-%m-%d'),
                'reason': 'blacklist_dismissal'
            }
            name_text = str(operator_name or '').strip()
            if name_text:
                payload['name'] = name_text
            summary['blacklist_skipped_entries'].append(payload)
            summary['blacklist_skipped_total'] += 1

        with self._get_cursor() as cursor:
            audit_actor = self._schedule_change_actor(cursor, actor_id=actor_id, source=change_source)
            audit_before = self._snapshot_schedule_days_tx(cursor, [
                (item.get('operator_id'), item.get('date'))
                for item in entries
                if isinstance(item, dict)
            ])

            for item in entries:
                if not isinstance(item, dict):
                    raise ValueError("Each entry must be an object")

                operator_id = item.get('operator_id')
                date_value = item.get('date')
                is_day_off = bool(item.get('is_day_off'))
                shifts = item.get('shifts') or []
                operator_name = item.get('operator_name') or item.get('name')

                if operator_id is None:
                    raise ValueError("Missing operator_id in entry")
                if not date_value:
                    raise ValueError("Missing date in entry")
                if not is_day_off and not isinstance(shifts, list):
                    raise ValueError("shifts must be a list")
                if is_day_off and shifts:
                    raise ValueError("Entry cannot have shifts when is_day_off is true")

                operator_id = int(operator_id)
                date_obj = self._normalize_schedule_date(date_value)
                affected_operator_ids.add(operator_id)
                register_affected_day(operator_id, date_obj)

                if (not is_day_off) and shifts and _has_blacklist_dismissal_on_day_tx(cursor, operator_id, date_obj):
                    _append_blacklist_skipped_entry(operator_id, date_obj, operator_name)
                    continue

                # Снимок до очистки дня: импорт заменяет день целиком, а уже
                # прошедшие перерывы переписывать нельзя.
                import_day_key = date_obj.strftime('%Y-%m-%d')
                import_breaks_snapshots[(operator_id, import_day_key)] = self._load_day_shift_breaks_tx(
                    cursor,
                    operator_id,
                    date_obj
                )
                cleared = self._clear_day_schedule_tx(cursor, operator_id, date_obj)
                summary['deleted_shift_rows'] += int(cleared.get('deleted_shifts') or 0)
                summary['deleted_day_off_rows'] += int(cleared.get('deleted_day_off_rows') or 0)

                if is_day_off:
                    cursor.execute("""
                        INSERT INTO days_off (operator_id, day_off_date)
                        VALUES (%s, %s)
                        ON CONFLICT (operator_id, day_off_date) DO NOTHING
                    """, (operator_id, date_obj))
                    summary['set_day_off_days'] += 1
                    summary['days_processed'] += 1
                    continue

                if not shifts:
                    # Пустая очистка дня (если будет нужна) — считаем обработанным днем.
                    summary['days_processed'] += 1
                    continue

                # Сохраняем несколько смен в один день после полной очистки этого дня.
                blocked_by_blacklist = False
                for shift in shifts:
                    if not isinstance(shift, dict):
                        raise ValueError("Each shift must be an object")
                    start_time_obj = self._normalize_schedule_time(shift.get('start') or shift.get('start_time'), 'start')
                    end_time_obj = self._normalize_schedule_time(shift.get('end') or shift.get('end_time'), 'end')
                    try:
                        self._save_shift_tx(
                            cursor=cursor,
                            operator_id=operator_id,
                            shift_date=date_obj,
                            start_time=start_time_obj,
                            end_time=end_time_obj,
                            breaks=shift.get('breaks') if ('breaks' in shift) else None,
                            shift_type=shift.get('shift_type'),
                            now=import_now,
                            day_breaks_snapshot=import_breaks_snapshots.get((operator_id, import_day_key)),
                            extra_occupied=self._extra_occupied_from_day_snapshots(
                                cursor=cursor,
                                day_breaks_snapshots=import_breaks_snapshots,
                                operator_id=operator_id,
                                shift_date=date_obj,
                                now=import_now,
                                direction_key_cache=import_direction_keys
                            )
                        )
                    except ValueError as e:
                        if _is_blacklist_shift_block_error(e):
                            _append_blacklist_skipped_entry(operator_id, date_obj, operator_name)
                            rollback_cleared = self._clear_day_schedule_tx(cursor, operator_id, date_obj)
                            summary['deleted_shift_rows'] += int(rollback_cleared.get('deleted_shifts') or 0)
                            summary['deleted_day_off_rows'] += int(rollback_cleared.get('deleted_day_off_rows') or 0)
                            blocked_by_blacklist = True
                            break
                        raise
                    summary['shift_rows_saved'] += 1

                if blocked_by_blacklist:
                    continue

                summary['set_shift_days'] += 1
                summary['days_processed'] += 1

            self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)

            for op_id, bounds in affected_range_by_operator.items():
                self._recalculate_auto_daily_hours_tx(
                    cursor=cursor,
                    operator_ids=[op_id],
                    start_date=bounds[0],
                    end_date=bounds[1]
                )

        summary['affected_operator_ids'] = sorted(affected_operator_ids)
        return summary

    def save_schedule_status_period(
        self,
        operator_id,
        status_code,
        start_date,
        end_date=None,
        dismissal_reason=None,
        is_blacklist=False,
        comment=None,
        created_by=None
    ):
        """
        Сохранить период специального статуса оператора.
        Новая запись замещает пересекающиеся периоды (с обрезкой/разделением при необходимости).
        """
        with self._get_cursor() as cursor:
            return self._save_schedule_status_period_tx(
                cursor,
                operator_id,
                status_code,
                start_date,
                end_date=end_date,
                dismissal_reason=dismissal_reason,
                is_blacklist=is_blacklist,
                comment=comment,
                created_by=created_by
            )

    def _save_schedule_status_period_tx(
        self,
        cursor,
        operator_id,
        status_code,
        start_date,
        end_date=None,
        dismissal_reason=None,
        is_blacklist=False,
        comment=None,
        created_by=None,
        exclude_from_tenure=False
    ):
        """То же, что save_schedule_status_period, но в транзакции вызывающего.

        Нужен заявкам на отпуск и Б/С (задача #358): утверждение заявки и
        период в графике — одна транзакция, иначе сбой между ними оставил бы
        «утверждённую» заявку без отметки в графике или наоборот.

        exclude_from_tenure — период не идёт в стаж (длительный Б/С, п. 8 ТЗ).
        При разрезании пересекающегося периода его остатки флаг сохраняют.
        """
        operator_id = int(operator_id)
        status_code_norm = self._normalize_schedule_status_code(status_code)
        start_date_obj = self._normalize_schedule_date(start_date)

        comment_norm = None
        if comment is not None:
            comment_text = str(comment).strip()
            comment_norm = comment_text or None

        if status_code_norm == 'dismissal':
            end_date_obj = self._normalize_schedule_date(end_date) if end_date else None
            if end_date_obj is not None and end_date_obj < start_date_obj:
                raise ValueError("end_date must be >= start_date")
            dismissal_reason_norm = str(dismissal_reason or '').strip()
            if dismissal_reason_norm not in set(SCHEDULE_DISMISSAL_REASONS):
                raise ValueError("Invalid dismissal_reason")
            if isinstance(is_blacklist, str):
                is_blacklist_norm = str(is_blacklist).strip().lower() in ('1', 'true', 'yes', 'on')
            else:
                is_blacklist_norm = bool(is_blacklist)
            if is_blacklist_norm and end_date_obj is not None:
                raise ValueError("Blacklisted dismissal cannot have end_date")
            if not comment_norm:
                raise ValueError("Comment is required for dismissal")
        else:
            end_date_obj = self._normalize_schedule_date(end_date or start_date_obj)
            if end_date_obj < start_date_obj:
                raise ValueError("end_date must be >= start_date")
            dismissal_reason_norm = None
            is_blacklist_norm = False

        created_by_id = int(created_by) if created_by is not None else None
        infinite_date = date(9999, 12, 31)
        new_end_cmp = end_date_obj or infinite_date

        cursor.execute("""
            SELECT id, status_code, start_date, end_date, dismissal_reason, comment, created_by, COALESCE(is_blacklist, FALSE),
                   COALESCE(exclude_from_tenure, FALSE)
            FROM operator_schedule_status_periods
            WHERE operator_id = %s
              AND start_date <= %s
              AND COALESCE(end_date, DATE '9999-12-31') >= %s
            ORDER BY start_date, id
        """, (operator_id, new_end_cmp, start_date_obj))
        overlapping = cursor.fetchall()

        has_blacklist_dismissal_overlap = any(
            str(row[1]) == 'dismissal' and bool(row[7])
            for row in overlapping
        )
        if has_blacklist_dismissal_overlap:
            raise ValueError("ЧС-увольнение нельзя изменить или перекрыть другим статусом")

        for row in overlapping:
            (
                existing_id,
                existing_status_code,
                existing_start,
                existing_end,
                existing_dismissal_reason,
                existing_comment,
                existing_created_by,
                existing_is_blacklist,
                existing_exclude_from_tenure
            ) = row
            existing_end_cmp = existing_end or infinite_date

            left_exists = existing_start < start_date_obj
            right_exists = (end_date_obj is not None) and (existing_end_cmp > end_date_obj)

            if left_exists and right_exists:
                left_end = start_date_obj - timedelta(days=1)
                right_start = end_date_obj + timedelta(days=1)

                cursor.execute("""
                    UPDATE operator_schedule_status_periods
                    SET end_date = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (left_end, existing_id))

                cursor.execute("""
                    INSERT INTO operator_schedule_status_periods (
                        operator_id, status_code, start_date, end_date,
                        dismissal_reason, is_blacklist, comment, created_by, exclude_from_tenure,
                        created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (
                    operator_id,
                    existing_status_code,
                    right_start,
                    existing_end,
                    existing_dismissal_reason,
                    bool(existing_is_blacklist),
                    existing_comment,
                    existing_created_by,
                    bool(existing_exclude_from_tenure)
                ))
                continue

            if left_exists:
                left_end = start_date_obj - timedelta(days=1)
                cursor.execute("""
                    UPDATE operator_schedule_status_periods
                    SET end_date = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (left_end, existing_id))
                continue

            if right_exists:
                right_start = end_date_obj + timedelta(days=1)
                cursor.execute("""
                    UPDATE operator_schedule_status_periods
                    SET start_date = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                """, (right_start, existing_id))
                continue

            cursor.execute("""
                DELETE FROM operator_schedule_status_periods
                WHERE id = %s
            """, (existing_id,))

        cursor.execute("""
            INSERT INTO operator_schedule_status_periods (
                operator_id, status_code, start_date, end_date,
                dismissal_reason, is_blacklist, comment, created_by, exclude_from_tenure,
                created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id, operator_id, status_code, start_date, end_date, dismissal_reason, comment, is_blacklist,
                      exclude_from_tenure
        """, (
            operator_id,
            status_code_norm,
            start_date_obj,
            end_date_obj,
            dismissal_reason_norm,
            is_blacklist_norm,
            comment_norm,
            created_by_id,
            bool(exclude_from_tenure)
        ))
        saved_row = cursor.fetchone()
        # В истории графика отпуск/больничный/увольнение виден как снятие смен:
        # снимок берём только по дням, где действительно что-то стоит, — период
        # увольнения открытый, и «до конца времён» снимать нечего и незачем.
        audit_actor = self._schedule_change_actor(
            cursor, actor_id=created_by_id, source='status_period'
        )
        audit_before = self._snapshot_schedule_days_tx(
            cursor,
            self._schedule_days_with_records_tx(cursor, operator_id, start_date_obj, end_date_obj)
        )
        # Отработанный день статус не снимает: иначе увольнение (или задним
        # числом поставленный отпуск/больничный) обнуляет часы за смену,
        # которую человек уже отработал.
        worked_days = self._schedule_days_with_worked_time_tx(
            cursor=cursor,
            operator_id=operator_id,
            start_date=start_date_obj,
            end_date=end_date_obj
        )
        deleted_schedule = self._delete_shifts_for_period_tx(
            cursor=cursor,
            operator_id=operator_id,
            start_date=start_date_obj,
            end_date=end_date_obj,
            keep_days=worked_days
        )
        self._record_schedule_day_changes_tx(cursor, audit_before, audit_actor)
        recalc_end_date_obj = end_date_obj
        if recalc_end_date_obj is None:
            recalc_end_date_obj = start_date_obj
            if isinstance(deleted_schedule, dict):
                max_deleted_shift_date = deleted_schedule.get('max_deleted_shift_date')
                max_deleted_day_off_date = deleted_schedule.get('max_deleted_day_off_date')
                if isinstance(max_deleted_shift_date, date) and max_deleted_shift_date > recalc_end_date_obj:
                    recalc_end_date_obj = max_deleted_shift_date
                if isinstance(max_deleted_day_off_date, date) and max_deleted_day_off_date > recalc_end_date_obj:
                    recalc_end_date_obj = max_deleted_day_off_date
        self._recalculate_auto_daily_hours_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=start_date_obj,
            end_date=recalc_end_date_obj
        )
        self._sync_user_statuses_from_schedule_periods_tx(cursor, operator_ids=[operator_id])
        serialized = self._serialize_schedule_status_period(saved_row)
        # Дни, смены за которые остались: раздел должен объяснить, почему смена
        # уволенного никуда не делась, — по ней есть отработанные часы.
        serialized['keptWorkedDays'] = [
            day.strftime('%Y-%m-%d')
            for day in ((deleted_schedule or {}).get('kept_shift_days') or [])
        ]
        return serialized

    def delete_schedule_status_period(self, status_period_id, operator_id=None):
        """
        Удалить специальный статус-период по id.
        Если передан operator_id — удаление ограничивается этим оператором.
        """
        period_id = int(status_period_id)
        operator_id_norm = int(operator_id) if operator_id is not None else None

        with self._get_cursor() as cursor:
            if operator_id_norm is None:
                cursor.execute("""
                    SELECT id, operator_id, status_code, start_date, end_date, dismissal_reason, comment, COALESCE(is_blacklist, FALSE)
                    FROM operator_schedule_status_periods
                    WHERE id = %s
                """, (period_id,))
            else:
                cursor.execute("""
                    SELECT id, operator_id, status_code, start_date, end_date, dismissal_reason, comment, COALESCE(is_blacklist, FALSE)
                    FROM operator_schedule_status_periods
                    WHERE id = %s AND operator_id = %s
                """, (period_id, operator_id_norm))
            existing_row = cursor.fetchone()
            if not existing_row:
                return None
            if str(existing_row[2]) == 'dismissal' and bool(existing_row[7]):
                raise ValueError("ЧС-увольнение нельзя удалить")

            if operator_id_norm is None:
                cursor.execute("""
                    DELETE FROM operator_schedule_status_periods
                    WHERE id = %s
                    RETURNING id, operator_id, status_code, start_date, end_date, dismissal_reason, comment, is_blacklist
                """, (period_id,))
            else:
                cursor.execute("""
                    DELETE FROM operator_schedule_status_periods
                    WHERE id = %s AND operator_id = %s
                    RETURNING id, operator_id, status_code, start_date, end_date, dismissal_reason, comment, is_blacklist
                """, (period_id, operator_id_norm))

            row = cursor.fetchone()
            if not row:
                return None
            self._sync_user_statuses_from_schedule_periods_tx(cursor, operator_ids=[int(row[1])])
            return self._serialize_schedule_status_period(row)

    def _normalize_recruiting_item(self, item):
        payload = item or {}

        def to_text(value):
            if value is None:
                return None
            text = str(value).strip()
            return text or None

        page_found = None
        raw_page = payload.get("page_found")
        if raw_page is not None and str(raw_page).strip() != "":
            try:
                page_found = int(raw_page)
            except Exception:
                page_found = None

        return {
            "keyword_group": to_text(payload.get("keyword_group")),
            "keyword_query": to_text(payload.get("keyword_query")),
            "page_found": page_found,
            "title": to_text(payload.get("title")),
            "category": to_text(payload.get("category")),
            "experience": to_text(payload.get("experience")),
            "location": to_text(payload.get("location")),
            "salary": to_text(payload.get("salary")),
            "education": to_text(payload.get("education")),
            "published_at": to_text(payload.get("published_at")),
            "detail_url": to_text(payload.get("detail_url")),
        }

    @staticmethod
    def _normalize_survey_role(role):
        return normalize_role_value(role)

    @staticmethod
    def _parse_survey_schedule_value(value, error_code):
        """Границы окна теста: ISO / 'YYYY-MM-DD HH:MM' / datetime. Пусто → None."""
        if value in (None, ''):
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo is not None else value
        text = str(value).strip()
        if not text:
            return None
        normalized = text.replace('T', ' ')
        if normalized.endswith('Z'):
            normalized = normalized[:-1]
        for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(normalized, fmt)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(error_code)
        return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed

    def _normalize_survey_definition(
        self,
        *,
        title,
        description,
        assignment,
        questions,
        operator_ids,
        is_test=False,
        test_config=None,
        keep_question_ids=False
    ):
        """
        Единая проверка описания опроса/теста для создания и редактирования —
        иначе правила «правильных ответов» и «баллов» разъезжаются между путями.
        """
        title_norm = str(title or '').strip()
        if not title_norm:
            raise ValueError("SURVEY_TITLE_REQUIRED")
        if not isinstance(questions, list) or len(questions) == 0:
            raise ValueError("SURVEY_QUESTIONS_REQUIRED")

        is_test_norm = self._survey_bool(is_test)

        operator_ids_norm = self._survey_int_id_list(operator_ids)
        if not operator_ids_norm:
            raise ValueError("SURVEY_OPERATORS_REQUIRED")

        assignment = assignment or {}
        direction_ids_norm = self._survey_int_id_list(assignment.get('direction_ids'))
        group_ids_norm = self._survey_int_id_list(assignment.get('group_ids'))

        tenure_weeks_min = assignment.get('tenure_weeks_min')
        tenure_weeks_max = assignment.get('tenure_weeks_max')
        try:
            tenure_weeks_min = int(tenure_weeks_min) if tenure_weeks_min not in (None, '') else None
        except Exception:
            raise ValueError("SURVEY_INVALID_TENURE_MIN")
        try:
            tenure_weeks_max = int(tenure_weeks_max) if tenure_weeks_max not in (None, '') else None
        except Exception:
            raise ValueError("SURVEY_INVALID_TENURE_MAX")
        if tenure_weeks_min is not None and tenure_weeks_min < 0:
            raise ValueError("SURVEY_INVALID_TENURE_MIN")
        if tenure_weeks_max is not None and tenure_weeks_max < 0:
            raise ValueError("SURVEY_INVALID_TENURE_MAX")
        if tenure_weeks_min is not None and tenure_weeks_max is not None and tenure_weeks_min > tenure_weeks_max:
            raise ValueError("SURVEY_INVALID_TENURE_RANGE")

        test_config = test_config or {}
        starts_at = self._parse_survey_schedule_value(test_config.get('starts_at'), "SURVEY_INVALID_STARTS_AT")
        ends_at = self._parse_survey_schedule_value(test_config.get('ends_at'), "SURVEY_INVALID_ENDS_AT")
        if starts_at is not None and ends_at is not None and starts_at >= ends_at:
            raise ValueError("SURVEY_INVALID_SCHEDULE_RANGE")
        single_attempt = self._survey_bool(test_config.get('single_attempt'), default=True)
        affects_quality = self._survey_bool(test_config.get('affects_quality'), default=False)
        if not is_test_norm:
            # Расписание и учёт в качестве — свойства теста. Для обычного опроса
            # их не храним, чтобы поля не «залипали» при переключении режима.
            starts_at = None
            ends_at = None
            affects_quality = False

        normalized_questions = []
        for idx, raw_question in enumerate(questions):
            raw_question = raw_question or {}
            question_id = None
            if keep_question_ids:
                raw_question_id = raw_question.get('id')
                if raw_question_id not in (None, ''):
                    try:
                        parsed_question_id = int(raw_question_id)
                        if parsed_question_id > 0:
                            question_id = parsed_question_id
                    except Exception:
                        question_id = None

            text = str(raw_question.get('text') or '').strip()
            qtype = str(raw_question.get('type') or 'single').strip().lower()
            required = bool(raw_question.get('required', True))
            allow_other = bool(raw_question.get('allow_other', False))

            if not text:
                raise ValueError(f"SURVEY_QUESTION_TEXT_REQUIRED_{idx + 1}")
            if qtype not in ('single', 'multiple', 'rating'):
                raise ValueError(f"SURVEY_INVALID_QUESTION_TYPE_{idx + 1}")
            if is_test_norm and qtype == 'rating':
                raise ValueError(f"SURVEY_TEST_RATING_NOT_ALLOWED_{idx + 1}")

            points = 1.0
            # Частичный зачёт имеет смысл только там, где вариантов несколько.
            partial_credit = is_test_norm and qtype == 'multiple' and self._survey_bool(
                raw_question.get('partial_credit')
            )
            if is_test_norm:
                raw_points = raw_question.get('points')
                if raw_points not in (None, ''):
                    try:
                        points = float(raw_points)
                    except Exception:
                        raise ValueError(f"SURVEY_INVALID_QUESTION_POINTS_{idx + 1}")
                    if not math.isfinite(points) or points <= 0 or points > 1000:
                        raise ValueError(f"SURVEY_INVALID_QUESTION_POINTS_{idx + 1}")
                    points = round(points, 2)

            options_norm = []
            correct_options_norm = []
            if qtype != 'rating':
                if is_test_norm:
                    allow_other = False

                for option in raw_question.get('options') or []:
                    option_text = str(option or '').strip()
                    if option_text and option_text not in options_norm:
                        options_norm.append(option_text)

                is_other_only_question = (qtype == 'single' and allow_other and len(options_norm) == 0)
                if len(options_norm) < 2 and not is_other_only_question:
                    raise ValueError(f"SURVEY_OPTIONS_REQUIRED_{idx + 1}")

                for option in raw_question.get('correct_options') or []:
                    option_text = str(option or '').strip()
                    if option_text and option_text not in correct_options_norm:
                        correct_options_norm.append(option_text)

                if is_test_norm:
                    if not correct_options_norm:
                        raise ValueError(f"SURVEY_CORRECT_OPTIONS_REQUIRED_{idx + 1}")
                    invalid_correct = [item for item in correct_options_norm if item not in options_norm]
                    if invalid_correct:
                        raise ValueError(f"SURVEY_CORRECT_OPTION_INVALID_{idx + 1}")
                    if qtype == 'single' and len(correct_options_norm) != 1:
                        raise ValueError(f"SURVEY_SINGLE_CORRECT_OPTION_REQUIRED_{idx + 1}")
                elif is_other_only_question:
                    correct_options_norm = []
            else:
                allow_other = False

            normalized_questions.append({
                'id': question_id,
                'position': idx + 1,
                'text': text,
                'type': qtype,
                'required': required,
                'allow_other': allow_other,
                'options': options_norm,
                'correct_options': correct_options_norm,
                'points': points,
                'partial_credit': partial_credit
            })

        return {
            'title': title_norm,
            'description': str(description or '').strip() or None,
            'is_test': is_test_norm,
            'direction_ids': direction_ids_norm,
            'group_ids': group_ids_norm,
            'tenure_weeks_min': tenure_weeks_min,
            'tenure_weeks_max': tenure_weeks_max,
            'starts_at': starts_at,
            'ends_at': ends_at,
            'single_attempt': single_attempt,
            'affects_quality': affects_quality,
            'operator_ids': operator_ids_norm,
            'questions': normalized_questions
        }

    @classmethod
    def _normalize_survey_page(cls, page, page_size):
        try:
            page = int(page)
        except Exception:
            page = 1
        try:
            page_size = int(page_size)
        except Exception:
            page_size = cls.SURVEY_PAGE_SIZE_DEFAULT
        page = max(1, page)
        page_size = max(1, min(cls.SURVEY_PAGE_SIZE_MAX, page_size))
        return page, page_size

    def _normalize_task_assignees(self, assignees):
        """Состав исполнителей из того, что прислал клиент.

        Понимает список, кортеж, строку «12,15,7» (так состав приезжает в
        multipart-форме создания) и одиночный id. Порядок СОХРАНЯЕТ — первый
        становится tasks.assigned_to, — дубликаты сворачивает.

        Возвращает None для _UNSET/None: это «состав не меняем», и отличать его
        от «состав пустой» обязательно — иначе правка одной темы задачи молча
        снимала бы с неё всех исполнителей.
        """
        if assignees is None or assignees is _UNSET:
            return None
        items = assignees
        if isinstance(items, bytes):
            items = items.decode('utf-8', 'ignore')
        if isinstance(items, str):
            items = items.split(',')
        elif isinstance(items, (int, float)):
            items = [items]
        elif not isinstance(items, (list, tuple, set, frozenset)):
            raise ValueError("INVALID_ASSIGNED_TO")

        ordered = []
        seen = set()
        for item in items:
            if item is None or (isinstance(item, str) and not item.strip()):
                continue
            try:
                person_id = int(str(item).strip() if isinstance(item, str) else item)
            except (TypeError, ValueError):
                raise ValueError("INVALID_ASSIGNED_TO")
            if person_id <= 0:
                raise ValueError("INVALID_ASSIGNED_TO")
            if person_id in seen:
                continue
            seen.add(person_id)
            ordered.append(person_id)

        if not ordered:
            raise ValueError("ASSIGNEE_REQUIRED")
        if len(ordered) > self.TASK_MAX_ASSIGNEES:
            raise ValueError("TOO_MANY_ASSIGNEES")
        return ordered

    @staticmethod
    def _normalize_task_project_id(value):
        """None — задача без проекта; иначе положительный id."""
        if value is None or value is _UNSET:
            return None
        raw = str(value).strip().lower()
        if raw in ('', 'none', 'null', '0'):
            return None
        try:
            project_id = int(raw)
        except (TypeError, ValueError):
            raise ValueError("INVALID_PROJECT")
        if project_id <= 0:
            raise ValueError("INVALID_PROJECT")
        return project_id

    @staticmethod
    def _normalize_task_project_filter(value):
        """'' / None / 'all' → без фильтра, 'none' → задачи без проекта, иначе id."""
        if value is None:
            return None
        raw = str(value).strip().lower()
        if raw in ('', 'all'):
            return None
        if raw in ('none', 'null'):
            return 'none'
        try:
            project_id = int(raw)
        except Exception:
            raise ValueError("INVALID_TASK_PROJECT_FILTER")
        if project_id <= 0:
            raise ValueError("INVALID_TASK_PROJECT_FILTER")
        return project_id

    @staticmethod
    def _normalize_task_section_id(value):
        """None — без раздела; иначе положительный id раздела доски."""
        if value is None or value is _UNSET:
            return None
        raw = str(value).strip().lower()
        if raw in ('', 'none', 'null', '0'):
            return None
        try:
            section_id = int(raw)
        except (TypeError, ValueError):
            raise ValueError("INVALID_SECTION")
        if section_id <= 0:
            raise ValueError("INVALID_SECTION")
        return section_id

    @staticmethod
    def _normalize_task_section_filter(value):
        """'' / None / 'all' → без фильтра, 'none' → задачи без раздела, иначе id."""
        if value is None:
            return None
        raw = str(value).strip().lower()
        if raw in ('', 'all'):
            return None
        if raw in ('none', 'null'):
            return 'none'
        try:
            section_id = int(raw)
        except Exception:
            raise ValueError("INVALID_TASK_SECTION_FILTER")
        if section_id <= 0:
            raise ValueError("INVALID_TASK_SECTION_FILTER")
        return section_id

    def _normalize_task_watchers(self, watcher_ids):
        """Наблюдатели из того, что прислал клиент. _UNSET/None — «не меняем»."""
        if watcher_ids is None or watcher_ids is _UNSET:
            return None
        return task_projects.normalize_user_ids(
            watcher_ids, task_projects.MAX_WATCHERS, "INVALID_WATCHERS", "TOO_MANY_WATCHERS"
        )

    def _normalize_task_project_department_tx(self, cursor, department_id):
        if department_id is None or str(department_id).strip().lower() in ('', 'none', 'null', '0'):
            return None
        try:
            department_id = int(department_id)
        except (TypeError, ValueError):
            raise ValueError("INVALID_DEPARTMENT")
        cursor.execute("SELECT 1 FROM departments WHERE id = %s", (department_id,))
        if not cursor.fetchone():
            raise ValueError("INVALID_DEPARTMENT")
        return department_id

    def _normalize_task_priority(self, priority):
        priority_norm = (priority or 'normal').strip().lower() or 'normal'
        if priority_norm not in ('normal', 'urgent', 'critical'):
            raise ValueError("INVALID_PRIORITY")
        return priority_norm

    def _normalize_task_deadline_minutes(self, deadline_minutes):
        if deadline_minutes is None or deadline_minutes is _UNSET:
            return None
        try:
            minutes = int(deadline_minutes)
        except Exception:
            raise ValueError("INVALID_DEADLINE")
        if minutes <= 0:
            return None
        if minutes > 10 * 365 * 24 * 60:
            raise ValueError("INVALID_DEADLINE")
        return minutes

    def _normalize_task_estimate_minutes(self, estimate_minutes):
        """Плановая длительность задачи в минутах (для таймлайна). 0/пусто → None."""
        if estimate_minutes is None or estimate_minutes is _UNSET:
            return None
        if isinstance(estimate_minutes, str) and not estimate_minutes.strip():
            return None
        try:
            minutes = int(estimate_minutes)
        except Exception:
            raise ValueError("INVALID_ESTIMATE")
        if minutes < 0 or minutes > 10 * 365 * 24 * 60:
            raise ValueError("INVALID_ESTIMATE")
        return minutes or None

    def _normalize_task_priority_score(self, priority_score):
        """Балл приоритета 1–5. None или пустая строка — снять балл."""
        if priority_score is None or priority_score is _UNSET:
            return None
        # True — тоже int, но баллом не является.
        if isinstance(priority_score, bool):
            raise ValueError("INVALID_PRIORITY_SCORE")
        if isinstance(priority_score, str):
            if not priority_score.strip():
                return None
            if not priority_score.strip().isdigit():
                raise ValueError("INVALID_PRIORITY_SCORE")
            priority_score = int(priority_score.strip())
        if isinstance(priority_score, float):
            if not priority_score.is_integer():
                raise ValueError("INVALID_PRIORITY_SCORE")
            priority_score = int(priority_score)
        if not isinstance(priority_score, int) or priority_score not in self.TASK_PRIORITY_SCORES:
            raise ValueError("INVALID_PRIORITY_SCORE")
        return priority_score

    def _normalize_task_reminder_minutes(self, reminder_minutes_before):
        """За сколько минут до дедлайна напомнить. Пусто/0 = напоминание выключено."""
        if reminder_minutes_before is None or reminder_minutes_before is _UNSET:
            return None
        if isinstance(reminder_minutes_before, str) and not reminder_minutes_before.strip():
            return None
        try:
            minutes = int(reminder_minutes_before)
        except Exception:
            raise ValueError("INVALID_REMINDER")
        if minutes < 0 or minutes > self.TASK_REMINDER_MAX_MINUTES:
            raise ValueError("INVALID_REMINDER")
        return minutes or None

    def _normalize_task_origin(self, requested_by_id, requested_by_name):
        """
        Источник задачи: сотрудник из системы либо свободный текст.
        Оба пустые = своя инициатива. Id имеет приоритет над текстом,
        чтобы не хранить два противоречащих ответа на один вопрос.
        """
        origin_id = None
        if requested_by_id not in (None, '', _UNSET):
            try:
                origin_id = int(requested_by_id)
            except Exception:
                raise ValueError("INVALID_REQUESTED_BY")
            if origin_id <= 0:
                origin_id = None

        origin_name = None
        if origin_id is None and requested_by_name not in (None, _UNSET):
            origin_name = str(requested_by_name or '').strip()[:160] or None

        return origin_id, origin_name

    def _normalize_task_recurrence(self, is_regulation=False, recurrence_type=None, recurrence_interval=None):
        recurrence_type_norm = (recurrence_type or '').strip().lower() or None
        is_regulation_norm = bool(is_regulation or recurrence_type_norm)
        if recurrence_type_norm and recurrence_type_norm not in ('daily', 'weekly', 'monthly'):
            raise ValueError("INVALID_RECURRENCE_TYPE")
        if recurrence_type_norm:
            try:
                interval_norm = int(recurrence_interval or 1)
            except Exception:
                raise ValueError("INVALID_RECURRENCE_INTERVAL")
            if interval_norm < 1 or interval_norm > 365:
                raise ValueError("INVALID_RECURRENCE_INTERVAL")
        else:
            interval_norm = None
        return is_regulation_norm, recurrence_type_norm, interval_norm

    def _normalize_task_checklist_items(self, checklist_items):
        if not checklist_items:
            return []
        if not isinstance(checklist_items, list):
            raise ValueError("INVALID_CHECKLIST")

        normalized = []
        seen = set()
        for raw_item in checklist_items:
            if isinstance(raw_item, str):
                title = raw_item.strip()
                is_required = True
            elif isinstance(raw_item, dict):
                title = str(raw_item.get('title') or raw_item.get('text') or '').strip()
                is_required = bool(raw_item.get('is_required', True))
            else:
                continue
            if not title:
                continue
            title = title[:255]
            dedup_key = title.casefold()
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            normalized.append({
                "title": title,
                "position": len(normalized),
                "is_required": is_required
            })
            if len(normalized) >= 50:
                break
        return normalized

    def _normalize_task_report_kind(self, kind):
        kind_norm = (kind or 'progress').strip().lower() or 'progress'
        if kind_norm not in ('progress', 'completion'):
            raise ValueError("INVALID_REPORT_KIND")
        return kind_norm

    def _normalize_task_spent_minutes(self, spent_minutes):
        if spent_minutes is None or spent_minutes is _UNSET:
            return None
        if isinstance(spent_minutes, str) and not spent_minutes.strip():
            return None
        try:
            minutes = int(spent_minutes)
        except Exception:
            raise ValueError("INVALID_SPENT_MINUTES")
        if minutes < 0 or minutes > 10 * 365 * 24 * 60:
            raise ValueError("INVALID_SPENT_MINUTES")
        return minutes or None

    def _normalize_task_message_kind(self, kind):
        kind_norm = (kind or 'note').strip().lower() or 'note'
        if kind_norm not in self.TASK_MESSAGE_KINDS:
            raise ValueError("INVALID_MESSAGE_KIND")
        return kind_norm

    def _normalize_task_note_priority(self, priority):
        priority_norm = (priority or 'normal').strip().lower() or 'normal'
        if priority_norm not in ('normal', 'urgent', 'critical'):
            raise ValueError("INVALID_PRIORITY")
        return priority_norm

    def _normalize_task_note_due_at(self, value):
        if value is _UNSET:
            return _UNSET
        if value is None:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            text = str(value or '').strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
            except Exception:
                raise ValueError("INVALID_DUE_AT")

        if getattr(parsed, 'tzinfo', None) is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed

    @staticmethod
    def _normalize_task_department_filter(value):
        """'' / None / 'all' → без фильтра, 'none' → без отдела, иначе id отдела."""
        if value is None:
            return None
        raw = str(value).strip().lower()
        if raw in ('', 'all'):
            return None
        if raw in ('none', 'null'):
            return 'none'
        try:
            department_id = int(raw)
        except Exception:
            raise ValueError("INVALID_TASK_DEPARTMENT_FILTER")
        if department_id <= 0:
            raise ValueError("INVALID_TASK_DEPARTMENT_FILTER")
        return department_id

    def remove_schedule_dismissal_blacklist(self, operator_id, actor_id=None):
        """Убрать сотрудника из ЧС: снять флаг is_blacklist с его ЧС-увольнений.

        Само увольнение остаётся — с той же датой, причиной и комментарием, — но
        становится обычным: его можно прервать сменой, закрыть датой или удалить.
        Кому это можно (главы отделов, админы, супер-админы), решает роут.
        В «Истории изменений» сотрудника остаётся запись, кто снял ЧС.

        Возвращает снятые периоды; пустой список — человек не в ЧС.
        """
        operator_id = int(operator_id)
        actor_id_norm = int(actor_id) if actor_id is not None else None
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE operator_schedule_status_periods
                SET is_blacklist = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE operator_id = %s
                  AND status_code = 'dismissal'
                  AND COALESCE(is_blacklist, FALSE) = TRUE
                RETURNING id, operator_id, status_code, start_date, end_date, dismissal_reason, comment, is_blacklist
            """, (operator_id,))
            rows = cursor.fetchall() or []
            if not rows:
                return []
            cursor.execute("""
                INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
                VALUES (%s, %s, %s, %s, %s)
            """, (operator_id, actor_id_norm, 'blacklist', 'Да', 'Нет'))
            return [self._serialize_schedule_status_period(row) for row in rows]

    # ── Продакшн (задача #354) ───────────────────────────────────────────────
    # Тип задачи для отдела маркетинга с брифом из ТЗ. Сам бриф проверяет
    # task_production; здесь — то, для чего нужна задача целиком: описание
    # задания и состав исполнителей.

    def _normalize_task_production_brief(self, tag, production_brief, description):
        """Бриф для записи в базу; у любого типа, кроме продакшна, — None.

        У продакшна обязательны и бриф, и описание задания. Пропуски в обоих
        собираются в ОДНУ ошибку: иначе тот, кто прошёл мимо формы, узнавал бы
        о них по одному на каждый отказ.
        """
        if tag != task_production.PRODUCTION_TAG:
            return None
        missing = []
        brief = None
        try:
            brief = task_production.normalize_brief(production_brief)
        except ValueError as error:
            code = str(error)
            if not code.startswith(task_production.INCOMPLETE_PREFIX):
                raise
            missing.extend(key for key in code[len(task_production.INCOMPLETE_PREFIX):].split(',') if key)
        if not str(description or '').strip():
            missing.append('description')
        if missing:
            raise ValueError(task_production.incomplete(missing))
        return brief
