"""HTTP-ручки раздела «Почта» (Blueprint /api/mail).

Blueprint собирается фабрикой и получает зависимости аргументами — обратный импорт
bot_schedule2 был бы циклом (так же устроены parcels/, payments/, hr_office/).

Здесь только разбор запроса, порядок шагов и коды ответов. Что можно — access.py,
какие данные годятся — rules.py, SQL — queries.py.
"""

import json
import logging
from datetime import timedelta
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import access, files, queries, report, rules, schema

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PAGE_LIMIT = 200
# Насколько назад можно поставить дату шага: занесли передачу курьеру задним числом —
# нормально, на полгода назад — почти наверняка опечатка в годе.
MAX_BACKDATE_DAYS = 60
MAX_AHEAD_DAYS = 60
# Время передачи курьеру не может быть в будущем; запас — на разницу часов у людей.
CLOCK_SKEW = timedelta(minutes=10)
# К какому шагу какой вид файла прикладывается.
ACTION_FILE_KINDS = {
    'courier_request': 'waybill',
    'hand_over': 'handover',
    'deliver': 'delivery',
    'reconcile': 'problem',
}


def build_mail_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                         excel_text_warning=None, gcs=None):
    gcs = gcs or {}
    bp = Blueprint('mail', __name__, url_prefix='/api/mail')
    schema.ensure(db)
    holidays = rules.kz_holidays()

    def route(rule, methods=('GET',)):
        def decorator(handler):
            @bp.route(rule, methods=list(methods) + ['OPTIONS'], endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({'error': message}), status
                    with db._get_cursor() as cursor:
                        ctx = queries.load_context(cursor, requester_id)
                    # Спрятанный пункт меню — не доступ: раздел открывается и прямым адресом.
                    if not ctx or not access.can_open(ctx):
                        return jsonify({'error': 'Раздел «Почта» вам не открыт', 'code': 'MAIL_CLOSED'}), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except rules.MailError as exc:
                    payload = {'error': exc.message, 'code': exc.code}
                    if exc.fields:
                        payload['fields'] = exc.fields
                    payload.update(exc.extra or {})
                    return jsonify(payload), exc.status
                except Exception:  # noqa: BLE001
                    logging.exception('mail: %s упал', handler.__name__)
                    return jsonify({'error': 'Внутренняя ошибка раздела «Почта»'}), 500
            return wrapper
        return decorator

    # ─── Разбор запроса ──────────────────────────────────────────────────────

    def _payload():
        """JSON или multipart (шаг с файлами): поля формы лежат JSON-строкой в `payload`."""
        if request.mimetype == 'multipart/form-data':
            raw = request.form.get('payload') or '{}'
            try:
                data = json.loads(raw)
            except ValueError as exc:
                raise rules.MailError('Неверные данные формы') from exc
            return data if isinstance(data, dict) else {}
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def _uploads():
        uploads = [item for item in request.files.getlist('files') if item and item.filename] \
            if request.mimetype == 'multipart/form-data' else []
        if len(uploads) > files.MAX_FILES:
            raise rules.MailError('Не больше %d файлов за раз' % files.MAX_FILES, code='MAIL_TOO_MANY_FILES')
        prepared = []
        for upload in uploads:
            data = upload.read()
            files.check_file(filename=upload.filename, content_type=upload.mimetype, size=len(data))
            prepared.append({'data': data, 'filename': files.safe_name(upload.filename),
                             'content_type': upload.mimetype or 'application/octet-stream'})
        return prepared

    def _arg_date(name):
        value = request.args.get(name)
        if not value:
            return None
        try:
            day = rules.date_or_none(value)
        except ValueError as exc:
            raise rules.MailError('Неверная дата: %s' % name) from exc
        # 9999-12-31 + сутки — переполнение даты и 500 вместо отбора.
        if day.year < 2000 or day.year > 2100:
            raise rules.MailError('Неверная дата: %s' % name)
        return day

    def _list_filters():
        scope = request.args.get('scope') or 'registry'
        if scope not in queries.SCOPES:
            raise rules.MailError('Неизвестная вкладка реестра')
        quick = request.args.get('quick') or None
        if quick and quick not in queries.QUICK_CODES + ('drafts',):
            raise rules.MailError('Неизвестный быстрый фильтр')
        status = request.args.get('status') or None
        if status and status not in rules.STATUSES:
            raise rules.MailError('Неизвестный статус')
        office_id = request.args.get('office_id') or None
        try:
            office_id = int(office_id) if office_id else None
        except ValueError as exc:
            raise rules.MailError('Неверный офис') from exc
        event_kind = request.args.get('event') or 'created'
        if event_kind not in queries.EVENT_COLUMNS:
            raise rules.MailError('Неизвестное событие для отбора по дате')
        return {
            'scope': scope, 'quick': quick, 'status': status, 'office_id': office_id,
            'query': (request.args.get('q') or '').strip()[:200] or None,
            'event_kind': event_kind, 'event_from': _arg_date('event_from'), 'event_to': _arg_date('event_to'),
            'doc_from': _arg_date('doc_from'), 'doc_to': _arg_date('doc_to'),
        }

    def _actor(ctx):
        return {'user_id': ctx['user_id'], 'name': ctx.get('name')}

    def _load_visible(cursor, ctx, shipment_id, *, for_update=False):
        shipment = queries.read_shipment(cursor, shipment_id, for_update=for_update)
        if not shipment or not access.can_view(ctx, shipment):
            raise rules.MailError('Отправление не найдено', code='MAIL_NOT_FOUND', status=404)
        return shipment

    def _route_zone(cursor, shipment):
        """Зона маршрута для окна «Передано курьеру»: известна по матрице — подставится сама,
        нет — человек выбирает вручную (ТЗ п. 7: «специальные маршруты — ручное подтверждение»)."""
        code = rules.zone_for_route(shipment.get('sender_city'), shipment.get('recipient_city'),
                                    queries.routes_map(cursor))
        if not code:
            return None
        zone = queries.zone_by_code(cursor, code)
        if not zone:
            return None
        window = rules.sla_window(zone, shipment.get('delivery_type'))
        return {'code': code, 'label': zone['label'], 'window': list(window) if window else None}

    def _card(cursor, ctx, shipment_id):
        today = queries.today_almaty()
        shipment = _load_visible(cursor, ctx, shipment_id)
        items = queries.read_items(cursor, shipment_id)
        can_files = access.can_open_files(ctx, shipment)
        edit_allowed, reason_required, items_editable = access.edit_rights(ctx, shipment)
        actions = access.allowed_actions(ctx, shipment)
        events = queries.read_events(cursor, shipment_id)
        return {
            'shipment': queries.serialize_shipment(shipment, today, holidays),
            'items': queries.serialize_items(items),
            # Кому вложения закрыты, тому и их имена в истории не показываем.
            'events': events if can_files else queries.redact_events(events),
            # Трек и время передачи поправить может сторона отправки (ТЗ п. 11: с причиной и историей).
            'can_fix_handover': bool(edit_allowed and shipment.get('handed_at')
                                     and 'sender' in access.sides(ctx, shipment)),
            'files': queries.read_files(cursor, shipment_id) if can_files else [],
            'files_hidden': not can_files,
            'actions': actions,
            'edit': {'allowed': edit_allowed, 'reason_required': reason_required, 'items': items_editable},
            'can_attach': access.can_attach(ctx, shipment),
            'missing_for_ready': rules.missing_for_ready(shipment, items) if shipment['status'] == 'draft' else {},
            'route_zone': _route_zone(cursor, shipment) if 'hand_over' in actions else None,
        }

    def _offices_and_payer(cursor, ctx, fields, *, current=None):
        """Проверяет офисы и плательщика по справочникам и снимает их в карточку."""
        active = {office['id']: office for office in queries.list_offices(cursor)}
        errors = {}
        sender_id, recipient_id = fields.get('sender_office_id'), fields.get('recipient_office_id')
        unchanged_sender = current is not None and sender_id == current.get('sender_office_id')
        unchanged_recipient = current is not None and recipient_id == current.get('recipient_office_id')
        if not sender_id:
            errors['sender_office_id'] = 'Выберите офис отправки'
        elif not unchanged_sender:
            if sender_id not in active:
                errors['sender_office_id'] = 'Офиса нет в справочнике'
            elif sender_id not in access.sender_office_ids(ctx, list(active)):
                errors['sender_office_id'] = 'Отправлять можно только из своего офиса'
        if recipient_id and not unchanged_recipient and recipient_id not in active:
            errors['recipient_office_id'] = 'Офиса нет в справочнике'
        if errors:
            raise rules.MailError('Проверьте офисы', code='MAIL_FIELDS', fields=errors)
        offices = dict(active)
        # Офис не меняли — в карточке остаётся её снимок, даже если справочник с тех пор
        # переименовал офис или убрал его в архив: карточка — документ того дня.
        for key, office_id, same in (('sender', sender_id, unchanged_sender),
                                     ('recipient', recipient_id, unchanged_recipient)):
            if office_id and same:
                offices[office_id] = {
                    'id': office_id, 'city': current.get('%s_city' % key),
                    'name': current.get('%s_office_name' % key),
                    'address': current.get('sender_office_address') if key == 'sender' else None,
                }
        payer = None
        if fields.get('payer_entity_id'):
            payer = queries.read_legal_entity(cursor, fields['payer_entity_id'])
            unchanged_payer = current is not None and fields['payer_entity_id'] == current.get('payer_entity_id')
            if not payer or (not payer['active'] and not unchanged_payer):
                raise rules.MailError('Проверьте плательщика', code='MAIL_FIELDS',
                                      fields={'payer_entity_id': 'Юрлица нет в справочнике'})
            if unchanged_payer and current.get('payer_name'):
                payer = {'id': payer['id'], 'name': current['payer_name']}
        return offices, payer

    def _readable_changes(changes, offices, payer, current):
        """Офис и плательщик в истории — названиями, а не номерами."""
        readable = dict(changes)
        for name, key in (('sender_office_id', 'sender'), ('recipient_office_id', 'recipient')):
            if name in readable:
                old_id, new_id = readable[name]
                old = current.get('%s_office_name' % key) if old_id else None
                new = (offices.get(new_id) or {}).get('name') if new_id else None
                readable[name] = [old, new]
        if 'payer_entity_id' in readable:
            readable['payer_entity_id'] = [current.get('payer_name'), (payer or {}).get('name')]
        for name in ('delivery_type', 'urgency'):
            labels = rules.DELIVERY_TYPE_LABELS if name == 'delivery_type' else rules.URGENCY_LABELS
            if name in readable:
                readable[name] = [labels.get(value, value) for value in readable[name]]
        return readable

    def _check_economy(cursor, delivery_type, sender_city, recipient_city):
        """«Эконом» — только когда его срок задан: иначе отправление застрянет на «Передано
        курьеру», когда пакет уже у курьера. Проверяем при «Готово к отправке», а не на шаге."""
        if delivery_type != 'economy':
            return
        zones = queries.load_zones(cursor)
        if not rules.economy_ready(zones):
            raise rules.MailError(
                'Сроки эконом-доставки ещё не заданы — выберите «Стандарт» или попросите администратора раздела '
                'задать их в «Справочниках»', code='MAIL_FIELDS',
                fields={'delivery_type': 'Сроки эконома не заданы в «Справочниках»'})
        code = rules.zone_for_route(sender_city, recipient_city, queries.routes_map(cursor))
        zone = next((item for item in zones if item['code'] == code), None) if code else None
        if zone and zone.get('eco_max_days') is None:
            raise rules.MailError(
                'Для зоны «%s» срок эконом-доставки не задан — выберите «Стандарт»' % zone['label'],
                code='MAIL_FIELDS', fields={'delivery_type': 'Для зоны «%s» эконом не задан' % zone['label']})

    def _cities(fields, offices):
        return ((offices.get(fields.get('sender_office_id')) or {}).get('city'),
                (offices.get(fields.get('recipient_office_id')) or {}).get('city'))

    def _handover_fix(cursor, ctx, current, fields, payload, now, today):
        """Правка передачи курьеру (ТЗ п. 11: после передачи — с причиной и историей): трек и
        время передачи — стороне отправки; смена типа доставки — с пересчётом срока по зоне.
        Возвращает (поля для записи, изменения для истории)."""
        if not current.get('handed_at'):
            return {}, {}
        values, changes = {}, {}
        track = current.get('track_number')
        handed_at = current.get('handed_at')
        fixing = 'track_number' in payload or 'handed_at' in payload
        if fixing:
            if 'sender' not in access.sides(ctx, current):
                raise rules.MailError('Трек и время передачи правит сторона отправки', code='MAIL_FORBIDDEN',
                                      status=403)
            if 'track_number' in payload:
                track = rules.text(payload.get('track_number'), 64)
                if not track:
                    raise rules.MailError('Укажите трек-номер', code='MAIL_FIELDS',
                                          fields={'track_number': 'Трек-номер или согласованный временный номер'})
            if 'handed_at' in payload:
                try:
                    handed_at = rules.datetime_or_none(payload.get('handed_at'))
                except ValueError:
                    handed_at = None
                if not handed_at:
                    raise rules.MailError('Укажите дату и время передачи', code='MAIL_FIELDS',
                                          fields={'handed_at': 'Укажите дату и время передачи'})
                if handed_at > now + CLOCK_SKEW or handed_at.date() < today - timedelta(days=MAX_BACKDATE_DAYS):
                    raise rules.MailError('Проверьте время передачи', code='MAIL_FIELDS',
                                          fields={'handed_at': 'Время вне допустимого окна'})
                if current.get('delivered_on') and handed_at.date() > current['delivered_on']:
                    raise rules.MailError('Передача курьеру не может быть позже вручения', code='MAIL_FIELDS',
                                          fields={'handed_at': 'Позже даты вручения'})
            if (track or '').lower() != (current.get('track_number') or '').lower():
                duplicates = queries.track_duplicates(cursor, track, exclude_id=current['id'])
                if duplicates and not rules.bool_value(payload.get('confirm_duplicate')):
                    raise rules.MailError('Этот трек уже есть у %s' % ', '.join(duplicates),
                                          code='MAIL_TRACK_DUPLICATE', status=409, extra={'duplicates': duplicates})
                values['track_number'] = track
                changes['track_number'] = [current.get('track_number'), track]
            if handed_at != current['handed_at']:
                values['handed_at'] = handed_at
                changes['handed_at'] = [current['handed_at'].isoformat(), handed_at.isoformat()]
        window = None
        if current.get('sla_max_days') is not None:
            window = (current.get('sla_min_days'), current['sla_max_days'])
        if fields['delivery_type'] != current['delivery_type']:
            zone = queries.zone_by_code(cursor, current.get('zone_code')) if current.get('zone_code') else None
            window = rules.sla_window(zone, fields['delivery_type'])
            if not window:
                raise rules.MailError('Для зоны отправления не задан срок этого типа доставки — тип не меняется',
                                      code='MAIL_ZONE_NO_SLA', status=409)
            values.update(sla_min_days=window[0], sla_max_days=window[1])
        if window and ('handed_at' in values or 'sla_max_days' in values):
            earliest, due = rules.deadline(handed_at.date(), (window[0] if window[0] is not None else window[1],
                                                              window[1]), holidays)
            values.update(earliest_on=earliest, due_on=due)
            if due != current.get('due_on'):
                changes['due_on'] = [current['due_on'].isoformat() if current.get('due_on') else None,
                                     due.isoformat()]
        return values, changes

    # ─── Контекст и списки ───────────────────────────────────────────────────

    @route('/context')
    def mail_context(ctx):
        with db._get_cursor() as cursor:
            offices = queries.list_offices(cursor)
            office_ids = [office['id'] for office in offices]
            own = queries.offices_by_id(cursor, ctx['office_ids'])
            payload = {
                'me': {'user_id': ctx['user_id'], 'name': ctx.get('name'), 'phone': ctx.get('phone'),
                       'city': ctx.get('city')},
                'capabilities': access.capabilities(ctx),
                'offices': offices,
                'own_offices': [own[x] for x in ctx['office_ids'] if x in own],
                'sender_office_ids': access.sender_office_ids(ctx, office_ids),
                'default_sender_office_id': access.default_sender_office(ctx, office_ids),
                'legal_entities': queries.list_legal_entities(cursor),
                'zones': queries.load_zones(cursor),
                'today': queries.today_almaty().isoformat(),
                'storage_ready': files.storage_ready(gcs),
            }
        return jsonify(payload)

    @route('/shipments')
    def mail_list(ctx):
        filters = _list_filters()
        try:
            limit = max(1, min(PAGE_LIMIT, int(request.args.get('limit') or 50)))
            offset = max(0, int(request.args.get('offset') or 0))
        except ValueError as exc:
            raise rules.MailError('Неверная страница') from exc
        if filters['scope'] in ('outgoing', 'incoming') and not ctx['office_ids']:
            raise rules.MailError('У вас нет своего офиса в «Участниках»', code='MAIL_NO_OFFICE', status=403)
        today = queries.today_almaty()
        with db._get_cursor() as cursor:
            rows, total = queries.list_shipments(cursor, ctx, today=today, limit=limit, offset=offset, **filters)
            counts = queries.counters(cursor, ctx, today=today, **filters)
        return jsonify({
            'items': [queries.serialize_shipment(row, today, holidays) for row in rows],
            'total': total, 'counters': counts, 'limit': limit, 'offset': offset,
        })

    @route('/overview')
    def mail_overview(ctx):
        today = queries.today_almaty()
        with db._get_cursor() as cursor:
            counts = queries.counters(cursor, ctx, today=today, scope='registry')
            groups = queries.attention(cursor, ctx, today=today)
        for group in groups:
            group['items'] = [queries.serialize_shipment(row, today, holidays) for row in group['items']]
        return jsonify({'counters': counts, 'attention': groups})

    @route('/shipments/<int:shipment_id>')
    def mail_card(shipment_id, ctx):
        with db._get_cursor() as cursor:
            return jsonify(_card(cursor, ctx, shipment_id))

    # ─── Создание и правка ───────────────────────────────────────────────────

    @route('/shipments', methods=('POST',))
    def mail_create(ctx):
        if not access.can_create(ctx):
            raise rules.MailError('Создавать отправления ваша роль не может', code='MAIL_FORBIDDEN', status=403)
        payload = _payload()
        fields, items = rules.clean_shipment(payload)
        submit = rules.bool_value(payload.get('submit'))
        if submit:
            rules.require_complete(fields, items)
        today = queries.today_almaty()
        with db._get_cursor() as cursor:
            offices, payer = _offices_and_payer(cursor, ctx, fields)
            if submit:
                _check_economy(cursor, fields['delivery_type'], *_cities(fields, offices))
            shipment_id = queries.create_shipment(cursor, fields=fields, items=items, offices=offices, payer=payer,
                                                  actor=_actor(ctx), ready=submit, year=today.year)
            card = _card(cursor, ctx, shipment_id)
        return jsonify(card), 201

    @route('/shipments/<int:shipment_id>', methods=('PATCH',))
    def mail_update(shipment_id, ctx):
        payload = _payload()
        with db._get_cursor() as cursor:
            current = _load_visible(cursor, ctx, shipment_id, for_update=True)
            allowed, reason_required, items_editable = access.edit_rights(ctx, current)
            if not allowed:
                raise rules.MailError('Править это отправление вы не можете', code='MAIL_FORBIDDEN', status=403)
            fields, items = rules.clean_shipment(payload)
            current_items = queries.read_items(cursor, shipment_id)
            if 'items' not in payload:
                items = None
            items_changed = items is not None and \
                rules.items_signature(items) != rules.items_signature(current_items)
            if items_changed and not items_editable:
                raise rules.MailError('Содержимое после сверки не меняется', code='MAIL_ITEMS_LOCKED', status=409)
            if current['status'] not in rules.BEFORE_HANDOVER and (
                    fields['sender_office_id'] != current['sender_office_id']
                    or fields['recipient_office_id'] != current['recipient_office_id']):
                raise rules.MailError('Офисы после передачи курьеру не меняются', code='MAIL_OFFICES_LOCKED',
                                      status=409)
            changes = rules.changed_fields(current, fields)
            fix_values, fix_changes = _handover_fix(cursor, ctx, current, fields, payload, queries.now_almaty(),
                                                    queries.today_almaty())
            if not changes and not items_changed and not fix_changes:
                return jsonify(_card(cursor, ctx, shipment_id))
            reason = rules.text(payload.get('reason'), 2000)
            if reason_required and not reason:
                raise rules.MailError('Укажите причину изменения', code='MAIL_REASON_REQUIRED',
                                      fields={'reason': 'После передачи курьеру правка — только с причиной'})
            if current['status'] != 'draft':
                rules.require_complete(fields, items if items is not None else current_items)
            offices, payer = _offices_and_payer(cursor, ctx, fields, current=current)
            if current['status'] in ('ready', 'courier_requested'):
                _check_economy(cursor, fields['delivery_type'], *_cities(fields, offices))
            history = _readable_changes(changes, offices, payer, current)
            history.update(fix_changes)
            extra = None
            if items_changed and current['status'] != 'draft':
                # Строки — главное, с чем сверяет получатель: в истории остаются обе версии (критерий L).
                extra = {'items_before': [rules.item_line(item) for item in current_items],
                         'items_after': [rules.item_line(item) for item in items]}
            queries.update_shipment(cursor, shipment_id, fields=fields, items=items, offices=offices, payer=payer,
                                    actor=_actor(ctx), changes=history, items_changed=items_changed, reason=reason,
                                    extra_payload=extra)
            if fix_values:
                queries.update_fields(cursor, shipment_id, fix_values)
            return jsonify(_card(cursor, ctx, shipment_id))

    # ─── Шаги статуса ────────────────────────────────────────────────────────

    def _required_date(payload, name, message, *, today, not_after=None, not_before=None):
        try:
            value = rules.date_or_none(payload.get(name))
        except ValueError:
            value = None
        if not value:
            raise rules.MailError(message, code='MAIL_FIELDS', fields={name: message})
        low = not_before or (today - timedelta(days=MAX_BACKDATE_DAYS))
        high = not_after or (today + timedelta(days=MAX_AHEAD_DAYS))
        if value < low or value > high:
            raise rules.MailError('Проверьте дату', code='MAIL_FIELDS', fields={name: 'Дата вне допустимого окна'})
        return value

    @route('/shipments/<int:shipment_id>/actions/<action>', methods=('POST',))
    def mail_action(shipment_id, action, ctx):
        payload = _payload()
        spec = rules.action_spec(action)
        uploads = _uploads()
        file_kind = ACTION_FILE_KINDS.get(action)
        if uploads and not file_kind:
            raise rules.MailError('К этому шагу файлы не прикладываются', code='MAIL_FILES_NOT_EXPECTED')
        today = queries.today_almaty()
        now = queries.now_almaty()
        actor = _actor(ctx)
        refs = []
        try:
            with db._get_cursor() as cursor:
                shipment = _load_visible(cursor, ctx, shipment_id, for_update=True)
                rules.check_transition(action, shipment['status'])
                if action not in access.allowed_actions(ctx, shipment):
                    raise rules.MailError('Этот шаг вам недоступен', code='MAIL_FORBIDDEN', status=403)
                note = rules.text(payload.get('note'), 2000)
                if action in rules.NOTE_REQUIRED and not note:
                    raise rules.MailError(rules.NOTE_REQUIRED[action], code='MAIL_FIELDS',
                                          fields={'note': rules.NOTE_REQUIRED[action]})
                status_from = shipment['status']
                status_to = spec['to']
                extra = {}
                event = {}
                handed_on = shipment['handed_at'].date() if shipment.get('handed_at') else None

                if action == 'ready':
                    rules.require_complete(shipment, queries.read_items(cursor, shipment_id))
                    _check_economy(cursor, shipment['delivery_type'], shipment.get('sender_city'),
                                   shipment.get('recipient_city'))
                    extra['ready_at'] = queries.NOW_VALUE
                elif action == 'courier_request':
                    planned = _required_date(payload, 'planned_pickup_on', 'Укажите плановую дату забора',
                                             today=today, not_before=today - timedelta(days=7))
                    track = rules.text(payload.get('track_number'), 64) or shipment.get('track_number')
                    extra.update(courier_requested_at=queries.NOW_VALUE, planned_pickup_on=planned,
                                 track_number=track)
                    event.update(planned_pickup_on=planned, track_number=track)
                elif action == 'pickup_failed':
                    planned = _required_date(payload, 'planned_pickup_on', 'Укажите новую дату забора',
                                             today=today, not_before=today)
                    queries.update_fields(cursor, shipment_id, {'planned_pickup_on': planned})
                    queries.log_event(cursor, shipment_id, 'pickup_failed', actor, note=note,
                                      payload={'planned_pickup_on': planned})
                    return jsonify(_card(cursor, ctx, shipment_id))
                elif action == 'hand_over':
                    extra.update(_hand_over(cursor, shipment, payload, uploads, now=now, today=today))
                    event.update(track_number=extra['track_number'], handed_at=extra['handed_at'],
                                 due_on=extra['due_on'], zone=extra['zone_code'])
                    if extra.get('zone_manual'):
                        event['zone_manual'] = True
                elif action == 'deliver':
                    delivered = _required_date(payload, 'delivered_on', 'Укажите дату вручения', today=today,
                                               not_after=today, not_before=handed_on)
                    extra['delivered_on'] = delivered
                    event['delivered_on'] = delivered
                elif action == 'reconcile':
                    items = queries.read_items(cursor, shipment_id)
                    lines, outcome = rules.clean_reconcile_lines(payload.get('lines'), items)
                    if outcome == 'problem' and not note:
                        raise rules.MailError('Опишите расхождение', code='MAIL_FIELDS',
                                              fields={'note': 'Опишите расхождение'})
                    if status_from != 'delivered':
                        # Получатель сверяет сразу, без отдельной отметки курьера: дата вручения
                        # фиксируется здесь, а «Доставлено курьером» остаётся в истории своим
                        # шагом — курьерская отметка и внутренняя сверка разные (ТЗ п. 4).
                        delivered = _required_date(payload, 'delivered_on', 'Укажите дату вручения', today=today,
                                                   not_after=today, not_before=handed_on)
                        extra['delivered_on'] = delivered
                        queries.log_event(cursor, shipment_id, 'status', actor, from_status=status_from,
                                          to_status='delivered', payload={'delivered_on': delivered})
                        status_from = 'delivered'
                    queries.save_reconciliation(cursor, lines)
                    status_to = outcome
                    extra.update(reconciled_at=queries.NOW_VALUE, reconciled_by=ctx['user_id'],
                                 reconciled_by_name=ctx.get('name'))
                    event['discrepancies'] = sum(1 for line in lines if line['line_status'] != 'ok')
                if uploads:
                    for upload in uploads:
                        refs.append(files.upload(gcs, data=upload['data'], filename=upload['filename'],
                                                 content_type=upload['content_type'], now=now))
                    # Чем подтверждён шаг — видно в истории и на листе «История» (ТЗ п. 4, критерий J).
                    event['files'] = [{'filename': upload['filename'], 'kind': file_kind} for upload in uploads]
                queries.set_status(cursor, shipment_id, status=status_to, actor=actor, extra=extra)
                event_id = queries.log_event(cursor, shipment_id, 'status', actor, from_status=status_from,
                                             to_status=status_to, note=note, payload=dict(event, action=action))
                for upload, (bucket, blob_path) in zip(uploads, refs):
                    queries.insert_file(cursor, shipment_id, kind=file_kind, filename=upload['filename'],
                                        content_type=upload['content_type'], size=len(upload['data']),
                                        bucket=bucket, blob_path=blob_path, actor=actor, event_id=event_id)
                return jsonify(_card(cursor, ctx, shipment_id))
        except Exception:
            files.drop(gcs, refs)
            raise

    def _hand_over(cursor, shipment, payload, uploads, *, now, today):
        """«Передано курьеру» (ТЗ п. 5 шаг 7, п. 7, п. 14): время, трек, доказательство — и
        только здесь запускается срок доставки."""
        try:
            handed_at = rules.datetime_or_none(payload.get('handed_at'))
        except ValueError:
            handed_at = None
        if not handed_at:
            raise rules.MailError('Укажите дату и время передачи', code='MAIL_FIELDS',
                                  fields={'handed_at': 'Укажите дату и время передачи'})
        if handed_at > now + CLOCK_SKEW:
            raise rules.MailError('Время передачи ещё не наступило', code='MAIL_FIELDS',
                                  fields={'handed_at': 'Время в будущем'})
        if handed_at.date() < today - timedelta(days=MAX_BACKDATE_DAYS):
            raise rules.MailError('Проверьте дату передачи', code='MAIL_FIELDS',
                                  fields={'handed_at': 'Слишком давно — проверьте год'})
        track = rules.text(payload.get('track_number'), 64) or shipment.get('track_number')
        if not track:
            raise rules.MailError('Укажите трек-номер', code='MAIL_FIELDS',
                                  fields={'track_number': 'Трек-номер или согласованный временный номер'})
        duplicates = queries.track_duplicates(cursor, track, exclude_id=shipment['id'])
        if duplicates and not rules.bool_value(payload.get('confirm_duplicate')):
            raise rules.MailError('Этот трек уже есть у %s' % ', '.join(duplicates), code='MAIL_TRACK_DUPLICATE',
                                  status=409, extra={'duplicates': duplicates})
        if not uploads and not queries.has_proof_file(cursor, shipment['id']):
            raise rules.MailError('Приложите накладную или подтверждение передачи', code='MAIL_FIELDS',
                                  fields={'files': 'Нужно доказательство передачи'})
        code = rules.zone_for_route(shipment.get('sender_city'), shipment.get('recipient_city'),
                                    queries.routes_map(cursor))
        zone = queries.zone_by_code(cursor, code) if code else None
        manual = False
        if not zone:
            # Маршрута нет в матрице — или его зону убрали из справочника (в т. ч. внутригородскую «2»):
            # зону выбирают вручную, как специальный маршрут (ТЗ п. 7). Так же решает и _route_zone.
            code = str(payload.get('zone_code') or '').strip()
            zone = queries.zone_by_code(cursor, code) if code else None
            manual = True
        if not zone:
            raise rules.MailError('Маршрута нет в матрице — выберите зону', code='MAIL_FIELDS',
                                  fields={'zone_code': 'Выберите зону'})
        window = rules.sla_window(zone, shipment.get('delivery_type'))
        if not window:
            raise rules.MailError('Для зоны «%s» не задан срок эконом-доставки — задайте его в «Справочниках»'
                                  % zone['label'], code='MAIL_ZONE_NO_SLA', status=409)
        earliest, due = rules.deadline(handed_at.date(), window, holidays)
        return {'handed_at': handed_at, 'track_number': track, 'zone_code': code, 'zone_manual': manual,
                'sla_min_days': window[0], 'sla_max_days': window[1], 'earliest_on': earliest, 'due_on': due}

    # ─── Файлы ───────────────────────────────────────────────────────────────

    @route('/shipments/<int:shipment_id>/files', methods=('POST',))
    def mail_upload(shipment_id, ctx):
        payload = _payload()
        kind = str(payload.get('kind') or request.form.get('kind') or 'other').strip()
        if kind not in rules.FILE_KIND_LABELS:
            raise rules.MailError('Неизвестный вид файла')
        uploads = _uploads()
        if not uploads:
            raise rules.MailError('Выберите файл', code='MAIL_FIELDS', fields={'files': 'Выберите файл'})
        now = queries.now_almaty()
        actor = _actor(ctx)
        refs = []
        try:
            with db._get_cursor() as cursor:
                shipment = _load_visible(cursor, ctx, shipment_id, for_update=True)
                if not access.can_attach(ctx, shipment):
                    raise rules.MailError('Прикладывать файлы к этому отправлению вы не можете',
                                          code='MAIL_FORBIDDEN', status=403)
                for upload in uploads:
                    refs.append(files.upload(gcs, data=upload['data'], filename=upload['filename'],
                                             content_type=upload['content_type'], now=now))
                for upload, (bucket, blob_path) in zip(uploads, refs):
                    event_id = queries.log_event(cursor, shipment_id, 'file', actor,
                                                 payload={'filename': upload['filename'], 'file_kind': kind})
                    queries.insert_file(cursor, shipment_id, kind=kind, filename=upload['filename'],
                                        content_type=upload['content_type'], size=len(upload['data']),
                                        bucket=bucket, blob_path=blob_path, actor=actor, event_id=event_id)
                return jsonify(_card(cursor, ctx, shipment_id))
        except Exception:
            files.drop(gcs, refs)
            raise

    @route('/files/<int:file_id>/download')
    def mail_file_download(file_id, ctx):
        with db._get_cursor() as cursor:
            meta = queries.read_file(cursor, file_id)
            if not meta:
                raise rules.MailError('Файл не найден', code='MAIL_NOT_FOUND', status=404)
            shipment = _load_visible(cursor, ctx, meta['shipment_id'])
            if not access.can_open_files(ctx, shipment):
                raise rules.MailError('Вложения этого отправления вам закрыты', code='MAIL_FILES_CLOSED', status=403)
        data = files.download(gcs, meta['bucket'], meta['blob_path'])
        if data is None:
            raise rules.MailError('Файл не найден в хранилище', code='MAIL_NOT_FOUND', status=404)
        return send_file(BytesIO(data), mimetype=meta['content_type'] or 'application/octet-stream',
                         as_attachment=True, download_name=meta['filename'])

    # ─── Выгрузка ────────────────────────────────────────────────────────────

    @route('/export')
    def mail_export(ctx):
        filters = _list_filters()
        if filters['scope'] in ('outgoing', 'incoming') and not ctx['office_ids']:
            raise rules.MailError('У вас нет своего офиса в «Участниках»', code='MAIL_NO_OFFICE', status=403)
        today = queries.today_almaty()
        with db._get_cursor() as cursor:
            rows, items, events, total = queries.export_data(cursor, ctx, today=today, **filters)
            zones = queries.load_zones(cursor)
        # Имена закрытых вложений не выходят и в файл — то же правило, что в карточке.
        for row in rows:
            if row['id'] in events and not access.can_open_files(ctx, row):
                events[row['id']] = queries.redact_events(events[row['id']])
        stream = report.build_workbook(
            rows, items, events, zones=zones, today=today, holidays=holidays, generated_at=queries.now_almaty(),
            generated_by=ctx.get('name') or '', filters_note=_filters_note(filters), total=total,
            text_warning_patch=excel_text_warning)
        return send_file(stream, mimetype=XLSX_MIME, as_attachment=True, download_name=report.filename(today))

    def _filters_note(filters):
        tab = {'outgoing': 'Исходящие', 'incoming': 'Входящие', 'registry': 'Общий реестр',
               'problems': 'Проблемы и возвраты'}.get(filters['scope'], '')
        parts = ['вкладка «%s»' % tab] if tab else []
        if filters.get('quick'):
            label = (rules.QUICK_FILTERS.get(filters['quick']) or {}).get('label') or filters['quick']
            parts.append('«%s»' % label)
        if filters.get('status'):
            parts.append('статус «%s»' % rules.STATUS_LABELS.get(filters['status'], filters['status']))
        if filters.get('query'):
            parts.append('поиск «%s»' % filters['query'])
        if filters.get('event_from') or filters.get('event_to'):
            label = {'created': 'создано', 'handed': 'передано курьеру', 'delivered': 'вручено',
                     'reconciled': 'сверено'}.get(filters.get('event_kind'), 'создано')
            parts.append('%s %s — %s' % (label, filters.get('event_from') or '…', filters.get('event_to') or '…'))
        if filters.get('doc_from') or filters.get('doc_to'):
            parts.append('период документа %s — %s' % (filters.get('doc_from') or '…', filters.get('doc_to') or '…'))
        return ', '.join(str(part) for part in parts)

    # ─── Участники ───────────────────────────────────────────────────────────

    def _require_admin(ctx):
        if not access.is_admin(ctx):
            raise rules.MailError('Это может только администратор раздела', code='MAIL_ADMIN_ONLY', status=403)

    def _participants_payload(cursor):
        people = queries.list_participants(cursor)
        known = queries.offices_by_id(cursor, [x for person in people for x in person['office_ids']])
        return {'participants': people, 'offices': list(known.values())}

    @route('/participants')
    def mail_participants(ctx):
        _require_admin(ctx)
        with db._get_cursor() as cursor:
            return jsonify(_participants_payload(cursor))

    @route('/participants/<int:user_id>', methods=('PUT',))
    def mail_participant_save(user_id, ctx):
        _require_admin(ctx)
        payload = _payload()
        with db._get_cursor() as cursor:
            person = queries.read_user(cursor, user_id)
            if not person or person['fired']:
                raise rules.MailError('Сотрудник не найден или уволен', code='MAIL_NOT_FOUND', status=404)
            known = {office['id'] for office in queries.list_offices(cursor)}
            # Офис, ушедший в архив, у участника остаётся — снять его можно, добавить нельзя.
            cursor.execute("SELECT office_id FROM mail_participant_offices WHERE user_id = %s", (user_id,))
            known |= {row[0] for row in cursor.fetchall()}
            cursor.execute("SELECT role FROM mail_participants WHERE user_id = %s", (user_id,))
            current = cursor.fetchone()
            role, office_ids = access.clean_participant(payload, known, current_role=current[0] if current else None)
            queries.save_participant(cursor, user_id, role, office_ids, ctx['user_id'])
            return jsonify(_participants_payload(cursor))

    @route('/participants/<int:user_id>', methods=('DELETE',))
    def mail_participant_delete(user_id, ctx):
        _require_admin(ctx)
        with db._get_cursor() as cursor:
            if not queries.delete_participant(cursor, user_id):
                raise rules.MailError('Участник не найден', code='MAIL_NOT_FOUND', status=404)
            return jsonify(_participants_payload(cursor))

    @route('/people')
    def mail_people(ctx):
        _require_admin(ctx)
        query = (request.args.get('q') or '').strip()[:100]
        if len(query) < 2:
            return jsonify({'items': []})
        with db._get_cursor() as cursor:
            return jsonify({'items': queries.search_people(cursor, query)})

    # ─── Справочники и настройки ─────────────────────────────────────────────

    def _settings_payload(cursor):
        offices = queries.list_offices(cursor)
        return {
            'zones': queries.load_zones(cursor),
            'routes': queries.load_routes(cursor),
            'cities': sorted({office['city'] for office in offices if office.get('city')}, key=str.lower),
        }

    @route('/settings')
    def mail_settings(ctx):
        _require_admin(ctx)
        with db._get_cursor() as cursor:
            return jsonify(_settings_payload(cursor))

    @route('/settings', methods=('PUT',))
    def mail_settings_save(ctx):
        _require_admin(ctx)
        payload = _payload()
        zones = rules.clean_zones(payload.get('zones'))
        routes = rules.clean_routes(payload.get('routes') or [], {zone['code'] for zone in zones})
        with db._get_cursor() as cursor:
            queries.upsert_zones(cursor, zones)
            queries.save_routes(cursor, routes, ctx['user_id'])
            queries.drop_zones_except(cursor, [zone['code'] for zone in zones])
            return jsonify(_settings_payload(cursor))

    return bp
