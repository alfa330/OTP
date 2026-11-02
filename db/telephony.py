"""База данных: телефония: Oktell, Binotel, iCORE Phone.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    BINOTEL_CABINET_URL_DEFAULT,
    Optional,
)


class TelephonyMixin:
    def get_oktell_billing_hour_comments(self, date_from, date_to):
        """Комментарии «Биллинг Oktell → Группировка»: {'ГГГГ-ММ-ДД': {час: {comment, author, updated_at}}}."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT c.report_date, c.hour, c.comment, u.name, c.updated_at
                FROM oktell_billing_hour_comments c
                LEFT JOIN users u ON u.id = c.updated_by
                WHERE c.report_date BETWEEN %s AND %s
                ORDER BY c.report_date, c.hour
            """, (date_from, date_to))
            rows = cursor.fetchall() or []
        result = {}
        for report_date, hour, comment, author, updated_at in rows:
            day_key = report_date.strftime('%Y-%m-%d') if hasattr(report_date, 'strftime') else str(report_date)
            result.setdefault(day_key, {})[int(hour)] = {
                'comment': comment or '',
                'author': author or '',
                'updated_at': updated_at.isoformat() if updated_at else None,
            }
        return result

    def save_oktell_billing_hour_comment(self, report_date, hour_from, hour_to, comment,
                                         updated_by=None, previous_hour_from=None,
                                         previous_hour_to=None):
        """Комментарий на отрезок часов одного дня; пустой текст — снять его.

        previous_* — отрезок, который комментарий занимал до правки. Его часы сначала
        очищаются: иначе при сужении отрезка отпавшие часы остались бы со старым текстом.
        Чужой комментарий под новым отрезком перезаписывается — побеждает последняя правка.
        Возвращает комментарии этого дня в виде get_oktell_billing_hour_comments."""
        with self._get_cursor() as cursor:
            if previous_hour_from is not None and previous_hour_to is not None:
                cursor.execute("""
                    DELETE FROM oktell_billing_hour_comments
                    WHERE report_date = %s AND hour BETWEEN %s AND %s
                """, (report_date, int(previous_hour_from), int(previous_hour_to)))
            if comment:
                cursor.execute("""
                    INSERT INTO oktell_billing_hour_comments (report_date, hour, comment, updated_by)
                    SELECT %s, h, %s, %s
                    FROM generate_series(%s::int, %s::int) AS h
                    ON CONFLICT (report_date, hour) DO UPDATE
                    SET comment = EXCLUDED.comment,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = CURRENT_TIMESTAMP
                """, (report_date, comment, updated_by, int(hour_from), int(hour_to)))
            else:
                cursor.execute("""
                    DELETE FROM oktell_billing_hour_comments
                    WHERE report_date = %s AND hour BETWEEN %s AND %s
                """, (report_date, int(hour_from), int(hour_to)))
        day_key = report_date.strftime('%Y-%m-%d') if hasattr(report_date, 'strftime') else str(report_date)
        return self.get_oktell_billing_hour_comments(report_date, report_date).get(day_key) or {}

    def get_binotel_account(self, user_id: int) -> Optional[dict]:
        """Учётка веб-кабинета Binotel вместе с паролем — только для самого оператора.

        Панель руководителя пароль не получает никогда (там лишь признак
        «задан»); телефон получает, потому что сам поднимает сессию кабинета и
        переключает статус запросом."""
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT b.cabinet_login, b.cabinet_password, b.employee_id, b.cabinet_url,
                       COALESCE(dc.binotel_cabinet_url, '')
                FROM binotel_user_accounts b
                JOIN users u ON u.id = b.user_id
                LEFT JOIN sip_department_config dc ON dc.department_id = u.department_id
                WHERE b.user_id = %s
            """, (int(user_id),))
            row = cur.fetchone()
        if not row or not (row[0] or '').strip():
            return None
        # Цепочка та же, что у домена: персональный адрес → общий адрес отдела →
        # зашитый дефолт. Пустое поле в карточке значит «как у всех», а не «нет».
        base_url = ((row[3] or '').strip() or (row[4] or '').strip()
                    or BINOTEL_CABINET_URL_DEFAULT)
        employee_id = (row[2] or '').strip()
        return {
            "cabinet_url": base_url,
            "cabinet_login": row[0] or "",
            "cabinet_password": row[1] or "",
            "employee_id": employee_id,
            # «me» кабинет понимает сам, поэтому ссылка работает и без employee_id.
            "status_url": "%s/f/pbx/#/settings/users/manage-users/%s" % (
                base_url.rstrip('/'), employee_id or 'me'),
        }

    def get_oktell_account(self, user_id: int) -> Optional[dict]:
        """Учётка кабинета Oktell вместе с паролем — только для самого оператора.

        Панель руководителя пароль не получает никогда (там лишь признак
        «задан»); клиент Oktell получает, потому что ради этого всё и заведено:
        оператор скачивает клиент с нашего сайта, входит своей учёткой iCORE, а
        логин и пароль АТС подставляются за него. Возвращаем None, пока логин не
        задан, — пароль без логина не учётка.
        """
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT cabinet_login, cabinet_password
                  FROM oktell_user_accounts
                 WHERE user_id = %s
            """, (int(user_id),))
            row = cur.fetchone()
        if not row or not (row[0] or '').strip():
            return None
        return {
            "cabinet_login": (row[0] or "").strip(),
            "cabinet_password": row[1] or "",
        }

    @staticmethod
    def _icore_phone_release_row(row) -> dict:
        return {
            "id": row[0],
            "version": row[1] or "",
            "variant": row[2] or "online",
            "filename": row[3] or "",
            "sha256": (row[4] or "").strip().lower(),
            "size": int(row[5] or 0),
            "gcs_bucket": row[6] or "",
            "gcs_path": row[7] or "",
            "notes": row[8] or "",
            "mandatory": bool(row[9]),
            "is_current": bool(row[10]),
            "uploaded_by": row[11],
            "published_at": row[12].isoformat() if row[12] else None,
        }

    def get_icore_phone_release(self, variant: str = 'online'):
        """Текущий релиз варианта или None, если ещё ничего не публиковали."""
        variant = self.normalize_icore_phone_variant(variant)
        with self._get_cursor() as cur:
            cur.execute(
                f"SELECT {self._ICORE_PHONE_RELEASE_COLUMNS} FROM icore_phone_releases "
                "WHERE variant = %s AND is_current LIMIT 1",
                (variant,))
            row = cur.fetchone()
        return self._icore_phone_release_row(row) if row else None

    def get_icore_phone_releases(self, limit: int = 50) -> list:
        """История публикаций для админского UI: свежие сверху."""
        limit = max(1, min(int(limit or 50), 200))
        with self._get_cursor() as cur:
            cur.execute(
                f"SELECT {self._ICORE_PHONE_RELEASE_COLUMNS} FROM icore_phone_releases "
                "ORDER BY uploaded_at DESC, id DESC LIMIT %s",
                (limit,))
            rows = cur.fetchall()
        return [self._icore_phone_release_row(r) for r in rows]

    def add_icore_phone_release(self, *, version, variant='online', filename, sha256,
                                size_bytes, gcs_bucket, gcs_path, notes='',
                                mandatory=False, uploaded_by=None) -> dict:
        """Зарегистрировать релиз и сделать его текущим для своего варианта.

        Возвращает {'release': ..., 'stale_blobs': [(bucket, path), ...]}.
        stale_blobs — объекты прежних версий этого варианта: имя объекта содержит
        версию, поэтому старые файлы никем больше не используются и без чистки
        бакет растёт на 6 МБ с каждой сборкой. Ссылку на них мы тут же обнуляем в
        базе — подписать URL на уже удалённый объект хуже, чем не подписать вовсе.
        """
        variant = self.normalize_icore_phone_variant(variant)
        version = str(version or '').strip()
        if not version:
            raise ValueError("Не задан номер версии")
        sha256 = str(sha256 or '').strip().lower()
        with self._get_cursor() as cur:
            cur.execute(
                "SELECT gcs_bucket, gcs_path FROM icore_phone_releases "
                "WHERE variant = %s AND version <> %s AND gcs_path <> ''",
                (variant, version))
            stale = [(r[0], r[1]) for r in cur.fetchall() if r[0] and r[1]]
            cur.execute(
                "UPDATE icore_phone_releases SET is_current = FALSE, gcs_path = '' "
                "WHERE variant = %s AND version <> %s",
                (variant, version))
            cur.execute(f"""
                INSERT INTO icore_phone_releases (
                    version, variant, filename, sha256, size_bytes, gcs_bucket, gcs_path,
                    notes, mandatory, is_current, uploaded_by, uploaded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s,
                        (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                ON CONFLICT (variant, version) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    sha256 = EXCLUDED.sha256,
                    size_bytes = EXCLUDED.size_bytes,
                    gcs_bucket = EXCLUDED.gcs_bucket,
                    gcs_path = EXCLUDED.gcs_path,
                    notes = EXCLUDED.notes,
                    mandatory = EXCLUDED.mandatory,
                    is_current = TRUE,
                    uploaded_by = EXCLUDED.uploaded_by,
                    uploaded_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                RETURNING {self._ICORE_PHONE_RELEASE_COLUMNS}
            """, (version, variant, str(filename or '').strip(), sha256, int(size_bytes or 0),
                  str(gcs_bucket or '').strip(), str(gcs_path or '').strip(),
                  str(notes or '').strip(), bool(mandatory), uploaded_by))
            row = cur.fetchone()
        return {
            "release": self._icore_phone_release_row(row) if row else None,
            "stale_blobs": stale,
        }
