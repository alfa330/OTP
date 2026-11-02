"""База данных: отделы и направления.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    json,
    normalize_calculation_model_code,
)


class DepartmentsMixin:
    def get_directions(self, department_id=None):
        """Получить активные направления. Если задан department_id — только этого отдела."""
        with self._get_cursor() as cursor:
            if department_id is not None:
                cursor.execute("""
                    SELECT d.id, d.name, d.has_file_upload, d.criteria, d.is_active, d.calculation_model_code, d.department_id, dep.code
                    FROM directions d
                    LEFT JOIN departments dep ON dep.id = d.department_id
                    WHERE d.is_active = TRUE AND d.department_id = %s
                    ORDER BY d.name
                """, (department_id,))
            else:
                cursor.execute("""
                    SELECT d.id, d.name, d.has_file_upload, d.criteria, d.is_active, d.calculation_model_code, d.department_id, dep.code
                    FROM directions d
                    LEFT JOIN departments dep ON dep.id = d.department_id
                    WHERE d.is_active = TRUE  -- Added filter for performance
                    ORDER BY d.name
                """)
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "hasFileUpload": row[2],
                    "criteria": row[3],
                    "isActive": row[4],
                    "calculationModelCode": normalize_calculation_model_code(row[5], row[1]),
                    "calculation_model_code": normalize_calculation_model_code(row[5], row[1]),
                    "departmentId": row[6],
                    "department_id": row[6],
                    "departmentCode": row[7],
                    "department_code": row[7]
                } for row in cursor.fetchall()
            ]

    # ── Department CRUD ──────────────────────────────────────────────

    @staticmethod
    def _department_row_to_dict(r):
        return {
            "id": r[0], "code": r[1], "slug": r[2], "name": r[3],
            "description": r[4], "is_active": r[5],
            "head_user_id": r[6], "head_name": r[7],
            "operator_field_schema": r[8] if r[8] is not None else [],
            # Тумблер раздела «Вики». COALESCE в запросах: на базе, где схема
            # вики ещё не разворачивалась, колонки нет — раздел считаем выданным.
            "wiki_enabled": True if len(r) < 10 or r[9] is None else bool(r[9]),
        }

    def get_departments(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT d.id, d.code, d.slug, d.name, d.description, d.is_active,
                       d.head_user_id, h.name, d.operator_field_schema,
                       COALESCE(d.wiki_enabled, TRUE)
                FROM departments d
                LEFT JOIN users h ON h.id = d.head_user_id
                ORDER BY d.name
            """)
            return [self._department_row_to_dict(r) for r in cursor.fetchall()]

    def get_department_by_id(self, department_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT d.id, d.code, d.slug, d.name, d.description, d.is_active,
                       d.head_user_id, h.name, d.operator_field_schema,
                       COALESCE(d.wiki_enabled, TRUE)
                FROM departments d
                LEFT JOIN users h ON h.id = d.head_user_id
                WHERE d.id = %s
            """, (department_id,))
            r = cursor.fetchone()
            if not r:
                return None
            return self._department_row_to_dict(r)

    def create_department(self, code, name, description=None, slug=None):
        if not slug:
            slug = code
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO departments (code, slug, name, description)
                VALUES (%s, %s, %s, %s)
                RETURNING id, code, slug, name, description, is_active, head_user_id, operator_field_schema
            """, (code, slug, name, description))
            r = cursor.fetchone()
            return {
                "id": r[0], "code": r[1], "slug": r[2], "name": r[3],
                "description": r[4], "is_active": r[5],
                "head_user_id": r[6], "head_name": None,
                "operator_field_schema": r[7] if r[7] is not None else [],
            }

    def update_department(self, department_id, **kwargs):
        allowed = {'code', 'slug', 'name', 'description', 'is_active',
                   'operator_field_schema', 'wiki_enabled'}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return self.get_department_by_id(department_id)
        from psycopg2 import sql as _sql
        from psycopg2.extras import Json as _Json
        set_clause = _sql.SQL(', ').join(
            _sql.SQL("{} = %s").format(_sql.Identifier(k)) for k in updates
        )
        params = [
            _Json(v) if k == 'operator_field_schema' else v
            for k, v in updates.items()
        ]
        with self._get_cursor() as cursor:
            cursor.execute(
                _sql.SQL("UPDATE departments SET {} WHERE id = %s RETURNING id").format(set_clause),
                params + [department_id]
            )
            if cursor.fetchone() is None:
                return None
        return self.get_department_by_id(department_id)

    def set_department_head(self, department_id, user_id, changed_by=None):
        """Назначить/снять главу отдела с записью в журнал истории.

        user_id=None снимает текущего главу. Денормализованный «текущий глава»
        хранится в departments.head_user_id, полная история — в
        department_head_assignments. Возвращает обновлённый отдел или None.
        """
        new_head = int(user_id) if user_id not in (None, '') else None
        with self._get_cursor() as cursor:
            cursor.execute("SELECT head_user_id FROM departments WHERE id = %s", (department_id,))
            row = cursor.fetchone()
            if not row:
                return None
            current_head = row[0]
            if current_head != new_head:
                cursor.execute(
                    "UPDATE departments SET head_user_id = %s WHERE id = %s",
                    (new_head, department_id)
                )
                if current_head is not None:
                    cursor.execute("""
                        INSERT INTO department_head_assignments (department_id, user_id, action, changed_by)
                        VALUES (%s, %s, 'unassigned', %s)
                    """, (department_id, current_head, changed_by))
                if new_head is not None:
                    cursor.execute("""
                        INSERT INTO department_head_assignments (department_id, user_id, action, changed_by)
                        VALUES (%s, %s, 'assigned', %s)
                    """, (department_id, new_head, changed_by))
        return self.get_department_by_id(department_id)

    def get_department_head_history(self, department_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT a.id, a.user_id, u.name, a.action, a.changed_by, cb.name, a.created_at
                FROM department_head_assignments a
                LEFT JOIN users u ON u.id = a.user_id
                LEFT JOIN users cb ON cb.id = a.changed_by
                WHERE a.department_id = %s
                ORDER BY a.created_at DESC, a.id DESC
            """, (department_id,))
            return [
                {
                    "id": r[0],
                    "user_id": r[1],
                    "user_name": r[2],
                    "action": r[3],
                    "changed_by": r[4],
                    "changed_by_name": r[5],
                    "created_at": r[6].isoformat() if r[6] else None,
                }
                for r in cursor.fetchall()
            ]

    def headed_department_id_for_user(self, user_id):
        """Вернуть id отдела, главой которого является пользователь, иначе None.

        Отделов несколько — первый по _HEADED_DEPARTMENTS_ORDER_SQL: свой отдел
        главы, а без него первый по названию.
        """
        if not user_id:
            return None
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id
                FROM departments
                WHERE head_user_id = %s
                  AND COALESCE(is_active, TRUE) = TRUE
                ORDER BY """ + self._HEADED_DEPARTMENTS_ORDER_SQL + """
                LIMIT 1
            """, (user_id, user_id))
            r = cursor.fetchone()
            return r[0] if r else None

    def get_headed_departments_for_user(self, user_id):
        """Вернуть id, название и код всех активных возглавляемых отделов.

        Порядок тот же, что у headed_department_id_for_user: первая строка —
        тот самый «первый» отдел.
        """
        if not user_id:
            return []
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, name, code
                FROM departments
                WHERE head_user_id = %s
                  AND COALESCE(is_active, TRUE) = TRUE
                ORDER BY """ + self._HEADED_DEPARTMENTS_ORDER_SQL + """
            """, (user_id, user_id))
            return [
                {"id": int(row[0]), "name": row[1] or "", "code": row[2] or ""}
                for row in (cursor.fetchall() or [])
                if row and row[0] is not None
            ]

    def get_user_department_id(self, user_id):
        """Лёгкий доступ к users.department_id без позиционного кортежа get_user."""
        if not user_id:
            return None
        with self._get_cursor() as cursor:
            cursor.execute("SELECT department_id FROM users WHERE id = %s", (user_id,))
            r = cursor.fetchone()
            return r[0] if r else None

    def get_user_department(self, user_id):
        """Вернуть (department_id, department_code) пользователя одним запросом."""
        if not user_id:
            return (None, None)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.department_id, d.code
                FROM users u
                LEFT JOIN departments d ON d.id = u.department_id
                WHERE u.id = %s
            """, (user_id,))
            r = cursor.fetchone()
            return (r[0], r[1]) if r else (None, None)

    def get_user_direction_model(self, user_id):
        """Код модели расчёта направления сотрудника ('chat_manager', 'tez_op', …).

        Отдельным запросом, а не колонкой в get_user: тот возвращает КОРТЕЖ и
        читается по индексам в десятках мест — расширять его ради одного поля
        нельзя (та же причина, что у get_user_hr_card).

        Нужен разделу «Чаты водителей»: чат-менеджеру пункт меню не показывается,
        а опознаётся он именно моделью — направления версионируются, и id 69 лишь
        текущая версия «Чат менеджера», тогда как модель переживает и новую
        версию, и переименование.
        """
        if not user_id:
            return None
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT dir.calculation_model_code
                  FROM users u
                  JOIN directions dir ON dir.id = u.direction_id
                 WHERE u.id = %s
            """, (user_id,))
            row = cursor.fetchone()
            return row[0] if row else None

    def department_wiki_enabled(self, department_id):
        """Выдан ли отделу раздел «Вики». Без отдела — выдан.

        Колонку заводит схема раздела (wiki/schema.py). Отсутствие колонки на
        ещё не мигрированной базе трактуем как «выдан»: раздел уже открыт всем,
        и миграция не должна отбирать его молча.
        """
        if not department_id:
            return True
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(wiki_enabled, TRUE) FROM departments WHERE id = %s",
                (department_id,),
            )
            row = cursor.fetchone()
            return bool(row[0]) if row else True

    def department_has_wiki_space(self, department_ids, user_id=None):
        """Есть ли хоть одно пространство вики, выданное этим отделам.

        Тумблер departments.wiki_enabled отвечает на вопрос «раздел выдан?», а
        это — на «есть ли в нём для отдела хоть что-нибудь». Без второй проверки
        Тез КЦ, не указанный ни в одном пространстве, видел раздел в меню и
        открывал пустую вику: тумблер у него включён, а содержимого нет и быть
        не может.

        ГОСТЬ — второй законный ответ «да» (решение владельца 25.08.2026).
        Гостевой доступ выдают любому сотруднику компании, в том числе из отдела,
        которому вика не предназначена вовсе; периметр такого человека сервер
        считает как гостевой (wiki/queries.py: исключение в _SPACE_GATE_SQL), но
        пункта меню он без этой ветки не увидит — и придёт к разделу разве что
        по прямой ссылке. Признак живёт ровно столько, сколько выдача.

        Пустой список отделов у пространства = видно всем, соглашение то же, что
        в периметре (wiki/queries.py). Нет таблицы (база до миграции) — считаем,
        что пространство есть: молча отбирать раздел на неразвёрнутой схеме
        нельзя.
        """
        wanted = sorted({int(x) for x in (department_ids or []) if x})
        if not wanted:
            return True
        with self._get_cursor() as cursor:
            try:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM wiki_spaces sp
                         WHERE sp.status = 'active'
                           AND (NOT EXISTS (SELECT 1 FROM wiki_space_departments sd
                                             WHERE sd.space_id = sp.id)
                                OR EXISTS (SELECT 1 FROM wiki_space_departments sd
                                            WHERE sd.space_id = sp.id
                                              AND sd.department_id = ANY(%(depts)s)))
                    ) OR EXISTS (
                        SELECT 1 FROM wiki_guest_access g
                         WHERE %(user)s::int IS NOT NULL
                           AND g.user_id = %(user)s::int
                           AND g.revoked_at IS NULL
                           AND g.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    )
                    """,
                    {'depts': wanted, 'user': user_id},
                )
            except Exception:
                return True
            row = cursor.fetchone()
            return bool(row[0]) if row else True

    def set_department_wiki_enabled(self, department_id, enabled):
        """Включить/выключить раздел «Вики» для отдела."""
        with self._get_cursor() as cursor:
            cursor.execute(
                "UPDATE departments SET wiki_enabled = %s WHERE id = %s",
                (bool(enabled), department_id),
            )
            return cursor.rowcount > 0

    def get_department_scale_direction_ids(self, department_id):
        """Живые направления отдела, по которым размечены звонки из АТС
        (imported_calls.qa_direction_id) — шкалы без своих операторов.

        Скоупу СВ в «ИИ-оценке» их не дают группы: операторы обзвона остаются в
        основном направлении, и без этого списка СВ не открыл бы по такой шкале ни
        фильтр, ни разборы, ни настройку критериев. Один EXISTS на направление — по
        частичному индексу idx_imported_calls_qa_direction."""
        if department_id is None:
            return []
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT d.id
                  FROM directions d
                 WHERE d.department_id = %s
                   AND d.is_active = TRUE
                   AND d.canonical_id IS NULL
                   AND EXISTS (SELECT 1 FROM imported_calls ic
                                WHERE ic.qa_direction_id = d.id)
                 ORDER BY d.id
            """, (int(department_id),))
            return [int(row[0]) for row in (cursor.fetchall() or [])]

    def get_department_member_ids(self, department_id):
        """Множество id пользователей, принадлежащих отделу (для скоупа главы)."""
        if not department_id:
            return set()
        with self._get_cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE department_id = %s", (department_id,))
            return {r[0] for r in cursor.fetchall()}

    def get_direction_department_id(self, direction_id):
        """Вернуть department_id направления (для проверки принадлежности отделу)."""
        if not direction_id:
            return None
        with self._get_cursor() as cursor:
            cursor.execute("SELECT department_id FROM directions WHERE id = %s", (direction_id,))
            r = cursor.fetchone()
            return r[0] if r else None

    def assign_user_department(self, user_id, department_id):
        with self._get_cursor() as cursor:
            cursor.execute("UPDATE users SET department_id = %s WHERE id = %s RETURNING id", (department_id, user_id))
            return cursor.fetchone() is not None

    def get_department_monthly_plan(self, department_id, year, month):
        """Общий месячный план направления/отдела (план успешек на 1 FTE)."""
        from tez.department_plan import default_norm_hours
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT department_id, year, month, plan_per_fte, updated_by, updated_at, norm_hours_fte
                FROM department_monthly_plans
                WHERE department_id = %s AND year = %s AND month = %s
                """,
                (int(department_id), int(year), int(month))
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            'department_id': int(row[0]),
            'year': int(row[1]),
            'month': int(row[2]),
            'plan_per_fte': float(row[3] or 0),
            'norm_hours_fte': float(row[6]) if row[6] is not None else default_norm_hours(year, month),
            'updated_by': int(row[4]) if row[4] is not None else None,
            'updated_at': row[5].isoformat() if hasattr(row[5], 'isoformat') else (str(row[5]) if row[5] else None),
        }

    def upsert_department_monthly_plan(self, department_id, year, month, plan_per_fte, updated_by=None, norm_hours_fte=None):
        """Создать/обновить общий месячный план отдела (upsert по department_id+year+month)."""
        from tez.department_plan import default_norm_hours
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO department_monthly_plans
                    (department_id, year, month, plan_per_fte, updated_by, updated_at, norm_hours_fte)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
                ON CONFLICT (department_id, year, month) DO UPDATE SET
                    plan_per_fte = EXCLUDED.plan_per_fte,
                    norm_hours_fte = COALESCE(EXCLUDED.norm_hours_fte, department_monthly_plans.norm_hours_fte),
                    updated_by = EXCLUDED.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING department_id, year, month, plan_per_fte, updated_by, updated_at, norm_hours_fte
                """,
                (
                    int(department_id), int(year), int(month),
                    float(plan_per_fte or 0),
                    int(updated_by) if updated_by is not None else None,
                    float(norm_hours_fte) if norm_hours_fte is not None else None
                )
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            'department_id': int(row[0]),
            'year': int(row[1]),
            'month': int(row[2]),
            'plan_per_fte': float(row[3] or 0),
            'norm_hours_fte': float(row[6]) if row[6] is not None else default_norm_hours(year, month),
            'updated_by': int(row[4]) if row[4] is not None else None,
            'updated_at': row[5].isoformat() if hasattr(row[5], 'isoformat') else (str(row[5]) if row[5] else None),
        }

    def save_directions(self, directions, scope_department_id=None):
        """Сохранить направления, сопоставляя записи по id (стабильная идентичность).

        Живая строка направления держит свой id навсегда, поэтому правки не
        отвязывают операторов/группы и внешние связки по direction_id (ИИ-оценка,
        rollout-конфиги, база знаний):
        - имя / файл / модель расчёта меняются НА МЕСТЕ;
        - изменение критериев уводит старую шкалу в архивную строку
          (is_active=FALSE, canonical_id -> живая строка) и перевешивает на неё
          исторические оценки (calls, калибровки, «Моя оценка» ИИ-оценки) —
          старые оценки продолжают отображаться по той шкале, по которой
          ставились (баллы позиционные);
        - запись без id (legacy-клиент) матчится по имени среди активных
          направлений скоупа, иначе создаётся новое направление (version=1);
        - активные направления скоупа, отсутствующие в списке, деактивируются,
          их операторы отвязываются (direction_id = NULL) — это удаление.

        Если задан scope_department_id — операция ограничена этим отделом:
        глава отдела (или админ, редактирующий конкретный отдел) не может
        затронуть направления других отделов; чужой id в списке — ошибка.
        """
        scoped = scope_department_id is not None
        with self._get_cursor() as cursor:
            # 1. Текущие активные направления (в рамках отдела, если задан scope)
            if scoped:
                cursor.execute("""
                    SELECT id, name, has_file_upload, criteria, version, calculation_model_code, department_id
                    FROM directions
                    WHERE is_active = TRUE AND department_id = %s
                """, (scope_department_id,))
            else:
                cursor.execute("""
                    SELECT id, name, has_file_upload, criteria, version, calculation_model_code, department_id
                    FROM directions
                    WHERE is_active = TRUE
                """)
            existing_by_id = {
                row[0]: {
                    "id": row[0],
                    "name": row[1],
                    "has_file_upload": row[2],
                    "criteria": json.dumps(row[3] or [], ensure_ascii=False, sort_keys=True),
                    "version": row[4],
                    "calculation_model_code": normalize_calculation_model_code(row[5], row[1]),
                    "department_id": row[6]
                } for row in cursor.fetchall()
            }
            existing_by_name = {row["name"]: row for row in existing_by_id.values()}

            # 2. Классификация входящих записей
            seen_ids = set()
            names_in_payload = set()
            in_place_updates = []   # (name, has_file_upload, model_code, id)
            criteria_changes = []   # (existing, name, has_file_upload, criteria_json, model_code)
            inserts = []            # (name, has_file_upload, criteria_json, model_code)

            for direction in directions:
                name = str(direction.get('name') or '').strip()
                if not name:
                    continue
                if name in names_in_payload:
                    raise ValueError(f"направление «{name}» указано в списке дважды")
                names_in_payload.add(name)
                has_file_upload = bool(direction.get('hasFileUpload', True))
                criteria = json.dumps(direction.get('criteria') or [], ensure_ascii=False, sort_keys=True)
                calculation_model_code = normalize_calculation_model_code(
                    direction.get('calculationModelCode') or direction.get('calculation_model_code'),
                    name
                )

                raw_id = direction.get('id')
                if raw_id in (None, ''):
                    existing = existing_by_name.get(name)
                else:
                    try:
                        raw_id = int(raw_id)
                    except (TypeError, ValueError):
                        raise ValueError(f"некорректный id направления: {raw_id!r}")
                    existing = existing_by_id.get(raw_id)
                    if existing is None:
                        raise ValueError(
                            f"направление id={raw_id} не найдено среди активных направлений отдела")

                if existing is None:
                    inserts.append((name, has_file_upload, criteria, calculation_model_code))
                    continue

                if existing["id"] in seen_ids:
                    raise ValueError(f"направление id={existing['id']} указано в списке дважды")
                seen_ids.add(existing["id"])

                criteria_changed = existing["criteria"] != criteria
                settings_changed = (existing["name"] != name or
                                    existing["has_file_upload"] != has_file_upload or
                                    existing["calculation_model_code"] != calculation_model_code)
                if criteria_changed:
                    criteria_changes.append((existing, name, has_file_upload, criteria, calculation_model_code))
                elif settings_changed:
                    in_place_updates.append((name, has_file_upload, calculation_model_code, existing["id"]))

            # 3. Правки на месте (переименование/файл/модель): id стабилен, привязки целы
            for name, has_file_upload, model_code, direction_id in in_place_updates:
                cursor.execute("""
                    UPDATE directions
                    SET name = %s, has_file_upload = %s, calculation_model_code = %s
                    WHERE id = %s
                """, (name, has_file_upload, model_code, direction_id))

            # 4. Смена критериев: старая шкала — в архивную строку, живая строка
            #    сохраняет id; исторические оценки перевешиваются на архив, потому
            #    что их баллы позиционно привязаны к старому списку критериев.
            for existing, name, has_file_upload, criteria, model_code in criteria_changes:
                cursor.execute("""
                    INSERT INTO directions (
                        name, has_file_upload, criteria, calculation_model_code,
                        version, previous_version_id, department_id, is_active, canonical_id
                    )
                    SELECT name, has_file_upload, criteria, calculation_model_code,
                           version, previous_version_id, department_id, FALSE, id
                    FROM directions WHERE id = %s
                    RETURNING id
                """, (existing["id"],))
                archive_id = cursor.fetchone()[0]
                for frozen_table in ("calls", "calibration_rooms", "calibration_room_calls"):
                    cursor.execute(
                        f"UPDATE {frozen_table} SET direction_id = %s WHERE direction_id = %s",
                        (archive_id, existing["id"]))
                # «Моя оценка» карточки ИИ-оценки тоже позиционная, а версии шкалы у неё,
                # кроме direction_id, нет: без перевеса после «Переоценить» её вердикты
                # легли бы на новые критерии. Таблица — схемы call_qa, на голой базе её
                # может не быть.
                cursor.execute("SELECT to_regclass('public.ai_human_reviews') IS NOT NULL")
                if cursor.fetchone()[0]:
                    cursor.execute(
                        "UPDATE ai_human_reviews SET direction_id = %s WHERE direction_id = %s",
                        (archive_id, existing["id"]))
                cursor.execute("""
                    UPDATE directions
                    SET name = %s, has_file_upload = %s, criteria = %s,
                        calculation_model_code = %s, version = version + 1,
                        previous_version_id = %s
                    WHERE id = %s
                """, (name, has_file_upload, criteria, model_code, archive_id, existing["id"]))

            # 5. Новые направления
            if inserts:
                cursor.executemany("""
                    INSERT INTO directions (
                        name, has_file_upload, criteria, calculation_model_code,
                        version, previous_version_id, department_id
                    )
                    VALUES (%s, %s, %s, %s, 1, NULL, %s)
                """, [(n, h, c, m, scope_department_id) for n, h, c, m in inserts])

            # 6. Удаление: активные направления скоупа, которых нет в новом списке
            removed_ids = [direction_id for direction_id in existing_by_id if direction_id not in seen_ids]
            if removed_ids:
                cursor.execute("""
                    UPDATE directions SET is_active = FALSE WHERE id = ANY(%s)
                """, (removed_ids,))
                cursor.execute("""
                    UPDATE users SET direction_id = NULL WHERE direction_id = ANY(%s)
                """, (removed_ids,))

    @classmethod
    def _department_filter(cls, department):
        """Разбор фильтра отдела: id числом, 'none' (без отдела) или None (все).

        Разборщик один на все места намеренно. Фильтр и список отделов для
        пикера обязаны понимать значение ОДИНАКОВО: разъехавшись, они дали бы
        пустой пикер ровно тогда, когда отдел выбран, и снять фильтр стало бы
        нечем. Чужое значение здесь игнорируется, как незнакомая роль или
        устройство, — отбивает его ручка, до базы оно не доезжает.
        """
        if department is None or isinstance(department, bool):
            return None
        if isinstance(department, int):
            return department
        text = str(department).strip().lower()
        if text in ('', 'all'):
            return None
        if text == cls.ACTIVE_SESSION_DEPARTMENT_NONE:
            return cls.ACTIVE_SESSION_DEPARTMENT_NONE
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def _departments_from_json(value):
        """Список отделов пикера из JSON_AGG. Чужого в нём быть не может, но
        пустая выборка приезжает не списком, а нулём из строки-заглушки."""
        rows = value if isinstance(value, list) else []
        return [
            {'department_id': row.get('department_id'),
             'department_name': row.get('department_name')}
            for row in rows if isinstance(row, dict)
        ]

    def _get_direction_name_map_tx(self, cursor, direction_ids):
        normalized_ids = self._coerce_int_list(direction_ids)
        if not normalized_ids:
            return {}
        cursor.execute("""
            SELECT id, name
            FROM directions
            WHERE id = ANY(%s)
        """, (normalized_ids,))
        return {int(row[0]): row[1] for row in cursor.fetchall()}

    def _is_smz_direction(self, direction_name):
        key = self._normalize_direction_key(direction_name)
        if not key:
            return False
        # Поддерживаем варианты вида "СМЗ 1", "smz team" и смешанную запись "sмz".
        return ('смз' in key) or ('smz' in key) or ('sмz' in key)

    def _is_line_direction(self, direction_name):
        key = self._normalize_direction_key(direction_name)
        if not key:
            return False
        # Поддерживаем варианты вида "Основа 1", "Линия 2", "osnova team", "line 2".
        return ('основа' in key) or ('osnova' in key) or ('линия' in key) or ('line' in key)

    def _get_operator_direction_name_tx(self, cursor, operator_id):
        cursor.execute(
            """
            SELECT d.name
            FROM users u
            LEFT JOIN directions d ON d.id = u.direction_id
            WHERE u.id = %s
            LIMIT 1
            """,
            (int(operator_id),)
        )
        row = cursor.fetchone()
        return row[0] if row else None
