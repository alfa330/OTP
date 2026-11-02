"""Раздел «tez»: ручки /api/tez_leads — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_tez_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, g, jsonify, request, send_file
from database import db
from datetime import date as dt_date
from datetime import datetime
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Alignment
from openpyxl.styles import Font
from openpyxl.styles import PatternFill
from openpyxl.utils import get_column_letter
from werkzeug.utils import secure_filename
from zipfile import ZIP_DEFLATED
from zipfile import ZipFile
from zoneinfo import ZoneInfo
import logging
import os
import re
import uuid


TEZ_LEADS_BATCH_REASON_MAX_LENGTH = 2000

def _tez_leads_display_filename(raw_filename, fallback_filename='leads.xlsx'):
    """Безопасное отображаемое имя файла без потери кириллицы.

    secure_filename нужен для проверки расширения, но удаляет кириллицу и для
    обычного «Лиды Алматы.xlsx» оставляет общий fallback. Сам файл на диск здесь
    не пишется, поэтому для аудита сохраняем очищенный basename исходного имени.
    """
    fallback = str(fallback_filename or 'leads.xlsx').strip() or 'leads.xlsx'
    basename = str(raw_filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    basename = re.sub(r'[\x00-\x1f\x7f]+', '', basename)
    basename = ' '.join(basename.split()).strip(' .')
    if not basename:
        basename = fallback
    if len(basename) > 255:
        root, ext = os.path.splitext(basename)
        ext = ext[:20]
        basename = root[:max(1, 255 - len(ext))] + ext
    return basename

def _tez_leads_excel_text_cell(ws, row, column, value):
    """Записывает текст как текст, не позволяя Excel выполнить его как формулу."""
    cell = ws.cell(
        row=row,
        column=column,
        value='' if value is None else str(value),
    )
    # openpyxl автоматически помечает строки с "=" как formula. Источник и
    # ФИО приходят из загруженного пользователем файла, поэтому тип задаём явно.
    cell.data_type = 's'
    # Формат ячейки — «Текстовый». Без него у колонки остаётся «Общий», и Excel
    # при любой правке или пересохранении превращает телефон 77012345678 в число
    # (а длинные номера — в экспоненту), теряя ведущие нули и ломая поиск.
    cell.number_format = '@'
    return cell

def _tez_leads_call_bucket(call_at, year, month):
    """'month' | 'prev' | None — в какую пару колонок отчёта ложится звонок.

    Звонок отчётного месяца идёт в «отчётные» колонки, звонок прошлого — только
    если попал в окно последних 7 дней (то самое, по которому начисляется
    успешка на стыке месяцев, см. call_window_for_period). Звонок раньше окна не
    показываем нигде: успешку он дать не может, а в колонке читался бы как
    основание для неё — почему именно он не прошёл, объясняет «Правило».
    """
    if not call_at:
        return None
    from tez.op_leads import call_window_for_period

    try:
        day = datetime.fromisoformat(str(call_at)).date()
    except ValueError:
        return None
    year, month = int(year), int(month)
    if (day.year, day.month) == (year, month):
        return 'month'
    window_start, _ = call_window_for_period(year, month)
    if window_start <= day < dt_date(year, month, 1):
        return 'prev'
    return None

def _tez_leads_period_from_request():
    """year/month из запроса; по умолчанию — текущий месяц по Алматы."""
    now = datetime.now(ZoneInfo('Asia/Almaty'))
    try:
        year = int(request.values.get('year') or now.year)
        month = int(request.values.get('month') or now.month)
    except (TypeError, ValueError):
        return None, None
    if not (2000 <= year <= 2100) or not (1 <= month <= 12):
        return None, None
    return year, month

def _tez_leads_batch_uuid(batch_id):
    """Валидирует id загрузки как UUID — иначе psycopg упадёт на неверном типе."""
    try:
        return str(uuid.UUID(str(batch_id)))
    except (TypeError, ValueError, AttributeError):
        return None

def _tez_leads_parse_batch_reason(require_reason=False):
    """Строго читает JSON-object и валидирует audit reason для batch-действия."""
    raw_body = request.get_data(cache=True)
    if not raw_body:
        payload = {}
    else:
        if not request.is_json:
            return None, (jsonify({"error": "Ожидается JSON-объект"}), 400)
        try:
            payload = request.get_json(silent=False)
        except Exception:
            return None, (jsonify({"error": "Некорректный JSON"}), 400)
        if not isinstance(payload, dict):
            return None, (jsonify({"error": "Тело запроса должно быть JSON-объектом"}), 400)

    if 'reason' not in payload:
        if require_reason:
            return None, (jsonify({"error": "reason is required"}), 400)
        return '', None

    raw_reason = payload.get('reason')
    if not isinstance(raw_reason, str):
        return None, (jsonify({"error": "reason должен быть строкой"}), 400)
    if len(raw_reason) > TEZ_LEADS_BATCH_REASON_MAX_LENGTH:
        return None, (jsonify({
            "error": f"reason не должен превышать {TEZ_LEADS_BATCH_REASON_MAX_LENGTH} символов"
        }), 400)

    reason = raw_reason.strip()
    if require_reason and not reason:
        return None, (jsonify({"error": "reason не может быть пустой"}), 400)
    return reason, None


def build_tez_blueprint(
    *,
    ROLE_HIERARCHY,
    TEZ_LEADS_MAX_FILE_SIZE_MB,
    _department_scope_id_for_requester,
    _excel_suppress_number_as_text_warning,
    _get_authenticated_requester,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_global_admin_requester,
    _is_supervisor_role,
    _normalize_user_role,
    _status_import_secure_filename_and_ext,
    _tez_leads_binotel_client,
    _tez_leads_first_orders_client,
    _tez_leads_import_rows,
    _tez_op_operator_resolver,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('tez', __name__, url_prefix='/api')

    TEZ_LEADS_MAX_FILE_SIZE_BYTES = TEZ_LEADS_MAX_FILE_SIZE_MB * 1024 * 1024

    def _tez_leads_require_manager():
        """Права: база лидов — инструмент СВ и админов. Возвращает (user_id, error_response)."""
        requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
        if not requester_id:
            return None, (jsonify({"error": "Unauthorized"}), 401)
        requester_id = int(requester_id)
        user_data = db.get_user(id=requester_id)
        if not user_data:
            return None, (jsonify({"error": "User not found"}), 404)
        if not (_is_admin_role(user_data[3]) or _is_supervisor_role(user_data[3])):
            return None, (jsonify({"error": "Forbidden"}), 403)
        return requester_id, None

    def _tez_leads_require_batch_manager(batch_id):
        """Проверяет manager-role и department scope конкретной загрузки.

    Глобальный администратор может управлять любой загрузкой. Остальные менеджеры
    (СВ либо формальный глава отдела) — только загрузкой ровно своего отдела.
    Исторические загрузки без department_id доступны только глобальному админу.
    """
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return None, None, None, (jsonify({"error": message}), status_code)

        requester_role = _normalize_user_role(
            requester.get('role') if isinstance(requester, dict) else requester[3]
        )
        headed_department_id = _headed_department_id(requester_id)
        is_global_admin = _is_global_admin_requester(requester_role, requester_id)
        is_department_manager = (
            _is_supervisor_role(requester_role)
            or headed_department_id is not None
        )
        if not (is_global_admin or is_department_manager):
            return None, None, None, (jsonify({"error": "Forbidden"}), 403)

        uid = _tez_leads_batch_uuid(batch_id)
        if not uid:
            return None, None, None, (jsonify({"error": "Некорректный id загрузки"}), 400)

        try:
            metadata = db.get_tez_lead_batch_metadata(uid)
        except Exception:
            logging.exception('tez_leads: не удалось проверить загрузку %s', uid)
            return None, None, None, (jsonify({"error": "Не удалось проверить загрузку"}), 500)
        if metadata is None:
            return None, None, None, (jsonify({"error": "Загрузка не найдена"}), 404)
        if not isinstance(metadata, dict):
            logging.error('tez_leads: некорректные метаданные загрузки %s', uid)
            return None, None, None, (jsonify({"error": "Не удалось проверить загрузку"}), 500)

        if not is_global_admin:
            batch_department_id = metadata.get('department_id')
            if batch_department_id is None:
                return None, None, None, (jsonify({"error": "Forbidden"}), 403)
            try:
                scope_department_id = _department_scope_id_for_requester(requester_id)
                same_department = (
                    scope_department_id is not None
                    and int(scope_department_id) == int(batch_department_id)
                )
            except (TypeError, ValueError):
                same_department = False
            except Exception:
                logging.exception('tez_leads: не удалось определить отдел пользователя %s', requester_id)
                return None, None, None, (jsonify({"error": "Не удалось проверить права"}), 500)
            if not same_department:
                return None, None, None, (jsonify({"error": "Forbidden"}), 403)

        return requester_id, uid, metadata, None

    @bp.route('/tez_leads/upload', methods=['POST'])
    @require_api_key
    def tez_leads_upload():
        """Загрузка базы лидов (fio + phone) в помесячную накопительную базу.

    Проверка «кто уже работает» запускается сразу, но в фоне: файл на несколько
    тысяч строк — это десятки запросов к TEZ APP, и держать на них HTTP-ответ
    незачем. Прогресс виден по check_status загрузки.
    """
        requester_id, error = _tez_leads_require_manager()
        if error:
            return error

        year, month = _tez_leads_period_from_request()
        if not year:
            return jsonify({"error": "Некорректный период (year/month)"}), 400

        file_storage = request.files.get('file')
        if not file_storage:
            return jsonify({"error": "file is required"}), 400

        safe_file_name, file_ext = _status_import_secure_filename_and_ext(
            file_storage.filename, fallback_filename='leads.xlsx'
        )
        file_name = _tez_leads_display_filename(
            file_storage.filename,
            fallback_filename=safe_file_name,
        )
        if file_ext not in ('.csv', '.xlsx', '.xlsm'):
            return jsonify({"error": "Поддерживаются только .csv, .xlsx и .xlsm"}), 400

        raw_bytes = file_storage.read(TEZ_LEADS_MAX_FILE_SIZE_BYTES + 1)
        if not raw_bytes:
            return jsonify({"error": "Файл пустой"}), 400
        if len(raw_bytes) > TEZ_LEADS_MAX_FILE_SIZE_BYTES:
            return jsonify({"error": f"Файл слишком большой. Лимит: {TEZ_LEADS_MAX_FILE_SIZE_MB} MB"}), 413

        from tez import lead_service as tez_lead_service
        try:
            rows = tez_lead_service.parse_leads_file(raw_bytes, file_ext)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            logging.exception('tez_leads: разбор файла не удался')
            return jsonify({"error": "Не удалось разобрать файл"}), 400

        department_id = request.values.get('department_id')
        try:
            result = _tez_leads_import_rows(
                requester_id,
                int(department_id) if department_id else None,
                year, month, file_name, rows
            )
        except Exception:
            logging.exception('tez_leads: импорт строк не удался')
            return jsonify({"error": "Не удалось сохранить базу лидов"}), 500
        return jsonify(result)

    @bp.route('/tez_leads/stats', methods=['GET'])
    @require_api_key
    def tez_leads_stats():
        """Воронка по базе месяца + рейтинг операторов + успешки по дням + загрузки."""
        requester_id, error = _tez_leads_require_manager()
        if error:
            return error
        year, month = _tez_leads_period_from_request()
        if not year:
            return jsonify({"error": "Некорректный период (year/month)"}), 400
        # Группа сужает только показатели, привязанные к оператору (рейтинг, по дням):
        # база лидов грузится на весь отдел, поэтому воронка и загрузки — отделу целиком.
        group_raw = request.args.get('group_id')
        try:
            group_id = int(group_raw) if group_raw else None
        except (TypeError, ValueError):
            group_id = None
        try:
            return jsonify({
                "year": year,
                "month": month,
                "group_id": group_id,
                "funnel": db.get_tez_lead_funnel(year, month),
                "operators": db.get_tez_operator_successes(year, month, group_id=group_id),
                "by_day": db.get_tez_successes_by_day(year, month, group_id=group_id),
                "by_operator_day": db.get_tez_successes_operator_day(year, month, group_id=group_id),
                "batches": db.list_tez_lead_batches(year, month),
            })
        except Exception:
            logging.exception('tez_leads: не удалось собрать статистику')
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/tez_leads/day', methods=['GET'])
    @require_api_key
    def tez_leads_day():
        """Успешки одного дня — раскрытие карточки в разбивке «По дням»."""
        requester_id, error = _tez_leads_require_manager()
        if error:
            return error
        year, month = _tez_leads_period_from_request()
        if not year:
            return jsonify({"error": "Некорректный период (year/month)"}), 400

        raw_date = (request.args.get('date') or '').strip()
        try:
            success_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({"error": "Некорректная дата (ожидается YYYY-MM-DD)"}), 400
        if (success_date.year, success_date.month) != (year, month):
            return jsonify({"error": "Дата вне выбранного месяца"}), 400

        group_raw = request.args.get('group_id')
        try:
            group_id = int(group_raw) if group_raw else None
        except (TypeError, ValueError):
            group_id = None

        try:
            rows = db.get_tez_successes_for_day(year, month, success_date, group_id=group_id)
        except Exception:
            logging.exception('tez_leads: не удалось собрать успешки дня')
            return jsonify({"error": "Internal server error"}), 500
        return jsonify({"date": success_date.isoformat(), "group_id": group_id, "successes": rows})

    @bp.route('/tez_leads/detail', methods=['GET'])
    @require_api_key
    def tez_leads_detail():
        """Детализация по лидам со связкой звонок -> поездка."""
        requester_id, error = _tez_leads_require_manager()
        if error:
            return error
        year, month = _tez_leads_period_from_request()
        if not year:
            return jsonify({"error": "Некорректный период (year/month)"}), 400
        operator_id = request.args.get('operator_id')
        status = request.args.get('status') or None
        search = request.args.get('search') or None

        def _int_arg(name, default, lo, hi):
            try:
                return max(lo, min(hi, int(request.args.get(name, default))))
            except (TypeError, ValueError):
                return default

        page = _int_arg('page', 1, 1, 10_000_000)
        page_size = _int_arg('page_size', 50, 1, 500)
        offset = (page - 1) * page_size

        try:
            op_id = int(operator_id) if operator_id else None
            total = db.count_tez_leads_detail(year, month, status=status, operator_id=op_id, search=search)
            rows = db.get_tez_leads_detail(
                year, month,
                status=status, operator_id=op_id, search=search,
                limit=page_size, offset=offset,
            )
        except Exception:
            logging.exception('tez_leads: не удалось собрать детализацию')
            return jsonify({"error": "Internal server error"}), 500
        return jsonify({
            "year": year, "month": month,
            "leads": rows,
            "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if page_size else 1,
        })

    @bp.route('/tez_leads/export', methods=['GET'])
    @require_api_key
    def tez_leads_export():
        """Выгрузка отчёта в Excel — для разбора спорных случаев с операторами.

    Лист один, и на нём весь периметр месяца: своя база (успешные и нет) плюс
    лиды прошлого месяца, которые дорабатываются в этом. У перенесённой строки
    все колонки пересчитаны на ОТЧЁТНЫЙ месяц (см. db.get_tez_leads_report),
    поэтому видно, стал перенесённый лид успешкой здесь или нет, и число
    успешек в файле совпадает с числом в разделе. Раньше перенос уезжал на
    отдельный лист со своим прошлым статусом: в разделе за август стояло 245
    успешек, в файле — 134, а недостающие 111 лежали на листе июля как обычная
    база без признака зачёта.
    """
        requester_id, error = _tez_leads_require_manager()
        if error:
            return error
        year, month = _tez_leads_period_from_request()
        if not year:
            return jsonify({"error": "Некорректный период (year/month)"}), 400

        status_labels = {
            'new': 'Новый',
            'in_progress': 'В работе',
            'already_working': 'Уже работающий',
            'success': 'Успешка',
            'not_counted': 'Не засчитана',
            # Статуса доработки у строки нет: либо пересчёт после появления
            # carry_status по этому периоду ещё не проходил, либо период закрыт
            # и автоматически уже не пересчитывается. Пустая ячейка читалась бы
            # как потерянный статус, поэтому состояние названо и подсказывает
            # выход — кнопку «Сверка» в разделе. «Ещё не пересчитан» тут писать
            # нельзя: пересчёт периода мог идти много раз, просто складывать
            # результат было некуда.
            'carry_pending': 'Статус не рассчитан',
            # Статус говорит «успешка», а записи об успешке за период нет. Успешкой
            # такую строку не называем (иначе в файле их станет больше, чем в
            # разделе), но и не прячем: лечится кнопкой «Сверка».
            'stale_success': 'Требует сверки',
        }
        rule_labels = {
            'same_month': 'Звонок в месяце поездки',
            'prev_month_last7': 'Звонок в последние 7 дней прошлого месяца',
            'reactivated_30d': 'Не работал 30+ дней и вернулся после звонка',
            'no_call_before_trip': 'Нет звонка до поездки',
            'call_before_last7': 'Звонок раньше последних 7 дней прошлого месяца',
            'gap_under_30d': 'Заказ был меньше 30 дней назад — водитель не уходил',
            'no_call_after_last_order': 'Звонка между последним и новым заказом не было',
            'active_prev_month': 'Были заказы в прошлом месяце — уже работал (старое правило)',
            # Коды прежнего правила (окно было на стороне поездки) — остались на
            # закрытых месяцах, без лейблов старые выгрузки читались бы кодами.
            'prev_month_week1': 'Звонок в прошлом месяце, поездка до 7-го (старое правило)',
            'trip_after_day7': 'Поездка позже 7-го числа (старое правило)',
        }

        try:
            rows = db.get_tez_leads_report(year, month, limit=50000)
        except Exception:
            logging.exception('tez_leads: экспорт не удался')
            return jsonify({"error": "Internal server error"}), 500

        # Месяц прописью — для колонки «Месяц базы».
        MONTHS_RU = ('Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль',
                     'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь')
        # «Месяц базы» — первой колонкой и у КАЖДОЙ строки: на одном листе это
        # единственное, что отличает свою базу от переноса. Пометку «(перенос)»
        # ставим прямо в значение, чтобы читателю не приходилось помнить, что
        # прошлый месяц на этом листе и означает перенос.
        headers = ['Месяц базы',
                   'ФИО', 'Телефон', 'Источник', 'Статус', 'Правило', 'Загрузок',
                   'Поездка в прошлом месяце', 'Поездка в отчётном месяце',
                   'Оператор',
                   'Звонок в прошлом месяце', 'Время разговора в прошлом месяце',
                   'Звонок в отчётном месяце', 'Время разговора в отчётном месяце',
                   'Дата успешки']
        widths = [22,
                  34, 16, 34, 18, 40, 10, 24, 24, 28, 22, 26, 22, 26, 14]

        wb = Workbook()
        sheet = wb.active
        sheet.title = f'Лиды {month:02d}.{year}'[:31]
        for col, title in enumerate(headers, start=1):
            cell = sheet.cell(row=1, column=col, value=title)
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill(fill_type='solid', fgColor='1F2937')
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

        for idx, row in enumerate(rows, start=2):
            base_month = int(row.get('base_month') or month)
            base_year = int(row.get('base_year') or year)
            base_label = f'{MONTHS_RU[base_month - 1]} {base_year}'
            if row.get('is_carried'):
                base_label += ' (перенос)'
            _tez_leads_excel_text_cell(sheet, idx, 1, base_label)
            _tez_leads_excel_text_cell(sheet, idx, 2, row['full_name'])
            _tez_leads_excel_text_cell(sheet, idx, 3, row['phone'])
            _tez_leads_excel_text_cell(sheet, idx, 4, row.get('source_file_name') or '')
            _tez_leads_excel_text_cell(
                sheet, idx, 5, status_labels.get(row['status'], row['status'])
            )
            _tez_leads_excel_text_cell(
                sheet, idx, 6,
                rule_labels.get(row['status_rule'], row['status_rule'] or ''),
            )
            sheet.cell(row=idx, column=7, value=row['upload_count'])
            _tez_leads_excel_text_cell(
                sheet, idx, 8,
                (row.get('prev_month_first_order_at') or '').replace('T', ' ')[:19],
            )
            _tez_leads_excel_text_cell(
                sheet, idx, 9,
                (row.get('month_first_order_at') or row.get('first_order_at') or '')
                .replace('T', ' ')[:19],
            )
            _tez_leads_excel_text_cell(sheet, idx, 10, row['operator_name'])
            # Звонок один, но лежит он в паре колонок своего месяца: так по строке
            # видно, привлечение это внутри месяца или на стыке с прошлым. Месяц
            # один на весь лист — отчётный, — и у переноса тоже: его строка целиком
            # пересчитана на этот месяц, поэтому звонок зачёта встаёт в те же
            # колонки, что и у обычной успешки.
            bucket = _tez_leads_call_bucket(row['call_at'], year, month)
            call_text = (row['call_at'] or '').replace('T', ' ')[:19]
            duration_seconds = row.get('talk_duration_seconds')
            for target, call_column in (('prev', 11), ('month', 13)):
                matched = bucket == target
                _tez_leads_excel_text_cell(
                    sheet, idx, call_column, call_text if matched else ''
                )
                # Длительность — обычное число секунд, а не формат времени: по
                # решению владельца отчёт считают и фильтруют в секундах (порог
                # успешки тоже задан в секундах), а «00:44» пришлось бы
                # конвертировать.
                has_duration = matched and duration_seconds is not None
                duration_cell = sheet.cell(
                    row=idx,
                    column=call_column + 1,
                    value=max(int(duration_seconds), 0) if has_duration else '',
                )
                if has_duration:
                    duration_cell.number_format = '0'
            _tez_leads_excel_text_cell(sheet, idx, 15, row['success_date'] or '')

        for col, width in enumerate(widths, start=1):
            sheet.column_dimensions[sheet.cell(row=1, column=col).column_letter].width = width

        stream = BytesIO()
        wb.save(stream)
        stream.seek(0)
        if rows:
            phone_letter = get_column_letter(headers.index('Телефон') + 1)
            stream = _excel_suppress_number_as_text_warning(
                stream, f'{phone_letter}2:{phone_letter}{len(rows) + 1}'
            )
        return send_file(
            stream,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'tez_leads_{year}_{month:02d}.xlsx'
        )

    @bp.route('/tez_leads/recompute', methods=['POST'])
    @require_api_key
    def tez_leads_recompute():
        """Ручной прогон сверки за период (то же, что делает ночная джоба)."""
        requester_id, error = _tez_leads_require_manager()
        if error:
            return error
        year, month = _tez_leads_period_from_request()
        if not year:
            return jsonify({"error": "Некорректный период (year/month)"}), 400

        from tez import lead_service as tez_lead_service
        try:
            first_orders_client = _tez_leads_first_orders_client()
            binotel_client = _tez_leads_binotel_client()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 503

        try:
            resolve_operator = _tez_op_operator_resolver()
            # Один прогон на процесс: параллельный клик (или клик во время ночной
            # джобы) не сломал бы данные — их защищает advisory-лок периода, — но
            # заново потратил бы лимиты Binotel и TEZ APP.
            with tez_lead_service.sync_lock():
                orders = tez_lead_service.sync_first_orders(db, year, month, first_orders_client)
                # Перенос: у лидов прошлого месяца спрашиваем поездку за этот.
                carry = tez_lead_service.sync_carry_first_orders(
                    db, year, month, first_orders_client
                )
                # Зеркало звонков ограничено по времени: за клик добираем сколько
                # успеваем, остаток закроет ночная джоба (в ответе — сколько дней ещё
                # не перекачано, чтобы это было видно, а не молча недосчитано).
                mirror = tez_lead_service.sync_calls_for_period(
                    db, year, month, binotel_client, resolve_operator
                )
                calls = tez_lead_service.sync_calls_for_converted(
                    db, year, month, binotel_client, resolve_operator
                )
                # Локальный шаг без запросов в Binotel: возвращает привязку звонкам,
                # у которых провайдер забыл сотрудника (удалён при увольнении).
                restored = tez_lead_service.reattribute_calls_without_operator(
                    db, year, month, resolve_operator
                )
                outcomes = tez_lead_service.recompute_outcomes(db, year, month)
        except tez_lead_service.SyncBusy as exc:
            # Отдельно от 502 ниже: это не сбой, а «уже идёт», и повторять запрос
            # бессмысленно, пока прогон не закончится.
            return jsonify({"error": str(exc)}), 409
        except RuntimeError as exc:
            # Понятные причины (Cloudflare-блок, нет токена и т.п.) показываем как есть.
            logging.warning('tez_leads: ручной пересчёт прерван: %s', exc)
            return jsonify({"error": str(exc)}), 502
        except Exception:
            logging.exception('tez_leads: ручной пересчёт не удался')
            return jsonify({"error": "Не удалось выполнить сверку"}), 502

        return jsonify({
            "first_orders": orders,
            "carry_first_orders": carry,
            "calls_mirror": mirror,
            "calls": calls,
            "restored_calls": restored,
            "outcomes": outcomes,
        })

    @bp.route('/tez_leads/batches/<batch_id>/delete', methods=['POST'])
    @require_api_key
    def tez_leads_batch_delete(batch_id):
        """Удаляет загруженную базу: лиды уходят в архив, история остаётся.

    Удаление обратимо — см. /restore. Причину просим сохранять всегда: по ней
    потом видно, была это ошибочная загрузка или чистка дубля.
    """
        requester_id, uid, _, error = _tez_leads_require_batch_manager(batch_id)
        if error:
            return error

        reason, error = _tez_leads_parse_batch_reason(require_reason=True)
        if error:
            return error

        try:
            result = db.delete_tez_lead_batch(uid, requester_id, reason=reason)
        except Exception:
            logging.exception('tez_leads: удаление загрузки %s не удалось', uid)
            return jsonify({"error": "Не удалось удалить базу"}), 500

        if result is None:
            return jsonify({"error": "Загрузка не найдена"}), 404
        if result.get('already_deleted'):
            return jsonify({"error": "Эта загрузка уже удалена"}), 409

        logging.info('tez_leads: пользователь %s удалил загрузку %s '
                     '(лидов удалено %s, оставлено %s, успешек %s)',
                     requester_id, uid, result.get('leads_removed'), result.get('leads_kept'),
                     result.get('successes_removed'))
        return jsonify(result)

    @bp.route('/tez_leads/batches/<batch_id>/restore', methods=['POST'])
    @require_api_key
    def tez_leads_batch_restore(batch_id):
        """Откатывает удаление базы — лиды и успешки возвращаются из архива."""
        requester_id, uid, _, error = _tez_leads_require_batch_manager(batch_id)
        if error:
            return error

        reason, error = _tez_leads_parse_batch_reason(require_reason=False)
        if error:
            return error

        try:
            result = db.restore_tez_lead_batch(uid, requester_id, reason=reason)
        except Exception:
            logging.exception('tez_leads: восстановление загрузки %s не удалось', uid)
            return jsonify({"error": "Не удалось восстановить базу"}), 500

        if result is None:
            return jsonify({"error": "Загрузка не найдена"}), 404
        if result.get('not_deleted'):
            return jsonify({"error": "Эта загрузка не была удалена"}), 409

        logging.info('tez_leads: пользователь %s восстановил загрузку %s (лидов %s, успешек %s, слито %s)',
                     requester_id, uid, result.get('leads_restored'),
                     result.get('successes_restored'), result.get('leads_merged'))
        return jsonify(result)

    @bp.route('/tez_leads/batches/<batch_id>/history', methods=['GET'])
    @require_api_key
    def tez_leads_batch_history(batch_id):
        """Кто и когда удалял/восстанавливал эту загрузку."""
        _, uid, _, error = _tez_leads_require_batch_manager(batch_id)
        if error:
            return error

        try:
            return jsonify({"batch_id": uid, "events": db.get_tez_lead_batch_events(uid)})
        except Exception:
            logging.exception('tez_leads: история загрузки %s недоступна', uid)
            return jsonify({"error": "Internal server error"}), 500

    return bp
