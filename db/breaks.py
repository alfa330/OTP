"""База данных: перерывы.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _time_to_minutes,
    datetime,
    dt_time,
    json,
    math,
    SCHEDULE_AUTO_BREAK_STATUS_KEYS,
    SHIFT_BREAK_EVEN_WINDOW_SHARE,
    SHIFT_BREAK_HANDOVER_OVERLAP_MINUTES,
    SHIFT_BREAK_LAYOUT_PASSES,
    SHIFT_BREAK_MAX_CONCURRENT,
    SHIFT_BREAK_MIN_EDGE_MARGIN_MINUTES,
    SHIFT_BREAK_MIN_GAP_MINUTES,
    SHIFT_BREAK_PAIR_OVERLAP_BASE_MINUTES,
    SHIFT_BREAK_PAIR_OVERLAP_SHARE,
    SHIFT_BREAK_PLANNING_BUFFER_MINUTES,
    SHIFT_BREAK_WEIGHT_CONCURRENCY,
    SHIFT_BREAK_WEIGHT_CROSS_GAP,
    SHIFT_BREAK_WEIGHT_EVENNESS,
    SHIFT_BREAK_WEIGHT_KEEP_PLACE,
    SHIFT_BREAK_WEIGHT_OVERLAP,
    SHIFT_BREAK_WEIGHT_PAIR_EXCESS,
    timedelta,
)


class BreaksMixin:
    @staticmethod
    def _break_overlaps_minute_range(brk, range_start_min, range_end_min):
        """True if a break {start,end} (in minutes, night-wrap aware) overlaps a range."""
        if not isinstance(brk, dict):
            return False
        try:
            b_start = int(brk.get('start'))
            b_end = int(brk.get('end'))
        except Exception:
            return False
        if b_end <= b_start:
            b_end += 24 * 60
        if range_end_min > 24 * 60 and b_start < range_start_min:
            b_start += 24 * 60
            b_end += 24 * 60
        return b_start < range_end_min and range_start_min < b_end

    # --- Выходы на перерыв мимо графика (задача #114) ---------------------------------------

    def get_planned_breaks_for_days(self, operator_ids, days):
        """Плановые перерывы и интервалы смен по (оператор, дата).

        Возвращает (breaks, shifts), у обоих ключ — (operator_id, 'YYYY-MM-DD'):
          breaks — [(start_minutes, end_minutes), ...] плановых перерывов;
          shifts — [(start_minutes, end_minutes), ...] самих смен.

        Минуты считаются от полуночи ДАТЫ СМЕНЫ и у ночной смены уходят за 1440 — так их
        хранит shift_breaks, и так же их разворачивает сверка (ночной хвост вчерашней смены
        приезжает вместе с датой минус сутки). Поэтому и смена возвращается интервалом, а не
        просто датой: «человек на смене» у ночной смены нельзя выяснить по календарному дню —
        перерыв в 13:36 и хвост смены, кончившейся в 02:00, приходятся на одну и ту же дату."""
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        day_objs = sorted({self._normalize_schedule_date(day) for day in (days or [])})
        if not op_ids or not day_objs:
            return {}, {}
        breaks = {}
        shifts = {}
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT ws.operator_id, ws.shift_date, ws.start_time, ws.end_time,
                       sb.start_minutes, sb.end_minutes
                FROM work_shifts ws
                LEFT JOIN shift_breaks sb ON sb.shift_id = ws.id
                WHERE ws.operator_id = ANY(%s) AND ws.shift_date = ANY(%s)
            """, (op_ids, day_objs))
            for row in (cursor.fetchall() or []):
                operator_id, shift_date, start_time, end_time, start_minutes, end_minutes = row
                key = (int(operator_id), shift_date.strftime('%Y-%m-%d'))
                if start_time is not None and end_time is not None:
                    shift_start = start_time.hour * 60 + start_time.minute
                    shift_end = end_time.hour * 60 + end_time.minute
                    if shift_end <= shift_start:
                        shift_end += 1440   # смена через полночь
                    interval = (shift_start, shift_end)
                    if interval not in shifts.setdefault(key, []):
                        shifts[key].append(interval)
                if start_minutes is None or end_minutes is None:
                    continue
                start_value, end_value = int(start_minutes), int(end_minutes)
                if end_value > start_value:
                    breaks.setdefault(key, []).append((start_value, end_value))
        for value in breaks.values():
            value.sort()
        for value in shifts.values():
            value.sort()
        return breaks, shifts

    def get_phone_break_episodes(self, operator_ids, window_from, window_to,
                                 status_keys=None) -> list:
        """Эпизоды перерыва по событиям телефона: [{operator_id, started_at, ended_at}].

        Второй источник той же сверки «перерывы вне графика»: у «Линии» СЗоВ факт берётся
        из истории статусов Oktell, у «Чата» — из событий Chat2Desk, у Тез КЦ — отсюда, из
        событий iCORE Phone, которые телефон присылает нам сам.

        Конец эпизода — время СЛЕДУЮЩЕГО события того же оператора (LEAD), поэтому фильтр
        по статусу навешивается СНАРУЖИ подзапроса: изнутри «следующим» оказался бы
        следующий перерыв, а не возврат на линию. Открытый эпизод (человек всё ещё на
        перерыве) приезжает с пустым ended_at — это нормально, длительность уточнит
        следующий заход разбора.

        Берём ТОЛЬКО живые события телефона (client_event_id IS NOT NULL). В той же
        таблице лежит ночная выгрузка кабинета Binotel, и она размечает ПРОШЕДШИЕ сутки
        целиком: её «break in work» приехал бы сюда эпизодом в полсмены и сделал бы
        нарушением каждый обед. Служебные события ('action') в ленту статусов не входят по
        той же причине, что и в пересборку сегментов.
        """
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        keys = sorted({self._normalize_import_status_key(key)
                       for key in (status_keys or SCHEDULE_AUTO_BREAK_STATUS_KEYS)
                       if self._normalize_import_status_key(key)})
        if not op_ids or not keys or window_from is None or window_to is None:
            return []
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT x.operator_id, x.event_at, x.next_at
                FROM (
                    SELECT e.operator_id, e.status_key, e.event_at,
                           LEAD(e.event_at) OVER (PARTITION BY e.operator_id
                                                  ORDER BY e.event_at, e.id) AS next_at
                    FROM operator_status_events e
                    WHERE e.operator_id = ANY(%s)
                      AND e.event_at >= %s
                      AND e.event_at < %s
                      AND e.client_event_id IS NOT NULL
                      AND e.event_kind = 'status'
                ) x
                WHERE regexp_replace(lower(btrim(x.status_key)), '\\s+', ' ', 'g') = ANY(%s)
                ORDER BY x.operator_id, x.event_at
                """,
                (op_ids, window_from, window_to, keys)
            )
            rows = cursor.fetchall() or []
        return [{'operator_id': int(row[0]), 'started_at': row[1], 'ended_at': row[2]}
                for row in rows]

    def save_szov_break_violations(self, rows) -> int:
        """Записать найденные нарушения. Возвращает число НОВЫХ строк.

        Разбор идёт с перекрытием окон (перерыв, начавшийся в 12:58, во время первой проверки
        мог ещё длиться), поэтому запись обязана быть идемпотентной: повтор того же перерыва
        не создаёт второе нарушение, а лишь уточняет конец и длительность. `reported_at` при
        этом не трогаем — о чём уже написали, о том второй раз не пишем."""
        payload = []
        for row in (rows or []):
            try:
                payload.append((
                    int(row['operator_id']),
                    self._normalize_schedule_date(row['violation_date']),
                    row['started_at'],
                    row.get('ended_at'),
                    None if row.get('duration_minutes') is None else int(row['duration_minutes']),
                    str(row.get('kind') or 'off_schedule'),
                    None if row.get('planned_start_minutes') is None else int(row['planned_start_minutes']),
                    None if row.get('planned_end_minutes') is None else int(row['planned_end_minutes']),
                    None if row.get('deviation_minutes') is None else int(row['deviation_minutes']),
                    str(row.get('direction') or 'line'),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        if not payload:
            return 0
        inserted = 0
        with self._get_cursor() as cursor:
            for item in payload:
                cursor.execute("""
                    INSERT INTO szov_break_violations
                        (operator_id, violation_date, started_at, ended_at, duration_minutes,
                         kind, planned_start_minutes, planned_end_minutes, deviation_minutes,
                         direction)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (operator_id, started_at) DO UPDATE
                    SET ended_at = EXCLUDED.ended_at,
                        duration_minutes = EXCLUDED.duration_minutes
                    RETURNING (xmax = 0)
                """, item)
                result = cursor.fetchone()
                if result and result[0]:
                    inserted += 1
        return inserted

    @staticmethod
    def _szov_break_violation_row(row):
        return {
            "id": row[0],
            "operator_id": row[1],
            "operator_name": row[2] or "",
            "violation_date": row[3].strftime('%Y-%m-%d') if row[3] else None,
            "started_at": row[4].isoformat(timespec='seconds') if row[4] else None,
            "ended_at": row[5].isoformat(timespec='seconds') if row[5] else None,
            "duration_minutes": row[6],
            "kind": row[7],
            "planned_start_minutes": row[8],
            "planned_end_minutes": row[9],
            "deviation_minutes": row[10],
            "detected_at": row[11].isoformat(timespec='seconds') if row[11] else None,
            "reported_at": row[12].isoformat(timespec='seconds') if row[12] else None,
            "direction": row[13],
        }

    def get_szov_break_violations(self, date_from=None, date_to=None, direction=None,
                                  limit: int = 500) -> list:
        """Нарушения за период — журнал для раздела «Табло СЗоВ».

        direction сужает выборку до одного направления: у «Линии» и «Чата» свои экраны,
        и мешать их в одном списке нельзя — источники разные и люди разные."""
        limit = max(1, min(int(limit or 500), 2000))
        clauses, params = [], []
        if direction:
            clauses.append("v.direction = %s")
            params.append(str(direction))
        if date_from:
            clauses.append("v.violation_date >= %s")
            params.append(self._normalize_schedule_date(date_from))
        if date_to:
            clauses.append("v.violation_date <= %s")
            params.append(self._normalize_schedule_date(date_to))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {self._SZOV_BREAK_VIOLATION_COLUMNS}
                FROM szov_break_violations v
                LEFT JOIN users u ON u.id = v.operator_id
                {where}
                ORDER BY v.started_at DESC, v.id DESC
                LIMIT %s
            """, tuple(params))
            rows = cursor.fetchall() or []
        return [self._szov_break_violation_row(row) for row in rows]

    def get_szov_break_violations_for_hour(self, day, hour, direction='line') -> list:
        """Нарушения одного часа — срез для команды «/tablo ЧЧ:ММ» и предпросмотра."""
        day_obj = self._normalize_schedule_date(day)
        start = datetime.combine(day_obj, dt_time(hour=max(0, min(23, int(hour)))))
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {self._SZOV_BREAK_VIOLATION_COLUMNS}
                FROM szov_break_violations v
                LEFT JOIN users u ON u.id = v.operator_id
                WHERE v.started_at >= %s AND v.started_at < %s AND v.direction = %s
                ORDER BY v.started_at, v.id
            """, (start, start + timedelta(hours=1), str(direction)))
            rows = cursor.fetchall() or []
        return [self._szov_break_violation_row(row) for row in rows]

    def get_unreported_szov_break_violations(self, max_age_hours: int = 24, now=None,
                                             direction='line') -> list:
        """Нарушения, о которых отбивка ещё не писала, — не старше max_age_hours.

        Ограничение по возрасту обязательно: если получателей отбивки не было неделю,
        первое же сообщение вывалило бы сотни строк за всю неделю. Направление обязательно:
        отбивка «Линии» не должна забирать нарушения чатников — о них напишет отбивка «Чата»,
        и наоборот."""
        moment = now or datetime.now()
        since = moment - timedelta(hours=max(1, int(max_age_hours or 24)))
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {self._SZOV_BREAK_VIOLATION_COLUMNS}
                FROM szov_break_violations v
                LEFT JOIN users u ON u.id = v.operator_id
                WHERE v.reported_at IS NULL AND v.started_at >= %s AND v.direction = %s
                ORDER BY v.started_at, v.id
            """, (since, str(direction)))
            rows = cursor.fetchall() or []
        return [self._szov_break_violation_row(row) for row in rows]

    def save_szov_break_unscheduled_day(self, direction, day, operators) -> None:
        """Сколько человек за день работали, но смены в графике у них нет.

        Их перерывы сверять не с чем, и по решению владельца в отбивку они не идут. Но и
        молчать совсем нельзя: журнал показывает это счётчиком, иначе пустой список читался
        бы как «нарушений нет», хотя на самом деле проверка просто не работала."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO szov_break_unscheduled_days (direction, day, operators, updated_at)
                VALUES (%s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                ON CONFLICT (direction, day) DO UPDATE
                SET operators = EXCLUDED.operators,
                    updated_at = EXCLUDED.updated_at
            """, (str(direction), self._normalize_schedule_date(day), max(0, int(operators or 0))))

    def get_szov_break_unscheduled_days(self, direction, date_from=None, date_to=None) -> list:
        """Дни периода, в которые кто-то работал без смены в графике."""
        clauses = ["direction = %s"]
        params = [str(direction)]
        if date_from:
            clauses.append("day >= %s")
            params.append(self._normalize_schedule_date(date_from))
        if date_to:
            clauses.append("day <= %s")
            params.append(self._normalize_schedule_date(date_to))
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT day, operators FROM szov_break_unscheduled_days
                WHERE {' AND '.join(clauses)} AND operators > 0
                ORDER BY day DESC
            """, tuple(params))
            rows = cursor.fetchall() or []
        return [{"day": row[0].strftime('%Y-%m-%d'), "operators": int(row[1])} for row in rows]

    def mark_szov_break_violations_reported(self, violation_ids) -> int:
        """Отметить, что об этих нарушениях отбивка уже написала."""
        ids = sorted({int(v) for v in (violation_ids or []) if v is not None})
        if not ids:
            return 0
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE szov_break_violations
                SET reported_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE id = ANY(%s) AND reported_at IS NULL
            """, (ids,))
            return cursor.rowcount or 0

    def _normalize_break_durations_list(self, value):
        if value is None:
            return []
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(',')]
            raw_items = [p for p in parts if p]
        elif isinstance(value, list):
            raw_items = value
        else:
            raise ValueError("break_durations must be a list of minutes")

        result = []
        for item in raw_items:
            try:
                minutes = int(item)
            except Exception:
                raise ValueError("break_durations must contain integer minutes")
            if minutes <= 0:
                raise ValueError("break_durations values must be > 0")
            if minutes > 240:
                raise ValueError("break_durations values must be <= 240")
            result.append(minutes)
        return result

    def _normalize_break_rule_ranges(self, rules):
        if rules is None:
            return []
        if not isinstance(rules, list):
            raise ValueError("rules must be a list")

        normalized = []

        def _to_minutes(item, minutes_key, minutes_key_alt, hours_key, hours_key_alt):
            if item.get(minutes_key) is not None:
                return int(item.get(minutes_key))
            if item.get(minutes_key_alt) is not None:
                return int(item.get(minutes_key_alt))
            if item.get(hours_key) is not None:
                return int(round(float(item.get(hours_key)) * 60))
            if item.get(hours_key_alt) is not None:
                return int(round(float(item.get(hours_key_alt)) * 60))
            return None

        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError("Each rule must be an object")
            min_minutes = _to_minutes(rule, 'min_minutes', 'minMinutes', 'min_hours', 'minHours')
            max_minutes = _to_minutes(rule, 'max_minutes', 'maxMinutes', 'max_hours', 'maxHours')
            if min_minutes is None or max_minutes is None:
                raise ValueError("Each rule must contain min/max hours or minutes")
            if min_minutes < 0:
                raise ValueError("min_minutes must be >= 0")
            if max_minutes <= min_minutes:
                raise ValueError("max_minutes must be > min_minutes")
            break_durations = self._normalize_break_durations_list(
                rule.get('break_durations')
                if 'break_durations' in rule else (
                    rule.get('breakDurations')
                    if 'breakDurations' in rule else rule.get('durations')
                )
            )
            normalized.append({
                'minMinutes': int(min_minutes),
                'maxMinutes': int(max_minutes),
                'breakDurations': break_durations
            })

        normalized.sort(key=lambda x: (x['minMinutes'], x['maxMinutes']))
        prev = None
        for cur in normalized:
            if prev is not None and cur['minMinutes'] < prev['maxMinutes']:
                raise ValueError("Break rules ranges must not overlap within one direction")
            prev = cur
        return normalized

    def _normalize_break_cross_operator_gap(self, value):
        """
        Минимальный промежуток между перерывами РАЗНЫХ операторов одного
        направления, в минутах. None/'' -> None (значение не передано),
        иначе целое 0..240 (0 = требования нет).
        """
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        try:
            minutes = int(round(float(value)))
        except Exception:
            raise ValueError("cross_operator_gap_minutes must be an integer number of minutes")
        if minutes < 0:
            raise ValueError("cross_operator_gap_minutes must be >= 0")
        if minutes > 240:
            raise ValueError("cross_operator_gap_minutes must be <= 240")
        return minutes

    def _normalize_break_allow_overlap(self, value):
        """
        Галочка «Разрешить пересечение» направления. None — значение не передано
        (настройка остаётся прежней), иначе True/False.
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        text = str(value).strip().lower()
        if text in ('true', '1'):
            return True
        if text in ('false', '0'):
            return False
        raise ValueError("allowOverlap must be true or false")

    def get_work_schedule_break_rules(self, department_id=None):
        """
        Возвращает правила автоперерывов по направлениям.
        Если задан department_id — только правила направлений этого отдела
        (изоляция отделов для настроек перерывов у супервайзера); правила
        сопоставляются с отделом по имени направления (directions.name).
        Формат:
        [
          {
            "direction": "Название",
            "rules": [
              {"minMinutes": 360, "maxMinutes": 540, "breakDurations": [30]}
            ],
            "crossOperatorGapMinutes": 10,
            "allowOverlap": false
          }
        ]
        Направление попадает в ответ, если у него есть правила, задан
        промежуток между перерывами разных операторов или стоит галочка
        «Разрешить пересечение» (allowOverlap).
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT direction_name, min_shift_minutes, max_shift_minutes, break_durations_json
                FROM work_schedule_break_rules
                WHERE (%(dept)s IS NULL OR LOWER(TRIM(direction_name)) IN (
                    SELECT LOWER(TRIM(d.name)) FROM directions d
                    WHERE d.department_id = %(dept)s
                ))
                ORDER BY LOWER(direction_name), min_shift_minutes, max_shift_minutes, id
            """, {'dept': department_id})
            rows = cursor.fetchall() or []

            cursor.execute("""
                SELECT direction_name, cross_operator_gap_minutes, allow_break_overlap
                FROM work_schedule_break_direction_settings
                WHERE (%(dept)s IS NULL OR LOWER(TRIM(direction_name)) IN (
                    SELECT LOWER(TRIM(d.name)) FROM directions d
                    WHERE d.department_id = %(dept)s
                ))
                ORDER BY LOWER(direction_name), id
            """, {'dept': department_id})
            gap_rows = cursor.fetchall() or []

        by_direction = {}
        for direction_name, min_shift_minutes, max_shift_minutes, break_durations_json in rows:
            direction_label = str(direction_name or '').strip()
            if not direction_label:
                continue
            item = by_direction.setdefault(
                direction_label,
                {"direction": direction_label, "rules": [], "crossOperatorGapMinutes": 0, "allowOverlap": False}
            )
            item["rules"].append({
                "minMinutes": int(min_shift_minutes),
                "maxMinutes": int(max_shift_minutes),
                "breakDurations": self._normalize_break_durations_list(break_durations_json)
            })

        for direction_name, cross_operator_gap_minutes, allow_break_overlap in gap_rows:
            direction_label = str(direction_name or '').strip()
            if not direction_label:
                continue
            gap_minutes = max(0, int(cross_operator_gap_minutes or 0))
            allow_overlap = bool(allow_break_overlap)
            item = by_direction.get(direction_label)
            if item is None:
                if gap_minutes <= 0 and not allow_overlap:
                    continue
                item = by_direction.setdefault(
                    direction_label,
                    {"direction": direction_label, "rules": [], "crossOperatorGapMinutes": 0, "allowOverlap": False}
                )
            item["crossOperatorGapMinutes"] = gap_minutes
            item["allowOverlap"] = allow_overlap

        result = list(by_direction.values())
        result.sort(key=lambda x: str(x.get('direction') or '').lower())
        return result

    def get_work_schedule_break_cross_operator_gaps_map(self):
        """
        Возвращает map normalized direction key -> минимальный промежуток
        между перерывами разных операторов направления (минуты).
        Направления без настройки в map не попадают.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT direction_name, cross_operator_gap_minutes
                FROM work_schedule_break_direction_settings
                WHERE cross_operator_gap_minutes > 0
            """)
            rows = cursor.fetchall() or []

        result = {}
        for direction_name, cross_operator_gap_minutes in rows:
            key = self._normalize_direction_key(direction_name)
            if not key:
                continue
            result[key] = max(0, int(cross_operator_gap_minutes or 0))
        return result

    def _get_break_cross_operator_gap_for_direction_tx(self, cursor, direction_name):
        direction_label = str(direction_name or '').strip()
        if not direction_label:
            return 0
        cursor.execute(
            """
            SELECT cross_operator_gap_minutes
            FROM work_schedule_break_direction_settings
            WHERE LOWER(TRIM(direction_name)) = LOWER(TRIM(%s))
            LIMIT 1
            """,
            (direction_label,)
        )
        row = cursor.fetchone()
        if not row:
            return 0
        return max(0, int(row[0] or 0))

    def get_work_schedule_break_overlap_directions(self):
        """
        Направления с галочкой «Разрешить пересечение» — множество normalized
        direction key. Перерывы их операторов могут совпадать по времени.
        """
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT direction_name
                FROM work_schedule_break_direction_settings
                WHERE allow_break_overlap
            """)
            rows = cursor.fetchall() or []

        result = set()
        for (direction_name,) in rows:
            key = self._normalize_direction_key(direction_name)
            if key:
                result.add(key)
        return result

    def _break_overlap_allowed_for_direction_tx(self, cursor, direction_name):
        """Стоит ли у направления галочка «Разрешить пересечение»."""
        direction_label = str(direction_name or '').strip()
        if not direction_label:
            return False
        cursor.execute(
            """
            SELECT allow_break_overlap
            FROM work_schedule_break_direction_settings
            WHERE LOWER(TRIM(direction_name)) = LOWER(TRIM(%s))
            LIMIT 1
            """,
            (direction_label,)
        )
        row = cursor.fetchone()
        return bool(row and row[0])

    def get_work_schedule_break_rules_map(self):
        """
        Возвращает map по normalized direction key -> rules list.
        """
        result = {}
        for item in (self.get_work_schedule_break_rules() or []):
            direction = str(item.get('direction') or '').strip()
            key = self._normalize_direction_key(direction)
            if not key:
                continue
            result[key] = [dict(rule) for rule in (item.get('rules') or []) if isinstance(rule, dict)]
        return result

    def _get_work_schedule_break_rules_for_direction_tx(self, cursor, direction_name):
        direction_label = str(direction_name or '').strip()
        if not direction_label:
            return []
        cursor.execute("""
            SELECT min_shift_minutes, max_shift_minutes, break_durations_json
            FROM work_schedule_break_rules
            WHERE LOWER(direction_name) = LOWER(%s)
            ORDER BY min_shift_minutes, max_shift_minutes, id
        """, (direction_label,))
        rows = cursor.fetchall() or []
        return [
            {
                "minMinutes": int(min_shift_minutes),
                "maxMinutes": int(max_shift_minutes),
                "breakDurations": self._normalize_break_durations_list(break_durations_json)
            }
            for min_shift_minutes, max_shift_minutes, break_durations_json in rows
        ]

    def save_work_schedule_break_rules(self, direction_rules):
        """
        Сохранить правила автоперерывов по направлениям.
        direction_rules:
        [
          {
            "direction": "Название",
            "rules": [{"minMinutes": 360, "maxMinutes": 540, "breakDurations": [30]}],
            "crossOperatorGapMinutes": 10,
            "allowOverlap": false
          }
        ]
        Для направления переданные rules полностью заменяют старые.
        crossOperatorGapMinutes необязателен: если ключа нет — текущее значение
        промежутка между перерывами разных операторов остаётся без изменений
        (чтобы старый клиент не обнулял настройку молча), 0 — выключить.
        allowOverlap («Разрешить пересечение») так же необязателен. Когда он
        включён, промежуток не хранится: операторы направления друг другу не
        мешают, и спрятанное в окне число не должно влиять на раскладку.
        """
        if not isinstance(direction_rules, list):
            raise ValueError("direction_rules must be a list")

        normalized_payload = []
        seen = set()
        for item in direction_rules:
            if not isinstance(item, dict):
                raise ValueError("Each direction_rules item must be an object")
            direction = str(item.get('direction') or item.get('direction_name') or '').strip()
            if not direction:
                raise ValueError("direction is required")
            direction_key = self._normalize_direction_key(direction)
            if not direction_key:
                raise ValueError("direction is required")
            if direction_key in seen:
                raise ValueError("Duplicate direction in direction_rules")
            seen.add(direction_key)
            rules = self._normalize_break_rule_ranges(item.get('rules'))
            cross_gap_raw = None
            for gap_key in ('crossOperatorGapMinutes', 'cross_operator_gap_minutes', 'crossOperatorGap'):
                if gap_key in item:
                    cross_gap_raw = item.get(gap_key)
                    break
            allow_overlap_raw = None
            for overlap_key in ('allowOverlap', 'allow_overlap', 'allow_break_overlap'):
                if overlap_key in item:
                    allow_overlap_raw = item.get(overlap_key)
                    break
            normalized_payload.append({
                'direction': direction,
                'rules': rules,
                'crossOperatorGapMinutes': self._normalize_break_cross_operator_gap(cross_gap_raw),
                'allowOverlap': self._normalize_break_allow_overlap(allow_overlap_raw)
            })

        with self._get_cursor() as cursor:
            for item in normalized_payload:
                direction = item['direction']
                cross_gap_minutes = item['crossOperatorGapMinutes']
                allow_overlap = item['allowOverlap']
                if cross_gap_minutes is not None or allow_overlap is not None:
                    # Чего не прислали — остаётся как было.
                    cursor.execute("""
                        SELECT cross_operator_gap_minutes, allow_break_overlap
                        FROM work_schedule_break_direction_settings
                        WHERE LOWER(TRIM(direction_name)) = LOWER(TRIM(%s))
                        LIMIT 1
                    """, (direction,))
                    current = cursor.fetchone()
                    if cross_gap_minutes is None:
                        cross_gap_minutes = max(0, int(current[0] or 0)) if current else 0
                    if allow_overlap is None:
                        allow_overlap = bool(current[1]) if current else False
                    if allow_overlap:
                        cross_gap_minutes = 0
                    cursor.execute("""
                        DELETE FROM work_schedule_break_direction_settings
                        WHERE LOWER(TRIM(direction_name)) = LOWER(TRIM(%s))
                    """, (direction,))
                    if cross_gap_minutes > 0 or allow_overlap:
                        cursor.execute("""
                            INSERT INTO work_schedule_break_direction_settings (
                                direction_name,
                                cross_operator_gap_minutes,
                                allow_break_overlap,
                                updated_at
                            )
                            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                        """, (direction, int(cross_gap_minutes), bool(allow_overlap)))
                cursor.execute("""
                    DELETE FROM work_schedule_break_rules
                    WHERE LOWER(direction_name) = LOWER(%s)
                """, (direction,))

                rows_to_insert = item['rules']
                if not rows_to_insert:
                    continue

                cursor.executemany("""
                    INSERT INTO work_schedule_break_rules (
                        direction_name,
                        min_shift_minutes,
                        max_shift_minutes,
                        break_durations_json,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s::jsonb, CURRENT_TIMESTAMP)
                """, [
                    (
                        direction,
                        int(rule['minMinutes']),
                        int(rule['maxMinutes']),
                        json.dumps(rule['breakDurations'], ensure_ascii=False)
                    )
                    for rule in rows_to_insert
                ])

        return self.get_work_schedule_break_rules()

    def _normalize_shift_breaks(self, breaks):
        if breaks is None:
            return []
        if not isinstance(breaks, list):
            raise ValueError("breaks must be a list")

        normalized = []
        seen = set()
        for item in breaks:
            if not isinstance(item, dict):
                raise ValueError("Each break must be an object")
            start_raw = item.get('start')
            end_raw = item.get('end')
            if start_raw is None or end_raw is None:
                raise ValueError("Each break must contain start and end")

            start_val = _time_to_minutes(start_raw) if isinstance(start_raw, str) else int(start_raw)
            end_val = _time_to_minutes(end_raw) if isinstance(end_raw, str) else int(end_raw)
            if end_val <= start_val:
                raise ValueError("Break end must be greater than break start")

            key = (start_val, end_val)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({'start': start_val, 'end': end_val})

        normalized.sort(key=lambda b: (b['start'], b['end']))
        return normalized

    def _pick_break_durations_for_shift(self, duration_minutes, direction_name=None, direction_rules=None):
        dur = int(duration_minutes)
        rules = direction_rules if isinstance(direction_rules, list) else []
        custom_rules = [r for r in rules if isinstance(r, dict)]
        selected_custom = None
        for rule in sorted(
            custom_rules,
            key=lambda x: (int(x.get('minMinutes', 0)), int(x.get('maxMinutes', 0)))
        ):
            try:
                min_minutes = int(rule.get('minMinutes'))
                max_minutes = int(rule.get('maxMinutes'))
            except Exception:
                continue
            if max_minutes <= min_minutes:
                continue
            if dur >= min_minutes and dur <= max_minutes:
                # При совпадении нескольких диапазонов берем с максимальным minMinutes
                # (например, 6-9 и 9-12 -> для 9ч сработает 9-12).
                if selected_custom is None or min_minutes >= int(selected_custom.get('minMinutes', -1)):
                    selected_custom = rule

        if selected_custom is not None:
            return self._normalize_break_durations_list(selected_custom.get('breakDurations'))
        if custom_rules:
            return []

        # Fallback: действующий базовый профиль + чат-менеджер по умолчанию.
        if self._is_chat_manager_direction(direction_name):
            if dur >= 6 * 60 and dur < 9 * 60:
                return [30]
            if dur >= 9 * 60 and dur <= 12 * 60:
                return [30, 30]

        if dur >= 5 * 60 and dur < 6 * 60:
            return [15]
        if dur >= 6 * 60 and dur < 8 * 60:
            return [15, 15]
        if dur >= 8 * 60 and dur < 11 * 60:
            return [15, 30, 15]
        if dur >= 11 * 60:
            return [15, 30, 15, 15]
        return []

    def _place_break_durations_centered_minutes(self, start_min, end_min, break_durations):
        """
        Раскладывает заданные длительности перерывов по центрам равных долей отрезка.
        Используется и для целой смены, и для остатка смены (когда прошедшие перерывы
        уже зафиксированы и планировать можно только будущее).
        """
        start_min = int(start_min)
        end_min = int(end_min)
        dur = end_min - start_min
        if dur <= 0:
            return []

        breaks = []

        def snap5(x):
            # Половину округляем вверх — ровно как Math.round в браузере,
            # иначе планировщик показывает перерыв на 5 минут левее сохранённого.
            return int(math.floor((float(x) / 5.0) + 0.5) * 5)

        def push_centered(center, size):
            center_snapped = snap5(center)
            s = snap5(center_snapped - (size / 2))
            e = s + int(size)
            s = max(start_min, min(end_min, s))
            e = max(start_min, min(end_min, e))
            if e > s:
                breaks.append({'start': s, 'end': e})

        durations = [int(x) for x in (break_durations or []) if int(x) > 0]
        if durations:
            count = len(durations)
            for idx, size in enumerate(durations):
                center = start_min + (dur * ((idx + 1) / (count + 1)))
                push_centered(center, int(size))

        normalized = []
        seen = set()
        for b in sorted(breaks, key=lambda x: (x['start'], x['end'])):
            key = (int(b['start']), int(b['end']))
            if key[1] <= key[0] or key in seen:
                continue
            seen.add(key)
            normalized.append({'start': key[0], 'end': key[1]})
        return normalized

    def _compute_auto_shift_breaks_minutes(self, start_min, end_min, direction_name=None, direction_rules=None):
        """
        Серверная автогенерация перерывов (зеркалит фронтенд-базовую логику по длительности смены).
        Возвращает список интервалов в минутах от начала дня; для ночных смен end_min может быть > 1440.
        """
        start_min = int(start_min)
        end_min = int(end_min)
        dur = end_min - start_min
        if dur <= 0:
            return []

        break_durations = self._pick_break_durations_for_shift(
            duration_minutes=dur,
            direction_name=direction_name,
            direction_rules=direction_rules
        )
        return self._place_break_durations_centered_minutes(start_min, end_min, break_durations)

    def _break_freeze_boundary_minutes(self, shift_date, seg_start=None, now=None):
        """
        «Граница прошлого» для даты смены в минутах от её полуночи: перерыв, начавшийся
        раньше границы, уже случился и автоматикой не переставляется.

        Для будущих дат граница отрицательна (замораживать нечего — поведение прежнее),
        для прошедших больше суток (весь день неизменен).

        seg_start нужен для ночных смен: кусок 00:00-08:00 может храниться на дате лота
        (то есть на «вчера» в системе координат минут). Без выравнивания шкалы такая
        смена целиком объявляется прошедшей и остаётся без перерывов.
        """
        date_obj = self._normalize_schedule_date(shift_date)
        now_dt = now if isinstance(now, datetime) else self._almaty_now()
        day_delta = (now_dt.date() - date_obj).days
        boundary = int(day_delta * 1440 + now_dt.hour * 60 + now_dt.minute)
        if now_dt.second:
            boundary += 1
        if seg_start is not None:
            while boundary - int(seg_start) >= 1440:
                boundary -= 1440
        return boundary

    def _normalize_break_intervals_soft(self, breaks, dedupe=True):
        """
        Мягкая нормализация списка перерывов: сортировка, отсев мусора и дублей.
        В отличие от _normalize_shift_breaks не кидает ошибку — применяется в том числе
        к данным, пришедшим от клиента, и сохранение смены не должно падать из-за одного
        битого интервала.

        dedupe=False — для списков, где интервалы принадлежат РАЗНЫМ операторам:
        два одинаковых перерыва там значат «двое на перерыве», и схлопывать их нельзя.
        """
        normalized = []
        seen = set()
        for item in (breaks or []):
            if not isinstance(item, dict):
                continue
            try:
                start = int(item.get('start'))
                end = int(item.get('end'))
            except (TypeError, ValueError):
                continue
            if end <= start or (dedupe and (start, end) in seen):
                continue
            seen.add((start, end))
            normalized.append({'start': start, 'end': end})
        normalized.sort(key=lambda b: (b['start'], b['end']))
        return normalized

    def _split_breaks_by_freeze_boundary(self, breaks, boundary_minutes, seg_start=None, seg_end=None):
        """
        Делит перерывы на три группы относительно границы прошлого:
        - frozen: прошедшие, попадающие в новый интервал смены (обрезаются по нему) —
          их нельзя двигать и удалять;
        - upcoming: будущие внутри интервала — их можно переставлять;
        - used: ВСЕ прошедшие, включая те, что в новый интервал не попали (смену
          подрезали или сдвинули начало). Они уже отсижены, поэтому обязаны
          вычитаться из нормы, даже если хранить их больше негде.
        """
        boundary = int(boundary_minutes)
        seg_start_value = None if seg_start is None else int(seg_start)
        seg_end_value = None if seg_end is None else int(seg_end)
        frozen = []
        upcoming = []
        used = []
        for item in self._normalize_break_intervals_soft(breaks):
            start = int(item['start'])
            end = int(item['end'])
            clipped_start = start if seg_start_value is None else max(start, seg_start_value)
            clipped_end = end if seg_end_value is None else min(end, seg_end_value)
            inside_segment_minutes = clipped_end - clipped_start
            if start < boundary:
                # В зачёт нормы идут перерывы этой смены. Перерывы соседних смен дня
                # (у дня бывает несколько отдельных кусков) считаются отдельно.
                if inside_segment_minutes <= 0:
                    continue
                used.append({'start': start, 'end': end})
                if inside_segment_minutes >= 5:
                    frozen.append({'start': clipped_start, 'end': clipped_end})
                continue
            if seg_start_value is not None and start < seg_start_value:
                continue
            if seg_end_value is not None and end > seg_end_value:
                continue
            upcoming.append({'start': start, 'end': end})
        return frozen, upcoming, used

    def _fit_break_durations_to_window(self, window_start, window_end, break_durations):
        """
        Оставляет из нормы только те перерывы, которые физически помещаются в окно
        планирования с приличными отступами от краёв и друг от друга. Лишние
        отбрасываются с конца: лучше меньше перерывов, чем перерыв впритык к концу
        смены или внахлёст с соседним.
        """
        window = max(0, int(window_end) - int(window_start))
        durations = [int(x) for x in (break_durations or []) if int(x) > 0]
        while durations:
            required = (
                sum(durations)
                + (2 * SHIFT_BREAK_MIN_EDGE_MARGIN_MINUTES)
                + (SHIFT_BREAK_MIN_GAP_MINUTES * (len(durations) - 1))
            )
            if required <= window:
                return durations
            durations.pop()
        return []

    def _seed_break_positions_from_existing(self, planned_breaks, existing_breaks):
        """
        Если перерыв такой же длительности уже стоит в графике и ещё не начался —
        оставляем его на прежнем месте. Правка смены не должна двигать то, что
        оператор уже видит у себя в расписании.
        """
        available = [dict(item) for item in (existing_breaks or [])]
        result = []
        for item in (planned_breaks or []):
            length = int(item['end']) - int(item['start'])
            match = next(
                (x for x in available if (int(x['end']) - int(x['start'])) == length),
                None
            )
            if match is not None:
                available.remove(match)
                result.append({'start': int(match['start']), 'end': int(match['end'])})
            else:
                result.append({'start': int(item['start']), 'end': int(item['end'])})
        result.sort(key=lambda b: (b['start'], b['end']))
        return result

    def _remaining_break_durations_after_used(self, break_durations, used_breaks):
        """
        Вычитает уже использованные перерывы из нормы смены по минутному бюджету:
        сколько минут перерыва положено минус сколько уже отсижено. Оставшиеся минуты
        набираются слотами нормы от больших к меньшим.

        Минуты, а не «слот за слот»: у направлений бывают нестандартные перерывы, и
        списание «первого попавшегося» слота выдавало бы больше нормы.
        """
        durations = [int(x) for x in (break_durations or []) if int(x) > 0]
        if not durations:
            return []

        used_lengths = []
        for used in (used_breaks or []):
            try:
                length = max(0, int(used.get('end', 0)) - int(used.get('start', 0)))
            except (TypeError, ValueError, AttributeError):
                continue
            if length > 0:
                used_lengths.append(length)
        if not used_lengths:
            return list(durations)

        # 1) Списываем слот такой же длительности: отсидел обед — остаются короткие.
        remaining = list(durations)
        for length in used_lengths:
            if length in remaining:
                remaining.remove(length)

        # 2) Общий минутный лимит: суммарно перерывов не больше нормы смены.
        budget = max(0, sum(durations) - sum(used_lengths))
        while remaining and sum(remaining) > budget:
            remaining.remove(max(remaining))
        return remaining

    def _insert_shift_breaks(self, cursor, shift_id, breaks):
        cursor.execute("DELETE FROM shift_breaks WHERE shift_id = %s", (shift_id,))
        if breaks:
            cursor.executemany(
                """
                INSERT INTO shift_breaks (shift_id, start_minutes, end_minutes)
                VALUES (%s, %s, %s)
                """,
                [(shift_id, b['start'], b['end']) for b in breaks]
            )

    def _load_shift_breaks_tx(self, cursor, shift_id):
        cursor.execute(
            """
            SELECT start_minutes, end_minutes
            FROM shift_breaks
            WHERE shift_id = %s
            ORDER BY start_minutes, end_minutes
            """,
            (int(shift_id),)
        )
        result = []
        for start_minutes, end_minutes in cursor.fetchall() or []:
            result.append({
                'start': int(start_minutes),
                'end': int(end_minutes)
            })
        return result

    def _load_day_shift_breaks_tx(self, cursor, operator_id, shift_date):
        """
        Все перерывы оператора на дату (по всем сменам дня) в минутах от её полуночи.

        Снимок нужно брать ДО удаления/пересборки смен дня: перерывы уезжают вместе
        со сменой, а уже прошедшие переписывать нельзя.
        """
        cursor.execute(
            """
            SELECT sb.start_minutes, sb.end_minutes
            FROM work_shifts ws
            JOIN shift_breaks sb ON sb.shift_id = ws.id
            WHERE ws.operator_id = %s AND ws.shift_date = %s
            ORDER BY sb.start_minutes, sb.end_minutes
            """,
            (int(operator_id), self._normalize_schedule_date(shift_date))
        )
        result = []
        for start_minutes, end_minutes in cursor.fetchall() or []:
            start_value = int(start_minutes)
            end_value = int(end_minutes)
            if end_value > start_value:
                result.append({'start': start_value, 'end': end_value})
        return result

    def _merge_break_intervals(self, items):
        if not items:
            return []
        normalized = sorted(
            [
                {'start': int(x.get('start', 0)), 'end': int(x.get('end', 0))}
                for x in items
                if int(x.get('end', 0)) > int(x.get('start', 0))
            ],
            key=lambda x: (x['start'], x['end'])
        )
        if not normalized:
            return []
        result = [dict(normalized[0])]
        for cur in normalized[1:]:
            last = result[-1]
            if cur['start'] <= last['end']:
                last['end'] = max(last['end'], cur['end'])
            else:
                result.append(dict(cur))
        return result

    def _break_layout_spacing(self, seg_start, seg_end, break_durations):
        duration = max(0, int(seg_end) - int(seg_start))
        count = len(break_durations or [])
        if duration <= 0 or count <= 0:
            return 0, 0

        if duration >= 8 * 60:
            edge_margin = 90
        elif duration >= 6 * 60:
            edge_margin = 60
        else:
            edge_margin = 45
        min_gap = 45 if count > 1 else 0

        total_break_minutes = sum(max(0, int(x)) for x in (break_durations or []))

        def required(edge_value, gap_value):
            return total_break_minutes + (2 * edge_value) + (gap_value * max(0, count - 1))

        while edge_margin > 30 and required(edge_margin, min_gap) > duration:
            edge_margin -= 5
        while min_gap > 15 and required(edge_margin, min_gap) > duration:
            min_gap -= 5
        while edge_margin > 0 and required(edge_margin, min_gap) > duration:
            edge_margin -= 5
        while min_gap > 0 and required(edge_margin, min_gap) > duration:
            min_gap -= 5

        return max(0, int(edge_margin)), max(0, int(min_gap))

    def _break_start_bounds_for_index(self, seg_start, seg_end, break_durations, index, edge_margin, min_gap):
        durations = [max(0, int(x)) for x in (break_durations or [])]
        idx = int(index)
        count = len(durations)
        if idx < 0 or idx >= count:
            return int(seg_start), int(seg_end)

        before_minutes = sum(durations[:idx]) + (int(min_gap) * idx)
        after_minutes = sum(durations[idx + 1:]) + (int(min_gap) * max(0, count - idx - 1))
        lower = int(seg_start) + int(edge_margin) + before_minutes
        upper = int(seg_end) - int(edge_margin) - durations[idx] - after_minutes
        return int(lower), int(upper)

    def _break_total_overlap_minutes(self, interval, occupied_intervals):
        total = 0
        for occ in (occupied_intervals or []):
            try:
                overlap = min(int(interval['end']), int(occ['end'])) - max(int(interval['start']), int(occ['start']))
            except Exception:
                continue
            if overlap > 0:
                total += overlap
        return total

    def _break_allowed_pair_overlap_minutes(self, length_a, length_b):
        """
        Сколько минут пересечения допустимо между перерывами двух операторов.
        Длительность 15 → 10 минут, 30 → 15; для нестандартных длительностей
        та же прямая, и не больше самого короткого из двух перерывов.
        """
        def allowed(value):
            length = max(0, int(value or 0))
            if length <= 0:
                return 0
            share = SHIFT_BREAK_PAIR_OVERLAP_BASE_MINUTES + (length / float(SHIFT_BREAK_PAIR_OVERLAP_SHARE))
            return min(length, int(round(share)))

        return min(allowed(length_a), allowed(length_b))

    def _break_pair_overlap_excess_minutes(self, interval, neighbor_intervals):
        """Минуты пересечения сверх допустимых — по каждому чужому перерыву отдельно."""
        total = 0
        for occ in (neighbor_intervals or []):
            try:
                start = max(int(interval['start']), int(occ['start']))
                end = min(int(interval['end']), int(occ['end']))
            except Exception:
                continue
            overlap = end - start
            if overlap <= 0:
                continue
            allowed = self._break_allowed_pair_overlap_minutes(
                int(interval['end']) - int(interval['start']),
                int(occ['end']) - int(occ['start'])
            )
            if overlap > allowed:
                total += overlap - allowed
        return total

    def _break_concurrency_penalty_minutes(self, interval, neighbor_intervals):
        """
        Штраф за нарушение «одновременно не больше двух операторов».

        Считаем, сколько чужих перерывов накрывает каждую минуту кандидата: один
        сосед — норма, два (то есть трое на перерыве) допустимы только как пересдача
        и не дольше SHIFT_BREAK_HANDOVER_OVERLAP_MINUTES подряд, трое и больше —
        всегда нарушение.
        """
        try:
            c_start = int(interval['start'])
            c_end = int(interval['end'])
        except Exception:
            return 0
        if c_end <= c_start:
            return 0

        spans = []
        for occ in (neighbor_intervals or []):
            try:
                start = max(c_start, int(occ['start']))
                end = min(c_end, int(occ['end']))
            except Exception:
                continue
            if end > start:
                spans.append((start, end))
        if len(spans) < SHIFT_BREAK_MAX_CONCURRENT:
            return 0

        points = sorted({c_start, c_end} | {value for span in spans for value in span})
        penalty = 0
        run = 0
        for left, right in zip(points, points[1:]):
            if right <= left:
                continue
            covering = sum(1 for start, end in spans if start < right and end > left)
            if covering > SHIFT_BREAK_MAX_CONCURRENT:
                # Четвёртый оператор на перерыве — нарушение без всяких скидок.
                penalty += (right - left) * (covering - SHIFT_BREAK_MAX_CONCURRENT) * 10
            if covering >= SHIFT_BREAK_MAX_CONCURRENT:
                run += right - left
            else:
                penalty += max(0, run - SHIFT_BREAK_HANDOVER_OVERLAP_MINUTES)
                run = 0
        penalty += max(0, run - SHIFT_BREAK_HANDOVER_OVERLAP_MINUTES)
        return penalty

    def _break_cross_gap_deficit_minutes(self, interval, neighbor_intervals):
        """
        Насколько не выдержан промежуток между перерывами разных операторов
        (настройка направления, задача #112). Промежуток не задан — 0.
        """
        total = 0
        for occ in (neighbor_intervals or []):
            gap = max(0, int(occ.get('gap') or 0))
            if gap <= 0:
                continue
            try:
                distance = max(
                    int(occ['start']) - int(interval['end']),
                    int(interval['start']) - int(occ['end'])
                )
            except Exception:
                continue
            if distance < gap:
                total += gap - distance
        return total

    def _even_break_starts(self, win_start, seg_end, break_durations):
        """
        Старты перерывов при идеально равномерной раскладке: рабочие отрезки
        между ними одинаковые. Возвращает (список стартов, длина отрезка).
        """
        durations = [int(x) for x in (break_durations or []) if int(x) > 0]
        span = int(seg_end) - int(win_start)
        if not durations or span <= 0:
            return [], 0.0
        work = span - sum(durations)
        run = work / float(len(durations) + 1)
        starts = []
        position = float(win_start)
        for length in durations:
            position += run
            starts.append(position)
            position += length
        return starts, run

    def _break_position_cost(self, interval, neighbor_intervals):
        """Штраф позиции перерыва: нарушения правил тяжелее пожеланий."""
        return (
            self._break_concurrency_penalty_minutes(interval, neighbor_intervals) * SHIFT_BREAK_WEIGHT_CONCURRENCY
            + self._break_pair_overlap_excess_minutes(interval, neighbor_intervals) * SHIFT_BREAK_WEIGHT_PAIR_EXCESS
            + self._break_cross_gap_deficit_minutes(interval, neighbor_intervals) * SHIFT_BREAK_WEIGHT_CROSS_GAP
            + self._break_total_overlap_minutes(interval, neighbor_intervals) * SHIFT_BREAK_WEIGHT_OVERLAP
        )

    def _plan_break_positions(self, win_start, seg_end, break_durations, neighbor_intervals,
                              own_intervals=None, edge_margin=0, min_gap=0, desired_starts=None):
        """
        Подбирает старты ВСЕХ перерывов смены сразу.

        Каждый перерыв ищется рядом со своей равномерной точкой, а выбор внутри
        этого окна делает динамика: цена = нарушения правил плюс отклонение
        рабочих отрезков от равномерного. Так сдвиг одного перерыва тянет за
        собой соседние, и смена не разваливается на «дыру и три подряд».

        Возвращает список стартов или None, если разложить не удалось.
        """
        step = 5
        durations = [int(x) for x in (break_durations or []) if int(x) > 0]
        count = len(durations)
        if count == 0:
            return []
        ideal_starts, ideal_run = self._even_break_starts(win_start, seg_end, durations)
        if not ideal_starts:
            return None
        # Промежуток направления (#112) требует отойти дальше обычного — ровно на
        # него окно и расширяем, иначе соблюдающая промежуток позиция не попадёт
        # в перебор. Без настройки (0) окно прежнее.
        cross_gap = 0
        for item in (neighbor_intervals or []):
            try:
                cross_gap = max(cross_gap, int(item.get('gap') or 0))
            except Exception:
                continue
        window = max(step, int(round(ideal_run * SHIFT_BREAK_EVEN_WINDOW_SHARE))) + cross_gap
        own = [item for item in (own_intervals or [])]
        desired = list(desired_starts or [])

        grids = []
        costs = []
        for idx in range(count):
            lower, upper = self._break_start_bounds_for_index(
                win_start, seg_end, durations, idx, edge_margin, min_gap
            )
            near_lower = max(lower, ideal_starts[idx] - window)
            near_upper = min(upper, ideal_starts[idx] + window)
            low = int(math.ceil(float(near_lower) / step) * step)
            high = int(math.floor(float(near_upper) / step) * step)
            if high < low:
                # Окно равномерности не пересеклось с отступами — держимся отступов.
                low = int(math.ceil(float(lower) / step) * step)
                high = int(math.floor(float(upper) / step) * step)
                if high < low:
                    return None
            grid = list(range(low, high + 1, step))
            row = []
            for start in grid:
                interval = {'start': start, 'end': start + durations[idx]}
                if own and self._break_total_overlap_minutes(interval, own) > 0:
                    row.append(None)
                    continue
                cost = self._break_position_cost(interval, neighbor_intervals)
                # Перерыв, который уже стоит в графике и ещё не начался, при прочих
                # равных остаётся на месте: людям его уже назвали.
                if desired and idx < len(desired) and desired[idx] is not None:
                    cost += abs(start - int(desired[idx])) * SHIFT_BREAK_WEIGHT_KEEP_PLACE
                row.append(cost)
            grids.append(grid)
            costs.append(row)

        def evenness(run_minutes):
            return abs(run_minutes - ideal_run) * SHIFT_BREAK_WEIGHT_EVENNESS

        best_cost = [
            (costs[0][j] + evenness(start - win_start)) if costs[0][j] is not None else None
            for j, start in enumerate(grids[0])
        ]
        back_refs = []
        for idx in range(1, count):
            current = [None] * len(grids[idx])
            refs = [None] * len(grids[idx])
            for j, start in enumerate(grids[idx]):
                if costs[idx][j] is None:
                    continue
                best_value = None
                best_ref = None
                for k, prev_start in enumerate(grids[idx - 1]):
                    if best_cost[k] is None:
                        continue
                    run = start - (prev_start + durations[idx - 1])
                    if run < min_gap:
                        continue
                    value = best_cost[k] + evenness(run)
                    if best_value is None or value < best_value:
                        best_value = value
                        best_ref = k
                if best_ref is None:
                    continue
                current[j] = best_value + costs[idx][j]
                refs[j] = best_ref
            best_cost = current
            back_refs.append(refs)

        final_value = None
        final_index = None
        for j, start in enumerate(grids[count - 1]):
            if best_cost[j] is None:
                continue
            value = best_cost[j] + evenness(seg_end - (start + durations[count - 1]))
            if final_value is None or value < final_value:
                final_value = value
                final_index = j
        if final_index is None:
            return None

        indexes = [0] * count
        indexes[count - 1] = final_index
        for idx in range(count - 1, 0, -1):
            indexes[idx - 1] = back_refs[idx - 1][indexes[idx]]
        return [grids[idx][indexes[idx]] for idx in range(count)]

    def _find_best_break_start(self, desired_start, length, lower_bound, upper_bound,
                               neighbor_intervals, self_intervals=None):
        """
        Ищет старт перерыва в допустимом окне.

        Пересечения с чужими перерывами больше не запрещены наглухо: сначала
        отсеиваются нарушения правил (свои перерывы, одновременность, лимит на
        пару), а среди оставшихся выигрывает точка, ближайшая к равномерной.
        """
        step = 5
        length = int(length)
        if length <= 0:
            return None

        lower = int(math.ceil(float(lower_bound) / step) * step)
        upper = int(math.floor(float(upper_bound) / step) * step)
        if upper < lower:
            return None

        desired = int(math.floor((float(desired_start) / step) + 0.5) * step)
        best = None
        best_score = None
        for start in range(lower, upper + 1, step):
            interval = {'start': start, 'end': start + length}
            deviation = abs(start - desired)
            cost = (
                self._break_concurrency_penalty_minutes(interval, neighbor_intervals) * SHIFT_BREAK_WEIGHT_CONCURRENCY
                + self._break_pair_overlap_excess_minutes(interval, neighbor_intervals) * SHIFT_BREAK_WEIGHT_PAIR_EXCESS
                + self._break_cross_gap_deficit_minutes(interval, neighbor_intervals) * SHIFT_BREAK_WEIGHT_CROSS_GAP
                + self._break_total_overlap_minutes(interval, neighbor_intervals) * SHIFT_BREAK_WEIGHT_OVERLAP
                + deviation
            )
            score = (
                # Свои же перерывы наложиться не могут ни при каких условиях.
                self._break_total_overlap_minutes(interval, self_intervals),
                cost,
                deviation,
                start
            )
            if best_score is None or score < best_score:
                best_score = score
                best = start
        return best

    def _load_occupied_break_intervals_for_operator_date_tx(self, cursor, operator_id, shift_date):
        """
        Возвращает занятые интервалы перерывов других операторов того же направления
        для shift_date с учетом переходов через полночь (пред/след день со сдвигом).
        """
        operator_id = int(operator_id)
        date_obj = self._normalize_schedule_date(shift_date)

        cursor.execute(
            """
            SELECT direction_id
            FROM users
            WHERE id = %s
            LIMIT 1
            """,
            (operator_id,)
        )
        row = cursor.fetchone()
        direction_id = row[0] if row else None
        if direction_id is None:
            return []

        prev_date = date_obj - timedelta(days=1)
        next_date = date_obj + timedelta(days=1)
        occupied = []

        cursor.execute(
            """
            SELECT ws.shift_date, sb.start_minutes, sb.end_minutes
            FROM work_shifts ws
            JOIN users u ON u.id = ws.operator_id
            JOIN shift_breaks sb ON sb.shift_id = ws.id
            WHERE ws.operator_id <> %s
              AND u.role = 'operator'
              AND u.direction_id = %s
              AND ws.shift_date IN (%s, %s, %s)
            """,
            (operator_id, direction_id, prev_date, date_obj, next_date)
        )

        for break_date, start_minutes, end_minutes in cursor.fetchall() or []:
            try:
                s = int(start_minutes)
                e = int(end_minutes)
            except Exception:
                continue
            if e <= s:
                continue

            if break_date == prev_date:
                s -= 1440
                e -= 1440
            elif break_date == next_date:
                s += 1440
                e += 1440

            ns = max(0, min(2880, int(s)))
            ne = max(0, min(2880, int(e)))
            if ne > ns:
                occupied.append({'start': ns, 'end': ne})

        return sorted(occupied, key=lambda item: (int(item['start']), int(item['end'])))

    def _tag_break_intervals_with_cross_gap(self, intervals, gap_minutes):
        """
        Помечает перерывы коллег требуемым промежутком. Сливать и раздувать их
        нельзя: по исходным интервалам считается и одновременность, и лимит
        пересечения пары перерывов.
        """
        gap = max(0, int(gap_minutes or 0))
        tagged = []
        for item in (intervals or []):
            try:
                start = int(item['start'])
                end = int(item['end'])
            except Exception:
                continue
            if end > start:
                tagged.append({'start': start, 'end': end, 'gap': max(gap, int(item.get('gap') or 0))})
        return sorted(tagged, key=lambda item: (item['start'], item['end']))

    def _adjust_shift_breaks_against_occupied_tx(
        self,
        cursor,
        operator_id,
        shift_date,
        start_time,
        end_time,
        breaks,
        cross_gap_minutes=None,
        frozen_breaks=None,
        planning_from_minutes=None,
        extra_occupied=None,
        allow_overlap=None
    ):
        """
        Раскладывает перерывы смены равномерно, сверяясь с перерывами коллег направления.

        Пересечение с коллегой допустимо в пределах правил (см. SHIFT_BREAK_PAIR_*),
        поэтому норма не сбивается к краям смены, когда операторов много.

        frozen_breaks — уже прошедшие перерывы: они не двигаются и не удаляются,
        а лишь занимают время. planning_from_minutes — минута, раньше которой нельзя
        ставить новый перерыв (для смены «на ходу» это «сейчас» плюс буфер).
        extra_occupied — перерывы коллег, которых в этот момент нет в базе: пути с
        предварительной очисткой дня удаляют смены до планирования, и замороженные
        перерывы коллег иначе не были бы видны.
        allow_overlap — галочка «Разрешить пересечение» направления: перерывы
        коллег не учитываются вовсе, смена раскладывается только по себе.
        None — узнать по направлению оператора.
        Без этих аргументов поведение прежнее.
        """
        seg_start, seg_end = self._schedule_interval_minutes(start_time, end_time)
        seg_start = max(0, seg_start)
        seg_end = max(seg_start, min(2880, seg_end))
        if seg_end <= seg_start:
            return []

        protected = []
        for item in sorted((frozen_breaks or []), key=lambda x: (int(x.get('start', 0)), int(x.get('end', 0)))):
            p_start = int(item.get('start', 0))
            p_end = int(item.get('end', 0))
            if p_end <= p_start or p_start < seg_start or p_end > seg_end:
                continue
            protected.append({'start': p_start, 'end': p_end})

        if not isinstance(breaks, list) or not breaks:
            return protected

        if allow_overlap is None:
            allow_overlap = self._break_overlap_allowed_for_direction_tx(
                cursor,
                self._get_operator_direction_name_tx(cursor, operator_id)
            )
        if allow_overlap:
            # «Разрешить пересечение»: перерывы коллег направления не мешают.
            neighbors = []
        else:
            neighbors = self._load_occupied_break_intervals_for_operator_date_tx(cursor, operator_id, shift_date)
            if cross_gap_minutes is None:
                cross_gap_minutes = self._get_break_cross_operator_gap_for_direction_tx(
                    cursor,
                    self._get_operator_direction_name_tx(cursor, operator_id)
                )
            neighbors.extend(self._normalize_break_intervals_soft(extra_occupied, dedupe=False))
            neighbors = self._tag_break_intervals_with_cross_gap(neighbors, cross_gap_minutes)
        # Свои перерывы — отдельно от чужих: они запрещены наглухо, а не «нежелательны».
        own_intervals = [{'start': int(p['start']), 'end': int(p['end'])} for p in protected]

        prepared_breaks = []
        for b in sorted((breaks or []), key=lambda x: (int(x.get('start', 0)), int(x.get('end', 0)))):
            b_start = int(b.get('start', 0))
            b_end = int(b.get('end', 0))
            length = b_end - b_start
            if length > 0:
                prepared_breaks.append({'start': b_start, 'end': b_end, 'length': length})

        if not prepared_breaks:
            return protected

        # Планировать можно только «незамороженный» хвост смены.
        protected_end = max((int(p['end']) for p in protected), default=None)
        win_start = seg_start
        if planning_from_minutes is not None:
            win_start = max(win_start, int(planning_from_minutes))
        if protected_end is not None:
            win_start = max(win_start, protected_end)
        win_start = min(win_start, seg_end)

        break_durations = [int(b['length']) for b in prepared_breaks]
        edge_margin, min_gap = self._break_layout_spacing(win_start, seg_end, break_durations)

        # Основной путь: перерывы смены подбираются совместно, цель — одинаковые
        # рабочие отрезки между ними. Жадный перебор ниже остаётся запасным.
        planned_starts = self._plan_break_positions(
            win_start=win_start,
            seg_end=seg_end,
            break_durations=break_durations,
            neighbor_intervals=neighbors,
            own_intervals=own_intervals,
            edge_margin=edge_margin,
            min_gap=min_gap,
            desired_starts=[int(b['start']) for b in prepared_breaks]
        )
        if planned_starts:
            new_breaks = [
                {'start': int(start), 'end': int(start) + int(break_durations[idx])}
                for idx, start in enumerate(planned_starts)
            ]
            if not protected:
                return new_breaks
            return sorted(
                protected + new_breaks,
                key=lambda item: (int(item['start']), int(item['end']))
            )

        new_breaks = []
        for idx, b in enumerate(prepared_breaks):
            length = int(b['length'])
            lower, upper = self._break_start_bounds_for_index(
                win_start,
                seg_end,
                break_durations,
                idx,
                edge_margin,
                min_gap
            )
            if new_breaks and min_gap > 0:
                lower = max(lower, int(new_breaks[-1]['end']) + int(min_gap))
            elif protected_end is not None and min_gap > 0:
                lower = max(lower, protected_end + int(min_gap))

            desired_start = max(lower, min(upper, int(b['start']))) if upper >= lower else int(b['start'])
            found = self._find_best_break_start(
                desired_start=desired_start,
                length=length,
                lower_bound=lower,
                upper_bound=upper,
                neighbor_intervals=neighbors,
                self_intervals=own_intervals
            )
            if found is not None:
                nb = {'start': int(found), 'end': int(found) + int(length)}
            else:
                clamped_start = max(win_start, min(seg_end - length, int(b['start'])))
                if new_breaks and min_gap > 0:
                    clamped_start = max(clamped_start, int(new_breaks[-1]['end']) + int(min_gap))
                clamped_start = min(clamped_start, seg_end - length)
                clamped_end = clamped_start + length
                if clamped_end <= clamped_start or clamped_start < win_start:
                    continue
                nb = {'start': int(clamped_start), 'end': int(clamped_end)}

            new_breaks.append(nb)
            own_intervals.append(dict(nb))

        if not protected:
            return new_breaks
        return sorted(
            protected + new_breaks,
            key=lambda item: (int(item['start']), int(item['end']))
        )

    def _break_layout_score(self, planned_by_shift, shift_meta):
        """
        Оценка целого прохода: сперва нарушения правил, потом неравномерность.

        Нужна потому, что повторные проходы улучшают раскладку немонотонно —
        четвёртый проход может оказаться хуже третьего. Записываем лучший.
        """
        by_direction_date = {}
        for shift_id, breaks in planned_by_shift.items():
            meta = shift_meta.get(shift_id)
            if not meta or not breaks:
                continue
            operator_id, shift_date, direction_key = meta
            key = (direction_key, self._normalize_schedule_date(shift_date))
            by_direction_date.setdefault(key, []).append((int(operator_id), breaks))

        violations = 0
        unevenness = 0
        for group in by_direction_date.values():
            for index, (operator_id, breaks) in enumerate(group):
                neighbors = []
                for other_index, (other_operator_id, other_breaks) in enumerate(group):
                    if other_index == index:
                        continue
                    neighbors.extend({'start': int(x['start']), 'end': int(x['end'])} for x in other_breaks)
                for item in breaks:
                    interval = {'start': int(item['start']), 'end': int(item['end'])}
                    violations += (
                        self._break_concurrency_penalty_minutes(interval, neighbors) * SHIFT_BREAK_WEIGHT_CONCURRENCY
                        + self._break_pair_overlap_excess_minutes(interval, neighbors) * SHIFT_BREAK_WEIGHT_PAIR_EXCESS
                    )
        for shift_id, breaks in planned_by_shift.items():
            if len(breaks) < 2:
                continue
            ordered = sorted(breaks, key=lambda item: int(item['start']))
            runs = [
                int(nxt['start']) - int(cur['end'])
                for cur, nxt in zip(ordered, ordered[1:])
            ]
            if not runs:
                continue
            average = sum(runs) / float(len(runs))
            unevenness += sum(abs(run - average) for run in runs)
        return (violations, round(unevenness, 3))

    def recalculate_work_schedule_breaks(self, start_date, end_date, operator_ids=None, now=None):
        """
        Пересчёт перерывов по текущим правилам направлений.

        Прошедшие дни не трогаются вовсе, а у идущей смены сохраняются уже начавшиеся
        перерывы: пересчёт не должен переписывать то, что оператор уже отсидел.
        """
        start_date_obj = self._normalize_schedule_date(start_date)
        end_date_obj = self._normalize_schedule_date(end_date)
        if end_date_obj < start_date_obj:
            raise ValueError("end_date must be >= start_date")

        recalc_now = now if isinstance(now, datetime) else self._almaty_now()
        today_obj = recalc_now.date()
        skipped_past_days = max(
            0,
            (min(today_obj, end_date_obj + timedelta(days=1)) - start_date_obj).days
        )
        requested_start_date_obj = start_date_obj
        if start_date_obj < today_obj:
            start_date_obj = today_obj
        if start_date_obj > end_date_obj:
            return {
                "start_date": start_date_obj.strftime('%Y-%m-%d'),
                "end_date": end_date_obj.strftime('%Y-%m-%d'),
                "requested_start_date": requested_start_date_obj.strftime('%Y-%m-%d'),
                "operators_requested": 0,
                "operators_affected": 0,
                "shifts_processed": 0,
                "breaks_deleted": 0,
                "breaks_inserted": 0,
                "breaks_frozen": 0,
                "skipped_past_days": skipped_past_days
            }

        normalized_operator_ids = None
        if operator_ids is not None:
            normalized_operator_ids = sorted({
                int(value)
                for value in (operator_ids or [])
                if value is not None and str(value).strip() != ''
            })
            if not normalized_operator_ids:
                return {
                    "start_date": start_date_obj.strftime('%Y-%m-%d'),
                    "end_date": end_date_obj.strftime('%Y-%m-%d'),
                    "requested_start_date": requested_start_date_obj.strftime('%Y-%m-%d'),
                    "operators_requested": 0,
                    "operators_affected": 0,
                    "shifts_processed": 0,
                    "breaks_deleted": 0,
                    "breaks_inserted": 0,
                    "breaks_frozen": 0,
                    "skipped_past_days": skipped_past_days
                }

        with self._get_cursor() as cursor:
            query = """
                SELECT ws.id, ws.operator_id, ws.shift_date, ws.start_time, ws.end_time, d.name
                FROM work_shifts ws
                JOIN users u ON u.id = ws.operator_id
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE u.role = 'operator'
                  AND ws.shift_date >= %s
                  AND ws.shift_date <= %s
            """
            params = [start_date_obj, end_date_obj]
            if normalized_operator_ids is not None:
                query += " AND ws.operator_id = ANY(%s)"
                params.append(normalized_operator_ids)
            query += " ORDER BY ws.shift_date, COALESCE(d.name, ''), ws.start_time, ws.end_time, ws.operator_id, ws.id"
            cursor.execute(query, params)
            rows = cursor.fetchall() or []

            if not rows:
                return {
                    "start_date": start_date_obj.strftime('%Y-%m-%d'),
                    "end_date": end_date_obj.strftime('%Y-%m-%d'),
                    "requested_start_date": requested_start_date_obj.strftime('%Y-%m-%d'),
                    "operators_requested": len(normalized_operator_ids or []),
                    "operators_affected": 0,
                    "shifts_processed": 0,
                    "breaks_deleted": 0,
                    "breaks_inserted": 0,
                    "breaks_frozen": 0,
                    "skipped_past_days": skipped_past_days
                }

            # Снимки перерывов дня снимаем ДО удаления: у идущей смены прошедшие
            # перерывы возвращаются на свои места, а не планируются заново.
            day_breaks_snapshots = {}
            for row in rows:
                snapshot_key = (int(row[1]), row[2].strftime('%Y-%m-%d'))
                if snapshot_key not in day_breaks_snapshots:
                    day_breaks_snapshots[snapshot_key] = self._load_day_shift_breaks_tx(
                        cursor,
                        row[1],
                        row[2]
                    )

            shift_ids = [int(row[0]) for row in rows]
            cursor.execute("DELETE FROM shift_breaks WHERE shift_id = ANY(%s)", (shift_ids,))
            breaks_deleted = int(cursor.rowcount or 0)

            direction_rules_cache = {}
            direction_cross_gap_cache = {}
            direction_overlap_cache = {}

            def _rules_for_direction(direction_name):
                direction_key = self._normalize_direction_key(direction_name)
                if direction_key not in direction_rules_cache:
                    direction_rules_cache[direction_key] = self._get_work_schedule_break_rules_for_direction_tx(
                        cursor,
                        direction_name
                    )
                return direction_rules_cache[direction_key]

            def _cross_gap_for_direction(direction_name):
                direction_key = self._normalize_direction_key(direction_name)
                if direction_key not in direction_cross_gap_cache:
                    direction_cross_gap_cache[direction_key] = self._get_break_cross_operator_gap_for_direction_tx(
                        cursor,
                        direction_name
                    )
                return direction_cross_gap_cache[direction_key]

            def _overlap_allowed_for_direction(direction_name):
                direction_key = self._normalize_direction_key(direction_name)
                if direction_key not in direction_overlap_cache:
                    direction_overlap_cache[direction_key] = self._break_overlap_allowed_for_direction_tx(
                        cursor,
                        direction_name
                    )
                return direction_overlap_cache[direction_key]

            def _sort_key(row):
                _, operator_id, shift_date, start_time_value, end_time_value, direction_name = row
                start_min, end_min = self._schedule_interval_minutes(start_time_value, end_time_value)
                break_durations = self._pick_break_durations_for_shift(
                    max(0, end_min - start_min),
                    direction_name=direction_name,
                    direction_rules=_rules_for_direction(direction_name)
                )
                # «Жёсткость» считаем по окну планирования: у идущей смены свободен
                # только хвост, и она должна получить слоты раньше свободных смен.
                window_start = max(
                    start_min,
                    self._break_freeze_boundary_minutes(shift_date, seg_start=start_min, now=recalc_now)
                    + SHIFT_BREAK_PLANNING_BUFFER_MINUTES
                )
                window_start = min(window_start, end_min)
                duration = max(0, end_min - window_start)
                edge_margin, min_gap = self._break_layout_spacing(window_start, end_min, break_durations)
                required_minutes = (
                    sum(max(0, int(x)) for x in (break_durations or []))
                    + (2 * int(edge_margin))
                    + (int(min_gap) * max(0, len(break_durations or []) - 1))
                )
                flexibility = duration - required_minutes
                return (
                    shift_date,
                    self._normalize_direction_key(direction_name),
                    flexibility,
                    start_min,
                    end_min,
                    int(operator_id)
                )

            breaks_inserted = 0
            breaks_frozen = 0
            affected_operator_ids = set()
            recalc_direction_keys = {}
            # Пересчёт идёт в несколько проходов: за один проход операторам,
            # которых раскладывают последними, свободного места уже не остаётся.
            # Пишем в базу только победивший проход, промежуточные живут в памяти.
            ordered_rows = sorted(rows, key=_sort_key)
            planned_by_shift = {}
            frozen_by_shift = {}
            shift_meta = {
                int(row[0]): (
                    int(row[1]),
                    row[2],
                    self._normalize_direction_key(row[5])
                )
                for row in ordered_rows
            }
            best_snapshot = None
            for _pass_index in range(max(1, int(SHIFT_BREAK_LAYOUT_PASSES))):
                self._plan_recalc_pass(
                    cursor=cursor,
                    ordered_rows=ordered_rows,
                    planned_by_shift=planned_by_shift,
                    frozen_by_shift=frozen_by_shift,
                    shift_meta=shift_meta,
                    day_breaks_snapshots=day_breaks_snapshots,
                    rules_for_direction=_rules_for_direction,
                    cross_gap_for_direction=_cross_gap_for_direction,
                    recalc_now=recalc_now,
                    recalc_direction_keys=recalc_direction_keys,
                    overlap_allowed_for_direction=_overlap_allowed_for_direction
                )
                score = self._break_layout_score(planned_by_shift, shift_meta)
                if best_snapshot is None or score < best_snapshot[0]:
                    best_snapshot = (
                        score,
                        {key: [dict(item) for item in value] for key, value in planned_by_shift.items()}
                    )
            planned_by_shift = best_snapshot[1] if best_snapshot else planned_by_shift

            for shift_id, operator_id, shift_date, start_time_value, end_time_value, direction_name in ordered_rows:
                adjusted_breaks = planned_by_shift.get(int(shift_id)) or []
                self._insert_shift_breaks(cursor, shift_id, adjusted_breaks)
                breaks_inserted += len(adjusted_breaks)
                breaks_frozen += len(frozen_by_shift.get(int(shift_id)) or [])
                affected_operator_ids.add(int(operator_id))

            return {
                "start_date": start_date_obj.strftime('%Y-%m-%d'),
                "end_date": end_date_obj.strftime('%Y-%m-%d'),
                "requested_start_date": requested_start_date_obj.strftime('%Y-%m-%d'),
                "operators_requested": len(normalized_operator_ids or []),
                "operators_affected": len(affected_operator_ids),
                "shifts_processed": len(rows),
                "breaks_deleted": breaks_deleted,
                "breaks_inserted": breaks_inserted,
                "breaks_frozen": breaks_frozen,
                "skipped_past_days": skipped_past_days
            }

    def _resolve_breaks_for_day_replacement_tx(self, cursor, operator_id, shift_date, start_time, end_time, breaks):
        """
        Для replace-сценариев (bulk set_shift): если клиент прислал старые автоперерывы
        и смена стала короче, переводим на серверную автогенерацию (возвращаем None).
        """
        if breaks is None:
            return None

        incoming_breaks_norm = self._normalize_shift_breaks(breaks)
        cursor.execute(
            """
            SELECT id, start_time, end_time
            FROM work_shifts
            WHERE operator_id = %s AND shift_date = %s
            ORDER BY start_time
            """,
            (int(operator_id), self._normalize_schedule_date(shift_date))
        )
        existing_rows = cursor.fetchall() or []
        if len(existing_rows) != 1:
            return incoming_breaks_norm

        direction_name = self._get_operator_direction_name_tx(cursor, operator_id)
        direction_rules = self._get_work_schedule_break_rules_for_direction_tx(cursor, direction_name)
        shift_id, existing_start_value, existing_end_value = existing_rows[0]
        existing_breaks = self._load_shift_breaks_tx(cursor, shift_id)
        if not existing_breaks:
            return incoming_breaks_norm
        if incoming_breaks_norm != existing_breaks:
            return incoming_breaks_norm

        existing_start_min, existing_end_min = self._schedule_interval_minutes(existing_start_value, existing_end_value)
        existing_duration = max(0, existing_end_min - existing_start_min)
        existing_auto_breaks = self._compute_auto_shift_breaks_minutes(
            existing_start_min,
            existing_end_min,
            direction_name=direction_name,
            direction_rules=direction_rules
        )
        if existing_breaks != existing_auto_breaks:
            return incoming_breaks_norm

        new_start_min, new_end_min = self._schedule_interval_minutes(start_time, end_time)
        new_duration = max(0, new_end_min - new_start_min)
        if new_duration < existing_duration:
            return None

        return incoming_breaks_norm

    def _supervisor_schedule_break_minutes_tx(self, cursor, user_ids, date_from, date_to):
        """{(СВ, день): минут перерывов, поставленных РОП на сменах этого дня}.

        День — дата смены (смена принадлежит дню своего начала, как и отметки).
        Нет перерывов на сменах дня — нет ключа, и вычитается общий перерыв СВ."""
        ids = sorted({int(v) for v in (user_ids or []) if v is not None})
        if not ids:
            return {}
        cursor.execute("""
            SELECT ws.operator_id, ws.shift_date, SUM(sb.end_minutes - sb.start_minutes)
            FROM work_shifts ws
            JOIN shift_breaks sb ON sb.shift_id = ws.id
            WHERE ws.operator_id = ANY(%s) AND ws.shift_date BETWEEN %s AND %s
            GROUP BY ws.operator_id, ws.shift_date
        """, (ids, date_from, date_to))
        return {(int(row[0]), row[1]): int(row[2] or 0) for row in cursor.fetchall() if row[2]}
