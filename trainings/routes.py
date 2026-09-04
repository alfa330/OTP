# -*- coding: utf-8 -*-
"""HTTP-эндпоинты справочника корпоративных тем (Flask Blueprint).

Blueprint собирается фабрикой и получает зависимости аргументами, а не
импортирует bot_schedule2: тот сам подключает этот модуль, и обратный импорт
был бы циклом (как в wiki/routes.py и crm/routes.py).

Почему префикс /api/training_topics, а не /api/trainings. Ручки /api/trainings
уже существуют плоскими @app.route и вплетены в расчёт оплачиваемых часов,
квоту звонков и лист «Тренинги» в выгрузке; на них ссылаются 17 мест фронта,
планировщик смен и «Журнал оценок». Перенос их в Blueprint — отдельная работа
с зарплатным трактом, а не оформление раздела. Здесь только новая поверхность.

Соглашения те же, что у остальных роутов портала: методы всегда включают
OPTIONS и первым делом отдаётся preflight, авторизация — общий require_api_key,
ошибка — {"error": "..."} с осмысленным кодом.
"""

import logging
from functools import wraps

from flask import Blueprint, jsonify, request

from . import access, queries, schema


def build_trainings_blueprint(*, db, require_api_key, build_cors_preflight_response,
                              resolve_requester, normalize_role, headed_department_id):
    """Собирает Blueprint справочника тем.

    normalize_role и headed_department_id приходят аргументами, чтобы раздел
    считал роль и «главу отдела» ровно теми же правилами, что весь портал:
    своя копия нормализации рано или поздно разошлась бы с общей (роль
    'supervisor' против 'sv' — классика), и права разъехались бы молча.
    Аргументы обязательные, без значений по умолчанию: забытая зависимость
    должна уронить сборку блюпринта на старте, а не тихо открыть раздел всем.
    """
    bp = Blueprint('training_topics', __name__, url_prefix='/api/training_topics')

    def topics_route(rules, methods=('GET',), manage=False):
        """Общий каркас роута: preflight, авторизация, контекст, ошибки.

        rules — строка или несколько правил на один обработчик. Несколько нужны
        ровно для пары '' и '/': без второго правила запрос на /api/training_topics/
        отдал бы 404, а axios на фронте добавляет слэш не всегда. Регистрируются
        они ОДНИМ декоратором на одну view-функцию: два наложенных декоратора
        зарегистрировали бы один и тот же endpoint дважды и уронили сборку
        блюпринта на старте.

        manage=True — роут правки справочника: гейт стоит здесь, до
        обработчика, чтобы не повторять проверку в каждом.
        """
        all_methods = tuple(methods) + ('OPTIONS',)
        rule_list = (rules,) if isinstance(rules, str) else tuple(rules)

        def decorator(handler):
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status

                    role = normalize_role(requester[3])
                    headed_dept = headed_department_id(requester_id)
                    own_dept = db.get_user_department_id(requester_id)

                    if not access.can_read(role, headed_dept):
                        return jsonify({
                            "error": "Раздел «Тренинги» вам не открыт",
                            "code": "TRAININGS_SECTION_CLOSED",
                        }), 403

                    if manage and not access.can_manage_topics(role, headed_dept):
                        return jsonify({
                            "error": "Вести справочник тем вам не разрешено",
                            "code": "TRAININGS_FORBIDDEN",
                        }), 403

                    ctx = {
                        'user_id': requester_id,
                        'role': role,
                        'headed_department_id': headed_dept,
                        'own_department_id': own_dept,
                        'can_manage': access.can_manage_topics(role, headed_dept),
                        'unscoped': access.is_unscoped(role, headed_dept),
                        'can_subscribe_reports': access.can_subscribe_reports(role, headed_dept),
                    }
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('trainings: ошибка в %s', rule_list[0] or '/')
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Тренинги»",
                        "detail": str(exc)[:200],
                    }), 500

            for index, rule in enumerate(rule_list):
                bp.add_url_rule(
                    rule,
                    endpoint='%s_%d' % (handler.__name__, index),
                    view_func=wrapper,
                    methods=list(all_methods),
                )
            return wrapper

        return decorator

    # ── Чтение ───────────────────────────────────────────────────────────────

    @topics_route(('', '/'), methods=('GET',))
    def list_topics(ctx):
        """Справочник тем с охватом.

        Отдаём и справочник базовых тем: их список тоже должен быть один на
        сервер и клиент. До этой правки фронт держал свою копию из 9 значений,
        сервер разрешал 11 — и 243 записи «Тех. сбой»/«Мониторинг» при
        открытии на редактирование теряли причину.
        """
        include_archived = str(request.args.get('include_archived', '')).lower() in ('1', 'true', 'yes')
        department_ids = access.readable_department_ids(
            ctx['role'], ctx['headed_department_id'], ctx['own_department_id']
        )
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({
                    "status": "success",
                    "schema_ready": False,
                    "topics": [],
                    "default_reasons": [],
                    "audience_by_department": {},
                }), 200
            topics = queries.list_topics(
                cursor, department_ids=department_ids, include_archived=include_archived
            )
            audience = queries.department_audience_counts(cursor)

        if not ctx['can_manage']:
            # «О чём тема» написано для того, кто будет её проводить, — в форме
            # так и подписано. Рядовому сотруднику отдаём только название.
            topics = [dict(item, description=None) for item in topics]

        return jsonify({
            "status": "success",
            "schema_ready": True,
            "can_manage": ctx['can_manage'],
            "unscoped": ctx['unscoped'],
            # Показывать ли кнопку «Отчёты в Telegram». Флаг едет вместе со
            # справочником, а не отдельным запросом: раздел и без того делает
            # ровно два обращения, и третье ради одного булева было бы платой
            # ни за что. Сам роут подписки права проверяет заново — спрятанная
            # кнопка доступом не является.
            "can_subscribe_reports": ctx['can_subscribe_reports'],
            "scope_department_id": (
                ctx['headed_department_id']
                if ctx['headed_department_id'] is not None else ctx['own_department_id']
            ),
            "topics": topics,
            # Базовые темы: полный список и какие из них архивные.
            "default_reasons": list(schema.DEFAULT_REASONS),
            "archived_reasons": list(schema.ARCHIVED_REASONS),
            "topic_kinds": [
                {"value": value, "label": schema.TOPIC_KIND_LABELS.get(value, value)}
                for value in schema.TOPIC_KINDS
            ],
            "audience_by_department": {str(key): value for key, value in audience.items()},
        }), 200

    @topics_route('/<int:topic_id>/audience', methods=('GET',), manage=True)
    def topic_audience(ctx, topic_id):
        """Кому тему провели и кому осталось — список для набора пачки.

        manage=True не для симметрии: это поимённый список всего отдела с
        отметкой, кто тренинг прошёл, а кто нет. Рядовому сотруднику такой
        список не нужен и видеть его он не должен, хотя сам справочник тем ему
        открыт. Гейт стоит на сервере, а не в интерфейсе: спрятанная кнопка
        доступом не является.
        """
        with db._get_cursor() as cursor:
            topic = queries.get_topic(cursor, topic_id)
            if not topic:
                return jsonify({"error": "Тема не найдена"}), 404
            error = _read_scope_error(ctx, topic)
            if error:
                message, status = error
                return jsonify({"error": message}), status
            people = queries.topic_audience(cursor, topic_id, topic['department_id'])

        covered = [item for item in people if item['covered']]
        return jsonify({
            "status": "success",
            "topic": topic,
            "audience": people,
            "audience_count": len(people),
            "covered_count": len(covered),
            "remaining_count": len(people) - len(covered),
        }), 200

    # ── Запись ───────────────────────────────────────────────────────────────

    @topics_route(('', '/'), methods=('POST',), manage=True)
    def create_topic(ctx):
        data = request.get_json(silent=True) or {}

        title = str(data.get('title') or '').strip()
        if not title:
            return jsonify({"error": "Укажите название темы"}), 400
        if len(title) > 255:
            return jsonify({"error": "Название темы длиннее 255 символов"}), 400

        kind = str(data.get('kind') or 'info').strip().lower()
        if kind not in schema.TOPIC_KINDS:
            return jsonify({"error": "Неизвестный тип темы"}), 400

        department_id, scope_error = access.writable_department_id(
            ctx['role'], ctx['headed_department_id'], ctx['own_department_id'],
            _optional_int(data.get('department_id')),
        )
        if scope_error:
            message, status = scope_error
            return jsonify({"error": message}), status

        # Корпоративная тема не идёт в оплачиваемые часы: решение владельца —
        # это факт прохождения, а не оплачиваемая работа. Флаг оставлен в
        # схеме на случай будущего обучающего типа, но из запроса не берётся,
        # чтобы клиент не мог случайно завести оплату.
        count_in_hours = False

        with db._get_cursor() as cursor:
            if queries.topic_title_taken(cursor, title, department_id):
                return jsonify({
                    "error": "Тема с таким названием в этом отделе уже есть",
                    "code": "TOPIC_TITLE_TAKEN",
                }), 409
            topic_id = queries.create_topic(
                cursor, title, kind, department_id,
                str(data.get('description') or '').strip() or None,
                count_in_hours, ctx['user_id'],
            )
        logging.info("Тренинги: тема %s «%s» создана пользователем %s", topic_id, title, ctx['user_id'])
        return jsonify({"status": "success", "id": topic_id}), 201

    @topics_route('/<int:topic_id>', methods=('PUT', 'PATCH'), manage=True)
    def update_topic(ctx, topic_id):
        data = request.get_json(silent=True) or {}

        with db._get_cursor() as cursor:
            topic = queries.get_topic(cursor, topic_id)
            if not topic:
                return jsonify({"error": "Тема не найдена"}), 404
            error = _write_scope_error(ctx, topic)
            if error:
                message, status = error
                return jsonify({"error": message}), status

            fields = {}
            if 'title' in data:
                title = str(data.get('title') or '').strip()
                if not title:
                    return jsonify({"error": "Укажите название темы"}), 400
                if len(title) > 255:
                    return jsonify({"error": "Название темы длиннее 255 символов"}), 400
                if queries.topic_title_taken(cursor, title, topic['department_id'], exclude_id=topic_id):
                    return jsonify({
                        "error": "Тема с таким названием в этом отделе уже есть",
                        "code": "TOPIC_TITLE_TAKEN",
                    }), 409
                fields['title'] = title
            if 'kind' in data:
                kind = str(data.get('kind') or '').strip().lower()
                if kind not in schema.TOPIC_KINDS:
                    return jsonify({"error": "Неизвестный тип темы"}), 400
                fields['kind'] = kind
            if 'description' in data:
                fields['description'] = str(data.get('description') or '').strip() or None
            if 'is_archived' in data:
                fields['is_archived'] = bool(data.get('is_archived'))

            if not fields:
                return jsonify({"error": "Нечего менять"}), 400

            queries.update_topic(cursor, topic_id, fields)

        # Переименование темы не переписывает reason у проведённых записей
        # СОЗНАТЕЛЬНО: в «Моих часах» сотрудник видит то название, под которым
        # тренинг ему и провели. Переименование задним числом сделало бы
        # прошлые записи рассказом о том, чего не было.
        return jsonify({"status": "success", "id": topic_id}), 200

    @topics_route('/<int:topic_id>', methods=('DELETE',), manage=True)
    def delete_topic(ctx, topic_id):
        """Удаление — только для темы без истории. С историей — архивирование."""
        with db._get_cursor() as cursor:
            topic = queries.get_topic(cursor, topic_id)
            if not topic:
                # Идемпотентно: удалять уже удалённое — не ошибка.
                return jsonify({"status": "success", "already_absent": True}), 200
            error = _write_scope_error(ctx, topic)
            if error:
                message, status = error
                return jsonify({"error": message}), status
            if queries.topic_has_sessions(cursor, topic_id):
                return jsonify({
                    "error": "По теме уже проводили тренинги — её можно только "
                             "отправить в архив, иначе охват прошлых месяцев перестанет сходиться",
                    "code": "TOPIC_HAS_SESSIONS",
                }), 409
            queries.delete_topic(cursor, topic_id)
        return jsonify({"status": "success"}), 200

    # ── Границы отдела ───────────────────────────────────────────────────────

    def _read_scope_error(ctx, topic):
        if ctx['unscoped']:
            return None
        if topic['department_id'] is None:
            return None  # Общие темы видит каждый.
        scope_id = (
            ctx['headed_department_id']
            if ctx['headed_department_id'] is not None else ctx['own_department_id']
        )
        if scope_id is None or int(topic['department_id']) != int(scope_id):
            return ("Тема относится к другому отделу", 403)
        return None

    def _write_scope_error(ctx, topic):
        if ctx['unscoped']:
            return None
        if topic['department_id'] is None:
            # Общую тему правит только тот, кто работает без границы отдела:
            # иначе один СВ переименовал бы тему, раскатанную на весь портал.
            return ("Общую тему правит только администратор портала", 403)
        return _read_scope_error(ctx, topic)

    return bp


def _optional_int(value):
    if value in (None, '', 'null'):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
