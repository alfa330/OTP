"""База данных: опоздания (бот групп).

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    CALCULATION_MODEL_OPERATOR,
    date,
    datetime,
    dt_time,
    execute_values,
    Json,
    logging,
    psycopg2,
    re,
    SCHEDULE_AUTO_NO_PHONE_STATUS_KEY,
    supervisor_hours,
    time,
    timedelta,
    WORK_SHIFT_TYPE_PHONE_SHIFT,
)


class GroupLateMixin:
    def _recalculate_chat_manager_score_adjustments_tx(self, cursor, operator_day_pairs):
        pairs = sorted({
            (
                int(op_id),
                day_obj.date() if isinstance(day_obj, datetime)
                else day_obj if isinstance(day_obj, date)
                else datetime.strptime(str(day_obj), '%Y-%m-%d').date()
            )
            for op_id, day_obj in (operator_day_pairs or [])
            if op_id is not None and day_obj is not None
        }, key=lambda item: (item[0], item[1]))
        if not pairs:
            return {'adjusted_days': 0, 'aggregations': {}}

        adjusted = []
        for operator_id, day_obj in pairs:
            cursor.execute(
                """
                SELECT raw_score_sum, raw_score_count, avg_score
                FROM chat_manager_daily_metrics
                WHERE operator_id = %s AND day = %s
                FOR UPDATE
                """,
                (operator_id, day_obj)
            )
            metric_row = cursor.fetchone()

            # Сырой день Chat2Desk правится решениями по низким оценкам (задача
            # #286): снимаем свои оценки, которые оператору не засчитаны
            # (необоснованные и отданные другим менеджерам), и добавляем чужие,
            # засчитанные ему. Засчитанная нескольким идёт каждому целиком.
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(score) FILTER (WHERE operator_id = %s AND (
                        final_status = 'invalid'
                        OR (final_status = 'valid'
                            AND attributed_operator_ids IS NOT NULL
                            AND NOT (%s = ANY(attributed_operator_ids)))
                    )), 0),
                    COUNT(*) FILTER (WHERE operator_id = %s AND (
                        final_status = 'invalid'
                        OR (final_status = 'valid'
                            AND attributed_operator_ids IS NOT NULL
                            AND NOT (%s = ANY(attributed_operator_ids)))
                    )),
                    COALESCE(SUM(score) FILTER (WHERE operator_id <> %s
                        AND final_status = 'valid'
                        AND %s = ANY(attributed_operator_ids)), 0),
                    COUNT(*) FILTER (WHERE operator_id <> %s
                        AND final_status = 'valid'
                        AND %s = ANY(attributed_operator_ids))
                FROM chat_manager_low_rating_reviews
                WHERE day = %s
                  AND (operator_id = %s OR %s = ANY(attributed_operator_ids))
                """,
                (
                    operator_id, operator_id,
                    operator_id, operator_id,
                    operator_id, operator_id,
                    operator_id, operator_id,
                    day_obj, operator_id, operator_id,
                )
            )
            removed_sum_raw, removed_count_raw, added_sum_raw, added_count_raw = cursor.fetchone() or (0, 0, 0, 0)
            removed_sum = float(removed_sum_raw or 0.0)
            removed_count = int(removed_count_raw or 0)
            added_sum = float(added_sum_raw or 0.0)
            added_count = int(added_count_raw or 0)

            if not metric_row:
                # Менеджеру засчитали чужую оценку, а своей строки за этот день у
                # него нет (чат шёл через полночь). Без строки оценка бы просто
                # потерялась — заводим пустую: оценок Chat2Desk за день ноль.
                if added_count <= 0:
                    continue
                cursor.execute(
                    """
                    INSERT INTO chat_manager_daily_metrics (
                        operator_id, day, raw_score_sum, raw_score_count, raw_payload
                    )
                    VALUES (%s, %s, 0, 0, %s)
                    ON CONFLICT (operator_id, day) DO NOTHING
                    """,
                    (operator_id, day_obj, Json({'source': 'low_rating_attribution'}))
                )
                base_sum, base_count = 0.0, 0
            else:
                raw_sum, raw_count, stored_avg = metric_row
                if raw_sum is None or raw_count is None:
                    # Сырья по оценкам за день нет: Chat2Desk оценок не присылал
                    # или это старый ручной ввод одной средней. Трогаем такой день
                    # только ради засчитанной чужой оценки, и тогда сырьём
                    # становится то, что день уже вносил в месяц: средняя с весом 1
                    # (так её и считает месячная формула) либо ноль.
                    if added_count <= 0:
                        continue
                    if stored_avg is not None:
                        base_sum, base_count = float(stored_avg), 1
                    else:
                        base_sum, base_count = 0.0, 0
                else:
                    try:
                        base_sum = float(raw_sum)
                        base_count = int(raw_count)
                    except Exception:
                        continue

            effective_count = max(0, base_count - removed_count + added_count)
            effective_sum = round(max(0.0, base_sum - removed_sum + added_sum), 4)
            # Счётчик и сумма урезаются по отдельности, поэтому у испорченного
            # дня получалось «оценок 0, сумма 4.0». Месячный балл считается как
            # SUM(score_sum)/SUM(score_count), и такой день добавлял в числитель,
            # не добавляя в знаменатель, — балл за месяц завышался. Ноль оценок
            # означает нулевую сумму, иначе состояние противоречиво.
            if effective_count == 0:
                effective_sum = 0.0
            effective_avg = round(effective_sum / effective_count, 4) if effective_count > 0 else None

            cursor.execute(
                """
                UPDATE chat_manager_daily_metrics
                SET score_sum = %s,
                    score_count = %s,
                    avg_score = %s,
                    -- Сырьё фиксируем, если его не было: следующий пересчёт
                    -- (например, когда засчитанную оценку отдадут обратно)
                    -- должен вернуть день ровно к исходному состоянию.
                    raw_score_sum = COALESCE(raw_score_sum, %s),
                    raw_score_count = COALESCE(raw_score_count, %s),
                    updated_at = CURRENT_TIMESTAMP
                WHERE operator_id = %s AND day = %s
                """,
                (effective_sum, effective_count, effective_avg, base_sum, base_count, operator_id, day_obj)
            )
            adjusted.append((operator_id, day_obj))

        aggregations = {}
        for operator_id, day_obj in adjusted:
            month_key = day_obj.strftime('%Y-%m')
            aggregations.setdefault(operator_id, {})[month_key] = self._aggregate_month_from_daily_tx(
                cursor,
                operator_id,
                month_key
            )
        return {'adjusted_days': len(adjusted), 'aggregations': aggregations}

    @staticmethod
    def _wazzup_chat_later_tx(cursor, channel_id, chat_id, dt, skip_late=False):
        """Время сообщений чата позже dt — для wazzup.delivery.after_known_replies.
        Всё, что уже лежит в базе, к приходу этого сообщения в чате уже было, в
        том числе записанное раньше него в том же вебхуке (та же транзакция).
        skip_late — для ответа с телефона: опоздавшие входящие ему не указ
        (wazzup/delivery.py). Индекс по каналу, чату и времени."""
        cursor.execute("""
            SELECT dt FROM wazzup_messages
             WHERE channel_id = %s AND chat_id = %s AND dt > %s"""
                       + (" AND wazzup_dt IS NULL" if skip_late else ""),
            (channel_id, chat_id, dt))
        return [r[0] for r in cursor.fetchall()]

    def recalculate_supervisor_clockster_hours(self, date_from, date_to, user_ids=None, keep_break_before=None):
        """Пересчитать часы СВ по отметкам Clockster за период и записать в учёт часов.

        Пишутся только дни, которые кэш «Отметок» собрал вместе с Clockster, и
        только дни СВ (не покрытые операторским членством). День, которого в кэше
        нет или который собран без Clockster (сбой), не трогается — иначе сброс
        кэша стёр бы отработанные часы; так же не трогается день, у которого
        следующий собран без Clockster: уход ночной смены лежит в нём.
        СВ без карточки Clockster (не сшит или «нет карточки») получает ноль по
        собранным дням — прежние часы по карточке, которую сняли, не остаются.

        keep_break_before — дни раньше этой даты (закрытый прошлый месяц) берут
        уже вычтенный перерыв из строки, а не нынешнюю настройку: правка перерыва
        не переписывает задним числом месяц, который уже закрыт.

        Отметки — из _supervisor_card_marks_multi_tx: за дни, которые прошла
        синхронизация с Clockster, — из supervisor_clockster_marks, за остальные —
        из кэша «Отметок»."""
        start = self._normalize_schedule_date(date_from)
        end = self._normalize_schedule_date(date_to)
        if end < start:
            start, end = end, start
        keep_before = self._normalize_schedule_date(keep_break_before) if keep_break_before else None
        summary = {'updated_days': 0, 'supervisors': 0, 'unlinked': [], 'months': []}
        with self._get_cursor() as cursor:
            sv_ids = self._clockster_hours_supervisor_ids_tx(cursor, user_ids)
            if not sv_ids:
                return summary
            cursor.execute("SELECT id, name FROM users WHERE id = ANY(%s)", (sorted(sv_ids),))
            people = [(int(row[0]), row[1]) for row in cursor.fetchall()]
            summary['supervisors'] = len(people)

            months = []
            cursor_month = date(start.year, start.month, 1)
            while cursor_month <= end:
                months.append(cursor_month.strftime('%Y-%m'))
                cursor_month = (cursor_month + timedelta(days=32)).replace(day=1)
            for month in months:
                self._fill_supervisor_norm_hours_tx(cursor, month, sv_ids)
            summary['months'] = months

            settings = self._supervisor_hours_settings_tx(cursor, sv_ids)
            cards = self._supervisor_clockster_cards_tx(cursor, people, settings)
            linked = {uid: card['ext_id'] for uid, card in cards.items() if card and card.get('ext_id')}
            summary['unlinked'] = sorted(uid for uid, _ in people if uid not in linked)

            # Отметки читаются с дня накануне: иначе повторное касание после
            # полуночи на краю окна стало бы новым приходом и первый день получил
            # бы смену, уже учтённую накануне. И на день вперёд: уход ночной смены.
            marks_by_card, known_days, unknown_days = self._supervisor_card_marks_multi_tx(
                cursor, set(linked.values()), start - timedelta(days=1), end + timedelta(days=1))
            countable_days = {
                day for day in known_days
                if start <= day <= end and (day + timedelta(days=1)) not in unknown_days
            }
            if not countable_days:
                return summary

            cursor.execute("""
                SELECT operator_id, day, break_time FROM daily_hours
                WHERE operator_id = ANY(%s) AND day BETWEEN %s AND %s
            """, (sorted(sv_ids), start, end))
            existing = {(int(row[0]), row[1]): float(row[2] or 0) for row in cursor.fetchall()}
            operator_days = self._operator_membership_days_tx(cursor, sv_ids, start, end)
            # Перерывы, поставленные РОП на сменах дня, главнее общего перерыва СВ.
            schedule_breaks = self._supervisor_schedule_break_minutes_tx(cursor, sv_ids, start, end)
            # Ручные отметки РОП идут в ленту наравне с терминальными — в том числе
            # у СВ без карточки Clockster (не зарегистрирован на терминале).
            manual_marks, replaced_marks = self._supervisor_manual_marks_tx(
                cursor, sv_ids, start - timedelta(days=1), end + timedelta(days=1))

            rows = []
            affected = set()
            for user_id, _name in people:
                ext_id = linked.get(user_id)
                user_marks = supervisor_hours.drop_replaced(
                    marks_by_card.get(ext_id, []) if ext_id else [], replaced_marks.get(user_id)
                ) + manual_marks.get(user_id, [])
                sessions = supervisor_hours.pair_sessions(supervisor_hours.normalize_marks(user_marks))
                presence_by_day = supervisor_hours.presence_by_day(sessions)
                break_minutes = settings[user_id]['break_minutes']
                for day in sorted(countable_days):
                    if (user_id, day) in operator_days:
                        continue
                    presence = presence_by_day.get(day, 0)
                    key = (user_id, day)
                    if presence <= 0 and key not in existing:
                        continue
                    day_break = break_minutes
                    if key in schedule_breaks:
                        day_break = schedule_breaks[key]
                    elif keep_before and day < keep_before and existing.get(key, 0) > 0:
                        day_break = int(round(existing[key] * 60))
                    worked, deducted = supervisor_hours.day_hours(presence, day_break)
                    rows.append((user_id, day, round(worked / 3600.0, 4), round(deducted / 3600.0, 4)))
                    affected.add((user_id, day.strftime('%Y-%m')))

            if rows:
                execute_values(cursor, """
                    INSERT INTO daily_hours (operator_id, day, work_time, break_time,
                                             auto_aggregated, auto_aggregated_at)
                    VALUES %s
                    ON CONFLICT (operator_id, day) DO UPDATE SET
                        work_time = EXCLUDED.work_time,
                        break_time = EXCLUDED.break_time,
                        auto_aggregated = TRUE,
                        auto_aggregated_at = CURRENT_TIMESTAMP
                """, rows, template="(%s, %s, %s, %s, TRUE, CURRENT_TIMESTAMP)", page_size=500)
            for user_id, month in sorted(affected):
                self._aggregate_month_from_daily_tx(cursor, user_id, month)
            summary['updated_days'] = len(rows)
        return summary

    def _recalculate_supervisor_days(self, user_id, *moments):
        """Пересчитать дни отметок (и дни накануне: уход после полуночи закрывает
        смену вчерашнего дня). Закрытый месяц держит прежний вычет перерыва — как
        в ночном пересчёте, иначе правка отметки переписала бы его по нынешнему."""
        days = [moment.astimezone(supervisor_hours.TZ).date() for moment in moments if moment is not None]
        if not days:
            return None
        today = datetime.now(supervisor_hours.TZ).date()
        return self.recalculate_supervisor_clockster_hours(
            min(days) - timedelta(days=1), max(days), [user_id],
            keep_break_before=today.replace(day=1))

    def _recalculate_auto_daily_hours_tx(self, cursor, operator_ids, start_date, end_date):
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        if not op_ids:
            return {'updated_days': 0, 'aggregated_months': 0}
        # Часы СВ с Clockster пишет recalculate_supervisor_clockster_hours. Статусов
        # у СВ нет, и этот пересчёт при каждом сохранении смены затирал бы их нулём.
        # Роль — на дату: дни, покрытые операторским членством (СВ ещё работал на
        # линии), остаются за этим пересчётом.
        #
        # То же — у всех, кто не на линии (09.11.2026 график в «Графиках работы»
        # открыт всем ролям): у бухгалтера, кадровика, админа, тренера статусов
        # телефонии нет, и пересчёт записал бы им в «Учёт часов» нулевые дни.
        exempt_ids = self._clockster_hours_supervisor_ids_tx(cursor, op_ids)
        cursor.execute(
            """
            SELECT id FROM users
            WHERE id = ANY(%s)
              AND lower(COALESCE(role, '')) NOT IN ('operator', 'trainee')
            """,
            (op_ids,),
        )
        exempt_ids = set(exempt_ids) | {int(row[0]) for row in cursor.fetchall()}
        supervisor_operator_days = set()
        if exempt_ids:
            supervisor_operator_days = self._operator_membership_days_tx(
                cursor, exempt_ids,
                self._normalize_schedule_date(start_date) - timedelta(days=1),
                self._normalize_schedule_date(end_date) + timedelta(days=1),
            )
            still_operators = {user_id for user_id, _day in supervisor_operator_days}
            op_ids = [
                op_id for op_id in op_ids
                if op_id not in exempt_ids or op_id in still_operators
            ]
            if not op_ids:
                return {'updated_days': 0, 'aggregated_months': 0}

        start_date_obj = self._normalize_schedule_date(start_date)
        end_date_obj = self._normalize_schedule_date(end_date)
        if end_date_obj < start_date_obj:
            start_date_obj, end_date_obj = end_date_obj, start_date_obj

        # Для ночных смен (например, 20:00-08:00) досчитываем следующий календарный день,
        # чтобы хвост после полуночи попадал в "день+1".
        calc_start_date_obj, calc_end_date_obj = self._schedule_auto_clamp_range(
            start_date_obj,
            end_date_obj + timedelta(days=1)
        )
        if calc_start_date_obj is None or calc_end_date_obj is None:
            return {'updated_days': 0, 'aggregated_months': 0}

        # Для корректного расчета текущего дня учитываем смены, начавшиеся днем ранее
        # и перешедшие через полночь.
        shifts_source_start = calc_start_date_obj - timedelta(days=1)
        shifts_source_end = calc_end_date_obj

        cursor.execute("""
            SELECT id, operator_id, shift_date, start_time, end_time, shift_type
            FROM work_shifts
            WHERE operator_id = ANY(%s)
              AND shift_date >= %s
              AND shift_date <= %s
            ORDER BY operator_id, shift_date, start_time, end_time
        """, (op_ids, shifts_source_start, shifts_source_end))
        shifts_rows = cursor.fetchall() or []

        shifts_by_start_day = {}
        shifts_segments_by_day = {}
        for shift_id, op_id, shift_date_value, start_time_value, end_time_value, shift_type_value in shifts_rows:
            start_min, end_min = self._schedule_interval_minutes(start_time_value, end_time_value)
            start_sec = int(start_min) * 60
            end_sec = int(end_min) * 60
            shift_type_norm = self._normalize_work_shift_type(shift_type_value)
            item = {
                'id': int(shift_id),
                'start': int(start_sec),
                'end': int(end_sec),
                'shift_type': shift_type_norm
            }
            start_key = (int(op_id), shift_date_value)
            shifts_by_start_day.setdefault(start_key, []).append(item)

            shift_start_abs = int(start_sec)
            shift_end_abs = int(end_sec)
            first_day_offset = int(shift_start_abs // 86400)
            last_day_offset = int((shift_end_abs - 1) // 86400)
            for day_offset in range(first_day_offset, last_day_offset + 1):
                day_floor = int(day_offset) * 86400
                day_ceil = day_floor + 86400
                segment_abs_start = max(shift_start_abs, day_floor)
                segment_abs_end = min(shift_end_abs, day_ceil)
                if segment_abs_end <= segment_abs_start:
                    continue

                day_value = shift_date_value + timedelta(days=int(day_offset))
                if day_value < calc_start_date_obj or day_value > calc_end_date_obj:
                    continue

                local_start = int(segment_abs_start - day_floor)
                local_end = int(segment_abs_end - day_floor)
                if local_end <= local_start:
                    continue
                shifts_segments_by_day.setdefault((int(op_id), day_value), []).append({
                    'start': local_start,
                    'end': local_end,
                    'shift_type': shift_type_norm
                })

        status_start_for_query = calc_start_date_obj - timedelta(days=1)
        status_end_for_query = calc_end_date_obj + timedelta(days=1)
        cursor.execute("""
            SELECT
                operator_id,
                status_date,
                start_at,
                end_at,
                status_key
            FROM operator_status_segments
            WHERE operator_id = ANY(%s)
              AND status_date >= %s
              AND status_date <= %s
            ORDER BY operator_id, status_date, start_at, end_at, id
        """, (op_ids, status_start_for_query, status_end_for_query))
        status_rows = cursor.fetchall() or []
        statuses_by_day = {}
        for op_id, status_date_value, start_at_value, end_at_value, status_key in status_rows:
            day_chunks = self._schedule_auto_split_status_segment_by_day(
                status_date_value=status_date_value,
                start_at_value=start_at_value,
                end_at_value=end_at_value
            )
            if not day_chunks:
                continue
            for chunk in day_chunks:
                chunk_day = chunk.get('day')
                start_sec = int(chunk.get('start', 0))
                end_sec = int(chunk.get('end', 0))
                if not isinstance(chunk_day, date):
                    continue
                if chunk_day < calc_start_date_obj or chunk_day > status_end_for_query:
                    continue
                if end_sec <= start_sec:
                    continue
                day_key = (int(op_id), chunk_day)
                statuses_by_day.setdefault(day_key, []).append({
                    'start': int(start_sec),
                    'end': int(end_sec),
                    'status_key': str(status_key or '').strip().lower()
                })

        cursor.execute("""
            SELECT
                operator_id,
                day,
                COALESCE(auto_aggregated, FALSE),
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
        """, (op_ids, calc_start_date_obj, calc_end_date_obj))
        existing_rows = cursor.fetchall() or []
        existing_by_day = {}
        for row in existing_rows:
            op_id = int(row[0])
            day_value = row[1]
            existing_by_day[(op_id, day_value)] = {
                'auto_aggregated': bool(row[2]),
                'late_status': self._schedule_auto_normalize_flag_status(row[3]),
                'early_leave_status': self._schedule_auto_normalize_flag_status(row[4]),
                'training_status': self._schedule_auto_normalize_flag_status(row[5]),
                'technical_reason_status': self._schedule_auto_normalize_flag_status(row[6]),
                'late_fine_id': int(row[7]) if row[7] is not None else None,
                'early_leave_fine_id': int(row[8]) if row[8] is not None else None,
                'training_fine_id': int(row[9]) if row[9] is not None else None,
                'technical_reason_fine_id': int(row[10]) if row[10] is not None else None
            }

        target_days = set(shifts_segments_by_day.keys())
        for key, item in existing_by_day.items():
            if (
                item.get('auto_aggregated')
                or item.get('late_status')
                or item.get('early_leave_status')
                or item.get('training_status')
                or item.get('technical_reason_status')
            ):
                target_days.add(key)
        if exempt_ids:
            target_days = {
                key for key in target_days
                if key[0] not in exempt_ids or key in supervisor_operator_days
            }

        if not target_days:
            return {'updated_days': 0, 'aggregated_months': 0}

        upsert_rows = []
        affected_months = set()
        calculation_model_by_operator_day = {}
        group_id_by_operator_day = {}

        for op_id, day_value in sorted(target_days, key=lambda x: (x[0], x[1])):
            operator_day_key = (int(op_id), day_value)
            if operator_day_key not in calculation_model_by_operator_day:
                calculation_model_by_operator_day[operator_day_key] = self._get_operator_calculation_model_tx(
                    cursor, op_id, as_of=day_value
                )
            if operator_day_key not in group_id_by_operator_day:
                group_id_by_operator_day[operator_day_key] = self._get_operator_group_id_tx(
                    cursor, op_id, day_value
                )

            day_statuses = statuses_by_day.get((op_id, day_value)) or []
            status_profile = self._status_profile_for_calculation_model(
                calculation_model_by_operator_day.get(operator_day_key, CALCULATION_MODEL_OPERATOR)
            )
            day_group_id = group_id_by_operator_day.get(operator_day_key)
            work_status_keys = set(status_profile.get('work') or set())
            talk_status_keys = set(status_profile.get('talk') or set())
            break_status_keys = set(status_profile.get('break') or set())
            training_status_keys = set(status_profile.get('training') or set())
            late_start_status_keys = set(status_profile.get('late_start') or work_status_keys)

            def pick_status_intervals(source_items, predicate_fn):
                return self._merge_break_intervals([
                    {'start': int(seg.get('start', 0)), 'end': int(seg.get('end', 0))}
                    for seg in (source_items or [])
                    if predicate_fn(seg)
                ])

            day_work_status = pick_status_intervals(
                day_statuses,
                lambda seg: str(seg.get('status_key') or '') in work_status_keys
            )
            day_talk_status = pick_status_intervals(
                day_statuses,
                lambda seg: str(seg.get('status_key') or '') in talk_status_keys
            )
            day_break_status = pick_status_intervals(
                day_statuses,
                lambda seg: str(seg.get('status_key') or '') in break_status_keys
            )
            day_training_status = pick_status_intervals(
                day_statuses,
                lambda seg: str(seg.get('status_key') or '') in training_status_keys
            )
            day_late_start_status = pick_status_intervals(
                day_statuses,
                lambda seg: str(seg.get('status_key') or '') in late_start_status_keys
            )
            day_technical_reason_status = pick_status_intervals(
                day_statuses,
                lambda seg: self._schedule_auto_is_tech_reason_status_key(seg.get('status_key'))
            )
            day_no_phone_status = pick_status_intervals(
                day_statuses,
                lambda seg: str(seg.get('status_key') or '') == SCHEDULE_AUTO_NO_PHONE_STATUS_KEY
            )
            day_late_excused_status = pick_status_intervals(
                day_statuses,
                lambda seg: self._schedule_auto_is_late_excused_status_key(seg.get('status_key'))
            )

            day_shift_intervals = self._merge_break_intervals(
                shifts_segments_by_day.get((op_id, day_value)) or []
            )
            day_phone_shift_intervals = self._merge_break_intervals([
                {'start': int(seg.get('start', 0)), 'end': int(seg.get('end', 0))}
                for seg in (shifts_segments_by_day.get((op_id, day_value)) or [])
                if seg.get('shift_type') == WORK_SHIFT_TYPE_PHONE_SHIFT
            ])
            work_seconds = self._schedule_auto_overlap_minutes(day_shift_intervals, day_work_status)
            talk_seconds = self._schedule_auto_overlap_minutes(day_shift_intervals, day_talk_status)
            # Перерывы считаем по фактическим статусам в пределах смены, независимо от
            # запланированных break-интервалов графика.
            break_seconds = self._schedule_auto_overlap_minutes(day_shift_intervals, day_break_status)
            phone_shift_training_intervals = self._merge_break_intervals([
                item
                for interval in (day_phone_shift_intervals or [])
                for item in self._schedule_auto_intersect_interval_with_list(interval, day_training_status)
            ])
            training_intervals_effective = self._schedule_auto_subtract_intervals(
                self._merge_break_intervals(day_training_status or []),
                phone_shift_training_intervals
            )
            training_seconds_in_shift = self._schedule_auto_overlap_minutes(day_shift_intervals, training_intervals_effective)
            technical_reason_seconds_in_shift = self._schedule_auto_overlap_minutes(day_shift_intervals, day_technical_reason_status)
            no_phone_seconds_in_shift = self._schedule_auto_overlap_minutes(day_shift_intervals, day_no_phone_status)

            late_seconds_total = 0.0
            early_leave_seconds_total = 0.0
            shifts_started_today = shifts_by_start_day.get((op_id, day_value)) or []
            if shifts_started_today:
                next_day_statuses = statuses_by_day.get((op_id, day_value + timedelta(days=1))) or []
                next_day_work_status = pick_status_intervals(
                    next_day_statuses,
                    lambda seg: str(seg.get('status_key') or '') in work_status_keys
                )
                next_day_late_start_status = pick_status_intervals(
                    next_day_statuses,
                    lambda seg: str(seg.get('status_key') or '') in late_start_status_keys
                )
                next_day_late_excused_status = pick_status_intervals(
                    next_day_statuses,
                    lambda seg: self._schedule_auto_is_late_excused_status_key(seg.get('status_key'))
                )
                shifted_next_day_work_status = [
                    {
                        'start': int(seg.get('start', 0)) + 86400,
                        'end': int(seg.get('end', 0)) + 86400
                    }
                    for seg in (next_day_work_status or [])
                    if int(seg.get('end', 0)) > int(seg.get('start', 0))
                ]
                shifted_next_day_late_start_status = [
                    {
                        'start': int(seg.get('start', 0)) + 86400,
                        'end': int(seg.get('end', 0)) + 86400
                    }
                    for seg in (next_day_late_start_status or [])
                    if int(seg.get('end', 0)) > int(seg.get('start', 0))
                ]
                shifted_next_day_late_excused_status = [
                    {
                        'start': int(seg.get('start', 0)) + 86400,
                        'end': int(seg.get('end', 0)) + 86400
                    }
                    for seg in (next_day_late_excused_status or [])
                    if int(seg.get('end', 0)) > int(seg.get('start', 0))
                ]
                work_status_for_shift = self._merge_break_intervals(
                    (day_work_status or []) + shifted_next_day_work_status
                )
                late_start_status_for_shift = self._merge_break_intervals(
                    (day_late_start_status or []) + shifted_next_day_late_start_status
                )
                late_excused_status_for_shift = self._merge_break_intervals(
                    (day_late_excused_status or []) + shifted_next_day_late_excused_status
                )
                for shift in shifts_started_today:
                    shift_interval = {'start': int(shift['start']), 'end': int(shift['end'])}
                    shift_work_status = self._schedule_auto_intersect_interval_with_list(
                        shift_interval,
                        work_status_for_shift
                    )
                    if not shift_work_status:
                        continue
                    shift_late_start_status = self._schedule_auto_intersect_interval_with_list(
                        shift_interval,
                        late_start_status_for_shift
                    )
                    first_work_start = int((shift_late_start_status or shift_work_status)[0].get('start', shift_interval['start']))
                    last_work_end = int(shift_work_status[-1].get('end', shift_interval['end']))
                    shift_excused_status = self._schedule_auto_intersect_interval_with_list(
                        shift_interval,
                        late_excused_status_for_shift
                    )
                    late_excused_boundary = self._schedule_auto_shift_start_excused_boundary(
                        shift_interval,
                        shift_excused_status
                    )
                    late_seconds_total += max(0, first_work_start - int(late_excused_boundary))
                    # Ранний уход: та же логика что у рабочих статусов —
                    # берём конец последнего excused-сегмента внутри смены.
                    # Разрывы внутри excused-периода (например, "без телефона"
                    # между двумя тренинг-блоками) не влияют на результат.
                    last_excused_end = max(
                        (int(seg['end']) for seg in (shift_excused_status or [])),
                        default=0
                    )
                    last_active_end = max(last_work_end, min(last_excused_end, shift_interval['end']))
                    early_leave_seconds_total += max(0, shift_interval['end'] - last_active_end)

            day_shift_intervals = self._merge_break_intervals(day_shift_intervals)
            work_inside_shift_day = self._schedule_auto_overlap_minutes(day_work_status, day_shift_intervals)
            overtime_seconds_total = max(0.0, self._schedule_auto_total_minutes(day_work_status) - work_inside_shift_day)

            work_time_hours = round(work_seconds / 3600.0, 4)
            break_time_hours = round(break_seconds / 3600.0, 4)
            talk_time_hours = round(talk_seconds / 3600.0, 4)
            efficiency_hours = round(talk_seconds / 3600.0, 4)
            training_time_hours = round(training_seconds_in_shift / 3600.0, 4)

            late_seconds_int = max(0, int(round(late_seconds_total)))
            early_leave_seconds_int = max(0, int(round(early_leave_seconds_total)))
            overtime_seconds_int = max(0, int(round(overtime_seconds_total)))
            training_seconds_int = max(0, int(round(training_seconds_in_shift)))
            technical_reason_seconds_int = max(0, int(round(technical_reason_seconds_in_shift)))
            no_phone_seconds_int = max(0, int(round(no_phone_seconds_in_shift)))

            late_minutes_int = self._schedule_auto_seconds_to_display_minutes(late_seconds_int)
            early_leave_minutes_int = self._schedule_auto_seconds_to_display_minutes(early_leave_seconds_int)
            overtime_minutes_int = self._schedule_auto_seconds_to_display_minutes(overtime_seconds_int)
            training_minutes_int = self._schedule_auto_seconds_to_display_minutes(training_seconds_int)
            technical_reason_minutes_int = self._schedule_auto_seconds_to_display_minutes(technical_reason_seconds_int)
            no_phone_minutes_int = self._schedule_auto_seconds_to_display_minutes(no_phone_seconds_int)

            existing = existing_by_day.get((op_id, day_value)) or {}
            prev_late_status = self._schedule_auto_normalize_flag_status(existing.get('late_status'))
            prev_early_status = self._schedule_auto_normalize_flag_status(existing.get('early_leave_status'))
            prev_training_status = self._schedule_auto_normalize_flag_status(existing.get('training_status'))
            prev_technical_reason_status = self._schedule_auto_normalize_flag_status(existing.get('technical_reason_status'))

            late_status = self._schedule_auto_flag_status_for_minutes(prev_late_status, late_minutes_int)
            early_status = self._schedule_auto_flag_status_for_minutes(prev_early_status, early_leave_minutes_int)
            training_status = self._schedule_auto_flag_status_for_minutes(prev_training_status, training_minutes_int)
            technical_reason_status = self._schedule_auto_flag_status_for_minutes(prev_technical_reason_status, technical_reason_minutes_int)

            upsert_rows.append((
                int(op_id),
                day_value,
                day_group_id,
                float(work_time_hours),
                float(break_time_hours),
                float(talk_time_hours),
                int(0),
                float(efficiency_hours),
                float(training_time_hours),
                int(late_minutes_int),
                int(late_seconds_int),
                int(early_leave_minutes_int),
                int(early_leave_seconds_int),
                int(overtime_minutes_int),
                int(overtime_seconds_int),
                int(training_minutes_int),
                int(training_seconds_int),
                int(technical_reason_minutes_int),
                int(technical_reason_seconds_int),
                int(no_phone_minutes_int),
                int(no_phone_seconds_int),
                late_status,
                early_status,
                training_status,
                technical_reason_status,
                existing.get('late_fine_id'),
                existing.get('early_leave_fine_id'),
                existing.get('training_fine_id'),
                existing.get('technical_reason_fine_id')
            ))
            affected_months.add((int(op_id), day_value.strftime('%Y-%m')))

        if upsert_rows:
            # Auto aggregation owns timing/status fields only; imported call counts must survive recalculations.
            execute_values(
                cursor,
                """
                INSERT INTO daily_hours (
                    operator_id, day, group_id, work_time, break_time, talk_time, calls, efficiency,
                    training_time,
                    late_minutes, late_seconds,
                    early_leave_minutes, early_leave_seconds,
                    overtime_minutes, overtime_seconds,
                    training_minutes, training_seconds,
                    technical_reason_minutes, technical_reason_seconds,
                    no_phone_minutes, no_phone_seconds,
                    late_status, early_leave_status, training_status, technical_reason_status,
                    late_fine_id, early_leave_fine_id, training_fine_id, technical_reason_fine_id,
                    auto_aggregated, auto_aggregated_at
                )
                VALUES %s
                ON CONFLICT (operator_id, day)
                DO UPDATE SET
                    group_id = COALESCE(EXCLUDED.group_id, daily_hours.group_id),
                    work_time = EXCLUDED.work_time,
                    break_time = EXCLUDED.break_time,
                    talk_time = EXCLUDED.talk_time,
                    efficiency = EXCLUDED.efficiency,
                    training_time = EXCLUDED.training_time,
                    late_minutes = EXCLUDED.late_minutes,
                    late_seconds = EXCLUDED.late_seconds,
                    early_leave_minutes = EXCLUDED.early_leave_minutes,
                    early_leave_seconds = EXCLUDED.early_leave_seconds,
                    overtime_minutes = EXCLUDED.overtime_minutes,
                    overtime_seconds = EXCLUDED.overtime_seconds,
                    training_minutes = EXCLUDED.training_minutes,
                    training_seconds = EXCLUDED.training_seconds,
                    technical_reason_minutes = EXCLUDED.technical_reason_minutes,
                    technical_reason_seconds = EXCLUDED.technical_reason_seconds,
                    no_phone_minutes = EXCLUDED.no_phone_minutes,
                    no_phone_seconds = EXCLUDED.no_phone_seconds,
                    late_status = EXCLUDED.late_status,
                    early_leave_status = EXCLUDED.early_leave_status,
                    training_status = EXCLUDED.training_status,
                    technical_reason_status = EXCLUDED.technical_reason_status,
                    late_fine_id = EXCLUDED.late_fine_id,
                    early_leave_fine_id = EXCLUDED.early_leave_fine_id,
                    training_fine_id = EXCLUDED.training_fine_id,
                    technical_reason_fine_id = EXCLUDED.technical_reason_fine_id,
                    auto_aggregated = TRUE,
                    auto_aggregated_at = CURRENT_TIMESTAMP,
                    created_at = CURRENT_TIMESTAMP
                """,
                upsert_rows,
                template="""(
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    TRUE, CURRENT_TIMESTAMP
                )""",
                page_size=2000
            )

        fine_sync_result = self._schedule_auto_sync_flag_fines_for_range_tx(
            cursor=cursor,
            operator_ids=op_ids,
            start_date=calc_start_date_obj,
            end_date=calc_end_date_obj
        )

        for op_id, month_key in sorted(affected_months):
            self._aggregate_month_from_daily_tx(cursor, op_id, month_key)

        return {
            'updated_days': len(upsert_rows),
            'aggregated_months': len(affected_months),
            'auto_flag_fines': fine_sync_result
        }

    def recalculate_auto_daily_hours(self, operator_ids, start_date, end_date):
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        start_date_obj = self._normalize_schedule_date(start_date)
        end_date_obj = self._normalize_schedule_date(end_date)
        if end_date_obj < start_date_obj:
            start_date_obj, end_date_obj = end_date_obj, start_date_obj

        with self._get_cursor() as cursor:
            segment_rebuild_summary = self._rebuild_operator_status_segments_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start_date_obj - timedelta(days=1),
                end_date=end_date_obj + timedelta(days=1),
                imported_by=None
            )
            result = self._recalculate_auto_daily_hours_tx(
                cursor=cursor,
                operator_ids=op_ids,
                start_date=start_date_obj,
                end_date=end_date_obj
            )
            if isinstance(result, dict):
                result['segment_rebuild'] = segment_rebuild_summary
            return result

    def get_latest_recruiting_parse_run(self, status='success'):
        status_norm = (str(status or '').strip().lower() or None)
        with self._get_cursor() as cursor:
            if status_norm:
                cursor.execute("""
                    SELECT run_uuid::text, source, triggered_by, status, total_items, error_message, started_at, finished_at
                    FROM recruiting_parse_runs
                    WHERE status = %s
                    ORDER BY finished_at DESC, id DESC
                    LIMIT 1
                """, (status_norm,))
            else:
                cursor.execute("""
                    SELECT run_uuid::text, source, triggered_by, status, total_items, error_message, started_at, finished_at
                    FROM recruiting_parse_runs
                    ORDER BY finished_at DESC, id DESC
                    LIMIT 1
                """)
            row = cursor.fetchone()
        return self._serialize_recruiting_run_row(row)

    @staticmethod
    def _glb_chat_id(value):
        chat_id = str(value or '').strip()
        if not chat_id:
            raise ValueError("chat_id is required")
        if len(chat_id) > 64 or not re.fullmatch(r'-?\d+', chat_id):
            raise ValueError("Invalid chat_id")
        return chat_id

    @staticmethod
    def _glb_clean_departments(values):
        """Список отделов без пустых строк и регистрозависимых дублей."""
        if values is None:
            return []
        if isinstance(values, str):
            values = re.split(r'\s*[;|]\s*', values)
        cleaned, seen = [], set()
        for raw in values:
            name = str(raw or '').strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(name[:200])
        return cleaned

    @staticmethod
    def _glb_department(value):
        """Отдел Workpace, которым ограничен раздел, либо None (ограничений нет)."""
        name = str(value or '').strip()
        return name[:200] or None

    @staticmethod
    def _glb_chat_scope_sql(chat_expr, department, param='glb_dept'):
        """SQL-условие «чат принадлежит только этому отделу Workpace».

        Отдел-владелец распоряжается лишь теми чатами, чей фильтр — ровно его отдел:
        чат без фильтра получает нарушения всей компании, а чат с несколькими
        отделами — ещё и чужие, и трогать их глава своего отдела не вправе.
        Без отдела возвращает TRUE — глобальный админ видит всё."""
        if not department:
            return 'TRUE'
        return (
            f"EXISTS (SELECT 1 FROM glb_chat_departments cd_in"
            f" WHERE cd_in.chat_id = {chat_expr}"
            f" AND lower(cd_in.department_name) = lower(%({param})s))"
            f" AND NOT EXISTS (SELECT 1 FROM glb_chat_departments cd_out"
            f" WHERE cd_out.chat_id = {chat_expr}"
            f" AND lower(cd_out.department_name) <> lower(%({param})s))"
        )

    def _glb_mute_scope_sql(self, mute_alias, department, param='glb_dept'):
        """SQL-условие «правило тишины в границах отдела»: либо оно привязано к
        нашему чату, либо это глобальное правило на сам наш отдел."""
        if not department:
            return 'TRUE'
        chat_scope = self._glb_chat_scope_sql(f'{mute_alias}.chat_id', department, param)
        return (
            f"(({mute_alias}.chat_id IS NOT NULL AND {chat_scope})"
            f" OR ({mute_alias}.chat_id IS NULL AND {mute_alias}.mute_kind = 'dept'"
            f" AND lower({mute_alias}.mute_value) = lower(%({param})s)))"
        )

    def glb_chat_department_state(self, chat_id, department):
        """'absent' | 'own' | 'foreign' — есть ли чат в рассылке и наш ли он.

        Нужно до записи: у главы отдела «подключить чат» не должно уводить чужой
        чат под свой фильтр (INSERT идёт через ON CONFLICT DO UPDATE)."""
        chat_id = self._glb_chat_id(chat_id)
        scope_sql = self._glb_chat_scope_sql('c.chat_id', department)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT ({scope_sql}) AS own
                FROM glb_chats c
                WHERE c.chat_id = %(chat_id)s
            """, {'chat_id': chat_id, 'glb_dept': department})
            row = cursor.fetchone()
        if not row:
            return 'absent'
        return 'own' if row[0] else 'foreign'

    @staticmethod
    def _glb_parse_date(value, field_name='date'):
        text = str(value or '').strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError(f"Invalid {field_name}, expected YYYY-MM-DD")

    def get_group_late_overview(self, days=7, department=None):
        """Сводка для вкладки «Обзор»: состояние опроса, объёмы за период, тишина.

        department — отдел Workpace, которым ограничен запрашивающий (глава отдела);
        None у глобального админа. Состояние самого опроса общее: это здоровье
        источника данных, а не чьи-то цифры."""
        days_int = max(1, min(int(days or 7), 90))
        dept = self._glb_department(department)
        chat_scope = self._glb_chat_scope_sql('c.chat_id', dept)
        mute_scope = self._glb_mute_scope_sql('m', dept)
        dept_filter = 'TRUE' if not dept else 'lower(d.name) = lower(%(glb_dept)s)'
        events_scope = 'TRUE' if not dept else 'lower(e.department_name) = lower(%(glb_dept)s)'
        reports_scope = 'TRUE' if not dept else 'lower(r.department_filter) = lower(%(glb_dept)s)'
        params = {'days': days_int, 'glb_dept': dept}
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT
                    (SELECT COUNT(*) FROM glb_chats c WHERE {chat_scope}) AS chats_total,
                    (SELECT COUNT(*) FROM glb_chats c WHERE c.enabled AND {chat_scope}) AS chats_enabled,
                    (SELECT COUNT(*) FROM glb_chats c
                      WHERE {chat_scope}
                        AND EXISTS (SELECT 1 FROM glb_chat_departments cd WHERE cd.chat_id = c.chat_id)) AS chats_with_filter,
                    (SELECT COUNT(*) FROM glb_departments d WHERE {dept_filter}) AS departments_total,
                    (SELECT MAX(d.synced_at) FROM glb_departments d WHERE {dept_filter}) AS departments_synced_at,
                    (SELECT COUNT(*) FROM glb_mutes m WHERE {mute_scope}) AS mutes_total,
                    (SELECT COUNT(*) FROM glb_mutes m WHERE m.chat_id IS NULL AND {mute_scope}) AS mutes_global,
                    (SELECT COUNT(*) FROM glb_events e
                      WHERE e.detected_at >= NOW() - make_interval(days => %(days)s) AND {events_scope}) AS events_period,
                    (SELECT COUNT(*) FROM glb_reports r
                      WHERE r.created_at >= NOW() - make_interval(days => %(days)s) AND {reports_scope}) AS reports_period,
                    (SELECT COUNT(*) FROM glb_reports r
                      WHERE r.created_at >= NOW() - make_interval(days => %(days)s)
                        AND r.status <> 'ok' AND {reports_scope}) AS reports_failed
            """, params)
            row = cursor.fetchone()
            totals = {
                'chats_total': int(row[0] or 0),
                'chats_enabled': int(row[1] or 0),
                'chats_with_filter': int(row[2] or 0),
                'departments_total': int(row[3] or 0),
                'departments_synced_at': row[4].isoformat() if row[4] else None,
                'mutes_total': int(row[5] or 0),
                'mutes_global': int(row[6] or 0),
                'events_period': int(row[7] or 0),
                'reports_period': int(row[8] or 0),
                'reports_failed': int(row[9] or 0),
            }

            cursor.execute(f"""
                SELECT e.event_type, COUNT(*)
                FROM glb_events e
                WHERE e.detected_at >= NOW() - make_interval(days => %(days)s) AND {events_scope}
                GROUP BY e.event_type
            """, params)
            by_type = [
                {'event_type': r[0], 'count': int(r[1] or 0)}
                for r in cursor.fetchall()
            ]

            cursor.execute(f"""
                SELECT COALESCE(NULLIF(e.department_name, ''), 'Без отдела') AS dept, COUNT(*)
                FROM glb_events e
                WHERE e.detected_at >= NOW() - make_interval(days => %(days)s) AND {events_scope}
                GROUP BY dept
                ORDER BY COUNT(*) DESC
                LIMIT 12
            """, params)
            by_department = [
                {'department_name': r[0], 'count': int(r[1] or 0)}
                for r in cursor.fetchall()
            ]

            cursor.execute(f"""
                SELECT to_char(e.detected_at AT TIME ZONE 'Asia/Almaty', 'YYYY-MM-DD') AS day, COUNT(*)
                FROM glb_events e
                WHERE e.detected_at >= NOW() - make_interval(days => %(days)s) AND {events_scope}
                GROUP BY day
                ORDER BY day
            """, params)
            by_day = [
                {'date': r[0], 'count': int(r[1] or 0)}
                for r in cursor.fetchall()
            ]

            cursor.execute("""
                SELECT id, started_at, finished_at, ok, fetched, events_found, sent, duration_ms, error
                FROM glb_poll_runs
                ORDER BY started_at DESC
                LIMIT 1
            """)
            run_row = cursor.fetchone()
            last_run = None
            if run_row:
                last_run = {
                    'id': int(run_row[0]),
                    'started_at': run_row[1].isoformat() if run_row[1] else None,
                    'finished_at': run_row[2].isoformat() if run_row[2] else None,
                    'ok': run_row[3],
                    'fetched': run_row[4],
                    'events_found': run_row[5],
                    'sent': run_row[6],
                    'duration_ms': run_row[7],
                    'error': run_row[8],
                }

            cursor.execute("""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE ok IS NOT TRUE)
                FROM glb_poll_runs
                WHERE started_at >= NOW() - INTERVAL '24 hours'
            """)
            runs_row = cursor.fetchone()
            totals['poll_runs_24h'] = int(runs_row[0] or 0)
            totals['poll_failures_24h'] = int(runs_row[1] or 0)

        return {
            'days': days_int,
            'totals': totals,
            'by_type': by_type,
            'by_department': by_department,
            'by_day': by_day,
            'last_poll_run': last_run,
        }

    def get_group_late_chats(self, days=7, department=None):
        """Чаты рассылки со связкой отделов, правилами тишины и активностью.

        department — отдел Workpace, которым ограничен запрашивающий: тогда в списке
        только чаты этого отдела (см. _glb_chat_scope_sql)."""
        days_int = max(1, min(int(days or 7), 90))
        dept = self._glb_department(department)
        chat_scope = self._glb_chat_scope_sql('c.chat_id', dept)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT c.chat_id, c.title, c.chat_type, c.is_admin_chat, c.enabled, c.note,
                       c.created_by, c.created_at, c.updated_at, c.last_delivery_at,
                       COALESCE(d.departments, '{{}}') AS departments,
                       COALESCE(s.deliveries, 0) AS deliveries_period,
                       COALESCE(m.mutes, '{{}}') AS mutes
                FROM glb_chats c
                LEFT JOIN LATERAL (
                    SELECT array_agg(cd.department_name ORDER BY cd.department_name) AS departments
                    FROM glb_chat_departments cd WHERE cd.chat_id = c.chat_id
                ) d ON TRUE
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS deliveries
                    FROM glb_event_deliveries ed
                    WHERE ed.chat_id = c.chat_id
                      AND ed.sent_at >= NOW() - make_interval(days => %(days)s)
                ) s ON TRUE
                LEFT JOIN LATERAL (
                    SELECT array_agg(mu.mute_kind || ':' || mu.mute_value ORDER BY mu.mute_kind, mu.mute_value) AS mutes
                    FROM glb_mutes mu WHERE mu.chat_id = c.chat_id
                ) m ON TRUE
                WHERE {chat_scope}
                ORDER BY c.is_admin_chat DESC, c.created_at
            """, {'days': days_int, 'glb_dept': dept})
            items = []
            for r in cursor.fetchall():
                mutes = list(r[12] or [])
                items.append({
                    'chat_id': r[0],
                    'title': r[1],
                    'chat_type': r[2],
                    'is_admin_chat': bool(r[3]),
                    'enabled': bool(r[4]),
                    'note': r[5],
                    'created_by': r[6],
                    'created_at': r[7].isoformat() if r[7] else None,
                    'updated_at': r[8].isoformat() if r[8] else None,
                    'last_delivery_at': r[9].isoformat() if r[9] else None,
                    'departments': list(r[10] or []),
                    'deliveries_period': int(r[11] or 0),
                    'muted_all': any(m.startswith('all:') for m in mutes),
                    'muted_users': [m.split(':', 1)[1] for m in mutes if m.startswith('user:')],
                    'muted_departments': [m.split(':', 1)[1] for m in mutes if m.startswith('dept:')],
                })
        return {'days': days_int, 'items': items}

    def add_group_late_chat(self, chat_id, title=None, chat_type=None, departments=None,
                            note=None, created_by=None):
        """Добавить чат в рассылку. Чат, уже обнаруженный ботом, включаем."""
        chat_id = self._glb_chat_id(chat_id)
        department_names = self._glb_clean_departments(departments)
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO glb_chats (chat_id, title, chat_type, note, created_by, enabled)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (chat_id) DO UPDATE SET
                    title = COALESCE(NULLIF(EXCLUDED.title, ''), glb_chats.title),
                    chat_type = COALESCE(EXCLUDED.chat_type, glb_chats.chat_type),
                    note = COALESCE(EXCLUDED.note, glb_chats.note),
                    enabled = TRUE,
                    updated_at = NOW()
                RETURNING (xmax = 0) AS inserted
            """, (chat_id, (title or '').strip() or None, chat_type,
                  (note or '').strip() or None, created_by))
            created = bool(cursor.fetchone()[0])
            if department_names:
                self._glb_replace_chat_departments_tx(cursor, chat_id, department_names, created_by)
        return {'chat_id': chat_id, 'created': created, 'departments': department_names}

    def update_group_late_chat(self, chat_id, title=None, note=None, enabled=None, department=None):
        """department сужает правку до чатов своего отдела: чужой чат «не найден»."""
        chat_id = self._glb_chat_id(chat_id)
        dept = self._glb_department(department)
        sets = ['updated_at = NOW()']
        params = {'chat_id': chat_id, 'glb_dept': dept}
        if title is not None:
            sets.append('title = %(title)s')
            params['title'] = str(title).strip()[:200] or None
        if note is not None:
            sets.append('note = %(note)s')
            params['note'] = str(note).strip()[:500] or None
        if enabled is not None:
            sets.append('enabled = %(enabled)s')
            params['enabled'] = bool(enabled)
        scope = self._glb_chat_scope_sql('c.chat_id', dept)
        with self._get_cursor() as cursor:
            cursor.execute(
                f"UPDATE glb_chats AS c SET {', '.join(sets)}"
                f" WHERE c.chat_id = %(chat_id)s AND ({scope})", params)
            if cursor.rowcount == 0:
                raise ValueError("Chat not found")
        return {'chat_id': chat_id}

    def delete_group_late_chat(self, chat_id, department=None):
        """Убрать чат из рассылки. Историю доставок не трогаем — она нужна разделу.
        department сужает удаление до чатов своего отдела."""
        chat_id = self._glb_chat_id(chat_id)
        dept = self._glb_department(department)
        scope = self._glb_chat_scope_sql('c.chat_id', dept)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT c.is_admin_chat FROM glb_chats c
                WHERE c.chat_id = %(chat_id)s AND ({scope})
            """, {'chat_id': chat_id, 'glb_dept': dept})
            row = cursor.fetchone()
            if not row:
                raise ValueError("Chat not found")
            if bool(row[0]):
                raise ValueError("Админский чат удалить нельзя")
            cursor.execute("DELETE FROM glb_mutes WHERE chat_id = %s", (chat_id,))
            cursor.execute("DELETE FROM glb_chats WHERE chat_id = %s", (chat_id,))
        return {'chat_id': chat_id, 'deleted': True}

    def _glb_replace_chat_departments_tx(self, cursor, chat_id, department_names, created_by=None):
        cursor.execute("DELETE FROM glb_chat_departments WHERE chat_id = %s", (chat_id,))
        for name in department_names:
            cursor.execute("""
                INSERT INTO glb_chat_departments (chat_id, department_name, created_by)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (chat_id, name, created_by))

    def set_group_late_chat_departments(self, chat_id, departments, created_by=None, department=None):
        """Полная перезапись связки чат→отделы. Пустой список = чат получает все отделы.
        department сужает правку до чатов своего отдела."""
        chat_id = self._glb_chat_id(chat_id)
        department_names = self._glb_clean_departments(departments)
        dept = self._glb_department(department)
        scope = self._glb_chat_scope_sql('c.chat_id', dept)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT 1 FROM glb_chats c WHERE c.chat_id = %(chat_id)s AND ({scope})
            """, {'chat_id': chat_id, 'glb_dept': dept})
            if not cursor.fetchone():
                raise ValueError("Chat not found")
            self._glb_replace_chat_departments_tx(cursor, chat_id, department_names, created_by)
            cursor.execute("UPDATE glb_chats SET updated_at = NOW() WHERE chat_id = %s", (chat_id,))
        return {'chat_id': chat_id, 'departments': department_names}

    def get_group_late_available_chats(self):
        """Группы, в которых бот уже есть, но которых нет в рассылке.

        Telegram не даёт боту список своих чатов, поэтому берём из того, что мы
        сами записали при добавлении бота: общий реестр обнаруженных чатов
        (it_ticket_channels, туда пишет тот же обработчик my_chat_member).
        Так админ выбирает чат из списка, а не выясняет chat_id руками."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT c.chat_id::text, c.title, c.chat_type, c.last_seen_at
                FROM it_ticket_channels c
                WHERE c.is_active
                  AND c.chat_type IN ('group', 'supergroup')
                  AND NOT EXISTS (
                      SELECT 1 FROM glb_chats g WHERE g.chat_id = c.chat_id::text
                  )
                ORDER BY COALESCE(c.title, c.chat_id::text)
            """)
            items = [{
                'chat_id': row[0],
                'title': row[1],
                'chat_type': row[2],
                'seen_at': row[3].isoformat() if row[3] else None,
            } for row in cursor.fetchall()]
        return {'items': items}

    def get_group_late_departments(self, department=None):
        """Справочник отделов Workpace + к скольким чатам привязан каждый отдел.

        department — отдел Workpace, которым ограничен запрашивающий: тогда в
        справочнике остаётся только он один."""
        dept = self._glb_department(department)
        dept_filter = 'TRUE' if not dept else 'lower(d.name) = lower(%(glb_dept)s)'
        unknown_filter = ('TRUE' if not dept
                          else 'lower(cd.department_name) = lower(%(glb_dept)s)')
        params = {'glb_dept': dept}
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT d.name, d.employees_count, d.synced_at,
                       COALESCE(l.chats, '{{}}') AS chats
                FROM glb_departments d
                LEFT JOIN LATERAL (
                    SELECT array_agg(cd.chat_id ORDER BY cd.chat_id) AS chats
                    FROM glb_chat_departments cd
                    WHERE lower(cd.department_name) = lower(d.name)
                ) l ON TRUE
                WHERE {dept_filter}
                ORDER BY d.name
            """, params)
            items = [{
                'name': r[0],
                'employees_count': int(r[1] or 0),
                'synced_at': r[2].isoformat() if r[2] else None,
                'chat_ids': list(r[3] or []),
            } for r in cursor.fetchall()]

            # Отделы, закреплённые за чатами, но исчезнувшие из Workpace: чат
            # с таким фильтром молча не получает ничего — это видно в разделе.
            cursor.execute(f"""
                SELECT cd.department_name, array_agg(cd.chat_id ORDER BY cd.chat_id)
                FROM glb_chat_departments cd
                WHERE NOT EXISTS (
                    SELECT 1 FROM glb_departments d WHERE lower(d.name) = lower(cd.department_name)
                ) AND {unknown_filter}
                GROUP BY cd.department_name
                ORDER BY cd.department_name
            """, params)
            unknown = [{'name': r[0], 'chat_ids': list(r[1] or [])} for r in cursor.fetchall()]
        return {'items': items, 'unknown': unknown}

    def get_group_late_mutes(self, department=None):
        dept = self._glb_department(department)
        mute_scope = self._glb_mute_scope_sql('m', dept)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT m.id, m.chat_id, m.mute_kind, m.mute_value, m.created_by, m.created_at, c.title
                FROM glb_mutes m
                LEFT JOIN glb_chats c ON c.chat_id = m.chat_id
                WHERE {mute_scope}
                ORDER BY (m.chat_id IS NOT NULL), m.chat_id, m.mute_kind, m.mute_value
            """, {'glb_dept': dept})
            items = [{
                'id': int(r[0]),
                'chat_id': r[1],
                'mute_kind': r[2],
                'mute_value': r[3],
                'created_by': r[4],
                'created_at': r[5].isoformat() if r[5] else None,
                'chat_title': r[6],
            } for r in cursor.fetchall()]
        return {'items': items}

    def add_group_late_mute(self, mute_kind, mute_value=None, chat_id=None, created_by=None):
        kind = str(mute_kind or '').strip().lower()
        if kind not in self.GLB_MUTE_KINDS:
            raise ValueError("Invalid mute_kind")
        value = '' if kind == 'all' else str(mute_value or '').strip()[:200]
        if kind != 'all' and not value:
            raise ValueError("mute_value is required")
        scope_chat_id = self._glb_chat_id(chat_id) if chat_id else None
        with self._get_cursor() as cursor:
            if scope_chat_id:
                cursor.execute("SELECT 1 FROM glb_chats WHERE chat_id = %s", (scope_chat_id,))
                if not cursor.fetchone():
                    raise ValueError("Chat not found")
            cursor.execute("""
                INSERT INTO glb_mutes (chat_id, mute_kind, mute_value, created_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id
            """, (scope_chat_id, kind, value, created_by))
            row = cursor.fetchone()
        return {'id': int(row[0]) if row else None, 'created': bool(row)}

    def delete_group_late_mute(self, mute_id=None, mute_kind=None, mute_value=None, chat_id=None,
                               department=None):
        """department сужает удаление до правил своего отдела: чужое правило просто
        не находится и вызов возвращает «не найдено», как и для несуществующего id."""
        dept = self._glb_department(department)
        mute_scope = self._glb_mute_scope_sql('m', dept)
        with self._get_cursor() as cursor:
            if mute_id is not None:
                cursor.execute(f"""
                    DELETE FROM glb_mutes m
                    WHERE m.id = %(mute_id)s AND {mute_scope}
                """, {'mute_id': int(mute_id), 'glb_dept': dept})
            else:
                kind = str(mute_kind or '').strip().lower()
                if kind not in self.GLB_MUTE_KINDS:
                    raise ValueError("Invalid mute_kind")
                value = '' if kind == 'all' else str(mute_value or '').strip()
                scope_chat_id = self._glb_chat_id(chat_id) if chat_id else None
                cursor.execute(f"""
                    DELETE FROM glb_mutes m
                    WHERE COALESCE(m.chat_id, '') = COALESCE(%(chat_id)s, '')
                      AND m.mute_kind = %(kind)s
                      AND lower(m.mute_value) = lower(%(value)s)
                      AND {mute_scope}
                """, {'chat_id': scope_chat_id, 'kind': kind, 'value': value, 'glb_dept': dept})
            deleted = cursor.rowcount
        if not deleted:
            raise ValueError("Mute rule not found")
        return {'deleted': deleted}

    def get_group_late_events(
        self,
        date_from=None,
        date_to=None,
        event_type=None,
        department=None,
        chat_id=None,
        search=None,
        limit=100,
        offset=0,
    ):
        """Лента найденных нарушений с доставками по чатам."""
        limit_int = max(1, min(int(limit or 100), 500))
        offset_int = max(0, int(offset or 0))
        where, params = [], []

        from_date = self._glb_parse_date(date_from, 'date_from')
        to_date = self._glb_parse_date(date_to, 'date_to')
        if from_date:
            where.append("e.event_date >= %s")
            params.append(from_date)
        if to_date:
            where.append("e.event_date <= %s")
            params.append(to_date)

        type_text = str(event_type or '').strip().lower()
        if type_text:
            if type_text not in self.GLB_EVENT_TYPES:
                raise ValueError("Invalid event_type")
            where.append("e.event_type = %s")
            params.append(type_text)

        department_text = str(department or '').strip()
        if department_text:
            where.append("lower(e.department_name) = lower(%s)")
            params.append(department_text)

        if chat_id:
            where.append("EXISTS (SELECT 1 FROM glb_event_deliveries ed WHERE ed.event_id = e.id AND ed.chat_id = %s)")
            params.append(self._glb_chat_id(chat_id))

        search_text = str(search or '').strip()
        if search_text:
            where.append("(e.employee_name ILIKE %s OR e.department_name ILIKE %s)")
            params.extend([f"%{search_text}%", f"%{search_text}%"])

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self._get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM glb_events e {where_sql}", params)
            total = int(cursor.fetchone()[0] or 0)

            cursor.execute(f"""
                SELECT e.id, e.event_type, e.event_date, e.employee_name, e.department_name,
                       e.schedule_name, e.plan_at, e.fact_at, e.minutes, e.location,
                       e.message_text, e.detected_at,
                       COALESCE(d.deliveries, '[]'::json) AS deliveries
                FROM glb_events e
                LEFT JOIN LATERAL (
                    SELECT json_agg(json_build_object(
                               'chat_id', ed.chat_id,
                               'chat_title', c.title,
                               'message_id', ed.telegram_message_id,
                               'sent_at', ed.sent_at,
                               'error', ed.error
                           ) ORDER BY ed.sent_at) AS deliveries
                    FROM glb_event_deliveries ed
                    LEFT JOIN glb_chats c ON c.chat_id = ed.chat_id
                    WHERE ed.event_id = e.id
                ) d ON TRUE
                {where_sql}
                ORDER BY e.detected_at DESC, e.id DESC
                LIMIT %s OFFSET %s
            """, params + [limit_int, offset_int])
            items = []
            for r in cursor.fetchall():
                deliveries = r[12] or []
                items.append({
                    'id': int(r[0]),
                    'event_type': r[1],
                    'event_date': r[2].isoformat() if r[2] else None,
                    'employee_name': r[3],
                    'department_name': r[4],
                    'schedule_name': r[5],
                    'plan_at': r[6].isoformat() if r[6] else None,
                    'fact_at': r[7].isoformat() if r[7] else None,
                    'minutes': r[8],
                    'location': r[9],
                    'message_text': r[10],
                    'detected_at': r[11].isoformat() if r[11] else None,
                    'deliveries': deliveries,
                })
        return {'total': total, 'items': items, 'limit': limit_int, 'offset': offset_int}

    @classmethod
    def _glb_name_keys(cls, value):
        """(ключ по всему ФИО, ключ по «Фамилия Имя»).

        Второй нужен, потому что отчество есть не везде: в Workpace
        «Досанбаев Асан Тестович», у нас — «Досанбаев Асан»."""
        text = re.sub(r'[^0-9a-zа-яёәғқңөұүһі\s]', ' ', str(value or '').strip().lower())
        tokens = text.translate(cls._GLB_NAME_FOLD).split()
        if not tokens:
            return None, None
        return ' '.join(tokens), (' '.join(tokens[:2]) if len(tokens) > 1 else None)

    @staticmethod
    def _glb_index_put(store, key, value, weak=False):
        """Ключ ФИО → запись. Тёзки обнуляют ключ: показать чужого хуже, чем
        не показать никого. `weak` — запись, которая не спорит за занятый ключ
        (уволенный сотрудник против действующего)."""
        if not key:
            return
        if key not in store:
            store[key] = value
        elif weak or store[key] is value:
            return
        else:
            store[key] = None

    def _glb_department_staff(self, cursor, department_ids):
        """{id отдела: [карточка]} — наши сотрудники отделов с парой в Workpace.

        Читаем только отделы, у которых пара объявлена: справочники не связаны,
        ключ — ФИО, и по всей базе матчить нельзя — тёзка из соседнего отдела
        подставил бы чужое имя и чужой город. `active` — действующий оператор:
        такие задают состав таблицы, остальные (уволенные, руководители) попадут
        в неё, только если за период у них были нарушения."""
        staff = {}
        if not department_ids:
            return staff
        cursor.execute("""
            SELECT u.department_id, btrim(u.name), btrim(COALESCE(u.city, '')),
                   (lower(COALESCE(u.role, '')) = 'operator'
                    AND lower(COALESCE(u.status, '')) <> 'fired') AS active
            FROM users u
            WHERE u.department_id = ANY(%s) AND btrim(COALESCE(u.name, '')) <> ''
            ORDER BY active DESC, u.name, u.id
        """, (sorted(int(value) for value in department_ids),))
        for department_id, name, city, active in cursor.fetchall():
            staff.setdefault(int(department_id), []).append(
                {'name': name, 'city': city or None, 'active': bool(active)})
        return staff

    @staticmethod
    def _glb_one_edit_apart(left, right):
        """Слова различаются не больше чем одной буквой.

        Нужно для «Сынакабаева» ↔ «УНГАРБАЕВА»: в Workpace фамилию завели с
        опечаткой, а складыванием букв это не лечится."""
        if left == right:
            return True
        if abs(len(left) - len(right)) > 1:
            return False
        if len(left) > len(right):
            left, right = right, left
        index_left = index_right = 0
        edited = False
        while index_left < len(left) and index_right < len(right):
            if left[index_left] == right[index_right]:
                index_left += 1
                index_right += 1
                continue
            if edited:
                return False
            edited = True
            if len(left) == len(right):
                index_left += 1
            index_right += 1
        return True

    @staticmethod
    def _glb_stats_entry(name, city=None, matched=False, workpace_name=None, in_roster=False):
        return {
            'employee_name': name,
            'workpace_name': workpace_name,
            'city': city,
            'matched': matched,
            'in_roster': in_roster,
            'late_count': 0, 'late_minutes': 0,
            'early_out_count': 0, 'early_out_minutes': 0,
            'missing_count': 0, 'suspicious_count': 0,
            'events_total': 0,
            'last_event_date': None,
        }

    @classmethod
    def _glb_resolve_entry(cls, bucket, full_key, short_key):
        """Найти сотрудника отдела по ключам ФИО.

        Порядок: полное ФИО → «Фамилия Имя» → фамилия с опечаткой в одну букву
        при точно совпавшем имени. Последнее — про «Сынакабаева» ↔ «УНГАРБАЕВА»;
        включается, только когда кандидат в отделе ровно один, иначе можно
        приписать нарушения не тому."""
        entry = bucket['by_key'].get(full_key) if full_key else None
        if entry is None and short_key:
            entry = bucket['by_short'].get(short_key)
        if entry is not None or not short_key:
            return entry

        surname, _, first_name = short_key.partition(' ')
        if len(surname) < 5:
            return None
        found = None
        for candidate_key, candidate in bucket['by_short'].items():
            if candidate is None:
                continue
            candidate_surname, _, candidate_first = candidate_key.partition(' ')
            if candidate_first != first_name or len(candidate_surname) < 5:
                continue
            if not cls._glb_one_edit_apart(surname, candidate_surname):
                continue
            if found is not None and found is not candidate:
                return None      # два похожих — угадывать нельзя
            found = candidate
        return found

    @staticmethod
    def _glb_new_bucket(department_name, paired=False):
        return {
            'department_name': department_name,
            'paired': paired,
            'by_key': {}, 'by_short': {}, 'by_ext': {},
            'employees': [],        # строки таблицы
            'reserve': [],          # наши, но не действующие операторы — только с нарушениями
            'foreign': 0,           # в Workpace есть, в iCore нет
        }

    def get_group_late_employee_stats(self, date_from=None, date_to=None, department=None,
                                      search=None, match_departments=None):
        """Дисциплина по сотрудникам в разрезе отделов Workpace за период.

        В таблице ВЕСЬ состав отдела, а не только нарушители, а нарушения берутся
        из `glb_events` по дате смены — поэтому цифры сходятся с «Отбивками» и
        «Обзором». Откуда состав, зависит от того, знаем ли мы отдел у себя:

        * отдел с парой (match_departments) — состав из НАШЕЙ базы: действующие
          операторы этого отдела. В Workpace числятся и другие компании холдинга,
          и их сотрудников в таблице быть не должно; сколько таких, возвращаем
          отдельным счётчиком `foreign_employees`. Уволенные и руководители
          попадают в таблицу, только если за период у них были нарушения;
        * остальные отделы — состав из кэша `glb_employees` (его наполняет опрос):
          сверять не с чем, поэтому показываем всех, кого знает Workpace.

        match_departments — {отдел Workpace: id нашего отдела}. Справочники не
        связаны, ключ — ФИО, и без явной пары матчить нельзя: тёзка из соседнего
        отдела подставил бы чужое имя и чужой город.

        department — отдел Workpace, которым ограничен запрашивающий."""
        from_date = self._glb_parse_date(date_from, 'date_from')
        to_date = self._glb_parse_date(date_to, 'date_to')
        if from_date and to_date and from_date > to_date:
            raise ValueError("date_from is later than date_to")

        where, params = [], []
        if from_date:
            where.append("e.event_date >= %s")
            params.append(from_date)
        if to_date:
            where.append("e.event_date <= %s")
            params.append(to_date)
        dept = self._glb_department(department)
        if dept:
            where.append("lower(e.department_name) = lower(%s)")
            params.append(dept)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        # Пары «отдел Workpace ↔ наш отдел». Ключ приводим к регистру, но исходное
        # написание сохраняем: под ним отдел показывается и приходит в нарушениях.
        pairs = {}
        for name, department_id in (match_departments or {}).items():
            clean_name = str(name or '').strip()
            if not clean_name or department_id is None:
                continue
            if dept and clean_name.casefold() != dept.casefold():
                continue
            pairs[clean_name.casefold()] = (clean_name, int(department_id))

        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT COALESCE(NULLIF(btrim(e.department_name), ''), 'Без отдела') AS dept,
                       COALESCE(NULLIF(btrim(e.employee_name), ''), 'Без имени') AS employee,
                       COUNT(*) FILTER (WHERE e.event_type = 'late') AS late_count,
                       COALESCE(SUM(e.minutes) FILTER (WHERE e.event_type = 'late'), 0) AS late_minutes,
                       COUNT(*) FILTER (WHERE e.event_type = 'early_out') AS early_out_count,
                       COALESCE(SUM(e.minutes) FILTER (WHERE e.event_type = 'early_out'), 0) AS early_out_minutes,
                       COUNT(*) FILTER (WHERE e.event_type = 'missing') AS missing_count,
                       COUNT(*) FILTER (WHERE e.event_type = 'suspicious') AS suspicious_count,
                       COUNT(*) AS events_total,
                       MAX(e.event_date) AS last_event_date,
                       array_remove(array_agg(DISTINCT e.employee_ext_id), NULL) AS ext_ids
                FROM glb_events e
                {where_sql}
                GROUP BY 1, 2
            """, params)
            event_rows = cursor.fetchall()

            roster_sql = "" if not dept else "WHERE lower(p.department_name) = lower(%s)"
            cursor.execute(f"""
                SELECT COALESCE(NULLIF(btrim(p.department_name), ''), 'Без отдела') AS dept,
                       COALESCE(NULLIF(btrim(p.full_name), ''), 'Без имени') AS employee,
                       p.ext_id, p.external_id
                FROM glb_employees p
                {roster_sql}
                ORDER BY 1, 2
            """, ([dept] if dept else []))
            roster_rows = cursor.fetchall()

            # Состав приезжает с опросом Workpace: пока он не прошёл ни разу,
            # в таблице будут только нарушители — об этом честно говорим наверху.
            cursor.execute("SELECT COUNT(*), MAX(synced_at) FROM glb_employees")
            roster_total, roster_synced_at = cursor.fetchone()

            staff = self._glb_department_staff(
                cursor, {department_id for _, department_id in pairs.values()})

        buckets = {}

        # 1. Отделы с парой: состав берём У СЕБЯ. Кого нет в iCore, того в таблице
        #    быть не должно — в Workpace числятся и чужие компании холдинга.
        for pair_key, (workpace_name, department_id) in pairs.items():
            bucket = buckets.setdefault(workpace_name, self._glb_new_bucket(workpace_name, True))
            for card in staff.get(department_id, []):
                full_key, short_key = self._glb_name_keys(card['name'])
                if full_key and full_key in bucket['by_key']:
                    continue                # полный тёзка у нас — вторую строку не заводим
                entry = self._glb_stats_entry(card['name'], city=card['city'], matched=True,
                                              in_roster=card['active'])
                # Действующие операторы задают состав; уволенные и руководители
                # попадут в таблицу, только если за период были нарушения.
                (bucket['employees'] if card['active'] else bucket['reserve']).append(entry)
                if full_key:
                    bucket['by_key'][full_key] = entry
                self._glb_index_put(bucket['by_short'], short_key, entry)

        # 2. Остальные отделы: пары нет, сверять не с чем — берём состав Workpace.
        for department_name, employee_name, ext_id, external_id in roster_rows:
            if department_name.casefold() in pairs:
                bucket = buckets[pairs[department_name.casefold()][0]]
                full_key, short_key = self._glb_name_keys(employee_name)
                entry = self._glb_resolve_entry(bucket, full_key, short_key)
                if entry is None:
                    # Считаем, скольких Workpace знает сверх нашей базы: строки для
                    # них нет, но молча терять людей нельзя.
                    bucket['foreign'] += 1
                    continue
                # Идентификаторы Workpace вешаем на нашу карточку: по ним нарушения
                # найдут сотрудника даже там, где написание ФИО разошлось.
                entry['workpace_name'] = entry['workpace_name'] or employee_name
                for value in (ext_id, external_id):
                    if value:
                        bucket['by_ext'][str(value)] = entry
                continue
            bucket = buckets.setdefault(department_name, self._glb_new_bucket(department_name))
            full_key, short_key = self._glb_name_keys(employee_name)
            # Два одинаковых ФИО в отделе — это одна карточка, заведённая в Workpace
            # дважды (так, например, живёт Тестбаева Дильназ): у неё два id, и
            # нарушения приходят то под одним, то под другим. Строку не двоим —
            # тем более что сами нарушения группируются по ФИО и всё равно легли бы
            # в одну. Отчество различает однофамильцев, поэтому сливаем только по
            # ПОЛНОМУ совпадению.
            entry = bucket['by_key'].get(full_key) if full_key else None
            if entry is None:
                entry = self._glb_stats_entry(employee_name, workpace_name=employee_name,
                                              in_roster=True)
                bucket['employees'].append(entry)
                if full_key:
                    bucket['by_key'][full_key] = entry
                self._glb_index_put(bucket['by_short'], short_key, entry)
            for value in (ext_id, external_id):
                if value:
                    bucket['by_ext'][str(value)] = entry

        # 3. Нарушения наполняют строки.
        for row in event_rows:
            department_name, employee_name = row[0], row[1]
            pair = pairs.get(department_name.casefold())
            bucket = (buckets[pair[0]] if pair
                      else buckets.setdefault(department_name,
                                              self._glb_new_bucket(department_name)))
            full_key, short_key = self._glb_name_keys(employee_name)
            entry = next(
                (bucket['by_ext'][str(value)] for value in (row[10] or [])
                 if str(value) in bucket['by_ext']),
                None,
            ) or self._glb_resolve_entry(bucket, full_key, short_key)
            if entry is None and bucket['paired']:
                continue        # нарушение чужого сотрудника: в iCore его нет
            if entry is None:
                # Уволенный или переведённый: в составе его уже нет, а нарушения
                # за период есть — строку показываем всё равно.
                entry = self._glb_stats_entry(employee_name, workpace_name=employee_name)
                bucket['employees'].append(entry)
                self._glb_index_put(bucket['by_key'], full_key, entry)
                self._glb_index_put(bucket['by_short'], short_key, entry)
            if entry in bucket['reserve']:
                bucket['reserve'].remove(entry)
                bucket['employees'].append(entry)
            entry['workpace_name'] = entry['workpace_name'] or employee_name
            for index, field in enumerate(self.GLB_STATS_COUNTERS, start=2):
                entry[field] += int(row[index] or 0)
            entry['events_total'] += int(row[8] or 0)
            last_event = row[9].isoformat() if row[9] else None
            if last_event and (not entry['last_event_date'] or last_event > entry['last_event_date']):
                entry['last_event_date'] = last_event

        search_text = str(search or '').strip().casefold()
        overall = {'departments': 0, 'employees': 0, 'employees_with_violations': 0,
                   'late_count': 0, 'late_minutes': 0, 'early_out_count': 0,
                   'early_out_minutes': 0, 'missing_count': 0, 'suspicious_count': 0}
        departments = []
        for bucket in buckets.values():
            totals = {'employees': 0, 'employees_with_violations': 0, 'late_count': 0,
                      'late_minutes': 0, 'early_out_count': 0, 'early_out_minutes': 0,
                      'missing_count': 0, 'suspicious_count': 0}
            employees, has_city = [], False
            for entry in bucket['employees']:
                if search_text and not any(
                    search_text in field.casefold() for field in (
                        entry['employee_name'], entry['workpace_name'] or '',
                        bucket['department_name'], entry['city'] or '')
                ):
                    continue
                if entry['city']:
                    has_city = True
                employees.append(entry)
                totals['employees'] += 1
                violations = 0
                for field in self.GLB_STATS_COUNTERS:
                    totals[field] += entry[field]
                    if field.endswith('_count'):
                        violations += entry[field]
                if violations:
                    totals['employees_with_violations'] += 1
            if not employees:
                continue
            employees.sort(key=self._glb_stats_sort_key)
            departments.append({
                'department_name': bucket['department_name'],
                'has_city': has_city,
                # Отдел с парой: состав из iCore, ФИО и город — из карточки.
                'matched_department': bucket['paired'],
                'foreign_employees': bucket['foreign'] if bucket['paired'] else 0,
                'employees': employees,
                'totals': totals,
            })
            overall['departments'] += 1
            for field in ('employees', 'employees_with_violations') + self.GLB_STATS_COUNTERS:
                overall[field] += totals[field]
        departments.sort(key=lambda item: (
            -(item['totals']['late_count'] + item['totals']['early_out_count']
              + item['totals']['missing_count'] + item['totals']['suspicious_count']),
            item['department_name'],
        ))

        return {
            'date_from': from_date.isoformat() if from_date else None,
            'date_to': to_date.isoformat() if to_date else None,
            'department': dept,
            'roster_total': int(roster_total or 0),
            'roster_synced_at': roster_synced_at.isoformat() if roster_synced_at else None,
            'totals': overall,
            'departments': departments,
        }

    @staticmethod
    def _glb_stats_sort_key(entry):
        """Сначала нарушители (по опозданиям), в конце — те, к кому нет вопросов."""
        return (-entry['late_count'], -entry['late_minutes'],
                -(entry['missing_count'] + entry['suspicious_count'] + entry['early_out_count']),
                entry['employee_name'])

    def get_group_late_reports(self, date_from=None, date_to=None, status=None, source=None,
                               limit=50, offset=0, department=None):
        """department — отдел Workpace, которым ограничен запрашивающий: тогда в списке
        только отчёты, собранные ровно по этому отделу."""
        limit_int = max(1, min(int(limit or 50), 300))
        offset_int = max(0, int(offset or 0))
        where, params = [], []

        from_date = self._glb_parse_date(date_from, 'date_from')
        to_date = self._glb_parse_date(date_to, 'date_to')
        if from_date:
            where.append("r.created_at >= %s")
            params.append(datetime.combine(from_date, dt_time.min))
        if to_date:
            where.append("r.created_at < %s")
            params.append(datetime.combine(to_date, dt_time.min) + timedelta(days=1))

        status_text = str(status or '').strip().lower()
        if status_text in ('ok', 'error', 'running'):
            where.append("r.status = %s")
            params.append(status_text)
        source_text = str(source or '').strip().lower()
        if source_text in ('web', 'telegram'):
            where.append("r.source = %s")
            params.append(source_text)

        dept = self._glb_department(department)
        if dept:
            where.append("lower(r.department_filter) = lower(%s)")
            params.append(dept)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self._get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM glb_reports r {where_sql}", params)
            total = int(cursor.fetchone()[0] or 0)
            cursor.execute(f"""
                SELECT r.id, r.source, r.requested_chat_id, r.requested_by, r.date_from, r.date_to,
                       r.department_filter, r.employee_filter, r.status, r.error, r.file_name, r.file_size,
                       r.rows_count, r.employees_count, r.late_count, r.absent_count,
                       r.duration_ms, r.created_at, c.title,
                       EXISTS (SELECT 1 FROM glb_report_files f WHERE f.report_id = r.id) AS has_file
                FROM glb_reports r
                LEFT JOIN glb_chats c ON c.chat_id = r.requested_chat_id
                {where_sql}
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT %s OFFSET %s
            """, params + [limit_int, offset_int])
            items = [{
                'id': int(r[0]),
                'source': r[1],
                'requested_chat_id': r[2],
                'requested_by': r[3],
                'date_from': r[4].isoformat() if r[4] else None,
                'date_to': r[5].isoformat() if r[5] else None,
                'department_filter': r[6],
                # Список ФИО, по которым собран отчёт. Пусто — весь отдел.
                'employee_filter': [x for x in (r[7] or '').splitlines() if x.strip()],
                'status': r[8],
                'error': r[9],
                'file_name': r[10],
                'file_size': r[11],
                'rows_count': r[12],
                'employees_count': r[13],
                'late_count': r[14],
                'absent_count': r[15],
                'duration_ms': r[16],
                'created_at': r[17].isoformat() if r[17] else None,
                'chat_title': r[18],
                'has_file': bool(r[19]),
            } for r in cursor.fetchall()]
        return {'total': total, 'items': items, 'limit': limit_int, 'offset': offset_int}

    def get_group_late_report_file(self, report_id, department=None):
        """department сужает выдачу до отчётов своего отдела: чужой файл не найдётся."""
        dept = self._glb_department(department)
        scope = 'TRUE' if not dept else 'lower(r.department_filter) = lower(%(glb_dept)s)'
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT r.file_name, f.content
                FROM glb_reports r
                JOIN glb_report_files f ON f.report_id = r.id
                WHERE r.id = %(report_id)s AND {scope}
            """, {'report_id': int(report_id), 'glb_dept': dept})
            row = cursor.fetchone()
        if not row:
            return None
        return {'file_name': row[0], 'content': bytes(row[1])}

    # ---- сторона бота: опрос Workpace, отправка, отбивки ----
    # Всё синхронное: джоба крутится в пуле потоков, event loop бота не занимаем.

    def glb_start_poll_run(self):
        with self._get_cursor() as cursor:
            cursor.execute("INSERT INTO glb_poll_runs (started_at) VALUES (NOW()) RETURNING id")
            return int(cursor.fetchone()[0])

    def glb_finish_poll_run(self, run_id, ok, fetched=0, events_found=0, sent=0, error=None):
        if not run_id:
            return
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE glb_poll_runs
                SET finished_at = NOW(), ok = %s, fetched = %s, events_found = %s, sent = %s,
                    error = %s,
                    duration_ms = GREATEST(0, (EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000)::int)
                WHERE id = %s
            """, (bool(ok), int(fetched or 0), int(events_found or 0), int(sent or 0),
                  (error or None), int(run_id)))

    def glb_get_routing(self):
        """Куда рассылать: включённые чаты со своими отделами. Один запрос за опрос."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT c.chat_id, c.title,
                       COALESCE(array_agg(d.department_name)
                                FILTER (WHERE d.department_name IS NOT NULL), '{}') AS departments
                FROM glb_chats c
                LEFT JOIN glb_chat_departments d ON d.chat_id = c.chat_id
                WHERE c.enabled
                GROUP BY c.chat_id, c.title
                ORDER BY c.is_admin_chat DESC, c.created_at
            """)
            return [
                {'chat_id': row[0], 'title': row[1], 'departments': list(row[2] or [])}
                for row in cursor.fetchall()
            ]

    def glb_get_mute_rows(self):
        with self._get_cursor() as cursor:
            cursor.execute("SELECT chat_id, mute_kind, mute_value FROM glb_mutes")
            return [
                {'chat_id': row[0], 'mute_kind': row[1], 'mute_value': row[2]}
                for row in cursor.fetchall()
            ]

    def glb_known_event_keys(self, event_date):
        """Ключи уже найденных за день нарушений: опрос идёт раз в 2 минуты и
        иначе на каждом круге пытался бы вставить те же события заново."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT event_key FROM glb_events WHERE event_date = %s", (event_date,))
            return {row[0] for row in cursor.fetchall()}

    def glb_drop_event(self, event_id):
        """Вернуть событие в очередь: ни в один чат отправить не удалось."""
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM glb_events WHERE id = %s", (int(event_id),))

    def glb_record_delivery(self, event_id, chat_id, message_id=None, error=None):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO glb_event_deliveries (event_id, chat_id, telegram_message_id, error)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (event_id, chat_id) DO UPDATE SET
                    telegram_message_id = EXCLUDED.telegram_message_id,
                    sent_at = NOW(),
                    error = EXCLUDED.error
            """, (int(event_id), str(chat_id), message_id, error))
            if message_id and not error:
                cursor.execute(
                    "UPDATE glb_chats SET last_delivery_at = NOW() WHERE chat_id = %s", (str(chat_id),)
                )

    def glb_sync_departments(self, counts):
        """Кэш справочника отделов Workpace: раздел на сайте в Workpace не ходит."""
        if not counts:
            return 0
        with self._get_cursor() as cursor:
            for name, employees_count in counts.items():
                cursor.execute("""
                    INSERT INTO glb_departments (name, employees_count, synced_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (name) DO UPDATE SET
                        employees_count = EXCLUDED.employees_count, synced_at = NOW()
                """, (name, int(employees_count)))
            # Отдел, исчезнувший из Workpace, из справочника убираем: связку чата с
            # ним раздел покажет отдельно как «фильтр, которого нет в Workpace».
            cursor.execute("DELETE FROM glb_departments WHERE name <> ALL(%s)", (list(counts.keys()),))
        return len(counts)

    def glb_sync_employees(self, rows, source='workpace'):
        """Кэш состава для вкладки «Сотрудники» и выбора людей в отчёт.

        Полная замена — но ТОЛЬКО внутри своего источника: Workpace отдаёт лишь
        действующих, поэтому уволенный должен исчезнуть из состава. В таблице он
        всё равно останется, если за период у него были нарушения, — их берём из
        glb_events.

        Источников теперь два (задача #307), и чистка обязана считаться по
        `source`: опрос Workpace идёт каждые две минуты, и без этого условия он
        стирал бы из состава весь центральный офис между своими проходами."""
        source = str(source or 'workpace').strip().lower() or 'workpace'
        clean = []
        seen = set()
        for row in rows or []:
            ext_id = str(row.get('ext_id') or '').strip()
            full_name = str(row.get('full_name') or '').strip()
            if not ext_id or not full_name or ext_id in seen:
                continue
            seen.add(ext_id)
            clean.append((
                ext_id[:128],
                (str(row.get('external_id') or '').strip() or None),
                full_name[:200],
                (str(row.get('department_name') or '').strip() or None),
                (str(row.get('position_name') or '').strip() or None),
                source,
            ))
        if not clean:
            return 0
        with self._get_cursor() as cursor:
            execute_values(cursor, """
                INSERT INTO glb_employees (ext_id, external_id, full_name, department_name,
                                           position_name, source, synced_at)
                VALUES %s
                ON CONFLICT (ext_id) DO UPDATE SET
                    external_id = EXCLUDED.external_id,
                    full_name = EXCLUDED.full_name,
                    department_name = EXCLUDED.department_name,
                    position_name = EXCLUDED.position_name,
                    source = EXCLUDED.source,
                    synced_at = NOW()
            """, clean, template="(%s, %s, %s, %s, %s, %s, NOW())")
            cursor.execute(
                "DELETE FROM glb_employees WHERE source = %s AND ext_id <> ALL(%s)",
                (source, [item[0] for item in clean]))
        return len(clean)

    def glb_cached_employee_roster(self, department_names=None, source='workpace'):
        """Состав Workpace из кэша `glb_employees` в том же виде, что `employee_roster`.

        Раздел на сайте в Workpace не ходит: справочник ему достаётся опросом.
        Живой список нужен только самому опросу — там он и так уже загружен.

        `source` по умолчанию workpace: с появлением второго источника (#307) в
        той же таблице лежит и центральный офис, а карточки Clockster этому
        методу нужны только там, где их спросили явно. `source=None` — обе."""
        names = [str(name or '').strip() for name in (department_names or [])]
        names = [name for name in names if name]
        src_filter = '' if source is None else ' AND source = %s'
        src_params = () if source is None else (str(source),)
        with self._get_cursor() as cursor:
            if names:
                cursor.execute(f"""
                    SELECT ext_id, external_id, full_name, department_name
                    FROM glb_employees
                    WHERE lower(COALESCE(department_name, '')) = ANY(%s){src_filter}
                    ORDER BY full_name
                """, ([name.lower() for name in names],) + src_params)
            else:
                cursor.execute(f"""
                    SELECT ext_id, external_id, full_name, department_name
                    FROM glb_employees WHERE TRUE{src_filter} ORDER BY full_name
                """, src_params)
            return [
                {'ext_id': row[0], 'external_id': row[1],
                 'full_name': row[2], 'department_name': row[3]}
                for row in cursor.fetchall()
            ]

    def glb_employee_links(self):
        """{ext_id Workpace: id нашего сотрудника или None} — ручные привязки.

        None означает явное «это не наш сотрудник»: строка есть, значения нет.
        Поэтому вернуть просто словарь без None нельзя — исключение потерялось бы."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT workpace_ext_id, user_id FROM glb_employee_links")
            return {row[0]: (int(row[1]) if row[1] is not None else None)
                    for row in cursor.fetchall()}

    def glb_set_employee_link(self, workpace_ext_id, user_id, linked_by=None):
        """Привязать карточку Workpace к сотруднику или (user_id=None) исключить её."""
        ext_id = str(workpace_ext_id or '').strip()
        if not ext_id:
            raise ValueError("workpace_ext_id is required")
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO glb_employee_links (workpace_ext_id, user_id, linked_by, linked_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (workpace_ext_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    linked_by = EXCLUDED.linked_by,
                    linked_at = NOW()
            """, (ext_id[:128], (int(user_id) if user_id is not None else None),
                  (int(linked_by) if linked_by is not None else None)))
        return {'workpace_ext_id': ext_id, 'user_id': user_id}

    def glb_delete_employee_link(self, workpace_ext_id):
        """Снять ручную привязку — карточка вернётся к автоматическому матчингу по ФИО."""
        ext_id = str(workpace_ext_id or '').strip()
        if not ext_id:
            raise ValueError("workpace_ext_id is required")
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM glb_employee_links WHERE workpace_ext_id = %s", (ext_id,))
            return {'workpace_ext_id': ext_id, 'deleted': cursor.rowcount > 0}

    def _glb_plan_department_ids(self, cursor, department_codes):
        """{код отдела: id} — id засеяны миграцией и в разных окружениях разные."""
        codes = [str(code or '').strip().lower() for code in (department_codes or [])]
        codes = [code for code in codes if code]
        if not codes:
            return {}
        cursor.execute(
            "SELECT id, lower(code) FROM departments WHERE lower(code) = ANY(%s)", (codes,))
        return {row[1]: int(row[0]) for row in cursor.fetchall()}

    def glb_icore_plan_snapshot(self, department_pairs, date_from, date_to, workpace_employees):
        """План из графика iCore + мост «карточка Workpace → наш сотрудник».

        `department_pairs` — {название отдела Workpace: код нашего отдела}: только
        те отделы, у которых план берётся у нас. `workpace_employees` — состав из
        Workpace в виде `employee_roster()`: ext_id, ФИО, отдел.

        Возвращает всё, что нужно сборщику плана, одним походом в базу:
        `people` — наши сотрудники этих отделов с привязанными карточками Workpace
        (пустой список карточек = отметок по человеку нам взять негде),
        `shifts` — смены за период, `unlinked` — карточки Workpace без нашего
        сотрудника. Матчинг ФИО тот же, что во вкладке «Сотрудники»
        (`_glb_resolve_entry`): держать два разных правила нельзя, иначе таблица и
        отбивки разойдутся на одних и тех же людях."""
        pairs = {}
        for workpace_name, code in (department_pairs or {}).items():
            clean_name = str(workpace_name or '').strip()
            clean_code = str(code or '').strip().lower()
            if clean_name and clean_code:
                pairs[clean_name.casefold()] = (clean_name, clean_code)
        if not pairs:
            return {'people': [], 'shifts': [], 'unlinked': [], 'departments': {}}

        from_date = self._glb_parse_date(date_from, 'date_from')
        to_date = self._glb_parse_date(date_to, 'date_to')
        if not from_date or not to_date:
            raise ValueError("date_from and date_to are required")
        if from_date > to_date:
            raise ValueError("date_from is later than date_to")

        manual_links = self.glb_employee_links()

        with self._get_cursor() as cursor:
            code_to_id = self._glb_plan_department_ids(cursor, {code for _, code in pairs.values()})
            department_ids = sorted(set(code_to_id.values()))
            if not department_ids:
                return {'people': [], 'shifts': [], 'unlinked': [], 'departments': {}}

            # Состав: действующие сотрудники отдела любой роли. Раньше — только
            # операторы («руководители по графику не работают»), но с 09.11.2026
            # график в «Графиках работы» ведут всем ролям, и человек без смен плана
            # всё равно не получит. Уволенных не берём — смен у них уже нет.
            cursor.execute("""
                SELECT u.id, btrim(u.name), btrim(COALESCE(u.city, '')), u.department_id
                FROM users u
                WHERE u.department_id = ANY(%s)
                  AND lower(COALESCE(u.status, '')) <> 'fired'
                  AND btrim(COALESCE(u.name, '')) <> ''
                ORDER BY u.name, u.id
            """, (department_ids,))
            staff_rows = cursor.fetchall()

            cursor.execute("""
                SELECT ws.operator_id, ws.shift_date, ws.start_time, ws.end_time
                FROM work_shifts ws
                JOIN users u ON u.id = ws.operator_id
                WHERE u.department_id = ANY(%s)
                  AND ws.shift_date BETWEEN %s AND %s
                  AND NOT EXISTS (
                      SELECT 1 FROM operator_schedule_status_periods p
                      WHERE p.operator_id = ws.operator_id
                        AND p.status_code = ANY(%s)
                        AND p.start_date <= ws.shift_date
                        AND COALESCE(p.end_date, DATE '9999-12-31') >= ws.shift_date
                  )
                ORDER BY ws.operator_id, ws.shift_date, ws.start_time
            """, (department_ids, from_date, to_date,
                  list(self.GLB_PLAN_BLOCKING_STATUSES)))
            shift_rows = cursor.fetchall()

        id_to_code = {department_id: code for code, department_id in code_to_id.items()}
        people = {}
        buckets = {}
        for user_id, name, city, department_id in staff_rows:
            code = id_to_code.get(int(department_id))
            if not code:
                continue
            person = {
                'user_id': int(user_id), 'name': name, 'city': city or None,
                'department_code': code, 'workpace_ext_ids': [], 'cards': [],
                'link_source': None,
            }
            people[int(user_id)] = person
            bucket = buckets.setdefault(code, {'by_key': {}, 'by_short': {}})
            full_key, short_key = self._glb_name_keys(name)
            self._glb_index_put(bucket['by_key'], full_key, person)
            self._glb_index_put(bucket['by_short'], short_key, person)

        unlinked = []
        for employee in (workpace_employees or []):
            ext_id = str(employee.get('ext_id') or '').strip()
            if not ext_id:
                continue
            pair = pairs.get(str(employee.get('department_name') or '').strip().casefold())
            if not pair:
                continue
            workpace_name, code = pair
            full_name = str(employee.get('full_name') or '').strip()

            if ext_id in manual_links:
                linked_id = manual_links[ext_id]
                if linked_id is None:
                    # Явно исключённая карточка. В план она не попадает, но в
                    # список отдаётся: иначе исключение нельзя было бы снять —
                    # карточка просто исчезла бы из раздела.
                    unlinked.append({'ext_id': ext_id, 'full_name': full_name,
                                     'department_name': workpace_name, 'reason': 'excluded'})
                    continue
                person = people.get(linked_id)
                if person is not None:
                    person['workpace_ext_ids'].append(ext_id)
                    person['cards'].append({'ext_id': ext_id, 'full_name': full_name,
                                            'source': 'manual'})
                    person['link_source'] = 'manual'
                    continue
                # Привязали к тому, кого уже нет в составе отдела: строка осталась,
                # человека нет — показываем как несопоставленную карточку.
                unlinked.append({'ext_id': ext_id, 'full_name': full_name,
                                 'department_name': workpace_name, 'reason': 'stale_link'})
                continue

            full_key, short_key = self._glb_name_keys(full_name)
            person = self._glb_resolve_entry(buckets.get(code) or {'by_key': {}, 'by_short': {}},
                                             full_key, short_key)
            if person is None:
                unlinked.append({'ext_id': ext_id, 'full_name': full_name,
                                 'department_name': workpace_name, 'reason': 'no_match'})
                continue
            person['workpace_ext_ids'].append(ext_id)
            person['cards'].append({'ext_id': ext_id, 'full_name': full_name,
                                    'source': 'name'})
            if person['link_source'] != 'manual':
                person['link_source'] = 'name'

        shifts = [
            {'user_id': int(row[0]), 'date': row[1], 'start': row[2], 'end': row[3]}
            for row in shift_rows if int(row[0]) in people
        ]
        return {
            'people': list(people.values()),
            'shifts': shifts,
            'unlinked': unlinked,
            'departments': {code: workpace_name for workpace_name, code in pairs.values()},
        }

    def glb_discover_chat(self, chat_id, title=None, chat_type=None):
        """Бота добавили в группу — запоминаем чат ВЫКЛЮЧЕННЫМ. Рассылку по нему
        включают в разделе: иначе любая случайная группа начала бы получать
        нарушения по всей компании."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO glb_chats (chat_id, title, chat_type, enabled, created_by)
                VALUES (%s, %s, %s, FALSE, 'discovered')
                ON CONFLICT (chat_id) DO UPDATE SET
                    title = COALESCE(NULLIF(EXCLUDED.title, ''), glb_chats.title),
                    chat_type = COALESCE(EXCLUDED.chat_type, glb_chats.chat_type),
                    updated_at = NOW()
                RETURNING (xmax = 0) AS inserted
            """, (self._glb_chat_id(chat_id), (title or '').strip() or None, chat_type))
            return bool(cursor.fetchone()[0])

    def glb_forget_chat(self, chat_id):
        """Бота выгнали из чата — выключаем рассылку, историю оставляем."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE glb_chats SET enabled = FALSE, updated_at = NOW()
                WHERE chat_id = %s AND enabled
            """, (str(chat_id),))
            return cursor.rowcount > 0

    def glb_update_chat_title(self, chat_id, title):
        title = str(title or '').strip()[:200]
        if not title:
            return
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE glb_chats SET title = %s, updated_at = NOW()
                WHERE chat_id = %s AND COALESCE(title, '') IS DISTINCT FROM %s
            """, (title, str(chat_id), title))

    def glb_get_chat(self, chat_id):
        """Чат со своими отделами — для гейта команд бота в группах."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT c.chat_id, c.title, c.enabled, c.is_admin_chat,
                       COALESCE(array_agg(d.department_name)
                                FILTER (WHERE d.department_name IS NOT NULL), '{}') AS departments
                FROM glb_chats c
                LEFT JOIN glb_chat_departments d ON d.chat_id = c.chat_id
                WHERE c.chat_id = %s
                GROUP BY c.chat_id, c.title, c.enabled, c.is_admin_chat
            """, (str(chat_id),))
            row = cursor.fetchone()
        if not row:
            return None
        return {'chat_id': row[0], 'title': row[1], 'enabled': bool(row[2]),
                'is_admin_chat': bool(row[3]), 'departments': list(row[4] or [])}

    def glb_create_report(self, date_from, date_to, department=None, source='telegram',
                          requested_chat_id=None, requested_by=None, employees=None):
        """Карточка отчёта со статусом «формируется»: раздел показывает её сразу,
        не дожидаясь, пока Workpace отдаст все дни периода."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO glb_reports (source, requested_chat_id, requested_by, date_from, date_to,
                                         department_filter, employee_filter, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'running')
                RETURNING id
            """, (source, requested_chat_id, requested_by,
                  self._glb_parse_date(date_from, 'date_from'),
                  self._glb_parse_date(date_to, 'date_to'),
                  (department or None),
                  ("\n".join(employees) if employees else None)))
            return int(cursor.fetchone()[0])

    def glb_finish_report(self, report_id, file_bytes=None, file_name=None, stats=None,
                          error=None, duration_ms=None):
        if not report_id:
            return
        stats = stats or {}
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE glb_reports
                SET status = %s, error = %s, file_name = %s, file_size = %s,
                    rows_count = %s, employees_count = %s, late_count = %s, absent_count = %s,
                    duration_ms = %s
                WHERE id = %s
            """, (
                'ok' if file_bytes else 'error',
                (error or None),
                (file_name or None),
                len(file_bytes) if file_bytes else None,
                stats.get('rows_count'), stats.get('employees_count'),
                stats.get('late_count'), stats.get('absent_count'),
                int(duration_ms) if duration_ms is not None else None,
                int(report_id),
            ))
            if file_bytes:
                cursor.execute("""
                    INSERT INTO glb_report_files (report_id, content)
                    VALUES (%s, %s)
                    ON CONFLICT (report_id) DO UPDATE SET content = EXCLUDED.content
                """, (int(report_id), psycopg2.Binary(file_bytes)))

    def glb_cleanup_history(self, events_days=180, report_files_days=60, poll_runs_days=7):
        """Чистка по ретеншну. Журнал опросов пишется каждые 2 минуты, так что без
        неё эта таблица растёт быстрее всех остальных вместе взятых."""
        now = time.time()
        if now - getattr(self, '_glb_last_cleanup_ts', 0) < 6 * 3600:
            return False
        self._glb_last_cleanup_ts = now
        try:
            with self._get_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM glb_poll_runs WHERE started_at < NOW() - make_interval(days => %s)",
                    (int(poll_runs_days),))
                # Карточку отчёта оставляем как след в истории, тяжёлый Excel — нет.
                cursor.execute("""
                    DELETE FROM glb_report_files
                    WHERE report_id IN (
                        SELECT id FROM glb_reports WHERE created_at < NOW() - make_interval(days => %s)
                    )
                """, (int(report_files_days),))
                cursor.execute(
                    "DELETE FROM glb_events WHERE detected_at < NOW() - make_interval(days => %s)",
                    (int(events_days),))
        except Exception:
            logging.exception("group_late: чистка истории не удалась")
            return False
        return True

    def get_group_late_poll_runs(self, limit=30):
        limit_int = max(1, min(int(limit or 30), 200))
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, started_at, finished_at, ok, fetched, events_found, sent, duration_ms, error
                FROM glb_poll_runs
                ORDER BY started_at DESC
                LIMIT %s
            """, (limit_int,))
            items = [{
                'id': int(r[0]),
                'started_at': r[1].isoformat() if r[1] else None,
                'finished_at': r[2].isoformat() if r[2] else None,
                'ok': r[3],
                'fetched': r[4],
                'events_found': r[5],
                'sent': r[6],
                'duration_ms': r[7],
                'error': r[8],
            } for r in cursor.fetchall()]
        return {'items': items}

    @staticmethod
    def _glb_day(value):
        """'ГГГГ-ММ-ДД' / date / datetime → date. Пустое значение → None."""
        if value is None or value == '':
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()

    def glb_attendance_built_days(self, day_from, day_to, settle_from_version=None, settle_hour=None,
                                  fresh_minutes=None):
        """Множество дней периода, уже собранных в кэш.

        Без критериев — все сохранённые дни. `settle_hour` — день, собранный
        правилами версии не ниже `settle_from_version`, окончателен, только если
        собран после этого часа следующего дня (по Алматы): уход ночной смены
        лежит завтра (ТЗ iCore 3, п. 4). Неокончательный день всё же считается
        собранным, если собран не раньше `fresh_minutes` назад, — иначе каждое
        утреннее открытие пересобирало бы вчерашний день. Дни прежних версий
        окончательны, как и были: историю задним числом не пересчитываем."""
        start, end = self._glb_day(day_from), self._glb_day(day_to)
        if not start or not end:
            return set()
        conditions = ['day BETWEEN %s AND %s']
        params = [start, end]
        if settle_hour is not None:
            settled = ("built_at >= ((day + 1)::timestamp + make_interval(hours => %s)) "
                       "AT TIME ZONE 'Asia/Almaty'")
            params.append(int(settle_hour))
            if fresh_minutes is not None:
                settled += " OR built_at >= NOW() - make_interval(mins => %s)"
                params.append(int(fresh_minutes))
            if settle_from_version is not None:
                settled += " OR engine_version < %s"
                params.append(int(settle_from_version))
            conditions.append(f"({settled})")
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT day FROM glb_attendance_days
                WHERE {' AND '.join(conditions)}
            """, params)
            return {row[0] for row in cursor.fetchall()}

    def glb_store_attendance_day(self, day, rows, sources=None, engine_version=1):
        """Кладёт в кэш ОДИН день целиком, заменяя всё, что там было.

        Замена, а не доливка: смену в источнике могли отменить, и старая строка
        иначе осталась бы висеть неявкой навсегда. День без единой строки тоже
        отмечается собранным — иначе выходной тянулся бы из Workpace заново при
        каждом открытии раздела."""
        target = self._glb_day(day)
        if not target:
            return 0
        payload = []
        seq_by_employee = {}
        for row in rows or []:
            employee_id = str(row.get('employee_id') or '').strip()
            if not employee_id:
                continue
            employee_id = employee_id[:128]
            seq = seq_by_employee.get(employee_id, 0)
            seq_by_employee[employee_id] = seq + 1
            payload.append((
                target,
                employee_id,
                seq,
                str(row.get('employee') or '—')[:200],
                (str(row.get('department') or '').strip() or None),
                (str(row.get('position') or '').strip() or None),
                (str(row.get('location') or '').strip() or None),
                (str(row.get('schedule') or '').strip() or None),
                str(row.get('system') or 'workpace')[:32],
                row.get('plan_in'), row.get('plan_out'),
                row.get('fact_in'), row.get('fact_out'),
                int(row.get('late_minutes') or 0),
                int(row.get('early_out_minutes') or 0),
                int(row.get('work_seconds') or 0),
                int(row.get('present_seconds') or 0),
                int(row.get('lunch_seconds') or 0),
                str(row.get('status') or 'ok')[:32],
                str(row.get('plan_mode') or 'schedule')[:16],
                (str(row.get('plan_source') or '').strip() or None),
                row.get('hours_norm'),
                Json(row.get('marks') or []),
            ))
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM glb_attendance_rows WHERE day = %s", (target,))
            if payload:
                execute_values(cursor, f"""
                    INSERT INTO glb_attendance_rows ({', '.join(self.GLB_ATTENDANCE_COLUMNS)})
                    VALUES %s
                """, payload)
            cursor.execute("""
                INSERT INTO glb_attendance_days (day, built_at, rows_count, sources, engine_version)
                VALUES (%s, NOW(), %s, %s, %s)
                ON CONFLICT (day) DO UPDATE SET
                    built_at = NOW(), rows_count = EXCLUDED.rows_count,
                    sources = EXCLUDED.sources, engine_version = EXCLUDED.engine_version
            """, (target, len(payload), (sources or None), int(engine_version or 1)))
        return len(payload)

    def glb_read_attendance_rows(self, day_from, day_to, query=None, days=None):
        """Строки раздела из кэша — в том же виде, в каком их строит attendance.

        Фильтры подразделения, типа отметки и состава ЗДЕСЬ не применяются
        намеренно: живой путь фильтрует их в Python, и второй набор правил в SQL
        неминуемо разошёлся бы с ним на нестрогом сравнении названий отделов.
        В SQL остаётся то, что по-настоящему режет объём, — период и поиск."""
        start, end = self._glb_day(day_from), self._glb_day(day_to)
        if not start or not end:
            return []
        wanted = None
        if days is not None:
            wanted = sorted({self._glb_day(value) for value in days if self._glb_day(value)})
            if not wanted:
                return []
        conditions = ['day BETWEEN %s AND %s']
        params = [start, end]
        if wanted is not None:
            conditions.append('day = ANY(%s)')
            params.append(wanted)
        needle = str(query or '').strip()
        if needle:
            conditions.append('(employee_name ILIKE %s OR COALESCE(position_name, \'\') ILIKE %s)')
            params.extend([f'%{needle}%'] * 2)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {', '.join(self.GLB_ATTENDANCE_COLUMNS)}
                FROM glb_attendance_rows
                WHERE {' AND '.join(conditions)}
                ORDER BY day, employee_name, seq
            """, params)
            return [dict(zip(self.GLB_ATTENDANCE_COLUMNS, row)) for row in cursor.fetchall()]

    def glb_latest_attendance_rows(self, day_before):
        """По каждому сотруднику — строки его последнего дня с отметкой до `day_before`.

        Режим «Последние отметки» раздела: список без привязки к дате, где каждый
        человек стоит днём, когда отметился в последний раз. Свежие дни (от
        `day_before`) сюда не входят — их отдаёт `rows_for` по тем же правилам,
        что экран за период. День без прихода и ухода (неявка, «ждём прихода»)
        отметкой не считается. Подразделение и поиск, как и у
        `glb_read_attendance_rows`, режутся в Python."""
        edge = self._glb_day(day_before)
        if not edge:
            return []
        columns = ', '.join(f'r.{column}' for column in self.GLB_ATTENDANCE_COLUMNS)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                WITH latest AS (
                    SELECT employee_id, MAX(day) AS day
                    FROM glb_attendance_rows
                    WHERE day < %s AND (fact_in IS NOT NULL OR fact_out IS NOT NULL)
                    GROUP BY employee_id
                )
                SELECT {columns}
                FROM glb_attendance_rows r
                JOIN latest l ON l.employee_id = r.employee_id AND l.day = r.day
                WHERE r.fact_in IS NOT NULL OR r.fact_out IS NOT NULL
                ORDER BY r.day DESC, r.employee_name, r.seq
            """, (edge,))
            return [dict(zip(self.GLB_ATTENDANCE_COLUMNS, row)) for row in cursor.fetchall()]

    def glb_forget_attendance_days(self, day_from=None, day_to=None):
        """Сбрасывает кэш дней: правила плана поменялись — прошлое надо пересчитать."""
        start, end = self._glb_day(day_from), self._glb_day(day_to)
        conditions, params = [], []
        if start:
            conditions.append('day >= %s')
            params.append(start)
        if end:
            conditions.append('day <= %s')
            params.append(end)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ''
        with self._get_cursor() as cursor:
            cursor.execute(f"DELETE FROM glb_attendance_rows {where}", params)
            cursor.execute(f"DELETE FROM glb_attendance_days {where}", params)
            return cursor.rowcount or 0

    def _glb_reopen_attendance_days_tx(self, cursor, change_rows):
        """Правка графика задним числом → день «Отметок» пересобирается.

        У отделов, чей план раздел берёт из «Графиков работы»
        (`group_late.config.ICORE_PLAN_DEPARTMENTS`), прошедший день уже лежит в
        кэше со старым планом и сам не меняется: смену внесли позже, а человек
        так и стоит «Вне графика». День не стираем, а возвращаем в
        неокончательные: строки остаются на экране, и раздел пересобирает день
        при следующем открытии периода или ночным досбором. Запись реестра дня
        остаётся на месте, поэтому часы СВ, которые читают тот же реестр, не
        задеты. День, собранный прежними правилами, окончателен, как и был.

        `change_rows` — строки журнала правок (`_diff_schedule_day`). Сегодня и
        будущее не в счёт: сегодняшний день раздел и так собирает живьём.
        Выходной не в счёт: план складывается из смен. Работает внутри
        транзакции правки графика и под SAVEPOINT — сбой здесь не должен
        отменить саму правку."""
        savepoint = False
        try:
            from group_late import config as glb_config

            codes = sorted({str(code or '').strip().lower()
                            for code in (glb_config.ICORE_PLAN_DEPARTMENTS or {}).values()} - {''})
            if not codes:
                return 0
            today = datetime.now(glb_config.TZ).date()
            edge = today - timedelta(days=self.GLB_ATTENDANCE_REOPEN_DAYS)
            days_by_operator = {}
            for row in change_rows or []:
                operator_id, day, action = row[0], row[1], row[2]
                if action in ('day_off_set', 'day_off_cleared'):
                    continue
                if isinstance(day, datetime):
                    day = day.date()
                if isinstance(day, date) and edge <= day < today:
                    days_by_operator.setdefault(int(operator_id), set()).add(day)
            if not days_by_operator:
                return 0

            cursor.execute("SAVEPOINT sp_glb_reopen_attendance")
            savepoint = True
            cursor.execute("""
                SELECT u.id
                FROM users u
                JOIN departments d ON d.id = u.department_id
                WHERE u.id = ANY(%s) AND lower(d.code) = ANY(%s)
            """, (sorted(days_by_operator), codes))
            days = sorted({day for (operator_id,) in cursor.fetchall()
                           for day in days_by_operator.get(int(operator_id), ())})
            reopened = 0
            if days:
                # Полночь самого дня заведомо раньше порога окончательности (16:00
                # следующего дня) — тем же признаком раздел отличает день, чьи
                # ночные смены ещё не закрылись.
                cursor.execute("""
                    UPDATE glb_attendance_days
                    SET built_at = day::timestamp AT TIME ZONE 'Asia/Almaty'
                    WHERE day = ANY(%s)
                """, (days,))
                reopened = cursor.rowcount or 0
            cursor.execute("RELEASE SAVEPOINT sp_glb_reopen_attendance")
            return reopened
        except Exception:
            logging.exception("Отметки: не удалось вернуть дни в пересборку после правки графика")
            if savepoint:
                cursor.execute("ROLLBACK TO SAVEPOINT sp_glb_reopen_attendance")
            return 0

    def glb_cleanup_attendance_cache(self, keep_days=None):
        """Чистка кэша по глубине хранения. Гоняется той же ночной джобой."""
        keep = int(keep_days or self.GLB_ATTENDANCE_RETENTION_DAYS)
        edge = date.today() - timedelta(days=max(1, keep))
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM glb_attendance_rows WHERE day < %s", (edge,))
            removed = cursor.rowcount or 0
            cursor.execute("DELETE FROM glb_attendance_days WHERE day < %s", (edge,))
            cursor.execute("DELETE FROM glb_mark_photos WHERE mark_at < %s", (edge,))
        return removed

    def glb_attendance_directory(self, department=None):
        """Справочники фильтров раздела: подразделения и состав ОБОИХ источников.

        Отдельно от `get_group_late_departments`: тот справочник — про отделы
        Workpace и маршрутизацию чатов, а здесь нужны и подразделения Clockster,
        которых в маршрутизации нет и быть не должно (уведомления по ним не
        уходят — ТЗ #307, п. 3)."""
        dept = self._glb_department(department)
        scope_sql = '' if not dept else ' AND lower(COALESCE(department_name, \'\')) = lower(%s)'
        scope_params = () if not dept else (dept,)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT COALESCE(NULLIF(btrim(department_name), ''), 'Без отдела') AS name,
                       source, COUNT(*)
                FROM glb_employees
                WHERE TRUE{scope_sql}
                GROUP BY 1, 2
                ORDER BY 1
            """, scope_params)
            departments = [{'name': row[0], 'source': row[1], 'employees_count': int(row[2] or 0)}
                           for row in cursor.fetchall()]
            cursor.execute(f"""
                SELECT ext_id, full_name,
                       COALESCE(NULLIF(btrim(department_name), ''), 'Без отдела'),
                       position_name, source
                FROM glb_employees
                WHERE TRUE{scope_sql}
                ORDER BY full_name
            """, scope_params)
            employees = [{'id': row[0], 'name': row[1], 'department': row[2],
                          'position': row[3], 'source': row[4]}
                         for row in cursor.fetchall()]
        return {'departments': departments, 'employees': employees}

    @staticmethod
    def _glb_plan_rule_row(row):
        keys = ('id', 'scope', 'target', 'target_label', 'mode', 'weekdays', 'date_from',
                'date_to', 'time_start', 'time_end', 'break_minutes', 'hours_norm', 'note',
                'enabled', 'created_by', 'created_at', 'author_name')
        item = dict(zip(keys, row))
        item['weekdays'] = [int(day) for day in (item['weekdays'] or [])]
        item['date_from'] = item['date_from'].isoformat() if item['date_from'] else None
        item['date_to'] = item['date_to'].isoformat() if item['date_to'] else None
        item['created_at'] = item['created_at'].isoformat() if item['created_at'] else None
        item['hours_norm'] = float(item['hours_norm']) if item['hours_norm'] is not None else None
        return item

    def glb_plan_rules(self, department=None, enabled_only=False):
        """Правила своего графика. Глава отдела видит только правила своего отдела."""
        dept = self._glb_department(department)
        conditions = []
        params = []
        if enabled_only:
            conditions.append('r.enabled')
        if dept:
            # Адресное правило на человека чужого отдела за границу не пускаем:
            # подпись правила хранит подразделение, по нему и режем.
            conditions.append("""(
                (r.scope = 'department' AND lower(r.target) = lower(%s))
                OR (r.scope = 'employee' AND lower(COALESCE(r.target_label, '')) LIKE %s)
            )""")
            params.extend([dept, f'%{dept.lower()}%'])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ''
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT r.id, r.scope, r.target, r.target_label, r.mode, r.weekdays,
                       r.date_from, r.date_to, r.time_start, r.time_end, r.break_minutes,
                       r.hours_norm, r.note, r.enabled, r.created_by, r.created_at, u.name
                FROM glb_plan_rules r
                LEFT JOIN users u ON u.id = r.created_by
                {where}
                ORDER BY r.scope, lower(COALESCE(r.target_label, r.target)), r.id
            """, params)
            return [self._glb_plan_rule_row(row) for row in cursor.fetchall()]

    def glb_save_plan_rule(self, rule, created_by=None):
        """Создаёт или правит правило и СБРАСЫВАЕТ кэш затронутых дней.

        Сброс обязателен: строки кэша уже посчитаны со старым планом, и без него
        новое правило проявилось бы только на днях, которые ещё не открывали."""
        scope = str((rule or {}).get('scope') or '').strip().lower()
        if scope not in self.GLB_PLAN_RULE_SCOPES:
            raise ValueError('Правило ставится либо на подразделение, либо на сотрудника')
        target = str((rule or {}).get('target') or '').strip()
        if not target:
            raise ValueError('Не указано, на кого ставится правило')
        mode = str(rule.get('mode') or 'schedule').strip().lower()
        if mode not in self.GLB_PLAN_RULE_MODES:
            raise ValueError('Режим бывает «по графику» или «по часам»')

        def _time(value):
            text = str(value or '').strip()
            if not text:
                return None
            try:
                return datetime.strptime(text[:5], '%H:%M').strftime('%H:%M')
            except ValueError:
                raise ValueError('Время указывается как ЧЧ:ММ')

        time_start = _time(rule.get('time_start'))
        time_end = _time(rule.get('time_end'))
        hours_norm = rule.get('hours_norm')
        hours_norm = float(hours_norm) if hours_norm not in (None, '') else None
        if mode == 'schedule' and not (time_start and time_end):
            raise ValueError('У графика должны быть начало и конец смены')
        if mode == 'hours' and not hours_norm:
            raise ValueError('У режима «по часам» должна быть норма часов')
        if hours_norm is not None and not (0 < hours_norm <= 24):
            raise ValueError('Норма часов — от 0 до 24')

        weekdays = sorted({int(day) for day in (rule.get('weekdays') or [])
                           if str(day).strip().isdigit() and 1 <= int(day) <= 7})
        date_from = self._glb_day(rule.get('date_from'))
        date_to = self._glb_day(rule.get('date_to'))
        if date_from and date_to and date_to < date_from:
            date_from, date_to = date_to, date_from
        break_minutes = rule.get('break_minutes')
        break_minutes = int(break_minutes) if str(break_minutes or '').strip() != '' else None
        if break_minutes is not None and not (0 <= break_minutes <= 480):
            raise ValueError('Перерыв — от 0 до 480 минут')

        values = (scope, target[:200], (str(rule.get('target_label') or '').strip()[:300] or None),
                  mode, (weekdays or None), date_from, date_to, time_start, time_end,
                  break_minutes, hours_norm,
                  (str(rule.get('note') or '').strip()[:500] or None),
                  bool(rule.get('enabled', True)))
        rule_id = rule.get('id')
        with self._get_cursor() as cursor:
            if rule_id:
                cursor.execute("""
                    UPDATE glb_plan_rules SET
                        scope = %s, target = %s, target_label = %s, mode = %s, weekdays = %s,
                        date_from = %s, date_to = %s, time_start = %s, time_end = %s,
                        break_minutes = %s, hours_norm = %s, note = %s, enabled = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id
                """, values + (int(rule_id),))
            else:
                cursor.execute("""
                    INSERT INTO glb_plan_rules
                        (scope, target, target_label, mode, weekdays, date_from, date_to,
                         time_start, time_end, break_minutes, hours_norm, note, enabled, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, values + (int(created_by) if created_by else None,))
            saved = cursor.fetchone()
            if not saved:
                raise ValueError('Правило не найдено')
            rule_id = int(saved[0])
        self.glb_forget_attendance_days(day_from=date_from)
        return rule_id

    def glb_roster_synced_at(self, source):
        """Когда состав источника в последний раз обновлялся. None — ни разу."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT MAX(synced_at) FROM glb_employees WHERE source = %s",
                           (str(source or ''),))
            row = cursor.fetchone()
        return row[0] if row else None

    def glb_delete_plan_rule(self, rule_id):
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM glb_plan_rules WHERE id = %s RETURNING date_from",
                           (int(rule_id),))
            row = cursor.fetchone()
        if not row:
            return False
        self.glb_forget_attendance_days(day_from=row[0])
        return True

    def retime_late_wazzup_messages(self, account='op', apply=False):
        """Разовая починка входящих, которые Wazzup доставил с опозданием ДО того,
        как приёмник начал ставить им время доставки (wazzup/delivery.py).

        Момент доставки — created_at: строку аккаунта с вебхуком вставляет только
        приёмник, так что это и есть приход вебхука. У «Потока» created_at — время
        забора окном чатов, поэтому починка — только для аккаунтов с вебхуком.

        В той же транзакции обновляются сводки затронутых чатов и удаляются их
        эпизоды начиная с самого раннего сдвинутого сообщения: состав этих
        эпизодов изменился. Эпизод, на который ссылается оценка, выборка или
        журнал (_wazzup_episode_kept_sql), не удаляется никогда. Удалённое
        пересобирает обычная сборка (build_wazzup_episodes) — её зовёт тот, кто
        запускает починку, после apply=True. Сборка начинает чат с конца его
        последнего эпизода, поэтому сдвинутое раньше конца оставленного
        оценённого эпизода ни в один эпизод уже не попадёт — такие сообщения
        считаются в outside_episodes, решение принимается по холостому прогону.

        apply=False — холостой прогон: всё считается в транзакции и
        откатывается. Повторный запуск ничего не находит (wazzup_dt IS NULL).

        Чаты проходятся так, как их видел приёмник: сообщения по порядку
        прихода (внутри одного вебхука — по времени Wazzup), у каждого
        опоздавшего — delivery_backlog, reply_pending и «не раньше уже
        известного» по тому, что пришло до него."""
        from wazzup import accounts as wz_accounts
        from wazzup.delivery import (LATE_DELIVERY, delivery_backlog, delivery_time,
                                     is_late_inbound, reply_pending)
        if (wz_accounts.ACCOUNTS.get(account) or {}).get('source') != 'webhook':
            raise ValueError(f"починка только для аккаунта с вебхуком, а не {account!r}")
        result = {'account': account, 'applied': bool(apply), 'messages': 0, 'chats': 0,
                  'episodes_deleted': {}, 'episodes_kept': [], 'outside_episodes': 0,
                  'outside_chats': []}
        with self._get_cursor() as cursor:
            # Ночная сборка и кнопка «пересобрать» ждут конца транзакции: иначе
            # они собрали бы эпизоды по старому времени между удалением и
            # пересборкой.
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (self.WAZZUP_EPISODE_LOCK_KEY,))
            cursor.execute("""
                SELECT m.message_id, m.channel_id, m.chat_id, m.is_echo, m.dt,
                       m.created_at, m.wazzup_dt IS NOT NULL
                  FROM wazzup_messages m
                 WHERE m.account = %s
                   AND (m.channel_id, m.chat_id) IN (
                       SELECT channel_id, chat_id FROM wazzup_messages
                        WHERE account = %s AND NOT is_echo AND wazzup_dt IS NULL
                          AND created_at - dt > %s)
                 ORDER BY m.channel_id, m.chat_id, m.created_at, m.dt, m.message_id""",
                (account, account, LATE_DELIVERY))
            chats = {}
            for message_id, channel_id, chat_id, is_echo, dt, created_at, late in cursor.fetchall():
                chats.setdefault((channel_id, chat_id), []).append({
                    'message_id': message_id, 'is_echo': is_echo, 'dt': dt,
                    'created_at': created_at, 'late': late})
            moved, first_moved = {}, {}
            for key, history in chats.items():
                latest = None
                for msg in history:
                    if (not msg['late']
                            and is_late_inbound(msg['dt'], msg['created_at'], msg['is_echo'])
                            and not delivery_backlog(self._wazzup_recent_arrivals_tx(
                                cursor, account, msg['created_at']))):
                        pending = reply_pending(history, msg['created_at'])
                        first_moved[key] = min(first_moved.get(key, msg['dt']), msg['dt'])
                        msg['dt'] = delivery_time(msg['created_at'], pending, after=latest)
                        msg['late'] = True
                        moved.setdefault(key, []).append((msg['message_id'], msg['dt']))
                    latest = msg['dt'] if latest is None else max(latest, msg['dt'])
            moves = [move for chat_moves in moved.values() for move in chat_moves]
            if moves:
                cursor.execute("""
                    UPDATE wazzup_messages m
                       SET wazzup_dt = m.dt, dt = v.new_dt
                      FROM unnest(%s::text[], %s::timestamptz[]) AS v(message_id, new_dt)
                     WHERE m.message_id = v.message_id AND m.wazzup_dt IS NULL""",
                    ([mid for mid, _ in moves], [new_dt for _, new_dt in moves]))
                if cursor.rowcount != len(moves):
                    raise RuntimeError(
                        f"wazzup retime: сдвинуто {cursor.rowcount} строк из {len(moves)}")
            kept_sql = self._wazzup_episode_kept_sql(cursor) if first_moved else None
            for (channel_id, chat_id), since in first_moved.items():
                self._refresh_wazzup_chat_tx(cursor, channel_id, chat_id, account=account)
                cursor.execute(f"""
                    DELETE FROM wazzup_episodes e
                     WHERE e.channel_id = %s AND e.chat_id = %s AND e.ended_at >= %s
                       AND NOT {kept_sql}
                    RETURNING e.kind""", (channel_id, chat_id, since))
                for (kind,) in cursor.fetchall():
                    result['episodes_deleted'][kind] = result['episodes_deleted'].get(kind, 0) + 1
                cursor.execute(f"""
                    SELECT e.id, e.ended_at FROM wazzup_episodes e
                     WHERE e.channel_id = %s AND e.chat_id = %s AND e.ended_at >= %s
                       AND {kept_sql}
                     ORDER BY e.id""", (channel_id, chat_id, since))
                kept = cursor.fetchall()
                result['episodes_kept'].extend(row[0] for row in kept)
                if kept:
                    kept_end = max(row[1] for row in kept)
                    outside = sum(1 for _, new_dt in moved[(channel_id, chat_id)]
                                  if new_dt <= kept_end)
                    if outside:
                        result['outside_episodes'] += outside
                        result['outside_chats'].append(chat_id)
            result['messages'] = len(moves)
            result['chats'] = len(first_moved)
            if not apply:
                cursor.connection.rollback()
        logging.info("wazzup retime (%s, apply=%s): сообщений %s, чатов %s, эпизодов удалено %s, "
                     "оставлено оценённых %s, сообщений вне эпизодов %s", account, apply,
                     result['messages'], result['chats'], result['episodes_deleted'],
                     len(result['episodes_kept']), result['outside_episodes'])
        return result

    # ------------------------------------------------------------------
    # Фото отметок Workpace (13.11.2026): есть ли у отметки снимок.

    def glb_mark_photo_rows(self, mark_ids):
        """{номер отметки: {'file_id', 'department_name'}} по тем, что уже спрашивали."""
        ids = sorted({str(value).strip()[:64] for value in (mark_ids or []) if str(value or '').strip()})
        if not ids:
            return {}
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT mark_id, file_id, department_name
                  FROM glb_mark_photos
                 WHERE mark_id = ANY(%s)
            """, (ids,))
            return {row[0]: {'file_id': row[1], 'department_name': row[2]} for row in cursor.fetchall()}

    def glb_store_mark_photos(self, rows):
        """Запомнить ответ карточки Workpace. Фото у отметки не меняется, поэтому
        повторная запись только освежает отдел (его могли переименовать)."""
        payload = []
        for row in rows or []:
            mark_id = str(row.get('mark_id') or '').strip()[:64]
            if not mark_id:
                continue
            origin = row.get('origin')
            payload.append((
                mark_id,
                (str(row.get('employee_id') or '').strip()[:128] or None),
                (str(row.get('employee_name') or '').strip()[:200] or None),
                (str(row.get('department_name') or '').strip()[:200] or None),
                row.get('mark_at'),
                int(origin) if isinstance(origin, int) else None,
                (str(row.get('file_id') or '').strip()[:64] or None),
            ))
        if not payload:
            return 0
        with self._get_cursor() as cursor:
            execute_values(cursor, """
                INSERT INTO glb_mark_photos
                    (mark_id, employee_id, employee_name, department_name, mark_at, origin, file_id)
                VALUES %s
                ON CONFLICT (mark_id) DO UPDATE SET
                    department_name = EXCLUDED.department_name,
                    file_id = COALESCE(EXCLUDED.file_id, glb_mark_photos.file_id),
                    checked_at = NOW()
            """, payload)
        return len(payload)
