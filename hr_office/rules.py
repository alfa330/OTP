# -*- coding: utf-8 -*-
"""Правила разделов ТЗ HR «iCore 4» («Прием», табель, АВР): без базы и сети — всё проверяется тестами.

Здесь живут: виды оформления и подписи статусов по ним, обозначения табеля,
проекты, проверки личных данных, суммы прописью, должность в родительном
падеже для АВР, даты по-русски и номера документов.
"""

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# ── Виды оформления ─────────────────────────────────────────────────────────
# Те же коды, что в карточке «Учета сотрудников» (users.employment_type).
EMPLOYMENT_TYPES = ('gph', 'of', 'smz')
EMPLOYMENT_SHORT = {'gph': 'ГПХ', 'of': 'ТД', 'smz': 'СМЗ'}
EMPLOYMENT_TITLES = {'gph': 'Договор ГПХ', 'of': 'Трудовой договор', 'smz': 'Самозанятый'}


# ── Пилот «Кадров» ──────────────────────────────────────────────────────────
# Решение владельца 10.11.2026: «один единый раздел … на данный момент опубликовать только для
# суперадминов для тестирования» и кадровику отдела HR. Пока пилот идёт, раздел «Кадры» и всё
# новое из ТЗ HR «iCore 4» в общих окнах (данные для документов при создании учётки, подписи
# статусов по виду оформления) видят только суперадмины и учётки списка (id прода, без ФИО:
# репозиторий публичный). None — пилот закончен: доступ по ролям ТЗ, новое в общих окнах — всем.
PILOT_USER_IDS = frozenset({456})


def pilot_allows(user_id, role):
    """Видит ли учётка «Кадры» и новое из ТЗ, пока идёт пилот."""
    if PILOT_USER_IDS is None:
        return True
    if str(role or '').strip().lower() == 'super_admin':
        return True
    try:
        return int(user_id) in PILOT_USER_IDS
    except (TypeError, ValueError):
        return False


def clean_employment_type(value):
    text = str(value or '').strip().lower()
    aliases = {'гпх': 'gph', 'тд': 'of', 'оф': 'of', 'смз': 'smz', 'td': 'of'}
    text = aliases.get(text, text)
    return text if text in EMPLOYMENT_TYPES else None


# ── Статусы (ТЗ п. 5) ───────────────────────────────────────────────────────
# Коды графика НЕ новые (решение по разбору 10.11.2026): annual_leave, bs и
# sick_leave давно живут в графиках, заявках, отчётах и ночной синхронизации.
# Меняется только подпись — по виду оформления человека, и только на экранах HR.
STATUS_WORKING = 'working'
STATUS_FIRED = 'fired'
PERIOD_STATUSES = ('annual_leave', 'bs', 'sick_leave')

_STATUS_LABELS = {
    'of': {'annual_leave': 'Трудовой отпуск', 'bs': 'Отпуск без содержания', 'sick_leave': 'Больничный'},
    'gph': {'annual_leave': 'Оплачиваемый отпуск', 'bs': 'Приостановление договора', 'sick_leave': 'Больничный'},
    'smz': {'annual_leave': 'Оплачиваемый отпуск', 'bs': 'Приостановление договора', 'sick_leave': 'Больничный'},
}
# Вид не указан: подпись без привязки к договору.
_STATUS_NEUTRAL = {'annual_leave': 'Отпуск', 'bs': 'Б/С', 'sick_leave': 'Больничный'}
_STATUS_COMMON = {'working': 'Работает', 'fired': 'Уволен', 'dismissal': 'Увольнение'}

# Какие статусы ТЗ даёт каждому виду (таблица п. 8: «Б» — только ОФ).
_STATUS_BY_TYPE = {
    'of': ('annual_leave', 'bs', 'sick_leave'),
    'gph': ('annual_leave', 'bs'),
    # ТЗ п. 8: у СМЗ из статусов — только «Приостановление договора» («А»).
    'smz': ('bs',),
}


def status_label(code, employment_type=None):
    code = str(code or '').strip().lower()
    if code == 'unpaid_leave':
        code = 'bs'
    if code in _STATUS_COMMON:
        return _STATUS_COMMON[code]
    kind = clean_employment_type(employment_type)
    if kind and code in _STATUS_LABELS[kind]:
        return _STATUS_LABELS[kind][code]
    return _STATUS_NEUTRAL.get(code, code)


def status_options(employment_type=None, current=None):
    """Пункты выбора статуса: «Работает» первым, дальше — что положено виду.

    Текущий статус человека в списке остаётся всегда, даже если виду он не
    положен (больничный у ГПХ, поставленный до ТЗ), — иначе выбор показал бы
    «Работает» у человека на больничном."""
    kind = clean_employment_type(employment_type)
    codes = list(_STATUS_BY_TYPE.get(kind, PERIOD_STATUSES))
    current = 'bs' if current == 'unpaid_leave' else current
    if current in PERIOD_STATUSES and current not in codes:
        codes.append(current)
    return [{'value': STATUS_WORKING, 'label': status_label(STATUS_WORKING)}] + [
        {'value': code, 'label': status_label(code, kind)} for code in codes]


# ── Обозначения табеля (ТЗ п. 8) ────────────────────────────────────────────
CODE_DAY_OFF = 'В'
CODE_LEAVE = 'О'
CODE_UNPAID = 'А'
CODE_SICK = 'Б'
PERIOD_CODES = {'annual_leave': CODE_LEAVE, 'bs': CODE_UNPAID, 'unpaid_leave': CODE_UNPAID, 'sick_leave': CODE_SICK}
ABSENCE_CODES = (CODE_LEAVE, CODE_UNPAID, CODE_SICK)
LETTER_CODES = (CODE_DAY_OFF,) + ABSENCE_CODES

LEGEND = (
    ('8', 'Работает — часы по графику (8/6/10…)'),
    (CODE_DAY_OFF, 'Выходной или праздник'),
    (CODE_LEAVE, 'Отпуск: трудовой (ТД) или оплачиваемый (ГПХ)'),
    (CODE_UNPAID, 'Отпуск без содержания (ТД) или приостановление договора (ГПХ, СМЗ)'),
    (CODE_SICK, 'Больничный'),
)


def clean_cell_value(value):
    """Ручная правка ячейки → (значение, ошибка). Число часов, буква или пусто."""
    text = str(value if value is not None else '').strip().upper().replace(',', '.')
    if text == '':
        return '', None
    if text in ('B',):  # латинская B вместо кириллической В
        text = CODE_DAY_OFF
    if text in ('O',):
        text = CODE_LEAVE
    if text in ('A',):
        text = CODE_UNPAID
    if text in LETTER_CODES:
        return text, None
    try:
        hours = Decimal(text)
    except InvalidOperation:
        return None, 'В ячейке — часы числом или буква В, О, А, Б'
    if hours < 0 or hours > 24:
        return None, 'Часов в дне — от 0 до 24'
    return _plain_number(hours), None


def clean_minutes_value(value):
    """Правка строки под ячейкой: минуты (со знаком) или короткий текст («в офисе»)."""
    text = str(value if value is not None else '').strip()
    if text == '':
        return None, None
    if re.fullmatch(r'[+-]?\d{1,4}', text):
        return int(text), None
    if len(text) > 40:
        return None, 'Не длиннее 40 знаков'
    return text, None


def _plain_number(value):
    value = Decimal(value).normalize()
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def number_or_none(value):
    try:
        return float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        return None


# ── Проекты (ТЗ п. 8) ───────────────────────────────────────────────────────
# Проект — не отдел портала (разбор 10.11.2026): региональный супервайзер числится
# во фронт-офисах, а в табеле он в ЦО. Поэтому проект — поле человека; пока HR его
# не задал, берётся по отделу.
PROJECTS = ('ЦО', 'iTaxi Rent', 'Eazy Go', 'Eazy Coffee', 'Регионы', 'TEZ', 'Колл-центр')
_DEPARTMENT_PROJECT = {
    'front_office': 'Регионы',
    'car_rental': 'iTaxi Rent',
    'eazy_go': 'Eazy Go',
    'eazy_coffee': 'Eazy Coffee',
    'tez_project': 'TEZ',
    'szov': 'Колл-центр',
    'op': 'Колл-центр',
    'tez': 'Колл-центр',
    'remote_cc': 'Колл-центр',
}
_PROJECT_ALIASES = {
    'цо': 'ЦО', 'центральный офис': 'ЦО', 'itaxi rent': 'iTaxi Rent', 'аренда авто': 'iTaxi Rent',
    'eazy go': 'Eazy Go', 'eazy go алматы': 'Eazy Go', 'eazy coffee': 'Eazy Coffee', 'регионы': 'Регионы',
    'tez': 'TEZ', 'колл-центр': 'Колл-центр', 'колл центр': 'Колл-центр', 'кц': 'Колл-центр',
}


def clean_project(value):
    text = ' '.join(str(value or '').split())
    if not text:
        return None
    return _PROJECT_ALIASES.get(text.casefold()) or (text if text in PROJECTS else None)


def default_project(department_code):
    """Проект по отделу. Без отдела — без проекта: служебные учётки не попадают в ЦО."""
    code = str(department_code or '').strip().lower()
    if not code:
        return None
    return _DEPARTMENT_PROJECT.get(code, 'ЦО')


def project_of(person):
    return clean_project(person.get('project')) or default_project(person.get('department_code'))


# ── Оформление по умолчанию (ТЗ п. 3) ───────────────────────────────────────
# Только для нового оформления: у кого компания уже записана, не переписываем.
DEFAULT_HIRING = {
    'szov': ('gph', 'eazy go rent'),
    'op': ('gph', 'eazy go rent'),
    'tez': ('gph', 'gaba'),
}
# Должность по умолчанию у линейных отделов: в карточке их поле «Должность» скрыто.
DEFAULT_JOB_TITLES = {
    'szov': 'Оператор службы заботы о водителях',
    'op': 'Менеджер по привлечению водителей',
    'tez': 'Оператор колл-центра',
    'remote_cc': 'Оператор колл-центра',
    'front_office': 'Менеджер фронт офиса',
}


# Чьё это умолчание — подписью для карточки и списка «Приема».
DEFAULT_HIRING_FOR = {'szov': 'ОП и СЗоВ', 'op': 'ОП и СЗоВ', 'tez': 'операторов TEZ'}


def default_hiring(department_code):
    return DEFAULT_HIRING.get(str(department_code or '').strip().lower())


def default_job_title(department_code):
    return DEFAULT_JOB_TITLES.get(str(department_code or '').strip().lower(), '')


_COMPANY_FORMS = re.compile(r'^(ип|тоо|too|ао|ооо|ип\.|тоо\.)\s+', re.IGNORECASE)


def company_key(name):
    """«ИП "Тестова"», «ИП ТЕСТОВА», «ИП Тестова» → «тестова»: так 18 написаний одной
    компании из карточек находят свою строку справочника."""
    text = str(name or '').replace('«', ' ').replace('»', ' ').replace('"', ' ').replace("'", ' ')
    text = ' '.join(text.split()).casefold()
    text = text.replace('ё', 'е')
    previous = None
    while previous != text:
        previous = text
        text = _COMPANY_FORMS.sub('', text).strip()
    return text


def company_kind_label(kind):
    return {'ip': 'ИП', 'too': 'ТОО'}.get(str(kind or '').strip().lower(), '')


def company_id_label(kind):
    """ИП работает по ИИН владельца, ТОО — по БИН."""
    return 'ИИН' if str(kind or '').strip().lower() == 'ip' else 'БИН'


def company_display_name(company):
    name = ' '.join(str(company.get('name') or '').split())
    if not name:
        return ''
    prefix = company_kind_label(company.get('kind'))
    if prefix and not name.casefold().startswith(prefix.casefold()):
        return f'{prefix} «{name}»'
    return name


# ── Личные данные ───────────────────────────────────────────────────────────
DOC_TYPES = ('id_card', 'passport')
DOC_TYPE_LABELS = {'id_card': 'Удостоверение личности', 'passport': 'Паспорт'}


def validate_iin(raw, birth_date=None, gender=None):
    """(ИИН, ошибка, предупреждение). Проверка — общая с «Ссылками на подписание»."""
    from sign_links import iin as iin_rules
    text = str(raw or '').strip()
    if not text:
        return None, None, None
    value, code = iin_rules.validate(text)
    if code:
        return None, iin_rules.error_message(code), None
    warning = None
    if birth_date:
        expected = birth_date.strftime('%y%m%d')
        if value[:6] != expected:
            warning = 'Первые 6 цифр ИИН не совпадают с датой рождения в карточке'
    if gender in ('male', 'female') and value[6] in '123456':
        is_male = int(value[6]) % 2 == 1
        if is_male != (gender == 'male') and not warning:
            warning = 'Седьмая цифра ИИН указывает другой пол, чем в карточке'
    return value, None, warning


def name_parts(full_name):
    """«Фамилия Имя Отчество» → (фамилия, имя, отчество). Отчество может быть из двух слов
    («Серікқызы», «оглы»)."""
    parts = str(full_name or '').split()
    if not parts:
        return '', '', ''
    if len(parts) == 1:
        return parts[0], '', ''
    return parts[0], parts[1], ' '.join(parts[2:])


def short_name(full_name):
    """«Иванова Анна Сергеевна» → «Иванова А.С.»."""
    last, first, middle = name_parts(full_name)
    initials = ''.join(f'{part[0]}.' for part in (first, middle.split()[0] if middle else '') if part)
    return f'{last} {initials}'.strip()


def gender_word(gender, male, female, unknown=None):
    if gender == 'male':
        return male
    if gender == 'female':
        return female
    return unknown if unknown is not None else f'{male}(-ая)'


# ── Деньги ──────────────────────────────────────────────────────────────────

def clean_amount(value):
    """Сумма вознаграждения → Decimal с копейками или None; ошибка текстом."""
    text = str(value if value is not None else '').strip().replace(' ', '').replace(' ', '').replace(',', '.')
    if not text:
        return None, None
    try:
        amount = Decimal(text).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None, 'Сумма — числом, например 250000'
    if amount < 0 or amount > Decimal('999999999'):
        return None, 'Сумма вне разумных пределов'
    return amount, None


def money_text(amount):
    """250000 → «250 000»; 250000.5 → «250 000,50»."""
    if amount is None:
        return ''
    value = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    whole = int(value)
    cents = int((value - whole) * 100)
    text = f'{whole:,}'.replace(',', ' ')
    return f'{text},{cents:02d}' if cents else text


_UNITS_M = ('', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять')
_UNITS_F = ('', 'одна', 'две', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять')
_TEENS = ('десять', 'одиннадцать', 'двенадцать', 'тринадцать', 'четырнадцать', 'пятнадцать',
          'шестнадцать', 'семнадцать', 'восемнадцать', 'девятнадцать')
_TENS = ('', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят', 'семьдесят', 'восемьдесят',
         'девяносто')
_HUNDREDS = ('', 'сто', 'двести', 'триста', 'четыреста', 'пятьсот', 'шестьсот', 'семьсот', 'восемьсот',
             'девятьсот')
_SCALES = (
    (('', '', ''), False),
    (('тысяча', 'тысячи', 'тысяч'), True),
    (('миллион', 'миллиона', 'миллионов'), False),
    (('миллиард', 'миллиарда', 'миллиардов'), False),
)


def plural_form(number, forms):
    number = abs(int(number))
    if number % 10 == 1 and number % 100 != 11:
        return forms[0]
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return forms[1]
    return forms[2]


def _triad_words(number, feminine):
    words = []
    hundreds, rest = divmod(number, 100)
    if hundreds:
        words.append(_HUNDREDS[hundreds])
    if 10 <= rest <= 19:
        words.append(_TEENS[rest - 10])
    else:
        tens, units = divmod(rest, 10)
        if tens:
            words.append(_TENS[tens])
        if units:
            words.append((_UNITS_F if feminine else _UNITS_M)[units])
    return words


def number_words(number):
    """Целое → слова: 250000 → «двести пятьдесят тысяч», 0 → «ноль»."""
    number = int(number)
    if number == 0:
        return 'ноль'
    if number < 0:
        return 'минус ' + number_words(-number)
    words, scale = [], 0
    while number:
        number, triad = divmod(number, 1000)
        if triad:
            forms, feminine = _SCALES[scale]
            part = _triad_words(triad, feminine)
            if scale:
                part.append(plural_form(triad, forms))
            words = part + words
        scale += 1
    return ' '.join(words)


def amount_words(amount):
    """Сумма прописью для договора: «двести пятьдесят тысяч» (+ «50 тиын», если есть)."""
    if amount is None:
        return ''
    value = Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    whole = int(value)
    cents = int((value - whole) * 100)
    text = number_words(whole)
    if cents:
        text += f' {cents:02d} тиын'
    return text


# ── Должность в родительном падеже: «Услуги HR менеджера» (ТЗ п. 12) ──────────
_ADJ_ENDINGS = (('ый', 'ого'), ('ой', 'ого'), ('ий', 'его'))
# Прилагательные на -ий с твёрдой основой: «главный» уже на -ый, а «младший» —
# -его, «ведущий» — -его; «региональный» — -ого. Исключений для -ий нет.
_NOUN_EXCEPTIONS = {
    'техничка': 'технички',
    'уборщица': 'уборщицы',
    'няня': 'няни',
}


def _is_cyrillic(word):
    return bool(re.search(r'[а-яё]', word, re.IGNORECASE))


def _noun_genitive(word):
    lower = word.lower()
    if lower in _NOUN_EXCEPTIONS:
        result = _NOUN_EXCEPTIONS[lower]
    elif lower.endswith('ь'):
        result = lower[:-1] + 'я'
    elif lower.endswith('й'):
        result = lower[:-1] + 'я'
    elif lower.endswith('а'):
        result = lower[:-1] + ('и' if lower[-2:-1] in 'гкхжчшщ' else 'ы')
    elif lower.endswith('я'):
        result = lower[:-1] + 'и'
    elif lower.endswith('о') or lower.endswith('е'):
        result = lower
    else:
        result = lower + 'а'
    return _same_case(word, result)


def _same_case(original, result):
    if original.isupper() and len(original) > 1:
        return result.upper()
    if original[:1].isupper():
        return result[:1].upper() + result[1:]
    return result


def job_genitive(title):
    """«Менеджер по аренде» → «менеджера по аренде»; «Младший финансовый аналитик» →
    «младшего финансового аналитика»; «HR менеджер» → «HR менеджера».

    Склоняются прилагательные до первого существительного и само
    существительное; латиница («HR», «PHP», «Junior») и всё после
    существительного («по аренде», «отдела продаж») остаются как есть. Итог в
    АВР можно поправить руками — эвристика не обязана знать все должности."""
    title = ' '.join(str(title or '').split())
    # «МЕНЕДЖЕР ПО КЛИНИНГУ» из таблицы HR — не аббревиатура, а набор капслоком.
    if title.isupper() and len(title.replace(' ', '')) > 6:
        title = title.lower()
    words = title.split()
    out, done = [], False
    for word in words:
        acronym = word.isupper() and len(word) > 1
        if done or acronym or not _is_cyrillic(word):
            out.append(word)
            continue
        lower = word.lower()
        adjective = next((pair for pair in _ADJ_ENDINGS if lower.endswith(pair[0]) and len(lower) > 3), None)
        if adjective:
            out.append(_same_case(word, lower[: -len(adjective[0])] + adjective[1]))
            continue
        if '-' in word:
            # «Маркетолог-контекстолог» склоняется в обеих частях.
            out.append('-'.join(_noun_genitive(part) for part in word.split('-')))
        else:
            out.append(_noun_genitive(word))
        done = True
    if out:
        # В середине фразы должность пишется со строчной; латиница и аббревиатуры — как есть.
        first = out[0]
        if _is_cyrillic(first) and not (first.isupper() and len(first) > 1):
            out[0] = first[:1].lower() + first[1:]
    return ' '.join(out)


def act_work_name(job_title):
    title = job_genitive(job_title)
    return f'Услуги {title}'.strip() if title else 'Услуги'


# ── Даты ────────────────────────────────────────────────────────────────────
MONTHS_GENITIVE = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
                   'сентября', 'октября', 'ноября', 'декабря')
MONTHS_NOMINATIVE = ('январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль', 'август',
                     'сентябрь', 'октябрь', 'ноябрь', 'декабрь')
WEEKDAYS_UPPER = ('ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ', 'ВС')

MONTH_RE = re.compile(r'^(\d{4})-(0[1-9]|1[0-2])$')


def parse_month(value):
    match = MONTH_RE.match(str(value or '').strip())
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def parse_day(value):
    try:
        return date.fromisoformat(str(value or '').strip()[:10])
    except ValueError:
        return None


def date_long(value):
    """«10» ноября 2026 г."""
    if not value:
        return '«__» ____________ ____ г.'
    return f'«{value.day:02d}» {MONTHS_GENITIVE[value.month - 1]} {value.year} г.'


def date_dots(value):
    return value.strftime('%d.%m.%Y') if value else ''


def month_title(month_start):
    return f'{MONTHS_NOMINATIVE[month_start.month - 1].capitalize()} {month_start.year}'


def month_days(start, end):
    out, cursor = [], start
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(days=1)
    return out


def default_act_period(today):
    """ТЗ п. 12: по умолчанию — с первого числа месяца по текущую дату."""
    return today.replace(day=1), today


def previous_month_period(today):
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def act_period_text(start, end):
    if not start or not end:
        return ''
    first, last = parse_month(f'{start.year}-{start.month:02d}')
    if start == first and end == last:
        return f'{MONTHS_NOMINATIVE[start.month - 1]} {start.year} г.'
    return f'{date_dots(start)} – {date_dots(end)}'


# ── Номера документов ───────────────────────────────────────────────────────

def contract_number(sequence, year):
    """Номер договора в справочнике компании: «15/2026». Предлагается, HR может
    вписать свой (например, номер бумажного договора)."""
    return f'{int(sequence)}/{int(year)}'


def act_number(sequence, year):
    return f'АВР-{int(sequence)}/{int(year)}'


# ── График табеля ───────────────────────────────────────────────────────────
SCHEDULE_PATTERNS = ('5/2', '6/1', '2/2')
DEFAULT_DAY_HOURS = {'5/2': 8, '6/1': 8, '2/2': 10}


def clean_schedule(value):
    text = str(value or '').strip().replace('.', '/').replace(' ', '')
    return text if text in SCHEDULE_PATTERNS else None


def default_day_hours(pattern):
    return DEFAULT_DAY_HOURS.get(clean_schedule(pattern) or '5/2', 8)


def calendar_workday(day, pattern, holidays):
    """Рабочий ли день у календарного графика. 2/2 календарём не считается — None."""
    pattern = clean_schedule(pattern) or '5/2'
    if pattern == '2/2':
        return None
    if day in holidays:
        return False
    last_workday = 4 if pattern == '5/2' else 5
    return day.weekday() <= last_workday
