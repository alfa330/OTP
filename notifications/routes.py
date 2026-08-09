# -*- coding: utf-8 -*-
"""Эндпоинты центра уведомлений.

Второй Blueprint в проекте — после «Вики» и по той же причине: собран фабрикой
с внедрением зависимостей, потому что импортировать bot_schedule2 отсюда нельзя
(вышел бы цикл), а нужны и авторизация, и правила видимости разделов, которые
живут там.

Роутов три: сводка, гашение и SSE-канал тычков (/stream). Принцип «вход в
портал стоит ОДИН запрос» цел: /stream не считает ничего сам — он лишь будит
клиента, и тот перечитывает ту же единственную сводку.
"""

import logging
import time

from flask import Blueprint, Response, jsonify, request

from . import realtime
from . import sources as notif_sources


def build_notifications_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                  resolve_requester, viewer_context,
                                  listen_connect=None, stream_limit=50):
    """viewer_context(requester_id, requester) -> dict для sources.collect.

    listen_connect() -> новое psycopg2-соединение для LISTEN (своё, вне пула).
    Без него /stream отвечает 503 и колокол живёт на обновлении по фокусу —
    так же блюпринт ведёт себя в юнит-тестах, где базы нет.
    """

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

            # Размер порции. Клиент увеличивает его, докручивая список до низа:
            # счётчики считают всё, а элементов до сих пор отдавалось пять на
            # источник, и шестая задача была недостижима. Значение вне диапазона
            # не ошибка, а повод взять ближайшее допустимое — сводка важнее
            # придирок к параметру.
            try:
                limit = int(request.args.get('limit') or notif_sources.ITEMS_PER_SOURCE)
            except (TypeError, ValueError):
                limit = notif_sources.ITEMS_PER_SOURCE

            with db._get_cursor() as cursor:
                counts, items, meta = notif_sources.collect(cursor, viewer, limit=limit)
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

        # next_change_in заменяет собой фоновую сверку: клиент спит ровно
        # столько секунд, сколько названо, вместо того чтобы опрашивать сервер
        # по кругу. Интервал, а не момент — чтобы не зависеть от часового пояса
        # и часов на машине пользователя.
        response = jsonify({"status": "success", "counts": counts, "items": items,
                            "has_more": meta['has_more'],
                            "next_change_in": meta['next_change_in']})
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

    @bp.route('/stream', methods=('GET', 'OPTIONS'))
    @require_api_key
    def notifications_stream():
        """SSE-канал «у тебя что-то изменилось» — см. notifications/realtime.py.

        Каждый поток занимает нить waitress на всё время соединения, поэтому
        мест ровно stream_limit: сверх лимита — 503, клиент молча остаётся на
        обновлении по фокусу и попробует позже. Периметр зрителя здесь не
        нужен: тычок не несёт данных, а перечитка сводки фильтруется сервером.
        """
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        if listen_connect is None:
            return jsonify({"error": "Реалтайм-канал не подключён"}), 503

        requester_id, requester, auth_error = resolve_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code
        user_id = int(requester_id)

        realtime.ensure_listener(listen_connect)
        if not realtime.try_acquire_stream_slot(stream_limit):
            response = jsonify({"status": "busy"})
            response.headers['Retry-After'] = '300'
            return response, 503

        def generate():
            cursor_seq = realtime.current_seq()
            yield ": connected %d\n\n" % int(time.time())
            while True:
                # Строго событийно: reload уходит только когда на сервере
                # ДЕЙСТВИТЕЛЬНО что-то изменилось. Периодической сверки здесь
                # нет намеренно — это был фоновый опрос, ради отсутствия
                # которого весь механизм триггеров и делался. Изменения от хода
                # часов (окно теста, дедлайн) клиент ждёт по next_change_at,
                # который приезжает вместе со сводкой.
                poked, cursor_seq = realtime.wait_for_tick(
                    cursor_seq, user_id, realtime.HEARTBEAT_SECONDS)
                if poked:
                    # Содержимого нет намеренно: клиент перечитает сводку сам.
                    yield "event: reload\ndata: {}\n\n"
                else:
                    # Не опрос, а 20 байт комментария: без них прокси и браузер
                    # считают молчащее соединение мёртвым.
                    yield ": heartbeat %d\n\n" % int(time.time())

        response = Response(generate(), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Accel-Buffering'] = 'no'
        # Слот возвращается, когда WSGI-сервер закрывает ответ, — это надёжнее
        # finally внутри генератора: у ни разу не итерированного генератора
        # finally не выполнится, и слот утёк бы.
        response.call_on_close(realtime.release_stream_slot)
        return response

    return bp
