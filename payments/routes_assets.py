"""Ручки учёта имущества: /api/payments/assets и «моё имущество» для профиля.

ТЗ «Закуп и оплата», пп. 10–12. Реестр имущества видят ответственный за учёт,
бухгалтерия и администратор раздела; ведёт (правит карточку, перемещает) —
ответственный за учёт и администратор.

Одна ручка стоит вне периметра раздела — `/my-assets`: п. 12 требует, чтобы
переданное сотруднику имущество отображалось в его профиле, а профиль есть у
каждого. Она отдаёт только имущество самого смотрящего, без стоимости и без
ссылки на заявку.

Подключается из routes.build_payments_blueprint через `register(api)`.
"""

from flask import request

from . import access, assets as assets_sql, directory, queries, schema, workflow
from .web import (ApiError, actor as _actor, as_choice, as_date, as_int, as_money, as_text, jsonify,
                  payload as _payload, uploaded_files)

PAGE_SIZE_MAX = 500


def _place(cursor, data, errors, *, label=''):
    """Владелец и место имущества из запроса: подразделение, ответственный, город,
    место эксплуатации, статус — со снимками названий."""
    fields = {}
    department_id = as_int(data.get('department_id'), 'Подразделение')
    department = directory.department_brief(cursor, department_id) if department_id else None
    if department_id and not department:
        errors.append('%sподразделение не найдено' % label)
    fields['department_id'] = department_id if department else None
    fields['department_name'] = (department or {}).get('name')
    responsible_id = as_int(data.get('responsible_user_id'), 'Ответственный')
    responsible = queries.user_brief(cursor, responsible_id) if responsible_id else None
    if responsible_id and (not responsible or responsible['fired']):
        # Уволенному имущество не передают: числиться за ним оно не должно.
        errors.append('%sответственный сотрудник не найден или уволен' % label)
        responsible = None
    fields['responsible_user_id'] = responsible_id if responsible else None
    fields['responsible_name'] = (responsible or {}).get('name')
    fields['city'] = as_text(data.get('city'), 'Город', limit=120)
    fields['location'] = as_text(data.get('location'), 'Место эксплуатации', limit=300)
    fields['status'] = as_choice(data.get('status'), schema.ASSET_STATUSES, 'Статус имущества не из списка')
    return fields


def parse_assets(cursor, raw_assets, request_row):
    """Карточки имущества из действия «Поставить на учёт» (п. 10.2).

    Компания-владелец и дата получения по умолчанию берутся из заявки: имущество
    куплено этой компанией и получено в день, который подтвердил инициатор.
    Полнота (все ли обязательные поля) проверяется в workflow.asset_missing.
    """
    errors, result = [], []
    for index, raw in enumerate(raw_assets if isinstance(raw_assets, list) else [], start=1):
        raw = raw if isinstance(raw, dict) else {}
        label = 'Имущество №%s: ' % index
        fields = _place(cursor, raw, errors, label=label)
        category_id = as_int(raw.get('category_id'), 'Категория')
        if category_id:
            cursor.execute("SELECT 1 FROM payment_asset_categories WHERE id = %s", (category_id,))
            if not cursor.fetchone():
                errors.append('%sкатегория не найдена' % label)
                category_id = None
        legal_entity_id = as_int(raw.get('legal_entity_id'), 'Компания') or request_row.get('legal_entity_id')
        fields.update({
            'name': as_text(raw.get('name'), 'Наименование', limit=300),
            'category_id': category_id,
            'serial_number': as_text(raw.get('serial_number'), 'Серийный номер', limit=120),
            'inventory_number': as_text(raw.get('inventory_number'), 'Инвентарный номер', limit=64),
            'received_on': as_date(raw.get('received_on'), 'Дата получения') or request_row.get('received_on'),
            'cost': as_money(raw.get('cost'), 'Стоимость'),
            'legal_entity_id': legal_entity_id,
            'note': as_text(raw.get('note'), 'Примечание', limit=2000),
        })
        result.append(fields)
    if errors:
        raise ApiError('; '.join(errors), code='PAYMENT_ASSET_INVALID', extra={'errors': errors})
    return result


def register(api):
    route, db = api.route, api.db

    def _guard_view(ctx):
        if not access.can_view_assets(ctx, ctx['roles']):
            raise ApiError('Реестр имущества ведёт ответственный за учёт имущества',
                           code='PAYMENTS_FORBIDDEN', status=403)

    def _guard_manage(ctx):
        if not access.can_manage_assets(ctx, ctx['roles']):
            raise ApiError('Менять учёт имущества может ответственный за учёт имущества',
                           code='PAYMENTS_FORBIDDEN', status=403)

    @route('/assets')
    def assets_list(ctx):
        _guard_view(ctx)
        args = request.args
        limit = min(max(as_int(args.get('limit'), 'limit') or 100, 1), PAGE_SIZE_MAX)
        offset = max(as_int(args.get('offset'), 'offset') or 0, 0)
        with db._get_cursor() as cursor:
            total, items = assets_sql.list_assets(
                cursor, query=(args.get('q') or '').strip() or None,
                city=(args.get('city') or '').strip() or None,
                department_id=as_int(args.get('department_id')),
                responsible_user_id=as_int(args.get('responsible_user_id')),
                status=as_choice(args.get('status'), schema.ASSET_STATUSES, 'Статус не из списка'),
                category_id=as_int(args.get('category_id')),
                legal_entity_id=as_int(args.get('legal_entity_id')),
                limit=limit, offset=offset)
            facets = assets_sql.filter_values(cursor)
        return jsonify({'items': items, 'total': total, 'limit': limit, 'offset': offset, 'facets': facets,
                        'can_manage': access.can_manage_assets(ctx, ctx['roles'])})

    @route('/assets/<int:asset_id>')
    def asset_read(ctx, asset_id):
        _guard_view(ctx)
        with db._get_cursor() as cursor:
            item = assets_sql.read_asset(cursor, asset_id)
            if not item:
                raise ApiError('Имущество не найдено', status=404)
            moves = assets_sql.list_moves(cursor, asset_id)
        return jsonify({'asset': item, 'moves': moves,
                        'can_manage': access.can_manage_assets(ctx, ctx['roles'])})

    @route('/assets/<int:asset_id>', methods=('PATCH',))
    def asset_edit(ctx, asset_id):
        """Правка описания карточки. Владельца, место и статус меняет перемещение."""
        _guard_manage(ctx)
        data = _payload()
        with db._get_cursor() as cursor:
            current = assets_sql.read_asset(cursor, asset_id, lock=True)
            if not current:
                raise ApiError('Имущество не найдено', status=404)
            fields = {}
            if 'name' in data:
                fields['name'] = as_text(data.get('name'), 'Наименование', required=True, limit=300)
            if 'category_id' in data:
                fields['category_id'] = as_int(data.get('category_id'), 'Категория')
            if 'serial_number' in data:
                fields['serial_number'] = as_text(data.get('serial_number'), 'Серийный номер', required=True, limit=120)
            if 'inventory_number' in data:
                number = as_text(data.get('inventory_number'), 'Инвентарный номер', required=True, limit=64)
                taken = assets_sql.inventory_taken(cursor, number, exclude_id=asset_id)
                if taken:
                    raise ApiError('Инвентарный номер %s уже занят: «%s»' % (number, taken['name']),
                                   code='PAYMENT_ASSET_DUPLICATE', status=409)
                fields['inventory_number'] = number
            if 'received_on' in data:
                fields['received_on'] = as_date(data.get('received_on'), 'Дата получения')
            if 'cost' in data:
                fields['cost'] = as_money(data.get('cost'), 'Стоимость')
            if 'legal_entity_id' in data:
                fields['legal_entity_id'] = as_int(data.get('legal_entity_id'), 'Компания')
            if 'note' in data:
                fields['note'] = as_text(data.get('note'), 'Примечание', limit=2000)
            # П. 10.2: обязательные поля карточки остаются обязательными и после
            # постановки на учёт — правкой их не обнулить.
            missing = workflow.asset_missing(dict(current, **fields))
            if missing:
                raise ApiError('Не заполнено: ' + ', '.join(missing), code='PAYMENT_ASSET_INCOMPLETE',
                               extra={'missing': missing})
            assets_sql.update_details(cursor, asset_id, fields)
            item = assets_sql.read_asset(cursor, asset_id)
        return jsonify({'status': 'success', 'asset': item})

    @route('/assets/<int:asset_id>/move', methods=('POST',))
    def asset_move(ctx, asset_id):
        """Перемещение: новый ответственный, подразделение, город, место или статус.
        Прежние владелец и место остаются строкой истории (п. 12)."""
        _guard_manage(ctx)
        data = _payload()
        uploads = uploaded_files()
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            current = assets_sql.read_asset(cursor, asset_id, lock=True)
            if not current:
                raise ApiError('Имущество не найдено', status=404)
            errors = []
            merged = {key: data.get(key, current.get(key)) for key in
                      ('department_id', 'responsible_user_id', 'city', 'location', 'status')}
            fields = _place(cursor, merged, errors)
            if errors:
                raise ApiError('; '.join(errors), code='PAYMENT_ASSET_INVALID')
            gaps = [label for key, label in (('city', 'город'), ('department_id', 'подразделение'),
                                             ('responsible_user_id', 'ответственный сотрудник'),
                                             ('location', 'место эксплуатации'), ('status', 'статус'))
                    if not fields.get(key)]
            if gaps:
                raise ApiError('Укажите: %s' % ', '.join(gaps), code='PAYMENT_ASSET_INVALID')
            # Обе проверки — ДО загрузки файла: отказ не должен оставлять акт в хранилище.
            if all(fields.get(key) == current.get(key) for key in assets_sql.MOVE_KEYS):
                raise ApiError('Ничего не изменилось — укажите нового ответственного, место или статус',
                               code='PAYMENT_ASSET_UNCHANGED', status=409)
            # П. 11: имущество передаётся конкретному сотруднику — нужен акт приёма-передачи.
            if fields['responsible_user_id'] != current.get('responsible_user_id') and not uploads:
                raise ApiError('Имущество передаётся другому сотруднику — приложите акт приёма-передачи',
                               code='PAYMENT_ASSET_ACT_REQUIRED')
            attachment_id = None
            if uploads:
                if not current.get('request_id'):
                    raise ApiError('Акт можно приложить только к имуществу, заведённому из заявки')
                # Акт приёма-передачи лежит в заявке, из которой имущество появилось (п. 11).
                api.store_files(cursor, current['request_id'],
                                [(storage, 'handover_act', None) for storage, _kind, _offer in uploads[:1]],
                                actor, subtask_kind=workflow.KIND_ASSETS)
                cursor.execute(
                    "SELECT id FROM payment_attachments WHERE request_id = %s AND kind = 'handover_act' "
                    "ORDER BY id DESC LIMIT 1", (current['request_id'],))
                row = cursor.fetchone()
                attachment_id = row[0] if row else None
            moved = assets_sql.move(cursor, asset_id, fields=fields, actor=actor,
                                    comment=as_text(data.get('comment'), 'comment'), attachment_id=attachment_id)
            if not moved:
                raise ApiError('Имущество не найдено', status=404)
            item = assets_sql.read_asset(cursor, asset_id)
            moves = assets_sql.list_moves(cursor, asset_id)
        return jsonify({'status': 'success', 'asset': item, 'moves': moves})

    @route('/my-assets', open_to_all=True)
    def my_assets(ctx):
        """Имущество, числящееся за смотрящим, — для блока в его профиле (п. 12)."""
        with db._get_cursor() as cursor:
            cursor.execute('SAVEPOINT payments_my_assets')
            try:
                rows = assets_sql.of_user(cursor, ctx['user_id'])
                cursor.execute('RELEASE SAVEPOINT payments_my_assets')
            except Exception:  # noqa: BLE001 — схема раздела не развернулась: имущества просто нет
                cursor.execute('ROLLBACK TO SAVEPOINT payments_my_assets')
                rows = []
        keys = ('id', 'name', 'category_name', 'serial_number', 'inventory_number', 'received_on', 'city',
                'department_name', 'location', 'status', 'status_label')
        return jsonify({'items': [{key: row.get(key) for key in keys} for row in rows]})

    return api
