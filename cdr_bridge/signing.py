# -*- coding: utf-8 -*-
"""Подпись запросов моста закрытым ключом Ed25519.

Половина протокола, живущая на шлюзе. Вторая — проверка — в `cdr/agent_auth.py`
на портале, и канонический вид подписываемой строки берётся ОТТУДА, а не
переписывается здесь: у двух копий было бы где разойтись.

Ключ
----
Закрытый ключ — 32 байта в base64. Живёт в файле (`CDR_AGENT_KEY_FILE`, права
600) или, на худой конец, в переменной окружения (`CDR_AGENT_PRIVATE_KEY`).
Файл предпочтительнее: окружение процесса видно в /proc и в docker inspect.

Открытый ключ и его идентификатор мост печатает при генерации — их и надо
занести на портал (`CDR_AGENT_KEYS`). Идентификатор выводится из ключа, и
если на портале он вдруг другой — значит ключ не тот, а не «опечатка в id».

Ротация
-------
    1. на шлюзе: python -m cdr_bridge.agent --keygen /etc/otp-gateway/agent.key.new
    2. на портале: добавить НОВЫЙ открытый ключ в CDR_AGENT_KEYS через запятую
    3. на шлюзе: подменить файл ключа, перезапустить мост
    4. убедиться по состоянию моста, что портал видит новый идентификатор
    5. на портале: убрать старый ключ

Ни одного шага, на котором мост молчит.
"""

import base64
import binascii
import logging
import os
import secrets
import stat
import sys
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import agent_auth  # noqa: E402

log = logging.getLogger('cdr_bridge.signing')

_RAW = serialization.Encoding.Raw
_RAW_PRIVATE = serialization.PrivateFormat.Raw
_RAW_PUBLIC = serialization.PublicFormat.Raw


def _b64(data):
    return base64.b64encode(data).decode('ascii')


def generate():
    """Новая пара. Возвращает (закрытый base64, открытый base64, идентификатор)."""
    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(_RAW, _RAW_PRIVATE, serialization.NoEncryption())
    public = private.public_key().public_bytes(_RAW, _RAW_PUBLIC)
    return _b64(seed), _b64(public), agent_auth.key_id(public)


def load_private_key(text):
    """base64 → закрытый ключ. Ошибка — ValueError с внятным текстом."""
    try:
        seed = base64.b64decode(str(text or '').strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('закрытый ключ моста должен быть base64')
    if len(seed) != agent_auth.PUBLIC_KEY_BYTES:
        raise ValueError('закрытый ключ Ed25519 — это 32 байта, получено %d' % len(seed))
    return Ed25519PrivateKey.from_private_bytes(seed)


def read_key_file(path):
    """Содержимое файла ключа. Слишком широкие права — предупреждение в журнал,
    не отказ: мост важнее, а чинить права надо человеку."""
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError as exc:
        raise ValueError('файл ключа %s не читается: %s' % (path, exc))
    if os.name == 'posix' and mode & 0o077:
        log.warning('Файл ключа %s доступен не только владельцу (права %o) — '
                    'поставьте 600', path, mode)
    with open(path, encoding='ascii') as handle:
        return handle.read().strip()


def write_key_file(path, private_b64):
    """Записать ключ файлом с правами 600 — атомарно, чтобы полуфайл не остался."""
    tmp = path + '.tmp'
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    with os.fdopen(os.open(tmp, flags, 0o600), 'w', encoding='ascii') as handle:
        handle.write(private_b64 + '\n')
    os.replace(tmp, path)


class Signer:
    """Подписывает запросы моста. Один на процесс."""

    def __init__(self, private_key):
        self._private = private_key
        public = private_key.public_key().public_bytes(_RAW, _RAW_PUBLIC)
        self.public_b64 = _b64(public)
        self.key_id = agent_auth.key_id(public)

    def headers(self, method, path, body, now=None, nonce=None):
        """Заголовки подписи для одного запроса. Тело — байты, ровно те, что
        уйдут в сеть: подписывается их хеш."""
        stamp = int(time.time() if now is None else now)
        nonce = nonce or secrets.token_hex(16)
        message = agent_auth.canonical(method, path, stamp, nonce, body)
        signature = self._private.sign(message)
        return {
            agent_auth.HEADER_KEY: self.key_id,
            agent_auth.HEADER_TIME: str(stamp),
            agent_auth.HEADER_NONCE: nonce,
            agent_auth.HEADER_SIGNATURE: _b64(signature),
        }
