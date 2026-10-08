"""Раздел «Чаты ОП» в режиме обработки: кому он открыт и чем подтверждается.

Постановка владельца 08.10.2026. Писать клиентам из iCORE могут двое:

* верификатор — рядовой оператор отдела продаж, чья группа на сегодня считается
  по модели «Верификатор». Супервайзер сканирует его QR и вводит временный код,
  который уходит в Telegram главе отдела. Админ, супер-админ и глава отдела
  открывают доступ сканированием без Telegram-кода.
  Доступ живёт, пока жива сессия портала на этом
  устройстве, — как у остальных разделов за QR;
* супер-админ — без подтверждения.

Остальная аудитория раздела (админы, главы ОП, СЗоВ и маркетинга, СВ продаж)
осталась на просмотре переписки — её считает `_verifier_chats_guard` в
bot_schedule2.py, сюда она не входит.

Ключ ОТДЕЛЬНЫЙ от общего QR-доступа («Вики», «Обращения», оценки). Тот открывает
один скан без кода; впусти он ещё и в чаты, требование о коде главы отдела
обходилось бы подтверждением любого другого раздела. Поэтому и у кода свой
знак (`OTPW:`), и подпись у него своя: код одного вида второй ручкой не
принимается.

Модуль чистый: ни Flask, ни подключения к базе. SQL получает курсор вызывающего.
"""
import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

SALES_DEPARTMENT_CODE = 'op'
VERIFIER_MODEL = 'op_verificator'
# Стажёра здесь нет намеренно: QR ему портал не выдаёт нигде, кроме «Чатов
# водителей», и переписку с клиентами от имени компании он не ведёт.
OPERATOR_ROLES = frozenset({'operator'})
DISMISSED_STATUSES = frozenset({'fired', 'dismissal'})

MODE_FULL = 'full'
MODE_OPERATOR = 'operator'

QR_PREFIX = 'OTPW:'
QR_TTL_SECONDS = 300
_QR_DOMAIN = b'wazzup-chat-access\x00'
_QR_BODY_BYTES = 24
_QR_SIGNATURE_BYTES = 8

CODE_DIGITS = 6
# Десять минут, а не пять: код читает один человек (глава отдела), а вводит
# другой (супервайзер у стола оператора) — между ними звонок или сообщение.
CODE_TTL_SECONDS = 600
CODE_MAX_ATTEMPTS = 5
CODE_RESEND_SECONDS = 60
# Сколько раз на один скан можно выслать код. Потолок не даёт завалить главу
# отдела сообщениями одной кнопкой «Отправить ещё раз».
CODE_MAX_SENDS = 3

# Автор сообщений, отправленных из iCORE, в `wazzup_messages.author_id`. Ключ
# Wazzup у аккаунта «op» не подписывает отправителя: эхо приходит от «Admin» без
# id автора, и показатели (аналитика раздела, «Табло ОП · Чат», «Воронка ОП»,
# ИИ-оценка) такой ответ никому не засчитывают. Свой id автора с готовой
# привязкой к сотруднику возвращает ответ его хозяину.
ICORE_AUTHOR_PREFIX = 'icore:'


def normalize(value):
    return str(value or '').strip().lower()


def is_dismissed(status):
    return normalize(status) in DISMISSED_STATUSES


def is_verifier(role, department_code, day_model, status=None):
    """Рядовой верификатор отдела продаж — тот, кому раздел открывается за кодом.

    day_model — модель расчёта ДНЯ: группы, в которой человек состоит сегодня, а
    без членства — его направления (та же лестница, что у учёта часов и у
    «Табло ОП · Чат»). По направлению судить нельзя: часть верификаторов
    числится в «Основе ОП»."""
    return (normalize(role) in OPERATOR_ROLES
            and normalize(department_code) == SALES_DEPARTMENT_CODE
            and normalize(day_model) == VERIFIER_MODEL
            and not is_dismissed(status))


def processes_without_gate(role, status=None):
    """Кому обработка открыта без скана и кода. Пока — только супер-админам."""
    return normalize(role) == 'super_admin' and not is_dismissed(status)


def approves_without_code(role, headed_department_ids, operator_department_id):
    """Кто открывает доступ без Telegram-кода после проверки периметра подтверждения."""
    return (normalize(role) in {'admin', 'super_admin'}
            or (operator_department_id is not None
                and operator_department_id in (headed_department_ids or ())))


def clean_session_id(value):
    """id сессии строкой uuid или None.

    Сессия приходит из access-токена, и «нет сессии» выглядит по-разному: None,
    пустая строка, а у токена без sid — строка 'None'. В запрос она идёт с
    приведением к uuid, и любое из этих значений уронило бы его вместо того,
    чтобы просто оставить доступ закрытым."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def icore_author_id(user_id):
    return f'{ICORE_AUTHOR_PREFIX}{int(user_id)}'


def is_icore_author(author_id):
    return str(author_id or '').startswith(ICORE_AUTHOR_PREFIX)


# ── Состояние человека одним запросом ────────────────────────────────────────

_OPERATOR_STATE_SQL = """
    SELECT LOWER(COALESCE(u.role, '')),
           LOWER(COALESCE(u.status, '')),
           CASE WHEN u.department_id = %(sales_department_id)s THEN %(sales_code)s
                ELSE LOWER(COALESCE(dep.code, '')) END,
           LOWER(COALESCE(gm.calculation_model_code, dir.calculation_model_code, '')),
           EXISTS (
               SELECT 1
                 FROM wazzup_chat_access a
                 JOIN user_sessions s ON s.session_id = a.session_id
                WHERE a.session_id = %(session_id)s::uuid
                  AND a.user_id = u.id
                  AND a.revoked_at IS NULL
                  AND s.revoked_at IS NULL
                  AND s.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
           )
      FROM users u
      LEFT JOIN departments dep ON dep.id = u.department_id
      LEFT JOIN directions dir ON dir.id = u.direction_id
      LEFT JOIN LATERAL (
            SELECT gr.calculation_model_code
              FROM group_operator_memberships gom
              JOIN groups gr ON gr.id = gom.group_id
             WHERE gom.operator_id = u.id
               AND gom.start_date <= %(day)s::date
               AND (gom.end_date IS NULL OR gom.end_date >= %(day)s::date)
             ORDER BY gom.start_date DESC, gom.id DESC
             LIMIT 1
      ) gm ON TRUE
     WHERE u.id = %(user_id)s
"""


def load_operator_state(cursor, user_id, session_id, day, sales_department_id):
    """{'verifier': bool, 'unlocked': bool} — один запрос на всё.

    Отдел продаж узнаём и по id: код в справочнике заполнен не везде, а по
    одному полю человек молча терялся бы (то же правило в `_department_code_of_user`).
    day — дата Алматы из Python, а не CURRENT_DATE: часы базы в UTC, и до 05:00
    она жила бы вчерашним составом группы."""
    session = clean_session_id(session_id)
    cursor.execute(_OPERATOR_STATE_SQL, {
        'user_id': int(user_id),
        # Сессии нет — подставляем заведомо чужой id: запрос один на оба вопроса,
        # и без сессии он обязан ответить «верификатор, но закрыто».
        'session_id': session or '00000000-0000-0000-0000-000000000000',
        'day': day,
        'sales_department_id': int(sales_department_id),
        'sales_code': SALES_DEPARTMENT_CODE,
    })
    row = cursor.fetchone()
    if not row:
        return {'verifier': False, 'unlocked': False}
    verifier = is_verifier(row[0], row[2], row[3], row[1])
    return {'verifier': verifier, 'unlocked': bool(verifier and session and row[4])}


# ── Код для сканера ──────────────────────────────────────────────────────────

class QrError(ValueError):
    """Код не принят. reason — 'format' | 'signature' | 'expired'."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _qr_signature(secret, body):
    return hmac.new(str(secret).encode('utf-8'), _QR_DOMAIN + body,
                    hashlib.sha256).digest()[:_QR_SIGNATURE_BYTES]


def build_qr_token(secret, session_id, user_id, now=None, ttl_seconds=QR_TTL_SECONDS):
    """(токен, срок) — той же длины, что код общего доступа: сессия, человек, срок
    и усечённая подпись в base32. Подпись считается со своей меткой, поэтому
    код одного вида не проходит проверку другого."""
    now = now or datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=int(ttl_seconds))
    body = (uuid.UUID(str(session_id)).bytes
            + int(user_id).to_bytes(4, 'big')
            + int(expires_at.timestamp()).to_bytes(4, 'big'))
    token = base64.b32encode(body + _qr_signature(secret, body)).decode('ascii').rstrip('=')
    return token, expires_at


def is_chat_qr(raw):
    return str(raw or '').strip().upper().startswith(QR_PREFIX)


def strip_qr_prefix(raw):
    token = str(raw or '').strip()
    return token[len(QR_PREFIX):].strip() if is_chat_qr(token) else token


def decode_qr_token(secret, raw, now=None):
    token = strip_qr_prefix(raw)
    if not token:
        raise QrError('format')
    try:
        data = base64.b32decode(token + '=' * (-len(token) % 8), casefold=True)
    except Exception as exc:
        raise QrError('format') from exc
    if len(data) != _QR_BODY_BYTES + _QR_SIGNATURE_BYTES:
        raise QrError('format')
    body, signature = data[:_QR_BODY_BYTES], data[_QR_BODY_BYTES:]
    if not hmac.compare_digest(signature, _qr_signature(secret, body)):
        raise QrError('signature')
    expires_ts = int.from_bytes(body[20:24], 'big')
    now = now or datetime.now(timezone.utc)
    if expires_ts <= int(now.timestamp()):
        raise QrError('expired')
    return {
        'session_id': str(uuid.UUID(bytes=body[:16])),
        'user_id': int.from_bytes(body[16:20], 'big'),
        'expires_at': datetime.fromtimestamp(expires_ts, timezone.utc),
    }


# ── Временный код из Telegram ────────────────────────────────────────────────

def new_code():
    return str(secrets.randbelow(10 ** CODE_DIGITS)).zfill(CODE_DIGITS)


def normalize_code(raw):
    """Шесть цифр или None. Пробелы и дефисы снимаем: код читают вслух и
    диктуют парами, а из Telegram его вставляют как есть."""
    digits = ''.join(ch for ch in str(raw or '') if ch.isdigit())
    return digits if len(digits) == CODE_DIGITS and len(str(raw or '').strip()) <= 16 else None


def hash_code(secret, challenge_id, code):
    """В базе лежит не код, а его отпечаток, привязанный к своему скану: строка
    таблицы, попавшая в чужие руки, кода не выдаёт и к другому скану не подходит."""
    return hmac.new(str(secret).encode('utf-8'),
                    f'{challenge_id}:{code}'.encode('utf-8'), hashlib.sha256).hexdigest()


def code_matches(secret, challenge_id, code, code_hash):
    return hmac.compare_digest(hash_code(secret, challenge_id, code), str(code_hash or ''))
