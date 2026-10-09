"""Ручки справочников раздела: /api/payments/dictionaries, /roles, /settings, /templates.

ТЗ «Закуп и оплата», п. 13. Справочники ведёт администратор раздела; карты —
финансовый отдел (в них полные номера), категории имущества — ответственный за
учёт (payments/access.can_save_dictionary). Читает справочники любой участник
раздела, кроме карт: их список с номерами открывается только ведущим.

Подключается из routes.build_payments_blueprint через `register(api)`.
"""

from io import BytesIO

from flask import request, send_file

from . import access, cards, directory, files, fixed, queries, schema, workflow
from .web import (ApiError, XLSX_MIME, actor as _actor, as_bool, as_choice, as_date, as_int, as_money, as_text,
                  digits, jsonify, party_kind, payload as _payload)

# Справочник → как его читать списком.
_READERS = {
    'projects': lambda cursor, args: directory.list_projects(cursor, include_inactive=True),
    'categories': lambda cursor, args: directory.list_categories(cursor, include_inactive=True),
    'asset_categories': lambda cursor, args: directory.list_asset_categories(cursor, include_inactive=True),
    'legal_entities': lambda cursor, args: directory.list_legal_entities(cursor, include_inactive=True),
    'counterparties': lambda cursor, args: directory.list_counterparties(
        cursor, include_inactive=True, query=args.get('q')),
    'counterparty_accounts': lambda cursor, args: directory.list_accounts(
        cursor, counterparty_id=as_int(args.get('counterparty_id')), include_inactive=True),
    'contracts': lambda cursor, args: directory.list_contracts(
        cursor, counterparty_id=as_int(args.get('counterparty_id'))),
    'cards': lambda cursor, args: directory.list_cards(cursor, include_inactive=True),
    'limits': lambda cursor, args: directory.list_limits(cursor),
    'routes': lambda cursor, args: directory.list_routes(cursor),
}


def _iban(value, *, required=False):
    """ИИК (IBAN): латиница и цифры без пробелов. Длину не проверяем — счёт
    может быть и зарубежным."""
    text = ''.join(str(value or '').upper().split())
    if not text:
        if required:
            raise ApiError('Укажите ИИК (номер счёта)')
        return None
    if not text.isalnum() or len(text) > 34:
        raise ApiError('ИИК: только латинские буквы и цифры, до 34 знаков')
    return text


def _user(cursor, value, field):
    """Сотрудник справочника (согласующий, ответственный). Уволенного не выбрать:
    заявка, ушедшая ему, не дошла бы ни до кого."""
    user_id = as_int(value, field)
    if user_id:
        person = queries.user_brief(cursor, user_id)
        if not person or person['fired']:
            raise ApiError('%s: сотрудник не найден или уволен' % field, status=404)
    return user_id


def register(api):
    route, db, gcs = api.route, api.db, api.gcs

    # ═══════════════════════════════════════════════════════════════════════
    # Форма заявки: всё нужное одним запросом
    # ═══════════════════════════════════════════════════════════════════════

    @route('/dictionaries')
    def dictionaries(ctx):
        with db._get_cursor() as cursor:
            bundle = {
                'projects': directory.list_projects(cursor),
                'categories': directory.list_categories(cursor),
                'asset_categories': directory.list_asset_categories(cursor),
                'legal_entities': directory.list_legal_entities(cursor),
                'counterparties': directory.list_counterparties(cursor),
                'counterparty_accounts': directory.list_accounts(cursor),
                'contracts': directory.list_contracts(cursor),
                'templates': [item for item in directory.list_templates(cursor, include_inactive=False)],
                'departments': directory.list_departments(cursor),
                'manager': queries.resolve_manager(cursor, ctx['user_id']),
                'settings': directory.get_settings(cursor),
            }
        for entity in bundle['legal_entities']:
            entity['requisites_text'] = directory.requisites_text(entity)
        return jsonify(bundle)

    @route('/cards/options')
    def card_options(ctx):
        """Сохранённые карты получателя для формы заявки — только маски.
        Выбрать карту может любой инициатор; увидеть номер — нет."""
        owner_kind = as_choice(request.args.get('owner_kind'), schema.CARD_OWNER_KINDS, 'Получатель не из списка')
        user_id = as_int(request.args.get('user_id'))
        counterparty_id = as_int(request.args.get('counterparty_id'))
        if not owner_kind or not (user_id or counterparty_id):
            return jsonify({'items': []})
        with db._get_cursor() as cursor:
            items = directory.list_cards(cursor, owner_kind=owner_kind, user_id=user_id,
                                         counterparty_id=counterparty_id)
        return jsonify({'items': items})

    # ═══════════════════════════════════════════════════════════════════════
    # Справочники: список, запись, удаление
    # ═══════════════════════════════════════════════════════════════════════

    @route('/dictionaries/<name>')
    def dictionary_full(ctx, name):
        if name not in _READERS:
            raise ApiError('Нет такого справочника', status=404)
        if not access.can_view_dictionary(ctx, name, ctx['roles']):
            raise ApiError('Этот справочник ведёт финансовый отдел', code='PAYMENTS_FORBIDDEN', status=403)
        with db._get_cursor() as cursor:
            rows = _READERS[name](cursor, request.args)
        if name == 'legal_entities':
            for entity in rows:
                entity['requisites_text'] = directory.requisites_text(entity)
        return jsonify({'items': rows,
                        'can_edit': access.can_save_dictionary(ctx, name, row_id=1, roles=ctx['roles'])})

    def _save_named(cursor, name, data, row_id, ctx):
        title = as_text(data.get('name'), 'Название', required=True, limit=200)
        active = as_bool(data.get('is_active', True))
        if name == 'projects':
            return directory.upsert_project(cursor, project_id=row_id, name=title, is_active=active,
                                            actor_id=ctx['user_id'])
        if name == 'asset_categories':
            return directory.upsert_asset_category(cursor, category_id=row_id, name=title, is_active=active,
                                                   actor_id=ctx['user_id'])
        return directory.upsert_category(
            cursor, category_id=row_id, parent_id=as_int(data.get('parent_id'), 'parent_id'), name=title,
            position=as_int(data.get('position'), 'position') or 0, is_active=active, actor_id=ctx['user_id'])

    def _save_legal_entity(cursor, data, row_id, ctx):
        return directory.upsert_legal_entity(cursor, entity_id=row_id, fields={
            'name': as_text(data.get('name'), 'Название', required=True, limit=200),
            'bin': digits(data.get('bin')) or None,
            'kind': party_kind(data.get('kind')),
            'vat_payer': as_bool(data.get('vat_payer')),
            'legal_address': as_text(data.get('legal_address'), 'Юридический адрес', limit=1000),
            'bank_name': as_text(data.get('bank_name'), 'Банк', limit=200),
            'iik': _iban(data.get('iik')),
            'bik': as_text(data.get('bik'), 'БИК', limit=16),
            'kbe': as_text(data.get('kbe'), 'КБЕ', limit=4),
            'requisites': as_text(data.get('requisites'), 'Прочие реквизиты', limit=4000),
            'note': as_text(data.get('note'), 'Примечание', limit=2000),
            'is_active': as_bool(data.get('is_active', True)),
        }, actor_id=ctx['user_id'])

    def _save_counterparty(cursor, data, row_id, ctx):
        category_id = as_int(data.get('category_id'), 'category_id')
        return directory.upsert_counterparty(cursor, counterparty_id=row_id, fields={
            'name': as_text(data.get('name'), 'Наименование', required=True, limit=200),
            'legal_name': as_text(data.get('legal_name'), 'Юридическое наименование', limit=300),
            'bin': digits(data.get('bin')) or None,
            'kind': party_kind(data.get('kind')),
            'vat_payer': as_bool(data.get('vat_payer')),
            'requisites': as_text(data.get('requisites'), 'Прочие реквизиты', limit=4000),
            'contact': as_text(data.get('contact'), 'Контакт', limit=1000),
            'category_id': category_id,
            'responsible_user_id': _user(cursor, data.get('responsible_user_id'), 'Ответственный'),
            # П. 13.1 и п. 6: согласующий поставщика и лимит, в пределах
            # которого заявка уходит ему сама.
            'approver_user_id': _user(cursor, data.get('approver_user_id'), 'Согласующий'),
            'approval_limit': as_money(data.get('approval_limit'), 'Лимит согласования'),
            'note': as_text(data.get('note'), 'Примечание', limit=2000),
            'is_active': as_bool(data.get('is_active', True)),
        }, actor_id=ctx['user_id'])

    def _save_account(cursor, data, row_id, ctx):
        counterparty_id = as_int(data.get('counterparty_id'), 'counterparty_id')
        if not counterparty_id or not directory.read_counterparty(cursor, counterparty_id):
            raise ApiError('Укажите поставщика, чьи это реквизиты')
        return directory.upsert_account(cursor, account_id=row_id, fields={
            'counterparty_id': counterparty_id,
            'bank_name': as_text(data.get('bank_name'), 'Банк', limit=200),
            'iik': _iban(data.get('iik'), required=True),
            'bik': as_text(data.get('bik'), 'БИК', limit=16),
            'kbe': as_text(data.get('kbe'), 'КБЕ', limit=4),
            'note': as_text(data.get('note'), 'Примечание', limit=2000),
            'is_default': as_bool(data.get('is_default')),
            'is_active': as_bool(data.get('is_active', True)),
        }, actor_id=ctx['user_id'])

    def _save_contract(cursor, data, row_id, ctx):
        status = as_choice(data.get('status') or 'active', schema.CONTRACT_STATUSES, 'Статус договора не из списка')
        counterparty_id = as_int(data.get('counterparty_id'), 'counterparty_id')
        if not counterparty_id:
            raise ApiError('Укажите поставщика договора')
        starts_on, ends_on = as_date(data.get('starts_on'), 'starts_on'), as_date(data.get('ends_on'), 'ends_on')
        if starts_on and ends_on and ends_on < starts_on:
            raise ApiError('Дата окончания договора раньше даты начала')
        return directory.upsert_contract(cursor, contract_id=row_id, fields={
            'counterparty_id': counterparty_id,
            'legal_entity_id': as_int(data.get('legal_entity_id'), 'legal_entity_id'),
            'number': as_text(data.get('number'), 'Номер договора', required=True, limit=100),
            'signed_on': as_date(data.get('signed_on'), 'signed_on'),
            'starts_on': starts_on, 'ends_on': ends_on, 'status': status,
            'subject': as_text(data.get('subject'), 'Предмет', limit=2000),
            'amount': as_money(data.get('amount'), 'Сумма договора'),
            'amount_limit': as_money(data.get('amount_limit'), 'Лимит'),
            'periodicity': as_choice(data.get('periodicity'), schema.PERIODICITIES, 'Периодичность не из списка'),
            'responsible_user_id': _user(cursor, data.get('responsible_user_id'), 'Ответственный'),
            'note': as_text(data.get('note'), 'Примечание', limit=2000),
        }, actor_id=ctx['user_id'])

    def _save_card(cursor, data, row_id, ctx):
        owner_kind = as_choice(data.get('owner_kind'), schema.CARD_OWNER_KINDS,
                               'Чья карта: сотрудника или поставщика')
        if not owner_kind:
            raise ApiError('Выберите, чья карта: сотрудника или поставщика')
        user_id = counterparty_id = None
        holder = as_text(data.get('holder_name'), 'Владелец карты', limit=200)
        if owner_kind == 'employee':
            user_id = as_int(data.get('user_id'), 'user_id')
            owner = queries.user_brief(cursor, user_id) if user_id else None
            if not owner or owner['fired']:
                raise ApiError('Выберите сотрудника — владельца карты')
            holder = holder or owner['name']
        else:
            counterparty_id = as_int(data.get('counterparty_id'), 'counterparty_id')
            if not counterparty_id or not directory.read_counterparty(cursor, counterparty_id):
                raise ApiError('Выберите поставщика — владельца карты')
            if not holder:
                raise ApiError('Укажите ФИО владельца карты')
        number = cards.digits(data.get('card_number'))
        if number or not row_id:
            problem = cards.problem(number)
            if problem:
                raise ApiError(problem)
            # Та же карта того же владельца второй строкой — дубль: в форме заявки
            # появились бы два неразличимых варианта «•••• 1234».
            for other in directory.list_cards(cursor, owner_kind=owner_kind, user_id=user_id,
                                              counterparty_id=counterparty_id, include_inactive=True):
                if other['id'] == row_id or other.get('card_last4') != cards.last4(number):
                    continue
                saved = directory.read_card(cursor, other['id']) or {}
                if cards.decrypt(saved.get('card_number_enc')) == number:
                    raise ApiError('Эта карта уже есть в справочнике', code='PAYMENTS_DUPLICATE', status=409,
                                   extra={'id': other['id']})
        return directory.upsert_card(cursor, card_id=row_id, number=number or None, fields={
            'owner_kind': owner_kind, 'user_id': user_id, 'counterparty_id': counterparty_id,
            'holder_name': holder, 'note': as_text(data.get('note'), 'Примечание', limit=2000),
            'is_active': as_bool(data.get('is_active', True)),
        }, actor_id=ctx['user_id'])

    def _save_limit(cursor, data, row_id, ctx):
        """Лимит согласования (п. 6): кто, до какой суммы и по каким поставщикам
        согласует. Номер и дата Приказа — необязательное основание."""
        status = as_choice(data.get('status') or 'active', schema.ORDER_STATUSES,
                           'Статус лимита: действующий или отменён')
        delegate_id = as_int(data.get('delegate_user_id'), 'delegate_user_id')
        delegate = queries.user_brief(cursor, delegate_id) if delegate_id else None
        if not delegate:
            raise ApiError('Укажите согласующего')
        if delegate['fired'] and status == 'active':
            # Отменить лимит уволенного можно, оставить действующим — нет: заявка ушла бы ему.
            raise ApiError('Согласующий уволен — выберите другого или отмените лимит')
        issued_on = as_date(data.get('issued_on'), 'issued_on')
        starts_on = as_date(data.get('starts_on'), 'starts_on') or issued_on
        ends_on = as_date(data.get('ends_on'), 'ends_on')
        if starts_on and ends_on and ends_on < starts_on:
            raise ApiError('Дата окончания раньше даты начала')
        project_ids = [as_int(x) for x in (data.get('project_ids') or []) if as_int(x)]
        counterparty_ids = [as_int(x) for x in (data.get('counterparty_ids') or []) if as_int(x)]
        # Пустой перечень означает «все»: лимит без названных поставщиков
        # действует на любого, а не ни на кого.
        all_projects = as_bool(data.get('all_projects')) or not project_ids
        all_counterparties = as_bool(data.get('all_counterparties')) or not counterparty_ids
        return directory.upsert_limit(cursor, limit_id=row_id, fields={
            'number': as_text(data.get('number'), 'Номер Приказа', limit=100),
            'issued_on': issued_on, 'starts_on': starts_on, 'ends_on': ends_on, 'status': status,
            'replaces_role': workflow.ROLE_APPROVER, 'delegate_user_id': delegate_id,
            'amount_limit': as_money(data.get('amount_limit'), 'Лимит суммы'),
            'all_projects': all_projects, 'all_counterparties': all_counterparties,
            'legal_entity_id': as_int(data.get('legal_entity_id'), 'legal_entity_id'),
            'department_id': as_int(data.get('department_id'), 'department_id'),
            'category_id': as_int(data.get('category_id'), 'category_id'),
            'payment_method': as_choice(data.get('payment_method'), schema.PAYMENT_METHODS,
                                        'Тип платежа не из списка'),
            'request_kind': as_choice(data.get('request_kind'), schema.REQUEST_KINDS,
                                      'Регулярность не из списка'),
            'note': as_text(data.get('note'), 'Примечание', limit=2000),
        }, project_ids=[] if all_projects else project_ids,
            counterparty_ids=[] if all_counterparties else counterparty_ids, actor_id=ctx['user_id'])

    def _save_route(cursor, data, row_id, ctx):
        amount_from = as_money(data.get('amount_from'), 'Сумма от')
        amount_to = as_money(data.get('amount_to'), 'Сумма до')
        if amount_from is not None and amount_to is not None and amount_to < amount_from:
            raise ApiError('«Сумма до» меньше «Суммы от»')
        mode = as_choice(data.get('manager_step') or 'auto', schema.MANAGER_STEP_MODES,
                         'Этап руководителя: по умолчанию, обязателен или не нужен')
        return directory.upsert_route(cursor, route_id=row_id, fields={
            'name': as_text(data.get('name'), 'Название', required=True, limit=200),
            'position': as_int(data.get('position'), 'position') or 0,
            'legal_entity_id': as_int(data.get('legal_entity_id'), 'legal_entity_id'),
            'department_id': as_int(data.get('department_id'), 'department_id'),
            'category_id': as_int(data.get('category_id'), 'category_id'),
            'payment_method': as_choice(data.get('payment_method'), schema.PAYMENT_METHODS,
                                        'Тип платежа не из списка'),
            'request_kind': as_choice(data.get('request_kind'), schema.REQUEST_KINDS, 'Регулярность не из списка'),
            'amount_from': amount_from, 'amount_to': amount_to, 'manager_step': mode,
            'approver_user_id': _user(cursor, data.get('approver_user_id'), 'Утверждающий'),
            'is_active': as_bool(data.get('is_active', True)),
            'note': as_text(data.get('note'), 'Примечание', limit=2000),
        }, actor_id=ctx['user_id'])

    _SAVERS = {
        'legal_entities': _save_legal_entity, 'counterparties': _save_counterparty,
        'counterparty_accounts': _save_account, 'contracts': _save_contract, 'cards': _save_card,
        'limits': _save_limit, 'routes': _save_route,
    }

    @route('/dictionaries/<name>', methods=('POST',))
    def dictionary_save(ctx, name):
        if name not in _READERS:
            raise ApiError('Нет такого справочника', status=404)
        data = _payload()
        row_id = as_int(data.get('id'), 'id')
        if not access.can_save_dictionary(ctx, name, row_id, ctx['roles']):
            raise ApiError('Это действие доступно администратору раздела', code='PAYMENTS_ADMIN_ONLY', status=403)
        with db._get_cursor() as cursor:
            duplicate = directory.find_duplicate_name(
                cursor, str(data.get('name') or '').strip(), dictionary=name, row_id=row_id,
                parent_id=as_int(data.get('parent_id'), 'parent_id'))
            if duplicate:
                raise ApiError('«%s» уже есть в справочнике' % duplicate[1],
                               code='PAYMENTS_DUPLICATE', status=409, extra={'id': duplicate[0]})
            if name in _SAVERS:
                saved = _SAVERS[name](cursor, data, row_id, ctx)
            else:
                saved = _save_named(cursor, name, data, row_id, ctx)
        if not saved:
            raise ApiError('Запись не найдена', status=404)
        return jsonify({'status': 'success', 'id': saved})

    @route('/dictionaries/<name>/<int:row_id>', methods=('DELETE',))
    def dictionary_delete(ctx, name, row_id):
        if name not in directory.DICTIONARIES:
            raise ApiError('Нет такого справочника', status=404)
        if not access.can_save_dictionary(ctx, name, row_id, ctx['roles']):
            raise ApiError('Это действие доступно администратору раздела', code='PAYMENTS_ADMIN_ONLY', status=403)
        stale_file = None
        with db._get_cursor() as cursor:
            # Удаляется только то, на что ничто не ссылается: используемую запись скрывают.
            used = directory.usage(cursor, name, row_id)
            if used:
                raise ApiError('Удалить нельзя — запись используется: %s. Скройте её вместо удаления'
                               % ', '.join('%s — %s' % item for item in used),
                               code='PAYMENTS_IN_USE', status=409, extra={'usage': dict(used)})
            if name == 'contracts':
                stale_file = directory.contract_file(cursor, row_id)
            deleted = directory.delete_row(cursor, name, row_id)
        if not deleted:
            raise ApiError('Запись не найдена', status=404)
        if stale_file and gcs.get('client'):
            files.drop(gcs, [(stale_file['bucket'], stale_file['blob_path'])])
        return jsonify({'status': 'success'})

    # ── Файл договора (п. 13.2) ─────────────────────────────────────────────

    @route('/dictionaries/contracts/<int:contract_id>/file', methods=('POST',))
    def contract_file_upload(ctx, contract_id):
        if not access.can_save_dictionary(ctx, 'contracts', contract_id, ctx['roles']):
            raise ApiError('Это действие доступно администратору раздела', code='PAYMENTS_ADMIN_ONLY', status=403)
        storage = request.files.get('file')
        if not storage or not storage.filename:
            raise ApiError('Приложите файл договора', code='PAYMENT_FILES_MISSING')
        data = storage.read() or b''
        files.check_file(filename=storage.filename, content_type=storage.mimetype, size=len(data))
        with db._get_cursor() as cursor:
            if not directory.read_contract(cursor, contract_id):
                raise ApiError('Договор не найден', status=404)
            bucket, blob_path = files.upload(gcs, data=data, filename=storage.filename,
                                             content_type=storage.mimetype)
            previous = directory.set_contract_file(
                cursor, contract_id, file_name=files.safe_name(storage.filename),
                file_type=storage.mimetype or 'application/octet-stream', file_size=len(data),
                bucket=bucket, blob_path=blob_path)
            contract = directory.read_contract(cursor, contract_id)
        if previous and gcs.get('client'):
            files.drop(gcs, [(previous['bucket'], previous['blob_path'])])
        return jsonify({'status': 'success', 'item': contract})

    @route('/dictionaries/contracts/<int:contract_id>/file', methods=('DELETE',))
    def contract_file_remove(ctx, contract_id):
        if not access.can_save_dictionary(ctx, 'contracts', contract_id, ctx['roles']):
            raise ApiError('Это действие доступно администратору раздела', code='PAYMENTS_ADMIN_ONLY', status=403)
        with db._get_cursor() as cursor:
            previous = directory.set_contract_file(cursor, contract_id)
        if previous and gcs.get('client'):
            files.drop(gcs, [(previous['bucket'], previous['blob_path'])])
        return jsonify({'status': 'success'})

    @route('/dictionaries/contracts/<int:contract_id>/file')
    def contract_file_download(ctx, contract_id):
        with db._get_cursor() as cursor:
            item = directory.contract_file(cursor, contract_id)
        if not item:
            raise ApiError('К договору файл не приложен', status=404)
        if not gcs.get('client'):
            raise ApiError('Хранилище файлов не настроено', code='PAYMENT_STORAGE_OFF', status=503)
        data = files.download(gcs, item['bucket'], item['blob_path'])
        if data is None:
            raise ApiError('Файла нет в хранилище', status=404)
        return send_file(BytesIO(data), as_attachment=not as_bool(request.args.get('inline')),
                         download_name=item['file_name'],
                         mimetype=item.get('file_type') or 'application/octet-stream')

    @route('/dictionaries/cards/<int:card_id>/number')
    def card_number(ctx, card_id):
        """Полный номер карты из справочника — тем, кто его ведёт (п. 13)."""
        if not access.can_manage_cards(ctx, ctx['roles']):
            raise ApiError('Полный номер карты доступен финансовому отделу', code='PAYMENTS_FORBIDDEN', status=403)
        with db._get_cursor() as cursor:
            item = directory.read_card(cursor, card_id)
        number = cards.decrypt((item or {}).get('card_number_enc'))
        if not number:
            raise ApiError('Номер карты не сохранён или не читается', code='PAYMENT_CARD_MISSING', status=404)
        return jsonify({'number': number, 'pretty': cards.pretty(number)})

    # ═══════════════════════════════════════════════════════════════════════
    # Настройки процесса
    # ═══════════════════════════════════════════════════════════════════════

    @route('/settings')
    def settings_read(ctx):
        with db._get_cursor() as cursor:
            values = directory.get_settings(cursor)
        return jsonify({'settings': values, 'limits': workflow.SETTINGS_LIMITS})

    @route('/settings', methods=('POST',), admin=True)
    def settings_save(ctx):
        data = _payload()
        with db._get_cursor() as cursor:
            for key, (low, high) in workflow.SETTINGS_LIMITS.items():
                if key not in data:
                    continue
                value = as_int(data.get(key), key)
                if value is None or not low <= value <= high:
                    raise ApiError('Значение от %s до %s' % (low, high), code='PAYMENT_SETTING_INVALID')
                directory.save_setting(cursor, key, value, ctx['user_id'])
            values = directory.get_settings(cursor)
        return jsonify({'status': 'success', 'settings': values})

    # ═══════════════════════════════════════════════════════════════════════
    # Участники ролей
    # ═══════════════════════════════════════════════════════════════════════

    # Поимённый состав ролей видит только администратор раздела: остальным
    # незачем знать, кто в бухгалтерии и финансовом отделе (п. 9 ТЗ).
    @route('/roles', admin=True)
    def roles(ctx):
        with db._get_cursor() as cursor:
            members = access.mark_section_access(queries.role_members(cursor))
        return jsonify({'roles': members, 'labels': {k: workflow.ROLE_LABELS[k] for k in schema.MEMBER_ROLES}})

    @route('/roles', methods=('POST',), admin=True)
    def roles_add(ctx):
        data = _payload()
        role_code = str(data.get('role_code') or '').strip()
        user_id = as_int(data.get('user_id'), 'user_id')
        if role_code not in schema.MEMBER_ROLES or not user_id:
            raise ApiError('Нужны роль и сотрудник')
        with db._get_cursor() as cursor:
            person = queries.user_brief(cursor, user_id)
            if not person or person['fired']:
                raise ApiError('Сотрудник не найден или уволен', status=404)
            queries.add_role_member(cursor, role_code, user_id, ctx['user_id'])
            members = access.mark_section_access(queries.role_members(cursor))
        return jsonify({'status': 'success', 'roles': members})

    @route('/roles/<role_code>/<int:user_id>', methods=('DELETE',), admin=True)
    def roles_remove(ctx, role_code, user_id):
        if role_code not in schema.MEMBER_ROLES:
            raise ApiError('Нет такой роли', status=404)
        with db._get_cursor() as cursor:
            # Последнего участника роли не убирают, пока за ролью есть заявки в
            # работе: их задачи не увидел бы никто (п. 1 ТЗ — «исключить потерю заявок»).
            left = queries.role_member_ids(cursor).get(role_code, set()) - {user_id}
            waiting = 0 if left else queries.role_backlog(cursor, role_code)
            if waiting:
                raise ApiError('Это последний участник роли «%s», а за ней %s в работе. Сначала добавьте '
                               'другого участника' % (workflow.ROLE_LABELS[role_code],
                                                      workflow._plural(waiting, ('заявка', 'заявки', 'заявок'))),
                               code='PAYMENTS_ROLE_IN_USE', status=409)
            queries.remove_role_member(cursor, role_code, user_id)
            members = access.mark_section_access(queries.role_members(cursor))
        return jsonify({'status': 'success', 'roles': members})

    # ═══════════════════════════════════════════════════════════════════════
    # Регулярные платежи (п. 4.4, п. 13)
    # ═══════════════════════════════════════════════════════════════════════

    @route('/templates')
    def templates_list(ctx):
        with db._get_cursor() as cursor:
            rows = directory.list_templates(cursor)
        today = queries.today_almaty()
        for row in rows:
            due = workflow._as_date(row.get('next_due_on'))
            row['generate_on'] = fixed.generate_on(due, row['periodicity'], row.get('lead_days')) if due else None
            row['is_due'] = fixed.is_due(row, today)
            row['periodicity_label'] = fixed.PERIOD_LABELS.get(row['periodicity'], row['periodicity'])
        return jsonify({'items': rows})

    def _validate_template(cursor, data):
        periodicity = as_choice(data.get('periodicity') or 'monthly', schema.PERIODICITIES,
                                'Периодичность не из списка')
        interval_days = as_int(data.get('interval_days'), 'interval_days')
        if periodicity == 'custom' and not (interval_days and interval_days > 0):
            raise ApiError('Для своего интервала укажите число дней')
        amount = workflow.to_decimal(data.get('amount'))
        if amount <= 0:
            raise ApiError('Сумма платежа должна быть больше нуля')
        next_due_on = as_date(data.get('next_due_on'), 'next_due_on')
        if not next_due_on:
            raise ApiError('Укажите ближайшую дату оплаты')
        responsible_id = as_int(data.get('responsible_user_id'), 'responsible_user_id')
        responsible = queries.user_brief(cursor, responsible_id) if responsible_id else None
        if not responsible or responsible['fired']:
            raise ApiError('Укажите ответственного за платёж')
        object_type = as_choice(data.get('object_type') or 'service', schema.OBJECT_TYPES,
                                'Тип объекта: товар или услуга')
        accounting_category = as_choice(data.get('accounting_category'), schema.ACCOUNTING_CATEGORIES,
                                        'Категория учёта: расходный материал или имущество')
        if object_type == workflow.OBJECT_GOODS and not accounting_category:
            # П. 10: у товара категория учёта обязательна; заявка берёт её отсюда (п. 4.4).
            raise ApiError('Для товара выберите категорию учёта: расходный материал или имущество')
        lead_days = as_int(data.get('lead_days'), 'lead_days')
        counterparty_id = as_int(data.get('counterparty_id'), 'counterparty_id')
        contract_id = as_int(data.get('contract_id'), 'contract_id')
        account_id = as_int(data.get('counterparty_account_id'), 'counterparty_account_id')
        if contract_id:
            contract = directory.read_contract(cursor, contract_id)
            if not contract or (counterparty_id and contract['counterparty_id'] != counterparty_id):
                raise ApiError('Договор заключён с другим поставщиком')
        if account_id:
            account = directory.read_account(cursor, account_id)
            if not account or (counterparty_id and account['counterparty_id'] != counterparty_id):
                raise ApiError('Банковские реквизиты принадлежат другому поставщику')
        return {
            'name': as_text(data.get('name'), 'Наименование', required=True, limit=300),
            'amount': amount, 'periodicity': periodicity,
            'interval_days': interval_days if periodicity == 'custom' else None,
            'next_due_on': next_due_on,
            'lead_days': 7 if lead_days is None else max(0, lead_days),
            'project_id': as_int(data.get('project_id'), 'project_id'),
            'branch': as_text(data.get('branch'), 'branch'),
            'responsible_user_id': responsible_id,
            'category_id': as_int(data.get('category_id'), 'category_id'),
            'subcategory_id': as_int(data.get('subcategory_id'), 'subcategory_id'),
            'counterparty_id': counterparty_id,
            'legal_entity_id': as_int(data.get('legal_entity_id'), 'legal_entity_id'),
            'contract_id': contract_id,
            'counterparty_account_id': account_id,
            'payment_purpose': as_text(data.get('payment_purpose'), 'payment_purpose'),
            'amount_limit': as_money(data.get('amount_limit'), 'Лимит'),
            'approver_user_id': _user(cursor, data.get('approver_user_id'), 'Согласующий'),
            'object_type': object_type,
            'accounting_category': accounting_category if object_type == workflow.OBJECT_GOODS else None,
            'due_day': fixed.due_day_of(next_due_on, periodicity),
            'auto_create': as_bool(data.get('auto_create', True)),
            'note': as_text(data.get('note'), 'notes'),
            'is_active': as_bool(data.get('is_active', True)),
        }

    @route('/templates', methods=('POST',), admin=True)
    def templates_save(ctx):
        data = _payload()
        with db._get_cursor() as cursor:
            fields = _validate_template(cursor, data)
            template_id = directory.upsert_template(cursor, template_id=as_int(data.get('id'), 'id'),
                                                    fields=fields, actor_id=ctx['user_id'])
            row = directory.read_template(cursor, template_id)
        return jsonify({'status': 'success', 'item': row})

    @route('/templates/<int:template_id>', methods=('DELETE',), admin=True)
    def templates_delete(ctx, template_id):
        with db._get_cursor() as cursor:
            # Платёж, по которому уже есть заявки, приостанавливают: удаление
            # оторвало бы их от справочника (в карточке пропал бы «Регулярный платёж»).
            used = directory.template_requests(cursor, template_id)
            if used:
                raise ApiError('По платежу есть %s — приостановите его вместо удаления'
                               % workflow._plural(used, ('заявка', 'заявки', 'заявок')),
                               code='PAYMENTS_IN_USE', status=409)
            deleted = directory.delete_template(cursor, template_id)
        if not deleted:
            raise ApiError('Платёж не найден', status=404)
        return jsonify({'status': 'success'})

    @route('/templates/generate', methods=('POST',), admin=True)
    def templates_generate(ctx):
        """Ручной запуск того же, что делает ночной планировщик."""
        with db._get_cursor() as cursor:
            created, outbox = fixed.generate(cursor, actor=_actor(ctx), base_url=api.base_url)
        return jsonify({'status': 'success', 'created': created, 'warnings': api.flush(outbox)})

    @route('/templates/<int:template_id>/generate', methods=('POST',), admin=True)
    def template_generate_now(ctx, template_id):
        """Создать заявку по платежу сейчас, не дожидаясь начала периода."""
        with db._get_cursor() as cursor:
            template = directory.read_template(cursor, template_id, lock=True)
            if not template:
                raise ApiError('Платёж не найден', status=404)
            outbox = []
            try:
                request_id = fixed.create_from_template(cursor, template, actor=_actor(ctx),
                                                        today=queries.today_almaty(), outbox=outbox,
                                                        base_url=api.base_url)
            except ValueError as exc:
                raise ApiError(str(exc), code='PAYMENT_TEMPLATE_INCOMPLETE', status=409)
        return jsonify({'status': 'success', 'request_id': request_id, 'warnings': api.flush(outbox)})

    @route('/templates/import', methods=('POST',), admin=True)
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

    @route('/templates/import/confirm', methods=('POST',), admin=True)
    def templates_import_confirm(ctx):
        data = _payload()
        rows = data.get('rows') or []
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
            interval_days = as_int(row.get('interval_days'))
            row['interval_days'] = interval_days if row['periodicity'] == 'custom' else None
            if row['periodicity'] == 'custom' and not (interval_days and interval_days > 0):
                # Без числа дней календарь не знал бы, когда создавать заявку.
                row['errors'].append('для своего интервала нужно число дней')
            if not as_int(row.get('responsible_user_id')):
                row['errors'].append('не определён ответственный')
        with db._get_cursor() as cursor:
            created = fixed.commit_import(cursor, rows, ctx['user_id'])
        return jsonify({'status': 'success', 'created': len(created),
                        'existing': sum(1 for r in rows if r.get('exists') and not r.get('errors')),
                        'skipped': sum(1 for r in rows if r.get('errors'))})

    @route('/templates/sample')
    def templates_sample(ctx):
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Регулярные платежи'
        for row in fixed.template_sample_rows():
            sheet.append(list(row))
        for column, width in zip('ABCDEFGHIJK', (32, 12, 16, 14, 22, 24, 16, 16, 24, 16, 24)):
            sheet.column_dimensions[column].width = width
        stream = BytesIO()
        workbook.save(stream)
        stream.seek(0)
        return send_file(stream, as_attachment=True, download_name='Регулярные платежи.xlsx',
                         mimetype=XLSX_MIME)

    return api
