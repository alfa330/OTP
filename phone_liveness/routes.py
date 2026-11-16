# -*- coding: utf-8 -*-
"""HTTP пульса телефона (Flask Blueprint /api/admin/phone-liveness) — один роут для супер-админа.

POST /api/admin/phone-liveness/close-hang  {"operator_id": 123, "at": "2026-11-09 12:00"}
    Закрыть уже случившееся зависание статуса: телефон умер молча, а сервер держал последний
    статус, пока телефон не запустили снова. «at» — любой момент внутри зависшего отрезка
    (время Алматы). Условия и момент закрытия — PhoneLivenessService.close_hang.

Фабрика получает зависимости аргументами: обратный импорт из монолита был бы циклом.
"""
import logging
from datetime import datetime

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)


def build_phone_liveness_blueprint(*, service, require_api_key, build_cors_preflight_response,
                                   resolve_requester, is_super_admin):
    bp = Blueprint('phone_liveness', __name__, url_prefix='/api/admin/phone-liveness')

    @bp.route('/close-hang', methods=['POST', 'OPTIONS'])
    @require_api_key
    def close_hang():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        requester_id, requester, error = resolve_requester()
        if error:
            message, status = error
            return jsonify({"error": message}), status
        if not is_super_admin(requester_id, requester):
            return jsonify({"error": "Только супер-админ"}), 403
        data = request.get_json(silent=True) or {}
        try:
            operator_id = int(data.get('operator_id'))
            around = datetime.fromisoformat(str(data.get('at') or '').strip())
        except (TypeError, ValueError):
            return jsonify({"error": "Нужны operator_id и at (дата и время, например 2026-11-09 12:00)"}), 400
        if around.tzinfo is not None:
            return jsonify({"error": "at — время Алматы без часового пояса"}), 400
        try:
            result = service.close_hang(operator_id, around)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            log.exception('phone_liveness: закрытие зависания оператора %s упало', operator_id)
            return jsonify({"error": "Не удалось закрыть зависание"}), 500
        log.info('phone_liveness: супер-админ %s закрыл зависание оператора %s: %s', requester_id, operator_id, result)
        return jsonify({"status": "success", "result": result}), 200

    return bp
