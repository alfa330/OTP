# -*- coding: utf-8 -*-
"""Черновик акта в DOCX (ТЗ п. 9): «чтобы супервайзер не набирал его вручную».

Шаблон отдела загружает руководитель (DOCX с метками вида {{ФИО}}). Нет
шаблона — собирается стандартный акт: шапка, сотрудник, таблица нарушений
периода с номерами записей и объяснительными, решение и строки подписей.

Метки шаблона:
    {{НОМЕР_АКТА}} {{ДАТА}} {{ФИО}} {{ДОЛЖНОСТЬ}} {{ОТДЕЛ}} {{ГРУППА}}
    {{ДАТА_ПРИЁМА}} {{НАРУШЕНИЯ}} {{ОБЪЯСНИТЕЛЬНЫЕ}} {{РЕШЕНИЕ}} {{СОСТАВИЛ}}

Табельного номера в портале нет (ни в одной карточке сотрудника его не
заводили), поэтому и метки под него нет — её отсутствие честнее пустой строки.

Метка в Word нередко разрезана на несколько «прогонов» с разным оформлением
(Word делит текст при проверке орфографии), поэтому замена идёт по тексту
абзаца целиком: найденный абзац переписывается в первый прогон. Оформление
внутри такого абзаца выравнивается по первому прогону — для бланка акта это
приемлемо, а не найти метку было бы хуже.
"""

from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def _fmt_date(value):
    return value.strftime('%d.%m.%Y') if hasattr(value, 'strftime') else str(value or '')


def _fmt_moment(value):
    return value.strftime('%d.%m.%Y %H:%M') if hasattr(value, 'strftime') else str(value or '')


def _explanation(record):
    if record.get('refused_at'):
        return 'отказ, акт об отказе от %s' % _fmt_date(record['refused_at'])
    if record.get('explanation_received_at'):
        return 'получена %s' % _fmt_date(record['explanation_received_at'])
    return 'не предоставлена'


def placeholders(act, operator, records, *, position, today, composed_by):
    lines = ['%d. %s — %s (запись %s)' % (index, _fmt_moment(r.get('occurred_at')), r.get('type_title') or '',
                                         r.get('number') or '')
             for index, r in enumerate(records, start=1)]
    explanations = ['%s — %s' % (r.get('number') or '', _explanation(r)) for r in records]
    return {
        '{{НОМЕР_АКТА}}': act.get('number') or '',
        '{{ДАТА}}': _fmt_date(act.get('composed_on') or today),
        '{{ФИО}}': operator.get('name') or '',
        '{{ДОЛЖНОСТЬ}}': position or '',
        '{{ОТДЕЛ}}': operator.get('department_name') or '',
        '{{ГРУППА}}': operator.get('group_name') or '',
        '{{ДАТА_ПРИЁМА}}': _fmt_date(operator.get('hire_date')) if operator.get('hire_date') else '',
        '{{НАРУШЕНИЯ}}': '\n'.join(lines),
        '{{ОБЪЯСНИТЕЛЬНЫЕ}}': '\n'.join(explanations),
        '{{РЕШЕНИЕ}}': act.get('decision_title') or '',
        '{{СОСТАВИЛ}}': composed_by or '',
    }


def _replace_in_paragraph(paragraph, values):
    text = paragraph.text
    if '{{' not in text:
        return
    replaced = text
    for key, value in values.items():
        replaced = replaced.replace(key, value)
    if replaced == text or not paragraph.runs:
        return
    paragraph.runs[0].text = replaced
    for run in paragraph.runs[1:]:
        run.text = ''


def _walk_paragraphs(container):
    for paragraph in container.paragraphs:
        yield paragraph
    for table in getattr(container, 'tables', ()):
        for row in table.rows:
            for cell in row.cells:
                yield from _walk_paragraphs(cell)


def fill_template(template_bytes, values):
    document = Document(BytesIO(template_bytes))
    for paragraph in _walk_paragraphs(document):
        _replace_in_paragraph(paragraph, values)
    for section in document.sections:
        for part in (section.header, section.footer):
            for paragraph in _walk_paragraphs(part):
                _replace_in_paragraph(paragraph, values)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def default_act(act, operator, records, *, position, today, composed_by):
    document = Document()
    style = document.styles['Normal']
    style.font.name = 'Times New Roman'
    style.font.size = Pt(12)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run('АКТ № %s' % (act.get('number') or ''))
    run.bold = True
    run.font.size = Pt(14)
    subtitle = document.add_paragraph('о нарушении трудовой дисциплины')
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER

    document.add_paragraph('Дата составления: %s' % _fmt_date(act.get('composed_on') or today))
    document.add_paragraph('Сотрудник: %s' % (operator.get('name') or ''))
    if position:
        document.add_paragraph('Должность: %s' % position)
    document.add_paragraph('Отдел: %s' % (operator.get('department_name') or ''))
    if operator.get('group_name'):
        document.add_paragraph('Группа: %s' % operator['group_name'])
    if operator.get('hire_date'):
        document.add_paragraph('Дата приёма: %s' % _fmt_date(operator['hire_date']))

    document.add_paragraph()
    document.add_paragraph('Нарушения за период подсчёта:')
    table = document.add_table(rows=1, cols=5)
    table.style = 'Table Grid'
    for cell, text in zip(table.rows[0].cells, ('№', 'Дата и время', 'Нарушение', 'Запись', 'Объяснительная')):
        cell.text = text
        for paragraph in cell.paragraphs:
            for item in paragraph.runs:
                item.bold = True
    for index, record in enumerate(records, start=1):
        cells = table.add_row().cells
        cells[0].text = str(index)
        cells[1].text = _fmt_moment(record.get('occurred_at'))
        cells[2].text = record.get('type_title') or ''
        cells[3].text = record.get('number') or ''
        cells[4].text = _explanation(record)

    document.add_paragraph()
    document.add_paragraph('Решение (мера): %s' % (act.get('decision_title') or '_' * 40))
    document.add_paragraph()
    document.add_paragraph('Составил: %s ____________________' % (composed_by or '____________________'))
    document.add_paragraph('Утвердил: ____________________ ____________________')
    document.add_paragraph('С актом ознакомлен(а): ____________________ «___» __________ 20___ г.')

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build(act, operator, records, *, template_bytes=None, position=None, today=None, composed_by=None):
    """Байты DOCX: по шаблону отдела, если он есть, иначе стандартный акт."""
    if template_bytes:
        values = placeholders(act, operator, records, position=position, today=today, composed_by=composed_by)
        return fill_template(template_bytes, values)
    return default_act(act, operator, records, position=position, today=today, composed_by=composed_by)
