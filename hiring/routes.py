# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Воронка найма» (Flask Blueprint /api/hiring).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2 сам подключает
модуль, обратный импорт был бы циклом (как у rental/ и twofa/). Соглашения портала: методы
включают OPTIONS и первым делом отдаётся preflight, авторизация — общий require_api_key,
ошибка — {"error": "...", "code": "..."}.

    GET    ''                               экран: права, справочники, состояние интеграций
    GET    /desk                             рабочий стол: план найма, задачи на сегодня, моя воронка, мотивация
    GET    /board                            канбан: колонки с порцией карточек и счётчиками
    GET    /cards                            список по фильтрам (и по ids — для клика по цифре дашборда)
    POST   /cards                            отклик вручную
    GET    /cards/<id>                       карточка целиком (гасит уведомления по ней)
    PATCH  /cards/<id>                       поля карточки
    POST   /cards/<id>/take                  «Взять в работу»
    POST   /cards/<id>/touches               касание: звонок (исход можно не указывать — ждём телефонию), WhatsApp, комментарий
    PATCH  /cards/<id>/touches/<touch_id>    дописать касание: комментарий, следующий контакт, исход звонка без телефонии
    PUT    /cards/<id>/substage              подстатус «В работе» / «Приглашен в офис»
    POST   /cards/<id>/move                  переход по этапам
    POST   /cards/<id>/reassign              переназначить / вернуть в пул (руководитель)
    POST   /cards/<id>/shifts/sync           пересчитать смены из статусов работы сейчас (руками смены не ведутся)
    PUT    /cards/<id>/motivation            выплачено / отменено (руководитель рекрутинга)
    POST   /upload                           файл базы контактов (multipart: file, source_id)
    GET    /batches, /batches/<id>           партии загрузки
    GET    /reports/dashboard[/export]       итоговый дашборд (ТЗ 8.1), выгрузка
    GET    /reports/cards?key=               список карточек за цифрой дашборда
    GET    /reports/motivation[/export]      начисления месяца
    …/people, /sources, /vacancies, /reasons, /groups, /plans, /integrations — настройки

Внешний вход откликов (Kaspi, таргет, любой интегратор) — отдельный Blueprint
/api/hiring/external/responses по ключу партнёра с областью «Отклики в воронку найма».
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request

from . import access, notices, queries, reports, rules
from .service import HiringError

log = logging.getLogger(__name__)

EXTERNAL_MAX_ITEMS = 100
EXTERNAL_MAX_BODY = 256 * 1024
EXTERNAL_PER_MINUTE = 30


def _json(value):
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat(timespec='seconds')
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _bad(message, code, status=400):
    return jsonify({"error": message, "code": code}), status


def _int(value):
    if value is None or value == '' or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def public_card(card, ctx):
    """Карточка наружу: телефон только тому, кому положено (ТЗ 2)."""
    data = _json(dict(card))
    if not access.sees_phone(ctx, card):
        data['phone'] = None
        data['whatsapp'] = None
        data['phone_hidden'] = True
    else:
        data['phone_pretty'] = rules.pretty_phone(card.get('phone'))
        data['whatsapp_link'] = rules.whatsapp_link(card.get('whatsapp') or card.get('phone'))
    data['no_answer_suggest'] = (card.get('stage') == rules.IN_WORK
                                 and int(card.get('no_answer_streak') or 0) >= rules.NO_ANSWER_SUGGEST_AFTER)
    # Из пула путь один — «Взять в работу» (отдельная кнопка и ручка /take), поэтому целей перехода у
    # карточки пула нет: иначе интерфейс рисовал бы второй путь в «В работе» мимо закрепления.
    data['targets'] = [] if card.get('stage') in rules.POOL_STAGES else rules.TARGETS_FROM.get(card.get('stage'), [])
    return data


def build_hiring_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester, service,
                           dial_list=None):
    """dial_list — () -> сервис раздела «Удаленный КЦ» (dial_list.service.DialListService) или None.
    Через него руководитель рекрутинга сажает рекрутера на линию Binotel прямо из настроек воронки,
    чтобы отклики прозванивались из iCore Phone (владелец 30.10.2026). Без него линии недоступны."""
    bp = Blueprint('hiring', __name__, url_prefix='/api/hiring')

    def _dial_list():
        try:
            return dial_list() if callable(dial_list) else dial_list
        except Exception:  # noqa: BLE001
            return None

    def hiring_route(rule, methods=('GET',)):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    ctx = service.context(requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    if not access.can_open_section(ctx):
                        return _bad('Раздел «Воронка найма» вам не открыт', 'HIRING_SECTION_CLOSED', 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except HiringError as exc:
                    return _bad(exc.message, exc.code, exc.status)
                except Exception as exc:  # noqa: BLE001
                    log.exception('hiring: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Воронка найма»",
                                    "detail": type(exc).__name__}), 500

            return wrapper

        return decorator

    def _manage_only(ctx):
        if not access.can_manage(ctx):
            raise HiringError('HIRING_FORBIDDEN', 'Настройки ведёт руководитель рекрутинга', 403)

    def _body():
        return request.get_json(silent=True) or {}

    def _dictionaries(cursor, ctx):
        caps = access.capabilities(ctx)
        return {
            'sources': _json(queries.list_sources(cursor, active_only=not caps['manage'])),
            'vacancies': _json(queries.list_vacancies(cursor, active_only=not caps['manage'])),
            'reasons': _json(queries.list_reasons(cursor, active_only=not caps['manage'])),
            'groups': _json(queries.list_groups(cursor, active_only=not caps['manage'])),
            'recruiters': _json(queries.recruiters_with_cards(cursor)),
            'cities': queries.distinct_cities(cursor),
            'departments': _json(queries.departments(cursor)) if caps['manage'] else [],
        }

    # ─── Экран ───────────────────────────────────────────────────────────────

    @hiring_route('')
    def hiring_screen(ctx):
        caps = access.capabilities(ctx)
        with db._get_cursor() as cursor:
            payload = {
                'capabilities': caps,
                'me': {'id': ctx['user_id'], 'name': ctx['name'], 'scope_label': access.scope_label(ctx)},
                'dictionaries': _dictionaries(cursor, ctx),
                'meta': {
                    'stages': [{'code': code, 'label': label} for code, label in rules.STAGES],
                    'substages': {stage: [{'code': code, 'label': label} for code, label in items]
                                  for stage, items in rules.SUBSTAGES.items()},
                    'call_results': [{'code': code, 'label': label} for code, label in rules.CALL_RESULTS],
                    'targets': rules.TARGETS_FROM,
                    'stage_dates': [{'code': code, 'label': label} for code, label in reports.STAGE_DATE_OPTIONS],
                    'shifts_to_hire': rules.SHIFTS_TO_HIRE,
                    'idle_days': rules.IDLE_RETURN_DAYS,
                    'grant_kinds': [{'code': code, 'label': rules.GRANT_LABELS[code]} for code in rules.GRANT_KINDS],
                    'reason_kinds': [{'code': code, 'label': rules.REASON_KIND_LABELS[code]} for code in rules.REASON_KINDS],
                    'motivation_labels': rules.MOTIVATION_LABELS,
                },
                'integrations': _json(queries.list_integrations(cursor)) if caps['manage'] else [],
                'server_time': rules.now_almaty().isoformat(timespec='seconds'),
            }
        return jsonify(payload)

    # ─── Рабочий стол ────────────────────────────────────────────────────────

    @hiring_route('/desk')
    def hiring_desk(ctx):
        caps = access.capabilities(ctx)
        now = rules.now_almaty()
        month = str(request.args.get('month') or rules.month_of(now))
        try:
            month_start, month_end = rules.month_bounds(month)
        except (TypeError, ValueError):
            month = rules.month_of(now)
            month_start, month_end = rules.month_bounds(month)
        recruiter_id = _int(request.args.get('recruiter_id')) if caps['lead'] else ctx['user_id']
        if not caps['lead']:
            recruiter_id = ctx['user_id']
        today = datetime(now.year, now.month, now.day)
        # Фильтры рабочего стола (ТЗ 3): вакансия, город, источник, этап — на задачи и воронку.
        desk_filters = {
            'vacancy_id': _int(request.args.get('vacancy_id')), 'city': request.args.get('city') or None,
            'source_id': _int(request.args.get('source_id')),
            'stage': request.args.get('stage') if request.args.get('stage') in rules.STAGE_CODES else None,
        }
        with db._get_cursor() as cursor:
            plans = queries.list_plans(cursor, month_start.date(), recruiter_id=recruiter_id)
            facts = queries.plan_facts(cursor, month_start, month_end)
            for plan in plans:
                if desk_filters['vacancy_id'] and plan['vacancy_id'] and plan['vacancy_id'] != desk_filters['vacancy_id']:
                    plan['hidden'] = True
                plan['fact'] = sum(1 for fact in facts
                                   if (not plan['vacancy_id'] or fact['vacancy_id'] == plan['vacancy_id'])
                                   and (not plan['department_id'] or fact['department_id'] == plan['department_id'])
                                   and (not plan['recruiter_id'] or fact['recruiter_id'] == plan['recruiter_id']))
                plan['remaining'] = max(0.0, float(plan['plan_fte'] or 0) - plan['fact'])
                plan['overdue'] = bool(plan['planned_date'] and plan['planned_date'] < today.date() and plan['remaining'] > 0)
            plans = [plan for plan in plans if not plan.get('hidden')]
            tasks = queries.desk_tasks(cursor, recruiter_id or ctx['user_id'], today, today + timedelta(days=1), desk_filters)
            funnel_counts = queries.stage_counts_for_recruiter(cursor, recruiter_id, month_start, month_end, desk_filters)
            motivation = queries.motivation_list(cursor, month_start, month_end, recruiter_id=recruiter_id)
        funnel = []
        previous = None
        base = funnel_counts.get(rules.IN_WORK, 0)
        for stage in rules.FUNNEL_STAGES:
            count = int(funnel_counts.get(stage, 0))
            funnel.append({'stage': stage, 'label': rules.FUNNEL_LABELS[stage], 'count': count,
                           'from_previous': 100.0 if previous is None and count else rules.through_rate(count, previous),
                           'through': rules.through_rate(count, base)})
            previous = count
        grouped = {'today': [], 'overdue': [], 'office': [], 'retry': []}
        for card in tasks:
            next_at = card.get('next_contact_at')
            if card['stage'] == rules.INVITED_OFFICE and card.get('office_visit_at') and today <= card['office_visit_at'] < today + timedelta(days=1):
                grouped['office'].append(card)
            elif next_at and next_at < today:
                grouped['overdue'].append(card)
            elif next_at and today <= next_at < today + timedelta(days=1):
                grouped['today'].append(card)
            else:
                grouped['retry'].append(card)
        return jsonify({
            'month': month,
            'recruiter_id': recruiter_id,
            'plans': _json(plans),
            'tasks': {key: [public_card(card, ctx) for card in items] for key, items in grouped.items()},
            'funnel': funnel,
            'motivation': {
                'counted': sum(1 for row in motivation if row['status'] in (rules.MOTIVATION_COUNTED, rules.MOTIVATION_PAID)),
                'pending': sum(1 for row in motivation if row['status'] == rules.MOTIVATION_PENDING),
                'items': _json(motivation),
            },
        })

    # ─── Доска и списки ──────────────────────────────────────────────────────

    def _filters_from_args(ctx):
        args = request.args
        caps = access.capabilities(ctx)
        filters = {
            'vacancy_id': _int(args.get('vacancy_id')), 'city': args.get('city') or None,
            'source_id': _int(args.get('source_id')), 'recruiter_id': _int(args.get('recruiter_id')),
            'stage': args.get('stage') or None, 'query': args.get('q') or None,
        }
        # «Мои и пул» осмысленно только рекрутеру: тренеру и руководителю отдела такой отбор
        # пересёкся бы с их кругом (группы, отдел) в пустоту.
        if args.get('mine') in ('1', 'true') and caps['recruiter']:
            filters['mine_user_id'] = ctx['user_id']
        # Удалённый рекрутер телефонов в пуле не видит — и искать по цифрам телефона может только
        # среди своих карточек, иначе поиск отвечал бы «есть/нет» о скрытом номере.
        if caps['remote']:
            filters['phone_scope_user_id'] = ctx['user_id']
        if args.get('from'):
            try:
                filters['from'] = datetime.fromisoformat(str(args.get('from'))[:10])
            except ValueError:
                pass
        if args.get('to'):
            try:
                filters['to'] = datetime.fromisoformat(str(args.get('to'))[:10]) + timedelta(days=1)
            except ValueError:
                pass
        return filters

    @hiring_route('/board')
    def hiring_board(ctx):
        caps = access.capabilities(ctx)
        filters = _filters_from_args(ctx)
        with db._get_cursor() as cursor:
            columns, counts = queries.board(cursor, caps, ctx['user_id'], filters)
        return jsonify({
            'columns': [{'stage': code, 'label': label, 'total': counts.get(code, 0),
                         'cards': [public_card(card, ctx) for card in columns.get(code, [])]}
                        for code, label in rules.STAGES],
        })

    @hiring_route('/cards')
    def hiring_cards(ctx):
        caps = access.capabilities(ctx)
        filters = _filters_from_args(ctx)
        ids = request.args.get('ids')
        if ids:
            filters['ids'] = [int(x) for x in str(ids).split(',') if x.strip().isdigit()][:2000]
        limit = max(1, min(_int(request.args.get('limit')) or 100, 500))
        offset = max(0, _int(request.args.get('offset')) or 0)
        with db._get_cursor() as cursor:
            total, rows = queries.list_cards(cursor, caps, ctx['user_id'], filters, limit=limit, offset=offset)
        return jsonify({'total': total, 'items': [public_card(card, ctx) for card in rows]})

    @hiring_route('/cards', methods=('POST',))
    def hiring_add_response(ctx):
        outcome, card = service.add_response(ctx, _body())
        return jsonify({'outcome': outcome, 'card': public_card(card, ctx)}), 201 if outcome == 'created' else 200

    @hiring_route('/cards/<int:card_id>')
    def hiring_card(card_id, ctx):
        now = rules.now_almaty()
        with db._get_cursor() as cursor:
            card = queries.get_card(cursor, card_id)
            if not card or not service.visible(ctx, card):
                return _bad('Карточка не найдена', 'HIRING_NOT_FOUND', 404)
            queries.mark_card_seen(cursor, ctx['user_id'], card_id, now)
            payload = {
                'card': public_card(card, ctx),
                'touches': _json(queries.card_touches(cursor, card_id)),
                'stages': _json(queries.card_stages(cursor, card_id)),
                'assignments': _json(queries.card_assignments(cursor, card_id)),
                'shifts': _json(queries.card_shifts(cursor, card_id)),
                'motivation': _json(queries.get_motivation(cursor, card_id)),
                # Звонки из телефонии Binotel (исход, длительность, запись) — к касаниям по touch_id.
                'calls': _json(queries.card_calls(cursor, card_id)),
                'repeat_of': None,
            }
            if card.get('repeat_of_card_id'):
                previous = queries.get_card(cursor, card['repeat_of_card_id'])
                if previous:
                    payload['repeat_of'] = {'id': previous['id'], 'candidate_no': previous.get('candidate_no'),
                                            'stage': previous['stage'], 'closed_at': _json(previous.get('closed_at')),
                                            'close_reason_text': previous.get('close_reason_text')}
        caps = access.capabilities(ctx)
        payload['actor'] = sorted(rules.actor_kinds(caps, card, ctx['user_id'], ctx.get('trainer_group_ids') or ()))
        return jsonify(payload)

    @hiring_route('/cards/<int:card_id>', methods=('PATCH',))
    def hiring_edit_card(card_id, ctx):
        return jsonify({'card': public_card(service.edit_card(ctx, card_id, _body()), ctx)})

    @hiring_route('/cards/<int:card_id>/take', methods=('POST',))
    def hiring_take(card_id, ctx):
        return jsonify({'card': public_card(service.take(ctx, card_id), ctx)})

    @hiring_route('/cards/<int:card_id>/touches', methods=('POST',))
    def hiring_touch(card_id, ctx):
        card = service.touch(ctx, card_id, _body())
        with db._get_cursor() as cursor:
            touches = queries.card_touches(cursor, card_id)
        return jsonify({'card': public_card(card, ctx), 'touches': _json(touches)}), 201

    @hiring_route('/cards/<int:card_id>/touches/<int:touch_id>', methods=('PATCH',))
    def hiring_touch_edit(card_id, touch_id, ctx):
        """Дописать касание: комментарий, следующий контакт, исход звонка, которого телефония не видела."""
        card = service.edit_touch(ctx, card_id, touch_id, _body())
        with db._get_cursor() as cursor:
            touches = queries.card_touches(cursor, card_id)
        return jsonify({'card': public_card(card, ctx), 'touches': _json(touches)})

    @hiring_route('/cards/<int:card_id>/substage', methods=('PUT',))
    def hiring_substage(card_id, ctx):
        substage = _body().get('substage') or None
        return jsonify({'card': public_card(service.set_substage(ctx, card_id, substage), ctx)})

    @hiring_route('/cards/<int:card_id>/move', methods=('POST',))
    def hiring_move(card_id, ctx):
        body = _body()
        target = str(body.get('target') or '').strip()
        card = service.move(ctx, card_id, target, body)
        with db._get_cursor() as cursor:
            stages = queries.card_stages(cursor, card_id)
            motivation = queries.get_motivation(cursor, card_id)
        return jsonify({'card': public_card(card, ctx), 'stages': _json(stages), 'motivation': _json(motivation)})

    @hiring_route('/cards/<int:card_id>/reassign', methods=('POST',))
    def hiring_reassign(card_id, ctx):
        body = _body()
        card = service.reassign(ctx, card_id, body.get('recruiter_id'), body.get('comment'))
        with db._get_cursor() as cursor:
            assignments = queries.card_assignments(cursor, card_id)
        return jsonify({'card': public_card(card, ctx), 'assignments': _json(assignments)})

    @hiring_route('/cards/<int:card_id>/shifts/sync', methods=('POST',))
    def hiring_shifts_sync(card_id, ctx):
        """Пересчитать смены из статусов работы сейчас (джоба делает это сама раз в полчаса)."""
        card = service.sync_card_shifts(ctx, card_id)
        with db._get_cursor() as cursor:
            shifts = queries.card_shifts(cursor, card_id)
        return jsonify({'card': public_card(card, ctx), 'shifts': _json(shifts)})

    @hiring_route('/cards/<int:card_id>/motivation', methods=('PUT',))
    def hiring_motivation(card_id, ctx):
        body = _body()
        return jsonify({'motivation': _json(service.set_motivation(ctx, card_id, body.get('status'), body.get('reason')))})

    @hiring_route('/calls/<int:call_id>/recording')
    def hiring_call_recording(call_id, ctx):
        """Ссылка на запись разговора у Binotel (живёт около часа) — тому, кто видит карточку."""
        return jsonify(service.recording_url(ctx, call_id))

    # ─── Загрузка базы ───────────────────────────────────────────────────────

    @hiring_route('/upload', methods=('POST',))
    def hiring_upload(ctx):
        file_storage = request.files.get('file')
        if file_storage is None or not file_storage.filename:
            return _bad('Приложите файл Excel или CSV', 'HIRING_NEED_FILE')
        raw = file_storage.read()
        if len(raw) > 20 * 1024 * 1024:
            return _bad('Файл больше 20 МБ', 'HIRING_FILE_TOO_BIG', 413)
        batch = service.upload_contacts(ctx, raw, file_storage.filename, _int(request.form.get('source_id')))
        return jsonify({'batch': _json(batch)}), 201

    @hiring_route('/batches')
    def hiring_batches(ctx):
        _manage_only(ctx)
        with db._get_cursor() as cursor:
            rows = queries.list_batches(cursor)
        return jsonify({'items': _json(rows)})

    @hiring_route('/batches/<int:batch_id>')
    def hiring_batch(batch_id, ctx):
        _manage_only(ctx)
        with db._get_cursor() as cursor:
            batch = queries.get_batch(cursor, batch_id)
        if not batch:
            return _bad('Партия не найдена', 'HIRING_NOT_FOUND', 404)
        return jsonify({'batch': _json(batch)})

    # ─── Отчёты ──────────────────────────────────────────────────────────────

    def _report_filters(ctx):
        caps = access.capabilities(ctx)
        forced = None if caps['reports'] else ctx['user_id']
        return reports.parse_filters(request.args, forced_recruiter_id=forced)

    @hiring_route('/reports/dashboard')
    def hiring_dashboard(ctx):
        filters = _report_filters(ctx)
        with db._get_cursor() as cursor:
            data = reports.dashboard(cursor, filters)
        return jsonify(_json(data))

    @hiring_route('/reports/dashboard/export')
    def hiring_dashboard_export(ctx):
        filters = _report_filters(ctx)
        with db._get_cursor() as cursor:
            data = reports.dashboard(cursor, filters)
        content = reports.export_dashboard(_json(data), 'Воронка найма')
        name = 'Воронка найма %s.xlsx' % (filters.get('month') or '%s — %s' % (data['filters']['from'], data['filters']['to']))
        return _xlsx(content, name)

    @hiring_route('/reports/cards')
    def hiring_report_cards(ctx):
        caps = access.capabilities(ctx)
        filters = _report_filters(ctx)
        key = str(request.args.get('key') or '').strip()
        if not key:
            return _bad('Не указано, какую цифру раскрыть', 'HIRING_NEED_KEY')
        with db._get_cursor() as cursor:
            ids = reports.card_ids_for(cursor, filters, key)
            total, rows = queries.list_cards(cursor, caps, ctx['user_id'], {'ids': ids or [0]}, limit=500)
        return jsonify({'total': total, 'items': [public_card(card, ctx) for card in rows]})

    @hiring_route('/reports/motivation')
    def hiring_report_motivation(ctx):
        filters = _report_filters(ctx)
        with db._get_cursor() as cursor:
            rows = reports.motivation_report(cursor, filters)
        return jsonify({'month': filters.get('month'), 'items': _json(rows)})

    @hiring_route('/reports/motivation/export')
    def hiring_report_motivation_export(ctx):
        filters = _report_filters(ctx)
        with db._get_cursor() as cursor:
            rows = reports.motivation_report(cursor, filters)
        label = filters.get('month') or '%s — %s' % (filters['cohort_from'].date(), (filters['cohort_to'] - timedelta(days=1)).date())
        return _xlsx(reports.export_motivation(_json(rows), label), 'Мотивация рекрутеров %s.xlsx' % label)

    def _xlsx(content, name):
        return Response(content, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={
            'Content-Disposition': "attachment; filename*=UTF-8''%s" % quote(name),
            'Access-Control-Expose-Headers': 'Content-Disposition',
        })

    # ─── Настройки ───────────────────────────────────────────────────────────

    def _people_payload(cursor):
        """Выдачи плюс линия Binotel каждого (членство в линии «Удаленного КЦ»), без учёток линий."""
        rows = _json(queries.list_people(cursor))
        lines = _dial_list()
        departments = {}
        if lines is not None:
            try:
                departments = {row['department_id']: row['department_name'] for row in lines.list_departments()}
            except Exception:  # noqa: BLE001
                log.exception('hiring: отделы «Удаленного КЦ» не прочитаны')
        for row in rows:
            row['line'] = None
            if lines is None or not row.get('is_active'):
                continue
            try:
                member = lines.line_member(row['user_id'])
            except Exception:  # noqa: BLE001
                log.exception('hiring: линия сотрудника %s не прочитана', row['user_id'])
                member = None
            if member:
                row['line'] = {'department_id': member['department_id'],
                               'department_name': departments.get(member['department_id'], ''),
                               'internal_number': member['internal_number']}
        return rows

    @hiring_route('/people')
    def hiring_people(ctx):
        _manage_only(ctx)
        with db._get_cursor() as cursor:
            rows = _people_payload(cursor)
        return jsonify({'items': rows, 'lines_available': _dial_list() is not None})

    # ─── Линии «Удаленного КЦ» для рекрутеров (владелец 30.10.2026) ─────────
    # Рекрутера сажают на свободную линию Binotel отдела удалённого КЦ прямо здесь — тем же
    # сервисом, что в разделе «Удаленный КЦ» (посадка «чужого» сотрудника, seat_anyone): его
    # iCore Phone регистрируется на линии, и отклики прозваниваются кнопкой из карточки.

    def _dial_list_error(exc):
        status = int(getattr(exc, 'status', 0) or 0)
        if type(exc).__name__ == 'DialListError' and status:
            return _bad(str(exc), 'HIRING_LINE_ERROR', status)
        log.exception('hiring: сервис линий «Удаленного КЦ» упал')
        return _bad('Сервис линий «Удаленного КЦ» не ответил', 'HIRING_LINE_UNAVAILABLE', 502)

    def _recruiter_grant(cursor, user_id):
        return next((row for row in queries.list_people(cursor) if row['user_id'] == int(user_id) and row['is_active']), None)

    @hiring_route('/lines')
    def hiring_lines(ctx):
        """Отделы удалённого КЦ, а с ?department_id= — его линии Binotel и кто на них сидит."""
        _manage_only(ctx)
        lines = _dial_list()
        if lines is None:
            return _bad('Раздел «Удаленный КЦ» не подключён', 'HIRING_LINES_OFF', 503)
        department_id = _int(request.args.get('department_id'))
        try:
            departments = [{'department_id': row['department_id'], 'department_name': row['department_name']}
                           for row in lines.list_departments(profile='remote_cc')]
            payload = {'departments': departments, 'lines': [], 'sip_server': ''}
            if department_id:
                if department_id not in {row['department_id'] for row in departments}:
                    return _bad('Отдел не подключён к «Удаленному КЦ»', 'HIRING_NOT_FOUND', 404)
                payload['sip_server'] = lines.department_sip_server(department_id)
                payload['lines'] = [{
                    'internal_number': line['internal_number'], 'online': line['online'], 'status': line['status'],
                    'holder': ({'id': line['icore_user']['id'], 'name': line['icore_user']['name']}
                               if line.get('icore_user') else None),
                } for line in lines.list_lines(department_id)]
        except Exception as exc:  # noqa: BLE001
            return _dial_list_error(exc)
        return jsonify(_json(payload))

    @hiring_route('/people/<int:user_id>/line', methods=('POST', 'DELETE'))
    def hiring_people_line(user_id, ctx):
        _manage_only(ctx)
        lines = _dial_list()
        if lines is None:
            return _bad('Раздел «Удаленный КЦ» не подключён', 'HIRING_LINES_OFF', 503)
        with db._get_cursor() as cursor:
            grant = _recruiter_grant(cursor, user_id)
        if not grant:
            return _bad('Линию получает только действующий рекрутер (выдача в разделе)', 'HIRING_NOT_RECRUITER', 409)
        try:
            if request.method == 'DELETE':
                member = lines.line_member(user_id)
                if not member:
                    return _bad('У сотрудника нет линии', 'HIRING_NOT_FOUND', 404)
                lines.release_line(member['department_id'], user_id, changed_by=ctx['user_id'], seat_anyone=True)
                lines.save_user_setting(user_id, None, changed_by=ctx['user_id'])
            else:
                body = _body()
                department_id = _int(body.get('department_id'))
                number = str(body.get('internal_number') or '').strip()
                if not department_id or not number:
                    return _bad('Нужны отдел и внутренний номер линии', 'HIRING_BAD_LINE')
                lines.assign_line(department_id, user_id, number, changed_by=ctx['user_id'], seat_anyone=True)
                # Линия рекрутеру — обычная учётка телефона, не обзвон (владелец 30.10.2026): отдел
                # линии включает режим обзвона всем своим, персональное «выключено» сильнее отдела.
                lines.save_user_setting(user_id, False, changed_by=ctx['user_id'])
        except Exception as exc:  # noqa: BLE001
            return _dial_list_error(exc)
        with db._get_cursor() as cursor:
            rows = _people_payload(cursor)
        return jsonify({'items': rows, 'lines_available': True})

    @hiring_route('/people/search')
    def hiring_people_search(ctx):
        _manage_only(ctx)
        with db._get_cursor() as cursor:
            rows = queries.search_users(cursor, request.args.get('q') or '')
        return jsonify({'items': _json(rows)})

    @hiring_route('/people', methods=('POST',))
    def hiring_people_add(ctx):
        _manage_only(ctx)
        body = _body()
        user_id, kind = _int(body.get('user_id')), str(body.get('kind') or '').strip()
        if not user_id or kind not in rules.GRANT_KINDS:
            return _bad('Нужны сотрудник и вид выдачи', 'HIRING_BAD_GRANT')
        now = rules.now_almaty()
        with db._get_cursor() as cursor:
            if not queries.user_name(cursor, user_id):
                return _bad('Сотрудник не найден', 'HIRING_NOT_FOUND', 404)
            queries.upsert_person(cursor, user_id, kind, ctx['user_id'], now)
            rows = _people_payload(cursor)
        return jsonify({'items': rows, 'lines_available': _dial_list() is not None}), 201

    @hiring_route('/people/<int:user_id>', methods=('DELETE',))
    def hiring_people_remove(user_id, ctx):
        returned = service.deactivate_person(ctx, user_id)
        # Отключённый рекрутер освобождает и линию Binotel: иначе линия осталась бы занята
        # человеком, которого в воронке уже нет.
        lines = _dial_list()
        if lines is not None:
            try:
                member = lines.line_member(user_id)
                if member:
                    lines.release_line(member['department_id'], user_id, changed_by=ctx['user_id'], seat_anyone=True)
                    lines.save_user_setting(user_id, None, changed_by=ctx['user_id'])
            except Exception:  # noqa: BLE001
                log.exception('hiring: линия отключённого рекрутера %s не снята', user_id)
        with db._get_cursor() as cursor:
            rows = _people_payload(cursor)
        return jsonify({'items': rows, 'returned_to_pool': returned, 'lines_available': lines is not None})

    @hiring_route('/sources', methods=('POST',))
    def hiring_source_add(ctx):
        _manage_only(ctx)
        body = _body()
        name = str(body.get('name') or '').strip()[:120]
        kind = str(body.get('kind') or '').strip()
        if not name or kind not in (rules.SOURCE_CONTACTS, rules.SOURCE_RESPONSES):
            return _bad('Нужны название и вид источника', 'HIRING_BAD_SOURCE')
        code = str(body.get('code') or '').strip().lower()[:40] or ('custom_%d' % int(rules.now_almaty().timestamp()))
        with db._get_cursor() as cursor:
            if queries.source_by_code(cursor, code):
                return _bad('Источник с таким кодом уже есть', 'HIRING_DUPLICATE', 409)
            queries.create_source(cursor, code, name, kind, rules.now_almaty())
            rows = queries.list_sources(cursor)
        return jsonify({'items': _json(rows)}), 201

    @hiring_route('/sources/<int:source_id>', methods=('PATCH',))
    def hiring_source_edit(source_id, ctx):
        _manage_only(ctx)
        body = _body()
        fields = {}
        if 'name' in body:
            fields['name'] = str(body.get('name') or '').strip()[:120] or None
            if not fields['name']:
                return _bad('Название пустое', 'HIRING_BAD_SOURCE')
        if 'is_active' in body:
            fields['is_active'] = bool(body.get('is_active'))
        with db._get_cursor() as cursor:
            queries.update_source(cursor, source_id, **fields)
            rows = queries.list_sources(cursor)
        return jsonify({'items': _json(rows)})

    @hiring_route('/vacancies', methods=('POST',))
    def hiring_vacancy_add(ctx):
        _manage_only(ctx)
        body = _body()
        name = str(body.get('name') or '').strip()[:160]
        if not name:
            return _bad('Название вакансии пустое', 'HIRING_BAD_VACANCY')
        with db._get_cursor() as cursor:
            queries.create_vacancy(cursor, name, _int(body.get('department_id')), body.get('office_interview', True),
                                   ctx['user_id'], rules.now_almaty())
            rows = queries.list_vacancies(cursor)
        return jsonify({'items': _json(rows)}), 201

    @hiring_route('/vacancies/<int:vacancy_id>', methods=('PATCH',))
    def hiring_vacancy_edit(vacancy_id, ctx):
        _manage_only(ctx)
        body = _body()
        fields = {}
        if 'name' in body:
            fields['name'] = str(body.get('name') or '').strip()[:160]
            if not fields['name']:
                return _bad('Название вакансии пустое', 'HIRING_BAD_VACANCY')
        if 'department_id' in body:
            fields['department_id'] = _int(body.get('department_id'))
        if 'office_interview' in body:
            fields['office_interview'] = bool(body.get('office_interview'))
        if 'is_active' in body:
            fields['is_active'] = bool(body.get('is_active'))
        with db._get_cursor() as cursor:
            queries.update_vacancy(cursor, vacancy_id, rules.now_almaty(), **fields)
            rows = queries.list_vacancies(cursor)
        return jsonify({'items': _json(rows)})

    @hiring_route('/reasons', methods=('POST',))
    def hiring_reason_add(ctx):
        _manage_only(ctx)
        body = _body()
        kind = str(body.get('kind') or '').strip()
        name = str(body.get('name') or '').strip()[:160]
        if kind not in rules.REASON_KINDS or not name:
            return _bad('Нужны вид и название причины', 'HIRING_BAD_REASON')
        with db._get_cursor() as cursor:
            queries.create_reason(cursor, kind, name, bool(body.get('requires_comment')), rules.now_almaty())
            rows = queries.list_reasons(cursor)
        return jsonify({'items': _json(rows)}), 201

    @hiring_route('/reasons/<int:reason_id>', methods=('PATCH',))
    def hiring_reason_edit(reason_id, ctx):
        _manage_only(ctx)
        body = _body()
        fields = {}
        if 'name' in body:
            fields['name'] = str(body.get('name') or '').strip()[:160]
            if not fields['name']:
                return _bad('Название причины пустое', 'HIRING_BAD_REASON')
        if 'requires_comment' in body:
            fields['requires_comment'] = bool(body.get('requires_comment'))
        if 'is_active' in body:
            fields['is_active'] = bool(body.get('is_active'))
        with db._get_cursor() as cursor:
            queries.update_reason(cursor, reason_id, **fields)
            rows = queries.list_reasons(cursor)
        return jsonify({'items': _json(rows)})

    @hiring_route('/groups')
    def hiring_groups(ctx):
        caps = access.capabilities(ctx)
        with db._get_cursor() as cursor:
            rows = queries.list_groups(cursor, active_only=not caps['manage'])
        return jsonify({'items': _json(rows)})

    @hiring_route('/groups', methods=('POST',))
    def hiring_group_add(ctx):
        _manage_only(ctx)
        body = _body()
        name = str(body.get('name') or '').strip()[:120]
        start = _date(body.get('start_date'))
        if not name or not start:
            return _bad('Нужны название группы и дата старта', 'HIRING_BAD_GROUP')
        with db._get_cursor() as cursor:
            queries.create_group(cursor, name, start, _int(body.get('trainer_id')), _int(body.get('department_id')),
                                 _int(body.get('vacancy_id')), ctx['user_id'], rules.now_almaty())
            rows = queries.list_groups(cursor)
        return jsonify({'items': _json(rows)}), 201

    @hiring_route('/groups/<int:group_id>', methods=('PATCH',))
    def hiring_group_edit(group_id, ctx):
        _manage_only(ctx)
        body = _body()
        fields = {}
        if 'name' in body:
            fields['name'] = str(body.get('name') or '').strip()[:120]
            if not fields['name']:
                return _bad('Название группы пустое', 'HIRING_BAD_GROUP')
        if 'start_date' in body:
            fields['start_date'] = _date(body.get('start_date'))
            if not fields['start_date']:
                return _bad('Дата старта не разобрана', 'HIRING_BAD_GROUP')
        for key in ('trainer_id', 'department_id', 'vacancy_id'):
            if key in body:
                fields[key] = _int(body.get(key))
        if 'status' in body and body.get('status') in ('active', 'done'):
            fields['status'] = body['status']
        with db._get_cursor() as cursor:
            queries.update_group(cursor, group_id, rules.now_almaty(), **fields)
            rows = queries.list_groups(cursor)
        return jsonify({'items': _json(rows)})

    @hiring_route('/plans')
    def hiring_plans(ctx):
        _manage_only(ctx)
        month = str(request.args.get('month') or rules.month_of(rules.now_almaty()))
        try:
            start, _end = rules.month_bounds(month)
        except (TypeError, ValueError):
            return _bad('Месяц в виде ГГГГ-ММ', 'HIRING_BAD_MONTH')
        with db._get_cursor() as cursor:
            rows = queries.list_plans(cursor, start.date())
        return jsonify({'month': month, 'items': _json(rows)})

    @hiring_route('/plans', methods=('POST',))
    def hiring_plan_add(ctx):
        _manage_only(ctx)
        body = _body()
        try:
            start, _end = rules.month_bounds(str(body.get('month') or ''))
        except (TypeError, ValueError):
            return _bad('Месяц в виде ГГГГ-ММ', 'HIRING_BAD_MONTH')
        plan_fte = _number(body.get('plan_fte'))
        if plan_fte is None or plan_fte <= 0:
            return _bad('План в FTE должен быть больше нуля', 'HIRING_BAD_PLAN')
        with db._get_cursor() as cursor:
            queries.create_plan(cursor, start.date(), _int(body.get('vacancy_id')), _int(body.get('department_id')),
                                _int(body.get('recruiter_id')), plan_fte, _date(body.get('planned_date')),
                                str(body.get('comment') or '').strip()[:1000] or None, ctx['user_id'], rules.now_almaty())
            rows = queries.list_plans(cursor, start.date())
        return jsonify({'items': _json(rows)}), 201

    @hiring_route('/plans/<int:plan_id>', methods=('PATCH', 'DELETE'))
    def hiring_plan_edit(plan_id, ctx):
        _manage_only(ctx)
        if request.method == 'DELETE':
            with db._get_cursor() as cursor:
                queries.delete_plan(cursor, plan_id)
            return jsonify({'status': 'ok'})
        body = _body()
        fields = {}
        for key in ('vacancy_id', 'department_id', 'recruiter_id'):
            if key in body:
                fields[key] = _int(body.get(key))
        if 'plan_fte' in body:
            value = _number(body.get('plan_fte'))
            if value is None or value <= 0:
                return _bad('План в FTE должен быть больше нуля', 'HIRING_BAD_PLAN')
            fields['plan_fte'] = value
        if 'planned_date' in body:
            fields['planned_date'] = _date(body.get('planned_date'))
        if 'comment' in body:
            fields['comment'] = str(body.get('comment') or '').strip()[:1000] or None
        with db._get_cursor() as cursor:
            queries.update_plan(cursor, plan_id, rules.now_almaty(), **fields)
            cursor.execute("SELECT month FROM hiring_plans WHERE id = %s", (int(plan_id),))
            row = cursor.fetchone()
            rows = queries.list_plans(cursor, row[0]) if row else []
        return jsonify({'items': _json(rows)})

    def _olx_cabinet_catalog(cursor):
        """Кабинеты, которые знает робот OLX: реестр (код и название) плюс учётки из базы."""
        catalog = []
        seen = set()
        try:
            from olx_amo import cabinets as olx_cabinets
            for cabinet in olx_cabinets.CABINETS:
                catalog.append({'code': cabinet.code, 'title': cabinet.title or cabinet.code})
                seen.add(cabinet.code)
        except Exception:  # noqa: BLE001 — робот не развёрнут: список только из базы
            pass
        for code in queries.olx_cabinet_codes(cursor):
            if code not in seen:
                catalog.append({'code': code, 'title': code})
                seen.add(code)
        return catalog

    def _integrations_payload(cursor):
        rows = _json(queries.list_integrations(cursor))
        for row in rows:
            if row['code'] == 'olx':
                row['cabinets_available'] = _olx_cabinet_catalog(cursor)
        return rows

    @hiring_route('/integrations')
    def hiring_integrations(ctx):
        _manage_only(ctx)
        with db._get_cursor() as cursor:
            rows = _integrations_payload(cursor)
        return jsonify({'items': rows})

    @hiring_route('/integrations/olx/settings', methods=('PUT',))
    def hiring_olx_settings(ctx):
        """Кабинеты OLX «для найма»: чаты только этих кабинетов идут в «Отклики». Кабинеты лидов
        (таксопарки) к рекрутерам не попадают — владелец 30.10.2026."""
        _manage_only(ctx)
        body = _body()
        raw = body.get('cabinets')
        if not isinstance(raw, list):
            return _bad('Нужен список кодов кабинетов', 'HIRING_BAD_SETTINGS')
        wanted = []
        with db._get_cursor() as cursor:
            known = {item['code'] for item in _olx_cabinet_catalog(cursor)}
            for code in raw:
                code = str(code or '').strip()
                if code and code not in known:
                    return _bad('Робот OLX не знает кабинет «%s»' % code[:40], 'HIRING_BAD_SETTINGS')
                if code and code not in wanted:
                    wanted.append(code)
            queries.set_integration_settings(cursor, 'olx', {'cabinets': wanted}, rules.now_almaty())
            rows = _integrations_payload(cursor)
        return jsonify({'items': rows})

    @hiring_route('/integrations/<code>/run', methods=('POST',))
    def hiring_integration_run(code, ctx):
        _manage_only(ctx)
        if code == 'olx':
            received = service.pull_olx()
        elif code == 'hh':
            received = service.pull_hh()
        else:
            return _bad('Такой интеграции нет', 'HIRING_NOT_FOUND', 404)
        with db._get_cursor() as cursor:
            rows = queries.list_integrations(cursor)
        return jsonify({'received': received, 'items': _json(rows)})

    @hiring_route('/notifications/seen', methods=('POST',))
    def hiring_notifications_seen(ctx):
        with db._get_cursor() as cursor:
            cleared = queries.mark_all_seen(cursor, ctx['user_id'], rules.now_almaty())
        return jsonify({'cleared': cleared})

    return bp


def _date(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _number(value):
    try:
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


# ─── Внешний вход откликов ───────────────────────────────────────────────────

def build_hiring_external_blueprint(*, db, service, partner_auth, partner_scopes, partner_journal, rate_limiter):
    """POST /api/hiring/external/responses — отклики с площадок по ключу партнёра (область
    «Отклики в воронку найма»). Тело: один отклик или {"items": [...]} до EXTERNAL_MAX_ITEMS.
    Поля отклика: source (код источника: kaspi, target, …), full_name, phone, city, vacancy,
    campaign, ref, received_at, comment."""
    bp = Blueprint('hiring_external', __name__, url_prefix='/api/hiring/external')
    scope = partner_scopes.HIRING_LEADS

    @bp.route('/responses', methods=['POST', 'OPTIONS'])
    def external_responses():
        if request.method == 'OPTIONS':
            return '', 204
        presented = partner_auth.presented_from(request.headers)
        status, key = partner_auth.lookup(db, presented, scope=scope)
        if status == partner_auth.NOT_READY:
            return _bad('Приём откликов не настроен', 'HIRING_API_NOT_READY', 503)
        if status == partner_auth.ERROR:
            return _bad('Ключ не проверен — попробуйте позже', 'HIRING_API_UNAVAILABLE', 503)
        if status in (partner_auth.UNKNOWN, partner_auth.REVOKED):
            return _bad('Ключ не принят', 'HIRING_API_UNAUTHORIZED', 401)
        if status == partner_auth.NO_SCOPE:
            partner_journal.record(db, key, scope=scope, endpoint='POST /api/hiring/external/responses',
                                   outcome='forbidden', summary='нет области')
            return _bad('У ключа нет области «Отклики в воронку найма»', 'HIRING_API_FORBIDDEN', 403)
        allowed, retry_after = rate_limiter.allow(key['id'])
        if not allowed:
            response = _bad('Слишком часто — подождите', 'HIRING_API_RATE_LIMITED', 429)
            response[0].headers['Retry-After'] = str(int(retry_after or 1))
            return response
        if (request.content_length or 0) > EXTERNAL_MAX_BODY:
            return _bad('Тело запроса больше 256 КБ', 'HIRING_API_TOO_BIG', 413)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _bad('Ожидается JSON-объект', 'HIRING_API_BAD_REQUEST')
        items = body.get('items') if isinstance(body.get('items'), list) else [body]
        if not items or len(items) > EXTERNAL_MAX_ITEMS:
            return _bad('От 1 до %d откликов за запрос' % EXTERNAL_MAX_ITEMS, 'HIRING_API_BAD_REQUEST')
        source_code = str(body.get('source') or (items[0].get('source') if isinstance(items[0], dict) else '') or '').strip().lower()
        if not source_code:
            return _bad('Укажите source — код источника (kaspi, target, …)', 'HIRING_API_BAD_REQUEST')
        try:
            results = service.external_intake(items, source_code=source_code, actor_label='Партнёр: %s' % (key.get('name') or key['id']))
        except HiringError as exc:
            partner_journal.record(db, key, scope=scope, endpoint='POST /api/hiring/external/responses',
                                   outcome='rejected', summary=exc.message[:200])
            return _bad(exc.message, exc.code, exc.status)
        created = sum(1 for row in results if row['status'] == 'created')
        partner_journal.record(db, key, scope=scope, endpoint='POST /api/hiring/external/responses', outcome='ok',
                               summary='%s: %d новых из %d' % (source_code, created, len(results)),
                               details={'source': source_code, 'created': created, 'total': len(results)})
        # Партнёру — только «принят / не телефон»: чем кончился приём внутри (новая карточка, повтор по
        # открытой, телефон действующего сотрудника) и номера карточек — наши персональные данные,
        # по ним внешняя сторона перебором узнавала бы, кто у нас сотрудник и кто в найме.
        public = [{'status': 'accepted' if row['status'] in ('created', 'repeat', 'employee') else 'invalid',
                   'phone': row.get('phone')} for row in results]
        return jsonify({'results': public, 'accepted': sum(1 for row in public if row['status'] == 'accepted')})

    return bp
