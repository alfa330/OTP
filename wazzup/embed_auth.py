"""Окно «Чатов ОП» в CRM без пароля iCORE (владелец, 20.10.2026: «операторам что нужно будет
заходить в свои аккаунты внутри этого iframe? Это надо бы как то решить»).

Как раньше у Wazzup (POST /v3/iframe): сервер CRM с секретным ключом просит у iCORE одноразовую
ссылку на окно для своего пользователя (POST /api/wazzup/embed/launch), окно меняет её на
обычную сессию iCORE (POST /api/auth/embed-launch). Ключ — тот же, что у пересылки вебхуков в
CRM (POTOK_CRM_WEBHOOK_API_KEY): решение владельца.

Чья учётка за пользователем CRM — решение владельца: пара, уже известная по окну
(crm_identity.py), иначе ФИО — среди всех действующих учёток, «у кого совпадает», админские
роли тоже. Совпало у нескольких или ни у кого — оператор один раз входит в окне сам, окно
запоминает пару, и дальше вход без пароля. QR от СВ окну из CRM не нужен (тоже решение
владельца): сессия, открытая по ссылке, сразу получает доступ к чатам (wazzup_chat_access).

Пишет в окне CRM каждый, кого CRM открыла (владелец, 23.10.2026: «всем кто есть в crm…
не только потоку»): сессия из CRM помечена в wazzup_crm_sessions — по ссылке или входом
паролем в окне с подписью CRM, — и кто сам в разделе не пишет, получает в ней режим «crm»
(access.py): номер CRM, без смены. Поэтому ссылку получает любая действующая учётка,
нашедшаяся по правилам, а не только та, кому раздел открыт в портале.

Сессия окна — только чаты: в её токенах scope crm_embed, и сервер пускает с ней лишь в
/api/wazzup/* и вход-выход (path_allowed). Ключ CRM, попавший не в те руки, откроет чаты под
чужой учёткой, но не весь портал, — среди совпавших по ФИО бывают админы. Куки такой сессии
не ставятся: окно живёт на токенах, а общие куки домена API перебили бы сессию портала в том
же браузере.
"""
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import deque
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request

from . import crm_identity
from .names import normalize_name

log = logging.getLogger(__name__)

SCOPE = 'crm_embed'
KEY_ENV = crm_identity.KEY_ENV
LAUNCH_TTL_SECONDS = 120
ALLOWED_PREFIXES = ('/api/wazzup/',)
ALLOWED_PATHS = frozenset({'/api/auth/me', '/api/auth/refresh', '/api/logout', '/api/login',
                           '/api/auth/embed-launch'})
SCOPE_REFUSAL = 'В окне CRM открыты только «Чаты ОП»'
MAX_CODE = 100
DISMISSED = ('fired', 'dismissal')


def path_allowed(path):
    path = str(path or '')
    return path in ALLOWED_PATHS or any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def scope_refusal(scope, path):
    """Текст отказа или None: сессии окна CRM нельзя туда, где нет чатов."""
    return SCOPE_REFUSAL if scope == SCOPE and not path_allowed(path) else None


def launch_key():
    return crm_identity.crm_key()


def key_matches(authorization, expected):
    """Authorization: Bearer <ключ> — сравнение постоянного времени."""
    if not expected:
        return False
    value = str(authorization or '').strip()
    if value[:7].lower() == 'bearer ':
        value = value[7:].strip()
    return hmac.compare_digest(value.encode('utf-8'), expected.encode('utf-8'))


def new_code():
    return secrets.token_urlsafe(32)


def hash_code(code):
    return hashlib.sha256(str(code).encode('utf-8')).hexdigest()


def _words(name):
    return frozenset(normalize_name(name).split())


def name_matches(crm_name, users):
    """Действующие учётки с тем же ФИО. Порядок слов не важен, казахские буквы и ь/ъ — как в
    подсказке привязки (names.py); отчество может быть только с одной стороны. Меньше двух
    слов — не ФИО, совпадений нет."""
    want = _words(crm_name)
    if len(want) < 2:
        return []
    found = []
    for user in users:
        have = _words(user.get('name'))
        if len(have) >= 2 and (want <= have or have <= want):
            found.append(user)
    return found


ACTIVE_USERS_SQL = ("SELECT id, name, role FROM users "
                    "WHERE COALESCE(LOWER(status), '') NOT IN ('fired', 'dismissal')")


def resolve_user(cursor, crm_user_id, crm_user_name):
    """(id учётки iCORE, как найдена) или (None, причина).

    как: 'binding' — пара уже известна (действующая учётка); 'name' — по ФИО. Причина:
    'NOT_FOUND', 'AMBIGUOUS', 'TAKEN' (учётка с этим ФИО уже закреплена за другим
    пользователем CRM — тёзка или второй аккаунт CRM не получает её без пароля). Доступ к
    разделу в портале не спрашиваем: в окне CRM пишет каждый, кого CRM открыла (access.py)."""
    bound = crm_identity.user_for_crm(cursor, crm_user_id)
    if bound is not None:
        return bound, 'binding'
    cursor.execute(ACTIVE_USERS_SQL)
    users = [{'id': int(row[0]), 'name': row[1], 'role': row[2]} for row in cursor.fetchall()]
    found = name_matches(crm_user_name, users)
    if not found:
        return None, 'NOT_FOUND'
    if len(found) > 1:
        return None, 'AMBIGUOUS'
    user_id = found[0]['id']
    paired = crm_identity.pair_of(cursor, user_id)
    if paired is not None and paired != crm_user_id:
        return None, 'TAKEN'
    return user_id, 'name'


def signed_crm_user(body):
    """Пользователь CRM из входа паролем в окне ({crmUserId, crmUserName, crmExp, crmSig} из
    адреса окна) — только с живой подписью iCORE, иначе None: без неё сессия окна из CRM не
    считается, и режима «crm» в ней нет."""
    identity = crm_identity.clean_identity(body)
    if identity is None:
        return None
    crm_user_id, crm_user_name = identity
    if not crm_identity.identity_signed(launch_key(), crm_user_id, crm_user_name,
                                        body.get('crmExp'), body.get('crmSig')):
        return None
    return crm_user_id


def mark_crm_session(cursor, session_id, user_id, crm_user_id, via):
    """Сессия открыта из CRM: via — 'launch' (ссылка) или 'password' (вход в окне)."""
    cursor.execute("""
        INSERT INTO wazzup_crm_sessions (session_id, user_id, crm_user_id, via)
        VALUES (%s::uuid, %s, %s, %s)
        ON CONFLICT (session_id) DO NOTHING
    """, (str(session_id), int(user_id), str(crm_user_id), via))


def create_launch(cursor, user_id, crm_user_id, ttl=LAUNCH_TTL_SECONDS):
    """Одноразовый код окна: в базе только отпечаток. Попутно — уборка вчерашних кодов и
    пометок сессий старше любой живой сессии (refresh живёт 30 дней)."""
    cursor.execute("""
        DELETE FROM wazzup_embed_launches WHERE code_hash IN (
            SELECT code_hash FROM wazzup_embed_launches
             WHERE expires_at < now() - interval '1 day' LIMIT 500)
    """)
    cursor.execute("""
        DELETE FROM wazzup_crm_sessions WHERE session_id IN (
            SELECT session_id FROM wazzup_crm_sessions
             WHERE created_at < now() - interval '60 days' LIMIT 500)
    """)
    code = new_code()
    cursor.execute("""
        INSERT INTO wazzup_embed_launches (code_hash, user_id, crm_user_id, expires_at)
        VALUES (%s, %s, %s, now() + (%s * interval '1 second'))
    """, (hash_code(code), int(user_id), crm_user_id, int(ttl)))
    return code


def consume_launch(cursor, code):
    """(id учётки, пользователь CRM) или None: код не тот, уже обменян или истёк."""
    cursor.execute("""
        UPDATE wazzup_embed_launches SET used_at = now()
         WHERE code_hash = %s AND used_at IS NULL AND expires_at > now()
        RETURNING user_id, crm_user_id
    """, (hash_code(code),))
    row = cursor.fetchone()
    return (int(row[0]), row[1]) if row else None


def launch_url(base, *, crm_user_id, crm_user_name=None, code=None, user_id=None, key=None, now=None):
    """Адрес окна. Код и id учётки — только у найденной учётки; uid — подсказка окну,
    что сохранённую сессию того же человека можно оставить (embedLaunch.js). Пользователь CRM
    подписан ключом CRM (crm_identity.sign_identity): только по такой подписи окно запомнит
    пару после входа паролем."""
    params = {'view': 'wazzup_chats', 'embed': '1'}
    if code:
        params['launch'] = code
        params['uid'] = str(int(user_id))
    params['crmUserId'] = crm_user_id
    if crm_user_name:
        params['crmUserName'] = crm_user_name
    if key:
        exp = int((time.time() if now is None else now) + crm_identity.IDENTITY_TTL_SECONDS)
        params['crmExp'] = str(exp)
        params['crmSig'] = crm_identity.sign_identity(key, crm_user_id, crm_user_name, exp)
    return f"{str(base).rstrip('/')}/?{urlencode(params)}"


def init_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_embed_launches (
            code_hash TEXT PRIMARY KEY,
            user_id BIGINT NOT NULL,
            crm_user_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            used_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_embed_launches_expires
            ON wazzup_embed_launches(expires_at);
        CREATE TABLE IF NOT EXISTS wazzup_crm_sessions (
            session_id UUID PRIMARY KEY,
            user_id BIGINT NOT NULL,
            crm_user_id TEXT NOT NULL,
            via TEXT NOT NULL CHECK (via IN ('launch', 'password')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_crm_sessions_created
            ON wazzup_crm_sessions(created_at);
    """)


class RateLimiter:
    """Не больше limit событий за window секунд на ключ — в памяти процесса."""

    def __init__(self, limit, window=60, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._events = {}
        self._lock = threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            if len(self._events) > 10000:
                self._events = {k: v for k, v in self._events.items() if v}
            return True


def build_embed_blueprint(*, db, preflight, portal_base_url, load_user, issue_session,
                          client_ip=lambda: request.remote_addr):
    """load_user(user_id) -> строка users (db.get_user).
    issue_session(user, auth_transport, crm_user_id) -> ответ Flask: сессия scope crm_embed,
    помеченная как сессия из CRM, токены, без кук, чаты открыты без QR.
    client_ip() -> адрес человека за прокси Render (bot_schedule2._client_ip)."""
    bp = Blueprint('wazzup_embed', __name__)
    per_crm_user = RateLimiter(30)
    overall = RateLimiter(600)
    per_ip = RateLimiter(60)

    @bp.route('/api/wazzup/embed/launch', methods=['POST', 'OPTIONS'])
    def embed_launch():
        if request.method == 'OPTIONS':
            return preflight()
        expected = launch_key()
        if not expected:
            return jsonify(error='Вход из CRM не настроен', code='EMBED_DISABLED'), 503
        if not key_matches(request.headers.get('Authorization'), expected):
            return jsonify(error='unauthorized'), 401
        identity = crm_identity.clean_identity(request.get_json(silent=True))
        if identity is None:
            return jsonify(error='Нужен crmUserId — id пользователя CRM', code='BAD_IDENTITY'), 400
        crm_user_id, crm_user_name = identity
        if not overall.allow('all') or not per_crm_user.allow(crm_user_id):
            return jsonify(error='Слишком часто', code='RATE_LIMITED'), 429, {'Retry-After': '60'}
        with db._get_cursor() as cursor:
            user_id, how = resolve_user(cursor, crm_user_id, crm_user_name)
            if user_id is None:
                url = launch_url(portal_base_url, crm_user_id=crm_user_id, crm_user_name=crm_user_name,
                                 key=expected)
                log.info('Чаты ОП: вход из CRM без учётки (%s)', how)
                return jsonify(status='success', linked=False, reason=how, url=url)
            if how == 'name':
                crm_identity.bind(cursor, user_id, crm_user_id, crm_user_name)
            code = create_launch(cursor, user_id, crm_user_id)
        user = load_user(user_id)
        log.info('Чаты ОП: вход из CRM — учётка %s (%s)', user_id, how)
        return jsonify(status='success', linked=True, matchedBy=how,
                       url=launch_url(portal_base_url, crm_user_id=crm_user_id, crm_user_name=crm_user_name,
                                      code=code, user_id=user_id, key=expected),
                       expiresInSeconds=LAUNCH_TTL_SECONDS,
                       icoreUser={'id': user_id, 'name': user[2] if user else None})

    @bp.route('/api/auth/embed-launch', methods=['POST', 'OPTIONS'])
    def embed_exchange():
        if request.method == 'OPTIONS':
            return preflight()
        body = request.get_json(silent=True) or {}
        code = body.get('code')
        if not isinstance(code, str) or not code or len(code) > MAX_CODE:
            return jsonify(error='Нет ссылки входа', code='LAUNCH_INVALID'), 400
        if not per_ip.allow(client_ip() or 'unknown'):
            return jsonify(error='Слишком часто', code='RATE_LIMITED'), 429, {'Retry-After': '60'}
        with db._get_cursor() as cursor:
            consumed = consume_launch(cursor, code)
        if consumed is None:
            return jsonify(error='Ссылка входа устарела — откройте окно в CRM заново',
                           code='LAUNCH_INVALID'), 401
        user_id, crm_user_id = consumed
        user = load_user(user_id)
        status = str(user[11] if user and len(user) > 11 else '').strip().lower()
        if not user or status in DISMISSED:
            return jsonify(error='Учётка неактивна', code='USER_INACTIVE'), 403
        return issue_session(user, 'bearer', crm_user_id)

    return bp
