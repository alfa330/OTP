# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

Круг проверки подписания «Удаленного КЦ» (владелец, 10.11.2026: «проверять только тех, кто в списке, способом,
который показывает статусы точно, и максимально быстро»; «отображать ближайшее обновление»):

  * круг берёт всю базу текущего и прошлого месяца, кроме истёкших (подписать уже нельзя): тех — в базе
    текущего месяца раз в 3 часа, в базе прошлого — не берёт вовсе;
  * строку водителя круг переписывает, только когда есть что писать: сменился статус или документы, была
    ошибка, подписал, проверяли впервые — либо времени проверки в строке уже полчаса;
  * «Проверить сейчас» в карточке и проверка перед выдачей пишут время всегда;
  * перепроверка перед отчётом не спрашивает тех, кого круг спросил только что, — это знает его память;
  * «Аналитика» отдаёт круг для отсчёта, журнал — точное время проверки из памяти круга;
  * «на проверке у Яндекса» — подписал (владелец, 19.11.2026): момент подписи водителя (время «ждущего Яндекса»
    документа — по Алматы) и успешка сразу, везде — среди подписавших; круг ведёт таких до «Подписано», не
    сдвигая ни подпись, ни успешку.

Ни боевых учёток, ни сети: только 127.0.0.1, своя схема со случайным именем, в конце удаляется; Sapar подменён.
Запуск на локальном кластере:

    DIAL_LIST_TEST_PORT=55461 python -m pytest tests/test_dial_list_sign_cycle_postgres.py

Люди, номера и ИИН выдуманы.
"""
import contextlib
import os
import sys
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import analytics as dial_analytics  # noqa: E402
from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402
from dial_list import sign_check, signing  # noqa: E402
from dial_list.sign_check import SignChecker  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

RCC = 1954
UTC = timezone.utc

BASE_DDL = '''
    CREATE TABLE departments (id integer PRIMARY KEY, name text NOT NULL, code text,
                              is_active boolean DEFAULT TRUE);
    CREATE TABLE test_phone_numbers (phone_key varchar(10) UNIQUE);
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL, login text, role text,
                        status text NOT NULL DEFAULT 'working', department_id integer,
                        sip_number text);
    CREATE TABLE sip_department_config (department_id integer PRIMARY KEY, provider text,
                                        sip_server text, ring_timeout_rings smallint);
    INSERT INTO departments (id, name, code) VALUES (1954, 'Удаленный КЦ', 'remote_cc');
    INSERT INTO sip_department_config VALUES (1954, 'binotel', 'sip53.binotel.com', NULL);
'''

WAITING = [{'ServiceId': 1, 'Status': 'НаПодписанииУВодителя'}, {'ServiceId': 2, 'Status': 'НаПодписанииУВодителя'}]
AT_YANDEX = [{'ServiceId': 1, 'Status': 'НаПодписанииУЯндекса', 'DocSignDateByDriver': '2026-11-10T04:00:00'},
             {'ServiceId': 2, 'Status': 'НаПодписанииУЯндекса', 'DocSignDateByDriver': '2026-11-10T04:01:00'}]
HALF = [{'ServiceId': 1, 'Status': 'НаПодписанииУЯндекса', 'DocSignDateByDriver': '2026-11-10T04:00:00'},
        {'ServiceId': 2, 'Status': 'НаПодписанииУВодителя'}]
SIGNED = [{'ServiceId': 1, 'Status': 'Подписано', 'DocSignDateByDriver': '2026-11-01T04:00:00'},
          {'ServiceId': 2, 'Status': 'Подписано', 'DocSignDateByDriver': '2026-11-01T04:01:00'}]


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


class _Sapar:
    """Подставной Sapar: что отдать по ИИН (документы Яндекса или отказ) и кого спрашивали."""

    def __init__(self):
        self.docs, self.errors, self.asked = {}, {}, []

    def fetch(self, iin, month, year, session=None, timeout=None):
        self.asked.append(iin)
        if iin in self.errors:
            return {'ok': False, 'error': self.errors[iin], 'systemic': False}
        return {'ok': True, 'yandex': self.docs.get(iin, WAITING), 'park': []}

    def took(self):
        asked, self.asked = sorted(self.asked), []
        return asked


class _Env:
    def __init__(self, db):
        self.db = db
        self.period = dial_service.current_period()
        self.prev = (self.period - timedelta(days=1)).replace(day=1)
        # Десятое число месяца базы, 10:00 по Алматы.
        self.t0 = datetime.combine(self.period + timedelta(days=9), time(5, 0), tzinfo=UTC)
        self.sapar = _Sapar()
        self.ids = {}

    def at(self, minutes):
        return self.t0 + timedelta(minutes=minutes)

    def q(self, sql, params=None):
        with self.db._get_cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else None

    def lead(self, iin, period=None, status='new', sign_status='', checked_min=None, signed=False):
        """Водитель базы: checked_min — сколько минут назад (от t0) записано время проверки; None — не проверяли."""
        row = self.q("""
            INSERT INTO dial_list_leads (department_id, phone_norm, full_name, period, iin, status, sign_status,
                                         sign_checked_at, signed_at)
            VALUES (%s, %s, 'Водитель', %s, %s, %s, %s, %s, %s) RETURNING id
        """, (RCC, '7701' + iin[-7:], period or self.period, iin, status, sign_status,
              self.at(-checked_min) if checked_min is not None else None,
              self.at(-600) if signed else None))
        self.ids[iin] = str(row[0][0])
        return self.ids[iin]

    def roster(self, iins):
        for iin in iins:
            self.q("INSERT INTO dial_list_report_roster (department_id, period, iin, city) VALUES (%s, %s, %s, %s)",
                   (RCC, self.period, iin, 'Алматы'))

    def checker(self):
        """Новая проверка — как после перезапуска сервера: память круга пустая."""
        return SignChecker(self.db, fetch=self.sapar.fetch, configured=lambda: True, sleep=lambda _s: None,
                           fetch_ids=lambda month, year, wanted=None: {'ok': True, 'ids': {}, 'listed': 1})

    def row(self, iin):
        """(версия строки, статус, время проверки, ошибка, подписал ли): версия меняется при любой перезаписи."""
        return self.q("SELECT xmin::text, sign_status, sign_checked_at, sign_error, signed_at IS NOT NULL "
                      "FROM dial_list_leads WHERE id = %s", (self.ids[iin],))[0]

    def signing(self, iin):
        """(статус, момент подписи, когда увидели, кому успешка, решено ли)."""
        return self.q("SELECT sign_status, signed_at, signed_detected_at, success_operator_id, "
                      "success_resolved_at IS NOT NULL FROM dial_list_leads WHERE id = %s", (self.ids[iin],))[0]

    def call(self, iin, operator, at, billsec):
        """Разговор оператора с водителем: выдача, строка и законченная попытка с ответом Binotel."""
        with self.db._get_cursor() as cur:
            cur.execute("INSERT INTO dial_list_portions (operator_id, department_id, size, issued_at, closed_at) "
                        "VALUES (%s, %s, 1, %s, %s) RETURNING id", (operator, RCC, at, at))
            portion = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_assignments (portion_id, lead_id, operator_id, position, state, "
                        "result, attempts, created_at, done_at) VALUES (%s, %s, %s, 1, 'done', 'answered', 1, %s, %s) "
                        "RETURNING id", (portion, self.ids[iin], operator, at, at))
            assignment = cur.fetchone()[0]
            cur.execute("INSERT INTO dial_list_attempts (assignment_id, operator_id, requested_at, state, disposition, "
                        "billsec, finished_at) VALUES (%s, %s, %s, 'finished', 'ANSWER', %s, %s)",
                        (assignment, operator, at, billsec, at + timedelta(seconds=billsec + 20)))

    def yandex_docs(self, minutes, signed_by_yandex=False):
        """Пакет из двух документов, водитель подписал оба в момент at(minutes). Как отдаёт Sapar (сверка
        18–19.11.2026): у «Подписано» время по UTC, у «НаПодписанииУЯндекса» — по Алматы, без пояса."""
        moment = self.at(minutes)
        utc = moment.astimezone(UTC).replace(tzinfo=None).isoformat()
        local = moment.astimezone(signing.PERIOD_TZ).replace(tzinfo=None).isoformat()
        waiting = ('Подписано', utc) if signed_by_yandex else ('НаПодписанииУЯндекса', local)
        return [{'ServiceId': 1, 'Status': 'Подписано', 'DocSignDateByDriver': utc},
                {'ServiceId': 2, 'Status': waiting[0], 'DocSignDateByDriver': waiting[1]}]


@pytest.fixture
def env():
    schema = 't_dial_sign_cycle_' + uuid.uuid4().hex
    db = _Db(schema)
    admin = db.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled) VALUES (1954, TRUE)")
    try:
        yield _Env(db)
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


A, B, C = '900101300001', '900101300002', '900101300003'


def test_cycle_takes_the_whole_base_but_expired(env):
    env.lead(A)                                                         # не проверяли
    env.lead(B, sign_status='unsigned', checked_min=5)                  # спрашивали 5 минут назад — всё равно в круге
    env.lead(C, sign_status='processing', checked_min=40)
    env.lead('900101300004', sign_status='expired', checked_min=60)     # истёк, спрашивали час назад — рано
    env.lead('900101300005', sign_status='expired', checked_min=200)    # истёк, прошло больше 3 часов — пора
    env.lead('900101300006', sign_status='expired')                     # истёк, времени проверки нет — пора
    env.lead('900101300007', status='excluded', sign_status='unsigned', checked_min=300)   # не в списке — не в базе
    env.lead('900101300008', sign_status='unsigned', checked_min=300, signed=True)         # подписал — всё
    env.lead('900101300009', sign_status='signed', checked_min=300, signed=True)           # и Яндекс подписал — всё
    env.lead('900101300010', sign_status='online', checked_min=300, signed=True)           # «вышел на линию» (Тез) — не Sapar
    # Подписал, документы «на проверке у Яндекса» (19.11.2026): в круге до «Подписано» — обновить статус.
    env.lead('900101300014', sign_status='processing', checked_min=20, signed=True)
    env.roster([A, B, C, '900101300004', '900101300005', '900101300006', '900101300008', '900101300009',
                '900101300010', '900101300014'])
    # Прошлый месяц (списка нет — база вся): ждём только подписи Яндекса; истёкшие там уже не изменятся.
    env.lead('900101300011', period=env.prev, sign_status='processing', checked_min=10)
    env.lead('900101300012', period=env.prev, sign_status='expired', checked_min=900)
    env.lead('900101300013', period=env.prev, sign_status='unsigned', checked_min=60)

    summary = env.checker().run(reason='тест', now=env.t0)
    assert env.sapar.took() == [A, B, C, '900101300005', '900101300006', '900101300011', '900101300013',
                                '900101300014']
    assert (summary['checked'], summary['stopped'], summary['lanes']) == (8, '', sign_check.PASS_LANES)
    # Запас в 15 минут: истёкший, проверенный 2 ч 50 мин назад, идёт в этот круг, а не ждёт следующего.
    env.q("UPDATE dial_list_leads SET sign_checked_at = %s WHERE id = %s", (env.at(-170), env.ids['900101300004']))
    env.checker().run(reason='тест', now=env.t0)
    assert '900101300004' in env.sapar.took()
    # «Проверить сейчас» в карточке — любой водитель: истёкший из базы прошлого месяца и исключённый не из списка.
    env.checker().run(reason='карточка', now=env.t0, force=True,
                      lead_ids=[env.ids['900101300012'], env.ids['900101300007']])
    assert env.sapar.took() == ['900101300007', '900101300012']


def test_cycle_rewrites_a_row_only_when_there_is_something_to_write(env):
    for iin in (A, B, C):
        env.lead(iin)
    env.roster([A, B, C])
    checker = env.checker()

    checker.run(reason='тест', now=env.t0)                      # первая проверка — пишутся все
    first = {iin: env.row(iin) for iin in (A, B, C)}
    assert all(row[1:] == ('unsigned', env.t0, '', False) for row in first.values())

    checker.run(reason='тест', now=env.at(10))                  # ответ Sapar тот же, времени проверки 10 минут
    assert env.sapar.took() == sorted([A, B, C] * 2)            # спросили всех оба раза
    assert {iin: env.row(iin) for iin in (A, B, C)} == first    # а строки не тронуты

    env.sapar.docs[B] = HALF                                    # у B один документ ушёл к Яндексу: статус прежний,
    checker.run(reason='тест', now=env.at(20))                  # документы другие — строка B пишется
    assert env.row(B)[1:] == ('unsigned', env.at(20), '', False) and env.row(B)[0] != first[B][0]
    assert env.q("SELECT sign_docs->0->>'group' FROM dial_list_leads WHERE id = %s", (env.ids[B],))[0][0] == 'processing'
    assert (env.row(A), env.row(C)) == (first[A], first[C])

    env.sapar.docs[B] = AT_YANDEX                               # сменился статус: «на проверке у Яндекса» — подписал
    summary = checker.run(reason='тест', now=env.at(25))        # (19.11.2026), момент подписи пишется сразу
    assert summary['signed'] == 1 and env.row(B)[1:] == ('processing', env.at(25), '', True)
    assert (env.row(A), env.row(C)) == (first[A], first[C])

    summary = checker.run(reason='тест', now=env.at(31))        # времени проверки у A и C уже 31 минута — пишем его
    assert env.row(A)[1:] == ('unsigned', env.at(31), '', False) and env.row(A)[0] != first[A][0]
    assert env.row(C)[2] == env.at(31)
    # B в круге до «Подписано», но ответ тот же и проверке 6 минут — строку не трогаем и подписавшим снова не считаем.
    assert env.row(B)[2] == env.at(25) and summary['signed'] == 0

    env.sapar.errors[A] = 'ИИН не найден'                       # Sapar отказал по водителю — ошибка пишется сразу
    checker.run(reason='тест', now=env.at(35))
    refused = env.row(A)
    assert refused[1:] == ('unsigned', env.at(35), 'ИИН не найден', False)
    checker.run(reason='тест', now=env.at(40))                  # тот же отказ — строка не тронута
    assert env.row(A) == refused
    del env.sapar.errors[A]
    checker.run(reason='тест', now=env.at(45))                  # ответил — ошибка снимается сразу
    assert env.row(A)[1:] == ('unsigned', env.at(45), '', False)

    env.sapar.docs[C] = SIGNED                                  # подписал — пишется сразу и из круга уходит
    summary = checker.run(reason='тест', now=env.at(50))
    assert summary['signed'] == 1 and env.row(C)[1:] == ('signed', env.at(50), '', True)
    env.sapar.took()
    checker.run(reason='тест', now=env.at(55))
    assert env.sapar.took() == [A, B]

    # Статус в строке разошёлся с ответом Sapar при тех же документах (правили руками) — круг его возвращает.
    env.q("UPDATE dial_list_leads SET sign_status = 'no_docs' WHERE id = %s", (env.ids[A],))
    checker.run(reason='тест', now=env.at(58))
    assert env.row(A)[1:] == ('unsigned', env.at(58), '', False)


def test_waiting_for_yandex_is_a_success_at_once_at_the_drivers_moment(env):
    # Владелец, 19.11.2026: «засчитывать ждут Яндекса сразу в успешки, в отчётах и везде тоже».
    env.q("INSERT INTO users (id, name) VALUES (7, 'Оператор Сава'), (8, 'Оператор Назар')")
    for iin in (A, B):
        env.lead(iin)
    env.roster([A, B])
    # A: Сава поговорил за час до подписи, водитель подписал за 30 минут до проверки, Назар звонил уже после подписи.
    env.call(A, 7, env.at(-60), billsec=40)
    env.call(A, 8, env.at(-10), billsec=40)
    env.sapar.docs[A] = env.yandex_docs(-30)
    env.sapar.docs[B] = env.yandex_docs(-20)                    # B подписал сам, без разговора
    summary = env.checker().run(reason='тест', now=env.t0)
    assert (summary['signed'], summary['successes']) == (2, 1)
    # Момент подписи — водителя: время «ждущего Яндекса» документа по Алматы. Прочитанное по UTC, оно было бы на
    # 5 часов позже (обрезалось бы до момента проверки), и успешка ушла бы Назару, звонившему уже после подписи.
    assert env.signing(A) == ('processing', env.at(-30), env.t0, 7, True)
    assert env.signing(B) == ('processing', env.at(-20), env.t0, None, True)
    docs = env.q("SELECT sign_docs FROM dial_list_leads WHERE id = %s", (env.ids[A],))[0][0]
    assert [_moment(d['signed_at']) for d in docs] == [env.at(-30), env.at(-30)]

    # Везде — подписали: «Аналитика», журнал (этап и фильтры), карточка.
    svc = dial_service.DialListService(env.db, client_factory=lambda dep: None, sign_checker=env.checker(),
                                       calls_submit=lambda fn: fn())
    with env.db._get_cursor() as cur:
        totals = dial_analytics.department_analytics(cur, RCC, env.period, configured=True)['totals']
    assert {k: totals[k] for k in ('signed', 'successes', 'self_signed', 'processing')} == \
        {'signed': 2, 'successes': 1, 'self_signed': 1, 'processing': 0}
    page = svc.leads_journal(RCC, period=env.period, limit=50)
    assert {item['id']: item['stage'] for item in page['items']} == {env.ids[A]: 'signed', env.ids[B]: 'signed'}
    assert {item['id'] for item in svc.leads_journal(RCC, period=env.period, sign='success')['items']} == {env.ids[A]}
    assert {item['id'] for item in svc.leads_journal(RCC, period=env.period, sign='processing')['items']} == \
        {env.ids[A], env.ids[B]}
    card = svc.lead_card(env.ids[A])['sign']
    assert (card['status'], card['success']['operator']['id']) == ('signed', 7)

    # Круг ведёт их до «Подписано»: ответ тот же — строку не трогаем и подписавшими снова не считаем.
    before = env.row(A)
    summary = env.checker().run(reason='тест', now=env.at(10))
    assert (summary['signed'], env.row(A)) == (0, before)
    # Яндекс подписал — меняются статус и документы; момент подписи, «когда увидели» и успешка — нет.
    env.sapar.docs[A] = env.yandex_docs(-30, signed_by_yandex=True)
    summary = env.checker().run(reason='тест', now=env.at(20))
    assert (summary['signed'], summary['successes']) == (0, 0)
    assert env.signing(A) == ('signed', env.at(-30), env.t0, 7, True)
    env.sapar.took()
    env.checker().run(reason='тест', now=env.at(30))
    assert env.sapar.took() == [B]                              # A подписан и Яндексом — из круга ушёл
    # Откат у подписавшего (документы вернули водителю): статус пишется, подпись и решение по успешке остаются,
    # и в круге он больше не нужен.
    env.sapar.docs[B] = WAITING
    env.checker().run(reason='тест', now=env.at(40))
    assert env.signing(B) == ('unsigned', env.at(-20), env.t0, None, True)
    env.sapar.took()
    env.checker().run(reason='тест', now=env.at(50))
    assert env.sapar.took() == []


def test_clamped_moment_is_corrected_back_and_the_success_decided_again(env):
    # Прод, 19.11.2026, первые минуты нового правила: время «ИдетСохранениеФайла» читалось по UTC (на 5 часов в
    # будущем), и момент подписи обрезался до времени проверки. Более ранний момент из следующего ответа уточняет
    # подпись назад, а успешка решается заново: разговор после настоящей подписи её не даёт.
    env.q("INSERT INTO users (id, name) VALUES (7, 'Оператор Сава'), (8, 'Оператор Назар')")
    env.lead(A)
    env.roster([A])
    env.call(A, 7, env.at(-60), billsec=40)                     # до подписи
    env.call(A, 8, env.at(-10), billsec=40)                     # после подписи, до проверки
    local = env.at(-30).astimezone(signing.PERIOD_TZ).replace(tzinfo=None).isoformat()
    env.sapar.docs[A] = [{'ServiceId': 1, 'Status': 'НаПодписанииУЯндекса', 'DocSignDateByDriver': local},
                         {'ServiceId': 2, 'Status': 'ИдетСохранениеФайла'}]     # без времени — момент проверки
    env.checker().run(reason='тест', now=env.t0)
    assert env.signing(A) == ('processing', env.t0, env.t0, 8, True)
    env.sapar.docs[A] = [{'ServiceId': 1, 'Status': 'НаПодписанииУЯндекса', 'DocSignDateByDriver': local},
                         {'ServiceId': 2, 'Status': 'ИдетСохранениеФайла', 'DocSignDateByDriver': local}]
    summary = env.checker().run(reason='тест', now=env.at(15))
    assert (summary['signed'], summary['successes']) == (0, 1)
    assert env.signing(A) == ('processing', env.at(-30), env.t0, 7, True)
    # Позже момент не сдвигается: документ с подписью позже ни подпись, ни успешку не меняет.
    later = env.at(5).astimezone(signing.PERIOD_TZ).replace(tzinfo=None).isoformat()
    env.sapar.docs[A] = [{'ServiceId': 1, 'Status': 'НаПодписанииУЯндекса', 'DocSignDateByDriver': local},
                         {'ServiceId': 2, 'Status': 'НаПодписанииУЯндекса', 'DocSignDateByDriver': later}]
    summary = env.checker().run(reason='тест', now=env.at(25))
    assert summary['successes'] == 0
    assert env.signing(A) == ('processing', env.at(-30), env.t0, 7, True)


def test_older_answer_never_overwrites_a_newer_signature(env):
    # Круг держит ответы Sapar до записи пачкой — секунды. За это время водитель подписал, а «Проверить на
    # подписание» с телефона (check_now) уже записала подпись «на проверке у Яндекса». Запоздавший старый ответ
    # «не подписал» не должен её перекрыть: иначе статус стал бы «не подписал», и круг больше не спрашивал бы
    # водителя — документы до подписи остались бы в карточке навсегда.
    env.lead(A)
    env.roster([A])
    checker = env.checker()
    env.sapar.docs[A] = env.yandex_docs(3)
    summary = checker.check_now([(env.ids[A], A, env.period)], timeout=3, workers=1, budget_sec=5, max_systemic=1,
                                now=env.at(5))
    assert summary['signed'] == [env.ids[A]] and summary['processing'] == [env.ids[A]]
    signed = env.row(A)
    assert signed[1:] == ('processing', env.at(5), '', True)
    # Ответ круга от at(1) — до подписи — пришёл в базу позже.
    checker._write([('ok', env.ids[A], signing.UNSIGNED, '[]', None, env.at(1))], env.at(1), sparse=True)
    checker._write([('error', env.ids[A], 'ИИН не найден', False, env.at(2))], env.at(2), sparse=True)
    assert env.row(A) == signed
    # Водитель по-прежнему в круге — до «Подписано».
    env.sapar.took()
    checker.run(reason='тест', now=env.at(10))
    assert env.sapar.took() == [A]
    # Ответ не старше записанного — пишется как обычно.
    env.sapar.docs[A] = env.yandex_docs(3, signed_by_yandex=True)
    checker.run(reason='тест', now=env.at(20))
    assert env.signing(A) == ('signed', env.at(3), env.at(5), None, True)


def test_first_check_is_recorded_even_when_the_answer_equals_the_row(env):
    # Водителю сменили ИИН при повторной загрузке: время проверки сброшено, статус «документы не поступили»
    # остался от прежнего ИИН. По новому ИИН документов тоже нет — ответ совпал со строкой, но проверку пишем:
    # иначе водитель вечно числился бы непроверенным и шёл в каждом круге первым.
    env.lead(A, sign_status='no_docs')
    env.roster([A])
    env.sapar.docs[A] = []
    env.checker().run(reason='тест', now=env.t0)
    assert env.row(A)[1:] == ('no_docs', env.t0, '', False)


def test_card_check_and_issue_check_always_write_the_time(env):
    for iin in (A, B):
        env.lead(iin)
    env.roster([A, B])
    checker = env.checker()
    checker.run(reason='тест', now=env.t0)
    # «Проверить сейчас» в карточке: ответ тот же, а время проверки в строке — новое.
    checker.run(reason='карточка', now=env.at(5), lead_ids=[env.ids[A]], force=True)
    assert env.row(A)[1:] == ('unsigned', env.at(5), '', False)
    assert env.row(B)[2] == env.t0
    # Проверка перед выдачей оператору (check_now): по этому времени выдача видит, что водителя уже спросили.
    summary = checker.check_now([(env.ids[B], B, env.period)], timeout=3, workers=2, budget_sec=5, max_systemic=2,
                                now=env.at(7))
    assert summary['checked'] == 1 and env.row(B)[2] == env.at(7)
    # И круг помнит этот ответ: в очереди водитель встанет за теми, кого спрашивали раньше.
    assert (checker.seen_at(env.ids[B]), checker.seen_at(env.ids[A])) == (env.at(7), env.at(5))


def test_refresh_before_report_skips_those_the_cycle_has_just_asked(env):
    for iin in (A, B, C):
        env.lead(iin)
    env.roster([A, B, C])
    checker = env.checker()
    checker.run(reason='тест', now=env.t0)
    checker.run(reason='тест', now=env.at(10))          # в строках время проверки осталось t0, круг помнит t0+10
    env.sapar.took()
    bases = [(RCC, env.period)]
    summary = checker.refresh(bases, env.at(5), env.at(60), reason='тест', now=env.at(12))
    assert (env.sapar.took(), summary['due']) == ([], 0)
    # Кого круг спросил раньше fresh_before — спрашиваем.
    summary = checker.refresh(bases, env.at(11), env.at(60), reason='тест', now=env.at(12))
    assert (env.sapar.took(), summary['due'], summary['stopped']) == ([A, B, C], 3, '')
    # После перезапуска сервера память пустая — перепроверка идёт по времени из строк.
    summary = env.checker().refresh(bases, env.at(5), env.at(60), reason='тест', now=env.at(12))
    assert (env.sapar.took(), summary['due']) == ([A, B, C], 3)


def _moment(text):
    return datetime.fromisoformat(text) if text else None


def test_analytics_carries_the_cycle_and_journal_the_remembered_time(env):
    for iin in (A, B, C):
        env.lead(iin)
    env.lead('900101300004', status='excluded')         # исключён и не в списке — не в базе месяца
    env.roster([A, B, C])
    env.sapar.errors[C] = 'ИИН не найден'
    checker = env.checker()
    svc = dial_service.DialListService(env.db, client_factory=lambda dep: None, sign_checker=checker,
                                       calls_submit=lambda fn: fn())

    before = svc.analytics(RCC, env.period)['sign_check']
    assert (before['never_checked'], before['errors'], before['last_checked_at']) == (3, 0, None)
    assert (before['updated_at'], before['updating']) == (None, False)
    # Кругов ещё не было — первый начнётся с запуском планировщика.
    assert _moment(before['next_update_at']) == signing.next_check_tick(_moment(before['server_now']))

    checker.run(reason='тест', now=env.t0)
    checker.run(reason='тест', now=env.at(10))
    with env.db._get_cursor() as cur:
        data = dial_analytics.department_analytics(cur, RCC, env.period, configured=True)
    check = data['sign_check']
    # Настоящим SQL: в базе трое; у C — отказ Sapar; время записи — круг, где было что писать (во втором
    # ответы те же, строки не тронуты).
    assert (data['totals']['leads'], check['never_checked'], check['errors']) == (3, 0, 1)
    assert _moment(check['last_checked_at']) == env.t0
    after = svc.analytics(RCC, env.period)['sign_check']
    assert (_moment(after['updated_at']), after['updating']) == (env.at(10), False)
    assert _moment(after['next_update_at']) >= _moment(after['server_now'])
    assert set(after) >= {'server_now', 'updated_at', 'next_update_at', 'updating', 'last_checked_at', 'active'}

    # Журнал и карточка: в строках время проверки — t0, а показываем то, что помнит круг.
    assert {iin: env.row(iin)[2] for iin in (A, B)} == {A: env.t0, B: env.t0}
    page = svc.leads_journal(RCC, period=env.period, limit=50)
    shown = {item['id']: _moment(item['sign']['checked_at']) for item in page['items']}
    assert shown == {env.ids[A]: env.at(10), env.ids[B]: env.at(10), env.ids[C]: env.t0}
    assert _moment(svc.lead_card(env.ids[A])['sign']['checked_at']) == env.at(10)
    # После перезапуска сервера, пока круг до водителя не дошёл, — время из строки.
    restarted = dial_service.DialListService(env.db, client_factory=lambda dep: None, sign_checker=env.checker(),
                                             calls_submit=lambda fn: fn())
    assert _moment(restarted.lead_card(env.ids[A])['sign']['checked_at']) == env.t0
