# -*- coding: utf-8 -*-
"""Необязательная проверка настоящим SQL: DIAL_LIST_TEST_PORT — порт локального Postgres.

Отчёт «Подписание водителей по городам» (владелец, 19.10.2026) на настоящей схеме раздела
(dial_list.schema): подсчёт по городам (подписал + на проверке у Яндекса, тестовые номера мимо,
база обзваниваемого месяца), выбор групп из реестра бота, «один отчёт на слот» и итог отправки.
Telegram подменён. Запуск:

    DIAL_LIST_TEST_PORT=55461 python -m pytest tests/test_dial_list_sign_report_postgres.py

Люди, номера, ИИН и группы выдуманы.
"""
import contextlib
import os
import sys
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dial_list import schema as dial_schema  # noqa: E402
from dial_list import service as dial_service  # noqa: E402

PORT = os.environ.get('DIAL_LIST_TEST_PORT')
pytestmark = pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (DIAL_LIST_TEST_PORT)')

RCC, TEZ = 1954, 560
ALMATY = timezone(timedelta(hours=5))
GROUP_A, GROUP_B, GROUP_GONE = -1001000000001, -1001000000002, -1001000000003

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
    INSERT INTO test_phone_numbers (phone_key) VALUES ('7019999999');
'''


def _at(day, hh, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=ALMATY)


class _Db:
    """_get_cursor на своей схеме; list_it_ticket_channels — реестр групп бота (подменён)."""

    def __init__(self, schema):
        self.schema = schema
        self.channels = [
            {'chat_id': GROUP_A, 'title': 'Удалёнка: отчёты', 'chat_type': 'supergroup'},
            {'chat_id': GROUP_B, 'title': 'Регионы — руководители', 'chat_type': 'group'},
        ]
        self.registry_broken = False

    def list_it_ticket_channels(self, active_only=True):
        if self.registry_broken:
            raise RuntimeError('реестр недоступен')
        return list(self.channels)

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


@pytest.fixture
def db():
    schema = 't_dial_sign_report_' + uuid.uuid4().hex
    base = _Db(schema)
    admin = base.connect()
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute('CREATE SCHEMA ' + schema)
        cur.execute('SET search_path TO ' + schema)
        cur.execute(BASE_DDL)
        dial_schema.init_dial_list_schema(cur)
        # Схема идемпотентна: второй прогон на той же базе ничего не ломает.
        dial_schema.init_dial_list_schema(cur)
        cur.execute("INSERT INTO dial_list_department_settings (department_id, enabled, profile, active_period) "
                    "VALUES (1954, TRUE, 'remote_cc', DATE '2026-10-01'), (560, FALSE, 'tez', NULL)")
    try:
        yield base
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP SCHEMA ' + schema + ' CASCADE')
        admin.close()


_seq = [0]


def lead(db, city, *, signed=False, status='', department=RCC, period=date(2026, 10, 1), phone=None):
    _seq[0] += 1
    phone = phone or '7701%07d' % _seq[0]
    with db._get_cursor() as cur:
        cur.execute("""
            INSERT INTO dial_list_leads (department_id, phone_norm, full_name, iin, period, city, sign_status, signed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (department, phone, 'Водитель %d' % _seq[0], '99010130%04d' % _seq[0], period, city, status,
              _at(18, 12) if signed else None))


def _service(db, sent):
    def send(chat_id, png):
        sent.append((chat_id, png[:4]))
        return {'message_id': len(sent)}, None
    svc = dial_service.DialListService(db, telegram_send=send)
    svc.SIGN_REPORT_RETRY_SEC = 0
    return svc


def test_counts_by_city_signed_means_signed_or_processing(db):
    for _ in range(3):
        lead(db, 'Астана', signed=True, status='signed')
    lead(db, 'Астана', status='processing')          # ждёт подписи Яндекса — «подписано»
    lead(db, 'Астана', status='unsigned')
    lead(db, 'Шымкент', status='unsigned')
    lead(db, 'Шымкент', status='expired')
    lead(db, '', status='unsigned')                   # без города
    lead(db, 'Астана', signed=True, phone='77019999999')                 # тестовый номер — мимо
    lead(db, 'Астана', signed=True, period=date(2026, 9, 1))             # база другого месяца — мимо
    lead(db, 'Астана', signed=True, department=TEZ)                      # другой отдел — мимо
    report = dial_service.DialListService(db).sign_report_data(RCC)
    assert report['period'] == date(2026, 10, 1)
    assert [(c['city'], c['total'], c['signed']) for c in report['cities']] == [
        ('Астана', 5, 4), ('Шымкент', 2, 0), ('Город не указан', 1, 0)]
    assert report['totals'] == {'total': 8, 'signed': 4, 'unsigned': 4, 'signed_pct': 50.0, 'unsigned_pct': 50.0}


def test_choose_groups_from_bot_registry(db):
    svc = dial_service.DialListService(db)
    state = svc.sign_report_chats(RCC)
    assert state['chosen_ids'] == [] and state['schedule'] == ['10:00', '19:00'] and state['registry_ok']
    assert [g['title'] for g in state['available']] == ['Регионы — руководители', 'Удалёнка: отчёты']

    with pytest.raises(dial_service.DialListError) as refused:
        svc.save_sign_report_chats(RCC, [GROUP_A, GROUP_GONE], changed_by=2)
    assert refused.value.status == 400 and str(GROUP_GONE) in str(refused.value)
    with pytest.raises(dial_service.DialListError):
        svc.save_sign_report_chats(RCC, 'abc', changed_by=2)
    with pytest.raises(dial_service.DialListError) as tez:
        svc.save_sign_report_chats(TEZ, [GROUP_A], changed_by=2)
    assert tez.value.status == 400

    state = svc.save_sign_report_chats(RCC, [GROUP_A, str(GROUP_B), GROUP_A], changed_by=2, now=_at(19, 15))
    assert sorted(state['chosen_ids']) == sorted([GROUP_A, GROUP_B])
    # Бота убрали из группы после выбора — группа остаётся в выборе с пометкой.
    db.channels = db.channels[:1]
    state = svc.sign_report_chats(RCC)
    lost = {s['chat_id']: s['is_available'] for s in state['selected']}
    assert lost == {GROUP_A: True, GROUP_B: False}
    # Снять выбор можно и с группы, где бота уже нет; реестр недоступен — 503, выбор не трогаем.
    db.registry_broken = True
    with pytest.raises(dial_service.DialListError) as broken:
        svc.save_sign_report_chats(RCC, [], changed_by=2)
    assert broken.value.status == 503
    assert svc.sign_report_chats(RCC)['registry_ok'] is False
    db.registry_broken = False
    assert svc.save_sign_report_chats(RCC, [GROUP_A], changed_by=2)['chosen_ids'] == [GROUP_A]


def test_one_report_per_slot_and_outcome(db):
    lead(db, 'Астана', signed=True, status='signed')
    lead(db, 'Шымкент', status='unsigned')
    sent = []
    svc = _service(db, sent)
    # Выбрали в 09:30 — первый же слот (10:00) уходит.
    svc.save_sign_report_chats(RCC, [GROUP_A, GROUP_B], changed_by=2, now=_at(19, 9, 30))

    summary = svc.send_sign_reports(now=_at(19, 10, 0))
    assert summary['sent'] == 2 and summary['slot'] == _at(19, 10, 0).isoformat()
    assert sorted(chat for chat, _ in sent) == sorted([GROUP_A, GROUP_B])
    assert all(head == b'\x89PNG' for _, head in sent)
    # Тот же слот второй раз (вторая копия сервера, перезапуск) — ничего.
    assert svc.send_sign_reports(now=_at(19, 10, 20))['sent'] == 0
    assert len(sent) == 2
    # Между слотами — ничего; в 19:00 — снова обеим.
    assert svc.send_sign_reports(now=_at(19, 15, 0))['slot'] is None
    assert svc.send_sign_reports(now=_at(19, 19, 0))['sent'] == 2
    assert len(sent) == 4
    state = svc.sign_report_chats(RCC)
    assert all(s['last_sent_at'] and not s['last_error'] for s in state['selected'])


def test_two_processes_same_slot_send_once(db):
    lead(db, 'Астана', signed=True, status='signed')
    sent, lock = [], threading.Lock()

    def send(chat_id, png):
        with lock:
            sent.append(chat_id)
        return {'message_id': 1}, None

    first = dial_service.DialListService(db, telegram_send=send)
    second = dial_service.DialListService(db, telegram_send=send)
    first.save_sign_report_chats(RCC, [GROUP_A], changed_by=2, now=_at(19, 9, 0))
    threads = [threading.Thread(target=s.send_sign_reports, kwargs={'now': _at(19, 10, 0)}) for s in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sent == [GROUP_A]


def test_new_group_waits_for_next_slot(db):
    lead(db, 'Астана', signed=True, status='signed')
    sent = []
    svc = _service(db, sent)
    # Выбрали в 10:20 — утренний слот уже прошёл; перезапуск сервера в 10:40 его не досылает.
    svc.save_sign_report_chats(RCC, [GROUP_A], changed_by=2, now=_at(19, 10, 20))
    assert svc.send_sign_reports(now=_at(19, 10, 40))['sent'] == 0
    assert svc.send_sign_reports(now=_at(19, 19, 0))['sent'] == 1


def test_failure_is_recorded_and_slot_closed(db):
    lead(db, 'Астана', signed=True, status='signed')
    attempts = []

    def send(chat_id, png):
        attempts.append(chat_id)
        return None, 'Forbidden: bot was kicked from the supergroup chat'

    svc = dial_service.DialListService(db, telegram_send=send)
    svc.SIGN_REPORT_RETRY_SEC = 0
    svc.save_sign_report_chats(RCC, [GROUP_A], changed_by=2, now=_at(19, 9, 0))
    summary = svc.send_sign_reports(now=_at(19, 10, 0))
    assert summary['failed'] == 1 and attempts == [GROUP_A, GROUP_A]
    chat = svc.sign_report_chats(RCC)['selected'][0]
    assert chat['last_error'].startswith('Forbidden') and chat['last_sent_at'] is None
    # Опоздавший отчёт не досылается: слот отработан.
    assert svc.send_sign_reports(now=_at(19, 10, 30))['failed'] == 0 and len(attempts) == 2


def test_empty_base_sends_nothing_and_says_why(db):
    sent = []
    svc = _service(db, sent)
    svc.save_sign_report_chats(RCC, [GROUP_A], changed_by=2, now=_at(19, 9, 0))
    summary = svc.send_sign_reports(now=_at(19, 10, 0))
    assert summary['skipped'] == 1 and sent == []
    assert svc.sign_report_chats(RCC)['selected'][0]['last_error'].startswith('В базе месяца никого')


def test_tez_rows_are_ignored_by_sender(db):
    lead(db, 'Астана', signed=True, status='signed', department=TEZ)
    with db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_sign_report_chats (department_id, chat_id, title) VALUES (560, %s, 'x')",
                    (GROUP_A,))
    sent = []
    assert _service(db, sent).send_sign_reports(now=_at(19, 10, 0))['sent'] == 0
    assert sent == []


def test_city_list_defines_report_population(db):
    """Владелец, 21.10.2026: «отчёт строго по списку». Список месяца («Города по ИИН») задаёт состав
    отчёта: водители базы не из списка в отчёт не входят, водители списка не из базы добавляются без
    номера (только для отчёта); города проставляются по ИИН."""
    with db._get_cursor() as cur:
        for n, (iin, city, department, period) in enumerate([
                ('900101300007', '', RCC, date(2026, 10, 1)), ('050312500771', 'Тараз', RCC, date(2026, 10, 1)),
                ('990101300013', '', RCC, date(2026, 10, 1)), ('990101300023', '', RCC, date(2026, 10, 1)),
                ('900101300007', '', RCC, date(2026, 9, 1)), ('900101300007', '', TEZ, date(2026, 10, 1))],
                start=1):
            cur.execute("""
                INSERT INTO dial_list_leads (department_id, phone_norm, full_name, iin, period, city, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, TIMESTAMPTZ '2026-10-01 00:00+05')
            """, (department, '77020000%03d' % n, 'Водитель %d' % n, iin, period, city))
    svc = dial_service.DialListService(db)
    checks = []
    svc.request_sign_check = lambda reason='': checks.append(reason)
    counts = svc.import_cities(RCC, [
        (2, '900101300007', 'Астана', ''),
        (3, '50312500771', 'Тараз', ''),          # ведущий ноль потерян в Excel — тот же водитель, город тот же
        (4, '900101300007', 'Шымкент', ''),       # повтор ИИН — берётся первая строка
        (5, '123', 'Актау', ''),                  # ошибка в ИИН
        (6, '990101300013', '', ''),              # без города — в списке, в отчёте «Город не указан»
        (7, '880101300000', 'Актобе', 'Иванова Айгерим'),   # в базе нет — добавится без номера
    ])
    assert counts == {'rows_total': 6, 'matched': 3, 'updated': 1, 'added_report_only': 1, 'not_in_report': 1,
                      'bad_iin': 1, 'no_city': 1, 'duplicates': 1, 'period': '2026-10-01'}
    assert checks == ['города по ИИН']           # добавленного без номера Sapar проверит сразу
    with db._get_cursor() as cur:
        cur.execute("SELECT department_id, period, iin, city, phone_norm, full_name, status, note, "
                    "updated_at = TIMESTAMPTZ '2026-10-01 00:00+05' FROM dial_list_leads ORDER BY phone_norm")
        rows = cur.fetchall()
    assert [(r[0], r[1].isoformat(), r[2], r[3]) for r in rows] == [
        (RCC, '2026-10-01', '900101300007', 'Астана'),
        (RCC, '2026-10-01', '050312500771', 'Тараз'),
        (RCC, '2026-10-01', '990101300013', ''),
        (RCC, '2026-10-01', '990101300023', ''),      # не из списка — в обзвоне, город не тронут
        (RCC, '2026-09-01', '900101300007', ''),      # база другого месяца — не тронута
        (TEZ, '2026-10-01', '900101300007', ''),      # другой отдел — не тронут
        (RCC, '2026-10-01', '880101300000', 'Актобе'),
    ]
    assert all(r[8] for r in rows[:6])                # город не событие журнала: updated_at прежний
    added = rows[6]
    assert (added[4], added[5], added[6], added[7]) == (dial_service.report_only_phone('880101300000'),
                                                        'Иванова Айгерим', 'excluded', dial_service.REPORT_ONLY_NOTE)
    report = svc.sign_report_data(RCC)
    assert [(c['city'], c['total']) for c in report['cities']] == [
        ('Актобе', 1), ('Астана', 1), ('Тараз', 1), ('Город не указан', 1)]
    assert report['totals']['total'] == 4            # водитель не из списка в отчёт не вошёл
    # Тот же список ещё раз — ничего не переписывает и не добавляет; список заменяется целиком.
    again = svc.import_cities(RCC, [(2, '900101300007', 'Астана', ''), (3, '880101300000', 'Актобе', '')])
    assert (again['matched'], again['updated'], again['added_report_only'], again['not_in_report']) == (2, 0, 0, 3)
    assert svc.sign_report_data(RCC)['totals']['total'] == 2
    # Файл без единого верного ИИН состав не трогает; автообзвон Тез — отказ.
    with pytest.raises(dial_service.DialListError):
        svc.import_cities(RCC, [(2, '123', 'Астана', '')])
    assert svc.sign_report_data(RCC)['totals']['total'] == 2
    with pytest.raises(dial_service.DialListError) as tez:
        svc.import_cities(TEZ, [(2, '900101300007', 'Астана', '')])
    assert tez.value.status == 400


def test_report_only_driver_is_not_dialed_until_a_phone_comes(db):
    svc = dial_service.DialListService(db)
    svc.request_sign_check = lambda reason='': None
    svc.import_leads(RCC, 2, 'база.xlsx', [(2, 'Иванов Иван', '87011234501', '77011234501', '900101300007', '')],
                     period=date(2026, 10, 1))
    svc.import_cities(RCC, [(2, '900101300007', 'Астана', ''), (3, '880101300000', 'Актобе', ''),
                            (4, '990101300013', 'Тараз', '')], period=date(2026, 10, 1))
    with db._get_cursor() as cur:
        cur.execute("SELECT id, iin FROM dial_list_leads WHERE phone_norm LIKE 'iin:%%'")
        placeholders = {r[1]: str(r[0]) for r in cur.fetchall()}
    assert set(placeholders) == {'880101300000', '990101300013'}
    with pytest.raises(dial_service.DialListError) as refused:
        svc.requeue_lead(placeholders['880101300000'], actor_id=2)
    assert refused.value.status == 409 and 'нет номера' in str(refused.value)
    # Номер пришёл обычной загрузкой базы — строка та же, только теперь с номером и в обзвоне. Номер,
    # занятый другим водителем базы, к строке не цепляется.
    counts = svc.import_leads(RCC, 2, 'добор.xlsx', [
        (2, 'Петров Пётр', '87011234502', '77011234502', '880101300000', 'Актобе'),
        (3, 'Сидоров Сидор', '87011234501', '77011234501', '990101300013', ''),
    ], period=date(2026, 10, 1))
    assert (counts['rows_new'], counts['rows_duplicate']) == (1, 1)
    with db._get_cursor() as cur:
        cur.execute("SELECT id, phone_norm, full_name, status, note, city FROM dial_list_leads WHERE iin = %s",
                    ('880101300000',))
        row = cur.fetchone()
        cur.execute("SELECT phone_norm, status FROM dial_list_leads WHERE iin = %s", ('990101300013',))
        still = cur.fetchone()
    assert (str(row[0]), row[1], row[2], row[3], row[4], row[5]) == (
        placeholders['880101300000'], '77011234502', 'Петров Пётр', 'new', '', 'Актобе')
    assert still == (dial_service.report_only_phone('990101300013'), 'excluded')
    assert svc.sign_report_data(RCC)['totals']['total'] == 3


def test_base_upload_keeps_and_updates_city(db):
    svc = dial_service.DialListService(db)
    first = svc.import_leads(RCC, 2, 'база.xlsx', [
        (2, 'Иванов Иван', '87011234501', '77011234501', '900101300007', 'Костанай'),
        (3, 'Петров Пётр', '87011234502', '77011234502', '050312500771'),          # без колонки города
    ], period=date(2026, 10, 1))
    assert first['rows_new'] == 2
    svc.import_leads(RCC, 2, 'база-2.xlsx', [
        (2, 'Иванов Иван', '87011234501', '77011234501', '900101300007', ''),      # пустой город не затирает
        (3, 'Петров Пётр', '87011234502', '77011234502', '050312500771', 'Атырау'),
    ], period=date(2026, 10, 1))
    with db._get_cursor() as cur:
        cur.execute("SELECT phone_norm, city FROM dial_list_leads ORDER BY phone_norm")
        assert cur.fetchall() == [('77011234501', 'Костанай'), ('77011234502', 'Атырау')]


def test_refresh_before_report_rechecks_stale_unsigned_of_report_base(db):
    """Перепроверка перед отчётом (владелец, 20.10.2026) на настоящей схеме: только база отчёта, только
    неподписавшие, проверенные раньше fresh_before; итог пишет настоящий _write."""
    from dial_list import sign_check
    utc = timezone.utc
    rows = [
        # (id-метка, отдел, месяц, ИИН, проверен когда, подписал)
        ('stale', RCC, date(2026, 10, 1), '900101300007', datetime(2026, 10, 20, 1, 0, tzinfo=utc), False),
        ('never', RCC, date(2026, 10, 1), '050312500771', None, False),
        ('fresh', RCC, date(2026, 10, 1), '990101300013', datetime(2026, 10, 20, 4, 20, tzinfo=utc), False),
        ('signed', RCC, date(2026, 10, 1), '880101300000', datetime(2026, 10, 20, 1, 0, tzinfo=utc), True),
        ('september', RCC, date(2026, 9, 1), '990101300023', datetime(2026, 10, 20, 1, 0, tzinfo=utc), False),
        ('tez', TEZ, date(2026, 10, 1), '880101300010', datetime(2026, 10, 20, 1, 0, tzinfo=utc), False),
    ]
    ids = {}
    with db._get_cursor() as cur:
        for n, (label, department, period, iin, checked, signed) in enumerate(rows, start=1):
            cur.execute("""
                INSERT INTO dial_list_leads (department_id, phone_norm, full_name, iin, period, sign_status,
                                             sign_checked_at, signed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (department, '77030000%03d' % n, 'Водитель %d' % n, iin, period,
                  'signed' if signed else 'unsigned', checked, _at(18, 12) if signed else None))
            ids[str(cur.fetchone()[0])] = label
    asked = []

    def fetch(iin, month, year, session=None):
        asked.append((iin, month, year))
        return {'ok': True, 'yandex': [{'ServiceId': 1, 'Status': 'Подписано',
                                        'DocSignDateByDriver': '2026-10-20T03:00:00'}]}

    checker = sign_check.SignChecker(db, fetch=fetch, configured=lambda: True, sleep=lambda s: None)
    now = datetime(2026, 10, 20, 4, 30, tzinfo=utc)
    summary = checker.refresh([(RCC, date(2026, 10, 1))], datetime(2026, 10, 20, 4, 15, tzinfo=utc),
                              datetime(2026, 10, 20, 4, 59, tzinfo=utc), reason='тест', now=now)
    assert sorted(asked) == sorted([('900101300007', 9, 2026), ('050312500771', 9, 2026)])
    assert (summary['due'], summary['checked'], summary['signed']) == (2, 2, 2)
    with db._get_cursor() as cur:
        cur.execute("SELECT id, signed_at IS NOT NULL, sign_checked_at FROM dial_list_leads")
        state = {ids[str(r[0])]: (r[1], r[2]) for r in cur.fetchall()}
    assert state['stale'] == (True, now) and state['never'] == (True, now)
    assert state['fresh'][0] is False and state['september'][0] is False and state['tez'][0] is False
    # Как обычный прогон, перепроверка решает успешку по всем подписавшим без решения — двум новым и
    # засеянному подписавшему; звонков не было, поэтому успешек нет.
    with db._get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM dial_list_leads WHERE success_resolved_at IS NOT NULL "
                    "AND success_attempt_id IS NULL")
        assert cur.fetchone()[0] == 3


def test_refresh_request_only_for_departments_with_groups(db):
    planned = []
    svc = dial_service.DialListService(db)
    svc.sign_checker.request_refresh = lambda bases, fresh_before, deadline, reason='': planned.append(
        (bases, fresh_before, deadline))
    assert svc.request_sign_report_refresh(now=_at(19, 9, 30)) is None          # групп нет — ничего
    svc.save_sign_report_chats(RCC, [GROUP_A], changed_by=2, now=_at(19, 8, 0))
    with db._get_cursor() as cur:
        cur.execute("INSERT INTO dial_list_sign_report_chats (department_id, chat_id, title) VALUES (560, %s, 'x')",
                    (GROUP_B,))
    result = svc.request_sign_report_refresh(now=_at(19, 18, 30))
    assert result['bases'] == [(RCC, '2026-10-01')]                             # отдел Тез — мимо
    assert planned == [([(RCC, date(2026, 10, 1))], _at(19, 18, 15), _at(19, 18, 59))]


def test_preview_png(db):
    lead(db, 'Астана', signed=True, status='signed')
    png = dial_service.DialListService(db).sign_report_png(RCC, now=_at(19, 12, 0))
    assert png.startswith(b'\x89PNG')
