"""База данных: ChatApp.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    datetime,
    dt_timezone,
    execute_batch,
    Json,
    logging,
    timedelta,
)


class ChatappMixin:
    def chatapp_token_load(self):
        """Пара токенов ChatApp из БД (формат ответа tokens.make) или None."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT access_token, access_expires_at, refresh_token, refresh_expires_at
                  FROM chatapp_tokens WHERE id = 1
            """)
            row = cursor.fetchone()
        if not row or not row[0]:
            return None
        return {
            'accessToken': row[0],
            'accessTokenEndTime': int(row[1].timestamp()) if row[1] else None,
            'refreshToken': row[2],
            'refreshTokenEndTime': int(row[3].timestamp()) if row[3] else None,
        }

    def chatapp_token_save(self, data):
        """Сохранить пару токенов (квота 100 tokens.make в сутки — храним в БД,
        чтобы рестарты и несколько воркеров её не сжигали)."""
        def moment(value):
            return (datetime.fromtimestamp(int(value), dt_timezone.utc)
                    if value else None)

        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO chatapp_tokens (id, access_token, access_expires_at,
                                            refresh_token, refresh_expires_at, updated_at)
                VALUES (1, %s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    access_expires_at = EXCLUDED.access_expires_at,
                    refresh_token = EXCLUDED.refresh_token,
                    refresh_expires_at = EXCLUDED.refresh_expires_at,
                    updated_at = now()
            """, (data.get('accessToken'), moment(data.get('accessTokenEndTime')),
                  data.get('refreshToken'), moment(data.get('refreshTokenEndTime'))))

    def store_chatapp_chats(self, rows):
        """Upsert пула чатов. messages_synced_until не трогаем — им владеет синк."""
        rows = [r for r in (rows or []) if r.get('chat_id') and r.get('license_id')]
        if not rows:
            return 0
        with self._get_cursor() as cursor:
            execute_batch(cursor, """
                INSERT INTO chatapp_chats (
                    license_id, messenger_type, chat_id, internal_id, chat_type,
                    name, phone, responsible_employee_id, status, last_time, synced_at)
                VALUES (%(license_id)s, %(messenger_type)s, %(chat_id)s, %(internal_id)s,
                        %(chat_type)s, %(name)s, %(phone)s, %(responsible_employee_id)s,
                        %(status)s, %(last_time)s, now())
                ON CONFLICT (license_id, messenger_type, chat_id) DO UPDATE SET
                    internal_id = EXCLUDED.internal_id,
                    chat_type = EXCLUDED.chat_type,
                    name = EXCLUDED.name,
                    phone = EXCLUDED.phone,
                    responsible_employee_id = EXCLUDED.responsible_employee_id,
                    status = EXCLUDED.status,
                    last_time = GREATEST(chatapp_chats.last_time, EXCLUDED.last_time),
                    synced_at = now()
            """, rows, page_size=200)
        return len(rows)

    def store_chatapp_messages(self, rows):
        """Upsert сообщений. Повторный проход окна не плодит дублей."""
        rows = [r for r in (rows or []) if r.get('message_id')]
        if not rows:
            return 0
        with self._get_cursor() as cursor:
            execute_batch(cursor, """
                INSERT INTO chatapp_messages (
                    license_id, messenger_type, chat_id, message_id, dt, side, type,
                    subtype, text, file_link, file_name, file_content_type,
                    employee_id, app_id, app_sender, client_name, is_deleted)
                VALUES (%(license_id)s, %(messenger_type)s, %(chat_id)s, %(message_id)s,
                        %(dt)s, %(side)s, %(type)s, %(subtype)s, %(text)s, %(file_link)s,
                        %(file_name)s, %(file_content_type)s, %(employee_id)s, %(app_id)s,
                        %(app_sender)s, %(client_name)s, %(is_deleted)s)
                ON CONFLICT (license_id, messenger_type, chat_id, message_id) DO UPDATE SET
                    text = EXCLUDED.text,
                    is_deleted = EXCLUDED.is_deleted,
                    file_link = EXCLUDED.file_link
            """, rows, page_size=200)
        return len(rows)

    def chatapp_chats_needing_messages(self, since, force=False):
        """Чаты, у которых активность новее, чем уже вытянутая переписка.

        force=True снимает проверку «уже синкали» — нужно, когда период выбран
        руками в разделе: иначе повторный синк того же диапазона молча ничего
        не сделает."""
        fresh_only = "" if force else \
            "AND (messages_synced_until IS NULL OR messages_synced_until < last_time)"
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT license_id, messenger_type, chat_id, name, phone,
                       messages_synced_until, last_time
                  FROM chatapp_chats
                 WHERE last_time >= %s
                   {fresh_only}
                 ORDER BY last_time DESC
            """, (since,))
            return [{'license_id': r[0], 'messenger_type': r[1], 'chat_id': r[2],
                     'name': r[3], 'phone': r[4], 'synced_until': r[5], 'last_time': r[6]}
                    for r in cursor.fetchall()]

    def mark_chatapp_chat_synced(self, license_id, messenger_type, chat_id, until):
        """Отметка «докуда вытянута переписка». Только вперёд: догрузка старого
        периода не должна откатывать маркер и заставлять перечитывать свежее."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE chatapp_chats
                   SET messages_synced_until = GREATEST(
                           COALESCE(messages_synced_until, %s), %s)
                 WHERE license_id = %s AND messenger_type = %s AND chat_id = %s
            """, (until, until, int(license_id), str(messenger_type), str(chat_id)))

    def sync_chatapp_employees(self, employees):
        """Обновить справочник сотрудников ChatApp, не затирая ручную привязку."""
        rows = [{
            'employee_id': int(e['id']),
            'employee_name': e.get('fullName'),
            'email': e.get('email'),
            'role_name': (e.get('role') or {}).get('name'),
        } for e in (employees or []) if e.get('id') is not None]
        if not rows:
            return 0
        with self._get_cursor() as cursor:
            execute_batch(cursor, """
                INSERT INTO chatapp_operator_map (employee_id, employee_name, email, role_name)
                VALUES (%(employee_id)s, %(employee_name)s, %(email)s, %(role_name)s)
                ON CONFLICT (employee_id) DO UPDATE SET
                    employee_name = EXCLUDED.employee_name,
                    email = EXCLUDED.email,
                    role_name = EXCLUDED.role_name
            """, rows, page_size=100)
        return len(rows)

    def list_chatapp_authors(self):
        """Сотрудники ChatApp с их привязкой и объёмом исходящих (для UI привязки)."""
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT map.employee_id, map.employee_name, map.email, map.role_name,
                       map.user_id, u.name, map.is_bot,
                       COALESCE(stat.messages, 0), stat.last_dt
                  FROM chatapp_operator_map map
                  LEFT JOIN users u ON u.id = map.user_id
                  LEFT JOIN (
                        SELECT m.employee_id, COUNT(*) AS messages, MAX(m.dt) AS last_dt
                          FROM chatapp_messages m
                         WHERE {self._CHATAPP_HUMAN_OUT} AND m.employee_id IS NOT NULL
                         GROUP BY m.employee_id
                  ) stat ON stat.employee_id = map.employee_id
                 ORDER BY COALESCE(stat.messages, 0) DESC, map.employee_name
            """)
            return [{'employee_id': r[0], 'employee_name': r[1], 'email': r[2],
                     'role_name': r[3], 'user_id': r[4], 'user_name': r[5],
                     'is_bot': r[6], 'messages': r[7],
                     'last_message_at': r[8].isoformat() if r[8] else None}
                    for r in cursor.fetchall()]

    def upsert_chatapp_author_map(self, employee_id, user_id=None, is_bot=None,
                                  updated_by=None):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO chatapp_operator_map (employee_id, user_id, is_bot,
                                                  updated_by, updated_at)
                VALUES (%s, %s, COALESCE(%s, FALSE), %s, now())
                ON CONFLICT (employee_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    is_bot = COALESCE(%s, chatapp_operator_map.is_bot),
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
            """, (int(employee_id),
                  int(user_id) if user_id is not None else None,
                  is_bot, int(updated_by) if updated_by is not None else None,
                  is_bot))

    def list_chatapp_operator_candidates(self, department_code='tez'):
        """Кандидаты для привязки — действующие операторы отдела ТЭЗ."""
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.id, u.name, u.direction_id, d.name
                  FROM users u
                  LEFT JOIN directions d ON d.id = u.direction_id
                  JOIN departments dep ON dep.id = u.department_id
                 WHERE u.role IN ('operator', 'trainee')
                   AND u.status NOT IN ('fired', 'dismissal')
                   AND lower(dep.code) = %s
                 ORDER BY u.name
            """, (str(department_code).lower(),))
            return [{'id': r[0], 'name': r[1], 'direction_id': r[2], 'direction_name': r[3]}
                    for r in cursor.fetchall()]

    def build_chatapp_episodes(self, gap_hours=6, force_close_hours=48,
                               force_close_msgs=200, now=None):
        """Нарезать переписку ChatApp на эпизоды и сохранить закрытые.

        Логика та же, что у build_wazzup_episodes: эпизод — подряд идущие
        сообщения чата с паузами < gap_hours; сохраняются только закрытые
        тишиной либо принудительно (слишком длинные). Идемпотентность —
        UNIQUE(license_id, messenger_type, chat_id, started_at) плюс отбор
        сообщений новее последнего эпизода чата."""
        if now is None:
            now = datetime.now(dt_timezone.utc)
        gap = timedelta(hours=float(gap_hours))
        force_span = timedelta(hours=float(force_close_hours))
        stored, skipped_open = 0, 0
        with self._get_cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)",
                           (self.CHATAPP_EPISODE_LOCK_KEY,))
            if not cursor.fetchone()[0]:
                logging.warning("chatapp episodes: прогон уже идёт, выходим")
                return {'stored': 0, 'skipped_open': 0, 'locked': True}
            cursor.execute(f"""
                SELECT m.license_id, m.messenger_type, m.chat_id, m.dt, m.side,
                       m.employee_id, m.app_id, m.app_sender, m.type,
                       map.user_id, COALESCE(map.is_bot, FALSE),
                       ({self._CHATAPP_HUMAN_OUT}) AS human_out
                  FROM chatapp_messages m
                  LEFT JOIN chatapp_operator_map map ON map.employee_id = m.employee_id
                  LEFT JOIN (SELECT license_id, messenger_type, chat_id,
                                    MAX(ended_at) AS le
                               FROM chatapp_episodes
                              GROUP BY license_id, messenger_type, chat_id) e
                    ON e.license_id = m.license_id
                   AND e.messenger_type = m.messenger_type
                   AND e.chat_id = m.chat_id
                 WHERE (e.le IS NULL OR m.dt > e.le)
                   AND m.type <> 'system'
                 ORDER BY m.license_id, m.messenger_type, m.chat_id, m.dt, m.message_id
            """)
            chats = {}
            for r in cursor.fetchall():
                chats.setdefault((r[0], r[1], r[2]), []).append({
                    'license_id': r[0], 'messenger_type': r[1], 'chat_id': r[2],
                    'dt': r[3], 'side': r[4], 'employee_id': r[5], 'app_id': r[6],
                    'app_sender': r[7], 'type': r[8], 'user_id': r[9],
                    'is_bot': r[10], 'human_out': r[11],
                })
            cursor.execute("""
                SELECT license_id, messenger_type, chat_id, name, phone
                  FROM chatapp_chats""")
            contacts = {(r[0], r[1], r[2]): (r[3], r[4]) for r in cursor.fetchall()}
            for key, msgs in chats.items():
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
                        stored += self._store_chatapp_episode_tx(
                            cursor, head, contacts, force_closed=True)
                        ep = tail
                    if not ep:
                        continue
                    if is_last and not closed_by_silence:
                        skipped_open += 1
                        continue
                    stored += self._store_chatapp_episode_tx(
                        cursor, ep, contacts, force_closed=False)
        logging.info("chatapp episodes: сохранено %s, открытых пропущено %s",
                     stored, skipped_open)
        return {'stored': stored, 'skipped_open': skipped_open, 'locked': False}

    def _store_chatapp_episode_tx(self, cursor, msgs, contacts, force_closed):
        """Атрибуция и запись одного эпизода ChatApp."""
        first, last = msgs[0], msgs[-1]
        key = (first['license_id'], first['messenger_type'], first['chat_id'])
        inbound = sum(1 for m in msgs if m['side'] == 'in')
        outbound = len(msgs) - inbound
        human_out = [m for m in msgs if m['human_out'] and not m['is_bot']]
        # доминирующий оператор — по привязанным исходящим; непривязанные авторы
        # остаются в authors, но на атрибуцию не претендуют
        per_user = {}
        for m in human_out:
            if m['user_id'] is not None:
                per_user[m['user_id']] = per_user.get(m['user_id'], 0) + 1
        operator_user_id, operator_share = None, None
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
            if m['human_out'] and m['employee_id'] is not None:
                a = authors.setdefault(m['employee_id'], {
                    'employee_id': m['employee_id'], 'user_id': m['user_id'],
                    'is_bot': m['is_bot'], 'messages': 0})
                a['messages'] += 1
        contact_name, contact_phone = contacts.get(key, (None, None))
        cursor.execute("""
            INSERT INTO chatapp_episodes (
                license_id, messenger_type, chat_id, contact_name, contact_phone,
                started_at, ended_at, messages_count, inbound_count, outbound_count,
                human_outbound_count, kind, operator_user_id, operator_share,
                authors, force_closed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (license_id, messenger_type, chat_id, started_at) DO NOTHING
        """, (first['license_id'], first['messenger_type'], first['chat_id'],
              contact_name, contact_phone, first['dt'], last['dt'], len(msgs),
              inbound, outbound, len(human_out), kind, operator_user_id,
              operator_share, Json(list(authors.values())), bool(force_closed)))
        return cursor.rowcount

    def pick_chatapp_episode(self, operator_id=None, date_from=None, date_to=None,
                             min_messages=None, max_messages=None,
                             exclude='any', evaluator_id=None, department_id=None,
                             exclude_episode_ids=None):
        """Случайный эпизод ChatApp по фильтрам — как pick_wazzup_episode.
        Эпизоды с номерами «Реестра тестовых номеров» (test_numbers) не предлагаются.
        Возвращает (episode|None, candidates_count)."""
        from test_numbers import keys as test_keys
        where = ["e.kind = 'dialog'", "e.operator_user_id IS NOT NULL",
                 test_keys.sql_not_test('e.contact_phone', digits=True)]
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
            WHERE s.source = 'chatapp'
              AND s.wz_channel_id = e.license_id::text || ':' || e.messenger_type
              AND s.wz_chat_id = e.chat_id
              AND s.episode_start = e.started_at{extra})"""
        if exclude == 'mine' and evaluator_id is not None:
            where.append(evaluated_exists.format(extra=" AND c.evaluator_id = %s"))
            params.append(int(evaluator_id))
        elif exclude in ('any', 'mine'):
            where.append(evaluated_exists.format(extra=""))
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT e.id, e.license_id, e.messenger_type, e.chat_id,
                       e.started_at, e.ended_at, e.messages_count, e.inbound_count,
                       e.operator_user_id, u.name, e.contact_name, e.contact_phone,
                       COUNT(*) OVER ()
                  FROM chatapp_episodes e
                  JOIN users u ON u.id = e.operator_user_id
                 WHERE {' AND '.join(where)}
                 ORDER BY random()
                 LIMIT 1
            """, params)
            row = cursor.fetchone()
        if not row:
            return None, 0
        return {
            'episode_id': row[0], 'license_id': row[1], 'messenger_type': row[2],
            'chat_id': row[3], 'episode_start': row[4], 'episode_end': row[5],
            'msg_count': row[6], 'inbound_count': row[7],
            'operator_id': row[8], 'operator_name': row[9],
            'client_name': row[10], 'client_phone': row[11],
        }, int(row[12] or 0)

    def fetch_chatapp_episode_messages(self, license_id, messenger_type, chat_id,
                                       ep_start, ep_end):
        """Сообщения эпизода по возрастанию. matched_name — имя оператора из
        привязки: в ленте подписываем автора им, а не ником ChatApp."""
        with self._get_cursor() as cursor:
            cursor.execute(f"""
                SELECT m.message_id, m.dt, m.side, m.type, m.text, m.file_link,
                       m.file_name, m.file_content_type, m.is_deleted,
                       ({self._CHATAPP_HUMAN_OUT}) AS human_out,
                       COALESCE(map.is_bot, FALSE), u.name, map.employee_name
                  FROM chatapp_messages m
                  LEFT JOIN chatapp_operator_map map ON map.employee_id = m.employee_id
                  LEFT JOIN users u ON u.id = map.user_id
                 WHERE m.license_id = %s AND m.messenger_type = %s AND m.chat_id = %s
                   AND m.dt >= %s AND m.dt <= %s
                   AND m.type <> 'system'
                 ORDER BY m.dt, m.message_id
            """, (int(license_id), str(messenger_type), str(chat_id), ep_start, ep_end))
            return [{
                'message_id': r[0], 'dt': r[1], 'side': r[2], 'type': r[3],
                'text': r[4], 'file_link': r[5], 'file_name': r[6],
                'file_content_type': r[7], 'is_deleted': r[8], 'human_out': r[9],
                'is_bot': r[10], 'matched_name': r[11], 'author_name': r[12],
            } for r in cursor.fetchall()]

    @staticmethod
    def chatapp_channel_key(license_id, messenger_type):
        """Обобщённый ключ канала для c2d_chat_snapshots.wz_channel_id."""
        return f"{int(license_id)}:{messenger_type}"

    def cleanup_chatapp_data(self, messages_days=45, chats_days=180):
        """Ретеншн ChatApp: сообщения 45 дней (как у Wazzup), пул чатов — полгода.
        Эпизоды и снапшоты живут дольше: снапшот — источник оценки."""
        with self._get_cursor() as cursor:
            cursor.execute(
                "DELETE FROM chatapp_messages WHERE dt < now() - make_interval(days => %s)",
                (int(messages_days),))
            deleted_messages = cursor.rowcount
            cursor.execute(
                "DELETE FROM chatapp_chats WHERE last_time < now() - make_interval(days => %s)",
                (int(chats_days),))
            deleted_chats = cursor.rowcount
        if deleted_messages or deleted_chats:
            logging.info("chatapp retention: удалено %s сообщений, %s чатов",
                         deleted_messages, deleted_chats)
        return {'messages': deleted_messages, 'chats': deleted_chats}
