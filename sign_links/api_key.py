# -*- coding: utf-8 -*-
"""Ключ внешнего входа раздела «Ссылка на подписание». Чистая логика: ни Flask, ни базы.

Внешний сервис присылает список ИИН и получает ссылки (sign_links/external.py).
Сотрудником портала он не является: у него нет ни учётки, ни сессии, ни QR —
вместо них секретный ключ в заголовке запроса. Этот модуль отвечает на один
вопрос: «запрос пришёл от держателя ключа?» Пределы и содержимое — дело роута.

Что здесь решено и почему:

* **Ключ живёт в переменной окружения сервера и нигде больше.** Репозиторий
  публичный. В имени переменной есть «KEY», поэтому её точное значение само
  попадает под чистку логов (common/log_secrets).

* **Не настроено — закрыто.** Переменная пуста или задана с ошибкой — входа нет
  (роут отвечает 503), а не «пускаем всех». Короткий или однообразный ключ тоже
  ошибка настройки: ключ, который подбирается перебором, хуже отсутствующего —
  с ним вход выглядит защищённым.

* **Свой заголовок, а не Authorization.** `Authorization: Bearer` в портале
  читает вход сотрудников (JWT), а общий обработчик 401 пишет начало этого
  заголовка в лог. В адресе запроса ключ не принимается вовсе: адреса оседают
  в журналах прокси.

* **Сверка — за постоянное время и по sha256.** Обычное `==` отвечает тем
  быстрее, чем раньше расходятся строки, и по этой разнице ключ подбирают
  посимвольно; хеш заодно прячет длину ключа.

* **Несколько ключей через запятую — для смены без простоя**: добавили новый,
  переключили сервис, убрали старый. В журнал и в лог уходит не ключ, а его
  отпечаток — первые восемь знаков sha256: по нему видно, каким ключом пришли.
"""

import hashlib
import hmac
import logging
import os
import re

ENV_NAME = 'SIGN_LINKS_API_KEY'
HEADER = 'X-Api-Key'

# 32 знака — длина uuid без дефисов и token_hex(16): короче случайные ключи не
# выдают, а всё, что короче, человек набрал руками.
MIN_KEY_LENGTH = 32
# Потолок чистки логов (log_secrets._MAX_SECRET_LEN): длиннее значение она не
# подменяет, и такой ключ остался бы без последнего рубежа.
MAX_KEY_LENGTH = 200
# «aaaa…a» и «testtesttest…» проходят по длине, но ключом не являются. У
# случайного ключа из 32 знаков меньше восьми разных не бывает.
MIN_DISTINCT_CHARS = 8

FINGERPRINT_LENGTH = 8

_SEPARATORS_RE = re.compile(r'[\s,;]+')


class KeyConfigError(ValueError):
    """Переменная с ключами задана с ошибкой. Текст — без самого ключа."""


def _digest(key):
    return hashlib.sha256(key.encode('utf-8')).digest()


def fingerprint(key):
    """Отпечаток ключа для журнала и лога. Выводится из ключа, руками не задаётся."""
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:FINGERPRINT_LENGTH]


def _defect(key):
    """Чем ключ плох — словами, без его значения. None — годится."""
    # Заголовки ходят в latin-1: ключ с кириллицей до сервера не доедет вовсе.
    if not key.isascii() or not key.isprintable():
        return 'в ключе допустимы только печатные знаки ASCII (латиница, цифры, знаки)'
    if len(key) < MIN_KEY_LENGTH:
        return 'ключ короче %d знаков' % MIN_KEY_LENGTH
    if len(key) > MAX_KEY_LENGTH:
        return 'ключ длиннее %d знаков' % MAX_KEY_LENGTH
    if len(set(key)) < MIN_DISTINCT_CHARS:
        return 'ключ слишком однообразен — нужен случайный'
    return None


def parse(raw):
    """Настройка сервера → [(отпечаток, sha256 ключа)].

    Разделители — запятая, точка с запятой, пробел, перевод строки. Плохая
    запись — KeyConfigError на весь список, а не молчаливый пропуск: одна
    опечатка не должна незаметно оставить рабочим только старый ключ.
    """
    keys = []
    seen = set()
    for position, item in enumerate(filter(None, _SEPARATORS_RE.split(str(raw or ''))), 1):
        problem = _defect(item)
        if problem:
            raise KeyConfigError('ключ №%d: %s' % (position, problem))
        digest = _digest(item)
        if digest not in seen:
            seen.add(digest)
            keys.append((fingerprint(item), digest))
    return keys


# Разбор помним по сырому значению: sha256 на каждый запрос — пустяк, а вот
# ошибку настройки писать в лог на каждый запрос незачем.
_parsed = {'raw': None, 'keys': [], 'error': None}


def configured(environ=None):
    """(ключи, ошибка настройки). Пустой список без ошибки — переменная не задана."""
    global _parsed
    raw = ((os.environ if environ is None else environ).get(ENV_NAME) or '').strip()
    state = _parsed
    if state['raw'] != raw:
        try:
            state = {'raw': raw, 'keys': parse(raw), 'error': None}
        except KeyConfigError as error:
            state = {'raw': raw, 'keys': [], 'error': str(error)}
            # Кривая настройка закрывает вход целиком, а не открывает его.
            logging.error('sign_links: %s задан с ошибкой (%s) — внешний вход закрыт',
                          ENV_NAME, error)
        _parsed = state
    return state['keys'], state['error']


def match(presented, keys):
    """Отпечаток ключа, которым пришли, либо None.

    Перебираем ВСЕ ключи без раннего выхода: время ответа не должно зависеть
    от того, какой из них подошёл и подошёл ли.
    """
    value = str(presented or '').strip()
    if not value or not keys:
        return None
    try:
        candidate = _digest(value)
    except UnicodeError:
        return None
    found = None
    for mark, digest in keys:
        if hmac.compare_digest(candidate, digest):
            found = mark
    return found
