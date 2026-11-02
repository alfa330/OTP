"""База данных: чаты Chat2Desk: метрики и оценки.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    calendar,
    date,
    datetime,
    dt_time,
    dt_timezone,
    execute_values,
    Json,
    json,
    logging,
    LowRatingAttributionConflict,
    math,
    re,
    uuid,
    ZoneInfo,
)


class ChatRatingsMixin:
    def add_chat_hourly_subscription(self, chat_id, title=None, chat_type=None, user_id=None):
        """Подписать чат на почасовой отчёт по чатам. Повторная подписка обновляет данные чата."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO chat_hourly_subscriptions (chat_id, title, chat_type, subscribed_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    chat_type = EXCLUDED.chat_type,
                    subscribed_by = EXCLUDED.subscribed_by
                RETURNING (xmax = 0) AS is_new
            """, (str(chat_id), title, chat_type, user_id))
            return bool(cursor.fetchone()[0])

    def remove_chat_hourly_subscription(self, chat_id):
        """Отписать чат. Возвращает True, если подписка была."""
        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM chat_hourly_subscriptions WHERE chat_id = %s",
                           (str(chat_id),))
            return cursor.rowcount > 0

    def get_chat_hourly_subscriptions(self):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT chat_id, title, chat_type
                FROM chat_hourly_subscriptions
                ORDER BY created_at
            """)
            return [{"chat_id": r[0], "title": r[1], "chat_type": r[2]}
                    for r in cursor.fetchall()]

    def mark_chat_hourly_subscription_sent(self, chat_id):
        with self._get_cursor() as cursor:
            cursor.execute(
                "UPDATE chat_hourly_subscriptions SET last_sent_at = NOW() WHERE chat_id = %s",
                (str(chat_id),))

    @staticmethod
    def _coerce_daily_extra_metrics(value):
        """JSONB daily_hours.extra_metrics -> обычный dict без исключений."""
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}

    def _load_chat_manager_metrics_by_operator_day_tx(self, cursor, operator_ids, start_date, end_date, group_id=None):
        op_ids = sorted({int(v) for v in (operator_ids or []) if v is not None})
        result = {op_id: {} for op_id in op_ids}
        totals = {
            op_id: {
                'chats_count': 0,
                'whatsapp_chats_count': 0,
                'name_requests_chats_count': 0,
                'transfer_chat_count': 0,
                'avg_score': None,
                'avg_response_time_seconds': None
            }
            for op_id in op_ids
        }
        if not op_ids:
            return result, totals

        group_filter_sql = ""
        params = [op_ids, start_date, end_date]
        if group_id is not None:
            group_filter_sql = """
              AND EXISTS (
                  SELECT 1
                  FROM group_operator_memberships gom
                  WHERE gom.operator_id = chat_manager_daily_metrics.operator_id
                    AND gom.group_id = %s
                    AND gom.start_date <= chat_manager_daily_metrics.day
                    AND (gom.end_date IS NULL OR gom.end_date >= chat_manager_daily_metrics.day)
              )
            """
            params.append(int(group_id))

        cursor.execute(
            f"""
            SELECT
                operator_id,
                day,
                COALESCE(chats_count, 0),
                avg_score,
                avg_response_time_seconds,
                COALESCE(transfer_chat_count, 0),
                score_sum,
                score_count,
                COALESCE(whatsapp_chats_count, 0),
                COALESCE(name_requests_chats_count, 0)
            FROM chat_manager_daily_metrics
            WHERE operator_id = ANY(%s)
              AND day >= %s
              AND day <= %s
              {group_filter_sql}
            ORDER BY operator_id, day
            """,
            params
        )

        # Средняя оценка считается ВЗВЕШЕННО: суммируем score_sum и score_count по дням.
        # Для исторических дней без счётчика берём сохранённый avg_score с весом 1.
        score_acc = {op_id: {'sum': 0.0, 'count': 0} for op_id in op_ids}
        # Время ответа храним как готовое дневное среднее — период считаем как среднее дневных.
        response_acc = {op_id: {'sum': 0.0, 'count': 0} for op_id in op_ids}
        for op_id, day_obj, chats_count, avg_score, avg_response_time_seconds, transfer_chat_count, score_sum, score_count, whatsapp_chats_count, name_requests_chats_count in cursor.fetchall() or []:
            op_id_int = int(op_id)
            if op_id_int not in result or day_obj is None:
                continue
            chats_int = max(0, int(chats_count or 0))
            whatsapp_chats_int = max(0, int(whatsapp_chats_count or 0))
            name_requests_chats_int = max(0, int(name_requests_chats_count or 0))
            transfer_int = max(0, int(transfer_chat_count or 0))
            s_sum = float(score_sum) if score_sum is not None else None
            s_cnt = int(score_count) if score_count is not None else None
            # Дневная средняя оценка: взвешенная (sum/count), иначе сохранённое avg_score.
            if s_cnt and s_cnt > 0 and s_sum is not None:
                day_avg_score = round(s_sum / s_cnt, 2)
            elif avg_score is not None:
                day_avg_score = float(avg_score)
            else:
                day_avg_score = None
            payload = {
                "date": day_obj.strftime('%Y-%m-%d'),
                "chats_count": chats_int,
                "whatsapp_chats_count": whatsapp_chats_int,
                "name_requests_chats_count": name_requests_chats_int,
                "avg_score": day_avg_score,
                "score_sum": s_sum,
                "score_count": s_cnt,
                "avg_response_time_seconds": float(avg_response_time_seconds) if avg_response_time_seconds is not None else None,
                "avg_response_time_minutes": round(float(avg_response_time_seconds) / 60.0, 2) if avg_response_time_seconds is not None else None,
                "transfer_chat_count": transfer_int
            }
            day_key = str(int(day_obj.day))
            result.setdefault(op_id_int, {})[day_key] = payload
            totals[op_id_int]['chats_count'] += chats_int
            totals[op_id_int]['whatsapp_chats_count'] += whatsapp_chats_int
            totals[op_id_int]['name_requests_chats_count'] += name_requests_chats_int
            totals[op_id_int]['transfer_chat_count'] += transfer_int
            if s_cnt and s_cnt > 0 and s_sum is not None:
                score_acc[op_id_int]['sum'] += s_sum
                score_acc[op_id_int]['count'] += s_cnt
            elif avg_score is not None:
                score_acc[op_id_int]['sum'] += float(avg_score)
                score_acc[op_id_int]['count'] += 1
            if avg_response_time_seconds is not None:
                response_acc[op_id_int]['sum'] += float(avg_response_time_seconds)
                response_acc[op_id_int]['count'] += 1

        for op_id in op_ids:
            score_count = score_acc[op_id]['count']
            response_count = response_acc[op_id]['count']
            if score_count > 0:
                # Полная точность нужна потребителям KPI/зарплаты; до 2 знаков округляет только UI.
                totals[op_id]['avg_score'] = score_acc[op_id]['sum'] / score_count
            if response_count > 0:
                totals[op_id]['avg_response_time_seconds'] = round(response_acc[op_id]['sum'] / response_count, 2)

        return result, totals

    @staticmethod
    def _normalize_low_rating_review_status(value):
        text = str(value or '').strip().lower()
        if not text:
            return None
        if text in {'valid', 'justified', 'substantiated', 'true', '1', 'yes', 'обоснованно', 'обоснованная'}:
            return 'valid'
        if text in {'invalid', 'unjustified', 'not_valid', 'false', '0', 'no', 'необоснованно', 'необоснованная'}:
            return 'invalid'
        raise ValueError("review status must be valid, invalid or empty")

    @staticmethod
    def _normalize_low_rating_operator_ids(value):
        """Кому засчитать оценку: уникальные id сотрудников по возрастанию.

        None — поле не пришло, атрибуцию не трогаем. Пустой список — пришло
        пустым; для «обоснованно» это ошибка, решает вызывающий."""
        if value is None:
            return None
        if isinstance(value, (str, bytes, dict)) or not isinstance(value, (list, tuple, set)):
            raise ValueError("operator_ids must be a list of user ids")
        result = set()
        for raw in value:
            if isinstance(raw, bool):
                raise ValueError("operator_ids must be a list of user ids")
            try:
                number = int(raw)
            except (TypeError, ValueError):
                raise ValueError("operator_ids must be a list of user ids")
            if number <= 0:
                raise ValueError("operator_ids must be a list of user ids")
            result.add(number)
        return sorted(result)

    @staticmethod
    def _low_rating_effective_attribution(operator_id, attributed_operator_ids):
        """Кому засчитывается оценка сейчас: решение проверяющих или, пока его
        нет, тот, кому её отдал Chat2Desk."""
        ids = sorted({int(v) for v in (attributed_operator_ids or []) if v is not None})
        if ids:
            return ids
        return [int(operator_id)] if operator_id is not None else []

    @staticmethod
    def _low_rating_attribution_to_store(operator_id, operator_ids):
        """Что писать в attributed_operator_ids. Решение, совпавшее с Chat2Desk,
        хранится как NULL: тогда оценка и дальше идёт за operator_id, если
        пересинк поправит, кому её отдал вендор, — «по умолчанию» не застывает."""
        ids = sorted({int(v) for v in (operator_ids or [])})
        if not ids or (operator_id is not None and ids == [int(operator_id)]):
            return None
        return ids

    @staticmethod
    def _low_rating_db_time_iso(value):
        """Служебные метки разбора (голоса, итог, история) пишутся
        CURRENT_TIMESTAMP в сессии UTC и хранятся без пояса, а время самой
        оценки — местное. На одном экране они стояли рядом с разницей в пять
        часов. Отдаём по Алматы."""
        if not isinstance(value, datetime):
            return None
        aware = value if value.tzinfo else value.replace(tzinfo=dt_timezone.utc)
        return aware.astimezone(ZoneInfo('Asia/Almaty')).replace(tzinfo=None).isoformat()

    def _low_rating_operator_names_tx(self, cursor, user_ids):
        """{id сотрудника: имя} для подписи атрибуции.

        Имя учётки Chat2Desk («Имя Фамилия»), а не карточки: им подписаны
        журнал, сама переписка и сообщения о передаче чата — на одном экране
        человек не должен называться двумя разными способами."""
        ids = sorted({int(v) for v in (user_ids or []) if v is not None})
        if not ids:
            return {}
        cursor.execute(
            """
            SELECT u.id,
                   COALESCE(
                       (SELECT r.c2d_operator_name
                          FROM c2d_requests r
                         WHERE r.operator_id = u.id
                           AND NULLIF(r.c2d_operator_name, '') IS NOT NULL
                         ORDER BY r.day DESC
                         LIMIT 1),
                       (SELECT lr.operator_name
                          FROM chat_manager_low_rating_reviews lr
                         WHERE lr.operator_id = u.id
                           AND NULLIF(lr.operator_name, '') IS NOT NULL
                         ORDER BY lr.day DESC
                         LIMIT 1),
                       u.name,
                       ''
                   )
            FROM users u
            WHERE u.id = ANY(%s)
            """,
            (ids,)
        )
        return {int(row[0]): str(row[1] or '') for row in cursor.fetchall() or []}

    def _attach_low_rating_attribution_tx(self, cursor, items):
        """attributed_operators = [{id, name}] — кому засчитывается каждая оценка."""
        explicit_ids = {
            int(v)
            for item in items
            for v in (item.get('attributed_operator_ids') or [])
            if v is not None
        }
        names = self._low_rating_operator_names_tx(cursor, explicit_ids) if explicit_ids else {}
        for item in items:
            operator_id = item.get('operator_id')
            own_id = int(operator_id) if operator_id is not None else None
            effective = self._low_rating_effective_attribution(operator_id, item.get('attributed_operator_ids'))
            # Тот, кому оценку отдал Chat2Desk, — первым: так список читается как
            # «было у него, добавили ещё…», а не перемешивается при каждой правке.
            effective.sort(key=lambda value: (value != own_id, value))
            item['attributed_operators'] = [
                {
                    'id': value,
                    'name': (
                        (item.get('operator_name') if value == own_id else '')
                        or names.get(value)
                        or ''
                    ),
                }
                for value in effective
            ]
            item['attribution_changed'] = bool(item.get('attributed_operator_ids'))
        return items

    def _validate_low_rating_attribution_tx(self, cursor, operator_ids, own_operator_id,
                                            department_scope_id=None, stored_ids=None):
        """Засчитать можно только существующему сотруднику, а проверяющему с
        ограничением по отделу — только сотруднику своего отдела. Без проверки
        отдела проходят тот, кому оценку отдал Chat2Desk (оценка и так на нём), и
        уже засчитанные раньше: это не новое решение, и переведённого с тех пор в
        другой отдел менеджера иначе пришлось бы снять, чтобы сохранить вердикт."""
        cursor.execute(
            "SELECT id, department_id FROM users WHERE id = ANY(%s)",
            (list(operator_ids),)
        )
        found = {int(row[0]): row[1] for row in cursor.fetchall() or []}
        if any(value not in found for value in operator_ids):
            raise ValueError("Менеджер для атрибуции не найден")
        if department_scope_id is None:
            return
        own_id = int(own_operator_id) if own_operator_id is not None else None
        kept = {int(value) for value in (stored_ids or []) if value is not None}
        for value in operator_ids:
            if value == own_id or value in kept:
                continue
            department_id = found.get(value)
            if department_id is None or int(department_id) != int(department_scope_id):
                raise ValueError("Засчитать оценку можно только менеджеру своего отдела")

    def _log_low_rating_history_tx(self, cursor, review_id, actor_id, action, status, prev_status,
                                   comment, operator_ids, prev_operator_ids, final_status,
                                   prev_final_status):
        cursor.execute(
            """
            INSERT INTO chat_manager_low_rating_review_history (
                review_id, actor_id, action, status, prev_status, comment,
                operator_ids, prev_operator_ids, final_status, prev_final_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                review_id,
                int(actor_id) if actor_id is not None else None,
                action,
                status,
                prev_status,
                comment,
                list(operator_ids) if operator_ids is not None else None,
                list(prev_operator_ids) if prev_operator_ids is not None else None,
                final_status,
                prev_final_status,
            )
        )

    @staticmethod
    def _low_rating_recalc_pairs(operator_id, day_obj, *attributions):
        """(оператор, день), которые надо пересчитать после правки разбора:
        тот, кому оценку отдал Chat2Desk, и все, кому она была или стала засчитана."""
        pairs = {(int(operator_id), day_obj)}
        for attribution in attributions:
            for value in attribution or []:
                pairs.add((int(value), day_obj))
        return pairs

    def get_chat_manager_low_rating_review_history(self, review_id):
        """История разбора, новые события сверху."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT h.id, h.action, h.actor_id, COALESCE(NULLIF(u.name, ''), ''),
                       h.status, h.prev_status, h.comment, h.operator_ids,
                       h.prev_operator_ids, h.final_status, h.prev_final_status,
                       h.created_at
                FROM chat_manager_low_rating_review_history h
                LEFT JOIN users u ON u.id = h.actor_id
                WHERE h.review_id = %s
                ORDER BY h.created_at DESC, h.id DESC
                """,
                (review_id,)
            )
            rows = cursor.fetchall() or []
            mentioned = {
                int(value)
                for row in rows
                for value in list(row[7] or []) + list(row[8] or [])
                if value is not None
            }
            names = self._low_rating_operator_names_tx(cursor, mentioned) if mentioned else {}

        def _operators(values):
            if values is None:
                return None
            return [{'id': int(value), 'name': names.get(int(value)) or ''} for value in values]

        return [
            {
                'id': int(row[0]),
                'action': row[1],
                'actor_id': int(row[2]) if row[2] is not None else None,
                'actor_name': row[3] or '',
                'status': self._normalize_low_rating_review_status(row[4]),
                'prev_status': self._normalize_low_rating_review_status(row[5]),
                'comment': row[6],
                'operators': _operators(row[7]),
                'prev_operators': _operators(row[8]),
                'final_status': self._normalize_low_rating_review_status(row[9]),
                'prev_final_status': self._normalize_low_rating_review_status(row[10]),
                'created_at': self._low_rating_db_time_iso(row[11]),
            }
            for row in rows
        ]

    @staticmethod
    def _resolve_low_rating_final_status(arai_status, zhanna_status, head_status):
        arai = ChatRatingsMixin._normalize_low_rating_review_status(arai_status)
        zhanna = ChatRatingsMixin._normalize_low_rating_review_status(zhanna_status)
        head = ChatRatingsMixin._normalize_low_rating_review_status(head_status)
        if arai and zhanna and arai == zhanna:
            return arai, 'consensus'
        if head:
            return head, 'head'
        return None, None

    @staticmethod
    def _parse_low_rating_datetime(value):
        if value in (None, ''):
            return None
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        if isinstance(value, date):
            return datetime.combine(value, dt_time.min)
        text = str(value or '').strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
            if getattr(parsed, 'tzinfo', None) is not None:
                # Пояс задаём явно. astimezone() без аргумента берёт пояс
                # процесса, а он зависит от машины: на Render это UTC, локально —
                # Алматы, и одно и то же значение раскладывалось на пять часов
                # по-разному. День оценки считается по Алматы, время обязано
                # считаться там же.
                parsed = parsed.astimezone(ZoneInfo('Asia/Almaty')).replace(tzinfo=None)
            return parsed
        except Exception:
            pass
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
        return None

    @staticmethod
    def _normalize_low_rating_phone(value):
        return re.sub(r'\D+', '', str(value or ''))

    @staticmethod
    def _low_rating_review_state(row):
        arai = row.get('arai_status')
        zhanna = row.get('zhanna_status')
        final_status = row.get('final_status')
        if final_status:
            return 'resolved'
        if arai and zhanna and arai != zhanna:
            return 'conflict'
        return 'pending'

    @staticmethod
    def _low_rating_row_to_dict(row):
        keys = [
            'id', 'source', 'source_key', 'operator_id', 'operator_name', 'phone_number',
            'phone_normalized', 'taxi_park', 'rated_at', 'day', 'score', 'raw_payload',
            'imported_by', 'source_batch_id', 'arai_status', 'arai_comment',
            'arai_updated_by', 'arai_updated_by_name', 'arai_updated_at',
            'zhanna_status', 'zhanna_comment', 'zhanna_updated_by',
            'zhanna_updated_by_name', 'zhanna_updated_at', 'head_status',
            'head_comment', 'head_updated_by', 'head_updated_by_name',
            'head_updated_at', 'final_status', 'final_source', 'final_comment',
            'final_decided_by', 'final_decided_by_name', 'final_decided_at',
            'created_at', 'updated_at', 'direction_name', 'department_id', 'department_name',
            'attributed_operator_ids'
        ]
        item = dict(zip(keys, row))
        for key in ('id', 'source_batch_id'):
            if item.get(key) is not None:
                item[key] = str(item[key])
        item['attributed_operator_ids'] = [
            int(value) for value in (item.get('attributed_operator_ids') or []) if value is not None
        ] or None
        # Время решения показывается рядом с временем голосов — оба по Алматы.
        item['final_decided_at'] = ChatRatingsMixin._low_rating_db_time_iso(item.get('final_decided_at'))
        for key in (
            'rated_at', 'day', 'arai_updated_at', 'zhanna_updated_at',
            'head_updated_at', 'final_decided_at', 'created_at', 'updated_at'
        ):
            value = item.get(key)
            if isinstance(value, datetime):
                item[key] = value.isoformat()
            elif isinstance(value, date):
                item[key] = value.strftime('%Y-%m-%d')
            elif value is None:
                item[key] = None
        item['score'] = float(item.get('score') or 0)
        item['review_state'] = ChatRatingsMixin._low_rating_review_state(item)
        item['needs_head_decision'] = bool(item['review_state'] == 'conflict')
        return item

    @staticmethod
    def _low_rating_extract_source_details(item):
        raw = item.get('raw_payload') if isinstance(item.get('raw_payload'), dict) else {}
        return {
            'client_comment': str(raw.get('client_comment') or '').strip(),
            'rating_text': str(raw.get('rating_text') or raw.get('rating_name') or '').strip(),
            'request_id': raw.get('request_id'),
            'rating_id': raw.get('rating_id'),
            'valuation_request_id': raw.get('valuation_request_id'),
            'channel_name': raw.get('channel_name'),
        }

    @staticmethod
    def _low_rating_public_item(item, viewer_id=None, can_finalize=False):
        public_item = {
            'id': item.get('id'),
            'operator_id': item.get('operator_id'),
            'operator_name': item.get('operator_name') or '',
            'phone_number': item.get('phone_number') or '',
            'phone_normalized': item.get('phone_normalized') or '',
            'taxi_park': item.get('taxi_park') or '',
            'rated_at': item.get('rated_at'),
            'day': item.get('day'),
            'score': item.get('score'),
            'direction_name': item.get('direction_name') or '',
            'department_id': item.get('department_id'),
            'department_name': item.get('department_name') or '',
            'final_status': item.get('final_status'),
            'final_source': item.get('final_source'),
            'final_comment': item.get('final_comment') or '',
            'final_decided_by_name': item.get('final_decided_by_name') or '',
            'final_decided_at': item.get('final_decided_at'),
            'review_count': int(item.get('review_count') or 0),
            'review_state': item.get('review_state') or 'pending',
            'has_review_conflict': bool(item.get('has_review_conflict')),
            'needs_head_decision': bool(item.get('needs_head_decision')),
            'can_finalize': bool(can_finalize and item.get('has_review_conflict')),
            'review_entries': item.get('review_entries') or [],
            'my_review_status': item.get('my_review_status'),
            'my_review_comment': item.get('my_review_comment') or '',
            'my_review_updated_at': item.get('my_review_updated_at'),
            # Кому засчитывается оценка ([{id, name}]) и отличается ли это от
            # того, кому её отдал Chat2Desk.
            'attributed_operators': item.get('attributed_operators') or [],
            'attribution_changed': bool(item.get('attribution_changed')),
            'history_count': int(item.get('history_count') or 0),
        }
        public_item.update(ChatRatingsMixin._low_rating_extract_source_details(item))
        return public_item

    def _attach_low_rating_review_entries_tx(self, cursor, items, viewer_id=None):
        if not items:
            return items
        review_ids = [str(item.get('id')) for item in items if item.get('id')]
        if not review_ids:
            return items

        cursor.execute(
            """
            SELECT review_id::text, status, COUNT(*)
            FROM chat_manager_low_rating_review_entries
            WHERE review_id::text = ANY(%s)
              AND status IS NOT NULL
            GROUP BY review_id, status
            """,
            (review_ids,)
        )
        by_review = {}
        for review_id, status, count in cursor.fetchall() or []:
            bucket = by_review.setdefault(str(review_id), {'review_count': 0, 'status_counts': {}})
            normalized = self._normalize_low_rating_review_status(status)
            bucket['status_counts'][normalized] = int(count or 0)
            bucket['review_count'] += int(count or 0)

        review_entries = {}
        cursor.execute(
            """
            SELECT e.review_id::text, e.reviewer_id, COALESCE(NULLIF(u.name, ''), ''), e.status, e.comment, e.updated_at
            FROM chat_manager_low_rating_review_entries e
            LEFT JOIN users u ON u.id = e.reviewer_id
            WHERE e.review_id::text = ANY(%s)
              AND e.status IS NOT NULL
            ORDER BY e.updated_at ASC NULLS LAST, e.created_at ASC NULLS LAST
            """,
            (review_ids,)
        )
        for review_id, reviewer_id, reviewer_name, status, comment, updated_at in cursor.fetchall() or []:
            normalized = self._normalize_low_rating_review_status(status)
            if not normalized:
                continue
            review_entries.setdefault(str(review_id), []).append({
                'reviewer_id': int(reviewer_id) if reviewer_id is not None else None,
                'reviewer_name': reviewer_name or '',
                'status': normalized,
                'comment': comment or '',
                'updated_at': self._low_rating_db_time_iso(updated_at),
                'is_mine': bool(viewer_id is not None and reviewer_id is not None and int(reviewer_id) == int(viewer_id)),
            })

        my_entries = {}
        if viewer_id is not None:
            cursor.execute(
                """
                SELECT review_id::text, status, comment, updated_at
                FROM chat_manager_low_rating_review_entries
                WHERE review_id::text = ANY(%s)
                  AND reviewer_id = %s
                """,
                (review_ids, int(viewer_id))
            )
            for review_id, status, comment, updated_at in cursor.fetchall() or []:
                my_entries[str(review_id)] = {
                    'my_review_status': self._normalize_low_rating_review_status(status),
                    'my_review_comment': comment or '',
                    'my_review_updated_at': self._low_rating_db_time_iso(updated_at),
                }

        # Сколько событий в истории разбора — чтобы строку «История» показывать
        # только там, где она есть, и не тянуть сам журнал на каждую карточку.
        cursor.execute(
            """
            SELECT review_id::text, COUNT(*)
            FROM chat_manager_low_rating_review_history
            WHERE review_id = ANY(%s::uuid[])
            GROUP BY review_id
            """,
            (review_ids,)
        )
        history_counts = {str(row[0]): int(row[1] or 0) for row in cursor.fetchall() or []}
        self._attach_low_rating_attribution_tx(cursor, items)

        for item in items:
            item['history_count'] = history_counts.get(str(item.get('id')), 0)
            review_id = str(item.get('id'))
            counts = by_review.get(review_id, {'review_count': 0, 'status_counts': {}})
            status_counts = counts.get('status_counts') or {}
            review_count = int(counts.get('review_count') or 0)
            has_review_conflict = bool(
                review_count >= 2
                and int(status_counts.get('valid') or 0) > 0
                and int(status_counts.get('invalid') or 0) > 0
            )
            item['review_count'] = int(counts.get('review_count') or 0)
            item['review_status_counts'] = status_counts
            item.update(my_entries.get(review_id, {
                'my_review_status': None,
                'my_review_comment': '',
                'my_review_updated_at': None,
            }))
            item['has_review_conflict'] = has_review_conflict
            item['review_entries'] = review_entries.get(review_id, [])
            if item.get('final_status'):
                item['review_state'] = 'resolved'
            elif has_review_conflict:
                item['review_state'] = 'conflict'
            else:
                item['review_state'] = 'pending'
            item['needs_head_decision'] = bool(has_review_conflict and not item.get('final_status'))
        return items

    def _resolve_low_rating_final_status_from_entries_tx(self, cursor, review_id, manager_status=None):
        cursor.execute(
            """
            SELECT status, COUNT(*)
            FROM chat_manager_low_rating_review_entries
            WHERE review_id = %s
              AND status IS NOT NULL
            GROUP BY status
            """,
            (review_id,)
        )
        counts = {}
        for status, count in cursor.fetchall() or []:
            normalized = self._normalize_low_rating_review_status(status)
            if normalized:
                counts[normalized] = counts.get(normalized, 0) + int(count or 0)
        review_count = sum(counts.values())
        has_review_conflict = bool(
            review_count >= 2
            and counts.get('valid', 0) > 0
            and counts.get('invalid', 0) > 0
        )
        if review_count >= 2 and len(counts) == 1:
            return next(iter(counts.keys())), 'consensus'
        manager = self._normalize_low_rating_review_status(manager_status)
        if manager and has_review_conflict:
            return manager, 'manager'
        return None, None

    def save_chat_manager_low_ratings(self, low_ratings, imported_by=None, source_batch_id=None):
        if not isinstance(low_ratings, list):
            raise ValueError("low_ratings must be a list")

        rows_by_key = {}
        duplicates_in_batch = 0
        affected_pairs = set()
        imported_by_id = int(imported_by) if imported_by is not None else None
        batch_id = str(source_batch_id) if source_batch_id else None

        for item in low_ratings:
            if not isinstance(item, dict):
                continue
            try:
                operator_id = int(item.get('operator_id'))
                score = float(str(item.get('score')).replace(',', '.'))
            except Exception:
                continue
            if score >= 4:
                continue

            rated_at = self._parse_low_rating_datetime(
                item.get('rated_at') or item.get('created_at') or item.get('date')
            )
            day_raw = item.get('day') or item.get('metric_day') or item.get('date')
            day_obj = None
            if isinstance(day_raw, datetime):
                day_obj = day_raw.date()
            elif isinstance(day_raw, date):
                day_obj = day_raw
            else:
                try:
                    day_obj = datetime.strptime(str(day_raw or '').strip()[:10], '%Y-%m-%d').date()
                except Exception:
                    day_obj = rated_at.date() if rated_at else None
            if day_obj is None:
                continue
            if rated_at is None:
                rated_at = datetime.combine(day_obj, dt_time.min)

            source = str(item.get('source') or 'chat2desk').strip()[:64] or 'chat2desk'
            phone_number = str(item.get('phone_number') or item.get('phone') or '').strip()[:64]
            phone_normalized = self._normalize_low_rating_phone(phone_number)[:32]
            taxi_park = str(item.get('taxi_park') or item.get('taxopark') or item.get('direction') or '').strip()[:255]
            operator_name = str(item.get('operator_name') or '').strip()[:255]
            raw_payload = item.get('raw_payload') if isinstance(item.get('raw_payload'), dict) else dict(item)
            # Пустые поля в payload не записываем. Chat2Desk присылает одну и ту
            # же оценку двумя ответами, и во втором часть полей (например
            # transport) пустая; при склейке по общему ключу пустое значение
            # затёрло бы уже известное. Читатели payload пустую строку и так
            # считают отсутствием значения.
            raw_payload = {
                key: value for key, value in raw_payload.items()
                if value is not None and not (isinstance(value, str) and not value.strip())
            }

            source_key = str(item.get('source_key') or item.get('external_id') or '').strip()
            if not source_key:
                source_key_seed = json.dumps({
                    'source': source,
                    'operator_id': operator_id,
                    'rated_at': rated_at.isoformat() if rated_at else None,
                    'day': day_obj.strftime('%Y-%m-%d'),
                    'score': score,
                    'phone': phone_normalized,
                    'taxi_park': taxi_park,
                    'source_row': item.get('source_row'),
                    'raw': raw_payload,
                }, ensure_ascii=False, sort_keys=True, default=str)
                source_key = uuid.uuid5(uuid.NAMESPACE_URL, source_key_seed).hex
            source_key = source_key[:255]

            # Ключ внутри пачки должен быть уникален. Chat2Desk кладёт одну и ту
            # же оценку в ответ дважды, а INSERT ... ON CONFLICT DO UPDATE, где
            # одна строка задета дважды одной командой, Postgres отклоняет
            # (21000, «cannot affect row a second time»). Поэтому вторую копию
            # сливаем в первую, а не отправляем отдельной строкой.
            previous = rows_by_key.get((source, source_key))
            if previous is not None:
                merged_payload = dict(previous[10])
                merged_payload.update(raw_payload)
                raw_payload = merged_payload
                duplicates_in_batch += 1

            rows_by_key[(source, source_key)] = (
                source,
                source_key,
                operator_id,
                operator_name,
                phone_number,
                phone_normalized,
                taxi_park,
                rated_at,
                day_obj,
                score,
                raw_payload,
                imported_by_id,
                batch_id,
            )
            affected_pairs.add((operator_id, day_obj))

        rows = [
            row[:10] + (Json(row[10]),) + row[11:]
            for row in rows_by_key.values()
        ]
        if not rows:
            return {'processed': 0, 'adjusted_days': 0}
        if duplicates_in_batch:
            logging.warning(
                "Chat2Desk: %s копий одной оценки в одной пачке низких оценок — склеены по source_key",
                duplicates_in_batch
            )

        batch_sources = [row[0] for row in rows]
        batch_keys = [row[1] for row in rows]

        def _collect_stored_pairs(cursor, with_owner):
            # Пересинк может сдвинуть день оценки или сменить оператора: пересчитать
            # надо и прежний день, и дни всех, кому оценка засчитана решением.
            cursor.execute(
                """
                SELECT lr.operator_id, lr.day, lr.attributed_operator_ids
                FROM chat_manager_low_rating_reviews lr
                JOIN unnest(%s::text[], %s::text[]) AS k(source, source_key)
                  ON lr.source = k.source AND lr.source_key = k.source_key
                WHERE %s OR lr.attributed_operator_ids IS NOT NULL
                """,
                (batch_sources, batch_keys, bool(with_owner))
            )
            for owner_id, stored_day, attributed in cursor.fetchall() or []:
                if with_owner and owner_id is not None:
                    affected_pairs.add((int(owner_id), stored_day))
                for value in attributed or []:
                    if value is not None:
                        affected_pairs.add((int(value), stored_day))

        with self._get_cursor() as cursor:
            _collect_stored_pairs(cursor, with_owner=True)
            execute_values(
                cursor,
                """
                INSERT INTO chat_manager_low_rating_reviews (
                    source, source_key, operator_id, operator_name, phone_number,
                    phone_normalized, taxi_park, rated_at, day, score, raw_payload,
                    imported_by, source_batch_id
                )
                VALUES %s
                ON CONFLICT (source, source_key)
                DO UPDATE SET
                    operator_id = EXCLUDED.operator_id,
                    operator_name = COALESCE(NULLIF(EXCLUDED.operator_name, ''), chat_manager_low_rating_reviews.operator_name),
                    phone_number = COALESCE(NULLIF(EXCLUDED.phone_number, ''), chat_manager_low_rating_reviews.phone_number),
                    phone_normalized = COALESCE(NULLIF(EXCLUDED.phone_normalized, ''), chat_manager_low_rating_reviews.phone_normalized),
                    taxi_park = COALESCE(NULLIF(EXCLUDED.taxi_park, ''), chat_manager_low_rating_reviews.taxi_park),
                    rated_at = EXCLUDED.rated_at,
                    day = EXCLUDED.day,
                    score = EXCLUDED.score,
                    -- Дополняем, а не заменяем: во втором ответе вендора часть
                    -- полей отсутствует, и замена целиком их бы потеряла.
                    raw_payload = chat_manager_low_rating_reviews.raw_payload || EXCLUDED.raw_payload,
                    imported_by = EXCLUDED.imported_by,
                    source_batch_id = COALESCE(EXCLUDED.source_batch_id, chat_manager_low_rating_reviews.source_batch_id),
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
                page_size=1000
            )
            _collect_stored_pairs(cursor, with_owner=False)
            adjustment = self._recalculate_chat_manager_score_adjustments_tx(cursor, affected_pairs)

        return {
            'processed': len(rows),
            'adjusted_days': int((adjustment or {}).get('adjusted_days') or 0),
            'aggregations': (adjustment or {}).get('aggregations') or {},
        }

    def list_chat_manager_low_rating_reviews(
        self,
        month,
        department_id=None,
        operator_id=None,
        status=None,
        limit=2000,
        page=1,
        per_page=12,
        viewer_id=None,
        can_finalize=False,
        start=None,
        end=None,
        search=None,
        paginate=True,
        unreviewed=False,
    ):
        def _coerce_day(value):
            text = str(value or '').strip()
            if not text:
                return None
            try:
                return date.fromisoformat(text[:10])
            except Exception:
                return None

        period_start = _coerce_day(start)
        period_end = _coerce_day(end)
        if period_start and period_end:
            # Произвольный период (кастомный пикер). Переставляем границы при инверсии.
            if period_start > period_end:
                period_start, period_end = period_end, period_start
        else:
            try:
                year, mon = map(int, str(month or '').split('-'))
                period_start = date(year, mon, 1)
                period_end = date(year, mon, calendar.monthrange(year, mon)[1])
            except Exception as exc:
                raise ValueError("month must be in YYYY-MM format") from exc

        where = ["lr.day >= %s", "lr.day <= %s"]
        params = [period_start, period_end]
        if department_id not in (None, ''):
            where.append("u.department_id = %s")
            params.append(int(department_id))
        if operator_id not in (None, ''):
            # Оценки оператора — и отданные ему Chat2Desk, и засчитанные решением.
            where.append("(lr.operator_id = %s OR %s = ANY(lr.attributed_operator_ids))")
            params.extend([int(operator_id), int(operator_id)])

        safe_limit = max(1, min(10000, int(limit or 2000)))
        safe_page = max(1, int(page or 1))
        safe_per_page = max(1, min(50, int(per_page or 12)))
        with self._get_cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    lr.id, lr.source, lr.source_key, lr.operator_id,
                    COALESCE(NULLIF(lr.operator_name, ''), u.name, '') AS operator_name,
                    lr.phone_number, lr.phone_normalized, lr.taxi_park,
                    lr.rated_at, lr.day, lr.score, lr.raw_payload,
                    lr.imported_by, lr.source_batch_id,
                    lr.arai_status, lr.arai_comment, lr.arai_updated_by, au.name, lr.arai_updated_at,
                    lr.zhanna_status, lr.zhanna_comment, lr.zhanna_updated_by, zu.name, lr.zhanna_updated_at,
                    lr.head_status, lr.head_comment, lr.head_updated_by, hu.name, lr.head_updated_at,
                    lr.final_status, lr.final_source, lr.final_comment, lr.final_decided_by, fu.name, lr.final_decided_at,
                    lr.created_at, lr.updated_at,
                    d.name AS direction_name,
                    dep.id AS department_id,
                    dep.name AS department_name,
                    lr.attributed_operator_ids
                FROM chat_manager_low_rating_reviews lr
                JOIN users u ON u.id = lr.operator_id
                LEFT JOIN directions d ON d.id = u.direction_id
                LEFT JOIN departments dep ON dep.id = u.department_id
                LEFT JOIN users au ON au.id = lr.arai_updated_by
                LEFT JOIN users zu ON zu.id = lr.zhanna_updated_by
                LEFT JOIN users hu ON hu.id = lr.head_updated_by
                LEFT JOIN users fu ON fu.id = lr.final_decided_by
                WHERE {' AND '.join(where)}
                ORDER BY lr.rated_at DESC NULLS LAST, lr.day DESC, lr.created_at DESC
                LIMIT %s
                """,
                params + [safe_limit]
            )
            all_rows = [self._low_rating_row_to_dict(row) for row in cursor.fetchall() or []]
            self._attach_low_rating_review_entries_tx(cursor, all_rows, viewer_id=viewer_id)

        # Поиск среди всех оценок периода (по ФИО / телефону / таксопарку / направлению).
        search_text = str(search or '').strip().lower()
        if search_text:
            search_digits = re.sub(r'\D+', '', search_text)

            def _matches_search(item):
                haystack = ' '.join(str(item.get(key) or '') for key in (
                    'operator_name', 'phone_number', 'phone_normalized',
                    'taxi_park', 'direction_name', 'department_name'
                )).lower()
                # Засчитанную по решению ищут и по тому, кому её засчитали.
                haystack += ' ' + ' '.join(
                    str(op.get('name') or '') for op in (item.get('attributed_operators') or [])
                ).lower()
                if search_text in haystack:
                    return True
                if search_digits:
                    phone_digits = re.sub(r'\D+', '', str(item.get('phone_normalized') or item.get('phone_number') or ''))
                    if phone_digits and search_digits in phone_digits:
                        return True
                return False

            all_rows = [item for item in all_rows if _matches_search(item)]

        summary = {
            'total': len(all_rows),
            'pending': 0,
            'conflict': 0,
            'valid': 0,
            'invalid': 0,
            'unreviewed': 0,
        }
        for item in all_rows:
            state = item.get('review_state')
            final_status = item.get('final_status')
            if final_status == 'valid':
                summary['valid'] += 1
            elif final_status == 'invalid':
                summary['invalid'] += 1
            elif state == 'conflict':
                summary['conflict'] += 1
            else:
                summary['pending'] += 1
            # Сколько из «К проверке» (без финального вердикта) ещё не проверил текущий пользователь.
            if not final_status and not item.get('my_review_status'):
                summary['unreviewed'] += 1

        status_key = str(status or 'attention').strip().lower()
        if status_key in ('all', ''):
            rows = all_rows
        elif status_key == 'attention':
            rows = [item for item in all_rows if not item.get('final_status')]
        elif status_key == 'mine':
            rows = [item for item in all_rows if item.get('my_review_status')]
        elif status_key in ('pending', 'conflict'):
            rows = [item for item in all_rows if item.get('review_state') == status_key]
        elif status_key in ('valid', 'invalid'):
            rows = [item for item in all_rows if item.get('final_status') == status_key]
        else:
            rows = all_rows

        # Доп. флаг «не проверенные мной»: оставляем только строки без личного вердикта зрителя.
        if unreviewed:
            rows = [item for item in rows if not item.get('my_review_status')]

        total_filtered = len(rows)
        if paginate:
            total_pages = max(1, int(math.ceil(total_filtered / safe_per_page)))
            safe_page = min(safe_page, total_pages)
            start_idx = (safe_page - 1) * safe_per_page
            page_rows = rows[start_idx:start_idx + safe_per_page]
        else:
            # Режим выгрузки: возвращаем все отфильтрованные строки без постраничной нарезки.
            total_pages = 1
            safe_page = 1
            page_rows = rows

        return {
            'month': period_start.strftime('%Y-%m'),
            'start': period_start.isoformat(),
            'end': period_end.isoformat(),
            'rows': [
                self._low_rating_public_item(item, viewer_id=viewer_id, can_finalize=can_finalize)
                for item in page_rows
            ],
            'summary': summary,
            'pagination': {
                'page': safe_page,
                'per_page': safe_per_page,
                'total': total_filtered,
                'total_pages': total_pages,
            },
        }

    @staticmethod
    def _low_rating_operator_item(item, viewer_id=None):
        """Строка низкой оценки в том виде, в каком её видит сам оператор.

        Отдаём только итог проверки: пока вердикта нет — «на проверке» без
        промежуточных голосов проверяющих (разногласие ОКК — их внутренняя
        кухня). Когда решение принято, показываем и обоснование каждого
        проверяющего: именно оно объясняет, почему оценка осталась или снята.

        reassigned — оценка обоснованная, но по решению проверяющих засчитана
        другому менеджеру, а не зрителю: на его показатель она не влияет. Кому
        именно — не сообщаем, это разбор чужих показателей."""
        resolved = bool(item.get('final_status'))
        reassigned = False
        if viewer_id is not None and item.get('final_status') == 'valid':
            reassigned = int(viewer_id) not in ChatRatingsMixin._low_rating_effective_attribution(
                item.get('operator_id'), item.get('attributed_operator_ids')
            )
        public_item = {
            'id': item.get('id'),
            'rated_at': item.get('rated_at'),
            'day': item.get('day'),
            'score': item.get('score'),
            'taxi_park': item.get('taxi_park') or '',
            'phone_number': item.get('phone_number') or item.get('phone_normalized') or '',
            'final_status': item.get('final_status'),
            'final_source': item.get('final_source'),
            'final_comment': item.get('final_comment') or '',
            'final_decided_at': item.get('final_decided_at'),
            'state': 'resolved' if resolved else 'pending',
            'reassigned': reassigned,
            'decisions': [
                {
                    'reviewer_name': entry.get('reviewer_name') or 'Проверяющий',
                    'status': entry.get('status'),
                    'comment': entry.get('comment') or '',
                    'updated_at': entry.get('updated_at'),
                }
                for entry in (item.get('review_entries') or [])
            ] if resolved else [],
        }
        source = ChatRatingsMixin._low_rating_extract_source_details(item)
        public_item['client_comment'] = source.get('client_comment') or ''
        public_item['rating_text'] = source.get('rating_text') or ''
        public_item['channel_name'] = source.get('channel_name') or ''
        return public_item

    def list_operator_low_rating_reviews(self, operator_id, month=None, start=None, end=None):
        """Низкие оценки одного оператора с итогами проверки — для раздела «Мои оценки».

        Отдельный метод, а не list_chat_manager_low_rating_reviews с фильтром:
        оператору не положены поля проверяющей стороны (личный вердикт зрителя,
        флаги финализации, отдел). Сюда же попадают чужие оценки, засчитанные
        оператору итоговым решением (задача #286), а его собственная, отданная
        по итогу другому менеджеру, помечается reassigned."""
        def _coerce_day(value):
            text = str(value or '').strip()
            if not text:
                return None
            try:
                return date.fromisoformat(text[:10])
            except Exception:
                return None

        period_start = _coerce_day(start)
        period_end = _coerce_day(end)
        if period_start and period_end:
            if period_start > period_end:
                period_start, period_end = period_end, period_start
        else:
            try:
                year, mon = map(int, str(month or '').split('-'))
                period_start = date(year, mon, 1)
                period_end = date(year, mon, calendar.monthrange(year, mon)[1])
            except Exception as exc:
                raise ValueError("month must be in YYYY-MM format") from exc

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    lr.id, lr.source, lr.source_key, lr.operator_id,
                    COALESCE(NULLIF(lr.operator_name, ''), u.name, '') AS operator_name,
                    lr.phone_number, lr.phone_normalized, lr.taxi_park,
                    lr.rated_at, lr.day, lr.score, lr.raw_payload,
                    lr.imported_by, lr.source_batch_id,
                    lr.arai_status, lr.arai_comment, lr.arai_updated_by, NULL, lr.arai_updated_at,
                    lr.zhanna_status, lr.zhanna_comment, lr.zhanna_updated_by, NULL, lr.zhanna_updated_at,
                    lr.head_status, lr.head_comment, lr.head_updated_by, NULL, lr.head_updated_at,
                    lr.final_status, lr.final_source, lr.final_comment, lr.final_decided_by, NULL, lr.final_decided_at,
                    lr.created_at, lr.updated_at,
                    NULL AS direction_name,
                    u.department_id AS department_id,
                    NULL AS department_name,
                    lr.attributed_operator_ids
                FROM chat_manager_low_rating_reviews lr
                JOIN users u ON u.id = lr.operator_id
                WHERE lr.day >= %s
                  AND lr.day <= %s
                  AND (
                      lr.operator_id = %s
                      -- Засчитанная ему чужая оценка — только когда она на нём
                      -- по итогу: пока разбор идёт, она всё ещё у того, кому её
                      -- отдал Chat2Desk, и пугать ею другого рано.
                      OR (lr.final_status = 'valid' AND %s = ANY(lr.attributed_operator_ids))
                  )
                ORDER BY lr.rated_at DESC NULLS LAST, lr.day DESC, lr.created_at DESC
                """,
                (period_start, period_end, int(operator_id), int(operator_id))
            )
            rows = [self._low_rating_row_to_dict(row) for row in cursor.fetchall() or []]
            self._attach_low_rating_review_entries_tx(cursor, rows)

        public_rows = [self._low_rating_operator_item(item, viewer_id=operator_id) for item in rows]
        summary = {'total': len(public_rows), 'pending': 0, 'valid': 0, 'invalid': 0, 'reassigned': 0}
        for item in public_rows:
            final_status = item.get('final_status')
            if item.get('reassigned'):
                summary['reassigned'] += 1
            elif final_status in ('valid', 'invalid'):
                summary[final_status] += 1
            else:
                summary['pending'] += 1

        return {
            'month': period_start.strftime('%Y-%m'),
            'start': period_start.isoformat(),
            'end': period_end.isoformat(),
            'rows': public_rows,
            'summary': summary,
        }

    def get_chat_manager_low_rating_review(self, review_id, viewer_id=None, public=False, can_finalize=False):
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    lr.id, lr.source, lr.source_key, lr.operator_id,
                    COALESCE(NULLIF(lr.operator_name, ''), u.name, '') AS operator_name,
                    lr.phone_number, lr.phone_normalized, lr.taxi_park,
                    lr.rated_at, lr.day, lr.score, lr.raw_payload,
                    lr.imported_by, lr.source_batch_id,
                    lr.arai_status, lr.arai_comment, lr.arai_updated_by, au.name, lr.arai_updated_at,
                    lr.zhanna_status, lr.zhanna_comment, lr.zhanna_updated_by, zu.name, lr.zhanna_updated_at,
                    lr.head_status, lr.head_comment, lr.head_updated_by, hu.name, lr.head_updated_at,
                    lr.final_status, lr.final_source, lr.final_comment, lr.final_decided_by, fu.name, lr.final_decided_at,
                    lr.created_at, lr.updated_at,
                    d.name AS direction_name,
                    dep.id AS department_id,
                    dep.name AS department_name,
                    lr.attributed_operator_ids
                FROM chat_manager_low_rating_reviews lr
                JOIN users u ON u.id = lr.operator_id
                LEFT JOIN directions d ON d.id = u.direction_id
                LEFT JOIN departments dep ON dep.id = u.department_id
                LEFT JOIN users au ON au.id = lr.arai_updated_by
                LEFT JOIN users zu ON zu.id = lr.zhanna_updated_by
                LEFT JOIN users hu ON hu.id = lr.head_updated_by
                LEFT JOIN users fu ON fu.id = lr.final_decided_by
                WHERE lr.id = %s
                """,
                (review_id,)
            )
            row = cursor.fetchone()
            item = self._low_rating_row_to_dict(row) if row else None
            if item is not None:
                self._attach_low_rating_review_entries_tx(cursor, [item], viewer_id=viewer_id)
        if not item:
            return None
        if public:
            return self._low_rating_public_item(item, viewer_id=viewer_id, can_finalize=can_finalize)
        return item

    def _low_rating_next_attribution_tx(self, cursor, status, operator_ids, operator_id,
                                        stored_attribution, department_scope_id=None,
                                        base_operator_ids=None):
        """Атрибуция после сохранения вердикта (задача #286).

        Меняется только вместе с «обоснованно»: необоснованная не засчитывается
        никому, и отметки менеджеров при ней не имеют смысла — прежнее решение
        остаётся как было. operator_ids=None — отметки не трогали (или старый
        клиент): атрибуцию не меняем. Обоснованную с пустым списком сохранить нельзя.

        base_operator_ids — какие отметки проверяющий видел, когда выносил
        «обоснованно». Решение об атрибуции одно на разбор, и второй проверяющий
        со списком, загруженным до чужого сохранения, молча стёр бы его — поэтому
        при расхождении сохранение отклоняется, а клиент показывает свежие отметки."""
        if status != 'valid':
            return stored_attribution
        if base_operator_ids is not None:
            current = self._low_rating_effective_attribution(operator_id, stored_attribution)
            seen = self._low_rating_effective_attribution(operator_id, base_operator_ids)
            if seen != current:
                raise LowRatingAttributionConflict(self.LOW_RATING_ATTRIBUTION_CHANGED)
        if operator_ids is None:
            return stored_attribution
        if not operator_ids:
            raise ValueError(self.LOW_RATING_ATTRIBUTION_REQUIRED)
        self._validate_low_rating_attribution_tx(
            cursor, operator_ids, operator_id, department_scope_id, stored_ids=stored_attribution)
        return self._low_rating_attribution_to_store(operator_id, operator_ids)

    def save_chat_manager_low_rating_personal_review(self, review_id, reviewer_id, status, comment='',
                                                     operator_ids=None, department_scope_id=None,
                                                     base_operator_ids=None):
        normalized_status = self._normalize_low_rating_review_status(status)
        clean_comment = str(comment or '').strip()[:4000]
        reviewer_id_int = int(reviewer_id)
        requested_ids = self._normalize_low_rating_operator_ids(operator_ids)
        seen_ids = self._normalize_low_rating_operator_ids(base_operator_ids)

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT operator_id, day, head_status, head_comment, final_status, attributed_operator_ids
                FROM chat_manager_low_rating_reviews
                WHERE id = %s
                FOR UPDATE
                """,
                (review_id,)
            )
            current = cursor.fetchone()
            if not current:
                return None
            operator_id, day_obj, head_status, head_comment, prev_final_status, stored_attribution = current
            prev_attribution = self._low_rating_effective_attribution(operator_id, stored_attribution)
            next_stored = self._low_rating_next_attribution_tx(
                cursor, normalized_status, requested_ids, operator_id, stored_attribution,
                department_scope_id=department_scope_id, base_operator_ids=seen_ids
            )
            next_attribution = self._low_rating_effective_attribution(operator_id, next_stored)

            cursor.execute(
                """
                SELECT status, comment
                FROM chat_manager_low_rating_review_entries
                WHERE review_id = %s AND reviewer_id = %s
                """,
                (review_id, reviewer_id_int)
            )
            prev_entry = cursor.fetchone()
            prev_status = self._normalize_low_rating_review_status(prev_entry[0]) if prev_entry else None
            prev_comment = (prev_entry[1] or '') if prev_entry else ''

            cursor.execute(
                """
                INSERT INTO chat_manager_low_rating_review_entries (
                    review_id, reviewer_id, status, comment, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (review_id, reviewer_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    comment = EXCLUDED.comment,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (review_id, reviewer_id_int, normalized_status, clean_comment)
            )
            final_status, final_source = self._resolve_low_rating_final_status_from_entries_tx(
                cursor,
                review_id,
                manager_status=head_status
            )
            final_comment = head_comment if final_source == 'manager' else ''
            cursor.execute(
                """
                UPDATE chat_manager_low_rating_reviews
                SET final_status = %s,
                    final_source = %s,
                    final_comment = %s,
                    final_decided_by = %s,
                    final_decided_at = CASE WHEN %s IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
                    attributed_operator_ids = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    final_status, final_source, final_comment or '',
                    reviewer_id_int if final_status else None, final_status,
                    next_stored, review_id,
                )
            )

            attribution_changed = next_attribution != prev_attribution
            comment_changed = clean_comment != prev_comment
            if (normalized_status != prev_status or comment_changed or attribution_changed
                    or final_status != prev_final_status):
                self._log_low_rating_history_tx(
                    cursor, review_id, reviewer_id_int, 'review',
                    status=normalized_status,
                    prev_status=prev_status,
                    comment=clean_comment if comment_changed else None,
                    operator_ids=next_attribution if attribution_changed else None,
                    prev_operator_ids=prev_attribution if attribution_changed else None,
                    final_status=final_status,
                    prev_final_status=prev_final_status,
                )
            adjustment = self._recalculate_chat_manager_score_adjustments_tx(
                cursor,
                self._low_rating_recalc_pairs(operator_id, day_obj, prev_attribution, next_attribution)
            )

        item = self.get_chat_manager_low_rating_review(review_id, viewer_id=reviewer_id_int)
        if item is not None:
            item['adjustment'] = adjustment
        return item

    def finalize_chat_manager_low_rating_review(self, review_id, status, comment='', updated_by=None,
                                                operator_ids=None, department_scope_id=None,
                                                base_operator_ids=None):
        normalized_status = self._normalize_low_rating_review_status(status)
        if not normalized_status:
            raise ValueError("final status is required")
        clean_comment = str(comment or '').strip()[:4000]
        updated_by_id = int(updated_by) if updated_by is not None else None
        requested_ids = self._normalize_low_rating_operator_ids(operator_ids)
        seen_ids = self._normalize_low_rating_operator_ids(base_operator_ids)

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT operator_id, day, head_status, head_comment, final_status, attributed_operator_ids
                FROM chat_manager_low_rating_reviews
                WHERE id = %s
                FOR UPDATE
                """,
                (review_id,)
            )
            current = cursor.fetchone()
            if not current:
                return None
            (
                operator_id, day_obj, prev_head_status, prev_head_comment,
                prev_final_status, stored_attribution,
            ) = current
            prev_attribution = self._low_rating_effective_attribution(operator_id, stored_attribution)

            cursor.execute(
                """
                SELECT status, COUNT(*)
                FROM chat_manager_low_rating_review_entries
                WHERE review_id = %s
                  AND status IS NOT NULL
                GROUP BY status
                """,
                (review_id,)
            )
            counts = {}
            for status_value, count in cursor.fetchall() or []:
                normalized = self._normalize_low_rating_review_status(status_value)
                if normalized:
                    counts[normalized] = counts.get(normalized, 0) + int(count or 0)
            review_count = sum(counts.values())
            has_review_conflict = bool(
                review_count >= 2
                and counts.get('valid', 0) > 0
                and counts.get('invalid', 0) > 0
            )
            if not has_review_conflict:
                raise ValueError("Final decision is available only when two reviews disagree")

            next_stored = self._low_rating_next_attribution_tx(
                cursor, normalized_status, requested_ids, operator_id, stored_attribution,
                department_scope_id=department_scope_id, base_operator_ids=seen_ids
            )
            next_attribution = self._low_rating_effective_attribution(operator_id, next_stored)

            final_status, final_source = self._resolve_low_rating_final_status_from_entries_tx(
                cursor,
                review_id,
                manager_status=normalized_status
            )
            final_comment = clean_comment if final_source == 'manager' else ''
            cursor.execute(
                """
                UPDATE chat_manager_low_rating_reviews
                SET head_status = %s,
                    head_comment = %s,
                    head_updated_by = %s,
                    head_updated_at = CURRENT_TIMESTAMP,
                    final_status = %s,
                    final_source = %s,
                    final_comment = %s,
                    final_decided_by = %s,
                    final_decided_at = CASE WHEN %s IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
                    attributed_operator_ids = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (
                    normalized_status,
                    clean_comment,
                    updated_by_id,
                    final_status,
                    final_source,
                    final_comment,
                    updated_by_id if final_status else None,
                    final_status,
                    next_stored,
                    review_id,
                )
            )

            attribution_changed = next_attribution != prev_attribution
            comment_changed = clean_comment != (prev_head_comment or '')
            prev_head = self._normalize_low_rating_review_status(prev_head_status)
            if (normalized_status != prev_head or comment_changed or attribution_changed
                    or final_status != prev_final_status):
                self._log_low_rating_history_tx(
                    cursor, review_id, updated_by_id, 'final',
                    status=normalized_status,
                    prev_status=prev_head,
                    comment=clean_comment if comment_changed else None,
                    operator_ids=next_attribution if attribution_changed else None,
                    prev_operator_ids=prev_attribution if attribution_changed else None,
                    final_status=final_status,
                    prev_final_status=prev_final_status,
                )
            adjustment = self._recalculate_chat_manager_score_adjustments_tx(
                cursor,
                self._low_rating_recalc_pairs(operator_id, day_obj, prev_attribution, next_attribution)
            )

        item = self.get_chat_manager_low_rating_review(review_id, viewer_id=updated_by_id)
        if item is not None:
            item['adjustment'] = adjustment
        return item

    def update_chat_manager_low_rating_review(self, review_id, reviewer, status, comment='', updated_by=None):
        reviewer_key = str(reviewer or '').strip().lower()
        field_map = {
            'arai': ('arai_status', 'arai_comment', 'arai_updated_by', 'arai_updated_at'),
            'zhanna': ('zhanna_status', 'zhanna_comment', 'zhanna_updated_by', 'zhanna_updated_at'),
            'head': ('head_status', 'head_comment', 'head_updated_by', 'head_updated_at'),
        }
        if reviewer_key not in field_map:
            raise ValueError("reviewer must be arai, zhanna or head")
        normalized_status = self._normalize_low_rating_review_status(status)
        clean_comment = str(comment or '').strip()[:4000]
        updated_by_id = int(updated_by) if updated_by is not None else None
        status_col, comment_col, by_col, at_col = field_map[reviewer_key]

        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT operator_id, day, arai_status, zhanna_status, head_status, attributed_operator_ids
                FROM chat_manager_low_rating_reviews
                WHERE id = %s
                FOR UPDATE
                """,
                (review_id,)
            )
            current = cursor.fetchone()
            if not current:
                return None
            operator_id, day_obj, arai_status, zhanna_status, head_status, stored_attribution = current

            cursor.execute(
                f"""
                UPDATE chat_manager_low_rating_reviews
                SET {status_col} = %s,
                    {comment_col} = %s,
                    {by_col} = %s,
                    {at_col} = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (normalized_status, clean_comment, updated_by_id, review_id)
            )

            next_statuses = {
                'arai': arai_status,
                'zhanna': zhanna_status,
                'head': head_status,
            }
            next_statuses[reviewer_key] = normalized_status
            final_status, final_source = self._resolve_low_rating_final_status(
                next_statuses.get('arai'),
                next_statuses.get('zhanna'),
                next_statuses.get('head')
            )
            cursor.execute(
                """
                UPDATE chat_manager_low_rating_reviews
                SET final_status = %s,
                    final_source = %s,
                    final_decided_by = %s,
                    final_decided_at = CASE WHEN %s IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (final_status, final_source, updated_by_id if final_status else None, final_status, review_id)
            )
            adjustment = self._recalculate_chat_manager_score_adjustments_tx(
                cursor,
                self._low_rating_recalc_pairs(
                    operator_id, day_obj,
                    self._low_rating_effective_attribution(operator_id, stored_attribution)
                )
            )

        item = self.get_chat_manager_low_rating_review(review_id)
        if item is not None:
            item['adjustment'] = adjustment
        return item

    @staticmethod
    def _parse_chat_metric_surge_month(month):
        text = str(month or '').strip()
        if not re.fullmatch(r'\d{4}-\d{2}', text):
            raise ValueError("month must be in YYYY-MM format")
        try:
            return datetime.strptime(text, '%Y-%m').date().replace(day=1)
        except Exception:
            raise ValueError("month must be in YYYY-MM format")

    @staticmethod
    def _parse_chat_metric_surge_dt(value, field_name='datetime'):
        if isinstance(value, datetime):
            return value.replace(tzinfo=None)
        text = str(value or '').strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        text = text.replace('Z', '')
        for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                pass
        try:
            return datetime.fromisoformat(text).replace(tzinfo=None)
        except Exception:
            raise ValueError(f"{field_name} must be a valid datetime")

    @staticmethod
    def _format_chat_metric_surge_dt(value):
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%dT%H:%M')
        return ''

    def get_chat_metric_surge_windows(self, month):
        month_start = self._parse_chat_metric_surge_month(month)
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    id,
                    month,
                    start_at,
                    end_at,
                    description,
                    created_by,
                    updated_by,
                    created_at,
                    updated_at
                FROM chat_metric_surge_windows
                WHERE month = %s
                ORDER BY start_at ASC, end_at ASC, id ASC
            """, (month_start,))
            rows = cursor.fetchall() or []

        windows = []
        for row in rows:
            windows.append({
                'id': str(row[0]),
                'month': row[1].strftime('%Y-%m') if row[1] else month_start.strftime('%Y-%m'),
                'start': self._format_chat_metric_surge_dt(row[2]),
                'end': self._format_chat_metric_surge_dt(row[3]),
                'description': row[4] or '',
                'created_by': row[5],
                'updated_by': row[6],
                'created_at': row[7].isoformat() if row[7] else None,
                'updated_at': row[8].isoformat() if row[8] else None,
            })
        return {
            'month': month_start.strftime('%Y-%m'),
            'windows': windows,
        }

    def save_chat_metric_surge_windows(self, month, windows, updated_by=None):
        month_start = self._parse_chat_metric_surge_month(month)
        if not isinstance(windows, list):
            raise ValueError("windows must be a list")

        rows = []
        updated_by_id = int(updated_by) if updated_by is not None else None
        for item in windows:
            if not isinstance(item, dict):
                continue
            start_dt = self._parse_chat_metric_surge_dt(item.get('start'), 'start')
            end_dt = self._parse_chat_metric_surge_dt(item.get('end'), 'end')
            if end_dt < start_dt:
                start_dt, end_dt = end_dt, start_dt
            description = str(
                item.get('description')
                if item.get('description') is not None
                else item.get('comment') if item.get('comment') is not None else ''
            ).strip()[:2000]
            rows.append((month_start, start_dt, end_dt, description, updated_by_id, updated_by_id))

        with self._get_cursor() as cursor:
            cursor.execute("DELETE FROM chat_metric_surge_windows WHERE month = %s", (month_start,))
            if rows:
                execute_values(
                    cursor,
                    """
                    INSERT INTO chat_metric_surge_windows (
                        month,
                        start_at,
                        end_at,
                        description,
                        created_by,
                        updated_by
                    )
                    VALUES %s
                    """,
                    rows,
                    page_size=500
                )

        return self.get_chat_metric_surge_windows(month_start.strftime('%Y-%m'))

    def save_chat_manager_daily_metrics(self, metrics, imported_by=None, preserve_missing=False, update_fields=None):
        """Сохраняет дневные метрики чат-менеджеров (upsert по (operator_id, day)).

        update_fields: если задан (множество имён колонок из {chats_count,
        whatsapp_chats_count, name_requests_chats_count, avg_score,
        avg_response_time_seconds, transfer_chat_count, score_sum, score_count}) — на конфликте
        ПЕРЕЗАПИСЫВАЮТСЯ только эти колонки (остальные метрики дня сохраняются нетронутыми).
        Это нужно для пер-форматного импорта: отчёт по оценкам не должен обнулять чаты и т.п.
        Для двух отчётов количества чатов обновляется только своя составляющая, а chats_count
        пересчитывается как whatsapp_chats_count + name_requests_chats_count.
        Если None — прежнее поведение (обновляются все метрики; preserve_missing влияет на
        avg_score/avg_response_time_seconds; score_sum/score_count сохраняются при отсутствии)."""
        if not isinstance(metrics, list):
            raise ValueError("metrics must be a list")

        _ALLOWED_UPDATE_FIELDS = {
            'chats_count', 'avg_score', 'avg_response_time_seconds',
            'transfer_chat_count', 'score_sum', 'score_count',
            'whatsapp_chats_count', 'name_requests_chats_count',
        }
        _CHAT_COUNT_COMPONENT_FIELDS = {'whatsapp_chats_count', 'name_requests_chats_count'}
        _PRESERVE_WHEN_MISSING_FIELDS = {
            'avg_score', 'avg_response_time_seconds', 'score_sum', 'score_count',
            'whatsapp_chats_count', 'name_requests_chats_count',
        }
        update_set = None
        if update_fields is not None:
            update_set = {str(f) for f in update_fields if str(f) in _ALLOWED_UPDATE_FIELDS}
            if not update_set:
                raise ValueError("update_fields must contain at least one valid metric column")

        rows = []
        batch_id = uuid.uuid4()
        imported_by_id = int(imported_by) if imported_by is not None else None

        for item in metrics:
            if not isinstance(item, dict):
                continue
            try:
                operator_id = int(item.get('operator_id'))
            except Exception:
                continue

            day_raw = item.get('day') or item.get('date')
            if isinstance(day_raw, datetime):
                day_obj = day_raw.date()
            elif isinstance(day_raw, date):
                day_obj = day_raw
            else:
                try:
                    day_obj = datetime.strptime(str(day_raw or '').strip(), '%Y-%m-%d').date()
                except Exception:
                    continue

            def _optional_float(raw):
                if raw in (None, ''):
                    return None
                try:
                    value = float(str(raw).replace(',', '.'))
                except Exception:
                    return None
                return value if math.isfinite(value) else None

            def _optional_int(raw):
                if raw in (None, ''):
                    return 0
                try:
                    return max(0, int(round(float(str(raw).replace(',', '.')))))
                except Exception:
                    return 0

            def _optional_int_or_none(raw):
                if raw in (None, ''):
                    return None
                try:
                    return max(0, int(round(float(str(raw).replace(',', '.')))))
                except Exception:
                    return None

            avg_response_seconds = _optional_float(
                item.get('avg_response_time_seconds')
                if item.get('avg_response_time_seconds') not in (None, '')
                else item.get('response_time_seconds')
            )
            avg_response_minutes = _optional_float(
                item.get('avg_response_time_minutes')
                if item.get('avg_response_time_minutes') not in (None, '')
                else item.get('response_time_minutes')
            )
            if avg_response_seconds is None and avg_response_minutes is not None:
                avg_response_seconds = avg_response_minutes * 60.0

            score_sum_val = _optional_float(item.get('score_sum'))
            score_count_val = _optional_int_or_none(item.get('score_count'))
            avg_score_val = _optional_float(item.get('avg_score') if item.get('avg_score') not in (None, '') else item.get('score'))
            # Если оценка пришла как сумма+количество (импорт отчёта), avg = sum/count.
            if avg_score_val is None and score_sum_val is not None and score_count_val:
                avg_score_val = round(score_sum_val / score_count_val, 4)
            raw_score_sum_val = score_sum_val if 'score_sum' in item else None
            raw_score_count_val = score_count_val if 'score_count' in item else None

            whatsapp_chats_val = _optional_int(item.get('whatsapp_chats_count'))
            name_requests_chats_val = _optional_int(item.get('name_requests_chats_count'))

            rows.append((
                operator_id,
                day_obj,
                _optional_int(item.get('chats_count') if item.get('chats_count') not in (None, '') else item.get('chats')),
                avg_score_val,
                avg_response_seconds,
                _optional_int(item.get('transfer_chat_count') if item.get('transfer_chat_count') not in (None, '') else item.get('transfers')),
                imported_by_id,
                str(batch_id),
                Json(item),
                score_sum_val,
                score_count_val,
                whatsapp_chats_val,
                name_requests_chats_val,
                raw_score_sum_val,
                raw_score_count_val,
            ))

        if not rows:
            return {'batch_id': str(batch_id), 'processed': 0}

        if update_set is not None:
            # Пер-форматный импорт: перезаписываем ТОЛЬКО указанные колонки (replace),
            # остальные метрики дня остаются нетронутыми (другой отчёт их не обнуляет).
            component_updates = update_set & _CHAT_COUNT_COMPONENT_FIELDS

            def _update_expr(col):
                if col in _PRESERVE_WHEN_MISSING_FIELDS:
                    return (
                        f"CASE WHEN EXCLUDED.raw_payload ? '{col}' "
                        f"THEN EXCLUDED.{col} ELSE chat_manager_daily_metrics.{col} END"
                    )
                return f"EXCLUDED.{col}"

            set_clauses = [
                f"{col} = {_update_expr(col)}"
                for col in sorted(update_set)
                if col not in _CHAT_COUNT_COMPONENT_FIELDS and not (component_updates and col == 'chats_count')
            ]
            if 'score_sum' in update_set:
                set_clauses.append(
                    "raw_score_sum = CASE WHEN EXCLUDED.raw_payload ? 'score_sum' "
                    "THEN EXCLUDED.raw_score_sum ELSE chat_manager_daily_metrics.raw_score_sum END"
                )
            if 'score_count' in update_set:
                set_clauses.append(
                    "raw_score_count = CASE WHEN EXCLUDED.raw_payload ? 'score_count' "
                    "THEN EXCLUDED.raw_score_count ELSE chat_manager_daily_metrics.raw_score_count END"
                )
            set_clauses.extend([f"{col} = {_update_expr(col)}" for col in sorted(component_updates)])
            if component_updates:
                whatsapp_expr = (
                    _update_expr('whatsapp_chats_count')
                    if 'whatsapp_chats_count' in component_updates
                    else "chat_manager_daily_metrics.whatsapp_chats_count"
                )
                name_requests_expr = (
                    _update_expr('name_requests_chats_count')
                    if 'name_requests_chats_count' in component_updates
                    else "chat_manager_daily_metrics.name_requests_chats_count"
                )
                set_clauses.append(
                    f"chats_count = COALESCE({whatsapp_expr}, 0) + COALESCE({name_requests_expr}, 0)"
                )
        else:
            # Прежнее поведение: обновляем все метрики. preserve_missing защищает оценку/время
            # ответа от затирания NULL-ом; score_sum/score_count сохраняем при частичных
            # импортах, чтобы не терять взвешенную оценку из отчёта Chat2Desk.
            avg_score_expr = (
                "COALESCE(EXCLUDED.avg_score, chat_manager_daily_metrics.avg_score)"
                if preserve_missing else "EXCLUDED.avg_score"
            )
            avg_response_expr = (
                "COALESCE(EXCLUDED.avg_response_time_seconds, chat_manager_daily_metrics.avg_response_time_seconds)"
                if preserve_missing else "EXCLUDED.avg_response_time_seconds"
            )
            set_clauses = [
                "chats_count = EXCLUDED.chats_count",
                f"avg_score = {avg_score_expr}",
                f"avg_response_time_seconds = {avg_response_expr}",
                "transfer_chat_count = EXCLUDED.transfer_chat_count",
                "score_sum = COALESCE(EXCLUDED.score_sum, chat_manager_daily_metrics.score_sum)",
                "score_count = COALESCE(EXCLUDED.score_count, chat_manager_daily_metrics.score_count)",
                "raw_score_sum = COALESCE(EXCLUDED.raw_score_sum, chat_manager_daily_metrics.raw_score_sum)",
                "raw_score_count = COALESCE(EXCLUDED.raw_score_count, chat_manager_daily_metrics.raw_score_count)",
            ]
        set_clauses += [
            "imported_by = EXCLUDED.imported_by",
            "source_batch_id = EXCLUDED.source_batch_id",
            "raw_payload = EXCLUDED.raw_payload",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        do_update_sql = ",\n                    ".join(set_clauses)

        with self._get_cursor() as cursor:
            execute_values(
                cursor,
                f"""
                INSERT INTO chat_manager_daily_metrics (
                    operator_id,
                    day,
                    chats_count,
                    avg_score,
                    avg_response_time_seconds,
                    transfer_chat_count,
                    imported_by,
                    source_batch_id,
                    raw_payload,
                    score_sum,
                    score_count,
                    whatsapp_chats_count,
                    name_requests_chats_count,
                    raw_score_sum,
                    raw_score_count
                )
                VALUES %s
                ON CONFLICT (operator_id, day)
                DO UPDATE SET
                    {do_update_sql}
                """,
                rows,
                page_size=2000
            )

            score_adjustment = self._recalculate_chat_manager_score_adjustments_tx(
                cursor,
                [(row[0], row[1]) for row in rows]
            )
            affected_months = sorted({(int(row[0]), row[1].strftime('%Y-%m')) for row in rows})
            aggregations = {}
            for operator_id, month_key in affected_months:
                aggregations.setdefault(operator_id, {})[month_key] = self._aggregate_month_from_daily_tx(
                    cursor,
                    operator_id,
                    month_key
                )

        return {
            'batch_id': str(batch_id),
            'processed': len(rows),
            'aggregations': aggregations,
            'score_adjustment': score_adjustment,
        }

    # ── Chat2Desk: оценка чатов ЧМ супервайзером (раздел c2d_eval) ────────────

    def save_c2d_requests(self, rows):
        """Идемпотентный upsert заявок Chat2Desk (report=request_stats) в c2d_requests.
        rows — уже нормализованные dict (см. _chat2desk_build_request_rows).
        Возвращает число обработанных строк."""
        if not rows:
            return 0
        # Одно и то же обращение вендор иногда отдаёт дважды в одном отчёте (страницы
        # съезжают, пока идёт листание). INSERT ... ON CONFLICT DO UPDATE, где одна
        # строка задета дважды ОДНОЙ командой, Postgres отклоняет целиком
        # («cannot affect row a second time»), и день не сохранялся вовсе: на 01.09.2026
        # так потерялись все 1994 обращения, а в логе осталась одна строка исключения.
        # Поэтому держим последнюю копию по request_id, как это уже делает
        # save_chat_manager_low_ratings.
        by_request_id = {}
        for row in rows:
            if not isinstance(row, dict) or row.get('request_id') is None:
                continue
            by_request_id[int(row['request_id'])] = (
                int(row['request_id']), row.get('day'),
                row.get('request_start'), row.get('request_end'),
                row.get('request_time'), row.get('reaction_time'),
                row.get('request_type'), row.get('transport'),
                row.get('channel_id'), row.get('channel_name'),
                row.get('client_id'), row.get('client_name'), row.get('client_phone'),
                row.get('c2d_operator_id'), row.get('c2d_operator_name'),
                row.get('operator_id'),
                row.get('incoming_messages'), row.get('outgoing_messages'),
                row.get('rating_score'), row.get('rating_text'),
                row.get('replies'), row.get('average_replies_time'),
                row.get('assigned_phone'),
            )
        values = list(by_request_id.values())
        if not values:
            return 0
        with self._get_cursor() as cursor:
            execute_values(cursor, """
                INSERT INTO c2d_requests (
                    request_id, day, request_start, request_end, request_time,
                    reaction_time, request_type, transport, channel_id, channel_name,
                    client_id, client_name, client_phone, c2d_operator_id,
                    c2d_operator_name, operator_id, incoming_messages,
                    outgoing_messages, rating_score, rating_text,
                    replies, average_replies_time, assigned_phone)
                VALUES %s
                ON CONFLICT (request_id) DO UPDATE SET
                    day = EXCLUDED.day,
                    request_start = EXCLUDED.request_start,
                    request_end = EXCLUDED.request_end,
                    request_time = EXCLUDED.request_time,
                    reaction_time = EXCLUDED.reaction_time,
                    request_type = EXCLUDED.request_type,
                    transport = EXCLUDED.transport,
                    channel_id = EXCLUDED.channel_id,
                    channel_name = EXCLUDED.channel_name,
                    client_id = EXCLUDED.client_id,
                    client_name = EXCLUDED.client_name,
                    client_phone = EXCLUDED.client_phone,
                    c2d_operator_id = EXCLUDED.c2d_operator_id,
                    c2d_operator_name = EXCLUDED.c2d_operator_name,
                    operator_id = EXCLUDED.operator_id,
                    incoming_messages = EXCLUDED.incoming_messages,
                    outgoing_messages = EXCLUDED.outgoing_messages,
                    rating_score = EXCLUDED.rating_score,
                    rating_text = EXCLUDED.rating_text,
                    replies = EXCLUDED.replies,
                    average_replies_time = EXCLUDED.average_replies_time,
                    assigned_phone = EXCLUDED.assigned_phone,
                    synced_at = now()
            """, values)
        return len(values)

    # ── Chat2Desk: приём вебхуков (источник табло вместо опроса) ──────────────

    def save_c2d_webhook_events(self, events):
        """Идемпотентная запись событий вебхука в c2d_webhook_events.

        events — уже нормализованные dict (см. _chat2desk_webhook_events в
        bot_schedule2). Ключ дедупа — `event_key`: вендор повторяет неудачную
        доставку трижды (через 10 секунд, 5 минут и 2 часа), и без него одно
        сообщение посчиталось бы за три. Возвращает число ЗАПИСАННЫХ строк, а не
        принятых: повтор — это ноль, и по этой цифре видно, что поток дублируется.
        """
        if not events:
            return 0
        values = []
        for row in events:
            if not isinstance(row, dict):
                continue
            key = str(row.get('event_key') or '').strip()
            if not key or row.get('event_at') is None or row.get('day') is None:
                continue
            values.append((
                key[:255],
                str(row.get('hook_type') or '')[:64],
                row.get('event_at'), row.get('day'),
                row.get('message_id'), row.get('request_id'), row.get('dialog_id'),
                row.get('client_id'), row.get('c2d_operator_id'), row.get('channel_id'),
                (str(row.get('transport'))[:64] if row.get('transport') else None),
                (str(row.get('message_type'))[:32] if row.get('message_type') else None),
                (str(row.get('request_type'))[:32] if row.get('request_type') else None),
                (str(row.get('client_phone'))[:64] if row.get('client_phone') else None),
                (str(row.get('client_name'))[:255] if row.get('client_name') else None),
                row.get('is_new_request'),
                Json(row.get('payload') if isinstance(row.get('payload'), dict) else {}),
            ))
        if not values:
            return 0
        with self._get_cursor() as cursor:
            execute_values(cursor, """
                INSERT INTO c2d_webhook_events (
                    event_key, hook_type, event_at, day, message_id, request_id,
                    dialog_id, client_id, c2d_operator_id, channel_id, transport,
                    message_type, request_type, client_phone, client_name,
                    is_new_request, payload)
                VALUES %s
                ON CONFLICT (event_key) DO NOTHING
            """, values, page_size=500)
            return int(cursor.rowcount or 0)

    def get_c2d_webhook_events(self, day_from, day_to=None):
        """События вебхука за период, по возрастанию времени — сырьё для метрик табло.

        Порядок задан здесь, а не у вызывающего: и первый ответ, и ответ внутри
        чата считаются проходом по ленте обращения, и на неупорядоченной выборке
        обе величины молча поедут.

        Обращения с номерами «Реестра тестовых номеров» (test_numbers) выпадают целиком,
        со всеми событиями: номер приходит не в каждом событии обращения."""
        from test_numbers import keys as test_keys
        day_to = day_to or day_from
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT hook_type, event_at, day, message_id, request_id, dialog_id,
                       client_id, c2d_operator_id, channel_id, transport,
                       message_type, request_type, is_new_request
                FROM c2d_webhook_events
                WHERE day BETWEEN %s AND %s
                  AND (request_id IS NULL OR request_id NOT IN ("""
                           + test_keys.C2D_WEBHOOK_TEST_REQUESTS_SQL + """))
                ORDER BY event_at, id
            """, (day_from, day_to))
            columns = [description[0] for description in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_c2d_open_chats_by_operator(self):
        """{id оператора Chat2Desk: сколько чатов у него сейчас открыто}.

        Открытый чат — обращение, по которому были сообщения и не приходило
        `close_request`. Считаем в SQL, а не строками: открытым может быть и
        позавчерашний чат, а тянуть ради счётчика всю неделю событий в память
        значит платить памятью за одну цифру на плитке.

        Тестовые обращения (номер из «Реестра тестовых номеров») открытыми не считаются."""
        from test_numbers import keys as test_keys
        with self._get_cursor() as cursor:
            cursor.execute("""
                WITH messages AS (
                    SELECT request_id,
                           MAX(event_at) FILTER (WHERE c2d_operator_id IS NOT NULL) AS last_at,
                           (ARRAY_AGG(c2d_operator_id ORDER BY event_at DESC)
                                FILTER (WHERE c2d_operator_id IS NOT NULL))[1] AS operator_id
                    FROM c2d_webhook_events
                    WHERE request_id IS NOT NULL
                      AND hook_type IN ('inbox', 'outbox', 'imported_message', 'comment',
                                        'dialog_transferred', 'new_request')
                      AND request_id NOT IN (""" + test_keys.C2D_WEBHOOK_TEST_REQUESTS_SQL + """)
                    GROUP BY request_id
                )
                SELECT m.operator_id, COUNT(*)::int
                FROM messages m
                WHERE m.operator_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM c2d_webhook_events c
                      WHERE c.request_id = m.request_id
                        AND c.hook_type IN ('close_request', 'close_dialog'))
                GROUP BY 1
            """)
            return {int(row[0]): int(row[1]) for row in cursor.fetchall()}

    def get_c2d_operator_names(self, days=30):
        """{id оператора Chat2Desk: имя} из уже синхронизированных обращений.

        Вебхук приносит только `operator_id`, а разрез табло по людям и почасовой
        отчёт работают по именам учёток. Справочник берём из своей базы, а не из
        `/v1/operators`: там он стоит запроса, а здесь уже лежит с ночного синка."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT ON (c2d_operator_id) c2d_operator_id, c2d_operator_name
                FROM c2d_requests
                WHERE c2d_operator_id IS NOT NULL
                  AND NULLIF(c2d_operator_name, '') IS NOT NULL
                  AND day >= CURRENT_DATE - %s
                ORDER BY c2d_operator_id, day DESC
            """, (int(days),))
            return {int(row[0]): str(row[1]) for row in cursor.fetchall()}

    def get_c2d_webhook_health(self):
        """{first_event_at, last_event_at, events_today} — жив ли поток и с какого момента.

        Табло решает по этим двум цифрам, можно ли верить базе:
          * последнее событие — вендор отключает вебхук после пяти часов неуспешных
            доставок, а молчание потока внешне неотличимо от «сегодня не было чатов»;
          * первое событие — в сутки, начавшиеся ДО включения вебхука, поток по
            определению неполон, и показывать по ним цифры нельзя: на стене это
            выглядело бы не как «данных нет», а как упавший вдвое объём."""
        # Три подзапроса, а не один проход: MIN/MAX берутся из краёв индекса по event_at,
        # счёт суток — из индекса (day, request_id). Одним SELECT с COUNT FILTER Postgres
        # проходил всю неделю событий на каждую пересборку табло — ~1 с базы на вызов
        # (замер 15–19.10.2026). Ответ тот же.
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT (SELECT MIN(event_at) FROM c2d_webhook_events),
                       (SELECT MAX(event_at) FROM c2d_webhook_events),
                       (SELECT COUNT(*) FROM c2d_webhook_events WHERE day = CURRENT_DATE)::int
            """)
            row = cursor.fetchone() or (None, None, 0)
            return {'first_event_at': row[0], 'last_event_at': row[1],
                    'events_today': int(row[2] or 0)}

    def cleanup_c2d_eval_data(self, requests_days=45, snapshots_days=180,
                              webhook_days=7):
        """Ретеншн раздела оценки чатов: заявки 45 дней, снапшоты переписки ~полгода.
        Оценки не удаляются (при удалении снапшота snapshot_id -> NULL по FK).
        Сырые вебхуки Chat2Desk живут неделю — столько же, сколько их читает табло."""
        with self._get_cursor() as cursor:
            marked_journal_episodes = self._mark_journal_evaluated_wazzup_episodes_tx(
                cursor)
            cursor.execute(
                "DELETE FROM c2d_requests WHERE day < CURRENT_DATE - %s",
                (int(requests_days),))
            deleted_requests = cursor.rowcount
            # Снапшот, который ИИ уже оценил, НЕ удаляем: для переписки СЗоВ он
            # сам является субъектом оценки (subject_kind='c2d_snapshot'), и его
            # удаление не обнулило бы ссылку, как у человеческой оценки, а увело
            # бы оценку из очереди ревью и списка оценок целиком — их выборки
            # соединяются с таблицей субъекта (см. _SUBJECT_EXISTS в call_qa/api.py).
            # У человеческих оценок поведение прежнее: snapshot_id -> NULL по FK.
            cursor.execute(
                """DELETE FROM c2d_chat_snapshots s
                    WHERE s.created_at < now() - make_interval(days => %s)
                      AND NOT EXISTS (SELECT 1 FROM ai_evaluation_runs r
                                       WHERE r.subject_kind = 'c2d_snapshot'
                                         AND r.call_id = s.id
                                         AND r.status = 'succeeded')
                      AND NOT EXISTS (SELECT 1 FROM ai_evaluation_meta m
                                       WHERE m.subject_kind = 'c2d_snapshot'
                                         AND m.call_id = s.id)
                      AND NOT EXISTS (SELECT 1 FROM ai_review_cache rc
                                       WHERE rc.subject_kind = 'c2d_snapshot'
                                         AND rc.call_id = s.id)""",
                (int(snapshots_days),))
            deleted_snapshots = cursor.rowcount
            # Сырьё вебхуков: неделя. Считаем по времени события, а не приёма —
            # запоздавший повтор старых суток не должен продлевать им жизнь.
            cursor.execute(
                "DELETE FROM c2d_webhook_events WHERE day < CURRENT_DATE - %s",
                (int(webhook_days),))
            deleted_webhook_events = cursor.rowcount
        if (deleted_requests or deleted_snapshots or marked_journal_episodes
                or deleted_webhook_events):
            logging.info(
                "c2d eval retention: удалено %s заявок, %s снапшотов, %s событий вебхука; "
                "отмечено %s оценённых эпизодов Wazzup",
                deleted_requests, deleted_snapshots, deleted_webhook_events,
                marked_journal_episodes)
        return {
            'requests': deleted_requests,
            'snapshots': deleted_snapshots,
            'webhook_events': deleted_webhook_events,
            'wazzup_journal_marked': marked_journal_episodes,
        }

    @staticmethod
    def _c2d_requests_where(date_from=None, date_to=None, operator_id=None,
                            channel_id=None, transport=None, min_messages=None,
                            max_messages=None, rating_filter=None, department_id=None):
        """Общий WHERE по c2d_requests (алиас r, users — алиас u).

        Обращения с номерами «Реестра тестовых номеров» (test_numbers) не предлагаются
        и не считаются: номер клиента — в client_phone или, у клиента WhatsApp с
        идентификатором, в assigned_phone; оба поля — цифры."""
        from test_numbers import keys as test_keys
        where = ["r.operator_id IS NOT NULL",
                 test_keys.sql_not_test_any('r.client_phone', 'r.assigned_phone', digits=True)]
        params = []
        if date_from:
            where.append("r.day >= %s")
            params.append(date_from)
        if date_to:
            where.append("r.day <= %s")
            params.append(date_to)
        if operator_id is not None:
            where.append("r.operator_id = %s")
            params.append(int(operator_id))
        if channel_id is not None:
            where.append("r.channel_id = %s")
            params.append(int(channel_id))
        if transport:
            where.append("r.transport = %s")
            params.append(str(transport))
        if min_messages is not None:
            where.append("(COALESCE(r.incoming_messages,0) + COALESCE(r.outgoing_messages,0)) >= %s")
            params.append(int(min_messages))
        if max_messages is not None:
            where.append("(COALESCE(r.incoming_messages,0) + COALESCE(r.outgoing_messages,0)) <= %s")
            params.append(int(max_messages))
        rating_filter = str(rating_filter or '').strip().lower()
        if rating_filter == 'rated':
            where.append("r.rating_score IS NOT NULL")
        elif rating_filter == 'unrated':
            where.append("r.rating_score IS NULL")
        elif rating_filter == 'low':
            where.append("r.rating_score IS NOT NULL AND r.rating_score < 4")
        if department_id is not None:
            where.append("u.department_id = %s")
            params.append(int(department_id))
        return where, params

    @staticmethod
    def _c2d_request_row_dict(r):
        return {
            'request_id': r[0], 'day': r[1].isoformat() if r[1] else None,
            'request_start': r[2].isoformat() if r[2] else None,
            'request_end': r[3].isoformat() if r[3] else None,
            'request_time': r[4], 'reaction_time': r[5], 'request_type': r[6],
            'transport': r[7], 'channel_id': r[8], 'channel_name': r[9],
            'client_id': r[10], 'client_name': r[11], 'client_phone': r[12],
            'c2d_operator_id': r[13], 'c2d_operator_name': r[14],
            'operator_id': r[15], 'operator_name': r[16],
            'incoming_messages': r[17], 'outgoing_messages': r[18],
            'rating_score': r[19], 'rating_text': r[20],
        }

    def pick_c2d_request(self, date_from=None, date_to=None, operator_id=None,
                         channel_id=None, transport=None, min_messages=None,
                         max_messages=None, rating_filter=None, department_id=None,
                         exclude='any', evaluator_id=None, exclude_request_ids=None):
        """Случайная заявка по фильтрам. Оценки чатов живут в журнале (calls с
        c2d_snapshot_id), поэтому исключения смотрят туда: 'any' — без чатов,
        уже оценённых кем-либо, 'mine' — оценённых этим СВ, 'none' — без
        исключений. exclude_request_ids — заявки, отсеянные в этом же запросе
        (например, без переписки). Возвращает (row|None, candidates_count)."""
        where, params = self._c2d_requests_where(
            date_from=date_from, date_to=date_to, operator_id=operator_id,
            channel_id=channel_id, transport=transport, min_messages=min_messages,
            max_messages=max_messages, rating_filter=rating_filter,
            department_id=department_id)
        if exclude_request_ids:
            where.append("r.request_id != ALL(%s)")
            params.append([int(v) for v in exclude_request_ids])
        exclude = str(exclude or 'none').strip().lower()
        evaluated_exists = """NOT EXISTS (
            SELECT 1 FROM calls c
            JOIN c2d_chat_snapshots s ON s.id = c.c2d_snapshot_id
            WHERE s.request_id = r.request_id{extra})"""
        if exclude == 'mine' and evaluator_id is not None:
            where.append(evaluated_exists.format(extra=" AND c.evaluator_id = %s"))
            params.append(int(evaluator_id))
        elif exclude in ('any', 'mine'):
            where.append(evaluated_exists.format(extra=""))
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {self._C2D_REQUEST_COLUMNS}, COUNT(*) OVER ()
                  FROM c2d_requests r
                  LEFT JOIN users u ON u.id = r.operator_id
                 WHERE {' AND '.join(where)}
                 ORDER BY random()
                 LIMIT 1
            """, params)
            row = cursor.fetchone()
        if not row:
            return None, 0
        return self._c2d_request_row_dict(row), int(row[21] or 0)

    def get_c2d_request(self, request_id):
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT {self._C2D_REQUEST_COLUMNS}
                  FROM c2d_requests r
                  LEFT JOIN users u ON u.id = r.operator_id
                 WHERE r.request_id = %s
            """, (int(request_id),))
            row = cursor.fetchone()
        return self._c2d_request_row_dict(row) if row else None

    def c2d_filter_options(self, date_from=None, date_to=None, department_id=None):
        """Каналы/транспорты/операторы с числом атрибутированных заявок за период —
        данные для панели фильтров случайного выбора."""
        where, params = self._c2d_requests_where(
            date_from=date_from, date_to=date_to, department_id=department_id)
        base = f"""FROM c2d_requests r
                   LEFT JOIN users u ON u.id = r.operator_id
                  WHERE {' AND '.join(where)}"""
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT r.channel_id, (array_agg(r.channel_name))[1], COUNT(*) {base}
                 GROUP BY r.channel_id ORDER BY 3 DESC
            """, params)
            channels = [{'id': r[0], 'name': r[1], 'count': r[2]} for r in cursor.fetchall()]
            cursor.execute(f"SELECT r.transport, COUNT(*) {base} GROUP BY r.transport ORDER BY 2 DESC", params)
            transports = [{'id': r[0], 'count': r[1]} for r in cursor.fetchall() if r[0]]
            cursor.execute(f"""
                SELECT r.operator_id, COALESCE(u.name, (array_agg(r.c2d_operator_name))[1]), COUNT(*) {base}
                 GROUP BY r.operator_id, u.name ORDER BY 2
            """, params)
            operators = [{'id': r[0], 'name': r[1], 'count': r[2]} for r in cursor.fetchall()]
            cursor.execute(f"SELECT MIN(r.day), MAX(r.day), COUNT(*) {base}", params)
            span = cursor.fetchone()
        return {
            'channels': channels, 'transports': transports, 'operators': operators,
            'minDay': span[0].isoformat() if span and span[0] else None,
            'maxDay': span[1].isoformat() if span and span[1] else None,
            'totalRequests': int(span[2] or 0) if span else 0,
        }

    def save_c2d_snapshot(self, request_id, dialog_id, messages, request_row=None,
                          created_by=None):
        """Upsert снапшота переписки заявки (один на request_id). Возвращает id."""
        request_row = request_row or {}
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO c2d_chat_snapshots (
                    request_id, dialog_id, day, operator_id, c2d_operator_name,
                    channel_name, transport, client_name, client_phone,
                    messages, messages_count, request_payload, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (request_id) DO UPDATE SET
                    dialog_id = EXCLUDED.dialog_id,
                    messages = EXCLUDED.messages,
                    messages_count = EXCLUDED.messages_count,
                    request_payload = EXCLUDED.request_payload
                RETURNING id
            """, (
                int(request_id),
                int(dialog_id) if dialog_id is not None else None,
                request_row.get('day'),
                request_row.get('operator_id'),
                request_row.get('c2d_operator_name'),
                request_row.get('channel_name'), request_row.get('transport'),
                request_row.get('client_name'), request_row.get('client_phone'),
                Json(messages or []), len(messages or []),
                Json(request_row),
                int(created_by) if created_by is not None else None,
            ))
            return cursor.fetchone()[0]

    def update_c2d_snapshot_messages(self, snapshot_id, messages, request_patch=None):
        """Перезаписывает ленту снапшота тем же набором сообщений, но дополненным
        полями (сейчас — operatorId, задача #286). Состав сообщений не меняется:
        на их id ссылаются цитаты оценок в журнале."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                UPDATE c2d_chat_snapshots
                SET messages = %s,
                    request_payload = COALESCE(request_payload, '{}'::jsonb) || %s
                WHERE id = %s
                """,
                (Json(messages or []), Json(request_patch or {}), int(snapshot_id))
            )
            return int(cursor.rowcount or 0)

    def get_c2d_webhook_message_operators(self, request_id, day_from, day_to):
        """{id сообщения: id учётки Chat2Desk или None} из сырья вебхуков — бесплатно,
        без квоты API. Сырьё живёт неделю, поэтому годится только для свежих чатов.
        None — вендор сам прислал сообщение без учётки (чат ещё в очереди): это
        ответ, а не пробел, и спрашивать о нём платный API незачем.
        Выборка по (day, request_id) — ровно по индексу idx_c2d_webhook_events_day."""
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT message_id, MAX(c2d_operator_id)
                FROM c2d_webhook_events
                WHERE day BETWEEN %s AND %s
                  AND request_id = %s
                  AND message_id IS NOT NULL
                GROUP BY message_id
                """,
                (day_from, day_to, int(request_id))
            )
            return {
                int(row[0]): (int(row[1]) if row[1] is not None else None)
                for row in cursor.fetchall() or []
            }

    def get_c2d_operator_directory(self, c2d_operator_ids):
        """{id учётки Chat2Desk: {'user_id', 'name'}} — кто есть кто в переписке.

        Сообщения вендора знают только id учётки. Связку «учётка → сотрудник» уже
        строит ночной синк (c2d_requests сопоставляет имя учётки с сотрудником), а
        у заявок старше 45 дней она сохраняется в самих низких оценках."""
        ids = sorted({int(v) for v in (c2d_operator_ids or []) if v not in (None, '')})
        if not ids:
            return {}
        result = {}
        with self._get_cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (r.c2d_operator_id)
                       r.c2d_operator_id, r.operator_id, NULLIF(r.c2d_operator_name, '')
                FROM c2d_requests r
                WHERE r.c2d_operator_id = ANY(%s)
                ORDER BY r.c2d_operator_id, (r.operator_id IS NULL), r.day DESC
                """,
                (ids,)
            )
            for c2d_id, user_id, name in cursor.fetchall() or []:
                result[int(c2d_id)] = {
                    'user_id': int(user_id) if user_id is not None else None,
                    'name': name or '',
                }
            missing = [value for value in ids if not (result.get(value) or {}).get('user_id')]
            if missing:
                cursor.execute(
                    """
                    SELECT DISTINCT ON (lr.raw_payload->>'operator_id')
                           lr.raw_payload->>'operator_id', lr.operator_id, NULLIF(lr.operator_name, '')
                    FROM chat_manager_low_rating_reviews lr
                    WHERE lr.raw_payload->>'operator_id' = ANY(%s)
                    ORDER BY lr.raw_payload->>'operator_id', lr.day DESC
                    """,
                    ([str(value) for value in missing],)
                )
                for c2d_raw, user_id, name in cursor.fetchall() or []:
                    try:
                        c2d_id = int(c2d_raw)
                    except (TypeError, ValueError):
                        continue
                    known = result.get(c2d_id) or {}
                    result[c2d_id] = {
                        'user_id': int(user_id) if user_id is not None else None,
                        'name': known.get('name') or name or '',
                    }
            user_ids = [entry['user_id'] for entry in result.values() if entry.get('user_id')]
            if user_ids:
                cursor.execute("SELECT id, name, department_id FROM users WHERE id = ANY(%s)", (user_ids,))
                users_info = {int(row[0]): (str(row[1] or ''), row[2]) for row in cursor.fetchall() or []}
                for entry in result.values():
                    if not entry.get('user_id'):
                        continue
                    name, department_id = users_info.get(entry['user_id'], ('', None))
                    if not entry.get('name'):
                        entry['name'] = name
                    # Отдел — чтобы не предлагать проверяющему засчитать оценку
                    # тому, кому он её засчитать не вправе.
                    entry['department_id'] = int(department_id) if department_id is not None else None
        return result

    def get_c2d_snapshot(self, snapshot_id=None, request_id=None):
        """Снапшот по id или request_id (Chat2Desk и Wazzup — общая таблица)."""
        cond, param = ("s.id = %s", snapshot_id) if snapshot_id is not None \
            else ("s.request_id = %s", request_id)
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT s.id, s.request_id, s.dialog_id, s.day, s.operator_id,
                       COALESCE(u.name, s.c2d_operator_name), s.channel_name,
                       s.transport, s.client_name, s.client_phone, s.messages,
                       s.messages_count, s.request_payload, s.created_by, s.created_at,
                       u.department_id, s.source, s.wz_channel_id, s.wz_chat_id,
                       s.episode_start, s.episode_end
                  FROM c2d_chat_snapshots s
                  LEFT JOIN users u ON u.id = s.operator_id
                 WHERE {cond}
            """, (int(param),))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            'id': row[0], 'request_id': row[1], 'dialog_id': row[2],
            'day': row[3].isoformat() if row[3] else None,
            'operator_id': row[4], 'operator_name': row[5],
            'channel_name': row[6], 'transport': row[7],
            'client_name': row[8], 'client_phone': row[9],
            'messages': row[10] or [], 'messages_count': row[11],
            'request': row[12] or {}, 'created_by': row[13],
            'created_at': row[14].isoformat() if row[14] else None,
            'operator_department_id': row[15],
            'source': row[16] or 'chat2desk',
            'wz_channel_id': row[17], 'wz_chat_id': row[18],
            'episode_start': row[19].isoformat() if row[19] else None,
            'episode_end': row[20].isoformat() if row[20] else None,
        }

    def get_wz_snapshot_id(self, channel_id, chat_id, episode_start):
        """id уже сохранённого снапшота эпизода Wazzup (или None). Нужен, чтобы
        переиспользовать снапшот при повторном пике, а не перезатирать его
        сообщения (у оценённого эпизода на снапшот уже ссылается calls-ряд)."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id FROM c2d_chat_snapshots
                 WHERE source = 'wazzup' AND wz_channel_id = %s
                   AND wz_chat_id = %s AND episode_start = %s
            """, (str(channel_id), str(chat_id), episode_start))
            row = cursor.fetchone()
        return row[0] if row else None

    def save_wz_snapshot(self, episode, messages, created_by=None, channel_name=None):
        """Upsert снапшота эпизода Wazzup (ключ: чат + начало эпизода)."""
        day = None
        ep_start = episode.get('episode_start')
        if ep_start is not None:
            try:
                day = ep_start.astimezone(ZoneInfo('Asia/Almaty')).date()
            except Exception:
                day = None
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO c2d_chat_snapshots (
                    source, wz_channel_id, wz_chat_id, episode_start, episode_end,
                    day, operator_id, c2d_operator_name, channel_name, transport,
                    client_name, client_phone, messages, messages_count,
                    request_payload, created_by)
                VALUES ('wazzup', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (wz_channel_id, wz_chat_id, episode_start) WHERE source = 'wazzup'
                DO UPDATE SET
                    episode_end = EXCLUDED.episode_end,
                    messages = EXCLUDED.messages,
                    messages_count = EXCLUDED.messages_count,
                    request_payload = EXCLUDED.request_payload
                RETURNING id
            """, (
                str(episode.get('channel_id')), str(episode.get('chat_id')),
                episode.get('episode_start'), episode.get('episode_end'),
                day,
                int(episode['operator_id']) if episode.get('operator_id') is not None else None,
                episode.get('operator_name'),
                channel_name or str(episode.get('channel_id') or ''),
                episode.get('chat_type') or 'whatsapp',
                episode.get('client_name'), episode.get('client_phone'),
                Json(messages or []), len(messages or []),
                Json({k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                      for k, v in episode.items()}),
                int(created_by) if created_by is not None else None,
            ))
            return cursor.fetchone()[0]

    def get_ca_snapshot_id(self, license_id, messenger_type, chat_id, episode_start):
        """id уже сохранённого снапшота эпизода ChatApp (или None)."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id FROM c2d_chat_snapshots
                 WHERE source = 'chatapp' AND wz_channel_id = %s
                   AND wz_chat_id = %s AND episode_start = %s
            """, (self.chatapp_channel_key(license_id, messenger_type),
                  str(chat_id), episode_start))
            row = cursor.fetchone()
        return row[0] if row else None

    def save_ca_snapshot(self, episode, messages, created_by=None, channel_name=None):
        """Upsert снапшота эпизода ChatApp (ключ: канал + чат + начало эпизода)."""
        day = None
        ep_start = episode.get('episode_start')
        if ep_start is not None:
            try:
                day = ep_start.astimezone(ZoneInfo('Asia/Almaty')).date()
            except Exception:
                day = None
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO c2d_chat_snapshots (
                    source, wz_channel_id, wz_chat_id, episode_start, episode_end,
                    day, operator_id, c2d_operator_name, channel_name, transport,
                    client_name, client_phone, messages, messages_count,
                    request_payload, created_by)
                VALUES ('chatapp', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (wz_channel_id, wz_chat_id, episode_start) WHERE source = 'chatapp'
                DO UPDATE SET
                    episode_end = EXCLUDED.episode_end,
                    messages = EXCLUDED.messages,
                    messages_count = EXCLUDED.messages_count,
                    request_payload = EXCLUDED.request_payload
                RETURNING id
            """, (
                self.chatapp_channel_key(episode['license_id'], episode['messenger_type']),
                str(episode.get('chat_id')),
                episode.get('episode_start'), episode.get('episode_end'),
                day,
                int(episode['operator_id']) if episode.get('operator_id') is not None else None,
                episode.get('operator_name'),
                channel_name or self.chatapp_channel_key(episode['license_id'],
                                                         episode['messenger_type']),
                'whatsapp',
                episode.get('client_name'), episode.get('client_phone'),
                Json(messages or []), len(messages or []),
                Json({k: (v.isoformat() if hasattr(v, 'isoformat') else v)
                      for k, v in episode.items()}),
                int(created_by) if created_by is not None else None,
            ))
            return cursor.fetchone()[0]

    def _is_chat_manager_direction(self, direction_name):
        key = self._normalize_direction_key(direction_name)
        return key in ('чат менеджер', 'chat manager')
