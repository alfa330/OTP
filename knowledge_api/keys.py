# -*- coding: utf-8 -*-
"""Ключ партнёра: выпуск, хеш, отпечаток. Чистая логика — ни Flask, ни базы.

* **Ключ случайный и с префиксом.** `otpk_` + 40 знаков token_urlsafe: по
  префиксу ключ узнаёт сканер секретов GitHub и чистка логов, а человек не
  спутает его с JWT сотрудника.

* **В базе — только sha256.** Сверка: хеш присланного значения ищется по
  индексу; сам ключ после выпуска нигде не хранится и не печатается.

* **Отпечаток** — первые восемь знаков хеша: по нему журнал и лог говорят,
  каким ключом пришли, не раскрывая ключа.

* **Заголовок — X-Api-Key, не Authorization.** Authorization: Bearer читает
  вход сотрудников (JWT), и его обработчик 401 пишет начало заголовка в лог.
"""

import hashlib
import secrets

HEADER = 'X-Api-Key'
PREFIX = 'otpk_'
# 30 байт → 40 знаков URL-safe base64. Вместе с префиксом — 45.
_RANDOM_BYTES = 30
MIN_LENGTH = 40
MAX_LENGTH = 200
MARK_LENGTH = 8


def generate():
    """Новый ключ. Выдаётся один раз — вызывающий обязан показать его сразу."""
    return PREFIX + secrets.token_urlsafe(_RANDOM_BYTES)


def digest(key):
    return hashlib.sha256(str(key).encode('utf-8')).hexdigest()


def mark(key):
    """Отпечаток для журнала и лога. Из ключа, руками не задаётся."""
    return digest(key)[:MARK_LENGTH]


def presented(value):
    """Значение заголовка → ключ для сверки, либо None, если это не ключ.

    Отсев до базы: пустое, не ASCII, слишком короткое или длинное значение
    в базу не ходит. Префикс не обязателен — так старый ключ можно заменить
    без простоя, даже если формат когда-нибудь сменится.
    """
    text = str(value or '').strip()
    if not text or len(text) < MIN_LENGTH or len(text) > MAX_LENGTH:
        return None
    if not text.isascii() or not text.isprintable() or any(ch.isspace() for ch in text):
        return None
    return text
