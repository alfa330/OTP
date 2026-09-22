# -*- coding: utf-8 -*-
"""HTTP раздела «Обзвон из телефона» (Flask Blueprint).

Зависимости приходят аргументами фабрики, а не импортом `bot_schedule2`: тот
сам подключает этот модуль (как op_funnel/routes.py, oktell_guard/routes.py).

Ручки телефона (bearer оператора, как /api/operator/sip_settings):
    GET  /api/operator/dial_list                             текущая порция и состояние
    POST /api/operator/dial_list/next                        выдать следующую порцию
    POST /api/operator/dial_list/items/<assignment_id>/call  позвонить по строке
    POST /api/operator/dial_list/attempts/<attempt_id>/phone_event
                                                             {event: ringing|answered|ended|no_leg}

Ручки руководителя. Круг свой, не от «Настроек SIP»: админ видит все отделы
раздела, глава отдела — свои отделы, если они в периметре (Binotel, заведённые
настройки обзвона или код из DIAL_LIST_DEPARTMENT_CODES):
    GET     /api/dial_list/departments                       отделы раздела в моей зоне
    GET/PUT /api/dial_list/departments/<id>/settings         настройки отдела
    GET/PUT /api/dial_list/operators/<user_id>/settings      {enabled: true|false|null}
    POST    /api/dial_list/departments/<id>/leads/upload     файл ФИО+телефон
    GET     /api/dial_list/departments/<id>/leads/summary    сколько загружено/в пуле
    GET     /api/dial_list/overview?date=YYYY-MM-DD[&department_id=]

Binotel (без авторизации, токен в адресе либо IP сервера Binotel):
    GET/POST /api/dial_list/webhook/binotel?token=…          «API Call Completed»

Номер телефона водителя ни одна ручка оператора не отдаёт — это весь смысл
раздела; проверяется тестом.
"""
import logging
import os
from datetime import date, datetime

from flask import Blueprint, jsonify, request

from .service import DialListError, DialListService

log = logging.getLogger(__name__)

LEADS_MAX_FILE_SIZE_MB = 10
LEADS_MAX_FILE_SIZE_BYTES = LEADS_MAX_FILE_SIZE_MB * 1024 * 1024


def build_dial_list_blueprint(*, db, require_api_key, build_cors_preflight_response,
                              resolve_requester, is_admin_role, headed_department_ids,
                              service=None):
    """resolve_requester() -> (requester_id, requester_row, error|None);
    is_admin_role(role) -> bool; headed_department_ids(requester_id) -> iterable[int]."""
    bp = Blueprint('dial_list', __name__)
    svc = service or DialListService(db)
    bp.service = svc  # для тестов и для /api/operator/sip_settings

    # ── вспомогательное ─────────────────────────────────────────────────────
    def _preflight():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        return None

    def _operator():
        requester_id, requester, error = resolve_requester()
        if error:
            message, status = error
            raise DialListError(message, status)
        return int(requester_id), requester

    def _manager(department_id=None):
        """Руководитель раздела и его зона (None — все отделы раздела)."""
        requester_id, requester, error = resolve_requester()
        if error:
            message, status = error
            raise DialListError(message, status)
        role = requester[3]
        # Логин — восьмое поле строки get_user (u.login); по нему работает пилот.
        login = requester[7] if len(requester) > 7 else None
        scope = svc.manager_scope(bool(is_admin_role(role)), headed_department_ids(requester_id), login=login)
        if scope is not None and not scope:
            raise DialListError("Раздел «Обзвон из телефона» вам недоступен", 403)
        if department_id is not None and scope is not None and int(department_id) not in set(scope):
            raise DialListError("Это не ваш отдел", 403)
        return int(requester_id), scope

    def _error(exc):
        return jsonify({"error": str(exc)}), exc.status

    def _guard(fn):
        """Единая обработка ошибок: DialListError → её статус, остальное → 500."""
        def wrapper(*args, **kwargs):
            pre = _preflight()
            if pre is not None:
                return pre
            try:
                return fn(*args, **kwargs)
            except DialListError as exc:
                return _error(exc)
            except Exception:
                log.exception("dial_list: %s", request.path)
                return jsonify({"error": "Internal server error"}), 500
        wrapper.__name__ = fn.__name__
        return wrapper

    # ── телефон оператора ───────────────────────────────────────────────────
    @bp.route('/api/operator/dial_list', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_state():
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.get_state(user_id)}), 200

    @bp.route('/api/operator/dial_list/next', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_next():
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.issue_next_portion(user_id)}), 200

    @bp.route('/api/operator/dial_list/items/<assignment_id>/call', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_call(assignment_id):
        user_id, _ = _operator()
        return jsonify({"status": "success", "attempt": svc.start_call(user_id, assignment_id)}), 200

    @bp.route('/api/operator/dial_list/attempts/<attempt_id>/phone_event', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_phone_event(attempt_id):
        user_id, _ = _operator()
        payload = request.get_json(silent=True) or {}
        result = svc.phone_event(user_id, attempt_id, payload.get('event'), payload.get('at'))
        return jsonify({"status": "success", **result}), 200

    # ── руководитель ────────────────────────────────────────────────────────
    @bp.route('/api/dial_list/departments', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def departments():
        _requester_id, scope = _manager()
        return jsonify({"status": "success", "departments": svc.list_departments(scope)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/settings', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def department_settings(department_id):
        requester_id, _scope = _manager(department_id)
        if request.method == 'GET':
            return jsonify({"status": "success", "settings": svc.department_settings(department_id)}), 200
        payload = request.get_json(silent=True) or {}
        settings = svc.save_department_settings(department_id, payload, changed_by=requester_id)
        return jsonify({"status": "success", "settings": settings}), 200

    @bp.route('/api/dial_list/operators/<int:user_id>/settings', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_settings(user_id):
        target = db.get_sip_operator(user_id)
        if not target:
            raise DialListError("Сотрудник не найден", 404)
        requester_id, _scope = _manager(target.get('department_id'))
        department_id = target.get('department_id')
        dept = svc.department_settings(department_id) if department_id is not None else None
        department_enabled = bool(dept and dept["configured"] and dept["enabled"])
        if request.method == 'GET':
            personal = svc.user_setting(user_id)
            return jsonify({
                "status": "success", "user_id": int(user_id), "enabled": personal,
                "department_enabled": department_enabled,
                "effective": personal if personal is not None else department_enabled,
            }), 200
        payload = request.get_json(silent=True) or {}
        enabled = payload.get('enabled', None)
        if enabled is not None and not isinstance(enabled, bool):
            raise DialListError("enabled: true, false или null (как у отдела)")
        saved = svc.save_user_setting(user_id, enabled, changed_by=requester_id)
        saved["department_enabled"] = department_enabled
        saved["effective"] = enabled if enabled is not None else department_enabled
        return jsonify({"status": "success", **saved}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/leads/upload', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def leads_upload(department_id):
        requester_id, _scope = _manager(department_id)
        file_storage = request.files.get('file')
        if not file_storage:
            raise DialListError("file is required")
        file_name = os.path.basename(str(file_storage.filename or 'leads.xlsx'))
        file_ext = os.path.splitext(file_name)[1].lower()
        if file_ext not in ('.csv', '.xlsx', '.xlsm'):
            raise DialListError("Поддерживаются только .csv, .xlsx и .xlsm")
        raw_bytes = file_storage.read(LEADS_MAX_FILE_SIZE_BYTES + 1)
        if not raw_bytes:
            raise DialListError("Файл пустой")
        if len(raw_bytes) > LEADS_MAX_FILE_SIZE_BYTES:
            raise DialListError(f"Файл слишком большой. Лимит: {LEADS_MAX_FILE_SIZE_MB} MB", 413)
        # Разбор файла общий с базой лидов Тез: формат «fio + phone» тот же,
        # а нормализация номера — единственная точка правды для всей телефонии.
        from tez.lead_service import parse_leads_file
        try:
            rows = parse_leads_file(raw_bytes, file_ext)
        except ValueError as exc:
            raise DialListError(str(exc))
        counts = svc.import_leads(department_id, requester_id, file_name, rows)
        return jsonify({"status": "success", **counts}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/leads/summary', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def leads_summary(department_id):
        _manager(department_id)
        return jsonify({"status": "success", **svc.leads_summary(department_id)}), 200

    @bp.route('/api/dial_list/overview', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def overview():
        _requester_id, scope = _manager()
        raw_day = request.args.get('date')
        try:
            day = datetime.strptime(raw_day, '%Y-%m-%d').date() if raw_day else date.today()
        except ValueError:
            raise DialListError("date: ожидается YYYY-MM-DD")
        department_ids = scope
        wanted = request.args.get('department_id')
        if wanted:
            try:
                wanted_id = int(wanted)
            except ValueError:
                raise DialListError("department_id: число")
            if scope is not None and wanted_id not in set(scope):
                raise DialListError("Это не ваш отдел", 403)
            department_ids = [wanted_id]
        return jsonify({"status": "success", "date": day.isoformat(),
                        "operators": svc.overview(department_ids, day)}), 200

    # ── Binotel ─────────────────────────────────────────────────────────────
    @bp.route('/api/dial_list/webhook/binotel', methods=['GET', 'POST'])
    def binotel_webhook():
        remote_addr = (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
                       or request.remote_addr or '')
        token = request.args.get('token', '')
        if request.method == 'GET':
            # Binotel и человек проверяют адрес открытием: отвечаем коротко и без данных.
            return "ok", 200
        try:
            if not svc.webhook_allowed(token, remote_addr):
                log.warning("dial_list: вебхук отклонён, адрес %s", remote_addr)
                return jsonify({"error": "Forbidden"}), 403
            form = request.form if request.form else (request.get_json(silent=True) or {})
            result = svc.handle_webhook(form, remote_addr)
        except Exception:
            log.exception("dial_list: ошибка обработки вебхука")
            # Binotel ретраит только на не-200; свои ошибки чиним по журналу.
            return "error", 500
        return jsonify({"status": "success", **result}), 200

    return bp
