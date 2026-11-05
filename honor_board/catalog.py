# -*- coding: utf-8 -*-
"""Справочник «Доски почёта» (задача #318) и правила её карточек.

Три направления по ТЗ — ОП, СЗоВ и Фронт-офис. У ОП и СЗоВ номинации заданы
списком ТЗ. У Фронт-офиса ТЗ оставило их тренеру: «2–4 профильные номинации,
включая ключевую — «Лучший сотрудник отдела»». Поэтому там закреплена одна
ключевая номинация, а остальные (до трёх) тренер называет сам.

Карточка победителя на доске — по одной на номинацию: кто (сотрудник отдела,
его фото из профиля), имя и фамилия так, как их напишет тренер, и описание
заслуг. «Лучший сотрудник отдела» — главная номинация: доска ставит её первой
и выделяет.

Тот же справочник — во фронте (src/components/events/honorBoardMeta.js);
tests/test_honor_board.py сверяет их буквально, чтобы доска не назвала
номинацию иначе, чем её принял сервер.

Здесь нет ни Flask, ни базы — только правила: их проверяют тесты без стенда.
"""
import re
from datetime import date

MAIN_NOMINATION = 'best_employee'
# Номинация, которую тренер Фронт-офиса называет сам: ключ один на все такие,
# название живёт в самой записи.
CUSTOM_NOMINATION = 'custom'

MAIN_TITLE = 'Лучший сотрудник отдела'
MAIN_REASON = 'по итогам месяца, за лучшие показатели по всем ключевым метрикам'

# key — department_key в honor_boards; code — departments.code: из этого отдела
# окно тренера предлагает победителей. Номинации — (ключ, название, за что) в
# порядке доски: главная первой.
DEPARTMENTS = (
    {
        'key': 'op', 'code': 'op', 'label': 'ОП', 'title': 'Отдел продаж',
        'custom': False,
        'nominations': (
            (MAIN_NOMINATION, MAIN_TITLE, MAIN_REASON),
            ('guru_sales', 'Гуру продаж', 'за высокие результаты и профессионализм в продажах'),
            ('master_yr', 'Мастер ЯР', 'за лучшие показатели в группе Яндекс Регистрации'),
            ('best_verifier', 'Лучший верификатор', 'за наибольшее количество регистраций за месяц'),
        ),
    },
    {
        'key': 'szov', 'code': 'szov', 'label': 'СЗоВ', 'title': 'Служба заботы о водителях',
        'custom': False,
        'nominations': (
            (MAIN_NOMINATION, MAIN_TITLE, MAIN_REASON),
            ('productivity_champion', 'Чемпион по продуктивности',
             'за высокие результаты и стабильную работу'),
            ('first_class_manager', 'Первоклассный менеджер',
             'за качество обслуживания и вовлечённость'),
            ('chat_star', 'Звезда чат-направления',
             'за эффективность и внимательность в переписке с водителями'),
        ),
    },
    {
        'key': 'front_office', 'code': 'front_office', 'label': 'Фронт-офис', 'title': 'Фронт-офис',
        'custom': True,
        'nominations': (
            (MAIN_NOMINATION, MAIN_TITLE, MAIN_REASON),
        ),
    },
)

DEPARTMENT_KEYS = tuple(item['key'] for item in DEPARTMENTS)
_BY_KEY = {item['key']: item for item in DEPARTMENTS}

# «2–4 профильные номинации, включая ключевую» — потолок вместе с ключевой.
CUSTOM_MAX_NOMINATIONS = 4

# Пределы полей. Имя и фамилия — строка карточки; название своей номинации —
# подпись бейджа; описание — «краткое описание заслуг» (по ТЗ — хоть стихами):
# восемь строк стиха по сорок знаков — 320, шестьсот хватает с запасом, а
# длиннее карточка в ряду с соседними вытягивается в столбик.
NAME_MAX = 80
TITLE_MAX = 60
DESCRIPTION_MAX = 600
# Записей у доски не бывает больше номинаций; запрос с сотней строк — ошибка.
ENTRIES_MAX = 8

# С какого месяца можно подводить итоги и сколько месяцев назад предлагает окно.
FIRST_PERIOD = date(2025, 1, 1)
SELECTABLE_MONTHS = 24

MONTHS_NOMINATIVE = (
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)

_PERIOD_RE = re.compile(r'^(\d{4})-(\d{2})$')
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_SPACES_RE = re.compile(r'\s+')


def department(key):
    """Направление доски по ключу или None."""
    return _BY_KEY.get(str(key or '').strip())


def nominations(key):
    return department(key)['nominations'] if department(key) else ()


# ── Период ──────────────────────────────────────────────────────────────

def month_start(value):
    return date(value.year, value.month, 1)


def shift_month(month, delta):
    index = month.year * 12 + (month.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def period_key(month):
    return f'{month.year:04d}-{month.month:02d}'


def period_label(month):
    return f'{MONTHS_NOMINATIVE[month.month - 1]} {month.year}'


def parse_period(raw, today):
    """'2026-10' -> date(2026, 10, 1) или None.

    None — не тот вид, месяц раньше FIRST_PERIOD или ещё не закончившийся:
    итоги подводят «по итогам прошедшего месяца» (ТЗ, п. 1), а текущий месяц
    первым в списке окна легко выбрать по ошибке и показать всем незаконченные
    итоги.
    """
    match = _PERIOD_RE.match(str(raw or '').strip())
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    value = date(year, month, 1)
    if value < FIRST_PERIOD or value > last_closed_period(today):
        return None
    return value


def last_closed_period(today):
    """Последний закончившийся месяц."""
    return shift_month(month_start(today), -1)


def default_period(today):
    """Итоги подводят «по итогам прошедшего месяца» — его окно и открывает."""
    return max(FIRST_PERIOD, last_closed_period(today))


def selectable_periods(today):
    """Месяцы окна тренера, от прошедшего назад: [date, …]."""
    last = last_closed_period(today)
    result = []
    for step in range(SELECTABLE_MONTHS):
        value = shift_month(last, -step)
        if value < FIRST_PERIOD:
            break
        result.append(value)
    return result


# ── Записи доски ────────────────────────────────────────────────────────

class EntriesError(ValueError):
    """Записи прислали в неверном виде — 400 с этим текстом."""


def clean_line(value):
    """Одна строка: управляющие символы и переводы строк — в пробел, пробелы схлопнуты."""
    text = _CONTROL_RE.sub(' ', str(value or ''))
    return _SPACES_RE.sub(' ', text).strip()


def clean_text(value):
    """Описание заслуг: переносы строк сохраняются (стих — по строкам), но
    хвостовые пробелы срезаны, а подряд не больше одной пустой строки."""
    text = str(value or '').replace('\r\n', '\n').replace('\r', '\n').replace('\t', ' ')
    text = _CONTROL_RE.sub('', text)
    lines = [line.rstrip() for line in text.split('\n')]
    kept = []
    for line in lines:
        if not line and kept and not kept[-1]:
            continue
        kept.append(line)
    return '\n'.join(kept).strip('\n').strip()


def _user_id(value):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise EntriesError('Сотрудник указан неверно')
    text = str(value).strip()
    if not (text.isascii() and text.isdigit()) or len(text) > 9 or int(text) <= 0:
        raise EntriesError('Сотрудник указан неверно')
    return int(text)


def text_field(raw, key):
    """Текстовое поле записи: строка или пусто. Число или объект — ошибка, а не
    str() от него: иначе в черновик молча легло бы «{'a': 1}»."""
    value = raw.get(key) if isinstance(raw, dict) else None
    if value is None:
        return ''
    if not isinstance(value, str):
        raise EntriesError('Записи доски переданы неверно')
    return value


def _entry(nomination, title, raw):
    raw = raw if isinstance(raw, dict) else {}
    name = clean_line(text_field(raw, 'name'))
    description = clean_text(text_field(raw, 'description'))
    if len(name) > NAME_MAX:
        raise EntriesError(f'Имя и фамилия — не длиннее {NAME_MAX} знаков')
    if len(description) > DESCRIPTION_MAX:
        raise EntriesError(f'Описание заслуг — не длиннее {DESCRIPTION_MAX} знаков')
    if len(title) > TITLE_MAX:
        raise EntriesError(f'Название номинации — не длиннее {TITLE_MAX} знаков')
    return {
        'nomination': nomination,
        'title': title,
        'user_id': _user_id(raw.get('user_id')),
        'name': name,
        'description': description,
    }


def blank_entry(nomination, title=''):
    return {'nomination': nomination, 'title': title, 'user_id': None, 'name': '', 'description': ''}


def blank_slots(department_key, custom_titles=()):
    """Пустая доска: по строке на каждую номинацию списка. У Фронт-офиса —
    ключевая и свои номинации прошлого месяца без победителей, чтобы тренер не
    набирал их каждый месяц заново; прошлого раза нет — одна пустая своя:
    «2–4 номинации» начинаются с двух."""
    item = department(department_key)
    if not item:
        return []
    slots = [blank_entry(code, title) for code, title, _reason in item['nominations']]
    if item['custom']:
        titles = [clean_line(title)[:TITLE_MAX] for title in custom_titles or ()]
        titles = [title for title in titles if title][:CUSTOM_MAX_NOMINATIONS - len(slots)]
        slots.extend(blank_entry(CUSTOM_NOMINATION, title) for title in (titles or ['']))
    return slots


def normalize_entries(department_key, raw_entries):
    """Записи из запроса -> строки доски в порядке номинаций.

    У ОП и СЗоВ строк ровно столько, сколько номинаций в списке (пустая —
    номинация без победителя); у Фронт-офиса — ключевая первой, за ней свои
    в порядке тренера. Незаполненная строка — не ошибка: черновик сохраняется
    по ходу ввода, а полноту проверяет публикация (publish_issues).
    """
    item = department(department_key)
    if not item:
        raise EntriesError('Неизвестное направление доски')
    if not isinstance(raw_entries, list):
        raise EntriesError('Записи доски переданы неверно')
    if len(raw_entries) > ENTRIES_MAX:
        raise EntriesError('Слишком много записей')
    fixed = {code: title for code, title, _reason in item['nominations']}
    by_code = {}
    custom = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise EntriesError('Записи доски переданы неверно')
        code = str(raw.get('nomination') or '').strip()
        if code in fixed:
            if code in by_code:
                raise EntriesError('Номинация повторяется')
            by_code[code] = _entry(code, fixed[code], raw)
        elif code == CUSTOM_NOMINATION and item['custom']:
            custom.append(_entry(code, clean_line(text_field(raw, 'title')), raw))
        else:
            raise EntriesError('Неизвестная номинация')
    if item['custom'] and len(fixed) + len(custom) > CUSTOM_MAX_NOMINATIONS:
        raise EntriesError(f'Номинаций — не больше {CUSTOM_MAX_NOMINATIONS}, вместе с ключевой')
    slots = [by_code.get(code) or blank_entry(code, title) for code, title, _reason in item['nominations']]
    return slots + custom


def restore_slots(department_key, stored):
    """Сохранённые записи -> строки доски по нынешнему справочнику.

    Мягче normalize_entries: запись, которую справочник уже не знает (номинацию
    убрали из списка) или которая не проходит нынешние пределы, пропускается,
    а не роняет окно тренера.
    """
    item = department(department_key)
    if not item:
        return []
    fixed = {code: title for code, title, _reason in item['nominations']}
    by_code = {}
    custom = []
    for raw in stored if isinstance(stored, list) else []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get('nomination') or '').strip()
        try:
            if code in fixed and code not in by_code:
                by_code[code] = _entry(code, fixed[code], raw)
            elif (code == CUSTOM_NOMINATION and item['custom']
                  and len(fixed) + len(custom) < CUSTOM_MAX_NOMINATIONS):
                custom.append(_entry(code, clean_line(text_field(raw, 'title')), raw))
        except EntriesError:
            continue
    slots = [by_code.get(code) or blank_entry(code, title) for code, title, _reason in item['nominations']]
    return slots + custom


def revert_slots(department_key, published, draft):
    """«Отменить правки»: черновик снова равен опубликованному.

    Опубликованный снимок хранит только заполненные строки, а своя номинация
    Фронт-офиса без победителя живёт лишь в черновике — откат по одному снимку
    молча стёр бы её название. Такие строки возвращаются пустыми, на доску они
    по-прежнему не выходят.
    """
    slots = restore_slots(department_key, published)
    item = department(department_key)
    if not item or not item['custom']:
        return slots
    present = {entry['title'] for entry in slots if entry['nomination'] == CUSTOM_NOMINATION}
    for entry in restore_slots(department_key, draft):
        if len(slots) >= CUSTOM_MAX_NOMINATIONS:
            break
        if (entry['nomination'] == CUSTOM_NOMINATION and entry['title']
                and not entry_has_content(entry) and entry['title'] not in present):
            slots.append(blank_entry(CUSTOM_NOMINATION, entry['title']))
            present.add(entry['title'])
    return slots


def entry_has_content(entry):
    """Строка, в которой тренер что-то начал. Название своей номинации само
    по себе не в счёт: оно остаётся с прошлого месяца, и номинация без
    победителя в этом месяце просто не выходит на доску."""
    return bool(entry.get('user_id') or entry.get('name') or entry.get('description'))


def publish_issues(entries):
    """Чего не хватает для публикации: [{index, field, message}].

    field — поле окна тренера (user, name, description, title); index — строка
    доски. Пустые строки не мешают: номинация без победителя на доску не выйдет.
    """
    issues = []
    filled = 0
    for index, entry in enumerate(entries):
        if not entry_has_content(entry):
            continue
        filled += 1
        if entry['nomination'] == CUSTOM_NOMINATION and not entry['title']:
            issues.append({'index': index, 'field': 'title', 'message': 'Назовите номинацию'})
        if not entry['user_id']:
            issues.append({'index': index, 'field': 'user', 'message': 'Выберите сотрудника'})
        if not entry['name']:
            issues.append({'index': index, 'field': 'name', 'message': 'Впишите имя и фамилию'})
        if not entry['description']:
            issues.append({'index': index, 'field': 'description', 'message': 'Опишите заслуги'})
    if not filled:
        issues.append({'index': None, 'field': None, 'message': 'Выберите хотя бы одного победителя'})
    return issues


def published_snapshot(entries):
    """То, что увидят сотрудники: заполненные строки в порядке доски."""
    return [
        {
            'nomination': entry['nomination'],
            'title': entry['title'],
            'user_id': entry['user_id'],
            'name': entry['name'],
            'description': entry['description'],
        }
        for entry in entries
        if entry_has_content(entry)
    ]
