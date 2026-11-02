"""Раздел «technical_issues»: ручки /api/technical_issues — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_technical_issues_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, g, jsonify, request, send_file
from database import TECHNICAL_ISSUE_REASONS
from database import db
from database import technical_issue_comment_rules_payload
from datetime import datetime
from io import BytesIO
from openpyxl import Workbook
import logging



def build_technical_issues_blueprint(
    *,
    ALLOWED_ORIGINS,
    ROLE_HIERARCHY,
    _build_cors_preflight_response,
    _effective_management_scope,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_allowed_origin,
    _is_supervisor_role,
    _management_scope_for_targets,
    _normalize_int_id_list,
    _normalize_management_role,
    _normalize_origin,
    _normalize_user_role,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('technical_issues', __name__, url_prefix='/api')

    @bp.route('/technical_issues/reasons', methods=['GET'])
    @require_api_key
    def technical_issue_reasons():
        try:
            requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401
            requester_id = int(requester_id)

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "User not found"}), 404

            requester_role = _normalize_management_role(requester[3])
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or _headed_department_id(requester_id) is not None):
                return jsonify({"error": "Forbidden"}), 403

            return jsonify({
                "status": "success",
                "reasons": list(TECHNICAL_ISSUE_REASONS),
                "comment_required_reasons": technical_issue_comment_rules_payload()
            }), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error fetching technical issue reasons: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/technical_issues/workplace_settings', methods=['GET', 'OPTIONS'])
    @require_api_key
    def technical_issue_workplace_settings():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401
            requester_id = int(requester_id)

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "User not found"}), 404

            requester_role = _normalize_management_role(requester[3])
            effective_role, scope_department_id = _effective_management_scope(requester, requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or _headed_department_id(requester_id) is not None):
                return jsonify({"error": "Forbidden"}), 403

            result = db.get_technical_issue_workplace_settings(
                requester_id=requester_id,
                requester_role=effective_role
            )

            return jsonify({
                "status": "success",
                "items": result.get('items') or []
            }), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error fetching technical issue workplace settings: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/technical_issues/workplace_settings/<int:workplace_number>', methods=['PUT', 'PATCH', 'OPTIONS'])
    @require_api_key
    def update_technical_issue_workplace_setting(workplace_number):
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401
            requester_id = int(requester_id)

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "User not found"}), 404

            requester_role = _normalize_management_role(requester[3])
            if not _is_admin_role(requester_role):
                return jsonify({"error": "Only admin can update technical issue workplace settings"}), 403

            payload = request.get_json(silent=True) or {}
            for_supervisor = payload.get('for_supervisor')
            if for_supervisor is None:
                for_supervisor = payload.get('is_for_supervisor')
            if for_supervisor is None:
                for_supervisor = payload.get('forSupervisor')

            item = db.update_technical_issue_workplace_setting(
                requester_id=requester_id,
                requester_role=requester_role,
                workplace_number=workplace_number,
                for_supervisor=for_supervisor
            )

            return jsonify({
                "status": "success",
                "item": item
            }), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error updating technical issue workplace setting {workplace_number}: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/technical_issues', methods=['POST'])
    @require_api_key
    def create_technical_issue():
        try:
            requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401
            requester_id = int(requester_id)

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "User not found"}), 404

            requester_role = _normalize_management_role(requester[3])
            effective_role, scope_department_id = _effective_management_scope(requester, requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or _headed_department_id(requester_id) is not None):
                return jsonify({"error": "Forbidden"}), 403

            payload = request.get_json(silent=True) or {}
            issue_date = payload.get('date') or payload.get('issue_date')
            start_time = payload.get('start_time') or payload.get('start')
            end_time = payload.get('end_time') or payload.get('end')
            reason = payload.get('reason')
            comment = payload.get('comment')
            workplace_number = payload.get('workplace_number')
            if workplace_number is None:
                workplace_number = payload.get('workplace')

            operator_ids = _normalize_int_id_list(payload.get('operator_ids'))
            operator_ids.extend(_normalize_int_id_list(payload.get('operator_id')))
            operator_ids = _normalize_int_id_list(operator_ids)

            direction_ids = _normalize_int_id_list(payload.get('direction_ids'))
            direction_ids.extend(_normalize_int_id_list(payload.get('direction_id')))
            direction_ids = _normalize_int_id_list(direction_ids)
            effective_role, scope_department_id = _management_scope_for_targets(
                requester, requester_id, operator_ids, direction_ids)

            result = db.create_operator_technical_issues(
                requester_id=requester_id,
                requester_role=effective_role,
                issue_date=issue_date,
                start_time=start_time,
                end_time=end_time,
                reason=reason,
                comment=comment,
                operator_ids=operator_ids,
                direction_ids=direction_ids,
                workplace_number=workplace_number,
                scope_department_id=scope_department_id
            )

            return jsonify({
                "status": "success",
                "message": "Technical issue saved",
                "result": result
            }), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error creating technical issue: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/technical_issues', methods=['GET'])
    @require_api_key
    def list_technical_issues():
        try:
            requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401
            requester_id = int(requester_id)

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "User not found"}), 404

            requester_role = _normalize_management_role(requester[3])
            effective_role, scope_department_id = _effective_management_scope(requester, requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or _headed_department_id(requester_id) is not None):
                return jsonify({"error": "Forbidden"}), 403

            issue_date = (request.args.get('date') or '').strip() or None
            date_from = (request.args.get('date_from') or '').strip() or None
            date_to = (request.args.get('date_to') or '').strip() or None
            operator_id = (request.args.get('operator_id') or '').strip() or None
            group_id = (request.args.get('group_id') or '').strip() or None
            reason = (request.args.get('reason') or '').strip() or None
            workplace_number = (request.args.get('workplace_number') or request.args.get('workplace') or '').strip() or None
            limit = request.args.get('limit', 500)
            offset = request.args.get('offset', 0)

            result = db.get_operator_technical_issues(
                requester_id=requester_id,
                requester_role=effective_role,
                issue_date=issue_date,
                date_from=date_from,
                date_to=date_to,
                operator_id=operator_id,
                group_id=group_id,
                reason=reason,
                workplace_number=workplace_number,
                limit=limit,
                offset=offset,
                scope_department_id=scope_department_id
            )

            return jsonify({
                "status": "success",
                "total": int(result.get('total') or 0),
                "items": result.get('items') or [],
                "reasons": list(TECHNICAL_ISSUE_REASONS),
                "comment_required_reasons": technical_issue_comment_rules_payload()
            }), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error listing technical issues: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/technical_issues/<int:issue_id>', methods=['DELETE'])
    @require_api_key
    def delete_technical_issue(issue_id):
        try:
            requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401
            requester_id = int(requester_id)

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "User not found"}), 404

            requester_role = _normalize_management_role(requester[3])
            effective_role, scope_department_id = _effective_management_scope(requester, requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or _headed_department_id(requester_id) is not None):
                return jsonify({"error": "Forbidden"}), 403

            result = db.delete_operator_technical_issue(
                requester_id=requester_id,
                requester_role=effective_role,
                issue_id=issue_id,
                scope_department_id=scope_department_id
            )

            return jsonify({
                "status": "success",
                "message": "Technical issue deleted",
                "result": result
            }), 200
        except PermissionError:
            return jsonify({"error": "Forbidden"}), 403
        except ValueError as e:
            error_text = str(e)
            if error_text == "Technical issue not found":
                return jsonify({"error": error_text}), 404
            return jsonify({"error": error_text}), 400
        except Exception as e:
            logging.error(f"Error deleting technical issue {issue_id}: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/technical_issues/<int:issue_id>', methods=['PUT', 'PATCH'])
    @require_api_key
    def update_technical_issue_batch(issue_id):
        try:
            requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401
            requester_id = int(requester_id)

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "User not found"}), 404

            requester_role = _normalize_management_role(requester[3])
            effective_role, scope_department_id = _effective_management_scope(requester, requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or _headed_department_id(requester_id) is not None):
                return jsonify({"error": "Forbidden"}), 403

            payload = request.get_json(silent=True) or {}
            workplace_number = payload.get('workplace_number')
            if workplace_number is None:
                workplace_number = payload.get('workplace')

            direction_ids = _normalize_int_id_list(payload.get('direction_ids'))
            direction_ids.extend(_normalize_int_id_list(payload.get('direction_id')))
            direction_ids = _normalize_int_id_list(direction_ids)

            result = db.update_operator_technical_issue_batch(
                requester_id=requester_id,
                requester_role=effective_role,
                issue_id=issue_id,
                issue_date=payload.get('date') or payload.get('issue_date'),
                start_time=payload.get('start_time') or payload.get('start'),
                end_time=payload.get('end_time') or payload.get('end'),
                reason=payload.get('reason'),
                comment=payload.get('comment'),
                direction_ids=direction_ids,
                workplace_number=workplace_number,
                scope_department_id=scope_department_id
            )

            return jsonify({
                "status": "success",
                "message": "Massive technical issue updated",
                "result": result
            }), 200
        except PermissionError:
            return jsonify({"error": "Forbidden"}), 403
        except ValueError as e:
            error_text = str(e)
            if error_text == "Technical issue not found":
                return jsonify({"error": error_text}), 404
            return jsonify({"error": error_text}), 400
        except Exception as e:
            logging.error(f"Error updating technical issue batch {issue_id}: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/technical_issues/export_excel', methods=['GET'])
    @require_api_key
    def export_technical_issues_excel():
        try:
            requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401
            requester_id = int(requester_id)

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "User not found"}), 404

            requester_role = _normalize_management_role(requester[3])
            effective_role, scope_department_id = _effective_management_scope(requester, requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or _headed_department_id(requester_id) is not None):
                return jsonify({"error": "Only admin and sv can export technical issues"}), 403

            issue_date = (request.args.get('date') or '').strip() or None
            date_from = (request.args.get('date_from') or '').strip() or None
            date_to = (request.args.get('date_to') or '').strip() or None
            operator_id = (request.args.get('operator_id') or '').strip() or None
            reason = (request.args.get('reason') or '').strip() or None
            workplace_number = (request.args.get('workplace_number') or request.args.get('workplace') or '').strip() or None

            result = db.get_operator_technical_issues(
                requester_id=requester_id,
                requester_role=effective_role,
                issue_date=issue_date,
                date_from=date_from,
                date_to=date_to,
                operator_id=operator_id,
                reason=reason,
                workplace_number=workplace_number,
                limit=5000,
                offset=0,
                scope_department_id=scope_department_id
            )
            rows = result.get('items') or []

            wb = Workbook()
            ws = wb.active
            ws.title = 'Тех причины'

            headers = [
                'Дата проблемы',
                'Время начала',
                'Время окончания',
                'Оператор',
                'Рабочее место',
                'Для супервайзера',
                'Направление оператора',
                'Техническая причина',
                'Комментарий',
                'Выбранные направления',
                'Добавил',
                'Дата фиксации'
            ]
            for col_idx, title in enumerate(headers, start=1):
                cell = ws.cell(row=1, column=col_idx, value=title)
                cell.font = cell.font.copy(bold=True)

            for row_idx, item in enumerate(rows, start=2):
                selected_directions = ', '.join(item.get('selected_direction_names') or [])
                ws.cell(row=row_idx, column=1, value=item.get('date') or '')
                ws.cell(row=row_idx, column=2, value=item.get('start_time') or '')
                ws.cell(row=row_idx, column=3, value=item.get('end_time') or '')
                ws.cell(row=row_idx, column=4, value=item.get('operator_name') or '')
                ws.cell(row=row_idx, column=5, value=item.get('workplace_number') or '')
                ws.cell(row=row_idx, column=6, value='Да' if item.get('for_supervisor') else 'Нет')
                ws.cell(row=row_idx, column=7, value=item.get('direction_name') or '')
                ws.cell(row=row_idx, column=8, value=item.get('reason') or '')
                ws.cell(row=row_idx, column=9, value=item.get('comment') or '')
                ws.cell(row=row_idx, column=10, value=selected_directions)
                ws.cell(row=row_idx, column=11, value=item.get('created_by_name') or '')
                ws.cell(row=row_idx, column=12, value=item.get('created_at') or '')

            for column_cells in ws.columns:
                max_len = 0
                for cell in column_cells:
                    try:
                        max_len = max(max_len, len(str(cell.value or '')))
                    except Exception:
                        continue
                ws.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 60)

            filename = f"technical_issues_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            output = BytesIO()
            wb.save(output)
            output.seek(0)

            return send_file(
                output,
                as_attachment=True,
                download_name=filename,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error exporting technical issues: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    return bp
