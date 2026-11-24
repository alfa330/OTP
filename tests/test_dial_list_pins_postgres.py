# -*- coding: utf-8 -*-
"""Закреплённые водители на настоящем PostgreSQL (владелец, 18.11.2026: «направить самим менеджерам на обзвон т.е
именно по ним закрепить этих водителей что бы они их обрабатывали»; решение: менеджер получает сначала своих
водителей, когда кончатся — общий список).

Что проверяется: пул оператора (свои — первыми и мимо отметок файлов, чужие — никогда, повтор недозвона — снова
своему), закрепление уволенного или снятого с линии не держит водителя, ручка закрепления (снятие со списка
другого оператора, идущий звонок, обнуление попыток, пропуски, повторный запуск, снятие закрепления), журнал и
«Позвонить ещё раз».

Ни боевых учёток, ни импорта приложения: только 127.0.0.1, своя схема со случайным именем, в конце она
удаляется. Binotel и Sapar подменены. Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55493 python -m pytest tests/test_dial_list_pins_postgres.py

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

TEZ, FRONT, RCC = 560, 909, 1954
RCC_OP = 520          # оператор удалённого КЦ
FRONT_A, FRONT_B = 801, 802  # менеджеры фронт-офиса, сидят на линиях удалённого КЦ
TEZ_OP = 530           # оператор Тез — в удалённом КЦ не работает
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
    INSERT INTO departments (id, name, code) VALUES (560, 'Тез КЦ', 'tez'), (909, 'Фронт офисы', 'front_office'),
                                                    (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES (560, 'binotel', 'sip52.binotel.com', NULL),
                                             (1954, 'binotel', 'sip53.binotel.com', NULL);
    INSERT INTO users (id, name, login, role, status, department_id, sip_number) VALUES
        (1, 'Руководитель', 'boss', 'admin', 'working', NULL, NULL),
        (520, 'Своев Сава', 'sava', 'operator', 'working', 1954, '902'),
        (801, 'Фронтова Фарида', 'farida', 'operator', 'working', 909, '701'),
        (802, 'Офисова Ольга', 'olga', 'operator', 'working', 909, '702'),
        (530, 'Ашимова Айгерим', 'aigerim', 'operator', 'working', 560, '601');
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
    def originate_internal_to_external(self, internal_number, external_number, **extra):
        return 'gc-' + uuid.uuid4().hex[:8]

    def call_details(self, general_call_ids, max_wait=None, timeout=None):
        return {}


class _NoSapar:
    def configured(self):
        return False


def _sync(fn):
    fn()


class _Env:
    def __init__(self, db, svc):
        self.db, self.svc = db, svc
        self.month = dial_service.current_period()
        self.next_month = (self.month.replace(day=28) + dial_service.timedelta(days=4)).replace(day=1)

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def one(self, sql, params=None):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def upload(self, file_name, phones, period=None, department=RCC):
        rows = [(i, f'Водитель {p[-4:]}', p, p, '') for i, p in enumerate(phones, start=2)]
        return self.svc.import_leads(department, 1, file_name, rows, period=period, require_iin=False)["batch_id"]

    def lead(self, phone):
        return str(self.one("SELECT id FROM dial_list_leads WHERE phone_norm = %s", (phone,))[0])

    def leads(self, *phones):
        return [self.lead(p) for p in phones]

    def phones_of(self, items):
        return [self.one("SELECT phone_norm FROM dial_list_leads WHERE id = %s", (i["lead_id"],))[0] for i in items]

    def issue(self, operator):
        return self.phones_of(self.svc.issue_next_portion(operator, phone_version=GUARDED)["portion"]["items"])

    def close_rows(self, operator, result='no_answer'):
        """Строки выдачи оператора — обработаны недозвоном, как после исхода Binotel: строка закрыта, у водителя
        попытка в счёте (повтор отдела — 0 часов, водитель снова в пуле сразу)."""
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT a.id, a.lead_id FROM dial_list_assignments a
                WHERE a.operator_id = %s AND a.state = 'issued'
            """, (operator,))
            for asg, lead in cur.fetchall():
                cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, state, disposition, billsec) "
                            "VALUES (%s, %s, 'finished', 'NOANSWER', 0)", (asg, operator))
                self.svc._close_assignment(cur, str(asg), str(lead), result, answered=False, count_attempt=True)

    def pin(self, operator, *phones, note=''):
        return self.svc.pin_leads(RCC, operator, self.leads(*phones), actor_id=1, note=note)

    def refused(self, fn, *args, **kwargs):
        try:
            fn(*args, **kwargs)
        except dial_service.DialListError as exc:
            return str(exc), exc.status
        return None


@pytest.fixture
def env():
    schema = 't_dial_pins_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled, binotel_company, portion_size, "
                    "max_attempts, retry_after_hours) VALUES (560, FALSE, 'remote_cc', 20, 3, 24), "
                    "(1954, TRUE, 'remote_cc', 3, 3, 0)")
        cur.execute("INSERT INTO dial_list_outcomes (department_id, name, position, requeue) VALUES "
                    "(1954, 'Отказ', 1, FALSE), (1954, 'Перезвонить позже', 2, TRUE)")
        # Менеджеры фронт-офиса сидят на линиях удалённого КЦ (как на проде с 05.11.2026).
        cur.execute("INSERT INTO dial_list_line_members (user_id, department_id, internal_number) VALUES "
                    "(801, 1954, '981'), (802, 1954, '982')")
    svc = dial_service.DialListService(db, client_factory=lambda dep: _Binotel(), sign_checker=_NoSapar(),
                                       calls_submit=_sync)
    svc.operator_context = lambda uid: {
        "user_id": int(uid), "name": "Оператор", "department_id": RCC,
        "internal_number": {RCC_OP: '902', FRONT_A: '981', FRONT_B: '982'}[int(uid)],
        "settings": svc.department_settings(RCC)}
    try:
        yield _Env(db, svc)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


PHONES = ['77010000001', '77010000002', '77010000003', '77010000004', '77010000005', '77010000006']


def test_own_drivers_first_others_never_then_the_common_list(env):
    env.upload('База ноября.xlsx', PHONES)
    res = env.pin(FRONT_A, PHONES[4], PHONES[5])
    assert (res["pinned"], res["taken_from_lists"], res["kept"], res["skipped"]) == (2, 0, 0, {})
    assert res["operator_name"] == 'Фронтова Фарида'

    # Сводка отдела: закреплённые в «доступно» — их выдадут своему.
    assert env.svc.leads_summary(RCC)["pool_available"] == 6
    # Оператор удалённого КЦ закреплённых не получает никогда: порция 3 — из общих.
    assert env.svc.get_state(RCC_OP, phone_version=GUARDED)["pool_available"] == 4
    rcc = env.issue(RCC_OP)
    assert len(rcc) == 3 and set(rcc) <= set(PHONES[:4])
    # Менеджер — сначала свои (хотя они загружены последними), потом общий список (решение владельца).
    assert env.svc.get_state(FRONT_A, phone_version=GUARDED)["pool_available"] == 3
    front_a = env.issue(FRONT_A)
    assert set(front_a[:2]) == {PHONES[4], PHONES[5]}
    assert front_a[2:] == sorted(set(PHONES[:4]) - set(rcc))
    assert env.svc.leads_summary(RCC)["pool_available"] == 0


def test_no_answer_returns_the_driver_to_the_same_manager_only(env):
    env.upload('База ноября.xlsx', PHONES)
    env.pin(FRONT_A, PHONES[0])
    front_a = env.issue(FRONT_A)
    assert front_a[0] == PHONES[0] and len(front_a) == 3
    env.close_rows(FRONT_A)                                    # недозвон всем трём, повтор отдела — 0 часов
    # Общие недозвоны — в пул любому (после ещё не звонённых); закреплённый — только своему, и у него снова первым.
    rcc = env.issue(RCC_OP)
    assert set(rcc) == set(PHONES[1:]) - set(front_a)
    env.close_rows(RCC_OP)
    front_b = env.issue(FRONT_B)
    assert len(front_b) == 3 and PHONES[0] not in front_b
    assert env.issue(FRONT_A)[0] == PHONES[0]
    assert env.one("SELECT attempts_total, pinned_operator_id FROM dial_list_leads WHERE phone_norm = %s",
                   (PHONES[0],)) == (1, FRONT_A)


def test_pin_of_someone_who_left_does_not_hold_the_driver(env):
    env.upload('База ноября.xlsx', PHONES[:2])
    env.pin(FRONT_A, PHONES[0])
    assert env.issue(RCC_OP) == [PHONES[1]]
    env.close_rows(RCC_OP)
    # Уволили — водитель снова общий, без правки данных.
    env.q("UPDATE users SET status = 'fired' WHERE id = %s", (FRONT_A,))
    journal = {i["id"]: i for i in env.svc.leads_journal(RCC)["items"]}
    assert journal[env.lead(PHONES[0])]["pinned_operator"] is None
    assert env.issue(RCC_OP)[0] == PHONES[0]
    env.close_rows(RCC_OP)
    # Вернули на работу — закрепление снова в силе.
    env.q("UPDATE users SET status = 'working' WHERE id = %s", (FRONT_A,))
    assert env.issue(RCC_OP) == [PHONES[1]]
    env.close_rows(RCC_OP)
    # Сняли с линии удалённого КЦ (сотрудник другого отдела) — снова общий.
    env.q("UPDATE dial_list_line_members SET released_at = CURRENT_TIMESTAMP WHERE user_id = %s", (FRONT_A,))
    assert env.issue(RCC_OP)[0] == PHONES[0]


def test_pinned_driver_goes_past_the_file_marks(env):
    a = env.upload('Город А.xlsx', PHONES[:3])
    b = env.upload('Город Б.xlsx', PHONES[3:])
    env.pin(FRONT_A, PHONES[0])
    summary = env.svc.set_batch_focus(RCC, [b], requester_id=1)
    # Закреплённый из неотмеченного файла — не «на паузе»: его выдают своему мимо отметок.
    assert summary["focus"]["paused_ready"] == 2
    assert summary["pool_available"] == 4
    journal = env.svc.leads_journal(RCC)
    assert (journal["by_stage"]["parked"], journal["by_stage"]["queue"]) == (2, 4)
    pinned_row = next(i for i in journal["items"] if i["id"] == env.lead(PHONES[0]))
    assert pinned_row["stage"] == 'queue'
    assert pinned_row["pinned_operator"] == {"id": FRONT_A, "name": 'Фронтова Фарида'}
    assert sorted(env.issue(RCC_OP)) == PHONES[3:]
    assert env.issue(FRONT_A) == [PHONES[0]]
    assert a != b


def test_pin_takes_the_driver_off_another_operators_list_but_not_during_a_call(env):
    env.upload('База ноября.xlsx', PHONES[:3])
    assert sorted(env.issue(RCC_OP)) == PHONES[:3]
    with env.db._get_cursor() as cur:
        cur.execute("SELECT a.id FROM dial_list_assignments a JOIN dial_list_leads l ON l.id = a.lead_id "
                    "WHERE l.phone_norm = %s", (PHONES[1],))
        talking = cur.fetchone()[0]
        cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, state) VALUES (%s, %s, 'leg_answered')",
                    (talking, RCC_OP))
    res = env.pin(FRONT_A, PHONES[0], PHONES[1])
    assert (res["pinned"], res["taken_from_lists"], res["skipped"]) == (1, 1, {"calling": 1})
    assert res["skipped_labels"] == {"calling": dial_service.DialListService.PIN_SKIPS["calling"]}
    # Строку первого водителя у оператора закрыли без результата, как «Исключить»; второй разговаривает — не тронут.
    assert env.one("SELECT a.state, a.result FROM dial_list_assignments a JOIN dial_list_leads l ON l.id = a.lead_id "
                   "WHERE l.phone_norm = %s AND a.operator_id = %s", (PHONES[0], RCC_OP)) == ('done', 'other')
    assert env.one("SELECT pinned_operator_id FROM dial_list_leads WHERE phone_norm = %s", (PHONES[1],)) == (None,)
    state = env.svc.get_state(RCC_OP, phone_version=GUARDED)
    assert {i["state"] for i in state["portion"]["items"] if i["lead_id"] == env.lead(PHONES[0])} == {'done'}
    # Повторный запуск — когда звонок кончился: второй закреплён, первый — без изменений.
    env.q("UPDATE dial_list_attempts SET state = 'finished', disposition = 'ANSWER', billsec = 30")
    env.q("UPDATE dial_list_assignments SET state = 'done', result = 'answered', done_at = CURRENT_TIMESTAMP "
          "WHERE id = %s", (talking,))
    env.q("UPDATE dial_list_leads SET status = 'done', attempts_total = 1, answered_at = CURRENT_TIMESTAMP, "
          "last_attempt_at = CURRENT_TIMESTAMP WHERE phone_norm = %s", (PHONES[1],))
    res = env.pin(FRONT_A, PHONES[0], PHONES[1])
    assert (res["pinned"], res["kept"], res["taken_from_lists"]) == (1, 1, 0)
    # Обработанный (был разговор, не подписал) снова в обзвоне у менеджера — попытки с нуля.
    assert env.one("SELECT status, attempts_total, last_attempt_at FROM dial_list_leads WHERE phone_norm = %s",
                   (PHONES[1],)) == ('new', 0, None)
    events = env.q("SELECT kind, note, actor_id FROM dial_list_lead_events WHERE lead_id = %s",
                   (env.lead(PHONES[1]),))
    assert events == [('requeue', 'Закреплён за оператором Фронтова Фарида', 1)]
    # С ним уже говорили — это повтор (доля повторов в пачке, 24.11.2026: у удалённого КЦ не больше 20 %, пока есть
    # другие водители). Других у менеджера один — пачка из него, повтор ждёт; другие кончились — повтор выдаётся.
    assert env.issue(FRONT_A) == [PHONES[0]]
    env.close_rows(FRONT_A)
    env.q("UPDATE dial_list_leads SET status = 'done' WHERE phone_norm = %s", (PHONES[0],))
    assert env.issue(FRONT_A) == [PHONES[1]]


def test_already_on_the_managers_list_keeps_its_counters(env):
    env.upload('База ноября.xlsx', PHONES[:1])
    assert env.issue(FRONT_A) == PHONES[:1]
    env.q("UPDATE dial_list_leads SET status = 'in_progress', attempts_total = 2")
    res = env.pin(FRONT_A, PHONES[0])
    assert (res["pinned"], res["taken_from_lists"]) == (1, 0)
    assert env.one("SELECT status, attempts_total, pinned_operator_id FROM dial_list_leads") == (
        'in_progress', 2, FRONT_A)
    assert env.one("SELECT COUNT(*) FROM dial_list_assignments WHERE state = 'issued'") == (1,)


def test_what_is_never_pinned(env):
    env.upload('База ноября.xlsx', PHONES[:5])
    env.upload('Декабрь.xlsx', ['77040000001'], period=env.next_month)
    env.q("UPDATE dial_list_leads SET signed_at = CURRENT_TIMESTAMP WHERE phone_norm = %s", (PHONES[0],))
    env.q("UPDATE dial_list_leads SET sign_status = 'processing' WHERE phone_norm = %s", (PHONES[1],))
    env.q("UPDATE dial_list_leads SET status = 'excluded' WHERE phone_norm = %s", (PHONES[2],))
    env.q("UPDATE dial_list_leads SET phone_norm = %s WHERE phone_norm = %s",
          (dial_service.report_only_phone('900101300001'), PHONES[3]))
    ids = env.leads(PHONES[0], PHONES[1], PHONES[2], '77040000001') + [
        str(env.one("SELECT id FROM dial_list_leads WHERE phone_norm LIKE 'iin:%%'")[0]), str(uuid.uuid4())]
    res = env.svc.pin_leads(RCC, FRONT_A, ids, actor_id=1)
    assert res["pinned"] == 0
    assert res["skipped"] == {"signed": 2, "excluded": 1, "other_month": 1, "no_phone": 1, "not_found": 1}
    assert env.one("SELECT COUNT(*) FROM dial_list_leads WHERE pinned_operator_id IS NOT NULL") == (0,)


def test_who_can_be_given_drivers(env):
    env.upload('База ноября.xlsx', PHONES[:1])
    lead = env.lead(PHONES[0])
    msg = "Оператор не работает в этом отделе: не сотрудник отдела и не сидит на его линии"
    assert env.refused(env.svc.pin_leads, RCC, TEZ_OP, [lead], actor_id=1) == (msg, 409)
    env.q("UPDATE users SET status = 'dismissal' WHERE id = %s", (FRONT_B,))
    assert env.refused(env.svc.pin_leads, RCC, FRONT_B, [lead], actor_id=1) == (msg, 409)
    # Свой оператор отдела (не на чужой линии) — можно.
    assert env.svc.pin_leads(RCC, RCC_OP, [lead], actor_id=1)["pinned"] == 1
    # Тез — не удалённый КЦ.
    env.svc.enroll_department(TEZ, changed_by=1, profile='tez')
    assert env.refused(env.svc.pin_leads, TEZ, TEZ_OP, [lead], actor_id=1) == (
        "Закрепление водителей — только в удалённом КЦ", 409)


def test_unpin_returns_the_driver_to_everyone(env):
    env.upload('База ноября.xlsx', PHONES[:2])
    env.pin(FRONT_A, PHONES[0])
    res = env.svc.pin_leads(RCC, None, env.leads(PHONES[0], PHONES[1]), actor_id=1, note='ошибся')
    assert (res["unpinned"], res["skipped"]) == (1, {"unchanged": 1})
    assert env.one("SELECT pinned_operator_id, pinned_at, pinned_by FROM dial_list_leads WHERE phone_norm = %s",
                   (PHONES[0],)) == (None, None, None)
    assert env.q("SELECT kind, note FROM dial_list_lead_events WHERE lead_id = %s ORDER BY id",
                 (env.lead(PHONES[0]),))[-1] == ('note', 'Закрепление за оператором снято: ошибся')
    assert sorted(env.issue(RCC_OP)) == PHONES[:2]


def test_recall_of_a_driver_pinned_to_someone_else_is_refused(env):
    env.upload('База ноября.xlsx', PHONES[:2])
    assert sorted(env.issue(RCC_OP)) == PHONES[:2]
    env.close_rows(RCC_OP)
    env.pin(FRONT_A, PHONES[0])
    # Последним звонил оператор удалённого КЦ, но водитель уже закреплён за менеджером.
    assert env.refused(env.svc.recall_lead, RCC_OP, env.lead(PHONES[0]), phone_version=GUARDED) == (
        dial_service.RECALL_PINNED_OTHER, 409)
    # Незакреплённого своего — можно, как раньше.
    assert env.svc.recall_lead(RCC_OP, env.lead(PHONES[1]), phone_version=GUARDED)["assignment_id"]


def test_lead_card_shows_the_pin(env):
    env.upload('База ноября.xlsx', PHONES[:1])
    env.pin(FRONT_B, PHONES[0])
    card = env.svc.lead_card(env.lead(PHONES[0]))
    assert card["pinned_operator"] == {"id": FRONT_B, "name": 'Офисова Ольга'}
    assert card["stage"] == 'queue'
