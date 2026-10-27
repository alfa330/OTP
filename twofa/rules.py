"""Сроки, подписи и тексты раздела «Коды 2FA». Ни базы, ни сети.

Все сроки — из ТЗ #373, в той части, что выбрал владелец 27.10.2026:

    10 минут   запрос ждёт подтверждения, потом сгорает (п. 3), сотрудник об этом узнаёт
    3 минуты   после подтверждения «Код не подошёл» выдаёт новый код без главы (п. 5)
    60 секунд  цифры кода живут в Telegram, потом стираются (п. 5)

Время на экране и в сообщениях — Алматы (+5): так его видят люди. Внутри сервис считает в
секундах эпохи (как часы телефона для TOTP), в базу кладёт наивное время Алматы — как
соседние разделы портала.
"""

import html
from datetime import datetime, timedelta, timezone

from .totp import group

REQUEST_TTL_SECONDS = 10 * 60
RESEND_WINDOW_SECONDS = 3 * 60
CODE_VISIBLE_SECONDS = 60

TITLE_MAX = 120
SERVICE_MAX = 80

_ALMATY = timedelta(hours=5)

STATUS_LABELS = {
    'pending': 'Ждёт подтверждения',
    'approved': 'Подтверждён',
    'rejected': 'Отклонён',
    'expired': 'Истёк',
    'cancelled': 'Отменён',
}

EVENT_LABELS = {
    'requested': 'Запросил код',
    'approved': 'Подтвердил запрос',
    'rejected': 'Отклонил запрос',
    'expired': 'Запрос истёк без ответа',
    'cancelled': 'Запрос отменён: доступ снят или аккаунт удалён',
    'code_sent': 'Код отправлен в Telegram',
    'code_resent': 'Новый код по кнопке «Код не подошёл»',
    'code_failed': 'Код не дошёл до Telegram',
    'account_added': 'Добавил аккаунт',
    'account_renamed': 'Переименовал аккаунт',
    'account_removed': 'Удалил аккаунт и его ключ',
    'access_granted': 'Выдал доступ',
    'access_revoked': 'Снял доступ',
}

# Кто действовал в событии: человек или сам раздел (срок вышел, бот отправил).
SYSTEM_EVENTS = frozenset({'expired', 'cancelled', 'code_sent', 'code_failed'})


# ─── Время ─────────────────────────────────────────────────────────────────────

def almaty(unix_time):
    """Секунды эпохи → наивное время Алматы (так время лежит в базе)."""
    return datetime.fromtimestamp(float(unix_time), timezone.utc).replace(tzinfo=None) + _ALMATY


def epoch(moment):
    """Наивное время Алматы из базы → секунды эпохи."""
    return (moment - _ALMATY - datetime(1970, 1, 1)).total_seconds()


def clock(unix_time, seconds=True):
    return almaty(unix_time).strftime('%H:%M:%S' if seconds else '%H:%M')


def iso(moment):
    """Время Алматы для экрана: строка с явным +05:00, чтобы браузер не принял её за свою."""
    if moment is None:
        return None
    return moment.strftime('%Y-%m-%dT%H:%M:%S') + '+05:00'


# ─── Поля аккаунта ─────────────────────────────────────────────────────────────

def clean_title(value):
    text = ' '.join(str(value or '').split())
    if not text:
        raise ValueError('Укажите название аккаунта')
    if len(text) > TITLE_MAX:
        raise ValueError('Название — не длиннее %d символов' % TITLE_MAX)
    return text


def clean_service(value):
    text = ' '.join(str(value or '').split())
    if len(text) > SERVICE_MAX:
        raise ValueError('Сервис — не длиннее %d символов' % SERVICE_MAX)
    return text or None


def suggested_title(entry):
    """Название по умолчанию для аккаунта из QR: «Google · ads@company.kz»."""
    issuer = (entry.get('issuer') or '').strip()
    name = (entry.get('name') or '').strip()
    if issuer and name and name.lower() != issuer.lower():
        return ('%s · %s' % (issuer, name))[:TITLE_MAX]
    return (issuer or name or 'Аккаунт')[:TITLE_MAX]


# ─── Тексты Telegram (parse_mode HTML) ─────────────────────────────────────────

def _esc(value):
    return html.escape(str(value or ''), quote=False)


def approval_text(*, requester_name, account_title, service, created_at, expires_at):
    service_part = ' · %s' % _esc(service) if service else ''
    return (
        '🔐 <b>Запрос кода 2FA</b>\n'
        '<b>%s</b> просит код для «%s»%s\n'
        'Запрошено в %s. Без ответа запрос сгорит в %s.'
    ) % (_esc(requester_name), _esc(account_title), service_part,
         clock(created_at, seconds=False), clock(expires_at, seconds=False))


def approval_closed_text(*, requester_name, account_title, status, decided_at, decided_by=None):
    """Отметка вместо кнопок. Кто решил — словами: сообщение получают все подтверждающие,
    включившие уведомления, и каждому надо видеть, что запрос закрыл другой."""
    by = ' — %s' % _esc(decided_by) if decided_by else ''
    mark = {
        'approved': '✅ Подтверждено в %s%s' % (clock(decided_at, seconds=False), by),
        'rejected': '❌ Отклонено в %s%s' % (clock(decided_at, seconds=False), by),
        'expired': '⏱ Сгорел в %s — ответа не было' % clock(decided_at, seconds=False),
        'cancelled': 'Отменён в %s: доступ снят или аккаунт удалён' % clock(decided_at, seconds=False),
    }.get(status, STATUS_LABELS.get(status, status))
    return (
        '🔐 <b>Запрос кода 2FA</b>\n'
        '%s — «%s»\n'
        '%s'
    ) % (_esc(requester_name), _esc(account_title), mark)


def approval_markup(request_id):
    return {'inline_keyboard': [[
        {'text': '✅ Подтвердить', 'callback_data': 'twofa:ok:%d' % int(request_id)},
        {'text': 'Отклонить', 'callback_data': 'twofa:no:%d' % int(request_id)},
    ]]}


def resend_markup(request_id):
    return {'inline_keyboard': [[
        {'text': 'Код не подошёл', 'callback_data': 'twofa:new:%d' % int(request_id)},
    ]]}


def code_text(*, account_title, issued, now):
    """Сообщение с кодом: цифры и время, после которого код недействителен (слова владельца)."""
    lines = [
        '🔐 Код для «%s»' % _esc(account_title),
        '',
        '<code>%s</code>' % _esc(group(issued['code'])),
        '',
        'Действует до <b>%s</b>' % clock(issued['valid_until']),
    ]
    if issued.get('is_next') and issued['valid_from'] > now:
        lines.append('Начнёт действовать в %s — через %d с' % (
            clock(issued['valid_from']), max(1, int(round(issued['valid_from'] - now)))))
    lines.append('Цифры сотрутся через минуту.')
    return '\n'.join(lines)


def code_hidden_text(*, account_title, resend_until):
    return (
        '🔐 Код для «%s» стёрт.\n'
        'Не подошёл? Новый код — кнопкой ниже до %s.'
    ) % (_esc(account_title), clock(resend_until, seconds=False))


def rejected_text(*, account_title):
    return '❌ Запрос кода для «%s» отклонён.' % _esc(account_title)


def expired_text(*, account_title):
    return (
        '⏱ Запрос кода для «%s» сгорел: за 10 минут его не подтвердили.\n'
        'Если код ещё нужен, запросите его заново в разделе «Коды 2FA».'
    ) % _esc(account_title)
