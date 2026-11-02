"""База данных: рекрутинг.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    execute_values,
    logging,
    RECRUITING_RUNS_KEEP_DAYS,
    uuid,
)


class RecruitingMixin:
    def _serialize_recruiting_run_row(self, row):
        if not row:
            return None
        return {
            "run_uuid": row[0],
            "source": row[1],
            "triggered_by": row[2],
            "status": row[3],
            "total_items": int(row[4] or 0),
            "error_message": row[5],
            "started_at": row[6].isoformat() if hasattr(row[6], "isoformat") else row[6],
            "finished_at": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7],
        }

    def save_recruiting_parse_success(self, items, source='enbek', triggered_by='scheduler'):
        source_norm = (str(source or 'enbek').strip().lower() or 'enbek')[:64]
        trigger_norm = (str(triggered_by or 'scheduler').strip().lower() or 'scheduler')[:32]
        run_uuid = str(uuid.uuid4())
        normalized_rows = []
        for raw in (items or []):
            if isinstance(raw, dict):
                normalized_rows.append(self._normalize_recruiting_item(raw))
        deduped_rows = []
        seen_links = set()
        for row in normalized_rows:
            detail_url = row.get("detail_url")
            if detail_url:
                detail_key = str(detail_url).strip().lower()
                if detail_key in seen_links:
                    continue
                seen_links.add(detail_key)
            deduped_rows.append(row)

        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO recruiting_parse_runs (
                    run_uuid, source, triggered_by, status, total_items, error_message, started_at, finished_at
                )
                VALUES (%s::uuid, %s, %s, 'success', %s, NULL, CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
            """, (run_uuid, source_norm, trigger_norm, len(deduped_rows)))

            if deduped_rows:
                values = [
                    (
                        run_uuid,
                        row["keyword_group"],
                        row["keyword_query"],
                        row["page_found"],
                        row["title"],
                        row["category"],
                        row["experience"],
                        row["location"],
                        row["salary"],
                        row["education"],
                        row["published_at"],
                        row["detail_url"],
                    )
                    for row in deduped_rows
                ]
                execute_values(
                    cursor,
                    """
                    INSERT INTO recruiting_resumes (
                        run_uuid, keyword_group, keyword_query, page_found,
                        title, category, experience, location, salary,
                        education, published_at, detail_url
                    )
                    VALUES %s
                    ON CONFLICT (run_uuid, detail_url) DO NOTHING
                    """,
                    values,
                    page_size=1000
                )

            cursor.execute("""
                SELECT run_uuid::text, source, triggered_by, status, total_items, error_message, started_at, finished_at
                FROM recruiting_parse_runs
                WHERE run_uuid = %s::uuid
                LIMIT 1
            """, (run_uuid,))
            run_row = cursor.fetchone()

        self._prune_recruiting_parse_runs(keep_run_uuid=run_uuid)
        return {
            "run": self._serialize_recruiting_run_row(run_row),
            "inserted": len(deduped_rows),
            "duplicates_dropped": max(0, len(normalized_rows) - len(deduped_rows))
        }

    def _prune_recruiting_parse_runs(self, keep_run_uuid, keep_days=None):
        """Удалить прогоны парсера старше RECRUITING_RUNS_KEEP_DAYS; резюме уходят каскадом.

        Зовётся после записи нового успешного прогона, поэтому последний успешный
        остаётся всегда (его uuid исключён явно). Своей транзакцией и без исключений
        наружу: сбой чистки не должен отменить уже записанный прогон."""
        days = RECRUITING_RUNS_KEEP_DAYS if keep_days is None else int(keep_days)
        try:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    DELETE FROM recruiting_parse_runs
                    WHERE finished_at < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                                        - make_interval(days => %s)
                      AND run_uuid <> %s::uuid
                """, (days, keep_run_uuid))
                removed = int(cursor.rowcount or 0)
        except Exception:
            logging.exception("recruiting: чистка старых прогонов не удалась")
            return 0
        if removed:
            logging.info("recruiting: удалено %s прогонов старше %s дней", removed, days)
        return removed

    def save_recruiting_parse_failure(self, error_message, source='enbek', triggered_by='scheduler'):
        source_norm = (str(source or 'enbek').strip().lower() or 'enbek')[:64]
        trigger_norm = (str(triggered_by or 'scheduler').strip().lower() or 'scheduler')[:32]
        run_uuid = str(uuid.uuid4())
        error_text = (str(error_message or '').strip() or 'Parser execution failed')[:4000]

        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO recruiting_parse_runs (
                    run_uuid, source, triggered_by, status, total_items, error_message, started_at, finished_at
                )
                VALUES (%s::uuid, %s, %s, 'failed', 0, %s, CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
            """, (run_uuid, source_norm, trigger_norm, error_text))

            cursor.execute("""
                SELECT run_uuid::text, source, triggered_by, status, total_items, error_message, started_at, finished_at
                FROM recruiting_parse_runs
                WHERE run_uuid = %s::uuid
                LIMIT 1
            """, (run_uuid,))
            run_row = cursor.fetchone()

        return {"run": self._serialize_recruiting_run_row(run_row)}

    def get_recruiting_resumes(self, run_uuid=None, limit=500, offset=0, search=None, keyword_group=None):
        try:
            limit_val = int(limit)
        except Exception:
            limit_val = 500
        limit_val = max(1, min(limit_val, 5000))

        try:
            offset_val = int(offset)
        except Exception:
            offset_val = 0
        offset_val = max(0, offset_val)

        run_uuid_text = (str(run_uuid or '').strip() or None)
        with self._get_cursor() as cursor:
            if not run_uuid_text:
                cursor.execute("""
                    SELECT run_uuid::text, source, triggered_by, status, total_items, error_message, started_at, finished_at
                    FROM recruiting_parse_runs
                    WHERE status = 'success'
                    ORDER BY finished_at DESC, id DESC
                    LIMIT 1
                """)
                run_row = cursor.fetchone()
                if not run_row:
                    return {"run": None, "total": 0, "items": []}
                run_uuid_text = str(run_row[0])
            else:
                cursor.execute("""
                    SELECT run_uuid::text, source, triggered_by, status, total_items, error_message, started_at, finished_at
                    FROM recruiting_parse_runs
                    WHERE run_uuid = %s::uuid
                    LIMIT 1
                """, (run_uuid_text,))
                run_row = cursor.fetchone()
                if not run_row:
                    return {"run": None, "total": 0, "items": []}

            where_clauses = ["run_uuid = %s::uuid"]
            params = [run_uuid_text]

            keyword_group_norm = (str(keyword_group or '').strip() or None)
            if keyword_group_norm:
                where_clauses.append("keyword_group = %s")
                params.append(keyword_group_norm)

            search_text = (str(search or '').strip() or None)
            if search_text:
                like = f"%{search_text}%"
                where_clauses.append("""
                    (
                        COALESCE(title, '') ILIKE %s
                        OR COALESCE(category, '') ILIKE %s
                        OR COALESCE(location, '') ILIKE %s
                        OR COALESCE(experience, '') ILIKE %s
                        OR COALESCE(keyword_query, '') ILIKE %s
                        OR COALESCE(education, '') ILIKE %s
                    )
                """)
                params.extend([like, like, like, like, like, like])

            where_sql = " AND ".join(where_clauses)

            cursor.execute(f"""
                SELECT COUNT(*)
                FROM recruiting_resumes
                WHERE {where_sql}
            """, tuple(params))
            total_count = int(cursor.fetchone()[0] or 0)

            cursor.execute(
                f"""
                SELECT
                    id,
                    run_uuid::text,
                    keyword_group,
                    keyword_query,
                    page_found,
                    title,
                    category,
                    experience,
                    location,
                    salary,
                    education,
                    published_at,
                    detail_url,
                    scraped_at
                FROM recruiting_resumes
                WHERE {where_sql}
                ORDER BY COALESCE(page_found, 999999), id DESC
                LIMIT %s OFFSET %s
                """,
                tuple(params) + (limit_val, offset_val),
            )
            rows = cursor.fetchall()

        items = []
        for row in rows:
            items.append({
                "id": int(row[0]),
                "run_uuid": row[1],
                "keyword_group": row[2],
                "keyword_query": row[3],
                "page_found": row[4],
                "title": row[5],
                "category": row[6],
                "experience": row[7],
                "location": row[8],
                "salary": row[9],
                "education": row[10],
                "published_at": row[11],
                "detail_url": row[12],
                "scraped_at": row[13].isoformat() if hasattr(row[13], "isoformat") else row[13],
            })

        return {
            "run": self._serialize_recruiting_run_row(run_row),
            "total": total_count,
            "items": items,
        }
