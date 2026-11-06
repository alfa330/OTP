# -*- coding: utf-8 -*-
"""Проекты-доски раздела «Задачи» (задача #401).

ЗАЧЕМ. До сих пор «отдел задачи» нигде не хранился: его выводили из отдела
того, кто задачу поставил. Отделу этого мало — у IT, например, три разные
доски (Helpdesk, Backend, iCore), а задачу HR-отделу ставят люди из любого
отдела. Проект — явная доска со своим ключом на латинице: задача получает
номер внутри проекта, и по ключу HR-125 сразу видно, чья она.

КТО ЧТО ВИДИТ. Состав доски — `task_project_members`, два уровня:
  member — участник проекта: видит все задачи проекта;
  board  — доступ к доске: ставит задачи в проект и видит на доске свои
           задачи и те, где его добавили наблюдателем (`task_watchers`).
Видеть — не значит действовать. Брать в работу, сдавать и принимать по-прежнему
решают исполнители и поручитель: участник и наблюдатель читают задачу так же,
как СВ-коллега (задача #298).

РАЗДЕЛЫ. «Регулируемый набор разделов» из постановки — у каждой доски свои
группы задач (`task_project_sections`: «Подбор», «Адаптация» у HR). Колонки доски
при этом остаются статусами: на них держатся приёмка и уведомления. Раздел — это
«о чём задача» внутри доски, а не «на каком она шаге»; у задачи он один или его нет.

НОМЕР. Глобальный `tasks.id` остаётся: на него ссылаются Telegram, коммиты, CLI
и старые ссылки. Ключ — это `task_projects.code` + `tasks.project_number`.
Перенесённая в другой проект задача получает новый номер, а старый ключ
остаётся в `task_key_aliases` и по-прежнему открывает её по ссылке. Ключ доски
можно сменить в её настройках (06.11.2026): номера остаются, прежние ключи всех
её задач уходят туда же, а бывший чужой ключ новой доске не отдаётся.

Модуль — лист: re и ничего больше. Правила гоняет tests/test_task_projects.py,
то же правило ключа во фронте — src/components/tasks/taskProjects.js.
"""

import re

# Уровни доступа к доске. Порядок — от широкого к узкому.
ACCESS_MEMBER = 'member'
ACCESS_BOARD = 'board'
ACCESS_LEVELS = (ACCESS_MEMBER, ACCESS_BOARD)

# Ключ проекта: латиница и цифры, первой — буква, от 2 до 10 знаков. Тот же
# шаблон стоит CHECK'ом на task_projects.code.
CODE_PATTERN = r'^[A-Z][A-Z0-9]{1,9}$'
CODE_MAX_LENGTH = 10
NAME_MAX_LENGTH = 120
_CODE_RE = re.compile(CODE_PATTERN)
# Ключ задачи целиком: HR-125. Регистр букв в ссылке не важен.
_TASK_KEY_RE = re.compile(r'^\s*#?([A-Za-z][A-Za-z0-9]{1,9})-(\d{1,9})\s*$')

# Наблюдателей у задачи не больше двадцати: это список «кому показать», а не
# рассылка на отдел — отдел целиком видит задачу участником проекта.
MAX_WATCHERS = 20
# Состав одной доски. Потолок — страховка от вставки «всех сотрудников разом».
MAX_MEMBERS = 300
# Разделы доски: дольше тридцати список перестаёт быть навигацией.
MAX_SECTIONS = 30
SECTION_NAME_MAX_LENGTH = 80

# Проекты из постановки #401. Заводятся один раз, на пустой таблице (см.
# database.py): дальше список ведут в разделе, и переименованный или
# отправленный в архив проект при следующем старте приложения не меняется.
# Третье поле — код отдела, к которому проект привязан. Привязка нужна только
# чтобы подставить доску по умолчанию; отдела с таким кодом может и не быть —
# тогда проект просто без отдела.
SEED_PROJECTS = (
    ('HR', 'HR отдел', 'hr'),
    ('FIN', 'Фин отдел (аналитики)', 'analytik'),
    ('ACC', 'Бухгалтерия', 'accounting'),
    ('ITH', 'IT Helpdesk', 'it'),
    ('BE', 'IT разработка — Backend', 'it'),
    ('IC', 'IT разработка — iCore', 'it'),
    ('SZOV', 'СЗоВ — служба заботы о водителях', 'szov'),
    ('SALES', 'ОП — отдел продаж', 'op'),
    ('MKT', 'Отдел маркетинга', 'marketing'),
)

# Коды ошибок слоя данных → текст для человека. Форма раздела проверяет всё
# сама, поэтому эти тексты видит только тот, кто прошёл мимо неё.
ERROR_MESSAGES = {
    'PROJECT_NOT_FOUND': 'Проект не найден',
    'PROJECT_ARCHIVED': 'Проект в архиве — задачи в него больше не ставят',
    'PROJECT_FORBIDDEN': 'У вас нет доступа к этому проекту',
    'PROJECT_MANAGE_FORBIDDEN': 'Проекты настраивают только администраторы',
    'INVALID_PROJECT': 'Неверный проект',
    'INVALID_PROJECT_CODE': 'Ключ проекта — от 2 до 10 латинских букв и цифр, первой буквой',
    'PROJECT_CODE_TAKEN': 'Проект с таким ключом уже есть',
    'PROJECT_CODE_USED_BEFORE': 'Этим ключом раньше назывались задачи другой доски — старые ссылки ведут к ним. Возьмите другой ключ',
    'PROJECT_NAME_REQUIRED': 'Укажите название проекта',
    'INVALID_PROJECT_MEMBERS': 'Неверный состав проекта',
    'TOO_MANY_PROJECT_MEMBERS': 'В проекте не больше %d человек' % MAX_MEMBERS,
    'INVALID_WATCHERS': 'Неверный список наблюдателей',
    'TOO_MANY_WATCHERS': 'Наблюдателей у задачи не больше %d' % MAX_WATCHERS,
    'WATCHERS_NEED_PROJECT': 'Наблюдатели есть только у задач проекта — сначала выберите проект',
    'WATCHER_NO_PROJECT_ACCESS': 'Наблюдателем можно добавить только того, у кого есть доступ к доске проекта',
    'INVALID_DEPARTMENT': 'Неверный отдел',
    'INVALID_PROJECT_SECTIONS': 'Неверный список разделов',
    'TOO_MANY_PROJECT_SECTIONS': 'Разделов у доски не больше %d' % MAX_SECTIONS,
    'PROJECT_SECTION_NAME_REQUIRED': 'У раздела должно быть название',
    'PROJECT_SECTION_DUPLICATE': 'Два раздела с одним названием',
    'INVALID_SECTION': 'Неверный раздел',
    'SECTION_NOT_IN_PROJECT': 'Этот раздел — с другой доски',
}


def error_message(code):
    """Текст ошибки для человека или None, если код не про проекты."""
    return ERROR_MESSAGES.get(str(code or ''))


def normalize_code(value):
    """Ключ проекта в каноническом виде (заглавные) или ValueError."""
    code = str(value or '').strip().upper()
    if not _CODE_RE.match(code):
        raise ValueError('INVALID_PROJECT_CODE')
    return code


def normalize_name(value):
    name = ' '.join(str(value or '').split())
    if not name:
        raise ValueError('PROJECT_NAME_REQUIRED')
    return name[:NAME_MAX_LENGTH]


def normalize_access_level(value):
    level = str(value or ACCESS_MEMBER).strip().lower() or ACCESS_MEMBER
    if level not in ACCESS_LEVELS:
        raise ValueError('INVALID_PROJECT_MEMBERS')
    return level


def format_task_key(code, number):
    """HR-125 или None, если задача не в проекте."""
    if not code or number is None:
        return None
    try:
        number = int(number)
    except (TypeError, ValueError):
        return None
    return '%s-%d' % (str(code).upper(), number) if number > 0 else None


def parse_task_key(value):
    """('HR', 125) из «HR-125» / «hr-125» / «#HR-125», иначе None."""
    match = _TASK_KEY_RE.match(str(value or ''))
    if not match:
        return None
    number = int(match.group(2))
    if number <= 0:
        return None
    return match.group(1).upper(), number


def normalize_user_ids(values, limit, error_code, too_many_code):
    """Список id людей из того, что прислал клиент: порядок сохраняем, повторы сворачиваем.

    Понимает список и строку «12,15,7» (так он приезжает в multipart-форме
    создания задачи). Пустой список — законное значение «никого».
    """
    if values is None:
        return []
    items = values
    if isinstance(items, bytes):
        items = items.decode('utf-8', 'ignore')
    if isinstance(items, str):
        items = items.split(',')
    elif isinstance(items, (int, float)):
        items = [items]
    elif not isinstance(items, (list, tuple, set, frozenset)):
        raise ValueError(error_code)
    ordered = []
    seen = set()
    for item in items:
        if item is None or (isinstance(item, str) and not item.strip()):
            continue
        try:
            person_id = int(str(item).strip() if isinstance(item, str) else item)
        except (TypeError, ValueError):
            raise ValueError(error_code)
        if person_id <= 0:
            raise ValueError(error_code)
        if person_id in seen:
            continue
        seen.add(person_id)
        ordered.append(person_id)
    if len(ordered) > limit:
        raise ValueError(too_many_code)
    return ordered


def normalize_members(values):
    """Состав доски: [{'user_id', 'access_level'}] → [(user_id, level)] без повторов.

    Один человек — один уровень: прислали дважды, побеждает последняя строка
    (так ведёт себя список в окне настройки, где уровень переключают на месте).
    """
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError('INVALID_PROJECT_MEMBERS')
    levels = {}
    order = []
    for item in values:
        if isinstance(item, dict):
            raw_id = item.get('user_id', item.get('id'))
            level = normalize_access_level(item.get('access_level'))
        else:
            raw_id = item
            level = ACCESS_MEMBER
        try:
            person_id = int(raw_id)
        except (TypeError, ValueError):
            raise ValueError('INVALID_PROJECT_MEMBERS')
        if person_id <= 0:
            raise ValueError('INVALID_PROJECT_MEMBERS')
        if person_id not in levels:
            order.append(person_id)
        levels[person_id] = level
    if len(order) > MAX_MEMBERS:
        raise ValueError('TOO_MANY_PROJECT_MEMBERS')
    return [(person_id, levels[person_id]) for person_id in order]


def normalize_sections(values):
    """Разделы доски по порядку: [{'id'?, 'name'}] → [(id|None, name)].

    id — у раздела, который уже есть (переименование и порядок), None — у нового.
    Названия без повторов без учёта регистра: «Подбор» и «подбор» на одной доске
    различить нельзя.
    """
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError('INVALID_PROJECT_SECTIONS')
    if len(values) > MAX_SECTIONS:
        raise ValueError('TOO_MANY_PROJECT_SECTIONS')
    ordered = []
    seen_names = set()
    seen_ids = set()
    for item in values:
        if isinstance(item, dict):
            raw_id = item.get('id')
            raw_name = item.get('name')
        else:
            raw_id, raw_name = None, item
        name = ' '.join(str(raw_name or '').split())[:SECTION_NAME_MAX_LENGTH]
        if not name:
            raise ValueError('PROJECT_SECTION_NAME_REQUIRED')
        key = name.casefold()
        if key in seen_names:
            raise ValueError('PROJECT_SECTION_DUPLICATE')
        seen_names.add(key)
        section_id = None
        if raw_id not in (None, ''):
            try:
                section_id = int(raw_id)
            except (TypeError, ValueError):
                raise ValueError('INVALID_PROJECT_SECTIONS')
            if section_id <= 0 or section_id in seen_ids:
                raise ValueError('INVALID_PROJECT_SECTIONS')
            seen_ids.add(section_id)
        ordered.append((section_id, name))
    return ordered
