# -*- coding: utf-8 -*-
"""Пересылка со старого адреса Render на новый сервер портала.

После переезда в Yandex Cloud (ночь 24→25.11.2026) старые программы на ПК операторов —
«Oktell» до 1.0.35 и iCORE Phone до 3.22.64 — по-прежнему ходят на имя сервиса Render
https://otp-2-fos4.onrender.com: оно вшито в них при сборке. Сервис Render вместо портала
запускает этот файл: каждый запрос уходит как есть на OTP_FORWARD_TO (https://api.icorehub.kz),
ответ возвращается потоком — долгие опросы и SSE тоже. Старые программы снова входят и сами
обновляются до версий с новым адресом; когда обновятся все, сервис Render выключается.

Здесь нет ни бота, ни базы, ни планировщика: портал на Render НЕ поднимается. Без
OTP_FORWARD_TO (https://…) процесс завершается с ошибкой, а не начинает что-то ещё.

Пересылка одна на всех клиентов, поэтому у неё нет своего состояния между запросами: urllib3
напрямую, без requests.Session. У сессии requests есть банка cookie — 25.11 она запомнила
cookie входа одного оператора из Set-Cookie и подставляла их во все запросы без своих cookie:
вход остальных получал 403 «Invalid request origin», а запрос с просроченным токеном прошёл бы
под чужой учёткой. Ещё сессия дописывает свои заголовки (Accept-Encoding: gzip), которых
клиент не просил. Здесь уходят только заголовки самого клиента.

Start Command на Render: python deploy/render_forward.py
"""
import logging
import os
import sys
from urllib.parse import quote

import certifi
import urllib3

TARGET = (os.getenv('OTP_FORWARD_TO') or '').strip().rstrip('/')
# Заголовки одного соединения (RFC 9110 §7.6.1) и те, что пересчитываются при пересылке.
HOP_BY_HOP = frozenset({
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'proxy-connection',
    'te', 'trailer', 'trailers', 'transfer-encoding', 'upgrade', 'host', 'content-length',
})
MAX_BODY_BYTES = 100 * 1024 * 1024
CHUNK_BYTES = 16 * 1024
CONNECT_TIMEOUT = 10
# Долгие опросы и SSE: портал шлёт пульс раз в 15–25 с, так что пять минут тишины —
# это обрыв, а не ожидание.
READ_TIMEOUT = 300
HEALTH_PATH = '/__forward/health'
# Путь кодируется обратно теми же правилами, что у браузера: WSGI отдаёт PATH_INFO уже
# раскодированным, а подпись агентов и токены в пути должны дойти байт в байт.
PATH_SAFE = "/:@!$&'()*+,;=-._~"

log = logging.getLogger('render_forward')

# Ни cookie, ни повторов, ни переходов по редиректам, ни прокси из окружения: это всё
# состояние или решения, которые принимает клиент, а не пересылка.
_pool = urllib3.PoolManager(
    num_pools=4, maxsize=64, block=False, retries=False,
    timeout=urllib3.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT),
    cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())


def upstream_url(environ, target=None):
    target = (target if target is not None else TARGET).rstrip('/')
    path = environ.get('PATH_INFO') or '/'
    try:
        raw_path = path.encode('latin-1')   # PEP 3333: байты пути, прочитанные как latin-1
    except UnicodeEncodeError:
        raw_path = path.encode('utf-8')     # сервер отдал уже раскодированный текст
    url = target + quote(raw_path, safe=PATH_SAFE)
    query = environ.get('QUERY_STRING') or ''
    return url + ('?' + query if query else '')


def request_headers(environ):
    headers = {}
    for key, value in environ.items():
        if not key.startswith('HTTP_'):
            continue
        name = key[5:].replace('_', '-').title()
        if name.lower() in HOP_BY_HOP:
            continue
        headers[name] = value
    if environ.get('CONTENT_TYPE'):
        headers['Content-Type'] = environ['CONTENT_TYPE']
    # Адрес клиента первым в X-Forwarded-For: на нём журнал сессий и лимит неудачных входов.
    # Render его уже кладёт; если нет — ставим адрес соединения.
    if not headers.get('X-Forwarded-For') and environ.get('REMOTE_ADDR'):
        headers['X-Forwarded-For'] = environ['REMOTE_ADDR']
    if environ.get('HTTP_HOST'):
        headers['X-Forwarded-Host'] = environ['HTTP_HOST']
    headers['X-Forwarded-Proto'] = 'https'
    return headers


def read_body(environ):
    """Тело запроса целиком (None — тела нет). Длиннее MAX_BODY_BYTES — ValueError."""
    raw_length = (environ.get('CONTENT_LENGTH') or '').strip()
    stream = environ.get('wsgi.input')
    if raw_length:
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            raise ValueError('тело запроса больше %d байт' % MAX_BODY_BYTES)
        return stream.read(length) if length else b''
    if environ.get('REQUEST_METHOD', 'GET') in ('POST', 'PUT', 'PATCH', 'DELETE') and stream is not None:
        # Тело без Content-Length (chunked): waitress уже собрал его целиком.
        data = stream.read(MAX_BODY_BYTES + 1)
        if len(data) > MAX_BODY_BYTES:
            raise ValueError('тело запроса больше %d байт' % MAX_BODY_BYTES)
        return data
    return None


def response_headers(raw_headers):
    out = []
    # Повторы (два Set-Cookie) — отдельными строками, а не склеенными через запятую.
    items = raw_headers.iteritems() if hasattr(raw_headers, 'iteritems') else raw_headers.items()
    for name, value in items:
        lowered = name.lower()
        # Длину отдаём как есть: тело идёт байт в байт (сжатое — сжатым).
        if lowered in HOP_BY_HOP and lowered != 'content-length':
            continue
        out.append((name, value))
    return out


def _plain(start_response, status, text):
    body = text.encode('utf-8')
    start_response(status, [('Content-Type', 'text/plain; charset=utf-8'),
                            ('Content-Length', str(len(body))), ('Cache-Control', 'no-store')])
    return [body]


def app(environ, start_response):
    if (environ.get('PATH_INFO') or '') == HEALTH_PATH:
        return _plain(start_response, '200 OK', 'forward -> %s\n' % TARGET)
    try:
        body = read_body(environ)
    except ValueError as exc:
        return _plain(start_response, '413 Payload Too Large', str(exc))
    method = environ.get('REQUEST_METHOD', 'GET')
    try:
        upstream = _pool.urlopen(
            method, upstream_url(environ), body=body, headers=request_headers(environ),
            redirect=False, assert_same_host=False, preload_content=False, decode_content=False)
    except (urllib3.exceptions.HTTPError, OSError) as exc:
        log.warning('новый сервер не ответил: %s %s — %s', method, environ.get('PATH_INFO'), exc)
        return _plain(start_response, '502 Bad Gateway', 'upstream unavailable')
    status = '%d %s' % (upstream.status, upstream.reason or '')
    start_response(status.strip(), response_headers(upstream.headers))

    def body_iter():
        finished = False
        try:
            # Без распаковки: Content-Encoding ответа остаётся прежним. Чанки — по мере прихода
            # (SSE не копится в буфере).
            for chunk in upstream.stream(CHUNK_BYTES, decode_content=False):
                if chunk:
                    yield chunk
            finished = True
        except Exception as exc:  # noqa: BLE001 — обрыв посреди потока: клиент переподключится
            log.info('поток %s оборвался: %s', environ.get('PATH_INFO'), exc)
        finally:
            if not finished:
                # Недочитанный ответ (клиент ушёл с SSE): соединение живым в пул не возвращаем.
                upstream.close()
            upstream.release_conn()

    return body_iter()


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if not TARGET.startswith('https://'):
        sys.exit('OTP_FORWARD_TO не задан (нужен https://…) — пересылка не запущена, портал здесь не поднимается')
    from waitress import serve
    port = int(os.getenv('PORT') or '10000')
    log.info('пересылка: :%d -> %s', port, TARGET)
    serve(app, host='0.0.0.0', port=port, threads=64, connection_limit=600,
          channel_timeout=READ_TIMEOUT + 60, ident='otp-forward')


if __name__ == '__main__':
    main()
