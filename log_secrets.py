# -*- coding: utf-8 -*-
"""Последний рубеж: секрет, который собрались напечатать, в лог не попадает.

Поставлено после инцидента 22.08.2026. Ключ Gemini передавался query-параметром
'?key=...', httpx пишет полный URL на уровне INFO — и ключ лежал в логах Render
открытым текстом с 20.08, пока сканер GitHub не отдал его Google. Сами вызовы
переведены на заголовки, но одной правкой мест такую утечку не закрыть: завтра
появится четвёртое.

ПОЧЕМУ ФОРМАТТЕР, А НЕ ФИЛЬТР. Фильтр видит только message. Traceback logging
собирает ОТДЕЛЬНО, из exc_info, уже после фильтров — а в проекте ~337 вызовов
logging.exception и ~239 с exc_info=True, и текст исключения requests при любом
отказе соединения содержит полный адрес: «Max retries exceeded with url:
/bot<ТОКЕН>/sendMessage». Форматтер работает с готовой строкой, поэтому
покрывает и сообщение, и traceback.

ЧТО РЕЖЕМ. Три источника правил, от точного к общему:
  1. Точные значения переменных окружения, похожих на секрет. Это единственное,
     что покрывает НЕПРЕДУГАДАННЫЕ форматы — у проекта два десятка сервисов.
  2. Форматы известных ключей (Google, Anthropic, Groq, Render, Telegram, JWT).
  3. Общие места, где секрет лежит по своей природе: query-параметры, пароль в
     строке подключения, заголовки авторизации.

Адрес и текст запроса остаются читаемыми: подменяется только само значение,
иначе фильтр лечит утечку ценой пригодности логов.
"""
import logging
import os
import re

HIDDEN = '<СКРЫТО>'

# Имена переменных, значения которых считаем секретом. Прочие (URL, id, регионы)
# не трогаем: подмена безобидного значения делает лог нечитаемым без пользы.
_SECRET_NAME_RE = re.compile(
    r'KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL|DATABASE_URL|DSN|AUTH', re.I)

# Слишком короткое значение подменять опасно (совпадёт со словом в тексте),
# слишком длинное смысла не имеет — это JSON сервис-аккаунта целиком.
_MIN_SECRET_LEN = 12
_MAX_SECRET_LEN = 200

_RULES = (
    # Значение query-параметра. Имя параметра сохраняем — по нему видно, что
    # именно ушло, и запрос остаётся узнаваемым.
    (re.compile(r'([?&](?:key|api_key|apikey|access_token|refresh_token|token|'
                r'password|passwd|secret|sig|signature|auth)=)[^&\s\'"<>]+', re.I),
     r'\1' + HIDDEN),
    # Токен Telegram лежит в ПУТИ адреса, а не в параметре: /bot<цифры>:<хвост>.
    (re.compile(r'(/bot)\d{6,}:[A-Za-z0-9_\-]{20,}'), r'\1' + HIDDEN),
    # Он же сам по себе, без адреса, — например в тексте исключения aiogram.
    (re.compile(r'\b\d{8,12}:AA[A-Za-z0-9_\-]{30,}'), HIDDEN),
    (re.compile(r'\bAIza[0-9A-Za-z_\-]{10,}'), HIDDEN),          # Google
    (re.compile(r'\bsk-[A-Za-z0-9\-_]{10,}'), HIDDEN),           # Anthropic, OpenAI
    (re.compile(r'\bgsk_[A-Za-z0-9]{20,}'), HIDDEN),             # Groq
    (re.compile(r'\brnd_[A-Za-z0-9]{20,}'), HIDDEN),             # Render
    (re.compile(r'\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+'), HIDDEN),  # JWT
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----',
                re.S), HIDDEN),
    # Пароль в строке подключения: postgres://user:ПАРОЛЬ@host.
    (re.compile(r'((?:[a-z][a-z0-9+.\-]*)://[^\s:/@]+:)[^\s@/]+(@)'), r'\1' + HIDDEN + r'\2'),
    # Заголовки авторизации, если их напечатали целиком.
    (re.compile(r'((?:Authorization|X-API-KEY|x-goog-api-key|api-key|X-Auth-Token)'
                r'["\']?\s*[:=]\s*["\']?(?:Bearer\s+|Basic\s+)?)[A-Za-z0-9._\-+/=]{12,}', re.I),
     r'\1' + HIDDEN),
)

_env_pattern = None


def refresh_env_secrets(environ=None):
    """Пересобирает список точных значений из окружения.

    Вызывается при установке. Отдельная функция нужна тестам и точкам входа,
    которые дочитывают .env уже после импорта.
    """
    global _env_pattern

    values = set()
    for name, value in (environ if environ is not None else os.environ).items():
        if not value or not _SECRET_NAME_RE.search(name):
            continue
        value = value.strip()
        if _MIN_SECRET_LEN <= len(value) <= _MAX_SECRET_LEN:
            values.add(value)
    if not values:
        _env_pattern = None
        return 0
    # Длинные вперёд: иначе короткое значение съест кусок длинного.
    _env_pattern = re.compile('|'.join(re.escape(v) for v in
                                       sorted(values, key=len, reverse=True)))
    return len(values)


def scrub(text):
    """Возвращает текст без секретов. Ошибка здесь не должна ронять логирование."""
    if not text:
        return text
    try:
        if _env_pattern is not None:
            text = _env_pattern.sub(HIDDEN, text)
        for pattern, replacement in _RULES:
            text = pattern.sub(replacement, text)
    except Exception:  # noqa: BLE001
        return text
    return text


class SecretScrubbingFormatter(logging.Formatter):
    """Оборачивает форматтер обработчика, не меняя его вид."""

    def __init__(self, inner=None):
        super().__init__()
        self._inner = inner if inner is not None else logging.Formatter()

    @property
    def inner(self):
        return self._inner

    def format(self, record):
        return scrub(self._inner.format(record))


def install(logger=None, quiet_http_clients=True):
    """Вешает чистку на ОБРАБОТЧИКИ логгера (по умолчанию — корневого).

    Именно на обработчики, а не на логгер: запись, созданную дочерним логгером
    (httpx, urllib3, telegram), фильтры и форматтеры родителя не проходят — она
    поднимается сразу к обработчикам корня.

    quiet_http_clients: httpx и httpcore печатают полный URL КАЖДОГО исходящего
    вызова на INFO. Сегодня секрета в адресах нет, но канал открыт, а пользы от
    этих строк на проде нет — уводим их на WARNING.
    """
    refresh_env_secrets()
    target = logger if logger is not None else logging.getLogger()
    wrapped = 0
    for handler in target.handlers:
        if isinstance(handler.formatter, SecretScrubbingFormatter):
            continue
        handler.setFormatter(SecretScrubbingFormatter(handler.formatter))
        wrapped += 1
    if quiet_http_clients:
        for name in ('httpx', 'httpcore', 'urllib3.connectionpool'):
            logging.getLogger(name).setLevel(logging.WARNING)
    return wrapped
