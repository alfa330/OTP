"""HTTP раздела «Провайдер ЭДО».

Blueprint, как у вики, «Обращений» и «Ограничителя Перезвона»: зависимости
приходят аргументами, обратный импорт из bot_schedule2 сюда был бы циклом.

Выгрузка считается В ФОНЕ и отдаётся 202-м с номером задания. Не потому что так
моднее: обход занимает минуты (замеренные пять на восьми тысячах строк), а
waitress рвёт соединение на 120 секундах. Синхронный ответ здесь означал бы
«кнопка всегда падает по таймауту».
"""

import logging
import re
import time
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import access, engine, queries, report
from .client import FleetClient, FleetError, FleetSessionExpired

# Обычная выгрузка из кабинета на 150 тысяч строк весит около 10 МБ. Двадцать
# пять — с запасом, и при этом не даёт положить инстанс одним запросом.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_EXTENSIONS = ('.xlsx', '.xlsm', '.csv')

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Прогресс пишем в базу не чаще раза в две секунды: обход шлёт сотни событий в
# минуту, и каждое означало бы соединение из общего пула.
PROGRESS_MIN_INTERVAL = 2.0


def build_fleet_edm_blueprint(*, db, require_api_key, build_cors_preflight_response,
                              resolve_requester, pool, excel_text_warning=None):
    """pool — свой ThreadPoolExecutor раздела. Общий executor_pool сюда не годится:
    в нём четыре места на всё приложение, а выгрузка занимает одно из них на
    минуты подряд."""
    bp = Blueprint('fleet_edm', __name__, url_prefix='/api/fleet_edm')

    # ── доступ ───────────────────────────────────────────────────────────────

    def section_route(rule, methods=('GET',), manage_session=False):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _row, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    with db._get_cursor() as cursor:
                        requester = queries.access_context(cursor, requester_id)
                    if not requester:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    # Гейт здесь, а не в обработчиках: спрятанный пункт меню
                    # доступом не является, раздел открывается и прямым адресом.
                    if not access.can_view_section(requester):
                        return jsonify({"error": "Раздел вам не открыт"}), 403
                    if manage_session and not access.can_manage_session(requester):
                        return jsonify({"error": "Сессию кабинета меняют только админы"}), 403
                    return handler(requester_id, requester, *args, **kwargs)
                except Exception:
                    logging.exception("Провайдер ЭДО: ошибка в %s", rule)
                    return jsonify({"error": "Внутренняя ошибка"}), 500
            return wrapper
        return decorator

    # ── фоновая выгрузка ─────────────────────────────────────────────────────

    def make_progress(job_id):
        state = {'at': 0.0}

        def emit(**payload):
            now = time.time()
            # Финальные события (100%) пишем всегда, промежуточные — не чаще
            # раза в две секунды.
            if payload.get('percent') != 100 and now - state['at'] < PROGRESS_MIN_INTERVAL:
                return
            state['at'] = now
            try:
                with db._get_cursor() as cursor:
                    queries.update_progress(
                        cursor, job_id,
                        percent=payload.get('percent'),
                        note=payload.get('note'),
                        rows_total=payload.get('rows_total'),
                        rows_resolved=payload.get('rows_resolved'),
                        requests_count=payload.get('requests'),
                    )
            except Exception:
                logging.exception("Провайдер ЭДО: прогресс задания %s не записан", job_id)

        return emit

    def run_job(job_id, file_bytes, source_name):
        """Тело фоновой выгрузки. Живёт в потоке, поэтому не трогает ни request,
        ни flask.g — у потока их нет (на этом уже обжигались обработчики бота)."""
        started = time.time()
        requests_count = 0
        try:
            with db._get_cursor() as cursor:
                session = queries.load_session(cursor)
            if not session or not session.get('cookies'):
                raise FleetSessionExpired(
                    'Сессия кабинета Fleet не настроена. Нужен вход в кабинет и '
                    'передача сессии в раздел.'
                )

            rows, meta = engine.parse_input(file_bytes, source_name)
            emit = make_progress(job_id)
            emit(percent=3, note='Файл разобран: {} строк'.format(meta['rows_total']),
                 rows_total=meta['rows_total'])

            client = FleetClient(session['cookies'], session.get('user_agent'))
            resolution = engine.resolve(rows, client, progress=emit)
            requests_count = resolution.get('requests') or client.requests_count

            emit(percent=98, note='Собираем файл', requests=requests_count)
            stream = report.build_workbook(
                rows, resolution, source_name=source_name,
                text_warning_patch=excel_text_warning,
            )
            file_bytes_out = stream.getvalue()
            file_name = report.report_filename(source_name)

            results = resolution.get('results') or {}
            resolved = sum(1 for entry in results.values() if entry.get('provider_name'))
            stats = {
                'rows_total': meta['rows_total'],
                'unique_ids': meta['unique_ids'],
                'had_park_column': meta['has_park_column'],
                'providers': resolution.get('provider_counts') or {},
                'check': resolution.get('check') or {},
                'from_card': (resolution.get('stats') or {}).get('from_card', 0),
                'not_found': (resolution.get('stats') or {}).get('not_found', 0),
                'park_probe_requests': resolution.get('park_probe_requests') or 0,
                'skipped_orphans': resolution.get('skipped_orphans') or 0,
                'parks_total': resolution.get('parks_total') or 0,
            }
            with db._get_cursor() as cursor:
                queries.finish_job(
                    cursor, job_id,
                    file_bytes=file_bytes_out, file_name=file_name, stats=stats,
                    duration_ms=int((time.time() - started) * 1000),
                    rows_total=meta['rows_total'],
                    rows_resolved=resolved,
                    rows_failed=meta['rows_total'] - resolved,
                    requests_count=requests_count,
                )
                queries.mark_session_ok(cursor)
            logging.info("Провайдер ЭДО: задание %s готово, %s строк, %s запросов",
                         job_id, meta['rows_total'], requests_count)

        except FleetSessionExpired as error:
            _fail(job_id, error, 'session_expired', started, requests_count)
            try:
                with db._get_cursor() as cursor:
                    queries.mark_session_error(cursor, str(error))
            except Exception:
                logging.exception("Провайдер ЭДО: не удалось отметить сессию протухшей")
        except engine.InputError as error:
            _fail(job_id, error, 'bad_file', started, requests_count)
        except FleetError as error:
            _fail(job_id, error, 'fleet_error', started, requests_count)
        except Exception as error:  # noqa: BLE001 — карточка обязана закрыться всегда
            logging.exception("Провайдер ЭДО: задание %s упало", job_id)
            _fail(job_id, error, 'internal', started, requests_count)

    def _fail(job_id, error, code, started, requests_count):
        try:
            with db._get_cursor() as cursor:
                queries.finish_job(
                    cursor, job_id, error=str(error), error_code=code,
                    duration_ms=int((time.time() - started) * 1000),
                    requests_count=requests_count,
                )
        except Exception:
            logging.exception("Провайдер ЭДО: не удалось закрыть карточку %s", job_id)

    # ── ручки ────────────────────────────────────────────────────────────────

    @section_route('/overview')
    def fleet_edm_overview(requester_id, requester):
        """Всё, что нужно разделу при входе, одним запросом: состояние сессии и
        список выгрузок."""
        with db._get_cursor() as cursor:
            # Карточки, пережившие рестарт, закрываем здесь: иначе раздел вечно
            # показывает прогресс задания, за которым уже никого нет.
            queries.fail_stale_jobs(cursor)
            session = queries.session_status(cursor)
            jobs = queries.list_jobs(cursor, limit=request.args.get('limit', 50))
            running = queries.has_running_job(cursor)
        return jsonify({
            'status': 'success',
            'session': session,
            'jobs': jobs,
            'running_job_id': running,
            'can_manage_session': access.can_manage_session(requester),
        }), 200

    @section_route('/jobs', methods=('GET', 'POST'))
    def fleet_edm_jobs(requester_id, requester):
        if request.method == 'GET':
            with db._get_cursor() as cursor:
                jobs = queries.list_jobs(cursor, limit=request.args.get('limit', 50))
            return jsonify({'status': 'success', 'jobs': jobs}), 200

        if not access.can_run_job(requester):
            return jsonify({"error": "Недостаточно прав"}), 403

        uploaded = request.files.get('file')
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "Файл не приложен"}), 400
        source_name = _safe_name(uploaded.filename)
        if not source_name.lower().endswith(ALLOWED_EXTENSIONS):
            return jsonify({"error": "Нужен файл Excel (.xlsx) или .csv"}), 400
        file_bytes = uploaded.read()
        if not file_bytes:
            return jsonify({"error": "Файл пустой"}), 400
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            return jsonify({"error": "Файл больше {} МБ".format(
                MAX_UPLOAD_BYTES // (1024 * 1024))}), 413

        with db._get_cursor() as cursor:
            session = queries.session_status(cursor)
            if not session.get('configured'):
                return jsonify({
                    "error": "Сессия кабинета Fleet не настроена — выгрузка не сможет "
                             "туда зайти.",
                    "code": "SESSION_NOT_CONFIGURED",
                }), 503
            running = queries.has_running_job(cursor)
            if running:
                # Параллельный обход не сломает данные, но удвоит темп запросов к
                # чужому кабинету, а его лимит нам неизвестен.
                return jsonify({
                    "error": "Выгрузка №{} ещё идёт. Дождитесь её окончания.".format(running),
                    "code": "ALREADY_RUNNING",
                    "running_job_id": running,
                }), 409
            queries.cleanup(cursor)
            job_id = queries.create_job(
                cursor, user_id=requester_id, user_name=(requester or {}).get('name'),
                source_name=source_name, source_bytes=file_bytes,
            )

        pool.submit(run_job, job_id, file_bytes, source_name)
        return jsonify({'status': 'success', 'job_id': job_id, 'job_status': 'running'}), 202

    @section_route('/jobs/<int:job_id>')
    def fleet_edm_job(requester_id, requester, job_id):
        with db._get_cursor() as cursor:
            job = queries.get_job(cursor, job_id)
        if not job:
            return jsonify({"error": "Выгрузка не найдена"}), 404
        return jsonify({'status': 'success', 'job': job}), 200

    @section_route('/jobs/<int:job_id>/file')
    def fleet_edm_job_file(requester_id, requester, job_id):
        with db._get_cursor() as cursor:
            payload = queries.job_file(cursor, job_id, kind=request.args.get('kind', 'result'))
        if not payload:
            return jsonify({"error": "Файл не найден"}), 404
        return send_file(
            BytesIO(payload['content']),
            mimetype=XLSX_MIME,
            as_attachment=True,
            download_name=payload['file_name'] or 'Провайдер ЭДО {}.xlsx'.format(job_id),
        )

    @section_route('/session', methods=('GET', 'POST'), manage_session=True)
    def fleet_edm_session(requester_id, requester):
        if request.method == 'GET':
            with db._get_cursor() as cursor:
                return jsonify({'status': 'success', 'session': queries.session_status(cursor)}), 200

        payload = request.get_json(silent=True) or {}
        cookies = payload.get('cookies')
        if not cookies:
            return jsonify({"error": "Не переданы куки сессии"}), 400
        user_agent = str(payload.get('user_agent') or '').strip()

        # Принимаем только то, что реально работает: сразу ходим в кабинет.
        # Молча сохранённая нерабочая сессия — это выгрузка, падающая через час
        # ожидания вместо честного отказа сейчас.
        try:
            client = FleetClient(cookies, user_agent)
            checked = client.check()
        except FleetSessionExpired as error:
            return jsonify({"error": str(error), "code": "SESSION_INVALID"}), 400
        except FleetError as error:
            return jsonify({"error": "Кабинет не ответил: {}".format(error)}), 502

        with db._get_cursor() as cursor:
            queries.save_session(
                cursor, cookies=cookies, user_agent=user_agent,
                account=checked.get('account'), parks_count=checked.get('parks_count'),
                updated_by=requester_id,
            )
            session = queries.session_status(cursor)
        logging.info("Провайдер ЭДО: сессия кабинета обновлена (%s парков)",
                     checked.get('parks_count'))
        return jsonify({'status': 'success', 'session': session}), 200

    @section_route('/session/check', methods=('POST',))
    def fleet_edm_session_check(requester_id, requester):
        """Жива ли сессия прямо сейчас. Отдельной кнопкой: узнавать об этом из
        упавшей через десять минут выгрузки — плохой способ."""
        with db._get_cursor() as cursor:
            session = queries.load_session(cursor)
        if not session or not session.get('cookies'):
            return jsonify({'status': 'success', 'alive': False,
                            'error': 'Сессия не настроена'}), 200
        try:
            client = FleetClient(session['cookies'], session.get('user_agent'))
            checked = client.check()
        except FleetError as error:
            with db._get_cursor() as cursor:
                queries.mark_session_error(cursor, str(error))
                status = queries.session_status(cursor)
            return jsonify({'status': 'success', 'alive': False,
                            'error': str(error), 'session': status}), 200
        with db._get_cursor() as cursor:
            queries.mark_session_ok(cursor)
            status = queries.session_status(cursor)
        return jsonify({'status': 'success', 'alive': True,
                        'account': checked.get('account'),
                        'parks_count': checked.get('parks_count'),
                        'session': status}), 200

    return bp


def _safe_name(filename):
    """Кириллицу оставляем: secure_filename её съедает, и «Нет провайдера.xlsx»
    превращается в «.xlsx» — в списке выгрузок такое имя бесполезно."""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', '_', str(filename or '')).strip()
    name = name.replace('..', '.')
    return name[:200] or 'file.xlsx'
