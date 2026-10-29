# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

ID водителя у базы «Удаленного КЦ» (владелец, 29.10.2026: «в детализацию и в отчеты добавить id и иин
водителя») на настоящей схеме раздела: сверка со списком Sapar (dial_list/driver_ids.py) — окно баз, «не
знает» против «не спрашивали», ИИН, сменившийся посреди сверки; прогон проверки подписания, который её
зовёт, и пауза после отказа Sapar; загрузка базы, сбрасывающая ID при смене ИИН. Sapar подменён. Запуск:

    DIAL_LIST_TEST_PORT=55461 python -m pytest tests/test_dial_list_driver_ids_postgres.py

Люди, номера, ИИН и ID выдуманы.
"""
import contextlib
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import driver_ids  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list.sign_check import SignChecker  # noqa: E402
from sign_links import iin as iin_rules  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

RCC, TEZ = 1954, 560
ALMATY = timezone(timedelta(hours=5))
TODAY = date(2026, 10, 29)
NOW = datetime(2026, 10, 29, 12, 20, tzinfo=ALMATY)
OCT, SEP, AUG = date(2026, 10, 1), date(2026, 9, 1), date(2026, 8, 1)
A, B, C, D = 'a' * 32, 'b' * 32, 'c' * 32, 'd' * 32

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint);
    INSERT INTO departments (id, name, code) VALUES (560, 'Тез КЦ', 'tez'), (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO users (id, name, login, role, department_id) VALUES (2, 'Админова Анна', 'anna', 'admin', NULL);
'''


def iin(n):
    """Настоящий по контрольной цифре ИИН (загрузка базы другие не берёт): 1988-03-17 + номер."""
    for tail in range(10):
        first = f'8803173{n:03d}{tail}'
        check = iin_rules._checksum(first)
        if check is not None:
            return first + str(check)
    raise AssertionError(n)


class _Db:
    def __init__(self, schema):
        self.schema = schema

    def connect(self):
        import psycopg2
        conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres',
                                connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute('SET search_path TO ' + self.schema)
            cur.execute("SET statement_timeout TO '10s'")
        conn.commit()
        return conn

    @contextlib.contextmanager
    def _get_cursor(self):
        conn = self.connect()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def one(self, sql, params=None):
        with self._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def lead(self, department, phone, iin_value, period=OCT, ids=None):
        return str(self.one("INSERT INTO dial_list_leads (department_id, phone_norm, full_name, iin, period, "
                            "yandex_driver_ids) VALUES (%s, %s, 'Водитель', %s, %s, %s) RETURNING id",
                            (department, phone, iin_value, period, ids))[0])

    def ids(self, lead_id):
        return self.one("SELECT yandex_driver_ids FROM dial_list_leads WHERE id = %s", (lead_id,))[0]


@pytest.fixture
def db():
    schema = 't_dial_ids_' + uuid.uuid4().hex
    base = _Db(schema)
    admin = base.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


class _Sapar:
    """Подменённый список Sapar: {ИИН: [ID, …]}; ok=False — отказ."""

    def __init__(self, known, ok=True, during=None):
        self.known, self.ok, self.during = known, ok, during
        self.calls = []

    def __call__(self, month, year, wanted=None):
        self.calls.append((month, year, set(wanted or ())))
        if self.during:
            self.during()
        if not self.ok:
            return {'ok': False, 'ids': {}, 'listed': 0, 'error': 'Sapar лежит', 'systemic': True}
        ids = {k: v for k, v in self.known.items() if wanted is None or k in wanted}
        return {'ok': True, 'ids': ids, 'listed': len(self.known), 'error': None, 'systemic': False}


def test_column_is_added_without_default(db):
    """Колонка появляется пустой (NULL) у всех строк — первая сверка после выкладки спросит всех."""
    row = db.one("SELECT is_nullable, column_default FROM information_schema.columns "
                 "WHERE table_schema = %s AND table_name = 'dial_list_leads' AND column_name = 'yandex_driver_ids'",
                 (db.schema,))
    assert row == ('YES', None)


def test_sync_writes_ids_unknown_and_keeps_old(db):
    two = db.lead(RCC, '77010000001', iin(1))                     # два аккаунта, Sapar отдал не по алфавиту
    unknown = db.lead(RCC, '77010000002', iin(2))                 # Sapar не знает → '' (спросили)
    september = db.lead(RCC, '77010000003', iin(3), period=SEP)   # база прошлого месяца — тоже в окне
    august = db.lead(RCC, '77010000004', iin(4), period=AUG)      # позапрошлого — вне окна
    kept = db.lead(RCC, '77010000005', iin(5), ids=D)             # пропал из списка — прежний ID остаётся
    same = db.lead(RCC, '77010000006', iin(6), ids=A)             # то же значение — строку не переписываем
    tez = db.lead(TEZ, '77010000007', '')                         # автообзвон Тез: ИИН нет — Sapar не про него
    sapar = _Sapar({iin(1): [B, A], iin(3): [C], iin(4): [C], iin(6): [A]})

    summary = driver_ids.sync(db, sapar, TODAY)

    assert sapar.calls == [(9, 2026, {iin(n) for n in (1, 2, 3, 5, 6)})]
    assert summary == {'ok': True, 'leads': 5, 'known': 3, 'unknown': 2, 'updated': 3, 'error': ''}
    assert db.ids(two) == f'{A}, {B}'
    assert db.ids(unknown) == ''
    assert db.ids(september) == C
    assert db.ids(august) is None
    assert db.ids(kept) == D
    assert db.ids(same) == A
    assert db.ids(tez) is None
    # Спрашивать больше некого — в Sapar не ходим.
    assert driver_ids.sync(db, sapar, TODAY) is None and len(sapar.calls) == 1
    # Принудительная сверка — спрашивает, но ничего не переписывает.
    assert driver_ids.sync(db, sapar, TODAY, force=True)['updated'] == 0


def test_refusal_writes_nothing(db):
    lead = db.lead(RCC, '77010000001', iin(1))
    summary = driver_ids.sync(db, _Sapar({iin(1): [A]}, ok=False), TODAY)
    assert (summary['ok'], summary['updated'], summary['error']) == (False, 0, 'Sapar лежит')
    # Неспрошенный остаётся неспрошенным, а не «Sapar не знает».
    assert db.ids(lead) is None


def test_iin_changed_during_sync_gets_no_foreign_id(db):
    lead = db.lead(RCC, '77010000001', iin(1))

    def reupload():
        db.one("UPDATE dial_list_leads SET iin = %s WHERE id = %s RETURNING id", (iin(9), lead))

    driver_ids.sync(db, _Sapar({iin(1): [A]}, during=reupload), TODAY)
    # ID был бы прежнего ИИН — не пишем; новый ИИН спросит следующая сверка.
    assert db.ids(lead) is None


def test_upload_with_another_iin_resets_the_id(db):
    svc = dial_service.DialListService(db)
    svc.import_leads(RCC, 2, 'база.xlsx', [(2, 'Иванов Иван', '87010000001', '77010000001', iin(1))], period=OCT)
    lead = db.one("SELECT id FROM dial_list_leads WHERE phone_norm = '77010000001'")[0]
    driver_ids.sync(db, _Sapar({iin(1): [A]}), TODAY)
    assert db.ids(lead) == A
    # Тот же ИИН ещё раз — ID на месте.
    svc.import_leads(RCC, 2, 'база.xlsx', [(2, 'Иванов Иван', '87010000001', '77010000001', iin(1))], period=OCT)
    assert db.ids(lead) == A
    # Другой ИИН у того же номера — ID прежнего водителя стирается до следующей сверки.
    svc.import_leads(RCC, 2, 'база.xlsx', [(2, 'Иванов Иван', '87010000001', '77010000001', iin(2))], period=OCT)
    assert db.ids(lead) is None
    driver_ids.sync(db, _Sapar({iin(1): [A], iin(2): [B]}), TODAY)
    assert db.ids(lead) == B


def _checker(db, sapar):
    no_docs = lambda iin_value, month, year, session=None, timeout=None: {'ok': True, 'yandex': [], 'park': []}  # noqa: E731
    return SignChecker(db, fetch=no_docs, configured=lambda: True, sleep=lambda _s: None, fetch_ids=sapar)


def test_sign_check_run_syncs_ids_first_and_waits_after_refusal(db):
    first = db.lead(RCC, '77010000001', iin(1))
    sapar = _Sapar({iin(1): [A], iin(2): [B]})
    checker = _checker(db, sapar)

    summary = checker.run(reason='планировщик', now=NOW)
    assert summary['checked'] == 1 and db.ids(first) == A
    assert checker.last_ids_sync['updated'] == 1
    # Следующий прогон: спрашивать некого — в Sapar за списком не ходим.
    checker.run(reason='планировщик', now=NOW)
    assert len(sapar.calls) == 1

    # Новая загрузка, а Sapar отказал — пауза: прогоны подписания идут, список не спрашивают.
    second = db.lead(RCC, '77010000002', iin(2))
    sapar.ok = False
    checker.run(reason='загрузка базы', now=NOW)
    checker.run(reason='планировщик', now=NOW)
    assert len(sapar.calls) == 2 and db.ids(second) is None
    # Пауза вышла — сверка снова идёт.
    sapar.ok = True
    checker._ids_retry_at = 0.0
    checker.run(reason='планировщик', now=NOW)
    assert len(sapar.calls) == 3 and db.ids(second) == B


def test_single_driver_check_does_not_fetch_the_list(db):
    lead = db.lead(RCC, '77010000001', iin(1))
    sapar = _Sapar({iin(1): [A]})
    _checker(db, sapar).run(reason='карточка водителя', now=NOW, lead_ids=[lead], force=True)
    assert sapar.calls == [] and db.ids(lead) is None


def test_broken_sync_does_not_stop_the_sign_check(db):
    db.lead(RCC, '77010000001', iin(1))

    def boom(month, year, wanted=None):
        raise RuntimeError('ответ не разобрался')

    checker = _checker(db, boom)
    assert checker.run(reason='планировщик', now=NOW)['checked'] == 1
    assert checker.last_ids_sync == {'ok': False, 'error': 'ответ не разобрался'}
