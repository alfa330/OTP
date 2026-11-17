# -*- coding: utf-8 -*-
"""Точечная обработка базы на настоящем PostgreSQL (владелец, 17.11.2026: «удобный выбор загруженных баз на
точечную обработку, с возможностью выбрать несколько и отображать прогресс по каждой базе»; решения: оба отдела,
операторы получают водителей только из отмеченных файлов, ничего не отмечено — вся база месяца).

Что проверяется: пул выдачи, «доступно» у телефона и сводки, этап «База на паузе» в журнале, прогресс каждого
файла, отметки только в обзваниваемом месяце, уже выданные списки и свои перезвоны Тез — мимо отметок.

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем, в конце она
удаляется. Binotel и TEZ APP подменены. Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55493 python -m pytest tests/test_dial_list_batch_focus_postgres.py

Люди и номера выдуманы.
"""
import contextlib
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

TEZ, RCC = 560, 1954
RCC_OP, TEZ_OP, TEZ_OTHER = 520, 530, 531
GUARDED = '3.22.46'

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text, rate numeric);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint);
    INSERT INTO departments (id, name, code) VALUES (560, 'Тез КЦ', 'tez'), (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES (560, 'binotel', 'sip52.binotel.com', NULL),
                                             (1954, 'binotel', 'sip53.binotel.com', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (1, 'Руководитель', 'boss', 'admin', 'working', NULL, NULL),
        (520, 'Своев Сава', 'sava', 'operator', 'working', 1954, '902'),
        (530, 'Ашимова Айгерим', 'aigerim', 'operator', 'working', 560, '601'),
        (531, 'Серикова Сауле', 'saule', 'operator', 'working', 560, '602');
'''


class _Db:
    _SIP_INACTIVE_STATUSES = ('fired', 'dismissal')

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


class _Binotel:
    def __init__(self):
        self.originated = []

    def originate_internal_to_external(self, internal_number, external_number, **extra):
        self.originated.append((internal_number, external_number))
        return 'gc-%d-%s' % (len(self.originated), uuid.uuid4().hex[:6])

    def call_details(self, general_call_ids, max_wait=None, timeout=None):
        return {}


class _NoSapar:
    def configured(self):
        return False


class _TezApp:
    """TEZ APP без сети и без поездок: никто не вышел на линию."""

    def fetch(self, phones, month, timeout=None):
        return {}


def _sync(fn):
    fn()


class _Env:
    def __init__(self, db, svc, binotel):
        self.db, self.svc, self.binotel = db, svc, binotel
        self.month = dial_service.current_period()
        self.next_month = (self.month.replace(day=28) + dial_service.timedelta(days=4)).replace(day=1)

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def one(self, sql, params=None):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def upload(self, department, file_name, phones, period=None):
        """Загрузка файла базы, как ручка /leads/upload: строки (номер строки, ФИО, номер как в файле,
        номер нормализованный, ИИН). Возвращает id файла (dial_list_lead_batches)."""
        rows = [(i, f'Водитель {p[-4:]}', p, p, '') for i, p in enumerate(phones, start=2)]
        counts = self.svc.import_leads(department, 1, file_name, rows, period=period, require_iin=False)
        return counts["batch_id"]

    def lead(self, phone):
        return str(self.one("SELECT id FROM dial_list_leads WHERE phone_norm = %s", (phone,))[0])

    def phones_of(self, items):
        return sorted(self.one("SELECT phone_norm FROM dial_list_leads WHERE id = %s", (i["lead_id"],))[0]
                      for i in items)

    def file(self, summary, batch_id):
        return next(b for b in summary["batches"] if b["id"] == batch_id)

    def refused(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except dial_service.DialListError as exc:
            return str(exc), exc.status
        return None


@pytest.fixture
def env():
    schema = 't_dial_focus_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled, binotel_company) "
                    "VALUES (560, FALSE, 'remote_cc'), (1954, TRUE, 'remote_cc')")
        cur.execute("INSERT INTO dial_list_outcomes (department_id, name, position, requeue) VALUES "
                    "(1954, 'Отказ', 1, FALSE), (1954, 'Перезвонить позже', 2, TRUE), "
                    "(560, 'Отказ', 1, FALSE), (560, 'Перезвонить позже', 2, TRUE)")
    binotel = _Binotel()
    svc = dial_service.DialListService(db, client_factory=lambda dep: binotel, sign_checker=_NoSapar(),
                                       calls_submit=_sync, trips_fetch=_TezApp().fetch,
                                       trips_configured=lambda: True)
    svc.operator_context = lambda uid: {
        "user_id": int(uid), "name": "Оператор", "department_id": RCC if int(uid) == RCC_OP else TEZ,
        "internal_number": {RCC_OP: '902', TEZ_OP: '601', TEZ_OTHER: '602'}[int(uid)],
        "settings": svc.department_settings(RCC if int(uid) == RCC_OP else TEZ)}
    try:
        yield _Env(db, svc, binotel)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


A_PHONES = ['77010000001', '77010000002', '77010000003']
B_PHONES = ['77020000001', '77020000002']


def test_marks_narrow_the_pool_and_clearing_them_returns_the_whole_month(env):
    a = env.upload(RCC, 'Город А.xlsx', A_PHONES)
    b = env.upload(RCC, 'Город Б.xlsx', B_PHONES)

    # Ничего не отмечено — как было: обзванивается вся база месяца.
    summary = env.svc.leads_summary(RCC)
    assert summary["focus"] == {"active": False, "files": 0, "paused_ready": 0}
    assert summary["pool_available"] == 5
    assert [b_["file_name"] for b_ in summary["batches"]] == ['Город Б.xlsx', 'Город А.xlsx']   # новые сверху
    assert {b_["file_name"]: (b_["focused"], b_["focused_at"]) for b_ in summary["batches"]} == {
        'Город А.xlsx': (False, None), 'Город Б.xlsx': (False, None)}
    assert env.file(summary, a)["progress"] == {"total": 3, "goal": 0, "open": 3, "ready": 3}
    assert env.file(summary, b)["progress"] == {"total": 2, "goal": 0, "open": 2, "ready": 2}

    # Отметили файл Б: пул, «доступно» сводки и телефона — только его водители.
    summary = env.svc.set_batch_focus(RCC, [b], requester_id=1)
    assert summary["focus"] == {"active": True, "files": 1, "paused_ready": 3}
    assert summary["pool_available"] == 2
    assert env.file(summary, b)["focused"] is True and env.file(summary, b)["focused_at"]
    assert env.file(summary, a)["focused"] is False
    # Прогресс файла не зависит от отметок: «можно выдать» — «если отметить этот файл».
    assert env.file(summary, a)["progress"]["ready"] == 3
    assert env.one("SELECT focused_by FROM dial_list_lead_batches WHERE id = %s", (b,)) == (1,)
    assert env.svc.get_state(RCC_OP, phone_version=GUARDED)["pool_available"] == 2
    portion = env.svc.issue_next_portion(RCC_OP, phone_version=GUARDED)["portion"]
    assert env.phones_of(portion["items"]) == B_PHONES

    # Журнал: водители неотмеченного файла — «База на паузе», а не «В очереди».
    journal = env.svc.leads_journal(RCC)
    assert journal["stages"]["parked"] == 'База на паузе'
    assert (journal["by_stage"]["parked"], journal["by_stage"]["queue"], journal["by_stage"]["issued"]) == (3, 0, 2)
    parked = env.svc.leads_journal(RCC, stage='parked')
    tails = sorted(''.join(ch for ch in i["phone_masked"] if ch.isdigit())[-4:] for i in parked["items"])
    assert tails == ['0001', '0002', '0003']
    assert {i["stage_label"] for i in parked["items"]} == {'База на паузе'}

    # Оба файла — пул из обоих; снятые отметки — снова вся база.
    summary = env.svc.set_batch_focus(RCC, [a, b], requester_id=1)
    assert (summary["focus"]["files"], summary["focus"]["paused_ready"], summary["pool_available"]) == (2, 0, 3)
    summary = env.svc.set_batch_focus(RCC, [], requester_id=1)
    assert summary["focus"] == {"active": False, "files": 0, "paused_ready": 0}
    assert summary["pool_available"] == 3     # двое у оператора
    assert env.svc.leads_journal(RCC)["by_stage"]["parked"] == 0
    assert env.one("SELECT focused, focused_at, focused_by FROM dial_list_lead_batches WHERE id = %s", (b,)) == (
        False, None, None)


def test_already_issued_rows_stay_and_the_next_portion_follows_the_marks(env):
    a = env.upload(RCC, 'Город А.xlsx', A_PHONES)
    env.upload(RCC, 'Город Б.xlsx', B_PHONES)
    env.q("UPDATE dial_list_department_settings SET portion_size = 2 WHERE department_id = %s", (RCC,))
    first = env.svc.issue_next_portion(RCC_OP, phone_version=GUARDED)["portion"]
    assert env.phones_of(first["items"]) == A_PHONES[:2]                  # по порядку загрузки
    # Руководитель перевёл работу на файл Б: выданный список оператор дорабатывает, «Ещё» — после него.
    b = env.one("SELECT id::text FROM dial_list_lead_batches WHERE file_name = 'Город Б.xlsx'")[0]
    env.svc.set_batch_focus(RCC, [b], requester_id=1)
    state = env.svc.get_state(RCC_OP, phone_version=GUARDED)
    assert env.phones_of(state["portion"]["items"]) == A_PHONES[:2]
    assert state["pool_available"] == 2
    assert env.refused(env.svc.issue_next_portion, RCC_OP, phone_version=GUARDED) == (
        "Сначала обработайте текущий список: осталось 2", 409)
    # Строки выдачи закрыты (как после итогов) — новая пачка только из отмеченного файла.
    env.q("UPDATE dial_list_assignments SET state = 'done', result = 'no_answer', done_at = CURRENT_TIMESTAMP")
    env.q("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 1, "
          "last_attempt_at = CURRENT_TIMESTAMP - INTERVAL '10 hours' WHERE phone_norm = ANY(%s)", (A_PHONES[:2],))
    second = env.svc.issue_next_portion(RCC_OP, phone_version=GUARDED)["portion"]
    assert env.phones_of(second["items"]) == B_PHONES
    assert env.one("SELECT COUNT(*) FROM dial_list_assignments a JOIN dial_list_leads l ON l.id = a.lead_id "
                   "WHERE l.first_batch_id = %s AND a.state = 'issued'", (a,)) == (0,)


def test_driver_of_two_files_belongs_to_both(env):
    a = env.upload(RCC, 'Город А.xlsx', A_PHONES)
    # Тот же водитель пришёл и во втором файле (повторная загрузка): first — А, last — Б.
    b = env.upload(RCC, 'Добор.xlsx', [A_PHONES[0], '77030000001'])
    assert env.one("SELECT first_batch_id::text, last_batch_id::text FROM dial_list_leads WHERE phone_norm = %s",
                   (A_PHONES[0],)) == (a, b)
    summary = env.svc.leads_summary(RCC)
    assert (env.file(summary, a)["progress"]["total"], env.file(summary, b)["progress"]["total"]) == (3, 2)
    # Отмечен А — водитель 0001 в работе по первой загрузке, на паузе только новый водитель Б.
    summary = env.svc.set_batch_focus(RCC, [a], requester_id=1)
    assert (summary["pool_available"], summary["focus"]["paused_ready"]) == (3, 1)
    assert [i["id"] for i in env.svc.leads_journal(RCC, stage='parked')["items"]] == [env.lead('77030000001')]
    summary = env.svc.set_batch_focus(RCC, [b], requester_id=1)
    assert summary["pool_available"] == 2
    portion = env.svc.issue_next_portion(RCC_OP, phone_version=GUARDED)["portion"]
    assert env.phones_of(portion["items"]) == [A_PHONES[0], '77030000001']
    summary = env.svc.set_batch_focus(RCC, [a], requester_id=1)
    assert summary["pool_available"] == 2          # третий водитель А у оператора (он и в Б)


def test_third_upload_keeps_the_driver_in_the_marked_middle_file(env):
    # Ревью 17.11.2026: first/last помнят две загрузки — третья уводила водителя из отмеченного «среднего»
    # файла (из пула и из прогресса). Теперь каждая загрузка записывает, кто был в файле.
    a = env.upload(RCC, 'А.xlsx', ['77010000001', '77010000002'])
    b = env.upload(RCC, 'Б.xlsx', ['77010000001', '77020000002'])
    assert env.svc.set_batch_focus(RCC, [b], requester_id=1)["pool_available"] == 2
    c = env.upload(RCC, 'В.xlsx', ['77010000001', '77030000003'])
    assert env.one("SELECT first_batch_id::text, last_batch_id::text FROM dial_list_leads "
                   "WHERE phone_norm = '77010000001'") == (a, c)
    summary = env.svc.leads_summary(RCC)
    assert [env.file(summary, x)["progress"]["total"] for x in (a, b, c)] == [2, 2, 2]
    assert summary["pool_available"] == 2                          # 0001 по-прежнему в работе с Б
    tails = sorted(''.join(ch for ch in i["phone_masked"] if ch.isdigit())[-4:]
                   for i in env.svc.leads_journal(RCC, batch_id=b)["items"])
    assert tails == ['0001', '0002']                               # фильтр «Файл» — по тем же правилам
    portion = env.svc.issue_next_portion(RCC_OP, phone_version=GUARDED)["portion"]
    assert env.phones_of(portion["items"]) == ['77010000001', '77020000002']


def test_overdue_tez_callback_goes_to_anyone_despite_marks(env):
    # Ревью 17.11.2026: перезвон оператора, чьё окно прошло, общий пул отдаёт любому — отметки файлов этому не
    # мешают (обещанный звонок не ждёт, пока файл отметят).
    env.svc.enroll_department(TEZ, changed_by=1, profile='tez')
    env.upload(TEZ, 'КЗО база.xlsx', ['77060000001'])
    trk = env.upload(TEZ, 'Туркестан база.xlsx', ['77070000001'])
    promised = env.lead('77060000001')
    env.q("UPDATE dial_list_leads SET status = 'in_progress', owner_operator_id = %s, due_kind = 'callback', "
          "owner_call_at = CURRENT_TIMESTAMP - INTERVAL '3 hours', next_call_at = CURRENT_TIMESTAMP - INTERVAL '1 hour'"
          " WHERE id = %s", (TEZ_OP, promised))
    summary = env.svc.set_batch_focus(TEZ, [trk], requester_id=1)
    assert (summary["pool_available"], summary["focus"]["paused_ready"]) == (2, 0)
    assert env.svc.leads_journal(TEZ)["by_stage"]["parked"] == 0
    portion = env.svc.issue_next_portion(TEZ_OTHER, phone_version=GUARDED)["portion"]
    assert env.phones_of(portion["items"]) == ['77060000001', '77070000001']


def test_changing_the_dialed_month_clears_marks(env):
    # Ревью 17.11.2026: отметки прежнего месяца не оживают при возврате к нему — смена месяца их снимает; отметка
    # в обзваниваемом месяце снимает и оставшиеся от смены месяца по календарю.
    a = env.upload(RCC, 'А.xlsx', A_PHONES)
    later = env.upload(RCC, 'Декабрь.xlsx', ['77040000001'], period=env.next_month)
    env.svc.set_batch_focus(RCC, [a], requester_id=1)
    env.svc.save_department_settings(RCC, {"max_attempts": 4}, changed_by=1)        # месяц тот же — отметки целы
    assert env.one("SELECT COUNT(*) FROM dial_list_lead_batches WHERE focused") == (1,)
    env.svc.save_department_settings(RCC, {"active_period": env.next_month.strftime('%Y-%m')}, changed_by=1)
    assert env.one("SELECT COUNT(*) FROM dial_list_lead_batches WHERE focused") == (0,)
    env.svc.save_department_settings(RCC, {"active_period": ""}, changed_by=1)
    assert env.svc.leads_summary(RCC)["focus"]["active"] is False
    env.q("UPDATE dial_list_lead_batches SET focused = TRUE WHERE id = %s", (later,))   # след смены по календарю
    env.svc.set_batch_focus(RCC, [a], requester_id=1)
    assert env.q("SELECT id::text FROM dial_list_lead_batches WHERE focused") == [(a,)]


def test_remaining_and_ready_follow_the_department_rules(env):
    # Ревью 17.11.2026: «можно выдать» и «операторы ждут» — по часам повтора и числу попыток отдела (порядок
    # параметров), «в обзвоне» — как в журнале: попытки кончились — уже не в обзвоне.
    env.q("UPDATE dial_list_department_settings SET max_attempts = 5, retry_after_hours = 2 WHERE department_id = %s",
          (RCC,))
    f1 = env.upload(RCC, 'Ф1.xlsx', ['77080000001', '77080000002', '77080000003'])
    f2 = env.upload(RCC, 'Ф2.xlsx', ['77080000004'])
    for phone, attempts, hours in (('77080000001', 4, 3), ('77080000002', 5, 3), ('77080000003', 1, 1)):
        env.q("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = %s, "
              "last_attempt_at = CURRENT_TIMESTAMP - make_interval(hours => %s) WHERE phone_norm = %s",
              (attempts, hours, phone))
    summary = env.svc.leads_summary(RCC)
    # 0001 — 4 попытки из 5, повтор прошёл: в обзвоне и можно выдать; 0002 — попытки кончились; 0003 — ждёт повтора.
    assert env.file(summary, f1)["progress"] == {"total": 3, "goal": 0, "open": 2, "ready": 1}
    summary = env.svc.set_batch_focus(RCC, [f2], requester_id=1)
    assert (summary["pool_available"], summary["focus"]["paused_ready"]) == (1, 1)


def test_driver_without_a_file_waits_while_files_are_marked(env):
    # Строка без файла (first/last NULL): без отметок она в обзвоне, как вся база; при отметках её нет ни в
    # одном отмеченном файле — пул её не выдаёт, журнал пишет «База на паузе», а не «В очереди».
    a = env.upload(RCC, 'Город А.xlsx', A_PHONES)
    lone = str(env.one("INSERT INTO dial_list_leads (department_id, phone_norm, full_name, period) "
                       "VALUES (%s, '77090000001', 'Без Файла', %s) RETURNING id", (RCC, env.month))[0])
    assert env.svc.leads_summary(RCC)["pool_available"] == 4
    summary = env.svc.set_batch_focus(RCC, [a], requester_id=1)
    assert (summary["pool_available"], summary["focus"]["paused_ready"]) == (3, 1)
    assert [i["id"] for i in env.svc.leads_journal(RCC, stage='parked')["items"]] == [lone]
    portion = env.svc.issue_next_portion(RCC_OP, phone_version=GUARDED)["portion"]
    assert env.phones_of(portion["items"]) == A_PHONES


def test_progress_counts_goal_open_ready_and_only_the_month_base(env):
    a = env.upload(RCC, 'Город А.xlsx', A_PHONES + ['77010000004', '77010000005', '77010000006'])
    ids = {p: env.lead(p) for p in A_PHONES + ['77010000004', '77010000005', '77010000006']}
    # 1 — подписал, 2 — на проверке у Яндекса, 3 — закрыт итогом, 4 — ждёт повтора, 5 — исключён руководителем,
    # 6 — новый. Список месяца есть (Города по ИИН), исключённого в нём нет — он не в базе месяца вовсе.
    env.q("UPDATE dial_list_leads SET signed_at = CURRENT_TIMESTAMP WHERE id = %s", (ids['77010000001'],))
    env.q("UPDATE dial_list_leads SET sign_status = 'processing' WHERE id = %s", (ids['77010000002'],))
    env.q("UPDATE dial_list_leads SET status = 'done' WHERE id = %s", (ids['77010000003'],))
    env.q("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 1, last_attempt_at = CURRENT_TIMESTAMP "
          "WHERE id = %s", (ids['77010000004'],))
    env.q("UPDATE dial_list_leads SET status = 'excluded' WHERE id = %s", (ids['77010000005'],))
    env.q("INSERT INTO dial_list_report_roster (department_id, period, iin, city) VALUES (%s, %s, '900101300001', '')",
          (RCC, env.month))
    summary = env.svc.leads_summary(RCC)
    # Всего 5 (исключённый не из списка — не в базе), цель 1, в обзвоне 2 (4 и 6), выдать сейчас — 1 (6).
    assert env.file(summary, a)["progress"] == {"total": 5, "goal": 1, "open": 2, "ready": 1}
    assert summary["total"] == 5                                   # та же база, что у плиток


def test_marks_only_in_the_dialed_month_and_only_own_files(env):
    a = env.upload(RCC, 'Город А.xlsx', A_PHONES)
    later = env.upload(RCC, 'Декабрь.xlsx', ['77040000001'], period=env.next_month)
    other = env.upload(TEZ, 'КЗО база.xlsx', ['77050000001'])
    message, status = env.refused(env.svc.set_batch_focus, RCC, [later], requester_id=1, period=env.next_month)
    assert status == 409 and message.startswith('Отмечать файлы можно только в обзваниваемом месяце')
    assert env.refused(env.svc.set_batch_focus, RCC, [later], requester_id=1) == (
        dial_service.BATCH_FOCUS_UNKNOWN, 404)                    # файл другого месяца
    assert env.refused(env.svc.set_batch_focus, RCC, [other], requester_id=1) == (
        dial_service.BATCH_FOCUS_UNKNOWN, 404)                    # файл другого отдела
    assert env.refused(env.svc.set_batch_focus, RCC, ['не-файл'], requester_id=1)[1] == 400
    assert env.refused(env.svc.set_batch_focus, RCC, a, requester_id=1)[1] == 400   # не список
    assert env.one("SELECT COUNT(*) FROM dial_list_lead_batches WHERE focused") == (0,)
    # Тот же файл дважды и в другом регистре — одна отметка.
    summary = env.svc.set_batch_focus(RCC, [a, a.upper()], requester_id=1)
    assert summary["focus"]["files"] == 1
    # Повторная отметка не сдвигает «когда отметили».
    first_at = env.one("SELECT focused_at FROM dial_list_lead_batches WHERE id = %s", (a,))[0]
    env.svc.set_batch_focus(RCC, [a], requester_id=7)
    assert env.one("SELECT focused_at, focused_by FROM dial_list_lead_batches WHERE id = %s", (a,)) == (first_at, 1)
    # Сводка не обзваниваемого месяца: отметок нет, «можно выдать сейчас» — ноль.
    december = env.svc.leads_summary(RCC, period=env.next_month)
    assert december["focus"] == {"active": False, "files": 0, "paused_ready": 0}
    assert december["pool_available"] == 0


def test_marked_file_is_listed_even_past_the_list_limit(env, monkeypatch):
    oldest = env.upload(RCC, 'Первый.xlsx', ['77010000001'])
    env.upload(RCC, 'Второй.xlsx', ['77010000002'])
    newest = env.upload(RCC, 'Третий.xlsx', ['77010000003'])
    env.q("UPDATE dial_list_lead_batches SET created_at = created_at - "
          "make_interval(mins => CASE file_name WHEN 'Первый.xlsx' THEN 30 WHEN 'Второй.xlsx' THEN 20 ELSE 10 END)")
    monkeypatch.setattr(dial_service, 'BATCHES_SHOWN_MAX', 2)
    env.svc.set_batch_focus(RCC, [oldest], requester_id=1)
    summary = env.svc.leads_summary(RCC)
    assert [b["id"] for b in summary["batches"]] == [newest, oldest]   # отмеченный не отрезан, порядок — по дате


def test_tez_own_callbacks_bypass_marks_and_the_pool_follows_them(env):
    env.svc.enroll_department(TEZ, changed_by=1, profile='tez')
    kzo = env.upload(TEZ, 'КЗО база.xlsx', ['77060000001', '77060000002'])
    trk = env.upload(TEZ, 'Туркестан база.xlsx', ['77070000001', '77070000002'])
    # Оператор обещал перезвонить водителю КЗО — срок наступил.
    promised = env.lead('77060000001')
    env.q("UPDATE dial_list_leads SET status = 'in_progress', owner_operator_id = %s, due_kind = 'callback', "
          "owner_call_at = CURRENT_TIMESTAMP - INTERVAL '1 minute', next_call_at = CURRENT_TIMESTAMP + INTERVAL '2 hours'"
          " WHERE id = %s", (TEZ_OP, promised))
    summary = env.svc.set_batch_focus(TEZ, [trk], requester_id=1)
    assert (summary["focus"]["files"], summary["pool_available"], summary["focus"]["paused_ready"]) == (1, 2, 1)
    journal = env.svc.leads_journal(TEZ)
    assert (journal["by_stage"]["callback"], journal["by_stage"]["parked"], journal["by_stage"]["queue"]) == (1, 1, 2)
    assert journal["stages"]["parked"] == 'База на паузе'
    # Свой перезвон — первым, хотя файл КЗО не отмечен; дальше — только Туркестан.
    res = env.svc.auto_next(TEZ_OP, status='active', phone_version=GUARDED)
    assert (res["next"]["lead_id"], res["next"]["kind"]) == (promised, 'callback')
    env.q("UPDATE dial_list_leads SET owner_operator_id = NULL, due_kind = '', owner_call_at = NULL "
          "WHERE id = %s", (promised,))
    env.q("UPDATE dial_list_assignments SET state = 'done', result = 'no_answer', done_at = CURRENT_TIMESTAMP")
    portion = env.svc.issue_next_portion(TEZ_OP, phone_version=GUARDED)["portion"]
    assert env.phones_of(portion["items"]) == ['77070000001', '77070000002']
    assert env.one("SELECT COUNT(*) FROM dial_list_lead_batches WHERE focused AND id = %s", (kzo,)) == (0,)
