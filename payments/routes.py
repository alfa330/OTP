"""HTTP-эндпоинты раздела «Оплата счетов» (Flask Blueprint, /api/payments).

Blueprint собирается фабрикой и получает зависимости аргументами, а не
импортирует bot_schedule2: тот сам подключает этот модуль (как вики, обращения,
посылки). Соглашения те же: методы всегда включают OPTIONS и первым делом
отдаётся preflight, авторизация — общий require_api_key, ошибка —
{"error": "...", "code": "..."} с осмысленным кодом.

Разделение обязанностей внутри пакета — в __init__.py; здесь только разбор
запроса, проверки прав и коды ответов. Правила процесса — в workflow.py.

Про Telegram. Сообщения собираются ВНУТРИ транзакции в `outbox`, а уходят
ПОСЛЕ коммита: иначе человек получил бы ссылку на заявку, которой в базе ещё
нет (или которая откатилась). Отказ Telegram не ломает ответ — он приходит
в `warnings`.
"""

import json
import logging
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify as _flask_jsonify, request, send_file

from . import access, files, fixed, notify, queries, report, schema, workflow

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
PAGE_SIZE_MAX = 200
EXPORT_MAX_ROWS = 5000

_MAX_LENGTHS = {
    'expense_name': 300, 'branch': 200, 'payment_period': 120, 'notes': 4000, 'invoice_number': 100,
    'invoice_description': 4000, 'invoice_requisites': 4000, 'previous_payment_note': 4000,
    'comment': 4000,
}


class ApiError(Exception):
    def __init__(self, message, code='PAYMENTS_BAD_REQUEST', status=400, extra=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra or {}


def jsonify(payload, status=200):
    """Все ответы раздела идут через plain(): Decimal → число, даты → isoformat без «GMT»."""
    return _flask_jsonify(queries.plain(payload)), status


# ─── Разбор значений ─────────────────────────────────────────────────────────

def _int(value, field=None):
    if value in (None, '', 'null'):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError('Поле «%s»: ожидается число' % (field or 'id'))


def _date(value, field=None):
    if value in (None, ''):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for pattern in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    raise ApiError('Поле «%s»: дата не разобрана' % (field or 'дата'))


def _bool(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'да', 'on')


def _text(value, field, *, required=False, limit=None):
    text = str(value or '').strip()
    if required and not text:
        raise ApiError('Заполните поле «%s»' % field)
    limit = limit or _MAX_LENGTHS.get(field)
    if limit and len(text) > limit:
        raise ApiError('Поле «%s» длиннее %s символов' % (field, limit))
    return text or None


def _digits(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def _payload():
    """JSON-тело либо поле `payload` формы multipart (когда рядом файлы)."""
    if request.content_type and 'multipart/form-data' in request.content_type.lower():
        raw = request.form.get('payload')
        if raw:
            try:
                return json.loads(raw)
            except ValueError:
                raise ApiError('Поле payload — не JSON')
        return {key: request.form.get(key) for key in request.form.keys()}
    return request.get_json(silent=True) or {}


def _uploaded_files():
    """[(FileStorage, kind)] из multipart: файлы полем `files`, виды — `kinds`
    (JSON-массив или повторяющееся поле) либо один `kind` на все."""
    if not request.files:
        return []
    storages = [item for item in request.files.getlist('files') if item and item.filename]
    kinds = request.form.getlist('kinds')
    if len(kinds) == 1 and kinds[0].startswith('['):
        try:
            kinds = json.loads(kinds[0])
        except ValueError:
            kinds = []
    default_kind = request.form.get('kind') or 'other'
    result = []
    for index, storage in enumerate(storages):
        kind = kinds[index] if index < len(kinds) else default_kind
        if kind not in schema.ATTACHMENT_KINDS:
            kind = 'other'
        result.append((storage, kind))
    return result


def _actor(ctx):
    return {'id': ctx['user_id'], 'name': ctx.get('name')}


# ─── Фабрика ─────────────────────────────────────────────────────────────────

def build_payments_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                             send_telegram=None, web_app_base_url=None, excel_text_warning=None,
                             gcs=None):
    """Собирает Blueprint раздела.

    send_telegram — (chat_id, text_html, reply_markup) -> response, из монолита.
    Без него уведомления просто не уходят (тесты, локальный стенд).
    gcs — {'bucket_name': () -> str, 'client': () -> Client}; без него вложения
    отвечают 503, а остальной раздел работает.
    """
    gcs = gcs or {}
    bp = Blueprint('payments', __name__, url_prefix='/api/payments')

    def payments_route(rule, methods=('GET',), admin=False):
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
                        return jsonify({"error": message}, status)
                    with db._get_cursor() as cursor:
                        ctx = queries.load_access_context(cursor, requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}, 404)
                    # Гейт раздела — на КАЖДОМ роуте: спрятанный пункт меню
                    # доступом не является, раздел открывается и прямым адресом.
                    if not access.can_open_section(ctx):
                        return jsonify({"error": "Раздел «Оплата счетов» вам не открыт",
                                        "code": "PAYMENTS_SECTION_CLOSED"}, 403)
                    if admin and not access.is_section_admin(ctx):
                        return jsonify({"error": "Это действие доступно администратору раздела",
                                        "code": "PAYMENTS_ADMIN_ONLY"}, 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except ApiError as exc:
                    body = {"error": str(exc), "code": exc.code}
                    body.update(exc.extra)
                    return jsonify(body, exc.status)
                except files.FileError as exc:
                    return jsonify({"error": str(exc), "code": exc.code}, exc.status)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('payments: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Оплата счетов»",
                                    "detail": str(exc)[:200]}, 500)

            return wrapper

        return decorator

    # ── Telegram ────────────────────────────────────────────────────────────

    def _flush(outbox):
        warnings = []
        if not outbox or not callable(send_telegram):
            return warnings
        for chat_id, text, markup in outbox:
            try:
                response = send_telegram(chat_id, text, parse_mode='HTML', reply_markup=markup)
                status = getattr(response, 'status_code', 200)
                if status != 200:
                    warnings.append('Telegram не принял сообщение (%s)' % status)
            except Exception as exc:  # noqa: BLE001
                logging.warning('payments: Telegram недоступен: %s', exc)
                warnings.append('Telegram недоступен: %s' % str(exc)[:120])
        return warnings

    def _queue_step(cursor, request_row, step_no, outbox, exclude=()):
        """Уведомить того, чей шаг наступил: человека либо всех участников роли."""
        current = next((s for s in queries.list_steps(cursor, request_row['id']) if s['step_no'] == step_no), None)
        if not current:
            return
        recipients = queries.telegram_recipients(
            cursor,
            user_ids=[current['assignee_id']] if current.get('assignee_id') else (),
            role_code=None if current.get('assignee_id') else current['role_code'],
            exclude=exclude)
        link = notify.request_link(web_app_base_url, request_row['id'])
        for recipient in recipients:
            outbox.append((recipient['chat_id'], notify.step_message(request_row, step_no),
                           notify.reply_markup(link)))

    def _queue_to_initiator(cursor, request_row, text, outbox, exclude=()):
        link = notify.request_link(web_app_base_url, request_row['id'])
        for recipient in queries.telegram_recipients(cursor, user_ids=[request_row.get('initiator_id')],
                                                     exclude=exclude):
            outbox.append((recipient['chat_id'], text, notify.reply_markup(link)))

    # ── Сборка полной карточки ──────────────────────────────────────────────

    def _permissions(ctx, request_row, steps, members):
        current = next((s for s in steps if s['state'] == 'current'), None)
        active = request_row.get('status') == 'active'
        can_act = bool(active and current and access.can_act_on_step(ctx, current, members))
        directly = False
        if current:
            me = ctx['user_id']
            directly = ((current.get('assignee_id') is not None and int(current['assignee_id']) == me)
                        or (current.get('assignee_id') is None
                            and me in (members.get(current['role_code']) or set())))
        paid = int(request_row.get('current_step') or 0) > 10 or request_row.get('status') == 'done'
        is_admin = access.is_section_admin(ctx)
        return {
            'can_act': can_act,
            'acting_as_admin': bool(can_act and not directly),
            'can_edit': access.can_edit_request(ctx, request_row),
            'can_cancel': access.can_cancel_request(ctx, request_row),
            'can_reassign': bool(is_admin and active),
            'can_delete': access.can_delete_request(ctx),
            'can_refund': bool(paid and (is_admin or ctx['user_id'] in (members.get('accounting') or set()))),
            'can_return': bool(can_act and current and current.get('returns_to')),
            'can_reject': bool(can_act and current and current.get('can_reject')),
            'current_step': current['step_no'] if current else None,
        }

    def _full(cursor, request_id, ctx):
        request_row = queries.read_request(cursor, request_id)
        if not request_row:
            raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
        members = queries.role_member_ids(cursor)
        steps = queries.list_steps(cursor, request_id)
        items = queries.list_items(cursor, request_id)
        attachments = [queries.public_attachment(a) for a in queries.list_attachments(cursor, request_id)]
        events = queries.list_events(cursor, request_id)
        history = queries.payment_history(
            cursor, counterparty_id=request_row.get('counterparty_id'),
            category_id=request_row.get('category_id'), subcategory_id=request_row.get('subcategory_id'),
            expense_name=request_row.get('expense_name'), exclude_request_id=request_id)
        contract = queries.read_contract(cursor, request_row.get('contract_id'))
        gate_ok, gate_reason = workflow.contract_gate(
            request_row.get('amount'), contract, counterparty_id=request_row.get('counterparty_id'),
            on_date=request_row.get('invoice_date') or queries.today_almaty())
        return {
            'request': request_row,
            'items': items,
            'steps': steps,
            'attachments': attachments,
            'events': events,
            'history': history,
            'contract': contract,
            'contract_check': {'ok': gate_ok, 'reason': gate_reason,
                               'threshold': float(workflow.CONTRACT_REQUIRED_OVER)},
            'permissions': _permissions(ctx, request_row, steps, members),
        }

    # ── Проверка полей заявки ───────────────────────────────────────────────

    def _validate_request(cursor, payload, ctx, *, creating, current=None):
        fields = {}
        errors = []
        fields['expense_name'] = _text(payload.get('expense_name'), 'expense_name') or (
            current or {}).get('expense_name')
        if not fields['expense_name']:
            errors.append('Укажите наименование расхода')

        raw_items = payload.get('items')
        items = None
        if raw_items is not None or creating:
            items = []
            for raw in (raw_items or []):
                name = _text((raw or {}).get('name'), 'name', limit=300)
                if not name:
                    continue
                qty = workflow.to_decimal((raw or {}).get('quantity'), Decimal('1'))
                price = workflow.to_decimal((raw or {}).get('unit_price'))
                if qty <= 0:
                    errors.append('Позиция «%s»: количество должно быть больше нуля' % name)
                if price < 0:
                    errors.append('Позиция «%s»: цена не может быть отрицательной' % name)
                items.append({'name': name, 'quantity': qty, 'unit': _text((raw or {}).get('unit'), 'unit', limit=32),
                              'unit_price': price})
            if not items:
                errors.append('Добавьте хотя бы одну позицию: наименование, количество, цена за единицу')
            elif workflow.items_total(items) <= 0:
                errors.append('Сумма заявки должна быть больше нуля')

        for key in ('project_id', 'category_id', 'subcategory_id', 'counterparty_id', 'legal_entity_id',
                    'contract_id', 'department_id', 'manager_id'):
            if key in payload or creating:
                fields[key] = _int(payload.get(key), key)
        if creating or 'counterparty_id' in payload:
            if not fields.get('counterparty_id'):
                errors.append('Выберите контрагента (поставщика)')
        if creating or 'category_id' in payload:
            if not fields.get('category_id'):
                errors.append('Выберите категорию расхода')
        if fields.get('subcategory_id') and fields.get('category_id'):
            cursor.execute("SELECT parent_id FROM payment_categories WHERE id = %s", (fields['subcategory_id'],))
            row = cursor.fetchone()
            if not row or row[0] != fields['category_id']:
                errors.append('Подкатегория не относится к выбранной категории')
        if fields.get('contract_id'):
            contract = queries.read_contract(cursor, fields['contract_id'])
            cp_id = fields.get('counterparty_id') or (current or {}).get('counterparty_id')
            if not contract:
                errors.append('Договор не найден')
            elif cp_id and contract['counterparty_id'] != cp_id:
                errors.append('Договор №%s заключён с другим контрагентом' % contract['number'])

        for key in ('branch', 'payment_period', 'notes', 'invoice_number', 'invoice_description'):
            if key in payload:
                fields[key] = _text(payload.get(key), key)
        if 'payment_source' in payload or creating:
            source = str(payload.get('payment_source') or 'too').strip().lower()
            if source not in schema.PAYMENT_SOURCES:
                errors.append('Источник оплаты: ТОО, кошелёк или наличные')
            fields['payment_source'] = source
        if 'payment_type' in payload or creating:
            ptype = str(payload.get('payment_type') or 'one_time').strip().lower()
            if ptype not in schema.PAYMENT_TYPES:
                errors.append('Тип оплаты: фиксированный, ежемесячный или разовый')
            fields['payment_type'] = ptype
        if 'card_number' in payload:
            digits = _digits(payload.get('card_number'))
            if digits and not 12 <= len(digits) <= 19:
                errors.append('Номер карты: от 12 до 19 цифр')
            fields['card_number'] = digits or None
        for key in ('due_on', 'invoice_date'):
            if key in payload:
                fields[key] = _date(payload.get(key), key)
        for key in ('needs_power_of_attorney', 'needs_payment_order'):
            if key in payload:
                fields[key] = _bool(payload.get(key))

        if 'department_id' in fields:
            dept = queries.department_brief(cursor, fields['department_id'])
            fields['department_name'] = (dept or {}).get('name')
        if creating and not fields.get('department_id'):
            fields['department_id'] = ctx.get('department_id')
            fields['department_name'] = ctx.get('department_name')

        manager = None
        if 'manager_id' in fields and fields['manager_id']:
            manager = queries.user_brief(cursor, fields['manager_id'])
            if not manager:
                errors.append('Руководитель не найден')
            elif manager['id'] == ctx['user_id'] and creating:
                errors.append('Руководителем инициатора нельзя указать самого инициатора')
        if errors:
            raise ApiError('; '.join(errors), code='PAYMENT_REQUEST_INVALID', extra={'errors': errors})
        return fields, items, manager

    def _store_files(cursor, request_id, uploads, step_no, actor):
        stored = []
        if not uploads:
            return stored
        if len(uploads) > files.MAX_FILES:
            raise files.FileError('Не больше %s файлов за раз' % files.MAX_FILES)
        for storage, kind in uploads:
            data = storage.read() or b''
            files.check_file(filename=storage.filename, content_type=storage.mimetype, size=len(data))
        for storage, kind in uploads:
            storage.stream.seek(0)
            data = storage.read() or b''
            bucket, blob_path = files.upload(gcs, data=data, filename=storage.filename,
                                             content_type=storage.mimetype)
            attachment_id = queries.add_attachment(
                cursor, request_id, step_no=step_no, kind=kind, file_name=files.safe_name(storage.filename),
                content_type=storage.mimetype or 'application/octet-stream', file_size=len(data),
                bucket=bucket, blob_path=blob_path, actor=actor)
            stored.append(attachment_id)
        return stored

    def _apply_invoice_route(cursor, request_row, actor):
        """Шаг 7: проверка договора и выбор согласующего счёта по Приказам.
        Возвращает (ok, reason). При отказе заявка помечена блоком."""
        contract = queries.read_contract(cursor, request_row.get('contract_id'))
        on_date = request_row.get('invoice_date') or queries.today_almaty()
        ok, reason = workflow.contract_gate(request_row.get('amount'), contract,
                                            counterparty_id=request_row.get('counterparty_id'), on_date=on_date)
        if not ok:
            if request_row.get('block_code') != workflow.BLOCK_CONTRACT_REQUIRED or request_row.get('block_reason') != reason:
                queries.set_block(cursor, request_row['id'], workflow.BLOCK_CONTRACT_REQUIRED, reason, actor)
            return False, reason
        queries.clear_block(cursor, request_row['id'], actor)
        orders = queries.list_orders(cursor, only_active=True)
        for order in orders:
            order['_counterparty_name'] = request_row.get('counterparty_name')
        basis = workflow.resolve_invoice_approver(
            orders, amount=request_row.get('amount'), project_id=request_row.get('project_id'),
            counterparty_id=request_row.get('counterparty_id'), on_date=on_date)
        queries.set_route_basis(cursor, request_row['id'], basis)
        approver = None
        if basis.get('approver_user_id'):
            approver = {'id': basis['approver_user_id'], 'name': basis.get('approver_name')}
            note = 'Согласующий счёта: %s — по Приказу №%s вместо Учредителя' % (
                approver['name'], basis.get('order_number'))
        else:
            reasons = [ev['reason'] for ev in basis.get('evaluations') or [] if ev.get('reason')]
            note = 'Согласующий счёта: Учредитель' + ('. ' + ' '.join(reasons) if reasons else '')
        if basis.get('conflict'):
            note += '. Внимание: несколько действующих Приказов дают право разным людям — применён приоритетный'
        queries.set_step_assignee(cursor, request_row['id'], 9, approver, actor, reason=note, kind='route')
        return True, None

    # ═══════════════════════════════════════════════════════════════════════
    # Служебное
    # ═══════════════════════════════════════════════════════════════════════

    @payments_route('/ping')
    def ping(ctx):
        with db._get_cursor() as cursor:
            ready = schema.schema_ready(cursor)
            members = queries.role_members(cursor) if ready else {}
            my_roles = sorted(queries.roles_of_user(cursor, ctx['user_id'])) if ready else []
            counters = queries.state_counters(cursor, viewer_id=ctx['user_id'], viewer_roles=my_roles) if ready else {}
        return jsonify({
            'capabilities': access.capabilities(ctx),
            'me': {'id': ctx['user_id'], 'name': ctx.get('name'), 'has_telegram': ctx.get('has_telegram'),
                   'department_id': ctx.get('department_id'), 'department_name': ctx.get('department_name')},
            'schema_ready': ready,
            'storage_ready': files.storage_ready(gcs),
            'telegram_ready': callable(send_telegram),
            'roles': members,
            'roles_ready': bool(members.get('founder')) and bool(members.get('accounting')),
            'my_roles': my_roles,
            'counters': counters,
            'steps': [{'no': s['no'], 'role': s['role'], 'role_label': workflow.ROLE_LABELS[s['role']],
                       'title': s['title'], 'brief': s['brief'], 'phase': s['phase'],
                       'action': s['action'], 'files': list(s.get('files') or ()),
                       'files_required': bool(s.get('files_required')), 'fields': list(s.get('fields') or ()),
                       'can_reject': bool(s.get('can_reject')), 'returns_to': s.get('returns_to')}
                      for s in workflow.STEPS],
            'contract_threshold': float(workflow.CONTRACT_REQUIRED_OVER),
        })

    @payments_route('/users')
    def users(ctx):
        with db._get_cursor() as cursor:
            people = queries.list_users(cursor)
        return jsonify({'users': people})

    @payments_route('/dictionaries')
    def dictionaries(ctx):
        """Всё, что нужно форме заявки, одним запросом."""
        with db._get_cursor() as cursor:
            payload = {
                'projects': queries.list_projects(cursor),
                'categories': queries.list_categories(cursor),
                'legal_entities': queries.list_legal_entities(cursor),
                'counterparties': queries.list_counterparties(cursor),
                'contracts': queries.list_contracts(cursor),
                'departments': queries.list_departments(cursor),
                'manager': queries.resolve_manager(cursor, ctx['user_id']),
            }
        return jsonify(payload)

    _DICTIONARIES = ('projects', 'categories', 'legal_entities', 'counterparties', 'contracts', 'orders')

    @payments_route('/dictionaries/<name>')
    def dictionary_full(ctx, name):
        if name not in _DICTIONARIES:
            raise ApiError('Нет такого справочника', status=404)
        with db._get_cursor() as cursor:
            if name == 'projects':
                rows = queries.list_projects(cursor, include_inactive=True)
            elif name == 'categories':
                rows = queries.list_categories(cursor, include_inactive=True)
            elif name == 'legal_entities':
                rows = queries.list_legal_entities(cursor, include_inactive=True)
            elif name == 'counterparties':
                rows = queries.list_counterparties(cursor, include_inactive=True, query=request.args.get('q'))
            elif name == 'contracts':
                rows = queries.list_contracts(cursor, counterparty_id=_int(request.args.get('counterparty_id')))
            else:
                rows = queries.list_orders(cursor)
        return jsonify({'items': rows})

    @payments_route('/dictionaries/<name>', methods=('POST',), admin=True)
    def dictionary_save(ctx, name):
        if name not in _DICTIONARIES:
            raise ApiError('Нет такого справочника', status=404)
        payload = _payload()
        row_id = _int(payload.get('id'), 'id')
        with db._get_cursor() as cursor:
            if name == 'projects':
                saved = queries.upsert_project(cursor, project_id=row_id, name=_text(payload.get('name'), 'name', required=True, limit=200),
                                               is_active=_bool(payload.get('is_active', True)), actor_id=ctx['user_id'])
            elif name == 'categories':
                saved = queries.upsert_category(
                    cursor, category_id=row_id, parent_id=_int(payload.get('parent_id'), 'parent_id'),
                    name=_text(payload.get('name'), 'name', required=True, limit=200),
                    position=_int(payload.get('position'), 'position') or 0,
                    is_active=_bool(payload.get('is_active', True)), actor_id=ctx['user_id'])
            elif name == 'legal_entities':
                saved = queries.upsert_legal_entity(
                    cursor, entity_id=row_id, name=_text(payload.get('name'), 'name', required=True, limit=200),
                    bin_code=_digits(payload.get('bin')) or None, kind=_party_kind(payload.get('kind')),
                    vat_payer=_bool(payload.get('vat_payer')), note=_text(payload.get('note'), 'note', limit=2000),
                    is_active=_bool(payload.get('is_active', True)), actor_id=ctx['user_id'])
            elif name == 'counterparties':
                saved = queries.upsert_counterparty(
                    cursor, counterparty_id=row_id, name=_text(payload.get('name'), 'name', required=True, limit=200),
                    bin_code=_digits(payload.get('bin')) or None, kind=_party_kind(payload.get('kind')),
                    vat_payer=_bool(payload.get('vat_payer')),
                    requisites=_text(payload.get('requisites'), 'requisites', limit=4000),
                    contact=_text(payload.get('contact'), 'contact', limit=1000),
                    note=_text(payload.get('note'), 'note', limit=2000),
                    is_active=_bool(payload.get('is_active', True)), actor_id=ctx['user_id'])
            elif name == 'contracts':
                status = str(payload.get('status') or 'active').lower()
                if status not in schema.CONTRACT_STATUSES:
                    raise ApiError('Статус договора не из списка')
                counterparty_id = _int(payload.get('counterparty_id'), 'counterparty_id')
                if not counterparty_id:
                    raise ApiError('Укажите контрагента договора')
                starts_on, ends_on = _date(payload.get('starts_on'), 'starts_on'), _date(payload.get('ends_on'), 'ends_on')
                if starts_on and ends_on and ends_on < starts_on:
                    raise ApiError('Дата окончания договора раньше даты начала')
                saved = queries.upsert_contract(cursor, contract_id=row_id, fields={
                    'counterparty_id': counterparty_id,
                    'legal_entity_id': _int(payload.get('legal_entity_id'), 'legal_entity_id'),
                    'number': _text(payload.get('number'), 'number', required=True, limit=100),
                    'signed_on': _date(payload.get('signed_on'), 'signed_on'),
                    'starts_on': starts_on, 'ends_on': ends_on, 'status': status,
                    'subject': _text(payload.get('subject'), 'subject', limit=2000),
                    'note': _text(payload.get('note'), 'note', limit=2000),
                }, actor_id=ctx['user_id'])
            else:
                status = str(payload.get('status') or 'active').lower()
                if status not in schema.ORDER_STATUSES:
                    raise ApiError('Статус Приказа: действующий или отменён')
                delegate_id = _int(payload.get('delegate_user_id'), 'delegate_user_id')
                if not delegate_id or not queries.user_brief(cursor, delegate_id):
                    raise ApiError('Укажите сотрудника, которому передаётся право согласования')
                starts_on = _date(payload.get('starts_on'), 'starts_on') or _date(payload.get('issued_on'), 'issued_on')
                issued_on = _date(payload.get('issued_on'), 'issued_on') or starts_on
                if not issued_on:
                    raise ApiError('Укажите дату Приказа')
                ends_on = _date(payload.get('ends_on'), 'ends_on')
                if ends_on and ends_on < starts_on:
                    raise ApiError('Дата окончания Приказа раньше даты начала')
                project_ids = [x for x in (payload.get('project_ids') or []) if _int(x)]
                counterparty_ids = [x for x in (payload.get('counterparty_ids') or []) if _int(x)]
                all_projects, all_cps = _bool(payload.get('all_projects')), _bool(payload.get('all_counterparties'))
                if not all_projects and not project_ids:
                    raise ApiError('Укажите проекты Приказа или отметьте «Все проекты»')
                if not all_cps and not counterparty_ids:
                    raise ApiError('Укажите контрагентов Приказа или отметьте «Все контрагенты»')
                limit = payload.get('amount_limit')
                saved = queries.upsert_order(cursor, order_id=row_id, fields={
                    'number': _text(payload.get('number'), 'number', required=True, limit=100),
                    'issued_on': issued_on, 'starts_on': starts_on, 'ends_on': ends_on, 'status': status,
                    'replaces_role': workflow.ROLE_FOUNDER, 'delegate_user_id': delegate_id,
                    'amount_limit': workflow.to_decimal(limit) if limit not in (None, '') else None,
                    'all_projects': all_projects, 'all_counterparties': all_cps,
                    'note': _text(payload.get('note'), 'note', limit=2000),
                }, project_ids=project_ids, counterparty_ids=counterparty_ids, actor_id=ctx['user_id'])
        return jsonify({'status': 'success', 'id': saved})

    @payments_route('/dictionaries/<name>/<int:row_id>', methods=('DELETE',), admin=True)
    def dictionary_delete(ctx, name, row_id):
        if name not in _DICTIONARIES:
            raise ApiError('Нет такого справочника', status=404)
        with db._get_cursor() as cursor:
            try:
                deleted = queries.delete_dictionary_row(cursor, name, row_id)
            except Exception as exc:  # noqa: BLE001 — внешний ключ: строка уже в заявках
                if 'foreign key' in str(exc).lower() or 'violates' in str(exc).lower():
                    raise ApiError('Запись используется в заявках — снимите галочку «активна» вместо удаления',
                                   code='PAYMENTS_IN_USE', status=409)
                raise
        if not deleted:
            raise ApiError('Запись не найдена', status=404)
        return jsonify({'status': 'success'})

    @payments_route('/roles', methods=('GET',))
    def roles(ctx):
        with db._get_cursor() as cursor:
            members = queries.role_members(cursor)
        return jsonify({'roles': members, 'labels': {k: workflow.ROLE_LABELS[k] for k in schema.MEMBER_ROLES}})

    @payments_route('/roles', methods=('POST',), admin=True)
    def roles_add(ctx):
        payload = _payload()
        role_code = str(payload.get('role_code') or '').strip()
        user_id = _int(payload.get('user_id'), 'user_id')
        if role_code not in schema.MEMBER_ROLES or not user_id:
            raise ApiError('Нужны роль (founder/accounting) и сотрудник')
        with db._get_cursor() as cursor:
            if not queries.user_brief(cursor, user_id):
                raise ApiError('Сотрудник не найден', status=404)
            queries.add_role_member(cursor, role_code, user_id, ctx['user_id'])
            members = queries.role_members(cursor)
        return jsonify({'status': 'success', 'roles': members})

    @payments_route('/roles/<role_code>/<int:user_id>', methods=('DELETE',), admin=True)
    def roles_remove(ctx, role_code, user_id):
        if role_code not in schema.MEMBER_ROLES:
            raise ApiError('Нет такой роли', status=404)
        with db._get_cursor() as cursor:
            queries.remove_role_member(cursor, role_code, user_id)
            members = queries.role_members(cursor)
        return jsonify({'status': 'success', 'roles': members})

    # ═══════════════════════════════════════════════════════════════════════
    # Заявки
    # ═══════════════════════════════════════════════════════════════════════

    def _list_filters(args):
        return {
            'query': (args.get('q') or '').strip() or None,
            'state': (args.get('state') or '').strip() or None,
            'responsible_id': _int(args.get('responsible_id'), 'responsible_id'),
            'initiator_id': _int(args.get('initiator_id'), 'initiator_id'),
            'counterparty_id': _int(args.get('counterparty_id'), 'counterparty_id'),
            'project_id': _int(args.get('project_id'), 'project_id'),
            'payment_source': (args.get('payment_source') or '').strip() or None,
            'payment_type': (args.get('payment_type') or '').strip() or None,
            'fixed_only': _bool(args.get('fixed_only')),
            'phase': (args.get('phase') or '').strip() or None,
            'date_from': _date(args.get('date_from'), 'date_from'),
            'date_to': _date(args.get('date_to'), 'date_to'),
        }

    @payments_route('/requests')
    def requests_list(ctx):
        filters = _list_filters(request.args)
        limit = min(max(_int(request.args.get('limit'), 'limit') or 50, 1), PAGE_SIZE_MAX)
        offset = max(_int(request.args.get('offset'), 'offset') or 0, 0)
        with db._get_cursor() as cursor:
            my_roles = queries.roles_of_user(cursor, ctx['user_id'])
            if filters.get('responsible_id'):
                filters['responsible_roles'] = queries.roles_of_user(cursor, filters['responsible_id'])
            total, items = queries.list_requests(cursor, limit=limit, offset=offset, viewer_id=ctx['user_id'],
                                                 viewer_roles=my_roles, **filters)
            counters = queries.state_counters(cursor, viewer_id=ctx['user_id'], viewer_roles=my_roles, **filters)
        return jsonify({'items': items, 'total': total, 'counters': counters, 'limit': limit, 'offset': offset})

    @payments_route('/requests', methods=('POST',))
    def request_create(ctx):
        if not access.can_create_request(ctx):
            raise ApiError('Заводить заявки вам не разрешено', code='PAYMENTS_READ_ONLY', status=403)
        payload = _payload()
        uploads = _uploaded_files()
        actor = _actor(ctx)
        outbox, warnings = [], []
        with db._get_cursor() as cursor:
            members = queries.role_member_ids(cursor)
            if not members.get('founder') or not members.get('accounting'):
                raise ApiError('Сначала назначьте участников процесса: Учредителя и Бухгалтерию '
                               '(вкладка «Участники»)', code='PAYMENTS_ROLES_NOT_CONFIGURED', status=409)
            fields, items, manager = _validate_request(cursor, payload, ctx, creating=True)
            if not manager and 'manager_id' not in payload:
                manager = queries.resolve_manager(cursor, ctx['user_id'])
            initiator = {'id': ctx['user_id'], 'name': ctx.get('name')}
            route = workflow.build_route(initiator=initiator, manager=manager)
            request_id = queries.create_request(cursor, fields=fields, items=items, actor=actor,
                                                initiator=initiator, manager=manager, route=route)
            _store_files(cursor, request_id, uploads, 1, actor)
            submitted = False
            if _bool(payload.get('submit')):
                attachments = [a for a in queries.list_attachments(cursor, request_id) if a['step_no'] == 1]
                current = queries.read_request(cursor, request_id)
                missing = workflow.missing_requirements(1, current, attachments)
                if missing:
                    warnings.append('Заявка создана, но не отправлена: ' + '; '.join(missing))
                else:
                    next_no = queries.complete_step(cursor, request_id, 1, actor, _text(payload.get('comment'), 'comment'))
                    submitted = True
                    if next_no:
                        _queue_step(cursor, queries.read_request(cursor, request_id), next_no, outbox,
                                    exclude=[ctx['user_id']])
            full = _full(cursor, request_id, ctx)
        warnings += _flush(outbox)
        full.update({'status': 'success', 'submitted': submitted, 'warnings': warnings})
        return jsonify(full, 201)

    @payments_route('/requests/<int:request_id>')
    def request_read(ctx, request_id):
        with db._get_cursor() as cursor:
            full = _full(cursor, request_id, ctx)
        return jsonify(full)

    @payments_route('/requests/<int:request_id>', methods=('PATCH',))
    def request_edit(ctx, request_id):
        payload = _payload()
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            current = queries.read_request(cursor, request_id, lock=True)
            if not current:
                raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
            if not access.can_edit_request(ctx, current):
                raise ApiError('Править заявку может инициатор, пока она не оплачена', code='PAYMENTS_FORBIDDEN', status=403)
            fields, items, manager = _validate_request(cursor, payload, ctx, creating=False, current=current)
            if 'manager_id' in fields:
                step2 = next((s for s in queries.list_steps(cursor, request_id) if s['step_no'] == 2), None)
                if step2 and step2['state'] == 'current' and not manager:
                    raise ApiError('Заявка сейчас у руководителя — сначала укажите другого руководителя',
                                   code='PAYMENT_REQUEST_INVALID')
                cursor.execute("UPDATE payment_requests SET manager_name = %s WHERE id = %s",
                               ((manager or {}).get('name'), request_id))
                if step2 and step2['state'] in ('pending', 'current', 'skipped') and step2.get('assignee_id') != fields['manager_id']:
                    if manager:
                        if step2['state'] == 'skipped':
                            cursor.execute("UPDATE payment_request_steps SET state = 'pending', comment = NULL "
                                           "WHERE request_id = %s AND step_no = 2", (request_id,))
                        queries.set_step_assignee(cursor, request_id, 2, manager, actor,
                                                  reason='Руководитель изменён в заявке')
                    elif step2['state'] == 'pending':
                        # Руководителя сняли до его шага — шаг пропускается, как у заявки без руководителя.
                        cursor.execute("UPDATE payment_request_steps SET state = 'skipped', assignee_id = NULL, "
                                       "assignee_name = NULL, comment = %s WHERE request_id = %s AND step_no = 2",
                                       ('Шаг пропущен: у инициатора не определён непосредственный руководитель', request_id))
                        queries.log_event(cursor, request_id, 'step_skipped', actor, step_no=2,
                                          comment='Руководитель снят в заявке')
            changes = queries.update_request_fields(cursor, request_id, fields)
            if items is not None:
                before = [(i['name'], str(i['quantity']), str(i['unit_price'])) for i in queries.list_items(cursor, request_id)]
                after = [(i['name'], str(i['quantity']), str(i['unit_price'])) for i in items]
                if before != after:
                    total = queries.replace_items(cursor, request_id, items)
                    changes['items'] = (len(before), len(after))
                    changes['amount'] = (queries.plain(current.get('amount')), float(total))
            if changes:
                queries.log_event(cursor, request_id, 'edited', actor, payload={'changes': changes})
            refreshed = queries.read_request(cursor, request_id)
            # Блок «ожидает договор» снимается, как только данные ему больше не противоречат.
            if refreshed.get('block_code') == workflow.BLOCK_CONTRACT_REQUIRED:
                contract = queries.read_contract(cursor, refreshed.get('contract_id'))
                ok, _reason = workflow.contract_gate(refreshed.get('amount'), contract,
                                                     counterparty_id=refreshed.get('counterparty_id'),
                                                     on_date=refreshed.get('invoice_date') or queries.today_almaty())
                if ok:
                    queries.clear_block(cursor, request_id, actor)
            full = _full(cursor, request_id, ctx)
        full['status'] = 'success'
        return jsonify(full)

    @payments_route('/requests/<int:request_id>', methods=('DELETE',), admin=True)
    def request_delete(ctx, request_id):
        with db._get_cursor() as cursor:
            if not queries.read_request(cursor, request_id):
                raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
            refs = queries.delete_request(cursor, request_id)
        files.drop(gcs, refs) if gcs.get('client') else None
        return jsonify({'status': 'success'})

    # ── Шаги ────────────────────────────────────────────────────────────────

    def _load_for_action(cursor, ctx, request_id, step_no):
        current = queries.read_request(cursor, request_id, lock=True)
        if not current:
            raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
        if current['status'] != 'active':
            raise ApiError('Заявка уже закрыта', code='PAYMENT_REQUEST_CLOSED', status=409)
        steps = queries.list_steps(cursor, request_id)
        step_row = next((s for s in steps if s['step_no'] == step_no), None)
        if not step_row or step_row['state'] != 'current':
            raise ApiError('Сейчас заявка на шаге %s, а не %s — обновите страницу'
                           % (current['current_step'], step_no), code='PAYMENT_STEP_MISMATCH', status=409)
        members = queries.role_member_ids(cursor)
        if not access.can_act_on_step(ctx, step_row, members):
            raise ApiError('На этом шаге отписывается %s' % (step_row.get('assignee_name') or step_row['role_label']),
                           code='PAYMENTS_NOT_YOUR_STEP', status=403)
        return current, step_row

    @payments_route('/requests/<int:request_id>/steps/<int:step_no>/complete', methods=('POST',))
    def step_complete(ctx, request_id, step_no):
        payload = _payload()
        uploads = _uploaded_files()
        actor = _actor(ctx)
        comment = _text(payload.get('comment'), 'comment')
        outbox = []
        with db._get_cursor() as cursor:
            current, step_row = _load_for_action(cursor, ctx, request_id, step_no)

            # Поля шага — то, что ответственный вписывает при отписке.
            step_fields = {}
            if step_no == 5 and 'invoice_requisites' in payload:
                step_fields['invoice_requisites'] = _text(payload.get('invoice_requisites'), 'invoice_requisites')
            if step_no == 7:
                for key in ('invoice_description', 'invoice_number'):
                    if key in payload:
                        step_fields[key] = _text(payload.get(key), key)
                if 'invoice_date' in payload:
                    step_fields['invoice_date'] = _date(payload.get('invoice_date'), 'invoice_date')
                for key in ('legal_entity_id', 'contract_id'):
                    if key in payload:
                        step_fields[key] = _int(payload.get(key), key)
                for key in ('needs_power_of_attorney', 'needs_payment_order'):
                    if key in payload:
                        step_fields[key] = _bool(payload.get(key))
                if step_fields.get('contract_id'):
                    contract = queries.read_contract(cursor, step_fields['contract_id'])
                    if not contract or contract['counterparty_id'] != current.get('counterparty_id'):
                        raise ApiError('Договор заключён с другим контрагентом', code='PAYMENT_REQUEST_INVALID')
            if step_no == 8 and 'previous_payment_note' in payload:
                step_fields['previous_payment_note'] = _text(payload.get('previous_payment_note'), 'previous_payment_note')
            if step_no == 10:
                step_fields['paid_on'] = _date(payload.get('paid_on'), 'paid_on') or queries.today_almaty()
                amount = payload.get('paid_amount')
                step_fields['paid_amount'] = workflow.to_decimal(amount) if amount not in (None, '') else workflow.to_decimal(current.get('amount'))
            if step_fields:
                changes = queries.update_request_fields(cursor, request_id, step_fields)
                if changes:
                    queries.log_event(cursor, request_id, 'edited', actor, step_no=step_no, payload={'changes': changes})
            _store_files(cursor, request_id, uploads, step_no, actor)

            refreshed = queries.read_request(cursor, request_id)
            attachments = [a for a in queries.list_attachments(cursor, request_id) if a['step_no'] == step_no]
            missing = workflow.missing_requirements(step_no, refreshed, attachments)
            if missing:
                raise ApiError('; '.join(missing), code='PAYMENT_STEP_INCOMPLETE', extra={'missing': missing})

            if step_no == 7:
                ok, reason = _apply_invoice_route(cursor, refreshed, actor)
                if not ok:
                    _queue_to_initiator(cursor, refreshed, notify.blocked_message(refreshed, reason), outbox,
                                        exclude=[ctx['user_id']])
                    full = _full(cursor, request_id, ctx)
                    full.update({'status': 'blocked', 'code': 'CONTRACT_REQUIRED', 'error': reason})
                    warnings = _flush(outbox)
                    full['warnings'] = warnings
                    return jsonify(full, 409)

            next_no = queries.complete_step(cursor, request_id, step_no, actor, comment)
            refreshed = queries.read_request(cursor, request_id)
            if next_no:
                _queue_step(cursor, refreshed, next_no, outbox, exclude=[ctx['user_id']])
            else:
                _queue_to_initiator(cursor, refreshed, notify.done_message(refreshed), outbox, exclude=[ctx['user_id']])
            full = _full(cursor, request_id, ctx)
        full.update({'status': 'success', 'next_step': next_no, 'warnings': _flush(outbox)})
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/steps/<int:step_no>/return', methods=('POST',))
    def step_return(ctx, request_id, step_no):
        payload = _payload()
        comment = _text(payload.get('comment'), 'comment', required=True)
        actor = _actor(ctx)
        outbox = []
        with db._get_cursor() as cursor:
            current, step_row = _load_for_action(cursor, ctx, request_id, step_no)
            to_step = step_row.get('returns_to')
            if not to_step:
                raise ApiError('С этого шага заявку не возвращают', code='PAYMENT_STEP_NO_RETURN', status=409)
            queries.return_to_step(cursor, request_id, to_step, actor, comment)
            refreshed = queries.read_request(cursor, request_id)
            _queue_to_initiator(cursor, refreshed, notify.returned_message(
                refreshed, to_step=to_step, by_name=ctx.get('name'), comment=comment), outbox, exclude=[ctx['user_id']])
            full = _full(cursor, request_id, ctx)
        full.update({'status': 'success', 'warnings': _flush(outbox)})
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/steps/<int:step_no>/reject', methods=('POST',))
    def step_reject(ctx, request_id, step_no):
        payload = _payload()
        comment = _text(payload.get('comment'), 'comment', required=True)
        actor = _actor(ctx)
        outbox = []
        with db._get_cursor() as cursor:
            current, step_row = _load_for_action(cursor, ctx, request_id, step_no)
            if not step_row.get('can_reject'):
                raise ApiError('На этом шаге заявку не отклоняют — только возвращают', code='PAYMENT_STEP_NO_REJECT', status=409)
            queries.close_request(cursor, request_id, 'rejected', actor, comment)
            refreshed = queries.read_request(cursor, request_id)
            _queue_to_initiator(cursor, refreshed, notify.rejected_message(refreshed, by_name=ctx.get('name'), comment=comment),
                                outbox, exclude=[ctx['user_id']])
            full = _full(cursor, request_id, ctx)
        full.update({'status': 'success', 'warnings': _flush(outbox)})
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/cancel', methods=('POST',))
    def request_cancel(ctx, request_id):
        payload = _payload()
        comment = _text(payload.get('comment'), 'comment')
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            current = queries.read_request(cursor, request_id, lock=True)
            if not current:
                raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
            if not access.can_cancel_request(ctx, current):
                raise ApiError('Отменить можно только свою неоплаченную заявку', code='PAYMENTS_FORBIDDEN', status=403)
            queries.close_request(cursor, request_id, 'cancelled', actor, comment)
            full = _full(cursor, request_id, ctx)
        full['status'] = 'success'
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/steps/<int:step_no>/assignee', methods=('POST',), admin=True)
    def step_assignee(ctx, request_id, step_no):
        payload = _payload()
        user_id = _int(payload.get('user_id'), 'user_id')
        actor = _actor(ctx)
        outbox = []
        with db._get_cursor() as cursor:
            current = queries.read_request(cursor, request_id, lock=True)
            if not current or current['status'] != 'active':
                raise ApiError('Заявка не найдена или закрыта', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
            step_row = next((s for s in queries.list_steps(cursor, request_id) if s['step_no'] == step_no), None)
            if not step_row or step_row['state'] == 'done':
                raise ApiError('Этот шаг уже пройден', code='PAYMENT_STEP_DONE', status=409)
            user = queries.user_brief(cursor, user_id) if user_id else None
            if user_id and not user:
                raise ApiError('Сотрудник не найден', status=404)
            if step_row['state'] == 'skipped':
                cursor.execute("UPDATE payment_request_steps SET state = 'pending', comment = NULL "
                               "WHERE request_id = %s AND step_no = %s", (request_id, step_no))
            queries.set_step_assignee(cursor, request_id, step_no, user, actor,
                                      reason=_text(payload.get('reason'), 'comment'))
            refreshed = queries.read_request(cursor, request_id)
            if step_row['state'] == 'current' and user:
                _queue_step(cursor, refreshed, step_no, outbox, exclude=[ctx['user_id']])
            full = _full(cursor, request_id, ctx)
        full.update({'status': 'success', 'warnings': _flush(outbox)})
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/refund', methods=('POST',))
    def request_refund(ctx, request_id):
        payload = _payload()
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            current = queries.read_request(cursor, request_id, lock=True)
            if not current:
                raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
            members = queries.role_member_ids(cursor)
            paid = int(current.get('current_step') or 0) > 10 or current.get('status') == 'done'
            if not paid:
                raise ApiError('Возврат отмечают только по оплаченной заявке', code='PAYMENT_NOT_PAID', status=409)
            if not (access.is_section_admin(ctx) or ctx['user_id'] in (members.get('accounting') or set())):
                raise ApiError('Возврат отмечает бухгалтерия', code='PAYMENTS_FORBIDDEN', status=403)
            amount = workflow.to_decimal(payload.get('refund_amount'))
            if amount < 0 or amount > workflow.to_decimal(current.get('paid_amount') or current.get('amount')):
                raise ApiError('Сумма возврата не может превышать оплаченную')
            fields = {'refund_amount': amount if amount > 0 else None,
                      'refund_on': _date(payload.get('refund_on'), 'refund_on') if amount > 0 else None}
            queries.update_request_fields(cursor, request_id, fields)
            queries.log_event(cursor, request_id, 'refund', actor, comment=_text(payload.get('comment'), 'comment'),
                              payload={'refund_amount': amount, 'refund_on': fields['refund_on']})
            full = _full(cursor, request_id, ctx)
        full['status'] = 'success'
        return jsonify(full)

    # ── Вложения ────────────────────────────────────────────────────────────

    @payments_route('/requests/<int:request_id>/attachments', methods=('POST',))
    def attachments_add(ctx, request_id):
        uploads = _uploaded_files()
        if not uploads:
            raise ApiError('Файлы не переданы', code='PAYMENT_FILES_MISSING')
        step_no = _int(request.form.get('step_no'), 'step_no')
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            current = queries.read_request(cursor, request_id, lock=True)
            if not current:
                raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
            steps = queries.list_steps(cursor, request_id)
            members = queries.role_member_ids(cursor)
            current_step = next((s for s in steps if s['state'] == 'current'), None)
            allowed = access.can_edit_request(ctx, current) or (
                current_step and access.can_act_on_step(ctx, current_step, members))
            if not allowed:
                raise ApiError('Прикладывать файлы может инициатор или ответственный текущего шага',
                               code='PAYMENTS_FORBIDDEN', status=403)
            _store_files(cursor, request_id, uploads, step_no or (current_step or {}).get('step_no'), actor)
            full = _full(cursor, request_id, ctx)
        full['status'] = 'success'
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/attachments/<int:attachment_id>', methods=('DELETE',))
    def attachments_remove(ctx, request_id, attachment_id):
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            item = queries.read_attachment(cursor, attachment_id)
            if not item or item['request_id'] != request_id:
                raise ApiError('Файл не найден', status=404)
            current = queries.read_request(cursor, request_id)
            steps = queries.list_steps(cursor, request_id)
            step_row = next((s for s in steps if s['step_no'] == item['step_no']), None)
            own_open = (item.get('uploaded_by') == ctx['user_id'] and step_row and step_row['state'] == 'current')
            if not (access.is_section_admin(ctx) or own_open):
                raise ApiError('Снять можно только свой файл на текущем шаге', code='PAYMENTS_FORBIDDEN', status=403)
            queries.remove_attachment(cursor, attachment_id, actor)
            full = _full(cursor, request_id, ctx)
        if gcs.get('client'):
            files.drop(gcs, [(item['bucket'], item['blob_path'])])
        full['status'] = 'success'
        return jsonify(full)

    @payments_route('/attachments/<int:attachment_id>/download')
    def attachment_download(ctx, attachment_id):
        with db._get_cursor() as cursor:
            item = queries.read_attachment(cursor, attachment_id)
        if not item:
            raise ApiError('Файл не найден', status=404)
        if not gcs.get('client'):
            raise ApiError('Хранилище файлов не настроено', code='PAYMENT_STORAGE_OFF', status=503)
        data = files.download(gcs, item['bucket'], item['blob_path'])
        if data is None:
            raise ApiError('Файла нет в хранилище', status=404)
        inline = _bool(request.args.get('inline'))
        return send_file(BytesIO(data), as_attachment=not inline, download_name=item['file_name'],
                         mimetype=item.get('content_type') or 'application/octet-stream')

    # ── Справки ─────────────────────────────────────────────────────────────

    @payments_route('/history')
    def history(ctx):
        with db._get_cursor() as cursor:
            rows = queries.payment_history(
                cursor, counterparty_id=_int(request.args.get('counterparty_id')),
                category_id=_int(request.args.get('category_id')),
                subcategory_id=_int(request.args.get('subcategory_id')),
                expense_name=request.args.get('name'),
                exclude_request_id=_int(request.args.get('exclude')))
        return jsonify({'items': rows})

    @payments_route('/route-preview')
    def route_preview(ctx):
        """Кто согласует счёт при таких параметрах — до отправки на шаг 9."""
        on_date = _date(request.args.get('on_date'), 'on_date') or queries.today_almaty()
        with db._get_cursor() as cursor:
            orders = queries.list_orders(cursor, only_active=True)
            contract = queries.read_contract(cursor, _int(request.args.get('contract_id')))
        amount = workflow.to_decimal(request.args.get('amount'))
        basis = workflow.resolve_invoice_approver(
            orders, amount=amount, project_id=_int(request.args.get('project_id')),
            counterparty_id=_int(request.args.get('counterparty_id')), on_date=on_date)
        ok, reason = workflow.contract_gate(amount, contract, counterparty_id=_int(request.args.get('counterparty_id')),
                                            on_date=on_date)
        return jsonify({'route': basis, 'contract_check': {'ok': ok, 'reason': reason}})

    # ── Выгрузка ────────────────────────────────────────────────────────────

    @payments_route('/export')
    def export(ctx):
        filters = _list_filters(request.args)
        with db._get_cursor() as cursor:
            my_roles = queries.roles_of_user(cursor, ctx['user_id'])
            if filters.get('responsible_id'):
                filters['responsible_roles'] = queries.roles_of_user(cursor, filters['responsible_id'])
            total, rows = queries.list_requests(cursor, limit=EXPORT_MAX_ROWS, offset=0, viewer_id=ctx['user_id'],
                                                viewer_roles=my_roles, **filters)
            items_by_request = queries.items_for_requests(cursor, [r['id'] for r in rows])
        described = [key for key, value in filters.items() if value not in (None, '', False)]
        stream = report.build_workbook(
            rows, items_by_request, filters_text=', '.join(described) if described else '',
            total=total, truncated=total > len(rows), text_warning_patch=excel_text_warning)
        return send_file(stream, as_attachment=True, download_name=report.file_name(), mimetype=XLSX_MIME)

    # ═══════════════════════════════════════════════════════════════════════
    # Календарь фиксированных платежей
    # ═══════════════════════════════════════════════════════════════════════

    @payments_route('/templates')
    def templates_list(ctx):
        with db._get_cursor() as cursor:
            rows = queries.list_templates(cursor)
        today = queries.today_almaty()
        for row in rows:
            due = workflow._as_date(row.get('next_due_on'))
            row['generate_on'] = fixed.generate_on(due, row['periodicity'], row.get('lead_days')) if due else None
            row['is_due'] = fixed.is_due(row, today)
            row['periodicity_label'] = fixed.PERIOD_LABELS.get(row['periodicity'], row['periodicity'])
        return jsonify({'items': rows})

    def _validate_template(cursor, payload):
        periodicity = str(payload.get('periodicity') or 'monthly').lower()
        if periodicity not in schema.PERIODICITIES:
            raise ApiError('Периодичность не из списка')
        interval_days = _int(payload.get('interval_days'), 'interval_days')
        if periodicity == 'custom' and not (interval_days and interval_days > 0):
            raise ApiError('Для своего интервала укажите число дней')
        amount = workflow.to_decimal(payload.get('amount'))
        if amount <= 0:
            raise ApiError('Сумма платежа должна быть больше нуля')
        next_due_on = _date(payload.get('next_due_on'), 'next_due_on')
        if not next_due_on:
            raise ApiError('Укажите ближайшую дату оплаты')
        responsible_id = _int(payload.get('responsible_user_id'), 'responsible_user_id')
        if not responsible_id or not queries.user_brief(cursor, responsible_id):
            raise ApiError('Укажите ответственного за платёж')
        source = payload.get('payment_source')
        if source and source not in schema.PAYMENT_SOURCES:
            raise ApiError('Источник оплаты не из списка')
        return {
            'name': _text(payload.get('name'), 'name', required=True, limit=300),
            'amount': amount, 'periodicity': periodicity,
            'interval_days': interval_days if periodicity == 'custom' else None,
            'next_due_on': next_due_on,
            'lead_days': max(0, _int(payload.get('lead_days'), 'lead_days') or 7),
            'project_id': _int(payload.get('project_id'), 'project_id'),
            'branch': _text(payload.get('branch'), 'branch'),
            'responsible_user_id': responsible_id,
            'category_id': _int(payload.get('category_id'), 'category_id'),
            'subcategory_id': _int(payload.get('subcategory_id'), 'subcategory_id'),
            'counterparty_id': _int(payload.get('counterparty_id'), 'counterparty_id'),
            'legal_entity_id': _int(payload.get('legal_entity_id'), 'legal_entity_id'),
            'payment_source': source or None,
            'note': _text(payload.get('note'), 'notes'),
            'is_active': _bool(payload.get('is_active', True)),
        }

    @payments_route('/templates', methods=('POST',), admin=True)
    def templates_save(ctx):
        payload = _payload()
        with db._get_cursor() as cursor:
            fields = _validate_template(cursor, payload)
            template_id = queries.upsert_template(cursor, template_id=_int(payload.get('id'), 'id'),
                                                  fields=fields, actor_id=ctx['user_id'])
            row = queries.read_template(cursor, template_id)
        return jsonify({'status': 'success', 'item': row})

    @payments_route('/templates/<int:template_id>', methods=('DELETE',), admin=True)
    def templates_delete(ctx, template_id):
        with db._get_cursor() as cursor:
            deleted = queries.delete_template(cursor, template_id)
        if not deleted:
            raise ApiError('Платёж не найден', status=404)
        return jsonify({'status': 'success'})

    @payments_route('/templates/generate', methods=('POST',), admin=True)
    def templates_generate(ctx):
        """Ручной запуск того же, что делает ночной планировщик."""
        with db._get_cursor() as cursor:
            created, outbox = fixed.generate(cursor, actor=_actor(ctx), base_url=web_app_base_url)
        return jsonify({'status': 'success', 'created': created, 'warnings': _flush(outbox)})

    @payments_route('/templates/<int:template_id>/generate', methods=('POST',), admin=True)
    def template_generate_now(ctx, template_id):
        """Создать заявку по платежу сейчас, не дожидаясь начала периода."""
        with db._get_cursor() as cursor:
            template = queries.read_template(cursor, template_id, lock=True)
            if not template:
                raise ApiError('Платёж не найден', status=404)
            outbox = []
            request_id = fixed.create_from_template(cursor, template, actor=_actor(ctx),
                                                    today=queries.today_almaty(), outbox=outbox,
                                                    base_url=web_app_base_url)
        return jsonify({'status': 'success', 'request_id': request_id, 'warnings': _flush(outbox)})

    @payments_route('/templates/import', methods=('POST',), admin=True)
    def templates_import_preview(ctx):
        storage = request.files.get('file')
        if not storage or not storage.filename:
            raise ApiError('Приложите файл xlsx', code='PAYMENT_FILES_MISSING')
        data = storage.read() or b''
        if len(data) > files.MAX_BYTES:
            raise files.FileError('Файл больше 10 МБ', code='PAYMENT_FILE_TOO_LARGE')
        try:
            rows = fixed.parse_workbook(data)
        except ValueError as exc:
            raise ApiError(str(exc), code='PAYMENT_IMPORT_INVALID')
        except Exception:  # noqa: BLE001
            raise ApiError('Не удалось прочитать файл: нужен xlsx', code='PAYMENT_IMPORT_INVALID')
        with db._get_cursor() as cursor:
            rows = fixed.resolve_import(cursor, rows, ctx['user_id'])
        return jsonify({'rows': rows, 'ready': sum(1 for r in rows if not r['errors']),
                        'failed': sum(1 for r in rows if r['errors'])})

    @payments_route('/templates/import/confirm', methods=('POST',), admin=True)
    def templates_import_confirm(ctx):
        payload = _payload()
        rows = payload.get('rows') or []
        if not isinstance(rows, list) or not rows:
            raise ApiError('Нет строк для импорта')
        for row in rows:
            row['errors'] = list(row.get('errors') or [])
            row['amount'] = workflow.to_decimal(row.get('amount'))
            row['next_due_on'] = workflow._as_date(row.get('next_due_on'))
            if not row.get('name'):
                row['errors'].append('нет наименования')
            if row['amount'] <= 0:
                row['errors'].append('сумма не разобрана')
            if not row['next_due_on']:
                row['errors'].append('дата оплаты не разобрана')
            if row.get('periodicity') not in schema.PERIODICITIES:
                row['periodicity'] = 'monthly'
            if not _int(row.get('responsible_user_id')):
                row['errors'].append('не определён ответственный')
        with db._get_cursor() as cursor:
            created = fixed.commit_import(cursor, rows, ctx['user_id'])
        return jsonify({'status': 'success', 'created': len(created),
                        'skipped': sum(1 for r in rows if r.get('errors'))})

    @payments_route('/templates/sample')
    def templates_sample(ctx):
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Фиксированные платежи'
        for row in fixed.template_sample_rows():
            sheet.append(list(row))
        for column, width in zip('ABCDEFGHIJK', (32, 12, 16, 14, 22, 24, 16, 16, 24, 16, 24)):
            sheet.column_dimensions[column].width = width
        stream = BytesIO()
        workbook.save(stream)
        stream.seek(0)
        return send_file(stream, as_attachment=True, download_name='Календарь фиксированных платежей.xlsx',
                         mimetype=XLSX_MIME)

    return bp


def _party_kind(value):
    text = str(value or '').strip().lower()
    if not text:
        return None
    if text in schema.PARTY_KINDS:
        return text
    raise ApiError('Форма: ТОО, ИП или другое')
