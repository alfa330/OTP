# -*- coding: utf-8 -*-
"""HTTP раздела «Дресс-код» (Flask Blueprint /api/dress_code).

Две аудитории:
* сотрудник, которому положено фото (настройка: отделы и роли), — /me, /photo, /ack;
* проверяющий — вкладка «Дресс-код» в «Отметках» (ТЗ п. 6: «Отметки → Фото»). Вход
  тот же, что у самих «Отметок» (`reviewer_access` — гард раздела, а не его копия):
  глобальные админы и отдел кадров видят все отделы, глава отдела — свой.

Суперадмину сверх этого — стенд камеры (владелец 13.11.2026: «добавь суперадмину тестовый
стенд, чтобы я проверял и корректировал»): правила автопроверки кадра (PUT /settings с
camera), пробный кадр через тот же ИИ без записи (/stand/analyze) и удаление пробных фото
(DELETE /checks/<id>).

Telegram уходит после фиксации транзакции; залитое в бакет стирается, если строка не
записалась (service.submit).
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import ROLE_LABELS, notify, photos, report, schema, service

log = logging.getLogger(__name__)

MAX_PERIOD_DAYS = 62
UPLOAD_LIMIT = photos.MAX_UPLOAD_BYTES + 512 * 1024

_settings_cache = {'ts': 0.0, 'value': None}
_settings_lock = threading.Lock()
SETTINGS_TTL = 60


def cached_settings(db):
    """Настройка раздела для флага в профиле: читается не чаще раза в минуту."""
    now = time.monotonic()
    with _settings_lock:
        if _settings_cache['value'] is not None and now - _settings_cache['ts'] < SETTINGS_TTL:
            return _settings_cache['value']
    with db._get_cursor() as cursor:
        value = service.load_settings(cursor) if schema.schema_is_ready(cursor) else None
    with _settings_lock:
        _settings_cache.update(ts=now, value=value)
    return value


def forget_settings_cache():
    with _settings_lock:
        _settings_cache.update(ts=0.0, value=None)


def section_open_for(db, user_id):
    """Флаг профиля dress_code_access: человеку положено фото (то же правило, что у /photo)."""
    if user_id is None:
        return False
    try:
        settings = cached_settings(db)
        if not settings or not settings.get('department_ids') or not settings.get('roles'):
            return False
        with db._get_cursor() as cursor:
            user = service.user_row(cursor, user_id)
        return service.must_photograph(user, settings)
    except Exception:
        log.exception('Дресс-код: не удалось определить, положено ли фото пользователю %s', user_id)
        return False


def _day(value, fallback):
    text = str(value or '').strip()[:10]
    if not text:
        return fallback
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        raise service.DressCodeError('Дата должна быть в формате ГГГГ-ММ-ДД')


def _period():
    today = service.today_almaty()
    date_from = _day(request.args.get('date_from'), today)
    date_to = _day(request.args.get('date_to'), date_from)
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days > MAX_PERIOD_DAYS:
        raise service.DressCodeError(f'Период не больше {MAX_PERIOD_DAYS + 1} дней')
    return date_from, date_to


def build_dress_code_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                               reviewer_access, send_telegram=None, web_app_base_url=None, gcs=None,
                               analyze=None, is_super_admin=None):
    bp = Blueprint('dress_code', __name__, url_prefix='/api/dress_code')

    def _is_super(cursor, user_id):
        """Суперадмин — по роли из базы тем же правилом, что во всём портале (is_super_admin
        приходит из приложения); без него — точное имя роли."""
        role = (service.user_row(cursor, user_id) or {}).get('role')
        if is_super_admin is not None:
            return bool(is_super_admin(role))
        return str(role or '').strip().lower() == 'super_admin'

    def route(rule, methods=('GET',), reviewer=False, super_only=False):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    if reviewer:
                        requester_id, department_ids, error = reviewer_access()
                    else:
                        requester_id, _requester, error = resolve_requester()
                        department_ids = None
                    if error:
                        # resolve_requester отдаёт (текст, код), гард «Отметок» — готовый ответ Flask.
                        if isinstance(error, tuple) and len(error) == 2 and isinstance(error[0], str):
                            return jsonify({'error': error[0]}), error[1]
                        return error
                    with db._get_cursor() as cursor:
                        if not schema.schema_is_ready(cursor):
                            return jsonify({'error': 'Раздел «Дресс-код» ещё не развёрнут',
                                            'code': 'DRESS_CODE_NOT_READY'}), 503
                        if super_only and not _is_super(cursor, requester_id):
                            return jsonify({'error': 'Доступно только суперадмину',
                                            'code': 'DRESS_CODE_SUPER_ONLY'}), 403
                    kwargs['requester_id'] = int(requester_id)
                    if reviewer:
                        kwargs['department_ids'] = department_ids
                    return handler(*args, **kwargs)
                except service.DressCodeError as exc:
                    return jsonify({'error': exc.message, 'code': exc.code}), exc.status
                except photos.PhotoError as exc:
                    return jsonify({'error': exc.message, 'code': exc.code}), exc.status
                except Exception:
                    log.exception('Дресс-код: %s упал', handler.__name__)
                    return jsonify({'error': 'Внутренняя ошибка раздела «Дресс-код»'}), 500
            return wrapper
        return decorator

    def _send(messages):
        if not send_telegram or not messages:
            return 0
        return notify.send_all(send_telegram, messages)

    def notify_reviewers(checks):
        """Новые спорные фото — главе отдела, один раз на фото."""
        messages = []
        for check in checks:
            with db._get_cursor() as cursor:
                if not service.mark_reviewers_notified(cursor, check['id']):
                    continue
                chats = notify.reviewer_chats(cursor, check)
            text, markup = notify.review_message(check, web_app_base_url)
            messages.extend((chat, text, markup) for chat in chats)
        return _send(messages)

    bp.notify_reviewers = notify_reviewers

    # ── Сотрудник ───────────────────────────────────────────────────────────

    @route('/me')
    def dress_code_me(requester_id):
        with db._get_cursor() as cursor:
            state = service.my_state(cursor, gcs, requester_id)
        return jsonify({'status': 'success', **state}), 200

    @route('/photo', methods=('POST',))
    def dress_code_photo(requester_id):
        if (request.content_length or 0) > UPLOAD_LIMIT:
            return jsonify({'error': 'Фото больше 8 МБ'}), 413
        upload = request.files.get('photo')
        if upload is None:
            return jsonify({'error': 'Фото не пришло'}), 400
        result = service.submit(
            db, gcs, requester_id, upload.read(),
            attempt=request.form.get('attempt'), source=request.form.get('source'),
            retake_of=request.form.get('retake_of') or None, analyze=analyze)
        if result.get('retake'):
            return jsonify({'status': 'retake', **result}), 200
        check = result['check']
        if service.needs_review(check):
            notify_reviewers([check])
        with db._get_cursor() as cursor:
            state = service.my_state(cursor, gcs, requester_id)
        return jsonify({'status': 'success', 'accepted': service.employee_view(gcs, check), **state}), 200

    @route('/checks/<int:check_id>/ack', methods=('POST',))
    def dress_code_ack(check_id, requester_id):
        with db._get_cursor() as cursor:
            service.acknowledge(cursor, check_id, requester_id)
        with db._get_cursor() as cursor:
            state = service.my_state(cursor, gcs, requester_id)
        return jsonify({'status': 'success', **state}), 200

    # ── Проверяющий ─────────────────────────────────────────────────────────

    def _filters():
        date_from, date_to = _period()
        return {
            'date_from': date_from, 'date_to': date_to,
            'ai_status': (request.args.get('ai_status') or '').strip() or None,
            'decision': (request.args.get('decision') or '').strip() or None,
            'q': (request.args.get('q') or '').strip()[:100] or None,
            'city': (request.args.get('city') or '').strip()[:100] or None,
        }

    def _scope(department_ids):
        """Отдел из фильтра — только внутри своих отделов проверяющего."""
        wanted = request.args.get('department_id')
        if not wanted or not str(wanted).isdigit():
            return department_ids
        wanted = int(wanted)
        if department_ids is not None and wanted not in department_ids:
            return []
        return [wanted]

    @route('/checks', reviewer=True)
    def dress_code_checks(requester_id, department_ids):
        filters = _filters()
        scope = _scope(department_ids)
        with db._get_cursor() as cursor:
            settings = service.load_settings(cursor)
            listing = service.list_checks(cursor, gcs, department_ids=scope, **filters)
            missing = service.missing_photos(
                cursor, settings, date_from=filters['date_from'], date_to=filters['date_to'],
                department_ids=scope, q=filters['q'], city=filters['city'])
            cursor.execute("""
                SELECT DISTINCT city FROM users
                 WHERE department_id = ANY(%s) AND COALESCE(city, '') <> '' AND status = 'working'
                 ORDER BY city
            """, (list(scope if scope is not None else settings['department_ids']),))
            cities = [row[0] for row in cursor.fetchall()]
            is_super = _is_super(cursor, requester_id)
        return jsonify({
            'status': 'success', **listing, 'missing': missing['rows'], 'shifts': missing['shifts'],
            'cities': cities, 'full_access': department_ids is None, 'is_super_admin': is_super,
            'date_from': filters['date_from'].isoformat(), 'date_to': filters['date_to'].isoformat(),
        }), 200

    @route('/checks/<int:check_id>', reviewer=True)
    def dress_code_check(check_id, requester_id, department_ids):
        with db._get_cursor() as cursor:
            detail = service.check_detail(cursor, gcs, check_id, department_ids)
        if not detail:
            return jsonify({'error': 'Фото не найдено'}), 404
        return jsonify({'status': 'success', 'check': detail}), 200

    @route('/checks/<int:check_id>', methods=('DELETE',), reviewer=True, super_only=True)
    def dress_code_check_delete(check_id, requester_id, department_ids):
        """Суперадмин убирает пробные фото: строка, повторные к ней, журнал и файлы."""
        deleted = service.delete_check(db, gcs, check_id)
        log.info('Дресс-код: суперадмин %s удалил фото %s', requester_id, deleted)
        return jsonify({'status': 'success', 'deleted': deleted}), 200

    @route('/checks/<int:check_id>/decision', methods=('POST',), reviewer=True)
    def dress_code_decision(check_id, requester_id, department_ids):
        payload = request.get_json(silent=True) or {}
        with db._get_cursor() as cursor:
            actor = service.user_row(cursor, requester_id)
            outcome = service.decide(
                cursor, check_id, department_ids=department_ids, actor_id=requester_id,
                actor_name=(actor or {}).get('name'), decision=str(payload.get('decision') or ''),
                comment=payload.get('comment'))
            messages = []
            for event in outcome['events']:
                check = event['check']
                if event['kind'] == 'repeat':
                    text, markup = notify.repeat_message(check, event.get('previous'), web_app_base_url)
                    messages.extend((chat, text, markup) for chat in notify.reviewer_chats(cursor, check))
                else:
                    text, markup = notify.employee_message(check, web_app_base_url)
                    chat = notify.employee_chat(cursor, check)
                    if chat and text:
                        messages.append((chat, text, markup))
        _send(messages)
        with db._get_cursor() as cursor:
            detail = service.check_detail(cursor, gcs, check_id, department_ids)
        return jsonify({'status': 'success', 'check': detail}), 200

    @route('/export', reviewer=True)
    def dress_code_export(requester_id, department_ids):
        filters = _filters()
        scope = _scope(department_ids)
        with db._get_cursor() as cursor:
            settings = service.load_settings(cursor)
            listing = service.list_checks(cursor, None, department_ids=scope, limit=5000, **filters)
            missing = service.missing_photos(
                cursor, settings, date_from=filters['date_from'], date_to=filters['date_to'],
                department_ids=scope, q=filters['q'], city=filters['city'])
        data = report.build(listing['rows'], missing, listing['summary'],
                            date_from=filters['date_from'], date_to=filters['date_to'],
                            base_url=web_app_base_url)
        name = 'dress_code_%s_%s.xlsx' % (filters['date_from'].strftime('%Y%m%d'),
                                          filters['date_to'].strftime('%Y%m%d'))
        return send_file(BytesIO(data), as_attachment=True, download_name=name,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    @route('/settings', methods=('GET', 'PUT'), reviewer=True)
    def dress_code_settings(requester_id, department_ids):
        """Правила меняет любой проверяющий (ТЗ п. 8: «администратор»); кто снимает
        фото (отделы и роли) — только полный доступ: глава отдела не назначает фото
        чужим отделам; правила камеры (camera) — только суперадмин, со стенда."""
        full = department_ids is None
        with db._get_cursor() as cursor:
            is_super = _is_super(cursor, requester_id)
            if request.method == 'PUT':
                payload = request.get_json(silent=True) or {}
                if not full and ('department_ids' in payload or 'roles' in payload):
                    return jsonify({'error': 'Кто снимает фото, меняют администраторы и отдел кадров'}), 403
                if 'camera' in payload and not is_super:
                    return jsonify({'error': 'Правила камеры меняет только суперадмин',
                                    'code': 'DRESS_CODE_SUPER_ONLY'}), 403
                try:
                    service.save_settings(
                        cursor, requester_id, rules_body=payload.get('rules_body'),
                        department_ids=payload.get('department_ids') if full else None,
                        roles=payload.get('roles') if full else None,
                        camera=payload.get('camera') if 'camera' in payload else None)
                except ValueError as exc:
                    return jsonify({'error': str(exc)}), 400
                forget_settings_cache()
            settings = service.load_settings(cursor)
            departments = service.department_choices(cursor) if full else []
        return jsonify({'status': 'success', 'settings': settings, 'departments': departments,
                        'roles': [{'value': key, 'label': label} for key, label in ROLE_LABELS.items()],
                        'can_edit_scope': full, 'is_super_admin': is_super}), 200

    @route('/stand/analyze', methods=('POST',), reviewer=True, super_only=True)
    def dress_code_stand_analyze(requester_id, department_ids):
        """Стенд камеры: пробный кадр проверяет тот же ИИ по действующим правилам, но ничего
        не сохраняется — ни в базе, ни в бакете."""
        if (request.content_length or 0) > UPLOAD_LIMIT:
            return jsonify({'error': 'Фото больше 8 МБ'}), 413
        upload = request.files.get('photo')
        if upload is None:
            return jsonify({'error': 'Фото не пришло'}), 400
        with db._get_cursor() as cursor:
            rules_text = service.load_settings(cursor)['rules']['body']
        result = service.stand_analyze(upload.read(), rules_text, analyze=analyze)
        return jsonify({'status': 'success', 'result': result}), 200

    return bp


def run_pending(db, gcs, notify_reviewers, *, limit=3, analyze=None):
    """Фоновая джоба: ИИ повторно смотрит фото «Ожидает анализа», спорные — руководителю."""
    became = service.analyze_pending(db, gcs, limit=limit, analyze=analyze)
    if became and notify_reviewers:
        notify_reviewers(became)
    return len(became)
