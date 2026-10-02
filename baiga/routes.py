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
    POST   /export                 xlsx текущей выборки (маркетинг, руководители)
    POST   /uploads/preview        разбор файла без записи (аналитик)
    POST   /uploads                загрузка недели; замена — с replace=1 (аналитик)
    DELETE /uploads/<id>           удалить неделю (аналитик)
    GET    /uploads/<id>/file      исходник недели (аналитик)
    GET    /journal                журнал загрузок и выгрузок (аналитик)

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

        need='export' — выгрузка (маркетинг, руководители, аналитик, админ);
        need='manage' — загрузка, удаление, журнал (аналитик, админ).
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
                        return _bad('Выгружать списки могут маркетинг и руководители',
                                    'BAIGA_EXPORT_FORBIDDEN', 403)
                    if need == 'manage' and not access.can_manage(ctx):
                        return _bad('Загружать и удалять недели может аналитик', 'BAIGA_MANAGE_FORBIDDEN', 403)
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

    return bp
