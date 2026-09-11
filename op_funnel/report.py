# -*- coding: utf-8 -*-
"""Выгрузка воронки ОП в xlsx (раздел «Воронка ОП», задачи #301–#305).

Пять листов: «Свод», «По операторам», «По дням», «Причины», «Лиды». Первым идёт
свод — цифра без периода, без направления и без оговорок живёт своей жизнью, а
через неделю по файлу «Воронка ОП.xlsx» уже не восстановить, за какие сутки он и
что в нём считалось дозвоном. Тот же порядок, что у «Касаний» и «Посылок».

ФАЙЛ — СНИМОК, И ЭТО НЕ ОГОВОРКА ДЛЯ ПОРЯДКА
---------------------------------------------
СРМ переписывает прошлое. Замер 11.09.2026: снимок супервайзера за 01.09 против
того, что партнёрская ручка отдаёт сейчас, — состав лидов совпал ровно (1125
против 1125), а исходы уехали: Дозвон 499 → 534, Согласия 257 → 267. Поэтому в
книгу идут ЗАФИКСИРОВАННЫЕ суточные итоги (`op_funnel_daily`), а не пересчёт из
источника на лету, и на «Своде» стоят дата сборки и строка свежести выгрузки.
Функция в базу не ходит вовсе: всё приходит аргументами из роута — иначе экран и
файл, собранные в одну минуту, показывали бы разные числа.

ПОЧЕМУ openpyxl, А НЕ xlsxwriter
--------------------------------
В проекте живут оба: монолит местами пишет книги через xlsxwriter. В ОДНОЙ книге
их смешивать нельзя — это два независимых писателя одного файла, и второй не
видит листов первого. Все пакетные выгрузки («Касания», «Посылки», «Чаты
водителей», «Провайдер ЭДО») стоят на openpyxl, эта — тоже.

ПОТОКОВЫЙ РЕЖИМ И ЕГО ДВЕ МОЛЧАЛИВЫЕ ЛОВУШКИ
---------------------------------------------
1. **`freeze_panes` и ширины колонок задаются ДО первого `append`.** Шапка листа
   уходит в файл вместе с первой строкой, и выставленное после в книгу уже не
   попадёт — без ошибки, просто лист откроется без закреплённой шапки.
2. **Объекты стиля создаются один раз, модульными константами.** `Font(...)`
   внутри цикла — восьмикратное замедление: 37 секунд против 4,4 на 30 тысячах
   строк (замер 25.08.2026, `cdr/report.py`).

Отсюда же асимметрия набора колонок. У «По операторам» и «По дням» колонки,
которых у направления нет (входящая линия, «увели в другой процесс»), из шапки
убираются: пять колонок нулей читаются как «данные не подтянулись». У «Лидов»
так сделать нельзя — шапка уходит в файл до первой строки, а строки приходят
генератором, и «посмотреть в данные и решить» там негде. Набор колонок листа
«Лиды» поэтому постоянный.

ЛИСТЫ СОЗДАЮТСЯ В ПОРЯДКЕ ЧТЕНИЯ, А НАПОЛНЯЮТСЯ В ДРУГОМ
---------------------------------------------------------
«Лиды» наполняются ПЕРВЫМИ: до конца обхода генератора неизвестно ни число
строк, ни сработал ли потолок, а сказать об обрезке нужно на первом листе — и до
счётчиков, а не сноской после них. Порядок наполнения на порядок листов в книге
не влияет: в потоковом режиме у каждого листа свой временный файл (проверено на
openpyxl 3.1.5, вперемешку приходящие строки двух листов не путаются).

ПОТОЛОК ЛИСТА «ЛИДЫ»
--------------------
Замер 01.08–11.09.2026 (42 суток): у Потока 30 000 лидов в первом потоке и
23 514 во втором — это около 1,3 тысячи строк в сутки, то есть месяц Потока даёт
порядка сотни тысяч строк, а выгрузка «за квартал» — уже сотни тысяч. Excel
держит 1 048 576 строк, память Render — нет: 300 тысяч строк в потоковом режиме
стоят около 30 МБ (замер `cdr/report.py`), и это тот предел, на который инстанс
ещё рассчитан. Поэтому `LEADS_LIMIT = 300000`, а если потолок сработал, об этом
написано на «Своде» ДО счётчиков: молча обрезанный файл читается как полный.
Потолок ОБРЫВАЕТ генератор недочитанным — курсор закрывает вызывающий, у роута
это происходит вместе с ответом.

ЦВЕТ — ТОЛЬКО НА ПРОЦЕНТЕ ВЫПОЛНЕНИЯ ПЛАНА
-------------------------------------------
Прямое требование владельца: цвет только там, где несёт смысл. Три тона на
колонках «% плана», нейтральное состояние (нет данных) не красится вовсе —
`metrics.color_bucket` отдаёт для него пустую строку, и серый прочерк честнее
зелёного нуля. На «Своде» заливки нет: там колонка значений шириной 92 знака, и
заливка превращается в полосу через пол-листа, которая индикатором уже не
читается.

Ловушка порогов: в `op_funnel_targets` сид кладёт `green_from = 100` и
`amber_from = 80`, то есть ПРОЦЕНТАМИ, а `color_bucket` ждёт долю (1,0 и 0,8).
Без нормализации весь лист был бы красным — 0,69 меньше 80. Нормализация в
`_thresholds`.

ЧИСЛА ЧИСЛАМИ, ПРОЦЕНТЫ ДОЛЕЙ ЕДИНИЦЫ
-------------------------------------
В ячейке лежит 0,508 с форматом `0.0%`, а не число 50,8: по доле работают и
среднее по колонке, и диаграмма, а «50,8» Excel с процентным форматом покажет
как 5080 %. Тот же формат и то же соглашение, что у биллинговой выгрузки
монолита (`_OKTELL_BILLING_EXPORT_PCT_FMT`) и что у API раздела, который отдаёт
проценты долями.

Конверсии НЕ приезжают аргументом, а считаются здесь же `metrics.derive_rates` —
той самой функцией, которой их считает экран. Две реализации одной конверсии
дают два ответа на один вопрос, и в этом проекте на таком уже горели.

Телефон и ключ лида лежат ТЕКСТОМ — числом номер теряет ведущие нули и уезжает в
экспоненту; зелёный уголок «Число сохранено как текст» гасится тегом
`<ignoredErrors>`, сама функция приходит аргументом, потому что живёт в монолите
(`_excel_suppress_number_as_text_warning`), а импортировать монолит из пакета
нельзя.
"""

from datetime import date, datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import metrics

ALMATY = ZoneInfo('Asia/Almaty')

# Стили — модульные константы. Создавать их в цикле нельзя, см. шапку модуля.
HEADER_FILL = PatternFill('solid', fgColor='1F2937')
HEADER_FONT = Font(bold=True, color='FFFFFF')
HEADER_ALIGN = Alignment(horizontal='center', vertical='center', wrap_text=True)
TITLE_FONT = Font(bold=True, size=13)
SECTION_FONT = Font(bold=True, size=11)
WRAP_TOP = Alignment(wrap_text=True, vertical='top')
# Число на «Своде» прижимается влево: в колонке шириной 92 знака прижатое вправо
# число отрывается от своей подписи на пол-экрана.
VALUE_ALIGN = Alignment(horizontal='left', vertical='top')

# Три тона выполнения плана — те же, что уже стоят в выгрузках монолита
# (bot_schedule2.py: DCFCE7 / FEF3C7 / FEE2E2). Ключи — ровно то, что возвращает
# metrics.color_bucket; пустой ключ («не красим») в словаре отсутствует
# намеренно, чтобы заливка нейтрального состояния была невозможна физически.
PLAN_FILLS = {
    'green': PatternFill('solid', fgColor='DCFCE7'),
    'amber': PatternFill('solid', fgColor='FEF3C7'),
    'red': PatternFill('solid', fgColor='FEE2E2'),
}

DAY_FORMAT = 'DD.MM.YYYY'
DATETIME_FORMAT = 'DD.MM.YYYY HH:MM'
PERCENT_FORMAT = '0.0%'
NUMBER_FORMATS = {'hours': '0.0', 'num1': '0.0', 'num2': '0.00'}
NUMBER_DECIMALS = {'hours': 1, 'num1': 1, 'num2': 2}

# Подписи направлений. Каноническая подпись приезжает аргументом (её отдаёт
# `/meta`), этот словарь — для прогонов из скрипта и ночных выгрузок, чтобы в
# шапке книги не стояло «op_potok». В `database.py` у тех же кодов подписи с
# приставкой «ОП — » (CALCULATION_MODEL_DESCRIPTIONS); здесь она лишняя —
# название направления и так стоит рядом со словами «Воронка ОП».
DIRECTION_TITLES = {
    'op_osnova': 'Основа ОП',
    'op_potok': 'Поток',
    'op_yandex_reg': 'Яндекс Регистрация',
    'op_verificator': 'Верификатор',
}

# Откуда взялись цифры направления. Строка «Источник» на «Своде» отвечает на
# первый вопрос, который задают о незнакомом файле.
DIRECTION_SOURCES = {
    'op_osnova': 'amoCRM, воронка 5524684 «Отдел продаж»',
    'op_potok': 'партнёрская ручка СРМ /api/partners/stream-leads (потоки 1 и 2)',
    'op_yandex_reg': 'партнёрская ручка СРМ /api/partners/paid-hire-leads',
    'op_verificator': 'чаты Wazzup и ручная выгрузка тикетов',
}

DIRECTION_VERIFICATOR = 'op_verificator'

# Подписи исходов собраны в ОДИН словарь на книгу, а не выписаны по листам
# руками: третья копия одних и тех же слов в проекте уже однажды разошлась —
# 01.09.2026 в прод уехало «ОТДАЛИ ОТПРАВИТЕЛЮ» вместо «Вернули отправителю», и
# заметить это можно было только открыв файл.
REACH_LABELS = {
    'dozvon': 'Дозвон',
    'nedozvon': 'Недозвон',
    # Не «Новый»: у платного найма это ещё и «В работе», и «Без статуса». Общее у
    # них одно — оператор до лида не дошёл, и в «обработано» лид не попал.
    'new': 'Не в работе',
}

DIALOG_LABELS = {
    # «Успех» — общее слово: источники называют его по-разному («вышел на линию»
    # у Потока, «ПРОШЕЛ РЕГИСТРАЦИЮ» в воронке 5524684). Сырой статус лежит
    # рядом, в колонках «Статус диалога» и «Этап».
    'success': 'Успех',
    'agree': 'Согласие',
    'reject': 'Отказ',
    'untargeted': 'Нецелевой',
    'callback': 'Перезвон назначен',
    'none': '',
}

BUCKET_LABELS = {
    'nedozvon': 'Недозвоны',
    'otkaz': 'Отказы',
    'netsel': 'Нецелевые',
    metrics.BUCKET_MOVED: 'Увели в другой процесс',
    '': '',
}

SHIFT_LABELS = {'day': 'День', 'night': 'Ночь', 'any': ''}

HOURS_SOURCE_LABELS = {
    'phone': 'iCORE Phone',
    'schedule': 'График смен',
    'manual': 'Ручная выгрузка',
}

HOURS_SOURCE_NOTES = {
    'phone': 'из статусов iCORE Phone — это факт, а не ввод руками',
    'schedule': 'по графику смен: на телефоне это направление не работает',
    'manual': 'из ручной выгрузки супервайзера',
}

UNMAPPED_LABEL = 'Не сопоставлен'

# Порядок листов = порядок чтения. Наполняются они в другом порядке, см. шапку.
SHEET_SUMMARY = 'Свод'
SHEET_OPERATORS = 'По операторам'
SHEET_DAYS = 'По дням'
SHEET_REASONS = 'Причины'
SHEET_LEADS = 'Лиды'
SHEET_TITLES = (SHEET_SUMMARY, SHEET_OPERATORS, SHEET_DAYS, SHEET_REASONS, SHEET_LEADS)

# Путь листа «Лиды» внутри xlsx считается ИЗ ЭТОГО ЖЕ кортежа, а не вписан
# строкой: openpyxl нумерует xl/worksheets/sheetN.xml по порядку СОЗДАНИЯ листов
# (проверено на 3.1.5). В «Касаниях» и «Посылках» путь вписан руками («второй
# лист, поэтому sheet2.xml»), и вставка нового листа в начало книги там молча
# начнёт гасить уголок не на том листе.
LEADS_SHEET_PATH = 'xl/worksheets/sheet%d.xml' % (SHEET_TITLES.index(SHEET_LEADS) + 1)

# Потолок строк листа «Лиды». Обоснование объёмами — в шапке модуля.
LEADS_LIMIT = 300000

# (ключ, заголовок, ширина, вид). Вид решает и тип ячейки, и формат, и заливку:
# 'plan' — единственный вид, который красится.
OPERATOR_COLUMNS = (
    ('operator', 'Оператор', 30, 'text'),
    ('group_name', 'Группа', 26, 'text'),
    ('rate', 'Ставка', 8, 'num2'),
    ('shift_kind', 'Смена', 8, 'text'),
    ('work_hours', 'Часы', 8, 'hours'),
    ('hours_source', 'Часы откуда', 14, 'text'),
    ('handled', 'Обработано', 12, 'int'),
    ('reached', 'Дозвон', 9, 'int'),
    ('not_reached', 'Недозвон', 10, 'int'),
    ('reach_rate', '% дозвона', 11, 'rate'),
    ('agreed', 'Согласия', 10, 'int'),
    ('agree_rate', '% согласий', 12, 'rate'),
    ('succeeded', 'Успехи', 9, 'int'),
    ('success_rate', '% успеха', 11, 'rate'),
    ('rejected', 'Отказы', 9, 'int'),
    ('untargeted', 'Нецелевые', 11, 'int'),
    ('callbacks', 'Перезвоны', 11, 'int'),
    ('moved', 'Увели', 9, 'int'),
    ('inbound', 'Входящие', 10, 'int'),
    ('leads_per_hour', 'Лидов в час', 12, 'num1'),
    ('plan_reached', 'План дозвонов', 14, 'num1'),
    ('plan_reached_rate', '% плана дозвонов', 17, 'plan'),
    ('plan_agreed', 'План согласий', 14, 'num1'),
    ('plan_agreed_rate', '% плана согласий', 17, 'plan'),
    ('chats', 'Чаты', 8, 'int'),
    ('tickets', 'Тикеты', 9, 'int'),
    ('chats_per_hour', 'Чатов в час', 12, 'num1'),
    ('chat_reply_seconds', 'Ответ в чате, с', 15, 'num1'),
    ('ticket_handle_seconds', 'Тикет, с', 10, 'num1'),
    ('quality_score', 'Качество', 10, 'num1'),
)

DAY_COLUMNS = (
    ('work_day', 'День', 12, 'date'),
    ('operators', 'Операторов', 11, 'int'),
    ('work_hours', 'Часы', 8, 'hours'),
    ('handled', 'Обработано', 12, 'int'),
    ('reached', 'Дозвон', 9, 'int'),
    ('not_reached', 'Недозвон', 10, 'int'),
    ('reach_rate', '% дозвона', 11, 'rate'),
    ('agreed', 'Согласия', 10, 'int'),
    ('agree_rate', '% согласий', 12, 'rate'),
    ('succeeded', 'Успехи', 9, 'int'),
    ('rejected', 'Отказы', 9, 'int'),
    ('untargeted', 'Нецелевые', 11, 'int'),
    ('callbacks', 'Перезвоны', 11, 'int'),
    ('moved', 'Увели', 9, 'int'),
    ('inbound', 'Входящие', 10, 'int'),
    ('leads_per_hour', 'Лидов в час', 12, 'num1'),
    ('plan_reached', 'План дозвонов', 14, 'num1'),
    ('plan_reached_rate', '% плана дозвонов', 17, 'plan'),
    ('chats', 'Чаты', 8, 'int'),
    ('tickets', 'Тикеты', 9, 'int'),
    ('chats_per_hour', 'Чатов в час', 12, 'num1'),
    ('chat_reply_seconds', 'Ответ в чате, с', 15, 'num1'),
)

REASON_COLUMNS = (
    ('bucket', 'Корзина', 22, 'text'),
    ('reason_title', 'Причина', 42, 'text'),
    ('reason_code', 'Код', 24, 'text'),
    ('leads', 'Лидов', 9, 'int'),
    ('share_bucket', 'Доля в корзине', 15, 'rate'),
    ('share_handled', 'Доля обработанных', 18, 'rate'),
)

LEAD_COLUMNS = (
    ('work_day', 'Сутки', 11, 'date'),
    ('operator', 'Оператор', 28, 'text'),
    # 'code' — текст с форматом «@»: ключ лида бывает 32-значным (у платного
    # найма это id аккаунта), числом Excel его округлит до 15 знаков.
    ('lead_key', 'Ключ лида', 18, 'code'),
    ('stream_type', 'Поток', 7, 'int'),
    ('full_name', 'Водитель', 26, 'text'),
    ('phone', 'Телефон', 16, 'code'),
    ('park_name', 'Таксопарк', 22, 'text'),
    ('city', 'Город', 16, 'text'),
    ('base_title', 'База', 22, 'text'),
    ('reach', 'Дозвон', 12, 'text'),
    ('dialog', 'Исход', 18, 'text'),
    ('bucket', 'Корзина', 20, 'text'),
    ('reason_title', 'Причина', 30, 'text'),
    ('call_status', 'Статус звонка', 18, 'text'),
    ('dialog_status', 'Статус диалога', 20, 'text'),
    ('stage_raw', 'Этап', 26, 'text'),
    ('comment', 'Комментарий', 40, 'text'),
    ('created_at', 'Загружен', 17, 'datetime'),
    ('taken_at', 'Взят в работу', 17, 'datetime'),
    ('updated_at', 'Обновлён', 17, 'datetime'),
)

# Колонки листа «Лиды», которые обязаны остаться текстом. Перечисление, а не
# отрезок: они разбросаны по листу, и одним диапазоном уголок гасился бы не у
# всех (та же ошибка была в «Касаниях», где C:E гасило только два столбца).
LEAD_TEXT_COLUMNS = ('lead_key', 'phone')

# Колонки, которые исчезают из шапки, когда по всем строкам в них ноль. Пустая
# колонка читается как «данные не подтянулись», а её отсутствие — как «у этого
# направления такого нет». Входящая линия есть только у Основы, «увели» —
# только в amoCRM, чаты и тикеты — только у Верификатора.
OPTIONAL_COLUMNS = frozenset((
    'inbound', 'moved', 'chats', 'tickets', 'chats_per_hour',
    'chat_reply_seconds', 'ticket_handle_seconds', 'quality_score',
))

# У Верификатора чаты, тикеты и качество показываются ВСЕГДА, даже нулями:
# нулевые чаты у этого направления — не «нечего показывать», а ЧП, и оно должно
# быть видно в файле, а не пропадать вместе с колонкой.
VERIFICATOR_COLUMNS = ('chats', 'tickets', 'chats_per_hour', 'chat_reply_seconds',
                       'ticket_handle_seconds', 'quality_score')


# ── общие мелочи ─────────────────────────────────────────────────────────────

def _column_index(columns, key):
    for index, column in enumerate(columns, start=1):
        if column[0] == key:
            return index
    return 1


def clean(value):
    """Текст, который xlsx вообще способен унести.

    Управляющие символы (0x00–0x1F, кроме табуляции и переводов строки) в XML
    запрещены, и openpyxl роняет на них ВСЮ книгу — IllegalCharacterError в
    момент `save()`. Комментарии к лидам операторы вставляют из чатов и из СРМ, а
    оттуда такой символ приезжает невидимым: одна строка «взял 3 лицо» с таким
    знаком означала бы, что выгрузка перестала работать у всех и навсегда.

    Заменяем пробелом, а не пустотой, — иначе два слова склеились бы в одно. Тот
    же приём, что в `parcels/report.py::clean`; свой в каждом пакете, чтобы
    раздел не тянул за собой чужой.
    """
    if value is None:
        return ''
    return ILLEGAL_CHARACTERS_RE.sub(' ', str(value))


def _text(sheet, value, *, keep_format=False):
    """Строковая ячейка, которая ГАРАНТИРОВАННО остаётся строкой.

    Единственная дверь для каждой строковой ячейки книги. Причин три:

    * openpyxl выводит тип из значения, и строка с ведущим «=» становится
      ФОРМУЛОЙ. В комментарии к лиду и в примечании к фильтру такое приезжает от
      живых людей, и книга открывалась бы с предупреждением безопасности;
    * управляющий символ роняет сборку целиком — см. `clean`;
    * `number_format='@'` нужен телефону и ключу лида: без него Excel предложит
      «преобразовать в число» при первой правке файла руками.

    Формат вешается только на непустую ячейку: у пустой он превращает «нет
    данных» в ноль при протяжке столбца.
    """
    text = clean(value)
    cell = WriteOnlyCell(sheet, value=text)
    cell.data_type = 's'
    if keep_format and text:
        cell.number_format = '@'
    return cell


def _as_float(value):
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value):
    """Через float: счётчики приезжают и int, и Decimal, и строкой «5»."""
    number = _as_float(value)
    return None if number is None else int(round(number))


def _as_date(value):
    """'2026-09-01' → date. Настоящей датой, а не строкой: иначе «По дням»
    сортируется по алфавиту и по нему не построить график."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _as_datetime(value):
    """Время источника кладём КАК ЕСТЬ, без пояса: источники отдают местное
    (Алматы), и сдвигать его нельзя — на этом уже горели, отчёт Chat2Desk уехал
    на +5 часов."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    for template in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(str(value)[:19], template)
        except (TypeError, ValueError):
            continue
    return None


def _ru_date(value):
    day = _as_date(value)
    return day.strftime('%d.%m.%Y') if day else '—'


def _ru_moment(value):
    moment = _as_datetime(value)
    return moment.strftime('%d.%m.%Y %H:%M') if moment else '—'


def period_label(period_from, period_to):
    left, right = _ru_date(period_from), _ru_date(period_to)
    if left == '—' and right == '—':
        return 'весь период'
    return left if left == right else '%s — %s' % (left, right)


def _days_in_period(period_from, period_to):
    left, right = _as_date(period_from), _as_date(period_to)
    if not left or not right:
        return None
    return (right - left).days + 1


def _thresholds(targets):
    """Пороги раскраски долями единицы: (зелёный, жёлтый).

    В `op_funnel_targets` они лежат ПРОЦЕНТАМИ — сид ставит green_from = 100 и
    amber_from = 80, — а `metrics.color_bucket` сравнивает с долей. Без этого
    перевода красным был бы весь лист: 0,69 меньше 80.

    Граница «это уже проценты» — 1,5. Выбрана по зазору между шкалами: в долях
    осмысленные пороги лежат в 0,5–1,2, в процентах — в 50–120, и полтора не
    попадает ни в одну из них.
    """
    targets = targets or {}
    green = _as_float(targets.get('green_from'))
    amber = _as_float(targets.get('amber_from'))
    green = 1.0 if green is None else (green / 100.0 if green > 1.5 else green)
    amber = 0.8 if amber is None else (amber / 100.0 if amber > 1.5 else amber)
    return green, amber


def _cell(sheet, value, kind, thresholds=None):
    """Ячейка таблицы по виду колонки. Пустая, а не «—», когда данных нет:
    прочерк в числовой колонке делает её текстовой, а по ней считают среднее."""
    if kind == 'text':
        return _text(sheet, value)
    if kind == 'code':
        return _text(sheet, value, keep_format=True)

    cell = WriteOnlyCell(sheet, value=None)
    if kind == 'date':
        cell.value = _as_date(value)
        if cell.value is not None:
            cell.number_format = DAY_FORMAT
        return cell
    if kind == 'datetime':
        cell.value = _as_datetime(value)
        if cell.value is not None:
            cell.number_format = DATETIME_FORMAT
        return cell
    if kind == 'int':
        cell.value = _as_int(value)
        return cell
    if kind in ('rate', 'plan'):
        rate = _as_float(value)
        cell.value = rate
        if rate is not None:
            cell.number_format = PERCENT_FORMAT
        if kind == 'plan':
            green, amber = thresholds or (1.0, 0.8)
            fill = PLAN_FILLS.get(metrics.color_bucket(rate, green, amber))
            if fill is not None:
                cell.fill = fill
        return cell

    number = _as_float(value)
    if number is not None:
        cell.value = round(number, NUMBER_DECIMALS.get(kind, 1))
        cell.number_format = NUMBER_FORMATS.get(kind, '0.0')
    return cell


def _setup(sheet, columns, freeze='A2'):
    """Шапка, ширины и закрепление. Всё ДО первой строки данных — иначе не
    доедет до файла, причём молча (ловушка потокового режима, см. шапку)."""
    if freeze:
        sheet.freeze_panes = freeze
    for index, column in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = column[2]
    header = []
    for column in columns:
        cell = WriteOnlyCell(sheet, value=column[1])
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGN
        header.append(cell)
    sheet.append(header)


def _autofilter(sheet, columns, rows):
    if rows:
        sheet.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(columns)), rows + 1)


def _visible_columns(columns, rows, keep=()):
    """Убрать колонки из OPTIONAL_COLUMNS, в которых по всем строкам ноль."""
    keep = set(keep)
    visible = []
    for column in columns:
        key = column[0]
        if key in OPTIONAL_COLUMNS and key not in keep:
            if not any(_as_float(row.get(key)) for row in rows):
                continue
        visible.append(column)
    return tuple(visible)


def _operator_name(row):
    """ФИО в портале, а если сотрудник ещё не сопоставлен — так и написано.

    Ни один источник не отдаёт id сотрудника, а имена в СРМ и в портале не
    совпадают («Кузембаева Аяулым» против «Кузембековой Аяулым»), поэтому связь
    подтверждает человек. Строка user_id = 0 остаётся в таблице намеренно: без
    неё сумма по операторам не сходится с итогом команды, и это выглядит как
    ошибка расчёта, а не как пробел в сопоставлении.
    """
    name = row.get('name') or row.get('operator') or row.get('user_name')
    if name:
        return str(name)
    raw = row.get('owner_raw')
    if raw:
        return '%s (%s)' % (UNMAPPED_LABEL, str(raw))
    return UNMAPPED_LABEL


def _with_rates(row):
    """Строка плюс конверсии, посчитанные тем же `metrics.derive_rates`, которым
    их считает экран. Порядок важен: значения конверсий перекрывают одноимённые
    поля строки — если запрос когда-нибудь начнёт отдавать свой «% дозвона», в
    файл всё равно пойдёт единственная формула раздела."""
    values = dict(row)
    values.update(metrics.derive_rates(row))
    return values


def _operator_values(row):
    values = _with_rates(row)
    values['operator'] = _operator_name(row)
    values['shift_kind'] = SHIFT_LABELS.get(row.get('shift_kind') or '', '')
    values['hours_source'] = HOURS_SOURCE_LABELS.get(row.get('hours_source') or '', '')
    values['group_name'] = row.get('group_name') or ''
    return values


def _day_values(row):
    values = _with_rates(row)
    values['work_day'] = row.get('work_day') or row.get('day')
    return values


def _reason_values(reasons, summary):
    """Причины сводятся по корзине и коду.

    Запрос отдаёт их подневно и по оператору (так лежит `op_funnel_reasons`), а
    читают их итогом за период — иначе в файле было бы 13 операторов × 11 суток
    × 20 причин строк вместо двадцати. Доля внутри корзины и доля от
    обработанных считаются `metrics._ratio`: свой знак деления здесь означал бы
    второе правило «что делать при нулевом знаменателе», а оно должно быть одно
    на раздел (пусто при нуле, а не ноль).
    """
    handled = (summary or {}).get('handled')
    buckets = {}
    totals = {}
    for row in reasons or ():
        bucket = row.get('bucket') or ''
        code = str(row.get('reason_code') or '')
        leads = _as_int(row.get('leads')) or 0
        key = (bucket, code)
        found = buckets.get(key)
        if found is None:
            buckets[key] = found = {
                'bucket': BUCKET_LABELS.get(bucket, bucket),
                'bucket_code': bucket,
                'reason_code': code,
                # Подпись из справочника, а если её нет — сырое написание
                # источника: в поле «Причина отказа» у Потока свободный текст, и
                # терять его нельзя, человек выбрал именно это.
                'reason_title': row.get('reason_title') or row.get('reason_raw') or code,
                'leads': 0,
            }
        found['leads'] += leads
        totals[bucket] = totals.get(bucket, 0) + leads

    rows = sorted(buckets.values(), key=lambda item: (item['bucket'], -item['leads'],
                                                      item['reason_title']))
    for row in rows:
        row['share_bucket'] = metrics._ratio(row['leads'], totals.get(row['bucket_code']))
        row['share_handled'] = metrics._ratio(row['leads'], handled)
    return rows


def _lead_values(lead):
    values = dict(lead)
    values['operator'] = _operator_name(lead)
    values['reach'] = REACH_LABELS.get(lead.get('reach_outcome') or '', '')
    values['dialog'] = DIALOG_LABELS.get(lead.get('dialog_outcome') or '', '')
    values['bucket'] = BUCKET_LABELS.get(lead.get('reason_bucket') or '', '')
    values['reason_title'] = lead.get('reason_title') or lead.get('reason_raw') or ''
    # Ноль в stream_type — это «у источника потоков нет» (он стоит у amoCRM и у
    # платного найма). Пустая ячейка, а не 0: иначе колонка читается как
    # «нулевой поток», которого не существует.
    values['stream_type'] = lead.get('stream_type') or None
    return values


# ── книга ────────────────────────────────────────────────────────────────────

def build_workbook(leads, *, direction, period_from, period_to, summary,
                   operators=(), days=(), reasons=(), targets=None, freshness=None,
                   hours_source='phone', compare=None, generated_at=None,
                   generated_by='', filters_note='', leads_total=None,
                   leads_limit=LEADS_LIMIT, text_warning_patch=None):
    """Собрать книгу. В базу не ходит: все данные приходят готовыми.

    `leads` — итерируемое лидов, можно генератор: лист «Лиды» пишется потоково.
    Остальные наборы — списки: операторов десятки, суток не больше года, и по ним
    нужен второй проход (какие колонки показывать и сколько строк в файле).

    `direction` — словарь `{code, title, streams}` в том же виде, в каком его
    отдаёт `/overview`; допустима и просто строка с кодом. `targets` — нормы на
    период, из них берутся пороги раскраски. `freshness` — состояние выгрузки
    (`last_run_at`, `status`, `unmapped`, `drift_rows`).

    Возвращает `(BytesIO, число строк листа «Лиды»)`.
    """
    generated_at = generated_at or datetime.now(ALMATY)
    if not isinstance(direction, dict):
        direction = {'code': str(direction or '')}
    code = str(direction.get('code') or '')
    title = direction.get('title') or DIRECTION_TITLES.get(code) or code or 'все направления'
    bounds = _thresholds(targets)
    summary = dict(summary or {})

    operator_rows = [_operator_values(row) for row in (operators or ())]
    day_rows = [_day_values(row) for row in (days or ())]
    reason_rows = _reason_values(reasons, summary)

    workbook = Workbook(write_only=True)
    # Порядок создания = порядок листов в книге и нумерация sheetN.xml.
    sheets = {name: workbook.create_sheet(name) for name in SHEET_TITLES}

    # «Лиды» первыми: обрезку и число строк надо знать до записи «Свода».
    written, truncated = _fill_leads(sheets[SHEET_LEADS], leads, leads_limit)

    keep = VERIFICATOR_COLUMNS if code == DIRECTION_VERIFICATOR else ()
    _fill_table(sheets[SHEET_OPERATORS], OPERATOR_COLUMNS, operator_rows, bounds,
                keep=keep, freeze='B2')
    _fill_table(sheets[SHEET_DAYS], DAY_COLUMNS, day_rows, bounds, keep=keep)
    _fill_table(sheets[SHEET_REASONS], REASON_COLUMNS, reason_rows, bounds)

    _fill_summary(sheets[SHEET_SUMMARY], code=code, title=title, streams=direction.get('streams'),
                  period_from=period_from, period_to=period_to, summary=summary,
                  operator_rows=operator_rows, day_rows=day_rows, reason_rows=reason_rows,
                  freshness=freshness or {}, hours_source=hours_source, compare=compare,
                  thresholds=bounds, generated_at=generated_at, generated_by=generated_by,
                  filters_note=filters_note, leads_written=written, truncated=truncated,
                  leads_total=leads_total, leads_limit=leads_limit)

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)

    if text_warning_patch and written:
        # sqref допускает несколько диапазонов через пробел — этим и перечисляем
        # разбросанные текстовые колонки.
        sqref = ' '.join(
            '{0}2:{0}{1}'.format(get_column_letter(_column_index(LEAD_COLUMNS, key)), written + 1)
            for key in LEAD_TEXT_COLUMNS)
        try:
            stream = text_warning_patch(stream, sqref, sheet_path=LEADS_SHEET_PATH)
        except Exception:
            # Значок в углу ячейки — досадно, но не повод не отдать файл.
            stream.seek(0)
    return stream, written


def report_filename(direction, period_from, period_to):
    """Имя файла — по ВЫБРАННОМУ периоду и направлению, а не по дате сборки.

    Две выгрузки, собранные в один день за разные периоды, иначе назывались бы
    одинаково, и вторая легла бы в загрузки как «Воронка ОП (1)». Дата сборки
    никуда не делась — она на «Своде».
    """
    if not isinstance(direction, dict):
        direction = {'code': str(direction or '')}
    code = str(direction.get('code') or '')
    title = direction.get('title') or DIRECTION_TITLES.get(code) or code
    head = 'Воронка ОП — %s' % title if title else 'Воронка ОП'
    left, right = _ru_date(period_from), _ru_date(period_to)
    if left == '—' and right == '—':
        return '%s.xlsx' % head
    if left == right:
        return '%s %s.xlsx' % (head, left)
    return '%s %s — %s.xlsx' % (head, left, right)


# ── листы ────────────────────────────────────────────────────────────────────

def _fill_table(sheet, columns, rows, thresholds, keep=(), freeze='A2'):
    """Табличный лист: шапка, ширины, закрепление, автофильтр.

    Закрепление у «По операторам» — 'B2', а не 'A2': колонок под тридцать, и без
    закреплённого первого столбца, дойдя до «% плана согласий», уже не видно,
    чья это строка.
    """
    visible = _visible_columns(columns, rows, keep=keep)
    _setup(sheet, visible, freeze=freeze)
    for row in rows:
        sheet.append([_cell(sheet, row.get(key), kind, thresholds)
                      for key, _title, _width, kind in visible])
    _autofilter(sheet, visible, len(rows))


def _fill_leads(sheet, leads, limit):
    """Лист «Лиды» потоково. Возвращает (сколько записано, сработал ли потолок).

    Набор колонок здесь постоянный: шапка и ширины уходят в файл до первой
    строки, а строки приходят генератором (см. шапку модуля). Потолок обрывает
    генератор недочитанным — это осознанно: дочитывать сотни тысяч строк ради
    точного «в базе их N» незачем, общее число приходит аргументом из COUNT.
    """
    _setup(sheet, LEAD_COLUMNS)
    written = 0
    truncated = False
    for lead in leads or ():
        if written >= limit:
            truncated = True
            break
        values = _lead_values(lead)
        sheet.append([_cell(sheet, values.get(key), kind)
                      for key, _title, _width, kind in LEAD_COLUMNS])
        written += 1
    _autofilter(sheet, LEAD_COLUMNS, written)
    return written, truncated


def _fill_summary(sheet, *, code, title, streams, period_from, period_to, summary,
                  operator_rows, day_rows, reason_rows, freshness, hours_source,
                  compare, thresholds, generated_at, generated_by, filters_note,
                  leads_written, truncated, leads_total, leads_limit):
    """«Свод»: рамка файла, итог команды, план, свежесть и оговорки.

    Заливки здесь нет намеренно — в колонке значений шириной 92 знака она
    становится полосой через пол-листа. Цвет живёт в таблицах, где ячейка узкая.
    """
    sheet.column_dimensions['A'].width = 46
    sheet.column_dimensions['B'].width = 92
    sheet.column_dimensions['C'].width = 14
    sheet.column_dimensions['D'].width = 14

    rates = metrics.derive_rates(summary)
    mapped = [row for row in operator_rows if (_as_int(row.get('user_id')) or 0) > 0]
    unmapped_row = next((row for row in operator_rows
                         if (_as_int(row.get('user_id')) or 0) == 0), None)
    is_verificator = code == DIRECTION_VERIFICATOR
    state = {'section': 0}

    def head(text):
        cell = _text(sheet, text)
        cell.font = TITLE_FONT
        sheet.append([cell])

    def section(text):
        state['section'] += 1
        cell = _text(sheet, '%d. %s' % (state['section'], text))
        cell.font = SECTION_FONT
        sheet.append([])
        sheet.append([cell])

    def row(label, value, kind='text'):
        cell = _cell(sheet, value, kind)
        cell.alignment = WRAP_TOP if kind in ('text', 'code') else VALUE_ALIGN
        sheet.append([_text(sheet, label), cell])

    def note(claim, explanation):
        right = _text(sheet, explanation)
        right.alignment = WRAP_TOP
        sheet.append([_text(sheet, claim), right])

    head('Воронка ОП — %s' % title)

    section('Главное')
    row('Период', period_label(period_from, period_to))
    days_count = _days_in_period(period_from, period_to)
    if days_count is not None:
        row('Суток в периоде', days_count, 'int')
    row('Направление', title)
    if streams:
        row('Потоки', ', '.join(str(item) for item in streams))
    row('Источник', DIRECTION_SOURCES.get(code, '—'))
    row('Собрано', generated_at.strftime('%d.%m.%Y %H:%M') + ' (Алматы)')
    row('Собрал', generated_by or '—')
    row('Отобрано дополнительно', filters_note or 'ничего — весь период целиком')
    row('Операторов в отчёте', len(mapped), 'int')
    if unmapped_row is not None:
        # Отдельной строкой, а не сноской: пока лиды лежат на несопоставленном
        # операторе, таблица «По операторам» недосчитывает именно на столько.
        row('Обработано лидов без сопоставленного оператора',
            unmapped_row.get('handled'), 'int')
    row('Отработано часов', summary.get('work_hours'), 'hours')
    row('Часы взяты', HOURS_SOURCE_NOTES.get(hours_source, hours_source or '—'))
    if truncated:
        # Предупреждение стоит ДО счётчиков, а не после: счётчики ниже посчитаны
        # по всему периоду, а лист «Лиды» — обрезан, и прочитать одно как другое
        # — первое, что сделает человек.
        row('В ЛИСТ «ЛИДЫ» ПОПАЛО НЕ ВСЁ',
            'По этому отбору лидов %s, а на лист помещается не больше %d. '
            'Итоги ниже посчитаны по всему периоду и обрезкой не затронуты, но '
            'построчная расшифровка неполная: сузьте период и выгрузите частями.'
            % (_as_int(leads_total) if leads_total is not None else 'больше',
               leads_limit))
    row('Строк на листе «Лиды»', leads_written, 'int')

    section('Воронка команды')
    row('Обработано лидов', summary.get('handled'), 'int')
    row('Дозвон', summary.get('reached'), 'int')
    row('% дозвона (от обработанных)', rates.get('reach_rate'), 'rate')
    row('Недозвон', summary.get('not_reached'), 'int')
    row('% дозвона от попыток', rates.get('attempt_rate'), 'rate')
    row('Согласия', summary.get('agreed'), 'int')
    row('% согласий от дозвона', rates.get('agree_rate'), 'rate')
    row('Успехи', summary.get('succeeded'), 'int')
    row('% успеха от согласий', rates.get('success_rate'), 'rate')
    row('Отказы', summary.get('rejected'), 'int')
    row('Нецелевые', summary.get('untargeted'), 'int')
    row('Перезвоны назначены', summary.get('callbacks'), 'int')
    if summary.get('moved'):
        row('Увели в другой процесс', summary.get('moved'), 'int')
    if summary.get('inbound'):
        row('Входящая линия', summary.get('inbound'), 'int')
    row('Лидов в час', rates.get('leads_per_hour'), 'num1')
    if is_verificator or summary.get('chats') or summary.get('tickets'):
        row('Чаты', summary.get('chats'), 'int')
        row('Тикеты', summary.get('tickets'), 'int')
        row('Чатов и тикетов в час', rates.get('chats_per_hour'), 'num1')
        row('Среднее время ответа в чате, с', summary.get('chat_reply_seconds'), 'num1')
        row('Среднее время обработки тикета, с', summary.get('ticket_handle_seconds'), 'num1')
        row('Качество', summary.get('quality_score'), 'num1')

    section('План')
    row('План дозвонов', summary.get('plan_reached'), 'num1')
    row('% выполнения по дозвонам', rates.get('plan_reached_rate'), 'rate')
    row('План согласий', summary.get('plan_agreed'), 'num1')
    row('% выполнения по согласиям', rates.get('plan_agreed_rate'), 'rate')

    if compare:
        section('Сравнение с предыдущим периодом той же длины')
        header = []
        for caption in ('Показатель', 'Сейчас', 'Было', 'Разница'):
            cell = _text(sheet, caption)
            cell.font = SECTION_FONT
            header.append(cell)
        sheet.append(header)
        for metric in metrics.COMPARABLE_METRICS:
            item = compare.get(metric)
            if not item:
                continue
            kind = 'rate' if metric.endswith('_rate') else 'num1'
            left = _cell(sheet, item.get('now'), kind)
            left.alignment = VALUE_ALIGN
            sheet.append([_text(sheet, COMPARE_LABELS.get(metric, metric)), left,
                          _cell(sheet, item.get('was'), kind),
                          _cell(sheet, item.get('delta'), kind)])

    section('Свежесть данных')
    row('Последняя выгрузка', _ru_moment(freshness.get('last_run_at')))
    row('Как закончилась', RUN_STATUS_LABELS.get(freshness.get('status') or '',
                                                 freshness.get('status') or '—'))
    row('Операторов не сопоставлено', _as_int(freshness.get('unmapped')) or 0, 'int')
    row('Строк расхождений за период', _as_int(freshness.get('drift_rows')) or 0, 'int')
    row('Строк в таблицах', '%d операторов, %d суток, %d причин'
        % (len(operator_rows), len(day_rows), len(reason_rows)))

    green, amber = thresholds
    section('Что важно знать про эти цифры')
    note('Это снимок на дату сборки, а не вечная цифра.',
         'СРМ переписывает прошлое: 11.09.2026 сверка суток 01.09 дала тот же состав лидов '
         '(1125 против 1125) и другие исходы — Дозвон 499 против 534, Согласия 257 против 267. '
         'В файле стоят зафиксированные суточные итоги: то, что видели в день выгрузки.')
    note('«% дозвона» считается от ОБРАБОТАННЫХ лидов.',
         'Один знаменатель на все четыре направления (решение владельца 11.09.2026). Раньше в '
         '«Яндекс Регистрации» делили на дозвон + недозвон, а в «Потоке» — на все лиды, и цифры '
         'команд были несравнимы. Теперь показатель честно падает, когда до части базы оператор '
         'не дошёл. Второй знаменатель оставлен отдельной строкой — «% дозвона от попыток».')
    note('«Обработано» у направлений считается по-разному.',
         'У «Потока» и «Яндекс Регистрации» это дозвон + недозвон: ручка отдаёт всю базу, включая '
         'лиды «Новый», до которых никто не дошёл. У «Основы ОП» это число строк выгрузки amoCRM — '
         'туда попадает только то, что оператор трогал.')
    note('Успех входит в согласия.',
         'Человек, который согласился и вышел на линию, посчитан и там, и там: иначе «% согласий от '
         'дозвона» провалился бы у лучших операторов.')
    note('«Увели в другой процесс» — не отказ.',
         'В amoCRM причиной закрытия помечают и переход в другую воронку («Диалоги», «YaPROREG», '
         '«Дожим приглашенные»). Сводные листы супервайзера их не считают: на 01.09 у одного '
         'оператора иначе выходило 14 отказов вместо 7.')
    note('Строка «%s» — это лиды, чей оператор ещё не привязан к сотруднику.' % UNMAPPED_LABEL,
         'Ни один источник не отдаёт id сотрудника, а имена в СРМ и в портале не совпадают '
         '(«Кузембаева Аяулым» против «Кузембековой Аяулым»), поэтому связь подтверждает человек. '
         'Строка оставлена в таблице намеренно: без неё сумма по операторам не сходится с итогом '
         'команды, и это выглядит как ошибка расчёта, а не как пробел в сопоставлении.')
    note('Часы взяты %s.' % HOURS_SOURCE_NOTES.get(hours_source, hours_source or '—'),
         'У Верификаторов статусов на телефоне нет вовсе: за 01–11.09.2026 записи в учёте часов у '
         'группы 13 есть, а часы в них нулевые (проверено на проде). Для таких направлений часы '
         'берутся по графику смен, и у каждой строки написано, откуда они.')
    note('Цветом помечен только процент выполнения плана.',
         'Три тона: зелёный — с %d %% плана, жёлтый — с %d %%, красный — ниже. Пустая ячейка не '
         'красится: нет данных — это не «плохо». Остальные колонки не красятся вовсе — цвет, '
         'стоящий везде, ничего не выделяет.' % (round(green * 100), round(amber * 100)))
    note('Проценты лежат долей единицы.',
         'В ячейке 0,508 с процентным форматом, а не число 50,8: по доле работают и среднее по '
         'колонке, и диаграмма. Так же их отдаёт и API раздела.')
    note('Телефон и ключ лида лежат текстом.',
         'Числом номер теряет ведущий ноль и уезжает в экспоненту, а 32-значный ключ Excel '
         'округлит. Подсказка «Число сохранено как текст» на этих колонках погашена.')
    note('Все даты и времена — Алматы (UTC+5).',
         'Источники отдают местное время, и мы его не сдвигаем: на сдвиге в проекте уже горели — '
         'отчёт Chat2Desk уехал на +5 часов.')
    if _as_int(freshness.get('drift_rows')):
        note('Часть уже закрытых суток пересчитывалась.',
             'Расхождения не спрятаны: каждая изменившаяся метрика записана в журнал расхождений, '
             'и его видно в разделе на вкладке прогонов. Число строк — выше.')


# Подписи сравниваемых показателей. Ключи — ровно metrics.COMPARABLE_METRICS,
# порядок берётся оттуда же: блок сравнения не должен разъезжаться со списком,
# по которому его считают.
COMPARE_LABELS = {
    'handled': 'Обработано лидов',
    'reached': 'Дозвон',
    'agreed': 'Согласия',
    'succeeded': 'Успехи',
    'rejected': 'Отказы',
    'untargeted': 'Нецелевые',
    'work_hours': 'Часы',
    'reach_rate': '% дозвона',
    'agree_rate': '% согласий',
    'success_rate': '% успеха',
    'leads_per_hour': 'Лидов в час',
    'plan_reached_rate': '% плана по дозвонам',
}

# Статусы прогона выгрузки (op_funnel_sync_runs.status).
RUN_STATUS_LABELS = {
    'ok': 'успешно',
    'error': 'с ошибкой — цифры могли не обновиться',
    'running': 'ещё идёт',
}
