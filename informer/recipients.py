# -*- coding: utf-8 -*-
"""Список водителей рассылки из файла: CSV или Excel, телефон обязателен, ФИО — по желанию.

Разбор строк — общий с базами обзвона (common.leads_file), номер — общая нормализация
(common.kz_phone). Отличие от базы «Удаленного КЦ»: ИИН не нужен, а файл может быть просто
столбцом номеров — тогда колонку телефона находим по содержимому."""
from common.kz_phone import normalize_kz_phone
from common.leads_file import FIO_HEADERS, PHONE_HEADERS, _norm_header, _rows_from_csv, _rows_from_xlsx

MAX_ROWS = 50000
_SNIFF_ROWS = 50


def _cell(value):
    if isinstance(value, float) and value.is_integer():
        return f"{value:.0f}"
    return str(value if value is not None else '').strip()


def _phone_column(rows):
    """Колонка, где больше всего настоящих казахстанских номеров среди первых строк."""
    width = max((len(r) for r in rows[:_SNIFF_ROWS]), default=0)
    best, best_hits = None, 0
    for idx in range(width):
        hits = sum(1 for r in rows[:_SNIFF_ROWS] if idx < len(r) and normalize_kz_phone(_cell(r[idx])))
        if hits > best_hits:
            best, best_hits = idx, hits
    return best


def parse(raw_bytes, file_ext):
    """{'rows': [(номер строки, ФИО, телефон 77XXXXXXXXX)], 'invalid': n, 'duplicates': n}.

    Повтор номера в файле — одна строка (первая): звонить водителю дважды за рассылку незачем.
    Невалидный номер — в счётчик invalid, а не в отказ всему файлу. ValueError — файл не годится."""
    ext = str(file_ext or '').lower()
    if ext in ('.xlsx', '.xlsm'):
        raw_rows = _rows_from_xlsx(raw_bytes)
    elif ext == '.csv':
        raw_rows = _rows_from_csv(raw_bytes)
    else:
        raise ValueError('Список водителей — файл .csv, .xlsx или .xlsm')
    raw_rows = [r for r in raw_rows if any(_cell(c) for c in r)]
    if not raw_rows:
        raise ValueError('Файл со списком пустой')

    header = [_norm_header(c) for c in raw_rows[0]]
    phone_idx = next((i for i, h in enumerate(header) if h in PHONE_HEADERS), None)
    name_idx = next((i for i, h in enumerate(header) if h in FIO_HEADERS), None)
    start_at = 1 if (phone_idx is not None or name_idx is not None) else 0
    data = raw_rows[start_at:]
    if phone_idx is None:
        phone_idx = _phone_column(data)
        if phone_idx is None:
            raise ValueError('В файле не нашлось ни одного казахстанского номера')
    if name_idx is None and start_at == 0:
        # Без шапки ФИО — первая колонка, где не номер (если такая есть).
        width = max(len(r) for r in data[:_SNIFF_ROWS])
        name_idx = next((i for i in range(width) if i != phone_idx), None)

    rows, seen, invalid, duplicates = [], set(), 0, 0
    for offset, raw in enumerate(data, start=start_at + 1):
        phone = normalize_kz_phone(_cell(raw[phone_idx])) if phone_idx < len(raw) else None
        if not phone:
            invalid += 1
            continue
        if phone in seen:
            duplicates += 1
            continue
        if len(rows) >= MAX_ROWS:
            raise ValueError(f'В файле больше {MAX_ROWS} номеров — разделите его на несколько рассылок')
        seen.add(phone)
        name = _cell(raw[name_idx]) if name_idx is not None and name_idx < len(raw) else ''
        if normalize_kz_phone(name):
            name = ''  # во «второй колонке» оказался тоже номер — это не имя
        rows.append((offset, name[:200], phone))
    if not rows:
        raise ValueError('В файле не нашлось ни одного казахстанского номера')
    return {'rows': rows, 'invalid': invalid, 'duplicates': duplicates}
