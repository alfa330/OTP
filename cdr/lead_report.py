# -*- coding: utf-8 -*-
"""Выгрузка «сделки + касания»: книга xlsx и её JSON-двойник для ИИ.

Форма книги повторяет файлы, которые с июня 2026 собирались вручную и ушли
заказчику десятками («Лиды 24.06-24.07 + касания ОП.xlsx» и далее): первым
листом «Контекст», потом «Лиды» — одна сделка в строке, справа сводка по её
звонкам и блоки «Касание 1..N», потом «Касания» по одному звонку в строке,
«Операторы» и «Сводка». Люди к этой форме привыкли, а ИИ, которому такие файлы
отдают, читает «Контекст» первым — там сказано, что означает каждая колонка и
чему в цифрах верить нельзя.

Книга пишется в потоковом режиме openpyxl, как и `report.py`: месяц «Основы» —
это 27 тысяч сделок на полторы сотни колонок. Ловушки режима те же — ширины и
закрепление до первой строки, стили константами. Объединённых ячеек в потоковом
режиме нет, поэтому двухэтажная шапка сделана полосой: название группы стоит в
первой ячейке группы, остальные ячейки группы залиты тем же цветом.

JSON-двойник: первый ключ — `КОНТЕКСТ_ДЛЯ_ИИ` (модель читает файл сверху), потом
метаданные, сводка и сделки с касаниями внутри.
"""

import math
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import leads as leads_mod
from .report import (ALMATY, DATE_FORMAT, HEADER_ALIGN, HEADER_FONT, LINK_FONT, SECTION_FONT,
                     TITLE_FONT, WRAP_TOP, _hours, _percent, _ru_date, hms)

# Цвета шапки. Тёмно-синий — исходные колонки, синий — сводка, зелёный и
# светло-зелёный — блоки касаний через один, чтобы глаз не терял границу блока.
FILL_BASE = PatternFill('solid', fgColor='1F3864')
FILL_SUMMARY = PatternFill('solid', fgColor='2E5496')
FILL_BLOCK_A = PatternFill('solid', fgColor='375623')
FILL_BLOCK_B = PatternFill('solid', fgColor='4C7A2E')
FILL_SUB_A = PatternFill('solid', fgColor='6E9F4A')
FILL_SUB_B = PatternFill('solid', fgColor='84B45C')
FILL_HEADER = PatternFill('solid', fgColor='1F2937')

# key, заголовок, ширина, вид (text | dt | day | int | bool | list)
_BASE_COLUMNS = {
    'amo': (
        ('key', 'ID сделки', 12, 'text'), ('moment', 'Дата создания', 18, 'dt'),
        ('full_name', 'Название сделки', 24, 'text'), ('phone', 'Телефон', 14, 'text'),
        ('owner', 'Ответственный', 24, 'text'), ('stage', 'Этап сделки', 30, 'text'),
        ('reason', 'Причина закрытия', 26, 'text'), ('park', 'Таксопарк привлечения', 20, 'text'),
        ('city', 'Город привлечения', 16, 'text'), ('lead_type', 'Тип лида', 10, 'text'),
        ('utm_source', 'Источник (норм.)', 14, 'text'), ('tags', 'Теги сделки', 26, 'text'),
        ('registered', 'Зарегистрирован', 12, 'bool'), ('updated_at', 'Дата изменения', 18, 'dt'),
    ),
    'crm_paid_hire': (
        ('key', 'ID лида', 10, 'text'), ('moment', 'Дата регистрации водителя', 18, 'dt'),
        ('full_name', 'ФИО водителя', 26, 'text'), ('phone', 'Телефон', 14, 'text'),
        ('owner', 'Ответственный', 24, 'text'), ('stage', 'Статус', 20, 'text'),
        ('reason', 'Причина закрытия', 26, 'text'), ('park', 'Парк', 20, 'text'),
        ('city', 'Город', 14, 'text'), ('comment', 'Комментарий', 30, 'text'),
        ('updated_at', 'Дата изменения', 18, 'dt'),
    ),
    'crm_stream': (
        ('key', 'ID лида', 10, 'text'), ('moment', 'Взят в работу / загружен', 18, 'dt'),
        ('base_title', 'База', 10, 'text'), ('full_name', 'ФИО водителя', 26, 'text'),
        ('phone', 'Телефон', 14, 'text'), ('owner', 'Оператор', 24, 'text'),
        ('stage', 'Статус звонка · диалога', 28, 'text'), ('reason', 'Причина отказа', 26, 'text'),
        ('park', 'Парк', 20, 'text'), ('comment', 'Комментарий', 30, 'text'),
        ('updated_at', 'Дата изменения', 18, 'dt'),
    ),
}

_SUMMARY_COLUMNS = (
    ('touches', 'Касаний всего', 9, 'int'), ('outgoing', 'Исходящих', 9, 'int'),
    ('incoming', 'Входящих', 9, 'int'), ('talks', 'Разговоров', 10, 'int'),
    ('talk_seconds', 'Разговор всего, с', 11, 'int'), ('first_at', 'Первое касание', 17, 'dt'),
    ('first_out_at', 'Первый исходящий ОП', 17, 'dt'), ('reaction_min', 'Реакция ОП, мин', 12, 'float'),
    ('last_at', 'Последнее касание', 17, 'dt'), ('operators', 'Кто работал с лидом', 34, 'list'),
    ('dup', 'Дубль телефона', 11, 'text'), ('before_lead', 'Касаний до заявки', 11, 'int'),
    ('owner_called', 'Ответственный звонил', 12, 'text'),
)

_TOUCH_COLUMNS = (
    ('operator', 'ФИО', 24), ('direction', 'Направление', 15), ('started_at', 'Дата и время', 17),
    ('call_type', 'Тип', 17), ('result', 'Результат', 17), ('talk_seconds', 'Разговор, с', 9),
)

_TOUCHES_SHEET = (
    ('ID сделки', 12), ('Телефон', 13), ('Тип лида', 10), ('Статус / этап', 30),
    ('Дата сделки', 17), ('№ касания', 9), ('Дата и время', 17), ('Ответили в', 17),
    ('ФИО', 26), ('Вн. номер', 9), ('Направление', 16), ('Тип', 18), ('Результат', 18),
    ('Разговор, с', 10), ('Вызов всего, с', 10), ('Очередь', 9), ('Есть запись', 10),
    ('Ссылка на запись', 64), ('linkedid', 20), ('Относительно заявки', 15), ('Дней от заявки', 11),
)


def _as_dt(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19].replace('T', ' '))
    except ValueError:
        return None


def _iso(value):
    value = _as_dt(value)
    return value.isoformat(sep=' ') if value else None


def _header_cell(sheet, value, fill, font=HEADER_FONT):
    cell = WriteOnlyCell(sheet, value=value)
    cell.fill = fill
    cell.font = font
    cell.alignment = HEADER_ALIGN
    return cell


def _value_cell(sheet, value, kind):
    if value is None or value == '':
        return None
    if kind == 'dt':
        moment = _as_dt(value)
        if moment is None:
            return None
        cell = WriteOnlyCell(sheet, value=moment)
        cell.number_format = DATE_FORMAT
        return cell
    if kind == 'int':
        return int(value)
    if kind == 'float':
        return round(float(value), 1)
    if kind == 'bool':
        return 'да' if value else ''
    if kind == 'list':
        return ', '.join(str(v) for v in value if v)
    return str(value)


def report_filename(source_info, period_from, period_to, extension='xlsx'):
    label = source_info.get('label') or 'Сделки'
    if str(period_from)[:10] == str(period_to)[:10]:
        return '%s %s + касания ОП.%s' % (label, _ru_date(period_from), extension)
    return '%s %s-%s + касания ОП.%s' % (label, _ru_date(period_from), _ru_date(period_to), extension)


# ── контекст ─────────────────────────────────────────────────────────────────

def build_context(rows, *, source_info, period_from, period_to, touches_to, summary, blocks,
                  full_url, filters_note='', generated_by='', generated_at=None,
                  cdr_coverage=None, leads_coverage=None, grace_minutes=2, no_window=False,
                  own_only=False):
    """Словарь для листа «Контекст» и для ключа КОНТЕКСТ_ДЛЯ_ИИ. Один источник
    текста на оба места — иначе книга и JSON расходились бы в объяснениях."""
    generated_at = generated_at or datetime.now(ALMATY)
    subject, subjects = source_info['subject'], source_info['subjects']
    total = summary.get('leads', 0) or 0
    with_touches = summary.get('with_touches', 0) or 0
    cdr_coverage = cdr_coverage or {}
    leads_coverage = leads_coverage or {}
    missing_lead_days = leads_coverage.get('missing_days') or []

    binding = (
        'По номеру телефона и по времени. Звонок относится к %s, если он состоялся не раньше чем '
        'за %d минуты до её появления в системе и раньше появления следующей %s с тем же '
        'номером. Окна соседних записей с одним номером стыкуются встык и не перекрываются, '
        'поэтому один звонок никогда не посчитан дважды. Допуск в %d минуты нужен потому, что '
        'карточку часто заводят уже после начала разговора: человек звонит, оператор берёт '
        'трубку, запись появляется через несколько секунд (медиана опережения 10 с). Колонка '
        '«Дубль телефона» показывает, что записей с этим номером несколько: «2 из 3» — вторая '
        'по времени из трёх.' % (subject, grace_minutes, subject, grace_minutes))
    if no_window:
        binding += (' В ЭТОМ файле окна по дате отключены для записей с уникальным номером: к ним '
                    'отнесены все звонки по номеру за весь период звонков, включая сделанные до '
                    'появления записи. У записей с дублем номера окна сохранены.')
    traps = []
    if filters_note:
        traps.append('Файл собран с фильтрами: %s. Это не весь поток, а его срез.' % filters_note)
    if own_only:
        traps.append('В файле ТОЛЬКО звонки ответственного по каждой записи. Звонки коллег по тем '
                     'же номерам намеренно исключены: «0 касаний» значит «ответственный не '
                     'звонил», а не «никто не звонил».')
    traps.append(
        'У %d из %d %s (%.0f%%) касаний нет. Часть этого законна: лиды типа «wz» (WhatsApp) '
        'ведут верификаторы в переписке, и звонков по ним быть не должно; у «Потока» лид может '
        'быть не взят в работу вовсе.'
        % (total - with_touches, total, subjects, (100.0 * (total - with_touches) / total) if total else 0))
    traps.append(
        '«Сброс без разговора» без ссылки на запись — повторные наборы автодозвона подряд, а не '
        'отдельные попытки. Нужны реальные попытки — считайте касания с непустой ссылкой на запись.')
    traps.append(
        'Окно наблюдения у записей разной длины: звонки взяты по %s включительно, поэтому у ранних '
        'записей на дозвон было больше времени, чем у поздних. Сравнивать число касаний по дням '
        'создания напрямую нельзя — берите «Реакция ОП, мин».' % _ru_date(touches_to))
    traps.append(
        '«Разговор, с» — чистое время разговора (billsec плеча агента), без гудков и ожидания в '
        'очереди. «Дата и время» касания — начало вызова; момент ответа — в колонке «Ответили в» '
        'листа «Касания», когда станция его отдала.')
    traps.append(
        'Статус / этап — состояние на момент последней выгрузки из источника, а не на момент '
        'звонка. Источник переписывает прошлое: статусы меняются задним числом.')
    traps.append(
        'Оператор определён по внутреннему номеру из имени файла записи и каналов; ФИО подставлено '
        'на дату звонка — номер уволившегося отдают новому сотруднику.')
    traps.append('Ссылки на записи открываются только из внутренней сети компании.')
    if cdr_coverage.get('days_missing'):
        traps.append('Звонки не полны: %d суток из %d ещё не забраны со станции (мост в '
                     'корпоративной сети приносит их с задержкой). Цифры по этим суткам занижены.'
                     % (cdr_coverage['days_missing'], cdr_coverage.get('days_total', 0)))
    if missing_lead_days:
        traps.append('За %d суток периода записей источника в снимке нет (%s): либо их не было, '
                     'либо ночная выгрузка «Воронки ОП» ещё не прошла — она идёт в 05:20 за вчера.'
                     % (len(missing_lead_days),
                        ', '.join(_ru_date(d) for d in missing_lead_days[:5])
                        + ('…' if len(missing_lead_days) > 5 else '')))
    traps.append('Записи разговоров не расшифрованы — в файле факты о звонках и ссылки на аудио.')

    return {
        'прочти_первым': (
            'Это выгрузка %s отдела продаж («%s»), к каждой записи приклеены все звонки отдела по '
            'её номеру телефона: кто звонил, когда, дозвонился ли, сколько говорили и где запись. '
            'Ниже — что это за данные, что означает каждая колонка и чему в цифрах верить нельзя. '
            'Лист «Лиды» широкий, по одной записи в строке; лист «Касания» — по одному звонку в '
            'строке, для сводных и подсчётов.' % (subjects, source_info['label'])),
        'что_это_за_файл': (
            'Компания управляет сетью таксопарков в Казахстане и подключает водителей к работе на '
            'заказах агрегатора. Источник записей — %s; момент записи — %s. В файле %d %s за '
            '%s — %s и %d касаний отдела продаж по ним за %s — %s.'
            % (source_info['system'], source_info['moment'], total, subjects,
               _ru_date(period_from), _ru_date(period_to), summary.get('touches', 0),
               _ru_date(period_from), _ru_date(touches_to))),
        'что_такое_касание': (
            'Касание — один звонок между отделом продаж и человеком из записи. Это не строка '
            'телефонной станции: у звонка в очередь строк бывает десятки, они склеены по linkedid. '
            'Касания пронумерованы по времени: «Касание 1» — первый звонок в окне записи.'),
        'как_касания_привязаны': binding,
        'колонки_касания': {
            'ФИО': 'кто звонил (для входящих — кто взял трубку); прочерк, если никто не принял',
            'Направление': 'группа отдела продаж: Основа ОП, Поток, Яндекс Регистрация, Верификатор, СВ',
            'Дата и время': 'начало звонка, Алматы (UTC+5)',
            'Тип': '«Исходящий» — звонил оператор; «Входящий» — позвонил человек и оператор ответил; '
                   '«Входящий (не приняли)» — никто не взял трубку',
            'Результат': '«Разговор» — говорили; «Не ответил»; «Занято»; «Сброс без разговора» — '
                         'соединение было, разговора нет; «Не соединился» — техническая ошибка',
            'Разговор, с': 'чистое время разговора, без ожидания в очереди',
            ('Ссылка на запись' if full_url else 'Запись'):
                'адрес wav-файла на сервере записей; открывается только из внутренней сети',
        },
        'колонки_сводки': {
            'Касаний всего': 'сколько звонков ОП было по записи в её окне',
            'Исходящих / Входящих': 'те же касания по инициатору',
            'Разговоров': 'сколько касаний закончились разговором (длительность > 0)',
            'Разговор всего, с': 'суммарное время разговоров по записи',
            'Первое касание / Последнее касание': 'крайние по времени касания',
            'Первый исходящий ОП': 'первый звонок именно со стороны отдела',
            'Реакция ОП, мин': 'минут от появления записи до первого исходящего; отрицательное — '
                               'оператор набрал номер раньше, чем завёл карточку',
            'Кто работал с лидом': 'все операторы, касавшиеся записи, в порядке появления',
            'Дубль телефона': '«2 из 3» — вторая по времени запись с этим номером из трёх',
            'Касаний до заявки': 'звонки, начавшиеся до появления записи (в пределах допуска)',
            'Ответственный звонил': 'да / нет — звонил ли закреплённый за записью сотрудник; пусто, '
                                    'если ответственного нет или звонков не было',
        },
        'ловушки_обязательно_учесть': traps,
        'на_что_файл_отвечает': [
            'дозвонились ли до человека и с какой попытки',
            'сколько времени прошло от появления записи до первого звонка и как это отличается по дням, паркам, источникам',
            'сколько попыток тратится на одну запись и когда попытки прекращают',
            'какие записи остались без звонка и чем они отличаются от остальных',
            'звонил ли закреплённый сотрудник сам или лидом занимались другие',
        ],
        'на_что_файл_НЕ_отвечает': [
            'о чём говорили — записи есть ссылками, расшифровок нет',
            'переписка в WhatsApp и других мессенджерах — только телефония',
            'история статусов в источнике между звонками',
            'звонки вне окна %s — %s' % (_ru_date(period_from), _ru_date(touches_to)),
        ],
        'цифры': {
            'записей': total, 'с_касаниями': with_touches,
            'касаний': summary.get('touches', 0), 'разговоров': summary.get('talks', 0),
            'медиана_реакции_мин': summary.get('median_reaction_min'),
            'ответственный_звонил': summary.get('owner_called'),
            'блоков_касаний_на_листе': blocks,
        },
        'собрано': generated_at.strftime('%d.%m.%Y %H:%M') + ' (Алматы)',
        'собрал': generated_by or '—',
    }


# ── книга ────────────────────────────────────────────────────────────────────

def build_workbook(rows, *, source_info, period_from, period_to, touches_to, summary, blocks,
                   full_url, filters_note='', generated_by='', generated_at=None,
                   cdr_coverage=None, leads_coverage=None, grace_minutes=2, no_window=False,
                   own_only=False, text_warning_patch=None):
    """rows — список {'lead', 'touches', 'agg', 'owner_called', 'lead_type', 'stage', 'park'}.
    Возвращает (BytesIO, число записей)."""
    context = build_context(
        rows, source_info=source_info, period_from=period_from, period_to=period_to,
        touches_to=touches_to, summary=summary, blocks=blocks, full_url=full_url,
        filters_note=filters_note, generated_by=generated_by, generated_at=generated_at,
        cdr_coverage=cdr_coverage, leads_coverage=leads_coverage, grace_minutes=grace_minutes,
        no_window=no_window, own_only=own_only)

    workbook = Workbook(write_only=True)
    context_sheet = workbook.create_sheet('Контекст')
    leads_sheet = workbook.create_sheet('Лиды')
    touches_sheet = workbook.create_sheet('Касания')
    operators_sheet = workbook.create_sheet('Операторы')
    summary_sheet = workbook.create_sheet('Сводка')

    _fill_context(context_sheet, context)
    base_columns = _BASE_COLUMNS.get(source_info.get('key'), _BASE_COLUMNS['amo'])
    phone_col = _fill_leads(leads_sheet, rows, base_columns, blocks, full_url)
    written_touches = _fill_touches(touches_sheet, rows)
    _fill_operators(operators_sheet, rows)
    _fill_summary(summary_sheet, summary, rows)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    if text_warning_patch and rows:
        # Телефоны лежат текстом намеренно; зелёный уголок гасим на обоих листах.
        try:
            stream = text_warning_patch(
                stream, '%s3:%s%d' % (phone_col, phone_col, len(rows) + 2),
                sheet_path='xl/worksheets/sheet2.xml')
            if written_touches:
                stream = text_warning_patch(
                    stream, 'B2:B%d J2:J%d S2:S%d' % ((written_touches + 1,) * 3),
                    sheet_path='xl/worksheets/sheet3.xml')
        except Exception:
            stream.seek(0)
    return stream, len(rows)


def _fill_context(sheet, context):
    sheet.column_dimensions['A'].width = 36
    sheet.column_dimensions['B'].width = 118

    def section(title):
        cell = WriteOnlyCell(sheet, value=title)
        cell.font = SECTION_FONT
        sheet.append([cell])

    def line(key, value):
        left = WriteOnlyCell(sheet, value=key)
        left.font = Font(bold=True)
        left.alignment = WRAP_TOP
        if isinstance(value, (list, tuple)):
            value = '\n'.join(str(v) for v in value)
        elif isinstance(value, dict):
            value = '\n'.join('%s — %s' % (k, v) for k, v in value.items())
        right = WriteOnlyCell(sheet, value=value)
        right.alignment = WRAP_TOP
        sheet.append([left, right])

    title = WriteOnlyCell(sheet, value='Сделки отдела продаж и касания по ним')
    title.font = TITLE_FONT
    sheet.append([title])
    sheet.append([])
    section('ПРОЧТИ ПЕРВЫМ')
    line('Что это', context['прочти_первым'])
    line('Что за бизнес и данные', context['что_это_за_файл'])
    line('Что считается касанием', context['что_такое_касание'])
    line('Как касания привязаны', context['как_касания_привязаны'])
    sheet.append([])
    section('КОЛОНКИ БЛОКА «КАСАНИЕ N»')
    for key, value in context['колонки_касания'].items():
        line(key, value)
    sheet.append([])
    section('КОЛОНКИ СВОДКИ ПО ЗАПИСИ')
    for key, value in context['колонки_сводки'].items():
        line(key, value)
    sheet.append([])
    section('ЛОВУШКИ — ОБЯЗАТЕЛЬНО УЧЕСТЬ')
    for index, value in enumerate(context['ловушки_обязательно_учесть'], 1):
        line('%d.' % index, value)
    sheet.append([])
    section('НА ЧТО ФАЙЛ ОТВЕЧАЕТ')
    for value in context['на_что_файл_отвечает']:
        line('', value)
    section('НА ЧТО ФАЙЛ НЕ ОТВЕЧАЕТ')
    for value in context['на_что_файл_НЕ_отвечает']:
        line('', value)
    sheet.append([])
    section('ЦИФРЫ ФАЙЛА')
    for key, value in context['цифры'].items():
        line(key.replace('_', ' ').capitalize(), value if not isinstance(value, dict)
             else ', '.join('%s: %s' % kv for kv in value.items()))
    line('Собрано', context['собрано'])
    line('Собрал', context['собрал'])


def _fill_leads(sheet, rows, base_columns, blocks, full_url):
    """Лист «Лиды»: исходные колонки, сводка, блоки касаний. Возвращает букву
    колонки телефона — её гасят от «числа, сохранённого как текст»."""
    touch_columns = list(_TOUCH_COLUMNS) + [
        ('recording_url', 'Ссылка на запись', 62) if full_url else ('recording_url', 'Запись', 11)]
    widths = ([w for _k, _t, w, _kind in base_columns]
              + [w for _k, _t, w, _kind in _SUMMARY_COLUMNS]
              + [w for _k, _t, w in touch_columns] * blocks + [60])
    sheet.freeze_panes = 'C3'
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    # Первый этаж шапки: название группы в первой ячейке, остальные — полоса цвета.
    top = []
    for index, (_k, title, _w, _kind) in enumerate(base_columns):
        top.append(_header_cell(sheet, title if index >= 0 else '', FILL_BASE))
    top.append(_header_cell(sheet, 'СВОДКА ПО КАСАНИЯМ', FILL_SUMMARY))
    top.extend(_header_cell(sheet, '', FILL_SUMMARY) for _ in _SUMMARY_COLUMNS[1:])
    for block in range(blocks):
        fill = FILL_BLOCK_A if block % 2 == 0 else FILL_BLOCK_B
        top.append(_header_cell(sheet, 'КАСАНИЕ %d' % (block + 1), fill))
        top.extend(_header_cell(sheet, '', fill) for _ in touch_columns[1:])
    top.append(_header_cell(sheet, 'Касания %d+' % (blocks + 1), FILL_BASE))
    sheet.append(top)

    second = [_header_cell(sheet, title, FILL_BASE) for _k, title, _w, _kind in base_columns]
    second.extend(_header_cell(sheet, title, FILL_SUMMARY) for _k, title, _w, _kind in _SUMMARY_COLUMNS)
    for block in range(blocks):
        fill = FILL_SUB_A if block % 2 == 0 else FILL_SUB_B
        second.extend(_header_cell(sheet, title, fill) for _k, title, _w in touch_columns)
    second.append(_header_cell(sheet, 'остальные касания текстом', FILL_BASE))
    sheet.append(second)

    for row in rows:
        lead, agg, touches = row['lead'], row['agg'], row['touches']
        cells = []
        for key, _title, _w, kind in base_columns:
            cells.append(_value_cell(sheet, lead.get(key), kind))
        values = dict(agg)
        values['dup'] = lead.get('dup') or ''
        values['owner_called'] = row.get('owner_called') or ''
        for key, _title, _w, kind in _SUMMARY_COLUMNS:
            value = values.get(key)
            if kind == 'int' and not value:
                value = 0 if key == 'touches' else None
            cells.append(_value_cell(sheet, value, kind))
        for touch in touches[:blocks]:
            cells.extend([
                touch.get('operator') or '—',
                touch.get('direction') or '',
                _value_cell(sheet, touch.get('started_at'), 'dt'),
                touch.get('call_type') or '',
                touch.get('result') or '',
                int(touch.get('talk_seconds') or 0),
                _link_cell(sheet, touch.get('recording_url'), full_url),
            ])
        for _ in range(blocks - min(len(touches), blocks)):
            cells.extend([None] * len(touch_columns))
        rest = touches[blocks:]
        if rest:
            text = '; '.join(
                '%d) %s %s %s %s %sс' % (blocks + 1 + i, _iso(t.get('started_at')) or '',
                                         t.get('operator') or '—', t.get('call_type') or '',
                                         t.get('result') or '', int(t.get('talk_seconds') or 0))
                for i, t in enumerate(rest))
            cell = WriteOnlyCell(sheet, value=text)
            cell.alignment = WRAP_TOP
            cells.append(cell)
        sheet.append(cells)

    if rows:
        sheet.auto_filter.ref = 'A2:%s%d' % (get_column_letter(len(widths)), len(rows) + 2)
    phone_index = next((i for i, (k, _t, _w, _kind) in enumerate(base_columns, start=1)
                        if k == 'phone'), 1)
    return get_column_letter(phone_index)


def _link_cell(sheet, url, full_url):
    if not url:
        return None
    cell = WriteOnlyCell(sheet, value=url if full_url else 'запись')
    cell.hyperlink = url
    cell.font = LINK_FONT
    return cell


def _fill_touches(sheet, rows):
    sheet.freeze_panes = 'A2'
    for index, (_title, width) in enumerate(_TOUCHES_SHEET, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.append([_header_cell(sheet, title, FILL_HEADER) for title, _w in _TOUCHES_SHEET])
    written = 0
    for row in rows:
        lead = row['lead']
        moment = _as_dt(lead.get('moment'))
        for number, touch in enumerate(row['touches'], start=1):
            started = _as_dt(touch.get('started_at'))
            relation, days = '', None
            if moment is not None and started is not None:
                delta = (started - moment).total_seconds()
                relation = 'до заявки' if delta < 0 else 'после заявки'
                days = round(delta / 86400.0, 2)
            sheet.append([
                str(lead.get('key') or ''), str(touch.get('phone') or ''),
                lead.get('lead_type') or '', lead.get('stage') or '',
                _value_cell(sheet, moment, 'dt'), number,
                _value_cell(sheet, started, 'dt'), _value_cell(sheet, touch.get('answered_at'), 'dt'),
                touch.get('operator') or '—', str(touch.get('ext') or ''),
                touch.get('direction') or '', touch.get('call_type') or '', touch.get('result') or '',
                int(touch.get('talk_seconds') or 0), int(touch.get('dial_seconds') or 0),
                str(touch.get('queue') or ''), 'да' if touch.get('recording_url') else 'нет',
                _link_cell(sheet, touch.get('recording_url'), True),
                str(touch.get('linkedid') or ''), relation, days,
            ])
            written += 1
    if written:
        sheet.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(_TOUCHES_SHEET)), written + 1)
    return written


def operators_table(rows):
    """Разрез по операторам из касаний файла: касаний, разговоров, часов, записей."""
    table = {}
    for row in rows:
        for touch in row['touches']:
            name = touch.get('operator') or '—'
            item = table.setdefault(name, {
                'operator': name, 'direction': touch.get('direction') or '', 'exts': set(),
                'touches': 0, 'talks': 0, 'talk_seconds': 0, 'leads': set(),
            })
            if touch.get('ext'):
                item['exts'].add(str(touch['ext']))
            item['touches'] += 1
            if int(touch.get('talk_seconds') or 0) > 0:
                item['talks'] += 1
            item['talk_seconds'] += int(touch.get('talk_seconds') or 0)
            item['leads'].add(row['lead'].get('key'))
    out = []
    for item in table.values():
        item['exts'] = ','.join(sorted(item['exts']))
        item['leads'] = len(item['leads'])
        out.append(item)
    out.sort(key=lambda item: -item['touches'])
    return out


def _fill_operators(sheet, rows):
    sheet.freeze_panes = 'A2'
    titles = ('ФИО', 'Вн. номера', 'Направление', 'Касаний', 'Разговоров', 'Разговор, ч',
              'Записей', 'Дозваниваемость, %')
    for index, width in enumerate((30, 14, 18, 10, 11, 11, 10, 14), start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.append([_header_cell(sheet, title, FILL_HEADER) for title in titles])
    for item in operators_table(rows):
        sheet.append([item['operator'], item['exts'], item['direction'], item['touches'],
                      item['talks'], _hours(item['talk_seconds']), item['leads'],
                      _percent(item['talks'], item['touches'])])


def _fill_summary(sheet, summary, rows):
    sheet.column_dimensions['A'].width = 44
    for column in 'BCDE':
        sheet.column_dimensions[column].width = 14

    def block(title, lines, head=None):
        cell = WriteOnlyCell(sheet, value=title)
        cell.font = SECTION_FONT
        sheet.append([cell])
        if head:
            sheet.append([_header_cell(sheet, h, FILL_SUMMARY) for h in head])
        for line in lines:
            sheet.append(list(line))
        sheet.append([])

    total = summary.get('leads', 0) or 0
    block('ГЛАВНОЕ', [
        ('Записей в файле', total),
        ('Записей с касаниями ОП', summary.get('with_touches', 0)),
        ('Записей без единого звонка', summary.get('without_touches', 0)),
        ('Доля записей с касанием, %', _percent(summary.get('with_touches', 0), total)),
        ('Касаний всего', summary.get('touches', 0)),
        ('Из них закончились разговором', summary.get('talks', 0)),
        ('Общее время разговоров, ч', _hours(summary.get('talk_seconds', 0))),
        ('Медиана реакции ОП, мин', summary.get('median_reaction_min')),
        ('Касаний до появления записи', summary.get('before_lead', 0)),
        ('«Ответственный звонил»: да / нет / пусто', '%s / %s / %s' % (
            summary.get('owner_called', {}).get('yes', 0),
            summary.get('owner_called', {}).get('no', 0),
            summary.get('owner_called', {}).get('blank', 0))),
    ])
    block('ПО ТИПУ ЛИДА', [(k, v, t, _percent(t, v)) for k, v, t in summary.get('by_lead_type', [])],
          ('Тип лида', 'Записей', 'С касанием', 'Доля, %'))
    block('ПО СТАТУСАМ / ЭТАПАМ', [(k, v, t, _percent(t, v)) for k, v, t in summary.get('by_stage', [])],
          ('Статус', 'Записей', 'С касанием', 'Доля, %'))
    block('ПО ПАРКАМ', [(k, v, t, _percent(t, v)) for k, v, t in summary.get('by_park', [])],
          ('Парк', 'Записей', 'С касанием', 'Доля, %'))
    block('СКОЛЬКО КАСАНИЙ НА ЗАПИСЬ', [(k, v, _percent(v, total)) for k, v in summary.get('distribution', [])],
          ('Касаний', 'Записей', 'Доля, %'))
    block('ОПЕРАТОРЫ, ТОП-20', [(k, v) for k, v in summary.get('by_operator', [])[:20]],
          ('ФИО', 'Касаний'))
    by_day = {}
    for row in rows:
        for touch in row['touches']:
            day = str(_iso(touch.get('started_at')) or '')[:10]
            by_day[day] = by_day.get(day, 0) + 1
    block('КАСАНИЯ ПО ДНЯМ', [(_ru_date(k), v) for k, v in sorted(by_day.items()) if k],
          ('День', 'Касаний'))


# ── JSON ─────────────────────────────────────────────────────────────────────

def build_json(rows, *, source_info, period_from, period_to, touches_to, summary, blocks,
               full_url, filters_note='', generated_by='', generated_at=None, cdr_coverage=None,
               leads_coverage=None, grace_minutes=2, no_window=False, own_only=False):
    """Тот же файл для ИИ и скриптов. Первый ключ — контекст: модель читает сверху."""
    context = build_context(
        rows, source_info=source_info, period_from=period_from, period_to=period_to,
        touches_to=touches_to, summary=summary, blocks=blocks, full_url=full_url,
        filters_note=filters_note, generated_by=generated_by, generated_at=generated_at,
        cdr_coverage=cdr_coverage, leads_coverage=leads_coverage, grace_minutes=grace_minutes,
        no_window=no_window, own_only=own_only)
    return {
        'КОНТЕКСТ_ДЛЯ_ИИ': context,
        'метаданные': {
            'источник': source_info['label'], 'система': source_info['system'],
            'период_записей': '%s - %s' % (_ru_date(period_from), _ru_date(period_to)),
            'период_звонков': '%s - %s' % (_ru_date(period_from), _ru_date(touches_to)),
            'отдел': 'Отдел продаж', 'телефония': 'FreePBX (CDR через мост в корпоративной сети)',
            'фильтры': filters_note or 'без фильтров',
        },
        'сводка': {
            'по_операторам': summary.get('by_operator', []),
            'по_типу_лида': summary.get('by_lead_type', []),
            'по_статусам': summary.get('by_stage', []),
            'по_паркам': summary.get('by_park', []),
            'распределение_касаний': summary.get('distribution', []),
            'операторы': [{k: v for k, v in item.items()} for item in operators_table(rows)],
        },
        'записи': [_lead_json(row) for row in rows],
    }


def _lead_json(row):
    lead, agg = row['lead'], row['agg']
    out = {k: (_iso(v) if isinstance(v, (datetime, date)) else v)
           for k, v in lead.items() if k not in ('phones_list',)}
    out['касаний'] = agg['touches']
    out['реакция_мин'] = agg['reaction_min']
    out['ответственный_звонил'] = row.get('owner_called') or ''
    out['кто_работал'] = agg['operators']
    out['касания'] = [{
        'время': _iso(t.get('started_at')), 'ответили_в': _iso(t.get('answered_at')),
        'фио': t.get('operator') or '', 'вн_номер': t.get('ext') or '',
        'направление': t.get('direction') or '', 'тип': t.get('call_type') or '',
        'результат': t.get('result') or '', 'разговор_сек': int(t.get('talk_seconds') or 0),
        'запись': t.get('recording_url') or '', 'linkedid': t.get('linkedid') or '',
    } for t in row['touches']]
    return out
