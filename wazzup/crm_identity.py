"""Кто из пользователей CRM «Потока» — какая учётка iCORE (владелец, 20.10.2026: «поскольку
поток будет писать через iframe, нам нужно как-то заматчить учётки, чтобы видеть авторов
сообщений»).

Раньше «Поток» писал из окна Wazzup, открытого в CRM под пользователем CRM, и эхо каждого
исходящего приходило в CRM с authorId/authorName этого пользователя. Теперь пишут из окна
«Чатов ОП» под своей учёткой iCORE, отправка идёт ключом аккаунта «op», и Wazzup подписывает
эхо как «Admin» без id — CRM автора не видит. В iCORE автор известен (icore:<id>), не хватает
пары «учётка iCORE ↔ пользователь CRM».

Пару сообщает сама CRM: страница, в которую встроено окно, знает своего пользователя и
передаёт его окну (команда icore.chats.identify или crmUserId в адресе окна, embedBridge.js),
окно — сюда (POST /api/wazzup/pilot/crm-identity) от имени вошедшего человека. Пересылка в
CRM (syntony.py, маршрут potok_crm) подставляет в эхо наших отправок authorId/authorName
пользователя CRM — так, как это делал Wazzup, — и icoreAuthor с учёткой iCORE. Без пары
authorName — имя сотрудника в iCORE.
"""
import json
import re
from datetime import datetime, timezone

from flask import jsonify, request

MAX_CRM_ID = 64
MAX_NAME = 100
_CRM_ID = re.compile(r'[A-Za-z0-9_.@:-]{1,%d}' % MAX_CRM_ID)
# Эхо нашей отправки приходит раньше ответа Wazzup на саму отправку (так бывает): автора
# ещё нет в outbox. Столько секунд пересылка ждёт его, прежде чем отправить эхо как есть.
AUTHOR_WAIT_SECONDS = 20


def init_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_crm_identities (
            user_id BIGINT PRIMARY KEY,
            crm_user_id TEXT NOT NULL,
            crm_user_name TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)


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


def register_crm_identity_route(bp, actor, require_api_key, preflight, db):
    @bp.route('/crm-identity', methods=['POST', 'OPTIONS'])
    @require_api_key
    def crm_identity():
        if request.method == 'OPTIONS':
            return preflight()
        user, error = actor()
        if error:
            return error
        identity = clean_identity(request.get_json(silent=True))
        if identity is None:
            return jsonify(error='Некорректный пользователь CRM'), 400
        crm_user_id, crm_user_name = identity
        with db._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO wazzup_crm_identities (user_id, crm_user_id, crm_user_name, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (user_id) DO UPDATE SET crm_user_id = EXCLUDED.crm_user_id,
                    crm_user_name = EXCLUDED.crm_user_name, updated_at = now()
                WHERE (wazzup_crm_identities.crm_user_id, wazzup_crm_identities.crm_user_name)
                      IS DISTINCT FROM (EXCLUDED.crm_user_id, EXCLUDED.crm_user_name)
            """, (int(user[0]), crm_user_id, crm_user_name))
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
