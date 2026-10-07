"""HTTP-эндпоинты раздела «Списки Байги» (Flask Blueprint /api/baiga).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2
сам подключает этот модуль, обратный импорт был бы циклом (как у parcels/).

Соглашения портала: методы включают OPTIONS и первым делом отдаётся preflight,
авторизация — общий require_api_key, ошибка — {"error": "...", "code": "..."}.

ВАЖНО ПРО КОДЫ ОТВЕТОВ. `db._get_cursor()` коммитит всё, что записано до
`return … , 4xx` (откат — только по исключению). Поэтому каждый обработчик
сначала проверяет ВСЁ, и только потом пишет: загрузка недели либо ложится
целиком, либо не ложится вовсе.

Ручки:
    GET    ''                      экран: права, недели, значения фильтров
    POST   /rows                   поиск: фильтры, сортировка, страница
    POST   /export                 xlsx текущей выборки
    POST   /uploads/preview        разбор файла без записи
    POST   /uploads                загрузка недели; замена — с replace=1
    DELETE /uploads/<id>           удалить неделю
    GET    /uploads/<id>/file      исходник недели
    GET    /journal                журнал загрузок и выгрузок
    GET    /access                 лист «Доступ»: выдачи, круг, кому можно выдать
    POST   /access/grants          выдать уровень сразу нескольким адресатам
    PATCH  /access/grants/<id>     сменить уровень выдачи
    DELETE /access/grants/<id>     снять выдачу

Экран и поиск — всем, кому раздел открыт; выгрузка — от уровня «выгрузка»,
загрузка и журнал — тем, кому он открыт полностью (access.can_manage); доступ
раздают супер-админ и названные поимённо (access.can_manage_access).

Поиск — POST, а не GET: вставленный список ID бывает в сотни значений, и в
адрес запроса он бы не влез.
"""

import logging
from datetime import date, datetime
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify as _flask_jsonify, request, send_file

from . import access, filters, parse, queries, report, schema

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Заголовки ответа с файлом, которые браузер обязан отдать странице: имя файла
# (в нём период) и число строк — для тоста «выгружено N строк».
_EXPOSED = 'Content-Disposition, X-Rows'


def _plain(value):
    """Ответ → JSON: время — ISO-строкой без зоны.

    Своего JSON-провайдера в приложении нет, а Flask по умолчанию пишет datetime
    как RFC 1123 с приписанным «GMT»; в базе же лежат НАСТЕННЫЕ часы Алматы, и
    браузер сдвинул бы каждую дату журнала на пять часов (тот же приём, что у
    olx_ads/routes.py).
    """
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def jsonify(value):
    return _flask_jsonify(_plain(value))


def _bad(message, code, status=400, **extra):
    payload = {"error": message, "code": code}
    payload.update(extra)
    return jsonify(payload), status


def _existing_payload(existing, sha256):
    if not existing:
        return None
    return {
        'id': existing['id'],
        'file_name': existing['file_name'],
        'rows_count': existing['rows_count'],
        'uploaded_by_name': existing['uploaded_by_name'],
        'uploaded_at': existing['uploaded_at'],
        'same_file': existing.get('file_sha256') == sha256,
    }


def _overlap_message(overlaps):
    weeks = ', '.join('%s – %s' % (week['period_start'].strftime('%d.%m.%Y'), week['period_end'].strftime('%d.%m.%Y'))
                      for week in overlaps)
    return ('Период пересекается с уже загруженной неделей (%s), но начинается в другой день. '
            'Проверьте даты в имени файла и в колонке «Дата»' % weeks)


def build_baiga_blueprint(*, db, require_api_key, build_cors_preflight_response,
                          resolve_requester, sensitive_access_granted):
    """Собирает Blueprint раздела.

    sensitive_access_granted — (user_id) -> bool: подтверждена ли ТЕКУЩАЯ сессия
    QR-кодом. Аргумент обязательный, без значения по умолчанию, как у
    «Посылок»: забытая зависимость должна уронить сборку блюпринта на старте, а
    не тихо открыть ФИО и номера ВУ всем.
    """
    bp = Blueprint('baiga', __name__, url_prefix='/api/baiga')

    def baiga_route(rule, methods=('GET',), need=None):
        """Каркас роута: preflight, авторизация, контекст, гейты, ошибки.

        need='export' — выгрузка; need='manage' — загрузка, удаление, журнал;
        need='access' — раздача доступа. Кому что открыто, считает access.py.
        """
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

                    # Гейт раздела — до любого обработчика: спрятанный пункт меню
                    # доступом не является, раздел открывается и прямым адресом.
                    if not access.can_open_section(ctx):
                        return _bad('Раздел «Списки Байги» вам не открыт', 'BAIGA_SECTION_CLOSED', 403)
                    # Второй гейт — QR-подтверждение сессии. ПОСЛЕ первого:
                    # предлагать подтвердить доступ к тому, чего человеку не
                    # выдавали, — тупик.
                    if (access.requires_sensitive_qr(ctx)
                            and not sensitive_access_granted(ctx['user_id'])):
                        return _bad('Раздел «Списки Байги» откроется после QR-подтверждения доступа',
                                    'SENSITIVE_ACCESS_REQUIRED', 403)
                    if need == 'export' and not access.can_export(ctx):
                        return _bad('Выгрузка списков вам не открыта', 'BAIGA_EXPORT_FORBIDDEN', 403)
                    if need == 'manage' and not access.can_manage(ctx):
                        return _bad('Загрузка недель и журнал вам не открыты', 'BAIGA_MANAGE_FORBIDDEN', 403)
                    if need == 'access' and not access.can_manage_access(ctx):
                        return _bad('Раздавать доступ к разделу вам не поручено', 'BAIGA_ACCESS_FORBIDDEN', 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('baiga: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Списки Байги»",
                        "detail": str(exc)[:200],
                    }), 500

            return wrapper

        return decorator

    def _payload():
        return request.get_json(silent=True) or {}

    def _actor(ctx):
        return {'user_id': ctx['user_id'], 'name': ctx.get('name')}

    def _not_ready():
        return _bad('Раздел ещё разворачивается — попробуйте после перезапуска сервера',
                    'BAIGA_SCHEMA_NOT_READY', 409)

    def _read_upload():
        """(байты, имя файла) или готовый ответ-отказ."""
        if (request.content_length or 0) > parse.MAX_FILE_BYTES + 1024 * 1024:
            return None, None, _bad('Файл больше %d МБ' % (parse.MAX_FILE_BYTES // (1024 * 1024)),
                                    'BAIGA_FILE_TOO_BIG', 413)
        upload = request.files.get('file')
        if upload is None:
            return None, None, _bad('Выберите файл недели', 'BAIGA_FILE_REQUIRED')
        content = upload.read(parse.MAX_FILE_BYTES + 1)
        if len(content) > parse.MAX_FILE_BYTES:
            return None, None, _bad('Файл больше %d МБ' % (parse.MAX_FILE_BYTES // (1024 * 1024)),
                                    'BAIGA_FILE_TOO_BIG', 413)
        # Имя — как у человека на диске: в нём период недели, а secure_filename
        # выбросил бы кириллицу вместе со словом «байги». Путь отрезаем.
        name = str(request.form.get('file_name') or upload.filename or '').strip()
        name = name.replace('\\', '/').rsplit('/', 1)[-1][:255]
        return content, name, None

    def _parse(content, name):
        try:
            return parse.parse_workbook(content, name), None
        except parse.ParseError as exc:
            return None, _bad(exc.message, exc.code, 422)

    # ── Экран ─────────────────────────────────────────────────────────────
    @baiga_route('')
    def baiga_screen(ctx):
        capabilities = access.capabilities(ctx)
        limits = {
            'page_sizes': list(filters.PAGE_SIZES),
            'max_upload_mb': parse.MAX_FILE_BYTES // (1024 * 1024),
            'export_limit': report.EXPORT_LIMIT,
            'max_list': filters.MAX_LIST_TOKENS,
        }
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"schema_ready": False, "capabilities": capabilities, "limits": limits,
                                "weeks": [], "options": {}})
            weeks = queries.list_weeks(cursor, parse.CAMPAIGN)
            options = queries.filter_options(cursor)
        return jsonify({"schema_ready": True, "capabilities": capabilities, "limits": limits,
                        "weeks": weeks, "options": options})

    # ── Поиск ─────────────────────────────────────────────────────────────
    @baiga_route('/rows', methods=('POST',))
    def baiga_rows(ctx):
        data = _payload()
        chosen = filters.normalize(data.get('filters'))
        page, size = filters.page_args(data)
        order_sql, sort_key, sort_dir = filters.order_by(data.get('sort'), data.get('dir'))
        where_sql, params = filters.where(chosen)
        # Полоса зачётов считается без выбранного зачёта — из неё переходят к
        # соседнему (queries.zachet_summary).
        zachet_where, zachet_params = filters.where(dict(chosen, zachet=''))
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            totals = queries.totals(cursor, where_sql, params)
            zachets = queries.zachet_summary(cursor, zachet_where, zachet_params)
            rows = queries.search(cursor, where_sql, params, order_sql, size, (page - 1) * size)
            missing = []
            if chosen['list']:
                drivers, licenses = queries.found_keys(cursor, where_sql, params)
                missing = filters.not_found(chosen['list'], drivers, licenses)
        return jsonify({
            "rows": rows, "totals": totals, "zachets": zachets, "page": page, "size": size,
            "sort": sort_key, "dir": sort_dir,
            "list": {"count": len(chosen['list']), "not_found": missing[:200], "not_found_total": len(missing)},
        })

    # ── Выгрузка ──────────────────────────────────────────────────────────
    @baiga_route('/export', methods=('POST',), need='export')
    def baiga_export(ctx):
        data = _payload()
        chosen = filters.normalize(data.get('filters'))
        mode = data.get('mode') if data.get('mode') in report.MODES else report.MODE_SHEETS
        where_sql, params = filters.where(chosen)
        if mode == report.MODE_SHEETS:
            # Порядок файла: неделя, лист, строка — тогда листы и строки встают
            # как в исходнике (п. 8: «совпадает с исходником построчно»).
            order_sql = 'r.period_start, r.sheet_order, r.row_number, r.id'
        else:
            order_sql, _, _ = filters.order_by(data.get('sort'), data.get('dir'))

        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            # Сначала счёт, потом строки: отказ «больше лимита» не должен стоить
            # выборки ста тысяч строк в память единственного процесса портала.
            count = queries.totals(cursor, where_sql, params)['rows']
            if count > report.EXPORT_LIMIT:
                return _bad('В выборке больше %d строк — сузьте фильтры' % report.EXPORT_LIMIT,
                            'BAIGA_EXPORT_TOO_BIG', 422)
            if not count:
                return _bad('В выборке нет строк — выгружать нечего', 'BAIGA_EXPORT_EMPTY', 422)
            rows = queries.export_rows(cursor, where_sql, params, order_sql, report.EXPORT_LIMIT)
            weeks = {week['period_start']: week for week in queries.list_weeks(cursor, parse.CAMPAIGN)}
        if len(rows) > report.EXPORT_LIMIT:
            return _bad('В выборке больше %d строк — сузьте фильтры' % report.EXPORT_LIMIT,
                        'BAIGA_EXPORT_TOO_BIG', 422)
        if not rows:
            return _bad('В выборке нет строк — выгружать нечего', 'BAIGA_EXPORT_EMPTY', 422)

        periods = {row['period_start'] for row in rows}
        week = weeks.get(next(iter(periods))) if len(periods) == 1 else None
        name = report.file_name(week['period_start'], week['period_end']) if week else report.file_name()
        content = report.build(rows, mode)
        # Журнал — после того, как файл собрался: запись «выгрузил» без файла
        # была бы ложью. Не записался журнал — файл не отдаётся (исключение →
        # 500): выгрузка персональных данных без следа хуже отказа.
        with db._get_cursor() as cursor:
            queries.log_export(cursor, kind='export', mode=mode, filters=filters.public(chosen),
                               rows_count=len(rows), actor=_actor(ctx))
        response = send_file(BytesIO(content), mimetype=XLSX_MIME, as_attachment=True, download_name=name)
        response.headers['X-Rows'] = str(len(rows))
        response.headers['Access-Control-Expose-Headers'] = _EXPOSED
        return response

    # ── Загрузка недели ───────────────────────────────────────────────────
    @baiga_route('/uploads/preview', methods=('POST',), need='manage')
    def baiga_upload_preview(ctx):
        """Разбор файла без записи: период, листы, ошибки, предупреждения и —
        если неделя уже есть — кто и когда её загрузил."""
        content, name, refusal = _read_upload()
        if refusal:
            return refusal
        result, refusal = _parse(content, name)
        if refusal:
            return refusal
        summary = parse.public_summary(result)
        summary['existing'] = None
        if result['period']:
            period = result['period']
            with db._get_cursor() as cursor:
                if not schema.schema_is_ready(cursor):
                    return _not_ready()
                existing = queries.find_active_upload(cursor, result['campaign'], period['start'])
                overlaps = queries.find_overlapping_uploads(cursor, result['campaign'], period['start'], period['end'])
            summary['existing'] = _existing_payload(existing, result['sha256'])
            if overlaps:
                # Та же проверка, что у загрузки: предпросмотр не обещает того,
                # от чего загрузка откажется.
                summary['ok'] = False
                summary['errors'] = [{'message': _overlap_message(overlaps)}] + summary['errors']
                summary['errors_total'] += 1
        return jsonify(summary)

    @baiga_route('/uploads', methods=('POST',), need='manage')
    def baiga_upload(ctx):
        content, name, refusal = _read_upload()
        if refusal:
            return refusal
        result, refusal = _parse(content, name)
        if refusal:
            return refusal
        if not result['ok']:
            return _bad('В файле есть ошибки — загрузка не выполнена', 'BAIGA_FILE_HAS_ERRORS', 422,
                        preview=parse.public_summary(result))
        replace = str(request.form.get('replace') or '').lower() in ('1', 'true', 'yes')
        actor = _actor(ctx)
        period_start = result['period']['start']

        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            # Блокировка недели — и только потом проверка «есть ли она»: две
            # одновременные загрузки одной недели идут по очереди.
            queries.lock_week(cursor, result['campaign'], period_start)
            overlaps = queries.find_overlapping_uploads(cursor, result['campaign'], period_start,
                                                        result['period']['end'])
            if overlaps:
                return _bad(_overlap_message(overlaps), 'BAIGA_WEEK_OVERLAP', 409)
            existing = queries.find_active_upload(cursor, result['campaign'], period_start)
            if existing and not replace:
                return _bad('Эта неделя уже загружена — подтвердите замену', 'BAIGA_WEEK_EXISTS', 409,
                            existing=_existing_payload(existing, result['sha256']))

            # Дальше только запись — все проверки позади.
            if existing:
                queries.close_upload(cursor, existing['id'], 'replaced', actor)
            upload_id = queries.insert_upload(cursor, result, actor)
            queries.insert_file(cursor, upload_id, content)
            written = queries.insert_rows(cursor, upload_id, period_start, result['rows'])
            if written != len(result['rows']):
                # Исключение — значит откат всей транзакции: неделя не ляжет
                # наполовину (п. 4: «все строки или ни одной»).
                raise RuntimeError('записано %d строк из %d' % (written, len(result['rows'])))
            if existing:
                queries.mark_replaced_by(cursor, existing['id'], upload_id)
            upload = queries.get_upload(cursor, upload_id)
        return jsonify({"upload": upload, "replaced": bool(existing)}), 201

    @baiga_route('/uploads/<int:upload_id>', methods=('DELETE',), need='manage')
    def baiga_upload_delete(upload_id, ctx):
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            upload = queries.get_upload(cursor, upload_id, lock=True)
            if not upload:
                return _bad('Такой загрузки нет', 'BAIGA_UPLOAD_NOT_FOUND', 404)
            if upload['status'] != 'active':
                return _bad('Эта неделя уже заменена или удалена — обновите страницу',
                            'BAIGA_UPLOAD_CLOSED', 409)
            queries.close_upload(cursor, upload_id, 'deleted', _actor(ctx))
        return jsonify({"deleted": upload_id})

    @baiga_route('/uploads/<int:upload_id>/file', need='manage')
    def baiga_upload_file(upload_id, ctx):
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            upload = queries.get_upload(cursor, upload_id)
            if not upload:
                return _bad('Такой загрузки нет', 'BAIGA_UPLOAD_NOT_FOUND', 404)
            content = queries.read_file(cursor, upload_id)
            if content is None:
                return _bad('Исходника больше нет: неделя заменена или удалена', 'BAIGA_FILE_GONE', 404)
            # Исходник — те же персональные данные, что выгрузка: в журнал.
            queries.log_export(cursor, kind='source', rows_count=upload['rows_count'],
                               upload_id=upload_id, actor=_actor(ctx))
        response = send_file(BytesIO(content), mimetype=XLSX_MIME, as_attachment=True,
                             download_name=upload['file_name'] or 'Список байги.xlsx')
        response.headers['X-Rows'] = str(upload['rows_count'])
        response.headers['Access-Control-Expose-Headers'] = _EXPOSED
        return response

    # ── Журнал ────────────────────────────────────────────────────────────
    @baiga_route('/journal', need='manage')
    def baiga_journal(ctx):
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"uploads": [], "exports": []})
            uploads = queries.list_uploads(cursor)
            exports = queries.list_exports(cursor)
        return jsonify({"uploads": uploads, "exports": exports})

    # ── Доступ ────────────────────────────────────────────────────────────
    def _access_not_ready():
        return _bad('Выдача доступа ещё разворачивается — попробуйте после перезапуска сервера',
                    'BAIGA_ACCESS_NOT_READY', 409)

    def _level(data):
        level = str(data.get('level') or '').strip().lower()
        return level if level in access.LEVELS else None

    def _subjects(data):
        """[(вид, id)] без повторов или готовый ответ-отказ."""
        raw = data.get('subjects')
        if not isinstance(raw, list) or not raw:
            return None, _bad('Выберите, кому открыть раздел', 'BAIGA_ACCESS_SUBJECT_REQUIRED')
        subjects = []
        for item in raw:
            kind = str((item or {}).get('type') or '').strip().lower() if isinstance(item, dict) else ''
            ident = item.get('id') if isinstance(item, dict) else None
            # bool — тоже int: True прошёл бы как адресат №1.
            if kind not in access.SUBJECT_TYPES or isinstance(ident, bool) or not isinstance(ident, int) or ident <= 0:
                return None, _bad('Не удалось разобрать, кому открыть раздел', 'BAIGA_ACCESS_BAD_SUBJECT')
            if (kind, ident) not in subjects:
                subjects.append((kind, ident))
        if len(subjects) > access.MAX_GRANT_SUBJECTS:
            return None, _bad('За один раз — не больше %d адресатов' % access.MAX_GRANT_SUBJECTS,
                              'BAIGA_ACCESS_TOO_MANY')
        return subjects, None

    def _circle(cursor):
        """Круг раздела строками — с названиями отделов и именами названных."""
        rows = [dict(row) for row in access.circle()]
        codes = sorted({code for row in rows for code in row.get('departments', ())})
        user_ids = sorted({user_id for row in rows for user_id in row.get('user_ids', ())})
        departments, people = queries.circle_names(cursor, codes, user_ids)
        return [{
            'key': row['key'], 'level': row['level'], 'qr': row['qr'],
            'departments': [departments.get(code, code) for code in row.get('departments', ())],
            'people': [people[user_id] for user_id in row.get('user_ids', ()) if user_id in people],
        } for row in rows]

    @baiga_route('/access', need='access')
    def baiga_access(ctx):
        """Лист «Доступ» одним запросом: что выдано, кому открыто и без выдач
        и кому можно выдать."""
        with db._get_cursor() as cursor:
            if not schema.grants_ready(cursor):
                return _access_not_ready()
            return jsonify({
                "grants": queries.list_grants(cursor),
                "circle": _circle(cursor),
                "catalog": queries.access_catalog(cursor),
                "max_subjects": access.MAX_GRANT_SUBJECTS,
            })

    @baiga_route('/access/grants', methods=('POST',), need='access')
    def baiga_access_grant(ctx):
        """Выдать уровень сразу нескольким. Всё или ничего: сначала проверены
        все адресаты, потом одна запись — половина выданного списка читалась бы
        как «не удалось» при наполовину открытом разделе."""
        data = _payload()
        level = _level(data)
        if not level:
            return _bad('Выберите, что разрешить', 'BAIGA_ACCESS_BAD_LEVEL')
        subjects, refusal = _subjects(data)
        if refusal:
            return refusal
        with db._get_cursor() as cursor:
            if not schema.grants_ready(cursor):
                return _access_not_ready()
            queries.lock_access(cursor)
            names = queries.find_subjects(cursor, subjects)
            gone = [subject for subject in subjects if subject not in names]
            if gone:
                return _bad('Кого-то из выбранных больше нет в списке — обновите страницу и выберите заново',
                            'BAIGA_ACCESS_SUBJECT_GONE', 422)
            # Дальше только запись — все проверки позади.
            granted, changed = queries.write_grants(
                cursor, [(kind, ident, names[(kind, ident)]) for kind, ident in subjects], level, _actor(ctx))
            grants = queries.list_grants(cursor)
        return jsonify({"granted": granted, "changed": changed, "grants": grants})

    @baiga_route('/access/grants/<int:grant_id>', methods=('PATCH',), need='access')
    def baiga_access_change(grant_id, ctx):
        level = _level(_payload())
        if not level:
            return _bad('Выберите, что разрешить', 'BAIGA_ACCESS_BAD_LEVEL')
        with db._get_cursor() as cursor:
            if not schema.grants_ready(cursor):
                return _access_not_ready()
            queries.lock_access(cursor)
            grant = queries.get_grant(cursor, grant_id, lock=True)
            if not grant:
                return _bad('Этой выдачи уже нет — её сняли', 'BAIGA_ACCESS_GRANT_NOT_FOUND', 404)
            if grant['level'] != level:
                queries.set_grant_level(cursor, grant, level, _actor(ctx))
            grants = queries.list_grants(cursor)
        return jsonify({"grants": grants})

    @baiga_route('/access/grants/<int:grant_id>', methods=('DELETE',), need='access')
    def baiga_access_revoke(grant_id, ctx):
        with db._get_cursor() as cursor:
            if not schema.grants_ready(cursor):
                return _access_not_ready()
            queries.lock_access(cursor)
            grant = queries.get_grant(cursor, grant_id, lock=True)
            if not grant:
                return _bad('Этой выдачи уже нет — её сняли', 'BAIGA_ACCESS_GRANT_NOT_FOUND', 404)
            queries.delete_grant(cursor, grant, _actor(ctx))
            grants = queries.list_grants(cursor)
        return jsonify({"grants": grants})

    return bp
