"""База данных: супервайзеры и Clockster.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    CALCULATION_MODEL_CHAT_MANAGER,
    CALCULATION_MODEL_TEZ_OP,
    calendar,
    date,
    datetime,
    dt_time,
    execute_values,
    logging,
    SCHEDULE_SPECIAL_STATUS_META,
    SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES,
    supervisor_hours,
    timedelta,
)


class SupervisorsMixin:
    def get_supervisor_direction_ids(self, supervisor_id, department_id=None):
        """Живые направления супервайзера: активные группы, где он текущий СВ,
        плюс операторы с users.supervisor_id (legacy-кэш, синхронизируется с группами).
        department_id сужает результат до направлений одного отдела."""
        if not supervisor_id:
            return []
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT d.id
                  FROM directions d
                 WHERE d.is_active = TRUE
                   AND (%(dept)s::int IS NULL OR d.department_id = %(dept)s)
                   AND d.id IN (
                       SELECT g.direction_id
                         FROM groups g
                         JOIN group_supervisor_memberships m ON m.group_id = g.id
                        WHERE m.supervisor_id = %(sv)s
                          AND g.status = 'active'
                          AND g.direction_id IS NOT NULL
                          AND m.start_date <= CURRENT_DATE
                          AND (m.end_date IS NULL OR m.end_date >= CURRENT_DATE)
                       UNION
                       SELECT u.direction_id
                         FROM users u
                        WHERE u.supervisor_id = %(sv)s AND u.direction_id IS NOT NULL
                   )
                 ORDER BY d.id
            """, {"sv": supervisor_id, "dept": department_id})
            return [int(row[0]) for row in (cursor.fetchall() or [])]

    def get_daily_hours_by_supervisor_month(self, supervisor_id, month, group_id=None):
        """
        Возвращает daily_hours и агрегаты work_hours для операторов за месяц YYYY-MM.
        Если задан group_id — операторы выбираются по членству в группе, пересекающему
        месяц; модель берётся из ГРУППЫ, а daily фильтруется по этой группе (поддержка
        раздельных дней при переводе посреди месяца). Иначе — legacy-выборка по
        users.supervisor_id (поведение сохранено 1:1).
        Включает также ставку (rate) из users, norm_hours и fines из work_hours.
        """
        import calendar as _py_calendar
        from datetime import date as _date

        # validate month format YYYY-MM
        try:
            year, mon = map(int, month.split('-'))
            days = _py_calendar.monthrange(year, mon)[1]
            start = _date(year, mon, 1)
            end = _date(year, mon, days)
        except Exception as e:
            raise ValueError("Invalid month format, expected YYYY-MM") from e

        with self._get_cursor() as cursor:
            # Ленивая заморозка: при первом обращении к ЗАКРЫТОМУ месяцу (после 10-го числа
            # следующего) создаём снимок, если его ещё нет. Защищено try/except — не ломает чтение.
            if group_id is not None and self._is_month_closed(month):
                try:
                    if not self._month_snapshot_exists_tx(cursor, month):
                        self._freeze_month_to_snapshots_tx(cursor, month)
                except Exception as _freeze_err:
                    logging.error("lazy snapshot freeze failed for %s: %s", month, _freeze_err, exc_info=True)
            # Получаем операторов + ставка + norm_hours + агрегаты work_hours (включая fines).
            # Колонка модели и источник операторов различаются: group-aware vs legacy,
            # но СПИСОК и ПОРЯДОК колонок идентичны (downstream-распаковка одна).
            _sel_prefix = """
                SELECT u.id, u.name,
                    COALESCE(
                        (SELECT wr.rate FROM work_hours wr
                         WHERE wr.operator_id = u.id AND wr.rate IS NOT NULL AND wr.month <= %s
                         ORDER BY wr.month DESC LIMIT 1),
                        u.rate
                    ) AS rate,
                    u.status, u.supervisor_id, u.hire_date,
                    d.name as direction_name,
            """
            _sel_suffix = """
                    COALESCE(w.norm_hours, 0) as norm_hours,
                    COALESCE(w.regular_hours, 0) as regular_hours,
                    COALESCE(w.total_break_time, 0) as total_break_time,
                    COALESCE(w.total_talk_time, 0) as total_talk_time,
                    COALESCE(w.total_calls, 0) as total_calls,
                    COALESCE(w.total_efficiency_hours, 0) as total_efficiency_hours,
                    COALESCE(w.calls_per_hour, 0) as calls_per_hour,
                    COALESCE(w.fines, 0) as fines
            """
            if group_id is not None:
                # У группы без своей модели — модель направления сотрудника
                # (та же лестница, что в _GROUP_MEMBER_MODEL_SQL; d здесь уже
                # направление сотрудника).
                cursor.execute(
                    _sel_prefix
                    + "                    COALESCE(g.calculation_model_code, d.calculation_model_code) as calculation_model_code,\n"
                    + _sel_suffix
                    + """
                    FROM group_operator_memberships gom
                    JOIN groups g ON g.id = gom.group_id
                    -- Историческая видимость: состав месяца определяется ЧЛЕНСТВОМ в группе,
                    -- а не текущей ролью. Иначе оператор, ставший СВ/уволенный, пропадает из
                    -- прошлых месяцев. Роль/статус на конец месяца резолвятся отдельно (status_as_of).
                    JOIN users u ON u.id = gom.operator_id
                    LEFT JOIN work_hours w ON w.operator_id = u.id AND w.month = %s
                    LEFT JOIN directions d ON u.direction_id = d.id
                    WHERE gom.group_id = %s
                      AND gom.start_date <= %s
                      AND (gom.end_date IS NULL OR gom.end_date >= %s)
                    ORDER BY u.name
                    """,
                    (month, month, int(group_id), end, start),
                )
            else:
                cursor.execute(
                    _sel_prefix
                    + "                    d.calculation_model_code as calculation_model_code,\n"
                    + _sel_suffix
                    + """
                    FROM users u
                    LEFT JOIN work_hours w ON w.operator_id = u.id AND w.month = %s
                    LEFT JOIN directions d ON u.direction_id = d.id
                    WHERE u.role = 'operator' AND u.supervisor_id = %s
                    ORDER BY u.name
                    """,
                    (month, month, supervisor_id),
                )
            operator_rows = cursor.fetchall()  # list of tuples
            if group_id is not None:
                # Состав месяца — люди, а не записи членства. Записей одного человека
                # в группе за месяц бывает две: ушёл и вернулся, или перевод исправили
                # в день зачисления (осталась запись «с 09.11 по 08.11»). Строки у них
                # одинаковые — без этого он шёл бы в «Учёте часов» и в Excel дважды.
                unique_rows, seen_operator_ids = [], set()
                for row in operator_rows:
                    if row[0] not in seen_operator_ids:
                        seen_operator_ids.add(row[0])
                        unique_rows.append(row)
                operator_rows = unique_rows

            if not operator_rows:
                return {"month": month, "days_in_month": days, "operators": []}

            op_ids = [row[0] for row in operator_rows]

            # --- transfer-aware: членства всех операторов, пересекающие месяц ---
            # Только в group-aware режиме (group_id задан). В legacy сегменты пустые,
            # фронт остаётся в прежнем поведении 1:1. Важно отдавать модель сегмента:
            # при переводе между операторской и чат-моделью UI должен брать метрики
            # той модели, которая действовала в конкретные дни.
            segments_by_operator = {}
            if group_id is not None:
                segments_by_operator = self._load_segments_by_operator_tx(cursor, op_ids, start, end)
                for segs in segments_by_operator.values():
                    for seg in segs:
                        seg["is_current"] = int(seg.get("group_id") or 0) == int(group_id)

            # --- статус оператора на конец месяца (из user_history; fallback — текущий) ---
            # Прошлый месяц должен показывать статус, который был тогда, а не нынешний.
            status_as_of = {}
            cursor.execute(
                """
                SELECT user_id, old_value, new_value, changed_at
                FROM user_history
                WHERE field_changed = 'status' AND user_id = ANY(%s)
                ORDER BY user_id, changed_at
                """,
                (op_ids,),
            )
            _st_changes, _st_first_old = {}, {}
            for _uid, _oldv, _newv, _at in cursor.fetchall() or []:
                if _uid not in _st_changes:
                    _st_changes[_uid] = []
                    _st_first_old[_uid] = _oldv
                _st_changes[_uid].append((_at, _newv))
            _st_ref = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)
            for _uid, _changes in _st_changes.items():
                _eff = None
                for _at, _newv in _changes:
                    if _at is not None and _at < _st_ref:
                        _eff = _newv
                    else:
                        break
                if _eff is None:
                    _eff = _st_first_old.get(_uid)
                if _eff:
                    status_as_of[int(_uid)] = _eff

            # Дата увольнения — фронт по ней решает вкладку «Активные»/«Уволенные».
            dismissal_dates = self._load_dismissal_dates_tx(cursor, op_ids)

            # Получаем daily_hours для этих операторов за месяц (с информацией о штрафах).
            # В group-aware режиме фильтруем по группе: дни этого оператора, отнесённые
            # именно к этой группе (поддержка раздельных дней при переводе).
            _daily_sql = """
                SELECT d.operator_id, d.day, d.work_time, d.break_time, d.talk_time, d.calls, d.efficiency,
                    d.fine_amount, d.fine_reason, d.fine_comment,
                    COALESCE(d.no_phone_minutes, 0), COALESCE(d.no_phone_seconds, 0),
                    COALESCE(d.extra_metrics, '{{}}'::jsonb)
                FROM daily_hours d
                WHERE d.operator_id = ANY(%s)
                AND d.day >= %s AND d.day <= %s
                {group_filter}
                ORDER BY d.operator_id, d.day
            """
            if group_id is not None:
                cursor.execute(
                    _daily_sql.format(group_filter="AND d.group_id = %s"),
                    (op_ids, start, end, int(group_id)),
                )
            else:
                cursor.execute(
                    _daily_sql.format(group_filter=""),
                    (op_ids, start, end),
                )
            daily_rows = cursor.fetchall()

            # Готовим словарь daily: operator_id -> {day_number: {...}}
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

            # fetch fines for all operators in range
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
                end_date=end,
                group_id=group_id
            )
            offline_map_by_operator, offline_totals_by_operator = self._load_offline_activities_by_operator_day_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start,
                end_date=end,
                group_id=group_id
            )
            training_totals_by_operator = self._load_training_hours_by_operator_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start,
                end_date=end,
                group_id=group_id
            )
            chat_metrics_by_operator, chat_totals_by_operator = self._load_chat_manager_metrics_by_operator_day_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start,
                end_date=end,
                group_id=group_id
            )

            # Сбор финального списка операторов
            operators = []
            for row in operator_rows:
                (op_id, op_name, rate, status, sup_id, hire_date, direction_name, calculation_model_raw, norm_hours,
                regular_hours, total_break_time, total_talk_time,
                total_calls, total_efficiency_hours, calls_per_hour, fines) = row
                calculation_model_code = self._normalize_calculation_model_code(calculation_model_raw, direction_name)
                op_daily = daily_map.get(op_id, {})
                # transfer-aware: в group-aware режиме месячные агрегаты пересобираем из
                # group-filtered daily_map (work_hours хранит ОБЩИЙ итог оператора без
                # разбивки по группам). Формулы — как в _aggregate_month_from_daily_tx;
                # fines — из daily_fines (список fines[]).
                grp_regular = grp_break = grp_talk = grp_eff = grp_fines = grp_dial = 0.0
                grp_calls = grp_chats = 0
                if group_id is not None:
                    for _dnum, _dd in op_daily.items():
                        grp_regular += float(_dd.get("work_time") or 0.0)
                        grp_break += float(_dd.get("break_time") or 0.0)
                        grp_talk += float(_dd.get("talk_time") or 0.0)
                        grp_dial += float(_dd.get("dial_time") or 0.0)
                        grp_eff += float(_dd.get("efficiency") or 0.0)
                        grp_calls += int(_dd.get("calls") or 0)
                        grp_chats += int(_dd.get("chats") or 0)
                        for _f in (_dd.get("fines") or []):
                            grp_fines += float(_f.get("amount") or 0.0)
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
                accounted_regular_hours = float(grp_regular if group_id is not None else (regular_hours or 0.0))
                accounted_hours = accounted_regular_hours + training_hours + technical_issue_hours + offline_activity_hours
                chat_metrics_by_day = chat_metrics_by_operator.get(op_id, {}) if isinstance(chat_metrics_by_operator, dict) else {}
                chat_metric_totals = chat_totals_by_operator.get(op_id, {}) if isinstance(chat_totals_by_operator, dict) else {}
                if calculation_model_code == CALCULATION_MODEL_CHAT_MANAGER:
                    _chat_chats = int(chat_metric_totals.get("chats_count") or 0)
                    total_calls = _chat_chats
                    regular_hours_for_rate = max(0.0, accounted_regular_hours)
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
                elif calculation_model_code == CALCULATION_MODEL_TEZ_OP and group_id is None:
                    total_calls = int(daily_calls_total)
                    total_talk_time = float(daily_talk_total)
                    regular_hours_for_rate = max(0.0, accounted_regular_hours)
                    calls_per_hour = (
                        float(total_calls) / regular_hours_for_rate
                        if regular_hours_for_rate > 0
                        else 0.0
                    )

                operators.append({
                    "operator_id": op_id,
                    "name": op_name,
                    "direction": direction_name,
                    "calculation_model_code": calculation_model_code,
                    "calculationModelCode": calculation_model_code,
                    "supervisor_id": sup_id,
                    "rate": float(rate) if rate is not None else 0.0,
                    "status": status_as_of.get(op_id, status),
                    "dismissal_date": self._dismissal_date_iso(dismissal_dates, op_id),
                    "hire_date": hire_date.isoformat() if hasattr(hire_date, "isoformat") else hire_date,
                    "norm_hours": float(norm_hours) if norm_hours is not None else 0.0,
                    "training_hours": round(float(training_hours), 2),
                    "no_phone_hours": round(float(no_phone_hours), 2),
                    "accounted_hours": round(float(accounted_hours), 2),
                    "worked_hours_used": round(float(accounted_hours), 2),
                    "daily": op_daily,
                    "group_segments": segments_by_operator.get(op_id, []),
                    "chat_metrics_by_day": chat_metrics_by_day,
                    "chat_metrics": chat_metric_totals,
                    "technical_issues_by_day": technical_map_by_operator.get(op_id, {}) if isinstance(technical_map_by_operator, dict) else {},
                    "technical_issue_hours": round(float(technical_issue_hours), 2),
                    "offline_activities_by_day": offline_map_by_operator.get(op_id, {}) if isinstance(offline_map_by_operator, dict) else {},
                    "offline_activity_hours": round(float(offline_activity_hours), 2),
                    "aggregates": ({
                        # group-aware: суммы из group-filtered daily_map (раздельно по группам)
                        "regular_hours": float(grp_regular),
                        "total_break_time": float(grp_break),
                        "total_talk_time": float(grp_talk),
                        "total_calls": int(total_calls if calculation_model_code == CALCULATION_MODEL_CHAT_MANAGER else grp_calls),
                        "total_dial_time": float(grp_dial),
                        "total_chats": int(grp_chats),
                        "total_efficiency_hours": float(grp_eff),
                        "calls_per_hour": (
                            (float(total_calls if calculation_model_code == CALCULATION_MODEL_CHAT_MANAGER else grp_calls) / grp_regular)
                            if grp_regular > 0 else 0.0
                        ),
                        "fines": float(grp_fines),
                        "chat_avg_score": chat_metric_totals.get("avg_score"),
                        "chat_avg_response_time_seconds": chat_metric_totals.get("avg_response_time_seconds"),
                        "chat_transfer_count": int(chat_metric_totals.get("transfer_chat_count") or 0),
                    } if group_id is not None else {
                        # legacy (по СВ / без группы): агрегаты из work_hours — поведение 1:1
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
                    })
                })

        # Закрытый месяц (после 10-го числа следующего): накладываем ЗАМОРОЖЕННЫЕ агрегаты/мету
        # из снимка — числа стабильны и не «плывут» от пост-правок daily/смены формул. Композиция
        # и daily-календарь остаются (исторический след). Защищено try/except — фолбэк на live.
        if group_id is not None and operators and self._is_month_closed(month):
            try:
                with self._get_cursor() as _sc:
                    _sc.execute("""
                        SELECT operator_id, status, rate, norm_hours, regular_hours, total_break_time,
                               total_talk_time, total_dial_time, total_calls, total_chats,
                               total_efficiency_hours, calls_per_hour, fines,
                               chat_avg_score, chat_avg_response_time_seconds, chat_transfer_count
                        FROM group_operator_month_snapshots
                        WHERE group_id = %s AND month = %s AND frozen_at IS NOT NULL
                    """, (int(group_id), month))
                    snap = {int(r[0]): r for r in _sc.fetchall() or []}
                for _op in operators:
                    r = snap.get(int(_op.get("operator_id")))
                    if not r:
                        continue
                    _live_aggregates = _op.get("aggregates") or {}
                    if r[1] is not None:
                        _op["status"] = r[1]
                    if r[2] is not None:
                        _op["rate"] = float(r[2])
                    if r[3] is not None:
                        _op["norm_hours"] = float(r[3])
                    _op["aggregates"] = {
                        "regular_hours": float(r[4] or 0.0),
                        "total_break_time": float(r[5] or 0.0),
                        "total_talk_time": float(r[6] or 0.0),
                        "total_dial_time": (
                            float(r[7])
                            if r[7] is not None
                            else float(_live_aggregates.get("total_dial_time") or 0.0)
                        ),
                        "total_calls": int(r[8] or 0),
                        "total_chats": (
                            int(r[9])
                            if r[9] is not None
                            else int(_live_aggregates.get("total_chats") or 0)
                        ),
                        "total_efficiency_hours": float(r[10] or 0.0),
                        "calls_per_hour": float(r[11] or 0.0),
                        "fines": float(r[12] or 0.0),
                        "chat_avg_score": (
                            float(r[13])
                            if r[13] is not None
                            else _live_aggregates.get("chat_avg_score")
                        ),
                        "chat_avg_response_time_seconds": r[14],
                        "chat_transfer_count": int(r[15] or 0),
                    }
                    _op["frozen"] = True
            except Exception as _ov_err:
                logging.error("snapshot overlay failed for %s g%s: %s", month, group_id, _ov_err, exc_info=True)

        return {"month": month, "days_in_month": days, "operators": operators}

    def _clockster_hours_supervisor_ids_tx(self, cursor, user_ids=None):
        """СВ, чьи часы считаются по Clockster. user_ids=None — все такие СВ."""
        params = [list(SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES)]
        user_filter = ''
        if user_ids is not None:
            ids = sorted({int(v) for v in user_ids if v is not None})
            if not ids:
                return set()
            user_filter = ' AND u.id = ANY(%s)'
            params.append(ids)
        cursor.execute(
            "SELECT u.id FROM users u WHERE " + self.SUPERVISOR_ROLES_SQL
            + " AND " + self.SUPERVISOR_CLOCKSTER_DEPARTMENT_SQL + user_filter,
            tuple(params),
        )
        return {int(row[0]) for row in cursor.fetchall()}

    def clockster_hours_department_ids(self):
        """id отделов, у СВ которых часы по Clockster (коды засеяны миграцией, id разные)."""
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT id FROM departments WHERE lower(COALESCE(code, '')) = ANY(%s)",
                (list(SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES),),
            )
            return sorted(int(row[0]) for row in cursor.fetchall())

    def _supervisor_hours_settings_tx(self, cursor, user_ids):
        """{СВ: {'break_minutes', 'custom_break', 'no_card'}}; без строки — час по умолчанию."""
        ids = sorted({int(v) for v in (user_ids or []) if v is not None})
        result = {
            uid: {'break_minutes': supervisor_hours.DEFAULT_BREAK_MINUTES, 'custom_break': False, 'no_card': False}
            for uid in ids
        }
        if not ids:
            return result
        cursor.execute("""
            SELECT user_id, break_minutes, no_clockster_card
            FROM supervisor_hours_settings WHERE user_id = ANY(%s)
        """, (ids,))
        for user_id, minutes, no_card in cursor.fetchall():
            entry = result[int(user_id)]
            if minutes is not None:
                entry['break_minutes'] = int(minutes)
                entry['custom_break'] = True
            entry['no_card'] = bool(no_card)
        return result

    def _supervisor_directions_tx(self, cursor, user_ids, period_start, period_end):
        """{СВ: «Поток, Основа ОП»} — направления групп, которые СВ ведёт в периоде.

        Своего направления у СВ нет, и ставить его в карточку нельзя: по
        users.direction_id людей отбирают воронка ОП, табло Тез КЦ и план чатов,
        и СВ попал бы туда оператором. Направление группы — действующее
        (_GROUP_EFFECTIVE_DIRECTION_SQL): у части групп оно не задано, но
        выводится из модели расчёта."""
        ids = sorted({int(v) for v in (user_ids or []) if v is not None})
        if not ids:
            return {}
        cursor.execute("""
            SELECT gsm.supervisor_id, string_agg(DISTINCT dn.name, ', ' ORDER BY dn.name)
            FROM group_supervisor_memberships gsm
            JOIN groups g ON g.id = gsm.group_id
            JOIN directions dn ON dn.id = (""" + self._GROUP_EFFECTIVE_DIRECTION_SQL + """)
            WHERE gsm.supervisor_id = ANY(%s)
              AND gsm.start_date <= %s
              AND (gsm.end_date IS NULL OR gsm.end_date >= %s)
            GROUP BY gsm.supervisor_id
        """, (ids, period_end, period_start))
        return {int(row[0]): row[1] for row in cursor.fetchall() if row[1]}

    def _supervisor_clockster_cards_tx(self, cursor, people, settings=None):
        """{СВ: {'ext_id', 'full_name', 'source'} или None} — карточка Clockster человека.

        Ручная привязка (glb_employee_links, та же таблица, что в «Отметках»)
        главнее автоматической; «нет карточки» (no_clockster_card) главнее обеих.
        Автоматически сшиваем по ФИО тем же ключом, что «Отметки»
        (_glb_name_keys): «Иванов Пётр Сергеевич» у нас и «Иванов Пётр» в
        Clockster сходятся по «Фамилия Имя». Не сшиваем, если отчества есть с
        обеих сторон и разные, если ключ неоднозначен в Clockster (тёзки) или
        среди самих СВ — приписать СВ чужие часы хуже, чем не посчитать их вовсе.
        Карточка, у которой уже есть ручная строка (чья угодно или «не наш»), в
        автоматику не идёт."""
        people = [(int(uid), name) for uid, name in (people or [])]
        if settings is None:
            settings = self._supervisor_hours_settings_tx(cursor, [uid for uid, _ in people])
        cursor.execute(
            "SELECT workpace_ext_id, user_id FROM glb_employee_links WHERE workpace_ext_id LIKE %s",
            ('clockster:%',),
        )
        manual_by_user = {}
        taken = set()
        for ext_id, user_id in cursor.fetchall():
            taken.add(ext_id)
            if user_id is not None:
                manual_by_user.setdefault(int(user_id), ext_id)
        cursor.execute("SELECT ext_id, full_name FROM glb_employees WHERE source = 'clockster'")
        names = {}
        card_tokens = {}
        by_full, by_short = {}, {}
        for ext_id, full_name in cursor.fetchall():
            names[ext_id] = full_name
            full_key, short_key = self._glb_name_keys(full_name)
            card_tokens[ext_id] = len(full_key.split()) if full_key else 0
            if ext_id in taken:
                continue
            self._glb_index_put(by_full, full_key, ext_id)
            self._glb_index_put(by_short, short_key, ext_id)

        # Тёзки среди самих СВ: одна карточка не должна сшиться с двумя людьми.
        # Считаем по ВСЕМ СВ с часами по Clockster, а не по переданным: окно дня
        # и пересчёт после ручной отметки зовут этот метод с одним человеком, и
        # тёзка тогда был бы не виден — сшивка разошлась бы с ночным пересчётом.
        our_keys = {}
        for user_id, name in people:
            full_key, short_key = self._glb_name_keys(name)
            our_keys[user_id] = (full_key, short_key)
        all_supervisors = self._clockster_hours_supervisor_ids_tx(cursor)
        cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (sorted(all_supervisors | set(our_keys)),))
        short_counts = {}
        for _user_id, name in cursor.fetchall():
            _full_key, short_key = self._glb_name_keys(name)
            if short_key:
                short_counts[short_key] = short_counts.get(short_key, 0) + 1

        cards = {}
        for user_id, _name in people:
            if (settings.get(user_id) or {}).get('no_card'):
                cards[user_id] = None
                continue
            if user_id in manual_by_user:
                ext_id = manual_by_user[user_id]
                cards[user_id] = {'ext_id': ext_id, 'full_name': names.get(ext_id), 'source': 'manual'}
                continue
            full_key, short_key = our_keys[user_id]
            ext_id = None
            if full_key and full_key in by_full:
                ext_id = by_full[full_key]          # None — тёзки в Clockster
            elif short_key and short_key in by_short and short_counts.get(short_key, 0) == 1:
                candidate = by_short[short_key]
                our_tokens = len(full_key.split()) if full_key else 0
                # Отчество с обеих сторон и разное — это другой человек.
                if candidate and (our_tokens <= 2 or card_tokens.get(candidate, 0) <= 2):
                    ext_id = candidate
            cards[user_id] = (
                {'ext_id': ext_id, 'full_name': names.get(ext_id), 'source': 'auto'} if ext_id else None
            )
        return cards

    def store_supervisor_clockster_marks(self, marks_by_card, date_from, date_to):
        """Сохранить отметки, только что взятые из Clockster за дни [date_from, date_to].

        Замена, а не доливка: за эти дни синхронизация получила ВСЕХ людей
        Clockster, поэтому всё прежнее за период стирается — отметку, которую в
        Clockster убрали или поправили, иначе нельзя было бы забыть. Дни
        помечаются синхронизированными: окно дня и пересчёт берут отметки за них
        отсюда, а не из кэша «Отметок». Заодно — чистка старше срока хранения."""
        start = self._normalize_schedule_date(date_from)
        end = self._normalize_schedule_date(date_to)
        if end < start:
            start, end = end, start
        start_at = datetime.combine(start, dt_time.min, tzinfo=supervisor_hours.TZ)
        end_at = datetime.combine(end + timedelta(days=1), dt_time.min, tzinfo=supervisor_hours.TZ)
        rows = set()
        for ext_id, marks in (marks_by_card or {}).items():
            if not ext_id:
                continue
            for mark in marks or []:
                key = supervisor_hours.mark_key(mark.get('at'), mark.get('kind'))
                if key and key[1] in (supervisor_hours.MARK_IN, supervisor_hours.MARK_OUT) and start_at <= key[0] < end_at:
                    rows.add((str(ext_id), key[0], key[1]))
        with self._get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM supervisor_clockster_marks WHERE mark_at >= %s AND mark_at < %s",
                (start_at, end_at),
            )
            if rows:
                execute_values(cursor, """
                    INSERT INTO supervisor_clockster_marks (ext_id, mark_at, kind) VALUES %s
                    ON CONFLICT (ext_id, mark_at, kind) DO NOTHING
                """, sorted(rows), page_size=1000)
            execute_values(cursor, """
                INSERT INTO supervisor_clockster_sync_days (day) VALUES %s
                ON CONFLICT (day) DO UPDATE SET synced_at = NOW()
            """, [(day,) for day in supervisor_hours.iter_days(start, end)])
            cursor.execute(
                "DELETE FROM supervisor_clockster_marks WHERE mark_at < NOW() - make_interval(days => %s)",
                (self.SUPERVISOR_CLOCKSTER_MARKS_RETENTION_DAYS,),
            )
        return len(rows)

    def _supervisor_card_marks_multi_tx(self, cursor, ext_ids, date_from, date_to):
        """Отметки карточек Clockster за дни [date_from, date_to]:
        ({карточка: [отметка]}, дни с известными отметками, дни без них).

        День, который прошла синхронизация с Clockster, берётся из
        supervisor_clockster_marks; иначе — из кэша «Отметок», если он собран с
        Clockster. «Без них» — день кэша, собранный без Clockster (сбой), и не
        синхронизированный: такой день не считается, а день перед ним тоже
        (уход ночной смены лежит в нём)."""
        cursor.execute(
            "SELECT day FROM supervisor_clockster_sync_days WHERE day BETWEEN %s AND %s",
            (date_from, date_to),
        )
        synced = {row[0] for row in cursor.fetchall()}
        cursor.execute("""
            SELECT day, COALESCE(sources, '') LIKE %s
            FROM glb_attendance_days
            WHERE day BETWEEN %s AND %s
        """, ('%clockster%', date_from, date_to))
        cache_ok, cache_bad = set(), set()
        for day, has_clockster in cursor.fetchall():
            (cache_ok if has_clockster else cache_bad).add(day)
        known_days = synced | cache_ok
        unknown_days = cache_bad - synced
        ids = sorted({str(v) for v in (ext_ids or []) if v})
        marks = {}
        if ids:
            start_at = datetime.combine(date_from, dt_time.min, tzinfo=supervisor_hours.TZ)
            end_at = datetime.combine(date_to + timedelta(days=1), dt_time.min, tzinfo=supervisor_hours.TZ)
            if synced:
                cursor.execute("""
                    SELECT ext_id, mark_at, kind FROM supervisor_clockster_marks
                    WHERE ext_id = ANY(%s) AND mark_at >= %s AND mark_at < %s
                """, (ids, start_at, end_at))
                for ext_id, mark_at, kind in cursor.fetchall():
                    local = mark_at.astimezone(supervisor_hours.TZ)
                    if local.date() in synced:
                        marks.setdefault(ext_id, []).append({'at': local.isoformat(), 'kind': kind})
            cache_days = sorted(cache_ok - synced)
            if cache_days:
                cursor.execute("""
                    SELECT employee_id, marks FROM glb_attendance_rows
                    WHERE employee_id = ANY(%s) AND day = ANY(%s)
                """, (ids, cache_days))
                for ext_id, row_marks in cursor.fetchall():
                    for mark in row_marks or []:
                        when = supervisor_hours.parse_mark_time((mark or {}).get('at'))
                        # Отметка дня кэша, который синхронизация уже перекрыла, — не дублируем.
                        if when is not None and when.date() not in synced:
                            # Тип, который назвал терминал: «Отметки» показывают роль
                            # по порядку в смене (ТЗ iCore 3), а правило пар СВ
                            # построено на типе терминала — как у синхронизации.
                            if mark.get('terminal_kind'):
                                mark = {**mark, 'kind': mark['terminal_kind']}
                            marks.setdefault(ext_id, []).append(mark)
        return marks, known_days, unknown_days

    def _supervisor_manual_marks_tx(self, cursor, user_ids, date_from, date_to):
        """Ручные отметки РОП за дни [date_from, date_to] (по Алматы).

        Возвращает ({СВ: [отметка]}, {СВ: {ключ исправленной отметки Clockster}}).
        Исправление попадает во второй словарь, если в период попадает время
        исходной отметки, — иначе исходная вернулась бы в пары на краю окна."""
        ids = sorted({int(v) for v in (user_ids or []) if v is not None})
        if not ids:
            return {}, {}
        start_at = datetime.combine(date_from, dt_time.min, tzinfo=supervisor_hours.TZ)
        end_at = datetime.combine(date_to + timedelta(days=1), dt_time.min, tzinfo=supervisor_hours.TZ)
        cursor.execute("""
            SELECT m.id, m.user_id, m.mark_at, m.kind, m.comment, m.created_at, u.name,
                   m.replaces_clockster_at, m.replaces_clockster_kind
            FROM supervisor_manual_marks m
            LEFT JOIN users u ON u.id = m.created_by
            WHERE m.user_id = ANY(%s) AND m.deleted_at IS NULL
              AND ((m.mark_at >= %s AND m.mark_at < %s)
                   OR (m.replaces_clockster_at >= %s AND m.replaces_clockster_at < %s))
            ORDER BY m.mark_at, m.id
        """, (ids, start_at, end_at, start_at, end_at))
        marks, replaced = {}, {}
        for (mark_id, user_id, mark_at, kind, comment, created_at, author,
             replaces_at, replaces_kind) in cursor.fetchall():
            user_id = int(user_id)
            if replaces_at is not None and replaces_kind:
                key = supervisor_hours.mark_key(replaces_at, replaces_kind)
                if key:
                    replaced.setdefault(user_id, set()).add(key)
            if not (start_at <= mark_at < end_at):
                continue
            marks.setdefault(user_id, []).append({
                'id': int(mark_id),
                'at': mark_at.astimezone(supervisor_hours.TZ).isoformat(),
                'kind': kind,
                'source': 'manual',
                'comment': comment,
                'created_by_name': author,
                'created_at': created_at.isoformat() if created_at else None,
                'replaces_at': replaces_at.astimezone(supervisor_hours.TZ).isoformat() if replaces_at else None,
                'replaces_kind': replaces_kind,
            })
        return marks, replaced

    def _supervisor_card_marks_tx(self, cursor, user_id, name, settings, date_from, date_to):
        """(карточка Clockster СВ, её отметки за дни периода, дни с известными отметками)."""
        card = self._supervisor_clockster_cards_tx(cursor, [(user_id, name)], settings).get(user_id)
        ext_ids = [card['ext_id']] if card and card.get('ext_id') else []
        marks, known_days, _unknown = self._supervisor_card_marks_multi_tx(cursor, ext_ids, date_from, date_to)
        return card, (marks.get(ext_ids[0], []) if ext_ids else []), known_days

    def get_supervisor_clockster_day(self, user_id, day):
        """Окно дня СВ в «Учёте часов»: отметки Clockster и ручные, какие вошли в
        пары и почему остальные не засчитаны, итог дня и что записано в учёт."""
        user_id = int(user_id)
        day = self._normalize_schedule_date(day)
        with self._get_cursor() as cursor:
            if not self._clockster_hours_supervisor_ids_tx(cursor, [user_id]):
                raise ValueError('Это не супервайзер с часами по Clockster')
            cursor.execute("SELECT name FROM users WHERE id = %s", (user_id,))
            name = (cursor.fetchone() or [None])[0]
            settings = self._supervisor_hours_settings_tx(cursor, [user_id])
            card, raw, known_days = self._supervisor_card_marks_tx(
                cursor, user_id, name, settings, day - timedelta(days=1), day + timedelta(days=1))
            manual_by_user, replaced_by_user = self._supervisor_manual_marks_tx(
                cursor, [user_id], day - timedelta(days=1), day + timedelta(days=1))
            manual = manual_by_user.get(user_id, [])
            raw.extend(manual)
            cursor.execute("""
                SELECT work_time, break_time FROM daily_hours WHERE operator_id = %s AND day = %s
            """, (user_id, day))
            stored = cursor.fetchone()
            operator_day = bool(self._operator_membership_days_tx(cursor, [user_id], day, day))
            schedule_break = self._supervisor_schedule_break_minutes_tx(cursor, [user_id], day, day).get((user_id, day))
        break_minutes = schedule_break if schedule_break is not None else settings[user_id]['break_minutes']
        detail = supervisor_hours.explain_day(raw, day, break_minutes, replaced_by_user.get(user_id))
        manual_by_id = {mark['id']: mark for mark in manual}
        for row in detail['marks']:
            extra = manual_by_id.get(row.get('id')) if row.get('source') == 'manual' else None
            if extra:
                for field in ('comment', 'created_by_name', 'replaces_at', 'replaces_kind'):
                    row[field] = extra.get(field)
        return {
            'user_id': user_id,
            'name': name,
            'date': day.isoformat(),
            'card': card,
            'day_built': day in known_days,
            'operator_day': operator_day,
            'break_minutes': break_minutes,
            # schedule — перерывы, поставленные на смене; settings — общий перерыв СВ.
            'break_source': 'schedule' if schedule_break is not None else 'settings',
            'stored_work_time': float(stored[0]) if stored else None,
            'stored_break_time': float(stored[1]) if stored else None,
            **detail,
        }

    def supervisor_manual_mark_user(self, mark_id):
        """Чья действующая ручная отметка (для проверки прав до правки/снятия)."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT user_id FROM supervisor_manual_marks WHERE id = %s AND deleted_at IS NULL
            """, (int(mark_id),))
            row = cursor.fetchone()
        if not row:
            raise ValueError('Отметка не найдена')
        return int(row[0])

    def _validate_supervisor_mark_input(self, at, kind):
        kind = str(kind or '').strip().lower()
        if kind not in (supervisor_hours.MARK_IN, supervisor_hours.MARK_OUT):
            raise ValueError('Тип отметки — приход или уход')
        when = supervisor_hours.parse_mark_time(at)
        if when is None:
            raise ValueError('Укажите дату и время отметки')
        when = when.replace(second=0, microsecond=0)
        now = datetime.now(supervisor_hours.TZ)
        if when > now + timedelta(minutes=1):
            raise ValueError('Отметка не может быть в будущем')
        if when < now - timedelta(days=self.SUPERVISOR_MANUAL_MARK_MAX_AGE_DAYS):
            raise ValueError('Слишком старая дата: такой месяц уже закрыт')
        return when, kind

    def add_supervisor_manual_mark(self, user_id, at, kind, actor_id=None, comment=None, replaces_clockster=None):
        """Дописать СВ приход/уход, которого нет в Clockster, или исправить отметку
        Clockster (replaces_clockster={'at', 'kind'} — исходная), и пересчитать часы.

        Отметка из будущего и старше SUPERVISOR_MANUAL_MARK_MAX_AGE_DAYS не
        принимается: первая — это не исправление, вторая переписала бы давно
        закрытый месяц. Дубль (та же минута, тот же тип) — тоже отказ. Исходная
        отметка должна быть в отметках этого СВ, исправить её можно один раз
        (дальше правится само исправление), сдвинуть — не дальше чем на сутки."""
        user_id = int(user_id)
        when, kind = self._validate_supervisor_mark_input(at, kind)
        comment = str(comment or '').strip()[:500] or None
        original = None
        if replaces_clockster:
            original = supervisor_hours.mark_key(
                (replaces_clockster or {}).get('at'), (replaces_clockster or {}).get('kind'))
            if original is None or original[1] not in (supervisor_hours.MARK_IN, supervisor_hours.MARK_OUT):
                raise ValueError('Не указана исправляемая отметка Clockster')
            if abs((when - original[0]).total_seconds()) > 24 * 3600:
                raise ValueError('Исправленная отметка — не дальше суток от исходной')
        with self._get_cursor() as cursor:
            if not self._clockster_hours_supervisor_ids_tx(cursor, [user_id]):
                raise PermissionError('Отметки дописываются только супервайзерам с часами по Clockster')
            if original is not None:
                cursor.execute("SELECT name FROM users WHERE id = %s", (user_id,))
                name = (cursor.fetchone() or [None])[0]
                settings = self._supervisor_hours_settings_tx(cursor, [user_id])
                original_day = original[0].date()
                _card, raw, _known = self._supervisor_card_marks_tx(
                    cursor, user_id, name, settings, original_day - timedelta(days=1), original_day + timedelta(days=1))
                if original not in {supervisor_hours.mark_key(m.get('at'), m.get('kind')) for m in raw}:
                    raise ValueError('Исходной отметки нет среди отметок Clockster этого сотрудника')
                cursor.execute("""
                    SELECT 1 FROM supervisor_manual_marks
                    WHERE user_id = %s AND deleted_at IS NULL
                      AND replaces_clockster_at = %s AND replaces_clockster_kind = %s
                """, (user_id, original[0], original[1]))
                if cursor.fetchone():
                    raise ValueError('Эта отметка уже исправлена — измените исправление')
            cursor.execute("""
                SELECT 1 FROM supervisor_manual_marks
                WHERE user_id = %s AND kind = %s AND deleted_at IS NULL
                  AND mark_at >= %s AND mark_at < %s
            """, (user_id, kind, when, when + timedelta(minutes=1)))
            if cursor.fetchone():
                raise ValueError('Такая отметка уже есть')
            cursor.execute("""
                INSERT INTO supervisor_manual_marks
                    (user_id, mark_at, kind, comment, created_by, replaces_clockster_at, replaces_clockster_kind)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (user_id, when, kind, comment, actor_id,
                  original[0] if original else None, original[1] if original else None))
            mark_id = int(cursor.fetchone()[0])
        self._recalculate_supervisor_days(user_id, when, original[0] if original else None)
        return {'id': mark_id, 'user_id': user_id, 'day': when.date().isoformat()}

    def update_supervisor_manual_mark(self, mark_id, at, kind, actor_id=None, comment=None):
        """Изменить ручную отметку. Прежняя строка мягко снимается, новая ссылается
        на неё (replaces_mark_id) и наследует исправляемую отметку Clockster —
        история правок остаётся, а часы пересчитываются за старый и новый день."""
        when, kind = self._validate_supervisor_mark_input(at, kind)
        comment = str(comment or '').strip()[:500] or None
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT user_id, mark_at, replaces_clockster_at, replaces_clockster_kind
                FROM supervisor_manual_marks WHERE id = %s AND deleted_at IS NULL
            """, (int(mark_id),))
            row = cursor.fetchone()
            if not row:
                raise ValueError('Отметка не найдена')
            user_id, old_at, replaces_at, replaces_kind = int(row[0]), row[1], row[2], row[3]
            if replaces_at is not None and abs((when - replaces_at).total_seconds()) > 24 * 3600:
                raise ValueError('Исправленная отметка — не дальше суток от исходной')
            cursor.execute("""
                SELECT 1 FROM supervisor_manual_marks
                WHERE user_id = %s AND kind = %s AND deleted_at IS NULL AND id <> %s
                  AND mark_at >= %s AND mark_at < %s
            """, (user_id, kind, int(mark_id), when, when + timedelta(minutes=1)))
            if cursor.fetchone():
                raise ValueError('Такая отметка уже есть')
            cursor.execute("""
                UPDATE supervisor_manual_marks SET deleted_at = NOW(), deleted_by = %s WHERE id = %s
            """, (actor_id, int(mark_id)))
            cursor.execute("""
                INSERT INTO supervisor_manual_marks
                    (user_id, mark_at, kind, comment, created_by, replaces_mark_id,
                     replaces_clockster_at, replaces_clockster_kind)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (user_id, when, kind, comment, actor_id, int(mark_id), replaces_at, replaces_kind))
            new_id = int(cursor.fetchone()[0])
        self._recalculate_supervisor_days(user_id, when, old_at)
        return {'id': new_id, 'user_id': user_id, 'day': when.date().isoformat()}

    def delete_supervisor_manual_mark(self, mark_id, actor_id=None):
        """Снять ручную отметку (мягко: строка остаётся с автором снятия) и пересчитать
        часы. Снятие исправления возвращает в пары исходную отметку Clockster."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT user_id, mark_at, replaces_clockster_at FROM supervisor_manual_marks
                WHERE id = %s AND deleted_at IS NULL
            """, (int(mark_id),))
            row = cursor.fetchone()
            if not row:
                raise ValueError('Отметка не найдена')
            user_id, mark_at, replaces_at = int(row[0]), row[1], row[2]
            cursor.execute("""
                UPDATE supervisor_manual_marks SET deleted_at = NOW(), deleted_by = %s WHERE id = %s
            """, (actor_id, int(mark_id)))
        self._recalculate_supervisor_days(user_id, mark_at, replaces_at)
        return {'id': int(mark_id), 'user_id': user_id,
                'day': mark_at.astimezone(supervisor_hours.TZ).date().isoformat()}

    def get_supervisor_hours_settings(self, department_ids=None):
        """СВ отделов с часами по Clockster: перерыв, направление, карточка Clockster.

        Плюс справочник карточек Clockster — РОП выбирает из него карточку тому,
        кого ФИО не сшило (у нас «Хасенов», в Clockster «Касенов» — х против к).
        У карточки, которая уже чья-то, есть owner — занятую другому не отдать."""
        today = datetime.now(supervisor_hours.TZ).date()
        params = [list(SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES)]
        department_filter = ''
        if department_ids is not None:
            dept_ids = sorted({int(v) for v in department_ids if v is not None})
            if not dept_ids:
                return {'supervisors': [], 'clockster_cards': [],
                        'default_break_minutes': supervisor_hours.DEFAULT_BREAK_MINUTES}
            department_filter = ' AND u.department_id = ANY(%s)'
            params.append(dept_ids)
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT u.id, u.name, u.department_id FROM users u WHERE "
                + self.SUPERVISOR_ROLES_SQL + " AND " + self.SUPERVISOR_CLOCKSTER_DEPARTMENT_SQL
                + " AND lower(COALESCE(u.status, '')) <> 'fired'" + department_filter
                + " ORDER BY u.name",
                tuple(params),
            )
            people = [(int(row[0]), row[1], row[2]) for row in cursor.fetchall()]
            ids = [row[0] for row in people]
            settings = self._supervisor_hours_settings_tx(cursor, ids)
            directions = self._supervisor_directions_tx(cursor, ids, today, today)
            cards = self._supervisor_clockster_cards_tx(cursor, [(uid, name) for uid, name, _ in people], settings)
            owners = self._clockster_card_owners_tx(cursor)
            cursor.execute("""
                SELECT ext_id, full_name, department_name, position_name
                FROM glb_employees WHERE source = 'clockster' ORDER BY full_name
            """)
            clockster_cards = [
                {'ext_id': row[0], 'full_name': row[1], 'department_name': row[2],
                 'position_name': row[3], 'owner_id': owners.get(row[0])}
                for row in cursor.fetchall()
            ]
        supervisors = []
        for user_id, name, department_id in people:
            entry = settings.get(user_id) or {}
            card = cards.get(user_id)
            if entry.get('no_card'):
                card = {'ext_id': None, 'full_name': None, 'source': 'none'}
            supervisors.append({
                'user_id': user_id,
                'name': name,
                'department_id': department_id,
                'direction': directions.get(user_id),
                'break_minutes': entry.get('break_minutes', supervisor_hours.DEFAULT_BREAK_MINUTES),
                'break_is_default': not entry.get('custom_break'),
                'clockster': card,
            })
        return {
            'supervisors': supervisors,
            'clockster_cards': clockster_cards,
            'default_break_minutes': supervisor_hours.DEFAULT_BREAK_MINUTES,
        }

    def _clockster_card_owners_tx(self, cursor):
        """{карточка Clockster: чей она сейчас} — ручные привязки любых людей плюс
        карточки, сшитые по ФИО с СВ. Отдать занятую карточку другому нельзя:
        одни и те же отметки дали бы часы двоим."""
        cursor.execute(
            "SELECT workpace_ext_id, user_id FROM glb_employee_links WHERE workpace_ext_id LIKE %s AND user_id IS NOT NULL",
            ('clockster:%',),
        )
        owners = {row[0]: int(row[1]) for row in cursor.fetchall()}
        sv_ids = self._clockster_hours_supervisor_ids_tx(cursor)
        if sv_ids:
            cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (sorted(sv_ids),))
            people = [(int(row[0]), row[1]) for row in cursor.fetchall()]
            for user_id, card in self._supervisor_clockster_cards_tx(cursor, people).items():
                if card and card.get('ext_id'):
                    owners.setdefault(card['ext_id'], user_id)
        return owners

    def save_supervisor_hours_settings(self, items, actor_id=None, allowed_user_ids=None):
        """Сохранить перерыв и карточку Clockster СВ пакетом.

        Все строки проверяются ДО первой записи: пакетная ручка, которая
        записала половину и ответила 400, хуже отказа целиком.
        `break_minutes`: пусто — вернуть час по умолчанию.
        `clockster_ext_id`: ключа нет — карточку не трогаем; пусто — снять ручную
        привязку (вернётся автоматическая по ФИО); '__none__' — у СВ карточки нет;
        `clockster:<id>` — привязать. Карточку, которая уже чья-то, отдать нельзя.
        После записи часы этих СВ за текущий месяц пересчитываются."""
        if not isinstance(items, list) or not items:
            raise ValueError('Нет строк для сохранения')
        allowed = None if allowed_user_ids is None else {int(v) for v in allowed_user_ids}
        normalized = []
        with self._get_cursor() as cursor:
            requested_ids = []
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError('Строка настройки должна быть объектом')
                try:
                    requested_ids.append(int(item.get('user_id')))
                except (TypeError, ValueError):
                    raise ValueError('Не указан супервайзер')
            if len(set(requested_ids)) != len(requested_ids):
                raise ValueError('Супервайзер указан дважды')
            eligible = self._clockster_hours_supervisor_ids_tx(cursor, requested_ids)
            cursor.execute("SELECT ext_id FROM glb_employees WHERE source = 'clockster'")
            known_cards = {row[0] for row in cursor.fetchall()}
            owners = self._clockster_card_owners_tx(cursor)
            chosen_cards = set()
            for item, user_id in zip(items, requested_ids):
                if user_id not in eligible or (allowed is not None and user_id not in allowed):
                    raise PermissionError('Нельзя менять настройки этого сотрудника')
                entry = {'user_id': user_id}
                if 'break_minutes' in item:
                    raw_minutes = item.get('break_minutes')
                    entry['break_minutes'] = (
                        None if raw_minutes is None or str(raw_minutes).strip() == ''
                        else supervisor_hours.normalize_break_minutes(raw_minutes)
                    )
                if 'clockster_ext_id' in item:
                    ext_id = str(item.get('clockster_ext_id') or '').strip()
                    if ext_id == self.SUPERVISOR_NO_CLOCKSTER_CARD:
                        entry['clockster'] = 'none'
                    elif ext_id:
                        if ext_id not in known_cards:
                            raise ValueError('Карточка Clockster не найдена')
                        if ext_id in chosen_cards:
                            raise ValueError('Одна карточка Clockster выбрана двум сотрудникам')
                        owner = owners.get(ext_id)
                        if owner is not None and owner != user_id:
                            raise ValueError('Эта карточка Clockster уже привязана к другому сотруднику')
                        chosen_cards.add(ext_id)
                        entry['clockster'] = ext_id
                    else:
                        entry['clockster'] = 'auto'
                normalized.append(entry)

            for entry in normalized:
                user_id = entry['user_id']
                if 'break_minutes' in entry:
                    cursor.execute("""
                        INSERT INTO supervisor_hours_settings (user_id, break_minutes, updated_by, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (user_id) DO UPDATE SET
                            break_minutes = EXCLUDED.break_minutes,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = NOW()
                    """, (user_id, entry['break_minutes'], actor_id))
                if 'clockster' in entry:
                    no_card = entry['clockster'] == 'none'
                    cursor.execute("""
                        INSERT INTO supervisor_hours_settings (user_id, no_clockster_card, updated_by, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (user_id) DO UPDATE SET
                            no_clockster_card = EXCLUDED.no_clockster_card,
                            updated_by = EXCLUDED.updated_by,
                            updated_at = NOW()
                    """, (user_id, no_card, actor_id))
                    cursor.execute(
                        "DELETE FROM glb_employee_links WHERE user_id = %s AND workpace_ext_id LIKE %s",
                        (user_id, 'clockster:%'),
                    )
                    if entry['clockster'] not in ('none', 'auto'):
                        cursor.execute("""
                            INSERT INTO glb_employee_links (workpace_ext_id, user_id, linked_by, linked_at)
                            VALUES (%s, %s, %s, NOW())
                            ON CONFLICT (workpace_ext_id) DO UPDATE SET
                                user_id = EXCLUDED.user_id,
                                linked_by = EXCLUDED.linked_by,
                                linked_at = NOW()
                        """, (entry['clockster'], user_id, actor_id))

        today = datetime.now(supervisor_hours.TZ).date()
        recalc = self.recalculate_supervisor_clockster_hours(
            date(today.year, today.month, 1), today, [entry['user_id'] for entry in normalized])
        return {'saved': len(normalized), 'recalculated': recalc}


    def get_operators_by_supervisor(self, supervisor_id):
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
                    sp.comment as status_period_comment,
                    u.department_id
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
                WHERE u.supervisor_id = %s AND u.role = 'operator'
            """, (supervisor_id,))
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
                    'status_period_comment': row[20] or '',
                    'department_id': row[21]
                } for row in cursor.fetchall()
            ]

    def get_supervisors(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, hours_table_url, role, hire_date, status
                FROM users 
                WHERE LOWER(COALESCE(role, '')) IN ('sv', 'supervisor')
            """)
            return cursor.fetchall()

    def promote_operator_to_supervisor(self, user_id, changed_by=None):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, role, supervisor_id
                FROM users
                WHERE id = %s
                FOR UPDATE
            """, (user_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("User not found")

            target_id, target_name, current_role, current_supervisor_id = row
            role_value = str(current_role or '').lower()

            if role_value == 'sv':
                raise ValueError("User is already a supervisor")
            if role_value != 'operator':
                raise ValueError("Only operators can be promoted to supervisor")

            cursor.execute("""
                UPDATE users
                SET role = 'sv',
                    supervisor_id = NULL
                WHERE id = %s
                RETURNING id, name
            """, (target_id,))
            updated = cursor.fetchone()
            if not updated:
                raise ValueError("Failed to promote user")

            cursor.execute("""
                INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
                VALUES (%s, %s, %s, %s, %s)
            """, (target_id, changed_by, 'role', role_value, 'sv'))

            if current_supervisor_id is not None:
                cursor.execute("""
                    INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
                    VALUES (%s, %s, %s, %s, %s)
                """, (target_id, changed_by, 'supervisor_id', str(current_supervisor_id), None))

            # Deactivate operator profile on promotion
            cursor.execute(
                "UPDATE operator_profiles SET is_active = FALSE, supervisor_id = NULL WHERE user_id = %s",
                (target_id,)
            )

            return {
                "id": int(updated[0]),
                "name": updated[1] or target_name
            }

    def demote_supervisor_to_operator(
        self,
        user_id,
        group_id,
        direction_id=None,
        changed_by=None,
        effective_date=None,
    ):
        """Понижает супервайзера до оператора — обратная операция к
        promote_operator_to_supervisor.

        Образец каскада — archive_group, а НЕ увольнение: увольнение сегодня
        членство в группе не закрывает вовсе, поэтому уволенный СВ так и остаётся
        «активным СВ группы». Здесь членства не удаляем, а закрываем датой, и всё
        делаем одной транзакцией.

        Порядок шагов важен:
          1) закрыть открытые членства СВ (иначе человек остаётся СВ своих групп);
          2) сменить роль;
          3) пересинхронизировать группы, которые он вёл, — их операторы наследуют
             следующего активного СВ или остаются без СВ (осознанный NULL, так же
             поступает archive_group);
          4) только теперь завести человека оператором в группу.
        Если поменять 1 и 4 местами, _group_active_supervisor_id_tx вернёт его же
        самого и он станет собственным супервайзером.

        Группа обязательна: оператор без группы остаётся и без супервайзера, и без
        учёта часов (daily_hours/work_hours пишутся с group_id = NULL).
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, role, supervisor_id, direction_id, department_id, sip_number
                FROM users
                WHERE id = %s
                FOR UPDATE
            """, (int(user_id),))
            row = cursor.fetchone()
            if not row:
                raise ValueError(self.DEMOTE_USER_NOT_FOUND)

            (target_id, target_name, current_role, current_supervisor_id,
             current_direction_id, department_id, current_sip_number) = row
            role_value = str(current_role or '').strip().lower()
            if role_value not in ('sv', 'supervisor'):
                raise ValueError(self.DEMOTE_NOT_A_SUPERVISOR)

            # Права главы отдела роль не смотрят (departments.head_user_id), поэтому
            # понижение главы молча оставило бы оператора с правами админа по отделу.
            cursor.execute("""
                SELECT name FROM departments
                WHERE head_user_id = %s AND is_active
                ORDER BY id
            """, (target_id,))
            headed = [r[0] for r in (cursor.fetchall() or [])]
            if headed:
                raise ValueError(self.DEMOTE_HEADS_DEPARTMENT + ':' + ', '.join(headed))

            # Незакрытый период статуса графика. Пока человек — СВ, массовая
            # синхронизация его не видит (она берёт только role='operator'), но
            # сразу после понижения увидит и перепишет users.status по периоду,
            # причём в историю это ляжет от «Системы» (changed_by = NULL).
            # На проде такой период есть у одного из девяти СВ, и он 'dismissal',
            # то есть человека молча пометило бы уволенным.
            cursor.execute("""
                SELECT status_code
                FROM operator_schedule_status_periods
                WHERE operator_id = %s
                  AND start_date <= COALESCE(%s::date, CURRENT_DATE)
                  AND COALESCE(end_date, DATE '9999-12-31') >= COALESCE(%s::date, CURRENT_DATE)
                ORDER BY start_date DESC, id DESC
                LIMIT 1
            """, (target_id, effective_date, effective_date))
            active_period = cursor.fetchone()
            if active_period:
                status_code = str(active_period[0] or '')
                label = (SCHEDULE_SPECIAL_STATUS_META.get(status_code) or {}).get('label') or status_code
                raise ValueError(self.DEMOTE_ACTIVE_STATUS_PERIOD + ':' + label)

            cursor.execute("""
                SELECT id, department_id, direction_id, status
                FROM groups
                WHERE id = %s
            """, (int(group_id),))
            group_row = cursor.fetchone()
            if not group_row:
                raise ValueError(self.DEMOTE_GROUP_NOT_FOUND)
            _, group_department_id, _, group_status = group_row
            if str(group_status or '').strip().lower() == 'archived':
                raise ValueError(self.DEMOTE_GROUP_ARCHIVED)
            if (department_id is not None and group_department_id is not None
                    and int(group_department_id) != int(department_id)):
                raise ValueError(self.DEMOTE_GROUP_OTHER_DEPARTMENT)

            # Направление оператору обязательно: в проде оно заполнено у всех
            # операторов, и по нему считаются оценки, ЗП и половина отчётов.
            # Без явного выбора — действующее направление группы, как при любом
            # переводе (_sync_operator_direction_from_group_tx). Собственное
            # направление СВ осталось со времён, когда он был оператором, и берётся,
            # только если у группы направления нет.
            resolved_direction_id = direction_id
            if resolved_direction_id in (None, ''):
                resolved_direction_id = (
                    self._group_effective_direction_id_tx(cursor, group_id)
                    or current_direction_id
                )
            if resolved_direction_id in (None, ''):
                raise ValueError(self.DEMOTE_DIRECTION_REQUIRED)
            resolved_direction_id = int(resolved_direction_id)

            # 1) Закрываем членства СВ — датой, как archive_group.
            cursor.execute("""
                SELECT group_id FROM group_supervisor_memberships
                WHERE supervisor_id = %s AND end_date IS NULL
            """, (target_id,))
            led_group_ids = [int(r[0]) for r in (cursor.fetchall() or [])]
            if led_group_ids:
                cursor.execute("""
                    UPDATE group_supervisor_memberships
                    SET end_date = COALESCE(%s::date, CURRENT_DATE)
                    WHERE supervisor_id = %s AND end_date IS NULL
                """, (effective_date, target_id))

            # 2) Роль. SIP-номер снимаем: у СВ он не показывается в «Настройках SIP»
            # (там только operator/trainee), но занятым числится, и за время в СВ
            # его могли выдать другому — два оператора с одним добавочным ломают
            # привязку звонков. Новый номер выдаёт админ осознанно.
            try:
                cursor.execute("""
                    UPDATE users
                    SET role = 'operator',
                        supervisor_id = NULL,
                        direction_id = %s,
                        sip_number = NULL
                    WHERE id = %s
                    RETURNING id, name
                """, (resolved_direction_id, target_id))
            except Exception as exc:
                if 'unique_name_role' in str(exc):
                    raise ValueError(self.DEMOTE_NAME_TAKEN)
                raise
            updated = cursor.fetchone()
            if not updated:
                raise ValueError(self.DEMOTE_USER_NOT_FOUND)

            history_rows = [(target_id, changed_by, 'role', role_value, 'operator')]
            if current_supervisor_id is not None:
                history_rows.append(
                    (target_id, changed_by, 'supervisor_id', str(current_supervisor_id), None)
                )
            if (current_direction_id or None) != resolved_direction_id:
                history_rows.append((
                    target_id, changed_by, 'direction_id',
                    str(current_direction_id) if current_direction_id is not None else None,
                    str(resolved_direction_id),
                ))
            if current_sip_number:
                history_rows.append(
                    (target_id, changed_by, 'sip_number', str(current_sip_number), None)
                )
            cursor.executemany("""
                INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
                VALUES (%s, %s, %s, %s, %s)
            """, history_rows)

            # Профиль оператора: у действующих СВ строки нет вовсе (стартовый
            # бэкофилл заводит её только для operator/trainee), поэтому именно
            # создаём, а не «реактивируем».
            cursor.execute("""
                INSERT INTO operator_profiles (user_id, direction_id, supervisor_id, is_active, rate, sip_number)
                SELECT u.id, u.direction_id, NULL, u.is_active, u.rate, NULL
                FROM users u WHERE u.id = %s
                ON CONFLICT (user_id) DO UPDATE SET
                    direction_id = EXCLUDED.direction_id,
                    supervisor_id = NULL,
                    is_active = EXCLUDED.is_active,
                    rate = EXCLUDED.rate,
                    sip_number = NULL
            """, (target_id,))

            # 3) Группы, которые он вёл, получают следующего активного СВ (или NULL).
            orphaned_group_ids = []
            for led_group_id in led_group_ids:
                if self._sync_group_operators_supervisor_tx(cursor, led_group_id) is None:
                    orphaned_group_ids.append(led_group_id)

            # 4) Теперь он сам — оператор этой группы (его СВ-членство уже закрыто,
            # поэтому собственным супервайзером он стать не может).
            # Направление уже выставлено шагом 2 вместе со своей строкой истории.
            self._add_operator_to_group_tx(
                cursor, group_id, target_id,
                start_date=effective_date, assigned_by=changed_by,
                sync_direction=False,
            )

            cursor.execute("SELECT supervisor_id FROM users WHERE id = %s", (target_id,))
            new_supervisor_row = cursor.fetchone()

            return {
                "id": int(updated[0]),
                "name": updated[1] or target_name,
                "group_id": int(group_id),
                "direction_id": resolved_direction_id,
                "supervisor_id": (
                    int(new_supervisor_row[0])
                    if new_supervisor_row and new_supervisor_row[0] is not None else None
                ),
                "released_group_ids": led_group_ids,
                "orphaned_group_ids": orphaned_group_ids,
                "sip_number_cleared": bool(current_sip_number),
            }

    def _get_supervisor_group_ids_tx(self, cursor, supervisor_id, as_of):
        """Список group_id, которые ведёт СВ на дату as_of (обычно один)."""
        if supervisor_id is None or as_of is None:
            return []
        cursor.execute(
            """
            SELECT DISTINCT gsm.group_id
            FROM group_supervisor_memberships gsm
            WHERE gsm.supervisor_id = %s
              AND gsm.start_date <= %s
              AND (gsm.end_date IS NULL OR gsm.end_date >= %s)
            """,
            (int(supervisor_id), as_of, as_of),
        )
        return [int(r[0]) for r in (cursor.fetchall() or []) if r[0] is not None]

    def _supervisor_has_group_access_for_period_tx(self, cursor, supervisor_id, group_id, start_date, end_date):
        """True, если СВ вёл группу хотя бы часть указанного периода."""
        if supervisor_id is None or group_id is None or start_date is None or end_date is None:
            return False
        cursor.execute(
            """
            SELECT 1
            FROM group_supervisor_memberships gsm
            WHERE gsm.supervisor_id = %s
              AND gsm.group_id = %s
              AND gsm.start_date <= %s
              AND (gsm.end_date IS NULL OR gsm.end_date >= %s)
            LIMIT 1
            """,
            (int(supervisor_id), int(group_id), end_date, start_date),
        )
        return cursor.fetchone() is not None

    def supervisor_has_group_access_for_period(self, supervisor_id, group_id, start_date, end_date=None):
        if end_date is None:
            end_date = start_date
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        with self._get_cursor() as cursor:
            return self._supervisor_has_group_access_for_period_tx(
                cursor, supervisor_id, group_id, start_date, end_date
            )

    def get_supervisor_group_ids(self, supervisor_id, month=None):
        """Публичный: id групп, которые ведёт СВ на конец месяца YYYY-MM (или сегодня)."""
        if month:
            try:
                y, m = map(int, str(month).split('-'))
                as_of = date(y, m, calendar.monthrange(y, m)[1])
            except Exception:
                as_of = date.today()
        else:
            as_of = date.today()
        with self._get_cursor() as cursor:
            return self._get_supervisor_group_ids_tx(cursor, supervisor_id, as_of)

    def _group_active_supervisor_id_tx(self, cursor, group_id):
        """Текущий СВ группы: активное членство (end_date IS NULL); при нескольких —
        назначенный последним. None, если у группы сейчас нет СВ."""
        cursor.execute(
            """
            SELECT gsm.supervisor_id
            FROM group_supervisor_memberships gsm
            WHERE gsm.group_id = %s AND gsm.end_date IS NULL
            ORDER BY gsm.start_date DESC, gsm.id DESC
            LIMIT 1
            """,
            (int(group_id),),
        )
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def _set_operators_supervisor_tx(self, cursor, operator_ids, supervisor_id):
        """users.supervisor_id (+ зеркало в operator_profiles) — legacy-указатель
        «текущий СВ оператора»: выводится из группы, вручную не назначается."""
        ids = [int(oid) for oid in (operator_ids or []) if oid is not None]
        if not ids:
            return
        cursor.execute(
            "UPDATE users SET supervisor_id = %s WHERE id = ANY(%s)",
            (supervisor_id, ids),
        )
        cursor.execute(
            "UPDATE operator_profiles SET supervisor_id = %s WHERE user_id = ANY(%s)",
            (supervisor_id, ids),
        )

    def _sync_group_operators_supervisor_tx(self, cursor, group_id):
        """Проставляет всем активным операторам группы её текущего СВ (сменился
        СВ группы → операторы наследуют нового автоматически)."""
        sv_id = self._group_active_supervisor_id_tx(cursor, group_id)
        cursor.execute(
            """
            SELECT operator_id FROM group_operator_memberships
            WHERE group_id = %s AND end_date IS NULL
            """,
            (int(group_id),),
        )
        self._set_operators_supervisor_tx(
            cursor, [r[0] for r in (cursor.fetchall() or [])], sv_id
        )
        return sv_id

    def get_group_active_supervisor_id(self, group_id):
        """Публичный резолв текущего СВ группы (для создания оператора «в группу»)."""
        with self._get_cursor() as cursor:
            return self._group_active_supervisor_id_tx(cursor, group_id)

    def add_supervisor_to_group(self, group_id, supervisor_id, start_date=None, assigned_by=None):
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM group_supervisor_memberships
                WHERE supervisor_id = %s AND group_id = %s AND end_date IS NULL
                """,
                (int(supervisor_id), int(group_id)),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    """
                    INSERT INTO group_supervisor_memberships
                        (group_id, supervisor_id, start_date, assigned_by, created_at)
                    VALUES (%s, %s, COALESCE(%s::date, CURRENT_DATE), %s, CURRENT_TIMESTAMP)
                    """,
                    (int(group_id), int(supervisor_id), start_date, assigned_by),
                )
            self._sync_group_operators_supervisor_tx(cursor, group_id)

    def remove_supervisor_from_group(self, group_id, supervisor_id, end_date=None):
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE group_supervisor_memberships
                SET end_date = COALESCE(%s::date, CURRENT_DATE)
                WHERE group_id = %s AND supervisor_id = %s AND end_date IS NULL
                """,
                (end_date, int(group_id), int(supervisor_id)),
            )
            self._sync_group_operators_supervisor_tx(cursor, group_id)

    def _group_supervisors_for_month_tx(self, cursor, group_id, start, end):
        """СВ группы, чьё членство пересекает месяц (id + имена). Группа может быть без СВ."""
        cursor.execute("""
            SELECT gsm.supervisor_id, u.name
            FROM group_supervisor_memberships gsm
            JOIN users u ON u.id = gsm.supervisor_id
            WHERE gsm.group_id = %s AND gsm.start_date <= %s
              AND (gsm.end_date IS NULL OR gsm.end_date >= %s)
            ORDER BY gsm.start_date
        """, (int(group_id), end, start))
        ids, names = [], []
        for sv_id, sv_name in cursor.fetchall() or []:
            ids.append(int(sv_id))
            names.append(sv_name)
        return ids, names
