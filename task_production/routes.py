# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Продакшн» (Flask Blueprint, /api/production).

Раздел — рабочее место маркетинга поверх задач типа «Продакшн» (#354, #316):
доска этих задач, материалы по версиям и «Готовое». Это та же задача, а не
копия: статусы двигает ОБЫЧНЫЙ маршрут /api/tasks/<id>/status (взять в работу,
сдать, принять, вернуть) — с его правами и Telegram-уведомлениями. Здесь —
только чтение задач в охвате раздела и материалы.

ПОРЯДОК В РУЧКАХ С ХРАНИЛИЩЕМ. Курсор базы не держим, пока ждём облако: пул
соединений общий на весь портал, а открытие сессии или сверка объекта — это
сетевой запрос в GCS. Поэтому: прочитать и проверить → закрыть курсор →
сходить в хранилище → открыть курсор и записать, перепроверив под блокировкой
то, что могло измениться за это время.

Соглашения те же, что у остальных разделов: методы всегда включают OPTIONS и
первым делом отдаётся preflight, ошибка — {"error": "...", "code": "..."}.
"""

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from functools import wraps

from flask import Blueprint, jsonify as flask_jsonify, request

import task_production

from . import materials, queries, storage

_POSTER_TYPES = {'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/png': 'png'}


class ApiError(Exception):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _json(body, status=200):
    return flask_jsonify(_plain(body)), status


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def build_production_blueprint(*, db, require_api_key, build_cors_preflight_response, task_guard,
                               headed_department_codes, is_super_admin,
                               allowed_origin, gcs=None, avatar_url=None, blob_prefix=None):
    """Собирает Blueprint раздела.

    task_guard — гард раздела «Задачи» из монолита: (id, запись, ответ, код) и
    действующая роль в g.effective_task_role — ровно те права, с которыми этот
    человек двигает статусы задачи, поэтому кнопки здесь совпадут с тем, что
    сервер позволит.
    headed_department_codes(id) — коды отделов, которые человек возглавляет.
    allowed_origin(origin) — пускает ли портал этот сайт (тот же список, что у CORS).
    gcs — {'bucket_name': () -> str, 'client': () -> Client}.
    avatar_url — (bucket, blob_path) -> подписанная ссылка на фото сотрудника.
    blob_prefix — папка материалов в бакете (по умолчанию Production/); стенд
    кладёт свои файлы в отдельную, чтобы не смешивать их с боевыми.
    """
    from flask import g

    gcs = gcs or {}
    bp = Blueprint('production', __name__, url_prefix='/api/production')

    def route(rule, methods=('GET',)):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint='production_' + handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, requester, guard_response, guard_status = task_guard()
                    if guard_response is not None:
                        return guard_response, guard_status
                    role = getattr(g, 'effective_task_role', None) or (requester[3] if requester else None)
                    base_role = requester[3] if requester else None
                    ctx = {
                        'user_id': int(requester_id),
                        'name': requester[2] if requester and len(requester) > 2 else '',
                        'role': role,
                        'base_role': base_role,
                        'super_admin': bool(is_super_admin(base_role)),
                    }
                    # Проверка главы сама ходит в базу своим соединением — звать её ДО того,
                    # как этот запрос займёт своё: пул общий на портал, и запрос, который
                    # держит одно соединение и ждёт второе, при занятом пуле ждал бы 30 с.
                    heads_marketing = (task_production.PRODUCTION_DEPARTMENT_CODE
                                       in [str(c).lower() for c in (headed_department_codes(requester_id) or [])])
                    with db._get_cursor() as cursor:
                        marketing = heads_marketing or queries.is_marketing_member(cursor, requester_id)
                    # Раздел открыт супер-админу и маркетингу (решение владельца 16.10.2026:
                    # «можно раздел открыть для суперадминов и для маркетинга»). Оба видят
                    # всё — охват «свои задачи» в запросах остаётся на случай, если раздел
                    # откроют и постановщикам из других отделов.
                    if not (ctx['super_admin'] or marketing):
                        raise ApiError('Раздел «Продакшн» открыт маркетингу и супер-админам', 403, 'PRODUCTION_CLOSED')
                    ctx['marketing'] = bool(marketing)
                    ctx['heads_marketing'] = bool(heads_marketing)
                    ctx['scope_all'] = True
                    return handler(*args, ctx=ctx, **kwargs)
                except ApiError as exc:
                    return _json({'error': str(exc), 'code': exc.code}, exc.status)
                except storage.StorageUnavailable:
                    logging.exception('Продакшн: хранилище недоступно')
                    return _json({'error': 'Хранилище файлов сейчас недоступно, попробуйте позже',
                                  'code': 'PRODUCTION_STORAGE'}, 503)
                except Exception:  # noqa: BLE001
                    logging.exception('Продакшн: ошибка в %s', handler.__name__)
                    return _json({'error': 'Внутренняя ошибка сервера'}, 500)
            return wrapper
        return decorator

    # ── Вспомогательное ──────────────────────────────────────────────────────

    def _person(person):
        url = None
        if avatar_url and person.get('avatar_blob_path'):
            try:
                url = avatar_url(person.get('avatar_bucket'), person.get('avatar_blob_path'))
            except Exception:  # noqa: BLE001 — без фото будут инициалы
                url = None
        return {'id': person['id'], 'name': person.get('name') or '', 'avatar_url': url}

    def _card(task, client, bucket):
        cover = task.get('cover')
        cover_out = None
        if cover:
            cover_out = {'id': cover['id'], 'kind': cover['kind'], 'name': cover['name'],
                         'duration_seconds': cover['duration_seconds'], 'poster_url': None}
            if client is not None and cover.get('poster_blob_path'):
                try:
                    cover_out['poster_url'] = storage.sign(
                        client, cover.get('bucket') or bucket, cover['poster_blob_path'],
                        minutes=materials.VIEW_SIGN_MINUTES, content_type='image/jpeg')
                except Exception:  # noqa: BLE001
                    logging.warning('Продакшн: обложка не подписалась', exc_info=True)
        brief = task.get('brief') or {}
        return {
            'id': task['id'],
            'subject': task['subject'],
            'status': task['status'],
            'priority': task['priority'],
            'due_at': task['due_at'],
            'created_at': task['created_at'],
            'updated_at': task['updated_at'],
            'completed_at': task.get('completed_at'),
            'accepted_at': task.get('accepted_at'),
            'item': brief.get('item'),
            'item_label': task_production.item_label(brief),
            'created_by': task['created_by'],
            'created_by_name': task['created_by_name'],
            'requested_by_name': task['requested_by_name'],
            'assignees': [_person(p) for p in task.get('assignees') or []],
            'version': max(1, task['completed_count'] + (0 if task['status'] in materials.SUBMITTED_STATUSES else 1)),
            'materials_count': task.get('materials_count', 0),
            'cover': cover_out,
        }

    def _client_or_none():
        try:
            return storage.client_of(gcs), storage.bucket_of(gcs)
        except storage.StorageUnavailable:
            return None, None
        except Exception:  # noqa: BLE001
            logging.warning('Продакшн: клиент хранилища не создался', exc_info=True)
            return None, None

    def _load_task(cursor, task_id, ctx):
        found = queries.task(cursor, task_id)
        if not found or found.get('tag') != queries.TAG:
            raise ApiError('Задача не найдена', 404, 'PRODUCTION_TASK_NOT_FOUND')
        if not queries.visible(cursor, found, scope_all=ctx['scope_all'], user_id=ctx['user_id']):
            raise ApiError('Задача не найдена', 404, 'PRODUCTION_TASK_NOT_FOUND')
        return found

    def _is_assignee(task_row, user_id):
        return any(int(p['id']) == int(user_id) for p in task_row.get('assignees') or [])

    def _can_review(cursor, task_row, ctx):
        """Те же два условия, что проверит маршрут статусов задачи при приёмке."""
        scope = db._task_assignee_scope_tx(cursor, task_row['id'])
        if not db._task_visible_for_requester(ctx['role'], ctx['user_id'], task_row['created_by'],
                                              scope, task_row['requested_by_id']):
            return False
        return db._task_can_review(ctx['role'], ctx['user_id'], task_row['created_by'],
                                   scope, task_row['requested_by_id'])

    def _material_out(material, client, bucket, *, can_delete):
        out = {
            'id': material['id'],
            'version': material['version'],
            'status': material['status'],
            'kind': material['kind'],
            'name': material['name'],
            'extension': material['extension'],
            'content_type': material['content_type'],
            'size': material['size'],
            'duration_seconds': material['duration_seconds'],
            'width': material['width'],
            'height': material['height'],
            'uploaded_by': material['uploaded_by'],
            'uploaded_by_name': material['uploaded_by_name'],
            'created_at': material['created_at'],
            'ready_at': material['ready_at'],
            'category': material.get('library_category'),
            'previewable': materials.can_preview(material['kind'], material['content_type']),
            'can_delete': bool(can_delete),
            'poster_url': None,
            'view_url': None,
        }
        if client is not None and material['status'] == 'ready':
            out.update(storage.sign_view(client, material['bucket'] or bucket, material))
        return out

    # ── Доступ к разделу ─────────────────────────────────────────────────────

    @route('/access')
    def access(ctx):
        return _json({
            'open': True,
            'scope': 'all' if ctx['scope_all'] else 'own',
            'marketing': ctx['marketing'],
            # «Медиатека» (файлы без задачи) — полка маркетинга: видит и грузит тот, кто видит всё.
            'library': ctx['scope_all'],
            'max_file_bytes': materials.MAX_MATERIAL_BYTES,
            'chunk_bytes': materials.CHUNK_BYTES,
            'extensions': materials.accepted_extensions(),
        })

    # ── Доска ────────────────────────────────────────────────────────────────

    @route('/board')
    def board(ctx):
        with db._get_cursor() as cursor:
            tasks = queries.board(cursor, scope_all=ctx['scope_all'], user_id=ctx['user_id'])
        client, bucket = _client_or_none() if any(t.get('cover') for t in tasks) else (None, None)
        return _json({'tasks': [_card(t, client, bucket) for t in tasks]})

    # ── Готовое ──────────────────────────────────────────────────────────────

    @route('/done')
    def done(ctx):
        limit = max(1, min(_int(request.args.get('limit'), 30) or 30, 60))
        offset = max(0, _int(request.args.get('offset'), 0) or 0)
        group = (request.args.get('group') or '').strip() or None
        query = (request.args.get('q') or '').strip()[:200]
        with db._get_cursor() as cursor:
            tasks, total = queries.done(cursor, scope_all=ctx['scope_all'], user_id=ctx['user_id'],
                                        query=query, group=group, limit=limit, offset=offset)
        client, bucket = _client_or_none() if any(t.get('cover') for t in tasks) else (None, None)
        return _json({'tasks': [_card(t, client, bucket) for t in tasks], 'total': total,
                      'offset': offset, 'limit': limit})

    # ── Карточка задачи ──────────────────────────────────────────────────────

    @route('/tasks/<int:task_id>')
    def task_detail(task_id, ctx):
        with db._get_cursor() as cursor:
            task_row = _load_task(cursor, task_id, ctx)
            history = queries.history(cursor, task_id)
            rows = queries.materials_of(cursor, task_id)
            can_review = _can_review(cursor, task_row, ctx)
            working = materials.working_version(task_row['status'], task_row['completed_count'])
            # Что ещё грузится у соисполнителей — чтобы не отправить версию без их файлов.
            others_uploading = (queries.uploading_by_others(cursor, task_id, working, ctx['user_id'])
                                if working else [])
        is_assignee = _is_assignee(task_row, ctx['user_id'])
        status = task_row['status']
        completed_count = task_row['completed_count']
        perms = materials.permissions(status=status, is_assignee=is_assignee,
                                      can_review=can_review, is_super_admin=ctx['super_admin'])
        client, bucket = _client_or_none() if rows else (None, None)

        visible_rows = [m for m in rows
                        if m['status'] == 'ready' or m['uploaded_by'] == ctx['user_id']]
        by_version = {}
        for m in visible_rows:
            deletable = materials.can_delete(
                m, status=status, completed_count=completed_count, user_id=ctx['user_id'],
                is_assignee=is_assignee, is_super_admin=ctx['super_admin'])
            by_version.setdefault(m['version'], []).append(
                _material_out(m, client, bucket, can_delete=deletable))

        rounds = {r['version']: r for r in materials.rounds_from_history(
            [(h[0], h[1], h[2], h[3], h[4]) for h in history])}
        top = max([completed_count, working or 0] + list(by_version))
        versions = []
        for number in range(top, 0, -1):
            info = rounds.get(number)
            if info is None and number != working and not by_version.get(number):
                continue
            if number == working:
                state = 'working'
            elif info is None:
                # Файл догрузился уже после сдачи: лежит до следующей версии.
                state = 'draft'
            else:
                state = info['outcome'] or 'review'
            versions.append({
                'version': number,
                'state': state,
                'submitted_at': info['submitted_at'] if info else None,
                'submitted_by_name': info['submitted_by_name'] if info else None,
                'comment': info['comment'] if info else '',
                'outcome_at': info['outcome_at'] if info else None,
                'outcome_by_name': info['outcome_by_name'] if info else None,
                'outcome_comment': info['outcome_comment'] if info else '',
                'materials': by_version.get(number, []),
            })

        card = _card(dict(task_row, cover=None, materials_count=0), None, None)
        card.update({
            'description': task_row['description'],
            'brief': task_row['brief'],
            'requested_by_id': task_row['requested_by_id'],
            'started_at': task_row['started_at'],
        })
        return _json({
            'task': card,
            'versions': versions,
            'working_version': working,
            'others_uploading': others_uploading,
            'permissions': perms,
            'is_assignee': is_assignee,
            'limits': {'max_file_bytes': materials.MAX_MATERIAL_BYTES,
                       'chunk_bytes': materials.CHUNK_BYTES,
                       'max_per_version': materials.MAX_MATERIALS_PER_VERSION},
        })

    # ── Загрузка ─────────────────────────────────────────────────────────────

    def _sweep_stale():
        """Брошенные загрузки старше недели: строку убрать, объект — если успел собраться."""
        try:
            with db._get_cursor() as cursor:
                stale = queries.stale_uploads(cursor)
                for row in stale:
                    queries.delete_material(cursor, row['id'])
            if stale:
                client = storage.client_of(gcs)
                for row in stale:
                    storage.delete_objects(client, row['bucket'], [row['blob_path'], materials.poster_path(row['blob_path'])])
        except Exception:  # noqa: BLE001 — уборка не должна мешать загрузке
            logging.warning('Продакшн: уборка брошенных загрузок не прошла', exc_info=True)

    @route('/tasks/<int:task_id>/uploads', methods=('POST',))
    def start_upload(task_id, ctx):
        data = _body()
        try:
            name, ext, kind, content_type = materials.classify(data.get('name'), data.get('size'))
        except ValueError as exc:
            code = str(exc)
            status = 413 if code == materials.TOO_LARGE else 400
            raise ApiError(materials.error_message(code), status, code)
        size = int(data.get('size'))
        # Отпечаток файла (дата изменения от браузера) — чтобы докачка не подхватила
        # другой файл с тем же именем и размером.
        fingerprint = str(data.get('fingerprint') or '').strip()[:64] or None

        origin = (request.headers.get('Origin') or '').strip().rstrip('/') or None
        if origin and not allowed_origin(origin):
            raise ApiError('Загрузка с этого сайта не разрешена', 403, 'PRODUCTION_ORIGIN')

        with db._get_cursor() as cursor:
            task_row = _load_task(cursor, task_id, ctx)
            if not _is_assignee(task_row, ctx['user_id']):
                raise ApiError('Загружать материалы может исполнитель задачи', 403, 'PRODUCTION_ONLY_ASSIGNEE')
            version = materials.working_version(task_row['status'], task_row['completed_count'])
            if version is None:
                raise ApiError(_closed_text(task_row['status']), 409, 'PRODUCTION_VERSION_CLOSED')
            pending = queries.pending_for(cursor, task_id=task_id, user_id=ctx['user_id'], name=name, size=size,
                                          fingerprint=fingerprint)
            if pending is None and queries.count_in_version(cursor, task_id, version) >= materials.MAX_MATERIALS_PER_VERSION:
                raise ApiError('В одной версии не больше %d файлов' % materials.MAX_MATERIALS_PER_VERSION,
                               409, 'PRODUCTION_TOO_MANY')

        client = storage.client_of(gcs)
        bucket = storage.bucket_of(gcs)

        if pending is not None and pending.get('upload_session'):
            # Тот же файл уже начинали грузить: продолжаем с того места, где встали.
            state = storage.session_offset(pending['upload_session'], pending['size'],
                                           origin=pending.get('upload_origin'))
            if state is not None:
                offset, finished = state
                return _json({'material_id': pending['id'], 'session_url': pending['upload_session'],
                              'offset': offset, 'finished': finished, 'size': pending['size'],
                              'chunk_bytes': materials.CHUNK_BYTES, 'resumed': True})
            # Сессия истекла — заготовку убираем и открываем новую.
            with db._get_cursor() as cursor:
                queries.delete_material(cursor, pending['id'])

        _sweep_stale()
        material_id = uuid.uuid4()
        path = materials.blob_path(task_id, material_id, ext, prefix=blob_prefix or materials.BLOB_PREFIX)
        session_url = storage.open_session(client, bucket, path, content_type=content_type,
                                           size=size, origin=origin)
        refusal = None
        with db._get_cursor() as cursor:
            # Пока открывалась сессия, задачу могли сдать, исполнителя — снять, а
            # соседняя вкладка — добить предел файлов: всё перепроверяем под блокировкой.
            cursor.execute('SELECT status FROM tasks WHERE id = %s FOR UPDATE', (task_id,))
            row = cursor.fetchone()
            task_now = queries.task(cursor, task_id)
            version_now = materials.working_version(row[0] if row else None,
                                                    task_now['completed_count'] if task_now else 0)
            if version_now is None:
                refusal = ApiError(_closed_text((row[0] if row else None) or 'closed'), 409,
                                   'PRODUCTION_VERSION_CLOSED')
            elif not task_now or not _is_assignee(task_now, ctx['user_id']):
                refusal = ApiError('Загружать материалы может исполнитель задачи', 403, 'PRODUCTION_ONLY_ASSIGNEE')
            elif queries.count_in_version(cursor, task_id, version_now) >= materials.MAX_MATERIALS_PER_VERSION:
                refusal = ApiError('В одной версии не больше %d файлов' % materials.MAX_MATERIALS_PER_VERSION,
                                   409, 'PRODUCTION_TOO_MANY')
            else:
                queries.insert_material(
                    cursor, material_id=material_id, task_id=task_id, version=version_now, kind=kind,
                    name=name, extension=ext, content_type=content_type, size=size, bucket=bucket,
                    blob_path=path, upload_session=session_url, upload_origin=origin,
                    uploaded_by=ctx['user_id'], fingerprint=fingerprint)
        if refusal is not None:
            # Сессию отменяем уже после того, как отпустили соединение с базой: это сетевой запрос.
            storage.cancel_session(session_url, origin=origin)
            raise refusal
        return _json({'material_id': str(material_id), 'session_url': session_url, 'offset': 0,
                      'finished': False, 'size': size, 'chunk_bytes': materials.CHUNK_BYTES,
                      'resumed': False}, 201)

    def _closed_text(status):
        if status == 'assigned':
            return 'Сначала возьмите задачу в работу'
        if status == 'completed':
            return 'Задача на проверке — новые файлы можно загрузить после ответа постановщика'
        if status == 'accepted':
            return 'Задача уже принята'
        return 'Сейчас загрузить файлы к этой задаче нельзя'

    def _own_upload(cursor, material_id, ctx):
        """Свой материал и его задача. Чужой — «не найден»: id не подсказывает, что он есть.

        Задача проверяется тем же охватом, что везде в разделе: человек, у которого
        задачу забрали, свою незаконченную загрузку уже не доведёт.
        """
        material = queries.material(cursor, material_id)
        if material is None or material['uploaded_by'] != ctx['user_id']:
            raise ApiError('Загрузка не найдена', 404, 'PRODUCTION_UPLOAD_NOT_FOUND')
        try:
            task_row = _material_access(cursor, material, ctx)
        except ApiError:
            raise ApiError('Загрузка не найдена', 404, 'PRODUCTION_UPLOAD_NOT_FOUND')
        return material, task_row

    def _material_access(cursor, material, ctx):
        """Охват материала: задачи — как у задачи; файла «Медиатеки» — у того, кто видит всё.

        Возвращает строку задачи или None для файла без задачи.
        """
        if materials.is_library(material):
            if not ctx['scope_all']:
                raise ApiError('Файл не найден', 404, 'PRODUCTION_MATERIAL_NOT_FOUND')
            return None
        return _load_task(cursor, material['task_id'], ctx)

    @route('/uploads/<material_id>')
    def upload_state(material_id, ctx):
        _check_uuid(material_id)
        with db._get_cursor() as cursor:
            material, _task_row = _own_upload(cursor, material_id, ctx)
        if material['status'] == 'ready':
            return _json({'offset': material['size'], 'finished': True, 'status': 'ready'})
        state = storage.session_offset(material['upload_session'], material['size'],
                                       origin=material.get('upload_origin'))
        if state is None:
            return _json({'error': 'Загрузка устарела — начните заново', 'code': 'PRODUCTION_UPLOAD_EXPIRED'}, 410)
        offset, finished = state
        return _json({'offset': offset, 'finished': finished, 'status': 'uploading'})

    @route('/uploads/<material_id>/complete', methods=('POST',))
    def complete_upload(material_id, ctx):
        _check_uuid(material_id)
        data = _body()
        with db._get_cursor() as cursor:
            material, _task_row = _own_upload(cursor, material_id, ctx)
        if material['status'] == 'ready':
            return _json({'material': {'id': material['id'], 'status': 'ready'}})

        client = storage.client_of(gcs)
        info = storage.object_info(client, material['bucket'], material['blob_path'])
        if info is None:
            raise ApiError('Файл ещё не догрузился', 409, 'PRODUCTION_UPLOAD_INCOMPLETE')
        problem = None
        message = None
        if info['size'] != material['size']:
            problem = 'размер не совпал'
        client_crc = materials.crc32c_from_client(data.get('crc32c'))
        stored_crc = materials.crc32c_from_gcs(info.get('crc32c'))
        if problem is None and client_crc is not None and stored_crc is not None and client_crc != stored_crc:
            problem = 'контрольная сумма не совпала'
        if problem is not None:
            message = 'Файл повреждён при загрузке — загрузите его ещё раз'
        else:
            head = storage.object_head(client, material['bucket'], material['blob_path'])
            if not materials.content_matches(material['extension'], head):
                problem = 'содержимое не похоже на %s' % material['extension'].upper()
                # Это не порча в пути: внутри просто другой формат (переименованный файл).
                message = 'Это не %s — внутри файл другого формата. Загрузите сам файл, без переименования' % (
                    material['extension'].upper())
        if problem is not None:
            storage.delete_objects(client, material['bucket'], [material['blob_path']])
            with db._get_cursor() as cursor:
                queries.delete_material(cursor, material['id'])
            logging.warning('Продакшн: файл %s отклонён — %s', material['blob_path'], problem)
            raise ApiError(message, 422, 'PRODUCTION_UPLOAD_CORRUPT')

        duration = _positive_float(data.get('duration'), 24 * 3600)
        width = _positive_int(data.get('width'), 100000)
        height = _positive_int(data.get('height'), 100000)
        if materials.is_library(material):
            with db._get_cursor() as cursor:
                locked = queries.material(cursor, material['id'], lock=True)
                if locked is None:
                    raise ApiError('Загрузка отменена', 404, 'PRODUCTION_UPLOAD_NOT_FOUND')
                queries.mark_ready(cursor, locked['id'], version=1, duration=duration, width=width, height=height)
                queries.add_library_event(cursor, material=locked, action=queries.LIBRARY_EVENT_ADDED,
                                          actor_id=ctx['user_id'], category=locked.get('library_category'))
            return _json({'material': {'id': material['id'], 'status': 'ready', 'version': 1}})

        with db._get_cursor() as cursor:
            locked = queries.material(cursor, material['id'], lock=True)
            if locked is None:
                raise ApiError('Загрузка отменена', 404, 'PRODUCTION_UPLOAD_NOT_FOUND')
            cursor.execute('SELECT status FROM tasks WHERE id = %s FOR UPDATE', (locked['task_id'],))
            task_status = (cursor.fetchone() or [None])[0]
            task_now = queries.task(cursor, locked['task_id'])
            completed_count = task_now['completed_count'] if task_now else 0
            removed = not task_now or not _is_assignee(task_now, ctx['user_id'])
            if removed:
                # Пока файл ехал, человека сняли с задачи: в чужую теперь работу он не ляжет.
                queries.delete_material(cursor, locked['id'])
            else:
                # Если задачу успели сдать, пока шла загрузка, файл не подмешивается в
                # версию, которую уже смотрят: он ложится в следующую.
                version = materials.working_version(task_status, completed_count) or (completed_count + 1)
                queries.mark_ready(cursor, locked['id'], version=version, duration=duration,
                                   width=width, height=height)
        if removed:
            storage.delete_objects(client, material['bucket'],
                                   [material['blob_path'], material.get('poster_blob_path')])
            raise ApiError('Вас сняли с этой задачи — файл в неё не добавлен', 403, 'PRODUCTION_ONLY_ASSIGNEE')
        return _json({'material': {'id': material['id'], 'status': 'ready', 'version': version}})

    @route('/uploads/<material_id>/poster', methods=('POST',))
    def upload_poster(material_id, ctx):
        _check_uuid(material_id)
        file = request.files.get('file')
        if not file:
            raise ApiError('Кадр не приложен', 400, 'PRODUCTION_POSTER_MISSING')
        content_type = (file.mimetype or '').lower()
        if content_type not in _POSTER_TYPES:
            raise ApiError('Кадр должен быть картинкой', 400, 'PRODUCTION_POSTER_TYPE')
        data = file.read(materials.MAX_POSTER_BYTES + 1)
        if not data or len(data) > materials.MAX_POSTER_BYTES:
            raise ApiError('Кадр слишком большой', 413, 'PRODUCTION_POSTER_SIZE')
        with db._get_cursor() as cursor:
            material, task_row = _own_upload(cursor, material_id, ctx)
        if material['kind'] not in (materials.KIND_VIDEO, materials.KIND_IMAGE):
            raise ApiError('Кадр нужен только роликам и картинкам', 400, 'PRODUCTION_POSTER_KIND')
        # Обложку меняют, пока файл грузится или его версия ещё у исполнителя. У
        # отправленной и принятой версии её не подменить — её уже видели.
        working = materials.working_version(task_row['status'], task_row['completed_count']) if task_row else None
        if task_row is not None and not (material['status'] == 'uploading' or material['version'] == working
                                         or materials.is_draft(material, task_row['completed_count'])):
            raise ApiError('Эта версия уже отправлена — обложку не поменять', 409, 'PRODUCTION_POSTER_CLOSED')
        path = materials.poster_path(material['blob_path'])
        client = storage.client_of(gcs)
        storage.put_small(client, material['bucket'], path, data, content_type)
        storage.forget_signatures([path])
        with db._get_cursor() as cursor:
            queries.set_poster(cursor, material['id'], path)
        return _json({'ok': True})

    # ── «Медиатека»: просто загрузить файл, без задачи ───────────────────────
    # Просьба владельца 16.10.2026: «как можно просто загружать ролики — данная
    # возможность тоже должна быть на сайте», и в тот же день — категории: баннеры
    # (фото), анимационные, рекламные ролики и рилсы. Полка маркетинга: видит и
    # грузит тот, у кого в разделе охват «всё» (маркетинг, админ со всеми задачами).
    # Путь файла тот же, что у материалов задач: сессия докачки, сверка, обложка.

    def _require_library(ctx):
        if not ctx['scope_all']:
            raise ApiError('«Медиатека» открыта маркетингу', 403, 'PRODUCTION_LIBRARY_CLOSED')

    def _category_or_error(value):
        try:
            return materials.library_category(value)
        except ValueError as exc:
            raise ApiError(materials.error_message(str(exc)), 400, str(exc))

    def _can_manage_library(material, ctx):
        return materials.can_delete_library(material, user_id=ctx['user_id'], is_super_admin=ctx['super_admin'],
                                            heads_marketing=ctx['heads_marketing'])

    def _library_counts(cursor):
        """Числа на плитках: «Все файлы» ('') и каждая категория."""
        by_category = queries.library_counts(cursor)
        counts = {code: by_category.get(code, 0) for code, _label, _kind in materials.LIBRARY_CATEGORIES}
        counts[''] = sum(by_category.values())
        return counts

    @route('/library')
    def library_list(ctx):
        _require_library(ctx)
        limit = max(1, min(_int(request.args.get('limit'), 30) or 30, 60))
        offset = max(0, _int(request.args.get('offset'), 0) or 0)
        query = (request.args.get('q') or '').strip()[:200]
        raw_category = (request.args.get('category') or '').strip()
        category = _category_or_error(raw_category) if raw_category else None
        with db._get_cursor() as cursor:
            rows, total = queries.library(cursor, query=query, category=category, limit=limit, offset=offset)
            counts = _library_counts(cursor)
        client, bucket = _client_or_none() if rows else (None, None)
        items = [_material_out(row, client, bucket, can_delete=_can_manage_library(row, ctx)) for row in rows]
        return _json({'items': items, 'total': total, 'offset': offset, 'limit': limit,
                      'category': category or '', 'counts': counts})

    @route('/library/uploads', methods=('POST',))
    def start_library_upload(ctx):
        _require_library(ctx)
        data = _body()
        try:
            name, ext, kind, content_type, category = materials.classify_library(
                data.get('name'), data.get('size'), data.get('category'))
        except ValueError as exc:
            code = str(exc)
            status = 413 if code == materials.TOO_LARGE else 400
            raise ApiError(materials.error_message(code), status, code)
        size = int(data.get('size'))
        fingerprint = str(data.get('fingerprint') or '').strip()[:64] or None
        origin = (request.headers.get('Origin') or '').strip().rstrip('/') or None
        if origin and not allowed_origin(origin):
            raise ApiError('Загрузка с этого сайта не разрешена', 403, 'PRODUCTION_ORIGIN')

        with db._get_cursor() as cursor:
            pending = queries.pending_for(cursor, task_id=None, user_id=ctx['user_id'], name=name, size=size,
                                          fingerprint=fingerprint)
        client = storage.client_of(gcs)
        bucket = storage.bucket_of(gcs)
        if pending is not None and pending.get('upload_session'):
            state = storage.session_offset(pending['upload_session'], pending['size'],
                                           origin=pending.get('upload_origin'))
            if state is not None:
                if pending.get('library_category') != category:
                    # Тот же файл добавили снова, уже в другую категорию, — ляжет в последнюю выбранную.
                    with db._get_cursor() as cursor:
                        queries.set_library_category(cursor, pending['id'], category)
                offset, finished = state
                return _json({'material_id': pending['id'], 'session_url': pending['upload_session'],
                              'offset': offset, 'finished': finished, 'size': pending['size'],
                              'chunk_bytes': materials.CHUNK_BYTES, 'resumed': True})
            with db._get_cursor() as cursor:
                queries.delete_material(cursor, pending['id'])

        _sweep_stale()
        material_id = uuid.uuid4()
        path = materials.library_blob_path(material_id, ext, prefix=blob_prefix or materials.BLOB_PREFIX)
        session_url = storage.open_session(client, bucket, path, content_type=content_type, size=size, origin=origin)
        with db._get_cursor() as cursor:
            queries.insert_material(
                cursor, material_id=material_id, task_id=None, version=1, kind=kind, name=name, extension=ext,
                content_type=content_type, size=size, bucket=bucket, blob_path=path, upload_session=session_url,
                upload_origin=origin, uploaded_by=ctx['user_id'], fingerprint=fingerprint,
                library_category=category)
        return _json({'material_id': str(material_id), 'session_url': session_url, 'offset': 0,
                      'finished': False, 'size': size, 'chunk_bytes': materials.CHUNK_BYTES,
                      'resumed': False}, 201)

    @route('/library/<material_id>', methods=('PATCH',))
    def update_library_material(material_id, ctx):
        """Переименовать файл и/или перенести в другую категорию.

        Кто вправе — те же, кто вправе удалить. Каждое изменение — строка «Истории».
        """
        _require_library(ctx)
        _check_uuid(material_id)
        data = _body()
        if 'name' not in data and 'category' not in data:
            raise ApiError('Нечего менять', 400, 'PRODUCTION_NOTHING_TO_CHANGE')
        category = _category_or_error(data.get('category')) if 'category' in data else None
        with db._get_cursor() as cursor:
            material = queries.material(cursor, material_id, lock=True)
            if material is None or not materials.is_library(material) or material['status'] != 'ready':
                raise ApiError('Файл не найден', 404, 'PRODUCTION_MATERIAL_NOT_FOUND')
            if not _can_manage_library(material, ctx):
                raise ApiError('Менять файл могут загрузивший, глава маркетинга и супер-админ',
                               403, 'PRODUCTION_MOVE_FORBIDDEN')
            if category is not None and not materials.library_kind_fits(material, category):
                code = (materials.LIBRARY_ONLY_IMAGE if materials.LIBRARY_CATEGORY_KIND[category] == materials.KIND_IMAGE
                        else materials.LIBRARY_ONLY_VIDEO)
                raise ApiError(materials.error_message(code), 400, code)
            name = material['name']
            if 'name' in data:
                try:
                    name = materials.renamed_file(data.get('name'), material['extension'])
                except ValueError as exc:
                    raise ApiError(materials.error_message(str(exc)), 400, str(exc))
            previous = material.get('library_category')
            current = previous
            if category is not None and category != previous:
                queries.set_library_category(cursor, material['id'], category)
                queries.add_library_event(cursor, material=material, action=queries.LIBRARY_EVENT_MOVED,
                                          actor_id=ctx['user_id'], category=category, from_category=previous)
                current = category
            if name != material['name']:
                queries.rename_library_material(cursor, material['id'], name)
                queries.add_library_event(cursor, material=dict(material, name=name),
                                          action=queries.LIBRARY_EVENT_RENAMED, actor_id=ctx['user_id'],
                                          category=current, from_name=material['name'])
        return _json({'ok': True, 'category': current, 'name': name})

    @route('/library/history')
    def library_history(ctx):
        """Кто и когда добавил, перенёс или удалил файл «Медиатеки»."""
        _require_library(ctx)
        limit = max(1, min(_int(request.args.get('limit'), 50) or 50, 100))
        offset = max(0, _int(request.args.get('offset'), 0) or 0)
        query = (request.args.get('q') or '').strip()[:200]
        raw_category = (request.args.get('category') or '').strip()
        category = _category_or_error(raw_category) if raw_category else None
        with db._get_cursor() as cursor:
            events, total = queries.library_history(cursor, query=query, category=category,
                                                    limit=limit, offset=offset)
            alive = queries.library_materials(cursor, [event['material_id'] for event in events])
            counts = _library_counts(cursor)
        client, bucket = _client_or_none() if alive else (None, None)
        files = {mid: _material_out(row, client, bucket, can_delete=_can_manage_library(row, ctx))
                 for mid, row in alive.items()}
        people = {}
        out = []
        for event in events:
            actor = event['actor']
            if actor is not None and actor['id'] not in people:
                people[actor['id']] = _person(actor)
            out.append({
                'id': event['id'],
                'action': event['action'],
                'at': event['at'],
                'name': event['name'],
                'kind': event['kind'],
                'size': event['size'],
                'category': event['category'],
                'from_category': event['from_category'],
                'from_name': event.get('from_name'),
                'actor': people.get(actor['id']) if actor is not None else None,
                # Файл на месте — строку можно открыть; удалённый остаётся только записью.
                'material': files.get(event['material_id']),
            })
        return _json({'events': out, 'total': total, 'offset': offset, 'limit': limit,
                      'category': category or '', 'counts': counts})

    # ── Удаление и скачивание ────────────────────────────────────────────────

    @route('/materials/<material_id>', methods=('DELETE',))
    def delete_material(material_id, ctx):
        _check_uuid(material_id)
        # Клиент хранилища — до того, как строка исчезнет из базы: если облако
        # недоступно, человек получит 503, а файл останется в списке, а не
        # пропадёт из него, оставшись в бакете навсегда.
        client = storage.client_of(gcs)
        with db._get_cursor() as cursor:
            material = queries.material(cursor, material_id, lock=True)
            if material is None:
                raise ApiError('Файл не найден', 404, 'PRODUCTION_MATERIAL_NOT_FOUND')
            task_row = _material_access(cursor, material, ctx)
            is_assignee = bool(task_row) and _is_assignee(task_row, ctx['user_id'])
            if material['status'] == 'uploading':
                allowed = material['uploaded_by'] == ctx['user_id'] or ctx['super_admin']
            elif task_row is None:
                allowed = _can_manage_library(material, ctx)
            else:
                allowed = materials.can_delete(
                    material, status=task_row['status'], completed_count=task_row['completed_count'],
                    user_id=ctx['user_id'], is_assignee=is_assignee, is_super_admin=ctx['super_admin'])
            if not allowed:
                raise ApiError('Удалить файл могут загрузивший, глава маркетинга и супер-админ'
                               if task_row is None else
                               'Этот файл уже отправлен на проверку — удалить его нельзя',
                               403, 'PRODUCTION_DELETE_FORBIDDEN')
            queries.delete_material(cursor, material['id'])
            if task_row is None and material['status'] == 'ready':
                queries.add_library_event(cursor, material=material, action=queries.LIBRARY_EVENT_DELETED,
                                          actor_id=ctx['user_id'], category=material.get('library_category'))
        if material['status'] == 'uploading' and material.get('upload_session'):
            storage.cancel_session(material['upload_session'], origin=material.get('upload_origin'))
        storage.delete_objects(client, material['bucket'],
                               [material['blob_path'], material.get('poster_blob_path')])
        return _json({'ok': True})

    @route('/materials/<material_id>/download')
    def download_material(material_id, ctx):
        _check_uuid(material_id)
        with db._get_cursor() as cursor:
            material = queries.material(cursor, material_id)
            if material is None or material['status'] != 'ready':
                raise ApiError('Файл не найден', 404, 'PRODUCTION_MATERIAL_NOT_FOUND')
            _material_access(cursor, material, ctx)
        client = storage.client_of(gcs)
        try:
            url = storage.sign(client, material['bucket'], material['blob_path'],
                               minutes=materials.DOWNLOAD_SIGN_MINUTES, purpose='download',
                               name=material['name'], content_type=material['content_type'])
        except Exception as exc:  # noqa: BLE001 — нет ключа подписи или облако не ответило
            raise storage.StorageUnavailable('SIGN: %s' % exc.__class__.__name__) from exc
        return _json({'url': url})

    return bp


def _body():
    """Тело запроса — объект; массив или строка вместо объекта — то же, что пустое тело."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _check_uuid(value):
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise ApiError('Файл не найден', 404, 'PRODUCTION_MATERIAL_NOT_FOUND')


def _positive_float(value, ceiling):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0 or number > ceiling:
        return None
    return round(number, 3)


def _positive_int(value, ceiling):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if number <= 0 or number > ceiling:
        return None
    return number
