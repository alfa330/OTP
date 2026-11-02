"""Раздел «supervisors»: ручки /api/sv — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_supervisors_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, jsonify, request
from database import db
from datetime import date as dt_date
from datetime import datetime
from datetime import timedelta
from google.cloud import storage as gcs_storage
import calendar
import group_late
import logging
import os
import requests
import threading
import time


SUPERVISOR_CLOCKSTER_SYNC_MAX_DAYS = 31

def _clockster_error_types():
    from group_late.clockster import ClocksterError
    return (ClocksterError,)


def build_supervisors_blueprint(
    *,
    ALLOWED_ORIGINS,
    API_TOKEN,
    AVATAR_SIGNED_URL_CACHE,
    AVATAR_SIGNED_URL_CACHE_LOCK,
    AVATAR_SIGNED_URL_CACHE_MAX_ITEMS,
    AVATAR_SIGNED_URL_CACHE_MIN_REMAINING_SECONDS,
    AVATAR_SIGNED_URL_TTL_SECONDS,
    ROLE_HIERARCHY,
    _build_avatar_signed_url,
    _build_cors_preflight_response,
    _department_scope_id_for_requester,
    _get_authenticated_requester,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _headed_department_ids,
    _is_admin_role,
    _is_allowed_origin,
    _is_global_admin_requester,
    _is_marketing_observer,
    _is_supervisor_role,
    _load_target_user_with_scope,
    _normalize_origin,
    _normalize_user_role,
    _operator_item_id,
    _scoped_groups_for_requester,
    _supervisor_hours_settings_scope,
    _sync_supervisor_clockster_marks,
    get_gcs_client,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('supervisors', __name__, url_prefix='/api')

    def _get_operator_month_salary_metrics(operator_id, month):
        """Return non-sensitive, month-scoped inputs used by the salary preview."""
        empty_payload = {
            "month": str(month),
            "quality_average": None,
            "quality_evaluation_count": 0,
            "quality_available": False,
        }
        try:
            aggregates = db.get_operator_score_aggregates_for_month(
                month=month,
                operator_ids=[operator_id],
            ) or {}
            aggregate = aggregates.get(int(operator_id)) or {}
            quality_count = int(aggregate.get("call_count") or 0)
            quality_average = aggregate.get("avg_score_raw")
            if quality_average is None:
                quality_average = aggregate.get("avg_score")
            quality_available = quality_count > 0 and quality_average is not None
            return {
                "month": str(month),
                "quality_average": float(quality_average) if quality_available else None,
                "quality_evaluation_count": quality_count,
                "quality_available": quality_available,
            }
        except Exception:
            # Сбой необязательного превью зарплаты не должен скрывать сами часы.
            logging.exception(
                "Failed to load salary quality metrics for operator %s, month %s",
                operator_id,
                month,
            )
            return empty_payload

    def _supervisor_marks_access(requester_id, role, target_user_id):
        """Окно дня СВ (задача #352): 'edit' — РОП отдела СВ или глобальный админ
    (дописывают и снимают ручные отметки), 'view' — сам СВ смотрит свои
    отметки, None — нельзя."""
        clockster_departments = set(db.clockster_hours_department_ids())
        target_department = db.get_user_department_id(target_user_id)
        if target_department is None or int(target_department) not in clockster_departments:
            return None
        if _is_global_admin_requester(role, requester_id):
            return 'edit'
        if int(target_department) in set(_headed_department_ids(requester_id) or ()):
            return 'edit'
        if int(target_user_id) == int(requester_id) and _is_supervisor_role(role):
            return 'view'
        return None

    def _supervisor_day_response(user_id, day, access):
        detail = db.get_supervisor_clockster_day(user_id, day)
        detail['can_edit'] = access == 'edit'
        return jsonify(detail), 200

    def _supervisor_view_date(value):
        """День окна, который вернуть после правки. Проверяется ДО записи: иначе
    отметка уже сохранена, а клиент получает 400 и повторяет — «уже есть»."""
        if not value:
            return None
        try:
            return db._normalize_schedule_date(value).isoformat()
        except (TypeError, ValueError):
            raise ValueError('Неверная дата окна: нужен формат ГГГГ-ММ-ДД')

    @bp.route('/sv/preview_calls_table', methods=['POST'])
    @require_api_key
    def preview_calls_table():
        try:
            # Проверяем наличие файла
            if 'file' not in request.files:
                return jsonify({"error": "Файл не был загружен"}), 400

            file = request.files['file']
            if file.filename == '':
                return jsonify({"error": "Файл не выбран"}), 400

            if not file.filename.lower().endswith(('.csv', '.xls', '.xlsx')):
                return jsonify({"error": "Неверный формат файла. Допустимы .csv, .xls, .xlsx"}), 400

            # Получаем user_id из заголовка
            user_id_header = request.headers.get('X-User-Id')
            if not user_id_header or not user_id_header.isdigit():
                return jsonify({"error": "Некорректный X-User-Id"}), 400
            user_id = int(user_id_header)

            user = db.get_user(id=user_id)
            if not user:
                return jsonify({"error": "Requester not found"}), 403

            requester_role = _normalize_user_role(user[3])
            if not (_is_supervisor_role(requester_role) or _is_admin_role(requester_role)):
                return jsonify({"error": "Unauthorized: только супервайзеры или администраторы могут загружать таблицы"}), 403

            group_id_raw = (request.form.get('group_id') or '').strip()
            selected_group_id = None
            if group_id_raw:
                if not group_id_raw.isdigit() or int(group_id_raw) <= 0:
                    return jsonify({"error": "Некорректный group_id"}), 400
                selected_group_id = int(group_id_raw)
                if not db.get_group(selected_group_id):
                    return jsonify({"error": "Группа не найдена"}), 404

            selected_sv_id = None
            if _is_supervisor_role(requester_role):
                selected_sv_id = user_id
            else:
                sv_id_raw = (request.form.get('sv_id') or '').strip()
                if not sv_id_raw and selected_group_id is None:
                    return jsonify({"error": "Для администратора требуется выбранный супервайзер (sv_id)"}), 400
                if sv_id_raw:
                    try:
                        selected_sv_id = int(sv_id_raw)
                    except Exception:
                        return jsonify({"error": "Некорректный sv_id"}), 400

                    selected_sv = db.get_user(id=selected_sv_id)
                    selected_sv_role = str(selected_sv[3] or '').strip().lower() if selected_sv else ''
                    if not selected_sv or selected_sv_role not in ('sv', 'supervisor'):
                        return jsonify({"error": "Супервайзер не найден"}), 404 

            # Только парсим файл, не сохраняем.
            # date (YYYY-MM-DD) опционально: если передан, вернем только строки этой даты.
            target_date = (request.form.get('date') or '').strip() or None
            sheet_name, operators, error = db.parse_calls_file(file, target_date=target_date)
            if error:
                return jsonify({"error": error}), 400

            return jsonify({
                "status": "success",
                "sheet_name": sheet_name,
                "sv_id": selected_sv_id,
                "group_id": selected_group_id,
                "operators": operators
            }), 200

        except Exception as e:
            logging.error(f"Ошибка при предпросмотре таблицы звонков: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/sv/update_norm_hours', methods=['POST'])
    @require_api_key
    def update_norm_hours():
        try:
            data = request.get_json() or {}
            operator_id = data.get("operator_id")
            month = data.get("month")
            norm_hours = data.get("norm_hours")

            if not operator_id or not month:
                return jsonify({"error": "operator_id and month required"}), 400

            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not (_is_admin_role(requester[3]) or _is_supervisor_role(requester[3])):
                return jsonify({"error": "Forbidden"}), 403

            target_user, scope_error = _load_target_user_with_scope(
                requester,
                requester_id,
                operator_id,
                allow_self=False,
                supervisor_target_roles=('operator',),
                not_found_message="Operator not found",
                forbidden_message="Forbidden for this operator"
            )
            if scope_error:
                message, status_code = scope_error
                return jsonify({"error": message}), status_code

            with db._get_cursor() as cursor:
                cursor.execute("""
                INSERT INTO work_hours (operator_id, month, norm_hours)
                VALUES (%s, %s, %s)
                ON CONFLICT (operator_id, month)
                DO UPDATE SET norm_hours = EXCLUDED.norm_hours
            """, (int(target_user[0]), month, norm_hours))

            return jsonify({"status": "success"}), 200
        except Exception as e:
            logging.exception("update_norm_hours error")
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sv/daily_hours', methods=['GET'])
    @require_api_key
    def sv_daily_hours():
        """
    Возвращает daily_hours за месяц для всех операторов текущего супервайзера.
    Если запрос делает обычный оператор — возвращаем только его daily_hours
    и нечувствительный месячный salary_metrics для превью зарплаты.
    Параметры:
      - month (query param) — YYYY-MM (опционально, по умолчанию текущий месяц)
      - id (query param) — (только для admin) id нужного supervisor
    Заголовки:
      - X-User-Id (обязательно) — id супервайзера/оператора (проверяется роль)
    """
        try:
            month = request.args.get('month') or datetime.now().strftime('%Y-%m')
            try:
                _year, _mon = map(int, str(month).split('-'))
                _month_start = dt_date(_year, _mon, 1)
                _month_end = dt_date(_year, _mon, calendar.monthrange(_year, _mon)[1])
            except Exception:
                return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400
            # group_id (опц.) — group-aware режим: операторы и метрики по группе за месяц.
            group_param = request.args.get('group_id')
            group_id = int(group_param) if group_param and str(group_param).isdigit() else None
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            role = _normalize_user_role(requester[3])
            headed_dept_id = _headed_department_id(requester_id)
            is_global_admin = _is_global_admin_requester(role, requester_id)

            # Вкладка «Супервайзеры» (задача #352): СВ отделов с часами по Clockster.
            # РОП и глобальный админ видят всех таких СВ, сам СВ — только свою строку,
            # и параметр id тут не читается вовсе.
            if str(request.args.get('people') or '').strip().lower() == 'supervisors':
                clockster_departments = set(db.clockster_hours_department_ids())
                user_scope = None
                if is_global_admin:
                    departments = sorted(clockster_departments)
                else:
                    departments = sorted(set(_headed_department_ids(requester_id) or ()) & clockster_departments)
                    if not departments and _is_supervisor_role(role):
                        own_department = db.get_user_department_id(requester_id)
                        if own_department in clockster_departments:
                            departments = [own_department]
                            user_scope = [requester_id]
                if not departments:
                    return jsonify({"error": "Forbidden"}), 403
                supervisors = []
                for department_id in departments:
                    part = db.get_daily_hours_for_all_month(
                        month, department_id=department_id, people='supervisors', user_ids=user_scope)
                    supervisors.extend(part.get("operators", []))
                return jsonify({
                    "status": "success",
                    "month": month,
                    "days_in_month": calendar.monthrange(_year, _mon)[1],
                    "operators": supervisors
                }), 200

            if headed_dept_id is not None and not is_global_admin:
                if group_id is not None:
                    _grp = db.get_group(group_id)
                    if not (_grp and _grp.get('department_id') == headed_dept_id):
                        return jsonify({"error": "Forbidden: not your department's group"}), 403
                    result = db.get_daily_hours_by_group_month(group_id, month)
                else:
                    result = db.get_daily_hours_for_all_month(month, department_id=headed_dept_id)
                return jsonify({
                    "status": "success",
                    "month": result.get("month", month),
                    "days_in_month": result.get("days_in_month"),
                    "operators": result.get("operators", [])
                }), 200

            if _is_supervisor_role(role):
                user_param = request.args.get('id')
                if user_param and str(user_param).isdigit():
                    supervisor_id = int(user_param)
                else:
                    supervisor_id = requester_id

                if group_id is not None:
                    # СВ может смотреть группы своего отдела (или те, что ведёт).
                    # Проверяем по отделу запрашивающего (requester), а не выбранного СВ:
                    # иначе проверка всегда проходила бы (группа принадлежит выбранному СВ).
                    _grp = db.get_group(group_id)
                    _req_dept = db.get_user_department_id(requester_id)
                    _allowed = (
                        (_grp and _req_dept is not None and _grp.get('department_id') == _req_dept)
                        or db.supervisor_has_group_access_for_period(
                            requester_id, group_id, _month_start, _month_end
                        )
                    )
                    if not _allowed:
                        return jsonify({"error": "Forbidden: not your department's group"}), 403
                    result = db.get_daily_hours_by_group_month(group_id, month)
                else:
                    result = db.get_daily_hours_by_supervisor_month(supervisor_id, month)
                return jsonify({
                    "status": "success",
                    "month": result.get("month", month),
                    "days_in_month": result.get("days_in_month"),
                    "operators": result.get("operators", [])
                }), 200

            if role == 'operator':
                result = db.get_daily_hours_for_operator_month(requester_id, month)
                operator_obj = result.get("operator") if isinstance(result, dict) else None
                if operator_obj:
                    operator_obj["salary_metrics"] = _get_operator_month_salary_metrics(
                        operator_id=requester_id,
                        month=month,
                    )
                return jsonify({
                    "status": "success",
                    "month": result.get("month", month) if isinstance(result, dict) else month,
                    "days_in_month": result.get("days_in_month") if isinstance(result, dict) else None,
                    "operators": [operator_obj] if operator_obj else []
                }), 200

            if _is_admin_role(role):
                if group_id is not None:
                    # Админ может смотреть любую группу; id супервайзера не требуется.
                    result = db.get_daily_hours_by_group_month(group_id, month)
                else:
                    user_id = request.args.get('id')
                    if not user_id or not str(user_id).isdigit():
                        return jsonify({"error": "Missing or invalid 'id' parameter (supervisor id)"}), 400
                    supervisor_id = int(user_id)
                    result = db.get_daily_hours_by_supervisor_month(supervisor_id, month)
                operators = result.get("operators") if isinstance(result, dict) else None
                if operators is None:
                    single = result.get("operator") if isinstance(result, dict) else None
                    operators = [single] if single else []

                return jsonify({
                    "status": "success",
                    "month": result.get("month", month) if isinstance(result, dict) else month,
                    "days_in_month": result.get("days_in_month"),
                    "operators": operators
                }), 200

            return jsonify({"error": "Only supervisors, operators and admins can request this endpoint"}), 403

        except Exception as e:
            logging.exception("Error in sv_daily_hours")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/sv/save_table', methods=['POST'])
    @require_api_key
    def save_table():
        try:
            data = request.get_json()
            required_fields = ['table_url', 'direction_id', 'operators']
            if not data or not all(field in data for field in required_fields):
                return jsonify({"error": "Missing required fields: table_url, direction_id, operators"}), 400

            table_url = data['table_url']
            direction_id = int(data['direction_id'])
            operators = data['operators']  # List of dicts with 'name'

            user_id = int(request.headers.get('X-User-Id'))
            user = db.get_user(id=user_id)
            if not user or user[3] != 'sv':
                return jsonify({"error": "Unauthorized: Only supervisors can save tables"}), 403

            # Validate direction
            directions = db.get_directions()
            if not any(d['id'] == direction_id for d in directions):
                return jsonify({"error": "Invalid direction_id"}), 400

            # Update supervisor's hours table
            db.update_user_table(user_id=user_id, hours_table_url=table_url)

            # Create/update operators with direction and supervisor
            for op in operators:
                db.create_user(
                    telegram_id=None,
                    name=op['name'],
                    role='operator',
                    direction_id=direction_id,
                    supervisor_id=user_id,
                    hours_table_url=table_url
                )

            # Optional: Send Telegram notification to supervisor
            telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
            payload = {
                "chat_id": user[1],
                "text": f"Таблица часов успешно обновлена и операторы сохранены с направлением ID {direction_id} ✅",
                "parse_mode": "HTML"
            }
            response = requests.post(telegram_url, json=payload, timeout=10)
            if response.status_code != 200:
                logging.error(f"Telegram notification failed: {response.json().get('description')}")

            return jsonify({
                "status": "success",
                "message": "Table updated, operators saved with selected direction"
            }), 200
        except Exception as e:
            logging.error(f"Error saving table: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/sv/operators', methods=['GET'])
    @require_api_key
    def get_sv_operators_moderka():
        try:
            supervisor_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not _is_supervisor_role(requester[3]):
                return jsonify({"error": "Unauthorized: Only supervisors can access this"}), 403

            operators = db.get_all_operators_with_details() or []
            # Изоляция отделов: супервайзер видит операторов только своего отдела
            scope_dept = db.get_user_department_id(supervisor_id)
            if scope_dept is not None:
                member_ids = db.get_department_member_ids(scope_dept)
                operators = [op for op in operators if _operator_item_id(op) in member_ids]
            for operator in operators:
                operator['avatar_url'] = _build_avatar_signed_url(
                    operator.get('avatar_bucket'),
                    operator.get('avatar_blob_path')
                )
                operator.pop('avatar_bucket', None)
                operator.pop('avatar_blob_path', None)
            return jsonify({"status": "success", "operators": operators}), 200
        except Exception as e:
            logging.error(f"Error fetching operators: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/sv/data', methods=['GET'])
    @require_api_key
    def get_sv_data():
        try:
            # group_id — режим ГРУППЫ: состав месяца берётся по членству в группе
            # (ровно как в «Учёте часов»), а не по текущему users.supervisor_id.
            # Как и в /api/sv/daily_hours, при заданной группе параметр id не нужен.
            group_id_raw = request.args.get('group_id')
            group_id = None
            if group_id_raw not in (None, ''):
                try:
                    group_id = int(group_id_raw)
                except ValueError:
                    return jsonify({"error": "Invalid group_id parameter"}), 400

            # id
            user_id_raw = request.args.get('id')
            user_id = None
            if group_id is None:
                if not user_id_raw:
                    return jsonify({"error": "Missing ID parameter"}), 400
                try:
                    user_id = int(user_id_raw)
                except ValueError:
                    return jsonify({"error": "Invalid ID parameter"}), 400

            # optional month YYYY-MM
            month = request.args.get('month')
            if month:
                try:
                    datetime.strptime(month, "%Y-%m")
                except ValueError:
                    return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400
            else:
                month = datetime.now().strftime("%Y-%m")

            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3])
            headed_dept_id = _headed_department_id(requester_id)
            # Наблюдатель «Маркетинга» читает журнал наравне с главой своего отдела
            # (тому он открыт как главе — headed_dept_id). Раздел ему выдан на
            # просмотр: оценивать, править и отвечать на заявки он не может — это
            # решают отдельные ручки, каждая со своей проверкой.
            marketing_observer = _is_marketing_observer(requester_id, requester_role)
            if not (_is_admin_role(requester_role) or requester_role == 'sv'
                    or headed_dept_id is not None or marketing_observer):
                return jsonify({"error": "Forbidden"}), 403

            department_head_self_scope = False
            # id супервайзеров, чьи собственные оценки добавляются в таблицу отдельной
            # строкой: в режиме СВ это он сам, в режиме группы — СВ этой группы за месяц.
            supervisor_lookup_ids = []

            if group_id is not None:
                # Доступ к группе — теми же правилами, что и в /api/groups.
                # include_archived=True: у архивных групп есть месяцы, когда они жили,
                # и без них аналитика за прошлый месяц была бы недостижима.
                scoped_groups, scope_error = _scoped_groups_for_requester(
                    requester_id,
                    requester_role,
                    headed_dept_id,
                    include_archived=True,
                )
                if scope_error:
                    resp, code = scope_error
                    return resp, code
                group_row = next(
                    (g for g in (scoped_groups or []) if int(g.get('id') or 0) == group_id),
                    None
                )
                if not group_row:
                    return jsonify({"error": "Группа недоступна"}), 403

                group_supervisors = group_row.get('supervisors') or []
                for sv in group_supervisors:
                    try:
                        supervisor_lookup_ids.append(int(sv.get('id')))
                    except (TypeError, ValueError, AttributeError):
                        continue

                response_data = {
                    "status": "success",
                    "name": group_row.get('name') or '',
                    # Ссылка на таблицу оценок у группы своя не хранится — берём у её
                    # первого СВ; у группы без СВ (в проде такие есть) её просто нет.
                    "table": None,
                    "requested_month": month,
                    "group_id": group_id,
                    "group_name": group_row.get('name') or '',
                    "group_status": group_row.get('status') or '',
                    "group_department_id": group_row.get('department_id'),
                    "group_supervisors": group_supervisors,
                    "operators": []
                }

                try:
                    operators = db.get_group_operators_for_month(group_id, month) or []
                except Exception:
                    logging.exception("Failed to load group roster for /api/sv/data")
                    operators = []
            else:
                # Изоляция отделов: супервайзер и глава отдела видят данные только своего отдела.
                # Наблюдатель «Маркетинга» из этой изоляции выведен: журнал он смотрит
                # по ЧУЖИМ отделам (оценивают звонки СЗоВ и ОП), а своего отдела в
                # выборе нет вовсе — граница по своему отделу дала бы ему 403 на
                # каждого супервайзера.
                if not (_is_global_admin_requester(requester_role, requester_id) or marketing_observer):
                    scope_dept = _department_scope_id_for_requester(requester_id)
                    target_dept = db.get_user_department_id(user_id)
                    is_department_head_self_scope = headed_dept_id is not None and user_id == requester_id
                    if scope_dept is not None and target_dept != scope_dept and not is_department_head_self_scope:
                        return jsonify({"error": "Доступ только к супервайзерам своего отдела"}), 403

                # fetch user (accept any caller role; admin can call this endpoint)
                user = db.get_user(id=user_id)
                if not user:
                    return jsonify({"error": "User not found"}), 404

                # normalize user name (support tuple/list or dict)
                if isinstance(user, dict):
                    user_name = user.get("name") or user.get("full_name") or ""
                else:
                    user_name = user[2] if len(user) > 2 else ""

                response_data = {
                    "status": "success",
                    "name": user_name,
                    "table": user[9] if (not isinstance(user, dict) and len(user) > 9) else (user.get("scores_table_url") if isinstance(user, dict) else None),
                    "requested_month": month,
                    "operators": []
                }

                target_user_role = ''
                if isinstance(user, dict):
                    target_user_role = _normalize_user_role(user.get("role"))
                elif isinstance(user, (list, tuple)) and len(user) > 3:
                    target_user_role = _normalize_user_role(user[3])

                department_head_self_scope = headed_dept_id is not None and user_id == requester_id and target_user_role not in ('sv', 'supervisor')
                if target_user_role not in ('sv', 'supervisor') and not department_head_self_scope:
                    return jsonify({"error": "Selected user is not a supervisor"}), 400

                supervisor_lookup_ids = [user_id]

                # Return only operators that belong to the selected supervisor. Older
                # supervisor analytics used the full operator list here; that leaked
                # unrelated operators and made the selector ineffective.
                try:
                    if department_head_self_scope:
                        department_member_ids = set(db.get_department_member_ids(headed_dept_id) or [])
                        operators = [
                            op for op in (db.get_all_operators_with_details() or [])
                            if _operator_item_id(op) in department_member_ids
                        ]
                    else:
                        operators = db.get_operators_by_supervisor(user_id) or []
                except Exception:
                    operators = []

            supervisor_rows = []
            if supervisor_lookup_ids:
                try:
                    with db._get_cursor() as cursor:
                        # ANY(%s), а не один id: у группы супервайзеров может быть
                        # несколько (а может не быть ни одного).
                        cursor.execute("""
                        SELECT
                            u.id,
                            u.name,
                            u.direction_id,
                            u.hire_date,
                            u.scores_table_url,
                            u.status,
                            u.rate,
                            u.gender,
                            u.birth_date,
                            u.avatar_bucket,
                            u.avatar_blob_path
                        FROM users u
                        WHERE u.id = ANY(%s)
                          AND LOWER(COALESCE(u.role, '')) IN ('sv', 'supervisor')
                        ORDER BY u.name
                    """, (supervisor_lookup_ids,))
                        supervisor_rows = cursor.fetchall()
                except Exception:
                    supervisor_rows = []

            if group_id is not None and supervisor_rows:
                # Ссылка на таблицу оценок группы — у её первого СВ.
                for sv_row in supervisor_rows:
                    if len(sv_row) > 4 and sv_row[4]:
                        response_data["table"] = sv_row[4]
                        break

            operator_ids = []
            for op in operators:
                raw_operator_id = None
                if isinstance(op, dict):
                    raw_operator_id = op.get("id") or op.get("operator_id") or op.get("user_id")
                elif isinstance(op, (list, tuple)) and len(op) > 0:
                    raw_operator_id = op[0]
                try:
                    operator_ids.append(int(raw_operator_id))
                except (TypeError, ValueError):
                    continue

            supervisor_ids = []
            for sv_row in supervisor_rows:
                try:
                    supervisor_ids.append(int(sv_row[0]))
                except (TypeError, ValueError, IndexError):
                    continue

            metric_ids = operator_ids + supervisor_ids

            try:
                operator_metrics = db.get_operator_score_aggregates_for_month(month=month, operator_ids=metric_ids)
            except Exception:
                operator_metrics = {}

            try:
                operator_targets = db.get_operator_call_evaluation_targets_for_month(metric_ids, month=month)
            except Exception:
                operator_targets = {}

            try:
                feedback_stats_by_operator = db.get_feedback_sla_operator_stats_for_month(
                    month=month,
                    operator_ids=metric_ids
                ) if metric_ids else {}
            except Exception:
                logging.exception("Failed to load feedback SLA stats for /api/sv/data")
                feedback_stats_by_operator = {}

            seen_response_ids = set()
            for op in operators:
                # flexible unpacking: support dict or sequence rows
                if isinstance(op, dict):
                    operator_id = op.get("id") or op.get("operator_id") or op.get("user_id")
                    operator_name = op.get("name") or op.get("operator_name")
                    direction_id = op.get("direction_id")
                    operator_supervisor_id = op.get("supervisor_id")
                    operator_supervisor_name = op.get("supervisor_name")
                    hire_date = op.get("hire_date")
                    birth_date = op.get("birth_date")
                    hours_table_url = op.get("hours_table_url")
                    scores_table_url = op.get("scores_table_url")
                    status = op.get("status")
                    rate = op.get("rate")
                    gender = op.get("gender")
                    role_value = _normalize_user_role(op.get("role") or "operator")
                    avatar_url = op.get("avatar_url")
                    avatar_bucket = op.get("avatar_bucket")
                    avatar_blob_path = op.get("avatar_blob_path")
                    status_period_status_code = op.get("status_period_status_code")
                    status_period_start_date = op.get("status_period_start_date")
                    status_period_end_date = op.get("status_period_end_date")
                    status_period_dismissal_reason = op.get("status_period_dismissal_reason")
                    status_period_is_blacklist = op.get("status_period_is_blacklist")
                    status_period_comment = op.get("status_period_comment")
                    # Поля режима группы (в режиме СВ их в строке нет).
                    dismissal_date = op.get("dismissal_date")
                    status_current = op.get("status_current")
                    group_segments = op.get("group_segments")
                    has_other_group_in_month = op.get("has_other_group_in_month")
                else:
                    operator_id = op[0] if len(op) > 0 else None
                    operator_name = op[1] if len(op) > 1 else None
                    direction_id = op[2] if len(op) > 2 else None
                    operator_supervisor_id = op[3] if len(op) > 3 else None
                    hire_date = op[4] if len(op) > 4 else None
                    birth_date = op[11] if len(op) > 11 else None
                    hours_table_url = op[5] if len(op) > 5 else None
                    scores_table_url = op[6] if len(op) > 6 else None
                    operator_supervisor_name = op[7] if len(op) > 7 else None
                    status = op[8] if len(op) > 8 else None
                    rate = op[9] if len(op) > 9 else None
                    gender = op[10] if len(op) > 10 else None
                    role_value = "operator"
                    avatar_url = None
                    avatar_bucket = op[12] if len(op) > 12 else None
                    avatar_blob_path = op[13] if len(op) > 13 else None
                    status_period_status_code = op[15] if len(op) > 15 else None
                    status_period_start_date = op[16] if len(op) > 16 else None
                    status_period_end_date = op[17] if len(op) > 17 else None
                    status_period_dismissal_reason = op[18] if len(op) > 18 else ""
                    status_period_is_blacklist = bool(op[19]) if len(op) > 19 and op[19] is not None else False
                    status_period_comment = op[20] if len(op) > 20 else ""
                    dismissal_date = None
                    status_current = None
                    group_segments = None
                    has_other_group_in_month = None

                # skip invalid rows
                if not operator_id:
                    continue

                try:
                    operator_id_int = int(operator_id)
                except (TypeError, ValueError):
                    operator_id_int = None
                metrics = operator_metrics.get(operator_id_int, {}) if operator_id_int is not None else {}
                evaluation_target = operator_targets.get(operator_id_int) if operator_id_int is not None else None
                feedback_stats = feedback_stats_by_operator.get(operator_id_int, {}) if operator_id_int is not None else {}
                eval_count = int(metrics.get("call_count") or 0)
                avg_score = metrics.get("avg_score")
                evaluation_row_count = int(metrics.get("evaluation_row_count") or eval_count or 0)
                has_evaluation_data = bool(metrics.get("has_evaluation_data") or evaluation_row_count > 0)
                if operator_id_int is not None:
                    seen_response_ids.add(operator_id_int)

                # ensure rate is numeric and reasonable
                try:
                    rate_val = float(rate) if rate is not None else 1.0
                except Exception:
                    rate_val = 1.0

                response_data["operators"].append({
                    "id": operator_id,
                    "name": operator_name,
                    "hire_date": hire_date,
                    "birth_date": (birth_date.strftime('%d-%m-%Y') if hasattr(birth_date, 'strftime') else birth_date),
                    "direction_id": direction_id,
                    "supervisor_id": operator_supervisor_id,
                    "supervisor_name": operator_supervisor_name,
                    "role": role_value or "operator",
                    # number of actual evaluated calls (with scores)
                    "call_count": eval_count,
                    "avg_score": avg_score,
                    "evaluation_row_count": evaluation_row_count,
                    "has_evaluation_data": has_evaluation_data,
                    "evaluation_target": evaluation_target,
                    "feedback_stats": feedback_stats,
                    "feedback_count": int(feedback_stats.get("feedback_count") or 0),
                    "feedback_overdue_count": int(feedback_stats.get("overdue_count") or 0),
                    "feedback_pending_count": int(feedback_stats.get("pending_count") or 0),
                    "scores_table_url": scores_table_url,
                    "status": status,
                    "rate": rate_val,
                    "gender": gender,
                    "avatar_url": avatar_url or _build_avatar_signed_url(avatar_bucket, avatar_blob_path),
                    "status_period_status_code": status_period_status_code,
                    "status_period_start_date": status_period_start_date,
                    "status_period_end_date": status_period_end_date,
                    "status_period_dismissal_reason": status_period_dismissal_reason or "",
                    "status_period_is_blacklist": bool(status_period_is_blacklist),
                    "status_period_comment": status_period_comment or "",
                    # Режим группы: статус здесь — НА КОНЕЦ МЕСЯЦА, поэтому рядом едут
                    # дата увольнения (по ней фронт делит вкладки) и сегодняшний статус.
                    # group_segments/has_other_group_in_month нужны, чтобы показать
                    # переведённого: метрики оценок месячные и группу не различают.
                    "dismissal_date": dismissal_date,
                    "status_current": status_current,
                    "group_segments": group_segments or [],
                    "has_other_group_in_month": bool(has_other_group_in_month)
                })

            for sv_row in supervisor_rows:
                try:
                    supervisor_id_int = int(sv_row[0])
                except (TypeError, ValueError, IndexError):
                    continue
                if supervisor_id_int in seen_response_ids:
                    continue

                sv_metrics = operator_metrics.get(supervisor_id_int, {})
                sv_evaluation_target = operator_targets.get(supervisor_id_int)
                sv_feedback_stats = feedback_stats_by_operator.get(supervisor_id_int, {})
                sv_call_count = int(sv_metrics.get("call_count") or 0)
                sv_evaluation_row_count = int(sv_metrics.get("evaluation_row_count") or sv_call_count or 0)
                sv_has_evaluation_data = bool(
                    sv_metrics.get("has_evaluation_data")
                    or sv_evaluation_row_count > 0
                )
                if not sv_has_evaluation_data:
                    continue

                sv_name = sv_row[1]
                sv_direction_id = sv_row[2]
                sv_hire_date = sv_row[3]
                sv_scores_table_url = sv_row[4]
                sv_status = sv_row[5]
                sv_rate = sv_row[6]
                sv_gender = sv_row[7]
                sv_birth_date = sv_row[8]
                sv_avatar_bucket = sv_row[9]
                sv_avatar_blob_path = sv_row[10]

                try:
                    sv_rate_val = float(sv_rate) if sv_rate is not None else 1.0
                except Exception:
                    sv_rate_val = 1.0

                response_data["operators"].append({
                    "id": supervisor_id_int,
                    "name": sv_name,
                    "hire_date": sv_hire_date.strftime('%d-%m-%Y') if hasattr(sv_hire_date, 'strftime') else sv_hire_date,
                    "birth_date": sv_birth_date.strftime('%d-%m-%Y') if hasattr(sv_birth_date, 'strftime') else sv_birth_date,
                    "direction_id": sv_direction_id,
                    "supervisor_id": supervisor_id_int,
                    "supervisor_name": sv_name,
                    "role": "sv",
                    "call_count": sv_call_count,
                    "avg_score": sv_metrics.get("avg_score"),
                    "evaluation_row_count": sv_evaluation_row_count,
                    "has_evaluation_data": sv_has_evaluation_data,
                    "evaluation_target": sv_evaluation_target,
                    "feedback_stats": sv_feedback_stats,
                    "feedback_count": int(sv_feedback_stats.get("feedback_count") or 0),
                    "feedback_overdue_count": int(sv_feedback_stats.get("overdue_count") or 0),
                    "feedback_pending_count": int(sv_feedback_stats.get("pending_count") or 0),
                    "scores_table_url": sv_scores_table_url,
                    "status": sv_status,
                    "rate": sv_rate_val,
                    "gender": sv_gender,
                    "avatar_url": _build_avatar_signed_url(sv_avatar_bucket, sv_avatar_blob_path),
                    "status_period_status_code": None,
                    "status_period_start_date": None,
                    "status_period_end_date": None,
                    "status_period_dismissal_reason": "",
                    "status_period_is_blacklist": False,
                    "status_period_comment": ""
                })

            return jsonify(response_data), 200

        except Exception:
            logging.exception("Error fetching SV data")
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sv/supervisor_hours/sync', methods=['POST'])
    @require_api_key
    def sync_supervisor_hours_from_clockster():
        """«Синхронизация с Clockster» на вкладке «Супервайзеры» (задача #352).

    Берёт отметки прямо из Clockster за период (не дожидаясь ночной сборки кэша
    «Отметок») и пересчитывает часы СВ отделов requester'а. Один запрос /schedules
    отдаёт весь период по 50 человек, поэтому месяц тянется за секунды.
    Body: {"date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"} — не длиннее
    SUPERVISOR_CLOCKSTER_SYNC_MAX_DAYS, конец не позже сегодня."""
        try:
            requester_id, user_data, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            scope = _supervisor_hours_settings_scope(requester_id, _normalize_user_role(user_data[3]))
            if not scope:
                return jsonify({"error": "Синхронизацию часов супервайзеров запускает руководитель отдела"}), 403
            from group_late import config as _group_late_config
            if not _group_late_config.is_clockster_configured():
                return jsonify({"error": "Clockster не подключён"}), 400
            payload = request.get_json(silent=True) or {}
            try:
                date_from = db._normalize_schedule_date(payload.get('date_from'))
                date_to = db._normalize_schedule_date(payload.get('date_to') or payload.get('date_from'))
            except (TypeError, ValueError):
                return jsonify({"error": "Укажите период: даты в формате ГГГГ-ММ-ДД"}), 400
            if date_to < date_from:
                date_from, date_to = date_to, date_from
            today = datetime.now(group_late.TZ).date()
            if date_to > today:
                date_to = today
            if date_from > date_to:
                return jsonify({"error": "Период ещё не наступил"}), 400
            if (date_to - date_from).days + 1 > SUPERVISOR_CLOCKSTER_SYNC_MAX_DAYS:
                return jsonify({"error": f"Период — не больше {SUPERVISOR_CLOCKSTER_SYNC_MAX_DAYS} дней"}), 400

            # День накануне и следующий — ради ночных смен: уход после полуночи
            # закрывает смену, а утренний уход — смену вчерашнего дня.
            stored = _sync_supervisor_clockster_marks(date_from - timedelta(days=1), min(date_to + timedelta(days=1), today))
            supervisors = [row['user_id'] for row in db.get_supervisor_hours_settings(scope)['supervisors']]
            summary = db.recalculate_supervisor_clockster_hours(
                date_from, date_to, supervisors, keep_break_before=today.replace(day=1))
            summary['marks'] = stored
            return jsonify({"status": "success", "date_from": date_from.isoformat(),
                            "date_to": date_to.isoformat(), **summary}), 200
        except _clockster_error_types() as e:
            logging.warning("Синхронизация часов СВ: Clockster недоступен: %s", e)
            return jsonify({"error": "Clockster не ответил — попробуйте позже"}), 502
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error in sync_supervisor_hours_from_clockster: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sv/supervisor_day', methods=['GET'])
    @require_api_key
    def supervisor_clockster_day():
        """Отметки Clockster и ручные отметки СВ за день с разбором пар."""
        try:
            requester_id, user_data, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            user_param = request.args.get('user_id')
            if not user_param or not str(user_param).isdigit():
                return jsonify({"error": "Не указан сотрудник"}), 400
            day = request.args.get('date')
            if not day:
                return jsonify({"error": "Не указан день"}), 400
            access = _supervisor_marks_access(requester_id, _normalize_user_role(user_data[3]), int(user_param))
            if not access:
                return jsonify({"error": "Forbidden"}), 403
            return _supervisor_day_response(int(user_param), day, access)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error in supervisor_clockster_day: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sv/supervisor_marks', methods=['POST'])
    @require_api_key
    def add_supervisor_manual_mark():
        """РОП дописывает СВ приход/уход или исправляет отметку Clockster.
    Body: {"user_id", "at": "YYYY-MM-DDTHH:MM" (по Алматы), "kind": "in"|"out",
    "comment"?, "replaces_clockster"?: {"at", "kind"} — исправляемая отметка,
    "view_date"? — день окна, который вернуть обновлённым}."""
        try:
            requester_id, user_data, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            payload = request.get_json(silent=True) or {}
            try:
                user_id = int(payload.get('user_id'))
            except (TypeError, ValueError):
                return jsonify({"error": "Не указан сотрудник"}), 400
            access = _supervisor_marks_access(requester_id, _normalize_user_role(user_data[3]), user_id)
            if access != 'edit':
                return jsonify({"error": "Отметки дописывает руководитель отдела"}), 403
            view_date = _supervisor_view_date(payload.get('view_date'))
            replaces = payload.get('replaces_clockster')
            added = db.add_supervisor_manual_mark(
                user_id, payload.get('at'), payload.get('kind'),
                actor_id=requester_id, comment=payload.get('comment'),
                replaces_clockster=replaces if isinstance(replaces, dict) else None)
            return _supervisor_day_response(user_id, view_date or added['day'], access)
        except PermissionError as e:
            return jsonify({"error": str(e)}), 403
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error in add_supervisor_manual_mark: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sv/supervisor_marks/<int:mark_id>', methods=['PATCH', 'DELETE', 'OPTIONS'])
    @require_api_key
    def supervisor_manual_mark_item(mark_id):
        """РОП меняет (PATCH {"at", "kind", "comment"?}) или снимает (DELETE) ручную
    отметку СВ. Право — по владельцу отметки, как у добавления: так РОП снимет
    и отметку уволенного СВ, которую сам же дописал. ?view_date= / "view_date" —
    день окна, который вернуть обновлённым."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, user_data, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            owner_id = db.supervisor_manual_mark_user(mark_id)
            access = _supervisor_marks_access(requester_id, _normalize_user_role(user_data[3]), owner_id)
            if access != 'edit':
                return jsonify({"error": "Отметки меняет руководитель отдела"}), 403
            if request.method == 'PATCH':
                payload = request.get_json(silent=True) or {}
                view_date = _supervisor_view_date(payload.get('view_date'))
                changed = db.update_supervisor_manual_mark(
                    mark_id, payload.get('at'), payload.get('kind'),
                    actor_id=requester_id, comment=payload.get('comment'))
            else:
                view_date = _supervisor_view_date(request.args.get('view_date'))
                changed = db.delete_supervisor_manual_mark(mark_id, actor_id=requester_id)
            return _supervisor_day_response(owner_id, view_date or changed['day'], access)
        except PermissionError as e:
            return jsonify({"error": str(e)}), 403
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error in supervisor_manual_mark_item: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    return bp
