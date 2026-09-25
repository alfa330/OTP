# -*- coding: utf-8 -*-
"""HTTP раздела «Обзвон из телефона» (Flask Blueprint).

Зависимости приходят аргументами фабрики, а не импортом `bot_schedule2`: тот
сам подключает этот модуль (как op_funnel/routes.py, oktell_guard/routes.py).

Ручки телефона (bearer оператора, как /api/operator/sip_settings):
    GET  /api/operator/dial_list                             текущая порция и состояние
    GET  /api/operator/dial_list/progress                    «Мой прогресс»: счётчики за месяц и сегодня
    GET  /api/operator/dial_list/script                      скрипт разговора отдела (текст + вопросы)
    GET  /api/operator/dial_list/attempts/<attempt_id>/live  набираем / разговор / финал по Binotel
    POST /api/operator/dial_list/next                        выдать следующую порцию
    POST /api/operator/dial_list/items/<assignment_id>/call  позвонить по строке
    POST /api/operator/dial_list/attempts/<attempt_id>/phone_event
                                                             {event: ringing|answered|ended|no_leg|operator_hangup,
                                                              by_operator?, leg_sec?} → в ответе outcome_required /
                                                             cancelled — нужен ли итог после разговора
    POST /api/operator/dial_list/attempts/<attempt_id>/outcome
                                                             {outcome_id, comment} — итог разговора
                                                             (обязателен перед следующим звонком)

Ручки руководителя. Круг свой, не от «Настроек SIP»: админ видит все отделы
раздела, глава отдела — свои отделы, если они в периметре (Binotel, заведённые
настройки обзвона или код из DIAL_LIST_DEPARTMENT_CODES):
    GET     /api/dial_list/departments                       отделы раздела в моей зоне
    GET/PUT /api/dial_list/departments/<id>/settings         настройки отдела
    GET/PUT /api/dial_list/operators/<user_id>/settings      {enabled: true|false|null}
    POST    /api/dial_list/departments/<id>/leads/upload     файл ФИО+телефон (+period=YYYY-MM)
    GET     /api/dial_list/departments/<id>/leads/summary    сколько загружено/в пуле (?period=)
    GET/PUT /api/dial_list/departments/<id>/outcomes         справочник итогов звонка
    GET     /api/dial_list/departments/<id>/leads            журнал водителей (фильтры:
                                                             q, stage, operator_id, batch_id, outcome_id,
                                                             date_from/date_to — дни звонков, period,
                                                             sort, limit, offset)
    GET     /api/dial_list/leads/<lead_id>                   карточка: попытки, действия, загрузки
    POST    /api/dial_list/leads/<lead_id>/requeue           вернуть в список  {note}
    POST    /api/dial_list/leads/<lead_id>/exclude           исключить         {note}
    PUT     /api/dial_list/leads/<lead_id>/note              заметка руководителя
    GET     /api/dial_list/attempts/<attempt_id>/recording   ссылка на запись разговора (Binotel)
    GET     /api/dial_list/overview?date=YYYY-MM-DD[&department_id=]

Binotel (без авторизации, токен в адресе либо IP сервера Binotel):
    GET/POST /api/dial_list/webhook/binotel?token=…          «API Call Completed»

Номер телефона водителя ни одна ручка оператора не отдаёт — это весь смысл
раздела; проверяется тестом.
"""
import logging
import os
import uuid
from datetime import date, datetime

from flask import Blueprint, jsonify, request

from .service import DialListError, DialListService, parse_period

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

    @bp.route('/api/operator/dial_list/progress', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_progress():
        """«Мой прогресс» на телефоне: счётчики оператора за месяц и за сегодня, без ФИО и номеров."""
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.operator_progress(user_id)}), 200

    @bp.route('/api/operator/dial_list/script', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_script():
        """Скрипт разговора отдела для телефона: текст и включённые вопросы с ответами."""
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.operator_script(user_id)}), 200

    @bp.route('/api/operator/dial_list/attempts/<attempt_id>/live', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_attempt_live(attempt_id):
        """Живое состояние попытки для карточки звонка: набираем / разговор / финал (по Binotel)."""
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.attempt_live(user_id, attempt_id)}), 200

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
        # by_operator/leg_sec — у события ended: оператор ли положил трубку и сколько
        # секунд плечо было принято телефоном (см. service.phone_event).
        result = svc.phone_event(user_id, attempt_id, payload.get('event'), payload.get('at'),
                                 by_operator=bool(payload.get('by_operator')), leg_sec=payload.get('leg_sec', 0))
        return jsonify({"status": "success", **result}), 200

    @bp.route('/api/operator/dial_list/attempts/<attempt_id>/outcome', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_outcome(attempt_id):
        user_id, _ = _operator()
        payload = request.get_json(silent=True) or {}
        result = svc.set_attempt_outcome(user_id, attempt_id, payload.get('outcome_id'), payload.get('comment', ''))
        return jsonify({"status": "success", **result}), 200

    # ── руководитель ────────────────────────────────────────────────────────
    @bp.route('/api/dial_list/departments', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def departments():
        """Подключённые отделы в зоне запросившего; админу — ещё и кандидаты на
        подключение (отделы на Binotel, которых в разделе пока нет)."""
        _requester_id, scope = _manager()
        return jsonify({
            "status": "success",
            "departments": svc.list_departments(scope),
            "candidates": svc.candidate_departments() if scope is None else [],
        }), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/enroll', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def department_enroll(department_id):
        """Подключить отдел к разделу. Только админ: глава видит лишь уже подключённые."""
        requester_id, scope = _manager()
        if scope is not None:
            raise DialListError("Подключить отдел к обзвону может администратор", 403)
        settings = svc.enroll_department(department_id, changed_by=requester_id)
        return jsonify({"status": "success", "settings": settings}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/unenroll', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def department_unenroll(department_id):
        """Отключить отдел от раздела (пока по нему нет данных). Только админ."""
        _requester_id, scope = _manager()
        if scope is not None:
            raise DialListError("Отключить отдел может администратор", 403)
        return jsonify({"status": "success", **svc.unenroll_department(department_id)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/users', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def department_users(department_id):
        """Сотрудники отдела с линией и персональным включением обзвона."""
        _manager(department_id)
        return jsonify({"status": "success", "users": svc.department_users(department_id)}), 200

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

    @bp.route('/api/dial_list/departments/<int:department_id>/lines', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def lines(department_id):
        """Линии Binotel компании отдела и сотрудники отдела — для назначения. Без секретов."""
        _manager(department_id)
        return jsonify({
            "status": "success",
            "sip_server": svc.department_sip_server(department_id),
            "lines": svc.list_lines(department_id),
            "users": svc.department_users(department_id),
        }), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/lines/assign', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def lines_assign(department_id):
        requester_id, _scope = _manager(department_id)
        payload = request.get_json(silent=True) or {}
        try:
            user_id = int(payload.get('user_id'))
        except (TypeError, ValueError):
            raise DialListError("user_id: число")
        result = svc.assign_line(department_id, user_id, payload.get('internal_number'), changed_by=requester_id)
        return jsonify({"status": "success", **result}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/lines/release', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def lines_release(department_id):
        requester_id, _scope = _manager(department_id)
        payload = request.get_json(silent=True) or {}
        try:
            user_id = int(payload.get('user_id'))
        except (TypeError, ValueError):
            raise DialListError("user_id: число")
        return jsonify({"status": "success", **svc.release_line(department_id, user_id, changed_by=requester_id)}), 200

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
        # База какого месяца: поле формы period=YYYY-MM; пусто — обзваниваемый месяц.
        period = parse_period(request.form.get('period'))
        # Общий разбор файла «fio + phone» и общая нормализация номера — одна
        # точка правды для всей телефонии проекта.
        from common.leads_file import parse_leads_file
        try:
            rows = parse_leads_file(raw_bytes, file_ext)
        except ValueError as exc:
            raise DialListError(str(exc))
        counts = svc.import_leads(department_id, requester_id, file_name, rows, period=period)
        return jsonify({"status": "success", **counts}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/leads/summary', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def leads_summary(department_id):
        _manager(department_id)
        period = parse_period(request.args.get('period'))
        return jsonify({"status": "success", **svc.leads_summary(department_id, period=period)}), 200

    # ── итоги звонка ────────────────────────────────────────────────────────
    @bp.route('/api/dial_list/departments/<int:department_id>/outcomes', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def outcomes(department_id):
        requester_id, _scope = _manager(department_id)
        if request.method == 'PUT':
            payload = request.get_json(silent=True) or {}
            items = payload.get('items')
            return jsonify({"status": "success",
                            "outcomes": svc.save_outcomes(department_id, items, changed_by=requester_id)}), 200
        return jsonify({"status": "success", "outcomes": svc.outcomes(department_id)}), 200

    # ── скрипт разговора ────────────────────────────────────────────────────
    @bp.route('/api/dial_list/departments/<int:department_id>/script', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def script(department_id):
        """Основной текст с лёгкой разметкой и быстрые вопросы с ответами (редактор руководителя)."""
        requester_id, _scope = _manager(department_id)
        if request.method == 'PUT':
            payload = request.get_json(silent=True) or {}
            return jsonify({"status": "success",
                            "script": svc.save_script(department_id, payload, changed_by=requester_id)}), 200
        return jsonify({"status": "success", "script": svc.script(department_id)}), 200

    # ── журнал водителей ────────────────────────────────────────────────────
    # Номер водителя и здесь не отдаётся (только маска): руководитель удалённого
    # КЦ — тоже удалёнщик, а файл с номерами у загрузившего и так есть.
    def _uuid_or_404(value, what):
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            raise DialListError(f"{what} не найден", 404)

    @bp.route('/api/dial_list/departments/<int:department_id>/leads', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def leads_journal(department_id):
        _manager(department_id)
        args = request.args
        for key in ('date_from', 'date_to'):
            if args.get(key):
                try:
                    datetime.strptime(args.get(key), '%Y-%m-%d')
                except ValueError:
                    raise DialListError(f"{key}: ожидается YYYY-MM-DD")
        batch_id = args.get('batch_id') or None
        if batch_id:
            batch_id = _uuid_or_404(batch_id, "Файл загрузки")
        outcome_id = args.get('outcome_id') or None
        if outcome_id:
            outcome_id = _uuid_or_404(outcome_id, "Итог")
        return jsonify({"status": "success", **svc.leads_journal(
            department_id, q=args.get('q', ''), stage=args.get('stage', ''),
            operator_id=args.get('operator_id') or None, batch_id=batch_id,
            date_from=args.get('date_from') or None, date_to=args.get('date_to') or None,
            sort=args.get('sort', 'activity'), limit=args.get('limit', 50), offset=args.get('offset', 0),
            period=parse_period(args.get('period'), allow_all=True), outcome_id=outcome_id,
        )}), 200

    @bp.route('/api/dial_list/leads/<lead_id>', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def lead_card(lead_id):
        lead_id = _uuid_or_404(lead_id, "Водитель")
        _manager(svc.lead_department(lead_id))
        return jsonify({"status": "success", "lead": svc.lead_card(lead_id)}), 200

    @bp.route('/api/dial_list/leads/<lead_id>/requeue', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def lead_requeue(lead_id):
        lead_id = _uuid_or_404(lead_id, "Водитель")
        requester_id, _scope = _manager(svc.lead_department(lead_id))
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success",
                        "lead": svc.requeue_lead(lead_id, requester_id, note=payload.get('note', ''))}), 200

    @bp.route('/api/dial_list/leads/<lead_id>/exclude', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def lead_exclude(lead_id):
        lead_id = _uuid_or_404(lead_id, "Водитель")
        requester_id, _scope = _manager(svc.lead_department(lead_id))
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success",
                        "lead": svc.exclude_lead(lead_id, requester_id, note=payload.get('note', ''))}), 200

    @bp.route('/api/dial_list/leads/<lead_id>/note', methods=['PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def lead_note(lead_id):
        lead_id = _uuid_or_404(lead_id, "Водитель")
        requester_id, _scope = _manager(svc.lead_department(lead_id))
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success",
                        "lead": svc.set_lead_note(lead_id, requester_id, payload.get('note', ''))}), 200

    @bp.route('/api/dial_list/attempts/<attempt_id>/recording', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def attempt_recording(attempt_id):
        attempt_id = _uuid_or_404(attempt_id, "Попытка")
        _manager(svc.attempt_department(attempt_id))
        return jsonify({"status": "success", **svc.attempt_recording(attempt_id)}), 200

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
