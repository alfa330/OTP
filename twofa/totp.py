"""Коды TOTP по RFC 6238 — тот же расчёт, что в Google Authenticator. Ни базы, ни сети.

Код — это HMAC-SHA1 от номера 30-секундного шага, сжатый до 6 цифр (RFC 4226, п. 5.3).
Телефон и сервер считают его каждый у себя из одного ключа и одних часов, поэтому цифры
совпадают. Параметры у Google Authenticator всегда одни: SHA1, 6 цифр, шаг 30 секунд;
другие сочетания otpauth.py отсекает ещё при разборе QR.
"""

import base64
import binascii
import hashlib
import hmac
import struct

STEP_SECONDS = 30
DIGITS = 6

# ТЗ п. 5: «Если до смены кода осталось меньше 10 секунд, бот присылает следующий код».
NEXT_CODE_THRESHOLD_SECONDS = 10

# Ключ короче 80 бит Google Authenticator не выдаёт (16 символов base32). Верхний предел —
# только от явной ошибки: обычно ключи 10–64 байта, но бывают и длиннее — у Tilda в экспорте
# Google Authenticator ключ длиннее 64 байт (27.10.2026, прежний предел 64 его отсекал).
# Длинный ключ HMAC сначала сжимает SHA1 (RFC 2104) — так же считает и приложение, поэтому
# коды совпадают.
MIN_SECRET_BYTES = 10
MAX_SECRET_BYTES = 512


def normalize_secret(text):
    """Ключ из base32-строки в байты.

    Сервисы показывают ключ по-разному: группами через пробел, строчными буквами, без
    знаков '=' в конце. Всё это — один и тот же ключ. ValueError — если это не base32
    или длина не похожа на ключ.
    """
    value = ''.join(str(text or '').split()).replace('-', '').upper().rstrip('=')
    if not value:
        raise ValueError('Ключ пустой')
    padded = value + '=' * (-len(value) % 8)
    try:
        secret = base64.b32decode(padded, casefold=False)
    except (binascii.Error, ValueError):
        raise ValueError('Ключ должен состоять из латинских букв A–Z и цифр 2–7') from None
    validate_secret(secret)
    return secret


def validate_secret(secret):
    if not isinstance(secret, (bytes, bytearray)):
        raise ValueError('Ключ не прочитан')
    if len(secret) < MIN_SECRET_BYTES:
        raise ValueError('Ключ слишком короткий — проверьте, что он скопирован целиком')
    if len(secret) > MAX_SECRET_BYTES:
        raise ValueError('Ключ слишком длинный — это не похоже на ключ 2FA')
    return bytes(secret)


def hotp(secret, counter, digits=DIGITS):
    """RFC 4226: код по номеру шага."""
    digest = hmac.new(bytes(secret), struct.pack('>Q', int(counter)), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def counter_at(unix_time, step=STEP_SECONDS):
    return int(unix_time // step)


def code_at(secret, unix_time, step=STEP_SECONDS, digits=DIGITS):
    return hotp(secret, counter_at(unix_time, step), digits)


def issue(secret, unix_time):
    """Код к выдаче сейчас — по правилу ТЗ п. 5.

    Осталось 10 секунд и больше — текущий код. Меньше — следующий: человек не успеет его
    ввести, пока текущий жив. Время — секунды эпохи (UTC), как у часов телефона.

    Возвращает {'code', 'counter', 'valid_from', 'valid_until', 'is_next'}; границы — тоже
    секунды эпохи. У следующего кода valid_from в будущем: сервис со строгой проверкой
    примет его не раньше этой секунды, и сообщение об этом говорит.
    """
    counter = counter_at(unix_time)
    left = (counter + 1) * STEP_SECONDS - unix_time
    is_next = left < NEXT_CODE_THRESHOLD_SECONDS
    if is_next:
        counter += 1
    return {
        'code': hotp(secret, counter),
        'counter': counter,
        'valid_from': counter * STEP_SECONDS,
        'valid_until': (counter + 1) * STEP_SECONDS,
        'is_next': is_next,
    }
