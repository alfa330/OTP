# -*- coding: utf-8 -*-
"""HTTP-эндпоинты табеля фронт-офисов (Flask Blueprint /api/front_office_timesheet).

    GET /scope     id отдела фронт-офисов — по нему «Учет часов» узнаёт группу «Регионов»
    GET /options   отборы окна выгрузки за месяц (?month=YYYY-MM): города, люди, должности
    GET /data      тот же табель для экрана «Учета часов», JSON (?month=…, отборы как у export)
    GET /export    файл Excel (?month=YYYY-MM&city=…&user_id=…&job_title=…, отборы повторяются)

Кому: глава отдела «Фронт офисы» и суперадмины — решение владельца 06.11.2026.
Остальным, в том числе главам и СВ других отделов и админам без отдела, — 403:
табель только про фронт-офисы. Правило «кто может» приходит функцией can_export
из bot_schedule2 (там роли и главенство отделов), обратного импорта нет.
"""

import logging
from datetime import date, datetime
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import data, rules, workbook

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
FORBIDDEN_MESSAGE = 'Табель доступен главе фронт-офисов и суперадминам'


def today_almaty():
    try:
        return datetime.now(ZoneInfo('Asia/Almaty')).date()
    except Exception:  # noqa: BLE001 — без tzdata процесс и так живёт в Asia/Almaty
        return date.today()


def export_file_name(month):
    return f'Табель фронт-офисы {month}.xlsx'


def build_front_office_timesheet_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                           resolve_requester, can_export, today_fn=today_almaty):
    """can_export(requester_id, requester) -> bool."""
    bp = Blueprint('front_office_timesheet', __name__, url_prefix='/api/front_office_timesheet')

    def route(rule, needs_month=True):
        def decorator(handler):
            @bp.route(rule, methods=['GET', 'OPTIONS'], endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    if not can_export(requester_id, requester):
                        return jsonify({"error": FORBIDDEN_MESSAGE, "code": "TIMESHEET_FORBIDDEN"}), 403
                    if not needs_month:
                        return handler(*args, **kwargs)
                    month = str(request.args.get('month') or '').strip()
                    if not rules.parse_month(month):
                        return jsonify({"error": "Месяц нужен в виде ГГГГ-ММ",
                                        "code": "TIMESHEET_BAD_MONTH"}), 400
                    return handler(*args, month=month, today=today_fn(), **kwargs)
                except data.TimesheetError as exc:
                    return jsonify({"error": exc.message, "code": exc.code}), exc.status
                except Exception:  # noqa: BLE001
                    logging.exception('front_office_timesheet: %s упал', handler.__name__)
                    return jsonify({"error": "Внутренняя ошибка сервера"}), 500
            return wrapper
        return decorator

    def _model(month, today):
        """Табель месяца с отборами запроса — один для экрана и для файла."""
        inputs = data.collect(db, month, today, with_facts=True)
        if inputs['pending_days']:
            # Дни, которые «Отметки» не смогли собрать из Workpace: в табеле они
            # выглядели бы неявками. Лучше честный отказ, чем табель с дырами.
            raise data.TimesheetError(
                'Отметки Workpace за %d дн. ещё не собраны — повторите через минуту' % len(inputs['pending_days']),
                status=503, code='TIMESHEET_PENDING_DAYS')
        filters = rules.clean_filters(
            cities=request.args.getlist('city'),
            user_ids=request.args.getlist('user_id'),
            job_titles=request.args.getlist('job_title'),
        )
        return inputs, rules.build_model(inputs, filters)

    @route('/scope', needs_month=False)
    def timesheet_scope():
        return jsonify(data.scope(db)), 200

    @route('/options')
    def timesheet_options(*, month, today):
        inputs = data.collect(db, month, today, with_facts=False)
        return jsonify(data.options_payload(inputs)), 200

    @route('/data')
    def timesheet_data(*, month, today):
        inputs, model = _model(month, today)
        payload = rules.model_payload(model)
        payload['without_card'] = inputs['without_card']
        return jsonify(payload), 200

    @route('/export')
    def timesheet_export(*, month, today):
        _inputs, model = _model(month, today)
        if not model['cities']:
            raise data.TimesheetError('Под выбранный отбор никто не попал', status=404,
                                      code='TIMESHEET_EMPTY')
        return send_file(BytesIO(workbook.build(model)), mimetype=XLSX_MIME, as_attachment=True,
                         download_name=export_file_name(month))

    return bp
