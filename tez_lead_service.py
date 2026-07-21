"""Оркестрация успешек TEZ ОП: загрузка базы лидов и ночная сверка.

Разделение обязанностей:
  - tez_op_leads.py     — чистые правила (нормализация номера, статус лида);
  - tez_first_orders.py — клиент TEZ APP (дата первой поездки);
  - tez_binotel_calls.py — клиент Binotel (история звонков по номеру клиента);
  - этот модуль         — склейка всего вместе поверх Database.

Модуль намеренно не импортирует bot_schedule2: резолвер «имя сотрудника ->
оператор ОП» передаётся снаружи колбэком (`resolve_operator`), иначе получился
бы циклический импорт, а логику нельзя было бы прогнать в тестах.

Почему пайплайн устроен именно так: успешка невозможна без звонка, но узнать
«кто звонил» дёшево (Binotel умеет отдавать историю по номеру клиента пачкой),
а вот выехал ли водитель — приходится спрашивать TEZ APP регулярно, пока он не
выедет. Поэтому сначала спрашиваем про поездки по всей базе месяца, а звонки
поднимаем только по тем, кто реально выехал: их за ночь единицы.
"""

import csv
import io
import logging
from datetime import date, datetime

from tez_op_leads import (
    DEFAULT_MIN_BILLSEC,
    STATUS_SUCCESS,
    as_almaty,
    compute_lead_outcome,
    normalize_kz_phone,
)

log = logging.getLogger(__name__)

# Заголовки, которыми СВ подписывает колонки (файл приходит как fio/phone).
FIO_HEADERS = {'fio', 'фио', 'имя', 'name', 'full_name', 'водитель', 'driver'}
PHONE_HEADERS = {'phone', 'телефон', 'номер', 'phone_number', 'msisdn', 'тел'}
MAX_LEAD_ROWS = 50000


def _norm_header(value):
    return str(value or '').strip().lower().replace(' ', '_')


def _rows_from_csv(raw_bytes):
    text = raw_bytes.decode('utf-8-sig', errors='replace')
    sample = text[:4096]
    delimiter = ';' if sample.count(';') > sample.count(',') else ','
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _rows_from_xlsx(raw_bytes):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    try:
        ws = wb.active
        return [list(row) for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def parse_leads_file(raw_bytes, file_ext):
    """Разбирает файл базы лидов в строки (row_number, ФИО, сырой телефон, phone_norm).

    Ожидаемый формат — две колонки с шапкой `fio` и `phone`. Если шапки нет,
    считаем первую колонку именем, вторую телефоном. Телефон в Excel часто
    хранится числом, поэтому приводим к строке аккуратно (без экспоненты).
    """
    ext = str(file_ext or '').lower()
    if ext in ('.xlsx', '.xlsm'):
        raw_rows = _rows_from_xlsx(raw_bytes)
    elif ext == '.csv':
        raw_rows = _rows_from_csv(raw_bytes)
    else:
        raise ValueError('Поддерживаются только .csv, .xlsx и .xlsm')

    raw_rows = [r for r in raw_rows if any(str(c or '').strip() for c in r)]
    if not raw_rows:
        raise ValueError('Файл пустой')

    fio_idx, phone_idx, start_at = 0, 1, 0
    header = [_norm_header(c) for c in raw_rows[0]]
    if any(h in FIO_HEADERS for h in header) or any(h in PHONE_HEADERS for h in header):
        for idx, name in enumerate(header):
            if name in FIO_HEADERS:
                fio_idx = idx
            elif name in PHONE_HEADERS:
                phone_idx = idx
        start_at = 1

    rows = []
    for offset, raw in enumerate(raw_rows[start_at:], start=start_at + 1):
        if len(rows) >= MAX_LEAD_ROWS:
            break
        fio = str(raw[fio_idx] or '').strip() if fio_idx < len(raw) else ''
        phone_cell = raw[phone_idx] if phone_idx < len(raw) else ''
        if isinstance(phone_cell, float):
            phone_cell = f"{phone_cell:.0f}"
        phone_raw = str(phone_cell or '').strip()
        if not fio and not phone_raw:
            continue
        rows.append((offset, fio, phone_raw, normalize_kz_phone(phone_raw)))
    if not rows:
        raise ValueError('В файле не нашлось ни одной строки с данными')
    return rows


def import_lead_batch(db, department_id, year, month, uploaded_by, file_name, rows):
    """Создаёт загрузку и накатывает строки на помесячную базу."""
    batch_id = db.create_tez_lead_batch(department_id, year, month, uploaded_by, file_name)
    counts = db.import_tez_lead_rows(batch_id, year, month, rows)
    counts['batch_id'] = batch_id
    return counts


def check_batch_already_working(db, batch_id, year, month, first_orders_client, min_billsec=DEFAULT_MIN_BILLSEC):
    """Фоновая проверка свежей загрузки: кто из базы уже выехал раньше нас.

    Владелец просил помечать таких сразу, чтобы операторы не тратили день на
    обзвон тех, кто и так на линии. Проверяем только номера этой загрузки.
    """
    db.set_tez_lead_batch_check_state(batch_id, 'running')
    try:
        phones = db.get_tez_phones_pending_first_order(year, month, batch_id=batch_id)
        if phones:
            first_orders = first_orders_client.fetch_first_orders(phones)
            db.save_tez_first_orders(first_orders)
        outcomes = recompute_outcomes(db, year, month, min_billsec=min_billsec)
        db.set_tez_lead_batch_check_state(
            batch_id, 'done', already_working=outcomes.get('already_working', 0)
        )
        return outcomes
    except Exception as exc:
        log.error('Проверка базы лидов %s не удалась: %s', batch_id, exc, exc_info=True)
        db.set_tez_lead_batch_check_state(batch_id, 'error', error=str(exc))
        raise


def sync_first_orders(db, year, month, first_orders_client):
    """Шаг 1 ночной джобы: спрашиваем TEZ APP по всем ещё не выехавшим лидам месяца."""
    phones = db.get_tez_phones_pending_first_order(year, month)
    if not phones:
        return {'checked': 0, 'found': 0}
    first_orders = first_orders_client.fetch_first_orders(phones)
    found = db.save_tez_first_orders(first_orders)
    return {'checked': len(phones), 'found': found}


def sync_calls_for_converted(db, year, month, binotel_client, resolve_operator,
                             min_billsec=DEFAULT_MIN_BILLSEC):
    """Шаг 2: история звонков только по тем, кто уже выехал.

    resolve_operator(employee_name, call_date) должен вернуть id оператора ОП
    либо None. Именно тут отсекаются звонки ТП/линии: по решению владельца они
    не должны перехватывать успешку у отдела продаж.
    """
    phones = db.get_tez_phones_needing_calls(year, month)
    if not phones:
        return {'phones': 0, 'calls': 0}

    raw_calls = binotel_client.list_calls_by_external_numbers(phones)
    wanted = set(phones)
    prepared = []
    for call in raw_calls:
        phone_norm = normalize_kz_phone(call.get('external_number'))
        if not phone_norm or phone_norm not in wanted:
            continue
        started_at = as_almaty(call.get('start_time'))
        if started_at is None:
            continue
        operator_id = resolve_operator(call.get('employee_name'), started_at.date())
        prepared.append({
            'general_call_id': call.get('general_call_id'),
            'phone_norm': phone_norm,
            'started_at': started_at,
            'call_type': call.get('call_type'),
            'billsec': call.get('billsec'),
            'waitsec': call.get('waitsec'),
            'disposition': call.get('disposition'),
            'internal_number': call.get('internal_number'),
            'employee_name': call.get('employee_name'),
            'employee_email': call.get('employee_email'),
            'operator_id': operator_id,
            'is_qualifying': (
                int(call.get('call_type', -1)) == 1
                and int(call.get('billsec') or 0) >= int(min_billsec)
                and operator_id is not None
            ),
        })
    saved = db.save_tez_lead_calls(prepared)
    return {'phones': len(phones), 'calls': saved}


def recompute_outcomes(db, year, month, min_billsec=DEFAULT_MIN_BILLSEC, month_closed_before=None):
    """Шаг 3: пересчёт статусов лидов и успешек. Идемпотентен.

    month_closed_before — необязательная дата закрытия расчётного периода:
    успешки, найденные после неё, помечаются is_late, чтобы поздняя загрузка
    базы задним числом была видна, а не всплывала в выплате молча.
    """
    leads = db.get_tez_leads_for_recompute(year, month)
    if not leads:
        return {'success': 0, 'already_working': 0, 'not_counted': 0, 'in_progress': 0, 'new': 0}

    names_by_call = {}
    for lead in leads:
        for call in lead['calls']:
            names_by_call[call.get('general_call_id')] = call.get('employee_name') or ''

    outcomes = []
    for lead in leads:
        outcome = compute_lead_outcome(lead['first_order_at'], lead['calls'], min_billsec=min_billsec)
        item = {
            'lead_id': lead['id'],
            'phone_norm': lead['phone_norm'],
            'status': outcome['status'],
            'rule': outcome['rule'],
            'operator_id': outcome['operator_id'],
        }
        if outcome['status'] == STATUS_SUCCESS:
            call = outcome['call'] or {}
            success_date = outcome['success_date']
            item.update({
                'operator_name': names_by_call.get(call.get('general_call_id'), ''),
                'call_general_id': call.get('general_call_id'),
                'call_at': outcome['call_at'],
                'first_order_at': outcome['first_order_at'],
                'success_date': success_date,
                # Успешка живёт в месяце ПОЕЗДКИ: лид из июньской базы может дать
                # успешку в июле (звонок в июне, поездка до 7-го числа).
                'success_year': success_date.year,
                'success_month': success_date.month,
                'is_late': bool(month_closed_before and success_date < month_closed_before),
            })
        outcomes.append(item)
    return db.apply_tez_lead_outcomes(year, month, outcomes)


def run_nightly(db, first_orders_client, binotel_client, resolve_operator,
                today=None, min_billsec=DEFAULT_MIN_BILLSEC):
    """Полный ночной цикл для текущего месяца (и прошлого — в первую неделю).

    Прошлый месяц добираем 1–7 числа: по правилам владельца звонок из прошлого
    месяца ещё может дать успешку, если поездка состоялась в первую неделю.
    """
    today = today or date.today()
    periods = [(today.year, today.month)]
    if today.day <= 7:
        prev_year, prev_month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        periods.append((prev_year, prev_month))

    report = {}
    for year, month in periods:
        key = f"{year}-{month:02d}"
        try:
            orders = sync_first_orders(db, year, month, first_orders_client)
            calls = sync_calls_for_converted(db, year, month, binotel_client,
                                             resolve_operator, min_billsec=min_billsec)
            outcomes = recompute_outcomes(db, year, month, min_billsec=min_billsec)
            report[key] = {'first_orders': orders, 'calls': calls, 'outcomes': outcomes}
            log.info('Успешки TEZ ОП %s: проверено %s, выехали %s, звонков %s, успешек %s',
                     key, orders.get('checked'), orders.get('found'),
                     calls.get('calls'), outcomes.get('success'))
        except Exception as exc:
            log.error('Ночная сверка успешек TEZ ОП за %s упала: %s', key, exc, exc_info=True)
            report[key] = {'error': str(exc)}
    return report
