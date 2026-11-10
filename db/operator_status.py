"""База данных: статусы операторов (события и сегменты).

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _minutes_to_time,
    CALCULATION_MODEL_OPERATOR,
    CHAT_MANAGER_ACTION_STATUS_KEYS,
    date,
    datetime,
    dt_time,
    execute_values,
    json,
    LIVE_STATUS_SILENCE_LIMIT_MINUTES,
    logging,
    SCHEDULE_AUTO_NO_PHONE_STATUS_KEY,
    SCHEDULE_STATUS_KEY_LABELS,
    STATUS_EVENTS_RETENTION_DAYS,
    STATUS_IMPORT_INSERT_PAGE_SIZE,
    timedelta,
    uuid,
    ZoneInfo,
)


class OperatorStatusMixin:
    def _time_label_from_seconds(self, seconds_value, round_end=False):
        seconds_int = max(0, int(seconds_value or 0))
        if round_end:
            minutes = int((seconds_int + 59) // 60)
        else:
            minutes = int(seconds_int // 60)
        return _minutes_to_time(minutes)

    def update_wazzup_statuses(self, statuses, account='op'):
        """Обновляет статусы доставки (sent/delivered/read/error) сообщений.
        Ранние статусы сохраняются до прихода сообщения. Повторная доставка
        и нарушение порядка не откатывают delivered/read. Возвращает число обновлений."""
        if not isinstance(statuses, list) or not statuses:
            return 0
        updated = 0
        with self._get_cursor() as cursor:
            for s in sorted((s for s in statuses if isinstance(s, dict)),
                            key=lambda s: str(s.get('messageId', ''))):
                if not isinstance(s, dict):
                    continue
                message_id = s.get('messageId')
                status = s.get('status')
                if not message_id or status not in ('sent', 'delivered', 'read', 'error'):
                    continue
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext('wazzup-status'), hashtext(%s))",
                               (str(message_id),))
                cursor.execute("""
                    INSERT INTO wazzup_status_receipts(account, message_id, status)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(account, message_id) DO UPDATE SET
                        status=wazzup_merge_delivery(wazzup_status_receipts.status, EXCLUDED.status),
                        updated_at=now()
                    RETURNING status
                """, (account, str(message_id), str(status)))
                merged = cursor.fetchone()[0]
                cursor.execute(
                    "UPDATE wazzup_messages SET status = wazzup_merge_delivery(status, %s) "
                    "WHERE message_id = %s AND account = %s "
                    "AND status IS DISTINCT FROM wazzup_merge_delivery(status, %s)",
                    (merged, str(message_id), account, merged))
                updated += cursor.rowcount
        return updated

    def get_c2d_operator_status_events(self, day_from, day_to):
        """События смены статуса чатников за период, по возрастанию времени.

        `online` и почта лежат в теле события, а не колонками: колонки таблицы общие
        для всех событий вендора, и заводить под каждое своё поле значило бы менять
        схему всякий раз, когда он добавит событие."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT c2d_operator_id,
                       event_at,
                       (payload->>'online') AS online,
                       COALESCE(message_type, payload->>'offline_type') AS offline_type,
                       (payload->>'email') AS email
                FROM c2d_webhook_events
                WHERE hook_type IN ('operator_status_changed', 'operator_status_updated')
                  AND c2d_operator_id IS NOT NULL
                  AND day BETWEEN %s AND %s
                ORDER BY event_at, id
            """, (day_from, day_to))
            columns = [description[0] for description in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_c2d_operator_status_before(self, moment):
        """Последний известный статус каждого чатника ДО момента — чем он начал сутки.

        Без этого человек, который заступил вчера вечером и не трогал статус, выглядел
        бы сегодня «не в системе»: событий за сегодня у него нет, а лента статусов
        начинается с первого события суток."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT ON (c2d_operator_id)
                       c2d_operator_id,
                       event_at,
                       (payload->>'online') AS online,
                       COALESCE(message_type, payload->>'offline_type') AS offline_type,
                       (payload->>'email') AS email
                FROM c2d_webhook_events
                WHERE hook_type IN ('operator_status_changed', 'operator_status_updated')
                  AND c2d_operator_id IS NOT NULL
                  AND event_at < %s
                ORDER BY c2d_operator_id, event_at DESC, id DESC
            """, (moment,))
            columns = [description[0] for description in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _split_absolute_minutes_intervals_by_day(self, intervals, anchor_date_obj):
        """
        Splits absolute-minute intervals (relative to anchor_date midnight) into day chunks.
        Returns list of dicts: {'date': date, 'start_min': int, 'end_min': int, 'start_time': 'HH:MM', 'end_time': 'HH:MM'}.
        """
        anchor_date_obj = self._parse_technical_issue_date(anchor_date_obj, field_name='date')
        merged_intervals = self._merge_break_intervals(intervals or [])
        result = []
        for interval in merged_intervals:
            seg_start = int(interval.get('start', 0))
            seg_end = int(interval.get('end', 0))
            if seg_end <= seg_start:
                continue

            cursor_min = seg_start
            while cursor_min < seg_end:
                day_offset = int(cursor_min // 1440)
                day_floor = int(day_offset) * 1440
                day_ceil = day_floor + 1440
                chunk_end = min(seg_end, day_ceil)
                if chunk_end <= cursor_min:
                    break

                local_start = int(cursor_min - day_floor)
                local_end = int(chunk_end - day_floor)
                if local_end > local_start:
                    issue_day = anchor_date_obj + timedelta(days=day_offset)
                    result.append({
                        'date': issue_day,
                        'start_min': local_start,
                        'end_min': local_end,
                        'start_time': _minutes_to_time(local_start),
                        'end_time': _minutes_to_time(local_end)
                    })
                cursor_min = chunk_end
        return result

    def _load_operator_status_groups_tx(self, cursor, operator_ids, as_of):
        """Группа статусов iCORE Phone (Основа / ЯР / Поток) по операторам на дату.

        Лестница та же, что у _load_operator_calculation_models_tx(as_of=...):
        действующее на дату членство решает всегда — даже если это группа не ОП
        (тогда группы статусов нет), а модель направления берётся только без
        членства. Отличия намеренные: равные start_date разводятся по id (иначе
        при пересекающихся членствах группа «прыгала» бы между запросами), и
        `as_of` — дата Алматы из Python, а не CURRENT_DATE: часы базы в UTC, и
        до 05:00 по Алмате она жила бы ещё вчерашним днём.
        """
        from oktell_guard.phone import status_group_for

        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        if not op_ids:
            return {}
        if as_of is None:
            as_of = datetime.now(ZoneInfo('Asia/Almaty')).date()
        elif isinstance(as_of, datetime):
            as_of = as_of.date()
        cursor.execute(
            """
            SELECT u.id,
                   LOWER(COALESCE(u.role, '')) AS role,
                   LOWER(COALESCE(dep.code, '')) AS department_code,
                   g.group_id,
                   COALESCE(g.group_name, '') AS group_name,
                   LOWER(COALESCE(g.calculation_model_code, '')) AS group_model,
                   LOWER(COALESCE(d.calculation_model_code, '')) AS direction_model
            FROM users u
            LEFT JOIN departments dep ON dep.id = u.department_id
            LEFT JOIN directions d ON d.id = u.direction_id
            LEFT JOIN LATERAL (
                SELECT gom.group_id, gr.name AS group_name, gr.calculation_model_code
                FROM group_operator_memberships gom
                JOIN groups gr ON gr.id = gom.group_id
                WHERE gom.operator_id = u.id
                  AND gom.start_date <= %(day)s::date
                  AND (gom.end_date IS NULL OR gom.end_date >= %(day)s::date)
                ORDER BY gom.start_date DESC, gom.id DESC
                LIMIT 1
            ) g ON TRUE
            WHERE u.id = ANY(%(ids)s)
            """,
            {'day': as_of, 'ids': op_ids}
        )
        result = {}
        for (op_id, role, department_code, group_id, group_name,
             group_model, direction_model) in cursor.fetchall() or []:
            result[int(op_id)] = {
                'role': role or '',
                'department_code': department_code or '',
                'group_id': int(group_id) if group_id is not None else None,
                'group_name': group_name or '',
                'group_model': group_model or '',
                'direction_model': direction_model or '',
                'status_group': status_group_for(role, group_model, direction_model),
            }
        return result

    def get_operator_status_groups(self, user_ids, as_of):
        """{user_id: {'role', 'department_code', 'group_id', 'group_name', 'group_model',
        'direction_model', 'status_group'}} — группа статусов iCORE Phone на дату.

        status_group: 'osnova' | 'yar' | 'potok' или None (прежний набор статусов).
        Сотрудника, которого нет в базе, в ответе нет."""
        op_ids = [int(v) for v in (user_ids or []) if v is not None]
        if not op_ids:
            return {}
        with self._get_cursor() as cursor:
            return self._load_operator_status_groups_tx(cursor, op_ids, as_of)

    def _status_transition_signature(self, status_key_value, state_note_value=None):
        """Canonical state identity used for semantic no-op detection."""
        status_key = self._normalize_import_status_key(status_key_value)
        state_note = str(state_note_value or '').strip() or None
        return status_key, state_note

    def _canonicalize_status_transition_events(self, events):
        """Drop zero-duration/no-op transitions without losing real A -> B -> A.

        iCORE Phone historically emitted timestamps with one-second precision.
        A short CONFIRMED -> DISCONNECTED pair could therefore have the same
        event_at. Only the last event at an instant can own positive time; earlier
        states at that exact timestamp are zero-duration. After resolving those
        ties, repeated identical states are semantic no-ops and are collapsed.
        """
        last_by_timestamp = []
        for raw_event in (events or []):
            event = dict(raw_event or {})
            event_at = event.get('event_at')
            if last_by_timestamp and last_by_timestamp[-1].get('event_at') == event_at:
                last_by_timestamp[-1] = event
            else:
                last_by_timestamp.append(event)

        canonical = []
        for event in last_by_timestamp:
            signature = self._status_transition_signature(
                event.get('status_key'),
                event.get('state_note')
            )
            if canonical and self._status_transition_signature(
                canonical[-1].get('status_key'),
                canonical[-1].get('state_note')
            ) == signature:
                continue
            canonical.append(event)
        return canonical

    # ── Два источника статусов у одного человека ────────────────────────────
    # Оператор СЗоВ работает в Oktell (статусы приходят выгрузкой несколько раз в день),
    # а на обзвон удалённого КЦ садится в iCORE Phone (телефон шлёт события сам, у них
    # есть client_event_id). Решение владельца 10.11.2026: пока человек подключён к
    # обзвону через телефон, время считается по телефону, а не по Oktell.
    LIVE_STATUS_OFF_KEYS = frozenset({'выключен'})
    LIVE_STATUS_SILENCE_LIMIT_SECONDS = int(LIVE_STATUS_SILENCE_LIMIT_MINUTES) * 60
    # Состояния выгрузки «не на линии» (Oktell: 0 и 7). Работой в Oktell они не являются
    # и оборванную сессию телефона не доказывают: окно Oktell, оставленное на экране
    # входа, пишет «Без телефона» → «Выключен» каждую минуту (прод, 10.11.2026).
    LIVE_STATUS_IDLE_IMPORT_KEYS = frozenset({'выключен', 'без телефона'})
    # Состояние выгрузки возвращается после сессии телефона, только пока оно свежее. В базе
    # лежат и старые выгрузки отделов, давно работающих только в телефоне (ОП — до 01.08.2026,
    # Тез — до 27.07.2026): без этой границы после каждого «выключен» телефона у них
    # всплывал бы статус трёхмесячной давности. 16 часов — длиннее любой смены.
    LIVE_STATUS_RESUME_MAX_AGE_SECONDS = 16 * 3600

    def _overlay_live_status_sessions(self, events):
        """Свести события двух источников в один ряд: живой источник поверх выгрузки.

        events — события одного оператора по порядку времени; у каждого 'live':
        True — пришло само (iCORE Phone, рабочее место «Чатов ОП»), False — выгрузка
        (Oktell, Chat2Desk, файл).

        У кого источник один, ряд возвращается как есть: так считались и считаются
        отделы, работающие только в телефоне, и все, кто работает только в Oktell.

        Когда источников два, действует сессия живого источника:
        - она открыта от его статуса до его же «выключен»; события выгрузки за это
          время в ряд не идут, но запоминаются;
        - «выключен» закрывает сессию, и с этой секунды действует состояние выгрузки
          на этот момент: человек вышел из телефона, а в Oktell он, возможно, «Готов» —
          или «Выключен», если сессию Oktell закрыли при входе в телефон. Состояние
          старше LIVE_STATUS_RESUME_MAX_AGE_SECONDS не возвращается — это не «сейчас»;
        - телефон может пропасть без «выключен» (сбой, пропал свет). Тогда рабочее
          событие выгрузки (не «Выключен» и не «Без телефона»), пришедшее позже порога
          молчания, означает, что человек работает в Oktell: с момента «последнее
          событие телефона + порог» ряд снова идёт по выгрузке. Пока таких событий нет,
          статус телефона стоит — долгий «Тренинг» в телефоне сам сессию не рвёт.
        """
        events = list(events or [])
        if not (any(event.get('live') for event in events)
                and any(not event.get('live') for event in events)):
            return events

        silence_limit = timedelta(seconds=int(self.LIVE_STATUS_SILENCE_LIMIT_SECONDS))
        resume_max_age = timedelta(seconds=int(self.LIVE_STATUS_RESUME_MAX_AGE_SECONDS))
        merged = []
        session_open = False
        last_live_at = None
        last_imported = None

        def resume_imported(moment):
            """Состояние выгрузки на этот момент — событием в ряд (если оно ещё свежее)."""
            if last_imported is not None and moment - last_imported.get('event_at') <= resume_max_age:
                merged.append(dict(last_imported, event_at=moment))

        for event in events:
            event_at = event.get('event_at')
            if event.get('live'):
                merged.append(event)
                if event.get('status_key') in self.LIVE_STATUS_OFF_KEYS:
                    # Тем же моментом возвращаем состояние выгрузки: из двух событий с
                    # одним временем ряд оставляет последнее (см. канонизацию выше).
                    session_open = False
                    resume_imported(event_at)
                else:
                    session_open = True
                    last_live_at = event_at
                continue
            if session_open:
                if (event_at - last_live_at <= silence_limit
                        or event.get('status_key') in self.LIVE_STATUS_IDLE_IMPORT_KEYS):
                    last_imported = event
                    continue
                session_open = False
                resume_imported(last_live_at + silence_limit)
            last_imported = event
            merged.append(event)
        return merged

    def _merge_adjacent_status_timeline_segments(self, segments):
        """Merge presentation rows that represent one uninterrupted state."""
        merged = []
        for raw_segment in (segments or []):
            segment = dict(raw_segment or {})
            can_merge = (
                merged
                and merged[-1].get('end') == segment.get('start')
                and self._status_transition_signature(
                    merged[-1].get('stateKey'),
                    merged[-1].get('stateNote')
                ) == self._status_transition_signature(
                    segment.get('stateKey'),
                    segment.get('stateNote')
                )
            )
            if not can_merge:
                merged.append(segment)
                continue
            merged[-1]['end'] = segment.get('end')
            merged[-1]['durationSec'] = (
                int(merged[-1].get('durationSec') or 0)
                + int(segment.get('durationSec') or 0)
            )
        return merged

    def _status_import_event_kind_from_key(self, status_key_value, event_kind_value=None):
        kind = str(event_kind_value or '').strip().lower()
        if kind in ('status', 'action'):
            return kind
        status_key = self._normalize_import_status_key(status_key_value)
        if status_key in CHAT_MANAGER_ACTION_STATUS_KEYS:
            return 'action'
        return 'status'

    def _split_status_segment_datetimes_by_day(self, start_at_value, end_at_value):
        if not isinstance(start_at_value, datetime) or not isinstance(end_at_value, datetime):
            return []
        if end_at_value <= start_at_value:
            return []

        parts = []
        cursor = start_at_value
        while cursor < end_at_value:
            day_start = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            seg_end = end_at_value if end_at_value <= day_end else day_end
            if seg_end <= cursor:
                break
            duration_sec = int(round((seg_end - cursor).total_seconds()))
            if duration_sec <= 0:
                duration_sec = 1
            parts.append({
                'status_date': cursor.date(),
                'start_at': cursor,
                'end_at': seg_end,
                'duration_sec': duration_sec
            })
            cursor = seg_end
        return parts

    def _lock_operator_status_segments_tx(self, cursor, operator_ids):
        """Сериализует замену/rebuild сегментов по оператору в рамках транзакции."""
        for operator_id in sorted({int(v) for v in (operator_ids or []) if v is not None}):
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                (self.STATUS_SEGMENT_OPERATOR_LOCK_NAMESPACE, operator_id),
            )

    def _rebuild_operator_status_segments_tx(self, cursor, operator_ids, start_date, end_date, imported_by=None):
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        if not op_ids:
            return {'segments_saved': 0, 'deleted_segments': 0}

        self._lock_operator_status_segments_tx(cursor, op_ids)

        start_date_obj = self._normalize_schedule_date(start_date)
        end_date_obj = self._normalize_schedule_date(end_date)
        if end_date_obj < start_date_obj:
            start_date_obj, end_date_obj = end_date_obj, start_date_obj

        # Retention barrier: raw events older than STATUS_EVENTS_RETENTION_DAYS are removed by the
        # daily purge, so never try to rebuild from a window whose source events may already be gone.
        # Segments below the floor stay as-is — they are the durable report source, not re-derived.
        retention_floor = datetime.now().date() - timedelta(days=STATUS_EVENTS_RETENTION_DAYS)
        if end_date_obj < retention_floor:
            return {
                'segments_saved': 0,
                'deleted_segments': 0,
                'range_start': start_date_obj.strftime('%Y-%m-%d'),
                'range_end': end_date_obj.strftime('%Y-%m-%d'),
                'skipped_below_retention': True
            }
        if start_date_obj < retention_floor:
            start_date_obj = retention_floor

        window_start = datetime.combine(start_date_obj, dt_time.min)
        window_end = datetime.combine(end_date_obj + timedelta(days=1), dt_time.min)
        imported_by_id = int(imported_by) if imported_by is not None else None
        action_status_keys = sorted({
            self._normalize_import_status_key(item)
            for item in CHAT_MANAGER_ACTION_STATUS_KEYS
            if self._normalize_import_status_key(item)
        })

        # is_live — событие пришло само (iCORE Phone, рабочее место «Чатов ОП»), а не
        # выгрузкой. Опорные события до и после окна берём ПО ИСТОЧНИКУ: человеку с двумя
        # источниками (_overlay_live_status_sessions) на границе окна нужно знать и
        # открыта ли сессия телефона, и какое состояние у выгрузки. У кого источник
        # один, опорных по-прежнему по одному с каждой стороны.
        cursor.execute(
            """
            WITH candidates AS (
                SELECT
                    id,
                    operator_id,
                    event_at,
                    status_key,
                    state_note,
                    COALESCE(event_kind, 'status') AS event_kind,
                    (client_event_id IS NOT NULL) AS is_live
                FROM operator_status_events
                WHERE operator_id = ANY(%s)
                  AND COALESCE(event_kind, 'status') <> 'action'
                  AND COALESCE(is_authoritative, FALSE) = FALSE
                  AND NOT (LOWER(TRIM(status_key)) = ANY(%s))
            ),
            previous_events AS (
                SELECT DISTINCT ON (operator_id, is_live)
                    id, operator_id, event_at, status_key, state_note, event_kind, is_live
                FROM candidates
                WHERE event_at < %s
                ORDER BY operator_id, is_live, event_at DESC, id DESC
            ),
            main_events AS (
                SELECT id, operator_id, event_at, status_key, state_note, event_kind, is_live
                FROM candidates
                WHERE event_at >= %s
                  AND event_at < %s
            ),
            next_events AS (
                SELECT DISTINCT ON (operator_id, is_live)
                    id, operator_id, event_at, status_key, state_note, event_kind, is_live
                FROM candidates
                WHERE event_at >= %s
                ORDER BY operator_id, is_live, event_at ASC, id DESC
            )
            SELECT id, operator_id, event_at, status_key, state_note, event_kind, is_live
            FROM (
                SELECT * FROM previous_events
                UNION ALL
                SELECT * FROM main_events
                UNION ALL
                SELECT * FROM next_events
            ) e
            ORDER BY operator_id, event_at, id
            """,
            (op_ids, action_status_keys, window_start, window_start, window_end, window_end)
        )
        rows = cursor.fetchall() or []

        # Готовые start/stop-интервалы некоторых источников являются первичным
        # сырьём. Обычная пересборка из точек переключения не должна ни удалять
        # их, ни создавать поверх них дубли. Защита хранится в самой строке,
        # поэтому переживает отдельные HTTP-запросы и рестарты приложения.
        cursor.execute(
            """
            SELECT operator_id, status_date, MAX(end_at) AS authoritative_end
            FROM operator_status_segments
            WHERE operator_id = ANY(%s)
              AND status_date >= %s
              AND status_date <= %s
              AND COALESCE(is_authoritative, FALSE) = TRUE
            GROUP BY operator_id, status_date
            """,
            (op_ids, start_date_obj, end_date_obj),
        )
        # Защита действует ПО ВРЕМЕНИ, а не на весь день. В сутки перехода отдела
        # на другой источник (ТЭЗ: ночная выгрузка Binotel → события телефона
        # iCORE Phone) день делится между двумя источниками: выгрузка закрывает
        # ночь, дальше день закрывают события телефона. Блокировка целого дня
        # оставляла такой день почти пустым — 453 события телефона не давали ни
        # одного сегмента из-за 20 ночных строк выгрузки.
        # Отсутствие ключа = авторитетных интервалов за день нет, пересборка
        # свободна. Значение None = границу определить не удалось, тогда день
        # защищаем целиком, как раньше.
        authoritative_until = {}
        for operator_id, status_date, authoritative_end in (cursor.fetchall() or []):
            if status_date is None:
                continue
            authoritative_until[(int(operator_id), status_date)] = authoritative_end

        events_by_operator = {}
        for event_id, operator_id, event_at_value, status_key, state_note, event_kind, is_live in rows:
            if not isinstance(event_at_value, datetime):
                continue
            status_key_norm = self._normalize_import_status_key(status_key)
            if not status_key_norm:
                continue
            if self._status_import_event_kind_from_key(status_key_norm, event_kind) == 'action':
                continue
            events_by_operator.setdefault(int(operator_id), []).append({
                'id': int(event_id),
                'event_at': event_at_value,
                'status_key': status_key_norm,
                'state_note': str(state_note or '').strip() or None,
                'live': bool(is_live)
            })

        segment_values = []
        now_dt = datetime.now()
        for op_id in op_ids:
            events_list = self._canonicalize_status_transition_events(
                self._overlay_live_status_sessions(events_by_operator.get(int(op_id)) or [])
            )
            if not events_list:
                continue

            for idx in range(len(events_list) - 1):
                cur = events_list[idx]
                nxt = events_list[idx + 1]
                cur_at = cur.get('event_at')
                next_at = nxt.get('event_at')
                if not isinstance(cur_at, datetime) or not isinstance(next_at, datetime):
                    continue
                if next_at <= cur_at:
                    continue

                for part in self._split_status_segment_datetimes_by_day(cur_at, next_at):
                    status_date_obj = part.get('status_date')
                    if status_date_obj < start_date_obj or status_date_obj > end_date_obj:
                        continue
                    authoritative_key = (int(op_id), status_date_obj)
                    if authoritative_key in authoritative_until:
                        part = self._clip_part_after_authoritative(
                            part, authoritative_until[authoritative_key]
                        )
                        if part is None:
                            continue
                    segment_values.append((
                        int(op_id),
                        status_date_obj,
                        part.get('start_at'),
                        part.get('end_at'),
                        int(part.get('duration_sec') or 0),
                        cur.get('status_key'),
                        cur.get('state_note'),
                        imported_by_id
                    ))

            last_event = events_list[-1]
            last_event_at = last_event.get('event_at')
            if (
                isinstance(last_event_at, datetime)
                and now_dt >= last_event_at
                and (now_dt - last_event_at) <= timedelta(hours=48)
            ):
                for part in self._split_status_segment_datetimes_by_day(last_event_at, now_dt):
                    status_date_obj = part.get('status_date')
                    if status_date_obj < start_date_obj or status_date_obj > end_date_obj:
                        continue
                    authoritative_key = (int(op_id), status_date_obj)
                    if authoritative_key in authoritative_until:
                        part = self._clip_part_after_authoritative(
                            part, authoritative_until[authoritative_key]
                        )
                        if part is None:
                            continue
                    segment_values.append((
                        int(op_id),
                        status_date_obj,
                        part.get('start_at'),
                        part.get('end_at'),
                        int(part.get('duration_sec') or 0),
                        last_event.get('status_key'),
                        last_event.get('state_note'),
                        imported_by_id
                    ))

        # Пишем только разницу. Раньше окно пересборки удалялось и вставлялось заново
        # целиком, а событие телефона (append_operator_status_event) пересобирает три дня
        # оператора — ~300 сегментов на КАЖДОЕ событие, хотя оно меняет 1–3 (закрывает
        # открытый хвост и открывает новый). Замер 15–19.10.2026: ~1,5 млн удалённых и
        # столько же вставленных строк в сутки при ~14 тыс. новых сегментов, 15% WAL базы
        # и автовакуум таблицы каждые полчаса. Теперь сравниваем посчитанное с тем, что
        # лежит, и трогаем только несовпавшее: после пересборки в таблице ровно те же
        # строки, что дала бы полная замена (у нетронутых только прежние id).
        # Авторитетные строки в сравнение не попадают и не удаляются: id для удаления
        # берутся только из этой выборки.
        from collections import Counter
        cursor.execute(
            """
            SELECT id, operator_id, status_date, start_at, end_at, duration_sec,
                   status_key, state_note, imported_by
            FROM operator_status_segments
            WHERE operator_id = ANY(%s)
              AND status_date >= %s
              AND status_date <= %s
              AND COALESCE(is_authoritative, FALSE) = FALSE
            """,
            (op_ids, start_date_obj, end_date_obj)
        )
        existing_rows = cursor.fetchall() or []
        wanted = Counter(segment_values)
        stale_ids = []
        for row in existing_rows:
            key = (int(row[1]), row[2], row[3], row[4], int(row[5] or 0),
                   row[6], row[7], row[8])
            if wanted[key] > 0:
                wanted[key] -= 1
            else:
                stale_ids.append(int(row[0]))
        fresh_values = list(wanted.elements())

        if stale_ids:
            cursor.execute(
                "DELETE FROM operator_status_segments WHERE id = ANY(%s)",
                (stale_ids,)
            )

        if fresh_values:
            execute_values(
                cursor,
                """
                INSERT INTO operator_status_segments (
                    operator_id,
                    status_date,
                    start_at,
                    end_at,
                    duration_sec,
                    status_key,
                    state_note,
                    imported_by
                )
                VALUES %s
                """,
                fresh_values,
                page_size=STATUS_IMPORT_INSERT_PAGE_SIZE
            )

        return {
            'segments_saved': len(segment_values),
            # Сколько прежних сегментов окна заменено пересборкой — тот же смысл, что
            # у числа строк полной замены (его пишут сводки импорта).
            'deleted_segments': len(existing_rows),
            # Сколько строк реально записано и удалено — для наблюдения за разницей.
            'segments_inserted': len(fresh_values),
            'segments_removed': len(stale_ids),
            'range_start': start_date_obj.strftime('%Y-%m-%d'),
            'range_end': end_date_obj.strftime('%Y-%m-%d')
        }

    def purge_old_operator_status_events(self, retention_days=None, batch_size=20000, max_batches=2000):
        """
        Delete raw operator_status_events older than the retention horizon so the table does not
        grow without bound. Raw events are only a rebuild source for operator_status_segments (the
        durable report data, which is NOT purged here).

        Safety design:
        - Per operator we KEEP the single most recent pre-floor event that the rebuild could
          actually carry into a window. The anchor subquery therefore uses the SAME candidate
          filter as _rebuild_operator_status_segments_tx (exclude 'action' events and the
          CHAT_MANAGER_ACTION_STATUS_KEYS), so we never delete the status event that
          `previous_events` needs while keeping a useless 'action' row instead.
        - The purge floor is never more aggressive than the rebuild barrier
          (days >= STATUS_EVENTS_RETENTION_DAYS), so we cannot delete an event a clamped rebuild
          at the barrier floor still requires.
        - Deletes are batched and bounded by lock_timeout so the job never blocks the INSERT-heavy
          import path indefinitely and never holds ~hundreds-of-thousands of row locks at once.
        """
        try:
            days = int(retention_days) if retention_days is not None else STATUS_EVENTS_RETENTION_DAYS
        except Exception:
            days = STATUS_EVENTS_RETENTION_DAYS
        # Never purge more aggressively than the rebuild barrier clamps to (and never below 30d).
        days = max(int(days), int(STATUS_EVENTS_RETENTION_DAYS), 30)
        floor_date = datetime.now().date() - timedelta(days=days)

        action_status_keys = sorted({
            self._normalize_import_status_key(item)
            for item in CHAT_MANAGER_ACTION_STATUS_KEYS
            if self._normalize_import_status_key(item)
        })

        total_deleted = 0
        stopped_early = False
        for _ in range(int(max_batches)):
            try:
                with self._get_cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '5s'")
                    cursor.execute("SET LOCAL statement_timeout = '120s'")
                    cursor.execute(
                        """
                        WITH keep AS (
                            SELECT operator_id, max(event_at) AS anchor_at
                            FROM operator_status_events
                            WHERE event_date < %(floor)s
                              AND COALESCE(event_kind, 'status') <> 'action'
                              AND COALESCE(is_authoritative, FALSE) = FALSE
                              AND NOT (LOWER(TRIM(status_key)) = ANY(%(keys)s))
                            GROUP BY operator_id
                        ),
                        victims AS (
                            SELECT e.ctid
                            FROM operator_status_events e
                            JOIN keep ON keep.operator_id = e.operator_id
                            WHERE e.event_date < %(floor)s
                              AND e.event_at < keep.anchor_at
                            LIMIT %(batch)s
                        )
                        DELETE FROM operator_status_events e
                        USING victims v
                        WHERE e.ctid = v.ctid
                        """,
                        {'floor': floor_date, 'keys': action_status_keys, 'batch': int(batch_size)}
                    )
                    n = max(0, int(cursor.rowcount or 0))
            except Exception as exc:
                # lock_timeout / deadlock / statement_timeout: commit progress so far, retry tonight.
                logging.warning("purge_old_operator_status_events: batch aborted (%s); stopping early", exc)
                stopped_early = True
                break
            total_deleted += n
            if n < int(batch_size):
                break

        logging.info(
            "purge_old_operator_status_events: deleted %s raw events older than %s (retention=%sd)%s",
            total_deleted, floor_date.strftime('%Y-%m-%d'), days,
            " [stopped early]" if stopped_early else ""
        )
        return {
            'deleted_events': total_deleted,
            'floor_date': floor_date.strftime('%Y-%m-%d'),
            'retention_days': days,
            'stopped_early': stopped_early
        }

    def _status_label_from_key(self, status_key_value, fallback_name=None):
        key = self._normalize_import_status_key(status_key_value)
        if not key:
            fallback_text = str(fallback_name or '').strip()
            return fallback_text or 'Статус'
        if self._schedule_auto_is_tech_reason_status_key(key):
            return 'Тех причина'
        direct = SCHEDULE_STATUS_KEY_LABELS.get(key)
        if direct:
            return direct
        return ' '.join(part[:1].upper() + part[1:] for part in key.split(' ') if part)

    def _imported_status_segment_payload(self, calculation_model, status_date_value, start_at_value,
                                         end_at_value, duration_sec, status_key, state_note):
        """Один отрезок статуса в том виде, в каком его ждёт фронт.

        Вынесено из _load_imported_status_segments_for_operators, потому что тем
        же набором признаков описывается и ТЕКУЩИЙ, ещё не закрытый статус
        (см. _load_live_status_tail_for_operators). Собери его отдельно — и
        признаки разъедутся: «Готов» в сохранённом отрезке считался бы работой,
        а в живом хвосте нет, и проценты у оператора пошли бы своей дорогой.
        """
        day_key = status_date_value.strftime('%Y-%m-%d') if hasattr(status_date_value, 'strftime') else str(status_date_value or '')
        if not day_key:
            return None

        status_key_norm = self._normalize_import_status_key(status_key)
        if not status_key_norm:
            return None

        status_profile = self._status_profile_for_calculation_model(
            calculation_model or CALCULATION_MODEL_OPERATOR
        )
        return {
            'statusDate': day_key,
            'start': start_at_value.isoformat() if hasattr(start_at_value, 'isoformat') else str(start_at_value or ''),
            'end': end_at_value.isoformat() if hasattr(end_at_value, 'isoformat') else str(end_at_value or ''),
            'durationSec': int(duration_sec or 0),
            'stateName': str(self._status_label_from_key(status_key_norm) or ''),
            'stateKey': str(status_key_norm or ''),
            'stateNote': str(state_note or ''),
            'isWork': status_key_norm in set(status_profile.get('work') or set()),
            'isBreak': status_key_norm in set(status_profile.get('break') or set()),
            'isTraining': status_key_norm in set(status_profile.get('training') or set()),
            'isLateStart': status_key_norm in set(status_profile.get('late_start') or set()),
            'isTechnicalReason': bool(self._schedule_auto_is_tech_reason_status_key(status_key_norm)),
            'isLateExcused': bool(self._schedule_auto_is_late_excused_status_key(status_key_norm)),
            'isNoPhone': status_key_norm == SCHEDULE_AUTO_NO_PHONE_STATUS_KEY
        }

    def _load_imported_status_segments_for_operators(self, cursor, operator_ids, start_date_obj=None, end_date_obj=None):
        operator_ids = [int(v) for v in (operator_ids or []) if v is not None]
        result = {op_id: {} for op_id in operator_ids}
        if not operator_ids:
            return result

        calculation_model_by_operator = self._load_operator_calculation_models_tx(cursor, operator_ids)

        min_day = start_date_obj - timedelta(days=1) if start_date_obj else None
        max_day = end_date_obj + timedelta(days=1) if end_date_obj else None

        def _append_segment(op_id, status_date_value, start_at_value, end_at_value, duration_sec, status_key, state_note):
            target = result.get(int(op_id))
            if target is None:
                return

            payload = self._imported_status_segment_payload(
                calculation_model=calculation_model_by_operator.get(int(op_id), CALCULATION_MODEL_OPERATOR),
                status_date_value=status_date_value,
                start_at_value=start_at_value,
                end_at_value=end_at_value,
                duration_sec=duration_sec,
                status_key=status_key,
                state_note=state_note
            )
            if payload is None:
                return
            target.setdefault(payload['statusDate'], []).append(payload)

        query = """
            SELECT
                operator_id,
                status_date,
                start_at,
                end_at,
                duration_sec,
                status_key,
                state_note
            FROM operator_status_segments
            WHERE operator_id = ANY(%s)
        """
        params = [operator_ids]

        if min_day and max_day:
            query += " AND status_date >= %s AND status_date <= %s"
            params.extend([min_day, max_day])
        elif min_day:
            query += " AND status_date >= %s"
            params.append(min_day)
        elif max_day:
            query += " AND status_date <= %s"
            params.append(max_day)

        query += " ORDER BY operator_id, status_date, start_at, end_at, id"
        cursor.execute(query, params)
        rows = cursor.fetchall() or []

        for row in rows:
            (
                operator_id,
                status_date_value,
                start_at_value,
                end_at_value,
                duration_sec,
                status_key,
                state_note
            ) = row
            op_id = int(operator_id)
            _append_segment(
                op_id=op_id,
                status_date_value=status_date_value,
                start_at_value=start_at_value,
                end_at_value=end_at_value,
                duration_sec=duration_sec,
                status_key=status_key,
                state_note=state_note
            )

        for op_days in result.values():
            for segments in op_days.values():
                segments.sort(key=lambda item: (
                    str(item.get('start') or ''),
                    str(item.get('end') or ''),
                    str(item.get('stateKey') or '')
                ))
                segments[:] = self._merge_adjacent_status_timeline_segments(segments)

        return result

    def _attach_imported_status_segments_to_operators(self, cursor, operators_map, operator_ids, start_date_obj=None, end_date_obj=None):
        timeline_map = self._load_imported_status_segments_for_operators(
            cursor=cursor,
            operator_ids=operator_ids,
            start_date_obj=start_date_obj,
            end_date_obj=end_date_obj
        )
        for op_id in (operator_ids or []):
            target = operators_map.get(op_id)
            if target is None:
                continue
            target['importedStatusTimelineDays'] = timeline_map.get(int(op_id), {})

    @staticmethod
    def _timeline_days_last_end(timeline_days):
        """Докуда уже доведены сохранённые отрезки дня (или None).

        Пересборка после каждого события дописывает отрезок до момента самой
        пересборки, поэтому сохранённый кусок и живой хвост идут от одного и
        того же события. Если не сдвинуть начало хвоста, в подписях под полосой
        «Готов» посчитается дважды: у сетки и у оператора разъедутся минуты.
        """
        last_end = None
        for segments in (timeline_days or {}).values():
            for segment in (segments or []):
                raw = segment.get('end') if isinstance(segment, dict) else None
                if not raw:
                    continue
                try:
                    value = datetime.fromisoformat(str(raw))
                except ValueError:
                    continue
                if last_end is None or value > last_end:
                    last_end = value
        return last_end

    def _live_status_tail_window_tx(self, cursor, operator_ids, as_of_dt):
        """{operator_id: (не раньше, не позже)} — рамки текущего статуса.

        **Потолок** — конец смены: дальше него «Готов» рос бы у человека, чей
        телефон не прислал «выключен» при уходе домой (у Тез КЦ такие сутки не
        редкость — см. сегменты по 24 часа). Внутри смены потолок не мешает:
        там хвост упирается в «сейчас».

        **Пол** — начало той же смены с часом запаса на ранний вход. Без него
        забытый со вчерашнего дня «Готов» на следующее утро расцветал бы во всю
        смену: у человека со сменой 12:00–16:00 и последним событием вчера в
        08:23 полоса в 12:01 показала бы «работает с полуночи» и 100 %
        соответствия. Статус, поставленный не в эту смену, текущим не считаем.

        Смены берём за сегодня и за вчера: ночная 21:00–09:00 заходит в
        сегодняшний день своим концом, и без вчерашней строки утро такой смены
        осталось бы без рамок. Дня без смены здесь нет намеренно: сравнивать не
        с чем, а растущая полоса в выходной — тот самый лишний шум.
        """
        op_ids = [int(v) for v in (operator_ids or []) if v is not None]
        if not op_ids:
            return {}

        day = as_of_dt.date()
        day_start = datetime.combine(day, dt_time.min)
        prev_day_start = day_start - timedelta(days=1)
        now_min = (as_of_dt - day_start).total_seconds() / 60.0

        cursor.execute(
            """
            SELECT operator_id, shift_date, start_time, end_time
            FROM work_shifts
            WHERE operator_id = ANY(%s)
              AND shift_date >= %s
              AND shift_date <= %s
            """,
            (op_ids, day - timedelta(days=1), day)
        )

        parts_by_operator = {}
        for operator_id, shift_date, start_time_value, end_time_value in (cursor.fetchall() or []):
            if shift_date is None or start_time_value is None or end_time_value is None:
                continue
            start_min = start_time_value.hour * 60 + start_time_value.minute
            end_min = end_time_value.hour * 60 + end_time_value.minute
            if end_min <= start_min:
                end_min += 1440
            if shift_date == day:
                part_start, part_end = max(0, start_min), min(1440, end_min)
                shift_start_dt = day_start + timedelta(minutes=start_min)
            elif end_min > 1440:
                part_start, part_end = 0, min(1440, end_min - 1440)
                shift_start_dt = prev_day_start + timedelta(minutes=start_min)
            else:
                continue
            if part_end <= part_start:
                continue
            parts_by_operator.setdefault(int(operator_id), []).append(
                (part_start, part_end, shift_start_dt)
            )

        result = {}
        for op_id in op_ids:
            parts = parts_by_operator.get(op_id) or []
            if not parts:
                continue
            covering = [p for p in parts if p[0] <= now_min <= p[1]]
            chosen = max(covering, key=lambda p: p[1]) if covering else None
            if chosen is None:
                finished = [p for p in parts if p[1] <= now_min]
                chosen = max(finished, key=lambda p: p[1]) if finished else None
            if chosen is None:
                continue
            cap_dt = min(as_of_dt, day_start + timedelta(minutes=float(chosen[1])))
            floor_dt = chosen[2] - self.LIVE_STATUS_TAIL_EARLY_LOGIN_MARGIN
            result[op_id] = (floor_dt, cap_dt)
        return result

    def _status_range_covers_today(self, start_date_obj=None, end_date_obj=None):
        """Живой хвост имеет смысл только у сегодняшнего дня. Открытый период
        (обе границы пусты) — это «всё, что есть», сегодня туда входит."""
        today = datetime.now().date()
        if start_date_obj and start_date_obj > today:
            return False
        if end_date_obj and end_date_obj < today:
            return False
        return True

    def get_live_status_track(self, date_value, operator_ids=None):
        """Узкий срез для автообновления: статусы одного дня и текущий статус.

        Отдельно от get_operators_with_shifts намеренно: тот тянет смены,
        перерывы, выходные, периоды статусов и признаки брака за весь видимый
        период — раз в полминуты такое дёргать нельзя. Здесь только то, что
        меняется от минуты к минуте.

        Соседние сутки приезжают вместе с запрошенными: ночная смена 21:00–09:00
        забирает статусы из двух дней, и без них у неё пустел бы один конец.
        """
        date_obj = self._normalize_schedule_date(date_value)
        as_of_dt = datetime.now()
        result = {
            'date': date_obj.strftime('%Y-%m-%d'),
            'asOf': as_of_dt.isoformat(timespec='seconds'),
            'operators': []
        }

        with self._get_cursor() as cursor:
            live_ids = self._live_status_track_operator_ids_tx(cursor, operator_ids=operator_ids)
            if not live_ids:
                return result

            timeline_map = self._load_imported_status_segments_for_operators(
                cursor=cursor,
                operator_ids=live_ids,
                start_date_obj=date_obj,
                end_date_obj=date_obj
            )
            known_until = {}
            for op_id in live_ids:
                last_end = self._timeline_days_last_end(timeline_map.get(int(op_id)))
                if last_end is not None:
                    known_until[int(op_id)] = last_end
            tails = (
                self._load_live_status_tail_for_operators(
                    cursor, live_ids, as_of=as_of_dt, known_until=known_until
                )
                if date_obj == as_of_dt.date()
                else {}
            )

            for op_id in live_ids:
                result['operators'].append({
                    'id': int(op_id),
                    'importedStatusTimelineDays': timeline_map.get(int(op_id), {}),
                    'liveStatusTail': tails.get(int(op_id))
                })
        return result

    def save_operator_status_import(self, events, segments, imported_by=None, summary=None,
                                    keep_live_events=False):
        """
        Сохранить импорт статусов операторов:
        - сырые события переключений (operator_status_events)
        - рассчитанные интервалы/сегменты для аналитики (operator_status_segments)

        Перед вставкой удаляет существующие данные по затронутым операторам и датам,
        чтобы импорт работал как "replace" в рамках импортированного диапазона.

        keep_live_events — не трогать события, которые пришли сами (iCORE Phone: у них
        есть client_event_id). Так ходит выгрузка Oktell: оператор СЗоВ часть дня сидит
        на обзвоне в телефоне, и «replace» по дню стирал бы его телефонные статусы при
        каждой выгрузке. Заменяются только прежние события выгрузки; сегменты дня
        пересобираются из обоих источников (_overlay_live_status_sessions).
        """
        if not isinstance(events, list):
            raise ValueError("events must be a list")
        if not isinstance(segments, list):
            raise ValueError("segments must be a list")

        summary_payload = summary if isinstance(summary, dict) else {}
        summary_meta = summary_payload.get("meta")
        if not isinstance(summary_meta, dict):
            summary_meta = {}
        authoritative_segments = bool(summary_meta.get("segments_authoritative"))

        def _safe_int(value, default=0):
            try:
                return int(value)
            except Exception:
                return int(default)

        imported_by_id = int(imported_by) if imported_by is not None else None
        batch_id = uuid.uuid4()

        normalized_events = []
        normalized_segments = []

        event_ranges_map = {}
        segment_ranges_map = {}
        affected_operator_ids = set()

        def _extend_range(ranges_map, operator_id, day_value):
            prev = ranges_map.get(operator_id)
            if prev is None:
                ranges_map[operator_id] = [day_value, day_value]
                return
            if day_value < prev[0]:
                prev[0] = day_value
            if day_value > prev[1]:
                prev[1] = day_value

        for item in events:
            if not isinstance(item, dict):
                continue
            try:
                operator_id = int(item.get('operator_id'))
            except Exception:
                continue

            event_at_raw = item.get('event_at')
            if isinstance(event_at_raw, datetime):
                event_at_obj = event_at_raw
            elif isinstance(event_at_raw, str):
                text = str(event_at_raw).strip()
                if not text:
                    continue
                try:
                    event_at_obj = datetime.fromisoformat(text)
                except Exception:
                    continue
            else:
                continue

            event_date_obj = event_at_obj.date()
            state_name_raw = str(item.get('state_name') or '').strip()
            status_key = self._normalize_import_status_key(
                item.get('status_key')
                or item.get('state_key')
                or state_name_raw
            )
            if not status_key:
                continue
            state_note = str(item.get('state_note') or '').strip() or None
            event_kind = self._status_import_event_kind_from_key(
                status_key,
                item.get('event_kind') or item.get('kind')
            )

            normalized_events.append({
                'operator_id': operator_id,
                'event_at': event_at_obj,
                'event_date': event_date_obj,
                'status_key': status_key,
                'state_note': state_note,
                'event_kind': event_kind,
                'imported_by': imported_by_id
            })
            affected_operator_ids.add(operator_id)
            _extend_range(event_ranges_map, operator_id, event_date_obj)

        for item in segments:
            if not isinstance(item, dict):
                continue
            try:
                operator_id = int(item.get('operator_id'))
            except Exception:
                continue

            status_date_raw = item.get('status_date')
            if isinstance(status_date_raw, date):
                status_date_obj = status_date_raw
            elif isinstance(status_date_raw, str):
                text = str(status_date_raw).strip()
                if not text:
                    continue
                try:
                    status_date_obj = datetime.strptime(text, '%Y-%m-%d').date()
                except Exception:
                    continue
            else:
                continue

            start_at_raw = item.get('start_at')
            end_at_raw = item.get('end_at')
            if isinstance(start_at_raw, datetime):
                start_at_obj = start_at_raw
            elif isinstance(start_at_raw, str):
                text = str(start_at_raw).strip()
                if not text:
                    continue
                try:
                    start_at_obj = datetime.fromisoformat(text)
                except Exception:
                    continue
            else:
                continue

            if isinstance(end_at_raw, datetime):
                end_at_obj = end_at_raw
            elif isinstance(end_at_raw, str):
                text = str(end_at_raw).strip()
                if not text:
                    continue
                try:
                    end_at_obj = datetime.fromisoformat(text)
                except Exception:
                    continue
            else:
                continue

            if end_at_obj <= start_at_obj:
                continue

            duration_sec = _safe_int(item.get('duration_sec'), default=0)
            if duration_sec <= 0:
                duration_sec = int((end_at_obj - start_at_obj).total_seconds())
            if duration_sec <= 0:
                continue

            state_name_raw = str(item.get('state_name') or '').strip()
            status_key = self._normalize_import_status_key(
                item.get('status_key')
                or item.get('state_key')
                or state_name_raw
            )
            if not status_key:
                continue
            state_note = str(item.get('state_note') or '').strip() or None

            normalized_segments.append({
                'operator_id': operator_id,
                'status_date': status_date_obj,
                'start_at': start_at_obj,
                'end_at': end_at_obj,
                'duration_sec': duration_sec,
                'status_key': status_key,
                'state_note': state_note,
                'imported_by': imported_by_id
            })
            affected_operator_ids.add(operator_id)
            _extend_range(segment_ranges_map, operator_id, status_date_obj)

        event_ranges = sorted(
            [(int(op_id), bounds[0], bounds[1]) for op_id, bounds in event_ranges_map.items()],
            key=lambda x: (x[0], x[1], x[2])
        )
        segment_ranges = sorted(
            [(int(op_id), bounds[0], bounds[1]) for op_id, bounds in segment_ranges_map.items()],
            key=lambda x: (x[0], x[1], x[2])
        )
        # Для segment-данных используем объединение диапазонов:
        # даже если после новых событий не получилось сегментов (например, одиночное событие),
        # старые сегменты за этот период нужно очистить, чтобы не оставались устаревшие данные.
        segment_delete_ranges_map = {}
        for op_id, bounds in segment_ranges_map.items():
            segment_delete_ranges_map[int(op_id)] = [bounds[0], bounds[1]]
        for op_id, bounds in event_ranges_map.items():
            op_id_int = int(op_id)
            prev = segment_delete_ranges_map.get(op_id_int)
            if prev is None:
                segment_delete_ranges_map[op_id_int] = [bounds[0], bounds[1]]
                continue
            if bounds[0] < prev[0]:
                prev[0] = bounds[0]
            if bounds[1] > prev[1]:
                prev[1] = bounds[1]
        segment_delete_ranges = sorted(
            [(int(op_id), bounds[0], bounds[1]) for op_id, bounds in segment_delete_ranges_map.items()],
            key=lambda x: (x[0], x[1], x[2])
        )

        segment_rebuild_ranges_map = {}
        for op_id, bounds in segment_delete_ranges_map.items():
            op_id_int = int(op_id)
            segment_rebuild_ranges_map[op_id_int] = [
                bounds[0] - timedelta(days=1),
                bounds[1] + timedelta(days=1)
            ]
        segment_rebuild_ranges = sorted(
            [(int(op_id), bounds[0], bounds[1]) for op_id, bounds in segment_rebuild_ranges_map.items()],
            key=lambda x: (x[0], x[1], x[2])
        )

        range_source = segment_delete_ranges if segment_delete_ranges else event_ranges
        date_from_obj = min((r[1] for r in range_source), default=None)
        date_to_obj = max((r[2] for r in range_source), default=None)

        deleted_events = 0
        deleted_segments = 0
        segments_saved_count = len(normalized_segments)
        segment_rebuild_summary = None
        auto_aggregation_summary = {
            'updated_days': 0,
            'aggregated_months': 0,
            'auto_flag_fines': {
                'inserted': 0,
                'updated': 0,
                'deleted': 0
            }
        }

        with self._get_cursor() as cursor:
            self._lock_operator_status_segments_tx(cursor, affected_operator_ids)

            delete_events_sql = """
                    DELETE FROM operator_status_events
                    WHERE operator_id = %s
                      AND event_date >= %s
                      AND event_date <= %s
                    """
            if keep_live_events:
                delete_events_sql += "  AND client_event_id IS NULL\n"
            for operator_id, start_date_obj, end_date_obj in event_ranges:
                cursor.execute(
                    delete_events_sql,
                    (operator_id, start_date_obj, end_date_obj)
                )
                deleted_events += max(0, int(cursor.rowcount or 0))

            for operator_id, start_date_obj, end_date_obj in segment_delete_ranges:
                cursor.execute(
                    """
                    DELETE FROM operator_status_segments
                    WHERE operator_id = %s
                      AND status_date >= %s
                      AND status_date <= %s
                    """,
                    (operator_id, start_date_obj, end_date_obj)
                )
                deleted_segments += max(0, int(cursor.rowcount or 0))

            batch_meta = summary_payload.get('meta')
            if not isinstance(batch_meta, dict):
                batch_meta = {}
            batch_meta_json = json.dumps(batch_meta, ensure_ascii=False)

            cursor.execute(
                """
                INSERT INTO operator_status_import_batches (
                    id, imported_by,
                    source_rows, valid_events, matched_events, segments_saved,
                    deleted_events, deleted_segments,
                    invalid_rows_count, parse_errors_count,
                    operators_count, open_tail_events, zero_or_negative_transitions,
                    date_from, date_to, meta_json
                )
                VALUES (
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s::jsonb
                )
                """,
                (
                    str(batch_id),
                    imported_by_id,
                    _safe_int(summary_payload.get('source_rows'), default=0),
                    _safe_int(summary_payload.get('valid_events'), default=0),
                    len(normalized_events),
                    segments_saved_count,
                    deleted_events,
                    deleted_segments,
                    _safe_int(summary_payload.get('invalid_rows_count'), default=0),
                    _safe_int(summary_payload.get('parse_errors_count'), default=0),
                    len(affected_operator_ids),
                    _safe_int(summary_payload.get('open_tail_events'), default=0),
                    _safe_int(summary_payload.get('zero_or_negative_transitions'), default=0),
                    date_from_obj,
                    date_to_obj,
                    batch_meta_json
                )
            )

            if normalized_events:
                event_insert_columns = [
                    'operator_id',
                    'event_at',
                    'event_date',
                    'status_key',
                    'state_note',
                    'event_kind',
                    'imported_by',
                    'is_authoritative'
                ]
                event_values = []
                for item in normalized_events:
                    row_values = []
                    for col in event_insert_columns:
                        if col == 'is_authoritative':
                            row_values.append(bool(authoritative_segments))
                        else:
                            row_values.append(item.get(col))
                    event_values.append(tuple(row_values))
                event_columns_sql = ', '.join(event_insert_columns)
                execute_values(
                    cursor,
                    f"""
                    INSERT INTO operator_status_events (
                        {event_columns_sql}
                    )
                    VALUES %s
                    """,
                    event_values,
                    page_size=STATUS_IMPORT_INSERT_PAGE_SIZE
                )

            if normalized_events and segment_rebuild_ranges and not authoritative_segments:
                rebuild_start_obj = min(r[1] for r in segment_rebuild_ranges)
                rebuild_end_obj = max(r[2] for r in segment_rebuild_ranges)
                rebuilt = self._rebuild_operator_status_segments_tx(
                    cursor=cursor,
                    operator_ids=[r[0] for r in segment_rebuild_ranges],
                    start_date=rebuild_start_obj,
                    end_date=rebuild_end_obj,
                    imported_by=imported_by_id
                )
                rebuild_saved = int(rebuilt.get('segments_saved') or 0)
                rebuild_deleted = int(rebuilt.get('deleted_segments') or 0)
                segments_saved_count = int(rebuild_saved)
                deleted_segments += int(rebuild_deleted)
                segment_rebuild_summary = {
                    'segments_saved': int(rebuild_saved),
                    'deleted_segments': int(rebuild_deleted),
                    'range_start': rebuild_start_obj.strftime('%Y-%m-%d'),
                    'range_end': rebuild_end_obj.strftime('%Y-%m-%d'),
                    'operator_ids': [int(r[0]) for r in segment_rebuild_ranges]
                }

            if normalized_segments and (not normalized_events or authoritative_segments):
                segment_insert_columns = [
                    'operator_id',
                    'status_date',
                    'start_at',
                    'end_at',
                    'duration_sec',
                    'status_key',
                    'state_note',
                    'imported_by',
                    'is_authoritative'
                ]
                segment_values = []
                for item in normalized_segments:
                    row_values = []
                    for col in segment_insert_columns:
                        if col == 'is_authoritative':
                            row_values.append(bool(authoritative_segments))
                        else:
                            row_values.append(item.get(col))
                    segment_values.append(tuple(row_values))
                segment_columns_sql = ', '.join(segment_insert_columns)
                execute_values(
                    cursor,
                    f"""
                    INSERT INTO operator_status_segments (
                        {segment_columns_sql}
                    )
                    VALUES %s
                    """,
                    segment_values,
                    page_size=STATUS_IMPORT_INSERT_PAGE_SIZE
                )

            cursor.execute(
                """
                UPDATE operator_status_import_batches
                SET segments_saved = %s,
                    deleted_segments = %s
                WHERE id = %s
                """,
                (int(segments_saved_count), int(deleted_segments), str(batch_id))
            )

            if affected_operator_ids and date_from_obj and date_to_obj:
                if normalized_events and segment_rebuild_ranges and not authoritative_segments:
                    recalc_start_obj = min(r[1] for r in segment_rebuild_ranges)
                    recalc_end_obj = max(r[2] for r in segment_rebuild_ranges)
                else:
                    recalc_start_obj = date_from_obj - timedelta(days=1)
                    recalc_end_obj = date_to_obj
                auto_aggregation_summary = self._recalculate_auto_daily_hours_tx(
                    cursor=cursor,
                    operator_ids=sorted(int(v) for v in affected_operator_ids),
                    start_date=recalc_start_obj,
                    end_date=recalc_end_obj
                )

        return {
            'batch_id': str(batch_id),
            'source_rows': _safe_int(summary_payload.get('source_rows'), default=0),
            'valid_events': _safe_int(summary_payload.get('valid_events'), default=0),
            'matched_events': len(normalized_events),
            'segments_saved': int(segments_saved_count),
            'deleted_events': int(deleted_events),
            'deleted_segments': int(deleted_segments),
            'invalid_rows_count': _safe_int(summary_payload.get('invalid_rows_count'), default=0),
            'parse_errors_count': _safe_int(summary_payload.get('parse_errors_count'), default=0),
            'operators_count': len(affected_operator_ids),
            'open_tail_events': _safe_int(summary_payload.get('open_tail_events'), default=0),
            'zero_or_negative_transitions': _safe_int(summary_payload.get('zero_or_negative_transitions'), default=0),
            'date_from': date_from_obj.strftime('%Y-%m-%d') if date_from_obj else None,
            'date_to': date_to_obj.strftime('%Y-%m-%d') if date_to_obj else None,
            'segment_rebuild': segment_rebuild_summary,
            'auto_aggregation': auto_aggregation_summary
        }

    def append_operator_status_event(self, operator_id, event_at, status_key,
                                     state_note=None, event_kind=None,
                                     client_event_id=None):
        """Добавить ОДНО событие статуса оператора (live-пуш из iCORE Phone).

        В отличие от save_operator_status_import (который работает как replace
        целого дня и предназначен для пакетного импорта CSV), это аддитивная
        вставка одного события с локальным пересчётом сегментов и дневных часов
        ТОЛЬКО по этому оператору и вокруг затронутого дня (±1 сутки — событие
        может закрывать сегмент прошлых суток или открывать хвост следующих).

        Идемпотентность: при переданном client_event_id повторная отправка того
        же (operator_id, client_event_id) НЕ создаёт дубликат и НЕ пересчитывает
        (возвращает {'duplicate': True}). Вставка защищена частичным уникальным
        индексом + ON CONFLICT DO NOTHING, поэтому безопасна и при гонке
        параллельных ретраев из офлайн-очереди телефона.

        Всё выполняется в одной транзакции (вставка + rebuild сегментов +
        пересчёт часов), чтобы не оставлять сегменты/часы рассогласованными.
        """
        try:
            operator_id = int(operator_id)
        except Exception:
            raise ValueError("operator_id must be an integer")

        if isinstance(event_at, datetime):
            event_at_obj = event_at
        elif isinstance(event_at, str):
            text = event_at.strip()
            if not text:
                raise ValueError("event_at is required")
            try:
                event_at_obj = datetime.fromisoformat(text)
            except Exception:
                raise ValueError("event_at must be an ISO-8601 datetime")
        else:
            raise ValueError("event_at must be a datetime or ISO-8601 string")
        # Событие храним как naive-ЛОКАЛЬНОЕ время (Asia/Almaty), как и импорт
        # CSV: из event_at выводится event_date и сегменты сопоставляются со
        # сменами (work_shifts, локальные). Телефон работает по локальным часам
        # оператора и шлёт naive-локаль — сохраняем как есть. Если вдруг пришло
        # tz-aware значение, приводим к Алматы и снимаем tzinfo (НЕ к UTC, иначе
        # событие уедет на другой день/час относительно всей остальной аналитики).
        if event_at_obj.tzinfo is not None:
            event_at_obj = event_at_obj.astimezone(ZoneInfo('Asia/Almaty')).replace(tzinfo=None)

        event_date_obj = event_at_obj.date()
        norm_status_key = self._normalize_import_status_key(status_key)
        if not norm_status_key:
            raise ValueError("status_key is required")
        norm_note = str(state_note or '').strip() or None
        if norm_note and len(norm_note) > 255:
            norm_note = norm_note[:255]
        norm_kind = self._status_import_event_kind_from_key(norm_status_key, event_kind)
        norm_client_id = None
        if client_event_id is not None:
            norm_client_id = str(client_event_id).strip() or None
            if norm_client_id and len(norm_client_id) > 64:
                norm_client_id = norm_client_id[:64]

        with self._get_cursor() as cursor:
            # Serialize semantic comparison + INSERT + rebuild for this operator.
            # The unique client_event_id index handles transport retries; this lock
            # additionally prevents concurrent new GUIDs for the same state from
            # both passing the no-op check.
            self._lock_operator_status_segments_tx(cursor, [operator_id])

            if norm_client_id:
                cursor.execute(
                    """
                    SELECT id
                    FROM operator_status_events
                    WHERE operator_id = %s
                      AND client_event_id = %s
                    LIMIT 1
                    """,
                    (operator_id, norm_client_id)
                )
                if cursor.fetchone() is not None:
                    return {
                        'duplicate': True,
                        'operator_id': operator_id,
                        'event_date': event_date_obj.strftime('%Y-%m-%d'),
                        'status_key': norm_status_key,
                    }

            if norm_kind == 'status':
                action_status_keys = sorted({
                    self._normalize_import_status_key(item)
                    for item in CHAT_MANAGER_ACTION_STATUS_KEYS
                    if self._normalize_import_status_key(item)
                })
                cursor.execute(
                    """
                    SELECT status_key, state_note, (client_event_id IS NOT NULL) AS is_live
                    FROM operator_status_events
                    WHERE operator_id = %s
                      AND event_at <= %s
                      AND COALESCE(event_kind, 'status') <> 'action'
                      AND COALESCE(is_authoritative, FALSE) = FALSE
                      AND NOT (LOWER(TRIM(status_key)) = ANY(%s))
                    ORDER BY event_at DESC, id DESC
                    LIMIT 1
                    """,
                    (operator_id, event_at_obj, action_status_keys)
                )
                previous_state = cursor.fetchone()
                # Повтор — только если прежнее событие тоже пришло само. У оператора СЗоВ
                # перед «готов» телефона может лежать «Готов» из выгрузки Oktell: это два
                # разных источника, и первое событие телефона открывает его сессию
                # (_overlay_live_status_sessions) — проглотить его нельзя.
                if (
                    previous_state is not None
                    and bool(previous_state[2])
                    and self._status_transition_signature(
                        previous_state[0],
                        previous_state[1]
                    ) == self._status_transition_signature(
                        norm_status_key,
                        norm_note
                    )
                ):
                    return {
                        'duplicate': False,
                        'noop': True,
                        'operator_id': operator_id,
                        'event_date': event_date_obj.strftime('%Y-%m-%d'),
                        'status_key': norm_status_key,
                        'event_kind': norm_kind,
                    }

            cursor.execute(
                """
                INSERT INTO operator_status_events
                    (operator_id, event_at, event_date, status_key, state_note,
                     event_kind, imported_by, client_event_id)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, %s)
                ON CONFLICT (operator_id, client_event_id)
                    WHERE client_event_id IS NOT NULL
                    DO NOTHING
                RETURNING id
                """,
                (operator_id, event_at_obj, event_date_obj, norm_status_key,
                 norm_note, norm_kind, norm_client_id)
            )
            inserted = cursor.fetchone()
            if inserted is None:
                # Повтор с тем же client_event_id — событие уже есть, ничего не
                # пересчитываем (данные не изменились).
                return {
                    'duplicate': True,
                    'operator_id': operator_id,
                    'event_date': event_date_obj.strftime('%Y-%m-%d'),
                    'status_key': norm_status_key,
                }
            new_id = int(inserted[0])

            rebuild_start = event_date_obj - timedelta(days=1)
            rebuild_end = event_date_obj + timedelta(days=1)
            rebuilt = self._rebuild_operator_status_segments_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=rebuild_start,
                end_date=rebuild_end,
                imported_by=None
            )
            auto = self._recalculate_auto_daily_hours_tx(
                cursor=cursor,
                operator_ids=[operator_id],
                start_date=rebuild_start,
                end_date=rebuild_end
            )

        return {
            'duplicate': False,
            'event_id': new_id,
            'operator_id': operator_id,
            'event_date': event_date_obj.strftime('%Y-%m-%d'),
            'status_key': norm_status_key,
            'event_kind': norm_kind,
            'segments_saved': int((rebuilt or {}).get('segments_saved') or 0),
            'auto_aggregation': auto,
        }

    def get_operator_live_statuses(self, operator_ids, as_of=None, lookback_hours=None):
        """{operator_id: {'status_key', 'event_at', 'seconds'}} — последнее событие каждого.

        Окно по времени, а НЕ «события за сегодня», по двум причинам сразу:
        ночная смена переходит через полночь (последнее событие человека в 03:00
        лежит вчерашним числом и по дате потерялось бы), а событие трёхдневной
        давности — это не «до сих пор активен», а телефон, который умер, не
        прислав «выключен». Поэтому старше окна статус не показываем вовсе:
        «Активный, 3 дня» на стене читается как работающий человек.

        Оператор без событий в ответ НЕ попадает: его статус неизвестен, и это
        не то же самое, что «не в сети» (телефон мог просто не обновиться).
        Отличить одно от другого обязан вызывающий — правило нуля табло.

        Берём ТОЛЬКО события телефона (`client_event_id IS NOT NULL`). В той же
        таблице живут сегменты ночной выгрузки кабинета Binotel, и они не статус
        «сейчас», а история вчерашнего дня: 11.09.2026 на стене висело
        «Inactive, 14:48» у троих — их последним событием был выход из кабинета,
        записанный ночным импортом за 10.09. Живой статус приходит только через
        POST /api/operator/status_event, и только он несёт client_event_id.
        """
        ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        if not ids:
            return {}
        now_value = as_of or datetime.now(ZoneInfo('Asia/Almaty')).replace(tzinfo=None)
        if isinstance(now_value, datetime) and now_value.tzinfo is not None:
            now_value = now_value.astimezone(ZoneInfo('Asia/Almaty')).replace(tzinfo=None)
        hours = int(lookback_hours or self.OPERATOR_LIVE_STATUS_LOOKBACK_HOURS)
        floor_value = now_value - timedelta(hours=max(1, hours))
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (e.operator_id)
                       e.operator_id, e.status_key, e.event_at
                FROM operator_status_events e
                WHERE e.operator_id = ANY(%s)
                  AND e.event_at >= %s
                  AND e.client_event_id IS NOT NULL
                ORDER BY e.operator_id, e.event_at DESC, e.id DESC
                """,
                (ids, floor_value)
            )
            rows = cursor.fetchall()
        out = {}
        for operator_id, status_key, event_at in rows:
            key = self._normalize_import_status_key(status_key)
            if not key:
                continue
            seconds = None
            if isinstance(event_at, datetime) and event_at <= now_value:
                seconds = int((now_value - event_at).total_seconds())
            out[int(operator_id)] = {
                'status_key': key,
                'event_at': event_at.isoformat() if isinstance(event_at, datetime) else None,
                'seconds': seconds,
            }
        return out
