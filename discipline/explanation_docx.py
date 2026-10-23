# -*- coding: utf-8 -*-
"""Бланк объяснительной в DOCX — по шаблону организации (владелец, 23.10.2026,
прислал «Объяснительная.docx»): справа — кому и от кого, по центру —
«Объяснительная», место для текста, ФИО с подписью и дата.

Подставлено: ФИО и кто пишет (оператор или стажёр) — в «от кого» и над
строкой подписи. Дату ставят от руки: объяснительную датируют днём, когда её
пишут, а не днём, когда скачали бланк. Внизу листа мелко — к какой записи
бланк (номер, дата и тип нарушения): по нему скан находит свою запись, а
человек видит, о чём писать.

Кому адресована объяснительная, в коде нет: репозиторий публичный, а это
реквизиты заказчика. Строку хранит настройка отдела «Бланк объяснительной»
(catalog.explanation_addressee); пусто — в бланке линия, вписать от руки.
"""

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from . import docx_layout as layout

DEFAULT_ADDRESSEE = 'Заказчику: ' + '_' * 30
# Подпись внизу справа, как в шаблоне: ФИО — зазор — подпись. Таблица прижата
# к правому краю, а не сдвинута пустым столбцом: пустую ячейку иные
# просмотрщики схлопывают, и строка подписи уезжала влево. Поэтому и в
# зазоре, и над линией подписи — неразрывный пробел.
SIGNATURE_WIDTHS_CM = (7.0, 0.8, 4.7)
NBSP = '\u00a0'
WRITING_LINES = 12


def _bottom_border(cell):
    """Линия под ячейкой — строка, над которой пишут ФИО и ставят подпись.
    tcBorders по схеме идёт сразу за tcW, которую ставит ширина ячейки."""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement('w:tcBorders')
    bottom = OxmlElement('w:bottom')
    for key, value in (('w:val', 'single'), ('w:sz', '6'), ('w:space', '0'), ('w:color', '000000')):
        bottom.set(qn(key), value)
    borders.append(bottom)
    width = tcPr.find(qn('w:tcW'))
    if width is not None:
        width.addnext(borders)
    else:
        tcPr.insert(0, borders)


def build(record, operator, *, addressee=None, created=None):
    """Байты DOCX бланка. record — запись о нарушении (номер, когда, тип);
    operator — {'name', 'role'}; addressee — строки «кому» из настроек отдела."""
    document = layout.new_document(14, title='Объяснительная %s' % (record.get('number') or '').strip(),
                                   created=created)
    # Шапка уже приведена настройкой (catalog.normalize_addressee): строки — по \n.
    lines = [line.strip() for line in str(addressee or '').split('\n') if line.strip()] or [DEFAULT_ADDRESSEE]
    for line in lines:
        layout.paragraph(document, line, bold=True, align='right', after=4)
    writer = 'Стажёра' if operator.get('role') == 'trainee' else 'Оператора'
    name = operator.get('name') or ''
    layout.paragraph(document, 'от %s: %s' % (writer, name or '_' * 30), bold=True, align='right', before=14)

    layout.paragraph(document, 'Объяснительная', size=16, align='center', before=56, after=18)
    for _index in range(WRITING_LINES):
        # Пустые абзацы с красной строкой: от руки — место для текста, в Word —
        # готовые строки, если объяснительную набирают.
        layout.paragraph(document, align='both', indent_cm=1.25)

    table = layout.borderless_table(document, SIGNATURE_WIDTHS_CM)
    table.alignment = WD_TABLE_ALIGNMENT.RIGHT
    cells = layout.table_row(table, (name or NBSP, NBSP, NBSP), SIGNATURE_WIDTHS_CM, before=24)
    captions = layout.table_row(table, ('Ф.И.О', NBSP, 'подпись'), SIGNATURE_WIDTHS_CM, size=10)
    for cell in list(cells) + list(captions):
        cell.paragraphs[0].alignment = layout.ALIGN['center']
    _bottom_border(cells[0])
    _bottom_border(cells[2])

    layout.paragraph(document, '«____» ___________________ 20___ г.', align='right', before=24)

    moment = record.get('occurred_at')
    reference = 'Запись о нарушении %s от %s%s' % (
        record.get('number') or '', layout.fmt_date(moment),
        ': %s' % record['type_title'] if record.get('type_title') else '')
    footer = document.sections[0].footer.paragraphs[0]
    layout.run(footer, reference, size=9)
    footer.paragraph_format.space_before = Pt(0)
    return layout.to_bytes(document)
