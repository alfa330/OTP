# -*- coding: utf-8 -*-
"""Выгрузка «Дресс-кода» в Excel (ТЗ п. 7): фото периода, кто не прислал, сводка.

Предварительный (ИИ) и итоговый (руководитель) статусы — разными колонками и разными
строками сводки: смешивать их ТЗ запрещает прямо. У каждой доли в сводке назван
знаменатель.
"""

from datetime import datetime
from io import BytesIO

from . import AI_LABELS, DECISION_LABELS, QUALITY_LABELS

COLUMNS = (
    ('Дата', 11), ('Время фото', 10), ('Сотрудник', 30), ('Отдел', 20), ('Город', 16),
    ('Качество фото', 16), ('Вывод ИИ', 30), ('Видно на фото', 36), ('Причина', 40), ('Замечания', 30),
    ('Решение руководителя', 26), ('Комментарий', 36), ('Кто решил', 24), ('Время решения', 17),
    ('Повторное фото', 15), ('Повторное нарушение', 14), ('Ознакомлен', 17), ('Версия правил', 9),
    ('Ссылка', 40),
)


def _dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _join(values):
    return '; '.join(str(v) for v in values or [] if v)


def build(rows, missing, summary, *, date_from, date_to, base_url=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = 'Фото'
    ws.append([title for title, _ in COLUMNS])
    for index, (_, width) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[ws.cell(row=1, column=index).column_letter].width = width
        ws.cell(row=1, column=index).font = Font(bold=True)
    ws.freeze_panes = 'A2'
    base = str(base_url or '').rstrip('/')
    for row in rows:
        taken = _dt(row.get('taken_at'))
        decided = _dt(row.get('decided_at'))
        acknowledged = _dt(row.get('acknowledged_at'))
        reasons = _join(item.get('sign') for item in row.get('ai_reasons') or [])
        ws.append([
            _dt(row.get('day')).date() if _dt(row.get('day')) else row.get('day'),
            taken.strftime('%H:%M') if taken else '',
            row.get('employee') or '',
            row.get('department') or '',
            row.get('city') or '',
            QUALITY_LABELS.get(row.get('ai_quality'), '') if row.get('ai_quality') else '',
            AI_LABELS.get(row.get('ai_status'), row.get('ai_status') or ''),
            _join(row.get('ai_visible')),
            reasons or (row.get('ai_note') or ''),
            _join(row.get('ai_remarks')),
            DECISION_LABELS.get(row.get('decision'), '') if row.get('decision') else 'Нет решения',
            row.get('decision_comment') or '',
            row.get('decided_by') or '',
            decided,
            row.get('retake_id') or (('к фото %s' % row['retake_of']) if row.get('retake_of') else ''),
            'Да' if row.get('is_repeat') else '',
            acknowledged,
            row.get('rules_version'),
            ('%s/?view=group_late_bot&dc_check=%s' % (base, row['id'])) if base else '',
        ])
        current = ws.max_row
        ws.cell(row=current, column=1).number_format = 'DD.MM.YYYY'
        ws.cell(row=current, column=14).number_format = 'DD.MM.YYYY HH:MM'
        ws.cell(row=current, column=17).number_format = 'DD.MM.YYYY HH:MM'
        for column in (8, 9, 10, 12):
            ws.cell(row=current, column=column).alignment = Alignment(wrap_text=True, vertical='top')

    miss = wb.create_sheet('Не прислали фото')
    miss.append(['Дата', 'Сотрудник', 'Отдел', 'Город', 'Начало смены'])
    for index, width in enumerate((11, 30, 20, 16, 13), start=1):
        miss.column_dimensions[miss.cell(row=1, column=index).column_letter].width = width
        miss.cell(row=1, column=index).font = Font(bold=True)
    for row in missing.get('rows') or []:
        day = _dt(row.get('day'))
        miss.append([day.date() if day else row.get('day'), row.get('employee') or '',
                     row.get('department') or '', row.get('city') or '', row.get('shift_start') or ''])
        miss.cell(row=miss.max_row, column=1).number_format = 'DD.MM.YYYY'

    total = wb.create_sheet('Сводка')
    total.column_dimensions['A'].width = 52
    total.column_dimensions['B'].width = 12
    total.column_dimensions['C'].width = 40
    total.append(['Период', '%s — %s' % (date_from.strftime('%d.%m.%Y'), date_to.strftime('%d.%m.%Y'))])
    total.append([])
    total.append(['Показатель', 'Число', 'Из чего'])
    for cell in total[3]:
        cell.font = Font(bold=True)
    shifts = int(missing.get('shifts') or 0)
    for label, value, base_label in (
        ('Смен по графику (с начала прошло 30 мин)', shifts, ''),
        ('Не прислали фото', len(missing.get('rows') or []), 'из смен по графику'),
        ('Всего фото', summary.get('photos', 0), 'включая повторные'),
        ('Проанализировано ИИ', summary.get('analyzed', 0), 'из всех фото'),
        ('ИИ: соответствует', summary.get('ai_ok', 0), 'из проанализированных'),
        ('ИИ: предположительно не соответствует', summary.get('ai_violation', 0), 'из проанализированных'),
        ('ИИ: не удалось определить', summary.get('ai_unclear', 0), 'из проанализированных'),
        ('Ожидают анализа ИИ', summary.get('ai_pending', 0), 'из всех фото'),
        ('Ждут решения руководителя', summary.get('waiting', 0), 'спорные без решения'),
        ('Подтверждено нарушений', summary.get('confirmed', 0), 'решения руководителя'),
        ('Нарушения нет (отменён вывод ИИ)', summary.get('no_violation', 0), 'решения руководителя'),
        ('Запрошено повторное фото', summary.get('retake', 0), 'решения руководителя'),
        ('Повторных нарушений за 10 дней', summary.get('repeat', 0), 'из подтверждённых'),
    ):
        total.append([label, int(value or 0), base_label])

    wb.move_sheet('Сводка', offset=-2)
    wb.active = 0
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
