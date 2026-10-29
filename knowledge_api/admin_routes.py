# -*- coding: utf-8 -*-
"""Ключи партнёров и их журнал — вкладка «Ключи» раздела «Сессии».

    GET  /api/admin/knowledge-keys                 список ключей
    POST /api/admin/knowledge-keys                 {name} → ключ (показывается один раз)
    POST /api/admin/knowledge-keys/<id>/revoke     отозвать
    GET  /api/admin/knowledge-keys/<id>/calls      журнал вызовов ключа, страницей

Только супер-админ: ключ открывает базу знаний стороннему сервису, и выдавать
его — решение владельца портала, а не главы отдела. Раздел «Сессии» при этом
открыт всем админам — вкладка просто не показывается остальным, а ручки
отвечают 403.

Ключ действует от имени оператора отдела техподдержки (departments.code = szov):
так решил владелец 29.10.2026 («их ИИ — тот же оператор техподдержки»). Отдел и
роль лежат в строке ключа, чтобы их можно было поменять без правки кода.
"""

import logging
from functools import wraps

from flask import Blueprint, jsonify, request

from . import keys, queries, schema

DEFAULT_DEPARTMENT_CODE = 'szov'
DEFAULT_ROLE = 'operator'
NAME_MAX = 120
CALLS_PAGE_MAX = 200


def build_knowledge_admin_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                    resolve_requester, normalize_role):
    bp = Blueprint('knowledge_api_admin', __name__, url_prefix='/api/admin/knowledge-keys')

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
                                'code': 'KNOWLEDGE_KEYS_FORBIDDEN'}), 403
            try:
                with db._get_cursor() as cursor:
                    if not schema.schema_is_ready(cursor):
                        return jsonify({'error': 'Схема ключей не развёрнута',
                                        'code': 'KNOWLEDGE_KEYS_NOT_READY'}), 503
                    return handler(cursor, requester_id, *args, **kwargs)
            except Exception:                            # noqa: BLE001
                logging.exception('knowledge_api: ручка ключей упала')
                return jsonify({'error': 'Внутренняя ошибка раздела ключей'}), 500
        return wrapper

    def _public(key, last_day=None):
        item = {field: key[field] for field in (
            'id', 'name', 'mark', 'created_at', 'created_by_name', 'last_used_at',
            'calls_count', 'revoked_at', 'revoked_by_name', 'active')}
        item['acts_as'] = {'role': key['otp_role'], 'department_id': key['department_id'],
                           'department': key['department_name'],
                           'department_code': key['department_code']}
        item['calls_last_day'] = int((last_day or {}).get(key['id'], 0))
        return item

    @bp.route('', methods=['GET', 'OPTIONS'])
    @require_api_key
    @guarded
    def knowledge_keys_list(cursor, requester_id):
        last_day = queries.calls_last_day(cursor)
        return jsonify({'keys': [_public(key, last_day) for key in queries.list_keys(cursor)]})

    @bp.route('', methods=['POST', 'OPTIONS'])
    @require_api_key
    @guarded
    def knowledge_keys_create(cursor, requester_id):
        body = request.get_json(silent=True) or {}
        name = ' '.join(str(body.get('name') or '').split())
        if not name:
            return jsonify({'error': 'Нужно название ключа — кому он выдан'}), 400
        if len(name) > NAME_MAX:
            return jsonify({'error': 'Название длиннее %d знаков' % NAME_MAX}), 400
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
                                    key_mark=keys.mark(secret), otp_role=role,
                                    department_id=int(department[0]), created_by=requester_id)
        logging.info('knowledge_api: выпущен ключ #%s «%s» (отпечаток %s) пользователем %s',
                     key_id, name, keys.mark(secret), requester_id)
        created = queries.get_key(cursor, key_id)
        return jsonify({'status': 'success', 'key': _public(created), 'secret': secret,
                        'header': keys.HEADER}), 201

    @bp.route('/<int:key_id>/revoke', methods=['POST', 'OPTIONS'])
    @require_api_key
    @guarded
    def knowledge_keys_revoke(cursor, requester_id, key_id):
        if not queries.revoke_key(cursor, key_id, by=requester_id):
            existing = queries.get_key(cursor, key_id)
            if existing is None:
                return jsonify({'error': 'Ключ не найден'}), 404
            return jsonify({'status': 'success', 'key': _public(existing), 'already': True})
        logging.info('knowledge_api: ключ #%s отозван пользователем %s', key_id, requester_id)
        return jsonify({'status': 'success', 'key': _public(queries.get_key(cursor, key_id))})

    @bp.route('/<int:key_id>/calls', methods=['GET', 'OPTIONS'])
    @require_api_key
    @guarded
    def knowledge_keys_calls(cursor, requester_id, key_id):
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
