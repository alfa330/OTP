"""Шифрование ключей 2FA: AES-256-GCM, ключ шифрования — переменная окружения.

ТЗ п. 8: «Ключи хранятся только в зашифрованном виде (AES-256), ключ шифрования — отдельно
от базы, не в коде и не в репозитории». Поэтому:

  * не Fernet, которым шифрует портал (payments/cards.py): Fernet — это AES-128;
  * ключ — `TWOFA_SECRET_KEY` в настройках Render, 32 байта в base64. В базе его нет, и
    копия базы без него ключей не раскрывает. Нет переменной — раздел аккаунтов не
    принимает и кодов не выдаёт, а не шифрует чем-нибудь «временным»;
  * GCM проверяет целостность: подменённый в базе шифротекст не расшифруется вовсе, а не
    превратится в чужой ключ.

Сгенерировать ключ: python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
Сменить его нельзя без перешифровки: старые записи с новым ключом не откроются.

Формат в базе: 'v1:' + base64(nonce 12 байт + шифротекст с тегом).
"""

import base64
import binascii
import os

ENV_KEY = 'TWOFA_SECRET_KEY'

_PREFIX = 'v1:'
_NONCE_BYTES = 12
# Привязка шифротекста к назначению: тот же ключ, случайно взятый для другого раздела,
# не расшифрует наши записи и наоборот.
_AAD = b'icore-twofa-account-secret:v1'


class KeyMissing(RuntimeError):
    """Ключ шифрования не задан или негоден — раздел работать не может."""


def _load_key():
    raw = (os.getenv(ENV_KEY) or '').strip()
    if not raw:
        raise KeyMissing('Не задан ключ шифрования %s' % ENV_KEY)
    try:
        key = base64.b64decode(raw + '=' * (-len(raw) % 4), validate=False)
    except (binascii.Error, ValueError):
        raise KeyMissing('Ключ шифрования %s — не base64' % ENV_KEY) from None
    if len(key) != 32:
        raise KeyMissing('Ключ шифрования %s должен быть 32 байта (сейчас %d)' % (ENV_KEY, len(key)))
    return key


def key_status():
    """'ok' — можно работать; 'missing' — переменной нет; 'invalid' — она негодна."""
    if not (os.getenv(ENV_KEY) or '').strip():
        return 'missing'
    try:
        _load_key()
    except KeyMissing:
        return 'invalid'
    return 'ok'


def _aesgcm():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(_load_key())


def encrypt(secret):
    nonce = os.urandom(_NONCE_BYTES)
    sealed = _aesgcm().encrypt(nonce, bytes(secret), _AAD)
    return _PREFIX + base64.b64encode(nonce + sealed).decode('ascii')


def decrypt(token):
    from cryptography.exceptions import InvalidTag

    text = str(token or '')
    if not text.startswith(_PREFIX):
        raise ValueError('Неизвестный формат шифротекста')
    blob = base64.b64decode(text[len(_PREFIX):])
    try:
        return _aesgcm().decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], _AAD)
    except InvalidTag:
        raise ValueError('Ключ аккаунта не расшифровывается: сменили ключ шифрования?') from None
