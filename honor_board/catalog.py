# -*- coding: utf-8 -*-
"""Справочник «Доски почёта» (задача #318) и правила её карточек.

Три направления по ТЗ — ОП, СЗоВ и Фронт-офис. У каждого закреплена одна
ключевая номинация — «Лучший сотрудник отдела»: доска ставит её первой и
выделяет. Остальные номинации тренер называет сам (вместе с ключевой — до
MAX_NOMINATIONS): сначала так было только у Фронт-офиса, с 06.11.2026 — у всех
трёх («менять названия номинаций ОП и СЗоВ как у фронт-офиса и добавлять ещё,
если нужно побольше»). Номинации ОП и СЗоВ из ТЗ остались заготовкой
(presets): ими открывается первая доска направления, дальше каждый месяц
берёт названия своего прошлого месяца.

Карточка победителя на доске — по одной на номинацию: кто (сотрудник отдела),
имя и фамилия так, как их напишет тренер, описание заслуг, значок номинации и
фото для доски (photo_id — снимок, который загрузил тренер; без него карточка
берёт фото из профиля).

Доски, заполненные до 06.11.2026, хранят номинации ОП и СЗоВ своими кодами
(guru_sales, …) — LEGACY_NOMINATIONS читает их как свои номинации с тем же
названием и значком, ничего в базе не переписывая.

Тот же справочник — во фронте (src/components/events/honorBoardMeta.js);
tests/test_honor_board.py сверяет их буквально, чтобы доска не назвала
номинацию иначе, чем её принял сервер.

Здесь нет ни Flask, ни базы — только правила: их проверяют тесты без стенда.
"""
import re
from datetime import date

MAIN_NOMINATION = 'best_employee'
# Номинация, которую тренер называет сам: ключ один на все такие, название и
# значок живут в самой записи.
CUSTOM_NOMINATION = 'custom'

MAIN_TITLE = 'Лучший сотрудник отдела'
MAIN_REASON = 'по итогам месяца, за лучшие показатели по всем ключевым метрикам'
# Корона — только у ключевой номинации (ТЗ: «специальная плашка/корона»).
MAIN_ICON = 'Crown'
DEFAULT_ICON = 'Award'

# Значки своих номинаций — имена значков lucide, фронт подставляет компоненты.
# Список закрытый: в запись не попадёт произвольная строка.
ICONS = (
    'Award', 'Star', 'Trophy', 'Medal', 'Rocket', 'Target', 'Zap', 'TrendingUp',
    'Flame', 'Gem', 'Sparkles', 'HeartHandshake', 'MessageCircleHeart', 'Headphones', 'BadgeCheck', 'Car',
)

# key — department_key в honor_boards; code — departments.code: из этого отдела
# окно тренера предлагает победителей. presets — номинации ТЗ, которыми
# открывается первая доска направления: (прежний код, название, значок, за что).
DEPARTMENTS = (
    {
        'key': 'op', 'code': 'op', 'label': 'ОП', 'title': 'Отдел продаж',
        'presets': (
            ('guru_sales', 'Гуру продаж', 'TrendingUp', 'за высокие результаты и профессионализм в продажах'),
            ('master_yr', 'Мастер ЯР', 'Car', 'за лучшие показатели в группе Яндекс Регистрации'),
            ('best_verifier', 'Лучший верификатор', 'BadgeCheck', 'за наибольшее количество регистраций за месяц'),
        ),
    },
    {
        'key': 'szov', 'code': 'szov', 'label': 'СЗоВ', 'title': 'Служба заботы о водителях',
        'presets': (
            ('productivity_champion', 'Чемпион по продуктивности', 'Zap',
             'за высокие результаты и стабильную работу'),
            ('first_class_manager', 'Первоклассный менеджер', 'HeartHandshake',
             'за качество обслуживания и вовлечённость'),
            ('chat_star', 'Звезда чат-направления', 'MessageCircleHeart',
             'за эффективность и внимательность в переписке с водителями'),
        ),
    },
    {
        'key': 'front_office', 'code': 'front_office', 'label': 'Фронт-офис', 'title': 'Фронт-офис',
        'presets': (),
    },
)

DEPARTMENT_KEYS = tuple(item['key'] for item in DEPARTMENTS)
_BY_KEY = {item['key']: item for item in DEPARTMENTS}

# Коды номинаций ОП и СЗоВ, которыми хранились доски до 06.11.2026:
# код -> (название, значок).
LEGACY_NOMINATIONS = {
    code: (title, icon)
    for item in DEPARTMENTS
    for code, title, icon, _reason in item['presets']
}

# Номинаций на доске вместе с ключевой. ТЗ просило у Фронт-офиса «2–4»; у ОП
# и СЗоВ их было ровно четыре, и попросили «добавлять ещё, если нужно
# побольше» — восемь карточек доска показывает двумя рядами.
MAX_NOMINATIONS = 8

# Пределы полей. Имя и фамилия — строка карточки; название своей номинации —
# подпись бейджа; описание — «краткое описание заслуг» (по ТЗ — хоть стихами):
# восемь строк стиха по сорок знаков — 320, шестьсот хватает с запасом, а
# длиннее карточка в ряду с соседними вытягивается в столбик.
NAME_MAX = 80
TITLE_MAX = 60
DESCRIPTION_MAX = 600
# Записей у доски не бывает больше номинаций; запрос с сотней строк — ошибка.
ENTRIES_MAX = MAX_NOMINATIONS

# С какого месяца можно подводить итоги и сколько месяцев назад предлагает окно.
FIRST_PERIOD = date(2025, 1, 1)
SELECTABLE_MONTHS = 24

MONTHS_NOMINATIVE = (
    'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
)
# «Доска почёта: итоги октября» — строка колокола.
MONTHS_GENITIVE = (
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
)

_PERIOD_RE = re.compile(r'^(\d{4})-(\d{2})$')
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_SPACES_RE = re.compile(r'\s+')


def department(key):
    """Направление доски по ключу или None."""
    return _BY_KEY.get(str(key or '').strip())


def presets(key):
    """Номинации ТЗ направления: [(название, значок)] — заготовка первой доски."""
    item = department(key)
    return [(title, icon) for _code, title, icon, _reason in item['presets']] if item else []


def nomination_reason(department_key, nomination, title):
    """«За что» дают номинацию — для подсказки у бейджа. У ключевой — из ТЗ,
    у своей — только если её название совпадает с номинацией ТЗ этого
    направления: переименованную номинацию тренер описывает сам."""
    if nomination == MAIN_NOMINATION:
        return MAIN_REASON
    item = department(department_key)
    for _code, preset_title, _icon, reason in (item['presets'] if item else ()):
        if preset_title == title:
            return reason
    return ''


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


def period_genitive(month):
    """date(2026, 10, 1) -> «октября»: «Доска почёта: итоги октября»."""
    return MONTHS_GENITIVE[month.month - 1]


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


def _positive_id(value, message):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise EntriesError(message)
    text = str(value).strip()
    if not (text.isascii() and text.isdigit()) or len(text) > 9 or int(text) <= 0:
        raise EntriesError(message)
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


def clean_icon(value, fallback=DEFAULT_ICON):
    """Значок своей номинации: из списка ICONS, иначе — fallback.

    Не ошибка: значок — оформление, и окно старой сборки (без выбора значка)
    не должно получать отказ на каждое сохранение черновика."""
    text = str(value or '').strip() if isinstance(value, str) else ''
    return text if text in ICONS else fallback


def _entry(nomination, title, icon, raw):
    raw = raw if isinstance(raw, dict) else {}
    name = clean_line(text_field(raw, 'name'))
    description = clean_text(text_field(raw, 'description'))
    if len(name) > NAME_MAX:
        raise EntriesError(f'Имя и фамилия — не длиннее {NAME_MAX} знаков')
    if len(description) > DESCRIPTION_MAX:
        raise EntriesError(f'Описание заслуг — не длиннее {DESCRIPTION_MAX} знаков')
    if len(title) > TITLE_MAX:
        raise EntriesError(f'Название номинации — не длиннее {TITLE_MAX} знаков')
    user_id = _positive_id(raw.get('user_id'), 'Сотрудник указан неверно')
    photo_id = _positive_id(raw.get('photo_id'), 'Фото указано неверно')
    return {
        'nomination': nomination,
        'title': title,
        'icon': icon,
        'user_id': user_id,
        'name': name,
        'description': description,
        # Фото для доски — снимок конкретного человека: без него строки фото нет.
        'photo_id': photo_id if user_id else None,
    }


def blank_entry(nomination, title='', icon=None):
    if icon is None:
        icon = MAIN_ICON if nomination == MAIN_NOMINATION else DEFAULT_ICON
    return {'nomination': nomination, 'title': title, 'icon': icon, 'user_id': None, 'name': '',
            'description': '', 'photo_id': None}


def _main_entry(raw):
    return _entry(MAIN_NOMINATION, MAIN_TITLE, MAIN_ICON, raw)


def _custom_entry(raw):
    """Своя номинация из записи. Прежний код ОП/СЗоВ (guru_sales, …) — та же
    своя номинация: название из записи, а нет его — из ТЗ; значок — из ТЗ,
    если в записи своего нет."""
    code = str(raw.get('nomination') or '').strip()
    legacy_title, legacy_icon = LEGACY_NOMINATIONS.get(code, ('', DEFAULT_ICON))
    title = clean_line(text_field(raw, 'title')) or legacy_title
    return _entry(CUSTOM_NOMINATION, title, clean_icon(raw.get('icon'), legacy_icon), raw)


def _is_custom_code(code):
    return code == CUSTOM_NOMINATION or code in LEGACY_NOMINATIONS


def blank_slots(department_key, previous=()):
    """Пустая доска: ключевая и свои номинации без победителей.

    previous — [(название, значок)] своих номинаций прошлого месяца: тренер не
    набирает их каждый месяц заново. Прошлого раза нет — номинации ТЗ
    направления, а у Фронт-офиса (в ТЗ их нет) — одна пустая своя: «2–4
    номинации» начинаются с двух."""
    if not department(department_key):
        return []
    slots = [blank_entry(MAIN_NOMINATION, MAIN_TITLE)]
    named = []
    for item in previous or ():
        title, icon = (item, DEFAULT_ICON) if isinstance(item, str) else (tuple(item) + (DEFAULT_ICON,))[:2]
        title = clean_line(title)[:TITLE_MAX]
        if title:
            named.append(blank_entry(CUSTOM_NOMINATION, title, clean_icon(icon)))
    if not named:
        named = [blank_entry(CUSTOM_NOMINATION, title, icon) for title, icon in presets(department_key)]
    if not named:
        named = [blank_entry(CUSTOM_NOMINATION)]
    return slots + named[:MAX_NOMINATIONS - len(slots)]


def normalize_entries(department_key, raw_entries):
    """Записи из запроса -> строки доски: ключевая первой, за ней свои в
    порядке тренера.

    Незаполненная строка — не ошибка: черновик сохраняется по ходу ввода, а
    полноту проверяет публикация (publish_issues).
    """
    if not department(department_key):
        raise EntriesError('Неизвестное направление доски')
    if not isinstance(raw_entries, list):
        raise EntriesError('Записи доски переданы неверно')
    if len(raw_entries) > ENTRIES_MAX:
        raise EntriesError('Слишком много записей')
    main = None
    custom = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise EntriesError('Записи доски переданы неверно')
        code = str(raw.get('nomination') or '').strip()
        if code == MAIN_NOMINATION:
            if main is not None:
                raise EntriesError('Номинация повторяется')
            main = _main_entry(raw)
        elif _is_custom_code(code):
            custom.append(_custom_entry(raw))
        else:
            raise EntriesError('Неизвестная номинация')
    if 1 + len(custom) > MAX_NOMINATIONS:
        raise EntriesError(f'Номинаций — не больше {MAX_NOMINATIONS}, вместе с ключевой')
    return [main or blank_entry(MAIN_NOMINATION, MAIN_TITLE)] + custom


def restore_slots(department_key, stored):
    """Сохранённые записи -> строки доски по нынешнему справочнику.

    Мягче normalize_entries: запись, которую справочник не знает, или которая
    не проходит нынешние пределы, пропускается, а не роняет окно тренера.
    Ничего не сохранено (None) — пустая доска с номинациями ТЗ.
    """
    if not department(department_key):
        return []
    if stored is None:
        return blank_slots(department_key)
    main = None
    custom = []
    for raw in stored if isinstance(stored, list) else []:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get('nomination') or '').strip()
        try:
            if code == MAIN_NOMINATION and main is None:
                main = _main_entry(raw)
            elif _is_custom_code(code) and 1 + len(custom) < MAX_NOMINATIONS:
                custom.append(_custom_entry(raw))
        except EntriesError:
            continue
    return [main or blank_entry(MAIN_NOMINATION, MAIN_TITLE)] + custom


def revert_slots(department_key, published, draft):
    """«Отменить правки»: черновик снова равен опубликованному.

    Опубликованный снимок хранит только заполненные строки, а своя номинация
    без победителя живёт лишь в черновике — откат по одному снимку молча стёр
    бы её название. Такие строки возвращаются пустыми, на доску они
    по-прежнему не выходят.
    """
    slots = restore_slots(department_key, published or [])
    if not slots:
        return slots
    present = {entry['title'] for entry in slots if entry['nomination'] == CUSTOM_NOMINATION}
    for entry in restore_slots(department_key, draft or []):
        if len(slots) >= MAX_NOMINATIONS:
            break
        if (entry['nomination'] == CUSTOM_NOMINATION and entry['title']
                and not entry_has_content(entry) and entry['title'] not in present):
            slots.append(blank_entry(CUSTOM_NOMINATION, entry['title'], entry['icon']))
            present.add(entry['title'])
    return slots


def custom_nominations(entries):
    """Свои номинации доски: [(название, значок)] — заготовка следующего месяца."""
    return [
        (entry['title'], entry['icon'])
        for entry in entries
        if entry['nomination'] == CUSTOM_NOMINATION and entry['title']
    ]


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


SNAPSHOT_FIELDS = ('nomination', 'title', 'icon', 'user_id', 'name', 'description', 'photo_id')


def published_snapshot(entries):
    """То, что увидят сотрудники: заполненные строки в порядке доски."""
    return [{key: entry[key] for key in SNAPSHOT_FIELDS} for entry in entries if entry_has_content(entry)]
