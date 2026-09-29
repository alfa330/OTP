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
# ИИН водителя — колонка базы «Обзвона» (with_iin=True): по нему проверяется
# подписание документов. Другим базам (TEZ) она не нужна и не ищется.
IIN_HEADERS = {'iin', 'иин', 'iin_driver', 'driver_iin', 'иин_водителя', 'iin_водителя'}
IIN_MISSING_MESSAGE = ('В файле нет колонки «iin» с ИИН водителя. Добавьте её: '
                       'без ИИН нельзя проверить подписание документов.')
MAX_LEAD_ROWS = 50000


def _iin_cell(value):
    """Ячейка ИИН → строка. Excel хранит ИИН числом и теряет ведущий ноль у
    родившихся в 2000-х (050312… → 50312…): число дополняем до 12 цифр. Строку
    отдаём как есть — её проверит правило ИИН у вызывающего."""
    if isinstance(value, bool):
        return ''
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not value.is_integer():
            return str(value)
        number = int(value)
        return f"{number:012d}" if 0 < number < 10 ** 12 else str(number)
    return str(value or '').strip()


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


def parse_leads_file(raw_bytes, file_ext, max_rows=None, with_iin=False):
    """Разбирает файл в строки (row_number, ФИО, сырой телефон, phone_norm).

    max_rows — потолок строк с данными (по умолчанию MAX_LEAD_ROWS); файл крупнее
    отклоняется целиком, а не обрезается молча.

    with_iin — колонка ИИН обязательна («Обзвон»): строки становятся
    (row_number, ФИО, сырой телефон, phone_norm, ИИН как в файле). Нет колонки —
    ValueError: без неё база бесполезна, и молча загрузить её нельзя. Без шапки
    ИИН — третья колонка."""
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

    fio_idx, phone_idx, iin_idx, start_at = 0, 1, None, 0
    header = [_norm_header(c) for c in raw_rows[0]]
    known = FIO_HEADERS | PHONE_HEADERS | (IIN_HEADERS if with_iin else set())
    if any(h in known for h in header):
        for idx, name in enumerate(header):
            if name in FIO_HEADERS:
                fio_idx = idx
            elif name in PHONE_HEADERS:
                phone_idx = idx
            elif with_iin and name in IIN_HEADERS:
                iin_idx = idx
        start_at = 1
    elif with_iin and max(len(r) for r in raw_rows) >= 3:
        iin_idx = 2
    if with_iin and iin_idx is None:
        raise ValueError(IIN_MISSING_MESSAGE)

    rows = []
    for offset, raw in enumerate(raw_rows[start_at:], start=start_at + 1):
        fio = str(raw[fio_idx] or '').strip() if fio_idx < len(raw) else ''
        phone_cell = raw[phone_idx] if phone_idx < len(raw) else ''
        if isinstance(phone_cell, float):
            phone_cell = f"{phone_cell:.0f}"
        phone_raw = str(phone_cell or '').strip()
        iin_raw = _iin_cell(raw[iin_idx]) if with_iin and iin_idx < len(raw) else ''
        if not fio and not phone_raw and not iin_raw:
            continue
        if len(rows) >= limit:
            raise ValueError(
                f'В файле больше {limit} строк с данными. '
                'Разделите его на несколько файлов.'
            )
        if with_iin:
            rows.append((offset, fio, phone_raw, normalize_kz_phone(phone_raw), iin_raw))
        else:
            rows.append((offset, fio, phone_raw, normalize_kz_phone(phone_raw)))
    if not rows:
        raise ValueError('В файле не нашлось ни одной строки с данными')
    return rows
