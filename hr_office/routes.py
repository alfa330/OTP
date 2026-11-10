# -*- coding: utf-8 -*-
"""HTTP-ручки разделов «Прием», «Табель учета рабочего времени» и «АВР» (Blueprint /api/hr_office). ТЗ HR «iCore 4».

    GET    /context                       что открыто, проекты, справочники
    GET    /companies                     справочник компаний (HR)
    POST   /companies                     завести компанию
    PUT    /companies/<id>                изменить
    GET    /people                        «Прием»: люди и чего не хватает (?q&project&department_id)
    GET    /people/<id>                   карточка оформления
    PUT    /people/<id>                   сохранить личные данные и договор
    GET    /people/<id>/documents         пакет документов (?kinds=contract,nda)
    GET    /people/<id>/status            текущий статус и пункты выбора
    POST   /people/<id>/status            изменить статус {status, start, end, period_id}
    GET    /status/lookup                 статус по ФИО (для «Отметок»; ?name)
    GET    /templates                     шаблоны документов
    GET    /templates/<kind>              скачать шаблон
    POST   /templates/<kind>              загрузить свой (multipart file)
    DELETE /templates/<kind>              вернуть стандартный
    GET    /timesheet                     табель (?month&project&until&user_id…)
    PUT    /timesheet/cell                правка ячейки {user_id, day, field, value}
    PUT    /timesheet/note                комментарий {user_id, month, note}
    GET    /timesheet/export              Excel
    POST   /timesheet/close               закрыть месяц {month, project}
    DELETE /timesheet/close               открыть заново (?month&project)
    GET    /acts/people                   «АВР»: люди в моём охвате
    POST   /acts/export                   собрать АВР {period_from, period_to, act_date, items}
    POST   /import/preview                таблица HR → сверка с iCORE: что поменяется (multipart file, project)
    POST   /import/apply                  записать подтверждённое (строки с confirm: {поле: «было»})
    GET    /journal                       журнал раздела (?section=intake|timesheet|acts&q&offset)
"""

import json
import logging
from datetime import date, datetime
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import documents, journal, queries, rules, schema, service, timesheet
from .service import HrError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
COMPANY_LABELS = {
    'name': 'название', 'kind': 'форма', 'bin': 'ИИН/БИН', 'legal_address': 'адрес', 'contacts': 'контакты',
    'bank_name': 'банк', 'iik': 'счёт', 'bik': 'БИК', 'kbe': 'КБе', 'in_person': '«в лице»',
    'signer_name': 'подписант', 'signer_position': 'должность подписанта', 'city': 'город', 'is_active': 'действует',
}
JOURNAL_SECTIONS = {'intake': 'hiring', 'timesheet': 'timesheet', 'acts': 'acts'}
DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
ZIP_MIME = 'application/zip'


def today_almaty():
    try:
        return datetime.now(ZoneInfo('Asia/Almaty')).date()
    except Exception:  # noqa: BLE001
        return date.today()


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def build_hr_office_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                              viewer_context, update_user_field):
    """viewer_context(requester_id, requester) -> {'user_id', 'role', 'is_hr', 'is_super', 'is_admin',
    'is_supervisor', 'department_id', 'headed_department_ids'}.
    update_user_field(user_id, field, value, changed_by) — тот же путь, что у карточки (с историей)."""
    bp = Blueprint('hr_office', __name__, url_prefix='/api/hr_office')
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
                    requester_id, requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({'error': message}), status
                    ctx = viewer_context(requester_id, requester)
                    # Пилот (rules.PILOT_USER_IDS): пока идёт проверка, ручки «Кадров» отвечают
                    # только суперадминам и учёткам списка — для остальных раздела ещё нет.
                    if not rules.pilot_allows(ctx['user_id'], ctx['role']):
                        return jsonify({'error': 'Раздел «Кадры» пока открыт только для проверки',
                                        'code': 'HR_PILOT'}), 403
                    return handler(*args, ctx=ctx, today=today_almaty(), **kwargs)
                except (HrError, timesheet.TimesheetError, documents.DocumentError) as exc:
                    payload = {'error': exc.message, 'code': exc.code}
                    if getattr(exc, 'fields', None):
                        payload['fields'] = exc.fields
                    return jsonify(payload), exc.status
                except Exception:  # noqa: BLE001
                    logging.exception('hr_office: %s упал', handler.__name__)
                    return jsonify({'error': 'Внутренняя ошибка сервера'}), 500
            return wrapper
        return decorator

    def _body():
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}

    def _file(payload, mimetype, name):
        # Портал и API на разных адресах: без этого заголовка браузер не отдаёт фронту имя
        # файла, и одиночный .docx сохранялся бы под запасным именем «документы.zip».
        response = send_file(BytesIO(payload), mimetype=mimetype, as_attachment=True, download_name=name)
        response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response

    def _apply_user_fields(updates, actor_id):
        """Поля карточки «Учета сотрудников» — после записи своих таблиц, тем же путём,
        что правка карточки: с историей и зеркалом в user_hr_profiles."""
        failed = []
        for user_id, fields in updates.items():
            for field, value in fields.items():
                try:
                    update_user_field(int(user_id), field, value, actor_id)
                except Exception:  # noqa: BLE001
                    logging.exception('hr_office: поле %s сотрудника %s не записалось', field, user_id)
                    failed.append(user_id)
        return failed

    # ── Контекст ────────────────────────────────────────────────────────────

    @route('/context')
    def hr_context(*, ctx, today):
        caps = service.capabilities(ctx)
        if not any(caps.values()):
            raise HrError('Раздел вам не открыт', status=403, code='HR_FORBIDDEN')
        payload = {
            'capabilities': caps,
            'today': today.isoformat(),
            'projects': list(rules.PROJECTS),
            'employment_types': [{'value': key, 'label': rules.EMPLOYMENT_TITLES[key],
                                  'short': rules.EMPLOYMENT_SHORT[key]} for key in rules.EMPLOYMENT_TYPES],
            'schedules': list(rules.SCHEDULE_PATTERNS),
            'legend': [{'code': code, 'text': text} for code, text in rules.LEGEND],
            'doc_kinds': [{'kind': kind, 'title': title} for kind, title in documents.DOC_KINDS],
            'doc_kinds_by_type': {str(kind or ''): list(kinds) for kind, kinds in documents.KINDS_BY_EMPLOYMENT.items()},
            'markers': list(documents.KNOWN_MARKERS),
        }
        with db._get_cursor() as cursor:
            if caps['hiring'] or caps['acts']:
                payload['departments'] = _departments(cursor)
            if caps['companies']:
                payload['companies'] = [service.company_payload(company)
                                        for company in queries.list_companies(cursor)]
        default_from, default_to = rules.default_act_period(today)
        previous_from, previous_to = rules.previous_month_period(today)
        payload['act_periods'] = {
            'current': {'from': default_from.isoformat(), 'to': default_to.isoformat()},
            'previous': {'from': previous_from.isoformat(), 'to': previous_to.isoformat()},
        }
        return jsonify(payload), 200

    def _departments(cursor):
        cursor.execute("SELECT id, name, code FROM departments WHERE COALESCE(is_active, TRUE) ORDER BY name")
        return [{'id': row[0], 'name': row[1], 'code': row[2]} for row in cursor.fetchall()]

    # ── Компании ────────────────────────────────────────────────────────────

    @route('/companies', methods=('GET', 'POST'))
    def hr_companies(*, ctx, today):
        service.require(ctx, 'companies')
        with db._get_cursor() as cursor:
            if request.method == 'GET':
                return jsonify({'companies': [service.company_payload(company)
                                              for company in queries.list_companies(cursor)]}), 200
            fields = service.clean_company(_body())
            if queries.company_name_taken(cursor, fields['name']):
                raise HrError('Компания с таким названием уже есть', fields={'name': 'Уже есть'},
                              code='HR_COMPANY_DUPLICATE')
            company_id = queries.save_company(cursor, None, fields, ctx['user_id'])
            company = queries.get_company(cursor, company_id)
            queries.audit(cursor, section='intake', action='company', actor_id=ctx['user_id'],
                          summary=f"Компания {rules.company_display_name(company)} заведена")
        return jsonify({'company': service.company_payload(company)}), 200

    @route('/companies/<int:company_id>', methods=('PUT',))
    def hr_company_update(company_id, *, ctx, today):
        service.require(ctx, 'companies')
        fields = service.clean_company(_body())
        with db._get_cursor() as cursor:
            before = queries.get_company(cursor, company_id)
            if not before:
                raise HrError('Компания не найдена', status=404, code='HR_NOT_FOUND')
            if queries.company_name_taken(cursor, fields['name'], exclude_id=company_id):
                raise HrError('Компания с таким названием уже есть', fields={'name': 'Уже есть'},
                              code='HR_COMPANY_DUPLICATE')
            queries.save_company(cursor, company_id, fields, ctx['user_id'])
            company = queries.get_company(cursor, company_id)
            changed = [COMPANY_LABELS.get(key, key) for key in queries.COMPANY_WRITE
                       if str(before.get(key) or '') != str(company.get(key) or '')]
            if changed:
                queries.audit(cursor, section='intake', action='company', actor_id=ctx['user_id'],
                              summary=f"Компания {rules.company_display_name(company)}: изменены {', '.join(changed)}")
        return jsonify({'company': service.company_payload(company)}), 200

    # ── Люди («Прием») ──────────────────────────────────────────────────────

    @route('/people')
    def hr_people(*, ctx, today):
        service.require(ctx, 'hiring')
        with db._get_cursor() as cursor:
            people = service.list_people(cursor, ctx, query=request.args.get('q', ''),
                                         project=request.args.get('project', ''),
                                         department_id=request.args.get('department_id') or None)
        return jsonify({'people': people}), 200

    @route('/people/<int:user_id>', methods=('GET', 'PUT'))
    def hr_person(user_id, *, ctx, today):
        warnings, failed = [], []
        if request.method == 'PUT':
            with db._get_cursor() as cursor:
                user_fields, warnings = service.save_person(cursor, ctx, user_id, _body(), today)
            failed = _apply_user_fields({user_id: user_fields} if user_fields else {}, ctx['user_id'])
        with db._get_cursor() as cursor:
            card = service.person_card(cursor, ctx, user_id, today)
        card['warnings'] = warnings
        if failed:
            card['warnings'].append('Карточка «Учета сотрудников» обновилась не полностью — повторите сохранение')
        return jsonify(card), 200

    @route('/people/<int:user_id>/documents')
    def hr_person_documents(user_id, *, ctx, today):
        service.require(ctx, 'hiring')
        with db._get_cursor() as cursor:
            card = service.person_card(cursor, ctx, user_id, today)
            person = queries.get_person(cursor, user_id)
            allowed = [item['kind'] for item in card['documents']]
            wanted = [kind for kind in (request.args.get('kinds') or '').split(',') if kind] or allowed
            kinds = [kind for kind in wanted if kind in allowed]
            if not kinds:
                raise HrError('Для этого вида оформления таких документов нет', code='HR_DOCS_NONE')
            templates = []
            for kind in kinds:
                custom = queries.get_template(cursor, kind)
                templates.append((kind, custom['content'] if custom else documents.default_template(kind)))
            company = queries.get_company(cursor, card['company_id']) if card.get('company_id') else None
        contract = {'number': card['contract']['number'], 'signed_on': rules.parse_day(card['contract']['signed_on']),
                    'liability_number': card['contract']['liability_number'],
                    'amount': card['contract']['amount'], 'job_title': card['contract']['job_title']}
        name, payload = documents.documents_bundle(person, company, contract, templates, today)
        with db._get_cursor() as cursor:
            queries.audit(cursor, section='intake', action='documents', user_id=user_id, actor_id=ctx['user_id'],
                          summary='Скачаны документы: ' + ', '.join(documents.DOC_TITLES[kind] for kind in kinds))
        return _file(payload, DOCX_MIME if name.endswith('.docx') else ZIP_MIME, name)

    @route('/people/<int:user_id>/status', methods=('GET', 'POST'))
    def hr_person_status(user_id, *, ctx, today):
        if request.method == 'POST':
            with db._get_cursor() as cursor:
                service.change_status(cursor, db, ctx, user_id, _body(), today)
        with db._get_cursor() as cursor:
            return jsonify(service.person_status(cursor, ctx, user_id, today)), 200

    @route('/status/lookup')
    def hr_status_lookup(*, ctx, today):
        with db._get_cursor() as cursor:
            found = service.status_lookup(cursor, ctx, request.args.get('name', ''), today,
                                          employee_id=request.args.get('employee_id', ''))
        return jsonify({'people': found}), 200

    # ── Шаблоны ─────────────────────────────────────────────────────────────

    @route('/templates')
    def hr_templates(*, ctx, today):
        service.require(ctx, 'hiring')
        with db._get_cursor() as cursor:
            custom = queries.list_templates(cursor)
            names = queries.user_names(cursor, [item['uploaded_by'] for item in custom.values()])
        out = []
        for kind, title in documents.DOC_KINDS:
            item = custom.get(kind)
            out.append({'kind': kind, 'title': title, 'custom': bool(item),
                        'file_name': item['file_name'] if item else f'{title}.docx',
                        'uploaded_at': _iso(item['uploaded_at']) if item else None,
                        'uploaded_by': names.get(item['uploaded_by']) if item else None})
        return jsonify({'templates': out, 'markers': list(documents.KNOWN_MARKERS)}), 200

    @route('/templates/<kind>', methods=('GET', 'POST', 'DELETE'))
    def hr_template(kind, *, ctx, today):
        service.require(ctx, 'hiring')
        if kind not in documents.DOC_TITLES:
            raise HrError('Нет такого шаблона', status=404, code='HR_NOT_FOUND')
        with db._get_cursor() as cursor:
            if request.method == 'GET':
                custom = queries.get_template(cursor, kind)
                content = custom['content'] if custom else documents.default_template(kind)
                return _file(content, DOCX_MIME, f'Шаблон — {documents.DOC_TITLES[kind]}.docx')
            if request.method == 'DELETE':
                if queries.delete_template(cursor, kind):
                    queries.audit(cursor, section='intake', action='template', actor_id=ctx['user_id'],
                                  summary=f'Шаблон «{documents.DOC_TITLES[kind]}»: возвращён стандартный')
                return jsonify({'ok': True}), 200
            upload = request.files.get('file')
            if not upload:
                raise HrError('Приложите файл .docx', code='HR_TEMPLATE_NO_FILE')
            content = upload.read(documents.MAX_TEMPLATE_BYTES + 1)
            report = documents.check_template(content, upload.filename)
            queries.save_template(cursor, kind, upload.filename[:200], content, ctx['user_id'])
            queries.audit(cursor, section='intake', action='template', actor_id=ctx['user_id'],
                          summary=f'Шаблон «{documents.DOC_TITLES[kind]}»: загружен свой — {upload.filename[:120]}')
        return jsonify({'ok': True, **report}), 200

    # ── Табель ──────────────────────────────────────────────────────────────

    def _timesheet_args():
        month = request.args.get('month') or ''
        project = request.args.get('project') or ''
        until = rules.parse_day(request.args.get('until')) if request.args.get('until') else None
        user_ids = []
        for value in request.args.getlist('user_id'):
            try:
                user_ids.append(int(value))
            except (TypeError, ValueError):
                continue
        return month, project, until, user_ids

    def _model(*, today, month, project, until, user_ids):
        bounds = rules.parse_month(month)
        if not bounds:
            raise HrError('Месяц нужен в виде ГГГГ-ММ', code='HR_TIMESHEET_BAD_MONTH')
        if project:
            with db._get_cursor() as cursor:
                closure = queries.get_closure(cursor, project, bounds[0])
                names = queries.user_names(cursor, [closure['closed_by']]) if closure else {}
            if closure:
                # Закрытый месяц — только из снимка, и выгрузка по выбранным людям тоже: иначе
                # файл для бухгалтерии разошёлся бы с закрытым табелем.
                model = closure['snapshot'] if isinstance(closure['snapshot'], dict) else json.loads(closure['snapshot'])
                model['closed'] = {'by': names.get(closure['closed_by']), 'at': _iso(closure['closed_at'])}
                if user_ids:
                    wanted = set(user_ids)
                    model['groups'] = [dict(group, people=[person for person in group['people']
                                                           if person['user_id'] in wanted])
                                       for group in model['groups']]
                    model['groups'] = [group for group in model['groups'] if group['people']]
                return model
        return timesheet.collect(db, month=month, project=project, until=until, user_ids=user_ids or None,
                                 today=today)

    @route('/timesheet')
    def hr_timesheet(*, ctx, today):
        service.require(ctx, 'timesheet')
        month, project, until, user_ids = _timesheet_args()
        return jsonify(_model(today=today, month=month, project=project, until=until, user_ids=user_ids)), 200

    @route('/timesheet/export')
    def hr_timesheet_export(*, ctx, today):
        service.require(ctx, 'timesheet')
        month, project, until, user_ids = _timesheet_args()
        model = _model(today=today, month=month, project=project, until=until, user_ids=user_ids)
        if not any(group['people'] for group in model['groups']):
            raise HrError('В табель никто не попал', status=404, code='HR_TIMESHEET_EMPTY')
        return _file(timesheet.workbook(model), XLSX_MIME, timesheet.file_name(model))

    def _ensure_open(cursor, user_id, day):
        """Правки закрытого месяца запрещены: табель уже ушёл в бухгалтерию."""
        person = queries.get_person(cursor, user_id)
        if not person:
            raise HrError('Сотрудник не найден', status=404, code='HR_NOT_FOUND')
        month_start = day.replace(day=1)
        if queries.get_closure(cursor, person['project_value'], month_start):
            raise HrError('Табель этого месяца закрыт — откройте его заново, чтобы править',
                          status=409, code='HR_TIMESHEET_CLOSED')
        return person

    @route('/timesheet/cell', methods=('PUT',))
    def hr_timesheet_cell(*, ctx, today):
        service.require(ctx, 'timesheet')
        body = _body()
        day = rules.parse_day(body.get('day'))
        field = body.get('field') if body.get('field') in ('value', 'minutes') else None
        try:
            user_id = int(body.get('user_id'))
        except (TypeError, ValueError):
            user_id = None
        if not day or not field or not user_id:
            raise HrError('Нужны сотрудник, день и поле', code='HR_TIMESHEET_BAD_CELL')
        raw = body.get('value')
        if body.get('reset'):
            value = None
        elif field == 'value':
            value, error = rules.clean_cell_value(raw)
            if error:
                raise HrError(error, code='HR_TIMESHEET_BAD_VALUE')
        else:
            value, error = rules.clean_minutes_value(raw)
            if error:
                raise HrError(error, code='HR_TIMESHEET_BAD_VALUE')
            # Пусто без «вернуть расчёт» — явное «минут нет»: опоздание по уважительной причине.
            value = '' if value is None else str(value)
        with db._get_cursor() as cursor:
            _ensure_open(cursor, user_id, day)
            computed = body.get('computed')
            computed = str(computed)[:40] if computed is not None else None
            old = queries.save_timesheet_edit(cursor, user_id=user_id, day=day, field=field, value=value,
                                              computed=computed, actor_id=ctx['user_id'])
            queries.audit(cursor, section='timesheet', action='cell', user_id=user_id, actor_id=ctx['user_id'],
                          summary=journal.cell_summary(day, field, old, value, computed, reset=bool(body.get('reset'))))
        return jsonify({'ok': True}), 200

    @route('/timesheet/note', methods=('PUT',))
    def hr_timesheet_note(*, ctx, today):
        service.require(ctx, 'timesheet')
        body = _body()
        bounds = rules.parse_month(body.get('month'))
        try:
            user_id = int(body.get('user_id'))
        except (TypeError, ValueError):
            user_id = None
        if not bounds or not user_id:
            raise HrError('Нужны сотрудник и месяц', code='HR_TIMESHEET_BAD_NOTE')
        note = ' '.join(str(body.get('note') or '').split())[:300]
        with db._get_cursor() as cursor:
            _ensure_open(cursor, user_id, bounds[0])
            queries.save_timesheet_note(cursor, user_id=user_id, month_start=bounds[0], note=note or None,
                                        actor_id=ctx['user_id'])
            queries.audit(cursor, section='timesheet', action='note', user_id=user_id, actor_id=ctx['user_id'],
                          summary=(f'Комментарий за {rules.month_title(bounds[0]).lower()}: «{note}»' if note
                                   else f'Комментарий за {rules.month_title(bounds[0]).lower()} убран'))
        return jsonify({'ok': True}), 200

    @route('/timesheet/close', methods=('POST', 'DELETE'))
    def hr_timesheet_close(*, ctx, today):
        service.require(ctx, 'timesheet')
        if request.method == 'DELETE':
            month, project = request.args.get('month'), request.args.get('project')
        else:
            body = _body()
            month, project = body.get('month'), body.get('project')
        bounds = rules.parse_month(month)
        if not bounds or project not in rules.PROJECTS:
            raise HrError('Нужны месяц и проект', code='HR_TIMESHEET_BAD_CLOSE')
        if request.method == 'DELETE':
            with db._get_cursor() as cursor:
                if queries.delete_closure(cursor, project, bounds[0]):
                    queries.audit(cursor, section='timesheet', action='reopen', actor_id=ctx['user_id'],
                                  summary=f'Табель «{project}», {rules.month_title(bounds[0]).lower()}: открыт заново')
            return jsonify({'ok': True}), 200
        with db._get_cursor() as cursor:
            if queries.get_closure(cursor, project, bounds[0]):
                # Уже закрыт: снимок не переснимается молча (сменились бы и «кто закрыл», и цифры).
                raise HrError('Табель за этот месяц уже закрыт — сначала откройте его заново',
                              status=409, code='HR_TIMESHEET_ALREADY_CLOSED')
        model = timesheet.collect(db, month=month, project=project, today=today)
        # Закрытый табель уходит в бухгалтерию и дальше не пересчитывается: без полного факта
        # «Отметок» (сбой Workpace/Clockster, недобранные дни) закрывать его нельзя.
        if not model.get('facts_ok') or model.get('pending_days'):
            raise HrError('«Отметки» за месяц собраны не полностью — закрыть табель можно, когда они соберутся',
                          status=409, code='HR_TIMESHEET_FACTS_INCOMPLETE')
        with db._get_cursor() as cursor:
            queries.save_closure(cursor, project, bounds[0], timesheet.model_json(model), ctx['user_id'])
            queries.audit(cursor, section='timesheet', action='close', actor_id=ctx['user_id'],
                          summary=f'Табель «{project}», {rules.month_title(bounds[0]).lower()}: закрыт')
        return jsonify({'ok': True}), 200

    # ── АВР ─────────────────────────────────────────────────────────────────

    @route('/acts/people')
    def hr_acts_people(*, ctx, today):
        with db._get_cursor() as cursor:
            people = service.acts_people(cursor, ctx, project=request.args.get('project', ''),
                                         department_id=request.args.get('department_id') or None,
                                         query=request.args.get('q', ''))
        return jsonify({'people': people}), 200

    @route('/acts/export', methods=('POST',))
    def hr_acts_export(*, ctx, today):
        body = _body()
        with db._get_cursor() as cursor:
            acts = service.build_acts(cursor, ctx, body, today)
        payload = documents.acts_workbook(acts)
        return _file(payload, XLSX_MIME, documents.acts_file_name(acts, acts[0]['period_to']))

    # ── Импорт таблиц HR ────────────────────────────────────────────────────

    @route('/import/preview', methods=('POST',))
    def hr_import_preview(*, ctx, today):
        upload = request.files.get('file')
        if not upload:
            raise HrError('Приложите файл Excel', code='HR_IMPORT_NO_FILE')
        content = upload.read(6 * 1024 * 1024)
        with db._get_cursor() as cursor:
            preview = service.import_preview(cursor, ctx, content, project=request.form.get('project'))
        return jsonify(preview), 200

    @route('/import/apply', methods=('POST',))
    def hr_import_apply(*, ctx, today):
        with db._get_cursor() as cursor:
            updates, summary = service.import_apply(cursor, ctx, _body())
        failed = _apply_user_fields(updates, ctx['user_id'])
        names = summary.pop('names', {})
        # Карточка «Учета сотрудников» не обновилась — окно итога называет этих людей поимённо:
        # повторная загрузка их уже не поправит (договор записан, сверка покажет «без изменений»).
        summary['cards_failed'] = len(set(failed))
        summary['cards_failed_names'] = sorted({names.get(user_id) or str(user_id) for user_id in failed})
        return jsonify(summary), 200

    # ── Журнал ──────────────────────────────────────────────────────────────

    @route('/journal')
    def hr_journal(*, ctx, today):
        section = request.args.get('section') or 'intake'
        if section not in JOURNAL_SECTIONS:
            raise HrError('Нет такого раздела', code='HR_JOURNAL_BAD_SECTION')
        service.require(ctx, JOURNAL_SECTIONS[section])
        # «АВР» у главы и СВ — их собственные выгрузки; отделу кадров — все.
        actor_only = section == 'acts' and not service.capabilities(ctx)['acts_all']
        try:
            offset = max(0, int(request.args.get('offset') or 0))
        except ValueError:
            offset = 0
        with db._get_cursor() as cursor:
            items, total = queries.list_audit(cursor, section=section, query=request.args.get('q', ''),
                                              actor_id=ctx['user_id'] if actor_only else None, limit=50, offset=offset)
        for item in items:
            item['created_at'] = _iso(item['created_at'])
        return jsonify({'items': items, 'total': total, 'offset': offset}), 200

    return bp
