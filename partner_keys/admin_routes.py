# -*- coding: utf-8 -*-
"""Ключи партнёров и их журнал — вкладка «Ключи» раздела «Сессии».

    GET   /api/admin/partner-keys                 список ключей + справочник областей
    POST  /api/admin/partner-keys                 {name, scopes} → ключ (показывается один раз)
    PATCH /api/admin/partner-keys/<id>            {scopes} — поменять области доступа
    POST  /api/admin/partner-keys/<id>/revoke     отозвать
    GET   /api/admin/partner-keys/<id>/calls      журнал вызовов ключа, страницей

Только супер-админ: ключ открывает ручки портала стороннему сервису, и выдавать
его — решение владельца портала, а не главы отдела. Раздел «Сессии» при этом
открыт всем админам — вкладка просто не показывается остальным, а ручки
отвечают 403.

Права ручки знаний (otp_role + department_id) у ключа — оператор отдела
техподдержки (departments.code = szov): решение владельца 29.10.2026 («их ИИ —
тот же оператор техподдержки»). Они лежат в строке ключа, чтобы менять без
правки кода; шире оператора не выдаются.
"""

import logging
from functools import wraps

from flask import Blueprint, jsonify, request

from . import keys, queries, schema, scopes

DEFAULT_DEPARTMENT_CODE = 'szov'
DEFAULT_ROLE = 'operator'
NAME_MAX = 120
CALLS_PAGE_MAX = 200


def build_partner_keys_admin_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                       resolve_requester, normalize_role):
    bp = Blueprint('partner_keys_admin', __name__, url_prefix='/api/admin/partner-keys')

    def guarded(handler):
        @wraps(handler)
        def wrapper(*args, **kwargs):
            if request.method == 'OPTIONS':
                return build_cors_preflight_response()
            requester_id, requester, error = resolve_requester()
            if error:
                message, status = error
                return jsonify({'error': message}), status
            if normalize_role(requester[3]) != 'super_admin':
                return jsonify({'error': 'Ключи партнёров выпускает только супер-админ',
                                'code': 'PARTNER_KEYS_FORBIDDEN'}), 403
            try:
                with db._get_cursor() as cursor:
                    if not schema.schema_is_ready(cursor):
                        return jsonify({'error': 'Схема ключей не развёрнута',
                                        'code': 'PARTNER_KEYS_NOT_READY'}), 503
                    return handler(cursor, requester_id, *args, **kwargs)
            except Exception:                            # noqa: BLE001
                logging.exception('partner_keys: ручка ключей упала')
                return jsonify({'error': 'Внутренняя ошибка раздела ключей'}), 500
        return wrapper

    def _public(key, last_day=None):
        item = {field: key[field] for field in (
            'id', 'name', 'mark', 'scopes', 'created_at', 'created_by_name', 'last_used_at',
            'calls_count', 'revoked_at', 'revoked_by_name', 'active')}
        item['scope_titles'] = scopes.titles(key['scopes'])
        item['acts_as'] = {'role': key['otp_role'], 'department_id': key['department_id'],
                           'department': key['department_name'],
                           'department_code': key['department_code']}
        item['calls_last_day'] = int((last_day or {}).get(key['id'], 0))
        return item

    def _scopes_from(body):
        """(список областей, ошибка). Пустой список — ошибка: ключ без дверей бессмыслен."""
        try:
            chosen = scopes.normalize(body.get('scopes'))
        except ValueError as error:
            return None, str(error)
        if not chosen:
            return None, 'Выберите хотя бы одну область доступа'
        return chosen, None

    @bp.route('', methods=['GET', 'OPTIONS'])
    @require_api_key
    @guarded
    def partner_keys_list(cursor, requester_id):
        last_day = queries.calls_last_day(cursor)
        return jsonify({'keys': [_public(key, last_day) for key in queries.list_keys(cursor)],
                        'scopes': scopes.catalog(), 'header': keys.HEADER})

    @bp.route('', methods=['POST', 'OPTIONS'])
    @require_api_key
    @guarded
    def partner_keys_create(cursor, requester_id):
        body = request.get_json(silent=True) or {}
        name = ' '.join(str(body.get('name') or '').split())
        if not name:
            return jsonify({'error': 'Нужно название ключа — кому он выдан'}), 400
        if len(name) > NAME_MAX:
            return jsonify({'error': 'Название длиннее %d знаков' % NAME_MAX}), 400
        chosen, problem = _scopes_from(body)
        if problem:
            return jsonify({'error': problem}), 400
        department_code = str(body.get('department_code') or DEFAULT_DEPARTMENT_CODE).strip().lower()
        cursor.execute('SELECT id, name FROM departments WHERE lower(code) = %s', (department_code,))
        department = cursor.fetchone()
        if not department:
            return jsonify({'error': 'Отдел «%s» не найден' % department_code}), 400
        role = normalize_role(body.get('otp_role') or DEFAULT_ROLE) or DEFAULT_ROLE
        if role not in ('operator', 'trainee'):
            # Ключ — это оператор: более широкие права стороннему сервису не выдаются.
            return jsonify({'error': 'Ключ действует только от имени оператора'}), 400
        secret = keys.generate()
        key_id = queries.create_key(cursor, name=name, key_hash=keys.digest(secret),
                                    key_mark=keys.mark(secret), scopes=chosen, otp_role=role,
                                    department_id=int(department[0]), created_by=requester_id)
        logging.info('partner_keys: выпущен ключ #%s «%s» (%s; отпечаток %s) пользователем %s',
                     key_id, name, ', '.join(chosen), keys.mark(secret), requester_id)
        created = queries.get_key(cursor, key_id)
        return jsonify({'status': 'success', 'key': _public(created), 'secret': secret,
                        'header': keys.HEADER}), 201

    @bp.route('/<int:key_id>', methods=['PATCH', 'OPTIONS'])
    @require_api_key
    @guarded
    def partner_keys_update(cursor, requester_id, key_id):
        body = request.get_json(silent=True) or {}
        chosen, problem = _scopes_from(body)
        if problem:
            return jsonify({'error': problem}), 400
        if not queries.set_scopes(cursor, key_id, chosen):
            existing = queries.get_key(cursor, key_id)
            if existing is None:
                return jsonify({'error': 'Ключ не найден'}), 404
            return jsonify({'error': 'Ключ отозван — области ему уже не нужны'}), 409
        logging.info('partner_keys: ключу #%s выданы области %s пользователем %s',
                     key_id, ', '.join(chosen), requester_id)
        return jsonify({'status': 'success', 'key': _public(queries.get_key(cursor, key_id))})

    @bp.route('/<int:key_id>/revoke', methods=['POST', 'OPTIONS'])
    @require_api_key
    @guarded
    def partner_keys_revoke(cursor, requester_id, key_id):
        if not queries.revoke_key(cursor, key_id, by=requester_id):
            existing = queries.get_key(cursor, key_id)
            if existing is None:
                return jsonify({'error': 'Ключ не найден'}), 404
            return jsonify({'status': 'success', 'key': _public(existing), 'already': True})
        logging.info('partner_keys: ключ #%s отозван пользователем %s', key_id, requester_id)
        return jsonify({'status': 'success', 'key': _public(queries.get_key(cursor, key_id))})

    @bp.route('/<int:key_id>/calls', methods=['GET', 'OPTIONS'])
    @require_api_key
    @guarded
    def partner_keys_calls(cursor, requester_id, key_id):
        key = queries.get_key(cursor, key_id)
        if key is None:
            return jsonify({'error': 'Ключ не найден'}), 404
        try:
            limit = int(request.args.get('limit') or 50)
            offset = int(request.args.get('offset') or 0)
        except ValueError:
            return jsonify({'error': 'limit и offset должны быть числами'}), 400
        limit = max(1, min(limit, CALLS_PAGE_MAX))
        offset = max(0, offset)
        items, total = queries.list_calls(cursor, key_id=key_id, limit=limit, offset=offset)
        return jsonify({'key': _public(key), 'calls': items,
                        'pagination': {'limit': limit, 'offset': offset, 'total': total,
                                       'has_more': offset + len(items) < total}})

    return bp
