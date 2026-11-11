"""Файл «список операторов» для Syntony (ТЗ, раздел 3, п. 4).

Syntony просит о каждом операторе три вещи: постоянный id в iCORE, ФИО и рабочую
почту — по почте её администратор находит сотрудника Syntony. Файл собирается из
списка доступа раздела: кто в списке, тот и оператор.

Один лист и три столбца — ровно то, что просили: файл уходит во внешнюю компанию,
и ни должности, ни отдела, ни «кто выдал» в нём быть не должно. Уволенных в файле
нет: окно им уже не открыть, и связывать их у Syntony незачем.

id лежит ЧИСЛОМ: это счётчик без ведущих нулей, в запросах он уходит той же
записью (rules.build_request_body), и зелёного уголка «число как текст» не будет.
"""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
SHEET_TITLE = 'Операторы'
COLUMNS = (
    ('id', 'ID оператора в iCORE', 22),
    ('name', 'ФИО', 42),
    ('email', 'Рабочая почта', 38),
)

_HEADER_FILL = PatternFill('solid', fgColor='1F2937')
_HEADER_FONT = Font(bold=True, color='FFFFFF')
_HEADER_ALIGN = Alignment(horizontal='center', vertical='center', wrap_text=True)


def _text(value):
    return ILLEGAL_CHARACTERS_RE.sub('', str(value or ''))


def export_rows(items):
    """Строки файла: работающие люди списка, по алфавиту (как на экране)."""
    return [
        {'id': int(item['id']), 'name': _text(item.get('name')), 'email': _text(item.get('email'))}
        for item in items
        if item.get('working')
    ]


def build_workbook(items):
    """Книга xlsx в памяти → BytesIO, готовый к send_file."""
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_TITLE
    for index, (_key, title, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=1, column=index, value=title)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _HEADER_ALIGN
        sheet.column_dimensions[cell.column_letter].width = width
    sheet.freeze_panes = 'A2'
    for row in export_rows(items):
        sheet.append([row[key] for key, _title, _width in COLUMNS])
    stream = BytesIO()
    book.save(stream)
    stream.seek(0)
    return stream


def export_file_name(today):
    """«Операторы iCORE для Syntony 11.11.2026.xlsx» — today: date."""
    return 'Операторы iCORE для Syntony %s.xlsx' % today.strftime('%d.%m.%Y')
