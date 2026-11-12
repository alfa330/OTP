# -*- coding: utf-8 -*-
"""«Отчёт по входу» — входящая линия отдела продаж по дням и очередям, xlsx.

Форма — образец владельца от 11.11.2026 («Отчет по Основной группе ноябрь 2026»):
лист на день, строка на очередь, у очереди столько строк, сколько у неё номеров,
внизу «Итого». До раздела этот файл заполняли руками по статистике очередей станции
(Asternic).

ЧТО СЧИТАЕТСЯ ЗВОНКОМ (решение владельца 11.11.2026)
---------------------------------------------------
Только тот, кто позвонил сам и дошёл до очереди, — те же входящие, что в «Касаниях»
и на «Табло ОП». Asternic считает всё, что записано в журнале очередей: заявки
автообзвона идут через те же очереди (3016 — целиком они), туда же попадают
тестовые звонки. Поэтому цифры здесь ниже, чем в ручном файле (02.11.2026 — 224
звонка против 541), а доля потерянных выше: это настоящие потери, не разбавленные
автообзвоном. Сводить отчёт с Asternic бессмысленно — сводить надо с «Табло ОП».

Считает звонки не этот модуль, а `op_wallboard.snapshot.aggregate` — то же правило,
что у стены: AR = потеряно ÷ дошедшие до очереди, SL = принятые не позже порога ÷
дошедшие до очереди, разговор — без ожидания и усечённый до секунды. Своя копия
правил разошлась бы со стеной при первой же правке порога.

ПЛАН ЗВОНКОВ — факт той же очереди в тот же день недели неделей раньше. Так его
ставили и руками: в образце план 09.11 и 10.11 равен факту 02.11 и 03.11 во всех
строках. Поэтому данные нужны на неделю раньше периода (`PLAN_SHIFT_DAYS`).

ЧТО ОСТАЁТСЯ ЧЕЛОВЕКУ (решение владельца 11.11.2026)
---------------------------------------------------
«ОСС», «План часов», «Факт часов» и «CR%» портал не заполняет — готового источника
для них нет. Клетки пустые; «% выработки часов» и «% невыходов» стоят формулами и
считаются, когда часы вписаны. Всё, что портал знает сам, лежит ЧИСЛАМИ, а не
формулами: файл открывают и в просмотрщиках, которые формул не считают, и клетка
без посчитанного значения там пустая.

Очереди и названия парков — из образца (`QUEUES`); номера линий приходят снаружи
(`line_numbers`): в этом модуле их нет, таблица номеров компании лежит в конфиге
линий в bot_schedule2.py. Очередь, которой в образце нет, но звонки в неё за период
были, дописывается строкой в конец с подписью «Касаний» — иначе её звонки пропали
бы из «Итого» и отчёт разошёлся бы с табло.

Модуль чистый: ни сети, ни базы. На вход — касания в виде
`cdr.queries.incoming_touches_compact`.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from op_wallboard import snapshot as snapshot_mod

from . import lines as lines_mod, sync

ALMATY = ZoneInfo('Asia/Almaty')

SL_SECONDS = snapshot_mod.DEFAULT_SL_SECONDS
# План суток — факт недельной давности: тот же день недели.
PLAN_SHIFT_DAYS = 7

# Очередь и парк — как в образце, в его порядке. Номеров линий здесь нет намеренно:
# репозиторий публичный, и таблица номеров компании живёт в одном месте — в рабочем
# конфиге линий в bot_schedule2.py (`_CDR_INBOUND_REPORT_LINES`), откуда приходит в
# `build_workbook` аргументом `line_numbers`. Номера справочные: звонки отчёт собирает
# по очереди, а не по номеру (у «Центра регистрации» номеров десятки, в образце шесть).
QUEUES = (
    ('3000', 'Честный'),
    ('3001', 'iTaxi вход'),
    ('3003', 'Бизнес Партнер'),
    ('3004', 'Маркетинг iTaxi лиды'),
    ('3005', 'iTaxi вход'),
    ('3006', 'Департамент'),
    ('3007', 'Аманат'),
    ('3008', 'QAZAQ'),
    ('3010', 'YandexRek'),
    ('3015', 'Global'),
    ('3016', 'YandexRek'),
    ('3024', 'ChestnyiRek'),
    ('3026', 'YandexRek'),
    ('3030', 'Eki Dongelek'),
    ('3034', 'Jana'),
    ('3035', 'Tenge Taxi'),
    ('3036', 'Аренда авто'),
    ('3037', 'Центррег6624'),
    ('3038', 'Salam Taxi'),
    ('3040', 'Такси 24'),
    ('3041', 'Ноль такси'),
    ('3077', 'Hokage'),
)

# Колонки листа: (ключ, заголовок, подпись нормы во второй строке шапки, ширина, формат).
# Заголовки, нормы и ширины — из образца. Ключи с числами портала совпадают с ключами
# строки `day_tables`; у ручных колонок ключа в строке нет.
COLUMNS = (
    ('date', 'Дата', '', 13.0, 'DD.MM.YYYY'),
    ('numbers', 'Номера', '', 13.9, '0'),
    ('queue', 'Очередь', '', 13.0, 'General'),
    ('park', 'Парк', '', 22.4, 'General'),
    ('plan', 'План звонки', '', 13.4, '0'),
    ('fact', 'Факт звонки', '', 13.3, '0'),
    ('match', '% совпадения', '', 16.3, '0%'),
    ('answered', 'Принято', '', 10.9, '0'),
    ('ar', 'AR%', 'норма >5%', 12.4, '0.0%'),
    ('occupancy', 'ОСС', 'норма >67%', 13.4, '0.0%'),
    ('hours_plan', 'План часов', '', 12.1, 'General'),
    ('hours_fact', 'Факт часов', '', 12.7, 'General'),
    ('hours_done', '% выработки часов', '', 19.7, '0%'),
    ('hours_missed', '% невыходов', '', 16.0, '0%'),
    ('aht', 'AHT', '', 9.1, '0'),
    ('sl', 'SL/20сек', 'норма 80%', 11.0, '0.0%'),
    ('cr', 'CR%', '', 9.1, '0%'),
)
_COL = {key: index for index, (key, _t, _n, _w, _f) in enumerate(COLUMNS, start=1)}
_LETTER = {key: get_column_letter(index) for key, index in _COL.items()}
_LAST_COLUMN = len(COLUMNS)

# Одно значение на весь день — клетка объединена по всем строкам очередей.
DAY_WIDE = ('occupancy', 'hours_plan', 'hours_fact', 'hours_done', 'hours_missed')
# Числа портала по очереди: объединяются по строкам номеров этой очереди.
QUEUE_VALUES = ('plan', 'fact', 'match', 'answered', 'ar', 'aht', 'sl')
# Объединяется по строкам очереди всё, кроме самих номеров, даты и дневных колонок.
_QUEUE_MERGED = ('queue', 'park') + QUEUE_VALUES + ('cr',)

HEADER_ROWS = 2
FIRST_DATA_ROW = HEADER_ROWS + 1

# Стили — как в образце: Calibri 11, всё по центру, тонкая сетка, жёлтая шапка,
# голубая строка итогов. Объекты одни на модуль (см. cdr/report.py: стиль в цикле
# замедляет openpyxl в разы).
FONT = Font(name='Calibri', size=11)
BOLD = Font(name='Calibri', size=11, bold=True)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True)
_THIN = Side(style='thin')
GRID = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
HEADER_FILL = PatternFill('solid', fgColor='FFFF00')
TOTAL_FILL = PatternFill('solid', fgColor='BDD7EE')
# Красная полоска в клетке AR — чем длиннее, тем больше потеряно. Из образца.
AR_BAR_COLOR = 'FFFF555A'
ZOOM = 90

NOTES_TITLE_FONT = Font(name='Calibri', size=13, bold=True)
NOTES_WRAP = Alignment(wrap_text=True, vertical='top')

NOTES_SHEET = 'Пояснения'


# ── расчёт ───────────────────────────────────────────────────────────────────

def _stats(touches, sl_seconds):
    """Показатели одной клетки (очередь за сутки или сутки целиком) правилом табло."""
    totals = snapshot_mod.aggregate(touches or [], sl_seconds)['totals']
    arrived = totals['arrived']
    if not arrived:
        # Звонков не было — клетки пустые, как в образце: ноль в «Принято» и «AR%»
        # читался бы как «всех потеряли».
        return {'fact': None, 'answered': None, 'ar': None, 'aht': None, 'sl': None}
    return {
        'fact': arrived,
        'answered': totals['answered'],
        'ar': totals['ar'],
        'aht': totals['avg_talk_seconds'],
        'sl': totals['sl'],
    }


def _match(fact, plan):
    """«% совпадения» — факт ÷ план. Плана нет — пусто; звонков не было — ноль, как
    считала формула образца."""
    return ((fact or 0) / plan) if plan else None


def layout(touches, day_from, day_to, line_numbers=None):
    """Строки листа: [(очередь, парк, номера)] — очереди образца и те, которых в нём
    нет, но звонки за период были.

    line_numbers — {очередь: номера линий} из конфига линий компании; очередь без
    номеров занимает одну строку с пустой клеткой «Номера».
    Состав один на всю книгу — листы разных дней ложатся строка в строку."""
    days = {day.isoformat() for day in sync.days_in_period(day_from, day_to)}
    numbers = line_numbers or {}
    known = {queue for queue, _park in QUEUES}
    extra = sorted({lines_mod.first_queue(touch.get('queue')) for touch in touches or []
                    if touch.get('call_day') in days} - known - {''})
    blocks = list(QUEUES) + [(queue, lines_mod.park_of_queue(queue)) for queue in extra]
    return [(queue, park, tuple(numbers.get(queue) or ())) for queue, park in blocks]


def day_tables(touches, day_from, day_to, sl_seconds=SL_SECONDS, line_numbers=None):
    """[{day, rows, total}] по дням периода.

    touches — входящие с очередью за период И за неделю до него (план). Звонок с
    двумя очередями (перевод) считается за первой — тем же правилом, каким «Касания»
    называют его парк, — поэтому сумма строк равна итогу дня."""
    by_cell = defaultdict(list)
    by_day = defaultdict(list)
    for touch in touches or []:
        queue = lines_mod.first_queue(touch.get('queue'))
        day = touch.get('call_day')
        if not queue or not day:
            continue
        by_cell[(day, queue)].append(touch)
        by_day[day].append(touch)

    blocks = layout(touches, day_from, day_to, line_numbers)
    tables = []
    for day in sync.days_in_period(day_from, day_to):
        key = day.isoformat()
        week_ago = (day - timedelta(days=PLAN_SHIFT_DAYS)).isoformat()
        rows = []
        plan_total = 0
        for queue, park, numbers in blocks:
            row = _stats(by_cell.get((key, queue)), sl_seconds)
            plan = _stats(by_cell.get((week_ago, queue)), sl_seconds)['fact']
            plan_total += plan or 0
            row.update(queue=queue, park=park, numbers=tuple(numbers), plan=plan,
                       match=_match(row['fact'], plan))
            rows.append(row)
        # Итог дня — по всем его звонкам разом, а не среднее по строкам: очередь с одним
        # звонком весила бы в среднем столько же, сколько очередь с сотней. Так же считает
        # «Табло ОП», и «Итого» листа сходится с его итогами дня.
        total = _stats(by_day.get(key), sl_seconds)
        total.update(plan=plan_total or None, match=_match(total['fact'], plan_total))
        tables.append({'day': day, 'rows': rows, 'total': total})
    return tables


# ── книга ────────────────────────────────────────────────────────────────────

def build_workbook(touches, *, day_from, day_to, generated_at=None, generated_by='',
                   missing_days=(), sl_seconds=SL_SECONDS, line_numbers=None):
    """Книга отчёта: лист на день и «Пояснения» в конце. Возвращает (BytesIO, дней).

    missing_days — сутки (из периода или недели до него), которых на портале ещё нет:
    о них сказано в «Пояснениях», иначе пустой план читался бы как «звонков не было».
    line_numbers — {очередь: номера линий} для колонки «Номера» (см. `QUEUES`)."""
    generated_at = generated_at or datetime.now(ALMATY)
    tables = day_tables(touches, day_from, day_to, sl_seconds, line_numbers)

    workbook = Workbook()
    workbook.remove(workbook.active)
    for table in tables:
        _fill_day(workbook.create_sheet(table['day'].strftime('%d.%m')), table)
    _fill_notes(workbook.create_sheet(NOTES_SHEET), day_from, day_to, generated_at,
                generated_by, missing_days, sl_seconds)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream, len(tables)


def report_filename(day_from, day_to):
    if day_from == day_to:
        return 'Отчёт по входу %s.xlsx' % _ru_date(day_from)
    return 'Отчёт по входу %s — %s.xlsx' % (_ru_date(day_from), _ru_date(day_to))


def _ru_date(day):
    return day.strftime('%d.%m.%Y')


def _put(sheet, row, key, value):
    return sheet.cell(row=row, column=_COL[key], value=value)


def _merge(sheet, key, row_from, row_to):
    if row_to > row_from:
        sheet.merge_cells(start_row=row_from, end_row=row_to,
                          start_column=_COL[key], end_column=_COL[key])


def _fill_day(sheet, table):
    sheet.sheet_view.zoomScale = ZOOM
    for key, title, norm, width, _fmt in COLUMNS:
        sheet.column_dimensions[_LETTER[key]].width = width
        _put(sheet, 1, key, title)
        if norm:
            _put(sheet, 2, key, norm)
        else:
            _merge(sheet, key, 1, HEADER_ROWS)

    row = FIRST_DATA_ROW
    for block in table['rows']:
        numbers = block['numbers']
        last = row + max(1, len(numbers)) - 1
        for offset, number in enumerate(numbers):
            # Числом, как в образце: одиннадцать цифр Excel показывает целиком, а
            # зелёного уголка «число сохранено как текст» при этом нет.
            _put(sheet, row + offset, 'numbers', int(number))
        _put(sheet, row, 'queue', int(block['queue']) if block['queue'].isdigit()
             else block['queue'])
        _put(sheet, row, 'park', block['park'])
        for key in QUEUE_VALUES:
            _put(sheet, row, key, block[key])
        for key in _QUEUE_MERGED:
            _merge(sheet, key, row, last)
        row = last + 1
    last_data = row - 1
    total_row = row

    _put(sheet, FIRST_DATA_ROW, 'date', table['day'])
    _merge(sheet, 'date', FIRST_DATA_ROW, last_data)
    # Часы и ОСС вписывает человек; доли считаются, когда вписаны ОБА числа часов —
    # иначе пустой «Факт часов» показывал бы «невыходов 100 %».
    top = FIRST_DATA_ROW
    plan_ref, fact_ref = '%s%d' % (_LETTER['hours_plan'], top), '%s%d' % (_LETTER['hours_fact'], top)
    blank = 'OR(%s="",%s="")' % (plan_ref, fact_ref)
    _put(sheet, top, 'hours_done', '=IF(%s,"",%s/%s)' % (blank, fact_ref, plan_ref))
    _put(sheet, top, 'hours_missed', '=IF(%s,"",(%s-%s)/%s)' % (blank, plan_ref, fact_ref, plan_ref))
    for key in DAY_WIDE:
        _merge(sheet, key, top, last_data)

    _put(sheet, total_row, 'date', 'Итого')
    sheet.merge_cells(start_row=total_row, end_row=total_row,
                      start_column=_COL['date'], end_column=_COL['park'])
    for key in QUEUE_VALUES:
        _put(sheet, total_row, key, table['total'][key])
    for key in DAY_WIDE:
        # Итог повторяет дневное значение. Пустую клетку `=J3` показал бы нулём.
        ref = '%s%d' % (_LETTER[key], top)
        _put(sheet, total_row, key, '=IF(%s="","",%s)' % (ref, ref))

    for line in range(1, total_row + 1):
        for key, _title, _norm, _width, fmt in COLUMNS:
            cell = sheet.cell(row=line, column=_COL[key])
            cell.font = FONT
            cell.alignment = CENTER
            cell.border = GRID
            if line <= HEADER_ROWS:
                cell.fill = HEADER_FILL
            else:
                cell.number_format = fmt
    sheet.cell(row=total_row, column=_COL['date']).font = BOLD
    for index in range(_COL['plan'], _LAST_COLUMN + 1):
        sheet.cell(row=total_row, column=index).fill = TOTAL_FILL

    sheet.conditional_formatting.add(
        '%s%d:%s%d' % (_LETTER['ar'], FIRST_DATA_ROW, _LETTER['ar'], last_data),
        DataBarRule(start_type='min', end_type='max', color=AR_BAR_COLOR))


def _fill_notes(sheet, day_from, day_to, generated_at, generated_by, missing_days,
                sl_seconds):
    sheet.column_dimensions['A'].width = 24
    sheet.column_dimensions['B'].width = 110
    period = (_ru_date(day_from) if day_from == day_to
              else '%s — %s' % (_ru_date(day_from), _ru_date(day_to)))
    lines = [
        ('Отчёт по входу — входящая линия отдела продаж', None),
        ('Период', period),
        ('Собрано', generated_at.strftime('%d.%m.%Y %H:%M') + ' (Алматы)'),
        ('Собрал', generated_by or '—'),
        ('Источник', 'Раздел «Касания»: звонки и журнал очередей АТС отдела продаж'),
        ('', None),
        ('Как считается', None),
        ('Факт звонки',
         'Клиент позвонил сам и дошёл до очереди. Не считаются: положившие трубку на '
         'приветствии, заявки автообзвона и номера из «Реестра тестовых номеров». Поэтому '
         'цифры ниже, чем в статистике станции (Asternic): она считает звонком и автообзвон. '
         'Сходится с итогами дня на «Табло ОП».'),
        ('План звонки', 'Факт звонки этой же очереди в тот же день недели неделей раньше.'),
        ('% совпадения', 'Факт звонки ÷ План звонки.'),
        ('Принято', 'Звонки из «Факт звонки», которые закончились разговором с оператором.'),
        ('AR%', '(Факт звонки − Принято) ÷ Факт звонки.'),
        ('AHT', 'Средняя длительность разговора принятых звонков, секунды. Ожидание в '
                'очереди в неё не входит.'),
        ('SL/20сек',
         'Принятые не позже %d секунд ожидания в очереди ÷ Факт звонки (все дошедшие до '
         'очереди, включая непринятые). Приветствие в ожидание не входит.' % sl_seconds),
        ('Итого', 'AR%, AHT и SL/20сек — по всем звонкам дня разом, а не среднее по строкам.'),
        ('Вписать вручную',
         '«ОСС», «План часов», «Факт часов» и «CR%» — портал их не заполняет. '
         '«% выработки часов» и «% невыходов» посчитаются сами, когда вписаны часы.'),
    ]
    if missing_days:
        lines += [
            ('', None),
            ('Нет данных станции',
             'За %s. В эти дни «Факт звонки» пустой, а неделей позже пустой «План звонки». '
             'Портал уже запросил эти сутки — соберите отчёт ещё раз через несколько минут.'
             % ', '.join(_ru_date(day) for day in sorted(missing_days))),
        ]
    for index, (label, value) in enumerate(lines, start=1):
        left = sheet.cell(row=index, column=1, value=label or None)
        if value is None:
            # Заголовок блока или пустая строка между блоками.
            left.font = NOTES_TITLE_FONT
            continue
        left.font = BOLD
        left.alignment = NOTES_WRAP
        right = sheet.cell(row=index, column=2, value=value)
        right.font = FONT
        right.alignment = NOTES_WRAP
