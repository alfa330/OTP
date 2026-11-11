"""Правила окна Syntony. Чистая логика: ни Flask, ни базы, ни сети.

Здесь всё, что по ТЗ Syntony должно быть верно «при любом запросе»:

* браузер присылает только scope, phones и theme — оператора (id, ФИО, почту) сервер
  подставляет сам, поле canWrite не передаётся никогда;
* ФИО — без переводов строк и управляющих символов, не длиннее 100 знаков (иначе 400);
* номеров водителя — не больше десяти;
* ошибка любого происхождения уходит браузеру в одном виде:
  {"error": {"code": ..., "message": ...}} — экран разбирает её по code.
"""

import re

SCOPE_INBOX = 'inbox'
SCOPE_PHONE = 'phone'
SCOPE_PHONES = 'phones'
SCOPE_LEADS = 'leads'
SCOPES = (SCOPE_INBOX, SCOPE_PHONE, SCOPE_PHONES, SCOPE_LEADS)
PHONE_SCOPES = frozenset({SCOPE_PHONE, SCOPE_PHONES})

THEME_LIGHT = 'light'
THEME_DARK = 'dark'
# Портал на русском; язык окна браузер не выбирает.
LOCALE = 'ru'

MAX_PHONES = 10
_PHONE_MAX_LENGTH = 32
_PHONE_MIN_DIGITS = 7
_PHONE_MAX_DIGITS = 15
_PHONE_CHARS = re.compile(r'^[0-9+()\-\s]+$')

NAME_MAX_LENGTH = 100
USER_ID_MAX_LENGTH = 128
EMAIL_MAX_LENGTH = 255
_EMAIL = re.compile(r'^[^\s@<>()\[\],;:"\\]+@[^\s@<>()\[\],;:"\\]+\.[^\s@<>()\[\],;:"\\.]+$')

# ── Коды ошибок ───────────────────────────────────────────────────────────────
# Коды Syntony (ТЗ, раздел 7). Незнакомый код — не ошибка разбора: экран покажет
# общее сообщение, а сервер запишет код в журнал (routes.py).
SYNTONY_ERROR_CODES = frozenset({
    'invalid_request',
    'invalid_key', 'key_revoked', 'key_expired',
    'insufficient_scope',
    'connection_inactive',
    'operator_not_linked',
    'transport_not_connected',
    'rate_limited',
    'operator_lookup_unavailable',
    'embed_disabled',
})
CODE_INVALID_REQUEST = 'invalid_request'
CODE_NOT_LINKED = 'operator_not_linked'

# Наши коды — то, что решается до запроса к Syntony. Приставка icore_, чтобы код
# Syntony с тем же смыслом когда-нибудь не совпал с нашим по имени.
CODE_NO_ACCESS = 'icore_no_access'            # человека нет в списке доступа
CODE_QR_REQUIRED = 'icore_qr_required'        # сессия оператора не подтверждена QR
CODE_NOT_CONFIGURED = 'icore_not_configured'  # ключ Syntony не задан на сервере
CODE_UNREACHABLE = 'icore_unreachable'        # Syntony не ответила или ответила не JSON
ICORE_ERROR_CODES = frozenset({
    CODE_NO_ACCESS, CODE_QR_REQUIRED, CODE_NOT_CONFIGURED, CODE_UNREACHABLE,
})

# ── Связь оператора с сотрудником Syntony ─────────────────────────────────────
LINK_UNKNOWN = 'unknown'        # ещё не спрашивали
LINK_LINKED = 'linked'          # Syntony выдала ссылку
LINK_NOT_LINKED = 'not_linked'  # 409 operator_not_linked: записан у Syntony, ждёт связи
LINK_STATES = (LINK_UNKNOWN, LINK_LINKED, LINK_NOT_LINKED)


def error_payload(code, message, **details):
    """Ошибка в том же виде, что у Syntony: экран разбирает её одним кодом."""
    error = {'code': str(code), 'message': str(message)}
    if details:
        error['details'] = details
    return {'error': error}


def error_code(body):
    """Код ошибки из ответа Syntony (или нашего) — '' если это не ошибка."""
    if not isinstance(body, dict):
        return ''
    error = body.get('error')
    if not isinstance(error, dict):
        return ''
    code = error.get('code')
    return code.strip() if isinstance(code, str) else ''


def _phone_digits(value):
    return ''.join(ch for ch in value if ch.isdigit())


def clean_phones(raw):
    """Номера водителя из запроса браузера: строки «в любом виде», без повторов, до десяти.

    Повтор — тот же номер в другой записи (+7 701… и 8701…): сверяем по последним
    десяти цифрам. Мусор (не строка, буквы, слишком коротко) молча отбрасывается —
    пригоден ли оставшийся номер, решает Syntony.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    phones, seen = [], set()
    for item in raw:
        if not isinstance(item, str):
            continue
        value = ' '.join(item.split())
        if not value or len(value) > _PHONE_MAX_LENGTH or not _PHONE_CHARS.match(value):
            continue
        digits = _phone_digits(value)
        if not _PHONE_MIN_DIGITS <= len(digits) <= _PHONE_MAX_DIGITS:
            continue
        key = digits[-10:]
        if key in seen:
            continue
        seen.add(key)
        phones.append(value)
        if len(phones) == MAX_PHONES:
            break
    return phones


def clean_window_request(payload):
    """Запрос браузера «дай окно» → ({'scope', 'phones', 'theme'}, None) или (None, ошибка).

    Читаются ТОЛЬКО scope, phones и theme. Всё остальное в теле (partnerUserId, почта,
    canWrite — что бы ни прислала страница) не используется вовсе: оператора сервер
    берёт из сессии.
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return None, error_payload(CODE_INVALID_REQUEST, 'Неизвестное окно')
    scope = payload.get('scope', SCOPE_INBOX)
    if not isinstance(scope, str) or scope not in SCOPES:
        return None, error_payload(CODE_INVALID_REQUEST, 'Неизвестное окно')
    phones = []
    if scope in PHONE_SCOPES:
        phones = clean_phones(payload.get('phones'))
        if not phones:
            return None, error_payload(CODE_INVALID_REQUEST, 'Не указан номер водителя')
        # Один номер — «phone», несколько — «phones»: так читает Syntony, а странице
        # незачем об этом помнить.
        scope = SCOPE_PHONE if len(phones) == 1 else SCOPE_PHONES
    theme = THEME_DARK if payload.get('theme') == THEME_DARK else THEME_LIGHT
    return {'scope': scope, 'phones': phones, 'theme': theme}, None


def registration_window():
    """Окно, которым оператора записывают у Syntony (ТЗ, задача 6): «Чаты», ссылку не открываем."""
    return {'scope': SCOPE_INBOX, 'phones': [], 'theme': THEME_LIGHT}


def clean_operator_name(value):
    """ФИО для Syntony: одна строка без управляющих символов, до 100 знаков.

    Управляющий символ — всё, что str.isprintable считает непечатным (переводы строк,
    табуляция, нулевая ширина): Syntony на таком имени отвечает 400, и оператор
    остался бы без окна из-за лишнего знака в карточке.
    """
    text = ''.join(ch if ch.isprintable() else ' ' for ch in str(value or ''))
    return ' '.join(text.split())[:NAME_MAX_LENGTH].strip()


def clean_email(value):
    """Почта оператора в одном виде (без пробелов по краям, строчными) или ''.

    Пустая строка — «почты нет или это не адрес»: по ней Syntony никого не найдёт,
    и отправлять такое вместо почты незачем.
    """
    text = str(value or '').strip().lower()
    if not text or len(text) > EMAIL_MAX_LENGTH or not _EMAIL.match(text):
        return ''
    return text


def build_request_body(window, *, user_id, name, email):
    """Тело запроса к Syntony. Оператор — из аргументов сервера, canWrite нет вовсе."""
    partner_user_id = str(int(user_id))
    if len(partner_user_id) > USER_ID_MAX_LENGTH:
        raise ValueError('partnerUserId длиннее допустимого')
    body = {'scope': window['scope']}
    if window['scope'] in PHONE_SCOPES:
        body['phones'] = list(window['phones'])
    body['partnerUserId'] = partner_user_id
    operator_name = clean_operator_name(name)
    if operator_name:
        body['partnerUserName'] = operator_name
    operator_email = clean_email(email)
    if operator_email:
        body['partnerUserEmail'] = operator_email
    body['theme'] = THEME_DARK if window.get('theme') == THEME_DARK else THEME_LIGHT
    body['locale'] = LOCALE
    return body


def link_state_of(status, body):
    """Что ответ Syntony говорит о связи оператора: linked, not_linked или None.

    None — ответ не про связь (ключ, пауза подключения, сбой, плохой запрос): по нему
    сохранённое состояние не меняют.
    """
    if status == 200 and isinstance(body, dict) and isinstance(body.get('url'), str) and body['url']:
        return LINK_LINKED
    if error_code(body) == CODE_NOT_LINKED:
        return LINK_NOT_LINKED
    return None
