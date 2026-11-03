# -*- coding: utf-8 -*-
"""HTTP-эндпоинты заявок на отпуск, Б/С и больничный (Flask Blueprint /api/leave_requests).

Фабрика получает зависимости аргументами — обратный импорт из bot_schedule2
был бы циклом. Граница «кого руководитель видит» приходит функцией
scope_filter: это та же _filter_operators_for_requester_scope, что режет
«Запросы по сменам», — второй копии правила в пакете нет.

    GET    /my                     свои заявки и доступные виды (оператор)
    POST   /my                     подать
    PATCH  /my/<id>                изменить
    POST   /my/<id>/cancel         отозвать
    POST   /my/<id>/proposal       ответ на предложенные даты {accept}
    POST   /my/seen                решения просмотрены
    GET    /review                 очередь руководителя (?status=pending|all)
    GET    /review/<id>/overlaps   кто отдыхает в выбранные даты (?start_date&end_date)
    POST   /review/<id>            решение {action: approve|propose|reject}
    GET    /journal                журнал месяца (?month=YYYY-MM&kind&status)
    GET    /journal/export         он же в Excel
    GET    /settings               настройки отделов (админ — все, глава — свои)
    PUT    /settings/<department>  изменить настройки отдела (админ, глава отдела)
"""

import logging
from datetime import date, datetime
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import notify, queries, report, rules, schema, service
from .rules import LeaveError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


def today_almaty():
    try:
        return datetime.now(ZoneInfo('Asia/Almaty')).date()
    except Exception:  # noqa: BLE001 — без tzdata процесс и так живёт в Asia/Almaty
        return date.today()


class _Deps:
    def __init__(self, resolve_supervisor, apply_period):
        self.resolve_supervisor = resolve_supervisor
        self.apply_period = apply_period


def build_leave_requests_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                                   viewer_context, scope_filter, resolve_supervisor, apply_period,
                                   send_telegram=None, web_app_base_url=None):
    """viewer_context(requester_id, requester) -> {'user_id', 'role', 'department_id',
    'is_admin', 'is_supervisor', 'headed_department_ids', 'is_manager'}."""
    bp = Blueprint('leave_requests', __name__, url_prefix='/api/leave_requests')
    deps = _Deps(resolve_supervisor, apply_period)

    def _flush(outbox):
        if not send_telegram:
            return
        for chat_id, text, markup in outbox.messages:
            try:
                send_telegram(chat_id, text, parse_mode='HTML', reply_markup=markup)
            except Exception:  # noqa: BLE001
                logging.warning('leave_requests: Telegram не принял сообщение для %s', chat_id, exc_info=True)

    def route(rule, methods=('GET',)):
        def decorator(handler):
            @bp.route(rule, methods=list(methods) + ['OPTIONS'], endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                outbox = notify.Outbox()
                try:
                    requester_id, requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    viewer = viewer_context(requester_id, requester)
                    with db._get_cursor() as cursor:
                        if not schema.schema_is_ready(cursor):
                            return jsonify({"error": "Заявки на отпуск ещё не развёрнуты",
                                            "code": "LEAVE_NOT_READY"}), 503
                        response = handler(*args, cursor=cursor, viewer=viewer, requester=requester,
                                           outbox=outbox, today=today_almaty(), **kwargs)
                    _flush(outbox)
                    return response
                except LeaveError as exc:
                    payload = {"error": exc.message, "code": exc.code}
                    return jsonify(payload), exc.status
                except Exception:  # noqa: BLE001
                    logging.exception('leave_requests: %s упал', handler.__name__)
                    return jsonify({"error": "Внутренняя ошибка сервера"}), 500
            return wrapper
        return decorator

    def _body():
        return request.get_json(silent=True) or {}

    def _operator(cursor, viewer):
        user = queries.load_user(cursor, viewer['user_id'])
        if not user:
            raise LeaveError('Пользователь не найден', code='LEAVE_NOT_FOUND', status=404)
        return user

    def _require_manager(viewer):
        if not viewer.get('is_manager'):
            raise LeaveError('Это доступно руководителю', code='LEAVE_FORBIDDEN', status=403)

    def _allowed_ids(requester, viewer, operator_ids):
        return scope_filter(requester, viewer['user_id'], operator_ids)

    # ── Оператор ────────────────────────────────────────────────────────────

    @route('/my', methods=('GET', 'POST'))
    def my_requests(*, cursor, viewer, requester, outbox, today):
        user = _operator(cursor, viewer)
        if request.method == 'GET':
            return jsonify(service.operator_payload(cursor, user, today)), 200
        created = service.create(cursor, deps, outbox, user=user, data=_body(), today=today,
                                 base_url=web_app_base_url)
        return jsonify({"request": created}), 200

    @route('/my/<int:request_id>', methods=('PATCH',))
    def edit_my_request(request_id, *, cursor, viewer, requester, outbox, today):
        user = _operator(cursor, viewer)
        updated = service.edit(cursor, deps, outbox, user=user, request_id=request_id, data=_body(),
                               today=today, base_url=web_app_base_url)
        return jsonify({"request": updated}), 200

    @route('/my/<int:request_id>/cancel', methods=('POST',))
    def cancel_my_request(request_id, *, cursor, viewer, requester, outbox, today):
        user = _operator(cursor, viewer)
        updated = service.cancel(cursor, outbox, user=user, request_id=request_id, data=_body(),
                                 today=today, base_url=web_app_base_url)
        return jsonify({"request": updated}), 200

    @route('/my/<int:request_id>/proposal', methods=('POST',))
    def answer_proposal(request_id, *, cursor, viewer, requester, outbox, today):
        user = _operator(cursor, viewer)
        data = _body()
        if 'accept' not in data:
            raise LeaveError('Ответьте «Согласен» или «Не согласен»', code='LEAVE_BAD_ANSWER')
        updated = service.answer_proposal(cursor, deps, outbox, user=user, request_id=request_id,
                                          accept=bool(data.get('accept')), data=data, today=today,
                                          base_url=web_app_base_url)
        return jsonify({"request": updated}), 200

    @route('/my/seen', methods=('POST',))
    def mark_my_seen(*, cursor, viewer, requester, outbox, today):
        user = _operator(cursor, viewer)
        return jsonify({"updated": service.mark_seen(cursor, user)}), 200

    # ── Руководитель ────────────────────────────────────────────────────────

    @route('/review')
    def review_queue(*, cursor, viewer, requester, outbox, today):
        _require_manager(viewer)
        status_filter = 'all' if (request.args.get('status') or '').strip().lower() == 'all' else 'pending'
        allowed = _allowed_ids(requester, viewer, service.review_candidates(cursor))
        try:
            limit = max(1, min(int(request.args.get('limit') or 300), 500))
        except ValueError:
            raise LeaveError('Неверный limit') from None
        return jsonify(service.review_payload(cursor, viewer=viewer, operator_ids=allowed,
                                              status_filter=status_filter, today=today, limit=limit)), 200

    def _in_scope_fn(requester, viewer):
        def in_scope(operator_id):
            return bool(_allowed_ids(requester, viewer, [int(operator_id)]))
        return in_scope

    @route('/review/<int:request_id>/overlaps')
    def review_overlaps(request_id, *, cursor, viewer, requester, outbox, today):
        _require_manager(viewer)
        service.require_access(viewer)
        raw = queries.fetch_request(cursor, request_id, today)
        if not raw or not _in_scope_fn(requester, viewer)(raw['operator_id']):
            raise LeaveError('Заявка не найдена', code='LEAVE_NOT_FOUND', status=404)
        start = rules.parse_date(request.args.get('start_date'))
        end = rules.parse_date(request.args.get('end_date'))
        return jsonify(service.overlaps_preview(cursor, request_id=request_id, start=start, end=end,
                                                today=today)), 200

    @route('/review/<int:request_id>', methods=('POST',))
    def decide_request(request_id, *, cursor, viewer, requester, outbox, today):
        _require_manager(viewer)
        actor = queries.load_user(cursor, viewer['user_id']) or {'id': viewer['user_id'], 'name': None}
        updated = service.decide(cursor, deps, outbox, viewer=viewer, actor=actor, request_id=request_id,
                                 data=_body(), in_scope=_in_scope_fn(requester, viewer), today=today,
                                 base_url=web_app_base_url)
        return jsonify({"request": updated}), 200

    # ── Журнал ──────────────────────────────────────────────────────────────

    def _journal(cursor, viewer, requester, today, limit):
        _require_manager(viewer)
        service.require_access(viewer)
        start, end = rules.parse_month(request.args.get('month'), today)
        kind, statuses = rules.journal_filter(request.args.get('kind'), request.args.get('status'))
        allowed = _allowed_ids(requester, viewer, service.review_candidates(cursor))
        return service.journal_payload(cursor, operator_ids=allowed, start=start, end=end, kind=kind,
                                       statuses=statuses, today=today, limit=limit)

    @route('/journal')
    def journal(*, cursor, viewer, requester, outbox, today):
        return jsonify(_journal(cursor, viewer, requester, today, rules.JOURNAL_LIMIT)), 200

    @route('/journal/export')
    def journal_export(*, cursor, viewer, requester, outbox, today):
        payload = _journal(cursor, viewer, requester, today, rules.EXPORT_LIMIT)
        return send_file(BytesIO(report.journal_workbook(payload['items'])), mimetype=report.XLSX_MIME,
                         as_attachment=True, download_name='Журнал заявок %s.xlsx' % payload['month'])

    # ── Настройки ───────────────────────────────────────────────────────────

    @route('/settings')
    def list_settings(*, cursor, viewer, requester, outbox, today):
        return jsonify(service.settings_payload(cursor, viewer=viewer)), 200

    @route('/settings/<int:department_id>', methods=('PUT',))
    def save_settings(department_id, *, cursor, viewer, requester, outbox, today):
        saved = service.save_department_settings(cursor, viewer=viewer, department_id=department_id, data=_body())
        return jsonify({"department": saved}), 200

    return bp
