# -*- coding: utf-8 -*-
"""Номер WhatsApp найма (владелец 06.11.2026): чистые правила без базы.

Реклама на найм ведёт в WhatsApp на отдельный номер. Номер остаётся в приложении WhatsApp Business
на телефоне рекрутера, а сервис на шлюзе (wa_bridge/, библиотека whatsmeow) подключён к нему
связанным устройством: каждое входящее приходит сюда событием, первое — становится откликом в пуле.

Сервис сам ничего не отправляет (этап 1): рекрутеры отвечают с телефона, их ответы приходят сюда
эхом и видны во вкладке карточки. Касаниями они не пишутся — телефон общий, автора не узнать.

Безопасность номера держит сервис (стоп без повторов на любой сигнал ограничения, одна сессия,
предохранители на переподключения) — здесь только то, что видно людям: состояние, причина
остановки, кнопки «Подключить / Возобновить / Отвязать» и тревоги руководителю рекрутинга.
"""

from datetime import datetime, timedelta

from . import rules

SOURCE_CODE = 'whatsapp'
INTEGRATION = 'whatsapp'

# Состояния, которые присылает сервис, плюс offline — его выставляет сторож портала.
UNPAIRED, PAIRING, CONNECTING, CONNECTED, PARKED, OFFLINE = (
    'unpaired', 'pairing', 'connecting', 'connected', 'parked', 'offline')
BRIDGE_STATES = (UNPAIRED, PAIRING, CONNECTING, CONNECTED, PARKED)
STATE_LABELS = {
    UNPAIRED: 'Не подключён', PAIRING: 'Ждёт сканирования QR', CONNECTING: 'Подключается',
    CONNECTED: 'Подключён', PARKED: 'Остановлен', OFFLINE: 'Сервис на шлюзе не на связи',
    'not_configured': 'Не настроен',
}

# Причины остановки. Сервис после них сам не подключается: решает человек.
PARK_REASONS = {
    'logged_out': 'Устройство отвязано от телефона — нужна новая привязка',
    'replaced': 'Сессию открыли в другом месте — сервис остановился, чтобы не было двух копий',
    'temp_ban': 'WhatsApp временно ограничил номер — сервис остановлен до конца срока',
    'banned': 'WhatsApp заблокировал номер',
    'locked': 'WhatsApp закрыл номеру связанные устройства',
    'outdated': 'Версия клиента устарела — сервис нужно обновить',
    'refused': 'Сервер WhatsApp отказал в подключении',
    'reconnect_fuse': 'Слишком много переподключений подряд — сервис остановлен',
    'start_fuse': 'Сервис перезапускался слишком часто — остановлен',
    'pair_failed': 'Две неудачные привязки подряд — остановлено',
    'passkey': 'WhatsApp просит passkey при привязке — нужна помощь разработчика',
    'duplicate': 'На шлюзе запущена вторая копия сервиса — эта не подключается',
}
# Временный бан: код причины от сервера WhatsApp (whatsmeow TempBanReason) → человеческий текст.
TEMP_BAN_CODES = {
    101: 'много сообщений тем, у кого номера нет в контактах',
    102: 'номер заблокировали многие собеседники',
    103: 'много групп с людьми не из контактов',
    104: 'одно и то же сообщение многим',
    106: 'много сообщений в рассылку',
}
# Привязку и отвязку решает человек. «Возобновить» снимает остановку — кроме «вторая копия»:
# её снимает только остановка лишнего контейнера на шлюзе.
COMMANDS = ('pair', 'resume', 'logout')

OFFLINE_AFTER = timedelta(minutes=3)          # без опроса дольше — сервис не на связи
QR_FRESH = timedelta(seconds=70)              # код QR живёт 20–60 с, сервис присылает новый
PHONE_WARN_DAYS = 7                           # телефон давно не в сети: подсказка
PHONE_ALERT_DAYS = 12                         # и тревога (предел WhatsApp — 14 дней)

MESSAGE_KINDS = ('text', 'image', 'video', 'audio', 'voice', 'document', 'sticker', 'location', 'contact', 'other')
KIND_LABELS = {'image': 'Фото', 'video': 'Видео', 'audio': 'Аудио', 'voice': 'Голосовое', 'document': 'Документ',
               'sticker': 'Стикер', 'location': 'Геопозиция', 'contact': 'Контакт', 'other': 'Сообщение'}
AD_FIELDS = ('title', 'body', 'source_url', 'source_id', 'source_type', 'ctwa_clid', 'source_app',
             'conversion_source', 'entry_point')
EVENTS_MAX = 200


def _text(value, limit):
    text = str(value or '').strip()
    return text[:limit] if text else None


def _digits(value, limit=20):
    text = ''.join(ch for ch in str(value or '') if ch.isdigit())
    return text[:limit] if text else None


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def moment_of(unix_seconds):
    """Время сообщения (секунды Unix) → наивное время Алматы, как во всём разделе."""
    seconds = _int(unix_seconds)
    if seconds is None or seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, rules.ALMATY).replace(tzinfo=None)


def kz_phone(digits):
    """Номер WhatsApp всегда международный: казахстанский (как у карточек, +7…) — только 11 цифр с 7.
    rules.normalize_phone принял бы и 10 цифр, а у WhatsApp это чужой номер с коротким кодом страны."""
    digits = _digits(digits)
    if not digits or len(digits) != 11 or not digits.startswith('7'):
        return None
    return rules.normalize_phone(digits)


def chat_key(phone, lid):
    """Ключ собеседника: казахстанский номер (как у карточек), иначе цифры чужого номера, иначе LID.

    Личный чат WhatsApp может прийти и по номеру, и по LID (скрытый идентификатор) — ключ по
    номеру склеивает оба вида, а чат «только LID» доклеится, когда номер станет известен."""
    normalized = kz_phone(phone)
    if normalized:
        return normalized
    digits = _digits(phone)
    if digits:
        return 'wa:' + digits
    lid = _digits(lid, 32)
    return ('lid:' + lid) if lid else None


def clean_detail(raw):
    """Подробности состояния от сервиса — только известные поля и с обрезкой: сервис — внешняя сторона."""
    raw = raw if isinstance(raw, dict) else {}
    park = raw.get('park') if isinstance(raw.get('park'), dict) else None
    timelock = raw.get('timelock') if isinstance(raw.get('timelock'), dict) else None
    detail = {
        'paired': bool(raw.get('paired')),
        'number': _digits(raw.get('number')),
        'push_name': _text(raw.get('push_name'), 100),
        'version': _text(raw.get('version'), 60),
        'qr': _text(raw.get('qr'), 600),
        'connected_since': _text(raw.get('connected_since'), 32),
        'phone_seen_at': _text(raw.get('phone_seen_at'), 32),
        'pair_failures': _int(raw.get('pair_failures')),
        'park': {
            'reason': _text(park.get('reason'), 32),
            'code': _int(park.get('code')),
            'since': _text(park.get('since'), 32),
            'until': _text(park.get('until'), 32),
        } if park else None,
        'timelock': {'active': bool(timelock.get('active')), 'until': _text(timelock.get('until'), 32)} if timelock else None,
    }
    return {name: value for name, value in detail.items() if value is not None}


def parse_message(raw):
    """Событие сообщения от сервиса → чистый словарь или None (событие не про личный чат / битое).

    Тексты обрезаются здесь, а не в базе: сервис на шлюзе — внешняя сторона, длину не обещает."""
    if not isinstance(raw, dict) or raw.get('type') != 'message':
        return None
    message_id = _text(raw.get('id'), 128)
    chat = _text(raw.get('chat'), 96)
    if not message_id or not chat:
        return None
    phone = _digits(raw.get('phone'))
    lid = _digits(raw.get('lid'), 32)
    key = chat_key(phone, lid)
    if not key:
        return None
    kind = str(raw.get('kind') or 'text').strip().lower()
    if kind not in MESSAGE_KINDS:
        kind = 'other'
    media = None
    if isinstance(raw.get('media'), dict):
        source = raw['media']
        media = {k: v for k, v in {
            'mime': _text(source.get('mime'), 100),
            'size': _int(source.get('size')),
            'seconds': _int(source.get('seconds')),
            'file_name': _text(source.get('file_name'), 200),
        }.items() if v is not None} or None
    ad = None
    if isinstance(raw.get('ad'), dict):
        ad = {name: _text(raw['ad'].get(name), 500) for name in AD_FIELDS}
        ad = {name: value for name, value in ad.items() if value} or None
    return {
        'id': message_id,
        'chat': chat,
        'key': key,
        'phone': kz_phone(phone),
        'phone_digits': phone,
        'lid': lid,
        'from_me': bool(raw.get('from_me')),
        'at': moment_of(raw.get('ts')),
        'push_name': _text(raw.get('push_name'), 200),
        'kind': kind,
        'text': _text(raw.get('text'), 8000),
        'media': media,
        'ad': ad,
        'edit_of': _text(raw.get('edit_of'), 128),
        'revoke_of': _text(raw.get('revoke_of'), 128),
    }


def campaign_of(ad):
    """Кампания карточки из данных рекламы Click-to-WhatsApp: заголовок объявления или его id."""
    if not ad:
        return None
    title = ad.get('title') or (ad.get('body') or '')[:120]
    if title:
        return title[:200]
    return ('Реклама %s' % ad['source_id'])[:200] if ad.get('source_id') else None


def first_message_comment(message):
    """Комментарий новой карточки — первое сообщение кандидата, чтобы рекрутер видел вопрос сразу."""
    label = KIND_LABELS.get(message['kind']) if message['kind'] != 'text' else None
    text = message.get('text') or ''
    parts = ['[%s]' % label.lower()] if label else []
    if text:
        parts.append('«%s»' % (text if len(text) <= 500 else text[:499] + '…'))
    return ('WhatsApp: ' + ' '.join(parts)) if parts else 'Написал в WhatsApp'


def park_text(detail):
    """Причина остановки человеческими словами (с кодом временного бана и сроком, если есть)."""
    park = (detail or {}).get('park') or {}
    reason = park.get('reason')
    text = PARK_REASONS.get(reason) or ('Сервис остановлен: %s' % reason if reason else 'Сервис остановлен')
    code = _int(park.get('code'))
    if reason == 'temp_ban' and code in TEMP_BAN_CODES:
        text += ' (причина WhatsApp: %s)' % TEMP_BAN_CODES[code]
    elif reason == 'refused' and code:
        text += ' (код %d)' % code
    until = park.get('until')
    if until:
        text += ', до %s' % str(until)[:16].replace('T', ' ')
    return text


def can_command(action, state, detail):
    """(можно ли, причина отказа). Привязка — только без сессии; «Возобновить» — только остановленный;
    отвязка — когда сессия есть. Вторую копию «Возобновить» не снимает."""
    detail = detail or {}
    paired = bool(detail.get('paired'))
    if action not in COMMANDS:
        return False, 'Неизвестная команда'
    if state == OFFLINE:
        return False, 'Сервис на шлюзе не на связи — команды он не получит'
    if state not in BRIDGE_STATES:
        return False, 'Сервис на шлюзе ещё не запущен'
    reason = ((detail.get('park') or {}).get('reason'))
    if action == 'pair':
        if paired:
            return False, 'Номер уже привязан'
        if state == PARKED and reason not in ('logged_out',):
            return False, 'Сервис остановлен — сначала «Возобновить»'
        if state in (PAIRING, CONNECTING, CONNECTED):
            return False, 'Привязка уже идёт' if state == PAIRING else 'Номер уже подключается'
        return True, None
    if action == 'resume':
        if state != PARKED:
            return False, 'Сервис не остановлен'
        if reason == 'duplicate':
            return False, 'Вторую копию сервиса нужно остановить на шлюзе'
        return True, None
    if not paired:
        return False, 'Номер не привязан'
    return True, None


def phone_seen_days(detail, now):
    seen = (detail or {}).get('phone_seen_at')
    if not seen:
        return None
    try:
        moment = datetime.fromisoformat(str(seen)[:19])
    except ValueError:
        return None
    return max(0, (now - moment).days)


def view(row, now):
    """Состояние для «Настроек → Интеграции»: метка, тон, причина, QR, подсказки, кнопки."""
    row = row or {}
    state = row.get('state') or 'not_configured'
    detail = row.get('cursor') or {}
    settings = row.get('settings') or {}
    last_run = row.get('last_run_at')
    if state in BRIDGE_STATES and last_run and now - last_run > OFFLINE_AFTER:
        state = OFFLINE
    qr = None
    if state == PAIRING and detail.get('qr'):
        qr_at = detail.get('qr_at')
        try:
            fresh = qr_at and now - datetime.fromisoformat(str(qr_at)[:19]) <= QR_FRESH
        except ValueError:
            fresh = False
        qr = detail['qr'] if fresh else None
    days = phone_seen_days(detail, now) if state == CONNECTED else None
    warnings = []
    if days is not None and days >= PHONE_WARN_DAYS:
        warnings.append('Телефон с номером не выходил в WhatsApp %d дн. — откройте на нём WhatsApp Business: '
                        'через 14 дней без телефона привязка слетает' % days)
    timelock = detail.get('timelock') or {}
    if timelock.get('active'):
        warnings.append('WhatsApp ограничил номеру первые сообщения незнакомым%s — отвечать на входящие можно'
                        % ((' до ' + str(timelock.get('until'))[:16].replace('T', ' ')) if timelock.get('until') else ''))
    command = settings.get('command') or None
    return {
        'state': state,
        'label': STATE_LABELS.get(state, state),
        'tone': ('green' if state == CONNECTED else 'red' if state in (PARKED, OFFLINE)
                 else 'amber' if state in (PAIRING, CONNECTING) else 'slate'),
        'reason': park_text(detail) if state == PARKED else None,
        'park_reason': (detail.get('park') or {}).get('reason') if state == PARKED else None,
        'number': detail.get('number') or None,
        'push_name': detail.get('push_name') or None,
        'qr': qr,
        'phone_seen_days': days,
        'warnings': warnings,
        'pending': command.get('action') if command else None,
        'version': detail.get('version') or None,
        'last_seen_at': last_run.isoformat(timespec='seconds') if last_run else None,
        'connected_since': detail.get('connected_since') or None,
        'received_total': int(row.get('received_total') or 0),
        'actions': {action: can_command(action, state, detail)[0] for action in COMMANDS},
    }


def alert_for(old_state, new_state, detail):
    """Тревога руководителю рекрутинга при переходе состояния: (ключ, заголовок, текст) или None.

    Ключ дедупликации — по причине и моменту остановки: одна остановка — одна тревога, сколько бы
    опросов ни пришло."""
    detail = detail or {}
    if new_state == PARKED and old_state != PARKED:
        park = detail.get('park') or {}
        return ('wa:park:%s:%s' % (park.get('reason') or '-', str(park.get('since') or '')[:19]),
                'WhatsApp найма: сервис остановлен', park_text(detail))
    if new_state == CONNECTED and old_state in (PARKED, OFFLINE):
        return ('wa:back:%s' % str(detail.get('connected_since') or '')[:19],
                'WhatsApp найма снова подключён', 'Новые сообщения снова попадают в воронку')
    return None
