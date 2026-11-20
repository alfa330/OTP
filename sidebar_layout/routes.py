"""HTTP личной раскладки сайдбара (Flask Blueprint).

    GET    /api/sidebar_layout   своя раскладка или null (стандартное меню)
    PUT    /api/sidebar_layout   {"layout": {...}} — сохранить свою
    DELETE /api/sidebar_layout   вернуть стандартное меню

Чья раскладка — решает ТОЛЬКО сессия: номера сотрудника ручка не принимает, и
чужую раскладку ни прочитать, ни перезаписать нельзя. Раскладка ничего не
открывает (см. layout.py), поэтому ручка доступна любой учётке.

Фабрика получает зависимости аргументами и не импортирует bot_schedule2 — тот
сам подключает модуль (тот же приём, что у my_data и sign_links).
"""

import json
import logging

from flask import Blueprint, jsonify, request

from .layout import MAX_BODY_BYTES, LayoutError, normalize_layout


def _stamp(value):
    """updated_at наружу — строкой: клиент сравнивает её как метку версии."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def load_layout(cursor, user_id):
    """(раскладка или None, updated_at строкой или None)."""
    cursor.execute(
        "SELECT layout, updated_at FROM user_sidebar_layouts WHERE user_id = %s",
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None, None
    value = row[0]
    if isinstance(value, str):
        value = json.loads(value)
    try:
        return normalize_layout(value), _stamp(row[1])
    except LayoutError:
        # Строка из старой версии формы — меню откроется стандартным.
        return None, _stamp(row[1])


def save_layout(cursor, user_id, layout):
    """Записывает раскладку и возвращает новую отметку updated_at."""
    cursor.execute(
        """
        INSERT INTO user_sidebar_layouts (user_id, layout, updated_at)
        VALUES (%s, %s::jsonb, NOW())
        ON CONFLICT (user_id) DO UPDATE
            SET layout = EXCLUDED.layout, updated_at = NOW()
        RETURNING updated_at
        """,
        (user_id, json.dumps(layout, ensure_ascii=False)),
    )
    row = cursor.fetchone()
    return _stamp(row[0]) if row else None


def delete_layout(cursor, user_id):
    cursor.execute("DELETE FROM user_sidebar_layouts WHERE user_id = %s", (user_id,))


def build_sidebar_layout_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester):
    bp = Blueprint('sidebar_layout', __name__, url_prefix='/api/sidebar_layout')

    @bp.route('', methods=['GET', 'PUT', 'DELETE', 'OPTIONS'], endpoint='sidebar_layout')
    @require_api_key
    def sidebar_layout():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        requester_id, _requester, error = resolve_requester()
        if error:
            message, status = error
            return jsonify({"error": message}), status
        user_id = int(requester_id)
        try:
            if request.method == 'GET':
                with db._get_cursor() as cursor:
                    layout, updated_at = load_layout(cursor, user_id)
                return jsonify({"status": "success", "layout": layout, "updated_at": updated_at}), 200

            if request.method == 'DELETE':
                with db._get_cursor() as cursor:
                    delete_layout(cursor, user_id)
                return jsonify({"status": "success", "layout": None, "updated_at": None}), 200

            if (request.content_length or 0) > MAX_BODY_BYTES:
                return jsonify({"error": "Раскладка слишком большая", "code": "SIDEBAR_LAYOUT_TOO_LARGE"}), 413
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or set(payload) != {'layout'}:
                return jsonify({"error": "Ожидается {\"layout\": {...}}", "code": "SIDEBAR_LAYOUT_INVALID"}), 400
            try:
                layout = normalize_layout(payload['layout'])
            except LayoutError as exc:
                return jsonify({"error": str(exc), "code": "SIDEBAR_LAYOUT_INVALID"}), 400
            with db._get_cursor() as cursor:
                updated_at = save_layout(cursor, user_id, layout)
            return jsonify({"status": "success", "layout": layout, "updated_at": updated_at}), 200
        except Exception:  # noqa: BLE001
            logging.exception('sidebar_layout: ошибка в %s', request.method)
            return jsonify({"error": "Не удалось сохранить порядок разделов"}), 500

    return bp
