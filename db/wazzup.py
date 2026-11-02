"""База данных: чаты Wazzup.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    datetime,
    dt_timezone,
    Json,
    logging,
    timedelta,
)


class WazzupMixin:
    # ── Wazzup (Верификаторы): приём вебхуков ────────────────────────────────

    def store_wazzup_messages(self, messages, account='op', received_at=None):
        """Идемпотентный upsert сообщений из вебхука Wazzup (messagesAndStatuses).

        Повторная доставка безопасна (PK message_id); правки/удаления
        (isEdited/isDeleted) обновляют существующую строку. После вставки
        обновляет сводки wazzup_chats затронутых чатов. Возвращает число
        обработанных сообщений.

        account — аккаунт Wazzup (wazzup/accounts.py). Сюда же, в форме
        вебхука, пишет и забор истории «Потока» (wazzup/potok_sync.py).
        Ключ автора у исходящих строится в wazzup.names.author_key: у «op»
        это authorId вебхука, у «potok» — нормализованное имя, потому что
        у истории из окна чатов id автора нет.

        received_at — момент прихода вебхука; передаёт только приёмник.
        Входящее канала на обычном WhatsApp, пришедшее с опозданием, получает
        время доставки (wazzup/delivery.py), исходное время уходит в
        wazzup_dt. Сообщение с минутным временем (входящее или ответ с
        телефона), пришедшее вовремя, встаёт не раньше сообщения с точным
        временем (ответа из окна iCORE), которое к его приходу уже было в чате.
        Время задаётся только при первой вставке: повторная доставка того же
        сообщения его не двигает. Забор «Потока» время прихода не передаёт — у
        окна чатов время своё и точное."""
        from wazzup.delivery import (after_known_replies, delivery_backlog, delivery_time,
                                     is_in_time, is_late_inbound, parse_wazzup_dt,
                                     reply_pending)
        from wazzup.names import author_key
        from wazzup.quotes import quote_values
        if not isinstance(messages, list) or not messages:
            return 0
        processed = 0
        affected = {}
        backlog = None   # задержка доставки вебхуков — одна на весь вызов
        latest = {}      # (канал, чат) → самое позднее время, записанное в этом вызове
        with self._get_cursor() as cursor:
            # Acquire the receipt locks in a common order without reordering
            # message processing (its order matters for late-delivery times).
            message_ids = sorted({str(m['messageId']) for m in messages
                                  if isinstance(m, dict) and m.get('messageId')})
            if message_ids:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext('wazzup-status'), hashtext(message_id)) "
                               "FROM unnest(%s::text[]) AS ids(message_id)", (message_ids,))
            for m in messages:
                if not isinstance(m, dict):
                    continue
                message_id = m.get('messageId')
                channel_id = m.get('channelId')
                chat_id = m.get('chatId')
                dt = m.get('dateTime')
                if not message_id or not channel_id or chat_id is None or not dt:
                    continue
                contact = m.get('contact') if isinstance(m.get('contact'), dict) else {}
                is_echo = bool(m.get('isEcho'))
                author_id = author_key(account, m.get('authorId'), m.get('authorName')) \
                    if is_echo else None
                chat_key = (str(channel_id), str(chat_id))
                parsed_dt = parse_wazzup_dt(dt)
                stored_dt, wazzup_dt, moment = dt, None, parsed_dt
                if is_late_inbound(parsed_dt, received_at, is_echo):
                    if backlog is None:
                        backlog = delivery_backlog(
                            self._wazzup_recent_arrivals_tx(cursor, account, received_at))
                    if not backlog:
                        history = self._wazzup_chat_history_tx(
                            cursor, chat_key[0], chat_key[1], received_at)
                        known = [h['dt'] for h in history]
                        if chat_key in latest:
                            known.append(latest[chat_key])
                        stored_dt = moment = delivery_time(
                            received_at, reply_pending(history, received_at),
                            after=max(known, default=None))
                        wazzup_dt = dt
                elif is_in_time(parsed_dt, received_at):
                    placed = after_known_replies(parsed_dt, self._wazzup_chat_later_tx(
                        cursor, chat_key[0], chat_key[1], parsed_dt, skip_late=is_echo))
                    if placed != parsed_dt:
                        stored_dt = moment = placed
                if moment is not None:
                    latest[chat_key] = max(latest.get(chat_key, moment), moment)
                quote = quote_values(m)
                if quote[0]:
                    # Снимок цитаты — с оригинала, если он уже в архиве этого чата: у ответа
                    # из iCORE вебхук прислал цитату входящего без текста и с пометкой
                    # исходящего (28.10.2026). Снимок Wazzup — только когда оригинала нет.
                    cursor.execute("""
                        SELECT is_echo, type, CASE WHEN is_deleted THEN NULL ELSE text END, author_name
                          FROM wazzup_messages
                         WHERE message_id = %s AND account = %s AND channel_id = %s AND chat_id = %s
                    """, (quote[0], account, chat_key[0], chat_key[1]))
                    original = cursor.fetchone()
                    if original:
                        quote = (quote[0], *original)
                cursor.execute("""
                    INSERT INTO wazzup_messages (
                        message_id, channel_id, chat_type, chat_id, dt, is_echo,
                        type, text, content_uri, author_name, author_id,
                        contact_name, contact_phone, status, sent_from_app,
                        quoted_message_id, quoted_is_echo, quoted_type, quoted_text,
                        quoted_author_name,
                        is_edited, is_deleted, account, wazzup_dt)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (message_id) DO UPDATE SET
                        -- событие удаления приходит без текста: сохраняем последний
                        -- известный текст, факт удаления фиксирует is_deleted
                        text = COALESCE(EXCLUDED.text, wazzup_messages.text),
                        content_uri = COALESCE(EXCLUDED.content_uri, wazzup_messages.content_uri),
                        status = wazzup_merge_delivery(wazzup_messages.status, EXCLUDED.status),
                        author_name = COALESCE(EXCLUDED.author_name, wazzup_messages.author_name),
                        author_id = COALESCE(EXCLUDED.author_id, wazzup_messages.author_id),
                        -- правка и удаление могут прийти без цитаты, а наша копия
                        -- отправки из iCORE пишется без неё до эха Wazzup
                        quoted_message_id = COALESCE(EXCLUDED.quoted_message_id,
                                                     wazzup_messages.quoted_message_id),
                        quoted_is_echo = COALESCE(EXCLUDED.quoted_is_echo, wazzup_messages.quoted_is_echo),
                        quoted_type = COALESCE(EXCLUDED.quoted_type, wazzup_messages.quoted_type),
                        quoted_text = COALESCE(EXCLUDED.quoted_text, wazzup_messages.quoted_text),
                        quoted_author_name = COALESCE(EXCLUDED.quoted_author_name,
                                                      wazzup_messages.quoted_author_name),
                        is_edited = EXCLUDED.is_edited,
                        is_deleted = EXCLUDED.is_deleted
                    WHERE wazzup_messages.account = EXCLUDED.account
                """, (
                    str(message_id), str(channel_id), m.get('chatType'), str(chat_id),
                    stored_dt, is_echo, m.get('type'), m.get('text'),
                    m.get('contentUri'), m.get('authorName'), author_id,
                    contact.get('name'), contact.get('phone'), m.get('status'),
                    m.get('sentFromApp'), *quote,
                    bool(m.get('isEdited')), bool(m.get('isDeleted')),
                    account, wazzup_dt,
                ))
                if cursor.rowcount == 0:
                    # Message IDs are globally unique, but never allow a wrong
                    # account to overwrite an existing message on collision.
                    continue
                processed += 1
                affected[(str(channel_id), str(chat_id))] = (m.get('chatType'), contact)
            for (channel_id, chat_id), (chat_type, contact) in affected.items():
                self._refresh_wazzup_chat_tx(cursor, channel_id, chat_id, chat_type, contact,
                                             account=account)
        return processed

    @staticmethod
    def _wazzup_chat_history_tx(cursor, channel_id, chat_id, received_at):
        """Сообщения чата, пришедшие за окно перед received_at, — в форме,
        которую ждёт wazzup.delivery.reply_pending."""
        from wazzup.delivery import history_since
        cursor.execute("""
            SELECT is_echo, dt, created_at, wazzup_dt IS NOT NULL
              FROM wazzup_messages
             WHERE channel_id = %s AND chat_id = %s
               AND created_at >= %s AND created_at < %s""",
            (channel_id, chat_id, history_since(received_at), received_at))
        return [{'is_echo': r[0], 'dt': r[1], 'created_at': r[2], 'late': r[3]}
                for r in cursor.fetchall()]

    @staticmethod
    def _wazzup_recent_arrivals_tx(cursor, account, received_at):
        """(dt, created_at) сообщений аккаунта, пришедших перед received_at, —
        для wazzup.delivery.delivery_backlog. Отбор по dt — ради индекса
        (account, dt)."""
        from wazzup.delivery import recent_window
        dt_from, arrived_from = recent_window(received_at)
        cursor.execute("""
            SELECT dt, created_at FROM wazzup_messages
             WHERE account = %s AND dt >= %s AND dt < %s
               AND created_at >= %s AND created_at < %s""",
            (account, dt_from, received_at, arrived_from, received_at))
        return cursor.fetchall()

    @staticmethod
    def _refresh_wazzup_chat_tx(cursor, channel_id, chat_id, chat_type=None, contact=None,
                                account='op'):
        """Пересчитывает сводку чата из wazzup_messages (в текущей транзакции).

        Счётчики отражают окно ретеншна (старые сообщения удаляются), это
        осознанно: сводка нужна списку чатов, не вечной статистике."""
        contact = contact or {}
        cursor.execute("""
            INSERT INTO wazzup_chats (
                channel_id, chat_id, chat_type, contact_name, contact_phone,
                last_message_at, last_message_text, last_message_is_echo,
                messages_count, inbound_count, outbound_count, updated_at, account)
            SELECT %(channel_id)s, %(chat_id)s, %(chat_type)s,
                   %(contact_name)s, %(contact_phone)s,
                   a.last_at, lm.preview, lm.is_echo,
                   a.total, a.inbound, a.outbound, now(), %(account)s
            FROM (SELECT COUNT(*) AS total,
                         COUNT(*) FILTER (WHERE NOT is_echo) AS inbound,
                         COUNT(*) FILTER (WHERE is_echo) AS outbound,
                         MAX(dt) AS last_at
                    FROM wazzup_messages
                   WHERE channel_id = %(channel_id)s AND chat_id = %(chat_id)s
                     AND NOT is_deleted) a
            LEFT JOIN LATERAL (
                SELECT COALESCE(NULLIF(LEFT(text, 300), ''),
                                '[' || COALESCE(type, 'message') || ']') AS preview,
                       is_echo
                  FROM wazzup_messages
                 WHERE channel_id = %(channel_id)s AND chat_id = %(chat_id)s
                   AND NOT is_deleted
                 ORDER BY dt DESC LIMIT 1) lm ON TRUE
            WHERE a.total > 0
            ON CONFLICT (channel_id, chat_id) DO UPDATE SET
                chat_type = COALESCE(EXCLUDED.chat_type, wazzup_chats.chat_type),
                contact_name = COALESCE(EXCLUDED.contact_name, wazzup_chats.contact_name),
                contact_phone = COALESCE(EXCLUDED.contact_phone, wazzup_chats.contact_phone),
                last_message_at = EXCLUDED.last_message_at,
                last_message_text = EXCLUDED.last_message_text,
                last_message_is_echo = EXCLUDED.last_message_is_echo,
                messages_count = EXCLUDED.messages_count,
                inbound_count = EXCLUDED.inbound_count,
                outbound_count = EXCLUDED.outbound_count,
                updated_at = now()
        """, {
            'channel_id': channel_id, 'chat_id': chat_id, 'chat_type': chat_type,
            'contact_name': contact.get('name'), 'contact_phone': contact.get('phone'),
            'account': account,
        })

    def wazzup_last_message_at(self, account='op'):
        """Время последнего сохранённого сообщения аккаунта (None — пусто).
        Отсюда регулярный забор «Потока» отсчитывает своё окно."""
        with self._get_cursor() as cursor:
            cursor.execute("SELECT MAX(dt) FROM wazzup_messages WHERE account = %s",
                           (account,))
            row = cursor.fetchone()
        return row[0] if row else None

    def wazzup_chat_last_messages(self, account='op'):
        """chat_id → время последнего сохранённого сообщения по аккаунту.

        Забор «Потока» сверяет с этим списком строку чата из Wazzup: если её
        lastMessage уже у нас, сообщения чата не запрашиваются. Так загрузка
        истории возобновляется после рестарта процесса (деплой убивает фоновый
        поток), а регулярное дотягивание не перечитывает молчащие чаты."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT chat_id, last_message_at FROM wazzup_chats
                 WHERE account = %s AND last_message_at IS NOT NULL""", (account,))
            return {r[0]: r[1] for r in cursor.fetchall()}

    def wazzup_accounts_summary(self):
        """Сколько чатов и когда последнее сообщение — по аккаунтам, для
        переключателя раздела."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT account, COUNT(*), MAX(last_message_at)
                  FROM wazzup_chats GROUP BY account""")
            return {r[0]: {'chats': r[1], 'last_message_at': r[2]} for r in cursor.fetchall()}

    @staticmethod
    def _mark_journal_evaluated_wazzup_episodes_tx(cursor):
        """Persist the human-journal link before its 180-day snapshot expires.

        A Wazzup journal evaluation points to an episode only through
        calls.c2d_snapshot_id -> c2d_chat_snapshots and the episode natural key.
        Snapshot retention eventually clears that link, so the episode itself
        keeps a durable marker once a non-draft evaluation exists.
        """
        cursor.execute("""
            UPDATE wazzup_episodes e
               SET journal_evaluated_at = CURRENT_TIMESTAMP
              FROM (
                    SELECT DISTINCT s.wz_channel_id, s.wz_chat_id, s.episode_start
                      FROM c2d_chat_snapshots s
                      JOIN calls c ON c.c2d_snapshot_id = s.id
                     WHERE s.source = 'wazzup'
                       AND COALESCE(c.is_draft, FALSE) = FALSE
                   ) evaluated
             WHERE e.journal_evaluated_at IS NULL
               AND e.channel_id = evaluated.wz_channel_id
               AND e.chat_id = evaluated.wz_chat_id
               AND e.started_at = evaluated.episode_start
        """)
        return cursor.rowcount

    def cleanup_wazzup_messages(self, retention_days=45):
        """Ретеншн Wazzup: удаляет сырые данные старше retention_days.

        45 дней — общее окно обоих аккаунтов (решение владельца 23.09.2026,
        столько же истории «Потока» загружено первоначально); диск базы 5 ГБ.

        Эпизоды с успешной ИИ-оценкой или финальной человеческой оценкой в
        «Журнале оценок» сохраняются бессрочно. Неоценённые эпизоды живут столько
        же, сколько исходные сообщения. Черновик журнала временно защищает
        эпизод, пока существует его snapshot, но не ставит бессрочную метку.
        """
        days = int(retention_days)
        with self._get_cursor() as cursor:
            marked_journal_episodes = self._mark_journal_evaluated_wazzup_episodes_tx(
                cursor)
            cursor.execute("DELETE FROM wazzup_status_receipts WHERE updated_at < now() - interval '2 days'")
            # Idempotency tombstones survive archive pruning; discard only the
            # duplicated text, never a request ID which could otherwise resend.
            cursor.execute("UPDATE wazzup_pilot_outbox SET text='' WHERE text<>'' "
                           "AND created_at < now() - make_interval(days => %s)", (days,))
            cursor.execute(
                "DELETE FROM wazzup_messages WHERE dt < now() - make_interval(days => %s)",
                (days,))
            deleted_messages = cursor.rowcount
            cursor.execute(
                "DELETE FROM wazzup_chats WHERE last_message_at < now() - make_interval(days => %s)",
                (days,))
            deleted_chats = cursor.rowcount
            cursor.execute("""
                DELETE FROM wazzup_episodes e
                 WHERE e.ended_at < now() - make_interval(days => %s)
                   AND e.journal_evaluated_at IS NULL
                   AND NOT EXISTS (
                       SELECT 1
                         FROM ai_evaluation_runs r
                        WHERE r.subject_kind = 'wz_episode'
                          AND r.call_id = e.id
                          AND r.status = 'succeeded'
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM ai_evaluation_meta m
                        WHERE m.subject_kind = 'wz_episode'
                          AND m.call_id = e.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM ai_review_cache rc
                        WHERE rc.subject_kind = 'wz_episode'
                          AND rc.call_id = e.id
                   )
                   AND NOT EXISTS (
                       SELECT 1
                         FROM c2d_chat_snapshots s
                         JOIN calls c ON c.c2d_snapshot_id = s.id
                        WHERE s.source = 'wazzup'
                          AND s.wz_channel_id = e.channel_id
                          AND s.wz_chat_id = e.chat_id
                          AND s.episode_start = e.started_at
                   )
            """, (days,))
            deleted_episodes = cursor.rowcount
        if (deleted_messages or deleted_chats or deleted_episodes
                or marked_journal_episodes):
            logging.info(
                "wazzup retention: удалено %s сообщений, %s чатов, "
                "%s неоценённых эпизодов; отмечено %s эпизодов журнала",
                deleted_messages, deleted_chats, deleted_episodes,
                marked_journal_episodes)
        return {
            'messages': deleted_messages,
            'chats': deleted_chats,
            'episodes': deleted_episodes,
            'journal_marked': marked_journal_episodes,
        }

    def list_wazzup_authors(self, account='op'):
        """Авторы исходящих сообщений Wazzup (по author_id) со статистикой
        и текущей привязкой из wazzup_operator_map. Привязки без сообщений
        в окне ретеншна тоже возвращаются (FULL JOIN) — маппинг не «исчезает».
        Аккаунты не смешиваются: у каждого свои авторы и своя привязка."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT COALESCE(a.author_id, map.author_id) AS author_id,
                       COALESCE(a.author_name, map.author_name) AS author_name,
                       COALESCE(a.messages_count, 0),
                       a.last_message_at,
                       COALESCE(a.chats_count, 0),
                       map.user_id, u.name, COALESCE(map.is_bot, FALSE)
                  FROM (SELECT author_id,
                               (array_agg(author_name ORDER BY dt DESC))[1] AS author_name,
                               COUNT(*) AS messages_count,
                               MAX(dt) AS last_message_at,
                               COUNT(DISTINCT (channel_id, chat_id)) AS chats_count
                          FROM wazzup_messages
                         WHERE is_echo AND author_id IS NOT NULL AND account = %s
                         GROUP BY author_id) a
                  FULL JOIN (SELECT * FROM wazzup_operator_map WHERE account = %s) map
                    ON map.author_id = a.author_id
                  LEFT JOIN users u ON u.id = map.user_id
                 -- Автор сообщений из iCORE привязан к отправителю при отправке
                 -- (wazzup/pilot.py): разбирать руками там нечего, и в списке он
                 -- был бы второй строкой того же человека.
                 WHERE COALESCE(a.author_id, map.author_id) NOT LIKE %s
                 ORDER BY COALESCE(a.messages_count, 0) DESC, author_name
            """, (account, account, self._WAZZUP_ICORE_AUTHOR_LIKE))
            return [{'author_id': r[0], 'author_name': r[1], 'messages_count': r[2],
                     'last_message_at': r[3], 'chats_count': r[4],
                     'user_id': r[5], 'user_name': r[6], 'is_bot': r[7]}
                    for r in cursor.fetchall()]

    def upsert_wazzup_author_map(self, author_id, author_name=None, user_id=None,
                                 is_bot=False, updated_by=None, account='op'):
        """Сохраняет привязку автора Wazzup: user_id (наш оператор) и/или is_bot.
        user_id=None + is_bot=False — сброс привязки (строка остаётся как факт разбора)."""
        author_id = str(author_id or '').strip()
        if not author_id:
            raise ValueError("author_id is required")
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO wazzup_operator_map
                       (author_id, author_name, user_id, is_bot, updated_by, updated_at, account)
                VALUES (%s, %s, %s, %s, %s, now(), %s)
                ON CONFLICT (author_id) DO UPDATE SET
                    author_name = COALESCE(EXCLUDED.author_name, wazzup_operator_map.author_name),
                    user_id = EXCLUDED.user_id,
                    is_bot = EXCLUDED.is_bot,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now(),
                    account = EXCLUDED.account
            """, (author_id, author_name,
                  int(user_id) if user_id is not None else None,
                  bool(is_bot),
                  int(updated_by) if updated_by is not None else None,
                  account))
        return True

    def list_wazzup_operator_candidates(self, department_id=367):
        """Кандидаты для привязки: действующие операторы отдела.

        Порядок — как группы показателей (Верификаторы, Поток, остальные
        направления), чтобы список в «Привязке» читался так же, как отчёт."""
        order = [d['direction_id'] for d in self.WAZZUP_ANALYTICS_DIRECTIONS]
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.direction_id, d.name
                  FROM users u
                  LEFT JOIN directions d ON d.id = u.direction_id
                 WHERE u.role IN ('operator', 'trainee')
                   AND u.status NOT IN ('fired', 'dismissal')
                   AND d.department_id = %s
                 ORDER BY COALESCE(array_position(%s::int[], u.direction_id),
                                   array_length(%s::int[], 1) + 1),
                          d.name, u.name
            """, (int(department_id), order, order))
            return [{'id': r[0], 'name': r[1], 'direction_id': r[2], 'direction_name': r[3]}
                    for r in cursor.fetchall()]

    def wazzup_operator_analytics(self, date_from=None, date_to=None, account='op'):
        """Аналитика по менеджерам Wazzup за период (границы — даты Алматы)
        по ОДНОМУ аккаунту: у «Верификаторов» и «Потока» показатели свои.

        • Диалоги — сумма активных чатов по дням: один чат в два разных дня
          считается дважды. Именно так недельные итоги строит Wazzup.
        • Сообщения — его исходящие.
        • Время ответа — на каждый чат одно значение: первое сообщение менеджера
          после первого сообщения клиента минус время того сообщения клиента.
          Засчитывается ответившему первым, поэтому сумма «первых ответов» по
          строкам ≤ числа диалогов, а сумма диалогов ≥ числа чатов (в одном чате
          могут писать несколько менеджеров).

        Строки группируются по привязанному оператору (несколько author_id одного
        человека сливаются в одну строку), непривязанные — по автору. Боты вне выборки.

        Кроме общего итога возвращаются итоги ПО НАПРАВЛЕНИЯМ (Верификаторы,
        Поток, прочие направления, «без привязки») — раздел делит показатели
        между ними. Считаются они тем же запросом по сырым значениям: среднее и
        медиану времени ответа усреднением строк не получить. Диалоги и там, и
        в общем итоге считаются ВМЕСТЕ с grp, поэтому итог направления — ровно
        сумма его строк, как и показывает подвал таблицы.

        Переписка с номерами «Реестра тестовых номеров» (test_numbers) в показатели
        не входит; номер Wazzup — цифры (contact_phone или сам chat_id).
        """
        from test_numbers import keys as test_keys
        window, params = ["m.account = %s",
                          test_keys.sql_not_test(test_keys.wazzup_phone_sql('m'), digits=True)], [account]
        if date_from:
            # Явный timestamp обязателен. Для date PostgreSQL выбирает другую
            # перегрузку AT TIME ZONE и при UTC-сессии сдвигает границу на +10 ч.
            window.append(
                "m.dt >= (%s::date::timestamp AT TIME ZONE 'Asia/Almaty')"
            )
            params.append(date_from)
        if date_to:
            window.append(
                "m.dt < ((%s::date + 1)::timestamp AT TIME ZONE 'Asia/Almaty')"
            )
            params.append(date_to)
        cte = self._WAZZUP_ANALYTICS_CTE.format(window=' AND '.join(window))
        with self._get_cursor() as cursor:
            cursor.execute(cte + """
                , totals AS (
                    SELECT grp,
                           MAX(user_id) AS user_id,
                           (array_agg(author_name ORDER BY dt DESC))[1] AS author_name,
                           (array_agg(author_id ORDER BY dt DESC))[1] AS author_id,
                           COUNT(*) AS messages_count,
                           COUNT(DISTINCT (local_date, channel_id, chat_id))
                               AS dialogs_count,
                           MAX(dt) AS last_message_at
                      FROM agent_msgs WHERE grp IS NOT NULL GROUP BY grp
                ),
                resp AS (
                    SELECT grp, COUNT(*) AS answered,
                           AVG(secs) AS avg_secs,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY secs) AS median_secs
                      FROM first_reply WHERE grp IS NOT NULL GROUP BY grp
                )
                SELECT t.grp, t.user_id, u.name, t.author_name, t.author_id,
                       t.messages_count, t.dialogs_count, t.last_message_at,
                       COALESCE(r.answered, 0), r.avg_secs, r.median_secs,
                       u.direction_id, d.name
                  FROM totals t
                  LEFT JOIN resp r ON r.grp = t.grp
                  LEFT JOIN users u ON u.id = t.user_id
                  LEFT JOIN directions d ON d.id = u.direction_id
                 ORDER BY t.dialogs_count DESC, t.messages_count DESC
            """, params)
            items = [{'key': r[0], 'user_id': r[1], 'user_name': r[2],
                      'author_name': r[3], 'author_id': r[4],
                      'messages_count': r[5], 'dialogs_count': r[6],
                      'last_message_at': r[7], 'answered_chats': r[8],
                      'avg_response_secs': float(r[9]) if r[9] is not None else None,
                      'median_response_secs': float(r[10]) if r[10] is not None else None,
                      'direction_id': r[11], 'direction_name': r[12]}
                     for r in cursor.fetchall()]
            # Направления И общий итог — ОДНИМ запросом через GROUPING SETS.
            # Двумя запросами числа расходились: вебхук Wazzup пишет непрерывно, а
            # READ COMMITTED даёт каждому запросу свой снимок — «Всего» на экране
            # отличалось от суммы направлений на пришедшее между ними сообщение.
            # Итоговую строку от строки «без привязки» отличает GROUPING(): у
            # обеих direction_id = NULL.
            # LEFT JOIN достаточно: first_reply выведен из agent_msgs, поэтому
            # направления с «первым ответом» — подмножество направлений с
            # сообщениями. Сравнение через IS NOT DISTINCT FROM — из-за тех же NULL.
            cursor.execute(cte + """
                , by_dir AS (
                    SELECT direction_id, GROUPING(direction_id) AS is_total,
                           COUNT(DISTINCT (grp, local_date, channel_id, chat_id))
                               AS chats,
                           COUNT(*) AS messages,
                           COUNT(DISTINCT grp) AS managers
                      FROM agent_msgs WHERE grp IS NOT NULL
                     GROUP BY GROUPING SETS ((direction_id), ())
                ),
                by_dir_resp AS (
                    SELECT direction_id, GROUPING(direction_id) AS is_total,
                           COUNT(*) AS answered,
                           AVG(secs) AS avg_secs,
                           percentile_cont(0.5) WITHIN GROUP (ORDER BY secs)
                               AS median_secs
                      FROM first_reply WHERE grp IS NOT NULL
                     GROUP BY GROUPING SETS ((direction_id), ())
                )
                SELECT t.is_total, t.direction_id, d.name,
                       t.chats, t.messages, t.managers,
                       COALESCE(r.answered, 0), r.avg_secs, r.median_secs
                  FROM by_dir t
                  LEFT JOIN by_dir_resp r
                    ON r.is_total = t.is_total
                   AND r.direction_id IS NOT DISTINCT FROM t.direction_id
                  LEFT JOIN directions d ON d.id = t.direction_id
                 ORDER BY t.is_total DESC, t.messages DESC
            """, params)
            total, directions = None, []
            for r in cursor.fetchall():
                row = {'direction_id': r[1], 'direction_name': r[2],
                       'chats': r[3] or 0, 'messages': r[4] or 0,
                       'managers': r[5] or 0, 'answered_chats': r[6] or 0,
                       'avg_response_secs': float(r[7]) if r[7] is not None else None,
                       'median_response_secs': float(r[8]) if r[8] is not None else None}
                if r[0]:
                    total = row
                else:
                    directions.append(row)
        summary = total or {'chats': 0, 'messages': 0, 'managers': 0,
                            'answered_chats': 0, 'avg_response_secs': None,
                            'median_response_secs': None}
        return {
            'items': items,
            'summary': {'chats': summary['chats'], 'messages': summary['messages'],
                        'managers': summary['managers'],
                        'avg_response_secs': summary['avg_response_secs'],
                        'median_response_secs': summary['median_response_secs'],
                        'answered_chats': summary['answered_chats']},
            'directions': directions,
        }

    @classmethod
    def _render_wazzup_line(cls, msg):
        """Одна строка транскрипта. Время — Алматы (там работают операторы).
        Шаблоны WABA помечаются [шаблон]: их текст одобрен заранее, оценивать
        формулировки бессмысленно; рассылки от ботов помечаются отдельно."""
        from zoneinfo import ZoneInfo
        local = msg['dt'].astimezone(ZoneInfo('Asia/Almaty'))
        stamp = local.strftime('%d.%m %H:%M')
        if not msg['is_echo']:
            who = 'Клиент'
        elif msg['is_bot']:
            who = f"Рассылка ({msg['author_name'] or 'бот'})"
        elif msg['type'] == 'wapi_template':
            who = f"Оператор ({msg['author_name'] or '?'}) [шаблон]"
        else:
            who = f"Оператор ({msg['author_name'] or '?'})"
        parts = []
        if msg['is_deleted']:
            parts.append('[удалено]')
        media = cls._WAZZUP_MEDIA_RU.get(msg['type'])
        if media:
            parts.append(media)
        if msg['text']:
            parts.append(msg['text'])
        if not parts:
            parts.append(f"[{msg['type'] or 'сообщение'}]")
        return f"[{stamp}] {who}: {' '.join(parts)}"

    def build_wazzup_episodes(self, gap_hours=6, force_close_hours=48,
                              force_close_msgs=200, context_tail=10, now=None,
                              account='op'):
        """Нарезает переписку Wazzup на эпизоды и сохраняет закрытые.

        account — эпизоды (единица ИИ-оценки) пока строятся ТОЛЬКО по
        историческому аккаунту: переписка «Потока» в разделе видна, но в
        оценку не идёт — это отдельное решение владельца (объём и критерии).

        Эпизод = подряд идущие сообщения чата с паузами < gap_hours (порог — «дно
        долины» бимодального распределения пауз). Сохраняются только эпизоды,
        закрытые тишиной (последнее сообщение старше gap_hours на момент прогона)
        либо принудительно (длительность >= force_close_hours или >= force_close_msgs
        сообщений — остаток такого чата дособерётся следующими прогонами).

        Идемпотентность: берутся только сообщения с dt позже ended_at последнего
        эпизода чата + UNIQUE(channel_id, chat_id, started_at). Сообщение,
        доставленное вебхуком задним числом внутрь уже собранного эпизода,
        игнорируется (осознанный компромисс — ретраи Wazzup редки и короткие).
        Конкурентные прогоны исключает advisory-лок (второй тихо выходит)."""
        if now is None:
            now = datetime.now(dt_timezone.utc)
        gap = timedelta(hours=float(gap_hours))
        force_span = timedelta(hours=float(force_close_hours))
        stored = 0
        skipped_open = 0
        with self._get_cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)",
                           (self.WAZZUP_EPISODE_LOCK_KEY,))
            if not cursor.fetchone()[0]:
                logging.warning("wazzup episodes: прогон уже идёт, выходим")
                return {'stored': 0, 'skipped_open': 0, 'locked': True}
            cursor.execute("""
                SELECT channel_id, chat_id, MAX(ended_at)
                  FROM wazzup_episodes GROUP BY channel_id, chat_id""")
            last_end = {(r[0], r[1]): r[2] for r in cursor.fetchall()}
            # Фильтр «новее последнего эпизода чата» — в SQL: на плато ретеншна
            # таблица держит ~240 тыс. строк, тянуть их в Python каждую ночь
            # нельзя; штатный прогон читает только последние сутки + открытые хвосты.
            cursor.execute("""
                SELECT m.channel_id, m.chat_id, m.chat_type, m.dt, m.is_echo, m.type,
                       m.text, m.author_id, m.author_name,
                       map.user_id, COALESCE(map.is_bot, FALSE), m.is_deleted
                  FROM wazzup_messages m
                  LEFT JOIN wazzup_operator_map map ON map.author_id = m.author_id
                  LEFT JOIN (SELECT channel_id, chat_id, MAX(ended_at) AS le
                               FROM wazzup_episodes
                              GROUP BY channel_id, chat_id) e
                    ON e.channel_id = m.channel_id AND e.chat_id = m.chat_id
                 WHERE m.account = %s AND (e.le IS NULL OR m.dt > e.le)
                 ORDER BY m.channel_id, m.chat_id, m.dt, m.message_id""", (account,))
            chats = {}
            for r in cursor.fetchall():
                key = (r[0], r[1])
                if last_end.get(key) is not None and r[3] <= last_end[key]:
                    continue
                chats.setdefault(key, []).append({
                    'channel_id': r[0], 'chat_id': r[1], 'chat_type': r[2], 'dt': r[3],
                    'is_echo': r[4], 'type': r[5], 'text': r[6], 'author_id': r[7],
                    'author_name': r[8], 'user_id': r[9], 'is_bot': r[10],
                    'is_deleted': r[11],
                })
            cursor.execute("""
                SELECT channel_id, chat_id, contact_name, contact_phone
                  FROM wazzup_chats""")
            contacts = {(r[0], r[1]): (r[2], r[3]) for r in cursor.fetchall()}
            for key, msgs in chats.items():
                # нарезка по паузе
                episodes, current = [], [msgs[0]]
                for m in msgs[1:]:
                    if m['dt'] - current[-1]['dt'] >= gap:
                        episodes.append(current)
                        current = [m]
                    else:
                        current.append(m)
                episodes.append(current)
                for idx, ep in enumerate(episodes):
                    is_last = idx == len(episodes) - 1
                    closed_by_silence = (now - ep[-1]['dt']) >= gap
                    # принудительная нарезка сверхдлинных: закрываем головы,
                    # хвост оставляем открытым до тишины
                    while ep:
                        force = (len(ep) >= int(force_close_msgs) or
                                 (ep[-1]['dt'] - ep[0]['dt']) >= force_span)
                        if not force:
                            break
                        head, rest = [ep[0]], ep[1:]
                        for m in rest:
                            if (len(head) >= int(force_close_msgs) or
                                    (m['dt'] - head[0]['dt']) >= force_span):
                                break
                            head.append(m)
                        tail = ep[len(head):]
                        if not tail:
                            break
                        stored += self._store_wazzup_episode_tx(
                            cursor, head, contacts, force_closed=True,
                            context_tail=context_tail)
                        ep = tail
                    if not ep:
                        continue
                    if is_last and not closed_by_silence:
                        skipped_open += 1
                        continue
                    stored += self._store_wazzup_episode_tx(
                        cursor, ep, contacts, force_closed=False,
                        context_tail=context_tail)
        logging.info("wazzup episodes: сохранено %s, открытых пропущено %s",
                     stored, skipped_open)
        return {'stored': stored, 'skipped_open': skipped_open, 'locked': False}

    def _store_wazzup_episode_tx(self, cursor, msgs, contacts, force_closed,
                                 context_tail=10):
        """Считает атрибуцию, рендерит транскрипт и сохраняет один эпизод."""
        first, last = msgs[0], msgs[-1]
        key = (first['channel_id'], first['chat_id'])
        inbound = sum(1 for m in msgs if not m['is_echo'])
        outbound = len(msgs) - inbound
        human_out = [m for m in msgs if m['is_echo'] and not m['is_bot']]
        # доминирующий оператор — по привязанным исходящим; непривязанные авторы
        # остаются людьми в транскрипте, но не претендуют на атрибуцию
        per_user = {}
        for m in human_out:
            if m['user_id'] is not None:
                per_user[m['user_id']] = per_user.get(m['user_id'], 0) + 1
        operator_user_id = None
        operator_share = None
        if per_user:
            operator_user_id = max(per_user, key=lambda u: (per_user[u], -u))
            operator_share = round(per_user[operator_user_id] / len(human_out), 3)
        if inbound and human_out:
            kind = 'dialog'
        elif inbound:
            kind = 'unanswered'
        else:
            kind = 'outbound_only'
        authors = {}
        for m in msgs:
            if m['is_echo'] and m['author_id'] is not None:
                a = authors.setdefault(m['author_id'], {
                    'author_id': m['author_id'], 'author_name': m['author_name'],
                    'user_id': m['user_id'], 'is_bot': m['is_bot'], 'messages': 0})
                a['messages'] += 1
        transcript = '\n'.join(self._render_wazzup_line(m) for m in msgs)
        # контекст: хвост переписки ДО эпизода (из прошлых эпизодов этого чата)
        tail_text = None
        if context_tail:
            cursor.execute("""
                SELECT m.channel_id, m.chat_id, m.chat_type, m.dt, m.is_echo, m.type,
                       m.text, m.author_id, m.author_name,
                       map.user_id, COALESCE(map.is_bot, FALSE), m.is_deleted
                  FROM wazzup_messages m
                  LEFT JOIN wazzup_operator_map map ON map.author_id = m.author_id
                 WHERE m.channel_id = %s AND m.chat_id = %s AND m.dt < %s
                 ORDER BY m.dt DESC LIMIT %s""",
                (first['channel_id'], first['chat_id'], first['dt'],
                 int(context_tail)))
            prev = cursor.fetchall()
            if prev:
                prev_msgs = [{
                    'channel_id': r[0], 'chat_id': r[1], 'chat_type': r[2], 'dt': r[3],
                    'is_echo': r[4], 'type': r[5], 'text': r[6], 'author_id': r[7],
                    'author_name': r[8], 'user_id': r[9], 'is_bot': r[10],
                    'is_deleted': r[11],
                } for r in reversed(prev)]
                tail_text = '\n'.join(self._render_wazzup_line(m) for m in prev_msgs)
        contact_name, contact_phone = contacts.get(key, (None, None))
        cursor.execute("""
            INSERT INTO wazzup_episodes (
                channel_id, chat_id, chat_type, contact_name, contact_phone,
                started_at, ended_at, messages_count, inbound_count, outbound_count,
                human_outbound_count, kind, operator_user_id, operator_share,
                authors, force_closed, transcript, context_tail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (channel_id, chat_id, started_at) DO NOTHING
        """, (first['channel_id'], first['chat_id'], first['chat_type'],
              contact_name, contact_phone, first['dt'], last['dt'], len(msgs),
              inbound, outbound, len(human_out), kind, operator_user_id,
              operator_share, Json(list(authors.values())), bool(force_closed),
              transcript, tail_text))
        return cursor.rowcount

    def list_wazzup_episodes(self, day=None, kind=None, operator_user_id=None,
                             limit=50, offset=0):
        """Список эпизодов для проверки/оценки. day — локальная дата Алматы по ended_at.

        Эпизоды с номерами «Реестра тестовых номеров» (test_numbers) в список не идут.
        Колонки условия — без алиаса: оно стоит и в счёте без алиаса, и в выборке с ним."""
        from test_numbers import keys as test_keys
        where, params = ["TRUE", test_keys.sql_not_test(test_keys.wazzup_phone_sql(), digits=True)], []
        if day:
            where.append("(ended_at AT TIME ZONE 'Asia/Almaty')::date = %s")
            params.append(day)
        if kind:
            where.append("kind = %s")
            params.append(kind)
        if operator_user_id is not None:
            where.append("operator_user_id = %s")
            params.append(int(operator_user_id))
        with self._get_cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM wazzup_episodes WHERE {' AND '.join(where)}",
                           params)
            total = cursor.fetchone()[0]
            cursor.execute(f"""
                SELECT e.id, e.channel_id, e.chat_id, e.contact_name, e.contact_phone,
                       e.started_at, e.ended_at, e.messages_count, e.inbound_count,
                       e.outbound_count, e.human_outbound_count, e.kind,
                       e.operator_user_id, u.name, e.operator_share, e.force_closed,
                       LEFT(e.transcript, 300)
                  FROM wazzup_episodes e
                  LEFT JOIN users u ON u.id = e.operator_user_id
                 WHERE {' AND '.join(where)}
                 ORDER BY e.ended_at DESC LIMIT %s OFFSET %s""",
                params + [min(max(int(limit), 1), 200), max(int(offset), 0)])
            items = [{'id': r[0], 'channelId': r[1], 'chatId': r[2],
                      'contactName': r[3], 'contactPhone': r[4],
                      'startedAt': r[5].isoformat(), 'endedAt': r[6].isoformat(),
                      'messagesCount': r[7], 'inboundCount': r[8], 'outboundCount': r[9],
                      'humanOutboundCount': r[10], 'kind': r[11],
                      'operatorUserId': r[12], 'operatorName': r[13],
                      'operatorShare': r[14], 'forceClosed': r[15],
                      'transcriptPreview': r[16]}
                     for r in cursor.fetchall()]
        return {'items': items, 'total': total}

    def get_wazzup_episode(self, episode_id):
        """Полный эпизод с транскриптом и контекстом."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT e.id, e.channel_id, e.chat_id, e.chat_type, e.contact_name,
                       e.contact_phone, e.started_at, e.ended_at, e.messages_count,
                       e.inbound_count, e.outbound_count, e.human_outbound_count,
                       e.kind, e.operator_user_id, u.name, e.operator_share,
                       e.authors, e.force_closed, e.transcript, e.context_tail
                  FROM wazzup_episodes e
                  LEFT JOIN users u ON u.id = e.operator_user_id
                 WHERE e.id = %s""", (int(episode_id),))
            r = cursor.fetchone()
        if not r:
            return None
        return {'id': r[0], 'channelId': r[1], 'chatId': r[2], 'chatType': r[3],
                'contactName': r[4], 'contactPhone': r[5],
                'startedAt': r[6].isoformat(), 'endedAt': r[7].isoformat(),
                'messagesCount': r[8], 'inboundCount': r[9], 'outboundCount': r[10],
                'humanOutboundCount': r[11], 'kind': r[12], 'operatorUserId': r[13],
                'operatorName': r[14], 'operatorShare': r[15], 'authors': r[16],
                'forceClosed': r[17], 'transcript': r[18], 'contextTail': r[19]}

    # ── Wazzup (Верификаторы): случайный эпизод чата для оценки в журнале ─────

    def pick_wazzup_episode(self, operator_id=None, date_from=None, date_to=None,
                            min_messages=None, max_messages=None,
                            exclude='any', evaluator_id=None, department_id=None,
                            exclude_episode_ids=None):
        """Случайный эпизод Wazzup по фильтрам. Кандидаты — готовые dialog-эпизоды
        из wazzup_episodes (ночная сборка build_wazzup_episodes) с атрибутированным
        оператором. exclude как у Chat2Desk: 'any' — без эпизодов, уже оценённых
        кем-либо (calls -> снапшот с ключами эпизода), 'mine' — оценённых этим
        проверяющим, 'none' — без исключений. exclude_episode_ids — эпизоды,
        отсеянные в этом же запросе (переписку съел ретеншн).
        Эпизоды с номерами «Реестра тестовых номеров» (test_numbers) не предлагаются.
        Возвращает (episode|None, candidates_count)."""
        from test_numbers import keys as test_keys
        where = ["e.kind = 'dialog'", "e.operator_user_id IS NOT NULL",
                 test_keys.sql_not_test(test_keys.wazzup_phone_sql('e'), digits=True)]
        params = []
        if operator_id is not None:
            where.append("e.operator_user_id = %s")
            params.append(int(operator_id))
        if department_id is not None:
            where.append("u.department_id = %s")
            params.append(int(department_id))
        if date_from:
            where.append("(e.started_at AT TIME ZONE 'Asia/Almaty')::date >= %s")
            params.append(date_from)
        if date_to:
            where.append("(e.started_at AT TIME ZONE 'Asia/Almaty')::date <= %s")
            params.append(date_to)
        if min_messages is not None:
            where.append("e.messages_count >= %s")
            params.append(int(min_messages))
        if max_messages is not None:
            where.append("e.messages_count <= %s")
            params.append(int(max_messages))
        if exclude_episode_ids:
            where.append("e.id != ALL(%s)")
            params.append([int(v) for v in exclude_episode_ids])
        exclude = str(exclude or 'none').strip().lower()
        evaluated_exists = """NOT EXISTS (
            SELECT 1 FROM calls c
            JOIN c2d_chat_snapshots s ON s.id = c.c2d_snapshot_id
            WHERE s.source = 'wazzup'
              AND s.wz_channel_id = e.channel_id AND s.wz_chat_id = e.chat_id
              AND s.episode_start = e.started_at{extra})"""
        if exclude == 'mine' and evaluator_id is not None:
            where.append(evaluated_exists.format(extra=" AND c.evaluator_id = %s"))
            params.append(int(evaluator_id))
        elif exclude in ('any', 'mine'):
            where.append(evaluated_exists.format(extra=""))
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT e.id, e.channel_id, e.chat_id, e.chat_type,
                       e.started_at, e.ended_at, e.messages_count, e.inbound_count,
                       e.operator_user_id, u.name, e.contact_name, e.contact_phone,
                       COUNT(*) OVER ()
                  FROM wazzup_episodes e
                  JOIN users u ON u.id = e.operator_user_id
                 WHERE {' AND '.join(where)}
                 ORDER BY random()
                 LIMIT 1
            """, params)
            row = cursor.fetchone()
        if not row:
            return None, 0
        return {
            'episode_id': row[0], 'channel_id': row[1], 'chat_id': row[2],
            'chat_type': row[3], 'episode_start': row[4], 'episode_end': row[5],
            'msg_count': row[6], 'inbound_count': row[7],
            'operator_id': row[8], 'operator_name': row[9],
            'client_name': row[10], 'client_phone': row[11],
        }, int(row[12] or 0)

    def fetch_wazzup_episode_messages(self, channel_id, chat_id, ep_start, ep_end):
        """Сообщения эпизода по возрастанию. matched_name — имя оператора из
        привязки авторов (wazzup_operator_map -> users): в ленте оценки автора
        подписываем им, а не ником Wazzup."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT m.message_id, m.dt, m.is_echo, m.type, m.text, m.content_uri,
                       m.author_name, COALESCE(map.is_bot, FALSE), m.is_deleted,
                       u.name
                  FROM wazzup_messages m
                  LEFT JOIN wazzup_operator_map map ON map.author_id = m.author_id
                  LEFT JOIN users u ON u.id = map.user_id
                 WHERE m.channel_id = %s AND m.chat_id = %s
                   AND m.dt >= %s AND m.dt <= %s
                 ORDER BY m.dt, m.message_id
            """, (str(channel_id), str(chat_id), ep_start, ep_end))
            return [{
                'message_id': r[0], 'dt': r[1], 'is_echo': r[2], 'type': r[3],
                'text': r[4], 'content_uri': r[5], 'author_name': r[6],
                'is_bot': r[7], 'is_deleted': r[8], 'matched_name': r[9],
            } for r in cursor.fetchall()]

    @classmethod
    def _wazzup_episode_kept_sql(cls, cursor):
        """Условие «эпизод e нельзя удалять». Таблицы ИИ-оценки создаёт своя
        миграция (call_qa/rag), на стенде их может не быть — берём только
        существующие, иначе EXISTS по несуществующей таблице уронит запрос."""
        cursor.execute("SELECT name FROM unnest(%s::text[]) AS t(name) "
                       "WHERE to_regclass(name) IS NOT NULL",
                       ([table for table, _ in cls.WAZZUP_EPISODE_REFERENCES],))
        present = {row[0] for row in cursor.fetchall()}
        parts = ["e.journal_evaluated_at IS NOT NULL",
                 "EXISTS (SELECT 1 FROM c2d_chat_snapshots s WHERE s.source = 'wazzup'"
                 " AND s.wz_channel_id = e.channel_id AND s.wz_chat_id = e.chat_id"
                 " AND s.episode_start = e.started_at)"]
        for table, column in cls.WAZZUP_EPISODE_REFERENCES:
            if table in present:
                parts.append(f"EXISTS (SELECT 1 FROM {table} x WHERE x.subject_kind = 'wz_episode'"
                             f" AND x.{column} = e.id)")
        return '(' + ' OR '.join(parts) + ')'

    def retime_wazzup_inbound_after_replies(self, account='op', apply=False, waiting_since=None):
        """Разовая починка сообщений с минутным временем, которые пришли вовремя,
        но ПОСЛЕ ответа из окна iCORE с точным временем позже их минуты, —
        сохранённых до того, как приёмник начал ставить их за таким ответом
        (wazzup/delivery.py, «Ответы из окна iCORE»; жалоба 19.10.2026).

        Проход тот же, что у приёмника (delivery.replay_after_replies): чат — по
        порядку прихода, created_at — момент вебхука (строку аккаунта с вебхуком
        вставляет только приёмник, ответ iCORE — ручка отправки). Сначала одним
        чтением находятся чаты со сдвигами, потом каждый чат чинится своей
        короткой транзакцией и заново читается под блокировкой: вебхуки этих
        чатов ждут доли секунды, а не всю починку. Строка, которую за это время
        поменяли (не совпало старое время), не трогается — чат в skipped_chats,
        повторный запуск его доделает.

        Эпизоды. Оценённый (_wazzup_episode_kept_sql) не удаляется никогда; его
        сообщение, которое ушло бы за конец эпизода, остаётся на месте
        (kept_in_place): сборка подхватила бы его отдельным эпизодом. Неоценённые
        эпизоды чата с самого раннего сдвинутого сообщения удаляются — их
        пересобирает build_wazzup_episodes (ручка «пересобрать» или ночь);
        удаляются только те, что сборка соберёт заново: позже конца последнего
        оценённого эпизода чата. Неоценённый со сдвинутым сообщением перед
        оценённым остаётся как был (stale_episodes).

        waiting_since — вернуть в «ждут ответа» чаты, которые счётчик пропустил
        из-за этой ошибки: клиент написал последним, после ответа оператора, а
        счётчик сравнил с ответом его минуту и счёл отвеченным. Возвращаются
        только сдвинутые входящие, чьё старое время не позже последнего ответа
        (answered_through), а новое — позже него и не раньше waiting_since, и
        только если счётчик чата пуст, последнее сообщение чата — такое входящее
        и после него не приходило ни одного ответа. Пустой счётчик при входящем
        позже answered_through значит «Ответ не нужен» — такие не трогаются.
        None — счётчик не трогать.

        apply=False — холостой прогон: только чтение (годится и для соединения
        только на чтение), итог тот же. Повторный запуск ничего не находит:
        сдвинутое время уже не минутное."""
        from wazzup import accounts as wz_accounts
        from wazzup.delivery import replay_after_replies
        if (wz_accounts.ACCOUNTS.get(account) or {}).get('source') != 'webhook':
            raise ValueError(f"починка только для аккаунта с вебхуком, а не {account!r}")
        result = {'account': account, 'applied': bool(apply), 'messages': 0, 'chats': 0,
                  'kept_in_place': 0, 'episodes_deleted': {}, 'episodes_kept': [],
                  'stale_episodes': [], 'waiting_chats': [], 'waiting_messages': 0,
                  'skipped_chats': []}
        with self._get_cursor() as cursor:
            # Точное время в чате канала с минутным временем бывает только у ответа
            # из окна iCORE (он есть в wazzup_pilot_outbox) и у опоздавшего
            # (wazzup_dt): только в таких чатах правилу есть за кем ставить. Обе
            # выборки малы — проход по каждому сообщению базе с 0,1 CPU не по силам.
            cursor.execute("""
                SELECT m.channel_id, m.chat_id
                  FROM wazzup_pilot_outbox o
                  JOIN wazzup_messages m ON m.message_id = o.message_id AND m.account = o.account
                 WHERE o.account = %s AND o.message_id IS NOT NULL
                UNION
                SELECT channel_id, chat_id FROM wazzup_messages
                 WHERE account = %s AND wazzup_dt IS NOT NULL""", (account, account))
            keys = cursor.fetchall()
        chats = {}
        # Историю — пачками чатов: ~70 тыс. строк одним ответом не укладываются в
        # statement_timeout соединения на чтение (30 с).
        for start in range(0, len(keys), self.WAZZUP_RETIME_CHATS_PER_READ):
            batch = keys[start:start + self.WAZZUP_RETIME_CHATS_PER_READ]
            with self._get_cursor() as cursor:
                cursor.execute("""
                    SELECT m.message_id, m.channel_id, m.chat_id, m.is_echo, m.dt,
                           m.created_at, m.wazzup_dt IS NOT NULL
                      FROM wazzup_messages m
                      JOIN unnest(%s::text[], %s::text[]) AS c(channel_id, chat_id)
                        ON m.channel_id = c.channel_id AND m.chat_id = c.chat_id
                     WHERE m.account = %s
                     ORDER BY m.channel_id, m.chat_id, m.created_at, m.dt, m.message_id""",
                    ([k[0] for k in batch], [k[1] for k in batch], account))
                for message_id, channel_id, chat_id, is_echo, dt, created_at, late in cursor.fetchall():
                    chats.setdefault((channel_id, chat_id), []).append({
                        'message_id': message_id, 'is_echo': is_echo, 'dt': dt,
                        'created_at': created_at, 'late': late})
        for key in [key for key, history in chats.items() if replay_after_replies(history)]:
            with self._get_cursor() as cursor:
                outcome = self._retime_wazzup_chat_after_replies_tx(
                    cursor, account, key, apply, waiting_since)
            result['kept_in_place'] += outcome['kept_in_place']
            if outcome['skipped']:
                result['skipped_chats'].append(key[1])
                continue
            if not outcome['moves']:
                continue
            result['messages'] += len(outcome['moves'])
            result['chats'] += 1
            for kind in outcome['episodes_deleted']:
                result['episodes_deleted'][kind] = result['episodes_deleted'].get(kind, 0) + 1
            result['episodes_kept'].extend(outcome['episodes_kept'])
            result['stale_episodes'].extend(outcome['stale_episodes'])
            if outcome['waiting']:
                result['waiting_chats'].append(key[1])
                result['waiting_messages'] += len(outcome['waiting'])
        logging.info("wazzup inbound retime (%s, apply=%s): сообщений %s, чатов %s, оставлено на "
                     "месте %s, эпизодов удалено %s, оценённых затронуто %s, устаревших %s, "
                     "ждут ответа: чатов %s, отложено чатов %s", account, apply, result['messages'],
                     result['chats'], result['kept_in_place'], result['episodes_deleted'],
                     len(result['episodes_kept']), len(result['stale_episodes']),
                     len(result['waiting_chats']), len(result['skipped_chats']))
        return result

    def _retime_wazzup_chat_after_replies_tx(self, cursor, account, key, apply, waiting_since):
        """Один чат разовой починки retime_wazzup_inbound_after_replies — в своей
        транзакции. Порядок блокировок как у приёмника: строка счётчика «ждут
        ответа» (её берёт триггер вставки), потом сообщения, потом сводка чата."""
        from wazzup.delivery import replay_after_replies
        from wazzup.pilot import EXCLUDED_CHANNELS
        channel_id, chat_id = key
        out = {'moves': [], 'kept_in_place': 0, 'episodes_deleted': [], 'episodes_kept': [],
               'stale_episodes': [], 'waiting': [], 'skipped': False}
        # Ночная сборка и кнопка «пересобрать» ждут конца транзакции чата.
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (self.WAZZUP_EPISODE_LOCK_KEY,))
        counts_waiting = (waiting_since is not None and account == 'op'
                          and channel_id not in EXCLUDED_CHANNELS)
        state = None
        if counts_waiting:
            cursor.execute("""
                SELECT answered_through, unread_count FROM wazzup_chat_read_state
                 WHERE account = %s AND channel_id = %s AND chat_id = %s"""
                           + (" FOR UPDATE" if apply else ""), (account, channel_id, chat_id))
            state = cursor.fetchone()
        cursor.execute("""
            SELECT message_id, is_echo, dt, created_at, wazzup_dt IS NOT NULL, is_deleted, chat_type
              FROM wazzup_messages
             WHERE account = %s AND channel_id = %s AND chat_id = %s
             ORDER BY created_at, dt, message_id""", (account, channel_id, chat_id))
        history = [{'message_id': r[0], 'is_echo': r[1], 'dt': r[2], 'created_at': r[3],
                    'late': r[4], 'is_deleted': r[5], 'chat_type': r[6]} for r in cursor.fetchall()]
        moves = replay_after_replies(history)
        if not moves:
            return out
        kept_sql = self._wazzup_episode_kept_sql(cursor)
        cursor.execute(f"""
            SELECT e.id, e.kind, e.started_at, e.ended_at, {kept_sql}
              FROM wazzup_episodes e
             WHERE e.channel_id = %s AND e.chat_id = %s
             ORDER BY e.started_at""", key)
        episodes = cursor.fetchall()
        keep = set()
        while True:
            crossing = {mid for mid, old, new in moves
                        for _, _, started, ended, kept in episodes
                        if kept and started <= old <= ended < new}
            if crossing <= keep:
                break
            keep |= crossing
            moves = replay_after_replies(history, frozenset(keep))
        out['kept_in_place'] = len(keep)
        if not moves:
            return out
        if apply:
            cursor.execute("""
                UPDATE wazzup_messages m
                   SET dt = v.new_dt
                  FROM unnest(%s::text[], %s::timestamptz[], %s::timestamptz[])
                       AS v(message_id, old_dt, new_dt)
                 WHERE m.message_id = v.message_id AND m.account = %s
                   AND m.dt = v.old_dt AND m.wazzup_dt IS NULL""",
                ([mid for mid, _, _ in moves], [old for _, old, _ in moves],
                 [new for _, _, new in moves], account))
            if cursor.rowcount != len(moves):
                # Строку успели поменять: чат целиком откатывается и ждёт повтора.
                cursor.connection.rollback()
                out['skipped'] = True
                return out
        since = min(old for _, old, _ in moves)
        kept_end = max((ended for _, _, _, ended, kept in episodes if kept), default=None)
        out['episodes_kept'] = [eid for eid, _, started, ended, kept in episodes
                                if kept and any(started <= old <= ended for _, old, _ in moves)]
        doomed = [eid for eid, _, started, ended, kept in episodes
                  if not kept and ended >= since and (kept_end is None or started > kept_end)]
        out['stale_episodes'] = [eid for eid, _, started, ended, kept in episodes
                                 if not kept and ended >= since and eid not in doomed
                                 and any(started <= old <= ended for _, old, _ in moves)]
        if apply:
            self._refresh_wazzup_chat_tx(cursor, channel_id, chat_id, account=account)
            if doomed:
                cursor.execute(f"""
                    DELETE FROM wazzup_episodes e
                     WHERE e.id = ANY(%s) AND NOT {kept_sql}
                    RETURNING e.kind""", (doomed,))
                out['episodes_deleted'] = [row[0] for row in cursor.fetchall()]
        else:
            out['episodes_deleted'] = [kind for eid, kind, *_ in episodes if eid in doomed]
        out['moves'] = moves
        if not counts_waiting or not state or state[0] is None or state[1]:
            return out
        answered = state[0]
        old = {mid: before for mid, before, _ in moves}
        placed = {mid: after for mid, _, after in moves}
        alive = [m for m in history if not m['is_deleted']]
        last = max(alive, key=lambda m: (placed.get(m['message_id'], m['dt']), m['message_id']))
        replied_later = any(m['is_echo'] and m['created_at'] > last['created_at'] for m in alive)
        waiting = sorted((placed[m['message_id']], m['message_id']) for m in alive
                         if not m['is_echo'] and m['chat_type'] == 'whatsapp'
                         and m['message_id'] in placed
                         and old[m['message_id']] <= answered < placed[m['message_id']]
                         and placed[m['message_id']] >= waiting_since)
        if replied_later or not waiting or waiting[-1][1] != last['message_id']:
            return out
        out['waiting'] = waiting
        if apply:
            cursor.execute("""
                INSERT INTO wazzup_unanswered_messages
                    (account, channel_id, chat_id, message_id, sent_at)
                SELECT %s, %s, %s, v.message_id, v.sent_at
                  FROM unnest(%s::text[], %s::timestamptz[]) AS v(message_id, sent_at)
                ON CONFLICT DO NOTHING""",
                (account, channel_id, chat_id, [mid for _, mid in waiting],
                 [sent for sent, _ in waiting]))
            inserted = cursor.rowcount
            if inserted:
                cursor.execute("""
                    UPDATE wazzup_chat_read_state
                       SET unread_count = unread_count + %s, last_inbound_id = %s,
                           revision = revision + 1, updated_at = now()
                     WHERE account = %s AND channel_id = %s AND chat_id = %s""",
                    (inserted, waiting[-1][1], account, channel_id, chat_id))
        return out
