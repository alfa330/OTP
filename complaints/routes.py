# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Жалобы» (Flask Blueprint /api/complaints).

Устроен как crm/routes.py: фабрика получает зависимости аргументами (обратный
импорт из bot_schedule2 был бы циклом), каждый роут проходит общий каркас —
preflight, авторизация, контекст доступа, гейт раздела и QR, — а ошибки
отдаются {"error": "..."} с понятным текстом по-русски.

Разделение обязанностей:
    catalog.py  — справочники и правила ТЗ (чистые)
    access.py   — кто что может (чистые)
    queries.py  — SQL
    service.py  — где база встречается с Telegram
    routes.py   — разбор запроса и коды ответов
"""

import json
import logging
from datetime import datetime
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from crm import transport

from . import access, catalog, queries, report, schema, service

ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
PAGE_SIZE = 40


def build_complaints_blueprint(*, db, require_api_key, build_cors_preflight_response,
                               resolve_requester, sensitive_access_granted,
                               excel_text_warning=None):
    """Собирает Blueprint раздела.

    sensitive_access_granted обязателен и без значения по умолчанию — по той
    же причине, что у «Обращений»: забытая зависимость должна уронить сборку
    блюпринта, а не тихо открыть раздел с данными водителей всем.
    """
    bp = Blueprint('complaints', __name__, url_prefix='/api/complaints')

    def route(rule, methods=('GET',), analytics=False, manage=False):
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
                    with db._get_cursor() as cursor:
                        ctx = queries.load_access_context(cursor, requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    # К API пускает can_use, а не вход в раздел: оператор
                    # работает со своими жалобами из «Обращений» теми же
                    # запросами. Разбор, аналитика и настройки закрыты ему
                    # своими правилами ниже и в самих обработчиках.
                    if not access.can_use(ctx):
                        return jsonify({"error": "Жалобы вам не открыты",
                                        "code": "COMPLAINTS_SECTION_CLOSED"}), 403
                    if (access.requires_sensitive_qr(ctx)
                            and not sensitive_access_granted(ctx['user_id'])):
                        return jsonify({"error": "Раздел «Жалобы» откроется после "
                                                 "QR-подтверждения доступа",
                                        "code": "SENSITIVE_ACCESS_REQUIRED"}), 403
                    if analytics and not access.can_view_analytics(ctx):
                        return jsonify({"error": "Аналитика вам не открыта",
                                        "code": "COMPLAINTS_FORBIDDEN"}), 403
                    if manage and not access.can_manage_settings(ctx):
                        return jsonify({"error": "Настраивать группу жалоб вам не разрешено",
                                        "code": "COMPLAINTS_FORBIDDEN"}), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except service.ComplaintError as exc:
                    payload = {"error": exc.message}
                    if exc.code:
                        payload['code'] = exc.code
                    return jsonify(payload), exc.status
                except Exception as exc:  # noqa: BLE001
                    logging.exception('complaints: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Жалобы»",
                                    "detail": str(exc)[:200]}), 500
            return wrapper
        return decorator

    def _payload():
        if request.files or request.form:
            return dict(request.form)
        return request.get_json(silent=True) or {}

    def _attachment():
        item = request.files.get('attachment')
        if item is None or not str(item.filename or '').strip():
            return None, None
        item.stream.seek(0, 2)
        size = item.stream.tell()
        item.stream.seek(0)
        if size == 0:
            return None, None
        if size > ATTACHMENT_MAX_BYTES:
            return None, 'Файл больше 20 МБ — Telegram его не примет'
        return {'filename': item.filename, 'stream': item.stream,
                'mimetype': item.mimetype}, None

    def _load(cursor, complaint_id, ctx):
        complaint = queries.get_complaint(cursor, complaint_id, ctx['user_id'])
        if not complaint:
            raise service.ComplaintError('Жалоба не найдена', 404)
        if not access.can_view(ctx, complaint):
            raise service.ComplaintError('Жалоба вне вашего доступа', 403)
        return complaint

    def _permissions(ctx, complaint):
        return {
            'can_handle': access.can_handle(ctx, complaint),
            'can_review': access.can_review(ctx, complaint),
            'can_set_employee': access.can_set_employee(ctx, complaint),
            'can_record_work': access.can_record_work(ctx, complaint),
            'can_see_internal': access.can_see_internal(ctx, complaint),
            'can_write': access.can_write_to_group(ctx, complaint),
            'can_delete': access.can_delete(ctx),
        }

    # ── Сводка ───────────────────────────────────────────────────────────
    @route('/ping')
    def complaints_ping(ctx):
        with db._get_cursor() as cursor:
            ready = schema.schema_is_ready(cursor)
            payload = {'ok': True, 'schema_ready': ready,
                       'capabilities': access.capabilities(ctx), 'user_id': ctx['user_id']}
            if ready:
                payload['counters'] = queries.counters(cursor, ctx)
        return jsonify(payload)

    @route('/meta')
    def complaints_meta(ctx):
        """Справочники одним запросом: цели и причины, подразделения, офисы,
        парки и готовность группы. Всё это нужно форме сразу при открытии."""
        from crm import queries as crm_queries

        with db._get_cursor() as cursor:
            spaces = crm_queries.section_space_ids(cursor)
            settings = queries.settings(cursor)
            departments = queries.departments(cursor)
            offices = queries.offices(cursor, spaces)
            parks = queries.parks(cursor, spaces)
        meta = catalog.public_meta()
        meta.update({
            'departments': departments,
            'offices': offices,
            'parks': parks,
            # Название группы видят все (оператор обязан знать, кого
            # побеспокоит), номер чата — только тот, кто её выбирает.
            'group': {'ready': bool(settings.get('chat_id')),
                      'chat_title': settings.get('chat_title')},
            'capabilities': access.capabilities(ctx),
        })
        return jsonify(meta)

    @route('/employees')
    def complaints_employees(ctx):
        department_id = _int_or_none(request.args.get('department_id'))
        if not department_id:
            return jsonify({"error": "Укажите подразделение"}), 400
        with db._get_cursor() as cursor:
            department = queries.department_by_id(cursor, department_id)
            if not department or department['code'] not in catalog.HANDLER_DEPARTMENT_CODES:
                return jsonify({"error": "Подразделение не найдено"}), 404
            items = queries.employees(cursor, department_id)
        return jsonify({'items': items})

    # ── Водитель по ссылке на аккаунт ────────────────────────────────────
    @route('/driver-lookup', methods=('POST',))
    def complaints_driver_lookup(ctx):
        """Ссылка на аккаунт водителя (или его ID) → ФИО, телефон и ВУ из CRM.

        Тот же поиск, что у «Реестра посылок» (parcels/drivers.py), — как там,
        чтобы данные водителя не набирали руками. Сама ссылка в жалобу и в
        группу не уходит (владелец, 29.09.2026): форма берёт из ответа только
        поля жалобы, поэтому и отдаём только их, без снимка CRM целиком.
        """
        if not access.can_create(ctx):
            return jsonify({"error": "Заводить жалобы вам не разрешено"}), 403
        from parcels import drivers

        data = _payload()
        try:
            found = drivers.lookup(data.get('link') or data.get('account_id'))
        except drivers.DriverLookupError as exc:
            return jsonify({"error": exc.message, "code": exc.code}), exc.status
        return jsonify({'driver': {key: found.get(key) for key in ('name', 'phone', 'license')}})

    # ── Лента ────────────────────────────────────────────────────────────
    @route('/complaints')
    def complaints_list(ctx):
        args = request.args
        with db._get_cursor() as cursor:
            items, has_more = queries.list_complaints(
                cursor, ctx,
                segment=args.get('segment') or queries.SEGMENT_ALL,
                status=args.get('status') or None,
                target=args.get('target') or None,
                search=(args.get('q') or '').strip() or None,
                limit=_int_or_none(args.get('limit')) or PAGE_SIZE,
                offset=_int_or_none(args.get('offset')) or 0,
                # Порядок ленты «Обращений»: там свои жалобы стоят в одном
                # списке с обращениями, и порядок у двух частей обязан совпадать.
                unread_first=args.get('unread_first') == '1',
            )
        return jsonify({'items': items, 'has_more': has_more,
                        'capabilities': access.capabilities(ctx)})

    @route('/complaints', methods=('POST',))
    def complaints_create(ctx):
        """Новая жалоба. Всё, что можно проверить без базы, проверяет каталог;
        отдел, сотрудника, офис и парк сверяем здесь — присланному названию
        верить нельзя, в жалобу ложится имя из справочника."""
        if not access.can_create(ctx):
            return jsonify({"error": "Заводить жалобы вам не разрешено"}), 403
        clean, errors = catalog.clean_complaint(_payload())
        if errors:
            return jsonify({"error": next(iter(errors.values())), "fields": errors}), 400
        spec = catalog.target(clean['target'])

        from crm import queries as crm_queries

        with db._get_cursor() as cursor:
            unit_kind = unit_name = target_department_id = None
            unit_id = clean['unit_id']
            if spec['unit'] == catalog.UNIT_DEPARTMENT and unit_id:
                department = queries.department_by_id(cursor, unit_id)
                if not department or department['code'] not in catalog.CALL_CENTER_DEPARTMENT_CODES:
                    return jsonify({"error": "Выберите подразделение колл-центра",
                                    "fields": {'unit_id': 'Нет такого подразделения'}}), 400
                unit_kind, unit_name = catalog.UNIT_DEPARTMENT, catalog.department_label(
                    department['code'], department['name'])
                target_department_id = department['id']
            elif spec['unit'] == catalog.UNIT_OFFICE and unit_id:
                office = queries.office_by_id(cursor, unit_id, crm_queries.section_space_ids(cursor))
                if not office:
                    return jsonify({"error": "Офис не найден",
                                    "fields": {'unit_id': 'Нет такого офиса'}}), 400
                unit_kind, unit_name = catalog.UNIT_OFFICE, office['name']
            elif spec['unit'] == catalog.UNIT_PARK and unit_id:
                park = queries.park_by_id(cursor, unit_id, crm_queries.section_space_ids(cursor))
                if not park:
                    return jsonify({"error": "Таксопарк не найден",
                                    "fields": {'unit_id': 'Нет такого парка'}}), 400
                unit_kind, unit_name = catalog.UNIT_PARK, park['name']
            if not unit_kind:
                unit_id = None
            if clean['target'] == catalog.TARGET_FRONT_OFFICE:
                front = [d for d in queries.departments(cursor)
                         if d['code'] == catalog.FRONT_OFFICE_DEPARTMENT_CODE]
                target_department_id = front[0]['id'] if front else None

            employee_name = responsible_id = None
            employee_id = clean['employee_id']
            if employee_id:
                person, department = service.resolve_employee(cursor, clean['target'], employee_id)
                if int(person['id']) == int(ctx['user_id']):
                    return jsonify({"error": "Жалобу на себя завести нельзя"}), 400
                if (clean['target'] == catalog.TARGET_CALL_CENTER
                        and target_department_id
                        and int(person['department_id']) != int(target_department_id)):
                    return jsonify({"error": "Сотрудник не из выбранного подразделения",
                                    "fields": {'employee_id': 'Сотрудник из другого подразделения'}}), 400
                employee_name = person['name']
                responsible_id = person.get('responsible_id')
                target_department_id = person.get('department_id') or target_department_id

            work_state = catalog.work_state_for(clean['target'], employee_id, False)
            status = 'closed' if catalog.is_closed(
                requires_processing=clean['requires_processing'], result_code=None,
                work_state=work_state, review_state=clean['review_state']) else 'open'
            complaint_id = queries.create_complaint(cursor, {
                'target': clean['target'], 'reason': clean['reason'],
                'target_department_id': target_department_id,
                'unit_kind': unit_kind, 'unit_id': unit_id, 'unit_name': unit_name,
                'employee_id': employee_id, 'employee_name': employee_name,
                'employee_source': 'operator' if employee_id else None,
                'employee_set_by': ctx['user_id'] if employee_id else None,
                'employee_set_by_name': ctx.get('name') if employee_id else None,
                'responsible_id': responsible_id,
                'driver_name': clean['driver_name'], 'driver_phone': clean['driver_phone'],
                'driver_ref': clean['driver_ref'], 'city': clean['city'],
                'description': clean['description'],
                'event_at': clean['event_at'],
                'event_time_known': clean['event_time_known'],
                'requires_processing': clean['requires_processing'],
                'review_state': clean['review_state'],
                'status': status, 'work_state': work_state,
                'created_by': ctx['user_id'], 'created_by_name': ctx.get('name'),
                'creator_department_id': ctx.get('department_id'),
                'delivery_status': 'pending' if clean['requires_processing'] else 'none',
            })
            queries.add_event(cursor, complaint_id=complaint_id, kind='created',
                              actor_user_id=ctx['user_id'], actor_name=ctx.get('name'),
                              payload={'target': clean['target'], 'reason': clean['reason'],
                                       'requires_processing': clean['requires_processing'],
                                       'review': bool(clean['review_state']),
                                       'employee': employee_name})
            if status == 'closed':
                cursor.execute(
                    "UPDATE complaints SET closed_at = %s WHERE id = %%s" % queries._NOW,
                    (complaint_id,))

        delivered, delivery_error = True, None
        if clean['requires_processing']:
            delivered, delivery_error = service.deliver(db, complaint_id)
        with db._get_cursor() as cursor:
            item = queries.public_item(ctx, queries.get_complaint(cursor, complaint_id,
                                                                  ctx['user_id']))
        return jsonify({'item': item, 'delivered': delivered,
                        'delivery_error': None if delivered else delivery_error}), 201

    # ── Карточка ─────────────────────────────────────────────────────────
    @route('/complaints/<int:complaint_id>')
    def complaints_show(complaint_id, ctx):
        """Карточка: жалоба, переписка по правам, журнал работы. Открытие
        автором гасит его «непрочитано» — ответ снимается прочтением."""
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
            permissions = _permissions(ctx, complaint)
            messages = queries.list_messages(cursor, complaint_id,
                                             include_internal=permissions['can_see_internal'])
            work = queries.work_log(cursor, complaint_id) if permissions['can_handle'] else []
            if queries.mark_seen_by_author(cursor, complaint_id, ctx['user_id']):
                complaint.update(unread=False, unread_kind=None, unread_count=0)
        # Внутреннее — только разбирающему. Оператору из работы с сотрудником
        # не уходит ничего, кроме того, что она проведена: «внутренние детали
        # обратной связи и обучения не должны уходить оператору». Правило одно
        # на все ответы раздела — queries.public_item.
        return jsonify({'item': queries.public_item(ctx, complaint), 'messages': messages,
                        'work': work, 'permissions': permissions})

    @route('/complaints/<int:complaint_id>/events')
    def complaints_events(complaint_id, ctx):
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
            if not access.can_handle(ctx, complaint):
                return jsonify({"error": "История доступна тем, кто разбирает жалобу"}), 403
            return jsonify({'events': queries.list_events(cursor, complaint_id)})

    @route('/complaints/<int:complaint_id>/messages', methods=('POST',))
    def complaints_write(complaint_id, ctx):
        data = _payload()
        body = str(data.get('body') or '').strip()
        attachment, attach_error = _attachment()
        if attach_error:
            return jsonify({"error": attach_error}), 400
        if not body and attachment is None:
            return jsonify({"error": "Пустое сообщение"}), 400
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
            if not access.can_write_to_group(ctx, complaint):
                return jsonify({"error": "Написать в группу по этой жалобе нельзя"}), 403
        ok, error = service.post_operator_message(
            db, complaint_id, body or '📎 Вложение', author_user_id=ctx['user_id'],
            author_name=ctx.get('name'), attachment=attachment)
        if not ok:
            return jsonify({"error": "Сообщение не ушло в Telegram: %s" % error}), 502
        with db._get_cursor() as cursor:
            complaint = queries.get_complaint(cursor, complaint_id, ctx['user_id'])
            messages = queries.list_messages(
                cursor, complaint_id, include_internal=access.can_see_internal(ctx, complaint))
        return jsonify({'item': queries.public_item(ctx, complaint), 'messages': messages})

    @route('/complaints/<int:complaint_id>/employee', methods=('POST',))
    def complaints_employee(complaint_id, ctx):
        data = _payload()
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
        if not access.can_set_employee(ctx, complaint):
            return jsonify({"error": "Определить сотрудника вам нельзя"}), 403
        item = service.set_employee(db, complaint_id, _int_or_none(data.get('employee_id')),
                                    ctx=ctx)
        return jsonify({'item': item})

    @route('/complaints/<int:complaint_id>/result', methods=('POST',))
    def complaints_result(complaint_id, ctx):
        data = _payload()
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
        if not access.can_handle(ctx, complaint):
            return jsonify({"error": "Итог проверки ставит тот, кто разбирает жалобу"}), 403
        item = service.set_result(db, complaint_id, str(data.get('result_code') or ''),
                                  data.get('result_note'), ctx=ctx)
        return jsonify({'item': item})

    @route('/complaints/<int:complaint_id>/review', methods=('POST',))
    def complaints_review(complaint_id, ctx):
        """Решение по жалобе на проверке (Яндекс): «Отправить в группу» или
        «Решено» с итогом. Решает тот, кто разбирает жалобу, — любой
        супервайзер отдела оператора, глава отдела, админ."""
        data = _payload()
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
        if not access.can_handle(ctx, complaint):
            return jsonify({"error": "Проверяет жалобу супервайзер отдела"}), 403
        if complaint.get('review_state') != catalog.REVIEW_PENDING:
            return jsonify({"error": "Жалоба уже не ждёт проверки — обновите карточку"}), 409
        decision = str(data.get('decision') or '')
        delivery_error = None
        if decision == 'send':
            item, delivery_error = service.review_send(db, complaint_id, ctx=ctx)
        elif decision == 'resolve':
            item = service.review_resolve(db, complaint_id, data.get('note'), ctx=ctx)
        else:
            return jsonify({"error": "Выберите: в группу или «Решено»"}), 400
        return jsonify({'item': queries.public_item(ctx, item),
                        'permissions': _permissions(ctx, item),
                        'delivered': delivery_error is None,
                        'delivery_error': delivery_error})

    @route('/complaints/<int:complaint_id>/shifts')
    def complaints_employee_shifts(complaint_id, ctx):
        """Две ближайшие смены сотрудника жалобы — для окна «Назначить
        тренинг»: тренинг назначают на время, когда человек на работе."""
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
            if not access.can_record_work(ctx, complaint):
                return jsonify({"error": "Смены сотрудника вам не открыты"}), 403
            if not complaint.get('employee_id'):
                return jsonify({'items': []})
            items = queries.upcoming_shifts(cursor, complaint['employee_id'], datetime.now())
        return jsonify({'items': items})

    @route('/complaints/<int:complaint_id>/work', methods=('POST',))
    def complaints_work(complaint_id, ctx):
        data = _payload()
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
        if not access.can_record_work(ctx, complaint):
            return jsonify({"error": "Записать работу с сотрудником вам нельзя"}), 403
        item = service.record_work(
            db, complaint_id, action=data.get('action'), comment=data.get('comment'),
            training=_json_dict(data.get('training')), plan=_json_dict(data.get('plan')),
            ctx=ctx)
        with db._get_cursor() as cursor:
            work = queries.work_log(cursor, complaint_id)
        return jsonify({'item': item, 'work': work})

    @route('/complaints/<int:complaint_id>/resend', methods=('POST',))
    def complaints_resend(complaint_id, ctx):
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
        if not (access.can_handle(ctx, complaint)
                or int(complaint.get('created_by') or 0) == int(ctx['user_id'])):
            return jsonify({"error": "Недостаточно прав"}), 403
        if complaint['delivery_status'] == 'sent':
            return jsonify({"error": "Жалоба уже в группе"}), 409
        if not complaint['requires_processing']:
            if complaint.get('review_state') == catalog.REVIEW_PENDING:
                return jsonify({"error": "Жалоба ждёт проверки: в группу её отправит "
                                         "супервайзер"}), 409
            return jsonify({"error": "Эта жалоба в группу не уходит"}), 409
        ok, error = service.deliver(db, complaint_id)
        if not ok:
            return jsonify({"error": error}), 502
        with db._get_cursor() as cursor:
            item = queries.get_complaint(cursor, complaint_id, ctx['user_id'])
        return jsonify({'item': queries.public_item(ctx, item)})

    @route('/complaints/<int:complaint_id>/attachments/<int:message_id>')
    def complaints_attachment(complaint_id, message_id, ctx):
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
            kinds = (queries.HANDLER_VISIBLE_KINDS if access.can_see_internal(ctx, complaint)
                     else queries.OPERATOR_VISIBLE_KINDS)
            found = queries.message_attachment(cursor, complaint_id, message_id, kinds)
        if not found:
            return jsonify({"error": "Вложение не найдено"}), 404
        content, error = transport.fetch_file(found['file_id'])
        if content is None:
            return jsonify({"error": "Telegram не отдал файл: %s" % error}), 502
        return send_file(BytesIO(content), mimetype=found.get('mime') or 'application/octet-stream',
                         download_name=found.get('name') or ('attachment-%s' % message_id),
                         as_attachment=not str(found.get('mime') or '').startswith('image/'))

    @route('/complaints/<int:complaint_id>', methods=('DELETE',))
    def complaints_delete(complaint_id, ctx):
        with db._get_cursor() as cursor:
            complaint = _load(cursor, complaint_id, ctx)
            if not access.can_delete(ctx):
                return jsonify({"error": "Удалять жалобы может только администратор"}), 403
            cursor.execute('DELETE FROM complaints WHERE id = %s', (complaint_id,))
        # Журнал жалобы уезжает каскадом — след остаётся только в логе. Запись
        # в «Тренингах» не трогаем: занятие было проведено, и оно в часах.
        logging.info('complaints: жалоба №%s удалена (%s, %s) — %s [id %s]',
                     complaint_id, complaint.get('target'), complaint.get('status'),
                     ctx.get('name'), ctx.get('user_id'))
        return jsonify({'status': 'deleted', 'id': complaint_id})

    # ── Аналитика и выгрузка ─────────────────────────────────────────────
    def _filters():
        args = request.args
        default_from, default_to = queries.default_period()
        filters = {key: (args.get(key) or '').strip() or None for key in (
            'date_from', 'date_to', 'city', 'target', 'reason', 'unit', 'employee_id',
            'status', 'result', 'confirmed', 'feedback', 'training')}
        filters['date_from'] = filters['date_from'] or default_from
        filters['date_to'] = filters['date_to'] or default_to
        return filters

    @route('/analytics', analytics=True)
    def complaints_analytics(ctx):
        filters = _filters()
        with db._get_cursor() as cursor:
            data = queries.analytics(cursor, ctx, filters)
        data['filters'] = filters
        return jsonify(data)

    @route('/export', analytics=True)
    def complaints_export(ctx):
        filters = _filters()
        with db._get_cursor() as cursor:
            items = queries.export_rows(cursor, ctx, filters)
        employee_name = None
        if filters.get('employee_id'):
            with db._get_cursor() as cursor:
                cursor.execute('SELECT name FROM users WHERE id = %s',
                               (int(filters['employee_id']),))
                row = cursor.fetchone()
                employee_name = row[0] if row else None
        stream = report.build_workbook(items, filters_note=_filters_note(filters, employee_name),
                                       generated_by=ctx.get('name'),
                                       text_warning_patch=excel_text_warning)
        logging.info('complaints: выгрузка %d строк за %s..%s — %s', len(items),
                     filters['date_from'], filters['date_to'], ctx.get('name'))
        return send_file(stream, mimetype=report.XLSX_MIME, as_attachment=True,
                         download_name=report.filename(filters['date_from'], filters['date_to']))

    def _filters_note(filters, employee_name=None):
        parts = ['период %s — %s' % (filters['date_from'], filters['date_to'])]
        if filters.get('target'):
            parts.append('на кого: %s' % catalog.target_title(filters['target']))
        if filters.get('reason') and filters.get('target'):
            parts.append('причина: %s' % catalog.reason_title(filters['target'], filters['reason']))
        for key, label in (('city', 'город'), ('unit', 'подразделение')):
            if filters.get(key):
                parts.append('%s: %s' % (label, filters[key]))
        if filters.get('status'):
            parts.append('статус: %s' % {'closed': 'отработанные', 'recorded': 'зафиксированные'}
                         .get(filters['status'], 'в работе'))
        if filters.get('result'):
            parts.append('итог: %s' % catalog.result_title(filters['result']))
        for key, yes, no in (('confirmed', 'подтверждённые', 'неподтверждённые'),
                             ('feedback', 'ОС проведена', 'ОС не проведена'),
                             ('training', 'тренинг проведён', 'тренинг не проведён')):
            if filters.get(key) in ('yes', 'no'):
                parts.append(yes if filters[key] == 'yes' else no)
        if filters.get('employee_id'):
            # Именем, а не номером: лист «Контекст» читают через месяц, и
            # «id 5123» там ничего не объясняет.
            parts.append('сотрудник: %s' % (employee_name or 'id %s' % filters['employee_id']))
        return '; '.join(parts)

    # ── Настройки группы ─────────────────────────────────────────────────
    @route('/settings', manage=True)
    def complaints_settings(ctx):
        from crm import queries as crm_queries

        with db._get_cursor() as cursor:
            settings = queries.settings(cursor)
            chats = crm_queries.bot_chats(cursor)
        return jsonify({'settings': settings, 'chats': chats})

    @route('/settings', methods=('PUT',), manage=True)
    def complaints_settings_save(ctx):
        from crm import queries as crm_queries
        from wiki import offices as wiki_offices

        data = _payload()
        chat_id = _int_or_none(data.get('chat_id'))
        names, problem = wiki_offices.clean_telegram_usernames(data.get('mention_usernames'))
        if problem:
            return jsonify({"error": problem}), 400
        with db._get_cursor() as cursor:
            chat_title = None
            if chat_id is not None:
                known = {chat['chat_id']: chat for chat in crm_queries.bot_chats(cursor)}
                chat = known.get(chat_id)
                if not chat:
                    return jsonify({"error": "Бот не состоит в этой группе"}), 400
                chat_title = chat['title']
            queries.save_settings(cursor, chat_id=chat_id, chat_title=chat_title,
                                  mention_usernames=wiki_offices.join_telegram_usernames(names),
                                  actor_id=ctx['user_id'], actor_name=ctx.get('name'))
            settings = queries.settings(cursor)
        return jsonify({'settings': settings})

    return bp


def _int_or_none(value):
    if value in (None, '', 'null'):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_dict(value):
    """Вложенный объект тела запроса. Из формы (multipart) он приезжает
    строкой JSON — разбираем; всё, что не объект, — «не прислали»."""
    if isinstance(value, str):
        try:
            value = json.loads(value or '{}')
        except ValueError:
            return None
    return value if isinstance(value, dict) else None
