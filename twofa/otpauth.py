"""Разбор того, чем добавляют аккаунт: QR сервиса, QR экспорта Google Authenticator, ключ.

Три вида входа, и экран не обязан их различать — он отдаёт строку как есть:

    otpauth://totp/Google:ads@company.kz?secret=JBSW…&issuer=Google
        QR, который сервис показывает при подключении 2FA. Один аккаунт.

    otpauth-migration://offline?data=CjEKCk…
        QR «Перенос аккаунтов → Экспорт» в Google Authenticator. Внутри protobuf со
        списком аккаунтов, при многих аккаунтах экспорт разбит на несколько QR (batch).
        Это единственный способ достать ключи уже настроенных аккаунтов: само приложение
        их не показывает.

    JBSW Y3DP EHPK 3PXP
        Ключ, который сервис показывает текстом под QR («не можете отсканировать?»).

Принимаем только то, что выдаёт Google Authenticator по умолчанию и требует ТЗ п. 5: TOTP,
SHA1, 6 цифр, шаг 30 секунд. Остальное (счётчик HOTP, 8 цифр, SHA256) возвращается с
supported=False и причиной — такой аккаунт бот посчитал бы неверно, и лучше сказать об
этом при добавлении, чем молча выдавать неподходящие коды.

Ни базы, ни сети. Ключи в ответе — байты; наружу (в API) они не уходят никогда.
"""

import base64
import binascii
from urllib.parse import unquote

from . import totp

MAX_TEXT_LENGTH = 8000

_ALGORITHMS = {0: 'SHA1', 1: 'SHA1', 2: 'SHA256', 3: 'SHA512', 4: 'MD5'}
_DIGITS = {0: 6, 1: 6, 2: 8}
_TYPES = {0: 'totp', 1: 'hotp', 2: 'totp'}


class ParseError(ValueError):
    """Строку нельзя разобрать как QR аккаунта или ключ."""


def _entry(name, issuer, secret, algorithm='SHA1', digits=6, period=30, kind='totp'):
    reason = None
    if kind != 'totp':
        reason = 'Счётный код (HOTP): бот выдаёт только коды по времени'
    elif str(algorithm).upper() != 'SHA1':
        reason = 'Алгоритм %s: Google Authenticator по умолчанию считает SHA1' % algorithm
    elif int(digits) != totp.DIGITS:
        reason = 'Код из %s цифр: бот выдаёт коды из 6 цифр' % digits
    elif int(period) != totp.STEP_SECONDS:
        reason = 'Код меняется раз в %s с: бот считает шаг 30 с' % period
    try:
        totp.validate_secret(secret)
    except ValueError as exc:
        reason = reason or str(exc)
    return {
        'name': (name or '').strip()[:200],
        'issuer': (issuer or '').strip()[:80],
        'secret': bytes(secret or b''),
        'supported': reason is None,
        'reason': reason,
    }


def _query(raw):
    """Параметры адреса без превращения '+' в пробел.

    parse_qs декодирует '+' как пробел, а в base64 экспорта это значащий символ: часть
    сканеров отдаёт его как есть, без %2B. Поэтому делим вручную и снимаем только %XX.
    """
    params = {}
    for part in (raw or '').split('&'):
        if not part:
            continue
        key, _, value = part.partition('=')
        params.setdefault(unquote(key).lower(), unquote(value))
    return params


def _parse_otpauth(text):
    rest = text[len('otpauth://'):]
    kind, _, rest = rest.partition('/')
    label, _, raw_query = rest.partition('?')
    params = _query(raw_query)
    secret_text = params.get('secret')
    if not secret_text:
        raise ParseError('В QR нет ключа (параметр secret)')
    try:
        secret = totp.normalize_secret(secret_text)
    except ValueError as exc:
        raise ParseError(str(exc)) from None
    label = unquote(label)
    issuer = params.get('issuer') or ''
    name = label
    if ':' in label:
        label_issuer, _, name = label.partition(':')
        issuer = issuer or label_issuer
    try:
        digits = int(params.get('digits') or totp.DIGITS)
        period = int(params.get('period') or totp.STEP_SECONDS)
    except ValueError:
        raise ParseError('В QR неверные параметры кода') from None
    return {
        'kind': 'single',
        'batch': None,
        'entries': [_entry(name, issuer, secret, params.get('algorithm') or 'SHA1', digits, period,
                           kind.lower())],
    }


# ─── protobuf экспорта Google Authenticator ──────────────────────────────────────
#
# message MigrationPayload {
#   repeated OtpParameters otp_parameters = 1;
#   int32 version = 2; int32 batch_size = 3; int32 batch_index = 4; int32 batch_id = 5;
# }
# message OtpParameters {
#   bytes secret = 1; string name = 2; string issuer = 3;
#   Algorithm algorithm = 4; DigitCount digits = 5; OtpType type = 6; int64 counter = 7;
# }
#
# Библиотека protobuf ради двух сообщений не нужна: формат на проводе — это пары
# «номер поля + тип» и значение; нам нужны только varint (0) и длина+байты (2). Поля, которых
# здесь нет (новые версии приложения дописывают свои), пропускаются по типу.


def _varint(data, pos):
    result = 0
    shift = 0
    while True:
        if pos >= len(data) or shift > 63:
            raise ParseError('QR экспорта повреждён')
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _fields(data):
    pos = 0
    while pos < len(data):
        key, pos = _varint(data, pos)
        number, wire = key >> 3, key & 0x07
        if wire == 0:
            value, pos = _varint(data, pos)
        elif wire == 2:
            length, pos = _varint(data, pos)
            if pos + length > len(data):
                raise ParseError('QR экспорта повреждён')
            value = data[pos:pos + length]
            pos += length
        elif wire == 1:
            value, pos = data[pos:pos + 8], pos + 8
        elif wire == 5:
            value, pos = data[pos:pos + 4], pos + 4
        else:
            raise ParseError('QR экспорта повреждён')
        if pos > len(data):
            raise ParseError('QR экспорта повреждён')
        yield number, value


def _text(value):
    try:
        return bytes(value).decode('utf-8')
    except UnicodeDecodeError:
        return bytes(value).decode('utf-8', errors='replace')


def _parse_otp_parameters(data):
    fields = {'secret': b'', 'name': '', 'issuer': '', 'algorithm': 0, 'digits': 0, 'type': 0}
    for number, value in _fields(data):
        if number == 1 and isinstance(value, (bytes, bytearray)):
            fields['secret'] = bytes(value)
        elif number == 2 and isinstance(value, (bytes, bytearray)):
            fields['name'] = _text(value)
        elif number == 3 and isinstance(value, (bytes, bytearray)):
            fields['issuer'] = _text(value)
        elif number == 4 and isinstance(value, int):
            fields['algorithm'] = value
        elif number == 5 and isinstance(value, int):
            fields['digits'] = value
        elif number == 6 and isinstance(value, int):
            fields['type'] = value
    return _entry(
        fields['name'], fields['issuer'], fields['secret'],
        _ALGORITHMS.get(fields['algorithm'], 'unknown'),
        _DIGITS.get(fields['digits'], 0),
        totp.STEP_SECONDS,
        _TYPES.get(fields['type'], 'unknown'),
    )


def _parse_migration(text):
    _, _, raw_query = text.partition('?')
    data_text = _query(raw_query).get('data') or ''
    if not data_text:
        raise ParseError('В QR экспорта нет данных')
    compact = ''.join(data_text.split()).replace('-', '+').replace('_', '/')
    try:
        payload = base64.b64decode(compact + '=' * (-len(compact) % 4), validate=True)
    except (binascii.Error, ValueError):
        raise ParseError('QR экспорта повреждён') from None
    entries = []
    batch = {'size': 1, 'index': 0}
    for number, value in _fields(payload):
        if number == 1 and isinstance(value, (bytes, bytearray)):
            entries.append(_parse_otp_parameters(bytes(value)))
        elif number == 3 and isinstance(value, int):
            batch['size'] = value
        elif number == 4 and isinstance(value, int):
            batch['index'] = value
    if not entries:
        raise ParseError('В QR экспорта нет аккаунтов')
    return {'kind': 'migration', 'batch': batch, 'entries': entries}


def parse(text):
    """Строка из QR или поля «Ключ» → {'kind', 'batch', 'entries': [...]}.

    kind: 'single' — один аккаунт (QR сервиса или ключ текстом), 'migration' — экспорт
    Google Authenticator; batch — {'size', 'index'} у экспорта: «QR 1 из 3».
    """
    value = str(text or '').strip()
    if not value:
        raise ParseError('Пустая строка')
    if len(value) > MAX_TEXT_LENGTH:
        raise ParseError('Слишком длинная строка — это не QR аккаунта')
    lowered = value.lower()
    if lowered.startswith('otpauth-migration://'):
        return _parse_migration(value)
    if lowered.startswith('otpauth://'):
        return _parse_otpauth(value)
    if '://' in value:
        raise ParseError('Это QR не от аккаунта: в нём ссылка, а не ключ 2FA')
    try:
        secret = totp.normalize_secret(value)
    except ValueError as exc:
        raise ParseError(str(exc)) from None
    return {'kind': 'single', 'batch': None, 'entries': [_entry('', '', secret)]}


def public(entry):
    """Строка разбора для ответа API — без ключа."""
    return {key: entry[key] for key in ('name', 'issuer', 'supported', 'reason')}
