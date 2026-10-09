"""HTTP-эндпоинты раздела «Оплата счетов» (Flask Blueprint, /api/payments).

Blueprint собирается фабрикой и получает зависимости аргументами, а не
импортирует bot_schedule2: тот сам подключает этот модуль (как вики, обращения,
посылки). Соглашения те же: методы всегда включают OPTIONS и первым делом
отдаётся preflight, авторизация — общий require_api_key, ошибка —
{"error": "...", "code": "..."} с осмысленным кодом.

Здесь — заявка и всё, что с ней делают: создание и правка, отправка, действия
подзадач, вложения, доски, рабочий стол, реестр и выгрузка. Справочники,
участники и регулярные платежи — в routes_directory.py, имущество — в
routes_assets.py. Правила процесса — в workflow.py, ход по маршруту — в flow.py;
здесь только разбор запроса, проверки прав и коды ответов.

Про Telegram. Сообщения собираются ВНУТРИ транзакции в `outbox`, а уходят
ПОСЛЕ коммита: иначе человек получил бы ссылку на заявку, которой в базе ещё
нет (или которая откатилась). Отказ Telegram не ломает ответ — он приходит
в `warnings`.

Про файлы. Обязательные вложения проверяются ДО загрузки в хранилище — по
видам присланных файлов: иначе отказ «не хватает номера счёта» оставлял бы в
бакете файл, на который не ссылается ни одна запись.
"""

import logging
import re
from decimal import Decimal
from functools import wraps
from io import BytesIO

from flask import Blueprint, g, request, send_file

from . import (access, assets as assets_sql, cards, directory, files, fixed, flow, legacy, notices, notify,
               privacy, queries, report, routes_assets, routes_directory, schema, workflow)
from .web import (ApiError, XLSX_MIME, actor as _actor, as_bool, as_choice, as_date, as_int, as_money, as_text,
                  jsonify, payload as _payload, uploaded_files)

PAGE_SIZE_MAX = 200
EXPORT_MAX_ROWS = 5000
# Доска отдаёт каждую колонку порцией (как доска «Задач»: по 20, 40 или 60 —
# выбирает человек), счётчик колонки — полный. Остальное листают в окне колонки
# запросами `column` + `offset`.
BOARD_PAGE_DEFAULT = 20
BOARD_PAGE_MAX = 100
# «Мои задачи» — страницами. Без `limit` стол отдаётся целиком, до этого предела.
DESK_PAGE_MAX = 200

_LINK_RE = re.compile(r'^https?://', re.IGNORECASE)


def as_link(value):
    """Ссылка на товар: только http(s). Без схемы — дописываем https://."""
    text = str(value or '').strip()
    if not text:
        return None
    if len(text) > 1000:
        raise ApiError('Ссылка длиннее 1000 символов')
    if _LINK_RE.match(text):
        return text
    if re.match(r'^[a-z][a-z0-9+.-]*:', text, re.IGNORECASE) or ' ' in text:
        raise ApiError('Ссылка должна начинаться с http:// или https://', code='PAYMENT_REQUEST_INVALID')
    return 'https://' + text


class Api:
    """То, что нужно модулям с ручками: обёртка маршрута, база, хранилище, отправка."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


# ─── Фабрика ─────────────────────────────────────────────────────────────────

def build_payments_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                             send_telegram=None, web_app_base_url=None, excel_text_warning=None,
                             gcs=None):
    """Собирает Blueprint раздела.

    send_telegram — (chat_id, text_html, reply_markup) -> response, из монолита.
    Без него уведомления в Telegram просто не уходят (тесты, локальный стенд).
    gcs — {'bucket_name': () -> str, 'client': () -> Client}; без него вложения
    отвечают 503, а остальной раздел работает.
    """
    gcs = gcs or {}
    bp = Blueprint('payments', __name__, url_prefix='/api/payments')

    def payments_route(rule, methods=('GET',), admin=False, open_to_all=False):
        """Маршрут раздела. `open_to_all` — ручка вне периметра раздела: ею
        пользуется профиль любого сотрудника («моё имущество»)."""
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
                        if ctx:
                            # Роли процесса — под SAVEPOINT: если схема раздела не
                            # развернулась, /ping должен об этом сказать, а не упасть.
                            cursor.execute('SAVEPOINT payments_roles')
                            try:
                                ctx['roles'] = queries.roles_of_user(cursor, requester_id)
                                cursor.execute('RELEASE SAVEPOINT payments_roles')
                            except Exception:  # noqa: BLE001
                                cursor.execute('ROLLBACK TO SAVEPOINT payments_roles')
                                ctx['roles'] = set()
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}, 404)
                    # Гейт раздела — на КАЖДОМ роуте: спрятанный пункт меню
                    # доступом не является, раздел открывается и прямым адресом.
                    if not open_to_all and not access.can_open_section(ctx):
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
                except flow.FlowError as exc:
                    body = {"error": str(exc), "code": exc.code}
                    if exc.missing:
                        body['missing'] = exc.missing
                    return jsonify(body, exc.status)
                except files.FileError as exc:
                    return jsonify({"error": str(exc), "code": exc.code}, exc.status)
                except Exception:  # noqa: BLE001
                    # Текст исключения — только в журнал: в нём имена таблиц и значения ключей.
                    logging.exception('payments: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Оплата счетов»"}, 500)
                finally:
                    _drop_orphans(g.pop('payments_uploaded', None))

            return wrapper

        return decorator

    def _drop_orphans(uploaded):
        """Стирает из хранилища файлы запроса, от которых в базе не осталось записи.

        Файл кладётся в хранилище раньше, чем действие проверено до конца: отказ
        («инвентарный номер занят») откатывает запись о вложении, а блоб остался бы
        навсегда. Сверяемся с базой, а не с тем, упал ли запрос: так не сотрём
        файл, запись о котором уже зафиксирована.
        """
        if not uploaded:
            return
        try:
            with db._get_cursor() as cursor:
                cursor.execute("SELECT blob_path FROM payment_attachments WHERE blob_path = ANY(%s)",
                               ([blob for _bucket, blob in uploaded],))
                kept = {row[0] for row in cursor.fetchall()}
            files.drop(gcs, [ref for ref in uploaded if ref[1] not in kept])
        except Exception:  # noqa: BLE001 — уборка не должна ломать ответ
            logging.warning('payments: не удалось убрать файлы незавершённого запроса', exc_info=True)

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

    # ── Файлы ───────────────────────────────────────────────────────────────

    def _check_uploads(uploads):
        if len(uploads) > files.MAX_FILES:
            raise files.FileError('Не больше %s файлов за раз' % files.MAX_FILES)
        for storage, _kind, _offer in uploads:
            data = storage.read() or b''
            storage.stream.seek(0)
            files.check_file(filename=storage.filename, content_type=storage.mimetype, size=len(data))

    def _store_files(cursor, request_id, uploads, actor, *, subtask_kind=None, offer_ids=None):
        """Кладёт файлы в хранилище и записывает вложения. Возвращает их виды."""
        if not uploads:
            return []
        _check_uploads(uploads)
        stored = []
        for storage, kind, offer_index in uploads:
            data = storage.read() or b''
            bucket, blob_path = files.upload(gcs, data=data, filename=storage.filename,
                                             content_type=storage.mimetype)
            g.setdefault('payments_uploaded', []).append((bucket, blob_path))
            offer_id = None
            if offer_ids and offer_index is not None and 0 <= offer_index < len(offer_ids):
                offer_id = offer_ids[offer_index]
            queries.add_attachment(
                cursor, request_id, kind=kind, file_name=files.safe_name(storage.filename),
                content_type=storage.mimetype or 'application/octet-stream', file_size=len(data),
                bucket=bucket, blob_path=blob_path, actor=actor, subtask_kind=subtask_kind, offer_id=offer_id)
            stored.append(kind)
        return stored

    # ── Видимость и права ───────────────────────────────────────────────────

    def _visible(cursor, ctx, request_row):
        # Участие в заявке (своя подзадача, личная или по роли) спрашиваем у базы,
        # только когда правило не ответило без неё.
        if access.can_see_request(ctx, request_row, ctx['roles']):
            return True
        return access.can_see_request(
            ctx, request_row, ctx['roles'],
            participant=queries.participates(cursor, request_row['id'], ctx['user_id'], ctx['roles']))

    def _history_scope(ctx):
        """Чьи прошлые оплаты показывать: бухгалтерии и администратору — все,
        остальным — по заявкам, которые они видят и в реестре."""
        if access.sees_all_requests(ctx, ctx['roles']):
            return {}
        return {'visible_to': ctx['user_id'], 'visible_roles': ctx['roles']}

    def _load_visible(cursor, ctx, request_id, *, lock=False):
        request_row = queries.read_request(cursor, request_id, lock=lock)
        if not request_row or not _visible(cursor, ctx, request_row):
            # Чужая заявка отвечает так же, как несуществующая: номер заявки не
            # должен подтверждаться тому, кому она не видна.
            raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
        return request_row

    def _permissions(ctx, request_row, subtasks, members):
        active = request_row.get('status') == 'active'
        current = next((s for s in subtasks if s['state'] == 'open'), None) if active else None
        can_act = bool(current and access.can_act_on_subtask(ctx, current, members, request_row))
        directly = bool(current and access.acts_directly(ctx, current, members))
        return {
            'can_act': can_act,
            'acting_as_admin': bool(can_act and not directly),
            'can_edit': access.can_edit_request(ctx, request_row),
            'can_cancel': access.can_cancel_request(ctx, request_row),
            'can_reassign': bool(access.is_section_admin(ctx) and active),
            'can_delete': access.can_delete_request(ctx),
            'can_refund': access.can_refund(ctx, request_row, ctx['roles']),
            'can_attach': access.can_attach(ctx, request_row, ctx['roles'], can_act),
            'can_view_card': bool(request_row.get('has_card')
                                  and access.can_view_card_number(ctx, request_row, ctx['roles'])),
            'current_kind': current['kind'] if current else None,
            'actions': list(workflow.ACTIONS.get(current['kind'], ())) if can_act else [],
        }

    def _full(cursor, request_id, ctx, request_row=None):
        """Карточка заявки целиком."""
        request_row = request_row or queries.read_request(cursor, request_id)
        if not request_row:
            raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
        members = queries.role_member_ids(cursor)
        subtasks = queries.list_subtasks(cursor, request_id)
        attachments = queries.list_attachments(cursor, request_id)
        contract = directory.read_contract(cursor, request_row.get('contract_id'))
        gate_ok, gate_reason = workflow.contract_gate(
            request_row.get('amount'), contract, counterparty_id=request_row.get('counterparty_id'),
            on_date=request_row.get('invoice_date') or queries.today_almaty())
        asset_rows = assets_sql.for_request(cursor, request_id)
        permissions = _permissions(ctx, request_row, subtasks, members)
        current_kind = permissions['current_kind']
        full = {
            'request': request_row,
            'items': queries.list_items(cursor, request_id),
            'offers': queries.list_offers(cursor, request_id),
            'subtasks': subtasks,
            'lifecycle': workflow.lifecycle(request_row, subtasks),
            'attachments': [queries.public_attachment(item) for item in attachments],
            'events': queries.list_events(cursor, request_id, with_hidden=access.is_section_admin(ctx)),
            'history': queries.payment_history(
                cursor, counterparty_id=request_row.get('counterparty_id'),
                category_id=request_row.get('category_id'), subcategory_id=request_row.get('subcategory_id'),
                expense_name=request_row.get('expense_name'), exclude_request_id=request_id,
                **_history_scope(ctx)),
            # Возможный дубль счёта (п. 8): бухгалтерии — по всем заявкам, остальным —
            # только среди тех, что они видят и в реестре.
            'duplicates': queries.find_duplicates(cursor, request_row, **_history_scope(ctx)),
            'contract': contract,
            'contract_check': {
                'applies': request_row.get('payment_method') == workflow.METHOD_INVOICE,
                'ok': gate_ok, 'reason': gate_reason, 'threshold': float(workflow.CONTRACT_REQUIRED_OVER)},
            'legal_entity': directory.read_legal_entity(cursor, request_row.get('legal_entity_id')),
            'counterparty_account': directory.read_account(cursor, request_row.get('counterparty_account_id')),
            'assets': asset_rows,
            'closing': workflow.closing_conditions(
                request_row, subtasks, assets=asset_rows,
                has_handover_act=any(item['kind'] == 'handover_act' for item in attachments)),
            'clarify_reasons': [{'code': item['code'], 'label': item['label'], 'ask': item['ask']}
                                for item in workflow.clarify_reasons_for(current_kind)] if current_kind else [],
            'submit_problems': flow.submit_problems(cursor, request_row) if permissions['can_edit'] else [],
            'permissions': permissions,
        }
        # П. 9: кто именно в бухгалтерии или финансовом отделе выполнил платёж,
        # знают само подразделение и администратор; остальным — название подразделения.
        if not access.sees_department_people(ctx, ctx['roles']):
            privacy.hide_department_people(full, members)
        return full

    # ── Разбор полей заявки ─────────────────────────────────────────────────

    def _not_a_number(value):
        """Значение прислали, но числом оно не читается."""
        return value not in (None, '') and (isinstance(value, bool) or workflow.to_decimal(value, None) is None)

    def _parse_items(raw_items, errors):
        items = []
        for raw in (raw_items if isinstance(raw_items, list) else []):
            raw = raw if isinstance(raw, dict) else {}
            name = as_text(raw.get('name'), 'name', limit=300)
            if not name:
                continue
            # Количество и цена — сразу в той точности, в какой лягут в базу:
            # сумма считается из тех же чисел, что потом покажет карточка.
            qty = workflow.item_quantity(raw)
            price = workflow.item_price(raw)
            # Не число («abc», «NaN») — ошибка, а не молчаливая «одна штука» или
            # «бесплатно»: пустое количество по умолчанию 1, а нечитаемое — нет.
            if _not_a_number(raw.get('quantity')):
                errors.append('Позиция «%s»: количество должно быть числом' % name)
            elif qty <= 0:
                errors.append('Позиция «%s»: количество должно быть больше нуля' % name)
            elif qty > workflow.MAX_QUANTITY:
                errors.append('Позиция «%s»: слишком большое количество' % name)
            if _not_a_number(raw.get('unit_price')):
                errors.append('Позиция «%s»: цена должна быть числом' % name)
            elif price < 0:
                errors.append('Позиция «%s»: цена не может быть отрицательной' % name)
            elif price > workflow.MAX_MONEY:
                errors.append('Позиция «%s»: слишком большая цена' % name)
            items.append({'name': name, 'quantity': qty, 'unit': as_text(raw.get('unit'), 'unit', limit=32),
                          'unit_price': price})
        if items:
            total = workflow.items_total(items)
            if total > workflow.MAX_MONEY and not any('слишком больш' in e for e in errors):
                errors.append('Сумма заявки слишком большая')
        return items

    def _parse_offers(cursor, raw_offers, ctx, errors):
        """Варианты поставщиков (п. 4.2). Рекомендуемому, вписанному названием,
        заводится карточка в справочнике — он становится поставщиком заявки."""
        offers = []
        for raw in (raw_offers if isinstance(raw_offers, list) else []):
            raw = raw if isinstance(raw, dict) else {}
            counterparty_id = as_int(raw.get('counterparty_id'), 'counterparty_id')
            name = as_text(raw.get('supplier_name'), 'Поставщик', limit=200)
            if counterparty_id:
                counterparty = directory.read_counterparty(cursor, counterparty_id)
                if not counterparty:
                    errors.append('Поставщик не найден в справочнике')
                    continue
                name = counterparty['name']
            amount = as_money(raw.get('amount'), 'Стоимость')
            terms = as_text(raw.get('terms'), 'Условия', limit=2000)
            link = as_link(raw.get('link'))
            comment = as_text(raw.get('comment'), 'Комментарий', limit=2000)
            if not (name or amount or terms or link or comment):
                continue          # пустая строка формы
            offers.append({
                'id': as_int(raw.get('id'), 'id'), 'counterparty_id': counterparty_id,
                'supplier_name': name or '', 'amount': amount, 'terms': terms, 'link': link,
                'comment': comment, 'is_recommended': as_bool(raw.get('is_recommended')),
            })
        recommended = [offer for offer in offers if offer['is_recommended']]
        if len(recommended) > 1:
            errors.append('Рекомендуемый поставщик должен быть один')
        if len(recommended) == 1 and not recommended[0]['counterparty_id'] and recommended[0]['supplier_name']:
            # Новый поставщик впервые появляется в заявке — заводим его карточку
            # одним названием; БИН и реквизиты допишет ведущий справочник.
            created_id, _created = directory.find_or_create_counterparty(
                cursor, recommended[0]['supplier_name'], ctx['user_id'])
            recommended[0]['counterparty_id'] = created_id
        return offers

    def _request_fields(cursor, data, ctx, *, creating, current=None):
        """Разбирает поля заявки из запроса. Возвращает (поля, позиции, поставщики, карта).

        Здесь проверяется только СОГЛАСОВАННОСТЬ присланного (типы, принадлежность
        договора поставщику и т. п.). Полнота — чего не хватает для отправки —
        проверяется одним правилом в workflow.missing_for_submit.
        """
        current = current or {}
        errors = []
        fields = {}
        items = offers = None
        card = {'number': None, 'card_id': None, 'clear': False}

        def has(key):
            return key in data

        if creating:
            kind = as_choice(data.get('request_kind'), schema.REQUEST_KINDS,
                             'Тип заявки: новый закуп или оплата по регулярному обязательству')
            if not kind:
                raise ApiError('Выберите тип заявки', code='PAYMENT_REQUEST_INVALID')
            fields['request_kind'] = kind
        else:
            kind = current.get('request_kind')
            if has('request_kind') and data.get('request_kind') not in (None, '', kind):
                raise ApiError('Тип заявки не меняется — отмените заявку и создайте новую',
                               code='PAYMENT_REQUEST_INVALID')

        # Подразделение: по умолчанию — отдел инициатора.
        if creating or has('department_id'):
            department_id = as_int(data.get('department_id'), 'department_id')
            if creating and not department_id:
                department_id = ctx.get('department_id')
            fields['department_id'] = department_id
            department = directory.department_brief(cursor, department_id)
            fields['department_name'] = (department or {}).get('name')
        for key in ('payment_period', 'notes', 'invoice_number', 'invoice_description'):
            if has(key):
                fields[key] = as_text(data.get(key), key)
        for key in ('due_on', 'invoice_date'):
            if has(key):
                fields[key] = as_date(data.get(key), key)

        if kind == workflow.KIND_REGULAR:
            # П. 4.4: поставщика, договор, компанию, реквизиты и назначение
            # подставляет справочник — из запроса они не читаются вовсе.
            template_id = (as_int(data.get('fixed_template_id'), 'fixed_template_id')
                           if (creating or has('fixed_template_id')) else current.get('fixed_template_id'))
            template = directory.read_template(cursor, template_id) if template_id else None
            if template_id and not template:
                errors.append('Регулярный платёж не найден')
            elif template and not template.get('is_active'):
                errors.append('Регулярный платёж «%s» приостановлен' % template['name'])
            if template and (creating or has('fixed_template_id')):
                fields.update(fixed.request_fields(template))
                if creating and not has('due_on') and template.get('next_due_on'):
                    fields['due_on'] = template['next_due_on']
            if creating or has('amount'):
                amount = as_money(data.get('amount'), 'Сумма')
                if amount is None and template:
                    amount = workflow.to_decimal(template.get('amount'))
                name = (template or {}).get('name') or current.get('expense_name') or 'Регулярный платёж'
                items = [{'name': name, 'quantity': Decimal('1'), 'unit': None, 'unit_price': amount or Decimal('0')}]
        else:
            for key in ('legal_entity_id', 'project_id', 'category_id', 'subcategory_id', 'contract_id',
                        'counterparty_account_id'):
                if creating or has(key):
                    fields[key] = as_int(data.get(key), key)
            for key in ('expense_name', 'branch', 'justification', 'supplier_choice_reason', 'payment_purpose'):
                if creating or has(key):
                    fields[key] = as_text(data.get(key), key)
            if creating or has('object_type'):
                fields['object_type'] = as_choice(data.get('object_type'), schema.OBJECT_TYPES,
                                                  'Тип объекта: товар или услуга')
            object_type = fields.get('object_type', current.get('object_type'))
            if creating or has('accounting_category') or has('object_type'):
                category = as_choice(data.get('accounting_category', current.get('accounting_category')),
                                     schema.ACCOUNTING_CATEGORIES,
                                     'Категория учёта: расходный материал или имущество')
                # Категория учёта есть только у товара (п. 10).
                fields['accounting_category'] = category if object_type == workflow.OBJECT_GOODS else None
            if creating or has('payment_method'):
                fields['payment_method'] = as_choice(data.get('payment_method'), schema.PAYMENT_METHODS,
                                                     'Способ оплаты: счёт или пополнение карты')
            if creating or has('no_alternatives'):
                fields['no_alternatives'] = as_bool(data.get('no_alternatives'))
            no_alternatives = fields.get('no_alternatives', bool(current.get('no_alternatives')))
            if creating or has('no_alternatives') or has('no_alternatives_reason') or has('no_alternatives_comment'):
                reason = as_choice(data.get('no_alternatives_reason', current.get('no_alternatives_reason')),
                                   workflow.NO_ALTERNATIVES_LABELS, 'Причина отсутствия альтернатив не из списка')
                comment = as_text(data.get('no_alternatives_comment', current.get('no_alternatives_comment')),
                                  'no_alternatives_comment')
                fields['no_alternatives_reason'] = reason if no_alternatives else None
                fields['no_alternatives_comment'] = comment if no_alternatives else None

            if creating or has('items'):
                items = _parse_items(data.get('items'), errors)
            if creating or has('offers'):
                offers = _parse_offers(cursor, data.get('offers'), ctx, errors)
                recommended = next((offer for offer in offers if offer['is_recommended']), None)
                # У заявки первой версии вариантов поставщиков не было: поставщик записан в
                # самой заявке, и пустой список из формы стирать его не должен.
                if offers or not workflow.accepted_before(current):
                    fields['counterparty_id'] = (recommended or {}).get('counterparty_id')

            if fields.get('subcategory_id'):
                category_id = fields.get('category_id', current.get('category_id'))
                cursor.execute("SELECT parent_id FROM payment_categories WHERE id = %s", (fields['subcategory_id'],))
                row = cursor.fetchone()
                if not row or row[0] != category_id:
                    errors.append('Подкатегория не относится к выбранной категории')
            counterparty_id = fields.get('counterparty_id', current.get('counterparty_id'))
            if 'counterparty_id' in fields and fields['counterparty_id'] != current.get('counterparty_id'):
                # Сменили поставщика — договор и счёт прежнего к новому не относятся.
                fields.setdefault('contract_id', None)
                fields.setdefault('counterparty_account_id', None)
            if fields.get('contract_id'):
                contract = directory.read_contract(cursor, fields['contract_id'])
                if not contract:
                    errors.append('Договор не найден')
                elif counterparty_id and contract['counterparty_id'] != counterparty_id:
                    errors.append('Договор №%s заключён с другим поставщиком' % contract['number'])
            if fields.get('counterparty_account_id'):
                account = directory.read_account(cursor, fields['counterparty_account_id'])
                if not account:
                    errors.append('Банковские реквизиты поставщика не найдены')
                elif counterparty_id and account['counterparty_id'] != counterparty_id:
                    errors.append('Эти банковские реквизиты принадлежат другому поставщику')

            # ── Карта (пп. 5.2–5.4) ──
            method = fields.get('payment_method', current.get('payment_method'))
            if method == workflow.METHOD_CARD:
                if creating or has('card_recipient'):
                    fields['card_recipient'] = as_choice(data.get('card_recipient'), schema.CARD_RECIPIENTS,
                                                         'Получатель: карта сотрудника или поставщика')
                recipient = fields.get('card_recipient', current.get('card_recipient'))
                if creating or has('card_holder_user_id'):
                    holder_id = as_int(data.get('card_holder_user_id'), 'card_holder_user_id')
                    fields['card_holder_user_id'] = holder_id if recipient == workflow.CARD_EMPLOYEE else None
                if creating or has('card_holder_name'):
                    fields['card_holder_name'] = as_text(data.get('card_holder_name'), 'card_holder_name')
                if (fields.get('card_holder_user_id') and not fields.get('card_holder_name')
                        and recipient == workflow.CARD_EMPLOYEE):
                    holder = queries.user_brief(cursor, fields['card_holder_user_id'])
                    if not holder or holder['fired']:
                        errors.append('Сотрудник — владелец карты не найден')
                    else:
                        fields['card_holder_name'] = holder['name']
                card_id = as_int(data.get('card_id'), 'card_id') if has('card_id') else None
                number = cards.digits(data.get('card_number')) if has('card_number') else ''
                if card_id:
                    saved = directory.read_card(cursor, card_id)
                    if not saved or not saved.get('is_active'):
                        errors.append('Карта не найдена в справочнике')
                    elif saved['owner_kind'] != recipient:
                        errors.append('Карта из справочника принадлежит другому получателю')
                    elif (saved.get('user_id') != fields.get('card_holder_user_id', current.get('card_holder_user_id'))
                          if recipient == workflow.CARD_EMPLOYEE
                          else saved.get('counterparty_id') not in (None, counterparty_id)):
                        # Карту берут только у того, кому платят: иначе в заявку
                        # попал бы номер постороннего человека.
                        errors.append('Эта карта из справочника принадлежит другому владельцу')
                    else:
                        card['card_id'] = card_id
                        fields['card_id'] = card_id
                        if not fields.get('card_holder_name'):
                            fields['card_holder_name'] = saved['holder_name']
                elif number:
                    problem = cards.problem(number)
                    if problem:
                        errors.append(problem)
                    else:
                        card['number'] = number
                        fields['card_id'] = None
                elif not creating and current.get('has_card') and (
                        recipient != current.get('card_recipient')
                        or fields.get('card_holder_user_id', current.get('card_holder_user_id'))
                        != current.get('card_holder_user_id')
                        or (recipient == workflow.CARD_SUPPLIER and counterparty_id != current.get('counterparty_id'))):
                    # Получателя сменили, а номер не прислали: прежний номер — карта другого
                    # человека. Стираем его, и заявка не уйдёт, пока не введут новый.
                    fields['card_id'] = None
                    card['clear'] = True
            elif has('payment_method') and current.get('payment_method') == workflow.METHOD_CARD:
                # Способ оплаты сменили на счёт — данные карты заявке больше не нужны.
                fields.update({'card_recipient': None, 'card_holder_name': None, 'card_holder_user_id': None,
                               'card_id': None})
                card['clear'] = True

        legal_entity_id = fields.get('legal_entity_id')
        if legal_entity_id and not directory.read_legal_entity(cursor, legal_entity_id):
            errors.append('Компания не найдена в справочнике')
        if errors:
            raise ApiError('; '.join(errors), code='PAYMENT_REQUEST_INVALID', extra={'errors': errors})
        return fields, items, offers, card

    def _apply_card(cursor, request_id, card, *, was_submitted):
        """Записывает номер карты заявки. Смена номера после отправки делает
        снимок существенных условий устаревшим — заявка согласуется заново."""
        changed = False
        if card.get('card_id'):
            changed = queries.copy_card_from_directory(cursor, request_id, card['card_id'])
        elif card.get('number'):
            changed = queries.set_card_number(cursor, request_id, card['number'])
        elif card.get('clear'):
            changed = queries.set_card_number(cursor, request_id, '')
        if changed and was_submitted:
            queries.mark_snapshot_stale(cursor, request_id)
        return changed

    def _precheck_submit(cursor, request_id, uploads):
        """Хватает ли заявке данных для отправки — с учётом файлов, которые ещё
        не загружены. Отказ здесь не оставляет файлов в хранилище."""
        request_row = queries.read_request(cursor, request_id)
        attachments = queries.list_attachments(cursor, request_id) + [{'kind': kind} for _s, kind, _o in uploads]
        missing = workflow.missing_for_submit(
            request_row, items=queries.list_items(cursor, request_id),
            offers=queries.list_offers(cursor, request_id), attachments=attachments,
            settings=directory.get_settings(cursor), card_number_ok=bool(request_row.get('has_card')))
        if missing:
            raise ApiError('; '.join(missing), code='PAYMENT_INCOMPLETE', extra={'missing': missing})

    # ═══════════════════════════════════════════════════════════════════════
    # Служебное
    # ═══════════════════════════════════════════════════════════════════════

    def _meta():
        """Словари процесса для фронта: подписи живут на сервере, второй копии
        названий этапов, колонок и причин во фронте нет."""
        return {
            'stages': [{'code': code, 'label': label} for code, label in workflow.STAGES],
            'subtasks': [{'kind': item['kind'], 'title': item['title'], 'brief': item['brief'],
                          'stage': item['stage'], 'role': item['role'],
                          'role_label': workflow.ROLE_LABELS[item['role']], 'board': item['board'],
                          'statuses': list(item['statuses']),
                          'actions': list(workflow.ACTIONS.get(item['kind'], ()))}
                         for item in workflow.SUBTASKS],
            'boards': [{'code': item['code'], 'title': item['title'], 'short': item['short'],
                        'columns': [{'key': key, 'label': label} for key, label in item['columns']],
                        'work': list(item['work'])} for item in workflow.BOARDS],
            'roles': [{'code': code, 'label': workflow.ROLE_LABELS[code]} for code in schema.MEMBER_ROLES],
            'request_kinds': workflow.REQUEST_KIND_LABELS,
            'payment_methods': workflow.PAYMENT_METHOD_LABELS,
            'card_recipients': workflow.CARD_RECIPIENT_LABELS,
            'object_types': workflow.OBJECT_TYPE_LABELS,
            'accounting_categories': workflow.ACCOUNTING_CATEGORY_LABELS,
            'no_alternatives_reasons': [{'code': code, 'label': label}
                                        for code, label in workflow.NO_ALTERNATIVES_REASONS],
            'closing_doc_statuses': [{'code': code, 'label': label}
                                     for code, label in workflow.CLOSING_DOC_STATUSES],
            'asset_statuses': [{'code': code, 'label': label} for code, label in workflow.ASSET_STATUSES],
            'attachment_labels': workflow.ATTACHMENT_LABELS,
            'closing_doc_kinds': list(workflow.CLOSING_DOC_KINDS),
            'action_labels': workflow.ACTION_LABELS,
            'contract_threshold': float(workflow.CONTRACT_REQUIRED_OVER),
            'card_digits': [cards.MIN_DIGITS, cards.MAX_DIGITS],
        }

    @payments_route('/ping')
    def ping(ctx):
        with db._get_cursor() as cursor:
            ready = schema.schema_ready(cursor)
            is_admin = access.is_section_admin(ctx)
            # mark_section_access: кому раздел ещё не открыт, хотя в «Участниках» он
            # записан, — такой человек задачи не увидит, сказать об этом надо заранее.
            members = access.mark_section_access(queries.role_members(cursor)) if ready else {}
            legacy_pending = len(legacy.pending(cursor)) if ready and is_admin else 0
            is_manager = queries.is_manager_somewhere(cursor, ctx['user_id']) if ready else False
            settings = directory.get_settings(cursor) if ready else dict(workflow.SETTINGS_DEFAULTS)
            desk = (queries.desk_count(cursor, ctx['user_id'], ctx['roles'], access.can_act_for_anyone(ctx))
                    if ready else 0)
        # Без кого заявка не пройдёт: утверждать и вести оплату счёта некому.
        roles_missing = [workflow.ROLE_LABELS[code] for code in ('approver', 'accounting')
                         if not members.get(code)]
        return jsonify({
            'capabilities': access.capabilities(ctx, ctx['roles'], is_manager),
            'me': {'id': ctx['user_id'], 'name': ctx.get('name'), 'has_telegram': ctx.get('has_telegram'),
                   'department_id': ctx.get('department_id'), 'department_name': ctx.get('department_name')},
            'schema_ready': ready,
            'storage_ready': files.storage_ready(gcs),
            'card_key_ready': cards.key_ready(),
            # Поимённый состав ролей — только вкладке «Участники» (администратор
            # раздела): остальным незачем знать, кто в бухгалтерии и финотделе (п. 9).
            'roles': members if is_admin else {},
            'roles_missing': roles_missing,
            # Есть ли у роли хоть один участник — без имён: форма заявки не предлагает
            # способ оплаты или категорию учёта, которые некому выполнить.
            'roles_staffed': {code: bool(members.get(code)) for code in schema.MEMBER_ROLES},
            # Заявки первой версии, которые не удалось перевести на этапы (см. legacy.py).
            'legacy_pending': legacy_pending,
            'counters': {'desk': desk},
            'settings': settings,
            'meta': _meta(),
        })

    @payments_route('/users')
    def users(ctx):
        with db._get_cursor() as cursor:
            people = queries.list_users(cursor)
        return jsonify({'users': people})

    # ═══════════════════════════════════════════════════════════════════════
    # Заявки
    # ═══════════════════════════════════════════════════════════════════════

    def _list_filters(args):
        return {
            'query': (args.get('q') or '').strip() or None,
            'state': (args.get('state') or '').strip() or None,
            'legal_entity_id': as_int(args.get('legal_entity_id'), 'legal_entity_id'),
            'department_id': as_int(args.get('department_id'), 'department_id'),
            'initiator_id': as_int(args.get('initiator_id'), 'initiator_id'),
            'assignee_id': as_int(args.get('assignee_id'), 'assignee_id'),
            'approver_id': as_int(args.get('approver_id'), 'approver_id'),
            'counterparty_id': as_int(args.get('counterparty_id'), 'counterparty_id'),
            'request_kind': as_choice(args.get('request_kind'), schema.REQUEST_KINDS, 'Тип заявки не из списка'),
            'payment_method': as_choice(args.get('payment_method'), schema.PAYMENT_METHODS,
                                        'Способ оплаты не из списка'),
            'amount_from': as_money(args.get('amount_from'), 'Сумма от'),
            'amount_to': as_money(args.get('amount_to'), 'Сумма до'),
            'date_from': as_date(args.get('date_from'), 'date_from'),
            'date_to': as_date(args.get('date_to'), 'date_to'),
            'overdue': as_bool(args.get('overdue')),
            'mine': as_bool(args.get('mine')),
        }

    def _scoped_filters(cursor, ctx, args):
        """Фильтры запроса плюс граница видимости смотрящего."""
        filters = _list_filters(args)
        filters['viewer_id'] = ctx['user_id']
        filters['viewer_roles'] = ctx['roles']
        filters['viewer_approves_own'] = access.can_act_for_anyone(ctx)
        if filters.get('assignee_id'):
            filters['assignee_roles'] = queries.roles_of_user(cursor, filters['assignee_id'])
        if not access.sees_all_requests(ctx, ctx['roles']):
            filters['visible_to'] = ctx['user_id']
            filters['visible_roles'] = ctx['roles']
        return filters

    @payments_route('/requests')
    def requests_list(ctx):
        limit = min(max(as_int(request.args.get('limit'), 'limit') or 50, 1), PAGE_SIZE_MAX)
        offset = max(as_int(request.args.get('offset'), 'offset') or 0, 0)
        with db._get_cursor() as cursor:
            filters = _scoped_filters(cursor, ctx, request.args)
            total, items = queries.list_requests(cursor, limit=limit, offset=offset, **filters)
            counters = queries.state_counters(cursor, **filters)
        if not access.sees_department_people(ctx, ctx['roles']):
            for item in items:
                privacy.hide_in_row(item)
        return jsonify({'items': items, 'total': total, 'counters': counters, 'limit': limit, 'offset': offset})

    @payments_route('/requests', methods=('POST',))
    def request_create(ctx):
        if not access.can_create_request(ctx):
            raise ApiError('Заводить заявки вам не разрешено', code='PAYMENTS_READ_ONLY', status=403)
        data = _payload()
        uploads = uploaded_files()
        actor = _actor(ctx)
        outbox = []
        submit = as_bool(data.get('submit', True))
        with db._get_cursor() as cursor:
            members = queries.role_member_ids(cursor)
            if not members.get('approver') or not members.get('accounting'):
                raise ApiError('Сначала назначьте участников процесса: утверждающих и бухгалтерию '
                               '(вкладка «Участники»)', code='PAYMENTS_ROLES_NOT_CONFIGURED', status=409)
            # Финансовый отдел и ответственный за имущество нужны не каждой заявке:
            # есть ли они, проверяет отправка — по маршруту заявки (flow.route_gaps).
            fields, items, offers, card = _request_fields(cursor, data, ctx, creating=True)
            if not fields.get('expense_name'):
                # Название у регулярного платежа подставляет справочник, у закупа его вводят.
                raise ApiError('Выберите регулярный платёж из справочника'
                               if fields['request_kind'] == workflow.KIND_REGULAR else 'Укажите наименование закупа',
                               code='PAYMENT_INCOMPLETE')
            initiator = {'id': ctx['user_id'], 'name': ctx.get('name')}
            request_id = queries.create_request(cursor, fields=fields, items=items or [], actor=actor,
                                                initiator=initiator)
            offer_ids = queries.save_offers(cursor, request_id, offers) if offers is not None else []
            _apply_card(cursor, request_id, card, was_submitted=False)
            if submit:
                _precheck_submit(cursor, request_id, uploads)
            _store_files(cursor, request_id, uploads, actor, subtask_kind=workflow.KIND_INITIATION,
                         offer_ids=offer_ids)
            result = None
            if submit:
                result = flow.submit(cursor, request_id, actor, comment=as_text(data.get('comment'), 'comment'),
                                     outbox=outbox, base_url=web_app_base_url)
            full = _full(cursor, request_id, ctx)
        full.update({'status': 'success', 'submitted': bool(result), 'warnings': _flush(outbox)})
        return jsonify(full, 201)

    @payments_route('/requests/<int:request_id>')
    def request_read(ctx, request_id):
        with db._get_cursor() as cursor:
            request_row = _load_visible(cursor, ctx, request_id)
            # Открыл заявку — уведомления «к сведению» по ней прочитаны.
            notices.mark_seen(cursor, ctx['user_id'], request_id)
            full = _full(cursor, request_id, ctx, request_row)
        return jsonify(full)

    def _offers_key(offers):
        return [(offer.get('counterparty_id'), offer.get('supplier_name') or '',
                 workflow.to_decimal(offer.get('amount'), None), offer.get('terms') or '',
                 offer.get('link') or '', offer.get('comment') or '', bool(offer.get('is_recommended')))
                for offer in offers or []]

    def _save_edit(cursor, ctx, request_id, data, uploads, actor):
        """Правка заявки инициатором: поля, позиции, поставщики, карта, файлы."""
        current = queries.read_request(cursor, request_id, lock=True)
        if not current or not _visible(cursor, ctx, current):
            raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
        if not access.can_edit_request(ctx, current):
            raise ApiError('Править заявку можно, только пока она у инициатора',
                           code='PAYMENTS_FORBIDDEN', status=403)
        fields, items, offers, card = _request_fields(cursor, data, ctx, creating=False, current=current)
        changes = queries.update_request_fields(cursor, request_id, fields)
        if items is not None:
            # Сравниваем значения, а не их запись: «5.000» из базы и «5» из формы —
            # одно и то же количество.
            def item_key(item):
                return (item['name'], workflow.item_quantity(item), item.get('unit') or '',
                        workflow.item_price(item))

            before = [item_key(i) for i in queries.list_items(cursor, request_id)]
            after = [item_key(i) for i in items]
            if before != after:
                total = queries.replace_items(cursor, request_id, items)
                changes['items'] = (len(before), len(after))
                if workflow.to_decimal(current.get('amount')) != total:
                    changes['amount'] = (queries.plain(current.get('amount')), float(total))
        offer_ids = None
        if offers is not None:
            before = _offers_key(queries.list_offers(cursor, request_id))
            offer_ids = queries.save_offers(cursor, request_id, offers)
            if before != _offers_key(offers):
                changes['offers'] = (len(before), len(offers))
        if _apply_card(cursor, request_id, card, was_submitted=bool(current.get('submitted_at'))):
            changes['card_number'] = ('•', '•')
        if changes:
            queries.log_event(cursor, request_id, 'edited', actor, payload={'changes': changes})
        return offer_ids

    def _drop_old_invoices(cursor, request_id, data, uploads, actor):
        """«Заменить файл счёта»: с новым файлом счёта прежние снимаются — у бухгалтерии
        не должно остаться двух счетов, нечитаемого и нового. Возвращает блобы,
        которые вызывающий стирает из хранилища ПОСЛЕ коммита."""
        if not (as_bool(data.get('replace_invoice')) and any(kind == 'invoice' for _s, kind, _o in uploads)):
            return []
        stale = [item for item in queries.list_attachments(cursor, request_id) if item['kind'] == 'invoice']
        for item in stale:
            queries.remove_attachment(cursor, item['id'], actor)
        return [(item['bucket'], item['blob_path']) for item in stale]

    @payments_route('/requests/<int:request_id>', methods=('PATCH',))
    def request_edit(ctx, request_id):
        data = _payload()
        uploads = uploaded_files()
        actor = _actor(ctx)
        outbox = []
        submit = as_bool(data.get('submit'))
        with db._get_cursor() as cursor:
            offer_ids = _save_edit(cursor, ctx, request_id, data, uploads, actor)
            if submit:
                _precheck_submit(cursor, request_id, uploads)
            stale = _drop_old_invoices(cursor, request_id, data, uploads, actor)
            _store_files(cursor, request_id, uploads, actor, subtask_kind=workflow.KIND_INITIATION,
                         offer_ids=offer_ids)
            result = None
            if submit:
                result = flow.submit(cursor, request_id, actor, comment=as_text(data.get('comment'), 'comment'),
                                     outbox=outbox, base_url=web_app_base_url)
            full = _full(cursor, request_id, ctx)
        if gcs.get('client'):
            files.drop(gcs, stale)
        full.update({'status': 'success', 'submitted': bool(result),
                     'rerouted': bool(result and result.get('rerouted')), 'warnings': _flush(outbox)})
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/submit', methods=('POST',))
    def request_submit(ctx, request_id):
        """Отправить заявку от инициатора без правки полей: ответ на уточнение,
        к которому достаточно приложить файл или написать комментарий."""
        data = _payload()
        uploads = uploaded_files()
        actor = _actor(ctx)
        outbox = []
        with db._get_cursor() as cursor:
            current = _load_visible(cursor, ctx, request_id, lock=True)
            if not access.can_edit_request(ctx, current):
                raise ApiError('Отправить заявку может инициатор, пока она у него',
                               code='PAYMENTS_FORBIDDEN', status=403)
            _precheck_submit(cursor, request_id, uploads)
            stale = _drop_old_invoices(cursor, request_id, data, uploads, actor)
            _store_files(cursor, request_id, uploads, actor, subtask_kind=workflow.KIND_INITIATION)
            result = flow.submit(cursor, request_id, actor, comment=as_text(data.get('comment'), 'comment'),
                                 outbox=outbox, base_url=web_app_base_url)
            full = _full(cursor, request_id, ctx)
        if gcs.get('client'):
            files.drop(gcs, stale)
        full.update({'status': 'success', 'submitted': True, 'rerouted': bool(result.get('rerouted')),
                     'warnings': _flush(outbox)})
        return jsonify(full)

    @payments_route('/requests/<int:request_id>', methods=('DELETE',), admin=True)
    def request_delete(ctx, request_id):
        with db._get_cursor() as cursor:
            if not queries.read_request(cursor, request_id):
                raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
            refs = queries.delete_request(cursor, request_id)
        if gcs.get('client'):
            files.drop(gcs, refs)
        return jsonify({'status': 'success'})

    # ── Действия подзадач ───────────────────────────────────────────────────

    def _action_fields(data):
        fields = {'comment': as_text(data.get('comment'), 'comment')}
        if 'reason' in data:
            fields['reason'] = str(data.get('reason') or '').strip() or None
        if 'paid_on' in data:
            fields['paid_on'] = as_date(data.get('paid_on'), 'Дата')
        if 'paid_amount' in data:
            fields['paid_amount'] = as_money(data.get('paid_amount'), 'Сумма')
        if 'received_on' in data:
            fields['received_on'] = as_date(data.get('received_on'), 'Дата получения')
        if 'received_quantity' in data:
            fields['received_quantity'] = as_text(data.get('received_quantity'), 'received_quantity')
        return fields

    def _guard_subtask(cursor, ctx, request_id, kind):
        """Заявка под блокировкой и открытая подзадача, на которую у человека есть право."""
        if kind not in workflow.SUBTASK_BY_KIND or kind == workflow.KIND_INITIATION:
            raise ApiError('Нет такого этапа', code='PAYMENT_ACTION_UNKNOWN')
        request_row, subtask = flow.load_for_action(cursor, request_id, kind)
        members = queries.role_member_ids(cursor)
        if not access.can_act_on_subtask(ctx, subtask, members, request_row):
            # Чужую заявку не подтверждаем и отказом: тот же ответ, что у несуществующей.
            if not _visible(cursor, ctx, request_row):
                raise ApiError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
            if access.approves_own_request(ctx, subtask, request_row):
                raise ApiError('Свою заявку согласует другой утверждающий', code='PAYMENTS_OWN_REQUEST', status=403)
            # У подзадачи подразделения исполнитель — подразделение, без фамилии (п. 9).
            executor = subtask['role_label'] if kind in privacy.DEPARTMENT_KINDS else (
                subtask.get('assignee_name') or subtask['role_label'])
            raise ApiError('Этап «%s» выполняет %s' % (subtask['title'], executor),
                           code='PAYMENTS_NOT_YOUR_STEP', status=403)
        return request_row, subtask

    @payments_route('/requests/<int:request_id>/act', methods=('POST',))
    def request_act(ctx, request_id):
        data = _payload()
        uploads = uploaded_files()
        actor = _actor(ctx)
        kind = str(data.get('kind') or '').strip()
        action = str(data.get('action') or '').strip()
        outbox = []
        with db._get_cursor() as cursor:
            request_row, subtask = _guard_subtask(cursor, ctx, request_id, kind)
            if action not in workflow.ACTIONS.get(kind, ()):
                raise ApiError('У этого этапа нет такого действия', code='PAYMENT_ACTION_UNKNOWN')
            fields = _action_fields(data)
            asset_rows = routes_assets.parse_assets(cursor, data.get('assets'), request_row) \
                if action == 'register' else None
            # Проверка до загрузки файлов: считаем уже лежащие файлы подзадачи и присланные.
            own_kinds = {item['kind'] for item in queries.list_attachments(cursor, request_id)
                         if item.get('subtask_kind') == kind} | {k for _s, k, _o in uploads}
            missing = workflow.missing_for_action(kind, action, request=request_row, fields=fields,
                                                  file_kinds=own_kinds, assets=asset_rows or ())
            if missing:
                raise ApiError('; '.join(missing), code='PAYMENT_ACTION_INCOMPLETE', extra={'missing': missing})
            new_kinds = _store_files(cursor, request_id, uploads, actor, subtask_kind=kind)
            result = flow.act(cursor, request_id, kind, action, actor, fields=fields, new_file_kinds=new_kinds,
                              assets=asset_rows, outbox=outbox, base_url=web_app_base_url)
            full = _full(cursor, request_id, ctx)
        full.update({'status': 'success', 'next': result.get('next'), 'warnings': _flush(outbox)})
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/move', methods=('POST',))
    def request_move(ctx, request_id):
        """Перенос карточки между рабочими колонками доски."""
        data = _payload()
        kind = str(data.get('kind') or '').strip()
        status = str(data.get('status') or '').strip()
        with db._get_cursor() as cursor:
            _guard_subtask(cursor, ctx, request_id, kind)
            flow.move(cursor, request_id, kind, status, _actor(ctx))
            full = _full(cursor, request_id, ctx)
        full['status'] = 'success'
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/cancel', methods=('POST',))
    def request_cancel(ctx, request_id):
        data = _payload()
        with db._get_cursor() as cursor:
            current = _load_visible(cursor, ctx, request_id, lock=True)
            if not access.can_cancel_request(ctx, current):
                in_payment = (current.get('status') == 'active' and not current.get('paid_on')
                              and current.get('current_role_code') in privacy.DEPARTMENT_ROLES)
                raise ApiError('Заявка уже в оплате — отменить её может администратор раздела' if in_payment
                               else 'Отменить можно только свою неоплаченную заявку',
                               code='PAYMENTS_FORBIDDEN', status=403)
            flow.cancel(cursor, request_id, _actor(ctx), as_text(data.get('comment'), 'comment'),
                        base_url=web_app_base_url)
            full = _full(cursor, request_id, ctx)
        full['status'] = 'success'
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/assignee', methods=('POST',), admin=True)
    def request_assignee(ctx, request_id):
        """Сменить исполнителя подзадачи (администратор): человек либо «вернуть подразделению»."""
        data = _payload()
        kind = str(data.get('kind') or '').strip()
        user_id = as_int(data.get('user_id'), 'user_id')
        actor = _actor(ctx)
        outbox = []
        with db._get_cursor() as cursor:
            current = queries.read_request(cursor, request_id, lock=True)
            if not current or current['status'] != 'active':
                raise ApiError('Заявка не найдена или закрыта', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
            subtask = queries.read_subtask(cursor, request_id, kind)
            if not subtask or subtask['state'] in ('done', 'skipped') or kind == workflow.KIND_INITIATION:
                raise ApiError('У этого этапа исполнителя не меняют', code='PAYMENT_STEP_DONE', status=409)
            if kind in privacy.DEPARTMENT_KINDS:
                # Пп. 2 и 9: исполнитель оплаты, пополнения и закрывающих документов —
                # подразделение, а не сотрудник; задачу видят все его участники.
                raise ApiError('Этап «%s» выполняет %s целиком — сотрудника ему не назначают'
                               % (subtask['title'], subtask['role_label']), code='PAYMENT_STEP_DEPARTMENT', status=409)
            user = queries.user_brief(cursor, user_id) if user_id else None
            if user_id and (not user or user['fired']):
                raise ApiError('Сотрудник не найден', status=404)
            if not user and subtask['role_code'] == workflow.ROLE_MANAGER:
                raise ApiError('У этапа руководителя должен быть исполнитель', code='PAYMENT_REQUEST_INVALID')
            notices.clear_subtask(cursor, subtask['id'])
            queries.set_subtask_assignee(cursor, request_id, kind, user, actor,
                                         reason=as_text(data.get('reason'), 'comment'))
            if subtask['state'] == 'open':
                flow.announce_task(cursor, request_id, kind, outbox=outbox, base_url=web_app_base_url,
                                   exclude=[ctx['user_id']])
            full = _full(cursor, request_id, ctx)
        full.update({'status': 'success', 'warnings': _flush(outbox)})
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/refund', methods=('POST',))
    def request_refund(ctx, request_id):
        data = _payload()
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            current = _load_visible(cursor, ctx, request_id, lock=True)
            if not current.get('paid_on'):
                raise ApiError('Возврат отмечают только по оплаченной заявке', code='PAYMENT_NOT_PAID', status=409)
            if not access.can_refund(ctx, current, ctx['roles']):
                raise ApiError('Возврат отмечает бухгалтерия', code='PAYMENTS_FORBIDDEN', status=403)
            amount = workflow.to_decimal(data.get('refund_amount'))
            if amount < 0 or amount > workflow.to_decimal(current.get('paid_amount') or current.get('amount')):
                raise ApiError('Сумма возврата не может превышать оплаченную')
            fields = {'refund_amount': amount if amount > 0 else None,
                      'refund_on': as_date(data.get('refund_on'), 'refund_on') if amount > 0 else None}
            queries.update_request_fields(cursor, request_id, fields)
            queries.log_event(cursor, request_id, 'refund', actor, comment=as_text(data.get('comment'), 'comment'),
                              payload={'refund_amount': amount, 'refund_on': fields['refund_on']})
            full = _full(cursor, request_id, ctx)
        full['status'] = 'success'
        return jsonify(full)

    @payments_route('/requests/<int:request_id>/card')
    def request_card(ctx, request_id):
        """Полный номер карты заявки — по запросу и только тем, кому положен (п. 5.2).
        Каждое обращение пишется в журнал заявки."""
        with db._get_cursor() as cursor:
            current = _load_visible(cursor, ctx, request_id)
            if not access.can_view_card_number(ctx, current, ctx['roles']):
                raise ApiError('Полный номер карты доступен финансовому отделу',
                               code='PAYMENTS_FORBIDDEN', status=403)
            number = queries.card_number(cursor, request_id)
            if not number:
                raise ApiError('Номер карты не сохранён или не читается', code='PAYMENT_CARD_MISSING', status=404)
            queries.log_event(cursor, request_id, 'card_revealed', _actor(ctx))
        return jsonify({'number': number, 'pretty': cards.pretty(number)})

    # ── Вложения ────────────────────────────────────────────────────────────

    @payments_route('/requests/<int:request_id>/attachments', methods=('POST',))
    def attachments_add(ctx, request_id):
        uploads = uploaded_files()
        if not uploads:
            raise ApiError('Файлы не переданы', code='PAYMENT_FILES_MISSING')
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            current = _load_visible(cursor, ctx, request_id, lock=True)
            subtasks = queries.list_subtasks(cursor, request_id)
            members = queries.role_member_ids(cursor)
            opened = next((s for s in subtasks if s['state'] == 'open'), None)
            can_act = bool(opened and access.can_act_on_subtask(ctx, opened, members, current))
            if not access.can_attach(ctx, current, ctx['roles'], can_act):
                raise ApiError('Прикладывать файлы может инициатор или исполнитель текущего этапа',
                               code='PAYMENTS_FORBIDDEN', status=403)
            # Файл относится к этапу, на котором заявка сейчас: так он подписан в карточке.
            kinds = _store_files(cursor, request_id, uploads, actor,
                                 subtask_kind=(opened or {}).get('kind') if can_act else None)
            flow.files_added(cursor, request_id, kinds, actor)
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
            current = _load_visible(cursor, ctx, request_id, lock=True)
            # Снять можно свой файл, пока этап, к которому он приложен, не пройден:
            # документ пройденного этапа — основание уже принятого решения.
            subtask = queries.read_subtask(cursor, request_id, item['subtask_kind']) if item.get('subtask_kind') else None
            stage_open = bool(subtask and subtask['state'] in ('open', 'waiting')) or not item.get('subtask_kind')
            own_open = (item.get('uploaded_by') == ctx['user_id'] and current['status'] == 'active' and stage_open)
            if not (access.is_section_admin(ctx) or own_open):
                raise ApiError('Снять можно только свой файл на незавершённом этапе',
                               code='PAYMENTS_FORBIDDEN', status=403)
            if (item['kind'] in workflow.CLOSING_DOC_KINDS
                    and current.get('closing_docs_status') == workflow.DOCS_SCAN
                    and not any(other['kind'] in workflow.CLOSING_DOC_KINDS and other['id'] != attachment_id
                                for other in queries.list_attachments(cursor, request_id))):
                # По этому файлу стоит «Скан получен» (п. 14): без него условие закрытия
                # «закрывающие документы получены» (п. 15) держалось бы ни на чём.
                raise ApiError('Это единственный закрывающий документ заявки. Сначала приложите новый, '
                               'затем снимите этот', code='PAYMENT_LAST_CLOSING_DOC', status=409)
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
            _load_visible(cursor, ctx, item['request_id'])
        if not gcs.get('client'):
            raise ApiError('Хранилище файлов не настроено', code='PAYMENT_STORAGE_OFF', status=503)
        data = files.download(gcs, item['bucket'], item['blob_path'])
        if data is None:
            raise ApiError('Файла нет в хранилище', status=404)
        inline = as_bool(request.args.get('inline'))
        return send_file(BytesIO(data), as_attachment=not inline, download_name=item['file_name'],
                         mimetype=item.get('content_type') or 'application/octet-stream')

    # ═══════════════════════════════════════════════════════════════════════
    # Доски и рабочий стол (пп. 7–9, п. 16)
    # ═══════════════════════════════════════════════════════════════════════

    def _own(ctx, subtask, request_row):
        """Подзадача касается человека: его лично либо его подразделения. Согласование
        своей же заявки у роли целиком — не его: её утверждают остальные."""
        if not subtask:
            return False
        me = ctx['user_id']
        if subtask.get('assignee_id') is not None:
            return int(subtask['assignee_id']) == me or subtask.get('done_by') == me
        if subtask.get('done_by') == me:
            return True
        return subtask['role_code'] in ctx['roles'] and not access.approves_own_request(ctx, subtask, request_row)

    def _focus(board, subtasks, ctx, scope, request_row):
        """Подзадача заявки, которой заявка представлена на доске этому человеку."""
        candidates = [s for s in subtasks if s['kind'] in board['kinds'] and s['state'] in ('open', 'waiting', 'done')]
        if scope == 'own':
            candidates = [s for s in candidates if _own(ctx, s, request_row)]
        live = [s for s in candidates if s['state'] in ('open', 'waiting')]
        if live:
            return live[0]
        return candidates[-1] if candidates else None

    def _card(request_row, focus, column, ctx, members, *, duplicate=False):
        """Карточка доски. Набор полей — пп. 7 и 9 ТЗ; номер карты — только маской."""
        return {
            'id': request_row['id'],
            'column': column,
            'expense_name': request_row.get('expense_name'),
            'initiator_name': request_row.get('initiator_name'),
            'department_name': request_row.get('department_name'),
            'legal_entity_name': request_row.get('legal_entity_name'),
            'amount': request_row.get('amount'),
            'counterparty_name': request_row.get('counterparty_name'),
            'request_kind': request_row.get('request_kind'),
            'payment_method': request_row.get('payment_method'),
            'due_on': request_row.get('due_on'),
            'state': request_row.get('state'),
            'status': request_row.get('status'),
            'stage': request_row.get('stage'),
            'stage_label': request_row.get('stage_label'),
            'current_assignee_name': request_row.get('current_assignee_name'),
            'approved_by_name': request_row.get('approved_by_name'),
            'invoice_number': request_row.get('invoice_number'),
            'invoice_date': request_row.get('invoice_date'),
            'paid_on': request_row.get('paid_on'),
            'paid_amount': request_row.get('paid_amount'),
            'closing_docs_status': request_row.get('closing_docs_status'),
            'card_recipient': request_row.get('card_recipient'),
            'card_holder_name': request_row.get('card_holder_name'),
            'card_mask': request_row.get('card_mask'),
            'payment_purpose': request_row.get('payment_purpose'),
            'created_at': request_row.get('created_at'),
            'duplicate': bool(duplicate),
            'subtask': {
                'kind': focus['kind'], 'title': focus['title'], 'state': focus['state'], 'outcome': focus.get('outcome'),
                # Имя роли — чтобы отличить на карточке подразделение («Бухгалтерия») от человека.
                'role_label': focus['role_label'],
                'status': focus.get('status'), 'assignee_name': focus.get('assignee_name') or focus['role_label'],
                'clarify_label': focus.get('clarify_label'), 'clarify_comment': focus.get('clarify_comment'),
                'done_by_name': focus.get('done_by_name'), 'done_at': focus.get('done_at'),
            } if focus else None,
            'can_act': bool(focus and request_row.get('status') == 'active'
                            and access.can_act_on_subtask(ctx, focus, members, request_row)),
        }

    @payments_route('/boards/<code>')
    def board_view(ctx, code):
        """Доска: колонки с полным счётом и первой порцией карточек.

        `limit` — порция колонки; `column` + `offset` — следующая порция одной
        колонки (окно «Посмотреть ещё»). Колонку заявки считает workflow по её
        подзадачам, поэтому срез делается после раскладки, а карточки
        собираются только для того, что уйдёт на экран.
        """
        board = workflow.board(code)
        if not board:
            raise ApiError('Нет такой доски', status=404)
        keys = [key for key, _label in board['columns']]
        only = (request.args.get('column') or '').strip() or None
        if only and only not in keys:
            raise ApiError('Нет такой колонки', status=404)
        limit = min(max(as_int(request.args.get('limit'), 'limit') or BOARD_PAGE_DEFAULT, 1), BOARD_PAGE_MAX)
        offset = max(as_int(request.args.get('offset'), 'offset') or 0, 0) if only else 0
        with db._get_cursor() as cursor:
            is_manager = queries.is_manager_somewhere(cursor, ctx['user_id'])
            if not access.can_open_board(ctx, code, ctx['roles'], is_manager):
                raise ApiError('Эта доска вам не открыта', code='PAYMENTS_FORBIDDEN', status=403)
            scope = access.board_scope(ctx, code, ctx['roles'])
            filters = _scoped_filters(cursor, ctx, request.args)
            # Границу видимости держит сама доска (свои согласования либо все
            # задачи подразделения) — реестровое ограничение здесь лишнее.
            filters.pop('visible_to', None)
            filters.pop('visible_roles', None)
            pairs = queries.board_requests(
                cursor, code, own_user_id=ctx['user_id'] if scope == 'own' else None,
                own_roles=ctx['roles'], **filters)
            members = queries.role_member_ids(cursor)
            placed = {key: [] for key in keys}
            hide_people = not access.sees_department_people(ctx, ctx['roles'])
            for request_row, subtasks in pairs:
                if hide_people:
                    privacy.hide_in_row(request_row)
                focus = _focus(board, subtasks, ctx, scope, request_row)
                if not focus:
                    continue
                column = workflow.board_column(
                    code, request_row, subtasks,
                    focus_kind=focus['kind'] if code == workflow.BOARD_APPROVAL else None)
                if column in placed:
                    placed[column].append((request_row, focus))
            shown = {key: placed[key][offset:offset + limit] for key in keys if not only or key == only}
            duplicates = (queries.duplicate_request_ids(cursor, [row['id'] for items in shown.values() for row, _f in items])
                          if code == workflow.BOARD_ACCOUNTING else set())
        return jsonify({
            'board': {'code': board['code'], 'title': board['title'], 'work': list(board['work'])},
            'scope': scope,
            'limit': limit,
            'offset': offset,
            'columns': [{
                'key': key, 'label': label, 'caption': workflow.column_caption(code, key),
                'count': len(placed[key]),
                'cards': [_card(row, focus, key, ctx, members, duplicate=row['id'] in duplicates)
                          for row, focus in shown[key]],
            } for key, label in board['columns'] if key in shown],
        })

    def _desk_title(request_row, subtask):
        if subtask['kind'] != workflow.KIND_INITIATION:
            return notify.TASK_TITLES.get(subtask['kind']) or subtask['title']
        if subtask.get('status') == 'rework':
            return 'Доработайте заявку'
        if subtask.get('status') == 'clarification':
            return 'Ответьте на запрос'
        return 'Отправьте заявку на согласование'

    @payments_route('/desk')
    def desk(ctx):
        """Персональный рабочий стол (п. 16): только то, по чему от человека ждут действия.

        Страницами: `limit` + `offset`, `total` — сколько задач всего. Порядок
        и состав считаются по номерам, а заявки читаются только для страницы.
        """
        limit = min(max(as_int(request.args.get('limit'), 'limit') or DESK_PAGE_MAX, 1), DESK_PAGE_MAX)
        offset = max(as_int(request.args.get('offset'), 'offset') or 0, 0)
        with db._get_cursor() as cursor:
            entries = queries.desk_pairs(cursor, ctx['user_id'], ctx['roles'], access.can_act_for_anyone(ctx))
            entries += [(request_id, None) for request_id in queries.docs_wanted_ids(cursor, ctx['user_id'])]
            total = len(entries)
            page = entries[offset:offset + limit]
            rows = queries.desk_page(cursor, [pair for pair in page if pair[1]])
            # requests_by_ids отдаёт по убыванию номера — возвращаем порядок стола.
            docs_ids = [pair[0] for pair in page if not pair[1]]
            docs_by_id = {item['id']: item for item in queries.requests_by_ids(cursor, docs_ids)}
            docs = [docs_by_id[request_id] for request_id in docs_ids if request_id in docs_by_id]
            members = queries.role_member_ids(cursor)
        tasks = []
        hide_people = not access.sees_department_people(ctx, ctx['roles'])
        for request_row, subtask in rows:
            if hide_people:
                privacy.hide_in_row(request_row)
                privacy.hide_in_subtask(subtask, request_row.get('payment_method'))
            card = _card(request_row, subtask, None, ctx, members)
            card.update({'task': subtask['kind'], 'title': _desk_title(request_row, subtask),
                         'brief': subtask.get('brief'), 'opened_at': subtask.get('opened_at'),
                         'clarify_label': subtask.get('clarify_label'),
                         'clarify_comment': subtask.get('clarify_comment'),
                         'clarify_by_name': subtask.get('clarify_by_name'),
                         # Своя заявка: строка стола не повторяет человеку его же имя.
                         'own': request_row.get('initiator_id') == ctx['user_id']})
            tasks.append(card)
        for request_row in docs:
            if hide_people:
                privacy.hide_in_row(request_row)
            card = _card(request_row, None, None, ctx, members)
            card.update({'task': 'docs_needed', 'title': 'Приложите закрывающие документы',
                         'brief': 'Накладная, акт или чек. Оригиналы передайте в бухгалтерию.',
                         'opened_at': None, 'own': True})
            tasks.append(card)
        return jsonify({'tasks': tasks, 'total': total, 'limit': limit, 'offset': offset})

    # ── Справки ─────────────────────────────────────────────────────────────

    @payments_route('/history')
    def history(ctx):
        with db._get_cursor() as cursor:
            rows = queries.payment_history(
                cursor, counterparty_id=as_int(request.args.get('counterparty_id')),
                category_id=as_int(request.args.get('category_id')),
                subcategory_id=as_int(request.args.get('subcategory_id')),
                expense_name=request.args.get('name'),
                exclude_request_id=as_int(request.args.get('exclude')), **_history_scope(ctx))
        return jsonify({'items': rows})

    @payments_route('/route-preview')
    def route_preview(ctx):
        """Кто согласует заявку с такими параметрами — ещё до отправки (п. 6:
        маршрут определяет система, инициатор его только видит)."""
        args = request.args
        draft = {
            'amount': workflow.to_decimal(args.get('amount')),
            'counterparty_id': as_int(args.get('counterparty_id')),
            'legal_entity_id': as_int(args.get('legal_entity_id')),
            'department_id': as_int(args.get('department_id')) or ctx.get('department_id'),
            'category_id': as_int(args.get('category_id')),
            'project_id': as_int(args.get('project_id')),
            'payment_method': as_choice(args.get('payment_method'), schema.PAYMENT_METHODS, 'Способ оплаты не из списка'),
            'request_kind': as_choice(args.get('request_kind'), schema.REQUEST_KINDS, 'Тип заявки не из списка')
                            or workflow.KIND_PURCHASE,
            'fixed_template_id': as_int(args.get('fixed_template_id')),
        }
        with db._get_cursor() as cursor:
            counterparty = directory.read_counterparty(cursor, draft['counterparty_id'])
            draft['counterparty_name'] = (counterparty or {}).get('name')
            basis = flow.approval_basis(cursor, draft)
            # Чужого руководителя показываем только администратору (он смотрит маршрут
            # чужой заявки); остальным — своего.
            initiator_id = as_int(args.get('initiator_id')) if access.is_section_admin(ctx) else None
            manager = queries.resolve_manager(cursor, initiator_id or ctx['user_id'])
            contract = directory.read_contract(cursor, as_int(args.get('contract_id')))
        ok, reason = workflow.contract_gate(
            draft['amount'], contract, counterparty_id=draft['counterparty_id'],
            on_date=as_date(args.get('on_date'), 'on_date') or queries.today_almaty())
        return jsonify({
            'route': basis,
            'manager': manager,
            'contract_check': {'applies': draft['payment_method'] == workflow.METHOD_INVOICE,
                               'ok': ok, 'reason': reason},
        })

    # ── Выгрузка ────────────────────────────────────────────────────────────

    @payments_route('/export')
    def export(ctx):
        with db._get_cursor() as cursor:
            filters = _scoped_filters(cursor, ctx, request.args)
            total, rows = queries.list_requests(cursor, limit=EXPORT_MAX_ROWS, offset=0, **filters)
            ids = [row['id'] for row in rows]
            items_by_request = queries.items_for_requests(cursor, ids)
            offers_by_request = queries.offers_for_requests(cursor, ids)
        if not access.sees_department_people(ctx, ctx['roles']):
            # Выгрузка — тот же реестр: «Текущий исполнитель» не называет сотрудника подразделения.
            for row in rows:
                privacy.hide_in_row(row)
        shown = {key: value for key, value in _list_filters(request.args).items()
                 if value not in (None, '', False)}
        stream = report.build_workbook(
            rows, items_by_request, offers_by_request=offers_by_request,
            filters_text=', '.join(sorted(shown)) if shown else '',
            total=total, truncated=total > len(rows), text_warning_patch=excel_text_warning)
        return send_file(stream, as_attachment=True, download_name=report.file_name(), mimetype=XLSX_MIME)

    api = Api(route=payments_route, db=db, gcs=gcs, flush=_flush, base_url=web_app_base_url,
              store_files=_store_files, check_uploads=_check_uploads)
    routes_directory.register(api)
    routes_assets.register(api)
    return bp
