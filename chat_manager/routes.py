"""Раздел «chat_manager»: ручки /api/chat_manager — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_chat_manager_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, g, jsonify, request, send_file
from database import LowRatingAttributionConflict
from database import db
from datetime import date as dt_date
from datetime import datetime
from datetime import timedelta
from io import BytesIO
from io import StringIO
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from werkzeug.utils import secure_filename
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from zoneinfo import ZoneInfo
import csv
import json
import jwt
import logging
import os
import re
import requests
import threading
import uuid


def _is_low_rating_review_id(review_id):
    """id низкой оценки — UUID. Без проверки нечисловой id улетает в запрос и
    роняет его 500-й ошибкой с трейсом в логах (эндпоинт переписки открыт
    операторам, а не только проверяющим)."""
    try:
        uuid.UUID(str(review_id))
        return True
    except (ValueError, AttributeError, TypeError):
        return False

def _low_rating_chat_request_id(raw_payload):
    """id заявки с самим разговором. Оценка приезжает отдельной «заявкой»-опросом
    (request_id — 3 служебных сообщения: вопрос, цифра, спасибо), а оцениваемый
    разговор лежит в valuation_request_id — проверено живьём на диалоге 19332881."""
    raw = raw_payload if isinstance(raw_payload, dict) else {}
    for key in ('valuation_request_id', 'request_id'):
        try:
            value = int(str(raw.get(key) or '').strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None

LOW_RATING_PARTICIPANT_MESSAGE_TYPES = ('to_client', 'comment', 'from_client')

def _c2d_api_date(day_iso):
    """'2026-08-10' → '10-08-2026': другого формата /v1/messages не принимает."""
    text = str(day_iso or '')[:10]
    return f"{text[8:10]}-{text[5:7]}-{text[0:4]}"

def _low_rating_verdict_label(row):
    """Возвращает (текст, категория) итогового статуса строки низкой оценки."""
    final_status = row.get('final_status')
    if final_status == 'valid':
        return 'Обоснованно', 'valid'
    if final_status == 'invalid':
        return 'Необоснованно (исключена)', 'invalid'
    if row.get('review_state') == 'conflict':
        return 'Спорные', 'conflict'
    return 'На проверке', 'pending'

def _low_rating_status_ru(status):
    key = str(status or '').strip().lower()
    if key == 'valid':
        return 'Обоснованно'
    if key == 'invalid':
        return 'Необоснованно'
    return 'На проверке'

def _low_rating_attributed_names(row):
    """Кому засчитана итоговая обоснованная оценка — для колонки выгрузки."""
    if row.get('final_status') != 'valid':
        return ''
    return ', '.join(
        str(op.get('name') or '').strip()
        for op in (row.get('attributed_operators') or [])
        if str(op.get('name') or '').strip()
    )

def _low_rating_fmt_dt(value):
    text = str(value or '').strip()
    if not text:
        return ''
    return text.replace('T', ' ')[:16]

def _build_low_rating_reviews_workbook(rows, summary, period_start, period_end, status_filter, search):
    """Строит .xlsx (лист с детализацией + лист сводки) и возвращает bytes."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    thin = Side(style='thin', color='E2E8F0')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill('solid', fgColor='1F2937')
    header_font = Font(bold=True, color='FFFFFF', size=11)
    title_font = Font(bold=True, size=15, color='0F172A')
    label_font = Font(bold=True, color='334155')
    wrap = Alignment(wrap_text=True, vertical='top')
    center = Alignment(horizontal='center', vertical='center')
    # Согласовано с UI: необоснованные (исключены) — зелёные, обоснованные (учтены) — красные.
    cat_fill = {
        'valid': PatternFill('solid', fgColor='FEE2E2'),
        'invalid': PatternFill('solid', fgColor='DCFCE7'),
        'conflict': PatternFill('solid', fgColor='FEF3C7'),
        'pending': PatternFill('solid', fgColor='F1F5F9'),
    }
    cat_font = {
        'valid': Font(bold=True, color='B91C1C'),
        'invalid': Font(bold=True, color='15803D'),
        'conflict': Font(bold=True, color='B45309'),
        'pending': Font(color='64748B'),
    }
    source_ru = {'consensus': 'Совпадение', 'manager': 'Руководитель', 'head': 'Руководитель'}

    wb = Workbook()

    # ── Лист 1: детализация ──────────────────────────────────────────────
    ws = wb.active
    ws.title = 'Низкие оценки'
    # «Засчитана» — кому оценка идёт в статистику по итогу разбора (задача #286):
    # Chat2Desk отдаёт её тому, кто закрыл чат, а проверяющие могут засчитать
    # её другому менеджеру или сразу нескольким. Заполнена только у итогового
    # «обоснованно»: необоснованная не засчитывается никому, а пока разбор
    # идёт, оценка на том, кто в колонке «Оператор».
    headers = [
        'Дата', 'Оператор', 'Засчитана', 'Отдел', 'Направление', 'Телефон', 'Таксопарк',
        'Оценка', 'Итог', 'Источник итога', 'Проверок',
        'Вердикты проверяющих', 'Комментарий итога', 'Комментарий клиента'
    ]
    center_cols = (8, 11)
    wrap_cols = (3, 12, 13, 14)
    verdict_col = 9
    for col_idx, title in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=title)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = center if col_idx in center_cols else Alignment(vertical='center')

    for r_idx, row in enumerate(rows, start=2):
        verdict_text, verdict_cat = _low_rating_verdict_label(row)
        entries = row.get('review_entries') or []
        verdict_parts = []
        for entry in entries:
            piece = f"{entry.get('reviewer_name') or 'Проверяющий'}: {_low_rating_status_ru(entry.get('status'))}"
            if entry.get('comment'):
                piece += f" — {entry.get('comment')}"
            verdict_parts.append(piece)
        values = [
            _low_rating_fmt_dt(row.get('rated_at') or row.get('day')),
            row.get('operator_name') or '',
            _low_rating_attributed_names(row),
            row.get('department_name') or '',
            row.get('direction_name') or '',
            row.get('phone_number') or row.get('phone_normalized') or '',
            row.get('taxi_park') or '',
            round(float(row.get('score') or 0), 2),
            verdict_text,
            source_ru.get(row.get('final_source'), '—'),
            int(row.get('review_count') or 0),
            '\n'.join(verdict_parts),
            row.get('final_comment') or '',
            row.get('client_comment') or row.get('rating_text') or '',
        ]
        for c_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=value)
            cell.border = border
            if c_idx in wrap_cols:
                cell.alignment = wrap
            elif c_idx in center_cols:
                cell.alignment = center
            else:
                cell.alignment = Alignment(vertical='top')
            if c_idx == verdict_col:
                cell.fill = cat_fill.get(verdict_cat)
                cell.font = cat_font.get(verdict_cat)

    widths = [17, 24, 26, 18, 18, 16, 18, 9, 22, 15, 10, 46, 30, 36]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, len(rows) + 1)}"

    # ── Лист 2: сводка ───────────────────────────────────────────────────
    ws2 = wb.create_sheet('Сводка')
    ws2.column_dimensions['A'].width = 26
    for col in ('B', 'C', 'D', 'E', 'F', 'G', 'H', 'I'):
        ws2.column_dimensions[col].width = 15
    ws2['A1'] = 'Сводка по низким оценкам Chat2Desk'
    ws2['A1'].font = title_font

    excluded_sum = sum(float(r.get('score') or 0) for r in rows if r.get('final_status') == 'invalid')
    info_rows = [
        ('Период', f"{period_start} — {period_end}"),
        ('Фильтр', str(status_filter or 'all')),
        ('Поиск', str(search or '—')),
        ('Сформировано', datetime.now().strftime('%Y-%m-%d %H:%M')),
    ]
    r = 3
    for label, value in info_rows:
        ws2.cell(row=r, column=1, value=label).font = label_font
        ws2.cell(row=r, column=2, value=value)
        r += 1

    r += 1
    metric_rows = [
        ('Всего низких оценок', int(summary.get('total') or 0), None),
        ('Обоснованные (учтены)', int(summary.get('valid') or 0), 'valid'),
        ('Необоснованные (исключены)', int(summary.get('invalid') or 0), 'invalid'),
        ('Спорные (ждут руководителя)', int(summary.get('conflict') or 0), 'conflict'),
        ('На проверке', int(summary.get('pending') or 0), 'pending'),
        ('Исключено баллов (Σ)', round(excluded_sum, 2), 'invalid'),
    ]
    for label, value, cat in metric_rows:
        lcell = ws2.cell(row=r, column=1, value=label)
        lcell.font = label_font
        lcell.border = border
        vcell = ws2.cell(row=r, column=2, value=value)
        vcell.border = border
        vcell.alignment = center
        if cat:
            vcell.fill = cat_fill.get(cat)
            vcell.font = cat_font.get(cat)
        r += 1

    # Детализация по операторам. Итоговая обоснованная — у тех, кому она
    # засчитана (каждому целиком, как и в статистике), остальные — у того,
    # кому оценку отдал Chat2Desk.
    by_operator = {}
    for row in rows:
        _, cat = _low_rating_verdict_label(row)
        owners = [{'id': row.get('operator_id'), 'name': row.get('operator_name')}]
        if row.get('final_status') == 'valid' and row.get('attributed_operators'):
            owners = row.get('attributed_operators')
        for owner in owners:
            key = owner.get('id') or owner.get('name')
            agg = by_operator.get(key)
            if agg is None:
                agg = {
                    'name': owner.get('name') or '—',
                    'department': row.get('department_name') or '',
                    'total': 0, 'valid': 0, 'invalid': 0, 'pending': 0, 'conflict': 0,
                    'excluded': 0.0, 'score_sum': 0.0,
                }
                by_operator[key] = agg
            agg['total'] += 1
            agg['score_sum'] += float(row.get('score') or 0)
            agg[cat] = agg.get(cat, 0) + 1
            if row.get('final_status') == 'invalid':
                agg['excluded'] += float(row.get('score') or 0)

    r += 2
    ws2.cell(row=r, column=1, value='Детализация по операторам').font = Font(bold=True, size=12, color='0F172A')
    r += 1
    op_headers = ['Оператор', 'Отдел', 'Всего', 'Обоснованно', 'Необоснованно', 'На проверке', 'Спорные', 'Σ исключ. балла', 'Средний балл']
    for c_idx, title in enumerate(op_headers, start=1):
        cell = ws2.cell(row=r, column=c_idx, value=title)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = center if c_idx >= 3 else Alignment(vertical='center')
    r += 1
    for agg in sorted(by_operator.values(), key=lambda a: (-a['total'], a['name'])):
        avg_score = round(agg['score_sum'] / agg['total'], 2) if agg['total'] else 0
        op_values = [
            agg['name'], agg['department'], agg['total'], agg['valid'], agg['invalid'],
            agg['pending'], agg['conflict'], round(agg['excluded'], 2), avg_score
        ]
        for c_idx, value in enumerate(op_values, start=1):
            cell = ws2.cell(row=r, column=c_idx, value=value)
            cell.border = border
            cell.alignment = center if c_idx >= 3 else Alignment(vertical='top')
            if c_idx == 4 and agg['valid']:
                cell.fill = cat_fill['valid']
                cell.font = cat_font['valid']
            if c_idx == 5 and agg['invalid']:
                cell.fill = cat_fill['invalid']
                cell.font = cat_font['invalid']
        r += 1

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


def build_chat_manager_blueprint(
    *,
    AuthError,
    C2D_EVAL_MAX_MESSAGE_PAGES,
    CHAT2DESK_API_BASE_URL,
    CHAT2DESK_API_TOKEN,
    CHAT2DESK_AUTH_SCHEME,
    CHAT2DESK_STORAGE_BASE_URL,
    CHAT2DESK_SYNC_TIMEZONE,
    CHAT_MANAGER_METRICS_IMPORT_LOCK,
    CHAT_REPORT_TYPE_COMBINED,
    CHAT_REPORT_TYPE_FIELDS,
    CHAT_REPORT_TYPE_LABELS,
    CHAT_REPORT_TYPE_NAME_REQUESTS,
    CHAT_REPORT_TYPE_RESPONSE,
    CHAT_REPORT_TYPE_SCORE,
    CHAT_REPORT_TYPE_WHATSAPP,
    JWT_ACCESS_COOKIE_NAME,
    JWT_ALGORITHM,
    JWT_SECRET,
    OPERATOR_NAME_VENDOR_MARK_RE,
    ROLE_HIERARCHY,
    STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT,
    STATUS_IMPORT_MAX_FILE_SIZE_BYTES,
    STATUS_IMPORT_MAX_FILE_SIZE_MB,
    STATUS_IMPORT_MAX_SOURCE_ROWS,
    _KZ_TO_RU_FOLD,
    _c2d_api_get,
    _c2d_fetch_request_messages,
    _c2d_int_or_none,
    _c2d_media_url,
    _c2d_normalize_message,
    _c2d_parse_api_datetime,
    _chat2desk_api_base_url,
    _chat2desk_api_token,
    _chat2desk_authorization_header,
    _chat2desk_parse_datetime,
    _chat2desk_sync_timezone,
    _chat_metrics_parse_date,
    _chat_metrics_parse_number,
    _chat_report_build_operator_token_index,
    _chat_report_detect_types,
    _chat_report_in_surge,
    _chat_report_name_tokens,
    _chat_report_parse_dt,
    _chat_report_parse_surge_windows,
    _chat_report_resolve_operator,
    _chat_report_tokens_match,
    _current_session_id_from_access_token,
    _decode_token,
    _department_scope_id_for_requester,
    _get_authenticated_requester,
    _get_bearer_access_token,
    _get_cookie_value,
    _get_cookie_values,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_global_admin_requester,
    _is_sensitive_access_unlocked,
    _is_supervisor_role,
    _normalize_user_role,
    _operator_info_is_chat_manager,
    _operator_infos_prefer_chat_manager,
    _operator_name_strip_vendor_mark,
    _status_import_build_operator_lookup,
    _status_import_normalize_header,
    _status_import_normalize_operator_name,
    _status_import_parse_datetime,
    _status_import_xlsx_rows,
    _xlsx_cell_ref_to_index,
    require_api_key,
    sync_chat2desk_daily_metrics,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('chat_manager', __name__, url_prefix='/api')

    def _chat_metrics_parse_duration_seconds(value, unit_hint='seconds'):
        text = str(value or '').strip()
        if not text:
            return None
        if ':' in text:
            try:
                parts = [float(part.replace(',', '.')) for part in text.split(':')]
            except Exception:
                parts = []
            if len(parts) == 3:
                return parts[0] * 3600.0 + parts[1] * 60.0 + parts[2]
            if len(parts) == 2:
                return parts[0] * 60.0 + parts[1]
        value_num = _chat_metrics_parse_number(text)
        if value_num is None:
            return None
        hint = str(unit_hint or '').lower()
        if hint in ('minutes', 'minute', 'min'):
            return value_num * 60.0
        return value_num

    def _chat_metrics_import_parse_csv(csv_text, operator_lookup, default_date=None, max_source_rows=None, invalid_rows_preview_limit=None):
        if not isinstance(csv_text, str):
            raise ValueError("CSV text is required")

        try:
            dialect = csv.Sniffer().sniff(csv_text[:4096], delimiters=';,\t')
        except Exception:
            dialect = csv.excel()
            dialect.delimiter = ';'

        reader = csv.reader(StringIO(csv_text), dialect)
        header = None
        for candidate_header in reader:
            if any(str(cell or '').strip() for cell in candidate_header):
                header = candidate_header
                break
        if not header:
            raise ValueError("Файл пустой")

        normalized_header = [_status_import_normalize_header(h) for h in header]

        def _find_col(candidates):
            for candidate in candidates:
                if candidate in normalized_header:
                    return normalized_header.index(candidate)
            return None

        def _find_first_contains(tokens):
            for idx, header_key in enumerate(normalized_header):
                if all(token in header_key for token in tokens):
                    return idx
            return None

        def _first_non_none(*values):
            for value in values:
                if value is not None:
                    return value
            return None

        operator_col = _find_col([
            'operatorname', 'operator', 'name', 'employee', 'manager', 'agent', 'user',
            'fio', 'fullname', 'оператор', 'сотрудник', 'фио', 'имя', 'менеджер',
            'чатменеджер', 'чатменеджеры'
        ])
        date_col = _find_col(['date', 'day', 'period', 'дата', 'день', 'период'])
        chats_col = _find_col([
            'chats', 'chatscount', 'chatcount', 'totalchats', 'dialogs', 'dialogscount',
            'conversations', 'conversationcount', 'обращения', 'чаты', 'колвочатов',
            'количествочатов', 'диалоги', 'колводиалогов'
        ])
        if chats_col is None:
            for idx, header_key in enumerate(normalized_header):
                is_chat_count = 'chat' in header_key or 'чат' in header_key
                is_transfer = 'transfer' in header_key or 'передач' in header_key
                if is_chat_count and not is_transfer:
                    chats_col = idx
                    break
        avg_score_col = _find_col([
            'avgscore', 'averagescore', 'score', 'rating', 'avgrating',
            'средняяоценка', 'среднийбалл', 'оценка', 'рейтинг'
        ])
        avg_response_seconds_col = _find_col([
            'avgresponsetimeseconds', 'averageresponsetimeseconds', 'responsetimeseconds',
            'firstresponsetimeseconds', 'среднеевремяответасек', 'времяответасек'
        ])
        avg_response_minutes_col = _find_col([
            'avgresponsetimeminutes', 'averageresponsetimeminutes', 'responsetimeminutes',
            'firstresponsetimeminutes', 'среднеевремяответамин', 'времяответамин'
        ])
        avg_response_col = None
        avg_response_unit = 'seconds'
        if avg_response_seconds_col is not None:
            avg_response_col = avg_response_seconds_col
            avg_response_unit = 'seconds'
        elif avg_response_minutes_col is not None:
            avg_response_col = avg_response_minutes_col
            avg_response_unit = 'minutes'
        else:
            avg_response_col = _first_non_none(
                _find_col(['avgresponsetime', 'averageresponsetime', 'responsetime', 'firstresponsetime']),
                _find_first_contains(['ответ'])
            )
            if avg_response_col is not None:
                header_key = normalized_header[avg_response_col]
                avg_response_unit = 'minutes' if ('min' in header_key or 'мин' in header_key) else 'seconds'

        transfer_col = _find_col([
            'transferchat', 'transferchatcount', 'transfers', 'transfercount',
            'передачачата', 'передачичата', 'передачи', 'колвопередач'
        ])
        if transfer_col is None:
            transfer_col = _first_non_none(_find_first_contains(['transfer']), _find_first_contains(['передач']))

        if operator_col is None:
            raise ValueError("CSV должен содержать колонку оператора/менеджера")
        if date_col is None and not default_date:
            raise ValueError("CSV должен содержать колонку даты или параметр date=YYYY-MM-DD")
        allowed_update_fields = []
        if chats_col is not None:
            allowed_update_fields.append('chats_count')
        if avg_response_col is not None:
            allowed_update_fields.append('avg_response_time_seconds')
        if transfer_col is not None:
            allowed_update_fields.append('transfer_chat_count')
        if not allowed_update_fields:
            if avg_score_col is not None:
                raise ValueError(
                    "Импорт готовой средней оценки запрещён. "
                    "Загрузите исходный отчёт Chat2Desk с колонкой rating_scale_score"
                )
            raise ValueError("CSV должен содержать хотя бы одну метрику: чаты, время ответа или передачи")

        max_rows_value = None
        if max_source_rows is not None:
            try:
                max_rows_value = int(max_source_rows)
            except Exception:
                max_rows_value = None
            if max_rows_value is not None and max_rows_value <= 0:
                max_rows_value = None

        preview_limit_value = STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT
        if invalid_rows_preview_limit is not None:
            try:
                preview_limit_value = max(1, int(invalid_rows_preview_limit))
            except Exception:
                preview_limit_value = STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT

        def _cell(cols, idx):
            if idx is None or idx < 0 or idx >= len(cols):
                return ''
            return str(cols[idx] or '').strip()

        metrics = []
        source_rows = 0
        invalid_rows_count = 0
        invalid_rows_preview = []

        def _push_invalid(row_num, reason, operator_name, date_value):
            nonlocal invalid_rows_count
            invalid_rows_count += 1
            if len(invalid_rows_preview) >= preview_limit_value:
                return
            invalid_rows_preview.append({
                'row': int(row_num),
                'reason': str(reason or '').strip() or 'Некорректная строка',
                'operator_name': str(operator_name or '').strip(),
                'date': str(date_value or '').strip()
            })

        for row_num, cols in enumerate(reader, start=2):
            source_rows += 1
            if max_rows_value is not None and source_rows > max_rows_value:
                raise OverflowError(f"Лимит строк CSV превышен ({max_rows_value})")

            operator_name = _cell(cols, operator_col)
            date_value = _cell(cols, date_col)
            if not operator_name and not date_value and not any(str(cell or '').strip() for cell in cols):
                continue

            day_obj = _chat_metrics_parse_date(date_value, default_date=default_date)
            if not operator_name or day_obj is None:
                _push_invalid(row_num, 'Отсутствует оператор или дата', operator_name, date_value or default_date)
                continue

            operator_key = _status_import_normalize_operator_name(operator_name)
            operator_matches = operator_lookup.get(operator_key) or []
            if len(operator_matches) != 1:
                _push_invalid(
                    row_num,
                    'Оператор не найден' if len(operator_matches) == 0 else 'Найдено несколько операторов с таким именем',
                    operator_name,
                    date_value or default_date
                )
                continue

            chats_value = _chat_metrics_parse_number(_cell(cols, chats_col))
            transfer_value = _chat_metrics_parse_number(_cell(cols, transfer_col))
            avg_response_seconds = _chat_metrics_parse_duration_seconds(_cell(cols, avg_response_col), avg_response_unit)

            metric = {
                'operator_id': int(operator_matches[0]['id']),
                'operator_name': operator_matches[0].get('name') or operator_name,
                'day': day_obj.strftime('%Y-%m-%d'),
                'source_row': int(row_num)
            }
            if chats_col is not None:
                metric['chats_count'] = int(round(chats_value or 0))
            if avg_response_col is not None:
                metric['avg_response_time_seconds'] = avg_response_seconds
            if transfer_col is not None:
                metric['transfer_chat_count'] = int(round(transfer_value or 0))
            metrics.append(metric)

        return {
            'source_rows': int(source_rows),
            'matched_rows': len(metrics),
            'invalid_rows_count': int(invalid_rows_count),
            'invalid_rows_preview': invalid_rows_preview,
            'update_fields': allowed_update_fields,
            'metrics': metrics
        }

    def _chat_report_rows_from_bytes(raw_bytes, filename):
        """(header, data_rows) из CSV или XLSX. header/строки — списки строк."""
        name = str(filename or '').lower()
        if name.endswith('.xlsx') or name.endswith('.xlsm'):
            rows = _status_import_xlsx_rows(raw_bytes)
        else:
            csv_text = None
            for enc in ('utf-8-sig', 'cp1251'):
                try:
                    csv_text = raw_bytes.decode(enc)
                    break
                except Exception:
                    continue
            if csv_text is None:
                raise ValueError("Не удалось декодировать файл (ожидается UTF-8 или CP1251)")
            try:
                dialect = csv.Sniffer().sniff(csv_text[:4096], delimiters=';,\t')
            except Exception:
                dialect = csv.excel()
                dialect.delimiter = ';'
            rows = [list(r) for r in csv.reader(StringIO(csv_text), dialect)]
        header = None
        data_rows = []
        for r in rows:
            if header is None:
                if any(str(c or '').strip() for c in r):
                    header = [str(c or '').strip() for c in r]
                continue
            data_rows.append(r)
        if not header:
            raise ValueError("Файл пустой")
        return header, data_rows

    def _chat_report_parse(header, data_rows, operator_lookup, operator_token_index,
                           default_date=None, surge_windows=None, preview_limit=None):
        """Определяет тип отчёта по колонкам и считает дневные метрики. Возвращает None,
    если формат не распознан (тогда вызывающий откатывается к универсальному парсеру)."""
        normalized_header = [_status_import_normalize_header(h) for h in (header or [])]
        report_types = _chat_report_detect_types(normalized_header)
        if not report_types:
            return None
        report_type = report_types[0] if len(report_types) == 1 else CHAT_REPORT_TYPE_COMBINED

        def find_idx(*candidates):
            for cand in candidates:
                for i, h in enumerate(normalized_header):
                    if h == cand:
                        return i
            for cand in candidates:
                for i, h in enumerate(normalized_header):
                    if cand in h:
                        return i
            return None

        def find_contains_all(tokens):
            for i, h in enumerate(normalized_header):
                if all(t in h for t in tokens):
                    return i
            return None

        def cell(cols, idx):
            if idx is None or idx < 0 or idx >= len(cols):
                return ''
            return str(cols[idx] or '').strip()

        preview_limit_value = STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT
        if preview_limit is not None:
            try:
                preview_limit_value = max(1, int(preview_limit))
            except Exception:
                preview_limit_value = STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT

        unmatched_names = {}
        unmatched_seen = set()

        def note_unmatched(raw, row_index=None):
            key = str(raw or '').strip() or '(пусто)'
            seen_key = (key, row_index) if row_index is not None else None
            if seen_key is not None:
                if seen_key in unmatched_seen:
                    return
                unmatched_seen.add(seen_key)
            unmatched_names[key] = unmatched_names.get(key, 0) + 1

        def nonempty(cols):
            return any(str(c or '').strip() for c in cols)

        source_rows = sum(1 for cols in (data_rows or []) if nonempty(cols))
        metrics_by_key = {}
        update_fields = set()
        excluded_surge = None

        def metric_for(op_id, day_str):
            return metrics_by_key.setdefault((op_id, day_str), {
                'operator_id': op_id,
                'day': day_str,
            })

        if CHAT_REPORT_TYPE_SCORE in report_types:
            op_idx = find_idx('operatorname', 'operator', 'fio', 'фио', 'оператор', 'имя')
            date_idx = find_idx('createdat', 'дата', 'date', 'day')
            if date_idx is None:
                date_idx = find_contains_all(['creat'])
            score_idx = find_idx('ratingscalescore')
            if score_idx is None:
                score_idx = find_contains_all(['scale', 'score'])
            if op_idx is None or score_idx is None:
                raise ValueError("Отчёт оценок: нужны колонки оператора и rating_scale_score")
            score_agg = {}
            for row_index, cols in enumerate(data_rows or []):
                if not nonempty(cols):
                    continue
                raw_name = cell(cols, op_idx)
                day = _chat_metrics_parse_date(cell(cols, date_idx), default_date=default_date)
                score = _chat_metrics_parse_number(cell(cols, score_idx))
                if not raw_name or day is None or score is None:
                    if raw_name:
                        note_unmatched(raw_name, row_index)
                    continue
                op_id, _ = _chat_report_resolve_operator(raw_name, operator_lookup, operator_token_index)
                if op_id is None:
                    note_unmatched(raw_name, row_index)
                    continue
                bucket = score_agg.setdefault((op_id, day.strftime('%Y-%m-%d')), {'score_sum': 0.0, 'score_count': 0})
                bucket['score_sum'] += float(score)
                bucket['score_count'] += 1
            for (op_id, day_str), v in score_agg.items():
                metric = metric_for(op_id, day_str)
                metric['score_sum'] = round(v['score_sum'], 4)
                metric['score_count'] = int(v['score_count'])
                metric['avg_score'] = round(v['score_sum'] / v['score_count'], 4) if v['score_count'] else None
            update_fields.update(CHAT_REPORT_TYPE_FIELDS[CHAT_REPORT_TYPE_SCORE])

        if CHAT_REPORT_TYPE_RESPONSE in report_types:
            op_idx = find_idx('operatorname', 'operator', 'fio', 'фио', 'оператор')
            start_idx = find_idx('requeststart')
            if start_idx is None:
                start_idx = find_contains_all(['request', 'start'])
            rt_idx = find_idx('reactiontime')
            if rt_idx is None:
                rt_idx = find_contains_all(['reaction'])
            if op_idx is None or start_idx is None or rt_idx is None:
                raise ValueError("Отчёт времени ответа: нужны operator_name, request_start, reaction_time")
            windows = _chat_report_parse_surge_windows(surge_windows)
            excluded_surge = 0
            response_agg = {}
            for row_index, cols in enumerate(data_rows or []):
                if not nonempty(cols):
                    continue
                raw_name = cell(cols, op_idx)
                start_dt = _chat_report_parse_dt(cell(cols, start_idx))
                reaction = _chat_metrics_parse_number(cell(cols, rt_idx))
                if not raw_name or start_dt is None or reaction is None:
                    if raw_name:
                        note_unmatched(raw_name, row_index)
                    continue
                if _chat_report_in_surge(start_dt, windows):
                    excluded_surge += 1
                    continue
                op_id, _ = _chat_report_resolve_operator(raw_name, operator_lookup, operator_token_index)
                if op_id is None:
                    note_unmatched(raw_name, row_index)
                    continue
                bucket = response_agg.setdefault((op_id, start_dt.date().strftime('%Y-%m-%d')), {'sum': 0.0, 'count': 0})
                bucket['sum'] += float(reaction)
                bucket['count'] += 1
            for (op_id, day_str), v in response_agg.items():
                if v['count'] <= 0:
                    continue
                metric = metric_for(op_id, day_str)
                metric['avg_response_time_seconds'] = round(v['sum'] / v['count'], 2)
            update_fields.update(CHAT_REPORT_TYPE_FIELDS[CHAT_REPORT_TYPE_RESPONSE])

        if CHAT_REPORT_TYPE_WHATSAPP in report_types:
            op_idx = find_contains_all(['создател'])
            if op_idx is None:
                op_idx = find_idx('фиосоздателя', 'operatorname', 'operator', 'fio', 'фио')
            date_idx = find_contains_all(['обращени'])
            if date_idx is None:
                date_idx = find_idx('дата', 'date', 'day')
            if date_idx is None:
                date_idx = find_contains_all(['создан'])
            chan_idx = None
            for i, h in enumerate(normalized_header):
                if 'звонок' in h and 'чат' in h:
                    chan_idx = i
                    break
            if op_idx is None or chan_idx is None:
                raise ValueError("Отчёт чатов: нужны «ФИО создателя» и «Звонок или Чат»")
            whatsapp_agg = {}
            for row_index, cols in enumerate(data_rows or []):
                if not nonempty(cols):
                    continue
                channel = _status_import_normalize_header(cell(cols, chan_idx))
                if 'whatsapp' not in channel:
                    continue  # считаем только Whatsapp
                raw_name = cell(cols, op_idx)
                day = _chat_metrics_parse_date(cell(cols, date_idx), default_date=default_date)
                if not raw_name or day is None:
                    if raw_name:
                        note_unmatched(raw_name, row_index)
                    continue
                op_id, _ = _chat_report_resolve_operator(raw_name, operator_lookup, operator_token_index)
                if op_id is None:
                    note_unmatched(raw_name, row_index)
                    continue
                bucket = whatsapp_agg.setdefault((op_id, day.strftime('%Y-%m-%d')), {'chats': 0})
                bucket['chats'] += 1
            for (op_id, day_str), v in whatsapp_agg.items():
                metric = metric_for(op_id, day_str)
                metric['chats_count'] = int(v['chats'])
                metric['whatsapp_chats_count'] = int(v['chats'])
                if report_type == CHAT_REPORT_TYPE_WHATSAPP:
                    metric['source_report_type'] = CHAT_REPORT_TYPE_WHATSAPP
            update_fields.update(CHAT_REPORT_TYPE_FIELDS[CHAT_REPORT_TYPE_WHATSAPP])

        if CHAT_REPORT_TYPE_NAME_REQUESTS in report_types:
            day = _chat_metrics_parse_date(None, default_date=default_date)
            if day is None:
                raise ValueError("Для отчёта Name/Requests укажите дату загрузки (date=YYYY-MM-DD)")
            name_idx = find_idx('name', 'имя', 'оператор', 'fio', 'фио')
            req_idx = find_idx('requests', 'request')
            if req_idx is None:
                req_idx = find_contains_all(['request']) or find_idx('чаты', 'count', 'колво')
            if name_idx is None or req_idx is None:
                raise ValueError("Отчёт Name/Requests: нужны колонки Name и Requests")
            day_str = day.strftime('%Y-%m-%d')
            name_agg = {}
            for row_index, cols in enumerate(data_rows or []):
                if not nonempty(cols):
                    continue
                raw_name = cell(cols, name_idx)
                requests_val = _chat_metrics_parse_number(cell(cols, req_idx))
                if not raw_name or requests_val is None:
                    if raw_name:
                        note_unmatched(raw_name, row_index)
                    continue
                op_id, _ = _chat_report_resolve_operator(raw_name, operator_lookup, operator_token_index)
                if op_id is None:
                    note_unmatched(raw_name, row_index)
                    continue
                bucket = name_agg.setdefault((op_id, day_str), {'chats': 0})
                bucket['chats'] += int(round(requests_val))
            for (op_id, day_key), v in name_agg.items():
                metric = metric_for(op_id, day_key)
                metric['chats_count'] = int(v['chats'])
                metric['name_requests_chats_count'] = int(v['chats'])
                if report_type == CHAT_REPORT_TYPE_NAME_REQUESTS:
                    metric['source_report_type'] = CHAT_REPORT_TYPE_NAME_REQUESTS
            update_fields.update(CHAT_REPORT_TYPE_FIELDS[CHAT_REPORT_TYPE_NAME_REQUESTS])

        metrics = sorted(metrics_by_key.values(), key=lambda item: (item.get('day') or '', int(item.get('operator_id') or 0)))
        unmatched_sorted = sorted(unmatched_names.items(), key=lambda kv: (-kv[1], kv[0]))
        if report_type == CHAT_REPORT_TYPE_COMBINED:
            detected_label = 'Комбинированный отчёт: ' + ', '.join(
                CHAT_REPORT_TYPE_LABELS.get(t, t) for t in report_types
            )
        else:
            detected_label = CHAT_REPORT_TYPE_LABELS.get(report_type, report_type)
        result = {
            'detected_type': report_type,
            'detected_label': detected_label,
            'detected_types': report_types,
            'update_fields': sorted(update_fields),
            'metrics': metrics,
            'source_rows': int(source_rows),
            'matched_rows': len(metrics),
            'unmatched_count': int(sum(unmatched_names.values())),
            'unmatched_preview': [{'name': name, 'rows': count} for name, count in unmatched_sorted[:preview_limit_value]],
        }
        if excluded_surge is not None:
            result['excluded_surge_rows'] = int(excluded_surge)
        return result

    def _low_rating_snapshot_fill_operators(snapshot):
        """Дописывает в сообщения снапшота учётку Chat2Desk (operatorId).

    Раньше снапшот её выбрасывал, хотя вендор отдаёт её в каждом сообщении, —
    без неё не видно, кто из менеджеров вёл чат (задача #286). Новые снапшоты
    пишутся уже с ней. Для старых — сначала бесплатное сырьё вебхуков (неделя),
    затем ОДИН запрос к API по окну дат диалога (квота Chat2Desk на исходе, а
    по дате /v1/messages отдаёт всю заявку одним ответом). Найденное сохраняется
    в снапшот, так что повторное открытие квоту уже не тратит; ленту не
    переписываем — только добавляем поле.

    Спрашиваем только о тех сообщениях, по которым видно, кто вёл чат
    (LOW_RATING_PARTICIPANT_MESSAGE_TYPES): учётка у системных и автоответов
    разбору не нужна, и ради неё платный запрос не делается."""
        if not snapshot or (snapshot.get('source') or 'chat2desk') != 'chat2desk':
            return snapshot
        messages = [dict(m) for m in (snapshot.get('messages') or []) if isinstance(m, dict)]
        missing = [m for m in messages if 'operatorId' not in m and m.get('id') is not None]
        if not missing:
            return snapshot

        def matters(message):
            return message.get('type') in LOW_RATING_PARTICIPANT_MESSAGE_TYPES

        days = sorted(str(m.get('created') or '')[:10] for m in messages if m.get('created'))
        first_day = last_day = None
        try:
            if days:
                first_day = dt_date.fromisoformat(days[0])
                last_day = dt_date.fromisoformat(days[-1])
        except ValueError:
            first_day = last_day = None

        found = {}
        if first_day and snapshot.get('request_id'):
            try:
                found.update(db.get_c2d_webhook_message_operators(
                    snapshot['request_id'], first_day - timedelta(days=1), last_day + timedelta(days=1)))
            except Exception:
                logging.exception("low rating chat: webhook operators lookup failed")

        api_checked = False
        api_covered = False
        request_meta = snapshot.get('request') if isinstance(snapshot.get('request'), dict) else {}
        need_api = [m['id'] for m in missing if matters(m) and m['id'] not in found]
        if need_api and first_day and snapshot.get('dialog_id') and not request_meta.get('operator_ids_checked'):
            try:
                params = {
                    'dialog_id': int(snapshot['dialog_id']),
                    'start_date': _c2d_api_date(first_day.isoformat()),
                    # Граница конца у вендора — «строго до» по Алматы (проверено
                    # живьём в driver_chats.fetch_window_messages): без лишних суток
                    # чат через полночь терял сообщения второго дня.
                    'finish_date': _c2d_api_date((last_day + timedelta(days=1)).isoformat()),
                    'limit': 200,
                }
                page = _c2d_api_get('/v1/messages', params=params)
                rows = [m for m in (page.get('data') or []) if isinstance(m, dict) and m.get('id') is not None]
                api_found = {m['id']: _c2d_int_or_none(m.get('operator_id')) for m in rows}
                try:
                    window_total = int(float((page.get('meta') or {}).get('total') or 0))
                except (TypeError, ValueError):
                    window_total = 0
                # В окне больше 200 сообщений (offset вендор игнорирует): второй
                # и последний запрос — хвост окна. Вместе они кроют до 400.
                if window_total > len(rows) and any(mid not in api_found for mid in need_api):
                    tail = _c2d_api_get('/v1/messages', params={**params, 'order': 'desc'})
                    for m in tail.get('data') or []:
                        if isinstance(m, dict) and m.get('id') is not None:
                            api_found.setdefault(m['id'], _c2d_int_or_none(m.get('operator_id')))
                for mid, operator_value in api_found.items():
                    found.setdefault(mid, operator_value)
                api_checked = True
                # Ответ вообще про эту переписку? Если ни одного нашего сообщения в нём
                # нет, отмечать остальные «вендор не знает» было бы враньём.
                api_covered = any(m.get('id') in api_found for m in messages)
            except Exception as exc:
                logging.warning("low rating chat: Chat2Desk operators lookup failed: %s", exc)

        changed = False
        for m in missing:
            if m['id'] in found:
                m['operatorId'] = found[m['id']]
            elif not matters(m) or api_covered:
                # Разбору не нужно или вендор об этом сообщении не знает — отмечаем,
                # чтобы не спрашивать снова.
                m['operatorId'] = None
            else:
                continue
            changed = True
        if not changed and not api_checked:
            return snapshot
        try:
            # Флаг — после любого успешного ответа: повторный платный запрос по той же
            # переписке ничего нового не даст, а квота на исходе.
            db.update_c2d_snapshot_messages(
                snapshot['id'], messages,
                {'operator_ids_checked': True} if api_checked else None)
        except Exception:
            logging.exception("low rating chat: failed to persist snapshot operators")
        return {**snapshot, 'messages': messages}

    def _low_rating_chat_view(snapshot, review, department_scope_id=None):
        """Переписка для разбора: подписи авторов и список менеджеров чата.

    Возвращает (снапшот для ответа, participants). Подписи — только в ответе,
    в БД не пишутся: имя учётки может смениться, а id — нет.

    selectable — можно ли этому проверяющему засчитать менеджеру оценку: нужен
    сотрудник, а проверяющему с ограничением по отделу — сотрудник своего
    отдела (то же правило, что проверяет сервер при сохранении). Тот, кому
    оценку отдал Chat2Desk, и уже засчитанные доступны всегда."""
        messages = [dict(m) for m in (snapshot.get('messages') or []) if isinstance(m, dict)]
        has_operator_ids = any('operatorId' in m for m in messages)
        c2d_ids = {m.get('operatorId') for m in messages if m.get('operatorId')}
        directory = db.get_c2d_operator_directory(c2d_ids) if c2d_ids else {}

        if has_operator_ids:
            for m in messages:
                if m.get('type') not in ('to_client', 'comment'):
                    continue
                # Неизвестный автор — без подписи, а не чужим именем.
                m['author'] = (directory.get(m.get('operatorId')) or {}).get('name') or ''

        own_id = int(review.get('operator_id') or 0) or None
        attribution = db._low_rating_effective_attribution(
            review.get('operator_id'), review.get('attributed_operator_ids'))
        always_allowed = {value for value in [own_id] + attribution if value}
        attributed_names = {op.get('id'): op.get('name') for op in (review.get('attributed_operators') or [])}

        def selectable(user_id, department_id):
            if not user_id:
                return False
            if user_id in always_allowed or department_scope_id is None:
                return True
            return department_id is not None and int(department_id) == int(department_scope_id)

        participants = {}
        order = []
        for m in messages:
            if m.get('type') not in LOW_RATING_PARTICIPANT_MESSAGE_TYPES or not m.get('operatorId'):
                continue
            entry_info = directory.get(m['operatorId']) or {}
            key = entry_info.get('user_id') or f"c2d:{m['operatorId']}"
            entry = participants.get(key)
            if entry is None:
                entry = {
                    'id': entry_info.get('user_id'),
                    'c2d_id': m['operatorId'],
                    'name': entry_info.get('name') or '',
                    'first_at': m.get('created'),
                    'last_at': m.get('created'),
                    'replies': 0,
                    'selectable': selectable(entry_info.get('user_id'), entry_info.get('department_id')),
                }
                participants[key] = entry
                order.append(key)
            if m.get('created'):
                entry['last_at'] = m['created']
            if m.get('type') == 'to_client':
                entry['replies'] += 1

        # Тот, кому оценку отдал Chat2Desk, и уже засчитанные — в списке всегда,
        # даже если в переписке их не видно (старый снапшот без учёток).
        for user_id in [own_id] + attribution:
            if user_id and user_id not in participants:
                participants[user_id] = {
                    'id': user_id,
                    'c2d_id': None,
                    'name': (
                        (review.get('operator_name') if user_id == own_id else '')
                        or attributed_names.get(user_id) or ''
                    ),
                    'first_at': None,
                    'last_at': None,
                    'replies': 0,
                    'selectable': True,
                }
                order.append(user_id)

        result = []
        for key in order:
            entry = participants[key]
            entry['is_default'] = bool(own_id and entry.get('id') == own_id)
            result.append(entry)
        # Сначала по времени появления в чате; без времени — в конец, как были.
        result.sort(key=lambda item: (item.get('first_at') is None, item.get('first_at') or ''))
        return {**snapshot, 'messages': messages}, result

    @bp.route('/chat_manager/surge_windows', methods=['GET', 'PUT'])
    @require_api_key
    def chat_manager_surge_windows():
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not (_is_admin_role(requester[3]) or _is_supervisor_role(requester[3])):
                return jsonify({"error": "Forbidden"}), 403

            if request.method == 'GET':
                month = request.args.get('month') or datetime.now().strftime('%Y-%m')
                result = db.get_chat_metric_surge_windows(month)
                return jsonify({"status": "success", **result}), 200

            payload = request.get_json(silent=True) or {}
            month = payload.get('month') or request.args.get('month') or datetime.now().strftime('%Y-%m')
            windows = payload.get('windows') or []
            result = db.save_chat_metric_surge_windows(month, windows, updated_by=requester_id)
            return jsonify({
                "status": "success",
                "message": "Окна наплыва сохранены",
                **result
            }), 200

        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error saving chat manager surge windows: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/chat_manager/low_rating_reviews', methods=['GET'])
    @require_api_key
    def chat_manager_low_rating_reviews():
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3])
            headed_dept_id = _headed_department_id(requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or headed_dept_id is not None):
                return jsonify({"error": "Forbidden"}), 403

            month = request.args.get('month') or datetime.now().strftime('%Y-%m')
            status_filter = request.args.get('status') or 'attention'
            operator_id = request.args.get('operator_id') or None
            department_id = request.args.get('department_id') or None
            start = request.args.get('start') or request.args.get('date_from') or None
            end = request.args.get('end') or request.args.get('date_to') or None
            search = request.args.get('search') or request.args.get('q') or None
            page = request.args.get('page') or 1
            per_page = request.args.get('per_page') or 12
            only_unreviewed = str(request.args.get('unreviewed') or '').strip().lower() in ('1', 'true', 'yes', 'on')
            if not _is_global_admin_requester(requester_role, requester_id):
                department_id = _department_scope_id_for_requester(requester_id)
            role_can_finalize = _is_global_admin_requester(requester_role, requester_id) or headed_dept_id is not None

            result = db.list_chat_manager_low_rating_reviews(
                month=month,
                department_id=department_id,
                operator_id=operator_id,
                status=status_filter,
                start=start,
                end=end,
                search=search,
                limit=request.args.get('limit') or 5000,
                page=page,
                per_page=per_page,
                viewer_id=requester_id,
                can_finalize=role_can_finalize,
                unreviewed=only_unreviewed
            )
            return jsonify({"status": "success", **result}), 200

        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error loading chat manager low rating reviews: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/chat_manager/low_rating_reviews/<review_id>', methods=['GET', 'PATCH'])
    @require_api_key
    def update_chat_manager_low_rating_review(review_id):
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3])
            headed_dept_id = _headed_department_id(requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or headed_dept_id is not None):
                return jsonify({"error": "Forbidden"}), 403

            current = db.get_chat_manager_low_rating_review(review_id)
            if not current:
                return jsonify({"error": "Review not found"}), 404
            if not _is_global_admin_requester(requester_role, requester_id):
                scope_dept = _department_scope_id_for_requester(requester_id)
                if scope_dept is not None and int(current.get('department_id') or 0) != int(scope_dept):
                    return jsonify({"error": "Forbidden"}), 403

            role_can_finalize = _is_global_admin_requester(requester_role, requester_id) or (
                headed_dept_id is not None and int(current.get('department_id') or 0) == int(headed_dept_id)
            )
            can_finalize = bool(role_can_finalize and current.get('has_review_conflict'))
            if request.method == 'GET':
                public_current = db.get_chat_manager_low_rating_review(
                    review_id,
                    viewer_id=requester_id,
                    public=True,
                    can_finalize=can_finalize
                )
                return jsonify({"status": "success", "review": public_current}), 200

            payload = request.get_json(silent=True) or {}
            action = str(payload.get('action') or payload.get('reviewer') or 'review').strip().lower()
            # Кому засчитать оценку (задача #286). Поля нет — атрибуцию не трогаем:
            # так продолжает работать и вкладка, открытая до обновления.
            operator_ids = payload.get('operator_ids') if 'operator_ids' in payload else None
            # Какие отметки проверяющий видел, выбирая «обоснованно»: если с тех пор
            # их поменял другой, сохранение отклоняется с 409 и свежей карточкой.
            base_operator_ids = payload.get('base_operator_ids') if 'base_operator_ids' in payload else None
            attribution_scope = (
                None if _is_global_admin_requester(requester_role, requester_id)
                else _department_scope_id_for_requester(requester_id)
            )
            try:
                if action in {'final', 'head', 'manager'}:
                    if not role_can_finalize:
                        return jsonify({"error": "Only department head can set final decision"}), 403
                    if not current.get('has_review_conflict'):
                        return jsonify({"error": "Final decision is available only when two reviews disagree"}), 400
                    updated = db.finalize_chat_manager_low_rating_review(
                        review_id=review_id,
                        status=payload.get('status'),
                        comment=payload.get('comment') or '',
                        updated_by=requester_id,
                        operator_ids=operator_ids,
                        department_scope_id=attribution_scope,
                        base_operator_ids=base_operator_ids
                    )
                else:
                    updated = db.save_chat_manager_low_rating_personal_review(
                        review_id=review_id,
                        reviewer_id=requester_id,
                        status=payload.get('status'),
                        comment=payload.get('comment') or '',
                        operator_ids=operator_ids,
                        department_scope_id=attribution_scope,
                        base_operator_ids=base_operator_ids
                    )
            except LowRatingAttributionConflict as conflict:
                fresh = db.get_chat_manager_low_rating_review(
                    review_id,
                    viewer_id=requester_id,
                    public=True,
                    can_finalize=role_can_finalize
                )
                return jsonify({"error": str(conflict), "code": "ATTRIBUTION_CHANGED", "review": fresh}), 409

            if not updated:
                return jsonify({"error": "Review not found"}), 404
            public_updated = db.get_chat_manager_low_rating_review(
                review_id,
                viewer_id=requester_id,
                public=True,
                can_finalize=role_can_finalize
            )
            return jsonify({"status": "success", "review": public_updated}), 200

        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error updating chat manager low rating review: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/chat_manager/low_rating_reviews/<review_id>/history', methods=['GET'])
    @require_api_key
    def chat_manager_low_rating_review_history(review_id):
        """История разбора низкой оценки: кто и когда менял вердикт и атрибуцию
    (задача #286). Видят только проверяющие — в пределах своего отдела."""
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3])
            headed_dept_id = _headed_department_id(requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or headed_dept_id is not None):
                return jsonify({"error": "Forbidden"}), 403
            if not _is_low_rating_review_id(review_id):
                return jsonify({"error": "Review not found"}), 404

            current = db.get_chat_manager_low_rating_review(review_id)
            if not current:
                return jsonify({"error": "Review not found"}), 404
            if not _is_global_admin_requester(requester_role, requester_id):
                scope_dept = _department_scope_id_for_requester(requester_id)
                if scope_dept is not None and int(current.get('department_id') or 0) != int(scope_dept):
                    return jsonify({"error": "Forbidden"}), 403

            return jsonify({
                "status": "success",
                "history": db.get_chat_manager_low_rating_review_history(review_id),
            }), 200

        except Exception as e:
            logging.error(f"Error loading chat manager low rating review history: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/chat_manager/low_rating_reviews/<review_id>/chat', methods=['GET'])
    @require_api_key
    def chat_manager_low_rating_review_chat(review_id):
        """Переписка чата, за который клиент поставил низкую оценку.

    Снапшоты общие с оценкой чатов в «Журнале оценок» (c2d_chat_snapshots, один
    на request_id): уже открытый/оценённый чат отдаётся из БД, к API Chat2Desk
    ходим только за новыми (2-3 запроса, квота почти не тратится)."""
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3])
            headed_dept_id = _headed_department_id(requester_id)
            is_reviewer = (
                _is_admin_role(requester_role)
                or _is_supervisor_role(requester_role)
                or headed_dept_id is not None
            )

            if not _is_low_rating_review_id(review_id):
                return jsonify({"error": "Review not found"}), 404

            current = db.get_chat_manager_low_rating_review(review_id)
            if not current:
                return jsonify({"error": "Review not found"}), 404

            # Оператор открывает переписку СВОЕЙ низкой оценки из «Мои оценки» —
            # только для чтения и только по QR-подтверждённой сессии (тот же ключ,
            # что открывает записи разговоров). Чужие оценки ему недоступны.
            # «Своя» — отданная ему Chat2Desk или засчитанная ему итоговым решением
            # (задача #286): ровно те строки, что он видит в «Моих оценках».
            is_own_rating = int(current.get('operator_id') or 0) == int(requester_id)
            if not is_own_rating and current.get('final_status') == 'valid':
                is_own_rating = int(requester_id) in (current.get('attributed_operator_ids') or [])
            if not is_reviewer:
                if not is_own_rating:
                    return jsonify({"error": "Forbidden"}), 403
                if requester_role == 'operator' and not _is_sensitive_access_unlocked(
                    requester_id, _current_session_id_from_access_token()
                ):
                    return jsonify({"error": "Переписка откроется после QR-подтверждения доступа"}), 403
            elif not _is_global_admin_requester(requester_role, requester_id):
                scope_dept = _department_scope_id_for_requester(requester_id)
                if scope_dept is not None and int(current.get('department_id') or 0) != int(scope_dept):
                    return jsonify({"error": "Forbidden"}), 403

            request_id = _low_rating_chat_request_id(current.get('raw_payload'))
            if not request_id:
                return jsonify({"error": "В отчёте Chat2Desk у этой оценки нет ссылки на заявку — переписку не достать"}), 404

            snapshot = db.get_c2d_snapshot(request_id=request_id)
            if snapshot is not None and not snapshot.get('messages'):
                snapshot = None  # пустой снапшот старой версии — перечитываем заново
            cached = snapshot is not None

            if snapshot is None:
                # Границы разговора: из c2d_requests, если заявка попала в синк метрик
                # (только последние 45 дней), иначе окном вокруг времени оценки —
                # клиент ставит её сразу после диалога.
                request_row = db.get_c2d_request(request_id) or {}
                request_start = request_row.get('request_start')
                request_end = request_row.get('request_end')
                if not request_start:
                    try:
                        rated_at = datetime.fromisoformat(str(current.get('rated_at') or '')[:19])
                    except ValueError:
                        rated_at = None
                    if rated_at is not None:
                        request_start = (rated_at - timedelta(hours=6)).isoformat()
                        request_end = rated_at.isoformat()

                dialog_id, messages = _c2d_fetch_request_messages(
                    request_id, request_start=request_start, request_end=request_end)
                if not messages:
                    return jsonify({"error": "Chat2Desk не отдал переписку по этой заявке (возможно, она уже недоступна)"}), 404

                raw = current.get('raw_payload') if isinstance(current.get('raw_payload'), dict) else {}
                snapshot_row = dict(request_row)
                # Заявки старше ретеншна c2d_requests в синке нет — тогда шапку
                # снапшота собираем из самой строки низкой оценки.
                snapshot_row.setdefault('request_id', request_id)
                for key, value in (
                    ('day', current.get('day')),
                    ('operator_id', current.get('operator_id')),
                    ('c2d_operator_name', current.get('operator_name')),
                    ('channel_name', current.get('taxi_park') or raw.get('channel_name')),
                    ('transport', raw.get('transport')),
                    ('client_name', raw.get('client_name')),
                    ('client_phone', current.get('phone_number') or raw.get('phone')),
                ):
                    if not snapshot_row.get(key) and value:
                        snapshot_row[key] = value

                snapshot_id = db.save_c2d_snapshot(
                    request_id, dialog_id, messages,
                    request_row=snapshot_row, created_by=requester_id)
                snapshot = db.get_c2d_snapshot(snapshot_id=snapshot_id)
            else:
                snapshot = _low_rating_snapshot_fill_operators(snapshot)

            participants = []
            if snapshot is not None:
                snapshot, participants = _low_rating_chat_view(
                    snapshot, current,
                    department_scope_id=(
                        None if not is_reviewer or _is_global_admin_requester(requester_role, requester_id)
                        else _department_scope_id_for_requester(requester_id)
                    ),
                )
            payload = {
                "status": "success",
                "snapshot": snapshot,
                "request_id": request_id,
                "cached": cached,
            }
            # Список менеджеров чата нужен только тем, кто выносит вердикт.
            if is_reviewer:
                payload["participants"] = participants
            return jsonify(payload), 200

        except RuntimeError as e:
            logging.warning("low rating chat fetch: %s", e)
            return jsonify({"error": str(e)}), 502
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error loading chat for low rating review: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/chat_manager/low_rating_reviews/export', methods=['GET'])
    @require_api_key
    def export_chat_manager_low_rating_reviews():
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            requester_role = _normalize_user_role(requester[3])
            headed_dept_id = _headed_department_id(requester_id)
            if not (_is_admin_role(requester_role) or _is_supervisor_role(requester_role) or headed_dept_id is not None):
                return jsonify({"error": "Forbidden"}), 403

            month = request.args.get('month') or datetime.now().strftime('%Y-%m')
            status_filter = request.args.get('status') or 'all'
            start = request.args.get('start') or request.args.get('date_from') or None
            end = request.args.get('end') or request.args.get('date_to') or None
            search = request.args.get('search') or request.args.get('q') or None
            operator_id = request.args.get('operator_id') or None
            department_id = request.args.get('department_id') or None
            if not _is_global_admin_requester(requester_role, requester_id):
                department_id = _department_scope_id_for_requester(requester_id)
            role_can_finalize = _is_global_admin_requester(requester_role, requester_id) or headed_dept_id is not None

            result = db.list_chat_manager_low_rating_reviews(
                month=month,
                department_id=department_id,
                operator_id=operator_id,
                status=status_filter,
                start=start,
                end=end,
                search=search,
                limit=20000,
                viewer_id=requester_id,
                can_finalize=role_can_finalize,
                paginate=False,
            )
            rows = result.get('rows') or []
            summary = result.get('summary') or {}
            period_start = result.get('start') or ''
            period_end = result.get('end') or ''

            content = _build_low_rating_reviews_workbook(
                rows, summary, period_start, period_end, status_filter, search
            )
            safe_start = (period_start or 'period').replace('-', '')
            safe_end = (period_end or 'period').replace('-', '')
            filename = f"low_ratings_{safe_start}_{safe_end}.xlsx"
            return send_file(
                BytesIO(content),
                as_attachment=True,
                download_name=filename,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )

        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error exporting chat manager low rating reviews: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/chat_manager/metrics/sync_chat2desk', methods=['POST'])
    @require_api_key
    def sync_chat_manager_metrics_chat2desk():
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            if not (_is_admin_role(requester[3]) or _is_supervisor_role(requester[3])):
                return jsonify({"error": "Forbidden"}), 403

            payload = request.get_json(silent=True) or {}
            target_day = payload.get('date') or request.args.get('date') or None
            target_from = (
                payload.get('date_from') or payload.get('start_date') or
                request.args.get('date_from') or request.args.get('start_date') or None
            )
            target_to = (
                payload.get('date_to') or payload.get('end_date') or
                request.args.get('date_to') or request.args.get('end_date') or None
            )
            if target_day and _chat_metrics_parse_date(target_day) is None:
                return jsonify({"error": "date must be in YYYY-MM-DD format"}), 400
            if target_day and (target_from or target_to):
                return jsonify({"error": "Use either date or date_from/date_to, not both"}), 400
            if (target_from or target_to):
                start_day = _chat_metrics_parse_date(target_from)
                end_day = _chat_metrics_parse_date(target_to or target_from)
                if start_day is None or end_day is None:
                    return jsonify({"error": "date_from and date_to must be in YYYY-MM-DD format"}), 400
                if end_day < start_day:
                    return jsonify({"error": "date_to must be greater than or equal to date_from"}), 400
                if (end_day - start_day).days + 1 > 31:
                    return jsonify({"error": "Период синхронизации Chat2Desk не может быть больше 31 дня"}), 400
                target_from = start_day.strftime('%Y-%m-%d')
                target_to = end_day.strftime('%Y-%m-%d')

            result = sync_chat2desk_daily_metrics(
                day=target_day,
                date_from=target_from,
                date_to=target_to,
                triggered_by='manual',
                force=True,
                imported_by=requester_id
            )
            status = result.get('status')
            if status == 'skipped':
                reason = result.get('reason')
                if reason == 'missing_token':
                    return jsonify({"error": "CHAT2DESK_API_TOKEN не задан", "sync": result}), 400
                if reason == 'locked':
                    return jsonify({
                        "error": "Синхронизация Chat2Desk уже выполняется. Повторите через несколько секунд.",
                        "sync": result
                    }), 429
                return jsonify({"error": f"Синхронизация Chat2Desk пропущена: {reason or 'unknown'}", "sync": result}), 400

            days = [item for item in (result.get('days') or []) if isinstance(item, dict)]
            failed_days = [item for item in days if item.get('status') == 'failed']
            success_days = [item for item in days if item.get('status') == 'success']

            if failed_days and not success_days:
                return jsonify({
                    "error": failed_days[0].get('error') or "Синхронизация Chat2Desk не выполнена",
                    "sync": result
                }), 502

            update_fields = sorted({
                field
                for item in success_days
                for field in (item.get('update_fields') or [])
            })
            unmatched_totals = {}
            for item in success_days:
                for row in (item.get('unmatched_operators') or []):
                    name = str(row.get('name') or '').strip()
                    if not name:
                        continue
                    try:
                        count = int(row.get('rows') or 0)
                    except Exception:
                        count = 0
                    unmatched_totals[name] = unmatched_totals.get(name, 0) + max(1, count)
            unmatched_preview = [
                {'name': name, 'rows': count}
                for name, count in sorted(unmatched_totals.items(), key=lambda kv: (-kv[1], kv[0]))[:STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT]
            ]
            import_block = {
                'batch_id': next((item.get('batch_id') for item in success_days if item.get('batch_id')), None),
                'detected_type': CHAT_REPORT_TYPE_COMBINED,
                'detected_label': 'Синхронизация Chat2Desk',
                'source_rows': sum(int(item.get('source_rows') or 0) for item in success_days),
                'matched_rows': sum(int(item.get('matched_rows') or 0) for item in success_days),
                'processed': sum(int(item.get('processed') or 0) for item in success_days),
                'low_rating_count': sum(int(item.get('low_rating_count') or 0) for item in success_days),
                'low_rating_saved': sum(int(item.get('low_rating_saved') or 0) for item in success_days),
                'unmatched_count': sum(int(item.get('unmatched_count') or 0) for item in success_days),
                'excluded_surge_rows': sum(int(item.get('excluded_surge_rows') or 0) for item in success_days),
                'update_fields': update_fields,
                'days': days,
            }
            message = "Метрики Chat2Desk синхронизированы"
            if failed_days:
                message = "Метрики Chat2Desk синхронизированы частично"
            return jsonify({
                "status": "success" if not failed_days else "partial_failed",
                "message": message,
                "import": import_block,
                "sync": result,
                "warnings": {
                    "unmatched_operators": unmatched_preview
                }
            }), 200

        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error syncing Chat2Desk chat manager metrics: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/chat_manager/metrics/import_csv', methods=['POST'])
    @require_api_key
    def import_chat_manager_metrics_csv():
        lock_acquired = False
        raw_bytes = b''
        try:
            requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401
            requester_id = int(requester_id)

            user_data = db.get_user(id=requester_id)
            if not user_data:
                return jsonify({"error": "User not found"}), 404
            if not (_is_admin_role(user_data[3]) or _is_supervisor_role(user_data[3])):
                return jsonify({"error": "Forbidden"}), 403

            file_storage = request.files.get('file')
            if not file_storage:
                return jsonify({"error": "file is required"}), 400

            file_name = secure_filename(file_storage.filename or 'chat_manager_metrics.csv')
            lower_name = file_name.lower()
            if not (lower_name.endswith('.csv') or lower_name.endswith('.xlsx') or lower_name.endswith('.xlsm')):
                return jsonify({"error": "Поддерживаются файлы .csv, .xlsx, .xlsm"}), 400

            default_date = request.form.get('date') or request.args.get('date')
            if default_date:
                if _chat_metrics_parse_date(default_date) is None:
                    return jsonify({"error": "date must be in YYYY-MM-DD format"}), 400

            # Окна «наплыва» для отчёта времени ответа (JSON-массив {start,end,description});
            # по request_start такие строки исключаются из расчёта. Сами окна хранятся
            # отдельно в chat_metric_surge_windows и подтягиваются по surge_month, если
            # форма не передала явный список.
            surge_windows_raw = request.form.get('surge_windows') or request.args.get('surge_windows')
            surge_month = request.form.get('surge_month') or request.args.get('surge_month') or None
            if not surge_month:
                if default_date:
                    parsed_default_day = _chat_metrics_parse_date(default_date)
                    surge_month = parsed_default_day.strftime('%Y-%m') if parsed_default_day else None
                if not surge_month:
                    surge_month = datetime.now().strftime('%Y-%m')
            if not surge_windows_raw:
                try:
                    saved_surge = db.get_chat_metric_surge_windows(surge_month)
                    saved_windows = saved_surge.get('windows') if isinstance(saved_surge, dict) else []
                    if saved_windows:
                        surge_windows_raw = json.dumps(saved_windows, ensure_ascii=False)
                except Exception:
                    logging.exception("failed to load saved chat metric surge windows for %s", surge_month)

            content_length = request.content_length
            if content_length is not None and int(content_length) > STATUS_IMPORT_MAX_FILE_SIZE_BYTES:
                return jsonify({
                    "error": f"Файл слишком большой. Лимит: {STATUS_IMPORT_MAX_FILE_SIZE_MB} MB"
                }), 413

            if not CHAT_MANAGER_METRICS_IMPORT_LOCK.acquire(blocking=False):
                return jsonify({
                    "error": "Импорт метрик чат-менеджеров уже выполняется другим пользователем. Повторите через несколько секунд."
                }), 429
            lock_acquired = True

            read_limit = int(STATUS_IMPORT_MAX_FILE_SIZE_BYTES) + 1
            if getattr(file_storage, 'stream', None) is not None:
                raw_bytes = file_storage.stream.read(read_limit)
            else:
                raw_bytes = file_storage.read(read_limit)
            if not raw_bytes:
                return jsonify({"error": "Файл пустой"}), 400
            if len(raw_bytes) > STATUS_IMPORT_MAX_FILE_SIZE_BYTES:
                return jsonify({
                    "error": f"Файл слишком большой. Лимит: {STATUS_IMPORT_MAX_FILE_SIZE_MB} MB"
                }), 413

            operator_lookup = _status_import_build_operator_lookup(prefer_chat_managers=True)
            operator_token_index = _chat_report_build_operator_token_index()

            # 1) Распознаём тип отчёта по колонкам (оценка / whatsapp-чаты / Name-Requests / время ответа).
            report = None
            try:
                header, data_rows = _chat_report_rows_from_bytes(raw_bytes, file_name)
                report = _chat_report_parse(
                    header, data_rows, operator_lookup, operator_token_index,
                    default_date=default_date, surge_windows=surge_windows_raw,
                    preview_limit=STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT
                )
            except ValueError:
                raise
            except Exception:
                logging.exception("chat report multi-format parse failed; falling back to generic CSV")
                report = None

            if report is not None:
                metrics = report.get('metrics') or []
                if not metrics:
                    empty_import_block = {
                        "batch_id": None,
                        "detected_type": report.get('detected_type'),
                        "detected_label": report.get('detected_label'),
                        "source_rows": int(report.get('source_rows') or 0),
                        "matched_rows": 0,
                        "processed": 0,
                        "unmatched_count": int(report.get('unmatched_count') or 0),
                        "surge_month": surge_month
                    }
                    if report.get('excluded_surge_rows') is not None:
                        empty_import_block["excluded_surge_rows"] = int(report.get('excluded_surge_rows') or 0)
                    return jsonify({
                        "message": "Нет валидных метрик для сохранения",
                        "import": empty_import_block,
                        "warnings": {"unmatched_operators": report.get('unmatched_preview') or []}
                    }), 200

                save_summary = db.save_chat_manager_daily_metrics(
                    metrics=metrics,
                    imported_by=requester_id,
                    update_fields=report.get('update_fields')
                )
                import_block = {
                    **(save_summary or {}),
                    "detected_type": report.get('detected_type'),
                    "detected_label": report.get('detected_label'),
                    "source_rows": int(report.get('source_rows') or 0),
                    "matched_rows": int(report.get('matched_rows') or 0),
                    "unmatched_count": int(report.get('unmatched_count') or 0),
                    "file_name": file_name,
                    "file_size_bytes": len(raw_bytes or b''),
                    "surge_month": surge_month
                }
                if report.get('excluded_surge_rows') is not None:
                    import_block["excluded_surge_rows"] = int(report.get('excluded_surge_rows') or 0)
                return jsonify({
                    "message": f"Импортирован отчёт: {report.get('detected_label')}",
                    "import": import_block,
                    "warnings": {"unmatched_operators": report.get('unmatched_preview') or []}
                }), 200

            # 2) Фолбэк: универсальный CSV-парсер (только .csv). Готовую среднюю оценку
            # он не принимает: оценка обновляется только сырым отчётом/синком Chat2Desk.
            if not lower_name.endswith('.csv'):
                return jsonify({"error": "Не удалось распознать тип отчёта по колонкам файла"}), 400
            csv_text = None
            for enc in ('utf-8-sig', 'cp1251'):
                try:
                    csv_text = raw_bytes.decode(enc)
                    break
                except Exception:
                    continue
            if csv_text is None:
                return jsonify({"error": "Не удалось декодировать CSV (поддерживаются UTF-8 и CP1251)"}), 400

            parsed = _chat_metrics_import_parse_csv(
                csv_text,
                operator_lookup,
                default_date=default_date,
                max_source_rows=STATUS_IMPORT_MAX_SOURCE_ROWS,
                invalid_rows_preview_limit=STATUS_IMPORT_INVALID_ROWS_PREVIEW_LIMIT
            )

            if not parsed.get('metrics'):
                return jsonify({
                    "message": "Нет валидных метрик для сохранения",
                    "import": {
                        "batch_id": None,
                        "source_rows": int(parsed.get('source_rows') or 0),
                        "matched_rows": 0,
                        "processed": 0,
                        "invalid_rows_count": int(parsed.get('invalid_rows_count') or 0)
                    },
                    "warnings": {
                        "invalid_rows_preview": parsed.get('invalid_rows_preview') or []
                    }
                }), 200

            save_summary = db.save_chat_manager_daily_metrics(
                metrics=parsed.get('metrics') or [],
                imported_by=requester_id,
                update_fields=parsed.get('update_fields')
            )

            return jsonify({
                "message": "Метрики чат-менеджеров сохранены",
                "import": {
                    **(save_summary or {}),
                    "source_rows": int(parsed.get('source_rows') or 0),
                    "matched_rows": int(parsed.get('matched_rows') or 0),
                    "invalid_rows_count": int(parsed.get('invalid_rows_count') or 0),
                    "file_name": file_name,
                    "file_size_bytes": len(raw_bytes or b'')
                },
                "warnings": {
                    "invalid_rows_preview": parsed.get('invalid_rows_preview') or []
                }
            }), 200

        except OverflowError as e:
            return jsonify({"error": str(e)}), 413
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error importing chat manager metrics csv: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500
        finally:
            if lock_acquired:
                CHAT_MANAGER_METRICS_IMPORT_LOCK.release()

    return bp
