"""Забор переписки аккаунта «Поток» в wazzup_messages/wazzup_chats.

Одна и та же процедура делает и первоначальную загрузку истории (days=45),
и регулярное дотягивание (каждые 10 минут: с последнего сохранённого
сообщения минус перекрытие). Сообщения окна чатов переводятся в форму
вебхука Wazzup и идут в тот же db.store_wazzup_messages, что и вебхук
исторического аккаунта — дальше оба аккаунта живут одним кодом.

Сопоставление полей (снято с живого ответа 23.09.2026):
  id → messageId; datetime (мс UTC) → dateTime; incoming → not isEcho;
  type 1/2/3/5 → text/image/audio/document (уточняется по contentType);
  status 99 — входящее, 1/2/3 — sent/delivered/read, errorCode → error;
  contentSha + filename → https://store.wazzup24.com/<sha>/?filename=… —
  та же форма, что contentUri вебхука (проверено HEAD → 200);
  authorName — имя менеджера (в окне подписано «API • Имя»), id автора нет.
"""
import logging
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone

from wazzup import accounts as wz_accounts
from wazzup.potok_client import WazzupInternalClient

log = logging.getLogger(__name__)

DEFAULT_HISTORY_DAYS = 45
DEFAULT_OVERLAP_HOURS = 2

TYPE_BY_CODE = {1: 'text', 2: 'image', 3: 'audio', 4: 'video', 5: 'document',
                6: 'vcard', 7: 'geo'}
STATUS_BY_CODE = {0: 'sent', 1: 'sent', 2: 'delivered', 3: 'read'}
MEDIA_URI = 'https://store.wazzup24.com/{sha}/?filename={filename}'

# Ход текущего/последнего прогона — для ручки статуса и логов. Один процесс,
# одна джоба (max_instances=1 + этот замок), поэтому словаря достаточно.
STATE = {
    'running': False, 'account': None, 'mode': None, 'since': None,
    'started_at': None, 'finished_at': None,
    'chats_seen': 0, 'chats_done': 0, 'skipped_known': 0, 'messages': 0, 'requests': 0,
    'error': None,
}
_RUN_LOCK = threading.Lock()


def message_type(m):
    """Наш строковый тип сообщения (как в вебхуке)."""
    content_type = str(m.get('contentType') or '').lower()
    if m.get('isHsm') or m.get('template'):
        return 'wapi_template'
    if m.get('location'):
        return 'geo'
    if m.get('vcard'):
        return 'vcard'
    if content_type.startswith('image/'):
        return 'image'
    if content_type.startswith('audio/'):
        return 'audio'
    if content_type.startswith('video/'):
        return 'video'
    if content_type:
        return 'document'
    code = m.get('type')
    if code in TYPE_BY_CODE:
        return TYPE_BY_CODE[code]
    return 'text' if m.get('text') else 'unsupported'


def message_status(m):
    if m.get('errorCode') or m.get('errorMessagesCode'):
        return 'error'
    if m.get('incoming'):
        return 'inbound'
    return STATUS_BY_CODE.get(m.get('status'), 'sent')


def content_uri(m):
    sha = m.get('contentSha')
    if not sha:
        return None
    return MEDIA_URI.format(sha=sha, filename=urllib.parse.quote(str(m.get('filename') or '')))


def iso_utc(ms):
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


def chat_contact(chat):
    """Контакт чата: имя — contactName из списка, телефон — сам chatId (у
    whatsapp это и есть номер). userName/userPhone в строке списка — это
    ОТВЕТСТВЕННЫЙ менеджер, а не клиент: взяв их, я один раз записал всем
    чатам «Потока» один и тот же телефон сотрудника."""
    chat_id = str(chat.get('chatId') or '')
    phone = chat_id if chat.get('chatType') == 'whatsapp' and chat_id.isdigit() else None
    name = chat.get('contactName') or None
    return {'name': name, 'phone': phone}


def to_webhook_message(m, chat):
    """Сообщение окна чатов → элемент messages[] вебхука Wazzup."""
    incoming = bool(m.get('incoming'))
    return {
        'messageId': m.get('id'),
        'channelId': m.get('channelId') or _chat_channel_id(chat),
        'chatType': m.get('chatType') or chat.get('chatType'),
        'chatId': m.get('chatId') or chat.get('chatId'),
        'dateTime': iso_utc(m.get('datetime')) if m.get('datetime') else None,
        'isEcho': not incoming,
        'type': message_type(m),
        'text': m.get('text'),
        'contentUri': content_uri(m),
        # у входящих authorName — телефон клиента, автором его не считаем
        'authorName': None if incoming else (m.get('authorName') or None),
        'authorId': None,
        'contact': chat_contact(chat),
        'status': message_status(m),
        'sentFromApp': None,
        'isEdited': bool(m.get('messageEditingByUser')),
        'isDeleted': bool(m.get('messageDeleteByUser')),
    }


def _chat_channel_id(chat):
    for sub in chat.get('chats') or []:
        if sub.get('channelId'):
            return sub['channelId']
    return None


def build_client(account='potok'):
    meta = wz_accounts.ACCOUNTS[account]
    return WazzupInternalClient(
        api_key=wz_accounts.api_key(account),
        account_id=meta['account_id'],
        viewer_user_id=wz_accounts.viewer_user_id(account),
    )


def _to_ms(value):
    """TIMESTAMPTZ из базы → миллисекунды эпохи, как datetime у Wazzup."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def sync_account(db, client, since_dt, account='potok'):
    """Тянет из окна всё не старше since_dt и пишет в базу. Возвращает счётчики.

    Возобновляемо: чат, чьё последнее сообщение (lastMessage из списка Wazzup)
    уже лежит у нас, пропускается без запроса сообщений. Сообщения чата пишутся
    одним батчем, поэтому «есть последнее» = «чат загружен целиком в окне».
    Рестарт процесса посреди многочасовой загрузки истории (любой деплой
    убивает фоновый поток) — не потеря: следующий запуск за десятки секунд
    пройдёт по загруженным чатам и продолжит с того, где остановился."""
    since_ms = int(since_dt.timestamp() * 1000)
    known = {cid: _to_ms(ts) for cid, ts in (db.wazzup_chat_last_messages(account) or {}).items()}
    stats = {'chats_seen': 0, 'chats_done': 0, 'messages': 0, 'skipped_chats': 0,
             'skipped_known': 0}
    for chat in client.iter_chats(since_ms):
        stats['chats_seen'] += 1
        STATE['chats_seen'] = stats['chats_seen']
        chat_id, chat_type, channel_id = client.chat_identity(chat)
        if not chat_id or not chat_type:
            stats['skipped_chats'] += 1
            continue
        stored_last = known.get(str(chat_id))
        if stored_last is not None and stored_last >= client.chat_last_ms(chat):
            stats['skipped_known'] += 1
            STATE['skipped_known'] = stats['skipped_known']
            continue
        batch = []
        for m in client.iter_messages(chat_id, since_ms, chat_type=chat_type, channel_id=channel_id):
            converted = to_webhook_message(m, chat)
            if converted['messageId'] and converted['dateTime'] and converted['channelId']:
                batch.append(converted)
        if batch:
            stats['messages'] += db.store_wazzup_messages(batch, account=account)
        stats['chats_done'] += 1
        STATE['chats_done'] = stats['chats_done']
        STATE['messages'] = stats['messages']
        STATE['requests'] = client.requests_made
    return stats


def run_sync(db, account='potok', days=None, overlap_hours=DEFAULT_OVERLAP_HOURS,
             client_factory=build_client, now=None):
    """Один прогон: days задан — история за столько дней (первоначальная
    загрузка), иначе — с последнего сохранённого сообщения минус перекрытие
    (регулярное дотягивание). Параллельный запуск не ждёт, а выходит."""
    if not _RUN_LOCK.acquire(blocking=False):
        return {'locked': True}
    now = now or datetime.now(timezone.utc)
    try:
        if days:
            since = now - timedelta(days=int(days))
            mode = 'history'
        else:
            last = db.wazzup_last_message_at(account)
            since = (last - timedelta(hours=float(overlap_hours))) if last \
                else now - timedelta(days=DEFAULT_HISTORY_DAYS)
            mode = 'incremental'
        STATE.update(running=True, account=account, mode=mode, since=since.isoformat(),
                     started_at=now.isoformat(), finished_at=None,
                     chats_seen=0, chats_done=0, skipped_known=0, messages=0, requests=0,
                     error=None)
        client = client_factory(account)
        stats = sync_account(db, client, since, account=account)
        stats.update(mode=mode, since=since.isoformat(), requests=client.requests_made)
        log.info('wazzup %s sync (%s, с %s): чатов %s (уже были %s), сообщений %s, запросов %s',
                 account, mode, since.isoformat(), stats['chats_done'], stats.get('skipped_known', 0),
                 stats['messages'], client.requests_made)
        return stats
    except Exception as error:
        STATE['error'] = f'{type(error).__name__}: {error}'
        log.exception('wazzup %s sync failed', account)
        raise
    finally:
        STATE['running'] = False
        STATE['finished_at'] = datetime.now(timezone.utc).isoformat()
        _RUN_LOCK.release()
