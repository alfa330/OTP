# -*- coding: utf-8 -*-
"""Генератор ссылки на подписание Sapar — только сеть и разбор HTML, без правил.

У Sapar нет API для этой операции: в swagger (tps-public.silt.kz/swagger)
одиннадцать ручек про статусы и документы, а ссылку выдаёт только гостевая
страница /ru/link-generator — обычная POST-форма ASP.NET Core с anti-forgery
токеном. Поэтому здесь не JSON, а два HTTP-запроса и разбор разметки:

    GET  страница        → cookie сессии + __RequestVerificationToken из формы
    POST та же страница  → multipart с ИИН; ответ — та же страница, в которой
                           либо `#postback-message` с текстом, либо ссылка

Что видели живьём 16.09.2026 (без реального ИИН — только чужой и заведомо
несуществующий): на несуществующий ИИН страница отвечает 200 и
`<div class="alert alert-info" id="postback-message">Нет документов на
подписание. Документы подписаны</div>`. Успешный ответ с самой ссылкой снять
не удалось, поэтому разбор ищет ссылку несколькими способами (href, value,
голый адрес в тексте) и честно отвечает «незнакомый формат», если ни одного
не нашёл, — а не выдаёт что попало за ссылку.

Наружу этот модуль отдаёт словарь результата и НИКОГДА не отдаёт адрес
генератора: оператор до него добираться не должен (требование владельца), и
даже текст ошибки чистится от адресов.

Ошибки не летят исключениями: генератор лёг — это исход `unavailable`,
который журнал запишет, а оператору покажется «попробуйте позже».
"""

import html
import logging
import os
import re
import time
from urllib.parse import urlsplit

import requests

DEFAULT_GENERATOR_URL = 'https://tps-public.silt.kz/ru/link-generator'
FORM_ID = 'MnuGenerateLinkToSignWithEgovMobile'
TIMEOUT = 20

# Браузерный User-Agent: страница за WAF, и «python-requests» там лишний повод
# для отказа. Своё имя оставляем в хвосте — по нему нас найдут в их логах.
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) OTP-Portal/1.0'

# Исходы генерации. Те же коды лежат в журнале (schema.OUTCOMES) и в подписях
# фронта (signLinkMeta.js).
LINK = 'link'
NO_DOCUMENTS = 'no_documents'
REJECTED = 'rejected'
UNAVAILABLE = 'unavailable'

_TOKEN_RE = re.compile(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"')
_OPTIONS_RE = re.compile(r'name="(flDriverIin-[^"]*-options)"[^>]*value="([^"]*)"')
_POSTBACK_RE = re.compile(r'id="postback-message"[^>]*>(.*?)</div>', re.S)
_ALERT_RE = re.compile(r'<div[^>]*class="[^"]*\balert\b[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
# Содержательная колонка страницы: от конца заголовка до конца колонки. Так
# устроена живая разметка (16.09.2026): `<!-- end page title -->`, затем
# `.row > .col` с alert'ом и формой, затем `<!-- end col -->`. Запасной якорь —
# сам alert или контейнер формы: если вендор уберёт комментарии, разбор не
# должен ослепнуть целиком.
_CONTENT_RE = re.compile(r'<!--\s*end page title\s*-->(.*?)<!--\s*end col\s*-->', re.S)
_CONTENT_FALLBACK_RE = re.compile(r'((?:id="postback-message"|id="form-container-' + FORM_ID
                                  + r').*?)<!--\s*end col\s*-->', re.S)
# Поле ИИН формы: страница возвращает его с введённым значением, и то, что ввёл
# человек, ссылкой считаться не должно ни при каких обстоятельствах.
_IIN_INPUT_RE = re.compile(r'<input[^>]*name="flDriverIin"[^>]*>', re.I)
_HREF_RE = re.compile(r'href="([^"]+)"')
_VALUE_RE = re.compile(r'value="((?:https?:|mobilesign:|egovmobile:)[^"]+)"', re.I)
_BARE_URL_RE = re.compile(r'(?<![="\'])(https?://[^\s"\'<>]+)')
_TAG_RE = re.compile(r'<[^>]+>')
_ANY_URL_RE = re.compile(r'(?:https?://|www\.)\S+', re.I)

# Фразы «документов нет» — ответ генератора, который для оператора не ошибка,
# а готовый ответ водителю. Сверяем по-русски и по-казахски/английски на
# случай, если вендор сменит язык ответа независимо от языка страницы.
_NO_DOCUMENTS_HINTS = ('нет документов', 'документы подписаны', 'құжаттар жоқ',
                       'no documents', 'documents are signed')

# Служебные адреса самой страницы: тема, скрипты, переключатели языка. Ссылкой
# на подписание не бывают, и попадаться разбору не должны.
_CHROME_PATH_PREFIXES = ('/theme/', '/uicorepackages/', '/scripts/', '/images2/',
                         '/app.webmanifest', '/ru/', '/kz/', '/en/')
_CHROME_HOSTS = ('cdnjs.cloudflare.com', 'cdn.jsdelivr.net', 'www.googletagmanager.com',
                 'pip.silt.kz', 'fonts.googleapis.com', 'fonts.gstatic.com')


def generator_url():
    return (os.getenv('SAPAR_LINK_GENERATOR_URL') or DEFAULT_GENERATOR_URL).strip()


def _clean_text(fragment):
    """HTML-фрагмент → одна строка текста без тегов, сущностей и адресов."""
    text = html.unescape(_TAG_RE.sub(' ', fragment or ''))
    text = _ANY_URL_RE.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()[:300]


def _is_chrome_link(url):
    """Ссылка принадлежит оформлению страницы, а не её содержимому."""
    parts = urlsplit(url)
    if parts.netloc.lower() in _CHROME_HOSTS:
        return True
    path = (parts.path or '').lower()
    if not parts.netloc and not path.startswith('/'):
        # «#», «javascript:», относительные хвосты — не адрес для водителя.
        return True
    return any(path.startswith(prefix) for prefix in _CHROME_PATH_PREFIXES)


def _content_region(page):
    """Содержательная часть страницы — там, где вендор показывает ответ."""
    match = _CONTENT_RE.search(page) or _CONTENT_FALLBACK_RE.search(page)
    return match.group(1) if match else page


def _candidate_links(region):
    """Все адреса из содержательной части страницы, в порядке появления."""
    region = _IIN_INPUT_RE.sub('', region)
    seen = []
    for pattern in (_HREF_RE, _VALUE_RE, _BARE_URL_RE):
        for match in pattern.finditer(region):
            url = html.unescape(match.group(1)).strip()
            if not url or url in seen or _is_chrome_link(url):
                continue
            seen.append(url)
    return seen


def _vendor_message(page):
    """Текст ответа вендора: `#postback-message`, а без него — любой alert.

    На несуществующий ИИН страница отвечает alert'ом с id — но отказ по другой
    причине может прийти alert'ом без id (так у Bootstrap-страниц обычно и
    бывает), и молчать про него значило бы показать оператору «сервис не
    отвечает» вместо слов вендора.
    """
    postback = _POSTBACK_RE.search(page)
    if postback:
        return _clean_text(postback.group(1))
    for match in _ALERT_RE.finditer(page):
        text = _clean_text(match.group(1))
        if text:
            return text
    return ''


def parse_response(page):
    """Разбор HTML ответа формы. Чистая функция — проверяется без сети.

    Возвращает (исход, ссылка, сообщение вендора). Ссылка есть только у
    исхода LINK; сообщение — текст alert'а, если он был.

    Ссылку ищем сначала в содержательной колонке, а не найдя — по всей
    странице: оформление страницы (тема, скрипты, переключатели языка,
    счётчики) отсеивает _is_chrome_link, и на живых страницах вендора без
    результата ни одного кандидата не остаётся (проверено 16.09.2026 на
    трёх снятых страницах). Так ссылка находится и если вендор рисует её
    вне колонки — в модальном окне или в подвале.
    """
    page = page or ''
    message = _vendor_message(page)

    links = _candidate_links(_content_region(page)) or _candidate_links(page)

    if links:
        return LINK, links[0], message or None
    if message:
        lowered = message.lower()
        if any(hint in lowered for hint in _NO_DOCUMENTS_HINTS):
            return NO_DOCUMENTS, None, message
        return REJECTED, None, message
    return UNAVAILABLE, None, None


def diagnostics(page):
    """Что за страницу прислал вендор — для лога, когда разбор её не узнал.

    Без персональных данных: поле ИИН и anti-forgery токен вырезаны, адреса
    сведены к доменам. Нужна ровно один раз — чтобы по логу понять новый
    формат ответа и поправить parse_response, не выпрашивая у кого-то живой
    ИИН для опытов.
    """
    page = page or ''
    scrubbed = _TOKEN_RE.sub('', _IIN_INPUT_RE.sub('', page))
    region = _content_region(scrubbed)
    hosts = sorted({(urlsplit(u).netloc or urlsplit(u).scheme)[:60]
                    for u in _candidate_links(scrubbed) + re.findall(r'https?://[^\s"\'<>]+', scrubbed)})
    alerts = [_clean_text(m.group(0))[:200] for m in _ALERT_RE.finditer(scrubbed)][:5]
    ids = re.findall(r'\bid="([^"]{1,60})"', region)[:20]
    return {
        'bytes': len(page),
        'token': bool(_TOKEN_RE.search(page)),
        'iin_echoed': bool(re.search(r'name="flDriverIin"[^>]*value="[^"]+"', page)),
        'alerts': alerts,
        'hosts': hosts[:20],
        'region_ids': ids,
        'region_html': re.sub(r'\s+', ' ', region)[:1500],
    }


def _result(outcome, *, link=None, message=None, error=None, started):
    return {
        'outcome': outcome,
        'link': link,
        'message': message,
        'error': error,
        'latency_ms': int((time.monotonic() - started) * 1000),
    }


def generate(iin):
    """Ссылка на подписание по ИИН. Никогда не бросает исключение.

    Возвращает словарь:
        outcome     — link / no_documents / rejected / unavailable
        link        — сама ссылка (только при outcome == link)
        message     — текст ответа генератора, уже без адресов
        error       — почему unavailable (для лога и журнала, не для оператора)
        latency_ms  — сколько ждали генератор
    """
    started = time.monotonic()
    url = generator_url()
    session = requests.Session()
    session.headers.update({'User-Agent': USER_AGENT, 'Accept-Language': 'ru'})
    try:
        page = session.get(url, timeout=TIMEOUT)
        page.raise_for_status()
    except Exception as error:  # noqa: BLE001
        logging.warning('sign_links: генератор не отдал форму: %s', error)
        return _result(UNAVAILABLE, error='форма не открылась: %s' % error, started=started)

    token = _TOKEN_RE.search(page.text or '')
    if not token:
        logging.warning('sign_links: на странице генератора нет anti-forgery токена')
        return _result(UNAVAILABLE, error='форма генератора изменилась: нет токена',
                       started=started)

    # Служебные поля редактора формы отдаём как есть — их имена и значения
    # берём со страницы, а не из головы: суффикс у поля вендор может сменить.
    fields = {
        '__RequestVerificationToken': (None, token.group(1)),
        'yoda_form_id': (None, FORM_ID),
        'flDriverIin': (None, str(iin)),
        'submit': (None, 'Сгенерировать ссылку'),
    }
    options = _OPTIONS_RE.findall(page.text or '')
    if options:
        for name, value in options:
            fields[name] = (None, html.unescape(value))
    else:
        fields['flDriverIin-12qsa-options'] = (None, 'YodaApp.Ui.TextInputEditor')

    try:
        answer = session.post(url, files=fields, timeout=TIMEOUT, allow_redirects=True)
        answer.raise_for_status()
    except Exception as error:  # noqa: BLE001
        logging.warning('sign_links: генератор не принял форму: %s', error)
        return _result(UNAVAILABLE, error='форма не отправилась: %s' % error, started=started)

    outcome, link, message = parse_response(answer.text or '')
    if outcome == UNAVAILABLE:
        # Формат ответа изменился — это надо увидеть в логе целиком (без ИИН и
        # токена, см. diagnostics): по одному такому логу разбор и правится.
        logging.warning('sign_links: незнакомый ответ генератора (статус %s): %s',
                        answer.status_code, diagnostics(answer.text or ''))
        return _result(UNAVAILABLE, error='незнакомый формат ответа генератора',
                       started=started)
    return _result(outcome, link=link, message=message, started=started)


def link_host(link):
    """Домен ссылки — справочно в журнал. Саму ссылку журнал не хранит."""
    try:
        return (urlsplit(link).netloc or urlsplit(link).scheme or '')[:120] or None
    except Exception:  # noqa: BLE001
        return None
