# -*- coding: utf-8 -*-
"""Печатная форма акта (Word): приём в учёт, перемещение, списание, передача при увольнении.

ТЗ: бухгалтерия «формирует акт перемещения, списания и передачи ОС». Акт собирается из
самого запроса — стороны, ОС с инвентарными номерами, основание — и уходит на подпись
бумагой; подписанный скан потом прикладывают к запросу. Word, а не PDF: бухгалтерии бывает
нужно дописать строку от руки (амортизацию, заключение комиссии).

СПИСАНИЕ — официальная форма ДА-3 «Акт на выбытие (списание) долгосрочных активов»
(приложение 18 к приказу Министра финансов РК от 20.12.2012 № 562), по образцу бухгалтерии
от 17.11.2026: шапка с организацией и БИН, «Утверждаю», таблица из десяти граф, комиссия и
оборотная сторона. Система заполняет то, что знает: наименование, дату приобретения, срок
эксплуатации, первоначальную стоимость, заводской и инвентарный номера, причину списания и
заключение проверяющего. Амортизацию и балансовую стоимость портал не ведёт — эти графы
остаются пустыми, их вписывает бухгалтерия.

Название организации, БИН, руководитель и комиссия в коде не записаны (репозиторий
открытый): их вписывает бухгалтерия в настройках раздела; пока не вписала — на бумаге
остаются пустые строки.

Здесь нет ни базы, ни Flask: на вход — запрос, его ОС и подписанты, на выход — байты .docx.
"""

from io import BytesIO

from . import rules

_MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября',
           'октября', 'ноября', 'декабря')
_BLANK = '____________________'


def date_words(day):
    """«17» ноября 2026 г."""
    if not day:
        return '«___» __________ 20___ г.'
    return '«%02d» %s %d г.' % (day.day, _MONTHS[day.month - 1], day.year)


def date_long(day):
    """13 апреля 2023 г. — как в образце формы ДА-3."""
    if not day:
        return ''
    return '%d %s %d г.' % (day.day, _MONTHS[day.month - 1], day.year)


def date_short(day):
    return day.strftime('%d.%m.%Y') if day else ''


def money(value):
    """141210 → «141 210,00»."""
    if value in (None, ''):
        return ''
    return '{:,.2f}'.format(float(value)).replace(',', ' ').replace('.', ',')


def short_name(full_name):
    """«Иванов Иван Иванович» → «Иванов И. И.» — для расшифровки подписи."""
    parts = str(full_name or '').split()
    if len(parts) < 2:
        return str(full_name or '').strip()
    return '%s %s' % (parts[0], ' '.join('%s.' % part[0] for part in parts[1:3]))


def file_name(request):
    return '%s № %s.docx' % (rules.ACT_SHORT.get(request.get('kind'), 'Акт'), request.get('id'))


def place_text(department, location, on_stock=False):
    parts = [part for part in (department, location) if part]
    if on_stock:
        parts.append('склад')
    return ', '.join(parts)


def _person_line(name, title=None, department=None):
    parts = [part for part in (name, title, department) if part]
    return ', '.join(parts) or _BLANK


def organization_line(requisites):
    """Строка «Организация» в шапке актов: название и БИН из настроек либо пустая строка."""
    requisites = requisites or {}
    name = requisites.get('org_name')
    if not name:
        return 'Организация: ______________________________________________'
    return 'Организация: %s%s' % (name, ', БИН %s' % requisites['org_bin'] if requisites.get('org_bin') else '')


def table_columns(kind):
    """Колонки таблицы ОС по виду акта: [(заголовок, ключ, ширина в см)]."""
    base = [('№', 'n', 1.0), ('Наименование', 'name', 6.0), ('Инвентарный №', 'inventory_number', 3.0),
            ('Серийный №', 'serial_number', 3.2)]
    if kind in rules.MOVING_KINDS:
        return base + [('Где находилось', 'before_place', 3.4)]
    return base + [('Категория', 'category_name', 3.4)]


def table_rows(request, items):
    rows = []
    for number, item in enumerate(items or [], start=1):
        rows.append({
            'n': str(number),
            'name': item.get('name') or '',
            'inventory_number': item.get('inventory_number') or '',
            'serial_number': item.get('serial_number') or '—',
            'category_name': item.get('category_name') or '',
            'before_place': place_text(item.get('before_department_name'), item.get('before_location'),
                                       item.get('before_on_stock')) or '—',
        })
    return rows


def statement(request):
    """Текст после таблицы: что именно стороны удостоверяют подписью."""
    kind = request.get('kind')
    if kind in rules.MOVING_KINDS:
        return ('Перечисленные основные средства осмотрены и переданы. Принявшая сторона принимает их под '
                'материальную ответственность с даты подписания акта.')
    return ('Перечисленные основные средства приняты к учёту и переданы материально ответственному лицу '
            'с даты подписания акта.')


def header_lines(request, people):
    """Строки «Сдал / Принял / Основание» над таблицей: [(подпись, текст)]."""
    kind = request.get('kind')
    people = people or {}
    sender = people.get(request.get('from_user_id')) or {}
    receiver = people.get(request.get('to_user_id')) or {}
    lines = []
    if kind in rules.MOVING_KINDS:
        lines.append(('Сдал', _person_line(request.get('from_name'), sender.get('job_title'),
                                           request.get('from_department_name'))))
        lines.append(('Принял', _person_line(request.get('to_name'), receiver.get('job_title'),
                                             request.get('to_department_name'))))
        place = place_text(None, request.get('to_location'), request.get('to_stock'))
        if place:
            lines.append(('Новое место', place))
        if request.get('comment'):
            lines.append(('Основание', request['comment']))
    else:
        lines.append(('Принял (МОЛ)', _person_line(request.get('to_name'), receiver.get('job_title'),
                                                   request.get('to_department_name'))))
        place = place_text(None, request.get('to_location'), request.get('to_stock'))
        if place:
            lines.append(('Место', place))
    lines.append(('Основание в системе', 'запрос № %s в разделе «Основные средства» iCore' % request.get('id')))
    return lines


# ─── Форма ДА-3: что в неё попадает (без Word — проверяется тестами) ──────────

DA3_HEADINGS = (
    'Номер по порядку', 'Наименование, характеристика', 'Дата выпуска, постройки, регистрации',
    'Дата приобретения (доступа для использования)', 'Фактический срок эксплуатации',
    'Первоначальная стоимость', 'Сумма начисленной амортизации, в теңге', 'Балансовая стоимость, в теңге',
    'заводской', 'инвентарный',
)


# Те же заголовки с переносами образца: Word сам рвёт «регистрации» посреди слова как попало.
DA3_HEADING_LINES = (
    'Номер по порядку', 'Наименование,\nхарактеристика', 'Дата\nвыпуска,\nпостройки,\nрегистра-\nции',
    'Дата\nприобре-\nтения\n(доступа\nдля\nиспользо-\nвания)', 'Фактический срок\nэксплуатации',
    'Первоначаль-\nная\nстоимость', 'Сумма\nначисленной\nамортизации, в\nтеңге',
    'Балансовая\nстоимость, в\nтеңге', 'заводской', 'инвентарный',
)


def da3_rows(request, items, *, with_cost=False):
    """Строки таблицы формы ДА-3: по списку из десяти граф на каждое ОС.

    Графы 3, 7 и 8 (дата выпуска, амортизация, балансовая стоимость) портал не ведёт — они
    пустые. Стоимость (графа 6) печатается только тем, кому она видна в карточке ОС."""
    rows = []
    for number, item in enumerate(items or [], start=1):
        asset = item.get('asset') or {}
        months = rules.service_months(asset.get('acquired_on'), request.get('act_date'))
        rows.append([
            str(number),
            item.get('name') or '',
            '',
            date_long(asset.get('acquired_on')),
            '' if months is None else str(months),
            money(asset.get('cost')) if with_cost else '',
            '',
            '',
            item.get('serial_number') or '',
            item.get('inventory_number') or '',
        ])
    return rows


def da3_commission(request, requisites=None, people=None, heads=None):
    """Кто подписывает оборотную сторону формы ДА-3.

    Председатель и члены комиссии — из настроек раздела (её назначают приказом). К членам
    добавляются те, чьи подписи требует ТЗ («подписи ответственных лиц и руководителей»):
    проверивший ОС перед списанием и руководитель отдела, — если их ещё нет в комиссии."""
    requisites = requisites or {}
    people = people or {}
    heads = heads or {}
    chairman = (requisites.get('chairman_title') or '', short_name(requisites.get('chairman_name')))
    members = [(row.get('title') or '', short_name(row.get('name'))) for row in requisites.get('members') or ()
               if isinstance(row, dict)]
    mol_person = people.get(request.get('from_user_id')) or {}
    mol = (mol_person.get('job_title') or '', short_name(request.get('from_name')))

    def known(name):
        return bool(name) and name in ([chairman[1], mol[1]] + [member[1] for member in members])

    checker = short_name(request.get('checked_by_name'))
    if checker and not known(checker):
        default = 'IT-специалист' if request.get('review_by') == rules.REVIEW_IT else 'Руководитель отдела'
        title = (people.get(request.get('checked_by')) or {}).get('job_title') or default
        members.append((title, checker))
    head = short_name(heads.get(request.get('from_department_id')))
    if head and not known(head):
        members.append(('Руководитель отдела', head))
    return {'chairman': chairman, 'members': members, 'mol': mol}


def da3_order_line(requisites):
    """«Комиссия, назначенная приказом от … года № …» — с данными приказа или с пропусками."""
    requisites = requisites or {}
    day = rules.clean_date(requisites.get('commission_order_date')) if requisites.get('commission_order_date') else None
    return 'Комиссия, назначенная приказом от %s года № %s' % (
        date_short(day) or '«___» __________ 20___', requisites.get('commission_order_number') or '_____')


# ─── Word ────────────────────────────────────────────────────────────────────

def _docx():
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt
    return Document, WD_TABLE_ALIGNMENT, WD_ALIGN_PARAGRAPH, OxmlElement, qn, Cm, Pt


def build(request, items, signers, people=None, *, requisites=None, with_cost=False, heads=None):
    """Собирает акт. Возвращает байты .docx."""
    if request.get('kind') == rules.KIND_WRITEOFF:
        return build_da3(request, items, people, requisites, with_cost=with_cost, heads=heads)
    Document, WD_TABLE_ALIGNMENT, WD_ALIGN_PARAGRAPH, _OxmlElement, qn, Cm, Pt = _docx()

    kind = request.get('kind')
    document = Document()
    section = document.sections[0]
    # Лист A4, как у формы ДА-3: заготовка python-docx — американский Letter.
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin, section.right_margin = Cm(2.5), Cm(1.5)
    section.top_margin, section.bottom_margin = Cm(2), Cm(2)

    style = document.styles['Normal']
    style.font.name = 'Times New Roman'
    style.font.size = Pt(12)
    style.element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')

    def paragraph(text='', *, bold=False, align=None, size=None, after=6):
        item = document.add_paragraph()
        run = item.add_run(text)
        run.bold = bold
        if size:
            run.font.size = Pt(size)
        if align:
            item.alignment = align
        item.paragraph_format.space_after = Pt(after)
        item.paragraph_format.space_before = Pt(0)
        return item

    paragraph(organization_line(requisites), after=14)
    paragraph('АКТ № %s' % request.get('id'), bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, size=14, after=2)
    title = rules.ACT_TITLES.get(kind, 'Акт')
    paragraph(title[len('Акт '):] if title.startswith('Акт ') else title, bold=True,
              align=WD_ALIGN_PARAGRAPH.CENTER, after=10)
    paragraph(date_words(request.get('act_date')), align=WD_ALIGN_PARAGRAPH.RIGHT, after=10)

    for label, text in header_lines(request, people):
        item = document.add_paragraph()
        item.paragraph_format.space_after = Pt(3)
        item.add_run('%s: ' % label).bold = True
        item.add_run(text)

    columns = table_columns(kind)
    rows = table_rows(request, items)
    table = document.add_table(rows=1, cols=len(columns))
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for cell, (heading, _key, width) in zip(table.rows[0].cells, columns):
        cell.width = Cm(width)
        cell.text = ''
        run = cell.paragraphs[0].add_run(heading)
        run.bold = True
        run.font.size = Pt(10.5)
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    for row in rows:
        cells = table.add_row().cells
        for cell, (_heading, key, width) in zip(cells, columns):
            cell.width = Cm(width)
            cell.text = ''
            run = cell.paragraphs[0].add_run(row.get(key) or '')
            run.font.size = Pt(10.5)
            if key == 'n':
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    paragraph('', after=2)
    paragraph('Всего: %d ед.' % len(rows), after=6)
    paragraph(statement(request), after=14)

    paragraph('Подписи:', bold=True, after=8)
    for role, name in signers or ():
        item = document.add_paragraph()
        item.paragraph_format.space_after = Pt(10)
        item.add_run('%s' % role)
        item.add_run('\n_______________ / %s /     «___» __________ 20___ г.' % (short_name(name) or '________________'))

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_da3(request, items, people=None, requisites=None, *, with_cost=False, heads=None):
    """Акт на списание по форме ДА-3. Возвращает байты .docx."""
    Document, WD_TABLE_ALIGNMENT, WD_ALIGN_PARAGRAPH, OxmlElement, qn, Cm, Pt = _docx()
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
    from docx.enum.text import WD_BREAK

    requisites = requisites or {}
    CENTER, RIGHT, LEFT = WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.RIGHT, WD_ALIGN_PARAGRAPH.LEFT
    FONT = 'Arial'
    PAGE = 18.4          # ширина полосы, см (A4 минус поля)

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin, section.right_margin = Cm(1.6), Cm(1.0)
    section.top_margin, section.bottom_margin = Cm(1.3), Cm(1.3)
    style = document.styles['Normal']
    style.font.name = FONT
    style.font.size = Pt(9)
    style.element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
    style.paragraph_format.space_after = Pt(0)
    style.paragraph_format.space_before = Pt(0)

    def write(paragraph, text, *, size=9, bold=False, italic=False, align=None, after=0, before=0):
        run = paragraph.add_run(text)
        run.font.size = Pt(size)
        run.bold = bold
        run.italic = italic
        if align is not None:
            paragraph.alignment = align
        paragraph.paragraph_format.space_after = Pt(after)
        paragraph.paragraph_format.space_before = Pt(before)
        return paragraph

    def line(text='', **options):
        return write(document.add_paragraph(), text, **options)

    def gap(points=1):
        """Тонкий абзац между таблицами: две таблицы подряд Word склеивает в одну, и ширины
        колонок у обеих съезжают."""
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.line_spacing = Pt(points)
        write(paragraph, '', size=1)
        return paragraph

    def edges(cell, **sides):
        """Рамки ячейки: top/bottom/left/right = True (линия) или False (нет)."""
        properties = cell._tc.get_or_add_tcPr()
        borders = properties.find(qn('w:tcBorders'))
        if borders is None:
            borders = OxmlElement('w:tcBorders')
            properties.append(borders)
        for side, on in sides.items():
            node = borders.find(qn('w:%s' % side))
            if node is None:
                node = OxmlElement('w:%s' % side)
                borders.append(node)
            node.set(qn('w:val'), 'single' if on else 'nil')
            node.set(qn('w:sz'), '4')
            node.set(qn('w:space'), '0')
            node.set(qn('w:color'), '000000')

    def vertical(cell):
        properties = cell._tc.get_or_add_tcPr()
        direction = OxmlElement('w:textDirection')
        direction.set(qn('w:val'), 'btLr')
        properties.append(direction)

    def layout(table, widths, *, grid=True, margin=0.08):
        """Жёсткие ширины колонок и узкие поля ячеек: в форме десять граф на полосу."""
        table.autofit = False
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        if grid:
            table.style = 'Table Grid'
        properties = table._tbl.tblPr
        fixed = OxmlElement('w:tblLayout')
        fixed.set(qn('w:type'), 'fixed')
        properties.append(fixed)
        margins = OxmlElement('w:tblCellMar')
        for side in ('left', 'right'):
            node = OxmlElement('w:%s' % side)
            node.set(qn('w:w'), str(int(margin * 567)))
            node.set(qn('w:type'), 'dxa')
            margins.append(node)
        properties.append(margins)
        for row in table.rows:
            for cell, width in zip(row.cells, widths):
                cell.width = Cm(width)

    def fill(cell, text, *, size=8, bold=False, italic=False, align=CENTER, valign='center'):
        cell.text = ''
        write(cell.paragraphs[0], text, size=size, bold=bold, italic=italic, align=align)
        cell.vertical_alignment = {'center': WD_CELL_VERTICAL_ALIGNMENT.CENTER, 'top': WD_CELL_VERTICAL_ALIGNMENT.TOP,
                                   'bottom': WD_CELL_VERTICAL_ALIGNMENT.BOTTOM}[valign]
        return cell

    def height(row, cm, *, exact=False):
        row.height = Cm(cm)
        row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY if exact else WD_ROW_HEIGHT_RULE.AT_LEAST

    # ── Шапка ────────────────────────────────────────────────────────────────
    for text in ('Приложение 18', 'к приказу Министра финансов', 'Республики Казахстан',
                 'от 20 декабря 2012 года № 562'):
        paragraph = line(text, size=8, italic=True, align=CENTER)
        paragraph.paragraph_format.left_indent = Cm(11.2)
    line('Форма № ДА-3', size=7.5, align=RIGHT, before=8, after=14)

    head = document.add_table(rows=2, cols=3)
    layout(head, (11.2, 1.9, PAGE - 13.1), grid=False)
    fill(head.cell(0, 0), requisites.get('org_name') or '', size=9, bold=True, valign='bottom')
    edges(head.cell(0, 0), bottom=True)
    fill(head.cell(0, 1), 'ИИН/БИН', size=9, align=RIGHT, valign='bottom')
    fill(head.cell(0, 2), requisites.get('org_bin') or '', size=9, bold=True, valign='bottom')
    edges(head.cell(0, 2), top=True, bottom=True, left=True, right=True)
    fill(head.cell(1, 0), 'Организация (индивидуальный предприниматель)', size=8, italic=True, valign='top')
    gap(8)

    approve = document.add_table(rows=5, cols=4)
    layout(approve, (8.0, 3.8, 0.4, PAGE - 12.2), grid=False)
    for index, text in ((0, 'УТВЕРЖДАЮ'), (1, 'Руководитель организации (индивидуальный предприниматель)')):
        merged = approve.cell(index, 1).merge(approve.cell(index, 3))
        fill(merged, text, size=9)
    edges(approve.cell(2, 1), bottom=True)
    fill(approve.cell(2, 2), '/', size=8, valign='bottom')
    fill(approve.cell(2, 3), short_name(requisites.get('head_name')), size=9, valign='bottom')
    edges(approve.cell(2, 3), bottom=True)
    fill(approve.cell(3, 1), 'подпись', size=8, italic=True, valign='top')
    fill(approve.cell(3, 3), 'расшифровка подписи', size=8, italic=True, valign='top')
    fill(approve.cell(4, 1).merge(approve.cell(4, 3)), '"____"_____________________20_____года', size=9)
    for index in (0, 2):
        height(approve.rows[index], 0.55)
    gap(16)

    title = document.add_table(rows=2, cols=3)
    layout(title, (PAGE - 6.4, 3.3, 3.1), grid=False)
    fill(title.cell(0, 0), 'АКТ', size=10.5, bold=True, valign='bottom')
    fill(title.cell(1, 0), 'НА ВЫБЫТИЕ (СПИСАНИЕ) ДОЛГОСРОЧНЫХ АКТИВОВ', size=10.5, bold=True, valign='top')
    fill(title.cell(0, 1), 'Номер документа', size=8.5)
    fill(title.cell(0, 2), 'Дата составления', size=8.5)
    fill(title.cell(1, 1), str(request.get('id') or ''), size=9.5)
    fill(title.cell(1, 2), date_short(request.get('act_date')), size=9.5)
    for row in title.rows:
        for cell in row.cells[1:]:
            edges(cell, top=True, bottom=True, left=True, right=True)
    gap(10)

    # ── Таблица ОС: десять граф ──────────────────────────────────────────────
    widths = (0.8, 3.1, 1.7, 1.8, 1.0, 2.2, 2.2, 2.0, 1.8, 1.8)
    rows = da3_rows(request, items, with_cost=with_cost)
    table = document.add_table(rows=3 + len(rows), cols=10)
    layout(table, widths)
    for column in range(8):
        merged = table.cell(0, column).merge(table.cell(1, column))
        fill(merged, DA3_HEADING_LINES[column], size=8)
        if column in (0, 4):
            vertical(merged)
    fill(table.cell(0, 8).merge(table.cell(0, 9)), 'Номер', size=8)
    for column in (8, 9):
        vertical(fill(table.cell(1, column), DA3_HEADING_LINES[column], size=8))
    height(table.rows[0], 0.7)
    height(table.rows[1], 2.2)
    for column in range(10):
        fill(table.cell(2, column), str(column + 1), size=6.5)
    for index, values in enumerate(rows, start=3):
        for column, value in enumerate(values):
            align = LEFT if column in (0, 1) else RIGHT if column in (5, 6, 7) else CENTER
            fill(table.cell(index, column), value, size=8, align=align)
        height(table.rows[index], 0.55)
    gap(14)

    # ── Приспособления и драгоценные материалы: в портале не ведутся, строки пустые ──
    extra_widths = (4.3, 2.6, 4.0, 2.9, 2.8, PAGE - 16.6)
    extra = document.add_table(rows=5, cols=6)
    layout(extra, extra_widths)
    fill(extra.cell(0, 0).merge(extra.cell(0, 1)), 'Приспособления и принадлежности', size=8.5)
    fill(extra.cell(0, 2).merge(extra.cell(0, 5)), 'Содержание драгоценных материалов (металлов, камней)', size=8.5)
    for column, text in enumerate(('наименование', 'количество', 'наименование драгоценных металлов',
                                   'номенклатурный номер', 'единица измерения', 'количество (масса)')):
        fill(extra.cell(1, column), text, size=8.5)
        fill(extra.cell(2, column), str(column + 1), size=6.5)
    for index in (3, 4):
        height(extra.rows[index], 0.42)

    # ── Решение комиссии ─────────────────────────────────────────────────────
    line(da3_order_line(requisites), size=9, before=4)
    names = [item.get('name') or '' for item in items or ()] or ['']
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    inspect = document.add_table(rows=len(seen) + 1, cols=2)
    layout(inspect, (7.9, PAGE - 7.9), grid=False)
    fill(inspect.cell(0, 0), 'на основании ________________ произвела осмотр', size=9, align=RIGHT, valign='bottom')
    for index, name in enumerate(seen):
        fill(inspect.cell(index, 1), name, size=9, align=LEFT, valign='bottom')
        edges(inspect.cell(index, 1), bottom=True)
    fill(inspect.cell(len(seen), 1), 'наименование', size=8, italic=True, valign='top')
    line('и установила его (их) надлежащим списанию.', size=9, before=2, after=4)

    def labelled(pairs, *, label_width=4.6):
        """Строки «подпись — значение» с линией под значением, как в бумажной форме."""
        block = document.add_table(rows=len(pairs), cols=2)
        layout(block, (label_width, PAGE - label_width), grid=False)
        for index, (label, value) in enumerate(pairs):
            fill(block.cell(index, 0), label, size=9, align=LEFT, valign='bottom')
            fill(block.cell(index, 1), value or '', size=9, align=LEFT, valign='bottom')
            edges(block.cell(index, 1), bottom=True)
            height(block.rows[index], 0.6)

    labelled([
        ('Количество ремонтов', ''),
        ('на сумму, в теңге', ''),
        ('Причина списания', request.get('comment') or ''),
        ('Состояние актива(ов)', request.get('check_comment') or ''),
        ('Заключение комиссии', ''),
    ])
    gap()
    labelled([('Приложение: Перечень прилагаемых документов',
               'фото ОС — запрос № %s, раздел «Основные средства»' % request.get('id'))],
             label_width=8.0)

    # ── Оборотная сторона ────────────────────────────────────────────────────
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    line('Оборотная сторона формы ДА-3', size=9, align=RIGHT, after=14)

    commission = da3_commission(request, requisites, people, heads)
    signer_rows = [('Председатель комиссии:', commission['chairman'])]
    members = commission['members'] or [('', '')]
    signer_rows += [('Члены комиссии:' if index == 0 else '', member) for index, member in enumerate(members)]
    signer_rows.append(('Материально-ответственное лицо:', commission['mol']))
    signatures = document.add_table(rows=len(signer_rows) * 2, cols=6)
    layout(signatures, (5.7, 4.2, 0.3, 4.1, 0.3, PAGE - 14.6), grid=False)
    for index, (label, (post, name)) in enumerate(signer_rows):
        top, bottom = signatures.rows[index * 2], signatures.rows[index * 2 + 1]
        fill(top.cells[0], label, size=9, align=LEFT, valign='bottom')
        fill(top.cells[1], post, size=9, valign='bottom')
        fill(top.cells[2], '/', size=9, valign='bottom')
        fill(top.cells[3], name, size=9, valign='bottom')
        fill(top.cells[4], '/', size=9, valign='bottom')
        for column in (1, 3, 5):
            edges(top.cells[column], bottom=True)
        for column, caption in ((1, 'должность'), (3, 'Ф.И.О.'), (5, 'подпись')):
            fill(bottom.cells[column], caption, size=8, italic=True, valign='top')
        height(top, 0.62)
        height(bottom, 0.5)

    line('Сведения', size=9, align=CENTER, before=6)
    line('о наличии затрат, связанных со списанием долгосрочных активов и о поступлении запасов от списания:',
         size=9, align=CENTER, after=2)
    cost_widths = (1.5, 1.7, 1.7, 1.9, 1.8, 3.1, 2.9, 1.9, PAGE - 16.5)
    costs = document.add_table(rows=6, cols=9)
    layout(costs, cost_widths)
    fill(costs.cell(0, 0).merge(costs.cell(0, 3)), 'Затраты на списание', size=8.5)
    fill(costs.cell(0, 4).merge(costs.cell(0, 8)), 'Поступило от списания', size=8.5)
    for column, text in ((0, 'Вид работ'), (1, 'Номер документа'), (2, 'Статья затрат'), (3, 'Сумма, в теңге'),
                         (4, 'Номер документа'), (7, 'Количество'), (8, 'Сумма, в теңге')):
        fill(costs.cell(1, column).merge(costs.cell(2, column)), text, size=8.5)
    fill(costs.cell(1, 5).merge(costs.cell(1, 6)), 'Запасы', size=8.5)
    fill(costs.cell(2, 5), 'наименование', size=8.5)
    fill(costs.cell(2, 6), 'номенклатурный номер', size=8.5)
    for column in range(9):
        fill(costs.cell(3, column), str(column + 1), size=6.5)
    height(costs.rows[4], 0.42)
    # «Итого» — подпись без рамок и рамка только у суммы, как в бумажной форме.
    for column in range(9):
        cell = costs.cell(5, column)
        boxed = column in (3, 8)
        edges(cell, top=True, bottom=boxed, left=boxed, right=boxed)
    for column in (2, 7):
        fill(costs.cell(5, column), 'Итого', size=9, align=RIGHT)
        edges(costs.cell(5, column), top=True, bottom=False, left=False, right=True)
    height(costs.rows[5], 0.45)

    gap(14)
    labelled([('Доход (убыток) от реализации', '')], label_width=5.0)
    line('сумма прописью, в теңге', size=8, italic=True, align=CENTER, after=4)
    line('В карточке (книге) учета выбытие отмечено', size=9, after=10)

    chief = document.add_table(rows=2, cols=5)
    layout(chief, (5.0, 4.3, 0.3, 2.9, PAGE - 12.5), grid=False)
    fill(chief.cell(0, 0), 'Главный бухгалтер (бухгалтер)', size=9, align=LEFT, valign='bottom')
    fill(chief.cell(0, 1), short_name(requisites.get('chief_accountant_name')), size=9, valign='bottom')
    fill(chief.cell(0, 2), '/', size=9, valign='bottom')
    fill(chief.cell(0, 4), '"____"________________20__года', size=9, valign='bottom')
    for column in (1, 3):
        edges(chief.cell(0, column), bottom=True)
    fill(chief.cell(1, 1), 'Ф.И.О.', size=8, italic=True, valign='top')
    fill(chief.cell(1, 3), 'подпись', size=8, italic=True, valign='top')
    height(chief.rows[0], 0.62)

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
