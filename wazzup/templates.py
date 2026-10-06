"""Shared iCORE quick replies and the documented Wazzup WABA template API.

Ordinary Wazzup quick replies require a partner client_access_token, not the
account's v3/sidecar key. Do not probe private endpoints or silently substitute
an empty list for that limitation.
"""
import copy
import hashlib
import re
import threading
import time
import uuid

import requests
from flask import jsonify, request

from . import accounts

API_URL = 'https://api.wazzup24.com/v3/templates/whatsapp'
TTL = 600
ERROR_TTL = 60
MAX_STALE = 3600
MAX_TEXT = 4096
MAX_TITLE = 100
MAX_LOCAL = 250
EXCLUDED_CHANNELS = frozenset({
    '99df6893-fb6b-4e1d-a78e-9e6e6b37abb2',
    'a4bccb5e-5d41-483d-b7c1-a1079685577d',
})
QUICK_WARNING = ('Обычные шаблоны Wazzup недоступны с текущим API-ключом. '
                 'Здесь доступны шаблоны WABA и общие быстрые ответы iCORE.')
_VARIABLE = re.compile(r'\[\[([^\[\]]+)\]\]')
_CODE = re.compile(r'^@template:\s*([0-9a-fA-F-]{36})\s*\{(.*?)\}\s*$', re.S)
_cache_condition = threading.Condition()
_cache = {}


def init_template_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wazzup_quick_templates (
            id UUID PRIMARY KEY,
            account TEXT NOT NULL DEFAULT 'op',
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_by BIGINT NOT NULL,
            updated_by BIGINT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_wazzup_quick_templates_account
            ON wazzup_quick_templates(account, title);
    """)


def _normalize_waba(row):
    if not isinstance(row, dict) or str(row.get('status', '')).lower() != 'approved':
        return None
    code = row.get('templateCode')
    if not isinstance(code, str) or not _CODE.fullmatch(code):
        return None
    try:
        template_id = str(uuid.UUID(str(row.get('templateGuid'))))
        uuid.UUID(_CODE.fullmatch(code)[1])
    except (ValueError, TypeError, AttributeError):
        return None
    components = row.get('components') or []
    if not isinstance(components, list):
        return None
    components = [c for c in components if isinstance(c, dict)]
    preview, media, buttons = [], False, []
    for component in components:
        kind = str(component.get('type', '')).upper()
        if kind in ('HEADER', 'BODY', 'FOOTER') and isinstance(component.get('text'), str):
            preview.append(component['text'])
        if kind == 'HEADER' and str(component.get('format', '')).upper() not in ('', 'TEXT'):
            media = True
        if kind == 'BUTTONS':
            buttons += [dict(type=b.get('type'), text=b.get('text'))
                        for b in (component.get('buttons') or []) if isinstance(b, dict)]
    channels = row.get('channels') or []
    channels = [cid for cid in channels if isinstance(cid, str)] if isinstance(channels, list) else []
    return {
        'id': template_id, 'source': 'wazzup', 'kind': 'waba',
        'title': str(row.get('title') or row.get('name') or template_id),
        'text': '\n\n'.join(preview), 'templateCode': code,
        'channels': channels, 'variables': _VARIABLE.findall(code),
        'supported': not media,
        'unsupportedReason': 'Шаблоны с вложениями пока отправляйте из Wazzup.' if media else None,
        'buttons': buttons,
    }


def _fetch_templates(account, key, transport):
    result = []
    deadline = time.monotonic() + 15
    # Bounded pages protect a worker if an upstream API ignores offset.
    for offset in range(0, 1000, 100):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('upstream request time budget exceeded')
        response = transport.get(API_URL, headers={'Authorization': 'Bearer ' + key},
                                 params={'limit': 100, 'offset': offset}, timeout=(3, min(8, remaining)))
        if response.status_code != 200:
            raise ValueError('upstream HTTP failure')
        rows = response.json()
        if not isinstance(rows, list):
            raise ValueError('upstream response is not a list')
        result.extend(item for item in (_normalize_waba(row) for row in rows) if item)
        if len(rows) < 100:
            return result
    raise ValueError('upstream template list exceeds safe page limit')


def _vendor_templates(account, transport=None):
    """Single-flight cache: one fetch per process/account, never one per operator.

    A cold concurrent request waits at most two seconds. With a warm cache,
    concurrent readers reuse it immediately while the first caller refreshes.
    Failures are cached for one minute; old data expires after one hour.
    """
    key = accounts.api_key(account)
    if not key:
        return [], False, 'API-ключ Wazzup не настроен.'
    cache_key = (account, hashlib.sha256(key.encode()).hexdigest())
    now = time.monotonic()
    with _cache_condition:
        entry = _cache.get(cache_key)
        if entry and entry['expires'] > now:
            return copy.deepcopy(entry['items']), entry['stale'], entry['error']
        if entry and entry['loading']:
            if entry['loaded'] and now - entry['loaded'] <= MAX_STALE:
                return copy.deepcopy(entry['items']), True, entry['error']
            _cache_condition.wait_for(lambda: not entry['loading'], timeout=2)
            if entry['loading']:
                return [], False, 'Шаблоны Wazzup загружаются. Откройте список ещё раз.'
            return copy.deepcopy(entry['items']), entry['stale'], entry['error']
        if not entry:
            # Only known accounts are accepted by the public caller. Retire old
            # key generations rather than retaining cache entries forever.
            for old in list(_cache):
                if old[0] == account:
                    _cache.pop(old, None)
            entry = dict(items=[], loaded=0, expires=0, loading=False, stale=False, error=None)
            _cache[cache_key] = entry
        entry['loading'] = True
    try:
        items = _fetch_templates(account, key, transport or requests)
    except (requests.RequestException, ValueError, TypeError):
        with _cache_condition:
            now = time.monotonic()
            stale = bool(entry['loaded'] and now - entry['loaded'] <= MAX_STALE)
            if not stale:
                entry['items'] = []
            entry.update(loading=False, stale=stale, expires=now + ERROR_TTL,
                         error='Не удалось обновить шаблоны Wazzup.' +
                         (' Показана сохранённая копия.' if stale else ' Попробуйте позднее.'))
            _cache_condition.notify_all()
            return copy.deepcopy(entry['items']), stale, entry['error']
    with _cache_condition:
        now = time.monotonic()
        entry.update(items=items, loaded=now, expires=now + TTL,
                     loading=False, stale=False, error=None)
        _cache_condition.notify_all()
        return copy.deepcopy(items), False, None


def list_templates(account='op', channel_id=None, *, excluded_channels=EXCLUDED_CHANNELS,
                   transport=None):
    if account not in accounts.ACCOUNTS:
        raise ValueError('Unknown Wazzup account')
    rows, stale, error = _vendor_templates(account, transport)
    result = []
    for item in rows:
        item['channels'] = [cid for cid in item['channels'] if cid not in excluded_channels]
        if not item['channels'] or (channel_id and channel_id not in item['channels']):
            continue
        result.append(item)
    result.sort(key=lambda item: item['title'].casefold())
    return dict(items=result, stale=stale, sourceWarnings=[QUICK_WARNING] + ([error] if error else []))


def validate_template_message(account, channel_id, text, *, excluded_channels=EXCLUDED_CHANNELS):
    """Return a user-facing error or None. Plain text never fetches templates.

    Validate the exact vendor code syntax and number of values before claiming
    an outbox request. API-side WABA moderation/channel checks remain final.
    """
    if not isinstance(text, str) or not text.lstrip().lower().startswith('@template:'):
        return None
    match = _CODE.fullmatch(text.strip())
    if not match:
        return 'Некорректный код шаблона WABA. Выберите шаблон заново.'
    try:
        code_id = str(uuid.UUID(match[1]))
    except ValueError:
        return 'Некорректный идентификатор шаблона WABA.'
    listing = list_templates(account, channel_id, excluded_channels=excluded_channels)
    template = next((item for item in listing['items']
                     if str(uuid.UUID(_CODE.fullmatch(item['templateCode'])[1])) == code_id), None)
    if not template:
        return 'Этот шаблон WABA недоступен для выбранного канала. Обновите список шаблонов.'
    if not template['supported']:
        return template['unsupportedReason']
    values = _VARIABLE.findall(match[2])
    # Only [[value]] segments separated by semicolons are accepted. This also
    # rejects broken nesting, raw text and values that inject another segment.
    remainder = _VARIABLE.sub('', match[2])
    if (len(values) != len(template['variables'])
            or re.sub(r'\s', '', remainder) != ';' * max(0, len(values) - 1)
            or any(not value.strip() or value.strip() in template['variables'] for value in values)):
        return 'Заполните все переменные шаблона WABA.'
    return None


def _local_item(row):
    return dict(id=str(row[0]), source='icore', kind='text', title=row[1], text=row[2],
                templateCode=None, channels=[], variables=[], supported=True,
                unsupportedReason=None, buttons=[])


def render_template_preview(account, channel_id, text):
    """Readable provisional archive text; wire code remains in the durable outbox."""
    match = _CODE.fullmatch(text.strip())
    if not match:
        return text
    listing = list_templates(account, channel_id)
    item = next((item for item in listing['items']
                 if _CODE.fullmatch(item['templateCode'])[1].lower() == match[1].lower()), None)
    if not item:
        return 'Шаблон WhatsApp'
    preview = item['text']
    values = _VARIABLE.findall(match[2])
    for key, value in zip(item['variables'], values):
        number = re.fullmatch(r'bodyVar(\d+)', key)
        if number:
            preview = preview.replace('{{' + number[1] + '}}', value)
    return preview or item['title']


def register_template_routes(bp, actor, require_api_key, preflight, db,
                             excluded_channels=EXCLUDED_CHANNELS):
    def context():
        user, error = actor()
        if error:
            return None, error
        if request.args.get('account', 'op') != 'op':
            return None, (jsonify(error='Шаблоны доступны только для Верификаторов'), 403)
        return user, None

    def validated_body():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.get('account', 'op') != 'op':
            return None
        title, text = body.get('title'), body.get('text')
        if (not isinstance(title, str) or not 1 <= len(title.strip()) <= MAX_TITLE
                or not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT):
            return None
        return title.strip(), text.strip()

    @bp.route('/templates', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def templates():
        if request.method == 'OPTIONS':
            return preflight()
        user, error = context()
        if error:
            return error
        if request.method == 'GET':
            channel_id = request.args.get('channelId') or None
            if channel_id:
                try:
                    channel_id = str(uuid.UUID(channel_id))
                except ValueError:
                    return jsonify(error='Некорректный канал'), 400
                if channel_id in excluded_channels:
                    return jsonify(error='Global исключён из пилота'), 403
            with db._get_cursor() as cur:
                cur.execute("SELECT id,title,body FROM wazzup_quick_templates "
                            "WHERE account='op' ORDER BY lower(title),id")
                local = [_local_item(row) for row in cur.fetchall()]
            result = dict(list_templates('op', channel_id, excluded_channels=excluded_channels))
            result['items'] = local + result['items']
            result['canManage'] = True
            return jsonify(result)
        body = validated_body()
        if body is None:
            return jsonify(error='Укажите название до 100 символов и текст до 4096 символов'), 400
        with db._get_cursor() as cur:
            # Serialize the bounded catalogue's capacity check across workers.
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('wazzup-quick-templates'),hashtext('op'))")
            cur.execute("SELECT COUNT(*) FROM wazzup_quick_templates WHERE account='op'")
            if cur.fetchone()[0] >= MAX_LOCAL:
                return jsonify(error='Достигнут лимит 250 быстрых ответов'), 409
            cur.execute("INSERT INTO wazzup_quick_templates(id,account,title,body,created_by,updated_by) "
                        "VALUES(%s,'op',%s,%s,%s,%s) RETURNING id,title,body",
                        (str(uuid.uuid4()), *body, user[0], user[0]))
            item = _local_item(cur.fetchone())
        return jsonify(item=item), 201

    @bp.route('/templates/<template_id>', methods=['PATCH', 'DELETE', 'OPTIONS'])
    @require_api_key
    def template_detail(template_id):
        if request.method == 'OPTIONS':
            return preflight()
        user, error = context()
        if error:
            return error
        try:
            template_id = str(uuid.UUID(template_id))
        except ValueError:
            return jsonify(error='Шаблон не найден'), 404
        if request.method == 'DELETE':
            with db._get_cursor() as cur:
                cur.execute("DELETE FROM wazzup_quick_templates WHERE account='op' AND id=%s RETURNING id",
                            (template_id,))
                found = cur.fetchone()
            if not found:
                return jsonify(error='Шаблон не найден'), 404
            return jsonify(status='success')
        body = validated_body()
        if body is None:
            return jsonify(error='Укажите название до 100 символов и текст до 4096 символов'), 400
        with db._get_cursor() as cur:
            cur.execute("UPDATE wazzup_quick_templates SET title=%s,body=%s,updated_by=%s,updated_at=NOW() "
                        "WHERE account='op' AND id=%s RETURNING id,title,body",
                        (*body, user[0], template_id))
            row = cur.fetchone()
        if not row:
            return jsonify(error='Шаблон не найден'), 404
        return jsonify(item=_local_item(row))
