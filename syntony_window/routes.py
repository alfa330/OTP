"""HTTP-эндпоинты окна Syntony (Flask Blueprint /api/syntony).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2 сам
подключает этот модуль, обратный импорт был бы циклом (как у twofa/ и my_data/).

Ручки:
    POST   /window                 «дай окно»: одноразовая ссылка Syntony оператору из сессии
    GET    /access                 список доступа (супер-админ)
    GET    /access/people?q=       кого можно добавить (поиск по имени и логину)
    POST   /access                 добавить человека: {user_id, email?}
    PATCH  /access/<user_id>       сменить почту: {email}
    DELETE /access/<user_id>       убрать из списка
    POST   /access/<user_id>/check спросить у Syntony, связан ли оператор
    GET    /access/export          файл «список операторов» для Syntony

ДВА ВИДА ОШИБОК. /window отвечает так же, как Syntony: {"error": {"code", "message"}} —
и её ответом как есть, и нашими отказами (rules.ICORE_ERROR_CODES): экран окна разбирает
всё одним кодом. Ручки списка доступа — обычным видом портала: {"error": "...", "code": "..."}.

ОПЕРАТОР — ТОЛЬКО ИЗ СЕССИИ. /window читает из тела scope, phones и theme; id, ФИО и
почту сервер берёт сам (rules.clean_window_request). За человека вне списка доступа
запрос к Syntony не уходит: первый же запрос записал бы его у Syntony оператором.

ЗАПРОС К SYNTONY — БЕЗ ОТКРЫТОГО СОЕДИНЕНИЯ С БАЗОЙ. Сначала читаем и закрываем
транзакцию, потом идём в сеть, потом коротко пишем ответ о связи: десять секунд
ожидания чужого сервера не должны держать соединение пула.

ЗАПИСЬ У SYNTONY (ТЗ, задача 6) идёт сама: добавили человека в список или сменили ему
почту — сервер тут же запрашивает за него ссылку окна «Чаты» и не открывает её. Ответ
409 operator_not_linked здесь ожидаем: оператор записан и ждёт связи.
"""

import logging
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request, send_file

from . import access, client, queries, report, rules

logger = logging.getLogger(__name__)

_PEOPLE_LIMIT = 30


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bad(message, code, status=400, **extra):
    payload = {"error": message, "code": code}
    payload.update(extra)
    return jsonify(payload), status


def _window_error(code, message, status):
    return jsonify(rules.error_payload(code, message)), status


def _email_from_payload(payload, fallback):
    """Почта из тела запроса → (почта, ошибка). Ключа нет — fallback; пусто — «без почты»."""
    if 'email' not in payload or payload.get('email') is None:
        return fallback, None
    text = str(payload.get('email')).strip()
    if not text:
        return '', None
    email = rules.clean_email(text)
    if not email:
        return None, 'Проверьте адрес почты: такой адрес Syntony не найдёт'
    return email, None


def build_syntony_window_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                   resolve_requester, sensitive_access_granted,
                                   load_config=client.load_config,
                                   request_window=client.request_window,
                                   store=queries):
    """Собирает Blueprint.

    sensitive_access_granted — (user_id) -> bool: подтверждена ли ТЕКУЩАЯ сессия QR.
    Аргумент обязательный, без значения по умолчанию: забытая зависимость должна
    уронить сборку блюпринта на старте, а не тихо открыть окно без QR.

    load_config, request_window и store — швы для тестов: ключ и сеть (client.py) и
    SQL (queries.py).
    """
    bp = Blueprint('syntony_window', __name__, url_prefix='/api/syntony')

    def syntony_route(rule, methods=('GET',), manage=False):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    with db._get_cursor() as cursor:
                        ctx = store.load_access_context(cursor, requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    if manage and not access.can_manage(ctx):
                        return _bad('Список доступа к окну Syntony ведут супер-админы',
                                    'SYNTONY_MANAGE_FORBIDDEN', 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception:  # noqa: BLE001
                    # Без тела запроса и ответа в журнале: там почта оператора и ссылка окна.
                    logger.exception('syntony_window: ошибка в %s %s', request.method, rule)
                    return jsonify({"error": "Внутренняя ошибка окна Syntony"}), 500

            return wrapper

        return decorator

    # ── Разговор с Syntony ───────────────────────────────────────────────────

    def _ask(window, *, user_id, name, email):
        """Запросить ссылку за человека. None — ключ Syntony на сервере не задан."""
        config = load_config()
        if config is None:
            return None
        body = rules.build_request_body(window, user_id=user_id, name=name, email=email)
        return request_window(config, body)

    def _remember_link(user_id, reply, known_state):
        """Записать, что Syntony ответила о связи, — только если ответ о ней и он новый."""
        state = rules.link_state_of(reply.status, reply.body)
        if not state or state == known_state:
            return
        try:
            with db._get_cursor() as cursor:
                store.set_link_state(cursor, user_id, state)
        except Exception:  # noqa: BLE001
            # Отметка о связи — справка для списка доступа; окно из-за неё не падает.
            logger.exception('syntony_window: не записалось состояние связи пользователя %s', user_id)

    def _note_reply(what, user_id, reply):
        code = reply.code
        if not code:
            return
        if code not in rules.SYNTONY_ERROR_CODES and code not in rules.ICORE_ERROR_CODES:
            logger.warning('syntony_window: незнакомый код ошибки Syntony %r (HTTP %s, %s, пользователь %s)',
                           code[:64], reply.status, what, user_id)
        else:
            logger.info('syntony_window: %s, пользователь %s — %s (HTTP %s)',
                        what, user_id, code, reply.status)

    def _syntony_note(reply):
        """Что сказать ведущему список о разговоре с Syntony — рядом со строкой человека."""
        if reply is None:
            return {'configured': False, 'code': rules.CODE_NOT_CONFIGURED,
                    'message': client.NOT_CONFIGURED_MESSAGE}
        state = rules.link_state_of(reply.status, reply.body)
        if state:
            return {'configured': True, 'code': '', 'message': ''}
        error = reply.body.get('error') if isinstance(reply.body.get('error'), dict) else {}
        return {'configured': True, 'code': reply.code,
                'message': str(error.get('message') or '')[:300]}

    def _register(user_id):
        """Записать человека у Syntony и вернуть (строка списка, заметка о разговоре).

        Человека уже нет в списке — (None, None); он уволен — (его строка, None): в обоих
        случаях спрашивать Syntony не о ком, и заметки нет.
        """
        with db._get_cursor() as cursor:
            item = store.get_item(cursor, user_id)
        if not item or not item['working']:
            return item, None
        reply = _ask(rules.registration_window(), user_id=item['id'], name=item['name'],
                     email=item['email'])
        if reply is not None:
            _note_reply('запись оператора', item['id'], reply)
            _remember_link(item['id'], reply, item['link_state'])
            with db._get_cursor() as cursor:
                item = store.get_item(cursor, user_id) or item
        return item, _syntony_note(reply)

    # ── Окно ─────────────────────────────────────────────────────────────────

    @syntony_route('/window', methods=('POST',))
    def syntony_window(ctx):
        user_id = ctx['user_id']
        if not access.can_request_window(ctx):
            message = ('Вас нет в списке доступа к окну Syntony. Добавьте себя кнопкой «Доступ».'
                       if access.can_manage(ctx) else 'Окно Syntony вам не открыто.')
            return _window_error(rules.CODE_NO_ACCESS, message, 403)
        if access.requires_qr(ctx) and not sensitive_access_granted(user_id):
            return _window_error(rules.CODE_QR_REQUIRED,
                                 'Окно Syntony открывается после подтверждения QR-кода старшим.', 403)
        window, error = rules.clean_window_request(request.get_json(silent=True))
        if error:
            return jsonify(error), 400
        reply = _ask(window, user_id=user_id, name=ctx['name'], email=ctx['email'])
        if reply is None:
            reply = client.not_configured_reply()
        else:
            _remember_link(user_id, reply, ctx['link_state'])
        _note_reply('окно «%s»' % window['scope'], user_id, reply)
        # Ответ Syntony — как есть: { url, expiresAt } или { error: { code, message } }.
        return jsonify(reply.body), reply.status

    # ── Список доступа ───────────────────────────────────────────────────────

    @syntony_route('/access', manage=True)
    def syntony_access_list(ctx):
        with db._get_cursor() as cursor:
            items = store.list_access(cursor)
        return jsonify({"status": "success", "items": items,
                        "configured": load_config() is not None}), 200

    @syntony_route('/access/people', manage=True)
    def syntony_access_people(ctx):
        with db._get_cursor() as cursor:
            people = store.search_people(cursor, request.args.get('q', ''), limit=_PEOPLE_LIMIT)
        return jsonify({"status": "success", "people": people}), 200

    def _email_taken(owner):
        # Одна почта — один сотрудник Syntony: двух операторов iCORE на неё не связать.
        return _bad('Эта почта уже указана у сотрудника «%s»' % owner, 'SYNTONY_EMAIL_TAKEN', 409)

    @syntony_route('/access', methods=('POST',), manage=True)
    def syntony_access_grant(ctx):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _bad('Не указан сотрудник', 'SYNTONY_BAD_REQUEST')
        user_id = _int(payload.get('user_id'))
        if user_id is None or user_id <= 0:
            return _bad('Не указан сотрудник', 'SYNTONY_BAD_REQUEST')
        with db._get_cursor() as cursor:
            person = store.person(cursor, user_id)
            if not person:
                return _bad('Сотрудник не найден', 'SYNTONY_PERSON_NOT_FOUND', 404)
            if not person['working']:
                return _bad('Сотрудник уволен — доступ ему не выдать', 'SYNTONY_PERSON_FIRED', 409)
            email, problem = _email_from_payload(payload, person['card_email'])
            if problem:
                return _bad(problem, 'SYNTONY_INVALID_EMAIL')
            owner = store.email_owner(cursor, email, user_id)
            if owner:
                return _email_taken(owner)
            if store.grant(cursor, user_id, email, ctx['user_id']):
                store.log(cursor, 'grant', user_id=user_id, user_name=person['name'], email=email,
                          actor_id=ctx['user_id'], actor_name=ctx['name'])
        item, note = _register(user_id)
        return jsonify({"status": "success", "item": item, "syntony": note}), 200

    @syntony_route('/access/<int:user_id>', methods=('PATCH',), manage=True)
    def syntony_access_email(user_id, ctx):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or 'email' not in payload:
            return _bad('Не указана почта', 'SYNTONY_BAD_REQUEST')
        email, problem = _email_from_payload(payload, '')
        if problem:
            return _bad(problem, 'SYNTONY_INVALID_EMAIL')
        with db._get_cursor() as cursor:
            owner = store.email_owner(cursor, email, user_id)
            if owner:
                return _email_taken(owner)
            changed = store.set_email(cursor, user_id, email)
            if changed is None:
                return _bad('Сотрудника нет в списке доступа', 'SYNTONY_NOT_LISTED', 404)
            if changed:
                person = store.person(cursor, user_id) or {}
                store.log(cursor, 'email', user_id=user_id, user_name=person.get('name'), email=email,
                          actor_id=ctx['user_id'], actor_name=ctx['name'])
        # Новая почта — новая запись у Syntony: по ней оператора и будут искать.
        item, note = _register(user_id)
        return jsonify({"status": "success", "item": item, "syntony": note}), 200

    @syntony_route('/access/<int:user_id>', methods=('DELETE',), manage=True)
    def syntony_access_revoke(user_id, ctx):
        with db._get_cursor() as cursor:
            person = store.person(cursor, user_id) or {}
            email = store.revoke(cursor, user_id)
            if email is not None:
                store.log(cursor, 'revoke', user_id=user_id, user_name=person.get('name'), email=email,
                          actor_id=ctx['user_id'], actor_name=ctx['name'])
        return jsonify({"status": "success"}), 200

    @syntony_route('/access/<int:user_id>/check', methods=('POST',), manage=True)
    def syntony_access_check(user_id, ctx):
        item, note = _register(user_id)
        if not item:
            return _bad('Сотрудника нет в списке доступа', 'SYNTONY_NOT_LISTED', 404)
        if note is None:
            return _bad('Сотрудник уволен — у Syntony о нём не спрашиваем', 'SYNTONY_PERSON_FIRED', 409)
        return jsonify({"status": "success", "item": item, "syntony": note}), 200

    @syntony_route('/access/export', manage=True)
    def syntony_access_export(ctx):
        with db._get_cursor() as cursor:
            items = store.list_access(cursor)
        today = datetime.now(ZoneInfo('Asia/Almaty')).date()
        return send_file(report.build_workbook(items), mimetype=report.XLSX_MIME,
                         as_attachment=True, download_name=report.export_file_name(today))

    return bp
