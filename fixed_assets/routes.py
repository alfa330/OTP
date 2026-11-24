"""HTTP-ручки раздела «Основные средства» (Blueprint /api/fixed_assets).

Blueprint собирается фабрикой и получает зависимости аргументами — обратный импорт
bot_schedule2 был бы циклом (так же устроены mail/, parcels/, payments/).

Здесь только разбор запроса, порядок шагов и коды ответов. Что можно — access.py, какие
данные годятся — rules.py, SQL — queries.py.

ДВА ПРАВИЛА ПОРЯДКА.
* `db._get_cursor()` фиксирует всё, что записано до `return …, 4xx` (откат — только по
  исключению), поэтому отказ здесь — всегда исключение FaError, а не ответ с кодом.
* Файл уходит в бакет ДО транзакции записи, а не внутри неё: сеть не держит открытым
  соединение с базой. Запись не удалась — загруженное стирается (files.drop_blobs).

Ручки:
    GET    /context                    права, вкладки, справочники, счётчик «Ждут меня»
    GET    /people                     сотрудники для выбора
    GET    /assets                     реестр / «Мои ОС» / выбор ОС в запрос
    GET    /assets/next-number         подсказка инвентарного номера
    GET    /assets/<id>                карточка ОС: описание, фото, акты, история
    POST   /assets                     приём в учёт (бухгалтерия): карточка + запрос приёма
    PATCH  /assets/<id>                исправление карточки, пока открыт приём
    GET    /requests                   запросы: «Ждут меня», мои, все видимые
    GET    /requests/<id>              карточка запроса
    POST   /requests                   новый запрос (черновик или сразу отправка)
    POST   /requests/<id>/save         правка черновика
    DELETE /requests/<id>              удалить черновик
    POST   /requests/<id>/submit       отправить
    POST   /requests/<id>/check        «Проверено» (списание)
    POST   /requests/<id>/scan         скан подписанного акта (PDF); пока ждут оригинал — замена скана
    POST   /requests/<id>/original     «Оригинал получен» — закрывает запрос и меняет ОС
    POST   /requests/<id>/reject       отклонить с причиной
    GET    /requests/<id>/act          печатная форма акта (Word)
    GET    /files/<id>                 скан акта
    GET    /history                    история: общая — кто видит всё, остальным — где они участвуют
    GET    /dismissals                 вкладка HR «Увольнение»
    GET    /dismissals/check           состояние одного сотрудника
    GET    /settings                   категории, «Участники», роли по отделам, реквизиты актов
    PUT    /settings/requisites        реквизиты организации и комиссия для печатных форм
    POST   /settings/categories        новая категория
    PUT    /settings/categories/<id>   правка категории
    POST   /settings/members           добавить участника
    DELETE /settings/members           убрать участника
    GET    /export                     реестр в xlsx
"""

import json
import logging
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import access, acts, files, queries, report, rules, schema

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
PAGE_LIMIT = 100
MAX_UPLOADS = 60


def build_fixed_assets_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                                 excel_text_warning=None, gcs=None):
    gcs = gcs or {}
    bp = Blueprint('fixed_assets', __name__, url_prefix='/api/fixed_assets')
    schema.ensure(db)

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
                    if not access.can_open(ctx):
                        return jsonify({'error': 'Раздел «Основные средства» вам не открыт',
                                        'code': 'FA_CLOSED'}), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except rules.FaError as exc:
                    body = {'error': exc.message, 'code': exc.code}
                    if exc.fields:
                        body['fields'] = exc.fields
                    body.update(queries.plain(exc.extra or {}))
                    return jsonify(body), exc.status
                except Exception:  # noqa: BLE001
                    logging.exception('fixed_assets: %s упал', handler.__name__)
                    return jsonify({'error': 'Внутренняя ошибка раздела «Основные средства»'}), 500
            return wrapper
        return decorator

    # ─── Разбор запроса ──────────────────────────────────────────────────────

    def _ok(payload, status=200):
        return jsonify(queries.plain(payload)), status

    def _payload():
        """JSON или multipart (шаг с файлами): поля формы лежат JSON-строкой в `payload`."""
        if request.mimetype == 'multipart/form-data':
            raw = request.form.get('payload') or '{}'
            try:
                data = json.loads(raw)
            except ValueError as exc:
                raise rules.FaError('Неверные данные формы') from exc
            return data if isinstance(data, dict) else {}
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def _uploads():
        if request.mimetype != 'multipart/form-data':
            return []
        uploads = [item for item in request.files.getlist('files') if item and item.filename]
        if len(uploads) > MAX_UPLOADS:
            raise rules.FaError('Слишком много файлов за раз', code='FA_TOO_MANY_FILES')
        return uploads

    def _actor(ctx):
        return {'id': ctx['user_id'], 'name': ctx.get('name')}

    def _int_arg(name):
        value = request.args.get(name)
        if value in (None, ''):
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise rules.FaError('Неверное значение: %s' % name) from exc

    def _page():
        limit = min(max(_int_arg('limit') or 50, 1), PAGE_LIMIT)
        offset = max(_int_arg('offset') or 0, 0)
        return limit, offset

    def _forbid(message, code='FA_FORBIDDEN'):
        raise rules.FaError(message, code=code, status=403)

    def _download(content, mimetype, name, *, attachment=True):
        """Файл в ответ. Имя файла экран читает из Content-Disposition, а сайт и API живут на
        разных адресах: без Expose-Headers браузер этот заголовок скрипту не отдаёт, и акт
        сохранялся под запасным именем («Акт № 28» вместо «Акт списания № 28»)."""
        response = send_file(BytesIO(content), mimetype=mimetype, as_attachment=attachment, download_name=name)
        response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response

    def _not_found(message='Запись не найдена'):
        raise rules.FaError(message, code='FA_NOT_FOUND', status=404)

    # ─── Что уходит экрану ───────────────────────────────────────────────────

    _ASSET_KEYS = ('id', 'inventory_number', 'name', 'category_id', 'category_name', 'check_by', 'serial_number', 'barcode',
                   'acquired_on', 'status', 'status_label', 'mol_user_id', 'mol_name', 'department_id',
                   'department_name', 'location', 'on_stock', 'open_request', 'intake_open', 'intake_request_id',
                   'written_off_at')

    def _asset_views(ctx, assets):
        """Карточки для списка: по уровню доступа, с подписанной обложкой."""
        covers = [asset['cover'] for asset in assets if asset.get('cover')]
        signed = {view['id']: view for view in files.photo_views(gcs, covers)} if covers else {}
        views = []
        for asset in assets:
            level = access.asset_level(ctx, asset)
            view = {key: asset.get(key) for key in _ASSET_KEYS}
            view['level'] = level
            if level == access.LEVEL_FULL:
                view['cost'] = asset.get('cost')
            if level in (access.LEVEL_FULL, access.LEVEL_SCOPED, access.LEVEL_OWN):
                view['note'] = asset.get('note')
            cover = signed.get(asset['cover']['id']) if asset.get('cover') else None
            view['cover_url'] = cover['thumb_url'] if cover else None
            views.append(view)
        return views

    def _request_brief(ctx, item):
        keys = ('id', 'kind', 'kind_label', 'status', 'status_label', 'review_by', 'created_by', 'created_by_name',
                'from_user_id', 'from_name', 'from_department_name', 'to_user_id', 'to_name', 'to_department_name',
                'to_location', 'to_stock', 'need_title', 'need_for_name', 'items_count', 'items_title',
                'submitted_at', 'created_at', 'closed_at', 'rejected_at', 'signed_on', 'act_date')
        view = {key: item.get(key) for key in keys}
        view['waiting'] = access.is_waiting(ctx, item)
        return view

    def _act_block(request_row, scan):
        """Блок акта из ТЗ (раздел 5): скан, дата подписания, кто и когда передал оригинал,
        «Оригинал получен», бухгалтер, принявший оригинал."""
        if request_row['kind'] not in rules.ACT_TITLES:
            return None
        return {
            'title': rules.ACT_TITLES[request_row['kind']],
            'number': request_row['id'],
            'act_date': request_row.get('act_date'),
            'scan': ({'id': scan['id'], 'file_name': scan.get('file_name'), 'file_size': scan.get('file_size'),
                      'uploaded_by_name': scan.get('uploaded_by_name'), 'created_at': scan.get('created_at')}
                     if scan else None),
            'signed_on': request_row.get('signed_on'),
            'original_handed_by_name': request_row.get('original_handed_by_name'),
            'original_handed_on': request_row.get('original_handed_on'),
            'original_received': bool(request_row.get('original_received_at')),
            'original_received_by_name': request_row.get('original_received_by_name'),
            'original_received_at': request_row.get('original_received_at'),
        }

    def _request_card(cursor, ctx, request_row):
        items = queries.read_items(cursor, request_row['id'])
        stored = queries.read_files(cursor, request_row['id'])
        photos = files.photo_views(gcs, [row for row in stored if row['kind'] != rules.ACT_SCAN])
        scan = next((row for row in stored if row['id'] == request_row.get('scan_file_id')), None)
        by_asset = {}
        for photo in photos:
            by_asset.setdefault(photo['asset_id'], []).append(photo)
        heads = queries.department_heads(cursor, [request_row.get('from_department_id'),
                                                  request_row.get('to_department_id')])
        view = dict(request_row)
        view['track'] = rules.track(request_row)
        view['act'] = _act_block(request_row, scan)
        view['signers'] = [{'role': role, 'name': name} for role, name in rules.signers(request_row, heads)]
        # У отправленного запроса «что было» — снимок на день отправки; у черновика — карточка.
        sent = request_row['status'] != rules.STATUS_DRAFT

        def before(item, key):
            return item.get('before_' + key) if sent else item['asset'].get(key)

        card = {
            'request': view,
            'items': [{
                'asset_id': item['asset_id'], 'name': item['name'], 'inventory_number': item['inventory_number'],
                'serial_number': item['serial_number'], 'category_name': item['category_name'],
                'check_by': item['check_by'],
                'before_mol_name': before(item, 'mol_name'),
                'before_department_name': before(item, 'department_name'),
                'before_location': before(item, 'location'),
                'before_on_stock': bool(before(item, 'on_stock')),
                'asset_status': item['asset'].get('status'),
                'photos': by_asset.get(item['asset_id'], []),
            } for item in items],
            'events': queries.request_events(cursor, request_row['id']),
            'actions': access.allowed_actions(ctx, request_row),
            'waiting': access.is_waiting(ctx, request_row),
        }
        if request_row['status'] == rules.STATUS_DRAFT:
            gaps = (rules.photo_gaps(items, [(row['asset_id'], row['kind']) for row in stored if row['asset_id']])
                    if request_row['kind'] in rules.ASSET_KINDS else [])
            card['missing'] = rules.missing_for_submit(request_row, items)
            card['photo_gaps'] = gaps
        return card

    def _load_request(cursor, ctx, request_id, *, for_update=False):
        request_row = queries.read_request(cursor, request_id, for_update=for_update)
        if not request_row or not access.can_view_request(ctx, request_row):
            _not_found('Запрос не найден')
        return request_row

    def _holder(cursor, user_id, department_id, location, on_stock, *, label='МОЛ'):
        """За кем и где будет числиться ОС: сотрудник (работающий) и его отдел со снимками имён."""
        person = queries.user_brief(cursor, user_id)
        if not person or person['fired']:
            raise rules.FaError('%s: сотрудник не найден или уволен' % label, code='FA_USER_INVALID',
                                fields={'to_user_id': 'Сотрудник не найден или уволен'})
        department = queries.department_brief(cursor, department_id) if department_id else None
        if department_id and not department:
            raise rules.FaError('Отдел не найден', code='FA_DEPARTMENT_INVALID')
        return {
            'user_id': person['id'], 'name': person['name'],
            'department_id': (department or {}).get('id') or person.get('department_id'),
            'department_name': (department or {}).get('name') or person.get('department_name'),
            'location': location, 'on_stock': bool(on_stock),
        }

    def _prepare_photos(uploads, kinds, *, asset_ids=None):
        """Фото из формы → готовые к загрузке кадры: [(asset_id, вид, кадр)].

        `kinds` — список {asset_id, kind} по порядку файлов. Проверка и перевод в WebP идут
        до любой записи: битый файл не оставит запрос наполовину сохранённым."""
        if len(kinds) != len(uploads):
            raise rules.FaError('Фото и их подписи не совпали — обновите страницу и попробуйте ещё раз')
        prepared = []
        for upload, meta in zip(uploads, kinds):
            meta = meta if isinstance(meta, dict) else {'kind': meta}
            kind = meta.get('kind')
            if kind not in rules.PHOTO_KINDS:
                raise rules.FaError('Неизвестный вид фото')
            asset_id = rules.clean_int(meta.get('asset_id'), label='ОС') if asset_ids is not None else None
            if asset_ids is not None and asset_id not in asset_ids:
                raise rules.FaError('Фото относится к ОС, которого нет в запросе')
            data = upload.read()
            prepared.append((asset_id, kind, files.prepare_photo(data, filename=upload.filename,
                                                                 content_type=upload.mimetype)))
        return prepared

    def _upload_photos(prepared):
        stored = []
        try:
            for asset_id, kind, photo in prepared:
                stored.append((asset_id, kind, files.upload_photo(gcs, photo)))
        except Exception:
            _drop_stored(stored)
            raise
        return stored

    def _drop_stored(stored):
        refs = []
        for _asset_id, _kind, row in stored:
            refs.append((row['bucket'], row['blob_path']))
            if row.get('thumb_blob_path'):
                refs.append((row['bucket'], row['thumb_blob_path']))
        files.drop_blobs(gcs, refs)

    def _file_note(file_id, row, kind):
        return {'id': file_id, 'name': row.get('file_name'), 'kind': kind}

    # ─── Контекст ────────────────────────────────────────────────────────────

    @route('/context')
    def fa_context(ctx):
        with db._get_cursor() as cursor:
            payload = {
                'me': {'id': ctx['user_id'], 'name': ctx['name'], 'department_id': ctx.get('department_id'),
                       'department_name': ctx.get('department_name')},
                'capabilities': access.capabilities(ctx),
                'categories': queries.list_categories(cursor),
                'departments': [{'id': row['id'], 'name': row['name']} for row in queries.list_departments(cursor)],
                'waiting': len(queries.waiting_requests(cursor, ctx)),
                'storage_ready': files.storage_ready(gcs),
                'limits': {'photos_per_asset': rules.MAX_PHOTOS_PER_ASSET, 'assets_per_request': rules.MAX_ASSETS_PER_REQUEST,
                           'scan_bytes': rules.MAX_SCAN_BYTES, 'photo_bytes': files.MAX_PHOTO_BYTES},
            }
        return _ok(payload)

    @route('/people')
    def fa_people(ctx):
        with db._get_cursor() as cursor:
            return _ok({'items': queries.list_people(cursor)})

    # ─── Реестр ──────────────────────────────────────────────────────────────

    @route('/assets')
    def fa_assets(ctx):
        limit, offset = _page()
        scope = request.args.get('scope') or 'all'
        pick = request.args.get('pick') or None
        filters = {
            'query': (request.args.get('q') or '').strip()[:200] or None,
            'category_id': _int_arg('category_id'),
            'department_id': _int_arg('department_id'),
            'mol_user_id': _int_arg('mol_user_id'),
        }
        slice_code = request.args.get('slice') or None
        if slice_code and slice_code not in queries.ASSET_SLICES:
            raise rules.FaError('Неизвестный срез реестра')
        with db._get_cursor() as cursor:
            if pick:
                # Выбор ОС в запрос: только свободные и только те, что человек вправе включить.
                if pick not in rules.LOCKING_KINDS or not access.can_create(ctx, pick):
                    _forbid('Запрос такого вида вам не доступен')
                total, assets = queries.list_assets(cursor, ctx, limit=PAGE_LIMIT, offset=0, free=True, **filters)
                assets = [asset for asset in assets if access.can_pick_asset(ctx, asset, pick)]
                return _ok({'items': _asset_views(ctx, assets), 'total': len(assets)})
            if scope == 'mine':
                total, assets = queries.list_assets(cursor, ctx, limit=PAGE_LIMIT, offset=0, mine=True, **filters)
                return _ok({
                    'items': _asset_views(ctx, assets), 'total': total,
                    'incoming': queries.incoming_items(cursor, ctx['user_id']),
                })
            if not (access.sees_registry(ctx) or access.is_head(ctx)):
                _forbid('Реестр вам не открыт — ваши ОС во вкладке «Мои ОС»')
            total, assets = queries.list_assets(cursor, ctx, limit=limit, offset=offset, slice_code=slice_code,
                                                **filters)
            return _ok({
                'items': _asset_views(ctx, assets), 'total': total, 'limit': limit, 'offset': offset,
                'counters': queries.asset_counters(cursor, ctx, **filters),
                'facets': queries.asset_facets(cursor, ctx),
            })

    @route('/assets/next-number')
    def fa_next_number(ctx):
        if not access.can_intake(ctx):
            _forbid('Инвентарный номер присваивает бухгалтерия')
        with db._get_cursor() as cursor:
            last = queries.last_inventory_number(cursor)
        return _ok({'last': last, 'suggestion': rules.next_inventory_number(last)})

    @route('/assets/<int:asset_id>')
    def fa_asset(asset_id, ctx):
        with db._get_cursor() as cursor:
            asset = queries.read_asset(cursor, asset_id)
            level = access.asset_level(ctx, asset)
            if not level:
                _not_found('ОС не найдено')
            view = _asset_views(ctx, [asset])[0]
            card = {'asset': view, 'level': level, 'can_correct': access.can_correct(ctx, asset),
                    'can_set_barcode': access.can_set_barcode(ctx, asset),
                    'can_request': [kind for kind in (rules.KIND_TRANSFER, rules.KIND_WRITEOFF)
                                    if access.can_create(ctx, kind) and access.can_pick_asset(ctx, asset, kind)
                                    and not asset.get('open_request_id')]}
            if level == access.LEVEL_BRIEF:
                # IT: «наличие на складе ОС и МОЛ» — без истории, актов и фото запросов.
                return _ok(card)
            card['photos'] = files.photo_views(gcs, queries.asset_photos(cursor, asset_id))
            _total, operations = queries.list_requests(cursor, ctx, limit=PAGE_LIMIT, offset=0, asset_id=asset_id)
            acts_view = []
            for item in operations:
                if item['status'] == rules.STATUS_DRAFT or item['kind'] not in rules.ACT_TITLES:
                    continue
                stored = queries.read_files(cursor, item['id'])
                scan = next((row for row in stored if row['id'] == item.get('scan_file_id')), None)
                acts_view.append({'request': _request_brief(ctx, item), 'act': _act_block(item, scan),
                                  'reject_reason': item.get('reject_reason')})
            card['operations'] = acts_view
            # МОЛ (своё ОС) видит историю, в которой участвует сам; руководитель отдела и те,
            # кто видит всё, — историю ОС целиком.
            card['events'] = queries.asset_events(
                cursor, asset_id, participant_id=ctx['user_id'] if level == access.LEVEL_OWN else None)
        return _ok(card)

    def _barcode_free(cursor, barcode, *, exclude_id=None):
        """Один штрихкод — одна карточка: иначе скан открыл бы не то ОС."""
        if not barcode:
            return
        taken = queries.barcode_taken(cursor, barcode, exclude_id=exclude_id)
        if taken:
            raise rules.FaError('Штрихкод %s уже привязан: «%s», инв. № %s' % (barcode, taken['name'],
                                                                              taken['inventory_number']),
                                code='FA_BARCODE_TAKEN', status=409, fields={'barcode': 'Этот штрихкод уже занят'})

    @route('/assets/lookup')
    def fa_asset_lookup(ctx):
        """ОС по скану наклейки: штрихкод, инвентарный или серийный номер. Только те, что человек и так
        видит: МОЛ — свои, руководитель — своего отдела, бухгалтерия и IT — все. hidden — сколько
        совпадений есть, но ему не открыто (экран честно скажет, что ОС есть, но не ваше)."""
        code = rules.scan_code(request.args.get('code'))
        if not code:
            raise rules.FaError('Нечего искать: отсканируйте штрихкод или введите номер', code='FA_CODE_EMPTY')
        with db._get_cursor() as cursor:
            found = queries.find_assets_by_code(cursor, code)
        visible = [asset for asset in found if access.asset_level(ctx, asset)]
        return _ok({'code': code, 'items': _asset_views(ctx, visible), 'hidden': len(found) - len(visible)})

    @route('/assets/<int:asset_id>/barcode', methods=('POST',))
    def fa_asset_barcode(asset_id, ctx):
        """Привязать (или снять) штрихкод наклейки. Бухгалтерия, в любой момент, пока ОС на учёте:
        ОС, принятые до наклеек, связывают с наклейками позже. Пишется в историю."""
        data = _payload()
        barcode = rules.clean_barcode(data.get('barcode'))
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            asset = queries.read_asset(cursor, asset_id, for_update=True)
            if not asset or not access.asset_level(ctx, asset):
                _not_found('ОС не найдено')
            if not access.can_set_barcode(ctx, asset):
                _forbid('Штрихкод привязывает бухгалтерия, пока ОС на учёте', code='FA_BARCODE_FORBIDDEN')
            if (barcode or None) == (asset.get('barcode') or None):
                raise rules.FaError('Штрихкод тот же', code='FA_UNCHANGED', status=409)
            _barcode_free(cursor, barcode, exclude_id=asset_id)
            try:
                queries.set_barcode(cursor, asset_id, barcode)
            except Exception as exc:  # noqa: BLE001
                if 'fa_assets_barcode_uq' in str(exc):
                    raise rules.FaError('Штрихкод %s уже привязан к другому ОС' % barcode,
                                        code='FA_BARCODE_TAKEN', status=409) from exc
                raise
            queries.add_event(cursor, 'barcode_set', actor, asset_id=asset_id,
                              payload={'from': asset.get('barcode'), 'to': barcode})
            view = _asset_views(ctx, [queries.read_asset(cursor, asset_id)])[0]
        return _ok({'asset': view})

    def _photo_kinds_ok(kinds):
        present = [meta.get('kind') if isinstance(meta, dict) else meta for meta in kinds]
        missing = [kind for kind in rules.REQUIRED_PHOTOS if kind not in present]
        if missing:
            raise rules.FaError(
                'Не хватает фото: %s. К ОС нужны два снимка: ОС целиком и инвентарный номер.'
                % ' и '.join(rules.PHOTO_LABELS[kind] for kind in missing),
                code='FA_PHOTOS_REQUIRED', extra={'missing_photos': missing})

    @route('/assets', methods=('POST',))
    def fa_intake(ctx):
        """Приём в учёт: карточка ОС с инвентарным номером, двумя фото и первым МОЛ. ОС сразу
        «На учёте»; рядом заводится запрос приёма — он ведёт акт до оригинала."""
        if not access.can_intake(ctx):
            _forbid('Карточку ОС заводит бухгалтерия', code='FA_INTAKE_FORBIDDEN')
        data = _payload()
        uploads = _uploads()
        kinds = data.get('photos') if isinstance(data.get('photos'), list) else []
        today = queries.today_almaty()
        fields = rules.clean_asset(data, today)
        holder_fields = rules.clean_holder(data)
        source_id = rules.clean_int(data.get('source_request_id'), label='Запрос')
        _photo_kinds_ok(kinds)

        # Проверки по базе — до загрузки фото: отказ не должен оставлять файлы в бакете.
        with db._get_cursor() as cursor:
            category = queries.read_category(cursor, fields['category_id'])
            if not category or not category['is_active']:
                raise rules.FaError('Категория не найдена', fields={'category_id': 'Выберите категорию'})
            taken = queries.inventory_taken(cursor, fields['inventory_number'])
            if taken:
                raise rules.FaError('Инвентарный номер %s уже занят: «%s»' % (fields['inventory_number'], taken['name']),
                                    code='FA_INVENTORY_TAKEN', status=409,
                                    fields={'inventory_number': 'Такой номер уже есть'})
            _barcode_free(cursor, fields.get('barcode'))
            holder = _holder(cursor, holder_fields['user_id'], holder_fields['department_id'],
                             holder_fields['location'], holder_fields['on_stock'])
            if source_id:
                source = queries.read_request(cursor, source_id)
                if (not source or source['kind'] != rules.KIND_NEED or source['status'] != rules.STATUS_REVIEW
                        or 'accept' not in access.allowed_actions(ctx, source)):
                    raise rules.FaError('Запрос «Новое ОС» уже закрыт или недоступен', code='FA_SOURCE_INVALID',
                                        status=409)

        prepared = _prepare_photos(uploads, kinds)
        stored = _upload_photos(prepared)
        actor = _actor(ctx)
        try:
            with db._get_cursor() as cursor:
                if queries.inventory_taken(cursor, fields['inventory_number']):
                    raise rules.FaError('Инвентарный номер %s уже занят' % fields['inventory_number'],
                                        code='FA_INVENTORY_TAKEN', status=409)
                try:
                    asset_id = queries.create_asset(cursor, fields, holder, actor)
                except Exception as exc:  # noqa: BLE001
                    if 'fa_assets_inventory_uq' in str(exc):
                        raise rules.FaError('Инвентарный номер %s уже занят' % fields['inventory_number'],
                                            code='FA_INVENTORY_TAKEN', status=409) from exc
                    if 'fa_assets_barcode_uq' in str(exc):
                        raise rules.FaError('Штрихкод %s уже привязан к другому ОС' % fields.get('barcode'),
                                            code='FA_BARCODE_TAKEN', status=409) from exc
                    raise
                request_id = queries.create_request(
                    cursor, rules.KIND_INTAKE,
                    {'to_user_id': holder['user_id'], 'to_name': holder['name'],
                     'to_department_id': holder['department_id'], 'to_department_name': holder['department_name'],
                     'to_location': holder['location'], 'to_stock': holder['on_stock'],
                     'comment': fields.get('note')},
                    actor, status=rules.STATUS_SIGNING,
                    extra={'act_date': today, 'submitted_at': queries.now_almaty(), 'source_request_id': source_id})
                queries.set_items(cursor, request_id, [asset_id])
                queries.snapshot_items(cursor, request_id)
                cursor.execute("UPDATE fa_assets SET intake_request_id = %s WHERE id = %s", (request_id, asset_id))
                notes = []
                for _asset, kind, row in stored:
                    notes.append(_file_note(queries.add_file(cursor, request_id, asset_id, kind, row, actor), row, kind))
                queries.add_event(cursor, 'registered', actor, asset_id=asset_id, request_id=request_id, payload={
                    'inventory_number': fields['inventory_number'], 'name': fields['name'],
                    'to': {'mol_user_id': holder['user_id'], 'mol_name': holder['name'],
                           'department_name': holder['department_name'], 'location': holder['location'],
                           'on_stock': holder['on_stock']},
                    'files': notes,
                })
                if source_id:
                    queries.mark_closed(cursor, source_id)
                    queries.add_event(cursor, 'need_accepted', actor, request_id=source_id, payload={
                        'asset_id': asset_id, 'inventory_number': fields['inventory_number'], 'name': fields['name'],
                        'intake_request_id': request_id})
                    queries.add_event(cursor, 'closed', actor, request_id=source_id, payload={})
                asset = queries.read_asset(cursor, asset_id)
                view = _asset_views(ctx, [asset])[0]
        except Exception:
            _drop_stored(stored)
            raise
        return _ok({'asset': view, 'request_id': request_id}, 201)

    @route('/assets/<int:asset_id>', methods=('PATCH',))
    def fa_correct(asset_id, ctx):
        """Исправление описания карточки — только бухгалтерия и только пока открыт запрос
        приёма (акт ещё не закрыт). После закрытия карточку меняют запросы: «нет обхода»."""
        data = _payload()
        reason = rules.clean_text(data.get('reason'), 500, label='Причина исправления', required=True)
        today = queries.today_almaty()
        fields = rules.clean_asset(data, today, partial=True)
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            asset = queries.read_asset(cursor, asset_id, for_update=True)
            if not asset or not access.asset_level(ctx, asset):
                _not_found('ОС не найдено')
            if not access.can_correct(ctx, asset):
                raise rules.FaError('Карточку можно исправить, пока не закрыт акт приёма. Дальше ОС меняют '
                                    'только запросы', code='FA_CORRECT_CLOSED', status=409)
            changes = []
            for name in rules.ASSET_CORRECTABLE:
                if name in fields and fields[name] != asset.get(name):
                    changes.append({'field': name, 'label': rules.ASSET_FIELD_LABELS[name],
                                    'from': asset.get(name), 'to': fields[name]})
            if not changes:
                raise rules.FaError('Ничего не изменилось', code='FA_UNCHANGED', status=409)
            if any(change['field'] == 'category_id' for change in changes):
                category = queries.read_category(cursor, fields['category_id'])
                if not category or not category['is_active']:
                    raise rules.FaError('Категория не найдена')
                for change in changes:
                    if change['field'] == 'category_id':
                        change['from'], change['to'] = asset.get('category_name'), category['name']
            if any(change['field'] == 'inventory_number' for change in changes):
                taken = queries.inventory_taken(cursor, fields['inventory_number'], exclude_id=asset_id)
                if taken:
                    raise rules.FaError('Инвентарный номер %s уже занят: «%s»'
                                        % (fields['inventory_number'], taken['name']),
                                        code='FA_INVENTORY_TAKEN', status=409)
            queries.correct_asset(cursor, asset_id, {change['field']: fields[change['field']] for change in changes})
            # Акт приёма печатается по снимку запроса — он обязан показать исправленное.
            if asset.get('intake_request_id'):
                queries.snapshot_items(cursor, asset['intake_request_id'])
            queries.add_event(cursor, 'corrected', actor, asset_id=asset_id,
                              request_id=asset.get('intake_request_id'),
                              payload={'changes': changes, 'reason': reason})
            view = _asset_views(ctx, [queries.read_asset(cursor, asset_id)])[0]
        return _ok({'asset': view})

    # ─── Запросы ─────────────────────────────────────────────────────────────

    # Плитки над списком запросов: «В работе» — три статуса разом.
    _REQUEST_TILES = {
        'open': rules.OPEN_STATUSES,
        'closed': (rules.STATUS_CLOSED,),
        'rejected': (rules.STATUS_REJECTED,),
    }

    @route('/requests')
    def fa_requests(ctx):
        limit, offset = _page()
        tile = request.args.get('tile') or None
        kind = request.args.get('kind') or None
        if tile and tile != 'waiting' and tile not in _REQUEST_TILES:
            raise rules.FaError('Неизвестный отбор запросов')
        if kind and kind not in rules.KINDS:
            raise rules.FaError('Неизвестный вид запроса')
        query = (request.args.get('q') or '').strip()[:200] or None
        with db._get_cursor() as cursor:
            waiting = queries.waiting_requests(cursor, ctx)
            filters = {'kind': kind, 'query': query}
            by_status = queries.request_counters(cursor, ctx, **filters)
            counters = {'all': by_status['all'], 'waiting': len(waiting)}
            for code, statuses in _REQUEST_TILES.items():
                counters[code] = sum(by_status.get(status, 0) for status in statuses)
            if tile == 'waiting':
                # «Ждут меня» — их десятки, не тысячи: список целиком, без страниц.
                items = [item for item in waiting if not kind or item['kind'] == kind]
                return _ok({'items': [_request_brief(ctx, item) for item in items], 'total': len(items),
                            'limit': len(items), 'offset': 0, 'counters': counters})
            total, items = queries.list_requests(cursor, ctx, limit=limit, offset=offset,
                                                 status=_REQUEST_TILES.get(tile), **filters)
            return _ok({'items': [_request_brief(ctx, item) for item in items], 'total': total, 'limit': limit,
                        'offset': offset, 'counters': counters})

    # Доска запросов: на колонку по столько карточек, остальное — «Посмотреть ещё» (?status=).
    BOARD_PAGE = 30

    @route('/requests/board')
    def fa_requests_board(ctx):
        """Запросы канбан-доской: колонка на каждый статус, по тем же правам и отбору, что и
        список. ?waiting=1 — только ждущие человека; ?status=… — следующая страница одной колонки."""
        kind = request.args.get('kind') or None
        if kind and kind not in rules.KINDS:
            raise rules.FaError('Неизвестный вид запроса')
        only = request.args.get('status') or None
        if only and only not in rules.STATUSES:
            raise rules.FaError('Неизвестная колонка доски')
        waiting_only = request.args.get('waiting') in ('1', 'true')
        query = (request.args.get('q') or '').strip()[:200] or None
        limit = min(max(_int_arg('limit') or BOARD_PAGE, 1), PAGE_LIMIT)
        offset = max(_int_arg('offset') or 0, 0) if only else 0
        with db._get_cursor() as cursor:
            waiting = queries.waiting_requests(cursor, ctx)
            filters = {'kind': kind, 'query': query,
                       'ids': [item['id'] for item in waiting] if waiting_only else None}
            columns = []
            for status in ([only] if only else rules.STATUSES):
                total, items = queries.board_requests(cursor, ctx, status=status, limit=limit, offset=offset,
                                                      **filters)
                columns.append({'status': status, 'total': total, 'items': items})
        covers = [item['cover'] for column in columns for item in column['items'] if item.get('cover')]
        signed = {view['id']: view for view in files.photo_views(gcs, covers)} if covers else {}
        for column in columns:
            cards = []
            for item in column['items']:
                card = _request_brief(ctx, item)
                card['since'] = item.get('since')
                # Экран разрешает перетащить карточку только туда, куда у человека есть шаг.
                card['actions'] = access.allowed_actions(ctx, item)
                cover = signed.get(item['cover']['id']) if item.get('cover') else None
                card['cover_url'] = cover['thumb_url'] if cover else None
                cards.append(card)
            column['items'] = cards
        return _ok({'columns': columns, 'counters': {'waiting': len(waiting)}, 'limit': limit, 'offset': offset})

    @route('/requests/<int:request_id>')
    def fa_request(request_id, ctx):
        with db._get_cursor() as cursor:
            return _ok(_request_card(cursor, ctx, _load_request(cursor, ctx, request_id)))

    def _resolve_request(cursor, ctx, kind, fields, *, current=None):
        """Проверяет поля черновика по базе и собирает колонки запроса и состав ОС."""
        columns = {'comment': fields.get('comment')}
        asset_ids = list(fields.get('asset_ids') or [])
        if kind == rules.KIND_DISMISSAL:
            leaver = queries.user_brief(cursor, fields['from_user_id'])
            if not leaver:
                raise rules.FaError('Сотрудник не найден', fields={'from_user_id': 'Сотрудник не найден'})
            owned = [asset for asset in queries.user_assets(cursor, leaver['id'])
                     if not asset.get('open_request_id')
                     or (current and asset['open_request_id'] == current['id'])]
            # В передачу при увольнении входят ВСЕ свободные ОС сотрудника: иначе акт подпишут,
            # а часть техники останется за уволенным.
            asset_ids = [asset['id'] for asset in owned]
            if not asset_ids:
                raise rules.FaError('За сотрудником нет ОС, которые нужно передать', code='FA_NOTHING_TO_HAND',
                                    status=409)
            columns.update({'from_user_id': leaver['id'], 'from_name': leaver['name'],
                            'from_department_id': leaver.get('department_id'),
                            'from_department_name': leaver.get('department_name')})
        assets = queries.read_assets(cursor, asset_ids) if kind in rules.ASSET_KINDS else []
        if kind in rules.ASSET_KINDS:
            if len(assets) != len(asset_ids):
                raise rules.FaError('Часть выбранных ОС не найдена — обновите страницу')
            for asset in assets:
                if not access.can_pick_asset(ctx, asset, kind):
                    raise rules.FaError('ОС «%s» нельзя включить в этот запрос' % asset['name'],
                                        code='FA_ASSET_FORBIDDEN', status=403)
            if assets and kind != rules.KIND_DISMISSAL:
                first = assets[0]
                columns.update({'from_user_id': first.get('mol_user_id'), 'from_name': first.get('mol_name'),
                                'from_department_id': first.get('department_id'),
                                'from_department_name': first.get('department_name')})
        if kind in rules.MOVING_KINDS:
            if fields.get('to_user_id'):
                holder = _holder(cursor, fields['to_user_id'], fields.get('to_department_id'),
                                 fields.get('to_location'), fields.get('to_stock'), label='Получатель')
                columns.update({'to_user_id': holder['user_id'], 'to_name': holder['name'],
                                'to_department_id': holder['department_id'],
                                'to_department_name': holder['department_name'],
                                'to_location': holder['location'], 'to_stock': holder['on_stock']})
            else:
                columns.update({'to_user_id': None, 'to_name': None, 'to_department_id': None,
                                'to_department_name': None, 'to_location': fields.get('to_location'),
                                'to_stock': bool(fields.get('to_stock'))})
        if kind == rules.KIND_NEED:
            person = queries.user_brief(cursor, fields.get('need_for_user_id')) if fields.get('need_for_user_id') else None
            if fields.get('need_for_user_id') and (not person or person['fired']):
                raise rules.FaError('Сотрудник не найден или уволен',
                                    fields={'need_for_user_id': 'Сотрудник не найден или уволен'})
            if fields.get('need_category_id') and not queries.read_category(cursor, fields['need_category_id']):
                raise rules.FaError('Категория не найдена')
            columns.update({'need_title': fields.get('need_title'), 'need_category_id': fields.get('need_category_id'),
                            'need_for_user_id': (person or {}).get('id'), 'need_for_name': (person or {}).get('name')})
        return columns, asset_ids

    def _store_photos(cursor, request_id, stored, actor):
        """Кладёт загруженные фото в черновик. Обязательный снимок («ОС целиком», «номер») у ОС
        один: новый заменяет прежний. Возвращает блобы заменённых — для уборки."""
        refs = []
        for asset_id, kind, row in stored:
            if kind in rules.REQUIRED_PHOTOS:
                cursor.execute("SELECT id FROM fa_files WHERE request_id = %s AND asset_id = %s AND kind = %s",
                               (request_id, asset_id, kind))
                refs += queries.drop_files(cursor, request_id, [found[0] for found in cursor.fetchall()])
            queries.add_file(cursor, request_id, asset_id, kind, row, actor)
        cursor.execute("SELECT asset_id, COUNT(*) FROM fa_files WHERE request_id = %s AND kind <> 'act_scan' "
                       "GROUP BY asset_id HAVING COUNT(*) > %s", (request_id, rules.MAX_PHOTOS_PER_ASSET))
        if cursor.fetchone():
            raise rules.FaError('К одному ОС — не больше %d фото' % rules.MAX_PHOTOS_PER_ASSET,
                                code='FA_TOO_MANY_PHOTOS')
        return refs

    def _submit(cursor, ctx, request_id):
        """Отправка запроса: полнота, фото, занятость ОС — и первый шаг пути."""
        request_row = queries.read_request(cursor, request_id, for_update=True)
        if not request_row or 'submit' not in access.allowed_actions(ctx, request_row):
            raise rules.FaError('Запрос уже отправлен или недоступен', code='FA_NOT_DRAFT', status=409)
        kind = request_row['kind']
        items = queries.read_items(cursor, request_id)
        missing = rules.missing_for_submit(request_row, items)
        if missing:
            raise rules.FaError(next(iter(missing.values())), code='FA_INCOMPLETE', fields=missing)
        today = queries.today_almaty()
        actor = _actor(ctx)
        holder = None
        review_by = review_department_id = None
        stored = queries.read_files(cursor, request_id)

        if kind in rules.ASSET_KINDS:
            assets = {asset['id']: asset for asset in
                      queries.read_assets(cursor, [item['asset_id'] for item in items], for_update=True)}
            holders = set()
            for item in items:
                asset = assets.get(item['asset_id'])
                if not asset or asset['status'] != rules.ASSET_ACTIVE:
                    raise rules.FaError('ОС «%s» уже списано' % item['name'], code='FA_ASSET_GONE', status=409)
                if asset.get('open_request_id') and asset['open_request_id'] != request_id:
                    raise rules.FaError('ОС «%s» уже в запросе № %s — дождитесь его закрытия'
                                        % (asset['name'], asset['open_request_id']), code='FA_ASSET_BUSY', status=409)
                if not access.can_pick_asset(ctx, asset, kind):
                    raise rules.FaError('ОС «%s» нельзя включить в этот запрос' % asset['name'],
                                        code='FA_ASSET_FORBIDDEN', status=403)
                holders.add(asset.get('mol_user_id'))
            if len(holders) > 1:
                raise rules.FaError('В одном запросе — ОС одного МОЛ: выбранные ОС числятся за разными людьми',
                                    code='FA_MIXED_HOLDERS', fields={'asset_ids': 'ОС разных МОЛ — разными запросами'})
            first = assets[items[0]['asset_id']]
            holder = {'user_id': first.get('mol_user_id'), 'name': first.get('mol_name'),
                      'department_id': first.get('department_id'), 'department_name': first.get('department_name')}
            # ТЗ, «Фото при любом запросе»: без обоих фото запрос не уходит, причина названа.
            gaps = rules.photo_gaps(items, [(row['asset_id'], row['kind']) for row in stored if row['asset_id']])
            if gaps:
                raise rules.FaError(rules.photo_gap_message(gaps), code='FA_PHOTOS_REQUIRED',
                                    extra={'photo_gaps': gaps})

        if kind in rules.MOVING_KINDS:
            receiver = queries.user_brief(cursor, request_row['to_user_id'])
            if not receiver or receiver['fired']:
                raise rules.FaError('Получатель не найден или уволен', code='FA_USER_INVALID',
                                    fields={'to_user_id': 'Выберите работающего сотрудника'})
            if kind == rules.KIND_DISMISSAL and request_row['to_user_id'] == request_row.get('from_user_id'):
                raise rules.FaError('ОС увольняющегося передают другому сотруднику',
                                    fields={'to_user_id': 'Выберите другого сотрудника'})
            for item in items:
                # Новое место не указано — оно остаётся прежним (так же закрытие запроса пишет карточку).
                target = {'user_id': request_row['to_user_id'], 'department_id': request_row.get('to_department_id'),
                          'location': request_row.get('to_location') or assets[item['asset_id']].get('location'),
                          'on_stock': request_row.get('to_stock')}
                if not rules.holder_changes(assets[item['asset_id']], target):
                    raise rules.FaError('ОС «%s» уже числится за этим сотрудником на этом месте — менять нечего'
                                        % item['name'], code='FA_UNCHANGED', status=409,
                                        fields={'to_user_id': 'Тот же МОЛ и то же место'})

        if kind == rules.KIND_WRITEOFF:
            review_by = rules.review_group(kind, [item['check_by'] for item in items])
            if review_by == rules.REVIEW_HEAD:
                departments = {assets[item['asset_id']].get('department_id') for item in items}
                if len(departments) > 1:
                    raise rules.FaError('ОС разных отделов списывают разными запросами: у них разные руководители',
                                        code='FA_MIXED_DEPARTMENTS')
                review_department_id = next(iter(departments))
                heads = queries.department_heads(cursor, [review_department_id])
                if not review_department_id or review_department_id not in heads:
                    # У отдела нет руководителя — проверяет бухгалтерия, иначе запрос встал бы навсегда.
                    review_by, review_department_id = rules.REVIEW_ACCOUNTING, None
        elif kind == rules.KIND_NEED:
            review_by = rules.review_group(kind, [])

        status = rules.after_submit(kind)
        queries.snapshot_items(cursor, request_id)
        if kind in rules.LOCKING_KINDS:
            queries.hold_assets(cursor, request_id, [item['asset_id'] for item in items])
        queries.mark_submitted(cursor, request_id, status=status, review_by=review_by,
                               review_department_id=review_department_id, holder=holder,
                               act_date=today if status == rules.STATUS_SIGNING else None)
        fresh = queries.read_request(cursor, request_id)
        queries.add_event(cursor, 'submitted', actor, request_id=request_id, payload={
            'kind': kind, 'status': status, 'status_label': rules.status_label(fresh),
            'comment': request_row.get('comment'),
            'from': {'name': fresh.get('from_name'), 'department_name': fresh.get('from_department_name')},
            'to': ({'name': fresh.get('to_name'), 'department_name': fresh.get('to_department_name'),
                    'location': fresh.get('to_location'), 'on_stock': bool(fresh.get('to_stock'))}
                   if kind in rules.MOVING_KINDS else None),
            'need': ({'title': fresh.get('need_title'), 'for_name': fresh.get('need_for_name')}
                     if kind == rules.KIND_NEED else None),
            'assets': [{'id': item['asset_id'], 'name': item['name'], 'inventory_number': item['inventory_number']}
                       for item in items],
            'files': [_file_note(row['id'], row, row['kind']) for row in stored if row['kind'] != rules.ACT_SCAN],
        })
        return fresh

    def _save_request(ctx, request_id=None):
        data = _payload()
        uploads = _uploads()
        photo_meta = data.get('photos') if isinstance(data.get('photos'), list) else []
        submit = rules.clean_bool(data.get('submit'))
        actor = _actor(ctx)

        with db._get_cursor() as cursor:
            current = None
            if request_id:
                current = queries.read_request(cursor, request_id)
                if not current or 'edit' not in access.allowed_actions(ctx, current):
                    raise rules.FaError('Править можно только свой черновик', code='FA_NOT_DRAFT', status=409)
                kind = current['kind']
            else:
                kind = data.get('kind')
                if kind not in rules.REQUESTABLE_KINDS:
                    raise rules.FaError('Неизвестный вид запроса', code='FA_KIND_UNKNOWN')
                if not access.can_create(ctx, kind):
                    _forbid('Запрос такого вида вам не доступен', code='FA_KIND_FORBIDDEN')
            fields = rules.clean_request(kind, data)
            columns, asset_ids = _resolve_request(cursor, ctx, kind, fields, current=current)

        prepared = _prepare_photos(uploads, photo_meta, asset_ids=asset_ids) if kind in rules.ASSET_KINDS else []
        if uploads and kind not in rules.ASSET_KINDS:
            raise rules.FaError('К этому запросу фото не прикладываются')
        stored = _upload_photos(prepared)
        garbage = []
        try:
            with db._get_cursor() as cursor:
                if request_id:
                    locked = queries.read_request(cursor, request_id, for_update=True)
                    if not locked or locked['status'] != rules.STATUS_DRAFT:
                        raise rules.FaError('Запрос уже отправлен', code='FA_NOT_DRAFT', status=409)
                    queries.update_request(cursor, request_id, columns)
                else:
                    request_id = queries.create_request(cursor, kind, columns, actor)
                if kind in rules.ASSET_KINDS:
                    garbage += queries.set_items(cursor, request_id, asset_ids)
                    removed = rules.clean_id_list(data.get('remove_file_ids'), label='Фото', limit=500)
                    garbage += queries.drop_files(cursor, request_id, removed)
                    garbage += _store_photos(cursor, request_id, stored, actor)
                if submit:
                    _submit(cursor, ctx, request_id)
                card = _request_card(cursor, ctx, queries.read_request(cursor, request_id))
        except Exception:
            _drop_stored(stored)
            raise
        files.drop_blobs(gcs, garbage)
        return _ok(card, 200 if current else 201)

    @route('/requests', methods=('POST',))
    def fa_request_create(ctx):
        return _save_request(ctx)

    @route('/requests/<int:request_id>/save', methods=('POST',))
    def fa_request_save(request_id, ctx):
        return _save_request(ctx, request_id)

    @route('/requests/<int:request_id>', methods=('DELETE',))
    def fa_request_delete(request_id, ctx):
        with db._get_cursor() as cursor:
            request_row = queries.read_request(cursor, request_id, for_update=True)
            if not request_row or 'delete' not in access.allowed_actions(ctx, request_row):
                raise rules.FaError('Удалить можно только свой черновик', code='FA_NOT_DRAFT', status=409)
            refs = queries.delete_draft(cursor, request_id)
        files.drop_blobs(gcs, refs)
        return _ok({'status': 'deleted'})

    @route('/requests/<int:request_id>/submit', methods=('POST',))
    def fa_request_submit(request_id, ctx):
        with db._get_cursor() as cursor:
            _submit(cursor, ctx, request_id)
            return _ok(_request_card(cursor, ctx, queries.read_request(cursor, request_id)))

    def _step(cursor, ctx, request_id, action, status):
        """Запрос под замком и право на шаг: иначе — «уже сделано» вместо двойной записи."""
        request_row = _load_request(cursor, ctx, request_id, for_update=True)
        if request_row['status'] != status or action not in access.allowed_actions(ctx, request_row):
            raise rules.FaError('Этот шаг уже сделан или вам недоступен — обновите карточку',
                                code='FA_STEP_GONE', status=409)
        return request_row

    @route('/requests/<int:request_id>/check', methods=('POST',))
    def fa_request_check(request_id, ctx):
        """«Проверено» — без него запрос на списание не уходит на подписи (ТЗ, правило «Списание»)."""
        comment = rules.clean_text(_payload().get('comment'), 2000, label='Заключение')
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            request_row = _step(cursor, ctx, request_id, 'check', rules.STATUS_REVIEW)
            queries.mark_checked(cursor, request_id, actor, comment, queries.today_almaty())
            queries.add_event(cursor, 'checked', actor, request_id=request_id, payload={
                'comment': comment, 'review_by': request_row.get('review_by'),
                'review_label': rules.CHECK_GROUP_LABELS.get(request_row.get('review_by'), 'Бухгалтерия')})
            return _ok(_request_card(cursor, ctx, queries.read_request(cursor, request_id)))

    @route('/requests/<int:request_id>/scan', methods=('POST',))
    def fa_request_scan(request_id, ctx):
        """Скан подписанного акта (PDF) с датой подписания: запрос уходит ждать оригинал.
        Пока оригинал ждут, скан можно заменить (приложили не тот файл): прежний остаётся в
        истории, запрос остаётся на том же шаге."""
        data = _payload()
        uploads = _uploads()
        today = queries.today_almaty()
        signed_on = rules.past_date(rules.clean_date(data.get('signed_on'), label='дата подписания', required=True),
                                    today, label='Дата подписания')
        if len(uploads) != 1:
            raise rules.FaError('Приложите один файл — скан подписанного акта в PDF', code='FA_SCAN_REQUIRED')
        blob = uploads[0].read()
        name = files.check_scan(blob, filename=uploads[0].filename)
        with db._get_cursor() as cursor:
            current = _load_request(cursor, ctx, request_id)
            replacing = current['status'] == rules.STATUS_ORIGINAL
            action = 'rescan' if replacing else 'scan'
            if (current['status'] not in (rules.STATUS_SIGNING, rules.STATUS_ORIGINAL)
                    or action not in access.allowed_actions(ctx, current)):
                raise rules.FaError('Этот шаг уже сделан или вам недоступен — обновите карточку',
                                    code='FA_STEP_GONE', status=409)
            if current.get('act_date') and signed_on < current['act_date']:
                raise rules.FaError('Дата подписания раньше даты акта (%s)' % acts.date_short(current['act_date']),
                                    code='FA_DATE_BEFORE_ACT', fields={'signed_on': 'Раньше даты акта'})
        stored = files.upload_scan(gcs, blob, filename=name)
        actor = _actor(ctx)
        try:
            with db._get_cursor() as cursor:
                locked = _step(cursor, ctx, request_id, action, current['status'])
                previous = queries.read_file(cursor, locked['scan_file_id']) if replacing and locked.get('scan_file_id') else None
                file_id = queries.add_file(cursor, request_id, None, rules.ACT_SCAN, stored, actor)
                queries.mark_scan(cursor, request_id, file_id, signed_on, actor)
                payload = {'signed_on': signed_on, 'files': [_file_note(file_id, stored, rules.ACT_SCAN)]}
                if previous:
                    payload['replaced'] = {'id': previous['id'], 'name': previous.get('file_name')}
                queries.add_event(cursor, 'scan_replaced' if replacing else 'scan_attached', actor,
                                  request_id=request_id, payload=payload)
                card = _request_card(cursor, ctx, queries.read_request(cursor, request_id))
        except Exception:
            files.drop_blobs(gcs, [(stored['bucket'], stored['blob_path'])])
            raise
        return _ok(card)

    @route('/requests/<int:request_id>/original', methods=('POST',))
    def fa_request_original(request_id, ctx):
        """«Оригинал получен»: бухгалтерия отмечает, кто и когда передал оригинал акта. Запрос
        закрывается, и только теперь меняется само ОС — новый МОЛ или «Списано»."""
        data = _payload()
        today = queries.today_almaty()
        handed_on = rules.past_date(
            rules.clean_date(data.get('handed_on'), label='дата передачи в бухгалтерию', required=True),
            today, label='Дата передачи в бухгалтерию')
        handed_by_id = rules.clean_int(data.get('handed_by_user_id'), label='Кто передал оригинал', required=True)
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            request_row = _step(cursor, ctx, request_id, 'receive', rules.STATUS_ORIGINAL)
            if request_row.get('signed_on') and handed_on < request_row['signed_on']:
                raise rules.FaError('Оригинал не могли передать раньше, чем акт подписали (%s)'
                                    % acts.date_short(request_row['signed_on']), code='FA_DATE_BEFORE_SIGN',
                                    fields={'handed_on': 'Раньше даты подписания'})
            handed_by = queries.user_brief(cursor, handed_by_id)
            if not handed_by:
                raise rules.FaError('Сотрудник не найден', fields={'handed_by_user_id': 'Сотрудник не найден'})
            items = queries.read_items(cursor, request_id)
            queries.mark_closed(cursor, request_id, handed_by=handed_by, handed_on=handed_on, actor=actor)
            queries.add_event(cursor, 'original_received', actor, request_id=request_id, payload={
                'handed_by_name': handed_by['name'], 'handed_on': handed_on})
            closed = queries.read_request(cursor, request_id)
            if request_row['kind'] in rules.MOVING_KINDS:
                queries.apply_move(cursor, closed, items, actor)
            elif request_row['kind'] == rules.KIND_WRITEOFF:
                queries.apply_writeoff(cursor, closed, items, actor)
            queries.add_event(cursor, 'closed', actor, request_id=request_id, payload={})
            return _ok(_request_card(cursor, ctx, queries.read_request(cursor, request_id)))

    @route('/requests/<int:request_id>/reject', methods=('POST',))
    def fa_request_reject(request_id, ctx):
        """«Отклонён» — из любого шага, с обязательной причиной (ТЗ, раздел 5)."""
        reason = rules.clean_text(_payload().get('reason'), 2000, label='Причина', required=True)
        if len(reason) < 3:
            raise rules.FaError('Напишите причину отклонения', fields={'reason': 'Причина обязательна'})
        actor = _actor(ctx)
        with db._get_cursor() as cursor:
            request_row = _load_request(cursor, ctx, request_id, for_update=True)
            if 'reject' not in access.allowed_actions(ctx, request_row):
                raise rules.FaError('Этот запрос уже закрыт или вам недоступен — обновите карточку',
                                    code='FA_STEP_GONE', status=409)
            queries.mark_rejected(cursor, request_id, reason=reason, from_status=request_row['status'], actor=actor)
            queries.release_assets(cursor, request_id)
            queries.add_event(cursor, 'rejected', actor, request_id=request_id, payload={
                'reason': reason, 'from_status': request_row['status'],
                'from_status_label': rules.status_label(request_row)})
            return _ok(_request_card(cursor, ctx, queries.read_request(cursor, request_id)))

    @route('/requests/<int:request_id>/act')
    def fa_request_act(request_id, ctx):
        with db._get_cursor() as cursor:
            request_row = _load_request(cursor, ctx, request_id)
            if 'act' not in access.allowed_actions(ctx, request_row):
                raise rules.FaError('Акт появится, когда запрос уйдёт на подписание', code='FA_ACT_EARLY', status=409)
            items = queries.read_items(cursor, request_id)
            heads = queries.department_heads(cursor, [request_row.get('from_department_id'),
                                                      request_row.get('to_department_id')])
            people = {user_id: queries.user_brief(cursor, user_id)
                      for user_id in (request_row.get('from_user_id'), request_row.get('to_user_id'),
                                      request_row.get('checked_by')) if user_id}
            requisites = queries.read_requisites(cursor)
        # Стоимость в печатной форме видит тот же круг, что и в карточке ОС: бухгалтерия, HR,
        # руководитель аналитики. У остальных графа стоимости в акте пустая.
        content = acts.build(request_row, items, rules.signers(request_row, heads), people,
                             requisites=requisites, with_cost=access.can_see_cost(ctx), heads=heads)
        return _download(content, DOCX_MIME, acts.file_name(request_row))

    @route('/files/<int:file_id>')
    def fa_file(file_id, ctx):
        """Скан акта: открывает тот, кто видит запрос. Фото отдаются подписанными ссылками."""
        with db._get_cursor() as cursor:
            row = queries.read_file(cursor, file_id)
            if not row or row['kind'] != rules.ACT_SCAN:
                _not_found('Файл не найден')
            _load_request(cursor, ctx, row['request_id'])
        content = files.download(gcs, row['bucket'], row['blob_path'])
        if content is None:
            _not_found('Файл не найден в хранилище')
        return _download(content, row.get('content_type') or files.PDF_TYPE, row.get('file_name') or 'акт.pdf',
                         attachment=False)

    # ─── История ─────────────────────────────────────────────────────────────

    def _date_arg(name):
        value = request.args.get(name)
        return rules.clean_date(value, label=name) if value else None

    @route('/history')
    def fa_history(ctx):
        """Бухгалтерия, HR и руководитель аналитики видят всю историю; остальные — записи, в
        которых участвуют сами (queries.event_scope)."""
        limit, offset = _page()
        kind = request.args.get('kind') or None
        if kind and kind not in rules.EVENT_LABELS:
            raise rules.FaError('Неизвестный вид события')
        with db._get_cursor() as cursor:
            total, events = queries.journal(
                cursor, ctx, limit=limit, offset=offset, kind=kind,
                query=(request.args.get('q') or '').strip()[:200] or None,
                date_from=_date_arg('date_from'), date_to=_date_arg('date_to'))
        return _ok({'items': events, 'total': total, 'limit': limit, 'offset': offset,
                    'scope': 'all' if access.sees_all(ctx) else 'own'})

    # ─── Увольнение ──────────────────────────────────────────────────────────

    def _dismissal_state(cursor, ctx, user_id):
        """Что с ОС сотрудника: сколько числится, какие держат приказ, идёт ли передача."""
        person = queries.user_brief(cursor, user_id)
        if not person:
            _not_found('Сотрудник не найден')
        assets = queries.user_assets(cursor, user_id)
        blockers = queries.dismissal_blockers(cursor, user_id)
        request_row = queries.open_dismissal_request(cursor, user_id) or queries.last_dismissal_request(cursor, user_id)
        return {
            'user': person,
            'assets': _asset_views(ctx, assets),
            'blockers': blockers,
            # ТЗ, правило «Увольнение»: приказ недоступен, пока есть ОС без подписанного акта.
            'order_locked': bool(blockers),
            'request': _request_brief(ctx, request_row) if request_row else None,
        }

    @route('/dismissals')
    def fa_dismissals(ctx):
        if not access.can_run_dismissal(ctx):
            _forbid('Передачу при увольнении ведёт HR')
        with db._get_cursor() as cursor:
            rows = [_dismissal_state(cursor, ctx, row['user_id']) for row in queries.dismissal_board(cursor)]
        return _ok({'items': rows})

    @route('/dismissals/check')
    def fa_dismissal_check(ctx):
        user_id = _int_arg('user_id')
        if not user_id:
            raise rules.FaError('Выберите сотрудника')
        if not (access.can_run_dismissal(ctx) or access.sees_all(ctx)):
            _forbid('ОС сотрудника перед увольнением проверяет HR')
        with db._get_cursor() as cursor:
            return _ok(_dismissal_state(cursor, ctx, user_id))

    # ─── Настройки ───────────────────────────────────────────────────────────

    def _settings(cursor, ctx):
        return {
            'categories': queries.list_categories(cursor, include_inactive=True),
            'members': queries.list_members(cursor),
            'role_departments': queries.role_departments(cursor),
            'role_labels': rules.MEMBER_ROLE_LABELS,
            'check_groups': rules.CHECK_GROUP_LABELS,
            'can_manage_members': access.can_manage_members(ctx),
            'requisites': queries.read_requisites(cursor),
            'limits': {'commission_members': rules.MAX_COMMISSION_MEMBERS},
        }

    def _category_fields(data):
        name = rules.clean_text(data.get('name'), 120, label='Название', required=True)
        check_by = data.get('check_by')
        if check_by not in rules.CHECK_GROUPS:
            raise rules.FaError('Выберите, кто проверяет ОС перед списанием', fields={'check_by': 'Не выбрано'})
        return name, check_by

    @route('/settings')
    def fa_settings(ctx):
        if not access.can_edit_settings(ctx):
            _forbid('Настройки раздела ведёт бухгалтерия')
        with db._get_cursor() as cursor:
            return _ok(_settings(cursor, ctx))

    @route('/settings/categories', methods=('POST',))
    def fa_category_create(ctx):
        if not access.can_edit_settings(ctx):
            _forbid('Настройки раздела ведёт бухгалтерия')
        name, check_by = _category_fields(_payload())
        with db._get_cursor() as cursor:
            if queries.category_name_taken(cursor, name):
                raise rules.FaError('Такая категория уже есть', code='FA_CATEGORY_TAKEN', status=409,
                                    fields={'name': 'Такая категория уже есть'})
            queries.create_category(cursor, name, check_by)
            return _ok(_settings(cursor, ctx), 201)

    @route('/settings/categories/<int:category_id>', methods=('PUT',))
    def fa_category_update(category_id, ctx):
        if not access.can_edit_settings(ctx):
            _forbid('Настройки раздела ведёт бухгалтерия')
        data = _payload()
        name, check_by = _category_fields(data)
        with db._get_cursor() as cursor:
            if not queries.read_category(cursor, category_id):
                _not_found('Категория не найдена')
            if queries.category_name_taken(cursor, name, exclude_id=category_id):
                raise rules.FaError('Такая категория уже есть', code='FA_CATEGORY_TAKEN', status=409,
                                    fields={'name': 'Такая категория уже есть'})
            queries.update_category(cursor, category_id, name=name, check_by=check_by,
                                    is_active=data.get('is_active', True) is not False)
            return _ok(_settings(cursor, ctx))

    @route('/settings/requisites', methods=('PUT',))
    def fa_requisites_save(ctx):
        """Реквизиты организации и комиссия по списанию — для шапки актов и формы ДА-3."""
        if not access.can_edit_settings(ctx):
            _forbid('Настройки раздела ведёт бухгалтерия')
        requisites = rules.clean_requisites(_payload())
        with db._get_cursor() as cursor:
            queries.write_setting(cursor, queries.REQUISITES_KEY, requisites, _actor(ctx))
            return _ok(_settings(cursor, ctx))

    def _member_fields(data):
        user_id = rules.clean_int(data.get('user_id'), label='Сотрудник', required=True)
        role = data.get('role')
        if role not in rules.MEMBER_ROLES:
            raise rules.FaError('Выберите роль')
        return user_id, role

    @route('/settings/members', methods=('POST',))
    def fa_member_add(ctx):
        if not access.can_manage_members(ctx):
            _forbid('Участников раздела назначает суперадмин')
        user_id, role = _member_fields(_payload())
        with db._get_cursor() as cursor:
            person = queries.user_brief(cursor, user_id)
            if not person or person['fired']:
                raise rules.FaError('Сотрудник не найден или уволен')
            queries.add_member(cursor, user_id, role, ctx['user_id'])
            return _ok(_settings(cursor, ctx), 201)

    @route('/settings/members', methods=('DELETE',))
    def fa_member_remove(ctx):
        if not access.can_manage_members(ctx):
            _forbid('Участников раздела назначает суперадмин')
        user_id, role = _member_fields({'user_id': request.args.get('user_id'), 'role': request.args.get('role')})
        with db._get_cursor() as cursor:
            queries.remove_member(cursor, user_id, role)
            return _ok(_settings(cursor, ctx))

    # ─── Выгрузка ────────────────────────────────────────────────────────────

    @route('/export')
    def fa_export(ctx):
        if not access.sees_all(ctx):
            _forbid('Выгрузку реестра делают бухгалтерия, HR и руководитель аналитики')
        with db._get_cursor() as cursor:
            _total, assets = queries.list_assets(cursor, ctx, limit=0)
            _events_total, events = queries.journal(cursor, ctx, limit=5000)
        content = report.build(assets, events, with_cost=access.can_see_cost(ctx),
                               excel_text_warning=excel_text_warning)
        return _download(content, XLSX_MIME, report.file_name(queries.today_almaty()))

    return bp
