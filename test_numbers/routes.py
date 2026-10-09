"""HTTP-эндпоинты «Реестра тестовых номеров» (Flask Blueprint /api/test_numbers).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2
сам подключает этот модуль, обратный импорт был бы циклом (как у parcels/).

Соглашения портала: методы включают OPTIONS и первым делом отдаётся preflight,
авторизация — общий require_api_key, ошибка — {"error": "...", "code": "..."}.
`db._get_cursor()` коммитит всё, что записано до `return …, 4xx`, поэтому каждый
обработчик сначала проверяет всё и только потом пишет.

Ручки:
    GET    ''                    экран: права и номера реестра
    GET    /people               сотрудники для выбора владельца
    POST   /numbers              добавить номер
    PATCH  /numbers/<id>         сменить владельца
    DELETE /numbers/<id>         убрать номер из реестра
    GET    /activity?from&to     тесты по дням и сами звонки и чаты за период
"""

import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

from . import access, activity, keys, queries, rules, schema

# Казахстан живёт в UTC+5 круглый год (с 01.03.2024 — единый пояс), поэтому
# смещение постоянное: tzdata на Windows-машинах разработчиков может не быть.
ALMATY = timezone(timedelta(hours=5))


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


def _iso(value):
    if value is None:
        return None
    if getattr(value, 'tzinfo', None) is not None:
        value = value.astimezone(ALMATY)
    return value.isoformat()


def serialize_number(row):
    return {
        'id': row['id'],
        'phone_key': row['phone_key'],
        'phone_display': row['phone_display'],
        'owner': ({
            'id': row['owner_user_id'],
            'name': row.get('owner_name'),
            'department': row.get('owner_department'),
            'fired': bool(row.get('owner_fired')),
        } if row.get('owner_user_id') else None),
        'created_by_name': row.get('created_by_name'),
        'created_at': _iso(row.get('created_at')),
    }


def _duplicate(existing, phone_display):
    """409 «номер уже в реестре» — с тем, чей он: и для дубля, и для гонки двух вставок."""
    existing = existing or {}
    who = existing.get('owner_name') or 'без владельца'
    shown = existing.get('phone_display') or phone_display
    return _bad(f'Номер уже в реестре: {shown} — {who}', 'TEST_NUMBERS_DUPLICATE', 409,
                number=serialize_number(existing) if existing else None)


def build_test_numbers_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                 resolve_requester, oktell_query=None):
    """Собирает Blueprint раздела.

    oktell_query — bot_schedule2._oktell_query (один read-only SELECT к прокси);
    без него звонки СЗоВ на экране не показываются, а экран говорит об этом.
    """
    bp = Blueprint('test_numbers', __name__, url_prefix='/api/test_numbers')

    def section_route(rule, methods=('GET',), edit=False):
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
                        ctx = queries.load_access_context(cursor, requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    if not access.can_open_section(ctx):
                        return _bad('Реестр тестовых номеров ведут админы и главы отделов',
                                    'TEST_NUMBERS_CLOSED', 403)
                    if edit and not access.can_edit(ctx):
                        return _bad('Править реестр могут админы и главы отделов',
                                    'TEST_NUMBERS_EDIT_FORBIDDEN', 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('test_numbers: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка реестра тестовых номеров",
                        "detail": str(exc)[:200],
                    }), 500

            return wrapper

        return decorator

    def _payload():
        return request.get_json(silent=True) or {}

    def _actor(ctx):
        return {'user_id': ctx['user_id'], 'name': ctx.get('name')}

    def _not_ready():
        return _bad('Раздел ещё разворачивается — попробуйте после перезапуска сервера',
                    'TEST_NUMBERS_SCHEMA_NOT_READY', 409)

    # ── Экран ─────────────────────────────────────────────────────────────
    @section_route('')
    def test_numbers_screen(ctx):
        capabilities = {'edit': access.can_edit(ctx)}
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"schema_ready": False, "capabilities": capabilities, "numbers": []})
            numbers = queries.list_numbers(cursor)
        return jsonify({
            "schema_ready": True,
            "capabilities": capabilities,
            "numbers": [serialize_number(row) for row in numbers],
        })

    @section_route('/people')
    def test_numbers_people(ctx):
        with db._get_cursor() as cursor:
            items = queries.people(cursor)
        return jsonify({"items": items})

    # ── Правка реестра ────────────────────────────────────────────────────
    @section_route('/numbers', methods=('POST',), edit=True)
    def test_numbers_add(ctx):
        data = _payload()
        try:
            phone_key, phone_display = rules.parse_phone(data.get('phone'))
        except rules.PhoneError as exc:
            return _bad(str(exc), 'TEST_NUMBERS_PHONE_INVALID', field='phone')
        owner_id = _int(data.get('owner_user_id'))
        if not owner_id:
            return _bad('Выберите сотрудника, которому принадлежит номер',
                        'TEST_NUMBERS_OWNER_REQUIRED', field='owner')

        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            owner = queries.active_person(cursor, owner_id)
            if not owner:
                return _bad('Такого действующего сотрудника нет', 'TEST_NUMBERS_OWNER_UNKNOWN',
                            404, field='owner')
            existing = queries.find_by_key(cursor, phone_key)
            if existing:
                return _duplicate(existing, phone_display)
            created = queries.add_number(cursor, phone_key=phone_key, phone_display=phone_display,
                                         owner=owner, actor=_actor(ctx))
            if not created:
                # Кто-то добавил тот же номер между проверкой и вставкой — ответ тот же.
                return _duplicate(queries.find_by_key(cursor, phone_key), phone_display)
        keys.invalidate_cache()
        return jsonify({"number": serialize_number(created)}), 201

    @section_route('/numbers/<int:number_id>', methods=('PATCH',), edit=True)
    def test_numbers_change_owner(number_id, ctx):
        owner_id = _int(_payload().get('owner_user_id'))
        if not owner_id:
            return _bad('Выберите сотрудника, которому принадлежит номер',
                        'TEST_NUMBERS_OWNER_REQUIRED', field='owner')
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            owner = queries.active_person(cursor, owner_id)
            if not owner:
                return _bad('Такого действующего сотрудника нет', 'TEST_NUMBERS_OWNER_UNKNOWN',
                            404, field='owner')
            if not queries.get_number(cursor, number_id):
                return _bad('Номера уже нет в реестре — обновите страницу',
                            'TEST_NUMBERS_NOT_FOUND', 404)
            updated = queries.change_owner(cursor, number_id, owner=owner, actor=_actor(ctx))
        if not updated:
            return _bad('Номера уже нет в реестре — обновите страницу', 'TEST_NUMBERS_NOT_FOUND', 404)
        return jsonify({"number": serialize_number(updated)})

    @section_route('/numbers/<int:number_id>', methods=('DELETE',), edit=True)
    def test_numbers_remove(number_id, ctx):
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            removed = queries.remove_number(cursor, number_id, actor=_actor(ctx))
        if not removed:
            return _bad('Номера уже нет в реестре — обновите страницу', 'TEST_NUMBERS_NOT_FOUND', 404)
        keys.invalidate_cache()
        return jsonify({"removed": serialize_number(removed)})

    # ── Тесты по дням ─────────────────────────────────────────────────────
    @section_route('/activity')
    def test_numbers_activity(ctx):
        today = datetime.now(ALMATY).date()
        try:
            start, end = activity.parse_period(request.args.get('from'), request.args.get('to'), today)
        except activity.PeriodError as exc:
            return _bad(str(exc), 'TEST_NUMBERS_PERIOD_INVALID')
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            phone_keys = sorted(keys.load_keys(cursor))
        result = activity.collect(db._get_cursor, phone_keys, start, end, oktell_query=oktell_query)
        return jsonify({"from": start.isoformat(), "to": end.isoformat(), **result})

    return bp
