# -*- coding: utf-8 -*-
"""Пересылка со старого адреса Render на новый сервер портала.

После переезда в Yandex Cloud (ночь 24→25.11.2026) старые программы на ПК операторов —
«Oktell» до 1.0.35 и iCORE Phone до 3.22.64 — по-прежнему ходят на имя сервиса Render
https://otp-2-fos4.onrender.com: оно вшито в них при сборке. Сервис Render вместо портала
запускает этот файл: каждый запрос уходит как есть на OTP_FORWARD_TO (https://api.icorehub.kz),
ответ возвращается потоком — долгие опросы и SSE тоже. Старые программы снова входят и сами
обновляются до версий с новым адресом; когда обновятся все, сервис Render выключается.

Если новый сервер с Render недоступен, клиент получает 307 на тот же путь нового адреса и идёт
туда сам. 26.11.2026 с 04:33 сеть Yandex Cloud перестала принимать соединения с Render
(Франкфурт): каждый запрос ждал 10 с и получал 502, и операторы со старыми программами не могли
войти — а с их ПК в Казахстане новый адрес открыт. На 307 обе программы повторяют запрос
сами (проверено опытом на их же HTTP-вызовах): iCORE Phone (WinHTTP) — тот же метод, тело и
Authorization; «Oktell» (requests) — тот же метод и тело, но Authorization на чужой хост не
несёт: вход, refresh (X-Refresh-Token), heartbeat и обновление (X-Agent-Token) проходят, а
объявления и учётка кабинета Oktell ждут обновления до 1.0.36. Поэтому, пока сервер доступен,
запросы по-прежнему пересылаются, а 307 — только когда пересылка невозможна.

Здесь нет ни бота, ни базы, ни планировщика: портал на Render НЕ поднимается. Без
OTP_FORWARD_TO (https://…) процесс завершается с ошибкой, а не начинает что-то ещё.

Пересылка одна на всех клиентов, поэтому у неё нет своего состояния между запросами: urllib3
напрямую, без requests.Session. У сессии requests есть банка cookie — 25.11 она запомнила
cookie входа одного оператора из Set-Cookie и подставляла их во все запросы без своих cookie:
вход остальных получал 403 «Invalid request origin», а запрос с просроченным токеном прошёл бы
под чужой учёткой. Ещё сессия дописывает свои заголовки (Accept-Encoding: gzip), которых
клиент не просил. Здесь уходят только заголовки самого клиента.

Start Command на Render: python deploy/render_forward.py
OTP_FORWARD_MODE: auto (по умолчанию: пересылка, при недоступности — 307) или redirect (всегда 307).
"""
import logging
import os
import sys
import threading
import time
from urllib.parse import quote

import certifi
import urllib3

TARGET = (os.getenv('OTP_FORWARD_TO') or '').strip().rstrip('/')
MODE = (os.getenv('OTP_FORWARD_MODE') or 'auto').strip().lower()
# Заголовки одного соединения (RFC 9110 §7.6.1) и те, что пересчитываются при пересылке.
HOP_BY_HOP = frozenset({
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'proxy-connection',
    'te', 'trailer', 'trailers', 'transfer-encoding', 'upgrade', 'host', 'content-length',
})
MAX_BODY_BYTES = 100 * 1024 * 1024
CHUNK_BYTES = 16 * 1024
# Соединение Франкфурт → Алматы (TCP и TLS) открывается за доли секунды. Дольше не ждём: старый
# «Oktell» ждёт ответа пересылки 10 с, и 307 должен прийти раньше, чем он сдастся.
CONNECT_TIMEOUT = 3
# Долгие опросы и SSE: портал шлёт пульс раз в 15–25 с, так что пять минут тишины —
# это обрыв, а не ожидание.
READ_TIMEOUT = 300
# Пересылка не удалась — столько секунд все клиенты сразу получают 307, без попыток; потом
# очередной запрос пробует пересылку снова.
DIRECT_FOR_S = 60
# Эти методы повторять безопасно: если запрос сорвался уже после отправки, сервер его ничего
# не записал. POST и прочие после возможной отправки получают 502, а не 307 — иначе запись
# могла бы пройти дважды; повтор клиента уйдёт по 307 (пересылка уже в паузе).
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
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

# Пауза пересылки: до какого момента (time.monotonic) всех сразу отправляем напрямую.
_direct_lock = threading.Lock()
_direct_until = 0.0
_direct_reason = ''


def _direct_left(now=None):
    """Сколько секунд ещё отправляем клиентов напрямую (0 — пересылка работает)."""
    left = _direct_until - (time.monotonic() if now is None else now)
    return left if left > 0 else 0.0


def _pause_forwarding(reason):
    global _direct_until, _direct_reason
    with _direct_lock:
        already = _direct_left() > 0
        _direct_until = time.monotonic() + DIRECT_FOR_S
        _direct_reason = reason
    if not already:
        log.warning('новый сервер недоступен (%s) — %d с отправляю клиентов напрямую (307)',
                    reason, DIRECT_FOR_S)


def _forwarding_back():
    """Пересылка удалась. Действующую паузу не снимаем: этот запрос мог уйти ещё до неё
    (по живому соединению из пула), а новые соединения по-прежнему не открываются."""
    global _direct_reason
    if not _direct_reason:
        return
    with _direct_lock:
        if not _direct_reason or _direct_left() > 0:
            return
        _direct_reason = ''
    log.info('связь с новым сервером есть — снова пересылаю')


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


def _send_direct(start_response, environ):
    """307 на тот же путь и запрос нового адреса: метод и тело клиент повторяет сам."""
    start_response('307 Temporary Redirect', [
        ('Location', upstream_url(environ)), ('Content-Length', '0'), ('Cache-Control', 'no-store')])
    return [b'']


def app(environ, start_response):
    if (environ.get('PATH_INFO') or '') == HEALTH_PATH:
        left = _direct_left()
        state = 'redirect' if MODE == 'redirect' else (
            'direct %d s: %s' % (left, _direct_reason) if left else 'forward')
        return _plain(start_response, '200 OK', 'forward -> %s (%s)\n' % (TARGET, state))
    if MODE == 'redirect' or _direct_left() > 0:
        return _send_direct(start_response, environ)
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
        _pause_forwarding('%s: %s' % (type(exc).__name__, exc))
        # Соединение не открылось (ConnectTimeoutError; NewConnectionError и NameResolutionError —
        # его подклассы) — запрос до сервера не дошёл, повторить его напрямую безопасно.
        if isinstance(exc, urllib3.exceptions.ConnectTimeoutError) or method in SAFE_METHODS:
            return _send_direct(start_response, environ)
        log.warning('новый сервер не ответил: %s %s — %s', method, environ.get('PATH_INFO'), exc)
        return _plain(start_response, '502 Bad Gateway', 'upstream unavailable')
    _forwarding_back()
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
    if MODE not in ('auto', 'redirect'):
        sys.exit('OTP_FORWARD_MODE=%r: можно auto или redirect' % MODE)
    from waitress import serve
    port = int(os.getenv('PORT') or '10000')
    log.info('пересылка: :%d -> %s (%s)', port, TARGET, MODE)
    serve(app, host='0.0.0.0', port=port, threads=64, connection_limit=600,
          channel_timeout=READ_TIMEOUT + 60, ident='otp-forward')


if __name__ == '__main__':
    main()
