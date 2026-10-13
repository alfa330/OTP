"""Внешний вход раздела «Ссылка на подписание» (Flask Blueprint).

Сторонний сервис присылает список ИИН и на каждый получает либо ссылку на
подписание, либо «не найдено» (просьба владельца 13.10.2026). Сотрудником
портала он не является, поэтому здесь нет ни require_api_key (это вход по
сессии), ни QR: вместо них секретный ключ в заголовке X-Api-Key (api_key.py).

Одна ручка:
    POST /api/sign_links/external/links      {"iins": ["…", "…"]}

Ответ — запись на каждый присланный ИИН, в том же порядке:
    link        ссылка на подписание (поле link)
    not_found   генератор ответил, что подписывать нечего
    invalid     это не ИИН: цифры, длина, дата или контрольная цифра (reason)
    error       ответа получить не удалось — запрос по этому ИИН надо повторить

`error` — не вежливая форма `not_found`. «Не спросили» и «документов нет» —
разные ответы водителю, и подменять первый вторым нельзя. Незнакомые слова
генератора (исход rejected) тоже уходят как error, с его текстом: «подписывать
нечего» мы говорим, только когда генератор сказал это знакомой фразой.

Порядок проверок — от дешёвого к дорогому, и до ключа не делается НИЧЕГО:
    ключ → размер и форма тела → место → дневной предел → генератор →
    журнал → ответ.

Чем ограничено (значения — в окружении сервера, см. README):
    * ИИН в одном запросе — MAX_IINS;
    * ИИН в сутки на все ключи — DAILY_LIMIT, счёт по журналу;
    * одновременных запросов — MAX_IN_FLIGHT; лишний получает 429, а не
      занимает нить waitress на полминуты;
    * одновременных походов к генератору — batch.LANES на весь процесс.
Пределы стоят не от подбора ключа (его не подобрать), а на случай, если ключ
утечёт или сервис на той стороне зациклится: генератор — чужая форма на общем
с API Sapar хосте, и блокировка там ударит по «Обзвону» и «Обращениям».

Каждый спрошенный ИИН — строка журнала раздела (кто: «Внешний сервис» с
отпечатком ключа). Журнал не записался — ссылки не отдаём: выдача без следа
хуже отказа. Сама ссылка, как и у сотрудников, в базе не хранится.
"""

import json
import logging
import os
import threading
import time

from flask import Blueprint, jsonify, request

from . import api_key, batch, iin as iin_rules, queries, sapar_link

MAX_IINS = 50
MAX_IINS_CEILING = 200
DAILY_LIMIT = 10000
DAILY_LIMIT_CEILING = 500000
MAX_IN_FLIGHT = 3
RETRY_AFTER_SEC = 2
# Полсотни ИИН — это килобайт. 64 КБ хватает на потолок с запасом на пробелы и
# дефисы, а читать больше до разбора незачем.
MAX_BODY_BYTES = 64 * 1024
# Сколько знаков присланного значения возвращаем и пишем в журнал: у опечатки
# длина любая, а колонка журнала — 32.
ECHO_LENGTH = 32

# Статусы ответа — контракт с внешним сервисом.
LINK, NOT_FOUND, INVALID, ERROR = 'link', 'not_found', 'invalid', 'error'
STATUSES = (LINK, NOT_FOUND, INVALID, ERROR)

NOT_FOUND_MESSAGE = 'Нет документов на подписание'
EMPTY_MESSAGE = 'Пустое значение вместо ИИН'
# Без адреса и без технической причины: она лежит в журнале, а не в ответе.
UNAVAILABLE_MESSAGE = 'Сервис подписания не ответил — повторите запрос позже'
TIMEOUT_MESSAGE = 'Не успели обработать — повторите запрос по этому ИИН'


def _env_int(name, default, minimum, maximum):
    try:
        value = int(str(os.getenv(name) or '').strip() or default)
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _text(item):
    """Элемент списка как строка. ИИН — строка; число тоже примем, остальное пусто."""
    if isinstance(item, str):
        return item
    if isinstance(item, int) and not isinstance(item, bool):
        return str(item)
    return ''


def _storable(text):
    """Без NUL и непарных суррогатов: чужой JSON приносит и то и другое, а база
    и UTF-8 их не примут — из-за одной такой строки упала бы запись всей пачки."""
    return ''.join(ch for ch in text if ch != '\x00' and not '\ud800' <= ch <= '\udfff')


def _printable(text):
    return ''.join(ch for ch in text if ch.isprintable())


def _entry(item):
    text = _storable(_text(item))
    value, defect = iin_rules.validate(text)
    return {
        'shown': text.strip()[:ECHO_LENGTH],
        'iin': value,
        'defect': defect,
        # В журнал — только печатное: его читает человек.
        'logged': _printable(iin_rules.normalize(text))[:ECHO_LENGTH],
    }


def _parse_items(body):
    """Тело запроса → (список как прислали, None) либо (None, что не так)."""
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        return None, 'Тело запроса — не JSON'
    items = payload.get('iins') if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return None, 'Нужен JSON вида {"iins": ["900101300007", "…"]}'
    if not items:
        return None, 'Список ИИН пуст'
    return items, None


def _settle(result):
    """(исход, ссылка): ссылка есть только у исхода link, и наоборот."""
    outcome = result.get('outcome') or sapar_link.UNAVAILABLE
    link = result.get('link') if outcome == sapar_link.LINK else None
    if outcome == sapar_link.LINK and not link:
        outcome = sapar_link.UNAVAILABLE
    return outcome, link


def _answer(entry, outcomes):
    """Запись ответа на один присланный ИИН."""
    answer = {'iin': entry['shown']}
    defect = entry['defect']
    if defect:
        message = EMPTY_MESSAGE if defect == 'empty' else iin_rules.error_message(defect)
        return dict(answer, status=INVALID, reason=defect, message=message)
    result = outcomes[entry['iin']]
    outcome, link = _settle(result)
    if link:
        return dict(answer, status=LINK, link=link)
    if outcome == sapar_link.NO_DOCUMENTS:
        return dict(answer, status=NOT_FOUND, message=result.get('message') or NOT_FOUND_MESSAGE)
    if outcome == sapar_link.REJECTED:
        return dict(answer, status=ERROR, reason='rejected',
                    message=result.get('message') or UNAVAILABLE_MESSAGE)
    if result.get('skipped') == batch.BUDGET:
        return dict(answer, status=ERROR, reason='timeout', message=TIMEOUT_MESSAGE)
    return dict(answer, status=ERROR, reason='unavailable', message=UNAVAILABLE_MESSAGE)


def build_sign_links_external_blueprint(*, db, client_ip=None, generate=None, executor=None,
                                        environ=None, max_iins=None, daily_limit=None):
    """Собирает Blueprint внешнего входа.

    client_ip — () -> str: адрес запроса, в журнал.
    generate — (iin) -> dict: клиент генератора; подменяется в тестах.
    executor, environ, max_iins, daily_limit — для тестов; на сервере места
    берутся общие (batch.shared_executor), ключ и пределы — из окружения.
    """
    bp = Blueprint('sign_links_external', __name__, url_prefix='/api/sign_links/external')
    if generate is None:
        def generate(value):
            return sapar_link.generate(value, timeout=batch.CALL_TIMEOUT)
    max_iins = max_iins or _env_int('SIGN_LINKS_API_MAX_IINS', MAX_IINS, 1, MAX_IINS_CEILING)
    daily_limit = daily_limit or _env_int('SIGN_LINKS_API_DAILY_LIMIT', DAILY_LIMIT, 1,
                                          DAILY_LIMIT_CEILING)
    slots = threading.BoundedSemaphore(MAX_IN_FLIGHT)

    def _ip():
        try:
            return client_ip() if client_ip else request.remote_addr
        except Exception:  # noqa: BLE001
            return None

    def _ua():
        return _printable(request.headers.get('User-Agent') or '')[:500]

    def _refuse(status, code, message, headers=None, **extra):
        return jsonify({"error": message, "code": code, **extra}), status, headers or {}

    def _limits(used):
        return {'per_day': daily_limit, 'used_today': used,
                'left_today': max(0, daily_limit - used)}

    def _read_body():
        """Тело запроса, но не больше MAX_BODY_BYTES. None — больше."""
        declared = request.content_length
        if declared is not None and declared > MAX_BODY_BYTES:
            return None
        body = request.stream.read(MAX_BODY_BYTES + 1)
        return None if len(body) > MAX_BODY_BYTES else body

    @bp.after_request
    def _never_cache(response):
        # В ответе ссылки на документы людей: ни прокси, ни клиенту хранить его незачем.
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route('/links', methods=['POST'])
    def sign_links_external_links():
        try:
            return _links()
        except Exception:  # noqa: BLE001
            logging.exception('sign_links: внешний вход упал')
            return _refuse(500, 'SIGN_LINKS_API_FAILED',
                           'Внутренняя ошибка — повторите запрос позже')

    def _links():
        # ── Ключ. До него не читается даже тело запроса. ─────────────────────
        keys, _config_error = api_key.configured(environ)
        if not keys:
            # Не задан и задан с ошибкой — снаружи одно и то же; причина в логе.
            return _refuse(503, 'SIGN_LINKS_API_NOT_CONFIGURED',
                           'Доступ по ключу на сервере не настроен')
        mark = api_key.match(request.headers.get(api_key.HEADER), keys)
        if not mark:
            # Нет заголовка и неверный ключ — один ответ: чужому незачем знать,
            # на чём он споткнулся.
            return _refuse(401, 'SIGN_LINKS_API_UNAUTHORIZED', 'Ключ не принят')

        # ── Тело ─────────────────────────────────────────────────────────────
        body = _read_body()
        if body is None:
            return _refuse(413, 'SIGN_LINKS_API_TOO_LARGE', 'Запрос слишком большой',
                           max_iins=max_iins)
        items, problem = _parse_items(body)
        if problem:
            return _refuse(400, 'SIGN_LINKS_API_BAD_REQUEST', problem)
        if len(items) > max_iins:
            return _refuse(413, 'SIGN_LINKS_API_TOO_MANY',
                           'В одном запросе не больше %d ИИН' % max_iins, max_iins=max_iins)
        entries = [_entry(item) for item in items]

        # ── Место. Без ожидания: ждущий запрос держал бы нить так же, как идущий. ──
        if not slots.acquire(blocking=False):
            return _refuse(429, 'SIGN_LINKS_API_BUSY',
                           'Слишком много одновременных запросов — повторите через '
                           'пару секунд', headers={'Retry-After': str(RETRY_AFTER_SEC)})
        try:
            return _process(mark, entries)
        finally:
            slots.release()

    def _process(mark, entries):
        started = time.monotonic()
        # Повтор в списке спрашивается и пишется в журнал один раз, а ответ
        # получает каждая его строка.
        valid = list(dict.fromkeys(entry['iin'] for entry in entries if entry['iin']))
        typos = list(dict.fromkeys(
            (entry['logged'], entry['defect']) for entry in entries
            if entry['defect'] and entry['defect'] != 'empty'))

        # ── Дневной предел: до генератора и по верхней оценке. Два запроса,
        # пришедшие разом, могут перебрать его на пачку — это предохранитель, а
        # не счётчик оплаты.
        planned = len(valid) + len(typos)
        used = 0
        if planned:
            with db._get_cursor() as cursor:
                used = queries.api_used_today(cursor)
            if used + planned > daily_limit:
                logging.warning('sign_links: внешний вход (ключ %s) упёрся в дневной предел: '
                                'принято %d из %d, в запросе ещё %d', mark, used, daily_limit,
                                planned)
                return _refuse(429, 'SIGN_LINKS_API_DAILY_LIMIT',
                               'Дневной предел: в запросе ИИН больше, чем осталось на сегодня',
                               limits=_limits(used))

        # Курсор на время похода к генератору НЕ держим: пачка идёт секунды, а
        # слот пула на это время нужен остальному порталу.
        outcomes, stopped = batch.run(valid, generate, executor=executor) if valid else ({}, '')

        rows = []
        for value in valid:
            result = outcomes[value]
            if not result.get('asked'):
                continue  # генератор не спрашивали — в журнал писать нечего
            outcome, link = _settle(result)
            rows.append({
                'iin': value, 'outcome': outcome,
                'vendor_message': result.get('message'),
                'link_issued': bool(link),
                'link_host': sapar_link.link_host(link) if link else None,
                'error_text': result.get('error'),
                'latency_ms': result.get('latency_ms'),
            })
        rows.extend({'iin': logged, 'outcome': 'invalid', 'error_text': defect}
                    for logged, defect in typos)
        if rows:
            with db._get_cursor() as cursor:
                queries.log_api_requests(cursor, key_mark=mark, rows=rows,
                                         ip_address=_ip(), user_agent=_ua())

        results = [_answer(entry, outcomes) for entry in entries]
        summary = {status: 0 for status in STATUSES}
        for item in results:
            summary[item['status']] += 1
        # Без ИИН и без ссылок: только счёт, отпечаток ключа и время.
        logging.info('sign_links: внешний вход (ключ %s): ИИН %d — ссылок %d, без документов %d, '
                     'не ИИН %d, без ответа %d; %d мс%s', mark, len(results), summary[LINK],
                     summary[NOT_FOUND], summary[INVALID], summary[ERROR],
                     int((time.monotonic() - started) * 1000),
                     ', остановлена: %s' % stopped if stopped else '')
        return jsonify({
            "results": results,
            "summary": dict(summary, total=len(results)),
            "limits": _limits(used + len(rows)),
        }), 200

    return bp
