"""Разбор файла готовых смен для аукциона Тез КЦ (задача #124).

Файл — тот же Excel, что руководитель загружает в «Графики работы»: первая строка —
«ФИО», «Ставка» и даты, дальше строка на человека, в клетках смены вида «9*18»,
«13*19/30», «21*09», «11*15 18*20» (день из двух кусков) или «Выходной».

Для аукциона имена и ставки строк НЕ учитываются (решение владельца 12.11.2026):
каждая смена из клетки — это смена, которую выберут в аукционе. Клетка из двух
кусков даёт две смены. Пустые клетки и «Выходной» пропускаются, нераспознанные —
не теряются молча, а возвращаются списком для сверки.

Разбор клеток и заголовка — общий с импортом графиков (функции приходят
аргументами из bot_schedule2): один формат не должен читаться двумя способами.
"""
from io import BytesIO

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

SHIFT_AUCTION_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
# Строка с датами — первая, но выше неё бывает заголовок листа: ищем в первых строках.
SHIFT_AUCTION_UPLOAD_HEADER_SCAN_ROWS = 10
SHIFT_AUCTION_UPLOAD_MAX_ROWS = 2000
SHIFT_AUCTION_UPLOAD_VALUE_PREVIEW = 60


class ShiftAuctionUploadError(ValueError):
    """Файл не разобрать: код для API и текст для человека."""

    def __init__(self, code, message):
        super().__init__(code)
        self.code = code
        self.message = message


def parse_shift_auction_upload(file_bytes, parse_header_date, parse_cell_value,
                               max_rows=SHIFT_AUCTION_UPLOAD_MAX_ROWS):
    """Смены из файла: {dates, shifts, invalid_cells, day_off_cells, header_row}.

    ``shifts`` — список {date: 'YYYY-MM-DD', start: 'HH:MM', end: 'HH:MM', cell: 'C3'}
    в порядке клеток файла; ``invalid_cells`` — {cell, date, value, error}.
    """
    if not file_bytes:
        raise ShiftAuctionUploadError("AUCTION_UPLOAD_FILE_EMPTY", "Файл пустой")
    try:
        workbook = load_workbook(filename=BytesIO(file_bytes), data_only=True)
    except Exception:
        raise ShiftAuctionUploadError(
            "AUCTION_UPLOAD_UNREADABLE",
            "Файл не читается как таблица Excel (.xlsx)",
        )
    sheet = workbook.active

    header_row = None
    date_columns = []
    for row_index in range(1, min(sheet.max_row or 0, SHIFT_AUCTION_UPLOAD_HEADER_SCAN_ROWS) + 1):
        columns = []
        for cell in sheet[row_index]:
            day = parse_header_date(cell)
            if day:
                columns.append((cell.column, day))
        if columns:
            header_row = row_index
            date_columns = columns
            break
    if not date_columns:
        raise ShiftAuctionUploadError(
            "AUCTION_UPLOAD_NO_DATES",
            "В файле не нашлась строка с датами — первая строка должна быть «ФИО, Ставка, даты…»",
        )

    shifts = []
    invalid_cells = []
    day_off_cells = 0
    last_row = min(sheet.max_row or header_row, header_row + max(1, int(max_rows)))
    for row_index in range(header_row + 1, last_row + 1):
        for column_index, day in date_columns:
            raw_value = sheet.cell(row=row_index, column=column_index).value
            parsed = parse_cell_value(raw_value) or {}
            kind = parsed.get("kind")
            if kind == "empty":
                continue
            address = f"{get_column_letter(column_index)}{row_index}"
            if kind == "day_off":
                day_off_cells += 1
                continue
            if kind != "shifts":
                invalid_cells.append({
                    "cell": address,
                    "date": day.isoformat(),
                    "value": str(raw_value if raw_value is not None else "")[:SHIFT_AUCTION_UPLOAD_VALUE_PREVIEW],
                    "error": str(parsed.get("error") or "Клетка не похожа на смену или «Выходной»"),
                })
                continue
            for shift in parsed.get("shifts") or []:
                start = str(shift.get("start") or "")
                end = str(shift.get("end") or "")
                if not start or not end or start == end:
                    invalid_cells.append({
                        "cell": address,
                        "date": day.isoformat(),
                        "value": str(raw_value)[:SHIFT_AUCTION_UPLOAD_VALUE_PREVIEW],
                        "error": "Начало и конец смены совпадают",
                    })
                    continue
                shifts.append({
                    "date": day.isoformat(),
                    "start": start,
                    "end": end,
                    "cell": address,
                })

    sheet_rows = sheet.max_row or header_row
    return {
        "dates": sorted({day.isoformat() for _column, day in date_columns}),
        "shifts": shifts,
        "invalid_cells": invalid_cells,
        "day_off_cells": day_off_cells,
        "header_row": header_row,
        # Читается только активный лист — его имя сверка покажет, если листов несколько,
        # чтобы «не тот лист» было видно до записи. Строки сверх предела — не молча.
        "sheet_name": sheet.title,
        "sheet_count": len(workbook.worksheets),
        "truncated_after_row": last_row if sheet_rows > last_row else None,
    }
