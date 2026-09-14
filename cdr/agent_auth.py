# -*- coding: utf-8 -*-
"""Подпись запросов моста: Ed25519 вместо общего токена.

Зачем менять общий токен
------------------------
Токен — симметричный секрет: он одинаков на портале (переменная окружения
Render) и на машине-шлюзе. Утечка любой из двух сторон даёт полный доступ:
поддельный мост может присылать выдуманные касания, забирать сутки из очереди
и тем самым молча портить или глушить данные раздела. Утечки с портала не
экзотика — переменные видны в дашборде, попадают в логи и снимки конфигурации.

С подписью портал хранит ТОЛЬКО открытый ключ. Украсть с портала нечего:
открытым ключом ничего не подпишешь. Закрытый ключ лежит на шлюзе, и это
единственное место, которое надо защищать.

Что подписывается
-----------------
Каждый запрос — целиком: метод, путь, время, одноразовый номер и хеш тела.

    OTP-AGENT-v1 \\n POST \\n /api/cdr/agent/day \\n 1757600000 \\n <nonce> \\n <sha256(тело)>

Время закрывает старые запросы (окно ±5 минут — часы на шлюзе держит NTP,
а больший разбег означал бы, что сломано что-то другое). Одноразовый номер
закрывает повтор: тот же запрос второй раз не пройдёт, даже внутри окна.
Хеш тела закрывает подмену содержимого — касаний или отказа по суткам.

Идентификатор ключа выводится из самого открытого ключа (первые 16 знаков
sha256), а не задаётся руками: подделать его или спутать два ключа нельзя.
На портале может быть несколько ключей одновременно — так ротация проходит
без единой секунды простоя: добавили новый, переключили мост, убрали старый.

Что здесь НЕ проверяется
------------------------
Доступ по путям и содержимое тела — это дело роутов. Этот модуль отвечает
на один вопрос: «запрос действительно от держателя одного из наших ключей и
не является повтором». Ничего больше.

Один и тот же модуль импортируют обе стороны — портал (проверка) и мост
(подпись, через cdr_bridge/signing.py). Копии протокола нет намеренно: двум
копиям было бы негде не разойтись.
"""

import base64
import binascii
import hashlib
import logging
import re
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

log = logging.getLogger(__name__)

SCHEME = 'OTP-AGENT-v1'

HEADER_KEY = 'X-Agent-Key'
HEADER_TIME = 'X-Agent-Time'
HEADER_NONCE = 'X-Agent-Nonce'
HEADER_SIGNATURE = 'X-Agent-Signature'

# Допустимый разбег часов. Пять минут — с запасом даже для машины без NTP на
# сутки; больше — уже не разбег часов, а признак чужой или очень старой записи.
MAX_SKEW_SECONDS = 300

# Одноразовые номера помним дольше окна: запрос с временем на краю окна мог
# прийти ещё раз через минуту, и он обязан быть отвергнут.
NONCE_TTL_SECONDS = 2 * MAX_SKEW_SECONDS

# Потолок памяти под номера. Мост шлёт единицы запросов в минуту; десятки
# тысяч — это уже не мост, а кто-то, кто пытается забить нам память.
NONCE_CACHE_LIMIT = 50000

PUBLIC_KEY_BYTES = 32
SIGNATURE_BYTES = 64

_NONCE_RE = re.compile(r'^[0-9a-f]{16,64}$')


def key_id(public_bytes):
    """Идентификатор ключа — из самого ключа, руками не задаётся."""
    return hashlib.sha256(public_bytes).hexdigest()[:16]


def body_digest(body):
    return hashlib.sha256(body or b'').hexdigest()


def canonical(method, path, time_value, nonce, body):
    """То, что подписывается. Единственное определение — здесь."""
    return '\n'.join([
        SCHEME,
        str(method).upper(),
        str(path),
        str(time_value),
        str(nonce),
        body_digest(body),
    ]).encode('utf-8')


def decode_public_key(text):
    """base64 → открытый ключ. ValueError с человеческим текстом, не трейсбек."""
    try:
        raw = base64.b64decode(str(text).strip(), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('открытый ключ должен быть base64')
    if len(raw) != PUBLIC_KEY_BYTES:
        raise ValueError('открытый ключ Ed25519 — это %d байт, получено %d'
                         % (PUBLIC_KEY_BYTES, len(raw)))
    return raw, Ed25519PublicKey.from_public_bytes(raw)


def parse_public_keys(raw_setting):
    """Настройка портала → {идентификатор: ключ}.

    Разделители — запятая, пробел, перевод строки: как удобнее записать в
    дашборде. Плохая запись — ValueError на весь список, а не молчаливый
    пропуск: одна опечатка не должна незаметно оставить портал без ключа,
    который считался рабочим.
    """
    keys = {}
    for item in re.split(r'[\s,;]+', str(raw_setting or '')):
        if not item:
            continue
        raw, public = decode_public_key(item)
        keys[key_id(raw)] = public
    return keys


class NonceCache:
    """Одноразовые номера, виденные за последние NONCE_TTL_SECONDS.

    Живёт в памяти процесса. Портал поднимается одним процессом waitress, так
    что второй копии, не знающей о номере, нет. Перезапуск процесса кэш
    обнуляет — и это принято: повтор возможен только для запроса, перехваченного
    в открытом виде, а канал зашифрован TLS. Подпись здесь второй рубеж, а не
    единственный.
    """

    def __init__(self, ttl=NONCE_TTL_SECONDS, limit=NONCE_CACHE_LIMIT):
        self._ttl = ttl
        self._limit = limit
        self._seen = {}
        self._lock = threading.Lock()

    def seen_or_remember(self, kid, nonce, now):
        """True — номер уже был (повтор). Иначе запоминает и отдаёт False."""
        item = (kid, nonce)
        with self._lock:
            self._purge(now)
            if item in self._seen:
                return True
            if len(self._seen) >= self._limit:
                # Переполнение — либо атака, либо ошибка. В обоих случаях
                # честнее отвергать новые запросы, чем забывать старые номера.
                log.warning('Касания: кэш одноразовых номеров переполнен')
                return True
            self._seen[item] = now + self._ttl
            return False

    def _purge(self, now):
        expired = [k for k, until in self._seen.items() if until <= now]
        for k in expired:
            del self._seen[k]

    def __len__(self):
        with self._lock:
            return len(self._seen)


_default_nonces = NonceCache()


def verify(headers, method, path, body, keys, now=None, nonces=None):
    """Проверка подписанного запроса.

    Возвращает (идентификатор ключа, None) при успехе и (None, причина) при
    отказе. Причина — для журнала портала, наружу она не уходит: злоумышленнику
    незачем знать, на чём именно он споткнулся.
    """
    now = time.time() if now is None else now
    nonces = _default_nonces if nonces is None else nonces

    kid = (headers.get(HEADER_KEY) or '').strip()
    time_text = (headers.get(HEADER_TIME) or '').strip()
    nonce = (headers.get(HEADER_NONCE) or '').strip()
    signature_text = (headers.get(HEADER_SIGNATURE) or '').strip()

    if not (kid and time_text and nonce and signature_text):
        return None, 'нет заголовков подписи'
    public = keys.get(kid)
    if public is None:
        return None, 'неизвестный ключ %s' % kid[:16]
    try:
        stamp = int(time_text)
    except ValueError:
        return None, 'время не число'
    if abs(now - stamp) > MAX_SKEW_SECONDS:
        return None, 'время вне окна (%+d с)' % int(stamp - now)
    if not _NONCE_RE.match(nonce):
        return None, 'одноразовый номер не той формы'
    try:
        signature = base64.b64decode(signature_text, validate=True)
    except (binascii.Error, ValueError):
        return None, 'подпись не base64'
    if len(signature) != SIGNATURE_BYTES:
        return None, 'подпись не той длины'
    try:
        public.verify(signature, canonical(method, path, stamp, nonce, body))
    except InvalidSignature:
        return None, 'подпись не сходится'
    # Номер запоминаем ПОСЛЕ проверки подписи: иначе кто угодно без ключа мог бы
    # «сжечь» номер настоящего моста, просто отправив его первым.
    if nonces.seen_or_remember(kid, nonce, now):
        return None, 'повтор запроса'
    return kid, None
