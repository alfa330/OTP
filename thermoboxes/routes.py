"""HTTP-эндпоинты раздела «Термокороба» (Flask Blueprint /api/thermoboxes).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2
сам подключает этот модуль, обратный импорт был бы циклом (как у parcels/).

Соглашения портала: методы включают OPTIONS и первым делом отдаётся preflight,
авторизация — общий require_api_key, ошибка — {"error": "...", "code": "..."}.

ВАЖНО ПРО КОДЫ ОТВЕТОВ. `db._get_cursor()` коммитит всё, что записано до
`return … , 4xx` (откат — только по исключению). Поэтому каждый обработчик
сначала проверяет ВСЁ, и только потом пишет: сохранение пачки либо ложится
целиком, либо не ложится вовсе.

Ручки:
    GET   ''                     экран целиком: права, строки, памятка, справочник
    PUT   /rows                  сохранить правки пачкой (фронт-офисы)
    POST  /rows                  завести офис в таблицу (глава, админ)
    PATCH /rows/<id>/visibility  убрать офис из таблицы или вернуть (глава, админ)
    GET   /rows/<id>/events      история строки
    PUT   /memo                  памятка (супервайзер)
    GET   /export                xlsx с тем же отбором, что на экране
"""

import logging
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from parcels import queries as parcels_queries

from . import access, queries, report, rules, schema

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Пачка — это экран: офисов в таблице полтора десятка. Двести — заведомо не
# экран, а чей-то цикл.
_MAX_BATCH = 200


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


def build_thermoboxes_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                resolve_requester):
    """Собирает Blueprint раздела."""
    bp = Blueprint('thermoboxes', __name__, url_prefix='/api/thermoboxes')

    def thermo_route(rule, methods=('GET',), need=None):
        """Каркас роута: preflight, авторизация, контекст, гейты, ошибки.

        need='edit'   — остатки и условия (фронт-офисы, глобальный админ);
        need='manage' — состав таблицы (глава фронт-офисов, глобальный админ);
        need='memo'   — памятка (супервайзер, глобальный админ).
        """
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
                        return _bad('Раздел «Термокороба» вам не открыт', 'THERMO_SECTION_CLOSED', 403)
                    if need == 'edit' and not access.can_edit(ctx):
                        return _bad('Данные заполняют сотрудники фронт-офисов', 'THERMO_EDIT_FORBIDDEN', 403)
                    if need == 'manage' and not access.can_manage(ctx):
                        return _bad('Состав таблицы меняет руководитель фронт-офисов',
                                    'THERMO_MANAGE_FORBIDDEN', 403)
                    if need == 'memo' and not access.can_edit_memo(ctx):
                        return _bad('Памятку редактирует супервайзер', 'THERMO_MEMO_FORBIDDEN', 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('thermoboxes: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Термокороба»",
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
                    'THERMO_SCHEMA_NOT_READY', 409)

    # ── Экран ─────────────────────────────────────────────────────────────
    @thermo_route('')
    def thermoboxes_screen(ctx):
        """Всё, что нужно экрану, одним запросом: права, строки, памятка и —
        руководителю — офисы справочника, которых в таблице ещё нет."""
        capabilities = access.capabilities(ctx)
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"schema_ready": False, "capabilities": capabilities,
                                "rows": [], "memo": None})
            spaces = queries.section_spaces(cursor)
            payload = {
                "schema_ready": True,
                "capabilities": capabilities,
                "rows": queries.list_rows(cursor, spaces=spaces,
                                          include_hidden=capabilities['can_manage']),
                "memo": queries.get_memo(cursor),
            }
            if capabilities['can_manage']:
                taken = queries.taken_office_ids(cursor)
                payload['directory'] = [office for office in queries.directory(cursor, spaces=spaces)
                                        if office['id'] not in taken]
        return jsonify(payload)

    # ── Сохранить правки пачкой ───────────────────────────────────────────
    @thermo_route('/rows', methods=('PUT',), need='edit')
    def thermoboxes_save(ctx):
        items = _payload().get('items')
        if not isinstance(items, list) or not items:
            return _bad('Нечего сохранять', 'THERMO_EMPTY')
        if len(items) > _MAX_BATCH:
            return _bad('Слишком много строк за раз', 'THERMO_BATCH_TOO_BIG')

        requests_by_id = {}
        for item in items:
            if not isinstance(item, dict):
                return _bad('Строка не разобрана', 'THERMO_ITEM_INVALID')
            row_id, version = _int(item.get('id')), _int(item.get('version'))
            if not row_id or version is None:
                return _bad('У строки нет номера или версии', 'THERMO_ITEM_INVALID')
            if row_id in requests_by_id:
                return _bad('Строка прислана дважды', 'THERMO_ITEM_DUPLICATE')
            requests_by_id[row_id] = (version, item)

        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            spaces = queries.section_spaces(cursor)
            locked = queries.lock_rows(cursor, requests_by_id, spaces=spaces)

            # Всё проверяется до первой записи: отказ после неё закоммитился бы.
            missing = [row_id for row_id in requests_by_id if row_id not in locked]
            if missing:
                return _bad('Офиса уже нет в таблице — обновите страницу', 'THERMO_ROW_NOT_FOUND', 404,
                            row_ids=missing)
            hidden = [row_id for row_id in requests_by_id if not locked[row_id]['is_active']]
            if hidden:
                return _bad('Офис убран из таблицы — обновите страницу', 'THERMO_ROW_HIDDEN', 409,
                            row_ids=hidden)
            stale = [row_id for row_id, (version, _) in requests_by_id.items()
                     if locked[row_id]['version'] != version]
            if stale:
                names = ', '.join(locked[row_id]['city'] for row_id in stale)
                return _bad('Пока вы правили, данные изменил коллега: %s. Проверьте свежие '
                            'цифры и сохраните ещё раз' % names, 'THERMO_CONFLICT', 409,
                            rows=[locked[row_id] for row_id in stale])

            plan = []
            for row_id, (_, item) in sorted(requests_by_id.items()):
                row = locked[row_id]
                try:
                    fields = rules.clean_fields(item)
                except rules.FieldError as exc:
                    return _bad('%s: %s' % (row['city'], exc.message), 'THERMO_FIELD_INVALID',
                                row_id=row_id, field=exc.field)
                diff = rules.changes(row, fields)
                if diff:
                    plan.append((row_id, {change['field']: change['to'] for change in diff}, diff))

            for row_id, fields, diff in plan:
                queries.update_row(cursor, row_id, fields, actor)
                queries.insert_event(cursor, row_id, 'edited', diff, actor)

            saved_ids = {row_id for row_id, _, _ in plan}
            rows = [queries.read_row(cursor, row_id, spaces=spaces) for row_id in sorted(saved_ids)]
        return jsonify({"saved": len(plan), "rows": rows})

    # ── Состав таблицы ────────────────────────────────────────────────────
    @thermo_route('/rows', methods=('POST',), need='manage')
    def thermoboxes_add(ctx):
        data = _payload()
        office_id = _int(data.get('office_id'))
        if not office_id:
            return _bad('Выберите офис из справочника', 'THERMO_OFFICE_REQUIRED')
        try:
            fields = rules.clean_fields(data)
        except rules.FieldError as exc:
            return _bad(exc.message, 'THERMO_FIELD_INVALID', field=exc.field)

        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            spaces = queries.section_spaces(cursor)
            office = parcels_queries.read_office(cursor, office_id, space_ids=spaces)
            if not office:
                return _bad('Офиса нет в справочнике фронт-офисов', 'THERMO_OFFICE_UNKNOWN', 404)
            if office_id in queries.taken_office_ids(cursor):
                return _bad('Этот офис уже в таблице', 'THERMO_OFFICE_TAKEN', 409)
            row_id = queries.create_row(cursor, office, fields, actor)
            queries.insert_event(cursor, row_id, 'created', [], actor)
            row = queries.read_row(cursor, row_id, spaces=spaces)
        return jsonify({"row": row}), 201

    @thermo_route('/rows/<int:row_id>/visibility', methods=('PATCH',), need='manage')
    def thermoboxes_visibility(row_id, ctx):
        active = _payload().get('is_active')
        if not isinstance(active, bool):
            return _bad('Не указано, показывать ли офис', 'THERMO_VISIBILITY_INVALID')
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            spaces = queries.section_spaces(cursor)
            locked = queries.lock_rows(cursor, [row_id], spaces=spaces)
            row = locked.get(row_id)
            if not row:
                return _bad('Офиса нет в таблице', 'THERMO_ROW_NOT_FOUND', 404)
            if row['is_active'] != active:
                queries.set_active(cursor, row_id, active, actor)
                queries.insert_event(cursor, row_id, 'shown' if active else 'hidden', [], actor)
            row = queries.read_row(cursor, row_id, spaces=spaces)
        return jsonify({"row": row})

    # ── История строки ────────────────────────────────────────────────────
    @thermo_route('/rows/<int:row_id>/events')
    def thermoboxes_events(row_id, ctx):
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"items": []})
            spaces = queries.section_spaces(cursor)
            row = queries.read_row(cursor, row_id, spaces=spaces)
            # Скрытая строка — только тому, кто её скрыл бы: остальным её нет.
            if not row or (not row['is_active'] and not access.can_manage(ctx)):
                return _bad('Офиса нет в таблице', 'THERMO_ROW_NOT_FOUND', 404)
            items = queries.list_events(cursor, row_id)
        return jsonify({"items": items})

    # ── Памятка ───────────────────────────────────────────────────────────
    @thermo_route('/memo', methods=('PUT',), need='memo')
    def thermoboxes_memo(ctx):
        try:
            memo = rules.clean_memo(_payload())
        except rules.FieldError as exc:
            return _bad(exc.message, 'THERMO_MEMO_INVALID', field=exc.field)
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            saved = queries.save_memo(cursor, memo, _actor(ctx))
        return jsonify({"memo": saved})

    # ── Выгрузка ──────────────────────────────────────────────────────────
    @thermo_route('/export')
    def thermoboxes_export(ctx):
        city = request.args.get('city') or ''
        query = request.args.get('q') or ''
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            rows = queries.list_rows(cursor, spaces=queries.section_spaces(cursor))
        rows = [row for row in rows if rules.matches(row, city, query)]
        content = report.build(rows)
        return send_file(
            BytesIO(content),
            mimetype=XLSX_MIME,
            as_attachment=True,
            download_name=report.file_name(parcels_queries.today_almaty()),
        )

    return bp
