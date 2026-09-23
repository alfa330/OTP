# -*- coding: utf-8 -*-
"""Разбор файла «ФИО + телефон» (CSV / Excel) — общий для всех баз обзвона.

Формат один на весь проект: две колонки с шапкой `fio` и `phone` (шапка
необязательна — тогда первая колонка имя, вторая телефон). Телефон в Excel
часто хранится числом, поэтому приводится к строке без экспоненты.
"""
import csv
import io

from common.kz_phone import normalize_kz_phone

# Заголовки, которыми подписывают колонки (файл приходит как fio/phone).
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


def parse_leads_file(raw_bytes, file_ext, max_rows=None):
    """Разбирает файл в строки (row_number, ФИО, сырой телефон, phone_norm).

    max_rows — потолок строк с данными (по умолчанию MAX_LEAD_ROWS); файл крупнее
    отклоняется целиком, а не обрезается молча."""
    limit = int(max_rows) if max_rows is not None else MAX_LEAD_ROWS
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
        fio = str(raw[fio_idx] or '').strip() if fio_idx < len(raw) else ''
        phone_cell = raw[phone_idx] if phone_idx < len(raw) else ''
        if isinstance(phone_cell, float):
            phone_cell = f"{phone_cell:.0f}"
        phone_raw = str(phone_cell or '').strip()
        if not fio and not phone_raw:
            continue
        if len(rows) >= limit:
            raise ValueError(
                f'В файле больше {limit} строк с данными. '
                'Разделите его на несколько файлов.'
            )
        rows.append((offset, fio, phone_raw, normalize_kz_phone(phone_raw)))
    if not rows:
        raise ValueError('В файле не нашлось ни одной строки с данными')
    return rows
