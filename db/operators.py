"""База данных: операторы.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _minutes_to_time,
    Any,
    CALCULATION_MODEL_CHAT_MANAGER,
    CALCULATION_MODEL_OPERATOR,
    calendar,
    CHAT_MANAGER_ACTION_STATUS_KEYS,
    date,
    datetime,
    Dict,
    execute_values,
    List,
    LIVE_STATUS_TRACK_DEPARTMENT_CODES,
    normalize_calculation_model_code,
    Optional,
    re,
    role_has_min,
    timedelta,
    uuid,
    WORK_SHIFT_OFFICE_PRACTICE_OFFLINE_COMMENT,
    WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT,
    WORK_SHIFT_TYPE_OFFICE_PRACTICE,
    WORK_SHIFT_TYPE_PHONE_SHIFT,
)


class OperatorsMixin:
    def _load_offline_activities_by_operator_day_tx(self, cursor, operator_ids, start_date, end_date, group_id=None):
        """
        Возвращает:
        - map: {operator_id: {day_num_str: [activities...]}}
        - totals: {operator_id: total_hours}
        """
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        result = {op_id: {} for op_id in op_ids}
        totals = {op_id: 0.0 for op_id in op_ids}
        if not op_ids:
            return result, totals

        group_filter_sql = ""
        params = [op_ids, start_date, end_date]
        if group_id is not None:
            group_filter_sql = """
              AND EXISTS (
                  SELECT 1
                  FROM group_operator_memberships gom
                  WHERE gom.operator_id = oa.operator_id
                    AND gom.group_id = %s
                    AND gom.start_date <= oa.activity_date
                    AND (gom.end_date IS NULL OR gom.end_date >= oa.activity_date)
              )
            """
            params.append(int(group_id))

        cursor.execute(
            f"""
            SELECT
                oa.id,
                oa.operator_id,
                oa.activity_date,
                oa.start_time,
                oa.end_time,
                oa.comment,
                cb.name,
                oa.created_at
            FROM operator_offline_activities oa
            LEFT JOIN users cb ON cb.id = oa.created_by
            WHERE oa.operator_id = ANY(%s)
              AND oa.activity_date >= %s
              AND oa.activity_date <= %s
              {group_filter_sql}
            ORDER BY oa.operator_id, oa.activity_date, oa.start_time, oa.end_time, oa.id
            """,
            params,
        )
        for activity_id, op_id, activity_date, start_time, end_time, comment, created_by_name, created_at in cursor.fetchall() or []:
            try:
                op_id_int = int(op_id)
            except Exception:
                continue
            if op_id_int not in result or activity_date is None:
                continue

            start_text = start_time.strftime('%H:%M') if start_time else None
            end_text = end_time.strftime('%H:%M') if end_time else None
            duration_minutes = 0
            try:
                if start_time and end_time:
                    start_min, end_min = self._schedule_interval_minutes(start_time, end_time)
                    duration_minutes = max(0, int(end_min - start_min))
            except Exception:
                duration_minutes = 0
            duration_hours = round(float(duration_minutes) / 60.0, 2)

            day_key = str(int(activity_date.day))
            comment_text = str(comment or '').strip()
            result.setdefault(op_id_int, {}).setdefault(day_key, []).append({
                "id": int(activity_id),
                "date": activity_date.strftime('%Y-%m-%d'),
                "start_time": start_text,
                "end_time": end_text,
                "time_range": f"{start_text} - {end_text}" if start_text and end_text else None,
                "comment": comment_text,
                "created_by_name": created_by_name,
                "created_at": created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else None,
                "duration_minutes": int(duration_minutes),
                "duration_hours": float(duration_hours),
            })
            totals[op_id_int] = round(float(totals.get(op_id_int, 0.0)) + float(duration_hours), 2)

        shift_group_filter_sql = ""
        shift_params = [op_ids, start_date, end_date]
        if group_id is not None:
            shift_group_filter_sql = """
              AND EXISTS (
                  SELECT 1
                  FROM group_operator_memberships gom
                  WHERE gom.operator_id = ws.operator_id
                    AND gom.group_id = %s
                    AND gom.start_date <= ws.shift_date
                    AND (gom.end_date IS NULL OR gom.end_date >= ws.shift_date)
              )
            """
            shift_params.append(int(group_id))

        cursor.execute(
            f"""
            SELECT
                ws.id,
                ws.operator_id,
                ws.shift_date,
                ws.start_time,
                ws.end_time
            FROM work_shifts ws
            WHERE ws.operator_id = ANY(%s)
              AND COALESCE(ws.shift_type, 'regular') = 'office_practice'
              AND ws.shift_date >= %s
              AND ws.shift_date <= %s
              {shift_group_filter_sql}
            ORDER BY ws.operator_id, ws.shift_date, ws.start_time, ws.end_time, ws.id
            """,
            shift_params,
        )
        for shift_id, op_id, shift_date, start_time, end_time in cursor.fetchall() or []:
            try:
                op_id_int = int(op_id)
            except Exception:
                continue
            if op_id_int not in result or shift_date is None:
                continue

            start_text = start_time.strftime('%H:%M') if start_time else None
            end_text = end_time.strftime('%H:%M') if end_time else None
            duration_minutes = 0
            try:
                if start_time and end_time:
                    start_min, end_min = self._schedule_interval_minutes(start_time, end_time)
                    duration_minutes = max(0, int(end_min - start_min))
            except Exception:
                duration_minutes = 0
            duration_hours = round(float(duration_minutes) / 60.0, 2)

            day_key = str(int(shift_date.day))
            result.setdefault(op_id_int, {}).setdefault(day_key, []).append({
                "id": f"practice-shift-{int(shift_id)}",
                "operator_id": op_id_int,
                "date": shift_date.strftime('%Y-%m-%d'),
                "start_time": start_text,
                "end_time": end_text,
                "time_range": f"{start_text} - {end_text}" if start_text and end_text else None,
                "comment": WORK_SHIFT_OFFICE_PRACTICE_OFFLINE_COMMENT,
                "created_by_name": "Планировщик",
                "created_at": None,
                "duration_minutes": int(duration_minutes),
                "duration_hours": float(duration_hours),
                "source": "work_shift",
                "shift_type": WORK_SHIFT_TYPE_OFFICE_PRACTICE,
                "is_practice_shift": True,
                "read_only": True,
            })
            totals[op_id_int] = round(float(totals.get(op_id_int, 0.0)) + float(duration_hours), 2)

        manual_phone_training_map, _manual_phone_training_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=op_ids,
            start_date=start_date,
            end_date=end_date,
            group_id=group_id
        )
        status_phone_training_map, _status_phone_training_totals = self._load_phone_shift_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=op_ids,
            start_date=start_date,
            end_date=end_date,
            group_id=group_id
        )
        phone_training_seen = {op_id: set() for op_id in op_ids}
        for phone_training_map in (manual_phone_training_map, status_phone_training_map):
            for op_id_int, days_map in (phone_training_map or {}).items():
                try:
                    op_id_int = int(op_id_int)
                except Exception:
                    continue
                if op_id_int not in result:
                    continue
                for day_key, items in (days_map or {}).items():
                    for item in (items or []):
                        start_sec = max(0, int(item.get('start_seconds') or 0))
                        end_sec = max(0, int(item.get('end_seconds') or 0))
                        if end_sec <= start_sec:
                            continue
                        signature = (str(item.get('date') or day_key), start_sec, end_sec)
                        if signature in phone_training_seen.setdefault(op_id_int, set()):
                            continue
                        phone_training_seen[op_id_int].add(signature)
                        duration_seconds = end_sec - start_sec
                        duration_minutes = self._schedule_auto_seconds_to_display_minutes(duration_seconds)
                        duration_hours = round(float(duration_seconds) / 3600.0, 2)
                        start_text = item.get('start_time')
                        end_text = item.get('end_time')
                        activity_date_value = item.get('date') or day_key
                        try:
                            payload_day_key = str(int(
                                str(activity_date_value).split('T', 1)[0].rsplit('-', 1)[-1]
                            ))
                        except (TypeError, ValueError):
                            payload_day_key = str(day_key)
                        result.setdefault(op_id_int, {}).setdefault(payload_day_key, []).append({
                            "id": item.get('id'),
                            "operator_id": op_id_int,
                            "date": activity_date_value,
                            "start_time": start_text,
                            "end_time": end_text,
                            "time_range": f"{start_text} - {end_text}" if start_text and end_text else None,
                            "comment": WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT,
                            "created_by_name": "Планировщик",
                            "created_at": None,
                            "duration_minutes": int(duration_minutes),
                            "duration_seconds": int(duration_seconds),
                            "duration_hours": float(duration_hours),
                            "source": item.get('source') or "work_shift_training_status",
                            "shift_type": WORK_SHIFT_TYPE_PHONE_SHIFT,
                            "is_phone_shift_training": True,
                            "read_only": True,
                        })
        phone_training_totals = self._sum_phone_shift_offline_interval_maps(
            op_ids,
            manual_phone_training_map,
            status_phone_training_map
        )
        for op_id_int in op_ids:
            totals[op_id_int] = round(
                float(totals.get(op_id_int, 0.0)) + float(phone_training_totals.get(op_id_int, 0.0) or 0.0),
                2
            )

        return result, totals

    def _operator_monthly_plan_row_to_dict(self, row):
        target = float(row[3] or 0)
        fact = float(row[4] or 0)
        completion = (fact / target * 100.0) if target > 0 else 0.0
        return {
            'operator_id': int(row[0]),
            'year': int(row[1]),
            'month': int(row[2]),
            'plan_target': target,
            'plan_fact': fact,
            'completion_percent': round(completion, 2),
            'updated_by': int(row[5]) if row[5] is not None else None,
            'updated_at': row[6].isoformat() if hasattr(row[6], 'isoformat') else (str(row[6]) if row[6] else None),
        }

    def get_operator_monthly_plan(self, operator_id, year, month):
        """Месячный план оператора (TEZ ОП): цель/факт/%. None — если не задан."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT operator_id, year, month, plan_target, plan_fact, updated_by, updated_at
                FROM operator_monthly_plans
                WHERE operator_id = %s AND year = %s AND month = %s
                """,
                (int(operator_id), int(year), int(month))
            )
            row = cursor.fetchone()
        return self._operator_monthly_plan_row_to_dict(row) if row else None

    def get_operator_monthly_plans_for_operators(self, operator_ids, year, month):
        """Планы за период для набора операторов: {operator_id: dict}."""
        ids = [int(x) for x in (operator_ids or []) if x is not None]
        if not ids:
            return {}
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT operator_id, year, month, plan_target, plan_fact, updated_by, updated_at
                FROM operator_monthly_plans
                WHERE year = %s AND month = %s AND operator_id = ANY(%s)
                """,
                (int(year), int(month), ids)
            )
            rows = cursor.fetchall()
        return {int(r[0]): self._operator_monthly_plan_row_to_dict(r) for r in rows}

    def upsert_operator_monthly_plan(self, operator_id, year, month, plan_target, plan_fact, updated_by=None):
        """Создать/обновить месячный план оператора (upsert по operator_id+year+month)."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO operator_monthly_plans
                    (operator_id, year, month, plan_target, plan_fact, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (operator_id, year, month) DO UPDATE SET
                    plan_target = EXCLUDED.plan_target,
                    plan_fact = EXCLUDED.plan_fact,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING operator_id, year, month, plan_target, plan_fact, updated_by, updated_at
                """,
                (
                    int(operator_id), int(year), int(month),
                    float(plan_target or 0), float(plan_fact or 0),
                    int(updated_by) if updated_by is not None else None
                )
            )
            row = cursor.fetchone()
        return self._operator_monthly_plan_row_to_dict(row) if row else None

    def get_active_operators_for_access(self, department_ids=None, supervisor_id=None):
        """Работающие операторы для «Управления операторами🔑» в боте (задача #380).

        Круг тот же, что пускает сайт (_requester_can_access_target_user): операторы отделов
        department_ids, а у СВ без отдела — только записанные за ним (supervisor_id). Уволенных
        нет: менять им вход незачем. Группа оператора — его СВ, имя группы — имя СВ.
        """
        departments = [int(item) for item in (department_ids or ())]
        if not departments and supervisor_id is None:
            return []
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.supervisor_id, s.name
                FROM users u
                LEFT JOIN users s ON s.id = u.supervisor_id
                WHERE u.role = 'operator'
                  AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
                  AND CASE WHEN cardinality(%(departments)s::int[]) > 0
                           THEN u.department_id = ANY(%(departments)s::int[])
                           ELSE u.supervisor_id = %(supervisor_id)s
                      END
                ORDER BY u.name, u.id
            """, {'departments': departments, 'supervisor_id': supervisor_id})
            return [
                {'id': row[0], 'name': row[1], 'supervisor_id': row[2], 'supervisor_name': row[3]}
                for row in cursor.fetchall()
            ]

    def get_operator_stats(self, operator_id):
        """Получить статистику оператора (часы, оценки, звонки в час, тренинги)"""
        with self._get_cursor() as cursor:
            # Текущий месяц в формате YYYY-MM
            current_month = datetime.now().strftime('%Y-%m')
            # Модель — по группе оператора на конец текущего месяца (fallback — направление).
            _cm_y, _cm_m = map(int, current_month.split('-'))
            _cm_end = date(_cm_y, _cm_m, calendar.monthrange(_cm_y, _cm_m)[1])
            calculation_model_code = self._get_operator_calculation_model_tx(cursor, operator_id, as_of=_cm_end)

            # 1) Берём агрегаты из work_hours (regular_hours, total_calls, fines, norm_hours)
            cursor.execute("""
                SELECT 
                    COALESCE(wh.regular_hours, 0) AS regular_hours,
                    COALESCE(wh.fines, 0) AS fines,
                    COALESCE(wh.norm_hours, 0) AS norm_hours,
                    COALESCE(wh.total_calls, 0) AS total_calls
                FROM work_hours wh
                WHERE wh.operator_id = %s AND wh.month = %s
                LIMIT 1
            """, (operator_id, current_month))
            wh_row = cursor.fetchone()

            if wh_row:
                regular_hours, fines, norm_hours, total_calls = wh_row
                regular_hours = float(regular_hours or 0.0)
                fines = float(fines or 0.0)
                norm_hours = float(norm_hours or 0.0)
                total_calls = int(total_calls or 0)
            else:
                regular_hours = 0.0
                fines = 0.0
                norm_hours = 0.0
                total_calls = 0

            # 2) Рассчитываем training_hours прямо по таблице trainings (учитываем count_in_hours = TRUE)
            cursor.execute("""
                SELECT COALESCE(SUM(
                    CASE 
                    WHEN t.end_time <= t.start_time 
                        THEN EXTRACT(EPOCH FROM (t.end_time + INTERVAL '24 hours' - t.start_time))
                    ELSE EXTRACT(EPOCH FROM (t.end_time - t.start_time))
                    END
                ) / 3600.0, 0) AS training_hours_seconds
                FROM trainings t
                WHERE t.operator_id = %s
                AND TO_CHAR(t.training_date, 'YYYY-MM') = %s
                AND t.count_in_hours = TRUE
            """, (operator_id, current_month))
            tr_row = cursor.fetchone()
            training_hours = float(tr_row[0] or 0.0)
            month_start_date, next_month_start = self._get_month_date_range(current_month)
            _manual_phone_training_counted_map, manual_phone_training_counted_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=month_start_date,
                end_date=next_month_start - timedelta(days=1),
                counted_only=True
            )
            training_hours = max(
                0.0,
                training_hours - float(manual_phone_training_counted_totals.get(int(operator_id), 0.0) or 0.0)
            )

            # 3) Рассчитываем техсбои за месяц (учитываются в часах выполнения нормы)
            cursor.execute("""
                SELECT COALESCE(SUM(
                    CASE
                    WHEN ti.end_time <= ti.start_time
                        THEN EXTRACT(EPOCH FROM (ti.end_time + INTERVAL '24 hours' - ti.start_time))
                    ELSE EXTRACT(EPOCH FROM (ti.end_time - ti.start_time))
                    END
                ) / 3600.0, 0) AS technical_issue_hours_seconds
                FROM operator_technical_issues ti
                WHERE ti.operator_id = %s
                AND TO_CHAR(ti.issue_date, 'YYYY-MM') = %s
            """, (operator_id, current_month))
            tech_row = cursor.fetchone()
            technical_issue_hours = float(tech_row[0] or 0.0)

            # 3.1) Рассчитываем офлайн-активность за месяц
            cursor.execute("""
                SELECT COALESCE(SUM(
                    CASE
                    WHEN oa.end_time <= oa.start_time
                        THEN EXTRACT(EPOCH FROM (oa.end_time + INTERVAL '24 hours' - oa.start_time))
                    ELSE EXTRACT(EPOCH FROM (oa.end_time - oa.start_time))
                    END
                ) / 3600.0, 0) AS offline_activity_hours_seconds
                FROM operator_offline_activities oa
                WHERE oa.operator_id = %s
                AND TO_CHAR(oa.activity_date, 'YYYY-MM') = %s
            """, (operator_id, current_month))
            offline_row = cursor.fetchone()
            offline_activity_hours = float(offline_row[0] or 0.0)

            cursor.execute("""
                SELECT COALESCE(SUM(
                    CASE
                    WHEN ws.end_time <= ws.start_time
                        THEN EXTRACT(EPOCH FROM (ws.end_time + INTERVAL '24 hours' - ws.start_time))
                    ELSE EXTRACT(EPOCH FROM (ws.end_time - ws.start_time))
                    END
                ) / 3600.0, 0) AS practice_shift_offline_hours
                FROM work_shifts ws
                WHERE ws.operator_id = %s
                AND COALESCE(ws.shift_type, 'regular') = 'office_practice'
                AND TO_CHAR(ws.shift_date, 'YYYY-MM') = %s
            """, (operator_id, current_month))
            practice_row = cursor.fetchone()
            offline_activity_hours += float(practice_row[0] or 0.0)
            manual_phone_training_map, _manual_phone_training_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=month_start_date,
                end_date=next_month_start - timedelta(days=1)
            )
            status_phone_training_map, _status_phone_training_totals = self._load_phone_shift_training_offline_intervals_by_operator_day_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=month_start_date,
                end_date=next_month_start - timedelta(days=1)
            )
            phone_training_totals = self._sum_phone_shift_offline_interval_maps(
                [operator_id],
                manual_phone_training_map,
                status_phone_training_map
            )
            offline_activity_hours += float(phone_training_totals.get(int(operator_id), 0.0) or 0.0)

            # 4) Количество оценённых звонков и средняя оценка (как раньше).
            # Оценка «Тестирование знаний» входит в среднюю, но звонком не
            # считается — план прослушки от неё не уменьшается.
            # Оценки разговоров с номерами «Реестра тестовых номеров» (test_numbers) в счёт
            # и средний балл оператора не входят: сотрудник проверял линию, а не работал.
            from test_numbers import keys as test_keys
            not_test_calls = test_keys.sql_calls_not_test()
            cursor.execute("""
                SELECT
                    (SELECT COUNT(*) FROM calls
                      WHERE operator_id = %s AND month = %s AND is_draft = FALSE
                        AND survey_response_id IS NULL
                        AND """ + not_test_calls + """) AS call_count,
                    (SELECT AVG(score) FROM calls
                      WHERE operator_id = %s AND month = %s AND is_draft = FALSE
                        AND """ + not_test_calls + """) AS avg_score
            """, (operator_id, current_month, operator_id, current_month))
            calls_row = cursor.fetchone()
            call_count = int(calls_row[0] or 0)
            avg_score = float(calls_row[1]) if calls_row[1] is not None else 0.0

            chat_avg_response_time_seconds = None
            chat_transfer_count = 0
            if calculation_model_code == CALCULATION_MODEL_CHAT_MANAGER:
                cursor.execute("""
                    SELECT
                        COALESCE(SUM(chats_count), 0),
                        (
                            COALESCE(SUM(score_sum) FILTER (WHERE score_count > 0 AND score_sum IS NOT NULL), 0)
                            + COALESCE(SUM(avg_score) FILTER (
                                WHERE NOT (COALESCE(score_count, 0) > 0 AND score_sum IS NOT NULL)
                                  AND avg_score IS NOT NULL
                            ), 0)
                        ) / NULLIF(
                            COALESCE(SUM(score_count) FILTER (WHERE score_count > 0 AND score_sum IS NOT NULL), 0)
                            + COALESCE(COUNT(avg_score) FILTER (
                                WHERE NOT (COALESCE(score_count, 0) > 0 AND score_sum IS NOT NULL)
                                  AND avg_score IS NOT NULL
                            ), 0),
                            0
                        ),
                        AVG(avg_response_time_seconds) FILTER (WHERE avg_response_time_seconds IS NOT NULL),
                        COALESCE(SUM(transfer_chat_count), 0)
                    FROM chat_manager_daily_metrics
                    WHERE operator_id = %s
                    AND TO_CHAR(day, 'YYYY-MM') = %s
                """, (operator_id, current_month))
                chat_row = cursor.fetchone() or (0, None, None, 0)
                total_calls = int(chat_row[0] or 0)
                call_count = total_calls
                avg_score = float(chat_row[1]) if chat_row[1] is not None else 0.0
                chat_avg_response_time_seconds = float(chat_row[2]) if chat_row[2] is not None else None
                chat_transfer_count = int(chat_row[3] or 0)

            # 5) calls_per_hour: total_calls / regular_hours (время в работе из статусов).
            effective_call_hours = max(0.0, float(regular_hours))
            if effective_call_hours > 0:
                calls_per_hour = float(total_calls) / float(effective_call_hours)
            else:
                calls_per_hour = 0.0

            # 6) percent_complete: считаем с учётом зачётных тренингов, техсбоев и офлайн-активности.
            accounted_hours = regular_hours + training_hours + technical_issue_hours + offline_activity_hours
            if norm_hours and norm_hours > 0:
                percent_complete = (accounted_hours / norm_hours) * 100.0
            else:
                percent_complete = 0.0

            return {
                'operator_id': operator_id,
                'month': current_month,
                'calculation_model_code': calculation_model_code,
                'calculationModelCode': calculation_model_code,
                'regular_hours': round(float(regular_hours), 2),
                'training_hours': round(float(training_hours), 2),
                'technical_issue_hours': round(float(technical_issue_hours), 2),
                'offline_activity_hours': round(float(offline_activity_hours), 2),
                'accounted_hours': round(float(accounted_hours), 2),
                'fines': round(float(fines), 2),
                'norm_hours': float(norm_hours),
                'percent_complete': round(percent_complete, 2),
                'call_count': int(call_count),
                'avg_score': round(float(avg_score), 2),
                'total_calls': int(total_calls),
                'calls_per_hour': round(float(calls_per_hour), 2),
                'chat_avg_score': round(float(avg_score), 2) if calculation_model_code == CALCULATION_MODEL_CHAT_MANAGER else None,
                'chat_avg_response_time_seconds': chat_avg_response_time_seconds,
                'chat_transfer_count': int(chat_transfer_count or 0)
            }

    def get_all_operators_with_details(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    u.id,
                    u.name,
                    u.direction_id,
                    u.supervisor_id,
                    u.hire_date,
                    u.hours_table_url,
                    u.scores_table_url,
                    s.name as supervisor_name,
                    u.status,
                    u.rate,
                    u.gender,
                    u.birth_date,
                    u.avatar_bucket,
                    u.avatar_blob_path,
                    u.avatar_updated_at,
                    sp.status_code as status_period_status_code,
                    sp.start_date as status_period_start_date,
                    sp.end_date as status_period_end_date,
                    sp.dismissal_reason as status_period_dismissal_reason,
                    COALESCE(sp.is_blacklist, FALSE) as status_period_is_blacklist,
                    sp.comment as status_period_comment
                FROM users u
                LEFT JOIN directions d ON u.direction_id = d.id
                LEFT JOIN users s ON u.supervisor_id = s.id
                LEFT JOIN LATERAL (
                    SELECT
                        p.status_code,
                        p.start_date,
                        p.end_date,
                        p.dismissal_reason,
                        p.is_blacklist,
                        p.comment
                    FROM operator_schedule_status_periods p
                    WHERE p.operator_id = u.id
                      AND p.status_code = (
                          CASE
                              WHEN u.status = 'fired' THEN 'dismissal'
                              WHEN u.status = 'dismissal' THEN 'dismissal'
                              WHEN u.status = 'unpaid_leave' THEN 'bs'
                              ELSE u.status
                          END
                      )
                    ORDER BY
                        CASE
                            WHEN p.start_date <= CURRENT_DATE
                             AND COALESCE(p.end_date, DATE '9999-12-31') >= CURRENT_DATE
                            THEN 0
                            ELSE 1
                        END,
                        p.start_date DESC,
                        p.id DESC
                    LIMIT 1
                ) sp ON TRUE
                WHERE u.role = 'operator'
            """)
            return [
                {
                    'id': row[0],
                    'name': row[1],
                    'direction_id': row[2],
                    'supervisor_id': row[3],
                    'hire_date': row[4].strftime('%d-%m-%Y') if row[4] else None,
                    'hours_table_url': row[5],
                    'scores_table_url': row[6],
                    'supervisor_name': row[7],
                    'status': row[8],
                    'rate': row[9],
                    'gender': row[10],
                    'birth_date': row[11].strftime('%d-%m-%Y') if row[11] else None,
                    'avatar_bucket': row[12],
                    'avatar_blob_path': row[13],
                    'avatar_updated_at': row[14].isoformat() if row[14] else None,
                    'status_period_status_code': row[15],
                    'status_period_start_date': row[16].strftime('%Y-%m-%d') if row[16] else None,
                    'status_period_end_date': row[17].strftime('%Y-%m-%d') if row[17] else None,
                    'status_period_dismissal_reason': row[18] or '',
                    'status_period_is_blacklist': bool(row[19]) if row[19] is not None else False,
                    'status_period_comment': row[20] or ''
                } for row in cursor.fetchall()
            ]

    def get_operator_score_aggregates_for_month(self, month, operator_ids: Optional[List[int]] = None):
        """
        Канонические агрегаты завершённых оценок за месяц:
        - call_count: количество оцененных звонков (score IS NOT NULL)
        - avg_score: средний балл

        Для каждого звонка берётся одна последняя завершённая версия по ключу
        (operator_id, phone_number, month, appeal_date). id — детерминированный
        tie-breaker, если две версии имеют одинаковый created_at.
        """
        if not month:
            return {}

        normalized_ids = None
        if operator_ids is not None:
            normalized_ids = []
            seen_ids = set()
            for raw_id in operator_ids:
                try:
                    op_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if op_id in seen_ids:
                    continue
                seen_ids.add(op_id)
                normalized_ids.append(op_id)
            if not normalized_ids:
                return {}

        params = [month]
        # Оценки разговоров с номерами «Реестра тестовых номеров» (test_numbers) в счёт
        # и средний балл оператора не входят: сотрудник проверял линию, а не работал.
        from test_numbers import keys as test_keys
        filter_clause = "month = %s AND is_draft = FALSE AND " + test_keys.sql_calls_not_test()
        if normalized_ids is not None:
            filter_clause += " AND operator_id = ANY(%s)"
            params.append(normalized_ids)

        # call_count — только прослушанные звонки: оценка «Тестирование знаний»
        # даёт средний балл, но плана по звонкам не закрывает.
        query = f"""
            WITH latest_calls AS (
                SELECT DISTINCT ON (operator_id, phone_number, month, appeal_date)
                    operator_id,
                    phone_number,
                    month,
                    appeal_date,
                    score,
                    survey_response_id
                FROM calls
                WHERE {filter_clause}
                ORDER BY operator_id, phone_number, month, appeal_date, created_at DESC, id DESC
            ),
            call_aggregates AS (
                SELECT
                    operator_id,
                    COUNT(*) AS evaluation_row_count,
                    COUNT(*) FILTER (WHERE score IS NOT NULL AND survey_response_id IS NULL) AS call_count,
                    AVG(score)::float AS avg_score
                FROM latest_calls
                GROUP BY operator_id
            )
            SELECT
                operator_id,
                call_count,
                avg_score,
                evaluation_row_count
            FROM call_aggregates ca
        """

        with self._get_cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

        result = {}
        for operator_id, call_count, avg_score, evaluation_row_count in rows:
            op_id = int(operator_id)
            evaluation_rows = int(evaluation_row_count or 0)
            avg_score_raw = float(avg_score) if avg_score is not None else None
            result[op_id] = {
                "call_count": int(call_count or 0),
                "avg_score": round(avg_score_raw, 2) if avg_score_raw is not None else None,
                "avg_score_raw": avg_score_raw,
                "evaluation_row_count": evaluation_rows,
                "has_evaluation_data": evaluation_rows > 0
            }
        return result

    def get_operator_score_aggregates_for_period(self, operator_ids: Optional[List[int]] = None, *,
                                                 months=None, start=None, end=None, operator_months=None):
        """Агрегаты завершённых оценок за ПЕРИОД «Аналитики» журнала (задача #389).

        Выборка та же, что у get_operator_score_aggregates_for_month: одна
        последняя завершённая версия оценки по ключу (оператор, номер, месяц,
        дата обращения), оценка «Тестирование знаний» входит в балл, но не в
        счёт звонков, тестовые номера не считаются. Период задаёт
        _journal_period_sql: целые месяцы — по месяцу журнала, остальное — по
        дню обращения, ничего — всё время. Один месяц «Аналитика» по-прежнему
        считает функцией месяца — ради тех же цифр, что в журнале и выгрузке.

        operator_months — пары (оператор, «ГГГГ-ММ»): месяцы журнала, когда человек
        был в группе; вместо operator_ids (режим группы, период длиннее месяца).
        """
        pair_ids, pair_months = self._journal_operator_month_pairs(operator_months)
        if operator_months is not None and not pair_ids:
            return {}
        normalized_ids = None
        if operator_ids is not None and operator_months is None:
            normalized_ids = []
            seen_ids = set()
            for raw_id in operator_ids:
                try:
                    op_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if op_id in seen_ids:
                    continue
                seen_ids.add(op_id)
                normalized_ids.append(op_id)
            if not normalized_ids:
                return {}

        from test_numbers import keys as test_keys
        period_sql, params = self._journal_period_sql(months=months, start=start, end=end)
        filter_clause = period_sql + " AND is_draft = FALSE AND " + test_keys.sql_calls_not_test()
        if pair_ids:
            filter_clause += " AND (operator_id, month) IN (SELECT * FROM unnest(%s::int[], %s::text[]))"
            params.extend([pair_ids, pair_months])
        elif normalized_ids is not None:
            filter_clause += " AND operator_id = ANY(%s)"
            params.append(normalized_ids)

        query = f"""
            WITH latest_calls AS (
                SELECT DISTINCT ON (operator_id, phone_number, month, appeal_date)
                    operator_id,
                    phone_number,
                    month,
                    appeal_date,
                    score,
                    survey_response_id
                FROM calls
                WHERE {filter_clause}
                ORDER BY operator_id, phone_number, month, appeal_date, created_at DESC, id DESC
            )
            SELECT
                operator_id,
                COUNT(*) FILTER (WHERE score IS NOT NULL AND survey_response_id IS NULL) AS call_count,
                AVG(score)::float AS avg_score,
                COUNT(*) AS evaluation_row_count
            FROM latest_calls
            GROUP BY operator_id
        """

        with self._get_cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

        result = {}
        for operator_id, call_count, avg_score, evaluation_row_count in rows:
            evaluation_rows = int(evaluation_row_count or 0)
            avg_score_raw = float(avg_score) if avg_score is not None else None
            result[int(operator_id)] = {
                "call_count": int(call_count or 0),
                "avg_score": round(avg_score_raw, 2) if avg_score_raw is not None else None,
                "avg_score_raw": avg_score_raw,
                "evaluation_row_count": evaluation_rows,
                "has_evaluation_data": evaluation_rows > 0
            }
        return result

    def get_feedback_sla_operator_stats_for_period(self, operator_ids: Optional[List[int]] = None, *,
                                                   months=None, start=None, end=None, operator_months=None,
                                                   reference_date=None) -> Dict[int, Dict[str, Any]]:
        """Обратная связь по оценкам за ПЕРИОД «Аналитики» журнала (#389) — счёт тот
        же, что у get_feedback_sla_operator_stats_for_month."""
        rows = self.get_feedback_sla_rows_for_period(
            operator_ids=operator_ids, months=months, start=start, end=end,
            operator_months=operator_months, reference_date=reference_date)
        return self._feedback_sla_stats_from_rows(rows)

    def get_feedback_sla_operator_stats_for_month(
        self,
        month: str,
        operator_ids: Optional[List[int]] = None,
        supervisor_ids: Optional[List[int]] = None,
        reference_date=None
    ) -> Dict[int, Dict[str, Any]]:
        rows = self.get_feedback_sla_rows_for_month(
            month=month,
            operator_ids=operator_ids,
            supervisor_ids=supervisor_ids,
            reference_date=reference_date
        )
        return self._feedback_sla_stats_from_rows(rows)

    @staticmethod
    def _feedback_sla_stats_from_rows(rows) -> Dict[int, Dict[str, Any]]:
        """Строки SLA обратной связи → счётчики по оператору (месяц и период)."""
        result: Dict[int, Dict[str, Any]] = {}
        for item in rows:
            operator_id = int(item.get("operator_id"))
            bucket = result.setdefault(operator_id, {
                "operator_id": operator_id,
                "operator_name": item.get("operator_name") or "",
                "total_evaluated": 0,
                "feedback_count": 0,
                "feedback_on_time_count": 0,
                "feedback_overdue_count": 0,
                "pending_count": 0,
                "pending_overdue_count": 0,
                "overdue_count": 0
            })

            bucket["total_evaluated"] += 1
            status = str(item.get("status") or '').strip().lower()
            feedback_provided = bool(item.get("feedback_provided"))

            if feedback_provided:
                bucket["feedback_count"] += 1
                if status == 'on_time':
                    bucket["feedback_on_time_count"] += 1
                elif status == 'overdue':
                    bucket["feedback_overdue_count"] += 1
            else:
                bucket["pending_count"] += 1
                if status == 'overdue':
                    bucket["pending_overdue_count"] += 1

            if status == 'overdue':
                bucket["overdue_count"] += 1

        return result

        
    def get_operators_summary_for_month(self, month, supervisor_id=None):
        """
        Возвращает список сотрудников с количеством последних версий звонков за месяц.
        Включает:
        - операторов;
        - супервайзеров, если у них есть оценки за месяц.

        Последние версии определяются как MAX(created_at) для каждой комбинации:
        (phone_number, operator_id, month, appeal_date)
        Если supervisor_id задан — фильтрует операторов по этому SV и включает самого SV.

        Оценки разговоров с номерами «Реестра тестовых номеров» (test_numbers) в сводку не входят.
        """
        from test_numbers import keys as test_keys
        query = """
        WITH latest_versions AS (
            -- для каждой пары (phone_number, operator_id, month, appeal_date) берем последний created_at
            SELECT
                phone_number,
                operator_id,
                month,
                appeal_date,
                MAX(created_at) AS latest_date
            FROM calls
            WHERE month = %s
              AND """ + test_keys.sql_calls_not_test() + """
            GROUP BY phone_number, operator_id, month, appeal_date
        ),
        latest_calls AS (
            -- берем сами записи (последние версии) для указанного месяца и appeal_date
            SELECT c.*
            FROM calls c
            JOIN latest_versions lv
            ON c.phone_number = lv.phone_number
            AND c.operator_id = lv.operator_id
            AND c.month = lv.month
            AND ( (c.appeal_date IS NULL AND lv.appeal_date IS NULL) OR (c.appeal_date = lv.appeal_date) )
            AND c.created_at = lv.latest_date
            WHERE c.month = %s
        ),
        counts AS (
            -- считаем количество последних версий звонков по оператору;
            -- оценка «Тестирование знаний» звонком не считается
            SELECT
                operator_id,
                COUNT(*) AS evaluation_row_count,
                COUNT(*) FILTER (WHERE score IS NOT NULL AND survey_response_id IS NULL) AS call_count,
                AVG(score)::float AS avg_score
            FROM latest_calls
            GROUP BY operator_id
        )
        SELECT
            u.id AS operator_id,
            u.name AS operator_name,
            u.status,
            u.direction_id,
            d.name AS direction_name,
            u.supervisor_id,
            su.name AS supervisor_name,
            u.hire_date,
            COALESCE(c.call_count, 0) AS call_count,
            c.avg_score,
            LOWER(COALESCE(u.role, '')) AS role_norm,
            COALESCE(c.evaluation_row_count, 0) AS evaluation_row_count
        FROM users u
        LEFT JOIN directions d ON u.direction_id = d.id
        LEFT JOIN users su ON u.supervisor_id = su.id
        LEFT JOIN counts c ON c.operator_id = u.id
        WHERE (
            LOWER(COALESCE(u.role, '')) = 'operator'
            OR (
                LOWER(COALESCE(u.role, '')) IN ('sv', 'supervisor')
                AND COALESCE(c.call_count, 0) > 0
            )
        )
        """
        params = [month, month]

        if supervisor_id is not None:
            query += """
                AND (
                    u.supervisor_id = %s
                    OR (
                        LOWER(COALESCE(u.role, '')) IN ('sv', 'supervisor')
                        AND u.id = %s
                    )
                )
            """
            params.extend([supervisor_id, supervisor_id])

        query += " ORDER BY u.name"

        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            result = []
            for row in rows:
                role_norm = str(row[10] or '').strip().lower()
                evaluation_row_count = int(row[11] or 0)
                is_supervisor_row = role_norm in ('sv', 'supervisor')
                effective_supervisor_id = row[5]
                effective_supervisor_name = row[6]
                if is_supervisor_row:
                    effective_supervisor_id = row[0]
                    effective_supervisor_name = row[1]

                result.append({
                    "id": row[0],
                    "name": row[1],
                    "status": row[2],
                    "direction_id": row[3],
                    "direction_name": row[4],
                    "supervisor_id": effective_supervisor_id,
                    "supervisor_name": effective_supervisor_name,
                    "hire_date": row[7].strftime('%d-%m-%Y') if row[7] else None,
                    "call_count": int(row[8] or 0),
                    "avg_score": round(float(row[9]), 2) if row[9] is not None else None,
                    "role": role_norm,
                    "evaluation_row_count": evaluation_row_count,
                    "has_evaluation_data": evaluation_row_count > 0
                })
            return result

    def get_all_operators(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    u.id,
                    u.name,
                    u.direction_id,
                    u.hire_date,
                    u.supervisor_id,
                    u.scores_table_url,
                    u.hours_table_url,
                    d.name AS direction_name,
                    d.calculation_model_code,
                    -- Статус нужен сопоставлению имён из телефонии: уволенный
                    -- тёзка делает совпадение НЕОДНОЗНАЧНЫМ, и резолвер молча
                    -- выбрасывает звонки живого сотрудника (см.
                    -- _status_import_build_operator_lookup(exclude_fired)).
                    -- Колонка добавлена В КОНЕЦ: строки читают по номерам.
                    u.status
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE u.role = 'operator'
            """)
            return cursor.fetchall()
    
    def get_active_operators(self, direction_name=None):
        with self._get_cursor() as cursor:
            if direction_name:
                cursor.execute("""
                    SELECT u.id, u.name, u.direction_id
                    FROM users u
                    JOIN directions d ON u.direction_id = d.id
                    WHERE u.role = 'operator' AND u.is_active = TRUE
                      AND d.name = %s AND d.is_active = TRUE
                    ORDER BY u.name
                """, (direction_name,))
            else:
                cursor.execute("""
                    SELECT id, name, direction_id 
                    FROM users 
                    WHERE role = 'operator' AND is_active = TRUE
                    ORDER BY name
                """)
            return [{"id": row[0], "name": row[1], "direction_id": row[2]} for row in cursor.fetchall()]

    def _get_operator_offices_tx(self, cursor, operator_ids):
        """{operator_id: 'support'|'sales'} по отделу каждого оператора."""
        ids = [int(o) for o in (operator_ids or []) if o is not None]
        if not ids:
            return {}
        cursor.execute(
            """
            SELECT u.id, d.code
            FROM users u
            LEFT JOIN departments d ON d.id = u.department_id
            WHERE u.id = ANY(%s)
            """,
            (ids,),
        )
        return {int(r[0]): self._workplace_office_for_code(r[1]) for r in cursor.fetchall()}

    def create_operator_offline_activity(
        self,
        requester_id,
        requester_role,
        activity_date,
        start_time=None,
        end_time=None,
        comment=None,
        operator_id=None,
        group_id=None,
        scope_department_id=None
    ):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can create offline activity")

        issue_date_obj = self._parse_technical_issue_date(activity_date, field_name='date')
        start_time_obj = self._parse_technical_issue_time(start_time, field_name='start_time', default_value='00:00')
        end_time_obj = self._parse_technical_issue_time(end_time, field_name='end_time', default_value='23:59')
        if start_time_obj == end_time_obj:
            raise ValueError("start_time and end_time cannot be equal")

        operator_ids_norm = self._coerce_int_list([operator_id])
        if not operator_ids_norm:
            raise ValueError("Field 'operator_id' is required")

        group_id_int = None
        if group_id is not None and str(group_id).strip() != '':
            try:
                group_id_int = int(group_id)
            except (TypeError, ValueError):
                raise ValueError("Invalid group_id")
            if group_id_int <= 0:
                raise ValueError("Invalid group_id")

        comment_text = str(comment or '').strip() or None
        requester_id_int = int(requester_id)

        with self._get_cursor() as cursor:
            if group_id_int is not None:
                target_operator_id = int(operator_ids_norm[0])
                cursor.execute("SELECT id, department_id FROM groups WHERE id = %s", (group_id_int,))
                group_row = cursor.fetchone()
                if not group_row:
                    raise ValueError("Group not found")

                if role_norm == 'sv' and scope_department_id is not None:
                    if int(group_row[1] or 0) != int(scope_department_id):
                        raise ValueError("Forbidden group for sv")
                elif role_norm == 'sv':
                    if not self._supervisor_has_group_access_for_period_tx(
                        cursor, requester_id_int, group_id_int, issue_date_obj, issue_date_obj
                    ):
                        raise ValueError("Forbidden group for sv")

                cursor.execute("SELECT 1 FROM users WHERE id = %s", (target_operator_id,))
                if cursor.fetchone() is None:
                    raise ValueError("Operator not found")
                if not self._operator_in_group_on_date_tx(cursor, group_id_int, target_operator_id, issue_date_obj):
                    raise ValueError("Operator is not in selected group on activity date")
            else:
                target_operator_ids, _ = self._resolve_technical_issue_operator_ids_tx(
                    cursor=cursor,
                    requester_id=requester_id_int,
                    requester_role=role_norm,
                    operator_ids=operator_ids_norm,
                    direction_ids=None,
                    scope_department_id=scope_department_id
                )
                target_operator_id = int(target_operator_ids[0])

            overlaps_by_operator = self._find_shift_overlap_intervals_for_technical_issue_tx(
                cursor=cursor,
                operator_ids=[target_operator_id],
                issue_date_obj=issue_date_obj,
                start_time_obj=start_time_obj,
                end_time_obj=end_time_obj
            )
            eligible_operator_ids = sorted(overlaps_by_operator.keys())
            if not eligible_operator_ids:
                raise ValueError("Selected operator has no shifts in the specified time range")

            batch_id = str(uuid.uuid4())
            activity_values = []
            for resolved_operator_id in eligible_operator_ids:
                day_chunks = self._split_absolute_minutes_intervals_by_day(
                    overlaps_by_operator.get(resolved_operator_id) or [],
                    issue_date_obj
                )
                for chunk in day_chunks:
                    activity_values.append((
                        batch_id,
                        int(resolved_operator_id),
                        chunk['date'],
                        chunk['start_time'],
                        chunk['end_time'],
                        comment_text,
                        requester_id_int
                    ))

            if not activity_values:
                raise ValueError("No overlapping shift intervals were found")

            execute_values(
                cursor,
                """
                    INSERT INTO operator_offline_activities (
                        batch_id,
                        operator_id,
                        activity_date,
                        start_time,
                        end_time,
                        comment,
                        created_by
                    )
                    VALUES %s
                """,
                activity_values
            )

            return {
                'batch_id': batch_id,
                'created_count': len(activity_values),
                'created_operator_count': len(eligible_operator_ids),
                'operator_ids': eligible_operator_ids,
                'date': issue_date_obj.strftime('%Y-%m-%d'),
                'start_time': start_time_obj.strftime('%H:%M'),
                'end_time': end_time_obj.strftime('%H:%M'),
                'comment': comment_text
            }

    def get_operator_offline_activities(
        self,
        requester_id,
        requester_role,
        activity_date=None,
        date_from=None,
        date_to=None,
        operator_id=None,
        supervisor_id=None,
        group_id=None,
        limit=500,
        offset=0,
        scope_department_id=None
    ):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can view offline activities")

        limit_int = max(1, min(int(limit or 500), 5000))
        offset_int = max(0, int(offset or 0))
        requester_id_int = int(requester_id)

        where_parts = []
        params = []
        activity_date_obj = None
        date_from_obj = None
        date_to_obj = None
        supervisor_id_int = None
        if supervisor_id is not None and str(supervisor_id).strip() != '':
            try:
                supervisor_id_int = int(supervisor_id)
            except (TypeError, ValueError):
                raise ValueError("Invalid supervisor_id")
            if supervisor_id_int <= 0:
                raise ValueError("Invalid supervisor_id")

        if role_norm == 'sv' and scope_department_id is None and supervisor_id_int is None:
            supervisor_id_int = requester_id_int

        if not role_has_min(role_norm, 'admin') and scope_department_id is not None:
            where_parts.append("op.department_id = %s")
            params.append(int(scope_department_id))

        if supervisor_id_int is not None:
            where_parts.append("op.supervisor_id = %s")
            params.append(supervisor_id_int)

        if activity_date:
            activity_date_obj = self._parse_technical_issue_date(activity_date, field_name='date')
            where_parts.append("oa.activity_date = %s")
            params.append(activity_date_obj)
        else:
            if date_from:
                from_obj = self._parse_technical_issue_date(date_from, field_name='date_from')
                date_from_obj = from_obj
                where_parts.append("oa.activity_date >= %s")
                params.append(from_obj)
            if date_to:
                to_obj = self._parse_technical_issue_date(date_to, field_name='date_to')
                date_to_obj = to_obj
                where_parts.append("oa.activity_date <= %s")
                params.append(to_obj)

        operator_id_int = None
        if operator_id is not None and str(operator_id).strip() != '':
            try:
                operator_id_int = int(operator_id)
            except (TypeError, ValueError):
                raise ValueError("Invalid operator_id")
            where_parts.append("oa.operator_id = %s")
            params.append(operator_id_int)

        group_id_int = None
        if group_id is not None and str(group_id).strip() != '':
            try:
                group_id_int = int(group_id)
            except (TypeError, ValueError):
                raise ValueError("Invalid group_id")
            if group_id_int <= 0:
                raise ValueError("Invalid group_id")
            where_parts.append("""
                EXISTS (
                    SELECT 1
                    FROM group_operator_memberships gom
                    WHERE gom.operator_id = oa.operator_id
                      AND gom.group_id = %s
                      AND gom.start_date <= oa.activity_date
                      AND (gom.end_date IS NULL OR gom.end_date >= oa.activity_date)
                )
            """)
            params.append(group_id_int)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        base_sql = """
            FROM operator_offline_activities oa
            JOIN users op ON op.id = oa.operator_id
            LEFT JOIN users sv ON sv.id = op.supervisor_id
            LEFT JOIN users cb ON cb.id = oa.created_by
            LEFT JOIN directions d ON d.id = op.direction_id
        """

        with self._get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) {base_sql} {where_sql}", params)
            total = int(cursor.fetchone()[0] or 0)

            cursor.execute(
                f"""
                    SELECT
                        oa.id,
                        oa.batch_id::text,
                        oa.operator_id,
                        op.name,
                        op.supervisor_id,
                        sv.name,
                        op.direction_id,
                        d.name,
                        oa.activity_date,
                        oa.start_time,
                        oa.end_time,
                        oa.comment,
                        oa.created_by,
                        cb.name,
                        oa.created_at
                    {base_sql}
                    {where_sql}
                    ORDER BY oa.activity_date DESC, oa.start_time DESC, oa.created_at DESC, oa.id DESC
                    LIMIT %s OFFSET %s
                """,
                params + [limit_int, offset_int]
            )
            rows = cursor.fetchall() or []

            items = []
            for row in rows:
                start_time_text = row[9].strftime('%H:%M') if row[9] else None
                end_time_text = row[10].strftime('%H:%M') if row[10] else None
                duration_minutes = 0
                try:
                    if row[9] and row[10]:
                        start_min, end_min = self._schedule_interval_minutes(row[9], row[10])
                        duration_minutes = max(0, int(end_min - start_min))
                except Exception:
                    duration_minutes = 0
                items.append({
                    'id': int(row[0]),
                    'batch_id': row[1],
                    'operator_id': int(row[2]),
                    'operator_name': row[3],
                    'supervisor_id': int(row[4]) if row[4] is not None else None,
                    'supervisor_name': row[5],
                    'direction_id': int(row[6]) if row[6] is not None else None,
                    'direction_name': row[7],
                    'date': row[8].strftime('%Y-%m-%d') if row[8] else None,
                    'start_time': start_time_text,
                    'end_time': end_time_text,
                    'time_range': f"{start_time_text} - {end_time_text}" if start_time_text and end_time_text else None,
                    'comment': row[11],
                    'created_by_id': int(row[12]) if row[12] is not None else None,
                    'created_by_name': row[13],
                    'created_at': row[14].strftime('%Y-%m-%d %H:%M:%S') if row[14] else None,
                    'duration_minutes': int(duration_minutes),
                    'duration_hours': round(float(duration_minutes) / 60.0, 2)
                })

            phone_start_obj = activity_date_obj or date_from_obj
            phone_end_obj = activity_date_obj or date_to_obj
            if phone_start_obj and phone_end_obj:
                if phone_end_obj < phone_start_obj:
                    phone_start_obj, phone_end_obj = phone_end_obj, phone_start_obj

                operator_where_parts = []
                operator_params = []
                if not role_has_min(role_norm, 'admin') and scope_department_id is not None:
                    operator_where_parts.append("op.department_id = %s")
                    operator_params.append(int(scope_department_id))
                if supervisor_id_int is not None:
                    operator_where_parts.append("op.supervisor_id = %s")
                    operator_params.append(supervisor_id_int)
                if operator_id_int is not None:
                    operator_where_parts.append("op.id = %s")
                    operator_params.append(operator_id_int)
                if group_id_int is not None:
                    operator_where_parts.append("""
                        EXISTS (
                            SELECT 1
                            FROM group_operator_memberships gom
                            WHERE gom.operator_id = op.id
                              AND gom.group_id = %s
                              AND gom.start_date <= %s
                              AND (gom.end_date IS NULL OR gom.end_date >= %s)
                        )
                    """)
                    operator_params.extend([group_id_int, phone_end_obj, phone_start_obj])

                operator_where_sql = f"WHERE {' AND '.join(operator_where_parts)}" if operator_where_parts else ""
                cursor.execute(
                    f"""
                        SELECT
                            op.id,
                            op.name,
                            op.supervisor_id,
                            sv.name,
                            op.direction_id,
                            d.name
                        FROM users op
                        LEFT JOIN users sv ON sv.id = op.supervisor_id
                        LEFT JOIN directions d ON d.id = op.direction_id
                        {operator_where_sql}
                    """,
                    operator_params
                )
                operator_meta = {}
                for op_row in cursor.fetchall() or []:
                    op_id_int = int(op_row[0])
                    operator_meta[op_id_int] = {
                        'operator_name': op_row[1],
                        'supervisor_id': int(op_row[2]) if op_row[2] is not None else None,
                        'supervisor_name': op_row[3],
                        'direction_id': int(op_row[4]) if op_row[4] is not None else None,
                        'direction_name': op_row[5]
                    }

                phone_operator_ids = sorted(operator_meta.keys())
                if phone_operator_ids:
                    manual_phone_training_map, _manual_phone_training_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
                        cursor=cursor,
                        operator_ids=phone_operator_ids,
                        start_date=phone_start_obj,
                        end_date=phone_end_obj,
                        group_id=group_id_int
                    )
                    status_phone_training_map, _status_phone_training_totals = self._load_phone_shift_training_offline_intervals_by_operator_day_tx(
                        cursor=cursor,
                        operator_ids=phone_operator_ids,
                        start_date=phone_start_obj,
                        end_date=phone_end_obj,
                        group_id=group_id_int
                    )
                    phone_training_seen = {op_id: set() for op_id in phone_operator_ids}
                    phone_virtual_count = 0
                    for phone_training_map in (manual_phone_training_map, status_phone_training_map):
                        for op_id_int, days_map in (phone_training_map or {}).items():
                            try:
                                op_id_int = int(op_id_int)
                            except Exception:
                                continue
                            meta = operator_meta.get(op_id_int) or {}
                            for day_key, day_items in (days_map or {}).items():
                                for item in (day_items or []):
                                    start_sec = max(0, int(item.get('start_seconds') or 0))
                                    end_sec = max(0, int(item.get('end_seconds') or 0))
                                    if end_sec <= start_sec:
                                        continue
                                    signature = (str(item.get('date') or day_key), start_sec, end_sec)
                                    if signature in phone_training_seen.setdefault(op_id_int, set()):
                                        continue
                                    phone_training_seen[op_id_int].add(signature)
                                    duration_seconds = end_sec - start_sec
                                    duration_minutes = self._schedule_auto_seconds_to_display_minutes(duration_seconds)
                                    start_text = item.get('start_time')
                                    end_text = item.get('end_time')
                                    items.append({
                                        'id': item.get('id'),
                                        'batch_id': None,
                                        'operator_id': int(op_id_int),
                                        'operator_name': meta.get('operator_name'),
                                        'supervisor_id': meta.get('supervisor_id'),
                                        'supervisor_name': meta.get('supervisor_name'),
                                        'direction_id': meta.get('direction_id'),
                                        'direction_name': meta.get('direction_name'),
                                        'date': item.get('date'),
                                        'start_time': start_text,
                                        'end_time': end_text,
                                        'time_range': f"{start_text} - {end_text}" if start_text and end_text else None,
                                        'comment': WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT,
                                        'created_by_id': None,
                                        'created_by_name': 'Планировщик',
                                        'created_at': None,
                                        'duration_minutes': int(duration_minutes),
                                        'duration_seconds': int(duration_seconds),
                                        'duration_hours': round(float(duration_seconds) / 3600.0, 2),
                                        'source': item.get('source') or 'work_shift_training_status',
                                        'shift_type': WORK_SHIFT_TYPE_PHONE_SHIFT,
                                        'is_phone_shift_training': True,
                                        'read_only': True
                                    })
                                    phone_virtual_count += 1

                    items.sort(
                        key=lambda item: (
                            str(item.get('date') or ''),
                            str(item.get('start_time') or ''),
                            str(item.get('created_at') or ''),
                            str(item.get('id') or '')
                        ),
                        reverse=True
                    )
                    total += phone_virtual_count

            return {
                'total': total,
                'items': items
            }

    def delete_operator_offline_activity(self, requester_id, requester_role, activity_id, scope_department_id=None):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can delete offline activities")

        requester_id_int = int(requester_id)
        try:
            activity_id_int = int(activity_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid offline activity id")
        if activity_id_int <= 0:
            raise ValueError("Invalid offline activity id")

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    oa.id,
                    oa.batch_id::text,
                    oa.operator_id,
                    op.supervisor_id,
                    op.department_id,
                    oa.activity_date,
                    oa.start_time,
                    oa.end_time,
                    oa.comment
                FROM operator_offline_activities oa
                JOIN users op ON op.id = oa.operator_id
                WHERE oa.id = %s
                """,
                (activity_id_int,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Offline activity not found")

            supervisor_id = int(row[3]) if row[3] is not None else None
            operator_department_id = int(row[4]) if row[4] is not None else None
            if role_norm == 'sv':
                can_manage_by_scope = (
                    operator_department_id == int(scope_department_id)
                    if scope_department_id is not None
                    else supervisor_id == requester_id_int
                )
                if not can_manage_by_scope:
                    raise PermissionError("Forbidden")

            cursor.execute(
                """
                DELETE FROM operator_offline_activities
                WHERE id = %s
                RETURNING id
                """,
                (activity_id_int,)
            )
            deleted_row = cursor.fetchone()
            if not deleted_row:
                raise ValueError("Offline activity not found")

            return {
                'id': int(row[0]),
                'batch_id': row[1],
                'operator_id': int(row[2]),
                'date': row[5].strftime('%Y-%m-%d') if row[5] else None,
                'start_time': row[6].strftime('%H:%M') if row[6] else None,
                'end_time': row[7].strftime('%H:%M') if row[7] else None,
                'comment': row[8]
            }

    def update_operator_offline_activity(
        self,
        requester_id,
        requester_role,
        activity_id,
        activity_date=None,
        start_time=None,
        end_time=None,
        comment=None,
        operator_id=None,
        scope_department_id=None
    ):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can update offline activities")

        requester_id_int = int(requester_id)
        try:
            activity_id_int = int(activity_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid offline activity id")
        if activity_id_int <= 0:
            raise ValueError("Invalid offline activity id")

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    oa.id,
                    oa.batch_id::text,
                    oa.operator_id,
                    op.supervisor_id,
                    op.department_id,
                    oa.activity_date,
                    oa.start_time,
                    oa.end_time,
                    oa.comment,
                    oa.created_by,
                    cb.name
                FROM operator_offline_activities oa
                JOIN users op ON op.id = oa.operator_id
                LEFT JOIN users cb ON cb.id = oa.created_by
                WHERE oa.id = %s
                """,
                (activity_id_int,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Offline activity not found")

            current_operator_id = int(row[2])
            current_supervisor_id = int(row[3]) if row[3] is not None else None
            current_operator_department_id = int(row[4]) if row[4] is not None else None
            if role_norm == 'sv':
                can_manage_by_scope = (
                    current_operator_department_id == int(scope_department_id)
                    if scope_department_id is not None
                    else current_supervisor_id == requester_id_int
                )
                if not can_manage_by_scope:
                    raise PermissionError("Forbidden")

            target_operator_id = current_operator_id
            if operator_id is not None and str(operator_id).strip() != '':
                operator_ids_norm = self._coerce_int_list([operator_id])
                if not operator_ids_norm:
                    raise ValueError("Invalid operator_id")
                target_operator_ids, _ = self._resolve_technical_issue_operator_ids_tx(
                    cursor=cursor,
                    requester_id=requester_id_int,
                    requester_role=role_norm,
                    operator_ids=operator_ids_norm,
                    direction_ids=None,
                    scope_department_id=scope_department_id
                )
                target_operator_id = int(target_operator_ids[0])

            next_date_obj = (
                self._parse_technical_issue_date(activity_date, field_name='date')
                if activity_date is not None and str(activity_date).strip() != ''
                else row[5]
            )
            next_start_time_obj = (
                self._parse_technical_issue_time(start_time, field_name='start_time')
                if start_time is not None and str(start_time).strip() != ''
                else row[6]
            )
            next_end_time_obj = (
                self._parse_technical_issue_time(end_time, field_name='end_time')
                if end_time is not None and str(end_time).strip() != ''
                else row[7]
            )
            if next_start_time_obj == next_end_time_obj:
                raise ValueError("start_time and end_time cannot be equal")

            overlaps_by_operator = self._find_shift_overlap_intervals_for_technical_issue_tx(
                cursor=cursor,
                operator_ids=[target_operator_id],
                issue_date_obj=next_date_obj,
                start_time_obj=next_start_time_obj,
                end_time_obj=next_end_time_obj
            )
            if int(target_operator_id) not in overlaps_by_operator:
                raise ValueError("Selected operator has no shifts in the specified time range")

            comment_text = str(comment).strip() if comment is not None else row[8]
            if comment_text == '':
                comment_text = None

            cursor.execute(
                """
                UPDATE operator_offline_activities
                SET
                    operator_id = %s,
                    activity_date = %s,
                    start_time = %s,
                    end_time = %s,
                    comment = %s
                WHERE id = %s
                RETURNING id, batch_id::text, operator_id, activity_date, start_time, end_time, comment
                """,
                (
                    target_operator_id,
                    next_date_obj,
                    next_start_time_obj,
                    next_end_time_obj,
                    comment_text,
                    activity_id_int
                )
            )
            updated = cursor.fetchone()
            if not updated:
                raise ValueError("Offline activity not found")

            start_time_text = updated[4].strftime('%H:%M') if updated[4] else None
            end_time_text = updated[5].strftime('%H:%M') if updated[5] else None
            duration_minutes = 0
            try:
                if updated[4] and updated[5]:
                    start_min, end_min = self._schedule_interval_minutes(updated[4], updated[5])
                    duration_minutes = max(0, int(end_min - start_min))
            except Exception:
                duration_minutes = 0

            return {
                'id': int(updated[0]),
                'batch_id': updated[1],
                'operator_id': int(updated[2]),
                'date': updated[3].strftime('%Y-%m-%d') if updated[3] else None,
                'start_time': start_time_text,
                'end_time': end_time_text,
                'time_range': f"{start_time_text} - {end_time_text}" if start_time_text and end_time_text else None,
                'comment': updated[6],
                'created_by_id': int(row[9]) if row[9] is not None else None,
                'created_by_name': row[10],
                'duration_minutes': int(duration_minutes),
                'duration_hours': round(float(duration_minutes) / 60.0, 2)
            }

    def reassign_operator_history(self, operator_id, before_group_id, from_date):
        """Историческая коррекция перевода: оператор был в before_group_id ДО from_date,
        затем — в своей текущей (открытой) группе. Чинит членства по датам,
        перестамповывает daily_hours по границе и переагрегирует все затронутые
        месяцы. Модель/группа каждого месяца резолвятся по членству на конец месяца
        (_aggregate_month_from_daily_tx), поэтому полные месяцы до from_date уходят в
        операторскую группу с операторской моделью, а месяц-граница и далее остаются
        в текущей группе. Уникальность work_hours (operator_id, month) не трогаем —
        граничный месяц считается одной строкой по группе на конец месяца.
        daily_hours сохраняются (меняется только group_id) — потери данных нет.
        """
        operator_id = int(operator_id)
        before_group_id = int(before_group_id)
        if isinstance(from_date, str):
            from_date = datetime.strptime(from_date, '%Y-%m-%d').date()
        with self._get_cursor() as cursor:
            # Текущая открытая (целевая) группа оператора.
            cursor.execute(
                """
                SELECT group_id FROM group_operator_memberships
                WHERE operator_id = %s AND end_date IS NULL
                ORDER BY start_date DESC LIMIT 1
                """,
                (operator_id,),
            )
            row = cursor.fetchone()
            to_group_id = int(row[0]) if row and row[0] is not None else None
            # Самый ранний день оператора — старт прежней группы.
            cursor.execute("SELECT MIN(day) FROM daily_hours WHERE operator_id = %s", (operator_id,))
            r = cursor.fetchone()
            earliest = r[0] if r and r[0] else from_date
            # 1) Целевую открытую группу двигаем на from_date.
            cursor.execute(
                """
                UPDATE group_operator_memberships SET start_date = %s
                WHERE operator_id = %s AND end_date IS NULL
                """,
                (from_date, operator_id),
            )
            # 2) Членство в прежней группе [earliest, from_date-1] (идемпотентно).
            cursor.execute(
                """
                SELECT 1 FROM group_operator_memberships
                WHERE operator_id = %s AND group_id = %s AND start_date = %s
                """,
                (operator_id, before_group_id, earliest),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    """
                    INSERT INTO group_operator_memberships
                        (group_id, operator_id, start_date, end_date, created_at)
                    VALUES (%s, %s, %s, (%s::date - INTERVAL '1 day'), CURRENT_TIMESTAMP)
                    """,
                    (before_group_id, operator_id, earliest, from_date),
                )
            # 3) Перестамповать daily по границе.
            cursor.execute(
                "UPDATE daily_hours SET group_id = %s WHERE operator_id = %s AND day < %s",
                (before_group_id, operator_id, from_date),
            )
            if to_group_id is not None:
                cursor.execute(
                    "UPDATE daily_hours SET group_id = %s WHERE operator_id = %s AND day >= %s",
                    (to_group_id, operator_id, from_date),
                )
            # 4) Переагрегировать все затронутые месяцы (группа+модель — по членству).
            cursor.execute(
                "SELECT DISTINCT TO_CHAR(day, 'YYYY-MM') FROM daily_hours WHERE operator_id = %s ORDER BY 1",
                (operator_id,),
            )
            months = [m[0] for m in (cursor.fetchall() or [])]
            for m in months:
                self._aggregate_month_from_daily_tx(cursor, operator_id, m)
        return {
            'operator_id': operator_id,
            'before_group_id': before_group_id,
            'to_group_id': to_group_id,
            'from_date': str(from_date),
            'reaggregated_months': months,
        }

    def _load_operator_calculation_models_tx(self, cursor, operator_ids, as_of=None):
        """
        Модель расчёта по операторам.

        as_of=None — legacy-поведение: модель берётся из ТЕКУЩЕГО направления
        оператора (users.direction_id -> directions.calculation_model_code). Так
        работают не привязанные к периоду вызовы (классификация статусов на
        пересчёте), чтобы их поведение не менялось.

        as_of=<date> — group-aware: модель берётся из группы оператора, активной
        на эту дату (group_operator_memberships -> groups.calculation_model_code).
        Если членства за период нет (например, прошлые месяцы до сидинга групп) —
        fallback на текущее направление. Это то, что делает прошлые месяцы
        устойчивыми к смене направления/роли оператора.
        """
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        result = {op_id: CALCULATION_MODEL_OPERATOR for op_id in op_ids}
        if not op_ids:
            return result
        if as_of is None:
            cursor.execute(
                """
                SELECT u.id, d.name, d.calculation_model_code
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE u.id = ANY(%s)
                """,
                (op_ids,)
            )
            for op_id, direction_name, model_code in cursor.fetchall() or []:
                result[int(op_id)] = self._normalize_calculation_model_code(model_code, direction_name)
            return result
        # Group-aware: модель из активной на as_of группы; fallback — направление.
        cursor.execute(
            """
            SELECT u.id, g.calculation_model_code AS group_model,
                   d.name AS direction_name, d.calculation_model_code AS direction_model
            FROM users u
            LEFT JOIN LATERAL (
                SELECT gr.calculation_model_code
                FROM group_operator_memberships gom
                JOIN groups gr ON gr.id = gom.group_id
                WHERE gom.operator_id = u.id
                  AND gom.start_date <= %s
                  AND (gom.end_date IS NULL OR gom.end_date >= %s)
                ORDER BY gom.start_date DESC
                LIMIT 1
            ) g ON TRUE
            LEFT JOIN directions d ON d.id = u.direction_id
            WHERE u.id = ANY(%s)
            """,
            (as_of, as_of, op_ids)
        )
        for op_id, group_model, direction_name, direction_model in cursor.fetchall() or []:
            chosen = group_model if group_model is not None else direction_model
            result[int(op_id)] = self._normalize_calculation_model_code(chosen, direction_name)
        return result

    def _get_operator_calculation_model_tx(self, cursor, operator_id, as_of=None):
        model_map = self._load_operator_calculation_models_tx(cursor, [operator_id], as_of=as_of)
        return model_map.get(int(operator_id), CALCULATION_MODEL_OPERATOR)

    def get_operator_calculation_models_as_of(self, operator_ids, as_of):
        """Публичная обёртка над group-aware резолвом модели расчёта на дату.

        Возвращает {operator_id: model_code}, где модель берётся из группы
        оператора, активной на `as_of` (group_operator_memberships ->
        groups.calculation_model_code); при отсутствии членства за день — fallback
        на текущее направление. Используется синхронизацией Oktell, чтобы тянуть и
        сохранять операторские статы только за те дни, когда оператор был в группе
        с операторской моделью (см. _oktell_operator_model_gate в bot_schedule2)."""
        op_ids = [int(v) for v in (operator_ids or []) if v is not None]
        if not op_ids:
            return {}
        with self._get_cursor() as cursor:
            return dict(self._load_operator_calculation_models_tx(cursor, op_ids, as_of=as_of))

    def _get_operator_month_segments_tx(self, cursor, operator_id, month):
        """Сегменты членства оператора за месяц (клиппнутые к месяцу) + per-segment агрегаты
        через _aggregate_segment_from_daily_tx. [] если оператор за месяц не был ни в одной
        группе (тогда вызывающий использует общий месячный итог из work_hours).
        Используется ридерами для раздельных данных при переводе посреди месяца."""
        operator_id = int(operator_id)
        year, mon = map(int, str(month).split('-'))
        last_day = calendar.monthrange(year, mon)[1]
        start = date(year, mon, 1)
        end = date(year, mon, last_day)
        cursor.execute("""
            SELECT gom.group_id, gr.name, gr.direction_id, d.name,
                   """ + self._GROUP_MEMBER_MODEL_SQL + """,
                   gom.start_date, gom.end_date
            FROM group_operator_memberships gom
            JOIN groups gr ON gr.id = gom.group_id
            LEFT JOIN directions d ON d.id = gr.direction_id
            WHERE gom.operator_id = %s AND gom.start_date <= %s
              AND (gom.end_date IS NULL OR gom.end_date >= %s)
            ORDER BY gom.start_date
        """, (operator_id, end, start))
        segments = []
        for g_id, g_name, dir_id, dir_name, model_code, seg_start, seg_end in cursor.fetchall() or []:
            clipped_start = seg_start if (seg_start and seg_start > start) else start
            clipped_end = seg_end if (seg_end and seg_end < end) else end
            model = normalize_calculation_model_code(model_code, dir_name)
            aggr = self._aggregate_segment_from_daily_tx(
                cursor, operator_id, month,
                int(clipped_start.day), int(clipped_end.day), int(g_id), model
            )
            segments.append({
                "group_id": int(g_id),
                "group_name": g_name,
                "direction_id": dir_id,
                "direction_name": dir_name,
                "calculation_model_code": model,
                "start_day": int(clipped_start.day),
                "end_day": int(clipped_end.day),
                "is_current": seg_end is None,
                "aggregates": aggr,
            })
        return segments

    def _load_segments_by_operator_tx(self, cursor, op_ids, start, end, with_dates=False):
        """Батч: {operator_id: [сегменты членства за месяц]} (клиппнутые к месяцу, с моделью
        группы) — БЕЗ агрегатов. Для отчётов: разбивка строки оператора по группам/моделям.
        start/end — date границы месяца.

        with_dates — ещё и даты сегмента (start_date/end_date, ISO): «Аналитике»
        журнала за период длиннее месяца (#389) номер дня ни о чём не говорит."""
        out = {}
        if not op_ids:
            return out
        cursor.execute("""
            SELECT gom.operator_id, gom.group_id, gr.name,
                   """ + self._GROUP_MEMBER_MODEL_SQL + """, d.name,
                   gom.start_date, gom.end_date
            FROM group_operator_memberships gom
            JOIN groups gr ON gr.id = gom.group_id
            LEFT JOIN directions d ON d.id = gr.direction_id
            WHERE gom.operator_id = ANY(%s) AND gom.start_date <= %s
              AND (gom.end_date IS NULL OR gom.end_date >= %s)
            ORDER BY gom.operator_id, gom.start_date
        """, (list(op_ids), end, start))
        for op_id, g_id, g_name, model_code, dir_name, seg_start, seg_end in cursor.fetchall() or []:
            cs = seg_start if (seg_start and seg_start > start) else start
            ce = seg_end if (seg_end and seg_end < end) else end
            segment = {
                "group_id": int(g_id),
                "group_name": g_name,
                "direction_name": dir_name,
                "calculation_model_code": normalize_calculation_model_code(model_code, dir_name),
                "start_day": int(cs.day),
                "end_day": int(ce.day),
                "is_current": seg_end is None,
            }
            if with_dates:
                segment["start_date"] = cs.isoformat()
                segment["end_date"] = ce.isoformat()
            out.setdefault(int(op_id), []).append(segment)
        return out

    def get_operator_segments_for_month(self, month, operator_ids):
        """Публичный батч для отчётов: {operator_id: [сегменты группы за месяц с моделью]}.
        Пусто для операторов без членств. Используется для разбивки строки отчёта по группам."""
        year, mon = map(int, str(month).split('-'))
        last = calendar.monthrange(year, mon)[1]
        start = date(year, mon, 1)
        end = date(year, mon, last)
        ids = [int(x) for x in (operator_ids or []) if x is not None]
        if not ids:
            return {}
        with self._get_cursor() as cursor:
            return self._load_segments_by_operator_tx(cursor, ids, start, end)

    def _live_status_track_operator_ids_tx(self, cursor, operator_ids=None):
        """Из переданных операторов — те, чьи статусы приезжают пушем."""
        codes = [str(code).strip().lower() for code in LIVE_STATUS_TRACK_DEPARTMENT_CODES if str(code).strip()]
        if not codes:
            return []
        query = """
            SELECT u.id
            FROM users u
            JOIN departments d ON d.id = u.department_id
            WHERE LOWER(d.code) = ANY(%s)
              AND u.role = 'operator'
              AND (u.status IS NULL OR u.status <> 'fired')
        """
        params = [codes]
        op_ids = [int(v) for v in (operator_ids or []) if v is not None]
        if operator_ids is not None:
            if not op_ids:
                return []
            query += " AND u.id = ANY(%s)"
            params.append(op_ids)
        query += " ORDER BY u.id"
        cursor.execute(query, params)
        return [int(row[0]) for row in (cursor.fetchall() or [])]

    def get_live_status_track_operator_ids(self, operator_ids=None):
        with self._get_cursor() as cursor:
            return self._live_status_track_operator_ids_tx(cursor, operator_ids=operator_ids)

    def _load_live_status_tail_for_operators(self, cursor, operator_ids, as_of=None, known_until=None):
        """{operator_id: хвост} — текущий незакрытый статус каждого оператора.

        Хвост отдаётся отрезком той же формы, что и обычный сегмент, поэтому
        дальше живёт обычной полосой. Рамки (докуда тянуть и с какого события
        статус вообще считается текущим) ставит _live_status_tail_window_tx —
        здесь, на сервере, чтобы у оператора и у руководителя они совпадали.
        Признак isLiveTail нужен интерфейсу для одного: соответствие идущей
        смены считается до этого момента, а не на всю её длину.
        """
        result = {}
        live_ids = self._live_status_track_operator_ids_tx(cursor, operator_ids=operator_ids)
        if not live_ids:
            return result

        as_of_dt = as_of if isinstance(as_of, datetime) else datetime.now()
        # То же окно, что у хвоста в пересборке: событие старше двух суток —
        # это не «текущий статус», а забытый телефон.
        window_start = as_of_dt - timedelta(hours=48)
        action_status_keys = sorted({
            self._normalize_import_status_key(item)
            for item in CHAT_MANAGER_ACTION_STATUS_KEYS
            if self._normalize_import_status_key(item)
        })

        cursor.execute(
            """
            SELECT DISTINCT ON (operator_id)
                operator_id, event_at, status_key, state_note
            FROM operator_status_events
            WHERE operator_id = ANY(%s)
              AND COALESCE(event_kind, 'status') <> 'action'
              AND COALESCE(is_authoritative, FALSE) = FALSE
              AND NOT (LOWER(TRIM(status_key)) = ANY(%s))
              AND event_at >= %s
              AND event_at <= %s
            ORDER BY operator_id, event_at DESC, id DESC
            """,
            (live_ids, action_status_keys, window_start, as_of_dt)
        )
        rows = cursor.fetchall() or []
        if not rows:
            return result

        row_operator_ids = [int(row[0]) for row in rows]
        calculation_model_by_operator = self._load_operator_calculation_models_tx(cursor, row_operator_ids)
        tail_window_by_operator = self._live_status_tail_window_tx(cursor, row_operator_ids, as_of_dt)
        for operator_id, event_at_value, status_key, state_note in rows:
            if not isinstance(event_at_value, datetime) or event_at_value >= as_of_dt:
                continue
            op_id = int(operator_id)
            window = tail_window_by_operator.get(op_id)
            if window is None:
                continue
            tail_floor, tail_end = window
            if tail_end <= event_at_value or event_at_value < tail_floor:
                continue
            # Пол по смене проверен по СОБЫТИЮ, а рисуем с того места, где
            # кончились сохранённые отрезки: иначе хвост лёг бы поверх них.
            tail_start = event_at_value
            already_until = (known_until or {}).get(op_id)
            if isinstance(already_until, datetime) and already_until > tail_start:
                tail_start = min(already_until, tail_end)
            if tail_end <= tail_start:
                continue
            payload = self._imported_status_segment_payload(
                calculation_model=calculation_model_by_operator.get(op_id, CALCULATION_MODEL_OPERATOR),
                status_date_value=tail_start.date(),
                start_at_value=tail_start,
                end_at_value=tail_end,
                duration_sec=int((tail_end - tail_start).total_seconds()),
                status_key=status_key,
                state_note=state_note
            )
            if payload is None:
                continue
            payload['isLiveTail'] = True
            result[op_id] = payload
        return result

    def _attach_live_status_tail_to_operators(self, cursor, operators_map, operator_ids, as_of=None):
        known_until = {}
        for op_id in (operator_ids or []):
            target = operators_map.get(op_id)
            if target is None:
                continue
            last_end = self._timeline_days_last_end(target.get('importedStatusTimelineDays'))
            if last_end is not None:
                known_until[int(op_id)] = last_end
        tails = self._load_live_status_tail_for_operators(
            cursor, operator_ids, as_of=as_of, known_until=known_until
        )
        as_of_dt = as_of if isinstance(as_of, datetime) else datetime.now()
        for op_id in (operator_ids or []):
            target = operators_map.get(op_id)
            if target is None:
                continue
            tail = tails.get(int(op_id))
            if tail is None:
                continue
            target['liveStatusTail'] = tail
            target['statusTrackAsOf'] = as_of_dt.isoformat(timespec='seconds')

    def _load_offline_activity_segments_for_operators(self, cursor, operator_ids, start_date_obj=None, end_date_obj=None):
        op_ids = [int(v) for v in (operator_ids or []) if v is not None]
        result = {op_id: {} for op_id in op_ids}
        if not op_ids:
            return result

        query = """
            SELECT
                id,
                operator_id,
                batch_id,
                activity_date,
                start_time,
                end_time,
                comment
            FROM operator_offline_activities
            WHERE operator_id = ANY(%s)
        """
        params = [op_ids]
        if start_date_obj:
            query += " AND activity_date >= %s"
            params.append(start_date_obj - timedelta(days=1))
        if end_date_obj:
            query += " AND activity_date <= %s"
            params.append(end_date_obj + timedelta(days=1))

        query += " ORDER BY operator_id, activity_date, start_time, end_time, id"
        cursor.execute(query, params)
        rows = cursor.fetchall() or []

        for row in rows:
            activity_id, operator_id, batch_id, activity_date_value, start_time_value, end_time_value, comment = row
            op_id = int(operator_id)
            if op_id not in result or activity_date_value is None:
                continue

            start_min, end_min = self._schedule_interval_minutes(start_time_value, end_time_value)
            day_chunks = self._split_absolute_minutes_intervals_by_day(
                [{'start': int(start_min), 'end': int(end_min)}],
                activity_date_value
            )
            for chunk in day_chunks:
                chunk_date = chunk.get('date')
                if not chunk_date:
                    continue
                if start_date_obj and chunk_date < start_date_obj:
                    continue
                if end_date_obj and chunk_date > end_date_obj:
                    continue

                day_key = chunk_date.strftime('%Y-%m-%d')
                start_min_local = int(chunk.get('start_min') or 0)
                end_min_local = int(chunk.get('end_min') or 0)
                if end_min_local <= start_min_local:
                    continue

                comment_text = str(comment or '').strip()
                result[op_id].setdefault(day_key, []).append({
                    'id': int(activity_id),
                    'batch_id': str(batch_id) if batch_id is not None else None,
                    'date': day_key,
                    'start': chunk.get('start_time') or _minutes_to_time(start_min_local),
                    'end': chunk.get('end_time') or _minutes_to_time(end_min_local),
                    'startMin': start_min_local,
                    'endMin': end_min_local,
                    'comment': comment_text
                })

        shift_query = """
            SELECT
                id,
                operator_id,
                shift_date,
                start_time,
                end_time
            FROM work_shifts
            WHERE operator_id = ANY(%s)
              AND COALESCE(shift_type, 'regular') = 'office_practice'
        """
        shift_params = [op_ids]
        if start_date_obj:
            shift_query += " AND shift_date >= %s"
            shift_params.append(start_date_obj - timedelta(days=1))
        if end_date_obj:
            shift_query += " AND shift_date <= %s"
            shift_params.append(end_date_obj + timedelta(days=1))

        shift_query += " ORDER BY operator_id, shift_date, start_time, end_time, id"
        cursor.execute(shift_query, shift_params)
        shift_rows = cursor.fetchall() or []

        for shift_id, operator_id, shift_date_value, start_time_value, end_time_value in shift_rows:
            op_id = int(operator_id)
            if op_id not in result or shift_date_value is None:
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
                if start_date_obj and chunk_date < start_date_obj:
                    continue
                if end_date_obj and chunk_date > end_date_obj:
                    continue

                day_key = chunk_date.strftime('%Y-%m-%d')
                start_min_local = int(chunk.get('start_min') or 0)
                end_min_local = int(chunk.get('end_min') or 0)
                if end_min_local <= start_min_local:
                    continue

                result[op_id].setdefault(day_key, []).append({
                    'id': f"practice-shift-{int(shift_id)}",
                    'batch_id': None,
                    'date': day_key,
                    'start': chunk.get('start_time') or _minutes_to_time(start_min_local),
                    'end': chunk.get('end_time') or _minutes_to_time(end_min_local),
                    'startMin': start_min_local,
                    'endMin': end_min_local,
                    'comment': WORK_SHIFT_OFFICE_PRACTICE_OFFLINE_COMMENT,
                    'source': 'work_shift',
                    'shift_type': WORK_SHIFT_TYPE_OFFICE_PRACTICE,
                    'isPracticeShift': True,
                    'readOnly': True
                })

        if start_date_obj and end_date_obj:
            manual_phone_training_map, _manual_phone_training_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start_date_obj,
                end_date=end_date_obj
            )
            status_phone_training_map, _status_phone_training_totals = self._load_phone_shift_training_offline_intervals_by_operator_day_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start_date_obj,
                end_date=end_date_obj
            )
            phone_training_seen = {op_id: set() for op_id in op_ids}
            for phone_training_map in (manual_phone_training_map, status_phone_training_map):
                for op_id, days_map in (phone_training_map or {}).items():
                    try:
                        op_id = int(op_id)
                    except Exception:
                        continue
                    for day_key, items in (days_map or {}).items():
                        for item in (items or []):
                            start_sec = max(0, int(item.get('start_seconds') or 0))
                            end_sec = max(0, int(item.get('end_seconds') or 0))
                            if end_sec <= start_sec:
                                continue
                            signature = (str(item.get('date') or day_key), start_sec, end_sec)
                            if signature in phone_training_seen.setdefault(op_id, set()):
                                continue
                            phone_training_seen[op_id].add(signature)
                            start_min_local = max(0, min(1440, int(start_sec // 60)))
                            end_min_local = max(0, min(1440, int((end_sec + 59) // 60)))
                            if end_min_local <= start_min_local:
                                end_min_local = min(1440, start_min_local + 1)
                            result.setdefault(op_id, {}).setdefault(day_key, []).append({
                                'id': item.get('id'),
                                'batch_id': None,
                                'date': day_key,
                                'start': item.get('start_time') or _minutes_to_time(start_min_local),
                                'end': item.get('end_time') or _minutes_to_time(end_min_local),
                                'startMin': start_min_local,
                                'endMin': end_min_local,
                                'durationSeconds': int(end_sec - start_sec),
                                'comment': WORK_SHIFT_PHONE_SHIFT_OFFLINE_COMMENT,
                                'source': item.get('source') or 'work_shift_training_status',
                                'shift_type': WORK_SHIFT_TYPE_PHONE_SHIFT,
                                'isPhoneShiftTraining': True,
                                'readOnly': True
                            })

        def _segment_sort_id(value):
            raw_id = value.get('id') if isinstance(value, dict) else None
            try:
                return int(raw_id)
            except Exception:
                return 0

        for op_id, days_map in result.items():
            for day_key, items in list(days_map.items()):
                items_sorted = sorted(
                    (items or []),
                    key=lambda seg: (int(seg.get('startMin', 0)), int(seg.get('endMin', 0)), _segment_sort_id(seg))
                )
                days_map[day_key] = items_sorted

        return result

    def _attach_offline_activity_segments_to_operators(self, cursor, operators_map, operator_ids, start_date_obj=None, end_date_obj=None):
        timeline_map = self._load_offline_activity_segments_for_operators(
            cursor=cursor,
            operator_ids=operator_ids,
            start_date_obj=start_date_obj,
            end_date_obj=end_date_obj
        )
        for op_id in (operator_ids or []):
            target = operators_map.get(op_id)
            if target is None:
                continue
            target['offlineActivityTimelineDays'] = timeline_map.get(int(op_id), {})

    def find_operator_by_name_fuzzy(self, name: str):
        """Попытаться найти оператора по ФИО. Сначала точное совпадение (case-insensitive),
        затем попытка по фамилии (ILIKE '%surname%'). Возвращает запись пользователя или None.
        """
        if not name:
            return None
        name = name.strip()
        with self._get_cursor() as cursor:
            # exact case-insensitive match
            cursor.execute("""
                SELECT id, telegram_id, name, role, direction_id, hire_date, supervisor_id, login
                FROM users WHERE LOWER(name) = LOWER(%s) AND role = 'operator' LIMIT 1
            """, (name,))
            row = cursor.fetchone()
            if row:
                return row

            # try surname match: take first token as likely surname
            tokens = re.split(r'\s+', name)
            if tokens:
                surname = tokens[0]
                cursor.execute("""
                    SELECT id, telegram_id, name, role, direction_id, hire_date, supervisor_id, login
                    FROM users WHERE name ILIKE %s AND role = 'operator' LIMIT 1
                """, (f"%{surname}%",))
                row = cursor.fetchone()
                if row:
                    return row

        return None

    def _get_visible_operator_ids_for_requester_tx(self, cursor, requester_id, requester_role, scope_department_id=None):
        role = self._normalize_survey_role(requester_role)
        requester_id = int(requester_id)

        if role_has_min(role, 'admin') or role == 'trainer':
            cursor.execute("""
                SELECT id
                FROM users
                WHERE LOWER(TRIM(COALESCE(role, ''))) = 'operator'
                  AND COALESCE(NULLIF(LOWER(TRIM(COALESCE(status, 'working'))), ''), 'working') <> 'fired'
            """)
            return [int(row[0]) for row in cursor.fetchall()]

        if role == 'sv':
            params = []
            scope_filter = ""
            if scope_department_id is not None:
                scope_filter = "AND department_id = %s"
                params.append(int(scope_department_id))
            cursor.execute(f"""
                SELECT id
                FROM users
                WHERE LOWER(TRIM(COALESCE(role, ''))) = 'operator'
                  AND COALESCE(NULLIF(LOWER(TRIM(COALESCE(status, 'working'))), ''), 'working') NOT IN ('fired', 'dismissal')
                  {scope_filter}
            """, tuple(params))
            return [int(row[0]) for row in cursor.fetchall()]

        if role == 'operator':
            return [requester_id]

        return []

    def get_visible_operator_ids_for_requester(self, requester_id, requester_role, scope_department_id=None):
        with self._get_cursor() as cursor:
            return self._get_visible_operator_ids_for_requester_tx(
                cursor,
                requester_id,
                requester_role,
                scope_department_id=scope_department_id
            )
