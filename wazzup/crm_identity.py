"""Кто из пользователей CRM «Потока» — какая учётка iCORE (владелец, 20.10.2026: «поскольку
поток будет писать через iframe, нам нужно как-то заматчить учётки, чтобы видеть авторов
сообщений»).

Раньше «Поток» писал из окна Wazzup, открытого в CRM под пользователем CRM, и эхо каждого
исходящего приходило в CRM с authorId/authorName этого пользователя. Теперь пишут из окна
«Чатов ОП» под своей учёткой iCORE, отправка идёт ключом аккаунта «op», и Wazzup подписывает
эхо как «Admin» без id — CRM автора не видит. В iCORE автор известен (icore:<id>), не хватает
пары «учётка iCORE ↔ пользователь CRM».

Пару сообщает сама CRM: её сервер своим ключом получает у iCORE адрес окна
(embed_auth.py), и в адресе — пользователь CRM с подписью iCORE (crmExp, crmSig). Окно
передаёт пару сюда (POST /api/wazzup/pilot/crm-identity) от имени вошедшего человека, и
сервер верит ей только с живой подписью: иначе любой с учёткой iCORE, встроив окно в свою
страницу, привязал бы к себе чужого пользователя CRM.

Подпись говорит лишь, что окно открыто для этого пользователя CRM, но не кто в нём вошёл.
Поэтому окно занимает только свободную пару: пользователь CRM и учётка ещё ни за кем не
закреплены (или уже друг за другом), и учётка — оператора, чей вход подтвердил СВ своим QR.
Иначе на общем компьютере коллега или админ, вошедший паролем в окне чужого пользователя
CRM, открыл бы ему свою учётку без пароля. Снять неверную пару — дело администратора.
Пересылка в CRM (syntony.py, маршрут
potok_crm) подставляет в эхо наших отправок authorId/authorName пользователя CRM — так, как
это делал Wazzup, — и icoreAuthor с учёткой iCORE. Без пары authorName — имя сотрудника в iCORE.
"""
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone

from flask import jsonify, request

MAX_CRM_ID = 64
MAX_NAME = 100
_CRM_ID = re.compile(r'[A-Za-z0-9_.@:-]{1,%d}' % MAX_CRM_ID)
# Эхо нашей отправки приходит раньше ответа Wazzup на саму отправку (так бывает): автора
# ещё нет в outbox. Столько секунд пересылка ждёт его, прежде чем отправить эхо как есть.
AUTHOR_WAIT_SECONDS = 20
# Ключ CRM — тот же, что у пересылки вебхуков (решение владельца 20.10.2026).
KEY_ENV = 'POTOK_CRM_WEBHOOK_API_KEY'
# Сколько живёт подпись пользователя CRM в адресе окна: оператор входит не сразу.
IDENTITY_TTL_SECONDS = 3600
_SIG = re.compile(r'[0-9a-f]{64}')


def crm_key():
    return (os.getenv(KEY_ENV) or '').strip()


def sign_identity(key, crm_user_id, crm_user_name, exp):
    """Подпись iCORE под пользователем CRM в адресе окна (HMAC ключом CRM)."""
    message = f'{crm_user_id}\n{crm_user_name or ""}\n{int(exp)}'.encode('utf-8')
    return hmac.new(key.encode('utf-8'), b'icore-crm-identity\x00' + message, hashlib.sha256).hexdigest()


def identity_signed(key, crm_user_id, crm_user_name, exp, sig, now=None):
    """Подпись своя и не истекла. Без ключа пар нет вовсе."""
    if not key or not isinstance(sig, str) or not _SIG.fullmatch(sig.strip().lower()):
        return False
    try:
        exp = int(exp)
    except (TypeError, ValueError):
        return False
    now = time.time() if now is None else now
    if exp < now or exp > now + IDENTITY_TTL_SECONDS + 60:
        return False
    return hmac.compare_digest(sign_identity(key, crm_user_id, crm_user_name, exp), sig.strip().lower())


def init_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_crm_identities (
            user_id BIGINT PRIMARY KEY,
            crm_user_id TEXT NOT NULL,
            crm_user_name TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_crm_identities_crm
            ON wazzup_crm_identities(crm_user_id);
    """)


def bind(cursor, user_id, crm_user_id, crm_user_name):
    """Пара «учётка iCORE ↔ пользователь CRM», один к одному: пользователь CRM, которого
    назвала другая учётка, у прежней снимается — иначе вход из CRM не знал бы, кого открыть."""
    cursor.execute("DELETE FROM wazzup_crm_identities WHERE crm_user_id = %s AND user_id <> %s",
                   (crm_user_id, int(user_id)))
    cursor.execute("""
        INSERT INTO wazzup_crm_identities (user_id, crm_user_id, crm_user_name, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (user_id) DO UPDATE SET crm_user_id = EXCLUDED.crm_user_id,
            crm_user_name = EXCLUDED.crm_user_name, updated_at = now()
        WHERE (wazzup_crm_identities.crm_user_id, wazzup_crm_identities.crm_user_name)
              IS DISTINCT FROM (EXCLUDED.crm_user_id, EXCLUDED.crm_user_name)
    """, (int(user_id), crm_user_id, crm_user_name))


def user_for_crm(cursor, crm_user_id):
    """Действующая учётка iCORE, известная за пользователем CRM, или None."""
    cursor.execute("""
        SELECT i.user_id FROM wazzup_crm_identities i
          JOIN users u ON u.id = i.user_id
         WHERE i.crm_user_id = %s AND COALESCE(LOWER(u.status), '') NOT IN ('fired', 'dismissal')
         ORDER BY i.updated_at DESC LIMIT 1
    """, (crm_user_id,))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def pair_of(cursor, user_id):
    """Пользователь CRM, за которым закреплена учётка, или None."""
    cursor.execute("SELECT crm_user_id FROM wazzup_crm_identities WHERE user_id = %s", (int(user_id),))
    row = cursor.fetchone()
    return row[0] if row else None


def clean_identity(body):
    """(crm_user_id, crm_user_name) или None — пара из запроса окна."""
    if not isinstance(body, dict):
        return None
    raw_id = body.get('crmUserId')
    if isinstance(raw_id, int) and not isinstance(raw_id, bool):
        raw_id = str(raw_id)
    if not isinstance(raw_id, str) or not _CRM_ID.fullmatch(raw_id.strip()):
        return None
    name = body.get('crmUserName')
    name = ' '.join(name.split())[:MAX_NAME] if isinstance(name, str) else ''
    return raw_id.strip(), name or None


def register_crm_identity_route(bp, actor, require_api_key, preflight, db, may_bind=lambda: True):
    """may_bind() — может ли человек запроса занять свободную пару (pilot.py: оператор за QR)."""
    @bp.route('/crm-identity', methods=['POST', 'OPTIONS'])
    @require_api_key
    def crm_identity():
        if request.method == 'OPTIONS':
            return preflight()
        user, error = actor()
        if error:
            return error
        body = request.get_json(silent=True)
        identity = clean_identity(body)
        if identity is None:
            return jsonify(error='Некорректный пользователь CRM'), 400
        crm_user_id, crm_user_name = identity
        if not identity_signed(crm_key(), crm_user_id, crm_user_name, body.get('crmExp'), body.get('crmSig')):
            return jsonify(error='Пользователь CRM без подписи iCORE — откройте окно по ссылке из CRM',
                           code='IDENTITY_UNSIGNED'), 403
        with db._get_cursor() as cursor:
            owner = user_for_crm(cursor, crm_user_id)
            if owner != int(user[0]):
                mine = pair_of(cursor, user[0])
                if owner is not None or (mine is not None and mine != crm_user_id):
                    return jsonify(error='Пользователь CRM или эта учётка уже закреплены за другими — '
                                         'снять пару может администратор iCORE', code='PAIR_TAKEN'), 409
                if not may_bind():
                    return jsonify(error='Без пароля из CRM эта учётка входит только по ФИО',
                                   code='PAIR_NOT_ALLOWED'), 403
            bind(cursor, user[0], crm_user_id, crm_user_name)
        return jsonify(status='success', crmUserId=crm_user_id)


def _ours(message):
    """Эхо отправки ключом аккаунта: исходящее без id автора. Отправленное из самого
    Wazzup приходит с authorId сотрудника Wazzup — его не трогаем."""
    return (isinstance(message, dict) and message.get('isEcho') is True
            and not message.get('authorId') and isinstance(message.get('messageId'), str))


def _age_seconds(message, now):
    try:
        moment = datetime.fromisoformat(str(message.get('dateTime')).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (now - moment).total_seconds()


def attach_authors(db, body, now=None):
    """(тело для CRM, готово ли). Подставляет автора в эхо наших отправок.

    Готово = у каждого нашего эха автор найден или ждать его дальше незачем (эхо старше
    AUTHOR_WAIT_SECONDS: отправка не из iCORE или ответ Wazzup на неё так и не пришёл).
    Тело без наших эх возвращается теми же байтами."""
    try:
        payload = json.loads(bytes(body))
    except (TypeError, ValueError, UnicodeError):
        return body, True
    messages = payload.get('messages') if isinstance(payload, dict) else None
    ours = [m for m in messages if _ours(m)] if isinstance(messages, list) else []
    if not ours:
        return body, True
    with db._get_cursor() as cursor:
        cursor.execute("""
            SELECT o.message_id, o.user_id, COALESCE(u.name, o.author_name), i.crm_user_id, i.crm_user_name
              FROM wazzup_pilot_outbox o
              LEFT JOIN users u ON u.id = o.user_id
              LEFT JOIN wazzup_crm_identities i ON i.user_id = o.user_id
             WHERE o.account = 'op' AND o.message_id = ANY(%s)
        """, ([m['messageId'] for m in ours],))
        authors = {row[0]: row[1:] for row in cursor.fetchall()}
    now = now or datetime.now(timezone.utc)
    ready = True
    for message in ours:
        author = authors.get(message['messageId'])
        if author is None:
            age = _age_seconds(message, now)
            if age is not None and age < AUTHOR_WAIT_SECONDS:
                ready = False
            continue
        user_id, name, crm_user_id, crm_user_name = author
        message['icoreAuthor'] = {'userId': int(user_id), 'name': name}
        if crm_user_id:
            message['authorId'] = crm_user_id
        message['authorName'] = crm_user_name or name
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), ready
