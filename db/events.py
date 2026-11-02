"""База данных: события, дни рождения, 4 You.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _coerce_four_you_annotations,
    datetime,
    json,
)


class EventsMixin:
    def get_birthdays_for_date(self, day_value, department_id=None,
                               include_user_id=None, all_departments=False):
        """Активные сотрудники, у которых в этот день (месяц/число) день рождения.

        Периметр обязателен и по умолчанию ПУСТОЙ: дата рождения — личные
        данные, и сотрудник чужого отдела о ней знать не должен. Кто где —
        решает вызывающий (bot_schedule2::birthdays_today), здесь только
        исполняется:

            all_departments=True   — весь портал (глобальные админы);
            department_id=<id>     — один отдел;
            include_user_id=<id>   — плюс сам зритель, что бы ни было выше.

        Умолчание «ничего» выбрано намеренно: новый вызов без периметра вернёт
        пустой список, а не всех сотрудников компании.
        """
        day = None
        if isinstance(day_value, str):
            try:
                day = datetime.strptime(day_value, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("Invalid date format. Use YYYY-MM-DD")
        elif isinstance(day_value, datetime):
            day = day_value.date()
        else:
            day = day_value

        if not day:
            return []

        if all_departments:
            scope = ""
        elif department_id is not None and include_user_id is not None:
            scope = " AND (u.department_id = %(dept)s OR u.id = %(self_id)s)"
        elif department_id is not None:
            scope = " AND u.department_id = %(dept)s"
        elif include_user_id is not None:
            scope = " AND u.id = %(self_id)s"
        else:
            return []

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    u.id,
                    u.name,
                    u.role,
                    d.name AS direction,
                    u.status,
                    u.birth_date
                FROM users u
                LEFT JOIN directions d ON u.direction_id = d.id
                WHERE u.birth_date IS NOT NULL
                  AND EXTRACT(MONTH FROM u.birth_date) = %(month)s
                  AND EXTRACT(DAY FROM u.birth_date) = %(day)s
                  AND (u.status IS NULL OR u.status NOT IN ('fired', 'dismissal'))
            """ + scope, {'month': day.month, 'day': day.day,
                          'dept': department_id, 'self_id': include_user_id})
            rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "name": row[1],
                "role": row[2],
                "direction": row[3],
                "status": row[4],
                "birth_date": row[5].strftime('%Y-%m-%d') if row[5] else None
            }
            for row in rows
        ]

    def list_session_access_events(self, session_id, limit: int = 50):
        """История выдачи доступа по сессии, свежие сверху."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    e.id,
                    e.action,
                    e.actor_id,
                    a.name AS actor_name,
                    COALESCE(e.actor_role, a.role) AS actor_role,
                    e.ip_address,
                    e.user_agent,
                    e.created_at
                FROM session_access_events e
                LEFT JOIN users a ON a.id = e.actor_id
                WHERE e.session_id = %s
                ORDER BY e.created_at DESC, e.id DESC
                LIMIT %s
            """, (session_id, max(1, int(limit))))
            return [
                {
                    "id": row[0],
                    "action": row[1],
                    "actor_id": row[2],
                    "actor_name": row[3],
                    "actor_role": row[4],
                    "ip_address": row[5],
                    "user_agent": row[6],
                    "created_at": row[7]
                }
                for row in cursor.fetchall()
            ]

    def get_ai_birthday_greeting_cache(self, user_id: int, date_key: str):
        """Получить кэшированное AI-поздравление с днем рождения"""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT greeting_data, created_at, updated_at
                FROM ai_birthday_greeting_cache
                WHERE user_id = %s AND greeting_date = %s
            """, (user_id, date_key))
            result = cursor.fetchone()
            if result:
                return {
                    'greeting_data': result[0],
                    'created_at': result[1],
                    'updated_at': result[2]
                }
            return None

    def list_four_you_images(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    id,
                    preview_bucket,
                    preview_blob_path,
                    display_bucket,
                    display_blob_path,
                    original_name,
                    width,
                    height,
                    preview_file_size,
                    display_file_size,
                    uploaded_by,
                    sort_order,
                    created_at,
                    annotations,
                    annotations_updated_at
                FROM four_you_images
                ORDER BY sort_order ASC, created_at ASC, id ASC
            """)
            return [
                {
                    "id": str(row[0]),
                    "preview_bucket": row[1],
                    "preview_blob_path": row[2],
                    "display_bucket": row[3],
                    "display_blob_path": row[4],
                    "original_name": row[5],
                    "width": int(row[6] or 0),
                    "height": int(row[7] or 0),
                    "preview_file_size": int(row[8] or 0),
                    "display_file_size": int(row[9] or 0),
                    "uploaded_by": int(row[10]),
                    "sort_order": int(row[11] or 0),
                    "created_at": row[12].isoformat() if row[12] else None,
                    "annotations": _coerce_four_you_annotations(row[13]),
                    "annotations_updated_at": row[14].isoformat() if row[14] else None,
                }
                for row in cursor.fetchall()
            ]

    def mark_four_you_seen(self, user_id):
        """Отметить, что пользователь открыл 4 You (гасит бейдж новых фото)."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO four_you_reads (user_id, last_seen_at)
                VALUES (%s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id)
                DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at
            """, (int(user_id),))

    def count_unread_four_you_images(self, user_id):
        """Кол-во фото, добавленных позже last_seen зрителя (свои загрузки не считаем)."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT last_seen_at FROM four_you_reads WHERE user_id = %s", (int(user_id),))
            r = cursor.fetchone()
            last_seen = r[0] if r else None
            conditions = ["uploaded_by IS DISTINCT FROM %s"]
            params = [int(user_id)]
            if last_seen is not None:
                conditions.append("created_at > %s")
                params.append(last_seen)
            cursor.execute(
                f"SELECT COUNT(*) FROM four_you_images WHERE {' AND '.join(conditions)}",
                tuple(params),
            )
            return int(cursor.fetchone()[0] or 0)

    def set_four_you_annotations(self, image_id: str, data: dict):
        """Сохранить JSONB-разметку фото. Возвращает (annotations, annotations_updated_at) или None."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE four_you_images
                SET annotations = %s::jsonb,
                    annotations_updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING annotations, annotations_updated_at
            """, (json.dumps(data or {}, ensure_ascii=False), str(image_id)))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "annotations": _coerce_four_you_annotations(row[0]),
                "annotations_updated_at": row[1].isoformat() if row[1] else None,
            }

    def list_four_you_annotations_changed_since(self, since_iso):
        """Лёгкий поллинг: разметка, изменившаяся после момента since_iso (ISO-строка или None)."""
        with self._get_cursor() as cursor:
            if since_iso:
                cursor.execute("""
                    SELECT id, annotations, annotations_updated_at
                    FROM four_you_images
                    WHERE annotations_updated_at > %s::timestamp
                    ORDER BY annotations_updated_at ASC
                """, (since_iso,))
            else:
                cursor.execute("""
                    SELECT id, annotations, annotations_updated_at
                    FROM four_you_images
                    ORDER BY annotations_updated_at ASC
                """)
            return [
                {
                    "id": str(row[0]),
                    "annotations": _coerce_four_you_annotations(row[1]),
                    "annotations_updated_at": row[2].isoformat() if row[2] else None,
                }
                for row in cursor.fetchall()
            ]

    def create_four_you_image(self, image_data: dict):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO four_you_images (
                    preview_bucket,
                    preview_blob_path,
                    display_bucket,
                    display_blob_path,
                    original_name,
                    width,
                    height,
                    preview_file_size,
                    display_file_size,
                    uploaded_by,
                    sort_order
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                image_data["preview_bucket"],
                image_data["preview_blob_path"],
                image_data["display_bucket"],
                image_data["display_blob_path"],
                image_data["original_name"],
                int(image_data["width"]),
                int(image_data["height"]),
                int(image_data.get("preview_file_size") or 0),
                int(image_data.get("display_file_size") or 0),
                int(image_data["uploaded_by"]),
                int(image_data.get("sort_order") or 0),
            ))
            row = cursor.fetchone()
            return str(row[0]) if row else None

    def delete_four_you_image(self, image_id: str):
        with self._get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM four_you_images
                WHERE id = %s
                RETURNING
                    id,
                    preview_bucket,
                    preview_blob_path,
                    display_bucket,
                    display_blob_path
            """, (str(image_id),))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": str(row[0]),
                "preview_bucket": row[1],
                "preview_blob_path": row[2],
                "display_bucket": row[3],
                "display_blob_path": row[4],
            }

    def delete_four_you_images(self, image_ids):
        ids = [str(image_id) for image_id in (image_ids or []) if image_id]
        if not ids:
            return []
        with self._get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM four_you_images
                WHERE id = ANY(%s::uuid[])
                RETURNING
                    id,
                    preview_bucket,
                    preview_blob_path,
                    display_bucket,
                    display_blob_path
            """, (ids,))
            return [
                {
                    "id": str(row[0]),
                    "preview_bucket": row[1],
                    "preview_blob_path": row[2],
                    "display_bucket": row[3],
                    "display_blob_path": row[4],
                }
                for row in cursor.fetchall()
            ]

    # ──────────────────────────────────────────────────────────────────
    # ИВЕНТЫ (раздел «Ивенты»): посты с медиа, лайки, комментарии, бейдж.
    # Buckets/blob_path хранятся в таблицах; signed URL строит роут-слой.
    # ──────────────────────────────────────────────────────────────────
    def _event_row_basic(self, row):
        """row: id, author_id, author_name, avatar_bucket, avatar_blob_path,
        legacy_department_id, legacy_department_name, title, body, created_at,
        is_pinned. Отделы догружаются в _attach_event_aggregates."""
        return {
            "id": int(row[0]),
            "author_id": int(row[1]) if row[1] is not None else None,
            "author_name": row[2] or "Пользователь",
            "author_avatar_bucket": row[3],
            "author_avatar_blob_path": row[4],
            "_legacy_department_id": int(row[5]) if row[5] is not None else None,
            "_legacy_department_name": row[6],
            "title": row[7],
            "body": row[8] or "",
            "created_at": row[9].isoformat() if row[9] else None,
            "is_pinned": bool(row[10]) if len(row) > 10 else False,
            "department_ids": [],
            "department_names": [],
            "media": [],
            "like_count": 0,
            "comment_count": 0,
            "liked": False,
        }

    def _attach_event_aggregates(self, cursor, events, viewer_id=None):
        """Догружает отделы-получатели, медиа, счётчики лайков/комментов и факт
        лайка зрителя для выбранной страницы постов — батч-запросами (без N+1)."""
        by_id = {ev["id"]: ev for ev in events}
        event_ids = list(by_id.keys())
        if not event_ids:
            return
        # Отделы-получатели (M:N). Пустой набор => пост виден всем.
        cursor.execute("""
            SELECT ed.event_id, ed.department_id, d.name
            FROM event_departments ed
            LEFT JOIN departments d ON d.id = ed.department_id
            WHERE ed.event_id = ANY(%s::bigint[])
            ORDER BY ed.event_id, d.name
        """, (event_ids,))
        for r in cursor.fetchall():
            ev = by_id.get(r[0])
            if ev:
                ev["department_ids"].append(int(r[1]))
                ev["department_names"].append(r[2] or "Отдел")
        for ev in events:
            if not ev["department_ids"] and ev.get("_legacy_department_id") is not None:
                ev["department_ids"].append(int(ev["_legacy_department_id"]))
                ev["department_names"].append(ev.get("_legacy_department_name") or "Отдел")
        cursor.execute("""
            SELECT event_id, id, media_type, bucket, blob_path,
                   preview_bucket, preview_blob_path, mime_type,
                   width, height, duration_seconds, sort_order
            FROM event_media
            WHERE event_id = ANY(%s::bigint[])
            ORDER BY event_id, sort_order, id
        """, (event_ids,))
        for r in cursor.fetchall():
            ev = by_id.get(r[0])
            if not ev:
                continue
            ev["media"].append({
                "id": int(r[1]),
                "type": r[2],
                "bucket": r[3],
                "blob_path": r[4],
                "preview_bucket": r[5],
                "preview_blob_path": r[6],
                "mime_type": r[7],
                "width": int(r[8] or 0),
                "height": int(r[9] or 0),
                "duration_seconds": float(r[10]) if r[10] is not None else None,
                "sort_order": int(r[11] or 0),
            })
        cursor.execute("""
            SELECT event_id, COUNT(*) FROM event_likes
            WHERE event_id = ANY(%s::bigint[]) GROUP BY event_id
        """, (event_ids,))
        for r in cursor.fetchall():
            ev = by_id.get(r[0])
            if ev:
                ev["like_count"] = int(r[1] or 0)
        if viewer_id:
            cursor.execute("""
                SELECT event_id FROM event_likes
                WHERE user_id = %s AND event_id = ANY(%s::bigint[])
            """, (viewer_id, event_ids))
            for r in cursor.fetchall():
                ev = by_id.get(r[0])
                if ev:
                    ev["liked"] = True
        cursor.execute("""
            SELECT event_id, COUNT(*) FROM event_comments
            WHERE event_id = ANY(%s::bigint[]) GROUP BY event_id
        """, (event_ids,))
        for r in cursor.fetchall():
            ev = by_id.get(r[0])
            if ev:
                ev["comment_count"] = int(r[1] or 0)

    def list_events(self, viewer_id, viewer_department_id=None, is_global=False,
                    before_id=None, limit=12):
        """Лента постов, видимых зрителю, id-курсором (новые сверху).
        Возвращает {events, pinned, has_more, next_before}.

        Закреплённые идут отдельным списком и ТОЛЬКО к первой странице. Всплыви
        они внутри общей выдачи — сломался бы курсор: лента листается условием
        e.id < before_id, и пост, поднятый наверх вопреки порядку id, либо
        продублировался бы на следующей странице, либо выпал из неё совсем.
        Из постраничной выдачи они поэтому исключены (иначе задвоились бы на
        первом же экране)."""
        limit = max(1, min(int(limit or 12), 50))

        # Условие видимости одно на оба запроса — собираем его отдельно, чтобы
        # закреплённые и обычные посты не могли разъехаться в правах.
        visible, visible_params = "TRUE", []
        if not is_global:
            # Виден, если у поста нет привязки к отделам (для всех) ИЛИ отдел
            # зрителя есть среди получателей. Legacy fallback по e.department_id
            # оставлен, чтобы старые посты не пропали, если бэкфилл ещё не прошёл.
            visible = """(
                (
                    NOT EXISTS (SELECT 1 FROM event_departments ed WHERE ed.event_id = e.id)
                    AND (e.department_id IS NULL OR e.department_id = %s)
                )
                OR EXISTS (SELECT 1 FROM event_departments ed WHERE ed.event_id = e.id AND ed.department_id = %s)
            )"""
            visible_params = [viewer_department_id, viewer_department_id]

        conditions = ["NOT e.is_pinned", visible]
        params = list(visible_params)
        if before_id:
            conditions.append("e.id < %s")
            params.append(int(before_id))
        where = "WHERE " + " AND ".join(conditions)
        columns = """e.id, e.author_id, u.name, u.avatar_bucket, u.avatar_blob_path,
                     e.department_id, d.name, e.title, e.body, e.created_at, e.is_pinned"""
        # Потолок на закреплённые. Их число ничем не ограничено, а идут они все
        # на первую страницу — и каждому медиа при отдаче подписывается URL.
        # Закрепить два десятка постов это уже не закрепление, а вторая лента;
        # лучше показать первые и не растить первый экран без предела.
        PINNED_LIMIT = 20
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {columns}
                FROM events e
                LEFT JOIN users u ON u.id = e.author_id
                LEFT JOIN departments d ON d.id = e.department_id
                {where}
                ORDER BY e.id DESC
                LIMIT %s
            """, (*params, limit + 1))
            rows = cursor.fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            events = [self._event_row_basic(r) for r in rows]

            # Закреплённые — только к первой странице (before_id ещё нет).
            pinned = []
            if not before_id:
                cursor.execute(f"""
                    SELECT {columns}
                    FROM events e
                    LEFT JOIN users u ON u.id = e.author_id
                    LEFT JOIN departments d ON d.id = e.department_id
                    WHERE e.is_pinned AND {visible}
                    ORDER BY e.id DESC
                    LIMIT {PINNED_LIMIT}
                """, tuple(visible_params))
                pinned = [self._event_row_basic(r) for r in cursor.fetchall()]

            # Один батч на обе выдачи: агрегаты грузятся без N+1 и без второго
            # круга запросов ради закреплённых.
            self._attach_event_aggregates(cursor, events + pinned, viewer_id)
        next_before = events[-1]["id"] if (events and has_more) else None
        return {"events": events, "pinned": pinned,
                "has_more": has_more, "next_before": next_before}

    def get_event(self, event_id, viewer_id=None):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT e.id, e.author_id, u.name, u.avatar_bucket, u.avatar_blob_path,
                       e.department_id, d.name, e.title, e.body, e.created_at
                FROM events e
                LEFT JOIN users u ON u.id = e.author_id
                LEFT JOIN departments d ON d.id = e.department_id
                WHERE e.id = %s
            """, (int(event_id),))
            row = cursor.fetchone()
            if not row:
                return None
            ev = self._event_row_basic(row)
            self._attach_event_aggregates(cursor, [ev], viewer_id)
            return ev

    def get_event_author_and_departments(self, event_id):
        """(author_id, [department_id, ...]) или None. Пустой список => виден всем."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT author_id, department_id FROM events WHERE id = %s", (int(event_id),))
            row = cursor.fetchone()
            if not row:
                return None
            author_id = int(row[0]) if row[0] is not None else None
            legacy_department_id = int(row[1]) if row[1] is not None else None
            cursor.execute(
                "SELECT department_id FROM event_departments WHERE event_id = %s",
                (int(event_id),),
            )
            dept_ids = [int(r[0]) for r in cursor.fetchall()]
            if not dept_ids and legacy_department_id is not None:
                dept_ids = [legacy_department_id]
            return (author_id, dept_ids)

    def create_event(self, author_id, title, body, department_ids=None):
        """Создаёт пост и привязывает его к отделам (пустой список => виден всем)."""
        dept_ids = []
        for d in (department_ids or []):
            try:
                d = int(d)
            except (TypeError, ValueError):
                continue
            if d not in dept_ids:
                dept_ids.append(d)
        legacy_department_id = dept_ids[0] if len(dept_ids) == 1 else None
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO events (author_id, department_id, title, body)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (
                int(author_id) if author_id is not None else None,
                legacy_department_id,
                (title or None),
                (body or ""),
            ))
            event_id = int(cursor.fetchone()[0])
            for d in dept_ids:
                cursor.execute(
                    "INSERT INTO event_departments (event_id, department_id) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (event_id, d),
                )
            return event_id

    def add_event_media(self, event_id, media):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO event_media (
                    event_id, media_type, bucket, blob_path,
                    preview_bucket, preview_blob_path, mime_type,
                    width, height, file_size, duration_seconds, sort_order
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (
                int(event_id),
                media["media_type"],
                media["bucket"],
                media["blob_path"],
                media.get("preview_bucket"),
                media.get("preview_blob_path"),
                media.get("mime_type"),
                int(media.get("width") or 0),
                int(media.get("height") or 0),
                int(media.get("file_size") or 0),
                media.get("duration_seconds"),
                int(media.get("sort_order") or 0),
            ))
            return int(cursor.fetchone()[0])

    def set_event_pinned(self, event_id, pinned):
        """Закрепить/открепить пост. Возвращает новое значение либо None."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE events
                   SET is_pinned = %s,
                       updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                 WHERE id = %s
             RETURNING is_pinned
            """, (bool(pinned), int(event_id)))
            row = cursor.fetchone()
        return bool(row[0]) if row else None

    def delete_event(self, event_id):
        """Удаляет пост (каскадом медиа/лайки/комменты). Возвращает список
        (bucket, blob_path) для удаления блобов из GCS, либо None если поста нет."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT bucket, blob_path, preview_bucket, preview_blob_path
                FROM event_media WHERE event_id = %s
            """, (int(event_id),))
            blobs = []
            for r in cursor.fetchall():
                if r[0] and r[1]:
                    blobs.append((r[0], r[1]))
                if r[2] and r[3]:
                    blobs.append((r[2], r[3]))
            cursor.execute("DELETE FROM events WHERE id = %s RETURNING id", (int(event_id),))
            return blobs if cursor.fetchone() else None

    def toggle_event_like(self, event_id, user_id):
        """Переключает лайк зрителя. Возвращает {liked, like_count}."""
        with self._get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM event_likes WHERE event_id = %s AND user_id = %s RETURNING event_id",
                (int(event_id), int(user_id)),
            )
            if cursor.fetchone():
                liked = False
            else:
                cursor.execute(
                    "INSERT INTO event_likes (event_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (int(event_id), int(user_id)),
                )
                liked = True
            cursor.execute("SELECT COUNT(*) FROM event_likes WHERE event_id = %s", (int(event_id),))
            return {"liked": liked, "like_count": int(cursor.fetchone()[0] or 0)}

    def add_event_comment(self, event_id, user_id, body):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO event_comments (event_id, user_id, body)
                VALUES (%s, %s, %s)
                RETURNING id, created_at
            """, (int(event_id), int(user_id), body))
            row = cursor.fetchone()
            cursor.execute(
                "SELECT name, avatar_bucket, avatar_blob_path FROM users WHERE id = %s",
                (int(user_id),),
            )
            u = cursor.fetchone()
            return {
                "id": int(row[0]),
                "event_id": int(event_id),
                "user_id": int(user_id),
                "user_name": (u[0] if u else None) or "Пользователь",
                "author_avatar_bucket": u[1] if u else None,
                "author_avatar_blob_path": u[2] if u else None,
                "body": body,
                "created_at": row[1].isoformat() if row[1] else None,
            }

    def list_event_comments(self, event_id, before_id=None, limit=20):
        """Комментарии поста, новые сверху, id-курсором. {comments, has_more, next_before}."""
        limit = max(1, min(int(limit or 20), 50))
        conditions = ["c.event_id = %s"]
        params = [int(event_id)]
        if before_id:
            conditions.append("c.id < %s")
            params.append(int(before_id))
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT c.id, c.user_id, u.name, u.avatar_bucket, u.avatar_blob_path,
                       c.body, c.created_at
                FROM event_comments c
                LEFT JOIN users u ON u.id = c.user_id
                WHERE {' AND '.join(conditions)}
                ORDER BY c.id DESC
                LIMIT %s
            """, (*params, limit + 1))
            rows = cursor.fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        comments = [{
            "id": int(r[0]),
            "user_id": int(r[1]) if r[1] is not None else None,
            "user_name": r[2] or "Пользователь",
            "author_avatar_bucket": r[3],
            "author_avatar_blob_path": r[4],
            "body": r[5] or "",
            "created_at": r[6].isoformat() if r[6] else None,
        } for r in rows]
        next_before = comments[-1]["id"] if (comments and has_more) else None
        return {"comments": comments, "has_more": has_more, "next_before": next_before}

    def get_event_comment(self, comment_id):
        """(id, event_id, user_id) или None — для проверки прав на удаление."""
        with self._get_cursor() as cursor:
            cursor.execute(
                "SELECT id, event_id, user_id FROM event_comments WHERE id = %s",
                (int(comment_id),),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "id": int(row[0]),
                "event_id": int(row[1]),
                "user_id": int(row[2]) if row[2] is not None else None,
            }

    def delete_event_comment(self, comment_id):
        with self._get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM event_comments WHERE id = %s RETURNING id",
                (int(comment_id),),
            )
            return bool(cursor.fetchone())

    def mark_events_seen(self, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO event_reads (user_id, last_seen_at)
                VALUES (%s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                ON CONFLICT (user_id)
                DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at
            """, (int(user_id),))

    def count_unread_events(self, user_id, viewer_department_id=None, is_global=False):
        """Кол-во постов новее last_seen зрителя, видимых ему, исключая свои."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT last_seen_at FROM event_reads WHERE user_id = %s", (int(user_id),))
            r = cursor.fetchone()
            last_seen = r[0] if r else None
            conditions = ["(e.author_id IS DISTINCT FROM %s)"]
            params = [int(user_id)]
            if last_seen is not None:
                conditions.append("e.created_at > %s")
                params.append(last_seen)
            if not is_global:
                conditions.append("""(
                    (
                        NOT EXISTS (SELECT 1 FROM event_departments ed WHERE ed.event_id = e.id)
                        AND (e.department_id IS NULL OR e.department_id = %s)
                    )
                    OR EXISTS (SELECT 1 FROM event_departments ed WHERE ed.event_id = e.id AND ed.department_id = %s)
                )""")
                params.extend([viewer_department_id, viewer_department_id])
            cursor.execute(
                f"SELECT COUNT(*) FROM events e WHERE {' AND '.join(conditions)}",
                tuple(params),
            )
            return int(cursor.fetchone()[0] or 0)

    def save_ai_birthday_greeting_cache(self, user_id: int, date_key: str, greeting_data: dict):
        """Сохранить или обновить AI-поздравление с днем рождения"""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO ai_birthday_greeting_cache (user_id, greeting_date, greeting_data, updated_at)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, greeting_date)
                DO UPDATE SET
                    greeting_data = EXCLUDED.greeting_data,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, date_key, json.dumps(greeting_data, ensure_ascii=False)))
