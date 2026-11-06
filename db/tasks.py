"""База данных: задачи.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _TASK_RECIPIENT_ROLES_SQL,
    _UNSET,
    calendar,
    datetime,
    defaultdict,
    Json,
    normalize_role_value,
    role_has_min,
    TASK_PERSONAL_SCOPE_ROLES,
    task_production,
    task_production_queries,
    task_projects,
    TASK_TAG_CODES,
    timedelta,
    ZoneInfo,
)


class TasksMixin:
    @staticmethod
    def _task_assignee_tuples(assignees):
        """Состав исполнителей в едином виде [(user_id, role, supervisor_id), ...].

        Принимает и готовый список кортежей, и просто список id, и одиночный id:
        у части вызывающих под рукой только id, и заставлять их собирать кортежи
        значило бы плодить одинаковые заглушки по всему файлу.
        """
        if assignees is None:
            return []
        if isinstance(assignees, (int, float, str)):
            return [(int(assignees), None, None)]
        rows = []
        for item in assignees:
            if item is None:
                continue
            if isinstance(item, (int, float, str)):
                rows.append((int(item), None, None))
                continue
            values = list(item)
            if not values or values[0] is None:
                continue
            values += [None, None]
            rows.append((int(values[0]), values[1], values[2]))
        return rows

    def _sync_task_assignees_tx(self, cursor, task_id, assignee_ids, added_by=None):
        """Состав исполнителей задачи = ровно переданный список, в его порядке.

        Единственное место, где держится инвариант раздела: tasks.assigned_to —
        это строка состава с наименьшим position. И колонку, и связь пишем здесь,
        чтобы «первый исполнитель» не мог разойтись между ними.

        Уже существующие строки не пересоздаём, а только двигаем по порядку:
        added_at/added_by у оставшегося исполнителя должны сохраниться, иначе
        «когда его подключили» превратится в дату последней правки задачи.
        """
        task_id = int(task_id)
        ordered = [int(item) for item in (assignee_ids or [])]
        if not ordered:
            raise ValueError("ASSIGNEE_REQUIRED")

        cursor.execute(
            "DELETE FROM task_assignees WHERE task_id = %s AND NOT (user_id = ANY(%s))",
            (task_id, ordered),
        )
        # Одним запросом, а не циклом: WITH ORDINALITY даёт позицию прямо из
        # порядка массива, и десять исполнителей стоят один поход в базу.
        cursor.execute("""
            INSERT INTO task_assignees (task_id, user_id, position, added_by)
            SELECT %s, src.user_id, src.ord - 1, %s
              FROM unnest(%s::int[]) WITH ORDINALITY AS src(user_id, ord)
            ON CONFLICT (task_id, user_id) DO UPDATE
                SET position = EXCLUDED.position
        """, (task_id, added_by, ordered))
        return ordered

    def _task_assignee_ids_tx(self, cursor, task_id):
        """Id исполнителей задачи в порядке показа."""
        cursor.execute(
            "SELECT user_id FROM task_assignees WHERE task_id = %s ORDER BY position, user_id",
            (int(task_id),),
        )
        return [int(row[0]) for row in cursor.fetchall()]

    def _task_watcher_ids_tx(self, cursor, task_id):
        """Id наблюдателей задачи в порядке добавления."""
        cursor.execute(
            "SELECT user_id FROM task_watchers WHERE task_id = %s ORDER BY added_at, user_id",
            (int(task_id),),
        )
        return [int(row[0]) for row in cursor.fetchall()]

    def _task_assignee_scope_tx(self, cursor, task_id):
        """Исполнители с ролью и СВ — всё, что нужно для проверки видимости."""
        cursor.execute("""
            SELECT ta.user_id, u.role, u.supervisor_id
              FROM task_assignees ta
              JOIN users u ON u.id = ta.user_id
             WHERE ta.task_id = %s
             ORDER BY ta.position, ta.user_id
        """, (int(task_id),))
        return [(int(row[0]), row[1], row[2]) for row in cursor.fetchall()]

    def _task_visible_for_requester(self, requester_role, requester_id, created_by, assignees,
                                    requested_by=None):
        """Видит ли человек задачу.

        `assignees` — весь состав исполнителей (см. _task_assignee_tuples):
        задачу видит КАЖДЫЙ из них, а не только первый.
        """
        role = normalize_role_value(requester_role)
        requester_id = int(requester_id)
        people = self._task_assignee_tuples(assignees)
        if role_has_min(role, 'admin'):
            return True
        if created_by is not None and int(created_by) == requester_id:
            return True
        if any(person_id == requester_id for person_id, _role, _supervisor in people):
            return True
        # Поручитель принимает итог, значит должен видеть задачу — даже если карточку создал не он.
        if requested_by is not None and int(requested_by) == requester_id:
            return True
        # СВ видит задачу, если ХОТЯ БЫ ОДИН из исполнителей — его оператор.
        if role == 'sv' and any(
            person_role == 'operator' and supervisor is not None and int(supervisor) == requester_id
            for _person_id, person_role, supervisor in people
        ):
            return True
        return False

    @staticmethod
    def _task_viewer_is_observer(requester_role, requester_id, creator_id, requested_by_id, assignees):
        """Задачу человек только смотрит: она пришла к нему не как к участнику.

        Так бывает у СВ, когда задачу поставил коллега-СВ (задача #298), и у
        любого, кто видит задачу как участник её проекта или наблюдатель
        (задача #401). По этому флагу фронт прячет действия — приёмку,
        чек-лист, перенос на доске. Участник задачи (постановщик, поручитель,
        любой исполнитель) наблюдателем не бывает, админ раздела — тоже: он
        видит всё и действует по своей роли.
        """
        if role_has_min(normalize_role_value(requester_role), 'admin'):
            return False
        requester_id = int(requester_id)
        if requester_id in (int(creator_id or 0), int(requested_by_id or 0)):
            return False
        return not any(int((person or {}).get('id') or 0) == requester_id for person in (assignees or []))

    def _task_observable_tx(self, cursor, requester_role, requester_id, task_id, created_by):
        """Задачу можно ЧИТАТЬ, хотя участником её человек не является.

        Три пути, все только на чтение: СВ-коллега постановщика (задача #298) —
        только у задач вне проектов, участник проекта задачи и наблюдатель с
        доступом к доске (задача #401). Тот же набор, что в охвате списка
        (_task_scope_filters), — иначе карточка открывалась бы, а её отчёты и
        файлы отвечали бы 403. Один запрос на все три пути.
        """
        clauses = [self._TASK_PROJECT_MEMBER_SQL, self._TASK_WATCHER_SQL]
        params = [int(task_id), int(requester_id), int(requester_id)]
        if normalize_role_value(requester_role) == 'sv' and created_by is not None:
            clauses.insert(0, f"(t.project_id IS NULL AND {self._TASK_COLLEAGUE_SV_SQL})")
            params.insert(1, int(requester_id))
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM tasks t WHERE t.id = %s AND ("
            + " OR ".join(clauses) + "))",
            tuple(params),
        )
        row = cursor.fetchone()
        return bool(row and row[0])

    def _task_review_authority(self, created_by, assigned_to, requested_by):
        # assigned_to в расчёте не участвует и оставлен только ради единой формы
        # вызова рядом с _task_can_review.
        """
        Кто принимает итог: поручитель, если он указан, иначе постановщик.
        Возвращает id или None (никого — своя инициатива без поручителя).
        """
        if requested_by is not None:
            return int(requested_by)
        if created_by is not None:
            return int(created_by)
        return None

    def _task_can_review(self, role, requester_id, created_by, assignees, requested_by):
        """
        Приёмка и возврат: право у поручителя (или постановщика, если поручителя нет).
        Исполнитель не принимает свою же работу — кроме случая, когда принимать больше некому
        (своя инициатива), иначе такая задача навсегда застрянет на проверке.
        Админ и СВ могут принять за поручителя, но не свою собственную работу.

        Исполнитель здесь — ЛЮБОЙ из состава: приписав себя соисполнителем,
        приёмщик своей работы не принимает.
        """
        requester_id = int(requester_id)
        assignee_ids = [person_id for person_id, _role, _supervisor in self._task_assignee_tuples(assignees)]
        authority = self._task_review_authority(created_by, assignee_ids, requested_by)
        is_assignee = requester_id in assignee_ids

        if authority is not None and authority == requester_id:
            # Сам себе поручил и сам исполняет — закрыть может, это своя инициатива.
            return True
        if is_assignee:
            return False
        return role_has_min(role, 'admin') or role == 'sv' or (created_by is not None and int(created_by) == requester_id)

    def get_task_recipients(self, requester_id, requester_role, scope_department_id=None):
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        with self._get_cursor() as cursor:
            if role_has_min(role, 'admin') or role in TASK_PERSONAL_SCOPE_ROLES:
                scope_filter = ""
                params = []
                if scope_department_id is not None and not role_has_min(role, 'admin'):
                    scope_filter = "AND (u.role IN ('super_admin', 'admin') OR u.department_id = %s)"
                    params.append(int(scope_department_id))
                # Себя включаем всегда: задачу можно поставить самому себе (своя инициатива),
                # иначе роли вне списка получателей (например trainer) не могут ничего себе записать.
                params.append(requester_id)
                cursor.execute(f"""
                    SELECT u.id, u.name, u.role, u.supervisor_id, COALESCE(u.status, 'working'),
                           d.code
                    FROM users u
                    LEFT JOIN departments d ON d.id = u.department_id
                    WHERE COALESCE(u.status, 'working') <> 'fired'
                      AND (
                          (u.role IN ({_TASK_RECIPIENT_ROLES_SQL}) {scope_filter})
                          OR u.id = %s
                      )
                    ORDER BY CASE WHEN u.role IN ('super_admin', 'admin') THEN 0 ELSE 1 END, u.name
                """, tuple(params))
            else:
                return []

            rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "name": row[1],
                "role": row[2],
                "supervisor_id": row[3],
                "status": row[4],
                # Отдел — чтобы форма знала, когда предлагать тип «Продакшн»
                # (исполнитель из маркетинга, задача #354).
                "department_code": row[5]
            }
            for row in rows
        ]

    def get_task_board_people(self, requester_id, requester_role):
        """
        Сотрудники, у которых есть задачи, — источник списка досок в разделе.
        Уволенных не отдаём: их доска уже история, а список от них пухнет.
        Отдел нужен, чтобы список группировался, а не был сплошной простынёй.
        """
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        conditions = ["COALESCE(u.status, 'working') <> 'fired'"]
        params = []
        if not role_has_min(role, 'admin'):
            # Рядового бэк-офиса здесь намеренно НЕТ, хотя раздел ему выдан:
            # список досок читает только админский селектор охвата, остальным
            # доска предлагает «Мои / На мне / Все» без выбора человека. Отдать
            # ему список — послать данные, которые никто не покажет.
            if role not in ('sv', 'trainer'):
                return []
            # Видит только тех, с кем пересекается по задачам. Исполнителем
            # считается любой из состава, поэтому участие — через EXISTS.
            conditions.append(
                "(t.created_by = %s OR t.requested_by_id = %s"
                " OR EXISTS (SELECT 1 FROM task_assignees ta_me"
                " WHERE ta_me.task_id = t.id AND ta_me.user_id = %s))"
            )
            params.extend([requester_id, requester_id, requester_id])

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT
                    u.id,
                    u.name,
                    u.role,
                    d.id,
                    d.name,
                    u.avatar_bucket,
                    u.avatar_blob_path,
                    -- DISTINCT обязателен: у задачи с несколькими исполнителями
                    -- строка связи своя у каждого, и COUNT(*) считал бы одну
                    -- задачу дважды у постановщика, который сам в исполнителях.
                    COUNT(DISTINCT t.id)::INT AS task_count
                FROM users u
                JOIN tasks t
                  ON t.created_by = u.id
                  OR EXISTS (SELECT 1 FROM task_assignees ta
                             WHERE ta.task_id = t.id AND ta.user_id = u.id)
                LEFT JOIN departments d ON d.id = u.department_id
                {where_sql}
                GROUP BY u.id, u.name, u.role, d.id, d.name, u.avatar_bucket, u.avatar_blob_path
                ORDER BY u.name
            """, tuple(params))
            rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "name": row[1],
                "role": row[2],
                "department_id": row[3],
                "department": row[4],
                "avatar_bucket": row[5],
                "avatar_blob_path": row[6],
                "task_count": int(row[7] or 0),
            }
            for row in rows
        ]

    def get_task_departments(self, requester_id, requester_role):
        """Отделы для переключателя раздела «Задачи».

        Отдел задачи — это отдел того, кто её поставил: своей колонки у задачи
        нет. Считаем по видимым задачам, а не по справочнику отделов: пункт без
        единой задачи — заведомо пустой экран, в который человек кликает зря.
        Строка с id = None — задачи постановщиков без отдела.
        """
        role = normalize_role_value(requester_role)
        scope = self._task_scope_filters(requester_id, role)
        if scope is None:
            return []
        conditions, params, _person_board_id, _person_id_rest = scope
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT d.id, d.name, COUNT(*)::INT AS task_count
                FROM tasks t
                LEFT JOIN users creator ON creator.id = t.created_by
                LEFT JOIN departments d ON d.id = creator.department_id
                {where_sql}
                GROUP BY d.id, d.name
                ORDER BY d.name ASC NULLS LAST
            """, tuple(params))
            rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "name": row[1],
                "task_count": int(row[2] or 0),
            }
            for row in rows
        ]

    # ──────────────────────────────────────────────────────────────────
    # ПРОЕКТЫ-ДОСКИ (задача #401). Правила и стартовый список — task_projects.py.

    @staticmethod
    def _task_projects_admin(requester_role):
        """Проекты настраивает тот, кто в «Задачах» и так видит всё.

        Это админ, не возглавляющий отдел, и супер-админ. Глава-админ идёт в
        раздел под 'sv' (_effective_scoped_manager_role) и проектами не
        управляет: вписав себя участником чужой доски, он обошёл бы границу
        своего отдела.
        """
        return role_has_min(normalize_role_value(requester_role), 'admin')

    def _require_task_section_tx(self, cursor, section_id, project_id):
        """Раздел задачи — только с её доски: разделы у каждой доски свои."""
        if section_id is None:
            return
        if project_id is None:
            raise ValueError("SECTION_NOT_IN_PROJECT")
        cursor.execute(
            "SELECT 1 FROM task_project_sections WHERE id = %s AND project_id = %s",
            (int(section_id), int(project_id)),
        )
        if not cursor.fetchone():
            raise ValueError("SECTION_NOT_IN_PROJECT")

    def _replace_task_project_sections_tx(self, cursor, project_id, sections):
        """Разделы доски = ровно переданный список по порядку.

        Уже существующие переименовываем и двигаем (у них id), новые заводим,
        пропавшие удаляем — их задачи остаются на доске без раздела (ON DELETE
        SET NULL). Чужой id в списке — ошибка, а не тихий перенос раздела.
        """
        ordered = task_projects.normalize_sections(sections)
        cursor.execute("SELECT id FROM task_project_sections WHERE project_id = %s", (int(project_id),))
        existing = {int(row[0]) for row in cursor.fetchall()}
        keep_ids = [section_id for section_id, _name in ordered if section_id is not None]
        if any(section_id not in existing for section_id in keep_ids):
            raise ValueError("INVALID_PROJECT_SECTIONS")
        cursor.execute(
            "DELETE FROM task_project_sections WHERE project_id = %s AND NOT (id = ANY(%s))",
            (int(project_id), keep_ids),
        )
        for position, (section_id, name) in enumerate(ordered):
            if section_id is None:
                cursor.execute(
                    "INSERT INTO task_project_sections (project_id, name, position) VALUES (%s, %s, %s)",
                    (int(project_id), name, position),
                )
            else:
                cursor.execute(
                    "UPDATE task_project_sections SET name = %s, position = %s WHERE id = %s AND project_id = %s",
                    (name, position, section_id, int(project_id)),
                )
        return ordered

    def _require_task_project_tx(self, cursor, project_id, requester_id, requester_role):
        """Поставить задачу в проект можно, если проект жив и у человека есть доступ к доске."""
        cursor.execute("""
            SELECT p.is_archived, pm.access_level
              FROM task_projects p
              LEFT JOIN task_project_members pm
                     ON pm.project_id = p.id AND pm.user_id = %s
             WHERE p.id = %s
        """, (int(requester_id), int(project_id)))
        row = cursor.fetchone()
        if not row:
            raise ValueError("PROJECT_NOT_FOUND")
        if row[0]:
            raise ValueError("PROJECT_ARCHIVED")
        if row[1] is None and not self._task_projects_admin(requester_role):
            raise ValueError("PROJECT_FORBIDDEN")

    def _take_task_project_number_tx(self, cursor, project_id):
        """Следующий номер задачи в проекте.

        UPDATE ... RETURNING блокирует строку проекта до конца транзакции:
        две задачи, поставленные в одну секунду, получают разные номера, а
        откат создания номер просто пропускает — дыра в нумерации честнее,
        чем повтор.
        """
        cursor.execute("""
            UPDATE task_projects
               SET next_number = next_number + 1
             WHERE id = %s
         RETURNING next_number - 1, code
        """, (int(project_id),))
        row = cursor.fetchone()
        if not row:
            raise ValueError("PROJECT_NOT_FOUND")
        return int(row[0]), row[1]

    def _task_project_board_user_ids_tx(self, cursor, project_id, user_ids):
        """Кто из перечисленных есть в составе доски проекта (любым уровнем)."""
        if not user_ids:
            return set()
        cursor.execute("""
            SELECT user_id FROM task_project_members
             WHERE project_id = %s AND user_id = ANY(%s)
        """, (int(project_id), [int(item) for item in user_ids]))
        return {int(row[0]) for row in cursor.fetchall()}

    def _require_task_watchers_tx(self, cursor, project_id, watcher_ids):
        """Наблюдатель видит задачу только через доску, поэтому без доступа к ней его не добавить."""
        if not watcher_ids:
            return
        if project_id is None:
            raise ValueError("WATCHERS_NEED_PROJECT")
        allowed = self._task_project_board_user_ids_tx(cursor, project_id, watcher_ids)
        if any(int(person_id) not in allowed for person_id in watcher_ids):
            raise ValueError("WATCHER_NO_PROJECT_ACCESS")

    def _sync_task_watchers_tx(self, cursor, task_id, watcher_ids, added_by=None):
        """Наблюдатели задачи = ровно переданный список. Оставшимся added_at не трогаем."""
        ordered = [int(item) for item in (watcher_ids or [])]
        cursor.execute(
            "DELETE FROM task_watchers WHERE task_id = %s AND NOT (user_id = ANY(%s))",
            (int(task_id), ordered),
        )
        if ordered:
            cursor.execute("""
                INSERT INTO task_watchers (task_id, user_id, added_by)
                SELECT %s, src.user_id, %s
                  FROM unnest(%s::int[]) AS src(user_id)
                ON CONFLICT (task_id, user_id) DO NOTHING
            """, (int(task_id), added_by, ordered))
        return ordered

    def get_task_projects(self, requester_id, requester_role):
        """Проекты для переключателя раздела, формы задачи и окна настройки.

        Как в постановке #401: человек видит в списке только доски, к которым у
        него есть доступ. Задача, поставленная ему на чужой доске, остаётся у
        него на виду — в «Мои задачи» и во «Все проекты», — но сама доска в
        переключателе не появляется. Админ раздела получает все проекты, включая
        архив: ему их настраивать.

        К каждой доске — состав (из него форма берёт наблюдателей, окно
        настройки — весь список) и разделы со счётчиком видимых задач.
        """
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        is_admin = self._task_projects_admin(role)
        scope = self._task_scope_filters(requester_id, role)
        if scope is None:
            return {"projects": [], "unassigned_count": 0, "can_manage": False}
        conditions, params, _person_board_id, _person_id_rest = scope
        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT t.project_id, t.section_id, COUNT(*)::INT
                FROM tasks t
                {where_sql}
                GROUP BY t.project_id, t.section_id
            """, tuple(params))
            counts = defaultdict(int)
            section_counts = {}
            for project_key, section_key, count in cursor.fetchall():
                counts[project_key] += int(count or 0)
                section_counts[(project_key, section_key)] = int(count or 0)

            cursor.execute("""
                SELECT p.id, p.code, p.name, p.department_id, d.name, p.is_archived,
                       me.access_level
                  FROM task_projects p
                  LEFT JOIN departments d ON d.id = p.department_id
                  LEFT JOIN task_project_members me
                         ON me.project_id = p.id AND me.user_id = %s
                 ORDER BY p.name ASC, p.id ASC
            """, (requester_id,))
            project_rows = []
            for row in cursor.fetchall():
                access = 'admin' if is_admin else row[6]
                if access is None:
                    continue
                project_rows.append((row, access))

            sections_by_project = defaultdict(list)
            project_ids = [row[0] for row, _access in project_rows]
            if project_ids:
                cursor.execute("""
                    SELECT s.project_id, s.id, s.name
                      FROM task_project_sections s
                     WHERE s.project_id = ANY(%s)
                     ORDER BY s.project_id, s.position, s.id
                """, (project_ids,))
                for project_key, section_id, name in cursor.fetchall():
                    sections_by_project[project_key].append({
                        "id": section_id,
                        "name": name,
                        "task_count": section_counts.get((project_key, section_id), 0),
                    })

            members_by_project = defaultdict(list)
            member_project_ids = [row[0] for row, access in project_rows if access]
            if member_project_ids:
                cursor.execute("""
                    SELECT pm.project_id, u.id, u.name, u.role, pm.access_level, d.name,
                           u.avatar_bucket, u.avatar_blob_path
                      FROM task_project_members pm
                      JOIN users u ON u.id = pm.user_id
                      LEFT JOIN departments d ON d.id = u.department_id
                     WHERE pm.project_id = ANY(%s)
                       AND COALESCE(u.status, 'working') <> 'fired'
                     ORDER BY pm.project_id,
                              CASE pm.access_level WHEN 'member' THEN 0 ELSE 1 END,
                              u.name, u.id
                """, (member_project_ids,))
                for row in cursor.fetchall():
                    members_by_project[row[0]].append({
                        "id": row[1],
                        "name": row[2],
                        "role": row[3],
                        "access_level": row[4],
                        "department": row[5],
                        "avatar_bucket": row[6],
                        "avatar_blob_path": row[7],
                    })

            # Кого админ может поставить на доску: всех, кто ходит в «Задачи», — и
            # тех, кому задачи не поручают (тренер), и глав отделов любой роли.
            # Список получателей задач для этого узок, и отдела в нём нет.
            candidates = []
            if is_admin:
                cursor.execute(f"""
                    SELECT u.id, u.name, u.role, d.name, u.department_id
                      FROM users u
                      LEFT JOIN departments d ON d.id = u.department_id
                     WHERE COALESCE(u.status, 'working') <> 'fired'
                       AND (u.role IN ({_TASK_RECIPIENT_ROLES_SQL}, 'trainer')
                            OR EXISTS (SELECT 1 FROM departments hd
                                        WHERE hd.head_user_id = u.id AND hd.is_active))
                     ORDER BY u.name, u.id
                """)
                candidates = [
                    {"id": row[0], "name": row[1], "role": row[2], "department": row[3],
                     "department_id": row[4]}
                    for row in cursor.fetchall()
                ]

        projects = []
        for row, access in project_rows:
            project = {
                "id": row[0],
                "code": row[1],
                "name": row[2],
                "department_id": row[3],
                "department_name": row[4],
                "is_archived": bool(row[5]),
                # admin — настраивает и ставит задачи в любой проект; member и
                # board — уровень в составе; None — видит здесь только свои задачи.
                "access": access,
                "can_create": bool(access) and not row[5],
                "task_count": counts.get(row[0], 0),
                "members": members_by_project.get(row[0], []),
                "sections": sections_by_project.get(row[0], []),
                # Задачи доски без раздела — чтобы фильтр «Без раздела» появлялся,
                # только когда в нём что-то есть.
                "unsectioned_count": section_counts.get((row[0], None), 0),
            }
            projects.append(project)
        return {
            "projects": projects,
            "unassigned_count": counts.get(None, 0),
            "can_manage": is_admin,
            "candidates": candidates,
        }

    def _replace_task_project_members_tx(self, cursor, project_id, members, added_by):
        """Состав доски = ровно переданный список; оставшимся added_at не трогаем."""
        ordered = task_projects.normalize_members(members)
        user_ids = [person_id for person_id, _level in ordered]
        if user_ids:
            cursor.execute("SELECT id FROM users WHERE id = ANY(%s)", (user_ids,))
            if len({int(row[0]) for row in cursor.fetchall()}) != len(user_ids):
                raise ValueError("INVALID_PROJECT_MEMBERS")
        cursor.execute(
            "DELETE FROM task_project_members WHERE project_id = %s AND NOT (user_id = ANY(%s))",
            (int(project_id), user_ids),
        )
        # Сняли человека с доски — снимаем и его наблюдение за задачами этой
        # доски: видеть их он больше не может, а строка осталась бы мусором,
        # который всплывает в форме правки.
        cursor.execute("""
            DELETE FROM task_watchers tw
             USING tasks t
             WHERE tw.task_id = t.id AND t.project_id = %s
               AND NOT (tw.user_id = ANY(%s))
        """, (int(project_id), user_ids))
        if user_ids:
            cursor.execute("""
                INSERT INTO task_project_members (project_id, user_id, access_level, added_by)
                SELECT %s, src.user_id, src.access_level, %s
                  FROM unnest(%s::int[], %s::text[]) AS src(user_id, access_level)
                ON CONFLICT (project_id, user_id) DO UPDATE
                    SET access_level = EXCLUDED.access_level
            """, (int(project_id), added_by, user_ids, [level for _person_id, level in ordered]))
        return ordered

    def create_task_project(self, requester_id, requester_role, code, name,
                            department_id=None, members=None, sections=None):
        """Новый проект. Ключ потом можно сменить — см. _rename_task_project_code_tx."""
        if not self._task_projects_admin(requester_role):
            raise PermissionError("PROJECT_MANAGE_FORBIDDEN")
        code_norm = task_projects.normalize_code(code)
        name_norm = task_projects.normalize_name(name)
        with self._get_cursor() as cursor:
            # Ключи досок раздаём по одному (см. _rename_task_project_code_tx).
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext('tasks:project_code'))")
            # Ключ, которым раньше звались задачи (доску переименовали или задачу
            # перенесли), новой доске не отдаём: её HR-1 и старая ссылка HR-1 вели бы
            # к разным задачам.
            cursor.execute("SELECT 1 FROM task_key_aliases WHERE code = %s LIMIT 1", (code_norm,))
            if cursor.fetchone():
                raise ValueError("PROJECT_CODE_USED_BEFORE")
            department_id_norm = self._normalize_task_project_department_tx(cursor, department_id)
            cursor.execute("""
                INSERT INTO task_projects (code, name, department_id, created_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (code) DO NOTHING
                RETURNING id
            """, (code_norm, name_norm, department_id_norm, int(requester_id)))
            row = cursor.fetchone()
            if not row:
                raise ValueError("PROJECT_CODE_TAKEN")
            project_id = int(row[0])
            if members:
                self._replace_task_project_members_tx(cursor, project_id, members, int(requester_id))
            if sections:
                self._replace_task_project_sections_tx(cursor, project_id, sections)
        return {"id": project_id, "code": code_norm, "name": name_norm}

    def update_task_project(self, project_id, requester_id, requester_role, name=_UNSET,
                            department_id=_UNSET, is_archived=_UNSET, members=_UNSET,
                            sections=_UNSET, code=_UNSET):
        """Название, ключ, отдел, архив, состав и разделы доски."""
        if not self._task_projects_admin(requester_role):
            raise PermissionError("PROJECT_MANAGE_FORBIDDEN")
        project_id = int(project_id)
        with self._get_cursor() as cursor:
            cursor.execute("SELECT id, code FROM task_projects WHERE id = %s FOR UPDATE", (project_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("PROJECT_NOT_FOUND")
            current_code = row[1]
            sets = []
            values = []
            code_new = current_code
            if code is not _UNSET:
                code_new = task_projects.normalize_code(code)
                if code_new != current_code:
                    self._rename_task_project_code_tx(cursor, project_id, current_code, code_new)
                    sets.append("code = %s")
                    values.append(code_new)
            if name is not _UNSET:
                sets.append("name = %s")
                values.append(task_projects.normalize_name(name))
            if department_id is not _UNSET:
                sets.append("department_id = %s")
                values.append(self._normalize_task_project_department_tx(cursor, department_id))
            if is_archived is not _UNSET:
                sets.append("is_archived = %s")
                values.append(bool(is_archived))
            if members is not _UNSET:
                self._replace_task_project_members_tx(cursor, project_id, members, int(requester_id))
            if sections is not _UNSET:
                self._replace_task_project_sections_tx(cursor, project_id, sections)
            if sets or members is not _UNSET or sections is not _UNSET:
                sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
                cursor.execute(
                    f"UPDATE task_projects SET {', '.join(sets)} WHERE id = %s",
                    tuple(values + [project_id]),
                )
        return {"id": project_id, "updated": True, "code": code_new, "previous_code": current_code}

    def _rename_task_project_code_tx(self, cursor, project_id, old_code, new_code):
        """Смена ключа доски (владелец 06.11.2026): HR-125 станет HRD-125.

        Номера задач остаются, меняется только ключ перед ними. Прежние ключи
        задач доски уходят в task_key_aliases — ссылки из Telegram, переписка и
        CLI по-прежнему открывают те же задачи (_TASK_KEY_MATCH_SQL ищет и там).
        Чужой нынешний ключ занять нельзя, а бывший — только свой (доске вернули
        прежний ключ): иначе старая ссылка HR-5 и новая HR-5 вели бы к разным
        задачам. Свой прежний ключ безопасен: номер в доске не переиспользуется,
        и HR-5 перенесённой задачи ни с одной нынешней HR-5 не совпадёт.

        Смены ключей и создание досок идут по одной (замок на транзакцию): два админа,
        взявшие один ключ в одну секунду, иначе получили бы 500 от UNIQUE, а обмен
        ключами «крест-накрест» прошёл бы мимо ещё не записанных псевдонимов соседа.
        """
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('tasks:project_code'))")
        cursor.execute(
            "SELECT 1 FROM task_projects WHERE code = %s AND id <> %s LIMIT 1",
            (new_code, int(project_id)),
        )
        if cursor.fetchone():
            raise ValueError("PROJECT_CODE_TAKEN")
        cursor.execute("""
            SELECT 1
              FROM task_key_aliases a
             WHERE a.code = %s
               AND a.project_id IS DISTINCT FROM %s
             LIMIT 1
        """, (new_code, int(project_id)))
        if cursor.fetchone():
            raise ValueError("PROJECT_CODE_USED_BEFORE")
        # Номер в доске не переиспользуется (next_number только растёт), поэтому
        # прежний ключ задачи доски ни с чьим другим не совпадёт; ON CONFLICT —
        # для ключей, уже записанных раньше (доску переименовывали туда и обратно).
        cursor.execute("""
            INSERT INTO task_key_aliases (code, number, task_id, project_id)
            SELECT %s, t.project_number, t.id, %s
              FROM tasks t
             WHERE t.project_id = %s
               AND t.project_number IS NOT NULL
            ON CONFLICT (code, number) DO NOTHING
        """, (old_code, int(project_id), int(project_id)))

    def get_task_key(self, task_id):
        """Ключ задачи (HR-125) или None — для ссылок в Telegram."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT p.code, t.project_number
                  FROM tasks t
                  JOIN task_projects p ON p.id = t.project_id
                 WHERE t.id = %s
            """, (int(task_id),))
            row = cursor.fetchone()
        return task_projects.format_task_key(row[0], row[1]) if row else None

    def _task_now(self):
        return datetime.now()

    def _task_dt_to_iso(self, value):
        return value.isoformat() if hasattr(value, 'isoformat') else value

    def _split_task_deadline(self, deadline_minutes):
        if deadline_minutes is None:
            return {"days": 0, "hours": 0, "minutes": 0, "total_minutes": None}
        try:
            total = max(0, int(deadline_minutes))
        except Exception:
            total = 0
        days = total // (24 * 60)
        remainder = total % (24 * 60)
        hours = remainder // 60
        minutes = remainder % 60
        return {
            "days": int(days),
            "hours": int(hours),
            "minutes": int(minutes),
            "total_minutes": int(total)
        }

    def collect_due_task_reminders(self, limit=100):
        """
        Напоминания, которые пора отправить: наступило (due_at - reminder), но дедлайн ещё не прошёл.
        Просроченные и закрытые не берём — напоминать о них поздно и бессмысленно.
        Возвращает записи заметок и задач с telegram-адресатом.
        """
        now = self._task_now()
        result = []
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT n.id, n.title, n.body, n.due_at, n.priority, u.telegram_id, u.name
                FROM task_notes n
                JOIN users u ON u.id = n.owner_id
                WHERE n.due_at IS NOT NULL
                  AND n.reminder_minutes_before IS NOT NULL
                  AND n.reminder_sent_at IS NULL
                  AND n.is_done = FALSE
                  AND u.telegram_id IS NOT NULL
                  AND n.due_at > %s
                  AND n.due_at - (n.reminder_minutes_before * INTERVAL '1 minute') <= %s
                ORDER BY n.due_at ASC
                LIMIT %s
            """, (now, now, int(limit)))
            for row in cursor.fetchall():
                result.append({
                    "kind": "note",
                    "id": row[0],
                    "title": (row[1] or '').strip() or 'Заметка',
                    "body": row[2] or '',
                    "due_at": row[3],
                    "priority": row[4] or 'normal',
                    "chat_id": row[5],
                    "recipient_name": row[6]
                })

            # По строке на каждого исполнителя с Telegram: срок один, но ждут
            # его все, и напоминание «только первому» — это молчание для
            # остальных. Отметка reminder_sent_at остаётся на задаче, поэтому
            # LIMIT обязан отбирать ЗАДАЧИ, а не строки состава: иначе у задачи
            # на пятерых часть людей попадала бы за границу порции, а отметка
            # уже стояла — и своё напоминание они не получили бы НИКОГДА.
            # Отсюда порция задач в CTE, и только потом разворот по людям.
            cursor.execute("""
                WITH due AS (
                    SELECT t.id, t.subject, t.description, t.due_at, t.priority
                    FROM tasks t
                    WHERE t.due_at IS NOT NULL
                      AND t.reminder_minutes_before IS NOT NULL
                      AND t.is_backlog = FALSE
                      AND t.status NOT IN ('completed', 'accepted')
                      AND t.due_at > %s
                      AND t.due_at - (t.reminder_minutes_before * INTERVAL '1 minute') <= %s
                      -- Задачу без ни одного НЕОТМЕЧЕННОГО адресата в порцию не
                      -- берём: иначе она заняла бы место и вытеснила ту, кому
                      -- действительно есть что послать. Здесь же и «уже всем
                      -- напомнили» — отдельного флага на задаче для этого нет.
                      AND EXISTS (
                          SELECT 1 FROM task_assignees ta_any
                          JOIN users u_any ON u_any.id = ta_any.user_id
                          WHERE ta_any.task_id = t.id
                            AND ta_any.reminder_sent_at IS NULL
                            AND u_any.telegram_id IS NOT NULL
                            AND COALESCE(u_any.status, 'working') <> 'fired'
                      )
                    ORDER BY t.due_at ASC, t.id ASC
                    LIMIT %s
                )
                SELECT d.id, d.subject, d.description, d.due_at, d.priority,
                       u.telegram_id, u.name, u.id
                FROM due d
                JOIN task_assignees ta ON ta.task_id = d.id
                JOIN users u ON u.id = ta.user_id
                WHERE ta.reminder_sent_at IS NULL
                  AND u.telegram_id IS NOT NULL
                  AND COALESCE(u.status, 'working') <> 'fired'
                ORDER BY d.due_at ASC, ta.position ASC, ta.user_id ASC
            """, (now, now, int(limit)))
            for row in cursor.fetchall():
                result.append({
                    "kind": "task",
                    "id": row[0],
                    "title": (row[1] or '').strip() or f'Задача #{row[0]}',
                    "body": row[2] or '',
                    "due_at": row[3],
                    "priority": row[4] or 'normal',
                    "chat_id": row[5],
                    "recipient_name": row[6],
                    # Кому именно — по нему отмечается отправка: у задачи
                    # исполнителей может быть несколько.
                    "recipient_id": row[7]
                })
        return result

    def mark_task_reminder_sent(self, kind, record_id, recipient_id=None):
        """Отметить напоминание отправленным, чтобы не дублировать его каждые пять минут.

        У заметки адресат один — владелец, там отметка на самой записи. У задачи
        исполнителей может быть несколько, и отметка ставится ПАРЕ
        (задача, исполнитель): иначе успех первого закрыл бы задачу для всех, и
        тот, кому сообщение не ушло, не получил бы своё напоминание никогда.
        Поле tasks.reminder_sent_at при этом всё равно заполняем — оно уходит в
        карточку как «когда напомнили», — но выборку оно больше не ограничивает.
        """
        record_id = int(record_id)
        if str(kind) == 'note':
            with self._get_cursor() as cursor:
                cursor.execute(
                    "UPDATE task_notes SET reminder_sent_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"
                    " WHERE id = %s",
                    (record_id,)
                )
            return True

        with self._get_cursor() as cursor:
            if recipient_id is not None:
                cursor.execute(
                    "UPDATE task_assignees"
                    " SET reminder_sent_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"
                    " WHERE task_id = %s AND user_id = %s",
                    (record_id, int(recipient_id))
                )
            else:
                # Адресат не назван (старый вызов) — закрываем задачу целиком,
                # иначе напоминание пошло бы по кругу.
                cursor.execute(
                    "UPDATE task_assignees"
                    " SET reminder_sent_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"
                    " WHERE task_id = %s AND reminder_sent_at IS NULL",
                    (record_id,)
                )
            cursor.execute(
                "UPDATE tasks SET reminder_sent_at ="
                " COALESCE(reminder_sent_at, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))"
                " WHERE id = %s",
                (record_id,)
            )
        return True

    def _parse_task_datetime(self, value, error_code="INVALID_DATETIME"):
        """ISO-строка / datetime → naive datetime в таймзоне проекта (Asia/Almaty)."""
        if value is None or value is _UNSET:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value).strip()
            if not raw:
                return None
            if raw.endswith('Z'):
                raw = raw[:-1] + '+00:00'
            try:
                parsed = datetime.fromisoformat(raw)
            except Exception:
                raise ValueError(error_code)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo('Asia/Almaty')).replace(tzinfo=None)
        return parsed.replace(microsecond=0)

    def _add_task_recurrence_interval(self, base_dt, recurrence_type, interval):
        base = base_dt or self._task_now()
        interval_norm = max(1, int(interval or 1))
        recurrence_type_norm = (recurrence_type or '').strip().lower()
        if recurrence_type_norm == 'daily':
            return base + timedelta(days=interval_norm)
        if recurrence_type_norm == 'weekly':
            return base + timedelta(weeks=interval_norm)
        if recurrence_type_norm == 'monthly':
            month_index = base.month - 1 + interval_norm
            year = base.year + month_index // 12
            month = month_index % 12 + 1
            day = min(base.day, calendar.monthrange(year, month)[1])
            return base.replace(year=year, month=month, day=day)
        return None

    def _insert_task_checklist_items(self, cursor, task_id, checklist_items):
        normalized = self._normalize_task_checklist_items(checklist_items)
        for item in normalized:
            cursor.execute("""
                INSERT INTO task_checklist_items (task_id, title, position, is_required)
                VALUES (%s, %s, %s, %s)
            """, (task_id, item["title"], item["position"], item["is_required"]))
        return normalized

    def create_task(
            self,
            subject,
            description,
            tag,
            assigned_to,
            created_by,
            attachments=None,
            priority='normal',
            deadline_minutes=None,
            is_regulation=False,
            recurrence_type=None,
            recurrence_interval=None,
            checklist_items=None,
            is_backlog=False,
            estimate_minutes=None,
            planned_start_at=None,
            due_at=None,
            requested_by_id=None,
            requested_by_name=None,
            reminder_minutes_before=None,
            assignee_ids=None,
            production_brief=None,
            project_id=None,
            watcher_ids=None,
            requester_role=None,
            section_id=None
    ):
        subject_norm = (subject or '').strip()
        description_norm = (description or '').strip() or None
        tag_norm = (tag or 'task').strip().lower() or 'task'
        # Исполнителей может быть несколько. assigned_to остаётся ради вызовов,
        # которые знают только одного, но состав всегда считается одним и тем же
        # нормализатором: первый в списке и есть tasks.assigned_to.
        assignee_ids_norm = self._normalize_task_assignees(
            assignee_ids if assignee_ids is not None else assigned_to
        )
        assigned_to_id = assignee_ids_norm[0]
        created_by_id = int(created_by) if created_by is not None else None
        attachments = attachments or []
        priority_norm = self._normalize_task_priority(priority)
        deadline_minutes_norm = self._normalize_task_deadline_minutes(deadline_minutes)
        is_backlog_norm = bool(is_backlog)
        estimate_minutes_norm = self._normalize_task_estimate_minutes(estimate_minutes)
        planned_start_at_norm = self._parse_task_datetime(planned_start_at, "INVALID_PLANNED_START")
        requested_by_id_norm, requested_by_name_norm = self._normalize_task_origin(requested_by_id, requested_by_name)
        reminder_minutes_norm = self._normalize_task_reminder_minutes(reminder_minutes_before)
        now = self._task_now()
        # Абсолютный дедлайн (нужен агентам/планировщику) выигрывает у относительного «через N».
        due_at_absolute = self._parse_task_datetime(due_at, "INVALID_DEADLINE")
        if due_at_absolute is not None:
            due_at = due_at_absolute
            deadline_minutes_norm = max(0, int((due_at_absolute - now).total_seconds() // 60)) or None
        else:
            due_at = now + timedelta(minutes=deadline_minutes_norm) if deadline_minutes_norm else None
        is_regulation_norm, recurrence_type_norm, recurrence_interval_norm = self._normalize_task_recurrence(
            is_regulation=is_regulation,
            recurrence_type=recurrence_type,
            recurrence_interval=recurrence_interval
        )
        recurrence_next_at = self._add_task_recurrence_interval(now, recurrence_type_norm, recurrence_interval_norm) if recurrence_type_norm else None

        if not subject_norm:
            raise ValueError("SUBJECT_REQUIRED")
        if tag_norm not in TASK_TAG_CODES:
            raise ValueError("INVALID_TAG")
        # Бриф — только у продакшна. У задачи другого типа он молча
        # отбрасывается: это остаток формы, где тип передумали.
        production_brief_norm = self._normalize_task_production_brief(
            tag_norm, production_brief, description_norm
        )
        # Проект-доска (задача #401). Без проекта задача остаётся как раньше —
        # так её ставят CLI и любой клиент, не знающий о проектах.
        project_id_norm = self._normalize_task_project_id(project_id)
        watcher_ids_norm = self._normalize_task_watchers(watcher_ids) or []
        section_id_norm = self._normalize_task_section_id(section_id)

        with self._get_cursor() as cursor:
            if production_brief_norm is not None:
                self._require_task_production_assignee_tx(cursor, assignee_ids_norm)
            project_number = None
            project_code = None
            if project_id_norm is not None:
                self._require_task_project_tx(cursor, project_id_norm, created_by_id or 0, requester_role)
                project_number, project_code = self._take_task_project_number_tx(cursor, project_id_norm)
            self._require_task_watchers_tx(cursor, project_id_norm, watcher_ids_norm)
            self._require_task_section_tx(cursor, section_id_norm, project_id_norm)
            # Новые элементы бэклога встают в конец очереди приоритезации.
            backlog_rank = None
            if is_backlog_norm:
                cursor.execute("SELECT COALESCE(MAX(backlog_rank), 0) + 1 FROM tasks WHERE is_backlog = TRUE")
                backlog_rank = float((cursor.fetchone() or [1])[0] or 1)

            cursor.execute("""
                INSERT INTO tasks (
                    subject, description, tag, priority, status, assigned_to, created_by,
                    deadline_duration_minutes, due_at, is_regulation, recurrence_type,
                    recurrence_interval, recurrence_next_at, regulation_iteration,
                    is_backlog, backlog_rank, estimate_minutes, planned_start_at,
                    requested_by_id, requested_by_name, reminder_minutes_before,
                    production_brief, project_id, project_number, section_id, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, 'assigned', %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at, updated_at
            """, (
                subject_norm,
                description_norm,
                tag_norm,
                priority_norm,
                assigned_to_id,
                created_by_id,
                deadline_minutes_norm,
                due_at,
                is_regulation_norm,
                recurrence_type_norm,
                recurrence_interval_norm,
                recurrence_next_at,
                is_backlog_norm,
                backlog_rank,
                estimate_minutes_norm,
                planned_start_at_norm,
                requested_by_id_norm,
                requested_by_name_norm,
                reminder_minutes_norm if due_at else None,
                Json(production_brief_norm) if production_brief_norm is not None else None,
                project_id_norm,
                project_number,
                section_id_norm,
                now,
                now
            ))
            task_id, created_at, updated_at = cursor.fetchone()

            self._sync_task_assignees_tx(cursor, task_id, assignee_ids_norm, added_by=created_by_id)
            if watcher_ids_norm:
                self._sync_task_watchers_tx(cursor, task_id, watcher_ids_norm, added_by=created_by_id)

            cursor.execute("""
                INSERT INTO task_status_history (task_id, status_code, changed_by)
                VALUES (%s, 'assigned', %s)
            """, (task_id, created_by_id))

            self._insert_task_checklist_items(cursor, task_id, checklist_items)

            for attachment in attachments:
                file_name = (attachment.get('file_name') or 'attachment').strip() or 'attachment'
                content_type = (attachment.get('content_type') or '').strip() or None
                storage_type = (attachment.get('storage_type') or 'gcs').strip().lower() or 'gcs'
                if storage_type != 'gcs':
                    continue
                gcs_bucket = (attachment.get('gcs_bucket') or '').strip() or None
                gcs_blob_path = (attachment.get('gcs_blob_path') or '').strip() or None
                file_size = int(attachment.get('file_size') or 0)
                if not gcs_bucket or not gcs_blob_path:
                    continue
                cursor.execute("""
                    INSERT INTO task_attachments (
                        task_id, file_name, content_type, file_size, file_data,
                        storage_type, gcs_bucket, gcs_blob_path, attachment_kind, uploaded_by,
                        thumb_blob_path
                    )
                    VALUES (%s, %s, %s, %s, NULL, 'gcs', %s, %s, 'initial', %s, %s)
                """, (
                    task_id, file_name, content_type, file_size, gcs_bucket, gcs_blob_path, created_by_id,
                    (attachment.get('thumb_blob_path') or '').strip() or None
                ))

        return {
            "id": task_id,
            "created_at": self._task_dt_to_iso(created_at),
            "updated_at": self._task_dt_to_iso(updated_at),
            "due_at": self._task_dt_to_iso(due_at),
            "priority": priority_norm,
            "is_regulation": is_regulation_norm,
            "is_backlog": is_backlog_norm,
            "estimate_minutes": estimate_minutes_norm,
            "planned_start_at": self._task_dt_to_iso(planned_start_at_norm),
            "requested_by_id": requested_by_id_norm,
            "requested_by_name": requested_by_name_norm,
            "reminder_minutes_before": reminder_minutes_norm if due_at else None,
            "production_brief": production_brief_norm,
            # Кого фактически записали: по этому списку роут рассылает Telegram.
            "assignee_ids": list(assignee_ids_norm),
            "project_id": project_id_norm,
            "project_number": project_number,
            "key": task_projects.format_task_key(project_code, project_number),
            "watcher_ids": list(watcher_ids_norm),
            "section_id": section_id_norm
        }

    def edit_task(
            self,
            task_id,
            requester_id,
            requester_role,
            subject=None,
            description=None,
            tag=None,
            assigned_to=None,
            priority=_UNSET,
            deadline_minutes=_UNSET,
            is_regulation=_UNSET,
            recurrence_type=_UNSET,
            recurrence_interval=_UNSET,
            checklist_items=_UNSET,
            due_at=_UNSET,
            estimate_minutes=_UNSET,
            planned_start_at=_UNSET,
            requested_by_id=_UNSET,
            requested_by_name=_UNSET,
            reminder_minutes_before=_UNSET,
            assignee_ids=_UNSET,
            production_brief=_UNSET,
            project_id=_UNSET,
            watcher_ids=_UNSET,
            section_id=_UNSET
    ):
        task_id = int(task_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)

        has_subject = subject is not None
        has_description = description is not None
        has_tag = tag is not None
        # Состав меняют либо новым полем, либо старым одиночным assigned_to.
        # Пустой список тут невозможен по построению: _normalize_task_assignees
        # на нём падает ASSIGNEE_REQUIRED, а None означает «состав не меняем» —
        # иначе правка одной темы снимала бы с задачи всех исполнителей.
        has_assigned_to = assigned_to is not None or assignee_ids is not _UNSET
        has_priority = priority is not _UNSET
        has_deadline = deadline_minutes is not _UNSET
        has_due_at = due_at is not _UNSET
        has_is_regulation = is_regulation is not _UNSET
        has_recurrence_type = recurrence_type is not _UNSET
        has_recurrence_interval = recurrence_interval is not _UNSET
        has_checklist = checklist_items is not _UNSET
        has_estimate = estimate_minutes is not _UNSET
        has_planned_start = planned_start_at is not _UNSET
        has_origin = requested_by_id is not _UNSET or requested_by_name is not _UNSET
        has_reminder = reminder_minutes_before is not _UNSET
        # Ключа нет — бриф не трогаем (так шлёт вкладка, открытая до выхода
        # «Продакшна»); None — убрать бриф; словарь — заменить целиком.
        has_production_brief = production_brief is not _UNSET
        # Проект и наблюдатели (задача #401): ключа нет — не трогаем.
        has_project = project_id is not _UNSET
        has_watchers = watcher_ids is not _UNSET
        has_section = section_id is not _UNSET

        if not any([
            has_subject, has_description, has_tag, has_assigned_to,
            has_priority, has_deadline, has_due_at, has_is_regulation,
            has_recurrence_type, has_recurrence_interval, has_checklist,
            has_estimate, has_planned_start, has_origin, has_reminder,
            has_production_brief, has_project, has_watchers, has_section
        ]):
            raise ValueError("NOTHING_TO_UPDATE")

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    t.id, t.subject, t.description, t.tag, t.priority,
                    t.created_by, t.assigned_to,
                    t.deadline_duration_minutes, t.due_at,
                    t.is_regulation, t.recurrence_type, t.recurrence_interval,
                    t.recurrence_next_at, t.created_at, t.estimate_minutes, t.planned_start_at,
                    t.requested_by_id, t.requested_by_name, t.reminder_minutes_before,
                    -- Автор открытого запроса информации: по нему решается,
                    -- снимать ли запрос при смене состава исполнителей.
                    (SELECT m.author_id FROM task_messages m WHERE m.id = t.info_request_id),
                    t.production_brief,
                    t.project_id, t.project_number,
                    (SELECT p.code FROM task_projects p WHERE p.id = t.project_id),
                    t.section_id
                FROM tasks t
                WHERE t.id = %s
            """, (task_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("TASK_NOT_FOUND")

            current_subject = (row[1] or '').strip()
            current_description = (row[2] or '').strip() or None
            current_tag = (row[3] or 'task').strip().lower() or 'task'
            current_priority = (row[4] or 'normal').strip().lower() or 'normal'
            created_by = row[5]
            current_assigned_to = row[6]
            current_deadline_minutes = row[7]
            current_due_at = row[8]
            current_is_regulation = bool(row[9])
            current_recurrence_type = (row[10] or '').strip().lower() or None
            current_recurrence_interval = row[11]
            current_recurrence_next_at = row[12]
            current_created_at = row[13]
            current_estimate_minutes = row[14]
            current_planned_start_at = row[15]
            current_requested_by_id = row[16]
            current_requested_by_name = row[17]
            current_reminder = row[18]
            info_request_author_id = row[19]
            current_production_brief = task_production.stored_brief(row[20])
            current_assignee_scope = self._task_assignee_scope_tx(cursor, task_id)
            current_assignee_ids = [person_id for person_id, _role, _sv in current_assignee_scope]

            if not self._task_visible_for_requester(role, requester_id, created_by, current_assignee_scope,
                                                    current_requested_by_id):
                raise PermissionError("TASK_FORBIDDEN")

            if created_by is None or int(created_by) != requester_id:
                raise PermissionError("ONLY_CREATOR_CAN_EDIT")

            # Проект и наблюдатели. Перенос в другой проект — это новый номер в
            # нём; прежний ключ остаётся в task_key_aliases и открывает задачу.
            current_project_id = row[21]
            current_project_number = row[22]
            current_project_code = row[23]
            # Пустой проект в правке — «не менять»: убрать задачу из проекта нельзя,
            # только перенести в другой. Так старая вкладка или форма, не знающая
            # о проекте (быстрая правка закреплённой задачи), не вынет задачу с
            # доски и не сожжёт её номер.
            project_id_requested = self._normalize_task_project_id(project_id) if has_project else None
            project_id_new = project_id_requested if project_id_requested is not None else current_project_id
            project_changed = project_id_new != current_project_id
            if project_changed and project_id_new is not None:
                self._require_task_project_tx(cursor, project_id_new, requester_id, role)
            current_watcher_ids = self._task_watcher_ids_tx(cursor, task_id)
            if has_watchers:
                watcher_ids_new = self._normalize_task_watchers(watcher_ids) or []
            elif project_changed:
                # Проект сменили, а список не прислали: остаются те, кто видит
                # новую доску. Остальные задачу и так перестали бы видеть.
                allowed = (
                    self._task_project_board_user_ids_tx(cursor, project_id_new, current_watcher_ids)
                    if project_id_new is not None else set()
                )
                watcher_ids_new = [pid for pid in current_watcher_ids if pid in allowed]
            else:
                watcher_ids_new = list(current_watcher_ids)
            if project_changed:
                # Новая доска — у всех наблюдателей должен быть доступ к ней.
                self._require_task_watchers_tx(cursor, project_id_new, watcher_ids_new)
            elif has_watchers:
                # Сверяем с доской только добавленных: тех, кто уже был, правкой
                # темы не проверяем — иначе один снятый с доски наблюдатель
                # запирал бы задачу от любой правки.
                self._require_task_watchers_tx(
                    cursor, project_id_new,
                    [pid for pid in watcher_ids_new if pid not in set(current_watcher_ids)],
                )
            watchers_changed = set(watcher_ids_new) != set(current_watcher_ids)

            # Раздел — свой у каждой доски: перенесли задачу, а раздел не выбрали —
            # она встаёт на новую доску без раздела. «Без раздела» — законный выбор.
            current_section_id = row[24]
            if has_section:
                section_id_new = self._normalize_task_section_id(section_id)
            elif project_changed:
                section_id_new = None
            else:
                section_id_new = current_section_id
            if section_id_new is not None and (has_section or project_changed):
                self._require_task_section_tx(cursor, section_id_new, project_id_new)
            section_changed = section_id_new != current_section_id

            if has_assigned_to:
                assignee_ids_new = self._normalize_task_assignees(
                    assignee_ids if assignee_ids is not _UNSET else assigned_to
                )
            else:
                assignee_ids_new = list(current_assignee_ids)
            if not assignee_ids_new:
                raise ValueError("ASSIGNEE_REQUIRED")
            assigned_to_new = assignee_ids_new[0]

            subject_new = current_subject if not has_subject else (str(subject or '').strip())
            if not subject_new:
                raise ValueError("SUBJECT_REQUIRED")

            if has_description:
                description_new = (str(description or '').strip() or None)
            else:
                description_new = current_description

            if has_tag:
                tag_new = (str(tag or '').strip().lower() or 'task')
            else:
                tag_new = current_tag
            if tag_new not in TASK_TAG_CODES:
                raise ValueError("INVALID_TAG")

            production_brief_new = self._edited_task_production_brief(
                tag_new, current_production_brief, has_production_brief, production_brief, description_new
            )

            priority_new = current_priority if not has_priority else self._normalize_task_priority(priority)

            if has_due_at:
                # Абсолютный дедлайн: считаем «через N» от постановки, чтобы форма правки осталась осмысленной.
                due_at_new = self._parse_task_datetime(due_at, "INVALID_DEADLINE")
                if due_at_new is None:
                    deadline_minutes_new = None
                else:
                    base_at = current_created_at or self._task_now()
                    deadline_minutes_new = max(0, int((due_at_new - base_at).total_seconds() // 60)) or None
            elif has_deadline:
                deadline_minutes_new = self._normalize_task_deadline_minutes(deadline_minutes)
                due_at_new = self._task_now() + timedelta(minutes=deadline_minutes_new) if deadline_minutes_new else None
            else:
                deadline_minutes_new = current_deadline_minutes
                due_at_new = current_due_at

            estimate_minutes_new = (
                self._normalize_task_estimate_minutes(estimate_minutes) if has_estimate else current_estimate_minutes
            )
            planned_start_at_new = (
                self._parse_task_datetime(planned_start_at, "INVALID_PLANNED_START")
                if has_planned_start else current_planned_start_at
            )

            if has_origin:
                requested_by_id_new, requested_by_name_new = self._normalize_task_origin(
                    requested_by_id if requested_by_id is not _UNSET else None,
                    requested_by_name if requested_by_name is not _UNSET else None
                )
            else:
                requested_by_id_new = current_requested_by_id
                requested_by_name_new = current_requested_by_name

            reminder_new = (
                self._normalize_task_reminder_minutes(reminder_minutes_before) if has_reminder else current_reminder
            )
            if due_at_new is None:
                reminder_new = None

            if has_is_regulation and not bool(is_regulation):
                recurrence_type_input = None
                recurrence_interval_input = None
                is_regulation_input = False
            else:
                recurrence_type_input = recurrence_type if has_recurrence_type else current_recurrence_type
                recurrence_interval_input = recurrence_interval if has_recurrence_interval else current_recurrence_interval
                is_regulation_input = is_regulation if has_is_regulation else current_is_regulation
            is_regulation_new, recurrence_type_new, recurrence_interval_new = self._normalize_task_recurrence(
                is_regulation=is_regulation_input,
                recurrence_type=recurrence_type_input,
                recurrence_interval=recurrence_interval_input
            )
            recurrence_schedule_changed = (
                is_regulation_new != current_is_regulation
                or recurrence_type_new != current_recurrence_type
                or recurrence_interval_new != current_recurrence_interval
            )
            recurrence_next_at_new = (
                self._add_task_recurrence_interval(self._task_now(), recurrence_type_new, recurrence_interval_new)
                if recurrence_type_new and recurrence_schedule_changed
                else (current_recurrence_next_at if recurrence_type_new else None)
            )

            changed_fields = []
            if subject_new != current_subject:
                changed_fields.append("subject")
            if description_new != current_description:
                changed_fields.append("description")
            if tag_new != current_tag:
                changed_fields.append("tag")
            if priority_new != current_priority:
                changed_fields.append("priority")
            # Сравниваем множества, а не списки: смена порядка показа — это не
            # изменение состава, и рассылать из-за неё уведомления не за что.
            added_assignees = [pid for pid in assignee_ids_new if pid not in set(current_assignee_ids)]
            removed_assignees = [pid for pid in current_assignee_ids if pid not in set(assignee_ids_new)]
            if added_assignees or removed_assignees:
                changed_fields.append("assigned_to")
            if self._task_production_needs_assignee_check(
                    production_brief_new, tag_new, current_tag, added_assignees, removed_assignees):
                self._require_task_production_assignee_tx(cursor, assignee_ids_new)
            if production_brief_new != current_production_brief:
                changed_fields.append("production_brief")
            if deadline_minutes_new != current_deadline_minutes or due_at_new != current_due_at:
                changed_fields.append("deadline")
            if recurrence_schedule_changed:
                changed_fields.append("recurrence")
            if has_checklist:
                changed_fields.append("checklist")
            if estimate_minutes_new != current_estimate_minutes:
                changed_fields.append("estimate")
            if planned_start_at_new != current_planned_start_at:
                changed_fields.append("planned_start")
            if (requested_by_id_new != current_requested_by_id
                    or requested_by_name_new != current_requested_by_name):
                changed_fields.append("requested_by")
            if reminder_new != current_reminder:
                changed_fields.append("reminder")
            if project_changed:
                changed_fields.append("project")
            if watchers_changed:
                changed_fields.append("watchers")
            if section_changed:
                changed_fields.append("section")

            if not changed_fields:
                return {
                    "task_id": task_id,
                    "updated": False,
                    "changed_fields": [],
                    "updated_at": None,
                    "subject": current_subject,
                    "description": current_description,
                    "tag": current_tag,
                    "priority": current_priority,
                    "assigned_to": current_assigned_to,
                    "previous_assigned_to": current_assigned_to,
                    "assignees": list(current_assignee_ids),
                    "previous_assignees": list(current_assignee_ids),
                    "added_assignees": [],
                    "removed_assignees": [],
                    "deadline_duration_minutes": current_deadline_minutes,
                    "due_at": self._task_dt_to_iso(current_due_at),
                    "is_regulation": current_is_regulation,
                    "recurrence_type": current_recurrence_type,
                    "recurrence_interval": current_recurrence_interval,
                    "recurrence_next_at": self._task_dt_to_iso(current_recurrence_next_at),
                    "estimate_minutes": current_estimate_minutes,
                    "planned_start_at": self._task_dt_to_iso(current_planned_start_at),
                    "production_brief": current_production_brief
                }

            cursor.execute("""
                UPDATE tasks
                SET
                    subject = %s,
                    description = %s,
                    tag = %s,
                    priority = %s,
                    assigned_to = %s,
                    deadline_duration_minutes = %s,
                    due_at = %s,
                    is_regulation = %s,
                    recurrence_type = %s,
                    recurrence_interval = %s,
                    recurrence_next_at = %s,
                    estimate_minutes = %s,
                    planned_start_at = %s,
                    requested_by_id = %s,
                    requested_by_name = %s,
                    reminder_minutes_before = %s,
                    production_brief = %s,
                    {reset_reminder_sql}
                    {reset_info_request_sql}
                    updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE id = %s
                RETURNING updated_at
            """.format(
                reset_reminder_sql=(
                    'reminder_sent_at = NULL,'
                    if ('deadline' in changed_fields or 'reminder' in changed_fields) else ''
                ),
                # Спрашивавшего сняли с задачи — открытый запрос информации
                # снимаем: иначе постановщика вечно дёргает вопрос от человека,
                # которого на задаче уже нет, а закрыть его некому (ответить не
                # на что). Правило именно про АВТОРА вопроса, а не про смену
                # первого исполнителя: пока автор в составе, его вопрос жив,
                # даже если рядом появились или ушли коллеги.
                reset_info_request_sql=(
                    'info_request_id = NULL, info_request_at = NULL,'
                    if (info_request_author_id is not None
                        and int(info_request_author_id) in set(removed_assignees)) else ''
                )
            ), (
                subject_new,
                description_new,
                tag_new,
                priority_new,
                assigned_to_new,
                deadline_minutes_new,
                due_at_new,
                is_regulation_new,
                recurrence_type_new,
                recurrence_interval_new,
                recurrence_next_at_new,
                estimate_minutes_new,
                planned_start_at_new,
                requested_by_id_new,
                requested_by_name_new,
                reminder_new,
                Json(production_brief_new) if production_brief_new is not None else None,
                task_id
            ))
            updated_row = cursor.fetchone()
            updated_at = updated_row[0] if updated_row else None

            self._sync_task_assignees_tx(cursor, task_id, assignee_ids_new, added_by=requester_id)

            project_key_new = task_projects.format_task_key(current_project_code, current_project_number)
            if project_changed:
                project_number_new = None
                project_code_new = None
                if project_id_new is not None:
                    project_number_new, project_code_new = self._take_task_project_number_tx(cursor, project_id_new)
                if current_project_code and current_project_number:
                    cursor.execute("""
                        INSERT INTO task_key_aliases (code, number, task_id, project_id)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (code, number) DO NOTHING
                    """, (current_project_code, int(current_project_number), task_id, current_project_id))
                cursor.execute(
                    "UPDATE tasks SET project_id = %s, project_number = %s WHERE id = %s",
                    (project_id_new, project_number_new, task_id),
                )
                project_key_new = task_projects.format_task_key(project_code_new, project_number_new)
            if watchers_changed:
                self._sync_task_watchers_tx(cursor, task_id, watcher_ids_new, added_by=requester_id)
            if section_changed:
                cursor.execute("UPDATE tasks SET section_id = %s WHERE id = %s", (section_id_new, task_id))

            # Съехал срок или интервал — напоминание должно сработать заново у
            # ВСЕХ исполнителей, а не только обнулиться на самой задаче.
            if 'deadline' in changed_fields or 'reminder' in changed_fields:
                cursor.execute(
                    "UPDATE task_assignees SET reminder_sent_at = NULL WHERE task_id = %s",
                    (task_id,)
                )

            if has_checklist:
                cursor.execute("DELETE FROM task_checklist_items WHERE task_id = %s", (task_id,))
                self._insert_task_checklist_items(cursor, task_id, checklist_items)

            return {
                "task_id": task_id,
                "updated": True,
                "changed_fields": changed_fields,
                "updated_at": self._task_dt_to_iso(updated_at),
                "subject": subject_new,
                "description": description_new,
                "tag": tag_new,
                "priority": priority_new,
                "assigned_to": assigned_to_new,
                "previous_assigned_to": current_assigned_to,
                # Состав целиком и его диф: роут по added рассылает «вам поручили»,
                # по removed — «с вас сняли», иначе снятый об этом не узнает.
                "assignees": list(assignee_ids_new),
                "previous_assignees": list(current_assignee_ids),
                "added_assignees": list(added_assignees),
                "removed_assignees": list(removed_assignees),
                "deadline_duration_minutes": deadline_minutes_new,
                "due_at": self._task_dt_to_iso(due_at_new),
                "is_regulation": is_regulation_new,
                "recurrence_type": recurrence_type_new,
                "recurrence_interval": recurrence_interval_new,
                "recurrence_next_at": self._task_dt_to_iso(recurrence_next_at_new),
                "estimate_minutes": estimate_minutes_new,
                "planned_start_at": self._task_dt_to_iso(planned_start_at_new),
                "reminder_minutes_before": reminder_new,
                "production_brief": production_brief_new,
                "project_id": project_id_new,
                "key": project_key_new,
                "watcher_ids": list(watcher_ids_new),
                "section_id": section_id_new
            }

    def _serialize_task_report(self, row):
        if not row:
            return None
        return {
            "id": row[0],
            "task_id": row[1],
            "author_id": row[2],
            "author_name": row[3],
            "kind": row[4],
            "body": row[5],
            "spent_minutes": row[6],
            "created_at": self._task_dt_to_iso(row[7]),
            "updated_at": self._task_dt_to_iso(row[8])
        }

    def _task_report_access_tx(self, cursor, task_id, requester_id, role, allow_observer=False):
        """Возвращает (created_by, assignee_ids) или бросает, если задача недоступна.

        allow_observer — только для чтения журнала: СВ-коллега задачу видит, но
        писать, править и удалять отчёты в ней не может.
        """
        cursor.execute("""
            SELECT t.id, t.created_by, t.requested_by_id
            FROM tasks t
            WHERE t.id = %s
        """, (int(task_id),))
        row = cursor.fetchone()
        if not row:
            raise ValueError("TASK_NOT_FOUND")
        created_by, requested_by = row[1], row[2]
        assignee_scope = self._task_assignee_scope_tx(cursor, int(task_id))
        if not self._task_visible_for_requester(role, requester_id, created_by, assignee_scope, requested_by):
            if not (allow_observer and self._task_observable_tx(cursor, role, requester_id, task_id, created_by)):
                raise PermissionError("TASK_FORBIDDEN")
        return created_by, [person_id for person_id, _role, _supervisor in assignee_scope]

    def get_task_reports(self, task_id, requester_id, requester_role):
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        with self._get_cursor() as cursor:
            self._task_report_access_tx(cursor, task_id, requester_id, role, allow_observer=True)
            cursor.execute(
                self._TASK_REPORT_SELECT + " WHERE r.task_id = %s ORDER BY r.created_at ASC, r.id ASC",
                (int(task_id),)
            )
            rows = cursor.fetchall()
        return [self._serialize_task_report(row) for row in rows]

    def create_task_report(self, task_id, requester_id, requester_role, body, spent_minutes=None, kind='progress'):
        """Отчёт о проделанной работе. Пишут исполнитель, постановщик и админ."""
        task_id = int(task_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        body_norm = str(body or '').strip()
        if not body_norm:
            raise ValueError("REPORT_BODY_REQUIRED")
        kind_norm = self._normalize_task_report_kind(kind)
        spent_norm = self._normalize_task_spent_minutes(spent_minutes)

        with self._get_cursor() as cursor:
            created_by, assignee_ids = self._task_report_access_tx(cursor, task_id, requester_id, role)
            # Отчёт пишет ЛЮБОЙ исполнитель: они равноправны, и работу по этапам
            # делает каждый. Автор отчёта виден в самой записи, а сумма
            # трудозатрат по задаче складывается из отчётов всех участников.
            is_participant = (
                requester_id in assignee_ids
                or (created_by is not None and int(created_by) == requester_id)
                or role_has_min(role, 'admin')
            )
            if not is_participant:
                raise PermissionError("ONLY_TASK_PARTICIPANT")

            cursor.execute("""
                INSERT INTO task_reports (task_id, author_id, kind, body, spent_minutes)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (task_id, requester_id, kind_norm, body_norm, spent_norm))
            report_id = cursor.fetchone()[0]

            # Итоговый отчёт — источник «Итогов выполнения» задачи (Telegram, виджеты, выгрузки).
            if kind_norm == 'completion':
                cursor.execute("""
                    UPDATE tasks
                    SET completion_summary = %s,
                        updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                """, (body_norm, task_id))

            cursor.execute(self._TASK_REPORT_SELECT + " WHERE r.id = %s", (report_id,))
            return self._serialize_task_report(cursor.fetchone())

    def update_task_report(self, report_id, requester_id, requester_role, body=_UNSET, spent_minutes=_UNSET):
        report_id = int(report_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        has_body = body is not _UNSET
        has_spent = spent_minutes is not _UNSET
        if not (has_body or has_spent):
            raise ValueError("NOTHING_TO_UPDATE")

        with self._get_cursor() as cursor:
            cursor.execute("SELECT id, task_id, author_id, kind, body, spent_minutes FROM task_reports WHERE id = %s", (report_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("REPORT_NOT_FOUND")
            task_id, author_id, kind, current_body, current_spent = row[1], row[2], row[3], row[4], row[5]
            self._task_report_access_tx(cursor, task_id, requester_id, role)

            # Править чужой отчёт нельзя даже админу: это чужие слова о своей работе.
            if author_id is None or int(author_id) != requester_id:
                raise PermissionError("ONLY_REPORT_AUTHOR")

            body_new = current_body
            if has_body:
                body_new = str(body or '').strip()
                if not body_new:
                    raise ValueError("REPORT_BODY_REQUIRED")
            spent_new = self._normalize_task_spent_minutes(spent_minutes) if has_spent else current_spent

            if body_new == current_body and spent_new == current_spent:
                cursor.execute(self._TASK_REPORT_SELECT + " WHERE r.id = %s", (report_id,))
                return self._serialize_task_report(cursor.fetchone())

            cursor.execute("""
                UPDATE task_reports
                SET body = %s,
                    spent_minutes = %s,
                    updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE id = %s
            """, (body_new, spent_new, report_id))

            # «Итоги выполнения» задачи отражают последний итоговый отчёт — правим только его.
            if kind == 'completion' and body_new != current_body:
                cursor.execute("""
                    SELECT id FROM task_reports
                    WHERE task_id = %s AND kind = 'completion'
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                """, (task_id,))
                latest = cursor.fetchone()
                if latest and int(latest[0]) == report_id:
                    cursor.execute("""
                        UPDATE tasks
                        SET completion_summary = %s,
                            updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                        WHERE id = %s
                    """, (body_new, task_id))

            cursor.execute(self._TASK_REPORT_SELECT + " WHERE r.id = %s", (report_id,))
            return self._serialize_task_report(cursor.fetchone())

    def delete_task_report(self, report_id, requester_id, requester_role):
        report_id = int(report_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)

        with self._get_cursor() as cursor:
            cursor.execute("SELECT id, task_id, author_id, kind FROM task_reports WHERE id = %s", (report_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("REPORT_NOT_FOUND")
            task_id, author_id, kind = row[1], row[2], row[3]
            self._task_report_access_tx(cursor, task_id, requester_id, role)

            is_author = author_id is not None and int(author_id) == requester_id
            if not (is_author or role_has_min(role, 'admin')):
                raise PermissionError("ONLY_REPORT_AUTHOR")

            cursor.execute("DELETE FROM task_reports WHERE id = %s", (report_id,))

            # Удалили итоговый — «Итоги выполнения» подтягиваем из предыдущего итогового отчёта.
            if kind == 'completion':
                cursor.execute("""
                    SELECT body FROM task_reports
                    WHERE task_id = %s AND kind = 'completion'
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                """, (task_id,))
                fallback = cursor.fetchone()
                cursor.execute("""
                    UPDATE tasks
                    SET completion_summary = %s,
                        updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                """, (fallback[0] if fallback else None, task_id))

        return {"report_id": report_id, "task_id": task_id, "deleted": True}

    def _serialize_task_message(self, row):
        if not row:
            return None
        return {
            "id": row[0],
            "task_id": row[1],
            "author_id": row[2],
            "author_name": row[3],
            "kind": row[4],
            "body": row[5],
            "reply_to_id": row[6],
            "resolved_at": self._task_dt_to_iso(row[7]),
            "resolved_by": row[8],
            "resolved_by_name": row[9],
            "created_at": self._task_dt_to_iso(row[10]),
            "attachments": []
        }

    def _task_message_attachments_tx(self, cursor, message_ids):
        """Файлы, приехавшие с уточнениями, — теми же полями, что и файлы задачи."""
        ids = [int(item) for item in (message_ids or []) if item]
        if not ids:
            return {}
        cursor.execute("""
            SELECT
                a.id, a.message_id, a.file_name, a.content_type, a.file_size, a.created_at,
                COALESCE(a.storage_type, 'db'), a.gcs_bucket, a.gcs_blob_path,
                COALESCE(a.attachment_kind, 'initial')
            FROM task_attachments a
            WHERE a.message_id = ANY(%s)
            ORDER BY a.id ASC
        """, (ids,))
        grouped = defaultdict(list)
        for row in cursor.fetchall():
            grouped[row[1]].append({
                "id": row[0],
                "message_id": row[1],
                "file_name": row[2],
                "content_type": row[3],
                "file_size": row[4],
                "created_at": self._task_dt_to_iso(row[5]),
                "storage_type": row[6],
                "gcs_bucket": row[7],
                "gcs_blob_path": row[8],
                "attachment_kind": row[9]
            })
        return grouped

    def _task_message_access_tx(self, cursor, task_id, requester_id, role, lock=False, allow_observer=False):
        """Задача глазами ленты уточнений: кто есть кто и есть ли открытый запрос.

        lock — блокировка строки задачи на время записи: правило «открытый запрос
        один» иначе проходит дважды, если исполнитель дважды щёлкнул кнопку.
        """
        cursor.execute("""
            SELECT t.id, t.created_by, t.assigned_to,
                   t.requested_by_id, t.status, t.info_request_id
            FROM tasks t
            WHERE t.id = %s
        """ + (" FOR UPDATE OF t" if lock else ""), (int(task_id),))
        row = cursor.fetchone()
        if not row:
            raise ValueError("TASK_NOT_FOUND")
        # Состав читаем отдельным запросом: блокировка остаётся на строке задачи
        # (правило «открытый запрос один» защищает именно задачу), а связь под
        # FOR UPDATE тянуть незачем.
        assignee_scope = self._task_assignee_scope_tx(cursor, int(row[0]))
        if not self._task_visible_for_requester(role, requester_id, row[1], assignee_scope, row[3]):
            # Ленту уточнений СВ-коллега читает (allow_observer), но не пишет в неё.
            if not (allow_observer and self._task_observable_tx(cursor, role, requester_id, row[0], row[1])):
                raise PermissionError("TASK_FORBIDDEN")
        return {
            "id": row[0],
            "created_by": row[1],
            "assigned_to": row[2],
            "assignee_ids": [person_id for person_id, _role, _supervisor in assignee_scope],
            "requested_by_id": row[3],
            "status": row[4],
            "info_request_id": row[5]
        }

    @staticmethod
    def _task_answer_authority(task_row):
        """Кто отвечает за постановку: поручитель, а если его нет — постановщик.

        Тот же порядок, что у приёмки итога (get_task_action_needs_summary,
        taskActionNeeds.js::reviewAuthorityId): у задачи один ответственный за
        «что нужно сделать», и это он же закрывает запрос информации.
        """
        return int(task_row.get('requested_by_id') or 0) or int(task_row.get('created_by') or 0)

    def get_task_messages(self, task_id, requester_id, requester_role):
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        with self._get_cursor() as cursor:
            self._task_message_access_tx(cursor, task_id, requester_id, role, allow_observer=True)
            cursor.execute(
                self._TASK_MESSAGE_SELECT + " WHERE m.task_id = %s ORDER BY m.created_at ASC, m.id ASC",
                (int(task_id),)
            )
            messages = [self._serialize_task_message(row) for row in cursor.fetchall()]
            files = self._task_message_attachments_tx(cursor, [item['id'] for item in messages])
        for message in messages:
            message['attachments'] = files.get(message['id'], [])
        return messages

    def create_task_message(self, task_id, requester_id, requester_role, kind, body, attachments=None):
        """Уточнение по задаче: дополнение, запрос информации либо ответ на него.

        Права: дополнять и отвечать — постановщик, поручитель или админ;
        просить информацию — только исполнитель. Открытый запрос у задачи один:
        пока на него не ответили и его не отозвали, второй не создать, иначе
        постановщика засыпало бы одинаковыми напоминаниями.
        """
        task_id = int(task_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        kind_norm = self._normalize_task_message_kind(kind)
        body_norm = str(body or '').strip()
        file_items = [item for item in (attachments or []) if item]
        if not body_norm and not file_items:
            raise ValueError("MESSAGE_BODY_REQUIRED")

        with self._get_cursor() as cursor:
            task_row = self._task_message_access_tx(cursor, task_id, requester_id, role, lock=True)
            created_by = int(task_row.get('created_by') or 0)
            assignee_ids = list(task_row.get('assignee_ids') or [])
            answer_authority = self._task_answer_authority(task_row)
            is_owner_side = (
                requester_id in (created_by, int(task_row.get('requested_by_id') or 0))
                or role_has_min(role, 'admin')
            )
            open_request_id = task_row.get('info_request_id')

            if kind_norm == 'request':
                # Спрашивает любой из исполнителей — работу делает каждый, и
                # не хватать данных может каждому.
                if requester_id not in assignee_ids:
                    raise PermissionError("ONLY_ASSIGNEE_ASKS")
                # Ровно те же статусы, по которым причина «просят информацию»
                # попадает в бейдж и колокол. Иначе на сданной задаче ушло бы
                # Telegram-уведомление, а бейдж молчал — уведомление-призрак.
                if str(task_row.get('status') or '') not in ('assigned', 'in_progress', 'returned'):
                    raise ValueError("TASK_CLOSED_FOR_REQUEST")
                # Спросить некого: постановка целиком своя (задача себе без поручителя).
                if not answer_authority or answer_authority == requester_id:
                    raise ValueError("NO_ONE_TO_ASK")
                # Открытый запрос по-прежнему ОДИН на задачу, а не на человека:
                # правило постановщика не менялось, ответ он пишет один и виден
                # он всем исполнителям. Пока вопрос коллеги не закрыт, второй
                # исполнитель дожидается того же ответа.
                if open_request_id:
                    raise ValueError("REQUEST_ALREADY_OPEN")
            elif kind_norm == 'answer':
                if not is_owner_side:
                    raise PermissionError("ONLY_TASK_OWNER_ANSWERS")
                if not open_request_id:
                    raise ValueError("NO_OPEN_REQUEST")
            else:
                if not is_owner_side:
                    raise PermissionError("ONLY_TASK_OWNER_ADDS")

            reply_to_id = open_request_id if kind_norm == 'answer' else None
            now = self._task_now()
            cursor.execute("""
                INSERT INTO task_messages (task_id, author_id, kind, body, reply_to_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (task_id, requester_id, kind_norm, body_norm, reply_to_id, now))
            message_id = cursor.fetchone()[0]

            for attachment in file_items:
                file_name = (attachment.get('file_name') or 'attachment').strip() or 'attachment'
                content_type = (attachment.get('content_type') or '').strip() or None
                storage_type = (attachment.get('storage_type') or 'gcs').strip().lower() or 'gcs'
                if storage_type != 'gcs':
                    continue
                gcs_bucket = (attachment.get('gcs_bucket') or '').strip() or None
                gcs_blob_path = (attachment.get('gcs_blob_path') or '').strip() or None
                if not gcs_bucket or not gcs_blob_path:
                    continue
                cursor.execute("""
                    INSERT INTO task_attachments (
                        task_id, file_name, content_type, file_size, file_data,
                        storage_type, gcs_bucket, gcs_blob_path, attachment_kind, uploaded_by,
                        thumb_blob_path, message_id
                    )
                    VALUES (%s, %s, %s, %s, NULL, 'gcs', %s, %s, 'initial', %s, %s, %s)
                """, (
                    task_id, file_name, content_type, int(attachment.get('file_size') or 0),
                    gcs_bucket, gcs_blob_path, requester_id,
                    (attachment.get('thumb_blob_path') or '').strip() or None, message_id
                ))

            if kind_norm == 'answer':
                cursor.execute("""
                    UPDATE task_messages
                    SET resolved_at = %s, resolved_by = %s
                    WHERE id = %s AND resolved_at IS NULL
                """, (now, requester_id, int(open_request_id)))

            # Метка задачи и её updated_at: по ним считаются бейдж «ждут вас» и
            # колокол, а UPDATE задачи заодно будит триггер мгновенных уведомлений.
            # Открытый запрос трогают только запрос и ответ: дополнение к
            # постановке НЕ закрывает чужой вопрос — постановщик мог дописать
            # совсем не про то, о чём спрашивали.
            if kind_norm == 'request':
                cursor.execute("""
                    UPDATE tasks
                    SET info_request_id = %s,
                        info_request_at = %s,
                        updated_at = %s
                    WHERE id = %s
                """, (message_id, now, now, task_id))
            elif kind_norm == 'answer':
                cursor.execute("""
                    UPDATE tasks
                    SET info_request_id = NULL,
                        info_request_at = NULL,
                        updated_at = %s
                    WHERE id = %s
                """, (now, task_id))
            else:
                cursor.execute("""
                    UPDATE tasks
                    SET updated_at = %s
                    WHERE id = %s
                """, (now, task_id))

            cursor.execute(self._TASK_MESSAGE_SELECT + " WHERE m.id = %s", (message_id,))
            message = self._serialize_task_message(cursor.fetchone())
            message['attachments'] = self._task_message_attachments_tx(cursor, [message_id]).get(message_id, [])
        return message

    def withdraw_task_info_request(self, message_id, requester_id, requester_role):
        """Снять свой запрос информации: спрашивавший разобрался сам.

        Иначе ошибочный клик навсегда оставил бы постановщику напоминание,
        закрыть которое он не может — ответить-то не на что.
        """
        message_id = int(message_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, task_id, author_id, kind, resolved_at
                FROM task_messages WHERE id = %s
            """, (message_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("MESSAGE_NOT_FOUND")
            task_id, author_id, kind, resolved_at = row[1], row[2], row[3], row[4]
            self._task_message_access_tx(cursor, task_id, requester_id, role, lock=True)
            if kind != 'request':
                raise ValueError("NOT_A_REQUEST")
            if author_id is None or int(author_id) != requester_id:
                raise PermissionError("ONLY_REQUEST_AUTHOR")
            now = self._task_now()
            if resolved_at is None:
                cursor.execute("""
                    UPDATE task_messages
                    SET resolved_at = %s, resolved_by = %s
                    WHERE id = %s
                """, (now, requester_id, message_id))
            cursor.execute("""
                UPDATE tasks
                SET info_request_id = NULL,
                    info_request_at = NULL,
                    updated_at = %s
                WHERE id = %s AND info_request_id = %s
            """, (now, task_id, message_id))
        return {"message_id": message_id, "task_id": task_id, "withdrawn": True}

    def update_task_board_state(
            self,
            task_id,
            requester_id,
            requester_role,
            is_backlog=_UNSET,
            backlog_rank=_UNSET,
            estimate_minutes=_UNSET,
            planned_start_at=_UNSET,
            due_at=_UNSET,
            reminder_minutes_before=_UNSET,
            priority_score=_UNSET
    ):
        """
        Планирующие правки с доски: бэклог ↔ доска, порядок приоритезации, оценка, сроки.

        Отличается от edit_task правами: двигать карточку и ставить оценку может
        и исполнитель (это его работа), а сроки — только постановщик или админ.
        Балл приоритета (1–5) ставят только суперадмины — по итогам совещания по бэклогу.
        """
        task_id = int(task_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)

        has_is_backlog = is_backlog is not _UNSET
        has_backlog_rank = backlog_rank is not _UNSET
        has_estimate = estimate_minutes is not _UNSET
        has_planned_start = planned_start_at is not _UNSET
        has_due_at = due_at is not _UNSET
        has_reminder = reminder_minutes_before is not _UNSET
        has_priority_score = priority_score is not _UNSET

        if not any([has_is_backlog, has_backlog_rank, has_estimate, has_planned_start, has_due_at, has_reminder,
                    has_priority_score]):
            raise ValueError("NOTHING_TO_UPDATE")

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    t.id, t.created_by, t.assigned_to, t.status, t.created_at,
                    t.is_backlog, t.backlog_rank, t.estimate_minutes, t.planned_start_at,
                    t.due_at, t.deadline_duration_minutes, t.subject,
                    t.reminder_minutes_before, t.requested_by_id, t.priority_score,
                    t.is_regulation, t.regulation_parent_id
                FROM tasks t
                WHERE t.id = %s
                -- Под замком строки, как у смены статуса: строка ниже переписывается
                -- целиком из этого чтения, и балл, поставленный в ту секунду, когда
                -- исполнитель взял задачу в работу, иначе вернул бы её в бэклог.
                FOR UPDATE OF t
            """, (task_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("TASK_NOT_FOUND")

            created_by = row[1]
            assigned_to = row[2]
            current_status = row[3]
            current_created_at = row[4]
            current_is_backlog = bool(row[5])
            current_backlog_rank = row[6]
            current_estimate = row[7]
            current_planned_start = row[8]
            current_due_at = row[9]
            current_deadline_minutes = row[10]
            subject = row[11]
            current_reminder = row[12]
            requested_by = row[13]
            current_priority_score = row[14]
            # Шаблон регламента: в бэклоге он на паузе — materialize_due_regulation_tasks
            # берёт только шаблоны вне бэклога.
            is_regulation_template = bool(row[15]) and row[16] is None
            assignee_scope = self._task_assignee_scope_tx(cursor, task_id)
            assignee_ids = [person_id for person_id, _role, _supervisor in assignee_scope]

            if not self._task_visible_for_requester(role, requester_id, created_by, assignee_scope, requested_by):
                raise PermissionError("TASK_FORBIDDEN")

            is_creator = created_by is not None and int(created_by) == requester_id
            # Карточку тащит любой исполнитель: статус у задачи один, и «взял в
            # работу» от соисполнителя — такое же законное движение.
            is_assignee = requester_id in assignee_ids
            is_admin = role_has_min(role, 'admin')
            if not (is_creator or is_assignee or is_admin):
                raise PermissionError("TASK_FORBIDDEN")
            if (has_due_at or has_planned_start) and not (is_creator or is_admin):
                raise PermissionError("ONLY_CREATOR_CAN_PLAN")
            # Балл — решение совещания по бэклогу, его ставят только суперадмины.
            if has_priority_score and not role_has_min(role, 'super_admin'):
                raise PermissionError("ONLY_SUPER_ADMIN_SCORE")

            is_backlog_new = bool(is_backlog) if has_is_backlog else current_is_backlog
            # Первый балл переводит задачу из «Новой» в бэклог: «Новая» — то, что ещё не
            # разобрали на совещании, бэклог — очередь уже оценённых (решение владельца
            # 06.11.2026). Смена балла у оценённой и снятие балла задачу не двигают.
            # Шаблон регламента остаётся на месте: в бэклоге он перестал бы рождать
            # повторы, а балл шаблона на сами повторы не переходит.
            priority_score_new = (
                self._normalize_task_priority_score(priority_score) if has_priority_score else current_priority_score
            )
            moved_by_score = bool(
                has_priority_score and not has_is_backlog and priority_score_new is not None
                and current_priority_score is None and current_status == 'assigned'
                and not current_is_backlog and not is_regulation_template
            )
            if moved_by_score:
                is_backlog_new = True
            # Снятый балл возвращает задачу из бэклога в «Новую» — снова к разбору
            # (владелец 06.11.2026). Снимают его в карточке задачи. Шаблон регламента —
            # нет: вне бэклога он ожил бы и сразу догнал пропущенные повторы.
            unscored_to_new = bool(
                has_priority_score and not has_is_backlog and priority_score_new is None
                and current_priority_score is not None and current_is_backlog
                and current_status == 'assigned' and not is_regulation_template
            )
            if unscored_to_new:
                is_backlog_new = False
            # В бэклог возвращаем только ещё не начатую задачу — иначе доска врала бы про прогресс.
            # Проверяем именно переход: правки сроков уже лежащей в бэклоге задачи блокировать не за что.
            if is_backlog_new and not current_is_backlog and current_status != 'assigned':
                raise ValueError("BACKLOG_ONLY_FOR_ASSIGNED")

            if has_backlog_rank:
                if backlog_rank is None:
                    backlog_rank_new = None
                else:
                    try:
                        backlog_rank_new = float(backlog_rank)
                    except Exception:
                        raise ValueError("INVALID_BACKLOG_RANK")
            elif is_backlog_new and (current_backlog_rank is None or moved_by_score or (
                    has_priority_score and current_priority_score is None and priority_score_new is not None)):
                # Только что оценённая встаёт в конец своего балла: старый ранг остался
                # от прежнего захода в бэклог и к сегодняшней очереди не относится. То же —
                # у задачи, которую положили в бэклог без балла руками и оценили там.
                cursor.execute("SELECT COALESCE(MAX(backlog_rank), 0) + 1 FROM tasks WHERE is_backlog = TRUE")
                backlog_rank_new = float((cursor.fetchone() or [1])[0] or 1)
            else:
                backlog_rank_new = current_backlog_rank

            estimate_new = (
                self._normalize_task_estimate_minutes(estimate_minutes) if has_estimate else current_estimate
            )
            planned_start_new = (
                self._parse_task_datetime(planned_start_at, "INVALID_PLANNED_START")
                if has_planned_start else current_planned_start
            )

            if has_due_at:
                due_at_new = self._parse_task_datetime(due_at, "INVALID_DEADLINE")
                if due_at_new is None:
                    deadline_minutes_new = None
                else:
                    base_at = current_created_at or self._task_now()
                    deadline_minutes_new = max(0, int((due_at_new - base_at).total_seconds() // 60)) or None
            else:
                due_at_new = current_due_at
                deadline_minutes_new = current_deadline_minutes

            reminder_new = (
                self._normalize_task_reminder_minutes(reminder_minutes_before) if has_reminder else current_reminder
            )
            # Без дедлайна напоминать не от чего.
            if due_at_new is None:
                reminder_new = None

            changed_fields = []
            if is_backlog_new != current_is_backlog:
                changed_fields.append("is_backlog")
            if backlog_rank_new != current_backlog_rank:
                changed_fields.append("backlog_rank")
            if estimate_new != current_estimate:
                changed_fields.append("estimate")
            if planned_start_new != current_planned_start:
                changed_fields.append("planned_start")
            if due_at_new != current_due_at:
                changed_fields.append("deadline")
            if reminder_new != current_reminder:
                changed_fields.append("reminder")
            if priority_score_new != current_priority_score:
                changed_fields.append("priority_score")

            if changed_fields:
                # Съехал срок или интервал — напоминание должно сработать заново у всего
                # состава, а не только обнулиться на самой задаче.
                reset_sent = ('deadline' in changed_fields) or ('reminder' in changed_fields)
                if reset_sent:
                    cursor.execute(
                        "UPDATE task_assignees SET reminder_sent_at = NULL WHERE task_id = %s",
                        (task_id,)
                    )
                cursor.execute(f"""
                    UPDATE tasks
                    SET
                        is_backlog = %s,
                        backlog_rank = %s,
                        estimate_minutes = %s,
                        planned_start_at = %s,
                        due_at = %s,
                        deadline_duration_minutes = %s,
                        reminder_minutes_before = %s,
                        priority_score = %s,
                        {'reminder_sent_at = NULL,' if reset_sent else ''}
                        updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                """, (
                    is_backlog_new,
                    backlog_rank_new,
                    estimate_new,
                    planned_start_new,
                    due_at_new,
                    deadline_minutes_new,
                    reminder_new,
                    priority_score_new,
                    task_id
                ))

            return {
                "task_id": task_id,
                "subject": subject,
                "updated": bool(changed_fields),
                "changed_fields": changed_fields,
                "assigned_to": assigned_to,
                "status": current_status,
                "is_backlog": is_backlog_new,
                "was_backlog": current_is_backlog,
                # Куда задачу увёл балл: 'to_backlog' — первый балл, 'to_new' — снятый.
                # Ручка доски по нему не шлёт «вынесли в работу» на возврат в «Новую».
                "score_move": 'to_backlog' if moved_by_score else ('to_new' if unscored_to_new else None),
                "backlog_rank": backlog_rank_new,
                "estimate_minutes": estimate_new,
                "planned_start_at": self._task_dt_to_iso(planned_start_new),
                "due_at": self._task_dt_to_iso(due_at_new),
                "deadline_duration_minutes": deadline_minutes_new,
                "reminder_minutes_before": reminder_new,
                "priority_score": priority_score_new
            }

    def delete_task(self, task_id, requester_id, requester_role):
        task_id = int(task_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)

        if role != 'super_admin':
            raise PermissionError("ONLY_SUPER_ADMIN")

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    t.id, t.created_by, t.assigned_to
                FROM tasks t
                WHERE t.id = %s
            """, (task_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("TASK_NOT_FOUND")

            created_by = row[1]
            assigned_to = row[2]
            # Сами строки состава уйдут каскадом по task_id — чистить не нужно.
            assignee_scope = self._task_assignee_scope_tx(cursor, task_id)

            if not self._task_visible_for_requester(role, requester_id, created_by, assignee_scope):
                raise PermissionError("TASK_FORBIDDEN")

            cursor.execute("""
                SELECT
                    id, file_name,
                    COALESCE(storage_type, 'db'),
                    gcs_bucket, gcs_blob_path,
                    COALESCE(attachment_kind, 'initial'),
                    thumb_blob_path
                FROM task_attachments
                WHERE task_id = %s
                ORDER BY id ASC
            """, (task_id,))
            attachment_rows = cursor.fetchall()

            # Материалы раздела «Продакшн» строки уйдут каскадом, а объекты в
            # хранилище убирает маршрут — как и вложения выше.
            production_blobs = task_production_queries.blobs_of_task(cursor, task_id)

            cursor.execute("""
                DELETE FROM tasks
                WHERE id = %s
                RETURNING id
            """, (task_id,))
            deleted_row = cursor.fetchone()
            if not deleted_row:
                raise ValueError("TASK_NOT_FOUND")

        attachments = [
            {
                "id": row[0],
                "file_name": row[1],
                "storage_type": row[2],
                "gcs_bucket": row[3],
                "gcs_blob_path": row[4],
                "attachment_kind": row[5],
                # Миниатюра фотографии уходит из бакета вместе с кадром.
                "thumb_blob_path": row[6]
            }
            for row in attachment_rows
        ]

        return {
            "task_id": task_id,
            "deleted": True,
            "attachments": attachments,
            "production_blobs": production_blobs
        }

    def _serialize_task_note(self, row):
        if not row:
            return None
        return {
            "id": row[0],
            "owner_id": row[1],
            "title": row[2] or '',
            "body": row[3] or '',
            "priority": row[4] or 'normal',
            "due_at": self._task_dt_to_iso(row[5]),
            "is_task": bool(row[6]),
            "is_done": bool(row[7]),
            "completed_at": self._task_dt_to_iso(row[8]),
            "created_at": self._task_dt_to_iso(row[9]),
            "updated_at": self._task_dt_to_iso(row[10]),
            "saved_at": self._task_dt_to_iso(row[10]),
            "reminder_minutes_before": row[11] if len(row) > 11 else None,
            "reminder_sent_at": self._task_dt_to_iso(row[12]) if len(row) > 12 else None
        }

    def get_task_notes(self, owner_id):
        owner_id = int(owner_id)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    id, owner_id, title, body, priority, due_at,
                    is_task, is_done, completed_at, created_at, updated_at,
                    reminder_minutes_before, reminder_sent_at
                FROM task_notes
                WHERE owner_id = %s
                ORDER BY
                    CASE WHEN due_at IS NULL THEN 1 ELSE 0 END ASC,
                    is_done ASC,
                    due_at ASC NULLS LAST,
                    CASE priority WHEN 'critical' THEN 0 WHEN 'urgent' THEN 1 ELSE 2 END ASC,
                    updated_at DESC,
                    id DESC
            """, (owner_id,))
            rows = cursor.fetchall()
        return [self._serialize_task_note(row) for row in rows]

    def create_task_note(
            self,
            owner_id,
            title='',
            body='',
            priority='normal',
            due_at=None,
            is_task=False,
            is_done=False,
            reminder_minutes_before=None
    ):
        owner_id = int(owner_id)
        title_norm = str(title or '').strip()[:160] or 'Без темы'
        body_norm = str(body or '')
        priority_norm = self._normalize_task_note_priority(priority)
        due_at_norm = self._normalize_task_note_due_at(due_at)
        is_task_norm = bool(is_task)
        is_done_norm = bool(is_done) if is_task_norm else False
        completed_at = self._task_now() if is_done_norm else None
        # Напоминание живёт только при дедлайне.
        reminder_norm = self._normalize_task_reminder_minutes(reminder_minutes_before) if due_at_norm else None

        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO task_notes (
                    owner_id, title, body, priority, due_at,
                    is_task, is_done, completed_at, reminder_minutes_before
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING
                    id, owner_id, title, body, priority, due_at,
                    is_task, is_done, completed_at, created_at, updated_at,
                    reminder_minutes_before, reminder_sent_at
            """, (
                owner_id,
                title_norm,
                body_norm,
                priority_norm,
                due_at_norm,
                is_task_norm,
                is_done_norm,
                completed_at,
                reminder_norm
            ))
            row = cursor.fetchone()
        return self._serialize_task_note(row)

    def update_task_note(
            self,
            note_id,
            owner_id,
            title=_UNSET,
            body=_UNSET,
            priority=_UNSET,
            due_at=_UNSET,
            is_task=_UNSET,
            is_done=_UNSET,
            reminder_minutes_before=_UNSET
    ):
        note_id = int(note_id)
        owner_id = int(owner_id)

        if not any(value is not _UNSET for value in
                   (title, body, priority, due_at, is_task, is_done, reminder_minutes_before)):
            raise ValueError("NOTHING_TO_UPDATE")

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    id, owner_id, title, body, priority, due_at,
                    is_task, is_done, completed_at, created_at, updated_at,
                    reminder_minutes_before, reminder_sent_at
                FROM task_notes
                WHERE id = %s
            """, (note_id,))
            current = cursor.fetchone()
            if not current:
                raise ValueError("NOTE_NOT_FOUND")
            if int(current[1]) != owner_id:
                raise PermissionError("NOTE_FORBIDDEN")

            title_new = current[2] if title is _UNSET else (str(title or '').strip()[:160] or 'Без темы')
            body_new = current[3] if body is _UNSET else str(body or '')
            priority_new = current[4] if priority is _UNSET else self._normalize_task_note_priority(priority)
            due_at_new = current[5] if due_at is _UNSET else self._normalize_task_note_due_at(due_at)
            is_task_new = bool(current[6]) if is_task is _UNSET else bool(is_task)
            is_done_new = bool(current[7]) if is_done is _UNSET else bool(is_done)
            if not is_task_new:
                is_done_new = False

            completed_at_new = current[8]
            if is_done_new and not bool(current[7]):
                completed_at_new = self._task_now()
            elif not is_done_new:
                completed_at_new = None

            current_reminder = current[11]
            reminder_new = (
                current_reminder if reminder_minutes_before is _UNSET
                else self._normalize_task_reminder_minutes(reminder_minutes_before)
            )
            if due_at_new is None:
                reminder_new = None
            # Съехал срок или интервал — напоминание отправляем заново.
            reset_sent = (due_at_new != current[5]) or (reminder_new != current_reminder)

            cursor.execute("""
                UPDATE task_notes
                SET
                    title = %s,
                    body = %s,
                    priority = %s,
                    due_at = %s,
                    is_task = %s,
                    is_done = %s,
                    completed_at = %s,
                    reminder_minutes_before = %s,
                    {reset_reminder_sql}
                    updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE id = %s AND owner_id = %s
                RETURNING
                    id, owner_id, title, body, priority, due_at,
                    is_task, is_done, completed_at, created_at, updated_at,
                    reminder_minutes_before, reminder_sent_at
            """.format(reset_reminder_sql='reminder_sent_at = NULL,' if reset_sent else ''), (
                title_new,
                body_new,
                priority_new,
                due_at_new,
                is_task_new,
                is_done_new,
                completed_at_new,
                reminder_new,
                note_id,
                owner_id
            ))
            row = cursor.fetchone()
            if not row:
                raise ValueError("NOTE_NOT_FOUND")
        return self._serialize_task_note(row)

    def delete_task_note(self, note_id, owner_id):
        note_id = int(note_id)
        owner_id = int(owner_id)
        with self._get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM task_notes
                WHERE id = %s AND owner_id = %s
                RETURNING id
            """, (note_id, owner_id))
            row = cursor.fetchone()
            if not row:
                raise ValueError("NOTE_NOT_FOUND")
        return {"note_id": note_id, "deleted": True}

    def materialize_due_regulation_tasks(self, max_templates=25, max_occurrences_per_template=3):
        created_task_ids = []
        now = self._task_now()
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    t.id, t.subject, t.description, t.tag, t.priority, t.assigned_to, t.created_by,
                    t.deadline_duration_minutes, t.recurrence_type, t.recurrence_interval,
                    t.recurrence_next_at, t.estimate_minutes, t.production_brief, t.project_id,
                    t.section_id
                FROM tasks t
                WHERE t.is_regulation = TRUE
                  AND t.regulation_parent_id IS NULL
                  AND t.is_backlog = FALSE
                  AND t.recurrence_type IS NOT NULL
                  AND t.recurrence_next_at IS NOT NULL
                  AND t.recurrence_next_at <= %s
                ORDER BY t.recurrence_next_at ASC, t.id ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            """, (now, int(max_templates or 25)))
            templates = cursor.fetchall()

            for template in templates:
                (
                    root_id, subject, description, tag, priority, assigned_to, created_by,
                    deadline_minutes, recurrence_type, recurrence_interval, next_at,
                    template_estimate_minutes, template_production_brief, template_project_id,
                    template_section_id
                ) = template
                # Бриф продакшна копируется в итерацию как есть, вместе с типом:
                # иначе регламентный продакшн каждый раз рождался бы без ТЗ.
                template_production_brief = task_production.stored_brief(template_production_brief)

                cursor.execute("""
                    SELECT title, position, is_required
                    FROM task_checklist_items
                    WHERE task_id = %s
                    ORDER BY position ASC, id ASC
                """, (root_id,))
                checklist_rows = cursor.fetchall()

                # Состав шаблона копируется в каждую итерацию целиком. Иначе
                # регламент, поручённый троим, каждый раз рождал бы задачу на
                # одного — молча, без единой ошибки в логах.
                template_assignee_ids = self._task_assignee_ids_tx(cursor, root_id)
                if not template_assignee_ids and assigned_to is not None:
                    # Шаблон старше состава (или бэкфилл не дошёл) — берём
                    # скалярного исполнителя, задача без исполнителя не бывает.
                    template_assignee_ids = [int(assigned_to)]
                # Итерация живёт на доске шаблона и со своими наблюдателями
                # (задача #401) — номер в проекте у каждой свой.
                template_watcher_ids = self._task_watcher_ids_tx(cursor, root_id)

                occurrence_count = 0
                latest_next_at = next_at
                while latest_next_at and latest_next_at <= now and occurrence_count < int(max_occurrences_per_template or 3):
                    occurrence_count += 1
                    due_at = (
                        latest_next_at + timedelta(minutes=int(deadline_minutes))
                        if deadline_minutes is not None else None
                    )
                    cursor.execute("""
                        SELECT COALESCE(MAX(regulation_iteration), 0)
                        FROM tasks
                        WHERE regulation_parent_id = %s
                    """, (root_id,))
                    iteration_row = cursor.fetchone()
                    next_iteration = int(iteration_row[0] or 0) + 1
                    iteration_project_number = None
                    if template_project_id is not None:
                        iteration_project_number, _iteration_code = self._take_task_project_number_tx(
                            cursor, template_project_id
                        )

                    cursor.execute("""
                        INSERT INTO tasks (
                            subject, description, tag, priority, status, assigned_to, created_by,
                            deadline_duration_minutes, due_at, is_regulation,
                            recurrence_type, recurrence_interval, recurrence_next_at,
                            regulation_parent_id, regulation_iteration, estimate_minutes,
                            production_brief, project_id, project_number, section_id,
                            created_at, updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, 'assigned', %s, %s,
                            %s, %s, TRUE,
                            NULL, NULL, NULL,
                            %s, %s, %s,
                            %s, %s, %s, %s,
                            (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                            (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                        )
                        RETURNING id
                    """, (
                        subject,
                        description,
                        tag,
                        priority or 'normal',
                        assigned_to,
                        created_by,
                        deadline_minutes,
                        due_at,
                        root_id,
                        next_iteration,
                        template_estimate_minutes,
                        Json(template_production_brief) if template_production_brief is not None else None,
                        template_project_id,
                        iteration_project_number,
                        template_section_id
                    ))
                    created_id = cursor.fetchone()[0]
                    created_task_ids.append(created_id)

                    if template_assignee_ids:
                        self._sync_task_assignees_tx(
                            cursor, created_id, template_assignee_ids, added_by=created_by
                        )
                    if template_watcher_ids:
                        self._sync_task_watchers_tx(
                            cursor, created_id, template_watcher_ids, added_by=created_by
                        )

                    cursor.execute("""
                        INSERT INTO task_status_history (task_id, status_code, changed_by)
                        VALUES (%s, 'assigned', %s)
                    """, (created_id, created_by))

                    for item in checklist_rows:
                        cursor.execute("""
                            INSERT INTO task_checklist_items (task_id, title, position, is_required)
                            VALUES (%s, %s, %s, %s)
                        """, (created_id, item[0], item[1], item[2]))

                    latest_next_at = self._add_task_recurrence_interval(
                        latest_next_at,
                        recurrence_type,
                        recurrence_interval
                    )

                cursor.execute("""
                    UPDATE tasks
                    SET recurrence_next_at = %s,
                        updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                """, (latest_next_at, root_id))

        return created_task_ids

    def get_task_action_needs_summary(self, requester_id):
        """
        Сколько задач ждут действия лично от пользователя — источник бейджа в сайдбаре.

        Правила ровно те же, что считает раздел на клиенте
        (src/components/tasks/taskActionNeeds.js) и источник колокола
        (notifications/sources.py::tasks) — если меняете здесь, меняйте и там:
          overdue  — я исполнитель, дедлайн прошёл, задача не закрыта;
          returned — мне вернули на доработку;
          info     — я отвечаю за постановку, исполнителю не хватает информации;
          review   — я поручитель, исполнитель сдал работу и ждёт приёмки;
          fresh    — мне поручили, к работе ещё не приступил;
          accepted — мою работу приняли (единственное «к сведению», а не «сделай»).
        Категории взаимоисключающие: одна задача считается один раз.
        Бэклог не трогаем — это очередь планирования, а не работа.
        Просмотренные уведомления (task_action_reads) в счётчик не идут — иначе
        число растёт и перестаёт что-либо значить.
        """
        requester_id = int(requester_id)
        now = self._task_now()
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE {assignee_exists}
                          AND t.is_backlog = FALSE
                          AND t.status IN ('assigned', 'in_progress', 'returned')
                          AND t.due_at IS NOT NULL
                          AND t.due_at < %s
                          AND (r.task_id IS NULL OR r.kind <> 'overdue' OR r.seen_at < t.updated_at)
                    )::INT,
                    COUNT(*) FILTER (
                        WHERE {assignee_exists}
                          AND t.is_backlog = FALSE
                          AND t.status = 'returned'
                          AND (t.due_at IS NULL OR t.due_at >= %s)
                          AND (r.task_id IS NULL OR r.kind <> 'returned' OR r.seen_at < t.updated_at)
                    )::INT,
                    COUNT(*) FILTER (
                        -- Спрашивает исполнитель, отвечает тот же человек, что
                        -- принимает итог. Бэклог здесь считается, в отличие от
                        -- остальных причин: вопрос задали живому человеку, и
                        -- «задача ещё в очереди» ответа не отменяет.
                        WHERE t.info_request_id IS NOT NULL
                          AND t.status IN ('assigned', 'in_progress', 'returned')
                          AND COALESCE(t.requested_by_id, t.created_by) = %s
                          -- Не «я не исполнитель», а «спрашивал не я». При
                          -- нескольких исполнителях постановщик может сам быть
                          -- одним из них, и прежнее условие прятало от него
                          -- вопрос коллеги — отвечать было некому.
                          AND COALESCE(
                                  (SELECT m.author_id FROM task_messages m
                                    WHERE m.id = t.info_request_id), 0
                              ) <> %s
                          AND (r.task_id IS NULL OR r.kind <> 'info' OR r.seen_at < t.updated_at)
                    )::INT,
                    COUNT(*) FILTER (
                        WHERE t.status = 'completed'
                          AND COALESCE(t.requested_by_id, t.created_by) = %s
                          AND (r.task_id IS NULL OR r.kind <> 'review' OR r.seen_at < t.updated_at)
                    )::INT,
                    COUNT(*) FILTER (
                        WHERE {assignee_exists}
                          AND t.is_backlog = FALSE
                          AND t.status = 'assigned'
                          AND (t.due_at IS NULL OR t.due_at >= %s)
                          AND (r.task_id IS NULL OR r.kind <> 'fresh' OR r.seen_at < t.updated_at)
                    )::INT,
                    COUNT(*) FILTER (
                        WHERE {assignee_exists}
                          AND t.status = 'accepted'
                          -- Сам себе принял — уведомлять не о чем.
                          AND COALESCE(t.requested_by_id, t.created_by) IS DISTINCT FROM %s
                          -- Отметка вечная: принятую задачу правка отчёта не
                          -- должна воскрешать (в отличие от живых причин).
                          AND (r.task_id IS NULL OR r.kind <> 'accepted')
                    )::INT
                FROM tasks t
                LEFT JOIN task_action_reads r ON r.task_id = t.id AND r.user_id = %s
            """.format(assignee_exists=self._TASK_ASSIGNEE_EXISTS_SQL), (
                # У предиката участия ДВА плейсхолдера — id повторяется.
                requester_id, requester_id, now,
                requester_id, requester_id, now,
                requester_id, requester_id,
                requester_id,
                requester_id, requester_id, now,
                requester_id, requester_id, requester_id,
                requester_id
            ))
            row = cursor.fetchone() or (0, 0, 0, 0, 0, 0)

        breakdown = {
            "overdue": int(row[0] or 0),
            "returned": int(row[1] or 0),
            "info": int(row[2] or 0),
            "review": int(row[3] or 0),
            "fresh": int(row[4] or 0),
            "accepted": int(row[5] or 0)
        }
        return {"count": sum(breakdown.values()), "breakdown": breakdown}

    def mark_task_action_seen(self, requester_id, task_id, kind):
        """
        Отметить уведомление просмотренным. Гасится только счётчик: сама задача
        остаётся в списке и вернётся в счётчик, если причина сменится или задачу
        снова тронут (сравнение seen_at с tasks.updated_at).
        """
        kind_norm = (kind or '').strip().lower()
        if kind_norm not in self.TASK_ACTION_NEED_KINDS:
            raise ValueError("INVALID_TASK_ACTION_KIND")

        requester_id = int(requester_id)
        task_id = int(task_id)
        with self._get_cursor() as cursor:
            cursor.execute("SELECT 1 FROM tasks WHERE id = %s", (task_id,))
            if not cursor.fetchone():
                raise ValueError("TASK_NOT_FOUND")
            cursor.execute("""
                INSERT INTO task_action_reads (user_id, task_id, kind, seen_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id, task_id) DO UPDATE
                SET kind = EXCLUDED.kind,
                    seen_at = EXCLUDED.seen_at
            """, (requester_id, task_id, kind_norm, self._task_now()))
        return self.get_task_action_needs_summary(requester_id)

    def _task_department_condition(self, department_filter):
        """Условие «задача принадлежит отделу» для WHERE по таблице tasks.

        Своей колонки у задачи нет: принадлежность выводим из членства того, кто
        задачу поставил (created_by). Подзапросом, а не JOIN'ом — эти же условия
        уходят в сводку, в счётчик и в выгрузку, где таблицы users в запросе нет.
        """
        if department_filter is None:
            return None, []
        if department_filter == 'none':
            # Сюда же попадают задачи, чей постановщик удалён: отдела у них нет.
            return (
                "NOT EXISTS (SELECT 1 FROM users task_dept_creator "
                "WHERE task_dept_creator.id = t.created_by "
                "AND task_dept_creator.department_id IS NOT NULL)"
            ), []
        return (
            "EXISTS (SELECT 1 FROM users task_dept_creator "
            "WHERE task_dept_creator.id = t.created_by "
            "AND task_dept_creator.department_id = %s)"
        ), [department_filter]

    def _task_scope_filters(self, requester_id, role, only_my=False, mine=None,
                            person_id=None, person_scope=None, department_id=None,
                            project_id=None, section_id=None):
        """Кого пускаем к задачам и чья это доска — общая часть списка и выгрузки.

        Вынесено из get_tasks_for_requester, чтобы Excel-выгрузка отбирала ровно
        те же задачи, что видны в разделе: разъедься эти два места — в файл
        уехало бы больше, чем человеку показывает доска.

        Возвращает (conditions, params, person_board_id, person_id_rest), где
        person_id_rest — сотрудник, которого ещё предстоит применить фильтром
        (срезы «входящие»/«исходящие»), либо None, если роль задач не видит.
        """
        requester_id = int(requester_id)
        only_my_flag = bool(only_my)
        mine_norm = (mine or '').strip().lower() or None
        person_scope_norm = (person_scope or '').strip().lower() or None

        # mine='any' — это те же «мои», что и only_my: разными путями к одному условию.
        if mine_norm == 'any':
            only_my_flag = True
            mine_norm = None
        if mine_norm and mine_norm not in {'assignee', 'creator'}:
            raise ValueError("INVALID_TASK_MINE_FILTER")
        if person_scope_norm and person_scope_norm not in {'incoming', 'outgoing', 'any'}:
            raise ValueError("INVALID_TASK_PERSON_SCOPE_FILTER")

        department_filter = self._normalize_task_department_filter(department_id)
        project_filter = self._normalize_task_project_filter(project_id)
        section_filter = self._normalize_task_section_filter(section_id)

        if person_id is None or str(person_id).strip() == '':
            person_id_norm = None
        else:
            person_id_norm = int(person_id)
            if person_id_norm <= 0:
                raise ValueError("INVALID_TASK_PERSON_ID_FILTER")

        conditions = []
        params = []
        if role_has_min(role, 'admin'):
            pass
        elif role in TASK_PERSONAL_SCOPE_ROLES:
            # Поручитель видит задачи, которые поручил, — иначе он не сможет принять итог.
            participant_sql = f"(t.created_by = %s OR t.requested_by_id = %s OR {self._TASK_ASSIGNEE_EXISTS_SQL})"
            participant_params = [requester_id, requester_id, requester_id, requester_id]
            # Доска проекта (задача #401): участник видит все её задачи,
            # наблюдатель с доступом к доске — свою. Тоже только на просмотр.
            project_sql = f"{self._TASK_PROJECT_MEMBER_SQL} OR {self._TASK_WATCHER_SQL}"
            project_params = [requester_id, requester_id]
            if role == 'sv':
                # СВ видит и задачи, поставленные коллегами-СВ своего отдела, — на
                # просмотр (задача #298). Действия над чужой задачей по-прежнему
                # решает _task_visible_for_requester, а не этот охват.
                # Задачи проекта путь коллеги не открывает: там видимость решает
                # состав доски (задача #401) — иначе конфиденциальная задача HR,
                # поставленная СВ, была бы видна всем СВ его отдела.
                conditions.append(
                    f"({participant_sql} OR (t.project_id IS NULL AND {self._TASK_COLLEAGUE_SV_SQL}) OR {project_sql})"
                )
                params.extend(participant_params + [requester_id] + project_params)
            else:
                conditions.append(f"({participant_sql} OR {project_sql})")
                params.extend(participant_params + project_params)
        else:
            return None

        # Отдел — часть базового охвата, а не фильтр поверх него: иначе сводка
        # раздела и счётчик бэклога считались бы по всем отделам сразу, и цифры
        # в шапке не сходились бы с тем, что видно в списке.
        department_sql, department_params = self._task_department_condition(department_filter)
        if department_sql:
            conditions.append(department_sql)
            params.extend(department_params)

        # Проект — туда же, в базовый охват, по той же причине: сводка и бэклог
        # шапки доски обязаны считаться по выбранной доске.
        if project_filter == 'none':
            conditions.append("t.project_id IS NULL")
        elif project_filter is not None:
            conditions.append("t.project_id = %s")
            params.append(project_filter)
        # Раздел доски — тем же базовым охватом: счётчики шапки — по разделу.
        if section_filter == 'none':
            conditions.append("t.section_id IS NULL")
        elif section_filter is not None:
            conditions.append("t.section_id = %s")
            params.append(section_filter)

        if only_my_flag:
            conditions.append(
                f"(t.created_by = %s OR t.requested_by_id = %s OR {self._TASK_ASSIGNEE_EXISTS_SQL})"
            )
            params.extend([requester_id, requester_id, requester_id, requester_id])

        if mine_norm == 'assignee':
            # Ключевой фильтр вкладки «Мои задачи»: я среди исполнителей, а не
            # «я тот единственный исполнитель».
            conditions.append(self._TASK_ASSIGNEE_EXISTS_SQL)
            params.extend([requester_id, requester_id])
        elif mine_norm == 'creator':
            conditions.append("(t.created_by = %s OR t.requested_by_id = %s)")
            params.extend([requester_id, requester_id])

        person_board_id = None
        if person_id_norm is not None and person_scope_norm == 'any':
            # Доска сотрудника: фильтр уходит в базовые условия, чтобы summary считал
            # именно его задачи, а не весь отдел. Соисполнителю нужна своя доска
            # ровно так же, как «основному».
            conditions.append(
                f"(t.created_by = %s OR {self._TASK_ASSIGNEE_EXISTS_SQL})"
            )
            params.extend([person_id_norm, person_id_norm, person_id_norm])
            person_board_id = person_id_norm
            person_id_norm = None

        return conditions, params, person_board_id, person_id_norm

    def get_tasks_for_requester(
            self,
            requester_id,
            requester_role,
            search=None,
            status=None,
            tag=None,
            priority=None,
            limit=None,
            offset=0,
            only_my=False,
            person_id=None,
            person_scope=None,
            backlog=None,
            mine=None,
            task_id=None,
            sort=None,
            department_id=None,
            include_summary=True,
            project_id=None,
            task_key=None,
            section_id=None
    ):
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        search_text = (search or '').strip()
        # Статус приходит списком: колонка «В работе» — это in_progress + returned.
        status_values = [part.strip().lower() for part in str(status or '').split(',') if part.strip()]
        status_norm = status_values[0] if len(status_values) == 1 else None
        tag_norm = (tag or '').strip().lower() or None
        priority_norm = (priority or '').strip().lower() or None
        only_my_flag = bool(only_my)
        person_scope_norm = (person_scope or '').strip().lower() or None
        backlog_norm = (backlog or '').strip().lower() or None
        # sort — порядок выдачи: свежесть (по умолчанию) или важность.
        # Важность считает сервер, иначе на постраничной доске она сортировала бы
        # только видимую страницу.
        sort_norm = (sort or '').strip().lower() or 'freshness'
        if sort_norm not in {'freshness', 'importance'}:
            raise ValueError("INVALID_TASK_SORT")
        for status_value in status_values:
            if status_value not in {'assigned', 'in_progress', 'completed', 'accepted', 'returned'}:
                raise ValueError("INVALID_TASK_STATUS_FILTER")
        if tag_norm and tag_norm not in TASK_TAG_CODES:
            raise ValueError("INVALID_TASK_TAG_FILTER")
        if priority_norm and priority_norm not in {'normal', 'urgent', 'critical'}:
            raise ValueError("INVALID_TASK_PRIORITY_FILTER")
        if person_scope_norm and person_scope_norm not in {'incoming', 'outgoing', 'any'}:
            raise ValueError("INVALID_TASK_PERSON_SCOPE_FILTER")
        if backlog_norm and backlog_norm not in {'only', 'exclude'}:
            raise ValueError("INVALID_TASK_BACKLOG_FILTER")

        # Запрос без limit не должен уметь вытянуть всю базу — своя страховка на слое БД,
        # даже если кто-то дёрнет API напрямую.
        if limit is None or str(limit).strip() == '':
            limit_norm = self.TASKS_MAX_ROWS
        else:
            limit_norm = int(limit)
            if limit_norm <= 0:
                raise ValueError("INVALID_TASK_LIMIT")
            limit_norm = min(limit_norm, self.TASKS_MAX_ROWS)

        offset_norm = int(offset or 0)
        if offset_norm < 0:
            raise ValueError("INVALID_TASK_OFFSET")

        summary_empty = {
            "total": 0,
            "in_progress": 0,
            "completed": 0,
            "returned": 0,
            "backlog": 0,
            "accepted": 0,
            "overdue": 0,
            "delegated": 0
        }

        scope = self._task_scope_filters(
            requester_id, role,
            only_my=only_my_flag, mine=mine, person_id=person_id, person_scope=person_scope,
            department_id=department_id, project_id=project_id, section_id=section_id
        )
        if scope is None:
            return {
                "tasks": [],
                "total_all": 0,
                "total_filtered": 0,
                "summary": summary_empty
            }
        base_conditions, base_params, person_board_id, person_id_norm = scope

        # Базовый набор собран — дальше идут фильтры выборки поверх него.
        filtered_conditions = list(base_conditions)
        filtered_params = list(base_params)

        # Точечный запрос по id: диплинк на задачу, которой нет в загруженной выборке.
        if task_id is not None and str(task_id).strip() != '':
            try:
                task_id_norm = int(task_id)
            except Exception:
                raise ValueError("INVALID_TASK_ID_FILTER")
            if task_id_norm <= 0:
                raise ValueError("INVALID_TASK_ID_FILTER")
            filtered_conditions.append("t.id = %s")
            filtered_params.append(task_id_norm)

        # Ссылка по ключу (?task=HR-125): текущему или прежнему, если задачу
        # переносили в другой проект (задача #401).
        if task_key is not None and str(task_key).strip() != '':
            parsed_key = task_projects.parse_task_key(task_key)
            if not parsed_key:
                raise ValueError("INVALID_TASK_KEY_FILTER")
            filtered_conditions.append(self._TASK_KEY_MATCH_SQL)
            filtered_params.extend([parsed_key[0], parsed_key[1], parsed_key[0], parsed_key[1]])

        if search_text:
            like_pattern = f"%{search_text}%"
            # Строка поиска, похожая на ключ задачи (HR-125), ищет и по ключу.
            search_key = task_projects.parse_task_key(search_text)
            key_sql = f"OR {self._TASK_KEY_MATCH_SQL}" if search_key else ""
            # Ищем по ЛЮБОМУ исполнителю, но через EXISTS: джойн на состав
            # вернул бы задачу с тремя исполнителями трижды.
            filtered_conditions.append(f"""
                (
                    t.subject ILIKE %s
                    OR COALESCE(t.description, '') ILIKE %s
                    OR EXISTS (
                        SELECT 1 FROM task_assignees ta_s
                        JOIN users au ON au.id = ta_s.user_id
                        WHERE ta_s.task_id = t.id AND COALESCE(au.name, '') ILIKE %s
                    )
                    OR COALESCE(creator.name, '') ILIKE %s
                    {key_sql}
                )
            """)
            filtered_params.extend([like_pattern, like_pattern, like_pattern, like_pattern])
            if search_key:
                filtered_params.extend([search_key[0], search_key[1], search_key[0], search_key[1]])

        if len(status_values) == 1:
            filtered_conditions.append("t.status = %s")
            filtered_params.append(status_values[0])
        elif status_values:
            filtered_conditions.append("t.status = ANY(%s)")
            filtered_params.append(status_values)

        if tag_norm:
            filtered_conditions.append("t.tag = %s")
            filtered_params.append(tag_norm)

        if priority_norm:
            filtered_conditions.append("t.priority = %s")
            filtered_params.append(priority_norm)

        if backlog_norm == 'only':
            filtered_conditions.append("t.is_backlog = TRUE")
        elif backlog_norm == 'exclude':
            filtered_conditions.append("t.is_backlog = FALSE")

        if person_id_norm is not None:
            # «Входящие/исходящие» по человеку: исполнителем считается любой из
            # состава, поэтому «он исполняет» — это участие, а не равенство.
            if person_scope_norm == 'incoming':
                filtered_conditions.append(self._TASK_ASSIGNEE_EXISTS_SQL)
                filtered_params.extend([requester_id, requester_id])
                filtered_conditions.append("t.created_by = %s")
                filtered_params.append(person_id_norm)
            elif person_scope_norm == 'outgoing':
                filtered_conditions.append("t.created_by = %s")
                filtered_params.append(requester_id)
                filtered_conditions.append(self._TASK_ASSIGNEE_EXISTS_SQL)
                filtered_params.extend([person_id_norm, person_id_norm])
            else:
                filtered_conditions.append(
                    f"(t.created_by = %s OR {self._TASK_ASSIGNEE_EXISTS_SQL})"
                )
                filtered_params.extend([person_id_norm, person_id_norm, person_id_norm])

        base_where_sql = f"WHERE {' AND '.join(base_conditions)}" if base_conditions else ""
        filtered_where_sql = f"WHERE {' AND '.join(filtered_conditions)}" if filtered_conditions else ""

        with self._get_cursor() as cursor:
            # Сводка считается по базовому набору (доска целиком), а не по странице —
            # иначе шапка доски сотрудника показывала бы «итоги текущей страницы».
            # Это семь агрегатов по всей видимой базе, поэтому колонки доски просят
            # её ровно один раз на загрузку, а не каждым запросом.
            # Сводка — семь агрегатов по всей видимой базе. Колонки доски просят её
            # ровно один раз на загрузку, остальные запросы её пропускают.
            summary = dict(summary_empty)
            if include_summary:
                summary_params = [self._task_now()]
                summary_params.extend(
                    [person_board_id, person_board_id] if person_board_id is not None else [0, 0]
                )
                summary_params.extend(base_params)
                cursor.execute(f"""
                    SELECT
                        COUNT(*)::INT AS total_count,
                        COUNT(*) FILTER (WHERE t.status = 'in_progress')::INT AS in_progress_count,
                        COUNT(*) FILTER (WHERE t.status IN ('completed', 'accepted'))::INT AS completed_count,
                        COUNT(*) FILTER (WHERE t.status = 'returned')::INT AS returned_count,
                        COUNT(*) FILTER (WHERE t.is_backlog)::INT AS backlog_count,
                        COUNT(*) FILTER (WHERE t.status = 'accepted')::INT AS accepted_count,
                        COUNT(*) FILTER (
                            WHERE t.due_at IS NOT NULL
                              AND t.due_at < %s
                              AND t.status NOT IN ('completed', 'accepted')
                        )::INT AS overdue_count,
                        COUNT(*) FILTER (
                            -- «Поручено другим»: я поставил, и среди исполнителей
                            -- есть кто-то кроме меня. Задача «я + коллега» сюда
                            -- попадает — коллеге я её всё-таки поручил.
                            WHERE t.created_by = %s
                              AND EXISTS (
                                  SELECT 1 FROM task_assignees ta_d
                                  WHERE ta_d.task_id = t.id AND ta_d.user_id <> %s
                              )
                        )::INT AS delegated_count
                    FROM tasks t
                    {base_where_sql}
                """, tuple(summary_params))
                summary_row = cursor.fetchone() or (0, 0, 0, 0, 0, 0, 0, 0)
                summary = {
                    "total": int(summary_row[0] or 0),
                    "in_progress": int(summary_row[1] or 0),
                    "completed": int(summary_row[2] or 0),
                    "returned": int(summary_row[3] or 0),
                    "backlog": int(summary_row[4] or 0),
                    "accepted": int(summary_row[5] or 0),
                    "overdue": int(summary_row[6] or 0),
                    "delegated": int(summary_row[7] or 0)
                }

            # JOIN к users нужен только поиску по имени постановщика — имена
            # исполнителей ищет EXISTS внутри самого условия, поэтому строк
            # по-прежнему столько же, сколько задач, и COUNT(*) честный.
            count_joins_sql = """
                LEFT JOIN users creator ON creator.id = t.created_by
            """ if search_text else ""
            cursor.execute(f"""
                SELECT COUNT(*)::INT
                FROM tasks t
                {count_joins_sql}
                {filtered_where_sql}
            """, tuple(filtered_params))
            total_filtered_row = cursor.fetchone()
            total_filtered = int(total_filtered_row[0] or 0) if total_filtered_row else 0

            # Бэклог — упорядоченная очередь приоритезации, остальные списки — свежие сверху.
            # Порядок должен совпадать с compareBoardTasks во фронте: та же важность,
            # тот же tie-break по created_at и id.
            importance_sql = (
                "CASE t.priority WHEN 'critical' THEN 0 WHEN 'urgent' THEN 1 ELSE 2 END ASC, "
            )
            if backlog_norm == 'only':
                # Бэклог — очередь приоритезации, её порядок важнее сортировки: сперва
                # балл суперадминов (1 — важнее, без балла — в конце), внутри балла —
                # ручная очередь. Тот же порядок — compareBacklogTasks во фронте.
                order_by_sql = (
                    "t.priority_score ASC NULLS LAST, t.backlog_rank ASC NULLS LAST, "
                    "t.created_at DESC, t.id DESC"
                )
            elif sort_norm == 'importance':
                order_by_sql = f"{importance_sql}t.created_at DESC, t.id DESC"
            else:
                order_by_sql = "t.created_at DESC, t.id DESC"
            task_query = f"""
                SELECT
                    t.id, t.subject, t.description, t.tag, t.status, t.created_at, t.updated_at,
                    t.completion_summary, t.completed_at, t.completed_by, completed_user.name,
                    t.priority, t.deadline_duration_minutes, t.due_at, t.is_regulation,
                    t.recurrence_type, t.recurrence_interval, t.recurrence_next_at,
                    t.regulation_parent_id, t.regulation_iteration,
                    assignee.id, assignee.name, assignee.role, assignee.supervisor_id,
                    assignee.avatar_bucket, assignee.avatar_blob_path,
                    creator.id, creator.name, creator.role,
                    creator.avatar_bucket, creator.avatar_blob_path,
                    t.is_backlog, t.backlog_rank, t.estimate_minutes,
                    t.planned_start_at, t.started_at,
                    t.requested_by_id, COALESCE(origin_user.name, t.requested_by_name),
                    t.reminder_minutes_before, t.reminder_sent_at,
                    action_read.kind, action_read.seen_at,
                    t.info_request_id, t.production_brief,
                    t.project_id, t.project_number, project.code, project.name,
                    section.id, section.name,
                    t.priority_score
                FROM tasks t
                LEFT JOIN users assignee ON assignee.id = t.assigned_to
                LEFT JOIN users creator ON creator.id = t.created_by
                LEFT JOIN users completed_user ON completed_user.id = t.completed_by
                LEFT JOIN users origin_user ON origin_user.id = t.requested_by_id
                LEFT JOIN task_projects project ON project.id = t.project_id
                LEFT JOIN task_project_sections section ON section.id = t.section_id
                LEFT JOIN task_action_reads action_read
                       ON action_read.task_id = t.id AND action_read.user_id = %s
                {filtered_where_sql}
                ORDER BY {order_by_sql}
            """
            # Параметр JOIN'а идёт раньше условий WHERE — порядок плейсхолдеров важен.
            task_query_params = [requester_id] + list(filtered_params)
            if limit_norm is not None:
                task_query += "\n                LIMIT %s"
                task_query_params.append(limit_norm)
            if offset_norm > 0:
                task_query += "\n                OFFSET %s"
                task_query_params.append(offset_norm)

            cursor.execute(task_query, tuple(task_query_params))
            task_rows = cursor.fetchall()

            history_rows = []
            attachment_rows = []
            checklist_rows = []
            report_rows = []
            message_rows = []
            assignee_rows = []
            watcher_rows = []
            if task_rows:
                task_ids = [row[0] for row in task_rows]

                # Наблюдатели (задача #401) — тем же пакетным запросом на страницу.
                # Только те, у кого ещё есть доступ к доске: остальные задачу уже не
                # видят, и значиться в карточке наблюдателем им незачем.
                cursor.execute("""
                    SELECT tw.task_id, u.id, u.name, u.role, u.avatar_bucket, u.avatar_blob_path
                    FROM task_watchers tw
                    JOIN tasks tw_task ON tw_task.id = tw.task_id
                    JOIN task_project_members tw_board
                      ON tw_board.project_id = tw_task.project_id AND tw_board.user_id = tw.user_id
                    JOIN users u ON u.id = tw.user_id
                    WHERE tw.task_id = ANY(%s)
                    ORDER BY tw.task_id ASC, tw.added_at ASC, tw.user_id ASC
                """, (task_ids,))
                watcher_rows = cursor.fetchall()

                # Состав исполнителей — отдельным пакетным запросом, как история
                # и чеклист. Джойном его тянуть нельзя: главный запрос обязан
                # оставаться «одна строка = одна задача», иначе LIMIT перестанет
                # означать «столько-то карточек».
                cursor.execute("""
                    SELECT
                        ta.task_id, u.id, u.name, u.role, u.supervisor_id,
                        u.avatar_bucket, u.avatar_blob_path
                    FROM task_assignees ta
                    JOIN users u ON u.id = ta.user_id
                    WHERE ta.task_id = ANY(%s)
                    ORDER BY ta.task_id ASC, ta.position ASC, ta.user_id ASC
                """, (task_ids,))
                assignee_rows = cursor.fetchall()

                cursor.execute("""
                    SELECT
                        h.id, h.task_id, h.status_code, h.comment, h.changed_at,
                        h.changed_by, u.name
                    FROM task_status_history h
                    LEFT JOIN users u ON u.id = h.changed_by
                    WHERE h.task_id = ANY(%s)
                    ORDER BY h.changed_at ASC, h.id ASC
                """, (task_ids,))
                history_rows = cursor.fetchall()

                cursor.execute("""
                    SELECT
                        a.id, a.task_id, a.file_name, a.content_type, a.file_size, a.created_at,
                        COALESCE(a.storage_type, 'db'), a.gcs_bucket, a.gcs_blob_path,
                        COALESCE(a.attachment_kind, 'initial'), a.message_id
                    FROM task_attachments a
                    WHERE a.task_id = ANY(%s)
                    ORDER BY a.id ASC
                """, (task_ids,))
                attachment_rows = cursor.fetchall()

                cursor.execute("""
                    SELECT
                        ci.id, ci.task_id, ci.title, ci.position, ci.is_required, ci.is_done,
                        ci.completed_by, completed_user.name, ci.completed_at, ci.updated_at,
                        ci.result_note
                    FROM task_checklist_items ci
                    LEFT JOIN users completed_user ON completed_user.id = ci.completed_by
                    WHERE ci.task_id = ANY(%s)
                    ORDER BY ci.task_id ASC, ci.position ASC, ci.id ASC
                """, (task_ids,))
                checklist_rows = cursor.fetchall()

                cursor.execute(
                    self._TASK_REPORT_SELECT + " WHERE r.task_id = ANY(%s) ORDER BY r.created_at ASC, r.id ASC",
                    (task_ids,)
                )
                report_rows = cursor.fetchall()

                cursor.execute(
                    self._TASK_MESSAGE_SELECT + " WHERE m.task_id = ANY(%s) ORDER BY m.created_at ASC, m.id ASC",
                    (task_ids,)
                )
                message_rows = cursor.fetchall()

        history_map = defaultdict(list)
        for row in history_rows:
            history_map[row[1]].append({
                "id": row[0],
                "status_code": row[2],
                "comment": row[3],
                "changed_at": row[4].isoformat() if hasattr(row[4], 'isoformat') else row[4],
                "changed_by": row[5],
                "changed_by_name": row[6]
            })

        attachment_map = defaultdict(list)
        for row in attachment_rows:
            attachment_map[row[1]].append({
                "id": row[0],
                "file_name": row[2],
                "content_type": row[3],
                "file_size": row[4],
                "created_at": row[5].isoformat() if hasattr(row[5], 'isoformat') else row[5],
                "storage_type": row[6],
                "gcs_bucket": row[7],
                "gcs_blob_path": row[8],
                "attachment_kind": row[9],
                # Не NULL — файл приехал с уточнением: карточка покажет его
                # внутри уточнения и не станет дублировать в «Файлах задачи».
                "message_id": row[10]
            })

        checklist_map = defaultdict(list)
        for row in checklist_rows:
            checklist_map[row[1]].append({
                "id": row[0],
                "task_id": row[1],
                "title": row[2],
                "position": row[3],
                "is_required": bool(row[4]),
                "is_done": bool(row[5]),
                "completed_by": row[6],
                "completed_by_name": row[7],
                "completed_at": self._task_dt_to_iso(row[8]),
                "updated_at": self._task_dt_to_iso(row[9]),
                "result_note": row[10]
            })

        report_map = defaultdict(list)
        for row in report_rows:
            report_map[row[1]].append(self._serialize_task_report(row))

        assignee_map = defaultdict(list)
        for row in assignee_rows:
            assignee_map[row[0]].append({
                "id": row[1],
                "name": row[2],
                "role": row[3],
                "supervisor_id": row[4],
                "avatar_bucket": row[5],
                "avatar_blob_path": row[6]
            })

        watcher_map = defaultdict(list)
        for row in watcher_rows:
            watcher_map[row[0]].append({
                "id": row[1],
                "name": row[2],
                "role": row[3],
                "avatar_bucket": row[4],
                "avatar_blob_path": row[5]
            })

        # Уточнения: файлы к ним раскладываются из уже выбранных вложений задачи,
        # пятый запрос ради того же самого был бы лишним.
        message_map = defaultdict(list)
        message_by_id = {}
        for row in message_rows:
            message = self._serialize_task_message(row)
            message_map[row[1]].append(message)
            message_by_id[message['id']] = message
        for attachments in attachment_map.values():
            for attachment in attachments:
                owner = message_by_id.get(attachment.get('message_id'))
                if owner is not None:
                    owner['attachments'].append(attachment)

        result = []
        for row in task_rows:
            task_id = row[0]
            task_reports = report_map.get(task_id, [])
            spent_total = sum(int(item.get('spent_minutes') or 0) for item in task_reports)
            all_attachments = attachment_map.get(task_id, [])
            initial_attachments = [a for a in all_attachments if a.get("attachment_kind") != "result"]
            result_attachments = [a for a in all_attachments if a.get("attachment_kind") == "result"]
            result.append({
                "id": task_id,
                "subject": row[1],
                "description": row[2],
                "tag": row[3],
                "status": row[4],
                "created_at": self._task_dt_to_iso(row[5]),
                "updated_at": self._task_dt_to_iso(row[6]),
                "completion_summary": row[7],
                "completed_at": self._task_dt_to_iso(row[8]),
                "completed_by": row[9],
                "completed_by_name": row[10],
                "priority": row[11] or 'normal',
                "deadline_duration_minutes": row[12],
                "deadline": self._split_task_deadline(row[12]),
                "due_at": self._task_dt_to_iso(row[13]),
                "is_regulation": bool(row[14]),
                "recurrence_type": row[15],
                "recurrence_interval": row[16],
                "recurrence_next_at": self._task_dt_to_iso(row[17]),
                "regulation_parent_id": row[18],
                "regulation_iteration": row[19],
                # Первый исполнитель отдельным ключом — на него смотрят места,
                # где человек ровно один (аватар в списке, группировка, бот).
                "assignee": {
                    "id": row[20],
                    "name": row[21],
                    "role": row[22],
                    "supervisor_id": row[23],
                    "avatar_bucket": row[24],
                    "avatar_blob_path": row[25]
                } if row[20] else None,
                # Полный состав по порядку показа. Права у исполнителей равные,
                # первый в списке — просто первый.
                "assignees": assignee_map.get(task_id, []),
                # Задача коллеги-СВ: видна, но только на просмотр (задача #298).
                "viewer_is_observer": self._task_viewer_is_observer(
                    role, requester_id, row[26], row[36], assignee_map.get(task_id, [])
                ),
                "creator": {
                    "id": row[26],
                    "name": row[27],
                    "role": row[28],
                    "avatar_bucket": row[29],
                    "avatar_blob_path": row[30]
                } if row[26] else None,
                "is_backlog": bool(row[31]),
                "backlog_rank": float(row[32]) if row[32] is not None else None,
                # Балл приоритета 1–5 от суперадминов; None — ещё не оценена.
                "priority_score": row[50],
                "estimate_minutes": row[33],
                "planned_start_at": self._task_dt_to_iso(row[34]),
                "started_at": self._task_dt_to_iso(row[35]),
                # Источник задачи: сотрудник, внешний текст либо своя инициатива.
                "requested_by": ({
                    "id": row[36],
                    "name": row[37],
                    "source": 'user' if row[36] else 'external'
                } if (row[36] or row[37]) else None),
                "reminder_minutes_before": row[38],
                "reminder_sent_at": self._task_dt_to_iso(row[39]),
                # Отметка «уведомление просмотрено» — своя у каждого пользователя.
                "action_seen": ({
                    "kind": row[40],
                    "seen_at": self._task_dt_to_iso(row[41])
                } if row[40] else None),
                "history": history_map.get(task_id, []),
                "attachments": initial_attachments,
                "completion_attachments": result_attachments,
                "checklist": checklist_map.get(task_id, []),
                "reports": task_reports,
                "spent_minutes": spent_total or None,
                # Уточнения по постановке + открытый запрос информации отдельно:
                # по нему рисуется чип на карточке и считается «ждут вас».
                "messages": message_map.get(task_id, []),
                "info_request": message_by_id.get(row[42]) if row[42] else None,
                # Бриф продакшна (задача #354): коды вариантов, подписи — во фронте.
                "production_brief": task_production.stored_brief(row[43]),
                # Проект-доска и ключ задачи (задача #401): HR-125 = код + номер в проекте.
                "project": ({
                    "id": row[44],
                    "code": row[46],
                    "name": row[47]
                } if row[44] else None),
                "project_number": row[45] if row[44] else None,
                "key": task_projects.format_task_key(row[46], row[45]) if row[44] else None,
                "section": {"id": row[48], "name": row[49]} if row[48] else None,
                "watchers": watcher_map.get(task_id, [])
            })

        return {
            "tasks": result,
            "total_all": int(summary.get("total", 0)),
            "total_filtered": int(total_filtered),
            "summary": summary
        }

    def get_tasks_for_export(self, requester_id, requester_role, mine=None,
                             person_id=None, person_scope=None, department_id=None,
                             project_id=None, section_id=None):
        """Плоский срез задач для выгрузки в Excel — тот же охват, что у доски.

        Отдельный запрос, а не get_tasks_for_requester: выгрузке не нужны
        история, вложения, чек-листы и журнал отчётов, и тянуть их на всю базу
        значило бы четыре лишних запроса и мегабайты ради пяти листов.
        Трудозатраты поэтому считаем агрегатом прямо здесь.

        Порядок строк повторяет доску: бэклог — ручной очередью приоритезации,
        остальные — свежие сверху.
        """
        role = normalize_role_value(requester_role)
        scope = self._task_scope_filters(
            requester_id, role, mine=mine, person_id=person_id, person_scope=person_scope,
            department_id=department_id, project_id=project_id, section_id=section_id
        )
        if scope is None:
            return []
        conditions, params, _person_board_id, person_id_rest = scope

        person_scope_norm = (person_scope or '').strip().lower() or None
        if person_id_rest is not None:
            # Тот же предикат участия, что в списке: охват выгрузки обязан
            # совпадать с тем, что человек видит на экране.
            if person_scope_norm == 'incoming':
                conditions.append(f"{self._TASK_ASSIGNEE_EXISTS_SQL} AND t.created_by = %s")
                params.extend([int(requester_id), int(requester_id), person_id_rest])
            elif person_scope_norm == 'outgoing':
                conditions.append(f"t.created_by = %s AND {self._TASK_ASSIGNEE_EXISTS_SQL}")
                params.extend([int(requester_id), person_id_rest, person_id_rest])
            else:
                conditions.append(f"(t.created_by = %s OR {self._TASK_ASSIGNEE_EXISTS_SQL})")
                params.extend([person_id_rest, person_id_rest, person_id_rest])

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT
                    t.id, t.subject, t.description, t.tag, t.status, t.priority,
                    t.is_backlog, t.created_at, t.planned_start_at, t.started_at,
                    t.due_at, t.completed_at, t.completion_summary,
                    t.estimate_minutes, spent.total,
                    -- Имена собираем агрегатом в подзапросе, а не джойном:
                    -- джойн размножил бы задачу по числу исполнителей и съел
                    -- потолок строк выгрузки.
                    (SELECT string_agg(au.name, ', ' ORDER BY ta_x.position, ta_x.user_id)
                       FROM task_assignees ta_x
                       JOIN users au ON au.id = ta_x.user_id
                      WHERE ta_x.task_id = t.id),
                    creator.name,
                    COALESCE(origin_user.name, t.requested_by_name, creator.name),
                    t.production_brief,
                    project.code, t.project_number,
                    t.priority_score
                FROM tasks t
                LEFT JOIN users creator ON creator.id = t.created_by
                LEFT JOIN users origin_user ON origin_user.id = t.requested_by_id
                LEFT JOIN task_projects project ON project.id = t.project_id
                LEFT JOIN LATERAL (
                    SELECT SUM(r.spent_minutes)::INT AS total
                    FROM task_reports r
                    WHERE r.task_id = t.id
                ) spent ON TRUE
                {where_sql}
                ORDER BY
                    t.is_backlog DESC,
                    -- Лист «Бэклог» — в том же порядке, что на экране: балл, затем очередь.
                    CASE WHEN t.is_backlog THEN t.priority_score END ASC NULLS LAST,
                    CASE WHEN t.is_backlog THEN t.backlog_rank END ASC NULLS LAST,
                    t.created_at DESC, t.id DESC
                LIMIT %s
            """, tuple(params) + (self.TASKS_EXPORT_MAX_ROWS,))
            rows = cursor.fetchall()

        return [{
            "id": row[0],
            "subject": row[1],
            "description": row[2],
            "tag": row[3],
            "status": row[4],
            "priority": row[5] or 'normal',
            "is_backlog": bool(row[6]),
            # Даты отдаём объектами: в Excel они должны лечь датой, а не текстом,
            # иначе по ним не отсортировать и не отфильтровать.
            "created_at": row[7],
            "planned_start_at": row[8],
            "started_at": row[9],
            "due_at": row[10],
            "completed_at": row[11],
            "completion_summary": row[12],
            "estimate_minutes": row[13],
            "spent_minutes": row[14],
            "assignee_name": row[15],
            "creator_name": row[16],
            # «Поручил» — источник задачи, а если его нет, то постановщик: пустая
            # клетка тут читалась бы как «спросить не у кого».
            "requested_by_name": row[17],
            "production_brief": task_production.stored_brief(row[18]),
            # Ключ задачи проекта (HR-125) — в колонку «№» вместо глобального id.
            "key": task_projects.format_task_key(row[19], row[20]),
            "priority_score": row[21],
        } for row in rows]

    def update_task_status(self, task_id, requester_id, requester_role, action, comment=None, completion_summary=None,
                           completion_attachments=None, spent_minutes=None):
        task_id = int(task_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        action_norm = str(action or '').strip().lower()
        comment_norm = (comment or '').strip() or None
        completion_summary_norm = (completion_summary or '').strip() or None
        completion_attachments = completion_attachments or []
        spent_minutes_norm = self._normalize_task_spent_minutes(spent_minutes)

        with self._get_cursor() as cursor:
            # FOR UPDATE: переход проверяется под замком строки задачи. Без него две
            # одновременные сдачи (два исполнителя, двойной клик, две вкладки) обе
            # проходили проверку «в работе» и писали в историю два 'completed' — а по
            # числу сдач раздел «Продакшн» нумерует версии материалов (задача #316).
            cursor.execute("""
                SELECT
                    t.id, t.created_by, t.assigned_to, t.status, t.requested_by_id
                FROM tasks t
                WHERE t.id = %s
                FOR UPDATE
            """, (task_id,))
            row = cursor.fetchone()
            if not row:
                raise ValueError("TASK_NOT_FOUND")

            created_by = row[1]
            assigned_to = row[2]
            current_status = row[3]
            requested_by = row[4]
            assignee_scope = self._task_assignee_scope_tx(cursor, task_id)
            assignee_ids = [person_id for person_id, _role, _supervisor in assignee_scope]

            if not self._task_visible_for_requester(role, requester_id, created_by, assignee_scope, requested_by):
                raise PermissionError("TASK_FORBIDDEN")

            # Статус у задачи ОДИН, а исполнителей может быть несколько: взять в
            # работу и сдать вправе любой из них, и первый же перевод двигает
            # задачу целиком. Личных статусов раздел не заводит намеренно —
            # иначе «в работе» перестало бы отвечать на вопрос «идёт или нет».
            is_assignee = requester_id in assignee_ids
            # Приёмку итога делает поручитель (или постановщик, если поручителя нет),
            # а не исполнитель — свою работу себе не принимают.
            is_reviewer = self._task_can_review(role, requester_id, created_by, assignee_scope, requested_by)

            target_status = None
            history_status = None

            if action_norm == 'in_progress':
                if not is_assignee:
                    raise PermissionError("ONLY_ASSIGNEE")
                if current_status not in ('assigned', 'returned', 'in_progress'):
                    raise ValueError("INVALID_TRANSITION")
                if current_status == 'in_progress':
                    return {
                        "task_id": task_id,
                        "status": current_status
                    }
                target_status = 'in_progress'
                history_status = 'in_progress'
            elif action_norm == 'completed':
                if not is_assignee:
                    raise PermissionError("ONLY_ASSIGNEE")
                if current_status not in ('in_progress', 'returned'):
                    raise ValueError("INVALID_TRANSITION")
                cursor.execute("""
                    SELECT COUNT(*)::INT
                    FROM task_checklist_items
                    WHERE task_id = %s
                      AND is_required = TRUE
                      AND is_done = FALSE
                """, (task_id,))
                checklist_row = cursor.fetchone()
                if int((checklist_row or [0])[0] or 0) > 0:
                    raise ValueError("CHECKLIST_INCOMPLETE")
                target_status = 'completed'
                history_status = 'completed'
            elif action_norm == 'accepted':
                if not is_reviewer:
                    raise PermissionError("ONLY_REVIEWER")
                if current_status != 'completed':
                    raise ValueError("INVALID_TRANSITION")
                target_status = 'accepted'
                history_status = 'accepted'
            elif action_norm == 'returned':
                if not is_reviewer:
                    raise PermissionError("ONLY_REVIEWER")
                if current_status not in ('completed', 'accepted'):
                    raise ValueError("INVALID_TRANSITION")
                target_status = 'returned'
                history_status = 'returned'
            elif action_norm == 'reopened':
                if not is_reviewer:
                    raise PermissionError("ONLY_REVIEWER")
                if current_status != 'accepted':
                    raise ValueError("INVALID_TRANSITION")
                target_status = 'in_progress'
                history_status = 'reopened'
            else:
                raise ValueError("INVALID_ACTION")

            if action_norm == 'completed':
                cursor.execute("""
                    UPDATE tasks
                    SET
                        status = %s,
                        completion_summary = %s,
                        completed_by = %s,
                        completed_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                        updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                    RETURNING updated_at
                """, (target_status, completion_summary_norm, requester_id, task_id))
            elif target_status == 'in_progress':
                # Работа началась: фиксируем фактический старт (для таймлайна) и убираем из бэклога.
                cursor.execute("""
                    UPDATE tasks
                    SET
                        status = %s,
                        is_backlog = FALSE,
                        started_at = COALESCE(started_at, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')),
                        updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                    RETURNING updated_at
                """, (target_status, task_id))
            else:
                cursor.execute("""
                    UPDATE tasks
                    SET status = %s, updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                    RETURNING updated_at
                """, (target_status, task_id))
            updated_at_row = cursor.fetchone()
            updated_at = updated_at_row[0] if updated_at_row else None

            history_comment = completion_summary_norm if action_norm == 'completed' else comment_norm
            cursor.execute("""
                INSERT INTO task_status_history (task_id, status_code, changed_by, comment)
                VALUES (%s, %s, %s, %s)
                RETURNING id, changed_at
            """, (task_id, history_status, requester_id, history_comment))
            history_row = cursor.fetchone()

            # Сдача задачи = итоговый отчёт о проделанной работе в журнале.
            completion_report_id = None
            if action_norm == 'completed' and (completion_summary_norm or spent_minutes_norm):
                cursor.execute("""
                    INSERT INTO task_reports (task_id, author_id, kind, body, spent_minutes)
                    VALUES (%s, %s, 'completion', %s, %s)
                    RETURNING id
                """, (
                    task_id,
                    requester_id,
                    completion_summary_norm or 'Работа выполнена',
                    spent_minutes_norm
                ))
                completion_report_id = cursor.fetchone()[0]

            if action_norm in ('completed', 'returned', 'reopened'):
                attachment_kind = 'result' if action_norm == 'completed' else 'initial'
                for attachment in completion_attachments:
                    file_name = (attachment.get('file_name') or 'attachment').strip() or 'attachment'
                    content_type = (attachment.get('content_type') or '').strip() or None
                    storage_type = (attachment.get('storage_type') or 'gcs').strip().lower() or 'gcs'
                    if storage_type != 'gcs':
                        continue
                    gcs_bucket = (attachment.get('gcs_bucket') or '').strip() or None
                    gcs_blob_path = (attachment.get('gcs_blob_path') or '').strip() or None
                    file_size = int(attachment.get('file_size') or 0)
                    if not gcs_bucket or not gcs_blob_path:
                        continue
                    cursor.execute("""
                        INSERT INTO task_attachments (
                            task_id, file_name, content_type, file_size, file_data,
                            storage_type, gcs_bucket, gcs_blob_path, attachment_kind, uploaded_by,
                            thumb_blob_path
                        )
                        VALUES (%s, %s, %s, %s, NULL, 'gcs', %s, %s, %s, %s, %s)
                    """, (
                        task_id, file_name, content_type, file_size, gcs_bucket, gcs_blob_path,
                        attachment_kind, requester_id,
                        (attachment.get('thumb_blob_path') or '').strip() or None
                    ))

            return {
                "task_id": task_id,
                "status": target_status,
                "updated_at": updated_at.isoformat() if hasattr(updated_at, 'isoformat') else updated_at,
                "report_id": completion_report_id,
                "spent_minutes": spent_minutes_norm,
                "history_id": history_row[0] if history_row else None,
                "history_changed_at": (history_row[1].isoformat() if history_row and hasattr(history_row[1], 'isoformat') else (history_row[1] if history_row else None))
            }

    def update_task_checklist_item(self, task_id, checklist_item_id, requester_id, requester_role, is_done,
                                   result_note=None, update_note=False):
        task_id = int(task_id)
        checklist_item_id = int(checklist_item_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        is_done_norm = bool(is_done)

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    ci.id, ci.task_id, ci.title, ci.position, ci.is_required,
                    t.created_by, t.assigned_to,
                    ci.is_done, ci.result_note, t.requested_by_id
                FROM task_checklist_items ci
                JOIN tasks t ON t.id = ci.task_id
                WHERE ci.id = %s AND ci.task_id = %s
            """, (checklist_item_id, task_id))
            row = cursor.fetchone()
            if not row:
                raise ValueError("CHECKLIST_ITEM_NOT_FOUND")

            created_by = row[5]
            assigned_to = row[6]
            # Пункт чеклиста — общее рабочее поле задачи: отмечает его любой, кто
            # задачу видит. Это ровно то, о чём просили: «выполнять задачу по
            # этапам может каждый из них». Кто отметил, видно в completed_by.
            assignee_scope = self._task_assignee_scope_tx(cursor, task_id)
            if not self._task_visible_for_requester(role, requester_id, created_by, assignee_scope, row[9]):
                raise PermissionError("TASK_FORBIDDEN")

            was_done = bool(row[7])
            current_note = row[8]
            # Determine the note value: only change it when explicitly provided.
            if update_note:
                cleaned_note = (result_note or '').strip()
                note_value = cleaned_note or None
            else:
                note_value = current_note

            common_returning = (
                "RETURNING id, task_id, title, position, is_required, is_done, "
                "completed_by, completed_at, updated_at, result_note"
            )

            if is_done_norm and was_done:
                # Item already done — preserve original completion metadata, update note only.
                cursor.execute(f"""
                    UPDATE task_checklist_items
                    SET is_done = TRUE,
                        result_note = %s,
                        updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                    {common_returning}
                """, (note_value, checklist_item_id))
            elif is_done_norm:
                # Transition to done — stamp completer and time.
                cursor.execute(f"""
                    UPDATE task_checklist_items
                    SET is_done = TRUE,
                        completed_by = %s,
                        completed_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                        result_note = %s,
                        updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                    {common_returning}
                """, (requester_id, note_value, checklist_item_id))
            else:
                # Re-opened — clear completion metadata and the result note.
                cursor.execute(f"""
                    UPDATE task_checklist_items
                    SET is_done = FALSE,
                        completed_by = NULL,
                        completed_at = NULL,
                        result_note = NULL,
                        updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                    {common_returning}
                """, (checklist_item_id,))

            updated = cursor.fetchone()
            if not updated:
                raise ValueError("CHECKLIST_ITEM_NOT_FOUND")

            completed_by_name = None
            if updated[6]:
                cursor.execute("SELECT name FROM users WHERE id = %s", (updated[6],))
                name_row = cursor.fetchone()
                completed_by_name = name_row[0] if name_row else None

            return {
                "id": updated[0],
                "task_id": updated[1],
                "title": updated[2],
                "position": updated[3],
                "is_required": bool(updated[4]),
                "is_done": bool(updated[5]),
                "completed_by": updated[6],
                "completed_by_name": completed_by_name,
                "completed_at": self._task_dt_to_iso(updated[7]),
                "updated_at": self._task_dt_to_iso(updated[8]),
                "result_note": updated[9]
            }

    def get_task_attachment_for_requester(self, attachment_id, requester_id, requester_role):
        attachment_id = int(attachment_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    a.id, a.file_name, a.content_type, a.file_size, a.file_data, a.created_at,
                    COALESCE(a.storage_type, 'db'), a.gcs_bucket, a.gcs_blob_path,
                    t.id, t.created_by, t.assigned_to, t.requested_by_id
                FROM task_attachments a
                JOIN tasks t ON t.id = a.task_id
                WHERE a.id = %s
            """, (attachment_id,))
            row = cursor.fetchone()
            if not row:
                return None

            created_by = row[10]
            # Соисполнитель видит задачу, значит видит и её файлы: иначе он
            # получал бы 403 на вложении задачи, открытой у него на экране.
            assignee_scope = self._task_assignee_scope_tx(cursor, int(row[9]))

            if not self._task_visible_for_requester(role, requester_id, created_by, assignee_scope, row[12]):
                # Файлы задачи, которую человек только смотрит (коллега-СВ, участник
                # проекта, наблюдатель), открываются так же, как её карточка, — на чтение.
                if not self._task_observable_tx(cursor, role, requester_id, row[9], created_by):
                    raise PermissionError("TASK_FORBIDDEN")

            storage_type = row[6] or 'db'
            file_data = row[4]
            if storage_type != 'gcs':
                if isinstance(file_data, memoryview):
                    file_data = file_data.tobytes()
                elif isinstance(file_data, bytearray):
                    file_data = bytes(file_data)
                elif file_data is None:
                    file_data = b''

            return {
                "id": row[0],
                "file_name": row[1],
                "content_type": row[2] or 'application/octet-stream',
                "file_size": int(row[3] or 0),
                "file_data": file_data,
                "created_at": row[5].isoformat() if hasattr(row[5], 'isoformat') else row[5],
                "storage_type": storage_type,
                "gcs_bucket": row[7],
                "gcs_blob_path": row[8],
                "task_id": row[9]
            }

    def list_task_photo_attachments_for_requester(self, task_id, requester_id, requester_role,
                                                  content_types):
        """Картинки задачи для плиток карточки: файлы постановки, результата и уточнений.

        Доступ — ровно как у скачивания одного файла (get_task_attachment_for_requester):
        кто видит задачу или читает её как коллега-СВ, тот видит и её картинки.
        None — задачи нет; PermissionError — задача чужая.
        """
        task_id = int(task_id)
        requester_id = int(requester_id)
        role = normalize_role_value(requester_role)
        types = [str(item).lower() for item in (content_types or [])]
        if not types:
            return []

        with self._get_cursor() as cursor:
            cursor.execute("SELECT created_by, requested_by_id FROM tasks WHERE id = %s", (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            created_by = row[0]
            assignee_scope = self._task_assignee_scope_tx(cursor, task_id)
            if not self._task_visible_for_requester(role, requester_id, created_by, assignee_scope, row[1]):
                if not self._task_observable_tx(cursor, role, requester_id, task_id, created_by):
                    raise PermissionError("TASK_FORBIDDEN")

            # Тип сравнивается без параметров («image/png; charset=binary»): так
            # же его читает фронт, иначе плитка и ответ разошлись бы.
            cursor.execute("""
                SELECT id, content_type, gcs_bucket, gcs_blob_path, thumb_blob_path
                FROM task_attachments
                WHERE task_id = %s
                  AND COALESCE(storage_type, 'db') = 'gcs'
                  AND gcs_bucket IS NOT NULL AND gcs_blob_path IS NOT NULL
                  AND lower(trim(split_part(COALESCE(content_type, ''), ';', 1))) = ANY(%s)
                ORDER BY id ASC
            """, (task_id, types))
            return [
                {
                    "id": item[0],
                    "content_type": item[1],
                    "gcs_bucket": item[2],
                    "gcs_blob_path": item[3],
                    "thumb_blob_path": item[4],
                }
                for item in cursor.fetchall()
            ]

    def list_legacy_task_photo_attachments(self, uploaded_before, content_types, limit=500):
        """Снимки во вложениях задач, загруженные до перевода фото в WebP.

        Выборка добора task_photos.convert_legacy: переведённая запись становится
        image/webp и сюда больше не попадает, поэтому повторный прогон видит
        только оставленное как есть.
        """
        types = [str(item).lower() for item in (content_types or [])]
        if not types:
            return []
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, file_name, content_type, gcs_bucket, gcs_blob_path
                FROM task_attachments
                WHERE created_at < %s
                  AND COALESCE(storage_type, 'db') = 'gcs'
                  AND gcs_bucket IS NOT NULL AND gcs_blob_path IS NOT NULL
                  AND lower(trim(split_part(COALESCE(content_type, ''), ';', 1))) = ANY(%s)
                ORDER BY id ASC
                LIMIT %s
            """, (uploaded_before, types, int(limit)))
            return [
                {
                    "id": item[0],
                    "file_name": item[1],
                    "content_type": item[2],
                    "gcs_bucket": item[3],
                    "gcs_blob_path": item[4],
                }
                for item in cursor.fetchall()
            ]

    def _edited_task_production_brief(self, tag_new, current_brief, has_brief, brief_input, description):
        """Бриф после правки задачи.

        Тип сменили с продакшна — бриф уходит вместе с типом: висеть невидимым в
        задаче другого типа ему незачем. Бриф прислали (или у задачи его ещё
        нет) — проверяется заново целиком. Не прислали — остаётся прежний и
        заново НЕ проверяется: пункт, позже убранный из справочника, не должен
        запрещать поправить в старой задаче тему. Описание задания у продакшна
        обязательно всегда.
        """
        if tag_new != task_production.PRODUCTION_TAG:
            return None
        if has_brief or current_brief is None:
            return self._normalize_task_production_brief(
                tag_new, brief_input if has_brief else None, description
            )
        if not str(description or '').strip():
            raise ValueError(task_production.incomplete(['description']))
        return current_brief

    @staticmethod
    def _task_production_needs_assignee_check(brief_new, tag_new, current_tag, added, removed):
        """Сверять ли маркетинг в составе при правке.

        Только когда продакшн появляется или меняется состав. На правке одной
        темы — нет: сотрудник мог с тех пор перейти в другой отдел, и задача
        стала бы неправимой.
        """
        return brief_new is not None and (tag_new != current_tag or bool(added) or bool(removed))

    def _require_task_production_assignee_tx(self, cursor, assignee_ids):
        """Продакшн — только если ВСЕ исполнители из отдела маркетинга.

        Уточнение владельца 16.10.2026: «при выборе данного типа в исполнителях
        будет только маркетинг». Отдел — тот, где человек числится
        (`users.department_id`). Форма и так предлагает при этом типе одних
        маркетологов, но правило обязано жить и здесь — иначе его обходит любой
        запрос мимо формы.
        """
        ids = sorted({int(person_id) for person_id in (assignee_ids or []) if person_id})
        cursor.execute(
            "SELECT COUNT(*) FROM users u JOIN departments d ON d.id = u.department_id"
            " WHERE u.id = ANY(%s) AND d.code = %s",
            (ids, task_production.PRODUCTION_DEPARTMENT_CODE),
        )
        row = cursor.fetchone()
        if not ids or int((row or [0])[0] or 0) != len(ids):
            raise ValueError(task_production.NEEDS_MARKETING_ASSIGNEE)
