"""SQL рабочего места верификатора в «Чатах ОП» — отдельно от правил и ручек.

Ручки (workspace_routes.py) решают, что делать; здесь — только чтение и запись.
Вынесено ради проверки: порядок шагов подтверждения (скан -> код -> доступ)
тестируется на хранилище в памяти без базы, а сам SQL — на настоящем Postgres
(tests/test_wazzup_workspace_persistence.py). Иначе пришлось бы либо подделывать
SQL строкой, либо оставить подтверждение без тестов там, где базы нет.
"""
from datetime import timedelta

from . import access


class WorkspaceStore:
    def __init__(self, db):
        self.db = db

    # ── кто этот человек ────────────────────────────────────────────────────
    def operator_state(self, user_id, session_id, day, sales_department_id):
        with self.db._get_cursor() as cursor:
            return access.load_operator_state(cursor, user_id, session_id, day,
                                              sales_department_id)

    def session_is_live(self, session_id, user_id):
        session = access.clean_session_id(session_id)
        if not session:
            return False
        with self.db._get_cursor() as cursor:
            cursor.execute("""
                SELECT 1 FROM user_sessions
                 WHERE session_id = %s::uuid AND user_id = %s AND revoked_at IS NULL
                   AND expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
            """, (session, int(user_id)))
            return cursor.fetchone() is not None

    def code_recipient(self, user_id):
        """Глава отдела сотрудника — тот, кому уходит код. None — слать некому.

        Уволенного главу получателем не считаем: назначение могло остаться в
        справочнике, а код ушёл бы человеку, который за отдел уже не отвечает."""
        with self.db._get_cursor() as cursor:
            cursor.execute("""
                SELECT h.id, h.name, h.telegram_id, d.name
                  FROM users u
                  JOIN departments d ON d.id = u.department_id AND COALESCE(d.is_active, TRUE)
                  JOIN users h ON h.id = d.head_user_id
                 WHERE u.id = %s
                   AND LOWER(COALESCE(h.status, '')) NOT IN ('fired', 'dismissal')
            """, (int(user_id),))
            row = cursor.fetchone()
        if not row:
            return None
        return {'id': row[0], 'name': row[1], 'telegram_id': row[2], 'department_name': row[3]}

    # ── скан, ждущий код ────────────────────────────────────────────────────
    _CHALLENGE_COLUMNS = ('id', 'session_id', 'user_id', 'recipient_id', 'code_hash', 'attempts',
                          'sent_count', 'last_sent_at', 'expires_at', 'consumed_at')
    _CHALLENGE_SELECT = """
        SELECT id::text, session_id::text, user_id, recipient_id, code_hash, attempts, sent_count,
               last_sent_at, expires_at, consumed_at
          FROM wazzup_chat_access_challenges
    """

    def _challenge(self, row):
        return dict(zip(self._CHALLENGE_COLUMNS, row)) if row else None

    def active_challenge(self, session_id, approver_id, user_id, moment, max_attempts):
        """Действующее ожидание этого же скана этим же человеком — чтобы повторный
        скан код заново не слал. Заодно чистим старые строки: своей джобы у
        таблицы нет."""
        with self.db._get_cursor() as cursor:
            cursor.execute("DELETE FROM wazzup_chat_access_challenges "
                           "WHERE created_at < now() - interval '2 days'")
            cursor.execute(self._CHALLENGE_SELECT + """
                 WHERE session_id = %s::uuid AND approver_id = %s AND user_id = %s
                   AND consumed_at IS NULL AND expires_at > %s AND attempts < %s
                 ORDER BY created_at DESC LIMIT 1
            """, (str(session_id), int(approver_id), int(user_id), moment, int(max_attempts)))
            return self._challenge(cursor.fetchone())

    def recent_challenges(self, approver_id, user_id):
        with self.db._get_cursor() as cursor:
            cursor.execute("""
                SELECT COUNT(*) FROM wazzup_chat_access_challenges
                 WHERE approver_id = %s AND user_id = %s
                   AND created_at > now() - interval '1 hour'
            """, (int(approver_id), int(user_id)))
            return int(cursor.fetchone()[0])

    def create_challenge(self, challenge_id, session_id, user_id, approver_id, recipient_id,
                         code_hash, expires_at, sent_at, max_scans=6, max_attempts=5):
        with self.db._get_cursor() as cursor:
            # Повторные запросы из двух вкладок не обходят лимит и не шлют
            # два разных кода. Проверка и INSERT фиксируются под одним замком.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           (f'wazzup-access-scan:{int(approver_id)}:{int(user_id)}',))
            cursor.execute(self._CHALLENGE_SELECT + """
                 WHERE session_id = %s::uuid AND approver_id = %s AND user_id = %s
                   AND consumed_at IS NULL AND expires_at > %s AND attempts < %s
                 ORDER BY created_at DESC LIMIT 1
            """, (str(session_id), int(approver_id), int(user_id), sent_at, int(max_attempts)))
            active = self._challenge(cursor.fetchone())
            if active:
                return 'active', active
            cursor.execute("""SELECT COUNT(*) FROM wazzup_chat_access_challenges
                WHERE approver_id = %s AND user_id = %s
                  AND created_at > now() - interval '1 hour'""",
                (int(approver_id), int(user_id)))
            if int(cursor.fetchone()[0]) >= int(max_scans):
                return 'limited', None
            cursor.execute("""
                INSERT INTO wazzup_chat_access_challenges
                    (id, session_id, user_id, approver_id, recipient_id, code_hash,
                     expires_at, last_sent_at)
                VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)
            """, (str(challenge_id), str(session_id), int(user_id), int(approver_id),
                  recipient_id, code_hash, expires_at, sent_at))
            return 'created', None

    def drop_challenge(self, challenge_id):
        with self.db._get_cursor() as cursor:
            cursor.execute("DELETE FROM wazzup_chat_access_challenges WHERE id = %s::uuid",
                           (str(challenge_id),))

    def challenge(self, challenge_id, approver_id):
        with self.db._get_cursor() as cursor:
            cursor.execute(self._CHALLENGE_SELECT + " WHERE id = %s::uuid AND approver_id = %s",
                           (str(challenge_id), int(approver_id)))
            return self._challenge(cursor.fetchone())

    def renew_code(self, challenge_id, approver_id, code_hash, sent_at, expires_at,
                   recipient_id, max_sends, cooldown_seconds=60):
        """Новый код гасит прежний: действует один, тот, что пришёл последним.
        Возвращает, сколько раз код уже отправлен, либо None — ожидание закрыто."""
        with self.db._get_cursor() as cursor:
            cursor.execute("""
                UPDATE wazzup_chat_access_challenges
                   SET code_hash = %s, attempts = 0, sent_count = sent_count + 1,
                       last_sent_at = %s, expires_at = %s, recipient_id = %s
                 WHERE id = %s::uuid AND approver_id = %s AND consumed_at IS NULL
                   AND sent_count < %s
                   AND last_sent_at <= %s
                RETURNING sent_count
            """, (code_hash, sent_at, expires_at, recipient_id, str(challenge_id),
                  int(approver_id), int(max_sends), sent_at - timedelta(seconds=cooldown_seconds)))
            row = cursor.fetchone()
        return int(row[0]) if row else None

    def redeem(self, challenge_id, approver_id, moment, code_matches, max_attempts):
        """Проверить код и открыть доступ — одной транзакцией под замком строки.

        (итог, осталось попыток, ожидание): 'granted' | 'gone' | 'expired' |
        'attempts' | 'wrong'. Счётчик попыток и выдача стоят под одним замком:
        два одновременных ввода не получат по лишней попытке, а доступ не
        откроется дважды разным кодом."""
        with self.db._get_cursor() as cursor:
            cursor.execute(self._CHALLENGE_SELECT
                           + " WHERE id = %s::uuid AND approver_id = %s FOR UPDATE",
                           (str(challenge_id), int(approver_id)))
            challenge = self._challenge(cursor.fetchone())
            if not challenge or challenge['consumed_at'] is not None:
                return 'gone', 0, challenge
            left = max(0, int(max_attempts) - int(challenge['attempts']))
            if challenge['expires_at'] <= moment:
                return 'expired', left, challenge
            if left <= 0:
                return 'attempts', 0, challenge
            if not code_matches(challenge):
                cursor.execute("UPDATE wazzup_chat_access_challenges SET attempts = attempts + 1 "
                               "WHERE id = %s::uuid", (str(challenge_id),))
                return 'wrong', left - 1, challenge
            cursor.execute("""
                INSERT INTO wazzup_chat_access
                    (session_id, user_id, granted_by, code_recipient_id, granted_at, revoked_at)
                VALUES (%s::uuid, %s, %s, %s, now(), NULL)
                ON CONFLICT (session_id) DO UPDATE
                   SET user_id = EXCLUDED.user_id, granted_by = EXCLUDED.granted_by,
                       code_recipient_id = EXCLUDED.code_recipient_id,
                       granted_at = now(), revoked_at = NULL
            """, (challenge['session_id'], challenge['user_id'], int(approver_id),
                  challenge['recipient_id']))
            cursor.execute("UPDATE wazzup_chat_access_challenges SET consumed_at = now() "
                           "WHERE id = %s::uuid", (str(challenge_id),))
            return 'granted', left, challenge

    # ── смена: статус и отметки портала ─────────────────────────────────────
    def latest_status(self, user_id, moment):
        """Последнее событие статуса человека — любого источника: у верификатора с
        телефоном статус один на человека, а не на программу.

        Без нижней границы по времени НАМЕРЕННО. Проверка «тот же статус — не
        писать» в append_operator_status_event смотрит на последнее событие
        вообще, и раздел обязан видеть то же самое: окно в 48 часов показывало
        «Не на смене» при открытой в базе смене, а «Начать смену» тогда молча
        ничего не записывало — выхода из этого состояния не было. Индекс
        (operator_id, event_at) делает запрос точечным и без окна."""
        with self.db._get_cursor() as cursor:
            cursor.execute("""
                SELECT status_key, event_at, client_event_id, state_note
                  FROM operator_status_events
                 WHERE operator_id = %s
                   AND event_at <= %s
                   AND COALESCE(event_kind, 'status') <> 'action'
                 ORDER BY event_at DESC, id DESC
                 LIMIT 1
            """, (int(user_id), moment + timedelta(minutes=1)))
            row = cursor.fetchone()
        if not row:
            return None
        return {'status_key': row[0], 'event_at': row[1], 'client_event_id': row[2],
                'state_note': row[3]}

    def last_seen(self, user_id):
        with self.db._get_cursor() as cursor:
            cursor.execute("SELECT last_seen_at FROM wazzup_workspace_presence WHERE user_id = %s",
                           (int(user_id),))
            row = cursor.fetchone()
        return row[0] if row else None

    def touch(self, user_id, seen_at):
        # GREATEST: две вкладки одного человека не откатывают отметку друг другу.
        with self.db._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO wazzup_workspace_presence (user_id, last_seen_at, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (user_id) DO UPDATE
                   SET last_seen_at = GREATEST(wazzup_workspace_presence.last_seen_at,
                                               EXCLUDED.last_seen_at),
                       updated_at = now()
            """, (int(user_id), seen_at))

    def stale_presences(self, cutoff, floor):
        """Отметки старше cutoff вместе с последним статусом человека.

        Последнее событие — LATERAL по индексу (operator_id, event_at); строк в
        отметках столько, сколько верификаторов, так что проход сторожа — это
        полтора десятка точечных чтений."""
        with self.db._get_cursor() as cursor:
            cursor.execute("""
                SELECT p.user_id, p.last_seen_at, e.status_key, e.event_at, e.client_event_id
                  FROM wazzup_workspace_presence p
                  CROSS JOIN LATERAL (
                        SELECT status_key, event_at, client_event_id
                          FROM operator_status_events
                         WHERE operator_id = p.user_id
                           AND COALESCE(event_kind, 'status') <> 'action'
                         ORDER BY event_at DESC, id DESC
                         LIMIT 1
                  ) e
                 WHERE p.last_seen_at < %s
                   AND p.last_seen_at >= %s
            """, (cutoff, floor))
            rows = cursor.fetchall()
        return [{'user_id': row[0], 'last_seen_at': row[1],
                 'latest': {'status_key': row[2], 'event_at': row[3], 'client_event_id': row[4]}}
                for row in rows]

    def append_status(self, user_id, event_at, status_key, state_note, client_event_id):
        return self.db.append_operator_status_event(
            operator_id=user_id, event_at=event_at, status_key=status_key,
            state_note=state_note, event_kind='status', client_event_id=client_event_id)
