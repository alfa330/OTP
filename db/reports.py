"""База данных: отчёты и выгрузки.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    Alignment,
    Any,
    Border,
    BytesIO,
    CALCULATION_MODEL_CHAT_MANAGER,
    calendar,
    CellRichText,
    date,
    datetime,
    defaultdict,
    Dict,
    dt_time,
    Font,
    get_column_letter,
    InlineFont,
    List,
    logging,
    Optional,
    PatternFill,
    PROXY_STATUS_LABELS,
    re,
    Side,
    Table,
    TableStyleInfo,
    TextBlock,
    timedelta,
    Tuple,
    Workbook,
)


class ReportsMixin:
    def get_admin_feedback_telegram_report_setting(self, user_id: int) -> Optional[bool]:
        """Настройка Excel-отчёта для администратора или главы активного отдела."""
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT COALESCE(ap.feedback_telegram_report_enabled,
                               u.feedback_telegram_report_enabled, FALSE)
                FROM users u
                LEFT JOIN admin_profiles ap ON ap.user_id = u.id
                WHERE u.id = %s
                  AND (
                      LOWER(COALESCE(u.role, '')) IN ('admin', 'super_admin')
                      OR EXISTS (
                          SELECT 1 FROM departments d
                          WHERE d.head_user_id = u.id
                            AND COALESCE(d.is_active, TRUE) = TRUE
                      )
                  )
                LIMIT 1
            """, (user_id_int,))
            row = cursor.fetchone()
            if not row:
                return None
            return bool(row[0])

    def set_admin_feedback_telegram_report_setting(self, user_id: int, enabled: bool) -> Optional[bool]:
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return None

        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET feedback_telegram_report_enabled = %s
                WHERE id = %s
                  AND (
                      LOWER(COALESCE(role, '')) IN ('admin', 'super_admin')
                      OR EXISTS (
                          SELECT 1 FROM departments d
                          WHERE d.head_user_id = users.id
                            AND COALESCE(d.is_active, TRUE) = TRUE
                      )
                  )
                RETURNING COALESCE(feedback_telegram_report_enabled, FALSE)
            """, (bool(enabled), user_id_int))
            row = cursor.fetchone()
            if not row:
                return None
            # Sync to admin_profiles
            cursor.execute("""
                INSERT INTO admin_profiles (user_id, feedback_telegram_report_enabled)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET feedback_telegram_report_enabled = EXCLUDED.feedback_telegram_report_enabled
            """, (user_id_int, bool(enabled)))
            return bool(row[0])

    def get_admins_with_feedback_telegram_reports_enabled(self) -> List[Dict[str, Any]]:
        """Подписанные администраторы (все отделы) и главы (только их отделы)."""
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
                    ) AS headed_department_ids,
                    COALESCE(
                        ARRAY_AGG(DISTINCT d.name ORDER BY d.name) FILTER (WHERE d.name IS NOT NULL),
                        ARRAY[]::VARCHAR[]
                    ) AS headed_department_names
                FROM users u
                LEFT JOIN admin_profiles ap ON ap.user_id = u.id
                LEFT JOIN departments d
                  ON d.head_user_id = u.id
                 AND COALESCE(d.is_active, TRUE) = TRUE
                WHERE u.telegram_id IS NOT NULL
                  AND (
                      LOWER(COALESCE(u.role, '')) IN ('admin', 'super_admin')
                      OR d.id IS NOT NULL
                  )
                  AND COALESCE(ap.feedback_telegram_report_enabled,
                               u.feedback_telegram_report_enabled, FALSE) = TRUE
                GROUP BY u.id, u.name, u.telegram_id, u.role
                ORDER BY u.name
            """)
            rows = cursor.fetchall()

        result = []
        for row in rows:
            role_norm = str(row[3] or '').strip().lower()
            is_admin = role_norm in ('admin', 'super_admin')
            department_ids = [int(value) for value in (row[4] or []) if value is not None]
            department_names = [str(value) for value in (row[5] or []) if value]
            result.append({
                "id": int(row[0]),
                "name": row[1] or "",
                "telegram_id": int(row[2]) if row[2] is not None else None,
                "scope": "global" if is_admin else "department",
                "department_ids": None if is_admin else department_ids,
                "department_names": [] if is_admin else department_names,
                "department_name": None if is_admin else ", ".join(department_names),
            })
        return result

    # ---- Telegram-отчёт о сменах ставок операторов ----

    def get_rate_change_report_setting(self, user_id) -> Optional[bool]:
        """Текущее значение переключателя отчёта о сменах ставок. None если юзер не найден."""
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(rate_change_report_enabled, FALSE) FROM users WHERE id = %s",
                (uid,)
            )
            row = cursor.fetchone()
            return bool(row[0]) if row else None

    def set_rate_change_report_setting(self, user_id, enabled: bool) -> Optional[bool]:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE users SET rate_change_report_enabled = %s
                WHERE id = %s
                RETURNING COALESCE(rate_change_report_enabled, FALSE)
                """,
                (bool(enabled), uid)
            )
            row = cursor.fetchone()
            return bool(row[0]) if row else None

    def get_rate_change_report_recipients(self) -> List[Dict[str, Any]]:
        """Получатели отчёта о сменах ставок.

        - Админы с включённым переключателем -> глобальный отчёт (department_id=None).
        - Главы отделов с включённым переключателем -> отчёт по своему отделу.
        Если пользователь и админ, и глава отдела — оставляем только глобальный.
        """
        recipients = []
        seen_user_ids = set()
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, telegram_id
                FROM users
                WHERE LOWER(COALESCE(role, '')) IN ('admin', 'super_admin')
                  AND telegram_id IS NOT NULL
                  AND COALESCE(rate_change_report_enabled, FALSE) = TRUE
                ORDER BY name
            """)
            for row in cursor.fetchall():
                recipients.append({
                    "id": int(row[0]), "name": row[1] or "",
                    "telegram_id": int(row[2]),
                    "department_id": None, "department_name": None,
                })
                seen_user_ids.add(int(row[0]))

            cursor.execute("""
                SELECT h.id, h.name, h.telegram_id, d.id, d.name
                FROM departments d
                JOIN users h ON h.id = d.head_user_id
                WHERE h.telegram_id IS NOT NULL
                  AND COALESCE(h.rate_change_report_enabled, FALSE) = TRUE
                ORDER BY d.name
            """)
            for row in cursor.fetchall():
                if int(row[0]) in seen_user_ids:
                    continue
                recipients.append({
                    "id": int(row[0]), "name": row[1] or "",
                    "telegram_id": int(row[2]),
                    "department_id": int(row[3]), "department_name": row[4] or "",
                })
        return recipients

    def get_rate_change_report_data(self, window_start, window_end, department_id=None) -> Dict[str, Any]:
        """Данные для отчёта о сменах ставок за окно [window_start, window_end).

        window_start/window_end — naive-datetime в локальном времени Алматы
        (так же хранится user_history.changed_at). Для каждого оператора берём
        итоговый переход за окно (с какой ставки на какую), пропуская net-zero.
        """
        allowed_rates = (1.0, 0.75, 0.5)

        def _snap(raw):
            try:
                v = float(raw)
            except (TypeError, ValueError):
                return None
            return min(allowed_rates, key=lambda a: abs(a - v))

        params = [window_start, window_end]
        dept_clause = ""
        if department_id is not None:
            dept_clause = " AND u.department_id = %s"
            params.append(int(department_id))

        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT uh.user_id, u.name, dep.name, dir.name, sv.name,
                       uh.old_value, uh.new_value, uh.changed_at,
                       uh.changed_by, cb.name
                FROM user_history uh
                JOIN users u ON u.id = uh.user_id
                LEFT JOIN departments dep ON dep.id = u.department_id
                LEFT JOIN directions dir ON dir.id = u.direction_id
                LEFT JOIN users sv ON sv.id = u.supervisor_id
                LEFT JOIN users cb ON cb.id = uh.changed_by
                WHERE uh.field_changed = 'rate'
                  AND uh.changed_at >= %s AND uh.changed_at < %s
                  AND LOWER(COALESCE(u.role, '')) = 'operator'
                  {dept_clause}
                ORDER BY uh.user_id, uh.changed_at, uh.id
            """, params)
            rows = cursor.fetchall()

        by_op = {}
        order = []
        for uid, name, dept, direction, sv, old_v, new_v, changed_at, changed_by, cb_name in rows:
            if uid not in by_op:
                by_op[uid] = {
                    "operator_id": int(uid), "operator_name": name or "",
                    "department_name": dept or "", "direction_name": direction or "",
                    "supervisor_name": sv or "",
                    "first_old": _snap(old_v),
                    "change_count": 0,
                    "last_changed_at": changed_at,
                    "last_changed_by_id": changed_by,
                    "last_changed_by_name": cb_name or "",
                    "self_involved": False,
                }
                order.append(uid)
            rec = by_op[uid]
            rec["last_new"] = _snap(new_v)
            rec["change_count"] += 1
            rec["last_changed_at"] = changed_at
            rec["last_changed_by_id"] = changed_by
            rec["last_changed_by_name"] = cb_name or ""
            if changed_by is not None and int(changed_by) == int(uid):
                rec["self_involved"] = True

        changes = []
        new_rate_dist = {1.0: 0, 0.75: 0, 0.5: 0}
        self_count = 0
        for uid in order:
            rec = by_op[uid]
            old_r = rec.get("first_old")
            new_r = rec.get("last_new")
            if old_r is None or new_r is None or abs(old_r - new_r) < 1e-9:
                continue  # нет реального перехода
            rec["old_rate"] = old_r
            rec["new_rate"] = new_r
            rec["by_self"] = (
                rec["last_changed_by_id"] is not None
                and int(rec["last_changed_by_id"]) == int(uid)
            )
            changes.append(rec)
            if new_r in new_rate_dist:
                new_rate_dist[new_r] += 1
            if rec["self_involved"]:
                self_count += 1

        cur_params = []
        cur_dept = ""
        if department_id is not None:
            cur_dept = " AND department_id = %s"
            cur_params.append(int(department_id))
        current_dist = {1.0: 0, 0.75: 0, 0.5: 0}
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT rate, COUNT(*) FROM users
                WHERE LOWER(COALESCE(role, '')) = 'operator'{cur_dept}
                GROUP BY rate
            """, cur_params)
            for rate, cnt in cursor.fetchall():
                r = _snap(rate)
                if r in current_dist:
                    current_dist[r] += int(cnt)

        return {
            "changes": changes,
            "summary": {
                "changed_count": len(changes),
                "total_change_events": sum(r["change_count"] for r in changes),
                "self_count": self_count,
                "by_other_count": len(changes) - self_count,
                "new_rate_distribution": new_rate_dist,
            },
            "current_distribution": current_dist,
        }

    def get_feedback_sla_report_for_month(
        self,
        month: str,
        reference_date=None,
        department_ids: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        rows = self.get_feedback_sla_rows_for_month(
            month=month,
            department_ids=department_ids,
            reference_date=reference_date
        )

        overview = {
            "total_evaluated": 0,
            "feedback_provided": 0,
            "feedback_on_time": 0,
            "feedback_overdue": 0,
            "pending": 0,
            "pending_overdue": 0,
            "overdue_total": 0
        }

        supervisor_map: Dict[Tuple[int, str], Dict[str, Any]] = {}
        for item in rows:
            overview["total_evaluated"] += 1
            status = str(item.get("status") or '').strip().lower()
            feedback_provided = bool(item.get("feedback_provided"))

            if feedback_provided:
                overview["feedback_provided"] += 1
                if status == 'on_time':
                    overview["feedback_on_time"] += 1
                elif status == 'overdue':
                    overview["feedback_overdue"] += 1
            else:
                overview["pending"] += 1
                if status == 'overdue':
                    overview["pending_overdue"] += 1

            supervisor_id = item.get("supervisor_id")
            supervisor_name = item.get("supervisor_name") or "Без супервайзера"
            key = (int(supervisor_id) if supervisor_id is not None else 0, str(supervisor_name))
            bucket = supervisor_map.setdefault(key, {
                "supervisor_id": key[0] if key[0] != 0 else None,
                "supervisor_name": key[1],
                "total_evaluated": 0,
                "feedback_provided": 0,
                "feedback_on_time": 0,
                "feedback_overdue": 0,
                "pending": 0,
                "pending_overdue": 0,
                "overdue_total": 0,
                "items": [],
                "on_time_items": [],
                "overdue_items": []
            })

            bucket["total_evaluated"] += 1
            if feedback_provided:
                bucket["feedback_provided"] += 1
                if status == 'on_time':
                    bucket["feedback_on_time"] += 1
                elif status == 'overdue':
                    bucket["feedback_overdue"] += 1
            else:
                bucket["pending"] += 1
                if status == 'overdue':
                    bucket["pending_overdue"] += 1

            call_item = {
                "call_id": item.get("call_id"),
                "operator_id": item.get("operator_id"),
                "operator_name": item.get("operator_name"),
                "score": item.get("score"),
                "evaluation_date": item.get("evaluation_date"),
                "deadline_days": item.get("deadline_days"),
                "due_date": item.get("due_date"),
                "feedback_date": item.get("feedback_date"),
                "feedback_provided": feedback_provided,
                "feedback_supervisor_name": item.get("feedback_supervisor_name"),
                "status": status,
                "overdue_days": item.get("overdue_days"),
                "has_critical_error": bool(item.get("has_critical_error"))
            }

            bucket["items"].append(call_item)
            if status == 'on_time' and feedback_provided:
                bucket["on_time_items"].append(call_item)
            elif status == 'overdue':
                bucket["overdue_items"].append(call_item)

        overview["overdue_total"] = int(overview["feedback_overdue"] + overview["pending_overdue"])

        supervisors = list(supervisor_map.values())
        for bucket in supervisors:
            bucket["overdue_total"] = int(bucket["feedback_overdue"] + bucket["pending_overdue"])
            bucket["items"] = sorted(
                bucket["items"],
                key=lambda item: (item.get("evaluation_date") or "", item.get("operator_name") or "")
            )
            bucket["on_time_items"] = sorted(
                bucket["on_time_items"],
                key=lambda item: (item.get("due_date") or "", item.get("operator_name") or "")
            )
            bucket["overdue_items"] = sorted(
                bucket["overdue_items"],
                key=lambda item: (item.get("due_date") or "", item.get("operator_name") or "")
            )

        supervisors.sort(key=lambda item: (str(item.get("supervisor_name") or '').lower(), item.get("supervisor_id") or 0))

        return {
            "month": month,
            "generated_at": datetime.now().isoformat(),
            "overview": overview,
            "supervisors": supervisors
        }

        
    def generate_monthly_report(self, supervisor_id, month=None, current_date=None):
        """
        Генерация месячного отчёта (xlsx).
        Включает:
        - Summary (как раньше);
        - All active (даты dd.mm.yyyy, ФИО с рамкой, ячейки часов >0 — серый B3B3B3);
        - Листы операторов: дата в формате dd.mm.yyyy в объединённой ячейке на день,
        длительности в числовом формате (часы, 2 знака), длительности окрашены в цвет статуса.
        """
        def sanitize_table_name(name):
            sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', (name or ""))
            if not sanitized or (not sanitized[0].isalpha() and sanitized[0] != '_'):
                sanitized = f"_{sanitized}"
            return sanitized[:255]

        def format_hms(duration):
            """Оставлю для совместимости — не используется для числовых полей."""
            if duration is None:
                return "N/A"
            total = int(duration.total_seconds())
            if total < 0:
                return "N/A"
            h = total // 3600
            m = (total % 3600) // 60
            s = total % 60
            return f"{h:02d}:{m:02d}:{s:02d}"

        try:
            if current_date is None:
                current_date = date.today()
            else:
                current_date = datetime.strptime(current_date, "%Y-%m-%d").date() if isinstance(current_date, str) else current_date

            if month is None:
                year = current_date.year
                mon = current_date.month
            else:
                try:
                    year, mon = map(int, month.split('-'))
                    if not (1 <= mon <= 12):
                        raise ValueError("Invalid month")
                except:
                    raise ValueError("Invalid month format. Use YYYY-MM")

            month_str = f"{year}-{mon:02d}"
            filename = f"monthly_report_supervisor_{supervisor_id}_{month_str}.xlsx"

            month_start_date = date(year, mon, 1)
            days_in_month = calendar.monthrange(year, mon)[1]
            month_end = date(year, mon, days_in_month)
            end_date = min(month_end, current_date)

            end_time = datetime.combine(end_date, dt_time(23, 59, 59))

            # Получаем данные
            with self._get_cursor() as cursor:
                cursor.execute("""
                    SELECT id, name
                    FROM users
                    WHERE supervisor_id = %s AND role = 'operator'
                """, (supervisor_id,))
                operators = cursor.fetchall()
                if not operators:
                    raise ValueError("No operators found for the given supervisor")
                operators_dict = {op[0]: op[1] for op in operators}

                cursor.execute("""
                    SELECT o.operator_id, o.change_time, o.is_active
                    FROM operator_activity_logs o
                    JOIN users u ON o.operator_id = u.id
                    WHERE u.supervisor_id = %s AND u.role = 'operator'
                    AND o.change_time >= %s
                    AND o.change_time < %s + INTERVAL '1 DAY'
                    ORDER BY o.operator_id, o.change_time
                """, (supervisor_id, month_start_date, end_date))
                all_logs = cursor.fetchall()

            # Группируем логи и считаем активации/деактивации для summary
            logs_per_op = defaultdict(list)
            counts_per_op = defaultdict(lambda: defaultdict(lambda: {'act': 0, 'deact': 0}))
            total_counts_per_op = defaultdict(lambda: {'act': 0, 'deact': 0})

            for log in all_logs:
                op_id, change_time, is_active = log
                dt = change_time.date()
                logs_per_op[op_id].append({'change_time': change_time, 'is_active': is_active, 'date': dt})
                if is_active == 'active':
                    counts_per_op[op_id][dt]['act'] += 1
                    total_counts_per_op[op_id]['act'] += 1
                else:
                    counts_per_op[op_id][dt]['deact'] += 1
                    total_counts_per_op[op_id]['deact'] += 1

            # Список дат для summary (от 1 числа до end_date)
            dates = [month_start_date + timedelta(days=i) for i in range((end_date - month_start_date).days + 1)]

            # Подготовим переводы статусов и заливки
            # перевод статусов, согласно вашему запросу:
            status_display = {
                'active': 'Активен',
                'tech': 'Перерыв',
                'break': 'Перерыв',
                'training': 'Тренинг',
                'iesigning': 'Подписание',
                'inactive': 'Завершил смену'
            }

            # стили и заливки
            total_fill = PatternFill(start_color="EDEDED", end_color="EDEDED", fill_type="solid")
            status_fill = PatternFill(start_color="F7F7F7", end_color="F7F7F7", fill_type="solid")
            green_fill = PatternFill(start_color="CFF5D0", end_color="CFF5D0", fill_type="solid")   # active
            orange_fill = PatternFill(start_color="FFE8C0", end_color="FFE8C0", fill_type="solid")  # break/tech
            yellow_fill = PatternFill(start_color="FFF9C4", end_color="FFF9C4", fill_type="solid")  # training
            purple_fill = PatternFill(start_color="E8D7F7", end_color="E8D7F7", fill_type="solid")  # tech/alt
            blue_fill = PatternFill(start_color="DCEEFF", end_color="DCEEFF", fill_type="solid")    # iesigning
            inactive_fill = PatternFill(start_color="FFDADA", end_color="FFDADA", fill_type="solid")# inactive
            grey_fill = PatternFill(start_color="B3B3B3", end_color="B3B3B3", fill_type="solid")     # All active >0

            # Соответствие статуса -> заливка (используем явное сопоставление)
            status_fill_map = {
                'active': green_fill,
                'iesigning': blue_fill,
                'break': orange_fill,
                'training': yellow_fill,
                'tech': orange_fill,      # вы просили tech = "Перерыв", поэтому даём тот же цвет что и break
                'inactive': inactive_fill
            }

            # порядок статусов для колонок итогов E..I (исключаем 'inactive')
            status_order = [
                ('active', status_display.get('active', 'active'), status_fill_map.get('active')),
                ('iesigning', status_display.get('iesigning', 'iesigning'), status_fill_map.get('iesigning')),
                ('break', status_display.get('break', 'break'), status_fill_map.get('break')),
                ('training', status_display.get('training', 'training'), status_fill_map.get('training')),
                ('tech', status_display.get('tech', 'tech'), status_fill_map.get('tech')),
            ]

            # --------- НОВЫЙ БЛОК: подсчёт суммарных секунд активности (active + iesigning) по дням для каждого оператора
            active_seconds_per_op_per_day = defaultdict(lambda: defaultdict(int))
            for op_id, logs in logs_per_op.items():
                logs_sorted = sorted(logs, key=lambda x: x['change_time'])
                for i, log in enumerate(logs_sorted):
                    dt = log['date']
                    if i < len(logs_sorted) - 1:
                        next_time = logs_sorted[i + 1]['change_time']
                    else:
                        next_time = end_time
                    duration = next_time - log['change_time']
                    # пропускаем дубликаты состояния
                    if i > 0 and log['is_active'] == logs_sorted[i - 1]['is_active']:
                        continue
                    if log['is_active'] in ('active', 'iesigning'):
                        active_seconds_per_op_per_day[op_id][dt] += int(duration.total_seconds())
            # --------- КОНЕЦ НОВОГО БЛОКА

            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = "Summary"

            # -------------------------
            # 1) Summary: заголовки и данные
            # -------------------------
            ws_summary.cell(1, 1).value = "ФИО"
            for col, dt in enumerate(dates, start=2):
                ws_summary.cell(1, col).value = dt.strftime("%Y-%m-%d")
                ws_summary.cell(1, col).alignment = Alignment(horizontal='center', vertical='center')
            last_date_col = 1 + len(dates)
            ws_summary.cell(1, last_date_col + 1).value = "Итого активаций"
            ws_summary.cell(1, last_date_col + 2).value = "Итого деактиваций"

            # шрифты для rich text (если доступны)
            green_font = InlineFont(color="00AA00") if InlineFont else None
            red_font = InlineFont(color="AA0000") if InlineFont else None
            default_font = InlineFont() if InlineFont else None

            # Заполняем summary
            row = 2
            for op_id, name in operators_dict.items():
                ws_summary.cell(row, 1).value = name
                for col, dt in enumerate(dates, start=2):
                    activations = counts_per_op[op_id][dt]['act']
                    deactivations = counts_per_op[op_id][dt]['deact']
                    cell = ws_summary.cell(row, col)
                    if CellRichText and TextBlock and green_font and red_font:
                        rt = CellRichText([
                            TextBlock(green_font, str(activations)),
                            TextBlock(default_font, " | "),
                            TextBlock(red_font, str(deactivations))
                        ])
                        cell.value = rt
                    else:
                        cell.value = f"{activations} | {deactivations}"
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrapText=True)
                # итоговые колонки
                act_cell = ws_summary.cell(row, last_date_col + 1)
                deact_cell = ws_summary.cell(row, last_date_col + 2)
                if CellRichText and TextBlock and green_font and red_font:
                    act_cell.value = CellRichText([TextBlock(green_font, str(total_counts_per_op[op_id]['act']))])
                    deact_cell.value = CellRichText([TextBlock(red_font, str(total_counts_per_op[op_id]['deact']))])
                else:
                    act_cell.value = total_counts_per_op[op_id]['act']
                    deact_cell.value = total_counts_per_op[op_id]['deact']
                act_cell.alignment = Alignment(horizontal='center', vertical='center')
                deact_cell.alignment = Alignment(horizontal='center', vertical='center')
                row += 1

            # легенда
            ws_summary.cell(row + 1, 1).value = "Легенда:"
            ws_summary.cell(row + 2, 1).value = "Зелёный — активации"
            ws_summary.cell(row + 3, 1).value = "Красный — деактивации"

            # стили summary: жирная шапка, границы, автоширины
            thin_border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
            for c in range(1, last_date_col + 3):
                cell = ws_summary.cell(1, c)
                cell.font = Font(bold=True)
                cell.border = thin_border
            for r in range(1, row):
                for c in range(1, last_date_col + 3):
                    ws_summary.cell(r, c).border = thin_border
            ws_summary.column_dimensions['A'].width = 24
            for idx in range(2, last_date_col + 3):
                col_letter = ws_summary.cell(1, idx).column_letter
                ws_summary.column_dimensions[col_letter].width = 12
            ws_summary.freeze_panes = "A2"

            # -------------------------
            # НОВЫЙ ЛИСТ: All active (суммы часов активности по дням для каждого оператора)
            # -------------------------
            ws_all_active = wb.create_sheet(title="All active")
            # Заголовок: даты в формате dd.mm.yyyy
            ws_all_active.cell(1, 1).value = "ФИО"
            for col, dt in enumerate(dates, start=2):
                ws_all_active.cell(1, col).value = dt.strftime("%d.%m.%Y")
                ws_all_active.cell(1, col).alignment = Alignment(horizontal='center', vertical='center')
            total_col_idx = 1 + len(dates) + 1  # колонка для Итого (ч)
            ws_all_active.cell(1, total_col_idx).value = "Итого (ч)"

            # стиль шапки
            for c in range(1, total_col_idx + 1):
                cell = ws_all_active.cell(1, c)
                cell.font = Font(bold=True)
                cell.border = thin_border

            row = 2
            daily_totals_seconds = defaultdict(int)
            grand_total_seconds = 0

            for op_id in operators_dict.keys():
                name = operators_dict[op_id]
                name_cell = ws_all_active.cell(row, 1)
                name_cell.value = name
                name_cell.border = thin_border  # обводка ФИО
                name_cell.alignment = Alignment(horizontal='left', vertical='center')

                row_total_seconds = 0
                for col_idx, dt in enumerate(dates, start=2):
                    secs = active_seconds_per_op_per_day[op_id].get(dt, 0)
                    hours = secs / 3600.0
                    cell = ws_all_active.cell(row, col_idx)
                    cell.value = round(hours, 2)
                    cell.number_format = '0.00'
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    cell.border = thin_border
                    # если сумма больше 0 — красим в серый
                    if hours > 0:
                        cell.fill = grey_fill

                    row_total_seconds += secs
                    daily_totals_seconds[dt] += secs

                # итого по строке (оператору) в часах
                row_total_hours = row_total_seconds / 3600.0
                total_cell = ws_all_active.cell(row, total_col_idx)
                total_cell.value = round(row_total_hours, 2)
                total_cell.number_format = '0.00'
                total_cell.alignment = Alignment(horizontal='center', vertical='center')
                total_cell.border = thin_border
                if row_total_hours > 0:
                    total_cell.fill = grey_fill

                grand_total_seconds += row_total_seconds
                row += 1

            # Добавим строку итогов по дням (и общий итог)
            total_row = row
            tot_label_cell = ws_all_active.cell(total_row, 1)
            tot_label_cell.value = "Итого"
            tot_label_cell.font = Font(bold=True)
            tot_label_cell.border = thin_border
            tot_label_cell.alignment = Alignment(horizontal='left', vertical='center')

            for col_idx, dt in enumerate(dates, start=2):
                secs = daily_totals_seconds.get(dt, 0)
                hours = secs / 3600.0
                cell = ws_all_active.cell(total_row, col_idx)
                cell.value = round(hours, 2)
                cell.number_format = '0.00'
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border = thin_border
                if hours > 0:
                    cell.fill = grey_fill

            # общий итог всех операторов в часах
            grand_cell = ws_all_active.cell(total_row, total_col_idx)
            grand_cell.value = round(grand_total_seconds / 3600.0, 2)
            grand_cell.font = Font(bold=True)
            grand_cell.number_format = '0.00'
            grand_cell.alignment = Alignment(horizontal='center', vertical='center')
            grand_cell.border = thin_border
            if grand_total_seconds > 0:
                grand_cell.fill = grey_fill

            # автоширины и фиксация панелей
            ws_all_active.column_dimensions['A'].width = 24
            for idx in range(2, total_col_idx + 1):
                col_letter = ws_all_active.cell(1, idx).column_letter
                ws_all_active.column_dimensions[col_letter].width = 12
            ws_all_active.freeze_panes = "A2"

            # -------------------------
            # 2) Листы по операторам: события + одна строка итого на день с колонками итогов
            #    (E..I — ЧАСЫ с 2 знаками, J..N — секунды скрытые)
            # -------------------------
            for op_id, name in operators_dict.items():
                ws = wb.create_sheet(title=(name[:31] or f"op_{op_id}"))

                headers = [
                    "Дата", "Время / Описание", "Статус", "Длительность (ч)",
                    "Итого: Активен", "Итого: Подписание", "Итого: Перерыв", "Итого: Тренинг", "Итого: Тех",
                    "Итого Активен (сек)", "Итого Подписание (сек)", "Итого Перерыв (сек)", "Итого Тренинг (сек)", "Итого Тех (сек)"
                ]
                for col_idx, h in enumerate(headers, start=1):
                    cell = ws.cell(1, col_idx)
                    cell.value = h
                    cell.font = Font(bold=True)
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                    cell.border = thin_border

                for col_letter in ['J', 'K', 'L', 'M', 'N']:
                    ws.column_dimensions[col_letter].hidden = True

                ws.freeze_panes = "A2"
                ws.column_dimensions['A'].width = 12
                ws.column_dimensions['B'].width = 36
                ws.column_dimensions['C'].width = 18
                ws.column_dimensions['D'].width = 14
                ws.column_dimensions['E'].width = 16
                ws.column_dimensions['F'].width = 16
                ws.column_dimensions['G'].width = 14
                ws.column_dimensions['H'].width = 14
                ws.column_dimensions['I'].width = 14

                logs = sorted(logs_per_op[op_id], key=lambda x: x['change_time'])
                current_row = 2
                current_day = None
                day_status_times = defaultdict(timedelta)  # суммарное timedelta по статусам для дня
                day_start_row = None  # для объединения дат

                def write_day_totals_row(r, day_dt, counts_dict, day_status_times_dict):
                    """
                    Строка 'Итого' для оператора за день.
                    A — пустая объединяемая ячейка
                    B — "Итого"
                    C — "В работе" (ярко-зелёная заливка)
                    D — итог рабочих часов (active + iesigning) в часах, 2 знака
                    E..I — часы по статусам (число часов, 2 знака), J..N — секунды
                    Возвращает следующий свободный row (r + 1).

                    ВАЖНО: НЕ присваивает cell.fill = None (openpyxl ожидает объект Fill).
                            Вместо этого fill задаётся только когда нужно.
                    """
                    # fallback на случай отсутствия глобальных стилей
                    try:
                        _ = total_fill
                    except NameError:
                        total_fill = PatternFill(start_color="EDEDED", end_color="EDEDED", fill_type="solid")
                    try:
                        _ = thin_border
                    except NameError:
                        thin_border = Border(left=Side(style='thin'), right=Side(style='thin'),
                                            top=Side(style='thin'), bottom=Side(style='thin'))
                    try:
                        _ = status_order
                    except NameError:
                        # default minimal status_order: (key, name, fill)
                        status_order = [
                            ('active', 'Активен', PatternFill(start_color="CFF5D0", end_color="CFF5D0", fill_type="solid")),
                            ('iesigning', 'Подписание', PatternFill(start_color="DCEEFF", end_color="DCEEFF", fill_type="solid")),
                            ('break', 'Перерыв', PatternFill(start_color="FFE8C0", end_color="FFE8C0", fill_type="solid")),
                            ('training', 'Тренинг', PatternFill(start_color="FFF9C4", end_color="FFF9C4", fill_type="solid")),
                            ('tech', 'Перерыв', PatternFill(start_color="FFE8C0", end_color="FFE8C0", fill_type="solid")),
                        ]

                    # жирная нижняя граница (для визуального раздела)
                    bottom_bold_border = Border(
                        left=Side(style='thin'),
                        right=Side(style='thin'),
                        top=Side(style='thin'),
                        bottom=Side(style='medium')
                    )

                    # насыщённый зелёный цвет для "В работе" и часов
                    strong_green_fill = PatternFill(start_color="66CC66", end_color="66CC66", fill_type="solid")

                    # A — пустая объединяемая ячейка (обычно объединена выше)
                    a_cell = ws.cell(r, 1)
                    a_cell.fill = total_fill
                    a_cell.border = bottom_bold_border
                    a_cell.alignment = Alignment(horizontal='center', vertical='center')

                    # B — "Итого"
                    b_cell = ws.cell(r, 2)
                    b_cell.value = "Итого"
                    b_cell.font = Font(bold=True)
                    b_cell.alignment = Alignment(horizontal='center', vertical='center')
                    b_cell.fill = total_fill
                    b_cell.border = bottom_bold_border

                    # C — "В работе" (ярко-зелёный)
                    c_cell = ws.cell(r, 3)
                    c_cell.value = "В работе"
                    c_cell.font = Font(bold=True)
                    c_cell.alignment = Alignment(horizontal='center', vertical='center')
                    c_cell.fill = strong_green_fill
                    c_cell.border = bottom_bold_border

                    # D — итог рабочих часов = active + iesigning (в часах)
                    active_td = day_status_times_dict.get('active', timedelta(0))
                    iesigning_td = day_status_times_dict.get('iesigning', timedelta(0))
                    total_work_sec = int((active_td + iesigning_td).total_seconds())
                    total_work_hours = round(total_work_sec / 3600.0, 2)

                    d_cell = ws.cell(r, 4)
                    d_cell.value = total_work_hours
                    d_cell.number_format = '0.00'
                    d_cell.alignment = Alignment(horizontal='center', vertical='center')
                    # ставим заливку ТОЛЬКО если > 0
                    if total_work_hours > 0:
                        d_cell.fill = strong_green_fill
                    d_cell.border = bottom_bold_border

                    # E..I (часы по статусам) и J..N (секунды) — заполняем и даём заливку по статусу, если >0
                    for idx, (status_key, status_label, fill) in enumerate(status_order):
                        dur_td = day_status_times_dict.get(status_key, timedelta(0))
                        dur_sec = int(dur_td.total_seconds())
                        dur_hours = round(dur_sec / 3600.0, 2)

                        col_read = 5 + idx   # E..I
                        col_sec = 10 + idx   # J..N

                        # часы (число)
                        rcell = ws.cell(r, col_read)
                        rcell.value = dur_hours
                        rcell.number_format = '0.00'
                        rcell.alignment = Alignment(horizontal='center', vertical='center')
                        # заливаем ТОЛЬКО если >0 и у нас есть валидная заливка
                        if dur_hours > 0 and isinstance(fill, PatternFill):
                            rcell.fill = fill
                        rcell.border = bottom_bold_border

                        # секунды (скрытые колонки) — всегда записываем число
                        scell = ws.cell(r, col_sec)
                        scell.value = dur_sec
                        scell.alignment = Alignment(horizontal='center', vertical='center')
                        scell.border = bottom_bold_border

                    return r + 1


                for i, log in enumerate(logs):
                    dt = log['date']
                    if current_day is None:
                        current_day = dt
                        day_start_row = current_row  # первый ряд для этого дня (включая будущий "Итого")
                    if dt != current_day:
                        # Перед записью итоговой строки — объединяем столбец A для предыдущего дня:
                        if day_start_row is not None:
                            merge_end = current_row  # текущ_row — место где будет написан "Итого"
                            ws.merge_cells(start_row=day_start_row, start_column=1, end_row=merge_end, end_column=1)
                            merged_cell = ws.cell(day_start_row, 1)
                            merged_cell.value = current_day.strftime("%d.%m.%Y")
                            merged_cell.alignment = Alignment(horizontal='center', vertical='center')

                        # записать строку итогов предыдущего дня
                        counts = counts_per_op[op_id][current_day]
                        current_row = write_day_totals_row(current_row, current_day, counts, day_status_times)
                        # сброс
                        day_status_times = defaultdict(timedelta)
                        # переходим к новому дню
                        current_day = dt
                        day_start_row = current_row  # первый ряд нового дня (включая будущий Итого)

                    # next_time
                    if i < len(logs) - 1:
                        next_time = logs[i + 1]['change_time']
                    else:
                        next_time = end_time

                    duration = next_time - log['change_time']

                    # дубликат состояния?
                    is_duplicate = (i > 0 and log['is_active'] == logs[i - 1]['is_active'])
                    if is_duplicate:
                        dur_hours = None
                    else:
                        dur_hours = round(duration.total_seconds() / 3600.0, 2)
                        if log['is_active'] != 'inactive':
                            day_status_times[log['is_active']] += duration

                    # пишем событие (A..D)
                    # A оставляем пустой — объединится позже
                    ws.cell(current_row, 2).value = log['change_time'].strftime("%H:%M:%S")
                    # статус — русская версия
                    status_key = log['is_active']
                    status_rus = status_display.get(status_key, status_key)
                    ws.cell(current_row, 3).value = status_rus

                    # длительность — ЧАСЫ числом с 2 знаками или пусто для дубликата
                    dur_cell = ws.cell(current_row, 4)
                    if dur_hours is None:
                        dur_cell.value = None
                    else:
                        dur_cell.value = dur_hours
                        dur_cell.number_format = '0.00'
                        # окрашиваем длительность в цвет статуса (если сопоставление есть)
                        fill = status_fill_map.get(status_key)
                        if fill is not None:
                            dur_cell.fill = fill

                    # заливка статуса в колонке C (сам статус)
                    cell_stat = ws.cell(current_row, 3)
                    fill_for_cell = status_fill_map.get(status_key)
                    if fill_for_cell is not None:
                        cell_stat.fill = fill_for_cell
                    else:
                        # fallback
                        if status_key == 'inactive':
                            cell_stat.fill = inactive_fill

                    # границы и выравнивание для строки события
                    for col_idx in range(1, 15):
                        ccell = ws.cell(current_row, col_idx)
                        ccell.border = thin_border
                        if col_idx == 4:
                            ccell.alignment = Alignment(horizontal='center', vertical='center')
                        else:
                            ccell.alignment = Alignment(horizontal='left', vertical='center')

                    current_row += 1

                # итоги для последнего дня
                if current_day is not None:
                    if day_start_row is not None:
                        merge_end = current_row  # сюда запишется "Итого"
                        ws.merge_cells(start_row=day_start_row, start_column=1, end_row=merge_end, end_column=1)
                        merged_cell = ws.cell(day_start_row, 1)
                        merged_cell.value = current_day.strftime("%d.%m.%Y")
                        merged_cell.alignment = Alignment(horizontal='center', vertical='center')

                    counts = counts_per_op[op_id][current_day]
                    current_row = write_day_totals_row(current_row, current_day, counts, day_status_times)

                # добавляем таблицу, охватывающую A..N
                if current_row > 2:
                    last_row = current_row - 1
                    sanitized_name = sanitize_table_name(f"{name}_{op_id}")
                    tab_ref = f"A1:N{last_row}"
                    tab_op = Table(displayName=f"Table_{sanitized_name}", ref=tab_ref)
                    style_op = TableStyleInfo(name="TableStyleMedium9", showFirstColumn=False, showRowStripes=True)
                    tab_op.tableStyleInfo = style_op
                    ws.add_table(tab_op)

            # Сохранение в BytesIO
            output = BytesIO()
            wb.save(output)
            output.seek(0)
            content = output.getvalue()
            return filename, content

        except Exception as e:
            logging.error(f"Error generating report: {e}")
            return None, None

    def generate_users_report(
        self,
        current_date=None,
        include_fired: bool = False,
        include_dismissal_details: bool = True,
        sheet_mode: str = 'summary_and_supervisors',
        period_month: str = None,
        department_ids=None,
        supervisor_ids=None
    ):
        """
        Generates an Excel report of operators with extended profile fields:
        base data, contacts, study, corporate data, proxy/SIP, and two close contacts.

        :param current_date: Optional, current date for filename (defaults to today).
        :param include_fired: Include operators with fired/dismissal status.
        :param include_dismissal_details: Add dismissal date/reason/comment columns when fired operators are included.
        :param sheet_mode: summary, supervisors, or summary_and_supervisors.
        :param period_month: Optional 'YYYY-MM'. When set, exports operators who worked during that
            calendar month (employed from hire date up to dismissal date overlapping the month),
            regardless of their current status. Overrides include_fired.
        :param department_ids: Optional iterable of department IDs. None exports all departments;
            an iterable restricts every report sheet to operators from those departments.
        :param supervisor_ids: Optional iterable of supervisor user IDs. None exports every
            supervisor; an iterable restricts the export to their own operators. Combines with
            department_ids (both filters apply).
        :return: (filename, content) or (None, None) on error.
        """
        try:
            if current_date is None:
                current_date = date.today()
            else:
                current_date = datetime.strptime(current_date, "%Y-%m-%d").date() if isinstance(current_date, str) else current_date

            # Optional calculation-month window ("расчетный месяц")
            period_start = None
            period_end = None
            if period_month:
                try:
                    pm = str(period_month).strip()
                    period_start = datetime.strptime(pm + '-01', '%Y-%m-%d').date()
                    last_day = calendar.monthrange(period_start.year, period_start.month)[1]
                    period_end = period_start.replace(day=last_day)
                except (ValueError, TypeError):
                    period_start = None
                    period_end = None

            include_fired = bool(include_fired)
            if period_start and period_end:
                # The month window itself defines who is included, and dismissal dates are
                # meaningful context for that window, so always surface dismissal details.
                include_dismissal_details = True
            else:
                include_dismissal_details = bool(include_dismissal_details) and include_fired
            sheet_mode = str(sheet_mode or 'summary_and_supervisors').strip().lower()
            if sheet_mode not in ('summary', 'supervisors', 'summary_and_supervisors'):
                sheet_mode = 'summary_and_supervisors'

            def _normalize_id_filter(raw_ids):
                if raw_ids is None:
                    return None
                values = raw_ids if isinstance(raw_ids, (list, tuple, set, frozenset)) else [raw_ids]
                return sorted({int(value) for value in values if value not in (None, '')})

            normalized_department_ids = _normalize_id_filter(department_ids)
            normalized_supervisor_ids = _normalize_id_filter(supervisor_ids)

            if period_start and period_end:
                filename = f"users_report_{period_start.strftime('%Y-%m')}.xlsx"
            else:
                filename = f"users_report_{current_date.strftime('%Y-%m-%d')}.xlsx"

            # Fetch operators with all export fields
            with self._get_cursor() as cursor:
                if period_start and period_end:
                    # Keep operators whose employment interval overlaps the selected month.
                    # A closed dismissal may be followed by a return, so an operator also belongs
                    # in the report when that dismissal ended before the month itself ended.
                    status_filter_sql = """
                        AND u.hire_date IS NOT NULL
                        AND u.hire_date <= %s::date
                        AND (
                            dismissal_info.dismissal_start_date IS NULL
                            OR dismissal_info.dismissal_start_date >= %s::date
                            OR (
                                dismissal_info.dismissal_end_date IS NOT NULL
                                AND dismissal_info.dismissal_end_date < %s::date
                            )
                        )
                    """
                else:
                    status_filter_sql = "" if include_fired else """
                        AND COALESCE(NULLIF(LOWER(TRIM(u.status)), ''), 'working') NOT IN ('fired', 'dismissal')
                    """
                department_filter_sql = ""
                supervisor_filter_sql = ""
                query_params = []
                if normalized_department_ids is not None:
                    department_filter_sql = "AND u.department_id = ANY(%s)"
                    query_params.append(normalized_department_ids)
                if normalized_supervisor_ids is not None:
                    supervisor_filter_sql = "AND u.supervisor_id = ANY(%s)"
                    query_params.append(normalized_supervisor_ids)
                if period_start and period_end:
                    query_params.extend([period_end, period_start, period_end])

                users_report_query = f"""
                    SELECT
                        u.name,
                        u.login,
                        u.role,
                        COALESCE(dep.name, 'N/A') as department_name,
                        COALESCE(d.name, 'N/A') as direction,
                        COALESCE(s.name, 'N/A') as supervisor,
                        u.status,
                        u.rate,
                        u.hire_date,
                        u.phone,
                        u.email,
                        u.personal_email,
                        u.instagram,
                        u.telegram_nick,
                        u.study_place,
                        u.study_specialty,
                        u.study_course,
                        u.company_name,
                        u.employment_type,
                        COALESCE(u.has_proxy, FALSE) as has_proxy,
                        u.proxy_card_number,
                        u.proxy_status,
                        COALESCE(u.has_driver_license, FALSE) as has_driver_license,
                        u.sip_number,
                        u.close_contact_1_relation,
                        u.close_contact_1_full_name,
                        u.close_contact_1_phone,
                        u.close_contact_2_relation,
                        u.close_contact_2_full_name,
                        u.close_contact_2_phone,
                        u.card_number,
                        u.city,
                        COALESCE(u.internship_in_company, FALSE) as internship_in_company,
                        COALESCE(u.front_office_training, FALSE) as front_office_training,
                        u.front_office_training_date,
                        u.taxipro_id,
                        u.supervisor_id,
                        u.gender,
                        u.birth_date,
                        dismissal_info.dismissal_start_date,
                        dismissal_info.dismissal_end_date,
                        dismissal_info.dismissal_reason,
                        dismissal_info.dismissal_comment,
                        dismissal_info.dismissal_is_blacklist,
                        status_history.changed_at as status_fired_changed_at
                    FROM users u
                    LEFT JOIN departments dep ON u.department_id = dep.id
                    LEFT JOIN directions d ON u.direction_id = d.id
                    LEFT JOIN users s ON u.supervisor_id = s.id
                    LEFT JOIN LATERAL (
                        SELECT
                            p.start_date AS dismissal_start_date,
                            p.end_date AS dismissal_end_date,
                            p.dismissal_reason,
                            p.comment AS dismissal_comment,
                            COALESCE(p.is_blacklist, FALSE) AS dismissal_is_blacklist
                        FROM operator_schedule_status_periods p
                        WHERE p.operator_id = u.id
                          AND p.status_code = 'dismissal'
                        ORDER BY
                            COALESCE(p.end_date, DATE '9999-12-31') DESC,
                            p.start_date DESC,
                            p.id DESC
                        LIMIT 1
                    ) dismissal_info ON TRUE
                    LEFT JOIN LATERAL (
                        SELECT uh.changed_at
                        FROM user_history uh
                        WHERE uh.user_id = u.id
                          AND uh.field_changed = 'status'
                          AND LOWER(TRIM(COALESCE(uh.new_value, ''))) IN ('fired', 'dismissal')
                        ORDER BY uh.changed_at DESC, uh.id DESC
                        LIMIT 1
                    ) status_history ON TRUE
                    WHERE u.role = 'operator'
                    {department_filter_sql}
                    {supervisor_filter_sql}
                    {status_filter_sql}
                    ORDER BY s.name, u.name
                """
                if query_params:
                    cursor.execute(users_report_query, query_params)
                else:
                    cursor.execute(users_report_query)
                all_operators = cursor.fetchall()
                city_column_index = next(
                    (
                        index
                        for index, column in enumerate(getattr(cursor, 'description', None) or ())
                        if column.name == 'city'
                    ),
                    None
                )

            if not all_operators:
                logging.warning("No operators found for users report")

            # «Город» есть только у отделов, чьи офисы стоят в разных городах
            # (сейчас — «Фронт офисы»). Пустую колонку остальным не добавляем.
            include_city_column = city_column_index is not None and any(
                str(row[city_column_index] or '').strip() for row in all_operators
            )
            
            def _format_date(value):
                if not value:
                    return 'N/A'
                if hasattr(value, 'strftime'):
                    return value.strftime('%Y-%m-%d')
                if isinstance(value, str):
                    normalized = value.strip()
                    if not normalized:
                        return 'N/A'
                    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d-%m-%Y', '%d.%m.%Y'):
                        try:
                            return datetime.strptime(normalized, fmt).strftime('%Y-%m-%d')
                        except ValueError:
                            continue
                    try:
                        return datetime.fromisoformat(normalized).strftime('%Y-%m-%d')
                    except ValueError:
                        return normalized
                return str(value)

            def _format_employment(value):
                normalized = str(value or '').strip().lower()
                if normalized == 'gph':
                    return 'ГПХ'
                if normalized == 'of':
                    return 'ОФ'
                if normalized == 'smz':
                    return 'СМЗ'
                return ''

            def _format_gender(value):
                normalized = str(value or '').strip().lower()
                if normalized == 'male':
                    return 'Мужской'
                if normalized == 'female':
                    return 'Женский'
                return ''

            def _format_proxy(value):
                return 'Да' if bool(value) else 'Нет'

            def _format_proxy_status(value):
                return PROXY_STATUS_LABELS.get(str(value or '').strip().lower(), '')

            def _format_status(value):
                normalized = str(value or '').strip().lower()
                if normalized == 'working':
                    return 'Работает'
                if normalized in ('fired', 'dismissal'):
                    return 'Уволен'
                if normalized in ('bs', 'unpaid_leave'):
                    return 'Б/С'
                if normalized == 'sick_leave':
                    return 'Больничный'
                if normalized == 'annual_leave':
                    return 'Ежегодный отпуск'
                return value or ""

            headers = [
                "ФИО",
                "Пол",
                "Дата рождения",
                "Логин",
                "Роль",
                "Отдел",
                *(["Город"] if include_city_column else []),
                "Направление",
                "Супервайзер",
                "Статус",
                "Ставка",
                "Дата принятия",
                "Номер телефона",
                "Почта",
                "Личный Email",
                "Инстаграм",
                "Ник Telegram",
                "Место учебы",
                "Наименование специальности",
                "Курс",
                "Номер карты",
                "Наименование ТОО/ИП",
                "Оформлен как",
                "Практика в компании",
                "Обучение во фронт офисе",
                "Дата обучения во фронт офисе",
                "ID таксипро",
                "Наличие прокси",
                "Номер прокси карты",
                "Статус прокси",
                "Наличие водительских прав",
                "SIP номер",
                "Близкий 1: Кем приходится",
                "Близкий 1: ФИО",
                "Близкий 1: Номер",
                "Близкий 2: Кем приходится",
                "Близкий 2: ФИО",
                "Близкий 2: Номер"
            ]
            if include_dismissal_details:
                headers.extend([
                    "Дата увольнения",
                    "Дата окончания увольнения",
                    "Причина увольнения",
                    "Комментарий увольнения",
                    "ЧС-увольнение"
                ])

            # Group by supervisor and normalize rows for export
            operators_by_supervisor = defaultdict(list)
            supervisors = {}  # supervisor_id -> name
            summary_rows = []
            for row in all_operators:
                (
                    name, login, role, department_name, direction, supervisor, status, rate, hire_date,
                    phone, email, personal_email, instagram, telegram_nick,
                    study_place, study_specialty, study_course, company_name, employment_type,
                    has_proxy, proxy_card_number, proxy_status, has_driver_license, sip_number,
                    close_contact_1_relation, close_contact_1_full_name, close_contact_1_phone,
                    close_contact_2_relation, close_contact_2_full_name, close_contact_2_phone,
                    card_number, city, internship_in_company, front_office_training, front_office_training_date, taxipro_id, sup_id, gender,
                    birth_date,
                    dismissal_start_date, dismissal_end_date, dismissal_reason, dismissal_comment, dismissal_is_blacklist,
                    status_fired_changed_at
                ) = row

                is_fired_status = str(status or '').strip().lower() in ('fired', 'dismissal')
                dismissal_date_value = dismissal_start_date or status_fired_changed_at
                row_values = [
                    name or "",
                    _format_gender(gender),
                    _format_date(birth_date),
                    login or "",
                    role or "",
                    department_name or "N/A",
                    *([city or ""] if include_city_column else []),
                    direction or "N/A",
                    supervisor or "N/A",
                    _format_status(status),
                    float(rate) if rate else 1.0,
                    _format_date(hire_date),
                    phone or "",
                    email or "",
                    personal_email or "",
                    instagram or "",
                    telegram_nick or "",
                    study_place or "",
                    study_specialty or "",
                    study_course or "",
                    card_number or "",
                    company_name or "",
                    _format_employment(employment_type),
                    _format_proxy(internship_in_company),
                    _format_proxy(front_office_training),
                    _format_date(front_office_training_date),
                    taxipro_id or "",
                    _format_proxy(has_proxy),
                    (proxy_card_number or "") if bool(has_proxy) else "",
                    _format_proxy_status(proxy_status),
                    _format_proxy(has_driver_license),
                    sip_number or "",
                    close_contact_1_relation or "",
                    close_contact_1_full_name or "",
                    close_contact_1_phone or "",
                    close_contact_2_relation or "",
                    close_contact_2_full_name or "",
                    close_contact_2_phone or ""
                ]
                if include_dismissal_details:
                    row_values.extend([
                        _format_date(dismissal_date_value) if is_fired_status else "",
                        _format_date(dismissal_end_date) if is_fired_status and dismissal_end_date else "",
                        (dismissal_reason or "") if is_fired_status else "",
                        (dismissal_comment or "") if is_fired_status else "",
                        _format_proxy(dismissal_is_blacklist) if is_fired_status else ""
                    ])

                summary_rows.append(row_values)
                operators_by_supervisor[sup_id].append(row_values)
                if sup_id and supervisor != 'N/A':
                    supervisors[sup_id] = supervisor
            
            # Create workbook
            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = "Summary" if sheet_mode != 'supervisors' else "Report"

            style = TableStyleInfo(
                name="TableStyleMedium2",
                showFirstColumn=True,
                showLastColumn=False,
                showRowStripes=True,
                showColumnStripes=False
            )

            def _write_rows_to_sheet(ws, rows, table_name):
                # headers
                for col, header in enumerate(headers, start=1):
                    cell = ws.cell(1, col)
                    cell.value = header
                    cell.font = Font(bold=True)

                # data
                for row_idx, row_values in enumerate(rows, start=2):
                    for col_idx, value in enumerate(row_values, start=1):
                        ws.cell(row_idx, col_idx).value = value

                # table
                if rows:
                    last_col = get_column_letter(len(headers))
                    tab = Table(displayName=table_name, ref=f"A1:{last_col}{len(rows) + 1}")
                    tab.tableStyleInfo = style
                    ws.add_table(tab)

                # column widths
                for col_idx in range(1, len(headers) + 1):
                    max_len = len(str(headers[col_idx - 1]))
                    for row_values in rows:
                        value = row_values[col_idx - 1]
                        value_len = len(str(value)) if value is not None else 0
                        if value_len > max_len:
                            max_len = value_len
                    ws.column_dimensions[get_column_letter(col_idx)].width = min(60, max(12, max_len + 2))

            if sheet_mode in ('summary', 'summary_and_supervisors'):
                _write_rows_to_sheet(ws_summary, summary_rows, "SummaryTable")
            
            # Create per-supervisor sheets
            if sheet_mode in ('supervisors', 'summary_and_supervisors'):
                if sheet_mode == 'supervisors':
                    if operators_by_supervisor:
                        wb.remove(ws_summary)
                    else:
                        _write_rows_to_sheet(ws_summary, [], "ReportTable")
                for sup_id, ops in operators_by_supervisor.items():
                    sup_name = supervisors.get(sup_id, "No Supervisor")
                    base_sheet_title = (sup_name or "No Supervisor")[:31]
                    sheet_title = base_sheet_title
                    if sheet_title in wb.sheetnames:
                        suffix_counter = 2
                        while sheet_title in wb.sheetnames:
                            suffix = f"_{suffix_counter}"
                            sheet_title = f"{base_sheet_title[:31 - len(suffix)]}{suffix}"
                            suffix_counter += 1
                    ws = wb.create_sheet(title=sheet_title)
                    table_name = f"Table_sup_{sup_id if sup_id is not None else 'none'}"
                    table_name = re.sub(r'[^A-Za-z0-9_]', '_', table_name)
                    _write_rows_to_sheet(ws, ops, table_name)
            
            # Save to BytesIO
            output = BytesIO()
            wb.save(output)
            output.seek(0)
            content = output.getvalue()
            
            return filename, content
        
        except Exception as e:
            logging.error(f"Error generating users report: {e}")
            return None, None

    def generate_excel_report_from_view(self,
        operators: List[Dict[str, Any]],
        trainings_map: Dict[int, Dict[int, List[Dict[str, Any]]]],
        technical_issues_map: Dict[int, Dict[int, List[Dict[str, Any]]]],
        month: str,  # 'YYYY-MM'
        offline_activities_map: Dict[int, Dict[int, List[Dict[str, Any]]]] = None,
        filename: str = None,
        include_supervisor: bool = False,
        report_group_id: int = None
    ) -> Tuple[str, bytes]:
        """
        Генерирует xlsx с листами: Отработанные часы, Перерыв, Звонки, Эффективность,
        Штрафы, Бонусы, Тренинги, Тех. сбои, Офлайн активность, Без телефона.

        Правила форматирования (по заданию пользователя):
        - Все числовые данные пишем без дополнительного округления.
        - В Excel визуально показываем максимум 2 знака после запятой (через number format), без изменения фактического значения.
        - Внутри таблицы по дням значение 0 отображается как целое 0 (не 0.0).
        - Ячейки по дням, где значение > 0, закрашивать светло-серым (теперь чуть темнее).
        - Calls (количество звонков) остаются целыми числами.
        - Добавить границы ко всем ячейкам таблиц.
        """

        def _normalize_day_items_map(source_map: Any) -> Dict[int, Dict[int, List[Dict[str, Any]]]]:
            normalized: Dict[int, Dict[int, List[Dict[str, Any]]]] = {}
            if not isinstance(source_map, dict):
                return normalized
            for op_key, by_day in source_map.items():
                try:
                    op_id_int = int(op_key)
                except Exception:
                    continue
                if not isinstance(by_day, dict):
                    continue
                op_bucket = normalized.setdefault(op_id_int, {})
                for day_key, items in by_day.items():
                    try:
                        day_int = int(day_key)
                    except Exception:
                        continue
                    if day_int <= 0:
                        continue
                    if isinstance(items, list):
                        day_items = items
                    elif isinstance(items, dict):
                        day_items = [items]
                    else:
                        day_items = []
                    op_bucket.setdefault(day_int, []).extend(day_items)
            return normalized

        trainings_map = _normalize_day_items_map(trainings_map or {})
        technical_issues_map = _normalize_day_items_map(technical_issues_map or {})
        offline_activities_map = _normalize_day_items_map(offline_activities_map or {})
        operators = operators["operators"]

        # If requested, ensure each operator has `supervisor_name` populated.
        if include_supervisor:
            # collect supervisor ids that don't already have a name
            sup_ids = []
            for op in operators:
                if not op.get('supervisor_name') and op.get('supervisor_id'):
                    try:
                        sup_ids.append(int(op.get('supervisor_id')))
                    except Exception:
                        continue
            sup_ids = list(set(sup_ids))
            sup_map = {}
            if sup_ids:
                try:
                    with self._get_cursor() as cursor:
                        cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (sup_ids,))
                        sup_map = {r[0]: r[1] for r in cursor.fetchall()}
                except Exception:
                    logging.exception("Error fetching supervisors for report")
            # fill supervisor_name where missing
            for op in operators:
                if not op.get('supervisor_name') and op.get('supervisor_id'):
                    try:
                        op['supervisor_name'] = sup_map.get(int(op.get('supervisor_id')))
                    except Exception:
                        op['supervisor_name'] = None

        def _num_has_value(value: Any) -> bool:
            try:
                return abs(float(value)) > 0
            except Exception:
                return False

        def _has_any_hours_indicators(op: Dict[str, Any]) -> bool:
            if not isinstance(op, dict):
                return False

            aggregates = op.get('aggregates') if isinstance(op.get('aggregates'), dict) else {}
            for key in (
                'regular_hours',
                'total_break_time',
                'total_talk_time',
                'total_calls',
                'total_efficiency_hours',
                'calls_per_hour',
                'fines',
                'bonuses'
            ):
                if _num_has_value(aggregates.get(key)):
                    return True

            if (
                _num_has_value(op.get('fines')) or
                _num_has_value(op.get('training_hours')) or
                _num_has_value(op.get('technical_issue_hours')) or
                _num_has_value(op.get('offline_activity_hours'))
            ):
                return True

            daily = op.get('daily') if isinstance(op.get('daily'), dict) else {}
            for day_payload in daily.values():
                if not isinstance(day_payload, dict):
                    continue
                for key in ('work_time', 'break_time', 'talk_time', 'calls', 'efficiency', 'fine_amount'):
                    if _num_has_value(day_payload.get(key)):
                        return True
                if str(day_payload.get('fine_reason') or '').strip():
                    return True
                if str(day_payload.get('fine_comment') or '').strip():
                    return True
                if isinstance(day_payload.get('fines'), list) and len(day_payload.get('fines')) > 0:
                    return True
                if isinstance(day_payload.get('bonuses'), list) and len(day_payload.get('bonuses')) > 0:
                    return True

            op_id_int = None
            try:
                op_id_int = int(op.get('operator_id'))
            except Exception:
                op_id_int = None

            if op_id_int is not None:
                for source_map in (trainings_map, technical_issues_map, offline_activities_map):
                    if not isinstance(source_map, dict):
                        continue
                    by_day = source_map.get(op_id_int)
                    if by_day is None:
                        by_day = source_map.get(str(op_id_int))
                    if not isinstance(by_day, dict):
                        continue
                    if any(isinstance(items, list) and len(items) > 0 for items in by_day.values()):
                        return True

            return False

        # Фильтруем уволенных операторов:
        # - оставляем, если увольнение попадает в выбранный месяц
        # - либо если у оператора есть любые показатели за период отчета.
        try:
            year, mon = map(int, month.split('-'))
            month_start = date(year, mon, 1)
            fired_candidates = set()
            for op in operators:
                status_norm = str(op.get('status') or '').strip().lower()
                if status_norm in ('fired', 'dismissal'):
                    try:
                        fired_candidates.add(int(op.get('operator_id')))
                    except Exception:
                        continue

            if fired_candidates:
                with self._get_cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT user_id, changed_at
                        FROM user_history
                        WHERE user_id = ANY(%s)
                          AND field_changed = 'status'
                          AND lower(new_value) IN ('fired', 'dismissal')
                        """,
                        (list(fired_candidates),)
                    )
                    rows = cursor.fetchall()
                allowed_fired_ids = set()
                for r in rows:
                    try:
                        uid = r[0]
                        changed_at = r[1]
                        if changed_at is None:
                            continue
                        ch_date = changed_at.date() if hasattr(changed_at, 'date') else changed_at
                        if ch_date >= month_start:
                            allowed_fired_ids.add(uid)
                    except Exception:
                        continue
            else:
                allowed_fired_ids = set()

            filtered_operators = []
            for op in operators:
                status_norm = str(op.get('status') or '').strip().lower()
                if status_norm not in ('fired', 'dismissal'):
                    filtered_operators.append(op)
                    continue

                op_id_int = None
                try:
                    op_id_int = int(op.get('operator_id'))
                except Exception:
                    op_id_int = None

                keep_by_dismissal_month = bool(op_id_int in allowed_fired_ids) if op_id_int is not None else False
                keep_by_metrics = _has_any_hours_indicators(op)
                if keep_by_dismissal_month or keep_by_metrics:
                    filtered_operators.append(op)

            operators = filtered_operators
        except Exception:
            logging.exception("Error filtering fired operators for report; proceeding without filter")

        FILL_POS = PatternFill(fill_type='solid', start_color='b3b3b3')  # чуть темнее серый
        FILL_FOREIGN = PatternFill(fill_type='solid', start_color='dfe7ef')  # день в ДРУГОЙ группе (перевод)
        THIN = Side(style='thin')
        BORDER_ALL = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

        # Перешедший оператор — ОДНА строка на группу. В отчёте ПО ГРУППЕ (report_group_id задан)
        # дни, когда оператор был в ЭТОЙ группе, считаем и показываем; дни, когда он был в ДРУГОЙ
        # группе, помечаем «др.» заливкой и НЕ учитываем (итоги/тренинги/тех/офлайн) — метрики
        # разных моделей не смешиваются. Дни без членства (до прихода/после ухода) НЕ помечаем.
        # В отчёте по СВ/общем (report_group_id=None) маркировки нет — поведение 1:1 (legacy).
        # _op_seg(op) возвращает «маркер» (op, report_group_id) только если оператор за месяц был
        # ещё и в другой группе (перевод); иначе None. _seg_has_day трактует «свой» день как True.
        def _op_seg(op):
            if report_group_id is None or not isinstance(op, dict):
                return None
            segs = op.get('group_segments')
            if not isinstance(segs, list) or not segs:
                return None
            has_foreign = False
            for s in segs:
                try:
                    gid = int(s.get('group_id'))
                except (TypeError, ValueError):
                    continue
                if gid != int(report_group_id):
                    has_foreign = True
                    break
            return (op, int(report_group_id)) if has_foreign else None

        def _seg_has_day(seg, day):
            # True = «свой» день этой группы ИЛИ день без членства (рендерим обычно);
            # False = день, когда оператор был в ДРУГОЙ группе (помечаем «др.», не считаем).
            if seg is None:
                return True
            op, rgid = seg
            d = int(day)
            for s in (op.get('group_segments') or []):
                try:
                    a, b = int(s['start_day']), int(s['end_day'])
                except (KeyError, TypeError, ValueError):
                    continue
                if a <= d <= b:
                    try:
                        return int(s.get('group_id')) == rgid
                    except (TypeError, ValueError):
                        return True
            return True  # день без членства в группах — не помечаем

        def _weighted_chat_average(op):
            aggregates = op.get('aggregates') if isinstance(op, dict) else None
            aggregate_value = aggregates.get('chat_avg_score') if isinstance(aggregates, dict) else None
            if aggregate_value not in (None, ''):
                try:
                    aggregate_number = float(aggregate_value)
                    if aggregate_number > 0:
                        return aggregate_number
                except (TypeError, ValueError):
                    pass

            daily = op.get('daily') if isinstance(op, dict) else None
            daily = daily if isinstance(daily, dict) else {}
            seg = _op_seg(op)
            score_sum = 0.0
            score_count = 0
            for day_key, day_data in daily.items():
                try:
                    day_number = int(day_key)
                except (TypeError, ValueError):
                    continue
                if not _seg_has_day(seg, day_number) or not isinstance(day_data, dict):
                    continue
                chat_metrics = day_data.get('chat_metrics')
                if not isinstance(chat_metrics, dict):
                    continue
                try:
                    raw_sum = float(chat_metrics.get('score_sum'))
                    raw_count = int(chat_metrics.get('score_count'))
                except (TypeError, ValueError):
                    raw_sum = None
                    raw_count = 0
                if raw_sum is not None and raw_count > 0:
                    score_sum += raw_sum
                    score_count += raw_count
                    continue
                try:
                    legacy_average = float(chat_metrics.get('avg_score'))
                except (TypeError, ValueError):
                    legacy_average = 0.0
                if legacy_average > 0:
                    score_sum += legacy_average
                    score_count += 1
            return (score_sum / score_count) if score_count > 0 else None

        def _op_norm(op):
            # Норма для строки. В отчёте по группе у перешедшего оператора норма
            # пропорциональна дням его сегмента в ЭТОЙ группе (как и раздельный расчёт ЗП),
            # иначе «% выполнения» был бы занижен (норма месячная, а дни — только часть).
            base = float(op.get('norm_hours') or 0)
            seg = _op_seg(op)
            if seg is None or base <= 0:
                return base
            _op, rgid = seg
            own = total = 0
            for s in (op.get('group_segments') or []):
                try:
                    a, b = int(s['start_day']), int(s['end_day'])
                    gid = int(s.get('group_id'))
                except (KeyError, TypeError, ValueError):
                    continue
                span = max(0, b - a + 1)
                total += span
                if gid == rgid:
                    own += span
            return round(base * own / total, 2) if total > 0 else base

        def _make_header(ws, headers: List[str], row_idx: int = 1):
            thin = Side(style='thin')
            border = Border(left=thin, right=thin, top=thin, bottom=thin)
            bold = Font(bold=True)
            for i, h in enumerate(headers, start=1):
                cell = ws.cell(row_idx, i)
                cell.value = h
                cell.font = bold
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border = border

        def parse_time_to_minutes(t: str):
            if not t:
                return None
            try:
                parts = t.strip().split(':')
                hh = int(parts[0])
                mm = int(parts[1]) if len(parts) > 1 else 0
                return hh * 60 + mm
            except Exception:
                return None

        def compute_training_duration_hours(t: Dict[str, Any]) -> float:
            try:
                if t.get('duration_minutes') is not None:
                    return float(t['duration_minutes']) / 60.0
                if t.get('duration_hours') is not None:
                    return float(t['duration_hours'])
                s = parse_time_to_minutes(t.get('start_time'))
                e = parse_time_to_minutes(t.get('end_time'))
                if s is None or e is None:
                    return 0.0
                diff = e - s
                if diff < 0:
                    diff += 24 * 60
                return diff / 60.0
            except Exception:
                return 0.0

        def training_to_interval_minutes(t: Dict[str, Any]):
            s = parse_time_to_minutes((t or {}).get('start_time'))
            e = parse_time_to_minutes((t or {}).get('end_time'))
            if s is None or e is None:
                return None
            if e < s:
                e += 24 * 60
            if e <= s:
                return None
            return (s, e)

        def compute_unique_training_duration_hours(items: List[Dict[str, Any]], counted_only: bool = False) -> float:
            intervals = []
            for item in items or []:
                if counted_only and not item.get('count_in_hours'):
                    continue
                interval = training_to_interval_minutes(item)
                if interval:
                    intervals.append(interval)
            if not intervals:
                return 0.0
            intervals.sort(key=lambda part: (part[0], part[1]))
            merged = []
            for start, end in intervals:
                if not merged or start > merged[-1][1]:
                    merged.append([start, end])
                elif end > merged[-1][1]:
                    merged[-1][1] = end
            return sum(end - start for start, end in merged) / 60.0

        def compute_technical_issue_duration_hours(item: Dict[str, Any]) -> float:
            try:
                if item.get('duration_minutes') is not None:
                    return float(item['duration_minutes']) / 60.0
                if item.get('duration_hours') is not None:
                    return float(item['duration_hours'])
                s = parse_time_to_minutes(item.get('start_time'))
                e = parse_time_to_minutes(item.get('end_time'))
                if s is None or e is None:
                    return 0.0
                diff = e - s
                if diff < 0:
                    diff += 24 * 60
                return diff / 60.0
            except Exception:
                return 0.0

        def compute_offline_activity_duration_hours(item: Dict[str, Any]) -> float:
            try:
                if item.get('duration_minutes') is not None:
                    return float(item['duration_minutes']) / 60.0
                if item.get('duration_hours') is not None:
                    return float(item['duration_hours'])
                s = parse_time_to_minutes(item.get('start_time'))
                e = parse_time_to_minutes(item.get('end_time'))
                if s is None or e is None:
                    return 0.0
                diff = e - s
                if diff < 0:
                    diff += 24 * 60
                return diff / 60.0
            except Exception:
                return 0.0

        def fmt_day_value(metric_key: str, value: Any):
            """Формат для значений в столбцах по дням.
            - calls -> int
            - for hours/perc/eff -> без дополнительного округления; 0 как integer 0
            """
            if metric_key == 'calls':
                try:
                    return int(value or 0)
                except Exception:
                    return 0
            try:
                num = float(value or 0)
            except Exception:
                return 0
            # represent exactly zero as int 0
            if abs(num) < 1e-9:
                return 0
            return num

        def fmt_total_value(metric_key: str, value: Any):
            """Формат для итоговых/доп. колонок (итого, проценты и т.п.).
            - для calls оставляем int
            - для остальных без дополнительного округления
            """
            if value is None:
                return None
            if metric_key == 'calls':
                try:
                    return int(value or 0)
                except Exception:
                    return 0
            try:
                num = float(value)
            except Exception:
                return None
            return num

        logging.info(month)
        year, mon = map(int, month.split('-'))
        days_in_month = calendar.monthrange(year, mon)[1]
        days = list(range(1, days_in_month + 1))

        wb = Workbook()
        default = wb.active
        wb.remove(default)

        FLOAT_DISPLAY_FORMAT = '0.##'
        INTEGER_DISPLAY_FORMAT = '0'

        def _normalize_excel_number(value: Any):
            """
            Avoid showing a dangling decimal separator in Excel for values like 10.0.
            We keep non-integer floats as-is (shown with max 2 fraction digits),
            but convert integer-like floats to int for cleaner display.
            """
            if isinstance(value, float):
                rounded = round(value)
                if abs(value - rounded) < 1e-9:
                    return int(rounded)
            return value

        def set_cell(ws, r, c, value, align_center=True, fill=None):
            cell = ws.cell(r, c)
            normalized_value = _normalize_excel_number(value)
            cell.value = normalized_value
            # Визуально показываем максимум 2 знака после запятой без фактического округления значения.
            if isinstance(normalized_value, float):
                cell.number_format = FLOAT_DISPLAY_FORMAT
            elif isinstance(normalized_value, int) and not isinstance(normalized_value, bool):
                cell.number_format = INTEGER_DISPLAY_FORMAT
            if align_center:
                cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = BORDER_ALL
            if fill:
                cell.fill = fill
            return cell

        def build_generic_sheet(key: str, label: str, metric_key: str, is_hour=True, format_fn=None, extra_cols=None, value_getter=None, aggregate='sum', aggregate_value_getter=None):
            ws = wb.create_sheet(title=label[:31])
            # Build headers with optional supervisor column
            headers = ["Оператор"]
            if include_supervisor:
                headers.append("Супервайзер")
            headers += ["Ставка", "Норма часов (ч)"] + [f"{d:02d}.{mon:02d}" for d in days] + ["Итого"]
            if extra_cols:
                headers += [c[0] for c in extra_cols]
            _make_header(ws, headers)

            # column indexes depending on presence of supervisor column
            rate_col = 2 + (1 if include_supervisor else 0)
            norm_col = rate_col + 1
            day_start_col = norm_col + 1

            row = 2
            for op in operators:
                daily = op.get('daily', {})
                name = op.get('name') or f"op_{op.get('operator_id')}"
                op_id_raw = op.get('operator_id')
                try:
                    op_id_int = int(op_id_raw)
                except Exception:
                    op_id_int = None
                trainings_by_day = trainings_map.get(op_id_int, {}) if op_id_int is not None else {}
                technical_by_day = technical_issues_map.get(op_id_int, {}) if op_id_int is not None else {}
                offline_by_day = offline_activities_map.get(op_id_int, {}) if op_id_int is not None else {}
                set_cell(ws, row, 1, name, align_center=False)
                if include_supervisor:
                    sup_name = op.get('supervisor_name') or ""
                    set_cell(ws, row, 2, sup_name, align_center=False)
                # Ставка и Норма оставляем без изменения/округления
                set_cell(ws, row, rate_col, float(op.get('rate') or 0), align_center=False)
                set_cell(ws, row, norm_col, _op_norm(op), align_center=False)
                total = 0.0
                totals = { 'work_time': 0.0, 'calls': 0, 'efficiency': 0.0 }
                avg_vals = []  # для aggregate='avg' (оценка/время ответа): среднее по непустым дням
                seg = _op_seg(op)
                for c_idx, day in enumerate(days, start=day_start_col):
                    dkey = str(day)
                    if seg is not None and not _seg_has_day(seg, day):
                        set_cell(ws, row, c_idx, "др.", fill=FILL_FOREIGN)
                        continue
                    if metric_key == 'trainings':
                        set_cell(ws, row, c_idx, "")
                    else:
                        d = daily.get(dkey)
                        raw_v = None
                        if d:
                            raw_v = value_getter(d) if value_getter else d.get(metric_key, 0)
                        if aggregate == 'avg':
                            # средние метрики (оценка/время ответа): пустой день — пусто, не 0
                            try:
                                fv = float(raw_v) if (raw_v is not None and raw_v != '') else None
                            except Exception:
                                fv = None
                            if fv is None or fv <= 0:
                                set_cell(ws, row, c_idx, "")
                            else:
                                set_cell(ws, row, c_idx, fmt_day_value(metric_key, fv), fill=FILL_POS)
                                avg_vals.append(fv)
                        else:
                            cell_val = fmt_day_value(metric_key, raw_v)
                            # apply fill if >0
                            fill = FILL_POS if (isinstance(cell_val, (int, float)) and cell_val > 0) else None
                            set_cell(ws, row, c_idx, cell_val, fill=fill)
                            if metric_key == 'calls':
                                totals['calls'] += int(cell_val or 0)
                                total += int(cell_val or 0)
                            else:
                                num_for_tot = float(raw_v or 0)
                                total += num_for_tot
                                if metric_key == 'work_time':
                                    totals['work_time'] += num_for_tot
                                if metric_key == 'efficiency':
                                    totals['efficiency'] += num_for_tot
                # total cell
                total_col = day_start_col + len(days)
                if aggregate == 'avg':
                    if aggregate_value_getter:
                        try:
                            avg_total = aggregate_value_getter(op)
                        except Exception:
                            avg_total = None
                    else:
                        avg_total = (sum(avg_vals) / len(avg_vals)) if avg_vals else None
                    set_cell(ws, row, total_col, (fmt_total_value(metric_key, avg_total) if avg_total is not None else ""))
                else:
                    set_cell(ws, row, total_col, fmt_total_value(metric_key, total))

                # extra cols
                if extra_cols:
                    for i, (_, fn) in enumerate(extra_cols, start=1):
                        val = fn(op, totals)
                        # if fn returns percent-string already, keep
                        if isinstance(val, str):
                            set_cell(ws, row, total_col + i, val)
                        elif metric_key == 'calls':
                            if val is None:
                                set_cell(ws, row, total_col + i, None)
                            else:
                                try:
                                    set_cell(ws, row, total_col + i, float(val))
                                except Exception:
                                    set_cell(ws, row, total_col + i, None)
                        else:
                            set_cell(ws, row, total_col + i, fmt_total_value(metric_key, val))

                row += 1

            ws.column_dimensions['A'].width = 24
            for i in range(2, len(headers) + 1):
                col = ws.cell(1, i).column_letter
                ws.column_dimensions[col].width = 12

        def build_work_time_sheet():
            ws = wb.create_sheet(title='Отработанные часы'[:31])
            headers = ["Оператор"]
            if include_supervisor:
                headers.append("Супервайзер")
            headers += ["Ставка", "Норма часов (ч)"] + [f"{d:02d}.{mon:02d}" for d in days] + ["Итого часов", "База часов", "Тех. сбои (ч)", "Тренинги (ч)", "Офлайн активность (ч)", "Вып нормы (%)", "Выработка"]
            _make_header(ws, headers)
            row = 2
            for op in operators:
                daily = op.get('daily', {})
                name = op.get('name') or f"op_{op.get('operator_id')}"
                op_id_raw = op.get('operator_id')
                try:
                    op_id_int = int(op_id_raw)
                except Exception:
                    op_id_int = None
                trainings_by_day = trainings_map.get(op_id_int, {}) if op_id_int is not None else {}
                technical_by_day = technical_issues_map.get(op_id_int, {}) if op_id_int is not None else {}
                offline_by_day = offline_activities_map.get(op_id_int, {}) if op_id_int is not None else {}
                set_cell(ws, row, 1, name, align_center=False)
                if include_supervisor:
                    sup_name = op.get('supervisor_name') or ""
                    set_cell(ws, row, 2, sup_name, align_center=False)
                rate_col = 2 + (1 if include_supervisor else 0)
                norm_col = rate_col + 1
                set_cell(ws, row, rate_col, float(op.get('rate') or 0), align_center=False)
                norm = _op_norm(op)
                set_cell(ws, row, norm_col, norm, align_center=False)

                total_work = 0.0
                total_counted_trainings = 0.0
                total_technical_issues = 0.0
                total_offline_activities = 0.0

                day_start = norm_col + 1
                seg = _op_seg(op)
                for c_idx, day in enumerate(days, start=day_start):
                    dkey = str(day)
                    if seg is not None and not _seg_has_day(seg, day):
                        set_cell(ws, row, c_idx, "др.", fill=FILL_FOREIGN)
                        continue
                    work_val = 0.0
                    d = daily.get(dkey)
                    if d:
                        work_val = float(d.get('work_time') or 0.0)
                    # Рассчитываем зачётные часы тренинга для дня и добавляем их к дневному показателю
                    trainings_for_day = trainings_by_day.get(day, []) if trainings_by_day else []
                    counted_for_day = compute_unique_training_duration_hours(trainings_for_day, counted_only=True)

                    technical_for_day = 0.0
                    technical_items_for_day = technical_by_day.get(day, []) if technical_by_day else []
                    for item in technical_items_for_day:
                        technical_for_day += compute_technical_issue_duration_hours(item)

                    offline_for_day = 0.0
                    offline_items_for_day = offline_by_day.get(day, []) if offline_by_day else []
                    for item in offline_items_for_day:
                        offline_for_day += compute_offline_activity_duration_hours(item)

                    # Сохраняем в суммарные показатели отдельно, итоговая ячейка по дню — work + trainings + technical + offline
                    total_work += work_val
                    total_counted_trainings += counted_for_day
                    total_technical_issues += technical_for_day
                    total_offline_activities += offline_for_day
                    combined = work_val + counted_for_day + technical_for_day + offline_for_day
                    cell_val = fmt_day_value('work_time', combined)
                    fill = FILL_POS if (isinstance(cell_val, (int, float)) and cell_val > 0) else None
                    set_cell(ws, row, c_idx, cell_val, fill=fill)

                itogo_chasov = total_work + total_counted_trainings + total_technical_issues + total_offline_activities
                base_total_col = day_start + len(days)
                set_cell(ws, row, base_total_col, fmt_total_value('work_time', itogo_chasov))
                set_cell(ws, row, base_total_col + 1, fmt_total_value('work_time', total_work))
                set_cell(ws, row, base_total_col + 2, fmt_total_value('work_time', total_technical_issues))
                set_cell(ws, row, base_total_col + 3, fmt_total_value('work_time', total_counted_trainings))
                set_cell(ws, row, base_total_col + 4, fmt_total_value('work_time', total_offline_activities))
                if norm and norm != 0:
                    percent_display = (itogo_chasov / norm) * 100
                else:
                    percent_display = None
                set_cell(ws, row, base_total_col + 5, percent_display)
                set_cell(ws, row, base_total_col + 6, fmt_total_value('work_time', (norm - itogo_chasov) if norm is not None else None))

                row += 1

            ws.column_dimensions['A'].width = 24
            for i in range(2, len(headers) + 1):
                col = ws.cell(1, i).column_letter
                ws.column_dimensions[col].width = 14

        def build_calls_sheet():
            def kvz_fn(op, totals):
                seg = _op_seg(op)
                work_hours = 0.0
                for _dk, dnum in (op.get('daily', {}) or {}).items():
                    if seg is not None:
                        try:
                            if not _seg_has_day(seg, int(_dk)):
                                continue
                        except Exception:
                            pass
                    work_hours += float(dnum.get('work_time') or 0.0)
                calls = totals.get('calls', 0)
                effective_hours = max(0.0, float(work_hours))
                if effective_hours > 0:
                    return calls / effective_hours
                return None

            build_generic_sheet('calls', 'Звонки', 'calls', is_hour=False, extra_cols=[('КВЗ', kvz_fn)])

        def build_efficiency_sheet():
            def otn_fn(op, totals):
                seg = _op_seg(op)
                sum_work = 0.0
                sum_eff = totals.get('efficiency', 0.0)
                for _dk, dnum in (op.get('daily', {}) or {}).items():
                    if seg is not None:
                        try:
                            if not _seg_has_day(seg, int(_dk)):
                                continue
                        except Exception:
                            pass
                    sum_work += float(dnum.get('work_time') or 0.0)
                if sum_work and sum_work != 0:
                    return (sum_eff / sum_work) * 100
                return None

            build_generic_sheet('efficiency', 'Эффективность', 'efficiency', is_hour=True, extra_cols=[('Отн.', otn_fn)])

        def build_fines_sheet():
            ws_f = wb.create_sheet(title='Штрафы'[:31])
            headers = ["ФИО"]
            if include_supervisor:
                headers.append("Супервайзер")
            headers += ["Ставка"] + [f"{d:02d}.{mon:02d}" for d in days] + ["Итого"]
            _make_header(ws_f, headers)

            reason_colors = {
                'Опоздание': 'EA580C',
                'Корп такси': '2563EB',
                'Не выход': 'DC2626',
                'Прокси карта': '7C3AED',
                'Другое': '047857',
            }
            dynamic_reason_colors = {}
            dynamic_palette = ['0F766E', 'BE123C', '4338CA', 'A16207', '0369A1', '86198F', 'B45309']
            known_reason_order = ['Опоздание', 'Корп такси', 'Не выход', 'Прокси карта', 'Другое']
            used_reasons = set()
            calculations_by_reason = defaultdict(set)
            totals_by_day = defaultdict(float)
            operator_summaries = []

            rich_separator_font = InlineFont(color='6B7280')
            rate_col = 2 + (1 if include_supervisor else 0)
            day_start_col = rate_col + 1

            def fmt_amt(x):
                try:
                    amount = float(x or 0.0)
                except Exception:
                    amount = 0.0
                if abs(amount - round(amount)) < 1e-9:
                    return f"{int(round(amount)):,}".replace(",", " ")
                return f"{amount:,.2f}".rstrip('0').rstrip('.').replace(",", " ").replace(".", ",")

            def fine_amount(fine):
                if not isinstance(fine, dict):
                    return 0.0
                try:
                    return float(fine.get('amount', fine.get('fine_amount', 0)) or 0.0)
                except Exception:
                    return 0.0

            def fine_reason(fine):
                if not isinstance(fine, dict):
                    return 'Другое'
                return str(fine.get('reason') or fine.get('fine_reason') or 'Другое').strip() or 'Другое'

            def fine_minutes(fine, amount):
                if not isinstance(fine, dict):
                    return 0.0
                minutes = fine.get('minutes')
                try:
                    if minutes not in (None, ''):
                        return max(0.0, float(minutes))
                except Exception:
                    pass
                try:
                    return max(0.0, float(amount or 0.0) / 50.0)
                except Exception:
                    return 0.0

            def reason_color(reason):
                if reason in reason_colors:
                    return reason_colors[reason]
                if reason not in dynamic_reason_colors:
                    dynamic_reason_colors[reason] = dynamic_palette[len(dynamic_reason_colors) % len(dynamic_palette)]
                return dynamic_reason_colors[reason]

            def fmt_calc_number(value):
                try:
                    number = float(value)
                except Exception:
                    return None
                if abs(number - round(number)) < 1e-9:
                    return str(int(round(number)))
                return f"{number:.2f}".rstrip('0').rstrip('.').replace(".", ",")

            def fine_calculation_text(fine, reason, amount):
                if isinstance(fine, dict):
                    explicit_calc = (
                        fine.get('calculation')
                        or fine.get('calculation_text')
                        or fine.get('calculationText')
                        or fine.get('calc')
                        or fine.get('formula')
                    )
                    if explicit_calc:
                        return str(explicit_calc).strip()

                reason_lc = reason.lower()
                if reason_lc == 'опоздание':
                    minutes = fine.get('minutes') if isinstance(fine, dict) else None
                    if minutes in (None, '') and amount:
                        minutes = amount / 50.0
                    minutes_text = fmt_calc_number(minutes)
                    return f"{minutes_text} мин x 50" if minutes_text is not None else "минуты x 50"
                if reason_lc == 'не выход' or 'не выход' in reason_lc:
                    return "фикс. 10 000"
                if 'прокси' in reason_lc:
                    return "фикс. 5 000"
                return ""

            def legend_calculation_text(reason):
                reason_lc = reason.lower()
                if reason_lc == 'опоздание':
                    return "минуты x 50"
                if reason_lc == 'не выход' or 'не выход' in reason_lc:
                    return "фикс. 10 000"
                if 'прокси' in reason_lc:
                    return "фикс. 5 000"
                custom_calculations = calculations_by_reason.get(reason) or set()
                return "; ".join(sorted(custom_calculations))

            def extract_day_fines(day_entry):
                if not isinstance(day_entry, dict):
                    return []

                fines_list = []
                if isinstance(day_entry.get('fines'), list):
                    fines_list = [fine for fine in day_entry.get('fines') if isinstance(fine, dict)]

                if fines_list:
                    return fines_list

                legacy_amount = day_entry.get('fine_amount')
                legacy_reason = day_entry.get('fine_reason')
                legacy_comment = day_entry.get('fine_comment')
                has_legacy_fine = False
                try:
                    has_legacy_fine = abs(float(legacy_amount or 0.0)) > 0
                except Exception:
                    has_legacy_fine = False
                has_legacy_fine = has_legacy_fine or bool(str(legacy_reason or '').strip()) or bool(str(legacy_comment or '').strip())
                if not has_legacy_fine:
                    return []

                fine = {
                    'amount': legacy_amount or 0.0,
                    'reason': legacy_reason or 'Другое',
                    'comment': legacy_comment,
                }
                try:
                    if str(fine['reason']) == 'Опоздание':
                        fine['minutes'] = int(round(float(fine['amount'] or 0.0) / 50.0)) if fine['amount'] else 0
                except Exception:
                    pass
                return [fine]

            def fine_item_text(fine):
                amount = fine_amount(fine)
                reason = fine_reason(fine)
                calculation = fine_calculation_text(fine, reason, amount)
                if calculation:
                    calculations_by_reason[reason].add(calculation)
                return fmt_amt(amount)

            def set_rich_cell(ws, r, c, parts):
                cell = ws.cell(r, c)
                cell.value = CellRichText(parts)
                cell.alignment = Alignment(horizontal='center', vertical='center', wrapText=True)
                cell.border = BORDER_ALL
                return cell

            def set_fines_cell(ws, r, c, fines_list):
                parts = []
                for idx, fine in enumerate(fines_list):
                    reason = fine_reason(fine)
                    used_reasons.add(reason)
                    if idx > 0:
                        parts.append(TextBlock(rich_separator_font, ", "))
                    parts.append(TextBlock(InlineFont(color=reason_color(reason), b=True), fine_item_text(fine)))
                return set_rich_cell(ws, r, c, parts)

            row = 2
            for op in operators:
                name = op.get('name') or f"op_{op.get('operator_id')}"
                name_cell = set_cell(ws_f, row, 1, name, align_center=False)
                name_cell.font = Font(bold=True)

                col_idx = 2
                if include_supervisor:
                    sup_name = op.get('supervisor_name') or ""
                    set_cell(ws_f, row, col_idx, sup_name, align_center=False)
                    col_idx += 1

                rate = op.get('rate') or 0
                rate_cell = set_cell(ws_f, row, col_idx, float(rate), align_center=False)
                rate_cell.font = Font(bold=True)

                fines_map = op.get('daily', {})
                seg = _op_seg(op)
                row_total = 0.0
                row_has_fines = False
                summary = {
                    'name': name,
                    'supervisor_name': op.get('supervisor_name') or "",
                    'rate': float(rate),
                    'late_count': 0,
                    'late_minutes': 0.0,
                    'late_amount': 0.0,
                    'taxi_count': 0,
                    'taxi_amount': 0.0,
                    'no_show_count': 0,
                    'no_show_amount': 0.0,
                    'proxy_count': 0,
                    'proxy_amount': 0.0,
                    'other_count': 0,
                    'other_amount': 0.0,
                    'fine_count': 0,
                    'total_amount': 0.0,
                }

                for day_idx, day in enumerate(days, start=day_start_col):
                    if seg is not None:
                        try:
                            if not _seg_has_day(seg, int(day)):
                                set_cell(ws_f, row, day_idx, "др.", fill=FILL_FOREIGN)
                                continue
                        except Exception:
                            pass
                    day_entry = fines_map.get(str(day)) if isinstance(fines_map, dict) else None
                    if day_entry is None and isinstance(fines_map, dict):
                        day_entry = fines_map.get(day)
                    fines_list = extract_day_fines(day_entry)
                    if not fines_list:
                        set_cell(ws_f, row, day_idx, "")
                        continue

                    day_total = sum(fine_amount(fine) for fine in fines_list)
                    row_total += day_total
                    totals_by_day[day] += day_total
                    row_has_fines = True
                    for fine in fines_list:
                        amount = fine_amount(fine)
                        reason_lc = fine_reason(fine).lower()
                        summary['fine_count'] += 1
                        summary['total_amount'] += amount
                        if reason_lc == 'опоздание':
                            summary['late_count'] += 1
                            summary['late_minutes'] += fine_minutes(fine, amount)
                            summary['late_amount'] += amount
                        elif 'корп' in reason_lc and 'такси' in reason_lc:
                            summary['taxi_count'] += 1
                            summary['taxi_amount'] += amount
                        elif reason_lc == 'не выход' or 'не выход' in reason_lc:
                            summary['no_show_count'] += 1
                            summary['no_show_amount'] += amount
                        elif 'прокси' in reason_lc:
                            summary['proxy_count'] += 1
                            summary['proxy_amount'] += amount
                        else:
                            # «Другое» и любые новые причины не теряются в сводке.
                            summary['other_count'] += 1
                            summary['other_amount'] += amount
                    set_fines_cell(ws_f, row, day_idx, fines_list)

                total_col = day_start_col + len(days)
                total_cell = set_cell(ws_f, row, total_col, row_total)
                total_cell.font = Font(bold=True)
                if row_total:
                    total_cell.fill = FILL_POS
                if row_has_fines:
                    ws_f.row_dimensions[row].height = 42

                operator_summaries.append(summary)
                row += 1

            total_row = row
            total_label_cell = set_cell(ws_f, total_row, 1, "Итого", align_center=False)
            total_label_cell.font = Font(bold=True)
            for empty_col in range(2, day_start_col):
                empty_cell = set_cell(ws_f, total_row, empty_col, "")
                empty_cell.font = Font(bold=True)
            grand_total = 0.0
            for day_idx, day in enumerate(days, start=day_start_col):
                day_total = totals_by_day.get(day, 0.0)
                grand_total += day_total
                cell = set_cell(ws_f, total_row, day_idx, day_total)
                cell.font = Font(bold=True)
                if day_total:
                    cell.fill = FILL_POS
            grand_cell = set_cell(ws_f, total_row, day_start_col + len(days), grand_total)
            grand_cell.font = Font(bold=True)
            if grand_total:
                grand_cell.fill = FILL_POS

            legend_start = total_row + 2
            legend_title = ws_f.cell(legend_start, 1)
            legend_title.value = "Легенда причин"
            legend_title.font = Font(bold=True)

            legend_header_row = legend_start + 1
            set_cell(ws_f, legend_header_row, 1, "Причина", align_center=False).font = Font(bold=True)
            set_cell(ws_f, legend_header_row, 2, "Расчет", align_center=False).font = Font(bold=True)

            legend_reasons = [reason for reason in known_reason_order if reason in used_reasons or reason in reason_colors]
            legend_reasons += sorted(reason for reason in used_reasons if reason not in legend_reasons)
            for offset, reason in enumerate(legend_reasons, start=1):
                legend_row = legend_header_row + offset
                reason_cell = set_cell(ws_f, legend_row, 1, reason, align_center=False)
                reason_cell.font = Font(color=reason_color(reason), bold=True)
                set_cell(ws_f, legend_row, 2, legend_calculation_text(reason), align_center=False)

            summary_title_row = legend_header_row + len(legend_reasons) + 2
            summary_title = ws_f.cell(summary_title_row, 1)
            summary_title.value = "Сводка по операторам"
            summary_title.font = Font(bold=True, size=12)

            summary_headers = ["ФИО"]
            if include_supervisor:
                summary_headers.append("Супервайзер")
            summary_headers += [
                "Ставка",
                "Опоздание, кол-во",
                "Опоздание, минуты",
                "Опоздание, сумма",
                "Корп такси, кол-во",
                "Корп такси, сумма",
                "Не выход, кол-во",
                "Не выход, сумма",
                "Прокси карта, кол-во",
                "Прокси карта, сумма",
                "Другое, кол-во",
                "Другое, сумма",
                "Всего штрафов",
                "Итого",
            ]
            summary_header_row = summary_title_row + 1
            _make_header(ws_f, summary_headers, row_idx=summary_header_row)
            for header_col in range(1, len(summary_headers) + 1):
                ws_f.cell(summary_header_row, header_col).alignment = Alignment(
                    horizontal='center', vertical='center', wrapText=True
                )
            ws_f.row_dimensions[summary_header_row].height = 42

            summary_keys = [
                'late_count', 'late_minutes', 'late_amount',
                'taxi_count', 'taxi_amount',
                'no_show_count', 'no_show_amount',
                'proxy_count', 'proxy_amount',
                'other_count', 'other_amount',
                'fine_count', 'total_amount',
            ]
            summary_row = summary_header_row + 1
            for summary in operator_summaries:
                values = [summary['name']]
                if include_supervisor:
                    values.append(summary['supervisor_name'])
                values.append(summary['rate'])
                values.extend(summary[key] for key in summary_keys)
                for col_idx, value in enumerate(values, start=1):
                    cell = set_cell(
                        ws_f,
                        summary_row,
                        col_idx,
                        value,
                        align_center=(col_idx > (2 if include_supervisor else 1)),
                    )
                    if col_idx == 1:
                        cell.font = Font(bold=True)
                total_cell = ws_f.cell(summary_row, len(summary_headers))
                total_cell.font = Font(bold=True)
                if summary['total_amount']:
                    total_cell.fill = FILL_POS
                summary_row += 1

            summary_total_values = ["Итого"]
            if include_supervisor:
                summary_total_values.append("")
            summary_total_values.append("")
            summary_total_values.extend(
                sum(float(summary[key] or 0.0) for summary in operator_summaries)
                for key in summary_keys
            )
            for col_idx, value in enumerate(summary_total_values, start=1):
                cell = set_cell(ws_f, summary_row, col_idx, value, align_center=(col_idx > 1))
                cell.font = Font(bold=True)
            if grand_total:
                ws_f.cell(summary_row, len(summary_headers)).fill = FILL_POS

            ws_f.column_dimensions['A'].width = 28
            if include_supervisor:
                ws_f.column_dimensions['B'].width = 22
            for i in range(2, len(headers) + 1):
                col = ws_f.cell(1, i).column_letter
                if i == rate_col:
                    ws_f.column_dimensions[col].width = 12
                elif day_start_col <= i < day_start_col + len(days):
                    ws_f.column_dimensions[col].width = 24
                else:
                    ws_f.column_dimensions[col].width = 14
            ws_f.freeze_panes = f"{get_column_letter(day_start_col)}2"

        def build_bonuses_sheet():
            ws_b = wb.create_sheet(title='Бонусы'[:31])
            headers = ["ФИО"]
            if include_supervisor:
                headers.append("Супервайзер")
            headers += ["Ставка"] + [f"{d:02d}.{mon:02d}" for d in days] + ["Итого"]
            _make_header(ws_b, headers)

            type_colors = {
                'Приведи друга': '16A34A',
                'Обучение': '0891B2',
                'Съемки': '7C3AED',
                'Другое': 'B45309',
            }
            dynamic_type_colors = {}
            dynamic_palette = ['0F766E', 'BE123C', '4338CA', 'A16207', '0369A1', '86198F', 'B45309']
            known_type_order = ['Приведи друга', 'Обучение', 'Съемки', 'Другое']
            used_types = set()
            calculations_by_type = defaultdict(set)
            totals_by_day = defaultdict(float)
            operator_summaries = []

            rich_separator_font = InlineFont(color='6B7280')
            rate_col = 2 + (1 if include_supervisor else 0)
            day_start_col = rate_col + 1

            def fmt_amt(value):
                try:
                    amount = float(value or 0.0)
                except Exception:
                    amount = 0.0
                if abs(amount - round(amount)) < 1e-9:
                    return f"{int(round(amount)):,}".replace(",", " ")
                return f"{amount:,.2f}".rstrip('0').rstrip('.').replace(",", " ").replace(".", ",")

            def fmt_calc_number(value):
                try:
                    number = float(value)
                except Exception:
                    return None
                if abs(number - round(number)) < 1e-9:
                    return str(int(round(number)))
                return f"{number:.2f}".rstrip('0').rstrip('.').replace(".", ",")

            def bonus_amount(bonus):
                if not isinstance(bonus, dict):
                    return 0.0
                try:
                    return float(bonus.get('amount') or 0.0)
                except Exception:
                    return 0.0

            def bonus_type(bonus):
                if not isinstance(bonus, dict):
                    return 'Другое'
                return str(bonus.get('type') or bonus.get('bonus_type') or 'Другое').strip() or 'Другое'

            def bonus_quantity(bonus):
                if not isinstance(bonus, dict):
                    return 1
                try:
                    return max(1, int(float(bonus.get('quantity') or 1)))
                except Exception:
                    return 1

            def bonus_training_hours(bonus):
                if not isinstance(bonus, dict):
                    return 0.0
                raw_value = bonus.get('training_hours')
                if raw_value in (None, ''):
                    raw_value = bonus.get('hours')
                try:
                    return max(0.0, float(raw_value or 0.0))
                except Exception:
                    return 0.0

            def type_color(type_name):
                if type_name in type_colors:
                    return type_colors[type_name]
                if type_name not in dynamic_type_colors:
                    dynamic_type_colors[type_name] = dynamic_palette[len(dynamic_type_colors) % len(dynamic_palette)]
                return dynamic_type_colors[type_name]

            def bonus_calculation_text(bonus, type_name):
                if isinstance(bonus, dict):
                    explicit_calc = (
                        bonus.get('calculation')
                        or bonus.get('calculation_text')
                        or bonus.get('calculationText')
                        or bonus.get('calc')
                        or bonus.get('formula')
                    )
                    if explicit_calc:
                        return str(explicit_calc).strip()

                type_key = type_name.lower().replace('ё', 'е')
                if type_key == 'приведи друга':
                    return f"{bonus_quantity(bonus)} x 5 000"
                if type_key == 'обучение':
                    hours_text = fmt_calc_number(bonus_training_hours(bonus))
                    return f"{hours_text} ч x 500" if hours_text is not None else "часы x 500"
                if type_key == 'съемки':
                    return f"{bonus_quantity(bonus)} x 5 000"
                return ""

            def legend_calculation_text(type_name):
                type_key = type_name.lower().replace('ё', 'е')
                if type_key == 'приведи друга':
                    return "количество x 5 000"
                if type_key == 'обучение':
                    return "часы x 500"
                if type_key == 'съемки':
                    return "количество x 5 000"
                custom_calculations = calculations_by_type.get(type_name) or set()
                return "; ".join(sorted(custom_calculations))

            def extract_day_bonuses(day_entry):
                if not isinstance(day_entry, dict) or not isinstance(day_entry.get('bonuses'), list):
                    return []
                return [bonus for bonus in day_entry.get('bonuses') if isinstance(bonus, dict)]

            def bonus_item_text(bonus):
                type_name = bonus_type(bonus)
                calculation = bonus_calculation_text(bonus, type_name)
                if calculation:
                    calculations_by_type[type_name].add(calculation)
                return fmt_amt(bonus_amount(bonus))

            def set_bonuses_cell(row_idx, col_idx, bonuses_list):
                parts = []
                for idx, bonus in enumerate(bonuses_list):
                    type_name = bonus_type(bonus)
                    used_types.add(type_name)
                    if idx > 0:
                        parts.append(TextBlock(rich_separator_font, ", "))
                    parts.append(TextBlock(InlineFont(color=type_color(type_name), b=True), bonus_item_text(bonus)))
                cell = ws_b.cell(row_idx, col_idx)
                cell.value = CellRichText(parts)
                cell.alignment = Alignment(horizontal='center', vertical='center', wrapText=True)
                cell.border = BORDER_ALL
                return cell

            row = 2
            for op in operators:
                name = op.get('name') or f"op_{op.get('operator_id')}"
                name_cell = set_cell(ws_b, row, 1, name, align_center=False)
                name_cell.font = Font(bold=True)

                col_idx = 2
                if include_supervisor:
                    set_cell(ws_b, row, col_idx, op.get('supervisor_name') or "", align_center=False)
                    col_idx += 1

                rate = float(op.get('rate') or 0)
                rate_cell = set_cell(ws_b, row, col_idx, rate, align_center=False)
                rate_cell.font = Font(bold=True)

                bonuses_map = op.get('daily', {})
                seg = _op_seg(op)
                row_total = 0.0
                row_has_bonuses = False
                summary = {
                    'name': name,
                    'supervisor_name': op.get('supervisor_name') or "",
                    'rate': rate,
                    'friend_count': 0,
                    'friend_amount': 0.0,
                    'training_hours': 0.0,
                    'training_amount': 0.0,
                    'filming_count': 0,
                    'filming_amount': 0.0,
                    'other_count': 0,
                    'other_amount': 0.0,
                    'bonus_count': 0,
                    'total_amount': 0.0,
                }

                for day_idx, day in enumerate(days, start=day_start_col):
                    if seg is not None:
                        try:
                            if not _seg_has_day(seg, int(day)):
                                set_cell(ws_b, row, day_idx, "др.", fill=FILL_FOREIGN)
                                continue
                        except Exception:
                            pass
                    day_entry = bonuses_map.get(str(day)) if isinstance(bonuses_map, dict) else None
                    if day_entry is None and isinstance(bonuses_map, dict):
                        day_entry = bonuses_map.get(day)
                    bonuses_list = extract_day_bonuses(day_entry)
                    if not bonuses_list:
                        set_cell(ws_b, row, day_idx, "")
                        continue

                    day_total = sum(bonus_amount(bonus) for bonus in bonuses_list)
                    row_total += day_total
                    totals_by_day[day] += day_total
                    row_has_bonuses = True
                    for bonus in bonuses_list:
                        amount = bonus_amount(bonus)
                        type_key = bonus_type(bonus).lower().replace('ё', 'е')
                        summary['bonus_count'] += 1
                        summary['total_amount'] += amount
                        if type_key == 'приведи друга':
                            summary['friend_count'] += bonus_quantity(bonus)
                            summary['friend_amount'] += amount
                        elif type_key == 'обучение':
                            summary['training_hours'] += bonus_training_hours(bonus)
                            summary['training_amount'] += amount
                        elif type_key == 'съемки':
                            summary['filming_count'] += bonus_quantity(bonus)
                            summary['filming_amount'] += amount
                        else:
                            summary['other_count'] += 1
                            summary['other_amount'] += amount
                    set_bonuses_cell(row, day_idx, bonuses_list)

                total_col = day_start_col + len(days)
                total_cell = set_cell(ws_b, row, total_col, row_total)
                total_cell.font = Font(bold=True)
                if row_total:
                    total_cell.fill = FILL_POS
                if row_has_bonuses:
                    ws_b.row_dimensions[row].height = 42

                operator_summaries.append(summary)
                row += 1

            total_row = row
            total_label_cell = set_cell(ws_b, total_row, 1, "Итого", align_center=False)
            total_label_cell.font = Font(bold=True)
            for empty_col in range(2, day_start_col):
                empty_cell = set_cell(ws_b, total_row, empty_col, "")
                empty_cell.font = Font(bold=True)
            grand_total = 0.0
            for day_idx, day in enumerate(days, start=day_start_col):
                day_total = totals_by_day.get(day, 0.0)
                grand_total += day_total
                cell = set_cell(ws_b, total_row, day_idx, day_total)
                cell.font = Font(bold=True)
                if day_total:
                    cell.fill = FILL_POS
            grand_cell = set_cell(ws_b, total_row, day_start_col + len(days), grand_total)
            grand_cell.font = Font(bold=True)
            if grand_total:
                grand_cell.fill = FILL_POS

            legend_start = total_row + 2
            legend_title = ws_b.cell(legend_start, 1)
            legend_title.value = "Легенда типов бонусов"
            legend_title.font = Font(bold=True)

            legend_header_row = legend_start + 1
            set_cell(ws_b, legend_header_row, 1, "Тип бонуса", align_center=False).font = Font(bold=True)
            set_cell(ws_b, legend_header_row, 2, "Расчет", align_center=False).font = Font(bold=True)

            legend_types = [type_name for type_name in known_type_order if type_name in used_types or type_name in type_colors]
            legend_types += sorted(type_name for type_name in used_types if type_name not in legend_types)
            for offset, type_name in enumerate(legend_types, start=1):
                legend_row = legend_header_row + offset
                type_cell = set_cell(ws_b, legend_row, 1, type_name, align_center=False)
                type_cell.font = Font(color=type_color(type_name), bold=True)
                set_cell(ws_b, legend_row, 2, legend_calculation_text(type_name), align_center=False)

            summary_title_row = legend_header_row + len(legend_types) + 2
            summary_title = ws_b.cell(summary_title_row, 1)
            summary_title.value = "Сводка по операторам"
            summary_title.font = Font(bold=True, size=12)

            summary_headers = ["ФИО"]
            if include_supervisor:
                summary_headers.append("Супервайзер")
            summary_headers += [
                "Ставка",
                "Приведи друга, кол-во",
                "Приведи друга, сумма",
                "Обучение, часы",
                "Обучение, сумма",
                "Съемки, кол-во",
                "Съемки, сумма",
                "Прочие, кол-во",
                "Прочие, сумма",
                "Всего бонусов",
                "Итого",
            ]
            summary_header_row = summary_title_row + 1
            _make_header(ws_b, summary_headers, row_idx=summary_header_row)
            for header_col in range(1, len(summary_headers) + 1):
                ws_b.cell(summary_header_row, header_col).alignment = Alignment(
                    horizontal='center', vertical='center', wrapText=True
                )
            ws_b.row_dimensions[summary_header_row].height = 42

            summary_keys = [
                'friend_count', 'friend_amount',
                'training_hours', 'training_amount',
                'filming_count', 'filming_amount',
                'other_count', 'other_amount',
                'bonus_count', 'total_amount',
            ]
            summary_row = summary_header_row + 1
            for summary in operator_summaries:
                values = [summary['name']]
                if include_supervisor:
                    values.append(summary['supervisor_name'])
                values.append(summary['rate'])
                values.extend(summary[key] for key in summary_keys)
                for col_idx, value in enumerate(values, start=1):
                    cell = set_cell(
                        ws_b,
                        summary_row,
                        col_idx,
                        value,
                        align_center=(col_idx > (2 if include_supervisor else 1)),
                    )
                    if col_idx == 1:
                        cell.font = Font(bold=True)
                total_cell = ws_b.cell(summary_row, len(summary_headers))
                total_cell.font = Font(bold=True)
                if summary['total_amount']:
                    total_cell.fill = FILL_POS
                summary_row += 1

            summary_total_values = ["Итого"]
            if include_supervisor:
                summary_total_values.append("")
            summary_total_values.append("")
            summary_total_values.extend(
                sum(float(summary[key] or 0.0) for summary in operator_summaries)
                for key in summary_keys
            )
            for col_idx, value in enumerate(summary_total_values, start=1):
                cell = set_cell(ws_b, summary_row, col_idx, value, align_center=(col_idx > 1))
                cell.font = Font(bold=True)
            if grand_total:
                ws_b.cell(summary_row, len(summary_headers)).fill = FILL_POS

            ws_b.column_dimensions['A'].width = 28
            if include_supervisor:
                ws_b.column_dimensions['B'].width = 22
            for i in range(2, len(headers) + 1):
                col = ws_b.cell(1, i).column_letter
                if i == rate_col:
                    ws_b.column_dimensions[col].width = 12
                elif day_start_col <= i < day_start_col + len(days):
                    ws_b.column_dimensions[col].width = 24
                else:
                    ws_b.column_dimensions[col].width = 14
            ws_b.freeze_panes = f"{get_column_letter(day_start_col)}2"

        # Модель отчёта: листы model-aware, только если ВСЯ выгрузка — чисто чат-модели
        # (per-group экспорт чат-группы). Смешанные/операторские — прежняя раскладка.
        _report_models = {str((o or {}).get('calculation_model_code') or '').strip() for o in operators}
        is_chat_report = (_report_models == {CALCULATION_MODEL_CHAT_MANAGER})

        build_work_time_sheet()
        build_generic_sheet('break_time', 'Перерыв', 'break_time', is_hour=True)
        if is_chat_report:
            # У чата нет звонков/эффективности: «Чаты» + «Средняя оценка» + «Среднее время ответа».
            build_generic_sheet('calls', 'Чаты', 'calls', is_hour=False)
            build_generic_sheet('avg_score', 'Средняя оценка', 'avg_score', is_hour=False,
                                value_getter=lambda d: (d.get('chat_metrics') or {}).get('avg_score'),
                                aggregate='avg', aggregate_value_getter=_weighted_chat_average)
            build_generic_sheet('response_time', 'Среднее время ответа', 'response_time', is_hour=False,
                                value_getter=lambda d: (d.get('chat_metrics') or {}).get('avg_response_time_seconds'),
                                aggregate='avg')
        else:
            build_calls_sheet()
            build_efficiency_sheet()
        build_generic_sheet('no_phone', 'Без телефона', 'no_phone_hours', is_hour=True)
        build_fines_sheet()
        build_bonuses_sheet()

        ws_t = wb.create_sheet(title='Тренинги'[:31])

        headers = ["Оператор"]
        if include_supervisor:
            headers.append("Супервайзер")
        headers += [f"{d:02d}.{mon:02d}" for d in days] + ["Всего (ч)"]
        _make_header(ws_t, headers)

        def fmt_num(n):
            """Возвращаем число без дополнительного округления."""
            try:
                return float(n or 0.0)
            except Exception:
                return 0.0

        # Заполняем строки — показываем по дням только зачётные часы и одну итоговую колонку "Всего (ч)".
        row_counted = 2

        for op in operators:
            name = op.get('name') or f"op_{op.get('operator_id')}"
            op_id = op.get('operator_id')
            try:
                op_id_int = int(op_id)
            except Exception:
                op_id_int = None

            # Берём все тренинги оператора (словарь day -> list)
            op_trainings = trainings_map.get(op_id_int) or {}
            seg = _op_seg(op)

            # Инициализация итогов
            total_all = 0.0

            # Сначала пройдем все дни, чтобы посчитать общие итоги (только дни своего сегмента)
            for day in days:
                if seg is not None and not _seg_has_day(seg, day):
                    continue
                arr = op_trainings.get(day, []) if isinstance(op_trainings, dict) else []
                total_all += compute_unique_training_duration_hours(arr)

            # --- Заполнение вкладки "Тренинги" ---
            set_cell(ws_t, row_counted, 1, name, align_center=False)
            if include_supervisor:
                sup_name = op.get('supervisor_name') or ""
                set_cell(ws_t, row_counted, 2, sup_name, align_center=False)
            day_start = 2 + (1 if include_supervisor else 0)
            for c_idx, day in enumerate(days, start=day_start):
                if seg is not None and not _seg_has_day(seg, day):
                    set_cell(ws_t, row_counted, c_idx, "др.", fill=FILL_FOREIGN)
                    continue
                arr = op_trainings.get(day, []) if isinstance(op_trainings, dict) else []
                counted = compute_unique_training_duration_hours(arr, counted_only=True)
                if counted == 0:
                    set_cell(ws_t, row_counted, c_idx, "")
                else:
                    set_cell(ws_t, row_counted, c_idx, fmt_num(counted), fill=FILL_POS)

            # Итоги по строке — только Всего (ч)
            total_col = len(headers)
            set_cell(ws_t, row_counted, total_col, fmt_num(total_all))
            row_counted += 1

        # Настройка ширины колонок для вкладки Тренинги
        ws_t.column_dimensions['A'].width = 24
        for i in range(2, 3 + len(days)):
            col = ws_t.cell(1, i).column_letter
            ws_t.column_dimensions[col].width = 14

        ws_tech = wb.create_sheet(title='Тех. сбои'[:31])
        tech_headers = ["Оператор"]
        if include_supervisor:
            tech_headers.append("Супервайзер")
        tech_headers += [f"{d:02d}.{mon:02d}" for d in days] + ["Всего (ч)"]
        _make_header(ws_tech, tech_headers)

        row_tech = 2
        for op in operators:
            name = op.get('name') or f"op_{op.get('operator_id')}"
            op_id = op.get('operator_id')
            try:
                op_id_int = int(op_id)
            except Exception:
                op_id_int = None
            op_issues = technical_issues_map.get(op_id_int) or {}
            seg = _op_seg(op)

            total_issue_hours = 0.0
            for day in days:
                if seg is not None and not _seg_has_day(seg, day):
                    continue
                arr = op_issues.get(day, []) if isinstance(op_issues, dict) else []
                for item in arr:
                    total_issue_hours += compute_technical_issue_duration_hours(item)

            set_cell(ws_tech, row_tech, 1, name, align_center=False)
            if include_supervisor:
                sup_name = op.get('supervisor_name') or ""
                set_cell(ws_tech, row_tech, 2, sup_name, align_center=False)

            day_start = 2 + (1 if include_supervisor else 0)
            for c_idx, day in enumerate(days, start=day_start):
                if seg is not None and not _seg_has_day(seg, day):
                    set_cell(ws_tech, row_tech, c_idx, "др.", fill=FILL_FOREIGN)
                    continue
                arr = op_issues.get(day, []) if isinstance(op_issues, dict) else []
                day_sum = 0.0
                for item in arr:
                    day_sum += compute_technical_issue_duration_hours(item)
                if day_sum == 0:
                    set_cell(ws_tech, row_tech, c_idx, "")
                else:
                    set_cell(ws_tech, row_tech, c_idx, fmt_num(day_sum), fill=FILL_POS)

            total_col = len(tech_headers)
            set_cell(ws_tech, row_tech, total_col, fmt_num(total_issue_hours))
            row_tech += 1

        ws_tech.column_dimensions['A'].width = 24
        for i in range(2, 3 + len(days)):
            col = ws_tech.cell(1, i).column_letter
            ws_tech.column_dimensions[col].width = 14

        ws_offline = wb.create_sheet(title='Офлайн активность'[:31])
        offline_headers = ["Оператор"]
        if include_supervisor:
            offline_headers.append("Супервайзер")
        offline_headers += [f"{d:02d}.{mon:02d}" for d in days] + ["Всего (ч)"]
        _make_header(ws_offline, offline_headers)

        row_offline = 2
        for op in operators:
            name = op.get('name') or f"op_{op.get('operator_id')}"
            op_id = op.get('operator_id')
            try:
                op_id_int = int(op_id)
            except Exception:
                op_id_int = None
            op_activities = offline_activities_map.get(op_id_int) or {}
            seg = _op_seg(op)

            total_activity_hours = 0.0
            for day in days:
                if seg is not None and not _seg_has_day(seg, day):
                    continue
                arr = op_activities.get(day, []) if isinstance(op_activities, dict) else []
                for item in arr:
                    total_activity_hours += compute_offline_activity_duration_hours(item)

            set_cell(ws_offline, row_offline, 1, name, align_center=False)
            if include_supervisor:
                sup_name = op.get('supervisor_name') or ""
                set_cell(ws_offline, row_offline, 2, sup_name, align_center=False)

            day_start = 2 + (1 if include_supervisor else 0)
            for c_idx, day in enumerate(days, start=day_start):
                if seg is not None and not _seg_has_day(seg, day):
                    set_cell(ws_offline, row_offline, c_idx, "др.", fill=FILL_FOREIGN)
                    continue
                arr = op_activities.get(day, []) if isinstance(op_activities, dict) else []
                day_sum = 0.0
                for item in arr:
                    day_sum += compute_offline_activity_duration_hours(item)
                if day_sum == 0:
                    set_cell(ws_offline, row_offline, c_idx, "")
                else:
                    set_cell(ws_offline, row_offline, c_idx, fmt_num(day_sum), fill=FILL_POS)

            total_col = len(offline_headers)
            set_cell(ws_offline, row_offline, total_col, fmt_num(total_activity_hours))
            row_offline += 1

        ws_offline.column_dimensions['A'].width = 24
        for i in range(2, 3 + len(days)):
            col = ws_offline.cell(1, i).column_letter
            ws_offline.column_dimensions[col].width = 14

        out = BytesIO()
        wb.save(out)
        out.seek(0)
        content = out.getvalue()
        if filename is None:
            filename = f"report_{month}.xlsx"
        return filename, content

    def generate_excel_report_all_operators_from_view(self,
        operators: List[Dict[str, Any]],
        trainings_map: Dict[int, Dict[int, List[Dict[str, Any]]]],
        technical_issues_map: Dict[int, Dict[int, List[Dict[str, Any]]]],
        month: str,  # 'YYYY-MM'
        offline_activities_map: Dict[int, Dict[int, List[Dict[str, Any]]]] = None,
        filename: str = None
        ) -> Tuple[str, bytes]:
        """
        Быстрая версия: дополняет операторов supervisor_name и вызывает
        generate_excel_report_from_view(include_supervisor=True).
        """
        # operators может быть {"operators": [...]} или список
        ops = operators["operators"] if isinstance(operators, dict) and "operators" in operators else operators
        logging.info("First operator for all-operators report: %s", ops[0])
        # Deduplicate ids и получить map одним запросом
        sup_ids = list({int(op.get('supervisor_id')) for op in ops if op.get('supervisor_id')})
        logging.info("Fetching supervisors for all-operators report: %s", sup_ids)
        sup_map = {}
        if sup_ids:
            try:
                with self._get_cursor() as cursor:
                    cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (sup_ids,))
                    logging.info("Fetching supervisors for all-operators report: %s", sup_ids)
                    sup_map = {r[0]: r[1] for r in cursor.fetchall()}
            except Exception:
                logging.exception("Error fetching supervisors for all-operators report")

        # Добавляем supervisor_name к каждому оператору (O(N))
        for op in ops:
            sid = op.get('supervisor_id')
            op['supervisor_name'] = sup_map.get(int(sid)) if sid else None

        # Вызов генератора — один проход, файл формируется сразу с колонкой "Супервайзер"
        return self.generate_excel_report_from_view(
            {"operators": ops},
            trainings_map,
            technical_issues_map,
            month,
            offline_activities_map=offline_activities_map,
            filename=filename,
            include_supervisor=True
        )

    def get_billing_operator_efficiency_report(
        self,
        start_day,
        end_day,
        department_id=None,
        allow_all=False,
    ):
        """Эффективность операторов по группам за произвольный период.

        Эффективность считается так же, как в разделе «Расчёт часов»:
        сумма часов эффективности / сумма рабочих часов × 100. Если в старой
        строке daily_hours ещё нет group_id, группа восстанавливается по
        историческому членству оператора на дату строки.
        """
        if isinstance(start_day, str):
            start_day = datetime.strptime(start_day, '%Y-%m-%d').date()
        if isinstance(end_day, str):
            end_day = datetime.strptime(end_day, '%Y-%m-%d').date()
        if not start_day or not end_day or end_day < start_day:
            raise ValueError("Invalid billing efficiency period")

        rate_month = end_day.strftime('%Y-%m')
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                WITH source_days AS (
                    SELECT
                        dh.operator_id,
                        COALESCE(
                            dh.group_id,
                            (
                                SELECT gom.group_id
                                FROM group_operator_memberships gom
                                WHERE gom.operator_id = dh.operator_id
                                  AND gom.start_date <= dh.day
                                  AND (gom.end_date IS NULL OR gom.end_date >= dh.day)
                                ORDER BY gom.start_date DESC, gom.group_id
                                LIMIT 1
                            )
                        ) AS group_id,
                        COALESCE(dh.work_time, 0) AS work_time,
                        COALESCE(dh.efficiency, 0) AS efficiency
                    FROM daily_hours dh
                    WHERE dh.day >= %s AND dh.day <= %s
                ),
                operator_totals AS (
                    SELECT
                        group_id,
                        operator_id,
                        SUM(work_time) AS work_hours,
                        SUM(efficiency) AS efficiency_hours
                    FROM source_days
                    WHERE group_id IS NOT NULL
                    GROUP BY group_id, operator_id
                    HAVING SUM(work_time) > 0
                )
                SELECT
                    g.id,
                    g.name,
                    u.id,
                    u.name,
                    COALESCE(
                        (
                            SELECT wh.rate
                            FROM work_hours wh
                            WHERE wh.operator_id = u.id
                              AND wh.rate IS NOT NULL
                              AND wh.month <= %s
                            ORDER BY wh.month DESC
                            LIMIT 1
                        ),
                        u.rate,
                        1.0
                    ) AS rate,
                    ot.work_hours,
                    ot.efficiency_hours
                FROM operator_totals ot
                JOIN groups g ON g.id = ot.group_id
                JOIN users u ON u.id = ot.operator_id
                LEFT JOIN directions od ON od.id = u.direction_id
                -- у группы без своей модели решает модель направления сотрудника
                WHERE COALESCE(
                          NULLIF(g.calculation_model_code, ''),
                          NULLIF(od.calculation_model_code, ''),
                          'operator'
                      ) = 'operator'
                  AND (%s OR g.department_id = %s)
                ORDER BY g.name, u.name
                """,
                (start_day, end_day, rate_month, bool(allow_all), department_id),
            )
            rows = cursor.fetchall() or []

        groups_by_id = {}
        for group_id, group_name, operator_id, operator_name, rate, work_hours, efficiency_hours in rows:
            work_value = float(work_hours or 0.0)
            if work_value <= 0:
                continue
            efficiency_value = max(
                0.0,
                min(100.0, (float(efficiency_hours or 0.0) / work_value) * 100.0),
            )
            group = groups_by_id.setdefault(
                int(group_id),
                {
                    "group_id": int(group_id),
                    "group_name": str(group_name or f"Группа {group_id}"),
                    "operators": [],
                },
            )
            group["operators"].append({
                "operator_id": int(operator_id),
                "operator": str(operator_name or f"Оператор {operator_id}"),
                "rate": float(rate if rate is not None else 1.0),
                "efficiency": efficiency_value,
            })

        groups = sorted(
            groups_by_id.values(),
            key=lambda item: (item["group_name"].casefold(), item["group_id"]),
        )
        for group in groups:
            group["operators"].sort(
                key=lambda item: (-item["efficiency"], item["operator"].casefold()),
            )
            efficiencies = [item["efficiency"] for item in group["operators"]]
            group["average_efficiency"] = (
                sum(efficiencies) / len(efficiencies) if efficiencies else None
            )

        group_averages = [
            group["average_efficiency"]
            for group in groups
            if group["average_efficiency"] is not None
        ]
        return {
            "date_from": start_day.isoformat(),
            "date_to": end_day.isoformat(),
            "groups": groups,
            "average_efficiency": (
                sum(group_averages) / len(group_averages) if group_averages else None
            ),
        }
