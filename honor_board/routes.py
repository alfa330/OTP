# -*- coding: utf-8 -*-
"""HTTP «Доски почёта» (Flask Blueprint), задача #318.

    GET  /api/events/honor_board             опубликованные доски месяца (все сотрудники)
    POST /api/events/honor_board/seen        вышедшие доски просмотрены — гасит колокол
    GET  /api/events/honor_board/edit        черновик доски месяца и направления (ведущие)
    PUT  /api/events/honor_board/edit        {period, department, entries, revision} — сохранить черновик
    POST /api/events/honor_board/publish     {period, department, revision} — опубликовать черновик
    POST /api/events/honor_board/unpublish   {period, department} — снять с публикации
    POST /api/events/honor_board/revert      {period, department, revision} — черновик = опубликованное
    GET  /api/events/honor_board/candidates  ?department= — сотрудники направления с фото
    POST /api/events/honor_board/photos      multipart {user_id, cutout, photo} — фото для доски

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

ФОТО НА КАРТОЧКЕ — то, что загрузил тренер (honor_board_photos, обычно с
вырезанным фоном), иначе фото из профиля, иначе инициалы.

КОЛОКОЛ — первая публикация доски будит всех (notifications/sources.py:
honor_board); заход на доску гасит уведомление водяным знаком.

Фабрика получает зависимости аргументами и не импортирует bot_schedule2 —
тот сам подключает этот модуль (тот же приём, что у library и news).
"""

import logging
import uuid
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

from . import catalog, queries
from .photos import CONTENT_TYPE as PHOTO_CONTENT_TYPE, MAX_UPLOAD_BYTES, PhotoError, prepare_photo
from .schema import schema_is_ready

MANAGER_ROLES = frozenset({'trainer', 'super_admin'})
# Направление доски → код отдела, глава которого ведёт это направление сам.
HEAD_MANAGED_DEPARTMENTS = {'front_office': 'front_office'}

ALMATY = ZoneInfo('Asia/Almaty')
PHOTO_FOLDER = 'HonorBoard'
# Тело загрузки — multipart: файл плюс пара полей, поэтому потолок запроса чуть
# выше потолка самого файла (honor_board/photos.py: MAX_UPLOAD_BYTES).
PHOTO_REQUEST_MAX = MAX_UPLOAD_BYTES + 512 * 1024


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


def _int_or_none(value):
    if isinstance(value, bool) or value in (None, ''):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _user_ids(entries):
    return {entry['user_id'] for entry in entries if entry.get('user_id')}


def _photo_ids(entries):
    return {entry['photo_id'] for entry in entries if entry.get('photo_id')}


def public_entries(stored):
    """Опубликованный снимок -> карточки доски. main — ключевая номинация.

    Доски до 06.11.2026 хранят номинации ОП и СЗоВ кодами (guru_sales, …) и
    без значков — они читаются своими номинациями с названием и значком из ТЗ,
    так же, как их черновик читает catalog.restore_slots."""
    result = []
    for raw in stored if isinstance(stored, list) else []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get('nomination') or '')
        main = code == catalog.MAIN_NOMINATION
        if main:
            title, icon = str(raw.get('title') or catalog.MAIN_TITLE), catalog.MAIN_ICON
        else:
            legacy_title, legacy_icon = catalog.LEGACY_NOMINATIONS.get(code, ('', catalog.DEFAULT_ICON))
            title = str(raw.get('title') or legacy_title)
            icon = catalog.clean_icon(raw.get('icon'), legacy_icon)
        user_id = _int_or_none(raw.get('user_id'))
        result.append({
            'nomination': catalog.MAIN_NOMINATION if main else catalog.CUSTOM_NOMINATION,
            'title': title,
            'icon': icon,
            'main': main,
            'user_id': user_id,
            'name': str(raw.get('name') or ''),
            'description': str(raw.get('description') or ''),
            'photo_id': _int_or_none(raw.get('photo_id')) if user_id else None,
        })
    return result


def public_snapshot(stored):
    """Опубликованный снимок в виде published_snapshot — для сравнения с черновиком."""
    return [{key: entry[key] for key in catalog.SNAPSHOT_FIELDS} for entry in public_entries(stored)]


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
                                headed_department_codes=None, photo_storage=None, today=None):
    """avatar_url(bucket, blob) -> подписанная ссылка на фото или None: без
    него доска рисует инициалы. headed_department_codes(user_id) -> коды
    отделов, которые человек возглавляет: без него направление по главе отдела
    не выдаётся. photo_storage — {'bucket_name': () -> str, 'client': () ->
    клиент GCS}: без него фото для доски не загружаются (503). today() -> date
    по Алматы (подменяют тесты)."""

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

    def _not_ready(manage, access):
        if manage:
            return jsonify({
                "error": "Доска почёта ещё разворачивается — попробуйте через минуту",
                "code": "HONOR_SCHEMA_NOT_READY",
            }), 503
        return jsonify({
            "status": "success", "schema_ready": False, "periods": [],
            "period": None, "boards": {}, "photos": {}, "honor_photos": {}, "unseen": False,
            "can_manage": bool(access), "manage_departments": list(access),
        }), 200

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
                            return _not_ready(manage, access)
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

    def _avatars(cursor, user_ids):
        """{id: ссылка на фото профиля} одним списком на ответ — тот же приём,
        что у карточек «Оплаты счетов»: человек может стоять и в двух номинациях."""
        if not avatar_url or not user_ids:
            return {}
        result = {}
        for user_id, bucket, blob in queries.user_avatars(cursor, user_ids):
            url = _sign(bucket, blob)
            if url:
                result[str(user_id)] = url
        return result

    def _photo_card(row):
        return {
            'id': row['id'], 'user_id': row['user_id'], 'url': _sign(row['bucket'], row['blob_path']),
            'cutout': bool(row['cutout']), 'width': row['width'], 'height': row['height'],
        }

    def _honor_photos(cursor, photo_ids):
        """{id фото: карточка} — фото для доски, на которые ссылаются записи."""
        return {str(photo_id): _photo_card(row) for photo_id, row in queries.photos_by_ids(cursor, photo_ids).items()}

    def _person(row, latest=None):
        return {
            'id': row['id'], 'name': row['name'] or '', 'role': row['role'] or '',
            'direction': row['direction_name'] or '',
            'photo_url': _sign(row['avatar_bucket'], row['avatar_blob_path']),
            # Последнее фото для доски: окно подставляет его, когда человека
            # выбирают победителем, — второй раз то же фото не грузят.
            'honor_photo': _photo_card(latest) if latest else None,
        }

    def _people(cursor, user_ids):
        """Карточки людей черновика для окна тренера: {id: {name, role, direction, photo_url, honor_photo}}."""
        rows = queries.people_cards(cursor, user_ids)
        latest = queries.latest_photos(cursor, [row['id'] for row in rows]) if rows else {}
        return {str(row['id']): _person(row, latest.get(row['id'])) for row in rows}

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
            previous = queries.previous_custom_nominations(cursor, period, department_key)
            return {
                'period': catalog.period_key(period), 'department': department_key,
                'entries': catalog.blank_slots(department_key, previous),
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
        """{'board', 'people', 'photos'} — то, что окно тренера получает целиком."""
        view = _board_view(cursor, period, department_key, queries.board(cursor, period, department_key))
        return {
            'board': view,
            'people': _people(cursor, _user_ids(view['entries'])),
            'photos': _honor_photos(cursor, _photo_ids(view['entries'])),
        }

    def _conflict(cursor, period, department_key):
        return jsonify({
            "error": "Итоги только что изменили в другом окне — показываю свежую версию",
            "code": "HONOR_CONFLICT", **_fresh(cursor, period, department_key),
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

    def _photo_issue(cursor, entries):
        """Фото записи — снимок того же человека, что выбран в номинации. Иначе
        (фото удалили, сменили человека в другой вкладке) — ответ 400."""
        photos = queries.photos_by_ids(cursor, _photo_ids(entries))
        for entry in entries:
            photo = photos.get(entry['photo_id']) if entry.get('photo_id') else None
            if entry.get('photo_id') and (photo is None or photo['user_id'] != entry['user_id']):
                return jsonify({"error": "Фото для доски не найдено — загрузите его заново",
                                "code": "HONOR_BAD_PHOTO"}), 400
        return None

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
        entries = [entry for board in boards.values() for entry in board['entries']]
        response = jsonify({
            'status': 'success', 'schema_ready': True,
            'periods': [catalog.period_key(value) for value in periods],
            'period': catalog.period_key(period) if period else None,
            'boards': boards,
            'photos': _avatars(cursor, _user_ids(entries)),
            'honor_photos': _honor_photos(cursor, _photo_ids(entries)),
            # Есть вышедшие доски, которых человек ещё не видел: вкладка гасит
            # колокол (POST /seen), только когда есть что гасить.
            'unseen': queries.has_unseen(cursor, requester_id),
            'can_manage': bool(access),
            'manage_departments': list(access),
            # Какой месяц открыть по «Заполнить итоги»: день по Алматы знает
            # сервер, а не часы в браузере.
            'default_period': catalog.period_key(catalog.default_period(today)) if access else None,
        })
        # Опубликовали — и сразу видно всем: кэшировать нечего.
        response.headers['Cache-Control'] = 'no-store'
        return response, 200

    @honor_route('/seen', ['POST'])
    def honor_board_seen(cursor, requester_id, access):
        queries.mark_seen(cursor, requester_id)
        return jsonify({'status': 'success'}), 200

    @honor_route('/edit', ['GET', 'PUT'], manage=True)
    def honor_board_edit(cursor, requester_id, access):
        today = today_fn()
        if request.method == 'GET':
            period, department_key, error = _target(request.args, today, access)
            if error:
                return error
            return jsonify({
                'status': 'success', **_fresh(cursor, period, department_key),
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
        photo_error = _photo_issue(cursor, entries)
        if photo_error:
            return photo_error

        row = queries.board(cursor, period, department_key, lock=True)
        if row is None:
            if base != 0 or queries.insert_board(cursor, period, department_key, entries, requester_id) is None:
                return _conflict(cursor, period, department_key)
        elif int(row['revision']) != base:
            return _conflict(cursor, period, department_key)
        elif entries != catalog.restore_slots(department_key, row['draft_entries']):
            queries.update_draft(cursor, row['id'], entries, requester_id)
        return jsonify({'status': 'success', **_fresh(cursor, period, department_key)}), 200

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
        # Себе о своей же публикации колокол не звонит.
        queries.mark_seen(cursor, requester_id)
        return jsonify({'status': 'success', **_fresh(cursor, period, department_key)}), 200

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
        return jsonify({'status': 'success', **_fresh(cursor, period, department_key)}), 200

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
        return jsonify({'status': 'success', **_fresh(cursor, period, department_key)}), 200

    @honor_route('/candidates', ['GET'], manage=True)
    def honor_board_candidates(cursor, requester_id, access):
        item = catalog.department(request.args.get('department'))
        if item is None:
            return jsonify({"error": "Выберите направление", "code": "HONOR_BAD_REQUEST"}), 400
        if item['key'] not in access:
            return _forbidden()
        department_row = queries.department_by_code(cursor, item['code'])
        people = queries.candidates(cursor, department_row['id']) if department_row else []
        latest = queries.latest_photos(cursor, [row['id'] for row in people]) if people else {}
        return jsonify({
            'status': 'success', 'department': item['key'],
            'department_name': department_row['name'] if department_row else None,
            'people': [_person(row, latest.get(row['id'])) for row in people],
        }), 200

    # ── Загрузка фото для доски ─────────────────────────────────────────
    # Своя ручка, а не honor_route: тот держит соединение с базой весь ответ,
    # а здесь между двумя короткими походами в базу — пересжатие снимка и
    # загрузка в хранилище, секунды. Соединения пула на это не тратим.

    def _upload_photo_bytes(user_id, data):
        bucket_name = str((photo_storage or {}).get('bucket_name', lambda: '')() or '').strip()
        if not bucket_name:
            raise RuntimeError('honor_board: бакет для фото не настроен')
        now = datetime.now(ALMATY)
        blob_path = f'{PHOTO_FOLDER}/{int(user_id)}/{now:%Y/%m}/{uuid.uuid4().hex}.webp'
        blob = photo_storage['client']().bucket(bucket_name).blob(blob_path)
        # Путь уникален и снимок под ним не меняется — кэшировать можно навсегда.
        blob.cache_control = 'public, max-age=31536000, immutable'
        blob.upload_from_string(data, content_type=PHOTO_CONTENT_TYPE)
        return bucket_name, blob_path

    def _delete_blob(bucket_name, blob_path):
        try:
            photo_storage['client']().bucket(bucket_name).blob(blob_path).delete()
        except Exception:  # noqa: BLE001 — сирота в бакете безвредна
            logging.warning('honor_board: не удалось убрать неиспользованное фото %s', blob_path)

    @bp.route('/photos', methods=['POST', 'OPTIONS'], endpoint='honor_board_photo_upload')
    @require_api_key
    def honor_board_photo_upload():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        try:
            requester_id, requester, error = resolve_requester()
            if error:
                message, status = error
                return jsonify({"error": message}), status
            access = _access(requester_id, requester)
            if not access:
                return _forbidden()
            if not photo_storage:
                return jsonify({"error": "Загрузка фото пока недоступна", "code": "HONOR_PHOTO_UNAVAILABLE"}), 503
            if (request.content_length or 0) > PHOTO_REQUEST_MAX:
                return jsonify({"error": "Фото слишком большое — выберите поменьше",
                                "code": "HONOR_BAD_PHOTO"}), 413
            user_id = _int_or_none(request.form.get('user_id'))
            upload = request.files.get('photo')
            if not user_id or upload is None:
                return jsonify({"error": "Выберите сотрудника и фото", "code": "HONOR_BAD_REQUEST"}), 400
            cutout = str(request.form.get('cutout') or '').strip().lower() in ('1', 'true', 'yes')

            # 1. Чьё фото и ведёт ли загружающий его направление — до того, как
            #    что-то ляжет в хранилище. Направление — отдел человека сейчас или
            #    доска, где он уже стоит победителем (с тех пор мог перейти в
            #    другой отдел или уволиться, а доску месяца правят и после).
            with db._get_cursor() as cursor:
                if not _schema_ready(cursor):
                    return _not_ready(True, access)
                if not queries.existing_user_ids(cursor, [user_id]):
                    return jsonify({"error": "Сотрудник не найден — выберите его заново",
                                    "code": "HONOR_UNKNOWN_USER"}), 400
                code = queries.user_department_code(cursor, user_id)
                department_key = next((item['key'] for item in catalog.DEPARTMENTS if item['code'] == code), None)
                allowed = department_key in access or queries.user_on_boards(cursor, user_id, access)
            if not allowed:
                return jsonify({"error": "Фото для доски загружают сотрудникам направлений, которые вы ведёте",
                                "code": "HONOR_MANAGE_FORBIDDEN"}), 403

            # 2. Пересжатие и хранилище — без соединения с базой.
            try:
                # Читаем на байт больше потолка: файл длиннее prepare_photo отклонит.
                data, width, height, cutout = prepare_photo(upload.read(MAX_UPLOAD_BYTES + 1), cutout=cutout)
            except PhotoError as exc:
                return jsonify({"error": str(exc), "code": "HONOR_BAD_PHOTO"}), 400
            bucket_name, blob_path = _upload_photo_bytes(user_id, data)

            # 3. Строка фото — короткой записью.
            try:
                with db._get_cursor() as cursor:
                    photo_id = queries.insert_photo(
                        cursor, user_id=user_id, bucket=bucket_name, blob_path=blob_path,
                        content_type=PHOTO_CONTENT_TYPE, width=width, height=height, file_size=len(data),
                        cutout=cutout, actor_id=int(requester_id))
            except Exception:
                _delete_blob(bucket_name, blob_path)
                raise
            return jsonify({'status': 'success', 'photo': {
                'id': photo_id, 'user_id': user_id, 'url': _sign(bucket_name, blob_path),
                'cutout': cutout, 'width': width, 'height': height,
            }}), 200
        except Exception:  # noqa: BLE001
            logging.exception('honor_board: не удалось загрузить фото для доски')
            return jsonify({"error": "Не удалось загрузить фото — попробуйте ещё раз"}), 500

    return bp

