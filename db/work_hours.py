"""База данных: учёт часов.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    Any,
    CALCULATION_MODEL_CHAT_MANAGER,
    CALCULATION_MODEL_TEZ_OP,
    calendar,
    date,
    datetime,
    Dict,
    Json,
    List,
    logging,
    normalize_calculation_model_code,
    Optional,
    re,
    SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES,
    timedelta,
    Tuple,
)


class WorkHoursMixin:
    def insert_or_update_daily_hours(self, operator_id, day, work_time=0.0, break_time=0.0,
                                    talk_time=0.0, calls=0, efficiency=0.0,
                                    fine_amount=0.0, fine_reason=None, fine_comment=None,
                                    fines: List[Dict[str, Any]] = None,
                                    bonuses: List[Dict[str, Any]] = None,
                                    group_id=None):
        """
        Вставляет/обновляет запись daily_hours (operator_id + day).
        efficiency и fine_amount ожидаются в часах/суммах соответственно.

        group_id — группа, к которой относится этот день оператора. Уникальность
        остаётся по (operator_id, day): правило «один оператор = одна группа в день»
        делает её совместимой, group_id просто меняется по дням. Если group_id не
        передан, существующее значение сохраняется (COALESCE), а при отсутствии —
        проставляется активная группа оператора на эту дату.
        """
        if isinstance(day, str):
            day = datetime.strptime(day, "%Y-%m-%d").date()
        with self._get_cursor() as cursor:
            # Если group_id не задан явно — резолвим активную группу оператора на дату.
            if group_id is None:
                group_id = self._get_operator_group_id_tx(cursor, operator_id, day)
            # Insert or update daily_hours and return its id for managing daily_fines
            cursor.execute("""
                INSERT INTO daily_hours (
                    operator_id, day, work_time, break_time, talk_time, calls, efficiency,
                    fine_amount, fine_reason, fine_comment, group_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (operator_id, day)
                DO UPDATE SET
                    work_time = EXCLUDED.work_time,
                    break_time = EXCLUDED.break_time,
                    talk_time = EXCLUDED.talk_time,
                    calls = EXCLUDED.calls,
                    efficiency = EXCLUDED.efficiency,
                    fine_amount = EXCLUDED.fine_amount,
                    fine_reason = EXCLUDED.fine_reason,
                    fine_comment = EXCLUDED.fine_comment,
                    group_id = COALESCE(EXCLUDED.group_id, daily_hours.group_id),
                    created_at = CURRENT_TIMESTAMP
                RETURNING id
            """, (operator_id, day, work_time, break_time, talk_time, calls, efficiency,
                float(fine_amount) if fine_amount is not None else 0.0,
                fine_reason, fine_comment, group_id))
            res = cursor.fetchone()
            daily_id = res[0] if res else None

            # If fines provided, replace existing fines for this daily_hours record
            if daily_id and isinstance(fines, list):
                # remove previous fines for this daily row
                cursor.execute("DELETE FROM daily_fines WHERE daily_hours_id = %s", (daily_id,))
                insert_vals = []
                total_amount = 0.0
                first_reason = None
                comments = []
                for f in fines:
                    amt = float(f.get('amount') or 0)
                    reason = f.get('reason')
                    comment = f.get('comment')
                    insert_vals.append((daily_id, operator_id, day, amt, reason, comment))
                    total_amount += amt
                    if not first_reason and reason:
                        first_reason = reason
                    if comment:
                        comments.append(str(comment))

                if insert_vals:
                    cursor.executemany(
                        "INSERT INTO daily_fines (daily_hours_id, operator_id, day, amount, reason, comment) VALUES (%s, %s, %s, %s, %s, %s)",
                        insert_vals
                    )

                # update aggregated fields in daily_hours for backward compatibility
                agg_comment = '; '.join(comments) if comments else None
                cursor.execute("""
                    UPDATE daily_hours SET fine_amount = %s, fine_reason = %s, fine_comment = %s
                    WHERE id = %s
                """, (float(total_amount), first_reason, agg_comment, daily_id))

            if daily_id and isinstance(bonuses, list):
                cursor.execute("DELETE FROM daily_bonuses WHERE daily_hours_id = %s", (daily_id,))
                insert_bonus_vals = []

                for b in bonuses:
                    if not isinstance(b, dict):
                        continue

                    bonus_type = str(b.get('type') or b.get('bonus_type') or '').strip()
                    if not bonus_type:
                        continue

                    quantity_raw = b.get('quantity')
                    try:
                        quantity = max(int(float(quantity_raw)), 1) if quantity_raw not in (None, '') else 1
                    except Exception:
                        quantity = 1

                    training_hours_raw = b.get('training_hours')
                    if training_hours_raw in (None, ''):
                        training_hours_raw = b.get('hours')
                    try:
                        training_hours = float(training_hours_raw) if training_hours_raw not in (None, '') else 0.0
                    except Exception:
                        training_hours = 0.0
                    if training_hours < 0:
                        training_hours = 0.0

                    if bonus_type == 'Обучение':
                        quantity = 1

                    amount_raw = b.get('amount')
                    if bonus_type == 'Обучение':
                        amount = float(training_hours * 500.0)
                    elif bonus_type in ('Приведи друга', 'Съемки'):
                        amount = float(quantity * 5000.0)
                    elif amount_raw in (None, ''):
                        amount = 0.0
                    else:
                        try:
                            amount = float(amount_raw)
                        except Exception:
                            amount = 0.0

                    friend_names = str(b.get('friend_names') or '').strip() or None
                    video_links = str(b.get('video_links') or '').strip() or None
                    comment = str(b.get('comment') or '').strip() or None

                    insert_bonus_vals.append((
                        daily_id,
                        operator_id,
                        day,
                        bonus_type,
                        int(quantity),
                        float(training_hours) if bonus_type == 'Обучение' else None,
                        float(amount),
                        friend_names,
                        video_links,
                        comment
                    ))

                if insert_bonus_vals:
                    cursor.executemany(
                        """
                        INSERT INTO daily_bonuses (
                            daily_hours_id, operator_id, day, bonus_type, quantity, training_hours, amount, friend_names, video_links, comment
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        insert_bonus_vals
                    )

    def get_daily_hours_for_operator_month(self, operator_id: int, month: str):
        """
        Возвращает daily_hours для одного оператора за месяц YYYY-MM,
        а также norm_hours, aggregates (из work_hours) и rate/name из users.

        Включает поля штрафов из daily_hours: fine_amount, fine_reason, fine_comment
        и агрегированное поле fines из work_hours.

        Результат:
        {
            "month": month,
            "days_in_month": <int>,
            "operator": { ... }
        }
        """
        import calendar as _py_calendar
        from datetime import date as _date

        # validate month format YYYY-MM and compute date range
        try:
            year, mon = map(int, month.split('-'))
            days_in_month = _py_calendar.monthrange(year, mon)[1]
            start = _date(year, mon, 1)
            end = _date(year, mon, days_in_month)
        except Exception as e:
            raise ValueError("Invalid month format, expected YYYY-MM") from e

        with self._get_cursor() as cursor:
            # 1) Получаем daily_hours (одним запросом), теперь с информацией о штрафах
            cursor.execute(
                """
                SELECT day, work_time, break_time, talk_time, calls, efficiency,
                    fine_amount, fine_reason, fine_comment,
                    COALESCE(no_phone_minutes, 0), COALESCE(no_phone_seconds, 0),
                    COALESCE(extra_metrics, '{}'::jsonb)
                FROM daily_hours
                WHERE operator_id = %s AND day >= %s AND day <= %s
                ORDER BY day
                """,
                (operator_id, start, end),
            )
            daily_rows = cursor.fetchall()

            # build daily map: { "1": {...}, "2": {...}, ... }
            daily_map = {}
            no_phone_hours = 0.0
            for row in daily_rows:
                # row: (day, work_time, break_time, talk_time, calls, efficiency, fine_amount, fine_reason, fine_comment)
                day_obj = row[0]
                day_key = str(int(day_obj.day))
                no_phone_minutes = int(row[9] or 0)
                no_phone_seconds = int(row[10] or 0)
                no_phone_day_hours = float(no_phone_seconds or 0) / 3600.0
                extra_metrics = self._coerce_daily_extra_metrics(row[11])
                imported_talk_time = extra_metrics.get("talk_time")
                talk_time_value = (
                    float(imported_talk_time)
                    if imported_talk_time is not None
                    else (float(row[3]) if row[3] is not None else 0.0)
                )
                no_phone_hours += no_phone_day_hours
                daily_map[day_key] = {
                    "work_time": float(row[1]) if row[1] is not None else 0.0,
                    "break_time": float(row[2]) if row[2] is not None else 0.0,
                    "talk_time": talk_time_value,
                    "calls": int(row[4]) if row[4] is not None else 0,
                    "dial_time": float(extra_metrics.get("dial_time") or 0.0),
                    "chats": int(extra_metrics.get("chats") or 0),
                    "extra_metrics": extra_metrics,
                    "efficiency": float(row[5]) if row[5] is not None else 0.0,
                    "fine_amount": float(row[6]) if row[6] is not None else 0.0,
                    "fine_reason": row[7],
                    "fine_comment": row[8],
                    "no_phone_minutes": no_phone_minutes,
                    "no_phone_seconds": no_phone_seconds,
                    "no_phone_hours": round(float(no_phone_day_hours), 4),
                    "fines": [],  # will populate from daily_fines table if present
                    "bonuses": []  # will populate from daily_bonuses table if present
                }

            # fetch fines for this operator in the date range
            cursor.execute(
                """
                SELECT df.amount, df.reason, df.comment, dh.day
                FROM daily_fines df
                JOIN daily_hours dh ON df.daily_hours_id = dh.id
                WHERE dh.operator_id = %s AND dh.day >= %s AND dh.day <= %s
                ORDER BY dh.day, df.id
                """,
                (operator_id, start, end)
            )
            fines_rows = cursor.fetchall()
            for amt, reason, comment, day_obj in fines_rows:
                day_key = str(int(day_obj.day))
                entry = daily_map.get(day_key)
                fine_obj = {
                    "amount": float(amt) if amt is not None else 0.0,
                    "reason": reason,
                    "comment": comment
                }
                # include minutes for Опоздание for client convenience
                try:
                    if str(reason) == 'Опоздание':
                        fine_obj['minutes'] = int(round(float(amt) / 50)) if amt else 0
                except Exception:
                    pass
                if entry is not None:
                    entry.setdefault('fines', []).append(fine_obj)

            cursor.execute(
                """
                SELECT bonus_type, quantity, training_hours, amount, friend_names, video_links, comment, day
                FROM daily_bonuses
                WHERE operator_id = %s AND day >= %s AND day <= %s
                ORDER BY day, id
                """,
                (operator_id, start, end)
            )
            bonuses_rows = cursor.fetchall()
            for bonus_type, quantity, training_hours, amount, friend_names, video_links, comment, day_obj in bonuses_rows:
                day_key = str(int(day_obj.day))
                entry = daily_map.get(day_key)
                if entry is None:
                    continue
                entry.setdefault('bonuses', []).append({
                    "type": bonus_type,
                    "bonus_type": bonus_type,
                    "quantity": int(quantity) if quantity is not None else 1,
                    "training_hours": float(training_hours) if training_hours is not None else None,
                    "amount": float(amount) if amount is not None else 0.0,
                    "friend_names": friend_names,
                    "video_links": video_links,
                    "comment": comment
                })

            technical_map_by_operator, technical_totals_by_operator = self._load_technical_issues_by_operator_day_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=start,
                end_date=end
            )
            offline_map_by_operator, offline_totals_by_operator = self._load_offline_activities_by_operator_day_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=start,
                end_date=end
            )
            training_totals_by_operator = self._load_training_hours_by_operator_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=start,
                end_date=end
            )
            chat_metrics_by_operator, chat_totals_by_operator = self._load_chat_manager_metrics_by_operator_day_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=start,
                end_date=end
            )

            # 2) Получаем имя/ставку + данные work_hours одним LEFT JOIN запросом (включая fines)
            cursor.execute(
                """
                SELECT u.name,
                    COALESCE(
                        (SELECT wr.rate FROM work_hours wr
                         WHERE wr.operator_id = u.id AND wr.rate IS NOT NULL AND wr.month <= %s
                         ORDER BY wr.month DESC LIMIT 1),
                        u.rate
                    ) AS rate,
                    u.hire_date,
                    d.name as direction_name,
                    d.calculation_model_code,
                    u.department_id,
                    COALESCE(w.norm_hours, 0) AS norm_hours,
                    COALESCE(w.regular_hours, 0) AS regular_hours,
                    COALESCE(w.total_break_time, 0) AS total_break_time,
                    COALESCE(w.total_talk_time, 0) AS total_talk_time,
                    COALESCE(w.total_calls, 0) AS total_calls,
                    COALESCE(w.total_efficiency_hours, 0) AS total_efficiency_hours,
                    COALESCE(w.calls_per_hour, 0) AS calls_per_hour,
                    COALESCE(w.fines, 0) AS fines
                FROM users u
                LEFT JOIN work_hours w
                ON w.operator_id = u.id AND w.month = %s
                LEFT JOIN directions d ON u.direction_id = d.id
                WHERE u.id = %s
                LIMIT 1
                """,
                (month, month, operator_id),
            )
            row = cursor.fetchone()

        technical_issues_by_day = technical_map_by_operator.get(int(operator_id), {}) if isinstance(technical_map_by_operator, dict) else {}
        technical_issue_hours = float(technical_totals_by_operator.get(int(operator_id), 0.0)) if isinstance(technical_totals_by_operator, dict) else 0.0
        offline_activities_by_day = offline_map_by_operator.get(int(operator_id), {}) if isinstance(offline_map_by_operator, dict) else {}
        offline_activity_hours = float(offline_totals_by_operator.get(int(operator_id), 0.0)) if isinstance(offline_totals_by_operator, dict) else 0.0
        training_hours = float(training_totals_by_operator.get(int(operator_id), 0.0)) if isinstance(training_totals_by_operator, dict) else 0.0
        chat_metrics_by_day = chat_metrics_by_operator.get(int(operator_id), {}) if isinstance(chat_metrics_by_operator, dict) else {}
        chat_metric_totals = chat_totals_by_operator.get(int(operator_id), {}) if isinstance(chat_totals_by_operator, dict) else {}

        # default values if user / work_hours not found
        if row:
            name, rate, hire_date, direction_name, calculation_model_raw, department_id, norm_hours, regular_hours, total_break_time, total_talk_time, \
                total_calls, total_efficiency_hours, calls_per_hour, fines = row
            rate = float(rate) if rate is not None else 0.0
        else:
            name = None
            rate = 0.0
            hire_date = None
            direction_name = None
            calculation_model_raw = None
            department_id = None
            norm_hours = regular_hours = total_break_time = total_talk_time = 0.0
            total_calls = 0
            total_efficiency_hours = calls_per_hour = fines = 0.0
        calculation_model_code = self._normalize_calculation_model_code(calculation_model_raw, direction_name)

        # Сегменты членства оператора за месяц — для уведомления «несколько направлений»
        # и селектора в «Мои часы». daily здесь НЕ разбиты по группам; сегменты дают
        # информативную раскладку по дням/направлениям (членства непересекающиеся).
        group_segments = []
        with self._get_cursor() as _seg_cursor:
            _seg_cursor.execute(
                """
                SELECT gom.group_id, gr.name, gr.direction_id, d.name,
                       """ + self._GROUP_MEMBER_MODEL_SQL + """, gom.start_date, gom.end_date
                FROM group_operator_memberships gom
                JOIN groups gr ON gr.id = gom.group_id
                LEFT JOIN directions d ON d.id = gr.direction_id
                WHERE gom.operator_id = %s
                  AND gom.start_date <= %s
                  AND (gom.end_date IS NULL OR gom.end_date >= %s)
                ORDER BY gom.start_date
                """,
                (operator_id, end, start),
            )
            for g_id, g_name, dir_id, dir_name, model_code, seg_start, seg_end in _seg_cursor.fetchall() or []:
                clipped_start = seg_start if (seg_start and seg_start > start) else start
                clipped_end = seg_end if (seg_end and seg_end < end) else end
                group_segments.append({
                    "group_id": int(g_id),
                    "group_name": g_name,
                    "direction_id": dir_id,
                    "direction_name": dir_name,
                    "calculation_model_code": normalize_calculation_model_code(model_code, dir_name),
                    "start_day": int(clipped_start.day),
                    "end_day": int(clipped_end.day),
                    "is_current": seg_end is None,
                })
        segment_model_codes = {
            str(seg.get("calculation_model_code") or "").strip()
            for seg in group_segments
            if seg.get("calculation_model_code")
        }
        effective_calculation_model_code = (
            next(iter(segment_model_codes))
            if len(segment_model_codes) == 1
            else calculation_model_code
        )

        if effective_calculation_model_code == CALCULATION_MODEL_CHAT_MANAGER:
            _chat_chats = int(chat_metric_totals.get("chats_count") or 0)
            total_calls = _chat_chats
            regular_hours_for_rate = max(0.0, float(regular_hours or 0.0))
            calls_per_hour = (float(total_calls) / regular_hours_for_rate) if regular_hours_for_rate > 0 else 0.0
            for day_key, metrics in (chat_metrics_by_day or {}).items():
                entry = daily_map.setdefault(str(day_key), {
                    "work_time": 0.0,
                    "break_time": 0.0,
                    "talk_time": 0.0,
                    "calls": 0,
                    "efficiency": 0.0,
                    "fine_amount": 0.0,
                    "fine_reason": None,
                    "fine_comment": None,
                    "fines": [],
                    "bonuses": []
                })
                entry["chat_metrics"] = metrics
                entry["calls"] = int(metrics.get("chats_count") or 0)

        total_dial_time = sum(
            float(day_data.get("dial_time") or 0.0)
            for day_data in daily_map.values()
            if isinstance(day_data, dict)
        )
        total_tez_chats = sum(
            int(day_data.get("chats") or 0)
            for day_data in daily_map.values()
            if isinstance(day_data, dict)
        )
        # Успешки ОП и общий план отдела — только для модели ОП TEZ: у остальных
        # моделей этой метрики нет, лишние запросы делать незачем.
        tez_successes_by_day = {}
        total_tez_successes = 0
        tez_plan_per_fte = None
        tez_norm_hours_fte = None
        if effective_calculation_model_code == CALCULATION_MODEL_TEZ_OP:
            total_calls = sum(
                int(day_data.get("calls") or 0)
                for day_data in daily_map.values()
                if isinstance(day_data, dict)
            )
            total_talk_time = sum(
                float(day_data.get("talk_time") or 0.0)
                for day_data in daily_map.values()
                if isinstance(day_data, dict)
            )
            regular_hours_for_rate = max(0.0, float(regular_hours or 0.0))
            calls_per_hour = (
                float(total_calls) / regular_hours_for_rate
                if regular_hours_for_rate > 0
                else 0.0
            )
            # Успешка привязана к дню первой поездки, поэтому месяц успешки может
            # отличаться от месяца лида — берём ровно тот месяц, что показываем.
            for row_success in self.get_tez_successes_operator_day(year, mon, operator_id=operator_id):
                day_key = str(int(row_success['day']))
                count = int(row_success['successes'] or 0)
                tez_successes_by_day[day_key] = count
                total_tez_successes += count
                entry = daily_map.get(day_key)
                if entry is not None:
                    entry['tez_successes'] = count
            if department_id is not None:
                plan_row = self.get_department_monthly_plan(int(department_id), year, mon)
                if plan_row and plan_row.get('plan_per_fte') is not None:
                    tez_plan_per_fte = float(plan_row['plan_per_fte'])
                    tez_norm_hours_fte = plan_row['norm_hours_fte']

        accounted_hours = (
            float(regular_hours or 0.0)
            + float(training_hours or 0.0)
            + float(technical_issue_hours or 0.0)
            + float(offline_activity_hours or 0.0)
        )

        operator_obj = {
            "operator_id": operator_id,
            "name": name,
            "direction": direction_name,
            "calculation_model_code": effective_calculation_model_code,
            "calculationModelCode": effective_calculation_model_code,
            "rate": rate,
            "hire_date": hire_date.isoformat() if hasattr(hire_date, "isoformat") else hire_date,
            "norm_hours": float(norm_hours),
            "fines": float(fines),
            "training_hours": round(float(training_hours), 2),
            "no_phone_hours": round(float(no_phone_hours), 2),
            "accounted_hours": round(float(accounted_hours), 2),
            "worked_hours_used": round(float(accounted_hours), 2),
            "daily": daily_map,
            "group_segments": group_segments,
            "chat_metrics_by_day": chat_metrics_by_day,
            "chat_metrics": chat_metric_totals,
            "technical_issues_by_day": technical_issues_by_day,
            "technical_issue_hours": round(float(technical_issue_hours), 2),
            "offline_activities_by_day": offline_activities_by_day,
            "offline_activity_hours": round(float(offline_activity_hours), 2),
            "tez_successes_by_day": tez_successes_by_day,
            "tez_plan_per_fte": tez_plan_per_fte,
            "tez_norm_hours_fte": tez_norm_hours_fte,
            "aggregates": {
                "regular_hours": float(regular_hours),
                "total_break_time": float(total_break_time),
                "total_talk_time": float(total_talk_time),
                "total_calls": int(total_calls),
                "total_dial_time": float(total_dial_time),
                "total_chats": int(total_tez_chats),
                "total_tez_successes": int(total_tez_successes),
                "total_efficiency_hours": float(total_efficiency_hours),
                "calls_per_hour": float(calls_per_hour),
                "chat_avg_score": chat_metric_totals.get("avg_score"),
                "chat_avg_response_time_seconds": chat_metric_totals.get("avg_response_time_seconds"),
                "chat_transfer_count": int(chat_metric_totals.get("transfer_chat_count") or 0),
            },
        }

        return {"month": month, "days_in_month": days_in_month, "operator": operator_obj}

    def get_daily_hours_for_all_month(self, month, department_id=None, people='operators', user_ids=None):
        """
        Возвращает все daily_hours и агрегаты work_hours для всех операторов за месяц YYYY-MM.
        Аналогично get_daily_hours_by_supervisor_month, но без фильтра по супервайзеру.

        people='supervisors' (задача #352) — вместо операторов СВ отделов с часами по
        Clockster, в той же форме строк: экран показывает их той же таблицей.
        В режиме операторов такие СВ, наоборот, не выводятся — у главы ОП они
        попадали сюда через строки daily_hours с нулём и выглядели операторами.
        Исключаются только те, у кого в месяце нет операторского членства в
        группе: бывший оператор, ставший СВ, остаётся в месяцах, когда работал.
        user_ids — сузить выборку до этих людей (СВ видит только себя).
        """
        import calendar as _py_calendar
        from datetime import date as _date

        try:
            year, mon = map(int, month.split('-'))
            days = _py_calendar.monthrange(year, mon)[1]
            start = _date(year, mon, 1)
            end = _date(year, mon, days)
        except Exception as e:
            raise ValueError("Invalid month format, expected YYYY-MM") from e

        supervisors_mode = str(people or 'operators').strip().lower() == 'supervisors'
        scope_ids = None
        if user_ids is not None:
            scope_ids = sorted({int(v) for v in user_ids if v is not None})
        clockster_codes = list(SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES)
        if supervisors_mode:
            # Уволенный СВ остаётся в месяце, где у него есть отработанные дни.
            people_where = (
                "(" + self.SUPERVISOR_ROLES_SQL + " AND " + self.SUPERVISOR_CLOCKSTER_DEPARTMENT_SQL + """
                    AND (lower(COALESCE(u.status, '')) <> 'fired'
                         OR EXISTS (SELECT 1 FROM daily_hours dh WHERE dh.operator_id = u.id
                                      AND dh.day >= %s AND dh.day <= %s)))"""
            )
            people_params = (clockster_codes, start, end)
        else:
            # Историческая видимость: берём не только текущих операторов, но и всех,
            # кто был оператором В ЭТОМ МЕСЯЦЕ (есть daily_hours или членство в группе),
            # даже если сейчас стал СВ/уволен. Иначе прошлый отчёт «теряет» людей.
            people_where = """(
                    u.role = 'operator'
                    OR EXISTS (SELECT 1 FROM daily_hours dh WHERE dh.operator_id = u.id AND dh.day >= %s AND dh.day <= %s)
                    OR EXISTS (SELECT 1 FROM group_operator_memberships gom
                               WHERE gom.operator_id = u.id AND gom.start_date <= %s
                                 AND (gom.end_date IS NULL OR gom.end_date >= %s))
                )
                AND NOT (
                    """ + self.SUPERVISOR_ROLES_SQL + " AND " + self.SUPERVISOR_CLOCKSTER_DEPARTMENT_SQL + """
                    AND NOT EXISTS (SELECT 1 FROM group_operator_memberships gom2
                                    WHERE gom2.operator_id = u.id AND gom2.start_date <= %s
                                      AND (gom2.end_date IS NULL OR gom2.end_date >= %s))
                )"""
            people_params = (start, end, end, start, clockster_codes, end, start)

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name,
                    COALESCE(
                        (SELECT wr.rate FROM work_hours wr
                         WHERE wr.operator_id = u.id AND wr.rate IS NOT NULL AND wr.month <= %s
                         ORDER BY wr.month DESC LIMIT 1),
                        u.rate
                    ) AS rate,
                    u.status, u.supervisor_id, u.hire_date,
                    d.name as direction_name,
                    d.calculation_model_code,
                    COALESCE(w.norm_hours, 0) as norm_hours,
                    COALESCE(w.regular_hours, 0) as regular_hours,
                    COALESCE(w.total_break_time, 0) as total_break_time,
                    COALESCE(w.total_talk_time, 0) as total_talk_time,
                    COALESCE(w.total_calls, 0) as total_calls,
                    COALESCE(w.total_efficiency_hours, 0) as total_efficiency_hours,
                    COALESCE(w.calls_per_hour, 0) as calls_per_hour,
                    COALESCE(w.fines, 0) as fines,
                    u.department_id,
                    u.role,
                    u.job_title
                FROM users u
                LEFT JOIN work_hours w
                ON w.operator_id = u.id AND w.month = %s
                LEFT JOIN directions d ON u.direction_id = d.id
                WHERE """ + people_where + """
                AND (%s::int IS NULL OR u.department_id = %s::int)
                AND (%s::int[] IS NULL OR u.id = ANY(%s::int[]))
                ORDER BY u.name
            """, (month, month, *people_params, department_id, department_id, scope_ids, scope_ids))
            operator_rows = cursor.fetchall()

            if not operator_rows:
                return {"month": month, "days_in_month": days, "operators": []}

            op_ids = [row[0] for row in operator_rows]

            # Дата увольнения — фронт по ней решает вкладку «Активные»/«Уволенные».
            dismissal_dates = self._load_dismissal_dates_tx(cursor, op_ids)

            cursor.execute("""
                SELECT d.operator_id, d.day, d.work_time, d.break_time, d.talk_time, d.calls, d.efficiency,
                    d.fine_amount, d.fine_reason, d.fine_comment,
                    COALESCE(d.no_phone_minutes, 0), COALESCE(d.no_phone_seconds, 0),
                    COALESCE(d.extra_metrics, '{}'::jsonb)
                FROM daily_hours d
                WHERE d.operator_id = ANY(%s)
                AND d.day >= %s AND d.day <= %s
                ORDER BY d.operator_id, d.day
            """, (op_ids, start, end))
            daily_rows = cursor.fetchall()

            daily_map = {}
            no_phone_totals_by_operator = {}
            for (op_id, day, work_time, break_time, talk_time, calls, eff,
                fine_amount, fine_reason, fine_comment, no_phone_minutes, no_phone_seconds,
                extra_metrics_raw) in daily_rows:
                day_num = str(int(day.day))
                no_phone_minutes = int(no_phone_minutes or 0)
                no_phone_seconds = int(no_phone_seconds or 0)
                no_phone_day_hours = float(no_phone_seconds or 0) / 3600.0
                extra_metrics = self._coerce_daily_extra_metrics(extra_metrics_raw)
                imported_talk_time = extra_metrics.get("talk_time")
                talk_time_value = (
                    float(imported_talk_time)
                    if imported_talk_time is not None
                    else (float(talk_time) if talk_time is not None else 0.0)
                )
                no_phone_totals_by_operator[op_id] = no_phone_totals_by_operator.get(op_id, 0.0) + no_phone_day_hours
                d = {
                    "work_time": float(work_time) if work_time is not None else 0.0,
                    "break_time": float(break_time) if break_time is not None else 0.0,
                    "talk_time": talk_time_value,
                    "calls": int(calls) if calls is not None else 0,
                    "dial_time": float(extra_metrics.get("dial_time") or 0.0),
                    "chats": int(extra_metrics.get("chats") or 0),
                    "extra_metrics": extra_metrics,
                    "efficiency": float(eff) if eff is not None else 0.0,
                    "fine_amount": float(fine_amount) if fine_amount is not None else 0.0,
                    "fine_reason": fine_reason,
                    "fine_comment": fine_comment,
                    "no_phone_minutes": no_phone_minutes,
                    "no_phone_seconds": no_phone_seconds,
                    "no_phone_hours": round(float(no_phone_day_hours), 4),
                    "fines": [],
                    "bonuses": []
                }
                daily_map.setdefault(op_id, {})[day_num] = d

            cursor.execute("""
                SELECT df.operator_id, df.amount, df.reason, df.comment, df.day
                FROM daily_fines df
                WHERE df.operator_id = ANY(%s)
                AND df.day >= %s AND df.day <= %s
                ORDER BY df.operator_id, df.day, df.id
            """, (op_ids, start, end))
            fines_rows = cursor.fetchall()
            for op_id, amt, reason, comment, day_obj in fines_rows:
                day_num = str(int(day_obj.day))
                fine_obj = {"amount": float(amt) if amt is not None else 0.0, "reason": reason, "comment": comment}
                try:
                    if str(reason) == 'Опоздание':
                        fine_obj['minutes'] = int(round(float(amt) / 50)) if amt else 0
                except Exception:
                    pass
                if op_id in daily_map and day_num in daily_map[op_id]:
                    daily_map[op_id][day_num].setdefault('fines', []).append(fine_obj)

            cursor.execute("""
                SELECT db.operator_id, db.bonus_type, db.quantity, db.training_hours, db.amount, db.friend_names, db.video_links, db.comment, db.day
                FROM daily_bonuses db
                WHERE db.operator_id = ANY(%s)
                AND db.day >= %s AND db.day <= %s
                ORDER BY db.operator_id, db.day, db.id
            """, (op_ids, start, end))
            bonuses_rows = cursor.fetchall()
            for op_id, bonus_type, quantity, training_hours, amount, friend_names, video_links, comment, day_obj in bonuses_rows:
                day_num = str(int(day_obj.day))
                bonus_obj = {
                    "type": bonus_type,
                    "bonus_type": bonus_type,
                    "quantity": int(quantity) if quantity is not None else 1,
                    "training_hours": float(training_hours) if training_hours is not None else None,
                    "amount": float(amount) if amount is not None else 0.0,
                    "friend_names": friend_names,
                    "video_links": video_links,
                    "comment": comment
                }
                if op_id in daily_map and day_num in daily_map[op_id]:
                    daily_map[op_id][day_num].setdefault('bonuses', []).append(bonus_obj)

            technical_map_by_operator, technical_totals_by_operator = self._load_technical_issues_by_operator_day_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start,
                end_date=end
            )
            offline_map_by_operator, offline_totals_by_operator = self._load_offline_activities_by_operator_day_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start,
                end_date=end
            )
            training_totals_by_operator = self._load_training_hours_by_operator_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start,
                end_date=end
            )
            chat_metrics_by_operator, chat_totals_by_operator = self._load_chat_manager_metrics_by_operator_day_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start,
                end_date=end
            )

            # У СВ своего направления нет — берём направления групп, которые он ведёт.
            supervisor_directions = (
                self._supervisor_directions_tx(cursor, op_ids, start, end) if supervisors_mode else {}
            )

            operators = []
            for row in operator_rows:
                (op_id, op_name, rate, status, sup_id, hire_date, direction_name, calculation_model_raw, norm_hours,
                regular_hours, total_break_time, total_talk_time,
                total_calls, total_efficiency_hours, calls_per_hour, fines, operator_department_id,
                user_role, job_title) = row
                if supervisors_mode:
                    direction_name = supervisor_directions.get(op_id) or direction_name
                calculation_model_code = self._normalize_calculation_model_code(calculation_model_raw, direction_name)
                op_daily = daily_map.get(op_id, {})
                daily_dial_total = sum(
                    float(_dd.get("dial_time") or 0.0)
                    for _dd in op_daily.values()
                    if isinstance(_dd, dict)
                )
                daily_talk_total = sum(
                    float(_dd.get("talk_time") or 0.0)
                    for _dd in op_daily.values()
                    if isinstance(_dd, dict)
                )
                daily_calls_total = sum(
                    int(_dd.get("calls") or 0)
                    for _dd in op_daily.values()
                    if isinstance(_dd, dict)
                )
                daily_chats_total = sum(
                    int(_dd.get("chats") or 0)
                    for _dd in op_daily.values()
                    if isinstance(_dd, dict)
                )
                training_hours = float(training_totals_by_operator.get(op_id, 0.0)) if isinstance(training_totals_by_operator, dict) else 0.0
                technical_issue_hours = float(technical_totals_by_operator.get(op_id, 0.0)) if isinstance(technical_totals_by_operator, dict) else 0.0
                offline_activity_hours = float(offline_totals_by_operator.get(op_id, 0.0)) if isinstance(offline_totals_by_operator, dict) else 0.0
                no_phone_hours = float(no_phone_totals_by_operator.get(op_id, 0.0))
                accounted_hours = float(regular_hours or 0.0) + training_hours + technical_issue_hours + offline_activity_hours
                if supervisors_mode:
                    # Часы СВ — только Clockster минус перерыв: собрание или тренинг СВ
                    # проходит в офисе и уже есть в отметках, второй раз не прибавляем.
                    accounted_hours = float(regular_hours or 0.0)
                chat_metrics_by_day = chat_metrics_by_operator.get(op_id, {}) if isinstance(chat_metrics_by_operator, dict) else {}
                chat_metric_totals = chat_totals_by_operator.get(op_id, {}) if isinstance(chat_totals_by_operator, dict) else {}
                if calculation_model_code == CALCULATION_MODEL_CHAT_MANAGER:
                    _chat_chats = int(chat_metric_totals.get("chats_count") or 0)
                    total_calls = _chat_chats
                    regular_hours_for_rate = max(0.0, float(regular_hours or 0.0))
                    calls_per_hour = (float(total_calls) / regular_hours_for_rate) if regular_hours_for_rate > 0 else 0.0
                    for day_key, metrics in (chat_metrics_by_day or {}).items():
                        entry = op_daily.setdefault(str(day_key), {
                            "work_time": 0.0,
                            "break_time": 0.0,
                            "talk_time": 0.0,
                            "calls": 0,
                            "efficiency": 0.0,
                            "fine_amount": 0.0,
                            "fine_reason": None,
                            "fine_comment": None,
                            "fines": [],
                            "bonuses": []
                        })
                        entry["chat_metrics"] = metrics
                        entry["calls"] = int(metrics.get("chats_count") or 0)
                elif calculation_model_code == CALCULATION_MODEL_TEZ_OP:
                    total_calls = int(daily_calls_total)
                    total_talk_time = float(daily_talk_total)
                    regular_hours_for_rate = max(0.0, float(regular_hours or 0.0))
                    calls_per_hour = (
                        float(total_calls) / regular_hours_for_rate
                        if regular_hours_for_rate > 0
                        else 0.0
                    )

                operators.append({
                    "operator_id": op_id,
                    "name": op_name,
                    "direction": direction_name,
                    "department_id": operator_department_id,
                    "calculation_model_code": calculation_model_code,
                    "calculationModelCode": calculation_model_code,
                    "supervisor_id": sup_id,
                    "role": user_role,
                    "employee_kind": "supervisor" if supervisors_mode else "operator",
                    "job_title": job_title or ("Супервайзер" if supervisors_mode else None),
                    "hours_source": "clockster" if supervisors_mode else None,
                    "rate": float(rate) if rate is not None else 0.0,
                    "status": status,
                    "dismissal_date": self._dismissal_date_iso(dismissal_dates, op_id),
                    "hire_date": hire_date.isoformat() if hasattr(hire_date, "isoformat") else hire_date,
                    "norm_hours": float(norm_hours) if norm_hours is not None else 0.0,
                    "training_hours": round(float(training_hours), 2),
                    "no_phone_hours": round(float(no_phone_hours), 2),
                    "accounted_hours": round(float(accounted_hours), 2),
                    "worked_hours_used": round(float(accounted_hours), 2),
                    "daily": op_daily,
                    "chat_metrics_by_day": chat_metrics_by_day,
                    "chat_metrics": chat_metric_totals,
                    "technical_issues_by_day": technical_map_by_operator.get(op_id, {}) if isinstance(technical_map_by_operator, dict) else {},
                    "technical_issue_hours": round(float(technical_issue_hours), 2),
                    "offline_activities_by_day": offline_map_by_operator.get(op_id, {}) if isinstance(offline_map_by_operator, dict) else {},
                    "offline_activity_hours": round(float(offline_activity_hours), 2),
                    "aggregates": {
                        "regular_hours": float(regular_hours),
                        "total_break_time": float(total_break_time),
                        "total_talk_time": float(total_talk_time),
                        "total_calls": int(total_calls),
                        "total_dial_time": float(daily_dial_total),
                        "total_chats": int(daily_chats_total),
                        "total_efficiency_hours": float(total_efficiency_hours),
                        "calls_per_hour": float(calls_per_hour),
                        "fines": float(fines),
                        "chat_avg_score": chat_metric_totals.get("avg_score"),
                        "chat_avg_response_time_seconds": chat_metric_totals.get("avg_response_time_seconds"),
                        "chat_transfer_count": int(chat_metric_totals.get("transfer_chat_count") or 0),
                    }
                })

        return {"month": month, "days_in_month": days, "operators": operators}

    def aggregate_month_from_daily(self, operator_id, month):
        """
        Публичный wrapper месячной агрегации daily_hours -> work_hours.
        """
        with self._get_cursor() as cursor:
            return self._aggregate_month_from_daily_tx(
                cursor=cursor,
                operator_id=int(operator_id),
                month=str(month)
            )

    def migrate_daily_fines_from_daily_hours(self):
        """
        Migration helper: convert existing aggregated fine_amount/fine_reason/fine_comment
        from daily_hours into individual rows in daily_fines when no daily_fines exist for that day.
        This is idempotent: it will skip days that already have entries in daily_fines.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, operator_id, day, fine_amount, fine_reason, fine_comment
                FROM daily_hours
                WHERE COALESCE(fine_amount, 0) > 0
            """)
            rows = cursor.fetchall()
            for dh_id, op_id, day_obj, fine_amount, fine_reason, fine_comment in rows:
                # skip if fines already exist for this daily_hours
                cursor.execute("SELECT 1 FROM daily_fines WHERE daily_hours_id = %s LIMIT 1", (dh_id,))
                if cursor.fetchone():
                    continue
                # insert single aggregated fine as one row
                cursor.execute(
                    "INSERT INTO daily_fines (daily_hours_id, operator_id, day, amount, reason, comment) VALUES (%s, %s, %s, %s, %s, %s)",
                    (dh_id, op_id, day_obj, float(fine_amount or 0.0), fine_reason, fine_comment)
                )


    def _get_month_end_date(self, month: Optional[str]) -> date:
        month_str = str(month or '').strip()
        try:
            year_str, month_part = month_str.split('-', 1)
            year_value = int(year_str)
            month_value = int(month_part)
            last_day = calendar.monthrange(year_value, month_value)[1]
            return date(year_value, month_value, last_day)
        except Exception:
            today = datetime.now().date()
            last_day = calendar.monthrange(today.year, today.month)[1]
            return date(today.year, today.month, last_day)

    def _get_month_date_range(self, month: Optional[str]) -> Tuple[date, date]:
        month_end_date = self._get_month_end_date(month)
        month_start_date = date(month_end_date.year, month_end_date.month, 1)
        if month_end_date.month == 12:
            next_month_start = date(month_end_date.year + 1, 1, 1)
        else:
            next_month_start = date(month_end_date.year, month_end_date.month + 1, 1)
        return month_start_date, next_month_start

    def _get_month_work_days(self, month: Optional[str]) -> int:
        # Рабочие дни месяца по формуле владельца: округл(дни месяца / 7 * 5),
        # НЕ календарные пн-пт (31 д -> 22, 30 д -> 21, 28 д -> 20; июль = 176 ч).
        # Используется в авто-норме часов (auto_fill_norm_hours) и норме на полную
        # ставку (_get_full_rate_norm_hours -> норматив звонков для оценки).
        month_start_date, next_month_start = self._get_month_date_range(month)
        days_in_month = (next_month_start - month_start_date).days
        return int(round(days_in_month / 7.0 * 5.0))

    def _tenure_excluded_days_tx(self, cursor, operator_ids, as_of):
        """{operator_id: дни} периодов графика «не в стаж», прошедшие к дате as_of.

        Задача #358, п. 8: длительный Б/С по решению главы не идёт в стаж.
        Период, не начавшийся к as_of, не считается; идущий — прошедшей частью.
        На эти дни сдвигается начало стажа (_shift_hire_date_by_excluded_days).
        """
        ids = [int(v) for v in (operator_ids or []) if v is not None]
        if not ids or as_of is None:
            return {}
        cursor.execute(
            """
            SELECT p.operator_id,
                   SUM(LEAST(COALESCE(p.end_date, %(as_of)s), %(as_of)s) - p.start_date + 1)
              FROM operator_schedule_status_periods p
             WHERE p.operator_id = ANY(%(ids)s)
               AND p.exclude_from_tenure
               AND p.start_date <= %(as_of)s
             GROUP BY p.operator_id
            """,
            {'ids': ids, 'as_of': as_of},
        )
        return {int(row[0]): max(0, int(row[1] or 0)) for row in cursor.fetchall()}

    def get_tenure_excluded_days(self, user_id, as_of=None):
        """Дни «не в стаж» одного сотрудника на дату (по умолчанию — сегодня по Алматы)."""
        as_of = as_of or self._almaty_now().date()
        with self._get_cursor() as cursor:
            return self._tenure_excluded_days_tx(cursor, [user_id], as_of).get(int(user_id), 0)

    @staticmethod
    def _shift_hire_date_by_excluded_days(hire_date_value, excluded_days):
        """Дата, с которой считается стаж: найм + дни «не в стаж». Строку и
        пустое значение не трогаем — их разбирает _get_tenure_months_for_reference."""
        if not excluded_days or not isinstance(hire_date_value, date):
            return hire_date_value
        if isinstance(hire_date_value, datetime):
            hire_date_value = hire_date_value.date()
        return hire_date_value + timedelta(days=int(excluded_days))

    def _get_tenure_months_for_reference(self, hire_date_value, reference_date: Optional[date]) -> Optional[int]:
        if not hire_date_value or not reference_date:
            return None

        if isinstance(hire_date_value, datetime):
            hire_date_obj = hire_date_value.date()
        elif isinstance(hire_date_value, date):
            hire_date_obj = hire_date_value
        elif isinstance(hire_date_value, str):
            hire_date_obj = None
            for date_format in ('%Y-%m-%d', '%d-%m-%Y'):
                try:
                    hire_date_obj = datetime.strptime(hire_date_value, date_format).date()
                    break
                except ValueError:
                    continue
            if hire_date_obj is None:
                return None
        else:
            return None

        months = (reference_date.year - hire_date_obj.year) * 12 + (reference_date.month - hire_date_obj.month)
        if reference_date.day < hire_date_obj.day:
            months -= 1
        return max(int(months), 0)
    
    def get_hours_summary(self, operator_id=None, month=None):
        query = """
            SELECT 
                u.id AS operator_id,
                u.name AS operator_name,
                u.direction_id,
                d.name AS direction_name,
                d.calculation_model_code,

                COALESCE(wh.regular_hours, 0) AS regular_hours,
                COALESCE(wh.fines, 0) AS fines,
                COALESCE(wh.norm_hours, 0) AS norm_hours,
                COALESCE(wh.total_calls, 0) AS total_calls,

                -- training hours считаем из trainings
                COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN t.end_time <= t.start_time
                                THEN EXTRACT(EPOCH FROM (t.end_time + INTERVAL '24 hours' - t.start_time))
                            ELSE EXTRACT(EPOCH FROM (t.end_time - t.start_time))
                        END
                    ) / 3600.0
                    FROM trainings t
                    WHERE t.operator_id = u.id
                    AND t.count_in_hours = TRUE
                    AND (%s IS NULL OR TO_CHAR(t.training_date, 'YYYY-MM') = %s)
                ), 0) AS training_hours,

                -- technical issue hours считаем отдельно и учитываем в часах выполнения
                COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN ti.end_time <= ti.start_time
                                THEN EXTRACT(EPOCH FROM (ti.end_time + INTERVAL '24 hours' - ti.start_time))
                            ELSE EXTRACT(EPOCH FROM (ti.end_time - ti.start_time))
                        END
                    ) / 3600.0
                    FROM operator_technical_issues ti
                    WHERE ti.operator_id = u.id
                    AND (%s IS NULL OR TO_CHAR(ti.issue_date, 'YYYY-MM') = %s)
                ), 0) AS technical_issue_hours,

                COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN oa.end_time <= oa.start_time
                                THEN EXTRACT(EPOCH FROM (oa.end_time + INTERVAL '24 hours' - oa.start_time))
                            ELSE EXTRACT(EPOCH FROM (oa.end_time - oa.start_time))
                        END
                    ) / 3600.0
                    FROM operator_offline_activities oa
                    WHERE oa.operator_id = u.id
                    AND (%s IS NULL OR TO_CHAR(oa.activity_date, 'YYYY-MM') = %s)
                ), 0)
                + COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN ws.end_time <= ws.start_time
                                THEN EXTRACT(EPOCH FROM (ws.end_time + INTERVAL '24 hours' - ws.start_time))
                            ELSE EXTRACT(EPOCH FROM (ws.end_time - ws.start_time))
                        END
                    ) / 3600.0
                    FROM work_shifts ws
                    WHERE ws.operator_id = u.id
                    AND COALESCE(ws.shift_type, 'regular') = 'office_practice'
                    AND (%s IS NULL OR TO_CHAR(ws.shift_date, 'YYYY-MM') = %s)
                ), 0) AS offline_activity_hours,

                COALESCE(cmm.chats_count, 0) AS chat_chats_count,
                cmm.avg_score AS chat_avg_score,
                cmm.avg_response_time_seconds AS chat_avg_response_time_seconds,
                COALESCE(cmm.transfer_chat_count, 0) AS chat_transfer_chat_count

            FROM users u
            LEFT JOIN directions d
                ON u.direction_id = d.id
            LEFT JOIN (
                SELECT
                    operator_id,
                    COALESCE(SUM(chats_count), 0) AS chats_count,
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
                    ) AS avg_score,
                    AVG(avg_response_time_seconds) FILTER (WHERE avg_response_time_seconds IS NOT NULL) AS avg_response_time_seconds,
                    COALESCE(SUM(transfer_chat_count), 0) AS transfer_chat_count
                FROM chat_manager_daily_metrics
                WHERE (%s IS NULL OR TO_CHAR(day, 'YYYY-MM') = %s)
                GROUP BY operator_id
            ) cmm ON cmm.operator_id = u.id
            LEFT JOIN work_hours wh
                ON u.id = wh.operator_id
            AND (%s IS NULL OR wh.month = %s)
            WHERE u.role = 'operator'
        """

        params = [month, month, month, month, month, month, month, month, month, month, month, month]

        if operator_id:
            query += " AND u.id = %s"
            params.append(operator_id)

        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            phone_training_totals = {}
            manual_phone_training_counted_totals = {}
            if month and rows:
                try:
                    month_start_date, next_month_start = self._get_month_date_range(month)
                    operator_ids_for_phone_training = [
                        int(row[0]) for row in rows if row and row[0] is not None
                    ]
                    manual_phone_training_map, _manual_phone_training_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
                        cursor=cursor,
                        operator_ids=operator_ids_for_phone_training,
                        start_date=month_start_date,
                        end_date=next_month_start - timedelta(days=1)
                    )
                    _manual_phone_training_counted_map, manual_phone_training_counted_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
                        cursor=cursor,
                        operator_ids=operator_ids_for_phone_training,
                        start_date=month_start_date,
                        end_date=next_month_start - timedelta(days=1),
                        counted_only=True
                    )
                    status_phone_training_map, _status_phone_training_totals = self._load_phone_shift_training_offline_intervals_by_operator_day_tx(
                        cursor=cursor,
                        operator_ids=operator_ids_for_phone_training,
                        start_date=month_start_date,
                        end_date=next_month_start - timedelta(days=1)
                    )
                    phone_training_totals = self._sum_phone_shift_offline_interval_maps(
                        operator_ids_for_phone_training,
                        manual_phone_training_map,
                        status_phone_training_map
                    )
                except Exception:
                    logging.exception("Failed to load phone-shift training offline totals for hours summary")
                    phone_training_totals = {}
                    manual_phone_training_counted_totals = {}

            result = []
            for row in rows:
                calculation_model_code = self._normalize_calculation_model_code(row[4], row[3])
                regular_hours = float(row[5])
                total_calls = int(row[8])
                training_hours = round(
                    max(
                        0.0,
                        float(row[9]) - float(manual_phone_training_counted_totals.get(int(row[0]), 0.0) or 0.0)
                    ),
                    2
                )
                technical_issue_hours = round(float(row[10]), 2)
                offline_activity_hours = round(
                    float(row[11]) + float(phone_training_totals.get(int(row[0]), 0.0) or 0.0),
                    2
                )
                chat_chats_count = int(row[12] or 0)
                chat_avg_score = float(row[13]) if row[13] is not None else None
                chat_avg_response_time_seconds = float(row[14]) if row[14] is not None else None
                chat_transfer_chat_count = int(row[15] or 0)
                if calculation_model_code == CALCULATION_MODEL_CHAT_MANAGER:
                    total_calls = chat_chats_count
                accounted_hours = round(regular_hours + training_hours + technical_issue_hours + offline_activity_hours, 2)

                effective_call_hours = max(0.0, float(regular_hours))
                calls_per_hour = (
                    round(total_calls / effective_call_hours, 2)
                    if effective_call_hours > 0 else 0.0
                )

                result.append({
                    "operator_id": row[0],
                    "operator_name": row[1],
                    "direction": row[2],
                    "direction_id": row[2],
                    "direction_name": row[3],
                    "calculation_model_code": calculation_model_code,
                    "calculationModelCode": calculation_model_code,
                    "regular_hours": regular_hours,
                    "training_hours": training_hours,
                    "technical_issue_hours": technical_issue_hours,
                    "offline_activity_hours": offline_activity_hours,
                    "accounted_hours": accounted_hours,
                    "fines": float(row[6]),
                    "norm_hours": float(row[7]),
                    "total_calls": int(total_calls),
                    "calls_per_hour": calls_per_hour,
                    "chat_avg_score": chat_avg_score,
                    "chat_avg_response_time_seconds": chat_avg_response_time_seconds,
                    "chat_transfer_count": chat_transfer_chat_count
                })

            return result

    def _assert_no_operator_hours_in_window_tx(self, cursor, operator_id, lo, hi):
        """Стоп-кран для сдвига границы членства.

        Вызывается только когда окно [lo, hi] до сдвига принадлежит группе и
        после сдвига будет принадлежать другой группе (или станет бесхозным).
        Если в окне уже есть непустые daily_hours, такой перенос блокируем.
        При опечатке в дате часы молча пропали бы из той группы, где их вели
        или перешли бы в другую. Осознанный перенос истории — это
        reassign_operator_history, а не побочный эффект правки даты, поэтому
        здесь просто запрещаем.
        """
        cursor.execute(
            """SELECT MIN(day), MAX(day), COUNT(*) FROM daily_hours
               WHERE operator_id = %s AND day >= %s AND day <= %s
                 AND (""" + self._DAILY_HOURS_NON_EMPTY + ")",
            (int(operator_id), lo, hi),
        )
        d_min, d_max, cnt = cursor.fetchone() or (None, None, 0)
        if not cnt:
            return
        span = (
            d_min.strftime('%d.%m.%Y') if d_min == d_max
            else "{} — {}".format(d_min.strftime('%d.%m.%Y'), d_max.strftime('%d.%m.%Y'))
        )
        raise ValueError(
            "Нельзя: за {} у оператора уже есть учтённые часы ({} дн.), и сейчас "
            "эти дни числятся за другой группой. Сдвиг даты перевесил бы их, "
            "поэтому правка заблокирована — проверьте дату.".format(span, int(cnt))
        )

    def _aggregate_month_from_daily_tx(self, cursor, operator_id, month):
        operator_id = int(operator_id)
        year, mon = map(int, str(month).split('-'))
        start = date(year, mon, 1)
        end = date(year, mon, calendar.monthrange(year, mon)[1])
        # Модель за месяц — по группе оператора на конец месяца (fallback — направление),
        # чтобы агрегат прошлого месяца не «съезжал» при смене направления/роли.
        calculation_model_code = self._get_operator_calculation_model_tx(cursor, operator_id, as_of=end)

        cursor.execute("""
            SELECT
                COALESCE(SUM(work_time),0),
                COALESCE(SUM(training_time),0),
                COALESCE(SUM(break_time),0),
                COALESCE(SUM(talk_time),0),
                COALESCE(SUM(calls),0),
                COALESCE(SUM(efficiency),0),
                COALESCE(SUM(no_phone_seconds),0),
                COALESCE(SUM(
                    CASE
                        WHEN jsonb_typeof(COALESCE(extra_metrics, '{}'::jsonb)->'talk_time') = 'number'
                            THEN (extra_metrics->>'talk_time')::double precision
                        ELSE 0
                    END
                ), 0),
                COALESCE(SUM(
                    CASE
                        WHEN jsonb_typeof(COALESCE(extra_metrics, '{}'::jsonb)->'dial_time') = 'number'
                            THEN (extra_metrics->>'dial_time')::double precision
                        ELSE 0
                    END
                ), 0),
                COALESCE(SUM(
                    CASE
                        WHEN jsonb_typeof(COALESCE(extra_metrics, '{}'::jsonb)->'chats') = 'number'
                            THEN (extra_metrics->>'chats')::integer
                        ELSE 0
                    END
                ), 0)
            FROM daily_hours
            WHERE operator_id = %s AND day >= %s AND day <= %s
        """, (operator_id, start, end))
        row = cursor.fetchone()
        (
            total_work_time,
            total_training_time,
            total_break_time,
            total_talk_time,
            total_calls,
            total_efficiency_hours,
            total_no_phone_seconds,
            tez_talk_time,
            tez_dial_time,
            tez_chats,
        ) = row

        if calculation_model_code == CALCULATION_MODEL_TEZ_OP:
            total_talk_time = float(tez_talk_time or 0.0)

        chat_avg_score = None
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
                  AND day >= %s
                  AND day <= %s
            """, (operator_id, start, end))
            chat_row = cursor.fetchone() or (0, None, None, 0)
            _chat_sum = int(chat_row[0] or 0)
            total_calls = _chat_sum
            chat_avg_score = float(chat_row[1]) if chat_row[1] is not None else None
            chat_avg_response_time_seconds = float(chat_row[2]) if chat_row[2] is not None else None
            chat_transfer_count = int(chat_row[3] or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(df.amount), 0)
            FROM daily_fines df
            JOIN daily_hours dh ON df.daily_hours_id = dh.id
            WHERE dh.operator_id = %s AND dh.day >= %s AND dh.day <= %s
        """, (operator_id, start, end))
        total_fines = cursor.fetchone()[0] or 0.0

        cursor.execute("""
            SELECT COALESCE(SUM(
                CASE
                    WHEN oa.end_time <= oa.start_time
                        THEN EXTRACT(EPOCH FROM (oa.end_time + INTERVAL '24 hours' - oa.start_time))
                    ELSE EXTRACT(EPOCH FROM (oa.end_time - oa.start_time))
                END
            ) / 3600.0, 0)
            FROM operator_offline_activities oa
            WHERE oa.operator_id = %s
              AND oa.activity_date >= %s
              AND oa.activity_date <= %s
        """, (operator_id, start, end))
        total_offline_hours = float(cursor.fetchone()[0] or 0.0)

        cursor.execute("""
            SELECT COALESCE(SUM(
                CASE
                    WHEN ws.end_time <= ws.start_time
                        THEN EXTRACT(EPOCH FROM (ws.end_time + INTERVAL '24 hours' - ws.start_time))
                    ELSE EXTRACT(EPOCH FROM (ws.end_time - ws.start_time))
                END
            ) / 3600.0, 0)
            FROM work_shifts ws
            WHERE ws.operator_id = %s
              AND COALESCE(ws.shift_type, 'regular') = 'office_practice'
              AND ws.shift_date >= %s
              AND ws.shift_date <= %s
        """, (operator_id, start, end))
        total_offline_hours += float(cursor.fetchone()[0] or 0.0)
        manual_phone_training_map, _manual_phone_training_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=start,
            end_date=end
        )
        status_phone_training_map, _status_phone_training_totals = self._load_phone_shift_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=start,
            end_date=end
        )
        phone_training_totals = self._sum_phone_shift_offline_interval_maps(
            [operator_id],
            manual_phone_training_map,
            status_phone_training_map
        )
        total_offline_hours += float(phone_training_totals.get(int(operator_id), 0.0) or 0.0)

        effective_hours_for_calls = max(0.0, float(total_work_time or 0.0))

        if effective_hours_for_calls > 0:
            calls_per_hour = float(total_calls) / float(effective_hours_for_calls)
        else:
            calls_per_hour = 0.0

        cursor.execute("""
            INSERT INTO work_hours (
                operator_id, month, regular_hours, training_hours, fines, total_break_time,
                total_talk_time, total_calls, total_efficiency_hours, calls_per_hour, group_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (operator_id, month)
            DO UPDATE SET
                regular_hours = EXCLUDED.regular_hours,
                training_hours = EXCLUDED.training_hours,
                fines = EXCLUDED.fines,
                total_break_time = EXCLUDED.total_break_time,
                total_talk_time = EXCLUDED.total_talk_time,
                total_calls = EXCLUDED.total_calls,
                total_efficiency_hours = EXCLUDED.total_efficiency_hours,
                calls_per_hour = EXCLUDED.calls_per_hour,
                group_id = COALESCE(EXCLUDED.group_id, work_hours.group_id),
                created_at = CURRENT_TIMESTAMP
        """, (
            operator_id,
            month,
            float(total_work_time or 0.0),
            float(total_training_time or 0.0),
            float(total_fines or 0.0),
            float(total_break_time or 0.0),
            float(total_talk_time or 0.0),
            int(total_calls or 0),
            float(total_efficiency_hours or 0.0),
            float(calls_per_hour or 0.0),
            # group_id: активная группа оператора на конец месяца. Уникальность пока
            # (operator_id, month) — раздельные месячные итоги по группам появятся
            # после свопа constraint на (group_id, operator_id, month).
            self._get_operator_group_id_tx(cursor, operator_id, end)
        ))

        return {
            "calculation_model_code": calculation_model_code,
            "regular_hours": float(total_work_time or 0.0),
            "training_hours": float(total_training_time or 0.0),
            "total_break_time": float(total_break_time or 0.0),
            "total_talk_time": float(total_talk_time or 0.0),
            "total_calls": int(total_calls or 0),
            "total_dial_time": float(tez_dial_time or 0.0),
            "total_chats": int(tez_chats or 0),
            "total_efficiency_hours": float(total_efficiency_hours or 0.0),
            "calls_per_hour": float(calls_per_hour or 0.0),
            "fines": float(total_fines or 0.0),
            "offline_activity_hours": float(total_offline_hours or 0.0),
            "no_phone_hours": float(total_no_phone_seconds or 0.0) / 3600.0,
            "chat_avg_score": chat_avg_score,
            "chat_avg_response_time_seconds": chat_avg_response_time_seconds,
            "chat_transfer_count": int(chat_transfer_count or 0)
        }

    def _aggregate_segment_from_daily_tx(self, cursor, operator_id, month, start_day, end_day,
                                         group_id, calculation_model_code):
        """
        READ-ONLY. Месячные агрегаты ОДНОГО оператора за ОДИН сегмент группы
        (диапазон дней внутри месяца, отфильтрованный по group_id). Формулы 1:1 с
        _aggregate_month_from_daily_tx, но БЕЗ INSERT в work_hours и с фильтром по
        group_id + диапазону дней. Основа для group-aware ридеров и снимков:
        раздельные per-group итоги при переводе оператора посреди месяца.

        start_day/end_day — день месяца (клиппнутые к границам месяца).
        group_id — группа сегмента (None = без фильтра по группе).
        calculation_model_code — модель ГРУППЫ сегмента (передаётся, не резолвится).
        """
        operator_id = int(operator_id)
        year, mon = map(int, str(month).split('-'))
        last_day = calendar.monthrange(year, mon)[1]
        sd = max(1, int(start_day))
        ed = min(last_day, int(end_day))
        seg_start = date(year, mon, sd)
        seg_end = date(year, mon, ed)
        gid = int(group_id) if group_id is not None else None

        cursor.execute("""
            SELECT
                COALESCE(SUM(work_time),0),
                COALESCE(SUM(training_time),0),
                COALESCE(SUM(break_time),0),
                COALESCE(SUM(talk_time),0),
                COALESCE(SUM(calls),0),
                COALESCE(SUM(efficiency),0),
                COALESCE(SUM(no_phone_seconds),0),
                COALESCE(SUM(
                    CASE
                        WHEN jsonb_typeof(COALESCE(extra_metrics, '{}'::jsonb)->'talk_time') = 'number'
                            THEN (extra_metrics->>'talk_time')::double precision
                        ELSE 0
                    END
                ), 0),
                COALESCE(SUM(
                    CASE
                        WHEN jsonb_typeof(COALESCE(extra_metrics, '{}'::jsonb)->'dial_time') = 'number'
                            THEN (extra_metrics->>'dial_time')::double precision
                        ELSE 0
                    END
                ), 0),
                COALESCE(SUM(
                    CASE
                        WHEN jsonb_typeof(COALESCE(extra_metrics, '{}'::jsonb)->'chats') = 'number'
                            THEN (extra_metrics->>'chats')::integer
                        ELSE 0
                    END
                ), 0)
            FROM daily_hours
            WHERE operator_id = %s AND day >= %s AND day <= %s
              AND (%s IS NULL OR group_id = %s)
        """, (operator_id, seg_start, seg_end, gid, gid))
        row = cursor.fetchone()
        (
            total_work_time,
            total_training_time,
            total_break_time,
            total_talk_time,
            total_calls,
            total_efficiency_hours,
            total_no_phone_seconds,
            tez_talk_time,
            tez_dial_time,
            tez_chats,
        ) = row

        if calculation_model_code == CALCULATION_MODEL_TEZ_OP:
            total_talk_time = float(tez_talk_time or 0.0)

        chat_avg_score = None
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
                WHERE operator_id = %s AND day >= %s AND day <= %s
            """, (operator_id, seg_start, seg_end))
            chat_row = cursor.fetchone() or (0, None, None, 0)
            _chat_sum = int(chat_row[0] or 0)
            total_calls = _chat_sum
            chat_avg_score = float(chat_row[1]) if chat_row[1] is not None else None
            chat_avg_response_time_seconds = float(chat_row[2]) if chat_row[2] is not None else None
            chat_transfer_count = int(chat_row[3] or 0)

        cursor.execute("""
            SELECT COALESCE(SUM(df.amount), 0)
            FROM daily_fines df
            JOIN daily_hours dh ON df.daily_hours_id = dh.id
            WHERE dh.operator_id = %s AND dh.day >= %s AND dh.day <= %s
              AND (%s IS NULL OR dh.group_id = %s)
        """, (operator_id, seg_start, seg_end, gid, gid))
        total_fines = cursor.fetchone()[0] or 0.0

        cursor.execute("""
            SELECT COALESCE(SUM(
                CASE
                    WHEN oa.end_time <= oa.start_time
                        THEN EXTRACT(EPOCH FROM (oa.end_time + INTERVAL '24 hours' - oa.start_time))
                    ELSE EXTRACT(EPOCH FROM (oa.end_time - oa.start_time))
                END
            ) / 3600.0, 0)
            FROM operator_offline_activities oa
            WHERE oa.operator_id = %s AND oa.activity_date >= %s AND oa.activity_date <= %s
        """, (operator_id, seg_start, seg_end))
        total_offline_hours = float(cursor.fetchone()[0] or 0.0)

        cursor.execute("""
            SELECT COALESCE(SUM(
                CASE
                    WHEN ws.end_time <= ws.start_time
                        THEN EXTRACT(EPOCH FROM (ws.end_time + INTERVAL '24 hours' - ws.start_time))
                    ELSE EXTRACT(EPOCH FROM (ws.end_time - ws.start_time))
                END
            ) / 3600.0, 0)
            FROM work_shifts ws
            WHERE ws.operator_id = %s
              AND COALESCE(ws.shift_type, 'regular') = 'office_practice'
              AND ws.shift_date >= %s AND ws.shift_date <= %s
        """, (operator_id, seg_start, seg_end))
        total_offline_hours += float(cursor.fetchone()[0] or 0.0)
        manual_phone_training_map, _manual_phone_training_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=seg_start,
            end_date=seg_end,
            group_id=gid
        )
        status_phone_training_map, _status_phone_training_totals = self._load_phone_shift_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=seg_start,
            end_date=seg_end,
            group_id=gid
        )
        phone_training_totals = self._sum_phone_shift_offline_interval_maps(
            [operator_id],
            manual_phone_training_map,
            status_phone_training_map
        )
        total_offline_hours += float(phone_training_totals.get(int(operator_id), 0.0) or 0.0)

        effective_hours_for_calls = max(0.0, float(total_work_time or 0.0))
        calls_per_hour = (float(total_calls) / effective_hours_for_calls) if effective_hours_for_calls > 0 else 0.0

        return {
            "group_id": gid,
            "calculation_model_code": calculation_model_code,
            "start_day": sd,
            "end_day": ed,
            "regular_hours": float(total_work_time or 0.0),
            "training_hours": float(total_training_time or 0.0),
            "total_break_time": float(total_break_time or 0.0),
            "total_talk_time": float(total_talk_time or 0.0),
            "total_calls": int(total_calls or 0),
            "total_dial_time": float(tez_dial_time or 0.0),
            "total_chats": int(tez_chats or 0),
            "total_efficiency_hours": float(total_efficiency_hours or 0.0),
            "calls_per_hour": float(calls_per_hour or 0.0),
            "fines": float(total_fines or 0.0),
            "offline_activity_hours": float(total_offline_hours or 0.0),
            "no_phone_hours": float(total_no_phone_seconds or 0.0) / 3600.0,
            "chat_avg_score": chat_avg_score,
            "chat_avg_response_time_seconds": chat_avg_response_time_seconds,
            "chat_transfer_count": int(chat_transfer_count or 0),
        }

    def _is_month_closed(self, month, today=None):
        """Окно правок: месяц открыт (правится, считается на лету) до 10-го числа
        СЛЕДУЮЩЕГО месяца включительно; с 11-го — закрыт и подлежит заморозке в снимок.
        today передаётся в тестах; иначе текущая дата."""
        try:
            year, mon = map(int, str(month).split('-'))
        except Exception:
            return False
        ny, nm = (year + 1, 1) if mon == 12 else (year, mon + 1)
        deadline = date(ny, nm, 10)  # последний день окна правок прошлого месяца
        ref = today or date.today()
        return ref > deadline

    def _freeze_month_to_snapshots_tx(self, cursor, month):
        """Заморозить месяц в snapshot-таблицы (идемпотентный upsert). По каждой группе ×
        оператору-сегменту: per-segment агрегаты (_aggregate_segment_from_daily_tx) + замороженная
        мета (роль/статус на конец месяца из user_history, ставка, модель/направление группы, СВ
        группы за месяц). Плюс групповой снимок group_month_snapshots. Вызывается лениво при первом
        обращении к закрытому месяцу и админским «пересчитать месяц». Без потери данных в daily_hours."""
        year, mon = map(int, str(month).split('-'))
        last_day = calendar.monthrange(year, mon)[1]
        start = date(year, mon, 1)
        end = date(year, mon, last_day)
        ref_dt = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)
        frozen_at = datetime.utcnow()

        cursor.execute("""
            SELECT DISTINCT gom.operator_id
            FROM group_operator_memberships gom
            WHERE gom.start_date <= %s AND (gom.end_date IS NULL OR gom.end_date >= %s)
        """, (end, start))
        op_ids = [int(r[0]) for r in cursor.fetchall() or []]
        rows_written = 0

        if op_ids:
            role_as_of = self._resolve_user_field_as_of_tx(cursor, op_ids, 'role', ref_dt)
            status_as_of = self._resolve_user_field_as_of_tx(cursor, op_ids, 'status', ref_dt)
            cursor.execute("""
                SELECT u.id, u.name, u.role, u.status, u.direction_id, d.name,
                    COALESCE(
                        (SELECT wr.rate FROM work_hours wr
                         WHERE wr.operator_id = u.id AND wr.rate IS NOT NULL AND wr.month <= %s
                         ORDER BY wr.month DESC LIMIT 1),
                        u.rate
                    ),
                    COALESCE((SELECT w.norm_hours FROM work_hours w WHERE w.operator_id = u.id AND w.month = %s), 0)
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE u.id = ANY(%s)
            """, (month, month, op_ids))
            meta = {}
            for uid, name, role, status, dir_id, dir_name, rate, norm in cursor.fetchall() or []:
                meta[int(uid)] = {
                    "name": name, "role": role, "status": status,
                    "direction_id": dir_id, "direction_name": dir_name,
                    "rate": rate, "norm_hours": norm,
                }
            sv_cache = {}
            for op in op_ids:
                segs = self._get_operator_month_segments_tx(cursor, op, month)
                m = meta.get(op, {})
                eff_role = role_as_of.get(op) or m.get("role")
                eff_status = status_as_of.get(op) or m.get("status")
                for seg in segs:
                    a = seg["aggregates"]
                    g_id = seg["group_id"]
                    if g_id not in sv_cache:
                        sv_cache[g_id] = self._group_supervisors_for_month_tx(cursor, g_id, start, end)
                    sv_ids, sv_names = sv_cache[g_id]
                    cursor.execute("""
                        INSERT INTO group_operator_month_snapshots (
                            group_id, month, operator_id, name, role, status, direction_id, direction_name,
                            calculation_model_code, rate, supervisor_ids, supervisor_names, first_day, last_day,
                            norm_hours, regular_hours, training_hours, total_break_time, total_talk_time,
                            total_dial_time, total_calls, total_chats,
                            total_efficiency_hours, calls_per_hour, fines, offline_activity_hours,
                            no_phone_hours, chat_avg_score, chat_avg_response_time_seconds, chat_transfer_count, frozen_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (group_id, month, operator_id) DO UPDATE SET
                            name=EXCLUDED.name, role=EXCLUDED.role, status=EXCLUDED.status,
                            direction_id=EXCLUDED.direction_id, direction_name=EXCLUDED.direction_name,
                            calculation_model_code=EXCLUDED.calculation_model_code, rate=EXCLUDED.rate,
                            supervisor_ids=EXCLUDED.supervisor_ids, supervisor_names=EXCLUDED.supervisor_names,
                            first_day=EXCLUDED.first_day, last_day=EXCLUDED.last_day,
                            norm_hours=EXCLUDED.norm_hours, regular_hours=EXCLUDED.regular_hours,
                            training_hours=EXCLUDED.training_hours, total_break_time=EXCLUDED.total_break_time,
                            total_talk_time=EXCLUDED.total_talk_time, total_dial_time=EXCLUDED.total_dial_time,
                            total_calls=EXCLUDED.total_calls, total_chats=EXCLUDED.total_chats,
                            total_efficiency_hours=EXCLUDED.total_efficiency_hours, calls_per_hour=EXCLUDED.calls_per_hour,
                            fines=EXCLUDED.fines, offline_activity_hours=EXCLUDED.offline_activity_hours,
                            no_phone_hours=EXCLUDED.no_phone_hours, chat_avg_score=EXCLUDED.chat_avg_score,
                            chat_avg_response_time_seconds=EXCLUDED.chat_avg_response_time_seconds,
                            chat_transfer_count=EXCLUDED.chat_transfer_count, frozen_at=EXCLUDED.frozen_at
                    """, (
                        g_id, month, op, m.get("name"), eff_role, eff_status,
                        seg.get("direction_id"), seg.get("direction_name"),
                        seg.get("calculation_model_code"), m.get("rate"),
                        Json(sv_ids), Json(sv_names),
                        date(year, mon, seg["start_day"]), date(year, mon, seg["end_day"]),
                        m.get("norm_hours"),
                        a["regular_hours"], a["training_hours"], a["total_break_time"], a["total_talk_time"],
                        a["total_dial_time"], a["total_calls"], a["total_chats"],
                        a["total_efficiency_hours"], a["calls_per_hour"], a["fines"],
                        a["offline_activity_hours"], a["no_phone_hours"],
                        a["chat_avg_score"], a["chat_avg_response_time_seconds"], a["chat_transfer_count"],
                        frozen_at,
                    ))
                    rows_written += 1

        # Групповой снимок: мета каждой группы, у которой были операторы в этом месяце.
        cursor.execute("""
            SELECT g.id, g.name, g.status, g.direction_id, g.calculation_model_code, g.table_url, g.archived_at
            FROM groups g
            WHERE EXISTS (
                SELECT 1 FROM group_operator_memberships gom
                WHERE gom.group_id = g.id AND gom.start_date <= %s
                  AND (gom.end_date IS NULL OR gom.end_date >= %s)
            )
        """, (end, start))
        for g_id, g_name, g_status, g_dir, g_model, g_url, g_arch in cursor.fetchall() or []:
            cursor.execute("""
                INSERT INTO group_month_snapshots (group_id, month, name, status, direction_id,
                    calculation_model_code, table_url, archived, frozen_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (group_id, month) DO UPDATE SET
                    name=EXCLUDED.name, status=EXCLUDED.status, direction_id=EXCLUDED.direction_id,
                    calculation_model_code=EXCLUDED.calculation_model_code, table_url=EXCLUDED.table_url,
                    archived=EXCLUDED.archived, frozen_at=EXCLUDED.frozen_at
            """, (g_id, month, g_name, g_status, g_dir, g_model, g_url, bool(g_arch), frozen_at))

        return {"month": month, "operators": len(op_ids), "rows": rows_written, "frozen_at": frozen_at.isoformat()}

    def recompute_month_snapshot(self, month):
        """Публичный: (пере)заморозить месяц в снимок (идемпотентно, upsert). Для админского
        «Пересчитать месяц» и ленивой авто-заморозки закрытых месяцев."""
        if not re.match(r'^\d{4}-\d{2}$', str(month or '')):
            raise ValueError("month must be YYYY-MM")
        if not self._is_month_closed(month):
            raise ValueError("Month is still open; snapshot can be frozen only after the correction window closes")
        with self._get_cursor() as cursor:
            return self._freeze_month_to_snapshots_tx(cursor, month)

    def _month_snapshot_exists_tx(self, cursor, month):
        """Есть ли уже замороженный снимок за месяц (хотя бы одна строка с frozen_at)."""
        cursor.execute(
            "SELECT 1 FROM group_operator_month_snapshots WHERE month = %s AND frozen_at IS NOT NULL LIMIT 1",
            (month,)
        )
        return cursor.fetchone() is not None

    def get_month_snapshot(self, month, group_id=None):
        """Читает замороженный снимок месяца (group_operator_month_snapshots), сгруппированный
        по группам. {month, frozen, groups: [{group_id, name, model, supervisors, operators:[...]}]}.
        Используется селектором месяца в GroupsView и (далее) ридерами закрытых месяцев."""
        with self._get_cursor() as cursor:
            params = [month]
            where = "WHERE s.month = %s"
            if group_id is not None:
                where += " AND s.group_id = %s"
                params.append(int(group_id))
            cursor.execute("""
                SELECT s.group_id, s.operator_id, s.name, s.role, s.status, s.direction_id, s.direction_name,
                       s.calculation_model_code, s.rate, s.supervisor_ids, s.supervisor_names,
                       s.first_day, s.last_day, s.norm_hours, s.regular_hours, s.training_hours,
                       s.total_break_time, s.total_talk_time, s.total_dial_time,
                       s.total_calls, s.total_chats, s.total_efficiency_hours,
                       s.calls_per_hour, s.fines, s.offline_activity_hours, s.no_phone_hours,
                       COALESCE(
                           s.chat_avg_score,
                           (
                               SELECT
                                   (
                                       COALESCE(SUM(cmm.score_sum) FILTER (
                                           WHERE cmm.score_count > 0 AND cmm.score_sum IS NOT NULL
                                       ), 0)
                                       + COALESCE(SUM(cmm.avg_score) FILTER (
                                           WHERE NOT (
                                               COALESCE(cmm.score_count, 0) > 0
                                               AND cmm.score_sum IS NOT NULL
                                           )
                                             AND cmm.avg_score IS NOT NULL
                                       ), 0)
                                   ) / NULLIF(
                                       COALESCE(SUM(cmm.score_count) FILTER (
                                           WHERE cmm.score_count > 0 AND cmm.score_sum IS NOT NULL
                                       ), 0)
                                       + COALESCE(COUNT(cmm.avg_score) FILTER (
                                           WHERE NOT (
                                               COALESCE(cmm.score_count, 0) > 0
                                               AND cmm.score_sum IS NOT NULL
                                           )
                                             AND cmm.avg_score IS NOT NULL
                                       ), 0),
                                       0
                                   )
                               FROM chat_manager_daily_metrics cmm
                               WHERE cmm.operator_id = s.operator_id
                                 AND cmm.day >= COALESCE(
                                     s.first_day,
                                     TO_DATE(s.month || '-01', 'YYYY-MM-DD')
                                 )
                                 AND cmm.day <= COALESCE(
                                     s.last_day,
                                     (
                                         TO_DATE(s.month || '-01', 'YYYY-MM-DD')
                                         + INTERVAL '1 month - 1 day'
                                     )::date
                                 )
                                 AND EXISTS (
                                     SELECT 1
                                     FROM group_operator_memberships gom
                                     WHERE gom.operator_id = cmm.operator_id
                                       AND gom.group_id = s.group_id
                                       AND gom.start_date <= cmm.day
                                       AND (gom.end_date IS NULL OR gom.end_date >= cmm.day)
                                 )
                           )
                       ) AS chat_avg_score,
                       s.chat_avg_response_time_seconds, s.chat_transfer_count, s.frozen_at,
                       gms.name AS group_name
                FROM group_operator_month_snapshots s
                LEFT JOIN group_month_snapshots gms ON gms.group_id = s.group_id AND gms.month = s.month
                """ + where + """
                ORDER BY s.group_id, s.name
            """, tuple(params))
            groups = {}
            frozen = False
            for r in cursor.fetchall() or []:
                (g_id, op_id, name, role, status, dir_id, dir_name, model, rate, sv_ids, sv_names,
                 first_day, last_day, norm, regular, training, brk, talk, dial, calls, chats, eff, cph, fines,
                 offline, no_phone, c_score, c_resp, c_transfer, frozen_at, group_name) = r
                if frozen_at is not None:
                    frozen = True
                g = groups.setdefault(int(g_id), {
                    "group_id": int(g_id),
                    "group_name": group_name,
                    "calculation_model_code": model,
                    "supervisor_ids": sv_ids or [],
                    "supervisor_names": sv_names or [],
                    "operators": [],
                })
                g["operators"].append({
                    "operator_id": int(op_id), "name": name, "role": role, "status": status,
                    "direction_id": dir_id, "direction_name": dir_name,
                    "calculation_model_code": model, "rate": float(rate) if rate is not None else None,
                    "first_day": first_day.isoformat() if first_day else None,
                    "last_day": last_day.isoformat() if last_day else None,
                    "aggregates": {
                        "norm_hours": norm, "regular_hours": regular, "training_hours": training,
                        "total_break_time": brk, "total_talk_time": talk,
                        "total_dial_time": dial, "total_calls": calls, "total_chats": chats,
                        "total_efficiency_hours": eff, "calls_per_hour": cph, "fines": fines,
                        "offline_activity_hours": offline, "no_phone_hours": no_phone,
                        "chat_avg_score": c_score, "chat_avg_response_time_seconds": c_resp,
                        "chat_transfer_count": c_transfer,
                    },
                    "frozen_at": frozen_at.isoformat() if frozen_at else None,
                })
            return {"month": month, "frozen": frozen, "groups": list(groups.values())}

    def add_manual_fine_for_day(self, operator_id, day, amount, reason, comment=None):
        operator_id_int = int(operator_id)
        day_obj = self._normalize_schedule_date(day)

        amount_value = round(float(amount or 0.0), 2)
        if amount_value <= 0:
            raise ValueError("amount must be greater than 0")

        reason_norm = str(reason or '').strip()
        if not reason_norm:
            raise ValueError("reason is required")
        if reason_norm.lower() in ('тренинг', 'training'):
            raise ValueError("Training must be saved in trainings table")

        comment_norm = str(comment or '').strip() or None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id
                FROM daily_hours
                WHERE operator_id = %s AND day = %s
                FOR UPDATE
            """, (operator_id_int, day_obj))
            row = cursor.fetchone()

            if row:
                daily_id = row[0]
            else:
                cursor.execute("""
                    INSERT INTO daily_hours (
                        operator_id,
                        day,
                        work_time,
                        break_time,
                        talk_time,
                        calls,
                        efficiency,
                        training_time,
                        late_minutes,
                        early_leave_minutes,
                        overtime_minutes,
                        training_minutes,
                        auto_aggregated,
                        auto_aggregated_at
                    )
                    VALUES (
                        %s, %s,
                        0, 0, 0, 0, 0,
                        0, 0, 0, 0, 0,
                        TRUE, CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (operator_id, day)
                    DO UPDATE SET operator_id = EXCLUDED.operator_id
                    RETURNING id
                """, (operator_id_int, day_obj))
                daily_id = cursor.fetchone()[0]

            cursor.execute("""
                INSERT INTO daily_fines (daily_hours_id, operator_id, day, amount, reason, comment)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                daily_id,
                operator_id_int,
                day_obj,
                amount_value,
                reason_norm,
                comment_norm
            ))
            fine_id = int(cursor.fetchone()[0])

            cursor.execute("""
                SELECT COALESCE(SUM(amount), 0)
                FROM daily_fines
                WHERE daily_hours_id = %s
            """, (daily_id,))
            total_fine_amount = float(cursor.fetchone()[0] or 0.0)

            cursor.execute("""
                SELECT reason, comment
                FROM daily_fines
                WHERE daily_hours_id = %s
                ORDER BY id DESC
                LIMIT 1
            """, (daily_id,))
            latest_fine_row = cursor.fetchone() or (None, None)
            latest_reason = latest_fine_row[0]
            latest_comment = latest_fine_row[1]

            cursor.execute("""
                UPDATE daily_hours
                SET fine_amount = %s,
                    fine_reason = %s,
                    fine_comment = %s,
                    created_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (
                total_fine_amount,
                latest_reason,
                latest_comment,
                daily_id
            ))

            self._aggregate_month_from_daily_tx(cursor, operator_id_int, day_obj.strftime('%Y-%m'))

            return {
                'operator_id': operator_id_int,
                'day': day_obj.strftime('%Y-%m-%d'),
                'daily_hours_id': str(daily_id),
                'fine': {
                    'id': fine_id,
                    'amount': float(amount_value),
                    'reason': reason_norm,
                    'comment': comment_norm
                },
                'fine_total_for_day': float(total_fine_amount)
            }

    def toggle_day_off(self, operator_id, day_off_date, actor_id=None, change_source='supervisor'):
        """
        Переключить выходной день для оператора.
        Если день уже выходной - убрать, иначе - добавить и удалить все смены в этот день.
        Возвращает True если день стал выходным, False если был убран.
        """
        day_off_date_obj = self._normalize_schedule_date(day_off_date)
        operator_id = int(operator_id)

        with self._get_cursor() as cursor:
            with self._schedule_change_audit(
                cursor,
                [(operator_id, day_off_date_obj)],
                actor_id=actor_id,
                source=change_source
            ):
                cursor.execute("""
                    SELECT 1
                    FROM days_off
                    WHERE operator_id = %s AND day_off_date = %s
                """, (operator_id, day_off_date_obj))
                existing = cursor.fetchone()

                if existing:
                    cursor.execute("""
                        DELETE FROM days_off
                        WHERE operator_id = %s AND day_off_date = %s
                    """, (operator_id, day_off_date_obj))
                else:
                    cursor.execute("""
                        INSERT INTO days_off (operator_id, day_off_date)
                        VALUES (%s, %s)
                        ON CONFLICT (operator_id, day_off_date) DO NOTHING
                    """, (operator_id, day_off_date_obj))
                    cursor.execute("""
                        DELETE FROM work_shifts
                        WHERE operator_id = %s AND shift_date = %s
                    """, (operator_id, day_off_date_obj))

            self._recalculate_auto_daily_hours_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=day_off_date_obj,
                end_date=day_off_date_obj
            )
            return not existing

    def _set_day_off_tx(self, cursor, operator_id, day_off_date):
        operator_id = int(operator_id)
        day_off_date_obj = self._normalize_schedule_date(day_off_date)
        cursor.execute("""
            INSERT INTO days_off (operator_id, day_off_date)
            VALUES (%s, %s)
            ON CONFLICT (operator_id, day_off_date) DO NOTHING
        """, (operator_id, day_off_date_obj))
        deleted_shifts = self._delete_all_shifts_for_day_tx(cursor, operator_id, day_off_date_obj)
        return {
            'operator_id': operator_id,
            'date': day_off_date_obj.strftime('%Y-%m-%d'),
            'deleted_shifts': deleted_shifts
        }
