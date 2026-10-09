"""Хранилище рабочего места «Чатов ОП» в памяти — для тестов ручек без базы.

Повторяет интерфейс wazzup.workspace_store.WorkspaceStore. Сам SQL проверяется
отдельно, на настоящем Postgres (test_wazzup_workspace_persistence.py); здесь
нужен только порядок шагов подтверждения и смены.
"""
from datetime import timedelta


class MemoryWorkspaceStore:
    def __init__(self):
        self.verifiers = set()            # id сотрудников-верификаторов
        self.live_sessions = set()        # (session_id, user_id)
        self.granted = {}                 # session_id -> {'user_id', 'granted_by', 'code_recipient_id'}
        self.portal_unlocked = set()      # (session_id, user_id) — подтверждено обычным QR портала
        self.recipients = {}              # user_id -> {'id', 'name', 'telegram_id', 'department_name'}
        self.challenges = {}              # id -> строка ожидания кода
        self.created = []                 # (approver_id, user_id, момент) — для потолка в час
        self.events = []                  # статусы: {'operator_id', 'event_at', 'status_key', ...}
        self.presence = {}                # user_id -> last_seen_at
        self.touch_fails = False
        self.last_seen_fails = False

    # ── кто этот человек ────────────────────────────────────────────────────
    def operator_state(self, user_id, session_id, day, sales_department_id):
        verifier = user_id in self.verifiers
        grant = self.granted.get(str(session_id))
        unlocked = bool(verifier and (str(session_id), user_id) in self.live_sessions
                        and ((grant and grant['user_id'] == user_id)
                             or (str(session_id), user_id) in self.portal_unlocked))
        return {'verifier': verifier, 'unlocked': unlocked}

    def session_is_live(self, session_id, user_id):
        return (str(session_id), int(user_id)) in self.live_sessions

    def code_recipient(self, user_id):
        return self.recipients.get(user_id)

    def grant_without_code(self, session_id, user_id, approver_id):
        if not self.session_is_live(session_id, user_id):
            return False
        self.granted[str(session_id)] = {'user_id': user_id, 'granted_by': approver_id,
                                         'code_recipient_id': None}
        return True

    # ── скан, ждущий код ────────────────────────────────────────────────────
    def active_challenge(self, session_id, approver_id, user_id, moment, max_attempts):
        rows = [row for row in self.challenges.values()
                if row['session_id'] == str(session_id) and row['approver_id'] == approver_id
                and row['user_id'] == user_id and row['consumed_at'] is None
                and row['expires_at'] > moment and row['attempts'] < max_attempts]
        return dict(rows[-1]) if rows else None

    def recent_challenges(self, approver_id, user_id):
        return sum(1 for a, u, _ in self.created if (a, u) == (approver_id, user_id))

    def create_challenge(self, challenge_id, session_id, user_id, approver_id, recipient_id,
                         code_hash, expires_at, sent_at, max_scans=6, max_attempts=5):
        active = self.active_challenge(session_id, approver_id, user_id, sent_at, max_attempts)
        if active:
            return 'active', active
        if self.recent_challenges(approver_id, user_id) >= max_scans:
            return 'limited', None
        self.challenges[str(challenge_id)] = {
            'id': str(challenge_id), 'session_id': str(session_id), 'user_id': user_id,
            'approver_id': approver_id, 'recipient_id': recipient_id, 'code_hash': code_hash,
            'attempts': 0, 'sent_count': 1, 'last_sent_at': sent_at, 'expires_at': expires_at,
            'consumed_at': None}
        self.created.append((approver_id, user_id, sent_at))
        return 'created', None

    def drop_challenge(self, challenge_id):
        self.challenges.pop(str(challenge_id), None)

    def challenge(self, challenge_id, approver_id):
        row = self.challenges.get(str(challenge_id))
        return dict(row) if row and row['approver_id'] == approver_id else None

    def renew_code(self, challenge_id, approver_id, code_hash, sent_at, expires_at,
                   recipient_id, max_sends, cooldown_seconds=60):
        row = self.challenges.get(str(challenge_id))
        if (not row or row['approver_id'] != approver_id or row['consumed_at'] is not None
                or row['sent_count'] >= max_sends
                or row['last_sent_at'] > sent_at - timedelta(seconds=cooldown_seconds)):
            return None
        row.update(code_hash=code_hash, attempts=0, sent_count=row['sent_count'] + 1,
                   last_sent_at=sent_at, expires_at=expires_at, recipient_id=recipient_id)
        return row['sent_count']

    def redeem(self, challenge_id, approver_id, moment, code_matches, max_attempts):
        row = self.challenges.get(str(challenge_id))
        if not row or row['approver_id'] != approver_id or row['consumed_at'] is not None:
            return 'gone', 0, dict(row) if row else None
        left = max(0, max_attempts - row['attempts'])
        if row['expires_at'] <= moment:
            return 'expired', left, dict(row)
        if left <= 0:
            return 'attempts', 0, dict(row)
        if not code_matches(row):
            row['attempts'] += 1
            return 'wrong', left - 1, dict(row)
        self.granted[row['session_id']] = {'user_id': row['user_id'], 'granted_by': approver_id,
                                           'code_recipient_id': row['recipient_id']}
        row['consumed_at'] = moment
        return 'granted', left, dict(row)

    # ── смена ───────────────────────────────────────────────────────────────
    def latest_status(self, user_id, moment):
        rows = [e for e in self.events
                if e['operator_id'] == user_id and e['event_at'] <= moment + timedelta(minutes=1)]
        if not rows:
            return None
        last = max(enumerate(rows), key=lambda pair: (pair[1]['event_at'], pair[0]))[1]
        return {'status_key': last['status_key'], 'event_at': last['event_at'],
                'client_event_id': last['client_event_id'], 'state_note': last['state_note']}

    def last_seen(self, user_id):
        if self.last_seen_fails:
            raise RuntimeError('relation "wazzup_workspace_presence" does not exist')
        return self.presence.get(user_id)

    def touch(self, user_id, seen_at):
        if self.touch_fails:
            raise RuntimeError('relation "wazzup_workspace_presence" does not exist')
        self.presence[user_id] = max(self.presence.get(user_id, seen_at), seen_at)

    def stale_presences(self, cutoff, floor):
        rows = []
        for user_id, seen in self.presence.items():
            latest = self.latest_status(user_id, seen + timedelta(days=3650))
            if latest and floor <= seen < cutoff:
                rows.append({'user_id': user_id, 'last_seen_at': seen, 'latest': latest})
        return rows

    def append_status(self, user_id, event_at, status_key, state_note, client_event_id):
        if any(e['operator_id'] == user_id and e['client_event_id'] == client_event_id
               for e in self.events):
            return {'duplicate': True}
        previous = self.latest_status(user_id, event_at)
        if previous and (previous['status_key'], previous['state_note']) == (status_key, state_note):
            # Как у настоящего append_operator_status_event: тот же статус не пишется.
            return {'duplicate': False, 'noop': True}
        self.events.append({'operator_id': user_id, 'event_at': event_at, 'status_key': status_key,
                            'state_note': state_note, 'client_event_id': client_event_id})
        return {'duplicate': False, 'event_id': len(self.events)}
