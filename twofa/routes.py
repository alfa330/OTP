"""HTTP-эндпоинты раздела «Коды 2FA» (Flask Blueprint /api/twofa).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2 сам
подключает этот модуль, обратный импорт был бы циклом (как у thermoboxes/).

Соглашения портала: методы включают OPTIONS и первым делом отдаётся preflight,
авторизация — общий require_api_key, ошибка — {"error": "...", "code": "..."}.

Пишет только сервис (service.py), и каждый его шаг сначала проверяет всё, а потом пишет:
`db._get_cursor()` коммитит и то, что записано до ответа 4xx.

Ручки:
    GET    ''                              экран: права, аккаунты, запросы, люди
    POST   /requests                       запросить код (сотрудник с доступом)
    POST   /requests/<id>/approve|reject   решение главы маркетинга
    GET    /journal                        журнал (глава, супер-админ), порциями
    POST   /accounts/preview               разобрать QR или ключ — без записи
    POST   /accounts                       сохранить аккаунты из того же текста — в раздел
    PATCH  /accounts/<id>                  название, сервис, раздел
    DELETE /accounts/<id>                  удалить аккаунт вместе с ключом
    POST   /groups                         новый раздел (сразу с аккаунтами или пустой)
    PATCH  /groups/<id>                    название и состав раздела
    DELETE /groups/<id>                    удалить раздел — аккаунты остаются без раздела
    GET    /people                         кому можно выдать доступ (поиск)
    PUT    /people/<user_id>/grants        набор аккаунтов человека целиком
"""

import logging
import threading
import time
from functools import wraps

from flask import Blueprint, jsonify, request

from . import access, crypto, otpauth, queries, rules
from .service import KEEP, public_request

# Экспорт Google Authenticator — до 10 аккаунтов в одном QR; полсотни — заведомо не QR.
_MAX_IMPORT = 50

_ERRORS = {
    'forbidden': ('Этот аккаунт вам не выдан', 'TWOFA_REQUEST_FORBIDDEN', 403),
    'no_telegram': ('Код приходит в Telegram, а он у вас не привязан. Войдите в бота портала своим '
                    'логином и паролем, потом запросите код', 'TWOFA_NO_TELEGRAM', 409),
    'no_approver': ('Подтвердить запрос сейчас некому — обратитесь к руководству', 'TWOFA_NO_APPROVER', 409),
    'not_found': ('Не найдено', 'TWOFA_NOT_FOUND', 404),
    'expired': ('Запрос уже сгорел: прошло 10 минут', 'TWOFA_EXPIRED', 409),
    'cancelled': ('Запрос отменён: у сотрудника больше нет доступа к аккаунту', 'TWOFA_CANCELLED', 409),
    'key_error': ('Ключ аккаунта не расшифровывается — код не выдан', 'TWOFA_KEY_ERROR', 409),
    'key_missing': ('Ключ шифрования раздела не задан — подтвердить нельзя', 'TWOFA_KEY_MISSING', 409),
}


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


def _outcome_error(outcome, **extra):
    message, code, status = _ERRORS.get(outcome, ('Не получилось', 'TWOFA_FAILED', 409))
    return _bad(message, code, status, **extra)


class _BotName:
    """Имя бота для ссылки в разделе. Не узнали — спросим снова не раньше чем через 5 минут:
    экран не должен ждать Telegram на каждом открытии."""

    def __init__(self, tg):
        self._tg = tg
        self._value = None
        self._tried_at = 0.0
        self._lock = threading.Lock()

    def get(self):
        if self._value or time.time() - self._tried_at < 300:
            return self._value
        with self._lock:
            if not self._value and time.time() - self._tried_at >= 300:
                self._tried_at = time.time()
                try:
                    self._value = self._tg.username()
                except Exception:  # noqa: BLE001
                    self._value = None
        return self._value


def build_twofa_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester, service):
    bp = Blueprint('twofa', __name__, url_prefix='/api/twofa')
    bot_name = _BotName(service.tg)

    def twofa_route(rule, methods=('GET',), need=None):
        """need='manage' — аккаунты, доступы, журнал; need='approve' — решение главы."""
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
                    ctx = service.context(requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    if not access.can_open_section(ctx):
                        return _bad('Раздел «Коды 2FA» вам не открыт', 'TWOFA_SECTION_CLOSED', 403)
                    if need == 'manage' and not access.can_manage(ctx):
                        return _bad('Аккаунты и доступы ведёт глава маркетинга', 'TWOFA_MANAGE_FORBIDDEN', 403)
                    if need == 'approve' and not access.can_approve(ctx):
                        return _bad('Запросы подтверждают глава маркетинга и супер-админы',
                                    'TWOFA_APPROVE_FORBIDDEN', 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except crypto.KeyMissing:
                    return _bad('Не задан ключ шифрования раздела (TWOFA_SECRET_KEY)', 'TWOFA_KEY_MISSING', 409)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('twofa: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Коды 2FA»",
                        "detail": type(exc).__name__,
                    }), 500

            return wrapper

        return decorator

    def _payload():
        return request.get_json(silent=True) or {}

    # ─── Экран ───────────────────────────────────────────────────────────────

    @twofa_route('')
    def twofa_screen(ctx):
        caps = access.capabilities(ctx)
        now_dt = rules.almaty(service.now())
        with db._get_cursor() as cursor:
            lead = queries.approver(cursor)
            people_who_approve = queries.approvers(cursor)
            notify = queries.notify_enabled(cursor, ctx['user_id']) if caps['can_approve'] else False
            if caps['can_manage']:
                accounts = queries.list_accounts(cursor)
                groups = queries.list_groups(cursor)
                grants = queries.grants_by_account(cursor)
                for account in accounts:
                    account['user_ids'] = grants.get(account['id'], [])
                people = queries.people_with_access(cursor)
                pending = queries.pending_requests(cursor, now_dt)
                recent = queries.requests_list(cursor, limit=30)
            else:
                accounts = queries.list_accounts(cursor, ids=access.granted_ids(ctx))
                # Сотруднику — только разделы его аккаунтов: заголовки над карточками.
                groups = queries.list_groups(cursor, ids=[a['group_id'] for a in accounts if a['group_id']])
                people, pending, recent = [], [], []
            mine = queries.requests_list(cursor, requester_id=ctx['user_id'], limit=10) if caps['can_request'] else []
        for account in accounts:
            account['created_at'] = rules.iso(account['created_at'])
        return jsonify({
            'capabilities': caps,
            'key_status': ctx['key_status'],
            'me': {'id': ctx['user_id'], 'name': ctx['name'], 'telegram_linked': bool(ctx['telegram_id'])},
            'approver': ({'id': lead['id'], 'name': lead['name'], 'telegram_linked': bool(lead['telegram_id'])}
                         if lead else None),
            # Есть ли вообще кому подтверждать (глава или супер-админ) — без этого кнопка
            # «Запросить код» обещала бы то, чего не будет.
            'approvers_available': bool(people_who_approve),
            # Тумблер «уведомления о запросах» — только тому, кто подтверждает.
            'notify': {'enabled': notify} if caps['can_approve'] else None,
            'bot_username': bot_name.get() if caps['can_request'] or caps['can_manage'] else None,
            'limits': {
                'request_ttl_seconds': rules.REQUEST_TTL_SECONDS,
                'resend_window_seconds': rules.RESEND_WINDOW_SECONDS,
                'code_visible_seconds': rules.CODE_VISIBLE_SECONDS,
            },
            'accounts': accounts,
            'groups': groups,
            'people': people,
            'pending': [public_request(r) for r in pending],
            'recent': [public_request(r) for r in recent],
            'my_requests': [public_request(r) for r in mine],
            'server_time': rules.iso(now_dt),
        })

    # ─── Запрос и решение ────────────────────────────────────────────────────

    @twofa_route('/requests', methods=('POST',))
    def twofa_request_create(ctx):
        account_id = _int(_payload().get('account_id'))
        if not account_id:
            return _bad('Не выбран аккаунт', 'TWOFA_BAD_INPUT')
        result = service.request_code(ctx, account_id)
        outcome = result['outcome']
        if outcome == 'created':
            return jsonify({'request': public_request(result['request'])}), 201
        if outcome == 'pending':
            return _bad('Запрос по этому аккаунту уже ждёт подтверждения', 'TWOFA_PENDING', 409,
                        request=public_request(result['request']) if result.get('request') else None)
        return _outcome_error(outcome)

    def _decide(ctx, request_id, decision):
        result = service.decide(ctx, request_id, decision, 'portal')
        outcome = result['outcome']
        if outcome in ('approved', 'rejected'):
            delivered = result.get('delivered')
            return jsonify({'request': public_request(result['request']), 'delivered': delivered,
                            'delivery_reason': (service.delivery_reason(result['request'].get('delivery_error'))
                                                if delivered is False else None)})
        if outcome == 'already':
            req = result['request']
            return _bad('Запрос уже решён: %s' % rules.STATUS_LABELS.get(req['status'], req['status']).lower(),
                        'TWOFA_ALREADY', 409, request=public_request(req))
        return _outcome_error(outcome)

    @twofa_route('/requests/<int:request_id>/approve', methods=('POST',), need='approve')
    def twofa_request_approve(ctx, request_id):
        return _decide(ctx, request_id, 'approve')

    @twofa_route('/requests/<int:request_id>/reject', methods=('POST',), need='approve')
    def twofa_request_reject(ctx, request_id):
        return _decide(ctx, request_id, 'reject')

    # ─── Тумблер уведомлений ─────────────────────────────────────────────────

    @twofa_route('/notify', methods=('PUT',), need='approve')
    def twofa_notify(ctx):
        enabled = _payload().get('enabled')
        if not isinstance(enabled, bool):
            return _bad('Нужно enabled: true или false', 'TWOFA_BAD_INPUT')
        return jsonify({'enabled': service.set_notify(ctx, enabled)})

    # ─── Журнал ──────────────────────────────────────────────────────────────

    @twofa_route('/journal', need='manage')
    def twofa_journal(ctx):
        before = _int(request.args.get('before'))
        with db._get_cursor() as cursor:
            items, has_more = queries.journal(cursor, before_id=before, limit=50)
        allowed_details = ('via', 'valid_until', 'error', 'from', 'to', 'service',
                           'group', 'from_group', 'to_group', 'accounts')
        return jsonify({
            'items': [{
                'id': item['id'],
                'kind': item['kind'],
                'label': rules.EVENT_LABELS.get(item['kind'], item['kind']),
                'at': rules.iso(item['created_at']),
                'actor_name': item['actor_name'],
                'system': item['kind'] in rules.SYSTEM_EVENTS,
                'subject_name': item['subject_name'],
                'account_title': item['account_title'],
                'request_id': item['request_id'],
                'details': {k: v for k, v in (item['details'] or {}).items() if k in allowed_details},
            } for item in items],
            'has_more': has_more,
        })

    # ─── Аккаунты ────────────────────────────────────────────────────────────

    def _parsed(text):
        try:
            return otpauth.parse(text), None
        except otpauth.ParseError as exc:
            return None, _bad(str(exc), 'TWOFA_PARSE')

    def _group_choice(body):
        """Раздел из окна: group_title — новый (или одноимённый существующий), group_id —
        существующий, group_id: null — без раздела, ни того ни другого — KEEP. field='group' —
        окну, к какому полю ошибка."""
        if body.get('group_title') is not None:
            try:
                return {'title': rules.clean_group_title(body.get('group_title'))}, None
            except ValueError as exc:
                return None, _bad(str(exc), 'TWOFA_BAD_GROUP', field='group')
        if 'group_id' in body:
            if body['group_id'] is None:
                return None, None
            group_id = _int(body['group_id'])
            if group_id is None:
                return None, _bad('Неверный раздел', 'TWOFA_BAD_GROUP', field='group')
            return {'id': group_id}, None
        return KEEP, None

    def _group_problem(outcome, title=None):
        if outcome == 'group_not_found':
            return _bad('Раздел удалён — выберите другой', 'TWOFA_GROUP_NOT_FOUND', 409, field='group')
        if outcome == 'group_exists':
            return _bad('Раздел «%s» уже есть' % title, 'TWOFA_GROUP_EXISTS', 409, field='title')
        if outcome == 'unknown_account':
            return _bad('Аккаунт удалён — обновите раздел', 'TWOFA_BAD_INPUT')
        if outcome == 'not_found':
            return _outcome_error('not_found')
        return None

    @twofa_route('/accounts/preview', methods=('POST',), need='manage')
    def twofa_accounts_preview(ctx):
        parsed, error = _parsed(_payload().get('text'))
        if error:
            return error
        # Уже добавленный ключ (тот же QR экспорта отсканировали второй раз) — отмечаем в
        # предпросмотре, чтобы не завести дубль. Без ключа шифрования сверить нечем.
        duplicates = {}
        if crypto.key_status() == 'ok':
            with db._get_cursor() as cursor:
                duplicates = service.duplicates(cursor, [entry['secret'] for entry in parsed['entries']])
        return jsonify({
            'kind': parsed['kind'],
            'batch': parsed['batch'],
            'entries': [dict(otpauth.public(entry), index=index, title=rules.suggested_title(entry),
                             duplicate_of=duplicates.get(index))
                        for index, entry in enumerate(parsed['entries'])],
        })

    @twofa_route('/accounts', methods=('POST',), need='manage')
    def twofa_accounts_create(ctx):
        if crypto.key_status() != 'ok':
            raise crypto.KeyMissing()
        body = _payload()
        parsed, error = _parsed(body.get('text'))
        if error:
            return error
        items = body.get('items')
        if not isinstance(items, list) or not items:
            return _bad('Не выбраны аккаунты', 'TWOFA_BAD_INPUT')
        if len(items) > _MAX_IMPORT:
            return _bad('Слишком много аккаунтов за раз', 'TWOFA_BAD_INPUT')
        entries, seen = [], set()
        for item in items:
            index = _int((item or {}).get('index'))
            if index is None or not 0 <= index < len(parsed['entries']) or index in seen:
                return _bad('Аккаунт из QR не найден — отсканируйте QR заново', 'TWOFA_BAD_INPUT')
            seen.add(index)
            entry = parsed['entries'][index]
            if not entry['supported']:
                return _bad(entry['reason'], 'TWOFA_UNSUPPORTED', index=index)
            try:
                title = rules.clean_title(item.get('title'))
                service_name = rules.clean_service(item.get('service'))
            except ValueError as exc:
                return _bad(str(exc), 'TWOFA_BAD_INPUT', index=index)
            entries.append({'title': title, 'service': service_name, 'secret': entry['secret']})
        choice, error = _group_choice(body)
        if error:
            return error
        result = service.add_accounts(ctx, entries, group=None if choice is KEEP else choice)
        problem = _group_problem(result['outcome'])
        if problem:
            return problem
        if result['outcome'] == 'duplicate':
            position = items[result['index']].get('index')
            return _bad('Этот ключ уже добавлен как «%s»' % result['title'], 'TWOFA_DUPLICATE', 409, index=position)
        return jsonify({'created': result['created'], 'group_id': result['group_id']}), 201

    @twofa_route('/accounts/<int:account_id>', methods=('PATCH', 'DELETE'), need='manage')
    def twofa_account_edit(ctx, account_id):
        if request.method == 'DELETE':
            if not service.remove_account(ctx, account_id):
                return _outcome_error('not_found')
            return jsonify({'ok': True})
        body = _payload()
        try:
            title = rules.clean_title(body.get('title'))
            service_name = rules.clean_service(body.get('service'))
        except ValueError as exc:
            return _bad(str(exc), 'TWOFA_BAD_INPUT')
        choice, error = _group_choice(body)
        if error:
            return error
        result = service.update_account(ctx, account_id, title, service_name, group=choice)
        return _group_problem(result['outcome']) or jsonify({'ok': True})

    # ─── Разделы ─────────────────────────────────────────────────────────────

    def _account_ids(raw):
        """Состав раздела: None — не трогать; иначе список id без повторов."""
        if raw is None:
            return None, None
        if not isinstance(raw, list):
            return None, _bad('Нужен список аккаунтов', 'TWOFA_BAD_INPUT')
        ids = [_int(x) for x in raw]
        if any(x is None for x in ids) or len(set(ids)) != len(ids):
            return None, _bad('Неверный список аккаунтов', 'TWOFA_BAD_INPUT')
        return ids, None

    def _group_title(body):
        try:
            return rules.clean_group_title(body.get('title')), None
        except ValueError as exc:
            return None, _bad(str(exc), 'TWOFA_BAD_GROUP', field='title')

    @twofa_route('/groups', methods=('POST',), need='manage')
    def twofa_group_create(ctx):
        body = _payload()
        title, error = _group_title(body)
        if error:
            return error
        account_ids, error = _account_ids(body.get('account_ids', []))
        if error:
            return error
        result = service.create_group(ctx, title, account_ids or [])
        return _group_problem(result['outcome'], title) or (jsonify({'id': result['id']}), 201)

    @twofa_route('/groups/<int:group_id>', methods=('PATCH', 'DELETE'), need='manage')
    def twofa_group_edit(ctx, group_id):
        if request.method == 'DELETE':
            result = service.delete_group(ctx, group_id)
            return _group_problem(result['outcome']) or jsonify({'ok': True, 'accounts': result['accounts']})
        body = _payload()
        title = None
        if 'title' in body:
            title, error = _group_title(body)
            if error:
                return error
        account_ids, error = _account_ids(body.get('account_ids'))
        if error:
            return error
        result = service.update_group(ctx, group_id, title=title, account_ids=account_ids)
        return _group_problem(result['outcome'], title) or jsonify({'ok': True})

    # ─── Доступы ─────────────────────────────────────────────────────────────

    @twofa_route('/people', need='manage')
    def twofa_people(ctx):
        with db._get_cursor() as cursor:
            people = queries.search_people(cursor, request.args.get('q'), limit=30)
        return jsonify({'people': people})

    @twofa_route('/people/<int:user_id>/grants', methods=('PUT',), need='manage')
    def twofa_person_grants(ctx, user_id):
        raw = _payload().get('account_ids')
        if not isinstance(raw, list):
            return _bad('Нужен список аккаунтов', 'TWOFA_BAD_INPUT')
        account_ids = [_int(x) for x in raw]
        if any(x is None for x in account_ids) or len(set(account_ids)) != len(account_ids):
            return _bad('Неверный список аккаунтов', 'TWOFA_BAD_INPUT')
        result = service.set_person_grants(ctx, user_id, account_ids)
        if result['outcome'] == 'not_found':
            return _outcome_error('not_found')
        if result['outcome'] == 'unknown_account':
            return _bad('Аккаунт удалён — обновите раздел', 'TWOFA_BAD_INPUT')
        if result['outcome'] == 'not_working':
            return _bad('Сотрудник не работает — доступ ему не выдаётся', 'TWOFA_NOT_WORKING', 409)
        return jsonify({'added': result['added'], 'removed': result['removed']})

    return bp
