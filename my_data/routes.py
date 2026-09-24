"""HTTP «Моих данных» (Flask Blueprint).

Фабрика получает зависимости аргументами и не импортирует bot_schedule2 —
тот сам подключает этот модуль (тот же приём, что у sign_links и parcels).

    GET  /api/my_data   свои шесть полей; от карты — только последние 4 цифры
    POST /api/my_data   правка своих полей; в ответе — то же, что отдаёт GET

Чей кабинет — решает ТОЛЬКО сессия. Номера сотрудника ручка не принимает вовсе:
ключ user_id в теле — такой же чужой ключ, как любое поле вне постановки, и
запрос с ним отклоняется целиком.
"""

import logging
from functools import wraps

from flask import Blueprint, jsonify, request

from . import fields, queries


def build_my_data_blueprint(*, db, require_api_key, build_cors_preflight_response,
                            resolve_requester, normalize_role, department_code_of):
    """Собирает Blueprint.

    normalize_role — (сырое значение роли) -> каноническая роль;
    department_code_of — (user_id) -> код отдела ('' если нет). Нужен монолитный
    _department_code_of_user: код в справочнике отделов заполнен не везде, и по
    одному полю оператор ОП молча остался бы без кабинета.
    """
    bp = Blueprint('my_data', __name__, url_prefix='/api/my_data')

    def my_data_route(methods):
        def decorator(handler):
            @bp.route('', methods=[*methods, 'OPTIONS'], endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper():
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    role = normalize_role(requester[3] if requester else None)
                    # Гейт на сервере, а не только в интерфейсе: спрятанный блок
                    # доступом не является, ручку можно дёрнуть напрямую.
                    if not fields.is_eligible(role, department_code_of(requester_id)):
                        return jsonify({
                            "error": "«Мои данные» доступны операторам СЗоВ, ОП и Тез",
                            "code": "MY_DATA_CLOSED",
                        }), 403
                    return handler(int(requester_id))
                except Exception:  # noqa: BLE001
                    # Без значений полей в логе: там телефон и номер карты.
                    logging.exception('my_data: ошибка в %s %s', request.method, request.path)
                    return jsonify({"error": "Не удалось обработать «Мои данные»"}), 500

            return wrapper

        return decorator

    def _view(user_id, *, for_save=None):
        with db._get_cursor() as cursor:
            if for_save is None:
                row = queries.load(cursor, user_id)
                changed = []
            else:
                result = queries.save(cursor, user_id, for_save)
                row, changed = result if result else (None, [])
        if row is None:
            return jsonify({"error": "Сотрудник не найден"}), 404
        return jsonify({
            "status": "success",
            "data": fields.public_view(row),
            "changed": changed,
            "course_options": list(fields.COURSE_OPTIONS),
        }), 200

    @my_data_route(['GET'])
    def my_data_get(user_id):
        return _view(user_id)

    @my_data_route(['POST'])
    def my_data_save(user_id):
        payload = request.get_json(silent=True)
        clean, errors, unknown = fields.validate(payload)
        if unknown:
            return jsonify({
                "error": "Эти данные нельзя изменить самостоятельно",
                "code": "FIELD_NOT_EDITABLE",
                "fields": unknown,
            }), 400
        if errors:
            return jsonify({
                "error": next(iter(errors.values())),
                "code": "INVALID_FIELDS",
                "errors": errors,
            }), 400
        return _view(user_id, for_save=clean)

    return bp
