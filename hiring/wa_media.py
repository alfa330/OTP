# -*- coding: utf-8 -*-
"""Файлы сообщений номера WhatsApp найма (владелец 09.11.2026: «давай реализуем, так как есть ещё
и аудио файлы, которые тоже надо бы видеть»).

Сервис на шлюзе качает файл сообщения у WhatsApp и отдаёт его сюда подписанным запросом (id сообщения
в пути, файл — телом). Файл ложится в бакет, как фото задач и посылок; во вкладке карточки он
показывается подписанной ссылкой: фото и видео — прямо в ленте, голосовое и аудио — плеером с
расшифровкой, документ — кнопкой «скачать».

Расшифровка — тот же Gemini на Vertex, что у голосовых «Чатов ОП» (wazzup/voice_asr.py: модель,
двойное прослушивание коротких записей против выдумок, проверка правдоподобия), со своим вступлением:
говорят кандидаты на работу и рекрутер, а не водители. Правила расшифровки — общие, из того модуля.

Файлы старых сообщений (владелец 10.11.2026: «фото отображается просто как фото, и непонятно, как его
посмотреть»). До 09.11.2026 10:00 шлюз файлы не качал, а ключ к файлу приходит только вместе с самим
сообщением. Он есть у телефона номера: в ответе на опрос портал отдаёт шлюзу сообщения без файла и
следующее сообщение того же чата, шлюз просит у телефона историю чата до него (wa_bridge/resend.go) и
ставит файл нужного сообщения в свою очередь — дальше обычным путём. Так же — файл, который
«загружается» дольше суток (очередь шлюза потерялась). Телефон не прислал за RESEND_ATTEMPTS просьб —
«не сохранился (телефон не прислал файл)».

Модуль без базы и без Flask: пути, типы, подписи и вид для экрана — чистые функции, бакет и модель
приходят снаружи (HiringService получает их зависимостями, тесты — подделками).
"""

import logging
import re
import threading
import time
import uuid
from datetime import timedelta
from urllib.parse import quote

log = logging.getLogger(__name__)

MAX_BYTES = 25 * 1024 * 1024          # предел и на шлюзе (wa_bridge/media.go: maxMediaBytes)
DOWNLOADABLE = ('image', 'video', 'audio', 'voice', 'document')
TRANSCRIBED = ('voice', 'audio')
PREFIX = 'hiring/whatsapp'
FAILURE_REASONS = {
    'gone': 'файла у WhatsApp уже нет',
    'too_large': 'файл больше 25 МБ',
    'rejected': 'портал не принял файл',
    'retries': 'не удалось скачать',
    'broken': 'не удалось скачать',
    'unanswered': 'телефон не прислал файл',
}

# Просьбы к телефону прислать старое сообщение заново (см. начало модуля).
RESEND_BATCH = 2                          # за один опрос шлюза (опрос — раз в 10 с)
RESEND_RETRY = timedelta(minutes=30)      # телефон не ответил — следующая просьба
RESEND_ATTEMPTS = 4                       # после последней — «телефон не прислал файл»
FETCH_STALE = timedelta(hours=24)         # очередь шлюза: 8 попыток за ~11 часов — дольше суток не ждёт

# Показываются прямо в ленте (inline); всё остальное — только скачиванием.
_INLINE = ('image/jpeg', 'image/png', 'image/webp', 'image/gif', 'video/mp4', 'video/3gpp', 'video/quicktime',
           'video/webm', 'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/aac', 'audio/amr', 'audio/wav',
           'audio/x-wav', 'audio/webm', 'audio/opus', 'audio/flac')
# Типы, которые браузер исполнил бы на странице хранилища: такой файл кладём как octet-stream.
_UNSAFE = ('text/html', 'application/xhtml+xml', 'image/svg+xml', 'text/javascript', 'application/javascript',
           'application/x-javascript', 'text/xml', 'application/xml')
_EXT = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif',
        'video/mp4': '.mp4', 'video/3gpp': '.3gp', 'video/quicktime': '.mov', 'video/webm': '.webm',
        'audio/ogg': '.ogg', 'audio/mpeg': '.mp3', 'audio/mp4': '.m4a', 'audio/aac': '.aac', 'audio/amr': '.amr',
        'audio/wav': '.wav', 'audio/opus': '.opus', 'application/pdf': '.pdf',
        'application/msword': '.doc', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
        'application/vnd.ms-excel': '.xls', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
        'text/plain': '.txt', 'application/zip': '.zip'}
_TYPE_RE = re.compile(r'^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$')
_EXT_RE = re.compile(r'\.([A-Za-z0-9]{1,8})$')


def base_type(mime):
    return str(mime or '').split(';', 1)[0].strip().lower()


def content_type(mime):
    """Тип файла для бакета: из сообщения WhatsApp, без опасных для браузера (те — octet-stream)."""
    base = base_type(mime)
    if not _TYPE_RE.match(base) or base in _UNSAFE:
        return 'application/octet-stream'
    return base


def is_inline(kind, ctype):
    return kind in ('image', 'video', 'audio', 'voice') and base_type(ctype) in _INLINE


def object_path(message_id, ctype, file_name, now):
    """Путь в бакете: по месяцам, id строки сообщения и случайный хвост. Имя файла кандидата в путь не
    идёт — оно хранится в сообщении и отдаётся при скачивании."""
    ext = _EXT.get(base_type(ctype))
    if not ext:
        match = _EXT_RE.search(str(file_name or ''))
        ext = ('.' + match.group(1).lower()) if match else ''
    return '%s/%04d/%02d/%d-%s%s' % (PREFIX, now.year, now.month, int(message_id), uuid.uuid4().hex[:12], ext)


def disposition(kind, ctype, file_name):
    """Content-Disposition подписанной ссылки: в ленте — inline, документ — attachment со своим именем."""
    if is_inline(kind, ctype):
        return 'inline'
    name = str(file_name or '').strip().replace('"', '').replace('\r', ' ').replace('\n', ' ')[:150]
    if not name:
        name = 'whatsapp' + (_EXT.get(base_type(ctype)) or '')
    ascii_name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('_') or 'file'
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(name, safe=''))


def public_view(kind, media, sign=None):
    """Файл сообщения для вкладки карточки: что показывать и подписанная ссылка. Путь в бакете наружу
    не уходит.

    state: stored — файл есть (url); loading — ждём от шлюза (качает сам или просит телефон прислать
    старое сообщение заново); failed — не сохранится (reason); too_large — больше 25 МБ; none — не файл
    (стикер, геопозиция, контакт)."""
    media = dict(media or {})
    view = {key: media[key] for key in ('mime', 'size', 'seconds', 'file_name') if media.get(key) is not None}
    if kind not in DOWNLOADABLE:
        view['state'] = 'none'
        return view
    if media.get('object'):
        ctype = media.get('stored_type') or content_type(media.get('mime'))
        url = sign(media['object'], ctype, disposition(kind, ctype, media.get('file_name'))) if sign else None
        view['state'] = 'stored' if url else 'failed'
        if url:
            view['url'] = url
            view['inline'] = is_inline(kind, ctype)
        if media.get('stored_size'):
            view['size'] = media['stored_size']
        transcript = media.get('transcript') or {}
        if kind in TRANSCRIBED and transcript.get('status'):
            view['transcript'] = {'status': transcript['status'], 'text': transcript.get('text') or None}
        return view
    if media.get('failed'):
        view['state'] = 'failed'
        view['reason'] = FAILURE_REASONS.get(media['failed'], 'не удалось скачать')
    elif (media.get('size') or 0) > MAX_BYTES:
        view['state'] = 'too_large'
    else:
        view['state'] = 'loading'
    return view


def resend_chat(jid, phone, lid, attempt):
    """JID чата для просьбы к телефону. Личный чат WhatsApp живёт и по номеру, и по LID, а у чата в
    портале записан вид последнего сообщения: третья просьба — другим видом, если он известен
    (сообщение могло прийти до смены вида), остальные — как у чата."""
    jid = str(jid or '').strip()
    other = None
    if jid.endswith('@lid'):
        digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
        other = (digits + '@s.whatsapp.net') if digits else None
    elif jid.endswith('@s.whatsapp.net'):
        digits = ''.join(ch for ch in str(lid or '') if ch.isdigit())
        other = (digits + '@lid') if digits else None
    return other if other and attempt == 2 else jid


def resend_anchor(anchor_id, anchor_from_me, anchor_at, tz):
    """Следующее сообщение чата для шлюза — отсчёт истории: id, своё ли и время (секунды Unix; в базе
    время Алматы без пояса). Нет следующего — None: шлюз попросит само сообщение заново."""
    if not anchor_id or anchor_at is None:
        return None
    return {'id': anchor_id, 'from_me': bool(anchor_from_me), 'ts': int(anchor_at.replace(tzinfo=tz).timestamp())}


# ─── Бакет ────────────────────────────────────────────────────────────────────

class Storage:
    """Бакет файлов найма: клиент Google Cloud Storage строится один раз и по требованию (разбор ключа
    сервисного аккаунта — не на каждый запрос). Подписи кэшируются, как у фото задач: подпись v4
    кладёт в адрес момент подписания, и без кэша лента, опрашиваемая раз в 15 секунд, качала бы фото
    и голосовые заново."""

    SIGN_MINUTES = 120
    RESIGN_BEFORE_MINUTES = 60
    SIGNED_MAX = 2000

    def __init__(self, client_getter, bucket_name):
        self._client_getter = client_getter
        self._bucket_name = bucket_name
        self._client = None
        self._lock = threading.Lock()
        self._signed = {}

    def _name(self):
        name = self._bucket_name() if callable(self._bucket_name) else self._bucket_name
        return str(name or '').strip()

    def available(self):
        return bool(self._name())

    def _bucket(self):
        name = self._name()
        if not name:
            raise RuntimeError('бакет файлов не настроен (GOOGLE_CLOUD_STORAGE_BUCKET)')
        with self._lock:
            if self._client is None:
                self._client = self._client_getter()
        return self._client.bucket(name)

    def upload(self, path, data, ctype):
        self._bucket().blob(path).upload_from_string(data, content_type=ctype, timeout=120)

    def download(self, path):
        return self._bucket().blob(path).download_as_bytes(timeout=120)

    def delete(self, path):
        self._bucket().blob(path).delete(timeout=60)

    def sign(self, path, ctype, content_disposition):
        key = (path, ctype, content_disposition)
        now = time.monotonic()
        cached = self._signed.get(key)
        if cached and cached[1] - now > self.RESIGN_BEFORE_MINUTES * 60:
            return cached[0]
        try:
            url = self._bucket().blob(path).generate_signed_url(
                version='v4', expiration=timedelta(minutes=self.SIGN_MINUTES), method='GET',
                response_disposition=content_disposition, response_type=ctype)
        except Exception:  # noqa: BLE001
            log.warning('WhatsApp найма: подпись файла не собралась', exc_info=True)
            return None
        if len(self._signed) >= self.SIGNED_MAX:
            self._signed = {k: v for k, v in self._signed.items() if v[1] > now}
            if len(self._signed) >= self.SIGNED_MAX:
                self._signed.clear()
        self._signed[key] = (url, now + self.SIGN_MINUTES * 60)
        return url


# ─── Расшифровка голосовых ────────────────────────────────────────────────────

INTRO = ('Ты — система дословной расшифровки голосовых сообщений из WhatsApp отдела найма компании в '
         'Казахстане. Голосовые присылают кандидаты на работу и рекрутер; говорят по-казахски, по-русски или '
         'смешивая языки в одной фразе. Рекрутер будет ЧИТАТЬ расшифровку вместо прослушивания.')


def system_prompt(asr):
    """Вступление про найм + правила расшифровки «Чатов ОП» как есть (одни правила на оба раздела)."""
    text = asr.SYSTEM
    start = text.find('Правила:')
    return INTRO + '\n\n' + (text[start:] if start >= 0 else text)


def transcription_available():
    try:
        from wazzup import voice_asr
        return voice_asr.vertex_configured()
    except Exception:  # noqa: BLE001
        return False


def transcribe(raw, ctype):
    """Голосовое → {'status': 'ready'|'no_speech', 'text', 'language', 'model', 'cost', 'error', …}.
    Ошибка — VoiceError (kind: retry — повторить позже; skipped / unavailable — не повторять)."""
    from wazzup import voice_asr
    system = system_prompt(voice_asr)
    timeout = voice_asr.request_seconds(None)

    def generate(model, body):
        body['systemInstruction'] = {'parts': [{'text': system}]}
        return voice_asr._generate(model, body, timeout)  # noqa: SLF001 — тот же вызов Vertex, свой промпт

    return voice_asr.transcribe(raw, ctype, generate=generate)


def transcript_record(result, now):
    """Что хранить из ответа распознавания (без сырых блоков usage)."""
    return {'status': result.get('status') or 'failed', 'text': result.get('text') or None,
            'language': result.get('language'), 'model': result.get('model'), 'cost': result.get('cost'),
            'error': result.get('error'), 'at': now.isoformat(timespec='seconds')}
