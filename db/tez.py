"""База данных: Тез.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    Any,
    CALCULATION_MODEL_TEZ_CODES,
    CALCULATION_MODEL_TEZ_LINE,
    CALCULATION_MODEL_TEZ_OP,
    datetime,
    Dict,
    dt_time,
    execute_values,
    Json,
    List,
    logging,
    timedelta,
)


class TezMixin:
    def get_tez_op_binotel_internal_numbers(self, start_date, end_date):
        """Внутренние номера Binotel, относящиеся к модели TEZ ОП в периоде.

        Один SIP может последовательно принадлежать нескольким людям, поэтому
        возвращаем только DISTINCT номера. Конкретного оператора для каждого
        звонка определяет имя из employeeData и модель на дату звонка.
        """
        start_obj = self._normalize_schedule_date(start_date)
        end_obj = self._normalize_schedule_date(end_date)
        if end_obj < start_obj:
            start_obj, end_obj = end_obj, start_obj
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT TRIM(u.sip_number)
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE NULLIF(TRIM(u.sip_number), '') IS NOT NULL
                  AND (
                    LOWER(COALESCE(d.calculation_model_code, '')) = %s
                    OR EXISTS (
                        SELECT 1
                        FROM group_operator_memberships gom
                        JOIN groups gr ON gr.id = gom.group_id
                        WHERE gom.operator_id = u.id
                          AND LOWER(COALESCE(gr.calculation_model_code, '')) = %s
                          AND gom.start_date <= %s
                          AND (gom.end_date IS NULL OR gom.end_date >= %s)
                    )
                  )
                ORDER BY 1
                """,
                (CALCULATION_MODEL_TEZ_OP, CALCULATION_MODEL_TEZ_OP, end_obj, start_obj),
            )
            return [str(row[0]).strip() for row in (cursor.fetchall() or []) if row and row[0]]

    def get_tez_wallboard_operators(self, department_id, on_date=None):
        """Состав табло Тез КЦ: действующие сотрудники отдела с моделью расчёта и SIP.

        Почему отдельный метод, а не общий lookup табло СЗоВ (_szov_wallboard_operator_lookup):
          - тот строится из get_all_operators() с WHERE role = 'operator', и стажёры со
            старшими сменами молча выпали бы из счётчиков «онлайн» и списков перерывов;
          - там нет фильтра по занятости, а sip_number в этом отделе НЕ уникален: один и тот
            же номер числится и за уволенным, и за действующим (914, 922, 924, 925, 927 на
            08.09.2026). Без отсева уволенных в списке «на перерыве» появились бы чужие
            фамилии — самый заметный сорт брака на экране в зале. Проверено: после отсева
            уволенных дублей SIP в отделе не остаётся ни одного;
          - ТП и ОП живут в ОДНОМ отделе, и разделить их можно только моделью расчёта.

        ТП от ОП отличает ГРУППА, а не направление. Проверено на проде 08.09.2026: у обоих
        направлений («ТП линия» id 83 и «ОП линия» id 78) calculation_model_code = 'operator',
        то есть модель роли, а не модель отдела; настоящие tez_line / tez_op стоят на группах
        34 «Тез КЦ - Тех поддержка» и 35 «Tez КЦ - Отдел продаж». Поэтому группа — первый ключ,
        направление — запасной. По id направления не отбираем принципиально: оно версионируется
        (у «ТП линия» уже семь версий), а переименование обнуляет users.direction_id.
        """
        day_obj = self._normalize_schedule_date(on_date or datetime.now().date())
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id,
                       u.name,
                       NULLIF(TRIM(u.sip_number), ''),
                       u.direction_id,
                       d.name,
                       LOWER(COALESCE(d.calculation_model_code, '')),
                       (
                           SELECT LOWER(COALESCE(gr.calculation_model_code, ''))
                           FROM group_operator_memberships gom
                           JOIN groups gr ON gr.id = gom.group_id
                           WHERE gom.operator_id = u.id
                             AND gom.start_date <= %s
                             AND (gom.end_date IS NULL OR gom.end_date >= %s)
                           ORDER BY gom.start_date DESC
                           LIMIT 1
                       ),
                       u.role
                FROM users u
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE u.department_id = %s
                  AND LOWER(COALESCE(u.status, '')) <> ALL(%s)
                ORDER BY u.name
                """,
                (day_obj, day_obj, int(department_id),
                 list(self.TEZ_WALLBOARD_EXCLUDED_STATUSES)),
            )
            rows = cursor.fetchall() or []

        out = []
        for row in rows:
            if not row or not row[0]:
                continue
            direction_name = str(row[4] or '').strip() or None
            direction_model = str(row[5] or '').strip() or None
            group_model = str(row[6] or '').strip() or None
            # Лестница: модель группы -> модель направления -> имя направления. Последняя
            # ступень нужна для тех, кого в группу ещё не завели: без неё новый сотрудник
            # просто исчезал бы с табло, и это выглядело бы как «человек не вышел».
            model = group_model if group_model in CALCULATION_MODEL_TEZ_CODES else None
            if model is None and direction_model in CALCULATION_MODEL_TEZ_CODES:
                model = direction_model
            if model is None and direction_name:
                lowered = direction_name.strip().lower()
                if lowered.startswith('тп'):
                    model = CALCULATION_MODEL_TEZ_LINE
                elif lowered.startswith('оп'):
                    model = CALCULATION_MODEL_TEZ_OP
            out.append({
                'id': int(row[0]),
                'name': str(row[1] or '').strip(),
                'sip_number': str(row[2]).strip() if row[2] else None,
                'direction_id': row[3],
                'direction_name': direction_name,
                'model': model,
                'group_model': group_model,
                'role': str(row[7] or '').strip() or None,
            })
        return out

    def replace_tez_op_call_metrics(self, start_date, end_date, rows):
        """Идемпотентно заменяет телефонную продуктивность TEZ ОП за период.

        `calls` хранится в штатной колонке, а времена набора/разговора — в
        extra_metrics. Последнее принципиально: автопересчёт часов по статусам
        владеет legacy-полем talk_time и не должен стирать данные Binotel.
        """
        start_obj = self._normalize_schedule_date(start_date)
        end_obj = self._normalize_schedule_date(end_date)
        if end_obj < start_obj:
            start_obj, end_obj = end_obj, start_obj

        normalized = []
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            try:
                operator_id = int(item.get("operator_id"))
                day_obj = self._normalize_schedule_date(item.get("day"))
            except Exception:
                continue
            if day_obj < start_obj or day_obj > end_obj:
                continue
            try:
                calls = max(0, int(item.get("calls") or 0))
                dial_time = max(
                    0.0,
                    float(
                        item.get("dial_time")
                        if item.get("dial_time") is not None
                        else float(item.get("dial_seconds") or 0) / 3600.0
                    ),
                )
                talk_time = max(
                    0.0,
                    float(
                        item.get("talk_time")
                        if item.get("talk_time") is not None
                        else float(item.get("talk_seconds") or 0) / 3600.0
                    ),
                )
            except (TypeError, ValueError):
                continue
            normalized.append(
                {
                    "operator_id": operator_id,
                    "day": day_obj,
                    "calls": calls,
                    "dial_time": round(dial_time, 8),
                    "talk_time": round(talk_time, 8),
                }
            )

        with self._get_cursor() as cursor:
            # Replace-семантика: исчезнувший после повторного импорта звонок не
            # должен оставлять вчерашнее значение. Часы/штрафы не затрагиваем.
            cursor.execute(
                """
                UPDATE daily_hours dh
                SET calls = 0,
                    extra_metrics = jsonb_set(
                        jsonb_set(
                            COALESCE(dh.extra_metrics, '{}'::jsonb),
                            '{dial_time}', '0'::jsonb, TRUE
                        ),
                        '{talk_time}', '0'::jsonb, TRUE
                    )
                WHERE dh.day >= %s
                  AND dh.day <= %s
                  AND LOWER(COALESCE(
                        (
                            SELECT gr.calculation_model_code
                            FROM group_operator_memberships gom
                            JOIN groups gr ON gr.id = gom.group_id
                            WHERE gom.operator_id = dh.operator_id
                              AND gom.start_date <= dh.day
                              AND (gom.end_date IS NULL OR gom.end_date >= dh.day)
                            ORDER BY gom.start_date DESC
                            LIMIT 1
                        ),
                        (
                            SELECT d.calculation_model_code
                            FROM users u
                            LEFT JOIN directions d ON d.id = u.direction_id
                            WHERE u.id = dh.operator_id
                            LIMIT 1
                        ),
                        ''
                  )) = %s
                RETURNING dh.operator_id, dh.day
                """,
                (
                    start_obj,
                    end_obj,
                    CALCULATION_MODEL_TEZ_OP,
                ),
            )
            cleared = max(0, int(cursor.rowcount or 0))
            cleared_rows = cursor.fetchall() or []

            values = []
            for item in normalized:
                group_id = self._get_operator_group_id_tx(
                    cursor, item["operator_id"], item["day"]
                )
                values.append(
                    (
                        item["operator_id"],
                        item["day"],
                        item["calls"],
                        group_id,
                        Json(
                            {
                                "dial_time": item["dial_time"],
                                "talk_time": item["talk_time"],
                            }
                        ),
                    )
                )
            if values:
                execute_values(
                    cursor,
                    """
                    INSERT INTO daily_hours (
                        operator_id, day, calls, group_id, extra_metrics
                    )
                    VALUES %s
                    ON CONFLICT (operator_id, day) DO UPDATE SET
                        calls = EXCLUDED.calls,
                        group_id = COALESCE(EXCLUDED.group_id, daily_hours.group_id),
                        extra_metrics = COALESCE(daily_hours.extra_metrics, '{}'::jsonb)
                                        || EXCLUDED.extra_metrics
                    """,
                    values,
                    page_size=500,
                )

            # work_hours читают не только экран учёта часов, но и общие сводки.
            # Обновляем их в той же транзакции, включая операторов, у которых
            # после повторной выгрузки звонки исчезли.
            affected_operator_months = {
                (int(operator_id), day_obj.strftime("%Y-%m"))
                for operator_id, day_obj in cleared_rows
                if operator_id is not None and day_obj is not None
            }
            affected_operator_months.update(
                (
                    int(item["operator_id"]),
                    item["day"].strftime("%Y-%m"),
                )
                for item in normalized
            )
            for operator_id, month_key in sorted(affected_operator_months):
                self._aggregate_month_from_daily_tx(cursor, operator_id, month_key)

        return {
            "date_from": start_obj.strftime("%Y-%m-%d"),
            "date_to": end_obj.strftime("%Y-%m-%d"),
            "cleared_days": cleared,
            "saved_days": len(values),
            "calls": sum(item["calls"] for item in normalized),
        }

    def refresh_tez_op_chat_metrics(self, start_date, end_date):
        """Пересчитывает дневные чаты TEZ ОП из сообщений ChatApp.

        Чат засчитывается каждому оператору, который написал в нём хотя бы одно
        ручное сообщение в этот локальный день. Доминирующий автор эпизода здесь
        намеренно не используется.
        """
        start_obj = self._normalize_schedule_date(start_date)
        end_obj = self._normalize_schedule_date(end_date)
        if end_obj < start_obj:
            start_obj, end_obj = end_obj, start_obj

        human_out = self._CHATAPP_HUMAN_OUT
        # Чат с номером из «Реестра тестовых номеров» (test_numbers) оператору не
        # засчитывается. Номер — у чата, не у сообщения.
        from test_numbers import keys as test_keys
        not_test_chat = (
            "NOT EXISTS (SELECT 1 FROM chatapp_chats tc"
            " WHERE tc.license_id = m.license_id AND tc.messenger_type = m.messenger_type"
            " AND tc.chat_id = m.chat_id AND "
            + test_keys.sql_is_test("COALESCE(NULLIF(tc.phone, ''), tc.chat_id)", digits=True) + ")")
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE daily_hours dh
                SET extra_metrics = jsonb_set(
                    COALESCE(dh.extra_metrics, '{}'::jsonb),
                    '{chats}', '0'::jsonb, TRUE
                )
                WHERE dh.day >= %s
                  AND dh.day <= %s
                  AND COALESCE(dh.extra_metrics, '{}'::jsonb) ? 'chats'
                  AND LOWER(COALESCE(
                        (
                            SELECT gr.calculation_model_code
                            FROM group_operator_memberships gom
                            JOIN groups gr ON gr.id = gom.group_id
                            WHERE gom.operator_id = dh.operator_id
                              AND gom.start_date <= dh.day
                              AND (gom.end_date IS NULL OR gom.end_date >= dh.day)
                            ORDER BY gom.start_date DESC
                            LIMIT 1
                        ),
                        (
                            SELECT d.calculation_model_code
                            FROM users u
                            LEFT JOIN directions d ON d.id = u.direction_id
                            WHERE u.id = dh.operator_id
                            LIMIT 1
                        ),
                        ''
                  )) = %s
                """,
                (start_obj, end_obj, CALCULATION_MODEL_TEZ_OP),
            )
            cleared = max(0, int(cursor.rowcount or 0))

            cursor.execute(
                f"""
                SELECT
                    map.user_id,
                    (m.dt AT TIME ZONE 'Asia/Almaty')::date AS local_day,
                    COUNT(DISTINCT (m.license_id, m.messenger_type, m.chat_id)) AS chats
                FROM chatapp_messages m
                JOIN chatapp_operator_map map ON map.employee_id = m.employee_id
                LEFT JOIN users u ON u.id = map.user_id
                LEFT JOIN directions d ON d.id = u.direction_id
                LEFT JOIN LATERAL (
                    SELECT gr.calculation_model_code
                    FROM group_operator_memberships gom
                    JOIN groups gr ON gr.id = gom.group_id
                    WHERE gom.operator_id = map.user_id
                      AND gom.start_date <= (m.dt AT TIME ZONE 'Asia/Almaty')::date
                      AND (
                        gom.end_date IS NULL
                        OR gom.end_date >= (m.dt AT TIME ZONE 'Asia/Almaty')::date
                      )
                    ORDER BY gom.start_date DESC
                    LIMIT 1
                ) active_group ON TRUE
                WHERE map.user_id IS NOT NULL
                  AND COALESCE(map.is_bot, FALSE) = FALSE
                  AND COALESCE(m.is_deleted, FALSE) = FALSE
                  AND ({human_out})
                  AND {not_test_chat}
                  AND (m.dt AT TIME ZONE 'Asia/Almaty')::date >= %s
                  AND (m.dt AT TIME ZONE 'Asia/Almaty')::date <= %s
                  AND LOWER(COALESCE(
                        active_group.calculation_model_code,
                        d.calculation_model_code,
                        ''
                  )) = %s
                GROUP BY map.user_id, local_day
                ORDER BY map.user_id, local_day
                """,
                (
                    start_obj,
                    end_obj,
                    CALCULATION_MODEL_TEZ_OP,
                ),
            )
            aggregate_rows = cursor.fetchall() or []

            values = []
            for operator_id, day_obj, chats in aggregate_rows:
                group_id = self._get_operator_group_id_tx(cursor, operator_id, day_obj)
                values.append(
                    (
                        int(operator_id),
                        day_obj,
                        group_id,
                        Json({"chats": max(0, int(chats or 0))}),
                    )
                )
            if values:
                execute_values(
                    cursor,
                    """
                    INSERT INTO daily_hours (
                        operator_id, day, group_id, extra_metrics
                    )
                    VALUES %s
                    ON CONFLICT (operator_id, day) DO UPDATE SET
                        group_id = COALESCE(EXCLUDED.group_id, daily_hours.group_id),
                        extra_metrics = COALESCE(daily_hours.extra_metrics, '{}'::jsonb)
                                        || EXCLUDED.extra_metrics
                    """,
                    values,
                    page_size=500,
                )

        return {
            "date_from": start_obj.strftime("%Y-%m-%d"),
            "date_to": end_obj.strftime("%Y-%m-%d"),
            "cleared_days": cleared,
            "saved_days": len(values),
            "chats": sum(int(row[2] or 0) for row in aggregate_rows),
        }

    def get_tez_op_department_plan_summary(self, department_id, year, month, plan_per_fte=None, norm_hours_fte=None):
        from tez.department_plan import summary
        return summary(self, department_id, year, month, plan_per_fte, norm_hours_fte)

    # ─────────────────────── Успешки TEZ ОП: база лидов ───────────────────────

    @classmethod
    def _lock_tez_lead_period_tx(cls, cursor, year, month):
        """Сериализует изменения состава/состояния базы лидов внутри месяца.

        Лиды дедуплицируются по (year, month, phone_norm), поэтому блокировки одной
        строки загрузки недостаточно: две разные загрузки могут менять один и тот же
        канонический лид. Advisory xact lock освобождается вместе с транзакцией.
        """
        period_key = int(year) * 100 + int(month)
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (int(cls.TEZ_LEAD_PERIOD_LOCK_NAMESPACE), period_key),
        )

    def create_tez_lead_batch(self, department_id, year, month, uploaded_by, file_name=''):
        """Заводит запись о загрузке базы лидов и возвращает её id."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tez_lead_batches
                    (department_id, year, month, uploaded_by, file_name, check_status)
                VALUES (%s, %s, %s, %s, %s, 'pending')
                RETURNING id
                """,
                (
                    int(department_id) if department_id is not None else None,
                    int(year), int(month),
                    int(uploaded_by) if uploaded_by is not None else None,
                    str(file_name or '')[:255],
                )
            )
            return str(cursor.fetchone()[0])

    def import_tez_lead_rows(self, batch_id, year, month, rows):
        """Записывает строки загрузки и накатывает их на помесячную базу лидов.

        rows — список кортежей (row_number, raw_full_name, raw_phone, phone_norm),
        где phone_norm=None означает невалидный номер.

        База помесячная: один и тот же номер в июньской и июльской базе — это два
        разных лида. Повторная загрузка того же номера в тот же месяц не создаёт
        дубль, а увеличивает upload_count (владелец просил историю дублей).
        """
        counts = {'rows_total': 0, 'rows_new': 0, 'rows_duplicate': 0, 'rows_invalid': 0}
        with self._get_cursor() as cursor:
            requested_year, requested_month = int(year), int(month)
            # Единый порядок блокировок для import/delete/restore:
            # period advisory lock -> batch FOR UPDATE. Иначе import может
            # продолжиться уже после мягкого удаления родительской загрузки.
            self._lock_tez_lead_period_tx(cursor, requested_year, requested_month)
            cursor.execute(
                """
                SELECT year, month, deleted_at
                FROM tez_lead_batches
                WHERE id = %s
                FOR UPDATE
                """,
                (batch_id,),
            )
            batch = cursor.fetchone()
            if not batch:
                raise ValueError('TEZ lead batch not found')
            batch_year, batch_month = int(batch[0]), int(batch[1])
            if (batch_year, batch_month) != (requested_year, requested_month):
                raise ValueError('TEZ lead batch period mismatch')
            if batch[2] is not None:
                raise ValueError('Cannot import rows into a deleted TEZ lead batch')

            for row_number, raw_name, raw_phone, phone_norm in rows or []:
                counts['rows_total'] += 1
                clean_name = str(raw_name or '').strip()[:255]

                if not phone_norm:
                    counts['rows_invalid'] += 1
                    cursor.execute(
                        """
                        INSERT INTO tez_lead_batch_rows
                            (batch_id, row_number, raw_full_name, raw_phone, phone_norm,
                             lead_id, row_status)
                        VALUES (%s, %s, %s, %s, NULL, NULL, 'invalid')
                        """,
                        (batch_id, int(row_number or 0), clean_name, str(raw_phone or '')[:64])
                    )
                    continue

                # Факт первой поездки — свойство водителя, а не месяца базы.
                cursor.execute(
                    "INSERT INTO tez_drivers (phone_norm) VALUES (%s) ON CONFLICT (phone_norm) DO NOTHING",
                    (phone_norm,)
                )
                cursor.execute(
                    """
                    INSERT INTO tez_leads
                        (year, month, phone_norm, full_name, first_batch_id, last_batch_id, upload_count)
                    VALUES (%s, %s, %s, %s, %s, %s, 1)
                    ON CONFLICT (year, month, phone_norm) DO UPDATE SET
                        upload_count = tez_leads.upload_count + 1,
                        last_batch_id = EXCLUDED.last_batch_id,
                        full_name = CASE
                            WHEN tez_leads.full_name = '' THEN EXCLUDED.full_name
                            ELSE tez_leads.full_name
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id, (xmax = 0) AS inserted
                    """,
                    (batch_year, batch_month, phone_norm, clean_name, batch_id, batch_id)
                )
                lead_id, inserted = cursor.fetchone()
                if inserted:
                    counts['rows_new'] += 1
                    row_status = 'new'
                else:
                    counts['rows_duplicate'] += 1
                    row_status = 'duplicate'

                cursor.execute(
                    """
                    INSERT INTO tez_lead_batch_rows
                        (batch_id, row_number, raw_full_name, raw_phone, phone_norm, lead_id, row_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (batch_id, int(row_number or 0), clean_name,
                     str(raw_phone or '')[:64], phone_norm, lead_id, row_status)
                )

            cursor.execute(
                """
                UPDATE tez_lead_batches
                SET rows_total = %s, rows_new = %s, rows_duplicate = %s, rows_invalid = %s
                WHERE id = %s
                """,
                (counts['rows_total'], counts['rows_new'],
                 counts['rows_duplicate'], counts['rows_invalid'], batch_id)
            )
        return counts

    def set_tez_lead_batch_check_state(self, batch_id, check_status, error=None, already_working=None):
        """Отмечает стадию фоновой проверки базы на «уже работающих»."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE tez_lead_batches
                SET check_status = %s,
                    check_error = %s,
                    already_working = COALESCE(%s, already_working)
                WHERE id = %s
                """,
                (str(check_status or 'pending'),
                 str(error)[:2000] if error else None,
                 int(already_working) if already_working is not None else None,
                 batch_id)
            )

    def get_tez_lead_batch_metadata(self, batch_id):
        """Минимальные метаданные загрузки для проверки HTTP-scope."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, department_id, year, month, deleted_at
                FROM tez_lead_batches
                WHERE id = %s
                """,
                (batch_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            'id': str(row[0]),
            'department_id': int(row[1]) if row[1] is not None else None,
            'year': int(row[2]),
            'month': int(row[3]),
            'is_deleted': row[4] is not None,
        }

    def list_tez_lead_batches(self, year, month, include_deleted=True):
        """Загрузки базы за месяц (свежие сверху).

        Удалённые загрузки по умолчанию остаются в списке (с deleted_at и автором
        удаления) — это и есть требуемая история: видно, что база была и кто её убрал.
        """
        sql = """
            SELECT b.id, b.file_name, b.rows_total, b.rows_new, b.rows_duplicate,
                   b.rows_invalid, b.already_working, b.check_status, b.check_error,
                   b.created_at, u.name,
                   b.deleted_at,
                   COALESCE(NULLIF(de.actor_name, ''), du.name, ''),
                   COALESCE(NULLIF(de.reason, ''), b.delete_reason, ''),
                   b.restored_at,
                   COALESCE(NULLIF(re.actor_name, ''), ru.name, '')
            FROM tez_lead_batches b
            LEFT JOIN users u ON u.id = b.uploaded_by
            LEFT JOIN users du ON du.id = b.deleted_by
            LEFT JOIN users ru ON ru.id = b.restored_by
            LEFT JOIN LATERAL (
                SELECT e.actor_name, e.reason
                FROM tez_lead_batch_events e
                WHERE e.batch_id = b.id AND e.action = 'delete'
                ORDER BY e.event_seq DESC
                LIMIT 1
            ) de ON TRUE
            LEFT JOIN LATERAL (
                SELECT e.actor_name
                FROM tez_lead_batch_events e
                WHERE e.batch_id = b.id AND e.action = 'restore'
                ORDER BY e.event_seq DESC
                LIMIT 1
            ) re ON TRUE
            WHERE b.year = %s AND b.month = %s
        """
        if not include_deleted:
            sql += " AND b.deleted_at IS NULL"
        sql += " ORDER BY b.created_at DESC"
        with self._get_cursor() as cursor:
            cursor.execute(sql, (int(year), int(month)))
            return [{
                'id': str(r[0]),
                'file_name': r[1],
                'rows_total': int(r[2] or 0),
                'rows_new': int(r[3] or 0),
                'rows_duplicate': int(r[4] or 0),
                'rows_invalid': int(r[5] or 0),
                'already_working': int(r[6] or 0),
                'check_status': r[7],
                'check_error': r[8],
                'created_at': r[9].isoformat() if hasattr(r[9], 'isoformat') else str(r[9] or ''),
                'uploaded_by_name': r[10] or '',
                'is_deleted': r[11] is not None,
                'deleted_at': r[11].isoformat() if hasattr(r[11], 'isoformat') else None,
                'deleted_by_name': r[12] or '',
                'delete_reason': r[13] or '',
                'restored_at': r[14].isoformat() if hasattr(r[14], 'isoformat') else None,
                'restored_by_name': r[15] or '',
            } for r in cursor.fetchall()]

    def get_tez_lead_batch_events(self, batch_id):
        """История удалений/восстановлений загрузки (старые снизу)."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT e.id, e.action, e.actor_id,
                       COALESCE(NULLIF(e.actor_name, ''), u.name, ''),
                       e.reason,
                       e.leads_removed, e.leads_kept, e.successes_removed, e.leads_merged,
                       e.created_at, e.source_event_id
                FROM tez_lead_batch_events e
                LEFT JOIN users u ON u.id = e.actor_id
                WHERE e.batch_id = %s
                ORDER BY e.event_seq DESC
                """,
                (batch_id,)
            )
            events = []
            for r in cursor.fetchall():
                action = r[1]
                # Колонки счётчиков общие для обоих действий, поэтому наружу отдаём
                # ещё и «говорящие» имена: у восстановления leads_kept — это сколько
                # лидов вернулось, а не сколько уцелело.
                affected, successes = int(r[6] or 0), int(r[7] or 0)
                events.append({
                    'id': str(r[0]),
                    'action': action,
                    'actor_id': int(r[2]) if r[2] is not None else None,
                    'actor_name': r[3] or '',
                    'reason': r[4] or '',
                    'leads_removed': int(r[5] or 0),
                    'leads_kept': affected if action == 'delete' else 0,
                    'leads_restored': affected if action == 'restore' else 0,
                    'successes_removed': successes if action == 'delete' else 0,
                    'successes_restored': successes if action == 'restore' else 0,
                    'leads_merged': int(r[8] or 0),
                    'created_at': r[9].isoformat() if hasattr(r[9], 'isoformat') else str(r[9] or ''),
                    'source_event_id': str(r[10]) if r[10] else None,
                })
            return events

    @staticmethod
    def _tez_resync_leads_from_rows_tx(cursor, lead_ids):
        """Пересобирает upload_count и first/last_batch_id из живых строк загрузок.

        Инвариант базы: upload_count лида = число строк в НЕудалённых загрузках,
        которые на него ссылаются (импорт кладёт ровно одну строку на вхождение).
        Поэтому и удаление, и откат не занимаются арифметикой «+1/−1», а просто
        пересчитывают истину из строк — операция идемпотентна и самолечится, если
        счётчик когда-то разъехался.
        """
        ids = [i for i in (lead_ids or []) if i]
        if not ids:
            return 0
        # Строку засчитываем и по lead_id, и по номеру: прошлые удаления могли
        # обнулить FK (ON DELETE SET NULL), и такая строка иначе выпала бы из счёта.
        cursor.execute(
            """
            UPDATE tez_leads l
            SET upload_count = GREATEST(agg.cnt, 1),
                first_batch_id = agg.first_batch_id,
                last_batch_id = agg.last_batch_id,
                updated_at = CURRENT_TIMESTAMP
            FROM (
                SELECT l2.id AS lead_id,
                       COUNT(*) AS cnt,
                       (ARRAY_AGG(r.batch_id ORDER BY b.created_at, r.row_number))[1] AS first_batch_id,
                       (ARRAY_AGG(r.batch_id ORDER BY b.created_at DESC, r.row_number DESC))[1] AS last_batch_id
                FROM tez_leads l2
                JOIN tez_lead_batch_rows r
                  ON r.lead_id = l2.id
                  OR (r.phone_norm IS NOT NULL AND r.phone_norm = l2.phone_norm)
                JOIN tez_lead_batches b
                  ON b.id = r.batch_id
                 AND b.deleted_at IS NULL
                 AND b.year = l2.year AND b.month = l2.month
                WHERE l2.id = ANY(%s::uuid[])
                GROUP BY l2.id
            ) agg
            WHERE l.id = agg.lead_id
            """,
            (ids,)
        )
        return cursor.rowcount

    def _lock_tez_lead_batch_for_change_tx(self, cursor, batch_id):
        """Находит batch и берёт блокировки в едином для всех операций порядке."""
        cursor.execute(
            "SELECT year, month FROM tez_lead_batches WHERE id = %s",
            (batch_id,),
        )
        preview = cursor.fetchone()
        if not preview:
            return None
        year, month = int(preview[0]), int(preview[1])
        self._lock_tez_lead_period_tx(cursor, year, month)
        cursor.execute(
            """
            SELECT id, department_id, year, month, deleted_at
            FROM tez_lead_batches
            WHERE id = %s
            FOR UPDATE
            """,
            (batch_id,),
        )
        return cursor.fetchone()

    @staticmethod
    def _tez_actor_name_tx(cursor, actor_id):
        if actor_id is None:
            return ''
        cursor.execute("SELECT name FROM users WHERE id = %s", (int(actor_id),))
        row = cursor.fetchone()
        return str(row[0] or '')[:255] if row else ''

    def delete_tez_lead_batch(self, batch_id, actor_id, reason=''):
        """Транзакционно исключает загрузку из базы и сохраняет полный снимок.

        Снимок делается для *всех* затронутых лидов, включая общие с другими
        загрузками. Это важно для порядка A(delete) -> B(delete) -> A(restore):
        восстановление A не зависит от того, какая загрузка последней удалила
        каноническую строку лида.
        """
        clean_reason = str(reason or '').strip()[:2000]
        with self._get_cursor() as cursor:
            batch = self._lock_tez_lead_batch_for_change_tx(cursor, batch_id)
            if not batch:
                return None
            if batch[4] is not None:
                return {'already_deleted': True}
            year, month = int(batch[2]), int(batch[3])

            cursor.execute(
                """
                SELECT DISTINCT l.id
                FROM tez_lead_batch_rows r
                JOIN tez_leads l
                  ON l.year = %s
                 AND l.month = %s
                 AND (
                        l.id = r.lead_id
                        OR (r.phone_norm IS NOT NULL AND l.phone_norm = r.phone_norm)
                     )
                WHERE r.batch_id = %s
                ORDER BY l.id
                """,
                (year, month, batch_id),
            )
            touched = [row[0] for row in cursor.fetchall()]

            # Защищаем снимок от параллельного пересчёта статусов/успешек.
            if touched:
                cursor.execute(
                    "SELECT id FROM tez_leads WHERE id = ANY(%s::uuid[]) ORDER BY id FOR UPDATE",
                    (touched,),
                )
                touched = [row[0] for row in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT id
                    FROM tez_lead_successes
                    WHERE lead_id = ANY(%s::uuid[])
                    ORDER BY id
                    FOR UPDATE
                    """,
                    (touched,),
                )
                cursor.fetchall()

            actor_name = self._tez_actor_name_tx(cursor, actor_id)
            cursor.execute(
                """
                INSERT INTO tez_lead_batch_events
                    (batch_id, action, actor_id, actor_name, reason)
                VALUES (%s, 'delete', %s, %s, %s)
                RETURNING id
                """,
                (
                    batch_id,
                    int(actor_id) if actor_id is not None else None,
                    actor_name,
                    clean_reason,
                ),
            )
            event_id = cursor.fetchone()[0]

            cursor.execute(
                """
                UPDATE tez_lead_batches
                SET deleted_at = CURRENT_TIMESTAMP,
                    deleted_by = %s,
                    delete_reason = %s,
                    restored_at = NULL,
                    restored_by = NULL
                WHERE id = %s
                """,
                (
                    int(actor_id) if actor_id is not None else None,
                    clean_reason,
                    batch_id,
                ),
            )

            doomed = []
            if touched:
                cursor.execute(
                    """
                    SELECT l.id
                    FROM tez_leads l
                    WHERE l.id = ANY(%s::uuid[])
                      AND NOT EXISTS (
                          SELECT 1
                          FROM tez_lead_batch_rows r
                          JOIN tez_lead_batches b ON b.id = r.batch_id
                          WHERE b.deleted_at IS NULL
                            AND b.year = l.year
                            AND b.month = l.month
                            AND (
                                   r.lead_id = l.id
                                   OR (r.phone_norm IS NOT NULL AND r.phone_norm = l.phone_norm)
                                )
                      )
                    ORDER BY l.id
                    """,
                    (touched,),
                )
                doomed = [row[0] for row in cursor.fetchall()]

                cursor.execute(
                    """
                    INSERT INTO tez_lead_archived_leads
                        (event_id, lead_id, year, month, phone_norm, full_name,
                         first_batch_id, last_batch_id, upload_count, status, status_rule,
                         month_first_order_at, prev_month_first_order_at,
                         first_order_checked_at, calls_synced_at,
                         lead_created_at, lead_updated_at)
                    SELECT %s, l.id, l.year, l.month, l.phone_norm, l.full_name,
                           l.first_batch_id, l.last_batch_id, l.upload_count,
                           l.status, l.status_rule, l.month_first_order_at,
                           l.prev_month_first_order_at, l.first_order_checked_at,
                           l.calls_synced_at, l.created_at, l.updated_at
                    FROM tez_leads l
                    WHERE l.id = ANY(%s::uuid[])
                    ON CONFLICT DO NOTHING
                    """,
                    (event_id, touched),
                )
                cursor.execute(
                    """
                    INSERT INTO tez_lead_archived_successes
                        (event_id, success_id, lead_id, phone_norm, operator_id,
                         operator_name, call_general_id, call_at, first_order_at,
                         success_date, year, month, rule_code, is_late,
                         success_created_at, success_updated_at)
                    SELECT %s, s.id, s.lead_id, s.phone_norm, s.operator_id,
                           s.operator_name, s.call_general_id, s.call_at,
                           s.first_order_at, s.success_date, s.year, s.month,
                           s.rule_code, s.is_late, s.created_at, s.updated_at
                    FROM tez_lead_successes s
                    WHERE s.lead_id = ANY(%s::uuid[])
                    ON CONFLICT DO NOTHING
                    """,
                    (event_id, touched),
                )

            successes_removed = 0
            if doomed:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM tez_lead_archived_successes
                    WHERE event_id = %s AND lead_id = ANY(%s::uuid[])
                    """,
                    (event_id, doomed),
                )
                successes_removed = int(cursor.fetchone()[0] or 0)
                cursor.execute("DELETE FROM tez_leads WHERE id = ANY(%s::uuid[])", (doomed,))

            doomed_set = set(doomed)
            kept = [lead_id for lead_id in touched if lead_id not in doomed_set]
            self._tez_resync_leads_from_rows_tx(cursor, kept)

            counts = {
                'batch_id': str(batch_id),
                'event_id': str(event_id),
                'leads_removed': len(doomed),
                'leads_kept': len(kept),
                'successes_removed': successes_removed,
            }
            cursor.execute(
                """
                UPDATE tez_lead_batch_events
                SET leads_removed = %s,
                    leads_kept = %s,
                    successes_removed = %s
                WHERE id = %s
                """,
                (
                    counts['leads_removed'],
                    counts['leads_kept'],
                    counts['successes_removed'],
                    event_id,
                ),
            )
        return counts

    def restore_tez_lead_batch(self, batch_id, actor_id, reason=''):
        """Безопасно возвращает загрузку, объединяя архив с текущим лидом по номеру."""
        clean_reason = str(reason or '').strip()[:2000]
        with self._get_cursor() as cursor:
            batch = self._lock_tez_lead_batch_for_change_tx(cursor, batch_id)
            if not batch:
                return None
            if batch[4] is None:
                return {'not_deleted': True}
            year, month = int(batch[2]), int(batch[3])

            cursor.execute(
                """
                SELECT id
                FROM tez_lead_batch_events
                WHERE batch_id = %s AND action = 'delete'
                ORDER BY event_seq DESC
                LIMIT 1
                """,
                (batch_id,),
            )
            source = cursor.fetchone()
            if not source:
                raise RuntimeError('Deleted TEZ lead batch has no delete audit event')
            delete_event_id = source[0]

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM tez_lead_archived_leads
                WHERE event_id = %s AND restored_at IS NULL
                """,
                (delete_event_id,),
            )
            archived_total = int(cursor.fetchone()[0] or 0)
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM tez_lead_archived_successes
                WHERE event_id = %s AND restored_at IS NULL
                """,
                (delete_event_id,),
            )
            archived_success_total = int(cursor.fetchone()[0] or 0)

            # Водитель нужен FK лида. Берём и архив, и исходные строки batch:
            # вторая ветка чинит данные ранней версии механизма отката.
            cursor.execute(
                """
                INSERT INTO tez_drivers (phone_norm)
                SELECT phone_norm
                FROM (
                    SELECT a.phone_norm
                    FROM tez_lead_archived_leads a
                    WHERE a.event_id = %s AND a.restored_at IS NULL
                    UNION
                    SELECT r.phone_norm
                    FROM tez_lead_batch_rows r
                    WHERE r.batch_id = %s AND r.phone_norm IS NOT NULL
                ) phones
                ON CONFLICT (phone_norm) DO NOTHING
                """,
                (delete_event_id, batch_id),
            )

            # Один телефон мог попасть в несколько загрузок, которые затем были
            # удалены в разном порядке. Восстанавливаем не снимок именно этого
            # batch, а последнее известное состояние логического лида во всём
            # периоде. restored_at намеренно не фильтруем: архив остаётся журналом
            # истины и после предыдущего отката.
            cursor.execute(
                """
                WITH target_phones AS (
                    SELECT DISTINCT phone_norm
                    FROM tez_lead_batch_rows
                    WHERE batch_id = %s AND phone_norm IS NOT NULL
                ),
                latest AS (
                    SELECT DISTINCT ON (a.year, a.month, a.phone_norm)
                           a.*, e.event_seq AS snapshot_event_seq
                    FROM tez_lead_archived_leads a
                    JOIN tez_lead_batch_events e
                      ON e.id = a.event_id
                     AND e.action = 'delete'
                    JOIN target_phones t ON t.phone_norm = a.phone_norm
                    WHERE a.year = %s AND a.month = %s
                    ORDER BY a.year, a.month, a.phone_norm,
                             e.event_seq DESC, a.id DESC
                )
                INSERT INTO tez_leads
                    (id, year, month, phone_norm, full_name,
                     first_batch_id, last_batch_id, upload_count,
                     status, status_rule, month_first_order_at,
                     prev_month_first_order_at, first_order_checked_at,
                     calls_synced_at, archive_state_event_seq,
                     created_at, updated_at)
                SELECT x.lead_id, x.year, x.month, x.phone_norm, x.full_name,
                       CASE WHEN fb.id IS NOT NULL THEN x.first_batch_id END,
                       CASE WHEN lb.id IS NOT NULL THEN x.last_batch_id END,
                       x.upload_count, x.status, x.status_rule,
                       x.month_first_order_at, x.prev_month_first_order_at,
                       x.first_order_checked_at, x.calls_synced_at,
                       x.snapshot_event_seq,
                       COALESCE(x.lead_created_at, CURRENT_TIMESTAMP),
                       CURRENT_TIMESTAMP
                FROM latest x
                LEFT JOIN tez_lead_batches fb ON fb.id = x.first_batch_id
                LEFT JOIN tez_lead_batches lb ON lb.id = x.last_batch_id
                ON CONFLICT (year, month, phone_norm) DO NOTHING
                RETURNING id
                """,
                (batch_id, year, month),
            )
            archive_recreated_ids = [row[0] for row in cursor.fetchall()]

            # Fallback гарантирует: каждая валидная строка восстановленного batch
            # снова имеет канонический лид, даже если архив был создан ранней
            # дефектной версией и не содержал общий лид.
            cursor.execute(
                """
                INSERT INTO tez_leads
                    (year, month, phone_norm, full_name,
                     first_batch_id, last_batch_id, upload_count)
                SELECT %s, %s, r.phone_norm,
                       COALESCE(MAX(NULLIF(BTRIM(r.raw_full_name), '')), ''),
                       %s, %s, 1
                FROM tez_lead_batch_rows r
                WHERE r.batch_id = %s AND r.phone_norm IS NOT NULL
                GROUP BY r.phone_norm
                ON CONFLICT (year, month, phone_norm) DO NOTHING
                RETURNING id
                """,
                (year, month, batch_id, batch_id, batch_id),
            )
            fallback_recreated_ids = [row[0] for row in cursor.fetchall()]

            # Текущие непустые поля считаем свежее архивных, но архив может
            # заполнить пробелы. Это обновление также инвалидирует outcome,
            # рассчитанный параллельно до начала restore.
            cursor.execute(
                """
                WITH target_phones AS (
                    SELECT DISTINCT phone_norm
                    FROM tez_lead_batch_rows
                    WHERE batch_id = %s AND phone_norm IS NOT NULL
                ),
                latest AS (
                    SELECT DISTINCT ON (a.year, a.month, a.phone_norm)
                           a.*, e.event_seq AS snapshot_event_seq
                    FROM tez_lead_archived_leads a
                    JOIN tez_lead_batch_events e
                      ON e.id = a.event_id
                     AND e.action = 'delete'
                    JOIN target_phones t ON t.phone_norm = a.phone_norm
                    WHERE a.year = %s AND a.month = %s
                    ORDER BY a.year, a.month, a.phone_norm,
                             e.event_seq DESC, a.id DESC
                )
                UPDATE tez_leads l
                SET full_name = CASE
                        WHEN BTRIM(COALESCE(l.full_name, '')) = '' THEN x.full_name
                        ELSE l.full_name
                    END,
                    month_first_order_at =
                        COALESCE(l.month_first_order_at, x.month_first_order_at),
                    prev_month_first_order_at =
                        COALESCE(l.prev_month_first_order_at, x.prev_month_first_order_at),
                    first_order_checked_at =
                        GREATEST(l.first_order_checked_at, x.first_order_checked_at),
                    calls_synced_at = GREATEST(l.calls_synced_at, x.calls_synced_at),
                    updated_at = CURRENT_TIMESTAMP
                FROM latest x
                WHERE l.year = x.year
                  AND l.month = x.month
                  AND l.phone_norm = x.phone_norm
                """,
                (batch_id, year, month),
            )

            # NULL + new — ещё не вычисленное состояние новой живой загрузки.
            # 0 — результат реального recompute: архив его не перезаписывает.
            # Положительное значение — применённый архив; более новый event_seq
            # может заменить более старый независимо от порядка restore A/B.
            cursor.execute(
                """
                WITH target_phones AS (
                    SELECT DISTINCT phone_norm
                    FROM tez_lead_batch_rows
                    WHERE batch_id = %s AND phone_norm IS NOT NULL
                ),
                latest AS (
                    SELECT DISTINCT ON (a.year, a.month, a.phone_norm)
                           a.*, e.event_seq AS snapshot_event_seq
                    FROM tez_lead_archived_leads a
                    JOIN tez_lead_batch_events e
                      ON e.id = a.event_id
                     AND e.action = 'delete'
                    JOIN target_phones t ON t.phone_norm = a.phone_norm
                    WHERE a.year = %s AND a.month = %s
                    ORDER BY a.year, a.month, a.phone_norm,
                             e.event_seq DESC, a.id DESC
                )
                UPDATE tez_leads l
                SET status = x.status,
                    status_rule = x.status_rule,
                    archive_state_event_seq = x.snapshot_event_seq,
                    updated_at = CURRENT_TIMESTAMP
                FROM latest x
                WHERE l.year = x.year
                  AND l.month = x.month
                  AND l.phone_norm = x.phone_norm
                  AND (
                        (l.archive_state_event_seq IS NULL AND l.status = 'new')
                        OR (
                            l.archive_state_event_seq > 0
                            AND l.archive_state_event_seq < x.snapshot_event_seq
                        )
                      )
                RETURNING l.id
                """,
                (batch_id, year, month),
            )
            archive_upgraded_ids = [row[0] for row in cursor.fetchall()]
            snapshot_applied_ids = list(dict.fromkeys(
                archive_recreated_ids + archive_upgraded_ids
            ))

            cursor.execute(
                """
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE l.id <> a.lead_id)
                FROM tez_lead_archived_leads a
                JOIN tez_leads l
                  ON l.year = a.year
                 AND l.month = a.month
                 AND l.phone_norm = a.phone_norm
                WHERE a.event_id = %s AND a.restored_at IS NULL
                """,
                (delete_event_id,),
            )
            mapped_total, merged = cursor.fetchone()
            mapped_total, merged = int(mapped_total or 0), int(merged or 0)
            if mapped_total != archived_total:
                raise RuntimeError('TEZ lead archive could not be mapped completely')

            successes_restored = 0
            if snapshot_applied_ids:
                # Архивный success допустим только из того же события, что и
                # latest lead snapshot. Иначе старый success мог бы воскреснуть
                # поверх более нового not_counted/in_progress.
                cursor.execute(
                    """
                    WITH target_phones AS (
                        SELECT DISTINCT phone_norm
                        FROM tez_lead_batch_rows
                        WHERE batch_id = %s AND phone_norm IS NOT NULL
                    ),
                    latest AS (
                        SELECT DISTINCT ON (a.year, a.month, a.phone_norm)
                               a.*, e.event_seq AS snapshot_event_seq
                        FROM tez_lead_archived_leads a
                        JOIN tez_lead_batch_events e
                          ON e.id = a.event_id
                         AND e.action = 'delete'
                        JOIN target_phones t ON t.phone_norm = a.phone_norm
                        WHERE a.year = %s AND a.month = %s
                        ORDER BY a.year, a.month, a.phone_norm,
                                 e.event_seq DESC, a.id DESC
                    )
                    SELECT COUNT(*)
                    FROM latest x
                    JOIN tez_leads l
                      ON l.year = x.year
                     AND l.month = x.month
                     AND l.phone_norm = x.phone_norm
                    WHERE l.id = ANY(%s::uuid[])
                      AND x.status = 'success'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM tez_lead_archived_successes a
                          WHERE a.event_id = x.event_id
                            AND a.lead_id = x.lead_id
                      )
                    """,
                    (batch_id, year, month, snapshot_applied_ids),
                )
                if int(cursor.fetchone()[0] or 0):
                    raise RuntimeError('Latest TEZ success snapshot has no archived success')

                # Сначала убираем успех от предыдущего архивного состояния:
                # latest может оказаться как success, так и non-success.
                cursor.execute(
                    "DELETE FROM tez_lead_successes WHERE lead_id = ANY(%s::uuid[])",
                    (snapshot_applied_ids,),
                )
                cursor.execute(
                    """
                    WITH target_phones AS (
                        SELECT DISTINCT phone_norm
                        FROM tez_lead_batch_rows
                        WHERE batch_id = %s AND phone_norm IS NOT NULL
                    ),
                    latest AS (
                        SELECT DISTINCT ON (a.year, a.month, a.phone_norm)
                               a.*, e.event_seq AS snapshot_event_seq
                        FROM tez_lead_archived_leads a
                        JOIN tez_lead_batch_events e
                          ON e.id = a.event_id
                         AND e.action = 'delete'
                        JOIN target_phones t ON t.phone_norm = a.phone_norm
                        WHERE a.year = %s AND a.month = %s
                        ORDER BY a.year, a.month, a.phone_norm,
                                 e.event_seq DESC, a.id DESC
                    )
                    INSERT INTO tez_lead_successes
                        (id, lead_id, phone_norm, operator_id, operator_name,
                         call_general_id, call_at, first_order_at, success_date,
                         year, month, rule_code, is_late, created_at, updated_at)
                    SELECT a.success_id, l.id, a.phone_norm,
                           CASE WHEN u.id IS NULL THEN NULL ELSE a.operator_id END,
                           a.operator_name, a.call_general_id, a.call_at,
                           a.first_order_at, a.success_date, a.year, a.month,
                           a.rule_code, a.is_late,
                           COALESCE(a.success_created_at, CURRENT_TIMESTAMP),
                           COALESCE(
                               a.success_updated_at,
                               a.success_created_at,
                               CURRENT_TIMESTAMP
                           )
                    FROM latest x
                    JOIN tez_lead_archived_successes a
                      ON a.event_id = x.event_id
                     AND a.lead_id = x.lead_id
                    JOIN tez_leads l
                      ON l.year = x.year
                     AND l.month = x.month
                     AND l.phone_norm = x.phone_norm
                    LEFT JOIN users u ON u.id = a.operator_id
                    WHERE l.id = ANY(%s::uuid[])
                      AND x.status = 'success'
                      AND l.archive_state_event_seq = x.snapshot_event_seq
                    ON CONFLICT (lead_id) DO UPDATE SET
                        id = EXCLUDED.id,
                        phone_norm = EXCLUDED.phone_norm,
                        operator_id = EXCLUDED.operator_id,
                        operator_name = EXCLUDED.operator_name,
                        call_general_id = EXCLUDED.call_general_id,
                        call_at = EXCLUDED.call_at,
                        first_order_at = EXCLUDED.first_order_at,
                        success_date = EXCLUDED.success_date,
                        year = EXCLUDED.year,
                        month = EXCLUDED.month,
                        rule_code = EXCLUDED.rule_code,
                        is_late = EXCLUDED.is_late,
                        created_at = EXCLUDED.created_at,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (batch_id, year, month, snapshot_applied_ids),
                )
                successes_restored = int(cursor.rowcount or 0)

                cursor.execute(
                    """
                    WITH target_phones AS (
                        SELECT DISTINCT phone_norm
                        FROM tez_lead_batch_rows
                        WHERE batch_id = %s AND phone_norm IS NOT NULL
                    ),
                    latest AS (
                        SELECT DISTINCT ON (a.year, a.month, a.phone_norm)
                               a.*, e.event_seq AS snapshot_event_seq
                        FROM tez_lead_archived_leads a
                        JOIN tez_lead_batch_events e
                          ON e.id = a.event_id
                         AND e.action = 'delete'
                        JOIN target_phones t ON t.phone_norm = a.phone_norm
                        WHERE a.year = %s AND a.month = %s
                        ORDER BY a.year, a.month, a.phone_norm,
                                 e.event_seq DESC, a.id DESC
                    )
                    SELECT COUNT(*)
                    FROM latest x
                    JOIN tez_leads l
                      ON l.year = x.year
                     AND l.month = x.month
                     AND l.phone_norm = x.phone_norm
                    WHERE l.id = ANY(%s::uuid[])
                      AND (
                            (
                                x.status = 'success'
                                AND NOT EXISTS (
                                    SELECT 1 FROM tez_lead_successes s
                                    WHERE s.lead_id = l.id
                                )
                            )
                            OR (
                                x.status <> 'success'
                                AND EXISTS (
                                    SELECT 1 FROM tez_lead_successes s
                                    WHERE s.lead_id = l.id
                                )
                            )
                          )
                    """,
                    (batch_id, year, month, snapshot_applied_ids),
                )
                if int(cursor.fetchone()[0] or 0):
                    raise RuntimeError('Restored TEZ lead and success states disagree')

            cursor.execute(
                """
                UPDATE tez_lead_archived_leads a
                SET restored_at = CURRENT_TIMESTAMP,
                    restored_lead_id = l.id
                FROM tez_leads l
                WHERE a.event_id = %s
                  AND a.restored_at IS NULL
                  AND l.year = a.year
                  AND l.month = a.month
                  AND l.phone_norm = a.phone_norm
                """,
                (delete_event_id,),
            )
            if int(cursor.rowcount or 0) != archived_total:
                raise RuntimeError('TEZ lead archive restore marker is incomplete')

            cursor.execute(
                """
                WITH target_phones AS (
                    SELECT DISTINCT phone_norm
                    FROM tez_lead_batch_rows
                    WHERE batch_id = %s AND phone_norm IS NOT NULL
                ),
                latest AS (
                    SELECT DISTINCT ON (a.year, a.month, a.phone_norm)
                           a.*, e.event_seq AS snapshot_event_seq
                    FROM tez_lead_archived_leads a
                    JOIN tez_lead_batch_events e
                      ON e.id = a.event_id
                     AND e.action = 'delete'
                    JOIN target_phones t ON t.phone_norm = a.phone_norm
                    WHERE a.year = %s AND a.month = %s
                    ORDER BY a.year, a.month, a.phone_norm,
                             e.event_seq DESC, a.id DESC
                )
                UPDATE tez_lead_archived_successes a
                SET restored_at = CURRENT_TIMESTAMP,
                    restored_lead_id = l.id,
                    restored_success_id = CASE
                        WHEN x.event_id = a.event_id
                         AND l.archive_state_event_seq = x.snapshot_event_seq
                        THEN s.id
                        ELSE NULL
                    END
                FROM tez_leads l
                LEFT JOIN tez_lead_successes s ON s.lead_id = l.id
                LEFT JOIN latest x
                  ON x.year = l.year
                 AND x.month = l.month
                 AND x.phone_norm = l.phone_norm
                WHERE a.event_id = %s
                  AND a.restored_at IS NULL
                  AND l.year = a.year
                  AND l.month = a.month
                  AND l.phone_norm = a.phone_norm
                """,
                (batch_id, year, month, delete_event_id),
            )
            if int(cursor.rowcount or 0) != archived_success_total:
                raise RuntimeError('TEZ success archive could not be restored completely')

            cursor.execute(
                """
                UPDATE tez_lead_batches
                SET deleted_at = NULL,
                    deleted_by = NULL,
                    delete_reason = NULL,
                    restored_at = CURRENT_TIMESTAMP,
                    restored_by = %s
                WHERE id = %s
                """,
                (int(actor_id) if actor_id is not None else None, batch_id),
            )
            cursor.execute(
                """
                UPDATE tez_lead_batch_rows r
                SET lead_id = l.id
                FROM tez_leads l
                WHERE r.batch_id = %s
                  AND r.phone_norm IS NOT NULL
                  AND l.year = %s
                  AND l.month = %s
                  AND l.phone_norm = r.phone_norm
                  AND (r.lead_id IS NULL OR r.lead_id <> l.id)
                """,
                (batch_id, year, month),
            )
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM tez_lead_batch_rows
                WHERE batch_id = %s
                  AND phone_norm IS NOT NULL
                  AND lead_id IS NULL
                """,
                (batch_id,),
            )
            if int(cursor.fetchone()[0] or 0):
                raise RuntimeError('Restored TEZ batch still has rows without a lead')

            cursor.execute(
                """
                SELECT DISTINCT lead_id
                FROM tez_lead_batch_rows
                WHERE batch_id = %s AND lead_id IS NOT NULL
                """,
                (batch_id,),
            )
            touched = [row[0] for row in cursor.fetchall()]
            self._tez_resync_leads_from_rows_tx(cursor, touched)

            leads_recreated = len(archive_recreated_ids) + len(fallback_recreated_ids)
            actor_name = self._tez_actor_name_tx(cursor, actor_id)
            cursor.execute(
                """
                INSERT INTO tez_lead_batch_events
                    (batch_id, action, source_event_id, actor_id, actor_name,
                     reason, leads_kept, successes_removed, leads_merged)
                VALUES (%s, 'restore', %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    batch_id,
                    delete_event_id,
                    int(actor_id) if actor_id is not None else None,
                    actor_name,
                    clean_reason,
                    leads_recreated,
                    successes_restored,
                    merged,
                ),
            )
            event_id = cursor.fetchone()[0]

        return {
            'batch_id': str(batch_id),
            'event_id': str(event_id),
            'leads_restored': leads_recreated,
            'successes_restored': successes_restored,
            'leads_merged': merged,
        }

    def get_tez_phones_pending_first_order(self, year, month, batch_id=None, limit=None):
        """Номера месяца, у которых заказ в отчётном месяце ещё не найден.

        Именно они идут в TEZ APP. Как только дата в месяце найдена, номер
        выпадает отсюда: первый заказ внутри месяца больше не изменится.
        """
        params = [int(year), int(month)]
        sql = """
            SELECT DISTINCT l.phone_norm
            FROM tez_leads l
            WHERE l.year = %s AND l.month = %s AND l.month_first_order_at IS NULL
        """
        if batch_id:
            sql += " AND (l.first_batch_id = %s OR l.last_batch_id = %s)"
            params.extend([batch_id, batch_id])
        sql += " ORDER BY l.phone_norm"
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self._get_cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return [r[0] for r in cursor.fetchall()]

    def save_tez_first_orders(self, year, month, first_orders):
        """Сохраняет оконные ответы TEZ APP на лидах отчётного месяца.

        first_orders — dict phone_norm -> {'month': datetime|None, 'prev': datetime|None}.
        Проверенные, но ещё не выехавшие номера тоже помечаются (checked_at), иначе
        их не отличить от никогда не проверявшихся.

        Пишем одним запросом: ответ TEZ APP приходит сразу по тысячам номеров
        (3 259 за август, вдвое больше при обработке лидов прошлого месяца), и
        построчный UPDATE превращал бы это в столько же round-trip'ов.
        """
        rows = []
        found = 0
        for phone_norm, dates in (first_orders or {}).items():
            if not phone_norm:
                continue
            month_at = (dates or {}).get('month')
            prev_at = (dates or {}).get('prev')
            last_before = (dates or {}).get('last_before')
            rows.append((phone_norm, int(year), int(month), month_at, prev_at, last_before))
            if month_at is not None:
                found += 1
        if not rows:
            return 0

        with self._get_cursor() as cursor:
            self._lock_tez_lead_period_tx(cursor, year, month)
            execute_values(
                cursor,
                """
                UPDATE tez_leads l SET
                    -- Все три даты для пары (год, месяц) неизменны: первый заказ
                    -- внутри месяца, первый заказ предыдущего и последний заказ
                    -- до начала месяца задним числом не меняются. Поэтому пишем
                    -- только пустое. Без COALESCE у prev была прямая потеря
                    -- данных: 11.08.2026 TEZ APP убрал это поле из ответа, и
                    -- каждая ночная проверка обнуляла бы уже сохранённую дату —
                    -- вместе с ней у закрытого июля исчезла бы причина статуса
                    -- «уже работающий».
                    month_first_order_at = COALESCE(l.month_first_order_at, v.month_at),
                    prev_month_first_order_at = COALESCE(l.prev_month_first_order_at, v.prev_at),
                    last_order_before_at = COALESCE(l.last_order_before_at, v.last_before),
                    first_order_checked_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                FROM (VALUES %s) AS v(phone_norm, lead_year, lead_month, month_at, prev_at, last_before)
                WHERE l.year = v.lead_year
                  AND l.month = v.lead_month
                  AND l.phone_norm = v.phone_norm
                """,
                rows,
                template="(%s, %s::int, %s::int, %s::timestamptz, %s::timestamptz, %s::timestamptz)",
                page_size=500,
            )
        return found

    def get_tez_carry_phones_pending_first_order(self, year, month):
        """Номера ПЕРЕНЕСЁННЫХ лидов, по которым ещё не знаем поездку в этом месяце.

        Перенос: лид прошлого месяца, не ставший успешкой, дорабатывается ещё
        один месяц. Спрашиваем по нему TEZ APP за ЦЕЛЕВОЙ месяц, а ответ кладём
        в carry_* колонки строки месяца базы.

        Уже выехавшие выпадают отсюда навсегда: первый заказ внутри месяца не
        изменится. Отбор ложится на idx_tez_leads_pending_order_check.
        """
        year, month = int(year), int(month)
        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT l.phone_norm
                FROM tez_leads l
                WHERE l.year = %s AND l.month = %s
                  AND l.status <> 'success'
                  AND l.carry_first_order_at IS NULL
                ORDER BY l.phone_norm
                """,
                (prev_year, prev_month)
            )
            return [r[0] for r in cursor.fetchall()]

    def save_tez_carry_first_orders(self, year, month, first_orders):
        """Ответ TEZ APP за ЦЕЛЕВОЙ месяц — на строки лидов месяца БАЗЫ.

        year/month — целевой месяц (тот, за который спрашивали). Пишем в
        carry_* колонки лидов предыдущего месяца: их собственные даты описывают
        их собственный месяц, и подмена сломала бы воронку месяца базы.
        """
        year, month = int(year), int(month)
        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        rows = []
        found = 0
        for phone_norm, dates in (first_orders or {}).items():
            if not phone_norm:
                continue
            month_at = (dates or {}).get('month')
            last_before = (dates or {}).get('last_before')
            rows.append((phone_norm, prev_year, prev_month, month_at, last_before))
            if month_at is not None:
                found += 1
        if not rows:
            return 0

        with self._get_cursor() as cursor:
            self._lock_tez_lead_period_tx(cursor, prev_year, prev_month)
            execute_values(
                cursor,
                """
                UPDATE tez_leads l SET
                    carry_first_order_at = COALESCE(l.carry_first_order_at, v.month_at),
                    carry_last_order_before_at = COALESCE(l.carry_last_order_before_at, v.last_before),
                    carry_checked_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                FROM (VALUES %s) AS v(phone_norm, lead_year, lead_month, month_at, last_before)
                WHERE l.year = v.lead_year
                  AND l.month = v.lead_month
                  AND l.phone_norm = v.phone_norm
                  AND l.status <> 'success'
                """,
                rows,
                template="(%s, %s::int, %s::int, %s::timestamptz, %s::timestamptz)",
                page_size=500,
            )
        return found

    def get_tez_phones_needing_calls(self, year, month):
        """Номера, по которым пора поднять историю звонков из Binotel.

        Это те, у кого заказ в месяце есть и по кому звонки ещё не поднимали.
        Гейт именно по `calls_synced_at`, а НЕ по статусу: лид со старым статусом
        (например already_working от прежней логики) иначе никогда не получил бы
        звонки и навсегда остался бы без успешки. Их единицы за ночь — отсюда
        один запрос в Binotel вместо зеркала всех звонков компании.

        Условия `prev_month_first_order_at IS NULL` здесь больше нет. Оно
        отсекало ровно тех, ради кого сделано правило разрыва: по «уснувшему»
        водителю история звонков не поднималась никогда, поэтому звонок,
        которым его вернули, для расчёта не существовал. Замер на проде
        11.08.2026: из 49 июльских лидов с разрывом >= 30 дней квалифицирующий
        звонок был известен ровно у одного. Цена снятия — ~200 номеров в месяц,
        то есть 8 пачек history-by-external-number, 0–1 запрос за ночь.
        """
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT l.phone_norm
                FROM tez_leads l
                WHERE l.year = %s AND l.month = %s
                  AND l.month_first_order_at IS NOT NULL
                  AND l.calls_synced_at IS NULL
                ORDER BY l.phone_norm
                """,
                (int(year), int(month))
            )
            return [r[0] for r in cursor.fetchall()]

    def mark_tez_leads_calls_synced(self, year, month, phones):
        """Отмечает, что звонки по этим номерам уже подняты (одной докачки хватает:
        дата поездки известна, а звонки до неё в прошлом и не изменятся)."""
        nums = [p for p in (phones or []) if p]
        if not nums:
            return 0
        with self._get_cursor() as cursor:
            self._lock_tez_lead_period_tx(cursor, year, month)
            cursor.execute(
                """
                UPDATE tez_leads
                SET calls_synced_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE year = %s AND month = %s AND phone_norm = ANY(%s)
                """,
                (int(year), int(month), nums)
            )
            return cursor.rowcount

    def get_tez_call_internal_number_owners(self):
        """Внутренний номер -> имя сотрудника, выведенное из наших же звонков.

        Запасной ключ привязки. Binotel перестаёт отдавать employeeData по
        старым звонкам уволенного сотрудника (его удаляют на их стороне), и
        звонок становится «ничьим»: матчинг у нас идёт по имени. Внутренний
        номер при этом остаётся, а кто с него звонил — видно по тем звонкам,
        где имя ещё было.

        Отдаём номер только при ОДНОЗНАЧНОМ владельце: sip со временем передают
        другому оператору, и угадывать в таком случае нельзя (на проде на
        2026-08-05 у всех 23 номеров владелец ровно один).
        """
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT internal_number, MIN(employee_name)
                FROM tez_lead_calls
                WHERE internal_number <> '' AND employee_name <> ''
                GROUP BY internal_number
                HAVING COUNT(DISTINCT employee_name) = 1
                """
            )
            return {r[0]: r[1] for r in cursor.fetchall()}

    def get_tez_calls_without_operator(self, window_from, window_to):
        """Звонки окна, оставшиеся без оператора, но с внутренним номером.

        Кандидаты на восстановление привязки по номеру. Входящие и короткие
        тоже берём: они не дают успешку, но участвуют в счётчике попыток.
        """
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT general_call_id, internal_number, call_type, billsec, started_at
                FROM tez_lead_calls
                WHERE operator_id IS NULL
                  AND internal_number <> ''
                  AND employee_name = ''
                  AND started_at >= %s AND started_at < %s
                """,
                (window_from, window_to)
            )
            return [{'general_call_id': r[0], 'internal_number': r[1],
                     'call_type': r[2], 'billsec': r[3], 'started_at': r[4]}
                    for r in cursor.fetchall()]

    def restore_tez_call_operators(self, rows):
        """Проставляет восстановленную привязку звонкам (ключ — generalCallID).

        rows — список dict'ов general_call_id / employee_name / operator_id /
        is_qualifying. Имя пишем тоже: иначе в разборе спора у звонка так и
        останется пустой сотрудник, хотя успешка ему засчитана.
        """
        values = [
            (str(r['general_call_id'])[:32], str(r.get('employee_name') or '')[:255],
             int(r['operator_id']), bool(r.get('is_qualifying')))
            for r in (rows or []) if r.get('operator_id')
        ]
        if not values:
            return 0
        with self._get_cursor() as cursor:
            # RETURNING + fetch=True, а не cursor.rowcount: execute_values шлёт
            # данные страницами, и rowcount после вызова знает только последнюю
            # из них (1056 восстановленных звонков отчитались как 56).
            restored = execute_values(
                cursor,
                """
                UPDATE tez_lead_calls c SET
                    employee_name = v.employee_name,
                    operator_id = v.operator_id,
                    is_qualifying = v.is_qualifying
                FROM (VALUES %s) AS v(general_call_id, employee_name, operator_id, is_qualifying)
                WHERE c.general_call_id = v.general_call_id
                RETURNING 1
                """,
                values,
                template="(%s, %s, %s::int, %s::boolean)",
                page_size=500,
                fetch=True,
            )
            return len(restored or [])

    def get_tez_call_synced_days(self, start_day, end_day):
        """Дни, за которые зеркало звонков уже перекачано целиком (set из date)."""
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT day FROM tez_call_sync_days WHERE day >= %s AND day <= %s",
                (start_day, end_day)
            )
            return {r[0] for r in cursor.fetchall()}

    def mark_tez_call_day_synced(self, day, calls):
        """Отмечает день как перекачанный (повторная выкачка обновляет счётчик)."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tez_call_sync_days (day, calls, synced_at)
                VALUES (%s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (day) DO UPDATE SET
                    calls = EXCLUDED.calls,
                    synced_at = EXCLUDED.synced_at
                """,
                (day, int(calls or 0))
            )
            return cursor.rowcount

    def save_tez_lead_calls(self, calls):
        """Upsert звонков Binotel (ключ — generalCallID).

        Пишем пачкой: зеркало приносит по 500–1200 звонков за день, и построчный
        INSERT превратил бы один день в тысячу round-trip'ов к базе.
        """
        rows = []
        for call in calls or []:
            rows.append((
                str(call.get('general_call_id'))[:32],
                call.get('phone_norm'),
                call.get('started_at'),
                int(call.get('call_type', -1)),
                int(call.get('billsec') or 0),
                int(call.get('waitsec') or 0),
                str(call.get('disposition') or '')[:32],
                str(call.get('internal_number') or '')[:64],
                str(call.get('employee_name') or '')[:255],
                str(call.get('employee_email') or '')[:255],
                int(call['operator_id']) if call.get('operator_id') else None,
                bool(call.get('is_qualifying')),
            ))
        if not rows:
            return 0
        # Дубли generalCallID внутри одной пачки уронили бы ON CONFLICT
        # («cannot affect row a second time»), а Binotel отдаёт один и тот же
        # звонок в разных ответах — оставляем последнее вхождение.
        deduped = {row[0]: row for row in rows}
        with self._get_cursor() as cursor:
            execute_values(
                cursor,
                """
                INSERT INTO tez_lead_calls
                    (general_call_id, phone_norm, started_at, call_type, billsec, waitsec,
                     disposition, internal_number, employee_name, employee_email,
                     operator_id, is_qualifying)
                VALUES %s
                ON CONFLICT (general_call_id) DO UPDATE SET
                    -- Кто говорил, решает ПЕРВАЯ выкачка, повторная это не
                    -- переписывает. Binotel отдаёт имя ТЕКУЩЕГО сотрудника
                    -- линии и для старых звонков: линию передают новому
                    -- оператору переименованием, и вся её история приходит
                    -- уже под новым именем (прод 30.10.2026: 41 звонок
                    -- уволенного оператора с линии 920 за 11.08–17.09 ушёл
                    -- новичку, который числился в группе раньше, чем принял
                    -- линию). А удалённого сотрудника Binotel не отдаёт вовсе
                    -- (05.08.2026, sip 927: обнулилась привязка, и 19 успешек
                    -- июля исчезли).
                    -- Поэтому известные имя и оператор остаются как есть, а
                    -- оператора дописываем только звонку без него и только под
                    -- тем же именем — или звонку, где имени ещё не было.
                    operator_id = COALESCE(
                        tez_lead_calls.operator_id,
                        CASE WHEN tez_lead_calls.employee_name IN ('', EXCLUDED.employee_name)
                             THEN EXCLUDED.operator_id END
                    ),
                    -- Признак считается от того оператора, что остался за
                    -- звонком: исходящий и длительность от имени не зависят.
                    is_qualifying = CASE
                        WHEN EXCLUDED.operator_id IS NULL AND tez_lead_calls.operator_id IS NOT NULL
                            THEN tez_lead_calls.is_qualifying
                        WHEN tez_lead_calls.operator_id IS NULL
                             AND tez_lead_calls.employee_name NOT IN ('', EXCLUDED.employee_name)
                            THEN FALSE
                        ELSE EXCLUDED.is_qualifying
                    END,
                    employee_name = CASE
                        WHEN tez_lead_calls.employee_name <> '' THEN tez_lead_calls.employee_name
                        ELSE EXCLUDED.employee_name
                    END,
                    -- Итог разговора тоже обновляем: зеркало перекачивает
                    -- сегодня и вчера как раз потому, что звонок мог попасть в
                    -- выдачу незавершённым. Оставь мы billsec от первой
                    -- выкачки — «Дозвонились» и успешка потерялись бы молча.
                    billsec = EXCLUDED.billsec,
                    waitsec = EXCLUDED.waitsec,
                    disposition = EXCLUDED.disposition,
                    call_type = EXCLUDED.call_type,
                    fetched_at = CURRENT_TIMESTAMP
                """,
                list(deduped.values()),
                page_size=500,
            )
        return len(deduped)

    def get_tez_leads_for_recompute(self, year, month, min_billsec=None):
        """Лиды месяца вместе с первой поездкой и их квалифицирующими звонками.

        Возвращает список dict'ов, готовых для compute_lead_outcome.

        min_billsec — порог длительности разговора. Если он задан, звонки
        отбираются тем же условием, что и в правиле успешки (исходящий,
        разрезолвленный оператор ОП, billsec >= порога), то есть в Python
        приезжает ровно то, что расчёт и так использует: на июльской базе это
        6 668 строк вместо 25 766, и запрос ложится на частичный индекс
        idx_tez_lead_calls_op_attempts. Без порога поведение прежнее — все
        звонки номера за всю историю.
        """
        year, month = int(year), int(month)
        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        with self._get_cursor() as cursor:
            # Две ветви через UNION ALL, а НЕ через OR: замер на проде даёт
            # 3,6 мс Index Scan против 7,0 мс Seq Scan. Вторая ветвь — перенос:
            # лид прошлого месяца, не ставший успешкой, дорабатывается в этом.
            # «Ровно +1 месяц» тут инвариант запроса, а не флаг в данных:
            # состояния «перенесён дважды» просто нечем выразить.
            cursor.execute(
                """
                SELECT l.id, l.phone_norm, l.full_name,
                       l.month_first_order_at, l.prev_month_first_order_at,
                       l.xmin::text, l.last_order_before_at,
                       l.year, l.month, FALSE AS is_carried,
                       NULL::timestamptz AS carry_first_order_at,
                       NULL::timestamptz AS carry_last_order_before_at
                FROM tez_leads l
                WHERE l.year = %s AND l.month = %s
                UNION ALL
                SELECT l.id, l.phone_norm, l.full_name,
                       l.month_first_order_at, l.prev_month_first_order_at,
                       l.xmin::text, l.last_order_before_at,
                       l.year, l.month, TRUE AS is_carried,
                       l.carry_first_order_at, l.carry_last_order_before_at
                FROM tez_leads l
                WHERE l.year = %s AND l.month = %s
                  AND l.status <> 'success'
                """,
                (year, month, prev_year, prev_month)
            )
            leads = [{
                'id': str(r[0]),
                'phone_norm': r[1],
                'full_name': r[2] or '',
                'month_first_order_at': r[3],
                'prev_month_first_order_at': r[4],
                'version': r[5],
                'last_order_before_at': r[6],
                'lead_year': int(r[7]),
                'lead_month': int(r[8]),
                'is_carried': bool(r[9]),
                'carry_first_order_at': r[10],
                'carry_last_order_before_at': r[11],
                'calls': [],
            } for r in cursor.fetchall()]
            if not leads:
                return []

            by_phone = {}
            for lead in leads:
                by_phone.setdefault(lead['phone_norm'], []).append(lead)

            calls_sql = """
                SELECT c.phone_norm, c.general_call_id, c.started_at, c.call_type,
                       c.billsec, c.operator_id, c.employee_name
                FROM tez_lead_calls c
                WHERE c.phone_norm = ANY(%s)
            """
            calls_params = [list(by_phone.keys())]
            if min_billsec is not None:
                calls_sql += """
                  AND c.call_type = 1
                  AND c.operator_id IS NOT NULL
                  AND c.billsec >= %s
                """
                calls_params.append(int(min_billsec))
            calls_sql += " ORDER BY c.started_at"
            cursor.execute(calls_sql, tuple(calls_params))
            for r in cursor.fetchall():
                call = {
                    'general_call_id': r[1],
                    'started_at': r[2],
                    'call_type': int(r[3] if r[3] is not None else -1),
                    'billsec': int(r[4] or 0),
                    'operator_id': int(r[5]) if r[5] is not None else None,
                    'employee_name': r[6] or '',
                }
                for lead in by_phone.get(r[0], []):
                    lead['calls'].append(call)
        return leads

    def apply_tez_lead_outcomes(self, year, month, outcomes):
        """Записывает пересчитанные статусы лидов и успешки.

        outcomes — список dict'ов с ключами lead_id, phone_norm, status, rule,
        operator_id, operator_name, call_general_id, call_at, first_order_at,
        success_date. Операция идемпотентна: успешка привязана к лиду через
        UNIQUE(lead_id), а лид, переставший быть успешкой, свою запись теряет.

        Период берётся из САМОГО item'а (lead_year/lead_month), а не из
        аргументов: при переносе в прогоне участвуют лиды прошлого месяца, и
        по году-месяцу функции их строки просто не нашлись бы — UPDATE вернул
        бы ноль строк, а перенос молча не работал бы.

        `write_status=False` (перенесённый лид) означает «успешку записать, а
        статус лида не трогать»: свой статус он получил в собственном месяце, и
        пересчёт того месяца иначе стирал бы результат переноса, а этот —
        результат того. Успешки при этом удаляются ТОЛЬКО за обрабатываемый
        период: успешка, забронированная в следующем месяце, пересчёту месяца
        базы не принадлежит.

        Пишем ТРЕМЯ запросами на весь пересчёт, а не двумя на каждый лид.
        Построчный вариант делал ~14 400 round-trip'ов на июльской базе (7 195
        лидов) и держал всё это время advisory-лок периода, который делит с
        импортом базы; при обработке лидов прошлого месяца число удваивалось.
        """
        stats = {'success': 0, 'already_working': 0, 'not_counted': 0,
                 'in_progress': 0, 'new': 0, 'skipped': 0, 'carried_success': 0}
        # Ключ — канонический UUID в нижнем регистре: именно в таком виде id
        # приходит обратно из RETURNING, и по нему мы находим исходный item.
        by_id = {}
        for item in outcomes or []:
            if item.get('lead_id'):
                by_id[str(item['lead_id']).lower()] = item
        if not by_id:
            return stats

        rows = [
            (
                lead_id,
                int(item.get('lead_year') or year),
                int(item.get('lead_month') or month),
                str(item.get('status') or 'new'),
                item.get('rule'),
                item.get('lead_version'),
                bool(item.get('write_status', True)),
            )
            for lead_id, item in by_id.items()
        ]
        # Лок берём на все затронутые периоды и строго по возрастанию: иначе
        # прогон месяца и параллельный импорт/удаление базы соседнего встанут
        # в дедлок, взяв те же два лока в разном порядке.
        periods = sorted({(r[1], r[2]) for r in rows})

        with self._get_cursor() as cursor:
            for period_year, period_month in periods:
                self._lock_tez_lead_period_tx(cursor, period_year, period_month)
            # RETURNING + fetch=True, а не rowcount: execute_values шлёт данные
            # страницами, и rowcount знает только последнюю из них.
            applied = execute_values(
                cursor,
                """
                UPDATE tez_leads l SET
                    -- write_status = FALSE у перенесённого лида: его статус
                    -- принадлежит месяцу базы, и трогать его отсюда нельзя.
                    status = CASE WHEN v.write_status THEN v.status ELSE l.status END,
                    status_rule = CASE WHEN v.write_status THEN v.status_rule ELSE l.status_rule END,
                    -- Зеркальная пара для переноса: статус месяца доработки
                    -- писать больше некуда, а выбросить его нельзя — именно он
                    -- отвечает на вопрос «стал ли перенесённый лид успешкой в
                    -- этом месяце и если нет, то почему».
                    carry_status = CASE WHEN v.write_status THEN l.carry_status ELSE v.status END,
                    carry_status_rule = CASE WHEN v.write_status THEN l.carry_status_rule
                                             ELSE v.status_rule END,
                    archive_state_event_seq = CASE WHEN v.write_status THEN 0
                                                   ELSE l.archive_state_event_seq END,
                    updated_at = CURRENT_TIMESTAMP
                FROM (VALUES %s) AS v(lead_id, lead_year, lead_month, status, status_rule,
                                      version, write_status)
                WHERE l.id = v.lead_id
                  AND l.year = v.lead_year
                  AND l.month = v.lead_month
                  -- Лид мог исчезнуть или измениться между read-фазой пересчёта
                  -- и этой транзакцией: xmin меняется при любом UPDATE и при
                  -- delete+insert, даже если восстановлен тот же UUID. В отличие
                  -- от timestamp он не может совпасть из-за точности часов.
                  -- NULL версии — вызовы старого контракта (внутренние и
                  -- тестовые), они проверкой не гейтятся.
                  AND (v.version IS NULL OR l.xmin::text = v.version)
                RETURNING l.id::text
                """,
                rows,
                template="(%s::uuid, %s::int, %s::int, %s, %s, %s, %s::boolean)",
                page_size=500,
                fetch=True,
            )

            # Считаем по НАМЕРЕНИЮ расчёта, а не по тому, что вернул UPDATE:
            # у перенесённого лида в базе остался его собственный статус.
            applied_ids = {str(r[0]).lower() for r in (applied or [])}
            for lead_id in applied_ids:
                item = by_id[lead_id]
                status = str(item.get('status') or 'new')
                if item.get('write_status', True):
                    stats[status] = stats.get(status, 0) + 1
                elif status == 'success':
                    # Успешка переноса живёт в обрабатываемом месяце, а её лид —
                    # в прошлом. В статусы периода она не входит, но в деньги да.
                    stats['carried_success'] += 1
            stats['skipped'] = len(by_id) - len(applied_ids)
            if stats['skipped']:
                # Раньше несовпадение версии проглатывалось молча, и «перенос не
                # работает» выглядело как «успешек нет» без единой строки в логе.
                logging.warning(
                    'Успешки TEZ ОП %s-%02d: %s лидов пропущены при записи '
                    '(лид удалён или изменился во время пересчёта)',
                    int(year), int(month), stats['skipped'],
                )

            # Лиды, переставшие быть успешками, теряют свою запись — но только
            # за ОБРАБАТЫВАЕМЫЙ период: успешка, забронированная в следующем
            # месяце переносом, пересчёту месяца базы не принадлежит.
            doomed = [lid for lid in applied_ids
                      if str(by_id[lid].get('status') or 'new') != 'success']
            if doomed:
                cursor.execute(
                    """
                    DELETE FROM tez_lead_successes
                    WHERE lead_id = ANY(%s::uuid[]) AND year = %s AND month = %s
                    """,
                    (doomed, int(year), int(month)),
                )

            success_rows = []
            for lead_id in applied_ids:
                item = by_id[lead_id]
                if str(item.get('status') or 'new') != 'success':
                    continue
                success_rows.append((
                    lead_id, item.get('phone_norm'),
                    int(item['operator_id']) if item.get('operator_id') else None,
                    str(item.get('operator_name') or '')[:255],
                    item.get('call_general_id'),
                    item.get('call_at'), item.get('first_order_at'),
                    item.get('success_date'),
                    int(item.get('success_year') or year), int(item.get('success_month') or month),
                    str(item.get('rule') or '')[:32],
                    bool(item.get('is_late')),
                ))
            if success_rows:
                # У таблицы ДВА уникальных ключа: lead_id и (phone_norm,
                # success_date). ON CONFLICT умеет держать только один, и держит
                # lead_id — поэтому смена владельца успешки роняет весь пересчёт.
                # Так и случилось на проде 18.08.2026: успешка номера была
                # забронирована переносом на лид ИЮЛЯ, а потом тот же номер
                # пришёл в базу АВГУСТА. Победителем становится августовский лид
                # (см. _drop_duplicate_successes), но июльская строка остаётся на
                # месте и занимает (номер, дата) — UniqueViolation, откат всей
                # транзакции, и за период не записывается НИЧЕГО. Успешки встали
                # на 17.08, пока Binotel и TEZ APP работали штатно.
                # Поэтому проигравшую строку убираем явно, до вставки: сам индекс
                # остаётся сторожем инварианта «одна поездка — одна успешка».
                execute_values(
                    cursor,
                    """
                    DELETE FROM tez_lead_successes s
                    USING (VALUES %s) AS w (lead_id, phone_norm, success_date)
                    WHERE s.phone_norm = w.phone_norm
                      AND s.success_date = w.success_date
                      AND s.lead_id <> w.lead_id
                    """,
                    [(row[0], row[1], row[7]) for row in success_rows],
                    template="(%s::uuid, %s::varchar, %s::date)",
                    page_size=500,
                )
                execute_values(
                    cursor,
                    """
                    INSERT INTO tez_lead_successes
                        (lead_id, phone_norm, operator_id, operator_name, call_general_id,
                         call_at, first_order_at, success_date, year, month, rule_code, is_late)
                    VALUES %s
                    ON CONFLICT (lead_id) DO UPDATE SET
                        operator_id = EXCLUDED.operator_id,
                        operator_name = EXCLUDED.operator_name,
                        call_general_id = EXCLUDED.call_general_id,
                        call_at = EXCLUDED.call_at,
                        first_order_at = EXCLUDED.first_order_at,
                        success_date = EXCLUDED.success_date,
                        year = EXCLUDED.year,
                        month = EXCLUDED.month,
                        rule_code = EXCLUDED.rule_code,
                        is_late = EXCLUDED.is_late,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    success_rows,
                    page_size=500,
                )
        return stats

    def count_tez_successes(self, year, month):
        """Сколько успешек сейчас засчитано за период (для контроля убыли).

        Без номеров «Реестра тестовых номеров»: их успешки пересчёт снимает намеренно,
        и сторож убыли сравнивал бы несопоставимые числа."""
        from test_numbers import keys as test_keys
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM tez_lead_successes s WHERE s.year = %s AND s.month = %s AND "
                + test_keys.sql_not_test('s.phone_norm', digits=True),
                (int(year), int(month))
            )
            return int((cursor.fetchone() or [0])[0] or 0)

    def get_tez_lead_funnel(self, year, month):
        """Воронка по базе месяца: загружено -> обзвонено -> дозвонились -> выехали -> успешки."""
        from tez.op_leads import ALMATY_TZ, call_window_for_period
        from test_numbers import keys as test_keys
        not_test = test_keys.sql_not_test('l.phone_norm', digits=True)
        window_start, window_end = call_window_for_period(year, month)
        # Границы считаем в Python готовыми aware-датами, а не приведением даты
        # к зоне прямо в SQL: у Postgres тип date неявно приводится и к
        # timestamp, и к timestamptz, и он выбирает вторую ветку — вместо
        # полуночи по Алматы выходит сдвиг на 10 часов (проверено на проде).
        window_from = datetime.combine(window_start, dt_time.min, tzinfo=ALMATY_TZ)
        window_to = datetime.combine(window_end + timedelta(days=1), dt_time.min, tzinfo=ALMATY_TZ)
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS leads_total,
                    COUNT(*) FILTER (WHERE l.month_first_order_at IS NOT NULL) AS went_online,
                    COUNT(*) FILTER (WHERE l.status = 'already_working') AS already_working,
                    -- Считается ниже по tez_lead_successes: успешка переноса
                    -- книжится в месяц ПОЕЗДКИ, а её лид остаётся в месяце базы,
                    -- и счёт по статусам лидов периода её потерял бы.
                    0 AS successes,
                    COUNT(*) FILTER (WHERE l.status = 'not_counted') AS not_counted,
                    COUNT(*) FILTER (WHERE l.status = 'in_progress') AS in_progress,
                    COALESCE(SUM(l.upload_count) - COUNT(*), 0) AS duplicates,
                    COUNT(*) FILTER (WHERE l.prev_month_first_order_at IS NOT NULL) AS active_prev_month
                FROM tez_leads l
                WHERE l.year = %s AND l.month = %s
                  AND """ + not_test + """
                """,
                (int(year), int(month))
            )
            row = cursor.fetchone() or (0,) * 8

            # «Обзвонено»/«Дозвонились» считаем в том же окне, в котором звонок
            # вообще может дать успешку (последние 7 дней прошлого месяца +
            # отчётный), и только по попыткам операторов ОП: звонки ТП/линии на
            # тот же номер работой отдела продаж не являются.
            cursor.execute(
                """
                SELECT
                    COUNT(DISTINCT l.phone_norm) FILTER (WHERE c.general_call_id IS NOT NULL) AS dialed,
                    COUNT(DISTINCT l.phone_norm) FILTER (WHERE c.is_qualifying) AS reached,
                    COUNT(c.general_call_id) AS attempts
                FROM tez_leads l
                LEFT JOIN tez_lead_calls c
                       ON c.phone_norm = l.phone_norm
                      AND c.call_type = 1
                      AND c.operator_id IS NOT NULL
                      AND c.started_at >= %s
                      AND c.started_at < %s
                WHERE l.year = %s AND l.month = %s
                  AND """ + not_test + """
                """,
                (window_from, window_to, int(year), int(month))
            )
            calls_row = cursor.fetchone() or (0, 0, 0)

            # Успешки периода — по месяцу ПОЕЗДКИ, вместе с перенесёнными, плюс
            # отдельно сколько из них дал перенос: для владельца это ответ на
            # вопрос «что дало правило», а не строчка мелким шрифтом.
            # Лиды с номерами «Реестра тестовых номеров» не входят ни в базу, ни в
            # успешки: пересчёт закрытых месяцев не идёт, и снятая им успешка
            # осталась бы в числителе при уже очищенном знаменателе.
            prev_year, prev_month = (int(year) - 1, 12) if int(month) == 1 else (int(year), int(month) - 1)
            cursor.execute(
                """
                SELECT COUNT(*) AS successes,
                       COUNT(*) FILTER (WHERE l.year = %s AND l.month = %s) AS carried_in
                FROM tez_lead_successes s
                JOIN tez_leads l ON l.id = s.lead_id
                WHERE s.year = %s AND s.month = %s
                  AND """ + not_test + """
                """,
                (prev_year, prev_month, int(year), int(month))
            )
            succ_row = cursor.fetchone() or (0, 0)

            # Сколько лидов ЭТОГО месяца сейчас дорабатываются в следующем.
            cursor.execute(
                """
                SELECT COUNT(*) FROM tez_leads l
                WHERE l.year = %s AND l.month = %s AND l.status <> 'success'
                  AND """ + not_test + """
                """,
                (int(year), int(month))
            )
            carried_out = int((cursor.fetchone() or [0])[0] or 0)

        leads_total = int(row[0] or 0)
        already_working = int(row[2] or 0)
        successes = int(succ_row[0] or 0)
        # Владелец: конверсию считаем от рабочей части базы, а «уже работающих»
        # показываем отдельным числом как оценку качества самой базы.
        workable = max(leads_total - already_working, 0)
        return {
            'leads_total': leads_total,
            'duplicates': int(row[6] or 0),
            'dialed': int(calls_row[0] or 0),
            'reached': int(calls_row[1] or 0),
            'attempts': int(calls_row[2] or 0),
            # Окно попыток отдаём наружу, чтобы подсказка на карточке называла
            # конкретные даты, а не абстрактные «последние 7 дней».
            'call_window_start': window_start.isoformat(),
            'call_window_end': window_end.isoformat(),
            'went_online': int(row[1] or 0),
            'already_working': already_working,
            'in_progress': int(row[5] or 0),
            'not_counted': int(row[4] or 0),
            'successes': successes,
            # Перенос: пришло из прошлого месяца / ушло дорабатываться в следующий.
            # В leads_total и в знаменатель конверсии перенесённые НЕ входят —
            # база месяца это то, что загрузил СВ, иначе конверсия августа
            # обвалилась бы втрое без единой потерянной успешки.
            'carried_in': int(succ_row[1] or 0),
            'carried_out': carried_out,
            'workable': workable,
            'active_prev_month': int(row[7] or 0),
            'conversion': round(successes / workable * 100, 1) if workable else 0.0,
            'conversion_all': round(successes / leads_total * 100, 1) if leads_total else 0.0,
        }

    def get_tez_operator_successes(self, year, month, group_id=None):
        """Рейтинг операторов по успешкам месяца (месяц берётся по дате поездки)."""
        from test_numbers import keys as test_keys
        params = [int(year), int(month)]
        group_sql = ''
        if group_id:
            group_sql = self._TEZ_GROUP_FILTER_SQL
            params.append(int(group_id))
        with self._get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT s.operator_id,
                       COALESCE(u.name, s.operator_name) AS operator_name,
                       COUNT(*) AS successes,
                       MIN(s.success_date) AS first_success,
                       MAX(s.success_date) AS last_success
                FROM tez_lead_successes s
                LEFT JOIN users u ON u.id = s.operator_id
                WHERE s.year = %s AND s.month = %s
                  AND {test_keys.sql_not_test('s.phone_norm', digits=True)}
                {group_sql}
                GROUP BY s.operator_id, COALESCE(u.name, s.operator_name)
                ORDER BY successes DESC, operator_name
                """,
                tuple(params)
            )
            return [{
                'operator_id': int(r[0]) if r[0] is not None else None,
                'operator_name': r[1] or '',
                'successes': int(r[2] or 0),
                'first_success': r[3].isoformat() if r[3] else None,
                'last_success': r[4].isoformat() if r[4] else None,
            } for r in cursor.fetchall()]

    def get_tez_successes_by_day(self, year, month, group_id=None):
        """Успешки по дням — дата = день выполнения первой поездки."""
        from test_numbers import keys as test_keys
        params = [int(year), int(month)]
        group_sql = ''
        if group_id:
            group_sql = self._TEZ_GROUP_FILTER_SQL
            params.append(int(group_id))
        with self._get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT s.success_date, COUNT(*)
                FROM tez_lead_successes s
                WHERE s.year = %s AND s.month = %s
                  AND {test_keys.sql_not_test('s.phone_norm', digits=True)}
                {group_sql}
                GROUP BY s.success_date
                ORDER BY s.success_date
                """,
                tuple(params)
            )
            return [{'date': r[0].isoformat(), 'successes': int(r[1] or 0)} for r in cursor.fetchall()]

    def get_tez_successes_for_day(self, year, month, success_date, group_id=None):
        """Успешки одного дня со всей раскладкой «кто позвонил -> кто выехал».

        Для карточки дня в разбивке «По дням»: там нужно не число, а сами
        контакты, дошедшие до заказа. Отдельный метод вместо фильтра в общей
        детализации лидов: тут выборка идёт от успешки, поэтому и группа, и день
        применяются без риска молча отрезать лиды без успешки.
        """
        from test_numbers import keys as test_keys
        params = [int(year), int(month), success_date]
        group_sql = ''
        if group_id:
            group_sql = self._TEZ_GROUP_FILTER_SQL
            params.append(int(group_id))
        with self._get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT s.phone_norm, l.full_name,
                       s.operator_id, COALESCE(u.name, s.operator_name),
                       s.call_at AT TIME ZONE 'Asia/Almaty',
                       s.first_order_at AT TIME ZONE 'Asia/Almaty',
                       s.rule_code, s.is_late, c.billsec, batch.file_name
                FROM tez_lead_successes s
                JOIN tez_leads l ON l.id = s.lead_id
                LEFT JOIN users u ON u.id = s.operator_id
                LEFT JOIN tez_lead_calls c ON c.general_call_id = s.call_general_id
                LEFT JOIN tez_lead_batches batch ON batch.id = l.first_batch_id
                WHERE s.year = %s AND s.month = %s AND s.success_date = %s
                  AND {test_keys.sql_not_test('s.phone_norm', digits=True)}
                {group_sql}
                ORDER BY s.call_at
                """,
                tuple(params)
            )
            return [{
                'phone': r[0],
                'full_name': r[1] or '',
                'operator_id': int(r[2]) if r[2] is not None else None,
                'operator_name': r[3] or '',
                'call_at': r[4].isoformat() if r[4] else None,
                'first_order_at': r[5].isoformat() if r[5] else None,
                'rule_code': r[6],
                'is_late': bool(r[7]),
                'talk_duration_seconds': int(r[8]) if r[8] is not None else None,
                'source_file_name': r[9] or '',
            } for r in cursor.fetchall()]

    def get_tez_successes_operator_day(self, year, month, group_id=None, operator_id=None):
        """Успешки по оператору и дню — для таба «Успешки» в учёте часов.

        Возвращает список {operator_id, day, successes}, где day — число месяца
        успешки (= дня первой поездки). Месяц берётся по дате поездки, поэтому в
        таблицу за июль попадут и успешки от лидов, обзвоненных в конце июня.

        operator_id сужает выборку до одного оператора — так «Мои часы» получают
        свои успешки без доступа к статистике всей группы.
        """
        from test_numbers import keys as test_keys
        params = [int(year), int(month)]
        group_sql = ''
        if group_id:
            group_sql = self._TEZ_GROUP_FILTER_SQL
            params.append(int(group_id))
        operator_sql = ''
        if operator_id is not None:
            operator_sql = " AND s.operator_id = %s"
            params.append(int(operator_id))
        with self._get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT s.operator_id,
                       EXTRACT(DAY FROM s.success_date)::int AS day,
                       COUNT(*) AS successes
                FROM tez_lead_successes s
                WHERE s.year = %s AND s.month = %s
                  AND s.operator_id IS NOT NULL
                  AND {test_keys.sql_not_test('s.phone_norm', digits=True)}
                {group_sql}{operator_sql}
                GROUP BY s.operator_id, EXTRACT(DAY FROM s.success_date)
                """,
                tuple(params)
            )
            return [
                {'operator_id': int(r[0]), 'day': int(r[1]), 'successes': int(r[2])}
                for r in cursor.fetchall()
            ]

    @staticmethod
    def _tez_leads_detail_filters(status=None, operator_id=None, search=None):
        """Общий фрагмент WHERE + параметры для детализации/подсчёта лидов —
        чтобы страница и её total считались по одинаковым условиям.

        Лид с номером «Реестра тестовых номеров» — не в периметре воронки: ни в
        странице, ни в total."""
        from test_numbers import keys as test_keys
        sql = ' AND ' + test_keys.sql_not_test('l.phone_norm', digits=True)
        params = []
        if status:
            sql += " AND l.status = %s"
            params.append(str(status))
        if operator_id:
            sql += " AND s.operator_id = %s"
            params.append(int(operator_id))
        if search:
            sql += " AND (l.full_name ILIKE %s OR l.phone_norm LIKE %s)"
            like = f"%{str(search).strip()}%"
            params.extend([like, like])
        return sql, params

    def count_tez_leads_detail(self, year, month, status=None, operator_id=None, search=None):
        """Сколько лидов подходит под фильтры детализации (для пагинации)."""
        filt_sql, filt_params = self._tez_leads_detail_filters(status, operator_id, search)
        sql = """
            SELECT COUNT(*)
            FROM tez_leads l
            JOIN tez_drivers d ON d.phone_norm = l.phone_norm
            LEFT JOIN tez_lead_successes s ON s.lead_id = l.id
            WHERE l.year = %s AND l.month = %s
        """ + filt_sql
        with self._get_cursor() as cursor:
            cursor.execute(sql, tuple([int(year), int(month)] + filt_params))
            return int(cursor.fetchone()[0] or 0)

    def get_tez_leads_detail(self, year, month, status=None, operator_id=None, search=None, limit=2000, offset=0):
        """Детализация по лидам со связкой звонок -> поездка (для разбора споров)."""
        filt_sql, filt_params = self._tez_leads_detail_filters(status, operator_id, search)
        # У «не засчитанных» (и «в работе») нет строки в tez_lead_successes, поэтому
        # оператора и звонок берём запасным путём — последний квалифицирующий звонок
        # ДО поездки (тот же, что рассматривал расчёт успешки). Так в детализации у
        # not_counted видно, кто звонил и когда, хоть успешка и не засчитана.
        sql = """
            SELECT l.id, l.phone_norm, l.full_name, l.status, l.status_rule, l.upload_count,
                   -- Даты храним в UTC (TIMESTAMPTZ), но показывать надо в Asia/Almaty
                   -- (UTC+5). AT TIME ZONE переводит в местное настенное время, иначе
                   -- в интерфейсе время уезжает на −5 часов (и у полуночных поездок
                   -- сдвигается день).
                   l.month_first_order_at AT TIME ZONE 'Asia/Almaty',
                   COALESCE(s.operator_id, lc.operator_id),
                   COALESCE(u.name, s.operator_name, lu.name),
                   COALESCE(s.call_at, lc.started_at) AT TIME ZONE 'Asia/Almaty',
                   s.success_date, s.rule_code,
                   l.prev_month_first_order_at AT TIME ZONE 'Asia/Almaty',
                   source_batch.file_name,
                   CASE
                       -- Для успешек длительность должна относиться ровно к тому
                       -- звонку Binotel, который сохранён как звонок привлечения.
                       WHEN s.id IS NOT NULL THEN success_call.billsec
                       -- Для остальных статусов это тот же последний
                       -- квалифицирующий звонок, который показываем в колонке
                       -- «Звонок» детализации.
                       ELSE lc.billsec
                   END
            FROM tez_leads l
            LEFT JOIN tez_lead_successes s ON s.lead_id = l.id
            LEFT JOIN users u ON u.id = s.operator_id
            LEFT JOIN tez_lead_batches source_batch ON source_batch.id = l.first_batch_id
            LEFT JOIN tez_lead_calls success_call
              ON success_call.general_call_id = s.call_general_id
            LEFT JOIN LATERAL (
                SELECT c.operator_id, c.started_at, c.billsec
                FROM tez_lead_calls c
                WHERE c.phone_norm = l.phone_norm
                  AND c.is_qualifying
                  AND (l.month_first_order_at IS NULL OR c.started_at < l.month_first_order_at)
                ORDER BY c.started_at DESC
                LIMIT 1
            ) lc ON TRUE
            LEFT JOIN users lu ON lu.id = lc.operator_id
            WHERE l.year = %s AND l.month = %s
        """ + filt_sql + """
            ORDER BY l.month_first_order_at DESC NULLS LAST, l.full_name
            LIMIT %s OFFSET %s
        """
        params = [int(year), int(month)] + filt_params + [int(limit), int(offset)]

        with self._get_cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return [{
                'lead_id': str(r[0]),
                'phone': r[1],
                'full_name': r[2] or '',
                'status': r[3],
                'status_rule': r[4],
                'upload_count': int(r[5] or 1),
                # Явное имя для поездки внутри отчётного месяца. Старое
                # first_order_at сохраняем в payload для обратной совместимости,
                # но оно двусмысленно рядом с prev_month_first_order_at.
                'month_first_order_at': r[6].isoformat() if r[6] else None,
                'first_order_at': r[6].isoformat() if r[6] else None,
                'operator_id': int(r[7]) if r[7] is not None else None,
                'operator_name': r[8] or '',
                'call_at': r[9].isoformat() if r[9] else None,
                'success_date': r[10].isoformat() if r[10] else None,
                'rule_code': r[11],
                'prev_month_first_order_at': r[12].isoformat() if r[12] else None,
                'source_file_name': r[13] or '',
                'talk_duration_seconds': int(r[14]) if r[14] is not None else None,
            } for r in cursor.fetchall()]

    def get_tez_leads_report(self, year, month, limit=50000):
        """Отчёт за месяц одним списком: своя база + перенос с прошлого месяца.

        Отличие от get_tez_leads_detail: тот отдаёт лиды ОДНОГО месяца в разрезе
        ИХ месяца, поэтому успешка переноса (лид прошлого месяца, поездка в этом)
        в него не попадала — в выгрузке августа было 134 успешки против 245 в
        разделе. Здесь набор строк равен периметру воронки периода:

          * вся база отчётного месяца — и успешные, и нет;
          * лиды прошлого месяца, не ставшие успешкой ТАМ (те самые, которые
            дорабатываются ещё месяц; тот же отбор, что у ветви переноса в
            get_tez_leads_for_recompute).

        Все колонки перенесённой строки пересчитаны на ОТЧЁТНЫЙ месяц: статус и
        правило берутся из carry_status, поездка — из carry_first_order_at, а
        поездка месяца базы становится «поездкой в прошлом месяце» (месяц базы и
        есть прошлый). Поэтому по строке видно, стал перенесённый лид успешкой
        или нет, а не только то, что он был в базе.

        Гарантия на число успешек: успешка периода всегда лежит на лиде этого
        месяца или на лиде прошлого со status <> 'success' (проверено на проде —
        других вариантов в tez_lead_successes нет), то есть ровно на строках
        этой выборки. Считать успешки в отчёте и в разделе теперь одно и то же.
        """
        from test_numbers import keys as test_keys
        year, month = int(year), int(month)
        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        sql = """
            WITH report AS (
                SELECT l.id, l.year AS base_year, l.month AS base_month,
                       FALSE AS is_carried, l.phone_norm, l.full_name,
                       l.upload_count, l.first_batch_id,
                       l.status AS base_status, l.status_rule AS base_status_rule,
                       l.month_first_order_at AS trip_at,
                       l.prev_month_first_order_at AS prev_trip_at
                FROM tez_leads l
                WHERE l.year = %(year)s AND l.month = %(month)s
                UNION ALL
                SELECT l.id, l.year, l.month,
                       TRUE, l.phone_norm, l.full_name,
                       l.upload_count, l.first_batch_id,
                       l.carry_status, l.carry_status_rule,
                       l.carry_first_order_at,
                       l.month_first_order_at
                FROM tez_leads l
                WHERE l.year = %(prev_year)s AND l.month = %(prev_month)s
                  AND l.status <> 'success'
                  -- Номер, пришедший в базу и этого месяца тоже (на августе это
                  -- 2797 строк из 6993), в отчёте не двоим: в отчётном месяце
                  -- водителя представляет строка СВОЕЙ базы — она же побеждает
                  -- в _drop_duplicate_successes, — а вторая читалась бы как
                  -- расхождение статусов по одному человеку. Строку с успешкой
                  -- отчётного месяца оставляем при любом раскладе: потерять
                  -- успешку тут страшнее, чем показать номер дважды.
                  AND (NOT EXISTS (
                           SELECT 1 FROM tez_leads own
                           WHERE own.year = %(year)s AND own.month = %(month)s
                             AND own.phone_norm = l.phone_norm
                       )
                       OR EXISTS (
                           SELECT 1 FROM tez_lead_successes s
                           WHERE s.lead_id = l.id
                             AND s.year = %(year)s AND s.month = %(month)s
                       ))
            )
            SELECT r.id, r.phone_norm, r.full_name,
                   -- Успешка отчётного месяца — сама себе статус: она уже
                   -- записана и посчитана разделом, поэтому в отчёте не может
                   -- зависеть от того, добежал ли пересчёт до carry_status.
                   CASE WHEN s.id IS NOT NULL THEN 'success'
                        -- И наоборот: успешкой строку делает ТОЛЬКО запись в
                        -- tez_lead_successes за период — по ней раздел считает
                        -- свои 245, по ней же обязан считать отчёт. Статус
                        -- 'success' без такой записи означает, что статус залип
                        -- (лид пропущен пересчётом по несовпадению версии,
                        -- номер отдан строке своей базы), и назвать его
                        -- успешкой — снова разойтись с разделом, теперь в
                        -- другую сторону. Так равенство «успешек в файле =
                        -- успешек в разделе» держится структурой запроса, а не
                        -- цепочкой инвариантов в трёх файлах.
                        WHEN r.base_status = 'success' THEN 'stale_success'
                        -- base_status у своей строки NOT NULL; NULL бывает только
                        -- у переноса, которого пересчёт ещё не видел.
                        ELSE COALESCE(r.base_status, 'carry_pending') END,
                   CASE WHEN s.id IS NOT NULL THEN s.rule_code
                        ELSE r.base_status_rule END,
                   r.upload_count,
                   -- Даты храним в UTC (TIMESTAMPTZ), показываем в Asia/Almaty:
                   -- без AT TIME ZONE время уезжает на −5 часов, а у полуночных
                   -- поездок сдвигается день.
                   r.trip_at AT TIME ZONE 'Asia/Almaty',
                   r.prev_trip_at AT TIME ZONE 'Asia/Almaty',
                   COALESCE(s.operator_id, lc.operator_id),
                   COALESCE(u.name, s.operator_name, lu.name),
                   COALESCE(s.call_at, lc.started_at) AT TIME ZONE 'Asia/Almaty',
                   s.success_date, s.rule_code,
                   source_batch.file_name,
                   CASE WHEN s.id IS NOT NULL THEN success_call.billsec
                        ELSE lc.billsec END,
                   r.is_carried, r.base_year, r.base_month
            FROM report r
            -- Успешка берётся ТОЛЬКО за отчётный период: успешка, забронированная
            -- лидом на следующий месяц (август уже дал 4 сентябрьские), в отчёт
            -- этого месяца не входит, иначе счёт разошёлся бы с разделом.
            LEFT JOIN tez_lead_successes s
                   ON s.lead_id = r.id AND s.year = %(year)s AND s.month = %(month)s
            LEFT JOIN users u ON u.id = s.operator_id
            LEFT JOIN tez_lead_batches source_batch ON source_batch.id = r.first_batch_id
            LEFT JOIN tez_lead_calls success_call
                   ON success_call.general_call_id = s.call_general_id
            -- У строки без успешки оператора и звонок показываем запасным путём —
            -- последний квалифицирующий звонок ДО поездки, тот же, что рассматривал
            -- расчёт. Порог поездки у переноса свой (carry_first_order_at), поэтому
            -- отсечка берётся из r.trip_at, а не из колонки лида.
            LEFT JOIN LATERAL (
                SELECT c.operator_id, c.started_at, c.billsec
                FROM tez_lead_calls c
                WHERE c.phone_norm = r.phone_norm
                  AND c.is_qualifying
                  AND (r.trip_at IS NULL OR c.started_at < r.trip_at)
                ORDER BY c.started_at DESC
                LIMIT 1
            ) lc ON TRUE
            LEFT JOIN users lu ON lu.id = lc.operator_id
            -- Номер «Реестра тестовых номеров» не в периметре воронки — и не в отчёте.
            WHERE """ + test_keys.sql_not_test('r.phone_norm', digits=True) + """
            -- Своя база месяца идёт первой, перенос — за ней: у листа одна
            -- шапка, и блоками он читается так же, как раньше читались два листа.
            ORDER BY r.is_carried, r.trip_at DESC NULLS LAST, r.full_name
            LIMIT %(limit)s
        """
        params = {
            'year': year, 'month': month,
            'prev_year': prev_year, 'prev_month': prev_month,
            'limit': int(limit),
        }
        with self._get_cursor() as cursor:
            cursor.execute(sql, params)
            return [{
                'lead_id': str(r[0]),
                'phone': r[1],
                'full_name': r[2] or '',
                'status': r[3],
                'status_rule': r[4],
                'upload_count': int(r[5] or 1),
                # Имена колонок — от ОТЧЁТНОГО месяца, а не от месяца базы:
                # у переноса это carry-поездка и поездка его собственного месяца.
                'month_first_order_at': r[6].isoformat() if r[6] else None,
                'first_order_at': r[6].isoformat() if r[6] else None,
                'prev_month_first_order_at': r[7].isoformat() if r[7] else None,
                'operator_id': int(r[8]) if r[8] is not None else None,
                'operator_name': r[9] or '',
                'call_at': r[10].isoformat() if r[10] else None,
                'success_date': r[11].isoformat() if r[11] else None,
                'rule_code': r[12],
                'source_file_name': r[13] or '',
                'talk_duration_seconds': int(r[14]) if r[14] is not None else None,
                'is_carried': bool(r[15]),
                'base_year': int(r[16]),
                'base_month': int(r[17]),
            } for r in cursor.fetchall()]

    def get_tez_success_counts_for_operators(self, operator_ids, year, month):
        """{operator_id: успешек за месяц} — для подстановки факта в план ОП."""
        from test_numbers import keys as test_keys
        ids = [int(v) for v in (operator_ids or []) if v is not None]
        if not ids:
            return {}
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT s.operator_id, COUNT(*)
                FROM tez_lead_successes s
                WHERE s.year = %s AND s.month = %s AND s.operator_id = ANY(%s)
                  AND """ + test_keys.sql_not_test('s.phone_norm', digits=True) + """
                GROUP BY s.operator_id
                """,
                (int(year), int(month), ids)
            )
            return {int(r[0]): int(r[1] or 0) for r in cursor.fetchall()}

    def get_tez_broadcast_personal(self, user_id, direction='tez') -> Dict[str, Any]:
        """Личная отбивка табло у человека: {enabled, mode}. Пустого профиля достаточно:
        строку в admin_profiles заводит только запись."""
        enabled_col, mode_col = self._broadcast_personal_columns(direction)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT COALESCE({enabled_col}, FALSE),
                       COALESCE({mode_col}, 'always')
                  FROM admin_profiles
                 WHERE user_id = %s
            """, (int(user_id),))
            row = cursor.fetchone()
        if not row:
            return {'enabled': False, 'mode': 'always'}
        return {'enabled': bool(row[0]), 'mode': row[1] or 'always'}

    def set_tez_broadcast_personal(self, user_id, enabled=None, mode=None,
                                   direction='tez') -> Dict[str, Any]:
        """Включить/выключить личную отбивку или сменить её режим. Не переданное поле остаётся
        как было — форма шлёт точечные патчи, как у получателей-чатов.

        Право проверяет ручка (_szov_broadcast_guard плюс роль админа), а рассылка
        перепроверяет его на каждой отправке — см. get_tez_broadcast_personal_recipients."""
        if mode is not None:
            mode = str(mode).strip()
            if mode not in self.SZOV_BROADCAST_MODES:
                raise ValueError("Неизвестный режим отправки")
        enabled_col, mode_col = self._broadcast_personal_columns(direction)
        current = self.get_tez_broadcast_personal(user_id, direction=direction)
        enabled = current['enabled'] if enabled is None else bool(enabled)
        mode = current['mode'] if mode is None else mode
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                INSERT INTO admin_profiles (user_id, {enabled_col}, {mode_col})
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE
                   SET {enabled_col} = EXCLUDED.{enabled_col},
                       {mode_col} = EXCLUDED.{mode_col}
                RETURNING {enabled_col}, {mode_col}
            """, (int(user_id), enabled, mode))
            row = cursor.fetchone()
        return {'enabled': bool(row[0]), 'mode': row[1] or 'always'}

    def get_tez_broadcast_personal_recipients(self, tez_department_id=None, direction='tez',
                                              extra_user_ids=None) -> List[Dict[str, Any]]:
        """Кому отбивка табло уходит лично: флаг направления включён и человек до сих пор вправе.

        Право — админ или супер-админ, у которого раздел табло отображается (постановка
        владельца 23.09.2026 для Тез КЦ, та же лестница у «Табло ОП · Чат»): супер-админ;
        админ, который не возглавляет ни одного отдела; админ — глава отдела этого табло
        (`tez_department_id` — id отдела табло, имя параметра историческое). extra_user_ids —
        поимённо допущенные без роли админа (у «Чата» ОП — СВ верификаторов, только лично). Перепроверяем на каждой отправке, а не
        только при включении: админ, которого потом поставили главой другого отдела,
        раздела больше не видит — и выключить подписку ему было бы негде.

        Без Telegram отправлять некуда; уволенный тоже выпадает — увольнение не снимает
        ни telegram_id, ни роль 'admin' ('dismissal' — такое же увольнение, как 'fired')."""
        enabled_col, mode_col = self._broadcast_personal_columns(direction)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT u.id, u.name, u.telegram_id,
                       COALESCE(ap.{mode_col}, 'always')
                  FROM admin_profiles ap
                  JOIN users u ON u.id = ap.user_id
                 WHERE ap.{enabled_col} = TRUE
                   AND u.telegram_id IS NOT NULL
                   AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
                   AND (
                       LOWER(COALESCE(u.role, '')) IN ('super_admin', 'superadmin', 'super-admin', 'super admin')
                       OR (
                           LOWER(COALESCE(u.role, '')) = 'admin'
                           AND (
                               NOT EXISTS (
                                   SELECT 1 FROM departments d
                                    WHERE d.head_user_id = u.id
                                      AND COALESCE(d.is_active, TRUE) = TRUE
                               )
                               OR EXISTS (
                                   SELECT 1 FROM departments d
                                    WHERE d.head_user_id = u.id
                                      AND COALESCE(d.is_active, TRUE) = TRUE
                                      AND d.id = %s
                               )
                           )
                       )
                       OR u.id = ANY(%s::int[])
                   )
                 ORDER BY u.name, u.id
            """, (tez_department_id, sorted(int(v) for v in (extra_user_ids or ()))))
            rows = cursor.fetchall() or []
        return [{'id': int(row[0]), 'name': row[1] or '', 'telegram_id': int(row[2]),
                 'mode': row[3] or 'always'}
                for row in rows if row[2] is not None]
