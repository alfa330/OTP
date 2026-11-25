# -*- coding: utf-8 -*-
"""Сжатие ответов API gzip-ом в самом приложении.

До переезда в Yandex Cloud (ночь 24→25.11.2026) все ответы шли через Render, а перед ним стоит
Cloudflare: он сжимал JSON сам (br или gzip), и приложение об этом не думало. Шлюз кластера
отдаёт ответ как есть. Сетка «Графиков работы» за три месяца — 19 МБ JSON; через Cloudflare это
было 0,4–0,55 МБ и три секунды, напрямую стало 19 МБ и до 100 секунд. Всё это время на экране
«Нет смены» у всех (замер 25.11 на боевом сайте). Сжимаем здесь, чтобы не зависеть от того, что
стоит впереди: шлюз или Cloudflare, если сжимают сами, видят Content-Encoding и второй раз не сжимают.

Что НЕ сжимаем:
  * потоки (SSE и всё, что отдаётся по частям) — сжатие копило бы их в буфере;
  * файлы с диска (direct_passthrough) и уже сжатое по природе — картинки, xlsx, zip, pdf:
    сжимаются только текстовые типы;
  * ответы меньше килобайта — заголовок gzip съест выигрыш;
  * ответ, который уже сжал обработчик сам (табло ОП, «Отчёт по сменам»), и Cache-Control: no-transform;
  * клиентов, которые gzip не просили: старые «Oktell» и iCORE Phone получают прежний JSON.

gzip, а не br: модуль из стандартной библиотеки, без новых пакетов в образе. На том же ответе
сетки уровень 6 даёт 568 КБ (в 34 раза) за 60 мс — Cloudflare отдавал gzip-ом 545 КБ.
"""
import gzip
import logging

from werkzeug.http import parse_accept_header

MIN_SIZE = 1024
LEVEL = 6

# Текст вне text/*. Живые потоки (SSE) отсекает is_streamed в compress(): их тело — генератор.
# Поток, отданный одним куском, — уже законченное тело, и сжатие его ничем не задерживает.
_TEXT_TYPES = frozenset({
    'application/json',
    'application/javascript',
    'application/xml',
    'application/x-ndjson',
    'image/svg+xml',
})


def client_accepts_gzip(accept_encoding):
    """Просил ли клиент gzip: `gzip;q=0` — отказ, `*` — согласие."""
    return parse_accept_header(accept_encoding or '').quality('gzip') > 0


def _text_type(mimetype):
    mimetype = (mimetype or '').lower()
    return mimetype.startswith('text/') or mimetype in _TEXT_TYPES or mimetype.endswith('+json')


def compress(response, accept_encoding, method='GET', min_size=MIN_SIZE, level=LEVEL):
    """Сжать готовый ответ на месте, если его можно и стоит сжимать. Возвращает тот же объект."""
    if method == 'HEAD' or response.direct_passthrough or response.is_streamed:
        return response
    if response.status_code < 200 or response.status_code in (204, 206, 304):
        return response
    headers = response.headers
    if 'Content-Encoding' in headers or 'Content-Range' in headers:
        return response
    if 'no-transform' in (headers.get('Cache-Control') or '').lower():
        return response
    if not _text_type(response.mimetype):
        return response
    data = response.get_data()
    if len(data) < min_size:
        return response
    # Ответ зависит от Accept-Encoding, даже когда ушёл несжатым: кэш не должен отдать его тому,
    # кто просил gzip, и наоборот. Vary: Origin от CORS к этому моменту уже стоит — дописываем.
    response.vary.add('Accept-Encoding')
    if not client_accepts_gzip(accept_encoding):
        return response
    try:
        packed = gzip.compress(data, compresslevel=level, mtime=0)
    except Exception:  # noqa: BLE001 — без сжатия ответ всё равно правильный
        logging.warning('gzip ответа не удался, уходит несжатым', exc_info=True)
        return response
    response.set_data(packed)
    headers['Content-Encoding'] = 'gzip'
    etag, weak = response.get_etag()
    if etag:
        # Сжатое тело — другое представление: сильный валидатор у него обязан быть своим.
        response.set_etag(etag + '-gzip', weak=weak)
    return response


def install(app, min_size=MIN_SIZE, level=LEVEL):
    """Подключить сжатие к приложению Flask.

    Вызывать СРАЗУ после создания app, до остальных after_request: Flask зовёт их в обратном
    порядке регистрации, так что этот отработает последним и сожмёт уже окончательный ответ —
    с заголовками CORS и Server-Timing, после всех правок тела.
    """
    from flask import request

    @app.after_request
    def gzip_response(response):
        return compress(response, request.headers.get('Accept-Encoding'), request.method,
                        min_size=min_size, level=level)

    return gzip_response
