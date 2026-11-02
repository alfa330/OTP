"""База данных: звонки, оценки и калибровка.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _normalize_call_end_party,
    _normalize_phone,
    _parse_datetime_raw,
    Any,
    date,
    datetime,
    Dict,
    execute_values,
    FRONT_OFFICE_DEPARTMENT_CODE,
    get_gcs_client,
    json,
    List,
    logging,
    math,
    Optional,
    os,
    pd,
    re,
    timedelta,
)


class CallsMixin:
    def get_front_office_call_roster(self):
        """Менеджеры, на которых распространяется план по обзвону.

        Реестр наш, а не CRM: CRM присылает только звонивших, и менеджер с
        нулём звонков — тот самый, кого отбивка обязана назвать. Уволенных
        отсекаем: их ноль ничего не значит.

        Город берём из кадровой карточки (users.city) — CRM географию не
        присылает вовсе, а поле заведено ровно под этот отдел: его офисы стоят
        в разных городах. Дата приёма нужна знаменателю плана: у принятого
        посреди периода нельзя требовать норму за дни до трудоустройства."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.email, NULLIF(btrim(COALESCE(u.city, '')), ''),
                       u.hire_date
                FROM users u
                JOIN departments d ON d.id = u.department_id
                WHERE d.code = %s
                  AND LOWER(COALESCE(u.role, '')) IN ('operator', 'trainee')
                  AND LOWER(COALESCE(u.status, '')) <> 'fired'
                ORDER BY u.name
            """, (FRONT_OFFICE_DEPARTMENT_CODE,))
            return [{"id": r[0], "name": r[1], "email": r[2],
                     "city": r[3], "hire_date": r[4]}
                    for r in cursor.fetchall()]

    def get_front_office_call_plan(self):
        """Дневная норма звонков на менеджера; None — план не задан."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT p.calls_per_day
                FROM front_office_call_plans p
                JOIN departments d ON d.id = p.department_id
                WHERE d.code = %s
            """, (FRONT_OFFICE_DEPARTMENT_CODE,))
            r = cursor.fetchone()
            return r[0] if r else None

    def set_front_office_call_plan(self, calls_per_day, user_id=None):
        """Задать норму (или снять её нулём/None). Возвращает новое значение."""
        department_id = self.get_front_office_department_id()
        if department_id is None:
            raise ValueError("Отдел «Фронт офисы» не найден")
        with self._get_cursor() as cursor:
            if not calls_per_day:
                cursor.execute(
                    "DELETE FROM front_office_call_plans WHERE department_id = %s",
                    (department_id,))
                return None
            cursor.execute("""
                INSERT INTO front_office_call_plans (department_id, calls_per_day, updated_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (department_id) DO UPDATE SET
                    calls_per_day = EXCLUDED.calls_per_day,
                    updated_by    = EXCLUDED.updated_by,
                    updated_at    = NOW()
                RETURNING calls_per_day
            """, (department_id, int(calls_per_day), user_id))
            return cursor.fetchone()[0]

    def add_front_office_call_subscription(self, chat_id, title=None, chat_type=None,
                                           user_id=None):
        """Подписать чат на утреннюю отбивку. Повтор просто обновляет данные чата."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO front_office_call_subscriptions
                    (chat_id, title, chat_type, subscribed_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    chat_type = EXCLUDED.chat_type,
                    subscribed_by = EXCLUDED.subscribed_by
                RETURNING (xmax = 0) AS is_new
            """, (str(chat_id), title, chat_type, user_id))
            return bool(cursor.fetchone()[0])

    def remove_front_office_call_subscription(self, chat_id):
        """Отписать чат. Возвращает True, если подписка была."""
        with self._get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM front_office_call_subscriptions WHERE chat_id = %s",
                (str(chat_id),))
            return cursor.rowcount > 0

    def get_front_office_call_subscriptions(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT chat_id, title, chat_type
                FROM front_office_call_subscriptions
                ORDER BY created_at
            """)
            return [{"chat_id": r[0], "title": r[1], "chat_type": r[2]}
                    for r in cursor.fetchall()]

    def mark_front_office_call_subscription_sent(self, chat_id):
        with self._get_cursor() as cursor:
            cursor.execute(
                "UPDATE front_office_call_subscriptions SET last_sent_at = NOW() "
                "WHERE chat_id = %s", (str(chat_id),))

    def get_call_evaluation_notify_setting(self, user_id: int) -> Optional[Dict[str, Any]]:
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    COALESCE(ap.call_evaluation_notify_enabled, FALSE) AS enabled,
                    ap.call_evaluation_notify_department_id,
                    d.name AS department_name
                FROM users u
                LEFT JOIN admin_profiles ap ON ap.user_id = u.id
                LEFT JOIN departments d ON d.id = ap.call_evaluation_notify_department_id
                WHERE u.id = %s
                LIMIT 1
            """, (user_id_int,))
            row = cursor.fetchone()

        if not row:
            return None
        return {
            "enabled": bool(row[0]),
            "department_id": int(row[1]) if row[1] is not None else None,
            "department_name": row[2] or None,
        }

    def set_call_evaluation_notify_setting(self, user_id: int, enabled: bool, department_id=None) -> Optional[Dict[str, Any]]:
        try:
            user_id_int = int(user_id)
        except (TypeError, ValueError):
            return None

        department_id_int = None
        if department_id not in (None, ''):
            try:
                department_id_int = int(department_id)
            except (TypeError, ValueError):
                return None

        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO admin_profiles (
                    user_id,
                    call_evaluation_notify_enabled,
                    call_evaluation_notify_department_id
                )
                SELECT u.id, %s, %s
                FROM users u
                WHERE u.id = %s
                ON CONFLICT (user_id) DO UPDATE SET
                    call_evaluation_notify_enabled = EXCLUDED.call_evaluation_notify_enabled,
                    call_evaluation_notify_department_id = EXCLUDED.call_evaluation_notify_department_id
                RETURNING call_evaluation_notify_enabled, call_evaluation_notify_department_id
            """, (bool(enabled), department_id_int, user_id_int))
            row = cursor.fetchone()

        if not row:
            return None
        return {
            "enabled": bool(row[0]),
            "department_id": int(row[1]) if row[1] is not None else None,
        }

    def get_call_evaluation_notify_recipients(self, operator_id: int) -> List[Dict[str, Any]]:
        try:
            operator_id_int = int(operator_id)
        except (TypeError, ValueError):
            return []

        with self._get_cursor() as cursor:
            cursor.execute("SELECT department_id FROM users WHERE id = %s", (operator_id_int,))
            operator_row = cursor.fetchone()
            if not operator_row or operator_row[0] is None:
                return []

            department_id = int(operator_row[0])
            cursor.execute("""
                SELECT
                    u.id,
                    u.name,
                    u.telegram_id,
                    d.id AS department_id,
                    d.name AS department_name
                FROM admin_profiles ap
                JOIN users u ON u.id = ap.user_id
                JOIN departments d ON d.id = ap.call_evaluation_notify_department_id
                WHERE COALESCE(ap.call_evaluation_notify_enabled, FALSE) = TRUE
                  AND ap.call_evaluation_notify_department_id = %s
                  AND u.telegram_id IS NOT NULL
                  AND (
                    LOWER(COALESCE(u.role, '')) IN ('admin', 'super_admin')
                    OR d.head_user_id = u.id
                  )
                ORDER BY u.name
            """, (department_id,))
            rows = cursor.fetchall() or []

        result = []
        for row in rows:
            if row[2] is None:
                continue
            result.append({
                "id": int(row[0]),
                "name": row[1] or "",
                "telegram_id": int(row[2]),
                "department_id": int(row[3]) if row[3] is not None else None,
                "department_name": row[4] or "",
            })
        return result

    def get_departments_with_call_evaluation_activity(self) -> set:
        """Отделы, где реально слушают звонки: есть хоть одна оценка в журнале или
        звонок в пуле распределения.

        Нужно селектору «Деления звонков»: отдел без телефонии всё равно попадает
        на экран (ОП оценивают звонки руками из журнала), а отдел, который звонки
        вообще не слушает (фронт-офисы, маркетинг), — не попадает, хотя формально
        его сотрудники заведены с ролью оператора."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT department_id FROM (
                    SELECT u.department_id
                    FROM calls c
                    JOIN users u ON u.id = c.operator_id
                    WHERE c.is_draft = FALSE AND u.department_id IS NOT NULL
                    UNION
                    SELECT u.department_id
                    FROM imported_calls ic
                    JOIN users u ON u.id = ic.operator_id
                    WHERE u.department_id IS NOT NULL
                ) t
            """)
            return {r[0] for r in cursor.fetchall() if r[0] is not None}

    def upsert_daily_calls(self, rows):
        """Обновляет ТОЛЬКО количество звонков (daily_hours.calls) за дни операторов,
        не затрагивая остальные поля (work_time/break_time/talk_time/efficiency/штрафы/
        бонусы). Используется синхронизацией звонков из Oktell — она не должна затирать
        вручную внесённые часы. rows — (operator_id, day, calls), по строке на оператор-день;
        при создании новой строки остальные метрики берут DEFAULT 0, а group_id резолвится
        в активную группу на дату. Звонки пишутся одной транзакцией.

        Месячный итог work_hours (total_calls, calls_per_hour) считается из дней, но сам
        по себе пересчитывается только пересчётом часов по статусам. Поэтому здесь же
        пересчитываем месяцы, где число звонков изменилось: иначе день, исправленный
        повторным синком (18.10.2026 после сбоя прокси), в месячном итоге и «звонках в час»
        оставался прежним. Месяцы считаются уже после коммита звонков, каждый своей короткой
        транзакцией: сбой пересчёта у одного оператора не откатывает звонки остальных, а
        строки дней не держатся под блокировкой, пока считаются месяцы.
        """
        calls_by_key = {}
        for operator_id, day, calls in rows or []:
            if isinstance(day, str):
                day = datetime.strptime(day, "%Y-%m-%d").date()
            calls_by_key[(int(operator_id), day)] = max(0, int(calls or 0))
        if not calls_by_key:
            return {"saved_days": 0, "changed_days": 0, "aggregated_months": 0, "aggregate_errors": 0}

        keys = sorted(calls_by_key)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT dh.operator_id, dh.day, dh.calls, dh.group_id
                FROM daily_hours dh
                JOIN UNNEST(%s::int[], %s::date[]) AS k(operator_id, day)
                  ON k.operator_id = dh.operator_id AND k.day = dh.day
            """, ([key[0] for key in keys], [key[1] for key in keys]))
            previous = {(int(row[0]), row[1]): (int(row[2] or 0), row[3]) for row in cursor.fetchall()}

            # Группа на дату нужна только новой строке или строке без группы: у остальных
            # upsert её не меняет, а запрос на каждую строку ручного синка за месяц — это
            # тысяча с лишним лишних запросов.
            values = []
            for operator_id, day in keys:
                group_id = (previous.get((operator_id, day)) or (0, None))[1]
                if group_id is None:
                    group_id = self._get_operator_group_id_tx(cursor, operator_id, day)
                values.append((operator_id, day, calls_by_key[(operator_id, day)], group_id))
            execute_values(
                cursor,
                """
                INSERT INTO daily_hours (operator_id, day, calls, group_id)
                VALUES %s
                ON CONFLICT (operator_id, day)
                DO UPDATE SET
                    calls = EXCLUDED.calls,
                    group_id = COALESCE(daily_hours.group_id, EXCLUDED.group_id)
                """,
                values,
                page_size=500,
            )

        changed = [key for key in keys if (previous.get(key) or (0, None))[0] != calls_by_key[key]]
        changed_months = sorted({
            (operator_id, day.strftime("%Y-%m")) for operator_id, day in changed
        })
        aggregate_errors = 0
        for operator_id, month_key in changed_months:
            try:
                self.aggregate_month_from_daily(operator_id, month_key)
            except Exception:
                aggregate_errors += 1
                logging.exception(
                    "upsert_daily_calls: месяц %s оператора %s не пересчитан", month_key, operator_id
                )

        return {
            "saved_days": len(values),
            "changed_days": len(changed),
            "aggregated_months": len(changed_months) - aggregate_errors,
            "aggregate_errors": aggregate_errors,
        }

    def record_oktell_calls_sync_run(self, started_at, finished_at, date_from, date_to,
                                     triggered_by, status, updated_days=0, error=None):
        """Запись в историю прогонов синка звонков Oktell (oktell_calls_sync_runs)."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO oktell_calls_sync_runs (
                    started_at, finished_at, date_from, date_to,
                    triggered_by, status, updated_days, error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                started_at, finished_at, date_from, date_to,
                str(triggered_by or '')[:32], str(status or '')[:16], int(updated_days or 0),
                str(error)[:1000] if error else None,
            ))

    def get_oktell_calls_unfinished_days(self, date_from, date_to):
        """Дни периода, которые синк звонков Oktell так и не добрал: после окончания дня по
        нему не прошло ни одного удачного прогона. Дни раньше начала истории прогонов не в
        счёт — до неё прогоны просто не записывались, и такие дни считаем добранными."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT g.day::date
                FROM generate_series(%s::date, %s::date, INTERVAL '1 day') AS g(day)
                WHERE g.day >= (SELECT MIN(r.started_at)::date FROM oktell_calls_sync_runs r)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM oktell_calls_sync_runs r
                      WHERE r.status = 'success'
                        AND r.date_from <= g.day::date
                        AND r.date_to >= g.day::date
                        AND r.started_at >= g.day + INTERVAL '1 day'
                  )
                ORDER BY 1
            """, (date_from, date_to))
            return [row[0] for row in cursor.fetchall()]

    def get_call_by_id(self, call_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, operator_id, phone_number, audio_path
                FROM calls
                WHERE id = %s
            """, (call_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "id": row[0],
                    "operator_id": row[1],
                    "phone_number": row[2],
                    "audio_path": row[3]
                }
            return None

    def add_call_evaluation(self,
                            evaluator_id,
                            operator_id,
                            phone_number,
                            score,
                            comment=None,
                            comment_visible_to_operator=True,
                            month=None,
                            audio_path=None,
                            is_draft=False,
                            scores=None,
                            criterion_comments=None,
                            direction_id=None,
                            is_correction=False,
                            previous_version_id=None,
                            appeal_date=None,
                            question_resolved=False,
                            resolved_first_contact=None,
                            external_id=None,
                            imported_call_id=None,
                            c2d_snapshot_id=None,
                            chat_quotes=None):
        """
        Создаёт/обновляет запись в calls и, если возможно, помечает связанную запись в imported_calls как evaluated.

        c2d_snapshot_id/chat_quotes — оценка чата Chat2Desk (чат-менеджеры):
        ссылка на снапшот переписки и цитаты из неё.
        """
        month = month or datetime.now().strftime('%Y-%m')
        question_resolved = bool(question_resolved)
        resolved_first_contact = bool(resolved_first_contact) if question_resolved else None
        if isinstance(appeal_date, str) and not appeal_date.strip():
            appeal_date = None

        # Подготовка JSON данных один раз
        scores_json = json.dumps(scores) if scores else None
        criterion_comments_json = json.dumps(criterion_comments) if criterion_comments else None
        chat_quotes_json = json.dumps(chat_quotes) if chat_quotes else None
        call_end_party = None
        try:
            linked_imported_call_id = int(imported_call_id) if imported_call_id not in (None, '') else None
        except (TypeError, ValueError):
            linked_imported_call_id = None

        with self._get_cursor() as cursor:
            # Проверка существования direction_id одним запросом
            if direction_id:
                cursor.execute("SELECT 1 FROM directions WHERE id = %s AND is_active = TRUE", (direction_id,))
                if not cursor.fetchone():
                    direction_id = None

            if is_correction and previous_version_id:
                cursor.execute(
                    "SELECT audio_path, call_end_party, imported_call_id FROM calls WHERE id = %s",
                    (previous_version_id,))
                result = cursor.fetchone()
                if result:
                    if not audio_path and result[0]:
                        audio_path = result[0]
                    call_end_party = _normalize_call_end_party(result[1])
                    linked_imported_call_id = result[2]

            # Оценка импортированного звонка наследует аудио и установленную интеграцией
            # сторону завершения. Клиентскую форму для этого поля намеренно не используем.
            if not is_correction:
                imported_call = None
                if linked_imported_call_id:
                    cursor.execute(
                        """
                        SELECT id, audio_path, call_end_party
                        FROM imported_calls
                        WHERE id = %s AND operator_id = %s AND month = %s
                          AND phone_normalized = %s
                          AND datetime_raw = %s::timestamp AT TIME ZONE 'UTC'
                        LIMIT 1
                        """,
                        (
                            linked_imported_call_id,
                            operator_id,
                            month,
                            _normalize_phone(phone_number),
                            appeal_date,
                        )
                    )
                    imported_call = cursor.fetchone()
                if imported_call is None and external_id:
                    cursor.execute(
                        """
                        SELECT id, audio_path, call_end_party
                        FROM imported_calls
                        WHERE external_id = %s AND operator_id = %s AND month = %s
                        LIMIT 1
                        """,
                        (external_id, operator_id, month)
                    )
                    imported_call = cursor.fetchone()
                if imported_call is None and phone_number and appeal_date:
                    cursor.execute(
                        """
                        SELECT id, audio_path, call_end_party
                        FROM imported_calls
                        WHERE phone_normalized = %s AND operator_id = %s AND month = %s
                          AND datetime_raw = %s::timestamp AT TIME ZONE 'UTC'
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                        (_normalize_phone(phone_number), operator_id, month, appeal_date)
                    )
                    imported_call = cursor.fetchone()
                if imported_call:
                    linked_imported_call_id = imported_call[0]
                    if not audio_path and imported_call[1]:
                        audio_path = imported_call[1]
                    call_end_party = _normalize_call_end_party(imported_call[2])
                else:
                    linked_imported_call_id = None

            # Объединенная проверка черновика и существующей оценки
            cursor.execute("""
                WITH existing_data AS (
                    -- Проверка на существующий черновик
                    SELECT id, audio_path, FALSE AS is_existing_eval, created_at
                    FROM calls 
                    WHERE evaluator_id = %s 
                    AND operator_id = %s 
                    AND month = %s 
                    AND is_draft = TRUE
                    
                    UNION ALL
                    
                    -- Проверка на существующую оценку (не черновик)
                    SELECT id, NULL AS audio_path, TRUE AS is_existing_eval, created_at
                    FROM calls
                    WHERE operator_id = %s
                    AND month = %s
                    AND phone_number = %s
                    AND appeal_date IS NOT DISTINCT FROM %s::timestamp
                    AND is_draft = FALSE
                )
                SELECT id, audio_path, is_existing_eval
                FROM existing_data
                ORDER BY created_at DESC
                LIMIT 1
            """, (evaluator_id, operator_id, month, operator_id, month, phone_number, appeal_date))

            existing_record = cursor.fetchone()
            old_audio_path = None
            call_id = None

            if existing_record and not is_correction:
                call_id, old_audio_path, is_existing_eval = existing_record

                # Обновление существующей записи
                if is_draft:
                    # Для черновика обновляем все поля
                    cursor.execute("""
                        UPDATE calls
                        SET phone_number = %s,
                            score = %s,
                            comment = %s,
                            comment_visible_to_operator = %s,
                            question_resolved = %s,
                            resolved_first_contact = %s,
                            audio_path = COALESCE(%s, audio_path),
                            created_at = CURRENT_TIMESTAMP,
                            scores = %s,
                            criterion_comments = %s,
                            is_correction = %s,
                            direction_id = %s,
                            c2d_snapshot_id = COALESCE(%s, c2d_snapshot_id),
                            chat_quotes = COALESCE(%s::jsonb, chat_quotes),
                            call_end_party = %s,
                            imported_call_id = %s
                        WHERE id = %s
                        RETURNING id
                    """, (
                        phone_number, score, comment, bool(comment_visible_to_operator),
                        question_resolved, resolved_first_contact, audio_path,
                        scores_json, criterion_comments_json,
                        False,
                        direction_id,
                        int(c2d_snapshot_id) if c2d_snapshot_id is not None else None,
                        chat_quotes_json,
                        call_end_party,
                        linked_imported_call_id,
                        call_id
                    ))
                    call_id = cursor.fetchone()[0]

                    # Удаляем старый аудиофайл если он был заменен
                    if old_audio_path and audio_path and old_audio_path != audio_path:
                        try:
                            bucket_name = os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET')
                            client = get_gcs_client()
                            bucket = client.bucket(bucket_name)
                            blob = bucket.blob(old_audio_path.replace("my-app-audio-uploads/", ""))
                            blob.delete()
                        except Exception as e:
                            logging.error(f"Error removing old audio file: {str(e)}")

                    # Перед возвратом — пометить imported_calls (если найдено)
                    try:
                        if linked_imported_call_id:
                            cursor.execute("""
                                UPDATE imported_calls
                                SET status = 'evaluated',
                                    evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                                    evaluated_by = %s
                                WHERE id = %s AND status != 'evaluated'
                            """, (evaluator_id, linked_imported_call_id))
                        elif external_id:
                            cursor.execute("""
                                UPDATE imported_calls
                                SET status = 'evaluated',
                                    evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                                    evaluated_by = %s
                                WHERE external_id = %s AND operator_id = %s
                                  AND month = %s AND status != 'evaluated'
                            """, (evaluator_id, external_id, operator_id, month))
                        else:
                            # попытаться найти по телефону + дате (точное совпадение)
                            if phone_number and appeal_date:
                                phone_norm = _normalize_phone(phone_number)
                                cursor.execute("""
                                    UPDATE imported_calls
                                    SET status = 'evaluated',
                                        evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                                        evaluated_by = %s
                                    WHERE phone_normalized = %s AND operator_id = %s
                                      AND month = %s
                                      AND datetime_raw = %s::timestamp AT TIME ZONE 'UTC'
                                      AND status != 'evaluated'
                                """, (evaluator_id, phone_norm, operator_id, month, appeal_date))
                    except Exception as e:
                        logging.exception("Error marking imported_call as evaluated: %s", e)

                    return call_id

            # Переоценка чата без явно переданного снапшота — наследуем чат и
            # цитаты из предыдущей версии (как audio_path у звонков).
            if is_correction and previous_version_id and c2d_snapshot_id is None:
                cursor.execute(
                    "SELECT c2d_snapshot_id, chat_quotes FROM calls WHERE id = %s",
                    (previous_version_id,))
                prev_chat = cursor.fetchone()
                if prev_chat and prev_chat[0]:
                    c2d_snapshot_id = prev_chat[0]
                    if chat_quotes_json is None and prev_chat[1]:
                        chat_quotes_json = json.dumps(prev_chat[1])

            # Создаем новую запись (для новой оценки, переоценки или если нет черновика)
            cursor.execute("""
                INSERT INTO calls (
                    evaluator_id, operator_id, month, phone_number, score, comment,
                    comment_visible_to_operator, question_resolved, resolved_first_contact,
                    audio_path, is_draft, is_correction, previous_version_id,
                    scores, criterion_comments, direction_id, appeal_date,
                    c2d_snapshot_id, chat_quotes, call_end_party, imported_call_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                evaluator_id, operator_id, month, phone_number, score, comment,
                bool(comment_visible_to_operator), question_resolved, resolved_first_contact,
                audio_path, is_draft, is_correction, previous_version_id,
                scores_json, criterion_comments_json, direction_id, appeal_date,
                int(c2d_snapshot_id) if c2d_snapshot_id is not None else None,
                chat_quotes_json, call_end_party, linked_imported_call_id
            ))
            call_id = cursor.fetchone()[0]

            # Удаляем старый аудиофайл, если он был заменен
            if old_audio_path and audio_path and old_audio_path != audio_path:
                try:
                    bucket_name = os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET')
                    client = get_gcs_client()
                    bucket = client.bucket(bucket_name)
                    blob = bucket.blob(old_audio_path.replace("my-app-audio-uploads/", ""))
                    blob.delete()
                except Exception as e:
                    logging.error(f"Error removing old audio file: {str(e)}")

            # После успешного создания — помечаем imported_calls как evaluated (если можем сопоставить)
            try:
                if linked_imported_call_id:
                    cursor.execute("""
                        UPDATE imported_calls
                        SET status = 'evaluated',
                            evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                            evaluated_by = %s
                        WHERE id = %s AND status != 'evaluated'
                    """, (evaluator_id, linked_imported_call_id))
                elif external_id:
                    cursor.execute("""
                        UPDATE imported_calls
                        SET status = 'evaluated',
                            evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                            evaluated_by = %s
                        WHERE external_id = %s AND operator_id = %s
                          AND month = %s AND status != 'evaluated'
                    """, (evaluator_id, external_id, operator_id, month))
                else:
                    if phone_number and appeal_date:
                        phone_norm = _normalize_phone(phone_number)
                        cursor.execute("""
                            UPDATE imported_calls
                            SET status = 'evaluated',
                                evaluated_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',
                                evaluated_by = %s
                            WHERE phone_normalized = %s AND operator_id = %s
                              AND month = %s
                              AND datetime_raw = %s::timestamp AT TIME ZONE 'UTC'
                              AND status != 'evaluated'
                        """, (evaluator_id, phone_norm, operator_id, month, appeal_date))
            except Exception as e:
                logging.exception("Error marking imported_call as evaluated: %s", e)

            return call_id
    
    def import_calls_from_distribution(self, payload: dict, importer_id: Optional[int] = None) -> dict:
        """
        Импортирует JSON payload в imported_calls.
        Возвращает отчёт: {'imported': n, 'updated': m, 'skipped': k, 'errors': [...], 'missing_operators': [...]}
        """
        month = payload.get('month')
        if not month:
            raise ValueError("Payload must include 'month' (YYYY-MM)")

        imported = 0
        updated = 0
        skipped = 0
        errors = []
        missing_operators = set()

        # Звонки с номерами «Реестра тестовых номеров» в пул не идут (test_numbers).
        from test_numbers import keys as test_keys
        test_numbers = test_keys.cached_keys(self._get_cursor)
        with self._get_cursor() as cur:
            for op in payload.get('distribution', []):
                op_name = op.get('operator')
                desired = op.get('desired')
                available = op.get('available')

                cur.execute("SELECT id FROM users WHERE name = %s LIMIT 1", (op_name,))
                r = cur.fetchone()
                operator_id = r[0] if r else None
                if not operator_id:
                    missing_operators.add(op_name)

                for c in op.get('calls', []):
                    if test_keys.is_test_phone(c.get('phone'), test_numbers):
                        continue
                    external_id = c.get('id')
                    dt_raw_str = c.get('datetimeRaw')
                    parsed_dt = _parse_datetime_raw(dt_raw_str)
                    phone = c.get('phone')
                    phone_norm = _normalize_phone(phone)
                    duration = c.get('durationSec')
                    audio_path = c.get('audioPath') or c.get('audio_path')
                    notes = c.get('notes')
                    call_end_party = _normalize_call_end_party(
                        c.get('callEndParty') if 'callEndParty' in c else c.get('call_end_party')
                    ) or 'unknown'

                    try:
                        cur.execute("""
                            INSERT INTO imported_calls
                            (external_id, operator_name, operator_id, month, datetime_raw,
                            phone_number, phone_normalized, duration_sec, desired, available,
                            status, imported_at, audio_path, notes, call_end_party)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'not_evaluated',
                                    CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty',%s,%s,%s)
                            ON CONFLICT (external_id, month) DO UPDATE
                            SET operator_name = EXCLUDED.operator_name,
                                operator_id = COALESCE(EXCLUDED.operator_id, imported_calls.operator_id),
                                datetime_raw = COALESCE(EXCLUDED.datetime_raw, imported_calls.datetime_raw),
                                phone_number = COALESCE(EXCLUDED.phone_number, imported_calls.phone_number),
                                phone_normalized = COALESCE(EXCLUDED.phone_normalized, imported_calls.phone_normalized),
                                duration_sec = COALESCE(EXCLUDED.duration_sec, imported_calls.duration_sec),
                                desired = COALESCE(EXCLUDED.desired, imported_calls.desired),
                                available = COALESCE(EXCLUDED.available, imported_calls.available),
                                audio_path = COALESCE(EXCLUDED.audio_path, imported_calls.audio_path),
                                notes = COALESCE(EXCLUDED.notes, imported_calls.notes),
                                call_end_party = COALESCE(
                                    NULLIF(EXCLUDED.call_end_party, 'unknown'),
                                    imported_calls.call_end_party,
                                    EXCLUDED.call_end_party
                                ),
                                status = COALESCE(imported_calls.status, 'not_evaluated'),
                                imported_at = CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'
                            """,
                            (external_id, op_name, operator_id, month, parsed_dt, phone,
                             phone_norm, duration, desired, available, audio_path, notes,
                             call_end_party)
                        )
                        # cur.rowcount может быть ненадёжен для ON CONFLICT; просто учитываем как imported/updated логически
                        imported += 1
                    except Exception as exc:
                        errors.append({"external_id": external_id, "error": str(exc)})
                        skipped += 1

        return {
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "missing_operators": list(missing_operators)
        }

    def import_single_random_call(self, *, operator_id, operator_name, external_id, month,
                                  datetime_raw, phone, duration_sec, notes=None, audio_path=None,
                                  call_end_party=None, status=None, qa_direction_id=None):
        """Кладёт ОДИН звонок в imported_calls как НЕ оценённый (status='not_evaluated') —
        для кнопки «Случайный звонок» в журнале. Возвращает id новой строки или None, если
        такой звонок уже импортирован (ON CONFLICT (external_id, month)).

        audio_path — путь к записи в GCS (для Binotel/Oktell); может дозаписываться
        через set_imported_call_audio_path после создания строки.

        status — только для ежедневной выборки «ИИ-оценки» ('ai_sample'): такая строка
        субъект оценки ИИ, а не звонок плана прослушки, и журнал её не показывает.

        qa_direction_id — шкала ИИ-оценки этого звонка, если она не совпадает с
        направлением оператора (звонки обзвона, см. subjects.scale_direction_id). Уже
        лежащую строку конфликт не трогает: пометку ей ставят отдельным UPDATE.

        Звонок с номером из «Реестра тестовых номеров» (test_numbers) в пул не кладётся
        вовсе — None, как у дубля: и для журнала, и для ИИ это не работа с водителем.
        Это страховка: выборки и деление отсеивают такие звонки раньше, до скачивания
        записи."""
        from test_numbers import keys as test_keys
        if test_keys.is_test_phone(phone, test_keys.cached_keys(self._get_cursor)):
            return None
        parsed_dt = _parse_datetime_raw(datetime_raw)
        phone_norm = _normalize_phone(phone)
        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO imported_calls
                (external_id, operator_name, operator_id, month, datetime_raw,
                 phone_number, phone_normalized, duration_sec, status, imported_at, notes,
                 audio_path, call_end_party, qa_direction_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty', %s, %s, %s, %s)
                ON CONFLICT (external_id, month) DO NOTHING
                RETURNING id
            """, (external_id, operator_name, operator_id, month, parsed_dt,
                  phone, phone_norm, duration_sec, status or 'not_evaluated', notes, audio_path,
                  _normalize_call_end_party(call_end_party) or 'unknown',
                  int(qa_direction_id) if qa_direction_id is not None else None))
            row = cur.fetchone()
        return int(row[0]) if row else None

    def get_unknown_oktell_call_external_ids(self, limit=10000, after_external_id=None):
        """UUID imports with no exact party yet; Binotel generalCallID values are numeric."""
        after_external_id = (
            str(after_external_id).strip().lower() if after_external_id else None
        )
        with self._get_cursor() as cur:
            cur.execute(
                """
                SELECT LOWER(external_id) AS external_id
                FROM imported_calls
                WHERE call_end_party = 'unknown'
                  AND external_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                  AND (
                      call_end_party_checked_at IS NULL
                      OR call_end_party_checked_at < CURRENT_TIMESTAMP - INTERVAL '7 days'
                  )
                  AND (%s IS NULL OR LOWER(external_id) > %s)
                GROUP BY LOWER(external_id)
                ORDER BY MIN(call_end_party_checked_at) NULLS FIRST, LOWER(external_id)
                LIMIT %s
                """,
                (
                    after_external_id,
                    after_external_id,
                    max(1, min(int(limit or 10000), 50000)),
                )
            )
            return [str(row[0]).lower() for row in cur.fetchall() if row and row[0]]

    def get_unknown_binotel_call_end_party_months(self, limit=6):
        """Месяцы, где сторона завершения не известна у Binotel-звонков ТЭЗ.

        Binotel-звонки узнаём по ЧИСЛОВОМУ external_id (это generalCallID), в отличие
        от UUID-conn_id Oktell — см. get_unknown_oktell_call_external_ids. Бэкфилл для
        них идёт МЕСЯЦАМИ, а не курсором по id: кабинет Binotel отдаёт журнал звонков
        периодом, поэтому один экспорт закрывает сразу все неизвестные звонки месяца.
        Сначала месяцы, которые дольше всех не проверяли."""
        with self._get_cursor() as cur:
            cur.execute(
                """
                SELECT month
                FROM imported_calls
                WHERE call_end_party = 'unknown'
                  AND external_id ~ '^[0-9]+$'
                  AND month IS NOT NULL
                  AND (
                      call_end_party_checked_at IS NULL
                      OR call_end_party_checked_at < CURRENT_TIMESTAMP - INTERVAL '7 days'
                  )
                GROUP BY month
                ORDER BY MIN(call_end_party_checked_at) NULLS FIRST, month DESC
                LIMIT %s
                """,
                (max(1, min(int(limit or 6), 60)),)
            )
            return [str(row[0]) for row in cur.fetchall() if row and row[0]]

    def get_unknown_binotel_call_external_ids_for_month(self, month, limit=20000):
        """generalCallID звонков месяца, у которых сторона завершения не известна."""
        with self._get_cursor() as cur:
            cur.execute(
                """
                SELECT external_id
                FROM imported_calls
                WHERE call_end_party = 'unknown'
                  AND external_id ~ '^[0-9]+$'
                  AND month = %s
                GROUP BY external_id
                ORDER BY external_id
                LIMIT %s
                """,
                (str(month), max(1, min(int(limit or 20000), 100000)))
            )
            return [str(row[0]) for row in cur.fetchall() if row and row[0]]

    def mark_imported_call_end_parties_checked(self, external_ids):
        """Отмечает попытку доуточнения, чтобы неразрешимые звонки не заслоняли
        остальные в следующих проходах бэкфилла (общее для Oktell и Binotel)."""
        values = [
            (str(external_id).strip().lower(),)
            for external_id in (external_ids or [])
            if external_id
        ]
        if not values:
            return 0
        with self._get_cursor() as cur:
            execute_values(
                cur,
                """
                UPDATE imported_calls AS ic
                SET call_end_party_checked_at = CURRENT_TIMESTAMP
                FROM (VALUES %s) AS data(external_id)
                WHERE LOWER(ic.external_id) = data.external_id
                  AND ic.call_end_party = 'unknown'
                """,
                values,
                page_size=len(values)
            )
            return max(int(cur.rowcount or 0), 0)

    def mark_oktell_call_end_parties_checked(self, external_ids):
        """Prevent unresolved legacy UUIDs from starving later backfill pages."""
        return self.mark_imported_call_end_parties_checked(external_ids)

    def update_imported_call_end_parties(self, party_by_external_id):
        """Persist exact provider results and refresh linked evaluation snapshots."""
        values = []
        for external_id, party in (party_by_external_id or {}).items():
            normalized = _normalize_call_end_party(party)
            if external_id and normalized and normalized != 'unknown':
                values.append((str(external_id).strip().lower(), normalized))
        if not values:
            return 0

        with self._get_cursor() as cur:
            execute_values(
                cur,
                """
                UPDATE imported_calls AS ic
                SET call_end_party = data.call_end_party
                FROM (VALUES %s) AS data(external_id, call_end_party)
                WHERE LOWER(ic.external_id) = data.external_id
                  AND (ic.call_end_party IS NULL OR ic.call_end_party = 'unknown')
                RETURNING ic.id
                """,
                values,
                page_size=len(values)
            )
            updated_ids = [row[0] for row in cur.fetchall()]
            if updated_ids:
                cur.execute(
                    """
                    UPDATE calls AS c
                    SET call_end_party = ic.call_end_party
                    FROM imported_calls AS ic
                    WHERE c.imported_call_id = ic.id
                      AND c.imported_call_id = ANY(%s)
                      AND (c.call_end_party IS NULL OR c.call_end_party = 'unknown')
                    """,
                    (updated_ids,)
                )
        return len(updated_ids)

    def set_imported_call_audio_path(self, imported_id, audio_path):
        """Проставляет audio_path импортированному звонку (фоновая заливка записи в GCS)."""
        with self._get_cursor() as cur:
            cur.execute(
                "UPDATE imported_calls SET audio_path = %s WHERE id = %s",
                (audio_path, int(imported_id))
            )

    def get_imported_call_audio(self, imported_id):
        """Возвращает {operator_id, audio_path, status, external_id, notes} импортированного
        звонка или None. external_id/notes нужны для докачки записи из Binotel/Oktell."""
        with self._get_cursor() as cur:
            cur.execute(
                "SELECT operator_id, audio_path, status, external_id, notes FROM imported_calls WHERE id = %s",
                (int(imported_id),)
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"operator_id": row[0], "audio_path": row[1], "status": row[2],
                "external_id": row[3], "notes": row[4]}

    def get_call_distribution_settings(self) -> dict:
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT s.min_duration_sec, s.max_duration_sec, s.enabled, s.updated_by, s.updated_at, u.name
                FROM call_distribution_settings s
                LEFT JOIN users u ON u.id = s.updated_by
                WHERE s.id = 1
            """)
            r = cur.fetchone()
        if not r:
            return {"min_duration_sec": 0, "max_duration_sec": 3600, "enabled": True,
                    "updated_by": None, "updated_by_name": None, "updated_at": None}
        return {
            "min_duration_sec": int(r[0] or 0),
            "max_duration_sec": int(r[1] or 0),
            "enabled": bool(r[2]),
            "updated_by": r[3],
            "updated_by_name": r[5],
            "updated_at": r[4].isoformat() if r[4] else None,
        }

    def update_call_distribution_settings(self, payload: dict, user_id: Optional[int] = None) -> dict:
        current = self.get_call_distribution_settings()
        min_d = current["min_duration_sec"]
        max_d = current["max_duration_sec"]
        enabled = current["enabled"]
        payload = payload or {}
        if payload.get('min_duration_sec') is not None:
            min_d = max(0, int(payload['min_duration_sec']))
        if payload.get('max_duration_sec') is not None:
            max_d = max(0, int(payload['max_duration_sec']))
        if payload.get('enabled') is not None:
            enabled = bool(payload['enabled'])
        if max_d and min_d and max_d < min_d:
            raise ValueError("max_duration_sec must be >= min_duration_sec")
        snapshot = json.dumps({"min_duration_sec": min_d, "max_duration_sec": max_d, "enabled": enabled})
        with self._get_cursor() as cur:
            cur.execute("""
                UPDATE call_distribution_settings
                SET min_duration_sec = %s, max_duration_sec = %s, enabled = %s,
                    updated_by = %s, updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE id = 1
            """, (min_d, max_d, enabled, user_id))
            cur.execute("""
                INSERT INTO call_distribution_settings_history (changed_by, settings)
                VALUES (%s, %s::jsonb)
            """, (user_id, snapshot))
        return self.get_call_distribution_settings()

    def get_imported_call_keys_for_month(self, month: str) -> dict:
        """{operator_id: set(external_id)} за месяц — чтобы добор пула не дублировал звонки."""
        out = {}
        with self._get_cursor() as cur:
            cur.execute(
                "SELECT operator_id, external_id FROM imported_calls "
                "WHERE month = %s AND operator_id IS NOT NULL AND external_id IS NOT NULL",
                (month,)
            )
            for op_id, ext in cur.fetchall():
                out.setdefault(int(op_id), set()).add(str(ext))
        return out

    def get_imported_call_external_ids_for_operator(self, operator_id) -> set:
        """Множество external_id (conn_id) всех уже импортированных звонков оператора —
        любой месяц/статус. Нужно «Случайному звонку», чтобы не выдавать то, что уже
        лежит в журнале/пуле (в т.ч. уже оценённое)."""
        out = set()
        with self._get_cursor() as cur:
            cur.execute(
                "SELECT external_id FROM imported_calls "
                "WHERE operator_id = %s AND external_id IS NOT NULL",
                (operator_id,)
            )
            for (ext,) in cur.fetchall():
                out.add(str(ext))
        return out

    def get_imported_calls_status_counts_by_operator(self, month: str) -> dict:
        """{operator_id: {total, evaluated, not_evaluated, skipped}} за месяц (для статус-таблицы).

        Строки ежедневной выборки «ИИ-оценки» (status='ai_sample') в пул плана
        прослушки не входят: это субъекты оценки ИИ, и «всего в пуле» с ними
        выводило бы в таблицу операторов, у которых нормы нет вовсе."""
        out = {}
        # Оценки разговоров с номерами «Реестра тестовых номеров» (test_numbers) в счёт
        # и средний балл оператора не входят: сотрудник проверял линию, а не работал.
        from test_numbers import keys as test_keys
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT operator_id,
                       COUNT(*) FILTER (WHERE status <> 'ai_sample') AS total,
                       COUNT(*) FILTER (WHERE status = 'evaluated') AS evaluated,
                       COUNT(*) FILTER (WHERE status = 'not_evaluated') AS not_evaluated,
                       COUNT(*) FILTER (WHERE status = 'skipped') AS skipped
                FROM imported_calls
                WHERE month = %s AND operator_id IS NOT NULL
                  AND """ + test_keys.sql_not_test(
                      "COALESCE(NULLIF(phone_normalized, ''), phone_number)") + """
                GROUP BY operator_id
            """, (month,))
            for r in cur.fetchall():
                out[int(r[0])] = {
                    "total": int(r[1] or 0), "evaluated": int(r[2] or 0),
                    "not_evaluated": int(r[3] or 0), "skipped": int(r[4] or 0),
                }
        return out

    def list_imported_calls(self, month: str, status: Optional[str] = None, operator_name: Optional[str] = None, limit: int = 200):
        q = "SELECT id, external_id, operator_name, operator_id, datetime_raw, phone_number, phone_normalized, duration_sec, desired, available, status, evaluated_at, evaluated_by, notes FROM imported_calls WHERE month = %s"
        params = [month]
        if status:
            q += " AND status = %s"
            params.append(status)
        if operator_name:
            q += " AND operator_name = %s"
            params.append(operator_name)
        q += " ORDER BY datetime_raw NULLS LAST LIMIT %s"
        params.append(limit)
        with self._get_cursor() as cur:
            cur.execute(q, tuple(params))
            rows = cur.fetchall()
        keys = ["id","external_id","operator_name","operator_id","datetime_raw","phone_number","phone_normalized","duration_sec","desired","available","status","evaluated_at","evaluated_by","notes"]
        return [dict(zip(keys, r)) for r in rows]

    def mark_imported_call_skipped(self, imported_call_id: str, reason: Optional[str] = None):
        with self._get_cursor() as cur:
            cur.execute("UPDATE imported_calls SET status = 'skipped', notes = COALESCE(notes,'') || %s WHERE id = %s",
                        (f"\nskipped: {reason}" if reason else "\nskipped", imported_call_id))
            return cur.rowcount == 1

    def parse_calls_file(self, file, target_date: Optional[str] = None):
        try:
            filename = str(file.filename or "").lower()

            if filename.endswith(".csv"):
                try:
                    df = pd.read_csv(file, sep=None, engine="python")
                except Exception:
                    file.stream.seek(0)
                    df = pd.read_csv(file, sep=";")
                sheet_name = "CSV"
            elif filename.endswith((".xls", ".xlsx")):
                excel = pd.ExcelFile(file)
                sheet_name = excel.sheet_names[0]
                df = pd.read_excel(excel, sheet_name=sheet_name)
            else:
                return None, None, "Неверный формат файла. Поддерживаются только CSV, XLS, XLSX"

            if df is None or df.empty:
                return None, None, "Файл пустой или не содержит данных"

            df.columns = [str(col).strip() for col in df.columns]

            def _norm_header(value):
                txt = str(value or "").strip().lower()
                txt = txt.replace("ё", "е")
                txt = re.sub(r"\s+", " ", txt)
                return txt

            normalized_to_original = {_norm_header(col): col for col in df.columns}

            def _pick_column(candidates):
                # exact normalized match
                for cand in candidates:
                    found = normalized_to_original.get(_norm_header(cand))
                    if found:
                        return found
                # soft contains match
                candidate_norms = [_norm_header(c) for c in candidates]
                for col in df.columns:
                    col_norm = _norm_header(col)
                    for cand_norm in candidate_norms:
                        if cand_norm and (cand_norm in col_norm or col_norm in cand_norm):
                            return col
                return None

            name_col = _pick_column(["ФИО", "ФИО оператора", "Оператор", "Name"])
            date_col = _pick_column(["Дата", "Date", "День", "Дата звонка"])
            calls_col = _pick_column([
                "Кол-во поступивших",
                "Количество поступивших"
            ])

            if not name_col or not date_col or not calls_col:
                return None, None, (
                    "Ожидаются колонки: ФИО, Дата, Кол-во поступивших "
                    f"(найдены: {', '.join(map(str, df.columns))})"
                )

            target_date_obj = None
            if target_date:
                try:
                    target_date_obj = datetime.strptime(str(target_date), "%Y-%m-%d").date()
                except Exception:
                    return None, None, "Некорректный формат выбранной даты. Ожидается YYYY-MM-DD"

            def _parse_row_date(raw_value):
                if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
                    return None
                if isinstance(raw_value, pd.Timestamp):
                    return raw_value.date()
                if isinstance(raw_value, datetime):
                    return raw_value.date()
                if isinstance(raw_value, date):
                    return raw_value

                text = str(raw_value).strip()
                if not text or text.lower() == "nan":
                    return None

                for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
                    try:
                        return datetime.strptime(text, fmt).date()
                    except Exception:
                        pass

                parsed = pd.to_datetime(text, dayfirst=True, errors="coerce")
                if pd.isna(parsed):
                    return None
                if isinstance(parsed, pd.Timestamp):
                    return parsed.date()
                return None

            def _parse_calls(raw_value):
                if raw_value is None or (isinstance(raw_value, float) and pd.isna(raw_value)):
                    return 0
                if isinstance(raw_value, (int, float)) and not pd.isna(raw_value):
                    try:
                        return max(int(round(float(raw_value))), 0)
                    except Exception:
                        return 0
                text = str(raw_value).strip().replace(" ", "").replace("\xa0", "")
                if not text or text.lower() == "nan":
                    return 0
                text = text.replace(",", ".")
                try:
                    return max(int(round(float(text))), 0)
                except Exception:
                    digits = re.sub(r"[^\d\-]", "", text)
                    try:
                        return max(int(digits), 0)
                    except Exception:
                        return 0

            aggregated = {}
            skipped_other_date = 0

            for _, row in df.iterrows():
                name_val = row.get(name_col)
                name = str(name_val or "").strip()
                if not name or name.lower() == "nan":
                    continue

                row_date_obj = _parse_row_date(row.get(date_col))
                if not row_date_obj:
                    continue

                if target_date_obj and row_date_obj != target_date_obj:
                    skipped_other_date += 1
                    continue

                calls_value = _parse_calls(row.get(calls_col))
                date_str = row_date_obj.strftime("%Y-%m-%d")
                key = (name, date_str)
                if key not in aggregated:
                    aggregated[key] = {
                        "name": name,
                        "date": date_str,
                        "calls": int(calls_value),
                        "month": date_str[:7]
                    }
                else:
                    aggregated[key]["calls"] = int(aggregated[key]["calls"]) + int(calls_value)

            operators = sorted(
                aggregated.values(),
                key=lambda item: (item.get("date") or "", str(item.get("name") or "").lower())
            )

            if not operators:
                if target_date_obj and skipped_other_date > 0:
                    return None, None, (
                        f"В файле нет строк за выбранную дату {target_date_obj.strftime('%Y-%m-%d')}"
                    )
                return None, None, "Не удалось распознать строки с данными ФИО/Дата/Кол-во звонков"

            return sheet_name, operators, None

        except Exception as e:
            return None, None, str(e)

    def _get_base_call_target_by_tenure(self, tenure_months: Optional[int]) -> int:
        if tenure_months is None:
            return 20
        if tenure_months <= 2:
            return 20
        if tenure_months <= 5:
            return 10
        return 5

    def _fetch_operator_call_evaluation_target_source(self, cursor, operator_id: int, target_month: str):
        month_start_date, next_month_start = self._get_month_date_range(target_month)
        cursor.execute("""
            SELECT
                u.hire_date,
                COALESCE(wh.regular_hours, 0) AS regular_hours,
                COALESCE(wh.norm_hours, 0) AS norm_hours,
                COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN t.end_time <= t.start_time
                                THEN EXTRACT(EPOCH FROM (t.end_time + INTERVAL '24 hours' - t.start_time))
                            ELSE EXTRACT(EPOCH FROM (t.end_time - t.start_time))
                        END
                    ) / 3600.0
                    FROM trainings t
                    WHERE t.operator_id = u.id
                      AND t.count_in_hours = TRUE
                      AND t.training_date >= %s
                      AND t.training_date < %s
                ), 0) AS training_hours,
                COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN ti.end_time <= ti.start_time
                                THEN EXTRACT(EPOCH FROM (ti.end_time + INTERVAL '24 hours' - ti.start_time))
                            ELSE EXTRACT(EPOCH FROM (ti.end_time - ti.start_time))
                        END
                    ) / 3600.0
                    FROM operator_technical_issues ti
                    WHERE ti.operator_id = u.id
                      AND ti.issue_date >= %s
                      AND ti.issue_date < %s
                ), 0) AS technical_issue_hours,
                COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN oa.end_time <= oa.start_time
                                THEN EXTRACT(EPOCH FROM (oa.end_time + INTERVAL '24 hours' - oa.start_time))
                            ELSE EXTRACT(EPOCH FROM (oa.end_time - oa.start_time))
                        END
                    ) / 3600.0
                    FROM operator_offline_activities oa
                    WHERE oa.operator_id = u.id
                      AND oa.activity_date >= %s
                      AND oa.activity_date < %s
                ), 0)
                + COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN ws.end_time <= ws.start_time
                                THEN EXTRACT(EPOCH FROM (ws.end_time + INTERVAL '24 hours' - ws.start_time))
                            ELSE EXTRACT(EPOCH FROM (ws.end_time - ws.start_time))
                        END
                    ) / 3600.0
                    FROM work_shifts ws
                    WHERE ws.operator_id = u.id
                      AND COALESCE(ws.shift_type, 'regular') = 'office_practice'
                      AND ws.shift_date >= %s
                      AND ws.shift_date < %s
                ), 0) AS offline_activity_hours
            FROM users u
            LEFT JOIN work_hours wh
                ON wh.operator_id = u.id
               AND wh.month = %s
            WHERE u.id = %s
            LIMIT 1
        """, (
            month_start_date,
            next_month_start,
            month_start_date,
            next_month_start,
            month_start_date,
            next_month_start,
            month_start_date,
            next_month_start,
            target_month,
            operator_id
        ))
        row = cursor.fetchone()
        if not row:
            return {
                'hire_date_value': None,
                'regular_hours': 0.0,
                'operator_norm_hours': 0.0,
                'training_hours': 0.0,
                'technical_issue_hours': 0.0,
                'offline_activity_hours': 0.0,
            }

        manual_phone_training_map, _manual_phone_training_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=month_start_date,
            end_date=next_month_start - timedelta(days=1)
        )
        _manual_phone_training_counted_map, manual_phone_training_counted_totals = self._load_phone_shift_manual_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=month_start_date,
            end_date=next_month_start - timedelta(days=1),
            counted_only=True
        )
        status_phone_training_map, _status_phone_training_totals = self._load_phone_shift_training_offline_intervals_by_operator_day_tx(
            cursor=cursor,
            operator_ids=[operator_id],
            start_date=month_start_date,
            end_date=next_month_start - timedelta(days=1)
        )
        phone_training_totals = self._sum_phone_shift_offline_interval_maps(
            [operator_id],
            manual_phone_training_map,
            status_phone_training_map
        )
        return {
            'hire_date_value': row[0],
            'regular_hours': float(row[1] or 0.0),
            'operator_norm_hours': float(row[2] or 0.0),
            'training_hours': max(
                0.0,
                float(row[3] or 0.0) - float(manual_phone_training_counted_totals.get(int(operator_id), 0.0) or 0.0)
            ),
            'technical_issue_hours': float(row[4] or 0.0),
            'offline_activity_hours': float(row[5] or 0.0) + float(phone_training_totals.get(int(operator_id), 0.0) or 0.0),
        }

    def _build_operator_call_evaluation_target(
        self,
        operator_id: int,
        target_month: str,
        hire_date_value=None,
        regular_hours: float = 0.0,
        training_hours: float = 0.0,
        technical_issue_hours: float = 0.0,
        offline_activity_hours: float = 0.0,
        operator_norm_hours: float = 0.0
    ):
        month_end_date = self._get_month_end_date(target_month)
        regular_hours_value = float(regular_hours or 0.0)
        training_hours_value = float(training_hours or 0.0)
        technical_issue_hours_value = float(technical_issue_hours or 0.0)
        offline_activity_hours_value = float(offline_activity_hours or 0.0)
        accounted_hours_value = (
            regular_hours_value
            + training_hours_value
            + technical_issue_hours_value
            + offline_activity_hours_value
        )
        operator_norm_hours_value = float(operator_norm_hours or 0.0)
        work_days_value = int(self._get_month_work_days(target_month))
        full_rate_norm_hours_value = float(self._get_full_rate_norm_hours(target_month) or 0.0)

        tenure_months = self._get_tenure_months_for_reference(hire_date_value, month_end_date)
        base_call_target = self._get_base_call_target_by_tenure(tenure_months)
        worked_hours_ratio = (
            accounted_hours_value / full_rate_norm_hours_value
        ) if full_rate_norm_hours_value > 0 else 0.0
        required_calls_raw = float(worked_hours_ratio) * float(base_call_target)
        required_calls = int(math.ceil(required_calls_raw - 1e-9)) if required_calls_raw > 0 else 0

        if tenure_months is None:
            tenure_bucket = 'unknown'
        elif tenure_months <= 2:
            tenure_bucket = '0_2_months'
        elif tenure_months <= 5:
            tenure_bucket = '3_5_months'
        else:
            tenure_bucket = '6_plus_months'

        return {
            'operator_id': int(operator_id),
            'month': target_month,
            'reference_date': month_end_date.isoformat(),
            'tenure_months': tenure_months,
            'tenure_bucket': tenure_bucket,
            'base_call_target': int(base_call_target),
            'worked_hours_for_calls': round(float(regular_hours_value), 2),
            'training_hours': round(float(training_hours_value), 2),
            'technical_issue_hours': round(float(technical_issue_hours_value), 2),
            'offline_activity_hours': round(float(offline_activity_hours_value), 2),
            'accounted_hours': round(float(accounted_hours_value), 2),
            'worked_hours_used': round(float(accounted_hours_value), 2),
            'work_days': int(work_days_value),
            'operator_norm_hours': round(float(operator_norm_hours_value), 2),
            'full_rate_norm_hours': round(float(full_rate_norm_hours_value), 2),
            'norm_hours': round(float(full_rate_norm_hours_value), 2),
            'worked_hours_ratio': round(float(worked_hours_ratio), 4),
            'required_calls_raw': round(float(required_calls_raw), 2),
            'required_calls': int(required_calls),
        }

    def get_operator_call_evaluation_target(self, operator_id: int, month: Optional[str] = None):
        target_month = str(month or datetime.now().strftime('%Y-%m'))

        with self._get_cursor() as cursor:
            source = self._fetch_operator_call_evaluation_target_source(cursor, operator_id, target_month)

        return self._build_operator_call_evaluation_target(
            operator_id=operator_id,
            target_month=target_month,
            hire_date_value=source.get('hire_date_value'),
            regular_hours=source.get('regular_hours'),
            training_hours=source.get('training_hours'),
            technical_issue_hours=source.get('technical_issue_hours'),
            offline_activity_hours=source.get('offline_activity_hours'),
            operator_norm_hours=source.get('operator_norm_hours')
        )

    def get_operator_call_evaluation_targets_for_month(self, operator_ids, month: Optional[str] = None):
        target_month = str(month or datetime.now().strftime('%Y-%m'))

        normalized_ids = []
        seen_ids = set()
        for raw_id in (operator_ids or []):
            try:
                op_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if op_id in seen_ids:
                continue
            seen_ids.add(op_id)
            normalized_ids.append(op_id)

        if not normalized_ids:
            return {}

        result = {}
        with self._get_cursor() as cursor:
            for op_id in normalized_ids:
                source = self._fetch_operator_call_evaluation_target_source(cursor, op_id, target_month)
                result[op_id] = self._build_operator_call_evaluation_target(
                    operator_id=op_id,
                    target_month=target_month,
                    hire_date_value=source.get('hire_date_value'),
                    regular_hours=source.get('regular_hours'),
                    training_hours=source.get('training_hours'),
                    technical_issue_hours=source.get('technical_issue_hours'),
                    offline_activity_hours=source.get('offline_activity_hours'),
                    operator_norm_hours=source.get('operator_norm_hours')
                )

        return result

    def get_call_evaluations(self, operator_id, month=None, include_target: bool = False):
        """
        Возвращает оценки звонков (calls) + неоценённые звонки (imported_calls).
        Последняя версия оценки определяется по ключу
        (operator_id, phone_number, month, appeal_date).
        Для calls.duration -> NULL, для imported_calls.duration -> ic.duration_sec.

        Оценки и звонки с номерами «Реестра тестовых номеров» (test_numbers) в журнал не
        идут: средний балл оператора браузер считает по этому же списку.
        """
        from test_numbers import keys as test_keys
        query = """
            WITH latest_versions AS (
                SELECT 
                    operator_id,
                    phone_number,
                    month,
                    appeal_date,
                    MAX(created_at) AS latest_date
                FROM calls
                WHERE operator_id = %s
                  AND """ + test_keys.sql_calls_not_test() + """
                GROUP BY operator_id, phone_number, month, appeal_date
            ),
            latest_calls AS (
                SELECT 
                    c.id::text AS id_text,
                    c.operator_id,
                    c.phone_number,
                    c.month,
                    c.appeal_date,
                    c.created_at
                FROM calls c
                JOIN latest_versions lv ON 
                    c.operator_id = lv.operator_id
                    AND (
                        (c.appeal_date IS NULL AND lv.appeal_date IS NULL)
                        OR c.appeal_date = lv.appeal_date
                    )
                    AND c.phone_number = lv.phone_number 
                    AND c.month = lv.month 
                    AND c.created_at = lv.latest_date
                WHERE c.operator_id = %s
            )
            SELECT 
                c.id::text AS id,
                c.month, 
                c.phone_number, 
                c.appeal_date,
                c.score, 
                c.comment,
                c.audio_path,
                c.is_draft,
                c.is_correction,
                TO_CHAR(c.created_at, 'YYYY-MM-DD HH24:MI') AS evaluation_date,
                c.scores,
                c.criterion_comments,
                d.id AS direction_id,
                d.name AS direction_name,
                d.criteria AS direction_criteria,
                d.has_file_upload AS direction_has_file_upload,
                u.name AS evaluator_name,
                c.created_at,
                c.sv_request,
                c.sv_request_comment,
                c.sv_request_by,
                su.name AS sv_request_by_name,
                c.sv_request_by_role,
                TO_CHAR(c.sv_request_at, 'YYYY-MM-DD HH24:MI') AS sv_request_at,
                c.sv_request_approved,
                c.sv_request_approved_by,
                apu.name AS sv_request_approved_by_name,
                TO_CHAR(c.sv_request_approved_at, 'YYYY-MM-DD HH24:MI') AS sv_request_approved_at,
                c.sv_request_rejected,
                c.sv_request_rejected_by,
                rju.name AS sv_request_rejected_by_name,
                TO_CHAR(c.sv_request_rejected_at, 'YYYY-MM-DD HH24:MI') AS sv_request_rejected_at,
                c.sv_request_reject_comment,
                NULL::numeric AS duration, -- у оценённых нет длительности
                FALSE AS is_imported,
                COALESCE(c.comment_visible_to_operator, TRUE) AS comment_visible_to_operator,
                cf.id AS feedback_id,
                cf.feedback_comment,
                cf.delivery_comment,
                cf.feedback_date,
                cf.start_time,
                cf.end_time,
                cf.training_id,
                TO_CHAR(cf.updated_at, 'YYYY-MM-DD HH24:MI') AS feedback_updated_at,
                cf_creator.name AS feedback_created_by_name,
                cf_updater.name AS feedback_updated_by_name,
                COALESCE(c.question_resolved, FALSE) AS question_resolved,
                c.resolved_first_contact,
                c.sv_request_approve_comment,
                c.c2d_snapshot_id,
                c.chat_quotes,
                c.call_end_party,
                c.imported_call_id,
                c.survey_response_id,
                ts.id AS knowledge_test_survey_id,
                ts.title AS knowledge_test_title,
                tr.earned_points AS knowledge_test_earned_points,
                tr.max_points AS knowledge_test_max_points,
                tr.is_auto_submitted AS knowledge_test_auto_submitted
            FROM calls c
            JOIN latest_calls lc ON c.id::text = lc.id_text
            LEFT JOIN directions d ON c.direction_id = d.id
            LEFT JOIN users u ON c.evaluator_id = u.id
            LEFT JOIN users su ON c.sv_request_by = su.id
            LEFT JOIN users apu ON c.sv_request_approved_by = apu.id
            LEFT JOIN users rju ON c.sv_request_rejected_by = rju.id
            LEFT JOIN call_feedbacks cf ON cf.call_id = c.id
            LEFT JOIN users cf_creator ON cf.created_by = cf_creator.id
            LEFT JOIN users cf_updater ON cf.updated_by = cf_updater.id
            LEFT JOIN survey_responses tr ON tr.id = c.survey_response_id
            LEFT JOIN surveys ts ON ts.id = tr.survey_id
            WHERE c.operator_id = %s
        """
        params = [operator_id, operator_id, operator_id]
        if month:
            query += " AND c.month = %s"
            params.append(month)

        # добавляем неоценённые звонки из imported_calls
        query += """
            UNION ALL
            SELECT 
                ic.id::text AS id,
                ic.month,
                ic.phone_number,
                ic.datetime_raw AS appeal_date,
                NULL::numeric AS score,
                NULL::text AS comment,
                ic.audio_path AS audio_path,
                FALSE AS is_draft,
                FALSE AS is_correction,
                NULL::text AS evaluation_date,
                NULL::jsonb AS scores,
                NULL::jsonb AS criterion_comments,
                NULL::integer AS direction_id,
                NULL::text AS direction_name,
                NULL::jsonb AS direction_criteria,
                NULL::boolean AS direction_has_file_upload,
                NULL::text AS evaluator_name,
                ic.imported_at AS created_at,
                FALSE::boolean AS sv_request,
                NULL::text AS sv_request_comment,
                NULL::integer AS sv_request_by,
                NULL::text AS sv_request_by_name,
                NULL::text AS sv_request_by_role,
                NULL::text AS sv_request_at,
                FALSE::boolean AS sv_request_approved,
                NULL::integer AS sv_request_approved_by,
                NULL::text AS sv_request_approved_by_name,
                NULL::text AS sv_request_approved_at,
                FALSE::boolean AS sv_request_rejected,
                NULL::integer AS sv_request_rejected_by,
                NULL::text AS sv_request_rejected_by_name,
                NULL::text AS sv_request_rejected_at,
                NULL::text AS sv_request_reject_comment,
                ic.duration_sec::numeric AS duration, -- берем из imported_calls
                TRUE AS is_imported,
                TRUE AS comment_visible_to_operator,
                NULL::integer AS feedback_id,
                NULL::text AS feedback_comment,
                NULL::text AS delivery_comment,
                NULL::date AS feedback_date,
                NULL::time AS start_time,
                NULL::time AS end_time,
                NULL::integer AS training_id,
                NULL::text AS feedback_updated_at,
                NULL::text AS feedback_created_by_name,
                NULL::text AS feedback_updated_by_name,
                FALSE::boolean AS question_resolved,
                NULL::boolean AS resolved_first_contact,
                NULL::text AS sv_request_approve_comment,
                NULL::integer AS c2d_snapshot_id,
                NULL::jsonb AS chat_quotes,
                ic.call_end_party,
                ic.id AS imported_call_id,
                NULL::integer AS survey_response_id,
                NULL::integer AS knowledge_test_survey_id,
                NULL::text AS knowledge_test_title,
                NULL::numeric AS knowledge_test_earned_points,
                NULL::numeric AS knowledge_test_max_points,
                NULL::boolean AS knowledge_test_auto_submitted
            FROM imported_calls ic
            WHERE ic.operator_id = %s AND ic.status = 'not_evaluated'
              AND """ + test_keys.sql_not_test(
                  "COALESCE(NULLIF(ic.phone_normalized, ''), ic.phone_number)") + """
        """
        params.append(operator_id)

        if month:
            query += " AND ic.month = %s"
            params.append(month)

        query += " ORDER BY created_at DESC"

        target_month = str(month or datetime.now().strftime('%Y-%m'))
        evaluation_target = None

        with self._get_cursor() as cursor:
            if include_target:
                source = self._fetch_operator_call_evaluation_target_source(cursor, operator_id, target_month)
                evaluation_target = self._build_operator_call_evaluation_target(
                    operator_id=operator_id,
                    target_month=target_month,
                    hire_date_value=source.get('hire_date_value'),
                    regular_hours=source.get('regular_hours'),
                    training_hours=source.get('training_hours'),
                    technical_issue_hours=source.get('technical_issue_hours'),
                    offline_activity_hours=source.get('offline_activity_hours'),
                    operator_norm_hours=source.get('operator_norm_hours')
                )

            cursor.execute(query, params)
            rows = cursor.fetchall()

            evaluations = []
            for row in rows:
                feedback_payload = (
                    {
                        "id": row[36],
                        "feedback_comment": row[37] or "",
                        "delivery_comment": row[38] or "",
                        "date": (
                            row[39].strftime('%Y-%m-%d')
                            if row[39] and hasattr(row[39], "strftime")
                            else (row[39] if row[39] else None)
                        ),
                        "start_time": (
                            row[40].strftime('%H:%M')
                            if row[40] and hasattr(row[40], "strftime")
                            else (row[40] if row[40] else None)
                        ),
                        "end_time": (
                            row[41].strftime('%H:%M')
                            if row[41] and hasattr(row[41], "strftime")
                            else (row[41] if row[41] else None)
                        ),
                        "training_id": row[42],
                        "updated_at": row[43],
                        "created_by_name": row[44],
                        "updated_by_name": row[45],
                    }
                    if row[36] is not None
                    else None
                )

                feedback_date_source = row[39] if row[39] is not None else None
                feedback_sla_payload = None
                if not bool(row[34]) and not bool(row[7]):
                    feedback_sla_payload = self._build_feedback_sla_payload(
                        evaluation_date_source=row[17],
                        score_value=row[4],
                        scores_payload=row[10] if row[10] else [],
                        direction_criteria_payload=row[14] if row[14] else [],
                        feedback_date_source=feedback_date_source
                    )
                if feedback_payload is not None:
                    feedback_payload["sla"] = feedback_sla_payload

                evaluations.append(
                    {
                        "id": row[0],
                        "month": row[1],
                        "phone_number": row[2],
                        "appeal_date": (
                            row[3].strftime('%Y-%m-%d %H:%M:%S')
                            if row[3] and hasattr(row[3], "strftime")
                            else (row[3] if row[3] else None)
                        ),
                        "score": float(row[4]) if row[4] is not None else None,
                        "comment": row[5],
                        "audio_path": row[6],
                        "is_draft": bool(row[7]),
                        "is_correction": bool(row[8]),
                        "evaluation_date": row[9],
                        "scores": row[10] if row[10] else [],
                        "criterion_comments": row[11] if row[11] else [],
                        "direction": {
                            "id": row[12],
                            "name": row[13],
                            "criteria": row[14] if row[14] else [],
                            "hasFileUpload": row[15] if row[15] is not None else True,
                        }
                        if row[12]
                        else None,
                        "evaluator": row[16] if row[16] else None,
                        "created_at": row[17],
                        "sv_request": bool(row[18]),
                        "sv_request_comment": row[19],
                        "sv_request_by": row[20],
                        "sv_request_by_name": row[21],
                        "sv_request_by_role": row[22],
                        "sv_request_at": row[23],
                        "sv_request_approved": bool(row[24]),
                        "sv_request_approved_by": row[25],
                        "sv_request_approved_by_name": row[26],
                        "sv_request_approved_at": row[27],
                        "sv_request_rejected": bool(row[28]),
                        "sv_request_rejected_by": row[29],
                        "sv_request_rejected_by_name": row[30],
                        "sv_request_rejected_at": row[31],
                        "sv_request_reject_comment": row[32],
                        "duration": float(row[33]) if row[33] is not None else None,
                        "is_imported": bool(row[34]),
                        "comment_visible_to_operator": bool(row[35]) if row[35] is not None else True,
                        "question_resolved": bool(row[46]) if row[46] is not None else False,
                        "resolved_first_contact": bool(row[47]) if row[47] is not None else None,
                        "sv_request_approve_comment": row[48],
                        "c2d_snapshot_id": row[49],
                        "chat_quotes": row[50] if row[50] else [],
                        "call_end_party": row[51],
                        "imported_call_id": row[52],
                        "survey_response_id": row[53],
                        # Оценка «Тестирование знаний»: не звонок, не чат —
                        # отдельный вид оценки со ссылкой на пройденный тест.
                        "knowledge_test": {
                            "survey_id": row[54],
                            "title": row[55] or '',
                            "earned_points": float(row[56]) if row[56] is not None else None,
                            "max_points": float(row[57]) if row[57] is not None else None,
                            "is_auto_submitted": bool(row[58]) if row[58] is not None else False
                        } if row[53] is not None else None,
                        "feedback": feedback_payload,
                        "feedback_sla": feedback_sla_payload
                    }
                )

            if include_target:
                return {
                    "evaluations": evaluations,
                    "evaluation_target": evaluation_target
                }

            return evaluations

    def get_checkpoints_by_feedback_ids(self, feedback_ids):
        """{id обратной связи: контрольная точка} — для чипа в «Журнале оценок».

        Отдельным запросом, а не колонками в get_call_evaluations: тот запрос —
        UNION из оценённых звонков и неоценённых импортов, и каждая новая
        колонка обязана быть добавлена в обе ветки с совпадающими типами.
        Ради поля, которое есть у меньшинства строк, это плохой обмен: здесь
        один короткий запрос по индексу uq_operator_checkpoints_feedback на всю
        страницу журнала.
        """
        ids = [int(value) for value in (feedback_ids or []) if value is not None]
        if not ids:
            return {}
        from trainings.checkpoints import fetch_by_feedback_ids
        with self._get_cursor() as cursor:
            return fetch_by_feedback_ids(cursor, ids)

    def get_call_reevaluation_requests(self, month=None, operator_id=None, supervisor_id=None, department_id=None):
        query = """
            SELECT
                c.id,
                c.month,
                c.phone_number,
                TO_CHAR(c.appeal_date, 'YYYY-MM-DD HH24:MI') AS appeal_date_text,
                c.score,
                TO_CHAR(c.created_at, 'YYYY-MM-DD HH24:MI') AS evaluation_date,
                c.sv_request,
                c.sv_request_comment,
                c.sv_request_by,
                req.name AS sv_request_by_name,
                c.sv_request_by_role,
                TO_CHAR(c.sv_request_at, 'YYYY-MM-DD HH24:MI') AS sv_request_at,
                c.sv_request_approved,
                c.sv_request_approved_by,
                apu.name AS sv_request_approved_by_name,
                TO_CHAR(c.sv_request_approved_at, 'YYYY-MM-DD HH24:MI') AS sv_request_approved_at,
                c.sv_request_rejected,
                c.sv_request_rejected_by,
                rju.name AS sv_request_rejected_by_name,
                TO_CHAR(c.sv_request_rejected_at, 'YYYY-MM-DD HH24:MI') AS sv_request_rejected_at,
                c.sv_request_reject_comment,
                c.operator_id,
                op.name AS operator_name,
                op.supervisor_id,
                sv.name AS supervisor_name,
                d.id AS direction_id,
                d.name AS direction_name,
                u.name AS evaluator_name,
                corr.id AS correction_call_id,
                corr.score AS correction_score,
                corr.evaluator_name AS correction_evaluator_name,
                TO_CHAR(corr.created_at, 'YYYY-MM-DD HH24:MI') AS correction_created_at,
                c.sv_request_approve_comment,
                c.call_end_party
            FROM calls c
            JOIN users op ON op.id = c.operator_id
            LEFT JOIN users sv ON sv.id = op.supervisor_id
            LEFT JOIN users req ON req.id = c.sv_request_by
            LEFT JOIN users apu ON apu.id = c.sv_request_approved_by
            LEFT JOIN users rju ON rju.id = c.sv_request_rejected_by
            LEFT JOIN users u ON u.id = c.evaluator_id
            LEFT JOIN directions d ON d.id = c.direction_id
            LEFT JOIN LATERAL (
                SELECT
                    cc.id,
                    cc.score,
                    cc.created_at,
                    cu.name AS evaluator_name
                FROM calls cc
                LEFT JOIN users cu ON cu.id = cc.evaluator_id
                WHERE cc.previous_version_id = c.id
                  AND COALESCE(cc.is_correction, FALSE) = TRUE
                ORDER BY cc.created_at DESC
                LIMIT 1
            ) corr ON TRUE
            WHERE c.sv_request = TRUE
        """
        params = []

        if month:
            query += " AND c.month = %s"
            params.append(month)
        if operator_id:
            query += " AND c.operator_id = %s"
            params.append(operator_id)
        if supervisor_id:
            query += " AND op.supervisor_id = %s"
            params.append(supervisor_id)
        if department_id:
            query += " AND op.department_id = %s"
            params.append(department_id)

        query += """
            ORDER BY
                CASE
                    WHEN COALESCE(c.sv_request_approved, FALSE) = FALSE AND COALESCE(c.sv_request_rejected, FALSE) = FALSE THEN 0
                    WHEN COALESCE(c.sv_request_approved, FALSE) = TRUE AND corr.id IS NULL THEN 1
                    WHEN COALESCE(c.sv_request_approved, FALSE) = TRUE AND corr.id IS NOT NULL THEN 2
                    ELSE 3
                END,
                COALESCE(c.sv_request_at, c.created_at) DESC,
                c.created_at DESC
        """

        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        requests = []
        for row in rows:
            requests.append(
                {
                    "id": row[0],
                    "month": row[1],
                    "phone_number": row[2],
                    "appeal_date": row[3],
                    "score": float(row[4]) if row[4] is not None else None,
                    "evaluation_date": row[5],
                    "sv_request": bool(row[6]),
                    "sv_request_comment": row[7],
                    "sv_request_by": row[8],
                    "sv_request_by_name": row[9],
                    "sv_request_by_role": row[10],
                    "sv_request_at": row[11],
                    "sv_request_approved": bool(row[12]),
                    "sv_request_approved_by": row[13],
                    "sv_request_approved_by_name": row[14],
                    "sv_request_approved_at": row[15],
                    "sv_request_rejected": bool(row[16]),
                    "sv_request_rejected_by": row[17],
                    "sv_request_rejected_by_name": row[18],
                    "sv_request_rejected_at": row[19],
                    "sv_request_reject_comment": row[20],
                    "operator_id": row[21],
                    "operator_name": row[22],
                    "supervisor_id": row[23],
                    "supervisor_name": row[24],
                    "direction": {
                        "id": row[25],
                        "name": row[26]
                    } if row[25] else None,
                    "evaluator": row[27],
                    "correction_call_id": row[28],
                    "correction_score": float(row[29]) if row[29] is not None else None,
                    "correction_evaluator_name": row[30],
                    "correction_created_at": row[31],
                    "sv_request_approve_comment": row[32],
                    "call_end_party": row[33]
                }
            )

        return requests

    def _has_call_critical_error(self, scores_payload, direction_criteria_payload) -> bool:
        if not isinstance(scores_payload, list) or not isinstance(direction_criteria_payload, list):
            return False

        for idx, criterion in enumerate(direction_criteria_payload):
            if not isinstance(criterion, dict):
                continue
            is_critical = bool(criterion.get('isCritical') or criterion.get('is_critical'))
            if not is_critical:
                continue

            raw_score = scores_payload[idx] if idx < len(scores_payload) else None
            score_norm = str(raw_score or '').strip().lower()
            if score_norm in ('error', 'incorrect'):
                return True
        return False

    def _build_feedback_sla_payload(
        self,
        evaluation_date_source,
        score_value,
        scores_payload,
        direction_criteria_payload,
        feedback_date_source=None,
        reference_date=None
    ) -> Optional[Dict[str, Any]]:
        evaluation_date = self._normalize_feedback_sla_date(evaluation_date_source)
        if evaluation_date is None:
            return None

        has_critical_error = self._has_call_critical_error(scores_payload, direction_criteria_payload)

        score_numeric = None
        try:
            score_numeric = float(score_value) if score_value is not None else None
        except Exception:
            score_numeric = None

        if has_critical_error:
            deadline_days = 1
        elif score_numeric is not None and score_numeric > 85:
            deadline_days = 5
        else:
            deadline_days = 3

        due_date = evaluation_date + timedelta(days=deadline_days)
        feedback_date = self._normalize_feedback_sla_date(feedback_date_source)
        report_date = self._normalize_feedback_sla_date(reference_date) or datetime.now().date()

        if feedback_date is not None:
            is_on_time = feedback_date <= due_date
            status = 'on_time' if is_on_time else 'overdue'
            overdue_days = max(0, (feedback_date - due_date).days)
        else:
            is_on_time = False
            if report_date > due_date:
                status = 'overdue'
                overdue_days = (report_date - due_date).days
            else:
                status = 'pending'
                overdue_days = 0

        return {
            "deadline_days": int(deadline_days),
            "evaluation_date": evaluation_date.isoformat(),
            "due_date": due_date.isoformat(),
            "feedback_date": feedback_date.isoformat() if feedback_date else None,
            "status": status,
            "is_overdue": bool(status == 'overdue'),
            "is_on_time": bool(status == 'on_time'),
            "overdue_days": int(overdue_days),
            "has_critical_error": bool(has_critical_error)
        }

    def get_feedback_sla_rows_for_month(
        self,
        month: str,
        operator_ids: Optional[List[int]] = None,
        supervisor_ids: Optional[List[int]] = None,
        department_ids: Optional[List[int]] = None,
        reference_date=None
    ) -> List[Dict[str, Any]]:
        if not month:
            return []

        try:
            datetime.strptime(str(month), '%Y-%m')
        except Exception:
            return []

        def _normalize_ids(values):
            if values is None:
                return None
            normalized = []
            seen = set()
            for value in values:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed in seen:
                    continue
                seen.add(parsed)
                normalized.append(parsed)
            return normalized

        operator_ids_norm = _normalize_ids(operator_ids)
        supervisor_ids_norm = _normalize_ids(supervisor_ids)
        department_ids_norm = _normalize_ids(department_ids)

        if operator_ids is not None and not operator_ids_norm:
            return []
        if supervisor_ids is not None and not supervisor_ids_norm:
            return []
        if department_ids is not None and not department_ids_norm:
            return []

        params: List[Any] = [month, month]
        where_clauses = [
            "LOWER(COALESCE(op.role, '')) IN ('operator', 'trainee')"
        ]

        if operator_ids_norm is not None:
            where_clauses.append("c.operator_id = ANY(%s)")
            params.append(operator_ids_norm)
        if supervisor_ids_norm is not None:
            where_clauses.append("op.supervisor_id = ANY(%s)")
            params.append(supervisor_ids_norm)
        if department_ids_norm is not None:
            where_clauses.append("op.department_id = ANY(%s)")
            params.append(department_ids_norm)

        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        # Оценки разговоров с номерами «Реестра тестовых номеров» (test_numbers) в счёт
        # и средний балл оператора не входят: сотрудник проверял линию, а не работал.
        from test_numbers import keys as test_keys
        not_test_calls = test_keys.sql_calls_not_test()
        query = f"""
            WITH latest_versions AS (
                SELECT
                    operator_id,
                    phone_number,
                    month,
                    appeal_date,
                    MAX(created_at) AS latest_date
                FROM calls
                WHERE month = %s
                  AND is_draft = FALSE
                  AND score IS NOT NULL
                  AND {not_test_calls}
                GROUP BY operator_id, phone_number, month, appeal_date
            ),
            latest_calls AS (
                SELECT c.*
                FROM calls c
                JOIN latest_versions lv
                  ON c.operator_id = lv.operator_id
                 AND c.phone_number = lv.phone_number
                 AND c.month = lv.month
                 AND (
                     (c.appeal_date IS NULL AND lv.appeal_date IS NULL)
                     OR c.appeal_date = lv.appeal_date
                 )
                 AND c.created_at = lv.latest_date
                WHERE c.month = %s
                  AND c.is_draft = FALSE
                  AND c.score IS NOT NULL
            )
            SELECT
                c.id AS call_id,
                c.operator_id,
                op.name AS operator_name,
                op.supervisor_id,
                sv.name AS supervisor_name,
                c.score,
                c.scores,
                d.criteria AS direction_criteria,
                c.created_at AS evaluation_created_at,
                cf.id AS feedback_id,
                cf.feedback_date,
                cf.created_at AS feedback_created_at,
                cf.supervisor_id AS feedback_supervisor_id,
                cf_sv.name AS feedback_supervisor_name
            FROM latest_calls c
            JOIN users op ON op.id = c.operator_id
            LEFT JOIN users sv ON sv.id = op.supervisor_id
            LEFT JOIN directions d ON d.id = c.direction_id
            LEFT JOIN call_feedbacks cf ON cf.call_id = c.id
            LEFT JOIN users cf_sv ON cf_sv.id = cf.supervisor_id
            WHERE {where_sql}
            ORDER BY COALESCE(sv.name, ''), op.name, c.created_at
        """

        with self._get_cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

        result: List[Dict[str, Any]] = []
        for row in rows:
            evaluation_created_at = row[8]
            feedback_exists = row[9] is not None

            feedback_date_value = row[10]
            if feedback_date_value is None and row[11] is not None and hasattr(row[11], 'date'):
                feedback_date_value = row[11].date()

            sla_payload = self._build_feedback_sla_payload(
                evaluation_date_source=evaluation_created_at,
                score_value=row[5],
                scores_payload=row[6],
                direction_criteria_payload=row[7],
                feedback_date_source=feedback_date_value,
                reference_date=reference_date
            ) or {}

            try:
                score_numeric = float(row[5]) if row[5] is not None else None
            except Exception:
                score_numeric = None

            result.append({
                "call_id": int(row[0]),
                "operator_id": int(row[1]),
                "operator_name": row[2] or "",
                "supervisor_id": int(row[3]) if row[3] is not None else None,
                "supervisor_name": row[4] or "Без супервайзера",
                "score": score_numeric,
                "feedback_provided": bool(feedback_exists),
                "feedback_supervisor_id": int(row[12]) if row[12] is not None else None,
                "feedback_supervisor_name": row[13] or None,
                "status": sla_payload.get("status"),
                "is_overdue": bool(sla_payload.get("is_overdue")),
                "is_on_time": bool(sla_payload.get("is_on_time")),
                "overdue_days": int(sla_payload.get("overdue_days") or 0),
                "deadline_days": int(sla_payload.get("deadline_days") or 0),
                "evaluation_date": sla_payload.get("evaluation_date"),
                "due_date": sla_payload.get("due_date"),
                "feedback_date": sla_payload.get("feedback_date"),
                "has_critical_error": bool(sla_payload.get("has_critical_error")),
                "sla": sla_payload
            })

        return result

    def get_week_call_stats(self, operator_id, start_date, end_date):
        # Оценки разговоров с номерами «Реестра тестовых номеров» (test_numbers) не считаются.
        from test_numbers import keys as test_keys
        with self._get_cursor() as cursor:
            query = """
                WITH latest_versions AS (
                    SELECT 
                        operator_id,
                        phone_number,
                        month,
                        appeal_date,
                        MAX(created_at) as latest_date
                    FROM calls
                    WHERE operator_id = %s
                    AND created_at >= %s
                    AND created_at <= %s
                    AND is_draft = FALSE
                    AND """ + test_keys.sql_calls_not_test() + """
                    GROUP BY operator_id, phone_number, month, appeal_date
                ),
                latest_calls AS (
                    SELECT
                        c.id,
                        c.score,
                        c.survey_response_id
                    FROM calls c
                    JOIN latest_versions lv ON
                        c.operator_id = lv.operator_id AND
                        c.phone_number = lv.phone_number AND
                        c.month = lv.month AND
                        (
                            (c.appeal_date IS NULL AND lv.appeal_date IS NULL)
                            OR c.appeal_date = lv.appeal_date
                        ) AND
                        c.created_at = lv.latest_date
                    WHERE c.operator_id = %s
                    AND c.is_draft = FALSE
                )
                SELECT COUNT(*) FILTER (WHERE survey_response_id IS NULL), AVG(score)::float
                FROM latest_calls
            """
            cursor.execute(query, (operator_id, start_date, end_date, operator_id))
            result = cursor.fetchone()
            count = result[0] or 0
            avg = result[1] or 0.0
            return count, avg

    def get_average_scores_for_period(self, start_date, end_date, operator_ids: Optional[List[int]] = None):
        """
        Возвращает среднюю оценку и количество оценок по операторам за выбранный период.

        Параметры:
        - start_date: начало периода (datetime или строка в формате SQL-совместимом, напр. 'YYYY-MM-DD' или full timestamp)
        - end_date: конец периода (inclusive)
        - operator_ids: опциональный список id операторов для фильтрации (если None — по всем операторам)

        Возвращает словарь:
        {
            "operators": { operator_id: {"count": int, "avg_score": float}, ... },
            "overall": {"count": total_count, "avg_score": overall_avg}
        }

        Реализация основана на логике последних версий оценок
        (по phone_number/operator_id/month/appeal_date)
        аналогично `get_week_call_stats`, но аггрегирует по операторам за период.
        """
        # normalize dates if strings were provided (leave as-is otherwise)
        sd = start_date
        ed = end_date
        params = [sd, ed]
        # Оценки разговоров с номерами «Реестра тестовых номеров» (test_numbers) не считаются.
        from test_numbers import keys as test_keys

        query = """
            WITH latest_versions AS (
                SELECT phone_number, operator_id, month, appeal_date, MAX(created_at) AS latest_date
                FROM calls
                WHERE is_draft = FALSE
                  AND created_at >= %s AND created_at <= %s
                  AND """ + test_keys.sql_calls_not_test() + """
                GROUP BY phone_number, operator_id, month, appeal_date
            )
            SELECT
                c.operator_id,
                COUNT(*) FILTER (WHERE c.survey_response_id IS NULL) AS cnt,
                AVG(c.score)::float AS avg_score
            FROM calls c
            JOIN latest_versions lv
              ON c.phone_number = lv.phone_number
             AND c.operator_id = lv.operator_id
             AND c.month = lv.month
             AND (
                 (c.appeal_date IS NULL AND lv.appeal_date IS NULL)
                 OR c.appeal_date = lv.appeal_date
             )
             AND c.created_at = lv.latest_date
        """

        if operator_ids:
            query += "\nWHERE c.operator_id = ANY(%s)"
            params.append(operator_ids)

        query += "\nGROUP BY c.operator_id"

        with self._get_cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

        operators = {}
        total_count = 0
        total_weighted = 0.0
        for op_id, cnt, avg in rows:
            c = int(cnt or 0)
            a = float(avg) if avg is not None else 0.0
            operators[int(op_id)] = {"count": c, "avg_score": round(a, 2)}
            total_count += c
            total_weighted += a * c

        overall_avg = round((total_weighted / total_count), 2) if total_count > 0 else 0.0

        return {"operators": operators, "overall": {"count": total_count, "avg_score": overall_avg}}

    @classmethod
    def knowledge_test_evaluation_label(cls, survey_title):
        title = str(survey_title or '').strip() or 'без названия'
        return (cls.KNOWLEDGE_TEST_EVALUATION_PREFIX + title)[:255]

    def _upsert_knowledge_test_evaluation_tx(self, cursor, *, survey_id, survey_title, survey_created_by,
                                            operator_id, response_id, score_percent, touch_timestamps=True):
        """
        Результат теста попадает в журнал оценок отдельной записью: она входит
        в средний показатель качества, но не считается прослушанным звонком
        (см. survey_response_id в подсчётах call_count).
        """
        cursor.execute("SELECT direction_id, supervisor_id FROM users WHERE id = %s", (operator_id,))
        operator_row = cursor.fetchone()
        direction_id = operator_row[0] if operator_row else None
        supervisor_id = operator_row[1] if operator_row else None

        evaluator_id = survey_created_by if survey_created_by is not None else supervisor_id
        if evaluator_id is None:
            # calls.evaluator_id обязателен: без автора теста запись создать
            # нельзя, но саму попытку это ломать не должно.
            logging.warning(
                "Knowledge test %s: no evaluator for operator %s, quality evaluation skipped",
                survey_id, operator_id
            )
            return None

        score_value = max(0.0, min(100.0, round(float(score_percent or 0.0), 2)))
        label = self.knowledge_test_evaluation_label(survey_title)

        cursor.execute("SELECT id FROM calls WHERE survey_response_id = %s", (response_id,))
        existing = cursor.fetchone()
        if existing:
            call_id = int(existing[0])
            if touch_timestamps:
                # Новая попытка — оценка датируется ею.
                cursor.execute("""
                    UPDATE calls
                    SET
                        evaluator_id = %s,
                        phone_number = %s,
                        appeal_date = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                        month = TO_CHAR((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'), 'YYYY-MM'),
                        score = %s,
                        direction_id = %s,
                        created_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE id = %s
                """, (evaluator_id, label, score_value, direction_id, call_id))
            else:
                # Пересчёт по изменённым правилам: балл новый, дата прохождения
                # прежняя — иначе оценка переехала бы в другой месяц.
                cursor.execute("""
                    UPDATE calls
                    SET
                        phone_number = %s,
                        score = %s
                    WHERE id = %s
                """, (label, score_value, call_id))
            return call_id

        cursor.execute("""
            INSERT INTO calls (
                evaluator_id,
                operator_id,
                month,
                phone_number,
                appeal_date,
                score,
                direction_id,
                is_draft,
                survey_response_id,
                created_at
            )
            VALUES (
                %s,
                %s,
                TO_CHAR((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'), 'YYYY-MM'),
                %s,
                (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                %s,
                %s,
                FALSE,
                %s,
                (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
            )
            RETURNING id
        """, (evaluator_id, operator_id, label, score_value, direction_id, response_id))
        return int(cursor.fetchone()[0])

    def update_cdr_call_end_parties(self):
        """Кто положил трубку — звонкам отдела продаж (FreePBX) в пуле и в оценках.

        Звонок ОП ложится в пул (imported_calls: external_id — linkedid станции, notes
        оканчиваются на ':cdr', телефон — '7' и десять цифр касания) со стороной, которую
        касание знало в ту минуту. Исходящим её до моста 1.6.0 не знал никто, и такие звонки
        лежат с 'unknown'. Здесь сторона из cdr_touches доезжает до пула и до уже сделанных
        оценок (calls), как у Oktell и Binotel в update_imported_call_end_parties. Известную
        сторону не переписываем.

        Проход общий, а не по присланным суткам: касание ищется по первичному ключу, а звонок,
        который не обновился из-за сбоя, подберёт любая следующая присылка суток. Ключ —
        (linkedid, телефон), то есть ровно то касание, что легло в пул. Возвращает число
        обновлённых звонков пула."""
        with self._get_cursor() as cur:
            cur.execute(
                """
                WITH known AS (
                    SELECT ic.id, t.hangup_side
                      FROM imported_calls AS ic
                      JOIN cdr_touches AS t
                        ON t.linkedid = ic.external_id
                       AND t.phone = right(ic.phone_normalized, 10)
                     WHERE ic.notes LIKE %(cdr)s
                       AND (ic.call_end_party IS NULL OR ic.call_end_party = 'unknown')
                       AND t.hangup_side IN ('client', 'operator')
                )
                UPDATE imported_calls AS ic
                   SET call_end_party = known.hangup_side
                  FROM known
                 WHERE ic.id = known.id
                   AND (ic.call_end_party IS NULL OR ic.call_end_party = 'unknown')
                RETURNING ic.id
                """,
                {'cdr': '%:cdr'}
            )
            updated_ids = [row[0] for row in cur.fetchall()]
            if updated_ids:
                cur.execute(
                    """
                    UPDATE calls AS c
                    SET call_end_party = ic.call_end_party
                    FROM imported_calls AS ic
                    WHERE c.imported_call_id = ic.id
                      AND c.imported_call_id = ANY(%s)
                      AND (c.call_end_party IS NULL OR c.call_end_party = 'unknown')
                    """,
                    (updated_ids,)
                )
        return len(updated_ids)
