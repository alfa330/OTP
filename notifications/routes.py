# -*- coding: utf-8 -*-
"""Эндпоинты центра уведомлений.

Второй Blueprint в проекте — после «Вики» и по той же причине: собран фабрикой
с внедрением зависимостей, потому что импортировать bot_schedule2 отсюда нельзя
(вышел бы цикл), а нужны и авторизация, и правила видимости разделов, которые
живут там.

Роутов всего два, и это принципиально: центр существует ради того, чтобы вход
в портал стоил ОДИН запрос вместо пяти, и второй счётчик рядом свёл бы смысл
на нет.
"""

import logging

from flask import Blueprint, jsonify, request

from . import sources as notif_sources


def build_notifications_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                  resolve_requester, viewer_context):
    """viewer_context(requester_id, requester) -> dict для sources.collect."""

    bp = Blueprint('notifications', __name__, url_prefix='/api/notifications')

    def _authenticated():
        """(viewer, ошибка). Ошибка — готовый кортеж (response, code)."""
        requester_id, requester, auth_error = resolve_requester()
        if auth_error:
            message, status_code = auth_error
            return None, (jsonify({"error": message}), status_code)
        return viewer_context(requester_id, requester), None

    @bp.route('', methods=('GET', 'OPTIONS'))
    @bp.route('/', methods=('GET', 'OPTIONS'))
    @require_api_key
    def notifications_list():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()

        try:
            # Внутри try намеренно. Определение периметра зрителя тоже ходит в
            # базу (отдел, доступ к «4 You»), и при исчерпанном пуле исключение
            # вылетало отсюда мимо обработчика ниже: глобального errorhandler в
            # проекте нет, и клиент получал HTML-страницу 500 вместо JSON. Один
            # и тот же сбой отвечал то так, то иначе.
            # Ошибка авторизации при этом возвращается как была — return внутри
            # try отрабатывает раньше except.
            viewer, error = _authenticated()
            if error:
                return error

            with db._get_cursor() as cursor:
                counts, items = notif_sources.collect(cursor, viewer)
        except Exception:
            # Сюда долетает НЕ падение отдельного раздела — оно уже изолировано
            # SAVEPOINT'ом внутри collect() и даёт по нему ноль, — а отказ
            # уровня соединения: исчерпанный пул (его делит с нами SSE аукциона
            # смен), обрыв сети, упавшая база.
            #
            # Отдавать на это 200 с пустой сводкой нельзя. Клиент записывает
            # counts прямо в бейджи сайдбара, и «нет соединения» превратилось бы
            # в «у вас ничего нет»: у человека погасли бы «Ивенты» и «4 You», а
            # просроченный документ под обязательное ознакомление исчез бы с
            # экрана. Отличить это от честного нуля нельзя ни ему, ни фронту.
            #
            # 503 фронт обрабатывает как сбой запроса: колокол оставляет прежние
            # числа и повторит позже — см. NotificationsBell.load.
            logging.exception('Центр уведомлений: не удалось собрать сводку')
            return jsonify({
                "status": "error",
                "error": "Не удалось получить уведомления",
                "code": "NOTIFICATIONS_UNAVAILABLE",
            }), 503

        response = jsonify({"status": "success", "counts": counts, "items": items})
        # Счётчик обязан быть свежим: закешированный ноль прячет документ,
        # который человек обязан прочитать к сроку.
        response.headers['Cache-Control'] = 'no-store'
        return response, 200

    @bp.route('/seen', methods=('POST', 'OPTIONS'))
    @require_api_key
    def notifications_seen():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()

        viewer, error = _authenticated()
        if error:
            return error

        body = request.get_json(silent=True) or {}
        requested = body.get('sources') or ([body['source']] if body.get('source') else [])
        # Дубликаты отсеиваем: повторное гашение того же источника — лишний
        # UPDATE и вводящий в заблуждение ответ ("marked": ["events","events"]).
        # Порядок сохраняем, он определяет порядок запросов.
        wanted, seen_names = [], set()
        for name in requested:
            if name in notif_sources.SOURCES and name not in seen_names:
                seen_names.add(name)
                wanted.append(name)
        if not wanted:
            return jsonify({"error": "Не указан источник"}), 400

        marked = []
        with db._get_cursor() as cursor:
            for name in wanted:
                if notif_sources.mark_seen(cursor, viewer['user_id'], name):
                    marked.append(name)
        return jsonify({"status": "success", "marked": marked}), 200

    return bp
