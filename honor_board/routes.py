# -*- coding: utf-8 -*-
"""HTTP «Доски почёта» (Flask Blueprint), задача #318.

    GET  /api/events/honor_board             опубликованные доски месяца (все сотрудники)
    GET  /api/events/honor_board/edit        черновик доски месяца и направления (ведущие)
    PUT  /api/events/honor_board/edit        {period, department, entries, revision} — сохранить черновик
    POST /api/events/honor_board/publish     {period, department, revision} — опубликовать черновик
    POST /api/events/honor_board/unpublish   {period, department} — снять с публикации
    POST /api/events/honor_board/revert      {period, department, revision} — черновик = опубликованное
    GET  /api/events/honor_board/candidates  ?department= — сотрудники направления с фото

КТО ВИДИТ — все сотрудники: «Раздел «Доска почёта» виден всем сотрудникам
компании без ограничений» (ТЗ, п. 1). Опубликованная доска одна для всех,
без деления по отделам, — в отличие от ленты «Ивентов».

КТО ВЕДЁТ — по направлениям (managed_departments):
  * роль «Тренер» — все три: «Права на ежемесячное добавление и редактирование
    победителей предоставляются роли «Тренер»» (ТЗ, п. 1, 4);
  * супер-админ (владельцы портала) — все три: без этого доску было бы некому
    поправить, если тренер ошибся;
  * глава отдела «Фронт-офис» — только Фронт-офис: «тренер или руководитель
    смогут указать 2–4 профильные номинации для Фронт-офиса» (ТЗ, п. 2).
Других руководителей ТЗ не называет — их здесь нет.

ЧЕРНОВИК И ПУБЛИКАЦИЯ — honor_board/schema.py. Окно тренера сохраняет черновик
само, по ходу ввода; сотрудники видят только опубликованное.

Фабрика получает зависимости аргументами и не импортирует bot_schedule2 —
тот сам подключает этот модуль (тот же приём, что у library и news).
"""

import logging
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from . import catalog, queries
from .schema import schema_is_ready

MANAGER_ROLES = frozenset({'trainer', 'super_admin'})
# Направление доски → код отдела, глава которого ведёт это направление сам.
HEAD_MANAGED_DEPARTMENTS = {'front_office': 'front_office'}

ALMATY = ZoneInfo('Asia/Almaty')


def managed_departments(role, headed_codes=()):
    """Направления, которые человек ведёт, в порядке доски (кортеж ключей)."""
    if role in MANAGER_ROLES:
        return catalog.DEPARTMENT_KEYS
    codes = {str(code or '').strip().lower() for code in headed_codes or ()}
    return tuple(key for key in catalog.DEPARTMENT_KEYS if HEAD_MANAGED_DEPARTMENTS.get(key) in codes)


def _iso(value):
    return value.isoformat(timespec='seconds') if value else None


def _revision(value):
    """revision из запроса -> int ≥ 0 или None (не прислали или не число)."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _user_ids(entries):
    return {entry['user_id'] for entry in entries if entry.get('user_id')}


def public_entries(stored):
    """Опубликованный снимок -> карточки доски. main — ключевая номинация."""
    result = []
    for raw in stored if isinstance(stored, list) else []:
        if not isinstance(raw, dict):
            continue
        nomination = str(raw.get('nomination') or '')
        try:
            user_id = int(raw.get('user_id')) if raw.get('user_id') else None
        except (TypeError, ValueError):
            user_id = None
        result.append({
            'nomination': nomination,
            'title': str(raw.get('title') or ''),
            'main': nomination == catalog.MAIN_NOMINATION,
            'user_id': user_id,
            'name': str(raw.get('name') or ''),
            'description': str(raw.get('description') or ''),
        })
    return result


def public_snapshot(stored):
    """Опубликованный снимок в виде published_snapshot — для сравнения с черновиком."""
    return [
        {key: entry[key] for key in ('nomination', 'title', 'user_id', 'name', 'description')}
        for entry in public_entries(stored)
    ]


def board_state(department_key, draft, published, published_at):
    """Отметка месяца в окне тренера: None (пусто), 'draft' (не опубликована),
    'published', 'changed' (опубликована, а в черновике правки)."""
    entries = catalog.restore_slots(department_key, draft)
    if published_at is None:
        return 'draft' if any(catalog.entry_has_content(entry) for entry in entries) else None
    if catalog.published_snapshot(entries) != public_snapshot(published):
        return 'changed'
    return 'published'


def build_honor_board_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                resolve_requester, normalize_role, avatar_url=None,
                                headed_department_codes=None, today=None):
    """avatar_url(bucket, blob) -> подписанная ссылка на фото профиля или None:
    без него доска рисует инициалы. headed_department_codes(user_id) -> коды
    отделов, которые человек возглавляет: без него направление по главе отдела
    не выдаётся. today() -> date по Алматы (подменяют тесты)."""

    bp = Blueprint('honor_board', __name__, url_prefix='/api/events/honor_board')
    schema_ready_once = []
    today_fn = today or (lambda: datetime.now(ALMATY).date())

    def _schema_ready(cursor):
        if schema_ready_once:
            return True
        if schema_is_ready(cursor):
            schema_ready_once.append(True)
            return True
        return False

    def _access(requester_id, requester):
        role = normalize_role(requester[3] if requester else None)
        if role in MANAGER_ROLES or not headed_department_codes:
            return managed_departments(role)
        # Главу отдела узнаём до того, как ручка возьмёт своё соединение:
        # помощник монолита ходит в базу сам, и два соединения на один запрос
        # из общего пула брать незачем.
        try:
            headed = headed_department_codes(int(requester_id)) or ()
        except Exception:  # noqa: BLE001 — без этого человек просто не ведёт доску
            logging.exception('honor_board: не удалось узнать отделы главы %s', requester_id)
            headed = ()
        return managed_departments(role, headed)

    def honor_route(rule, methods, *, manage=False):
        def decorator(handler):
            @bp.route(rule, methods=[*methods, 'OPTIONS'], endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(**kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    access = _access(requester_id, requester)
                    if manage and not access:
                        return _forbidden()
                    with db._get_cursor() as cursor:
                        if not _schema_ready(cursor):
                            if manage:
                                return jsonify({
                                    "error": "Доска почёта ещё разворачивается — попробуйте через минуту",
                                    "code": "HONOR_SCHEMA_NOT_READY",
                                }), 503
                            return jsonify({
                                "status": "success", "schema_ready": False, "periods": [],
                                "period": None, "boards": {}, "photos": {}, "can_manage": bool(access),
                                "manage_departments": list(access),
                            }), 200
                        return handler(cursor, int(requester_id), access, **kwargs)
                except catalog.EntriesError as exc:
                    return jsonify({"error": str(exc), "code": "HONOR_BAD_ENTRIES"}), 400
                except Exception:  # noqa: BLE001
                    logging.exception('honor_board: ошибка в %s %s', request.method, request.path)
                    return jsonify({"error": "Не удалось обработать запрос доски почёта"}), 500

            return wrapper

        return decorator

    def _forbidden():
        return jsonify({
            "error": "Итоги на доске почёта ведёт тренер",
            "code": "HONOR_MANAGE_FORBIDDEN",
        }), 403

    def _payload():
        """Тело запроса — объект JSON или None (тогда ответ 400)."""
        payload = request.get_json(silent=True)
        if payload is None:
            return {}
        return payload if isinstance(payload, dict) else None

    def _bad_payload():
        return jsonify({"error": "Запрос передан неверно — обновите окно", "code": "HONOR_BAD_REQUEST"}), 400

    def _sign(bucket, blob):
        if not avatar_url or not blob:
            return None
        try:
            return avatar_url(bucket, blob)
        except Exception:  # noqa: BLE001 — без фото карточка рисует инициалы
            return None

    def _photos(cursor, user_ids):
        """{id: ссылка} одним списком на ответ — тот же приём, что у карточек
        «Оплаты счетов»: человек может стоять и в двух номинациях."""
        if not avatar_url or not user_ids:
            return {}
        result = {}
        for user_id, bucket, blob in queries.user_avatars(cursor, user_ids):
            url = _sign(bucket, blob)
            if url:
                result[str(user_id)] = url
        return result

    def _person(row):
        return {
            'id': row['id'], 'name': row['name'] or '', 'role': row['role'] or '',
            'direction': row['direction_name'] or '',
            'photo_url': _sign(row['avatar_bucket'], row['avatar_blob_path']),
        }

    def _people(cursor, user_ids):
        """Карточки людей черновика для окна тренера: {id: {name, role, direction, photo_url}}."""
        return {str(row['id']): _person(row) for row in queries.people_cards(cursor, user_ids)}

    def _target(source, today, access):
        """(месяц, ключ направления, None) или (None, None, ответ 400/403)."""
        period = catalog.parse_period(source.get('period'), today)
        if period is None:
            return None, None, (jsonify({"error": "Выберите месяц", "code": "HONOR_BAD_REQUEST"}), 400)
        item = catalog.department(source.get('department'))
        if item is None:
            return None, None, (jsonify({"error": "Выберите направление", "code": "HONOR_BAD_REQUEST"}), 400)
        if item['key'] not in access:
            return None, None, _forbidden()
        return period, item['key'], None

    def _board_view(cursor, period, department_key, row):
        """Доска для окна тренера: черновик, состояние публикации и кто правил."""
        if row is None:
            item = catalog.department(department_key)
            titles = queries.previous_custom_titles(cursor, period, department_key) if item['custom'] else ()
            return {
                'period': catalog.period_key(period), 'department': department_key,
                'entries': catalog.blank_slots(department_key, titles),
                'revision': 0, 'saved': False, 'state': None,
                'published_at': None, 'published_by_name': None,
                'updated_at': None, 'updated_by_name': None,
            }
        return {
            'period': catalog.period_key(period), 'department': department_key,
            'entries': catalog.restore_slots(department_key, row['draft_entries']),
            'revision': int(row['revision']), 'saved': True,
            'state': board_state(department_key, row['draft_entries'],
                                 row['published_entries'], row['published_at']),
            'published_at': _iso(row['published_at']),
            'published_by_name': row['published_by_name'],
            'updated_at': _iso(row['updated_at']),
            'updated_by_name': row['updated_by_name'],
        }

    def _fresh(cursor, period, department_key):
        view = _board_view(cursor, period, department_key, queries.board(cursor, period, department_key))
        return view, _people(cursor, _user_ids(view['entries']))

    def _conflict(cursor, period, department_key):
        view, people = _fresh(cursor, period, department_key)
        return jsonify({
            "error": "Итоги только что изменили в другом окне — показываю свежую версию",
            "code": "HONOR_CONFLICT", "board": view, "people": people,
        }), 409

    def _months(cursor, today, access):
        """Месяцы окна с отметками по направлениям, которые человек ведёт."""
        periods = catalog.selectable_periods(today)
        states = {}
        for row in queries.boards_between(cursor, periods[-1], periods[0]) if periods else []:
            if row['department_key'] in access:
                states.setdefault(row['period_month'], {})[row['department_key']] = board_state(
                    row['department_key'], row['draft_entries'], row['published_entries'], row['published_at'])
        return [
            {
                'period': catalog.period_key(period),
                'states': {key: states.get(period, {}).get(key) for key in access},
            }
            for period in periods
        ]

    @honor_route('', ['GET'])
    def honor_board_view(cursor, requester_id, access):
        today = today_fn()
        periods = queries.published_periods(cursor)
        requested = catalog.parse_period(request.args.get('period'), today) if request.args.get('period') else None
        # Месяц, которого среди опубликованных нет (его только что сняли или
        # адрес устарел), — последний опубликованный: список месяцев на доске
        # иначе показал бы пустую заглушку вместо названия.
        period = requested if requested in periods else (periods[0] if periods else None)
        boards = {}
        if period is not None:
            for row in queries.published_boards(cursor, period):
                if catalog.department(row['department_key']):
                    boards[row['department_key']] = {
                        'entries': public_entries(row['published_entries']),
                        'published_at': _iso(row['published_at']),
                    }
        user_ids = {entry['user_id'] for board in boards.values() for entry in board['entries'] if entry['user_id']}
        response = jsonify({
            'status': 'success', 'schema_ready': True,
            'periods': [catalog.period_key(value) for value in periods],
            'period': catalog.period_key(period) if period else None,
            'boards': boards,
            'photos': _photos(cursor, user_ids),
            'can_manage': bool(access),
            'manage_departments': list(access),
            # Какой месяц открыть по «Заполнить итоги»: день по Алматы знает
            # сервер, а не часы в браузере.
            'default_period': catalog.period_key(catalog.default_period(today)) if access else None,
        })
        # Опубликовали — и сразу видно всем: кэшировать нечего.
        response.headers['Cache-Control'] = 'no-store'
        return response, 200

    @honor_route('/edit', ['GET', 'PUT'], manage=True)
    def honor_board_edit(cursor, requester_id, access):
        today = today_fn()
        if request.method == 'GET':
            period, department_key, error = _target(request.args, today, access)
            if error:
                return error
            view, people = _fresh(cursor, period, department_key)
            return jsonify({
                'status': 'success', 'board': view, 'people': people,
                'months': _months(cursor, today, access),
            }), 200

        payload = _payload()
        if payload is None:
            return _bad_payload()
        period, department_key, error = _target(payload, today, access)
        if error:
            return error
        base = _revision(payload.get('revision'))
        if base is None:
            return jsonify({"error": "Обновите окно и повторите", "code": "HONOR_BAD_REQUEST"}), 400
        entries = catalog.normalize_entries(department_key, payload.get('entries'))
        wanted = _user_ids(entries)
        if wanted - queries.existing_user_ids(cursor, wanted):
            return jsonify({"error": "Сотрудник не найден — выберите его заново",
                            "code": "HONOR_UNKNOWN_USER"}), 400

        row = queries.board(cursor, period, department_key, lock=True)
        if row is None:
            if base != 0 or queries.insert_board(cursor, period, department_key, entries, requester_id) is None:
                return _conflict(cursor, period, department_key)
        elif int(row['revision']) != base:
            return _conflict(cursor, period, department_key)
        elif entries != catalog.restore_slots(department_key, row['draft_entries']):
            queries.update_draft(cursor, row['id'], entries, requester_id)
        view, people = _fresh(cursor, period, department_key)
        return jsonify({'status': 'success', 'board': view, 'people': people}), 200

    @honor_route('/publish', ['POST'], manage=True)
    def honor_board_publish(cursor, requester_id, access):
        payload = _payload()
        if payload is None:
            return _bad_payload()
        period, department_key, error = _target(payload, today_fn(), access)
        if error:
            return error
        base = _revision(payload.get('revision'))
        row = queries.board(cursor, period, department_key, lock=True)
        if row is None:
            if base == 0:
                return jsonify({"error": "Выберите хотя бы одного победителя", "code": "HONOR_INCOMPLETE",
                                "issues": catalog.publish_issues([])}), 400
            return _conflict(cursor, period, department_key)
        if base is None or int(row['revision']) != base:
            return _conflict(cursor, period, department_key)
        entries = catalog.restore_slots(department_key, row['draft_entries'])
        issues = catalog.publish_issues(entries)
        known = queries.existing_user_ids(cursor, _user_ids(entries))
        issues.extend(
            {'index': index, 'field': 'user', 'message': 'Сотрудника больше нет в списке — выберите заново'}
            for index, entry in enumerate(entries)
            if entry['user_id'] and entry['user_id'] not in known
        )
        if issues:
            return jsonify({"error": "Не всё заполнено", "code": "HONOR_INCOMPLETE", "issues": issues}), 400
        queries.publish(cursor, row['id'], catalog.published_snapshot(entries), requester_id)
        view, people = _fresh(cursor, period, department_key)
        return jsonify({'status': 'success', 'board': view, 'people': people}), 200

    @honor_route('/unpublish', ['POST'], manage=True)
    def honor_board_unpublish(cursor, requester_id, access):
        payload = _payload()
        if payload is None:
            return _bad_payload()
        period, department_key, error = _target(payload, today_fn(), access)
        if error:
            return error
        row = queries.board(cursor, period, department_key, lock=True)
        if row is None or row['published_at'] is None:
            return _conflict(cursor, period, department_key)
        queries.unpublish(cursor, row['id'])
        view, people = _fresh(cursor, period, department_key)
        return jsonify({'status': 'success', 'board': view, 'people': people}), 200

    @honor_route('/revert', ['POST'], manage=True)
    def honor_board_revert(cursor, requester_id, access):
        """Отменить неопубликованные правки: черновик снова равен тому, что видят сотрудники."""
        payload = _payload()
        if payload is None:
            return _bad_payload()
        period, department_key, error = _target(payload, today_fn(), access)
        if error:
            return error
        base = _revision(payload.get('revision'))
        row = queries.board(cursor, period, department_key, lock=True)
        if row is None or row['published_at'] is None or base is None or int(row['revision']) != base:
            return _conflict(cursor, period, department_key)
        entries = catalog.revert_slots(department_key, row['published_entries'], row['draft_entries'])
        if entries != catalog.restore_slots(department_key, row['draft_entries']):
            queries.update_draft(cursor, row['id'], entries, requester_id)
        view, people = _fresh(cursor, period, department_key)
        return jsonify({'status': 'success', 'board': view, 'people': people}), 200

    @honor_route('/candidates', ['GET'], manage=True)
    def honor_board_candidates(cursor, requester_id, access):
        item = catalog.department(request.args.get('department'))
        if item is None:
            return jsonify({"error": "Выберите направление", "code": "HONOR_BAD_REQUEST"}), 400
        if item['key'] not in access:
            return _forbidden()
        department_row = queries.department_by_code(cursor, item['code'])
        people = queries.candidates(cursor, department_row['id']) if department_row else []
        return jsonify({
            'status': 'success', 'department': item['key'],
            'department_name': department_row['name'] if department_row else None,
            'people': [_person(row) for row in people],
        }), 200

    return bp
