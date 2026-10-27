# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Дисциплина» (Flask Blueprint /api/discipline).

Устроен как complaints/routes.py: фабрика получает зависимости аргументами
(обратный импорт из bot_schedule2 был бы циклом), каждый роут проходит общий
каркас — preflight, авторизация, контекст доступа и вход в раздел, — а ошибки
отдаются {"error": "..."} по-русски. QR-подтверждения раздел не спрашивает
(владелец, 23.10.2026; см. access.py).

Файлы сначала проверяются и заливаются в хранилище, потом пишется база; если
транзакция откатилась, залитое стирается (files.drop) — в бакете не остаётся
файлов, на которые нет строки. Telegram уходит после фиксации транзакции.
"""

import logging
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO
from urllib.parse import quote

from flask import Blueprint, jsonify, request, send_file

from . import access, act_docx, catalog, explanation_docx, files, notify, queries, report, schema, service

JOURNAL_LIMIT = 1500
AUDIT_PAGE_MIN, AUDIT_PAGE_MAX = 5, 100


def build_discipline_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                               send_telegram=None, web_app_base_url=None, gcs=None, avatar_url=None):
    bp = Blueprint('discipline', __name__, url_prefix='/api/discipline')

    def route(rule, methods=('GET',), manager=False, upload=None):
        """upload — вид вложений роута (catalog.FILE_KINDS): по нему потолок тела."""
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                outbox = notify.Outbox()
                uploaded = []
                try:
                    requester_id, _requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    today = service.today_almaty()
                    with db._get_cursor() as cursor:
                        if not schema.schema_is_ready(cursor):
                            return jsonify({"error": "Раздел «Дисциплина» ещё не развёрнут",
                                            "code": "DISCIPLINE_NOT_READY"}), 503
                        ctx = queries.load_access_context(cursor, requester_id, today)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    if not access.can_open_section(ctx):
                        return jsonify({"error": "Раздел «Дисциплина» вам не открыт",
                                        "code": "DISCIPLINE_SECTION_CLOSED"}), 403
                    if manager and not access.is_manager(ctx):
                        return jsonify({"error": "Это доступно руководителю отдела",
                                        "code": "DISCIPLINE_FORBIDDEN"}), 403
                    # До первого обращения к форме или JSON: тело больше честного
                    # не читается вовсе. `or 0` — запрос без тела заголовка не шлёт.
                    kind = upload if upload and access.can_send_files(ctx, upload) else None
                    if (request.content_length or 0) > catalog.request_max_bytes(kind):
                        return jsonify({"error": catalog.request_too_large_text(kind),
                                        "code": "DISCIPLINE_REQUEST_TOO_LARGE"}), 413
                    response = handler(*args, ctx=ctx, today=today, outbox=outbox, uploaded=uploaded, **kwargs)
                    _flush(outbox)
                    return response
                except (service.DisciplineError, files.FileError, catalog.SettingsError) as exc:
                    files.drop(gcs, uploaded)
                    payload = {"error": getattr(exc, 'message', None) or str(exc)}
                    code = getattr(exc, 'code', None)
                    if code:
                        payload['code'] = code
                    return jsonify(payload), getattr(exc, 'status', 400)
                except Exception as exc:  # noqa: BLE001
                    files.drop(gcs, uploaded)
                    logging.exception('discipline: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Дисциплина»",
                                    "detail": str(exc)[:200]}), 500
            return wrapper
        return decorator

    def _flush(outbox):
        if not send_telegram:
            return
        for chat_id, text, markup in outbox.messages:
            try:
                send_telegram(chat_id, text, parse_mode='HTML', reply_markup=markup)
            except Exception:  # noqa: BLE001
                logging.warning('discipline: Telegram не принял сообщение для %s', chat_id, exc_info=True)

    def _payload():
        if request.files or request.form:
            data = {key: request.form.get(key) for key in request.form}
            for key in request.form:
                values = request.form.getlist(key)
                if len(values) > 1:
                    data[key] = values
            return data
        return request.get_json(silent=True) or {}

    def _take_files(kind, field='files', required=False, uploaded=None):
        """Проверить и залить файлы из формы. Возвращает описания для базы."""
        items = [item for item in request.files.getlist(field) if str(item.filename or '').strip()]
        if field != 'file':
            items += [item for item in request.files.getlist('file') if str(item.filename or '').strip()]
        if required and not items:
            raise files.FileError('Приложите файл')
        spec = catalog.FILE_KINDS[kind]
        if len(items) > spec['max_files']:
            raise files.FileError('Не больше %d файлов за раз' % spec['max_files'])
        prepared = []
        for item in items:
            # Байт сверх границы вида хватает, чтобы files.check сказал «больше N МБ».
            data = item.read(catalog.file_max_bytes(kind) + 1)
            name, _ext, content_type = files.check(kind, filename=item.filename, data=data)
            prepared.append((name, content_type, data))
        result = []
        for name, content_type, data in prepared:
            bucket, blob_path = files.upload(gcs, kind=kind, data=data, filename=name, content_type=content_type)
            if uploaded is not None:
                uploaded.append((bucket, blob_path))
            result.append({'name': name, 'content_type': content_type, 'size': len(data),
                           'sha256': files.sha256(data), 'bucket': bucket, 'blob_path': blob_path})
        return result

    def _iso(value):
        return service._iso(value)

    def _dept_param(ctx):
        raw = request.args.get('department_id')
        managed = access.managed_department_ids(ctx)
        if raw and str(raw).isdigit() and int(raw) in managed:
            return int(raw)
        return None

    def _department_choices(cursor, ctx):
        ids = sorted(access.managed_department_ids(ctx))
        if not ids and ctx.get('department_id') is not None:
            ids = [int(ctx['department_id'])]
        names = queries.department_names(cursor, ids)
        return [{'id': i, 'name': (names.get(i) or {}).get('name') or '', 'code': (names.get(i) or {}).get('code')}
                for i in ids]

    def _public(items, ctx):
        return [service.public_record(item, ctx) for item in items]

    def _audit_export(cursor, ctx, entity, changes):
        """Выгрузка — в журнал КАЖДОГО отдела, чьи данные в неё попали: журнал
        руководитель читает по своим отделам, и строка без отдела там пропала бы."""
        department_id = _dept_param(ctx)
        if department_id:
            departments = [department_id]
        else:
            departments = sorted(access.managed_department_ids(ctx)) or (
                [int(ctx['department_id'])] if ctx.get('department_id') is not None else [])
        for department in departments:
            queries.audit(cursor, ctx, 'export', entity, department_id=department, changes=changes)

    # ── Вход ────────────────────────────────────────────────────────────────

    # ping — права и ЧИСЛО своих записей: по нему меню решает, показывать ли
    # оператору пункт.
    @route('/ping')
    def discipline_ping(ctx, today, **_kw):
        with db._get_cursor() as cursor:
            departments = _department_choices(cursor, ctx)
            for department in departments:
                queries.ensure_department_defaults(cursor, department['id'])
            payload = {
                'ok': True,
                'user_id': ctx['user_id'],
                'capabilities': access.capabilities(ctx),
                'departments': departments,
                'storage_ready': files.storage_ready(gcs),
            }
            if access.is_operator(ctx):
                cursor.execute(
                    'SELECT COUNT(*) FROM discipline_records WHERE operator_id = %s AND cancelled_at IS NULL',
                    (ctx['user_id'],))
                payload['own_total'] = int(cursor.fetchone()[0] or 0)
        return jsonify(payload)

    # ── Сводка ──────────────────────────────────────────────────────────────

    @route('/summary')
    def discipline_summary(ctx, today, **_kw):
        if access.is_operator(ctx):
            raise service.DisciplineError('Сводка — для супервайзеров и руководителей', 403)
        now = service.now_almaty()
        department_id = _dept_param(ctx)
        with db._get_cursor() as cursor:
            settings_for = service.SettingsCache(cursor)
            departments = _department_choices(cursor, ctx)
            base_settings = settings_for(department_id or (departments[0]['id'] if departments else None))
            window_start, _inclusive = catalog.window_start(now, base_settings)
            controls = service.visible_controls(cursor, ctx, today, state='active')
            if department_id:
                controls = [c for c in controls if (c.get('department_id') or c.get('operator_department_id'))
                            == department_id]
            control_rows = service.control_rows(cursor, ctx, controls, today)
            filters = {'date_from': window_start, 'department_id': department_id}
            period_records = queries.list_records(cursor, ctx, today, filters, limit=JOURNAL_LIMIT)
            enriched = service.enrich(cursor, ctx, period_records, now, settings_for=settings_for)
            # «За шаг до акта» (жёлтый блок, п. 4.1.3): чья следующая запись
            # потребует акта — по окну, которое заканчивается сейчас.
            by_operator = {}
            for item in enriched:
                by_operator.setdefault(item['operator_id'], []).append(item)
            histories = queries.operator_histories(cursor, by_operator.keys(),
                                                   since=now - timedelta(days=service.HISTORY_DAYS))
            pre_act = []
            on_control = {row['operator_id'] for row in control_rows}
            for operator_id, items in by_operator.items():
                settings = settings_for(items[0].get('department_id'))
                act_index = catalog.act_step(settings.get('ladder'))
                if not act_index or operator_id in on_control:
                    continue
                step = catalog.next_step(histories.get(operator_id, []), now, settings)
                if step['action'] == catalog.STEP_ACT:
                    last = max(items, key=lambda i: i['occurred_at'])
                    pre_act.append({'operator_id': operator_id, 'operator_name': last.get('operator_name'),
                                    'group_name': last.get('group_name'), 'count': step['count'],
                                    'last_at': _iso(last['occurred_at']), 'last_type': last.get('type_title')})
            pre_act.sort(key=lambda r: (-r['count'], r['operator_name'] or ''))
            counters = {
                'total': len(enriched),
                'with_explanation': sum(1 for i in enriched if i.get('explanation_received_at')),
                # «Актов составлено» — с подписанным файлом: черновик, который система
                # завела сама, ещё не акт (у записи статус «Требуется акт»).
                'acts': sum(1 for i in enriched if (i.get('act') or {}).get('is_trigger')
                            and (i.get('act') or {}).get('has_file')),
                'on_control': len(control_rows),
            }
            latest = queries.list_records(cursor, ctx, today, {'department_id': department_id}, limit=20)
            latest_items = service.enrich(cursor, ctx, latest, now, settings_for=settings_for)
        return jsonify({
            'departments': departments,
            'period': {'kind': base_settings['period_kind'], 'days': base_settings['period_days'],
                       'from': _iso(window_start)},
            'controls': control_rows,
            'counters': counters,
            'pre_act': pre_act,
            'latest': _public(latest_items, ctx),
        })

    # ── Журнал ──────────────────────────────────────────────────────────────

    def _journal_filters(ctx, today):
        args = request.args
        filters = {}
        try:
            if args.get('date_from'):
                filters['date_from'] = datetime.strptime(args['date_from'][:10], '%Y-%m-%d')
            if args.get('date_to'):
                filters['date_to'] = datetime.strptime(args['date_to'][:10], '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            raise service.DisciplineError('Период указан неверно') from None
        if 'date_from' not in filters and not args.get('operator_id'):
            filters['date_from'] = datetime.combine(today - timedelta(days=90), datetime.min.time())
        for key in ('operator_id', 'group_id', 'created_by'):
            if args.get(key) and str(args[key]).isdigit():
                filters[key] = int(args[key])
        if args.get('type_title'):
            filters['type_title'] = args['type_title'][:200]
        if args.get('search'):
            filters['search'] = args['search'][:100]
        department_id = _dept_param(ctx)
        if department_id:
            filters['department_id'] = department_id
        if args.get('include_cancelled') in ('1', 'true'):
            filters['include_cancelled'] = True
        return filters

    def _post_filter(items):
        args = request.args
        state = args.get('state')
        if state:
            items = [i for i in items if i['state'] == state]
        if args.get('explanation') == 'yes':
            items = [i for i in items if i.get('explanation_received_at') or i.get('refused_at')]
        elif args.get('explanation') == 'no':
            items = [i for i in items if i.get('needs_explanation_now') and not i.get('explanation_received_at')
                     and not i.get('refused_at')]
        elif args.get('explanation') == 'overdue':
            items = [i for i in items if i.get('explanation_overdue')]
        if args.get('act') == 'yes':
            items = [i for i in items if i.get('act')]
        elif args.get('act') == 'no':
            items = [i for i in items if not i.get('act')]
        return items

    @route('/records')
    def discipline_records(ctx, today, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            filters = _journal_filters(ctx, today)
            records = queries.list_records(cursor, ctx, today, filters, limit=JOURNAL_LIMIT)
            items = _post_filter(service.enrich(cursor, ctx, records, now))
            choices = queries.journal_choices(cursor, ctx, today, filters) if not access.is_operator(ctx) else {}
        return jsonify({'items': _public(items, ctx), 'truncated': len(records) >= JOURNAL_LIMIT,
                        'types': choices.get('types', []), 'groups': choices.get('groups', []),
                        'creators': choices.get('creators', []),
                        'date_from': _iso(filters.get('date_from').date()) if filters.get('date_from') else None})

    @route('/records/export')
    def discipline_records_export(ctx, today, **_kw):
        if access.is_operator(ctx):
            raise service.DisciplineError('Выгрузка — для супервайзеров и руководителей', 403)
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            filters = _journal_filters(ctx, today)
            records = queries.list_records(cursor, ctx, today, filters, limit=20000)
            items = _post_filter(service.enrich(cursor, ctx, records, now))
            _audit_export(cursor, ctx, 'journal', {'rows': len(items)})
        data = report.workbook('Журнал нарушений', [
            ('Собрано', now.strftime('%d.%m.%Y %H:%M')),
            ('Кто выгрузил', ctx.get('name') or ''),
            ('Период', '%s — %s' % (filters.get('date_from').strftime('%d.%m.%Y') if filters.get('date_from')
                                    else 'с начала', (filters['date_to'] - timedelta(days=1)).strftime('%d.%m.%Y')
                                    if filters.get('date_to') else 'по сегодня')),
            ('Записей', len(items)),
            ('Как читать', '«№ в периоде» и «Ступень» — по лестнице отдела, которая действовала, когда '
                           'запись зафиксировали; после отмены записи номера соседей сдвигаются.'),
        ], [('Журнал', report.JOURNAL_COLUMNS, report.journal_rows(items))])
        return _xlsx(data, 'Дисциплина_журнал_%s.xlsx' % now.strftime('%Y-%m-%d'))

    def _xlsx(data, filename):
        # Чисел текстом в выгрузках раздела нет (номер «Д-2026-0001» — с буквами),
        # поэтому <ignoredErrors> не нужен.
        return send_file(BytesIO(data), mimetype=report.XLSX_MIME, as_attachment=True, download_name=filename)

    @route('/records/<int:record_id>')
    def discipline_record(ctx, today, record_id, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today)
            item = service.enrich(cursor, ctx, [record], now)[0]
            payload = service.public_record(item, ctx)
            internal = access.can_see_internal(ctx, record)
            file_rows = queries.files_for(cursor, record_ids=[record_id])
            act = queries.acts_by_record(cursor, [record_id]).get(record_id)
            act_payload = None
            if act and internal:
                act_files = queries.files_for(cursor, act_ids=[act['id']])
                members = [queries.get_record(cursor, rid, today) for rid in act['record_ids'] if rid]
                visible = [r for r in members if r and access.can_view_record(ctx, r)]
                act_records = [service.public_record(r, ctx) for r in service.enrich(cursor, ctx, visible, now)]
                # Запись акта вне доступа — только номер и дата, без содержания.
                act_records += [{'id': r['id'], 'number': r['number'], 'occurred_at': _iso(r['occurred_at']),
                                 'hidden': True} for r in members if r and not access.can_view_record(ctx, r)]
                # По времени нарушения, как в черновике акта: запись, заведённая
                # задним числом, встаёт на своё место, а не в конец по номеру.
                moments = {r['id']: r['occurred_at'] for r in members if r}
                act_records.sort(key=lambda r: (moments[r['id']], r['id']))
                act_payload = {key: _iso(value) for key, value in act.items() if key not in ('record_ids',)}
                act_payload.update({'files': [_file_payload(f, ctx, record) for f in act_files], 'records': act_records,
                                    'basis_check': (item.get('act') or {}).get('basis_check')})
            if internal:
                perms = item.get('permissions') or {}
                # Справочники — отдела самой записи: сервер сверяет меру и тип с
                # ним, а у переведённого человека нынешний отдел другой.
                if perms.get('can_propose_measure') or perms.get('can_manage'):
                    payload['measures'] = [_measure_payload(m) for m in queries.list_measures(
                        cursor, [record['department_id']])]
                if perms.get('can_edit'):
                    payload['types'] = [_type_payload(t) for t in queries.list_types(cursor, [record['department_id']])]
                payload['files'] = [_file_payload(f, ctx, record) for f in file_rows]
                payload['history'] = [{
                    'id': h['id'], 'actor_name': h['actor_name'], 'action': h['action'],
                    'action_label': report.audit_label(h['action']), 'changes': report.changes_text(h['changes']),
                    'at': _iso(h['created_at']),
                } for h in queries.record_history(cursor, record_id)]
                payload['act_full'] = act_payload
                if record.get('training_id'):
                    training = queries.get_training(cursor, record['training_id'])
                    payload['training'] = {k: _iso(v) for k, v in training.items()} if training else None
                if record.get('refusal_witness_ids'):
                    names = queries.user_names(cursor, record['refusal_witness_ids'])
                    payload['refusal_witnesses'] = [names.get(w) for w in record['refusal_witness_ids']]
                if record.get('control_id'):
                    control = queries.get_control(cursor, record['control_id'])
                    payload['control'] = {'id': control['id'], 'reason': control['reason'],
                                          'end_date': _iso(control['end_date'])} if control else None
            else:
                payload['files'] = [_file_payload(f, ctx, record) for f in file_rows if f['kind'] == 'explanation']
            queries.mark_seen(cursor, ctx['user_id'], record_id=record_id)
            queries.audit(cursor, ctx, 'view_record', 'record', entity_id=record_id, operator_id=record['operator_id'],
                          department_id=record['department_id'], record_id=record_id)
        return jsonify(payload)

    def _can_invalidate(ctx, row, record):
        """Признать файл недействительным (п. 13): руководитель записи или тот, кто
        файл загрузил, пока ведёт запись. Та же граница, что в ручке invalidate."""
        if record is None or row.get('invalid_at'):
            return False
        return access.manages_record(ctx, record) or (
            row.get('uploaded_by') == ctx['user_id'] and access.can_work_record(ctx, record))

    def _file_payload(row, ctx=None, record=None):
        return {
            'can_invalidate': bool(ctx) and _can_invalidate(ctx, row, record),
            'id': row['id'], 'kind': row['kind'], 'kind_label': catalog.FILE_KINDS.get(row['kind'], {}).get('label'),
            'name': row['file_name'], 'content_type': row['content_type'], 'size': row['size_bytes'],
            'sha256': row['sha256'], 'uploaded_at': _iso(row['uploaded_at']),
            'uploaded_by_name': row.get('uploaded_by_name'), 'superseded': bool(row.get('superseded_at')),
            'invalid': bool(row.get('invalid_at')), 'invalid_reason': row.get('invalid_reason'),
            'invalid_by_name': row.get('invalid_by_name'), 'record_id': row.get('record_id'),
            'act_id': row.get('act_id'),
        }

    # ── Фиксация и работа с записью ─────────────────────────────────────────

    @route('/operators/search')
    def discipline_operator_search(ctx, today, **_kw):
        with db._get_cursor() as cursor:
            rows = queries.search_operators(cursor, ctx, today, request.args.get('q', ''), limit=40)
        return jsonify({'items': [{
            'id': r['id'], 'name': r['name'], 'login': r.get('login'), 'department_name': r.get('department_name'),
            'department_id': r.get('department_id'), 'group_name': r.get('group_name'),
            'direction_name': r.get('direction_name'), 'role': r.get('role'),
        } for r in rows]})

    @route('/people/search')
    def discipline_people_search(ctx, today, **_kw):
        if access.is_operator(ctx):
            raise service.DisciplineError('Поиск сотрудников — для супервайзеров и руководителей', 403)
        with db._get_cursor() as cursor:
            rows = queries.search_people(cursor, request.args.get('q', ''))
        return jsonify({'items': [{'id': r['id'], 'name': r['name'], 'department_name': r.get('department_name')}
                                  for r in rows]})

    @route('/operator-context')
    def discipline_operator_context(ctx, today, **_kw):
        now = service.now_almaty()
        operator_id = request.args.get('operator_id')
        if not str(operator_id or '').isdigit():
            raise service.DisciplineError('Не выбран сотрудник')
        at = service.parse_moment(request.args.get('at'), now)
        weight = 1
        with db._get_cursor() as cursor:
            if str(request.args.get('type_id') or '').isdigit():
                type_row = queries.get_type(cursor, int(request.args['type_id']))
                weight = int((type_row or {}).get('weight') or 1)
            payload = service.operator_context(cursor, ctx, int(operator_id), at, today, type_weight=weight)
            department_id = payload['operator']['department_id']
            queries.ensure_department_defaults(cursor, department_id)
            payload['types'] = [_type_payload(t) for t in queries.list_types(cursor, [department_id])]
            payload['measures'] = [_measure_payload(m) for m in queries.list_measures(cursor, [department_id])]
        return jsonify(payload)

    @route('/records', methods=('POST',), upload='evidence')
    def discipline_create(ctx, today, outbox, uploaded, **_kw):
        now = service.now_almaty()
        payload = _payload()
        uploads = _take_files('evidence', uploaded=uploaded)
        with db._get_cursor() as cursor:
            replayed = service.replayed_record(cursor, ctx, payload)
            if replayed:
                # Файлы повтора — копии уже сохранённых, в бакете не нужны.
                files.drop(gcs, uploaded)
                return jsonify({'ok': True, 'id': replayed['id'], 'number': replayed['number']}), 200
            record_id = service.create_record(cursor, ctx, payload, uploads, now=now, today=today,
                                              base_url=web_app_base_url, outbox=outbox)
            record = queries.get_record(cursor, record_id, today)
        return jsonify({'ok': True, 'id': record_id, 'number': record['number']}), 201

    @route('/records/<int:record_id>', methods=('PATCH',))
    def discipline_edit(ctx, today, record_id, outbox, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            changed = service.edit_record(cursor, ctx, record, _payload(), now=now, today=today,
                                          base_url=web_app_base_url, outbox=outbox)
        return jsonify({'ok': True, 'changed': changed})

    @route('/records/<int:record_id>/cancel', methods=('POST',))
    def discipline_cancel(ctx, today, record_id, outbox, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            service.cancel_record(cursor, ctx, record, _payload().get('reason'), now=now,
                                  base_url=web_app_base_url, outbox=outbox)
        return jsonify({'ok': True})

    @route('/records/<int:record_id>/close', methods=('POST',))
    def discipline_close(ctx, today, record_id, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            service.close_record(cursor, ctx, record, now=now)
        return jsonify({'ok': True})

    @route('/records/<int:record_id>/comment', methods=('POST',))
    def discipline_comment(ctx, today, record_id, **_kw):
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            service.set_comment(cursor, ctx, record, _payload().get('comment'))
        return jsonify({'ok': True})

    @route('/records/<int:record_id>/explanation', methods=('POST',), upload='explanation')
    def discipline_explanation(ctx, today, record_id, uploaded, **_kw):
        now = service.now_almaty()
        upload = _take_files('explanation', field='file', required=True, uploaded=uploaded)[0]
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            file_id = service.add_explanation(cursor, ctx, record, upload, now=now)
        return jsonify({'ok': True, 'file_id': file_id})

    @route('/records/<int:record_id>/refusal', methods=('POST',), upload='refusal_act')
    def discipline_refusal(ctx, today, record_id, outbox, uploaded, **_kw):
        now = service.now_almaty()
        payload = _payload()
        witnesses = payload.get('witness_ids') or []
        if isinstance(witnesses, str):
            witnesses = [w for w in witnesses.split(',') if w.strip()]
        upload = _take_files('refusal_act', field='file', required=True, uploaded=uploaded)[0]
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            service.record_refusal(cursor, ctx, record, upload, witnesses, now=now, base_url=web_app_base_url,
                                   outbox=outbox)
        return jsonify({'ok': True})

    @route('/records/<int:record_id>/evidence', methods=('POST',), upload='evidence')
    def discipline_evidence(ctx, today, record_id, uploaded, **_kw):
        now = service.now_almaty()
        uploads = _take_files('evidence', required=True, uploaded=uploaded)
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            if not access.can_work_record(ctx, record):
                raise service.DisciplineError('Добавить доказательство вам не разрешено', 403)
            existing = [f for f in queries.files_for(cursor, record_ids=[record_id], kind='evidence')
                        if not f.get('invalid_at')]
            if len(existing) + len(uploads) > catalog.FILE_KINDS['evidence']['max_files']:
                raise service.DisciplineError('Доказательств — не больше %d файлов на запись'
                                              % catalog.FILE_KINDS['evidence']['max_files'])
            for upload in uploads:
                file_id = queries.insert_file(cursor, service._file_row(upload, 'evidence', ctx, record_id=record_id,
                                                                        now=now))
                queries.audit(cursor, ctx, 'upload_evidence', 'file', entity_id=file_id,
                              operator_id=record['operator_id'], department_id=record['department_id'],
                              record_id=record_id, changes={'file': upload['name'], 'sha256': upload['sha256']})
        return jsonify({'ok': True})

    @route('/records/<int:record_id>/training', methods=('POST',))
    def discipline_training(ctx, today, record_id, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            training_id = service.record_training(cursor, ctx, record, _payload(), now=now, today=today)
        return jsonify({'ok': True, 'training_id': training_id})

    @route('/records/<int:record_id>/measure', methods=('POST',))
    def discipline_measure(ctx, today, record_id, outbox, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            service.set_measure(cursor, ctx, record, _payload(), now=now, base_url=web_app_base_url, outbox=outbox)
        return jsonify({'ok': True})

    @route('/records/<int:record_id>/measure/approve', methods=('POST',))
    def discipline_measure_approve(ctx, today, record_id, outbox, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today, for_update=True)
            service.approve_measure(cursor, ctx, record, now=now, base_url=web_app_base_url, outbox=outbox)
        return jsonify({'ok': True})

    # ── Файлы ───────────────────────────────────────────────────────────────

    def _file_access(cursor, ctx, row, today):
        if row.get('record_id'):
            record = service.load_record(cursor, ctx, row['record_id'], today)
            if not access.can_see_internal(ctx, record) and row['kind'] != 'explanation':
                raise service.DisciplineError('Файл вне вашего доступа', 403)
            return record
        if row.get('act_id'):
            act = queries.get_act(cursor, row['act_id'])
            record = service.load_record(cursor, ctx, act['trigger_record_id'], today)
            if not access.can_see_internal(ctx, record):
                raise service.DisciplineError('Файл вне вашего доступа', 403)
            return record
        if not access.can_manage_department(ctx, row.get('department_id')):
            raise service.DisciplineError('Файл вне вашего доступа', 403)
        return None

    @route('/files/<int:file_id>')
    def discipline_file(ctx, today, file_id, **_kw):
        with db._get_cursor() as cursor:
            row = queries.get_file(cursor, file_id)
            if not row:
                raise service.DisciplineError('Файл не найден', 404)
            record = _file_access(cursor, ctx, row, today)
            queries.audit(cursor, ctx, 'view_file', 'file', entity_id=file_id,
                          operator_id=(record or {}).get('operator_id'),
                          department_id=(record or {}).get('department_id') or row.get('department_id'),
                          record_id=(record or {}).get('id'), changes={'file': row['file_name']})
        data = files.download(gcs, row['bucket'], row['blob_path'])
        if data is None:
            raise service.DisciplineError('Файл не найден в хранилище', 404)
        inline = request.args.get('inline') in ('1', 'true') and (row['content_type'] or '') in (
            'application/pdf', 'image/jpeg', 'image/png')
        response = send_file(BytesIO(data), mimetype=row['content_type'] or 'application/octet-stream',
                             as_attachment=not inline, download_name=row['file_name'])
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'private, no-store'
        if inline:
            response.headers['Content-Disposition'] = "inline; filename*=UTF-8''%s" % quote(row['file_name'])
        return response

    @route('/files/<int:file_id>/invalidate', methods=('POST',))
    def discipline_file_invalidate(ctx, today, file_id, outbox, **_kw):
        reason = str(_payload().get('reason') or '').strip()[:500]
        if len(reason) < 3:
            raise service.DisciplineError('Укажите, почему файл недействителен')
        with db._get_cursor() as cursor:
            row = queries.get_file(cursor, file_id)
            if not row:
                raise service.DisciplineError('Файл не найден', 404)
            record = _file_access(cursor, ctx, row, today)
            allowed = _can_invalidate(ctx, row, record) or bool(row.get('invalid_at') and record is not None)
            if row['kind'] == 'act_template':
                allowed = access.can_manage_department(ctx, row.get('department_id'))
            if not allowed:
                raise service.DisciplineError('Признать файл недействительным может руководитель или тот, '
                                              'кто его загрузил', 403)
            if record is not None:
                # Объяснительная может снова понадобиться — та же очередь на человека.
                queries.lock_operator(cursor, record['operator_id'])
                record = queries.get_record(cursor, record['id'], today, for_update=True)
            service.invalidate_file(cursor, ctx, row, record, reason, now=service.now_almaty(),
                                    base_url=web_app_base_url, outbox=outbox)
        return jsonify({'ok': True})

    # ── Акт ─────────────────────────────────────────────────────────────────

    @route('/acts/<int:act_id>', methods=('PATCH',))
    def discipline_act_update(ctx, today, act_id, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            act, record = service.load_act(cursor, ctx, act_id, today)
            service.update_act(cursor, ctx, act, record, _payload(), now=now)
        return jsonify({'ok': True})

    @route('/acts/<int:act_id>/file', methods=('POST',), upload='act')
    def discipline_act_file(ctx, today, act_id, uploaded, **_kw):
        now = service.now_almaty()
        upload = _take_files('act', field='file', required=True, uploaded=uploaded)[0]
        with db._get_cursor() as cursor:
            act, record = service.load_act(cursor, ctx, act_id, today)
            service.add_act_file(cursor, ctx, act, record, upload, now=now)
        return jsonify({'ok': True})

    @route('/acts/<int:act_id>/draft.docx')
    def discipline_act_draft(ctx, today, act_id, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            act, record = service.load_act(cursor, ctx, act_id, today)
            if not (access.manages_record(ctx, record) or access.supervises_record(ctx, record)):
                raise service.DisciplineError('Черновик акта — для СВ и руководителя', 403)
            if record.get('cancelled_at'):
                # Основания нет — документ описал бы другое нарушение (п. 7.2: «проверить основание акта»).
                raise service.DisciplineError('Запись-основание акта отменена — черновик не собирается. Проверьте акт',
                                              409, code='ACT_BASIS_CANCELLED')
            operator = queries.operator_profiles(cursor, [act['operator_id']], today).get(int(act['operator_id']))
            # Все записи периода акта: в документе только номер, дата, тип и отметка
            # об объяснительной — без описаний (описание — лишь у записи-основания,
            # а она в доступе у того, кто скачивает), поэтому и записи вне доступа
            # читающего в нём остаются (акт без них был бы неполным).
            members = [queries.get_record(cursor, rid, today) for rid in act['record_ids']]
            records = service.enrich(cursor, ctx, sorted([r for r in members if r and not r.get('cancelled_at')],
                                                         key=lambda r: (r['occurred_at'], r['id'])), now)
            templates = [f for f in queries.files_for(cursor, department_id=act.get('department_id'),
                                                      kind='act_template')
                         if not f.get('superseded_at') and not f.get('invalid_at')]
            composed_by = (queries.user_names(cursor, [act['composed_by']]).get(act['composed_by'])
                           if act.get('composed_by') else ctx.get('name'))
            context = None if templates else service.act_draft_context(cursor, ctx, act, record,
                                                                       settings_for=service.SettingsCache(cursor))
            queries.audit(cursor, ctx, 'act_docx', 'act', entity_id=act_id, operator_id=act['operator_id'],
                          department_id=act.get('department_id'), record_id=act['trigger_record_id'])
        template_bytes = files.download(gcs, templates[0]['bucket'], templates[0]['blob_path']) if templates else None
        position = 'Стажёр' if (operator or {}).get('role') == 'trainee' else 'Оператор'
        data = act_docx.build(act, operator or {}, records, template_bytes=template_bytes, position=position,
                              today=today, composed_by=composed_by, context=context, created=now)
        return send_file(BytesIO(data), mimetype=act_docx.DOCX_MIME, as_attachment=True,
                         download_name='Акт_%s.docx' % act['number'])

    @route('/records/<int:record_id>/explanation.docx')
    def discipline_explanation_blank(ctx, today, record_id, **_kw):
        """Бланк объяснительной по шаблону организации — тому, кто её загружает:
        самому оператору и тем, кто ведёт запись (п. 8.1)."""
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            record = service.load_record(cursor, ctx, record_id, today)
            if not access.can_upload_explanation(ctx, record):
                raise service.DisciplineError('Бланк объяснительной — тому, кто её загружает', 403)
            operator = queries.people(cursor, [record['operator_id']]).get(int(record['operator_id'])) or {}
            settings = (queries.get_settings(cursor, record['department_id'])
                        if record.get('department_id') is not None else {})
            queries.audit(cursor, ctx, 'explanation_docx', 'record', entity_id=record_id,
                          operator_id=record['operator_id'], department_id=record['department_id'],
                          record_id=record_id)
        data = explanation_docx.build(record, operator, addressee=settings.get('explanation_addressee'), created=now)
        return send_file(BytesIO(data), mimetype=act_docx.DOCX_MIME, as_attachment=True,
                         download_name='Объяснительная_%s.docx' % record['number'])

    # ── Карточка оператора ─────────────────────────────────────────────────

    @route('/operators/<int:operator_id>')
    def discipline_operator_card(ctx, today, operator_id, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            if access.is_operator(ctx) and operator_id != ctx['user_id']:
                raise service.DisciplineError('Карточка вне вашего доступа', 403)
            profile = queries.operator_profiles(cursor, [operator_id], today).get(operator_id)
            if not profile:
                raise service.DisciplineError('Сотрудник не найден', 404)
            records = queries.list_records(cursor, ctx, today, {'operator_id': operator_id,
                                                                'include_cancelled': True}, limit=5000)
            if not records and not access.operator_in_scope(ctx, profile) and not access.is_operator(ctx):
                raise service.DisciplineError('Карточка вне вашего доступа', 403)
            items = service.enrich(cursor, ctx, records, now)
            internal = not access.is_operator(ctx)
            settings = queries.get_settings(cursor, profile['department_id']) if profile.get('department_id') else \
                catalog.normalize_settings({})
            in_scope = access.operator_in_scope(ctx, profile)
            own_card = access.is_operator(ctx) and operator_id == ctx['user_id']
            history = queries.operator_histories(cursor, [operator_id]).get(operator_id, [])
            if not in_scope and not own_card:
                # Руководитель, у которого остались лишь старые записи человека,
                # считает период по тому, что ему видно.
                visible_ids = {r['id'] for r in records}
                history = [h for h in history if h['id'] in visible_ids]
            step = catalog.next_step(history, now, settings, settings_for=service.SettingsCache(cursor))
            control = queries.active_controls(cursor, [operator_id]).get(operator_id)
            if control and not (in_scope or own_card or access.can_manage_department(
                    ctx, control.get('department_id') or control.get('operator_department_id'))):
                control = None
            documents = []
            controls = []
            # Меры решением по акту и по контролю — вся история тем, кто ведёт
            # человека, и только свой отдел руководителю, у которого остались его
            # старые записи.
            decision_departments = (access.section_department_ids(ctx) if in_scope
                                    else access.managed_department_ids(ctx))
            decisions = (queries.decision_measures(cursor, sorted(decision_departments), operator_id=operator_id)
                         if internal else [])
            measures, _totals = report.report_measures([i for i in items if not i.get('cancelled_at')], decisions)
            if not internal:
                measures = [{key: row.get(key) for key in ('approved_at', 'basis', 'measure_title', 'measure_amount',
                                                           'measure_percent', 'record_id')} for row in measures]
            if internal:
                record_ids = [r['id'] for r in records]
                acts = {a['id']: a for a in queries.acts_by_record(cursor, record_ids).values()}
                documents = [_file_payload(f) for f in queries.files_for(cursor, record_ids=record_ids,
                                                                          act_ids=list(acts))]
                # Контроли человека — тем, кто его ведёт; руководителю отдела, где
                # лишь остались старые записи, — только контроли своего отдела.
                own_controls = [c for c in queries.operator_controls(cursor, operator_id)
                                if in_scope or access.can_manage_department(
                                    ctx, c.get('department_id') or c.get('operator_department_id'))]
                controls = service.control_rows(cursor, ctx, own_controls, today)
                queries.audit(cursor, ctx, 'view_card', 'card', entity_id=operator_id, operator_id=operator_id,
                              department_id=profile.get('department_id'))
        photo = None
        if avatar_url and profile.get('avatar_bucket') and profile.get('avatar_blob_path'):
            try:
                photo = avatar_url(profile['avatar_bucket'], profile['avatar_blob_path'])
            except Exception:  # noqa: BLE001
                photo = None
        return jsonify({
            'operator': {
                'id': profile['id'], 'name': profile['name'], 'photo': photo,
                'role': profile.get('role'), 'department_name': profile.get('department_name'),
                'group_name': profile.get('group_name'), 'direction_name': profile.get('direction_name'),
                'supervisor_name': profile.get('supervisor_name'), 'hire_date': _iso(profile.get('hire_date')),
                'status': profile.get('status'),
            },
            'period': {'count': step['count'], 'kind': settings['period_kind'], 'days': settings['period_days'],
                       'next_title': catalog.step_title(step['action'], step['explanation_no'])},
            'control': {'end_date': _iso(control['end_date']), 'reason': control['reason'],
                        'source_label': catalog.CONTROL_SOURCE_LABELS.get(control['source'])} if control else None,
            'records': _public(items, ctx),
            'documents': documents,
            'controls': controls,
            'measures': [{key: _iso(value) for key, value in row.items()} for row in measures],
        })

    # ── Особый контроль ────────────────────────────────────────────────────

    @route('/controls')
    def discipline_controls(ctx, today, **_kw):
        state = request.args.get('state') or 'active'
        if state not in ('active', 'closed', 'all'):
            state = 'active'
        with db._get_cursor() as cursor:
            rows = service.control_rows(cursor, ctx, service.visible_controls(cursor, ctx, today, state=state), today)
            measures = []
            if access.is_manager(ctx):
                measures = [_measure_payload(m) for m in
                            queries.list_measures(cursor, sorted(access.managed_department_ids(ctx)))]
        return jsonify({'items': rows, 'measures': measures})

    @route('/controls', methods=('POST',), manager=True)
    def discipline_control_create(ctx, today, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            control_id = service.create_control(cursor, ctx, _payload(), now=now, today=today)
        return jsonify({'ok': True, 'id': control_id}), 201

    @route('/controls/<int:control_id>/decide', methods=('POST',), manager=True)
    def discipline_control_decide(ctx, today, control_id, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            service.decide_control(cursor, ctx, control_id, _payload(), now=now, today=today)
            queries.mark_seen(cursor, ctx['user_id'], control_id=control_id)
        return jsonify({'ok': True})

    # ── Настройки и справочники ────────────────────────────────────────────

    def _type_payload(row):
        return {key: row.get(key) for key in ('id', 'department_id', 'title', 'severity', 'needs_explanation',
                                               'needs_evidence', 'weight', 'is_other', 'is_archived')}

    def _measure_payload(row):
        return {key: row.get(key) for key in ('id', 'department_id', 'title', 'needs_approval', 'finance',
                                               'value_kind', 'is_archived')}

    def _settings_department(ctx, raw):
        try:
            department_id = int(raw)
        except (TypeError, ValueError):
            raise service.DisciplineError('Не выбран отдел') from None
        if not access.can_manage_department(ctx, department_id):
            raise service.DisciplineError('Настройки отдела меняет его руководитель', 403)
        return department_id

    @route('/settings', manager=True)
    def discipline_settings(ctx, today, **_kw):
        department_id = _settings_department(ctx, request.args.get('department_id'))
        with db._get_cursor() as cursor:
            queries.ensure_department_defaults(cursor, department_id)
            settings = queries.get_settings(cursor, department_id)
            types = queries.list_types(cursor, [department_id], include_archived=True)
            measures = queries.list_measures(cursor, [department_id], include_archived=True)
            templates = [f for f in queries.files_for(cursor, department_id=department_id, kind='act_template')
                         if not f.get('superseded_at') and not f.get('invalid_at')]
        return jsonify({
            'department_id': department_id,
            'settings': settings,
            'types': [_type_payload(t) for t in types],
            'measures': [_measure_payload(m) for m in measures],
            'template': _file_payload(templates[0]) if templates else None,
            'catalog': {
                'severities': [{'value': k, 'label': v} for k, v in catalog.SEVERITIES],
                'finance': [{'value': k, 'label': v} for k, v in catalog.FINANCE_KINDS],
                'value_kinds': [{'value': k, 'label': v} for k, v in catalog.VALUE_KINDS],
                'steps': [{'value': k, 'label': catalog.STEP_LABELS[k]} for k in catalog.STEP_ACTIONS],
                'ladders': {'max': catalog.MAX_LADDERS, 'name_max': catalog.LADDER_NAME_MAX,
                            'max_steps': catalog.MAX_LADDER},
                'addressee': {'max_lines': catalog.ADDRESSEE_MAX_LINES, 'max_chars': catalog.ADDRESSEE_MAX_CHARS},
                'period_kinds': [{'value': k, 'label': v} for k, v in catalog.PERIOD_KINDS],
                'events': [{'value': code, 'label': label, 'who': who} for code, label, who, _d in
                           catalog.NOTIFY_EVENTS],
                'limits': catalog.LIMITS,
            },
        })

    @route('/settings', methods=('PUT',), manager=True)
    def discipline_settings_save(ctx, today, **_kw):
        payload = _payload()
        department_id = _settings_department(ctx, payload.get('department_id'))
        with db._get_cursor() as cursor:
            before = queries.get_settings(cursor, department_id)
            after = catalog.normalize_settings(payload.get('settings') or {}, base=before)
            diff = {key: [before.get(key), after.get(key)] for key in after if before.get(key) != after.get(key)}
            # В журнал — действующая лестница по названию, а не по id; правка лестниц
            # видна целиком в «Лестницах отдела», ступени действующей не дублируем.
            if 'active_ladder' in diff:
                diff['active_ladder'] = [(catalog.active_ladder(before) or {}).get('name'),
                                         (catalog.active_ladder(after) or {}).get('name')]
            if 'ladders' in diff:
                diff.pop('ladder', None)
            if diff:
                service.save_counting_change(cursor, department_id, before, after, now=service.now_almaty(),
                                             user_id=ctx['user_id'])
                queries.save_settings(cursor, department_id, after, ctx['user_id'])
                queries.audit(cursor, ctx, 'settings_update', 'settings', entity_id=department_id,
                              department_id=department_id, changes=diff)
        return jsonify({'ok': True, 'settings': after})

    def _bool(value):
        return value in (True, 1, '1', 'true', 'on', 'yes')

    @route('/types', methods=('POST',), manager=True)
    def discipline_type_save(ctx, today, **_kw):
        payload = _payload()
        department_id = _settings_department(ctx, payload.get('department_id'))
        title = str(payload.get('title') or '').strip()[:200]
        if len(title) < 2:
            raise service.DisciplineError('Укажите название типа')
        severity = str(payload.get('severity') or 'medium')
        if severity not in catalog.SEVERITY_LABELS:
            raise service.DisciplineError('Неизвестная тяжесть')
        try:
            weight = int(payload.get('weight') or 1)
        except (TypeError, ValueError):
            weight = 0
        if not 1 <= weight <= catalog.MAX_WEIGHT:
            raise service.DisciplineError('Вес — от 1 до %d' % catalog.MAX_WEIGHT)
        data = {'title': title, 'severity': severity, 'needs_explanation': _bool(payload.get('needs_explanation')),
                'needs_evidence': _bool(payload.get('needs_evidence')), 'weight': weight,
                'is_other': _bool(payload.get('is_other')), 'is_archived': _bool(payload.get('is_archived'))}
        type_id = payload.get('id')
        with db._get_cursor() as cursor:
            before = queries.get_type(cursor, int(type_id)) if type_id else None
            if type_id and (not before or int(before['department_id']) != department_id):
                raise service.DisciplineError('Тип не найден', 404)
            try:
                cursor.execute('SAVEPOINT discipline_type')
                saved = queries.save_type(cursor, department_id, data, ctx['user_id'], type_id=type_id)
                cursor.execute('RELEASE SAVEPOINT discipline_type')
            except Exception as exc:  # noqa: BLE001
                cursor.execute('ROLLBACK TO SAVEPOINT discipline_type')
                if 'ux_discipline_types_title' in str(exc):
                    raise service.DisciplineError('Тип с таким названием уже есть') from None
                raise
            queries.audit(cursor, ctx, 'type_save', 'type', entity_id=saved, department_id=department_id,
                          changes={key: [(before or {}).get(key), value] for key, value in data.items()
                                   if (before or {}).get(key) != value})
        return jsonify({'ok': True, 'id': saved})

    @route('/measures', methods=('POST',), manager=True)
    def discipline_measure_save(ctx, today, **_kw):
        payload = _payload()
        department_id = _settings_department(ctx, payload.get('department_id'))
        title = str(payload.get('title') or '').strip()[:200]
        if len(title) < 2:
            raise service.DisciplineError('Укажите название меры')
        finance = str(payload.get('finance') or 'none')
        value_kind = str(payload.get('value_kind') or 'none')
        if finance not in catalog.FINANCE_LABELS or value_kind not in catalog.VALUE_LABELS:
            raise service.DisciplineError('Неизвестный финансовый эффект')
        if finance == 'none' and value_kind != 'none':
            raise service.DisciplineError('Мера без финансового эффекта не выражается суммой или процентом')
        data = {'title': title, 'needs_approval': _bool(payload.get('needs_approval')), 'finance': finance,
                'value_kind': value_kind, 'is_archived': _bool(payload.get('is_archived'))}
        measure_id = payload.get('id')
        with db._get_cursor() as cursor:
            before = queries.get_measure(cursor, int(measure_id)) if measure_id else None
            if measure_id and (not before or int(before['department_id']) != department_id):
                raise service.DisciplineError('Мера не найдена', 404)
            try:
                cursor.execute('SAVEPOINT discipline_measure')
                saved = queries.save_measure(cursor, department_id, data, ctx['user_id'], measure_id=measure_id)
                cursor.execute('RELEASE SAVEPOINT discipline_measure')
            except Exception as exc:  # noqa: BLE001
                cursor.execute('ROLLBACK TO SAVEPOINT discipline_measure')
                if 'ux_discipline_measures_title' in str(exc):
                    raise service.DisciplineError('Мера с таким названием уже есть') from None
                raise
            queries.audit(cursor, ctx, 'measure_save', 'measure_dict', entity_id=saved, department_id=department_id,
                          changes={key: [(before or {}).get(key), value] for key, value in data.items()
                                   if (before or {}).get(key) != value})
        return jsonify({'ok': True, 'id': saved})

    @route('/settings/template', methods=('POST',), manager=True, upload='act_template')
    def discipline_template_upload(ctx, today, uploaded, **_kw):
        now = service.now_almaty()
        department_id = _settings_department(ctx, request.form.get('department_id'))
        upload = _take_files('act_template', field='file', required=True, uploaded=uploaded)[0]
        with db._get_cursor() as cursor:
            queries.supersede_files(cursor, kind='act_template', department_id=department_id)
            file_id = queries.insert_file(cursor, service._file_row(upload, 'act_template', ctx,
                                                                    department_id=department_id, now=now))
            queries.audit(cursor, ctx, 'template_upload', 'file', entity_id=file_id, department_id=department_id,
                          changes={'file': upload['name']})
        return jsonify({'ok': True, 'file_id': file_id})

    # ── Отчёты и журнал действий ───────────────────────────────────────────

    def _report_period(today):
        try:
            date_from = datetime.strptime((request.args.get('date_from') or '')[:10], '%Y-%m-%d')
        except ValueError:
            date_from = datetime.combine(today.replace(day=1), datetime.min.time())
        try:
            date_to = datetime.strptime((request.args.get('date_to') or '')[:10], '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            date_to = datetime.combine(today, datetime.min.time()) + timedelta(days=1)
        if date_to <= date_from:
            raise service.DisciplineError('Конец периода раньше начала')
        if (date_to - date_from).days > 800:
            raise service.DisciplineError('Период отчёта — не больше двух лет')
        return date_from, date_to

    def _report_data(cursor, ctx, kind, today, now):
        date_from, date_to = _report_period(today)
        department_id = _dept_param(ctx)
        result = {'period': (date_from, date_to)}
        items = []
        if kind in ('operators', 'types', 'supervisors', 'groups'):
            records = queries.list_records(cursor, ctx, today, {'date_from': date_from, 'date_to': date_to,
                                                                'department_id': department_id}, limit=20000)
            items = service.enrich(cursor, ctx, records, now)
        if kind == 'operators':
            result['rows'] = report.report_operators(items)
            result['columns'] = report.OPERATORS_COLUMNS
        elif kind == 'types':
            result['rows'], result['columns'] = report.report_types([service.public_record(i, ctx) for i in items])
        elif kind == 'supervisors':
            result['rows'] = report.report_supervisors(items)
            result['columns'] = report.SUPERVISORS_COLUMNS
        elif kind == 'groups':
            result['rows'] = report.report_groups(items)
            result['columns'] = report.GROUPS_COLUMNS
        elif kind == 'measures':
            approved = queries.list_records(cursor, ctx, today, {'approved_from': date_from, 'approved_to': date_to,
                                                                 'department_id': department_id}, limit=20000)
            departments = [department_id] if department_id else sorted(access.managed_department_ids(ctx))
            decisions = queries.decision_measures(cursor, departments, date_from=date_from, date_to=date_to)
            rows, totals = report.report_measures(service.enrich(cursor, ctx, approved, now), decisions)
            result['rows'] = rows
            result['columns'] = report.MEASURES_COLUMNS
            result['totals'] = totals
            result['totals_columns'] = report.MEASURE_TOTALS_COLUMNS
        elif kind == 'controls':
            controls = service.control_rows(cursor, ctx, service.visible_controls(
                cursor, ctx, today, state='all', started_from=date_from.date(), started_to=date_to.date()), today)
            rows, summary = report.report_controls(controls, date_from.strftime('%Y-%m-%d'),
                                                   date_to.strftime('%Y-%m-%d'))
            result['rows'] = rows
            result['columns'] = report.CONTROLS_COLUMNS
            result['summary'] = {key: (dict(value) if hasattr(value, 'items') else value)
                                 for key, value in summary.items()}
        else:
            raise service.DisciplineError('Неизвестный отчёт', 404)
        return result

    @route('/reports/<kind>', manager=True)
    def discipline_report(ctx, today, kind, **_kw):
        now = service.now_almaty()
        with db._get_cursor() as cursor:
            data = _report_data(cursor, ctx, kind, today, now)
            if request.args.get('format') == 'xlsx':
                _audit_export(cursor, ctx, 'report', {'report': report.REPORTS.get(kind, kind)})
        date_from, date_to = data['period']
        title = report.REPORTS.get(kind, kind)
        if request.args.get('format') == 'xlsx':
            sheets = [(title, data['columns'], data['rows'])]
            if data.get('totals'):
                sheets.append(('Итог по мерам', data['totals_columns'], data['totals']))
            context = [('Собрано', now.strftime('%d.%m.%Y %H:%M')), ('Кто выгрузил', ctx.get('name') or ''),
                       ('Период', '%s — %s' % (date_from.strftime('%d.%m.%Y'),
                                               (date_to - timedelta(days=1)).strftime('%d.%m.%Y'))),
                       ('Строк', len(data['rows']))]
            if data.get('summary'):
                summary = data['summary']
                context += [('Поставлено на контроль', summary['started']),
                            ('Прошли срок без нарушений', summary['clean']),
                            ('Дошли до расторжения', summary['termination']),
                            ('Ещё на контроле', summary['active'])]
            return _xlsx(report.workbook(title, context, sheets),
                         'Дисциплина_%s_%s.xlsx' % (kind, now.strftime('%Y-%m-%d')))
        return jsonify({
            'title': title,
            'period': {'from': date_from.strftime('%Y-%m-%d'),
                       'to': (date_to - timedelta(days=1)).strftime('%Y-%m-%d')},
            'columns': [{'title': c[0], 'key': c[1]} for c in data['columns']],
            'rows': [{k: _iso(v) for k, v in row.items()} for row in data['rows']],
            'totals': data.get('totals'),
            'summary': data.get('summary'),
        })

    @route('/audit', manager=True)
    def discipline_audit(ctx, today, **_kw):
        now = service.now_almaty()
        date_from, date_to = _report_period(today)
        managed = sorted(access.managed_department_ids(ctx))
        department_id = _dept_param(ctx)
        try:
            page = max(1, int(request.args.get('page') or 1))
        except ValueError:
            page = 1
        # Размер страницы задаёт экран «Отчётов» (по 10 строк, чтобы журнал не уходил
        # вниз); без него — прежние 50. Границы — чтобы запросом не выкачать журнал целиком.
        try:
            page_size = min(AUDIT_PAGE_MAX, max(AUDIT_PAGE_MIN, int(request.args.get('page_size') or 50)))
        except ValueError:
            page_size = 50
        export = request.args.get('format') == 'xlsx'
        limit = 50000 if export else page_size
        with db._get_cursor() as cursor:
            total, rows = queries.list_audit(cursor, [department_id] if department_id else managed,
                                             date_from=date_from, date_to=date_to,
                                             operator_id=request.args.get('operator_id') or None,
                                             limit=limit, offset=0 if export else (page - 1) * limit)
            if export:
                _audit_export(cursor, ctx, 'audit', {'rows': len(rows)})
        items = [{
            'id': row['id'], 'created_at': _iso(row['created_at']), 'actor_name': row['actor_name'],
            'action': row['action'], 'action_label': report.audit_label(row['action']),
            'operator_name': row.get('operator_name'), 'entity': row['entity'], 'entity_id': row['entity_id'],
            'record_id': row.get('record_id'), 'changes_text': report.changes_text(row.get('changes')),
        } for row in rows]
        if export:
            data = report.workbook('Журнал действий', [
                ('Собрано', now.strftime('%d.%m.%Y %H:%M')), ('Кто выгрузил', ctx.get('name') or ''),
                ('Период', '%s — %s' % (date_from.strftime('%d.%m.%Y'),
                                        (date_to - timedelta(days=1)).strftime('%d.%m.%Y'))),
                ('Как читать', 'Журнал только дописывается: строки не правятся и не удаляются (ТЗ п. 14).'),
            ], [('Журнал действий', report.AUDIT_COLUMNS, items)])
            return _xlsx(data, 'Дисциплина_журнал_действий_%s.xlsx' % now.strftime('%Y-%m-%d'))
        return jsonify({'items': items, 'total': total, 'page': page, 'page_size': limit})

    return bp
