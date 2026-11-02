"""Разнос класса Database (database.py) на миксины по разделам: db/<раздел>.py.

    python scripts/split_database_mixins.py --report    # распределение, без записи
    python scripts/split_database_mixins.py --write     # записать db/*.py и фасад database.py

Механика, не ручная правка:
  * модульный уровень database.py (импорты, константы, помощники) → db/core.py байт в байт;
  * методы Database → db/<раздел>.py по RULES (первое совпадение по имени выигрывает),
    вместе с комментариями над ними, по номерам строк узлов AST;
  * методы, читающие или перепривязывающие POOL / POOL_SEMAPHORE, → CoreMixin в db/core.py:
    эти глобалы живут там, а импорт имени в другой модуль дал бы устаревшую копию None;
  * database.py → фасад: реэкспорт имён db.core (код и тесты пишут
    `from database import db, IT_TICKET_CATALOG`), class Database(миксины) с прежними
    атрибутами класса и __init__, затем прежний хвост (db = Database(), схема call_qa).

Единственная правка внутри тел методов: `Database._x(` → `self._x(` (статические помощники
низких оценок) — в миксине имени Database нет. Всё остальное переносится как есть.
Тесты, разбиравшие database.py целиком, читают склейку: tests/source_cache.py.
"""
import argparse
import ast
import os
import re
import sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, 'database.py')
PARTS_DIR = os.path.join(ROOT, 'db')
REBOUND = {'POOL', 'POOL_SEMAPHORE'}
STAMP = 'вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026'

# Точечные исключения: имя попадает под правило выше по списку, но принадлежит другому разделу.
# Проверяются раньше RULES; порядок миксинов в фасаде по-прежнему задают RULES. «norm» из правила
# графиков забирал помощники низких оценок чатов — и ChatRatingsMixin._normalize_low_rating_review_status
# вёл в никуда (03.11.2026).
OVERRIDES = [
    (r'^_normalize_low_rating_', 'chat_ratings', 'ChatRatingsMixin', 'чаты Chat2Desk: метрики и оценки'),
]

# (регулярное выражение по имени метода, модуль, класс, заголовок). Порядок важен.
RULES = [
    (r'^_init_|_schema|^_migrate_|^_backfill_|^_ensure_.*_(table|column|index|trigger)s?$',
     'schema', 'SchemaMixin', 'схема и миграции (инициализация при старте)'),
    (r'auction|post_auction|claim', 'shift_auction', 'ShiftAuctionMixin', 'аукцион смен'),
    (r'swap', 'shift_swaps', 'ShiftSwapsMixin', 'обмен сменами'),
    (r'break', 'breaks', 'BreaksMixin', 'перерывы'),
    (r'shift', 'shifts', 'ShiftsMixin', 'смены и заявки на смены'),
    (r'schedule|planner|norm', 'schedules', 'SchedulesMixin', 'графики'),
    (r'glb|late', 'group_late', 'GroupLateMixin', 'опоздания (бот групп)'),
    (r'task', 'tasks', 'TasksMixin', 'задачи'),
    (r'tez', 'tez', 'TezMixin', 'Тез'),
    (r'status_event|status_segment|status_import|operator_status|_statuses?$|status_transition',
     'operator_status', 'OperatorStatusMixin', 'статусы операторов (события и сегменты)'),
    (r'supervisor|clockster', 'supervisors', 'SupervisorsMixin', 'супервайзеры и Clockster'),
    (r'membership|group', 'groups', 'GroupsMixin', 'группы'),
    (r'survey', 'surveys', 'SurveysMixin', 'опросы'),
    (r'c2d|chat2desk|low_rating|chat_manager|rating', 'chat_ratings', 'ChatRatingsMixin',
     'чаты Chat2Desk: метрики и оценки'),
    (r'wazzup', 'wazzup', 'WazzupMixin', 'чаты Wazzup'),
    (r'chatapp', 'chatapp', 'ChatappMixin', 'ChatApp'),
    (r'szov|broadcast|wallboard', 'szov_wallboard', 'SzovWallboardMixin', 'табло и отбивки СЗоВ'),
    (r'calibration|call|audio|evaluation', 'calls', 'CallsMixin', 'звонки, оценки и калибровка'),
    (r'it_ticket|technical|issue', 'it_tickets', 'ItTicketsMixin', 'заявки IT и техпроблемы'),
    (r'training|trainer', 'trainings', 'TrainingsMixin', 'тренинги'),
    (r'event|birthday|four_you', 'events', 'EventsMixin', 'события, дни рождения, 4 You'),
    (r'sip|proxy', 'sip', 'SipMixin', 'SIP и прокси'),
    (r'^_?reg_|registration|contest', 'reg_contest', 'RegContestMixin', 'конкурс регистраций'),
    (r'work_hours|daily_hours|hours', 'work_hours', 'WorkHoursMixin', 'учёт часов'),
    (r'resource|fte|forecast', 'resource_fte', 'ResourceFteMixin', 'ресурсы и FTE'),
    (r'lms|course|lesson|quiz', 'lms', 'LmsMixin', 'LMS'),
    (r'recruiting|resume|candidate|parse_run', 'recruiting', 'RecruitingMixin', 'рекрутинг'),
    (r'front_office', 'front_office', 'FrontOfficeMixin', 'фронт-офис'),
    (r'report|xlsx|excel|serialize|export', 'reports', 'ReportsMixin', 'отчёты и выгрузки'),
    (r'department|direction|headed', 'departments', 'DepartmentsMixin', 'отделы и направления'),
    (r'session|auth|password|login|token|avatar|user', 'users', 'UsersMixin', 'пользователи и сессии'),
    (r'operator', 'operators', 'OperatorsMixin', 'операторы'),
    (r'salary|rate|bonus|penalty|payroll|calculation_model', 'salary', 'SalaryMixin', 'зарплата и модели расчёта'),
    (r'attach|file|photo|upload', 'attachments', 'AttachmentsMixin', 'вложения и файлы'),
    (r'news|notification|bell', 'notifications', 'NotificationsMixin', 'новости и колокол'),
    (r'dial', 'dial_list', 'DialListMixin', 'обзвон'),
    (r'import', 'imports', 'ImportsMixin', 'импорты'),
    # Хвост без явного слова раздела в имени — по тому, кто эти методы зовёт.
    (r'amo_lead', 'amo_leads', 'AmoLeadsMixin', 'лиды amoCRM'),
    (r'chat_hourly|chat_metric_surge|wz_snapshot|ca_snapshot|_coerce_daily_extra_metrics',
     'chat_ratings', 'ChatRatingsMixin', 'чаты Chat2Desk: метрики и оценки'),
    (r'driver_chats', 'driver_chats', 'DriverChatsMixin', 'чаты водителей'),
    (r'oktell|binotel|icore_phone', 'telephony', 'TelephonyMixin', 'телефония: Oktell, Binotel, iCORE Phone'),
    (r'_hourly_|_subtract_ranges', 'shift_auction', 'ShiftAuctionMixin', 'аукцион смен'),
    (r'_time_label_from_seconds|_split_absolute_minutes_intervals_by_day|_status_label_from_key'
     r'|_merge_adjacent_status_timeline_segments|_timeline_days_last_end|_status_range_covers_today',
     'operator_status', 'OperatorStatusMixin', 'статусы операторов (события и сегменты)'),
    (r'_get_month_|_get_tenure_months|month_snapshot|freeze_month|_is_month_closed'
     r'|aggregate_month_from_daily|aggregate_segment_from_daily|manual_fine|day_off',
     'work_hours', 'WorkHoursMixin', 'учёт часов'),
    (r'_extra_occupied_from_day_snapshots|_plan_recalc_pass|_almaty_now', 'schedules', 'SchedulesMixin', 'графики'),
    (r'_coerce_int_list|_parse_bool_value|_workplace_office_for_code|_it_channel|_it_instruction'
     r'|discovered_chat', 'it_tickets', 'ItTicketsMixin', 'заявки IT и техпроблемы'),
    (r'_people_|_summary_from_row|_live_search_predicate|telegram_id|dismissal|activity',
     'users', 'UsersMixin', 'пользователи и сессии'),
    (r'feedback_sla|feedback_ids|average_scores', 'calls', 'CallsMixin', 'звонки, оценки и калибровка'),
    (r'ai_feedback', 'ai_feedback', 'AiFeedbackMixin', 'кеш ИИ-обратной связи'),
    (r'baiga', 'baiga', 'BaigaMixin', 'списки Байги'),
    (r'live_status', 'operator_status', 'OperatorStatusMixin', 'статусы операторов (события и сегменты)'),
]
FALLBACK = ('misc', 'MiscMixin', 'прочее (разложить по разделам)')
# Соединения и курсоры — рядом с пулом, даже если имя не выдаёт глобалов.
CORE_NAMES = {'_get_connection', '_get_cursor', '_reset_pool_after_transient_error'}


def read_text(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def write_text(path, text, newline):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(text.replace('\n', newline))


def defined_names(nodes):
    names = OrderedDict()
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split('.')[0]] = 'import'
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names[alias.asname or alias.name] = 'from'
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names[node.name] = 'def'
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names[target.id] = 'assign'
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names[node.target.id] = 'assign'
    return names


def free_names(node):
    loads, bound, declared_global = set(), set(), set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            (loads if isinstance(sub.ctx, ast.Load) else bound).add(sub.id)
        elif isinstance(sub, ast.arg):
            bound.add(sub.arg)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub is not node:
            bound.add(sub.name)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            for alias in sub.names:
                bound.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            bound.add(sub.name)
        elif isinstance(sub, ast.Global):
            declared_global.update(sub.names)
    # Объявленное global — имя модуля, даже если функция ему присваивает. Учитывать его надо
    # после обхода: ast.walk идёт в ширину, и присваивание во вложенном блоке встречается позже
    # объявления (так 03.11.2026 при выносе ручек потерялся флаг слушателя аукциона).
    return (loads | declared_global) - (bound - declared_global)


def classify(node):
    if node.name == '__init__':
        return ('facade', 'Database', 'фасад')
    if node.name in CORE_NAMES or free_names(node) & REBOUND:
        return ('core', 'CoreMixin', 'пул соединений')
    for pattern, module, cls, title in OVERRIDES:
        if re.search(pattern, node.name):
            return (module, cls, title)
    for pattern, module, cls, title in RULES:
        if re.search(pattern, node.name):
            return (module, cls, title)
    return FALLBACK


def first_line(node):
    return min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--report', action='store_true')
    mode.add_argument('--write', action='store_true')
    parser.add_argument('--source', default=SOURCE,
                        help='database.py ДО разноса (после разноса сам database.py уже фасад)')
    args = parser.parse_args()

    raw = read_text(args.source)
    bom = raw.startswith('﻿')
    text = raw[1:] if bom else raw
    newline = '\r\n' if '\r\n' in text else '\n'
    assert text.count('\r\n') in (0, text.count('\n')), 'смешанные переводы строк'
    lines = text.replace('\r\n', '\n').split('\n')
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    pre = [n for n in tree.body if n.lineno < cls.lineno]
    post = [n for n in tree.body if n.lineno > cls.end_lineno]
    exc = next(n for n in post if isinstance(n, ast.ClassDef) and n.name == 'LowRatingAttributionConflict')

    core_names = defined_names(pre)
    core_names[exc.name] = 'def'

    # Куски тела класса: от конца предыдущего узла до конца текущего (комментарии над методом едут с ним).
    chunks = []
    prev_end = cls.lineno
    for node in cls.body:
        chunks.append((node, prev_end + 1, node.end_lineno))
        prev_end = node.end_lineno

    groups = OrderedDict()   # module -> dict(cls, title, chunks[])
    facade_chunks = []
    rewritten, errors, forced_core = [], [], []
    # Где окажется каждый метод — заранее: проверка ссылок Database._x ниже смотрит на хозяина.
    owners = {node.name: classify(node)[0] for node in cls.body
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for node, start, end in chunks:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facade_chunks.append((node, start, end))
            continue
        module, clsname, title = classify(node)
        if module == 'facade':
            facade_chunks.append((node, start, end))
            continue
        if module == 'core':
            forced_core.append(node.name)
        group = groups.setdefault(module, {'cls': clsname, 'title': title, 'chunks': [], 'names': set()})
        body = '\n'.join(lines[start - 1:end])
        free = free_names(node)
        if 'Database' in free:
            # Статические помощники зовут друг друга через класс; в миксине это его же имя —
            # но только если помощник лежит в том же миксине. Иначе вызов через класс миксина
            # не найдёт его по MRO Database (AttributeError в проде), а склейка для тестов
            # (tests/source_cache.py) возвращает «Database.» и ошибку прячет. Такое — стоп.
            for target in sorted(set(re.findall(r'\bDatabase\.(_\w+)\(', body))):
                if owners.get(target, module) != module:
                    errors.append(f'{node.name}: Database.{target} лежит в {owners[target]}, а не в {module} — '
                                  'вызов через класс миксина сломается; поправьте правила раскладки')
            body, count = re.subn(r'\bDatabase\.(_\w+)\(', clsname + r'.\1(', body)
            rewritten.append((node.name, count))
            free.discard('Database')
        group['chunks'].append(body)
        needed = free & set(core_names)
        group['names'] |= needed
        unknown = {n for n in free if n not in core_names and n not in dir(__builtins__)
                   and n not in ('self', 'cls')}
        for name in sorted(unknown):
            errors.append(f'{node.name}: имя {name} не найдено в модульном уровне database.py')

    ordered = []
    for module in ['core'] + [m for _, m, _, _ in RULES] + ['misc']:
        if module in groups and module not in ordered:
            ordered.append(module)

    if args.report:
        total = 0
        for module in ordered:
            group = groups[module]
            count = len(group['chunks'])
            size = sum(chunk.count('\n') + 1 for chunk in group['chunks'])
            total += count
            print(f'  db/{module:18s} {group["cls"]:24s} {count:4d} методов {size:6d} строк   {group["title"]}')
        print(f'  фасад: {len(facade_chunks)} узлов (атрибуты класса + __init__); всего методов {total}')
        print('  в CoreMixin из-за POOL:', forced_core)
        print('  правки Database.* → self.*:', rewritten)
        if 'misc' in groups:
            print('  НЕРАЗОБРАННЫЕ (misc):')
            for chunk in groups['misc']['chunks']:
                match = re.search(r'^\s+def (\w+)', chunk, re.M)
                print('    ', match.group(1) if match else '?')
        if errors:
            print('  ОШИБКИ:')
            for err in errors:
                print('    ', err)
        return 1 if errors else 0

    if errors or 'misc' in groups:
        for err in errors:
            print('ОШИБКА:', err)
        if 'misc' in groups:
            print('ОШИБКА: есть неразобранные методы — добавь правило в RULES (см. --report)')
        return 1

    # ── db/core.py: модульный уровень + исключение + CoreMixin ─────────────────────────────
    core_head = '\n'.join(lines[:cls.lineno - 1]).rstrip('\n')
    exc_text = '\n'.join(lines[exc.lineno - 1:exc.end_lineno])
    core_parts = [
        '"""Модульный уровень базы данных: пул соединений, константы, помощники.\n\n'
        'Байт в байт то, что стояло в database.py до класса Database (' + STAMP + ').\n'
        'Отсюда импортируют миксины db/*.py; database.py реэкспортирует эти имена наружу."""',
        core_head,
        '',
        '',
        '# Исключение методов низких оценок; в database.py стояло после класса, методам имя нужно\n'
        '# только в момент вызова.',
        exc_text,
    ]
    if 'core' in groups:
        core_parts += ['', '',
                       'class CoreMixin:\n'
                       '    """Соединения и пул. Эти методы читают и перепривязывают глобалы POOL и\n'
                       '    POOL_SEMAPHORE этого модуля, поэтому живут рядом с ними, а не в разделе."""',
                       '\n'.join(groups['core']['chunks']).lstrip('\n')]
    write_text(os.path.join(PARTS_DIR, 'core.py'), '\n'.join(core_parts).rstrip('\n') + '\n', newline)

    write_text(os.path.join(PARTS_DIR, '__init__.py'),
               '"""База данных портала: класс Database собирается из миксинов по разделам (см. database.py).\n'
               'Модульный уровень — db/core.py. ' + STAMP + '."""\n', newline)

    # Порядок узлов класса до разноса — для склейки «как было» в tests/source_cache.
    order_names = []
    for node, _, _ in chunks:
        name = node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else node.targets[0].id
        assert name not in order_names, f'повтор имени в теле класса: {name}'
        order_names.append(name)
    write_text(os.path.join(PARTS_DIR, '_legacy_layout.py'),
               '"""Порядок узлов класса Database в database.py до разноса на миксины (02.11.2026).\n\n'
               'Нужен одному потребителю — tests/source_cache.database_flat_text(): склейка собирает\n'
               'прежний database.py в прежнем порядке, чтобы тесты, режущие текст между соседними\n'
               'методами, продолжали работать. Новые методы сюда добавлять не нужно: чего нет в списке,\n'
               'склейка ставит в конец."""\n'
               'LEGACY_ORDER = (\n' + ''.join(f'    {name!r},\n' for name in order_names) + ')\n', newline)

    # ── db/<раздел>.py ────────────────────────────────────────────────────────────────────
    for module in ordered:
        if module == 'core':
            continue
        group = groups[module]
        names = sorted(group['names'], key=lambda n: (n.lower(), n))
        parts = ['"""База данных: ' + group['title'] + '.\n\nМетоды класса Database, ' + STAMP + ',\n'
                 'без изменений тел. Модульные помощники и константы — в db/core.py."""']
        if names:
            parts.append('from db.core import (  # noqa: F401\n' + ''.join(f'    {n},\n' for n in names) + ')')
        parts += ['', '', f'class {group["cls"]}:', '\n'.join(group['chunks']).lstrip('\n')]
        write_text(os.path.join(PARTS_DIR, f'{module}.py'), '\n'.join(parts).rstrip('\n') + '\n', newline)

    # ── фасад database.py ─────────────────────────────────────────────────────────────────
    export = [n for n, kind in core_names.items() if kind != 'import']
    bases = ['CoreMixin'] + [groups[m]['cls'] for m in ordered if m != 'core']
    imports = ['from db.core import CoreMixin'] + [f'from db.{m} import {groups[m]["cls"]}' for m in ordered if m != 'core']
    facade_body = '\n'.join('\n'.join(lines[start - 1:end]) for _, start, end in facade_chunks).lstrip('\n')
    tail_lines = lines[cls.end_lineno:]
    # Хвост без исключения и комментария над ним (они переехали в db/core.py).
    exc_start = exc.lineno - 1
    while exc_start - 1 > cls.end_lineno and lines[exc_start - 1].startswith('#'):
        exc_start -= 1
    tail = lines[cls.end_lineno:exc_start - 1] + lines[exc.end_lineno:]
    tail_text = '\n'.join(tail).strip('\n')
    facade = [
        '"""Фасад базы данных портала.\n\n'
        'Класс Database собран из миксинов db/<раздел>.py — по разделам, как и пакеты бэкенда\n'
        '(' + STAMP + '). Тела методов перенесены без изменений. Всё модульное — пул, константы,\n'
        'помощники — живёт в db/core.py и реэкспортируется отсюда: код и тесты по-прежнему пишут\n'
        '`from database import db, IT_TICKET_CATALOG`. Текст «как одним файлом» для тестов собирает\n'
        'tests/source_cache.py."""',
        'import logging',
        '',
        'from db.core import (  # noqa: F401 — реэкспорт модульных имён\n' + ''.join(f'    {n},\n' for n in export) + ')',
        '\n'.join(imports),
        '',
        '',
        f'class Database({", ".join(bases)}):',
        facade_body,
        '',
        '',
        tail_text,
    ]
    write_text(SOURCE, ('﻿' if bom else '') + '\n'.join(facade) + '\n', newline)
    print('записано: db/core.py, db/__init__.py,', ', '.join(f'db/{m}.py' for m in ordered if m != 'core'), 'и database.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
