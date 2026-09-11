# -*- coding: utf-8 -*-
"""Раздел «Табло Тез КЦ» (задача #292): права, снимок кабинета Binotel, кэш, пороги, разводка.

Устройство то же, что у стражей табло СЗоВ (tests/test_szov_wallboard.py), и по той же
причине: `bot_schedule2.py` из тестов не импортируется — он на старте поднимает пул к боевой
базе. Поэтому функции достаём из него через `ast` и исполняем в подготовленном namespace, а
пороговые функции фронта гоняем НАСТОЯЩИМ node.

Что здесь принципиально иначе, чем у СЗоВ:
  * источник — не SQL к Oktell, а кабинет my.binotel.kz. Ходит в него отдельный модуль
    `tez_wallboard_source` (он под своими тестами на фикстурах), поэтому подменяется здесь
    ровно ОДНА его функция — `fetch_snapshot`, то есть сам поход в кабинет. Разбор и
    арифметика (`day_totals`, `summarize_queue_operators`, `summarize_presence_operators`)
    остаются настоящими: иначе тест сторожил бы заглушку, а не правила;
  * состав отдела берётся настоящим методом `Database.get_tez_wallboard_operators`,
    поднятым тем же приёмом через `ast` и посаженным на фальшивый курсор. Только так
    сторожится главная ловушка отдела: sip_number в нём НЕ уникален, один и тот же
    внутренний номер числится и за уволенным, и за действующим сотрудником;
  * направления два и они разные по природе (у ТП есть очередь, у ОП её нет вовсе), а
    обход кабинета один на оба.
"""
import ast
import contextlib
import copy
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import date as dt_date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tests import source_cache

# Модуль-источник импортируется обычным import: в нём нет ни Flask, ни database.py — это его
# заявленная граница. Настоящие парсеры нужны, чтобы тест сторожил арифметику, а не заглушку.
import tez_wallboard_source as tez_source_real


ROOT = Path(__file__).resolve().parents[1]
BOT_PATH = ROOT / "bot_schedule2.py"
DB_PATH = ROOT / "database.py"
MONITORING = ROOT / "src" / "components" / "monitoring"

BOT_SOURCE = source_cache.read(BOT_PATH)
DB_SOURCE = source_cache.read(DB_PATH)


def _load_names(source, names, namespace, label="<tez-wallboard>"):
    """Исполняет в namespace перечисленные функции, классы и присваивания модульного уровня."""
    tree = source_cache.parse(source)
    wanted = set(names)
    body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in wanted:
            body.append(node)
        elif isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & wanted:
                body.append(node)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, label, "exec"), namespace)
    missing = sorted(name for name in wanted if name not in namespace)
    if missing:
        raise AssertionError(f"не найдено в bot_schedule2.py: {missing}")
    return namespace


# --- настоящий метод состава отдела из database.py ------------------------------------------

def _load_db_method(name):
    """Метод Database без импорта модуля: `import database` поднимает пул к боевой базе."""
    tree = source_cache.parse(DB_SOURCE)
    namespace = {'datetime': datetime, 'date': dt_date, 'timedelta': timedelta,
                 'ZoneInfo': ZoneInfo}
    for node in tree.body:
        if isinstance(node, ast.Assign) and all(
                isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            try:
                module = ast.Module(body=[copy.deepcopy(node)], type_ignores=[])
                ast.fix_missing_locations(module)
                exec(compile(module, str(DB_PATH), "exec"), namespace)
            except Exception:
                pass
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.Module(body=[copy.deepcopy(fn)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(DB_PATH), "exec"), namespace)
    return namespace[name]


def _db_class_attr(name):
    """Атрибут класса Database (например, список статусов уволенных) без импорта модуля."""
    tree = source_cache.parse(DB_SOURCE)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Database')
    node = next(n for n in cls.body
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
    return ast.literal_eval(node.value)


TEZ_EXCLUDED_STATUSES = _db_class_attr('TEZ_WALLBOARD_EXCLUDED_STATUSES')


class _UsersCursor:
    """Фальшивый курсор над таблицей users: применяет ровно те условия, что прислал метод.

    Отбор по занятости применяется ТОЛЬКО если метод и правда передал список исключённых
    статусов вместе с условием в SQL. Уберут условие — уволенный вернётся из «базы», и
    функциональный тест про дубль внутреннего номера покраснеет."""

    def __init__(self, table):
        self.table = table
        self.sql = None
        self.params = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = re.sub(r'\s+', ' ', sql or '').strip()
        self.params = list(params or [])
        department_id = next((p for p in self.params if isinstance(p, int)), None)
        excluded = next((p for p in self.params if isinstance(p, (list, tuple, set))), None)
        rows = [row for row in self.table if row.get('department_id') == department_id]
        if 'u.status' in self.sql and excluded is not None:
            banned = {str(x).strip().lower() for x in excluded}
            rows = [row for row in rows
                    if str(row.get('status') or '').strip().lower() not in banned]
        rows.sort(key=lambda row: row.get('name') or '')
        self._rows = [(
            row['id'],
            row.get('name'),
            (str(row['sip_number']).strip() or None) if row.get('sip_number') else None,
            row.get('direction_id'),
            row.get('direction_name'),
            str(row.get('direction_model') or '').lower(),
            str(row.get('group_model') or '').lower(),
            row.get('role'),
        ) for row in rows]

    def fetchall(self):
        return list(self._rows)


class _RealTezOperators:
    """Настоящий `Database.get_tez_wallboard_operators` поверх таблицы-фикстуры."""

    TEZ_WALLBOARD_EXCLUDED_STATUSES = TEZ_EXCLUDED_STATUSES
    _method = staticmethod(_load_db_method('get_tez_wallboard_operators'))

    def __init__(self, table):
        self.table = table
        self.cursor = _UsersCursor(table)

    def _normalize_schedule_date(self, value):
        return value

    def _get_cursor(self):
        return self.cursor

    def __call__(self, department_id, on_date=None):
        return type(self)._method(self, department_id, on_date)


class _FakeDb:
    """Минимальный db: отделы, отдел пользователя, состав табло Тез, снимки табло по направлениям."""

    def __init__(self, departments=None, user_departments=None, operators_table=None,
                 snapshots=None, snapshot_error=False, live_statuses=None,
                 live_status_error=False):
        self.departments = departments if departments is not None else [
            {'id': 5, 'code': 'szov'}, {'id': 41, 'code': 'tez'}, {'id': 367, 'code': 'op'}]
        self.user_departments = user_departments or {}
        self.operators_table = operators_table if operators_table is not None else []
        self._operators = _RealTezOperators(self.operators_table)
        # {направление: (payload, captured_at)} — строка снимка в БД теперь принадлежит
        # направлению, а не одному табло на всю компанию.
        self.snapshots = dict(snapshots or {})
        self.snapshot_error = snapshot_error
        self.saved_snapshots = []
        self.snapshot_reads = []
        # Статусы людей «сейчас» — события iCORE Phone из нашей же базы. Пустой словарь здесь
        # означает «телефоны ещё ничего не прислали», а не «все не в сети».
        self.live_statuses = dict(live_statuses or {})
        self.live_status_error = live_status_error
        self.live_status_requests = []

    def get_departments(self):
        return self.departments

    def get_user_department_id(self, user_id):
        return self.user_departments.get(int(user_id))

    def get_tez_wallboard_operators(self, department_id, on_date=None):
        return self._operators(department_id, on_date)

    def get_operator_live_statuses(self, operator_ids, as_of=None, lookback_hours=None):
        ids = sorted({int(value) for value in (operator_ids or [])})
        self.live_status_requests.append(ids)
        if self.live_status_error:
            raise RuntimeError("БД недоступна")
        return {op_id: dict(value) for op_id, value in self.live_statuses.items() if op_id in ids}

    def get_szov_wallboard_snapshot(self, direction='osnova'):
        self.snapshot_reads.append(direction)
        if self.snapshot_error:
            raise RuntimeError("БД недоступна")
        return self.snapshots.get(direction) or (None, None)

    def save_szov_wallboard_snapshot(self, payload, captured_at, direction='osnova'):
        if self.snapshot_error:
            raise RuntimeError("БД недоступна")
        self.saved_snapshots.append((payload, captured_at, direction))
        self.snapshots[direction] = (payload, captured_at)


# --- фикстура состава отдела ----------------------------------------------------------------
# Отдел Тез КЦ один, направления внутри него разделяет ТОЛЬКО модель расчёта группы.
TEZ_DEPARTMENT_ID = 41

USERS_TABLE = [
    {'id': 101, 'name': 'Аскар Тлеу', 'sip_number': '901', 'department_id': TEZ_DEPARTMENT_ID,
     'group_model': 'tez_line', 'direction_name': 'ТП линия', 'role': 'operator', 'status': 'active'},
    {'id': 102, 'name': 'Дана Ким', 'sip_number': '902', 'department_id': TEZ_DEPARTMENT_ID,
     'group_model': 'tez_line', 'direction_name': 'ТП линия', 'role': 'operator', 'status': 'active'},
    {'id': 103, 'name': 'Нурлан Абай', 'sip_number': '903', 'department_id': TEZ_DEPARTMENT_ID,
     'group_model': 'tez_line', 'direction_name': 'ТП линия', 'role': 'trainee', 'status': 'active'},
    {'id': 201, 'name': 'Салтанат Ли', 'sip_number': '950', 'department_id': TEZ_DEPARTMENT_ID,
     'group_model': 'tez_op', 'direction_name': 'ОП линия', 'role': 'operator', 'status': 'active'},
    {'id': 202, 'name': 'Ерлан Бек', 'sip_number': '951', 'department_id': TEZ_DEPARTMENT_ID,
     'group_model': 'tez_op', 'direction_name': 'ОП линия', 'role': 'operator', 'status': 'active'},
    {'id': 203, 'name': 'Айгуль Сат', 'sip_number': '952', 'department_id': TEZ_DEPARTMENT_ID,
     'group_model': 'tez_op', 'direction_name': 'ОП линия', 'role': 'operator', 'status': 'active'},
    # Уволенный держит ТОТ ЖЕ внутренний номер, что действующая Дана Ким (902). На проде таких
    # дублей на 08.09.2026 было пять. Попади он в состав — на стене в списке «на перерыве»
    # висела бы фамилия уволенного, и это самый заметный сорт брака.
    {'id': 999, 'name': 'Яков Уволенный', 'sip_number': '902', 'department_id': TEZ_DEPARTMENT_ID,
     'group_model': 'tez_line', 'direction_name': 'ТП линия', 'role': 'operator', 'status': 'fired'},
    # Глава отдела и СВ модели расчёта не имеют — они не на линии и в счётчики не идут.
    {'id': 300, 'name': 'Глава Тез КЦ', 'sip_number': None, 'department_id': TEZ_DEPARTMENT_ID,
     'group_model': None, 'direction_name': None, 'role': 'admin', 'status': 'active'},
    # Чужой отдел: в выборку не попадает вовсе.
    {'id': 700, 'name': 'Чужой Оператор', 'sip_number': '901', 'department_id': 5,
     'group_model': 'tez_line', 'direction_name': 'ТП линия', 'role': 'operator', 'status': 'active'},
]


def _stats(**kwargs):
    row = {key: 0 for key in tez_source_real.EMPLOYEE_STAT_FIELDS.values()}
    row.update(kwargs)
    return row


def _employee(number, name, stats, presence=None, since=None):
    return {
        'number': number,
        'ext_id': f'ext-{number}',
        'name': name,
        'department': 'Тез КЦ',
        'presence_state': presence,
        'presence_state_updated_at': since,
        'call_center_enabled': True,
        'stats': stats,
        # Ловушка безопасности: сырой ответ кабинета несёт живые SIP-учётки. Сюда они попасть
        # не должны ни при каком повороте — их ищет отдельный рекурсивный страж.
        'sipLogin': 'sip-901@binotel',
        'sipPassword': 'S3CRET-PASSWORD',
    }


BINOTEL_NOW_TS = 1757323638  # 2026-09-08 12:07:18 в поясе кабинета


def _raw_snapshot(*, queue=True, zero_day=False):
    """Ответ кабинета в том виде, в каком его отдаёт tez_wallboard_source.fetch_snapshot."""
    if zero_day:
        tp_stats = {number: _stats() for number in ('901', '902', '903')}
        op_stats = {number: _stats() for number in ('950', '951', '952')}
        served, lost, sl_ratio, avg_wait = 0, 0, None, None
    else:
        tp_stats = {
            '901': _stats(incoming_success=10, incoming_failed=1, incoming_billsec=1000,
                          incoming_waitsec=100, outgoing_amount=5, outgoing_success=3,
                          outgoing_billsec=300),
            '902': _stats(incoming_success=6, incoming_billsec=500, incoming_waitsec=60),
            '903': _stats(incoming_success=4, incoming_billsec=200, incoming_waitsec=40),
        }
        op_stats = {
            '950': _stats(outgoing_amount=50, outgoing_success=20, outgoing_billsec=1000),
            '951': _stats(outgoing_amount=30, outgoing_success=10, outgoing_billsec=500),
            '952': _stats(),
        }
        served, lost, sl_ratio, avg_wait = 300, 12, 0.93, 8

    employees = {}
    employees['901'] = _employee('901', 'Тлеу Аскар', tp_stats['901'], 'Active')
    employees['902'] = _employee('902', 'Ким Дана', tp_stats['902'], 'Break in work',
                                 BINOTEL_NOW_TS - 420)
    employees['903'] = _employee('903', 'Абай Нурлан', tp_stats['903'], 'Active')
    employees['950'] = _employee('950', 'Ли Салтанат', op_stats['950'], 'Inactive')
    employees['951'] = _employee('951', 'Бек Ерлан', op_stats['951'], 'Inactive')
    employees['952'] = _employee('952', 'Сат Айгуль', op_stats['952'], 'Break in work',
                                 BINOTEL_NOW_TS - 900)
    # Чужой номер того же кабинета: он не наш, и ни в один из двух наборов попасть не должен.
    employees['905'] = _employee('905', 'Чужой Кабинетный', _stats(
        incoming_success=999, incoming_billsec=99999, outgoing_amount=9999,
        outgoing_success=9999, outgoing_billsec=99999), 'Active')

    queue_block = None
    if queue:
        queue_block = {
            'queue_id': '17',
            'queue_name': 'ТП',
            'queue': 0,
            'served': served,
            'lost': lost,
            'sl_ratio': sl_ratio,
            'sl_percent_text': '93%',
            'sl_threshold_seconds': 20,
            'avg_talk_text': '02:09',
            'avg_talk_seconds': 129,
            'avg_wait_text': '00:08',
            'avg_wait_seconds': avg_wait,
            'counters': {'members': 3, 'working': 2, 'not_working': 1,
                         'status_active': 2, 'status_work_in_crm': 0,
                         'status_break': 1, 'status_inactive': 0},
            'unknown_counters': [],
            'unknown_statuses': [],
            'operators': [
                {'name': 'Тлеу Аскар', 'number': '901', 'status_key': 'free',
                 'status_text': 'В ожидании', 'in_state_seconds': 120,
                 'sipLogin': 'sip-901', 'sipPassword': 'S3CRET-PASSWORD'},
                {'name': 'Абай Нурлан', 'number': '903', 'status_key': 'talking',
                 'status_text': 'Разговаривает', 'in_state_seconds': 45},
                {'name': 'Ким Дана', 'number': '902', 'status_key': 'break',
                 'status_text': 'Перерыв в работе', 'in_state_seconds': 420},
            ],
        }

    return {
        'day': '2026-09-08',
        'binotel_now': '2026-09-08 12:07:18',
        'binotel_now_source': 'cabinet',
        'binotel_now_ts': BINOTEL_NOW_TS,
        'sl_threshold_seconds': 20,
        'queue_id': '17',
        'queue': queue_block,
        'queue_error': None if queue else 'Страница очереди Binotel не распознана',
        'queue_clients': [],
        'employees': {'departments': {}, 'employees': employees},
        'live_calls': [
            {'general_call_id': '1', 'employee_number': '950', 'call_type': 'outgoing',
             'started_at': BINOTEL_NOW_TS - 30, 'duration': 30, 'billsec': 30,
             'ring_seconds': None, 'now_ts': BINOTEL_NOW_TS, 'login': 'call-login'},
        ],
        'endpoints': {'901': True, '903': True, '950': True, '951': False, '952': False,
                      'endpointData': True},
        'endpoints_age_seconds': 12,
        'diagnostics': {
            'queue_rows_unverified': True,
            'unknown_queue_counters': [],
            'unknown_queue_statuses': [],
            'binotel_now_source': 'cabinet',
            'queue_error': None if queue else 'Страница очереди Binotel не распознана',
        },
    }


class _FakeCabinetSession:
    """Заглушка сессии кабинета: настоящая при создании читает учётку из окружения."""

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.tz_name = 'Asia/Almaty'


@contextlib.contextmanager
def _patched_source(state):
    """Подменяет модуль-источник целиком, оставляя настоящими все его парсеры.

    Функция табло берёт источник через `import tez_wallboard_source` ВНУТРИ себя, поэтому
    подмена делается в sys.modules, а не в namespace. Заменяется ровно поход в кабинет —
    `fetch_snapshot` и `CabinetSession`; арифметика (`day_totals`, `summarize_*`) остаётся
    настоящей, иначе тест сторожил бы заглушку."""
    stub = types.ModuleType('tez_wallboard_source')
    for name in dir(tez_source_real):
        if not name.startswith('__'):
            setattr(stub, name, getattr(tez_source_real, name))

    def fetch_snapshot(session, day=None, **kwargs):
        state['calls'] += 1
        state['kwargs'].append(kwargs)
        error = state.get('error')
        if error is not None:
            raise error
        return copy.deepcopy(state['raw'])

    stub.fetch_snapshot = fetch_snapshot
    stub.CabinetSession = _FakeCabinetSession
    stub.is_configured = lambda config=None: True
    previous = sys.modules.get('tez_wallboard_source')
    sys.modules['tez_wallboard_source'] = stub
    try:
        yield stub
    finally:
        if previous is None:
            sys.modules.pop('tez_wallboard_source', None)
        else:
            sys.modules['tez_wallboard_source'] = previous


# --- ПРАВА ----------------------------------------------------------------------------------

class TezWallboardGuardTests(unittest.TestCase):
    """Доступ к табло: глобальные админы, глава отдела Тез и СВ отдела Тез. Остальным 403."""

    def _namespace(self, accounts, departments=None):
        """accounts: {id: {'role':…, 'headed': id отдела или None, 'department': id или None}}."""
        current = {'id': None}

        def requester():
            uid = current['id']
            account = accounts[uid]
            return uid, (uid, None, None, account['role']), None

        ns = {
            'time': time,
            'jsonify': lambda payload: payload,
            'db': _FakeDb(
                departments=departments,
                user_departments={uid: acc['department'] for uid, acc in accounts.items()
                                  if acc.get('department')},
            ),
            '_get_authenticated_requester': requester,
            # Лестницу ролей берём настоящую: подделав её, мы бы сторожили собственную выдумку.
            '_headed_department_id': lambda uid: accounts[uid].get('headed'),
        }
        _load_names(BOT_SOURCE, {
            'ROLE_HIERARCHY',
            '_normalize_user_role', '_get_role_level', '_has_min_role',
            '_is_super_admin_role', '_is_admin_role', '_is_global_admin_requester',
            '_is_supervisor_role',
            'TEZ_WALLBOARD_DEPARTMENT_CODE',
            '_TEZ_WALLBOARD_DEPARTMENT_CACHE',
            '_TEZ_WALLBOARD_DEPARTMENT_CACHE_TTL',
            '_tez_wallboard_department_id',
            '_tez_wallboard_guard',
        }, ns)
        # Кэш отдела статический на модуль — сбрасываем, чтобы тесты не влияли друг на друга.
        ns['_TEZ_WALLBOARD_DEPARTMENT_CACHE'].update(ts=0.0, id=None)
        ns['_current'] = current
        return ns

    def _guard(self, *, role, requester_id, headed=None, department=None, departments=None,
               accounts=None):
        accounts = dict(accounts or {})
        accounts[requester_id] = {'role': role, 'headed': headed, 'department': department}
        ns = self._namespace(accounts, departments=departments)
        ns['_current']['id'] = requester_id
        return ns['_tez_wallboard_guard']()

    def test_super_admin_allowed(self):
        _, err = self._guard(role='super_admin', requester_id=1)
        self.assertIsNone(err)

    def test_global_admin_allowed(self):
        """Админ, который не возглавляет отдела, — глобальный: раздел открыт."""
        requester_id, err = self._guard(role='admin', requester_id=7)
        self.assertIsNone(err)
        self.assertEqual(requester_id, 7)

    def test_tez_department_head_allowed(self):
        _, err = self._guard(role='admin', requester_id=9, headed=TEZ_DEPARTMENT_ID)
        self.assertIsNone(err)

    def test_head_of_another_department_forbidden(self):
        """Граница отдела строгая: главе СЗоВ нагрузка линии Тез не показывается."""
        _, err = self._guard(role='admin', requester_id=11, headed=5)
        self.assertIsNotNone(err)
        payload, status = err
        self.assertEqual(status, 403)
        self.assertEqual(payload, {"error": "forbidden"})

    def test_tez_supervisor_allowed(self):
        _, err = self._guard(role='sv', requester_id=21, department=TEZ_DEPARTMENT_ID)
        self.assertIsNone(err)

    def test_supervisor_of_another_department_forbidden(self):
        _, err = self._guard(role='sv', requester_id=22, department=5)
        self.assertIsNotNone(err)
        self.assertEqual(err[1], 403)

    def test_operator_forbidden(self):
        """Оператор отдела Тез — сотрудник, а не руководитель: табло ему не положено."""
        _, err = self._guard(role='operator', requester_id=33, department=TEZ_DEPARTMENT_ID)
        self.assertIsNotNone(err)
        self.assertEqual(err[1], 403)

    def test_trainer_of_the_department_forbidden(self):
        _, err = self._guard(role='trainer', requester_id=34, department=TEZ_DEPARTMENT_ID)
        self.assertIsNotNone(err)
        self.assertEqual(err[1], 403)

    def test_missing_tez_department_forbids_non_admins(self):
        """Отдела с кодом tez в базе нет — пускаем только глобальных админов, а не всех подряд."""
        departments = [{'id': 5, 'code': 'szov'}, {'id': 367, 'code': 'op'}]
        _, err = self._guard(role='sv', requester_id=21, department=TEZ_DEPARTMENT_ID,
                             departments=departments)
        self.assertIsNotNone(err)
        self.assertEqual(err[1], 403)
        _, err_head = self._guard(role='admin', requester_id=9, headed=TEZ_DEPARTMENT_ID,
                                  departments=departments)
        self.assertIsNotNone(err_head)
        self.assertEqual(err_head[1], 403)
        _, err_admin = self._guard(role='admin', requester_id=7, departments=departments)
        self.assertIsNone(err_admin, "глобальный админ не должен зависеть от наличия отдела")

    def test_both_accounts_of_the_same_head_pass(self):
        """ГЛАВНЫЙ тест прав. У главы Тез КЦ ДВЕ учётки, и это один человек:

          * глава — роль admin, стоит в departments.head_user_id (и потому НЕ глобальный админ);
          * супервайзер — роль sv с department_id отдела.

        Сработать обязаны ОБЕ ветки лестницы. Отвалится любая — руководитель получит отказ на
        собственном табло, зайдя «не с той» учётки, и решит, что раздел не работает."""
        accounts = {
            # глава: admin + head_user_id отдела Тез
            9: {'role': 'admin', 'headed': TEZ_DEPARTMENT_ID, 'department': None},
            # её же вторая учётка: sv + department_id отдела Тез
            10: {'role': 'sv', 'headed': None, 'department': TEZ_DEPARTMENT_ID},
        }
        ns = self._namespace(accounts)
        for uid, branch in ((9, 'ветка главы отдела'), (10, 'ветка супервайзера отдела')):
            ns['_current']['id'] = uid
            requester_id, err = ns['_tez_wallboard_guard']()
            self.assertIsNone(err, f"{branch}: учётка {uid} получила отказ на своём табло")
            self.assertEqual(requester_id, uid)

    def test_head_is_not_a_global_admin(self):
        """Смысл ветки «глава отдела»: без неё эта учётка получила бы 403, а не доступ.

        _is_global_admin_requester для главы отдела возвращает False именно потому, что
        человек возглавляет отдел, — проверяем это прямо, чтобы тест выше не оказался
        зелёным по случайной причине."""
        accounts = {
            9: {'role': 'admin', 'headed': TEZ_DEPARTMENT_ID, 'department': None},
            7: {'role': 'admin', 'headed': None, 'department': None},
            1: {'role': 'super_admin', 'headed': None, 'department': None},
        }
        ns = self._namespace(accounts)
        self.assertFalse(ns['_is_global_admin_requester']('admin', 9),
                         "глава отдела не глобальный админ — иначе ветка ниже не нужна")
        self.assertTrue(ns['_is_global_admin_requester']('admin', 7))
        self.assertTrue(ns['_is_global_admin_requester']('super_admin', 1))


# --- СНИМОК ---------------------------------------------------------------------------------

class _SnapshotHarness:
    """Стенд снимка: настоящие функции табло, поддельные кабинет и БД."""

    def _namespace(self, *, raw=None, db=None, error=None):
        state = {'calls': 0, 'kwargs': [], 'raw': raw if raw is not None else _raw_snapshot(),
                 'error': error}
        ns = {
            'time': time,
            'threading': threading,
            'logging': logging,
            'datetime': datetime,
            'dt_date': dt_date,
            're': re,
            # Константы модуля читаются через _env_int; в тесте берём значения по умолчанию.
            '_env_int': lambda name, default, minimum=None, maximum=None: default,
            'db': db if db is not None else _FakeDb(operators_table=USERS_TABLE),
            # Нормализация имени — своя маленькая функция статус-импорта; связка людей идёт
            # прежде всего по внутреннему номеру, имя тут запасной ключ.
            '_status_import_normalize_operator_name': (
                lambda value: re.sub(r'\s+', ' ', str(value or '').strip()).lower()),
        }
        _load_names(BOT_SOURCE, {
            'TEZ_WALLBOARD_DEPARTMENT_CODE',
            'TEZ_WALLBOARD_CACHE_TTL_SECONDS',
            'TEZ_WALLBOARD_HTTP_TIMEOUT_SECONDS',
            'TEZ_WALLBOARD_LOCK_WAIT_SECONDS',
            'TEZ_WALLBOARD_RETRY_AFTER_FAIL_SECONDS',
            'TEZ_WALLBOARD_STALE_MAX_SECONDS',
            'TEZ_WALLBOARD_PERSIST_INTERVAL_SECONDS',
            'TEZ_WALLBOARD_ENDPOINTS_TTL_SECONDS',
            'TEZ_AR_TARGET_PERCENT',
            'TEZ_AR_BAD_PERCENT',
            'TEZ_WALLBOARD_DIRECTIONS',
            '_TEZ_WALLBOARD_MODEL_BY_DIRECTION',
            '_TEZ_WALLBOARD_DEPARTMENT_CACHE',
            '_TEZ_WALLBOARD_DEPARTMENT_CACHE_TTL',
            '_TEZ_WALLBOARD_PEOPLE_CACHE',
            '_TEZ_WALLBOARD_PEOPLE_CACHE_TTL',
            '_tez_wallboard_cache',
            '_tez_wallboard_lock',
            '_tez_wallboard_session_holder',
            'TezWallboardDirectionUnavailable',
            '_tez_wallboard_department_id',
            '_tez_wallboard_session',
            '_tez_wallboard_people',
            '_tez_wallboard_person',
            '_tez_wallboard_name_list',
            # Поимённый список: каталог статусов телефона, счётчики человека, сборка строк.
            '_TEZ_WALLBOARD_STATUS_CATALOG',
            '_TEZ_WALLBOARD_STATUS_UNKNOWN',
            '_tez_wallboard_status_entry',
            '_tez_wallboard_person_stats',
            '_tez_wallboard_roster',
            '_tez_wallboard_fetch_snapshot',
            '_tez_wallboard_snapshot',
            '_tez_wallboard_direction_payload',
            # Обвязка кэша общая на все табло: снимок в БД, устаревание, пауза после ошибки.
            '_wallboard_restore_cache',
            '_wallboard_persist_cache',
            '_wallboard_snapshot_with_cache',
        }, ns)
        ns['_TEZ_WALLBOARD_DEPARTMENT_CACHE'].update(ts=0.0, id=None)
        ns['_TEZ_WALLBOARD_PEOPLE_CACHE'].update(ts=0.0, day=None, people=None)
        ns['_tez_wallboard_cache'].update(ts=0.0, payload=None, failed_at=0.0, error=None,
                                          restored=False, persisted_at=0.0)
        ns['_tez_wallboard_session_holder']['session'] = None
        ns['_state'] = state
        return ns


def _walk_keys(node, path=()):
    """Все ключи и значения снимка вглубь: рекурсивный обход для стража секретов."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path + (str(key),), key, value
            yield from _walk_keys(value, path + (str(key),))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _walk_keys(value, path + (str(index),))


class TezWallboardSnapshotTests(_SnapshotHarness, unittest.TestCase):
    """Один обход кабинета — два направления, каждое по своему составу людей."""

    def test_one_cabinet_walk_feeds_both_directions(self):
        """Два табло не должны стоить двух обходов: учётка у кабинета одна на всю компанию."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_fetch_snapshot']()
        self.assertEqual(ns['_state']['calls'], 1)
        self.assertIsNotNone(snapshot['tp'])
        self.assertIsNotNone(snapshot['op'])

    def test_two_direction_requests_share_a_single_walk(self):
        """Открытые ТП и ОП внутри TTL ходят в кабинет ровно один раз на двоих."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            tp = ns['_tez_wallboard_direction_payload']('tp')
            op = ns['_tez_wallboard_direction_payload']('op')
        self.assertEqual(ns['_state']['calls'], 1)
        self.assertNotEqual(tp['now'], op['now'])

    def test_endpoints_are_asked_with_their_own_ttl(self):
        """Список линий кабинет отдаёт ~3,5 с — у него свой срок, а не общий шаг опроса."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            ns['_tez_wallboard_fetch_snapshot']()
        kwargs = ns['_state']['kwargs'][0]
        self.assertTrue(kwargs['with_endpoints'])
        self.assertEqual(kwargs['endpoints_ttl_seconds'], ns['TEZ_WALLBOARD_ENDPOINTS_TTL_SECONDS'])
        self.assertEqual(kwargs['timeout'], ns['TEZ_WALLBOARD_HTTP_TIMEOUT_SECONDS'])
        self.assertGreater(ns['TEZ_WALLBOARD_ENDPOINTS_TTL_SECONDS'],
                           ns['TEZ_WALLBOARD_CACHE_TTL_SECONDS'])

    def test_directions_are_counted_over_different_people(self):
        """ТП — те, кто обслуживает очередь; ОП — состав направления продаж. Множества разные."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_fetch_snapshot']()
        tp_now, op_now = snapshot['tp']['now'], snapshot['op']['now']
        self.assertEqual(tp_now['operators_total'], 3)
        self.assertEqual(op_now['operators_total'], 3)
        # ТП: свободен + разговаривает = онлайн, один на перерыве
        self.assertEqual((tp_now['operators_free'], tp_now['operators_talking']), (1, 1))
        self.assertEqual(tp_now['operators_online'], 2)
        self.assertEqual(tp_now['operators_on_break'], 1)
        # ОП: «свободен» = зарегистрированный телефон, «в разговоре» = активный звонок
        self.assertEqual(op_now['operators_talking'], 1)
        self.assertEqual(op_now['operators_on_break'], 1)
        self.assertEqual(op_now['operators_online'],
                         op_now['operators_free'] + op_now['operators_talking'])
        # Списки людей не пересекаются
        tp_names = {item['name'] for item in tp_now['break_list']}
        op_names = {item['name'] for item in op_now['break_list']}
        self.assertEqual(tp_names & op_names, set())

    def test_day_totals_are_summed_over_different_number_sets(self):
        """Итоги дня тоже считаются по своим номерам, и чужой номер кабинета не приплюсовывается."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_fetch_snapshot']()
        tp_today, op_today = snapshot['tp']['today'], snapshot['op']['today']
        # ТП: принятые/потерянные/SL приходят готовыми со страницы очереди
        self.assertEqual((tp_today['served'], tp_today['lost'], tp_today['arrived']),
                         (300, 12, 312))
        self.assertEqual(tp_today['sl_ratio'], 0.93)
        # Среднее время разговора считаем сами и УСЕКАЕМ: 1700 / 20 = 85
        self.assertEqual(tp_today['avg_talk_seconds'], 85)
        self.assertEqual(tp_today['outgoing_total'], 5)
        # ОП: 80 набранных, 30 поднятых — ни одна цифра не совпала с ТП
        self.assertEqual(op_today['outgoing_total'], 80)
        self.assertEqual(op_today['outgoing_success'], 30)
        self.assertEqual(op_today['outgoing_success_ratio'], 0.375)
        # Средний разговор ОП — по ИСХОДЯЩИМ: входящих у продаж нет вовсе
        self.assertEqual(op_today['avg_talk_seconds'], 50)
        self.assertEqual(op_today['avg_talk_seconds'], op_today['avg_outgoing_talk_seconds'])
        # Чужой номер 905 не попал ни в одну сумму
        for value in list(tp_today.values()) + list(op_today.values()):
            if isinstance(value, int):
                self.assertLess(value, 9000, f"в сумму затесался чужой номер кабинета: {value}")

    def test_empty_day_yields_none_not_zero(self):
        """Правило нуля: посчитать не из чего — прочерк. Ноль на стене читается как «всё хорошо»."""
        ns = self._namespace(raw=_raw_snapshot(zero_day=True))
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_fetch_snapshot']()
        tp_today, op_today = snapshot['tp']['today'], snapshot['op']['today']
        self.assertIsNone(tp_today['ar_ratio'])
        self.assertIsNone(tp_today['avg_talk_seconds'])
        self.assertIsNone(op_today['ar_ratio'])
        self.assertIsNone(op_today['avg_talk_seconds'])
        self.assertIsNone(op_today['avg_wait_seconds'])
        self.assertIsNone(op_today['outgoing_success_ratio'])
        for key in ('ar_ratio', 'avg_talk_seconds', 'outgoing_success_ratio'):
            self.assertNotEqual(op_today[key], 0, f"{key} обязан быть None, а не нулём")

    def test_unmatched_numbers_do_not_become_a_zero_day(self):
        """Сорванная привязка людей к линиям — это прочерк, а не «отдел не звонил»."""
        raw = _raw_snapshot()
        raw['employees']['employees'] = {
            '801': _employee('801', 'Никто Наш', _stats(outgoing_amount=10)),
        }
        ns = self._namespace(raw=raw)
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_fetch_snapshot']()
        op_today = snapshot['op']['today']
        self.assertIsNone(op_today['outgoing_total'])
        self.assertIsNone(op_today['served'])

    def test_fired_twin_with_the_same_extension_never_reaches_the_wall(self):
        """Внутренний номер в этом отделе НЕ уникален: 902 числится и за уволенным.

        На перерыве стоит номер 902. Просочись уволенный дубль в состав — на стене в списке
        «на перерыве» висела бы его фамилия вместо фамилии действующей сотрудницы."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_fetch_snapshot']()
        names = [item['name'] for item in snapshot['tp']['now']['break_list']]
        self.assertEqual(names, ['Дана Ким'])
        dumped = json.dumps(snapshot, ensure_ascii=False, default=str)
        self.assertNotIn('Уволенный', dumped)
        # И в диагностике его тоже нет — ни как «без sip», ни как неопознанного
        self.assertNotIn('Яков Уволенный',
                         snapshot['diagnostics']['operators_without_sip'])

    def test_department_roster_excludes_fired_statuses_in_sql(self):
        """Отсев уволенных живёт в SQL состава: фильтр обязан быть и там."""
        db = _FakeDb(operators_table=USERS_TABLE)
        rows = db.get_tez_wallboard_operators(TEZ_DEPARTMENT_ID, dt_date(2026, 9, 8))
        self.assertIn("LOWER(COALESCE(u.status, '')) <> ALL(%s)", db._operators.cursor.sql)
        self.assertIn('fired', TEZ_EXCLUDED_STATUSES)
        self.assertIn('dismissal', TEZ_EXCLUDED_STATUSES)
        self.assertNotIn('Яков Уволенный', [row['name'] for row in rows])
        # Чужой отдел в выборку не попадает даже с тем же внутренним номером
        self.assertNotIn('Чужой Оператор', [row['name'] for row in rows])
        # Стажёр остаётся: он на линии, и его пропажа выглядела бы как «не вышел»
        self.assertIn('Нурлан Абай', [row['name'] for row in rows])

    def test_people_without_a_model_are_not_on_the_wall(self):
        """У главы отдела и СВ модели расчёта нет — они не на линии и в счётчики не идут."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            people = ns['_tez_wallboard_people'](dt_date(2026, 9, 8))
        names = {row['name'] for row in people['tp'] + people['op']}
        self.assertNotIn('Глава Тез КЦ', names)
        self.assertEqual({row['name'] for row in people['tp']},
                         {'Аскар Тлеу', 'Дана Ким', 'Нурлан Абай'})
        self.assertEqual({row['name'] for row in people['op']},
                         {'Салтанат Ли', 'Ерлан Бек', 'Айгуль Сат'})

    def test_broken_queue_page_darkens_only_tp(self):
        """Очередь есть только у ТП: её поломка не имеет права снять со стены табло продаж."""
        ns = self._namespace(raw=_raw_snapshot(queue=False))
        with _patched_source(ns['_state']):
            with self.assertRaises(ns['TezWallboardDirectionUnavailable']) as ctx:
                ns['_tez_wallboard_direction_payload']('tp')
            op = ns['_tez_wallboard_direction_payload']('op')
        self.assertIn('очеред', str(ctx.exception).lower())
        self.assertEqual(op['now']['operators_total'], 3)
        self.assertEqual(op['today']['outgoing_total'], 80)
        # Вторая половина стоила того же одного обхода — повторно в кабинет не ходили
        self.assertEqual(ns['_state']['calls'], 1)

    def test_recall_list_is_always_empty(self):
        """«Перезвона» в Binotel не существует: список заведён пустым и обязан таким остаться."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_fetch_snapshot']()
        self.assertEqual(snapshot['tp']['now']['recall_list'], [])
        self.assertEqual(snapshot['op']['now']['recall_list'], [])
        for key in ('operators_on_recall', 'on_recall'):
            self.assertNotIn(key, snapshot['tp']['now'])
            self.assertNotIn(key, snapshot['op']['now'])

    def test_break_list_is_sorted_by_time_in_status(self):
        """Дольше всех в статусе — первым; неизвестное время уходит вниз, а не изображает ноль."""
        raw = _raw_snapshot()
        raw['queue']['operators'] = [
            {'name': 'Тлеу Аскар', 'number': '901', 'status_key': 'break', 'in_state_seconds': 30},
            {'name': 'Ким Дана', 'number': '902', 'status_key': 'break', 'in_state_seconds': None},
            {'name': 'Абай Нурлан', 'number': '903', 'status_key': 'break', 'in_state_seconds': 300},
        ]
        ns = self._namespace(raw=raw)
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_fetch_snapshot']()
        items = snapshot['tp']['now']['break_list']
        self.assertEqual([item['seconds'] for item in items], [300, 30, None])

    def test_snapshot_carries_no_credentials(self):
        """Сырой ответ кабинета несёт живые SIP login/password ВСЕХ сотрудников.

        Парсеры собирают новые словари по белому списку, но склейка на сервере могла бы
        протащить сырьё целиком. Ищем рекурсивно и по ключам, и по значению."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_fetch_snapshot']()
        banned = ('login', 'password', 'endpointdata', 'sippass')
        for path, key, _ in _walk_keys(snapshot):
            lowered = str(key).lower()
            for needle in banned:
                self.assertNotIn(needle, lowered,
                                 f"в снимке ключ с секретом: {'.'.join(path)}")
        dumped = json.dumps(snapshot, ensure_ascii=False, default=str)
        self.assertNotIn('S3CRET-PASSWORD', dumped)
        self.assertNotIn('sip-901', dumped)

    def test_name_list_copies_only_whitelisted_fields(self):
        """Строка списка собирается заново, а не расширяет строку источника.

        Отдельный тест, потому что штатный путь до этой функции уже промыт парсерами модуля:
        подмешай сюда `dict(row, …)` — и снимок понёс бы на стену всё, что кабинет положил
        в строку сотрудника, включая живую SIP-учётку."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            people = ns['_tez_wallboard_people'](dt_date(2026, 9, 8))
            out = ns['_tez_wallboard_name_list'](people, [{
                'name': 'Ким Дана', 'number': '902', 'reason': 'Перерыв', 'reason_key': 'break',
                'since': None, 'seconds': 60,
                'sipLogin': 'sip-902@binotel', 'sipPassword': 'S3CRET-PASSWORD',
                'clientPhone': '+7 777 000 00 00',
            }])
        self.assertEqual(len(out), 1)
        self.assertEqual(sorted(out[0]),
                         ['name', 'operator_id', 'reason', 'reason_key', 'seconds', 'since'])
        # Опознанного сотрудника называем НАШИМ именем: на стене висят фамилии из OTP
        self.assertEqual(out[0]['name'], 'Дана Ким')
        self.assertEqual(out[0]['operator_id'], 102)

    def test_direction_payload_keeps_the_thresholds_only_where_they_mean_something(self):
        """Порог SL и нормы AR — про очередь: у ОП их нет, и обещать их нельзя."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            tp = ns['_tez_wallboard_direction_payload']('tp')
            op = ns['_tez_wallboard_direction_payload']('op')
        self.assertEqual(tp['sl_threshold_seconds'], 20)
        self.assertEqual(tp['ar_target_percent'], ns['TEZ_AR_TARGET_PERCENT'])
        self.assertEqual(tp['ar_bad_percent'], ns['TEZ_AR_BAD_PERCENT'])
        for key in ('sl_threshold_seconds', 'ar_target_percent', 'ar_bad_percent'):
            self.assertNotIn(key, op)
        # Порог берём со страницы кабинета, а не из своей константы
        self.assertEqual(tp['sl_threshold_seconds'], ns['_state']['raw']['sl_threshold_seconds'])


# --- ПОИМЁННЫЙ СПИСОК -----------------------------------------------------------------------

class TezWallboardRosterTests(_SnapshotHarness, unittest.TestCase):
    """Строка на человека: статус из событий телефона, счётчики звонков из кабинета.

    Два источника в одной строке — не небрежность, а решение: у кабинета градаций статуса
    всего четыре, тренинг и техпауза схлопнуты в перерыв, а отдел продаж их не переключает
    вовсе. Тесты здесь стерегут именно стык: что статус приходит от телефона, что незнание
    не выдаётся за состояние человека и что чужие счётчики не приписываются соседу.
    """

    def _snapshot(self, live_statuses=None, *, users=None, live_status_error=False, raw=None):
        db = _FakeDb(operators_table=users if users is not None else USERS_TABLE,
                     live_statuses=live_statuses, live_status_error=live_status_error)
        ns = self._namespace(db=db, raw=raw)
        with _patched_source(ns['_state']):
            return ns['_tez_wallboard_fetch_snapshot'](), db

    def test_roster_covers_the_whole_direction(self):
        """В списке весь состав направления, а не только те, кого видно в кабинете."""
        snapshot, _ = self._snapshot()
        tp_names = [row['name'] for row in snapshot['tp']['roster']]
        op_names = [row['name'] for row in snapshot['op']['roster']]
        self.assertEqual(sorted(tp_names), ['Аскар Тлеу', 'Дана Ким', 'Нурлан Абай'])
        self.assertEqual(sorted(op_names), ['Айгуль Сат', 'Ерлан Бек', 'Салтанат Ли'])
        # Уволенный дубль с тем же номером 902 на стену не попадает и здесь.
        self.assertNotIn('Яков Уволенный', tp_names + op_names)

    def test_status_comes_from_the_phone_not_from_the_cabinet(self):
        """Кабинет говорит «перерыв», телефон — «готов». На стене должен быть телефон.

        Это и есть смысл всей затеи: у Дана Ким в фикстуре кабинета presence «Break in work»,
        и если бы список читал кабинет, она висела бы на перерыве, отвечая на звонки."""
        snapshot, _ = self._snapshot({102: {'status_key': 'готов', 'seconds': 300}})
        row = next(item for item in snapshot['tp']['roster'] if item['operator_id'] == 102)
        self.assertEqual((row['status_key'], row['status_label']), ('free', 'Активный'))
        self.assertEqual(row['status_seconds'], 300)
        # Плитки при этом по-прежнему считаются из кабинета — источники не перепутаны.
        self.assertEqual(snapshot['tp']['now']['operators_on_break'], 1)

    def test_every_phone_status_has_a_wall_label(self):
        """Все пять статусов пилюли плюс разговор и выход — словами самого телефона."""
        expected = {
            'готов': ('free', 'Активный'),
            'перезвон': ('outgoing', 'Исход'),
            'тренинг': ('training', 'Тренинг'),
            'перерыв': ('break', 'Перерыв'),
            'тех причина': ('tech', 'Техническая пауза'),
            'занят': ('talking', 'В разговоре'),
            'выключен': ('offline', 'Не в сети'),
        }
        ns = self._namespace()
        for status_key, (tone, label) in expected.items():
            got_label, got_tone, _ = ns['_tez_wallboard_status_entry'](status_key)
            self.assertEqual((got_tone, got_label), (tone, label), status_key)

    def test_tech_pause_is_recognised_in_any_spelling(self):
        """`тех причина`, `tech.break`, `тех_причина` — один и тот же статус телефона."""
        ns = self._namespace()
        for spelling in ('тех причина', 'Тех Причина', 'tech.break', 'tech_break', 'тех-причина'):
            label, tone, _ = ns['_tez_wallboard_status_entry'](spelling)
            self.assertEqual((tone, label), ('tech', 'Техническая пауза'), spelling)

    def test_unknown_status_key_is_shown_not_hidden(self):
        """Новый статус телефона приедет раньше табло: показываем как есть, а не «нет событий»."""
        ns = self._namespace()
        label, tone, weight = ns['_tez_wallboard_status_entry']('обед')
        self.assertEqual((tone, label), ('other', 'Обед'))
        self.assertLess(weight, ns['_TEZ_WALLBOARD_STATUS_UNKNOWN'][2])

    def test_person_without_events_is_not_offline(self):
        """Телефон молчит — это «нет событий», а не «человек вышел». Разница видна на стене."""
        snapshot, _ = self._snapshot({101: {'status_key': 'занят', 'seconds': 60}})
        silent = next(item for item in snapshot['tp']['roster'] if item['operator_id'] == 102)
        self.assertEqual((silent['status_key'], silent['status_label']), ('unknown', 'Нет событий'))
        self.assertIsNone(silent['status_seconds'])
        # И это же число видно в диагностике: пока флот обновляется, оно и есть мера готовности.
        self.assertEqual(snapshot['diagnostics']['operators_without_phone_events'], 5)

    def test_counters_come_from_the_cabinet_row_of_that_person(self):
        """Счётчики — из строки СВОЕГО номера: 901 принял 10, 902 — шесть, и не наоборот."""
        snapshot, _ = self._snapshot()
        by_id = {row['operator_id']: row for row in snapshot['tp']['roster']}
        self.assertEqual(by_id[101]['stats']['served'], 10)
        self.assertEqual(by_id[101]['stats']['missed'], 1)
        # Среднее усекаем, как и в плитках: 1000 / 10 = 100
        self.assertEqual(by_id[101]['stats']['avg_talk_seconds'], 100)
        self.assertEqual(by_id[101]['stats']['outgoing_total'], 5)
        self.assertEqual(by_id[101]['stats']['outgoing_success'], 3)
        self.assertEqual(by_id[102]['stats']['served'], 6)

    def test_op_row_carries_the_outgoing_numbers(self):
        """У продавца дневная величина — исходящие: набрано, дозвонились и время разговора."""
        snapshot, _ = self._snapshot()
        by_id = {row['operator_id']: row for row in snapshot['op']['roster']}
        self.assertEqual(by_id[201]['stats']['outgoing_total'], 50)
        self.assertEqual(by_id[201]['stats']['outgoing_success'], 20)
        self.assertEqual(by_id[201]['stats']['outgoing_talk_seconds'], 1000)
        # 1000 / 20 = 50 — среднее по ИСХОДЯЩИМ, входящих у отдела продаж нет вовсе.
        self.assertEqual(by_id[201]['stats']['avg_outgoing_talk_seconds'], 50)
        self.assertIsNone(by_id[203]['stats']['avg_outgoing_talk_seconds'])

    def test_person_unknown_to_the_cabinet_gets_dashes_not_zeros(self):
        """Номера нет в ответе кабинета — это сорванная привязка, а не «не сделал ни звонка»."""
        # Номер 907 в кабинете не заведён вовсе (905 там есть — это чужая линия с большими
        # счётчиками, и её нельзя путать с отсутствующей).
        users = USERS_TABLE + [
            {'id': 104, 'name': 'Новый Без Линии', 'sip_number': '907',
             'department_id': TEZ_DEPARTMENT_ID, 'group_model': 'tez_line',
             'direction_name': 'ТП линия', 'role': 'operator', 'status': 'active'},
        ]
        snapshot, _ = self._snapshot(users=users)
        row = next(item for item in snapshot['tp']['roster'] if item['operator_id'] == 104)
        self.assertIsNone(row['stats'])

    def test_rows_are_sorted_by_what_the_person_is_doing(self):
        """Сверху вниз — от работы к её отсутствию; внутри разряда дольше всех выше."""
        snapshot, _ = self._snapshot({
            101: {'status_key': 'перерыв', 'seconds': 600},
            102: {'status_key': 'занят', 'seconds': 30},
            103: {'status_key': 'готов', 'seconds': 120},
        })
        order = [row['status_key'] for row in snapshot['tp']['roster']]
        self.assertEqual(order, ['talking', 'free', 'break'])

        same_status, _ = self._snapshot({
            101: {'status_key': 'перерыв', 'seconds': 60},
            102: {'status_key': 'перерыв', 'seconds': 900},
            103: {'status_key': 'перерыв', 'seconds': 300},
        })
        seconds = [row['status_seconds'] for row in same_status['tp']['roster']]
        self.assertEqual(seconds, [900, 300, 60])

    def test_roster_is_asked_once_for_both_directions(self):
        """Статусы читаются одним запросом на оба направления, а не по разу на каждое."""
        _, db = self._snapshot()
        self.assertEqual(len(db.live_status_requests), 1)
        self.assertEqual(db.live_status_requests[0], [101, 102, 103, 201, 202, 203])

    def test_status_source_failure_does_not_darken_the_wall(self):
        """Упала база статусов — плитки из кабинета живы, а список теряет только разметку."""
        snapshot, _ = self._snapshot(live_status_error=True)
        self.assertIsNotNone(snapshot['tp'])
        self.assertEqual(snapshot['tp']['now']['operators_total'], 3)
        self.assertTrue(all(row['status_key'] == 'unknown' for row in snapshot['tp']['roster']))

    def test_roster_row_copies_only_whitelisted_fields(self):
        """В строку не должны просочиться ни внутренний номер, ни что-либо из ответа кабинета."""
        snapshot, _ = self._snapshot({101: {'status_key': 'готов', 'seconds': 10,
                                            'event_at': '2026-09-11T10:00:00'}})
        row = snapshot['tp']['roster'][0]
        self.assertEqual(set(row), {'operator_id', 'name', 'status_key', 'status_label',
                                    'status_seconds', 'stats'})
        self.assertEqual(set(row['stats']), {'served', 'missed', 'avg_talk_seconds',
                                             'outgoing_total', 'outgoing_success',
                                             'outgoing_talk_seconds', 'avg_outgoing_talk_seconds'})

    def test_roster_reaches_the_client(self):
        """Список должен доезжать до фронта: половина снимка без него бесполезна."""
        ns = self._namespace(db=_FakeDb(operators_table=USERS_TABLE,
                                        live_statuses={101: {'status_key': 'готов', 'seconds': 5}}))
        with _patched_source(ns['_state']):
            tp = ns['_tez_wallboard_direction_payload']('tp')
            op = ns['_tez_wallboard_direction_payload']('op')
        self.assertEqual(len(tp['roster']), 3)
        self.assertEqual(len(op['roster']), 3)


# --- ИСТОЧНИК СТАТУСОВ: НАСТОЯЩИЙ МЕТОД БАЗЫ ------------------------------------------------

class _EventsCursor:
    """Фальшивый курсор над operator_status_events: запоминает запрос, отдаёт заданные строки."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = re.sub(r'\s+', ' ', sql or '').strip()
        self.params = list(params or [])

    def fetchall(self):
        return list(self.rows)


class _RealLiveStatuses:
    """Настоящий `Database.get_operator_live_statuses` поверх фальшивого курсора."""

    OPERATOR_LIVE_STATUS_LOOKBACK_HOURS = _db_class_attr('OPERATOR_LIVE_STATUS_LOOKBACK_HOURS')
    _method = staticmethod(_load_db_method('get_operator_live_statuses'))
    # Без staticmethod: метод вызывается изнутри как `self._normalize_import_status_key(...)`,
    # то есть `self` ему нужен — обычная функция в атрибуте класса им и становится.
    _normalize_import_status_key = _load_db_method('_normalize_import_status_key')

    def __init__(self, rows=None):
        self.cursor = _EventsCursor(rows)

    def _get_cursor(self):
        return self.cursor

    def __call__(self, operator_ids, as_of=None, lookback_hours=None):
        return type(self)._method(self, operator_ids, as_of, lookback_hours)


class TezWallboardLiveStatusSourceTests(unittest.TestCase):
    """Откуда табло знает статус человека «сейчас»: последнее событие телефона в окне.

    Окно здесь не украшение. Событие трёхдневной давности — это не «человек до сих пор
    активен», а телефон, который умер, не прислав «выключен»; а по дате (`event_date`)
    выбирать нельзя вовсе — у ночной смены последнее событие лежит вчерашним числом.
    """

    NOW = datetime(2026, 9, 11, 12, 0, 0)

    def test_window_is_by_time_not_by_calendar_day(self):
        api = _RealLiveStatuses()
        api([101, 102], as_of=self.NOW)
        self.assertIn('WHERE e.operator_id = ANY(%s) AND e.event_at >= %s', api.cursor.sql)
        self.assertNotIn('event_date', api.cursor.sql)
        self.assertEqual(api.cursor.params[1],
                         self.NOW - timedelta(hours=api.OPERATOR_LIVE_STATUS_LOOKBACK_HOURS))

    def test_only_the_last_event_of_each_person_counts(self):
        """Статус — последнее событие, поэтому DISTINCT ON с сортировкой по времени вниз."""
        api = _RealLiveStatuses()
        api([101], as_of=self.NOW)
        self.assertIn('SELECT DISTINCT ON (e.operator_id)', api.cursor.sql)
        self.assertIn('ORDER BY e.operator_id, e.event_at DESC, e.id DESC', api.cursor.sql)

    def test_time_in_status_is_counted_from_the_event(self):
        api = _RealLiveStatuses([(101, 'готов', self.NOW - timedelta(minutes=7))])
        result = api([101], as_of=self.NOW)
        self.assertEqual(result[101]['status_key'], 'готов')
        self.assertEqual(result[101]['seconds'], 420)

    def test_event_from_the_future_does_not_become_negative_time(self):
        """Часы оператора могут спешить: минус на стене читался бы как поломка табло."""
        api = _RealLiveStatuses([(101, 'занят', self.NOW + timedelta(minutes=3))])
        result = api([101], as_of=self.NOW)
        self.assertEqual(result[101]['status_key'], 'занят')
        self.assertIsNone(result[101]['seconds'])

    def test_status_key_is_normalised(self):
        api = _RealLiveStatuses([(101, '  ГОТОВ  ', self.NOW - timedelta(minutes=1))])
        self.assertEqual(api([101], as_of=self.NOW)[101]['status_key'], 'готов')

    def test_person_without_events_is_absent_from_the_answer(self):
        """Отсутствие в ответе — это «не знаем», и отличать его от «вышел» обязан вызывающий."""
        api = _RealLiveStatuses([(101, 'готов', self.NOW)])
        self.assertEqual(set(api([101, 102], as_of=self.NOW)), {101})

    def test_empty_request_does_not_touch_the_database(self):
        api = _RealLiveStatuses()
        self.assertEqual(api([]), {})
        self.assertIsNone(api.cursor.sql)

    def test_aware_time_is_brought_to_almaty_not_to_utc(self):
        """Событие naive-локальное: приведи `as_of` к UTC — и «в статусе» уедет на часы."""
        api = _RealLiveStatuses([(101, 'перерыв', datetime(2026, 9, 11, 11, 55))])
        aware = datetime(2026, 9, 11, 12, 0, tzinfo=ZoneInfo('Asia/Almaty'))
        self.assertEqual(api([101], as_of=aware)[101]['seconds'], 300)


# --- КЭШ ------------------------------------------------------------------------------------

class TezWallboardCacheTests(_SnapshotHarness, unittest.TestCase):
    """Обвязка кэша: TTL, отдача устаревшего, пауза после ошибки, копия в БД."""

    def test_ttl_reuses_the_snapshot(self):
        ns = self._namespace()
        with _patched_source(ns['_state']):
            first = ns['_tez_wallboard_snapshot']()
            self.assertFalse(first['stale'])
            for _ in range(5):
                again = ns['_tez_wallboard_snapshot']()
                self.assertFalse(again['stale'])
        self.assertEqual(ns['_state']['calls'], 1)

    def test_stale_snapshot_served_when_the_cabinet_fails(self):
        """Табло на стене не должно гаснуть из-за одной ошибки кабинета."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            good = ns['_tez_wallboard_snapshot']()
            self.assertFalse(good['stale'])
            ns['_state']['error'] = RuntimeError("Binotel HTTP 502")
            ns['_tez_wallboard_cache']['ts'] = time.time() - 60
            stale = ns['_tez_wallboard_snapshot']()
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['tp']['today']['served'], 300)
        self.assertIn('502', stale['error'])
        self.assertGreaterEqual(stale['age_seconds'], 60)

    def test_failure_backoff_stops_hammering_the_cabinet(self):
        """Учётка у кабинета одна: долбить его после отказа нельзя, отдаём кэш сразу."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            ns['_tez_wallboard_snapshot']()
            calls_after_success = ns['_state']['calls']
            ns['_state']['error'] = RuntimeError("Binotel connect timeout")
            ns['_tez_wallboard_cache']['ts'] = time.time() - 60
            first = ns['_tez_wallboard_snapshot']()
            self.assertTrue(first['stale'])
            self.assertEqual(ns['_state']['calls'], calls_after_success + 1)
            for _ in range(5):
                again = ns['_tez_wallboard_snapshot']()
                self.assertTrue(again['stale'])
            self.assertEqual(ns['_state']['calls'], calls_after_success + 1,
                             "во время паузы новых обходов кабинета быть не должно")

    def test_lock_is_released_even_when_the_cabinet_fails(self):
        """Иначе первая же ошибка навсегда заблокировала бы обновление снимка."""
        ns = self._namespace(error=RuntimeError("Binotel HTTP 500"))
        with _patched_source(ns['_state']):
            with self.assertRaises(RuntimeError):
                ns['_tez_wallboard_snapshot']()
        self.assertTrue(ns['_tez_wallboard_lock'].acquire(blocking=False), "лок не отпущен")
        ns['_tez_wallboard_lock'].release()

    def test_second_viewer_does_not_queue_behind_a_slow_refresh(self):
        """Обход кабинета стоит ~2,3 с: второй зритель получает кэш, а не ждёт в очереди."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            ns['_tez_wallboard_snapshot']()
            ns['_tez_wallboard_cache']['ts'] = time.time() - 60
            ns['_tez_wallboard_lock'].acquire()
            try:
                started = time.time()
                result = ns['_tez_wallboard_snapshot']()
                waited = time.time() - started
            finally:
                ns['_tez_wallboard_lock'].release()
        self.assertTrue(result['stale'])
        self.assertIn('Обновление', result['error'])
        self.assertLess(waited, ns['TEZ_WALLBOARD_LOCK_WAIT_SECONDS'] + 2)

    def test_snapshot_older_than_stale_max_is_not_served(self):
        """Данные четвертьчасовой давности на стене опаснее честного «данных нет»."""
        ns = self._namespace()
        with _patched_source(ns['_state']):
            ns['_tez_wallboard_snapshot']()
            ns['_state']['error'] = RuntimeError("Binotel HTTP 500")
            ns['_tez_wallboard_cache']['ts'] = time.time() - (ns['TEZ_WALLBOARD_STALE_MAX_SECONDS'] + 5)
            with self.assertRaises(RuntimeError):
                ns['_tez_wallboard_snapshot']()

    def test_persisted_row_belongs_to_the_tez_direction(self):
        """Ключ строки в БД — направление. Запишем 'osnova' — табло затрут друг друга."""
        db = _FakeDb(operators_table=USERS_TABLE)
        ns = self._namespace(db=db)
        with _patched_source(ns['_state']):
            ns['_tez_wallboard_snapshot']()
        self.assertEqual(len(db.saved_snapshots), 1)
        payload, captured_at, direction = db.saved_snapshots[0]
        self.assertEqual(direction, 'tez')
        self.assertNotEqual(direction, 'osnova')
        self.assertEqual(payload['tp']['today']['served'], 300)
        self.assertEqual(db.snapshot_reads, ['tez'])

    def test_persist_happens_once_per_interval(self):
        """Копия нужна только на холодный старт — пишем не на каждый опрос."""
        db = _FakeDb(operators_table=USERS_TABLE)
        ns = self._namespace(db=db)
        with _patched_source(ns['_state']):
            ns['_tez_wallboard_snapshot']()
            ns['_tez_wallboard_cache']['ts'] = time.time() - (ns['TEZ_WALLBOARD_CACHE_TTL_SECONDS'] + 1)
            ns['_tez_wallboard_snapshot']()
        self.assertEqual(ns['_state']['calls'], 2)
        self.assertEqual(len(db.saved_snapshots), 1)

    def test_cold_start_restores_the_tez_row_not_someone_elses(self):
        """Рестарт в момент недоступности кабинета: экран поднимается из БД, и именно свой."""
        saved_tez = {'tp': {'today': {'served': 111}, 'now': {}},
                     'op': {'today': {}, 'now': {}}}
        saved_szov = {'today': {'served': 999}, 'now': {}}
        db = _FakeDb(operators_table=USERS_TABLE, snapshots={
            'tez': (saved_tez, time.time() - 30),
            'osnova': (saved_szov, time.time() - 5),
        })
        ns = self._namespace(db=db, error=RuntimeError("Binotel недоступен"))
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_snapshot']()
        self.assertTrue(snapshot['stale'])
        self.assertEqual(snapshot['tp']['today']['served'], 111)
        self.assertGreaterEqual(snapshot['age_seconds'], 30)
        self.assertEqual(db.snapshot_reads, ['tez'])

    def test_cold_start_ignores_a_snapshot_older_than_the_stale_window(self):
        db = _FakeDb(operators_table=USERS_TABLE, snapshots={
            'tez': ({'tp': {'today': {'served': 111}, 'now': {}}, 'op': None},
                    time.time() - 4000),
        })
        ns = self._namespace(db=db, error=RuntimeError("Binotel недоступен"))
        with _patched_source(ns['_state']):
            with self.assertRaises(RuntimeError):
                ns['_tez_wallboard_snapshot']()

    def test_db_failure_never_breaks_the_live_wallboard(self):
        """Ни чтение, ни запись копии не имеют права уронить живое табло."""
        db = _FakeDb(operators_table=USERS_TABLE, snapshot_error=True)
        ns = self._namespace(db=db)
        with _patched_source(ns['_state']):
            snapshot = ns['_tez_wallboard_snapshot']()
        self.assertFalse(snapshot['stale'])
        self.assertEqual(snapshot['tp']['today']['served'], 300)

    def test_restore_reads_the_db_only_once_per_process(self):
        """Пустая строка снимка не должна превращаться в запрос к БД на каждый опрос."""
        db = _FakeDb(operators_table=USERS_TABLE)
        ns = self._namespace(db=db, error=RuntimeError("Binotel недоступен"))
        with _patched_source(ns['_state']):
            for _ in range(3):
                with contextlib.suppress(RuntimeError):
                    ns['_tez_wallboard_snapshot']()
        self.assertEqual(db.snapshot_reads, ['tez'])


# --- ПОРОГИ ---------------------------------------------------------------------------------

class TezArCeilingToneTests(unittest.TestCase):
    """AR у Тез — ПОТОЛОК, а не коридор. Проверяем НАСТОЯЩИЙ js через node."""

    @classmethod
    def setUpClass(cls):
        cls.tez = (MONITORING / "tezWallboardShared.js").read_text(encoding="utf-8-sig")
        cls.szov = (MONITORING / "szovWallboardShared.js").read_text(encoding="utf-8-sig")
        if shutil.which("node") is None:
            raise unittest.SkipTest("node недоступен")

    def _run_tone(self, source, fn_name, const_prefix, expected_consts, values, divisor=100):
        """Вырезает пороги и функцию тона из настоящего модуля и гоняет их настоящим node."""
        consts = re.findall(rf"^(?:export )?const {const_prefix}[A-Z_]+ = [\d.]+;$", source,
                            flags=re.MULTILINE)
        self.assertEqual(len(consts), expected_consts,
                         f"ожидались {expected_consts} константы порогов {const_prefix}")
        fn = re.search(rf"^(?:export )?const {fn_name} = \(ratio\) => \{{.*?^\}};$", source,
                       flags=re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(fn, f"не нашли функцию {fn_name}")
        script = "\n".join(consts) + "\n" + fn.group(0) + "\n" + (
            f"console.log(JSON.stringify({json.dumps(values)}"
            f".map((p) => {fn_name}(p === null ? null : p / {divisor}))));")
        with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            out = subprocess.run([shutil.which("node"), path], capture_output=True,
                                 text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            return json.loads(out.stdout.strip())
        finally:
            os.unlink(path)

    def _ceiling(self, percents):
        return self._run_tone(self.tez, "arCeilingTone", "TEZ_AR_", 2, percents)

    def _corridor(self, percents):
        return self._run_tone(self.szov, "arTone", "AR_", 2, percents)

    def test_ceiling_thresholds(self):
        """Норма «не выше 5 %»: до 5 зелёный, 5-7 жёлтый, выше 7 красный."""
        cases = [
            (0.0, 'good'), (1.0, 'good'), (3.0, 'good'), (4.99, 'good'), (5.0, 'good'),
            (5.01, 'warn'), (6.9, 'warn'), (7.0, 'warn'),
            (7.01, 'bad'), (12.0, 'bad'), (28.6, 'bad'),
        ]
        got = self._ceiling([percent for percent, _ in cases])
        for (percent, expected), actual in zip(cases, got):
            self.assertEqual(actual, expected, f"AR {percent}% -> {actual}, ожидали {expected}")

    def test_missing_ar_is_neutral(self):
        """Звонков ещё не было — красить нечего: зелёная плитка обещала бы порядок вслепую."""
        self.assertEqual(self._ceiling([None]), ['neutral'])

    def test_ceiling_is_not_the_szov_corridor(self):
        """У СЗоВ 1 % красный (перезаложены операторы), у Тез — норма. Функции разные намеренно."""
        self.assertEqual(self._ceiling([1.0]), ['good'])
        self.assertEqual(self._corridor([1.0]), ['bad'])
        # И наоборот: у СЗоВ выше 5 % сразу красный, промежуточного тона там нет вовсе
        self.assertEqual(self._corridor([6.0]), ['bad'])
        self.assertEqual(self._ceiling([6.0]), ['warn'])
        self.assertIn('arCeilingTone', self.tez)
        self.assertNotIn('export const arTone', self.tez)

    def test_backend_and_frontend_thresholds_agree(self):
        """Сервер кладёт нормы в снимок, фронт красит по своим — разъедутся, и цвет соврёт.

        Префикс именно TEZ_AR_: под TEZ_ попали бы шаг опроса и порог SL, и страж сравнивал
        бы величины, у которых нет общего смысла."""
        backend = dict(re.findall(r"^(TEZ_AR_[A-Z_]+) = ([\d.]+)$", BOT_SOURCE, flags=re.MULTILINE))
        frontend = dict(re.findall(r"^export const (TEZ_AR_[A-Z_]+) = ([\d.]+);$", self.tez,
                                   flags=re.MULTILINE))
        self.assertEqual(set(backend), {'TEZ_AR_TARGET_PERCENT', 'TEZ_AR_BAD_PERCENT'})
        self.assertEqual(set(frontend), set(backend))
        for name, value in backend.items():
            self.assertEqual(float(value), float(frontend[name]), name)
        self.assertEqual(float(backend['TEZ_AR_TARGET_PERCENT']), 5)
        self.assertEqual(float(backend['TEZ_AR_BAD_PERCENT']), 7)


# --- РАЗВОДКА -------------------------------------------------------------------------------

def _strip_js_comments(source):
    """Код без комментариев: пояснения про «Перезвон» — это объяснение, а не плитка."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)


class TezWallboardWiringTests(unittest.TestCase):
    """Раздел должен быть подключён во всех точках App.jsx, иначе он просто не откроется."""

    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
        cls.shared = (MONITORING / "tezWallboardShared.js").read_text(encoding="utf-8-sig")
        cls.view = (MONITORING / "TezWallboardView.jsx").read_text(encoding="utf-8-sig")
        cls.tp = (MONITORING / "TezTpWallboard.jsx").read_text(encoding="utf-8-sig")
        cls.op = (MONITORING / "TezOpWallboard.jsx").read_text(encoding="utf-8-sig")
        cls.roster = (MONITORING / "TezOperatorsTable.jsx").read_text(encoding="utf-8-sig")
        cls.szov_shared = (MONITORING / "szovWallboardShared.js").read_text(encoding="utf-8-sig")
        cls.faicon = (ROOT / "src" / "components" / "common" / "FaIcon.jsx").read_text(encoding="utf-8-sig")
        cls.new_files = {
            'tezWallboardShared.js': cls.shared,
            'TezWallboardView.jsx': cls.view,
            'TezTpWallboard.jsx': cls.tp,
            'TezOpWallboard.jsx': cls.op,
            'TezOperatorsTable.jsx': cls.roster,
        }

    def test_backend_routes_are_registered_and_guarded(self):
        for direction in ('tp', 'op'):
            self.assertIn(
                f"@app.route('/api/tez_wallboard/{direction}_snapshot', methods=['GET', 'OPTIONS'])",
                BOT_SOURCE)
            self.assertIn(f"def api_tez_wallboard_{direction}_snapshot():", BOT_SOURCE)
        handler = BOT_SOURCE[BOT_SOURCE.index("def _api_tez_wallboard_direction("):
                             BOT_SOURCE.index("@app.route('/api/tez_wallboard/tp_snapshot'")]
        self.assertIn("requester_id, err = _tez_wallboard_guard()", handler)
        # OPTIONS обязателен: фронт живёт на другом origin
        self.assertIn("if request.method == 'OPTIONS':", handler)
        self.assertIn("return _build_cors_preflight_response()", handler)
        # Отказ половины табло отвечает предметно, а не «всё упало»
        self.assertIn("except TezWallboardDirectionUnavailable as exc:", handler)

    def test_all_wiring_points_present(self):
        self.assertIn(
            "const TezWallboardView = lazyWithRetry(() => import('./components/monitoring/TezWallboardView'));",
            self.app)
        self.assertIn("const canAccessTezWallboardSection = canAccessTezWallboardForUser(user);", self.app)
        self.assertIn("handleSidebarViewNavigation(e, 'tez_wallboard')", self.app)
        self.assertIn('{view === "tez_wallboard" && canAccessTezWallboardSection && (', self.app)
        self.assertIn("tez_wallboard: 'Tez KC wallboard',", self.app)

    def test_frontend_gate_repeats_the_backend_ladder(self):
        self.assertIn("const TEZ_WALLBOARD_DEPARTMENT_CODE = 'tez';", self.app)
        self.assertIn("const canAccessTezWallboardForUser = (userLike) => {", self.app)
        self.assertIn("isTezWallboardDepartmentHead(userLike)", self.app)
        # Тот же намеренный вырез, что у табло СЗоВ: глава чужого отдела не проходит как админ
        self.assertIn("if (role === 'admin' && !isDepartmentHead(userLike)) return true;", self.app)
        # Предикат СВОЙ, а не переиспользованный от СЗоВ: сузят однажды табло СЗоВ —
        # табло Тез не должно молча потерять тех же людей.
        self.assertNotIn("canAccessTezWallboardSection = canAccessSzovWallboardForUser", self.app)
        self.assertNotIn("const canAccessTezWallboardForUser = canAccessSzovWallboardForUser", self.app)

    def test_sidebar_item_present_in_both_branches(self):
        """Одна ветка = раздел открывается только по прямому адресу: пункта не видно половине."""
        self.assertEqual(self.app.count("handleSidebarViewNavigation(e, 'tez_wallboard')"), 2)
        self.assertEqual(self.app.count('<span className="sidebar-text">Табло Тез КЦ</span>'), 2)
        self.assertEqual(self.app.count("{canAccessTezWallboardSection && ("), 2)

    def test_three_redirect_guards_do_not_bounce_the_view(self):
        self.assertIn("(requestedViewFromUrl !== 'tez_wallboard' || canAccessTezWallboardSection)", self.app)
        self.assertIn("if (view === 'tez_wallboard' && !canAccessTezWallboardSection) {", self.app)
        # allowlist отдела не должен уводить с раздела — у него собственный предикат
        self.assertIn("if (view === 'tez_wallboard' && canAccessTezWallboardSection) return;", self.app)

    def test_polling_lives_only_in_the_shared_feed(self):
        """Опрос — через общий createSnapshotFeed: свой таймер разъедется с чужим на первой правке."""
        for name, source in self.new_files.items():
            self.assertNotIn("setInterval(", source, name)
        self.assertIn("createSnapshotFeed({", self.shared)
        self.assertEqual(self.shared.count("createSnapshotFeed({"), 2)
        self.assertIn("from './szovWallboardShared'", self.shared)

    def test_each_endpoint_path_is_written_exactly_once(self):
        """Адрес ручки живёт в одном месте — в описании фида, а не по экранам."""
        for path in ("'/api/tez_wallboard/tp_snapshot'", "'/api/tez_wallboard/op_snapshot'"):
            total = sum(source.count(path) for source in self.new_files.values())
            self.assertEqual(total, 1, path)
        for name, source in self.new_files.items():
            if name != 'tezWallboardShared.js':
                self.assertNotIn('/api/tez_wallboard/', source, name)

    def test_section_title_is_its_own(self):
        """Копипаста из табло СЗоВ на стене выглядит как чужой раздел."""
        self.assertIn('Табло Тез КЦ</h1>', self.view)
        for name, source in self.new_files.items():
            self.assertNotIn('Табло СЗоВ<', source, name)
            self.assertNotIn('>Табло СЗоВ', source, name)

    def test_direction_storage_key_is_its_own(self):
        """Чужое 'tez_op' в ключе СЗоВ означало бы молча открытую «Линию» в чужом разделе."""
        self.assertIn("`otp:tez-wallboard-direction${userId ? `:${userId}` : \'\'}`", self.view)
        for name, source in self.new_files.items():
            # Комментарии снимаем: в них ключ СЗоВ назван как раз затем, чтобы его не трогали.
            code = _strip_js_comments(source)
            self.assertNotIn('otp:szov-wallboard-direction', code, name)
            self.assertNotIn('otp:szov-wallboard-widget', code, name)

    def test_catalog_keys_are_unique_and_do_not_clash_with_szov(self):
        """«Онлайн» есть у обоих направлений: общий ключ молча затёр бы один другим."""
        # Только каталоги показателей: реестр направлений ниже по файлу тоже пишет `key:`,
        # но его ключи ('tez_tp'/'tez_op') — адрес экрана, а не показатель.
        catalog = self.shared[self.shared.index('export const TEZ_TP_METRICS'):
                              self.shared.index('export const TEZ_OP_METRIC_MAP')]
        keys = re.findall(r"^        key: '(\w+)',$", catalog, flags=re.MULTILINE)
        self.assertEqual(len(keys), len(set(keys)), "дубли ключей в каталогах Тез")
        tp_keys = {key for key in keys if key.startswith('tp_')}
        op_keys = {key for key in keys if key.startswith('op_')}
        self.assertEqual(tp_keys | op_keys, set(keys), "ключ без префикса направления")
        self.assertEqual(tp_keys & op_keys, set())
        szov_keys = set(re.findall(r"^        key: '(\w+)',$", self.szov_shared, flags=re.MULTILINE))
        self.assertTrue(szov_keys, "каталог СЗоВ прочитан пустым — страж пересечения ничего не ловит")
        self.assertEqual(szov_keys & set(keys), set(), "ключ каталога Тез совпал с каталогом СЗоВ")
        # Каждая нарисованная плитка есть в каталоге, и наборы по умолчанию тоже
        for source, catalog in ((self.tp, tp_keys), (self.op, op_keys)):
            for key in re.findall(r'metricKey="([^"]+)"', source):
                self.assertIn(key, catalog, key)
        for key in re.findall(r"^    '(\w+)',$", self.shared, flags=re.MULTILINE):
            self.assertIn(key, keys, f"набор по умолчанию ссылается на несуществующий {key}")

    def test_recall_is_absent_from_both_screens(self):
        """«Перезвона» в Binotel нет вовсе: плитка или колонка с ним врали бы каждую минуту."""
        for name, source in self.new_files.items():
            code = _strip_js_comments(source)
            self.assertNotIn('Перезвон', code, name)
            self.assertNotIn('recall', code.lower(), name)

    def test_op_has_no_queue_metrics_at_all(self):
        """У ОП входящих нет: «Принято», «Потеряно», AR, SL и ожидание — не прочерк, а отсутствие."""
        op_block = self.shared[self.shared.index('export const TEZ_OP_METRICS'):
                               self.shared.index('export const TEZ_OP_METRIC_MAP')]
        for absent in ("'В очереди'", "'AR'", "'SL'", "'Ср. ожидание'", "'Потеряно'"):
            self.assertNotIn(absent, op_block, absent)
        for key in ('sl_ratio', 'ar_ratio', 'avg_wait_seconds', 'queue'):
            self.assertNotIn(f'today.{key}', op_block, key)
        self.assertNotIn('metricKey="op_sl"', self.op)

    def test_both_directions_show_the_named_list(self):
        """Список людей стоит на обоих табло и знает своё направление — колонки у них разные."""
        self.assertIn('import TezOperatorsTable from \'./TezOperatorsTable\';', self.tp)
        self.assertIn('import TezOperatorsTable from \'./TezOperatorsTable\';', self.op)
        self.assertIn('<TezOperatorsTable rows={snapshot?.roster} direction="tp"', self.tp)
        self.assertIn('<TezOperatorsTable rows={snapshot?.roster} direction="op"', self.op)

    def test_every_status_the_backend_emits_has_a_chip(self):
        """Ключ оформления без стиля — серый чип без смысла: каталоги обязаны совпадать.

        Сервер выдаёт ключ разряда (`free`, `talking`, …), фронт по нему берёт цвет. Разойдись
        они — на стене молча появится безымянный серый статус, и заметят это не сразу."""
        catalog = BOT_SOURCE[BOT_SOURCE.index("_TEZ_WALLBOARD_STATUS_CATALOG = {"):
                             BOT_SOURCE.index("_TEZ_WALLBOARD_STATUS_UNKNOWN = ")]
        tones = set(re.findall(r"\('[^']+', '([a-z]+)', \d+\)", catalog))
        self.assertTrue(tones)
        # Плюс два разряда, которых в каталоге нет: незнание и незнакомый ключ.
        for tone in sorted(tones | {'unknown', 'other'}):
            self.assertIn(f"    {tone}: {{", self.shared, tone)

    def test_op_named_list_has_no_incoming_columns(self):
        """У отдела продаж входящих нет вовсе: «Принято» и «Пропущено» там были бы нулями."""
        op_columns = self.roster[self.roster.index("    op: ["):self.roster.index("};")]
        for forbidden in ('Принято', 'Пропущено'):
            self.assertNotIn(forbidden, op_columns)
        for expected in ('Набрано', 'Дозвонились'):
            self.assertIn(expected, op_columns)

    def test_every_fa_token_of_the_new_files_is_mapped(self):
        """tests/test_faicon_mappings.py падает молча, если токен не замаплен — икона исчезает."""
        tokens = set()
        for source in self.new_files.values():
            tokens.update(re.findall(r"fa-[a-z0-9-]+", source))
        self.assertTrue(tokens)
        for token in sorted(tokens):
            self.assertIn(f"'{token}'", self.faicon, token)


if __name__ == '__main__':
    unittest.main()
