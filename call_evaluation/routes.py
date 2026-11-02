"""Раздел «call_evaluation»: ручки /api/call_evaluation — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_call_evaluation_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, jsonify, request
from database import db
from datetime import datetime
from datetime import timedelta
from google.cloud import storage as gcs_storage
from werkzeug.utils import secure_filename
import html
import json
import logging
import os
import requests
import threading
import uuid


def _c2d_eval_normalize_quotes(quotes, messages):
    """Валидация цитат: текст обязан дословно (с точностью до пробелов)
    присутствовать в сообщении, на которое ссылается. Возвращает (list, error)."""
    def squash(text):
        return ' '.join(str(text or '').split()).lower()

    by_id = {str(m.get('id')): squash(m.get('text')) for m in (messages or [])}
    normalized = []
    for quote in (quotes or []):
        if not isinstance(quote, dict):
            continue
        text = str(quote.get('text') or '').strip()
        if not text:
            continue
        message_id = quote.get('messageId')
        message_text = by_id.get(str(message_id))
        if message_text is None:
            return None, f"Сообщение цитаты не найдено в снапшоте (id {message_id})"
        if squash(text) not in message_text:
            return None, "Цитата не совпадает с текстом сообщения — выделите фрагмент заново"
        normalized.append({
            'messageId': message_id,
            'text': text[:2000],
            'comment': str(quote.get('comment') or '').strip()[:2000] or None,
        })
    return normalized, None


def build_call_evaluation_blueprint(
    *,
    ALLOWED_ORIGINS,
    API_TOKEN,
    ROLE_HIERARCHY,
    TASK_TELEGRAM_TIMEOUT_SECONDS,
    TELEGRAM_MAX_MESSAGE_CHARS,
    _authorize_operator_scope,
    _build_cors_preflight_response,
    _ensure_call_access_for_requester,
    _escape_telegram_html,
    _get_authenticated_requester,
    _get_call_reevaluation_context,
    _get_role_level,
    _get_telegram_error_text,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_allowed_origin,
    _is_global_admin_requester,
    _is_super_admin_role,
    _is_supervisor_role,
    _normalize_origin,
    _normalize_reevaluation_request_role,
    _normalize_user_role,
    _parse_boolean_setting,
    _resolve_reevaluation_request,
    _send_telegram_text_message,
    _signed_audio_response_type,
    _truncate_for_telegram,
    background_upload_and_notify,
    get_gcs_client,
    require_api_key,
    send_telegram_notification,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('call_evaluation', __name__, url_prefix='/api')

    def _build_call_evaluation_notification_settings_payload(requester_id, requester, is_admin, headed_dept_id):
        setting = db.get_call_evaluation_notify_setting(requester_id)
        if setting is None:
            return None

        if is_admin:
            departments = [
                dept for dept in (db.get_departments() or [])
                if isinstance(dept, dict) and dept.get('is_active') is not False
            ]
            department_id = setting.get('department_id')
            department_name = setting.get('department_name')
            if department_id and not department_name:
                dept = db.get_department_by_id(department_id)
                department_name = (dept or {}).get('name') if isinstance(dept, dict) else None
            enabled = bool(setting.get('enabled') and department_id is not None)
            scope = 'admin'
        else:
            dept = db.get_department_by_id(headed_dept_id) if headed_dept_id else None
            departments = [dept] if isinstance(dept, dict) else []
            department_id = int(headed_dept_id) if headed_dept_id is not None else None
            department_name = (dept or {}).get('name') if isinstance(dept, dict) else None
            enabled = bool(setting.get('enabled') and setting.get('department_id') == department_id)
            scope = 'department'

        return {
            "status": "success",
            "enabled": enabled,
            "telegram_connected": bool(requester[1]) if requester else False,
            "scope": scope,
            "department_id": department_id,
            "department_name": department_name,
            "departments": departments,
        }

    def _reevaluation_request_role_label(role):
        role_norm = _normalize_reevaluation_request_role(role)
        labels = {
            'operator': 'оператора',
            'sv': 'супервайзера',
            'admin': 'администратора'
        }
        return labels.get(role_norm, 'сотрудника')

    def _build_reevaluation_request_admin_message(call_ctx, requester_name, requester_role, comment):
        performer_name = (
            call_ctx.get('supervisor_name') if requester_role == 'operator' else requester_name
        ) or 'Сотрудник'
        comment_text = str(comment or '').strip() or '—'
        score_text = '—' if call_ctx.get('score') is None else f"{call_ctx.get('score'):.2f}"
        return (
            "⚠️ <b>Запрос на переоценку</b>\n\n"
            f"<b>Источник:</b> от {_escape_telegram_html(_reevaluation_request_role_label(requester_role), 32)}\n"
            f"<b>Call ID:</b> <code>{_escape_telegram_html(call_ctx.get('call_id'), 32)}</code>\n"
            f"<b>Оператор:</b> {_escape_telegram_html(call_ctx.get('operator_name') or '—', 120)}\n"
            f"<b>Супервайзер:</b> {_escape_telegram_html(call_ctx.get('supervisor_name') or '—', 120)}\n"
            f"<b>Запросил:</b> {_escape_telegram_html(requester_name or 'Сотрудник', 120)}\n"
            f"<b>Переоценит:</b> {_escape_telegram_html(performer_name, 120)}\n"
            f"<b>Номер:</b> {_escape_telegram_html(call_ctx.get('phone_number') or '—', 64)}\n"
            f"<b>Дата обращения:</b> {_escape_telegram_html(call_ctx.get('appeal_date_text') or '—', 32)}\n"
            f"<b>Месяц:</b> {_escape_telegram_html(call_ctx.get('month') or '—', 16)}\n"
            f"<b>Оценка:</b> {_escape_telegram_html(score_text, 16)}\n"
            f"<b>Комментарий:</b> {_escape_telegram_html(comment_text, 600)}\n\n"
            "Выберите действие ниже."
        )

    def _notify_telegram_about_new_reevaluation_request(call_ctx, requester_name, requester_role, comment):
        if not API_TOKEN:
            return

        try:
            recipients = db.get_call_evaluation_notify_recipients(call_ctx.get('operator_id')) or []
        except Exception as exc:
            logging.error("Failed to load reevaluation notification recipients: %s", exc, exc_info=True)
            recipients = []

        for recipient in recipients:
            admin_chat_id = recipient.get('telegram_id') if isinstance(recipient, dict) else None
            if not admin_chat_id:
                continue
            reply_markup = {
                "inline_keyboard": [[
                    {"text": "Одобрить ✅", "callback_data": f"approve_reval:{call_ctx['call_id']}"},
                    {"text": "Отклонить ❌", "callback_data": f"reject_reval:{call_ctx['call_id']}"}
                ]]
            }
            try:
                response = _send_telegram_text_message(
                    chat_id=admin_chat_id,
                    text=_build_reevaluation_request_admin_message(call_ctx, requester_name, requester_role, comment),
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                if response.status_code != 200:
                    logging.error(
                        "Failed to send reevaluation request to admin chat: %s",
                        _get_telegram_error_text(response)
                    )
            except Exception as exc:
                logging.error("Failed to send telegram reevaluation admin notification: %s", exc)

        if requester_role == 'operator' and call_ctx.get('supervisor_tg_id'):
            supervisor_message = (
                "📨 <b>Новая заявка оператора на переоценку</b>\n\n"
                f"<b>Оператор:</b> {_escape_telegram_html(call_ctx.get('operator_name') or '—', 120)}\n"
                f"<b>Call ID:</b> <code>{_escape_telegram_html(call_ctx.get('call_id'), 32)}</code>\n"
                f"<b>Комментарий:</b> {_escape_telegram_html(comment or '—', 600)}\n\n"
                "Заявка отправлена администратору на согласование. После одобрения вы сможете выполнить переоценку."
            )
            try:
                response = _send_telegram_text_message(chat_id=call_ctx['supervisor_tg_id'], text=supervisor_message, parse_mode='HTML')
                if response.status_code != 200:
                    logging.error(
                        "Failed to send reevaluation request notification to supervisor: %s",
                        _get_telegram_error_text(response)
                    )
            except Exception as exc:
                logging.error("Failed to notify supervisor about operator reevaluation request: %s", exc)

    def _create_reevaluation_request(call_id, requester, requester_id, comment):
        call_ctx = _get_call_reevaluation_context(call_id)
        if not call_ctx:
            raise ValueError("Call not found")
        if call_ctx.get('is_draft'):
            raise ValueError("Reevaluation request is available only for completed evaluations")

        requester_role = _normalize_reevaluation_request_role(requester[3] if requester else None)
        requester_base_role = _normalize_user_role(requester[3] if requester else None)
        is_department_head = (
            _headed_department_id(requester_id) is not None
            and not _is_super_admin_role(requester_base_role)
        )
        if is_department_head:
            if not _ensure_call_access_for_requester(call_ctx.get('operator_id'), requester, requester_id):
                raise PermissionError("Only admins or this department head can request reevaluation")
            requester_role = 'admin'
        elif requester_role == 'operator':
            if int(call_ctx.get('operator_id') or 0) != int(requester_id):
                raise PermissionError("Operators can request reevaluation only for their own calls")
            if not call_ctx.get('supervisor_id'):
                raise ValueError("Supervisor not found for this operator")
        elif requester_role == 'sv':
            if int(call_ctx.get('supervisor_id') or 0) != int(requester_id):
                raise PermissionError("Only the operator's supervisor can request reevaluation")
        elif requester_role != 'admin':
            raise PermissionError("Only operators, supervisors and admins can request reevaluation")

        if call_ctx.get('sv_request') and not call_ctx.get('sv_request_approved') and not call_ctx.get('sv_request_rejected'):
            raise RuntimeError("Reevaluation request is already pending")
        if call_ctx.get('sv_request') and call_ctx.get('sv_request_approved'):
            raise RuntimeError("Reevaluation request is already approved")

        request_comment = str(comment or '').strip()
        with db._get_cursor() as cursor:
            cursor.execute("""
            UPDATE calls
            SET sv_request = TRUE,
                sv_request_comment = %s,
                sv_request_by = %s,
                sv_request_by_role = %s,
                sv_request_at = %s,
                sv_request_approved = FALSE,
                sv_request_approved_by = NULL,
                sv_request_approved_at = NULL,
                sv_request_approve_comment = NULL,
                sv_request_rejected = FALSE,
                sv_request_rejected_by = NULL,
                sv_request_rejected_at = NULL,
                sv_request_reject_comment = NULL
            WHERE id = %s
        """, (request_comment, requester_id, requester_role, datetime.utcnow(), call_id))

        requester_name = requester[2] if requester and len(requester) > 2 else 'Сотрудник'
        _notify_telegram_about_new_reevaluation_request(call_ctx, requester_name, requester_role, request_comment)
        return {
            "call": call_ctx,
            "requester_role": requester_role,
            "requester_name": requester_name
        }

    @bp.route('/call_evaluation/notification_settings', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    def call_evaluation_notification_settings():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()

        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3]) if requester else ''
            is_admin = _is_admin_role(requester_role)
            headed_dept_id = db.headed_department_id_for_user(requester_id)
            if not is_admin and headed_dept_id is None:
                return jsonify({"error": "Only admins and department heads can manage evaluation notifications"}), 403

            if request.method == 'GET':
                payload = _build_call_evaluation_notification_settings_payload(
                    requester_id, requester, is_admin, headed_dept_id
                )
                if payload is None:
                    return jsonify({"error": "User not found"}), 404
                return jsonify(payload), 200

            data = request.get_json(silent=True) or {}
            raw_enabled = data.get('enabled', data.get('call_evaluation_notify_enabled'))
            if raw_enabled is None:
                return jsonify({"error": "enabled is required"}), 400
            enabled, parse_error = _parse_boolean_setting(raw_enabled, 'enabled')
            if parse_error:
                return jsonify({"error": parse_error}), 400

            if is_admin:
                raw_department_id = data.get('department_id', data.get('call_evaluation_notify_department_id'))
                if raw_department_id in (None, '') and enabled:
                    current = db.get_call_evaluation_notify_setting(requester_id) or {}
                    raw_department_id = current.get('department_id')
                department_id = None
                if raw_department_id not in (None, ''):
                    try:
                        department_id = int(raw_department_id)
                    except (TypeError, ValueError):
                        return jsonify({"error": "Invalid department_id"}), 400
                if enabled and department_id is None:
                    return jsonify({"error": "department_id is required"}), 400
                if department_id is not None:
                    department = db.get_department_by_id(department_id)
                    if not department or department.get('is_active') is False:
                        return jsonify({"error": "Department not found"}), 404
            else:
                department_id = int(headed_dept_id)
                department = db.get_department_by_id(department_id)
                if not department or department.get('is_active') is False:
                    return jsonify({"error": "Department not found"}), 404

            updated = db.set_call_evaluation_notify_setting(
                requester_id,
                bool(enabled),
                department_id
            )
            if updated is None:
                return jsonify({"error": "User not found"}), 404

            payload = _build_call_evaluation_notification_settings_payload(
                requester_id, requester, is_admin, headed_dept_id
            )
            if payload is None:
                return jsonify({"error": "User not found"}), 404
            return jsonify(payload), 200

        except Exception:
            logging.exception("Error in /api/call_evaluation/notification_settings")
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/call_evaluation/dispute', methods=['POST'])
    @require_api_key
    def dispute_call_evaluation():
        try:
            data = request.get_json(silent=True) or {}
            if 'id' not in data or 'dispute_text' not in data:
                return jsonify({"error": "Missing required fields"}), 400

            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            result = _create_reevaluation_request(
                call_id=int(data['id']),
                requester=requester,
                requester_id=requester_id,
                comment=data.get('dispute_text')
            )
            return jsonify({
                "status": "success",
                "message": "Reevaluation request created",
                "requester_role": result.get("requester_role")
            }), 200

            operator_id = int(data['operator_id'])
            operator = db.get_user(id=operator_id)
            if not operator:
                return jsonify({"error": "Operator not found"}), 404

            supervisor = db.get_user(id=operator[6]) if operator[6] else None
            if not supervisor:
                return jsonify({"error": "Supervisor not found for this operator"}), 404

            # Get call details
            evaluations = db.get_call_evaluations(operator_id, data['month'])
            call = next((e for e in evaluations if e['id'] == data['id']), None)
            if not call:
                return jsonify({"error": "Call evaluation not found"}), 404

            # --- NEW: Try to get audio signed URL ---
            audio_url = None
            if call.get('audio_path'):
                try:
                    gcs_client = get_gcs_client()
                    path_parts = call['audio_path'].split('/', 1)
                    if len(path_parts) == 2:
                        bucket_name, blob_path = path_parts
                        bucket = gcs_client.bucket(bucket_name)
                        blob = bucket.blob(blob_path)
                        if blob.exists():
                            audio_url = blob.generate_signed_url(
                                version="v4",
                                expiration=timedelta(minutes=15),
                                method="GET",
                                response_type=_signed_audio_response_type(blob.name)
                            )
                except Exception as e:
                    logging.error(f"Error generating signed audio URL for dispute: {e}")
            # --- END NEW ---

            # Сообщение для супервайзера
            supervisor_message = (
                f"⚠️ <b>Запрос на пересмотр оценки</b>\n\n"
                f"👤 Оператор: <b>{operator[2]}</b>\n"
                f"📞 Звонок ID: {call['id']}\n"
                f"📱 Номер: {call['phone_number']}\n"
                f"📅 Дата обращения: {' '.join(call['appeal_date'].split('T'))}\n"
                f"📅 Дата оценки: {call['created_at']}\n"
                f"💯 Оценка: {call['score']}\n"
                f"📅 За месяц: {call['month']}\n\n"
                f"📝 <b>Сообщение от оператора:</b>\n"
                f"{data['dispute_text']}"
            )

            # Сообщение для админа
            admin_message = (
                f"⚠️ <b>Запрос на пересмотр оценки</b>\n\n"
                f"💬 Супервайзер: <b>{supervisor[2]}</b>\n"
                f"👤 Оператор: <b>{operator[2]}</b>\n"
                f"📞 Звонок ID: {call['id']}\n"
                f"📅 Дата обращения: {' '.join(call['appeal_date'].split('T'))}\n"
                f"📅 Дата оценки: {call['created_at']}\n"
                f"📱 Номер: {call['phone_number']}\n"
                f"💯 Оценка: {call['score']}\n"
                f"📅 За месяц: {call['month']}\n\n"
                f"📝 <b>Сообщение от оператора:</b>\n"
                f"{data['dispute_text']}"
            )

            telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
            telegram_audio_url = f"https://api.telegram.org/bot{API_TOKEN}/sendAudio"

            # --- NEW: Send audio if available ---
            def send_telegram_message(chat_id, text, audio_url=None):
                if audio_url:
                    payload = {
                        "chat_id": chat_id,
                        "audio": audio_url,
                        "caption": text,
                        "parse_mode": "HTML"
                    }
                    resp = requests.post(telegram_audio_url, json=payload, timeout=10)
                else:
                    payload = {
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML"
                    }
                    resp = requests.post(telegram_url, json=payload, timeout=10)
                return resp

            # Отправка супервайзеру
            supervisor_response = send_telegram_message(supervisor[1], supervisor_message, audio_url)
            if supervisor_response.status_code != 200:
                error_detail = supervisor_response.json().get('description', 'Unknown error')
                logging.error(f"Telegram API error (supervisor): {error_detail}")
                return jsonify({"error": f"Failed to send dispute message to supervisor: {error_detail}"}), 500

            # Отправка админу
            admin_response = send_telegram_message(os.getenv("ADMIN_ID"), admin_message, audio_url)
            if admin_response.status_code != 200:
                error_detail = admin_response.json().get('description', 'Unknown error')
                logging.error(f"Telegram API error (admin): {error_detail}")
                return jsonify({"error": f"Failed to send dispute message to admin: {error_detail}"}), 500
            # --- END NEW ---

            return jsonify({"status": "success", "message": "Dispute sent to supervisor and admin"})
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except Exception as e:
            logging.error(f"Error processing dispute: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_evaluation/sv_request', methods=['POST'])
    @require_api_key
    def sv_request_call():
        """
    Супервайзер отправляет запрос на переоценку по конкретному звонку (call_id).
    Тело JSON: { "call_id": int, "comment": "..." }
    Заголовки: X-User-Id (JWT/cookie авторизация обязательна)
    """
        try:
            data = request.get_json(silent=True) or {}
            if 'call_id' not in data:
                return jsonify({"error": "Missing call_id"}), 400

            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            result = _create_reevaluation_request(
                call_id=int(data['call_id']),
                requester=requester,
                requester_id=requester_id,
                comment=data.get('comment')
            )
            return jsonify({
                "status": "success",
                "message": "sv_request created",
                "requester_role": result.get("requester_role")
            }), 200

            call_id = int(data['call_id'])
            comment = data.get('comment', '').strip()
            requester_id = int(request.headers.get('X-User-Id'))

            # Проверка: существует ли звонок и принадлежит ли оператор супервайзеру
            call = db.get_call_by_id(call_id)
            if not call:
                return jsonify({"error": "Call not found"}), 404

            # получить запись звонка с operator_id
            with db._get_cursor() as cursor:
                cursor.execute("SELECT operator_id, evaluator_id, month FROM calls WHERE id = %s", (call_id,))
                row = cursor.fetchone()
                if not row:
                    return jsonify({"error": "Call not found"}), 404
                operator_id, evaluator_id, call_month = row

            operator = db.get_user(id=operator_id)
            if not operator:
                return jsonify({"error": "Operator not found"}), 404

            # Проверяем что requester — супервайзер этого оператора (или админ)
            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 403

            if not _is_admin_role(requester[3]) and operator[6] != requester_id:
                return jsonify({"error": "Only the operator's supervisor or admin can request reevaluation"}), 403

            # Сохраняем заявку в calls
            with db._get_cursor() as cursor:
                cursor.execute("""
                UPDATE calls
                SET sv_request = TRUE,
                    sv_request_comment = %s,
                    sv_request_by = %s,
                    sv_request_at = %s,
                    sv_request_approved = FALSE,
                    sv_request_approved_by = NULL,
                    sv_request_approved_at = NULL
                WHERE id = %s
            """, (comment, requester_id, datetime.utcnow(), call_id))

            # Уведомляем админа через Telegram с inline-кнопкой
            API_TOKEN = os.getenv('BOT_TOKEN')
            admin_chat_id = os.getenv('ADMIN_ID')
            if API_TOKEN and admin_chat_id:
                telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
                text = (
                    f"⚠️ <b>Запрос на переоценку (от СВ)</b>\n\n"
                    f"📞 Call ID: <b>{call_id}</b>\n"
                    f"👤 Оператор ID: <b>{operator_id}</b> / {operator[2]}\n"
                    f"📄 Месяц: <b>{call_month}</b>\n"
                    f"📝 Комментарий: {comment or '-'}\n\n"
                    f"Нажмите кнопку, чтобы одобрить переоценку и открыть звонок для переоценки."
                )
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "Одобрить переоценку ✅", "callback_data": f"approve_reval:{call_id}"}
                        ]
                    ]
                }
                try:
                    requests.post(telegram_url, json={
                        "chat_id": admin_chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "reply_markup": json.dumps(reply_markup)
                    }, timeout=10)
                except Exception as e:
                    logging.error(f"Failed to send telegram sv_request notification: {e}")

            return jsonify({"status": "success", "message": "sv_request created"}), 200
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except Exception as e:
            logging.error(f"Error in sv_request_call: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_evaluation/request_decision', methods=['POST'])
    @require_api_key
    def decide_call_reevaluation_request():
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            requester_role = _normalize_user_role(requester[3])
            headed_dept_id = db.headed_department_id_for_user(requester_id)
            if not _is_admin_role(requester_role) and headed_dept_id is None:
                return jsonify({"error": "Only admins or department heads can decide reevaluation requests"}), 403

            data = request.get_json(silent=True) or {}
            if 'call_id' not in data or 'decision' not in data:
                return jsonify({"error": "Missing required fields"}), 400

            result = _resolve_reevaluation_request(
                call_id=int(data['call_id']),
                approver_user_id=requester_id,
                decision=data.get('decision'),
                comment=data.get('comment')
            )
            return jsonify({
                "status": "success",
                "message": "Reevaluation request updated",
                "decision": result.get("decision")
            }), 200
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except Exception as exc:
            logging.error("Error deciding reevaluation request: %s", exc, exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/call_evaluation/requests', methods=['GET'])
    @require_api_key
    def get_call_reevaluation_requests():
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3]) if requester else ''
            headed_dept_id = _headed_department_id(requester_id)
            is_department_head = headed_dept_id is not None and not _is_super_admin_role(requester_role)
            is_global_admin = _is_global_admin_requester(requester_role, requester_id)
            if not (is_global_admin or _is_supervisor_role(requester_role) or is_department_head):
                return jsonify({"error": "Only admins, supervisors or department heads can view reevaluation requests"}), 403

            month = request.args.get('month') or None

            operator_id_raw = request.args.get('operator_id')
            operator_id = None
            if operator_id_raw not in (None, ''):
                try:
                    operator_id = int(operator_id_raw)
                except (TypeError, ValueError):
                    return jsonify({"error": "Invalid operator_id"}), 400

            supervisor_id = None
            department_id = None
            if is_department_head:
                department_id = int(headed_dept_id)
                if operator_id is not None and not _authorize_operator_scope(requester, requester_id, operator_id):
                    return jsonify({"error": "Unauthorized to view this operator reevaluation requests"}), 403
            elif _is_supervisor_role(requester_role):
                supervisor_id = requester_id
                if operator_id is not None and not _authorize_operator_scope(requester, requester_id, operator_id):
                    return jsonify({"error": "Unauthorized to view this operator reevaluation requests"}), 403
            else:
                supervisor_id_raw = request.args.get('supervisor_id')
                if supervisor_id_raw not in (None, ''):
                    try:
                        supervisor_id = int(supervisor_id_raw)
                    except (TypeError, ValueError):
                        return jsonify({"error": "Invalid supervisor_id"}), 400

            requests_payload = db.get_call_reevaluation_requests(
                month=month,
                operator_id=operator_id,
                supervisor_id=supervisor_id,
                department_id=department_id
            )

            return jsonify({
                "status": "success",
                "requests": requests_payload
            }), 200
        except Exception as exc:
            logging.error("Error fetching reevaluation requests: %s", exc, exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/call_evaluation', methods=['POST'])
    @require_api_key
    def receive_call_evaluation():
        try:
            if not request.form:
                return jsonify({"error": "Missing form data"}), 400

            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3])
            requester_headed_dept = _headed_department_id(requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or requester_headed_dept is not None):
                return jsonify({"error": "Only admins, supervisors and department heads can submit evaluations"}), 403

            required_fields = ['evaluator', 'operator', 'phone_number', 'appeal_date', 'score', 'comment', 'month', 'is_draft']
            missing_fields = [field for field in required_fields if field not in request.form]
            if missing_fields:
                return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

            evaluator_name = request.form['evaluator']
            operator_name = request.form['operator']
            phone_number = request.form['phone_number']
            appeal_date = request.form['appeal_date']
            score = float(request.form['score'])
            comment = request.form['comment']
            month = request.form['month'] or datetime.now().strftime('%Y-%m')
            is_draft = request.form['is_draft'].lower() == 'true'
            comment_visible_to_operator_raw = str(request.form.get('comment_visible_to_operator', 'true')).strip().lower()
            comment_visible_to_operator = comment_visible_to_operator_raw in ('1', 'true', 'yes', 'on')
            question_resolved_raw = str(request.form.get('question_resolved', 'false')).strip().lower()
            question_resolved = question_resolved_raw in ('1', 'true', 'yes', 'on')
            resolved_first_contact = None
            if question_resolved:
                resolved_first_contact_raw = str(request.form.get('resolved_first_contact', 'false')).strip().lower()
                resolved_first_contact = resolved_first_contact_raw in ('1', 'true', 'yes', 'on')
            scores = json.loads(request.form.get('scores', '[]'))
            criterion_comments = json.loads(request.form.get('criterion_comments', '[]'))
            direction_id = request.form.get('direction')
            previous_version_id = request.form.get('previous_version_id')
            is_correction = request.form.get('is_correction', 'false').lower() == 'true'
            imported_call_id = request.form.get('imported_call_id')
            if imported_call_id not in (None, ''):
                try:
                    imported_call_id = int(imported_call_id)
                    if imported_call_id <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    return jsonify({"error": "imported_call_id must be a positive integer"}), 400
            else:
                imported_call_id = None

            evaluator = db.get_user(name=evaluator_name)
            operator = db.get_user(name=operator_name)
            if not evaluator or not operator:
                return jsonify({"error": "Evaluator or operator not found"}), 404
            if not _ensure_call_access_for_requester(operator[0], requester, requester_id):
                return jsonify({"error": "Forbidden for this operator"}), 403

            # Оценка чата Chat2Desk (ЧМ СЗоВ): к записи журнала прикладывается снапшот
            # переписки и цитаты; цитаты сверяются с текстом сообщений дословно.
            c2d_snapshot_id = request.form.get('c2d_snapshot_id')
            chat_quotes = None
            if c2d_snapshot_id not in (None, ''):
                try:
                    c2d_snapshot_id = int(c2d_snapshot_id)
                except (TypeError, ValueError):
                    return jsonify({"error": "c2d_snapshot_id must be an integer"}), 400
                chat_snapshot = db.get_c2d_snapshot(snapshot_id=c2d_snapshot_id)
                if not chat_snapshot:
                    return jsonify({"error": "Снапшот чата не найден"}), 404
                if chat_snapshot.get('operator_id') is not None and \
                        int(chat_snapshot['operator_id']) != int(operator[0]):
                    return jsonify({"error": "Снапшот чата принадлежит другому чат-менеджеру"}), 400
                try:
                    raw_quotes = json.loads(request.form.get('chat_quotes') or '[]')
                except ValueError:
                    return jsonify({"error": "chat_quotes must be valid JSON"}), 400
                chat_quotes, quotes_error = _c2d_eval_normalize_quotes(
                    raw_quotes, chat_snapshot.get('messages'))
                if quotes_error:
                    return jsonify({"error": quotes_error}), 400
            else:
                c2d_snapshot_id = None
            if is_correction and previous_version_id:
                try:
                    previous_call = db.get_call_by_id(int(previous_version_id))
                except (TypeError, ValueError):
                    previous_call = None
                if not previous_call:
                    return jsonify({"error": "Previous evaluation not found"}), 404
                if not _ensure_call_access_for_requester(previous_call.get("operator_id"), requester, requester_id):
                    return jsonify({"error": "Forbidden for this previous evaluation"}), 403
            if is_correction:
                # admins всегда могут делать переоценки
                if _is_admin_role(requester_role) or requester_headed_dept is not None:
                    allowed = True
                else:
                    # non-admin может переоценивать ТОЛЬКО если:
                    # - передан previous_version_id
                    # - previous call exists AND sv_request == TRUE AND sv_request_approved == TRUE
                    # - и sv_request_by == requester_id (т.е. именно этот СВ отправил запрос)
                    allowed = False
                    if previous_version_id:
                        try:
                            prev_id = int(previous_version_id)
                        except Exception:
                            prev_id = None
                        if prev_id:
                            try:
                                with db._get_cursor() as cursor:
                                    cursor.execute(
                                        """
                                    SELECT
                                        sv_request,
                                        sv_request_approved,
                                        sv_request_by,
                                        sv_request_by_role,
                                        operator_id
                                    FROM calls
                                    WHERE id = %s
                                    """,
                                        (prev_id,)
                                    )
                                    prev = cursor.fetchone()
                            except Exception as e:
                                logging.error(f"Error checking previous call for re-eval permissions: {e}")
                                prev = None

                            if prev:
                                sv_request_flag = bool(prev[0])
                                sv_request_approved_flag = bool(prev[1])
                                sv_request_by_id = prev[2]
                                sv_request_by_role = _normalize_reevaluation_request_role(prev[3])
                                prev_operator_id = int(prev[4]) if prev[4] is not None else None
                                if sv_request_flag and sv_request_approved_flag:
                                    if sv_request_by_role == 'sv' and sv_request_by_id == requester_id:
                                        allowed = True
                                    elif sv_request_by_role == 'operator' and prev_operator_id:
                                        prev_operator = db.get_user(id=prev_operator_id)
                                        if prev_operator and int(prev_operator[6] or 0) == int(requester_id):
                                            allowed = True

                if not allowed:
                    return jsonify({"error": "Only admins or the approved supervisor can perform re-evaluations"}), 403


            audio_path = None
            audio_data = None
            blob_path = None
            bucket_name = None
            has_new_audio = False

            if 'audio_file' in request.files:
                file = request.files['audio_file']
                if file and file.filename:
                    has_new_audio = True
                    try:
                        audio_data = file.read()
                    except Exception as e:
                        logging.error(f"Error reading audio file: {e}")
                        return jsonify({"error": "Failed to process audio file"}), 500
                    filename = secure_filename(f"{uuid.uuid4()}.mp3")
                    bucket_name = os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET')
                    upload_folder = os.getenv('UPLOAD_FOLDER', 'Uploads/')
                    blob_path = f"{upload_folder}{filename}"
                    audio_path = "my-app-audio-uploads/" + blob_path

            elif is_correction and previous_version_id:
                try:
                    with db._get_cursor() as cursor:
                        cursor.execute("SELECT audio_path FROM calls WHERE id = %s", (previous_version_id,))
                        result = cursor.fetchone()
                        if result and result[0]:
                            audio_path = result[0]
                except Exception as e:
                    logging.error(f"Error retrieving audio_path from previous version: {e}")
                    return jsonify({"error": "Failed to retrieve audio from previous version"}), 500

            evaluation_id = db.add_call_evaluation(
                evaluator_id=evaluator[0],
                operator_id=operator[0],
                phone_number=phone_number,
                score=score,
                comment=comment,
                comment_visible_to_operator=comment_visible_to_operator,
                month=month,
                audio_path=audio_path,
                is_draft=is_draft,
                scores=scores,
                criterion_comments=criterion_comments,
                direction_id=direction_id,
                is_correction=is_correction,
                previous_version_id=previous_version_id if previous_version_id else None,
                appeal_date=appeal_date,
                question_resolved=question_resolved,
                resolved_first_contact=resolved_first_contact,
                imported_call_id=imported_call_id,
                c2d_snapshot_id=c2d_snapshot_id,
                chat_quotes=chat_quotes
            )

            if has_new_audio or (not is_draft and audio_path):
                threading.Thread(target=background_upload_and_notify, args=(
                    audio_data, bucket_name, blob_path, evaluation_id, is_draft,
                    evaluator[2], operator[2], month, phone_number, score, comment,
                    is_correction, previous_version_id, audio_path if not has_new_audio else None,
                    appeal_date, operator[0]
                )).start()
            elif not is_draft:
                threading.Thread(target=send_telegram_notification, args=(
                    evaluator[2], operator[2], month, phone_number, score, comment,
                    is_correction, previous_version_id, None, appeal_date, operator[0]
                )).start()

            return jsonify({"status": "success", "evaluation_id": evaluation_id}), 200
        except Exception as e:
            logging.error(f"Error processing call evaluation: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    return bp
