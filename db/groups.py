"""База данных: группы.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _UNSET_GROUP,
    CALCULATION_MODEL_ALLOWED,
    CALCULATION_MODEL_DESCRIPTIONS,
    calendar,
    date,
    datetime,
    MEMBERSHIP_DAY_DISTANCE_SQL,
    MEMBERSHIP_MONTH_OVERLAP_SQL,
    normalize_calculation_model_code,
    re,
    role_has_min,
    supervisor_hours,
    timedelta,
)


class GroupsMixin:
    def _stamp_orphan_group_ids_tx(self, cursor, operator_id=None):
        """Проставляет group_id строкам daily_hours/work_hours, где он NULL: для daily —
        по дню строки, для work_hours — по концу месяца. Покрывающее членство приоритетнее;
        если его нет, строка достаётся ближайшему членству того же месяца
        (MEMBERSHIP_MONTH_OVERLAP_SQL) — иначе день не виден в «Учёте часов», хотя в «Моих
        часах» считается. Без членства в этом месяце строка остаётся NULL.

        Идемпотентно (трогает только NULL). operator_id=None — по всем операторам.
        Возвращает (сколько daily, сколько work_hours)."""
        scope = "" if operator_id is None else " AND {}.operator_id = %(operator_id)s"
        params = {} if operator_id is None else {'operator_id': int(operator_id)}
        cursor.execute("""
            UPDATE daily_hours dh
            SET group_id = sub.group_id
            FROM (
                SELECT DISTINCT ON (d.id) d.id, gom.group_id
                FROM daily_hours d
                JOIN group_operator_memberships gom
                  ON gom.operator_id = d.operator_id
                 AND """ + MEMBERSHIP_MONTH_OVERLAP_SQL.format(m='gom', day='d.day') + """
                WHERE d.group_id IS NULL""" + scope.format('d') + """
                ORDER BY d.id, """
                + MEMBERSHIP_DAY_DISTANCE_SQL.format(m='gom', day='d.day')
                + """, gom.start_date DESC
            ) sub
            WHERE dh.id = sub.id
        """, params)
        daily_fixed = cursor.rowcount
        cursor.execute("""
            UPDATE work_hours w
            SET group_id = sub.group_id
            FROM (
                SELECT DISTINCT ON (x.id) x.id, gom.group_id
                FROM work_hours x
                CROSS JOIN LATERAL (
                    SELECT (to_date(x.month || '-01', 'YYYY-MM-DD')
                            + interval '1 month - 1 day')::date AS day
                ) me
                JOIN group_operator_memberships gom
                  ON gom.operator_id = x.operator_id
                 AND """ + MEMBERSHIP_MONTH_OVERLAP_SQL.format(m='gom', day='me.day') + """
                WHERE x.group_id IS NULL""" + scope.format('x') + """
                ORDER BY x.id, """
                + MEMBERSHIP_DAY_DISTANCE_SQL.format(m='gom', day='me.day')
                + """, gom.start_date DESC
            ) sub
            WHERE w.id = sub.id
        """, params)
        return daily_fixed, cursor.rowcount

    def get_daily_hours_by_group_month(self, group_id, month):
        """Group-aware: часы операторов ГРУППЫ за месяц (членство пересекает месяц);
        модель и фильтр daily — по этой группе. Тонкая обёртка над
        get_daily_hours_by_supervisor_month."""
        return self.get_daily_hours_by_supervisor_month(None, month, group_id=group_id)

    def _operator_membership_days_tx(self, cursor, user_ids, date_from, date_to):
        """{(человек, день)} периода, покрытые операторским членством в группе."""
        ids = sorted({int(v) for v in (user_ids or []) if v is not None})
        if not ids or date_to < date_from:
            return set()
        cursor.execute("""
            SELECT operator_id, start_date, end_date
            FROM group_operator_memberships
            WHERE operator_id = ANY(%s)
              AND start_date <= %s
              AND (end_date IS NULL OR end_date >= %s)
        """, (ids, date_to, date_from))
        days = set()
        for operator_id, membership_start, membership_end in cursor.fetchall():
            first = max(date_from, membership_start)
            last = min(date_to, membership_end or date_to)
            for day in supervisor_hours.iter_days(first, last):
                days.add((int(operator_id), day))
        return days

    def _szov_broadcast_group(self, value, direction, existing=None):
        """Группа получателя: «не передано» — оставить как было, пусто — весь отдел."""
        if direction not in self.SZOV_BROADCAST_GROUP_DIRECTIONS:
            return None
        if value is _UNSET_GROUP:
            return (existing or {}).get('group_id')
        if value in (None, '', 'null', 'all', 0, '0'):
            return None
        try:
            group_id = int(value)
        except (TypeError, ValueError):
            raise ValueError("Неизвестная группа для отбивки")
        if group_id <= 0:
            raise ValueError("Неизвестная группа для отбивки")
        return group_id

    def list_bot_group_chats(self) -> list:
        """Группы/каналы, в которых бот состоит (их же авто-находит IT-заявочный хук).

        Отдельной таблицы под отбивку не заводим: список «где бот есть» один на бота,
        а it_ticket_channels уже наполняется обработчиком my_chat_member."""
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT chat_id, title, chat_type, username, is_active
                FROM it_ticket_channels
                WHERE is_active
                ORDER BY COALESCE(NULLIF(title, ''), username, chat_id::text)
            """)
            rows = cur.fetchall()
        return [{
            "chat_id": r[0],
            "title": r[1] or "",
            "chat_type": r[2] or "",
            "username": r[3] or "",
        } for r in rows]

    def get_group_operators_for_month(self, group_id, month):
        """Состав ГРУППЫ за месяц для «Аналитики» журнала оценок.

        Историческая видимость ровно как в «Учёте часов»: состав определяется
        ЧЛЕНСТВОМ в группе, пересекающим месяц, а не текущим users.supervisor_id.
        Поэтому оператор не пропадает из прошлых месяцев, когда его перевели,
        повысили до СВ или уволили. Роль здесь не фильтруется намеренно.

        Отличия от get_operators_by_supervisor (роутер ждёт ту же форму строки):
          * status — статус НА КОНЕЦ МЕСЯЦА (user_history), а не сегодняшний;
          * dismissal_date — по ней фронт решает вкладку «Уволенные»;
          * group_segments — сегменты членства внутри месяца: у переведённого
            оператора метрики оценок принадлежат месяцу целиком (в calls нет
            group_id), и фронт обязан это показать, а не молча удвоить.

        DISTINCT ON (u.id): у одной пары (оператор, группа) бывает несколько
        строк членства, пересекающих месяц (перезачисление) — без него оператор
        появился бы в таблице дважды и удвоил итоги.

        Членство «конец раньше начала» (перевод, отменённый в момент заведения:
        на проде 16 таких, 3 — в сентябре 2026) не покрывает ни одного дня. Без
        отсечки человек попадал в состав группы, где не был ни дня, и фронт
        записывал его в «Переведённые» этой группы.
        """
        year, mon = map(int, str(month).split('-'))
        last_day = calendar.monthrange(year, mon)[1]
        start = date(year, mon, 1)
        end = date(year, mon, last_day)
        ref_dt = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT ON (u.id)
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
                    u.department_id,
                    u.role
                FROM group_operator_memberships gom
                JOIN users u ON u.id = gom.operator_id
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
                WHERE gom.group_id = %s
                  AND gom.start_date <= %s
                  AND (gom.end_date IS NULL OR gom.end_date >= %s)
                  AND (gom.end_date IS NULL OR gom.end_date >= gom.start_date)
                ORDER BY u.id, gom.start_date
            """, (int(group_id), end, start))
            rows = cursor.fetchall() or []
            if not rows:
                return []

            op_ids = [int(row[0]) for row in rows]
            status_as_of = self._resolve_user_field_as_of_tx(cursor, op_ids, 'status', ref_dt)
            dismissal_dates = self._load_dismissal_dates_tx(cursor, op_ids)
            segments_by_operator = self._load_segments_by_operator_tx(cursor, op_ids, start, end)

            operators = []
            for row in rows:
                op_id = int(row[0])
                # Сегменты общие с отчётами часов и там не фильтруются; здесь
                # пустые (конец раньше начала) выбрасываем: по сегментам фронт
                # решает, ушёл ли человек из группы и куда.
                segments = [
                    seg for seg in segments_by_operator.get(op_id, [])
                    if int(seg.get('end_day') or 0) >= int(seg.get('start_day') or 0)
                ]
                other_groups = {int(seg.get('group_id') or 0) for seg in segments} - {int(group_id)}
                operators.append({
                    'id': row[0],
                    'name': row[1],
                    'direction_id': row[2],
                    'supervisor_id': row[3],
                    'hire_date': row[4].strftime('%d-%m-%Y') if row[4] else None,
                    'hours_table_url': row[5],
                    'scores_table_url': row[6],
                    'supervisor_name': row[7],
                    # Статус месяца, а не сегодняшний: иначе июнь показывает «уволен»
                    # человеку, который в июне ещё работал.
                    'status': status_as_of.get(op_id) or row[8],
                    'status_current': row[8],
                    'dismissal_date': self._dismissal_date_iso(dismissal_dates, op_id),
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
                    'department_id': row[21],
                    'role': row[22],
                    'group_segments': segments,
                    # Признак «в этом месяце оператор был не только в этой группе».
                    # Фронт «Аналитики» направление перевода (ушёл/пришёл) берёт уже
                    # из group_segments; флаг оставлен для совместимости ответа.
                    'has_other_group_in_month': bool(other_groups),
                })

            operators.sort(key=lambda item: str(item.get('name') or '').lower())
            return operators

    def _get_operator_group_id_tx(self, cursor, operator_id, as_of):
        """group_id группы оператора на дату as_of, или None.

        Покрывающее членство выигрывает всегда (расстояние 0); если такого нет —
        берётся ближайшее членство того же месяца (см. MEMBERSHIP_MONTH_OVERLAP_SQL).
        """
        if operator_id is None or as_of is None:
            return None
        cursor.execute(
            """
            SELECT gom.group_id
            FROM group_operator_memberships gom
            JOIN groups gr ON gr.id = gom.group_id
            WHERE gom.operator_id = %(operator_id)s
              AND """
            + MEMBERSHIP_MONTH_OVERLAP_SQL.format(m='gom', day='%(day)s::date')
            + """
            ORDER BY """
            + MEMBERSHIP_DAY_DISTANCE_SQL.format(m='gom', day='%(day)s::date')
            + """, gom.start_date DESC
            LIMIT 1
            """,
            {'operator_id': int(operator_id), 'day': as_of},
        )
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def _operator_in_group_on_date_tx(self, cursor, group_id, operator_id, as_of):
        if group_id is None or operator_id is None or as_of is None:
            return False
        cursor.execute(
            """
            SELECT 1
            FROM group_operator_memberships gom
            WHERE gom.group_id = %s
              AND gom.operator_id = %s
              AND gom.start_date <= %s
              AND (gom.end_date IS NULL OR gom.end_date >= %s)
            LIMIT 1
            """,
            (int(group_id), int(operator_id), as_of, as_of),
        )
        return cursor.fetchone() is not None

    def operator_in_group_on_date(self, group_id, operator_id, as_of):
        if isinstance(as_of, str):
            as_of = datetime.strptime(as_of, '%Y-%m-%d').date()
        with self._get_cursor() as cursor:
            return self._operator_in_group_on_date_tx(cursor, group_id, operator_id, as_of)

    def find_operator_in_group_by_name(self, group_id, name, as_of):
        """Поиск оператора по ФИО среди исторического состава группы на дату."""
        if not name:
            return None
        if isinstance(as_of, str):
            as_of = datetime.strptime(as_of, '%Y-%m-%d').date()
        name = str(name).strip()
        if not name:
            return None
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, u.telegram_id, u.name, u.role, u.direction_id,
                       u.hire_date, u.supervisor_id, u.login
                FROM group_operator_memberships gom
                JOIN users u ON u.id = gom.operator_id
                WHERE gom.group_id = %s
                  AND gom.start_date <= %s
                  AND (gom.end_date IS NULL OR gom.end_date >= %s)
                  AND LOWER(u.name) = LOWER(%s)
                ORDER BY gom.start_date DESC
                LIMIT 1
                """,
                (int(group_id), as_of, as_of, name),
            )
            row = cursor.fetchone()
            if row:
                return row

            tokens = re.split(r'\s+', name)
            surname = tokens[0] if tokens else ''
            if not surname:
                return None
            cursor.execute(
                """
                SELECT u.id, u.telegram_id, u.name, u.role, u.direction_id,
                       u.hire_date, u.supervisor_id, u.login
                FROM group_operator_memberships gom
                JOIN users u ON u.id = gom.operator_id
                WHERE gom.group_id = %s
                  AND gom.start_date <= %s
                  AND (gom.end_date IS NULL OR gom.end_date >= %s)
                  AND u.name ILIKE %s
                ORDER BY gom.start_date DESC
                LIMIT 1
                """,
                (int(group_id), as_of, as_of, f"%{surname}%"),
            )
            return cursor.fetchone()

    def _group_row_to_dict(self, row):
        # Группа без модели так и уходит наружу — None, а не «операторская»:
        # иначе карточка показывала бы модель, которую группе никто не задавал.
        model_code = (
            normalize_calculation_model_code(row[4], row[11])
            if str(row[4] or '').strip() else None
        )
        model_desc = CALCULATION_MODEL_DESCRIPTIONS.get(model_code, {})
        return {
            'id': int(row[0]),
            'name': row[1],
            'department_id': row[2],
            'direction_id': row[3],
            'calculation_model_code': model_code,
            'calculation_model_name': model_desc.get('name'),
            'table_url': row[5],
            'status': row[6],
            'archived_at': row[7].isoformat() if row[7] else None,
            'reused_from_group_id': row[8],
            'created_at': row[9].isoformat() if row[9] else None,
            'updated_at': row[10].isoformat() if row[10] else None,
            'direction_name': row[11],
            'active_operators': int(row[12] or 0),
            'supervisors': row[13] or [],
            # Направление, которое получит оператор при переводе в группу.
            'effective_direction_id': int(row[14]) if row[14] is not None else None,
        }

    def list_groups(self, include_archived=False, department_id=None):
        with self._get_cursor() as cursor:
            cursor.execute(
                self._GROUP_SELECT + """
                WHERE (%s OR g.status = 'active')
                  AND (%s::int IS NULL OR g.department_id = %s::int)
                ORDER BY (g.status = 'archived'), g.name
                """,
                (bool(include_archived), department_id, department_id),
            )
            return [self._group_row_to_dict(r) for r in cursor.fetchall() or []]

    def get_group(self, group_id):
        with self._get_cursor() as cursor:
            cursor.execute(self._GROUP_SELECT + " WHERE g.id = %s", (int(group_id),))
            row = cursor.fetchone()
            return self._group_row_to_dict(row) if row else None

    def get_group_members(self, group_id):
        """Текущий состав группы: активные (end_date IS NULL) операторы и супервайзеры."""
        def _rows(cursor, table, col):
            cursor.execute(
                "SELECT u.id, u.name, m.start_date, m.end_date, u.status FROM " + table + """ m
                 JOIN users u ON u.id = m.""" + col + """
                 WHERE m.group_id = %s AND m.end_date IS NULL
                 ORDER BY u.name""",
                (int(group_id),),
            )
            return [
                {
                    'id': r[0], 'name': r[1],
                    'start_date': r[2].isoformat() if r[2] else None,
                    'end_date': r[3].isoformat() if r[3] else None,
                    'status': r[4],
                }
                for r in (cursor.fetchall() or [])
            ]
        with self._get_cursor() as cursor:
            operators = _rows(cursor, 'group_operator_memberships', 'operator_id')
            supervisors = _rows(cursor, 'group_supervisor_memberships', 'supervisor_id')
            return {'operators': operators, 'supervisors': supervisors}

    def get_group_suggestions(self, department_id=None, direction_id=None):
        """Архивные группы того же отдела/направления — кандидаты на переиспользование."""
        with self._get_cursor() as cursor:
            cursor.execute(
                self._GROUP_SELECT + """
                WHERE g.status = 'archived'
                  AND (%s::int IS NULL OR g.department_id = %s::int)
                  AND (%s::int IS NULL OR g.direction_id = %s::int)
                ORDER BY g.archived_at DESC NULLS LAST, g.name
                """,
                (department_id, department_id, direction_id, direction_id),
            )
            return [self._group_row_to_dict(r) for r in cursor.fetchall() or []]

    def create_group(self, name, calculation_model_code=None, direction_id=None,
                     department_id=None, table_url=None, created_by=None):
        """Создаёт активную группу. Модель расчёта необязательна: без неё у группы
        нет своей модели (NULL), и сотрудники считаются по модели своего направления
        (_GROUP_MEMBER_MODEL_SQL). Модель направления самой группы сюда не
        подставляется: форма показывает ровно то, что сохранится, и «без модели»
        при выбранном направлении — осознанный выбор. Задать модель можно позже
        (change_group_model)."""
        model_code = (
            normalize_calculation_model_code(calculation_model_code)
            if str(calculation_model_code or '').strip() else None
        )
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO groups
                    (name, department_id, direction_id, calculation_model_code,
                     table_url, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
                """,
                (name, department_id, direction_id, model_code, table_url),
            )
            new_id = cursor.fetchone()[0]
        return self.get_group(new_id)

    def archive_group(self, group_id, end_date=None, changed_by=None):
        """Архивирует группу: status='archived' + закрывает открытые членства на дату.
        Историю и закрытые членства не трогает."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE groups SET status = 'archived', archived_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status <> 'archived'
                """,
                (int(group_id),),
            )
            # Операторы, которых закрываем вместе с группой, — снять с них СВ
            # (users.supervisor_id выводится из группы).
            cursor.execute(
                """
                SELECT operator_id FROM group_operator_memberships
                WHERE group_id = %s AND end_date IS NULL
                """,
                (int(group_id),),
            )
            orphaned = [r[0] for r in (cursor.fetchall() or [])]
            for table in ('group_operator_memberships', 'group_supervisor_memberships'):
                cursor.execute(
                    "UPDATE " + table + """
                    SET end_date = COALESCE(%s::date, CURRENT_DATE)
                    WHERE group_id = %s AND end_date IS NULL
                    """,
                    (end_date, int(group_id)),
                )
            self._set_operators_supervisor_tx(cursor, orphaned, None)
        return self.get_group(group_id)

    def reuse_archived_group(self, group_id):
        """Возвращает архивную группу в активные (история сохраняется)."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE groups SET status = 'active', archived_at = NULL,
                       updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (int(group_id),),
            )
        return self.get_group(group_id)

    def update_group_name(self, group_id, name):
        """Переименование группы. Меняется только название; модель/отдел/направление
        остаются неизменными."""
        new_name = (name or '').strip()
        if not new_name:
            raise ValueError("Group name cannot be empty")
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE groups SET name = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (new_name, int(group_id)),
            )
        return self.get_group(group_id)

    def change_group_model(self, group_id, new_model_code, changed_by=None, is_revert=False):
        """Меняет модель расчёта группы с записью в журнал group_model_change_log.

        Смена модели НЕ удаляет сырьё: daily_hours, метрики чатов и замороженные
        снимки закрытых месяцев (group_operator_month_snapshots хранят свою модель
        per-month) не трогаются — меняется только трактовка модели для живого
        (незакрытого) периода. Поэтому изменение полностью обратимо через
        revert_group_model. Возвращает {'group': <dict>, 'changed': bool}."""
        group = self.get_group(group_id)
        if not group:
            raise ValueError("Group not found")
        new_code = normalize_calculation_model_code(new_model_code, group.get('direction_name'))
        if new_code not in CALCULATION_MODEL_ALLOWED:
            raise ValueError("Unknown calculation model: {}".format(new_model_code))
        old_code = group.get('calculation_model_code')
        if new_code == old_code:
            return {'group': group, 'changed': False}
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO group_model_change_log
                    (group_id, old_model_code, new_model_code, changed_by, is_revert, created_at)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                (int(group_id), old_code, new_code, changed_by, bool(is_revert)),
            )
            cursor.execute(
                """
                UPDATE groups SET calculation_model_code = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (new_code, int(group_id)),
            )
        return {'group': self.get_group(group_id), 'changed': True}

    def get_group_model_history(self, group_id, limit=50):
        """Журнал смены модели группы (новые сверху) для показа/отката в UI."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT l.id, l.old_model_code, l.new_model_code, l.is_revert,
                       l.created_at, l.changed_by, u.name
                FROM group_model_change_log l
                LEFT JOIN users u ON u.id = l.changed_by
                WHERE l.group_id = %s
                ORDER BY l.id DESC
                LIMIT %s
                """,
                (int(group_id), int(limit)),
            )
            rows = cursor.fetchall() or []

        def _name(code):
            return (CALCULATION_MODEL_DESCRIPTIONS.get(code) or {}).get('name') or code

        return [
            {
                'id': int(r[0]),
                'old_model_code': r[1],
                'old_model_name': _name(r[1]) if r[1] else None,
                'new_model_code': r[2],
                'new_model_name': _name(r[2]) if r[2] else None,
                'is_revert': bool(r[3]),
                'created_at': r[4].isoformat() if r[4] else None,
                'changed_by': r[5],
                'changed_by_name': r[6],
            }
            for r in rows
        ]

    def revert_group_model(self, group_id, target_model_code=None, changed_by=None):
        """Откат модели группы. Без target_model_code возвращает модель, которая была
        ДО последнего изменения (old_model_code последней записи журнала)."""
        if target_model_code is None:
            with self._get_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT old_model_code FROM group_model_change_log
                    WHERE group_id = %s ORDER BY id DESC LIMIT 1
                    """,
                    (int(group_id),),
                )
                row = cursor.fetchone()
            if not row or not row[0]:
                raise ValueError("No model changes to revert")
            target_model_code = row[0]
        return self.change_group_model(
            group_id, target_model_code, changed_by=changed_by, is_revert=True
        )

    def _group_effective_direction_id_tx(self, cursor, group_id):
        """Действующее направление группы (_GROUP_EFFECTIVE_DIRECTION_SQL) или None."""
        cursor.execute(
            "SELECT " + self._GROUP_EFFECTIVE_DIRECTION_SQL
            + " AS effective_direction_id FROM groups g WHERE g.id = %s",
            (int(group_id),),
        )
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def _sync_operator_direction_from_group_tx(self, cursor, group_id, operator_id, changed_by=None):
        """Направление оператора следует за группой — так же, как супервайзер.

        До задачи #228 СВ, переводя оператора, сам менял ему направление. После неё
        СВ меняет только группу, и направление оставалось от прежней группы, пока
        его не поправит админ. Теперь перевод сразу проставляет действующее
        направление группы и пишет это в историю от имени того, кто перевёл.
        Группа без действующего направления прежнее направление не трогает; у
        тренеров и СВ направления по группе нет.

        Возвращает направление оператора после синхронизации.
        """
        cursor.execute(
            "SELECT role, direction_id FROM users WHERE id = %s",
            (int(operator_id),),
        )
        row = cursor.fetchone()
        if not row:
            return None
        role = str(row[0] or '').strip().lower()
        current_direction_id = int(row[1]) if row[1] is not None else None
        if role not in ('operator', 'trainee'):
            return current_direction_id
        target_direction_id = self._group_effective_direction_id_tx(cursor, group_id)
        if target_direction_id is None or target_direction_id == current_direction_id:
            return current_direction_id
        cursor.execute(
            "UPDATE users SET direction_id = %s WHERE id = %s",
            (target_direction_id, int(operator_id)),
        )
        cursor.execute(
            "UPDATE operator_profiles SET direction_id = %s WHERE user_id = %s",
            (target_direction_id, int(operator_id)),
        )
        cursor.execute(
            """
            INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
            VALUES (%s, %s, 'direction_id', %s, %s)
            """,
            (
                int(operator_id),
                changed_by,
                str(current_direction_id) if current_direction_id is not None else None,
                str(target_direction_id),
            ),
        )
        return target_direction_id

    def add_operator_to_group(self, group_id, operator_id, start_date=None, assigned_by=None,
                              sync_direction=True):
        """Добавляет оператора в группу с effective date, закрывая прошлую основную
        группу (один активный основной membership на оператора). users.supervisor_id
        оператора становится текущим СВ новой группы, users.direction_id — её
        действующим направлением (sync_direction=False, когда направление выбрано
        явно). Возвращает направление оператора после перевода или None, если
        направление не синхронизировалось."""
        with self._get_cursor() as cursor:
            return self._add_operator_to_group_tx(
                cursor, group_id, operator_id, start_date=start_date, assigned_by=assigned_by,
                sync_direction=sync_direction,
            )

    def _add_operator_to_group_tx(self, cursor, group_id, operator_id, start_date=None, assigned_by=None,
                                  sync_direction=True):
        """Тело add_operator_to_group внутри чужой транзакции: нужно операциям,
        которые заводят оператора в группу вместе с другими правками (понижение
        СВ до оператора), чтобы всё легло одним коммитом."""
        cursor.execute(
            """
            UPDATE group_operator_memberships
            SET end_date = (COALESCE(%s::date, CURRENT_DATE) - INTERVAL '1 day')
            WHERE operator_id = %s AND end_date IS NULL AND group_id <> %s
            """,
            (start_date, int(operator_id), int(group_id)),
        )
        # Если уже есть открытый membership в этой группе — не дублируем.
        cursor.execute(
            """
            SELECT 1 FROM group_operator_memberships
            WHERE operator_id = %s AND group_id = %s AND end_date IS NULL
            """,
            (int(operator_id), int(group_id)),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """
                INSERT INTO group_operator_memberships
                    (group_id, operator_id, start_date, assigned_by, created_at)
                VALUES (%s, %s, COALESCE(%s::date, CURRENT_DATE), %s, CURRENT_TIMESTAMP)
                """,
                (int(group_id), int(operator_id), start_date, assigned_by),
            )
        self._set_operators_supervisor_tx(
            cursor, [operator_id], self._group_active_supervisor_id_tx(cursor, group_id)
        )
        direction_id = None
        if sync_direction:
            direction_id = self._sync_operator_direction_from_group_tx(
                cursor, group_id, operator_id, changed_by=assigned_by
            )
        # Оператора почти всегда заводят в группу позже, чем он в ней начал
        # работать: дни до start_date остались бы без группы и не попали бы в
        # «Учёт часов». Подбираем их сразу, а не до следующего рестарта.
        self._stamp_orphan_group_ids_tx(cursor, operator_id)
        return direction_id

    def remove_operator_from_group(self, group_id, operator_id, end_date=None):
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE group_operator_memberships
                SET end_date = COALESCE(%s::date, CURRENT_DATE)
                WHERE group_id = %s AND operator_id = %s AND end_date IS NULL
                """,
                (end_date, int(group_id), int(operator_id)),
            )
            # Без группы нет и СВ (если вдруг осталось другое открытое членство —
            # наследуем СВ оттуда).
            cursor.execute(
                """
                SELECT group_id FROM group_operator_memberships
                WHERE operator_id = %s AND end_date IS NULL
                ORDER BY start_date DESC, id DESC LIMIT 1
                """,
                (int(operator_id),),
            )
            row = cursor.fetchone()
            new_sv = self._group_active_supervisor_id_tx(cursor, row[0]) if row else None
            self._set_operators_supervisor_tx(cursor, [operator_id], new_sv)

    def update_group_membership_start_date(self, group_id, member_id, kind='operator',
                                           start_date=None):
        """Правит дату вступления в группу у ОТКРЫТОГО членства (оператора или СВ).

        Закрытые членства не трогаем — меняется только «сейчас действующее»:
        - у оператора открытое членство ровно одно, поэтому прошлое, закрытое встык
          (end_date = старт − 1 день), двигается вместе — в таймлайне не появляется
          ни дыры, ни нахлёста;
        - сдвиг по дням с УЖЕ учтёнными часами запрещён, если эти дни уже
          принадлежат другой группе. Расширение членства назад на дни, когда
          оператор был без группы, разрешено — см.
          _assert_no_operator_hours_in_window_tx (защита от опечатки в дате);
        - daily_hours на сдвинутом отрезке перестамповываются на нужную группу,
          затронутые месяцы переагрегируются (как в reassign_operator_history) —
          иначе они остались бы на прежней группе или без группы;
        - у СВ членств может быть несколько (ведёт разные группы), цепочки нет —
          только проверка, что не наезжаем на прошлое членство в этой же группе.
        """
        kind = (kind or 'operator').strip().lower()
        if kind not in ('operator', 'supervisor'):
            raise ValueError("kind must be 'operator' or 'supervisor'")
        if isinstance(start_date, str):
            try:
                start_date = datetime.strptime(start_date.strip(), '%Y-%m-%d').date()
            except ValueError:
                raise ValueError("Некорректная дата (ожидается YYYY-MM-DD)")
        if not isinstance(start_date, date):
            raise ValueError("Дата обязательна")
        if start_date > date.today():
            raise ValueError("Дата вступления не может быть в будущем")
        group_id = int(group_id)
        member_id = int(member_id)
        is_op = kind == 'operator'
        table = 'group_operator_memberships' if is_op else 'group_supervisor_memberships'
        col = 'operator_id' if is_op else 'supervisor_id'
        months = []
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT id, start_date FROM " + table + """ m
                WHERE m.group_id = %s AND m.""" + col + """ = %s AND m.end_date IS NULL
                ORDER BY m.start_date DESC, m.id DESC LIMIT 1""",
                (group_id, member_id),
            )
            row = cursor.fetchone()
            if not row:
                raise ValueError("Активное членство в этой группе не найдено")
            membership_id, old_start = int(row[0]), row[1]
            if old_start == start_date:
                return {
                    'changed': False, 'old_start_date': str(old_start),
                    'start_date': str(start_date), 'reaggregated_months': [],
                }
            # Предыдущее закрытое членство: у оператора — по любой группе (это звено
            # той же цепочки), у СВ — только по этой же группе.
            cursor.execute(
                "SELECT id, group_id, start_date, end_date FROM " + table + """ m
                WHERE m.""" + col + """ = %s AND m.id <> %s AND m.end_date IS NOT NULL
                  AND m.end_date < %s AND (%s OR m.group_id = %s)
                ORDER BY m.end_date DESC, m.id DESC LIMIT 1""",
                (member_id, membership_id, old_start, is_op, group_id),
            )
            prev = cursor.fetchone()
            prev_group_id = int(prev[1]) if prev else None
            # Прошлое членство, закрытое встык, двигается вместе со стартом — это
            # звено той же цепочки, а не соседний интервал, на который мы наезжаем.
            move_prev = bool(prev) and is_op and prev[3] == old_start - timedelta(days=1)
            if prev and start_date <= prev[2]:
                raise ValueError("Дата раньше начала предыдущего членства")
            if prev and not move_prev and start_date <= prev[3]:
                raise ValueError("Дата пересекается с предыдущим членством")

            # Отрезок, который сдвиг границы перевешивает на другую группу.
            lo = min(old_start, start_date)
            hi = max(old_start, start_date) - timedelta(days=1)
            moving_backward = start_date < old_start
            # Источник и получатель отрезка определяются членствами, а не
            # наличием daily_hours. При движении назад без смежного прошлого
            # членства источник None: это действительно дни «без группы», и их
            # можно забрать в текущую группу даже при уже посчитанных часах.
            if moving_backward:
                source_group_id = prev_group_id if move_prev else None
                target_group_id = group_id
            else:
                source_group_id = group_id
                target_group_id = prev_group_id if move_prev else None
            # Всё, что пишем, — только после валидации. Реальный перенос часов
            # между группами (или из группы в «без группы») остаётся запрещён.
            if is_op and source_group_id is not None and source_group_id != target_group_id:
                self._assert_no_operator_hours_in_window_tx(cursor, member_id, lo, hi)

            if move_prev:
                cursor.execute(
                    "UPDATE " + table + " SET end_date = %s WHERE id = %s",
                    (start_date - timedelta(days=1), int(prev[0])),
                )
            cursor.execute(
                "UPDATE " + table + " SET start_date = %s WHERE id = %s",
                (start_date, membership_id),
            )
            if is_op:
                # Сдвиг назад — дни отрезка забирает эта группа; вперёд — отдаёт
                # смежной прежней группе (или никому, если смежной группы нет).
                cursor.execute(
                    """SELECT DISTINCT TO_CHAR(day, 'YYYY-MM') FROM daily_hours
                       WHERE operator_id = %s AND day >= %s AND day <= %s ORDER BY 1""",
                    (member_id, lo, hi),
                )
                months = [m[0] for m in (cursor.fetchall() or [])]
                cursor.execute(
                    """UPDATE daily_hours SET group_id = %s
                       WHERE operator_id = %s AND day >= %s AND day <= %s""",
                    (target_group_id, member_id, lo, hi),
                )
                for m in months:
                    self._aggregate_month_from_daily_tx(cursor, member_id, m)
            else:
                self._sync_group_operators_supervisor_tx(cursor, group_id)
        return {
            'changed': True,
            'old_start_date': str(old_start),
            'start_date': str(start_date),
            'reaggregated_months': months,
        }

    def get_survey_assignable_groups(self, requester_id, requester_role, scope_department_id=None):
        """
        Группы с текущим составом операторов — для назначения теста «на группу».
        Отдаётся вместе со списком опросов, чтобы фронт не делал второй запрос.
        """
        role = self._normalize_survey_role(requester_role)
        if not (role_has_min(role, 'admin') or role in ('sv', 'trainer')):
            return []

        with self._get_cursor() as cursor:
            visible_operator_ids = set(self._get_visible_operator_ids_for_requester_tx(
                cursor,
                requester_id,
                role,
                scope_department_id=scope_department_id
            ))
            if not visible_operator_ids:
                return []

            params = [list(visible_operator_ids)]
            department_filter = ""
            if scope_department_id is not None and not role_has_min(role, 'admin'):
                department_filter = "AND (g.department_id IS NULL OR g.department_id = %s)"
                params.append(int(scope_department_id))

            cursor.execute(f"""
                SELECT
                    g.id,
                    g.name,
                    g.department_id,
                    g.direction_id,
                    ARRAY_AGG(gom.operator_id ORDER BY gom.operator_id)
                FROM groups g
                JOIN group_operator_memberships gom
                    ON gom.group_id = g.id AND gom.end_date IS NULL
                WHERE g.status = 'active'
                  AND gom.operator_id = ANY(%s)
                  {department_filter}
                GROUP BY g.id, g.name, g.department_id, g.direction_id
                ORDER BY g.name
            """, tuple(params))

            return [
                {
                    'id': int(row[0]),
                    'name': row[1],
                    'department_id': int(row[2]) if row[2] is not None else None,
                    'direction_id': int(row[3]) if row[3] is not None else None,
                    'operator_ids': self._survey_int_id_list(row[4] or [])
                }
                for row in cursor.fetchall()
            ]
