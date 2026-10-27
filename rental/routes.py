# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Аренда авто» (Flask Blueprint /api/rental).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2 сам
подключает этот модуль, обратный импорт был бы циклом (как у twofa/ и thermoboxes/).

Соглашения портала: методы включают OPTIONS и первым делом отдаётся preflight,
авторизация — общий require_api_key, ошибка — {"error": "...", "code": "..."}.

Ручки ночного отчёта (#398):
    GET    ''                              экран: права, список отчётов, выбранный отчёт со строками
    GET    /reports/<id>                   отчёт со строками (смена отчёта, опрос во время сбора)
    POST   /reports                        собрать отчёт текущего слота (пропустил планировщик)
    POST   /reports/<id>/refresh           собрать заново отчёт этой ночи; ручное «Состояние» остаётся
    PUT    /rows/<id>/state                «Состояние» вручную: base / repair / sale / null
    GET    /reports/<id>/export            выгрузка в Excel

История неприкосновенна: старый отчёт пересобрать нельзя — только отчёт, чей слот ещё
идёт (rules.CATCH_UP_HOURS). Иначе «данные за 28.10 01:00» молча стали бы дневными.
"""

import logging
from functools import wraps
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request

from . import access, queries, report as report_xlsx, rules
from .service import Busy


def _iso(value):
    return value.isoformat(timespec='seconds') if value else None


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bad(message, code, status=400):
    return jsonify({"error": message, "code": code}), status


def public_report(report):
    if not report:
        return None
    return {
        'id': report['id'], 'kind': report['kind'], 'kind_label': rules.KIND_LABELS.get(report['kind']),
        'slot_at': _iso(report['slot_at']), 'status': report['status'],
        'collected_at': _iso(report.get('collected_at')), 'started_at': _iso(report.get('started_at')),
        'sources': report.get('sources') or {}, 'error': report.get('error'),
        'counts': report.get('counts'),
        'shows_rent': report['kind'] == rules.KIND_NIGHT,
    }


def public_row(row, names):
    return {
        'id': row['id'], 'plate': row['plate'], 'gps_site': row.get('gps_site'),
        'gps_sites': row.get('gps_sites') or [], 'gps_status': row['gps_status'],
        'signal_at': _iso(row.get('signal_at')), 'address': row.get('address'),
        'lat': row.get('lat'), 'lon': row.get('lon'), 'rent_status': row.get('rent_status'),
        'in_fleet': bool(row.get('in_fleet')), 'model': row.get('model'),
        'state': row.get('state'), 'state_source': row.get('state_source'),
        'state_by_name': names.get(row.get('state_by')) if row.get('state_by') else None,
        'state_at': _iso(row.get('state_at')), 'needs_check': rules.needs_check(row),
    }


def build_rental_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester, service):
    bp = Blueprint('rental', __name__, url_prefix='/api/rental')

    def rental_route(rule, methods=('GET',)):
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
                        return _bad('Раздел «Аренда авто» вам не открыт', 'RENTAL_SECTION_CLOSED', 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('rental: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Аренда авто»",
                                    "detail": type(exc).__name__}), 500

            return wrapper

        return decorator

    def _report_payload(cursor, report):
        rows = queries.rows_for_report(cursor, report['id'])
        names = queries.user_names(cursor, [row.get('state_by') for row in rows])
        return public_report(report), [public_row(row, names) for row in rows]

    def _slot_info(ctx, now, reports):
        """Текущий слот: какой отчёт сейчас должен быть и можно ли его собрать кнопкой."""
        kind, slot = rules.current_slot(now)
        existing = next((r for r in reports if r['kind'] == kind and r['slot_at'] == slot), None)
        in_window = rules.can_catch_up(slot, now)
        next_kind, next_at = rules.next_slot(now)
        return {
            'kind': kind, 'kind_label': rules.KIND_LABELS[kind], 'slot_at': _iso(slot),
            'report_id': existing['id'] if existing else None,
            'in_window': in_window,
            'can_collect': existing is None and (in_window or access.can_collect_any_time(ctx)),
            'next_kind_label': rules.KIND_LABELS[next_kind], 'next_at': _iso(next_at),
        }

    # ─── Экран ───────────────────────────────────────────────────────────────

    @rental_route('')
    def rental_screen(ctx):
        now = queries.now_almaty()
        selected_id = _int(request.args.get('report'))
        with db._get_cursor() as cursor:
            queries.expire_stale(cursor, now)
            reports = queries.list_reports(cursor)
            selected = next((r for r in reports if r['id'] == selected_id), None) or (reports[0] if reports else None)
            report, rows = _report_payload(cursor, selected) if selected else (None, [])
        return jsonify({
            'capabilities': access.capabilities(ctx),
            'reports': [public_report(r) for r in reports],
            'report': report,
            'rows': rows,
            'slot': _slot_info(ctx, now, reports),
            'collecting': service.running(),
            'server_time': _iso(now),
            'states': [{'value': key, 'label': rules.STATE_LABELS[key]} for key in rules.STATES],
        })

    @rental_route('/reports/<int:report_id>')
    def rental_report(report_id, ctx):
        with db._get_cursor() as cursor:
            queries.expire_stale(cursor, queries.now_almaty())
            report = queries.get_report(cursor, report_id)
            if not report:
                return _bad('Отчёт не найден', 'RENTAL_NOT_FOUND', 404)
            report_payload, rows = _report_payload(cursor, report)
        return jsonify({'report': report_payload, 'rows': rows, 'collecting': service.running()})

    # ─── Сбор ────────────────────────────────────────────────────────────────

    @rental_route('/reports', methods=('POST',))
    def rental_collect(ctx):
        now = queries.now_almaty()
        kind, slot = rules.current_slot(now)
        with db._get_cursor() as cursor:
            existing = queries.find_report(cursor, kind, slot)
        if existing:
            return _bad('Отчёт за %s уже есть — его можно обновить' % rules.KIND_LABELS[kind], 'RENTAL_EXISTS', 409)
        if not (rules.can_catch_up(slot, now) or access.can_collect_any_time(ctx)):
            return _bad('Отчёт собирается ночью: в 01:00 и 07:00', 'RENTAL_OUT_OF_WINDOW', 409)
        try:
            report = service.start(kind, slot, by=ctx['user_id'])
        except Busy:
            return _bad('Отчёт уже собирается — подождите минуту', 'RENTAL_BUSY', 409)
        return jsonify({'report': public_report(report)}), 202

    @rental_route('/reports/<int:report_id>/refresh', methods=('POST',))
    def rental_refresh(report_id, ctx):
        now = queries.now_almaty()
        with db._get_cursor() as cursor:
            report = queries.get_report(cursor, report_id)
        if not report:
            return _bad('Отчёт не найден', 'RENTAL_NOT_FOUND', 404)
        if not rules.can_catch_up(report['slot_at'], now):
            return _bad('Прошлые отчёты не пересобираются — они остаются такими, какими были в ту ночь',
                        'RENTAL_HISTORY_LOCKED', 409)
        try:
            report = service.start(report['kind'], report['slot_at'], by=ctx['user_id'])
        except Busy:
            return _bad('Отчёт уже собирается — подождите минуту', 'RENTAL_BUSY', 409)
        return jsonify({'report': public_report(report)}), 202

    # ─── «Состояние» ─────────────────────────────────────────────────────────

    @rental_route('/rows/<int:row_id>/state', methods=('PUT',))
    def rental_set_state(row_id, ctx):
        payload = request.get_json(silent=True) or {}
        state = payload.get('state') or None
        outcome, row = service.set_state(ctx, row_id, state)
        if outcome == 'bad_state':
            return _bad('Такого состояния нет', 'RENTAL_BAD_STATE')
        if outcome == 'not_found':
            return _bad('Строка не найдена — обновите отчёт', 'RENTAL_NOT_FOUND', 404)
        if outcome == 'forbidden':
            return _bad('Состояние ставит ночная смена ООЗ', 'RENTAL_FORBIDDEN', 403)
        with db._get_cursor() as cursor:
            names = queries.user_names(cursor, [row.get('state_by')])
        return jsonify({'row': public_row(row, names)})

    # ─── Excel ───────────────────────────────────────────────────────────────

    @rental_route('/reports/<int:report_id>/export')
    def rental_export(report_id, ctx):
        with db._get_cursor() as cursor:
            report = queries.get_report(cursor, report_id)
            if not report:
                return _bad('Отчёт не найден', 'RENTAL_NOT_FOUND', 404)
            rows = queries.rows_for_report(cursor, report_id)
        stream, _written = report_xlsx.build_workbook(report, rows, generated_by=ctx.get('name') or '',
                                                      generated_at=queries.now_almaty())
        filename = report_xlsx.report_filename(report)
        response = Response(stream.getvalue(),
                            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response.headers['Content-Disposition'] = "attachment; filename*=UTF-8''%s" % quote(filename)
        response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response

    return bp
