"""База данных: заявки IT и техпроблемы.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _minutes_to_time,
    date,
    datetime,
    dt_time,
    execute_values,
    IT_TICKET_CATALOG,
    Json,
    json,
    logging,
    role_has_min,
    technical_issue_comment_required_error,
    technical_issue_comment_rule,
    TECHNICAL_ISSUE_REASONS,
    TECHNICAL_ISSUE_REASONS_SET,
    TECHNICAL_ISSUE_WORKPLACE_MAX,
    timedelta,
    uuid,
)


class ItTicketsMixin:
    def _load_technical_issues_by_operator_day_tx(self, cursor, operator_ids, start_date, end_date, group_id=None):
        """
        Возвращает:
        - map: {operator_id: {day_num_str: [issues...]}}
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
                  WHERE gom.operator_id = ti.operator_id
                    AND gom.group_id = %s
                    AND gom.start_date <= ti.issue_date
                    AND (gom.end_date IS NULL OR gom.end_date >= ti.issue_date)
              )
            """
            params.append(int(group_id))

        cursor.execute(
            f"""
            SELECT
                ti.id,
                ti.operator_id,
                ti.issue_date,
                ti.start_time,
                ti.end_time,
                ti.reason,
                ti.comment,
                ti.workplace_number,
                ti.for_supervisor,
                cb.name,
                ti.created_at
            FROM operator_technical_issues ti
            LEFT JOIN users cb ON cb.id = ti.created_by
            WHERE ti.operator_id = ANY(%s)
              AND ti.issue_date >= %s
              AND ti.issue_date <= %s
              {group_filter_sql}
            ORDER BY ti.operator_id, ti.issue_date, ti.start_time, ti.end_time, ti.id
            """,
            params,
        )
        for issue_id, op_id, issue_date, start_time, end_time, reason, comment, workplace_number, for_supervisor, created_by_name, created_at in cursor.fetchall() or []:
            try:
                op_id_int = int(op_id)
            except Exception:
                continue
            if op_id_int not in result or issue_date is None:
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

            day_key = str(int(issue_date.day))
            result.setdefault(op_id_int, {}).setdefault(day_key, []).append({
                "id": int(issue_id),
                "date": issue_date.strftime('%Y-%m-%d'),
                "start_time": start_text,
                "end_time": end_text,
                "time_range": f"{start_text} - {end_text}" if start_text and end_text else None,
                "reason": reason,
                "comment": comment,
                "workplace_number": int(workplace_number) if workplace_number is not None else None,
                "for_supervisor": bool(for_supervisor) if for_supervisor is not None else False,
                "created_by_name": created_by_name,
                "created_at": created_at.strftime('%Y-%m-%d %H:%M:%S') if created_at else None,
                "duration_minutes": int(duration_minutes),
                "duration_hours": float(duration_hours),
            })
            totals[op_id_int] = round(float(totals.get(op_id_int, 0.0)) + float(duration_hours), 2)

        return result, totals

    def _coerce_int_list(self, values):
        if values is None:
            return []
        if isinstance(values, (list, tuple, set)):
            raw_values = list(values)
        else:
            raw_values = [values]

        normalized = []
        seen = set()
        for item in raw_values:
            if item is None:
                continue
            if isinstance(item, str) and not item.strip():
                continue
            try:
                ivalue = int(item)
            except (TypeError, ValueError):
                continue
            if ivalue <= 0 or ivalue in seen:
                continue
            seen.add(ivalue)
            normalized.append(ivalue)
        return normalized

    def _parse_technical_issue_date(self, value, field_name='date'):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not value:
            raise ValueError(f"Field '{field_name}' is required (YYYY-MM-DD)")
        try:
            return datetime.strptime(str(value), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            raise ValueError(f"Invalid '{field_name}' format. Use YYYY-MM-DD")

    def _parse_technical_issue_time(self, value, field_name='start_time', default_value=None):
        raw_value = value
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            if default_value is None:
                raise ValueError(f"Field '{field_name}' is required (HH:MM)")
            raw_value = default_value

        if isinstance(raw_value, dt_time):
            return raw_value.replace(second=0, microsecond=0)

        raw_text = str(raw_value).strip()
        for fmt in ('%H:%M', '%H:%M:%S'):
            try:
                parsed = datetime.strptime(raw_text, fmt).time()
                return parsed.replace(second=0, microsecond=0)
            except (TypeError, ValueError):
                continue
        raise ValueError(f"Invalid '{field_name}' format. Use HH:MM")

    @staticmethod
    def _workplace_office_for_code(code):
        """Группа кабинетов РМ по коду отдела: 'op' → отдел продаж, иначе тех поддержка."""
        return 'sales' if str(code or '').strip().lower() == 'op' else 'support'

    def _parse_technical_issue_workplace_number(self, value, field_name='workplace_number', required=False):
        if value is None:
            if required:
                raise ValueError(f"Field '{field_name}' is required")
            return None

        if isinstance(value, str):
            value = value.strip()
            if not value:
                if required:
                    raise ValueError(f"Field '{field_name}' is required")
                return None

        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid '{field_name}'. Use integer in range 1..{TECHNICAL_ISSUE_WORKPLACE_MAX}")

        if number < 1 or number > TECHNICAL_ISSUE_WORKPLACE_MAX:
            raise ValueError(f"Field '{field_name}' must be in range 1..{TECHNICAL_ISSUE_WORKPLACE_MAX}")

        return number

    def _parse_bool_value(self, value, field_name='value', default=None):
        if value is None:
            if default is not None:
                return bool(default)
            raise ValueError(f"Field '{field_name}' is required")
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return bool(value)

        text = str(value).strip().lower()
        if text in ('1', 'true', 't', 'yes', 'y', 'on', 'да'):
            return True
        if text in ('0', 'false', 'f', 'no', 'n', 'off', 'нет'):
            return False
        raise ValueError(f"Invalid boolean field '{field_name}'")

    def _get_technical_issue_workplace_flag_tx(self, cursor, workplace_number):
        workplace_number_int = self._parse_technical_issue_workplace_number(
            workplace_number,
            field_name='workplace_number',
            required=False
        )
        if workplace_number_int is None:
            return False
        cursor.execute(
            """
            SELECT COALESCE(for_supervisor, FALSE)
            FROM technical_issue_workplace_settings
            WHERE workplace_number = %s
            """,
            (workplace_number_int,)
        )
        row = cursor.fetchone()
        return bool(row[0]) if row else False

    def _technical_issue_time_to_hhmm(self, time_value):
        if isinstance(time_value, dt_time):
            return time_value.strftime('%H:%M')
        if isinstance(time_value, str):
            try:
                parsed = self._parse_technical_issue_time(time_value, field_name='time')
                return parsed.strftime('%H:%M')
            except ValueError:
                return str(time_value)
        return str(time_value or '')

    def _resolve_technical_issue_operator_ids_tx(
        self,
        cursor,
        requester_id,
        requester_role,
        operator_ids=None,
        direction_ids=None,
        enforce_supervisor_scope=True,
        scope_department_id=None
    ):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can register technical issues")

        operator_ids_norm = self._coerce_int_list(operator_ids)
        direction_ids_norm = self._coerce_int_list(direction_ids)
        target_operator_ids = set()

        if operator_ids_norm:
            cursor.execute("""
                SELECT id, supervisor_id, department_id
                FROM users
                WHERE role = 'operator' AND id = ANY(%s)
            """, (operator_ids_norm,))
            rows = cursor.fetchall()
            found_ids = {int(row[0]) for row in rows}
            missing_ids = [op_id for op_id in operator_ids_norm if op_id not in found_ids]
            if missing_ids:
                raise ValueError(f"Operators not found: {', '.join(map(str, missing_ids))}")

            if role_norm == 'sv' and scope_department_id is not None:
                forbidden_ids = [
                    int(op_id)
                    for op_id, _supervisor_id, department_id in rows
                    if int(department_id or 0) != int(scope_department_id)
                ]
                if forbidden_ids:
                    raise ValueError(f"Forbidden operators for sv: {', '.join(map(str, forbidden_ids))}")
            elif role_norm == 'sv' and enforce_supervisor_scope:
                forbidden_ids = [int(op_id) for op_id, supervisor_id, _department_id in rows if int(supervisor_id or 0) != int(requester_id)]
                if forbidden_ids:
                    raise ValueError(f"Forbidden operators for sv: {', '.join(map(str, forbidden_ids))}")
            target_operator_ids.update(found_ids)

        if direction_ids_norm:
            cursor.execute("""
                SELECT id, department_id
                FROM directions
                WHERE id = ANY(%s) AND is_active = TRUE
            """, (direction_ids_norm,))
            direction_rows = cursor.fetchall()
            valid_direction_ids = {int(row[0]) for row in direction_rows}
            missing_directions = [dir_id for dir_id in direction_ids_norm if dir_id not in valid_direction_ids]
            if missing_directions:
                raise ValueError(f"Directions not found or inactive: {', '.join(map(str, missing_directions))}")

            if role_norm == 'sv' and scope_department_id is not None:
                forbidden_directions = [
                    int(direction_id)
                    for direction_id, department_id in direction_rows
                    if int(department_id or 0) != int(scope_department_id)
                ]
                if forbidden_directions:
                    raise ValueError(
                        f"Forbidden directions for sv: {', '.join(map(str, forbidden_directions))}"
                    )

            if role_norm == 'sv' and scope_department_id is not None:
                cursor.execute("""
                    SELECT id
                    FROM users
                    WHERE role = 'operator'
                      AND direction_id = ANY(%s)
                      AND department_id = %s
                      AND COALESCE(status, 'working') <> 'fired'
                """, (direction_ids_norm, int(scope_department_id)))
            elif role_norm == 'sv' and enforce_supervisor_scope:
                cursor.execute("""
                    SELECT id
                    FROM users
                    WHERE role = 'operator'
                      AND direction_id = ANY(%s)
                      AND supervisor_id = %s
                      AND COALESCE(status, 'working') <> 'fired'
                """, (direction_ids_norm, int(requester_id)))
            else:
                cursor.execute("""
                    SELECT id
                    FROM users
                    WHERE role = 'operator'
                      AND direction_id = ANY(%s)
                      AND COALESCE(status, 'working') <> 'fired'
                """, (direction_ids_norm,))
            target_operator_ids.update(int(row[0]) for row in cursor.fetchall())

        if not target_operator_ids:
            raise ValueError("Select at least one operator or direction with active operators")

        return sorted(target_operator_ids), direction_ids_norm

    def get_technical_issue_reasons(self):
        return list(TECHNICAL_ISSUE_REASONS)

    def get_technical_issue_workplace_settings(self, requester_id, requester_role):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can view technical issue workplace settings")

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    s.workplace_number,
                    COALESCE(s.for_supervisor, FALSE),
                    s.updated_by,
                    u.name,
                    s.updated_at
                FROM technical_issue_workplace_settings s
                LEFT JOIN users u ON u.id = s.updated_by
                ORDER BY s.workplace_number
                """
            )
            items = []
            for workplace_number, for_supervisor, updated_by, updated_by_name, updated_at in cursor.fetchall() or []:
                items.append({
                    'workplace_number': int(workplace_number),
                    'for_supervisor': bool(for_supervisor),
                    'updated_by_id': int(updated_by) if updated_by is not None else None,
                    'updated_by_name': updated_by_name,
                    'updated_at': updated_at.strftime('%Y-%m-%d %H:%M:%S') if updated_at else None
                })
            return {'items': items}

    def update_technical_issue_workplace_setting(self, requester_id, requester_role, workplace_number, for_supervisor):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not role_has_min(role_norm, 'admin'):
            raise ValueError("Only admin can update technical issue workplace settings")

        requester_id_int = int(requester_id)
        workplace_number_int = self._parse_technical_issue_workplace_number(
            workplace_number,
            field_name='workplace_number',
            required=True
        )
        flag_value = self._parse_bool_value(for_supervisor, field_name='for_supervisor', default=False)

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO technical_issue_workplace_settings (
                    workplace_number,
                    for_supervisor,
                    updated_by,
                    updated_at
                )
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (workplace_number) DO UPDATE SET
                    for_supervisor = EXCLUDED.for_supervisor,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING workplace_number, for_supervisor, updated_by, updated_at
                """,
                (workplace_number_int, flag_value, requester_id_int)
            )
            row = cursor.fetchone()
            updated_by_name = None
            if row and row[2] is not None:
                cursor.execute("SELECT name FROM users WHERE id = %s", (int(row[2]),))
                user_row = cursor.fetchone()
                updated_by_name = user_row[0] if user_row else None

            return {
                'workplace_number': int(row[0]),
                'for_supervisor': bool(row[1]),
                'updated_by_id': int(row[2]) if row[2] is not None else None,
                'updated_by_name': updated_by_name,
                'updated_at': row[3].strftime('%Y-%m-%d %H:%M:%S') if row[3] else None
            }

    @staticmethod
    def _row_to_it_channel(row):
        if not row:
            return None
        return {
            'id': int(row[0]),
            'chat_id': int(row[1]),
            'title': row[2],
            'chat_type': row[3],
            'username': row[4],
            'is_active': bool(row[5]),
            'source': row[6],
            'added_by': int(row[7]) if row[7] is not None else None,
            'created_at': row[8].strftime('%Y-%m-%d %H:%M:%S') if row[8] else None,
            'updated_at': row[9].strftime('%Y-%m-%d %H:%M:%S') if row[9] else None,
            'last_seen_at': row[10].strftime('%Y-%m-%d %H:%M:%S') if row[10] else None,
        }

    def list_it_ticket_channels(self, active_only=True):
        cols = self._IT_CHANNEL_COLUMNS
        with self._get_cursor() as cursor:
            if active_only:
                cursor.execute(
                    f"SELECT {cols} FROM it_ticket_channels "
                    f"WHERE is_active = TRUE ORDER BY title NULLS LAST, id"
                )
            else:
                cursor.execute(
                    f"SELECT {cols} FROM it_ticket_channels "
                    f"ORDER BY is_active DESC, title NULLS LAST, id"
                )
            return [self._row_to_it_channel(r) for r in cursor.fetchall()]

    def get_it_ticket_channel(self, channel_id):
        cols = self._IT_CHANNEL_COLUMNS
        with self._get_cursor() as cursor:
            cursor.execute(f"SELECT {cols} FROM it_ticket_channels WHERE id = %s", (int(channel_id),))
            return self._row_to_it_channel(cursor.fetchone())

    def add_it_ticket_channel(self, chat_id, title=None, chat_type=None, username=None,
                              added_by=None, source='manual'):
        """Ручное добавление / повторная активация канала (помечает is_active=TRUE)."""
        cols = self._IT_CHANNEL_COLUMNS
        with self._get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO it_ticket_channels
                    (chat_id, title, chat_type, username, is_active, source, added_by, last_seen_at)
                VALUES (%s, %s, %s, %s, TRUE, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (chat_id) DO UPDATE SET
                    title = COALESCE(EXCLUDED.title, it_ticket_channels.title),
                    chat_type = COALESCE(EXCLUDED.chat_type, it_ticket_channels.chat_type),
                    username = COALESCE(EXCLUDED.username, it_ticket_channels.username),
                    is_active = TRUE,
                    added_by = COALESCE(it_ticket_channels.added_by, EXCLUDED.added_by),
                    updated_at = CURRENT_TIMESTAMP,
                    last_seen_at = CURRENT_TIMESTAMP
                RETURNING {cols}
                """,
                (int(chat_id), title, chat_type, username, source, added_by),
            )
            return self._row_to_it_channel(cursor.fetchone())

    def record_discovered_chat(self, chat_id, title=None, chat_type=None, username=None):
        """Авто-обнаружение: бот добавлен в чат. Записывает/обновляет канал (active)."""
        cols = self._IT_CHANNEL_COLUMNS
        with self._get_cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO it_ticket_channels
                    (chat_id, title, chat_type, username, is_active, source, last_seen_at)
                VALUES (%s, %s, %s, %s, TRUE, 'auto', CURRENT_TIMESTAMP)
                ON CONFLICT (chat_id) DO UPDATE SET
                    title = COALESCE(EXCLUDED.title, it_ticket_channels.title),
                    chat_type = COALESCE(EXCLUDED.chat_type, it_ticket_channels.chat_type),
                    username = COALESCE(EXCLUDED.username, it_ticket_channels.username),
                    is_active = TRUE,
                    updated_at = CURRENT_TIMESTAMP,
                    last_seen_at = CURRENT_TIMESTAMP
                RETURNING {cols}
                """,
                (int(chat_id), title, chat_type, username),
            )
            return self._row_to_it_channel(cursor.fetchone())

    def set_it_ticket_channel_active(self, channel_id, is_active):
        cols = self._IT_CHANNEL_COLUMNS
        with self._get_cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE it_ticket_channels
                SET is_active = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING {cols}
                """,
                (bool(is_active), int(channel_id)),
            )
            return self._row_to_it_channel(cursor.fetchone())

    def deactivate_it_ticket_channel_by_chat_id(self, chat_id):
        with self._get_cursor() as cursor:
            cursor.execute(
                "UPDATE it_ticket_channels SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP "
                "WHERE chat_id = %s",
                (int(chat_id),),
            )

    def delete_it_ticket_channel(self, channel_id):
        with self._get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM it_ticket_channels WHERE id = %s RETURNING id",
                (int(channel_id),),
            )
            return cursor.fetchone() is not None

    def record_it_ticket(self, *, profile, category, subcategory, priority, title, body,
                         fields, channel_id, channel_chat_id, channel_title,
                         telegram_message_id, status, created_by, created_by_name):
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO it_tickets
                    (profile, category, subcategory, priority, title, body, fields,
                     channel_id, channel_chat_id, channel_title, telegram_message_id,
                     status, created_by, created_by_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
                """,
                (
                    profile, category, subcategory, priority, title, body,
                    json.dumps(fields or {}, ensure_ascii=False),
                    channel_id, channel_chat_id, channel_title, telegram_message_id,
                    status, created_by, created_by_name,
                ),
            )
            row = cursor.fetchone()
            return {
                'id': int(row[0]),
                'created_at': row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else None,
            }

    def list_it_tickets(self, limit=50, offset=0):
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, profile, category, subcategory, priority, title, body,
                       channel_title, telegram_message_id, status, created_by_name, created_at
                FROM it_tickets
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (int(limit), int(offset)),
            )
            items = []
            for row in cursor.fetchall():
                items.append({
                    'id': int(row[0]),
                    'profile': row[1],
                    'category': row[2],
                    'subcategory': row[3],
                    'priority': row[4],
                    'title': row[5],
                    'body': row[6],
                    'channel_title': row[7],
                    'telegram_message_id': int(row[8]) if row[8] is not None else None,
                    'status': row[9],
                    'created_by_name': row[10],
                    'created_at': row[11].strftime('%Y-%m-%d %H:%M:%S') if row[11] else None,
                })
            return items

    @staticmethod
    def _row_to_it_instruction(row, profile):
        if not row:
            return {
                'profile': profile,
                'instructions': '',
                'updated_by': None,
                'updated_by_name': None,
                'updated_at': None,
            }
        return {
            'profile': row[0],
            'instructions': row[1] or '',
            'updated_by': int(row[2]) if row[2] is not None else None,
            'updated_by_name': row[3],
            'updated_at': row[4].strftime('%Y-%m-%d %H:%M:%S') if row[4] else None,
        }

    def get_it_ticket_instructions(self, profile=None):
        """Возвращает инструкции к ИИ. Без profile — список по всем профилям
        (common/op/szov) с пустыми значениями по умолчанию для отсутствующих."""
        with self._get_cursor() as cursor:
            if profile is not None:
                cursor.execute(
                    """
                    SELECT i.profile, i.instructions, i.updated_by, u.name, i.updated_at
                    FROM it_ticket_instructions i
                    LEFT JOIN users u ON u.id = i.updated_by
                    WHERE i.profile = %s
                    """,
                    (str(profile).strip().lower(),),
                )
                return self._row_to_it_instruction(cursor.fetchone(), str(profile).strip().lower())
            cursor.execute(
                """
                SELECT i.profile, i.instructions, i.updated_by, u.name, i.updated_at
                FROM it_ticket_instructions i
                LEFT JOIN users u ON u.id = i.updated_by
                """
            )
            existing = {r[0]: r for r in cursor.fetchall()}
            return [self._row_to_it_instruction(existing.get(p), p) for p in self.IT_TICKET_INSTRUCTION_PROFILES]

    def set_it_ticket_instructions(self, profile, instructions, updated_by):
        profile_norm = str(profile or '').strip().lower()
        if profile_norm not in self.IT_TICKET_INSTRUCTION_PROFILES:
            raise ValueError("Unknown instruction profile")
        text = str(instructions or '').strip()
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO it_ticket_instructions (profile, instructions, updated_by, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (profile) DO UPDATE SET
                    instructions = EXCLUDED.instructions,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING profile, instructions, updated_by, updated_at
                """,
                (profile_norm, text, updated_by),
            )
            row = cursor.fetchone()
            name = None
            if row and row[2] is not None:
                cursor.execute("SELECT name FROM users WHERE id = %s", (int(row[2]),))
                user_row = cursor.fetchone()
                name = user_row[0] if user_row else None
            return {
                'profile': row[0],
                'instructions': row[1] or '',
                'updated_by': int(row[2]) if row[2] is not None else None,
                'updated_by_name': name,
                'updated_at': row[3].strftime('%Y-%m-%d %H:%M:%S') if row[3] else None,
            }

    def get_combined_it_ticket_instructions(self, profile):
        """Текст инструкций для инъекции в промпт: общие (common) + выбранный профиль."""
        prof = profile if profile in ('op', 'szov') else 'op'
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT profile, instructions FROM it_ticket_instructions WHERE profile IN ('common', %s)",
                (prof,),
            )
            rows = {r[0]: (r[1] or '').strip() for r in cursor.fetchall()}
        parts = []
        if rows.get('common'):
            parts.append(rows['common'])
        if rows.get(prof):
            parts.append(rows[prof])
        return "\n".join(parts).strip()

    # ─── IT-ticket catalog (редактируемые категории/типы; админ) ────────────────
    @staticmethod
    def _sanitize_it_ticket_catalog(data):
        """Приводит каталог к безопасной структуре. Профили op/szov; для отсутствующих
        или пустых берётся дефолт IT_TICKET_CATALOG."""
        result = {}
        src = data if isinstance(data, dict) else {}
        for profile, default in IT_TICKET_CATALOG.items():
            entry = src.get(profile) if isinstance(src.get(profile), dict) else {}
            label = str(entry.get('label') or default.get('label') or profile).strip()[:120]
            categories = []
            seen_cats = set()
            raw_categories = entry.get('categories')
            for cat in (raw_categories if isinstance(raw_categories, list) else []):
                if not isinstance(cat, dict):
                    continue
                name = str(cat.get('name') or '').strip()[:200]
                if not name or name.lower() in seen_cats:
                    continue
                seen_cats.add(name.lower())
                items = []
                seen_items = set()
                raw_items = cat.get('items')
                for it in (raw_items if isinstance(raw_items, list) else []):
                    text = str(it or '').strip()[:300]
                    if not text or text.lower() in seen_items:
                        continue
                    seen_items.add(text.lower())
                    items.append(text)
                    if len(items) >= 100:
                        break
                categories.append({'name': name, 'items': items})
                if len(categories) >= 100:
                    break
            # Пустой профиль → дефолт, чтобы каталог никогда не оказался пустым.
            # Глубокая копия: не делим списки items с модульной константой IT_TICKET_CATALOG.
            if not categories:
                categories = [
                    {'name': c.get('name'), 'items': list(c.get('items') or [])}
                    for c in default.get('categories', [])
                ]
            result[profile] = {'label': label, 'categories': categories}
        return result

    def get_it_ticket_catalog(self):
        """Действующий каталог: из БД, если задан, иначе дефолт IT_TICKET_CATALOG."""
        try:
            with self._get_cursor() as cursor:
                cursor.execute("SELECT data FROM it_ticket_catalog WHERE id = 1")
                row = cursor.fetchone()
            if row and isinstance(row[0], dict):
                return self._sanitize_it_ticket_catalog(row[0])
        except Exception:
            logging.exception("Failed to load IT-ticket catalog; using default")
        # Возвращаем копию дефолта
        return self._sanitize_it_ticket_catalog(IT_TICKET_CATALOG)

    def set_it_ticket_catalog(self, data, updated_by):
        sanitized = self._sanitize_it_ticket_catalog(data)
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO it_ticket_catalog (id, data, updated_by, updated_at)
                VALUES (1, %s::jsonb, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (id) DO UPDATE SET
                    data = EXCLUDED.data,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (json.dumps(sanitized, ensure_ascii=False), updated_by),
            )
        return sanitized

    # ─── IT-ticket pinned channels (закрепляет админ / глава отдела) ────────────
    def get_it_ticket_pinned_channels(self):
        """Карта профиль→закреплённый канал с информацией, кто и когда закрепил."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT p.profile, p.channel_id, c.title, c.chat_id, c.is_active,
                       p.pinned_by, u.name, p.pinned_at
                FROM it_ticket_pinned_channels p
                LEFT JOIN it_ticket_channels c ON c.id = p.channel_id
                LEFT JOIN users u ON u.id = p.pinned_by
                """
            )
            result = {}
            for row in cursor.fetchall():
                result[row[0]] = {
                    'profile': row[0],
                    'channel_id': int(row[1]) if row[1] is not None else None,
                    'channel_title': row[2],
                    'channel_chat_id': int(row[3]) if row[3] is not None else None,
                    'channel_is_active': bool(row[4]) if row[4] is not None else None,
                    'pinned_by': int(row[5]) if row[5] is not None else None,
                    'pinned_by_name': row[6],
                    'pinned_at': row[7].strftime('%Y-%m-%d %H:%M:%S') if row[7] else None,
                }
            return result

    def get_it_ticket_pinned_channel(self, profile):
        """Закреплённый АКТИВНЫЙ канал (полная запись) для профиля или None."""
        prof = profile if profile in ('op', 'szov') else 'op'
        cols = ', '.join(f"c.{col}" for col in self._IT_CHANNEL_COLUMNS.split(', '))
        with self._get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {cols}
                FROM it_ticket_pinned_channels p
                JOIN it_ticket_channels c ON c.id = p.channel_id
                WHERE p.profile = %s AND c.is_active = TRUE
                """,
                (prof,),
            )
            return self._row_to_it_channel(cursor.fetchone())

    def set_it_ticket_pinned_channel(self, profile, channel_id, pinned_by):
        prof = str(profile or '').strip().lower()
        if prof not in ('op', 'szov'):
            raise ValueError("Unknown pin profile")
        ch_id = None
        if channel_id is not None and str(channel_id).strip() != '':
            ch_id = int(channel_id)
        with self._get_cursor() as cursor:
            if ch_id is not None:
                cursor.execute("SELECT id, is_active FROM it_ticket_channels WHERE id = %s", (ch_id,))
                row = cursor.fetchone()
                if not row:
                    raise ValueError("Channel not found")
                if not bool(row[1]):
                    raise ValueError("Channel is inactive")
            cursor.execute(
                """
                INSERT INTO it_ticket_pinned_channels (profile, channel_id, pinned_by, pinned_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (profile) DO UPDATE SET
                    channel_id = EXCLUDED.channel_id,
                    pinned_by = EXCLUDED.pinned_by,
                    pinned_at = CURRENT_TIMESTAMP
                RETURNING profile
                """,
                (prof, ch_id, pinned_by),
            )
        return self.get_it_ticket_pinned_channels().get(prof)

    def create_operator_technical_issues(
        self,
        requester_id,
        requester_role,
        issue_date,
        reason,
        start_time=None,
        end_time=None,
        comment=None,
        operator_ids=None,
        direction_ids=None,
        workplace_number=None,
        scope_department_id=None
    ):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can create technical issues")

        reason_text = str(reason or '').strip()
        if reason_text not in TECHNICAL_ISSUE_REASONS_SET:
            raise ValueError("Invalid technical issue reason")

        issue_date_obj = self._parse_technical_issue_date(issue_date, field_name='date')
        start_time_obj = self._parse_technical_issue_time(start_time, field_name='start_time', default_value='00:00')
        end_time_obj = self._parse_technical_issue_time(end_time, field_name='end_time', default_value='23:59')
        if start_time_obj == end_time_obj:
            raise ValueError("start_time and end_time cannot be equal")
        comment_text = str(comment or '').strip() or None
        if comment_text is None and technical_issue_comment_rule(reason_text):
            raise ValueError(technical_issue_comment_required_error(reason_text))
        workplace_number_int = self._parse_technical_issue_workplace_number(
            workplace_number,
            field_name='workplace_number',
            required=False
        )
        requester_id_int = int(requester_id)

        with self._get_cursor() as cursor:
            target_operator_ids, selected_direction_ids = self._resolve_technical_issue_operator_ids_tx(
                cursor=cursor,
                requester_id=requester_id_int,
                requester_role=role_norm,
                operator_ids=operator_ids,
                direction_ids=direction_ids,
                enforce_supervisor_scope=False,
                scope_department_id=scope_department_id
            )

            overlaps_by_operator = self._find_shift_overlap_intervals_for_technical_issue_tx(
                cursor=cursor,
                operator_ids=target_operator_ids,
                issue_date_obj=issue_date_obj,
                start_time_obj=start_time_obj,
                end_time_obj=end_time_obj
            )
            eligible_operator_ids = sorted(overlaps_by_operator.keys())
            if not eligible_operator_ids:
                raise ValueError("No selected operators have shifts in the specified time range")

            batch_id = str(uuid.uuid4())
            for_supervisor = self._get_technical_issue_workplace_flag_tx(cursor, workplace_number_int)
            operator_offices = self._get_operator_offices_tx(cursor, eligible_operator_ids)
            issue_values = []

            for operator_id in eligible_operator_ids:
                office = operator_offices.get(int(operator_id), 'support')
                day_chunks = self._split_absolute_minutes_intervals_by_day(
                    overlaps_by_operator.get(operator_id) or [],
                    issue_date_obj
                )
                for chunk in day_chunks:
                    issue_values.append((
                        batch_id,
                        int(operator_id),
                        chunk['date'],
                        chunk['start_time'],
                        chunk['end_time'],
                        issue_date_obj,
                        start_time_obj,
                        end_time_obj,
                        reason_text,
                        comment_text,
                        workplace_number_int,
                        for_supervisor,
                        Json(selected_direction_ids),
                        requester_id_int,
                        office
                    ))

            if not issue_values:
                raise ValueError("No overlapping shift intervals were found")

            execute_values(
                cursor,
                """
                    INSERT INTO operator_technical_issues (
                        batch_id,
                        operator_id,
                        issue_date,
                        start_time,
                        end_time,
                        requested_issue_date,
                        requested_start_time,
                        requested_end_time,
                        reason,
                        comment,
                        workplace_number,
                        for_supervisor,
                        direction_ids,
                        created_by,
                        workplace_office
                    )
                    VALUES %s
                """,
                issue_values
            )

            direction_name_map = self._get_direction_name_map_tx(cursor, selected_direction_ids)
            skipped_operator_ids = [op_id for op_id in target_operator_ids if op_id not in set(eligible_operator_ids)]
            return {
                'batch_id': batch_id,
                'created_count': len(issue_values),
                'created_operator_count': len(eligible_operator_ids),
                'operator_ids': eligible_operator_ids,
                'requested_operator_ids': target_operator_ids,
                'requested_operator_count': len(target_operator_ids),
                'skipped_operator_ids': skipped_operator_ids,
                'skipped_operator_count': len(skipped_operator_ids),
                'selected_direction_ids': selected_direction_ids,
                'selected_direction_names': [direction_name_map.get(dir_id) for dir_id in selected_direction_ids if direction_name_map.get(dir_id)],
                'date': issue_date_obj.strftime('%Y-%m-%d'),
                'start_time': start_time_obj.strftime('%H:%M'),
                'end_time': end_time_obj.strftime('%H:%M'),
                'reason': reason_text,
                'workplace_number': workplace_number_int,
                'for_supervisor': bool(for_supervisor)
            }

    def update_operator_technical_issue_batch(
        self,
        requester_id,
        requester_role,
        issue_id,
        issue_date,
        reason,
        start_time=None,
        end_time=None,
        comment=None,
        direction_ids=None,
        workplace_number=None,
        scope_department_id=None
    ):
        """Atomically rebuild every row of a massive technical-issue batch."""
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can update technical issues")

        try:
            issue_id_int = int(issue_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid technical issue id")
        if issue_id_int <= 0:
            raise ValueError("Invalid technical issue id")

        reason_text = str(reason or '').strip()
        if reason_text not in TECHNICAL_ISSUE_REASONS_SET:
            raise ValueError("Invalid technical issue reason")

        issue_date_obj = self._parse_technical_issue_date(issue_date, field_name='date')
        start_time_obj = self._parse_technical_issue_time(start_time, field_name='start_time', default_value='00:00')
        end_time_obj = self._parse_technical_issue_time(end_time, field_name='end_time', default_value='23:59')
        if start_time_obj == end_time_obj:
            raise ValueError("start_time and end_time cannot be equal")

        selected_direction_ids = self._coerce_int_list(direction_ids)
        if not selected_direction_ids:
            raise ValueError("Select at least one direction for a massive technical issue")

        comment_text = str(comment or '').strip() or None
        if comment_text is None and technical_issue_comment_rule(reason_text):
            raise ValueError(technical_issue_comment_required_error(reason_text))
        workplace_number_int = self._parse_technical_issue_workplace_number(
            workplace_number,
            field_name='workplace_number',
            required=False
        )
        requester_id_int = int(requester_id)

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    ti.batch_id::text,
                    ti.direction_ids,
                    ti.created_by,
                    ti.created_at
                FROM operator_technical_issues ti
                WHERE ti.id = %s
                """,
                (issue_id_int,)
            )
            source_row = cursor.fetchone()
            if not source_row:
                raise ValueError("Technical issue not found")

            batch_id = source_row[0]
            if not self._normalize_direction_ids_json_payload(source_row[1]):
                raise ValueError("Only massive technical issues can be edited as a batch")

            cursor.execute(
                """
                SELECT op.supervisor_id, op.department_id, ti.created_by
                FROM operator_technical_issues ti
                JOIN users op ON op.id = ti.operator_id
                WHERE ti.batch_id = %s::uuid
                """,
                (batch_id,)
            )
            access_rows = cursor.fetchall() or []
            if not access_rows:
                raise ValueError("Technical issue not found")
            if role_norm == 'sv':
                for supervisor_id, department_id, created_by in access_rows:
                    can_manage_by_scope = (
                        int(department_id or 0) == int(scope_department_id)
                        if scope_department_id is not None
                        else int(supervisor_id or 0) == requester_id_int
                    )
                    can_manage_by_creator = int(created_by or 0) == requester_id_int
                    if not (can_manage_by_scope or can_manage_by_creator):
                        raise PermissionError("Forbidden")

            target_operator_ids, selected_direction_ids = self._resolve_technical_issue_operator_ids_tx(
                cursor=cursor,
                requester_id=requester_id_int,
                requester_role=role_norm,
                direction_ids=selected_direction_ids,
                enforce_supervisor_scope=False,
                scope_department_id=scope_department_id
            )
            overlaps_by_operator = self._find_shift_overlap_intervals_for_technical_issue_tx(
                cursor=cursor,
                operator_ids=target_operator_ids,
                issue_date_obj=issue_date_obj,
                start_time_obj=start_time_obj,
                end_time_obj=end_time_obj
            )
            eligible_operator_ids = sorted(overlaps_by_operator.keys())
            if not eligible_operator_ids:
                raise ValueError("No selected operators have shifts in the specified time range")

            for_supervisor = self._get_technical_issue_workplace_flag_tx(cursor, workplace_number_int)
            operator_offices = self._get_operator_offices_tx(cursor, eligible_operator_ids)
            issue_values = []
            for operator_id in eligible_operator_ids:
                office = operator_offices.get(int(operator_id), 'support')
                day_chunks = self._split_absolute_minutes_intervals_by_day(
                    overlaps_by_operator.get(operator_id) or [],
                    issue_date_obj
                )
                for chunk in day_chunks:
                    issue_values.append((
                        batch_id,
                        int(operator_id),
                        chunk['date'],
                        chunk['start_time'],
                        chunk['end_time'],
                        issue_date_obj,
                        start_time_obj,
                        end_time_obj,
                        reason_text,
                        comment_text,
                        workplace_number_int,
                        for_supervisor,
                        Json(selected_direction_ids),
                        int(source_row[2]),
                        office
                    ))

            if not issue_values:
                raise ValueError("No overlapping shift intervals were found")

            cursor.execute(
                "DELETE FROM operator_technical_issues WHERE batch_id = %s::uuid",
                (batch_id,)
            )
            execute_values(
                cursor,
                """
                    INSERT INTO operator_technical_issues (
                        batch_id,
                        operator_id,
                        issue_date,
                        start_time,
                        end_time,
                        requested_issue_date,
                        requested_start_time,
                        requested_end_time,
                        reason,
                        comment,
                        workplace_number,
                        for_supervisor,
                        direction_ids,
                        created_by,
                        workplace_office,
                        created_at
                    )
                    VALUES %s
                """,
                [values + (source_row[3],) for values in issue_values]
            )

            direction_name_map = self._get_direction_name_map_tx(cursor, selected_direction_ids)
            eligible_operator_set = set(eligible_operator_ids)
            skipped_operator_ids = [op_id for op_id in target_operator_ids if op_id not in eligible_operator_set]
            return {
                'batch_id': batch_id,
                'updated_count': len(issue_values),
                'updated_operator_count': len(eligible_operator_ids),
                'operator_ids': eligible_operator_ids,
                'requested_operator_ids': target_operator_ids,
                'skipped_operator_ids': skipped_operator_ids,
                'skipped_operator_count': len(skipped_operator_ids),
                'selected_direction_ids': selected_direction_ids,
                'selected_direction_names': [
                    direction_name_map.get(dir_id)
                    for dir_id in selected_direction_ids
                    if direction_name_map.get(dir_id)
                ],
                'date': issue_date_obj.strftime('%Y-%m-%d'),
                'start_time': start_time_obj.strftime('%H:%M'),
                'end_time': end_time_obj.strftime('%H:%M'),
                'reason': reason_text,
                'workplace_number': workplace_number_int,
                'for_supervisor': bool(for_supervisor)
            }

    def get_operator_technical_issues(
        self,
        requester_id,
        requester_role,
        issue_date=None,
        date_from=None,
        date_to=None,
        operator_id=None,
        reason=None,
        workplace_number=None,
        group_id=None,
        limit=500,
        offset=0,
        scope_department_id=None
    ):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can view technical issues")

        limit_int = max(1, min(int(limit or 500), 5000))
        offset_int = max(0, int(offset or 0))
        where_parts = []
        params = []

        # Изоляция отделов: обычный супервайзер видит тех. причины только своего
        # отдела; админ/супер-админ — все. Без отдела СВ не видит ничего (а не всё).
        if not role_has_min(role_norm, 'admin'):
            scope_dept = int(scope_department_id) if scope_department_id is not None else self.get_user_department_id(requester_id)
            if scope_dept is None:
                return {'total': 0, 'items': []}
            where_parts.append("op.department_id = %s")
            params.append(scope_dept)

        if issue_date:
            issue_date_obj = self._parse_technical_issue_date(issue_date, field_name='date')
            where_parts.append("ti.issue_date = %s")
            params.append(issue_date_obj)
        else:
            if date_from:
                from_obj = self._parse_technical_issue_date(date_from, field_name='date_from')
                where_parts.append("ti.issue_date >= %s")
                params.append(from_obj)
            if date_to:
                to_obj = self._parse_technical_issue_date(date_to, field_name='date_to')
                where_parts.append("ti.issue_date <= %s")
                params.append(to_obj)

        operator_id_int = None
        if operator_id is not None and str(operator_id).strip() != '':
            try:
                operator_id_int = int(operator_id)
            except (TypeError, ValueError):
                raise ValueError("Invalid operator_id")
            where_parts.append("ti.operator_id = %s")
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
                    WHERE gom.operator_id = ti.operator_id
                      AND gom.group_id = %s
                      AND gom.start_date <= ti.issue_date
                      AND (gom.end_date IS NULL OR gom.end_date >= ti.issue_date)
                )
            """)
            params.append(group_id_int)

        reason_text = str(reason or '').strip()
        if reason_text:
            if reason_text not in TECHNICAL_ISSUE_REASONS_SET:
                raise ValueError("Invalid reason filter")
            where_parts.append("ti.reason = %s")
            params.append(reason_text)

        workplace_number_int = self._parse_technical_issue_workplace_number(
            workplace_number,
            field_name='workplace_number',
            required=False
        )
        if workplace_number_int is not None:
            where_parts.append("ti.workplace_number = %s")
            params.append(workplace_number_int)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        base_sql = """
            FROM operator_technical_issues ti
            JOIN users op ON op.id = ti.operator_id
            LEFT JOIN users sv ON sv.id = op.supervisor_id
            LEFT JOIN users cb ON cb.id = ti.created_by
            LEFT JOIN directions d ON d.id = op.direction_id
        """

        with self._get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) {base_sql} {where_sql}", params)
            total = int(cursor.fetchone()[0] or 0)

            cursor.execute(
                f"""
                    SELECT
                        ti.id,
                        ti.batch_id::text,
                        ti.operator_id,
                        op.name,
                        op.supervisor_id,
                        sv.name,
                        op.direction_id,
                        d.name,
                        ti.issue_date,
                        ti.start_time,
                        ti.end_time,
                        ti.reason,
                        ti.comment,
                        ti.workplace_number,
                        ti.for_supervisor,
                        ti.created_by,
                        cb.name,
                        ti.created_at,
                        ti.direction_ids,
                        (
                            SELECT COUNT(*)
                            FROM operator_technical_issues batch_rows
                            WHERE batch_rows.batch_id = ti.batch_id
                        ) AS batch_record_count,
                        (
                            SELECT COUNT(DISTINCT batch_rows.operator_id)
                            FROM operator_technical_issues batch_rows
                            WHERE batch_rows.batch_id = ti.batch_id
                        ) AS batch_operator_count,
                        ti.requested_issue_date,
                        ti.requested_start_time,
                        ti.requested_end_time,
                        ti.workplace_office
                    {base_sql}
                    {where_sql}
                    ORDER BY ti.issue_date DESC, ti.start_time DESC, ti.created_at DESC, ti.id DESC
                    LIMIT %s OFFSET %s
                """,
                params + [limit_int, offset_int]
            )
            rows = cursor.fetchall()

            all_selected_direction_ids = set()
            prepared_rows = []
            for row in rows:
                selected_direction_ids = self._normalize_direction_ids_json_payload(row[18])
                all_selected_direction_ids.update(selected_direction_ids)
                prepared_rows.append((row, selected_direction_ids))

            direction_name_map = self._get_direction_name_map_tx(cursor, list(all_selected_direction_ids))
            items = []
            for row, selected_direction_ids in prepared_rows:
                start_time_text = row[9].strftime('%H:%M') if row[9] else None
                end_time_text = row[10].strftime('%H:%M') if row[10] else None
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
                    'reason': row[11],
                    'comment': row[12],
                    'workplace_number': int(row[13]) if row[13] is not None else None,
                    'for_supervisor': bool(row[14]) if row[14] is not None else False,
                    'created_by_id': int(row[15]) if row[15] is not None else None,
                    'created_by_name': row[16],
                    'created_at': row[17].strftime('%Y-%m-%d %H:%M:%S') if row[17] else None,
                    'selected_direction_ids': selected_direction_ids,
                    'selected_direction_names': [direction_name_map.get(dir_id) for dir_id in selected_direction_ids if direction_name_map.get(dir_id)],
                    'batch_record_count': int(row[19] or 0),
                    'batch_operator_count': int(row[20] or 0),
                    'requested_issue_date': row[21].strftime('%Y-%m-%d') if row[21] else None,
                    'requested_start_time': row[22].strftime('%H:%M') if row[22] else None,
                    'requested_end_time': row[23].strftime('%H:%M') if row[23] else None,
                    'workplace_office': row[24] or 'support'
                })

            return {
                'total': total,
                'items': items
            }

    def delete_operator_technical_issue(self, requester_id, requester_role, issue_id, scope_department_id=None):
        role_norm = self._normalize_technical_issue_role(requester_role)
        if not (role_has_min(role_norm, 'admin') or role_norm == 'sv'):
            raise ValueError("Only admin and sv can delete technical issues")

        requester_id_int = int(requester_id)
        try:
            issue_id_int = int(issue_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid technical issue id")
        if issue_id_int <= 0:
            raise ValueError("Invalid technical issue id")

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    ti.id,
                    ti.batch_id::text,
                    ti.operator_id,
                    op.supervisor_id,
                    op.department_id,
                    ti.created_by,
                    ti.issue_date,
                    ti.start_time,
                    ti.end_time,
                    ti.reason,
                    ti.direction_ids
                FROM operator_technical_issues ti
                JOIN users op ON op.id = ti.operator_id
                WHERE ti.id = %s
                """,
                (issue_id_int,)
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Technical issue not found")

            delete_as_batch = bool(self._normalize_direction_ids_json_payload(row[10]))

            access_rows = [(row[3], row[4], row[5])]
            if delete_as_batch:
                cursor.execute(
                    """
                    SELECT op.supervisor_id, op.department_id, ti.created_by
                    FROM operator_technical_issues ti
                    JOIN users op ON op.id = ti.operator_id
                    WHERE ti.batch_id = %s::uuid
                    """,
                    (row[1],)
                )
                access_rows = cursor.fetchall() or []

            if role_norm == 'sv':
                for supervisor_id, operator_department_id, issue_created_by in access_rows:
                    can_manage_by_scope = (
                        int(operator_department_id or 0) == int(scope_department_id)
                        if scope_department_id is not None
                        else int(supervisor_id or 0) == requester_id_int
                    )
                    can_manage_by_creator = int(issue_created_by or 0) == requester_id_int
                    if not (can_manage_by_scope or can_manage_by_creator):
                        raise PermissionError("Forbidden")

            if delete_as_batch:
                cursor.execute(
                    "DELETE FROM operator_technical_issues WHERE batch_id = %s::uuid RETURNING id",
                    (row[1],)
                )
            else:
                cursor.execute(
                    "DELETE FROM operator_technical_issues WHERE id = %s RETURNING id",
                    (issue_id_int,)
                )
            deleted_rows = cursor.fetchall() or []
            if not deleted_rows:
                raise ValueError("Technical issue not found")

            return {
                'id': int(row[0]),
                'batch_id': row[1],
                'operator_id': int(row[2]),
                'date': row[6].strftime('%Y-%m-%d') if row[6] else None,
                'start_time': row[7].strftime('%H:%M') if row[7] else None,
                'end_time': row[8].strftime('%H:%M') if row[8] else None,
                'reason': row[9],
                'deleted_count': len(deleted_rows),
                'deleted_as_batch': delete_as_batch
            }

    def _load_technical_issue_segments_for_operators(self, cursor, operator_ids, start_date_obj=None, end_date_obj=None):
        op_ids = [int(v) for v in (operator_ids or []) if v is not None]
        result = {op_id: {} for op_id in op_ids}
        if not op_ids:
            return result

        query = """
            SELECT
                id,
                operator_id,
                batch_id,
                issue_date,
                start_time,
                end_time,
                reason,
                comment,
                for_supervisor
            FROM operator_technical_issues
            WHERE operator_id = ANY(%s)
        """
        params = [op_ids]
        if start_date_obj:
            query += " AND issue_date >= %s"
            params.append(start_date_obj - timedelta(days=1))
        if end_date_obj:
            query += " AND issue_date <= %s"
            params.append(end_date_obj + timedelta(days=1))

        query += " ORDER BY operator_id, issue_date, start_time, end_time, id"
        cursor.execute(query, params)
        rows = cursor.fetchall() or []

        for row in rows:
            issue_id, operator_id, batch_id, issue_date_value, start_time_value, end_time_value, reason, comment, for_supervisor = row
            op_id = int(operator_id)
            if op_id not in result or issue_date_value is None:
                continue

            start_min, end_min = self._schedule_interval_minutes(start_time_value, end_time_value)
            day_chunks = self._split_absolute_minutes_intervals_by_day(
                [{'start': int(start_min), 'end': int(end_min)}],
                issue_date_value
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
                    'id': int(issue_id),
                    'batch_id': str(batch_id) if batch_id is not None else None,
                    'date': day_key,
                    'start': chunk.get('start_time') or _minutes_to_time(start_min_local),
                    'end': chunk.get('end_time') or _minutes_to_time(end_min_local),
                    'startMin': start_min_local,
                    'endMin': end_min_local,
                    'reason': str(reason or ''),
                    'comment': str(comment or ''),
                    'forSupervisor': bool(for_supervisor) if for_supervisor is not None else False
                })

        for op_id, days_map in result.items():
            for day_key, items in list(days_map.items()):
                items_sorted = sorted(
                    (items or []),
                    key=lambda seg: (int(seg.get('startMin', 0)), int(seg.get('endMin', 0)), int(seg.get('id', 0)))
                )
                days_map[day_key] = items_sorted

        return result

    def _attach_technical_issue_segments_to_operators(self, cursor, operators_map, operator_ids, start_date_obj=None, end_date_obj=None):
        timeline_map = self._load_technical_issue_segments_for_operators(
            cursor=cursor,
            operator_ids=operator_ids,
            start_date_obj=start_date_obj,
            end_date_obj=end_date_obj
        )
        for op_id in (operator_ids or []):
            target = operators_map.get(op_id)
            if target is None:
                continue
            target['technicalIssueTimelineDays'] = timeline_map.get(int(op_id), {})
